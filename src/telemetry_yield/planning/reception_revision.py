"""Opt-in delayed-feedback reception mixtures and conservative calibration.

Literature-informed engineering candidates, not a reproduction of MarBLR or a
claim of its regret guarantees. Only issued forecasts may receive feedback.
The caller must persist/replay receipts and explicitly choose a candidate;
this module never promotes itself or alters the registered campaign.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from .history_ensemble import HISTORY_EXPERTS, HistoryExpertMixer

CANDIDATES = ("uniform_history", "dynamic_history", "context_history",
              "dynamic_hybrid", "calibrated_hybrid")
CALIBRATION_MAPS = {f"a{a}_b{b}": (a, b) for a in (-.5, 0., .5) for b in (.75, 1., 1.25)}
IDENTITY_MAP = "a0.0_b1.0"


def calibration_probabilities(probability):
    if isinstance(probability, bool) or not isinstance(probability, (int, float)) or not math.isfinite(probability) or not 0 <= probability <= 1:
        raise ValueError("finite probability in [0,1] required")
    p = min(1 - 1e-12, max(1e-12, probability))
    logit = math.log(p) - math.log1p(-p)
    return {name: (probability if name == IDENTITY_MAP else 1 / (1 + math.exp(-(a + b * logit))))
            for name, (a, b) in CALIBRATION_MAPS.items()}


@dataclass(frozen=True)
class RevisedForecast:
    key: str
    probability: float
    base_probability: float
    global_feedback_count: int
    local_feedback_count: int
    expert_weights: tuple[tuple[str, float], ...]


class ReceptionRevision:
    """Fixed 30-day discount, no hyperparameter fitting or automatic selection.

    Context history shrinks a pair of satellite/station mixtures towards the
    global one, with 20 observed feedback records of prior strength per group.
    Calibration averages nine fixed logistic maps, with half the prior mass
    on the identity, using discounted log loss. The result is NOT a posterior
    credible interval or a proof of calibration.
    """

    def __init__(self, method):
        if method not in CANDIDATES:
            raise ValueError("unknown revision candidate")
        self.method = method
        self.experts = HISTORY_EXPERTS + (("hgb_recent",) if "hybrid" in method else ())
        self.global_mixer = HistoryExpertMixer(self.experts)
        self.local_mixers = {}
        self.groups = {}
        self.forecasts = {}
        self.calibrator = HistoryExpertMixer(tuple(CALIBRATION_MAPS), learning_rate=1., loss="log",
            prior={name: .5 if name == IDENTITY_MAP else .5 / 8 for name in CALIBRATION_MAPS})

    def issue(self, key, probabilities, *, norad_id, station_id, issued_at, pass_start, pass_end):
        if any(type(value) is not int or value <= 0 for value in (norad_id, station_id)):
            raise ValueError("positive integer satellite and station identities required")
        groups = (("satellite", norad_id), ("station", station_id))
        if key in self.groups and self.groups[key] != groups:
            raise ValueError("issued forecast group cannot change")
        times = dict(issued_at=issued_at, pass_start=pass_start, pass_end=pass_end)
        forecast = self.global_mixer.issue(key, probabilities, **times)
        if key in self.forecasts:
            return self.forecasts[key]
        self.groups[key] = groups
        weights = dict(zip(self.experts, forecast.weights, strict=True))
        p = forecast.probability
        local_count = 0
        if self.method == "uniform_history":
            weights = dict.fromkeys(self.experts, 1 / len(self.experts))
            p = math.fsum(probabilities.values()) / len(self.experts)
        elif self.method == "context_history":
            combined = dict.fromkeys(self.experts, 0.)
            for group in groups:
                mixer = self.local_mixers.setdefault(group, HistoryExpertMixer(self.experts))
                local = mixer.issue(key, probabilities, **times)
                local_count += local.feedback_count
                share = local.feedback_count / (20 + local.feedback_count)
                for name, local_weight in zip(self.experts, local.weights, strict=True):
                    combined[name] += .5 * ((1 - share) * weights[name] + share * local_weight)
            weights = combined
            p = math.fsum(weights[e] * probabilities[e] for e in self.experts)
        base = p
        if self.method == "calibrated_hybrid":
            p = self.calibrator.issue(key, calibration_probabilities(p), **times).probability
        result = RevisedForecast(key, p, base, forecast.feedback_count, local_count, tuple(weights.items()))
        self.forecasts[key] = result
        return result

    def feedback(self, key, outcome, *, available_at):
        # Global validation first ensures invalid/duplicate receipts cannot
        # partially mutate the subordinate models.
        self.global_mixer.feedback(key, outcome, available_at=available_at)
        if self.method == "context_history":
            for group in self.groups[key]:
                self.local_mixers[group].feedback(key, outcome, available_at=available_at)
        if self.method == "calibrated_hybrid":
            self.calibrator.feedback(key, outcome, available_at=available_at)
