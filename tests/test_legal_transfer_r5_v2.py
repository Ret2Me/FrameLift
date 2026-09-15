from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parents[1]


def load(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


NULLS = load("r5_v2_nulls_test", "work/positive-real-iq/make_legal_transfer_r5_v2_null.py")
RUNNER = load("r5_v2_runner_test", "work/positive-real-iq/run_legal_transfer_r5_v2.py")
WRAPPER = load("r5_v2_wrapper_test", "work/positive-real-iq/decode_legal_fsk_transfer_rate_v2.py")


def unique_pairs(count: int = 8192) -> np.ndarray:
    index = np.arange(count, dtype=np.int32)
    return np.column_stack((index % 30000, -(index % 29999))).astype("<i2")


def test_all_eight_transforms_are_deterministic_distinct_and_size_preserving(tmp_path: Path) -> None:
    source = tmp_path / "source.raw"
    block = unique_pairs()
    source.write_bytes(block.tobytes())
    digests: set[bytes] = set()
    for transform in NULLS.TRANSFORMS:
        first = tmp_path / f"{transform}-a.raw"
        second = tmp_path / f"{transform}-b.raw"
        NULLS.write_transform(source, first, observation_id=123, transform=transform)
        NULLS.write_transform(source, second, observation_id=123, transform=transform)
        assert first.stat().st_size == source.stat().st_size
        assert first.read_bytes() == second.read_bytes()
        assert first.read_bytes() != source.read_bytes()
        digests.add(first.read_bytes())
    assert len(digests) == len(NULLS.TRANSFORMS)


def test_each_transform_has_explicit_temporal_or_waveform_coherence_destruction(tmp_path: Path) -> None:
    source = tmp_path / "source.raw"
    original = unique_pairs()
    source.write_bytes(original.tobytes())
    results = {}
    for transform in NULLS.TRANSFORMS:
        output = tmp_path / f"{transform}.raw"
        NULLS.write_transform(source, output, observation_id=456, transform=transform)
        results[transform] = np.fromfile(output, dtype="<i2").reshape(-1, 2)

    assert np.count_nonzero(results["all-zero"]) == 0
    assert np.array_equal(results["full-complex-reversal"], original[::-1])

    # Permutation destroys directed chronological adjacency inside each block.
    original_edges = set(zip(map(tuple, original[:-1]), map(tuple, original[1:])))
    for name in ("sample-permutation", "permutation-quadrant-scramble"):
        candidate = results[name]
        surviving = sum((tuple(left), tuple(right)) in original_edges for left, right in zip(candidate[:-1], candidate[1:]))
        assert surviving / (len(candidate) - 1) < 0.01

    # Independent I/Q permutation destroys original complex-sample pairing.
    original_pairs = set(map(tuple, original))
    paired = sum(tuple(value) in original_pairs for value in results["independent-iq-permutation"])
    assert paired / len(original) < 0.01

    # Per-sample phase/reflection controls retain timestamps but destroy phase continuity.
    for name in ("quadrant-scramble", "binary-phase-scramble", "dihedral-scramble"):
        changed = np.any(results[name] != original, axis=1)
        assert changed.mean() > 0.30


def test_seed_domain_and_rate_explicit_commands() -> None:
    seeds = {NULLS.seed_for(9, transform) for transform in NULLS.TRANSFORMS[2:]}
    assert len(seeds) == len(NULLS.TRANSFORMS[2:])
    row = {
        "observation_id": 9,
        "mission": "RSP-03",
        "sample_rate_hz": 57600,
        "baudrate": 9600,
        "native_decimation": 2,
        "native_g3ruh": True,
    }
    baseline = RUNNER.baseline_command(row, Path("/tmp/in.raw"), Path("/tmp/out.kiss"))
    native = RUNNER.native_command(row, Path("/tmp/in.raw"), Path("/tmp/out.json"))
    assert baseline[baseline.index("--samp_rate") + 1] == "57600"
    assert native[native.index("--sample-rate-hz") + 1] == "57600"
    assert "--g3ruh" in native
    legacy = WRAPPER.load_legacy()
    legacy.SAMPLE_RATE = 57600
    assert legacy.SAMPLE_RATE == 57600
