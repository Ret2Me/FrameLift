"""Reference-free high-recall selection for expensive phase receivers.

The selector uses only robust per-window signal statistics: mean power and
lag-one phase coherence.  It never inspects frame bytes, decoder output, known
event times, mission callsigns, or protocol fields.  A fixed top-k rescue is
combined with conservative relative thresholds, then temporal padding turns
the selected one-second bins into starts for five-second decoder windows.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import math
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class PhaseWindowSelectorConfig:
    sample_rate_hz: int = 57_600
    analysis_window_seconds: float = 1.0
    decoder_window_seconds: float = 5.0
    top_k: int = 64
    active_power_ratio: float = 20.0
    active_coherence_ratio: float = 2.0
    minimum_phase_coherence: float = 0.60
    padding_seconds: float = 2.0
    baseline_fraction: float = 0.25

    def __post_init__(self) -> None:
        if self.sample_rate_hz <= 0:
            raise ValueError("sample_rate_hz must be positive")
        if self.analysis_window_seconds <= 0 or self.decoder_window_seconds <= 0:
            raise ValueError("window durations must be positive")
        if self.decoder_window_seconds < self.analysis_window_seconds:
            raise ValueError("decoder window cannot be shorter than analysis window")
        if self.top_k < 0:
            raise ValueError("top_k must be non-negative")
        if self.active_power_ratio < 1 or self.active_coherence_ratio < 1:
            raise ValueError("relative activity thresholds must be at least one")
        if not 0 <= self.minimum_phase_coherence <= 1:
            raise ValueError("minimum_phase_coherence must be in [0, 1]")
        if self.padding_seconds < 0:
            raise ValueError("padding_seconds must be non-negative")
        if not 0 < self.baseline_fraction <= 0.5:
            raise ValueError("baseline_fraction must be in (0, 0.5]")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _lower_baseline(values: Any, fraction: float, np: Any) -> float:
    count = max(1, int(math.ceil(len(values) * fraction)))
    baseline = float(np.median(np.partition(values, count - 1)[:count]))
    if not math.isfinite(baseline) or baseline <= 0:
        raise ValueError("signal statistics have no finite positive baseline")
    return baseline


def select_phase_windows_cf32(
    path: str | Path,
    *,
    config: PhaseWindowSelectorConfig | None = None,
) -> dict[str, Any]:
    """Return deterministic decoder starts from a headerless CF32 capture."""

    import numpy as np

    selected = config or PhaseWindowSelectorConfig()
    source = Path(path)
    size = source.stat().st_size
    if size <= 0 or size % np.dtype("<c8").itemsize:
        raise ValueError("CF32 input must contain complete non-empty complex samples")
    samples = np.memmap(source, dtype="<c8", mode="r")
    analysis_samples = int(
        round(selected.sample_rate_hz * selected.analysis_window_seconds)
    )
    window_count = samples.size // analysis_samples
    if window_count < 2:
        raise ValueError("capture must contain at least two analysis windows")

    powers = np.empty(window_count, dtype=np.float64)
    coherences = np.empty(window_count, dtype=np.float64)
    for index in range(window_count):
        start = index * analysis_samples
        values = np.asarray(samples[start : start + analysis_samples])
        powers[index] = float(np.mean(np.abs(values) ** 2))
        products = values[1:] * np.conjugate(values[:-1])
        magnitude_sum = float(np.sum(np.abs(products), dtype=np.float64))
        coherences[index] = (
            float(abs(np.sum(products, dtype=np.complex128))) / magnitude_sum
            if magnitude_sum > 0
            else 0.0
        )

    power_baseline = _lower_baseline(powers, selected.baseline_fraction, np)
    coherence_baseline = _lower_baseline(
        np.maximum(coherences, np.finfo(np.float64).eps),
        selected.baseline_fraction,
        np,
    )
    power_ratios = powers / power_baseline
    coherence_ratios = coherences / coherence_baseline
    coherence_threshold = max(
        selected.minimum_phase_coherence,
        coherence_baseline * selected.active_coherence_ratio,
    )
    active = (power_ratios >= selected.active_power_ratio) | (
        coherences >= coherence_threshold
    )

    # A monotonic reference-free score.  The top-k branch rescues weak bursts
    # even if every relative threshold is missed.
    scores = np.maximum(
        np.log1p(np.maximum(power_ratios - 1.0, 0.0)),
        np.log1p(np.maximum(coherence_ratios - 1.0, 0.0)),
    )
    top_count = min(selected.top_k, window_count)
    top_indices = np.argsort(-scores, kind="stable")[:top_count]
    active[top_indices] = True

    padding_bins = int(
        math.ceil(selected.padding_seconds / selected.analysis_window_seconds)
    )
    padded = active.copy()
    for shift in range(1, padding_bins + 1):
        padded[shift:] |= active[:-shift]
        padded[:-shift] |= active[shift:]

    decoder_duration = samples.size / selected.sample_rate_hz
    maximum_start = max(0.0, decoder_duration - selected.decoder_window_seconds)
    starts = sorted(
        {
            min(
                maximum_start,
                max(
                    0.0,
                    index * selected.analysis_window_seconds
                    - selected.padding_seconds,
                ),
            )
            for index, keep in enumerate(padded)
            if bool(keep)
        }
    )
    rows = [
        {
            "window_index": index,
            "start_seconds": index * selected.analysis_window_seconds,
            "mean_power": float(powers[index]),
            "power_ratio": float(power_ratios[index]),
            "lag1_phase_coherence": float(coherences[index]),
            "coherence_ratio": float(coherence_ratios[index]),
            "score": float(scores[index]),
            "selected_before_padding": bool(active[index]),
            "selected_after_padding": bool(padded[index]),
        }
        for index in range(window_count)
    ]
    return {
        "schema_version": "phase-window-selector-v1",
        "input_path": str(source.resolve()),
        "input_sha256": _sha256_file(source),
        "input_complex_samples": int(samples.size),
        "input_duration_seconds": decoder_duration,
        "reference_paths_available_to_selector": False,
        "reference_hashes_available_to_selector": False,
        "event_timestamps_available_to_selector": False,
        "config": asdict(selected),
        "baselines": {
            "mean_power": power_baseline,
            "lag1_phase_coherence": coherence_baseline,
            "active_coherence_threshold": coherence_threshold,
        },
        "analysis_window_count": window_count,
        "selected_before_padding_count": int(np.count_nonzero(active)),
        "selected_after_padding_count": int(np.count_nonzero(padded)),
        "decoder_window_start_count": len(starts),
        "decoder_window_starts_seconds": starts,
        "windows": rows,
    }
