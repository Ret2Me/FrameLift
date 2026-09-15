"""Non-executing, bounded-memory conversion of the published RML24 Pickles.

The upstream Pickle files are dictionaries mapping
``(modulation, snr_db, symbol_rate_hz)`` to one NumPy ndarray per group.  Normal
``pickle.load`` would both execute Pickle globals and materialize every group.
This module instead interprets a strict opcode allow-list, permits only NumPy's
documented ndarray reconstruction pattern, and writes each array immediately.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import pickletools
import stat
from typing import Any, BinaryIO
import zipfile

from .download_guard import DownloadPlan, assert_download_allowed


SHARD_SCHEMA_VERSION = "rml24-group-shards-v1"
BIT_MEMBER = "RML24_BITdata.pkl"
IQ_MEMBER = "RML24_IQdata.pkl"
PICKLE_ARCHIVE_BYTES = 20_506_729_517
PICKLE_ARCHIVE_MD5 = "35b3d996d290fc79e04969a0653baf31"
PICKLE_MEMBER_BYTES = {
    BIT_MEMBER: 6_844_913_226,
    IQ_MEMBER: 21_676_121_625,
}
PICKLE_MEMO_BYTES_RETAIN_LIMIT = 64 * 1024
PICKLE_TOTAL_MEMO_BYTES_LIMIT = 16 * 1024 * 1024
PICKLE_MEMO_ENTRY_LIMIT = 250_000
PICKLE_STACK_ENTRY_LIMIT = 250_000
_MARK = object()


def _require_numpy() -> Any:
    try:
        import numpy as np
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("RML24 Pickle conversion requires numpy") from exc
    return np


@dataclass(frozen=True, slots=True)
class _Global:
    module: str
    name: str


@dataclass(frozen=True, slots=True)
class _Reduced:
    callable: object
    args: tuple[object, ...]


@dataclass(frozen=True, slots=True)
class _DType:
    value: str


@dataclass(frozen=True, slots=True)
class _ArrayReference:
    shape: tuple[int, ...]
    dtype: str
    filename: str
    bytes: int
    sha256: str


@dataclass(frozen=True, slots=True)
class _ReleasedMemoPayload:
    bytes: int


def _embedded_memo_bytes(value: object) -> int:
    """Count bytes retained directly or inside immutable tuple memo values.

    NumPy protocol-4 memoizes both the raw bytes and the ndarray state tuple
    containing those same bytes.  Only tuples are traversed: mutable Pickle
    containers retain their mutation semantics, and the traversal remains
    bounded by the parser's existing stack-entry limit.
    """

    pending = [value]
    seen: set[int] = set()
    total = 0
    visited = 0
    while pending:
        item = pending.pop()
        visited += 1
        if visited > PICKLE_STACK_ENTRY_LIMIT:
            raise ValueError("Pickle memo payload nesting exceeds bounded limit")
        if isinstance(item, (bytes, tuple)):
            identity = id(item)
            if identity in seen:
                continue
            seen.add(identity)
        if isinstance(item, bytes):
            total += len(item)
        elif isinstance(item, tuple):
            pending.extend(item)
            if len(pending) > PICKLE_STACK_ENTRY_LIMIT:
                raise ValueError("Pickle memo payload width exceeds bounded limit")
    return total


class _BoundedPickleReader:
    """Reject a Pickle opcode's oversized read before it can allocate the payload."""

    def __init__(self, stream: BinaryIO, *, max_read_bytes: int) -> None:
        self.stream = stream
        self.max_read_bytes = max_read_bytes

    def read(self, size: int = -1) -> bytes:
        if size < 0:
            raise ValueError("unbounded Pickle reads are forbidden")
        if size > self.max_read_bytes:
            raise ValueError(
                f"Pickle opcode read request exceeds bounded limit: "
                f"{size} > {self.max_read_bytes}"
            )
        return self.stream.read(size)

    def readline(self, size: int = -1) -> bytes:
        limit = self.max_read_bytes if size < 0 else min(size, self.max_read_bytes)
        value = self.stream.readline(limit + 1)
        if len(value) > limit:
            raise ValueError("Pickle line opcode exceeds bounded limit")
        return value


def _validate_member_name(name: str) -> None:
    path = PurePosixPath(name)
    if not name or path.is_absolute() or ".." in path.parts or "\\" in name:
        raise ValueError(f"unsafe ZIP member path: {name!r}")


