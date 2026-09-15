"""Bounded-memory, content-bound CI16 null transformations.

The transformations in this module are controls for receiver evaluation, not
signal preprocessing.  They preserve the byte length of a capture while
removing the original forward-time telemetry hypothesis in a deterministic,
replayable way.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import stat
import tempfile
from typing import Literal


NullTransformKind = Literal["all_zero", "complex_time_reversal", "phase_scramble"]
TRANSFORM_VERSION = "telemetry-yield-ci16-null-transform-v1"
DEFAULT_CHUNK_COMPLEX_SAMPLES = 1_048_576
MAXIMUM_CHUNK_COMPLEX_SAMPLES = 1_048_576
_SHA256_CHUNK_BYTES = 1024 * 1024


def _valid_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value.casefold())
    )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb", buffering=0) as stream:
        for chunk in iter(lambda: stream.read(_SHA256_CHUNK_BYTES), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _regular_ci16(path: Path) -> os.stat_result:
    try:
        status = path.lstat()
    except FileNotFoundError as error:
        raise ValueError("CI16 source does not exist") from error
    if stat.S_ISLNK(status.st_mode) or not stat.S_ISREG(status.st_mode):
        raise ValueError("CI16 source must be a regular non-symlink file")
    if status.st_size <= 0 or status.st_size % 4:
        raise ValueError("CI16 source must contain complete non-empty I/Q pairs")
    return status


def _identity(status: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        status.st_dev,
        status.st_ino,
        status.st_size,
        status.st_mtime_ns,
        status.st_ctime_ns,
    )


def _fsync_directory(path: Path) -> None:
    directory_descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory_descriptor)
    finally:
        os.close(directory_descriptor)


def _publish_no_clobber(temporary_path: Path, output_path: Path) -> None:
    """Atomically publish a same-filesystem temporary without overwriting."""

    try:
        os.link(temporary_path, output_path)
    except FileExistsError as error:
        raise ValueError("null output was created concurrently") from error
    try:
        _fsync_directory(output_path.parent)
    except OSError:
        # The link is already visible.  Roll it back only if it still names
        # our temporary inode; never delete a concurrent replacement.
        try:
            temporary_status = temporary_path.stat(follow_symlinks=False)
            output_status = output_path.stat(follow_symlinks=False)
            if (
                temporary_status.st_dev == output_status.st_dev
                and temporary_status.st_ino == output_status.st_ino
            ):
                output_path.unlink()
                try:
                    _fsync_directory(output_path.parent)
                except OSError:
                    pass
        except FileNotFoundError:
            pass
        raise


def _twos_complement_negate(values):
    """Negate int16 values with defined two's-complement endpoint behaviour."""

    import numpy as np

    widened = values.astype(np.int32, copy=False)
    return ((-widened) & 0xFFFF).astype(np.uint16).view(np.int16)


@dataclass(frozen=True, slots=True)
class NullTransformResult:
    transform_version: str
    kind: NullTransformKind
    seed: int | None
    source_path: str
    source_size_bytes: int
    source_sha256: str
    output_path: str
    output_size_bytes: int
    output_sha256: str
    chunk_complex_samples: int
    peak_payload_bytes: int

    def to_dict(self) -> dict[str, str | int | None]:
        return {
            "transform_version": self.transform_version,
            "kind": self.kind,
            "seed": self.seed,
            "source_path": self.source_path,
            "source_size_bytes": self.source_size_bytes,
            "source_sha256": self.source_sha256,
            "output_path": self.output_path,
            "output_size_bytes": self.output_size_bytes,
            "output_sha256": self.output_sha256,
            "chunk_complex_samples": self.chunk_complex_samples,
            "peak_payload_bytes": self.peak_payload_bytes,
        }


