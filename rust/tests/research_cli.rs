//! Black-box checks with no Python, codec, shell or network tool on PATH.

use serde_json::{Value, json};
use std::process::{Command, Output};

fn run(command: &str, input: &Value) -> Output {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("input.json");
    std::fs::write(&path, serde_json::to_vec(input).unwrap()).unwrap();
    Command::new(env!("CARGO_BIN_EXE_framelift-research"))
        .args([command, "--input"])
        .arg(path)
        .env_clear()
        .env("PATH", "")
        .output()
        .unwrap()
}

#[test]
fn sqlite_leases_work_across_separate_native_processes() {
    let dir = tempfile::tempdir().unwrap();
    let database = dir.path().join("attempts.sqlite");
    let input = dir.path().join("request.json");
    let call = |value: &Value| {
        std::fs::write(&input, serde_json::to_vec(value).unwrap()).unwrap();
        Command::new(env!("CARGO_BIN_EXE_framelift-research"))
            .args(["attempts", "--database"])
            .arg(&database)
            .arg("--input")
            .arg(&input)
            .env_clear()
            .env("PATH", "")
            .output()
            .unwrap()
    };
    let reserve = json!({"action":"reserve","recording_id":"r","config_hash":"h","config":{},"stale_after_seconds":3600});
    let output = call(&reserve);
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    let result: Value = serde_json::from_slice(&output.stdout).unwrap();
    let token = result["result"]["lease_token"].as_str().unwrap();
    let output = call(&reserve);
    assert_eq!(
        serde_json::from_slice::<Value>(&output.stdout).unwrap()["result"]["reserved"],
        false
    );
    let mut finish = json!({"action":"finish","recording_id":"r","config_hash":"h","status":"ok","result":{"frames":3},"lease_token":"wrong"});
    let output = call(&finish);
    assert!(!output.status.success());
    assert!(output.stdout.is_empty());
    finish["lease_token"] = json!(token);
    assert!(call(&finish).status.success());
    let output = call(&json!({"action":"get","recording_id":"r","config_hash":"h"}));
    let row: Value = serde_json::from_slice(&output.stdout).unwrap();
    assert_eq!(row["result"]["attempt"]["result"]["frames"], 3);
    assert_eq!(row["result"]["attempt"]["status"], "ok");
}

#[test]
fn event_local_metrics_run_without_an_interpreter() {
    let frame = json!({"transmission_event_id":"a","payload_sha256":"digest","crc_valid":true});
    let output = run(
        "metrics",
        &json!({"frames":[frame.clone(),frame],"cpu_seconds":2.0,
        "negative":{"n_frames_on_negative":3,"total_hours_negative":1.5}}),
    );
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    let result: Value = serde_json::from_slice(&output.stdout).unwrap();
    assert_eq!(result["unique_crc_frames"], 1);
    assert_eq!(result["frames_per_cpu_second"], 0.5);
    assert_eq!(result["false_accepts_per_hour"], 2.0);
    assert_eq!(result["input_crc_flags_independently_verified"], false);
}

#[test]
fn leakage_and_unreviewed_export_fail_without_success_json() {
    for (command, input) in [
        ("check-splits", json!([["a", "train"], ["a", "test"]])),
        ("export-gate", json!([])),
        (
            "export-gate",
            json!([{"source_id":"a","license_spdx":"MIT","license_verified":true}]),
        ),
        ("metrics", json!({"frames":[],"cpu_seconds":0})),
        (
            "metrics",
            json!({"frames":[],"negative":{"n_frames_on_negative":true,"total_hours_negative":1}}),
        ),
    ] {
        let output = run(command, &input);
        assert!(!output.status.success());
        assert!(output.stdout.is_empty());
        assert!(!output.stderr.is_empty());
    }
}

#[test]
fn valid_split_and_export_succeed() {
    assert!(
        run(
            "check-splits",
            &json!([["a", "train"], ["a", "train"], ["b", "test"]])
        )
        .status
        .success()
    );
    assert!(run("export-gate",&json!([{"source_id":"a","license_spdx":"MIT","license_verified":true,"publication_reviewed":true}])).status.success());
}

