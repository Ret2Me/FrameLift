use super::*;
use serde_json::json;
use std::sync::{Arc, Barrier};

fn time() -> DateTime<Utc> {
    DateTime::parse_from_rfc3339("2026-09-15T00:00:00+00:00")
        .unwrap()
        .with_timezone(&Utc)
}

#[test]
fn reservation_survives_reopen_and_terminal_result_is_idempotent() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("attempts.sqlite");
    let config = json!({"unicode":"Łódź","baud":1200})
        .as_object()
        .unwrap()
        .clone();
    let store = AttemptStore::open(&path).unwrap();
    let token = store
        .reserve("r", "h", &config, Duration::from_secs(3600))
        .unwrap()
        .unwrap();
    assert_eq!(token.len(), 32);
    assert_eq!(&token[12..13], "4");
    let reopened = AttemptStore::open(&path).unwrap();
    assert!(
        reopened
            .reserve("r", "h", &config, Duration::from_secs(3600))
            .unwrap()
            .is_none()
    );
    assert_eq!(reopened.get("r", "h").unwrap().unwrap().config, config);
    assert!(
        reopened
            .finish("r", "h", "running", &config, &token)
            .is_err()
    );
    reopened.finish("r", "h", "ok", &config, &token).unwrap();
    let finished = store.get("r", "h").unwrap().unwrap();
    assert_eq!(finished.status, "ok");
    assert_eq!(finished.result, Some(config.clone()));
    assert!(finished.finished_at.is_some());
    assert!(
        store
            .reserve_at(
                "r",
                "h",
                &config,
                Duration::ZERO,
                time() + chrono::Duration::days(3650)
            )
            .unwrap()
            .is_none()
    );
    assert!(store.finish("r", "h", "ok", &config, &token).is_err());
}

#[test]
fn expired_worker_cannot_finish_a_reclaimed_attempt() {
    let dir = tempfile::tempdir().unwrap();
    let store = AttemptStore::open(&dir.path().join("attempts.sqlite")).unwrap();
    let config = Map::new();
    let start = time();
    let stale = Duration::from_secs(10);
    let first = store
        .reserve_at("r", "h", &config, stale, start)
        .unwrap()
        .unwrap();
    assert!(
        store
            .reserve_at(
                "r",
                "h",
                &config,
                stale,
                start + chrono::Duration::seconds(10)
            )
            .unwrap()
            .is_none()
    );
    let second = store
        .reserve_at(
            "r",
            "h",
            &config,
            stale,
            start + chrono::Duration::seconds(11),
        )
        .unwrap()
        .unwrap();
    assert_ne!(first, second);
    assert!(store.finish("r", "h", "ok", &config, &first).is_err());
    store.finish("r", "h", "timeout", &config, &second).unwrap();
    assert_eq!(store.get("r", "h").unwrap().unwrap().status, "timeout");
}

#[test]
fn simultaneous_workers_have_exactly_one_reservation_winner() {
    let dir = tempfile::tempdir().unwrap();
    let store = AttemptStore::open(&dir.path().join("attempts.sqlite")).unwrap();
    let barrier = Arc::new(Barrier::new(16));
    let workers: Vec<_> = (0..16)
        .map(|_| {
            let store = store.clone();
            let barrier = barrier.clone();
            std::thread::spawn(move || {
                barrier.wait();
                store
                    .reserve("r", "h", &Map::new(), Duration::from_secs(3600))
                    .unwrap()
            })
        })
        .collect();
    let leases: Vec<_> = workers
        .into_iter()
        .filter_map(|w| w.join().unwrap())
        .collect();
    assert_eq!(leases.len(), 1);
}

#[test]
fn malformed_store_and_absent_read_only_store_fail_without_creation() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("missing.sqlite");
    assert!(AttemptStore::inspect(&path, "r", "h").is_err());
    assert!(!path.exists());
    assert!(AttemptStore::open(dir.path()).is_err());
    assert!(AttemptStore::open(Path::new(":memory:")).is_err());
    let store = AttemptStore::open(&path).unwrap();
    let token = store
        .reserve("r", "h", &Map::new(), Duration::ZERO)
        .unwrap()
        .unwrap();
    let connection = store.connection(false, false).unwrap();
    connection
        .execute("UPDATE attempts SET started_at='bad time'", [])
        .unwrap();
    assert!(
        store
            .reserve("r", "h", &Map::new(), Duration::ZERO)
            .is_err()
    );
    assert_eq!(store.get("r", "h").unwrap().unwrap().lease_token, token);
    connection
        .execute("UPDATE attempts SET config_json='[1,2]'", [])
        .unwrap();
    assert!(store.get("r", "h").is_err());
    #[cfg(unix)]
    {
        let link = dir.path().join("symlink");
        std::os::unix::fs::symlink(&path, &link).unwrap();
        assert!(AttemptStore::open(&link).is_err());
    }
}
