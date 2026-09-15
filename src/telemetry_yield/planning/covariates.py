"""Auditable terrestrial-weather, space-weather and antenna covariates.

Historical model fitting uses immutable archived source responses, including
forecast-model output from Open-Meteo's Historical Forecast API. Forecasts
used by a live planning run must be captured before the pass and can be
supplied through the same archive schema. Hardware is even stricter: a
configuration is joined only when it was recorded no later than the
observation start, unless its ``known_at`` timestamp refers to an older,
independently archived source.
"""

from __future__ import annotations

import base64
import hashlib
import json
import math
import os
import tempfile
import time
from bisect import bisect_right
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Callable, Iterable, Mapping, Sequence
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

from .dataset import (
    DatasetIntegrityError,
    NormalizedObservation,
    parse_api_datetime,
    read_normalized_jsonl,
    write_normalized_jsonl,
)
from .models import GroundStation, as_utc
from .environment import EnvironmentSnapshot


NASA_POWER_ENDPOINT = "https://power.larc.nasa.gov/api/temporal/hourly/point"
GFZ_KP_ENDPOINT = "https://kp.gfz.de/app/json/"
OPEN_METEO_FORECAST_ENDPOINT = "https://api.open-meteo.com/v1/forecast"
OPEN_METEO_HISTORICAL_FORECAST_ENDPOINT = (
    "https://historical-forecast-api.open-meteo.com/v1/forecast"
)
NOAA_KP_FORECAST_ENDPOINT = (
    "https://services.swpc.noaa.gov/products/noaa-planetary-k-index-forecast.json"
)
SATNOGS_HARDWARE_SOURCE = "SatNOGS Network station detail API"
NASA_POWER_PARAMETERS = (
    "T2M",
    "RH2M",
    "PS",
    "WS10M",
    "PRECTOTCORR",
)


class CovariateError(ValueError):
    """A source response or time alignment violates the frozen contract."""


FetchBytes = Callable[[str, Mapping[str, str], float, int], bytes]


def _default_fetch_bytes(
    url: str, headers: Mapping[str, str], timeout: float, limit: int
) -> bytes:
    request = Request(url, headers=dict(headers), method="GET")
    with urlopen(request, timeout=timeout) as response:  # noqa: S310 - fixed HTTPS origins
        final = urlparse(response.geturl())
        initial = urlparse(url)
        if final.scheme != "https" or final.netloc != initial.netloc:
            raise CovariateError("covariate redirect left the configured HTTPS origin")
        body = response.read(limit + 1)
    if len(body) > limit:
        raise CovariateError(f"covariate response exceeded {limit} bytes")
    return body


def _canonical_hash(payload: object) -> str:
    body = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(body).hexdigest()


