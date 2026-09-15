"""Bounded empirical analysis for headerless CAMRAS IQ recordings.

The functions in this module never modify an input recording.  NumPy and
sgp4 are optional project analysis dependencies and are imported lazily so
the core package remains usable without the analysis extra.
"""

from __future__ import annotations

import hashlib
import math
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Mapping, Sequence


BYTES_PER_COMPLEX_INT16 = 4
SPEED_OF_LIGHT_KM_S = 299_792.458
WGS84_A_KM = 6_378.137
WGS84_FLATTENING = 1.0 / 298.257223563
EARTH_ROTATION_RAD_S = 7.29211514670698e-5


class IQAnalysisError(RuntimeError):
    """Raised when an IQ analysis cannot be performed safely."""


@dataclass(frozen=True)
class IQAmplitudeMetrics:
    byte_order: str
    component_order: str
    file_size_bytes: int
    complex_sample_count: int
    sampled_complex_count: int
    rms_normalized: float
    endpoint_clip_fraction: float
    dc_i_normalized: float
    dc_q_normalized: float


@dataclass(frozen=True)
class DopplerRidgeScore:
    model: str
    window_count: int
    median_ridge_to_noise: float
    p90_ridge_to_noise: float
    fraction_above_ratio_8: float
    median_peak_error_hz: float


@dataclass(frozen=True)
class SampleRateInference:
    state: str
    sample_rate_hz: float | None
    implied_full_coverage_rate_hz: float
    complex_sample_count: int
    observation_duration_seconds: float
    candidate_duration_deltas_seconds: tuple[tuple[float, float], ...]
    max_edge_margin_seconds: float
    reason: str


@dataclass(frozen=True)
class DopplerStateInference:
    state: str
    winning_score: float
    competing_score: float
    score_ratio: float
    reason: str


def _require_numpy():
    try:
        import numpy as np
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise IQAnalysisError(
            "IQ spectral analysis requires the project analysis extra (numpy)"
        ) from exc
    return np


def _require_sgp4():
    try:
        from sgp4.api import Satrec, jday
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise IQAnalysisError(
            "TLE Doppler analysis requires the project analysis extra (sgp4)"
        ) from exc
    return Satrec, jday


def raw_complex_sample_count(path: str | os.PathLike[str]) -> int:
    """Validate a headerless complex-int16 file and return its sample count."""

    size = Path(path).stat().st_size
    if size == 0:
        raise IQAnalysisError("IQ recording is empty")
    if size % BYTES_PER_COMPLEX_INT16:
        raise IQAnalysisError(
            f"IQ recording has {size} bytes; complex int16 requires a multiple of 4"
        )
    return size // BYTES_PER_COMPLEX_INT16


