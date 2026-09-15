from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import sys
from typing import Mapping

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (
    ROOT
    / "work/blind-phase-confirmatory-v2/freeze_operational_amendment_v2.py"
)
EVIDENCE_SCRIPT = (
    ROOT
    / "work/blind-phase-confirmatory-v2/build_no_decoder_since_v1_evidence.py"
)
EVIDENCE_TEST = ROOT / "tests/test_blind_phase_no_decoder_since_v1_evidence.py"
COMPONENT_CLOSURE = ROOT / "reports/blind-phase-confirmatory-component-closure-v1.json"
COMPONENT_CLOSURE_SCRIPT = (
    ROOT / "work/blind-phase-confirmatory-v2/build_component_closure_manifest.py"
)
COMPONENT_CLOSURE_TEST = ROOT / "tests/test_blind_phase_component_closure_manifest.py"


def _module():
    name = "blind_phase_operational_amendment_v2_test_module"
    spec = importlib.util.spec_from_file_location(name, SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


FREEZER = _module()


def _evidence_module():
    name = "blind_phase_no_decoder_since_v1_for_amendment_test_module"
    spec = importlib.util.spec_from_file_location(name, EVIDENCE_SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


EVIDENCE = _evidence_module()


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _identity(path: Path) -> dict[str, object]:
    return FREEZER.regular_file_identity(path)


def _active_dependencies(paths: Mapping[str, Path]) -> dict[str, object]:
    return {
        "runtime_guard_builder_v2": _identity(paths["runtime_guard_builder_v2"]),
        "campaign_launcher_v2": _identity(paths["launcher_v2"]),
        "frozen_campaign_launcher": _identity(FREEZER.FROZEN_CAMPAIGN_LAUNCHER),
        "normalizer_v2": _identity(paths["normalizer_v2"]),
    }


def _bundle(tmp_path: Path) -> dict[str, object]:
    amendment_v1, amendment_v1_identity = FREEZER._load_and_validate_v1()
    inventory = FREEZER._verify_unchanged_decoder_output_inventory(amendment_v1)
    paths = {
        "launcher_v2": tmp_path / "campaign_launcher_amended_v2.py",
        "runtime_guard_builder_v2": tmp_path / "build_runtime_guard_v2.py",
        "normalizer_v2": tmp_path / "normalize_result_amended_v2.py",
        "normalizer_v2_test": tmp_path / "test_blind_phase_normalizer_amended_v2.py",
        "launcher_test": tmp_path / "test_campaign_launcher_amended_v2.py",
        "runtime_guard_test": tmp_path / "test_runtime_guard_builder_v2.py",
        "freezer_test": tmp_path / "test_operational_amendment_v2.py",
    }
    for index, path in enumerate(paths.values(), start=1):
        path.write_text(f"# independently reviewed test artifact {index}\n", encoding="utf-8")

    evidence = EVIDENCE.build_evidence(generated_at="2026-09-04T16:00:00Z")
    evidence_path = tmp_path / "no-decoder-evidence.json"
    _write_json(evidence_path, evidence)

    tests = [
        _identity(paths["launcher_test"]),
        _identity(paths["runtime_guard_test"]),
        _identity(paths["freezer_test"]),
        _identity(EVIDENCE_TEST),
        _identity(paths["normalizer_v2_test"]),
        _identity(COMPONENT_CLOSURE_TEST),
    ]
    review: dict[str, object] = {
        "schema_version": FREEZER.REVIEW_SCHEMA_VERSION,
        "status": "GO",
        "reviewed_superseded_amendment": amendment_v1_identity,
        "reviewed_launcher_v2": _identity(paths["launcher_v2"]),
        "reviewed_runtime_guard_builder_v2": _identity(
            paths["runtime_guard_builder_v2"]
        ),
        "reviewed_normalizer_v2": _identity(paths["normalizer_v2"]),
        "reviewed_transitive_code_dependencies": _active_dependencies(paths),
        "reviewed_freezer_v2": _identity(SCRIPT),
        "reviewed_tests": tests,
        "reviewed_no_decoder_evidence_generator": _identity(EVIDENCE_SCRIPT),
        "reviewed_no_decoder_evidence": _identity(evidence_path),
        "reviewed_component_closure_generator": _identity(COMPONENT_CLOSURE_SCRIPT),
        "reviewed_component_closure": _identity(COMPONENT_CLOSURE),
        "checks": FREEZER._expected_review_checks(),
    }
    review["review_payload_sha256"] = FREEZER.sha256_document(review)
    review_path = tmp_path / "review.json"
    _write_json(review_path, review)
    return {
        **paths,
        "expected_launcher_v2_sha256": _identity(paths["launcher_v2"])["sha256"],
        "expected_runtime_guard_builder_v2_sha256": _identity(
            paths["runtime_guard_builder_v2"]
        )["sha256"],
        "expected_normalizer_v2_sha256": _identity(paths["normalizer_v2"])[
            "sha256"
        ],
        "expected_normalizer_v2_test_sha256": tests[4]["sha256"],
        "expected_launcher_test_sha256": tests[0]["sha256"],
        "expected_runtime_guard_test_sha256": tests[1]["sha256"],
        "expected_freezer_test_sha256": tests[2]["sha256"],
        "no_decoder_evidence_generator": EVIDENCE_SCRIPT,
        "expected_no_decoder_evidence_generator_sha256": _identity(EVIDENCE_SCRIPT)[
            "sha256"
        ],
        "no_decoder_evidence_generator_test": EVIDENCE_TEST,
        "expected_no_decoder_evidence_generator_test_sha256": tests[3]["sha256"],
        "no_decoder_evidence_path": evidence_path,
        "expected_no_decoder_evidence_sha256": _identity(evidence_path)["sha256"],
        "component_closure_generator": COMPONENT_CLOSURE_SCRIPT,
        "expected_component_closure_generator_sha256": _identity(
            COMPONENT_CLOSURE_SCRIPT
        )["sha256"],
        "component_closure_generator_test": COMPONENT_CLOSURE_TEST,
        "expected_component_closure_generator_test_sha256": tests[5]["sha256"],
        "component_closure_path": COMPONENT_CLOSURE,
        "expected_component_closure_sha256": _identity(COMPONENT_CLOSURE)["sha256"],
        "review_path": review_path,
        "expected_review_sha256": _identity(review_path)["sha256"],
        "evidence": evidence,
        "inventory": inventory,
        "amendment_v1_identity": amendment_v1_identity,
    }


def _build(tmp_path: Path) -> dict[str, object]:
    bundle = _bundle(tmp_path)
    arguments = {
        key: value
        for key, value in bundle.items()
        if key
        in {
            "launcher_v2",
            "expected_launcher_v2_sha256",
            "runtime_guard_builder_v2",
            "expected_runtime_guard_builder_v2_sha256",
            "normalizer_v2",
            "expected_normalizer_v2_sha256",
            "normalizer_v2_test",
            "expected_normalizer_v2_test_sha256",
            "launcher_test",
            "expected_launcher_test_sha256",
            "runtime_guard_test",
            "expected_runtime_guard_test_sha256",
            "freezer_test",
            "expected_freezer_test_sha256",
            "no_decoder_evidence_generator",
            "expected_no_decoder_evidence_generator_sha256",
            "no_decoder_evidence_generator_test",
            "expected_no_decoder_evidence_generator_test_sha256",
            "no_decoder_evidence_path",
            "expected_no_decoder_evidence_sha256",
            "component_closure_generator",
            "expected_component_closure_generator_sha256",
            "component_closure_generator_test",
            "expected_component_closure_generator_test_sha256",
            "component_closure_path",
            "expected_component_closure_sha256",
            "review_path",
            "expected_review_sha256",
        }
    }
    return FREEZER.build_amendment(
        **arguments, created_at="2026-09-04T16:01:00Z"
    )


def test_live_v1_and_decoder_output_inventory_are_still_exact() -> None:
    amendment, identity = FREEZER._load_and_validate_v1()
    assert identity["sha256"] == FREEZER.EXPECTED_AMENDMENT_V1_SHA256
    inventory = FREEZER._verify_unchanged_decoder_output_inventory(amendment)
    assert inventory["exposed_decoder_unit_count"] == 2
    assert inventory["new_decoder_unit_count"] == 0
    assert inventory["file_count"] == 6


def test_build_supersedes_v1_and_preserves_complete_scientific_contract(
    tmp_path: Path,
) -> None:
    document = _build(tmp_path)
    assert document["schema_version"] == FREEZER.SCHEMA_VERSION
    assert document["status"] == "approved_after_acquisition_before_remaining_decoder_execution"
    assert document["supersedes"]["amendment"]["sha256"] == (
        FREEZER.EXPECTED_AMENDMENT_V1_SHA256
    )
    assert document["supersedes"]["v1_artifact_modified"] is False
    assert document["schedule"]["unit_count"] == 6168
    assert document["schedule"]["units_to_execute"] == 6166
    assert len(document["prior_decoder_exposures"]) == 2
    assert all(
        row["observation_id"] == 4491
        and row["decoder_rerun_permitted"] is False
        for row in document["prior_decoder_exposures"]
    )
    assert document["sensitivity_exclusion_observation_ids"] == [4491]
    assert document["sensitivity_analysis"]["exclude_observation_ids"] == [4491]
    assert document["runtime_history_disclosure"] == {
        "remaining_decoder_run_count_with_full_component_closure_bound": 6166,
        "prior_exposed_decoder_unit_count": 2,
        "prior_exposed_observation_ids": [4491],
        "full_component_closure_for_prior_exposures_cryptographically_established": False,
        "full_30_observation_cohort_runtime_history": "mixed_and_non_pristine",
        "full_30_observation_cohort_retained_with_disclosure": True,
        "mandatory_sensitivity_observation_count": 29,
        "mandatory_sensitivity_excludes_entire_observation_ids": [4491],
    }
    assert document["amended_normalizer"] == _identity(
        tmp_path / "normalize_result_amended_v2.py"
    )
    imports = document["transitive_code_dependency_contract"]["dependencies"]
    assert imports == _active_dependencies(
        {
            "launcher_v2": tmp_path / "campaign_launcher_amended_v2.py",
            "runtime_guard_builder_v2": tmp_path / "build_runtime_guard_v2.py",
            "normalizer_v2": tmp_path / "normalize_result_amended_v2.py",
        }
    )
    assert imports["frozen_campaign_launcher"]["sha256"] == (
        FREEZER.EXPECTED_FROZEN_CAMPAIGN_LAUNCHER_SHA256
    )
    implementation = document["amended_implementation"]
    assert implementation["transitive_code_dependencies"] == imports
    assert not any(key.startswith("base_") for key in implementation)
    recovery = document["runtime_materialization_contract"]["recovery_and_handoff"]
    assert recovery["rollback_control_artifacts_if_invoked"] == [
        "rollback-sealed-runtimes-intent-v2.json",
        "rollback-sealed-runtimes-ready-to-unmount-v2.json",
        "rollback-sealed-runtimes-complete-v2.json",
    ]
    scope = document["operational_scope"]
    assert scope["execution_plan_changed"] is False
    assert scope["selection_changed"] is False
    assert scope["candidate_config_changed"] is False
    assert scope["normalizer_or_scoring_changed"] is True
    assert scope["scientific_normalization_or_scoring_changed"] is False
    assert scope["scoring_changed"] is False
    fix = document["determinism_projection_fix"]
    assert fix["hashes_retained_in_provenance"] == [
        "process_receipt_sha256",
        "raw_result_payload_sha256",
    ]
    assert fix["hashes_removed_only_from_semantic_receiver_projection"] == [
        "process_receipt_sha256",
        "raw_result_payload_sha256",
    ]
    assert fix["frozen_endpoint_changed"] is False
    assert fix["frozen_metric_changed"] is False
    payload_hash = document.pop("amendment_payload_sha256")
    assert payload_hash == FREEZER.sha256_document(document)


def test_runtime_topology_and_read_only_mount_lifecycle_are_exact(
    tmp_path: Path,
) -> None:
    document = _build(tmp_path)
    runtime = document["runtime_materialization_contract"]
    assert runtime["internal_symlinks_permitted"] is True
    assert runtime["external_symlinks_permitted"] is False
    assert runtime["external_hardlink_path_count"] == 19_619
    assert runtime["external_hardlink_bytes_changed"] is False
    recovery = runtime["recovery_and_handoff"]
    assert recovery["root_control_artifacts_in_order"][-2:] == [
        "freeze-guard-handoff-v2-<attempt_sha256>.json",
        "runtime-guard-v2.json",
    ]
    assert recovery["intent_published_before_mount_or_runtime_mutation"] is True
    assert recovery["candidate_interpreter_execution_before_closed_window"] is False
    identity = document["execution_identity_and_mount_contract"]
    assert identity["launcher_euid"] == identity["launcher_egid"] == 1000
    assert identity["project_root_mount"] == "exact root-only self-bind remounted read-only"
    assert identity["seal_control_interpreter"] == "/usr/bin/python3.12 -I -S"
    assert identity["lifecycle_builder_bootstrap"] == {
        "source": document["amended_implementation"]["runtime_guard_builder"],
        "copy_tool": "/usr/bin/install",
        "destination": "root-only control directory outside the project and campaign-results roots",
        "destination_owner_uid": 0,
        "destination_owner_gid": 0,
        "destination_mode": "0555",
        "source_and_copy_sha256_must_match": True,
        "sha256_verified_before_every_root_lifecycle_subcommand": True,
        "guard_binds_control_copy_identity": True,
        "builder_executed_from_writable_repository": False,
    }
    assert identity["seal_window_nested_rw_bind_count"] == 2
    assert identity["nested_rw_mount_count_during_freeze_guard_campaign_and_evaluation"] == 0
    assert identity["control_parent_owner_uid"] == identity["control_parent_owner_gid"] == 0
    assert identity["control_parent_mode"] == "0755"
    assert identity["campaign_results_root_location"] == "outside_project_root_and_control_parent"
    assert identity["campaign_results_root_mode"] == "0700"
    assert identity["read_only_lifecycle"][-1] == (
        "root deactivates the project mount without deleting control artifacts or campaign results"
    )


def test_no_decoder_evidence_fails_closed_on_any_new_raw_result(
    tmp_path: Path,
) -> None:
    bundle = _bundle(tmp_path)
    evidence = dict(bundle["evidence"])
    evidence["checks"] = dict(evidence["checks"])
    evidence["checks"]["new_raw_result_count_since_timestamp"] = 1
    evidence["evidence_payload_sha256"] = FREEZER.sha256_document(
        {key: value for key, value in evidence.items() if key != "evidence_payload_sha256"}
    )
    with pytest.raises(ValueError, match="evidence is not exact PASS"):
        FREEZER._validate_no_decoder_evidence(
            evidence,
            amendment_v1_identity=bundle["amendment_v1_identity"],
            evidence_generator_identity=_identity(EVIDENCE_SCRIPT),
            inventory=bundle["inventory"],
        )


def test_no_decoder_evidence_period_must_start_at_v1_timestamp(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    evidence = dict(bundle["evidence"])
    evidence["period_start_utc"] = "2026-09-04T15:59:00Z"
    evidence["evidence_payload_sha256"] = FREEZER.sha256_document(
        {key: value for key, value in evidence.items() if key != "evidence_payload_sha256"}
    )
    with pytest.raises(ValueError, match="does not begin at amendment v1 timestamp"):
        FREEZER._validate_no_decoder_evidence(
            evidence,
            amendment_v1_identity=bundle["amendment_v1_identity"],
            evidence_generator_identity=_identity(EVIDENCE_SCRIPT),
            inventory=bundle["inventory"],
        )


def test_review_requires_go_and_zero_p0_p1_p2(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    review = json.loads(bundle["review_path"].read_text(encoding="utf-8"))
    review["checks"]["p1_findings"] = 1
    review["review_payload_sha256"] = FREEZER.sha256_document(
        {key: value for key, value in review.items() if key != "review_payload_sha256"}
    )
    with pytest.raises(ValueError, match="review is not exact GO"):
        FREEZER._validate_review(
            review,
            amendment_v1=bundle["amendment_v1_identity"],
            launcher=_identity(bundle["launcher_v2"]),
            runtime_guard_builder=_identity(bundle["runtime_guard_builder_v2"]),
            normalizer_v2=_identity(bundle["normalizer_v2"]),
            transitive_code_dependencies=_active_dependencies(bundle),
            freezer=_identity(SCRIPT),
            tests=[
                _identity(bundle["launcher_test"]),
                _identity(bundle["runtime_guard_test"]),
                _identity(bundle["freezer_test"]),
                    _identity(EVIDENCE_TEST),
                    _identity(bundle["normalizer_v2_test"]),
                    _identity(COMPONENT_CLOSURE_TEST),
                ],
                no_decoder_evidence_generator=_identity(EVIDENCE_SCRIPT),
                no_decoder_evidence=_identity(bundle["no_decoder_evidence_path"]),
                component_closure_generator=_identity(COMPONENT_CLOSURE_SCRIPT),
                component_closure=_identity(COMPONENT_CLOSURE),
        )


def test_review_cannot_omit_read_only_mount_or_symlink_gate(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    review = json.loads(bundle["review_path"].read_text(encoding="utf-8"))
    review["checks"][
        "project_root_exact_self_bind_read_only_during_freeze_guard_and_campaign"
    ] = False
    review["review_payload_sha256"] = FREEZER.sha256_document(
        {key: value for key, value in review.items() if key != "review_payload_sha256"}
    )
    with pytest.raises(ValueError, match="review is not exact GO"):
        FREEZER._validate_review(
            review,
            amendment_v1=bundle["amendment_v1_identity"],
            launcher=_identity(bundle["launcher_v2"]),
            runtime_guard_builder=_identity(bundle["runtime_guard_builder_v2"]),
            normalizer_v2=_identity(bundle["normalizer_v2"]),
            transitive_code_dependencies=_active_dependencies(bundle),
            freezer=_identity(SCRIPT),
            tests=[
                _identity(bundle["launcher_test"]),
                _identity(bundle["runtime_guard_test"]),
                _identity(bundle["freezer_test"]),
                    _identity(EVIDENCE_TEST),
                    _identity(bundle["normalizer_v2_test"]),
                    _identity(COMPONENT_CLOSURE_TEST),
                ],
                no_decoder_evidence_generator=_identity(EVIDENCE_SCRIPT),
                no_decoder_evidence=_identity(bundle["no_decoder_evidence_path"]),
                component_closure_generator=_identity(COMPONENT_CLOSURE_SCRIPT),
                component_closure=_identity(COMPONENT_CLOSURE),
        )


def test_review_cannot_approve_broken_semantic_determinism_projection(
    tmp_path: Path,
) -> None:
    bundle = _bundle(tmp_path)
    review = json.loads(bundle["review_path"].read_text(encoding="utf-8"))
    review["checks"][
        "equal_semantic_different_bookkeeping_synthetic_case_passes_repeat_gate"
    ] = False
    review["review_payload_sha256"] = FREEZER.sha256_document(
        {key: value for key, value in review.items() if key != "review_payload_sha256"}
    )
    with pytest.raises(ValueError, match="review is not exact GO"):
        FREEZER._validate_review(
            review,
            amendment_v1=bundle["amendment_v1_identity"],
            launcher=_identity(bundle["launcher_v2"]),
            runtime_guard_builder=_identity(bundle["runtime_guard_builder_v2"]),
            normalizer_v2=_identity(bundle["normalizer_v2"]),
            transitive_code_dependencies=_active_dependencies(bundle),
            freezer=_identity(SCRIPT),
            tests=[
                _identity(bundle["launcher_test"]),
                _identity(bundle["runtime_guard_test"]),
                _identity(bundle["freezer_test"]),
                    _identity(EVIDENCE_TEST),
                    _identity(bundle["normalizer_v2_test"]),
                    _identity(COMPONENT_CLOSURE_TEST),
                ],
                no_decoder_evidence_generator=_identity(EVIDENCE_SCRIPT),
                no_decoder_evidence=_identity(bundle["no_decoder_evidence_path"]),
                component_closure_generator=_identity(COMPONENT_CLOSURE_SCRIPT),
                component_closure=_identity(COMPONENT_CLOSURE),
        )


def test_frozen_campaign_launcher_sha_mismatch_fails_before_amendment_build(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    changed = tmp_path / "campaign_launcher.py"
    changed.write_text("# changed transitive import\n", encoding="utf-8")
    monkeypatch.setattr(FREEZER, "FROZEN_CAMPAIGN_LAUNCHER", changed)
    with pytest.raises(ValueError, match="artifact SHA-256 mismatch"):
        _build(tmp_path)


def test_build_opens_no_iq_or_candidate_runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original_open = FREEZER.os.open
    opened: list[str] = []

    def recording_open(path: object, *args: object, **kwargs: object) -> int:
        opened.append(os.fspath(path))
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(FREEZER.os, "open", recording_open)
    _build(tmp_path)
    assert not any(path.endswith(".iq") for path in opened)
    assert not any("/runtime-r" in path or "/sealed-runtime" in path for path in opened)


def test_publish_is_self_hashed_chmod_0444_and_pairwise_no_clobber(
    tmp_path: Path,
) -> None:
    document = {"schema_version": FREEZER.SCHEMA_VERSION, "value": 1}
    document["amendment_payload_sha256"] = FREEZER.sha256_document(document)
    output = tmp_path / "amendment-v2.json"
    digest = FREEZER.publish_no_clobber(output, document)
    sidecar = output.with_name(output.name + ".sha256")
    assert digest == hashlib.sha256(output.read_bytes()).hexdigest()
    assert sidecar.read_text(encoding="ascii") == f"{digest}  {output.name}\n"
    assert stat.S_IMODE(output.stat().st_mode) == 0o444
    assert stat.S_IMODE(sidecar.stat().st_mode) == 0o444
    with pytest.raises(ValueError, match="already exists"):
        FREEZER.publish_no_clobber(output, document)


def test_existing_sidecar_prevents_partial_output(tmp_path: Path) -> None:
    output = tmp_path / "amendment-v2.json"
    sidecar = output.with_name(output.name + ".sha256")
    sidecar.write_text("reserved\n", encoding="ascii")
    with pytest.raises(ValueError, match="already exists"):
        FREEZER.publish_no_clobber(output, {"schema_version": FREEZER.SCHEMA_VERSION})
    assert not output.exists()
