"""Fail-closed guard for large data transfers."""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

LARGE_BATCH_BYTES = 10_000_000_000
MIN_REMAINING_BYTES = 10_000_000_000


@dataclass(frozen=True)
class DownloadPlan:
    source_id: str
    estimated_bytes: int
    destination: Path
    approved: bool
    created_at: datetime

    @classmethod
    def load(cls, path: Path) -> "DownloadPlan":
        raw = json.loads(path.read_text(encoding="utf-8"))
        return cls(
            source_id=str(raw["source_id"]),
            estimated_bytes=int(raw["estimated_bytes"]),
            destination=Path(raw["destination"]),
            approved=raw.get("approved") is True,
            created_at=datetime.fromisoformat(raw["created_at"]),
        )


def assert_download_allowed(
    source_id: str,
    estimated_bytes: int,
    destination: Path,
    *,
    plan: DownloadPlan | None = None,
    free_bytes: int | None = None,
    now: datetime | None = None,
    max_plan_age: timedelta = timedelta(days=7),
) -> None:
    if not source_id.strip():
        raise ValueError("source_id must be non-empty")
    if estimated_bytes <= 0:
        raise ValueError("estimated_bytes must be positive")
    if estimated_bytes >= LARGE_BATCH_BYTES:
        if plan is None or not plan.approved:
            raise PermissionError("batch >=10 GB requires an approved download-plan.json")
        current = now or datetime.now(UTC)
        if plan.created_at.tzinfo is None or plan.created_at.utcoffset() is None:
            raise PermissionError("download plan timestamp must be timezone-aware")
        if current - plan.created_at.astimezone(UTC) > max_plan_age:
            raise PermissionError("download plan has expired")
        if plan.source_id != source_id:
            raise PermissionError("download plan does not match source_id")
        if (
            plan.estimated_bytes != estimated_bytes
            or plan.destination.resolve() != destination.resolve()
        ):
            raise PermissionError("download plan does not match requested batch")
    available = free_bytes
    if available is None:
        destination.mkdir(parents=True, exist_ok=True)
        available = shutil.disk_usage(destination).free
    if available - estimated_bytes < MIN_REMAINING_BYTES:
        raise OSError("download would leave less than 10 GB free")


def assert_storage_capacity(
    required_bytes: int, destination: Path, *, free_bytes: int | None = None
) -> None:
    """Keep a safety reserve before creating a same-size derived artifact."""
    assert_download_allowed(
        "local-derived-artifact",
        required_bytes,
        destination,
        free_bytes=free_bytes,
    )
