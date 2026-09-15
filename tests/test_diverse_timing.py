import math

import pytest

from telemetry_yield.clock_recovery import TimingHypothesis
from telemetry_yield.diverse_timing import additive_diverse_bank


def hypothesis(rate, phase, score=1):
    return TimingHypothesis(rate, phase * 5, 5, 0, score, 256)


def test_original_global_prefix_is_never_removed():
    ranked = tuple(hypothesis(0, i / 32, 100-i) for i in range(32)) + tuple(
        hypothesis(rate, i / 32, 60-i) for rate in (-100, 100) for i in range(32))
    result = additive_diverse_bank(ranked, global_top=16)
    assert result[:16] == ranked[:16]
    assert {h.rate_error_ppm for h in result} == {-100, 0, 100}
    assert 16 <= len(result) <= 22
    assert len(set(result)) == len(result)


def test_phase_separation_wraps_at_symbol_boundary():
    ranked = tuple(hypothesis(0, phase) for phase in (.99, .01, .3, .5))
    result = additive_diverse_bank(ranked, global_top=1, per_rate=2, min_phase_distance=.2)
    assert result == (ranked[0], ranked[2])


@pytest.mark.parametrize("kwargs", [{"global_top": 0}, {"global_top": True},
    {"global_top": 1, "per_rate": 0}, {"global_top": 1, "min_phase_distance": math.nan},
    {"global_top": 1, "min_phase_distance": .51}])
def test_invalid_budgets_rejected(kwargs):
    with pytest.raises(ValueError):
        additive_diverse_bank((), **kwargs)


def test_empty_bank_is_valid_and_deterministic():
    assert additive_diverse_bank((), global_top=16) == ()
