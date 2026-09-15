from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np


SCRIPT = Path("work/rml24/benchmark_amr_feature_baseline_v1.py")
SPEC = importlib.util.spec_from_file_location("rml24_amr_feature_baseline_v1", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)

TRANSFER_SCRIPT = Path("work/rml24/audit_amr_group_transfer_v1.py")
TRANSFER_SPEC = importlib.util.spec_from_file_location(
    "rml24_amr_group_transfer_v1", TRANSFER_SCRIPT
)
assert TRANSFER_SPEC is not None and TRANSFER_SPEC.loader is not None
TRANSFER = importlib.util.module_from_spec(TRANSFER_SPEC)
TRANSFER_SPEC.loader.exec_module(TRANSFER)


def _fixture(seed: int = 7) -> np.ndarray:
    rng = np.random.default_rng(seed)
    samples = 2048
    first = np.exp(1j * rng.uniform(-np.pi, np.pi, samples))
    second = (1.0 + 0.25 * np.cos(np.arange(samples) / 11.0)) * np.exp(
        1j * np.arange(samples) * 0.17
    )
    return np.stack(
        (
            np.stack((first.real, first.imag)),
            np.stack((second.real, second.imag)),
        )
    ).astype(np.float32)


def test_features_are_deterministic_finite_and_iq_only() -> None:
    iq = _fixture()
    first = MODULE.extract_iq_features(iq)
    second = MODULE.extract_iq_features(iq.copy())
    np.testing.assert_array_equal(first, second)
    assert first.shape[0] == 2
    assert first.shape[1] >= 40
    assert np.all(np.isfinite(first))


def test_features_ignore_global_gain_phase_and_dc() -> None:
    iq = _fixture()
    z = iq[:, 0].astype(np.float64) + 1j * iq[:, 1].astype(np.float64)
    changed = 3.7 * z * np.exp(1j * 1.13) + (2.0 - 0.7j)
    changed_iq = np.stack((changed.real, changed.imag), axis=1)
    np.testing.assert_allclose(
        MODULE.extract_iq_features(iq),
        MODULE.extract_iq_features(changed_iq),
        rtol=1e-6,
        atol=1e-6,
    )


def test_metric_topk_and_confusion_are_exact() -> None:
    classes = np.asarray(["A", "B", "C"])
    labels = np.asarray(["A", "B", "C", "A"])
    scores = np.asarray(
        [
            [3.0, 2.0, 1.0],
            [2.0, 3.0, 1.0],
            [3.0, 2.0, 1.0],
            [1.0, 3.0, 2.0],
        ]
    )
    metrics = MODULE._metrics(
        scores,
        labels,
        classes,
        np.asarray([0, 0, 10, 10]),
        np.asarray([100, 100, 200, 200]),
    )
    assert metrics["top1_accuracy"] == 0.5
    assert metrics["top3_accuracy"] == 1.0
    assert metrics["confusion_matrix"]["rows_expected_columns_predicted"] == [
        [1, 1, 0],
        [0, 1, 0],
        [1, 0, 0],
    ]


def test_group_transfer_split_is_disjoint_and_balanced() -> None:
    groups = [
        {"modulation": modulation, "snr_db": index - 31, "symbol_rate_hz": 100_000}
        for modulation in ("A", "B")
        for index in range(63)
    ]
    split = TRANSFER.split_groups(groups)
    assert {key: len(value) for key, value in split.items()} == {
        "training": 88,
        "validation": 12,
        "holdout": 26,
    }
    identities = {
        name: {
            (value["modulation"], value["snr_db"], value["symbol_rate_hz"])
            for value in values
        }
        for name, values in split.items()
    }
    assert identities["training"].isdisjoint(identities["validation"])
    assert identities["training"].isdisjoint(identities["holdout"])
    assert identities["validation"].isdisjoint(identities["holdout"])