def infer_sample_rate_from_timing(
    *,
    file_size_bytes: int,
    observation_start_utc: str | datetime,
    observation_end_utc: str | datetime,
    candidate_rates_hz: Sequence[float] = (48_000.0, 57_600.0),
    max_edge_margin_seconds: float = 2.0,
) -> SampleRateInference:
    """Infer a unique complex-int16 rate or fail closed.

    ``max_edge_margin_seconds`` is the total allowed mismatch between the raw
    duration and the API window. It covers start/end roll and packet boundary
    rounding; it must be chosen by the caller and is always recorded.
    """

    if file_size_bytes <= 0 or file_size_bytes % BYTES_PER_COMPLEX_INT16:
        raise ValueError("file_size_bytes must be a positive multiple of 4")
    if not math.isfinite(max_edge_margin_seconds) or max_edge_margin_seconds < 0:
        raise ValueError("max_edge_margin_seconds must be finite and non-negative")
    rates = tuple(float(value) for value in candidate_rates_hz)
    if not rates or any(not math.isfinite(value) or value <= 0 for value in rates):
        raise ValueError("candidate rates must be finite and positive")
    if len(set(rates)) != len(rates):
        raise ValueError("candidate rates must be unique")

    start = _as_utc(observation_start_utc)
    end = _as_utc(observation_end_utc)
    duration = (end - start).total_seconds()
    if duration <= 0:
        raise ValueError("observation end must be after start")
    sample_count = file_size_bytes // BYTES_PER_COMPLEX_INT16
    implied_rate = sample_count / duration
    deltas = tuple((rate, sample_count / rate - duration) for rate in rates)
    plausible = tuple(
        rate for rate, delta in deltas if abs(delta) <= max_edge_margin_seconds
    )
    if len(plausible) == 1:
        return SampleRateInference(
            state="accepted",
            sample_rate_hz=plausible[0],
            implied_full_coverage_rate_hz=implied_rate,
            complex_sample_count=sample_count,
            observation_duration_seconds=duration,
            candidate_duration_deltas_seconds=deltas,
            max_edge_margin_seconds=max_edge_margin_seconds,
            reason="exactly one candidate fits the declared edge margin",
        )
    reason = (
        "no candidate fits the declared edge margin"
        if not plausible
        else "multiple candidates fit the declared edge margin"
    )
    return SampleRateInference(
        state="unresolved",
        sample_rate_hz=None,
        implied_full_coverage_rate_hz=implied_rate,
        complex_sample_count=sample_count,
        observation_duration_seconds=duration,
        candidate_duration_deltas_seconds=deltas,
        max_edge_margin_seconds=max_edge_margin_seconds,
        reason=reason,
    )


def infer_doppler_state(
    *,
    pre_correction_score: float,
    stationary_score: float,
    min_winning_score: float = 8.0,
    min_decision_ratio: float = 5.0,
) -> DopplerStateInference:
    """Select pre/post correction only when one ridge wins decisively."""

    values = (
        pre_correction_score,
        stationary_score,
        min_winning_score,
        min_decision_ratio,
    )
    if any(not math.isfinite(value) or value < 0 for value in values):
        raise ValueError(
            "Doppler scores and thresholds must be finite and non-negative"
        )
    if min_decision_ratio < 1:
        raise ValueError("min_decision_ratio must be at least one")
    if pre_correction_score >= stationary_score:
        state = "pre_correction"
        winning = pre_correction_score
        competing = stationary_score
    else:
        state = "post_correction"
        winning = stationary_score
        competing = pre_correction_score
    ratio = math.inf if competing == 0 and winning > 0 else (
        winning / competing if competing else 1.0
    )
    if winning < min_winning_score or ratio < min_decision_ratio:
        return DopplerStateInference(
            state="unknown",
            winning_score=winning,
            competing_score=competing,
            score_ratio=ratio,
            reason="ridge evidence does not meet both decision thresholds",
        )
    return DopplerStateInference(
        state=state,
        winning_score=winning,
        competing_score=competing,
        score_ratio=ratio,
        reason="winning ridge meets absolute and relative thresholds",
    )


def sha256_file(
    path: str | os.PathLike[str], *, chunk_bytes: int = 1_048_576
) -> str:
    """Hash a file in bounded memory."""

    if chunk_bytes <= 0:
        raise ValueError("chunk_bytes must be positive")
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        while block := source.read(chunk_bytes):
            digest.update(block)
    return digest.hexdigest()


def analyze_iq_amplitudes(
    path: str | os.PathLike[str],
    *,
    byte_order: str = "little",
    component_order: str = "iq",
    stride: int = 97,
) -> IQAmplitudeMetrics:
    """Measure amplitude evidence under an explicit int16 layout hypothesis."""

    if byte_order not in {"little", "big"}:
        raise ValueError("byte_order must be 'little' or 'big'")
    if component_order not in {"iq", "qi"}:
        raise ValueError("component_order must be 'iq' or 'qi'")
    if stride <= 0:
        raise ValueError("stride must be positive")

    np = _require_numpy()
    path_value = Path(path)
    sample_count = raw_complex_sample_count(path_value)
    dtype = "<i2" if byte_order == "little" else ">i2"
    raw = np.memmap(path_value, dtype=dtype, mode="r").reshape(-1, 2)
    sampled = np.asarray(raw[::stride], dtype=np.int32)
    if component_order == "qi":
        sampled = sampled[:, ::-1]

    i_values = sampled[:, 0].astype(np.float64)
    q_values = sampled[:, 1].astype(np.float64)
    squared = i_values * i_values + q_values * q_values
    clipped = (sampled == -32_768) | (sampled == 32_767)
    scale = 32_768.0
    return IQAmplitudeMetrics(
        byte_order=byte_order,
        component_order=component_order,
        file_size_bytes=path_value.stat().st_size,
        complex_sample_count=sample_count,
        sampled_complex_count=int(sampled.shape[0]),
        rms_normalized=float(np.sqrt(np.mean(squared)) / scale),
        endpoint_clip_fraction=float(np.mean(clipped)),
        dc_i_normalized=float(np.mean(i_values) / scale),
        dc_q_normalized=float(np.mean(q_values) / scale),
    )


