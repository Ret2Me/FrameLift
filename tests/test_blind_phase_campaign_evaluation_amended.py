from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import sys
import types

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "work/blind-phase-confirmatory-v2/evaluate_campaign_amended.py"


def _module():
    name = "blind_phase_campaign_evaluation_amended_test_module"
    spec = importlib.util.spec_from_file_location(name, SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


EVALUATE = _module()


def _self_hashed(schema: str, field: str, **values):
    document = {"schema_version": schema, **values}
    document[field] = EVALUATE.sha256_document(document)
    return document


def _identity(path: Path) -> dict[str, object]:
    payload = path.read_bytes()
    return {
        "path": str(path.absolute()),
        "size_bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }


def _write(path: Path, document: object) -> dict[str, object]:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, sort_keys=True) + "\n", encoding="utf-8")
    return _identity(path)


def _directory_identity(path: Path) -> dict[str, object]:
    status = path.lstat()
    return {
        "path": str(path.absolute()),
        "st_dev": status.st_dev,
        "st_ino": status.st_ino,
        "uid": status.st_uid,
        "gid": status.st_gid,
        "mode": stat.S_IMODE(status.st_mode),
    }


def test_compiled_contract_is_exact_and_reuses_frozen_scoring_engine() -> None:
    assert EVALUATE.FROZEN_SCORING is None
    assert EVALUATE.FROZEN_LAUNCHER is None
    assert EVALUATE.EXPECTED_FROZEN_EVALUATOR_SHA256 == (
        "b4b53a0aa8c9f4f0b45b6993e0084bfc35b1829199cc42554a35b14842770057"
    )
    assert EVALUATE.EXPECTED_FROZEN_LAUNCHER_SHA256 == (
        "af5d9fe1cc2f74e0b718fe9984a25e80a2579234030adc52ecfee754eb9e713a"
    )
    assert EVALUATE.EXPECTED_EXECUTION_PLAN_SHA256 == (
        "061e1c68acb34a04d766a3bd21d6d80bdee9fd14f484e378101264d81e23391d"
    )
    assert EVALUATE.EXPECTED_ACQUISITION_MANIFEST_SHA256 == (
        "f0da03513b23ff2aa9e5cd091ff0a32f4cb1a5581b6f637db7542f8edaea8dcf"
    )
    assert EVALUATE.EXPECTED_SOURCE_MANIFEST_SHA256 == (
        "742ba722dd065940666c376b8c52a626a57e943f69321b4da1825214369ced6b"
    )
    assert EVALUATE.EXPECTED_UNIT_COUNT == 6_168
    assert EVALUATE.SENSITIVITY_EXCLUSION == (4_491,)
    plan = json.loads(
        (ROOT / "reports/blind-phase-confirmatory-execution-plan-v4.json").read_text()
    )
    assert plan["provenance"]["campaign_execution_tools"]["evaluator"] == _identity(
        EVALUATE.FROZEN_EVALUATOR_PATH
    )


def test_complete_component_runtime_closure_artifact_is_exact() -> None:
    closure_path = ROOT / "reports/blind-phase-confirmatory-component-closure-v1.json"
    generator_path = (
        ROOT
        / "work/blind-phase-confirmatory-v2/build_component_closure_manifest.py"
    )
    closure, closure_identity = EVALUATE._artifact_json(
        closure_path, EVALUATE.MAXIMUM_JSON_BYTES
    )
    generator_identity = EVALUATE.regular_file_identity(generator_path)
    assert EVALUATE._validate_component_closure(
        closure,
        closure_identity=closure_identity,
        generator_identity=generator_identity,
    ) == closure


def test_activation_uses_only_exact_sealed_runtime_after_metadata_gates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime_root = tmp_path / "sealed-runtime"
    executable = runtime_root / "bin" / "python"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"sealed-python")
    runtime_site = runtime_root / "lib/python3.12/site-packages"
    runtime_site.mkdir(parents=True)
    target = _identity(executable)
    plan = {
        "provenance": {
            "candidate_runtime_lock": {
                "runtime_manifest": {
                    "environment": {
                        "runtime_closure": {
                            "runtime_root": str(runtime_root.absolute()),
                            "launcher": {
                                "requested_path": str(executable.absolute()),
                                "requested_is_symlink": False,
                                "target": target,
                            },
                        }
                    }
                }
            }
        }
    }
    candidate_identity = {
        "path": str(runtime_root.absolute()),
        "st_dev": 1,
        "st_ino": 2,
        "uid": 0,
        "gid": 0,
        "mode": 0o755,
        "recursive_entry_count": 3,
        "recursive_manifest_sha256": "a" * 64,
    }
    guard = {"protected_roots": {"candidate": candidate_identity}}
    monkeypatch.setattr(sys, "executable", str(executable.absolute()))
    monkeypatch.setattr(EVALUATE, "_isolated_runtime_mode", lambda: True)
    monkeypatch.setattr(
        EVALUATE,
        "_live_protected_root_identity",
        lambda path: {
            key: candidate_identity[key]
            for key in ("path", "st_dev", "st_ino", "uid", "gid", "mode")
        },
    )
    monkeypatch.setattr(EVALUATE, "FROZEN_SCORING", None)
    monkeypatch.setattr(EVALUATE, "FROZEN_LAUNCHER", None)
    monkeypatch.setattr(
        sys,
        "path",
        [str(ROOT), str(runtime_site), "/usr/lib/python3.12"],
    )
    for name in list(sys.modules):
        if name == "telemetry_yield" or name.startswith("telemetry_yield.") or name == "numpy" or name.startswith("numpy.") or name == "scipy" or name.startswith("scipy."):
            monkeypatch.delitem(sys.modules, name, raising=False)

    calls: list[str] = []

    def load(name: str, path: Path, expected_sha256: str):
        calls.append(name)
        if "scoring" in name:
            for dependency in (
                "telemetry_yield",
                "telemetry_yield.campaign_contract",
                "numpy",
                "scipy",
            ):
                module = types.ModuleType(dependency)
                module.__file__ = str(runtime_site / f"{dependency.replace('.', '/')}.py")
                monkeypatch.setitem(sys.modules, dependency, module)
        module = types.ModuleType(name)
        module.__file__ = str(path)
        return module

    monkeypatch.setattr(EVALUATE, "_load_module", load)
    EVALUATE._activate_frozen_dependencies(plan, guard)
    assert calls == [
        "blind_phase_frozen_scoring_for_amended_evaluator",
        "blind_phase_frozen_launcher_for_amended_evaluator",
    ]
    assert str(ROOT) not in sys.path
    assert str(runtime_site) in sys.path


