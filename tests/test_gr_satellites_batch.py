from __future__ import annotations

import hashlib
import json
from pathlib import Path

from telemetry_yield.gr_satellites_batch import (
    GrSatellitesBatchConfig,
    GrSatellitesBatchJob,
    run_gr_satellites_batch,
)
from telemetry_yield.satyaml_registry import build_satyaml_registry


FIXTURES = Path(__file__).parent / "fixtures" / "satyaml_registry"


def _fake_executable(path: Path, kiss_hex: str, counter: Path) -> Path:
    script = path / "fake-gr-satellites"
    script.write_text(
        "#!/usr/bin/env python3\n"
        "import os, pathlib, sys\n"
        "assert os.environ['GR_SATELLITES_SUBMIT_TLM'] == '0'\n"
        f"counter = pathlib.Path({str(counter)!r})\n"
        "count = int(counter.read_text()) if counter.exists() else 0\n"
        "counter.write_text(str(count + 1))\n"
        "output = pathlib.Path(sys.argv[sys.argv.index('--kiss_out') + 1])\n"
        f"output.write_bytes(bytes.fromhex({kiss_hex!r}))\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    return script


def _job(tmp_path: Path, observation_id: int = 1) -> GrSatellitesBatchJob:
    iq = tmp_path / f"observation_{observation_id}.iq"
    iq.write_bytes(b"\x00" * 32)
    profile = build_satyaml_registry(FIXTURES / "ax25.yml").get_by_norad(90001)
    assert profile is not None
    return GrSatellitesBatchJob(
        observation_id=observation_id,
        satellite_id="SAT-UUID",
        frequency_hz=437_250_000,
        iq_url="https://example.invalid/test.iq",
        iq_path=iq,
        expected_size_bytes=iq.stat().st_size,
        expected_sha256=hashlib.sha256(iq.read_bytes()).hexdigest(),
        profile=profile,
        route_document={"status": "routed", "observation_id": observation_id},
    )


def _config(tmp_path: Path, executable: Path) -> GrSatellitesBatchConfig:
    return GrSatellitesBatchConfig(
        executable=str(executable),
        executable_version="test",
        artifact_root=tmp_path / "artifacts",
        sample_format="ci16_le",
        sample_rate_hz=57_600,
        timeout_seconds=2,
        max_output_bytes=4096,
        max_kiss_bytes=4096,
    )


def test_batch_writes_unvalidated_candidate_and_artifacts_atomically(
    tmp_path: Path,
) -> None:
    # KISS data record containing one escaped-free three-byte PDU.
    kiss = "c000010203c0"
    counter = tmp_path / "counter"
    executable = _fake_executable(tmp_path, kiss, counter)

    manifest = run_gr_satellites_batch(
        (_job(tmp_path),), _config(tmp_path, executable)
    )

    assert manifest["status_counts"] == {"completed": 1}
    assert manifest["unvalidated_candidate_count"] == 1
    artifact = Path(manifest["jobs"][0]["artifact_directory"])
    result = json.loads((artifact / "result.json").read_text())
    assert result["candidate_count"] == 1
    assert result["validated_candidate_count"] == 0
    assert result["candidate_classification"] == "unvalidated_pdu_candidate"
    candidate = result["candidates"][0]
    assert candidate["validated"] is False
    assert candidate["validation_layers"] == []
    assert Path(candidate["payload_path"]).read_bytes() == b"\x01\x02\x03"
    assert (artifact / "stdout.bin").is_file()
    assert (artifact / "stderr.bin").is_file()
    assert not list((tmp_path / "artifacts").rglob("*.tmp"))


def test_completed_attempt_is_resumed_without_rerunning_process(tmp_path: Path) -> None:
    counter = tmp_path / "counter"
    executable = _fake_executable(tmp_path, "c000aac0", counter)
    job = _job(tmp_path)
    config = _config(tmp_path, executable)

    first = run_gr_satellites_batch((job,), config)
    second = run_gr_satellites_batch((job,), config)

    assert first["campaign_fingerprint"] == second["campaign_fingerprint"]
    assert counter.read_text() == "1"
    assert second["jobs"][0]["resumed"] is True
    assert second["status_counts"] == {"completed": 1}


def test_missing_input_does_not_prevent_another_job_from_finishing(
    tmp_path: Path,
) -> None:
    counter = tmp_path / "counter"
    executable = _fake_executable(tmp_path, "c00055c0", counter)
    missing = _job(tmp_path, observation_id=1)
    missing.iq_path.unlink()
    available = _job(tmp_path, observation_id=2)

    manifest = run_gr_satellites_batch(
        (missing, available), _config(tmp_path, executable)
    )

    assert manifest["status_counts"] == {"completed": 1, "input_error": 1}
    assert counter.read_text() == "1"
    assert [item["observation_id"] for item in manifest["jobs"]] == [1, 2]


def test_max_jobs_checkpoints_remaining_work_as_pending(tmp_path: Path) -> None:
    counter = tmp_path / "counter"
    executable = _fake_executable(tmp_path, "c00055c0", counter)

    manifest = run_gr_satellites_batch(
        (_job(tmp_path, 1), _job(tmp_path, 2)),
        _config(tmp_path, executable),
        max_jobs=1,
    )

    assert manifest["status_counts"] == {"completed": 1, "pending": 1}
    assert counter.read_text() == "1"