#[test]
fn overlapping_events_alone_are_not_merged() {
    let event = json!({"observation_id":"1","norad_id":123,"transmitter_uuid":"tx","station_id":"a", "start_utc":"2026-01-01T00:00:00Z","end_utc":"2026-01-01T00:05:00Z"});
    let output = run("same-event", &json!({"left":event,"right":event}));
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    let result: Value = serde_json::from_slice(&output.stdout).unwrap();
    assert_eq!(result["same_transmission_event"], false);
}

#[test]
fn packet_cli_rejects_truncated_files_without_success_json() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("capture");
    std::fs::write(&path, [0_u8; 3]).unwrap();
    for command in ["audit-pcap", "audit-ipv4-pdus"] {
        let output = Command::new(env!("CARGO_BIN_EXE_framelift-research"))
            .args([command, "--input"])
            .arg(&path)
            .env_clear()
            .env("PATH", "")
            .output()
            .unwrap();
        assert!(!output.status.success());
        assert!(output.stdout.is_empty());
    }
}

#[test]
fn canonical_hash_and_public_salt_match_frozen_python_vectors() {
    for (command, request, field, expected) in [
        (
            "config-hash",
            json!({"protocol_id":"afsk1200_ax25","decoder":"direwolf","decoder_version":"1.7","seed":7,"config":{"baud":1200,"agc":"slow"}}),
            "config_hash",
            "6f1d3809fed4498f55c4cfc070fb47b657e784abd39eecf4a88ab5c74d62905a",
        ),
        (
            "derive-selection-salt",
            json!({"domain":"polyitan-v2","preregistration_sha256":"1".repeat(64),"output_value":"AB".repeat(64)}),
            "selection_salt_hex",
            "10ac8ed87ea9f0fa1bfb13117b58a7c01b8874766977d1458536b095ee87eee7",
        ),
    ] {
        let output = run(command, &request);
        assert!(
            output.status.success(),
            "{}",
            String::from_utf8_lossy(&output.stderr)
        );
        let result: Value = serde_json::from_slice(&output.stdout).unwrap();
        assert_eq!(result[field], expected);
    }
}

#[test]
fn cohort_cli_does_not_export_decoder_outcomes() {
    let request = json!({"catalogue":{"observations":[{"observation_id":7,"satellite_id":"s","mode":"FSK","frequency_hz":435000000,"start":"2026-01-01T00:00:00Z","end":"2026-01-01T00:01:00Z","frame_count":123}]},
        "download_plan":{"candidates":[{"observation_id":7,"key":"observation_7.iq","size_bytes":400,"url":"https://example.invalid/7"}]},
        "eligible_modes":["FSK"],"development_exclusions":[],"selection_salt_hex":"61".repeat(32),"target_observations":1,"maximum_per_satellite":1,"minimum_satellites":1});
    let output = run("select-cohort", &request);
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    let result: Value = serde_json::from_slice(&output.stdout).unwrap();
    assert_eq!(result["eligible_observations"], 1);
    assert_eq!(result["selected"][0]["observation_id"], 7);
    assert!(result["selected"][0].get("frame_count").is_none());
    for field in [
        "target_observations",
        "maximum_per_satellite",
        "minimum_satellites",
    ] {
        let mut invalid = request.clone();
        invalid[field] = json!(0);
        let output = run("select-cohort", &invalid);
        assert!(!output.status.success());
        assert!(output.stdout.is_empty());
    }
}

#[test]
fn beacon_cli_preserves_unverified_signature_status() {
    use sha2::{Digest, Sha512};
    let request = json!({"document":{"pulse":{"version":"2.0","period":60000,"statusCode":0,"timeStamp":"2026-09-03T12:45:00.000Z",
        "outputValue":"AB".repeat(64),"certificateId":hex::encode(Sha512::digest([1,2,3])),"signatureValue":"12".repeat(64),"chainIndex":2,"pulseIndex":123}},
        "expected_timestamp":"2026-09-03T12:45:00Z","certificate_pem":"-----BEGIN CERTIFICATE-----\nAQID\n-----END CERTIFICATE-----\n"});
    let output = run("validate-beacon", &request);
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    let result: Value = serde_json::from_slice(&output.stdout).unwrap();
    assert_eq!(result["certificate_identifier_verified"], true);
    assert_eq!(result["pulse_signature_verified"], false);
}
