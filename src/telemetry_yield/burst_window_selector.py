"""Protocol-neutral IQ window selection with a deterministic coverage lane.

Signal ranking alone cannot guarantee that a weak, short transmission is sent
to an expensive decoder: a strong carrier or interferer can occupy every top
rank.  This exploratory selector therefore keeps three independent lanes:

* ``signal`` ranks persistent, non-carrier-like energy;
* ``change`` ranks transitions in protocol-neutral signal statistics;
* ``burst`` ranks time-localized spectral excess after suppressing stationary
  carriers and broadband impulses;
* ``coverage`` tiles the recording without gaps when enabled.

The coverage lane is deliberately simple.  It is the auditable high-recall
safety net; a future learned preprocessor may reduce its workload, but cannot
silently remove the guarantee.  No frame bytes, CRC result, mission metadata,
or known event timestamp is accepted by this module.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import math
from pathlib import Path
from typing import Any


SELECTOR_VERSION = "protocol-neutral-four-lane-v2"


@dataclass(frozen=True, slots=True)
class BurstWindowSelectorConfig:
    sample_rate_hz: int = 57_600
    analysis_window_seconds: float = 0.5
    decoder_window_seconds: float = 5.0
    decoder_window_lead_seconds: float = 0.25
    signal_window_limit: int = 16
    change_window_limit: int = 16
    burst_window_limit: int = 32
    signal_nms_seconds: float = 2.0
    burst_nms_seconds: float = 5.0
    coverage_hop_seconds: float | None = 4.0
    baseline_fraction: float = 0.25
    spectral_fft_size: int = 2048
    persistence_subwindows: int = 8
    burst_fft_size: int = 512
    burst_hop_samples: int = 256
    burst_band_width_bins: tuple[int, ...] = (4, 8, 16, 32, 64, 128)
    maximum_analysis_windows: int = 100_000

    def __post_init__(self) -> None:
        if self.sample_rate_hz <= 0:
            raise ValueError("sample_rate_hz must be positive")
        for name in (
            "analysis_window_seconds",
            "decoder_window_seconds",
        ):
            value = getattr(self, name)
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be finite and positive")
        if self.decoder_window_seconds < self.analysis_window_seconds:
            raise ValueError("decoder window cannot be shorter than analysis window")
        if (
            not math.isfinite(self.decoder_window_lead_seconds)
            or self.decoder_window_lead_seconds < 0
        ):
            raise ValueError("decoder_window_lead_seconds must be finite and non-negative")
        for name in (
            "signal_window_limit",
            "change_window_limit",
            "burst_window_limit",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        if not math.isfinite(self.signal_nms_seconds) or self.signal_nms_seconds < 0:
            raise ValueError("signal_nms_seconds must be finite and non-negative")
        if not math.isfinite(self.burst_nms_seconds) or self.burst_nms_seconds < 0:
            raise ValueError("burst_nms_seconds must be finite and non-negative")
        if self.coverage_hop_seconds is not None:
            if (
                not math.isfinite(self.coverage_hop_seconds)
                or self.coverage_hop_seconds <= 0
                or self.coverage_hop_seconds > self.decoder_window_seconds
            ):
                raise ValueError(
                    "coverage_hop_seconds must be positive and no greater than "
                    "decoder_window_seconds"
                )
        if not 0 < self.baseline_fraction <= 0.5:
            raise ValueError("baseline_fraction must be in (0, 0.5]")
        if (
            self.spectral_fft_size < 16
            or self.spectral_fft_size & (self.spectral_fft_size - 1)
        ):
            raise ValueError("spectral_fft_size must be a power of two of at least 16")
        if self.persistence_subwindows < 2:
            raise ValueError("persistence_subwindows must be at least two")
        if (
            isinstance(self.maximum_analysis_windows, bool)
            or not isinstance(self.maximum_analysis_windows, int)
            or self.maximum_analysis_windows < 2
        ):
            raise ValueError("maximum_analysis_windows must be an integer >= 2")
        if (
            self.burst_fft_size < 16
            or self.burst_fft_size > int(
                round(self.sample_rate_hz * self.analysis_window_seconds)
            )
        ):
            raise ValueError("burst_fft_size must fit inside an analysis window")
        if not 1 <= self.burst_hop_samples <= self.burst_fft_size:
            raise ValueError("burst_hop_samples must be in [1, burst_fft_size]")
        if (
            not self.burst_band_width_bins
            or tuple(sorted(set(self.burst_band_width_bins)))
            != self.burst_band_width_bins
            or self.burst_band_width_bins[0] < 2
            or self.burst_band_width_bins[-1] > self.burst_fft_size // 2
        ):
            raise ValueError(
                "burst_band_width_bins must be unique, ordered, and fit the FFT"
            )


def _lower_baseline(values: Any, fraction: float, np: Any) -> float:
    count = max(1, int(math.ceil(len(values) * fraction)))
    baseline = float(np.median(np.partition(values, count - 1)[:count]))
    if not math.isfinite(baseline) or baseline <= 0:
        raise ValueError("signal statistics have no finite positive baseline")
    return baseline


def _rank_with_nms(
    scores: Any,
    *,
    limit: int,
    analysis_window_seconds: float,
    nms_seconds: float,
    np: Any,
) -> list[int]:
    if limit == 0:
        return []
    selected: list[int] = []
    for raw_index in np.argsort(-scores, kind="stable"):
        index = int(raw_index)
        timestamp = index * analysis_window_seconds
        if any(
            abs(timestamp - kept * analysis_window_seconds) < nms_seconds
            for kept in selected
        ):
            continue
        selected.append(index)
        if len(selected) >= limit:
            break
    return selected


def _coverage_starts(duration: float, config: BurstWindowSelectorConfig) -> list[float]:
    hop = config.coverage_hop_seconds
    if hop is None:
        return []
    maximum_start = max(0.0, duration - config.decoder_window_seconds)
    starts: list[float] = []
    current = 0.0
    while current < maximum_start:
        starts.append(current)
        current += hop
    starts.append(maximum_start)
    return sorted(set(starts))


def _coverage_audit(
    starts: list[float], duration: float, decoder_window_seconds: float
) -> dict[str, Any]:
    if not starts:
        return {
            "enabled": False,
            "entire_capture_covered": False,
            "maximum_uncovered_gap_seconds": None,
        }
    cursor = 0.0
    maximum_gap = 0.0
    for start in sorted(starts):
        maximum_gap = max(maximum_gap, max(0.0, start - cursor))
        cursor = max(cursor, start + decoder_window_seconds)
    maximum_gap = max(maximum_gap, max(0.0, duration - cursor))
    return {
        "enabled": True,
        "entire_capture_covered": maximum_gap <= 1e-9,
        "maximum_uncovered_gap_seconds": maximum_gap,
    }


def select_protocol_neutral_ci16_windows(
    path: str | Path,
    *,
    config: BurstWindowSelectorConfig | None = None,
) -> dict[str, Any]:
    """Select CI16-LE decoder windows without protocol or outcome knowledge."""

    import numpy as np

    selected = config or BurstWindowSelectorConfig()
    source = Path(path)
    status = source.stat()
    if not source.is_file() or source.is_symlink() or status.st_size <= 0:
        raise ValueError("CI16 input must be a non-empty regular non-symlink file")
    if status.st_size % 4:
        raise ValueError("CI16 input must contain complete I/Q pairs")
    analysis_samples = int(
        round(selected.sample_rate_hz * selected.analysis_window_seconds)
    )
    if analysis_samples < selected.spectral_fft_size:
        raise ValueError("spectral_fft_size must fit inside an analysis window")
    if analysis_samples < selected.persistence_subwindows:
        raise ValueError("analysis window is too short for persistence subwindows")

    raw = np.memmap(source, dtype="<i2", mode="r").reshape(-1, 2)
    window_count = raw.shape[0] // analysis_samples
    if window_count < 2:
        raise ValueError("capture must contain at least two analysis windows")
    if window_count > selected.maximum_analysis_windows:
        raise ValueError("capture exceeds the bounded analysis-window limit")

    powers = np.empty(window_count, dtype=np.float64)
    coherences = np.empty(window_count, dtype=np.float64)
    persistence = np.empty(window_count, dtype=np.float64)
    spectral_entropy = np.empty(window_count, dtype=np.float64)
    peak_ratios = np.empty(window_count, dtype=np.float64)
    endpoint_clips = np.empty(window_count, dtype=np.float64)
    burst_scores = np.empty(window_count, dtype=np.float64)
    burst_localized_excess = np.empty(window_count, dtype=np.float64)
    burst_localized_fraction = np.empty(window_count, dtype=np.float64)
    hann = np.hanning(selected.spectral_fft_size).astype(np.float32)
    burst_hann = np.hanning(selected.burst_fft_size).astype(np.float32)
    epsilon = float(np.finfo(np.float64).eps)

    for index in range(window_count):
        start = index * analysis_samples
        components = np.asarray(
            raw[start : start + analysis_samples], dtype=np.float32
        )
        complex_values = components[:, 0] + 1j * components[:, 1]
        amplitudes_squared = (
            components[:, 0] * components[:, 0]
            + components[:, 1] * components[:, 1]
        )
        powers[index] = float(np.mean(amplitudes_squared))
        original = np.asarray(raw[start : start + analysis_samples])
        endpoint_clips[index] = float(
            np.mean(
                (original == np.int16(-32768)) | (original == np.int16(32767))
            )
        )

        products = complex_values[1:] * np.conjugate(complex_values[:-1])
        magnitude_sum = float(np.sum(np.abs(products), dtype=np.float64))
        coherences[index] = (
            float(abs(np.sum(products, dtype=np.complex128))) / magnitude_sum
            if magnitude_sum > 0
            else 0.0
        )

        subwindow_powers = np.asarray(
            [
                np.mean(chunk)
                for chunk in np.array_split(
                    amplitudes_squared, selected.persistence_subwindows
                )
            ],
            dtype=np.float64,
        )
        persistence[index] = float(
            np.quantile(subwindow_powers, 0.25)
            / max(float(np.quantile(subwindow_powers, 0.75)), epsilon)
        )

        middle = (analysis_samples - selected.spectral_fft_size) // 2
        spectrum_input = complex_values[
            middle : middle + selected.spectral_fft_size
        ]
        spectrum_input = spectrum_input - np.mean(spectrum_input)
        spectrum = np.abs(np.fft.fftshift(np.fft.fft(spectrum_input * hann))) ** 2
        total = float(np.sum(spectrum, dtype=np.float64))
        if total <= 0:
            spectral_entropy[index] = 0.0
            peak_ratios[index] = 0.0
        else:
            probabilities = spectrum / total
            nonzero = probabilities > 0
            spectral_entropy[index] = float(
                -np.sum(probabilities[nonzero] * np.log(probabilities[nonzero]))
                / math.log(selected.spectral_fft_size)
            )
            spectral_floor = float(np.median(spectrum))
            peak_ratios[index] = float(
                np.max(spectrum) / max(spectral_floor, epsilon)
            )

        # Remove persistent energy independently in every frequency bin, then
        # remove each time frame's common-mode excess.  The first subtraction
        # suppresses CW; the second suppresses broadband impulses.  A bank of
        # contiguous frequency widths retains short FSK, PSK, chirp, and
        # multicarrier bursts without asserting a protocol or symbol rate.
        burst_frames = np.lib.stride_tricks.sliding_window_view(
            complex_values, selected.burst_fft_size
        )[:: selected.burst_hop_samples]
        burst_spectrum = np.fft.fftshift(
            np.fft.fft(burst_frames * burst_hann, axis=1), axes=1
        )
        burst_log_power = np.log(np.abs(burst_spectrum) ** 2 + 1.0)
        burst_residual = burst_log_power - np.median(
            burst_log_power, axis=0, keepdims=True
        )
        burst_residual -= np.median(burst_residual, axis=1, keepdims=True)
        burst_positive = np.maximum(burst_residual, 0.0)
        burst_frame_total = burst_positive.sum(axis=1, keepdims=True)
        burst_padded = np.pad(burst_positive, ((0, 0), (1, 0)))
        burst_cumulative = np.cumsum(burst_padded, axis=1)
        localized = 0.0
        localized_fraction = 0.0
        for width in selected.burst_band_width_bins:
            band_sums = burst_cumulative[:, width:] - burst_cumulative[:, :-width]
            # Requiring the localized excess to persist across the upper 15%
            # of STFT frames prevents a single broadband sample (which leaks
            # through the taper into only a few adjacent frames) from winning
            # merely because its instantaneous amplitude is large.
            per_frame_localized = np.max(band_sums, axis=1)
            per_frame_fraction = np.max(
                band_sums / (burst_frame_total + 1e-12), axis=1
            )
            localized = max(
                localized,
                float(np.quantile(per_frame_localized, 0.85) / math.sqrt(width)),
            )
            localized_fraction = max(
                localized_fraction,
                float(np.quantile(per_frame_fraction, 0.85)),
            )
        burst_localized_excess[index] = localized
        burst_localized_fraction[index] = localized_fraction
        burst_scores[index] = localized * localized_fraction

    power_baseline = _lower_baseline(
        np.maximum(powers, epsilon), selected.baseline_fraction, np
    )
    power_ratios = powers / power_baseline
    power_strength = np.log1p(np.maximum(power_ratios - 1.0, 0.0))
    # Persistent non-carrier energy is useful for FSK, PSK, QAM and multicarrier
    # bursts.  It deliberately does not assert which of those waveforms exists.
    signal_scores = (
        power_strength
        * np.sqrt(np.maximum(1.0 - coherences, 0.0))
        * (0.25 + spectral_entropy)
        * (0.25 + np.clip(persistence, 0.0, 1.0))
    )

    feature_matrix = np.column_stack(
        (
            np.log1p(np.maximum(power_ratios, 0.0)),
            coherences,
            spectral_entropy,
            np.log1p(np.maximum(peak_ratios, 0.0)),
            persistence,
        )
    )
    feature_scale = np.median(
        np.abs(feature_matrix - np.median(feature_matrix, axis=0)), axis=0
    )
    feature_scale = np.maximum(feature_scale, 1e-6)
    change_scores = np.zeros(window_count, dtype=np.float64)
    differences = np.abs(np.diff(feature_matrix, axis=0)) / feature_scale
    if differences.size:
        transition = np.sqrt(np.sum(differences * differences, axis=1))
        change_scores[:-1] = np.maximum(change_scores[:-1], transition)
        change_scores[1:] = np.maximum(change_scores[1:], transition)

    signal_indices = _rank_with_nms(
        signal_scores,
        limit=selected.signal_window_limit,
        analysis_window_seconds=selected.analysis_window_seconds,
        nms_seconds=selected.signal_nms_seconds,
        np=np,
    )
    change_indices = _rank_with_nms(
        change_scores,
        limit=selected.change_window_limit,
        analysis_window_seconds=selected.analysis_window_seconds,
        nms_seconds=selected.signal_nms_seconds,
        np=np,
    )
    burst_indices = _rank_with_nms(
        burst_scores,
        limit=selected.burst_window_limit,
        analysis_window_seconds=selected.analysis_window_seconds,
        nms_seconds=selected.burst_nms_seconds,
        np=np,
    )
    duration = raw.shape[0] / selected.sample_rate_hz
    maximum_start = max(0.0, duration - selected.decoder_window_seconds)

    candidates: dict[float, dict[str, Any]] = {}

    def add_candidate(index: int, start: float, lane: str) -> None:
        bounded_start = min(maximum_start, max(0.0, start))
        key = round(bounded_start, 12)
        row = candidates.setdefault(
            key,
            {
                "decoder_start_seconds": bounded_start,
                "analysis_index": index,
                "analysis_start_seconds": index
                * selected.analysis_window_seconds,
                "lanes": [],
                "mean_power": float(powers[index]),
                "power_ratio": float(power_ratios[index]),
                "lag1_phase_coherence": float(coherences[index]),
                "within_window_power_persistence": float(persistence[index]),
                "spectral_entropy": float(spectral_entropy[index]),
                "spectral_peak_to_median_ratio": float(peak_ratios[index]),
                "endpoint_clip_fraction": float(endpoint_clips[index]),
                "signal_score": float(signal_scores[index]),
                "change_score": float(change_scores[index]),
                "burst_score": float(burst_scores[index]),
                "burst_localized_log_excess": float(
                    burst_localized_excess[index]
                ),
                "burst_localized_excess_fraction": float(
                    burst_localized_fraction[index]
                ),
            },
        )
        if lane not in row["lanes"]:
            row["lanes"].append(lane)

    for lane, indices in (("signal", signal_indices), ("change", change_indices)):
        for index in indices:
            add_candidate(
                index,
                index * selected.analysis_window_seconds
                - selected.decoder_window_lead_seconds,
                lane,
            )
    for index in burst_indices:
        add_candidate(
            index,
            index * selected.analysis_window_seconds
            - selected.decoder_window_lead_seconds,
            "burst",
        )
    coverage_starts = _coverage_starts(duration, selected)
    for start in coverage_starts:
        center = start + 0.5 * selected.decoder_window_seconds
        index = min(
            window_count - 1,
            max(0, int(center / selected.analysis_window_seconds)),
        )
        add_candidate(index, start, "coverage")

    ordered = sorted(candidates.values(), key=lambda item: item["decoder_start_seconds"])
    for row in ordered:
        row["lanes"].sort()
    return {
        "schema_version": "protocol-neutral-window-selection-v2",
        "selector_version": SELECTOR_VERSION,
        "input_path": str(source.resolve()),
        "input_size_bytes": status.st_size,
        "input_duration_seconds": duration,
        "reference_paths_available_to_selector": False,
        "frame_bytes_available_to_selector": False,
        "event_timestamps_available_to_selector": False,
        "mission_metadata_available_to_selector": False,
        "config": asdict(selected),
        "analysis_window_count": window_count,
        "lane_counts_before_merge": {
            "signal": len(signal_indices),
            "change": len(change_indices),
            "burst": len(burst_indices),
            "coverage": len(coverage_starts),
        },
        "selected_decoder_window_count": len(ordered),
        "coverage_audit": _coverage_audit(
            coverage_starts, duration, selected.decoder_window_seconds
        ),
        "selected_windows": ordered,
    }
