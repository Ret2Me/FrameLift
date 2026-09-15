from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile

import numpy as np
import pytest

from telemetry_yield.fsk_ax25_plugin import FskAx25Config
from telemetry_yield.cli import main
from telemetry_yield.fsk_file_orchestration import (
    API_VERSION,
    CHECKPOINT_SCHEMA_VERSION,
    SCHEMA_VERSION,
    FskAx25RunConfig,
    run_fsk_ax25_file,
)
from tests.test_fsk_ax25_plugin import REFERENCE, _synthetic_iq


def _write_ci16(path: Path, iq: np.ndarray) -> None:
    peak = float(np.max(np.abs(iq)))
    scaled = iq if peak == 0.0 else iq * (24_000.0 / peak)
    interleaved = np.empty(2 * len(iq), dtype="<i2")
    interleaved[0::2] = np.rint(scaled.real).astype("<i2")
    interleaved[1::2] = np.rint(scaled.imag).astype("<i2")
    path.write_bytes(interleaved.tobytes())


def _config(iq_samples: int, *, expected_sha256: str | None = None) -> FskAx25RunConfig:
    duration = iq_samples / 57_600
    return FskAx25RunConfig(
        plugin=FskAx25Config(
            sample_rate_hz=57_600,
            baudrate=9_600,
            decimation=3,
            g3ruh=True,
            window_seconds=duration,
            hop_seconds=duration,
            timing_rate_errors_ppm=(0.0,),
        ),
        event_key="synthetic-fsk",
        expected_input_size_bytes=iq_samples * 4,
        expected_input_sha256=expected_sha256,
    )


def test_versioned_fsk_file_api_recovers_and_serializes_strict_frame() -> None:
    iq = _synthetic_iq(g3ruh=True)
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        source = root / "capture.raw"
        output = root / "result.json"
        _write_ci16(source, iq)
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        result = run_fsk_ax25_file(
            source,
            output,
            config=_config(len(iq), expected_sha256=digest),
            api_version=API_VERSION,
        )
        assert result["api_version"] == API_VERSION
        assert result["schema_version"] == SCHEMA_VERSION
        assert result["status"] == "completed"
        assert REFERENCE.hex() in {
            row["normalized_payload_hex"] for row in result["candidates"]["trusted"]
        }
        assert result["candidate_ledger"]["candidate_count"] >= 1
        assert result["resource_counters"]["maximum_complex_samples_materialized_at_once"] == len(iq)
        assert result["resource_counters"]["maximum_iq_window_bytes"] == len(iq) * 16
        assert json.loads(output.read_text()) == result


def test_fsk_file_api_resumes_without_duplicate_payloads() -> None:
    single = _synthetic_iq(g3ruh=True)
    iq = np.concatenate((single, single))
    duration = len(single) / 57_600
    config = FskAx25RunConfig(
        plugin=FskAx25Config(
            sample_rate_hz=57_600,
            baudrate=9_600,
            decimation=3,
            g3ruh=True,
            window_seconds=duration,
            hop_seconds=duration,
            timing_rate_errors_ppm=(0.0,),
        ),
        event_key="resume-fsk",
    )
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        source = root / "capture.raw"
        output = root / "result.json"
        checkpoint = root / "checkpoint.json"
        _write_ci16(source, iq)
        partial = run_fsk_ax25_file(
            source,
            output,
            config=config,
            checkpoint_path=checkpoint,
            max_windows=1,
        )
        assert partial["status"] == "partial"
        resumed = run_fsk_ax25_file(
            source,
            output,
            config=config,
            checkpoint_path=checkpoint,
        )
        fresh = run_fsk_ax25_file(
            source,
            root / "fresh.json",
            config=config,
            checkpoint_path=root / "fresh-checkpoint.json",
        )
        assert resumed["status"] == "completed"
        assert resumed["candidate_ledger"] == fresh["candidate_ledger"]
        assert resumed["attempt_fingerprint"] == fresh["attempt_fingerprint"]


def test_fsk_file_api_fails_closed_on_version_hash_and_checkpoint_tamper() -> None:
    iq = _synthetic_iq(g3ruh=True)
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        source = root / "capture.raw"
        _write_ci16(source, iq)
        with pytest.raises(ValueError, match="API version"):
            run_fsk_ax25_file(
                source,
                root / "wrong-version.json",
                config=_config(len(iq)),
                api_version="telemetry-yield-fsk-ax25-file-api-v999",
            )
        with pytest.raises(ValueError, match="SHA-256"):
            run_fsk_ax25_file(
                source,
                root / "wrong-hash.json",
                config=_config(len(iq), expected_sha256="0" * 64),
            )
        checkpoint = root / "tampered-checkpoint.json"
        checkpoint.write_text(json.dumps({
            "schema_version": CHECKPOINT_SCHEMA_VERSION,
            "attempt_fingerprint": "0" * 64,
            "source_sha256": "0" * 64,
            "source_size_bytes": source.stat().st_size,
            "config_sha256": "0" * 64,
            "external_baseline_sha256": None,
            "windows": {},
        }))
        with pytest.raises(ValueError, match="does not match"):
            run_fsk_ax25_file(
                source,
                root / "tampered.json",
                config=_config(len(iq)),
                checkpoint_path=checkpoint,
            )


def test_fsk_file_api_zero_iq_returns_no_trusted_frames() -> None:
    iq = np.zeros(57_600, dtype=np.complex64)
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        source = root / "zero.raw"
        _write_ci16(source, iq)
        config = FskAx25RunConfig(
            plugin=FskAx25Config(
                sample_rate_hz=57_600,
                baudrate=9_600,
                decimation=3,
                g3ruh=True,
                window_seconds=1.0,
                hop_seconds=1.0,
                timing_rate_errors_ppm=(0.0,),
            )
        )
        result = run_fsk_ax25_file(source, root / "zero.json", config=config)
        assert result["candidates"]["trusted"] == []
        assert result["candidate_ledger"]["candidate_count"] == 0
        assert result["resource_counters"]["degenerate_windows"] == 1


def test_cli_routes_explicit_fsk_profile_into_versioned_result() -> None:
    iq = _synthetic_iq(g3ruh=True)
    duration = len(iq) / 57_600
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        source = root / "capture.raw"
        output = root / "result.json"
        _write_ci16(source, iq)
        status = main([
            "decode-fsk-ax25",
            str(source),
            "--output",
            str(output),
            "--sample-rate",
            "57600",
            "--baudrate",
            "9600",
            "--decimation",
            "3",
            "--g3ruh",
            "--window-seconds",
            str(duration),
            "--hop-seconds",
            str(duration),
            "--rate-error-ppm",
            "0",
        ])
        document = json.loads(output.read_text(encoding="utf-8"))
        assert status == 0
        assert document["api_version"] == API_VERSION
        assert document["status"] == "completed"
        assert document["route"]["protocol_adapter_id"] == "ax25_g3ruh"
        assert document["candidate_ledger"]["union_count"] >= 1
