"""High-recall selection of interesting IQ regions.

This module deliberately separates *ranking* a fixed IQ window from deciding
which windows are expensive enough to send to downstream receivers.  A future
ML model can implement :class:`CandidateScorer`; the bundled
:class:`DeterministicEnergyScorer` is only a dependency-free fallback heuristic
and must not be described as AI or as a calibrated signal detector.

Times are relative to the beginning of the recording.  Frequencies carry an
explicit reference label so a baseband offset is never confused with an
absolute RF frequency.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, field
from typing import Mapping, Protocol, Sequence, runtime_checkable


def _finite(name: str, value: float) -> float:
    value = float(value)
    if not math.isfinite(value):
        raise ValueError(f"{name} must be finite")
    return value


def _non_empty(name: str, value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value


@dataclass(frozen=True, slots=True)
class TimeHint:
    """A scorer's estimate of the active subinterval within an IQ window."""

    start_seconds: float
    end_seconds: float

    def __post_init__(self) -> None:
        start = _finite("start_seconds", self.start_seconds)
        end = _finite("end_seconds", self.end_seconds)
        if start < 0 or end <= start:
            raise ValueError("time hint must have 0 <= start_seconds < end_seconds")
        object.__setattr__(self, "start_seconds", start)
        object.__setattr__(self, "end_seconds", end)


@dataclass(frozen=True, slots=True)
class FrequencyHint:
    """A frequency interval with an explicit coordinate reference."""

    lower_hz: float
    upper_hz: float
    reference: str = "baseband_offset"

    def __post_init__(self) -> None:
        lower = _finite("lower_hz", self.lower_hz)
        upper = _finite("upper_hz", self.upper_hz)
        if upper < lower:
            raise ValueError("upper_hz must be greater than or equal to lower_hz")
        object.__setattr__(self, "lower_hz", lower)
        object.__setattr__(self, "upper_hz", upper)
        _non_empty("reference", self.reference)


@dataclass(frozen=True, slots=True)
class IQWindow:
    """One bounded, scorer-ready IQ window.

    ``samples`` may be a tuple, a memory-mapped slice, or another sequence of
    complex-like values.  Keeping it on the window makes scorer plugins simple;
    selection results do not retain it.
    """

    window_id: str
    start_seconds: float
    end_seconds: float
    samples: Sequence[complex] = field(repr=False, compare=False)

    def __post_init__(self) -> None:
        _non_empty("window_id", self.window_id)
        start = _finite("start_seconds", self.start_seconds)
        end = _finite("end_seconds", self.end_seconds)
        if start < 0 or end <= start:
            raise ValueError("IQ window must have 0 <= start_seconds < end_seconds")
        if not hasattr(self.samples, "__len__"):
            raise TypeError("samples must be a sized sequence")
        object.__setattr__(self, "start_seconds", start)
        object.__setattr__(self, "end_seconds", end)


@dataclass(frozen=True, slots=True)
class WindowScore:
    """Normalized scorer output plus optional localization hints.

    ``probability`` is the common interchange scale in ``[0, 1]``.  Individual
    plugins remain responsible for calibration and should disclose it in
    ``metadata``.  The fallback energy scorer explicitly reports that its value
    is not calibrated.
    """

    probability: float
    time_hint: TimeHint | None = None
    frequency_hint: FrequencyHint | None = None
    waveform_hints: tuple[str, ...] = ()
    metadata: Mapping[str, str | int | float | bool | None] = field(
        default_factory=dict
    )

    def __post_init__(self) -> None:
        probability = _finite("probability", self.probability)
        if not 0 <= probability <= 1:
            raise ValueError("probability must be in [0, 1]")
        hints = tuple(self.waveform_hints)
        if any(not isinstance(hint, str) or not hint.strip() for hint in hints):
            raise ValueError("waveform hints must be non-empty strings")
        if len(hints) != len(set(hints)):
            raise ValueError("waveform hints must be unique")
        if not isinstance(self.metadata, Mapping):
            raise TypeError("metadata must be a mapping")
        object.__setattr__(self, "probability", probability)
        object.__setattr__(self, "waveform_hints", hints)


@runtime_checkable
class CandidateScorer(Protocol):
    """Plugin contract for ranking IQ windows.

    A plugin may wrap an ML model, a conventional detector, or an ensemble.  It
    must be deterministic when its declared identity/configuration is the same,
    unless that limitation is explicitly recorded outside this interface.
    """

    name: str
    version: str

    def score(self, window: IQWindow) -> WindowScore:
        """Return one normalized score and any localization/class hints."""


@runtime_checkable
class BatchCandidateScorer(CandidateScorer, Protocol):
    """Optional accelerated contract for model/GPU-backed scorer plugins."""

    def score_many(self, windows: Sequence[IQWindow]) -> Sequence[WindowScore]:
        """Score a batch, preserving input order and length."""


