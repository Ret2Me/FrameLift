use super::*;
use std::sync::atomic::{AtomicUsize, Ordering as AtomicOrdering};
use std::time::Duration;

struct ProbeDemod {
    calls: Arc<AtomicUsize>,
    failures: Arc<AtomicUsize>,
    identity: Option<String>,
    family: String,
    features: Vec<String>,
    pause: Duration,
}
impl Demodulator for ProbeDemod {
    fn accepts(&self, iq: bool) -> bool {
        iq
    }
    fn modulation_families(&self) -> Vec<String> {
        vec![self.family.clone()]
    }
    fn features(&self) -> Vec<String> {
        self.features.clone()
    }
    fn resume_identity(&self) -> Option<String> {
        self.identity.clone()
    }
    fn demodulate(&self, _: Signal<'_>, _: u32, _: &Waveform) -> Result<dsp::Frontend, String> {
        self.calls.fetch_add(1, AtomicOrdering::SeqCst);
        std::thread::sleep(self.pause);
        if self
            .failures
            .fetch_update(AtomicOrdering::SeqCst, AtomicOrdering::SeqCst, |n| {
                n.checked_sub(1)
            })
            .is_ok()
        {
            return Err("controlled transient plugin failure".into());
        }
        Ok(dsp::Frontend {
            samples: (0..4096)
                .map(|n| if (n / 3) % 2 == 0 { 1.0 } else { -1.0 })
                .collect(),
            samples_per_symbol: 2.5,
        })
    }
}
struct ProbeProtocol {
    required: Vec<String>,
    kind: String,
}
impl ProtocolDecoder for ProbeProtocol {
    fn accepted_symbol_kind(&self) -> &str {
        &self.kind
    }
    fn required_demodulator_features(&self) -> Vec<String> {
        self.required.clone()
    }
    fn resume_identity(&self) -> Option<String> {
        Some("probe-full-frame-v1".into())
    }
    fn validate(&self) -> Result<(), String> {
        Ok(())
    }
    fn decode(&self, _: &[f64], _: f64) -> Result<Vec<protocol::ProtocolFrame>, String> {
        let oracle: Value = serde_json::from_str(include_str!("protocol_oracle.json")).unwrap();
        Ok(vec![protocol::ProtocolFrame {
            frame: hex::decode(
                oracle["cases"][0]["expected"][0]["frames_hex"][0]
                    .as_str()
                    .unwrap(),
            )
            .unwrap(),
            validation_layers: vec!["explicit-test-plugin-validator".into()],
        }])
    }
}
fn fixture(root: &Path) -> (PathBuf, Plan) {
    let path = root.join("input.ci16");
    fs::write(&path, vec![0; 4 * 28_800]).unwrap();
    let plan:Plan=serde_json::from_value(json!({"format":"ci16_le","sample_rate_hz":48000,
        "window_seconds":0.2,"hop_seconds":0.2,"protocols":{},"hypotheses":[{
            "protocol_id":"custom-protocol","waveform":{"hypothesis_id":"custom-waveform","demodulator_id":"custom-demod",
                "dsp":{"mode":"fsk","baud":9600,"rate_errors_ppm":[0],"phase_bins":4,"top_timing":1,"bank":"global"},
                "decimation":2}}]})).unwrap();
    (path, plan)
}
fn registry(
    calls: Arc<AtomicUsize>,
    failures: Arc<AtomicUsize>,
    identity: Option<&str>,
    pause: Duration,
) -> GenericReceiver {
    let mut receiver = GenericReceiver::default();
    receiver
        .register_demodulator(
            "custom-demod",
            Arc::new(ProbeDemod {
                calls,
                failures,
                identity: identity.map(str::to_owned),
                family: "fsk".into(),
                features: vec!["fixture-sync".into()],
                pause,
            }),
        )
        .unwrap();
    receiver
        .register_protocol(
            "custom-protocol",
            Arc::new(ProbeProtocol {
                required: vec!["fixture-sync".into()],
                kind: "binary_soft".into(),
            }),
        )
        .unwrap();
    receiver
}
fn default_registry() -> (GenericReceiver, Arc<AtomicUsize>) {
    let calls = Arc::new(AtomicUsize::new(0));
    let receiver = registry(
        calls.clone(),
        Arc::new(AtomicUsize::new(0)),
        Some("probe-config-v1"),
        Duration::ZERO,
    );
    (receiver, calls)
}
fn read(path: &Path) -> Value {
    input::read_json(path).unwrap()
}
fn overwrite(path: &Path, value: &Value) {
    fs::write(path, serde_json::to_vec_pretty(value).unwrap()).unwrap();
}

