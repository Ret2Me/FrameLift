use super::*;
use tempfile::TempDir;

struct Fixture {
    _temp: TempDir,
    summary: PathBuf,
    results: PathBuf,
    attempt: PathBuf,
    candidate: PathBuf,
}

fn write_json(path: &Path, value: &Value) {
    fs::write(path, serde_json::to_vec_pretty(value).unwrap()).unwrap();
}

fn identity(path: &Path) -> Value {
    let (hash, bytes) = digest_file(path).unwrap();
    json!({"path":path,"sha256":hash,"bytes":bytes})
}

fn pdu_frame(information: u8) -> Value {
    let mut pdu = Vec::new();
    for (call, ssid) in [(b"CQ    ", 0x60), (b"N0CALL", 0x61)] {
        pdu.extend(call.iter().map(|b| b << 1));
        pdu.push(ssid);
    }
    pdu.extend([0x03, 0xf0, information]);
    let mut crc = 0xffff_u16;
    for byte in &pdu {
        crc ^= u16::from(*byte);
        for _ in 0..8 {
            if crc & 1 == 1 {
                crc = (crc >> 1) ^ 0x8408;
            } else {
                crc >>= 1;
            }
        }
    }
    let mut full = pdu.clone();
    full.extend((!crc).to_le_bytes());
    json!({
        "payload_hex":hex::encode(&pdu),
        "frame_with_fcs_hex":hex::encode(full),
        "payload_sha256":hex::encode(Sha256::digest(&pdu)),
        "payload_bytes":pdu.len(),
        "validation":"crc16_x25+ax25_ui",
        "independent_bitwise_crc_passed":true,
        "provenance":[{
            "window_start_seconds":0.0,
            "waveform":"fm_demodulated:positive-audio-pilot-fsk-9600-v1",
            "timing_rank":0,
            "timing_score":3.0
        }]
    })
}

fn fixture(with_frame: bool) -> Fixture {
    let temp = tempfile::tempdir().unwrap();
    let reference = temp.path().join("reference");
    let summaries = reference.join("summaries");
    let attempt = reference.join("observations/7/attempt-001");
    let results = temp.path().join("results");
    let candidate = results.join("7");
    fs::create_dir_all(&summaries).unwrap();
    fs::create_dir_all(&attempt).unwrap();
    fs::create_dir_all(&candidate).unwrap();
    write_json(&reference.join("plan.json"), &json!({"frozen":"test"}));
    let plan_hash = digest_file(&reference.join("plan.json")).unwrap().0;
    let wav = attempt.join("audio.wav");
    fs::write(&wav, b"immutable test waveform identity").unwrap();
    let input = identity(&wav);
    let frame = pdu_frame(0x42);
    let count = usize::from(with_frame);
    let frames = if with_frame {
        vec![frame.clone()]
    } else {
        vec![]
    };
    let bytes = if with_frame {
        frame["payload_bytes"].as_u64().unwrap()
    } else {
        0
    };
    let row = json!({"offset_seconds":0.0,"samples":288000,"frame_instances":count,
        "unique_total":count,"failures":[],"timing_attempts":2});
    let journal = format!("{}\n", serde_json::to_string(&row).unwrap());
    fs::write(attempt.join("native.windows.jsonl"), &journal).unwrap();
    fs::write(candidate.join("windows.jsonl"), &journal).unwrap();
    write_json(&attempt.join("native.plan.json"), &json!({"input":input}));
    let native = json!({
        "schema":"native-audio-refinement-result-v2",
        "input":input,
        "plan_identity":identity(&attempt.join("native.plan.json")),
        "elapsed_seconds":12.0,"window_count":1,"failed_window_count":0,
        "unique_pdu_count":count,"unique_pdu_bytes":bytes,"frames":frames
    });
    write_json(&attempt.join("native.json"), &native);
    let mut new_result = native.clone();
    new_result["status"] = json!("complete");
    new_result["observation_id"] = json!(7);
    new_result["elapsed_seconds"] = json!(1.0);
    write_json(&candidate.join("result.json"), &new_result);
    write_json(
        &attempt.join("input-manifest.json"),
        &json!({"observation_id":7,"wav":input}),
    );
    let artifacts: Vec<_> = [
        "native.json",
        "native.plan.json",
        "native.windows.jsonl",
        "input-manifest.json",
    ]
    .iter()
    .map(|name| identity(&attempt.join(name)))
    .collect();
    let pdus = if with_frame {
        vec![frame["payload_hex"].clone()]
    } else {
        vec![]
    };
    let observation = json!({
        "observation_id":7,"status":"complete","native_status":"complete","baseline_status":"complete",
        "plan_sha256":plan_hash,"attempt":attempt,"artifacts":artifacts,
        "comparison":{"same_audio_sha256":input["sha256"],
            "counts":{"native_same_audio":{"pdus":pdus,"count":count,"bytes":bytes}}}
    });
    write_json(&attempt.join("final.json"), &observation);
    write_json(
        &attempt.join("commit.json"),
        &json!({"final":identity(&attempt.join("final.json"))}),
    );
    let summary = summaries.join("frozen.json");
    write_json(
        &summary,
        &json!({
            "schema":"ogg-archive-campaign-summary-v1","complete_campaign":true,
            "frozen_count":1,"complete_comparisons":1,"plan_sha256":plan_hash,
            "observations":[observation],
            "per_observation_deduplicated_totals":{"native_same_audio":{"count":count}},
            "global_union_of_observation_local_sets":{"native_same_audio":{"count":count}}
        }),
    );
    Fixture {
        _temp: temp,
        summary,
        results,
        attempt,
        candidate,
    }
}

