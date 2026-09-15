"""SQLite persistence for TLE versions and immutable plan revisions."""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterator

from .models import ObservationPlan, Opportunity, ProbabilityEstimate, TleSnapshot
from .models import as_utc
from .probability import ReceptionEvidence


@dataclass(frozen=True, slots=True)
class StoredReceptionEvidence:
    evidence_id: str
    observed_at: datetime
    evidence: ReceptionEvidence
    source_observation_id: str | None = None


def _opportunity_document(item: Opportunity) -> dict[str, object]:
    return {
        "opportunity_id": item.opportunity_id,
        "norad_id": item.norad_id,
        "satellite_name": item.satellite_name,
        "station_id": item.station_id,
        "resource_id": item.resource_id,
        "start": item.start.isoformat(),
        "end": item.end.isoformat(),
        "tle_fingerprint": item.tle_fingerprint,
        "probability": {
            "p_transmit": item.probability.p_transmit,
            "p_decode_given_transmit": item.probability.p_decode_given_transmit,
            "p_signal_present": item.probability.p_signal_present,
            "p_decode_given_signal": item.probability.p_decode_given_signal,
            "p_success": item.probability.p_success,
            "standard_deviation": item.probability.standard_deviation,
            "lower_90": item.probability.lower_90,
            "upper_90": item.probability.upper_90,
            "missing_features": list(item.probability.missing_features),
            "evidence_count": item.probability.evidence_count,
            "model_version": item.probability.model_version,
        },
        "nominal_unique_samples": item.nominal_unique_samples,
        "expected_unique_samples": item.expected_unique_samples,
        "priority": item.priority,
        "max_elevation_deg": item.max_elevation_deg,
        "min_range_km": item.min_range_km,
        "frequency_hz": item.frequency_hz,
        "modulation": item.modulation,
        "transmitter_uuid": item.transmitter_uuid,
        "satnogs_station_id": item.satnogs_station_id,
        "exclusive_transmission": item.exclusive_transmission,
        "metadata": dict(item.metadata),
    }


def plan_document(plan: ObservationPlan) -> dict[str, object]:
    return {
        "schema_version": "observation-plan-v2",
        "plan_id": plan.plan_id,
        "revision": plan.revision,
        "created_at": plan.created_at.isoformat(),
        "horizon_start": plan.horizon_start.isoformat(),
        "horizon_end": plan.horizon_end.isoformat(),
        "assignments": [_opportunity_document(item) for item in plan.assignments],
        "tle_fingerprints": {
            str(key): value for key, value in sorted(plan.tle_fingerprints.items())
        },
        "objective_value": plan.objective_value,
        "solver": plan.solver,
        "trigger": plan.trigger,
        "diagnostics": dict(plan.diagnostics),
        "canonical_fingerprint": plan.canonical_fingerprint(),
    }


