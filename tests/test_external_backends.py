from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest

from telemetry_yield.external_backends import (
    BackendProcessOutput,
    CaptureSegment,
    ExternalBackendCapabilities,
    ExternalFileBackend,
    ParsedByteCandidate,
)


CAPABILITIES = ExternalBackendCapabilities(
    modulations=("fsk", "bpsk"),
    framing=("ax25", "ccsds_tm"),
    fec=("none", "convolutional", "reed_solomon"),
    sample_formats=("cf32_le",),
)


def _capture(tmp_path: Path, **overrides: object) -> CaptureSegment:
    path = tmp_path / "capture;not-a-shell-command.cf32"
    path.write_bytes(b"\x00" * 32)
    values: dict[str, object] = {
        "path": path,
        "sample_format": "cf32_le",
        "sample_rate_hz": 48_000,
        "start_sample": 12,
        "sample_count": 8,
        "hints": {"modulation": "unknown", "priority": 0.9},
    }
    values.update(overrides)
    return CaptureSegment(**values)  # type: ignore[arg-type]


def _backend(
    *,
    argv_builder,
    parser=lambda output, segment: (),
    timeout_seconds: float = 1.0,
    max_output_bytes: int = 4096,
) -> ExternalFileBackend:
    return ExternalFileBackend(
        backend_id="example_decoder",
        backend_version="1.2.3",
        capabilities=CAPABILITIES,
        argv_builder=argv_builder,
        parser=parser,
        timeout_seconds=timeout_seconds,
        max_output_bytes=max_output_bytes,
    )


def test_success_passes_segment_as_argv_and_attaches_provenance(
    tmp_path: Path,
) -> None:
    capture = _capture(tmp_path)

    def argv(segment: CaptureSegment) -> tuple[str, ...]:
        code = (
            "import json,sys; "
            "print(json.dumps({'hex':'c0ffee','args':sys.argv[1:]}))"
        )
        return (
            sys.executable,
            "-c",
            code,
            str(segment.path),
            segment.sample_format,
            str(segment.sample_rate_hz),
            str(segment.start_sample),
            str(segment.sample_count),
        )

    def parser(
        output: BackendProcessOutput, segment: CaptureSegment
    ) -> tuple[ParsedByteCandidate, ...]:
        record = json.loads(output.stdout)
        assert record["args"][0] == str(segment.path)
        return (
            ParsedByteCandidate(
                bytes.fromhex(record["hex"]),
                {"sync_errors": 1, "source": "stdout-json"},
            ),
        )

    result = _backend(argv_builder=argv, parser=parser).run(capture)

    assert result.succeeded
    assert result.failure is None
    assert result.returncode == 0
    assert len(result.candidates) == 1
    candidate = result.candidates[0]
    assert candidate.payload == bytes.fromhex("c0ffee")
    assert candidate.provenance.backend_id == "example_decoder"
    assert candidate.provenance.capture_path == str(capture.path)
    assert candidate.provenance.sample_format == "cf32_le"
    assert candidate.provenance.start_sample == 12
    assert candidate.provenance.sample_count == 8
    assert candidate.provenance.capture_hints["modulation"] == "unknown"
    assert candidate.provenance.parser["sync_errors"] == 1
    assert result.capabilities.framing == ("ax25", "ccsds_tm")


def test_argv_is_not_interpreted_by_a_shell(tmp_path: Path) -> None:
    capture = _capture(tmp_path)
    marker = tmp_path / "should-not-exist"
    hostile = f"; touch {marker}"
    backend = _backend(
        argv_builder=lambda _: (
            sys.executable,
            "-c",
            "import sys; print(sys.argv[1])",
            hostile,
        )
    )

    result = backend.run(capture)

    assert result.succeeded
    assert result.stdout.decode().strip() == hostile
    assert not marker.exists()


