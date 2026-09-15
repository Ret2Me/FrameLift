from datetime import UTC, datetime, timedelta
import math
import pickle

import pytest

from telemetry_yield.planning.history_ensemble import HistoryExpertMixer
from telemetry_yield.planning.reception_revision import (
    CANDIDATES, IDENTITY_MAP, ReceptionRevision, calibration_probabilities,
)

T = datetime(2025, 1, 1, tzinfo=UTC)


def issue(model, key="a", day=0, station=1, satellite=1):
    at = T + timedelta(days=day)
    probabilities = {name: .1 + i * .12 for i, name in enumerate(model.experts)}
    return model.issue(key, probabilities, norad_id=satellite, station_id=station,
        issued_at=at, pass_start=at+timedelta(hours=2), pass_end=at+timedelta(hours=3))


@pytest.mark.parametrize("method", CANDIDATES)
def test_future_label_flip_cannot_change_prediction(method):
    a, b = ReceptionRevision(method), ReceptionRevision(method)
    issue(a)
    issue(b)
    a.feedback("a", 0, available_at=T+timedelta(days=2))
    b.feedback("a", 1, available_at=T+timedelta(days=2))
    assert issue(a, "b", day=1) == issue(b, "b", day=1)
    if method != "uniform_history":
        assert issue(a, "c", day=3).probability < issue(b, "c", day=3).probability


@pytest.mark.parametrize("method", CANDIDATES)
def test_pickle_restart_idempotency_and_retraction(method):
    model = ReceptionRevision(method)
    original = issue(model)
    model.feedback("a", 1, available_at=T+timedelta(days=1))
    clone = pickle.loads(pickle.dumps(model))
    assert issue(clone, "b", day=2) == issue(model, "b", day=2)
    assert issue(model) == original
    model.feedback("a", None, available_at=T+timedelta(days=3))
    assert issue(model, "c", day=4).global_feedback_count == 0
    with pytest.raises(ValueError, match="group"):
        issue(model, station=2)


def test_context_missing_groups_back_off_to_global_without_cross_contamination():
    model = ReceptionRevision("context_history")
    issue(model)
    model.feedback("a", 1, available_at=T+timedelta(days=1))
    new = issue(model, "new", day=2, station=2, satellite=2)
    assert new.local_feedback_count == 0
    assert dict(new.expert_weights) == pytest.approx(model.global_mixer.weights(as_of=T+timedelta(days=2)))


def test_calibration_grid_is_monotone_finite_and_contains_exact_identity():
    previous = calibration_probabilities(0)
    for p in (1e-15, .01, .2, .5, .9, 1-1e-15, 1):
        current = calibration_probabilities(p)
        assert current[IDENTITY_MAP] == p
        assert all(math.isfinite(v) and 0 <= v <= 1 and v >= previous[k] for k, v in current.items())
        previous = current
    for p in (True, float("nan"), -.1, 1.1):
        with pytest.raises(ValueError):
            calibration_probabilities(p)


def test_log_loss_prior_and_retraction():
    model = HistoryExpertMixer(("a", "b"), loss="log", prior={"a": 3., "b": 1.})
    assert model.weights(as_of=T) == pytest.approx({"a": .75, "b": .25})
    model.issue("x", {"a": 0., "b": 1.}, issued_at=T,
        pass_start=T+timedelta(hours=1), pass_end=T+timedelta(hours=2))
    model.feedback("x", 1, available_at=T+timedelta(days=1))
    assert model.weights(as_of=T+timedelta(days=2))["b"] > .99
    model.feedback("x", None, available_at=T+timedelta(days=3))
    assert model.weights(as_of=T+timedelta(days=4)) == pytest.approx({"a": .75, "b": .25})


@pytest.mark.parametrize("args", [{"loss":"bad"}, {"prior":{"a":0,"b":1}},
    {"prior":{"a":True,"b":1}}, {"prior":{"a":1}}, {"prior":{"a":float("inf"),"b":1}}])
def test_bad_mixer_configuration_is_rejected(args):
    with pytest.raises(ValueError):
        HistoryExpertMixer(("a", "b"), **args)
