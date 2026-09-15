"""History-first reception candidates with a shared, availability-gated context.

No network access or implicit training. Selection and reporting are separate:
March selects one challenger, June may reject it, and neither is a final test.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Mapping

import numpy as np

SCOPES = ("global", "sat", "station", "pair", "transmitter", "link", "similar", "station_band")
WINDOWS = ("7d", "30d", "90d", "last10", "last50")
LEGACY_RULES = ("global", "satellite", "station") + tuple(
    f"{family}_{strength}" for family in ("pair30", "pair90", "hierarchical30", "last10")
    for strength in (2, 10, 50))
EXTRA_RULES = ("hierarchical7_2", "hierarchical90_2", "last50_2", "link_last10_2")
RULES = LEGACY_RULES + EXTRA_RULES
ANCHOR = "last10_2"
BLENDS = {"blend25": .25, "blend50": .5, "blend75": .75}
CANDIDATES = RULES + ("hgb_recent",) + tuple(BLENDS)
PHYSICAL = ("max_elevation", "log_duration", "log_tle_age", "log_frequency", "log_baud",
            "rise_sin", "rise_cos", "set_sin", "set_cos", "hour_sin", "hour_cos",
            "year_sin", "year_cos", "mode_category")
FEATURES = PHYSICAL + tuple(f"{scope}_{part}" for scope in SCOPES
    for part in ("log_count", "rate", "log_age_days", *(
        f"{window}_{value}" for window in WINDOWS for value in ("log_count", "rate")))) + tuple(
    f"rule_{rule}" for rule in RULES)


def _counts(history, scope, window=None):
    if window is None:
        return history[f"{scope}_count"], history[f"{scope}_success"]
    n = history[f"{scope}_{window}_count"]
    # Undo the documented Beta(1,1) smoothing in HistoryCursor.snapshot.
    s = history[f"{scope}_{window}_rate"] * (n + 2) - 1
    return n, max(0., min(n, s))


def _shrink(n, successes, prior, strength):
    return (successes + strength * prior) / (n + strength)


def history_probabilities(history: Mapping[str, float]) -> dict[str, float]:
    """Deterministic rules; missing/unknown labels are not negative examples."""
    g = history["global_rate"]
    sat = _shrink(*_counts(history, "sat"), g, 10)
    station = _shrink(*_counts(history, "station"), g, 10)
    prior = (sat + station) / 2
    result = {"global": g, "satellite": sat, "station": station}
    for name in LEGACY_RULES[3:]:
        family, strength = name.rsplit("_", 1)
        window = "last10" if family == "last10" else "90d" if family == "pair90" else "30d"
        result[name] = _shrink(*_counts(history, "pair", window),
            prior if family in ("last10", "hierarchical30") else g, int(strength))
    for name, window in (("hierarchical7_2", "7d"), ("hierarchical90_2", "90d"), ("last50_2", "last50")):
        result[name] = _shrink(*_counts(history, "pair", window), prior, 2)
    result["link_last10_2"] = _shrink(*_counts(history, "link", "last10"), result[ANCHOR], 2)
    if any(not math.isfinite(p) or not 0 <= p <= 1 for p in result.values()):
        raise ValueError("invalid history probability")
    return result


def feature_vector(row: Mapping, history: Mapping[str, float], modes: Mapping[str, int]):
    """Exactly the same feature function in fitting, replay, and live inference."""
    def number(key, log=False):
        value = row.get(key)
        if value is None:
            return np.nan
        value = float(value)
        if not math.isfinite(value) or (log and value < 0):
            return np.nan
        return math.log1p(value) if log else value
    values = [number("max_elevation_deg"), number("duration_seconds", True), number("tle_age_hours", True),
              number("frequency_hz", True), number("transmitter_baud", True)]
    for key in ("rise_azimuth_deg", "set_azimuth_deg"):
        value = number(key)
        values.extend((math.sin(math.radians(value)), math.cos(math.radians(value))))
    start = row["start"]
    if isinstance(start, datetime):
        if start.tzinfo is None:
            raise ValueError("explicit timezone required")
        start = start.timestamp()
    dt = datetime.fromtimestamp(start, UTC)
    hour, year = start % 86400 / 86400 * 2 * math.pi, (dt.timetuple().tm_yday - 1) / 365.2425 * 2 * math.pi
    values.extend((math.sin(hour), math.cos(hour), math.sin(year), math.cos(year),
                   modes.get(row.get("transmitter_mode"), np.nan)))
    for scope in SCOPES:
        age = history.get(f"{scope}_days_since_last")
        values.extend((math.log1p(history[f"{scope}_count"]), history[f"{scope}_rate"],
                       math.log1p(age) if age is not None else np.nan))
        for window in WINDOWS:
            values.extend((math.log1p(history[f"{scope}_{window}_count"]), history[f"{scope}_{window}_rate"]))
    probabilities = history_probabilities(history)
    values.extend(probabilities[name] for name in RULES)
    return np.asarray(values, dtype=np.float32)


@dataclass
class RecentModel:
    estimator: object
    modes: dict[str, int]
    empty_columns: tuple[int, ...]

    def predict_matrix(self, matrix):
        x = np.array(matrix, dtype=np.float32, copy=True)
        if x.ndim != 2 or x.shape[1] != len(FEATURES):
            raise ValueError("feature matrix shape mismatch")
        x[:, self.empty_columns] = 0
        return self.estimator.predict_proba(x)[:, 1]

    def predict(self, row, history):
        return float(self.predict_matrix([feature_vector(row, history, self.modes)])[0])


def candidate_probabilities(history, learned_probability):
    result = history_probabilities(history)
    if not math.isfinite(learned_probability) or not 0 <= learned_probability <= 1:
        raise ValueError("invalid learned probability")
    result["hgb_recent"] = learned_probability
    for name, weight in BLENDS.items():
        result[name] = (1 - weight) * result[ANCHOR] + weight * learned_probability
    return result


def select_on_development(metrics):
    """One selection criterion; deterministic ties prefer the simpler rule."""
    if set(metrics) != set(CANDIDATES):
        raise ValueError("candidate panel is incomplete")
    if any(not math.isfinite(metrics[name]["brier"]) for name in CANDIDATES):
        raise ValueError("invalid candidate metric")
    return min(CANDIDATES, key=lambda name: (metrics[name]["brier"], CANDIDATES.index(name)))


def promotion_decision(challenger, metrics, paired_interval):
    """Conservative DEVELOPMENT gate, not a proof of future superiority.

    The already-inspected June panel is deliberately called development here.
    The anchor remains available even when a challenger loses or ties.
    """
    if challenger not in CANDIDATES:
        raise ValueError("unknown challenger")
    if challenger == ANCHOR:
        return {"method": ANCHOR, "promoted": False, "reason": "anchor_selected"}
    candidate, anchor = metrics[challenger], metrics[ANCHOR]
    improvement = anchor["brier"] - candidate["brier"]
    accuracy_ok = candidate["decision_at_0_5"]["accuracy"] >= anchor["decision_at_0_5"]["accuracy"]
    upper = paired_interval.get("upper_95")
    enough_days = paired_interval.get("day_blocks", 0) >= 10
    passed = improvement >= .001 and accuracy_ok and enough_days and upper is not None and math.isfinite(upper) and upper < 0
    return {"method": challenger if passed else ANCHOR, "promoted": bool(passed),
            "reason": "development_gate_passed" if passed else "retain_anchor",
            "brier_gain": improvement, "accuracy_noninferior": accuracy_ok,
            "day_blocks": paired_interval.get("day_blocks"), "delta_brier_upper_95": upper,
            "final_test_evidence": False}