def test_invalid_lock_fails_before_any_frozen_dependency_activation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scoring_identity = _identity(EVALUATE.FROZEN_EVALUATOR_PATH)
    launcher_identity = _identity(EVALUATE.FROZEN_LAUNCHER_PATH)
    documents = tmp_path / "metadata"
    plan_path = documents / "plan.json"
    evidence_path = documents / "evidence.json"
    plan = {
        "provenance": {
            "campaign_execution_tools": {
                "evaluator": scoring_identity,
                "launcher": launcher_identity,
            }
        }
    }
    _write(plan_path, plan)
    paths = {
        name: documents / f"{name}.json"
        for name in ("acquisition", "source", "amendment", "guard", "lock")
    }
    for path in paths.values():
        _write(path, {})
    evidence_identity = _write(evidence_path, {})
    generator_path = documents / "evidence-generator.py"
    component_closure_path = documents / "component-closure.json"
    component_closure_generator_path = documents / "component-closure-generator.py"
    amended_launcher_path = documents / "launcher.py"
    normalizer_path = documents / "normalizer.py"
    for path in (
        generator_path,
        component_closure_generator_path,
        amended_launcher_path,
        normalizer_path,
    ):
        path.write_text("# metadata-only test\n", encoding="utf-8")
    _write(component_closure_path, {})
    guard_document = {
        "protected_roots": {"candidate": {"path": str((tmp_path / "runtime").absolute())}},
        "control_parent": {"path": str((tmp_path / "control").absolute())},
        "campaign_results_parent": {"path": str((tmp_path / "results").absolute())},
    }
    _write(paths["guard"], guard_document)
    marker = tmp_path / "malicious-import-marker"

    monkeypatch.setattr(
        EVALUATE, "EXPECTED_NO_DECODER_EVIDENCE_V2_SHA256", evidence_identity["sha256"]
    )
    monkeypatch.setattr(EVALUATE, "_validate_plan_and_acquisition", lambda **kwargs: None)
    monkeypatch.setattr(
        EVALUATE, "_validate_frozen_source_manifest", lambda source, **kwargs: source
    )
    monkeypatch.setattr(
        EVALUATE,
        "_validate_no_decoder_evidence",
        lambda *args, **kwargs: {
            "evidence_payload_sha256": "8" * 64,
            "campaign_output_inventory": {},
        },
    )
    monkeypatch.setattr(EVALUATE, "_validate_amendment", lambda *args, **kwargs: {})
    monkeypatch.setattr(EVALUATE, "_validate_component_closure", lambda value, **kwargs: value)
    monkeypatch.setattr(EVALUATE, "_validate_runtime_guard", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        EVALUATE, "_validate_control_artifact_path", lambda *args, **kwargs: {}
    )
    monkeypatch.setattr(
        EVALUATE,
        "_validate_evaluator_lock",
        lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("invalid evaluator lock")),
    )
    monkeypatch.setattr(
        EVALUATE,
        "_activate_frozen_dependencies",
        lambda *args, **kwargs: marker.write_text("executed", encoding="utf-8"),
    )
    with pytest.raises(ValueError, match="invalid evaluator lock"):
        EVALUATE.evaluate_artifacts(
            execution_plan_path=plan_path,
            acquisition_manifest_path=paths["acquisition"],
            frozen_source_manifest_path=paths["source"],
            amendment_path=paths["amendment"],
            runtime_guard_path=paths["guard"],
            no_decoder_evidence_path=evidence_path,
            no_decoder_evidence_generator_path=generator_path,
            component_closure_path=component_closure_path,
            component_closure_generator_path=component_closure_generator_path,
            amended_launcher_path=amended_launcher_path,
            amended_normalizer_path=normalizer_path,
            evaluator_lock_path=paths["lock"],
            unit_manifest_path=tmp_path / "must-not-be-opened.json",
        )
    assert not marker.exists()


def test_full_and_mandatory_sensitivity_use_identical_frozen_scoring_parameters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observation_ids = [4_491, *range(5_001, 5_030)]
    mission_ids = observation_ids[:17]
    null_ids = [f"null-{index}" for index in range(11)]
    units = []
    records = []
    for observation_id in observation_ids:
        for repeat in ("repeat-a", "repeat-b"):
            for input_kind, null_id in [("signal", None), *(('null', value) for value in null_ids)]:
                for arm in ("component_a", "candidate"):
                    for rate in (1200, 4800, 9600, 19200):
                        unit = {
                            "observation_id": observation_id,
                            "arm": arm,
                            "repeat_id": repeat,
                            "input_kind": input_kind,
                            "null_id": null_id,
                            "baudrate": rate,
                        }
                        units.append(unit)
                        records.append({"unit": unit})
                if observation_id in mission_ids:
                    unit = {
                        "observation_id": observation_id,
                        "arm": "mission_satyaml",
                        "repeat_id": repeat,
                        "input_kind": input_kind,
                        "null_id": null_id,
                        "baudrate": None,
                    }
                    units.append(unit)
                    records.append({"unit": unit})
    assert len(units) == 6_168
    calls = []

    def scoring(**kwargs):
        calls.append(kwargs)
        return {"status": "complete", "call": len(calls)}

    monkeypatch.setattr(
        EVALUATE, "FROZEN_SCORING", types.SimpleNamespace(evaluate_campaign=scoring)
    )
    full, sensitivity = EVALUATE._evaluate_both_cohorts(
        expected_units=units,
        records=records,
        observation_ids=observation_ids,
        null_ids=null_ids,
        mission_observation_ids=mission_ids,
        plan_sha256="1" * 64,
        acquisition_sha256="2" * 64,
        normalizer_identity={"sha256": "3" * 64},
        far_null_ids=null_ids[1:],
        minimum_null_exposure_hours=30.0,
        maximum_null_rate_per_hour=0.1,
    )
    assert full == {"status": "complete", "call": 1}
    assert sensitivity == {"status": "complete", "call": 2}
    assert len(calls) == 2
    assert calls[0]["observation_ids"] == observation_ids
    assert calls[1]["observation_ids"] == observation_ids[1:]
    assert len(calls[1]["expected_units"]) < len(calls[0]["expected_units"])
    assert all(
        unit["observation_id"] != 4_491 for unit in calls[1]["expected_units"]
    )
    assert all(
        record["unit"]["observation_id"] != 4_491
        for record in calls[1]["records"]
    )
    for key in (
        "null_ids",
        "execution_plan_sha256",
        "acquisition_manifest_sha256",
        "normalizer_identity",
        "far_null_ids",
        "minimum_null_exposure_hours",
        "maximum_null_rate_per_hour",
    ):
        assert calls[1][key] == calls[0][key]


def test_sensitivity_fails_if_4491_is_not_the_only_removed_observation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        EVALUATE,
        "FROZEN_SCORING",
        types.SimpleNamespace(evaluate_campaign=lambda **kwargs: {"status": "complete"}),
    )
    with pytest.raises(ValueError, match="exact unexposed 29"):
        EVALUATE._evaluate_both_cohorts(
            expected_units=[{"observation_id": value} for value in range(1, 31)],
            records=[{"unit": {"observation_id": value}} for value in range(1, 31)],
            observation_ids=list(range(1, 31)),
            null_ids=["n"],
            mission_observation_ids=[],
            plan_sha256="1" * 64,
            acquisition_sha256="2" * 64,
            normalizer_identity={},
            far_null_ids=["n"],
            minimum_null_exposure_hours=0.0,
            maximum_null_rate_per_hour=1.0,
        )


