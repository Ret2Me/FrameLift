"""Turn TLE-derived passes into scored, hardware-compatible opportunities."""

from __future__ import annotations

import hashlib
import math
from dataclasses import asdict
from datetime import datetime, timedelta
from typing import Callable, Mapping, Sequence

from .models import (
    Blocker,
    GroundStation,
    Opportunity,
    OrbitSample,
    PassWindow,
    ReceiverResource,
    SatelliteTarget,
    TleSnapshot,
    as_utc,
)
from .orbit import PassPredictor
from .probability import (
    HierarchicalBetaEstimator,
    ProbabilityEstimator,
    ReceptionEvidence,
    ReceptionFeatures,
)

FeatureEnricher = Callable[
    [ReceptionFeatures, SatelliteTarget, GroundStation, ReceiverResource, PassWindow],
    ReceptionFeatures,
]


def _identity_enricher(
    features: ReceptionFeatures,
    target: SatelliteTarget,
    station: GroundStation,
    resource: ReceiverResource,
    pass_window: PassWindow,
) -> ReceptionFeatures:
    return features


def _allowed_segments(
    pass_window: PassWindow, target: SatelliteTarget
) -> tuple[tuple[datetime, datetime, tuple[OrbitSample, ...]], ...]:
    samples = pass_window.samples
    allowed = [
        target.transmission_rule.allows(
            sample.at,
            subsatellite_latitude_deg=sample.subsatellite_latitude_deg,
            subsatellite_longitude_deg=sample.subsatellite_longitude_deg,
        )
        for sample in samples
    ]
    segments: list[tuple[datetime, datetime, tuple[OrbitSample, ...]]] = []
    index = 0
    while index < len(samples):
        if not allowed[index]:
            index += 1
            continue
        first = index
        while index + 1 < len(samples) and allowed[index + 1]:
            index += 1
        last = index
        start = (
            pass_window.start
            if first == 0
            else samples[first - 1].at + (samples[first].at - samples[first - 1].at) / 2
        )
        end = (
            pass_window.end
            if last == len(samples) - 1
            else samples[last].at + (samples[last + 1].at - samples[last].at) / 2
        )
        if end > start:
            segments.append((start, end, tuple(samples[first : last + 1])))
        index += 1
    return tuple(segments)


def _opportunity_id(
    target: SatelliteTarget,
    station: GroundStation,
    resource: ReceiverResource,
    start: datetime,
    end: datetime,
    tle_fingerprint: str,
) -> str:
    raw = "|".join(
        (
            str(target.norad_id),
            station.station_id,
            resource.resource_id,
            start.isoformat(),
            end.isoformat(),
            target.transmitter_uuid or "",
            tle_fingerprint,
        )
    )
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


def _unblocked_segments(
    start: datetime,
    end: datetime,
    samples: tuple[OrbitSample, ...],
    *,
    station_id: str,
    resource_id: str,
    blockers: Sequence[Blocker],
) -> tuple[tuple[datetime, datetime, tuple[OrbitSample, ...]], ...]:
    """Subtract maintenance/priority reservations instead of dropping a whole pass."""

    intersections = sorted(
        (
            (max(start, blocker.start), min(end, blocker.end))
            for blocker in blockers
            if blocker.station_id == station_id
            and (blocker.resource_id is None or blocker.resource_id == resource_id)
            and start < blocker.end
            and blocker.start < end
        ),
        key=lambda item: (item[0], item[1]),
    )
    merged: list[tuple[datetime, datetime]] = []
    for blocked_start, blocked_end in intersections:
        if not merged or blocked_start > merged[-1][1]:
            merged.append((blocked_start, blocked_end))
        elif blocked_end > merged[-1][1]:
            merged[-1] = (merged[-1][0], blocked_end)

    spans: list[tuple[datetime, datetime]] = []
    cursor = start
    for blocked_start, blocked_end in merged:
        if cursor < blocked_start:
            spans.append((cursor, blocked_start))
        cursor = max(cursor, blocked_end)
    if cursor < end:
        spans.append((cursor, end))

    result: list[tuple[datetime, datetime, tuple[OrbitSample, ...]]] = []
    for span_start, span_end in spans:
        # The predictor samples the pass on a finite grid.  Discard an
        # unrepresented sub-grid sliver rather than inventing geometry.
        included = tuple(
            sample
            for sample in samples
            if span_start <= sample.at
            and (sample.at < span_end or (span_end == end and sample.at == span_end))
        )
        if included:
            result.append((span_start, span_end, included))
    return tuple(result)