def _hash_file(path: Path, *, chunk_size: int = 8 * 1024 * 1024) -> tuple[str, str]:
    md5 = hashlib.md5(usedforsecurity=False)
    sha256 = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(chunk_size), b""):
            md5.update(block)
            sha256.update(block)
    return md5.hexdigest(), sha256.hexdigest()


def _find_mark(stack: list[object]) -> int:
    for index in range(len(stack) - 1, -1, -1):
        if stack[index] is _MARK:
            return index
    raise ValueError("Pickle stack has no MARK")


def _normalise_key(value: object) -> tuple[str, int | float, int]:
    if not isinstance(value, tuple) or len(value) != 3:
        raise ValueError("RML24 dictionary key must be a three-item tuple")
    modulation, snr, rate = value
    if not isinstance(modulation, str) or not modulation or len(modulation) > 80:
        raise ValueError("invalid modulation in RML24 key")
    if not isinstance(snr, (int, float)) or isinstance(snr, bool):
        raise ValueError("invalid SNR in RML24 key")
    if not isinstance(rate, (int, float)) or isinstance(rate, bool):
        raise ValueError("invalid symbol rate in RML24 key")
    if not math.isfinite(float(snr)):
        raise ValueError("SNR must be finite")
    if not math.isfinite(float(rate)) or float(rate) <= 0 or not float(rate).is_integer():
        raise ValueError("symbol rate must be a positive integer value")
    return modulation, snr, int(rate)


def _safe_stem(key: tuple[str, int | float, int]) -> str:
    modulation, snr, rate = key
    readable = "".join(char if char.isalnum() else "-" for char in modulation).strip("-")
    digest = hashlib.sha256(
        json.dumps([modulation, snr, rate], separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:12]
    return f"{readable[:32]}_snr-{snr}_rate-{rate}_{digest}"


class _ShardSink:
    def __init__(
        self,
        directory: Path,
        role: str,
        *,
        max_array_bytes: int,
        materialize_shards: bool = True,
    ) -> None:
        if role not in {"iq", "bits"}:
            raise ValueError("role must be iq or bits")
        self.directory = directory
        self.role = role
        self.max_array_bytes = max_array_bytes
        self.materialize_shards = materialize_shards
        self.records: list[dict[str, object]] = []
        if materialize_shards:
            directory.mkdir(parents=True, exist_ok=True)

    def write(
        self,
        key_value: object,
        shape_value: object,
        dtype_value: object,
        fortran_order: object,
        raw_value: object,
    ) -> _ArrayReference:
        np = _require_numpy()
        key = _normalise_key(key_value)
        if (
            not isinstance(shape_value, tuple)
            or not shape_value
            or len(shape_value) > 8
            or any(
                not isinstance(item, int) or isinstance(item, bool) or item <= 0
                for item in shape_value
            )
        ):
            raise ValueError("invalid ndarray shape")
        shape = tuple(int(item) for item in shape_value)
        if not isinstance(dtype_value, _DType):
            raise ValueError("ndarray dtype was not reconstructed by the strict parser")
        dtype = np.dtype(dtype_value.value)
        if dtype.hasobject or dtype.kind not in "biufc" or dtype.itemsize > 16:
            raise ValueError(f"unsafe or unsupported ndarray dtype {dtype}")
        if not isinstance(fortran_order, bool):
            raise ValueError("ndarray Fortran flag must be boolean")
        if not isinstance(raw_value, bytes):
            raise ValueError("ndarray payload must use a bytes opcode")
        expected = math.prod(shape) * dtype.itemsize
        if expected > self.max_array_bytes:
            raise ValueError(f"one ndarray exceeds bounded conversion limit: {expected}")
        if expected != len(raw_value):
            raise ValueError(f"ndarray payload length mismatch: {len(raw_value)} != {expected}")
        filename = f"{self.role}/{_safe_stem(key)}.npy"
        if self.materialize_shards:
            array = np.frombuffer(raw_value, dtype=dtype).reshape(
                shape,
                order="F" if fortran_order else "C",
            )
            path = self.directory.parent / filename
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_name(path.name + ".part")
            with temporary.open("wb") as output:
                np.save(output, array, allow_pickle=False)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, path)
            digest = hashlib.sha256()
            with path.open("rb") as handle:
                for block in iter(lambda: handle.read(8 * 1024 * 1024), b""):
                    digest.update(block)
            digest_value = digest.hexdigest()
            file_bytes = path.stat().st_size
        else:
            filename = f"probe-only/{filename}"
            digest_value = hashlib.sha256(raw_value).hexdigest()
            file_bytes = 0
        record = {
            "modulation": key[0],
            "snr_db": key[1],
            "symbol_rate_hz": key[2],
            "role": self.role,
            "file": filename,
            "shape": list(shape),
            "dtype": dtype.str,
            "array_payload_bytes": expected,
            "file_bytes": file_bytes,
            "sha256": digest_value,
        }
        if not self.materialize_shards:
            record["probe_only"] = True
        self.records.append(record)
        return _ArrayReference(shape, dtype.str, filename, expected, digest_value)