def spectrogram_window_times(
    complex_sample_count: int,
    *,
    sample_rate_hz: float,
    fft_size: int,
    step_seconds: float,
) -> tuple[float, ...]:
    """Return center times for the exact windows used by ridge scoring."""

    if complex_sample_count < fft_size:
        raise ValueError("recording is shorter than one FFT window")
    if sample_rate_hz <= 0 or step_seconds <= 0:
        raise ValueError("sample rate and step must be positive")
    if fft_size <= 0 or fft_size & (fft_size - 1):
        raise ValueError("fft_size must be a positive power of two")
    step_samples = max(1, round(step_seconds * sample_rate_hz))
    starts = range(0, complex_sample_count - fft_size + 1, step_samples)
    half_window_seconds = fft_size / (2.0 * sample_rate_hz)
    return tuple(start / sample_rate_hz + half_window_seconds for start in starts)


def score_frequency_tracks(
    path: str | os.PathLike[str],
    frequency_tracks_hz: Mapping[str, Sequence[float]],
    *,
    sample_rate_hz: float,
    byte_order: str = "little",
    component_order: str = "iq",
    fft_size: int = 32_768,
    step_seconds: float = 1.0,
    search_half_width_hz: float = 75.0,
) -> tuple[DopplerRidgeScore, ...]:
    """Score candidate frequency tracks against a bounded-memory spectrogram.

    Each sequence must contain one frequency per window returned by
    :func:`spectrogram_window_times`.  Power is normalized by the median power
    of the same FFT window, making scores comparable across changing gain.
    """

    if byte_order not in {"little", "big"}:
        raise ValueError("byte_order must be 'little' or 'big'")
    if component_order not in {"iq", "qi"}:
        raise ValueError("component_order must be 'iq' or 'qi'")
    if not frequency_tracks_hz:
        raise ValueError("at least one frequency track is required")
    if search_half_width_hz <= 0:
        raise ValueError("search_half_width_hz must be positive")

    np = _require_numpy()
    path_value = Path(path)
    sample_count = raw_complex_sample_count(path_value)
    times = spectrogram_window_times(
        sample_count,
        sample_rate_hz=sample_rate_hz,
        fft_size=fft_size,
        step_seconds=step_seconds,
    )
    tracks = {
        name: np.asarray(values, dtype=np.float64)
        for name, values in frequency_tracks_hz.items()
    }
    for name, values in tracks.items():
        if values.shape != (len(times),):
            raise ValueError(
                f"frequency track {name!r} has {values.size} values; "
                f"expected {len(times)}"
            )
        if not np.all(np.isfinite(values)):
            raise ValueError(f"frequency track {name!r} contains non-finite values")
        if np.any(np.abs(values) >= sample_rate_hz / 2.0):
            raise ValueError(f"frequency track {name!r} leaves the Nyquist band")

    dtype = "<i2" if byte_order == "little" else ">i2"
    raw = np.memmap(path_value, dtype=dtype, mode="r").reshape(-1, 2)
    window = np.hanning(fft_size).astype(np.float32)
    frequencies = np.fft.fftshift(
        np.fft.fftfreq(fft_size, d=1.0 / sample_rate_hz)
    )
    bin_width_hz = sample_rate_hz / fft_size
    half_bins = max(1, math.ceil(search_half_width_hz / bin_width_hz))
    ratios: dict[str, list[float]] = {name: [] for name in tracks}
    errors: dict[str, list[float]] = {name: [] for name in tracks}
    step_samples = max(1, round(step_seconds * sample_rate_hz))

    for window_index, start in enumerate(
        range(0, sample_count - fft_size + 1, step_samples)
    ):
        components = np.asarray(raw[start : start + fft_size], dtype=np.float32)
        if component_order == "iq":
            samples = components[:, 0] + 1j * components[:, 1]
        else:
            samples = components[:, 1] + 1j * components[:, 0]
        samples -= np.mean(samples)
        spectrum = np.fft.fftshift(np.fft.fft(samples * window))
        power = np.abs(spectrum) ** 2
        noise = float(np.median(power))
        if not math.isfinite(noise) or noise <= 0:
            raise IQAnalysisError("spectral window has no finite positive noise floor")

        for name, track in tracks.items():
            target = float(track[window_index])
            center = round((target + sample_rate_hz / 2.0) / bin_width_hz)
            center = min(max(center, 0), power.size - 1)
            lower = max(0, center - half_bins)
            upper = min(power.size, center + half_bins + 1)
            local_index = int(np.argmax(power[lower:upper])) + lower
            ratios[name].append(float(power[local_index] / noise))
            errors[name].append(abs(float(frequencies[local_index]) - target))

    results = []
    for name in tracks:
        model_ratios = np.asarray(ratios[name], dtype=np.float64)
        model_errors = np.asarray(errors[name], dtype=np.float64)
        results.append(
            DopplerRidgeScore(
                model=name,
                window_count=int(model_ratios.size),
                median_ridge_to_noise=float(np.median(model_ratios)),
                p90_ridge_to_noise=float(np.quantile(model_ratios, 0.90)),
                fraction_above_ratio_8=float(np.mean(model_ratios >= 8.0)),
                median_peak_error_hz=float(np.median(model_errors)),
            )
        )
    return tuple(results)