fn mutate_json(path: &Path, mutation: impl FnOnce(&mut Value)) {
    let mut value = read_json(path).unwrap();
    mutation(&mut value);
    write_json(path, &value);
}

fn candidate_inventory_fixture() -> Fixture {
    let f = fixture(true);
    mutate_json(&f.attempt.join("native.plan.json"), |value| {
        value["decoder"] = json!("fast");
        value["reference_bytes_used_for_search"] = json!(false);
    });
    mutate_json(&f.attempt.join("native.json"), |value| {
        value["plan_identity"] = identity(&f.attempt.join("native.plan.json"));
    });
    fs::create_dir(f.attempt.join("baseline")).unwrap();
    fs::write(f.attempt.join("baseline/frames.kiss"), []).unwrap();
    let input = read_json(&f.attempt.join("input-manifest.json")).unwrap();
    write_json(
        &f.attempt.join("baseline/result.json"),
        &json!({
            "completed":true,"timed_out":false,"returncode":0,"input_sha256":input["wav"]["sha256"]
        }),
    );
    let frame = pdu_frame(0x42);
    let one = json!({"pdus":[frame["payload_hex"]],"count":1,"bytes":frame["payload_bytes"]});
    let zero = json!({"pdus":[],"count":0,"bytes":0});
    let mut row = read_json(&f.attempt.join("final.json")).unwrap();
    row["references"] = json!({"complete":true,"failures":[],"listed_count":0,"objects":[]});
    for name in ["archive", "baseline_same_audio", "baseline_only_vs_native"] {
        row["comparison"]["counts"][name] = zero.clone();
    }
    for name in [
        "native_new_vs_archive",
        "native_new_vs_archive_and_baseline",
    ] {
        row["comparison"]["counts"][name] = one.clone();
    }
    row["artifacts"] = json!(
        [
            "native.json",
            "native.plan.json",
            "native.windows.jsonl",
            "input-manifest.json",
            "baseline/result.json",
            "baseline/frames.kiss"
        ]
        .iter()
        .map(|name| identity(&f.attempt.join(name)))
        .collect::<Vec<_>>()
    );
    write_json(&f.attempt.join("final.json"), &row);
    write_json(
        &f.attempt.join("commit.json"),
        &json!({"final":identity(&f.attempt.join("final.json"))}),
    );
    mutate_json(&f.summary, |value| {
        value["observations"][0] = row;
        value["globally_absent_from_entire_cohort_archive"] = one;
    });
    f
}

