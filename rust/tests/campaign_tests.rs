use super::*;
use std::sync::Mutex;

static TEST_RUN: Mutex<()> = Mutex::new(());

struct Fixture {
    temp: tempfile::TempDir,
    summary: PathBuf,
    output: PathBuf,
}
fn fixture(count: usize, broken: bool) -> Fixture {
    let temp = tempfile::tempdir().unwrap();
    let root = temp.path().join("reference");
    fs::create_dir(&root).unwrap();
    fs::create_dir(root.join("summaries")).unwrap();
    fs::create_dir(root.join("observations")).unwrap();
    input::write_json_new(
        &root.join("plan.json"),
        &json!({"schema":"test-reference-plan"}),
    )
    .unwrap();
    let plan = input::identity(&root.join("plan.json")).unwrap();
    let ids: Vec<_> = (1..=count as u64).collect();
    input::write_json_new(
        &root.join("cohort.json"),
        &json!({"ids":ids,"selected_count":count}),
    )
    .unwrap();
    let cohort = input::identity(&root.join("cohort.json")).unwrap();
    input::write_json_new(
        &root.join("freeze.json"),
        &json!({"plan":plan,"cohort":cohort}),
    )
    .unwrap();
    let mut observations = Vec::new();
    for id in &ids {
        let observation = root.join(format!("observations/{id}"));
        fs::create_dir(&observation).unwrap();
        let attempt = observation.join("attempt-001");
        fs::create_dir(&attempt).unwrap();
        let source = observation.join("capture.ogg");
        if broken {
            fs::write(&source, b"RIFFbroken-wave-data").unwrap();
        } else {
            // The loader detects RIFF magic, not the filename extension. This
            // keeps orchestration tests pure Rust, without codec subprocesses.
            let spec = hound::WavSpec {
                channels: 1,
                sample_rate: 48_000,
                bits_per_sample: 32,
                sample_format: hound::SampleFormat::Float,
            };
            let mut writer = hound::WavWriter::create(&source, spec).unwrap();
            for _ in 0..9_001 {
                writer.write_sample(0.0_f32).unwrap();
            }
            writer.finalize().unwrap();
        }
        let source_identity = input::identity(&source).unwrap();
        let pcm = input::Identity {
            path: attempt.join("audio.wav").display().to_string(),
            sha256: source_identity.sha256.clone(),
            bytes: source_identity.bytes,
        };
        input::write_json_new(&attempt.join("native.plan.json"), &json!({"input":pcm})).unwrap();
        let native_plan = input::identity(&attempt.join("native.plan.json")).unwrap();
        input::write_json_new(&attempt.join("native.json"),&json!({"schema":"native-audio-refinement-result-v2",
            "input":pcm,"plan_identity":native_plan,"frames":[{"test_secret_reference_pdu":"not passed to receiver"}]})).unwrap();
        input::write_json_new(
            &attempt.join("input-manifest.json"),
            &json!({"observation_id":id,"wav":pcm,"ogg":source_identity}),
        )
        .unwrap();
        fs::write(attempt.join("native.windows.jsonl"), b"{}\n").unwrap();
        let mut artifacts: Vec<_> = [
            "native.plan.json",
            "native.json",
            "input-manifest.json",
            "native.windows.jsonl",
        ]
        .iter()
        .map(|name| input::identity(&attempt.join(name)).unwrap())
        .collect();
        artifacts.push(source_identity.clone());
        let row = json!({"observation_id":id,"status":"complete","native_status":"complete","baseline_status":"complete",
            "plan_sha256":plan.sha256,"attempt":attempt,"artifacts":artifacts,"external_artifacts":[source_identity],
            "comparison":{"same_audio_sha256":pcm.sha256}});
        input::write_json_new(&attempt.join("final.json"), &row).unwrap();
        input::write_json_new(
            &attempt.join("commit.json"),
            &json!({"final":input::identity(&attempt.join("final.json")).unwrap()}),
        )
        .unwrap();
        observations.push(row);
    }
    let summary = root.join("summaries/terminal.json");
    input::write_json_new(&summary,&json!({"schema":"ogg-archive-campaign-summary-v1","complete_campaign":true,
        "frozen_count":count,"complete_comparisons":count,"plan_sha256":plan.sha256,"observations":observations})).unwrap();
    let output = temp.path().join("candidate");
    Fixture {
        temp,
        summary,
        output,
    }
}

