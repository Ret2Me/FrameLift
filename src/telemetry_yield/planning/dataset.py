"""Leakage-resistant normalization of historical SatNOGS observations."""

from __future__ import annotations

import json
import math
import re
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterable, Mapping, Sequence

from .models import GroundStation, ReceiverResource, TleSnapshot, as_utc
from .orbit import SPEED_OF_LIGHT_KM_S, OrbitPredictionError, Sgp4PassPredictor
from .tle import TleError, parse_tle_text, tle_checksum_valid


NON_PACKET_MODES = frozenset({"CW", "FM", "AM", "USB", "LSB"})


class DatasetIntegrityError(ValueError):
    """The source snapshot cannot be normalized without ambiguity."""


def satnogs_mode_is_packet_capable(value: object) -> bool:
    """Return the frozen decode-task eligibility for a transmitter mode."""

    if value is None or not str(value).strip():
        return False
    return str(value).strip().upper() not in NON_PACKET_MODES


def satnogs_outcome_labels(
    waterfall_status: object,
    demoddata: object,
    *,
    packet_capable: bool,
) -> tuple[int | None, int | None]:
    """Apply the one fail-closed label contract used by every study phase.

    Waterfall review is the sole source of the primary signal label. A
    non-empty demodulation artifact may independently establish a positive
    decode, but it must not promote an unreviewed waterfall to positive signal
    or override an explicit negative review.
    """

    waterfall = str(waterfall_status or "unknown").casefold()
    signal = (
        1
        if waterfall == "with-signal"
        else 0
        if waterfall == "without-signal"
        else None
    )
    decoded_artifact = isinstance(demoddata, list) and bool(demoddata)
    if signal == 0 and decoded_artifact:
        raise DatasetIntegrityError("contradictory_signal_and_demoddata")

    decode = None
    if packet_capable:
        if signal == 1 and not isinstance(demoddata, list):
            raise DatasetIntegrityError("malformed_demoddata")
        if decoded_artifact:
            decode = 1
        elif signal == 1 and isinstance(demoddata, list):
            decode = 0
    return signal, decode