#[test]
fn candidate_inventory_replay_is_not_transmitter_authentication() {
    let f = candidate_inventory_fixture();
    let result = candidates::inventory(&f.summary, &f.results).unwrap();
    assert_eq!(result["global_archive_absent_pdus"], 1);
    assert_eq!(
        result["candidates"][0]["secondary_rust_replay_verified"],
        true
    );
    assert_eq!(result["candidates"][0]["origin_attribution"], "unverified");
    assert_eq!(
        result["candidates"][0]["address_pair_present_in_archive"],
        false
    );
    assert_eq!(
        result["candidates"][0]["encapsulated_ccsds_primary_header"],
        Value::Null
    );
    assert_eq!(result["mission_attribution_verified"], false);
    assert_eq!(result["publication_ready"], false);
}

#[test]
fn candidate_inventory_rejects_incomplete_archive_and_replay_tampering() {
    let f = candidate_inventory_fixture();
    mutate_json(&f.summary, |value| {
        value["observations"][0]["references"]["complete"] = json!(false)
    });
    assert!(candidates::inventory(&f.summary, &f.results).is_err());
    let f = candidate_inventory_fixture();
    mutate_json(&f.candidate.join("result.json"), |value| {
        value["frames"][0] = pdu_frame(0x43)
    });
    assert!(candidates::inventory(&f.summary, &f.results).is_err());
}

#[test]
fn candidate_inventory_rejects_changed_kiss_and_global_absence_claim() {
    let f = candidate_inventory_fixture();
    fs::write(
        f.attempt.join("baseline/frames.kiss"),
        [0xc0, 0x00, 0x01, 0xc0],
    )
    .unwrap();
    assert!(candidates::inventory(&f.summary, &f.results).is_err());
    let f = candidate_inventory_fixture();
    mutate_json(&f.summary, |value| {
        value["globally_absent_from_entire_cohort_archive"] = json!({"pdus":[],"count":0,"bytes":0})
    });
    assert!(candidates::inventory(&f.summary, &f.results).is_err());
}

#[test]
fn exact_frame_and_all_evidence_pass() {
    let f = fixture(true);
    let audit = compare_campaign(&f.summary, &f.results).unwrap();
    assert_eq!(audit["pass"], true);
    assert_eq!(audit["reference_pdus_deduplicated_per_observation"], 1);
    assert_eq!(audit["all_timing_scores_bitwise_equal"], true);
    assert_eq!(audit["universal_zero_accuracy_loss_proven"], false);
}

#[test]
fn completed_zero_frame_window_is_not_a_failure() {
    let f = fixture(false);
    assert_eq!(
        compare_campaign(&f.summary, &f.results).unwrap()["pass"],
        true
    );
}

#[test]
fn same_count_different_valid_payload_fails() {
    let f = fixture(true);
    mutate_json(&f.candidate.join("result.json"), |value| {
        value["frames"][0] = pdu_frame(0x43)
    });
    let audit = compare_campaign(&f.summary, &f.results).unwrap();
    assert_eq!(audit["pass"], false);
    assert_eq!(audit["missing_pdus_deduplicated_per_observation"], 1);
    assert_eq!(audit["additional_pdus_deduplicated_per_observation"], 1);
}

#[test]
fn different_scores_are_separate_from_exact_decisions() {
    let f = fixture(true);
    mutate_json(&f.candidate.join("result.json"), |value| {
        value["frames"][0]["provenance"][0]["timing_score"] = json!(3.00000000001);
    });
    let audit = compare_campaign(&f.summary, &f.results).unwrap();
    assert_eq!(audit["pass"], true);
    assert_eq!(audit["all_timing_scores_bitwise_equal"], false);
    assert_eq!(
        audit["comparisons"][0]["timing_scores"]["bitwise_differences"],
        1
    );
}

#[test]
fn different_timing_rank_fails_even_if_frame_is_same() {
    let f = fixture(true);
    mutate_json(&f.candidate.join("result.json"), |value| {
        value["frames"][0]["provenance"][0]["timing_rank"] = json!(1);
    });
    let audit = compare_campaign(&f.summary, &f.results).unwrap();
    assert_eq!(audit["pass"], false);
    assert_eq!(audit["ordered_provenance_differences"], 1);
}