def test_amendment_requires_exact_two_exposures_and_sensitivity_bindings() -> None:
    identities = {
        key: {"path": f"/{key}", "size_bytes": 1, "sha256": value * 64}
        for key, value in zip(
            (
                "plan",
                "acquisition",
                "source",
                "launcher",
                "normalizer",
                "evidence",
                "generator",
                "closure",
                "closure_generator",
            ),
            "123456789",
            strict=True,
        )
    }
    amendment = _self_hashed(
        EVALUATE.AMENDMENT_SCHEMA_VERSION,
        "amendment_payload_sha256",
        status="approved_after_acquisition_before_remaining_decoder_execution",
        campaign_pristine=False,
        execution_plan=identities["plan"],
        acquisition_manifest=identities["acquisition"],
        source_manifest=identities["source"],
        amended_normalizer=identities["normalizer"],
        component_runtime_closure=identities["closure"],
        component_runtime_closure_generator=identities["closure_generator"],
        amended_implementation={"launcher": identities["launcher"]},
        sensitivity_exclusion_observation_ids=[4491],
        schedule={
            "unit_count": 6168,
            "scientific_schedule_changed": False,
            "units_to_execute": 6166,
            "units_re_normalized_without_decoder_rerun": 2,
        },
        prior_decoder_exposures=[
            {
                "unit_id": value * 64,
                "observation_id": 4491,
                "decoder_rerun_permitted": False,
            }
            for value in "ab"
        ],
        sensitivity_analysis={
            "required": True,
            "exclude_observation_ids": [4491],
            "full_frozen_cohort_analysis_retained": True,
            "report_both_without_substitution": True,
        },
        no_decoder_execution_since_v1={
            "evidence": identities["evidence"],
            "evidence_generator": identities["generator"],
            "evidence_payload_sha256": "8" * 64,
            "unchanged_output_inventory": {"inventory_sha256": "9" * 64},
            "decoder_units_executed_since_v1": 0,
        },
        runtime_history_disclosure={
            "remaining_decoder_run_count_with_full_component_closure_bound": 6166,
            "prior_exposed_decoder_unit_count": 2,
            "prior_exposed_observation_ids": [4491],
            "full_component_closure_for_prior_exposures_cryptographically_established": False,
            "full_30_observation_cohort_runtime_history": "mixed_and_non_pristine",
            "full_30_observation_cohort_retained_with_disclosure": True,
            "mandatory_sensitivity_observation_count": 29,
            "mandatory_sensitivity_excludes_entire_observation_ids": [4491],
        },
        operational_scope={
            "retained_v1_execution_order": ["all_signals", "all_nulls"],
            "only_additional_changes": list(EVALUATE.EXPECTED_OPERATIONAL_CHANGES),
            "normalizer_or_scoring_changed": True,
            "scientific_normalization_or_scoring_changed": False,
            "scoring_changed": False,
            "normalizer_implementation_changed_for_bookkeeping_only_projection_fix": True,
            "execution_plan_changed": False,
            "selection_changed": False,
            "candidate_config_changed": False,
            "decoder_changed": False,
            "scientific_unit_set_changed": False,
            "scientific_hypotheses_changed": False,
        },
        determinism_projection_fix={
            "status": "approved_pre_outcome",
            "defect_in_v1": EVALUATE.DETERMINISM_V1_DEFECT,
            "normalizer_v2": identities["normalizer"],
            "hashes_retained_in_provenance": list(EVALUATE.DETERMINISM_BOOKKEEPING_FIELDS),
            "hashes_removed_only_from_semantic_receiver_projection": list(EVALUATE.DETERMINISM_BOOKKEEPING_FIELDS),
            "frozen_endpoint_changed": False,
            "frozen_metric_changed": False,
            "selection_changed": False,
            "scoring_changed": False,
            "required_synthetic_tests": {
                "equal_semantic_output_different_bookkeeping": "PASS",
                "true_receiver_output_difference": "FAIL_REPEAT_GATE",
            },
            "applied_before_any_remaining_confirmatory_outcome": True,
        },
    )
    assert EVALUATE._validate_amendment(
        amendment,
        plan_identity=identities["plan"],
        acquisition_identity=identities["acquisition"],
        source_identity=identities["source"],
        launcher_identity=identities["launcher"],
        normalizer_identity=identities["normalizer"],
        no_decoder_evidence_identity=identities["evidence"],
        no_decoder_generator_identity=identities["generator"],
        no_decoder_evidence_payload_sha256="8" * 64,
        no_decoder_output_inventory={"inventory_sha256": "9" * 64},
        component_closure_identity=identities["closure"],
        component_closure_generator_identity=identities["closure_generator"],
    ) == amendment
    changed = json.loads(json.dumps(amendment))
    changed["sensitivity_analysis"]["exclude_observation_ids"] = []
    changed["amendment_payload_sha256"] = EVALUATE.sha256_document(
        {key: value for key, value in changed.items() if key != "amendment_payload_sha256"}
    )
    with pytest.raises(ValueError, match="required analysis contract"):
        EVALUATE._validate_amendment(
            changed,
            plan_identity=identities["plan"],
            acquisition_identity=identities["acquisition"],
            source_identity=identities["source"],
            launcher_identity=identities["launcher"],
            normalizer_identity=identities["normalizer"],
            no_decoder_evidence_identity=identities["evidence"],
            no_decoder_generator_identity=identities["generator"],
            no_decoder_evidence_payload_sha256="8" * 64,
            no_decoder_output_inventory={"inventory_sha256": "9" * 64},
            component_closure_identity=identities["closure"],
            component_closure_generator_identity=identities["closure_generator"],
        )
    overclaimed = json.loads(json.dumps(amendment))
    overclaimed["runtime_history_disclosure"][
        "full_component_closure_for_prior_exposures_cryptographically_established"
    ] = True
    overclaimed["amendment_payload_sha256"] = EVALUATE.sha256_document(
        {
            key: value
            for key, value in overclaimed.items()
            if key != "amendment_payload_sha256"
        }
    )
    with pytest.raises(ValueError, match="required analysis contract"):
        EVALUATE._validate_amendment(
            overclaimed,
            plan_identity=identities["plan"],
            acquisition_identity=identities["acquisition"],
            source_identity=identities["source"],
            launcher_identity=identities["launcher"],
            normalizer_identity=identities["normalizer"],
            no_decoder_evidence_identity=identities["evidence"],
            no_decoder_generator_identity=identities["generator"],
            no_decoder_evidence_payload_sha256="8" * 64,
            no_decoder_output_inventory={"inventory_sha256": "9" * 64},
            component_closure_identity=identities["closure"],
            component_closure_generator_identity=identities["closure_generator"],
        )


def test_evaluator_lock_is_self_hashed_and_exactly_binds_all_inputs() -> None:
    identities = {
        key: {"path": f"/{key}", "size_bytes": 1, "sha256": value * 64}
        for key, value in zip(
            (
                "execution_plan",
                "acquisition_manifest",
                "source_manifest",
                "amendment",
                "runtime_guard",
                "amended_launcher",
                "amended_normalizer",
                "frozen_scoring_evaluator",
                "frozen_schedule_launcher",
                "amended_evaluator",
                "sealed_candidate_runtime",
                "component_runtime_closure",
                "component_runtime_closure_generator",
            ),
            "123456789abcd",
            strict=True,
        )
    }
    contract = {
        "scientific_unit_count": 6168,
        "full_cohort_observation_count": 30,
        "mandatory_sensitivity_observation_count": 29,
        "sensitivity_exclusion_observation_ids": [4491],
        "primary_endpoint": "component_a_plus_candidate_trusted_native_union_increment_over_component_a",
        "secondary_endpoints_unchanged_from_frozen_evaluator": True,
        "frozen_bootstrap_resamples": EVALUATE.FROZEN_BOOTSTRAP_RESAMPLES,
        "frozen_bootstrap_seed": EVALUATE.FROZEN_BOOTSTRAP_SEED,
        "blinded_arm_labels": "validate_if_present",
        "normalizer_v2_semantic_projection_excludes": list(
            EVALUATE.DETERMINISM_BOOKKEEPING_FIELDS
        ),
    }
    dependencies = {
        key: identities[key]
        for key in (
            "amended_evaluator",
            "frozen_scoring_evaluator",
            "frozen_schedule_launcher",
            "sealed_candidate_runtime",
        )
    }
    lock = _self_hashed(
        EVALUATE.LOCK_SCHEMA_VERSION,
        "evaluator_lock_payload_sha256",
        status="frozen_before_campaign_outcome_evaluation",
        **identities,
        executed_local_python_dependencies=dependencies,
        analysis_contract=contract,
    )
    assert EVALUATE._validate_evaluator_lock(lock, identities=identities) == lock
    changed = json.loads(json.dumps(lock))
    changed["amended_normalizer"]["sha256"] = "f" * 64
    changed["evaluator_lock_payload_sha256"] = EVALUATE.sha256_document(
        {key: value for key, value in changed.items() if key != "evaluator_lock_payload_sha256"}
    )
    with pytest.raises(ValueError, match="amended_normalizer"):
        EVALUATE._validate_evaluator_lock(changed, identities=identities)


