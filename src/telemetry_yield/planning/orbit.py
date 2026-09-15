"""SGP4 visibility-window generation with no network access."""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from typing import Protocol

from .models import (
    GroundStation,
    OrbitSample,
    PassWindow,
    TleSnapshot,
    as_utc,
)

EARTH_ROTATION_RAD_S = 7.2921150e-5
WGS84_A_KM = 6378.137
WGS84_FLATTENING = 1.0 / 298.257223563
SPEED_OF_LIGHT_KM_S = 299_792.458


class OrbitPredictionError(RuntimeError):
    pass


class PassPredictor(Protocol):
    def predict(
        self,
        tle: TleSnapshot,
        station: GroundStation,
        start: datetime,
        end: datetime,
        *,
        carrier_frequency_hz: float | None = None,
    ) -> tuple[PassWindow, ...]: ...


def _julian_date(value: datetime) -> float:
    value = value.astimezone(UTC)
    year, month = value.year, value.month
    day = value.day + (
        value.hour + (value.minute + (value.second + value.microsecond / 1e6) / 60) / 60
    ) / 24
    if month <= 2:
        year -= 1
        month += 12
    a = year // 100
    b = 2 - a + a // 4
    return (
        math.floor(365.25 * (year + 4716))
        + math.floor(30.6001 * (month + 1))
        + day
        + b
        - 1524.5
    )


def _greenwich_sidereal_angle(julian_date: float) -> float:
    centuries = (julian_date - 2451545.0) / 36525.0
    degrees = (
        280.46061837
        + 360.98564736629 * (julian_date - 2451545.0)
        + 0.000387933 * centuries**2
        - centuries**3 / 38_710_000.0
    ) % 360.0
    return math.radians(degrees)


def _observer_eci(
    julian_date: float, station: GroundStation
) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    latitude = math.radians(station.latitude_deg)
    longitude = math.radians(station.longitude_deg)
    eccentricity_squared = WGS84_FLATTENING * (2.0 - WGS84_FLATTENING)
    prime_vertical = WGS84_A_KM / math.sqrt(
        1.0 - eccentricity_squared * math.sin(latitude) ** 2
    )
    radius_xy = (prime_vertical + station.altitude_m / 1000.0) * math.cos(latitude)
    radius_z = (
        prime_vertical * (1.0 - eccentricity_squared) + station.altitude_m / 1000.0
    ) * math.sin(latitude)
    angle = _greenwich_sidereal_angle(julian_date) + longitude
    position = (radius_xy * math.cos(angle), radius_xy * math.sin(angle), radius_z)
    velocity = (
        -EARTH_ROTATION_RAD_S * position[1],
        EARTH_ROTATION_RAD_S * position[0],
        0.0,
    )
    return position, velocity


def _eci_to_ecef(
    position: tuple[float, float, float], julian_date: float
) -> tuple[float, float, float]:
    angle = _greenwich_sidereal_angle(julian_date)
    cosine, sine = math.cos(angle), math.sin(angle)
    x, y, z = position
    return cosine * x + sine * y, -sine * x + cosine * y, z


def _subsatellite_point(
    position_eci: tuple[float, float, float], julian_date: float
) -> tuple[float, float]:
    x, y, z = _eci_to_ecef(position_eci, julian_date)
    longitude = math.degrees(math.atan2(y, x))
    radius_xy = math.hypot(x, y)
    eccentricity_squared = WGS84_FLATTENING * (2 - WGS84_FLATTENING)
    latitude = math.atan2(z, radius_xy * (1 - eccentricity_squared))
    for _ in range(5):
        sine = math.sin(latitude)
        n = WGS84_A_KM / math.sqrt(1 - eccentricity_squared * sine * sine)
        altitude = radius_xy / max(math.cos(latitude), 1e-12) - n
        latitude = math.atan2(
            z, radius_xy * (1 - eccentricity_squared * n / (n + altitude))
        )
    return math.degrees(latitude), longitude


