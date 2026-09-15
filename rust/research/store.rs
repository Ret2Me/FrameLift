//! SQLite attempt reservations compatible with the legacy attempts table.
//!
//! A transaction chooses a single worker. A lease guards completion after a
//! stale running attempt is reclaimed. Terminal results are never reclaimed.
//! SQLite is bundled; the application does not invoke a database CLI or Python.

use chrono::{DateTime, SecondsFormat, Utc};
use rusqlite::{Connection, OpenFlags, OptionalExtension, TransactionBehavior, params};
use serde::Serialize;
use serde_json::{Map, Value};
use std::path::{Path, PathBuf};
use std::time::{Duration, SystemTime};

const SCHEMA: &str = "
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
);";

#[derive(Clone, Debug)]
pub struct AttemptStore {
    path: PathBuf,
}

#[derive(Debug, Serialize)]
pub struct Attempt {
    pub recording_id: String,
    pub config_hash: String,
    pub config: Map<String, Value>,
    pub status: String,
    pub lease_token: String,
    pub started_at: String,
    pub finished_at: Option<String>,
    pub result: Option<Map<String, Value>>,
}

fn fail(error: rusqlite::Error) -> String {
    format!("attempt store: {error}")
}

fn timestamp(now: DateTime<Utc>) -> String {
    now.to_rfc3339_opts(SecondsFormat::Micros, false)
}

fn now() -> DateTime<Utc> {
    SystemTime::now().into()
}

fn json_object(column: usize, text: &str) -> rusqlite::Result<Map<String, Value>> {
    serde_json::from_str(text).map_err(|error| {
        rusqlite::Error::FromSqlConversionFailure(
            column,
            rusqlite::types::Type::Text,
            Box::new(error),
        )
    })
}

fn decode_attempt(row: &rusqlite::Row<'_>) -> rusqlite::Result<Attempt> {
    let config: String = row.get(2)?;
    let result: Option<String> = row.get(7)?;
    Ok(Attempt {
        recording_id: row.get(0)?,
        config_hash: row.get(1)?,
        config: json_object(2, &config)?,
        status: row.get(3)?,
        lease_token: row.get(4)?,
        started_at: row.get(5)?,
        finished_at: row.get(6)?,
        result: result.as_deref().map(|s| json_object(7, s)).transpose()?,
    })
}

impl AttemptStore {
    /// Create or reopen a store. Parent directories must already exist.
    /// Do not use a network filesystem for SQLite WAL databases.
    pub fn open(path: &Path) -> Result<Self, String> {
        if path.as_os_str().is_empty() || path == Path::new(":memory:") {
            return Err("attempt store requires a persistent file path".into());
        }
        let store = Self {
            path: path.to_owned(),
        };
        let connection = store.connection(true, false)?;
        let mode: String = connection
            .query_row("PRAGMA journal_mode=WAL", [], |r| r.get(0))
            .map_err(fail)?;
        if mode != "wal" {
            return Err("attempt store requires WAL journal mode".into());
        }
        connection.execute_batch(SCHEMA).map_err(fail)?;
        Ok(store)
    }

    pub fn open_existing(path: &Path) -> Result<Self, String> {
        let store = Self {
            path: path.to_owned(),
        };
        store.connection(false, false)?;
        Ok(store)
    }

    fn connection(&self, create: bool, readonly: bool) -> Result<Connection, String> {
        match std::fs::symlink_metadata(&self.path) {
            Ok(m) if !m.file_type().is_file() => {
                return Err("attempt database must be a regular file, not a symlink".into());
            }
            Err(e) if e.kind() != std::io::ErrorKind::NotFound => return Err(e.to_string()),
            _ => {}
        }
        let access = if readonly {
            OpenFlags::SQLITE_OPEN_READ_ONLY
        } else {
            OpenFlags::SQLITE_OPEN_READ_WRITE
        };
        let mut flags = access | OpenFlags::SQLITE_OPEN_NO_MUTEX | OpenFlags::SQLITE_OPEN_NOFOLLOW;
        if create {
            flags |= OpenFlags::SQLITE_OPEN_CREATE;
        }
        // Deliberately omit SQLITE_OPEN_URI: the path is a path, never a URI.
        let connection = Connection::open_with_flags(&self.path, flags).map_err(fail)?;
        connection
            .busy_timeout(Duration::from_secs(30))
            .map_err(fail)?;
        Ok(connection)
    }