#[test]
fn custom_registered_plugins_execute_and_completed_resume_reuses_exact_commits() {
    let dir = tempfile::tempdir().unwrap();
    let (input, plan) = fixture(dir.path());
    let output = dir.path().join("output");
    let (receiver, calls) = default_registry();
    receiver.validate(&plan).unwrap();
    assert_eq!(
        receiver.capabilities()["demodulators"]["custom-demod"]["features"],
        json!(["fixture-sync"])
    );
    let first = receiver.decode_file(&input, &output, &plan, 1).unwrap();
    assert_eq!(calls.load(AtomicOrdering::SeqCst), 3);
    assert_eq!(first["unique_frame_count"], 1);
    assert_eq!(
        first["frames"][0]["provenance"].as_array().unwrap().len(),
        3
    );
    let result_bytes = fs::read(output.join("result.json")).unwrap();
    let journal = fs::read(output.join("windows.jsonl")).unwrap();
    let second = receiver
        .decode_file_resumable(&input, &output, &plan, 1, true)
        .unwrap();
    assert_eq!(
        calls.load(AtomicOrdering::SeqCst),
        3,
        "completed windows must not be rerun"
    );
    assert_eq!(first, second);
    assert_eq!(fs::read(output.join("result.json")).unwrap(), result_bytes);
    assert_eq!(fs::read(output.join("windows.jsonl")).unwrap(), journal);
    assert!(receiver.decode_file(&input, &output, &plan, 1).is_err());
}

#[test]
fn failed_windows_are_preserved_and_retried_not_cached_as_empty_success() {
    let dir = tempfile::tempdir().unwrap();
    let (input, plan) = fixture(dir.path());
    let output = dir.path().join("output");
    let calls = Arc::new(AtomicUsize::new(0));
    let receiver = registry(
        calls.clone(),
        Arc::new(AtomicUsize::new(1)),
        Some("probe-config-v1"),
        Duration::ZERO,
    );
    let first = receiver.decode_file(&input, &output, &plan, 1).unwrap();
    assert_eq!(first["status"], "failed");
    assert_eq!(first["failed_window_count"], 1);
    assert!(!output.join("windows/00000000.json").exists());
    assert!(output.join("windows/00000001.json").exists());
    assert!(!output.join("completion.json").exists());
    let second = receiver
        .decode_file_resumable(&input, &output, &plan, 1, true)
        .unwrap();
    assert_eq!(second["status"], "complete");
    assert_eq!(calls.load(AtomicOrdering::SeqCst), 4);
    assert_eq!(
        second["frames"][0]["provenance"].as_array().unwrap().len(),
        3
    );
    assert!(
        fs::read_dir(output.join("attempts"))
            .unwrap()
            .filter_map(Result::ok)
            .any(|entry| entry.path().join("prior-result.json").exists())
    );
}