def _duration_limited_segments(
    start: datetime,
    end: datetime,
    samples: tuple[OrbitSample, ...],
    *,
    maximum_duration_seconds: float | None,
) -> tuple[tuple[datetime, datetime, tuple[OrbitSample, ...]], ...]:
    """Make a continuously visible target interruptible without inventing geometry."""

    if (
        maximum_duration_seconds is None
        or (end - start).total_seconds() <= maximum_duration_seconds
    ):
        return ((start, end, samples),)
    maximum_duration = timedelta(seconds=maximum_duration_seconds)
    result: list[tuple[datetime, datetime, tuple[OrbitSample, ...]]] = []
    segment_start = start
    while segment_start < end:
        segment_end = min(end, segment_start + maximum_duration)
        included = tuple(
            sample
            for sample in samples
            if segment_start <= sample.at
            and (
                sample.at < segment_end
                or (segment_end == end and sample.at == segment_end)
            )
        )
        if included:
            result.append((segment_start, segment_end, included))
        segment_start = segment_end
    return tuple(result)


def _schedulable_segments(
    start: datetime,
    end: datetime,
    samples: tuple[OrbitSample, ...],
    *,
    station_id: str,
    resource_id: str,
    blockers: Sequence[Blocker],
    minimum_duration_seconds: float | None,
    maximum_duration_seconds: float | None,
) -> tuple[tuple[datetime, datetime, tuple[OrbitSample, ...], bool, bool], ...]:
    result: list[
        tuple[datetime, datetime, tuple[OrbitSample, ...], bool, bool]
    ] = []
    for usable_start, usable_end, usable_samples in _unblocked_segments(
        start,
        end,
        samples,
        station_id=station_id,
        resource_id=resource_id,
        blockers=blockers,
    ):
        blocker_clipped = usable_start != start or usable_end != end
        for selected_start, selected_end, selected_samples in _duration_limited_segments(
            usable_start,
            usable_end,
            usable_samples,
            maximum_duration_seconds=maximum_duration_seconds,
        ):
            if (
                minimum_duration_seconds is not None
                and (selected_end - selected_start).total_seconds()
                < minimum_duration_seconds
            ):
                continue
            result.append(
                (
                    selected_start,
                    selected_end,
                    selected_samples,
                    blocker_clipped,
                    selected_start != usable_start or selected_end != usable_end,
                )
            )
    return tuple(result)