def test_runtime_guard_binds_closed_control_copy_and_separated_results() -> None:
    identities = {
        key: {"path": f"/{key}", "size_bytes": 1, "sha256": value * 64}
        for key, value in zip(
            (
                "plan",
                "acquisition",
                "source",
                "amendment",
                "builder",
                "launcher",
                "closure",
                "closure_generator",
            ),
            "12345678",
            strict=True,
        )
    }
    control = {
        "path": "/control",
        "st_dev": 1,
        "st_ino": 2,
        "uid": 0,
        "gid": 0,
        "mode": 0o755,
    }
    results = {
        "path": "/results",
        "st_dev": 1,
        "st_ino": 3,
        "uid": 1000,
        "gid": 1000,
        "mode": 0o700,
    }
    copy = {**identities["builder"], "path": "/control/build_runtime_guard_v2.py"}
    dependencies = {"launcher_v2": identities["launcher"]}
    amendment = {
        "component_runtime_closure": identities["closure"],
        "component_runtime_closure_generator": identities["closure_generator"],
        "amended_implementation": {
            "runtime_guard_builder": identities["builder"],
            "transitive_code_dependencies": dependencies,
        }
    }
    guard = _self_hashed(
        EVALUATE.RUNTIME_GUARD_SCHEMA_VERSION,
        "runtime_guard_payload_sha256",
        status="active",
        execution_plan=identities["plan"],
        acquisition_manifest=identities["acquisition"],
        source_manifest=identities["source"],
        amendment=identities["amendment"],
        runtime_guard_builder=identities["builder"],
        runtime_guard_builder_control_copy=copy,
        transitive_code_dependencies=dependencies,
        control_parent=control,
        campaign_results_parent=results,
        seal_window_closed=True,
        component_runtime_closure=identities["closure"],
        component_runtime_closure_generator=identities["closure_generator"],
        component_runtime_closure_validation={
            "content_manifest_sha256": EVALUATE.EXPECTED_COMPONENT_CLOSURE_CONTENT_SHA256,
            "preseal_raw_manifest_exact": True,
            "sealed_semantic_manifest_exact": True,
            "pre_campaign_semantic_manifest_exact": True,
            "post_campaign_semantic_manifest_required": True,
        },
        component_runtime_python_startup={
            "bubblewrap_clearenv": True,
            "python_no_user_site": "1",
            "script_directory_inside_readonly_runtime_mount": "/runtime",
            "all_interpreter_library_paths_inside_component_runtime": True,
        },
    )
    assert EVALUATE._validate_runtime_guard(
        guard,
        plan_identity=identities["plan"],
        acquisition_identity=identities["acquisition"],
        source_identity=identities["source"],
        amendment_identity=identities["amendment"],
        amendment=amendment,
    ) == guard
    changed = json.loads(json.dumps(guard))
    changed["runtime_guard_builder_control_copy"]["sha256"] = "f" * 64
    changed["runtime_guard_payload_sha256"] = EVALUATE.sha256_document(
        {key: value for key, value in changed.items() if key != "runtime_guard_payload_sha256"}
    )
    with pytest.raises(ValueError, match="closed root-control lifecycle"):
        EVALUATE._validate_runtime_guard(
            changed,
            plan_identity=identities["plan"],
            acquisition_identity=identities["acquisition"],
            source_identity=identities["source"],
            amendment_identity=identities["amendment"],
            amendment=amendment,
        )


def test_control_artifacts_require_exact_root_control_namespace_and_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    control = tmp_path / "control"
    control.mkdir(mode=0o755)
    artifact = control / "evaluator-lock.json"
    artifact.write_text('{"status":"frozen"}\n', encoding="utf-8")
    artifact.chmod(0o444)
    actual_parent = _directory_identity(control)
    guarded_parent = {**actual_parent, "uid": 0, "gid": 0, "mode": 0o755}

    monkeypatch.setattr(
        EVALUATE,
        "_live_directory_identity",
        lambda path: dict(guarded_parent),
    )

    def root_file(path: Path) -> dict[str, object]:
        status = path.lstat()
        return {
            "path": str(path.absolute()),
            "st_dev": status.st_dev,
            "st_ino": status.st_ino,
            "uid": 0,
            "gid": 0,
            "mode": stat.S_IMODE(status.st_mode),
        }

    monkeypatch.setattr(EVALUATE, "_live_control_artifact_identity", root_file)
    assert EVALUATE._validate_control_artifact_path(artifact, guarded_parent)[
        "path"
    ] == str(artifact.absolute())

    copied = tmp_path / "copied-lock.json"
    copied.write_bytes(artifact.read_bytes())
    copied.chmod(0o444)
    with pytest.raises(ValueError, match="direct child"):
        EVALUATE._validate_control_artifact_path(copied, guarded_parent)

    def wrong_owner(path: Path) -> dict[str, object]:
        return {**root_file(path), "uid": 1000}

    monkeypatch.setattr(EVALUATE, "_live_control_artifact_identity", wrong_owner)
    with pytest.raises(ValueError, match="root:root"):
        EVALUATE._validate_control_artifact_path(artifact, guarded_parent)

    def wrong_mode(path: Path) -> dict[str, object]:
        return {**root_file(path), "mode": 0o644}

    monkeypatch.setattr(EVALUATE, "_live_control_artifact_identity", wrong_mode)
    with pytest.raises(ValueError, match="mode 0444"):
        EVALUATE._validate_control_artifact_path(artifact, guarded_parent)

    alias = tmp_path / "control-alias"
    alias.symlink_to(control, target_is_directory=True)
    alias_parent = {**guarded_parent, "path": str(alias.absolute())}
    with pytest.raises(ValueError, match="live identity"):
        EVALUATE._validate_control_artifact_path(
            alias / artifact.name, alias_parent
        )


def test_campaign_namespace_rejects_outside_symlink_wrong_mode_and_wrong_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    results_parent = tmp_path / "results"
    results_parent.mkdir(mode=0o700)
    output = results_parent / "campaign-run"
    output.mkdir(mode=0o700)
    manifest = output / "unit-manifest-amended.json"
    guarded_parent = _directory_identity(results_parent)
    assert EVALUATE._validate_campaign_namespace(manifest, guarded_parent) == output

    outside_parent = tmp_path / "outside"
    outside_parent.mkdir(mode=0o700)
    outside = outside_parent / "campaign-run"
    outside.mkdir(mode=0o700)
    with pytest.raises(ValueError, match="exact UID/GID 1000"):
        EVALUATE._validate_campaign_namespace(
            outside / "unit-manifest-amended.json", guarded_parent
        )

    output.chmod(0o755)
    with pytest.raises(ValueError, match="mode 0700"):
        EVALUATE._validate_campaign_namespace(manifest, guarded_parent)
    output.chmod(0o700)

    link = results_parent / "linked-run"
    link.symlink_to(output, target_is_directory=True)
    with pytest.raises(ValueError, match="not a real directory"):
        EVALUATE._validate_campaign_namespace(
            link / "unit-manifest-amended.json", guarded_parent
        )

    live = EVALUATE._live_directory_identity

    def wrong_owner(path: Path) -> dict[str, object]:
        identity = live(path)
        if path.absolute() == output.absolute():
            identity["uid"] = 999
        return identity

    monkeypatch.setattr(EVALUATE, "_live_directory_identity", wrong_owner)
    with pytest.raises(ValueError, match="UID/GID 1000"):
        EVALUATE._validate_campaign_namespace(manifest, guarded_parent)


