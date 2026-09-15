"""Deterministic single-process timing recovery for bounded FSK replays.

The implementation deliberately avoids a streaming scheduler.  A fixed bank
of fractional symbol clocks is scored in a stable order, and ties are broken
by explicit numeric keys.  AX.25 output is returned only after HDLC unstuffing
and validation of the complete CRC-16/X-25 frame.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from .camras_replay import bits_to_bytes, hdlc_unstuff
from .crc import validate_ax25_fcs


HDLC_FLAG = (0, 1, 1, 1, 1, 1, 1, 0)
AX25_MIN_PAYLOAD_BYTES = 14
AX25_MAX_DECODER_FRAME_BYTES = 1024


@dataclass(frozen=True)
class TimingHypothesis:
    """One deterministic fractional-clock hypothesis."""

    rate_error_ppm: float
    phase_samples: float
    step_samples: float
    threshold: float
    score: float
    symbol_count: int


@dataclass(frozen=True)
class DemodulationMetrics:
    """Stable metrics for one bounded complex-IQ window."""

    input_complex_samples: int
    output_real_samples: int
    carrier_offset_hz: float
    normalization_scale: float
    trimmed_input_samples: int


def recover_timing_hypotheses(
    samples: Sequence[float],
    *,
    nominal_samples_per_symbol: float,
    rate_errors_ppm: Sequence[float],
    phase_bins: int = 32,
    top_n: int = 8,
    guard_symbols: int = 32,
) -> tuple[TimingHypothesis, ...]:
    """Rank a fixed phase/rate bank using an unsupervised eye score.

    The score is the distance between the two median-split symbol clusters
    divided by their pooled within-cluster deviation.  Reference frame bytes
    are not an input to this function.
    """

    import numpy as np

    values = np.asarray(samples, dtype=np.float64)
    if values.ndim != 1 or values.size < 256:
        raise ValueError("samples must be a one-dimensional array of length >= 256")
    if not np.all(np.isfinite(values)):
        raise ValueError("samples must be finite")
    if nominal_samples_per_symbol <= 1.0:
        raise ValueError("nominal_samples_per_symbol must be greater than one")
    if not rate_errors_ppm:
        raise ValueError("rate_errors_ppm must not be empty")
    if phase_bins < 4 or top_n < 1 or guard_symbols < 0:
        raise ValueError("invalid timing-search budget")

    ordered_rates = tuple(sorted({float(value) for value in rate_errors_ppm}))
    steps = tuple(
        nominal_samples_per_symbol * (1.0 + value * 1e-6)
        for value in ordered_rates
    )
    if min(steps) <= 1.0:
        raise ValueError("rate error produces an invalid symbol step")

    maximum_step = max(steps)
    shared_symbols = int((values.size - maximum_step) // maximum_step)
    shared_symbols -= 2 * guard_symbols
    if shared_symbols < 128:
        raise ValueError("not enough samples for the requested guard")
    symbol_indices = np.arange(
        guard_symbols,
        guard_symbols + shared_symbols,
        dtype=np.float64,
    )
    sample_indices = np.arange(values.size, dtype=np.float64)

    hypotheses: list[TimingHypothesis] = []
    for rate_ppm, step in zip(ordered_rates, steps, strict=True):
        for phase_index in range(phase_bins):
            phase = step * (phase_index + 0.5) / phase_bins
            positions = phase + symbol_indices * step
            symbols = np.interp(positions, sample_indices, values)
            threshold = float(np.median(symbols))
            lower = symbols[symbols < threshold]
            upper = symbols[symbols >= threshold]
            if lower.size < 16 or upper.size < 16:
                score = float("-inf")
            else:
                lower_mean = float(lower.mean())
                upper_mean = float(upper.mean())
                threshold = 0.5 * (lower_mean + upper_mean)
                separation = upper_mean - lower_mean
                within = float(
                    np.sqrt(0.5 * (lower.var() + upper.var()) + 1e-15)
                )
                score = separation / within
            hypotheses.append(
                TimingHypothesis(
                    rate_error_ppm=rate_ppm,
                    phase_samples=float(phase),
                    step_samples=float(step),
                    threshold=threshold,
                    score=float(score),
                    symbol_count=shared_symbols,
                )
            )

    hypotheses.sort(
        key=lambda item: (
            -item.score,
            abs(item.rate_error_ppm),
            item.rate_error_ppm,
            item.phase_samples,
        )
    )
    return tuple(hypotheses[:top_n])


def slice_with_hypothesis(
    samples: Sequence[float],
    hypothesis: TimingHypothesis,
    *,
    guard_symbols: int = 32,
) -> tuple[int, ...]:
    """Return one complete deterministic NRZI-level candidate."""

    import numpy as np

    values = np.asarray(samples, dtype=np.float64)
    symbol_indices = np.arange(
        guard_symbols,
        guard_symbols + hypothesis.symbol_count,
        dtype=np.float64,
    )
    positions = hypothesis.phase_samples + symbol_indices * hypothesis.step_samples
    symbols = np.interp(
        positions,
        np.arange(values.size, dtype=np.float64),
        values,
    )
    return tuple((symbols >= hypothesis.threshold).astype(np.uint8).tolist())


def soft_symbols_with_hypothesis(
    samples: Sequence[float],
    hypothesis: TimingHypothesis,
    *,
    guard_symbols: int = 32,
) -> tuple[float, ...]:
    """Return interpolated symbol values without discarding reliability.

    Protocol adapters can hard-slice these values, feed them to a soft FEC
    decoder, or perform a bounded list search.  Keeping this boundary free of
    framing assumptions is what lets one timing bank serve many satellites.
    """

    import numpy as np

    values = np.asarray(samples, dtype=np.float64)
    if values.ndim != 1 or not np.all(np.isfinite(values)):
        raise ValueError("samples must be a finite one-dimensional array")
    if guard_symbols < 0:
        raise ValueError("guard_symbols must be non-negative")
    symbol_indices = np.arange(
        guard_symbols,
        guard_symbols + hypothesis.symbol_count,
        dtype=np.float64,
    )
    positions = hypothesis.phase_samples + symbol_indices * hypothesis.step_samples
    if positions.size and positions[-1] > values.size - 1:
        raise ValueError("timing hypothesis extends beyond the sample stream")
    symbols = np.interp(
        positions,
        np.arange(values.size, dtype=np.float64),
        values,
    )
    return tuple(float(value) for value in symbols)


def nrzi_decode_levels(
    levels: Sequence[int], *, initial_level: int = 0
) -> tuple[int, ...]:
    """Decode NRZI with AX.25 transition=0 and no-transition=1."""

    if initial_level not in (0, 1):
        raise ValueError("initial_level must be zero or one")
    previous = initial_level
    output: list[int] = []
    for level in levels:
        if level not in (0, 1):
            raise ValueError("levels must contain only zero and one")
        output.append(1 if level == previous else 0)
        previous = level
    return tuple(output)


def g3ruh_descramble_bits(bits: Sequence[int]) -> tuple[int, ...]:
    """Reproduce GNU Radio ``lfsr(0x21, 0, 16).next_bit_descramble``."""

    register = 0
    output: list[int] = []
    for bit in bits:
        if bit not in (0, 1):
            raise ValueError("bits must contain only zero and one")
        parity = ((register & 0x21).bit_count() & 1)
        output.append(parity ^ bit)
        register = (register >> 1) | (bit << 16)
    return tuple(output)


def g3ruh_scramble_bits(bits: Sequence[int]) -> tuple[int, ...]:
    """Inverse synthetic helper matching GNU Radio's self-sync scrambler."""

    register = 0
    output: list[int] = []
    for bit in bits:
        if bit not in (0, 1):
            raise ValueError("bits must contain only zero and one")
        encoded = register & 1
        new_bit = ((register & 0x21).bit_count() & 1) ^ bit
        register = (register >> 1) | (new_bit << 16)
        output.append(encoded)
    return tuple(output)


