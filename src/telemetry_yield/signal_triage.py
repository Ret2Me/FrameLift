"""Protocol-neutral, bounded-memory triage of headerless CI16-LE IQ files.

The metrics in this module measure energy and spectral structure only.  They do
not identify a waveform, framing protocol, or telemetry.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import math
from pathlib import Path
from typing import Sequence


@dataclass(frozen=True, slots=True)
class SignalTriageConfig:
    sample_rate_hz: float = 57_600.0
    window_seconds: float = 1.0
    fft_size: int = 4096
    fft_slices_per_window: int = 4
    noise_lower_fraction: float = 0.20
    active_power_ratio: float = 1.5
    active_fft_ratio: float = 20.0
    active_fft_baseline_multiplier: float = 1.5

    def __post_init__(self) -> None:
        if not math.isfinite(self.sample_rate_hz) or self.sample_rate_hz <= 0:
            raise ValueError("sample_rate_hz must be finite and positive")
        if not math.isfinite(self.window_seconds) or self.window_seconds <= 0:
            raise ValueError("window_seconds must be finite and positive")
        if self.fft_size <= 0 or self.fft_size & (self.fft_size - 1):
            raise ValueError("fft_size must be a positive power of two")
        if self.fft_slices_per_window <= 0:
            raise ValueError("fft_slices_per_window must be positive")
        if not 0 < self.noise_lower_fraction <= 0.5:
            raise ValueError("noise_lower_fraction must be in (0, 0.5]")
        for name in (
            "active_power_ratio",
            "active_fft_ratio",
            "active_fft_baseline_multiplier",
        ):
            value = getattr(self, name)
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be finite and positive")


@dataclass(frozen=True, slots=True)
class SignalTriageMetrics:
    file_size_bytes: int
    sha256: str
    complex_sample_count: int
    duration_seconds: float
    one_second_window_count: int
    trailing_sample_count: int
    robust_noise_power_normalized: float
    median_power_normalized: float
    p90_power_normalized: float
    median_power_to_noise_ratio: float
    p90_power_to_noise_ratio: float
    max_power_to_noise_ratio: float
    fft_noise_peak_to_median_ratio: float
    median_fft_peak_to_median_ratio: float
    p90_fft_peak_to_median_ratio: float
    max_fft_peak_to_median_ratio: float
    median_peak_frequency_offset_hz: float
    p10_peak_frequency_offset_hz: float
    p90_peak_frequency_offset_hz: float
    median_occupied_bandwidth_hz: float
    p90_occupied_bandwidth_hz: float
    active_window_count: int
    active_window_fraction: float
    longest_active_run_seconds: float
    ranking_score: float

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def _require_numpy():
    try:
        import numpy as np
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise RuntimeError("signal triage requires numpy") from exc
    return np


def _sha256_file(path: Path, chunk_size: int = 4 * 1024 * 1024) -> str:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(chunk_size):
            digest.update(block)
    return digest.hexdigest()


def _lower_envelope(values, fraction: float, np) -> float:
    count = max(1, math.ceil(values.size * fraction))
    return float(np.median(np.partition(values, count - 1)[:count]))


def _peak_bandwidth(power, peak_index: int, floor: float, bin_width_hz: float) -> float:
    """Approximate the peak-connected width above noise+6 dB / peak-6 dB."""

    threshold = max(floor * 4.0, float(power[peak_index]) / 4.0)
    left = peak_index
    gap = 0
    for index in range(peak_index - 1, -1, -1):
        if power[index] >= threshold:
            left = index
            gap = 0
        else:
            gap += 1
            if gap > 2:
                break
    right = peak_index
    gap = 0
    for index in range(peak_index + 1, power.size):
        if power[index] >= threshold:
            right = index
            gap = 0
        else:
            gap += 1
            if gap > 2:
                break
    return (right - left + 1) * bin_width_hz


def _longest_true_run(values: Sequence[bool]) -> int:
    longest = 0
    current = 0
    for value in values:
        current = current + 1 if value else 0
        longest = max(longest, current)
    return longest


def triage_ci16le_file(
    path: str | Path,
    *,
    config: SignalTriageConfig | None = None,
) -> SignalTriageMetrics:
    """Measure robust 1 s power and short-time spectral structure read-only."""

    np = _require_numpy()
    selected = config or SignalTriageConfig()
    path_value = Path(path)
    size = path_value.stat().st_size
    if size <= 0 or size % 4:
        raise ValueError("CI16-LE IQ file size must be a positive multiple of four")
    sample_count = size // 4
    window_samples = round(selected.sample_rate_hz * selected.window_seconds)
    if window_samples <= 0 or selected.fft_size > window_samples:
        raise ValueError("analysis window must contain at least one FFT")
    window_count = sample_count // window_samples
    if window_count == 0:
        raise ValueError("IQ file is shorter than one complete analysis window")

    raw = np.memmap(path_value, dtype="<i2", mode="r").reshape(-1, 2)
    hann = np.hanning(selected.fft_size).astype(np.float32)
    frequencies = np.fft.fftshift(
        np.fft.fftfreq(selected.fft_size, d=1.0 / selected.sample_rate_hz)
    )
    bin_width = selected.sample_rate_hz / selected.fft_size
    max_fft_start = window_samples - selected.fft_size
    fft_starts = np.linspace(
        0, max_fft_start, num=selected.fft_slices_per_window, dtype=np.int64
    )

    powers = np.empty(window_count, dtype=np.float64)
    peak_ratios = np.empty(window_count, dtype=np.float64)
    peak_frequencies = np.empty(window_count, dtype=np.float64)
    bandwidths = np.empty(window_count, dtype=np.float64)
    scale_squared = 32768.0 * 32768.0

    for window_index in range(window_count):
        start = window_index * window_samples
        components = np.asarray(raw[start : start + window_samples], dtype=np.float32)
        powers[window_index] = float(
            np.mean(
                components[:, 0] * components[:, 0]
                + components[:, 1] * components[:, 1]
            )
            / scale_squared
        )
        best_ratio = -math.inf
        best_frequency = 0.0
        best_bandwidth = bin_width
        for fft_offset in fft_starts:
            segment = components[
                int(fft_offset) : int(fft_offset) + selected.fft_size
            ]
            samples = segment[:, 0] + 1j * segment[:, 1]
            samples -= np.mean(samples)
            spectrum = np.fft.fftshift(np.fft.fft(samples * hann))
            spectrum_power = np.abs(spectrum) ** 2
            floor = float(np.median(spectrum_power))
            if not math.isfinite(floor) or floor <= 0:
                ratio = 0.0
                peak_index = 0
                bandwidth = 0.0
            else:
                peak_index = int(np.argmax(spectrum_power))
                ratio = float(spectrum_power[peak_index] / floor)
                bandwidth = _peak_bandwidth(
                    spectrum_power, peak_index, floor, bin_width
                )
            if ratio > best_ratio:
                best_ratio = ratio
                best_frequency = float(frequencies[peak_index])
                best_bandwidth = bandwidth
        peak_ratios[window_index] = best_ratio
        peak_frequencies[window_index] = best_frequency
        bandwidths[window_index] = best_bandwidth

    noise_power = _lower_envelope(powers, selected.noise_lower_fraction, np)
    if noise_power <= 0 or not math.isfinite(noise_power):
        raise ValueError("IQ windows have no finite positive power floor")
    power_ratios = powers / noise_power
    fft_noise_ratio = _lower_envelope(
        peak_ratios, selected.noise_lower_fraction, np
    )
    fft_active_threshold = max(
        selected.active_fft_ratio,
        fft_noise_ratio * selected.active_fft_baseline_multiplier,
    )
    activity = (power_ratios >= selected.active_power_ratio) | (
        peak_ratios >= fft_active_threshold
    )
    active_count = int(np.count_nonzero(activity))
    active_fraction = active_count / window_count

    p90_power_ratio = float(np.quantile(power_ratios, 0.90))
    p90_fft_ratio = float(np.quantile(peak_ratios, 0.90))
    max_fft_ratio = float(np.max(peak_ratios))
    # Fixed, monotonic and intentionally uncalibrated.  The max term rescues a
    # short isolated burst that does not reach the 90th percentile.
    ranking_score = (
        2.0 * math.sqrt(active_fraction)
        + math.log1p(max(0.0, p90_power_ratio - 1.0))
        + 0.75 * math.log1p(max(0.0, p90_fft_ratio - 1.0))
        + 0.10 * math.log1p(max(0.0, max_fft_ratio - 1.0))
    )

    return SignalTriageMetrics(
        file_size_bytes=size,
        sha256=_sha256_file(path_value),
        complex_sample_count=sample_count,
        duration_seconds=sample_count / selected.sample_rate_hz,
        one_second_window_count=window_count,
        trailing_sample_count=sample_count - window_count * window_samples,
        robust_noise_power_normalized=noise_power,
        median_power_normalized=float(np.median(powers)),
        p90_power_normalized=float(np.quantile(powers, 0.90)),
        median_power_to_noise_ratio=float(np.median(power_ratios)),
        p90_power_to_noise_ratio=p90_power_ratio,
        max_power_to_noise_ratio=float(np.max(power_ratios)),
        fft_noise_peak_to_median_ratio=fft_noise_ratio,
        median_fft_peak_to_median_ratio=float(np.median(peak_ratios)),
        p90_fft_peak_to_median_ratio=p90_fft_ratio,
        max_fft_peak_to_median_ratio=max_fft_ratio,
        median_peak_frequency_offset_hz=float(np.median(peak_frequencies)),
        p10_peak_frequency_offset_hz=float(np.quantile(peak_frequencies, 0.10)),
        p90_peak_frequency_offset_hz=float(np.quantile(peak_frequencies, 0.90)),
        median_occupied_bandwidth_hz=float(np.median(bandwidths)),
        p90_occupied_bandwidth_hz=float(np.quantile(bandwidths, 0.90)),
        active_window_count=active_count,
        active_window_fraction=active_fraction,
        longest_active_run_seconds=(
            _longest_true_run([bool(value) for value in activity])
            * selected.window_seconds
        ),
        ranking_score=ranking_score,
    )


def rank_signal_triage(
    records: Sequence[tuple[int, SignalTriageMetrics]],
) -> tuple[tuple[int, SignalTriageMetrics], ...]:
    """Return a deterministic descending high-recall ranking."""

    ids = [item[0] for item in records]
    if any(not isinstance(value, int) or isinstance(value, bool) for value in ids):
        raise ValueError("observation IDs must be integers")
    if len(ids) != len(set(ids)):
        raise ValueError("observation IDs must be unique")
    return tuple(
        sorted(
            records,
            key=lambda item: (
                -item[1].ranking_score,
                -item[1].active_window_fraction,
                -item[1].max_fft_peak_to_median_ratio,
                item[0],
            ),
        )
    )