@dataclass(frozen=True, slots=True)
class HighRecallPolicy:
    """Selection policy biased toward false positives over missed signals.

    Threshold and top-k are combined with OR: a window is retained when it
    clears ``probability_threshold`` *or* belongs to the global top-k.  The
    latter rescues weak windows when all scores are low.  Padding protects
    preambles and tails, while deterministic audits quantify what the selector
    rejected.
    """

    probability_threshold: float = 0.05
    top_k: int | None = 32
    padding_before_seconds: float = 0.25
    padding_after_seconds: float = 0.25
    merge_gap_seconds: float = 0.0
    rejected_audit_count: int = 16
    audit_seed: str = "telemetry-yield-candidate-audit-v1"

    def __post_init__(self) -> None:
        threshold = _finite("probability_threshold", self.probability_threshold)
        if not 0 <= threshold <= 1:
            raise ValueError("probability_threshold must be in [0, 1]")
        if self.top_k is not None and (
            isinstance(self.top_k, bool)
            or not isinstance(self.top_k, int)
            or self.top_k < 0
        ):
            raise ValueError("top_k must be None or a non-negative integer")
        for name in (
            "padding_before_seconds",
            "padding_after_seconds",
            "merge_gap_seconds",
        ):
            value = _finite(name, getattr(self, name))
            if value < 0:
                raise ValueError(f"{name} must be non-negative")
            object.__setattr__(self, name, value)
        if (
            isinstance(self.rejected_audit_count, bool)
            or not isinstance(self.rejected_audit_count, int)
            or self.rejected_audit_count < 0
        ):
            raise ValueError("rejected_audit_count must be a non-negative integer")
        _non_empty("audit_seed", self.audit_seed)
        object.__setattr__(self, "probability_threshold", threshold)


@dataclass(frozen=True, slots=True)
class ScoredWindow:
    """Audit-friendly scorer output without the potentially large IQ samples."""

    window_id: str
    window_start_seconds: float
    window_end_seconds: float
    hinted_start_seconds: float
    hinted_end_seconds: float
    probability: float
    frequency_hint: FrequencyHint | None
    waveform_hints: tuple[str, ...]
    metadata: Mapping[str, str | int | float | bool | None]
    selected: bool
    selection_reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CandidateRegion:
    """A padded, possibly merged region sent to expensive downstream stages."""

    start_seconds: float
    end_seconds: float
    peak_probability: float
    source_window_ids: tuple[str, ...]
    selection_reasons: tuple[str, ...]
    frequency_hints: tuple[FrequencyHint, ...]
    waveform_hints: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CandidateSelectionResult:
    scorer_name: str
    scorer_version: str
    policy: HighRecallPolicy
    scored_windows: tuple[ScoredWindow, ...]
    candidates: tuple[CandidateRegion, ...]
    audited_rejections: tuple[ScoredWindow, ...]


@dataclass(frozen=True, slots=True)
class DeterministicEnergyScorer:
    """Dependency-free energy heuristic for smoke tests and safe fallback.

    The score ``1 - exp(-mean_power / reference_power)`` is monotonic and
    bounded, but it is *not* a calibrated probability and it is not AI.
    """

    reference_power: float = 1.0
    name: str = field(default="deterministic-energy", init=False)
    version: str = field(default="1", init=False)

    def __post_init__(self) -> None:
        reference = _finite("reference_power", self.reference_power)
        if reference <= 0:
            raise ValueError("reference_power must be positive")
        object.__setattr__(self, "reference_power", reference)

    def score(self, window: IQWindow) -> WindowScore:
        if len(window.samples) == 0:
            raise ValueError("energy scorer cannot score an empty IQ window")

        def sample_powers():
            for sample in window.samples:
                value = complex(sample)
                power = value.real * value.real + value.imag * value.imag
                if not math.isfinite(power):
                    raise ValueError("IQ samples must have finite power")
                yield power

        mean_power = math.fsum(sample_powers()) / len(window.samples)
        probability = -math.expm1(-mean_power / self.reference_power)
        return WindowScore(
            probability=probability,
            metadata={
                "detector": "mean_energy",
                "mean_power": mean_power,
                "reference_power": self.reference_power,
                "calibrated_probability": False,
                "ai_model": False,
            },
        )

    def score_many(self, windows: Sequence[IQWindow]) -> tuple[WindowScore, ...]:
        return tuple(self.score(window) for window in windows)


