from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "work/blind-phase-confirmatory-v2/freeze_amended_evaluator_lock_v1.py"


def _module():
    name = "blind_phase_amended_evaluator_lock_test_module"
    spec = importlib.util.spec_from_file_location(name, SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


FREEZE = _module()


def _write(path: Path, document: object) -> dict[str, object]:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, sort_keys=True) + "\n", encoding="utf-8")
    payload = path.read_bytes()
    return {
        "path": str(path.absolute()),
        "size_bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }


def _file(path: Path, value: str) -> dict[str, object]:
    path.write_text(value, encoding="utf-8")
    return _identity(path)


def _identity(path: Path) -> dict[str, object]:
    payload = path.read_bytes()
    return {
        "path": str(path.absolute()),
        "size_bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }


def _review_checks() -> dict[str, object]:
    return {
        "iq_or_campaign_outcomes_opened_by_evaluator_work": False,
        "scientific_unit_count": 6168,
        "full_cohort_observation_count": 30,
        "mandatory_unexposed_sensitivity_observation_count": 29,
        "sensitivity_exclusion_observation_ids": [4491],
        "frozen_scoring_engine_reused_without_endpoint_changes": True,
        "normalizer_v2_semantic_projection_fix_bound": True,
        "exact_result_identity_path_order_and_self_hash_validation": True,
        "all_json_artifacts_parsed_from_same_verified_fd_bytes": True,
        "duplicate_and_incomplete_coverage_fail_closed": True,
        "evaluation_output_no_clobber": True,
        "amended_evaluator_verified_from_bytes_before_import": True,
        "frozen_evaluator_verified_from_bytes_before_import": True,
        "frozen_launcher_verified_from_bytes_before_import": True,
        "metadata_guard_and_evaluator_lock_validated_before_dependency_activation": True,
        "invalid_lock_fails_before_any_frozen_dependency_import": True,
        "exact_sealed_candidate_runtime_closure_bound": True,
        "sys_executable_is_exact_plan_bound_sealed_candidate_python": True,
        "unsafe_project_and_external_site_search_paths_removed_before_import": True,
        "all_loaded_telemetry_numpy_scipy_modules_inside_sealed_runtime": True,
        "evaluator_lock_root_owned_control_parent_enforced": True,
        "runtime_guard_is_exact_root_root_0444_control_parent_child": True,
        "evaluator_lock_is_exact_root_root_0444_control_parent_child": True,
        "control_parent_separate_from_uid1000_campaign_results": True,
        "evaluated_namespace_is_exact_uid1000_mode0700_child_of_guarded_results_parent": True,
        "zero_executed_unbound_local_python": True,
        "no_decoder_evidence_v2_bound": True,
        "complete_component_runtime_closure_bound_before_remaining_6166_runs": True,
        "component_runtime_closure_generator_and_self_hash_verified": True,
        "prior_two_exposures_full_component_closure_not_cryptographically_established": True,
        "full_30_cohort_mixed_runtime_history_non_pristine": True,
        "mandatory_sensitivity_29_excludes_all_of_observation_4491": True,
        "p0_findings": 0,
        "p1_findings": 0,
        "p2_findings": 0,
    }


def test_review_must_bind_every_implementation_and_clean_checks(tmp_path: Path) -> None:
    evaluator = {"path": "/evaluator", "size_bytes": 1, "sha256": "1" * 64}
    evaluator_test = {"path": "/test", "size_bytes": 1, "sha256": "2" * 64}
    freezer = {"path": "/freezer", "size_bytes": 1, "sha256": "3" * 64}
    freezer_test = {"path": "/freezer-test", "size_bytes": 1, "sha256": "4" * 64}
    dependencies = {
        "amended_evaluator": evaluator,
        "frozen_scoring_evaluator": {
            "path": "/scoring",
            "size_bytes": 1,
            "sha256": "5" * 64,
        },
        "frozen_schedule_launcher": {
            "path": "/launcher",
            "size_bytes": 1,
            "sha256": "6" * 64,
        },
        "sealed_candidate_runtime": {
            "path": "/sealed-runtime",
            "recursive_entry_count": 1,
            "recursive_manifest_sha256": "7" * 64,
        },
    }
    component_closure = {
        "path": "/component-closure.json",
        "size_bytes": 1,
        "sha256": "8" * 64,
    }
    component_closure_generator = {
        "path": "/component-closure-generator.py",
        "size_bytes": 1,
        "sha256": "9" * 64,
    }
    control_parent = {"path": "/control", "uid": 0, "gid": 0, "mode": 0o755}
    campaign_results_parent = {
        "path": "/results",
        "uid": 1000,
        "gid": 1000,
        "mode": 0o700,
    }
    review = {
        "schema_version": FREEZE.REVIEW_SCHEMA_VERSION,
        "status": "PASS",
        "reviewed_amended_evaluator": evaluator,
        "reviewed_evaluator_tests": evaluator_test,
        "reviewed_lock_freezer": freezer,
        "reviewed_lock_freezer_tests": freezer_test,
        "reviewed_executed_local_python_dependencies": dependencies,
        "reviewed_component_runtime_closure": component_closure,
        "reviewed_component_runtime_closure_generator": component_closure_generator,
        "reviewed_control_parent": control_parent,
        "reviewed_campaign_results_parent": campaign_results_parent,
        "checks": _review_checks(),
    }
    review["review_payload_sha256"] = FREEZE.EVALUATOR.sha256_document(review)
    path = tmp_path / "review.json"
    identity = _write(path, review)
    loaded, actual = FREEZE._review(
        path,
        expected_sha256=str(identity["sha256"]),
        evaluator_identity=evaluator,
        evaluator_test_identity=evaluator_test,
        freezer_identity=freezer,
        freezer_test_identity=freezer_test,
        executed_dependencies=dependencies,
        component_closure_identity=component_closure,
        component_closure_generator_identity=component_closure_generator,
        control_parent=control_parent,
        campaign_results_parent=campaign_results_parent,
    )
    assert loaded == review
    assert actual == identity
    changed = json.loads(json.dumps(review))
    changed["checks"]["p2_findings"] = 1
    changed["review_payload_sha256"] = FREEZE.EVALUATOR.sha256_document(
        {key: value for key, value in changed.items() if key != "review_payload_sha256"}
    )
    changed_identity = _write(tmp_path / "changed-review.json", changed)
    with pytest.raises(ValueError, match="clean PASS"):
        FREEZE._review(
            tmp_path / "changed-review.json",
            expected_sha256=str(changed_identity["sha256"]),
            evaluator_identity=evaluator,
            evaluator_test_identity=evaluator_test,
            freezer_identity=freezer,
            freezer_test_identity=freezer_test,
            executed_dependencies=dependencies,
            component_closure_identity=component_closure,
            component_closure_generator_identity=component_closure_generator,
            control_parent=control_parent,
            campaign_results_parent=campaign_results_parent,
        )


def test_lock_build_is_metadata_only_self_hashed_and_rfc_ready(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = {
        name: tmp_path / f"{name}.json"
        for name in (
            "plan",
            "acquisition",
            "source",
            "amendment",
            "guard",
            "evidence",
            "component_closure",
        )
    }
    documents = {
        "plan": {
            "provenance": {
                "campaign_execution_tools": {
                    "evaluator": FREEZE.EVALUATOR.regular_file_identity(
                        FREEZE.EVALUATOR.FROZEN_EVALUATOR_PATH
                    )
                }
            },
            "routing": {
                "routes": [
                    {"observation_id": 4491 + index, "profile_routable": index < 17}
                    for index in range(30)
                ]
            },
            "null_controls": {
                "controls_per_observation": [
                    {"kind": "all_zero", "seed": None},
                    *(
                        {"kind": "phase_scramble", "seed": index}
                        for index in range(10)
                    ),
                ]
            },
        },
        "acquisition": {"status": "complete"},
        "source": {"signals": [{"observation_id": 4491 + index} for index in range(30)]},
        "amendment": {"status": "approved"},
        "guard": {
            "status": "active",
            "protected_roots": {
                "candidate": {
                    "path": "/sealed-runtime",
                    "recursive_entry_count": 1,
                    "recursive_manifest_sha256": "7" * 64,
                }
            },
            "control_parent": {
                "path": "/control",
                "st_dev": 1,
                "st_ino": 2,
                "uid": 0,
                "gid": 0,
                "mode": 0o755,
            },
            "campaign_results_parent": {
                "path": "/results",
                "st_dev": 1,
                "st_ino": 3,
                "uid": 1000,
                "gid": 1000,
                "mode": 0o700,
            },
        },
        "evidence": {"status": "PASS"},
        "component_closure": {"status": "complete"},
    }
    identities = {name: _write(paths[name], document) for name, document in documents.items()}
    monkeypatch.setattr(
        FREEZE.EVALUATOR,
        "EXPECTED_EXECUTION_PLAN_SHA256",
        identities["plan"]["sha256"],
    )
    monkeypatch.setattr(
        FREEZE.EVALUATOR,
        "EXPECTED_ACQUISITION_MANIFEST_SHA256",
        identities["acquisition"]["sha256"],
    )
    monkeypatch.setattr(
        FREEZE.EVALUATOR,
        "EXPECTED_SOURCE_MANIFEST_SHA256",
        identities["source"]["sha256"],
    )
    monkeypatch.setattr(
        FREEZE.EVALUATOR,
        "EXPECTED_NO_DECODER_EVIDENCE_V2_SHA256",
        identities["evidence"]["sha256"],
    )
    monkeypatch.setattr(
        FREEZE.EVALUATOR, "_validate_plan_and_acquisition", lambda **kwargs: None
    )
    monkeypatch.setattr(
        FREEZE.EVALUATOR,
        "_validate_frozen_source_manifest",
        lambda source, **kwargs: source,
    )
    monkeypatch.setattr(FREEZE.EVALUATOR, "_validate_amendment", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        FREEZE.EVALUATOR, "_validate_component_closure", lambda value, **kwargs: value
    )
    monkeypatch.setattr(FREEZE.EVALUATOR, "_validate_runtime_guard", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        FREEZE.EVALUATOR,
        "_validate_control_artifact_path",
        lambda *args, **kwargs: {},
    )
    monkeypatch.setattr(
        FREEZE.EVALUATOR,
        "_validate_sealed_candidate_interpreter",
        lambda *args, **kwargs: Path("/sealed-runtime"),
    )
    monkeypatch.setattr(
        FREEZE.EVALUATOR,
        "_validate_no_decoder_evidence",
        lambda *args, **kwargs: {
            "evidence_payload_sha256": "8" * 64,
            "campaign_output_inventory": {"inventory_sha256": "9" * 64},
        },
    )

    launcher_identity = _file(tmp_path / "launcher.py", "launcher-v2")
    normalizer_identity = _file(tmp_path / "normalizer.py", "normalizer-v2")
    evidence_generator_identity = _file(
        tmp_path / "evidence-generator.py", "evidence-generator"
    )
    component_closure_generator_identity = _file(
        tmp_path / "component-closure-generator.py", "component-closure-generator"
    )
    monkeypatch.setattr(
        FREEZE.EVALUATOR,
        "EXPECTED_NO_DECODER_EVIDENCE_GENERATOR_SHA256",
        evidence_generator_identity["sha256"],
    )
    monkeypatch.setattr(
        FREEZE.EVALUATOR,
        "EXPECTED_COMPONENT_CLOSURE_SHA256",
        identities["component_closure"]["sha256"],
    )
    monkeypatch.setattr(
        FREEZE.EVALUATOR,
        "EXPECTED_COMPONENT_CLOSURE_GENERATOR_SHA256",
        component_closure_generator_identity["sha256"],
    )
    evaluator_test_identity = _file(tmp_path / "evaluator-test.py", "evaluator-tests")
    freezer_test_identity = _file(tmp_path / "freezer-test.py", "freezer-tests")
    evaluator_identity = FREEZE.EVALUATOR.regular_file_identity(FREEZE.EVALUATOR_PATH)
    freezer_identity = FREEZE.EVALUATOR.regular_file_identity(SCRIPT)
    dependencies = {
        "amended_evaluator": evaluator_identity,
        "frozen_scoring_evaluator": FREEZE.EVALUATOR.regular_file_identity(
            FREEZE.EVALUATOR.FROZEN_EVALUATOR_PATH
        ),
        "frozen_schedule_launcher": FREEZE.EVALUATOR.regular_file_identity(
            FREEZE.EVALUATOR.FROZEN_LAUNCHER_PATH
        ),
        "sealed_candidate_runtime": documents["guard"]["protected_roots"]["candidate"],
    }
    review = {
        "schema_version": FREEZE.REVIEW_SCHEMA_VERSION,
        "status": "PASS",
        "reviewed_amended_evaluator": evaluator_identity,
        "reviewed_evaluator_tests": evaluator_test_identity,
        "reviewed_lock_freezer": freezer_identity,
        "reviewed_lock_freezer_tests": freezer_test_identity,
        "reviewed_executed_local_python_dependencies": dependencies,
        "reviewed_component_runtime_closure": identities["component_closure"],
        "reviewed_component_runtime_closure_generator": component_closure_generator_identity,
        "reviewed_control_parent": documents["guard"]["control_parent"],
        "reviewed_campaign_results_parent": documents["guard"]["campaign_results_parent"],
        "checks": _review_checks(),
    }
    review["review_payload_sha256"] = FREEZE.EVALUATOR.sha256_document(review)
    review_identity = _write(tmp_path / "review.json", review)
    lock = FREEZE.build_lock(
        execution_plan_path=paths["plan"],
        expected_execution_plan_sha256=str(identities["plan"]["sha256"]),
        acquisition_manifest_path=paths["acquisition"],
        expected_acquisition_manifest_sha256=str(identities["acquisition"]["sha256"]),
        source_manifest_path=paths["source"],
        expected_source_manifest_sha256=str(identities["source"]["sha256"]),
        amendment_path=paths["amendment"],
        expected_amendment_sha256=str(identities["amendment"]["sha256"]),
        runtime_guard_path=paths["guard"],
        expected_runtime_guard_sha256=str(identities["guard"]["sha256"]),
        no_decoder_evidence_path=paths["evidence"],
        expected_no_decoder_evidence_sha256=str(identities["evidence"]["sha256"]),
        no_decoder_evidence_generator_path=tmp_path / "evidence-generator.py",
        expected_no_decoder_evidence_generator_sha256=str(
            evidence_generator_identity["sha256"]
        ),
        component_closure_path=paths["component_closure"],
        expected_component_closure_sha256=str(
            identities["component_closure"]["sha256"]
        ),
        component_closure_generator_path=tmp_path / "component-closure-generator.py",
        expected_component_closure_generator_sha256=str(
            component_closure_generator_identity["sha256"]
        ),
        amended_launcher_path=tmp_path / "launcher.py",
        expected_amended_launcher_sha256=str(launcher_identity["sha256"]),
        amended_normalizer_path=tmp_path / "normalizer.py",
        expected_amended_normalizer_sha256=str(normalizer_identity["sha256"]),
        evaluator_test_path=tmp_path / "evaluator-test.py",
        expected_evaluator_test_sha256=str(evaluator_test_identity["sha256"]),
        freezer_test_path=tmp_path / "freezer-test.py",
        expected_freezer_test_sha256=str(freezer_test_identity["sha256"]),
        review_path=tmp_path / "review.json",
        expected_review_sha256=str(review_identity["sha256"]),
        created_at="2026-09-04T00:00:00Z",
    )
    assert lock["status"] == "frozen_before_campaign_outcome_evaluation"
    assert lock["analysis_contract"]["scientific_unit_count"] == 6168
    assert lock["analysis_contract"]["sensitivity_exclusion_observation_ids"] == [4491]
    assert lock["input_boundary"] == {
        "iq_opened_by_freezer": False,
        "campaign_unit_manifest_opened_by_freezer": False,
        "normalized_or_raw_campaign_outcomes_opened_by_freezer": False,
    }
    assert lock["rfc3161"]["timestamp_required_before_campaign_outcome_evaluation"] is True
    assert lock["executed_local_python_dependencies"] == dependencies
    assert lock["component_runtime_closure"] == identities["component_closure"]
    assert (
        lock["component_runtime_closure_generator"]
        == component_closure_generator_identity
    )
    assert lock["control_parent"] == documents["guard"]["control_parent"]
    assert lock["campaign_results_parent"] == documents["guard"]["campaign_results_parent"]
    unhashed = dict(lock)
    assert unhashed.pop("evaluator_lock_payload_sha256") == FREEZE.EVALUATOR.sha256_document(unhashed)


def test_lock_publish_is_immutable_no_clobber(tmp_path: Path) -> None:
    path = tmp_path / "lock.json"
    FREEZE._publish(path, {"status": "frozen"})
    assert path.stat().st_mode & 0o777 == 0o444
    with pytest.raises(ValueError, match="already exists"):
        FREEZE._publish(path, {"status": "different"})


def test_freezer_verified_loader_rejects_mutation_before_exec(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dependency = tmp_path / "amended-evaluator.py"
    dependency.write_bytes(b"VALUE = 1\n")
    expected = hashlib.sha256(dependency.read_bytes()).hexdigest()
    original_read = FREEZE.os.read
    changed = False

    def racing_read(descriptor: int, count: int) -> bytes:
        nonlocal changed
        block = original_read(descriptor, count)
        if block and not changed:
            changed = True
            dependency.write_bytes(b"VALUE = 2\n")
        return block

    monkeypatch.setattr(FREEZE.os, "read", racing_read)
    with pytest.raises(RuntimeError, match="identity mismatch"):
        FREEZE._load_module("mutating_lock_evaluator", dependency, expected)


def test_freezer_cli_has_no_iq_or_campaign_outcome_input() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    assert "--unit-manifest" not in source
    assert "--normalized-result" not in source
    assert "--raw-result" not in source
    assert "--iq" not in source
    for option in (
        "--expected-amendment-sha256",
        "--expected-runtime-guard-sha256",
        "--expected-no-decoder-evidence-sha256",
        "--expected-no-decoder-evidence-generator-sha256",
        "--expected-component-closure-sha256",
        "--expected-component-closure-generator-sha256",
        "--expected-amended-launcher-sha256",
        "--expected-amended-normalizer-sha256",
        "--expected-review-sha256",
    ):
        assert option in source