def extract_valid_ax25_frames(
    bits: Sequence[int],
    *,
    min_payload_bytes: int = AX25_MIN_PAYLOAD_BYTES,
    max_decoder_frame_bytes: int = AX25_MAX_DECODER_FRAME_BYTES,
) -> tuple[bytes, ...]:
    """Extract complete AX.25 frames within gr-satnogs size limits.

    ``gr-satnogs`` increments ``d_received_bytes`` for every decoded octet,
    including the two FCS octets, and resets when the count is greater than or
    equal to ``d_max_frame_len``.  Thus a configured value of 1024 accepts at
    most 1023 octets including FCS, or 1021 octets after FCS removal.
    """

    checked = tuple(bits)
    if any(bit not in (0, 1) for bit in checked):
        raise ValueError("bits must contain only zero and one")
    if min_payload_bytes < 0 or max_decoder_frame_bytes < 3:
        raise ValueError("invalid AX.25 frame limits")
    maximum_unstuffed_bits = (max_decoder_frame_bytes - 1) * 8
    maximum_stuffed_bits = maximum_unstuffed_bits + (
        maximum_unstuffed_bits + 4
    ) // 5
    offsets = tuple(
        index
        for index in range(len(checked) - len(HDLC_FLAG) + 1)
        if checked[index : index + len(HDLC_FLAG)] == HDLC_FLAG
    )
    frames: list[bytes] = []
    seen: set[bytes] = set()
    for left, right in zip(offsets, offsets[1:]):
        region = checked[left + len(HDLC_FLAG) : right]
        if not region or len(region) > maximum_stuffed_bits:
            continue
        try:
            unstuffed = hdlc_unstuff(region)
            frame = bits_to_bytes(unstuffed, lsb_first=True)
        except (ValueError, RuntimeError):
            continue
        if (
            len(frame) >= min_payload_bytes + 2
            and len(frame) < max_decoder_frame_bytes
            and validate_ax25_fcs(frame)
            and frame not in seen
        ):
            frames.append(frame)
            seen.add(frame)
    return tuple(frames)


