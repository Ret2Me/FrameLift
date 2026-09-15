from __future__ import annotations

from pathlib import Path
import sys

from telemetry_yield.external_backends import CaptureSegment
from telemetry_yield.gr_satellites_backend import (
    FEND,
    FESC,
    TFEND,
    TFESC,
    GrSatellitesBackend,
    GrSatellitesProfile,
    GrSatellitesTransmitter,
    parse_kiss,
)


def _escape(payload: bytes) -> bytes:
    output = bytearray()
    for value in payload:
        if value == FEND:
            output.extend((FESC, TFEND))
        elif value == FESC:
            output.extend((FESC, TFESC))
        else:
            output.append(value)
    return bytes(output)


def _kiss_record(control: int, payload: bytes) -> bytes:
    return bytes((FEND,)) + _escape(bytes((control,)) + payload) + bytes((FEND,))


def _profile(selector: str = "CANVAS") -> GrSatellitesProfile:
    return GrSatellitesProfile(
        selector=selector,
        selector_kind="name",
        transmitters=(
            GrSatellitesTransmitter(
                transmitter_id="UHF telemetry",
                modulation="FSK",
                framing="AX.25 G3RUH",
                fec=("none",),
                metadata={"baudrate": 9600, "frequency": 437_250_000},
            ),
        ),
    )


def _backend(selector: str = "CANVAS", **overrides: object) -> GrSatellitesBackend:
    values: dict[str, object] = {
        "executable": "/usr/bin/gr_satellites",
        "executable_version": "5.9.0",
        "profile": _profile(selector),
        "timeout_seconds": 2.0,
        "max_output_bytes": 4096,
    }
    values.update(overrides)
    return GrSatellitesBackend(**values)  # type: ignore[arg-type]


def _capture(tmp_path: Path, **overrides: object) -> CaptureSegment:
    iq = tmp_path / "capture.iq"
    iq.write_bytes(b"\x00" * 32)
    values: dict[str, object] = {
        "path": iq,
        "sample_format": "ci16_le",
        "sample_rate_hz": 57_600,
    }
    values.update(overrides)
    return CaptureSegment(**values)  # type: ignore[arg-type]


def test_kiss_parser_unescapes_fend_and_fesc() -> None:
    payload = bytes((0x01, FEND, 0x02, FESC, 0x03))

    result = parse_kiss(_kiss_record(0x20, payload))

    assert result.malformed_records == 0
    assert result.timestamp_commands == ()
    assert len(result.data_frames) == 1
    assert result.data_frames[0].port == 2
    assert result.data_frames[0].payload == payload


def test_timestamp_commands_are_separate_and_attached_to_following_data() -> None:
    timestamp = 1_700_000_000_123
    stream = (
        _kiss_record(0x09, timestamp.to_bytes(8, "big"))
        + _kiss_record(0x00, b"first")
        + _kiss_record(0x06, b"hardware-command")
        + _kiss_record(0x00, b"second")
    )

    result = parse_kiss(stream)

    assert [item.timestamp_ms for item in result.timestamp_commands] == [timestamp]
    assert [item.payload for item in result.data_frames] == [b"first", b"second"]
    assert all(item.timestamp_ms == timestamp for item in result.data_frames)
    assert len(result.other_commands) == 1
    assert result.other_commands[0].command == 6


def test_malformed_and_unterminated_records_never_become_frames() -> None:
    invalid_escape = bytes((FEND, 0x00, FESC, 0x01, FEND))
    unterminated = bytes((0x00, 0x44))

    result = parse_kiss(invalid_escape + unterminated)

    assert result.data_frames == ()
    assert result.timestamp_commands == ()
    assert result.malformed_records == 2
    assert parse_kiss(b"").data_frames == ()


def test_argv_maps_only_formats_supported_by_inspected_cli(tmp_path: Path) -> None:
    backend = _backend()
    kiss_path = tmp_path / "output.kiss"
    ci16 = backend.build_argv(_capture(tmp_path), kiss_output_path=kiss_path)
    cf32 = backend.build_argv(
        _capture(tmp_path, sample_format="cf32_le"), kiss_output_path=kiss_path
    )

    assert "--rawint16" in ci16
    assert "--rawfile" not in ci16
    assert "--rawfile" in cf32
    assert "--iq" in ci16 and "--samp_rate" in ci16
    assert ci16[0] == "/usr/bin/gr_satellites"
    assert ci16[1] == "CANVAS"
    assert backend.capabilities.modulations == ("FSK",)
    assert backend.capabilities.framing == ("AX.25 G3RUH",)
    assert backend.capabilities.fec == ("none",)
    assert backend.capabilities.sample_formats == ("ci16_le", "cf32_le")
    assert not backend.supports_sample_ranges


def test_bounded_segment_is_rejected_instead_of_processing_whole_file(
    tmp_path: Path,
) -> None:
    segment = _capture(tmp_path, start_sample=10, sample_count=20)

    result = _backend().run(segment)

    assert not result.succeeded
    assert result.failure is not None
    assert result.failure.code == "input_error"
    assert "cannot map sample start/count" in result.failure.message
    assert result.argv == ()


def test_adapter_reads_controlled_kiss_file_as_unvalidated_candidates(
    tmp_path: Path,
) -> None:
    payload = bytes((0x10, FEND, FESC, 0x20))
    timestamp = 123_456_789
    kiss = _kiss_record(0x09, timestamp.to_bytes(8, "big")) + _kiss_record(
        0x00, payload
    )
    script = tmp_path / "fake_gr_satellites.yml"
    script.write_text(
        "import os, pathlib, sys\n"
        "assert os.environ['GR_SATELLITES_SUBMIT_TLM'] == '0'\n"
        "out = pathlib.Path(sys.argv[sys.argv.index('--kiss_out') + 1])\n"
        f"out.write_bytes(bytes.fromhex('{kiss.hex()}'))\n",
        encoding="utf-8",
    )
    profile = GrSatellitesProfile(
        selector=str(script),
        selector_kind="satyaml",
        transmitters=_profile().transmitters,
    )
    backend = _backend(
        executable=sys.executable,
        profile=profile,
    )

    result = backend.run(_capture(tmp_path))

    assert result.succeeded
    assert len(result.candidates) == 1
    candidate = result.candidates[0]
    assert candidate.payload == payload
    assert candidate.provenance.parser["kiss_timestamp_ms"] == timestamp
    assert (
        candidate.provenance.parser["candidate_validation"]
        == "pending_downstream_validator"
    )
    assert candidate.provenance.parser["transmitter_attribution"] is None
    transmitters = candidate.provenance.parser["profile_transmitters"]
    assert transmitters[0]["transmitter_id"] == "UHF telemetry"
    assert result.argv[0] == sys.executable
    assert "--kiss_out" in result.argv


def test_adapter_returns_success_with_no_candidates_for_empty_kiss(
    tmp_path: Path,
) -> None:
    script = tmp_path / "empty_gr_satellites.yml"
    script.write_text(
        "import pathlib, sys\n"
        "out = pathlib.Path(sys.argv[sys.argv.index('--kiss_out') + 1])\n"
        "out.write_bytes(b'')\n",
        encoding="utf-8",
    )
    backend = _backend(
        executable=sys.executable,
        profile=GrSatellitesProfile(
            selector=str(script),
            selector_kind="satyaml",
            transmitters=_profile().transmitters,
        ),
    )

    result = backend.run(_capture(tmp_path))

    assert result.succeeded
    assert result.candidates == ()
