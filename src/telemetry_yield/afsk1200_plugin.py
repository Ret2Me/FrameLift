"""Independent Bell-202 AFSK1200/AX.25 receiver plugin.

This module intentionally does not share the gr-satellites AFSK signal path.
It first recovers discriminator audio from complex baseband and then compares
non-coherent mark/space tone energies.  A fixed, truth-independent clock bank
feeds a fail-closed HDLC/CRC/AX.25 validator.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
from typing import Iterator, Literal, Sequence

import numpy as np

from .ax25_validation import AX25UIFrame, parse_ax25_ui
from .camras_replay import bits_to_bytes, hdlc_unstuff
from .ccsds_validation import CCSDSPrimaryHeader, parse_ccsds_space_packet
from .clock_recovery import AX25_MAX_DECODER_FRAME_BYTES, AX25_MIN_PAYLOAD_BYTES
from .crc import validate_ax25_fcs


ControlMode = Literal["identity", "all_zero_iq", "deterministic_sample_permutation"]


@dataclass(frozen=True, slots=True)
class Afsk1200Config:
    sample_rate_hz: int = 57_600
    baudrate: float = 1_200.0
    mark_hz: float = 1_200.0
    space_hz: float = 2_200.0
    audio_sample_rate_hz: int = 9_600
    window_blocks: int = 280
    stride_blocks: int = 140
    permutation_block_samples: int = 4_096
    timing_rate_errors_ppm: tuple[float, ...] = (
        -2_000.0,
        -1_000.0,
        -500.0,
        0.0,
        500.0,
        1_000.0,
        2_000.0,
    )
    timing_phase_bins: int = 16
    timing_top_n: int = 6
    max_candidate_records: int = 4_096

    def __post_init__(self) -> None:
        if self.sample_rate_hz <= 0 or self.audio_sample_rate_hz <= 0:
            raise ValueError("sample rates must be positive")
        if self.sample_rate_hz % self.audio_sample_rate_hz:
            raise ValueError("audio sample rate must divide the IQ sample rate")
        if self.baudrate <= 0 or self.mark_hz <= 0 or self.space_hz <= 0:
            raise ValueError("baudrate and Bell-202 tones must be positive")
        if self.audio_sample_rate_hz / self.baudrate <= 2.0:
            raise ValueError("audio stream must retain more than two samples/symbol")
        if not 0 < self.mark_hz < self.audio_sample_rate_hz / 2:
            raise ValueError("mark tone must be below audio Nyquist")
        if not 0 < self.space_hz < self.audio_sample_rate_hz / 2:
            raise ValueError("space tone must be below audio Nyquist")
        if self.mark_hz == self.space_hz:
            raise ValueError("mark and space tones must differ")
        if self.window_blocks < 2 or not 0 < self.stride_blocks < self.window_blocks:
            raise ValueError("invalid overlapping-window geometry")
        if self.permutation_block_samples < 32:
            raise ValueError("permutation block is too short")
        if self.timing_phase_bins < 4 or self.timing_top_n < 1:
            raise ValueError("invalid timing search budget")
        if not self.timing_rate_errors_ppm:
            raise ValueError("timing rate bank must not be empty")
        if (
            isinstance(self.max_candidate_records, bool)
            or not isinstance(self.max_candidate_records, int)
            or self.max_candidate_records < 1
        ):
            raise ValueError("max_candidate_records must be a positive integer")


@dataclass(frozen=True, slots=True)
class AfskStrictFrame:
    """One frame accepted by every native integrity layer."""

    frame_with_fcs: bytes
    normalized_pdu: bytes
    ax25: AX25UIFrame
    inner_ccsds: CCSDSPrimaryHeader | None
    window_start_sample: int
    rate_error_ppm: float
    phase_index: int
    polarity: Literal["normal", "inverted"]


@dataclass(frozen=True, slots=True)
class AfskRejectedFrame:
    """A CRC-valid HDLC frame rejected by strict AX.25 structure checks."""

    frame_with_fcs: bytes
    rejection_reason: str
    window_start_sample: int
    rate_error_ppm: float
    phase_index: int
    polarity: Literal["normal", "inverted"]


@dataclass(frozen=True, slots=True)
class AfskDecodeResult:
    frames: tuple[AfskStrictFrame, ...]
    windows_examined: int
    timing_hypotheses_examined: int
    crc_valid_candidates: int
    strict_ax25_rejections: int
    degenerate_windows: int
    rejected_frames: tuple[AfskRejectedFrame, ...] = ()


def _moving_tone_energy(values: np.ndarray, frequency_hz: float, sample_rate_hz: int, length: int) -> np.ndarray:
    """Return non-coherent one-symbol tone energy without a carrier PLL."""

    from scipy.signal import lfilter

    indices = np.arange(values.size, dtype=np.float64)
    oscillator = np.exp(-2j * np.pi * frequency_hz * indices / sample_rate_hz)
    taps = np.ones(length, dtype=np.float64) / length
    correlated = lfilter(taps, [1.0], values * oscillator)
    return np.square(np.abs(correlated))


def bell202_soft_discriminator(iq: Sequence[complex], config: Afsk1200Config = Afsk1200Config()) -> np.ndarray:
    """Convert complex RF IQ to mark-minus-space Bell-202 tone energy."""

    from scipy.signal import firwin, lfilter

    values = np.asarray(iq, dtype=np.complex64)
    if values.ndim != 1 or values.size < 16_384:
        raise ValueError("IQ window must be one-dimensional and at least 16384 samples")
    if not np.all(np.isfinite(values)):
        raise ValueError("IQ samples must be finite")
    magnitude = np.abs(values)
    if float(np.max(magnitude)) <= 1e-12:
        raise ValueError("degenerate all-zero IQ window")

    # FM discrimination recovers the audio subcarrier.  A fixed bandpass rejects
    # RF carrier offset (DC after discrimination) before integer decimation.
    audio = np.angle(values[1:] * np.conjugate(values[:-1])).astype(np.float64)
    taps = firwin(
        129,
        (500.0, 3_000.0),
        pass_zero=False,
        fs=config.sample_rate_hz,
        window="hamming",
    )
    decimation = config.sample_rate_hz // config.audio_sample_rate_hz
    audio = lfilter(taps, [1.0], audio)[128::decimation]
    if audio.size < 2_048:
        raise ValueError("not enough discriminator audio after filtering")
    audio -= float(np.median(audio))
    scale = float(np.quantile(np.abs(audio), 0.90))
    if not np.isfinite(scale) or scale <= 1e-12:
        raise ValueError("degenerate discriminator audio")
    audio /= scale

    tone_length = max(4, int(round(config.audio_sample_rate_hz / config.baudrate)))
    mark = _moving_tone_energy(audio, config.mark_hz, config.audio_sample_rate_hz, tone_length)
    space = _moving_tone_energy(audio, config.space_hz, config.audio_sample_rate_hz, tone_length)
    soft = mark - space
    soft = soft[2 * tone_length :]
    soft_scale = float(np.quantile(np.abs(soft), 0.90))
    if not np.isfinite(soft_scale) or soft_scale <= 1e-12:
        raise ValueError("degenerate Bell-202 energy stream")
    return np.asarray(soft / soft_scale, dtype=np.float32)


def _flag_offsets(bits: np.ndarray) -> np.ndarray:
    if bits.size < 8:
        return np.empty(0, dtype=np.int64)
    return np.flatnonzero(
        (bits[:-7] == 0)
        & (bits[1:-6] == 1)
        & (bits[2:-5] == 1)
        & (bits[3:-4] == 1)
        & (bits[4:-3] == 1)
        & (bits[5:-2] == 1)
        & (bits[6:-1] == 1)
        & (bits[7:] == 0)
    )


def _crc_frames_from_levels(levels: np.ndarray) -> tuple[bytes, ...]:
    """Vectorized flag search followed by exact HDLC and X.25 validation."""

    checked = np.asarray(levels, dtype=np.uint8)
    if checked.ndim != 1 or np.any(checked > 1):
        raise ValueError("levels must be a one-dimensional binary array")
    if checked.size < 16:
        return ()
    # Only the very first decoded bit depends on the assumed previous NRZI
    # level.  All complete flag-delimited regions later in a window are shared.
    bits = np.empty_like(checked)
    bits[0] = 0
    bits[1:] = (checked[1:] == checked[:-1]).astype(np.uint8)
    flags = _flag_offsets(bits)
    maximum_unstuffed_bits = (AX25_MAX_DECODER_FRAME_BYTES - 1) * 8
    maximum_stuffed_bits = maximum_unstuffed_bits + (maximum_unstuffed_bits + 4) // 5
    frames: list[bytes] = []
    seen: set[bytes] = set()
    for left, right in zip(flags[:-1], flags[1:], strict=True):
        region = bits[int(left) + 8 : int(right)]
        if region.size == 0 or region.size > maximum_stuffed_bits:
            continue
        try:
            unstuffed = hdlc_unstuff(tuple(int(value) for value in region))
            frame = bits_to_bytes(unstuffed, lsb_first=True)
        except (ValueError, RuntimeError):
            continue
        if (
            len(frame) >= AX25_MIN_PAYLOAD_BYTES + 2
            and len(frame) < AX25_MAX_DECODER_FRAME_BYTES
            and validate_ax25_fcs(frame)
            and frame not in seen
        ):
            frames.append(frame)
            seen.add(frame)
    return tuple(frames)


def _timing_candidates(soft: np.ndarray, config: Afsk1200Config):
    """Rank the frozen clock bank using only two-cluster eye separation."""

    nominal_step = config.audio_sample_rate_hz / config.baudrate
    sample_indices = np.arange(soft.size, dtype=np.float64)
    candidates: list[tuple[float, float, float, int, np.ndarray]] = []
    for rate_error_ppm in sorted(set(config.timing_rate_errors_ppm)):
        step = nominal_step * (1.0 + rate_error_ppm * 1e-6)
        for phase_index in range(config.timing_phase_bins):
            phase = step * (phase_index + 0.5) / config.timing_phase_bins
            positions = phase + np.arange(16, int((soft.size - phase) / step) - 16) * step
            if positions.size < 128:
                continue
            symbols = np.interp(positions, sample_indices, soft)
            threshold = float(np.median(symbols))
            lower = symbols[symbols < threshold]
            upper = symbols[symbols >= threshold]
            if lower.size < 16 or upper.size < 16:
                score = float("-inf")
            else:
                lower_mean = float(lower.mean())
                upper_mean = float(upper.mean())
                threshold = 0.5 * (lower_mean + upper_mean)
                within = float(np.sqrt(0.5 * (lower.var() + upper.var()) + 1e-15))
                score = (upper_mean - lower_mean) / within
            levels = np.asarray(symbols >= threshold, dtype=np.uint8)
            candidates.append((score, rate_error_ppm, phase, phase_index, levels))
    candidates.sort(key=lambda item: (-item[0], abs(item[1]), item[1], item[2]))
    return tuple(candidates[: config.timing_top_n])


def decode_afsk1200_iq(
    iq: Sequence[complex],
    *,
    config: Afsk1200Config = Afsk1200Config(),
    window_start_sample: int = 0,
) -> AfskDecodeResult:
    """Decode one bounded IQ window and return only strict AX.25 UI frames."""

    try:
        soft = bell202_soft_discriminator(iq, config)
    except ValueError as error:
        if "degenerate" not in str(error):
            raise
        return AfskDecodeResult((), 1, 0, 0, 0, 1)

    accepted: dict[bytes, AfskStrictFrame] = {}
    rejected_frames: dict[bytes, AfskRejectedFrame] = {}
    hypothesis_count = 0
    crc_count = 0
    rejected = 0
    for _score, rate_error_ppm, _phase, phase_index, levels in _timing_candidates(soft, config):
        for polarity, candidate_levels in (
            ("normal", levels),
            ("inverted", 1 - levels),
        ):
            hypothesis_count += 1
            for frame in _crc_frames_from_levels(candidate_levels):
                crc_count += 1
                normalized = frame[:-2]
                parsed = parse_ax25_ui(normalized)
                if parsed is None:
                    rejected += 1
                    rejected_frames.setdefault(
                        frame,
                        AfskRejectedFrame(
                            frame_with_fcs=frame,
                            rejection_reason="strict_parse_ax25_ui_rejected_after_fcs",
                            window_start_sample=window_start_sample,
                            rate_error_ppm=rate_error_ppm,
                            phase_index=phase_index,
                            polarity=polarity,
                        ),
                    )
                    if (
                        len(accepted) + len(rejected_frames)
                        > config.max_candidate_records
                    ):
                        raise ValueError("AFSK candidate record bound exceeded")
                    continue
                if normalized in accepted:
                    continue
                accepted[normalized] = AfskStrictFrame(
                    frame_with_fcs=frame,
                    normalized_pdu=normalized,
                    ax25=parsed,
                    inner_ccsds=parse_ccsds_space_packet(
                        parsed.information, require_exact_length=True
                    ),
                    window_start_sample=window_start_sample,
                    rate_error_ppm=rate_error_ppm,
                    phase_index=phase_index,
                    polarity=polarity,
                )
                if len(accepted) + len(rejected_frames) > config.max_candidate_records:
                    raise ValueError("AFSK candidate record bound exceeded")
    return AfskDecodeResult(
        frames=tuple(accepted[key] for key in sorted(accepted)),
        windows_examined=1,
        timing_hypotheses_examined=hypothesis_count,
        crc_valid_candidates=crc_count,
        strict_ax25_rejections=rejected,
        degenerate_windows=0,
        rejected_frames=tuple(
            rejected_frames[key] for key in sorted(rejected_frames)
        ),
    )


def _close_memmap(value: object) -> None:
    """Release a bounded memmap immediately instead of retaining file pages."""

    mapping = getattr(value, "_mmap", None)
    if mapping is not None:
        mapping.close()


def _mapped_ci16_pairs(path: Path, start: int, stop: int):
    return np.memmap(
        path,
        dtype="<i2",
        mode="r",
        offset=start * 4,
        shape=(stop - start, 2),
    )


def _permuted_window(
    source: Path,
    total_samples: int,
    start: int,
    stop: int,
    *,
    seed: int,
    block_samples: int,
) -> np.ndarray:
    output = np.empty(stop - start, dtype=np.complex64)
    first_block = start // block_samples
    final_block = (stop - 1) // block_samples
    for block_index in range(first_block, final_block + 1):
        block_start = block_index * block_samples
        block_stop = min(block_start + block_samples, total_samples)
        pairs = _mapped_ci16_pairs(source, block_start, block_stop)
        try:
            local_pairs = np.asarray(pairs, dtype=np.float32)
        finally:
            _close_memmap(pairs)
        block = np.asarray(
            local_pairs[:, 0] + 1j * local_pairs[:, 1], dtype=np.complex64
        )
        rng = np.random.default_rng(np.random.SeedSequence((seed, block_index)))
        permuted = block[rng.permutation(block.size)]
        copy_start = max(start, block_start)
        copy_stop = min(stop, block_stop)
        output[copy_start - start : copy_stop - start] = permuted[
            copy_start - block_start : copy_stop - block_start
        ]
    return output


def _complex_window(iq_pairs, start: int, stop: int) -> np.ndarray:
    """Materialize only one bounded CI16 pair window as complex64."""

    local_pairs = np.asarray(iq_pairs[start:stop], dtype=np.float32)
    if local_pairs.ndim != 2 or local_pairs.shape != (stop - start, 2):
        raise ValueError("CI16 memmap slice has an unexpected shape")
    return np.asarray(
        local_pairs[:, 0] + 1j * local_pairs[:, 1], dtype=np.complex64
    )


def ci16le_window_spans(
    path: str | Path,
    *,
    config: Afsk1200Config = Afsk1200Config(),
    start_sample: int = 0,
    sample_count: int | None = None,
) -> tuple[tuple[int, int], ...]:
    """Validate one CI16-LE segment and return deterministic absolute spans."""

    source = Path(path)
    if not source.is_file():
        raise ValueError("CI16-LE input must be a regular file")
    size = source.stat().st_size
    if size <= 0 or size % 4:
        raise ValueError("CI16-LE input must be non-empty whole complex samples")
    if (
        isinstance(start_sample, bool)
        or not isinstance(start_sample, int)
        or start_sample < 0
    ):
        raise ValueError("start_sample must be a non-negative integer")
    total_samples = size // 4
    if start_sample >= total_samples:
        raise ValueError("start_sample is outside the IQ file")
    if sample_count is None:
        selected_count = total_samples - start_sample
    else:
        if (
            isinstance(sample_count, bool)
            or not isinstance(sample_count, int)
            or sample_count <= 0
        ):
            raise ValueError("sample_count must be a positive integer or None")
        selected_count = sample_count
    segment_stop = start_sample + selected_count
    if segment_stop > total_samples:
        raise ValueError("requested CI16-LE segment extends beyond the file")
    if selected_count < 16_384:
        raise ValueError("requested CI16-LE segment is too short")

    window_samples = config.window_blocks * config.permutation_block_samples
    stride_samples = config.stride_blocks * config.permutation_block_samples
    relative_starts = list(
        range(0, max(1, selected_count - window_samples + 1), stride_samples)
    )
    final_relative_start = max(0, selected_count - window_samples)
    if not relative_starts or relative_starts[-1] != final_relative_start:
        relative_starts.append(final_relative_start)
    return tuple(
        (start_sample + relative, min(segment_stop, start_sample + relative + window_samples))
        for relative in relative_starts
        if min(segment_stop, start_sample + relative + window_samples)
        - (start_sample + relative)
        >= 16_384
    )


def iter_afsk1200_ci16le_windows(
    path: str | Path,
    *,
    config: Afsk1200Config = Afsk1200Config(),
    control: ControlMode = "identity",
    permutation_seed: int = 20_260_902,
    start_sample: int = 0,
    sample_count: int | None = None,
    skip_start_samples: Sequence[int] = (),
) -> Iterator[tuple[int, int, np.ndarray]]:
    """Yield bounded complex64 windows without materializing the full file."""

    if control not in ("identity", "all_zero_iq", "deterministic_sample_permutation"):
        raise ValueError("unsupported control mode")
    spans = ci16le_window_spans(
        path,
        config=config,
        start_sample=start_sample,
        sample_count=sample_count,
    )
    skipped = set(skip_start_samples)
    if any(
        isinstance(value, bool) or not isinstance(value, int) or value < 0
        for value in skipped
    ):
        raise ValueError("skip_start_samples must contain non-negative integers")
    known_starts = {start for start, _ in spans}
    if not skipped <= known_starts:
        raise ValueError("skip_start_samples contains a non-window boundary")

    source = Path(path)
    total_samples = source.stat().st_size // 4
    for start, stop in spans:
        if start in skipped:
            continue
        if control == "all_zero_iq":
            window = np.zeros(stop - start, dtype=np.complex64)
        elif control == "deterministic_sample_permutation":
            window = _permuted_window(
                source,
                total_samples,
                start,
                stop,
                seed=permutation_seed,
                block_samples=config.permutation_block_samples,
            )
        else:
            pairs = _mapped_ci16_pairs(source, start, stop)
            try:
                window = _complex_window(pairs, 0, stop - start)
            finally:
                _close_memmap(pairs)
        yield start, stop, window


def decode_afsk1200_ci16le(
    path: str | Path,
    *,
    config: Afsk1200Config = Afsk1200Config(),
    control: ControlMode = "identity",
    permutation_seed: int = 20_260_902,
    start_sample: int = 0,
    sample_count: int | None = None,
) -> AfskDecodeResult:
    """Decode a CI16-LE capture in deterministic overlapping bounded windows."""

    if control not in ("identity", "all_zero_iq", "deterministic_sample_permutation"):
        raise ValueError("unsupported control mode")
    spans = ci16le_window_spans(
        path,
        config=config,
        start_sample=start_sample,
        sample_count=sample_count,
    )

    # The zero control has a result fixed by construction.  Record every
    # window as exercised and degenerate without allocating overlapping zero
    # arrays proportional to the capture duration.
    if control == "all_zero_iq":
        examined = len(spans)
        return AfskDecodeResult((), examined, 0, 0, 0, examined)

    frames: dict[bytes, AfskStrictFrame] = {}
    rejected_frames: dict[bytes, AfskRejectedFrame] = {}
    totals = {"windows": 0, "hypotheses": 0, "crc": 0, "rejected": 0, "degenerate": 0}
    for start, _stop, window in iter_afsk1200_ci16le_windows(
        path,
        config=config,
        control=control,
        permutation_seed=permutation_seed,
        start_sample=start_sample,
        sample_count=sample_count,
    ):
        result = decode_afsk1200_iq(window, config=config, window_start_sample=start)
        totals["windows"] += result.windows_examined
        totals["hypotheses"] += result.timing_hypotheses_examined
        totals["crc"] += result.crc_valid_candidates
        totals["rejected"] += result.strict_ax25_rejections
        totals["degenerate"] += result.degenerate_windows
        for frame in result.frames:
            frames.setdefault(frame.normalized_pdu, frame)
        for frame in result.rejected_frames:
            rejected_frames.setdefault(frame.frame_with_fcs, frame)
        if len(frames) + len(rejected_frames) > config.max_candidate_records:
            raise ValueError("AFSK candidate record bound exceeded")
    return AfskDecodeResult(
        frames=tuple(frames[key] for key in sorted(frames)),
        windows_examined=totals["windows"],
        timing_hypotheses_examined=totals["hypotheses"],
        crc_valid_candidates=totals["crc"],
        strict_ax25_rejections=totals["rejected"],
        degenerate_windows=totals["degenerate"],
        rejected_frames=tuple(
            rejected_frames[key] for key in sorted(rejected_frames)
        ),
    )


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