def _build_value(instance: object, state: object, stack: list[object], sink: _ShardSink) -> object:
    np = _require_numpy()
    if isinstance(instance, _Reduced) and isinstance(instance.callable, _Global):
        global_ref = instance.callable
        if global_ref.module == "numpy" and global_ref.name == "dtype":
            if not instance.args or not isinstance(instance.args[0], str):
                raise ValueError("invalid NumPy dtype reduction")
            dtype = np.dtype(instance.args[0])
            if isinstance(state, tuple) and len(state) >= 2 and isinstance(state[1], str):
                byteorder = state[1]
                if byteorder in {"<", ">", "=", "|"}:
                    dtype = dtype.newbyteorder(byteorder)
            return _DType(dtype.str)
        if global_ref.name == "_reconstruct" and global_ref.module in {
            "numpy.core.multiarray",
            "numpy._core.multiarray",
        }:
            if not isinstance(state, tuple) or len(state) != 5:
                raise ValueError("unexpected NumPy ndarray state")
            if not stack:
                raise ValueError("ndarray has no preceding RML24 dictionary key")
            _, shape, dtype, fortran_order, raw = state
            return sink.write(stack[-1], shape, dtype, fortran_order, raw)
    raise ValueError("BUILD is allowed only for NumPy dtype and ndarray values")


