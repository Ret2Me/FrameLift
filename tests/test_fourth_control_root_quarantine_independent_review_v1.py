from __future__ import annotations

import ast
import copy
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest


SOURCE = (
    Path(__file__).parents[1]
    / "work/blind-phase-confirmatory-v2/"
    "build_fourth_control_root_quarantine_independent_review_v1.py"
)
SPEC = importlib.util.spec_from_file_location(
    "build_fourth_control_root_quarantine_independent_review_v1", SOURCE
)
assert SPEC is not None and SPEC.loader is not None
R = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(R)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _build() -> tuple[dict[str, object], dict[str, object]]:
    return R.build_review(_sha(R.GENERATOR), _sha(R.GENERATOR_TEST))


def test_frozen_quarantine_runner_and_test_identities_are_exact() -> None:
    assert _sha(R.RUNNER_SOURCE) == R.EXPECTED_RUNNER_SHA256
    assert R.RUNNER_SOURCE.stat().st_size == R.EXPECTED_RUNNER_SIZE
    assert _sha(R.RUNNER_TEST_SOURCE) == R.EXPECTED_TEST_SHA256
    assert R.RUNNER_TEST_SOURCE.stat().st_size == R.EXPECTED_TEST_SIZE


def test_review_is_self_hashed_clean_go_and_read_only() -> None:
    before = R.OUTPUT.read_bytes() if R.OUTPUT.exists() else None
    document, audit = _build()
    after = R.OUTPUT.read_bytes() if R.OUTPUT.exists() else None
    unhashed = copy.deepcopy(document)
    stored = unhashed.pop(R.HASH_FIELD)
    assert stored == R._sha256_document(unhashed)
    assert document["schema_version"] == R.SCHEMA
    assert document["status"] == "GO"
    assert document["findings"] == {"P0": 0, "P1": 0, "P2": 0}
    assert audit["status"] == "DRY_RUN_PASS_NO_WRITE"
    assert audit["published"] is False
    assert before == after


def test_review_binds_intended_live_paths_and_exact_stage2_result() -> None:
    document, _audit = _build()
    bindings = document["reviewed_bindings"]
    assert bindings["quarantine_runner"] == {
        "path": str(R.RUNNER_CONTROL), "size_bytes": R.EXPECTED_RUNNER_SIZE,
        "sha256": R.EXPECTED_RUNNER_SHA256,
    }
    assert bindings["quarantine_runner_test"] == {
        "path": str(R.RUNNER_TEST_CONTROL), "size_bytes": R.EXPECTED_TEST_SIZE,
        "sha256": R.EXPECTED_TEST_SHA256,
    }
    assert bindings["stage2_recovery_result"] == {
        "path": str(R.STAGE2_RESULT), "size_bytes": R.EXPECTED_STAGE2_RESULT_SIZE,
        "sha256": R.EXPECTED_STAGE2_RESULT_SHA256,
    }


def test_contract_rejects_loss_of_zero_runtime_resume_binding() -> None:
    runner = R.RUNNER_SOURCE.read_bytes().replace(
        b'if zero_before_action != intent.get("zero_runtime_state"):',
        b'if False:', 1,
    )
    with pytest.raises(R.ReviewError, match="contract differs"):
        R._validate_contract(runner, R.RUNNER_TEST_SOURCE.read_bytes())


def test_contract_rejects_delete_primitive_or_reenabled_block() -> None:
    runner = R.RUNNER_SOURCE.read_bytes().replace(
        b"QUARANTINE_TEMPLATE_BLOCKED_PENDING_REVIEW = False",
        b"QUARANTINE_TEMPLATE_BLOCKED_PENDING_REVIEW = True", 1,
    ) + b"\nos.unlink('x')\n"
    with pytest.raises(R.ReviewError, match="contract differs"):
        R._validate_contract(runner, R.RUNNER_TEST_SOURCE.read_bytes())


def test_conditional_candidates_are_exact_and_not_final_provenance() -> None:
    document, _audit = _build()
    conditional = document["conditional_downstream_candidates"]
    assert conditional["status"] == (
        "CONDITIONAL_NO_PUBLICATION_BEFORE_REAL_QUARANTINE_PROVENANCE"
    )
    assert conditional["bindings"] == R.CONDITIONAL_DOWNSTREAM
    assert conditional["tests_passed"] == 35
    assert conditional["tests_skipped_root_only_pending_real_provenance"] == 2


def test_install_contract_requires_fresh_read_only_preflight() -> None:
    document, _audit = _build()
    contract = document["install_and_preflight_contract"]
    assert contract["first_run_preflight_only"] is True
    assert contract["required_fresh_preflight_status"] == (
        "PASS_READ_ONLY_NO_PUBLICATION_NO_RENAME"
    )
    assert contract["required_fresh_preflight_prefix"] == 0
    assert contract["mutating_command_is_same_exact_argv_without_preflight_only"] is True
    assert contract["initial_exact_names"] == sorted({
        R.RUNNER_CONTROL.name, R.RUNNER_TEST_CONTROL.name,
        R.REVIEW_CONTROL.name, R.REVIEW_CONTROL.name + ".sha256",
        R.LOCK_CONTROL.name,
    })
    assert len(R.ATTEMPT_ID) == 64
    assert R.CONTROLLER.parent == Path("/var/lib")
    assert R.QUARANTINE_ROOT.parent == Path("/var/lib")
    assert not R.CONTROLLER.is_relative_to(R.SOURCE_CONTROL_ROOT)
    assert not R.CONTROLLER.is_relative_to(R.QUARANTINE_ROOT)


def test_prior_blocked_candidate_review_is_explicitly_superseded() -> None:
    document, _audit = _build()
    previous = document["superseded_for_execution"]
    assert previous["authorization_status"] == "HISTORICAL_NO_GO_FOR_EXECUTION"
    assert previous["blocked_candidate_runner_sha256"] == (
        "1805f52e3baae59ddfbabfe94edaccfca18fc5b4e7404e2130f0025a36a53405"
    )
    assert previous["blocked_candidate_review_file_sha256"] == (
        "63c01750c1d657c6d2af13659167b3efbcd885642ca1784cc4a74b14224b4ab0"
    )


def test_generator_has_no_write_or_lifecycle_primitives() -> None:
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    attributes = {
        node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
    }
    assert not {
        "write_text", "write_bytes", "unlink", "rename", "replace", "remove",
        "rmtree", "kill", "mount",
    } & attributes


def test_repo_artifact_equals_deterministic_document_when_present() -> None:
    if not R.OUTPUT.exists():
        pytest.skip("canonical quarantine review has not been materialized yet")
    document, _audit = _build()
    validated = R.validate_repo_artifact(document)
    assert validated["review"]["path"] == str(R.OUTPUT)
    assert json.loads(R.OUTPUT.read_bytes()) == document
