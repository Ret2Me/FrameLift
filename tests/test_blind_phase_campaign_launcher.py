from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import types

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "work/blind-phase-confirmatory-v2/campaign_launcher.py"


def _module():
    name = "blind_phase_campaign_launcher_test_module"
    spec = importlib.util.spec_from_file_location(name, SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


LAUNCHER = _module()


def _plan():
    return {
        "schema_version": "blind-phase-confirmatory-execution-plan-v2",
        "status": "frozen_before_selected_iq_download_and_decoder_execution",
        "routing": {
            "routes": [
                {
                    "observation_id": observation_id,
                    "satellite_id": f"SAT-{observation_id % 15}",
                    "profile_routable": observation_id <= 17,
                }
                for observation_id in range(1, 31)
            ]
        },
    }


def _sources():
    rows = []
    descriptors = [("signal", None), *(('null', f'null-{index}') for index in range(11))]
    for observation_id in range(1, 31):
        for input_kind, null_id in descriptors:
            payload = f"{observation_id}:{input_kind}:{null_id}".encode()
            rows.append(
                {
                    "observation_id": observation_id,
                    "satellite_id": f"SAT-{observation_id % 15}",
                    "input_kind": input_kind,
                    "null_id": null_id,
                    "path": f"/sources/{observation_id}/{input_kind}-{null_id}",
                    "size_bytes": 400,
                    "sha256": hashlib.sha256(payload).hexdigest(),
                    "duration_seconds": 400 / (4 * 57_600),
                    "far_denominator": input_kind == "null" and null_id != "null-0",
                }
            )
    return {
        "schema_version": LAUNCHER.SOURCE_MANIFEST_SCHEMA,
        "status": "complete",
        "sources": rows,
    }


def _execution_tools():
    return {
        label: LAUNCHER.regular_file_identity(path)
        for label, path in LAUNCHER.CAMPAIGN_EXECUTION_TOOL_PATHS.items()
    }


def _execution_bound_plan():
    plan = _plan()
    source_paths = {
        *sorted((ROOT / "src/telemetry_yield").rglob("*.py")),
        LAUNCHER.CANDIDATE_RELEASE_PATH,
        LAUNCHER.QUALIFIER_PATH,
        LAUNCHER.RESULT_SCHEMA_PATH,
        LAUNCHER.COMPONENT_SCHEMA_PATH,
    }
    source_manifest = [
        LAUNCHER.regular_file_identity(path) for path in sorted(source_paths)
    ]
    runtime_lock = {
        "schema_version": "blind-phase-fsk-candidate-runtime-lock-v1",
        "status": "PASS",
        "source_manifest": source_manifest,
        "source_manifest_sha256": LAUNCHER.sha256_document(source_manifest),
    }
    runtime_lock["runtime_lock_payload_sha256"] = LAUNCHER.sha256_document(
        runtime_lock
    )
    plan["provenance"] = {
        "campaign_execution_tools": _execution_tools(),
        "candidate_runtime_lock": runtime_lock,
    }
    return plan


def test_schedule_is_derived_as_four_single_rate_generic_and_one_mission_job() -> None:
    units = LAUNCHER.build_units(plan=_plan(), source_manifest=_sources())
    assert len(units) == 30 * 12 * 2 * 8 + 17 * 12 * 2
    assert len({unit["unit_id"] for unit in units}) == len(units)
    per_input = [
        unit
        for unit in units
        if unit["observation_id"] == 1
        and unit["input_kind"] == "signal"
        and unit["repeat_id"] == "repeat-a"
    ]
    assert sorted(
        unit["baudrate"]
        for unit in per_input
        if unit["arm"] == "component_a"
    ) == [1200, 4800, 9600, 19200]
    assert sorted(
        unit["baudrate"]
        for unit in per_input
        if unit["arm"] == "candidate"
    ) == [1200, 4800, 9600, 19200]
    assert [unit["baudrate"] for unit in per_input if unit["arm"] == "mission_satyaml"] == [None]


def test_schedule_rejects_operator_omission_or_extra_source() -> None:
    sources = _sources()
    sources["sources"].pop()
    with pytest.raises(ValueError, match="exact signal/null coverage"):
        LAUNCHER.build_units(plan=_plan(), source_manifest=sources)


def test_launcher_requires_and_revalidates_frozen_sandbox_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _plan()
    with pytest.raises(ValueError, match="lacks provenance"):
        LAUNCHER._validated_sandbox_contract(plan)
    contract = {"schema_version": "test-sandbox-contract"}
    plan["provenance"] = {"sandbox_lock": contract}
    observed = []
    monkeypatch.setattr(
        LAUNCHER,
        "SANDBOX",
        types.SimpleNamespace(validate_live_contract=lambda value: observed.append(value)),
    )
    assert LAUNCHER._validated_sandbox_contract(plan) == contract
    assert observed == [contract]


def test_launcher_requires_exact_complete_campaign_execution_tools() -> None:
    plan = _execution_bound_plan()
    assert LAUNCHER._validate_campaign_execution_tools(plan) == plan["provenance"][
        "campaign_execution_tools"
    ]

    drifted = json.loads(json.dumps(plan))
    drifted["provenance"]["campaign_execution_tools"]["sandbox"]["sha256"] = "0" * 64
    with pytest.raises(ValueError, match="identity drift: sandbox"):
        LAUNCHER._validate_campaign_execution_tools(drifted)

    missing = json.loads(json.dumps(plan))
    del missing["provenance"]["campaign_execution_tools"]["launcher"]
    with pytest.raises(ValueError, match="tool set is not exact"):
        LAUNCHER._validate_campaign_execution_tools(missing)

    assert {
        "candidate_release_verifier",
        "candidate_release_v1_helpers",
    } <= set(plan["provenance"]["campaign_execution_tools"])


def test_plan_bound_modules_load_only_between_pre_and_post_identity_gates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    name = "blind_phase_campaign_launcher_fresh_bootstrap_test_module"
    spec = importlib.util.spec_from_file_location(name, SCRIPT)
    assert spec is not None and spec.loader is not None
    fresh = importlib.util.module_from_spec(spec)
    sys.modules[name] = fresh
    spec.loader.exec_module(fresh)
    assert all(
        value is None
        for value in (
            fresh.BOUNDED,
            fresh.SANDBOX,
            fresh.NORMALIZER,
            fresh.RELEASE,
            fresh.NULL_TRANSFORM,
        )
    )
    events = []
    monkeypatch.setattr(
        fresh,
        "_validate_campaign_execution_tools",
        lambda plan: events.append("validate"),
    )

    def fake_load(name, path):
        events.append(f"load:{path.name}")
        return types.SimpleNamespace(
            name=name,
            validate_runtime_lock_live=lambda lock: events.append("runtime"),
        )

    monkeypatch.setattr(fresh, "_load_module", fake_load)
    fresh._activate_plan_bound_modules(_execution_bound_plan())
    assert events == [
        "validate",
        "load:bounded_process.py",
        "load:sandbox_contract.py",
        "load:normalize_result.py",
        "load:build_candidate_release.py",
        "load:null_transform.py",
        "validate",
        "runtime",
    ]


def test_bootstrap_source_manifest_rejects_rehashed_remove_and_add(
    tmp_path: Path,
) -> None:
    for mutation in ("remove", "add"):
        plan = _execution_bound_plan()
        runtime = plan["provenance"]["candidate_runtime_lock"]
        if mutation == "remove":
            runtime["source_manifest"].pop()
        else:
            extra = tmp_path / "unreviewed.py"
            extra.write_text("raise RuntimeError('must not execute')\n", encoding="utf-8")
            runtime["source_manifest"].append(
                LAUNCHER.regular_file_identity(extra)
            )
        runtime["source_manifest_sha256"] = LAUNCHER.sha256_document(
            runtime["source_manifest"]
        )
        runtime["runtime_lock_payload_sha256"] = LAUNCHER.sha256_document(
            {
                key: value
                for key, value in runtime.items()
                if key != "runtime_lock_payload_sha256"
            }
        )
        with pytest.raises(ValueError, match="exact bootstrap closure"):
            LAUNCHER._validate_campaign_execution_tools(plan)


def test_main_checks_execution_tools_before_acquisition_is_opened(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan_path = tmp_path / "plan.json"
    _write_json(plan_path, _plan())
    plan_identity = LAUNCHER.regular_file_identity(plan_path)
    acquisition_path = tmp_path / "must-not-be-opened.json"
    observed = []

    def stop_before_acquisition(plan):
        observed.append(plan)
        raise RuntimeError("execution-tool-gate")

    monkeypatch.setattr(LAUNCHER, "_validate_campaign_execution_tools", stop_before_acquisition)
    with pytest.raises(RuntimeError, match="execution-tool-gate"):
        LAUNCHER.main(
            [
                "--execution-plan",
                str(plan_path),
                "--expected-execution-plan-sha256",
                str(plan_identity["sha256"]),
                "--acquisition-manifest",
                str(acquisition_path),
                "--expected-acquisition-manifest-sha256",
                "0" * 64,
                "--candidate-config",
                str(tmp_path / "candidate.json"),
                "--source-root",
                str(tmp_path / "sources"),
                "--output-root",
                str(tmp_path / "output"),
            ]
        )
    assert observed == [_plan()]
    assert not acquisition_path.exists()


def test_direct_execute_and_materialize_gate_tools_before_acquisition_or_iq(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan_path = tmp_path / "plan.json"
    _write_json(plan_path, _plan())
    missing_acquisition = tmp_path / "must-not-be-opened.json"
    marker = RuntimeError("execution-tool-gate")
    monkeypatch.setattr(
        LAUNCHER,
        "_validate_campaign_execution_tools",
        lambda plan: (_ for _ in ()).throw(marker),
    )
    with pytest.raises(RuntimeError, match="execution-tool-gate"):
        LAUNCHER.execute_campaign(
            plan_path=plan_path,
            acquisition_path=missing_acquisition,
            source_manifest_path=tmp_path / "must-not-be-opened-sources.json",
            candidate_config_path=tmp_path / "must-not-be-opened-config.json",
            output_root=tmp_path / "must-not-exist",
        )
    assert not (tmp_path / "must-not-exist").exists()

    iq_check_reached = []
    monkeypatch.setattr(
        LAUNCHER,
        "_verified_acquisition",
        lambda *args, **kwargs: iq_check_reached.append(True),
    )
    with pytest.raises(RuntimeError, match="execution-tool-gate"):
        LAUNCHER.materialize_source_manifest(
            plan=_plan(),
            plan_identity={"path": str(plan_path), "size_bytes": 1, "sha256": "0" * 64},
            acquisition={},
            acquisition_identity={
                "path": str(missing_acquisition),
                "size_bytes": 1,
                "sha256": "0" * 64,
            },
            destination=tmp_path / "must-not-exist-sources",
        )
    assert iq_check_reached == []
    assert not (tmp_path / "must-not-exist-sources").exists()


def test_selection_contains_every_frozen_catalog_mode_without_profile_aliasing() -> None:
    selection = json.loads(
        (ROOT / "reports/blind-phase-prospective-holdout-selection-v2.json").read_text(
            encoding="utf-8"
        )
    )
    assert {row["mode"] for row in selection["selected"]} == {
        "FSK",
        "FSK AX.25 G3RUH",
        "GFSK",
        "GMSK",
    }


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")


def test_resume_reproduces_normalization_from_exact_receipt_and_raw_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    unit = LAUNCHER._unit(
        {
            "schema_version": "blind-phase-confirmatory-campaign-unit-v1",
            "observation_id": 1,
            "satellite_id": "SAT-1",
            "arm": "candidate",
            "repeat_id": "repeat-a",
            "input_kind": "signal",
            "null_id": None,
            "baudrate": 1200,
            "source": {
                "size_bytes": 4,
                "sha256": hashlib.sha256(b"iq00").hexdigest(),
                "duration_seconds": 4 / (4 * 57_600),
            },
        }
    )
    source = tmp_path / "source.ci16"
    source.write_bytes(b"iq00")
    plan_path = tmp_path / "plan.json"
    acquisition_path = tmp_path / "acquisition.json"
    config_path = tmp_path / "candidate.json"
    for path in (plan_path, acquisition_path, config_path):
        path.write_text("{}", encoding="utf-8")
    plan_identity = LAUNCHER.regular_file_identity(plan_path)
    acquisition_identity = LAUNCHER.regular_file_identity(acquisition_path)
    candidate_identity = LAUNCHER.regular_file_identity(config_path)
    normalizer_identity = LAUNCHER.regular_file_identity(LAUNCHER.NORMALIZER_PATH)
    output_root = tmp_path / "campaign"
    unit_root = output_root / "units" / unit["unit_id"]
    scratch = unit_root / "scratch"
    output = unit_root / "output"
    scratch.mkdir(parents=True, mode=0o700)
    output.mkdir(mode=0o700)
    raw_path = output / "raw-result.json"
    raw_path.write_text("{}", encoding="utf-8")
    raw_path.chmod(0o444)
    receipt_path = unit_root / "process-receipt.json"
    receipt = {"status": "completed"}
    _write_json(receipt_path, receipt)
    receipt_path.chmod(0o444)

    monkeypatch.setattr(LAUNCHER, "_receiver_argv", lambda **kwargs: ("/usr/bin/bwrap",))
    monkeypatch.setattr(
        LAUNCHER,
        "BOUNDED",
        types.SimpleNamespace(
            process_attempt_fingerprint=lambda *args, **kwargs: "f" * 64
        ),
    )
    normalized = LAUNCHER._failure_record(
        unit,
        status="normalization_failure",
        plan_sha256=plan_identity["sha256"],
        acquisition_sha256=acquisition_identity["sha256"],
        normalizer_identity=normalizer_identity,
        expected_process_attempt_fingerprint="f" * 64,
        process_receipt_identity=LAUNCHER.regular_file_identity(receipt_path),
        raw_identity=LAUNCHER.regular_file_identity(raw_path),
        error=ValueError("synthetic validation failure"),
    )
    normalized_path = unit_root / "normalized-result.json"
    _write_json(normalized_path, normalized)
    normalized_path.chmod(0o444)
    calls = []

    def reproduce(**kwargs):
        calls.append(kwargs)
        return normalized

    monkeypatch.setattr(LAUNCHER, "_normalize_from_process_evidence", reproduce)
    returned = LAUNCHER._run_one_unit(
        unit=unit,
        source_path=source,
        plan_path=plan_path,
        acquisition_path=acquisition_path,
        candidate_config_path=config_path,
        candidate_config={},
        candidate_identity=candidate_identity,
        plan={"routing": {"routes": []}},
        plan_identity=plan_identity,
        acquisition_identity=acquisition_identity,
        output_root=output_root,
        normalizer_identity=normalizer_identity,
        limits=object(),
    )
    assert returned == normalized
    assert len(calls) == 1
    assert calls[0]["raw_path"] == raw_path
    assert calls[0]["receipt_identity"] == LAUNCHER.regular_file_identity(receipt_path)

    forged = dict(normalized)
    forged["trusted_native"] = [{"payload_hex": "00"}]
    forged["normalized_result_payload_sha256"] = LAUNCHER.sha256_document(
        {key: value for key, value in forged.items() if key != "normalized_result_payload_sha256"}
    )
    normalized_path.chmod(0o600)
    _write_json(normalized_path, forged)
    normalized_path.chmod(0o444)
    with pytest.raises(ValueError, match="differs from reproduced evidence"):
        LAUNCHER._run_one_unit(
            unit=unit,
            source_path=source,
            plan_path=plan_path,
            acquisition_path=acquisition_path,
            candidate_config_path=config_path,
            candidate_config={},
            candidate_identity=candidate_identity,
            plan={"routing": {"routes": []}},
            plan_identity=plan_identity,
            acquisition_identity=acquisition_identity,
            output_root=output_root,
            normalizer_identity=normalizer_identity,
            limits=object(),
        )


def test_streaming_null_lifecycle_never_materializes_more_than_one_null(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    controls = [
        {"kind": "all_zero", "seed": None},
        {"kind": "complex_time_reversal", "seed": None},
        *({"kind": "phase_scramble", "seed": seed} for seed in range(1, 10)),
    ]
    capture_root = tmp_path / "captures"
    capture_root.mkdir()
    objects = []
    signals = []
    for observation_id in range(1, 31):
        path = capture_root / f"{observation_id}.ci16"
        payload = bytes((observation_id, 1, 2, 3))
        path.write_bytes(payload)
        digest = hashlib.sha256(payload).hexdigest()
        objects.append(
            {
                "observation_id": observation_id,
                "satellite_id": f"SAT-{observation_id % 15}",
                "local_path": str(path),
                "actual_size_bytes": 4,
                "sha256": digest,
            }
        )
        signals.append(
            {
                "observation_id": observation_id,
                "satellite_id": f"SAT-{observation_id % 15}",
                "input_kind": "signal",
                "null_id": None,
                "path": str(path),
                "size_bytes": 4,
                "sha256": digest,
                "duration_seconds": 4 / (4 * 57_600),
                "far_denominator": False,
            }
        )
    config_path = tmp_path / "candidate.json"
    config_path.write_text("{}", encoding="utf-8")
    candidate_identity = LAUNCHER.regular_file_identity(config_path)
    plan = {
        "schema_version": "blind-phase-confirmatory-execution-plan-v2",
        "status": "frozen_before_selected_iq_download_and_decoder_execution",
        "routing": {
            "routes": [
                {
                    "observation_id": observation_id,
                    "satellite_id": f"SAT-{observation_id % 15}",
                    "profile_routable": observation_id <= 17,
                }
                for observation_id in range(1, 31)
            ]
        },
        "null_controls": {
            "controls_per_observation": controls,
            "chunk_complex_samples": 1,
        },
        "candidate_execution": {
            "process_supervision": {
                "limits": {
                    "wall_timeout_seconds": 1.0,
                    "rlimit_cpu_seconds": 1,
                    "maximum_process_tree_rss_bytes": 1,
                    "rlimit_address_space_bytes_per_process": 1,
                    "rlimit_file_size_bytes": 1,
                    "maximum_stdout_bytes": 1,
                    "maximum_stderr_bytes": 1,
                }
            }
        },
        "provenance": {
            "candidate_config": candidate_identity,
            "sandbox_lock": {"schema_version": "test-sandbox-contract"},
        },
    }
    plan_path = tmp_path / "plan.json"
    _write_json(plan_path, plan)
    plan_identity = LAUNCHER.regular_file_identity(plan_path)
    acquisition = {
        "schema_version": "blind-phase-holdout-acquisition-manifest-v1",
        "status": "complete",
        "execution_plan": plan_identity,
        "objects": objects,
    }
    acquisition["manifest_payload_sha256"] = LAUNCHER.sha256_document(acquisition)
    acquisition_path = tmp_path / "acquisition.json"
    _write_json(acquisition_path, acquisition)
    acquisition_identity = LAUNCHER.regular_file_identity(acquisition_path)
    source_manifest = {
        "schema_version": LAUNCHER.SOURCE_MANIFEST_SCHEMA,
        "status": "complete",
        "execution_plan": plan_identity,
        "acquisition_manifest": acquisition_identity,
        "null_ids": [LAUNCHER._null_id(control) for control in controls],
        "signals": signals,
        "controls_per_observation": controls,
    }
    source_manifest["source_manifest_payload_sha256"] = LAUNCHER.sha256_document(source_manifest)
    source_manifest_path = tmp_path / "source-manifest.json"
    _write_json(source_manifest_path, source_manifest)
    output_root = tmp_path / "output"
    observations = []

    def fake_run_one_unit(*, unit, source_path, output_root, **kwargs):
        ephemeral = output_root / "ephemeral"
        live = list(ephemeral.glob("*.ci16")) if ephemeral.exists() else []
        assert len(live) <= 1
        if unit["input_kind"] == "null":
            assert live == [source_path]
        observations.append((unit["input_kind"], unit["unit_id"]))
        result = {
            "schema_version": LAUNCHER.NORMALIZED_RESULT_SCHEMA_VERSION,
            "status": "completed",
            "unit": dict(unit),
        }
        path = output_root / "units" / unit["unit_id"] / "normalized-result.json"
        _write_json(path, result)
        path.chmod(0o444)
        return result

    monkeypatch.setattr(LAUNCHER, "_run_one_unit", fake_run_one_unit)
    live_runtime_checks = []
    monkeypatch.setattr(
        LAUNCHER,
        "RELEASE",
        types.SimpleNamespace(
            validate_runtime_lock_live=lambda lock: live_runtime_checks.append(lock)
        ),
    )
    monkeypatch.setattr(
        LAUNCHER,
        "SANDBOX",
        types.SimpleNamespace(validate_live_contract=lambda contract: None),
    )
    monkeypatch.setattr(
        LAUNCHER,
        "_activate_plan_bound_modules",
        lambda plan: None,
    )
    monkeypatch.setattr(
        LAUNCHER,
        "BOUNDED",
        types.SimpleNamespace(
            ProcessLimits=lambda **values: types.SimpleNamespace(**values)
        ),
    )
    monkeypatch.setattr(
        LAUNCHER,
        "NULL_TRANSFORM",
        LAUNCHER._load_module(
            "blind_phase_campaign_null_transform_test",
            LAUNCHER.NULL_TRANSFORM_PATH,
        ),
    )
    plan["provenance"]["candidate_runtime_lock"] = {"runtime": "frozen"}
    _write_json(plan_path, plan)
    plan_identity = LAUNCHER.regular_file_identity(plan_path)
    acquisition["execution_plan"] = plan_identity
    acquisition["manifest_payload_sha256"] = LAUNCHER.sha256_document(
        {
            key: value
            for key, value in acquisition.items()
            if key != "manifest_payload_sha256"
        }
    )
    _write_json(acquisition_path, acquisition)
    acquisition_identity = LAUNCHER.regular_file_identity(acquisition_path)
    source_manifest["execution_plan"] = plan_identity
    source_manifest["acquisition_manifest"] = acquisition_identity
    source_manifest["source_manifest_payload_sha256"] = LAUNCHER.sha256_document(
        {
            key: value
            for key, value in source_manifest.items()
            if key != "source_manifest_payload_sha256"
        }
    )
    _write_json(source_manifest_path, source_manifest)
    manifest = LAUNCHER.execute_campaign(
        plan_path=plan_path,
        acquisition_path=acquisition_path,
        source_manifest_path=source_manifest_path,
        candidate_config_path=config_path,
        output_root=output_root,
    )
    assert manifest["status"] == "complete"
    assert manifest["unit_count"] == 30 * 12 * 2 * 8 + 17 * 12 * 2
    assert len(observations) == manifest["unit_count"]
    assert not list((output_root / "ephemeral").glob("*.ci16"))
    ledgers = list((output_root / "null-ledgers").glob("*.json"))
    assert len(ledgers) == 30 * 11
    source_ledger = json.loads((output_root / "source-ledger.json").read_text())
    assert source_ledger["counts"]["maximum_simultaneously_materialized_null_files"] == 1
    assert source_ledger["counts"]["peak_ephemeral_null_bytes"] == 4
    assert live_runtime_checks == [{"runtime": "frozen"}]