#[test]
fn custom_fingerprint_and_all_plan_input_executable_contract_changes_fail_closed() {
    let dir = tempfile::tempdir().unwrap();
    let (input, plan) = fixture(dir.path());
    let output = dir.path().join("output");
    let (receiver, calls) = default_registry();
    receiver.decode_file(&input, &output, &plan, 1).unwrap();
    let result_bytes = fs::read(output.join("result.json")).unwrap();
    let different = registry(
        calls.clone(),
        Arc::new(AtomicUsize::new(0)),
        Some("different-config"),
        Duration::ZERO,
    );
    assert!(
        different
            .decode_file_resumable(&input, &output, &plan, 1, true)
            .unwrap_err()
            .contains("contract mismatch")
    );
    let mut changed = plan.clone();
    changed.hypotheses[0].waveform.dsp.baud = 9000.0;
    assert!(
        receiver
            .decode_file_resumable(&input, &output, &changed, 1, true)
            .is_err()
    );
    assert!(
        receiver
            .decode_file_resumable(&input, &output, &plan, 2, true)
            .is_err()
    );
    let contract_path = output.join("run-contract.json");
    let original = read(&contract_path);
    let mut changed = original.clone();
    changed["executable"]["sha256"] = json!("0".repeat(64));
    overwrite(&contract_path, &changed);
    assert!(
        receiver
            .decode_file_resumable(&input, &output, &plan, 1, true)
            .is_err()
    );
    overwrite(&contract_path, &original);
    fs::write(&input, vec![1; 4 * 28_800]).unwrap();
    assert!(
        receiver
            .decode_file_resumable(&input, &output, &plan, 1, true)
            .is_err()
    );
    assert_eq!(fs::read(output.join("result.json")).unwrap(), result_bytes);
    assert_eq!(calls.load(AtomicOrdering::SeqCst), 3);
    let other = tempfile::tempdir().unwrap();
    let (input, plan) = fixture(other.path());
    let unknown = registry(
        Arc::new(AtomicUsize::new(0)),
        Arc::new(AtomicUsize::new(0)),
        None,
        Duration::ZERO,
    );
    let output = other.path().join("unfingerprinted");
    unknown.decode_file(&input, &output, &plan, 1).unwrap();
    assert!(
        unknown
            .decode_file_resumable(&input, &output, &plan, 1, true)
            .unwrap_err()
            .contains("configuration identities")
    );
}

#[test]
fn corrupted_checkpoints_final_artifacts_and_geometry_never_count_as_cached_work() {
    let dir = tempfile::tempdir().unwrap();
    let (input, plan) = fixture(dir.path());
    let output = dir.path().join("output");
    let (receiver, calls) = default_registry();
    receiver.decode_file(&input, &output, &plan, 1).unwrap();
    let window = output.join("windows/00000000.json");
    let original = read(&window);
    let mut changed = original.clone();
    changed["window"]["row"]["samples"] = json!(1);
    overwrite(&window, &changed);
    assert!(
        receiver
            .decode_file_resumable(&input, &output, &plan, 1, true)
            .is_err()
    );
    changed.as_object_mut().unwrap().remove("content_sha256");
    changed["content_sha256"] = json!(fingerprint(&changed).unwrap());
    overwrite(&window, &changed);
    assert!(
        receiver
            .decode_file_resumable(&input, &output, &plan, 1, true)
            .unwrap_err()
            .contains("geometry")
    );
    overwrite(&window, &original);
    let result = output.join("result.json");
    let original = fs::read(&result).unwrap();
    fs::write(&result, b"{}").unwrap();
    assert!(
        receiver
            .decode_file_resumable(&input, &output, &plan, 1, true)
            .unwrap_err()
            .contains("artifact identity")
    );
    fs::write(&result, original).unwrap();
    fs::rename(&window, output.join("preserved-window.json")).unwrap();
    assert!(
        receiver
            .decode_file_resumable(&input, &output, &plan, 1, true)
            .unwrap_err()
            .contains("complete hash-verified")
    );
    assert_eq!(calls.load(AtomicOrdering::SeqCst), 3);
}