def test_explicit_environment_override_is_passed_without_a_shell(
    tmp_path: Path,
) -> None:
    backend = ExternalFileBackend(
        backend_id="example_decoder",
        backend_version="1.2.3",
        capabilities=CAPABILITIES,
        argv_builder=lambda _: (
            sys.executable,
            "-c",
            "import os; print(os.environ['TELEMETRY_YIELD_TEST_MODE'])",
        ),
        parser=lambda output, segment: (),
        timeout_seconds=1.0,
        max_output_bytes=4096,
        environment_overrides={"TELEMETRY_YIELD_TEST_MODE": "offline"},
    )

    result = backend.run(_capture(tmp_path))

    assert result.succeeded
    assert result.stdout == b"offline\n"


def test_timeout_is_a_structured_failure(tmp_path: Path) -> None:
    result = _backend(
        argv_builder=lambda _: (
            sys.executable,
            "-c",
            "import time; time.sleep(10)",
        ),
        timeout_seconds=0.05,
    ).run(_capture(tmp_path))

    assert not result.succeeded
    assert result.candidates == ()
    assert result.failure is not None
    assert result.failure.code == "timeout"


def test_output_limit_is_enforced_before_parsing(tmp_path: Path) -> None:
    parser_called = False

    def parser(output: BackendProcessOutput, segment: CaptureSegment):
        nonlocal parser_called
        parser_called = True
        return ()

    result = _backend(
        argv_builder=lambda _: (
            sys.executable,
            "-c",
            "import sys; sys.stdout.buffer.write(b'x' * 4096)",
        ),
        parser=parser,
        max_output_bytes=64,
    ).run(_capture(tmp_path))

    assert not result.succeeded
    assert result.failure is not None
    assert result.failure.code == "output_limit"
    assert len(result.stdout) + len(result.stderr) == 64
    assert not parser_called


def test_nonzero_exit_is_a_structured_failure(tmp_path: Path) -> None:
    result = _backend(
        argv_builder=lambda _: (sys.executable, "-c", "raise SystemExit(7)")
    ).run(_capture(tmp_path))

    assert result.failure is not None
    assert result.failure.code == "nonzero_exit"
    assert result.failure.returncode == 7


def test_parser_failure_does_not_escape(tmp_path: Path) -> None:
    def parser(output: BackendProcessOutput, segment: CaptureSegment):
        raise ValueError("bad decoder output")

    result = _backend(
        argv_builder=lambda _: (sys.executable, "-c", "print('not-a-frame')"),
        parser=parser,
    ).run(_capture(tmp_path))

    assert result.failure is not None
    assert result.failure.code == "parse_error"
    assert "ValueError: bad decoder output" in result.failure.message


def test_command_string_and_missing_capture_are_data_failures(tmp_path: Path) -> None:
    capture = _capture(tmp_path)
    command_string = _backend(argv_builder=lambda _: "decoder --unsafe")
    invalid_argv = command_string.run(capture)
    assert invalid_argv.failure is not None
    assert invalid_argv.failure.code == "invocation_error"

    missing = CaptureSegment(
        path=tmp_path / "missing.cf32",
        sample_format="cf32_le",
        sample_rate_hz=48_000,
    )
    missing_result = _backend(argv_builder=lambda _: ("decoder",)).run(missing)
    assert missing_result.failure is not None
    assert missing_result.failure.code == "input_error"


def test_candidates_are_deduplicated_and_sorted(tmp_path: Path) -> None:
    def parser(output: BackendProcessOutput, segment: CaptureSegment):
        return (
            ParsedByteCandidate(b"z", {"rank": 2}),
            ParsedByteCandidate(b"a", {"rank": 1}),
            ParsedByteCandidate(b"z", {"rank": 2}),
        )

    result = _backend(
        argv_builder=lambda _: (sys.executable, "-c", "pass"), parser=parser
    ).run(_capture(tmp_path))

    assert result.succeeded
    assert [candidate.payload for candidate in result.candidates] == [b"a", b"z"]


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("sample_rate_hz", 0),
        ("sample_rate_hz", float("nan")),
        ("start_sample", -1),
        ("sample_count", 0),
    ),
)
def test_capture_rejects_invalid_bounds(
    tmp_path: Path, field: str, value: object
) -> None:
    with pytest.raises((TypeError, ValueError)):
        _capture(tmp_path, **{field: value})