def select_candidate_regions(
    windows: Sequence[IQWindow],
    scorer: CandidateScorer,
    *,
    policy: HighRecallPolicy | None = None,
    recording_duration_seconds: float | None = None,
) -> CandidateSelectionResult:
    """Score windows, select with high recall, pad/merge, and audit rejects."""

    selected_policy = policy or HighRecallPolicy()
    scorer_name = _non_empty("scorer.name", scorer.name)
    scorer_version = _non_empty("scorer.version", scorer.version)
    if recording_duration_seconds is not None:
        duration = _finite("recording_duration_seconds", recording_duration_seconds)
        if duration <= 0:
            raise ValueError("recording_duration_seconds must be positive")
    else:
        duration = None

    window_values = tuple(windows)
    ids = [window.window_id for window in window_values]
    if len(ids) != len(set(ids)):
        raise ValueError("window_id values must be unique within one selection run")
    if duration is not None and any(window.end_seconds > duration for window in window_values):
        raise ValueError("IQ window extends beyond recording_duration_seconds")

    if isinstance(scorer, BatchCandidateScorer):
        score_values = tuple(scorer.score_many(window_values))
        if len(score_values) != len(window_values):
            raise ValueError("scorer.score_many() must preserve input length")
    else:
        score_values = tuple(scorer.score(window) for window in window_values)

    raw_scores: list[tuple[IQWindow, WindowScore, float, float]] = []
    for window, score in zip(window_values, score_values, strict=True):
        if not isinstance(score, WindowScore):
            raise TypeError("scorer must return WindowScore values")
        if score.time_hint is None:
            hinted_start = window.start_seconds
            hinted_end = window.end_seconds
        else:
            hinted_start = score.time_hint.start_seconds
            hinted_end = score.time_hint.end_seconds
            if (
                hinted_start < window.start_seconds
                or hinted_end > window.end_seconds
            ):
                raise ValueError(
                    f"time hint for {window.window_id!r} lies outside its IQ window"
                )
        raw_scores.append((window, score, hinted_start, hinted_end))

    top_ids: set[str] = set()
    if selected_policy.top_k:
        ranked = sorted(
            raw_scores,
            key=lambda item: (
                -item[1].probability,
                item[0].window_id,
                item[0].start_seconds,
            ),
        )
        top_ids = {
            item[0].window_id for item in ranked[: selected_policy.top_k]
        }

    scored: list[ScoredWindow] = []
    for window, score, hinted_start, hinted_end in raw_scores:
        reasons: list[str] = []
        if score.probability >= selected_policy.probability_threshold:
            reasons.append("threshold")
        if window.window_id in top_ids:
            reasons.append("top_k")
        scored.append(
            ScoredWindow(
                window_id=window.window_id,
                window_start_seconds=window.start_seconds,
                window_end_seconds=window.end_seconds,
                hinted_start_seconds=hinted_start,
                hinted_end_seconds=hinted_end,
                probability=score.probability,
                frequency_hint=score.frequency_hint,
                waveform_hints=score.waveform_hints,
                metadata=dict(score.metadata),
                selected=bool(reasons),
                selection_reasons=tuple(reasons),
            )
        )

    candidates = _merge_selected(scored, selected_policy, duration)
    rejected = [item for item in scored if not item.selected]
    audited = tuple(
        sorted(rejected, key=lambda item: _audit_key(item, selected_policy.audit_seed))[
            : selected_policy.rejected_audit_count
        ]
    )
    return CandidateSelectionResult(
        scorer_name=scorer_name,
        scorer_version=scorer_version,
        policy=selected_policy,
        scored_windows=tuple(scored),
        candidates=candidates,
        audited_rejections=audited,
    )


def _audit_key(item: ScoredWindow, seed: str) -> tuple[bytes, str]:
    identity = (
        f"{seed}\0{item.window_id}\0{item.window_start_seconds:.17g}"
        f"\0{item.window_end_seconds:.17g}"
    ).encode("utf-8")
    return hashlib.sha256(identity).digest(), item.window_id


def _merge_selected(
    scored: Sequence[ScoredWindow],
    policy: HighRecallPolicy,
    duration: float | None,
) -> tuple[CandidateRegion, ...]:
    pending: list[CandidateRegion] = []
    for item in scored:
        if not item.selected:
            continue
        start = max(0.0, item.hinted_start_seconds - policy.padding_before_seconds)
        end = item.hinted_end_seconds + policy.padding_after_seconds
        if duration is not None:
            end = min(duration, end)
        pending.append(
            CandidateRegion(
                start_seconds=start,
                end_seconds=end,
                peak_probability=item.probability,
                source_window_ids=(item.window_id,),
                selection_reasons=item.selection_reasons,
                frequency_hints=(item.frequency_hint,) if item.frequency_hint else (),
                waveform_hints=item.waveform_hints,
            )
        )

    pending.sort(key=lambda item: (item.start_seconds, item.end_seconds, item.source_window_ids))
    merged: list[CandidateRegion] = []
    for item in pending:
        if not merged or item.start_seconds > merged[-1].end_seconds + policy.merge_gap_seconds:
            merged.append(item)
            continue
        previous = merged[-1]
        merged[-1] = CandidateRegion(
            start_seconds=previous.start_seconds,
            end_seconds=max(previous.end_seconds, item.end_seconds),
            peak_probability=max(previous.peak_probability, item.peak_probability),
            source_window_ids=tuple(
                sorted(set(previous.source_window_ids) | set(item.source_window_ids))
            ),
            selection_reasons=tuple(
                sorted(set(previous.selection_reasons) | set(item.selection_reasons))
            ),
            frequency_hints=tuple(
                sorted(
                    set(previous.frequency_hints) | set(item.frequency_hints),
                    key=lambda hint: (hint.reference, hint.lower_hz, hint.upper_hz),
                )
            ),
            waveform_hints=tuple(
                sorted(set(previous.waveform_hints) | set(item.waveform_hints))
            ),
        )
    return tuple(merged)