def test_result_tree_namespace_rejects_symlinked_ancestor_directories(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(EVALUATE, "EXPECTED_UNIT_COUNT", 1)
    output = tmp_path / "campaign-run"
    output.mkdir(mode=0o700)
    outside = tmp_path / "outside"
    outside.mkdir(mode=0o700)
    (output / "null-ledgers").mkdir(mode=0o700)

    (output / "units").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="not a real directory"):
        EVALUATE._validate_campaign_result_tree_namespace(output)
    (output / "units").unlink()

    units = output / "units"
    units.mkdir(mode=0o700)
    (units / "unit-a").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="not a real directory"):
        EVALUATE._validate_campaign_result_tree_namespace(output)
    (units / "unit-a").unlink()
    (units / "unit-a").mkdir(mode=0o700)

    (output / "null-ledgers").rmdir()
    (output / "null-ledgers").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="not a real directory"):
        EVALUATE._validate_campaign_result_tree_namespace(output)


def test_result_tree_namespace_binds_exact_safe_unit_directory_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(EVALUATE, "EXPECTED_UNIT_COUNT", 2)
    output = tmp_path / "campaign-run"
    units = output / "units"
    ledgers = output / "null-ledgers"
    units.mkdir(parents=True, mode=0o700)
    ledgers.mkdir(mode=0o700)
    for unit_id in ("unit-a", "unit-b"):
        (units / unit_id).mkdir(mode=0o700)
    topology = EVALUATE._validate_campaign_result_tree_namespace(output)
    assert set(topology["unit_directories"]) == {"unit-a", "unit-b"}

    relocated_units = output / "relocated-units"
    units.rename(relocated_units)
    units.symlink_to(relocated_units, target_is_directory=True)
    with pytest.raises(ValueError, match="unit result namespace changed"):
        EVALUATE._revalidate_result_tree_ancestor_chain(
            output, topology, unit_id="unit-a"
        )
    units.unlink()
    relocated_units.rename(units)

    (units / "unit-b").chmod(0o755)
    with pytest.raises(ValueError, match="mode 0700"):
        EVALUATE._validate_campaign_result_tree_namespace(output)


def test_result_tree_namespace_rejects_special_null_ledger_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(EVALUATE, "EXPECTED_UNIT_COUNT", 1)
    output = tmp_path / "campaign-run"
    unit = output / "units" / "unit-a"
    ledger_root = output / "null-ledgers"
    unit.mkdir(parents=True, mode=0o700)
    (output / "units").chmod(0o700)
    ledger_root.mkdir(mode=0o700)
    os.mkfifo(ledger_root / "malicious-ledger.json")
    with pytest.raises(ValueError, match="special entry"):
        EVALUATE._validate_campaign_result_tree_namespace(output)


def test_no_decoder_evidence_requires_exact_zero_new_output_contract() -> None:
    generator = {
        "path": "/generator.py",
        "size_bytes": 1,
        "sha256": EVALUATE.EXPECTED_NO_DECODER_EVIDENCE_GENERATOR_SHA256,
    }
    inventory = {
        "root": "/old-output",
        "directory_count": 7,
        "file_count": 6,
        "exposed_decoder_unit_count": 2,
        "new_decoder_unit_count": 0,
        "files": {
            f"file-{index}.json": {
                "path": f"/old-output/file-{index}.json",
                "size_bytes": 1,
                "sha256": f"{index + 2:x}" * 64,
            }
            for index in range(6)
        },
    }
    inventory["inventory_sha256"] = EVALUATE.sha256_document(inventory)
    evidence = _self_hashed(
        EVALUATE.NO_DECODER_EVIDENCE_SCHEMA_VERSION,
        "evidence_payload_sha256",
        status="PASS",
        generated_at_utc="2026-09-04T00:00:00Z",
        period_start_utc="2026-09-03T00:00:00Z",
        supersedes_evidence_v1={
            "evidence": {
                "path": "/evidence-v1.json",
                "size_bytes": 1,
                "sha256": EVALUATE.EXPECTED_SUPERSEDED_NO_DECODER_EVIDENCE_V1_SHA256,
            },
            "reason": "parser hardening",
            "v1_artifact_modified": False,
        },
        superseded_amendment={"sha256": "a" * 64},
        superseded_amendment_timestamp={"sha256": "b" * 64},
        generator=generator,
        campaign_output_inventory=inventory,
        checks={
            "rfc3161_timestamp_verified": True,
            "timestamp_message_imprint_is_amendment_v1_sha256": True,
            "exact_existing_decoder_unit_count": 2,
            "exact_existing_decoder_file_count": 6,
            "exact_existing_raw_result_count": 2,
            "exact_existing_normalized_result_count": 2,
            "exact_existing_process_receipt_count": 2,
            "new_decoder_unit_count_since_timestamp": 0,
            "new_raw_result_count_since_timestamp": 0,
            "new_normalized_result_count_since_timestamp": 0,
            "new_process_receipt_count_since_timestamp": 0,
            "existing_six_files_sha256_and_size_unchanged": True,
            "existing_six_files_mtime_before_timestamp": True,
            "output_tree_symlink_count": 0,
            "output_tree_special_file_count": 0,
            "selected_iq_opened_by_generator": False,
            "candidate_runtime_opened_by_generator": False,
        },
        claim_scope={
            "mechanically_establishes": [
                "exactly two pre-v1 exposed unit directories and their six immutable files remain",
                "zero additional raw-result.json, normalized-result.json, or process-receipt.json files are materialized",
                "all six existing output files predate the verified RFC3161 timestamp",
            ],
            "independent_review_still_required_for": [
                "absence of an unlogged decoder process that emitted neither a receipt nor an output"
            ],
        },
    )
    assert EVALUATE._validate_no_decoder_evidence(
        evidence, generator_identity=generator
    ) == evidence
    changed = json.loads(json.dumps(evidence))
    changed["checks"]["new_raw_result_count_since_timestamp"] = 1
    changed["evidence_payload_sha256"] = EVALUATE.sha256_document(
        {key: value for key, value in changed.items() if key != "evidence_payload_sha256"}
    )
    with pytest.raises(ValueError, match="exact PASS"):
        EVALUATE._validate_no_decoder_evidence(
            changed, generator_identity=generator
        )


def test_verified_loader_rejects_dependency_mutation_during_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dependency = tmp_path / "dependency.py"
    dependency.write_bytes(b"VALUE = 1\n")
    expected = hashlib.sha256(dependency.read_bytes()).hexdigest()
    original_read = EVALUATE.os.read
    changed = False

    def racing_read(descriptor: int, count: int) -> bytes:
        nonlocal changed
        block = original_read(descriptor, count)
        if block and not changed:
            changed = True
            dependency.write_bytes(b"VALUE = 2\n")
        return block

    monkeypatch.setattr(EVALUATE.os, "read", racing_read)
    with pytest.raises(RuntimeError, match="identity mismatch"):
        EVALUATE._load_module("mutating_evaluator_dependency", dependency, expected)


