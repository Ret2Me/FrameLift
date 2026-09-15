from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np


SCRIPT = Path(__file__).resolve().parents[1] / "work/positive-real-iq/make_legal_transfer_r5_nulls.py"
SPEC = importlib.util.spec_from_file_location("make_legal_transfer_r5_nulls", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def fixture_block() -> np.ndarray:
    return np.asarray(
        [[1, 2], [3, 4], [5, 6], [-7, 8], [9, -10], [11, 12]],
        dtype="<i2",
    )


def test_transform_seeds_are_deterministic_and_domain_separated() -> None:
    seeds = {MODULE.seed_for(123, name) for name in MODULE.TRANSFORMS}
    assert len(seeds) == len(MODULE.TRANSFORMS)
    assert MODULE.seed_for(123, MODULE.TRANSFORMS[0]) == MODULE.seed_for(123, MODULE.TRANSFORMS[0])
    assert MODULE.seed_for(123, MODULE.TRANSFORMS[0]) != MODULE.seed_for(124, MODULE.TRANSFORMS[0])


def test_transforms_are_deterministic_bounded_and_preserve_marginals() -> None:
    block = fixture_block()
    outputs = {}
    for name in MODULE.TRANSFORMS:
        left = MODULE.transform_block(
            block,
            transform=name,
            rng=np.random.Generator(np.random.PCG64(42)),
        )
        right = MODULE.transform_block(
            block,
            transform=name,
            rng=np.random.Generator(np.random.PCG64(42)),
        )
        assert left.dtype == np.dtype("<i2")
        assert left.shape == block.shape
        assert np.array_equal(left, right)
        outputs[name] = left

    source_pairs = sorted(map(tuple, block.tolist()))
    permuted_pairs = sorted(map(tuple, outputs["sample-permutation"].tolist()))
    assert permuted_pairs == source_pairs
    independent = outputs["independent-iq-permutation"]
    assert sorted(independent[:, 0].tolist()) == sorted(block[:, 0].tolist())
    assert sorted(independent[:, 1].tolist()) == sorted(block[:, 1].tolist())


def test_file_transform_is_repeatable_and_preserves_exact_size(tmp_path: Path) -> None:
    source = tmp_path / "input.raw"
    first = tmp_path / "first.raw"
    second = tmp_path / "second.raw"
    values = np.arange(2 * (MODULE.BLOCK_COMPLEX_SAMPLES + 7), dtype="<i2")
    source.write_bytes(values.tobytes())
    MODULE.write_transform(
        source,
        first,
        observation_id=55,
        transform="sample-permutation",
    )
    MODULE.write_transform(
        source,
        second,
        observation_id=55,
        transform="sample-permutation",
    )
    assert first.stat().st_size == source.stat().st_size
    assert first.read_bytes() == second.read_bytes()
    assert first.read_bytes() != source.read_bytes()
