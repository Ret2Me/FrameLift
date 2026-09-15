from datetime import UTC, datetime, timedelta

import pytest

from telemetry_yield.planning.history_ensemble import HistoryExpertMixer

T = datetime(2025, 1, 1, tzinfo=UTC)


def issue(mixer, key="one", at=T, p=None):
    return mixer.issue(key, p or {"good": .9, "bad": .1}, issued_at=at,
                       pass_start=at + timedelta(hours=2), pass_end=at + timedelta(hours=3))


def test_delayed_outcome_never_influences_an_earlier_forecast():
    a, b = HistoryExpertMixer(("good", "bad")), HistoryExpertMixer(("good", "bad"))
    issue(a)
    issue(b)
    for mixer, label in ((a, 1), (b, 0)):
        mixer.feedback("one", label, available_at=T + timedelta(days=2))
    assert issue(a, "two", T + timedelta(days=1)) == issue(b, "two", T + timedelta(days=1))
    assert issue(a, "three", T + timedelta(days=3)).probability > .5
    assert issue(b, "three", T + timedelta(days=3)).probability < .5


def test_corrections_replace_and_retractions_remove_original_loss():
    corrected, direct = HistoryExpertMixer(("good", "bad")), HistoryExpertMixer(("good", "bad"))
    issue(corrected)
    issue(direct)
    corrected.feedback("one", 0, available_at=T + timedelta(days=1))
    corrected.weights(as_of=T + timedelta(days=1))
    corrected.feedback("one", 1, available_at=T + timedelta(days=2))
    direct.feedback("one", 1, available_at=T + timedelta(days=2))
    assert corrected.weights(as_of=T + timedelta(days=3)) == pytest.approx(direct.weights(as_of=T + timedelta(days=3)))
    assert issue(corrected, "two", T + timedelta(days=3)).feedback_count == 1
    corrected.feedback("one", None, available_at=T + timedelta(days=4))
    assert corrected.weights(as_of=T + timedelta(days=4)) == pytest.approx({"good": .5, "bad": .5})
    assert issue(corrected, "three", T + timedelta(days=4)).feedback_count == 0


def test_multiple_queued_revisions_match_sequential_receipts():
    queued, sequential = HistoryExpertMixer(("good", "bad")), HistoryExpertMixer(("good", "bad"))
    issue(queued)
    issue(sequential)
    for day, label in ((1, 0), (2, 1), (3, None), (4, 0)):
        for mixer in (queued, sequential):
            mixer.feedback("one", label, available_at=T + timedelta(days=day))
        sequential.weights(as_of=T + timedelta(days=day))
    assert queued.weights(as_of=T + timedelta(days=5)) == pytest.approx(sequential.weights(as_of=T + timedelta(days=5)))


def test_replaying_a_forecast_or_receipt_does_not_double_count():
    mixer = HistoryExpertMixer(("good", "bad"))
    original = issue(mixer)
    for _ in range(2):
        mixer.feedback("one", 1, available_at=T + timedelta(days=1))
    later = issue(mixer, "two", T + timedelta(days=2))
    assert later.feedback_count == 1
    assert issue(mixer) == original
    with pytest.raises(ValueError, match="overwritten"):
        issue(mixer, p={"good": .8, "bad": .2})
    with pytest.raises(ValueError, match="conflicting"):
        mixer.feedback("one", 0, available_at=T + timedelta(days=1))


def test_stale_losses_decay_and_late_upload_does_not_refresh_observation_age():
    early, late = HistoryExpertMixer(("good", "bad")), HistoryExpertMixer(("good", "bad"))
    issue(early)
    issue(late)
    early.feedback("one", 1, available_at=T + timedelta(days=1))
    early.weights(as_of=T + timedelta(days=1))
    late.feedback("one", 1, available_at=T + timedelta(days=90))
    assert early.weights(as_of=T + timedelta(days=90)) == pytest.approx(late.weights(as_of=T + timedelta(days=90)))
    assert early.weights(as_of=T + timedelta(days=3000))["good"] == pytest.approx(.5)


def test_snapshot_replay_order_does_not_change_weights():
    a, b = HistoryExpertMixer(("good", "bad")), HistoryExpertMixer(("good", "bad"))
    for mixer in (a, b):
        issue(mixer)
        mixer.feedback("one", 1, available_at=T + timedelta(days=1))
    for hour in range(24, 72):
        a.weights(as_of=T + timedelta(hours=hour))
    assert a.weights(as_of=T + timedelta(days=3)) == pytest.approx(b.weights(as_of=T + timedelta(days=3)))


@pytest.mark.parametrize("settings", [{"learning_rate": 0}, {"half_life_days": float("nan")}, {"learning_rate": True}, {"experts": ("x", "x")}])
def test_invalid_settings_fail(settings):
    with pytest.raises(ValueError):
        HistoryExpertMixer(**settings)


def test_invalid_timestamps_labels_and_probabilities_fail():
    mixer = HistoryExpertMixer(("good", "bad"))
    issue(mixer)
    with pytest.raises(ValueError, match="reception end"):
        mixer.feedback("one", 1, available_at=T)
    with pytest.raises(ValueError, match="binary"):
        mixer.feedback("one", True, available_at=T + timedelta(days=1))
    with pytest.raises(ValueError, match="probabilities"):
        issue(mixer, "other", p={"good": float("nan"), "bad": .2})
    mixer.weights(as_of=T + timedelta(days=3))
    with pytest.raises(ValueError, match="backdated"):
        mixer.feedback("one", 1, available_at=T + timedelta(days=2))
    with pytest.raises(ValueError, match="chronological"):
        mixer.weights(as_of=T)
