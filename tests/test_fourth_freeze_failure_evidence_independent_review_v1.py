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
    "build_fourth_freeze_failure_evidence_independent_review_v1.py"
)
SPEC = importlib.util.spec_from_file_location(
    "build_fourth_freeze_failure_evidence_independent_review_v1", SOURCE,
)
assert SPEC is not None and SPEC.loader is not None
R = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(R)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _build() -> tuple[dict[str, object], dict[str, object]]:
    return R.build_review(_sha(R.GENERATOR), _sha(R.GENERATOR_TEST))


def test_exact_frozen_sources_and_clean_selfhashed_go() -> None:
    assert (_sha(R.EVIDENCE_SOURCE), R.EVIDENCE_SOURCE.stat().st_size) == R.EXPECTED_EVIDENCE_SOURCE
    assert (_sha(R.EVIDENCE_TEST_SOURCE), R.EVIDENCE_TEST_SOURCE.stat().st_size) == R.EXPECTED_EVIDENCE_TEST
    document, audit = _build()
    unhashed = copy.deepcopy(document)
    claimed = unhashed.pop(R.HASH_FIELD)
    assert claimed == R._document_sha(unhashed)
    assert document["schema_version"] == R.SCHEMA
    assert document["status"] == "GO"
    assert document["findings"] == {"P0": 0, "P1": 0, "P2": 0}
    assert audit["status"] == "DRY_RUN_PASS_NO_WRITE"
    assert audit["published"] is False


def test_review_binds_exact_incident_chain_and_candidate() -> None:
    document, _ = _build()
    bindings = document["reviewed_bindings"]
    assert bindings["exact_stage1_recovery_incident"] == R.EXACT_RECOVERY_INCIDENT
    for label, expected in R.CHAIN_BINDINGS.items():
        assert bindings[label] == expected
    dry_run = document["read_only_dry_run"]
    assert dry_run["candidate"] == R.EXPECTED_DRY_RUN_EVIDENCE
    assert dry_run["transcript_receipt"] == R.DRY_RUN_TRANSCRIPT
    assert dry_run["publication_executed"] is False


def test_review_binds_exact_publication_state_machine() -> None:
    document, _ = _build()
    contract = document["publication_contract"]
    assert contract["reports_parent"] == R.REPORTS_PARENT
    assert contract["published_metadata"] == {
        "uid": 0, "gid": 0, "mode": 0o444, "nlink": 1,
    }
    assert contract["presence_states"] == {
        "absent_absent": "publish_sidecar_then_main",
        "absent_exact_sidecar": "publish_main_then_validate_pair",
        "exact_main_exact_sidecar": "idempotent_validate_only_no_relink",
        "main_only_or_any_mismatch": "fail_closed",
    }
    assert contract["reports_parent_is_user_owned_staging_not_immutable_authority"] is True
    assert contract["retry3_must_validate_and_freeze_exact_candidate_before_new_canonical"] is True


def test_contract_rejects_lost_incident_or_fd_relative_binding() -> None:
    source = R.EVIDENCE_SOURCE.read_bytes().replace(
        R.EXACT_RECOVERY_INCIDENT["sha256"].encode(), b"0" * 64, 1,
    ).replace(
        b"os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW, dir_fd=directory_fd",
        b"os.O_RDONLY",
        1,
    )
    with pytest.raises(R.ReviewError, match="contract differs"):
        R._validate_contract(source, R.EVIDENCE_TEST_SOURCE.read_bytes())


def test_review_generator_itself_has_no_write_or_lifecycle_primitive() -> None:
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    attributes = {
        node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
    }
    assert not {
        "write", "write_text", "write_bytes", "unlink", "rename", "replace",
        "remove", "rmtree", "kill", "mount", "chmod", "chown", "fchmod",
        "fchown", "link", "symlink", "mkdir",
    } & attributes


def test_repo_artifact_is_deterministic_when_present() -> None:
    if not R.OUTPUT.exists():
        pytest.skip("canonical evidence review not materialized")
    document, _ = _build()
    validated = R.validate_repo_artifact(document)
    assert validated["review"]["path"] == str(R.OUTPUT)
    assert json.loads(R.OUTPUT.read_bytes()) == document