#[test]
fn different_hypothesis_accounting_fails() {
    let f = fixture(true);
    let mut rows = read_journal(&f.candidate.join("windows.jsonl")).unwrap();
    rows[0]["timing_attempts"] = json!(3);
    fs::write(f.candidate.join("windows.jsonl"), format!("{}\n", rows[0])).unwrap();
    let audit = compare_campaign(&f.summary, &f.results).unwrap();
    assert_eq!(audit["pass"], false);
    assert_eq!(audit["differing_window_count"], 1);
}

#[test]
fn missing_result_fails_closed() {
    let f = fixture(false);
    fs::remove_file(f.candidate.join("result.json")).unwrap();
    assert!(compare_campaign(&f.summary, &f.results).is_err());
}

#[test]
fn missing_window_journal_fails_closed() {
    let f = fixture(false);
    fs::remove_file(f.candidate.join("windows.jsonl")).unwrap();
    assert!(compare_campaign(&f.summary, &f.results).is_err());
}

#[test]
fn empty_journal_cannot_prove_completion() {
    let f = fixture(false);
    fs::write(f.candidate.join("windows.jsonl"), b"").unwrap();
    mutate_json(&f.candidate.join("result.json"), |v| {
        v["window_count"] = json!(0)
    });
    assert!(compare_campaign(&f.summary, &f.results).is_err());
}

#[test]
fn failed_zero_frame_run_is_rejected() {
    let f = fixture(false);
    mutate_json(&f.candidate.join("result.json"), |v| {
        v["status"] = json!("native_failed")
    });
    assert!(compare_campaign(&f.summary, &f.results).is_err());
}

#[test]
fn no_vacuous_empty_reference_pass() {
    let f = fixture(false);
    mutate_json(&f.summary, |v| {
        v["observations"] = json!([]);
        v["frozen_count"] = json!(0);
        v["complete_comparisons"] = json!(0);
    });
    assert!(compare_campaign(&f.summary, &f.results).is_err());
}

#[test]
fn unexpected_result_directory_is_rejected() {
    let f = fixture(false);
    fs::create_dir(f.results.join("999")).unwrap();
    assert!(compare_campaign(&f.summary, &f.results).is_err());
}

#[test]
fn malformed_id_alias_is_rejected() {
    let f = fixture(false);
    fs::create_dir(f.results.join("007")).unwrap();
    assert!(compare_campaign(&f.summary, &f.results).is_err());
}

#[test]
fn wrong_input_sha_fails_before_frame_comparison() {
    let f = fixture(true);
    mutate_json(&f.candidate.join("result.json"), |v| {
        v["input"]["sha256"] = json!("0".repeat(64))
    });
    let error = compare_campaign(&f.summary, &f.results).unwrap_err();
    assert!(error.contains("input SHA-256"));
}

#[test]
fn tampered_reference_commit_is_rejected() {
    let f = fixture(true);
    mutate_json(&f.attempt.join("commit.json"), |v| {
        v["final"]["sha256"] = json!("f".repeat(64))
    });
    assert!(
        compare_campaign(&f.summary, &f.results)
            .unwrap_err()
            .contains("identity mismatch")
    );
}

#[test]
fn tampered_reference_native_artifact_is_rejected() {
    let f = fixture(true);
    mutate_json(&f.attempt.join("native.json"), |v| {
        v["elapsed_seconds"] = json!(0.0)
    });
    assert!(
        compare_campaign(&f.summary, &f.results)
            .unwrap_err()
            .contains("identity mismatch")
    );
}

#[test]
fn tampered_summary_does_not_override_commit() {
    let f = fixture(true);
    mutate_json(&f.summary, |v| {
        v["observations"][0]["native_status"] = json!("fake")
    });
    assert!(compare_campaign(&f.summary, &f.results).is_err());
}

