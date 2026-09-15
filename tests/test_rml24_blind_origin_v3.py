from __future__ import annotations

import importlib.util
import hashlib
import inspect
import json
from pathlib import Path
import sys

import numpy as np

from telemetry_yield.rml24_blind_origin import Rml24BlindOriginV3


ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / "work" / "rml24"
if str(WORK) not in sys.path:
    sys.path.insert(0, str(WORK))
SCRIPT = WORK / "run_blind_origin_v3.py"
SPEC = importlib.util.spec_from_file_location("run_blind_origin_v3", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
origin = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = origin
SPEC.loader.exec_module(origin)


def _iq(signal: np.ndarray) -> np.ndarray:
    return np.stack((signal.real, signal.imag)).astype(np.float32)


def test_preregistered_v3_splits_are_disjoint_and_exposure_is_honest() -> None:
    prereg = json.loads(
        (ROOT / "reports" / "rml24-blind-origin-v3-prereg.json").read_text()
    )
    assert prereg["status"] == (
        "frozen_before_v3_implementation_or_new_transfer_holdout_access"
    )
    assert prereg["exposure_classification"]["not_pristine_dataset_claim"] is True
    selected: set[tuple[int, int]] = set()
    for split_name in ("development", "transfer", "holdout"):
        for cohort in prereg["splits"][split_name]:
            snr = int(cohort["snr_db"])
            for index in range(
                int(cohort["record_start_inclusive"]),
                int(cohort["record_stop_exclusive"]),
            ):
                assert (snr, index) not in selected
                selected.add((snr, index))


def test_runtime_estimator_signature_excludes_truth_record_and_snr() -> None:
    parameters = set(inspect.signature(origin.estimate_blind_origin_v3).parameters)
    assert not parameters & origin.FORBIDDEN_RUNTIME_NAMES
    assert parameters == {
        "iq",
        "modulation",
        "symbol_rate_hz",
        "sample_rate_hz",
        "frozen_config",
    }


def test_power_boundary_detects_a_synthetic_leading_edge() -> None:
    rng = np.random.default_rng(73)
    signal = np.zeros(400, dtype=np.complex128)
    signal[100:] = np.exp(1j * rng.uniform(-np.pi, np.pi, len(signal) - 100))
    detected = origin.detect_power_boundary_samples(
        _iq(signal),
        samples_per_symbol=10.0,
        smoothing_symbols=1.0,
        threshold_fraction=0.5,
    )
    assert 94 <= detected <= 102


def test_cyclostationary_boundary_detects_a_synthetic_leading_edge() -> None:
    samples_per_symbol = 10.0
    n = np.arange(600)
    amplitude = 1.0 + 0.35 * np.cos(2 * np.pi * n / samples_per_symbol)
    signal = amplitude.astype(np.complex128)
    signal[:120] = 0.0
    detected = origin.detect_cyclostationary_boundary_samples(
        _iq(signal),
        samples_per_symbol=samples_per_symbol,
        window_symbols=4,
        threshold_fraction=0.5,
    )
    assert 95 <= detected <= 130


def test_deterministic_estimator_uses_only_frozen_profile_constant() -> None:
    iq = _iq(np.ones(256, dtype=np.complex128))
    config = {
        "selected_estimator": {"family": "deterministic_profile_delay"},
        "profiles": {
            "BPSK:100000": {
                "deterministic_shift_bits": -17,
                "boundary_medians_samples": {},
            }
        },
    }
    assert origin.estimate_blind_origin_v3(
        iq=iq,
        modulation="BPSK",
        symbol_rate_hz=100_000,
        sample_rate_hz=1_000_000,
        frozen_config=config,
    ) == -17


def test_binary_fixed_error_curve_matches_direct_scorer() -> None:
    rng = np.random.default_rng(91)
    truth = rng.integers(0, 2, 57, dtype=np.uint8)
    predicted = rng.integers(0, 2, 63, dtype=np.uint8)
    candidate = origin.RecoveredCandidate(
        (predicted,),
        ("binary",),
        (0.0,),
        4.0,
        1,
    )
    shifts, errors = origin.fixed_error_curve(candidate, truth)
    for shift in (-32, -7, 0, 11, 40):
        position = int(np.flatnonzero(shifts == shift)[0])
        expected = origin.score_uniform_shift(candidate, truth, shift=shift)
        assert int(errors[position]) == expected.errors


def test_qpsk_fixed_error_curve_matches_direct_scorer() -> None:
    rng = np.random.default_rng(111)
    truth = rng.integers(0, 2, 64, dtype=np.uint8)
    predicted = rng.integers(0, 2, 76, dtype=np.uint8)
    candidate = origin.RecoveredCandidate(
        (predicted,),
        ("common_iq_timing",),
        (0.0,),
        4.0,
        2,
    )
    shifts, errors = origin.fixed_error_curve(candidate, truth)
    for shift in (-30, -8, 0, 12, 48):
        position = int(np.flatnonzero(shifts == shift)[0])
        expected = origin.score_uniform_shift(candidate, truth, shift=shift)
        assert int(errors[position]) == expected.errors


def test_opt_in_production_interface_exposes_frozen_profile_without_truth() -> None:
    estimator = Rml24BlindOriginV3()
    iq = np.zeros((2, 2048), dtype=np.float32)
    estimate = estimator.estimate(
        iq,
        modulation="QPSK",
        sample_rate_hz=1_000_000,
        symbol_rate_hz=250_000,
    )
    assert estimate.shift_bits == -90
    assert estimate.truth_used is False
    assert estimator.capabilities()["opt_in"] is True
    assert estimator.capabilities()["default_physical_dsp_changed"] is False
    assert "truth" not in inspect.signature(estimator.estimate).parameters


def test_opt_in_production_interface_fails_closed_outside_evaluated_scope() -> None:
    estimator = Rml24BlindOriginV3()
    iq = np.zeros((2, 2048), dtype=np.float32)
    for kwargs in (
        {
            "modulation": "QPSK",
            "sample_rate_hz": 2_000_000,
            "symbol_rate_hz": 250_000,
        },
        {
            "modulation": "16QAM",
            "sample_rate_hz": 1_000_000,
            "symbol_rate_hz": 250_000,
        },
    ):
        try:
            estimator.estimate(iq, **kwargs)
        except ValueError:
            pass
        else:  # pragma: no cover - assertion spelling keeps dependencies minimal
            raise AssertionError("unsupported profile must fail closed")


def test_frozen_lock_hashes_and_production_constants_match() -> None:
    config_path = ROOT / "reports" / "rml24-blind-origin-v3-frozen-config.json"
    lock = json.loads(
        (ROOT / "reports" / "rml24-blind-origin-v3-freeze-lock.json").read_text()
    )
    config = json.loads(config_path.read_text())
    assert hashlib.sha256(config_path.read_bytes()).hexdigest() == lock["config_sha256"]
    assert lock["status"] == "frozen_before_transfer_and_holdout"
    expected = {
        tuple(key.split(":", 1)): int(value["deterministic_shift_bits"])
        for key, value in config["profiles"].items()
    }
    actual = {
        (modulation, str(rate)): profile.shift_bits
        for (modulation, rate), profile in Rml24BlindOriginV3.profiles.items()
    }
    assert actual == expected
