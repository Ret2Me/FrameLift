from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parents[1]


def _load_script(name: str, relative_path: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ALIGNMENT = _load_script(
    "diagnose_physical_ber_v1", "work/rml24/diagnose_physical_ber_v1.py"
)
FRONTEND = _load_script(
    "diagnose_physical_frontend_v1",
    "work/rml24/diagnose_physical_frontend_v1.py",
)
HOLDOUT = _load_script(
    "evaluate_carrier_timing_v2_holdout",
    "work/rml24/evaluate_carrier_timing_v2_holdout.py",
)


def test_expanded_alignment_recovers_a_shift_beyond_reported_bound() -> None:
    rng = np.random.default_rng(61)
    truth = rng.integers(0, 2, size=256, dtype=np.uint8)
    candidate = np.concatenate((np.zeros(31, dtype=np.uint8), truth))
    limited = ALIGNMENT._score(
        candidate, truth, modulation="BPSK", max_shift_bits=8
    )
    expanded = ALIGNMENT._score(
        candidate, truth, modulation="BPSK", max_shift_bits=64
    )
    wrong = ALIGNMENT._score(
        candidate, np.roll(truth, 77), modulation="BPSK", max_shift_bits=64
    )
    assert expanded["shift_bits"] == 31
    assert expanded["ber_including_missing"] == 0.0
    assert limited["ber_including_missing"] > 0.25
    assert wrong["ber_including_missing"] > 0.25


def test_rrc_taps_are_symmetric_finite_and_unit_energy() -> None:
    taps = FRONTEND.root_raised_cosine_taps(
        10.0, rolloff=0.35, span_symbols=6
    )
    assert len(taps) == 61
    assert np.all(np.isfinite(taps))
    np.testing.assert_allclose(taps, taps[::-1], rtol=0.0, atol=1e-15)
    assert np.isclose(np.sum(taps**2), 1.0)


def test_truth_free_rrc_selector_and_legacy_recover_easy_bpsk() -> None:
    rng = np.random.default_rng(73)
    truth = rng.integers(0, 2, size=205, dtype=np.uint8)
    symbols = 1.0 - 2.0 * truth.astype(np.float64)
    signal = np.repeat(symbols, 10)[:2048].astype(np.complex128)
    samples = np.arange(len(signal), dtype=np.float64)
    signal *= np.exp(1j * (0.31 + 2.0 * np.pi * 700.0 * samples / 1_000_000.0))
    iq = np.stack((signal.real, signal.imag)).astype(np.float32)

    legacy, candidates, selected_span, scores = FRONTEND.recover_frontends(
        iq,
        modulation="BPSK",
        symbol_rate_hz=100_000.0,
        sample_rate_hz=1_000_000.0,
        rolloff=0.35,
        spans=(4, 6, 8),
    )

    assert set(candidates) == {4, 6, 8}
    assert selected_span in candidates
    assert all(np.isfinite(value) for value in scores.values())
    legacy_score = FRONTEND._score(
        legacy, truth, modulation="BPSK", max_shift_bits=16
    )
    selected_score = FRONTEND._score(
        candidates[selected_span], truth, modulation="BPSK", max_shift_bits=16
    )
    assert legacy_score["ber_including_missing"] < 0.03
    assert selected_score["ber_including_missing"] < 0.03


def test_carrier_v2_paired_bootstrap_is_deterministic_and_paired() -> None:
    pairs = [
        ({"errors": 40, "truth_bits": 100}, {"errors": 10, "truth_bits": 100}),
        ({"errors": 30, "truth_bits": 200}, {"errors": 20, "truth_bits": 200}),
        ({"errors": 25, "truth_bits": 50}, {"errors": 5, "truth_bits": 50}),
    ]
    first = HOLDOUT._paired_bootstrap(pairs, seed=17, resamples=500)
    second = HOLDOUT._paired_bootstrap(pairs, seed=17, resamples=500)
    assert first == second
    assert first["delta_v2_minus_legacy_p97_5"] < 0.0
