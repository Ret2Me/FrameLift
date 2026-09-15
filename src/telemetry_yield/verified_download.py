"""Resumable, checksum-verified downloads for large named research artifacts."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
import hashlib
from http.client import IncompleteRead
import json
import os
from pathlib import Path
import re
import time
from typing import Any
from urllib.request import Request, urlopen

from .download_guard import DownloadPlan, assert_download_allowed


MANIFEST_SCHEMA_VERSION = "verified-download-campaign-v1"


@dataclass(frozen=True, slots=True)
class ArtifactSpec:
    name: str
    url: str
    expected_size_bytes: int
    expected_md5: str

    def __post_init__(self) -> None:
        if Path(self.name).name != self.name or not self.name:
            raise ValueError("artifact name must be one safe path component")
        if not self.url.startswith(("https://", "http://")):
            raise ValueError("artifact URL must use HTTP(S)")
        if self.expected_size_bytes <= 0:
            raise ValueError("expected_size_bytes must be positive")
        if re.fullmatch(r"[0-9a-fA-F]{32}", self.expected_md5) is None:
            raise ValueError("expected_md5 must be 32 hexadecimal characters")


@dataclass(frozen=True, slots=True)
class ArtifactResult:
    name: str
    path: str
    status: str
    size_bytes: int | None
    expected_size_bytes: int
    md5: str | None
    expected_md5: str
    sha256: str | None
    resumed_from_bytes: int
    attempts: int
    error: str | None = None

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def hash_file_pair(path: Path, *, chunk_size: int = 8 * 1024 * 1024) -> tuple[str, str]:
    """Return MD5 (repository compatibility) and SHA-256 in one bounded pass."""

    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")
    md5 = hashlib.md5(usedforsecurity=False)
    sha256 = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(chunk_size), b""):
            md5.update(block)
            sha256.update(block)
    return md5.hexdigest(), sha256.hexdigest()


def _atomic_json(path: Path, document: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


class DownloadManifest:
    """Small atomic progress manifest; no downloaded bytes are held in memory."""

    def __init__(self, path: Path, specs: Sequence[ArtifactSpec]) -> None:
        self.path = path
        self.specs = tuple(specs)
        self.entries: dict[str, dict[str, object]] = {
            spec.name: {
                "name": spec.name,
                "url": spec.url,
                "expected_size_bytes": spec.expected_size_bytes,
                "expected_md5": spec.expected_md5.lower(),
                "status": "pending",
                "downloaded_bytes": 0,
            }
            for spec in specs
        }
        self.write()

    def update(self, name: str, **values: object) -> None:
        self.entries[name].update(values)
        self.write()

    def write(self) -> None:
        completed = sum(
            entry.get("status") in {"downloaded_verified", "skipped_verified"}
            for entry in self.entries.values()
        )
        document: dict[str, object] = {
            "schema_version": MANIFEST_SCHEMA_VERSION,
            "updated_at": datetime.now(UTC).isoformat(),
            "artifact_count": len(self.specs),
            "verified_count": completed,
            "complete": completed == len(self.specs),
            "expected_size_bytes": sum(item.expected_size_bytes for item in self.specs),
            "artifacts": [self.entries[item.name] for item in self.specs],
        }
        _atomic_json(self.path, document)


def _quarantine(path: Path, suffix: str) -> Path:
    target = path.with_name(path.name + suffix)
    serial = 1
    while target.exists():
        target = path.with_name(path.name + suffix + f".{serial}")
        serial += 1
    os.replace(path, target)
    return target


def _response_mode(response: Any, offset: int, expected: int) -> str:
    status = int(getattr(response, "status", response.getcode()))
    if offset == 0:
        if status not in {200, 206}:
            raise OSError(f"unexpected HTTP status {status}")
        return "wb"
    if status == 200:
        return "wb"
    if status != 206:
        raise OSError(f"range resume requires HTTP 206, received {status}")
    content_range = response.headers.get("Content-Range", "")
    match = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+|\*)", content_range)
    if match is None or int(match.group(1)) != offset:
        raise OSError("Content-Range does not start at the requested offset")
    if match.group(3) != "*" and int(match.group(3)) != expected:
        raise OSError("Content-Range total differs from expected size")
    return "ab"


def download_artifact(
    spec: ArtifactSpec,
    destination: Path,
    *,
    manifest: DownloadManifest | None = None,
    timeout_seconds: float = 60.0,
    retries: int = 50,
    retry_backoff_seconds: float = 2.0,
    chunk_size: int = 8 * 1024 * 1024,
    checkpoint_bytes: int = 256 * 1024 * 1024,
    opener: Callable[..., Any] = urlopen,
    sleeper: Callable[[float], None] = time.sleep,
) -> ArtifactResult:
    """Resume one artifact, verify size+MD5, compute SHA-256, then promote atomically."""

    if timeout_seconds <= 0 or retries < 0 or chunk_size <= 0 or checkpoint_bytes <= 0:
        raise ValueError("timeouts/chunk sizes must be positive and retries nonnegative")
    destination.mkdir(parents=True, exist_ok=True)
    final_path = destination / spec.name
    part_path = destination / f"{spec.name}.part"

    if final_path.exists():
        if final_path.stat().st_size == spec.expected_size_bytes:
            md5, sha256 = hash_file_pair(final_path, chunk_size=chunk_size)
            if md5 == spec.expected_md5.lower():
                result = ArtifactResult(
                    spec.name,
                    str(final_path),
                    "skipped_verified",
                    spec.expected_size_bytes,
                    spec.expected_size_bytes,
                    md5,
                    spec.expected_md5.lower(),
                    sha256,
                    0,
                    0,
                )
                if manifest:
                    values = {k: v for k, v in result.as_dict().items() if k != "name"}
                    manifest.update(
                        spec.name,
                        **values,
                        downloaded_bytes=spec.expected_size_bytes,
                    )
                return result
            _quarantine(final_path, ".invalid-md5")
        elif final_path.stat().st_size < spec.expected_size_bytes and not part_path.exists():
            os.replace(final_path, part_path)
        else:
            _quarantine(final_path, ".invalid-size")

    if part_path.exists() and part_path.stat().st_size > spec.expected_size_bytes:
        _quarantine(part_path, ".invalid-size")
    resumed_from = part_path.stat().st_size if part_path.exists() else 0
    last_error: Exception | None = None
    attempts = 0

    for attempt in range(retries + 1):
        attempts += 1
        offset = part_path.stat().st_size if part_path.exists() else 0
        if manifest:
            manifest.update(
                spec.name,
                status="downloading",
                downloaded_bytes=offset,
                resumed_from_bytes=resumed_from,
                attempts=attempts,
            )
        try:
            if offset < spec.expected_size_bytes:
                headers = {
                    "User-Agent": "telemetry-yield-rml24-downloader/1",
                    "Accept-Encoding": "identity",
                }
                if offset:
                    headers["Range"] = f"bytes={offset}-"
                request = Request(spec.url, headers=headers, method="GET")
                with opener(request, timeout=timeout_seconds) as response:
                    mode = _response_mode(response, offset, spec.expected_size_bytes)
                    if mode == "wb":
                        offset = 0
                    next_checkpoint = offset + checkpoint_bytes
                    with part_path.open(mode) as output:
                        while True:
                            try:
                                block = response.read(chunk_size)
                            except IncompleteRead as error:
                                if error.partial:
                                    output.write(error.partial)
                                raise
                            if not block:
                                break
                            output.write(block)
                            current = output.tell()
                            if current >= next_checkpoint:
                                output.flush()
                                os.fsync(output.fileno())
                                if manifest:
                                    manifest.update(
                                        spec.name,
                                        status="downloading",
                                        downloaded_bytes=current,
                                        resumed_from_bytes=resumed_from,
                                        attempts=attempts,
                                    )
                                next_checkpoint = current + checkpoint_bytes
                        output.flush()
                        os.fsync(output.fileno())
            actual_size = part_path.stat().st_size
            if actual_size != spec.expected_size_bytes:
                raise OSError(
                    f"incomplete download: {actual_size} != {spec.expected_size_bytes}"
                )
            if manifest:
                manifest.update(
                    spec.name,
                    status="verifying",
                    downloaded_bytes=actual_size,
                    resumed_from_bytes=resumed_from,
                    attempts=attempts,
                )
            md5, sha256 = hash_file_pair(part_path, chunk_size=chunk_size)
            if md5 != spec.expected_md5.lower():
                bad = _quarantine(part_path, ".invalid-md5")
                raise OSError(f"MD5 mismatch; quarantined as {bad}")
            os.replace(part_path, final_path)
            result = ArtifactResult(
                spec.name,
                str(final_path),
                "downloaded_verified",
                actual_size,
                spec.expected_size_bytes,
                md5,
                spec.expected_md5.lower(),
                sha256,
                resumed_from,
                attempts,
            )
            if manifest:
                values = {k: v for k, v in result.as_dict().items() if k != "name"}
                manifest.update(spec.name, **values, downloaded_bytes=actual_size)
            return result
        except Exception as error:
            last_error = error
            if manifest:
                current = part_path.stat().st_size if part_path.exists() else 0
                manifest.update(
                    spec.name,
                    status="retrying" if attempt < retries else "error",
                    downloaded_bytes=current,
                    resumed_from_bytes=resumed_from,
                    attempts=attempts,
                    error=f"{type(error).__name__}: {error}",
                )
            if attempt < retries:
                sleeper(min(retry_backoff_seconds * (2 ** min(attempt, 5)), 60.0))

    current = part_path.stat().st_size if part_path.exists() else None
    return ArtifactResult(
        spec.name,
        str(final_path),
        "error",
        current,
        spec.expected_size_bytes,
        None,
        spec.expected_md5.lower(),
        None,
        resumed_from,
        attempts,
        f"{type(last_error).__name__}: {last_error}",
    )


def run_verified_download_campaign(
    specs: Sequence[ArtifactSpec],
    destination: Path,
    *,
    source_id: str,
    plan: DownloadPlan,
    manifest_path: Path,
    **download_options: object,
) -> tuple[ArtifactResult, ...]:
    """Capacity-check and process named artifacts sequentially and resumably."""

    if not specs:
        raise ValueError("at least one artifact is required")
    names = [item.name for item in specs]
    if len(names) != len(set(names)):
        raise ValueError("artifact names must be unique")
    expected = sum(item.expected_size_bytes for item in specs)
    assert_download_allowed(source_id, expected, destination, plan=plan)
    manifest = DownloadManifest(manifest_path, specs)
    results: list[ArtifactResult] = []
    for spec in specs:
        result = download_artifact(
            spec,
            destination,
            manifest=manifest,
            **download_options,
        )
        results.append(result)
    manifest.write()
    return tuple(results)