def _legacy_plan_fingerprint(plan: ObservationPlan) -> str:
    """Verify already persisted v1 plans without weakening new v2 documents."""

    payload = {
        "horizon_start": plan.horizon_start.isoformat(),
        "horizon_end": plan.horizon_end.isoformat(),
        "assignments": [
            {
                "opportunity_id": item.opportunity_id,
                "norad_id": item.norad_id,
                "station_id": item.station_id,
                "resource_id": item.resource_id,
                "start": item.start.isoformat(),
                "end": item.end.isoformat(),
                "tle_fingerprint": item.tle_fingerprint,
                "p_success": float(item.probability.p_success),
                "nominal_unique_samples": float(item.nominal_unique_samples),
                "expected_unique_samples": float(item.expected_unique_samples),
                "priority": float(item.priority),
                "frequency_hz": (
                    float(item.frequency_hz)
                    if item.frequency_hz is not None
                    else None
                ),
                "transmitter_uuid": item.transmitter_uuid,
                "satnogs_station_id": item.satnogs_station_id,
                "exclusive_transmission": item.exclusive_transmission,
            }
            for item in plan.assignments
        ],
        "tle_fingerprints": dict(sorted(plan.tle_fingerprints.items())),
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def plan_from_document(document: dict[str, object]) -> ObservationPlan:
    """Validate and reconstruct a stored/exported immutable plan revision."""

    schema_version = document.get("schema_version")
    if schema_version not in {"observation-plan-v1", "observation-plan-v2"}:
        raise ValueError("unsupported observation plan schema")
    raw_assignments = document.get("assignments")
    if not isinstance(raw_assignments, list):
        raise ValueError("observation plan assignments must be a list")
    assignments: list[Opportunity] = []
    for raw in raw_assignments:
        if not isinstance(raw, dict) or not isinstance(raw.get("probability"), dict):
            raise ValueError("malformed observation plan assignment")
        for name in (
            "opportunity_id",
            "satellite_name",
            "station_id",
            "resource_id",
            "start",
            "end",
            "tle_fingerprint",
        ):
            if not isinstance(raw.get(name), str) or not str(raw[name]).strip():
                raise ValueError(f"plan assignment {name} must be a non-empty string")
        raw_norad = raw.get("norad_id")
        if (
            isinstance(raw_norad, bool)
            or not isinstance(raw_norad, int)
            or raw_norad <= 0
        ):
            raise ValueError("plan assignment norad_id must be a positive integer")
        exclusive = raw.get("exclusive_transmission", True)
        if not isinstance(exclusive, bool):
            raise ValueError("exclusive_transmission must be boolean")
        raw_station = raw.get("satnogs_station_id")
        if raw_station is not None and (
            isinstance(raw_station, bool)
            or not isinstance(raw_station, int)
            or raw_station <= 0
        ):
            raise ValueError("satnogs_station_id must be a positive integer or null")
        if schema_version == "observation-plan-v2" and not isinstance(
            raw.get("metadata"), dict
        ):
            raise ValueError("v2 plan assignment metadata must be an object")
        probability = raw["probability"]
        if schema_version == "observation-plan-v2" and not {
            "p_signal_present",
            "p_decode_given_signal",
        } <= set(probability):
            raise ValueError("v2 plan probability aliases are required")
        for name in (
            "p_transmit",
            "p_decode_given_transmit",
            "p_success",
            "standard_deviation",
            "lower_90",
            "upper_90",
        ):
            value = probability.get(name)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"plan probability {name} must be numeric")
        for name in ("p_signal_present", "p_decode_given_signal"):
            if name in probability:
                value = probability[name]
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    raise ValueError(f"plan probability {name} must be numeric")
        for name in (
            "nominal_unique_samples",
            "expected_unique_samples",
            "priority",
            "max_elevation_deg",
            "min_range_km",
        ):
            value = raw.get(name)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"plan assignment {name} must be numeric")
        frequency = raw.get("frequency_hz")
        if frequency is not None and (
            isinstance(frequency, bool) or not isinstance(frequency, (int, float))
        ):
            raise ValueError("plan assignment frequency_hz must be numeric or null")
        if frequency is not None and (
            not math.isfinite(float(frequency)) or float(frequency) <= 0
        ):
            raise ValueError("plan assignment frequency_hz must be finite and positive")
        for name in ("modulation", "transmitter_uuid"):
            value = raw.get(name)
            if value is not None and (
                not isinstance(value, str) or not value.strip()
            ):
                raise ValueError(f"plan assignment {name} must be a non-empty string or null")
        missing_features = probability.get("missing_features", [])
        if not isinstance(missing_features, list) or any(
            not isinstance(name, str) or not name for name in missing_features
        ):
            raise ValueError("plan probability missing_features must contain non-empty strings")
        evidence_count = probability.get("evidence_count", 0)
        if (
            isinstance(evidence_count, bool)
            or not isinstance(evidence_count, int)
            or evidence_count < 0
        ):
            raise ValueError("plan probability evidence_count must be non-negative")
        if (
            not isinstance(probability.get("model_version"), str)
            or not str(probability["model_version"]).strip()
        ):
            raise ValueError("plan probability model_version must be a non-empty string")
        for legacy, explicit in (
            ("p_transmit", "p_signal_present"),
            ("p_decode_given_transmit", "p_decode_given_signal"),
        ):
            if (
                explicit in probability
                and float(probability[explicit]) != float(probability[legacy])
            ):
                raise ValueError(
                    f"probability aliases disagree: {legacy} != {explicit}"
                )
        estimate = ProbabilityEstimate(
            p_transmit=float(probability["p_transmit"]),
            p_decode_given_transmit=float(probability["p_decode_given_transmit"]),
            p_success=float(probability["p_success"]),
            standard_deviation=float(probability["standard_deviation"]),
            lower_90=float(probability["lower_90"]),
            upper_90=float(probability["upper_90"]),
            missing_features=tuple(missing_features),
            evidence_count=evidence_count,
            model_version=str(probability.get("model_version", "unknown")),
        )
        assignments.append(
            Opportunity(
                opportunity_id=str(raw["opportunity_id"]),
                norad_id=raw_norad,
                satellite_name=raw["satellite_name"],
                station_id=raw["station_id"],
                resource_id=raw["resource_id"],
                start=datetime.fromisoformat(raw["start"]),
                end=datetime.fromisoformat(raw["end"]),
                tle_fingerprint=raw["tle_fingerprint"],
                probability=estimate,
                nominal_unique_samples=float(raw["nominal_unique_samples"]),
                expected_unique_samples=float(raw["expected_unique_samples"]),
                priority=float(raw["priority"]),
                max_elevation_deg=float(raw["max_elevation_deg"]),
                min_range_km=float(raw["min_range_km"]),
                frequency_hz=(
                    float(raw["frequency_hz"])
                    if raw.get("frequency_hz") is not None
                    else None
                ),
                modulation=(
                    raw["modulation"] if raw.get("modulation") is not None else None
                ),
                transmitter_uuid=(
                    raw["transmitter_uuid"]
                    if raw.get("transmitter_uuid") is not None
                    else None
                ),
                satnogs_station_id=(
                    raw_station
                    if raw_station is not None
                    else None
                ),
                exclusive_transmission=exclusive,
                metadata=(
                    dict(raw["metadata"])
                    if isinstance(raw.get("metadata"), dict)
                    else {}
                ),
            )
        )
    for name in ("plan_id", "created_at", "horizon_start", "horizon_end", "solver", "trigger"):
        if not isinstance(document.get(name), str) or not str(document[name]).strip():
            raise ValueError(f"plan {name} must be a non-empty string")
    revision = document.get("revision")
    if isinstance(revision, bool) or not isinstance(revision, int) or revision <= 0:
        raise ValueError("plan revision must be a positive integer")
    objective_value = document.get("objective_value")
    if (
        isinstance(objective_value, bool)
        or not isinstance(objective_value, (int, float))
        or not math.isfinite(float(objective_value))
    ):
        raise ValueError("plan objective_value must be finite and numeric")
    raw_tle_fingerprints = document.get("tle_fingerprints")
    if not isinstance(raw_tle_fingerprints, dict):
        raise ValueError("plan tle_fingerprints must be an object")
    tle_fingerprints: dict[int, str] = {}
    for key, value in raw_tle_fingerprints.items():
        if (
            not isinstance(key, str)
            or not key.isdecimal()
            or int(key) <= 0
            or not isinstance(value, str)
            or len(value) != 64
            or any(character not in "0123456789abcdef" for character in value)
        ):
            raise ValueError("plan TLE fingerprints must map positive NORAD IDs to SHA-256 values")
        tle_fingerprints[int(key)] = value
    diagnostics = document.get("diagnostics", {})
    if not isinstance(diagnostics, dict):
        raise ValueError("plan diagnostics must be an object")
    plan = ObservationPlan(
        plan_id=document["plan_id"],
        revision=revision,
        created_at=datetime.fromisoformat(document["created_at"]),
        horizon_start=datetime.fromisoformat(document["horizon_start"]),
        horizon_end=datetime.fromisoformat(document["horizon_end"]),
        assignments=tuple(assignments),
        tle_fingerprints=tle_fingerprints,
        objective_value=float(objective_value),
        solver=document["solver"],
        trigger=document["trigger"],
        diagnostics=dict(diagnostics),
    )
    expected_fingerprint = document.get("canonical_fingerprint")
    if (
        not isinstance(expected_fingerprint, str)
        or len(expected_fingerprint) != 64
        or any(character not in "0123456789abcdef" for character in expected_fingerprint)
    ):
        raise ValueError("observation plan canonical fingerprint is required")
    actual_fingerprint = (
        _legacy_plan_fingerprint(plan)
        if schema_version == "observation-plan-v1"
        else plan.canonical_fingerprint()
    )
    if actual_fingerprint != expected_fingerprint:
        raise ValueError("observation plan canonical fingerprint mismatch")
    return plan


class PlanningStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connection() as connection:
            connection.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS tle_snapshots (
                    fingerprint TEXT PRIMARY KEY,
                    norad_id INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    line1 TEXT NOT NULL,
                    line2 TEXT NOT NULL,
                    epoch TEXT NOT NULL,
                    fetched_at TEXT NOT NULL,
                    source TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS tle_snapshots_latest
                    ON tle_snapshots(norad_id, fetched_at DESC);
                CREATE TABLE IF NOT EXISTS observation_plan_revisions (
                    plan_id TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    trigger TEXT NOT NULL,
                    canonical_fingerprint TEXT NOT NULL,
                    document_json TEXT NOT NULL,
                    PRIMARY KEY(plan_id, revision)
                );
                CREATE TABLE IF NOT EXISTS reception_evidence (
                    evidence_id TEXT PRIMARY KEY,
                    observed_at TEXT NOT NULL,
                    norad_id INTEGER NOT NULL CHECK (norad_id > 0),
                    station_id TEXT,
                    resource_id TEXT,
                    listened INTEGER NOT NULL CHECK (listened IN (0, 1)),
                    transmitter_state TEXT NOT NULL CHECK (
                        transmitter_state IN ('confirmed','expected','not_transmitting','unknown')
                    ),
                    decoded INTEGER CHECK (decoded IS NULL OR decoded IN (0, 1)),
                    signal_present INTEGER CHECK (
                        signal_present IS NULL OR signal_present IN (0, 1)
                    ),
                    source TEXT NOT NULL CHECK (source IN ('local','satnogs')),
                    weight REAL NOT NULL CHECK (weight > 0),
                    source_observation_id TEXT,
                    created_at TEXT NOT NULL,
                    UNIQUE(source, source_observation_id)
                );
                CREATE INDEX IF NOT EXISTS reception_evidence_history
                    ON reception_evidence(norad_id, observed_at DESC);
                """
            )

    def save_tle(self, snapshot: TleSnapshot) -> None:
        with self._connection() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO tle_snapshots "
                "(fingerprint,norad_id,name,line1,line2,epoch,fetched_at,source) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (
                    snapshot.fingerprint,
                    snapshot.norad_id,
                    snapshot.name,
                    snapshot.line1,
                    snapshot.line2,
                    snapshot.epoch.isoformat(),
                    snapshot.fetched_at.isoformat(),
                    snapshot.source,
                ),
            )

    def latest_tle(self, norad_id: int) -> TleSnapshot | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM tle_snapshots WHERE norad_id=? "
                "ORDER BY fetched_at DESC LIMIT 1",
                (norad_id,),
            ).fetchone()
        if row is None:
            return None
        return TleSnapshot(
            norad_id=row["norad_id"],
            name=row["name"],
            line1=row["line1"],
            line2=row["line2"],
            epoch=datetime.fromisoformat(row["epoch"]),
            fetched_at=datetime.fromisoformat(row["fetched_at"]),
            source=row["source"],
        )

    def save_plan(self, plan: ObservationPlan) -> None:
        document = plan_document(plan)
        encoded = json.dumps(document, sort_keys=True, separators=(",", ":"))
        with self._connection() as connection:
            try:
                connection.execute(
                    "INSERT INTO observation_plan_revisions "
                    "(plan_id,revision,created_at,trigger,canonical_fingerprint,document_json) "
                    "VALUES (?,?,?,?,?,?)",
                    (
                        plan.plan_id,
                        plan.revision,
                        plan.created_at.isoformat(),
                        plan.trigger,
                        document["canonical_fingerprint"],
                        encoded,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise RuntimeError("plan revisions are immutable") from exc

    def next_plan_revision(self, plan_id: str) -> int:
        """Return the next immutable revision number for one logical plan."""

        plan_id = plan_id.strip()
        if not plan_id:
            raise ValueError("plan_id must be non-empty")
        with self._connection() as connection:
            row = connection.execute(
                "SELECT COALESCE(MAX(revision), 0) AS latest_revision "
                "FROM observation_plan_revisions WHERE plan_id=?",
                (plan_id,),
            ).fetchone()
        if row is None:
            raise RuntimeError("plan revision query returned no row")
        return int(row["latest_revision"]) + 1

    def save_reception_evidence(
        self,
        evidence_id: str,
        observed_at: datetime,
        evidence: ReceptionEvidence,
        *,
        source_observation_id: str | None = None,
    ) -> None:
        evidence_id = evidence_id.strip()
        if not evidence_id:
            raise ValueError("evidence_id must be non-empty")
        observed_at = as_utc(observed_at, name="observed_at")
        with self._connection() as connection:
            try:
                connection.execute(
                    "INSERT INTO reception_evidence "
                    "(evidence_id,observed_at,norad_id,station_id,resource_id,listened,"
                    "transmitter_state,decoded,signal_present,source,weight,"
                    "source_observation_id,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        evidence_id,
                        observed_at.isoformat(),
                        evidence.norad_id,
                        evidence.station_id,
                        evidence.resource_id,
                        int(evidence.listened),
                        evidence.transmitter_state,
                        None if evidence.decoded is None else int(evidence.decoded),
                        (
                            None
                            if evidence.signal_present is None
                            else int(evidence.signal_present)
                        ),
                        evidence.source,
                        evidence.weight,
                        source_observation_id,
                        datetime.now(observed_at.tzinfo).isoformat(),
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise RuntimeError("reception evidence records are immutable") from exc

    def load_reception_evidence(
        self,
        *,
        norad_id: int | None = None,
        before: datetime | None = None,
        limit: int = 10_000,
    ) -> tuple[StoredReceptionEvidence, ...]:
        if norad_id is not None and norad_id <= 0:
            raise ValueError("norad_id must be positive")
        if isinstance(limit, bool) or limit <= 0:
            raise ValueError("limit must be a positive integer")
        clauses: list[str] = []
        parameters: list[object] = []
        if norad_id is not None:
            clauses.append("norad_id=?")
            parameters.append(norad_id)
        if before is not None:
            clauses.append("observed_at<?")
            parameters.append(as_utc(before, name="before").isoformat())
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        parameters.append(limit)
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM reception_evidence"
                + where
                + " ORDER BY observed_at DESC, evidence_id LIMIT ?",
                parameters,
            ).fetchall()
        return tuple(
            StoredReceptionEvidence(
                evidence_id=str(row["evidence_id"]),
                observed_at=datetime.fromisoformat(row["observed_at"]),
                evidence=ReceptionEvidence(
                    norad_id=int(row["norad_id"]),
                    station_id=row["station_id"],
                    resource_id=row["resource_id"],
                    listened=bool(row["listened"]),
                    transmitter_state=row["transmitter_state"],
                    decoded=(
                        None if row["decoded"] is None else bool(row["decoded"])
                    ),
                    signal_present=(
                        None
                        if row["signal_present"] is None
                        else bool(row["signal_present"])
                    ),
                    source=row["source"],
                    weight=float(row["weight"]),
                ),
                source_observation_id=row["source_observation_id"],
            )
            for row in rows
        )

    def latest_plan_document(self, plan_id: str) -> dict[str, object] | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT document_json FROM observation_plan_revisions "
                "WHERE plan_id=? ORDER BY revision DESC LIMIT 1",
                (plan_id,),
            ).fetchone()
        return json.loads(row["document_json"]) if row else None
