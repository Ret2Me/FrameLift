"""Blind clipping-robust phase FSK receiver for headerless CI16 captures.

The receiver deliberately separates reference-free discovery from protocol
validation.  Candidate windows are selected from signal statistics, symbol
clocks are ranked by an unsupervised eye score, frame starts come from repeated
HDLC flags, and output is admitted only after full HDLC, CRC-16/X-25, and AX.25
UI validation.  No reference payload, event timestamp, callsign, or expected
frame length is accepted by this module.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
import math
from pathlib import Path
from typing import Sequence

from .clock_recovery import (
    TimingHypothesis,
    recover_timing_hypotheses,
    soft_symbols_with_hypothesis,
)
from .soft_sync import (
    SoftDecodedFrame,
    SoftListBudget,
    preamble_terminated_frame_starts,
    protocol_constrained_syndrome_decode,
)
from .symbol_boundary import decode_satnogs_symbols


@dataclass(frozen=True, slots=True)
class BlindPhaseFskConfig:
    """Hard bounds and signal assumptions for one deterministic replay."""

    sample_rate_hz: int = 57_600
    baudrate: float = 9_600.0
    analysis_window_seconds: float = 0.5
    decoder_window_seconds: float = 2.75
    decoder_window_lead_seconds: float = 1.25
    candidate_window_limit: int = 32
    window_nms_seconds: float = 2.0
    constant_radius_factors: tuple[float, ...] = (3.0,)
    phase_difference_lags: tuple[int, ...] = (1,)
    descramble_modes: tuple[bool, ...] = (False, True)
    rate_errors_ppm: tuple[float, ...] = (0.0,)
    phase_bins: int = 64
    short_search_timing_hypotheses: int = 16
    deep_search_timing_hypotheses: int = 16
    minimum_consecutive_flags: int = 4
    minimum_frame_body_bits: int = 128
    short_least_reliable_symbols: int = 64
    short_maximum_flips: int = 2
    short_maximum_attempts: int = 20_000
    deep_least_reliable_symbols: int = 64
    deep_maximum_flips: int = 5
    deep_maximum_attempts: int = 400_000
    maximum_regions_per_start: int = 8
    repair_path_maximum_attempts: int = 100_000
    repair_path_maximum_unique_frames: int = 8
    repair_region_maximum_attempts: int = 50_000
    repair_event_maximum_attempts: int = 400_000
    repair_event_maximum_unique_frames: int = 8
    repair_window_maximum_events: int = 16
    repair_window_maximum_attempts: int = 1_600_000
    repair_window_maximum_unique_frames: int = 16
    event_cluster_tolerance_symbols: int = 16
    candidate_neighbor_radius: int = 1
    error_unit_penalty: float = 0.05
    maximum_map_seed_states: int = 512
    maximum_receiver_paths_per_window: int = 20_000
    maximum_frame_detections: int = 20_000

    def __post_init__(self) -> None:
        if self.sample_rate_hz <= 0 or self.baudrate <= 0:
            raise ValueError("sample rate and baudrate must be positive")
        if self.analysis_window_seconds <= 0 or self.decoder_window_seconds <= 0:
            raise ValueError("window durations must be positive")
        if not 0 <= self.decoder_window_lead_seconds < self.decoder_window_seconds:
            raise ValueError("decoder window lead is outside the window")
        if self.candidate_window_limit < 1 or self.window_nms_seconds < 0:
            raise ValueError("invalid candidate-window limits")
        if (
            not self.constant_radius_factors
            or min(self.constant_radius_factors) < 1
        ):
            raise ValueError("constant radius factors must be at least one")
        if not self.phase_difference_lags or min(self.phase_difference_lags) < 1:
            raise ValueError("phase-difference lags must be positive")
        if (
            not self.descramble_modes
            or any(type(mode) is not bool for mode in self.descramble_modes)
            or len(set(self.descramble_modes)) != len(self.descramble_modes)
        ):
            raise ValueError("descramble modes must be unique booleans")
        if self.sample_rate_hz / self.baudrate < 2.0:
            raise ValueError("sample rate must provide at least two samples per symbol")
        if not self.rate_errors_ppm or self.phase_bins < 4:
            raise ValueError("timing bank must not be empty")
        bank_size = len(set(self.rate_errors_ppm)) * self.phase_bins
        if not 1 <= self.short_search_timing_hypotheses <= bank_size:
            raise ValueError("short timing budget exceeds the timing bank")
        if not 1 <= self.deep_search_timing_hypotheses <= bank_size:
            raise ValueError("deep timing budget exceeds the timing bank")
        if self.minimum_consecutive_flags < 2 or self.minimum_frame_body_bits < 1:
            raise ValueError("invalid preamble constraints")
        if self.maximum_regions_per_start < 1:
            raise ValueError("maximum_regions_per_start must be positive")
        if min(
            self.repair_path_maximum_attempts,
            self.repair_path_maximum_unique_frames,
            self.repair_region_maximum_attempts,
            self.repair_event_maximum_attempts,
            self.repair_event_maximum_unique_frames,
            self.repair_window_maximum_events,
            self.repair_window_maximum_attempts,
            self.repair_window_maximum_unique_frames,
            self.event_cluster_tolerance_symbols,
            self.maximum_receiver_paths_per_window,
            self.maximum_frame_detections,
        ) < 1:
            raise ValueError("repair scheduler limits must be positive")
        if self.repair_path_maximum_attempts > self.repair_event_maximum_attempts:
            raise ValueError("repair path budget exceeds event budget")
        if self.repair_event_maximum_attempts > self.repair_window_maximum_attempts:
            raise ValueError("repair event budget exceeds window budget")
        if self.maximum_map_seed_states < 1:
            raise ValueError("maximum_map_seed_states must be positive")


@dataclass(frozen=True, slots=True)
class PhaseWindowCandidate:
    analysis_index: int
    analysis_start_seconds: float
    decoder_start_seconds: float
    mean_power: float
    endpoint_clip_fraction: float
    lag1_phase_coherence: float
    score: float


@dataclass(frozen=True, slots=True)
class ClippingRobustAx25Frame:
    payload: bytes
    fcs: bytes
    decoder_start_seconds: float
    estimated_frame_start_seconds: float
    constant_radius_factor: float
    phase_difference_lag: int
    g3ruh_descramble: bool
    timing: TimingHypothesis
    frame_start_symbol: int
    frame_stop_symbol: int
    flipped_symbol_indices: tuple[int, ...]
    flipped_symbol_reliabilities: tuple[float, ...]
    search_stage: str
    attempted_candidates: int


@dataclass(frozen=True, slots=True)
class BlindPhaseFskResult:
    input_path: str
    input_sha256: str
    input_complex_samples: int
    input_duration_seconds: float
    selected_windows: tuple[PhaseWindowCandidate, ...]
    decoded_windows: int
    timing_hypotheses_examined: int
    protocol_candidates_attempted: int
    native_protocol_candidates_attempted: int
    repair_protocol_candidates_attempted: int
    frames: tuple[ClippingRobustAx25Frame, ...]


@dataclass(frozen=True, slots=True)
class _BlindReceiverPath:
    """One reference-free clock/line-code path for a detected preamble."""

    constant_radius_factor: float
    phase_difference_lag: int
    g3ruh_descramble: bool
    timing_rank: int
    timing: TimingHypothesis
    soft_symbols: tuple[float, ...]
    frame_start_symbol: int
    estimated_frame_start_seconds: float


@dataclass(frozen=True, slots=True)
class Ax25FrameConsensus:
    payload: bytes
    fcs: bytes
    estimated_frame_start_seconds: float
    detections: tuple[ClippingRobustAx25Frame, ...]
    independent_frontends: tuple[tuple[float, int], ...]
    g3ruh_descramble_modes: tuple[bool, ...]
    has_uncorrected_detection: bool
    has_correction_consensus: bool
    native_trusted: bool


def group_ax25_frame_consensus(
    detections: Sequence[ClippingRobustAx25Frame],
    *,
    time_tolerance_seconds: float = 0.025,
) -> tuple[Ax25FrameConsensus, ...]:
    """Group detections and require frontend consensus for repaired frames."""

    if time_tolerance_seconds < 0:
        raise ValueError("time_tolerance_seconds must be non-negative")
    groups: list[list[ClippingRobustAx25Frame]] = []
    for detection in sorted(
        detections,
        key=lambda item: (
            item.estimated_frame_start_seconds,
            item.payload,
            item.constant_radius_factor,
            item.phase_difference_lag,
            item.g3ruh_descramble,
        ),
    ):
        for group in groups:
            if (
                group[0].payload == detection.payload
                and abs(
                    group[0].estimated_frame_start_seconds
                    - detection.estimated_frame_start_seconds
                )
                <= time_tolerance_seconds
            ):
                group.append(detection)
                break
        else:
            groups.append([detection])

    output: list[Ax25FrameConsensus] = []
    for group in groups:
        ordered = tuple(
            sorted(
                group,
                key=lambda item: (
                    item.constant_radius_factor,
                    item.phase_difference_lag,
                    item.g3ruh_descramble,
                    item.timing.rate_error_ppm,
                    item.timing.phase_samples,
                ),
            )
        )
        frontends = tuple(
            sorted(
                {
                    (
                        item.constant_radius_factor,
                        item.phase_difference_lag,
                    )
                    for item in ordered
                }
            )
        )
        descramble_modes = tuple(
            sorted({item.g3ruh_descramble for item in group})
        )
        uncorrected = any(not item.flipped_symbol_indices for item in ordered)
        correction_consensus = len(frontends) >= 2
        output.append(
            Ax25FrameConsensus(
                payload=ordered[0].payload,
                fcs=ordered[0].fcs,
                estimated_frame_start_seconds=sum(
                    item.estimated_frame_start_seconds for item in ordered
                )
                / len(ordered),
                detections=ordered,
                independent_frontends=frontends,
                g3ruh_descramble_modes=descramble_modes,
                has_uncorrected_detection=uncorrected,
                has_correction_consensus=correction_consensus,
                # Multiple phase-difference lags reuse the same clipped
                # samples and are correlated.  Their agreement is useful
                # diagnostic evidence but cannot independently authenticate a
                # list-repaired frame after a large 16-bit CRC search.
                native_trusted=uncorrected,
            )
        )
    return tuple(
        sorted(
            output,
            key=lambda item: (
                item.estimated_frame_start_seconds,
                item.payload,
            ),
        )
    )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _lower_baseline(values, fraction: float, np) -> float:
    count = max(1, int(math.ceil(len(values) * fraction)))
    result = float(np.median(np.partition(values, count - 1)[:count]))
    if not math.isfinite(result) or result <= 0:
        raise ValueError("signal statistics have no finite positive baseline")
    return result


def select_ci16_phase_windows(
    path: str | Path,
    *,
    config: BlindPhaseFskConfig | None = None,
) -> tuple[PhaseWindowCandidate, ...]:
    """Rank non-overlapping CI16 windows using signal statistics only."""

    import numpy as np

    selected = config or BlindPhaseFskConfig()
    source = Path(path)
    if source.stat().st_size <= 0 or source.stat().st_size % 4:
        raise ValueError("CI16 input must contain complete non-empty I/Q pairs")
    scalars = np.memmap(source, dtype="<i2", mode="r")
    pairs = scalars.reshape(-1, 2)
    analysis_samples = int(
        round(selected.sample_rate_hz * selected.analysis_window_seconds)
    )
    window_count = len(pairs) // analysis_samples
    if window_count < 2:
        raise ValueError("capture must contain at least two analysis windows")

    powers = np.empty(window_count, dtype=np.float64)
    clips = np.empty(window_count, dtype=np.float64)
    coherences = np.empty(window_count, dtype=np.float64)
    for index in range(window_count):
        start = index * analysis_samples
        raw = np.asarray(pairs[start : start + analysis_samples])
        values = raw.astype(np.float64)
        i_values = values[:, 0]
        q_values = values[:, 1]
        powers[index] = float(np.mean(i_values * i_values + q_values * q_values))
        clips[index] = float(
            np.mean((raw == np.int16(-32768)) | (raw == np.int16(32767)))
        )
        real = i_values[1:] * i_values[:-1] + q_values[1:] * q_values[:-1]
        imag = q_values[1:] * i_values[:-1] - i_values[1:] * q_values[:-1]
        magnitude_sum = float(np.sum(np.hypot(real, imag), dtype=np.float64))
        coherences[index] = (
            float(math.hypot(float(np.sum(real)), float(np.sum(imag))))
            / magnitude_sum
            if magnitude_sum > 0
            else 0.0
        )

    power_baseline = _lower_baseline(powers, 0.25, np)
    coherence_baseline = _lower_baseline(
        np.maximum(coherences, np.finfo(np.float64).eps), 0.25, np
    )
    clip_baseline = _lower_baseline(
        np.maximum(clips, np.finfo(np.float64).eps), 0.25, np
    )
    scores = np.maximum.reduce(
        (
            np.log1p(np.maximum(powers / power_baseline - 1.0, 0.0)),
            np.log1p(
                np.maximum(coherences / coherence_baseline - 1.0, 0.0)
            ),
            np.log1p(np.maximum(clips / clip_baseline - 1.0, 0.0)),
        )
    )
    duration = len(pairs) / selected.sample_rate_hz
    maximum_start = max(0.0, duration - selected.decoder_window_seconds)
    ranked = np.argsort(-scores, kind="stable")
    candidates: list[PhaseWindowCandidate] = []
    for index_value in ranked:
        index = int(index_value)
        analysis_start = index * selected.analysis_window_seconds
        center = analysis_start + 0.5 * selected.analysis_window_seconds
        if any(
            abs(
                center
                - (
                    candidate.analysis_start_seconds
                    + 0.5 * selected.analysis_window_seconds
                )
            )
            < selected.window_nms_seconds
            for candidate in candidates
        ):
            continue
        decoder_start = min(
            maximum_start,
            max(0.0, analysis_start - selected.decoder_window_lead_seconds),
        )
        candidates.append(
            PhaseWindowCandidate(
                analysis_index=index,
                analysis_start_seconds=analysis_start,
                decoder_start_seconds=decoder_start,
                mean_power=float(powers[index]),
                endpoint_clip_fraction=float(clips[index]),
                lag1_phase_coherence=float(coherences[index]),
                score=float(scores[index]),
            )
        )
        if len(candidates) >= selected.candidate_window_limit:
            break
    return tuple(candidates)


def _read_ci16_window(
    path: Path,
    *,
    start_seconds: float,
    duration_seconds: float,
    sample_rate_hz: int,
):
    import numpy as np

    start_sample = int(round(start_seconds * sample_rate_hz))
    sample_count = int(round(duration_seconds * sample_rate_hz))
    raw = np.fromfile(
        path,
        dtype="<i2",
        count=2 * sample_count,
        offset=4 * start_sample,
    )
    if raw.size != 2 * sample_count:
        raise ValueError("decoder window extends beyond the CI16 capture")
    return raw.reshape(-1, 2)


def _constant_radius_declip_exact_endpoints(raw, factor: float):
    import numpy as np

    pairs = np.asarray(raw, dtype=np.float64).copy()
    saturated = (raw == np.int16(-32768)) | (raw == np.int16(32767))
    saturated_count = saturated.sum(axis=1)
    radius = factor * 32767.0
    one = saturated_count == 1
    for clipped_column, other_column in ((0, 1), (1, 0)):
        chosen = one & saturated[:, clipped_column]
        inferred = np.sqrt(
            np.maximum(
                radius * radius - pairs[chosen, other_column] ** 2,
                32767.0**2,
            )
        )
        pairs[chosen, clipped_column] = (
            np.sign(pairs[chosen, clipped_column]) * inferred
        )
    both = saturated_count == 2
    pairs[both] = np.sign(pairs[both]) * (radius / math.sqrt(2.0))
    return pairs[:, 0] + 1j * pairs[:, 1]


def _phase_first(iq, *, lag: int, config: BlindPhaseFskConfig):
    import numpy as np
    from scipy.signal import firwin, lfilter

    decimation, _ = _frontend_decimation_and_sps(config)
    increments = np.angle(iq[lag:] * np.conjugate(iq[:-lag])) / lag
    taps = firwin(
        115,
        cutoff=0.5 * config.baudrate,
        fs=config.sample_rate_hz,
        window="hamming",
    )
    filtered = lfilter(taps, [1.0], increments)[114::decimation]
    blocker_length = 1_024
    if filtered.size <= blocker_length:
        raise ValueError("decoder window is too short after phase discrimination")
    cumulative = np.concatenate(
        (np.zeros(1, dtype=np.float64), np.cumsum(filtered, dtype=np.float64))
    )
    trend = (
        cumulative[blocker_length:] - cumulative[:-blocker_length]
    ) / blocker_length
    output = filtered[blocker_length - 1 :] - trend
    output -= np.median(output)
    scale = float(np.quantile(np.abs(output), 0.90))
    if not math.isfinite(scale) or scale <= 1e-12:
        raise ValueError("degenerate phase-discriminator stream")
    return np.asarray(output / scale, dtype=np.float64)


def _frontend_decimation_and_sps(
    config: BlindPhaseFskConfig,
) -> tuple[int, float]:
    """Choose deterministic integer decimation while retaining at least 2 SPS."""

    source_sps = config.sample_rate_hz / config.baudrate
    if not math.isfinite(source_sps) or source_sps < 2.0:
        raise ValueError("sample rate must provide at least two samples per symbol")
    decimation = max(1, math.floor(source_sps / 2.0))
    nominal_sps = source_sps / decimation
    if nominal_sps < 2.0:
        raise AssertionError("frontend decimation violated the two-SPS floor")
    return decimation, nominal_sps


def _budget(config: BlindPhaseFskConfig, *, deep: bool) -> SoftListBudget:
    if not deep:
        raise ValueError("the short list-repair stage is reserved and disabled")
    return SoftListBudget(
        least_reliable_symbols=config.deep_least_reliable_symbols,
        maximum_flips=config.deep_maximum_flips,
        maximum_regions=config.maximum_regions_per_start,
        maximum_attempts=config.deep_maximum_attempts,
        maximum_attempts_per_region=config.repair_region_maximum_attempts,
        candidate_neighbor_radius=config.candidate_neighbor_radius,
        error_unit_penalty=config.error_unit_penalty,
        maximum_map_seed_states=config.maximum_map_seed_states,
        maximum_output_frames=config.repair_path_maximum_unique_frames,
    )


def _resolved(start: int, resolved_starts: Sequence[int]) -> bool:
    return any(abs(start - previous) <= 16 for previous in resolved_starts)


def _can_resolve_start(frame: SoftDecodedFrame) -> bool:
    """Only a strict hard-decision frame may suppress later receiver paths."""

    return not frame.flipped_symbol_indices


def _estimated_frame_start_seconds(
    *,
    decoder_start_seconds: float,
    frame_start_symbol: int,
    hypothesis: TimingHypothesis,
    lag: int,
    config: BlindPhaseFskConfig,
) -> float:
    decimation, _ = _frontend_decimation_and_sps(config)
    # FIR output starts at tap 114 and has group delay 57 samples.  The
    # blocker then discards 1023 decimated samples.  This formula preserves
    # the historical 3126-sample offset when decimation is three.
    preclock_zero_source_sample = 57.0 + 1_023.0 * decimation + lag / 2.0
    preclock_position = hypothesis.phase_samples + (
        32 + frame_start_symbol
    ) * hypothesis.step_samples
    return decoder_start_seconds + (
        preclock_zero_source_sample + decimation * preclock_position
    ) / config.sample_rate_hz


def _cluster_receiver_paths(
    paths: Sequence[_BlindReceiverPath],
    *,
    tolerance_seconds: float,
) -> tuple[tuple[_BlindReceiverPath, ...], ...]:
    """Group clock/front-end paths that describe the same physical preamble."""

    if tolerance_seconds < 0:
        raise ValueError("event tolerance must be non-negative")
    groups: list[list[_BlindReceiverPath]] = []
    for path in sorted(
        paths,
        key=lambda item: (
            item.estimated_frame_start_seconds,
            item.g3ruh_descramble,
            item.timing_rank,
            item.phase_difference_lag,
            item.constant_radius_factor,
            item.frame_start_symbol,
        ),
    ):
        for group in groups:
            center = sum(
                item.estimated_frame_start_seconds for item in group
            ) / len(group)
            if abs(center - path.estimated_frame_start_seconds) <= tolerance_seconds:
                group.append(path)
                break
        else:
            groups.append([path])
    return tuple(tuple(group) for group in groups)


def _repair_path_rank(path: _BlindReceiverPath) -> tuple[float, int, int, bool, int]:
    """Blind deterministic scheduling key; no payload property is consulted."""

    return (
        -path.timing.score,
        path.timing_rank,
        path.phase_difference_lag,
        path.g3ruh_descramble,
        path.frame_start_symbol,
    )


def _repair_frame_rank(
    frame: SoftDecodedFrame,
    *,
    error_unit_penalty: float,
) -> tuple[float, int, tuple[int, ...], bytes]:
    """Rank repaired outputs only by their frozen soft-decision search cost."""

    return (
        error_unit_penalty * len(frame.flipped_symbol_indices)
        + sum(frame.flipped_symbol_reliabilities),
        len(frame.flipped_symbol_indices),
        frame.flipped_symbol_indices,
        frame.frame_with_fcs,
    )


def decode_clipping_robust_ax25_ci16(
    path: str | Path,
    *,
    config: BlindPhaseFskConfig | None = None,
    selected_windows: Sequence[PhaseWindowCandidate] | None = None,
) -> BlindPhaseFskResult:
    """Run blind window, timing, preamble, syndrome, and AX.25 decoding."""

    import numpy as np

    chosen = config or BlindPhaseFskConfig()
    source = Path(path)
    windows = (
        tuple(selected_windows)
        if selected_windows is not None
        else select_ci16_phase_windows(source, config=chosen)
    )
    frames: list[ClippingRobustAx25Frame] = []
    hypotheses_examined = 0
    attempted = 0
    native_attempted = 0
    repair_attempted = 0
    decoded_windows = 0

    def append_frame(frame: ClippingRobustAx25Frame) -> None:
        """Append without ever transiently exceeding the configured hard cap."""

        if len(frames) >= chosen.maximum_frame_detections:
            raise ValueError("decoder frame detection bound exceeded")
        frames.append(frame)

    def materialize(
        receiver_path: _BlindReceiverPath,
        decoded: SoftDecodedFrame,
        *,
        stage: str,
        path_attempts: int,
        decoder_start_seconds: float,
    ) -> ClippingRobustAx25Frame:
        return ClippingRobustAx25Frame(
            payload=decoded.frame_with_fcs[:-2],
            fcs=decoded.frame_with_fcs[-2:],
            decoder_start_seconds=decoder_start_seconds,
            estimated_frame_start_seconds=_estimated_frame_start_seconds(
                decoder_start_seconds=decoder_start_seconds,
                frame_start_symbol=decoded.left_flag_bit,
                hypothesis=receiver_path.timing,
                lag=receiver_path.phase_difference_lag,
                config=chosen,
            ),
            constant_radius_factor=receiver_path.constant_radius_factor,
            phase_difference_lag=receiver_path.phase_difference_lag,
            g3ruh_descramble=receiver_path.g3ruh_descramble,
            timing=receiver_path.timing,
            frame_start_symbol=decoded.left_flag_bit,
            frame_stop_symbol=decoded.right_flag_bit,
            flipped_symbol_indices=decoded.flipped_symbol_indices,
            flipped_symbol_reliabilities=decoded.flipped_symbol_reliabilities,
            search_stage=stage,
            attempted_candidates=path_attempts,
        )

    for window in windows:
        raw = _read_ci16_window(
            source,
            start_seconds=window.decoder_start_seconds,
            duration_seconds=chosen.decoder_window_seconds,
            sample_rate_hz=chosen.sample_rate_hz,
        )
        decoded_windows += 1
        receiver_paths: list[_BlindReceiverPath] = []
        for radius_factor in chosen.constant_radius_factors:
            iq = _constant_radius_declip_exact_endpoints(raw, radius_factor)
            for lag in chosen.phase_difference_lags:
                preclock = _phase_first(iq, lag=lag, config=chosen)
                bank = recover_timing_hypotheses(
                    preclock,
                    nominal_samples_per_symbol=(
                        _frontend_decimation_and_sps(chosen)[1]
                    ),
                    rate_errors_ppm=chosen.rate_errors_ppm,
                    phase_bins=chosen.phase_bins,
                    top_n=max(
                        chosen.short_search_timing_hypotheses,
                        chosen.deep_search_timing_hypotheses,
                    ),
                )
                for timing_rank, hypothesis in enumerate(bank):
                    soft = soft_symbols_with_hypothesis(preclock, hypothesis)
                    levels = tuple(
                        int(value >= hypothesis.threshold) for value in soft
                    )
                    for descramble in chosen.descramble_modes:
                        hypotheses_examined += 1
                        plain = decode_satnogs_symbols(
                            levels, descramble=descramble
                        )
                        starts = preamble_terminated_frame_starts(
                            plain,
                            minimum_consecutive_flags=(
                                chosen.minimum_consecutive_flags
                            ),
                            minimum_body_bits=chosen.minimum_frame_body_bits,
                        )
                        for start in starts:
                            if (
                                len(receiver_paths)
                                >= chosen.maximum_receiver_paths_per_window
                            ):
                                raise ValueError(
                                    "receiver path bound exceeded before clustering"
                                )
                            receiver_paths.append(
                                _BlindReceiverPath(
                                    constant_radius_factor=radius_factor,
                                    phase_difference_lag=lag,
                                    g3ruh_descramble=descramble,
                                    timing_rank=timing_rank,
                                    timing=hypothesis,
                                    soft_symbols=soft,
                                    frame_start_symbol=start,
                                    estimated_frame_start_seconds=(
                                        _estimated_frame_start_seconds(
                                            decoder_start_seconds=(
                                                window.decoder_start_seconds
                                            ),
                                            frame_start_symbol=start,
                                            hypothesis=hypothesis,
                                            lag=lag,
                                            config=chosen,
                                        )
                                    ),
                                )
                            )

        events = sorted(
            _cluster_receiver_paths(
                receiver_paths,
                tolerance_seconds=(
                    chosen.event_cluster_tolerance_symbols / chosen.baudrate
                ),
            ),
            key=lambda group: (
                -len(group),
                -max(path.timing.score for path in group),
                min(path.estimated_frame_start_seconds for path in group),
            ),
        )
        native_budget = SoftListBudget(
            least_reliable_symbols=1,
            maximum_flips=0,
            maximum_regions=chosen.maximum_regions_per_start,
            maximum_attempts=chosen.maximum_regions_per_start,
            maximum_attempts_per_region=1,
            maximum_output_frames=1,
        )
        window_repaired: list[
            tuple[
                tuple[float, int, tuple[int, ...], bytes],
                ClippingRobustAx25Frame,
            ]
        ] = []
        window_repair_attempts = 0
        unresolved_events: list[tuple[_BlindReceiverPath, ...]] = []

        # Phase one is exhaustive over the frozen path bank but permits no
        # raw-decision repairs.  Therefore a later strict frame can never be
        # starved by earlier list-decoder work or a corrected CRC collision.
        for event_paths in events:
            ranked_paths = tuple(sorted(event_paths, key=_repair_path_rank))
            native_found = False
            for receiver_path in ranked_paths:
                native = protocol_constrained_syndrome_decode(
                    receiver_path.soft_symbols,
                    threshold=receiver_path.timing.threshold,
                    event_start_symbol=receiver_path.frame_start_symbol,
                    budget=native_budget,
                    require_ax25_ui=True,
                    stop_after_first_frame=True,
                    stop_after_uncorrected_frame=True,
                    descramble=receiver_path.g3ruh_descramble,
                )
                if native.output_limit_reached:
                    raise ValueError("native path output bound exceeded")
                attempted += native.attempted_candidates
                native_attempted += native.attempted_candidates
                for decoded in native.frames:
                    if not _can_resolve_start(decoded):
                        continue
                    append_frame(
                        materialize(
                            receiver_path,
                            decoded,
                            stage="native_all_paths",
                            path_attempts=native.attempted_candidates,
                            decoder_start_seconds=window.decoder_start_seconds,
                        )
                    )
                    native_found = True
                    break
                if native_found:
                    break
            if not native_found:
                unresolved_events.append(ranked_paths)

        # Phase two shares hard attempt and output budgets at event and window
        # scope.  Paths are ordered only by their unsupervised timing score.
        for ranked_paths in unresolved_events[
            : chosen.repair_window_maximum_events
        ]:
            event_attempts = 0
            native_found = False
            repaired_by_frame: dict[
                bytes,
                tuple[
                    tuple[float, int, tuple[int, ...], bytes],
                    ClippingRobustAx25Frame,
                ],
            ] = {}
            for receiver_path in ranked_paths:
                remaining = min(
                    chosen.repair_event_maximum_attempts - event_attempts,
                    chosen.repair_window_maximum_attempts
                    - window_repair_attempts,
                )
                if remaining <= 0:
                    break
                path_limit = min(
                    chosen.deep_maximum_attempts,
                    chosen.repair_path_maximum_attempts,
                    remaining,
                )
                repair_budget = replace(
                    _budget(chosen, deep=True),
                    maximum_attempts=path_limit,
                    maximum_attempts_per_region=min(
                        chosen.repair_region_maximum_attempts,
                        path_limit,
                    ),
                )
                repaired = protocol_constrained_syndrome_decode(
                    receiver_path.soft_symbols,
                    threshold=receiver_path.timing.threshold,
                    event_start_symbol=receiver_path.frame_start_symbol,
                    budget=repair_budget,
                    require_ax25_ui=True,
                    stop_after_first_frame=False,
                    stop_after_uncorrected_frame=True,
                    descramble=receiver_path.g3ruh_descramble,
                )
                if repaired.output_limit_reached:
                    raise ValueError("repair path output bound exceeded")
                event_attempts += repaired.attempted_candidates
                window_repair_attempts += repaired.attempted_candidates
                attempted += repaired.attempted_candidates
                repair_attempted += repaired.attempted_candidates
                for decoded in repaired.frames:
                    materialized = materialize(
                        receiver_path,
                        decoded,
                        stage="global_shared_repair",
                        path_attempts=repaired.attempted_candidates,
                        decoder_start_seconds=window.decoder_start_seconds,
                    )
                    if _can_resolve_start(decoded):
                        append_frame(materialized)
                        native_found = True
                        break
                    candidate_rank = _repair_frame_rank(
                        decoded,
                        error_unit_penalty=chosen.error_unit_penalty,
                    )
                    previous = repaired_by_frame.get(decoded.frame_with_fcs)
                    if previous is None or candidate_rank < previous[0]:
                        repaired_by_frame[decoded.frame_with_fcs] = (
                            candidate_rank,
                            materialized,
                        )
                if native_found:
                    break
            if native_found:
                continue
            ranked_repaired = sorted(
                repaired_by_frame.values(),
                key=lambda item: item[0],
            )
            window_repaired.extend(
                ranked_repaired[
                    : chosen.repair_event_maximum_unique_frames
                ]
            )
        for _, repaired_frame in sorted(window_repaired, key=lambda item: item[0])[
            : chosen.repair_window_maximum_unique_frames
        ]:
            append_frame(repaired_frame)

    size = source.stat().st_size
    return BlindPhaseFskResult(
        input_path=str(source.resolve()),
        input_sha256=_sha256_file(source),
        input_complex_samples=size // 4,
        input_duration_seconds=(size // 4) / chosen.sample_rate_hz,
        selected_windows=windows,
        decoded_windows=decoded_windows,
        timing_hypotheses_examined=hypotheses_examined,
        protocol_candidates_attempted=attempted,
        native_protocol_candidates_attempted=native_attempted,
        repair_protocol_candidates_attempted=repair_attempted,
        frames=tuple(frames),
    )
