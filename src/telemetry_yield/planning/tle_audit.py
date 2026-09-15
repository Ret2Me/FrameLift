"""Historical successive-TLE drift audit with explicit proxy limitations."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import timedelta
from typing import Mapping, Sequence

from .dataset import NormalizedObservation
from .models import GroundStation, PassWindow, ReceiverResource, TleSnapshot
from .orbit import OrbitPredictionError, Sgp4PassPredictor

DEFAULT_TLE_MATCH_TOLERANCE = timedelta(minutes=20)


@dataclass(frozen=True, slots=True)
class TleDriftRecord:
    norad_id: int
    station_id: int
    observation_id: int
    older_fingerprint: str
    reference_fingerprint: str
    older_epoch: str
    reference_epoch: str
    reference_window_start: str | None
    reference_window_end: str | None
    older_window_start: str | None
    older_window_end: str | None
    window_added: bool
    window_removed: bool
    start_shift_seconds: float | None
    end_shift_seconds: float | None
    duration_shift_seconds: float | None
    reference_lead_from_tle_epoch_seconds: float | None
    indeterminate_unmatched_both: bool = False
    propagation_error: str | None = None

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def _snapshot(row: NormalizedObservation) -> TleSnapshot:
    return TleSnapshot(
        norad_id=row.norad_id,
        name=row.tle_name,
        line1=row.tle_line1,
        line2=row.tle_line2,
        epoch=row.tle_epoch,
        fetched_at=row.start,
        source=f"satnogs-observation:{row.observation_id}",
    )


def _station(row: NormalizedObservation) -> GroundStation:
    return GroundStation(
        station_id=str(row.station_id),
        latitude_deg=row.station_latitude_deg,
        longitude_deg=row.station_longitude_deg,
        altitude_m=row.station_altitude_m,
        resources=(ReceiverResource("historical"),),
        minimum_elevation_deg=0.0,
        satnogs_station_id=row.station_id,
    )


def _nearest(
    windows: Sequence[PassWindow],
    row: NormalizedObservation,
    *,
    tolerance: timedelta,
) -> PassWindow | None:
    if not windows:
        return None
    midpoint = row.start + (row.end - row.start) / 2
    candidate = min(
        windows,
        key=lambda window: abs((window.culmination_at - midpoint).total_seconds()),
    )
    if abs(candidate.culmination_at - midpoint) > tolerance:
        return None
    return candidate


def audit_successive_tle_drift(
    rows: Sequence[NormalizedObservation],
    *,
    predictor: Sgp4PassPredictor | None = None,
    search_margin: timedelta = timedelta(minutes=45),
    match_tolerance: timedelta = DEFAULT_TLE_MATCH_TOLERANCE,
) -> tuple[TleDriftRecord, ...]:
    """Compare each new embedded TLE with the preceding distinct one.

    TLE epoch is retained as a *proxy* for availability because Network's
    observation API does not expose the exact time at which the element set was
    ingested.  The result measures planning-geometry drift, not orbit truth.
    """

    if match_tolerance <= timedelta(0) or match_tolerance >= search_margin:
        raise ValueError("TLE match tolerance must be positive and below search margin")
    predictor = predictor or Sgp4PassPredictor(step_seconds=30)
    by_satellite: dict[int, list[NormalizedObservation]] = {}
    for row in sorted(rows, key=lambda item: (item.start, item.observation_id)):
        by_satellite.setdefault(row.norad_id, []).append(row)
    records: list[TleDriftRecord] = []
    for norad_id, satellite_rows in sorted(by_satellite.items()):
        previous_distinct: NormalizedObservation | None = None
        previous_fingerprint: str | None = None
        for row in satellite_rows:
            if row.tle_fingerprint == previous_fingerprint:
                continue
            if previous_distinct is None:
                previous_distinct = row
                previous_fingerprint = row.tle_fingerprint
                continue
            station = _station(row)
            prediction_start = row.start - search_margin
            prediction_end = row.end + search_margin
            old_windows: Sequence[PassWindow] = ()
            reference_windows: Sequence[PassWindow] = ()
            propagation_errors: list[str] = []
            try:
                old_windows = predictor.predict(
                    _snapshot(previous_distinct),
                    station,
                    prediction_start,
                    prediction_end,
                    carrier_frequency_hz=row.frequency_hz,
                )
            except OrbitPredictionError as exc:
                propagation_errors.append(f"older: {exc}")
            try:
                reference_windows = predictor.predict(
                    _snapshot(row),
                    station,
                    prediction_start,
                    prediction_end,
                    carrier_frequency_hz=row.frequency_hz,
                )
            except OrbitPredictionError as exc:
                propagation_errors.append(f"reference: {exc}")
            old = _nearest(old_windows, row, tolerance=match_tolerance)
            reference = _nearest(
                reference_windows, row, tolerance=match_tolerance
            )
            start_shift = (
                (reference.start - old.start).total_seconds()
                if old is not None and reference is not None
                else None
            )
            end_shift = (
                (reference.end - old.end).total_seconds()
                if old is not None and reference is not None
                else None
            )
            duration_shift = (
                (reference.end - reference.start).total_seconds()
                - (old.end - old.start).total_seconds()
                if old is not None and reference is not None
                else None
            )
            records.append(
                TleDriftRecord(
                    norad_id=norad_id,
                    station_id=row.station_id,
                    observation_id=row.observation_id,
                    older_fingerprint=previous_distinct.tle_fingerprint,
                    reference_fingerprint=row.tle_fingerprint,
                    older_epoch=previous_distinct.tle_epoch.isoformat().replace("+00:00", "Z"),
                    reference_epoch=row.tle_epoch.isoformat().replace("+00:00", "Z"),
                    reference_window_start=(
                        reference.start.isoformat().replace("+00:00", "Z") if reference else None
                    ),
                    reference_window_end=(
                        reference.end.isoformat().replace("+00:00", "Z") if reference else None
                    ),
                    older_window_start=(
                        old.start.isoformat().replace("+00:00", "Z") if old else None
                    ),
                    older_window_end=(
                        old.end.isoformat().replace("+00:00", "Z") if old else None
                    ),
                    window_added=(
                        not propagation_errors and old is None and reference is not None
                    ),
                    window_removed=(
                        not propagation_errors and old is not None and reference is None
                    ),
                    start_shift_seconds=(None if propagation_errors else start_shift),
                    end_shift_seconds=(None if propagation_errors else end_shift),
                    duration_shift_seconds=(None if propagation_errors else duration_shift),
                    reference_lead_from_tle_epoch_seconds=(
                        (reference.start - row.tle_epoch).total_seconds()
                        if reference is not None and not propagation_errors
                        else None
                    ),
                    indeterminate_unmatched_both=(
                        not propagation_errors and old is None and reference is None
                    ),
                    propagation_error=(
                        "; ".join(propagation_errors) if propagation_errors else None
                    ),
                )
            )
            previous_distinct = row
            previous_fingerprint = row.tle_fingerprint
    return tuple(records)


def summarize_tle_drift(
    records: Sequence[TleDriftRecord],
    *,
    thresholds_seconds: Sequence[float] = (5, 15, 30, 60),
    freeze_horizons_minutes: Sequence[float] = (0, 15, 30, 60),
) -> Mapping[str, object]:
    for record in records:
        states = (
            record.start_shift_seconds is not None,
            record.window_added,
            record.window_removed,
            record.indeterminate_unmatched_both,
            record.propagation_error is not None,
        )
        if sum(states) != 1:
            raise ValueError("each TLE transition must have exactly one comparison state")
    paired = [record for record in records if record.start_shift_seconds is not None]
    threshold_summary: dict[str, object] = {}
    for threshold in thresholds_seconds:
        material = [
            record
            for record in records
            if record.window_added
            or record.window_removed
            or (
                record.start_shift_seconds is not None
                and abs(record.start_shift_seconds) >= threshold
            )
            or (
                record.duration_shift_seconds is not None
                and abs(record.duration_shift_seconds) >= threshold
            )
        ]
        freezes: dict[str, int] = {}
        for minutes in freeze_horizons_minutes:
            seconds = minutes * 60
            freezes[str(minutes)] = sum(
                record.reference_lead_from_tle_epoch_seconds is not None
                and 0 <= record.reference_lead_from_tle_epoch_seconds < seconds
                for record in material
            )
        threshold_summary[str(threshold)] = {
            "material_transition_count": len(material),
            "material_fraction": len(material) / len(records) if records else None,
            "transitions_inside_freeze_proxy": freezes,
        }
    absolute_starts = sorted(
        abs(record.start_shift_seconds)
        for record in paired
        if record.start_shift_seconds is not None
    )

    def percentile(fraction: float) -> float | None:
        if not absolute_starts:
            return None
        index = min(len(absolute_starts) - 1, round((len(absolute_starts) - 1) * fraction))
        return absolute_starts[index]

    return {
        "schema_version": "successive-tle-drift-summary-v1",
        "transition_count": len(records),
        "paired_window_count": len(paired),
        "window_added_count": sum(record.window_added for record in records),
        "window_removed_count": sum(record.window_removed for record in records),
        "indeterminate_unmatched_both_count": sum(
            record.indeterminate_unmatched_both for record in records
        ),
        "propagation_error_count": sum(
            record.propagation_error is not None for record in records
        ),
        "absolute_start_shift_seconds": {
            "median": percentile(0.5),
            "p90": percentile(0.9),
            "p95": percentile(0.95),
            "max": absolute_starts[-1] if absolute_starts else None,
        },
        "thresholds": threshold_summary,
        "availability_time_proxy": "reference TLE epoch; exact Network ingest time unavailable",
        "truth_boundary": "planning-geometry drift relative to the newer embedded TLE, not physical ephemeris error",
    }
