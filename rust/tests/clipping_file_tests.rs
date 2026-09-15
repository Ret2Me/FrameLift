use super::*;

#[test]
fn bounded_selector_matches_old_sqlite_choices_and_degenerate_contract() {
    let root = Path::new(env!("CARGO_MANIFEST_DIR"));
    let fixture: Value =
        serde_json::from_str(include_str!("clipping_file_fixtures/oracle.json")).unwrap();
    assert_eq!(fixture["runtime_dependency"], false);
    assert_eq!(
        input::identity(&root.join("src/telemetry_yield/blind_phase_fsk_file.py"))
            .unwrap()
            .sha256,
        fixture["source_sha256"]
    );
    let mut completed = 0;
    for case in fixture["cases"].as_array().unwrap() {
        let source = root
            .join("rust/tests")
            .join(case["input_path"].as_str().unwrap());
        let id = input::identity(&source).unwrap();
        assert_eq!(id.sha256, case["input_sha256"]);
        let config = BlindPhaseFskRunConfig {
            receiver: serde_json::from_value(case["config"].clone()).unwrap(),
            expected_input_size_bytes: id.bytes,
            expected_input_sha256: id.sha256,
            maximum_scratch_bytes: default_scratch(),
        };
        let result = select_windows_bounded(&source, &config);
        if case["status"] == "rejected" {
            assert!(result.unwrap_err().contains("baseline"));
            continue;
        }
        let (actual, counters) = result.unwrap();
        completed += 1;
        let expected: Vec<PhaseWindowCandidate> =
            serde_json::from_value(case["selected"].clone()).unwrap();
        assert_eq!(actual.len(), expected.len());
        for (a, e) in actual.iter().zip(&expected) {
            assert_eq!(a.analysis_index, e.analysis_index);
            assert_eq!(a.analysis_start_seconds, e.analysis_start_seconds);
            assert_eq!(a.decoder_start_seconds, e.decoder_start_seconds);
            assert_eq!(a.endpoint_clip_fraction, e.endpoint_clip_fraction);
            assert!((a.mean_power - e.mean_power).abs() < 1e-6);
            assert!((a.lag1_phase_coherence - e.lag1_phase_coherence).abs() < 2e-14);
            assert!((a.score - e.score).abs() < 2e-13);
        }
        for field in [
            "degenerate_input",
            "degenerate_reason",
            "analysis_windows_scanned",
            "analysis_window_bytes",
            "maximum_analysis_array_bytes",
        ] {
            assert_eq!(
                counters[field], case["counters"][field],
                "{} {field}",
                case["name"]
            );
        }
        assert_eq!(counters["actual_scratch_bytes"], 0);
        assert_eq!(counters["sqlite_storage_equivalence_claimed"], false);
    }
    assert_eq!(completed, 4);
}

#[test]
fn caller_selected_positive_fixture_binds_provenance_and_resumes_exactly() {
    let root = Path::new(env!("CARGO_MANIFEST_DIR"));
    let fixture: Value =
        serde_json::from_str(include_str!("clipping_fixtures/oracle.json")).unwrap();
    let case = &fixture["cases"][1];
    let source = root
        .join("rust/tests/clipping_fixtures")
        .join(case["filename"].as_str().unwrap());
    let id = input::identity(&source).unwrap();
    let config = BlindPhaseFskRunConfig {
        receiver: serde_json::from_value(case["config"].clone()).unwrap(),
        expected_input_size_bytes: id.bytes,
        expected_input_sha256: id.sha256,
        maximum_scratch_bytes: default_scratch(),
    };
    let selected: Vec<PhaseWindowCandidate> =
        serde_json::from_value(case["selected"].clone()).unwrap();
    let directory = tempfile::tempdir().unwrap();
    let output = directory.path().join("out");
    let result =
        run_blind_phase_fsk_file(&source, &output, &config, Some(&selected), false).unwrap();
    assert_eq!(
        result["identity"]["selection_mode"],
        "caller_selected_not_blind"
    );
    assert_eq!(result["native_detection_count"], 1);
    assert_eq!(result["repaired_candidate_detection_count"], 0);
    assert_eq!(
        result["frames"][0]["payload_hex"],
        hex::encode(
            serde_json::from_value::<Vec<u8>>(case["decode"]["frames"][0]["payload"].clone())
                .unwrap()
        )
    );
    assert_eq!(
        result["frames"][0]["fcs_hex"],
        hex::encode(
            serde_json::from_value::<Vec<u8>>(case["decode"]["frames"][0]["fcs"].clone()).unwrap()
        )
    );
    assert_eq!(
        run_blind_phase_fsk_file(&source, &output, &config, Some(&selected), true).unwrap(),
        result
    );
    assert!(
        run_blind_phase_fsk_file(&source, &output, &config, None, true)
            .unwrap_err()
            .contains("plan identity differs")
    );
}