def test_verified_loader_hashes_before_execution(tmp_path: Path) -> None:
    marker = tmp_path / "executed"
    dependency = tmp_path / "untrusted.py"
    dependency.write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).write_text('bad')\n",
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="identity mismatch"):
        EVALUATE._load_module("untrusted_evaluator_dependency", dependency, "0" * 64)
    assert not marker.exists()


def test_optional_blinded_labels_are_validated_if_present() -> None:
    assert EVALUATE._optional_blinded_labels({}) == {
        "present": False,
        "validated": True,
    }
    labels = {
        "component_a": "arm-7",
        "candidate": "arm-2",
        "mission_satyaml": "arm-9",
    }
    result = EVALUATE._optional_blinded_labels({"blinded_arm_labels": labels})
    assert result["present"] is True
    assert result["mapping_sha256"] == EVALUATE.sha256_document(labels)
    with pytest.raises(ValueError, match="non-bijective"):
        EVALUATE._optional_blinded_labels(
            {"blinded_arm_labels": {key: "same" for key in labels}}
        )


def test_frozen_repeat_gate_ignores_provenance_hash_drift_but_detects_count_drift() -> None:
    scoring = EVALUATE._load_module(
        "blind_phase_frozen_scoring_for_repeat_test",
        EVALUATE.FROZEN_EVALUATOR_PATH,
        EVALUATE.EXPECTED_FROZEN_EVALUATOR_SHA256,
    )
    validated = []
    normalizer = {"path": "/normalizer-v2.py", "size_bytes": 1, "sha256": "9" * 64}
    for repeat, receipt, raw in (
        ("repeat-a", "1" * 64, "2" * 64),
        ("repeat-b", "3" * 64, "4" * 64),
    ):
        unit = scoring.build_unit(
            {
                    "schema_version": scoring.UNIT_SCHEMA_VERSION,
                    "observation_id": 4492,
                    "satellite_id": "SAT",
                    "arm": "candidate",
                    "baudrate": 1200,
                    "input_kind": "signal",
                    "null_id": None,
                    "repeat_id": repeat,
                    "source": {"size_bytes": 400, "sha256": "8" * 64},
            }
        )
        receiver = {
            "process_outcome": {
                "return_code": 0,
                "inherited_pipe_failure_detected": False,
                "process_tree_cleanup_passed": True,
                "stdout": {"size_bytes": 0, "sha256": hashlib.sha256(b"").hexdigest()},
                "stderr": {"size_bytes": 0, "sha256": hashlib.sha256(b"").hexdigest()},
            },
            "receiver": {"config_sha256": "5" * 64, "counts": {"detections": 1}},
        }
        record = {
            "schema_version": scoring.RESULT_SCHEMA_VERSION,
            "status": "completed",
            "unit": unit,
            "process_tree_cleanup_passed": True,
            "receiver_projection": receiver,
            "trusted_native": [
                {
                    "protocol_id": "ax25",
                    "payload_hex": "0102",
                    "payload_sha256": hashlib.sha256(b"\x01\x02").hexdigest(),
                    "admission": "trusted_native",
                }
            ],
            "untrusted_diagnostic": [],
            "provenance": {
                "execution_plan_sha256": "6" * 64,
                "acquisition_manifest_sha256": "7" * 64,
                "normalizer": normalizer,
                "process_receipt_payload_sha256": receipt,
                "process_receipt_sha256": receipt,
                "raw_result": {"path": f"/{repeat}.json", "size_bytes": 1, "sha256": raw},
                "raw_result_payload_sha256": raw,
            },
        }
        projection = {
            "status": "completed",
            "receiver": receiver,
            "trusted_native": [("ax25", "0102")],
            "untrusted_diagnostic": [],
        }
        record["determinism_projection_sha256"] = scoring._sha256_document(projection)
        record["normalized_result_payload_sha256"] = scoring._sha256_document(record)
        EVALUATE._validate_v2_semantic_projection(record)
        validated.append(
            scoring._validated_record(
                record,
                unit,
                execution_plan_sha256="6" * 64,
                acquisition_manifest_sha256="7" * 64,
                normalizer_identity=normalizer,
            )
        )
    assert validated[0]["determinism_projection"] == validated[1]["determinism_projection"]

    drifted = json.loads(json.dumps(validated[1]["determinism_projection"]))
    drifted["receiver"]["receiver"]["counts"]["detections"] = 2
    assert validated[0]["determinism_projection"] != drifted


def test_source_ledger_requires_exact_signal_and_null_transform_closure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(EVALUATE, "EXPECTED_OBSERVATION_COUNT", 2)
    monkeypatch.setattr(EVALUATE, "EXPECTED_NULL_COUNT", 2)
    controls = [
        {"kind": "all_zero", "seed": None},
        {"kind": "phase_scramble", "seed": 7},
    ]
    signals = []
    sources = []
    for observation_id in (4491, 4492):
        signal = {
            "observation_id": observation_id,
            "satellite_id": f"SAT-{observation_id}",
            "input_kind": "signal",
            "null_id": None,
            "path": f"/iq/{observation_id}.iq",
            "size_bytes": 400,
            "sha256": hashlib.sha256(str(observation_id).encode()).hexdigest(),
            "duration_seconds": 400 / (4 * 57_600),
            "far_denominator": False,
        }
        signals.append(signal)
        sources.append(signal)
        for control in controls:
            null_id = EVALUATE._null_id(control)
            output_sha = hashlib.sha256(f"{observation_id}:{null_id}".encode()).hexdigest()
            path = f"/ephemeral/{observation_id}-{null_id}.ci16"
            sources.append(
                {
                    "observation_id": observation_id,
                    "satellite_id": signal["satellite_id"],
                    "input_kind": "null",
                    "null_id": null_id,
                    "path": path,
                    "size_bytes": 400,
                    "sha256": output_sha,
                    "duration_seconds": signal["duration_seconds"],
                    "far_denominator": control["kind"] != "all_zero",
                    "ephemeral_removed_after_terminal_coverage": True,
                    "transform": {
                        "transform_version": EVALUATE.NULL_TRANSFORM_VERSION,
                        "kind": control["kind"],
                        "seed": control["seed"],
                        "source_path": signal["path"],
                        "source_size_bytes": 400,
                        "source_sha256": signal["sha256"],
                        "output_path": path,
                        "output_size_bytes": 400,
                        "output_sha256": output_sha,
                        "chunk_complex_samples": 32,
                        "peak_payload_bytes": 128,
                    },
                }
            )
    ledger = _self_hashed(
        EVALUATE.SOURCE_MANIFEST_SCHEMA_VERSION,
        "source_manifest_payload_sha256",
        status="complete",
        execution_plan={"sha256": "1" * 64},
        acquisition_manifest={"sha256": "2" * 64},
        amendment={"sha256": "3" * 64},
        runtime_guard={"sha256": "4" * 64},
        sources=sources,
        counts={
            "observation_count": 2,
            "source_count": 6,
            "maximum_simultaneously_materialized_null_files": 1,
        },
    )
    assert len(
        EVALUATE._validate_source_ledger(
            ledger,
            frozen_source={
                "signals": signals,
                "controls_per_observation": controls,
            },
            plan={"null_controls": {"chunk_complex_samples": 32}},
            plan_identity={"sha256": "1" * 64},
            acquisition_identity={"sha256": "2" * 64},
            amendment_identity={"sha256": "3" * 64},
            guard_identity={"sha256": "4" * 64},
        )
    ) == 6
    changed = json.loads(json.dumps(ledger))
    changed["sources"][1]["transform"]["output_sha256"] = "f" * 64
    changed["source_manifest_payload_sha256"] = EVALUATE.sha256_document(
        {key: value for key, value in changed.items() if key != "source_manifest_payload_sha256"}
    )
    with pytest.raises(ValueError, match="content-bound"):
        EVALUATE._validate_source_ledger(
            changed,
            frozen_source={
                "signals": signals,
                "controls_per_observation": controls,
            },
            plan={"null_controls": {"chunk_complex_samples": 32}},
            plan_identity={"sha256": "1" * 64},
            acquisition_identity={"sha256": "2" * 64},
            amendment_identity={"sha256": "3" * 64},
            guard_identity={"sha256": "4" * 64},
        )


