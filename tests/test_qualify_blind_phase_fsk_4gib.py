from __future__ import annotations

from dataclasses import asdict
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

import pytest

from telemetry_yield.clipping_robust_fsk import BlindPhaseFskConfig


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "work/qualify_blind_phase_fsk_4gib.py"


def _module():
    name = "telemetry_yield_blind_4gib_qualifier_test_module"
    spec = importlib.util.spec_from_file_location(name, SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_sparse_qualification_input_is_deterministic_and_no_clobber(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    qualifier = _module()
    monkeypatch.setattr(qualifier, "LOGICAL_SIZE_BYTES", 1_600)
    monkeypatch.setattr(qualifier, "SAMPLE_RATE_HZ", 100)
    monkeypatch.setattr(qualifier, "ANALYSIS_WINDOW_SECONDS", 1.0)
    first = tmp_path / "first.ci16"
    second = tmp_path / "second.ci16"
    first_record = qualifier._make_sparse_ci16(first)
    second_record = qualifier._make_sparse_ci16(second)
    assert first_record["analysis_windows"] == 4
    assert first_record["signal_analysis_indices"] == [0, 1, 2, 3]
    assert first.read_bytes() == second.read_bytes()
    assert qualifier._sha256_file(first) == qualifier._sha256_file(second)
    with pytest.raises(FileExistsError):
        qualifier._make_sparse_ci16(first)


def test_qualification_report_is_atomic_no_clobber(tmp_path: Path) -> None:
    qualifier = _module()
    output = tmp_path / "qualification.json"
    report = {"schema_version": "test", "status": "PASS"}
    qualifier._atomic_report(output, report)
    assert json.loads(output.read_text(encoding="utf-8")) == report
    with pytest.raises(ValueError, match="already exists"):
        qualifier._atomic_report(output, {"status": "FAIL"})
    assert json.loads(output.read_text(encoding="utf-8")) == report
    assert not list(tmp_path.glob(".qualification.json.*.tmp"))


def test_qualification_binds_invoking_python_runtime() -> None:
    qualifier = _module()
    identity = qualifier._runtime_python_identity()
    assert Path(identity["launcher_path"]).is_absolute()
    assert Path(identity["target_path"]).is_file()
    assert len(identity["target_sha256"]) == 64


def _candidate_document() -> dict[str, object]:
    return {
        "schema_version": "blind-phase-fsk-confirmatory-candidate-config-v1",
        "sample_rate_hz": 57_600,
        "rate_configs": [
            {
                "baudrate": baudrate,
                "receiver_config": asdict(BlindPhaseFskConfig(baudrate=baudrate)),
            }
            for baudrate in (1_200, 4_800, 9_600, 19_200)
        ],
        "protocol_adapter": {
            "id": "hdlc-crc16-x25-ax25-ui-plain-and-g3ruh",
            "catalog_mode_supported_exact": ["FSK", "FSK AX25 G3RUH", "GFSK", "GMSK"],
            "compatible_profile_framing_exact": ["AX.25", "AX.25 G3RUH"],
            "compatible_profile_modulation_exact": ["FSK"],
            "repaired_output_default": "untrusted",
        },
    }


def _write_candidate(path: Path, document: dict[str, object]) -> tuple[int, str]:
    payload = (json.dumps(document, sort_keys=True) + "\n").encode("utf-8")
    path.write_bytes(payload)
    return len(payload), hashlib.sha256(payload).hexdigest()


def test_candidate_config_is_exactly_content_bound_and_four_rate(tmp_path: Path) -> None:
    qualifier = _module()
    candidate = tmp_path / "candidate.json"
    size, digest = _write_candidate(candidate, _candidate_document())
    receivers, loaded, status = qualifier._load_candidate_config(
        candidate,
        expected_size_bytes=size,
        expected_sha256=digest,
    )
    assert tuple(receiver.baudrate for receiver in receivers) == (
        1_200,
        4_800,
        9_600,
        19_200,
    )
    assert loaded == json.loads(json.dumps(_candidate_document()))
    assert status.st_size == size
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        qualifier._load_candidate_config(
            candidate,
            expected_size_bytes=size,
            expected_sha256="0" * 64,
        )


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (
            lambda candidate: candidate["rate_configs"][1]["receiver_config"].update(
                {"candidate_window_limit": 31}
            ),
            "differ between rates",
        ),
        (
            lambda candidate: candidate["protocol_adapter"].update(
                {"compatible_profile_framing_exact": ["AX.25"]}
            ),
            "framing set",
        ),
        (
            lambda candidate: candidate.update({"frames_recovered": 1}),
            "outcome field",
        ),
    ],
)
def test_candidate_config_rejects_nonfrozen_or_outcome_content(
    tmp_path: Path, mutation, message: str
) -> None:
    qualifier = _module()
    document = _candidate_document()
    mutation(document)
    candidate = tmp_path / "candidate.json"
    size, digest = _write_candidate(candidate, document)
    with pytest.raises(ValueError, match=message):
        qualifier._load_candidate_config(
            candidate,
            expected_size_bytes=size,
            expected_sha256=digest,
        )
