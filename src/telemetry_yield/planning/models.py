"""Validated domain records for monthly observation planning."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from datetime import UTC, datetime, time
from typing import Mapping


def as_utc(value: datetime, *, name: str = "timestamp") -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError(f"{name} must be a timezone-aware datetime")
    return value.astimezone(UTC)


def _non_empty(name: str, value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


def _probability(name: str, value: float) -> float:
    value = float(value)
    if not math.isfinite(value) or not 0.0 <= value <= 1.0:
        raise ValueError(f"{name} must be in [0, 1]")
    return value


@dataclass(frozen=True, slots=True)
class TleSnapshot:
    norad_id: int
    name: str
    line1: str
    line2: str
    epoch: datetime
    fetched_at: datetime
    source: str
    fingerprint: str = field(init=False)

    def __post_init__(self) -> None:
        if isinstance(self.norad_id, bool) or self.norad_id <= 0:
            raise ValueError("norad_id must be positive")
        object.__setattr__(self, "name", _non_empty("name", self.name))
        line1 = _non_empty("line1", self.line1)
        line2 = _non_empty("line2", self.line2)
        if not line1.startswith("1 ") or not line2.startswith("2 "):
            raise ValueError("TLE lines must start with '1 ' and '2 '")
        object.__setattr__(self, "line1", line1)
        object.__setattr__(self, "line2", line2)
        object.__setattr__(self, "epoch", as_utc(self.epoch, name="epoch"))
        object.__setattr__(
            self, "fetched_at", as_utc(self.fetched_at, name="fetched_at")
        )
        object.__setattr__(self, "source", _non_empty("source", self.source))
        digest = hashlib.sha256(f"{line1}\n{line2}\n".encode()).hexdigest()
        object.__setattr__(self, "fingerprint", digest)


@dataclass(frozen=True, slots=True)
class GeoBox:
    """A rectangular transmit region; antimeridian-crossing boxes are valid."""

    south_deg: float
    north_deg: float
    west_deg: float
    east_deg: float
    label: str = "region"

    def __post_init__(self) -> None:
        if not -90 <= self.south_deg <= self.north_deg <= 90:
            raise ValueError("invalid latitude bounds")
        if not -180 <= self.west_deg <= 180 or not -180 <= self.east_deg <= 180:
            raise ValueError("invalid longitude bounds")
        _non_empty("label", self.label)

    def contains(self, latitude_deg: float, longitude_deg: float) -> bool:
        if not self.south_deg <= latitude_deg <= self.north_deg:
            return False
        longitude_deg = ((longitude_deg + 180.0) % 360.0) - 180.0
        if self.west_deg <= self.east_deg:
            return self.west_deg <= longitude_deg <= self.east_deg
        return longitude_deg >= self.west_deg or longitude_deg <= self.east_deg


@dataclass(frozen=True, slots=True)
class TransmissionRule:
    """Known hard transmitter availability constraints in UTC.

    Weekdays use Python's convention (Monday=0).  Empty selectors mean "all".
    Daily windows are half-open; a window whose end is earlier than its start
    crosses midnight.
    """

    weekdays_utc: tuple[int, ...] = ()
    month_days_utc: tuple[int, ...] = ()
    daily_windows_utc: tuple[tuple[time, time], ...] = ()
    transmit_regions: tuple[GeoBox, ...] = ()
    probability_when_allowed: float = 1.0

    def __post_init__(self) -> None:
        if any(day < 0 or day > 6 for day in self.weekdays_utc):
            raise ValueError("weekdays_utc values must be in [0, 6]")
        if any(day < 1 or day > 31 for day in self.month_days_utc):
            raise ValueError("month_days_utc values must be in [1, 31]")
        _probability("probability_when_allowed", self.probability_when_allowed)
        for start, end in self.daily_windows_utc:
            if start.tzinfo is not None or end.tzinfo is not None:
                raise ValueError("daily window times must be naive UTC wall times")

    def allows(
        self,
        timestamp: datetime,
        *,
        subsatellite_latitude_deg: float | None = None,
        subsatellite_longitude_deg: float | None = None,
    ) -> bool:
        timestamp = as_utc(timestamp)
        if self.weekdays_utc and timestamp.weekday() not in self.weekdays_utc:
            return False
        if self.month_days_utc and timestamp.day not in self.month_days_utc:
            return False
        if self.daily_windows_utc:
            wall = timestamp.time().replace(tzinfo=None)
            within = any(
                (start <= wall < end)
                if start <= end
                else (wall >= start or wall < end)
                for start, end in self.daily_windows_utc
            )
            if not within:
                return False
        if self.transmit_regions:
            if (
                subsatellite_latitude_deg is None
                or subsatellite_longitude_deg is None
            ):
                return False
            if not any(
                region.contains(
                    subsatellite_latitude_deg, subsatellite_longitude_deg
                )
                for region in self.transmit_regions
            ):
                return False
        return True


@dataclass(frozen=True, slots=True)
class AntennaSpec:
    antenna_id: str
    frequency_ranges_hz: tuple[tuple[float, float], ...] = ()
    gain_dbi: float | None = None
    system_noise_temperature_k: float | None = None

    def __post_init__(self) -> None:
        _non_empty("antenna_id", self.antenna_id)
        for low, high in self.frequency_ranges_hz:
            if (
                not math.isfinite(low)
                or not math.isfinite(high)
                or low <= 0
                or high < low
            ):
                raise ValueError("invalid antenna frequency range")
        if self.gain_dbi is not None and not math.isfinite(self.gain_dbi):
            raise ValueError("gain_dbi must be finite when supplied")
        if self.system_noise_temperature_k is not None and (
            not math.isfinite(self.system_noise_temperature_k)
            or self.system_noise_temperature_k <= 0
        ):
            raise ValueError("system_noise_temperature_k must be positive")

    def supports(self, frequency_hz: float | None) -> bool:
        if frequency_hz is None or not self.frequency_ranges_hz:
            return True
        return any(low <= frequency_hz <= high for low, high in self.frequency_ranges_hz)


@dataclass(frozen=True, slots=True)
class ReceiverResource:
    resource_id: str
    antenna: AntennaSpec | None = None
    supported_modulations: tuple[str, ...] = ()
    capacity: int = 1

    def __post_init__(self) -> None:
        _non_empty("resource_id", self.resource_id)
        if (
            isinstance(self.capacity, bool)
            or not isinstance(self.capacity, int)
            or self.capacity <= 0
        ):
            raise ValueError("capacity must be a positive integer")
        if any(not isinstance(item, str) or not item.strip() for item in self.supported_modulations):
            raise ValueError("supported modulations must be non-empty strings")

    def supports(self, frequency_hz: float | None, modulation: str | None) -> bool:
        if self.antenna is not None and not self.antenna.supports(frequency_hz):
            return False
        if modulation is not None and self.supported_modulations:
            supported = {item.casefold() for item in self.supported_modulations}
            if modulation.casefold() not in supported:
                return False
        return True


@dataclass(frozen=True, slots=True)
class GroundStation:
    station_id: str
    latitude_deg: float
    longitude_deg: float
    altitude_m: float
    resources: tuple[ReceiverResource, ...]
    minimum_elevation_deg: float = 5.0
    satnogs_station_id: int | None = None

    def __post_init__(self) -> None:
        _non_empty("station_id", self.station_id)
        if not -90 <= self.latitude_deg <= 90:
            raise ValueError("latitude_deg must be in [-90, 90]")
        if not -180 <= self.longitude_deg <= 180:
            raise ValueError("longitude_deg must be in [-180, 180]")
        if not math.isfinite(self.altitude_m):
            raise ValueError("altitude_m must be finite")
        if not 0 <= self.minimum_elevation_deg < 90:
            raise ValueError("minimum_elevation_deg must be in [0, 90)")
        if not self.resources:
            raise ValueError("a station requires at least one receiver resource")
        ids = [resource.resource_id for resource in self.resources]
        if len(ids) != len(set(ids)):
            raise ValueError("receiver resource IDs must be unique per station")
        if self.satnogs_station_id is not None and self.satnogs_station_id <= 0:
            raise ValueError("satnogs_station_id must be positive")


@dataclass(frozen=True, slots=True)
class SatelliteTarget:
    norad_id: int
    name: str
    transmitter_uuid: str | None = None
    frequency_hz: float | None = None
    modulation: str | None = None
    baud: float | None = None
    transmit_power_dbw: float | None = None
    transmit_antenna_gain_dbi: float | None = None
    samples_per_second: float = 1.0
    priority: float = 1.0
    transmission_rule: TransmissionRule = field(default_factory=TransmissionRule)
    exclusive_transmission: bool = True
    transmitter_status: str | None = None

    def __post_init__(self) -> None:
        if isinstance(self.norad_id, bool) or self.norad_id <= 0:
            raise ValueError("norad_id must be positive")
        _non_empty("name", self.name)
        if self.transmitter_uuid is not None:
            _non_empty("transmitter_uuid", self.transmitter_uuid)
        if self.frequency_hz is not None and (
            not math.isfinite(self.frequency_hz) or self.frequency_hz <= 0
        ):
            raise ValueError("frequency_hz must be positive")
        if self.baud is not None and (
            not math.isfinite(self.baud) or self.baud <= 0
        ):
            raise ValueError("baud must be positive")
        for name in ("transmit_power_dbw", "transmit_antenna_gain_dbi"):
            value = getattr(self, name)
            if value is not None and not math.isfinite(value):
                raise ValueError(f"{name} must be finite when supplied")
        if self.modulation is not None and not self.modulation.strip():
            raise ValueError("modulation must be non-empty when supplied")
        if self.transmitter_status is not None:
            _non_empty("transmitter_status", self.transmitter_status)
        if self.samples_per_second < 0 or not math.isfinite(self.samples_per_second):
            raise ValueError("samples_per_second must be finite and non-negative")
        if self.priority <= 0 or not math.isfinite(self.priority):
            raise ValueError("priority must be finite and positive")


@dataclass(frozen=True, slots=True)
class OrbitSample:
    at: datetime
    elevation_deg: float
    azimuth_deg: float
    range_km: float
    range_rate_km_s: float
    subsatellite_latitude_deg: float
    subsatellite_longitude_deg: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "at", as_utc(self.at, name="sample.at"))
        if self.range_km <= 0:
            raise ValueError("range_km must be positive")


@dataclass(frozen=True, slots=True)
class PassWindow:
    norad_id: int
    station_id: str
    start: datetime
    end: datetime
    max_elevation_deg: float
    culmination_at: datetime
    min_range_km: float
    max_abs_doppler_hz: float | None
    samples: tuple[OrbitSample, ...]
    tle_fingerprint: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "start", as_utc(self.start, name="pass.start"))
        object.__setattr__(self, "end", as_utc(self.end, name="pass.end"))
        object.__setattr__(
            self, "culmination_at", as_utc(self.culmination_at, name="culmination_at")
        )
        if self.end <= self.start:
            raise ValueError("pass end must be after start")
        if not self.samples:
            raise ValueError("a pass requires orbit samples")
        _non_empty("tle_fingerprint", self.tle_fingerprint)


@dataclass(frozen=True, slots=True)
class ProbabilityEstimate:
    p_transmit: float
    p_decode_given_transmit: float
    p_success: float
    standard_deviation: float
    lower_90: float
    upper_90: float
    missing_features: tuple[str, ...] = ()
    evidence_count: int = 0
    model_version: str = "baseline"

    def __post_init__(self) -> None:
        for name in (
            "p_transmit",
            "p_decode_given_transmit",
            "p_success",
            "lower_90",
            "upper_90",
        ):
            _probability(name, getattr(self, name))
        if self.standard_deviation < 0 or not math.isfinite(self.standard_deviation):
            raise ValueError("standard_deviation must be finite and non-negative")
        if self.lower_90 > self.p_success or self.upper_90 < self.p_success:
            raise ValueError("probability interval must contain p_success")
        if not math.isclose(
            self.p_success,
            self.p_transmit * self.p_decode_given_transmit,
            rel_tol=1e-10,
            abs_tol=1e-12,
        ):
            raise ValueError("p_success must equal the two conditional factors")
        if (
            isinstance(self.evidence_count, bool)
            or not isinstance(self.evidence_count, int)
            or self.evidence_count < 0
        ):
            raise ValueError("evidence_count must be a non-negative integer")
        if any(not isinstance(name, str) or not name for name in self.missing_features):
            raise ValueError("missing feature names must be non-empty strings")
        _non_empty("model_version", self.model_version)

    @property
    def p_signal_present(self) -> float:
        """Operational signal probability stored in legacy ``p_transmit``."""

        return self.p_transmit

    @property
    def p_decode_given_signal(self) -> float:
        """Conditional decode probability stored in the legacy field name."""

        return self.p_decode_given_transmit


@dataclass(frozen=True, slots=True)
class Opportunity:
    opportunity_id: str
    norad_id: int
    satellite_name: str
    station_id: str
    resource_id: str
    start: datetime
    end: datetime
    tle_fingerprint: str
    probability: ProbabilityEstimate
    nominal_unique_samples: float
    expected_unique_samples: float
    priority: float
    max_elevation_deg: float
    min_range_km: float
    frequency_hz: float | None = None
    modulation: str | None = None
    transmitter_uuid: str | None = None
    satnogs_station_id: int | None = None
    exclusive_transmission: bool = True
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _non_empty("opportunity_id", self.opportunity_id)
        object.__setattr__(self, "start", as_utc(self.start, name="opportunity.start"))
        object.__setattr__(self, "end", as_utc(self.end, name="opportunity.end"))
        if self.end <= self.start:
            raise ValueError("opportunity end must be after start")
        if (
            self.nominal_unique_samples < 0
            or self.expected_unique_samples < 0
            or not math.isfinite(self.nominal_unique_samples)
            or not math.isfinite(self.expected_unique_samples)
        ):
            raise ValueError("sample counts must be non-negative")
        if self.expected_unique_samples > self.nominal_unique_samples + 1e-9:
            raise ValueError("expected samples cannot exceed nominal samples")
        if not math.isclose(
            self.expected_unique_samples,
            self.nominal_unique_samples * self.probability.p_success,
            rel_tol=1e-10,
            abs_tol=1e-9,
        ):
            raise ValueError("expected samples must equal nominal times p_success")
        if self.priority <= 0 or not math.isfinite(self.priority):
            raise ValueError("priority must be finite and positive")

    @property
    def duration_seconds(self) -> float:
        return (self.end - self.start).total_seconds()

    @property
    def conflict_assets(self) -> tuple[str, ...]:
        assets = (f"receiver:{self.station_id}:{self.resource_id}",)
        if self.exclusive_transmission:
            assets += (f"satellite:{self.norad_id}",)
        return assets

    def objective_value(self, risk_aversion: float = 0.0) -> float:
        if not 0 <= risk_aversion <= 1:
            raise ValueError("risk_aversion must be in [0, 1]")
        robust_expected_samples = max(
            0.0,
            self.expected_unique_samples
            - risk_aversion
            * 1.645
            * self.nominal_unique_samples
            * self.probability.standard_deviation,
        )
        return self.priority * robust_expected_samples


@dataclass(frozen=True, slots=True)
class Blocker:
    blocker_id: str
    station_id: str
    start: datetime
    end: datetime
    resource_id: str | None = None
    reason: str = "unavailable"
    kind: str = "maintenance"

    def __post_init__(self) -> None:
        _non_empty("blocker_id", self.blocker_id)
        _non_empty("station_id", self.station_id)
        object.__setattr__(self, "start", as_utc(self.start, name="blocker.start"))
        object.__setattr__(self, "end", as_utc(self.end, name="blocker.end"))
        if self.end <= self.start:
            raise ValueError("blocker end must be after start")

    def blocks(self, station_id: str, resource_id: str, start: datetime, end: datetime) -> bool:
        return (
            station_id == self.station_id
            and (self.resource_id is None or self.resource_id == resource_id)
            and start < self.end
            and self.start < end
        )


@dataclass(frozen=True, slots=True)
class ObservationPlan:
    plan_id: str
    revision: int
    created_at: datetime
    horizon_start: datetime
    horizon_end: datetime
    assignments: tuple[Opportunity, ...]
    tle_fingerprints: Mapping[int, str]
    objective_value: float
    solver: str
    trigger: str
    diagnostics: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _non_empty("plan_id", self.plan_id)
        if self.revision <= 0:
            raise ValueError("revision must be positive")
        object.__setattr__(self, "created_at", as_utc(self.created_at, name="created_at"))
        object.__setattr__(
            self, "horizon_start", as_utc(self.horizon_start, name="horizon_start")
        )
        object.__setattr__(
            self, "horizon_end", as_utc(self.horizon_end, name="horizon_end")
        )
        if self.horizon_end <= self.horizon_start:
            raise ValueError("plan horizon must be positive")

    def canonical_fingerprint(self) -> str:
        payload = {
            "plan_id": self.plan_id,
            "revision": self.revision,
            "created_at": self.created_at.isoformat(),
            "horizon_start": self.horizon_start.isoformat(),
            "horizon_end": self.horizon_end.isoformat(),
            "assignments": [
                {
                    "opportunity_id": item.opportunity_id,
                    "norad_id": item.norad_id,
                    "satellite_name": item.satellite_name,
                    "station_id": item.station_id,
                    "resource_id": item.resource_id,
                    "start": item.start.isoformat(),
                    "end": item.end.isoformat(),
                    "tle_fingerprint": item.tle_fingerprint,
                    "probability": {
                        "p_signal_present": float(
                            item.probability.p_signal_present
                        ),
                        "p_decode_given_signal": float(
                            item.probability.p_decode_given_signal
                        ),
                        "p_success": float(item.probability.p_success),
                        "standard_deviation": float(
                            item.probability.standard_deviation
                        ),
                        "lower_90": float(item.probability.lower_90),
                        "upper_90": float(item.probability.upper_90),
                        "missing_features": list(item.probability.missing_features),
                        "evidence_count": item.probability.evidence_count,
                        "model_version": item.probability.model_version,
                    },
                    "nominal_unique_samples": float(item.nominal_unique_samples),
                    "expected_unique_samples": float(item.expected_unique_samples),
                    "priority": float(item.priority),
                    "max_elevation_deg": float(item.max_elevation_deg),
                    "min_range_km": float(item.min_range_km),
                    "frequency_hz": (
                        float(item.frequency_hz) if item.frequency_hz is not None else None
                    ),
                    "modulation": item.modulation,
                    "transmitter_uuid": item.transmitter_uuid,
                    "satnogs_station_id": item.satnogs_station_id,
                    "exclusive_transmission": item.exclusive_transmission,
                    "metadata": dict(item.metadata),
                }
                for item in self.assignments
            ],
            "tle_fingerprints": dict(sorted(self.tle_fingerprints.items())),
            "objective_value": float(self.objective_value),
            "solver": self.solver,
            "trigger": self.trigger,
            "diagnostics": dict(self.diagnostics),
        }
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode()).hexdigest()