def _interpret_rml24_pickle_stream(
    stream: BinaryIO,
    output_root: Path,
    *,
    role: str,
    max_array_bytes: int = 64 * 1024 * 1024,
    stats: dict[str, int] | None = None,
    record_limit: int | None = None,
    materialize_shards: bool = True,
) -> tuple[dict[str, object], ...]:
    """Interpret a strict RML24/NumPy Pickle subset without invoking pickle.load."""

    if max_array_bytes <= 0:
        raise ValueError("max_array_bytes must be positive")
    if record_limit is not None and record_limit <= 0:
        raise ValueError("record_limit must be positive")
    sink = _ShardSink(
        output_root / role,
        role,
        max_array_bytes=max_array_bytes,
        materialize_shards=materialize_shards,
    )
    stack: list[object] = []
    memo: dict[int, object] = {}
    build_memo_aliases: dict[int, set[int]] = {}
    next_memo = 0
    stopped = False
    record_limit_reached = False
    suppressed_count = 0
    suppressed_bytes = 0
    nested_suppressed_count = 0
    nested_suppressed_bytes = 0
    largest_opcode_bytes = 0
    memo_bytes_retained = 0
    peak_memo_bytes_retained = 0
    memo_retained_bytes: dict[int, int] = {}

    def memo_value(value: object) -> tuple[object, int]:
        nonlocal suppressed_count, suppressed_bytes
        nonlocal nested_suppressed_count, nested_suppressed_bytes
        nonlocal memo_bytes_retained, peak_memo_bytes_retained
        retained_bytes = _embedded_memo_bytes(value)
        if retained_bytes and (
            retained_bytes > PICKLE_MEMO_BYTES_RETAIN_LIMIT
            or memo_bytes_retained + retained_bytes > PICKLE_TOTAL_MEMO_BYTES_LIMIT
        ):
            suppressed_count += 1
            suppressed_bytes += retained_bytes
            if not isinstance(value, bytes):
                nested_suppressed_count += 1
                nested_suppressed_bytes += retained_bytes
            return _ReleasedMemoPayload(retained_bytes), 0
        memo_bytes_retained += retained_bytes
        peak_memo_bytes_retained = max(
            peak_memo_bytes_retained,
            memo_bytes_retained,
        )
        return value, retained_bytes

    def memo_store(index: int, value: object) -> None:
        nonlocal memo_bytes_retained
        previous = memo.get(index)
        memo_bytes_retained -= memo_retained_bytes.pop(index, 0)
        if isinstance(previous, _Reduced):
            previous_aliases = build_memo_aliases.get(id(previous))
            if previous_aliases is not None:
                previous_aliases.discard(index)
                if not previous_aliases:
                    del build_memo_aliases[id(previous)]
        if index not in memo and len(memo) >= PICKLE_MEMO_ENTRY_LIMIT:
            raise ValueError("Pickle memo entry limit exceeded")
        stored, retained_bytes = memo_value(value)
        memo[index] = stored
        if retained_bytes:
            memo_retained_bytes[index] = retained_bytes
        if isinstance(stored, _Reduced):
            build_memo_aliases.setdefault(id(stored), set()).add(index)

    noops = {"PROTO", "FRAME"}
    scalar_ops = {
        "BININT",
        "BININT1",
        "BININT2",
        "LONG1",
        "LONG4",
        "BINFLOAT",
        "SHORT_BINUNICODE",
        "BINUNICODE",
        "BINUNICODE8",
        "SHORT_BINBYTES",
        "BINBYTES",
        "BINBYTES8",
    }
    bounded_stream = _BoundedPickleReader(
        stream,
        max_read_bytes=max(max_array_bytes + 64 * 1024, 1024 * 1024),
    )
    for opcode, argument, position in pickletools.genops(bounded_stream):
        if len(stack) > PICKLE_STACK_ENTRY_LIMIT:
            raise ValueError("Pickle stack entry limit exceeded")
        name = opcode.name
        if name in noops:
            continue
        if name == "EMPTY_DICT":
            stack.append({})
        elif name == "MARK":
            stack.append(_MARK)
        elif name in scalar_ops:
            if isinstance(argument, bytes):
                largest_opcode_bytes = max(largest_opcode_bytes, len(argument))
            stack.append(argument)
        elif name == "NONE":
            stack.append(None)
        elif name == "NEWTRUE":
            stack.append(True)
        elif name == "NEWFALSE":
            stack.append(False)
        elif name in {"TUPLE1", "TUPLE2", "TUPLE3"}:
            count = int(name[-1])
            if len(stack) < count:
                raise ValueError(f"Pickle stack underflow at {position}")
            values = tuple(stack[-count:])
            del stack[-count:]
            stack.append(values)
        elif name == "TUPLE":
            mark = _find_mark(stack)
            values = tuple(stack[mark + 1 :])
            del stack[mark:]
            stack.append(values)
        elif name == "MEMOIZE":
            if not stack:
                raise ValueError(f"MEMOIZE with empty stack at {position}")
            memo_store(next_memo, stack[-1])
            next_memo += 1
        elif name in {"BINPUT", "LONG_BINPUT"}:
            if not stack:
                raise ValueError(f"memo write with empty stack at {position}")
            memo_store(int(argument), stack[-1])
            next_memo = max(next_memo, int(argument) + 1)
        elif name in {"BINGET", "LONG_BINGET"}:
            index = int(argument)
            if index not in memo:
                raise ValueError(f"unknown Pickle memo index {index} at {position}")
            value = memo[index]
            if isinstance(value, _ReleasedMemoPayload):
                raise ValueError(
                    "Pickle attempts to reuse a released payload containing large bytes; "
                    "bounded conversion refuses shared raw array buffers or states"
                )
            stack.append(value)
        elif name == "STACK_GLOBAL":
            if len(stack) < 2 or not isinstance(stack[-2], str) or not isinstance(stack[-1], str):
                raise ValueError(f"invalid STACK_GLOBAL at {position}")
            module, global_name = stack[-2], stack[-1]
            del stack[-2:]
            allowed = (
                (
                    module in {"numpy.core.multiarray", "numpy._core.multiarray"}
                    and global_name == "_reconstruct"
                )
                or (module == "numpy" and global_name in {"ndarray", "dtype"})
            )
            if not allowed:
                raise ValueError(f"forbidden Pickle global {module}.{global_name}")
            stack.append(_Global(module, global_name))
        elif name == "GLOBAL":
            module, global_name = str(argument).split(" ", 1)
            if not (
                (
                    module in {"numpy.core.multiarray", "numpy._core.multiarray"}
                    and global_name == "_reconstruct"
                )
                or (module == "numpy" and global_name in {"ndarray", "dtype"})
            ):
                raise ValueError(f"forbidden Pickle global {module}.{global_name}")
            stack.append(_Global(module, global_name))
        elif name == "REDUCE":
            if len(stack) < 2 or not isinstance(stack[-1], tuple):
                raise ValueError(f"invalid REDUCE at {position}")
            args = stack.pop()
            callable_value = stack.pop()
            stack.append(_Reduced(callable_value, args))
        elif name == "BUILD":
            if len(stack) < 2:
                raise ValueError(f"invalid BUILD at {position}")
            state = stack.pop()
            instance = stack.pop()
            built = _build_value(instance, state, stack, sink)
            # CPython's BUILD mutates an already memoized object.  Our strict
            # interpreter uses immutable placeholders, so update only memo
            # entries that are identity aliases of this validated reduction.
            # The reverse index is bounded by PICKLE_MEMO_ENTRY_LIMIT and
            # avoids an O(groups * memo_entries) scan of the published file.
            for memo_index in build_memo_aliases.pop(id(instance), set()):
                if memo.get(memo_index) is instance:
                    memo[memo_index] = built
            stack.append(built)
            if record_limit is not None and len(sink.records) >= record_limit:
                record_limit_reached = True
                break
        elif name == "SETITEMS":
            mark = _find_mark(stack)
            if mark == 0 or not isinstance(stack[mark - 1], dict):
                raise ValueError(f"SETITEMS target is not a dictionary at {position}")
            items = stack[mark + 1 :]
            if len(items) % 2:
                raise ValueError(f"SETITEMS has an odd item count at {position}")
            mapping = stack[mark - 1]
            assert isinstance(mapping, dict)
            for index in range(0, len(items), 2):
                key = _normalise_key(items[index])
                if not isinstance(items[index + 1], _ArrayReference):
                    raise ValueError("RML24 dictionary value is not an extracted ndarray")
                mapping[key] = items[index + 1]
            del stack[mark:]
        elif name == "SETITEM":
            if len(stack) < 3 or not isinstance(stack[-3], dict):
                raise ValueError(f"invalid SETITEM at {position}")
            value = stack.pop()
            key = _normalise_key(stack.pop())
            if not isinstance(value, _ArrayReference):
                raise ValueError("RML24 dictionary value is not an extracted ndarray")
            mapping = stack[-1]
            assert isinstance(mapping, dict)
            mapping[key] = value
        elif name == "STOP":
            stopped = True
            break
        else:
            raise ValueError(f"unsupported Pickle opcode {name} at {position}")
    if not stopped and not record_limit_reached:
        raise ValueError("Pickle stream ended without STOP")
    if not record_limit_reached and (not stack or not isinstance(stack[-1], dict)):
        raise ValueError("Pickle did not produce the expected dictionary")
    if stats is not None:
        stats["large_memo_payloads_suppressed"] = (
            stats.get("large_memo_payloads_suppressed", 0) + suppressed_count
        )
        stats["large_memo_payload_bytes_suppressed"] = (
            stats.get("large_memo_payload_bytes_suppressed", 0) + suppressed_bytes
        )
        stats["nested_memo_payloads_suppressed"] = (
            stats.get("nested_memo_payloads_suppressed", 0) + nested_suppressed_count
        )
        stats["nested_memo_payload_bytes_suppressed"] = (
            stats.get("nested_memo_payload_bytes_suppressed", 0)
            + nested_suppressed_bytes
        )
        stats["largest_pickle_opcode_bytes"] = max(
            stats.get("largest_pickle_opcode_bytes", 0),
            largest_opcode_bytes,
        )
        stats["peak_pickle_memo_entries"] = max(
            stats.get("peak_pickle_memo_entries", 0),
            len(memo),
        )
        stats["peak_retained_pickle_memo_bytes"] = max(
            stats.get("peak_retained_pickle_memo_bytes", 0),
            peak_memo_bytes_retained,
        )
        if record_limit is not None:
            stats["probe_record_limit"] = record_limit
            stats["probe_records_read"] = len(sink.records)
            stats["probe_stopped_before_pickle_stop"] = int(record_limit_reached)
    return tuple(sink.records)


