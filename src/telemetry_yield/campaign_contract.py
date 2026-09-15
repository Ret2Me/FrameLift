"""Shared integrity contract for bounded holdout campaign processes.

This module intentionally contains no decoder logic.  It centralises terminal
status semantics and small, bounded content-identity helpers so a supervisor,
normalizer, launcher, and evaluator cannot silently disagree about failures.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat
from typing import Any, BinaryIO, Mapping


PROCESS_RECEIPT_SCHEMA_VERSION = "blind-phase-campaign-process-receipt-v1"
NORMALIZED_RESULT_SCHEMA_VERSION = "blind-phase-confirmatory-normalized-arm-result-v1"
CAMPAIGN_UNIT_SCHEMA_VERSION = "blind-phase-confirmatory-campaign-unit-v1"

TERMINAL_STATUSES = frozenset(
    {
        "completed",
        "timeout",
        "stdout_limit",
        "stderr_limit",
        "cpu_or_hard_limit",
        "exit_nonzero",
        "missing_result",
        "cleanup_failure",
        "descendant_pipe_leak",
        "result_file_size_limit",
        "memory_limit",
        "pids_limit",
        "cpu_limit",
        "descendant_cgroup_survivor",
        "cgroup_tracking_failure",
        "supervisor_failure",
        "normalization_failure",
    }
)
INCOMPLETE_TERMINAL_STATUSES = TERMINAL_STATUSES - {"completed"}


def sha256_document(value: object) -> str:
    """Hash one canonical JSON value, rejecting NaN and infinities."""

    return hashlib.sha256(
        json.dumps(
            value, allow_nan=False, separators=(",", ":"), sort_keys=True
        ).encode("utf-8")
    ).hexdigest()


def valid_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value.casefold())
    )


def _hash_stream(stream: BinaryIO, *, maximum_bytes: int | None = None) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    while True:
        block = stream.read(1024 * 1024)
        if not block:
            break
        size += len(block)
        if maximum_bytes is not None and size > maximum_bytes:
            raise ValueError("file exceeds the compiled content-identity bound")
        digest.update(block)
    return size, digest.hexdigest()


def regular_file_identity(
    path: str | Path, *, maximum_bytes: int | None = None
) -> dict[str, object]:
    """Hash a regular file through the same O_NOFOLLOW descriptor we validate."""

    absolute = Path(path).absolute()
    descriptor = os.open(absolute, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise ValueError(f"artifact must be a regular file: {absolute}")
        with os.fdopen(os.dup(descriptor), "rb", buffering=0) as stream:
            size, digest = _hash_stream(stream, maximum_bytes=maximum_bytes)
        after = os.fstat(descriptor)
        before_identity = (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        )
        after_identity = (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        )
        if before_identity != after_identity or size != before.st_size:
            raise ValueError(f"artifact changed while hashing: {absolute}")
        return {"path": str(absolute), "size_bytes": size, "sha256": digest}
    finally:
        os.close(descriptor)


def load_json_object(
    path: str | Path, *, maximum_bytes: int
) -> tuple[dict[str, Any], dict[str, object]]:
    """Read and hash one bounded JSON object through one O_NOFOLLOW FD."""

    if isinstance(maximum_bytes, bool) or not isinstance(maximum_bytes, int) or maximum_bytes < 1:
        raise ValueError("maximum_bytes must be a positive integer")
    absolute = Path(path).absolute()
    descriptor = os.open(absolute, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise ValueError(f"artifact must be a regular file: {absolute}")
        if before.st_size > maximum_bytes:
            raise ValueError("JSON file exceeds the compiled byte bound")
        payload_parts: list[bytes] = []
        digest = hashlib.sha256()
        size = 0
        while True:
            block = os.read(descriptor, min(1024 * 1024, maximum_bytes + 1 - size))
            if not block:
                break
            size += len(block)
            if size > maximum_bytes:
                raise ValueError("JSON file exceeds the compiled byte bound")
            payload_parts.append(block)
            digest.update(block)
        after = os.fstat(descriptor)
        before_identity = (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        )
        after_identity = (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        )
        if before_identity != after_identity or size != before.st_size:
            raise ValueError(f"artifact changed while reading: {absolute}")
        try:
            value = json.loads(
                b"".join(payload_parts).decode("utf-8"),
                parse_constant=lambda token: (_ for _ in ()).throw(ValueError(token)),
            )
        except UnicodeDecodeError as error:
            raise ValueError("JSON input is not UTF-8") from error
        if not isinstance(value, dict):
            raise ValueError("JSON input must be an object")
        identity = {
            "path": str(absolute),
            "size_bytes": size,
            "sha256": digest.hexdigest(),
        }
        return value, identity
    finally:
        os.close(descriptor)


def verified_self_hashed_document(
    value: object,
    *,
    hash_field: str,
    schema_field: str = "schema_version",
    schema_version: str | None = None,
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("document must be an object")
    document = dict(value)
    payload_hash = document.pop(hash_field, None)
    if not valid_sha256(payload_hash) or payload_hash != sha256_document(document):
        raise ValueError(f"{hash_field} mismatch")
    if schema_version is not None and document.get(schema_field) != schema_version:
        raise ValueError("document schema version mismatch")
    document[hash_field] = payload_hash
    return document
