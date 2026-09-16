use super::*;

fn frame(bytes: &str) -> Frame {
    Frame {
        payload_hex: bytes.into(),
        validation: Validation::ExternalDecoderAttested {
            decoder: "fixture-v1".into(),
        },
    }
}
fn arm(name: &str, frames: &[&str]) -> Arm {
    Arm {
        name: name.into(),
        status: Status::Completed,
        input_sha256: "a".repeat(64),
        runtime_sha256: "b".repeat(64),
        profile_sha256: "c".repeat(64),
        wall_seconds: 1.,
        cpu_seconds: Some(1.),
        error: None,
        frames: frames.iter().map(|s| frame(s)).collect(),
    }
}
fn study() -> Study {
    Study {
        schema: "framelift-recovery-study-v1".into(),
        baseline_arm: "old".into(),
        candidate_arms: vec!["new".into()],
        observations: vec![Observation {
            id: "one".into(),
            mission: "fixture".into(),
            station: 1,
            pass_group: "pass-one".into(),
            input_sha256: "a".repeat(64),
            exposure: Exposure::Development,
            confirmed_signal: Some(true),
            signal_evidence: Some("independent reference receipt".into()),
            negative_control: false,
            duration_seconds: 10.,
            arms: vec![
                arm("old", &["aa", "bbbb"]),
                arm("new", &["AA", "ccddff", "CCDDFF"]),
            ],
        }],
    }
}
fn all(v: &Value) -> &Value {
    &v["aggregates"]
        .as_array()
        .unwrap()
        .iter()
        .find(|a| a["stratum"] == "all_valid_pairs")
        .unwrap()["metrics"]
}
#[test]
fn canonical_unique_sets_report_both_additions_and_losses() {
    let v = summarize(&study()).unwrap();
    assert_eq!(all(&v)["baseline_unique"], 2);
    assert_eq!(all(&v)["candidate_unique"], 2);
    assert_eq!(all(&v)["added_unique"], 1);
    assert_eq!(all(&v)["lost_unique"], 1);
    assert_eq!(all(&v)["added_bytes"], 3);
    assert_eq!(all(&v)["lost_bytes"], 2);
    assert_eq!(all(&v)["net_gain_percent"], 0.);
    assert_eq!(v["publication_ready"], false);
    assert!(
        v["aggregates"]
            .as_array()
            .unwrap()
            .iter()
            .any(|a| a["stratum"] == "confirmed_signal")
    );
}
#[test]
fn missing_failed_and_unsupported_arms_are_attrition_not_zero_successes() {
    for status in [Status::Failed, Status::Unsupported, Status::TimedOut] {
        let mut s = study();
        let a = &mut s.observations[0].arms[1];
        a.status = status;
        a.error = Some("test reason".into());
        a.frames.clear();
        let v = summarize(&s).unwrap();
        assert!(v["aggregates"].as_array().unwrap().is_empty());
        assert_eq!(v["failures_and_missing"].as_array().unwrap().len(), 1);
    }
    let mut s = study();
    s.observations[0].arms.pop();
    assert_eq!(
        summarize(&s).unwrap()["failures_and_missing"][0]["status"],
        "missing"
    );
}
#[test]
fn receipt_identity_and_signal_labels_are_checked() {
    let mut s = study();
    s.observations[0].arms[1].input_sha256 = "d".repeat(64);
    assert!(summarize(&s).is_err());
    let mut s = study();
    s.observations[0].signal_evidence = None;
    assert!(summarize(&s).is_err());
    let mut s = study();
    s.observations.push(s.observations[0].clone());
    s.observations[1].id = "two".into();
    assert!(
        summarize(&s).is_err(),
        "duplicate samples must not inflate cohort"
    );
    let mut s = study();
    s.candidate_arms.push("old".into());
    assert!(summarize(&s).is_err());
    let mut s = study();
    s.observations[0].arms[0].wall_seconds = f64::NAN;
    assert!(summarize(&s).is_err());
}
#[test]
fn zero_baseline_has_no_percentage_and_negative_controls_are_separate() {
    let mut s = study();
    s.observations[0].arms[0].frames.clear();
    assert!(all(&summarize(&s).unwrap())["net_gain_percent"].is_null());
    s.observations[0].negative_control = true;
    s.observations[0].confirmed_signal = None;
    let v = summarize(&s).unwrap();
    assert!(v["aggregates"].as_array().unwrap().is_empty());
    let row = v["negative_controls"]
        .as_array()
        .unwrap()
        .iter()
        .find(|r| r["arm"] == "new")
        .unwrap();
    assert_eq!(row["accepted_unique"], 2);
    assert_eq!(row["accepted_per_hour"], 720.);
}
#[test]
fn received_fcs_and_payload_are_rechecked_not_trusted() {
    let mut payload = Vec::new();
    for (call, last) in [(b"APRS  ", false), (b"TEST  ", true)] {
        payload.extend(call.iter().map(|b| b << 1));
        payload.push(0x60 | u8::from(last));
    }
    payload.extend([3, 0xf0, 0x42]);
    let mut received = payload.clone();
    received.extend(protocol::crc16_x25(&payload).to_le_bytes());
    let mut f = Frame {
        payload_hex: hex::encode(&payload),
        validation: Validation::ReceivedAx25Fcs {
            frame_hex: hex::encode(&received),
        },
    };
    assert!(f.key().is_ok());
    let ambiguous = Frame {
        payload_hex: hex::encode(&received),
        validation: Validation::ReceivedFrameCheck {
            validator: coded::FrameValidator::Ax25,
        },
    };
    assert!(ambiguous.key().unwrap_err().contains("FCS-free"));
    *received.last_mut().unwrap() ^= 1;
    f.validation = Validation::ReceivedAx25Fcs {
        frame_hex: hex::encode(received),
    };
    assert!(f.key().is_err());
}

#[test]
fn exposure_strata_and_duration_guard_prevent_misleading_aggregation() {
    let mut s = study();
    let v = summarize(&s).unwrap();
    assert!(
        v["aggregates"]
            .as_array()
            .unwrap()
            .iter()
            .any(|a| a["stratum"] == "exposure/development")
    );
    for duration in [0., 1e-320, f64::MAX] {
        s.observations[0].duration_seconds = duration;
        assert!(summarize(&s).is_err());
    }
}
#[test]
fn descriptive_bootstrap_keeps_shared_station_components_together() {
    let mut s = study();
    let mut o = s.observations[0].clone();
    o.id = "two".into();
    o.input_sha256 = "d".repeat(64);
    o.station = 2;
    o.pass_group = "different".into();
    for a in &mut o.arms {
        a.input_sha256 = o.input_sha256.clone();
    }
    s.observations.push(o);
    let v = summarize(&s).unwrap();
    assert_eq!(all(&v)["descriptive_uncertainty"]["component_count"], 2);
    assert_eq!(v, summarize(&s).unwrap());
    s.observations[1].station = 1;
    assert_eq!(
        all(&summarize(&s).unwrap())["descriptive_uncertainty"]["component_count"],
        1
    );
}
