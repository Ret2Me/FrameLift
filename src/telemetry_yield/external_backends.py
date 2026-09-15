"""Bounded, satellite-neutral adapters for file-oriented decoder programs.

This module intentionally does not know about any decoder suite.  It provides
the process boundary needed to add an arbitrary command-line demodulator while
keeping a batch campaign alive when that command times out, emits excessive
output, exits unsuccessfully, or returns data its parser cannot understand.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import math
import os
from pathlib import Path
import selectors
import signal
import subprocess
import time
from types import MappingProxyType
from typing import Callable, Iterable, Literal, Mapping, Sequence, TypeAlias

from .models import JsonValue


BackendStatus: TypeAlias = Literal["success", "failure"]
FailureCode: TypeAlias = Literal[
    "input_error",
    "invocation_error",
    "launch_error",
    "timeout",
    "output_limit",
    "nonzero_exit",
    "parse_error",
]


def _non_empty_tuple(name: str, values: Sequence[str]) -> tuple[str, ...]:
    output = tuple(values)
    if any(not isinstance(value, str) or not value.strip() for value in output):
        raise ValueError(f"{name} values must be non-empty strings")
    if len(set(output)) != len(output):
        raise ValueError(f"{name} values must be unique")
    return output


def _json_mapping(
    name: str, values: Mapping[str, JsonValue]
) -> Mapping[str, JsonValue]:
    if not isinstance(values, Mapping):
        raise TypeError(f"{name} must be a mapping")
    copied = dict(values)
    if any(not isinstance(key, str) or not key.strip() for key in copied):
        raise ValueError(f"{name} keys must be non-empty strings")
    try:
        json.dumps(copied, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise TypeError(f"{name} must contain finite JSON values") from exc
    return MappingProxyType(dict(sorted(copied.items())))


@dataclass(frozen=True, slots=True)
class CaptureSegment:
    """A bounded region of an IQ file plus optional routing hints.

    ``start_sample`` and ``sample_count`` are measured in complex samples, not
    bytes.  Interpretation of ``sample_format`` belongs to the backend.
    """

    path: Path
    sample_format: str
    sample_rate_hz: float
    start_sample: int = 0
    sample_count: int | None = None
    hints: Mapping[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        path = Path(self.path)
        if not str(path):
            raise ValueError("path must be non-empty")
        if not isinstance(self.sample_format, str) or not self.sample_format.strip():
            raise ValueError("sample_format must be a non-empty string")
        if (
            isinstance(self.sample_rate_hz, bool)
            or not isinstance(self.sample_rate_hz, (int, float))
            or not math.isfinite(float(self.sample_rate_hz))
            or self.sample_rate_hz <= 0
        ):
            raise ValueError("sample_rate_hz must be finite and positive")
        if isinstance(self.start_sample, bool) or not isinstance(self.start_sample, int):
            raise TypeError("start_sample must be an integer")
        if self.start_sample < 0:
            raise ValueError("start_sample must be non-negative")
        if self.sample_count is not None and (
            isinstance(self.sample_count, bool)
            or not isinstance(self.sample_count, int)
            or self.sample_count <= 0
        ):
            raise ValueError("sample_count must be a positive integer or None")
        object.__setattr__(self, "path", path)
        object.__setattr__(self, "sample_rate_hz", float(self.sample_rate_hz))
        object.__setattr__(self, "hints", _json_mapping("hints", self.hints))


@dataclass(frozen=True, slots=True)
class ExternalBackendCapabilities:
    """Machine-readable routing metadata advertised by an external backend."""

    modulations: tuple[str, ...]
    framing: tuple[str, ...]
    fec: tuple[str, ...]
    sample_formats: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "modulations", _non_empty_tuple("modulations", self.modulations)
        )
        object.__setattr__(self, "framing", _non_empty_tuple("framing", self.framing))
        object.__setattr__(self, "fec", _non_empty_tuple("fec", self.fec))
        object.__setattr__(
            self,
            "sample_formats",
            _non_empty_tuple("sample_formats", self.sample_formats),
        )


@dataclass(frozen=True, slots=True)
class BackendProcessOutput:
    """Bounded process output given to a backend-specific parser."""

    stdout: bytes
    stderr: bytes
    returncode: int


@dataclass(frozen=True, slots=True)
class ParsedByteCandidate:
    """One parser result before backend and capture provenance are attached."""

    payload: bytes
    provenance: Mapping[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.payload, bytes) or not self.payload:
            raise ValueError("payload must be non-empty bytes")
        object.__setattr__(
            self, "provenance", _json_mapping("provenance", self.provenance)
        )


@dataclass(frozen=True, slots=True)
class CandidateProvenance:
    """Origin of a byte candidate, independent of its eventual protocol."""

    backend_id: str
    backend_version: str
    capture_path: str
    sample_format: str
    sample_rate_hz: float
    start_sample: int
    sample_count: int | None
    capture_hints: Mapping[str, JsonValue]
    argv: tuple[str, ...]
    parser: Mapping[str, JsonValue]


@dataclass(frozen=True, slots=True)
class ExternalByteCandidate:
    payload: bytes
    provenance: CandidateProvenance


@dataclass(frozen=True, slots=True)
class ExternalBackendFailure:
    code: FailureCode
    message: str
    returncode: int | None = None


@dataclass(frozen=True, slots=True)
class ExternalBackendResult:
    """A total result: expected backend failures are represented as data."""

    backend_id: str
    backend_version: str
    capabilities: ExternalBackendCapabilities
    status: BackendStatus
    candidates: tuple[ExternalByteCandidate, ...]
    argv: tuple[str, ...]
    stdout: bytes
    stderr: bytes
    returncode: int | None
    failure: ExternalBackendFailure | None

    @property
    def succeeded(self) -> bool:
        return self.status == "success"


ArgvBuilder: TypeAlias = Callable[[CaptureSegment], Sequence[str]]
OutputParser: TypeAlias = Callable[
    [BackendProcessOutput, CaptureSegment], Iterable[ParsedByteCandidate]
]


@dataclass(frozen=True, slots=True)
class _ProcessResult:
    stdout: bytes
    stderr: bytes
    returncode: int | None
    failure_code: FailureCode | None


@dataclass(frozen=True, slots=True)
class ExternalFileBackend:
    """Run one external file-based demodulator under explicit resource bounds.

    The caller supplies an argv builder and an output parser.  Arguments are
    always passed directly to :class:`subprocess.Popen`; no shell is involved.
    The byte limit applies to stdout and stderr combined.
    """

    backend_id: str
    backend_version: str
    capabilities: ExternalBackendCapabilities
    argv_builder: ArgvBuilder
    parser: OutputParser
    timeout_seconds: float
    max_output_bytes: int
    environment_overrides: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.backend_id, str) or not self.backend_id.strip():
            raise ValueError("backend_id must be a non-empty string")
        if not isinstance(self.backend_version, str) or not self.backend_version.strip():
            raise ValueError("backend_version must be a non-empty string")
        if (
            isinstance(self.timeout_seconds, bool)
            or not isinstance(self.timeout_seconds, (int, float))
            or not math.isfinite(float(self.timeout_seconds))
            or self.timeout_seconds <= 0
        ):
            raise ValueError("timeout_seconds must be finite and positive")
        if (
            isinstance(self.max_output_bytes, bool)
            or not isinstance(self.max_output_bytes, int)
            or self.max_output_bytes < 1
        ):
            raise ValueError("max_output_bytes must be a positive integer")
        if not callable(self.argv_builder) or not callable(self.parser):
            raise TypeError("argv_builder and parser must be callable")
        if not isinstance(self.environment_overrides, Mapping):
            raise TypeError("environment_overrides must be a mapping")
        environment = dict(self.environment_overrides)
        if any(
            not isinstance(key, str)
            or not key
            or "\x00" in key
            or "=" in key
            or not isinstance(value, str)
            or "\x00" in value
            for key, value in environment.items()
        ):
            raise ValueError(
                "environment overrides must contain valid string names and values"
            )
        object.__setattr__(self, "timeout_seconds", float(self.timeout_seconds))
        object.__setattr__(
            self,
            "environment_overrides",
            MappingProxyType(dict(sorted(environment.items()))),
        )

    def run(self, segment: CaptureSegment) -> ExternalBackendResult:
        """Run the backend once without propagating operational failures."""

        if not isinstance(segment, CaptureSegment):
            return self._failure(
                "input_error", "segment is not a CaptureSegment", argv=()
            )
        if not segment.path.is_file():
            return self._failure(
                "input_error", "capture path is not a regular file", argv=()
            )
        try:
            argv = self._validated_argv(self.argv_builder(segment))
        except Exception as exc:
            return self._failure(
                "invocation_error",
                f"argv builder failed: {type(exc).__name__}: {exc}",
                argv=(),
            )

        process = _run_bounded_process(
            argv,
            timeout_seconds=self.timeout_seconds,
            max_output_bytes=self.max_output_bytes,
            environment_overrides=self.environment_overrides,
        )
        if process.failure_code is not None:
            messages = {
                "launch_error": "backend process could not be launched",
                "timeout": "backend exceeded its timeout",
                "output_limit": "backend exceeded its output limit",
                "nonzero_exit": "backend exited with a non-zero status",
            }
            return self._failure(
                process.failure_code,
                messages[process.failure_code],
                argv=argv,
                stdout=process.stdout,
                stderr=process.stderr,
                returncode=process.returncode,
            )

        output = BackendProcessOutput(
            stdout=process.stdout,
            stderr=process.stderr,
            returncode=process.returncode if process.returncode is not None else 0,
        )
        try:
            parsed = tuple(self.parser(output, segment))
            candidates = self._candidates(parsed, segment=segment, argv=argv)
        except Exception as exc:
            return self._failure(
                "parse_error",
                f"output parser failed: {type(exc).__name__}: {exc}",
                argv=argv,
                stdout=process.stdout,
                stderr=process.stderr,
                returncode=process.returncode,
            )

        return ExternalBackendResult(
            backend_id=self.backend_id,
            backend_version=self.backend_version,
            capabilities=self.capabilities,
            status="success",
            candidates=candidates,
            argv=argv,
            stdout=process.stdout,
            stderr=process.stderr,
            returncode=process.returncode,
            failure=None,
        )

    @staticmethod
    def _validated_argv(values: Sequence[str]) -> tuple[str, ...]:
        if isinstance(values, (str, bytes)):
            raise TypeError("argv must be a sequence of strings, not a command string")
        argv = tuple(values)
        if not argv:
            raise ValueError("argv must not be empty")
        for value in argv:
            if not isinstance(value, str) or not value or "\x00" in value:
                raise ValueError("argv entries must be non-empty NUL-free strings")
        return argv

    def _candidates(
        self,
        parsed: Sequence[ParsedByteCandidate],
        *,
        segment: CaptureSegment,
        argv: tuple[str, ...],
    ) -> tuple[ExternalByteCandidate, ...]:
        unique: dict[tuple[bytes, str], ParsedByteCandidate] = {}
        for candidate in parsed:
            if not isinstance(candidate, ParsedByteCandidate):
                raise TypeError("parser must return ParsedByteCandidate objects")
            metadata = json.dumps(
                dict(candidate.provenance),
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
            unique.setdefault((candidate.payload, metadata), candidate)

        output: list[ExternalByteCandidate] = []
        for (payload, metadata), candidate in sorted(
            unique.items(), key=lambda item: (item[0][0], item[0][1])
        ):
            del metadata
            provenance = CandidateProvenance(
                backend_id=self.backend_id,
                backend_version=self.backend_version,
                capture_path=str(segment.path),
                sample_format=segment.sample_format,
                sample_rate_hz=segment.sample_rate_hz,
                start_sample=segment.start_sample,
                sample_count=segment.sample_count,
                capture_hints=segment.hints,
                argv=argv,
                parser=candidate.provenance,
            )
            output.append(ExternalByteCandidate(payload=payload, provenance=provenance))
        return tuple(output)

    def _failure(
        self,
        code: FailureCode,
        message: str,
        *,
        argv: tuple[str, ...],
        stdout: bytes = b"",
        stderr: bytes = b"",
        returncode: int | None = None,
    ) -> ExternalBackendResult:
        return ExternalBackendResult(
            backend_id=self.backend_id,
            backend_version=self.backend_version,
            capabilities=self.capabilities,
            status="failure",
            candidates=(),
            argv=argv,
            stdout=stdout,
            stderr=stderr,
            returncode=returncode,
            failure=ExternalBackendFailure(
                code=code, message=message, returncode=returncode
            ),
        )


def _run_bounded_process(
    argv: tuple[str, ...],
    *,
    timeout_seconds: float,
    max_output_bytes: int,
    environment_overrides: Mapping[str, str],
) -> _ProcessResult:
    """Capture stdout/stderr incrementally and kill on either configured bound."""

    try:
        environment = os.environ.copy()
        environment.update(environment_overrides)
        process = subprocess.Popen(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
            close_fds=True,
            start_new_session=os.name == "posix",
            env=environment,
        )
    except OSError:
        return _ProcessResult(b"", b"", None, "launch_error")

    assert process.stdout is not None
    assert process.stderr is not None
    streams = {
        process.stdout.fileno(): ("stdout", process.stdout),
        process.stderr.fileno(): ("stderr", process.stderr),
    }
    chunks: dict[str, list[bytes]] = {"stdout": [], "stderr": []}
    captured = 0
    deadline = time.monotonic() + timeout_seconds
    failure_code: FailureCode | None = None
    selector = selectors.DefaultSelector()
    try:
        for file_descriptor, (name, _) in streams.items():
            os.set_blocking(file_descriptor, False)
            selector.register(file_descriptor, selectors.EVENT_READ, name)

        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                failure_code = "timeout"
                break
            events = selector.select(min(remaining, 0.1))
            for key, _ in sorted(events, key=lambda event: event[0].fd):
                remaining_bytes = max_output_bytes - captured
                chunk = os.read(key.fd, min(65_536, remaining_bytes + 1))
                if not chunk:
                    selector.unregister(key.fd)
                    continue
                if len(chunk) > remaining_bytes:
                    chunks[key.data].append(chunk[:remaining_bytes])
                    captured = max_output_bytes
                    failure_code = "output_limit"
                    break
                chunks[key.data].append(chunk)
                captured += len(chunk)
            if failure_code is not None:
                break

        if failure_code is None:
            remaining = deadline - time.monotonic()
            try:
                process.wait(timeout=max(0.0, remaining))
            except subprocess.TimeoutExpired:
                failure_code = "timeout"
        if failure_code is not None:
            _kill_process(process)
        elif process.returncode != 0:
            failure_code = "nonzero_exit"
    finally:
        selector.close()
        process.stdout.close()
        process.stderr.close()

    return _ProcessResult(
        stdout=b"".join(chunks["stdout"]),
        stderr=b"".join(chunks["stderr"]),
        returncode=process.returncode,
        failure_code=failure_code,
    )


def _kill_process(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is None:
        try:
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
        except ProcessLookupError:
            pass
    try:
        process.wait(timeout=1.0)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