def extract_rml24_pickle_stream(
    stream: BinaryIO,
    output_root: Path,
    *,
    role: str,
    max_array_bytes: int = 64 * 1024 * 1024,
    stats: dict[str, int] | None = None,
) -> tuple[dict[str, object], ...]:
    """Strictly convert a complete Pickle stream into materialized NPY shards."""

    return _interpret_rml24_pickle_stream(
        stream,
        output_root,
        role=role,
        max_array_bytes=max_array_bytes,
        stats=stats,
    )


def probe_rml24_pickle_stream(
    stream: BinaryIO,
    *,
    role: str,
    record_limit: int,
    max_array_bytes: int = 64 * 1024 * 1024,
    stats: dict[str, int] | None = None,
) -> tuple[dict[str, object], ...]:
    """Parse and validate a bounded prefix without writing shards.

    This is an instrumentation-only path: it stops only after a complete
    validated ndarray BUILD and marks every returned record ``probe_only``.
    The production converter always uses the complete-stream function above.
    """

    return _interpret_rml24_pickle_stream(
        stream,
        Path("."),
        role=role,
        max_array_bytes=max_array_bytes,
        stats=stats,
        record_limit=record_limit,
        materialize_shards=False,
    )


def _atomic_json(path: Path, value: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def convert_rml24_pickle_zip(
    archive: Path,
    output_root: Path,
    *,
    manifest_path: Path | None = None,
    max_array_bytes: int = 64 * 1024 * 1024,
    plan: DownloadPlan | None = None,
    expected_archive_bytes: int | None = None,
    expected_archive_md5: str | None = None,
    expected_member_bytes: dict[str, int] | None = None,
) -> dict[str, object]:
    """Convert both RML24 members into independently memory-mappable group shards."""

    archive = Path(archive)
    output_root = Path(output_root)
    destination = manifest_path or output_root / "manifest.json"
    if not archive.is_file():
        raise FileNotFoundError(archive)
    archive_bytes = archive.stat().st_size
    if expected_archive_bytes is not None and archive_bytes != expected_archive_bytes:
        raise ValueError(
            f"archive size mismatch: {archive_bytes} != {expected_archive_bytes}"
        )
    archive_md5, archive_sha256 = _hash_file(archive)
    if expected_archive_md5 is not None and archive_md5 != expected_archive_md5.lower():
        raise ValueError("archive MD5 differs from the published checksum")

    records_by_key: dict[tuple[str, int | float, int], dict[str, object]] = {}
    parse_stats: dict[str, int] = {}
    with zipfile.ZipFile(archive) as bundle:
        members = bundle.infolist()
        for item in members:
            _validate_member_name(item.filename)
        for required in (BIT_MEMBER, IQ_MEMBER):
            if sum(item.filename == required for item in members) != 1:
                raise ValueError(
                    f"required Pickle member {required!r} must occur exactly once"
                )
        infos = {item.filename: item for item in members}
        for required in (BIT_MEMBER, IQ_MEMBER):
            info = infos[required]
            if info.is_dir():
                raise ValueError("required Pickle member is a directory")
            unix_mode = (info.external_attr >> 16) & 0xFFFF
            if unix_mode and stat.S_IFMT(unix_mode) == stat.S_IFLNK:
                raise ValueError("required Pickle member must not be a symbolic link")
            if info.flag_bits & 0x1:
                raise ValueError("encrypted Pickle members are not supported")
            if info.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}:
                raise ValueError("unsupported ZIP compression method")
            if expected_member_bytes is not None:
                expected = expected_member_bytes.get(required)
                if expected is None or info.file_size != expected:
                    raise ValueError(
                        f"Pickle member size mismatch for {required}: "
                        f"{info.file_size} != {expected}"
                    )
        derived_bytes = infos[BIT_MEMBER].file_size + infos[IQ_MEMBER].file_size
        assert_download_allowed(
            "local:rml24:pickle-convert",
            derived_bytes,
            output_root,
            plan=plan,
        )
        _atomic_json(
            destination,
            {
                "schema_version": SHARD_SCHEMA_VERSION,
                "generated_at": datetime.now(UTC).isoformat(),
                "source_archive": str(archive),
                "source_archive_bytes": archive_bytes,
                "source_archive_md5": archive_md5,
                "source_archive_sha256": archive_sha256,
                "status": "converting",
                "complete": False,
                "groups": [],
            },
        )
        try:
            for member, role in ((BIT_MEMBER, "bits"), (IQ_MEMBER, "iq")):
                info = bundle.getinfo(member)
                with bundle.open(info, "r") as stream:
                    records = extract_rml24_pickle_stream(
                        stream,
                        output_root,
                        role=role,
                        max_array_bytes=max_array_bytes,
                        stats=parse_stats,
                    )
                for record in records:
                    snr = record["snr_db"]
                    if not isinstance(snr, (int, float)) or isinstance(snr, bool):
                        raise ValueError("converted RML24 SNR is not numeric")
                    key = (
                        str(record["modulation"]),
                        snr,
                        int(record["symbol_rate_hz"]),
                    )
                    group = records_by_key.setdefault(
                        key,
                        {
                            "modulation": record["modulation"],
                            "snr_db": record["snr_db"],
                            "symbol_rate_hz": record["symbol_rate_hz"],
                        },
                    )
                    if role in group:
                        raise ValueError(f"duplicate {role} array for RML24 group {key}")
                    group[role] = {
                        field: value
                        for field, value in record.items()
                        if field not in {"modulation", "snr_db", "symbol_rate_hz", "role"}
                    }
        except Exception as error:
            _atomic_json(
                destination,
                {
                    "schema_version": SHARD_SCHEMA_VERSION,
                    "generated_at": datetime.now(UTC).isoformat(),
                    "source_archive": str(archive),
                    "source_archive_bytes": archive_bytes,
                    "source_archive_md5": archive_md5,
                    "source_archive_sha256": archive_sha256,
                    "status": "error",
                    "error": f"{type(error).__name__}: {error}",
                    "complete": False,
                    "groups": [],
                },
            )
            raise
    groups = [records_by_key[key] for key in sorted(records_by_key)]
    paired = sum("iq" in group and "bits" in group for group in groups)
    shard_records = [
        role
        for group in groups
        for role_name in ("bits", "iq")
        if isinstance((role := group.get(role_name)), dict)
    ]
    document: dict[str, object] = {
        "schema_version": SHARD_SCHEMA_VERSION,
        "generated_at": datetime.now(UTC).isoformat(),
        "source_archive": str(archive),
        "source_archive_bytes": archive_bytes,
        "source_archive_md5": archive_md5,
        "source_archive_sha256": archive_sha256,
        "pickle_executed": False,
        "strict_opcode_allowlist": True,
        "pickle_opcode_reads_bounded": True,
        "pickle_memo_bytes_retain_limit": PICKLE_MEMO_BYTES_RETAIN_LIMIT,
        "pickle_total_memo_bytes_limit": PICKLE_TOTAL_MEMO_BYTES_LIMIT,
        "pickle_memo_entry_limit": PICKLE_MEMO_ENTRY_LIMIT,
        "pickle_stack_entry_limit": PICKLE_STACK_ENTRY_LIMIT,
        **parse_stats,
        "max_array_bytes": max_array_bytes,
        "peak_array_payload_bytes": max(
            (int(item["array_payload_bytes"]) for item in shard_records),
            default=0,
        ),
        "total_array_payload_bytes": sum(
            int(item["array_payload_bytes"]) for item in shard_records
        ),
        "total_shard_file_bytes": sum(int(item["file_bytes"]) for item in shard_records),
        "group_count": len(groups),
        "paired_group_count": paired,
        "status": "complete" if paired == len(groups) and bool(groups) else "incomplete",
        "complete": paired == len(groups) and bool(groups),
        "groups": groups,
    }
    _atomic_json(destination, document)
    return document


def convert_published_rml24_pickle_zip(
    archive: Path,
    output_root: Path,
    *,
    manifest_path: Path | None = None,
    max_array_bytes: int = 64 * 1024 * 1024,
    plan: DownloadPlan,
) -> dict[str, object]:
    """Convert the exact Zenodo 17800058 Pickle artifact, fail-closed on drift."""

    return convert_rml24_pickle_zip(
        archive,
        output_root,
        manifest_path=manifest_path,
        max_array_bytes=max_array_bytes,
        plan=plan,
        expected_archive_bytes=PICKLE_ARCHIVE_BYTES,
        expected_archive_md5=PICKLE_ARCHIVE_MD5,
        expected_member_bytes=PICKLE_MEMBER_BYTES,
    )