def test_artifact_reader_rejects_self_hash_tamper_and_publish_is_no_clobber(
    tmp_path: Path,
) -> None:
    path = tmp_path / "document.json"
    document = _self_hashed("schema-v1", "payload_sha256", value=1)
    _write(path, document)
    loaded, identity = EVALUATE._artifact_json(path)
    assert loaded == document
    assert identity == _identity(path)
    assert EVALUATE._verified_self_hash(
        loaded, schema="schema-v1", field="payload_sha256"
    ) == document
    loaded["value"] = 2
    with pytest.raises(ValueError, match="self-hash"):
        EVALUATE._verified_self_hash(
            loaded, schema="schema-v1", field="payload_sha256"
        )

    output = tmp_path / "evaluation.json"
    EVALUATE._publish(output, {"status": "complete"})
    assert output.stat().st_mode & 0o777 == 0o444
    with pytest.raises(ValueError, match="already exists"):
        EVALUATE._publish(output, {"status": "changed"})


def test_artifact_reader_parses_the_same_fd_bytes_during_aba_path_swap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "document.json"
    original = {"selected": "original"}
    malicious = {"selected": "swapped"}
    _write(path, original)
    original_identity = _identity(path)
    backup = tmp_path / "document.original"
    real_read = EVALUATE.os.read
    swapped = False

    def aba_read(descriptor: int, count: int) -> bytes:
        nonlocal swapped
        block = real_read(descriptor, count)
        if block and not swapped:
            swapped = True
            path.rename(backup)
            _write(path, malicious)
            path.unlink()
            backup.rename(path)
        return block

    monkeypatch.setattr(EVALUATE.os, "read", aba_read)
    parsed = False
    real_loads = EVALUATE.json.loads

    def tracked_loads(*args, **kwargs):
        nonlocal parsed
        parsed = True
        return real_loads(*args, **kwargs)

    monkeypatch.setattr(EVALUATE.json, "loads", tracked_loads)
    try:
        loaded, identity = EVALUATE._artifact_json(path)
    except ValueError as error:
        assert "changed while reading" in str(error)
        assert parsed is False
    else:
        assert parsed is True
        assert loaded == original
        assert identity == original_identity
    assert swapped is True
    assert _identity(path) == original_identity


