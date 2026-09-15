from __future__ import annotations

from copy import deepcopy

import pytest

from telemetry_yield.release_readiness import (
    EVIDENCE_SCHEMA_VERSION,
    ReleaseCriteria,
    assess_release_readiness,
    poisson_rate_upper95,
)


def passing_evidence() -> dict[str, object]:
    return {
        "schema_version": EVIDENCE_SCHEMA_VERSION,
        "claim_type": "incremental_union",
        "research": {
            "independent_blind_holdout": True,
            "position_blind": True,
            "oracle_used": False,
            "same_iq_executable_baseline": True,
            "independent_observations": 30,
            "missions": 3,
            "trusted_frames": 40,
            "paired_gain_ci95_frames_per_observation": [0.01, 0.25],
            "negative_control_false_accepts": 0,
            "negative_control_hours": 31.0,
            "protocol_validation": "strict_integrity_and_structure",
            "deterministic_repeats": 2,
            "source_hashes_complete": True,
            "config_hashes_complete": True,
            "environment_locked": True,
        },
        "deployment": {
            "streaming": True,
            "peak_rss_bytes": 100_000_000,
            "longest_streaming_input_bytes": 5_000_000_000,
            "reference_free_runtime": True,
            "resumable": True,
            "idempotent": True,
            "malformed_input_rejected": True,
            "unknown_profile_rejected": True,
            "versioned_api": True,
            "versioned_output_schema": True,
            "external_process_timeout": True,
            "external_process_tree_cleanup": True,
            "full_test_suite_passed": True,
            "supported_protocol_golden_tests": True,
            "deterministic_replay_tests": True,
        },
        "publication": {
            "methods_complete": True,
            "limitations_complete": True,
            "data_card_complete": True,
            "artifact_manifest_complete": True,
            "independent_audit_complete": True,
            "source_permissions_attested": True,
            "secret_scan_passed": True,
            "attribution_complete": True,
        },
    }


def test_complete_evidence_passes_both_release_gates() -> None:
    result = assess_release_readiness(passing_evidence())
    assert result["ready_for_publication"] is True
    assert result["ready_for_deployment"] is True
    assert result["blockers"] == []
    assert result["false_accept_upper95_per_hour"] < 0.1


def test_oracle_and_insufficient_real_iq_fail_research_gate() -> None:
    evidence = passing_evidence()
    research = evidence["research"]
    assert isinstance(research, dict)
    research["oracle_used"] = True
    research["independent_observations"] = 1
    research["trusted_frames"] = 10
    result = assess_release_readiness(evidence)
    assert result["ready_for_publication"] is False
    assert result["ready_for_deployment"] is False
    assert "R1_independent_blind_holdout" in result["blockers"]
    assert "R3_sample_size" in result["blockers"]


def test_deployment_failure_does_not_erase_research_readiness() -> None:
    evidence = passing_evidence()
    deployment = evidence["deployment"]
    assert isinstance(deployment, dict)
    deployment["streaming"] = False
    result = assess_release_readiness(evidence)
    assert result["research"]["ready"] is True
    assert result["ready_for_publication"] is True
    assert result["ready_for_deployment"] is False
    assert "D1_streaming_bounded_memory" in result["blockers"]


def test_missing_or_wrong_typed_fields_fail_closed() -> None:
    evidence = passing_evidence()
    research = evidence["research"]
    assert isinstance(research, dict)
    del research["position_blind"]
    with pytest.raises(ValueError, match="position_blind"):
        assess_release_readiness(evidence)
    evidence = passing_evidence()
    deployment = evidence["deployment"]
    assert isinstance(deployment, dict)
    deployment["peak_rss_bytes"] = True
    with pytest.raises(ValueError, match="peak_rss_bytes"):
        assess_release_readiness(evidence)


def test_claim_interval_rules_are_explicit() -> None:
    evidence = passing_evidence()
    research = evidence["research"]
    assert isinstance(research, dict)
    research["paired_gain_ci95_frames_per_observation"] = [0.0, 0.1]
    assert "R4_paired_statistics" in assess_release_readiness(evidence)["blockers"]
    evidence["claim_type"] = "noninferiority"
    assert "R4_paired_statistics" not in assess_release_readiness(evidence)["blockers"]


def test_zero_event_poisson_bound_and_custom_memory_limit() -> None:
    assert poisson_rate_upper95(0, 30.0) == pytest.approx(0.09985774245)
    evidence = passing_evidence()
    deployment = evidence["deployment"]
    assert isinstance(deployment, dict)
    deployment["peak_rss_bytes"] = 100_000_000
    result = assess_release_readiness(
        evidence,
        criteria=ReleaseCriteria(maximum_peak_rss_bytes=50_000_000),
    )
    assert "D1_streaming_bounded_memory" in result["blockers"]


def test_zero_negative_exposure_is_a_blocker_not_an_unassessable_document() -> None:
    evidence = passing_evidence()
    research = evidence["research"]
    assert isinstance(research, dict)
    research["negative_control_hours"] = 0.0
    result = assess_release_readiness(evidence)
    assert result["false_accept_upper95_per_hour"] is None
    assert "R5_negative_controls" in result["blockers"]
