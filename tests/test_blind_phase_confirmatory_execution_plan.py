from __future__ import annotations

from dataclasses import replace
import importlib.util
import json
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (
    ROOT
    / "work/blind-phase-confirmatory-v1/freeze_execution_plan.py"
)


def _load_module():
    spec = importlib.util.spec_from_file_location("freeze_execution_plan", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


plan_module = _load_module()


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
                    "maximum_map_seed_states": 512,
                },
            }
            for baudrate in (1_200, 4_800, 9_600, 19_200)
        ],
        "protocol_adapter": {
            "id": "hdlc-crc16-x25-ax25-ui-plain-and-g3ruh",
            "compatible_profile_framing_exact": ["AX.25", "AX.25 G3RUH"],
            "compatible_profile_modulation_exact": ["FSK"],
            "repaired_output_default": "untrusted",
        },
    }


def _actual_inputs():
    selection = plan_module.load_object(plan_module.SELECTION)
    transmitters = plan_module._load_transmitters(
        plan_module.TRANSMITTER_SNAPSHOTS
    )
    registry = plan_module.load_object(plan_module.SATYAML_REGISTRY)
    return selection, transmitters, registry


def _build():
    selection, transmitters, registry = _actual_inputs()
    routes = plan_module.route_selection(selection["selected"], transmitters, registry)
    profile_sources = [
        {
            "path": path,
            "size_bytes": 1,
            "sha256": "4" * 64,
        }
        for path in sorted(
            {
                route.profile_selector
                for route in routes
                if route.profile_routable and route.profile_selector is not None
            }
        )
    ]
    return plan_module.build_execution_plan(
        selection=selection,
        transmitters=transmitters,
        registry=registry,
        candidate=_candidate(),
        fixed_inputs=[{"path": "selection.json", "sha256": "1" * 64}],
        candidate_config_identity={
            "path": "candidate.json",
            "size_bytes": 1,
            "sha256": "2" * 64,
        },
        candidate_sources=[
            {
                "path": "/frozen/src/telemetry_yield/null_transform.py",
                "size_bytes": 1,
                "sha256": "3" * 64,
            }
        ],
        baseline_profile_sources=profile_sources,
        baseline_runtime_identity={
            "executable": {"path": "gr_satellites", "sha256": "5" * 64},
            "environment_manifest_sha256": "6" * 64,
        },
        freezer_identity={
            "path": str(SCRIPT),
            "size_bytes": SCRIPT.stat().st_size,
            "sha256": plan_module.sha256_file(SCRIPT),
        },
    )


def test_actual_metadata_routing_is_stratified_and_not_inflated() -> None:
    plan = _build()
    summary = plan["routing"]["summary"]
    assert summary["all_observations"] == 30
    assert summary["profile_routable_observations"] == 14
    assert summary["protocol_matched_comparable_observations"] == 12
    assert summary["profile_missing_or_ambiguous_observations"] == 16
    assert (
        plan["strata_and_claim_hierarchy"]["confirmatory_primary"][
            "currently_sufficient_for_publication_level_baseline_claim"
        ]
        is False
    )
    assert plan["claim_guard"]["missing_profile_is_not_baseline_failure"] is True
    assert (
        plan["release_gates"][
            "protocol_matched_comparable_observations_at_least_30"
        ]
        is False
    )


def test_frozen_null_bank_exceeds_30_hours_and_zero_event_bound_passes() -> None:
    plan = _build()
    nulls = plan["null_controls"]
    assert nulls["control_count_per_observation"] == 10
    assert nulls["expected_exposure_hours"] >= 30
    assert nulls["zero_event_upper_rate_per_hour"] <= 0.1
    controls = nulls["controls_per_observation"]
    assert [item["kind"] for item in controls[:2]] == [
        "all_zero",
        "complex_time_reversal",
    ]
    seeds = [item["seed"] for item in controls[2:]]
    assert len(seeds) == len(set(seeds)) == 8
    assert all(0 <= seed < 2**64 for seed in seeds)


def test_candidate_bank_is_finite_and_rejects_rate_specific_tuning() -> None:
    normalized = plan_module.validate_candidate_config(_candidate())
    assert [item["baudrate"] for item in normalized] == [1_200, 4_800, 9_600, 19_200]

    tuned = _candidate()
    tuned["rate_configs"][3]["receiver_config"]["maximum_map_seed_states"] = 1024
    with pytest.raises(ValueError, match="rules differ"):
        plan_module.validate_candidate_config(tuned)


def test_outcome_fields_fail_closed() -> None:
    candidate = _candidate()
    candidate["frames_recovered"] = False
    with pytest.raises(ValueError, match="outcome field"):
        plan_module.validate_candidate_config(candidate)


def test_candidate_import_closure_binds_core_local_dependencies() -> None:
    closure = plan_module.local_import_closure(
        (ROOT / "src/telemetry_yield/blind_phase_fsk_file.py",)
    )
    names = {path.name for path in closure}
    assert {
        "blind_phase_fsk_file.py",
        "clipping_robust_fsk.py",
        "soft_sync.py",
        "clock_recovery.py",
        "crc.py",
    }.issubset(names)


def test_plan_is_canonical_and_contains_no_selected_iq_content_hash() -> None:
    plan = _build()
    first = plan_module.canonical_pretty(plan)
    second = plan_module.canonical_pretty(_build())
    assert first == second
    decoded = json.loads(first)
    binding = decoded["acquisition_content_binding"]
    assert "sha256" in binding["required_manifest_fields"]
    assert "payload_hex" not in first.decode("utf-8")


def test_protocol_compatibility_does_not_invent_cross_transmitter_pair() -> None:
    route = plan_module.Route(
        observation_id=1,
        satellite_id="sat",
        status="routed",
        detail="synthetic",
        norad_id=1,
        catalog_transmitter_uuids=("tx",),
        profile_selector="profile.yml",
        profile_name="profile",
        profile_transmitter_ids=("a", "b"),
        profile_modulations=("FSK", "GMSK"),
        profile_framing=("AX.25", "CCSDS"),
        profile_fec=(),
        profile_protocol_pairs=(("FSK", "CCSDS"), ("GMSK", "AX.25")),
    )
    assert plan_module._candidate_profile_compatible(route) is False

    exact = replace(route, profile_protocol_pairs=(("FSK", "AX.25"),))
    assert plan_module._candidate_profile_compatible(exact) is True


def test_exclusive_write_is_atomic_read_only_and_no_clobber(tmp_path: Path) -> None:
    output = tmp_path / "frozen.json"
    identity = plan_module._exclusive_write(output, b"first\n")
    status = output.stat()
    assert identity == (status.st_dev, status.st_ino)
    assert output.read_bytes() == b"first\n"
    assert status.st_mode & 0o777 == 0o444
    with pytest.raises(ValueError, match="refusing to overwrite"):
        plan_module._exclusive_write(output, b"second\n")
    assert output.read_bytes() == b"first\n"
    assert not tuple(tmp_path.glob("*.tmp"))


def test_selected_iq_firewall_scans_outside_work_polyitan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = tmp_path / "repo"
    repository.mkdir()
    monkeypatch.setattr(plan_module, "ROOT", repository)
    rows = ({"observation_id": 4502, "object_key": "observation_4502.iq"},)
    plan_module._assert_selected_iq_absent(rows)
    outside = tmp_path / "unrelated"
    outside.mkdir()
    (outside / "observation_4502.iq").write_bytes(b"not-opened")
    with pytest.raises(RuntimeError, match="already materialized"):
        plan_module._assert_selected_iq_absent(rows)