def _as_utc(value: str | datetime) -> datetime:
    if isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        parsed = value
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamps must include an explicit UTC offset")
    return parsed.astimezone(timezone.utc)


def _greenwich_sidereal_angle(julian_date: float) -> float:
    centuries = (julian_date - 2_451_545.0) / 36_525.0
    degrees = (
        280.46061837
        + 360.98564736629 * (julian_date - 2_451_545.0)
        + 0.000387933 * centuries * centuries
        - centuries * centuries * centuries / 38_710_000.0
    )
    return math.radians(degrees % 360.0)


def _observer_eci(
    julian_date: float,
    *,
    latitude_deg: float,
    longitude_deg: float,
    altitude_m: float,
) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    latitude = math.radians(latitude_deg)
    longitude = math.radians(longitude_deg)
    eccentricity_squared = WGS84_FLATTENING * (2.0 - WGS84_FLATTENING)
    prime_vertical = WGS84_A_KM / math.sqrt(
        1.0 - eccentricity_squared * math.sin(latitude) ** 2
    )
    radius_xy = (prime_vertical + altitude_m / 1_000.0) * math.cos(latitude)
    radius_z = (
        prime_vertical * (1.0 - eccentricity_squared) + altitude_m / 1_000.0
    ) * math.sin(latitude)
    angle = _greenwich_sidereal_angle(julian_date) + longitude
    position = (
        radius_xy * math.cos(angle),
        radius_xy * math.sin(angle),
        radius_z,
    )
    velocity = (
        -EARTH_ROTATION_RAD_S * position[1],
        EARTH_ROTATION_RAD_S * position[0],
        0.0,
    )
    return position, velocity


