"""Fail-closed planning for a small, offline A2 IQ ingest.

This module deliberately performs no network I/O.  It validates already-cached
recordings, freezes their conversion contracts, preflights derived-artifact
storage, and writes a content-addressed manifest that cannot be overwritten
with different bytes.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Mapping, Sequence

from telemetry_yield.canonical import canonical_json
from telemetry_yield.download_guard import assert_storage_capacity
from telemetry_yield.events import EventCandidate, same_transmission_event

MAX_INGEST_CONCURRENCY = 2
MAX_IQ_RECORDINGS_PER_PLAN = 2
ALLOWED_DOPPLER_STATES = frozenset({"pre_correction", "post_correction"})
ALLOWED_SPECTRAL_SIGNS = frozenset({-1, 1})


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True, slots=True)
class TimeBasis:
    """Explicit affine mapping from raw-sample time to a named time reference."""

    reference: str
    raw_seconds_to_reference_scale: float
    raw_start_offset_seconds: float
    evidence: str

    def blockers(self) -> tuple[str, ...]:
        blockers: list[str] = []
        if not isinstance(self.reference, str) or not self.reference.strip():
            blockers.append("missing_time_basis_reference")
        if (
            isinstance(self.raw_seconds_to_reference_scale, bool)
            or not isinstance(self.raw_seconds_to_reference_scale, (int, float))
            or not math.isfinite(self.raw_seconds_to_reference_scale)
            or self.raw_seconds_to_reference_scale <= 0
        ):
            blockers.append("invalid_time_basis_scale")
        if (
            isinstance(self.raw_start_offset_seconds, bool)
            or not isinstance(self.raw_start_offset_seconds, (int, float))
            or not math.isfinite(self.raw_start_offset_seconds)
        ):
            blockers.append("invalid_time_basis_offset")
        if not isinstance(self.evidence, str) or not self.evidence.strip():
            blockers.append("missing_time_basis_evidence")
        return tuple(blockers)

    def as_dict(self) -> dict[str, object]:
        return {
            "evidence": self.evidence,
            "raw_seconds_to_reference_scale": self.raw_seconds_to_reference_scale,
            "raw_start_offset_seconds": self.raw_start_offset_seconds,
            "reference": self.reference,
        }


@dataclass(frozen=True, slots=True)
class ConversionEvidence:
    """Per-recording facts that must be resolved before IQ conversion."""

    sample_rate_hz: int | float | None = None
    doppler_state: str | None = None
    spectral_sign: int | None = None
    time_basis: TimeBasis | None = None
    evidence_refs: tuple[str, ...] = ()

    def blockers(self) -> tuple[str, ...]:
        blockers: list[str] = []
        if (
            isinstance(self.sample_rate_hz, bool)
            or not isinstance(self.sample_rate_hz, (int, float))
            or not math.isfinite(self.sample_rate_hz)
            or self.sample_rate_hz <= 0
        ):
            blockers.append("missing_or_invalid_sample_rate_hz")
        if self.doppler_state not in ALLOWED_DOPPLER_STATES:
            blockers.append("missing_or_unknown_doppler_state")
        if self.spectral_sign not in ALLOWED_SPECTRAL_SIGNS:
            blockers.append("missing_or_invalid_spectral_sign")
        if self.time_basis is None:
            blockers.append("missing_time_basis")
        else:
            blockers.extend(self.time_basis.blockers())
        if not self.evidence_refs or any(
            not isinstance(reference, str) or not reference.strip()
            for reference in self.evidence_refs
        ):
            blockers.append("missing_conversion_evidence_refs")
        return tuple(blockers)

    def as_dict(self) -> dict[str, object]:
        return {
            "doppler_state": self.doppler_state,
            "evidence_refs": list(self.evidence_refs),
            "sample_rate_hz": self.sample_rate_hz,
            "spectral_sign": self.spectral_sign,
            "time_basis": self.time_basis.as_dict() if self.time_basis else None,
        }


@dataclass(frozen=True, slots=True)
class IngestCandidate:
    """One local recording plus the cached metadata needed to plan its ingest."""

    observation_id: str
    source_url: str
    source_path: Path
    content_length_bytes: int
    content_sha256: str | None
    metadata_sha256: str | None
    conversion: ConversionEvidence = field(default_factory=ConversionEvidence)
    norad_id: int | None = None
    transmitter_uuid: str | None = None
    start_utc: datetime | None = None
    end_utc: datetime | None = None
    station_id: str | None = None
    payload_hashes: frozenset[str] = frozenset()

    def as_event_candidate(self) -> EventCandidate:
        """Return the established event model, rejecting incomplete identity/time."""

        if self.norad_id is None or self.norad_id <= 0:
            raise ValueError("norad_id is required for event grouping")
        if not self.transmitter_uuid or not self.transmitter_uuid.strip():
            raise ValueError("transmitter_uuid is required for event grouping")
        if self.start_utc is None or self.end_utc is None:
            raise ValueError("start_utc and end_utc are required for event grouping")
        if not self.station_id or not self.station_id.strip():
            raise ValueError("station_id is required for event grouping")
        return EventCandidate(
            observation_id=self.observation_id,
            norad_id=self.norad_id,
            transmitter_uuid=self.transmitter_uuid,
            start_utc=self.start_utc,
            end_utc=self.end_utc,
            station_id=self.station_id,
            payload_hashes=self.payload_hashes,
        )


@dataclass(frozen=True, slots=True)
class IngestDecision:
    candidate: IngestCandidate
    blockers: tuple[str, ...]

    @property
    def ready(self) -> bool:
        return not self.blockers

    def as_dict(self) -> dict[str, object]:
        candidate = self.candidate
        return {
            "blockers": list(self.blockers),
            "content_length_bytes": candidate.content_length_bytes,
            "content_sha256": candidate.content_sha256,
            "conversion_contract": candidate.conversion.as_dict(),
            "event_identity": {
                "end_utc": (
                    candidate.end_utc.isoformat() if candidate.end_utc else None
                ),
                "norad_id": candidate.norad_id,
                "start_utc": (
                    candidate.start_utc.isoformat() if candidate.start_utc else None
                ),
                "station_id": candidate.station_id,
                "transmitter_uuid": candidate.transmitter_uuid,
            },
            "metadata_sha256": candidate.metadata_sha256,
            "observation_id": candidate.observation_id,
            "ready_for_iq_conversion": self.ready,
            "source_path": str(candidate.source_path),
            "source_url": candidate.source_url,
        }


@dataclass(frozen=True, slots=True)
class IngestPlan:
    campaign_id: str
    destination: Path
    max_concurrency: int
    max_recordings: int
    decisions: tuple[IngestDecision, ...]
    required_derived_bytes: int
    storage_free_bytes_before: int | None
    storage_preflight_passed: bool
    storage_error: str | None

    @property
    def ready_count(self) -> int:
        return sum(decision.ready for decision in self.decisions)

    @property
    def blocked_count(self) -> int:
        return len(self.decisions) - self.ready_count

    def as_manifest(
        self, *, source_snapshots: Mapping[str, str]
    ) -> dict[str, object]:
        invalid_snapshots = [
            name for name, digest in source_snapshots.items() if not _is_sha256(digest)
        ]
        if invalid_snapshots:
            raise ValueError(
                "source snapshot hashes must be lowercase SHA-256: "
                + ", ".join(sorted(invalid_snapshots))
            )
        return {
            "campaign_id": self.campaign_id,
            "counts": {
                "blocked": self.blocked_count,
                "ready": self.ready_count,
                "total": len(self.decisions),
            },
            "destination": str(self.destination),
            "manifest_version": 1,
            "policy": {
                "download_iq": False,
                "external_contact": False,
                "mass_download": False,
                "max_concurrency": self.max_concurrency,
                "max_recordings": self.max_recordings,
                "offline_cached_metadata_only": True,
                "publication": False,
            },
            "recordings": [decision.as_dict() for decision in self.decisions],
            "source_snapshots": dict(sorted(source_snapshots.items())),
            "storage_preflight": {
                "error": self.storage_error,
                "free_bytes_before": self.storage_free_bytes_before,
                "passed": self.storage_preflight_passed,
                "required_derived_bytes": self.required_derived_bytes,
            },
        }


def _candidate_blockers(
    candidate: IngestCandidate, *, verify_local_content: bool
) -> tuple[str, ...]:
    blockers: list[str] = []
    if not isinstance(candidate.observation_id, str) or not candidate.observation_id.strip():
        blockers.append("missing_observation_id")
    if not isinstance(candidate.source_url, str) or not candidate.source_url.strip():
        blockers.append("missing_source_url")
    if candidate.content_length_bytes <= 0:
        blockers.append("missing_or_invalid_content_length")
    if not _is_sha256(candidate.content_sha256):
        blockers.append("missing_or_invalid_content_sha256")
    if not _is_sha256(candidate.metadata_sha256):
        blockers.append("missing_or_invalid_metadata_sha256")
    blockers.extend(candidate.conversion.blockers())

    if verify_local_content:
        if not candidate.source_path.is_file():
            blockers.append("local_content_missing")
        else:
            if candidate.source_path.stat().st_size != candidate.content_length_bytes:
                blockers.append("local_content_length_mismatch")
            if _is_sha256(candidate.content_sha256):
                actual_sha256 = _sha256_file(candidate.source_path)
                if actual_sha256 != candidate.content_sha256:
                    blockers.append("local_content_sha256_mismatch")
    return tuple(dict.fromkeys(blockers))


def plan_bounded_ingest(
    candidates: Sequence[IngestCandidate],
    destination: Path,
    *,
    campaign_id: str = "a2-bounded-ingest",
    max_concurrency: int = MAX_INGEST_CONCURRENCY,
    max_recordings: int = MAX_IQ_RECORDINGS_PER_PLAN,
    free_bytes: int | None = None,
    verify_local_content: bool = True,
) -> IngestPlan:
    """Validate at most two local IQ recordings and preflight conversion space."""

    if not isinstance(campaign_id, str) or not campaign_id.strip():
        raise ValueError("campaign_id must be non-empty")
    if isinstance(max_concurrency, bool) or not 1 <= max_concurrency <= 2:
        raise ValueError("max_concurrency must be between 1 and 2")
    if isinstance(max_recordings, bool) or not 1 <= max_recordings <= 2:
        raise ValueError("max_recordings must be between 1 and 2")
    if len(candidates) > max_recordings:
        raise PermissionError(
            f"bounded ingest allows at most {max_recordings} IQ recordings"
        )
    observation_ids = [candidate.observation_id for candidate in candidates]
    if len(observation_ids) != len(set(observation_ids)):
        raise ValueError("observation_id values must be unique within a plan")

    decisions = tuple(
        IngestDecision(
            candidate=candidate,
            blockers=_candidate_blockers(
                candidate, verify_local_content=verify_local_content
            ),
        )
        for candidate in sorted(candidates, key=lambda item: item.observation_id)
    )
    required_bytes = sum(
        decision.candidate.content_length_bytes
        for decision in decisions
        if decision.ready
    )
    storage_passed = True
    storage_error: str | None = None
    if required_bytes:
        try:
            assert_storage_capacity(required_bytes, destination, free_bytes=free_bytes)
        except OSError as error:
            storage_passed = False
            storage_error = str(error)
            decisions = tuple(
                replace(
                    decision,
                    blockers=decision.blockers + ("storage_preflight_failed",),
                )
                if decision.ready
                else decision
                for decision in decisions
            )

    return IngestPlan(
        campaign_id=campaign_id,
        destination=destination,
        max_concurrency=max_concurrency,
        max_recordings=max_recordings,
        decisions=decisions,
        required_derived_bytes=required_bytes,
        storage_free_bytes_before=free_bytes,
        storage_preflight_passed=storage_passed,
        storage_error=storage_error,
    )


def write_immutable_manifest(
    path: Path,
    plan: IngestPlan,
    *,
    source_snapshots: Mapping[str, str],
) -> str:
    """Create a canonical manifest once; a different rewrite is rejected."""

    payload = (canonical_json(plan.as_manifest(source_snapshots=source_snapshots)) + "\n").encode(
        "utf-8"
    )
    if path.exists():
        if path.read_bytes() != payload:
            raise FileExistsError(f"immutable manifest already exists with different content: {path}")
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with path.open("xb") as handle:
                handle.write(payload)
        except FileExistsError:
            if path.read_bytes() != payload:
                raise
    return hashlib.sha256(payload).hexdigest()


@dataclass(frozen=True, slots=True)
class EventMatchEvidence:
    """Independent physical evidence for a proposed cross-recording match."""

    doppler_tracks_consistent: bool = False
    geometric_visibility_consistent: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.doppler_tracks_consistent, bool):
            raise TypeError("doppler_tracks_consistent must be a boolean")
        if not isinstance(self.geometric_visibility_consistent, bool):
            raise TypeError("geometric_visibility_consistent must be a boolean")

    @property
    def physical_evidence_present(self) -> bool:
        return self.doppler_tracks_consistent or self.geometric_visibility_consistent


def same_bounded_transmission_event(
    left: EventCandidate,
    right: EventCandidate,
    *,
    evidence: EventMatchEvidence,
    time_tolerance: timedelta,
) -> bool:
    """Require identity, bounded overlap, and Doppler/geometric evidence.

    Matching payload hashes are optional corroboration.  When both sides have
    payload hashes, a disagreement fails closed; payload agreement never
    substitutes for physical evidence.
    """

    if time_tolerance < timedelta(0):
        raise ValueError("time_tolerance must be non-negative")
    if left.observation_id == right.observation_id:
        return False
    if not evidence.physical_evidence_present:
        return False
    if not same_transmission_event(
        left,
        right,
        time_tolerance=time_tolerance,
        doppler_tracks_consistent=True,
    ):
        return False
    if (
        left.payload_hashes
        and right.payload_hashes
        and not left.payload_hashes.intersection(right.payload_hashes)
    ):
        return False
    return True


def group_transmission_events(
    candidates: Sequence[EventCandidate],
    evidence_by_pair: Mapping[tuple[str, str], EventMatchEvidence],
    *,
    time_tolerance: timedelta,
) -> tuple[tuple[EventCandidate, ...], ...]:
    """Complete-link grouping; every member pair must independently match."""

    if time_tolerance < timedelta(0):
        raise ValueError("time_tolerance must be non-negative")
    observation_ids = [candidate.observation_id for candidate in candidates]
    if len(observation_ids) != len(set(observation_ids)):
        raise ValueError("event candidates must have unique observation_id values")

    groups: list[list[EventCandidate]] = []
    ordered = sorted(
        candidates, key=lambda item: (item.start_utc, item.observation_id)
    )
    for candidate in ordered:
        eligible: list[list[EventCandidate]] = []
        for group in groups:
            matches_all = True
            for member in group:
                pair = tuple(sorted((candidate.observation_id, member.observation_id)))
                evidence = evidence_by_pair.get(pair, EventMatchEvidence())
                if not same_bounded_transmission_event(
                    candidate,
                    member,
                    evidence=evidence,
                    time_tolerance=time_tolerance,
                ):
                    matches_all = False
                    break
            if matches_all:
                eligible.append(group)
        if len(eligible) == 1:
            eligible[0].append(candidate)
        else:
            groups.append([candidate])
    return tuple(tuple(group) for group in groups)