#[test]
fn complete_resume_reuses_hash_committed_results_without_new_attempts() {
    let _serial = TEST_RUN.lock().unwrap();
    let f = fixture(2, false);
    let config = receiver::DecodeConfig::default();
    let options = CampaignOptions::default();
    let first = run_local(&f.summary, &f.output, &config, &options).unwrap();
    assert_eq!(first["complete"], 2, "{first}");
    assert_eq!(first["pass"], true);
    let result_identity = input::identity(&f.output.join("1/result.json")).unwrap();
    let second = run_local(
        &f.summary,
        &f.output,
        &config,
        &CampaignOptions {
            resume: true,
            ..options
        },
    )
    .unwrap();
    assert_eq!(second["complete"], 2);
    assert_eq!(second["pass"], true);
    assert!(
        second["observations"]
            .as_array()
            .unwrap()
            .iter()
            .all(|row| row["reused"] == true)
    );
    assert!(same_identity(
        &result_identity,
        &input::identity(&f.output.join("1/result.json")).unwrap()
    ));
    assert_eq!(
        fs::read_dir(f.output.join("1/attempts")).unwrap().count(),
        1
    );
    let plan = fs::read_to_string(f.output.join("batch-plan.json")).unwrap();
    assert!(!plan.contains("test_secret_reference_pdu"));
    assert!(run_local(&f.summary, &f.output, &config, &CampaignOptions::default()).is_err());
}

#[test]
fn resume_rejects_changed_configuration_or_reference_source() {
    let _serial = TEST_RUN.lock().unwrap();
    let f = fixture(1, false);
    let config = receiver::DecodeConfig::default();
    run_local(&f.summary, &f.output, &config, &CampaignOptions::default()).unwrap();
    let mut changed = config.clone();
    changed.dsp.bank = "global".into();
    assert!(
        run_local(
            &f.summary,
            &f.output,
            &changed,
            &CampaignOptions {
                resume: true,
                ..Default::default()
            }
        )
        .is_err()
    );
    let source = f.temp.path().join("reference/observations/1/capture.ogg");
    let mut file = OpenOptions::new().append(true).open(source).unwrap();
    file.write_all(b"changed").unwrap();
    assert!(
        run_local(
            &f.summary,
            &f.output,
            &config,
            &CampaignOptions {
                resume: true,
                ..Default::default()
            }
        )
        .is_err()
    );
}

#[test]
fn corrupt_published_journal_pauses_without_redecoding_or_overwriting() {
    let _serial = TEST_RUN.lock().unwrap();
    let f = fixture(1, false);
    let config = receiver::DecodeConfig::default();
    run_local(&f.summary, &f.output, &config, &CampaignOptions::default()).unwrap();
    let path = f.output.join("1/windows.jsonl");
    OpenOptions::new()
        .append(true)
        .open(&path)
        .unwrap()
        .write_all(b"{}\n")
        .unwrap();
    let changed = input::identity(&path).unwrap();
    let resumed = run_local(
        &f.summary,
        &f.output,
        &config,
        &CampaignOptions {
            resume: true,
            ..Default::default()
        },
    )
    .unwrap();
    assert_eq!(resumed["status"], "paused");
    assert_eq!(resumed["pass"], false);
    assert!(
        resumed["pause"]
            .as_str()
            .unwrap()
            .contains("identity mismatch")
    );
    assert_eq!(
        fs::read_dir(f.output.join("1/attempts")).unwrap().count(),
        1
    );
    assert!(same_identity(&changed, &input::identity(&path).unwrap()));
}

#[test]
fn interrupted_publication_finishes_from_verified_ready_marker() {
    let _serial = TEST_RUN.lock().unwrap();
    let f = fixture(1, false);
    let config = receiver::DecodeConfig::default();
    run_local(&f.summary, &f.output, &config, &CampaignOptions::default()).unwrap();
    // Simulate a crash before final publication, only in newly generated test data.
    fs::rename(
        f.output.join("1/commit.json"),
        f.output.join("saved-test-commit.json"),
    )
    .unwrap();
    fs::rename(
        f.output.join("1/windows.jsonl"),
        f.output.join("saved-test-journal.jsonl"),
    )
    .unwrap();
    // A crash before its first checkpoint has no published commitment in
    // progress records. Keep our generated later records as inert test data.
    for name in ["checkpoints", "summaries"] {
        fs::rename(
            f.output.join(name),
            f.output.join(format!("saved-test-{name}")),
        )
        .unwrap();
        fs::create_dir(f.output.join(name)).unwrap();
    }
    fs::rename(
        f.output.join("summary.json"),
        f.output.join("saved-test-summary.json"),
    )
    .unwrap();
    let resumed = run_local(
        &f.summary,
        &f.output,
        &config,
        &CampaignOptions {
            resume: true,
            ..Default::default()
        },
    )
    .unwrap();
    assert_eq!(resumed["pass"], true);
    assert_eq!(resumed["observations"][0]["reused"], true);
    assert_eq!(
        fs::read_dir(f.output.join("1/attempts")).unwrap().count(),
        1
    );
    assert_eq!(
        fs::read(f.output.join("1/commit.json")).unwrap(),
        fs::read(f.output.join("saved-test-commit.json")).unwrap()
    );
}