#[test]
fn capability_conflicts_and_protocol_id_shadowing_are_rejected() {
    let dir = tempfile::tempdir().unwrap();
    let (_, plan) = fixture(dir.path());
    let (receiver, _) = default_registry();
    let mut wrong = plan.clone();
    wrong.hypotheses[0].waveform.dsp.mode = "bpsk".into();
    assert!(
        receiver
            .validate(&wrong)
            .unwrap_err()
            .contains("modulation family")
    );
    let mut receiver = receiver.clone();
    receiver.protocols.insert(
        "custom-protocol".into(),
        Arc::new(ProbeProtocol {
            required: vec!["unavailable-feature".into()],
            kind: "binary_soft".into(),
        }),
    );
    assert!(
        receiver
            .validate(&plan)
            .unwrap_err()
            .contains("required protocol features")
    );
    receiver.protocols.insert(
        "custom-protocol".into(),
        Arc::new(ProbeProtocol {
            required: vec![],
            kind: "quaternary_soft".into(),
        }),
    );
    assert!(
        receiver
            .validate(&plan)
            .unwrap_err()
            .contains("incompatible")
    );
    let mut shadow = plan.clone();
    shadow.protocols.insert(
        "custom-protocol".into(),
        ProtocolConfig::Ax25 {
            g3ruh_modes: vec![false],
        },
    );
    assert!(
        receiver
            .validate(&shadow)
            .unwrap_err()
            .contains("duplicate")
    );
}

#[cfg(unix)]
#[test]
fn live_lock_and_symlink_artifacts_are_refused_without_signalling_owners() {
    use std::os::unix::fs::symlink;
    let dir = tempfile::tempdir().unwrap();
    let (input, plan) = fixture(dir.path());
    let output = dir.path().join("output");
    let lock = RunStore::open(&output, false).unwrap();
    assert!(
        RunStore::open(&output, true)
            .err()
            .unwrap()
            .contains("live lock owner")
    );
    drop(lock);
    let actual = dir.path().join("actual");
    let (receiver, _) = default_registry();
    receiver.decode_file(&input, &actual, &plan, 1).unwrap();
    let alias = dir.path().join("alias");
    symlink(&actual, &alias).unwrap();
    assert!(
        receiver
            .decode_file_resumable(&input, &alias, &plan, 1, true)
            .is_err()
    );
    fs::rename(
        actual.join("windows/00000000.json"),
        actual.join("saved.json"),
    )
    .unwrap();
    symlink(
        actual.join("saved.json"),
        actual.join("windows/00000000.json"),
    )
    .unwrap();
    assert!(
        receiver
            .decode_file_resumable(&input, &actual, &plan, 1, true)
            .is_err()
    );
}

#[test]
fn file_mutation_and_replacement_invalidate_open_source_guards() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("source");
    fs::write(&path, [1; 64]).unwrap();
    // Some filesystems coalesce metadata timestamps for writes in one clock
    // tick. Age the baseline deterministically: this test exercises the
    // metadata guard, while the decoder separately hashes the whole source.
    File::options()
        .write(true)
        .open(&path)
        .unwrap()
        .set_times(
            std::fs::FileTimes::new()
                .set_modified(std::time::SystemTime::UNIX_EPOCH + Duration::from_secs(1)),
        )
        .unwrap();
    let guard = SourceGuard::open(&path).unwrap();
    guard.unchanged().unwrap();
    fs::write(&path, [2; 64]).unwrap();
    assert!(guard.unchanged().is_err());
    let guard = SourceGuard::open(&path).unwrap();
    fs::rename(&path, dir.path().join("old-source")).unwrap();
    fs::write(&path, [2; 64]).unwrap();
    assert!(guard.unchanged().is_err());
}

#[test]
fn observed_source_change_permanently_invalidates_commits_even_after_restoration() {
    let dir = tempfile::tempdir().unwrap();
    let (input, plan) = fixture(dir.path());
    let output = dir.path().join("output");
    let (receiver, _) = default_registry();
    receiver.decode_file(&input, &output, &plan, 1).unwrap();
    let committed = fs::read(output.join("windows/00000000.json")).unwrap();
    let original = fs::read(&input).unwrap();
    let expected = input::identity(&input).unwrap().sha256;
    let guard = SourceGuard::open(&input).unwrap();
    let executable = SourceGuard::open(&std::env::current_exe().unwrap()).unwrap();
    let store = RunStore::open(&output, true).unwrap();
    let mut changed = original.clone();
    changed[0] ^= 1;
    fs::write(&input, &changed).unwrap();
    assert!(check_source_final(&store, &guard, &executable, &expected).is_err());
    let marker = fs::read(output.join("source-invalidated.json")).unwrap();
    assert!(!marker.is_empty());
    drop(store);
    fs::write(&input, &original).unwrap();
    let error = receiver
        .decode_file_resumable(&input, &output, &plan, 1, true)
        .unwrap_err();
    assert!(error.contains("invalidated checkpoints"), "{error}");
    assert_eq!(
        committed,
        fs::read(output.join("windows/00000000.json")).unwrap()
    );
    assert_eq!(
        marker,
        fs::read(output.join("source-invalidated.json")).unwrap()
    );
}