class OpportunityBuilder:
    def __init__(
        self,
        predictor: PassPredictor,
        *,
        estimator: ProbabilityEstimator | None = None,
        feature_enricher: FeatureEnricher = _identity_enricher,
        minimum_opportunity_duration_seconds: float | None = None,
        maximum_opportunity_duration_seconds: float | None = None,
    ) -> None:
        if minimum_opportunity_duration_seconds is not None and (
            isinstance(minimum_opportunity_duration_seconds, bool)
            or not isinstance(minimum_opportunity_duration_seconds, (int, float))
            or not math.isfinite(float(minimum_opportunity_duration_seconds))
            or minimum_opportunity_duration_seconds <= 0
        ):
            raise ValueError(
                "minimum_opportunity_duration_seconds must be finite and positive"
            )
        if maximum_opportunity_duration_seconds is not None and (
            isinstance(maximum_opportunity_duration_seconds, bool)
            or not isinstance(maximum_opportunity_duration_seconds, (int, float))
            or not math.isfinite(float(maximum_opportunity_duration_seconds))
            or maximum_opportunity_duration_seconds <= 0
        ):
            raise ValueError(
                "maximum_opportunity_duration_seconds must be finite and positive"
            )
        if (
            minimum_opportunity_duration_seconds is not None
            and maximum_opportunity_duration_seconds is not None
            and minimum_opportunity_duration_seconds
            > maximum_opportunity_duration_seconds
        ):
            raise ValueError(
                "minimum_opportunity_duration_seconds cannot exceed the maximum"
            )
        self.predictor = predictor
        self.estimator = estimator or HierarchicalBetaEstimator()
        self.feature_enricher = feature_enricher
        self.minimum_opportunity_duration_seconds = (
            float(minimum_opportunity_duration_seconds)
            if minimum_opportunity_duration_seconds is not None
            else None
        )
        self.maximum_opportunity_duration_seconds = (
            float(maximum_opportunity_duration_seconds)
            if maximum_opportunity_duration_seconds is not None
            else None
        )

    def build(
        self,
        targets: Sequence[SatelliteTarget],
        stations: Sequence[GroundStation],
        tles: Mapping[int, TleSnapshot],
        horizon_start: datetime,
        horizon_end: datetime,
        *,
        blockers: Sequence[Blocker] = (),
        evidence: Sequence[ReceptionEvidence] = (),
    ) -> tuple[Opportunity, ...]:
        horizon_start = as_utc(horizon_start, name="horizon_start")
        horizon_end = as_utc(horizon_end, name="horizon_end")
        if horizon_end <= horizon_start:
            raise ValueError("planning horizon must be positive")
        result: list[Opportunity] = []
        for target in sorted(targets, key=lambda item: (item.norad_id, item.name)):
            try:
                tle = tles[target.norad_id]
            except KeyError as exc:
                raise ValueError(f"missing TLE for NORAD {target.norad_id}") from exc
            for station in sorted(stations, key=lambda item: item.station_id):
                passes = self.predictor.predict(
                    tle,
                    station,
                    horizon_start,
                    horizon_end,
                    carrier_frequency_hz=target.frequency_hz,
                )
                for pass_window in passes:
                    if (
                        pass_window.norad_id != target.norad_id
                        or pass_window.station_id != station.station_id
                        or pass_window.tle_fingerprint != tle.fingerprint
                    ):
                        raise ValueError(
                            "pass predictor returned geometry for a different TLE/target/station"
                        )
                    for start, end, samples in _allowed_segments(pass_window, target):
                        for resource in sorted(
                            station.resources, key=lambda item: item.resource_id
                        ):
                            if not resource.supports(target.frequency_hz, target.modulation):
                                continue
                            for (
                                selected_start,
                                selected_end,
                                selected_samples,
                                blocker_clipped,
                                duration_limited,
                            ) in _schedulable_segments(
                                start,
                                end,
                                samples,
                                station_id=station.station_id,
                                resource_id=resource.resource_id,
                                blockers=blockers,
                                minimum_duration_seconds=(
                                    self.minimum_opportunity_duration_seconds
                                ),
                                maximum_duration_seconds=(
                                    self.maximum_opportunity_duration_seconds
                                ),
                            ):
                                peak = max(
                                    selected_samples,
                                    key=lambda item: item.elevation_deg,
                                )
                                min_range = min(
                                    item.range_km for item in selected_samples
                                )
                                selected_pass_window = PassWindow(
                                    norad_id=pass_window.norad_id,
                                    station_id=pass_window.station_id,
                                    start=selected_start,
                                    end=selected_end,
                                    max_elevation_deg=peak.elevation_deg,
                                    culmination_at=peak.at,
                                    min_range_km=min_range,
                                    max_abs_doppler_hz=(
                                        pass_window.max_abs_doppler_hz
                                    ),
                                    samples=selected_samples,
                                    tle_fingerprint=pass_window.tle_fingerprint,
                                )
                                antenna = resource.antenna
                                features = ReceptionFeatures(
                                    norad_id=target.norad_id,
                                    station_id=station.station_id,
                                    resource_id=resource.resource_id,
                                    max_elevation_deg=peak.elevation_deg,
                                    min_range_km=min_range,
                                    duration_seconds=(selected_end - selected_start).total_seconds(),
                                    tle_age_hours=max(
                                        0.0,
                                        (selected_start - tle.epoch).total_seconds() / 3600,
                                    ),
                                    frequency_hz=target.frequency_hz,
                                    modulation=target.modulation,
                                    baud=target.baud,
                                    transmit_power_dbw=target.transmit_power_dbw,
                                    transmit_antenna_gain_dbi=target.transmit_antenna_gain_dbi,
                                    receive_antenna_gain_dbi=(
                                        antenna.gain_dbi if antenna is not None else None
                                    ),
                                    system_noise_temperature_k=(
                                        antenna.system_noise_temperature_k
                                        if antenna is not None
                                        else None
                                    ),
                                    satnogs_station_id=station.satnogs_station_id,
                                    transmitter_uuid=target.transmitter_uuid,
                                    transmitter_status=target.transmitter_status,
                                    rise_azimuth_deg=selected_samples[0].azimuth_deg,
                                    set_azimuth_deg=selected_samples[-1].azimuth_deg,
                                    antenna_type=(
                                        antenna.antenna_id if antenna is not None else None
                                    ),
                                    antenna_frequency_supported=(
                                        antenna.supports(target.frequency_hz)
                                        if antenna is not None
                                        and target.frequency_hz is not None
                                        and antenna.frequency_ranges_hz
                                        else None
                                    ),
                                )
                                features = self.feature_enricher(
                                    features,
                                    target,
                                    station,
                                    resource,
                                    selected_pass_window,
                                )
                                probability = self.estimator.estimate(
                                    features,
                                    evidence,
                                    transmitter_probability=(
                                        target.transmission_rule.probability_when_allowed
                                    ),
                                )
                                contract_factory = getattr(
                                    self.estimator, "feature_use_contract", None
                                )
                                if callable(contract_factory):
                                    feature_use_contract = dict(
                                        contract_factory(features)
                                    )
                                else:
                                    feature_use_contract = {
                                        "schema_version": "probability-feature-use-v1",
                                        "model_version": probability.model_version,
                                        "active_probability_features": [],
                                        "evaluation_only_features": [],
                                        "audit_status": "estimator_did_not_expose_feature_use",
                                        "active_feasibility_constraints": [],
                                    }
                                feasibility = list(
                                    feature_use_contract.get(
                                        "active_feasibility_constraints", []
                                    )
                                )
                                if (
                                    antenna is not None
                                    and antenna.frequency_ranges_hz
                                    and target.frequency_hz is not None
                                ):
                                    feasibility.append(
                                        "receiver_antenna_frequency_range"
                                    )
                                if (
                                    resource.supported_modulations
                                    and target.modulation is not None
                                ):
                                    feasibility.append(
                                        "receiver_supported_modulation"
                                    )
                                feature_use_contract[
                                    "active_feasibility_constraints"
                                ] = sorted(set(feasibility))
                                feature_use_contract["receiver_antenna"] = {
                                    "antenna_id": (
                                        antenna.antenna_id
                                        if antenna is not None
                                        else None
                                    ),
                                    "frequency_ranges_hz": (
                                        [list(value) for value in antenna.frequency_ranges_hz]
                                        if antenna is not None
                                        else []
                                    ),
                                    "gain_supplied": (
                                        antenna is not None
                                        and antenna.gain_dbi is not None
                                    ),
                                    "system_noise_temperature_supplied": (
                                        antenna is not None
                                        and antenna.system_noise_temperature_k is not None
                                    ),
                                }
                                nominal = (
                                    (selected_end - selected_start).total_seconds()
                                    * target.samples_per_second
                                )
                                result.append(
                                    Opportunity(
                                        opportunity_id=_opportunity_id(
                                            target,
                                            station,
                                            resource,
                                            selected_start,
                                            selected_end,
                                            tle.fingerprint,
                                        ),
                                        norad_id=target.norad_id,
                                        satellite_name=target.name,
                                        station_id=station.station_id,
                                        resource_id=resource.resource_id,
                                        start=selected_start,
                                        end=selected_end,
                                        tle_fingerprint=tle.fingerprint,
                                        probability=probability,
                                        nominal_unique_samples=nominal,
                                        expected_unique_samples=nominal
                                        * probability.p_success,
                                        priority=target.priority,
                                        max_elevation_deg=peak.elevation_deg,
                                        min_range_km=min_range,
                                        frequency_hz=target.frequency_hz,
                                        modulation=target.modulation,
                                        transmitter_uuid=target.transmitter_uuid,
                                        satnogs_station_id=station.satnogs_station_id,
                                        exclusive_transmission=target.exclusive_transmission,
                                        metadata={
                                            "culmination_at": peak.at.isoformat(),
                                            "max_abs_doppler_hz": pass_window.max_abs_doppler_hz,
                                            "tle_epoch": tle.epoch.isoformat(),
                                            "probability_model": probability.model_version,
                                            "probability_semantics": {
                                                "p_transmit": "legacy serialized name for P(detectable signal in an allowed window)",
                                                "p_decode_given_transmit": "legacy serialized name for P(decode artifact | detectable signal)",
                                                "p_success": "P(detectable signal) * P(decode artifact | detectable signal)",
                                            },
                                            "receiver_capacity": resource.capacity,
                                            "feature_snapshot": asdict(features),
                                            "feature_use_contract": feature_use_contract,
                                            "source_pass_start": pass_window.start.isoformat(),
                                            "source_pass_end": pass_window.end.isoformat(),
                                            "blocker_clipped": blocker_clipped,
                                            "duration_limited": duration_limited,
                                            "minimum_opportunity_duration_seconds": (
                                                self.minimum_opportunity_duration_seconds
                                            ),
                                            "maximum_opportunity_duration_seconds": (
                                                self.maximum_opportunity_duration_seconds
                                            ),
                                        },
                                    )
                                )
        return tuple(
            sorted(
                result,
                key=lambda item: (
                    item.start,
                    item.end,
                    item.station_id,
                    item.resource_id,
                    item.norad_id,
                ),
            )
        )
