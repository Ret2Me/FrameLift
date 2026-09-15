"""Provider interface for terrestrial and space-weather opportunity features."""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Protocol, Sequence

from .models import (
    GroundStation,
    PassWindow,
    ReceiverResource,
    SatelliteTarget,
    as_utc,
)
from .probability import ReceptionFeatures


@dataclass(frozen=True, slots=True)
class EnvironmentSnapshot:
    valid_at: datetime
    source: str
    atmospheric_attenuation_db: float | None = None
    space_weather_kp: float | None = None
    ionospheric_scintillation_index: float | None = None
    air_temperature_c: float | None = None
    relative_humidity_percent: float | None = None
    surface_pressure_kpa: float | None = None
    wind_speed_m_s: float | None = None
    precipitation_corrected: float | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "valid_at", as_utc(self.valid_at, name="valid_at"))
        if not isinstance(self.source, str) or not self.source.strip():
            raise ValueError("environment source is required")
        source = self.source.strip()
        object.__setattr__(self, "source", source)
        _validate_environment_values(
            self.atmospheric_attenuation_db,
            self.space_weather_kp,
            self.ionospheric_scintillation_index,
            self.air_temperature_c,
            self.relative_humidity_percent,
            self.surface_pressure_kpa,
            self.wind_speed_m_s,
            self.precipitation_corrected,
        )


def _validate_environment_values(
    atmospheric_attenuation_db: float | None,
    space_weather_kp: float | None,
    ionospheric_scintillation_index: float | None,
    air_temperature_c: float | None = None,
    relative_humidity_percent: float | None = None,
    surface_pressure_kpa: float | None = None,
    wind_speed_m_s: float | None = None,
    precipitation_corrected: float | None = None,
) -> None:
    for name, value in (
        ("atmospheric_attenuation_db", atmospheric_attenuation_db),
        ("space_weather_kp", space_weather_kp),
        ("ionospheric_scintillation_index", ionospheric_scintillation_index),
        ("air_temperature_c", air_temperature_c),
        ("relative_humidity_percent", relative_humidity_percent),
        ("surface_pressure_kpa", surface_pressure_kpa),
        ("wind_speed_m_s", wind_speed_m_s),
        ("precipitation_corrected", precipitation_corrected),
    ):
        if value is not None and not math.isfinite(value):
            raise ValueError(f"{name} must be finite when supplied")
    if atmospheric_attenuation_db is not None and atmospheric_attenuation_db < 0:
        raise ValueError("atmospheric attenuation must be non-negative")
    if space_weather_kp is not None and not 0 <= space_weather_kp <= 9:
        raise ValueError("Kp must be in [0, 9]")
    if (
        ionospheric_scintillation_index is not None
        and ionospheric_scintillation_index < 0
    ):
        raise ValueError("scintillation index must be non-negative")
    if relative_humidity_percent is not None and not 0 <= relative_humidity_percent <= 100:
        raise ValueError("relative humidity must be in [0, 100]")
    if surface_pressure_kpa is not None and surface_pressure_kpa <= 0:
        raise ValueError("surface pressure must be positive")
    if wind_speed_m_s is not None and wind_speed_m_s < 0:
        raise ValueError("wind speed must be non-negative")


class EnvironmentProvider(Protocol):
    def at(
        self, station: GroundStation, pass_window: PassWindow
    ) -> EnvironmentSnapshot: ...


@dataclass(frozen=True, slots=True)
class RecordedEnvironmentWindow:
    """A versionable local/forecast interval; every value remains optional."""

    start: datetime
    end: datetime
    source: str
    station_id: str | None = None
    atmospheric_attenuation_db: float | None = None
    space_weather_kp: float | None = None
    ionospheric_scintillation_index: float | None = None
    air_temperature_c: float | None = None
    relative_humidity_percent: float | None = None
    surface_pressure_kpa: float | None = None
    wind_speed_m_s: float | None = None
    precipitation_corrected: float | None = None

    def __post_init__(self) -> None:
        start = as_utc(self.start, name="environment.start")
        end = as_utc(self.end, name="environment.end")
        object.__setattr__(self, "start", start)
        object.__setattr__(self, "end", end)
        if end <= start:
            raise ValueError("environment window requires a positive timezone-aware interval")
        if not isinstance(self.source, str) or not self.source.strip():
            raise ValueError("environment source is required")
        source = self.source.strip()
        object.__setattr__(self, "source", source)
        if self.station_id is not None and (
            not isinstance(self.station_id, str) or not self.station_id.strip()
        ):
            raise ValueError("station_id must be a non-empty string when supplied")
        _validate_environment_values(
            self.atmospheric_attenuation_db,
            self.space_weather_kp,
            self.ionospheric_scintillation_index,
            self.air_temperature_c,
            self.relative_humidity_percent,
            self.surface_pressure_kpa,
            self.wind_speed_m_s,
            self.precipitation_corrected,
        )


