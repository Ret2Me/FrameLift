"""Protocol-neutral waveform routing from strong CI16-LE signal windows.

Outputs are scheduling hypotheses, not modulation identifications.  No frame
or telemetry inference is performed here.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import math
from pathlib import Path


@dataclass(frozen=True, slots=True)
class WaveformRoutingConfig:
    sample_rate_hz: float = 57_600.0
    window_seconds: float = 1.0
    strongest_window_count: int = 8
    psd_fft_size: int = 32_768
    symbol_rate_bank_baud: tuple[int, ...] = (
        50,
        100,
        200,
        300,
        400,
        600,
        1200,
        2400,
        4800,
        9600,
        19200,
    )

    def __post_init__(self) -> None:
        if not math.isfinite(self.sample_rate_hz) or self.sample_rate_hz <= 0:
            raise ValueError("sample_rate_hz must be finite and positive")
        if not math.isfinite(self.window_seconds) or self.window_seconds <= 0:
            raise ValueError("window_seconds must be finite and positive")
        if self.strongest_window_count <= 0:
            raise ValueError("strongest_window_count must be positive")
        if self.psd_fft_size <= 0 or self.psd_fft_size & (self.psd_fft_size - 1):
            raise ValueError("psd_fft_size must be a positive power of two")
        if not self.symbol_rate_bank_baud or any(
            not isinstance(rate, int)
            or isinstance(rate, bool)
            or not 50 <= rate <= 19_200
            for rate in self.symbol_rate_bank_baud
        ):
            raise ValueError("symbol-rate bank must contain integers in 50..19200")
        if len(set(self.symbol_rate_bank_baud)) != len(self.symbol_rate_bank_baud):
            raise ValueError("symbol-rate bank values must be unique")


@dataclass(frozen=True, slots=True)
class RoutingHypothesis:
    signal_type: str
    confidence: float
    evidence: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class SymbolRateCandidate:
    baud: int
    clock_line_snr_db: float
    phase_transition_line_snr_db: float
    envelope_transition_line_snr_db: float
    phase_transition_autocorrelation: float

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class WaveformRoutingResult:
    strongest_window_start_seconds: tuple[float, ...]
    envelope_coefficient_of_variation: float
    envelope_robust_variation: float
    power_p99_to_mean_papr: float
    phase_increment_circular_concentration: float
    phase_increment_robust_sigma_rad: float
    phase_increment_near_center_fraction: float
    phase_increment_two_cluster_improvement: float
    spectral_peak_to_median_ratio: float
    occupied_bandwidth_99_hz: float
    peak_connected_bandwidth_hz: float
    dominant_connected_excess_fraction: float
    symbol_rate_candidates: tuple[SymbolRateCandidate, ...]
    hypotheses: tuple[RoutingHypothesis, ...]
    primary_routing: str
    primary_confidence: float
    g3ruh_9600_probe_physically_sensible: bool
    g3ruh_9600_evidence: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        value = asdict(self)
        return value


def _require_numpy():
    try:
        import numpy as np
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise RuntimeError("waveform routing requires numpy") from exc
    return np


def _clamp(value: float) -> float:
    return min(1.0, max(0.0, float(value)))


def _two_cluster_improvement(values, np) -> float:
    centered = np.asarray(values, dtype=np.float64)
    total = float(np.sum((centered - np.mean(centered)) ** 2))
    if total <= 1e-18:
        return 0.0
    centers = np.quantile(centered, [0.25, 0.75])
    for _ in range(16):
        distance = np.abs(centered[:, None] - centers[None, :])
        labels = np.argmin(distance, axis=1)
        updated = np.asarray(
            [
                np.mean(centered[labels == index])
                if np.any(labels == index)
                else centers[index]
                for index in range(2)
            ]
        )
        if np.allclose(updated, centers):
            break
        centers = updated
    residual = float(np.sum((centered - centers[labels]) ** 2))
    return _clamp(1.0 - residual / total)


def _connected_bounds(power, peak: int, threshold: float) -> tuple[int, int]:
    left = peak
    misses = 0
    for index in range(peak - 1, -1, -1):
        if power[index] >= threshold:
            left = index
            misses = 0
        else:
            misses += 1
            if misses > 4:
                break
    right = peak
    misses = 0
    for index in range(peak + 1, power.size):
        if power[index] >= threshold:
            right = index
            misses = 0
        else:
            misses += 1
            if misses > 4:
                break
    return left, right


def _connected_bandwidth(power, peak: int, threshold: float, bin_width: float) -> float:
    left, right = _connected_bounds(power, peak, threshold)
    return (right - left + 1) * bin_width


def _occupied_bandwidth(power, frequencies, floor: float, np) -> float:
    excess = np.maximum(power - 2.0 * floor, 0.0)
    total = float(np.sum(excess))
    if total <= 0:
        return 0.0
    cumulative = np.cumsum(excess) / total
    lower = int(np.searchsorted(cumulative, 0.005))
    upper = int(np.searchsorted(cumulative, 0.995))
    lower = min(max(lower, 0), power.size - 1)
    upper = min(max(upper, lower), power.size - 1)
    return float(frequencies[upper] - frequencies[lower])


def _line_snr_db(feature_windows, sample_rate: float, rate: int, np) -> float:
    spectra = []
    for feature in feature_windows:
        values = np.asarray(feature, dtype=np.float64)
        values -= np.mean(values)
        spectrum = np.abs(np.fft.rfft(values * np.hanning(values.size))) ** 2
        spectra.append(spectrum)
    power = np.mean(spectra, axis=0)
    frequencies = np.fft.rfftfreq(feature_windows[0].size, d=1.0 / sample_rate)
    target = int(np.argmin(np.abs(frequencies - rate)))
    half_width = max(12, int(round(0.02 * rate)))
    lower = max(1, target - half_width)
    upper = min(power.size, target + half_width + 1)
    local = power[lower:upper]
    if local.size < 5:
        return 0.0
    target_lower = max(lower, target - 2)
    target_upper = min(upper, target + 3)
    signal = float(np.max(power[target_lower:target_upper]))
    mask = np.ones(local.size, dtype=bool)
    mask[target_lower - lower : target_upper - lower] = False
    noise = float(np.median(local[mask])) if np.any(mask) else 0.0
    if signal <= 0 or noise <= 0:
        return 0.0
    return 10.0 * math.log10(signal / noise)


def _routing_hypotheses(
    *,
    envelope_cv: float,
    papr: float,
    phase_concentration: float,
    near_center: float,
    cluster_improvement: float,
    occupied_bw: float,
    connected_bw: float,
    connected_fraction: float,
    best_clock_db: float,
) -> tuple[RoutingHypothesis, ...]:
    constant_envelope = _clamp((0.45 - envelope_cv) / 0.40)
    narrow = max(
        _clamp((1200.0 - occupied_bw) / 1100.0),
        _clamp((500.0 - connected_bw) / 450.0)
        * _clamp((connected_fraction - 0.30) / 0.60),
    )
    wide = _clamp((occupied_bw - 600.0) / 5000.0)
    clock = _clamp((best_clock_db - 4.0) / 12.0)
    cw_score = (
        (0.50 + 0.50 * constant_envelope)
        * narrow
        * _clamp((phase_concentration - 0.85) / 0.15)
    )
    fsk_score = constant_envelope * wide * (1.0 - narrow) * (
        0.55 * cluster_improvement + 0.45 * clock
    )
    psk_score = constant_envelope * wide * (1.0 - narrow) * (
        0.60 * _clamp((near_center - 0.50) / 0.45) + 0.40 * clock
    )
    analog_score = (
        0.70
        * wide
        * (1.0 - narrow)
        * _clamp((envelope_cv - 0.12) / 0.45)
        * (1.0 - 0.70 * clock)
    )
    unknown_score = max(
        0.20,
        0.55 * (1.0 - max(cw_score, fsk_score, psk_score, analog_score)),
    )
    evidence_common = (
        f"envelope_cv={envelope_cv:.4g}",
        f"p99_power_to_mean={papr:.4g}",
        f"occupied_bandwidth_99_hz={occupied_bw:.4g}",
        f"peak_connected_bandwidth_hz={connected_bw:.4g}",
        f"dominant_connected_excess_fraction={connected_fraction:.4g}",
        f"phase_increment_concentration={phase_concentration:.4g}",
        f"best_clock_line_snr_db={best_clock_db:.4g}",
    )
    values = (
        ("continuous_carrier_or_cw_like", cw_score),
        ("fsk_like", fsk_score),
        ("psk_like", psk_score),
        ("analog_like", analog_score),
        ("unknown", unknown_score),
    )
    return tuple(
        RoutingHypothesis(name, _clamp(score), evidence_common)
        for name, score in sorted(values, key=lambda item: (-item[1], item[0]))
    )


def route_ci16le_waveform(
    path: str | Path,
    *,
    config: WaveformRoutingConfig | None = None,
) -> WaveformRoutingResult:
    """Analyze strongest 1 s windows and return conservative route hypotheses."""

    np = _require_numpy()
    selected = config or WaveformRoutingConfig()
    path_value = Path(path)
    size = path_value.stat().st_size
    if size <= 0 or size % 4:
        raise ValueError("CI16-LE IQ size must be a positive multiple of four")
    window_samples = round(selected.sample_rate_hz * selected.window_seconds)
    if selected.psd_fft_size > window_samples:
        raise ValueError("psd_fft_size must fit inside one routing window")
    raw = np.memmap(path_value, dtype="<i2", mode="r").reshape(-1, 2)
    window_count = raw.shape[0] // window_samples
    if window_count == 0:
        raise ValueError("IQ file is shorter than one routing window")

    powers = np.empty(window_count, dtype=np.float64)
    peak_ratios = np.empty(window_count, dtype=np.float64)
    preview_hann = np.hanning(4096).astype(np.float32)
    for index in range(window_count):
        start = index * window_samples
        components = np.asarray(raw[start : start + window_samples : 4], dtype=np.float32)
        powers[index] = float(
            np.mean(components[:, 0] ** 2 + components[:, 1] ** 2)
        )
        preview = components[:4096]
        values = preview[:, 0] + 1j * preview[:, 1]
        values -= np.mean(values)
        spectrum = np.abs(np.fft.fft(values * preview_hann)) ** 2
        floor = float(np.median(spectrum))
        peak_ratios[index] = float(np.max(spectrum) / floor) if floor > 0 else 0.0
    power_floor = float(np.quantile(powers, 0.20))
    peak_floor = float(np.quantile(peak_ratios, 0.20))
    score = np.log1p(powers / max(power_floor, 1e-12)) + np.log1p(
        peak_ratios / max(peak_floor, 1e-12)
    )
    chosen = np.argsort(-score, kind="stable")[: min(selected.strongest_window_count, window_count)]
    chosen = np.sort(chosen)

    raw_spectra = []
    aligned_psd_values = []
    spectral_peak_ratios = []
    psd_hann = np.hanning(selected.psd_fft_size).astype(np.float32)
    frequencies = np.fft.fftshift(
        np.fft.fftfreq(selected.psd_fft_size, d=1.0 / selected.sample_rate_hz)
    )
    spectrum_center = selected.psd_fft_size // 2
    for index in chosen:
        start = int(index) * window_samples
        components = np.asarray(raw[start : start + window_samples], dtype=np.float32)
        samples = components[:, 0] + 1j * components[:, 1]
        samples -= np.mean(samples)
        middle = (window_samples - selected.psd_fft_size) // 2
        segment = samples[middle : middle + selected.psd_fft_size]
        raw_spectrum = np.fft.fftshift(np.fft.fft(segment))
        psd_spectrum = np.fft.fftshift(np.fft.fft(segment * psd_hann))
        spectrum_power = np.abs(psd_spectrum) ** 2
        floor = float(np.median(spectrum_power))
        peak_index = int(np.argmax(spectrum_power))
        peak = float(spectrum_power[peak_index])
        shift = spectrum_center - peak_index
        raw_spectra.append(np.roll(raw_spectrum, shift))
        aligned_psd_values.append(np.roll(spectrum_power, shift))
        spectral_peak_ratios.append(peak / max(floor, 1e-20))

    average_psd = np.mean(aligned_psd_values, axis=0)
    spectral_floor = float(np.median(average_psd))
    peak_index = int(np.argmax(average_psd))
    peak = float(average_psd[peak_index])
    significant_excess = np.maximum(average_psd - 4.0 * spectral_floor, 0.0)
    if float(np.sum(significant_excess)) > 0:
        cumulative = np.cumsum(significant_excess) / float(np.sum(significant_excess))
        lower = int(np.searchsorted(cumulative, 0.005))
        upper = int(np.searchsorted(cumulative, 0.995))
        occupied_bw = (upper - lower + 1) * (
            selected.sample_rate_hz / selected.psd_fft_size
        )
    else:
        occupied_bw = 0.0
    connected_threshold = max(spectral_floor * 4.0, peak / 100.0)
    connected_left, connected_right = _connected_bounds(
        average_psd, peak_index, connected_threshold
    )
    connected_bw = (connected_right - connected_left + 1) * (
        selected.sample_rate_hz / selected.psd_fft_size
    )
    significant_total = float(np.sum(significant_excess))
    connected_fraction = (
        float(np.sum(significant_excess[connected_left : connected_right + 1]))
        / significant_total
        if significant_total > 0
        else 0.0
    )

    # Isolate the aligned dominant band before measuring envelope and phase.
    # A 250 Hz floor prevents a one-bin filter from turning arbitrary noise
    # into a perfectly constant synthetic carrier; wide routes retain their
    # sidebands according to the significant occupied span.
    narrow_dominant = bool(
        connected_bw <= 500.0
        and float(np.median(spectral_peak_ratios)) >= 20.0
        and connected_fraction >= 0.50
    )
    half_band_hz = (
        250.0
        if narrow_dominant
        else min(12_000.0, max(250.0, occupied_bw * 1.10, connected_bw * 2.0))
    )
    half_bins = max(
        1,
        math.ceil(
            half_band_hz / (selected.sample_rate_hz / selected.psd_fft_size)
        ),
    )
    band_left = max(0, spectrum_center - half_bins)
    band_right = min(selected.psd_fft_size, spectrum_center + half_bins + 1)
    mask = np.zeros(selected.psd_fft_size, dtype=np.float32)
    mask[band_left:band_right] = 1.0
    sample_windows = []
    envelope_windows = []
    phase_transition_windows = []
    envelope_transition_windows = []
    for spectrum in raw_spectra:
        filtered = np.fft.ifft(np.fft.ifftshift(spectrum * mask))
        # Discard short circular-filter edge regions from feature statistics.
        trim = min(512, filtered.size // 16)
        filtered = filtered[trim:-trim] if trim else filtered
        sample_windows.append(filtered)
        envelope = np.abs(filtered)
        envelope_windows.append(envelope)
        phase_increment = np.angle(filtered[1:] * np.conjugate(filtered[:-1]))
        center = float(np.angle(np.mean(np.exp(1j * phase_increment))))
        residual = np.angle(np.exp(1j * (phase_increment - center)))
        phase_transition_windows.append(np.abs(np.diff(residual)))
        envelope_transition_windows.append(np.abs(np.diff(envelope)))

    all_envelope = np.concatenate(envelope_windows).astype(np.float64)
    mean_envelope = float(np.mean(all_envelope))
    envelope_cv = float(np.std(all_envelope) / max(mean_envelope, 1e-12))
    envelope_robust = float(
        (np.quantile(all_envelope, 0.90) - np.quantile(all_envelope, 0.10))
        / max(float(np.median(all_envelope)), 1e-12)
    )
    all_power = all_envelope * all_envelope
    papr = float(np.quantile(all_power, 0.99) / max(float(np.mean(all_power)), 1e-12))

    phase_increments = []
    for samples in sample_windows:
        values = np.angle(samples[1:] * np.conjugate(samples[:-1]))
        center = float(np.angle(np.mean(np.exp(1j * values))))
        phase_increments.append(np.angle(np.exp(1j * (values - center))))
    phase = np.concatenate(phase_increments).astype(np.float64)
    concentration = float(abs(np.mean(np.exp(1j * phase))))
    phase_sigma = float(1.4826 * np.median(np.abs(phase - np.median(phase))))
    near_center = float(np.mean(np.abs(phase) <= max(0.03, phase_sigma * 0.5)))
    cluster_improvement = _two_cluster_improvement(phase[:: max(1, phase.size // 200_000)], np)

    peak_ratio = float(np.median(spectral_peak_ratios))

    rate_candidates = []
    for rate in selected.symbol_rate_bank_baud:
        phase_db = _line_snr_db(
            phase_transition_windows, selected.sample_rate_hz, rate, np
        )
        envelope_db = _line_snr_db(
            envelope_transition_windows, selected.sample_rate_hz, rate, np
        )
        lag = max(1, round(selected.sample_rate_hz / rate))
        correlations = []
        for values in phase_transition_windows:
            centered_values = values - np.mean(values)
            denominator = float(np.dot(centered_values, centered_values))
            correlations.append(
                float(np.dot(centered_values[:-lag], centered_values[lag:]) / denominator)
                if denominator > 0 and lag < centered_values.size
                else 0.0
            )
        rate_candidates.append(
            SymbolRateCandidate(
                baud=rate,
                clock_line_snr_db=max(phase_db, envelope_db),
                phase_transition_line_snr_db=phase_db,
                envelope_transition_line_snr_db=envelope_db,
                phase_transition_autocorrelation=float(np.median(correlations)),
            )
        )
    rate_candidates.sort(
        key=lambda item: (-item.clock_line_snr_db, item.baud)
    )
    best_clock = rate_candidates[0].clock_line_snr_db
    hypotheses = _routing_hypotheses(
        envelope_cv=envelope_cv,
        papr=papr,
        phase_concentration=concentration,
        near_center=near_center,
        cluster_improvement=cluster_improvement,
        occupied_bw=occupied_bw,
        connected_bw=connected_bw,
        connected_fraction=connected_fraction,
        best_clock_db=best_clock,
    )
    primary = hypotheses[0]
    rates = {item.baud: item for item in rate_candidates}
    rate_9600 = rates[9600]
    fsk_confidence = next(
        item.confidence for item in hypotheses if item.signal_type == "fsk_like"
    )
    sensible = bool(
        fsk_confidence >= 0.35
        and 4_000.0 <= occupied_bw <= 28_000.0
        and (
            rate_9600.clock_line_snr_db >= 2.5
            or abs(rate_9600.phase_transition_autocorrelation) >= 0.15
        )
        and primary.signal_type != "continuous_carrier_or_cw_like"
    )
    g3ruh_evidence = (
        f"fsk_like_confidence={fsk_confidence:.4g}",
        f"occupied_bandwidth_99_hz={occupied_bw:.4g}",
        f"9600_clock_line_snr_db={rate_9600.clock_line_snr_db:.4g}",
        "decision requires FSK-like>=0.35, BW 4..28 kHz, 9600 line>=2.5 dB or |autocorr|>=0.15, non-CW primary",
    )
    return WaveformRoutingResult(
        strongest_window_start_seconds=tuple(
            float(index * selected.window_seconds) for index in chosen
        ),
        envelope_coefficient_of_variation=envelope_cv,
        envelope_robust_variation=envelope_robust,
        power_p99_to_mean_papr=papr,
        phase_increment_circular_concentration=concentration,
        phase_increment_robust_sigma_rad=phase_sigma,
        phase_increment_near_center_fraction=near_center,
        phase_increment_two_cluster_improvement=cluster_improvement,
        spectral_peak_to_median_ratio=peak_ratio,
        occupied_bandwidth_99_hz=occupied_bw,
        peak_connected_bandwidth_hz=connected_bw,
        dominant_connected_excess_fraction=connected_fraction,
        symbol_rate_candidates=tuple(rate_candidates),
        hypotheses=hypotheses,
        primary_routing=primary.signal_type,
        primary_confidence=primary.confidence,
        g3ruh_9600_probe_physically_sensible=sensible,
        g3ruh_9600_evidence=g3ruh_evidence,
    )
