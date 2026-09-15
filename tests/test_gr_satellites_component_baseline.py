from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import numpy as np
import pytest
from jsonschema import Draft202012Validator

from telemetry_yield.camras_replay import bytes_to_bits
from telemetry_yield.clock_recovery import HDLC_FLAG, g3ruh_scramble_bits
from telemetry_yield.crc import append_ax25_fcs


ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "work/gr_satellites_component_baseline.py"
GOLDEN_ROOT = ROOT / "work/golden/env"
REFERENCE = bytes.fromhex(
    "94a662b2a0826094a662b29eb2e103f00018ad8001020304"
)


def _runner_module():
    name = "telemetry_yield_gr_satellites_component_baseline_test_module"
    spec = importlib.util.spec_from_file_location(name, RUNNER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _stuff(bits: tuple[int, ...]) -> tuple[int, ...]:
    output: list[int] = []
    ones = 0
    for bit in bits:
        output.append(bit)
        if bit:
            ones += 1
            if ones == 5:
                output.append(0)
                ones = 0
        else:
            ones = 0
    return tuple(output)


def _nrzi_encode(bits: tuple[int, ...], initial: int = 0) -> tuple[int, ...]:
    level = initial
    output: list[int] = []
    for bit in bits:
        if bit == 0:
            level ^= 1
        output.append(level)
    return tuple(output)


def _write_component_golden(path: Path, *, g3ruh: bool) -> None:
    frame = append_ax25_fcs(REFERENCE)
    body = _stuff(bytes_to_bits(frame, lsb_first=True))
    plain = HDLC_FLAG * 80 + (body + HDLC_FLAG * 8) * 12 + HDLC_FLAG * 80
    channel_bits = g3ruh_scramble_bits(plain) if g3ruh else plain
    levels = np.asarray(_nrzi_encode(channel_bits), dtype=np.float64)
    samples_per_symbol = 6
    frequency = np.repeat(5_000.0 * (2.0 * levels - 1.0), samples_per_symbol)
    phase = 2.0 * np.pi * np.cumsum(frequency) / 57_600.0
    iq = 0.75 * np.exp(1j * phase)
    raw = np.column_stack((iq.real, iq.imag))
    ci16 = np.rint(raw * 32_767.0).astype("<i2")
    path.write_bytes(ci16.tobytes())


def _run(
    source: Path,
    output: Path,
    *,
    expected_sha256: str | None = None,
    baudrate: int | None = None,
):
    digest = expected_sha256 or hashlib.sha256(source.read_bytes()).hexdigest()
    argv = [
            sys.executable,
            "-B",
            str(RUNNER),
            "--input",
            str(source),
            "--output",
            str(output),
            "--sample-rate-hz",
            "57600",
            "--expected-size-bytes",
            str(source.stat().st_size),
            "--expected-sha256",
            digest,
            "--golden-root",
            str(GOLDEN_ROOT),
            "--timeout-seconds",
            "120",
            "--cpu-seconds",
            "120",
            "--maximum-rss-mib",
            "2048",
        ]
    if baudrate is not None:
        argv.extend(("--baudrate", str(baudrate)))
    return subprocess.run(
        argv,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=150,
        check=False,
    )


@pytest.mark.skipif(
    not (GOLDEN_ROOT / "bin/python").is_file(),
    reason="frozen gr-satellites golden environment is unavailable",
)
@pytest.mark.parametrize("baudrate", [1_200, 4_800, 9_600, 19_200])
def test_component_baseline_single_rate_job_contract(
    tmp_path: Path, baudrate: int
) -> None:
    source = tmp_path / f"single-{baudrate}.ci16"
    output = tmp_path / f"single-{baudrate}.json"
    _write_component_golden(source, g3ruh=False)
    completed = _run(source, output, baudrate=baudrate)
    assert completed.returncode == 0, completed.stderr
    result = json.loads(output.read_text(encoding="utf-8"))
    assert result["baseline"]["id"] == (
        "gr-satellites-5.9.0-official-fsk-ax25-components"
    )
    assert result["effective_config"]["baudrates"] == [baudrate]
    assert result["hypothesis_bank"]["baudrates"] == [baudrate]
    assert {
        origin["baudrate"]
        for candidate in result["unique_candidates"]
        for origin in candidate["origins"]
    } <= {baudrate}


@pytest.mark.skipif(
    not (GOLDEN_ROOT / "bin/python").is_file(),
    reason="frozen gr-satellites golden environment is unavailable",
)
@pytest.mark.parametrize("g3ruh", [False, True])
def test_official_component_baseline_recovers_exact_fcs_free_ax25_pdu(
    tmp_path: Path, g3ruh: bool
) -> None:
    source = tmp_path / f"golden-{int(g3ruh)}.ci16"
    output = tmp_path / "result.json"
    _write_component_golden(source, g3ruh=g3ruh)
    completed = _run(source, output)
    assert completed.returncode == 0, completed.stderr
    result = json.loads(output.read_text(encoding="utf-8"))
    schema = json.loads(
        (ROOT / "schemas/gr-satellites-component-baseline-v1.schema.json").read_text(
            encoding="utf-8"
        )
    )
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(result)
    assert result["schema_version"] == "gr-satellites-component-baseline-v1"
    assert result["status"] == "completed"
    assert result["baseline"]["gr_satellites_version"] == "5.9.0"
    assert result["baseline"]["classification"] == "official_component_baseline"
    assert result["baseline"]["historical_satnogs_platform_equivalent"] is False
    assert result["baseline"]["mission_satyaml_flowgraph"] is False
    assert result["hypothesis_bank"]["baudrates"] == [1200, 4800, 9600, 19200]
    assert result["hypothesis_bank"]["g3ruh_modes"] == [False, True]
    assert result["source"]["sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert len(result["attempt_fingerprint"]) == 64
    assert len(result["config_sha256"]) == 64
    assert 0 < result["execution"]["peak_resident_bytes_observed"]
    assert (
        result["execution"]["peak_resident_bytes_observed"]
        <= result["execution"]["limits"]["maximum_rss_bytes"]
    )
    assert result["execution"]["limits"]["maximum_address_space_bytes"] == 8 * 1024**3

    candidates = {
        bytes.fromhex(candidate["payload_hex"]): candidate
        for candidate in result["unique_candidates"]
    }
    assert REFERENCE in candidates
    candidate = candidates[REFERENCE]
    assert candidate["payload_sha256"] == hashlib.sha256(REFERENCE).hexdigest()
    assert candidate["strict_ax25_ui"] is True
    assert {tuple(origin.values()) for origin in candidate["origins"]} >= {
        (9_600, g3ruh)
    }
    assert result["counts"]["trusted_unique_ax25_ui_count"] >= 1
    assert result["claim_guard"] == {
        "same_iq_component_baseline": True,
        "identical_to_historical_satnogs_deployment": False,
        "identical_to_mission_profile_gr_satellites": False,
        "publication_superiority_established": False,
    }


def test_component_baseline_rejects_wrong_content_hash_without_output(
    tmp_path: Path,
) -> None:
    source = tmp_path / "capture.ci16"
    output = tmp_path / "result.json"
    source.write_bytes(b"\x01\x00\x02\x00")
    completed = _run(source, output, expected_sha256="0" * 64)
    assert completed.returncode != 0
    assert "SHA-256" in completed.stderr
    assert not output.exists()


def test_component_baseline_atomic_result_is_no_clobber_under_race(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner = _runner_module()
    output = tmp_path / "result.json"
    output.write_bytes(b"existing")
    with pytest.raises(ValueError, match="created concurrently"):
        runner._atomic_json(output, {"status": "completed"})
    assert output.read_bytes() == b"existing"

    output.unlink()
    original_publish = runner._publish_no_clobber

    def racing_publish(temporary_path: Path, output_path: Path) -> None:
        output_path.write_bytes(b"concurrent")
        original_publish(temporary_path, output_path)

    monkeypatch.setattr(runner, "_publish_no_clobber", racing_publish)
    with pytest.raises(ValueError, match="concurrently"):
        runner._atomic_json(output, {"status": "completed"})
    assert output.read_bytes() == b"concurrent"
    assert not list(tmp_path.glob(".result.json.*.tmp"))


def test_component_baseline_rejects_inconsistent_serialized_output_bounds() -> None:
    runner = _runner_module()
    with pytest.raises(ValueError, match="child output bound"):
        runner.Bounds(
            maximum_frames=20_000,
            maximum_pdu_bytes=8 * 1024 * 1024,
            maximum_child_output_bytes=1_048_576,
        ).validate()