class RecordedEnvironmentProvider:
    """Select a frozen local/forecast record without silently extrapolating it."""

    def __init__(self, windows: Sequence[RecordedEnvironmentWindow]) -> None:
        self.windows = tuple(
            sorted(windows, key=lambda item: (item.start, item.end, item.station_id or ""))
        )

    def at(
        self, station: GroundStation, pass_window: PassWindow
    ) -> EnvironmentSnapshot:
        at = pass_window.culmination_at
        candidates = [
            item
            for item in self.windows
            if item.start <= at < item.end
            and (item.station_id is None or item.station_id == station.station_id)
        ]
        if not candidates:
            return EnvironmentSnapshot(valid_at=at, source="missing:no-covered-environment-window")
        # Station-specific data wins; ties choose the most recently issued interval.
        selected = max(
            candidates,
            key=lambda item: (item.station_id == station.station_id, item.start),
        )
        return EnvironmentSnapshot(
            valid_at=at,
            source=selected.source,
            atmospheric_attenuation_db=selected.atmospheric_attenuation_db,
            space_weather_kp=selected.space_weather_kp,
            ionospheric_scintillation_index=selected.ionospheric_scintillation_index,
            air_temperature_c=selected.air_temperature_c,
            relative_humidity_percent=selected.relative_humidity_percent,
            surface_pressure_kpa=selected.surface_pressure_kpa,
            wind_speed_m_s=selected.wind_speed_m_s,
            precipitation_corrected=selected.precipitation_corrected,
        )


class CompositeEnvironmentProvider:
    """Merge providers in priority order, filling only still-missing fields."""

    def __init__(self, providers: Sequence[EnvironmentProvider]) -> None:
        if not providers:
            raise ValueError("at least one environment provider is required")
        self.providers = tuple(providers)

    def at(
        self, station: GroundStation, pass_window: PassWindow
    ) -> EnvironmentSnapshot:
        snapshots = [provider.at(station, pass_window) for provider in self.providers]

        def first(name: str):
            return next(
                (value for snapshot in snapshots if (value := getattr(snapshot, name)) is not None),
                None,
            )

        return EnvironmentSnapshot(
            valid_at=pass_window.culmination_at,
            source=" + ".join(snapshot.source for snapshot in snapshots),
            atmospheric_attenuation_db=first("atmospheric_attenuation_db"),
            space_weather_kp=first("space_weather_kp"),
            ionospheric_scintillation_index=first("ionospheric_scintillation_index"),
            air_temperature_c=first("air_temperature_c"),
            relative_humidity_percent=first("relative_humidity_percent"),
            surface_pressure_kpa=first("surface_pressure_kpa"),
            wind_speed_m_s=first("wind_speed_m_s"),
            precipitation_corrected=first("precipitation_corrected"),
        )


class EnvironmentFeatureEnricher:
    """Inject weather data without making opportunity generation provider-specific."""

    def __init__(
        self,
        provider: EnvironmentProvider,
        *,
        required_snr_by_modulation_db: dict[str, float] | None = None,
    ) -> None:
        self.provider = provider
        self.required_snr_by_modulation_db = {
            key.casefold(): value
            for key, value in (required_snr_by_modulation_db or {}).items()
        }

    def __call__(
        self,
        features: ReceptionFeatures,
        target: SatelliteTarget,
        station: GroundStation,
        resource: ReceiverResource,
        pass_window: PassWindow,
    ) -> ReceptionFeatures:
        snapshot = self.provider.at(station, pass_window)
        required_snr = (
            self.required_snr_by_modulation_db.get(target.modulation.casefold())
            if target.modulation is not None
            else None
        )
        return replace(
            features,
            atmospheric_attenuation_db=snapshot.atmospheric_attenuation_db,
            space_weather_kp=snapshot.space_weather_kp,
            ionospheric_scintillation_index=snapshot.ionospheric_scintillation_index,
            air_temperature_c=snapshot.air_temperature_c,
            relative_humidity_percent=snapshot.relative_humidity_percent,
            surface_pressure_kpa=snapshot.surface_pressure_kpa,
            wind_speed_m_s=snapshot.wind_speed_m_s,
            precipitation_corrected=snapshot.precipitation_corrected,
            required_snr_db=required_snr,
            environment_source=snapshot.source,
        )