def decode_ax25_levels(
    levels: Sequence[int], *, g3ruh: bool
) -> tuple[bytes, ...]:
    """Decode only complete CRC-valid AX.25 frames from sliced levels."""

    frames: list[bytes] = []
    seen: set[bytes] = set()
    for initial_level in (0, 1):
        bits = nrzi_decode_levels(levels, initial_level=initial_level)
        if g3ruh:
            bits = g3ruh_descramble_bits(bits)
        for frame in extract_valid_ax25_frames(bits):
            if frame not in seen:
                frames.append(frame)
                seen.add(frame)
    return tuple(frames)


def read_complex_i16_window(
    path: str | Path,
    *,
    start_seconds: float,
    duration_seconds: float,
    sample_rate_hz: int = 57_600,
    scale: float = 16_768.0,
):
    """Read one bounded little-endian interleaved-IQ window as complex128."""

    import numpy as np

    source = Path(path)
    if start_seconds < 0 or duration_seconds <= 0:
        raise ValueError("invalid window")
    if sample_rate_hz <= 0 or scale <= 0:
        raise ValueError("invalid sample format")
    start_sample = int(round(start_seconds * sample_rate_hz))
    sample_count = int(round(duration_seconds * sample_rate_hz))
    offset = start_sample * 4
    scalar_count = sample_count * 2
    raw = np.fromfile(source, dtype="<i2", count=scalar_count, offset=offset)
    if raw.size != scalar_count:
        raise ValueError("window extends beyond IQ file")
    pairs = raw.reshape(-1, 2).astype(np.float64)
    return (pairs[:, 0] + 1j * pairs[:, 1]) / scale


def deterministic_direct_phase_fsk_demodulate(
    iq,
    *,
    sample_rate_hz: int = 57_600,
    baudrate: float = 9_600,
    decimation: int = 3,
    post_discriminator_cutoff_hz: float | None = None,
):
    """Reproduce the legacy phase-first binary-FSK frontend.

    Unlike :func:`deterministic_fsk_demodulate`, this path takes the phase
    difference on unfiltered IQ and only then low-pass filters the real
    discriminator output.  It is retained as an independent additive branch,
    not as an alias for the channel-conditioned frontend.
    """

    import numpy as np
    from scipy.signal import firwin, lfilter

    values = np.asarray(iq, dtype=np.complex128)
    if values.ndim != 1 or values.size < 8192:
        raise ValueError("IQ window is too short")
    if sample_rate_hz <= 0 or baudrate <= 0 or decimation < 1:
        raise ValueError("sample rate, baudrate, and decimation must be positive")
    output_samples_per_symbol = sample_rate_hz / (decimation * baudrate)
    if output_samples_per_symbol <= 1.1:
        raise ValueError("decimation must leave more than 1.1 samples per symbol")
    cutoff_hz = (
        0.75 * baudrate
        if post_discriminator_cutoff_hz is None
        else float(post_discriminator_cutoff_hz)
    )
    if not 0 < cutoff_hz < 0.5 * sample_rate_hz:
        raise ValueError("post-discriminator cutoff must be below Nyquist")

    increments = np.angle(values[1:] * np.conjugate(values[:-1]))
    carrier_increment = float(np.median(increments))
    carrier_offset_hz = carrier_increment * sample_rate_hz / (2.0 * np.pi)
    taps = firwin(
        115,
        cutoff=cutoff_hz,
        fs=sample_rate_hz,
        window="hamming",
    )
    filtered = lfilter(taps, [1.0], increments)[114::decimation]
    blocker_length = max(32, int(round(512 * output_samples_per_symbol)))
    if filtered.size <= blocker_length:
        raise ValueError("IQ window is too short for the adaptive DC blocker")
    cumulative = np.concatenate(
        (np.zeros(1, dtype=np.float64), np.cumsum(filtered, dtype=np.float64))
    )
    trend = (cumulative[blocker_length:] - cumulative[:-blocker_length])
    trend /= blocker_length
    detrended = filtered[blocker_length - 1 :] - trend
    center = float(np.median(detrended))
    detrended = detrended - center
    normalization = float(np.quantile(np.abs(detrended), 0.90))
    if not np.isfinite(normalization) or normalization <= 1e-12:
        raise ValueError("degenerate discriminator stream")
    output = np.asarray(detrended / normalization, dtype=np.float64)
    metrics = DemodulationMetrics(
        input_complex_samples=int(values.size),
        output_real_samples=int(output.size),
        carrier_offset_hz=carrier_offset_hz,
        normalization_scale=normalization,
        trimmed_input_samples=1 + 114 + decimation * blocker_length,
    )
    return output, metrics