def _atomic_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            json.dump(payload, handle, indent=2, sort_keys=True, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


class _CachedJsonClient:
    def __init__(
        self,
        *,
        user_agent: str,
        cache_dir: Path | None,
        cache_namespace: str | None = None,
        fetch_bytes: FetchBytes = _default_fetch_bytes,
        timeout_seconds: float = 60.0,
        max_response_bytes: int = 8_000_000,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        if not user_agent.strip() or "\n" in user_agent or "\r" in user_agent:
            raise ValueError("a safe, non-empty User-Agent is required")
        if timeout_seconds <= 0 or max_response_bytes <= 0:
            raise ValueError("timeout and response bound must be positive")
        if cache_namespace is not None and (
            not cache_namespace.strip()
            or "\n" in cache_namespace
            or "\r" in cache_namespace
        ):
            raise ValueError("cache_namespace must be a safe, non-empty string")
        self.user_agent = user_agent.strip()
        self.cache_dir = cache_dir
        self.cache_namespace = (
            cache_namespace.strip() if cache_namespace is not None else None
        )
        self.fetch_bytes = fetch_bytes
        self.timeout_seconds = timeout_seconds
        self.max_response_bytes = max_response_bytes
        self.now = now

    def get_json(
        self, url: str, *, require_mapping: bool = True
    ) -> tuple[object, datetime, str]:
        parsed = urlparse(url)
        if parsed.scheme != "https" or not parsed.netloc:
            raise CovariateError("covariate URL must be absolute HTTPS")
        # Historical endpoint URLs identify a frozen request and may safely be
        # replayed. Mutable forecast endpoints (notably NOAA's constant URL)
        # supply a per-planning-run namespace so a prior day's response cannot
        # silently satisfy today's capture.
        cache_identity = (
            url
            if self.cache_namespace is None
            else f"{self.cache_namespace}\0{url}"
        )
        key = hashlib.sha256(cache_identity.encode("utf-8")).hexdigest()
        cache_path = self.cache_dir / f"{key}.json" if self.cache_dir else None
        meta_path = self.cache_dir / f"{key}.meta.json" if self.cache_dir else None
        body: bytes
        retrieved_at: datetime
        if cache_path is not None and meta_path is not None and cache_path.exists():
            body = cache_path.read_bytes()
            metadata = json.loads(meta_path.read_text(encoding="utf-8"))
            if (
                metadata.get("url") != url
                or metadata.get("cache_namespace") != self.cache_namespace
            ):
                raise CovariateError("covariate cache URL mismatch")
            expected = metadata.get("body_sha256")
            actual = hashlib.sha256(body).hexdigest()
            if expected != actual:
                raise CovariateError("covariate cache hash mismatch")
            retrieved_at = parse_api_datetime(
                metadata.get("retrieved_at"), name="retrieved_at"
            )
        else:
            body = self.fetch_bytes(
                url,
                {"User-Agent": self.user_agent, "Accept": "application/json"},
                self.timeout_seconds,
                self.max_response_bytes,
            )
            retrieved_at = as_utc(self.now(), name="now")
            if cache_path is not None and meta_path is not None:
                cache_path.parent.mkdir(parents=True, exist_ok=True)
                cache_path.write_bytes(body)
                _atomic_json(
                    meta_path,
                    {
                        "schema_version": "public-covariate-http-cache-v1",
                        "url": url,
                        "cache_namespace": self.cache_namespace,
                        "retrieved_at": retrieved_at.isoformat().replace(
                            "+00:00", "Z"
                        ),
                        "body_sha256": hashlib.sha256(body).hexdigest(),
                    },
                )
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CovariateError("covariate source returned invalid JSON") from exc
        if require_mapping and not isinstance(payload, Mapping):
            raise CovariateError("covariate source JSON must be an object")
        return payload, retrieved_at, hashlib.sha256(body).hexdigest()


def _optional_source_float(value: object, *, fill_value: float = -999.0) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if not math.isfinite(result) or math.isclose(result, fill_value):
        return None
    return result


@dataclass(frozen=True, slots=True)
class WeatherRecord:
    station_id: int
    latitude_deg: float
    longitude_deg: float
    valid_at: datetime
    retrieved_at: datetime
    source: str
    source_response_sha256: str
    air_temperature_c: float | None = None
    relative_humidity_percent: float | None = None
    surface_pressure_kpa: float | None = None
    wind_speed_m_s: float | None = None
    precipitation_corrected: float | None = None

    def __post_init__(self) -> None:
        if isinstance(self.station_id, bool) or self.station_id <= 0:
            raise ValueError("weather station_id must be positive")
        if not -90 <= self.latitude_deg <= 90 or not -180 <= self.longitude_deg <= 180:
            raise ValueError("invalid weather coordinates")
        object.__setattr__(self, "valid_at", as_utc(self.valid_at, name="valid_at"))
        object.__setattr__(
            self, "retrieved_at", as_utc(self.retrieved_at, name="retrieved_at")
        )
        if not self.source.strip() or len(self.source_response_sha256) != 64:
            raise ValueError("weather source and SHA-256 are required")
        for name in (
            "air_temperature_c",
            "relative_humidity_percent",
            "surface_pressure_kpa",
            "wind_speed_m_s",
            "precipitation_corrected",
        ):
            value = getattr(self, name)
            if value is not None and not math.isfinite(value):
                raise ValueError(f"{name} must be finite")
        if self.relative_humidity_percent is not None and not 0 <= self.relative_humidity_percent <= 100:
            raise ValueError("relative humidity must be in [0, 100]")
        if self.surface_pressure_kpa is not None and self.surface_pressure_kpa <= 0:
            raise ValueError("surface pressure must be positive")
        if self.wind_speed_m_s is not None and self.wind_speed_m_s < 0:
            raise ValueError("wind speed must be non-negative")
        if (
            self.precipitation_corrected is not None
            and self.precipitation_corrected < 0
        ):
            raise ValueError("precipitation must be non-negative")

    def as_dict(self) -> dict[str, object]:
        payload = asdict(self)
        for key in ("valid_at", "retrieved_at"):
            payload[key] = payload[key].isoformat().replace("+00:00", "Z")  # type: ignore[union-attr]
        return payload

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> "WeatherRecord":
        values = dict(payload)
        values["valid_at"] = parse_api_datetime(values.get("valid_at"), name="valid_at")
        values["retrieved_at"] = parse_api_datetime(
            values.get("retrieved_at"), name="retrieved_at"
        )
        return cls(**values)  # type: ignore[arg-type]


@dataclass(frozen=True, slots=True)
class KpRecord:
    valid_at: datetime
    retrieved_at: datetime
    kp: float
    status: str
    source: str
    source_response_sha256: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "valid_at", as_utc(self.valid_at, name="valid_at"))
        object.__setattr__(
            self, "retrieved_at", as_utc(self.retrieved_at, name="retrieved_at")
        )
        if not 0 <= self.kp <= 9:
            raise ValueError("Kp must be in [0, 9]")
        if not self.status.strip() or not self.source.strip():
            raise ValueError("Kp status and source are required")
        if len(self.source_response_sha256) != 64:
            raise ValueError("Kp source SHA-256 is required")

    def as_dict(self) -> dict[str, object]:
        payload = asdict(self)
        for key in ("valid_at", "retrieved_at"):
            payload[key] = payload[key].isoformat().replace("+00:00", "Z")  # type: ignore[union-attr]
        return payload

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> "KpRecord":
        values = dict(payload)
        values["valid_at"] = parse_api_datetime(values.get("valid_at"), name="valid_at")
        values["retrieved_at"] = parse_api_datetime(
            values.get("retrieved_at"), name="retrieved_at"
        )
        return cls(**values)  # type: ignore[arg-type]


@dataclass(frozen=True, slots=True)
class HardwareRecord:
    station_id: int
    configuration_id: str
    antenna_type: str | None
    frequency_ranges_hz: tuple[tuple[float, float], ...]
    effective_from: datetime
    known_at: datetime
    source: str
    receive_antenna_gain_dbi: float | None = None
    system_noise_temperature_k: float | None = None
    evidence_sha256: str | None = None

    def __post_init__(self) -> None:
        if isinstance(self.station_id, bool) or self.station_id <= 0:
            raise ValueError("hardware station_id must be positive")
        if not self.configuration_id.strip() or not self.source.strip():
            raise ValueError("hardware configuration_id and source are required")
        object.__setattr__(
            self, "effective_from", as_utc(self.effective_from, name="effective_from")
        )
        object.__setattr__(self, "known_at", as_utc(self.known_at, name="known_at"))
        for low, high in self.frequency_ranges_hz:
            if not math.isfinite(low) or not math.isfinite(high) or low <= 0 or high < low:
                raise ValueError("invalid hardware frequency range")
        if self.receive_antenna_gain_dbi is not None and not math.isfinite(
            self.receive_antenna_gain_dbi
        ):
            raise ValueError("receive antenna gain must be finite")
        if self.system_noise_temperature_k is not None and (
            not math.isfinite(self.system_noise_temperature_k)
            or self.system_noise_temperature_k <= 0
        ):
            raise ValueError("system noise temperature must be positive")
        if self.evidence_sha256 is not None and (
            len(self.evidence_sha256) != 64
            or any(character not in "0123456789abcdef" for character in self.evidence_sha256)
        ):
            raise ValueError("hardware evidence_sha256 must be a lowercase SHA-256")

    def supports(self, frequency_hz: float | None) -> bool | None:
        if frequency_hz is None or not self.frequency_ranges_hz:
            return None
        return any(low <= frequency_hz <= high for low, high in self.frequency_ranges_hz)

    def as_dict(self) -> dict[str, object]:
        payload = asdict(self)
        for key in ("effective_from", "known_at"):
            payload[key] = payload[key].isoformat().replace("+00:00", "Z")  # type: ignore[union-attr]
        return payload

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> "HardwareRecord":
        values = dict(payload)
        values["effective_from"] = parse_api_datetime(
            values.get("effective_from"), name="effective_from"
        )
        values["known_at"] = parse_api_datetime(values.get("known_at"), name="known_at")
        values["frequency_ranges_hz"] = tuple(
            (float(item[0]), float(item[1]))
            for item in values.get("frequency_ranges_hz", [])  # type: ignore[union-attr]
        )
        return cls(**values)  # type: ignore[arg-type]

    @classmethod
    def from_station(
        cls,
        station: GroundStation,
        *,
        effective_from: datetime,
        known_at: datetime,
        source: str,
        evidence_sha256: str | None = None,
    ) -> "HardwareRecord":
        antennas = [resource.antenna for resource in station.resources if resource.antenna]
        ranges = tuple(
            sorted(
                {
                    pair
                    for antenna in antennas
                    for pair in antenna.frequency_ranges_hz
                }
            )
        )
        gains = {antenna.gain_dbi for antenna in antennas if antenna.gain_dbi is not None}
        noise = {
            antenna.system_noise_temperature_k
            for antenna in antennas
            if antenna.system_noise_temperature_k is not None
        }
        if len(gains) > 1 or len(noise) > 1:
            raise CovariateError(
                "aggregate station hardware requires one unambiguous gain/noise value"
            )
        if station.satnogs_station_id is None:
            raise CovariateError("hardware enrichment requires a SatNOGS station ID")
        antenna_types = sorted({antenna.antenna_id for antenna in antennas})
        identity_payload = {
            "station_id": station.satnogs_station_id,
            "antenna_types": antenna_types,
            "frequency_ranges_hz": ranges,
            "gain_dbi": next(iter(gains), None),
            "system_noise_temperature_k": next(iter(noise), None),
            "effective_from": as_utc(effective_from).isoformat(),
            "source": source,
        }
        return cls(
            station_id=station.satnogs_station_id,
            configuration_id=_canonical_hash(identity_payload),
            antenna_type=" + ".join(antenna_types) if antenna_types else None,
            frequency_ranges_hz=ranges,
            effective_from=effective_from,
            known_at=known_at,
            source=source,
            receive_antenna_gain_dbi=next(iter(gains), None),
            system_noise_temperature_k=next(iter(noise), None),
            evidence_sha256=evidence_sha256,
        )


def build_hardware_archive_from_declarations(
    declarations_path: Path, output_path: Path
) -> Mapping[str, object]:
    """Validate owner-supplied measured hardware without guessing any field."""

    payload = json.loads(declarations_path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping) or payload.get("schema_version") != "station-hardware-declarations-v1":
        raise CovariateError("unsupported station hardware declaration schema")
    raw_records = payload.get("records")
    if not isinstance(raw_records, list) or not raw_records:
        raise CovariateError("station hardware declarations require records")
    records = tuple(HardwareRecord.from_dict(item) for item in raw_records)
    if any(
        (item.receive_antenna_gain_dbi is not None or item.system_noise_temperature_k is not None)
        and item.evidence_sha256 is None
        for item in records
    ):
        raise CovariateError(
            "measured gain/noise declarations require evidence_sha256"
        )
    return write_covariate_archive(output_path, hardware=records)


class NasaPowerHourlyClient:
    """Fetch NASA POWER hourly meteorology in UTC for one station location."""

    def __init__(self, **client_kwargs: object) -> None:
        self._client = _CachedJsonClient(**client_kwargs)  # type: ignore[arg-type]

    def fetch(
        self,
        *,
        station_id: int,
        latitude_deg: float,
        longitude_deg: float,
        start: datetime,
        end: datetime,
    ) -> tuple[WeatherRecord, ...]:
        start, end = as_utc(start), as_utc(end)
        if end <= start:
            raise ValueError("weather interval must be positive")
        params = {
            "parameters": ",".join(NASA_POWER_PARAMETERS),
            "community": "AG",
            "longitude": f"{longitude_deg:.6f}",
            "latitude": f"{latitude_deg:.6f}",
            "start": start.strftime("%Y%m%d"),
            "end": (end - timedelta(microseconds=1)).strftime("%Y%m%d"),
            "format": "JSON",
            "time-standard": "UTC",
        }
        url = f"{NASA_POWER_ENDPOINT}?{urlencode(params)}"
        payload, retrieved_at, response_hash = self._client.get_json(url)
        properties = payload.get("properties")
        if not isinstance(properties, Mapping) or not isinstance(
            properties.get("parameter"), Mapping
        ):
            raise CovariateError("NASA POWER response is missing properties.parameter")
        parameters = properties["parameter"]
        assert isinstance(parameters, Mapping)
        parameter_metadata = payload.get("parameters")
        expected_units = {
            "T2M": "C",
            "RH2M": "%",
            "PS": "kPa",
            "WS10M": "m/s",
            "PRECTOTCORR": "mm/hour",
        }
        if not isinstance(parameter_metadata, Mapping) or any(
            not isinstance(parameter_metadata.get(name), Mapping)
            or parameter_metadata[name].get("units") != unit  # type: ignore[index]
            for name, unit in expected_units.items()
        ):
            raise CovariateError("NASA POWER response units do not match the frozen contract")
        series: dict[str, Mapping[str, object]] = {}
        for name in NASA_POWER_PARAMETERS:
            values = parameters.get(name)
            if not isinstance(values, Mapping):
                raise CovariateError(f"NASA POWER response is missing {name}")
            series[name] = values
        timestamps = sorted({key for values in series.values() for key in values})
        records: list[WeatherRecord] = []
        for key in timestamps:
            try:
                valid_at = datetime.strptime(key, "%Y%m%d%H").replace(tzinfo=UTC)
            except ValueError as exc:
                raise CovariateError("NASA POWER returned an invalid hour key") from exc
            if not start <= valid_at < end:
                continue
            records.append(
                WeatherRecord(
                    station_id=station_id,
                    latitude_deg=latitude_deg,
                    longitude_deg=longitude_deg,
                    valid_at=valid_at,
                    retrieved_at=retrieved_at,
                    source="NASA POWER Hourly API v2.10",
                    source_response_sha256=response_hash,
                    air_temperature_c=_optional_source_float(series["T2M"].get(key)),
                    relative_humidity_percent=_optional_source_float(
                        series["RH2M"].get(key)
                    ),
                    surface_pressure_kpa=_optional_source_float(series["PS"].get(key)),
                    wind_speed_m_s=_optional_source_float(series["WS10M"].get(key)),
                    precipitation_corrected=_optional_source_float(
                        series["PRECTOTCORR"].get(key)
                    ),
                )
            )
        return tuple(records)


class GfzKpClient:
    """Fetch definitive/provisional/predicted three-hour Kp values from GFZ."""

    def __init__(self, **client_kwargs: object) -> None:
        self._client = _CachedJsonClient(**client_kwargs)  # type: ignore[arg-type]

    def fetch(self, start: datetime, end: datetime) -> tuple[KpRecord, ...]:
        start, end = as_utc(start), as_utc(end)
        if end <= start:
            raise ValueError("Kp interval must be positive")
        params = {
            "start": start.isoformat().replace("+00:00", "Z"),
            "end": end.isoformat().replace("+00:00", "Z"),
            "index": "Kp",
        }
        url = f"{GFZ_KP_ENDPOINT}?{urlencode(params)}"
        payload, retrieved_at, response_hash = self._client.get_json(url)
        timestamps, values, statuses = (
            payload.get("datetime"),
            payload.get("Kp"),
            payload.get("status"),
        )
        if not isinstance(timestamps, list) or not isinstance(values, list):
            raise CovariateError("GFZ response is missing datetime/Kp arrays")
        if statuses is None:
            statuses = ["unknown"] * len(timestamps)
        if not isinstance(statuses, list) or not (
            len(timestamps) == len(values) == len(statuses)
        ):
            raise CovariateError("GFZ Kp arrays have different lengths")
        result: list[KpRecord] = []
        for timestamp, value, status in zip(timestamps, values, statuses, strict=True):
            kp = _optional_source_float(value)
            if kp is None:
                continue
            valid_at = parse_api_datetime(timestamp, name="GFZ datetime")
            # The upstream service may treat the requested end timestamp as
            # inclusive. Keep the archive contract half-open so adjacent
            # monthly chunks cannot duplicate or ambiguously override a Kp
            # interval at their shared boundary.
            if not start <= valid_at < end:
                continue
            result.append(
                KpRecord(
                    valid_at=valid_at,
                    retrieved_at=retrieved_at,
                    kp=kp,
                    status=str(status or "unknown"),
                    source="GFZ Potsdam Kp API",
                    source_response_sha256=response_hash,
                )
            )
        return tuple(result)


class NoaaKpForecastClient:
    """Capture NOAA SWPC observed/estimated/predicted planetary K-index."""

    def __init__(self, **client_kwargs: object) -> None:
        self._client = _CachedJsonClient(**client_kwargs)  # type: ignore[arg-type]

    def fetch(self, start: datetime, end: datetime) -> tuple[KpRecord, ...]:
        start, end = as_utc(start), as_utc(end)
        payload, retrieved_at, response_hash = self._client.get_json(
            NOAA_KP_FORECAST_ENDPOINT, require_mapping=False
        )
        if not isinstance(payload, list):
            raise CovariateError("NOAA Kp forecast must be a JSON array")
        records: list[KpRecord] = []
        for item in payload:
            if not isinstance(item, Mapping):
                raise CovariateError("NOAA Kp forecast row must be an object")
            timestamp = item.get("time_tag")
            if not isinstance(timestamp, str):
                raise CovariateError("NOAA Kp forecast is missing time_tag")
            try:
                parsed = datetime.fromisoformat(timestamp)
            except ValueError as exc:
                raise CovariateError("NOAA Kp returned an invalid timestamp") from exc
            valid_at = (
                parsed.replace(tzinfo=UTC)
                if parsed.tzinfo is None
                else parsed.astimezone(UTC)
            )
            value = _optional_source_float(item.get("kp"))
            if value is None or not start <= valid_at < end:
                continue
            records.append(
                KpRecord(
                    valid_at=valid_at,
                    retrieved_at=retrieved_at,
                    kp=value,
                    status=str(item.get("observed") or "unknown"),
                    source="NOAA SWPC planetary K-index forecast",
                    source_response_sha256=response_hash,
                )
            )
        return tuple(records)


class OpenMeteoForecastClient:
    """Capture an hourly forecast before passes; never silently extrapolate it."""

    endpoint = OPEN_METEO_FORECAST_ENDPOINT
    source = "Open-Meteo Forecast API"

    _VARIABLES = {
        "temperature_2m": "air_temperature_c",
        "relative_humidity_2m": "relative_humidity_percent",
        "surface_pressure": "surface_pressure_kpa",
        "wind_speed_10m": "wind_speed_m_s",
        "precipitation": "precipitation_corrected",
    }

    def __init__(self, **client_kwargs: object) -> None:
        self._client = _CachedJsonClient(**client_kwargs)  # type: ignore[arg-type]

    def fetch(
        self,
        *,
        station_id: int,
        latitude_deg: float,
        longitude_deg: float,
        start: datetime,
        end: datetime,
    ) -> tuple[WeatherRecord, ...]:
        start, end = as_utc(start), as_utc(end)
        if end <= start:
            raise ValueError("forecast interval must be positive")
        params = {
            "latitude": f"{latitude_deg:.6f}",
            "longitude": f"{longitude_deg:.6f}",
            "hourly": ",".join(self._VARIABLES),
            "timezone": "UTC",
            "wind_speed_unit": "ms",
            "start_date": start.date().isoformat(),
            "end_date": (end - timedelta(microseconds=1)).date().isoformat(),
        }
        url = f"{self.endpoint}?{urlencode(params)}"
        payload, retrieved_at, response_hash = self._client.get_json(url)
        hourly = payload.get("hourly")
        if not isinstance(hourly, Mapping) or not isinstance(hourly.get("time"), list):
            raise CovariateError("Open-Meteo response is missing hourly.time")
        hourly_units = payload.get("hourly_units")
        expected_units = {
            "temperature_2m": "°C",
            "relative_humidity_2m": "%",
            "surface_pressure": "hPa",
            "wind_speed_10m": "m/s",
            "precipitation": "mm",
        }
        if not isinstance(hourly_units, Mapping) or any(
            hourly_units.get(name) != unit for name, unit in expected_units.items()
        ):
            raise CovariateError("Open-Meteo response units do not match the frozen contract")
        timestamps = hourly["time"]
        assert isinstance(timestamps, list)
        values: dict[str, list[object]] = {}
        for source_name in self._VARIABLES:
            series = hourly.get(source_name)
            if not isinstance(series, list) or len(series) != len(timestamps):
                raise CovariateError(
                    f"Open-Meteo response has an invalid {source_name} series"
                )
            values[source_name] = series
        records: list[WeatherRecord] = []
        for index, timestamp in enumerate(timestamps):
            if not isinstance(timestamp, str):
                raise CovariateError("Open-Meteo returned a non-string timestamp")
            try:
                parsed = datetime.fromisoformat(timestamp)
                valid_at = (
                    parsed.replace(tzinfo=UTC)
                    if parsed.tzinfo is None
                    else parsed.astimezone(UTC)
                )
            except ValueError as exc:
                raise CovariateError("Open-Meteo returned an invalid timestamp") from exc
            if not start <= valid_at < end:
                continue
            kwargs = {
                target: _optional_source_float(values[source][index])
                for source, target in self._VARIABLES.items()
            }
            pressure_hpa = kwargs["surface_pressure_kpa"]
            kwargs["surface_pressure_kpa"] = (
                pressure_hpa / 10.0 if pressure_hpa is not None else None
            )
            records.append(
                WeatherRecord(
                    station_id=station_id,
                    latitude_deg=latitude_deg,
                    longitude_deg=longitude_deg,
                    valid_at=valid_at,
                    retrieved_at=retrieved_at,
                    source=self.source,
                    source_response_sha256=response_hash,
                    **kwargs,
                )
            )
        return tuple(records)


class OpenMeteoHistoricalForecastClient(OpenMeteoForecastClient):
    """Load forecast-aligned historical weather for model training."""

    endpoint = OPEN_METEO_HISTORICAL_FORECAST_ENDPOINT
    source = "Open-Meteo Historical Forecast API"


def fetch_forecast_covariates(
    stations: Sequence[GroundStation],
    *,
    start: datetime,
    end: datetime,
    user_agent: str,
    cache_dir: Path,
    fetch_bytes: FetchBytes = _default_fetch_bytes,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
    hardware_evidence_sha256: Mapping[int, str] | None = None,
) -> tuple[tuple[WeatherRecord, ...], tuple[KpRecord, ...], tuple[HardwareRecord, ...]]:
    """Capture the currently available <=16-day forecast and station hardware."""

    captured_at = as_utc(now(), name="now")
    cache_namespace = (
        "prospective-capture:"
        + captured_at.isoformat(timespec="microseconds").replace("+00:00", "Z")
    )
    start, end = as_utc(start), as_utc(end)
    # Open-Meteo's public forecast horizon is finite.  Long monthly plans are
    # deliberately sparse beyond it and gain fresh values during daily replans.
    forecast_end = min(
        end,
        captured_at.replace(hour=0, minute=0, second=0, microsecond=0)
        + timedelta(days=16),
    )
    effective_start = max(start, captured_at.replace(minute=0, second=0, microsecond=0))
    weather_client = OpenMeteoForecastClient(
        user_agent=user_agent,
        cache_dir=cache_dir / "open-meteo",
        cache_namespace=cache_namespace,
        fetch_bytes=fetch_bytes,
        now=lambda: captured_at,
    )
    weather: list[WeatherRecord] = []
    if forecast_end > effective_start:
        for station in sorted(stations, key=lambda item: item.station_id):
            if station.satnogs_station_id is None:
                raise CovariateError("forecast station requires a SatNOGS station ID")
            weather.extend(
                weather_client.fetch(
                    station_id=station.satnogs_station_id,
                    latitude_deg=station.latitude_deg,
                    longitude_deg=station.longitude_deg,
                    start=effective_start,
                    end=forecast_end,
                )
            )
    kp_client = GfzKpClient(
        user_agent=user_agent,
        cache_dir=cache_dir / "gfz-kp",
        cache_namespace=cache_namespace,
        fetch_bytes=fetch_bytes,
        now=lambda: captured_at,
    )
    kp = kp_client.fetch(effective_start, end) if end > effective_start else ()
    noaa_client = NoaaKpForecastClient(
        user_agent=user_agent,
        cache_dir=cache_dir / "noaa-swpc-kp",
        cache_namespace=cache_namespace,
        fetch_bytes=fetch_bytes,
        now=lambda: captured_at,
    )
    noaa_kp = (
        noaa_client.fetch(effective_start, end) if end > effective_start else ()
    )
    by_time = {item.valid_at: item for item in kp}
    # NOAA is the prospective forecast source; it wins for matching intervals.
    by_time.update({item.valid_at: item for item in noaa_kp})
    kp = tuple(by_time[key] for key in sorted(by_time))
    hardware = tuple(
        HardwareRecord.from_station(
            station,
            effective_from=captured_at,
            known_at=captured_at,
            source=SATNOGS_HARDWARE_SOURCE,
            evidence_sha256=(
                hardware_evidence_sha256.get(station.satnogs_station_id)
                if hardware_evidence_sha256 is not None
                and station.satnogs_station_id is not None
                else None
            ),
        )
        for station in stations
    )
    if hardware_evidence_sha256 is not None and any(
        item.evidence_sha256 is None for item in hardware
    ):
        raise CovariateError("station hardware evidence is incomplete")
    return tuple(weather), tuple(kp), hardware


class ArchivedEnvironmentProvider:
    """Expose a frozen covariate archive to the production opportunity builder.

    By default only values retrieved at or before ``as_of`` are visible.  The
    explicit retrospective mode exists for evaluation of past observations and
    must not be used to generate a purportedly prospective plan.
    """

    def __init__(
        self,
        weather: Sequence[WeatherRecord],
        kp: Sequence[KpRecord],
        *,
        as_of: datetime,
        retrospective: bool = False,
    ) -> None:
        self.as_of = as_utc(as_of, name="as_of")
        self.retrospective = bool(retrospective)
        self.weather = tuple(weather)
        self.kp = tuple(sorted(kp, key=lambda item: item.valid_at))

    def at(self, station: GroundStation, pass_window: object) -> EnvironmentSnapshot:
        if station.satnogs_station_id is None:
            raise CovariateError("archived environment lookup requires SatNOGS station ID")
        culmination = getattr(pass_window, "culmination_at", None)
        if not isinstance(culmination, datetime):
            raise CovariateError("pass window is missing culmination_at")
        culmination = as_utc(culmination)
        hour = culmination.replace(minute=0, second=0, microsecond=0)

        def visible(retrieved_at: datetime) -> bool:
            return self.retrospective or retrieved_at <= self.as_of

        weather_item = next(
            (
                item
                for item in self.weather
                if item.station_id == station.satnogs_station_id
                and item.valid_at == hour
                and visible(item.retrieved_at)
            ),
            None,
        )
        kp_item = next(
            (
                item
                for item in reversed(self.kp)
                if item.valid_at <= culmination < item.valid_at + timedelta(hours=3)
                and visible(item.retrieved_at)
            ),
            None,
        )
        sources = [
            item.source for item in (weather_item, kp_item) if item is not None
        ]
        return EnvironmentSnapshot(
            valid_at=culmination,
            source=" + ".join(sources) if sources else "missing:frozen-covariate-archive",
            space_weather_kp=kp_item.kp if kp_item else None,
            air_temperature_c=(weather_item.air_temperature_c if weather_item else None),
            relative_humidity_percent=(
                weather_item.relative_humidity_percent if weather_item else None
            ),
            surface_pressure_kpa=(
                weather_item.surface_pressure_kpa if weather_item else None
            ),
            wind_speed_m_s=weather_item.wind_speed_m_s if weather_item else None,
            precipitation_corrected=(
                weather_item.precipitation_corrected if weather_item else None
            ),
        )


def write_covariate_archive(
    path: Path,
    *,
    weather: Sequence[WeatherRecord] = (),
    kp: Sequence[KpRecord] = (),
    hardware: Sequence[HardwareRecord] = (),
) -> Mapping[str, object]:
    contracts = {
        "weather": {
            "sources": sorted({item.source for item in weather}),
            "temporal_alignment": "station ID and exact UTC hour",
            "units": {
                "air_temperature_c": "degC",
                "relative_humidity_percent": "percent",
                "surface_pressure_kpa": "kPa",
                "wind_speed_m_s": "m/s",
                "precipitation_corrected": "mm/hour",
            },
        },
        "space_weather": {
            "sources": sorted({item.source for item in kp}),
            "temporal_alignment": "three-hour interval beginning at valid_at",
            "units": {"space_weather_kp": "dimensionless_0_to_9"},
        },
        "hardware": {
            "sources": sorted({item.source for item in hardware}),
            "temporal_alignment": (
                "latest effective configuration whose known_at is no later than use"
            ),
            "units": {
                "frequency_ranges_hz": "Hz",
                "receive_antenna_gain_dbi": "dBi",
                "system_noise_temperature_k": "K",
            },
        },
        "missingness": (
            "never imputed in the archive; model preprocessing adds an explicit "
            "missing indicator"
        ),
    }
    payload: dict[str, object] = {
        "schema_version": "observation-planning-covariates-v1",
        "created_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "weather": [item.as_dict() for item in weather],
        "space_weather_kp": [item.as_dict() for item in kp],
        "hardware": [item.as_dict() for item in hardware],
        "contracts": contracts,
    }
    payload["content_identity_sha256"] = _canonical_hash(
        {key: payload[key] for key in ("weather", "space_weather_kp", "hardware")}
    )
    payload["contract_identity_sha256"] = _canonical_hash(contracts)
    _atomic_json(path, payload)
    return payload


def read_covariate_archive(
    path: Path,
) -> tuple[tuple[WeatherRecord, ...], tuple[KpRecord, ...], tuple[HardwareRecord, ...]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping) or payload.get("schema_version") != "observation-planning-covariates-v1":
        raise CovariateError("unsupported covariate archive schema")
    expected = payload.get("content_identity_sha256")
    actual = _canonical_hash(
        {key: payload.get(key, []) for key in ("weather", "space_weather_kp", "hardware")}
    )
    if expected != actual:
        raise CovariateError("covariate archive content hash mismatch")
    expected_contract = payload.get("contract_identity_sha256")
    if expected_contract is not None and expected_contract != _canonical_hash(
        payload.get("contracts")
    ):
        raise CovariateError("covariate archive contract hash mismatch")
    return (
        tuple(WeatherRecord.from_dict(item) for item in payload.get("weather", [])),  # type: ignore[arg-type]
        tuple(KpRecord.from_dict(item) for item in payload.get("space_weather_kp", [])),  # type: ignore[arg-type]
        tuple(HardwareRecord.from_dict(item) for item in payload.get("hardware", [])),  # type: ignore[arg-type]
    )


_COVARIATE_EVIDENCE_ORIGINS = frozenset(
    {
        "api.open-meteo.com",
        "historical-forecast-api.open-meteo.com",
        "kp.gfz.de",
        "network.satnogs.org",
        "power.larc.nasa.gov",
        "services.swpc.noaa.gov",
    }
)


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _required_covariate_response_hashes(
    weather: Sequence[WeatherRecord],
    kp: Sequence[KpRecord],
    hardware: Sequence[HardwareRecord],
) -> set[str]:
    required = {
        *(item.source_response_sha256 for item in weather),
        *(item.source_response_sha256 for item in kp),
    }
    for item in hardware:
        if not _is_satnogs_hardware_source(item.source):
            continue
        if item.evidence_sha256 is None:
            raise CovariateError("SatNOGS hardware record lacks raw response evidence")
        required.add(item.evidence_sha256)
    return required


def _is_satnogs_hardware_source(source: str) -> bool:
    return source in {
        SATNOGS_HARDWARE_SOURCE,
        "https://network.satnogs.org/api/",
    }


def _evidence_record(
    *, body: bytes, url: object, retrieved_at: object, cache_format: str
) -> Mapping[str, object]:
    if cache_format not in {
        "public-covariate-http-cache-v1",
        "satnogs-http-cache-v1",
    }:
        raise CovariateError("covariate evidence cache format is invalid")
    if not isinstance(url, str):
        raise CovariateError("covariate evidence URL is missing")
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.netloc not in _COVARIATE_EVIDENCE_ORIGINS:
        raise CovariateError("covariate evidence URL is outside an approved origin")
    timestamp = parse_api_datetime(retrieved_at, name="evidence.retrieved_at")
    return {
        "body_sha256": hashlib.sha256(body).hexdigest(),
        "body_byte_length": len(body),
        "body_base64": base64.b64encode(body).decode("ascii"),
        "url": url,
        "retrieved_at": timestamp.isoformat().replace("+00:00", "Z"),
        "cache_format": cache_format,
    }


def write_covariate_evidence_manifest(
    covariate_archive_path: Path,
    cache_dir: Path,
    output_path: Path,
) -> Mapping[str, object]:
    """Freeze the exact upstream HTTP bodies referenced by a covariate archive."""

    weather, kp, hardware = read_covariate_archive(covariate_archive_path)
    required = _required_covariate_response_hashes(weather, kp, hardware)
    expected_public_times: dict[str, datetime] = {}
    for item in (*weather, *kp):
        previous = expected_public_times.setdefault(
            item.source_response_sha256, item.retrieved_at
        )
        if previous != item.retrieved_at:
            raise CovariateError(
                "one covariate response hash has multiple retrieval timestamps"
            )
    records: dict[str, Mapping[str, object]] = {}
    if not cache_dir.is_dir():
        raise CovariateError("covariate evidence cache directory is absent")

    for metadata_path in sorted(cache_dir.rglob("*.meta.json")):
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        if (
            not isinstance(metadata, Mapping)
            or metadata.get("schema_version") != "public-covariate-http-cache-v1"
            or metadata.get("body_sha256") not in required
        ):
            continue
        body_path = metadata_path.with_name(
            metadata_path.name.removesuffix(".meta.json") + ".json"
        )
        try:
            body = body_path.read_bytes()
        except OSError as exc:
            raise CovariateError("covariate evidence body is absent") from exc
        record = _evidence_record(
            body=body,
            url=metadata.get("url"),
            retrieved_at=metadata.get("retrieved_at"),
            cache_format="public-covariate-http-cache-v1",
        )
        if record["body_sha256"] != metadata.get("body_sha256"):
            raise CovariateError("covariate evidence cache hash mismatch")
        expected_time = expected_public_times.get(str(record["body_sha256"]))
        if expected_time is not None and parse_api_datetime(
            record.get("retrieved_at"), name="evidence.retrieved_at"
        ) != expected_time:
            # A mutable endpoint may legitimately return byte-identical bodies
            # on multiple planning days. Bind the evidence to the cache entry
            # captured for this archive, not an older copy with the same hash.
            continue
        records[str(record["body_sha256"])] = record

    for cache_path in sorted(cache_dir.rglob("*.json")):
        if cache_path.name.endswith(".meta.json"):
            continue
        try:
            wrapper = json.loads(cache_path.read_text(encoding="utf-8"))
            if not isinstance(wrapper, Mapping) or "body_base64" not in wrapper:
                continue
            body = base64.b64decode(str(wrapper["body_base64"]), validate=True)
        except (KeyError, OSError, UnicodeDecodeError, ValueError, json.JSONDecodeError):
            continue
        digest = hashlib.sha256(body).hexdigest()
        if digest not in required:
            continue
        record = _evidence_record(
            body=body,
            url=wrapper.get("url"),
            retrieved_at=wrapper.get("fetched_at"),
            cache_format="satnogs-http-cache-v1",
        )
        records[digest] = record

    missing = sorted(required - records.keys())
    if missing:
        raise CovariateError(
            "raw covariate source evidence is incomplete: " + ", ".join(missing)
        )
    ordered_records = [records[digest] for digest in sorted(records)]
    payload: dict[str, object] = {
        "schema_version": "observation-planning-covariate-evidence-v1",
        "created_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "covariate_archive_path": str(covariate_archive_path.resolve()),
        "covariate_archive_sha256": _file_sha256(covariate_archive_path),
        "required_response_sha256": sorted(required),
        "response_count": len(ordered_records),
        "responses": ordered_records,
    }
    payload["content_identity_sha256"] = _canonical_hash(ordered_records)
    _atomic_json(output_path, payload)
    return payload


def validate_covariate_evidence_manifest(
    evidence_manifest_path: Path,
    covariate_archive_path: Path,
) -> Mapping[str, int]:
    """Verify all embedded source bodies and their archive/time bindings."""

    try:
        payload = json.loads(evidence_manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CovariateError("cannot read covariate evidence manifest") from exc
    if (
        not isinstance(payload, Mapping)
        or payload.get("schema_version")
        != "observation-planning-covariate-evidence-v1"
        or payload.get("covariate_archive_sha256")
        != _file_sha256(covariate_archive_path)
    ):
        raise CovariateError("covariate evidence manifest/archive binding failed")
    weather, kp, hardware = read_covariate_archive(covariate_archive_path)
    required = _required_covariate_response_hashes(weather, kp, hardware)
    raw_required = payload.get("required_response_sha256")
    raw_records = payload.get("responses")
    if (
        not isinstance(raw_required, list)
        or raw_required != sorted(required)
        or not isinstance(raw_records, list)
        or payload.get("response_count") != len(raw_records)
        or payload.get("content_identity_sha256") != _canonical_hash(raw_records)
    ):
        raise CovariateError("covariate evidence manifest inventory is invalid")
    evidence_times: dict[str, datetime] = {}
    for raw_record in raw_records:
        if not isinstance(raw_record, Mapping):
            raise CovariateError("covariate evidence record is malformed")
        try:
            body = base64.b64decode(str(raw_record["body_base64"]), validate=True)
            digest = hashlib.sha256(body).hexdigest()
            length = int(raw_record["body_byte_length"])
            normalized = _evidence_record(
                body=body,
                url=raw_record.get("url"),
                retrieved_at=raw_record.get("retrieved_at"),
                cache_format=str(raw_record.get("cache_format", "")),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise CovariateError("covariate evidence record is invalid") from exc
        if (
            digest != raw_record.get("body_sha256")
            or digest not in required
            or length != len(body)
            or dict(normalized) != dict(raw_record)
            or digest in evidence_times
        ):
            raise CovariateError("covariate evidence body verification failed")
        evidence_times[digest] = parse_api_datetime(
            raw_record.get("retrieved_at"), name="evidence.retrieved_at"
        )
    if set(evidence_times) != required:
        raise CovariateError("covariate evidence response set is incomplete")
    if any(
        evidence_times[item.source_response_sha256] != item.retrieved_at
        for item in (*weather, *kp)
    ):
        raise CovariateError("weather/Kp evidence timestamp mismatch")
    if any(
        item.evidence_sha256 is not None
        and evidence_times[item.evidence_sha256] > item.known_at
        for item in hardware
        if _is_satnogs_hardware_source(item.source)
    ):
        raise CovariateError("hardware evidence postdates its known-at boundary")
    return {
        "response_count": len(raw_records),
        "weather_response_count": len(
            {item.source_response_sha256 for item in weather}
        ),
        "kp_response_count": len({item.source_response_sha256 for item in kp}),
        "hardware_response_count": len(
            {
                item.evidence_sha256
                for item in hardware
                if _is_satnogs_hardware_source(item.source)
            }
        ),
    }


def _merged_weather_intervals(
    required_hours: Sequence[datetime], *, maximum_missing_days: int = 7
) -> tuple[tuple[datetime, datetime], ...]:
    """Coalesce observation days for efficient weather calls, not archive padding."""

    if maximum_missing_days < 0 or maximum_missing_days > 31:
        raise ValueError("maximum_missing_days must be in [0, 31]")
    dates = sorted({as_utc(item).date() for item in required_hours})
    if not dates:
        return ()
    result: list[tuple[datetime, datetime]] = []
    first = previous = dates[0]
    for current in dates[1:]:
        missing_days = (current - previous).days - 1
        if missing_days > maximum_missing_days:
            result.append(
                (
                    datetime.combine(first, datetime.min.time(), tzinfo=UTC),
                    datetime.combine(
                        previous + timedelta(days=1), datetime.min.time(), tzinfo=UTC
                    ),
                )
            )
            first = current
        previous = current
    result.append(
        (
            datetime.combine(first, datetime.min.time(), tzinfo=UTC),
            datetime.combine(
                previous + timedelta(days=1), datetime.min.time(), tzinfo=UTC
            ),
        )
    )
    return tuple(result)


def _historical_weather_location_is_verified(row: NormalizedObservation) -> bool:
    """Whether a row carries a location captured no later than reception.

    The Network observation serializer currently substitutes the station's
    present-day coordinates.  Historical weather must therefore be joined only
    when the observation's client metadata preserved the actual capture
    location.
    """

    return (
        row.station_location_source == "satnogs-client-metadata"
        and row.station_location_recorded_at is not None
        and row.station_location_recorded_at <= row.start
    )


def fetch_historical_covariates(
    rows: Sequence[NormalizedObservation],
    *,
    user_agent: str,
    cache_dir: Path,
    minimum_request_interval_seconds: float = 1.0,
    fetch_bytes: FetchBytes = _default_fetch_bytes,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
    weather_maximum_missing_days: int = 7,
) -> tuple[tuple[WeatherRecord, ...], tuple[KpRecord, ...]]:
    """Fetch the minimal real-data archive covering a normalized cohort."""

    if not rows:
        raise DatasetIntegrityError("cannot fetch covariates for an empty dataset")
    if minimum_request_interval_seconds < 0:
        raise ValueError("minimum request interval must be non-negative")
    if weather_maximum_missing_days < 0 or weather_maximum_missing_days > 31:
        raise ValueError("weather maximum missing days must be in [0, 31]")
    weather_client = OpenMeteoHistoricalForecastClient(
        user_agent=user_agent,
        cache_dir=cache_dir / "open-meteo-historical-forecast",
        fetch_bytes=fetch_bytes,
        now=now,
    )
    kp_client = GfzKpClient(
        user_agent=user_agent,
        cache_dir=cache_dir / "gfz-kp",
        fetch_bytes=fetch_bytes,
        now=now,
    )
    weather: list[WeatherRecord] = []
    by_location: dict[tuple[int, float, float], list[NormalizedObservation]] = {}
    for row in rows:
        if not _historical_weather_location_is_verified(row):
            continue
        key = (
            row.station_id,
            round(row.station_latitude_deg, 6),
            round(row.station_longitude_deg, 6),
        )
        by_location.setdefault(key, []).append(row)
    last_request = 0.0

    def throttle() -> None:
        nonlocal last_request
        delay = minimum_request_interval_seconds - (time.monotonic() - last_request)
        if delay > 0:
            time.sleep(delay)
        last_request = time.monotonic()

    for (station_id, latitude, longitude), station_rows in sorted(by_location.items()):
        required_hours = {
            row.start.replace(minute=0, second=0, microsecond=0)
            for row in station_rows
        }
        intervals = _merged_weather_intervals(
            tuple(required_hours),
            maximum_missing_days=weather_maximum_missing_days,
        )
        for interval_start, interval_end in intervals:
            cursor = interval_start
            while cursor < interval_end:
                chunk_end = min(interval_end, cursor + timedelta(days=366))
                throttle()
                fetched = weather_client.fetch(
                    station_id=station_id,
                    latitude_deg=latitude,
                    longitude_deg=longitude,
                    start=cursor,
                    end=chunk_end,
                )
                weather.extend(
                    item for item in fetched if item.valid_at in required_hours
                )
                cursor = chunk_end
    kp: list[KpRecord] = []
    cursor = min(row.start for row in rows).replace(minute=0, second=0, microsecond=0)
    end = max(row.end for row in rows) + timedelta(hours=3)
    while cursor < end:
        chunk_end = min(end, cursor + timedelta(days=31))
        throttle()
        kp.extend(kp_client.fetch(cursor, chunk_end))
        cursor = chunk_end
    return tuple(weather), tuple(kp)


def enrich_rows(
    rows: Sequence[NormalizedObservation],
    *,
    weather: Sequence[WeatherRecord],
    kp: Sequence[KpRecord],
    hardware: Sequence[HardwareRecord] = (),
) -> tuple[NormalizedObservation, ...]:
    """Time-align covariates without target leakage or silent extrapolation."""

    weather_index: dict[tuple[int, datetime], list[WeatherRecord]] = {}
    for item in weather:
        weather_index.setdefault((item.station_id, item.valid_at), []).append(item)
    kp_sorted = tuple(sorted(kp, key=lambda item: item.valid_at))
    kp_times = tuple(item.valid_at for item in kp_sorted)
    hardware_by_station: dict[int, list[HardwareRecord]] = {}
    for item in hardware:
        hardware_by_station.setdefault(item.station_id, []).append(item)
    enriched: list[NormalizedObservation] = []
    for row in rows:
        hour = row.start.replace(minute=0, second=0, microsecond=0)
        weather_candidates = (
            weather_index.get((row.station_id, hour), [])
            if _historical_weather_location_is_verified(row)
            else []
        )
        weather_item = min(
            weather_candidates,
            key=lambda item: (
                abs(item.latitude_deg - row.station_latitude_deg)
                + abs(item.longitude_deg - row.station_longitude_deg)
            ),
            default=None,
        )
        if weather_item is not None and (
            abs(weather_item.latitude_deg - row.station_latitude_deg) > 0.05
            or abs(weather_item.longitude_deg - row.station_longitude_deg) > 0.05
        ):
            weather_item = None
        kp_index = bisect_right(kp_times, row.start) - 1
        kp_item = kp_sorted[kp_index] if kp_index >= 0 else None
        if kp_item is not None and not (
            kp_item.valid_at <= row.start < kp_item.valid_at + timedelta(hours=3)
        ):
            kp_item = None
        candidates = [
            item
            for item in hardware_by_station.get(row.station_id, [])
            if item.effective_from <= row.start and item.known_at <= row.start
        ]
        hardware_item = max(
            candidates,
            key=lambda item: (item.effective_from, item.known_at, item.configuration_id),
            default=None,
        )
        enriched.append(
            replace(
                row,
                schema_version="satnogs-observation-features-v3",
                weather_air_temperature_c=(
                    weather_item.air_temperature_c if weather_item else None
                ),
                weather_relative_humidity_percent=(
                    weather_item.relative_humidity_percent if weather_item else None
                ),
                weather_surface_pressure_kpa=(
                    weather_item.surface_pressure_kpa if weather_item else None
                ),
                weather_wind_speed_m_s=(
                    weather_item.wind_speed_m_s if weather_item else None
                ),
                weather_precipitation_corrected=(
                    weather_item.precipitation_corrected if weather_item else None
                ),
                weather_source=weather_item.source if weather_item else None,
                weather_valid_at=weather_item.valid_at if weather_item else None,
                weather_retrieved_at=weather_item.retrieved_at if weather_item else None,
                space_weather_kp=kp_item.kp if kp_item else None,
                space_weather_source=kp_item.source if kp_item else None,
                space_weather_valid_at=kp_item.valid_at if kp_item else None,
                space_weather_retrieved_at=kp_item.retrieved_at if kp_item else None,
                antenna_configuration_id=(
                    hardware_item.configuration_id if hardware_item else None
                ),
                antenna_type=hardware_item.antenna_type if hardware_item else None,
                antenna_frequency_supported=(
                    hardware_item.supports(row.frequency_hz) if hardware_item else None
                ),
                receive_antenna_gain_dbi=(
                    hardware_item.receive_antenna_gain_dbi if hardware_item else None
                ),
                system_noise_temperature_k=(
                    hardware_item.system_noise_temperature_k if hardware_item else None
                ),
                antenna_source=hardware_item.source if hardware_item else None,
                antenna_effective_from=(
                    hardware_item.effective_from if hardware_item else None
                ),
                antenna_recorded_at=hardware_item.known_at if hardware_item else None,
            )
        )
    return tuple(enriched)


def enrich_dataset_file(
    dataset_path: Path,
    covariate_archive_path: Path,
    *,
    output_path: Path,
    manifest_path: Path,
) -> Mapping[str, object]:
    rows = read_normalized_jsonl(dataset_path)
    weather, kp, hardware = read_covariate_archive(covariate_archive_path)
    enriched = enrich_rows(rows, weather=weather, kp=kp, hardware=hardware)
    write_normalized_jsonl(output_path, enriched)
    manifest: dict[str, object] = {
        "schema_version": "observation-planning-enriched-dataset-manifest-v1",
        "created_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "input_dataset_path": str(dataset_path),
        "input_dataset_sha256": hashlib.sha256(dataset_path.read_bytes()).hexdigest(),
        "covariate_archive_path": str(covariate_archive_path),
        "covariate_archive_sha256": hashlib.sha256(
            covariate_archive_path.read_bytes()
        ).hexdigest(),
        "output_dataset_path": str(output_path),
        "output_dataset_sha256": hashlib.sha256(output_path.read_bytes()).hexdigest(),
        "row_count": len(enriched),
        "feature_availability": {
            "terrestrial_weather_rows": sum(
                row.weather_valid_at is not None for row in enriched
            ),
            "space_weather_rows": sum(
                row.space_weather_valid_at is not None for row in enriched
            ),
            "antenna_configuration_rows": sum(
                row.antenna_configuration_id is not None for row in enriched
            ),
            "antenna_gain_rows": sum(
                row.receive_antenna_gain_dbi is not None for row in enriched
            ),
            "system_noise_rows": sum(
                row.system_noise_temperature_k is not None for row in enriched
            ),
        },
        "leakage_guards": {
            "weather_aligned_by_station_and_utc_hour": True,
            "kp_aligned_by_containing_three_hour_interval": True,
            "hardware_known_at_no_later_than_observation": True,
            "unmatched_values_remain_missing": True,
        },
    }
    _atomic_json(manifest_path, manifest)
    return manifest
