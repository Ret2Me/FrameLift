"""An opt-in, non-neural mixture of reception-history rules.

Expert forecasts are immutable. Weights see only feedback that has actually
arrived, including revisions/retractions. This module never downloads data,
fits a classifier, selects a method on test outcomes, or submits station jobs.
"""
from __future__ import annotations

import heapq
import math
from dataclasses import dataclass
from datetime import datetime
from typing import Mapping

from .live_history import utc

HISTORY_EXPERTS = (
    "last10_2", "hierarchical7_2", "hierarchical30_2",
    "hierarchical90_2", "last50_2", "link_last10_2",
)


@dataclass(frozen=True)
class IssuedForecast:
    key: str
    issued_at: datetime
    pass_start: datetime
    pass_end: datetime
    probabilities: tuple[float, ...]
    weights: tuple[float, ...]
    probability: float
    feedback_count: int


class HistoryExpertMixer:
    """Discounted exponential weights using squared probability errors.

    Defaults are a single declared candidate, not a hyperparameter search.
    Half-life measures time since reception, not upload time: relabeling an old
    observation must not make it recent. We make no empirical superiority or
    delayed-feedback regret guarantee for these particular settings.
    """

    def __init__(self, experts=HISTORY_EXPERTS, *, half_life_days=30., learning_rate=2.,
                 loss="brier", prior=None):
        self.experts = tuple(experts)
        if (not self.experts or any(not isinstance(e, str) or not e for e in self.experts)
                or len(set(self.experts)) != len(self.experts)):
            raise ValueError("distinct nonempty expert names required")
        for value in (half_life_days, learning_rate):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
                raise ValueError("positive finite mixture settings required")
        self.half_life_seconds = half_life_days * 86400
        self.learning_rate = learning_rate
        if loss not in ("brier", "log"):
            raise ValueError("unknown mixture loss")
        self.loss = loss
        prior = dict.fromkeys(self.experts, 1.) if prior is None else dict(prior)
        if set(prior) != set(self.experts) or any(
            isinstance(p, bool) or not isinstance(p, (int, float)) or not math.isfinite(p) or p <= 0
            for p in prior.values()
        ):
            raise ValueError("exact positive finite expert prior required")
        total = math.fsum(prior.values())
        self.prior = tuple(prior[e] / total for e in self.experts)
        self.forecasts: dict[str, IssuedForecast] = {}
        self._outcomes: dict[str, int] = {}
        self._events: dict[tuple[str, datetime], int | None] = {}
        self._pending: list[tuple[datetime, str]] = []
        self._loss = [0.] * len(self.experts)
        self._time: datetime | None = None

    def _discount(self, seconds):
        return math.exp(-math.log(2) * seconds / self.half_life_seconds)

    def _score(self, probability, outcome):
        if self.loss == "brier":
            return (probability - outcome) ** 2
        p = min(1 - 1e-12, max(1e-12, probability))
        return -math.log(p) if outcome else -math.log1p(-p)

    def _advance(self, as_of):
        as_of = utc(as_of)
        if self._time is not None:
            if as_of < self._time:
                raise ValueError("mixture issue times must be chronological")
            decay = self._discount((as_of - self._time).total_seconds())
            self._loss = [loss * decay for loss in self._loss]
        while self._pending and self._pending[0][0] <= as_of:
            available_at, key = heapq.heappop(self._pending)
            forecast = self.forecasts[key]
            age = self._discount((as_of - forecast.pass_end).total_seconds())
            previous = self._outcomes.pop(key, None)
            outcome = self._events[(key, available_at)]
            for i, p in enumerate(forecast.probabilities):
                if previous is not None:
                    self._loss[i] -= age * self._score(p, previous)
                if outcome is not None:
                    self._loss[i] += age * self._score(p, outcome)
                # Roundoff from a subtraction after a correction/retraction.
                self._loss[i] = max(0., self._loss[i])
            if outcome is not None:
                self._outcomes[key] = outcome
        self._time = as_of

    def weights(self, *, as_of):
        self._advance(as_of)
        logits = [math.log(p) - self.learning_rate * loss
                  for p, loss in zip(self.prior, self._loss, strict=True)]
        best = max(logits)
        raw = [math.exp(value - best) for value in logits]
        total = math.fsum(raw)
        return dict(zip(self.experts, (v / total for v in raw), strict=True))

    def issue(self, key: str, probabilities: Mapping[str, float], *, issued_at, pass_start, pass_end):
        issued_at, pass_start, pass_end = map(utc, (issued_at, pass_start, pass_end))
        if not isinstance(key, str) or not key or not issued_at < pass_start < pass_end:
            raise ValueError("a key and a strictly future valid pass are required")
        if set(probabilities) != set(self.experts):
            raise ValueError("exact declared expert panel required")
        values = tuple(probabilities[e] for e in self.experts)
        if any(isinstance(p, bool) or not isinstance(p, (int, float)) or not math.isfinite(p) or not 0 <= p <= 1 for p in values):
            raise ValueError("finite expert probabilities in [0,1] required")
        if key in self.forecasts:
            old = self.forecasts[key]
            if (old.issued_at, old.pass_start, old.pass_end, old.probabilities) != (issued_at, pass_start, pass_end, values):
                raise ValueError("issued forecasts cannot be overwritten")
            return old
        weights = tuple(self.weights(as_of=issued_at).values())
        probability = min(1., max(0., math.fsum(w * p for w, p in zip(weights, values, strict=True))))
        forecast = IssuedForecast(key, issued_at, pass_start, pass_end, values, weights,
                                  probability, len(self._outcomes))
        self.forecasts[key] = forecast
        return forecast

    def feedback(self, key, outcome, *, available_at):
        """Register a receipt. None retracts a label; unknown is never failure.

        Repeated identical receipts are idempotent; conflicting labels at the
        same timestamp are rejected. Future receipts may be queued in a replay
        but are never used before their declared availability time.
        """
        available_at = utc(available_at)
        if key not in self.forecasts:
            raise ValueError("feedback requires a previously issued forecast")
        if outcome is not None and (type(outcome) is not int or outcome not in (0, 1)):
            raise ValueError("binary label or explicit retraction required")
        if available_at < self.forecasts[key].pass_end:
            raise ValueError("feedback cannot precede reception end")
        identity = (key, available_at)
        if identity in self._events:
            if self._events[identity] != outcome:
                raise ValueError("conflicting feedback at the same receipt time")
            return
        if self._time is not None and available_at < self._time:
            raise ValueError("backdated feedback must be replayed from its receipt time")
        self._events[identity] = outcome
        heapq.heappush(self._pending, identity[::-1])
