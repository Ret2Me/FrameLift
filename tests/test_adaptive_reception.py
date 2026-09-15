from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import numpy as np
import pytest

from telemetry_yield.planning import adaptive_reception as a
from telemetry_yield.planning.live_history import HistoryCursor, HistoryEvent


def row():
    return {"norad_id": 1, "station_id": 2, "transmitter_uuid": "tx", "start": datetime(2026, 2, 1, tzinfo=UTC),
            "max_elevation_deg": None, "frequency_hz": None}


def context(events=()):
    target = row()
    return HistoryCursor(events, task="signal_present", availability_kind="captured").snapshot(
        SimpleNamespace(**target), as_of=target["start"] - timedelta(hours=2), pass_start=target["start"])


def event(i, outcome, *, tx="tx", available=None):
    end = row()["start"] - timedelta(days=20-i)
    return HistoryEvent(str(i), "signal_present", end, available or end+timedelta(hours=1),
                        1, 2, tx, None, None, outcome)


def test_no_history_falls_back_and_optional_parameters_are_not_required():
    h = context()
    assert all(p == .5 for p in a.history_probabilities(h).values())
    vector = a.feature_vector(row(), h, {})
    assert vector.shape == (len(a.FEATURES),)
    assert np.isnan(vector[a.FEATURES.index("max_elevation")])
    assert not np.isinf(vector).any()


def test_last10_and_baseline_probability_are_actual_model_inputs():
    # Last10 all positive but lifetime includes failures.
    h = context([event(i, int(i>=5)) for i in range(15)])
    x = a.feature_vector(row(), h, {})
    assert x[a.FEATURES.index("pair_last10_rate")] == pytest.approx(11/12)
    assert x[a.FEATURES.index("rule_last10_2")] == pytest.approx(a.history_probabilities(h)[a.ANCHOR])
    assert h["pair_rate"] < h["pair_last10_rate"]
    assert len(set(a.FEATURES)) == len(a.FEATURES)


def test_unknown_future_and_retracted_results_never_become_failures():
    h = context([event(1, 1), event(2, None), event(3, 0, available=row()["start"]+timedelta(hours=1))])
    assert h["pair_count"] == 1
    original = event(1, 1)
    correction = HistoryEvent(original.observation_key, original.task, original.ended_at,
        original.available_at+timedelta(days=1), 1, 2, "tx", None, None, None)
    assert context([original, correction])["pair_count"] == 0


def test_transmitter_fallback_and_no_current_outcome_leakage():
    h = context([event(i, 1, tx="different") for i in range(5)])
    p = a.history_probabilities(h)
    assert p["link_last10_2"] == p[a.ANCHOR]
    np.testing.assert_equal(a.feature_vector(row(), h, {}),
        a.feature_vector({**row(), "signal_present": 0, "decode_success_given_signal": 1}, h, {}))


def test_blends_are_convex_and_selection_does_not_require_ai():
    h = context([event(i, 1) for i in range(5)])
    p = a.candidate_probabilities(h, .1)
    assert p["blend25"] == pytest.approx(.75*p[a.ANCHOR]+.025)
    metrics = {name: {"brier": .2} for name in a.CANDIDATES}
    metrics[a.ANCHOR]["brier"] = .1
    assert a.select_on_development(metrics) == a.ANCHOR
    with pytest.raises(ValueError):
        a.candidate_probabilities(h, float("nan"))


@pytest.mark.parametrize("gain,accuracy,upper,days,expected", [
    (.01,.81,-.001,14,True), (.0001,.81,-.001,14,False),
    (.01,.79,-.001,14,False), (.01,.81,.001,14,False),
    (.01,.81,float("nan"),14,False), (.01,.81,-.001,4,False)])
def test_promotion_requires_all_predeclared_checks(gain, accuracy, upper, days, expected):
    metrics = {a.ANCHOR: {"brier": .15, "decision_at_0_5": {"accuracy": .8}},
               "hgb_recent": {"brier": .15-gain, "decision_at_0_5": {"accuracy": accuracy}}}
    result = a.promotion_decision("hgb_recent", metrics, {"upper_95": upper, "day_blocks": days})
    assert result["promoted"] == expected
    assert result["method"] == ("hgb_recent" if expected else a.ANCHOR)
