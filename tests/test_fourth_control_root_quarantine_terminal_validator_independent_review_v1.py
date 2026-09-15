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
    "build_fourth_control_root_quarantine_terminal_validator_independent_review_v1.py"
)
SPEC = importlib.util.spec_from_file_location(
    "build_fourth_control_root_quarantine_terminal_validator_independent_review_v1",
    SOURCE,
)
assert SPEC is not None and SPEC.loader is not None
R = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(R)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _build() -> tuple[dict[str, object], dict[str, object]]:
    return R.build_review(_sha(R.GENERATOR), _sha(R.GENERATOR_TEST))


def test_exact_frozen_sources_and_clean_selfhashed_go() -> None:
    assert (_sha(R.VALIDATOR_SOURCE), R.VALIDATOR_SOURCE.stat().st_size) == R.EXPECTED_VALIDATOR
    assert (_sha(R.VALIDATOR_TEST_SOURCE), R.VALIDATOR_TEST_SOURCE.stat().st_size) == R.EXPECTED_VALIDATOR_TEST
    assert (_sha(R.CORRECTED_RUNNER_SOURCE), R.CORRECTED_RUNNER_SOURCE.stat().st_size) == R.EXPECTED_CORRECTED_RUNNER
    assert (_sha(R.CORRECTED_RUNNER_TEST_SOURCE), R.CORRECTED_RUNNER_TEST_SOURCE.stat().st_size) == R.EXPECTED_CORRECTED_RUNNER_TEST
    document, audit = _build()
    unhashed = copy.deepcopy(document)
    claimed = unhashed.pop(R.HASH_FIELD)
    assert claimed == R._document_sha(unhashed)
    assert document["schema_version"] == R.SCHEMA
    assert document["status"] == "GO"
    assert document["findings"] == {"P0": 0, "P1": 0, "P2": 0}
    assert audit["status"] == "DRY_RUN_PASS_NO_WRITE"
    assert audit["published"] is False


def test_review_binds_exact_installed_and_historical_paths() -> None:
    document, _ = _build()
    bindings = document["reviewed_bindings"]
    assert bindings["terminal_validator"] == {
        "path": str(R.VALIDATOR_COPY), "sha256": R.EXPECTED_VALIDATOR[0],
        "size_bytes": R.EXPECTED_VALIDATOR[1],
    }
    assert bindings["terminal_validator_test"] == {
        "path": str(R.VALIDATOR_TEST), "sha256": R.EXPECTED_VALIDATOR_TEST[0],
        "size_bytes": R.EXPECTED_VALIDATOR_TEST[1],
    }
    for label, (path, digest, size) in R.HISTORICAL_BINDINGS.items():
        assert bindings[label] == {
            "path": str(path), "sha256": digest, "size_bytes": size,
        }


def test_terminal_and_install_contracts_are_exact_read_only() -> None:
    document, _ = _build()
    terminal = document["terminal_live_contract"]
    install = document["install_and_preflight_contract"]
    assert terminal["pre_rename_snapshot_sha256"] == R.EXPECTED_PRE_SNAPSHOT
    assert terminal["post_rename_snapshot_sha256"] == R.EXPECTED_POST_SNAPSHOT
    assert terminal["stage2_result"] == R.EXPECTED_STAGE2_RESULT
    assert terminal["terminal_validator_prefix"] == 2
    assert terminal["provenance_committed"] is True
    assert terminal["validated_partial_provenance"] is False
    assert install["install_no_clobber_into_fresh_controller"] == str(R.VALIDATOR_CONTROL)
    assert install["lock_file_present"] is False
    assert install["required_prefix"] == 2
    assert install["exact_names"] == sorted({
        R.VALIDATOR_COPY.name, R.VALIDATOR_TEST.name, R.VALIDATOR_REVIEW.name,
        R.VALIDATOR_REVIEW.name + ".sha256",
    })


def test_contract_rejects_mutation_or_lost_parent_revalidation() -> None:
    validator = R.VALIDATOR_SOURCE.read_bytes().replace(
        b"_revalidate_pinned_directory(HISTORICAL_CONTROL, descriptor, parent)",
        b"pass", 1,
    ) + b"\nos.unlink('x')\n"
    with pytest.raises(R.ReviewError, match="contract differs"):
        R._validate_contract(validator, R.VALIDATOR_TEST_SOURCE.read_bytes())


def test_generator_itself_has_no_write_or_lifecycle_primitive() -> None:
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    attributes = {
        node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
    }
    assert not {
        "write_text", "write_bytes", "unlink", "rename", "replace", "remove",
        "rmtree", "kill", "mount", "chmod", "chown",
    } & attributes


def test_repo_artifact_is_deterministic_when_present() -> None:
    if not R.OUTPUT.exists():
        pytest.skip("canonical terminal-validator review not materialized")
    document, _ = _build()
    validated = R.validate_repo_artifact(document)
    assert validated["review"]["path"] == str(R.OUTPUT)
    assert json.loads(R.OUTPUT.read_bytes()) == document