#[test]
fn orphaned_owned_staging_is_preserved_and_safe_to_recompute() {
    let (directory, source, config) = zero_setup();
    let output = directory.path().join("out");
    run_blind_phase_fsk_file(&source, &output, &config, None, false).unwrap();
    let plan = bounded_json(&output.join("plan.json")).unwrap();
    fs::rename(
        output.join("plan.json"),
        directory.path().join("preserved-plan.json"),
    )
    .unwrap();
    fs::rename(
        output.join("result.json"),
        directory.path().join("preserved-result.json"),
    )
    .unwrap();
    let staged = output.join(format!("{}interrupted", staging_prefix(&plan)));
    fs::write(&staged, b"{partial interrupted JSON").unwrap();
    assert!(
        run_blind_phase_fsk_file(&source, &output, &config, None, false)
            .unwrap_err()
            .contains("resume")
    );
    assert_eq!(
        run_blind_phase_fsk_file(&source, &output, &config, None, true).unwrap()["status"],
        "complete"
    );
    assert_eq!(fs::read(&staged).unwrap(), b"{partial interrupted JSON");
}

#[test]
fn atomic_publish_race_never_overwrites_existing_destination() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("result.json");
    fs::write(&path, b"user-existing").unwrap();
    let plan = json!({"attempt_fingerprint":"00".repeat(32)});
    assert!(publish_new(&path, &json!({"unexpected":true}), &plan).is_err());
    assert_eq!(fs::read(&path).unwrap(), b"user-existing");
}

#[test]
fn replacing_named_lock_cannot_bypass_owned_lock_identity_check() {
    let directory = tempfile::tempdir().unwrap();
    let lock = AttemptLock::acquire(directory.path()).unwrap();
    fs::rename(
        directory.path().join("attempt.lock"),
        directory.path().join("preserved-lock"),
    )
    .unwrap();
    fs::write(directory.path().join("attempt.lock"), b"").unwrap();
    assert!(lock.verify().unwrap_err().contains("replaced"));
}

fn zero_setup() -> (tempfile::TempDir, PathBuf, BlindPhaseFskRunConfig) {
    let directory = tempfile::tempdir().unwrap();
    let source = directory.path().join("capture.ci16");
    fs::write(&source, vec![0_u8; 57600 * 4]).unwrap();
    let id = input::identity(&source).unwrap();
    let config = BlindPhaseFskRunConfig {
        receiver: BlindPhaseFskConfig::default(),
        expected_input_size_bytes: id.bytes,
        expected_input_sha256: id.sha256,
        maximum_scratch_bytes: default_scratch(),
    };
    (directory, source, config)
}

#[test]
fn file_api_zero_capture_is_completed_zero_not_a_decoder_failure() {
    let (directory, source, config) = zero_setup();
    let output = directory.path().join("out");
    let result = run_blind_phase_fsk_file(&source, &output, &config, None, false).unwrap();
    assert_eq!(result["status"], "complete");
    assert_eq!(result["decoded_windows"], 0);
    assert_eq!(result["frame_detections"], 0);
    assert_eq!(result["selector"]["degenerate_input"], true);
    assert_eq!(result["identity"]["selection_mode"], "blind_statistics");
    assert!(result["frames"].as_array().unwrap().is_empty());
    assert_eq!(
        result["caller_expected_input_identity_is_external_authentication"],
        false
    );
    let first = input::identity(&output.join("result.json")).unwrap();
    assert!(
        run_blind_phase_fsk_file(&source, &output, &config, None, false)
            .unwrap_err()
            .contains("resume")
    );
    assert_eq!(
        run_blind_phase_fsk_file(&source, &output, &config, None, true).unwrap(),
        result
    );
    assert_eq!(
        input::identity(&output.join("result.json")).unwrap().sha256,
        first.sha256
    );
}

#[test]
fn live_owned_lock_prevents_second_writer_without_killing_owner() {
    let (directory, source, config) = zero_setup();
    let output = output_directory(&directory.path().join("out")).unwrap();
    let lock = AttemptLock::acquire(&output).unwrap();
    assert!(
        run_blind_phase_fsk_file(&source, &output, &config, None, false)
            .unwrap_err()
            .contains("locked")
    );
    lock.verify().unwrap();
    drop(lock);
    assert_eq!(
        run_blind_phase_fsk_file(&source, &output, &config, None, false).unwrap()["status"],
        "complete"
    );
}

#[test]
fn tampered_completed_result_and_changed_plan_do_not_resume() {
    let (directory, source, config) = zero_setup();
    let output = directory.path().join("out");
    run_blind_phase_fsk_file(&source, &output, &config, None, false).unwrap();
    let path = output.join("result.json");
    let mut result = bounded_json(&path).unwrap();
    result["decoded_windows"] = json!(1);
    fs::write(&path, serde_json::to_vec(&result).unwrap()).unwrap();
    assert!(
        run_blind_phase_fsk_file(&source, &output, &config, None, true)
            .unwrap_err()
            .contains("corrupt")
    );
    let mut changed = config.clone();
    changed.maximum_scratch_bytes += 1024 * 1024;
    assert!(
        run_blind_phase_fsk_file(&source, &output, &changed, None, true)
            .unwrap_err()
            .contains("plan identity differs")
    );
}

