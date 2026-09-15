"""Concrete gr-satellites adapter built on the neutral external runner.

The adapter uses the executable's KISS file output.  A gr-satellites PDU is a
byte candidate, not automatically valid telemetry: protocol-specific integrity
validation remains a downstream responsibility.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import math
from pathlib import Path
import sys
import tempfile
from types import MappingProxyType
from typing import Literal, Mapping

from .canonical import canonical_json
from .external_backends import (
    BackendProcessOutput,
    CaptureSegment,
    ExternalBackendCapabilities,
    ExternalBackendFailure,
    ExternalBackendResult,
    ExternalFileBackend,
    ParsedByteCandidate,
)
from .models import JsonValue


FEND = 0xC0
FESC = 0xDB
TFEND = 0xDC
TFESC = 0xDD
KISS_DATA_COMMAND = 0x00
KISS_TIMESTAMP_COMMAND = 0x09


def _non_empty(name: str, value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value


def _strings(name: str, values: tuple[str, ...], *, required: bool) -> tuple[str, ...]:
    result = tuple(values)
    if required and not result:
        raise ValueError(f"{name} must not be empty")
    if any(not isinstance(value, str) or not value.strip() for value in result):
        raise ValueError(f"{name} values must be non-empty strings")
    if len(set(result)) != len(result):
        raise ValueError(f"{name} values must be unique")
    return result


def _json_mapping(name: str, value: Mapping[str, JsonValue]) -> Mapping[str, JsonValue]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{name} must be a mapping")
    # The round-trip both validates and detaches nested caller-owned objects.
    copied = json.loads(canonical_json(dict(value)))
    return MappingProxyType(dict(sorted(copied.items())))


@dataclass(frozen=True, slots=True)
class GrSatellitesTransmitter:
    """Capabilities copied from one transmitter in the selected SatYAML."""

    transmitter_id: str
    modulation: str
    framing: str
    fec: tuple[str, ...] = ()
    metadata: Mapping[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in ("transmitter_id", "modulation", "framing"):
            _non_empty(name, getattr(self, name))
        object.__setattr__(self, "fec", _strings("fec", self.fec, required=False))
        object.__setattr__(self, "metadata", _json_mapping("metadata", self.metadata))

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "transmitter_id": self.transmitter_id,
            "modulation": self.modulation,
            "framing": self.framing,
            "fec": list(self.fec),
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True, slots=True)
class GrSatellitesProfile:
    """One executable profile selected by name or by an explicit SatYAML file."""

    selector: str
    selector_kind: Literal["name", "satyaml"]
    transmitters: tuple[GrSatellitesTransmitter, ...]
    name: str | None = None
    norad_id: int | None = None

    def __post_init__(self) -> None:
        _non_empty("selector", self.selector)
        if self.selector_kind not in ("name", "satyaml"):
            raise ValueError("selector_kind must be 'name' or 'satyaml'")
        if self.name is not None:
            _non_empty("name", self.name)
        if self.norad_id is not None and (
            isinstance(self.norad_id, bool)
            or not isinstance(self.norad_id, int)
            or self.norad_id <= 0
        ):
            raise ValueError("norad_id must be a positive integer or None")
        transmitters = tuple(self.transmitters)
        if not transmitters or any(
            not isinstance(item, GrSatellitesTransmitter) for item in transmitters
        ):
            raise ValueError("profile must declare at least one transmitter")
        identifiers = [item.transmitter_id for item in transmitters]
        if len(set(identifiers)) != len(identifiers):
            raise ValueError("profile transmitter IDs must be unique")
        object.__setattr__(self, "transmitters", transmitters)

    @property
    def modulations(self) -> tuple[str, ...]:
        return tuple(sorted({item.modulation for item in self.transmitters}))

    @property
    def framing(self) -> tuple[str, ...]:
        return tuple(sorted({item.framing for item in self.transmitters}))

    @property
    def fec(self) -> tuple[str, ...]:
        return tuple(sorted({fec for item in self.transmitters for fec in item.fec}))

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "selector": self.selector,
            "selector_kind": self.selector_kind,
            "name": self.name,
            "norad_id": self.norad_id,
            "transmitters": [item.to_dict() for item in self.transmitters],
            "modulations": list(self.modulations),
            "framing": list(self.framing),
            "fec": list(self.fec),
        }


@dataclass(frozen=True, slots=True)
class KissTimestampCommand:
    timestamp_ms: int
    port: int
    record_index: int


@dataclass(frozen=True, slots=True)
class KissDataFrame:
    payload: bytes
    port: int
    record_index: int
    timestamp_ms: int | None


@dataclass(frozen=True, slots=True)
class KissOtherCommand:
    command: int
    port: int
    payload: bytes
    record_index: int


@dataclass(frozen=True, slots=True)
class KissParseResult:
    data_frames: tuple[KissDataFrame, ...]
    timestamp_commands: tuple[KissTimestampCommand, ...]
    other_commands: tuple[KissOtherCommand, ...]
    malformed_records: int


def parse_kiss(data: bytes) -> KissParseResult:
    """Parse a KISS byte stream with complete FEND/FESC handling.

    Timestamp control records (command 9, big-endian milliseconds) are retained
    separately and associated with following data records. Malformed records
    are counted and skipped; they never become byte candidates.
    """

    if not isinstance(data, bytes):
        raise TypeError("KISS input must be bytes")
    frames: list[KissDataFrame] = []
    timestamps: list[KissTimestampCommand] = []
    commands: list[KissOtherCommand] = []
    record = bytearray()
    escaped = False
    invalid = False
    malformed = 0
    record_index = 0
    latest_timestamp_ms: int | None = None

    def finish_record() -> None:
        nonlocal invalid, malformed, record_index, latest_timestamp_ms
        if not record and not invalid:
            return
        current_index = record_index
        record_index += 1
        if invalid or not record:
            malformed += 1
            return
        control = record[0]
        port = control >> 4
        command = control & 0x0F
        payload = bytes(record[1:])
        if command == KISS_DATA_COMMAND:
            if not payload:
                malformed += 1
                return
            frames.append(
                KissDataFrame(
                    payload=payload,
                    port=port,
                    record_index=current_index,
                    timestamp_ms=latest_timestamp_ms,
                )
            )
        elif command == KISS_TIMESTAMP_COMMAND:
            if len(payload) != 8:
                malformed += 1
                return
            latest_timestamp_ms = int.from_bytes(payload, "big")
            timestamps.append(
                KissTimestampCommand(
                    timestamp_ms=latest_timestamp_ms,
                    port=port,
                    record_index=current_index,
                )
            )
        else:
            commands.append(
                KissOtherCommand(
                    command=command,
                    port=port,
                    payload=payload,
                    record_index=current_index,
                )
            )

    for value in data:
        if value == FEND:
            if escaped:
                invalid = True
                escaped = False
            finish_record()
            record.clear()
            invalid = False
        elif invalid:
            continue
        elif escaped:
            if value == TFEND:
                record.append(FEND)
            elif value == TFESC:
                record.append(FESC)
            else:
                invalid = True
            escaped = False
        elif value == FESC:
            escaped = True
        else:
            record.append(value)

    if escaped or invalid or record:
        malformed += 1

    return KissParseResult(
        data_frames=tuple(frames),
        timestamp_commands=tuple(timestamps),
        other_commands=tuple(commands),
        malformed_records=malformed,
    )


@dataclass(frozen=True, slots=True)
class GrSatellitesBackend:
    """Run one explicitly described gr-satellites profile on a complete IQ file."""

    executable: str
    executable_version: str
    profile: GrSatellitesProfile
    timeout_seconds: float
    max_output_bytes: int
    max_kiss_bytes: int = 16 * 1024 * 1024
    supports_rawint16_iq: bool = True
    supports_rawfile_complex64: bool = True
    backend_id: str = "gr_satellites"

    def __post_init__(self) -> None:
        for name in ("executable", "executable_version", "backend_id"):
            _non_empty(name, getattr(self, name))
        if not isinstance(self.profile, GrSatellitesProfile):
            raise TypeError("profile must be a GrSatellitesProfile")
        if (
            isinstance(self.timeout_seconds, bool)
            or not isinstance(self.timeout_seconds, (int, float))
            or not math.isfinite(float(self.timeout_seconds))
            or self.timeout_seconds <= 0
        ):
            raise ValueError("timeout_seconds must be finite and positive")
        for name in ("max_output_bytes", "max_kiss_bytes"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{name} must be a positive integer")
        if not isinstance(self.supports_rawint16_iq, bool) or not isinstance(
            self.supports_rawfile_complex64, bool
        ):
            raise TypeError("input support flags must be booleans")
        if not self.supported_sample_formats:
            raise ValueError("at least one IQ input format must be enabled")
        object.__setattr__(self, "timeout_seconds", float(self.timeout_seconds))

    @property
    def supports_sample_ranges(self) -> bool:
        """The inspected gr-satellites CLI has no sample offset/count options."""

        return False

    @property
    def supported_sample_formats(self) -> tuple[str, ...]:
        if sys.byteorder != "little":
            return ()
        formats: list[str] = []
        if self.supports_rawint16_iq:
            formats.append("ci16_le")
        if self.supports_rawfile_complex64:
            formats.append("cf32_le")
        return tuple(formats)

    @property
    def capabilities(self) -> ExternalBackendCapabilities:
        return ExternalBackendCapabilities(
            modulations=self.profile.modulations,
            framing=self.profile.framing,
            fec=self.profile.fec,
            sample_formats=self.supported_sample_formats,
        )

    def validate_segment(self, segment: CaptureSegment) -> str | None:
        if not isinstance(segment, CaptureSegment):
            return "segment is not a CaptureSegment"
        if segment.sample_format not in self.supported_sample_formats:
            return f"unsupported IQ sample format: {segment.sample_format}"
        if segment.start_sample != 0 or segment.sample_count is not None:
            return (
                "this gr-satellites CLI cannot map sample start/count; "
                "materialize the exact segment as a separate full file"
            )
        if self.profile.selector_kind == "satyaml" and not Path(
            self.profile.selector
        ).is_file():
            return "SatYAML profile path is not a regular file"
        if (
            self.profile.selector_kind == "satyaml"
            and not self.profile.selector.lower().endswith(".yml")
        ):
            return "gr-satellites recognizes SatYAML file selectors ending in .yml"
        if self.profile.selector_kind == "name" and self.profile.selector.startswith(
            "-"
        ):
            return "gr-satellites profile names must not begin with '-'"
        start_time = segment.hints.get("start_time")
        if start_time is not None and (
            not isinstance(start_time, str) or not start_time.strip()
        ):
            return "start_time hint must be a non-empty string"
        return None

    def build_argv(
        self, segment: CaptureSegment, *, kiss_output_path: Path
    ) -> tuple[str, ...]:
        """Build the exact shell-free argv for the selected profile and IQ type."""

        problem = self.validate_segment(segment)
        if problem is not None:
            raise ValueError(problem)
        input_flag = {
            "ci16_le": "--rawint16",
            "cf32_le": "--rawfile",
        }[segment.sample_format]
        argv = [
            self.executable,
            self.profile.selector,
            input_flag,
            str(segment.path),
            "--samp_rate",
            format(segment.sample_rate_hz, ".17g"),
            "--iq",
            "--kiss_out",
            str(kiss_output_path),
            "--hexdump",
        ]
        start_time = segment.hints.get("start_time")
        if isinstance(start_time, str):
            argv.extend(("--start_time", start_time))
        return tuple(argv)

    def run(self, segment: CaptureSegment) -> ExternalBackendResult:
        """Decode one full-file segment and return unvalidated byte candidates."""

        problem = self.validate_segment(segment)
        if problem is not None:
            return ExternalBackendResult(
                backend_id=self.backend_id,
                backend_version=self.executable_version,
                capabilities=self.capabilities,
                status="failure",
                candidates=(),
                argv=(),
                stdout=b"",
                stderr=b"",
                returncode=None,
                failure=ExternalBackendFailure(
                    code="input_error", message=problem, returncode=None
                ),
            )

        with tempfile.TemporaryDirectory(prefix="telemetry-yield-grsat-") as directory:
            kiss_path = Path(directory) / "decoded.kiss"

            def argv_builder(input_segment: CaptureSegment) -> tuple[str, ...]:
                return self.build_argv(
                    input_segment,
                    kiss_output_path=kiss_path,
                )

            def output_parser(
                output: BackendProcessOutput, input_segment: CaptureSegment
            ) -> tuple[ParsedByteCandidate, ...]:
                del output
                if not kiss_path.is_file():
                    raise ValueError("gr-satellites did not create its KISS output")
                if kiss_path.stat().st_size > self.max_kiss_bytes:
                    raise ValueError("gr-satellites KISS output exceeded its byte limit")
                parsed = parse_kiss(kiss_path.read_bytes())
                transmitters = [item.to_dict() for item in self.profile.transmitters]
                candidates: list[ParsedByteCandidate] = []
                for item in parsed.data_frames:
                    provenance: dict[str, JsonValue] = {
                        "container": "kiss",
                        "kiss_port": item.port,
                        "kiss_record_index": item.record_index,
                        "kiss_timestamp_ms": item.timestamp_ms,
                        "malformed_kiss_records": parsed.malformed_records,
                        "profile_selector": self.profile.selector,
                        "profile_selector_kind": self.profile.selector_kind,
                        "profile_name": self.profile.name,
                        "profile_norad_id": self.profile.norad_id,
                        "profile_transmitters": transmitters,
                        "transmitter_attribution": None,
                        "candidate_validation": "pending_downstream_validator",
                    }
                    candidates.append(
                        ParsedByteCandidate(item.payload, provenance=provenance)
                    )
                return tuple(candidates)

            runner = ExternalFileBackend(
                backend_id=self.backend_id,
                backend_version=self.executable_version,
                capabilities=self.capabilities,
                argv_builder=argv_builder,
                parser=output_parser,
                timeout_seconds=self.timeout_seconds,
                max_output_bytes=self.max_output_bytes,
                environment_overrides={"GR_SATELLITES_SUBMIT_TLM": "0"},
            )
            return runner.run(segment)