def predict_tle_doppler_hz(
    timestamps_utc: Sequence[str | datetime],
    *,
    tle1: str,
    tle2: str,
    station_latitude_deg: float,
    station_longitude_deg: float,
    station_altitude_m: float,
    carrier_frequency_hz: float,
) -> tuple[float, ...]:
    """Predict one-way receive Doppler; positive means an approaching source."""

    if carrier_frequency_hz <= 0 or not math.isfinite(carrier_frequency_hz):
        raise ValueError("carrier_frequency_hz must be finite and positive")
    if not -90.0 <= station_latitude_deg <= 90.0:
        raise ValueError("station latitude is outside [-90, 90]")
    if not -180.0 <= station_longitude_deg <= 180.0:
        raise ValueError("station longitude is outside [-180, 180]")

    Satrec, jday = _require_sgp4()
    satellite = Satrec.twoline2rv(tle1, tle2)
    predicted = []
    for timestamp in timestamps_utc:
        value = _as_utc(timestamp)
        jd, fraction = jday(
            value.year,
            value.month,
            value.day,
            value.hour,
            value.minute,
            value.second + value.microsecond / 1_000_000.0,
        )
        error, satellite_position, satellite_velocity = satellite.sgp4(
            jd, fraction
        )
        if error:
            raise IQAnalysisError(f"SGP4 failed with error code {error}")
        observer_position, observer_velocity = _observer_eci(
            jd + fraction,
            latitude_deg=station_latitude_deg,
            longitude_deg=station_longitude_deg,
            altitude_m=station_altitude_m,
        )
        delta_position = tuple(
            satellite_position[index] - observer_position[index]
            for index in range(3)
        )
        range_km = math.sqrt(sum(value * value for value in delta_position))
        relative_velocity = tuple(
            satellite_velocity[index] - observer_velocity[index]
            for index in range(3)
        )
        range_rate_km_s = sum(
            relative_velocity[index] * delta_position[index]
            for index in range(3)
        ) / range_km
        predicted.append(
            -carrier_frequency_hz * range_rate_km_s / SPEED_OF_LIGHT_KM_S
        )
    return tuple(predicted)


def make_tle_frequency_tracks(
    raw_window_times_seconds: Sequence[float],
    *,
    observation_start_utc: str | datetime,
    raw_start_offsets_seconds: Sequence[float],
    raw_time_scales: Sequence[float] = (1.0,),
    spectral_signs: Sequence[int],
    carrier_offsets_hz: Sequence[float],
    tle1: str,
    tle2: str,
    station_latitude_deg: float,
    station_longitude_deg: float,
    station_altitude_m: float,
    carrier_frequency_hz: float,
) -> dict[str, tuple[float, ...]]:
    """Build auditable pre-Doppler ridge hypotheses for a grid search."""

    start = _as_utc(observation_start_utc)
    tracks: dict[str, tuple[float, ...]] = {}
    for raw_time_scale in raw_time_scales:
        if not math.isfinite(raw_time_scale) or raw_time_scale <= 0:
            raise ValueError("raw_time_scales must be finite and positive")
        for raw_offset in raw_start_offsets_seconds:
            timestamps = tuple(
                datetime.fromtimestamp(
                    start.timestamp()
                    + raw_offset
                    + raw_time_scale * window_time,
                    tz=timezone.utc,
                )
                for window_time in raw_window_times_seconds
            )
            doppler = predict_tle_doppler_hz(
                timestamps,
                tle1=tle1,
                tle2=tle2,
                station_latitude_deg=station_latitude_deg,
                station_longitude_deg=station_longitude_deg,
                station_altitude_m=station_altitude_m,
                carrier_frequency_hz=carrier_frequency_hz,
            )
            for sign in spectral_signs:
                if sign not in {-1, 1}:
                    raise ValueError("spectral_signs must contain only -1 or 1")
                for carrier_offset in carrier_offsets_hz:
                    name = (
                        f"pre_doppler:sign={sign:+d}:raw_start_offset_s="
                        f"{raw_offset:g}:raw_time_scale={raw_time_scale:g}:"
                        f"carrier_offset_hz={carrier_offset:g}"
                    )
                    tracks[name] = tuple(
                        sign * value * raw_time_scale + carrier_offset
                        for value in doppler
                    )
    return tracks


FrequencyTrack = Callable[[float], float]
