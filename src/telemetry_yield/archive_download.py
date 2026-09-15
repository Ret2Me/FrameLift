"""Resumable, fail-soft HTTP downloader for archive IQ campaigns."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Mapping, Sequence
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


CHECKPOINT_SCHEMA_VERSION = "archive-iq-download-checkpoint-v1"
MANIFEST_SCHEMA_VERSION = "archive-iq-download-manifest-v1"


@dataclass(frozen=True, slots=True)
class DownloadCandidate:
    observation_id: int
    url: str
    expected_size_bytes: int | None
    mode: str | None = None
    satellite_id: str | None = None

    @property
    def filename(self) -> str:
        return f"observation_{self.observation_id}.iq"


@dataclass(frozen=True, slots=True)
class DownloadResult:
    observation_id: int
    url: str
    expected_size_bytes: int | None
    path: str
    status: str
    size_bytes: int | None
    sha256: str | None
    attempts: int
    resumed_from_bytes: int
    error: str | None = None

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def select_catalogue_iq(
    catalogue: Mapping[str, Any], *, frames_recovered: bool | None = None
) -> tuple[DownloadCandidate, ...]:
    """Select downloadable IQ, optionally filtering by the catalogue outcome.

    ``None`` selects every IQ link.  A boolean performs an exact filter and is
    useful for keeping positive-reference and zero-frame campaigns separate.
    """

    raw_observations = catalogue.get("observations")
    if not isinstance(raw_observations, Sequence) or isinstance(
        raw_observations, (str, bytes, bytearray, memoryview)
    ):
        raise ValueError("observations must be an array")
    output: list[DownloadCandidate] = []
    seen: set[int] = set()
    for raw in raw_observations:
        if not isinstance(raw, Mapping):
            raise ValueError("observation must be an object")
        if frames_recovered is not None and raw.get("frames_recovered") is not frames_recovered:
            continue
        links = raw.get("links")
        url = links.get("iq") if isinstance(links, Mapping) else None
        if not isinstance(url, str) or not url.strip():
            continue
        observation_id = raw.get("observation_id")
        if not isinstance(observation_id, int) or isinstance(observation_id, bool):
            raise ValueError("observation_id must be an integer")
        if observation_id in seen:
            raise ValueError("observation_id values must be unique")
        seen.add(observation_id)
        artifacts = raw.get("artifacts")
        iq_artifact = artifacts.get("iq") if isinstance(artifacts, Mapping) else None
        raw_size = iq_artifact.get("size_bytes") if isinstance(iq_artifact, Mapping) else None
        expected_size = (
            raw_size
            if isinstance(raw_size, int)
            and not isinstance(raw_size, bool)
            and raw_size > 0
            else None
        )
        mode = raw.get("mode")
        satellite_id = raw.get("satellite_id")
        output.append(
            DownloadCandidate(
                observation_id=observation_id,
                url=url.strip(),
                expected_size_bytes=expected_size,
                mode=mode.strip() if isinstance(mode, str) and mode.strip() else None,
                satellite_id=(
                    satellite_id.strip()
                    if isinstance(satellite_id, str) and satellite_id.strip()
                    else None
                ),
            )
        )
    return tuple(sorted(output, key=lambda item: item.observation_id))


def select_zero_frame_iq(catalogue: Mapping[str, Any]) -> tuple[DownloadCandidate, ...]:
    """Select every downloadable IQ whose catalogue result says zero frames."""

    return select_catalogue_iq(catalogue, frames_recovered=False)


def select_positive_frame_iq(
    catalogue: Mapping[str, Any],
) -> tuple[DownloadCandidate, ...]:
    """Select IQ captures for which the catalogue reports recovered frames."""

    return select_catalogue_iq(catalogue, frames_recovered=True)


def sha256_file(path: Path, *, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(chunk_size), b""):
            digest.update(block)
    return digest.hexdigest()


def _quarantine_invalid_size(path: Path) -> Path:
    candidate = path.with_name(path.name + ".invalid-size")
    suffix = 1
    while candidate.exists():
        candidate = path.with_name(path.name + f".invalid-size.{suffix}")
        suffix += 1
    os.replace(path, candidate)
    return candidate


def _response_status(response: Any) -> int:
    status = getattr(response, "status", None)
    if isinstance(status, int):
        return status
    return int(response.getcode())


def _validated_append_mode(
    response: Any, offset: int, expected_size_bytes: int
) -> tuple[str, int]:
    status = _response_status(response)
    if offset == 0:
        if status not in (200, 206):
            raise OSError(f"unexpected HTTP status {status}")
        if status == 206:
            content_range = response.headers.get("Content-Range", "")
            match = re.fullmatch(r"bytes 0-(\d+)/(\d+|\*)", content_range)
            if match is None:
                raise OSError("invalid Content-Range for initial response")
            if match.group(2) != "*" and int(match.group(2)) != expected_size_bytes:
                raise OSError("Content-Range total differs from expected size_bytes")
        return "wb", 0
    if status == 200:
        # The server ignored Range.  Restart safely rather than appending a
        # duplicate full response to the partial file.
        return "wb", 0
    if status != 206:
        raise OSError(f"range resume requires HTTP 206, received {status}")
    content_range = response.headers.get("Content-Range", "")
    match = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+|\*)", content_range)
    if match is None or int(match.group(1)) != offset:
        raise OSError("Content-Range does not start at requested offset")
    if match.group(3) != "*" and int(match.group(3)) != expected_size_bytes:
        raise OSError("Content-Range total differs from expected size_bytes")
    return "ab", offset


def _download_attempt(
    candidate: DownloadCandidate,
    part_path: Path,
    *,
    timeout_seconds: float,
    chunk_size: int,
    opener: Callable[..., Any],
) -> None:
    offset = part_path.stat().st_size if part_path.exists() else 0
    headers = {"User-Agent": "telemetry-yield-archive-downloader/1"}
    if offset:
        headers["Range"] = f"bytes={offset}-"
    request = Request(candidate.url, headers=headers, method="GET")
    with opener(request, timeout=timeout_seconds) as response:
        expected = candidate.expected_size_bytes
        if expected is None:
            raise ValueError("missing expected size_bytes")
        file_mode, effective_offset = _validated_append_mode(response, offset, expected)
        with part_path.open(file_mode) as output:
            while True:
                try:
                    block = response.read(chunk_size)
                except IncompleteRead as error:
                    if error.partial:
                        output.write(error.partial)
                        output.flush()
                        os.fsync(output.fileno())
                    raise
                if not block:
                    break
                output.write(block)
            output.flush()
            os.fsync(output.fileno())
    actual_size = part_path.stat().st_size
    if actual_size > expected:
        raise OSError(
            f"download exceeds expected size: {actual_size} > {expected}"
        )
    if actual_size < expected:
        raise OSError(
            f"incomplete download: {actual_size} < {expected}; resume offset {effective_offset}"
        )


def download_candidate(
    candidate: DownloadCandidate,
    destination: Path,
    *,
    timeout_seconds: float = 30.0,
    retries: int = 4,
    retry_backoff_seconds: float = 1.0,
    chunk_size: int = 1024 * 1024,
    opener: Callable[..., Any] = urlopen,
    sleeper: Callable[[float], None] = time.sleep,
) -> DownloadResult:
    """Download one item, resuming a partial file and atomically promoting it."""

    if candidate.expected_size_bytes is None:
        raise ValueError("missing expected size_bytes")
    if timeout_seconds <= 0 or retries < 0 or chunk_size <= 0:
        raise ValueError("timeout/chunk size must be positive and retries nonnegative")
    destination.mkdir(parents=True, exist_ok=True)
    final_path = destination / candidate.filename
    part_path = final_path.with_name(final_path.name + ".part")

    if final_path.exists():
        if final_path.stat().st_size == candidate.expected_size_bytes:
            size = final_path.stat().st_size
            return DownloadResult(
                candidate.observation_id,
                candidate.url,
                candidate.expected_size_bytes,
                str(final_path),
                "skipped_valid",
                size,
                sha256_file(final_path, chunk_size=chunk_size),
                0,
                0,
            )
        if final_path.stat().st_size < candidate.expected_size_bytes and not part_path.exists():
            os.replace(final_path, part_path)
        else:
            _quarantine_invalid_size(final_path)

    if part_path.exists() and part_path.stat().st_size > candidate.expected_size_bytes:
        _quarantine_invalid_size(part_path)
    resumed_from = part_path.stat().st_size if part_path.exists() else 0
    if part_path.exists() and part_path.stat().st_size == candidate.expected_size_bytes:
        os.replace(part_path, final_path)
        return DownloadResult(
            candidate.observation_id,
            candidate.url,
            candidate.expected_size_bytes,
            str(final_path),
            "downloaded",
            candidate.expected_size_bytes,
            sha256_file(final_path, chunk_size=chunk_size),
            0,
            resumed_from,
        )

    last_error: Exception | None = None
    attempts = 0
    for attempt in range(retries + 1):
        attempts += 1
        try:
            _download_attempt(
                candidate,
                part_path,
                timeout_seconds=timeout_seconds,
                chunk_size=chunk_size,
                opener=opener,
            )
            os.replace(part_path, final_path)
            return DownloadResult(
                candidate.observation_id,
                candidate.url,
                candidate.expected_size_bytes,
                str(final_path),
                "downloaded",
                candidate.expected_size_bytes,
                sha256_file(final_path, chunk_size=chunk_size),
                attempts,
                resumed_from,
            )
        except Exception as error:  # fail-soft campaign records the final error
            last_error = error
            if attempt < retries:
                sleeper(retry_backoff_seconds * (2**attempt))
    actual_size = part_path.stat().st_size if part_path.exists() else None
    return DownloadResult(
        candidate.observation_id,
        candidate.url,
        candidate.expected_size_bytes,
        str(final_path),
        "error",
        actual_size,
        None,
        attempts,
        resumed_from,
        f"{type(last_error).__name__}: {last_error}",
    )


def append_checkpoint(path: Path, result: DownloadResult) -> dict[str, object]:
    event: dict[str, object] = {
        "schema_version": CHECKPOINT_SCHEMA_VERSION,
        "recorded_at": datetime.now(UTC).isoformat(),
        **result.as_dict(),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, sort_keys=True, separators=(",", ":")) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    return event


def load_checkpoint_events(path: Path) -> tuple[dict[str, object], ...]:
    if not path.exists():
        return ()
    events: list[dict[str, object]] = []
    lines = path.read_text(encoding="utf-8").splitlines()
    for line_number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            # A power loss may leave only the final append truncated.  Older
            # valid events remain usable; malformed middle records are unsafe.
            if line_number == len(lines):
                break
            raise ValueError(f"malformed checkpoint JSONL line {line_number}")
        if not isinstance(value, dict):
            raise ValueError(f"checkpoint line {line_number} is not an object")
        events.append(value)
    return tuple(events)


def write_manifest(
    path: Path,
    *,
    candidates: Sequence[DownloadCandidate],
    checkpoint_path: Path,
) -> dict[str, object]:
    events = load_checkpoint_events(checkpoint_path)
    latest: dict[int, dict[str, object]] = {}
    candidate_ids = {item.observation_id for item in candidates}
    for event in events:
        observation_id = event.get("observation_id")
        if isinstance(observation_id, int) and observation_id in candidate_ids:
            latest[observation_id] = event
    statuses = Counter(str(item.get("status")) for item in latest.values())
    document: dict[str, object] = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "updated_at": datetime.now(UTC).isoformat(),
        "candidate_count": len(candidates),
        "known_size_candidate_count": sum(
            item.expected_size_bytes is not None for item in candidates
        ),
        "expected_size_bytes": sum(
            item.expected_size_bytes or 0 for item in candidates
        ),
        "latest_result_count": len(latest),
        "status_counts": dict(sorted(statuses.items())),
        "results": [latest[key] for key in sorted(latest)],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    os.replace(temporary, path)
    return document


def run_download_campaign(
    candidates: Sequence[DownloadCandidate],
    destination: Path,
    *,
    checkpoint_path: Path,
    manifest_path: Path,
    timeout_seconds: float = 30.0,
    retries: int = 4,
    retry_backoff_seconds: float = 1.0,
    chunk_size: int = 1024 * 1024,
    opener: Callable[..., Any] = urlopen,
    sleeper: Callable[[float], None] = time.sleep,
) -> tuple[DownloadResult, ...]:
    """Process all candidates, checkpointing and continuing after failures."""

    results: list[DownloadResult] = []
    for candidate in candidates:
        try:
            result = download_candidate(
                candidate,
                destination,
                timeout_seconds=timeout_seconds,
                retries=retries,
                retry_backoff_seconds=retry_backoff_seconds,
                chunk_size=chunk_size,
                opener=opener,
                sleeper=sleeper,
            )
        except Exception as error:
            result = DownloadResult(
                candidate.observation_id,
                candidate.url,
                candidate.expected_size_bytes,
                str(destination / candidate.filename),
                "error",
                None,
                None,
                0,
                0,
                f"{type(error).__name__}: {error}",
            )
        append_checkpoint(checkpoint_path, result)
        write_manifest(
            manifest_path,
            candidates=candidates,
            checkpoint_path=checkpoint_path,
        )
        results.append(result)
    return tuple(results)
