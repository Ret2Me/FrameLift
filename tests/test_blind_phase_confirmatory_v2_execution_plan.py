from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "work/blind-phase-confirmatory-v2/freeze_execution_plan.py"


def _module():
    name = "blind_phase_confirmatory_v2_execution_plan_test_module"
    spec = importlib.util.spec_from_file_location(name, SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


PLAN = _module()
_LIVE_SANDBOX_LOCK: dict[str, object] | None = None


def _candidate() -> dict[str, object]:
    return {
        "schema_version": "blind-phase-fsk-confirmatory-candidate-config-v1",
        "sample_rate_hz": 57_600,
        "rate_configs": [
            {
                "baudrate": baudrate,
                "receiver_config": {
                    "sample_rate_hz": 57_600,
                    "baudrate": baudrate,
                    "phase_difference_lags": [1, 3],
                    "descramble_modes": [False, True],
                    "maximum_map_seed_states": 512,
                },
            }
            for baudrate in (1_200, 4_800, 9_600, 19_200)
        ],
        "protocol_adapter": {
            "id": "hdlc-crc16-x25-ax25-ui-plain-and-g3ruh",
            "catalog_mode_supported_exact": ["FSK", "FSK AX25 G3RUH", "GFSK", "GMSK"],
            "compatible_profile_framing_exact": ["AX.25", "AX.25 G3RUH"],
            "compatible_profile_modulation_exact": ["FSK"],
            "repaired_output_default": "untrusted",
        },
    }


def _component_lock() -> dict[str, object]:
    return {
        "baseline_id": "gr-satellites-5.9.0-official-fsk-ax25-components",
        "sample_rate_hz": 57_600,
        "baudrates": [1_200, 4_800, 9_600, 19_200],
        "g3ruh_modes": [False, True],
        "official_component_chain": [
            "gr_satellites.components.demodulators.fsk_demodulator",
            "gr_satellites.components.deframers.ax25_deframer",
        ],
        "runner": {"path": "component.py", "sha256": "4" * 64},
    }


def _sandbox_lock() -> dict[str, object]:
    global _LIVE_SANDBOX_LOCK
    if _LIVE_SANDBOX_LOCK is None:
        _LIVE_SANDBOX_LOCK = PLAN.SANDBOX.run_synthetic_preflight()
    return copy.deepcopy(_LIVE_SANDBOX_LOCK)


def _candidate_runtime_lock() -> dict[str, object]:
    source_manifest = _candidate_sources()
    runtime_manifest = {"wheel": {"path": "candidate.whl", "sha256": "8" * 64}}
    value = {
        "schema_version": "blind-phase-fsk-candidate-runtime-lock-v1",
        "status": "PASS",
        "api_version": "telemetry-yield-blind-phase-fsk-file-api-v2",
        "protocol_adapter_id": "hdlc-crc16-x25-ax25-ui-plain-and-g3ruh",
        "candidate_config": {"path": "candidate.json", "sha256": "2" * 64},
        "candidate_config_sha256": "2" * 64,
        "source_manifest": source_manifest,
        "source_manifest_sha256": PLAN._sha256_document(source_manifest),
        "runtime_manifest": runtime_manifest,
        "runtime_manifest_sha256": PLAN._sha256_document(runtime_manifest),
        "wheel_sha256": "8" * 64,
        "qualification_report": {"path": "qualification.json", "sha256": "9" * 64},
        "qualification_report_sha256": "9" * 64,
        "claim_guard": {
            "selected_holdout_iq_processed": False,
            "publication_ready": False,
        },
    }
    value["runtime_lock_payload_sha256"] = PLAN._sha256_document(value)
    return value


def _candidate_sources() -> list[dict[str, object]]:
    return [
        {
            "path": "/frozen/src/telemetry_yield/null_transform.py",
            "size_bytes": 1,
            "sha256": "5" * 64,
        }
    ]


def _build() -> dict[str, object]:
    selection = PLAN.V1.load_object(PLAN.SELECTION)
    transmitters = PLAN.V1._load_transmitters(PLAN.V1.TRANSMITTER_SNAPSHOTS)
    registry = PLAN.V1.load_object(PLAN.V1.SATYAML_REGISTRY)
    routes = PLAN.V1.route_selection(selection["selected"], transmitters, registry)
    profiles = [
        {"path": path, "size_bytes": 1, "sha256": "3" * 64}
        for path in sorted(
            {
                route.profile_selector
                for route in routes
                if route.profile_routable and route.profile_selector is not None
            }
        )
    ]
    return PLAN.build_execution_plan(
        selection=selection,
        transmitters=transmitters,
        registry=registry,
        candidate=_candidate(),
        fixed_inputs=[{"path": str(PLAN.SELECTION), "sha256": "1" * 64}],
        candidate_config_identity={"path": "candidate.json", "sha256": "2" * 64},
        candidate_sources=_candidate_sources(),
        candidate_runtime_lock=_candidate_runtime_lock(),
        component_baseline_lock=_component_lock(),
        mission_profile_sources=profiles,
        mission_runtime_lock={"executable": {"path": "gr_satellites", "sha256": "7" * 64}},
        sandbox_lock=_sandbox_lock(),
        freezer_identity={"path": str(SCRIPT), "sha256": "8" * 64},
        created_at="2026-09-03T18:00:00Z",
    )


def test_primary_is_all_30_component_a_and_mission_is_separate_secondary() -> None:
    plan = _build()
    assert plan["schema_version"] == "blind-phase-confirmatory-execution-plan-v2"
    primary = plan["strata_and_claim_hierarchy"]["confirmatory_primary_all_cohort"]
    assert primary["current_n"] == 30
    assert len(primary["observation_ids"]) == 30
    assert "component baseline A" in primary["baseline"]
    mission = plan["strata_and_claim_hierarchy"]
    assert mission["mission_satyaml_whole_receiver_secondary"]["current_n"] == 17
    assert mission["mission_satyaml_protocol_matched_secondary"]["current_n"] == 15
    assert plan["baseline_execution"]["primary_component_baseline_a"]["population"].startswith(
        "all 30"
    )
    assert plan["baseline_execution"]["primary_component_baseline_a"][
        "historical_satnogs_platform_equivalent"
    ] is False


def test_same_bytes_union_plain_g3ruh_and_paired_bootstrap_are_frozen() -> None:
    plan = _build()
    candidate = plan["candidate_execution"]
    assert candidate["rate_plan_baud"] == [1_200, 4_800, 9_600, 19_200]
    assert candidate["descramble_modes_exact"] == [False, True]
    assert candidate["plain_and_g3ruh_are_not_independent_repair_authentication"] is True
    supervision = candidate["process_supervision"]
    assert supervision["in_process_campaign_import_forbidden"] is True
    assert supervision["limits"] == PLAN.CANDIDATE_SUBPROCESS_LIMITS
    assert "SIGKILL" in supervision["cleanup"]
    assert "structured timeout/resource/exit result" in supervision["terminal_failure_record"]
    assert len(plan["primary_incremental_union"]["members"]) == 2
    assert "mission" not in " ".join(plan["primary_incremental_union"]["members"]).casefold()
    assert len(plan["operational_system_union_secondary"]["members"]) == 3
    assert "FCS-free" in plan["primary_incremental_union"]["ax25_normalization"]
    binding = plan["acquisition_content_binding"]
    assert "/input/capture.ci16" in binding["same_iq_rule"]
    bootstrap = plan["statistics"]["paired_satellite_cluster_bootstrap"]
    assert bootstrap["resamples"] == 10_000
    assert bootstrap["seed"] == 20_260_903
    assert "preserve all arm pairing" in bootstrap["resample"]
    assert plan["statistics"]["uncertainty_cluster"].startswith("satellite_id")
    nulls = plan["null_controls"]
    assert "trusted/admitted native" in nulls["primary_false_accept_rule"]
    assert "do not include" in nulls["repaired_candidate_diagnostic"]
    assert "count_as_false_accept" not in nulls
    gates = plan["release_gates"]
    assert gates["deployment_union_does_not_require_positive_increment"] is True
    assert (
        plan["diagnostics_pending"]["candidate_standalone_no_regression"]
        == "pending_execution"
    )
    assert "candidate_standalone_no_regression_diagnostic" not in gates
    assert "candidate_no_regression_vs_component_a" not in gates


def test_firewall_and_selected_iq_absence_gates_are_explicit() -> None:
    plan = _build()
    firewall = plan["reference_firewall"]
    assert firewall["sandbox"]["unshare_all_including_network"] is True
    assert firewall["sandbox"]["scientific_inputs_read_only"] is True
    assert "--unshare-all" in firewall["bwrap_required_flags"]
    assert plan["release_gates"]["selected_iq_absent_during_plan_freeze"] is True
    assert plan["claim_guard"]["publication_ready"] is False
    assert {
        "candidate_release_verifier",
        "candidate_release_v1_helpers",
    } <= set(plan["provenance"]["campaign_execution_tools"])


def test_plan_rejects_shallow_or_drifted_sandbox_pass_lock() -> None:
    shallow = {
        "schema_version": PLAN.SANDBOX.SANDBOX_CONTRACT_VERSION,
        "status": "PASS",
        "unshare_all_including_network": True,
        "host_root_default_inaccessible": True,
        "scientific_inputs_read_only": True,
        "only_fresh_scratch_and_output_writable": True,
    }
    with pytest.raises(ValueError, match="not live-exact"):
        PLAN._validate_sandbox_lock(shallow)

    drifted = _sandbox_lock()
    drifted["bubblewrap"]["sha256"] = "0" * 64
    with pytest.raises(ValueError, match="not live-exact"):
        PLAN._validate_sandbox_lock(drifted)


def test_candidate_requires_explicit_plain_and_g3ruh_for_every_rate() -> None:
    candidate = _candidate()
    candidate["rate_configs"][2]["receiver_config"]["descramble_modes"] = [True]
    with pytest.raises(ValueError, match="plain and G3RUH"):
        PLAN.validate_candidate_config(candidate)


def test_candidate_runtime_lock_is_config_bound_and_claim_guarded() -> None:
    runtime = _candidate_runtime_lock()
    identity = {"path": "candidate.json", "sha256": "2" * 64}
    PLAN._validate_candidate_runtime_lock(runtime, identity, _candidate_sources())
    runtime["candidate_config_sha256"] = "a" * 64
    with pytest.raises(ValueError, match="bind the candidate config"):
        PLAN._validate_candidate_runtime_lock(runtime, identity, _candidate_sources())
    runtime = _candidate_runtime_lock()
    runtime["claim_guard"]["selected_holdout_iq_processed"] = True
    runtime["runtime_lock_payload_sha256"] = PLAN._sha256_document(
        {
            key: value
            for key, value in runtime.items()
            if key != "runtime_lock_payload_sha256"
        }
    )
    with pytest.raises(ValueError, match="claim guard"):
        PLAN._validate_candidate_runtime_lock(runtime, identity, _candidate_sources())
    runtime = _candidate_runtime_lock()
    runtime["source_manifest"][0]["sha256"] = "0" * 64
    with pytest.raises(ValueError, match="source manifest hash mismatch"):
        PLAN._validate_candidate_runtime_lock(runtime, identity, _candidate_sources())


def test_plan_freeze_rechecks_full_live_candidate_runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = PLAN.CANDIDATE_RELEASE_SOURCE
    wheel = tmp_path / "candidate.whl"
    sdist = tmp_path / "candidate.tar.gz"
    qualification = tmp_path / "qualification.json"
    interpreter = tmp_path / "python"
    installed = tmp_path / "blind_phase_fsk_file.py"
    for path in (wheel, sdist, qualification, interpreter, installed):
        path.write_bytes(path.name.encode("ascii"))
    source_identity = PLAN._regular_identity(source)
    target_identity = PLAN._regular_identity(interpreter)
    runtime_manifest = {
        "wheel": PLAN._regular_identity(wheel),
        "sdist": PLAN._regular_identity(sdist),
        "environment": {
            "interpreter_target": target_identity,
            "query": {
                "blind_file_api_path": str(installed),
                "blind_file_api_sha256": PLAN.sha256_file(installed),
            },
        },
    }
    lock = {
        "source_manifest": [source_identity],
        "runtime_manifest": runtime_manifest,
        "qualification_report": PLAN._regular_identity(qualification),
    }
    observed = []
    monkeypatch.setattr(
        PLAN.CANDIDATE_RELEASE,
        "validate_runtime_lock_live",
        lambda value: observed.append(value),
    )
    assert PLAN._verify_runtime_lock_artifacts(lock) == (source,)
    assert observed == [lock]


def test_selected_iq_absence_scan_fails_without_opening_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = tmp_path / "repo"
    repository.mkdir()
    monkeypatch.setattr(PLAN, "ROOT", repository)
    row = {"observation_id": 999, "object_key": "selected-999.iq"}
    PLAN._assert_selected_iq_absent((row,))
    outside = tmp_path / "outside"
    outside.mkdir()
    selected = outside / "selected-999.iq"
    selected.write_bytes(b"must-not-be-opened")
    with pytest.raises(RuntimeError, match="already materialized"):
        PLAN._assert_selected_iq_absent((row,))


def test_plan_is_canonical_and_contains_no_outcome_fields() -> None:
    first = PLAN.V1.canonical_pretty(_build())
    second = PLAN.V1.canonical_pretty(_build())
    assert first == second
    decoded = json.loads(first)
    assert decoded["provenance"]["outcome_fields_used"] == []
    assert b"payload_hex" not in first


def test_v2_plan_has_no_stale_v1_selection_or_primary_semantics() -> None:
    plan = _build()
    encoded = PLAN.V1.canonical_pretty(plan).decode("utf-8")
    assert "8e5188fa92b6f602637db23f0c206f96ecc829c182aef56f428de4efb0127268" not in encoded
    assert "blind-phase-live-holdout-selection-v1" not in encoded
    scalar_values = []

    def visit(value) -> None:
        if isinstance(value, dict):
            for nested in value.values():
                visit(nested)
        elif isinstance(value, list):
            for nested in value:
                visit(nested)
        else:
            scalar_values.append(value)

    visit(plan)
    for old_only_observation_id in (4511, 4542, 4510, 4527, 4522):
        assert old_only_observation_id not in scalar_values
    primary_text = json.dumps(
        plan["strata_and_claim_hierarchy"]["confirmatory_primary_all_cohort"],
        sort_keys=True,
    ).casefold()
    assert "satyaml" not in primary_text
    assert "mission" not in primary_text
    assert "candidate minus executable pinned satyaml baseline" not in encoded.casefold()