#[test]
fn self_consistent_tamper_still_fails_recomputed_result() {
    let (directory, source, config) = zero_setup();
    let output = directory.path().join("out");
    run_blind_phase_fsk_file(&source, &output, &config, None, false).unwrap();
    let path = output.join("result.json");
    let mut result = bounded_json(&path).unwrap();
    result["decoded_windows"] = json!(1);
    result
        .as_object_mut()
        .unwrap()
        .remove("result_payload_sha256");
    result["result_payload_sha256"] = json!(fingerprint(&result).unwrap());
    fs::write(&path, serde_json::to_vec(&result).unwrap()).unwrap();
    assert!(
        run_blind_phase_fsk_file(&source, &output, &config, None, true)
            .unwrap_err()
            .contains("deterministic revalidation")
    );
}

#[test]
fn failed_pending_attempt_requires_explicit_resume_and_keeps_identity() {
    let (directory, source, config) = zero_setup();
    let output = directory.path().join("out");
    run_blind_phase_fsk_file(&source, &output, &config, None, false).unwrap();
    fs::rename(
        output.join("result.json"),
        directory.path().join("preserved-first-result.json"),
    )
    .unwrap();
    assert!(run_blind_phase_fsk_file(&source, &output, &config, None, false).is_err());
    assert_eq!(
        run_blind_phase_fsk_file(&source, &output, &config, None, true).unwrap()["status"],
        "complete"
    );
    assert!(
        run_blind_phase_fsk_file(
            &source,
            &directory.path().join("nonexistent"),
            &config,
            None,
            true
        )
        .unwrap_err()
        .contains("existing matching plan")
    );
}

#[test]
fn content_mismatch_and_symlinks_are_rejected_without_clobber() {
    use std::os::unix::fs::symlink;
    let (directory, source, mut config) = zero_setup();
    config.expected_input_sha256 = "00".repeat(32);
    assert!(
        run_blind_phase_fsk_file(&source, &directory.path().join("out"), &config, None, false)
            .unwrap_err()
            .contains("expected identity")
    );
    let id = input::identity(&source).unwrap();
    config.expected_input_sha256 = id.sha256;
    let link = directory.path().join("source-link");
    symlink(&source, &link).unwrap();
    assert!(
        run_blind_phase_fsk_file(&link, &directory.path().join("out"), &config, None, false)
            .is_err()
    );
    let out = output_directory(&directory.path().join("out")).unwrap();
    symlink(&source, out.join("attempt.lock")).unwrap();
    assert!(run_blind_phase_fsk_file(&source, &out, &config, None, false).is_err());
    assert_eq!(
        input::identity(&source).unwrap().sha256,
        config.expected_input_sha256
    );
}

#[test]
fn production_bounds_reject_overflow_aggregate_work_and_reserved_fields() {
    let (_, _, config) = zero_setup();
    config.validate().unwrap();
    let mut c = config.clone();
    c.receiver.short_maximum_flips = 3;
    assert!(c.validate().is_err());
    let mut c = config.clone();
    c.receiver.candidate_window_limit = 4096;
    assert!(c.validate().is_err());
    let mut c = config.clone();
    c.receiver.maximum_map_seed_states = usize::MAX;
    assert!(c.validate().is_err());
    assert!(decoder_working_set_model_bytes(&c.receiver).is_err());
    let mut c = config.clone();
    c.receiver.constant_radius_factors = vec![3.0; 65];
    assert!(c.validate().is_err());
    let mut c = config.clone();
    c.receiver.error_unit_penalty = f64::INFINITY;
    assert!(c.validate().is_err());
}

#[test]
fn caller_selected_empty_or_unbounded_windows_are_not_called_blind() {
    let (directory, source, config) = zero_setup();
    assert!(
        run_blind_phase_fsk_file(
            &source,
            &directory.path().join("out"),
            &config,
            Some(&[]),
            false
        )
        .is_err()
    );
}

#[test]
fn unknown_output_files_are_preserved_and_not_reused() {
    let (directory, source, config) = zero_setup();
    let output = output_directory(&directory.path().join("out")).unwrap();
    fs::write(output.join("user-notes.txt"), b"preserve").unwrap();
    assert!(
        run_blind_phase_fsk_file(&source, &output, &config, None, false)
            .unwrap_err()
            .contains("unexpected artifact")
    );
    assert_eq!(
        fs::read(output.join("user-notes.txt")).unwrap(),
        b"preserve"
    );
}