#[test]
fn bad_crc_cannot_be_hidden_by_boolean_marker() {
    let f = fixture(true);
    mutate_json(&f.candidate.join("result.json"), |v| {
        let full = v["frames"][0]["frame_with_fcs_hex"]
            .as_str()
            .unwrap()
            .to_string();
        let last = if full.ends_with("00") { "01" } else { "00" };
        v["frames"][0]["frame_with_fcs_hex"] = json!(format!("{}{last}", &full[..full.len() - 2]));
    });
    assert!(
        compare_campaign(&f.summary, &f.results)
            .unwrap_err()
            .contains("FCS validation")
    );
}

#[test]
fn duplicate_pdu_record_is_rejected() {
    let f = fixture(true);
    mutate_json(&f.candidate.join("result.json"), |v| {
        let frame = v["frames"][0].clone();
        v["frames"].as_array_mut().unwrap().push(frame);
    });
    assert!(compare_campaign(&f.summary, &f.results).is_err());
}

#[test]
fn provenance_cannot_claim_unexecuted_window() {
    let f = fixture(true);
    mutate_json(&f.candidate.join("result.json"), |v| {
        v["frames"][0]["provenance"][0]["window_start_seconds"] = json!(3.0);
    });
    assert!(
        compare_campaign(&f.summary, &f.results)
            .unwrap_err()
            .contains("unexecuted window")
    );
}

#[test]
fn journal_count_tampering_is_rejected() {
    let f = fixture(true);
    let mut rows = read_journal(&f.candidate.join("windows.jsonl")).unwrap();
    rows[0]["frame_instances"] = json!(0);
    fs::write(f.candidate.join("windows.jsonl"), format!("{}\n", rows[0])).unwrap();
    assert!(
        compare_campaign(&f.summary, &f.results)
            .unwrap_err()
            .contains("instances disagree")
    );
}

#[test]
fn forged_global_count_is_rejected() {
    let f = fixture(true);
    mutate_json(&f.summary, |v| {
        v["global_union_of_observation_local_sets"]["native_same_audio"]["count"] = json!(12);
    });
    assert!(compare_campaign(&f.summary, &f.results).is_err());
}

#[test]
fn single_observation_audit_is_labeled_subset_only() {
    let f = fixture(true);
    let audit = compare_observation(&f.summary, 7, &f.candidate).unwrap();
    assert_eq!(audit["pass"], true);
    assert_eq!(
        audit["scope"],
        "one_explicit_observation_not_campaign_qualification"
    );
    assert!(compare_observation(&f.summary, 999, &f.candidate).is_err());
}

#[cfg(unix)]
#[test]
fn result_symlink_escape_is_rejected() {
    let f = fixture(true);
    let external = f._temp.path().join("external-result.json");
    fs::rename(f.candidate.join("result.json"), &external).unwrap();
    std::os::unix::fs::symlink(&external, f.candidate.join("result.json")).unwrap();
    assert!(
        compare_campaign(&f.summary, &f.results)
            .unwrap_err()
            .contains("escapes")
    );
}

#[test]
#[ignore = "read-only artifact replay requires the local frozen research dataset"]
fn frozen_93_observation_reference_integrity() {
    let path = Path::new(
        "work/satnogs-ogg-archive-week-20260831-v1/summaries/0ff4bc3197c74436abc0e1b51ac479ec.json",
    );
    let campaign = load_campaign(path).unwrap();
    let mut observation_count = 0;
    let mut pdus = 0;
    let mut global = BTreeSet::new();
    let mut artifacts = 0;
    let mut windows = 0;
    for row in array(&campaign.summary, "observations").unwrap() {
        if row["status"] != "complete" {
            continue;
        }
        let reference = verify_reference(&campaign, row).unwrap();
        observation_count += 1;
        pdus += reference.frames.len();
        global.extend(reference.frames.keys().cloned());
        artifacts += reference.artifact_count;
        windows += reference.windows.len();
    }
    assert_eq!((observation_count, pdus, global.len()), (93, 477, 313));
    println!(
        "{}",
        json!({
            "scope":"reference_integrity_only_no_Rust_recovery_claim",
            "reference_summary_sha256":campaign.sha256,
            "observations":observation_count,
            "pdus_per_observation":pdus,
            "global_pdus":global.len(),
            "artifacts_hash_verified":artifacts,
            "windows_consistency_verified":windows
        })
    );
}
