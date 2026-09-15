"""Fail-closed publication and deployment readiness gates.

The evaluator deliberately separates a promising research result from a
release claim.  Missing, malformed, or merely diagnostic evidence never passes
implicitly.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import math
from typing import Mapping, Sequence


EVIDENCE_SCHEMA_VERSION = "telemetry-yield-release-evidence-v1"
ASSESSMENT_SCHEMA_VERSION = "telemetry-yield-release-assessment-v1"
CLAIM_TYPES = frozenset({"noninferiority", "incremental_union", "superiority"})


@dataclass(frozen=True, slots=True)
class ReleaseCriteria:
    minimum_independent_observations: int = 30
    minimum_missions: int = 3
    minimum_trusted_frames: int = 30
    minimum_deterministic_repeats: int = 2
    minimum_negative_control_hours: float = 30.0
    maximum_false_accept_upper95_per_hour: float = 0.1
    noninferiority_margin_frames_per_observation: float = 0.0
    maximum_peak_rss_bytes: int = 512 * 1024 * 1024
    minimum_streaming_test_bytes: int = 4 * 1024 * 1024 * 1024

    def __post_init__(self) -> None:
        integer_fields = (
            self.minimum_independent_observations,
            self.minimum_missions,
            self.minimum_trusted_frames,
            self.minimum_deterministic_repeats,
            self.maximum_peak_rss_bytes,
            self.minimum_streaming_test_bytes,
        )
        if any(isinstance(value, bool) or value <= 0 for value in integer_fields):
            raise ValueError("integer release criteria must be positive")
        finite_nonnegative = (
            self.minimum_negative_control_hours,
            self.maximum_false_accept_upper95_per_hour,
            self.noninferiority_margin_frames_per_observation,
        )
        if any(not math.isfinite(value) or value < 0 for value in finite_nonnegative):
            raise ValueError("floating release criteria must be finite and nonnegative")
        if self.minimum_negative_control_hours == 0:
            raise ValueError("minimum negative-control exposure must be positive")


def poisson_rate_upper95(events: int, exposure_hours: float) -> float:
    """One-sided 95% Poisson-rate upper bound without optional dependencies.

    For zero events this is the exact ``-log(0.05)/exposure`` bound.  For a
    positive count, a conservative Wilson-Hilferty chi-square approximation is
    used and explicitly identified in the assessment.
    """

    if isinstance(events, bool) or not isinstance(events, int) or events < 0:
        raise ValueError("events must be a nonnegative integer")
    if not math.isfinite(exposure_hours) or exposure_hours <= 0:
        raise ValueError("exposure_hours must be finite and positive")
    if events == 0:
        return -math.log(0.05) / exposure_hours
    # Upper confidence limit for a Poisson mean is 0.5*chi2(0.95, 2(k+1)).
    # Wilson-Hilferty with z_0.95 avoids a mandatory scipy runtime dependency.
    degrees = 2.0 * (events + 1)
    z95 = 1.6448536269514722
    chi_square = degrees * (
        1.0 - 2.0 / (9.0 * degrees) + z95 * math.sqrt(2.0 / (9.0 * degrees))
    ) ** 3
    return 0.5 * chi_square / exposure_hours


def _mapping(value: object, name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be an object")
    return value


def _boolean(value: object, name: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{name} must be a boolean")
    return value


def _integer(value: object, name: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"{name} must be an integer >= {minimum}")
    return value


def _number(value: object, name: str, *, minimum: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be numeric")
    result = float(value)
    if not math.isfinite(result) or (minimum is not None and result < minimum):
        raise ValueError(f"{name} is outside its finite range")
    return result


def _string(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a nonempty string")
    return value


def _interval(value: object, name: str) -> tuple[float, float]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)) or len(value) != 2:
        raise ValueError(f"{name} must contain exactly two numbers")
    lower = _number(value[0], f"{name}[0]")
    upper = _number(value[1], f"{name}[1]")
    if lower > upper:
        raise ValueError(f"{name} must be ordered")
    return lower, upper


def _gate(identifier: str, passed: bool, detail: str) -> dict[str, object]:
    return {"id": identifier, "passed": bool(passed), "detail": detail}


def assess_release_readiness(
    evidence: Mapping[str, object],
    *,
    criteria: ReleaseCriteria = ReleaseCriteria(),
) -> dict[str, object]:
    """Assess publication and deployment independently under strict schemas."""

    if evidence.get("schema_version") != EVIDENCE_SCHEMA_VERSION:
        raise ValueError("unsupported or missing release evidence schema")
    claim_type = _string(evidence.get("claim_type"), "claim_type")
    if claim_type not in CLAIM_TYPES:
        raise ValueError("claim_type is unsupported")
    research = _mapping(evidence.get("research"), "research")
    deployment = _mapping(evidence.get("deployment"), "deployment")
    publication = _mapping(evidence.get("publication"), "publication")

    observations = _integer(
        research.get("independent_observations"), "research.independent_observations"
    )
    missions = _integer(research.get("missions"), "research.missions")
    trusted_frames = _integer(
        research.get("trusted_frames"), "research.trusted_frames"
    )
    deterministic_repeats = _integer(
        research.get("deterministic_repeats"), "research.deterministic_repeats"
    )
    false_accepts = _integer(
        research.get("negative_control_false_accepts"),
        "research.negative_control_false_accepts",
    )
    negative_hours = _number(
        research.get("negative_control_hours"),
        "research.negative_control_hours",
        minimum=0.0,
    )
    interval = _interval(
        research.get("paired_gain_ci95_frames_per_observation"),
        "research.paired_gain_ci95_frames_per_observation",
    )
    false_accept_upper = (
        poisson_rate_upper95(false_accepts, negative_hours)
        if negative_hours > 0
        else math.inf
    )

    if claim_type in {"incremental_union", "superiority"}:
        statistics_pass = interval[0] > 0.0
        statistics_detail = f"gain CI lower bound {interval[0]:.6g} must be > 0"
    else:
        statistics_pass = interval[0] >= -criteria.noninferiority_margin_frames_per_observation
        statistics_detail = (
            f"gain CI lower bound {interval[0]:.6g} must be >= "
            f"{-criteria.noninferiority_margin_frames_per_observation:.6g}"
        )

    research_gates = [
        _gate(
            "R1_independent_blind_holdout",
            _boolean(research.get("independent_blind_holdout"), "research.independent_blind_holdout")
            and _boolean(research.get("position_blind"), "research.position_blind")
            and not _boolean(research.get("oracle_used"), "research.oracle_used"),
            "holdout must be independent, position-blind and oracle-free",
        ),
        _gate(
            "R2_same_iq_executable_baseline",
            _boolean(research.get("same_iq_executable_baseline"), "research.same_iq_executable_baseline"),
            "baseline and candidate must execute on the exact same source bytes",
        ),
        _gate(
            "R3_sample_size",
            observations >= criteria.minimum_independent_observations
            and missions >= criteria.minimum_missions
            and trusted_frames >= criteria.minimum_trusted_frames,
            f"observations={observations}, missions={missions}, trusted_frames={trusted_frames}",
        ),
        _gate("R4_paired_statistics", statistics_pass, statistics_detail),
        _gate(
            "R5_negative_controls",
            negative_hours >= criteria.minimum_negative_control_hours
            and false_accept_upper <= criteria.maximum_false_accept_upper95_per_hour,
            (
                f"false accepts={false_accepts}, hours={negative_hours:.6g}, "
                f"upper95={false_accept_upper:.6g}/h"
            ),
        ),
        _gate(
            "R6_protocol_validation",
            research.get("protocol_validation") == "strict_integrity_and_structure",
            "accepted frames require link integrity and protocol structure",
        ),
        _gate(
            "R7_reproducibility",
            deterministic_repeats >= criteria.minimum_deterministic_repeats
            and _boolean(research.get("source_hashes_complete"), "research.source_hashes_complete")
            and _boolean(research.get("config_hashes_complete"), "research.config_hashes_complete")
            and _boolean(research.get("environment_locked"), "research.environment_locked"),
            f"deterministic repeats={deterministic_repeats}",
        ),
    ]

    peak_rss = _integer(deployment.get("peak_rss_bytes"), "deployment.peak_rss_bytes")
    longest_input = _integer(
        deployment.get("longest_streaming_input_bytes"),
        "deployment.longest_streaming_input_bytes",
    )
    deployment_gates = [
        _gate(
            "D1_streaming_bounded_memory",
            _boolean(deployment.get("streaming"), "deployment.streaming")
            and peak_rss <= criteria.maximum_peak_rss_bytes
            and longest_input >= criteria.minimum_streaming_test_bytes,
            f"peak_rss={peak_rss}, longest_input={longest_input}",
        ),
        _gate(
            "D2_reference_free_runtime",
            _boolean(deployment.get("reference_free_runtime"), "deployment.reference_free_runtime"),
            "runtime must not load benchmark truth or expected payloads",
        ),
        _gate(
            "D3_resume_idempotence",
            _boolean(deployment.get("resumable"), "deployment.resumable")
            and _boolean(deployment.get("idempotent"), "deployment.idempotent"),
            "interrupted work resumes without duplicating accepted payloads",
        ),
        _gate(
            "D4_fail_closed_inputs",
            _boolean(deployment.get("malformed_input_rejected"), "deployment.malformed_input_rejected")
            and _boolean(deployment.get("unknown_profile_rejected"), "deployment.unknown_profile_rejected"),
            "malformed inputs and unknown profiles must fail closed",
        ),
        _gate(
            "D5_stable_contracts",
            _boolean(deployment.get("versioned_api"), "deployment.versioned_api")
            and _boolean(deployment.get("versioned_output_schema"), "deployment.versioned_output_schema"),
            "API and output schema are versioned",
        ),
        _gate(
            "D6_isolated_backends",
            _boolean(deployment.get("external_process_timeout"), "deployment.external_process_timeout")
            and _boolean(deployment.get("external_process_tree_cleanup"), "deployment.external_process_tree_cleanup"),
            "external decoders are bounded and their process trees are cleaned up",
        ),
        _gate(
            "D7_release_tests",
            _boolean(deployment.get("full_test_suite_passed"), "deployment.full_test_suite_passed")
            and _boolean(deployment.get("supported_protocol_golden_tests"), "deployment.supported_protocol_golden_tests")
            and _boolean(deployment.get("deterministic_replay_tests"), "deployment.deterministic_replay_tests"),
            "full, protocol-golden and deterministic replay tests must pass",
        ),
    ]

    publication_gates = [
        _gate(
            "P1_methods_and_limitations",
            _boolean(publication.get("methods_complete"), "publication.methods_complete")
            and _boolean(publication.get("limitations_complete"), "publication.limitations_complete"),
            "methods and limitations must match executable evidence",
        ),
        _gate(
            "P2_data_and_artifact_cards",
            _boolean(publication.get("data_card_complete"), "publication.data_card_complete")
            and _boolean(publication.get("artifact_manifest_complete"), "publication.artifact_manifest_complete"),
            "data card and immutable artifact manifest are required",
        ),
        _gate(
            "P3_external_audit",
            _boolean(publication.get("independent_audit_complete"), "publication.independent_audit_complete"),
            "an independent audit must reproduce the primary claim",
        ),
        _gate(
            "P4_human_release_attestations",
            _boolean(publication.get("source_permissions_attested"), "publication.source_permissions_attested")
            and _boolean(publication.get("secret_scan_passed"), "publication.secret_scan_passed")
            and _boolean(publication.get("attribution_complete"), "publication.attribution_complete"),
            "human source-permission attestation, secret scan and attribution are required",
        ),
    ]

    research_ready = all(bool(gate["passed"]) for gate in research_gates)
    deployment_ready = all(bool(gate["passed"]) for gate in deployment_gates)
    publication_package_ready = all(bool(gate["passed"]) for gate in publication_gates)
    blockers = [
        str(gate["id"])
        for gate in (*research_gates, *deployment_gates, *publication_gates)
        if not gate["passed"]
    ]
    return {
        "schema_version": ASSESSMENT_SCHEMA_VERSION,
        "claim_type": claim_type,
        "criteria": asdict(criteria),
        "research": {"ready": research_ready, "gates": research_gates},
        "deployment": {"ready": deployment_ready, "gates": deployment_gates},
        "publication_package": {
            "ready": publication_package_ready,
            "gates": publication_gates,
        },
        "false_accept_upper95_per_hour": (
            false_accept_upper if math.isfinite(false_accept_upper) else None
        ),
        "ready_for_publication": research_ready and publication_package_ready,
        "ready_for_deployment": research_ready and deployment_ready,
        "blockers": blockers,
    }