#[test]
#[ignore = "isolated subprocess helper invoked by native lifecycle test"]
fn generic_resume_subprocess_helper() {
    let root = PathBuf::from(std::env::var("RUST_GENERIC_HELPER_ROOT").unwrap());
    let plan: Plan = serde_json::from_value(read(&root.join("plan.json"))).unwrap();
    let receiver = registry(
        Arc::new(AtomicUsize::new(0)),
        Arc::new(AtomicUsize::new(0)),
        Some("probe-slow-v1"),
        Duration::from_millis(250),
    );
    let result = receiver
        .decode_file_resumable(
            &root.join("input.ci16"),
            &root.join("output"),
            &plan,
            1,
            std::env::var("RUST_GENERIC_HELPER_RESUME").unwrap() == "1",
        )
        .unwrap();
    assert_eq!(result["status"], "complete");
}

#[test]
fn abrupt_owned_process_interruption_preserves_commits_and_restarts_remaining_windows() {
    use std::process::{Command, Stdio};
    let dir = tempfile::tempdir().unwrap();
    let (_, plan) = fixture(dir.path());
    overwrite(&dir.path().join("plan.json"), &json!(plan));
    let original = std::env::current_exe().unwrap();
    assert!(
        fs::metadata(&original).unwrap().len() < 256 * 1024 * 1024,
        "bounded test snapshot"
    );
    let executable = dir.path().join("pinned-generic-test");
    fs::copy(original, &executable).unwrap();
    let run = |resume: &str| {
        Command::new(&executable)
            .args([
                "--ignored",
                "--exact",
                "generic::resume_tests::generic_resume_subprocess_helper",
                "--nocapture",
            ])
            .env("RUST_GENERIC_HELPER_ROOT", dir.path())
            .env("RUST_GENERIC_HELPER_RESUME", resume)
            .env("PATH", "/nonexistent-native-only")
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .spawn()
            .unwrap()
    };
    let mut child = run("0");
    let started = Instant::now();
    while !dir.path().join("output/windows/00000000.json").exists() {
        assert!(
            child.try_wait().unwrap().is_none(),
            "helper exited before first checkpoint"
        );
        assert!(
            started.elapsed() < Duration::from_secs(15),
            "bounded first checkpoint wait"
        );
        std::thread::sleep(Duration::from_millis(5));
    }
    child.kill().unwrap();
    child.wait().unwrap();
    let first = fs::read(dir.path().join("output/windows/00000000.json")).unwrap();
    let mut child = run("1");
    let started = Instant::now();
    loop {
        if let Some(status) = child.try_wait().unwrap() {
            assert!(status.success(), "resumed helper failed");
            break;
        }
        if started.elapsed() > Duration::from_secs(15) {
            let _ = child.kill();
            let _ = child.wait();
            panic!("bounded resume helper wait");
        }
        std::thread::sleep(Duration::from_millis(5));
    }
    assert_eq!(
        fs::read(dir.path().join("output/windows/00000000.json")).unwrap(),
        first
    );
    let result = read(&dir.path().join("output/result.json"));
    assert_eq!(result["status"], "complete");
    assert_eq!(result["window_count"], 3);
    assert_eq!(
        result["frames"][0]["provenance"].as_array().unwrap().len(),
        3
    );
}
