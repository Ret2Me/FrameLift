"""Bounded, mission-neutral binary FSK/GFSK/GMSK to AX.25 receiver.

This production plugin is the packaged counterpart of the frozen real-IQ
transfer receiver.  It treats CRC-valid but structurally invalid AX.25 frames
as rejected candidates and never promotes them to telemetry.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import math
from pathlib import Path
from typing import Iterator, Sequence

import numpy as np

from .ax25_validation import AX25UIFrame, parse_ax25_ui
from .clock_recovery import (
    deterministic_fsk_demodulate,
    extract_valid_ax25_frames,
    recover_timing_hypotheses,
    slice_with_hypothesis,
)
from .crc import validate_ax25_fcs


@dataclass(frozen=True, slots=True)
class FskAx25Config:
    """Finite search and streaming limits for one FSK-family route."""

    sample_rate_hz: int = 48_000
    baudrate: float = 2_400.0
    decimation: int = 8
    g3ruh: bool = False
    window_seconds: float = 5.0
    hop_seconds: float = 2.5
    timing_rate_errors_ppm: tuple[float, ...] = (
        -3_000.0,
        -1_500.0,
        -750.0,
        0.0,
        750.0,
        1_500.0,
        3_000.0,
    )
    timing_phase_bins: int = 32
    timing_top_n: int = 32
    max_candidate_records: int = 20_000

    def __post_init__(self) -> None:
        if (
            isinstance(self.sample_rate_hz, bool)
            or not isinstance(self.sample_rate_hz, int)
            or self.sample_rate_hz <= 0
        ):
            raise ValueError("sample_rate_hz must be a positive integer")
        if (
            isinstance(self.baudrate, bool)
            or not isinstance(self.baudrate, (int, float))
            or not math.isfinite(float(self.baudrate))
            or self.baudrate <= 0
        ):
            raise ValueError("baudrate must be finite and positive")
        if (
            isinstance(self.decimation, bool)
            or not isinstance(self.decimation, int)
            or self.decimation < 1
            or self.sample_rate_hz % self.decimation
        ):
            raise ValueError("decimation must be positive and divide sample_rate_hz")
        if not isinstance(self.g3ruh, bool):
            raise TypeError("g3ruh must be boolean")
        if any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
            or value <= 0
            for value in (self.window_seconds, self.hop_seconds)
        ):
            raise ValueError("window_seconds and hop_seconds must be finite and positive")
        if self.hop_seconds > self.window_seconds:
            raise ValueError("hop_seconds must not exceed window_seconds")
        if not self.timing_rate_errors_ppm or any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
            for value in self.timing_rate_errors_ppm
        ):
            raise ValueError("timing_rate_errors_ppm must be finite and non-empty")
        integer_limits = (
            self.timing_phase_bins,
            self.timing_top_n,
            self.max_candidate_records,
        )
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value < 1
            for value in integer_limits
        ):
            raise ValueError("timing and candidate limits must be positive integers")
        if self.timing_phase_bins < 4:
            raise ValueError("timing_phase_bins must be at least four")
        if self.window_samples < 2:
            raise ValueError("window duration is too short")

    @property
    def window_samples(self) -> int:
        return int(round(self.window_seconds * self.sample_rate_hz))

    @property
    def hop_samples(self) -> int:
        return int(round(self.hop_seconds * self.sample_rate_hz))


@dataclass(frozen=True, slots=True)
class FskStrictFrame:
    frame_with_fcs: bytes
    normalized_pdu: bytes
    ax25: AX25UIFrame
    window_start_sample: int
    timing_rank: int
    rate_error_ppm: float
    phase_samples: float
    step_samples: float
    threshold: float


@dataclass(frozen=True, slots=True)
class FskRejectedFrame:
    frame_with_fcs: bytes
    rejection_reason: str
    window_start_sample: int
    timing_rank: int
    rate_error_ppm: float
    phase_samples: float
    step_samples: float
    threshold: float


@dataclass(frozen=True, slots=True)
class FskDecodeResult:
    frames: tuple[FskStrictFrame, ...]
    rejected_frames: tuple[FskRejectedFrame, ...]
    timing_hypotheses_examined: int
    crc_valid_candidates: int
    carrier_offset_hz: float
    normalization_scale: float
    degenerate: bool = False
    failure: str | None = None


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _decode_levels_vectorized(levels: Sequence[int], *, g3ruh: bool) -> tuple[bytes, ...]:
    received = np.asarray(levels, dtype=np.uint8)
    if received.ndim != 1 or np.any(received > 1):
        raise ValueError("levels must be a one-dimensional binary sequence")
    frames: dict[bytes, None] = {}
    for initial_level in (0, 1):
        decoded = np.empty_like(received)
        if decoded.size:
            decoded[0] = received[0] == initial_level
            decoded[1:] = received[1:] == received[:-1]
        if g3ruh:
            nrzi = decoded.copy()
            decoded[12:] ^= nrzi[:-12]
            decoded[17:] ^= nrzi[:-17]
        for frame in extract_valid_ax25_frames(decoded.tolist()):
            frames.setdefault(frame, None)
    return tuple(frames)


def decode_fsk_ax25_iq(
    iq: Sequence[complex],
    *,
    config: FskAx25Config = FskAx25Config(),
    window_start_sample: int = 0,
) -> FskDecodeResult:
    """Decode one bounded IQ window and retain complete candidate provenance."""

    if (
        isinstance(window_start_sample, bool)
        or not isinstance(window_start_sample, int)
        or window_start_sample < 0
    ):
        raise ValueError("window_start_sample must be a non-negative integer")
    samples = np.asarray(iq, dtype=np.complex128)
    if samples.ndim != 1 or samples.size < 2:
        raise ValueError("iq must be a one-dimensional complex sequence")
    if not np.all(np.isfinite(samples)):
        raise ValueError("iq contains non-finite samples")
    # A sample-exact constant complex stream carries no phase increments and
    # therefore cannot contain binary FSK information.  Reject it before the
    # FIR/channel-estimation path so long no-signal captures stay inexpensive
    # without introducing an amplitude-dependent heuristic.
    if np.all(samples == samples[0]):
        return FskDecodeResult(
            frames=(),
            rejected_frames=(),
            timing_hypotheses_examined=0,
            crc_valid_candidates=0,
            carrier_offset_hz=0.0,
            normalization_scale=0.0,
            degenerate=True,
            failure=None,
        )
    try:
        demodulated, metrics = deterministic_fsk_demodulate(
            samples,
            sample_rate_hz=config.sample_rate_hz,
            baudrate=config.baudrate,
            decimation=config.decimation,
        )
    except ValueError as error:
        return FskDecodeResult(
            frames=(),
            rejected_frames=(),
            timing_hypotheses_examined=0,
            crc_valid_candidates=0,
            carrier_offset_hz=0.0,
            normalization_scale=0.0,
            degenerate=True,
            failure=str(error),
        )
    hypotheses = recover_timing_hypotheses(
        demodulated,
        nominal_samples_per_symbol=(
            config.sample_rate_hz / (config.decimation * config.baudrate)
        ),
        rate_errors_ppm=config.timing_rate_errors_ppm,
        phase_bins=config.timing_phase_bins,
        top_n=config.timing_top_n,
    )
    strict: dict[bytes, FskStrictFrame] = {}
    rejected: dict[bytes, FskRejectedFrame] = {}
    for rank, hypothesis in enumerate(hypotheses):
        levels = slice_with_hypothesis(demodulated, hypothesis)
        for frame in _decode_levels_vectorized(levels, g3ruh=config.g3ruh):
            if not validate_ax25_fcs(frame):
                raise RuntimeError("FSK decoder violated its FCS contract")
            payload = frame[:-2]
            parsed = parse_ax25_ui(payload)
            if parsed is None:
                rejected.setdefault(
                    frame,
                    FskRejectedFrame(
                        frame_with_fcs=frame,
                        rejection_reason="strict_ax25_ui_structure_failed",
                        window_start_sample=window_start_sample,
                        timing_rank=rank,
                        rate_error_ppm=float(hypothesis.rate_error_ppm),
                        phase_samples=float(hypothesis.phase_samples),
                        step_samples=float(hypothesis.step_samples),
                        threshold=float(hypothesis.threshold),
                    ),
                )
            else:
                strict.setdefault(
                    frame,
                    FskStrictFrame(
                        frame_with_fcs=frame,
                        normalized_pdu=payload,
                        ax25=parsed,
                        window_start_sample=window_start_sample,
                        timing_rank=rank,
                        rate_error_ppm=float(hypothesis.rate_error_ppm),
                        phase_samples=float(hypothesis.phase_samples),
                        step_samples=float(hypothesis.step_samples),
                        threshold=float(hypothesis.threshold),
                    ),
                )
    return FskDecodeResult(
        frames=tuple(strict.values()),
        rejected_frames=tuple(rejected.values()),
        timing_hypotheses_examined=len(hypotheses),
        crc_valid_candidates=len(strict) + len(rejected),
        carrier_offset_hz=float(metrics.carrier_offset_hz),
        normalization_scale=float(metrics.normalization_scale),
    )


def ci16le_window_spans(
    path: str | Path,
    *,
    config: FskAx25Config = FskAx25Config(),
    start_sample: int = 0,
    sample_count: int | None = None,
) -> tuple[tuple[int, int], ...]:
    source = Path(path)
    if not source.is_file():
        raise ValueError("CI16 IQ source must be a regular file")
    size = source.stat().st_size
    if size <= 0 or size % 4:
        raise ValueError("CI16 IQ size must be positive and divisible by four")
    total_samples = size // 4
    if isinstance(start_sample, bool) or not isinstance(start_sample, int) or start_sample < 0:
        raise ValueError("start_sample must be a non-negative integer")
    if sample_count is None:
        stop_sample = total_samples
    else:
        if (
            isinstance(sample_count, bool)
            or not isinstance(sample_count, int)
            or sample_count <= 0
        ):
            raise ValueError("sample_count must be positive or None")
        stop_sample = start_sample + sample_count
    if start_sample >= total_samples or stop_sample > total_samples:
        raise ValueError("requested segment lies outside the CI16 source")
    if stop_sample - start_sample < config.window_samples:
        raise ValueError("requested segment is shorter than one FSK window")
    starts = list(
        range(
            start_sample,
            stop_sample - config.window_samples + 1,
            config.hop_samples,
        )
    )
    final_start = stop_sample - config.window_samples
    if starts[-1] != final_start:
        starts.append(final_start)
    return tuple((start, start + config.window_samples) for start in starts)


def iter_fsk_ax25_ci16le_windows(
    path: str | Path,
    *,
    config: FskAx25Config = FskAx25Config(),
    start_sample: int = 0,
    sample_count: int | None = None,
    skip_start_samples: Sequence[int] = (),
) -> Iterator[tuple[int, int, np.ndarray]]:
    spans = ci16le_window_spans(
        path,
        config=config,
        start_sample=start_sample,
        sample_count=sample_count,
    )
    skipped = set(skip_start_samples)
    if not skipped <= {start for start, _ in spans}:
        raise ValueError("skip_start_samples contains an unknown boundary")
    source = Path(path)
    for start, stop in spans:
        if start in skipped:
            continue
        pairs = np.memmap(
            source,
            dtype="<i2",
            mode="r",
            offset=start * 4,
            shape=(stop - start, 2),
        )
        try:
            local = np.asarray(pairs, dtype=np.float64)
        finally:
            mapping = getattr(pairs, "_mmap", None)
            if mapping is not None:
                mapping.close()
        yield start, stop, local[:, 0] + 1j * local[:, 1]
