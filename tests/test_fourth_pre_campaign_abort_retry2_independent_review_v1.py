from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest


SOURCE = (
    Path(__file__).parents[1]
    / "work/blind-phase-confirmatory-v2/"
    "build_fourth_pre_campaign_abort_retry2_independent_review_v1.py"
)
SPEC = importlib.util.spec_from_file_location(
    "build_fourth_pre_campaign_abort_retry2_independent_review_v1", SOURCE
)
assert SPEC is not None and SPEC.loader is not None
review = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(review)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _build() -> tuple[dict[str, object], dict[str, object]]:
    return review.build_review(_sha(review.GENERATOR), _sha(review.GENERATOR_TEST))


def test_frozen_retry2_runner_test_and_incident_identities_are_exact() -> None:
    assert _sha(review.RUNNER_SOURCE) == review.EXPECTED_RUNNER_SHA256
    assert review.RUNNER_SOURCE.stat().st_size == review.EXPECTED_RUNNER_SIZE
    assert _sha(review.RUNNER_TEST_SOURCE) == review.EXPECTED_RUNNER_TEST_SHA256
    assert review.RUNNER_TEST_SOURCE.stat().st_size == review.EXPECTED_RUNNER_TEST_SIZE
    assert _sha(review.INCIDENT_SOURCE) == review.EXPECTED_INCIDENT_SHA256
    assert review.INCIDENT_SOURCE.stat().st_size == review.EXPECTED_INCIDENT_SIZE
    assert _sha(review.INCIDENT_SOURCE_SIDECAR) == review.EXPECTED_INCIDENT_SIDECAR_SHA256


def test_build_is_read_only_and_binds_intended_retry2_control_paths() -> None:
    before = review.OUTPUT.read_bytes() if review.OUTPUT.exists() else None
    document, audit = _build()
    after = review.OUTPUT.read_bytes() if review.OUTPUT.exists() else None
    assert before == after
    assert audit["status"] == "DRY_RUN_PASS_NO_WRITE"
    assert audit["published"] is False
    bindings = document["reviewed_bindings"]
    assert bindings["abort_runner_retry2"]["path"] == str(review.RUNNER_CONTROL)
    assert bindings["abort_runner_retry2_test"]["path"] == str(review.RUNNER_TEST_CONTROL)
    assert bindings["retry1_preflight_failure_incident"]["path"] == str(review.INCIDENT_CONTROL)


def test_review_payload_self_hash_is_canonical() -> None:
    document, _ = _build()
    unhashed = copy.deepcopy(document)
    stored = unhashed.pop("review_payload_sha256")
    assert stored == review._sha256_document(unhashed)
    assert document["status"] == "GO"
    assert document["schema_version"] == review.SCHEMA


def test_review_has_zero_findings_and_no_lifecycle_execution() -> None:
    document, _ = _build()
    assert document["findings"] == {"p0_open": 0, "p1_open": 0, "p2_open": 0}
    assert document["scope"]["lifecycle_or_recovery_executed_by_independent_review"] is False
    assert document["execution_preconditions"]["read_only_preflight_must_pass_before_mutating_execution"] is True
    assert all(document["verification"].values())


def test_contract_rejects_reenabled_retry2_review_block() -> None:
    runner = review.RUNNER_SOURCE.read_bytes().replace(
        b"STAGE2_TEMPLATE_BLOCKED_PENDING_RETRY2_REVIEW = False",
        b"STAGE2_TEMPLATE_BLOCKED_PENDING_RETRY2_REVIEW = True ",
        1,
    )
    with pytest.raises(review.ReviewError, match="constants/functions/composition"):
        review._validate_reviewed_contract(runner, review.RUNNER_TEST_SOURCE.read_bytes())


def test_contract_rejects_loss_of_projected_full_composition_boundary() -> None:
    runner = review.RUNNER_SOURCE.read_bytes().replace(
        b"_control_evidence_projection(_validate_control_inventory())",
        b"_validate_control_inventory()",
        1,
    )
    with pytest.raises(review.ReviewError, match="constants/functions/composition"):
        review._validate_reviewed_contract(runner, review.RUNNER_TEST_SOURCE.read_bytes())


def test_contract_rejects_missing_real_projection_integration_regression() -> None:
    runner_test = review.RUNNER_TEST_SOURCE.read_bytes().replace(
        b"test_validate_chain_and_pre_signal_use_same_real_control_projection",
        b"removed_validate_chain_and_pre_signal_control_projection_test",
        1,
    )
    with pytest.raises(review.ReviewError, match="integration/adversarial"):
        review._validate_reviewed_contract(review.RUNNER_SOURCE.read_bytes(), runner_test)


def test_wrong_generator_digest_is_rejected() -> None:
    with pytest.raises(review.ReviewError, match="identity or metadata"):
        review.build_review("0" * 64, _sha(review.GENERATOR_TEST))


def test_retry1_history_incident_and_stage1_authorities_are_exact() -> None:
    history = review._load_retry1_history()
    incident_bindings, incident = review._load_preflight_incident()
    stage1 = review._load_stage1_authorities()
    assert history["retry1_historical_parent"]["direct_child_count"] == 22
    assert incident["failure_cause"]["mutation_reached"] is False
    assert incident_bindings["retry1_preflight_failure_incident"]["sha256"] == review.EXPECTED_INCIDENT_SHA256
    assert set(stage1) == {
        "stage1_independent_review",
        "stage1_rollback_recovery",
        "canonical_rollback_ready",
        "canonical_rollback_complete",
    }


def test_test_evidence_records_exact_independent_runs() -> None:
    document, _ = _build()
    evidence = document["test_evidence"]
    assert evidence["stage2_tests_passed"] == 73
    assert evidence["combined_tests_passed"] == 228
    assert evidence["combined_tests_deselected"] == 1
    assert evidence["python_compile_pass"] is True


def test_repo_artifact_equals_deterministic_document_when_present() -> None:
    if not review.OUTPUT.exists():
        pytest.skip("canonical retry2 repo review has not been materialized yet")
    document, _ = _build()
    identity = review.validate_repo_artifact(document)
    assert identity["path"] == str(review.OUTPUT)
    assert json.loads(review.OUTPUT.read_bytes()) == document
