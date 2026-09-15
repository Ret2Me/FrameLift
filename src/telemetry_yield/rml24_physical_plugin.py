"""First real, truth-isolated RML24 physical-layer BER plugin.

The dataset modulation/rate labels route an already-selected supported profile.
Transmitted bits are not visible to carrier recovery, timing recovery or slicing;
they enter only the explicit final ambiguity/alignment scorer.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass
from functools import lru_cache
from typing import Any

from .rml24_benchmark import Rml24Batch


SUPPORTED_MODULATIONS = frozenset({"BPSK", "QPSK", "OQPSK", "GMSK", "2FSK", "FSK"})


def _require_numpy() -> Any:
    try:
        import numpy as np
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("RML24 physical demodulation requires numpy") from exc
    return np


def _scalar(value: object) -> object:
    return value.item() if hasattr(value, "item") else value


def _read_only(values: Any) -> Any:
    """Protect cached NumPy constants from accidental in-place modification."""

    values.flags.writeable = False
    return values


@lru_cache(maxsize=16)
def _sample_axis(length: int) -> Any:
    np = _require_numpy()
    return _read_only(np.arange(length, dtype=np.float64))


@lru_cache(maxsize=32)
def _boxcar(window: int) -> Any:
    np = _require_numpy()
    return _read_only(np.ones(window, dtype=np.float64) / window)


@lru_cache(maxsize=16)
def _hanning(length: int) -> Any:
    np = _require_numpy()
    return _read_only(np.hanning(length))


@lru_cache(maxsize=8)
def _fft_frequencies(fft_size: int, sample_rate_hz: float) -> Any:
    np = _require_numpy()
    return _read_only(np.fft.fftfreq(fft_size, d=1.0 / sample_rate_hz))


@lru_cache(maxsize=64)
def _timing_grid(length: int, samples_per_symbol: float) -> tuple[tuple[float, Any], ...]:
    """Return immutable interpolation positions shared by equal RML profiles."""

    np = _require_numpy()
    phase_count = max(8, int(np.ceil(samples_per_symbol * 4)))
    output: list[tuple[float, Any]] = []
    for phase in np.linspace(0.0, samples_per_symbol, phase_count, endpoint=False):
        positions = phase + np.arange(
            int(np.floor((length - 1 - phase) / samples_per_symbol)) + 1,
            dtype=np.float64,
        ) * samples_per_symbol
        output.append((float(phase), _read_only(positions)))
    return tuple(output)


@lru_cache(maxsize=32)
def _oqpsk_timing_grid(
    length: int, samples_per_symbol: float
) -> tuple[tuple[float, Any, Any], ...]:
    """Return immutable early/late OQPSK positions for one record profile."""

    np = _require_numpy()
    phase_count = max(8, int(np.ceil(samples_per_symbol * 4)))
    half = 0.5 * samples_per_symbol
    output: list[tuple[float, Any, Any]] = []
    for phase in np.linspace(0.0, samples_per_symbol, phase_count, endpoint=False):
        early = phase + np.arange(
            int(np.floor((length - 1 - phase - half) / samples_per_symbol)) + 1,
            dtype=np.float64,
        ) * samples_per_symbol
        output.append(
            (float(phase), _read_only(early), _read_only(early + half))
        )
    return tuple(output)


def _complex_record(iq: Any) -> Any:
    np = _require_numpy()
    array = np.asarray(iq)
    if array.ndim != 2:
        raise ValueError("one IQ record must be a two-dimensional array")
    if array.shape[0] == 2:
        output = array[0].astype(np.float64) + 1j * array[1].astype(np.float64)
    elif array.shape[1] == 2:
        output = array[:, 0].astype(np.float64) + 1j * array[:, 1].astype(np.float64)
    else:
        raise ValueError("IQ record must use shape (2,N) or (N,2)")
    output = output - np.mean(output)
    power = float(np.mean(np.abs(output) ** 2))
    if not np.isfinite(power) or power <= 1e-15:
        raise ValueError("IQ record has no finite signal power")
    return output / np.sqrt(power)


def _interpolate(values: Any, positions: Any) -> Any:
    np = _require_numpy()
    return np.interp(positions, _sample_axis(len(values)), values)


def _timing_slice(soft_trace: Any, samples_per_symbol: float) -> tuple[Any, float, float]:
    """Choose a fractional symbol phase by an unsupervised eye-opening score."""

    np = _require_numpy()
    if not np.isfinite(samples_per_symbol) or samples_per_symbol < 1.5:
        raise ValueError("samples_per_symbol must be at least 1.5")
    best_values = None
    best_phase = 0.0
    best_score = -np.inf
    for phase, positions in _timing_grid(len(soft_trace), samples_per_symbol):
        if len(positions) < 4:
            continue
        values = _interpolate(soft_trace, positions)
        scale = float(np.sqrt(np.mean(values**2))) + 1e-12
        score = float(np.mean(np.abs(values)) / scale)
        if score > best_score:
            best_values = values
            best_phase = float(phase)
            best_score = score
    if best_values is None:
        raise ValueError("timing search yielded no symbols")
    return best_values, best_phase, best_score


def demodulate_bpsk_unaligned(
    iq: Any,
    *,
    sample_rate_hz: float,
    symbol_rate_hz: float,
) -> tuple[Any, dict[str, float]]:
    """BPSK with square-law CFO/phase recovery and bounded timing search."""

    np = _require_numpy()
    signal = _complex_record(iq)
    squared = signal**2
    sample_index = _sample_axis(len(signal))
    # Unwrapped square-law phase removes BPSK data.  A global weighted slope is
    # less biased by noisy one-sample phase differences over a short 2048-sample
    # RML24 record.
    squared_phase = np.unwrap(np.angle(squared))
    weights = np.clip(np.abs(squared), 1e-3, None)
    slope = float(np.polyfit(sample_index, squared_phase, 1, w=weights)[0])
    cfo_cycles_per_sample = slope / (4.0 * np.pi)
    corrected = signal * np.exp(-2j * np.pi * cfo_cycles_per_sample * sample_index)
    carrier_phase = float(0.5 * np.angle(np.mean(corrected**2)))
    projected = np.real(corrected * np.exp(-1j * carrier_phase))
    samples_per_symbol = float(sample_rate_hz / symbol_rate_hz)
    window = max(1, int(round(samples_per_symbol * 0.65)))
    if window > 1:
        projected = np.convolve(projected, _boxcar(window), mode="same")
    soft, timing_phase, timing_score = _timing_slice(projected, samples_per_symbol)
    return (soft < 0).astype(np.uint8), {
        "cfo_hz": cfo_cycles_per_sample * sample_rate_hz,
        "carrier_phase_rad_mod_pi": carrier_phase,
        "timing_phase_samples": timing_phase,
        "timing_score": timing_score,
    }


def _bounded_nth_power_carrier_correct(
    signal: Any,
    sample_rate_hz: float,
    *,
    order: int,
    max_carrier_offset_hz: float = 2_000.0,
) -> tuple[Any, float, float]:
    """Truth-free bounded n-th-power carrier recovery for PSK waveforms.

    The RML24 article bounds CFO plus Doppler by 1.5 kHz.  The extra 0.5 kHz
    margin absorbs FFT quantization and unmodelled residual error without
    allowing symbol-rate spectral features to win the carrier search.
    """

    np = _require_numpy()
    if order not in {2, 4}:
        raise ValueError("PSK carrier order must be two or four")
    if max_carrier_offset_hz <= 0 or order * max_carrier_offset_hz >= sample_rate_hz / 2:
        raise ValueError("carrier search bound is outside the sampled spectrum")
    powered = signal**order
    target_fft = max(65_536, len(signal) * 32)
    fft_size = min(262_144, 1 << int(np.ceil(np.log2(target_fft))))
    spectrum = np.fft.fft(powered * _hanning(len(powered)), n=fft_size)
    frequencies = _fft_frequencies(fft_size, sample_rate_hz)
    allowed = np.abs(frequencies) <= order * max_carrier_offset_hz
    allowed_indices = np.flatnonzero(allowed)
    peak = int(allowed_indices[np.argmax(np.abs(spectrum[allowed]))])
    carrier_hz = float(frequencies[peak] / order)
    sample_index = _sample_axis(len(signal))
    corrected = signal * np.exp(-2j * np.pi * carrier_hz * sample_index / sample_rate_hz)
    reference_sign = -1.0 if order == 4 else 1.0
    carrier_phase = float(np.angle(reference_sign * np.mean(corrected**order)) / order)
    return corrected * np.exp(-1j * carrier_phase), carrier_hz, carrier_phase


def demodulate_bpsk_v2_unaligned(
    iq: Any,
    *,
    sample_rate_hz: float,
    symbol_rate_hz: float,
) -> tuple[Any, dict[str, float]]:
    """BPSK v2 with a bounded FFT square-law carrier estimate."""

    np = _require_numpy()
    signal = _complex_record(iq)
    corrected, cfo_hz, carrier_phase = _bounded_nth_power_carrier_correct(
        signal,
        sample_rate_hz,
        order=2,
    )
    projected = corrected.real
    samples_per_symbol = float(sample_rate_hz / symbol_rate_hz)
    window = max(1, int(round(samples_per_symbol * 0.65)))
    if window > 1:
        projected = np.convolve(projected, _boxcar(window), mode="same")
    soft, timing_phase, timing_score = _timing_slice(projected, samples_per_symbol)
    return (soft < 0).astype(np.uint8), {
        "cfo_hz": cfo_hz,
        "carrier_phase_rad_mod_pi": carrier_phase,
        "timing_phase_samples": timing_phase,
        "timing_score": timing_score,
    }


def _two_means_midpoint(values: Any) -> tuple[float, float, float]:
    np = _require_numpy()
    lower, upper = (float(value) for value in np.percentile(values, [25, 75]))
    for _ in range(12):
        midpoint = 0.5 * (lower + upper)
        mask = values <= midpoint
        if not np.any(mask) or np.all(mask):
            break
        new_lower = float(np.mean(values[mask]))
        new_upper = float(np.mean(values[~mask]))
        if abs(new_lower - lower) + abs(new_upper - upper) < 1e-10:
            lower, upper = new_lower, new_upper
            break
        lower, upper = new_lower, new_upper
    return lower, upper, 0.5 * (lower + upper)


def demodulate_binary_fsk_unaligned(
    iq: Any,
    *,
    sample_rate_hz: float,
    symbol_rate_hz: float,
) -> tuple[Any, dict[str, float]]:
    """Binary FSK/GMSK discriminator with blind midpoint and bounded timing."""

    np = _require_numpy()
    signal = _complex_record(iq)
    discriminator = np.angle(signal[1:] * np.conj(signal[:-1]))
    lower, upper, midpoint = _two_means_midpoint(discriminator)
    centered = discriminator - midpoint
    samples_per_symbol = float(sample_rate_hz / symbol_rate_hz)
    window = max(1, int(round(samples_per_symbol * 0.8)))
    if window > 1:
        centered = np.convolve(centered, _boxcar(window), mode="same")
    soft, timing_phase, timing_score = _timing_slice(centered, samples_per_symbol)
    return (soft < 0).astype(np.uint8), {
        "cfo_hz": midpoint * sample_rate_hz / (2.0 * np.pi),
        "tone_separation_hz": abs(upper - lower) * sample_rate_hz / (2.0 * np.pi),
        "timing_phase_samples": timing_phase,
        "timing_score": timing_score,
    }


@dataclass(frozen=True, slots=True)
class QpskUnaligned:
    """Truth-free QPSK decisions, possibly with both OQPSK stagger hypotheses."""

    bit_variants: tuple[Any, ...]
    variant_names: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.bit_variants or len(self.bit_variants) != len(self.variant_names):
            raise ValueError("QPSK decision variants and names must be nonempty and paired")


def _qpsk_carrier_correct(signal: Any, sample_rate_hz: float) -> tuple[Any, float, float]:
    """Remove QPSK data with the fourth power and estimate CFO/phase modulo pi/2."""

    np = _require_numpy()
    sample_index = _sample_axis(len(signal))
    fourth = signal**4
    # A zero-padded fourth-power periodogram avoids the cycle slips that a
    # direct phase unwrap suffers on short, noisy 2048-sample records.  It is
    # still bounded per record and uses no labels other than sample rate.
    target_fft = max(4096, len(signal) * 16)
    fft_size = min(262_144, 1 << int(np.ceil(np.log2(target_fft))))
    spectrum = np.fft.fft(fourth * _hanning(len(fourth)), n=fft_size)
    peak = int(np.argmax(np.abs(spectrum)))
    fourth_power_hz = float(_fft_frequencies(fft_size, sample_rate_hz)[peak])
    cfo_cycles_per_sample = fourth_power_hz / (4.0 * sample_rate_hz)
    corrected = signal * np.exp(-2j * np.pi * cfo_cycles_per_sample * sample_index)
    # The minus sign removes the pi phase of a square QPSK constellation whose
    # ideal points lie at +/-pi/4 and +/-3pi/4.
    carrier_phase = float(0.25 * np.angle(-np.mean(corrected**4)))
    return (
        corrected * np.exp(-1j * carrier_phase),
        cfo_cycles_per_sample * sample_rate_hz,
        carrier_phase,
    )


def _qpsk_eye_score(symbols: Any) -> float:
    np = _require_numpy()
    scale = float(np.sqrt(np.mean(np.abs(symbols) ** 2))) + 1e-12
    return float(np.mean(np.minimum(np.abs(symbols.real), np.abs(symbols.imag))) / scale)


def _qpsk_timing_slice(signal: Any, samples_per_symbol: float) -> tuple[Any, float, float]:
    """Select one common I/Q timing phase without consulting transmitted bits."""

    np = _require_numpy()
    if not np.isfinite(samples_per_symbol) or samples_per_symbol < 1.5:
        raise ValueError("samples_per_symbol must be at least 1.5")
    best_symbols = None
    best_phase = 0.0
    best_score = -np.inf
    for phase, positions in _timing_grid(len(signal), samples_per_symbol):
        if len(positions) < 4:
            continue
        symbols = _interpolate(signal.real, positions) + 1j * _interpolate(
            signal.imag, positions
        )
        score = _qpsk_eye_score(symbols)
        if score > best_score:
            best_symbols = symbols
            best_phase = float(phase)
            best_score = score
    if best_symbols is None:
        raise ValueError("QPSK timing search yielded no symbols")
    return best_symbols, best_phase, best_score


def _oqpsk_timing_slice(
    signal: Any,
    samples_per_symbol: float,
    *,
    delayed_branch: str,
) -> tuple[Any, float, float]:
    """Sample one OQPSK branch half a symbol after the other."""

    np = _require_numpy()
    if delayed_branch not in {"i", "q"}:
        raise ValueError("delayed_branch must be i or q")
    if not np.isfinite(samples_per_symbol) or samples_per_symbol < 2.0:
        raise ValueError("OQPSK requires at least two samples per symbol")
    best_symbols = None
    best_phase = 0.0
    best_score = -np.inf
    for phase, early, late in _oqpsk_timing_grid(len(signal), samples_per_symbol):
        if len(early) < 4:
            continue
        if delayed_branch == "q":
            i_values = _interpolate(signal.real, early)
            q_values = _interpolate(signal.imag, late)
        else:
            i_values = _interpolate(signal.real, late)
            q_values = _interpolate(signal.imag, early)
        symbols = i_values + 1j * q_values
        score = _qpsk_eye_score(symbols)
        if score > best_score:
            best_symbols = symbols
            best_phase = float(phase)
            best_score = score
    if best_symbols is None:
        raise ValueError("OQPSK timing search yielded no symbols")
    return best_symbols, best_phase, best_score


def _quadrant_bits(symbols: Any) -> Any:
    np = _require_numpy()
    return np.column_stack((symbols.real < 0, symbols.imag < 0)).astype(np.uint8).reshape(-1)


def demodulate_qpsk_unaligned(
    iq: Any,
    *,
    sample_rate_hz: float,
    symbol_rate_hz: float,
    offset: bool = False,
) -> tuple[QpskUnaligned, dict[str, object]]:
    """QPSK/OQPSK recovery that emits decisions before truth-based ambiguity resolution."""

    np = _require_numpy()
    signal = _complex_record(iq)
    corrected, cfo_hz, carrier_phase = _qpsk_carrier_correct(signal, sample_rate_hz)
    samples_per_symbol = float(sample_rate_hz / symbol_rate_hz)
    window_fraction = 0.3 if offset else 0.65
    window = max(1, int(round(samples_per_symbol * window_fraction)))
    if window > 1:
        corrected = np.convolve(corrected, _boxcar(window), mode="same")
    if not offset:
        symbols, timing_phase, timing_score = _qpsk_timing_slice(
            corrected, samples_per_symbol
        )
        decisions = QpskUnaligned((_quadrant_bits(symbols),), ("common_iq_timing",))
        timing: dict[str, object] = {
            "selected_phase_samples": timing_phase,
            "eye_score": timing_score,
        }
    else:
        variants: list[Any] = []
        names: list[str] = []
        hypotheses: dict[str, object] = {}
        # A pi/2 carrier ambiguity exchanges I and Q, reversing which physical
        # branch is delayed.  Preserve both truth-free hypotheses until the
        # explicitly permitted final constellation/alignment scorer.
        for delayed_branch in ("q", "i"):
            symbols, timing_phase, timing_score = _oqpsk_timing_slice(
                corrected,
                samples_per_symbol,
                delayed_branch=delayed_branch,
            )
            name = f"{delayed_branch}_delayed_half_symbol"
            variants.append(_quadrant_bits(symbols))
            names.append(name)
            hypotheses[name] = {
                "selected_phase_samples": timing_phase,
                "eye_score": timing_score,
            }
        decisions = QpskUnaligned(tuple(variants), tuple(names))
        timing = hypotheses
    return decisions, {
        "cfo_hz": cfo_hz,
        "carrier_phase_rad_mod_pi_over_2": carrier_phase,
        "timing": timing,
    }


def _oqpsk_pairing_variants(symbols: Any, *, delayed_branch: str) -> tuple[list[Any], list[str]]:
    """Preserve the three boundary-valid I/Q pairings of an OQPSK record."""

    i_values = symbols.real
    q_values = symbols.imag
    variants: list[Any] = []
    names: list[str] = []
    for pairing_offset in (-1, 0, 1):
        if pairing_offset < 0:
            paired = i_values[-pairing_offset:] + 1j * q_values[:pairing_offset]
        elif pairing_offset > 0:
            paired = i_values[:-pairing_offset] + 1j * q_values[pairing_offset:]
        else:
            paired = symbols
        variants.append(_quadrant_bits(paired))
        names.append(
            f"{delayed_branch}_delayed_half_symbol_pair_offset_{pairing_offset:+d}"
        )
    return variants, names


def demodulate_qpsk_v2_unaligned(
    iq: Any,
    *,
    sample_rate_hz: float,
    symbol_rate_hz: float,
    offset: bool = False,
) -> tuple[QpskUnaligned, dict[str, object]]:
    """Bounded-carrier QPSK/OQPSK with truth-free boundary hypotheses."""

    np = _require_numpy()
    signal = _complex_record(iq)
    corrected, cfo_hz, carrier_phase = _bounded_nth_power_carrier_correct(
        signal,
        sample_rate_hz,
        order=4,
    )
    samples_per_symbol = float(sample_rate_hz / symbol_rate_hz)
    window_fraction = 0.3 if offset else 0.65
    window = max(1, int(round(samples_per_symbol * window_fraction)))
    if window > 1:
        corrected = np.convolve(corrected, _boxcar(window), mode="same")
    if not offset:
        symbols, timing_phase, timing_score = _qpsk_timing_slice(
            corrected, samples_per_symbol
        )
        decisions = QpskUnaligned((_quadrant_bits(symbols),), ("common_iq_timing",))
        timing: dict[str, object] = {
            "selected_phase_samples": timing_phase,
            "eye_score": timing_score,
        }
    else:
        variants: list[Any] = []
        names: list[str] = []
        hypotheses: dict[str, object] = {}
        for delayed_branch in ("q", "i"):
            symbols, timing_phase, timing_score = _oqpsk_timing_slice(
                corrected,
                samples_per_symbol,
                delayed_branch=delayed_branch,
            )
            branch_variants, branch_names = _oqpsk_pairing_variants(
                symbols,
                delayed_branch=delayed_branch,
            )
            variants.extend(branch_variants)
            names.extend(branch_names)
            for name in branch_names:
                hypotheses[name] = {
                    "selected_phase_samples": timing_phase,
                    "eye_score": timing_score,
                }
        decisions = QpskUnaligned(tuple(variants), tuple(names))
        timing = hypotheses
    return decisions, {
        "cfo_hz": cfo_hz,
        "carrier_phase_rad_mod_pi_over_2": carrier_phase,
        "timing": timing,
    }


@dataclass(frozen=True, slots=True)
class AlignmentPolicy:
    max_shift_bits: int = 8
    allow_global_polarity_inversion: bool = True
    allow_iq_reflection: bool = True
    missing_truth_bits_count_as_errors: bool = True

    def __post_init__(self) -> None:
        if self.max_shift_bits < 0:
            raise ValueError("max_shift_bits must be nonnegative")


def align_for_ber(
    candidate: Any,
    truth: Any,
    *,
    policy: AlignmentPolicy,
) -> tuple[Any, dict[str, int | bool]]:
    """Use truth only here, for declared shift/polarity ambiguity resolution."""

    np = _require_numpy()
    predicted = np.asarray(candidate, dtype=np.uint8).reshape(-1) & 1
    expected = np.asarray(truth, dtype=np.uint8).reshape(-1) & 1
    if not len(expected):
        raise ValueError("truth sequence is empty")
    polarities = (0, 1) if policy.allow_global_polarity_inversion else (0,)
    best_rank: tuple[int, int, int, int] | None = None
    best_payload: tuple[int, int, int, Any] | None = None
    for shift in range(-policy.max_shift_bits, policy.max_shift_bits + 1):
        truth_start = max(0, -shift)
        predicted_start = max(0, shift)
        overlap = min(len(expected) - truth_start, len(predicted) - predicted_start)
        if overlap <= 0:
            continue
        for polarity in polarities:
            segment = predicted[predicted_start : predicted_start + overlap] ^ polarity
            mismatches = int(
                np.count_nonzero(segment != expected[truth_start : truth_start + overlap])
            )
            missing = len(expected) - overlap
            errors = mismatches + (missing if policy.missing_truth_bits_count_as_errors else 0)
            rank = (errors, -overlap, abs(shift), polarity)
            if best_rank is None or rank < best_rank:
                aligned = (
                    1 - expected
                    if policy.missing_truth_bits_count_as_errors
                    else np.zeros_like(expected)
                )
                aligned[truth_start : truth_start + overlap] = segment
                best_rank = rank
                best_payload = (shift, polarity, overlap, aligned)
    if best_rank is None or best_payload is None:
        raise ValueError("no permitted alignment has any overlap")
    errors = best_rank[0]
    winning_shift, polarity, overlap, aligned = best_payload
    return aligned, {
        "shift_bits": winning_shift,
        "polarity_inverted": bool(polarity),
        "overlap_bits": overlap,
        "truth_bits": len(expected),
        "errors_including_missing": errors,
    }


def _qpsk_symmetry(bits: Any, quarter_turns: int, reflected: bool) -> Any:
    """Apply one square-constellation symmetry to canonical hard decisions."""

    np = _require_numpy()
    values = np.asarray(bits, dtype=np.uint8).reshape(-1) & 1
    if len(values) % 2:
        values = values[:-1]
    pairs = values.reshape(-1, 2)
    symbols = (1.0 - 2.0 * pairs[:, 0]) + 1j * (1.0 - 2.0 * pairs[:, 1])
    if reflected:
        symbols = np.conj(symbols)
    symbols *= (1j) ** quarter_turns
    return _quadrant_bits(symbols)


def align_qpsk_for_ber(
    candidate: QpskUnaligned,
    truth: Any,
    *,
    policy: AlignmentPolicy,
) -> tuple[Any, dict[str, int | bool | str]]:
    """Resolve only final QPSK/OQPSK timing, D4 and whole-symbol shift ambiguities."""

    np = _require_numpy()
    expected = np.asarray(truth, dtype=np.uint8).reshape(-1) & 1
    if not len(expected):
        raise ValueError("truth sequence is empty")
    reflections = (False, True) if policy.allow_iq_reflection else (False,)
    max_shift_symbols = policy.max_shift_bits // 2
    best_rank: tuple[int, int, int, int, int, int] | None = None
    best_payload: tuple[int, int, bool, int, str, Any] | None = None
    for variant_index, raw in enumerate(candidate.bit_variants):
        for reflected in reflections:
            for quarter_turns in range(4):
                predicted = _qpsk_symmetry(raw, quarter_turns, reflected)
                for symbol_shift in range(-max_shift_symbols, max_shift_symbols + 1):
                    shift = 2 * symbol_shift
                    truth_start = max(0, -shift)
                    predicted_start = max(0, shift)
                    overlap = min(
                        len(expected) - truth_start,
                        len(predicted) - predicted_start,
                    )
                    if overlap <= 0:
                        continue
                    segment = predicted[predicted_start : predicted_start + overlap]
                    mismatches = int(
                        np.count_nonzero(
                            segment != expected[truth_start : truth_start + overlap]
                        )
                    )
                    missing = len(expected) - overlap
                    errors = mismatches + (
                        missing if policy.missing_truth_bits_count_as_errors else 0
                    )
                    rank = (
                        errors,
                        -overlap,
                        abs(symbol_shift),
                        int(reflected),
                        quarter_turns,
                        variant_index,
                    )
                    if best_rank is None or rank < best_rank:
                        aligned = (
                            1 - expected
                            if policy.missing_truth_bits_count_as_errors
                            else np.zeros_like(expected)
                        )
                        aligned[truth_start : truth_start + overlap] = segment
                        best_rank = rank
                        best_payload = (
                            symbol_shift,
                            quarter_turns,
                            reflected,
                            overlap,
                            candidate.variant_names[variant_index],
                            aligned,
                        )
    if best_rank is None or best_payload is None:
        raise ValueError("no permitted QPSK alignment has any overlap")
    symbol_shift, quarter_turns, reflected, overlap, variant_name, aligned = best_payload
    return aligned, {
        "shift_bits": 2 * symbol_shift,
        "shift_symbols": symbol_shift,
        "constellation_rotation_quarter_turns": quarter_turns,
        "constellation_reflected": reflected,
        "timing_hypothesis": variant_name,
        "overlap_bits": overlap,
        "truth_bits": len(expected),
        "errors_including_missing": best_rank[0],
    }


class Rml24PhysicalBerPlugin:
    """Modulation-aware router; waveform recovery remains truth-isolated."""

    supported_modulations = SUPPORTED_MODULATIONS

    def __init__(
        self,
        *,
        sample_rate_hz: float = 1_000_000.0,
        alignment_policy: AlignmentPolicy = AlignmentPolicy(),
        recovery_version: str = "legacy",
    ) -> None:
        if sample_rate_hz <= 0:
            raise ValueError("sample_rate_hz must be positive")
        if recovery_version not in {"legacy", "carrier_timing_v2"}:
            raise ValueError("recovery_version must be legacy or carrier_timing_v2")
        self.sample_rate_hz = sample_rate_hz
        self.alignment_policy = alignment_policy
        self.recovery_version = recovery_version
        self._records = 0
        self._polarity = Counter()
        self._shifts = Counter()
        self._constellation = Counter()
        self._timing_hypotheses = Counter()

    def supports(self, modulation: object) -> bool:
        return str(_scalar(modulation)).upper() in self.supported_modulations

    def demodulate_unaligned(self, iq: Any, batch: Rml24Batch) -> tuple[Any, ...]:
        """No access to ``batch.bits`` or ``batch.bit_lengths`` occurs here."""

        np = _require_numpy()
        if batch.symbol_rate_hz is None:
            raise ValueError("RML24 physical plugin requires nominal symbol-rate metadata")
        output: list[Any] = []
        for row in range(batch.record_count):
            modulation = str(_scalar(batch.modulation[row])).upper()
            if modulation not in self.supported_modulations:
                raise ValueError(f"unsupported modulation {modulation}")
            symbol_rate = float(_scalar(batch.symbol_rate_hz[row]))
            if modulation == "BPSK":
                demodulator = (
                    demodulate_bpsk_v2_unaligned
                    if self.recovery_version == "carrier_timing_v2"
                    else demodulate_bpsk_unaligned
                )
                bits, _diagnostics = demodulator(
                    batch.iq[row],
                    sample_rate_hz=self.sample_rate_hz,
                    symbol_rate_hz=symbol_rate,
                )
                output.append(np.asarray(bits, dtype=np.uint8))
            elif modulation in {"QPSK", "OQPSK"}:
                demodulator = (
                    demodulate_qpsk_v2_unaligned
                    if self.recovery_version == "carrier_timing_v2"
                    else demodulate_qpsk_unaligned
                )
                decisions, _diagnostics = demodulator(
                    batch.iq[row],
                    sample_rate_hz=self.sample_rate_hz,
                    symbol_rate_hz=symbol_rate,
                    offset=modulation == "OQPSK",
                )
                output.append(decisions)
            else:
                bits, _diagnostics = demodulate_binary_fsk_unaligned(
                    batch.iq[row],
                    sample_rate_hz=self.sample_rate_hz,
                    symbol_rate_hz=symbol_rate,
                )
                output.append(np.asarray(bits, dtype=np.uint8))
        return tuple(output)

    def __call__(self, iq: Any, batch: Rml24Batch) -> Any:
        np = _require_numpy()
        if batch.bits is None:
            raise ValueError("BER scoring requires ground-truth bits")
        candidates = self.demodulate_unaligned(iq, batch)
        truth = np.asarray(batch.bits)
        lengths = (
            np.asarray(batch.bit_lengths, dtype=np.int64).reshape(-1)
            if batch.bit_lengths is not None
            else np.full(batch.record_count, truth.shape[1], dtype=np.int64)
        )
        aligned = np.zeros_like(truth, dtype=np.uint8)
        for row, candidate in enumerate(candidates):
            width = int(lengths[row])
            modulation = str(_scalar(batch.modulation[row])).upper()
            if modulation in {"QPSK", "OQPSK"}:
                if not isinstance(candidate, QpskUnaligned):
                    raise TypeError("QPSK recovery did not return constellation decisions")
                value, diagnostics = align_qpsk_for_ber(
                    candidate,
                    truth[row, :width],
                    policy=self.alignment_policy,
                )
                key = (
                    f"rotation={diagnostics['constellation_rotation_quarter_turns']},"
                    f"reflected={str(diagnostics['constellation_reflected']).lower()}"
                )
                self._constellation[key] += 1
                self._timing_hypotheses[str(diagnostics["timing_hypothesis"])] += 1
            else:
                value, diagnostics = align_for_ber(
                    candidate,
                    truth[row, :width],
                    policy=self.alignment_policy,
                )
                self._polarity[str(diagnostics["polarity_inverted"]).lower()] += 1
            aligned[row, :width] = value
            self._records += 1
            self._shifts[str(diagnostics["shift_bits"])] += 1
        return aligned

    def benchmark_metadata(self) -> dict[str, object]:
        return {
            "name": "rml24-classical-bpsk-qpsk-oqpsk-binary-fsk-v2",
            "supported_modulations": sorted(self.supported_modulations),
            "explicitly_not_supported": [
                "SOQPSK-TG",
                "FQPSK",
                "ARTM",
                "FM",
                "PM",
                "8PSK",
                "16QAM",
                "32QAM",
                "64QAM",
                "16APSK",
                "32APSK",
                "PCM-BPSK-PM",
                "PCM-QPSK-PM",
                "PCM-SOQPSK-PM",
                "PCM-FQPSK-PM",
                "PCM-BPSK-FM",
                "PCM-QPSK-FM",
                "PCM-2FSK-PM",
            ],
            "sample_rate_hz": self.sample_rate_hz,
            "routing_uses_dataset_modulation_and_symbol_rate_labels": True,
            "amr_claimed": False,
            "truth_bits_visible_to_waveform_recovery": False,
            "truth_bits_used_only_by": (
                "final bounded shift, polarity, square-constellation symmetry, "
                "OQPSK stagger-hypothesis alignment, and BER"
            ),
            "alignment_policy": asdict(self.alignment_policy),
            "recovery": {
                "BPSK": "square-law CFO/phase plus unsupervised bounded timing phase",
                "QPSK": "fourth-power CFO/phase plus unsupervised common I/Q timing",
                "OQPSK": (
                    "fourth-power CFO/phase plus both truth-free half-symbol "
                    "branch-stagger hypotheses"
                ),
                "GMSK/2FSK": (
                    "phase discriminator, blind two-means midpoint/CFO, "
                    "bounded timing phase"
                ),
            },
            "records_aligned": self._records,
            "polarity_choices": dict(sorted(self._polarity.items())),
            "shift_choices": dict(sorted(self._shifts.items(), key=lambda item: int(item[0]))),
            "constellation_choices": dict(sorted(self._constellation.items())),
            "oqpsk_timing_hypothesis_choices": dict(sorted(self._timing_hypotheses.items())),
        }
