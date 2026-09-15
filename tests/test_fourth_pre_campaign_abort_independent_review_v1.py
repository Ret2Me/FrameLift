from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest


SOURCE = (
    Path(__file__).parents[1]
    / "work/blind-phase-confirmatory-v2/build_fourth_pre_campaign_abort_independent_review_v1.py"
)
SPEC = importlib.util.spec_from_file_location(
    "build_fourth_pre_campaign_abort_independent_review_v1", SOURCE
)
assert SPEC is not None and SPEC.loader is not None
review = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(review)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _build() -> tuple[dict[str, object], dict[str, object]]:
    return review.build_review(_sha(review.GENERATOR), _sha(review.GENERATOR_TEST))


def test_frozen_runner_and_test_identities_are_exact() -> None:
    assert _sha(review.RUNNER_SOURCE) == review.EXPECTED_RUNNER_SHA256
    assert review.RUNNER_SOURCE.stat().st_size == review.EXPECTED_RUNNER_SIZE
    assert _sha(review.RUNNER_TEST_SOURCE) == review.EXPECTED_RUNNER_TEST_SHA256
    assert review.RUNNER_TEST_SOURCE.stat().st_size == review.EXPECTED_RUNNER_TEST_SIZE


def test_build_is_read_only_and_binds_intended_control_paths() -> None:
    before = review.OUTPUT.read_bytes() if review.OUTPUT.exists() else None
    document, audit = _build()
    after = review.OUTPUT.read_bytes() if review.OUTPUT.exists() else None
    assert before == after
    assert audit["status"] == "DRY_RUN_PASS_NO_WRITE"
    assert audit["published"] is False
    assert document["reviewed_bindings"]["abort_runner"]["path"] == str(review.RUNNER_CONTROL)
    assert document["reviewed_bindings"]["abort_runner_test"]["path"] == str(review.RUNNER_TEST_CONTROL)


def test_review_payload_self_hash_is_canonical() -> None:
    document, _ = _build()
    unhashed = copy.deepcopy(document)
    stored = unhashed.pop("review_payload_sha256")
    assert stored == review._sha256_document(unhashed)
    assert document["status"] == "GO"
    assert document["schema_version"] == review.SCHEMA


def test_review_has_zero_open_findings_and_no_lifecycle_execution() -> None:
    document, _ = _build()
    assert document["findings"] == {"p0_open": 0, "p1_open": 0, "p2_open": 0}
    assert document["scope"]["lifecycle_or_recovery_executed_by_independent_review"] is False
    assert all(document["verification"].values())


def test_contract_rejects_reenabled_template_block() -> None:
    runner = review.RUNNER_SOURCE.read_bytes().replace(
        b"STAGE2_TEMPLATE_BLOCKED_PENDING_STAGE1_RECEIPTS = False",
        b"STAGE2_TEMPLATE_BLOCKED_PENDING_STAGE1_RECEIPTS = True ",
        1,
    )
    with pytest.raises(review.ReviewError, match="constants/functions"):
        review._validate_reviewed_contract(runner, review.RUNNER_TEST_SOURCE.read_bytes())


def test_contract_rejects_sigkill_or_extra_destructive_primitive() -> None:
    runner = review.RUNNER_SOURCE.read_bytes() + b"\nsignal.pidfd_send_signal(1, signal.SIGKILL)\n"
    with pytest.raises(review.ReviewError):
        review._validate_reviewed_contract(runner, review.RUNNER_TEST_SOURCE.read_bytes())


def test_wrong_generator_digest_is_rejected() -> None:
    with pytest.raises(review.ReviewError, match="identity or metadata"):
        review.build_review("0" * 64, _sha(review.GENERATOR_TEST))


def test_stage1_authorities_and_sidecars_are_exact() -> None:
    bindings = review._load_stage1_authorities()
    assert set(bindings) == {
        "stage1_independent_review",
        "stage1_rollback_recovery",
        "canonical_rollback_ready",
        "canonical_rollback_complete",
    }
    assert all(item["sha256"] in {value[0] for value in review.EXPECTED_STAGE1.values()} for item in bindings.values())


def test_test_evidence_records_exact_independent_runs() -> None:
    document, _ = _build()
    evidence = document["test_evidence"]
    assert evidence["stage2_tests_passed"] == 64
    assert evidence["combined_tests_passed"] == 219
    assert evidence["combined_tests_deselected"] == 1
    assert evidence["python_compile_pass"] is True


def test_repo_artifact_equals_deterministic_document_when_present() -> None:
    if not review.OUTPUT.exists():
        pytest.skip("canonical repo review has not been materialized yet")
    document, _ = _build()
    identity = review.validate_repo_artifact(document)
    assert identity["path"] == str(review.OUTPUT)
    loaded = json.loads(review.OUTPUT.read_bytes())
    assert loaded == document
