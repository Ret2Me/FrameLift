from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np
import pytest

import telemetry_yield.null_transform as null_transform
from telemetry_yield.null_transform import transform_ci16_null


def _source(path: Path) -> tuple[int, str]:
    values = np.array(
        [
            [-32768, 32767],
            [-120, 250],
            [300, -450],
            [12, 34],
            [-1, -2],
            [999, -32768],
            [0, 0],
        ],
        dtype="<i2",
    )
    payload = values.tobytes()
    path.write_bytes(payload)
    return len(payload), hashlib.sha256(payload).hexdigest()


def test_zero_and_reversal_are_exact_and_content_bound(tmp_path: Path) -> None:
    source = tmp_path / "source.ci16"
    size, digest = _source(source)
    zero = tmp_path / "zero.ci16"
    reverse = tmp_path / "reverse.ci16"
    zero_result = transform_ci16_null(
        source,
        zero,
        kind="all_zero",
        expected_source_size_bytes=size,
        expected_source_sha256=digest,
        chunk_complex_samples=3,
    )
    transform_ci16_null(
        source,
        reverse,
        kind="complex_time_reversal",
        expected_source_size_bytes=size,
        expected_source_sha256=digest,
        chunk_complex_samples=3,
    )
    original = np.fromfile(source, dtype="<i2").reshape(-1, 2)
    reversed_values = np.fromfile(reverse, dtype="<i2").reshape(-1, 2)
    assert zero.read_bytes() == bytes(size)
    assert np.array_equal(reversed_values, original[::-1])
    assert zero_result.output_size_bytes == size
    assert zero_result.peak_payload_bytes <= 12


def test_phase_scramble_is_repeatable_seeded_and_preserves_coordinate_power(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.ci16"
    size, digest = _source(source)
    first = tmp_path / "first.ci16"
    second = tmp_path / "second.ci16"
    different = tmp_path / "different.ci16"
    arguments = {
        "kind": "phase_scramble",
        "expected_source_size_bytes": size,
        "expected_source_sha256": digest,
        "chunk_complex_samples": 3,
    }
    first_result = transform_ci16_null(source, first, seed=17, **arguments)
    second_result = transform_ci16_null(source, second, seed=17, **arguments)
    transform_ci16_null(source, different, seed=18, **arguments)
    original = np.fromfile(source, dtype="<i2").reshape(-1, 2).astype(np.int64)
    scrambled = np.fromfile(first, dtype="<i2").reshape(-1, 2).astype(np.int64)
    assert first.read_bytes() == second.read_bytes()
    assert first.read_bytes().hex() == (
        "01800080fa007800d4fec201deff0c00feff01000080e70300000000"
    )
    assert first_result.output_sha256 == second_result.output_sha256
    assert first.read_bytes() != different.read_bytes()
    assert np.array_equal(
        np.sort(original * original, axis=1),
        np.sort(scrambled * scrambled, axis=1),
    )


def test_transform_rejects_mutable_or_ambiguous_contracts(tmp_path: Path) -> None:
    source = tmp_path / "source.ci16"
    size, digest = _source(source)
    output = tmp_path / "output.ci16"
    with pytest.raises(ValueError, match="uint64"):
        transform_ci16_null(
            source,
            output,
            kind="phase_scramble",
            expected_source_size_bytes=size,
            expected_source_sha256=digest,
        )
    with pytest.raises(ValueError, match="SHA-256"):
        transform_ci16_null(
            source,
            output,
            kind="all_zero",
            expected_source_size_bytes=size,
            expected_source_sha256="0" * 64,
        )
    output.write_bytes(b"occupied")
    with pytest.raises(ValueError, match="already exists"):
        transform_ci16_null(
            source,
            output,
            kind="all_zero",
            expected_source_size_bytes=size,
            expected_source_sha256=digest,
        )
    with pytest.raises(ValueError, match="memory bound"):
        transform_ci16_null(
            source,
            tmp_path / "large-chunk.ci16",
            kind="all_zero",
            expected_source_size_bytes=size,
            expected_source_sha256=digest,
            chunk_complex_samples=1_048_577,
        )


def test_publish_is_atomic_no_clobber_under_race(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source.ci16"
    size, digest = _source(source)
    output = tmp_path / "output.ci16"
    original_publish = null_transform._publish_no_clobber

    def racing_publish(temporary_path: Path, output_path: Path) -> None:
        output_path.write_bytes(b"other process")
        original_publish(temporary_path, output_path)

    monkeypatch.setattr(null_transform, "_publish_no_clobber", racing_publish)
    with pytest.raises(ValueError, match="concurrently"):
        transform_ci16_null(
            source,
            output,
            kind="all_zero",
            expected_source_size_bytes=size,
            expected_source_sha256=digest,
        )
    assert output.read_bytes() == b"other process"
    assert not list(tmp_path.glob(".output.ci16.*.tmp"))


def test_source_mutation_before_publication_fails_without_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source.ci16"
    size, digest = _source(source)
    output = tmp_path / "output.ci16"
    calls = 0
    original_identity = null_transform._identity

    def mutate_after_initial_checks(status):
        nonlocal calls
        calls += 1
        if calls == 3:
            payload = bytearray(source.read_bytes())
            payload[0] ^= 1
            source.write_bytes(payload)
            return original_identity(source.lstat())
        return original_identity(status)

    monkeypatch.setattr(null_transform, "_identity", mutate_after_initial_checks)
    with pytest.raises(ValueError, match="changed"):
        transform_ci16_null(
            source,
            output,
            kind="all_zero",
            expected_source_size_bytes=size,
            expected_source_sha256=digest,
        )
    assert not output.exists()


def test_directory_fsync_failure_rolls_back_only_our_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source.ci16"
    size, digest = _source(source)
    output = tmp_path / "output.ci16"

    def failed_directory_fsync(path: Path) -> None:
        raise OSError("injected directory fsync failure")

    monkeypatch.setattr(null_transform, "_fsync_directory", failed_directory_fsync)
    with pytest.raises(OSError, match="injected"):
        transform_ci16_null(
            source,
            output,
            kind="all_zero",
            expected_source_size_bytes=size,
            expected_source_sha256=digest,
        )
    assert not output.exists()
    assert not list(tmp_path.glob(".output.ci16.*.tmp"))