def deterministic_fsk_demodulate(
    iq,
    *,
    sample_rate_hz: int = 57_600,
    baudrate: float = 9_600,
    decimation: int = 3,
):
    """Produce a deterministic two-samples-per-symbol discriminator stream."""

    import numpy as np
    from scipy.signal import firwin, lfilter

    values = np.asarray(iq, dtype=np.complex128)
    if values.ndim != 1 or values.size < 8192:
        raise ValueError("IQ window is too short")
    if sample_rate_hz <= 0 or baudrate <= 0 or decimation < 1:
        raise ValueError("sample rate, baudrate, and decimation must be positive")
    output_samples_per_symbol = sample_rate_hz / (decimation * baudrate)
    if output_samples_per_symbol <= 1.1:
        raise ValueError("decimation must leave more than 1.1 samples per symbol")
    if 0.625 * baudrate >= 0.5 * sample_rate_hz:
        raise ValueError("sample rate is too low for the requested FSK channel")

    relaxed_taps = firwin(
        129,
        cutoff=min(1.25 * baudrate, 0.45 * sample_rate_hz),
        fs=sample_rate_hz,
        window="hamming",
    )
    relaxed = lfilter(relaxed_taps, [1.0], values)
    relaxed = relaxed[128:]
    increments = np.angle(relaxed[1:] * np.conjugate(relaxed[:-1]))
    carrier_increment = float(np.median(increments))
    carrier_offset_hz = carrier_increment * sample_rate_hz / (2.0 * np.pi)

    indices = np.arange(values.size, dtype=np.float64)
    corrected = values * np.exp(-1j * carrier_increment * indices)
    channel_taps = firwin(
        129,
        cutoff=0.625 * baudrate,
        fs=sample_rate_hz,
        window="hamming",
    )
    channel = lfilter(channel_taps, [1.0], corrected)[128::decimation]
    discriminator = 1.2 * np.angle(channel[1:] * np.conjugate(channel[:-1]))

    # Preserve the historical 512-symbol blocker while allowing any valid
    # sample-rate/baud-rate pair.
    blocker_length = max(32, int(round(512 * output_samples_per_symbol)))
    if discriminator.size <= blocker_length:
        raise ValueError("IQ window is too short for the adaptive DC blocker")
    cumulative = np.concatenate(
        (np.zeros(1, dtype=np.float64), np.cumsum(discriminator, dtype=np.float64))
    )
    trend = (cumulative[blocker_length:] - cumulative[:-blocker_length])
    trend /= blocker_length
    detrended = discriminator[blocker_length - 1 :] - trend
    center = float(np.median(detrended))
    detrended = detrended - center
    normalization = float(np.quantile(np.abs(detrended), 0.90))
    if not np.isfinite(normalization) or normalization <= 1e-12:
        raise ValueError("degenerate discriminator stream")
    output = np.asarray(detrended / normalization, dtype=np.float64)
    metrics = DemodulationMetrics(
        input_complex_samples=int(values.size),
        output_real_samples=int(output.size),
        carrier_offset_hz=carrier_offset_hz,
        normalization_scale=normalization,
        trimmed_input_samples=128 + decimation * blocker_length,
    )
    return output, metrics
