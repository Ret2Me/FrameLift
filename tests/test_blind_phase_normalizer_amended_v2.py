from __future__ import annotations

import copy
import importlib.util
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "work/blind-phase-confirmatory-v2/normalize_result_amended_v2.py"


def _module():
    name = "blind_phase_normalizer_amended_v2_test_module"
    spec = importlib.util.spec_from_file_location(name, SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


NORMALIZER = _module()


def _trusted_fixture_module():
    path = ROOT / "tests/test_blind_phase_trusted_normalizer.py"
    name = "blind_phase_trusted_normalizer_fixture_for_v2"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _receipt(*, receipt_hash: str = "1" * 64) -> dict[str, object]:
    return {
        "receipt_payload_sha256": receipt_hash,
        "return_code": 0,
        "inherited_pipe_failure_detected": False,
        "process_tree_cleanup_passed": True,
        "stdout": {"size_bytes": 4, "sha256": "2" * 64, "tail_utf8": "noise"},
        "stderr": {"size_bytes": 0, "sha256": "3" * 64, "tail_utf8": ""},
    }


def _base_document(*, raw_hash: str = "4" * 64, count: int = 1) -> dict[str, object]:
    document: dict[str, object] = {
        "schema_version": "blind-phase-confirmatory-normalized-result-v1",
        "status": "completed",
        "unit": {"unit_id": "unit"},
        "process_tree_cleanup_passed": True,
        "trusted_native": [
            {"protocol_id": "ax25", "payload_hex": "0102", "admission": "trusted_native"}
        ],
        "untrusted_diagnostic": [],
        "receiver_projection": {
            "terminal_status": "completed",
            "process_receipt_sha256": "1" * 64,
            "receiver": {
                "effective_config": {"baudrate": 1200},
                "runtime": {"sha256": "5" * 64},
                "counts": {"detections": count},
                "raw_result_payload_sha256": raw_hash,
            },
        },
        "determinism_projection_sha256": "6" * 64,
        "provenance": {
            "process_receipt_payload_sha256": "1" * 64,
            "raw_result": {"path": "/raw", "size_bytes": 1, "sha256": "7" * 64},
        },
    }
    document["normalized_result_payload_sha256"] = NORMALIZER.sha256_document(document)
    return document


def _normalize(
    monkeypatch: pytest.MonkeyPatch,
    *,
    receipt: dict[str, object],
    raw_hash: str,
    count: int = 1,
) -> dict[str, object]:
    base = _base_document(raw_hash=raw_hash, count=count)
    monkeypatch.setattr(
        NORMALIZER, "_BASE_NORMALIZE_RESULT", lambda **_kwargs: copy.deepcopy(base)
    )
    return NORMALIZER.normalize_result(
        receipt=receipt,
        raw_result={"result_payload_sha256": raw_hash},
    )


def _contains_forbidden_hash_key(value: object) -> bool:
    if isinstance(value, dict):
        return any(
            key in NORMALIZER.RUN_BOOKKEEPING_KEYS
            or _contains_forbidden_hash_key(child)
            for key, child in value.items()
        )
    if isinstance(value, list):
        return any(_contains_forbidden_hash_key(child) for child in value)
    return False


def test_different_receipt_and_raw_hashes_keep_same_semantic_repeat_projection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = _normalize(monkeypatch, receipt=_receipt(receipt_hash="1" * 64), raw_hash="4" * 64)
    second = _normalize(monkeypatch, receipt=_receipt(receipt_hash="8" * 64), raw_hash="9" * 64)
    assert first["determinism_projection_sha256"] == second["determinism_projection_sha256"]
    assert first["receiver_projection"] == second["receiver_projection"]
    assert first["normalized_result_payload_sha256"] != second["normalized_result_payload_sha256"]
    assert first["provenance"]["process_receipt_sha256"] == "1" * 64
    assert second["provenance"]["process_receipt_sha256"] == "8" * 64
    assert first["provenance"]["raw_result_payload_sha256"] == "4" * 64
    assert second["provenance"]["raw_result_payload_sha256"] == "9" * 64
    assert not _contains_forbidden_hash_key(first["receiver_projection"])


def test_true_receiver_count_difference_changes_determinism_projection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = _normalize(monkeypatch, receipt=_receipt(), raw_hash="4" * 64, count=1)
    second = _normalize(monkeypatch, receipt=_receipt(), raw_hash="4" * 64, count=2)
    assert first["determinism_projection_sha256"] != second["determinism_projection_sha256"]


@pytest.mark.parametrize("change", ["return_code", "stderr"])
def test_process_outcome_difference_is_not_flattened(
    monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    first_receipt = _receipt()
    second_receipt = copy.deepcopy(first_receipt)
    if change == "return_code":
        second_receipt["return_code"] = 2
    else:
        second_receipt["stderr"] = {"size_bytes": 5, "sha256": "a" * 64}
    first = _normalize(monkeypatch, receipt=first_receipt, raw_hash="4" * 64)
    second = _normalize(monkeypatch, receipt=second_receipt, raw_hash="4" * 64)
    assert first["determinism_projection_sha256"] != second["determinism_projection_sha256"]


@pytest.mark.parametrize(
    "receipt,error",
    [
        ({}, "process outcome"),
        (
            {
                **_receipt(),
                "stdout": {"size_bytes": True, "sha256": "2" * 64},
            },
            "stdout outcome",
        ),
    ],
)
def test_malformed_semantic_process_outcome_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    receipt: dict[str, object],
    error: str,
) -> None:
    with pytest.raises(ValueError, match=error):
        _normalize(monkeypatch, receipt=receipt, raw_hash="4" * 64)


def test_only_authorized_bookkeeping_keys_are_removed() -> None:
    value = {
        "process_receipt_sha256": "drop",
        "stable": {"raw_result_payload_sha256": "drop", "runtime_sha256": "keep"},
        "counts": {"detections": 1},
    }
    assert NORMALIZER._without_run_bookkeeping(value) == {
        "stable": {"runtime_sha256": "keep"},
        "counts": {"detections": 1},
    }


def test_actual_v1_receiver_validation_pipeline_feeds_v2_projection(
    tmp_path: Path,
) -> None:
    fixtures = _trusted_fixture_module()
    fixture = fixtures._candidate_fixture(tmp_path)
    receipt = fixture["receipt"]
    receipt.update(
        {
            "return_code": 0,
            "inherited_pipe_failure_detected": False,
            "stdout": {"size_bytes": 0, "sha256": "0" * 64},
            "stderr": {"size_bytes": 0, "sha256": "0" * 64},
        }
    )
    receipt.pop("receipt_payload_sha256")
    receipt["receipt_payload_sha256"] = NORMALIZER.sha256_document(receipt)
    normalized = NORMALIZER.normalize_result(
        unit=fixture["unit"],
        receipt=receipt,
        raw_result=fixture["raw"],
        raw_identity=fixture["raw_identity"],
        execution_plan_sha256="1" * 64,
        acquisition_manifest_sha256="2" * 64,
        candidate_config=fixture["config"],
        candidate_config_identity=fixture["config_identity"],
        candidate_runtime_lock=fixture["runtime_lock"],
        component_lock={},
        mission_route=None,
        mission_runtime_lock={},
        normalizer_identity={"path": str(SCRIPT), "size_bytes": 1, "sha256": "3" * 64},
        expected_process_attempt_fingerprint=fixture["attempt"],
    )
    assert normalized["status"] == "completed"
    assert normalized["receiver_projection"]["process_outcome"] == {
        "return_code": 0,
        "inherited_pipe_failure_detected": False,
        "process_tree_cleanup_passed": True,
        "stdout": {"size_bytes": 0, "sha256": "0" * 64},
        "stderr": {"size_bytes": 0, "sha256": "0" * 64},
    }
    assert not _contains_forbidden_hash_key(normalized["receiver_projection"])


def test_normalizer_v2_is_standalone_and_has_no_path_exec_loader() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    assert "normalize_result_amended.py" not in source
    assert "spec_from_file_location" not in source
    assert "exec_module" not in source