def parse_api_datetime(value: object, *, name: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise DatasetIntegrityError(f"{name} is missing")
    candidate = value.strip()
    if candidate.endswith("Z"):
        candidate = candidate[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise DatasetIntegrityError(f"{name} is not an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise DatasetIntegrityError(f"{name} must include a timezone")
    return parsed.astimezone(UTC)


def _finite_float(value: object, *, name: str) -> float:
    if isinstance(value, bool):
        raise DatasetIntegrityError(f"{name} must be numeric")
    try:
        result = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise DatasetIntegrityError(f"{name} must be numeric") from exc
    if not math.isfinite(result):
        raise DatasetIntegrityError(f"{name} must be finite")
    return result


def _optional_float(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _optional_positive_float(value: object) -> float | None:
    result = _optional_float(value)
    return result if result is not None and result > 0 else None


def _bounded_optional_text(value: object, *, maximum_length: int = 128) -> str | None:
    if value is None or isinstance(value, (bool, Mapping, list, tuple)):
        return None
    result = str(value).strip()
    if (
        not result
        or len(result) > maximum_length
        or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 ._+()#-]*", result) is None
    ):
        return None
    return result


def _parse_client_metadata(
    value: object,
) -> tuple[Mapping[str, object] | None, str]:
    """Parse the public capture metadata without retaining its raw payload.

    SatNOGS currently serializes this field as a JSON string, while fixtures and
    future API versions may already expose an object.  Malformed optional
    metadata must not discard an otherwise usable observation.
    """

    if value is None or (isinstance(value, str) and not value.strip()):
        return None, "absent"
    parsed: object = value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return None, "malformed_json"
    if not isinstance(parsed, Mapping):
        return None, "non_object"
    return parsed, "parsed"


def _captured_station_location(
    metadata: Mapping[str, object] | None,
    parse_status: str,
) -> tuple[tuple[float, float, float] | None, str]:
    if metadata is None:
        return None, parse_status
    values = tuple(metadata.get(key) for key in ("latitude", "longitude", "elevation"))
    if all(value is None for value in values):
        return None, "parsed_location_missing"
    if any(value is None for value in values):
        return None, "parsed_location_incomplete"
    latitude = _optional_float(values[0])
    longitude = _optional_float(values[1])
    altitude = _optional_float(values[2])
    if (
        latitude is None
        or longitude is None
        or altitude is None
        or not -90 <= latitude <= 90
        or not -180 <= longitude <= 180
    ):
        return None, "parsed_location_invalid"
    return (latitude, longitude, altitude), "parsed_location_valid"


def _soapy_driver(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text or len(text) > 512 or any(ord(character) < 32 for character in text):
        return None
    for component in text.split(","):
        key, separator, raw_value = component.partition("=")
        if separator and key.strip().casefold() == "driver":
            return _bounded_optional_text(raw_value.casefold(), maximum_length=64)
    return None


def _captured_receiver_configuration(
    metadata: Mapping[str, object] | None,
) -> Mapping[str, object]:
    radio = metadata.get("radio") if metadata is not None else None
    if not isinstance(radio, Mapping):
        return {}
    parameters = radio.get("parameters")
    parameters = parameters if isinstance(parameters, Mapping) else {}
    values: dict[str, object] = {
        "captured_receiver_radio_name": _bounded_optional_text(radio.get("name")),
        "captured_receiver_radio_version": _bounded_optional_text(radio.get("version")),
        "captured_receiver_driver": _soapy_driver(parameters.get("soapy-rx-device")),
        "captured_receiver_gain_mode": _bounded_optional_text(parameters.get("gain-mode")),
        # SatNOGS Client documents this as receiver/SoapySDR RF gain in dB.
        # It is deliberately not labelled or used as physical antenna gain.
        "captured_receiver_rf_gain_db": _optional_float(parameters.get("gain")),
        "captured_receiver_antenna_port": _bounded_optional_text(
            parameters.get("antenna")
        ),
        "captured_receiver_sample_rate_hz": _optional_positive_float(
            parameters.get("samp-rate-rx")
        ),
        "captured_receiver_ppm_error": _optional_float(parameters.get("ppm")),
        "captured_receiver_bandwidth_hz": _optional_positive_float(
            parameters.get("bw")
        ),
    }
    return {key: value for key, value in values.items() if value is not None}


def _positive_int(value: object, *, name: str) -> int:
    if isinstance(value, bool):
        raise DatasetIntegrityError(f"{name} must be a positive integer")
    try:
        result = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise DatasetIntegrityError(f"{name} must be a positive integer") from exc
    if result <= 0:
        raise DatasetIntegrityError(f"{name} must be a positive integer")
    return result


@dataclass(frozen=True, slots=True)
class NormalizedObservation:
    schema_version: str
    observation_id: int
    start: datetime
    end: datetime
    duration_seconds: float
    norad_id: int
    station_id: int
    station_name: str
    station_latitude_deg: float
    station_longitude_deg: float
    station_altitude_m: float
    transmitter_uuid: str
    transmitter_mode: str | None
    transmitter_baud: float | None
    frequency_hz: float | None
    transmitter_status: str | None
    tle_name: str
    tle_line1: str
    tle_line2: str
    tle_epoch: datetime
    tle_fingerprint: str
    tle_age_hours: float
    max_elevation_deg: float
    min_range_km: float
    max_abs_doppler_hz: float | None
    rise_azimuth_deg: float
    set_azimuth_deg: float
    signal_present: int | None
    decode_success_given_signal: int | None
    source_status: str
    source_waterfall_status: str
    # Optional, time-aligned covariates.  They live at the end of the record so
    # v1 JSONL fixtures and frozen v1-v3 publication artifacts remain readable.
    weather_air_temperature_c: float | None = None
    weather_relative_humidity_percent: float | None = None
    weather_surface_pressure_kpa: float | None = None
    weather_wind_speed_m_s: float | None = None
    weather_precipitation_corrected: float | None = None
    weather_source: str | None = None
    weather_valid_at: datetime | None = None
    weather_retrieved_at: datetime | None = None
    space_weather_kp: float | None = None
    space_weather_source: str | None = None
    space_weather_valid_at: datetime | None = None
    space_weather_retrieved_at: datetime | None = None
    antenna_configuration_id: str | None = None
    antenna_type: str | None = None
    antenna_frequency_supported: bool | None = None
    receive_antenna_gain_dbi: float | None = None
    system_noise_temperature_k: float | None = None
    antenna_source: str | None = None
    antenna_effective_from: datetime | None = None
    antenna_recorded_at: datetime | None = None
    client_metadata_parse_status: str | None = None
    station_location_source: str | None = None
    station_location_recorded_at: datetime | None = None
    captured_receiver_configuration_source: str | None = None
    captured_receiver_configuration_recorded_at: datetime | None = None
    captured_receiver_radio_name: str | None = None
    captured_receiver_radio_version: str | None = None
    captured_receiver_driver: str | None = None
    captured_receiver_gain_mode: str | None = None
    captured_receiver_rf_gain_db: float | None = None
    captured_receiver_antenna_port: str | None = None
    captured_receiver_sample_rate_hz: float | None = None
    captured_receiver_ppm_error: float | None = None
    captured_receiver_bandwidth_hz: float | None = None

    def as_dict(self) -> dict[str, object]:
        payload = asdict(self)
        for key in (
            "start",
            "end",
            "tle_epoch",
            "weather_valid_at",
            "weather_retrieved_at",
            "space_weather_valid_at",
            "space_weather_retrieved_at",
            "antenna_effective_from",
            "antenna_recorded_at",
            "station_location_recorded_at",
            "captured_receiver_configuration_recorded_at",
        ):
            value = payload[key]
            if value is not None:
                assert isinstance(value, datetime)
                payload[key] = value.astimezone(UTC).isoformat().replace("+00:00", "Z")
        return payload

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> "NormalizedObservation":
        converted = dict(payload)
        for key in ("start", "end", "tle_epoch"):
            converted[key] = parse_api_datetime(converted.get(key), name=key)
        for key in (
            "weather_valid_at",
            "weather_retrieved_at",
            "space_weather_valid_at",
            "space_weather_retrieved_at",
            "antenna_effective_from",
            "antenna_recorded_at",
            "station_location_recorded_at",
            "captured_receiver_configuration_recorded_at",
        ):
            if converted.get(key) is not None:
                converted[key] = parse_api_datetime(converted[key], name=key)
        return cls(**converted)  # type: ignore[arg-type]


@dataclass(frozen=True, slots=True)
class DatasetBuildResult:
    rows: tuple[NormalizedObservation, ...]
    exclusion_counts: Mapping[str, int]
    duplicate_count: int
    input_count: int

    @property
    def known_signal_count(self) -> int:
        return sum(row.signal_present is not None for row in self.rows)

    @property
    def conditional_decode_count(self) -> int:
        return sum(row.decode_success_given_signal is not None for row in self.rows)


def _canonical_observation(observation: Mapping[str, object]) -> str:
    return json.dumps(observation, sort_keys=True, separators=(",", ":"), default=str)


def _embedded_tle(
    observation: Mapping[str, object], norad_id: int, start: datetime
) -> TleSnapshot:
    line1, line2 = observation.get("tle1"), observation.get("tle2")
    if not isinstance(line1, str) or not isinstance(line2, str):
        raise DatasetIntegrityError("missing_tle")

    if not tle_checksum_valid(line1) or not tle_checksum_valid(line2):
        raise DatasetIntegrityError("tle_checksum_mismatch")
    name_value = observation.get("tle0")
    name = str(name_value).removeprefix("0 ") if name_value else f"NORAD {norad_id}"
    try:
        snapshot = parse_tle_text(
            f"{name}\n{line1}\n{line2}\n",
            norad_id=norad_id,
            fetched_at=start,
            source=f"satnogs-observation:{observation.get('id', 'unknown')}",
        )
    except (TleError, ValueError) as exc:
        raise DatasetIntegrityError("malformed_or_mismatched_tle") from exc
    try:
        line2_catalog = int(snapshot.line2[2:7])
    except (ValueError, IndexError) as exc:
        raise DatasetIntegrityError("malformed_or_mismatched_tle") from exc
    if line2_catalog != norad_id:
        raise DatasetIntegrityError("malformed_or_mismatched_tle")
    return snapshot


def _frequency(observation: Mapping[str, object]) -> float | None:
    for key in (
        "observation_frequency",
        "center_frequency",
        "transmitter_downlink_low",
        "transmitter_downlink_high",
    ):
        value = _optional_float(observation.get(key))
        if value is not None and value > 0:
            return value
    return None


def normalize_satnogs_observation(
    observation: Mapping[str, object],
    *,
    retrieved_at: datetime,
    predictor: Sgp4PassPredictor,
    geometry_step_seconds: int = 30,
) -> NormalizedObservation:
    """Normalize one observation or raise a stable, countable exclusion reason."""

    retrieved_at = as_utc(retrieved_at, name="retrieved_at")
    observation_id = _positive_int(observation.get("id"), name="observation_id")
    start = parse_api_datetime(observation.get("start"), name="start")
    end = parse_api_datetime(observation.get("end"), name="end")
    if end <= start:
        raise DatasetIntegrityError("non_positive_duration")
    status = str(observation.get("status") or "unknown").casefold()
    if status == "failed":
        raise DatasetIntegrityError("failed_job")
    if status == "future" or start >= retrieved_at:
        raise DatasetIntegrityError("future_job")
    if observation.get("transmitter_unconfirmed") is True:
        raise DatasetIntegrityError("unconfirmed_transmitter")

    norad_id = _positive_int(observation.get("norad_cat_id"), name="norad_id")
    if observation.get("ground_station") is None:
        raise DatasetIntegrityError("missing_station")
    station_id = _positive_int(observation.get("ground_station"), name="station_id")
    client_metadata, client_metadata_status = _parse_client_metadata(
        observation.get("client_metadata")
    )
    captured_location, client_metadata_status = _captured_station_location(
        client_metadata, client_metadata_status
    )
    receiver_configuration = _captured_receiver_configuration(client_metadata)
    if captured_location is not None:
        latitude, longitude, altitude = captured_location
        station_location_source = "satnogs-client-metadata"
        station_location_recorded_at = start
    else:
        if any(
            observation.get(key) is None
            for key in ("station_lat", "station_lng", "station_alt")
        ):
            raise DatasetIntegrityError("missing_station_coordinates")
        latitude = _finite_float(observation.get("station_lat"), name="station_lat")
        longitude = _finite_float(observation.get("station_lng"), name="station_lng")
        altitude = _finite_float(observation.get("station_alt"), name="station_alt")
        if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
            raise DatasetIntegrityError("invalid_station_coordinates")
        station_location_source = "satnogs-api-current-station"
        station_location_recorded_at = None
    transmitter = observation.get("transmitter_uuid") or observation.get("transmitter")
    if not isinstance(transmitter, str) or not transmitter.strip():
        raise DatasetIntegrityError("missing_transmitter")

    tle = _embedded_tle(observation, norad_id, start)
    tle_age_hours = (start - tle.epoch).total_seconds() / 3600.0
    if tle_age_hours < -1e-6:
        raise DatasetIntegrityError("tle_after_observation_start")

    # These three scheduling fields are cached on the observation itself.  Use
    # them for probability evaluation instead of reconstructing a pass from a
    # station profile that may have changed after capture.
    source_max_elevation = _finite_float(
        observation.get("max_altitude"), name="max_altitude"
    )
    source_rise_azimuth = _finite_float(
        observation.get("rise_azimuth"), name="rise_azimuth"
    )
    source_set_azimuth = _finite_float(
        observation.get("set_azimuth"), name="set_azimuth"
    )
    if (
        not -90 <= source_max_elevation <= 90
        or not 0 <= source_rise_azimuth <= 360
        or not 0 <= source_set_azimuth <= 360
    ):
        raise DatasetIntegrityError("invalid_cached_schedule_geometry")

    station = GroundStation(
        station_id=str(station_id),
        latitude_deg=latitude,
        longitude_deg=longitude,
        altitude_m=altitude,
        resources=(ReceiverResource("historical"),),
        minimum_elevation_deg=0.0,
        satnogs_station_id=station_id,
    )
    frequency = _frequency(observation)
    try:
        samples = predictor.sample_track(
            tle,
            station,
            start,
            end,
            step_seconds=geometry_step_seconds,
        )
    except (OrbitPredictionError, ValueError) as exc:
        raise DatasetIntegrityError("sgp4_failure") from exc
    max_abs_doppler = None
    if frequency is not None:
        max_abs_doppler = max(
            abs(-frequency * sample.range_rate_km_s / SPEED_OF_LIGHT_KM_S)
            for sample in samples
        )

    raw_mode = observation.get("transmitter_mode")
    mode = str(raw_mode).strip() if raw_mode is not None and str(raw_mode).strip() else None
    packet_capable = satnogs_mode_is_packet_capable(mode)
    demoddata = observation.get("demoddata")
    waterfall = str(observation.get("waterfall_status") or "unknown").casefold()
    signal, decode = satnogs_outcome_labels(
        waterfall,
        demoddata,
        packet_capable=packet_capable,
    )
    return NormalizedObservation(
        schema_version="satnogs-observation-features-v3",
        observation_id=observation_id,
        start=start,
        end=end,
        duration_seconds=(end - start).total_seconds(),
        norad_id=norad_id,
        station_id=station_id,
        station_name=str(observation.get("station_name") or station_id),
        station_latitude_deg=latitude,
        station_longitude_deg=longitude,
        station_altitude_m=altitude,
        transmitter_uuid=transmitter.strip(),
        transmitter_mode=mode,
        transmitter_baud=_optional_float(observation.get("transmitter_baud")),
        frequency_hz=frequency,
        transmitter_status=(
            str(observation["transmitter_status"])
            if observation.get("transmitter_status") is not None
            else None
        ),
        tle_name=tle.name,
        tle_line1=tle.line1,
        tle_line2=tle.line2,
        tle_epoch=tle.epoch,
        tle_fingerprint=tle.fingerprint,
        tle_age_hours=tle_age_hours,
        max_elevation_deg=source_max_elevation,
        min_range_km=min(sample.range_km for sample in samples),
        max_abs_doppler_hz=max_abs_doppler,
        rise_azimuth_deg=source_rise_azimuth,
        set_azimuth_deg=source_set_azimuth,
        signal_present=signal,
        decode_success_given_signal=decode,
        source_status=status,
        source_waterfall_status=waterfall,
        client_metadata_parse_status=client_metadata_status,
        station_location_source=station_location_source,
        station_location_recorded_at=station_location_recorded_at,
        captured_receiver_configuration_source=(
            "satnogs-client-metadata" if receiver_configuration else None
        ),
        captured_receiver_configuration_recorded_at=(
            start if receiver_configuration else None
        ),
        **receiver_configuration,
    )


def build_normalized_dataset(
    observations: Iterable[Mapping[str, object]],
    *,
    retrieved_at: datetime,
    predictor: Sgp4PassPredictor | None = None,
    geometry_step_seconds: int = 30,
) -> DatasetBuildResult:
    """Deduplicate, normalize and count every fail-closed exclusion."""

    predictor = predictor or Sgp4PassPredictor(step_seconds=geometry_step_seconds)
    seen: dict[int, str] = {}
    rows: list[NormalizedObservation] = []
    exclusions: Counter[str] = Counter()
    duplicate_count = 0
    input_count = 0
    for observation in observations:
        input_count += 1
        try:
            observation_id = _positive_int(observation.get("id"), name="observation_id")
        except DatasetIntegrityError:
            exclusions["invalid_observation_id"] += 1
            continue
        canonical = _canonical_observation(observation)
        previous = seen.get(observation_id)
        if previous is not None:
            if previous != canonical:
                raise DatasetIntegrityError(
                    f"conflicting duplicate observation ID {observation_id}"
                )
            duplicate_count += 1
            continue
        seen[observation_id] = canonical
        try:
            rows.append(
                normalize_satnogs_observation(
                    observation,
                    retrieved_at=retrieved_at,
                    predictor=predictor,
                    geometry_step_seconds=geometry_step_seconds,
                )
            )
        except DatasetIntegrityError as exc:
            reason = str(exc)
            if reason.startswith(("start ", "end ", "station_", "norad_", "observation_")):
                reason = "malformed_required_field"
            exclusions[reason] += 1
    rows.sort(key=lambda row: (row.start, row.observation_id))
    return DatasetBuildResult(
        rows=tuple(rows),
        exclusion_counts=dict(sorted(exclusions.items())),
        duplicate_count=duplicate_count,
        input_count=input_count,
    )


def write_normalized_jsonl(path: Path, rows: Sequence[NormalizedObservation]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row.as_dict(), sort_keys=True, separators=(",", ":")))
            handle.write("\n")


def read_normalized_jsonl(path: Path) -> tuple[NormalizedObservation, ...]:
    rows: list[NormalizedObservation] = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as exc:
                raise DatasetIntegrityError(f"invalid JSONL at line {line_number}") from exc
            if not isinstance(payload, Mapping):
                raise DatasetIntegrityError(f"non-object JSONL at line {line_number}")
            rows.append(NormalizedObservation.from_dict(payload))
    return tuple(rows)
