"""Additive, reference-free clock diversity for offline receiver searches.

The original global top-N bank is retained exactly. Optional per-rate,
phase-separated hypotheses extend it; they never replace the original bank.
This is a development extension, not a change to any frozen experiment.
"""
from __future__ import annotations

import math
from collections.abc import Sequence

from .clock_recovery import TimingHypothesis, recover_timing_hypotheses, soft_symbols_with_hypothesis
from .generic_receiver import DemodulatedSignal, FrameCandidate, GenericReceiver, ReceiverResult, WaveformHypothesis


def additive_diverse_bank(
    ranked: Sequence[TimingHypothesis], *, global_top: int, per_rate: int = 2,
    min_phase_distance: float = 0.20,
) -> tuple[TimingHypothesis, ...]:
    """Retain old top-N, then add up to per_rate separated clocks per rate.

    Phase distance is circular and expressed as a fraction of a symbol.
    Selection uses only the existing eye-score ordering and clock metadata.
    The maximum size is global_top + per_rate * number_of_rates.
    """
    for value in (global_top, per_rate):
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ValueError("clock budgets must be positive integers")
    if not math.isfinite(min_phase_distance) or not 0 <= min_phase_distance <= 0.5:
        raise ValueError("phase distance must lie between zero and half a symbol")
    for item in ranked:
        if not (math.isfinite(item.rate_error_ppm) and math.isfinite(item.phase_samples)
                and math.isfinite(item.step_samples) and item.step_samples > 0):
            raise ValueError("clock metadata must be finite with a positive step")
    selected = list(ranked[:global_top])
    seen = {(h.rate_error_ppm, h.phase_samples, h.step_samples) for h in selected}
    for rate in sorted({h.rate_error_ppm for h in ranked}, key=lambda r: (abs(r), r)):
        representatives = []
        for hypothesis in ranked:
            if hypothesis.rate_error_ppm != rate:
                continue
            phase = (hypothesis.phase_samples / hypothesis.step_samples) % 1.0
            if any(min(abs(phase - previous), 1 - abs(phase - previous)) < min_phase_distance
                   for previous in representatives):
                continue
            representatives.append(phase)
            key = (hypothesis.rate_error_ppm, hypothesis.phase_samples, hypothesis.step_samples)
            if key not in seen:
                selected.append(hypothesis)
                seen.add(key)
            if len(representatives) == per_rate:
                break
    return tuple(selected)


class DiverseTimingReceiver(GenericReceiver):
    """GenericReceiver with an additive timing-bank policy, same protocols."""

    def __init__(self, *, per_rate: int = 2, min_phase_distance: float = 0.20):
        super().__init__()
        # Validate policy even when the first input is silent.
        additive_diverse_bank((), global_top=1, per_rate=per_rate,
                              min_phase_distance=min_phase_distance)
        self.per_rate = per_rate
        self.min_phase_distance = min_phase_distance

    def decode_demodulated(self, signal: DemodulatedSignal, *, waveform: WaveformHypothesis,
                           protocol_id: str) -> ReceiverResult:
        decoder = self._protocols.get(protocol_id)
        if decoder is None:
            raise KeyError(f"unknown protocol adapter: {protocol_id}")
        if signal.symbol_kind not in self._protocol_capabilities[protocol_id].accepted_symbol_kinds:
            raise ValueError("signal symbol kind is incompatible with protocol")
        bank_size = len(set(waveform.rate_errors_ppm)) * waveform.phase_bins
        ranked = recover_timing_hypotheses(
            signal.samples, nominal_samples_per_symbol=signal.samples_per_symbol,
            rate_errors_ppm=waveform.rate_errors_ppm, phase_bins=waveform.phase_bins,
            top_n=bank_size,
        )
        hypotheses = additive_diverse_bank(
            ranked, global_top=waveform.top_timing_hypotheses, per_rate=self.per_rate,
            min_phase_distance=self.min_phase_distance,
        )
        frames = []
        seen = set()
        for rank, timing in enumerate(hypotheses):
            soft = soft_symbols_with_hypothesis(signal.samples, timing)
            for payload, validation in decoder.decode(soft, threshold=timing.threshold):
                key = (payload, protocol_id)
                if key not in seen:
                    seen.add(key)
                    frames.append(FrameCandidate(
                        payload=payload, protocol_id=protocol_id,
                        waveform_hypothesis_id=waveform.hypothesis_id,
                        timing_rank=rank, timing_score=timing.score, validation=validation,
                    ))
        return ReceiverResult(tuple(frames), 1, len(hypotheses), ())