#[test]
fn failed_attempts_are_retained_and_retried_in_new_directories() {
    let _serial = TEST_RUN.lock().unwrap();
    let f = fixture(1, true);
    let config = receiver::DecodeConfig::default();
    let first = run_local(&f.summary, &f.output, &config, &CampaignOptions::default()).unwrap();
    assert_eq!(first["status"], "failed");
    assert_eq!(first["failed"], 1);
    assert!(!f.output.join("1/result.json").exists());
    assert!(!f.output.join("summary.json").exists());
    let failure =
        input::identity(&f.output.join("1/attempts/attempt-000001/failure.json")).unwrap();
    let second = run_local(
        &f.summary,
        &f.output,
        &config,
        &CampaignOptions {
            resume: true,
            ..Default::default()
        },
    )
    .unwrap();
    assert_eq!(second["failed"], 1);
    assert_eq!(
        fs::read_dir(f.output.join("1/attempts")).unwrap().count(),
        2
    );
    assert!(same_identity(
        &failure,
        &input::identity(Path::new(&failure.path)).unwrap()
    ));
}

#[test]
fn kernel_lock_has_one_owner_and_can_be_reacquired_without_pid_signalling() {
    let temp = tempfile::tempdir().unwrap();
    let first = CampaignLock::acquire(temp.path()).unwrap();
    assert!(CampaignLock::acquire(temp.path()).is_err());
    drop(first);
    assert!(CampaignLock::acquire(temp.path()).is_ok());
}

#[test]
fn campaign_sparse_disk_budget_and_symlink_are_rejected() {
    let temp = tempfile::tempdir().unwrap();
    let file = File::create(temp.path().join("sparse-test-file")).unwrap();
    file.set_len(MAX_CAMPAIGN_BYTES).unwrap();
    assert!(resource_check(temp.path(), 1).is_err());
    #[cfg(unix)]
    {
        std::os::unix::fs::symlink("sparse-test-file", temp.path().join("symlink")).unwrap();
        assert!(campaign_bytes(temp.path()).is_err());
    }
}

#[test]
fn reference_selection_rejects_duplicates_and_freeze_replacement() {
    let f = fixture(2, false);
    let selected = reference_inputs(
        &f.summary,
        &CampaignOptions {
            observation_id: Some(2),
            ..Default::default()
        },
    )
    .unwrap();
    assert_eq!(selected.selected.len(), 1);
    assert_eq!(selected.selected[0].observation_id, 2);
    assert!(
        reference_inputs(
            &f.summary,
            &CampaignOptions {
                observation_id: Some(3),
                ..Default::default()
            }
        )
        .is_err()
    );
    let mut value = input::read_json(&f.summary).unwrap();
    value["observations"][1] = value["observations"][0].clone();
    fs::write(&f.summary, serde_json::to_vec(&value).unwrap()).unwrap();
    assert!(reference_inputs(&f.summary, &CampaignOptions::default()).is_err());
}

#[test]
fn coherent_commit_rewrite_is_rejected_by_historical_checkpoint_binding() {
    let _serial = TEST_RUN.lock().unwrap();
    let f = fixture(1, false);
    let config = receiver::DecodeConfig::default();
    run_local(&f.summary, &f.output, &config, &CampaignOptions::default()).unwrap();
    // Even harmless whitespace changes commitment bytes. Resume must not
    // silently substitute a new commitment into existing run history.
    OpenOptions::new()
        .append(true)
        .open(f.output.join("1/commit.json"))
        .unwrap()
        .write_all(b"\n")
        .unwrap();
    assert!(
        run_local(
            &f.summary,
            &f.output,
            &config,
            &CampaignOptions {
                resume: true,
                ..Default::default()
            }
        )
        .is_err()
    );
}

#[test]
fn stop_handler_requests_cooperative_stop_without_signalling_other_processes() {
    let _serial = TEST_RUN.lock().unwrap();
    let guard = StopSignals::install().unwrap();
    assert!(!STOP_REQUESTED.load(Ordering::SeqCst));
    stop_signal(libc::SIGINT);
    assert!(STOP_REQUESTED.load(Ordering::SeqCst));
    drop(guard);
    STOP_REQUESTED.store(false, Ordering::SeqCst);
}
