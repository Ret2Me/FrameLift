import math

import pytest

from telemetry_yield.candidate_selection import (
    BatchCandidateScorer,
    CandidateScorer,
    DeterministicEnergyScorer,
    FrequencyHint,
    HighRecallPolicy,
    IQWindow,
    TimeHint,
    WindowScore,
    select_candidate_regions,
)


class MappingScorer:
    name = "test-mapping"
    version = "1"

    def __init__(self, scores):
        self.scores = scores

    def score(self, window):
        return self.scores[window.window_id]


def window(window_id, start, end, samples=(0j,)):
    return IQWindow(window_id, start, end, samples)


def test_scorer_is_a_runtime_plugin_contract():
    scorer = MappingScorer({"a": WindowScore(0.2)})
    assert isinstance(scorer, CandidateScorer)


def test_optional_batch_contract_is_used_for_model_friendly_inference():
    class BatchScorer(MappingScorer):
        def __init__(self, scores):
            super().__init__(scores)
            self.batch_calls = 0

        def score(self, window):  # pragma: no cover - must not be used
            raise AssertionError("individual inference was used")

        def score_many(self, windows):
            self.batch_calls += 1
            return [self.scores[window.window_id] for window in windows]

    scorer = BatchScorer({"a": WindowScore(0.2), "b": WindowScore(0.3)})
    assert isinstance(scorer, BatchCandidateScorer)
    select_candidate_regions([window("a", 0, 1), window("b", 1, 2)], scorer)
    assert scorer.batch_calls == 1


def test_threshold_and_top_k_are_combined_with_or():
    windows = [window("high", 0, 1), window("rescued", 2, 3), window("low", 4, 5)]
    scorer = MappingScorer(
        {
            "high": WindowScore(0.9),
            "rescued": WindowScore(0.2),
            "low": WindowScore(0.1),
        }
    )
    result = select_candidate_regions(
        windows,
        scorer,
        policy=HighRecallPolicy(
            probability_threshold=0.8,
            top_k=2,
            padding_before_seconds=0,
            padding_after_seconds=0,
        ),
    )

    by_id = {item.window_id: item for item in result.scored_windows}
    assert by_id["high"].selection_reasons == ("threshold", "top_k")
    assert by_id["rescued"].selection_reasons == ("top_k",)
    assert not by_id["low"].selected


def test_time_hint_padding_and_recording_bounds_are_respected():
    windows = [window("edge", 0, 2), window("tail", 8, 10)]
    scorer = MappingScorer(
        {
            "edge": WindowScore(0.9, time_hint=TimeHint(0.1, 0.5)),
            "tail": WindowScore(0.9, time_hint=TimeHint(9.5, 10.0)),
        }
    )
    result = select_candidate_regions(
        windows,
        scorer,
        recording_duration_seconds=10,
        policy=HighRecallPolicy(
            probability_threshold=0.5,
            top_k=None,
            padding_before_seconds=1,
            padding_after_seconds=1,
        ),
    )
    assert [(item.start_seconds, item.end_seconds) for item in result.candidates] == [
        (0.0, 1.5),
        (8.5, 10.0),
    ]


def test_overlapping_padded_regions_merge_and_preserve_hints():
    windows = [window("a", 1, 2), window("b", 2.2, 3)]
    scorer = MappingScorer(
        {
            "a": WindowScore(
                0.7,
                frequency_hint=FrequencyHint(-100, 100),
                waveform_hints=("FSK",),
            ),
            "b": WindowScore(
                0.9,
                frequency_hint=FrequencyHint(200, 300),
                waveform_hints=("BPSK",),
            ),
        }
    )
    result = select_candidate_regions(
        windows,
        scorer,
        policy=HighRecallPolicy(
            probability_threshold=0.5,
            top_k=None,
            padding_before_seconds=0.2,
            padding_after_seconds=0.2,
        ),
    )
    assert len(result.candidates) == 1
    candidate = result.candidates[0]
    assert (candidate.start_seconds, candidate.end_seconds) == (0.8, 3.2)
    assert candidate.peak_probability == 0.9
    assert candidate.source_window_ids == ("a", "b")
    assert candidate.waveform_hints == ("BPSK", "FSK")
    assert len(candidate.frequency_hints) == 2


def test_rejected_audit_sample_is_exact_and_input_order_independent():
    windows = [window(str(index), index, index + 0.5) for index in range(10)]
    scorer = MappingScorer({item.window_id: WindowScore(0.0) for item in windows})
    policy = HighRecallPolicy(
        probability_threshold=1,
        top_k=0,
        rejected_audit_count=3,
        audit_seed="fixed",
    )
    first = select_candidate_regions(windows, scorer, policy=policy)
    second = select_candidate_regions(list(reversed(windows)), scorer, policy=policy)
    first_ids = [item.window_id for item in first.audited_rejections]
    second_ids = [item.window_id for item in second.audited_rejections]
    assert len(first_ids) == 3
    assert first_ids == second_ids


def test_deterministic_energy_fallback_is_monotonic_and_discloses_limits():
    scorer = DeterministicEnergyScorer(reference_power=1)
    zero = scorer.score(window("zero", 0, 1, (0j, 0j)))
    weak = scorer.score(window("weak", 0, 1, (0.5 + 0j, 0.5 + 0j)))
    strong = scorer.score(window("strong", 0, 1, (2 + 0j, 2 + 0j)))
    assert zero.probability == 0
    assert zero.probability < weak.probability < strong.probability < 1
    assert strong.metadata["calibrated_probability"] is False
    assert strong.metadata["ai_model"] is False
    assert scorer.score(window("strong", 0, 1, (2 + 0j, 2 + 0j))) == strong


@pytest.mark.parametrize("probability", [-0.01, 1.01, math.nan])
def test_invalid_probabilities_fail_closed(probability):
    with pytest.raises(ValueError):
        WindowScore(probability)


def test_scorer_time_hint_must_stay_inside_source_window():
    scorer = MappingScorer({"a": WindowScore(0.9, time_hint=TimeHint(0.5, 2.1))})
    with pytest.raises(ValueError, match="outside"):
        select_candidate_regions([window("a", 1, 2)], scorer)


def test_duplicate_window_ids_are_rejected_for_reproducible_audits():
    scorer = MappingScorer({"same": WindowScore(0.5)})
    with pytest.raises(ValueError, match="unique"):
        select_candidate_regions(
            [window("same", 0, 1), window("same", 1, 2)], scorer
        )


def test_energy_scorer_rejects_empty_or_non_finite_samples():
    scorer = DeterministicEnergyScorer()
    with pytest.raises(ValueError, match="empty"):
        scorer.score(window("empty", 0, 1, ()))
    with pytest.raises(ValueError, match="finite"):
        scorer.score(window("nan", 0, 1, (complex(math.nan, 0),)))
