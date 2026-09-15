from __future__ import annotations

import importlib.util
import inspect
import json
from pathlib import Path
import sys

import numpy as np

from telemetry_yield.rml24_physical_plugin import (
    AlignmentPolicy,
    QpskUnaligned,
    align_for_ber,
    align_qpsk_for_ber,
)


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "work" / "rml24" / "diagnose_acquisition_v2.py"
SPEC = importlib.util.spec_from_file_location("diagnose_acquisition_v2", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
acquisition = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = acquisition
SPEC.loader.exec_module(acquisition)


def test_preregistered_splits_are_disjoint_and_prior_exposure_is_disclosed() -> None:
    prereg = json.loads(
        (ROOT / "reports" / "rml24-acquisition-v2-prereg.json").read_text()
    )
    assert prereg["status"] == "frozen_before_prototype_implementation"
    assert prereg["prior_exposure"]["aggregate_full_report_seen"] is True
    assert "not a pristine" in prereg["prior_exposure"]["handling"]
    selected: set[tuple[int, int]] = set()
    for split in prereg["splits"].values():
        snr = int(split["snr_db"])
        for index in range(
            int(split["record_start_inclusive"]),
            int(split["record_stop_exclusive"]),
        ):
            assert (snr, index) not in selected
            selected.add((snr, index))


def test_waveform_origin_estimator_has_no_truth_input_and_uses_frozen_rounding() -> None:
    parameters = set(inspect.signature(acquisition.estimate_waveform_time_origin).parameters)
    assert not parameters & {"truth", "bits", "bit_lengths", "payload", "timestamp"}
    assert acquisition.estimate_waveform_time_origin(
        timing_phase_samples=4.9,
        samples_per_symbol=10.0,
        bits_per_symbol=1,
    ) == 0
    assert acquisition.estimate_waveform_time_origin(
        timing_phase_samples=5.0,
        samples_per_symbol=10.0,
        bits_per_symbol=1,
    ) == 1
    assert acquisition.estimate_waveform_time_origin(
        timing_phase_samples=5.0,
        samples_per_symbol=10.0,
        bits_per_symbol=2,
    ) == 2


def test_recover_candidate_reads_qpsk_and_oqpsk_timing_shapes(monkeypatch) -> None:
    def fake_qpsk(_iq, *, sample_rate_hz, symbol_rate_hz, offset):
        assert sample_rate_hz == 1_000_000.0
        assert symbol_rate_hz == 100_000.0
        if offset:
            return (
                QpskUnaligned(
                    (np.array([0, 1]), np.array([1, 0])),
                    ("q_delayed_half_symbol", "i_delayed_half_symbol"),
                ),
                {
                    "timing": {
                        "q_delayed_half_symbol": {"selected_phase_samples": 2.5},
                        "i_delayed_half_symbol": {"selected_phase_samples": 7.5},
                    }
                },
            )
        return (
            QpskUnaligned((np.array([0, 1]),), ("common_iq_timing",)),
            {"timing": {"selected_phase_samples": 4.0}},
        )

    monkeypatch.setattr(acquisition, "demodulate_qpsk_unaligned", fake_qpsk)
    qpsk = acquisition.recover_candidate(
        np.zeros((2, 4)),
        modulation="QPSK",
        symbol_rate_hz=100_000.0,
        sample_rate_hz=1_000_000.0,
    )
    oqpsk = acquisition.recover_candidate(
        np.zeros((2, 4)),
        modulation="OQPSK",
        symbol_rate_hz=100_000.0,
        sample_rate_hz=1_000_000.0,
    )
    assert qpsk.timing_phases_samples == (4.0,)
    assert oqpsk.timing_phases_samples == (2.5, 7.5)


def test_fixed_shift_edge_accounting_counts_missing_and_excess_separately() -> None:
    truth = np.array([1, 0, 1, 1], dtype=np.uint8)
    with_prefix = np.array([0, 1, 0, 1, 1], dtype=np.uint8)
    score = acquisition.score_binary_fixed_shift(with_prefix, truth, shift=1)
    assert score.errors == 0
    assert score.overlap_bits == 4
    assert score.missing_truth_bits == 0
    assert score.excess_candidate_bits == 1

    truncated = np.array([0, 1, 1], dtype=np.uint8)
    score = acquisition.score_binary_fixed_shift(truncated, truth, shift=-1)
    assert score.errors == 1
    assert score.overlap_bits == 3
    assert score.missing_truth_bits == 1
    assert score.excess_candidate_bits == 0


def test_fft_binary_oracle_exactly_matches_production_alignment() -> None:
    rng = np.random.default_rng(617)
    for truth_length, candidate_length, maximum in ((31, 37, 8), (205, 204, 8), (73, 81, 72)):
        truth = rng.integers(0, 2, truth_length, dtype=np.uint8)
        candidate = rng.integers(0, 2, candidate_length, dtype=np.uint8)
        expected = align_for_ber(
            candidate,
            truth,
            policy=AlignmentPolicy(max_shift_bits=maximum),
        )[1]
        actual = acquisition.score_binary_oracle(
            candidate,
            truth,
            max_shift_bits=maximum,
        )
        assert actual.errors == expected["errors_including_missing"]
        assert actual.shift_bits == expected["shift_bits"]
        assert actual.polarity_inverted == expected["polarity_inverted"]
        assert actual.overlap_bits == expected["overlap_bits"]


def test_fft_qpsk_oracle_exactly_matches_production_alignment() -> None:
    rng = np.random.default_rng(947)
    for maximum in (8, 64):
        truth = rng.integers(0, 2, 96, dtype=np.uint8)
        raw = QpskUnaligned(
            (
                rng.integers(0, 2, 104, dtype=np.uint8),
                rng.integers(0, 2, 92, dtype=np.uint8),
            ),
            ("first", "second"),
        )
        expected = align_qpsk_for_ber(
            raw,
            truth,
            policy=AlignmentPolicy(max_shift_bits=maximum),
        )[1]
        candidate = acquisition.RecoveredCandidate(
            tuple(raw.bit_variants),
            tuple(raw.variant_names),
            (0.0, 0.0),
            10.0,
            2,
        )
        actual = acquisition.score_qpsk_oracle(
            candidate,
            truth,
            max_shift_bits=maximum,
        )
        assert actual.errors == expected["errors_including_missing"]
        assert actual.shift_bits == expected["shift_bits"]
        assert actual.constellation_rotation_quarter_turns == expected[
            "constellation_rotation_quarter_turns"
        ]
        assert actual.constellation_reflected == expected["constellation_reflected"]
        assert actual.variant_name == expected["timing_hypothesis"]
        assert actual.overlap_bits == expected["overlap_bits"]


def test_fixed_qpsk_tie_break_includes_shift_magnitude_before_symmetry() -> None:
    truth = np.array([0, 0, 0, 0], dtype=np.uint8)
    candidate = acquisition.RecoveredCandidate(
        (
            np.array([0, 0, 0, 0, 0, 0], dtype=np.uint8),
            np.array([0, 0, 0, 0], dtype=np.uint8),
        ),
        ("large_shift", "zero_shift"),
        (0.0, 0.0),
        10.0,
        2,
    )
    score = acquisition.score_qpsk_fixed_shifts(candidate, truth, shifts=(2, 0))
    assert score.shift_bits == 0
    assert score.variant_name == "zero_shift"


def test_success_rule_reports_null_when_estimator_does_not_improve_zero() -> None:
    aggregates = []
    for method, errors in (
        ("correct:zero_shift", 40),
        ("correct:waveform_time_origin", 40),
        ("correct:truth_aided_shift_8", 30),
        ("wrong_record:waveform_time_origin", 50),
        ("random_candidate:waveform_time_origin", 50),
    ):
        aggregates.append({"method": method, "errors": errors, "truth_bits": 100})
    evaluation = acquisition.evaluate_success({"aggregates": aggregates})
    assert evaluation["success"] is False
    assert evaluation["verdict"] == "null_or_negative"


def test_edge_accounting_validator_checks_partitions_and_row_counts() -> None:
    split = {
        "waveform_records": 2,
        "score_rows": 4,
        "aggregates": [
            {
                "method": method,
                "records": 2,
                "errors": 3,
                "truth_bits": 10,
                "overlap_bits": 9,
                "missing_truth_bits": 1,
                "excess_candidate_bits": 2,
                "shift_histogram": {"0": 1, "1": 1},
            }
            for method in ("first", "second")
        ],
    }
    assert acquisition.validate_edge_accounting(split)["pass"] is True
    split["aggregates"][0]["missing_truth_bits"] = 2
    validation = acquisition.validate_edge_accounting(split)
    assert validation["pass"] is False
    assert validation["checks"]["truth_partition_overlap_plus_missing"] is False