    pub fn reserve(
        &self,
        recording_id: &str,
        config_hash: &str,
        config: &Map<String, Value>,
        stale_after: Duration,
    ) -> Result<Option<String>, String> {
        self.reserve_at(recording_id, config_hash, config, stale_after, now())
    }

    fn reserve_at(
        &self,
        recording_id: &str,
        config_hash: &str,
        config: &Map<String, Value>,
        stale_after: Duration,
        now: DateTime<Utc>,
    ) -> Result<Option<String>, String> {
        super::nonempty("recording_id", recording_id)?;
        super::nonempty("config_hash", config_hash)?;
        let stale_after =
            chrono::Duration::from_std(stale_after).map_err(|_| "stale interval overflow")?;
        let encoded = crate::ledger::canonical_json(&Value::Object(config.clone()))?;
        let mut connection = self.connection(false, false)?;
        let transaction = connection
            .transaction_with_behavior(TransactionBehavior::Immediate)
            .map_err(fail)?;
        let existing: Option<(String, String)> = transaction
            .query_row(
                "SELECT status, started_at FROM attempts WHERE recording_id=?1 AND config_hash=?2",
                params![recording_id, config_hash],
                |r| Ok((r.get(0)?, r.get(1)?)),
            )
            .optional()
            .map_err(fail)?;
        if let Some((status, started)) = &existing {
            if status != "running" {
                return Ok(None);
            }
            let started = DateTime::parse_from_rfc3339(started)
                .map_err(|e| format!("invalid stored timestamp: {e}"))?
                .with_timezone(&Utc);
            if now.signed_duration_since(started) <= stale_after {
                return Ok(None);
            }
        }
        let mut random = [0_u8; 16];
        getrandom::fill(&mut random).map_err(|e| format!("lease entropy: {e}"))?;
        random[6] = (random[6] & 0x0f) | 0x40;
        random[8] = (random[8] & 0x3f) | 0x80;
        let token = hex::encode(random);
        if existing.is_none() {
            transaction
                .execute(
                    "INSERT INTO attempts
                 (recording_id,config_hash,config_json,status,lease_token,started_at)
                 VALUES (?1,?2,?3,'running',?4,?5)",
                    params![recording_id, config_hash, encoded, token, timestamp(now)],
                )
                .map_err(fail)?;
        } else {
            transaction
                .execute(
                    "UPDATE attempts
                 SET config_json=?1,lease_token=?2,started_at=?3,finished_at=NULL,result_json=NULL
                 WHERE recording_id=?4 AND config_hash=?5",
                    params![encoded, token, timestamp(now), recording_id, config_hash],
                )
                .map_err(fail)?;
        }
        transaction.commit().map_err(fail)?;
        Ok(Some(token))
    }

    pub fn finish(
        &self,
        recording_id: &str,
        config_hash: &str,
        status: &str,
        result: &Map<String, Value>,
        lease_token: &str,
    ) -> Result<(), String> {
        if !matches!(status, "ok" | "error" | "timeout") {
            return Err("invalid terminal status".into());
        }
        super::nonempty("lease_token", lease_token)?;
        let encoded = crate::ledger::canonical_json(&Value::Object(result.clone()))?;
        let changed = self
            .connection(false, false)?
            .execute(
                "UPDATE attempts SET status=?1,finished_at=?2,result_json=?3
             WHERE recording_id=?4 AND config_hash=?5 AND status='running' AND lease_token=?6",
                params![
                    status,
                    timestamp(now()),
                    encoded,
                    recording_id,
                    config_hash,
                    lease_token
                ],
            )
            .map_err(fail)?;
        if changed != 1 {
            return Err("attempt is missing or no longer reserved".into());
        }
        Ok(())
    }

    pub fn get(&self, recording_id: &str, config_hash: &str) -> Result<Option<Attempt>, String> {
        Self::inspect(&self.path, recording_id, config_hash)
    }

    /// Read an existing store without creating or initializing a missing one.
    pub fn inspect(
        path: &Path,
        recording_id: &str,
        config_hash: &str,
    ) -> Result<Option<Attempt>, String> {
        let store = Self {
            path: path.to_owned(),
        };
        store.connection(false, true)?.query_row(
            "SELECT recording_id,config_hash,config_json,status,lease_token,started_at,finished_at,result_json
             FROM attempts WHERE recording_id=?1 AND config_hash=?2",
            params![recording_id, config_hash],
            decode_attempt,
        ).optional().map_err(fail)
    }
}

#[cfg(test)]
#[path = "store_tests.rs"]
mod tests;
