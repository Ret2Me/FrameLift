"""Small SQLite attempt store used by the local harness and offline tests.

PostgreSQL is the production target, but SQLite gives the pilot a durable,
dependency-free implementation of the same idempotency contract.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Iterator


class AttemptStore:
    """Reserve and finish attempts without computing the same work twice."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=30.0)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connection() as connection:
            connection.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS attempts (
                    recording_id TEXT NOT NULL,
                    config_hash TEXT NOT NULL,
                    config_json TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('running','ok','error','timeout')),
                    lease_token TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    result_json TEXT,
                    PRIMARY KEY (recording_id, config_hash)
                );
                """
            )

    def reserve(
        self,
        recording_id: str,
        config_hash: str,
        config: dict[str, Any],
        *,
        stale_after: timedelta = timedelta(hours=1),
    ) -> str | None:
        """Reserve work, reclaiming only an interrupted stale reservation."""
        now = datetime.now(UTC)
        lease_token = uuid.uuid4().hex
        canonical = json.dumps(config, sort_keys=True, separators=(",", ":"))
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT status, started_at FROM attempts "
                "WHERE recording_id=? AND config_hash=?",
                (recording_id, config_hash),
            ).fetchone()
            if row is None:
                connection.execute(
                    "INSERT INTO attempts "
                    "(recording_id, config_hash, config_json, status, lease_token, started_at) "
                    "VALUES (?, ?, ?, 'running', ?, ?)",
                    (recording_id, config_hash, canonical, lease_token, now.isoformat()),
                )
                return lease_token
            if row["status"] != "running":
                return None
            started = datetime.fromisoformat(row["started_at"])
            if now - started <= stale_after:
                return None
            connection.execute(
                "UPDATE attempts SET config_json=?, lease_token=?, started_at=?, "
                "finished_at=NULL, result_json=NULL "
                "WHERE recording_id=? AND config_hash=?",
                (
                    canonical,
                    lease_token,
                    now.isoformat(),
                    recording_id,
                    config_hash,
                ),
            )
            return lease_token

    def finish(
        self,
        recording_id: str,
        config_hash: str,
        status: str,
        result: dict[str, Any],
        *,
        lease_token: str,
    ) -> None:
        if status not in {"ok", "error", "timeout"}:
            raise ValueError(f"invalid terminal status: {status}")
        with self._connection() as connection:
            cursor = connection.execute(
                "UPDATE attempts SET status=?, finished_at=?, result_json=? "
                "WHERE recording_id=? AND config_hash=? AND status='running' "
                "AND lease_token=?",
                (
                    status,
                    datetime.now(UTC).isoformat(),
                    json.dumps(result, sort_keys=True, separators=(",", ":")),
                    recording_id,
                    config_hash,
                    lease_token,
                ),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("attempt is missing or no longer reserved")

    def get(self, recording_id: str, config_hash: str) -> dict[str, Any] | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM attempts WHERE recording_id=? AND config_hash=?",
                (recording_id, config_hash),
            ).fetchone()
        if row is None:
            return None
        value = dict(row)
        value["config"] = json.loads(value.pop("config_json"))
        value["result"] = json.loads(value.pop("result_json")) if value["result_json"] else None
        return value