class Sgp4PassPredictor:
    def __init__(self, *, step_seconds: int = 45, refinement_seconds: float = 0.5) -> None:
        if step_seconds < 5 or refinement_seconds <= 0:
            raise ValueError("invalid prediction step/refinement")
        self.step_seconds = step_seconds
        self.refinement_seconds = refinement_seconds

    @staticmethod
    def _satrec(tle: TleSnapshot):
        try:
            from sgp4.api import Satrec
        except ImportError as exc:  # pragma: no cover - optional dependency guard
            raise OrbitPredictionError(
                "SGP4 pass prediction requires the project planning/analysis extra"
            ) from exc
        return Satrec.twoline2rv(tle.line1, tle.line2)

    def _sample(self, satellite, station: GroundStation, at: datetime) -> OrbitSample:
        try:
            from sgp4.api import jday
        except ImportError as exc:  # pragma: no cover
            raise OrbitPredictionError("sgp4 is not installed") from exc
        at = as_utc(at)
        jd, fraction = jday(
            at.year,
            at.month,
            at.day,
            at.hour,
            at.minute,
            at.second + at.microsecond / 1e6,
        )
        error, sat_position, sat_velocity = satellite.sgp4(jd, fraction)
        if error:
            raise OrbitPredictionError(f"SGP4 failed with error code {error}")
        julian = jd + fraction
        observer_position, observer_velocity = _observer_eci(julian, station)
        delta_eci = tuple(sat_position[i] - observer_position[i] for i in range(3))
        range_km = math.sqrt(sum(item * item for item in delta_eci))
        relative_velocity = tuple(
            sat_velocity[i] - observer_velocity[i] for i in range(3)
        )
        range_rate = sum(relative_velocity[i] * delta_eci[i] for i in range(3)) / range_km

        sat_ecef = _eci_to_ecef(tuple(sat_position), julian)
        obs_ecef = _eci_to_ecef(observer_position, julian)
        dx, dy, dz = (sat_ecef[i] - obs_ecef[i] for i in range(3))
        latitude = math.radians(station.latitude_deg)
        longitude = math.radians(station.longitude_deg)
        east = -math.sin(longitude) * dx + math.cos(longitude) * dy
        north = (
            -math.sin(latitude) * math.cos(longitude) * dx
            - math.sin(latitude) * math.sin(longitude) * dy
            + math.cos(latitude) * dz
        )
        up = (
            math.cos(latitude) * math.cos(longitude) * dx
            + math.cos(latitude) * math.sin(longitude) * dy
            + math.sin(latitude) * dz
        )
        elevation = math.degrees(math.asin(max(-1.0, min(1.0, up / range_km))))
        azimuth = math.degrees(math.atan2(east, north)) % 360.0
        sub_lat, sub_lon = _subsatellite_point(tuple(sat_position), julian)
        return OrbitSample(
            at=at,
            elevation_deg=elevation,
            azimuth_deg=azimuth,
            range_km=range_km,
            range_rate_km_s=range_rate,
            subsatellite_latitude_deg=sub_lat,
            subsatellite_longitude_deg=sub_lon,
        )

    def sample(self, tle: TleSnapshot, station: GroundStation, at: datetime) -> OrbitSample:
        """Evaluate historical geometry at one instant using the supplied TLE.

        This deliberately does not fetch a newer element set.  It is used by
        the publication dataset builder so every feature is reproducible from
        the TLE embedded in the corresponding SatNOGS observation.
        """

        return self._sample(self._satrec(tle), station, as_utc(at, name="at"))

    def sample_track(
        self,
        tle: TleSnapshot,
        station: GroundStation,
        start: datetime,
        end: datetime,
        *,
        step_seconds: int | None = None,
    ) -> tuple[OrbitSample, ...]:
        """Sample a closed observation interval, regardless of visibility mask."""

        start, end = as_utc(start, name="start"), as_utc(end, name="end")
        if end <= start:
            raise ValueError("track end must be after start")
        step_value = step_seconds if step_seconds is not None else self.step_seconds
        if step_value <= 0:
            raise ValueError("step_seconds must be positive")
        step = timedelta(seconds=step_value)
        satellite = self._satrec(tle)
        samples: list[OrbitSample] = []
        at = start
        while True:
            samples.append(self._sample(satellite, station, at))
            if at >= end:
                break
            at = min(at + step, end)
        return tuple(samples)

    def _crossing(
        self,
        satellite,
        station: GroundStation,
        left: datetime,
        right: datetime,
    ) -> OrbitSample:
        threshold = station.minimum_elevation_deg
        while (right - left).total_seconds() > self.refinement_seconds:
            middle = left + (right - left) / 2
            sample = self._sample(satellite, station, middle)
            left_sample = self._sample(satellite, station, left)
            if (left_sample.elevation_deg >= threshold) == (
                sample.elevation_deg >= threshold
            ):
                left = middle
            else:
                right = middle
        return self._sample(satellite, station, left + (right - left) / 2)

    def predict(
        self,
        tle: TleSnapshot,
        station: GroundStation,
        start: datetime,
        end: datetime,
        *,
        carrier_frequency_hz: float | None = None,
    ) -> tuple[PassWindow, ...]:
        start, end = as_utc(start, name="start"), as_utc(end, name="end")
        if end <= start:
            raise ValueError("prediction end must be after start")
        satellite = self._satrec(tle)
        threshold = station.minimum_elevation_deg
        step = timedelta(seconds=self.step_seconds)
        passes: list[PassWindow] = []
        current: list[OrbitSample] = []
        previous_at = start
        previous = self._sample(satellite, station, start)
        if previous.elevation_deg >= threshold:
            current.append(previous)
        at = min(start + step, end)
        while True:
            sample = self._sample(satellite, station, at)
            was_visible = previous.elevation_deg >= threshold
            is_visible = sample.elevation_deg >= threshold
            if is_visible and not was_visible:
                current = [self._crossing(satellite, station, previous_at, at)]
            if is_visible:
                current.append(sample)
            if was_visible and not is_visible and current:
                current.append(self._crossing(satellite, station, previous_at, at))
                passes.append(
                    self._make_pass(
                        tle,
                        station,
                        current,
                        carrier_frequency_hz=carrier_frequency_hz,
                    )
                )
                current = []
            if at >= end:
                break
            previous_at, previous = at, sample
            at = min(at + step, end)
        if current:
            if current[-1].at < end:
                current.append(self._sample(satellite, station, end))
            passes.append(
                self._make_pass(
                    tle,
                    station,
                    current,
                    carrier_frequency_hz=carrier_frequency_hz,
                )
            )
        return tuple(passes)

    @staticmethod
    def _make_pass(
        tle: TleSnapshot,
        station: GroundStation,
        samples: list[OrbitSample],
        *,
        carrier_frequency_hz: float | None,
    ) -> PassWindow:
        peak = max(samples, key=lambda item: item.elevation_deg)
        doppler = None
        if carrier_frequency_hz is not None:
            doppler = max(
                abs(-carrier_frequency_hz * item.range_rate_km_s / SPEED_OF_LIGHT_KM_S)
                for item in samples
            )
        return PassWindow(
            norad_id=tle.norad_id,
            station_id=station.station_id,
            start=samples[0].at,
            end=samples[-1].at,
            max_elevation_deg=peak.elevation_deg,
            culmination_at=peak.at,
            min_range_km=min(item.range_km for item in samples),
            max_abs_doppler_hz=doppler,
            samples=tuple(samples),
            tle_fingerprint=tle.fingerprint,
        )