def test_artifact_reader_fails_closed_when_path_swap_remains(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "document.json"
    _write(path, {"selected": "original"})
    backup = tmp_path / "document.original"
    real_read = EVALUATE.os.read
    swapped = False

    def swapped_read(descriptor: int, count: int) -> bytes:
        nonlocal swapped
        block = real_read(descriptor, count)
        if block and not swapped:
            swapped = True
            path.rename(backup)
            _write(path, {"selected": "swapped"})
        return block

    monkeypatch.setattr(EVALUATE.os, "read", swapped_read)
    with pytest.raises(ValueError, match="changed while reading"):
        EVALUATE._artifact_json(path)
    assert swapped is True


def test_synthetic_artifact_namespace_is_consumed_end_to_end_and_path_tamper_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(EVALUATE, "EXPECTED_UNIT_COUNT", 2)
    monkeypatch.setattr(EVALUATE, "EXPECTED_OBSERVATION_COUNT", 2)
    monkeypatch.setattr(EVALUATE, "EXPECTED_MISSION_OBSERVATION_COUNT", 1)
    monkeypatch.setattr(EVALUATE, "EXPECTED_NULL_COUNT", 1)
    scoring_identity = _identity(EVALUATE.FROZEN_EVALUATOR_PATH)
    frozen_launcher_identity = _identity(EVALUATE.FROZEN_LAUNCHER_PATH)
    plan = {
        "provenance": {
            "campaign_execution_tools": {
                "evaluator": scoring_identity,
                "launcher": frozen_launcher_identity,
            }
        },
        "routing": {
            "routes": [
                {"observation_id": 4491, "profile_routable": True},
                {"observation_id": 4492, "profile_routable": False},
            ]
        },
        "null_controls": {
            "controls_per_observation": [{"kind": "all_zero", "seed": None}],
            "minimum_exposure_hours": 30.0,
            "maximum_rate_per_hour": 0.1,
        },
    }
    inputs = tmp_path / "inputs"
    plan_path = inputs / "plan.json"
    acquisition_path = inputs / "acquisition.json"
    source_path = inputs / "source.json"
    amendment_path = inputs / "amendment.json"
    guard_path = inputs / "guard.json"
    evidence_path = inputs / "no-decoder-evidence.json"
    evidence_generator_path = inputs / "no-decoder-evidence-generator.py"
    component_closure_path = inputs / "component-closure.json"
    component_closure_generator_path = inputs / "component-closure-generator.py"
    lock_path = inputs / "lock.json"
    plan_identity = _write(plan_path, plan)
    acquisition_identity = _write(acquisition_path, {})
    source_identity = _write(source_path, {})
    amendment_identity = _write(amendment_path, {})
    guard_identity = _write(
        guard_path,
            {
                "protected_roots": {
                    "candidate": {"path": str((tmp_path / "candidate-runtime").absolute())}
                },
                "control_parent": {"path": str((tmp_path / "control").absolute())},
                "campaign_results_parent": _directory_identity(tmp_path),
            },
    )
    evidence_identity = _write(evidence_path, {})
    _write(evidence_generator_path, {"implementation": "generator"})
    _write(component_closure_path, {})
    _write(component_closure_generator_path, {"implementation": "closure-generator"})
    lock_identity = _write(lock_path, {})
    launcher_path = inputs / "launcher-v2.py"
    normalizer_path = inputs / "normalizer-v2.py"
    launcher_identity = _write(launcher_path, {"implementation": "launcher"})
    normalizer_identity = _write(normalizer_path, {"implementation": "normalizer"})

    units = [
        {"unit_id": "a" * 64, "observation_id": 4491},
        {"unit_id": "b" * 64, "observation_id": 4492},
    ]
    output = tmp_path / "campaign"
    output.mkdir(mode=0o700)
    result_identities = []
    for unit in units:
        result_identities.append(
            _write(
                output / "units" / unit["unit_id"] / "normalized-result.json",
                {
                    "unit": unit,
                    "process_tree_cleanup_passed": True,
                    "receiver_projection": {
                        "process_outcome": {
                            "return_code": 0,
                            "inherited_pipe_failure_detected": False,
                            "process_tree_cleanup_passed": True,
                            "stdout": {"size_bytes": 0, "sha256": hashlib.sha256(b"").hexdigest()},
                            "stderr": {"size_bytes": 0, "sha256": hashlib.sha256(b"").hexdigest()},
                        }
                    },
                    "provenance": {
                        "process_receipt_payload_sha256": "1" * 64,
                        "process_receipt_sha256": "1" * 64,
                        "raw_result": None,
                    },
                },
            )
        )
    (output / "units").chmod(0o700)
    for unit in units:
        (output / "units" / str(unit["unit_id"])).chmod(0o700)
    (output / "null-ledgers").mkdir(mode=0o700)
    source_ledger_identity = _write(output / "source-ledger.json", {})
    _write(output / "amended-output-binding.json", {})
    exposures = [
        {"unit_id": "c" * 64, "observation_id": 4491},
        {"unit_id": "d" * 64, "observation_id": 4491},
    ]
    manifest = _self_hashed(
        EVALUATE.UNIT_MANIFEST_SCHEMA_VERSION,
        "unit_manifest_payload_sha256",
        status="complete",
        campaign_pristine=False,
        execution_order_changed_for_operational_efficiency=True,
        scientific_unit_set_changed=False,
        execution_plan=plan_identity,
        acquisition_manifest=acquisition_identity,
        source_manifest=source_ledger_identity,
        amendment=amendment_identity,
        runtime_guard=guard_identity,
        amended_normalizer=normalizer_identity,
        sensitivity_exclusion_observation_ids=[4491],
        unit_count=2,
        decoder_executed_unit_count=0,
        re_normalized_without_decoder_rerun_count=2,
        prior_exposed_unit_ids=sorted(item["unit_id"] for item in exposures),
        units=units,
        normalized_results=result_identities,
    )
    manifest_path = output / "unit-manifest-amended.json"
    _write(manifest_path, manifest)

    monkeypatch.setattr(EVALUATE, "_validate_plan_and_acquisition", lambda **kwargs: None)
    monkeypatch.setattr(
        EVALUATE,
        "EXPECTED_NO_DECODER_EVIDENCE_V2_SHA256",
        evidence_identity["sha256"],
    )
    monkeypatch.setattr(
        EVALUATE,
        "_validate_no_decoder_evidence",
        lambda *args, **kwargs: {
            "evidence_payload_sha256": "8" * 64,
            "campaign_output_inventory": {"inventory_sha256": "9" * 64},
        },
    )
    monkeypatch.setattr(
        EVALUATE, "_validate_component_closure", lambda closure, **kwargs: closure
    )
    monkeypatch.setattr(
        EVALUATE,
        "_validate_frozen_source_manifest",
        lambda source, **kwargs: {"signals": [], "controls_per_observation": []},
    )
    monkeypatch.setattr(
        EVALUATE,
        "_validate_amendment",
        lambda amendment, **kwargs: {"prior_decoder_exposures": exposures},
    )
    monkeypatch.setattr(EVALUATE, "_validate_runtime_guard", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        EVALUATE, "_validate_control_artifact_path", lambda *args, **kwargs: {}
    )
    monkeypatch.setattr(EVALUATE, "_validate_evaluator_lock", lambda *args, **kwargs: None)
    monkeypatch.setattr(EVALUATE, "_validate_output_binding", lambda *args, **kwargs: None)
    monkeypatch.setattr(EVALUATE, "_validate_null_ledgers", lambda **kwargs: None)
    monkeypatch.setattr(
        EVALUATE,
        "_validate_source_ledger",
        lambda *args, **kwargs: [
            {"observation_id": 4491, "input_kind": "signal", "null_id": None},
            {"observation_id": 4492, "input_kind": "signal", "null_id": None},
        ],
    )
    fake_launcher = types.SimpleNamespace(build_units=lambda **kwargs: units)
    fake_scoring = types.SimpleNamespace(MAXIMUM_PAYLOAD_BYTES_PER_UNIT=1024)

    def activate_dependencies(*args, **kwargs):
        EVALUATE.FROZEN_LAUNCHER = fake_launcher
        EVALUATE.FROZEN_SCORING = fake_scoring

    monkeypatch.setattr(EVALUATE, "_activate_frozen_dependencies", activate_dependencies)

    def evaluate_both(**kwargs):
        assert kwargs["expected_units"] == units
        assert [record["unit"] for record in kwargs["records"]] == units
        return {"status": "complete"}, {"status": "complete"}

    monkeypatch.setattr(EVALUATE, "_evaluate_both_cohorts", evaluate_both)
    report = EVALUATE.evaluate_artifacts(
        execution_plan_path=plan_path,
        acquisition_manifest_path=acquisition_path,
        frozen_source_manifest_path=source_path,
        amendment_path=amendment_path,
        runtime_guard_path=guard_path,
        no_decoder_evidence_path=evidence_path,
        no_decoder_evidence_generator_path=evidence_generator_path,
        component_closure_path=component_closure_path,
        component_closure_generator_path=component_closure_generator_path,
        amended_launcher_path=launcher_path,
        amended_normalizer_path=normalizer_path,
        evaluator_lock_path=lock_path,
        unit_manifest_path=manifest_path,
    )
    assert report["status"] == "complete"
    assert report["provenance"]["evaluator_lock"] == lock_identity
    assert report["full_frozen_cohort"]["observation_count"] == 2
    assert report["full_frozen_cohort"]["runtime_history"] == "mixed_and_non_pristine"
    assert (
        report["full_frozen_cohort"][
            "component_runtime_full_closure_for_two_prior_exposures_cryptographically_established"
        ]
        is False
    )
    assert report["mandatory_unexposed_sensitivity"]["observation_count"] == 1
    assert report["claim_guard"]["publication_ready"] is False
    assert (
        report["claim_guard"][
            "full_component_closure_claim_limited_to_remaining_6166_decoder_runs"
        ]
        is True
    )

    changed = json.loads(json.dumps(manifest))
    changed["normalized_results"][0]["path"] = str(
        (tmp_path / "outside-normalized-result.json").absolute()
    )
    changed["unit_manifest_payload_sha256"] = EVALUATE.sha256_document(
        {key: value for key, value in changed.items() if key != "unit_manifest_payload_sha256"}
    )
    _write(manifest_path, changed)
    with pytest.raises(ValueError, match="path/order"):
        EVALUATE.evaluate_artifacts(
            execution_plan_path=plan_path,
            acquisition_manifest_path=acquisition_path,
            frozen_source_manifest_path=source_path,
            amendment_path=amendment_path,
            runtime_guard_path=guard_path,
            no_decoder_evidence_path=evidence_path,
            no_decoder_evidence_generator_path=evidence_generator_path,
            component_closure_path=component_closure_path,
            component_closure_generator_path=component_closure_generator_path,
            amended_launcher_path=launcher_path,
            amended_normalizer_path=normalizer_path,
            evaluator_lock_path=lock_path,
            unit_manifest_path=manifest_path,
        )


def test_main_requires_every_postfreeze_artifact_hash(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit, match="0"):
        EVALUATE.main(["--help"])
    help_text = capsys.readouterr().out
    for option in (
        "--expected-amendment-sha256",
        "--expected-runtime-guard-sha256",
        "--expected-no-decoder-evidence-sha256",
        "--expected-no-decoder-evidence-generator-sha256",
        "--expected-component-closure-sha256",
        "--expected-component-closure-generator-sha256",
        "--expected-amended-launcher-sha256",
        "--expected-amended-normalizer-sha256",
        "--expected-evaluator-lock-sha256",
    ):
        assert option in help_text
    assert "raw-result" not in help_text
    assert "--iq" not in help_text