def transform_ci16_null(
    source: str | Path,
    output: str | Path,
    *,
    kind: NullTransformKind,
    expected_source_size_bytes: int,
    expected_source_sha256: str,
    seed: int | None = None,
    chunk_complex_samples: int = DEFAULT_CHUNK_COMPLEX_SAMPLES,
) -> NullTransformResult:
    """Materialize one deterministic null control with bounded memory.

    ``phase_scramble`` applies an independent pseudorandom quarter-turn to each
    complex sample.  Its PCG64 stream and chunk size are part of the contract.
    Coordinate magnitudes, including the int16 endpoint, are preserved exactly.
    """

    if kind not in ("all_zero", "complex_time_reversal", "phase_scramble"):
        raise ValueError("unknown CI16 null transformation")
    if (
        isinstance(expected_source_size_bytes, bool)
        or not isinstance(expected_source_size_bytes, int)
        or expected_source_size_bytes <= 0
        or expected_source_size_bytes % 4
    ):
        raise ValueError("expected_source_size_bytes must be a positive multiple of four")
    if not _valid_sha256(expected_source_sha256):
        raise ValueError("expected_source_sha256 must be a SHA-256 digest")
    if (
        isinstance(chunk_complex_samples, bool)
        or not isinstance(chunk_complex_samples, int)
        or not 1 <= chunk_complex_samples <= MAXIMUM_CHUNK_COMPLEX_SAMPLES
    ):
        raise ValueError(
            "chunk_complex_samples must be a positive integer within the memory bound"
        )
    if kind == "phase_scramble":
        if (
            isinstance(seed, bool)
            or not isinstance(seed, int)
            or not 0 <= seed < 2**64
        ):
            raise ValueError("phase_scramble requires a uint64 seed")
    elif seed is not None:
        raise ValueError("seed is only valid for phase_scramble")

    source_path = Path(source).absolute()
    output_path = Path(output).absolute()
    if source_path == output_path:
        raise ValueError("null output must differ from its source")
    if output_path.exists() or output_path.is_symlink():
        raise ValueError("null output already exists")
    initial = _regular_ci16(source_path)
    if initial.st_size != expected_source_size_bytes:
        raise ValueError("CI16 source size does not match the expected size")
    initial_identity = _identity(initial)
    source_sha256 = _sha256_file(source_path)
    if source_sha256 != expected_source_sha256.casefold():
        raise ValueError("CI16 source SHA-256 does not match the expected digest")
    if _identity(_regular_ci16(source_path)) != initial_identity:
        raise ValueError("CI16 source changed while hashing")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{output_path.name}.", suffix=".tmp", dir=output_path.parent
    )
    temporary_path = Path(temporary)
    digest = hashlib.sha256()
    chunk_bytes = chunk_complex_samples * 4
    peak_payload_bytes = 0
    try:
        with os.fdopen(descriptor, "wb", buffering=0) as destination:
            if kind == "all_zero":
                remaining = initial.st_size
                zeroes = bytes(min(chunk_bytes, remaining))
                while remaining:
                    payload = zeroes[: min(len(zeroes), remaining)]
                    destination.write(payload)
                    digest.update(payload)
                    peak_payload_bytes = max(peak_payload_bytes, len(payload))
                    remaining -= len(payload)
            elif kind == "complex_time_reversal":
                import numpy as np

                with source_path.open("rb", buffering=0) as stream:
                    remaining = initial.st_size
                    while remaining:
                        size = min(chunk_bytes, remaining)
                        start = remaining - size
                        stream.seek(start)
                        payload = stream.read(size)
                        if len(payload) != size:
                            raise ValueError("CI16 source changed during reversal")
                        # Reverse opaque four-byte complex records.  The V4
                        # dtype avoids host-endian interpretation and NumPy
                        # performs the bulk copy instead of a Python loop.
                        reversed_payload = (
                            np.frombuffer(payload, dtype="V4")[::-1].tobytes()
                        )
                        destination.write(reversed_payload)
                        digest.update(reversed_payload)
                        peak_payload_bytes = max(
                            peak_payload_bytes, len(payload) + len(reversed_payload)
                        )
                        remaining = start
            else:
                import numpy as np

                generator = np.random.Generator(np.random.PCG64(seed))
                with source_path.open("rb", buffering=0) as stream:
                    while True:
                        payload = stream.read(chunk_bytes)
                        if not payload:
                            break
                        if len(payload) % 4:
                            raise ValueError("CI16 source changed during phase scramble")
                        raw = np.frombuffer(payload, dtype="<i2").reshape(-1, 2)
                        rotations = generator.integers(
                            0, 4, size=len(raw), dtype=np.uint8
                        )
                        transformed = np.empty_like(raw)
                        negative_i = _twos_complement_negate(raw[:, 0])
                        negative_q = _twos_complement_negate(raw[:, 1])
                        masks = tuple(rotations == value for value in range(4))
                        transformed[masks[0], 0] = raw[masks[0], 0]
                        transformed[masks[0], 1] = raw[masks[0], 1]
                        transformed[masks[1], 0] = negative_q[masks[1]]
                        transformed[masks[1], 1] = raw[masks[1], 0]
                        transformed[masks[2], 0] = negative_i[masks[2]]
                        transformed[masks[2], 1] = negative_q[masks[2]]
                        transformed[masks[3], 0] = raw[masks[3], 1]
                        transformed[masks[3], 1] = negative_i[masks[3]]
                        output_payload = transformed.astype("<i2", copy=False).tobytes()
                        destination.write(output_payload)
                        digest.update(output_payload)
                        peak_payload_bytes = max(
                            peak_payload_bytes,
                            len(payload)
                            + rotations.nbytes
                            + transformed.nbytes
                            + negative_i.nbytes
                            + negative_q.nbytes
                            + sum(mask.nbytes for mask in masks)
                            + len(output_payload),
                        )
            destination.flush()
            os.fsync(destination.fileno())
        if _identity(_regular_ci16(source_path)) != initial_identity:
            raise ValueError("CI16 source changed during null transformation")
        if _sha256_file(source_path) != source_sha256:
            raise ValueError("CI16 source content changed during null transformation")
        temporary_status = temporary_path.stat()
        if temporary_status.st_size != initial.st_size:
            raise ValueError("null transformation changed the capture byte length")
        _publish_no_clobber(temporary_path, output_path)
    finally:
        temporary_path.unlink(missing_ok=True)

    return NullTransformResult(
        transform_version=TRANSFORM_VERSION,
        kind=kind,
        seed=seed,
        source_path=str(source_path),
        source_size_bytes=initial.st_size,
        source_sha256=source_sha256,
        output_path=str(output_path),
        output_size_bytes=initial.st_size,
        output_sha256=digest.hexdigest(),
        chunk_complex_samples=chunk_complex_samples,
        peak_payload_bytes=peak_payload_bytes,
    )
