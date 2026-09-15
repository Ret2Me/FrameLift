//! Black-box receiver CLI contracts. Every tested command runs with an empty
//! PATH and without Python, FFmpeg, external decoders or network access.
use serde_json::{Value, json};
use sha2::{Digest, Sha512};
use std::fs::{self, File};
use std::io::Write;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::Mutex;
use std::time::{Duration, Instant};

const BINARY: &str = env!("CARGO_BIN_EXE_telemetry-yield-rs");

#[test]
fn array_results_keep_the_success_exit_contract() {
    let output = cli(
        &["decode-ax25-symbols"],
        Some(&json!({
            "soft": [1.0, -1.0, 1.0, -1.0], "threshold": 0.0, "g3ruh_modes": [false]
        })),
    );
    assert_eq!(output.json(), json!([]));
    assert!(output.stderr.is_empty());
}
// Keep at most one child active. Individual parallel receiver checks use two
// workers, so even a multithreaded test harness never exceeds two DSP workers.
static CLI_LOCK: Mutex<()> = Mutex::new(());

struct CliOutput {
    success: bool,
    code: Option<i32>,
    stdout: Vec<u8>,
    stderr: Vec<u8>,
}
impl CliOutput {
    fn json(&self) -> Value {
        assert!(
            self.success,
            "code={:?}; stderr={}; stdout={}",
            self.code,
            String::from_utf8_lossy(&self.stderr),
            String::from_utf8_lossy(&self.stdout)
        );
        serde_json::from_slice(&self.stdout).expect("successful CLI result must be JSON")
    }
    fn failure(&self) {
        assert!(
            !self.success,
            "unexpected success: {}",
            String::from_utf8_lossy(&self.stdout)
        );
        assert!(
            matches!(self.code, Some(1) | Some(2)),
            "failure must be controlled, not a panic/signal: code={:?}; stderr={}",
            self.code,
            String::from_utf8_lossy(&self.stderr)
        );
        if let Ok(value) = serde_json::from_slice::<Value>(&self.stdout) {
            assert!(
                value["status"] == "failed"
                    || value["pass"] == false
                    || value["failed"].as_u64().is_some_and(|n| n > 0),
                "failed command printed success-shaped JSON: {value}"
            );
        } else {
            assert!(
                !self.stderr.is_empty(),
                "failure needs an explicit diagnostic"
            );
        }
    }
}

fn cli(args: &[&str], stdin: Option<&Value>) -> CliOutput {
    let _guard = CLI_LOCK.lock().unwrap();
    let scratch = tempfile::tempdir().unwrap();
    let no_programs = scratch.path().join("empty-path");
    fs::create_dir(&no_programs).unwrap();
    let stdout_path = scratch.path().join("stdout");
    let stderr_path = scratch.path().join("stderr");
    let mut command = Command::new(BINARY);
    command
        .args(args)
        .env_clear()
        .env("PATH", &no_programs)
        .env("RAYON_NUM_THREADS", "2")
        .env("OMP_NUM_THREADS", "1")
        .stdin(if stdin.is_some() {
            Stdio::piped()
        } else {
            Stdio::null()
        })
        .stdout(File::create(&stdout_path).unwrap())
        .stderr(File::create(&stderr_path).unwrap());
    let mut child = command
        .spawn()
        .expect("Cargo-provided Rust binary should launch without PATH lookup");
    if let Some(value) = stdin {
        let mut pipe = child.stdin.take().unwrap();
        pipe.write_all(&serde_json::to_vec(value).unwrap()).unwrap();
    }
    let started = Instant::now();
    let status = loop {
        if let Some(status) = child.try_wait().unwrap() {
            break status;
        }
        let output_bytes =
            fs::metadata(&stdout_path).unwrap().len() + fs::metadata(&stderr_path).unwrap().len();
        if started.elapsed() > Duration::from_secs(30) || output_bytes > 2 * 1024 * 1024 {
            let _ = child.kill();
            let _ = child.wait();
            panic!("native CLI exceeded bounded test time/output: {args:?}");
        }
        std::thread::sleep(Duration::from_millis(5));
    };
    CliOutput {
        success: status.success(),
        code: status.code(),
        stdout: fs::read(stdout_path).unwrap(),
        stderr: fs::read(stderr_path).unwrap(),
    }
}
fn string(path: &Path) -> &str {
    path.to_str().unwrap()
}

#[test]
fn progressive_deadline_checkpoint_and_resume_contract() {
    let tmp = tempfile::tempdir().unwrap();
    let source = tmp.path().join("silence.wav");
    wav(&source, 1, &vec![0.0; 48000 * 7]);
    let session = tmp.path().join("session");
    let partial = cli(
        &[
            "decode-progressive",
            "--input",
            string(&source),
            "--output",
            string(&session),
            "--mode",
            "quick",
            "--budget-ms",
            "50",
            "--cache-mib",
            "0",
            "--threads",
            "2",
        ],
        None,
    )
    .json();
    assert!(
        partial["invocation_wall_seconds"].as_f64().unwrap() < 1.0,
        "deadline supervisor failed to return promptly"
    );
    assert_eq!(partial["budget_exhausted"], true);
    assert_eq!(partial["input_and_checkpoints_verified"], false);
    assert!(partial["union_count"].is_null());
    for entry in fs::read_dir(session.join("runs")).unwrap() {
        let run = entry.unwrap().path();
        assert!(
            fs::read_dir(run)
                .unwrap()
                .all(|child| !child.unwrap().file_type().unwrap().is_dir()),
            "interrupted worker leaked preparation scratch directory"
        );
    }
    let complete = cli(
        &[
            "decode-progressive",
            "--input",
            string(&source),
            "--output",
            string(&session),
            "--mode",
            "full",
            "--cache-mib",
            "512",
            "--threads",
            "2",
            "--resume",
        ],
        None,
    )
    .json();
    assert_eq!(complete["complete"], true);
    let first = read_json(&session.join("result.json"));
    assert_eq!(first["union_count"], 0);
    assert_eq!(first["completed_tasks"], first["total_tasks"]);
    let task = session.join("tasks/quick-000000.json");
    let task_before = fs::read(&task).unwrap();
    let modified = fs::metadata(&task).unwrap().modified().unwrap();
    // Simulate interruption after committed tasks but before snapshot publication.
    fs::remove_file(session.join("result.json")).unwrap();
    cli(
        &[
            "decode-progressive",
            "--input",
            string(&source),
            "--output",
            string(&session),
            "--mode",
            "full",
            "--threads",
            "1",
            "--resume",
        ],
        None,
    )
    .json();
    assert_eq!(read_json(&session.join("result.json")), first);
    assert_eq!(fs::read(&task).unwrap(), task_before);
    assert_eq!(fs::metadata(&task).unwrap().modified().unwrap(), modified);
    // A subsequent deadline during validation must not serve the old snapshot.
    let unverified = cli(
        &[
            "decode-progressive",
            "--input",
            string(&source),
            "--output",
            string(&session),
            "--mode",
            "quick",
            "--budget-ms",
            "50",
            "--resume",
        ],
        None,
    )
    .json();
    if unverified["input_and_checkpoints_verified"] == false {
        assert!(unverified["result"].is_null());
        assert!(unverified["union_count"].is_null());
        assert_eq!(unverified["complete"], false);
    }
    // Search-policy changes must not inherit old successes or old skipped tasks.
    cli(
        &[
            "decode-progressive",
            "--input",
            string(&source),
            "--output",
            string(&session),
            "--mode",
            "full",
            "--no-blind",
            "--resume",
        ],
        None,
    )
    .failure();
    // Accidental alteration of a completed task is detected independently of CRC.
    let mut changed = read_json(&task);
    changed["task"]["elapsed_seconds"] = json!(999.0);
    write_json(&task, &changed);
    cli(
        &[
            "decode-progressive",
            "--input",
            string(&source),
            "--output",
            string(&session),
            "--mode",
            "full",
            "--resume",
        ],
        None,
    )
    .failure();
    // A valid later-phase commit cannot hide a missing prerequisite, even if
    // its own checksum and the old complete snapshot are still intact.
    fs::write(&task, &task_before).unwrap();
    fs::remove_file(session.join("tasks/quick-000001.json")).unwrap();
    let broken = cli(
        &[
            "decode-progressive",
            "--input",
            string(&source),
            "--output",
            string(&session),
            "--mode",
            "full",
            "--resume",
        ],
        None,
    );
    broken.failure();
    assert!(String::from_utf8_lossy(&broken.stderr).contains("dependency stages"));
}

#[test]
fn progressive_full_preserves_positive_baseline() {
    let tmp = tempfile::tempdir().unwrap();
    let source = tmp.path().join("positive.wav");
    let (samples, expected) = positive_samples();
    wav(&source, 1, &samples);
    let session = tmp.path().join("session");
    let result = cli(
        &[
            "decode-progressive",
            "--input",
            string(&source),
            "--output",
            string(&session),
            "--mode",
            "full",
            "--threads",
            "2",
            "--no-blind",
            "--no-multi-anchor",
        ],
        None,
    )
    .json();
    assert_eq!(result["complete"], true);
    let report = read_json(&session.join("result.json"));
    // Cache is a resource choice, not a search policy: compare the full
    // deterministic task bank, not merely the final number of packets.
    let uncached = tmp.path().join("uncached");
    cli(
        &[
            "decode-progressive",
            "--input",
            string(&source),
            "--output",
            string(&uncached),
            "--mode",
            "full",
            "--threads",
            "1",
            "--cache-mib",
            "0",
            "--compute-threads",
            "4",
            "--no-blind",
            "--no-multi-anchor",
        ],
        None,
    )
    .json();
    assert_eq!(read_json(&uncached.join("result.json")), report);
    fn without_task_times(mut value: Value) -> Value {
        match &mut value {
            Value::Object(map) => {
                map.remove("elapsed_seconds");
                for child in map.values_mut() {
                    *child = without_task_times(child.take());
                }
            }
            Value::Array(items) => {
                for child in items {
                    *child = without_task_times(child.take());
                }
            }
            _ => {}
        }
        value
    }
    let task_count = fs::read_dir(session.join("tasks")).unwrap().count();
    assert_eq!(
        fs::read_dir(uncached.join("tasks")).unwrap().count(),
        task_count
    );
    assert!(task_count > 0);
    let pair_audit = tmp.path().join("compute-pair.json");
    let parity = cli(
        &[
            "audit-compute-pair",
            "--cpu-session",
            string(&session),
            "--candidate-session",
            string(&uncached),
            "--output",
            string(&pair_audit),
        ],
        None,
    )
    .json();
    assert_eq!(parity["pass"], true);
    assert_eq!(parity["publication_ready"], false);
    for task in fs::read_dir(session.join("tasks")).unwrap() {
        let task = task.unwrap();
        let left = read_json(&task.path());
        let right = read_json(&uncached.join("tasks").join(task.file_name()));
        assert_eq!(
            without_task_times(left["task"].clone()),
            without_task_times(right["task"].clone())
        );
    }
    for frame in expected {
        assert!(
            report["frame_with_fcs_hex"]
                .as_array()
                .unwrap()
                .contains(&json!(frame))
        );
    }
    let mut changed = samples;
    changed[0] = 0.123;
    wav(&source, 1, &changed);
    cli(
        &[
            "decode-progressive",
            "--input",
            string(&source),
            "--output",
            string(&session),
            "--mode",
            "full",
            "--no-blind",
            "--no-multi-anchor",
            "--resume",
        ],
        None,
    )
    .failure();
}

#[test]
fn compute_options_are_explicit_and_invalid_requests_fail_before_writes() {
    let info = cli(
        &["compute-info", "--compute", "cpu", "--compute-threads", "4"],
        None,
    )
    .json();
    assert_eq!(info["identity"]["backend"], "cpu");
    assert_eq!(info["options"]["cpu_threads"], 4);
    assert_eq!(info["identity"]["fallback"], false);
    cli(&["compute-info", "--compute", "magic"], None).failure();
    cli(&["compute-info", "--compute-threads", "0"], None).failure();
    cli(&["compute-info", "--cuda-streams", "0"], None).failure();
    #[cfg(not(feature = "cuda"))]
    {
        let tmp = tempfile::tempdir().unwrap();
        let output = tmp.path().join("session");
        let failed = cli(
            &[
                "decode-progressive",
                "--compute",
                "cuda",
                "--input",
                "missing.wav",
                "--output",
                string(&output),
            ],
            None,
        );
        failed.failure();
        assert!(!output.exists());
        assert!(String::from_utf8_lossy(&failed.stderr).contains("without --features cuda"));
    }
}

#[cfg(target_os = "linux")]
#[test]
fn progressive_sigterm_publishes_cancellation_receipt_and_can_resume() {
    let _guard = CLI_LOCK.lock().unwrap();
    let tmp = tempfile::tempdir().unwrap();
    let source = tmp.path().join("positive.wav");
    let (samples, _) = positive_samples();
    wav(&source, 1, &samples.repeat(32));
    let session = tmp.path().join("session");
    let stdout = tmp.path().join("stdout.json");
    let mut child = Command::new(BINARY)
        .args([
            "decode-progressive",
            "--input",
            string(&source),
            "--output",
            string(&session),
            "--mode",
            "full",
            "--threads",
            "1",
        ])
        .stdin(Stdio::null())
        .stdout(File::create(&stdout).unwrap())
        .stderr(Stdio::null())
        .spawn()
        .unwrap();
    let started = Instant::now();
    while !session.join("manifest.json").exists() && started.elapsed() < Duration::from_secs(30) {
        assert!(
            child.try_wait().unwrap().is_none(),
            "worker exited before cancellation test"
        );
        std::thread::sleep(Duration::from_millis(10));
    }
    // This PID was just spawned and remains unreaped; never signal a saved PID.
    assert_eq!(unsafe { libc::kill(child.id() as i32, libc::SIGTERM) }, 0);
    let status = loop {
        if let Some(status) = child.try_wait().unwrap() {
            break status;
        }
        if started.elapsed() > Duration::from_secs(45) {
            child.kill().unwrap();
            let _ = child.wait();
            panic!("supervisor did not honor SIGTERM");
        }
        std::thread::sleep(Duration::from_millis(10));
    };
    assert!(status.success());
    let receipt = read_json(&stdout);
    assert_eq!(receipt["cancelled"], true);
    assert_eq!(receipt["stop_reason"], "signal");
    assert_eq!(receipt["budget_exhausted"], false);
    drop(_guard);
    let resumed = cli(
        &[
            "decode-progressive",
            "--input",
            string(&source),
            "--output",
            string(&session),
            "--mode",
            "quick",
            "--budget-ms",
            "500",
            "--resume",
        ],
        None,
    )
    .json();
    assert!(resumed["error"].is_null());
}
fn write_json(path: &Path, value: &Value) {
    fs::write(path, serde_json::to_vec(value).unwrap()).unwrap();
}
fn read_json(path: &Path) -> Value {
    serde_json::from_slice(&fs::read(path).unwrap()).unwrap()
}

fn positive_samples() -> (Vec<f32>, Vec<String>) {
    // Frozen independent framing fixture supplies waveform samples, never CLI
    // search hints, known payload bytes, expected times or receiver parameters.
    let oracle: Value = serde_json::from_str(include_str!("protocol_oracle.json")).unwrap();
    let case = &oracle["cases"][0];
    let packed = hex::decode(case["levels_hex"].as_str().unwrap()).unwrap();
    let count = case["symbol_count"].as_u64().unwrap() as usize;
    let mut samples = Vec::new();
    for _ in 0..3 {
        for n in 0..count {
            let value = if (packed[n / 8] >> (7 - n % 8)) & 1 == 1 {
                0.75
            } else {
                -0.75
            };
            samples.extend(std::iter::repeat_n(value, 5));
        }
    }
    let expected: Vec<String> =
        serde_json::from_value(case["expected"][0]["frames_hex"].clone()).unwrap();
    assert!(
        !expected.is_empty(),
        "positive fixture must have independently known frames"
    );
    (samples, expected)
}
fn wav(path: &Path, channels: u16, samples: &[f32]) {
    let mut writer = hound::WavWriter::create(
        path,
        hound::WavSpec {
            channels,
            sample_rate: 48000,
            bits_per_sample: 32,
            sample_format: hound::SampleFormat::Float,
        },
    )
    .unwrap();
    for value in samples {
        for _ in 0..channels {
            writer.write_sample(*value).unwrap();
        }
    }
    writer.finalize().unwrap();
}
fn decode_audio(input: &Path, output: &Path, threads: &str) -> CliOutput {
    cli(
        &[
            "decode-audio",
            "--input",
            string(input),
            "--output",
            string(output),
            "--window-seconds",
            "0.4",
            "--hop-seconds",
            "0.2",
            "--baud",
            "9600",
            "--threads",
            threads,
        ],
        None,
    )
}
fn frame_hexes(value: &Value, field: &str) -> Vec<String> {
    let mut frames: Vec<_> = value["frames"]
        .as_array()
        .unwrap()
        .iter()
        .map(|frame| frame[field].as_str().unwrap().to_owned())
        .collect();
    frames.sort();
    frames
}
fn generic_plan(format: &str, demodulator: &str) -> Value {
    json!({
        "format":format, "sample_rate_hz":48000, "window_seconds":0.4, "hop_seconds":0.2,
        "protocols":{"ax25":{"type":"ax25","g3ruh_modes":[false,true]}},
        "hypotheses":[{"protocol_id":"ax25","waveform":{
            "hypothesis_id":"cli-fixture", "demodulator_id":demodulator,
            "dsp":{"baud":9600.0,"mode":"fsk"}, "decimation":2,
            "cutoff_hz":null,"carrier_hz":null,"mark_hz":1200.0,"space_hz":2200.0
        }}]
    })
}
fn persisted_failure_if_present(output: &Path) {
    let result = output.join("result.json");
    if result.exists() {
        let value = read_json(&result);
        assert_eq!(value["status"], "failed");
        assert_ne!(value["status"], "complete");
    }
}

#[test]
fn help_and_capabilities_are_native_and_honest_about_qualification() {
    let help = cli(&["--help"], None);
    assert!(help.success);
    let help = String::from_utf8(help.stdout).unwrap();
    for command in [
        "decode-audio",
        "decode-metadata",
        "candidate-ledger",
        "soft-decode-symbols",
        "capabilities",
    ] {
        assert!(help.contains(command), "missing command {command}");
    }
    let caps = cli(&["capabilities"], None).json();
    assert_eq!(caps["runtime"], "Rust");
    assert_eq!(caps["python_runtime_required"], false);
    assert_eq!(caps["publication_ready"], false);
    assert_eq!(caps["deployment_ready"], false);
    assert!(
        caps["protocol_library"]
            .as_array()
            .unwrap()
            .iter()
            .any(|p| p.as_str().unwrap().contains("CCSDS"))
    );
}

#[test]
fn native_space_link_and_fec_cli_are_explicit_about_integrity() {
    assert!(!cli(&["fec-profile", "--profile", "ccsds-tc521"], None).success);
    let parsed = cli(
        &["parse-space-link"],
        Some(
            &json!({"config":{"type":"csp_v1","crc32":"required_header_and_payload"},
        "frame_hex":"a23a8501414243de918426"}),
        ),
    )
    .json();
    assert_eq!(parsed["integrity_verified"], true);
    assert_eq!(parsed["frame"]["payload"], json!([65, 66, 67]));
    let structural = cli(
        &["parse-space-link"],
        Some(&json!({"config":{"type":"csp_v1","crc32":"absent"},
        "frame_hex":"a23a8500414243"})),
    )
    .json();
    assert_eq!(structural["integrity_verified"], false);
    assert_eq!(structural["structural_only"], true);
    for (name, bits) in [("ccsds-tc128", 128), ("ccsds-tc512", 512)] {
        let p = cli(&["fec-profile", "--profile", name], None).json();
        assert_eq!(p["kind"], "ldpc");
        assert_eq!(p["config"]["codeword_bits"], bits);
    }
    let rs = cli(
        &[
            "fec-profile",
            "--profile",
            "ccsds-rs255-223",
            "--shortening",
            "191",
        ],
        None,
    )
    .json();
    assert_eq!(rs["config"]["shortening"], 191);
    assert!(
        !cli(
            &[
                "fec-profile",
                "--profile",
                "ccsds-tc512",
                "--shortening",
                "1"
            ],
            None
        )
        .success
    );
    let uncoded = cli(
        &["decode-codeword"],
        Some(&json!({"code":{"kind":"none"},"decoded_bytes":1,
        "soft":[1,-1,-1,-1,-1,-1,-1,1]})),
    )
    .json();
    assert_eq!(uncoded["decoded"]["bytes"], json!([129]));
    assert_eq!(uncoded["decoded"]["parity_verified"], false);
    assert_eq!(uncoded["telemetry_validated"], false);
}

#[test]
fn checked_in_qpsk_csp_rs_plan_decodes_and_resumes_without_external_runtime() {
    use telemetry_yield_rs::{coded, fec, space_link};
    let tmp = tempfile::tempdir().unwrap();
    let mut frame = vec![0xa2, 0x3a, 0x85, 0x01];
    frame.extend(0u8..24);
    frame.extend(space_link::csp_crc32c(&frame).to_be_bytes());
    let rs = fec::ReedSolomonConfig::ccsds_rs255_223(1, 191);
    let mut word = rs.encode(&frame).unwrap();
    word[9] ^= 0xa5;
    word[37] ^= 0x97;
    let mut soft: Vec<_> = word
        .iter()
        .flat_map(|b| {
            (0..8)
                .rev()
                .map(move |i| if b & (1 << i) != 0 { 1.0 } else { -1.0 })
        })
        .collect();
    coded::derandomize(&mut soft, &coded::Randomizer::CcsdsTm255);
    let mut burst: Vec<_> = [0x1au8, 0xcf, 0xfc, 0x1d]
        .iter()
        .flat_map(|b| {
            (0..8)
                .rev()
                .map(move |i| if b & (1 << i) != 0 { 1.0 } else { -1.0 })
        })
        .collect();
    burst.extend(soft);
    let mut bits: Vec<_> = (0..128)
        .map(|i| if i % 3 == 0 { 1.0 } else { -1.0 })
        .collect();
    for _ in 0..5 {
        bits.extend(&burst);
    }
    bits.extend((0..128).map(|i| if i % 2 == 0 { 1.0 } else { -1.0 }));
    let path = tmp.path().join("qpsk.cf32");
    let mut file = File::create(&path).unwrap();
    for n in 0..bits.len() / 2 * 8 {
        let (i, q) = (bits[(n / 8) * 2], bits[(n / 8) * 2 + 1]);
        let angle = 0.71 + std::f64::consts::TAU * 25.0 * n as f64 / 48000.0;
        for x in [
            i * angle.cos() - q * angle.sin(),
            i * angle.sin() + q * angle.cos(),
        ] {
            file.write_all(&(x as f32).to_le_bytes()).unwrap();
        }
    }
    drop(file);
    let plan = tmp.path().join("plan.json");
    fs::write(
        &plan,
        include_str!("../../configs/native/qpsk-csp-rs-example.json"),
    )
    .unwrap();
    let out = tmp.path().join("session");
    let args = [
        "decode",
        "--input",
        path.to_str().unwrap(),
        "--plan",
        plan.to_str().unwrap(),
        "--output",
        out.to_str().unwrap(),
        "--threads",
        "1",
    ];
    let result = cli(&args, None).json();
    assert_eq!(result["status"], "complete");
    assert_eq!(result["frames"].as_array().unwrap().len(), 1);
    assert_eq!(result["frames"][0]["frame_hex"], hex::encode(&frame));
    let mut resumed = args.to_vec();
    resumed.push("--resume");
    assert_eq!(cli(&resumed, None).json()["frames"], result["frames"]);
    // Rehashing a forged checkpoint cannot erase required FEC provenance.
    let checkpoint = out.join("windows/00000000.json");
    let mut saved: Value = serde_json::from_slice(&fs::read(&checkpoint).unwrap()).unwrap();
    saved["window"]["frames"][0]["validation_layers"]
        .as_array_mut()
        .unwrap()
        .retain(|v| v != "reed_solomon_syndrome_verified");
    saved.as_object_mut().unwrap().remove("content_sha256");
    let hash = hex::encode(sha2::Sha256::digest(serde_json::to_vec(&saved).unwrap()));
    saved["content_sha256"] = json!(hash);
    fs::write(&checkpoint, serde_json::to_vec(&saved).unwrap()).unwrap();
    let rejected = cli(&resumed, None);
    rejected.failure();
    assert!(String::from_utf8_lossy(&rejected.stderr).contains("FEC provenance"));
}

#[test]
fn positive_wav_decodes_without_python_and_parallelism_preserves_full_results() {
    let dir = tempfile::tempdir().unwrap();
    let input = dir.path().join("positive.wav");
    let (samples, mut expected) = positive_samples();
    wav(&input, 1, &samples);
    expected.sort();
    let one_path = dir.path().join("one");
    let two_path = dir.path().join("two");
    let one = decode_audio(&input, &one_path, "1").json();
    let two = decode_audio(&input, &two_path, "2").json();
    for result in [&one, &two] {
        assert_eq!(result["status"], "complete");
        assert_eq!(result["python_runtime_required"], false);
        assert_eq!(result["reference_bytes_used_for_search"], false);
        assert_eq!(result["failed_window_count"], 0);
        assert_eq!(frame_hexes(result, "frame_with_fcs_hex"), expected);
        assert!(
            result["frames"]
                .as_array()
                .unwrap()
                .iter()
                .all(|frame| frame["independent_bitwise_crc_passed"] == true)
        );
    }
    assert_eq!(
        one["frames"], two["frames"],
        "ordered timing provenance must also be identical"
    );
    assert_eq!(
        fs::read(one_path.join("windows.jsonl")).unwrap(),
        fs::read(two_path.join("windows.jsonl")).unwrap()
    );
    let plan = read_json(&one_path.join("plan.json"));
    assert_eq!(plan["network_submission"], false);
    assert!(plan["conversion_command"].is_null());
}

#[test]
fn existing_output_is_never_clobbered_even_for_invalid_input() {
    let dir = tempfile::tempdir().unwrap();
    let input = dir.path().join("positive.wav");
    let (samples, _) = positive_samples();
    wav(&input, 1, &samples);
    let output = dir.path().join("result");
    decode_audio(&input, &output, "1").json();
    let original_result = fs::read(output.join("result.json")).unwrap();
    let original_plan = fs::read(output.join("plan.json")).unwrap();
    fs::write(output.join("user-owned.txt"), b"preserve me").unwrap();
    decode_audio(&input, &output, "1").failure();
    decode_audio(&dir.path().join("missing.wav"), &output, "1").failure();
    assert_eq!(
        fs::read(output.join("result.json")).unwrap(),
        original_result
    );
    assert_eq!(fs::read(output.join("plan.json")).unwrap(), original_plan);
    assert_eq!(
        fs::read(output.join("user-owned.txt")).unwrap(),
        b"preserve me"
    );
}

#[test]
fn unsupported_formats_protocols_and_waveform_configurations_fail_not_zero_success() {
    let dir = tempfile::tempdir().unwrap();
    let input = dir.path().join("input.iq");
    fs::write(&input, vec![0; 4 * 19200]).unwrap();
    let base = generic_plan("ci16_le", "phase_fsk");
    let changes = [
        ("format", json!("ci16")),
        ("format", json!("unsupported")),
        ("sample_rate_hz", json!(0)),
        ("unknown_field", json!(true)),
        ("protocols", json!({"ax25":{"type":"unknown"}})),
        (
            "protocols",
            json!({"ax25":{"type":"ax25","g3ruh_modes":[]}}),
        ),
        (
            "protocols",
            json!({"ax25":{"type":"ccsds_tm","config":{
                "frame":{"frame_length_bytes":32,"fecf_present":false},
                "sync_marker":[1,0,1,0],"maximum_sync_hamming":0,"invert_modes":[false]
            }}}),
        ),
        (
            "protocols",
            json!({"ax25":{"type":"fixed_sync","config":{
                "protocol_id":"explicit-fixed","syncword":[1,0,1,0],"frame_bits":32,
                "lsb_first":false,"maximum_sync_hamming":0,"validation_name":"missing-integrity"
            }}}),
        ),
    ];
    for (index, (field, value)) in changes.into_iter().enumerate() {
        let mut plan = base.clone();
        plan[field] = value;
        let plan_path = dir.path().join(format!("bad-{index}.json"));
        write_json(&plan_path, &plan);
        let output = dir.path().join(format!("bad-{index}"));
        cli(
            &[
                "decode",
                "--input",
                string(&input),
                "--plan",
                string(&plan_path),
                "--output",
                string(&output),
            ],
            None,
        )
        .failure();
        persisted_failure_if_present(&output);
    }
    for (index, (field, value)) in [
        ("demodulator_id", json!("unknown-demodulator")),
        ("decimation", json!(0)),
        ("dsp", json!({"mode":"qpsk","baud":9600})),
        ("cutoff_hz", json!(48000)),
    ]
    .into_iter()
    .enumerate()
    {
        let mut plan = base.clone();
        plan["hypotheses"][0]["waveform"][field] = value;
        let plan_path = dir.path().join(format!("wave-{index}.json"));
        write_json(&plan_path, &plan);
        let output = dir.path().join(format!("wave-{index}"));
        cli(
            &[
                "decode",
                "--input",
                string(&input),
                "--plan",
                string(&plan_path),
                "--output",
                string(&output),
            ],
            None,
        )
        .failure();
        persisted_failure_if_present(&output);
    }
}

#[test]
fn malformed_audio_and_invalid_settings_are_persisted_as_failures() {
    let dir = tempfile::tempdir().unwrap();
    let (samples, _) = positive_samples();
    for (label, channels, data) in [
        ("stereo", 2, samples.clone()),
        ("short", 1, vec![0.0; 32]),
        ("nonfinite", 1, vec![f32::NAN; 8192]),
    ] {
        let input = dir.path().join(format!("{label}.wav"));
        wav(&input, channels, &data);
        let output = dir.path().join(label);
        decode_audio(&input, &output, "1").failure();
        persisted_failure_if_present(&output);
    }
    let input = dir.path().join("valid.wav");
    wav(&input, 1, &samples);
    for (index, args) in [
        vec!["--threads", "0"],
        vec!["--baud", "0"],
        vec!["--hop-seconds", "10"],
        vec!["--mode", "qpsk"],
    ]
    .into_iter()
    .enumerate()
    {
        let output = dir.path().join(format!("config-{index}"));
        let mut command = vec![
            "decode-audio",
            "--input",
            string(&input),
            "--output",
            string(&output),
        ];
        command.extend(args);
        cli(&command, None).failure();
        persisted_failure_if_present(&output);
    }
    let corrupt = dir.path().join("corrupt.wav");
    fs::write(&corrupt, b"not a WAV file").unwrap();
    let output = dir.path().join("corrupt-result");
    decode_audio(&corrupt, &output, "1").failure();
    persisted_failure_if_present(&output);
}

fn positive_sigmf(root: &Path) -> (PathBuf, Vec<String>) {
    let (samples, expected) = positive_samples();
    let mut raw = Vec::new();
    let mut phase = 0.0f64;
    for value in samples {
        phase += value as f64 * std::f64::consts::TAU * 4800.0 / 48000.0;
        for component in [phase.cos(), phase.sin()] {
            raw.extend(((component * 16000.0).round() as i16).to_be_bytes());
        }
    }
    fs::write(root.join("capture.sigmf-data"), &raw).unwrap();
    let metadata = root.join("capture.sigmf-meta");
    write_json(
        &metadata,
        &json!({
            "global":{"core:datatype":"ci16_be","core:version":"1.2.6","core:sample_rate":48000,
                "core:sha512":hex::encode(Sha512::digest(&raw)),"core:offset":1000},
            "captures":[{"core:sample_start":1000,"core:frequency":437500000}],"annotations":[]
        }),
    );
    (metadata, expected)
}

#[test]
fn metadata_cli_preserves_big_endian_sample_origin_and_rejects_contradictory_plans() {
    let dir = tempfile::tempdir().unwrap();
    let (metadata, mut expected) = positive_sigmf(dir.path());
    expected.sort();
    let inspected = cli(
        &[
            "inspect-metadata",
            "--kind",
            "sigmf",
            "--metadata",
            string(&metadata),
        ],
        None,
    )
    .json();
    assert_eq!(inspected["telemetry_validation"], false);
    assert_eq!(inspected["metadata"]["datatype"], "ci16_be");
    assert_eq!(inspected["metadata"]["byte_offset"], 0);
    assert_eq!(inspected["metadata"]["sample_index_offset"], 1000);
    assert_eq!(inspected["metadata"]["captures"][0]["sample_start"], 0);
    let plan = generic_plan("metadata", "phase_fsk");
    let plan_path = dir.path().join("plan.json");
    write_json(&plan_path, &plan);
    let output = dir.path().join("positive");
    let result = cli(
        &[
            "decode-metadata",
            "--kind",
            "sigmf",
            "--metadata",
            string(&metadata),
            "--plan",
            string(&plan_path),
            "--output",
            string(&output),
            "--threads",
            "2",
        ],
        None,
    )
    .json();
    assert_eq!(result["status"], "complete");
    assert_eq!(frame_hexes(&result, "frame_hex"), expected);
    let resumed = cli(
        &[
            "decode-metadata",
            "--kind",
            "sigmf",
            "--metadata",
            string(&metadata),
            "--plan",
            string(&plan_path),
            "--output",
            string(&output),
            "--threads",
            "2",
            "--resume",
        ],
        None,
    )
    .json();
    assert_eq!(
        resumed, result,
        "completed resume must return the immutable original result"
    );
    for (index, (field, value)) in [
        ("format", json!("ci16_le")),
        ("sample_rate_hz", json!(24000)),
    ]
    .into_iter()
    .enumerate()
    {
        let mut contradiction = plan.clone();
        contradiction[field] = value;
        write_json(&plan_path, &contradiction);
        let output = dir.path().join(format!("contradiction-{index}"));
        cli(
            &[
                "decode-metadata",
                "--kind",
                "sigmf",
                "--metadata",
                string(&metadata),
                "--plan",
                string(&plan_path),
                "--output",
                string(&output),
            ],
            None,
        )
        .failure();
        persisted_failure_if_present(&output);
    }
    let mut document = read_json(&metadata);
    document["global"]["core:datatype"] = json!("ci16");
    write_json(&metadata, &document);
    cli(
        &[
            "inspect-metadata",
            "--kind",
            "sigmf",
            "--metadata",
            string(&metadata),
        ],
        None,
    )
    .failure();
}

#[test]
fn candidate_ledger_requires_caller_validation_and_does_not_promote_unvalidated_roles() {
    let fixture: Value = serde_json::from_str(include_str!("ledger_oracle.json")).unwrap();
    let original = fixture["cases"][1]["input"][0].clone();
    let value = cli(
        &["candidate-ledger"],
        Some(&json!({"detections":[original.clone(),original.clone()]})),
    )
    .json();
    assert_eq!(value["union_count"], 1);
    assert_eq!(value["origin_count"], 1);
    for role in ["candidate", "baseline"] {
        let mut unvalidated = original.clone();
        unvalidated["validated"] = false.into();
        unvalidated["source_role"] = role.into();
        cli(
            &["candidate-ledger"],
            Some(&json!({"detections":[unvalidated]})),
        )
        .failure();
    }
    let mut no_layers = original.clone();
    no_layers["validation_layers"] = json!([]);
    cli(
        &["candidate-ledger"],
        Some(&json!({"detections":[no_layers]})),
    )
    .failure();
    let mut missing = original.clone();
    missing.as_object_mut().unwrap().remove("validated");
    cli(
        &["candidate-ledger"],
        Some(&json!({"detections":[missing]})),
    )
    .failure();
    let mut bad = original;
    bad["original_frame_hex"] = json!("not-hex");
    cli(&["candidate-ledger"], Some(&json!({"detections":[bad]}))).failure();
}

#[test]
fn transport_inspection_never_promotes_kiss_payload_to_verified_telemetry() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("unvalidated.kiss");
    fs::write(
        &path,
        [0xc0, 0x00, 0xff, 0xc0, 0xc0, 0x00, 0xdb, 0x01, 0xc0],
    )
    .unwrap();
    let value = cli(&["parse-kiss", "--input", string(&path)], None).json();
    assert_eq!(value["telemetry_validation"], false);
    assert_eq!(value["records"]["data_frames"][0]["payload"], json!([255]));
    assert_eq!(value["records"]["malformed_records"], 1);
}

#[test]
fn compatibility_cli_preserves_undersized_candidate_without_promoting_it() {
    let fixture: Value = serde_json::from_str(include_str!("compat_oracle.json")).unwrap();
    for case in fixture["cases"]
        .as_array()
        .unwrap()
        .iter()
        .filter(|c| c["name"] == "observation4704_left_padding4")
    {
        let stage = if case["stage"] == "decoded" {
            "decoded-bits"
        } else {
            "nrzi-g3ruh-levels"
        };
        let value = cli(
            &["compat-pdus", "--stage", stage],
            Some(&json!({"bits":case["input_bits"]})),
        )
        .json();
        assert_eq!(value["native_trusted"], false);
        assert_eq!(value["candidates"], case["expected"]);
        assert_eq!(
            value["candidates"][0]["classification"],
            "crc_valid_undersized_pdu"
        );
    }
    cli(
        &["compat-pdus", "--stage", "decoded-bits"],
        Some(&json!({"bits":[0,2]})),
    )
    .failure();
    cli(
        &[
            "compat-pdus",
            "--stage",
            "decoded-bits",
            "--max-length",
            "0",
        ],
        Some(&json!({"bits":[]})),
    )
    .failure();
}

fn malformed_crc_valid_soft_levels() -> Vec<f64> {
    // Independent bitwise X.25 encoder: this deliberately is not an AX.25 UI
    // address chain. The legacy list oracle accepts it on CRC alone.
    let mut bytes = vec![0u8; 20];
    let mut crc = 0xffffu16;
    for byte in &bytes {
        crc ^= *byte as u16;
        for _ in 0..8 {
            crc = if crc & 1 != 0 {
                (crc >> 1) ^ 0x8408
            } else {
                crc >> 1
            };
        }
    }
    bytes.extend((crc ^ 0xffff).to_le_bytes());
    let flag = [0, 1, 1, 1, 1, 1, 1, 0];
    let mut plain = flag.to_vec();
    let mut ones = 0;
    for byte in bytes {
        for index in 0..8 {
            let bit = (byte >> index) & 1;
            plain.push(bit);
            ones = if bit == 1 { ones + 1 } else { 0 };
            if ones == 5 {
                plain.push(0);
                ones = 0;
            }
        }
    }
    plain.extend(flag);
    let mut scrambled = Vec::new();
    let mut previous = 0;
    plain
        .into_iter()
        .enumerate()
        .map(|(index, bit)| {
            let encoded =
                bit ^ if index >= 12 {
                    scrambled[index - 12]
                } else {
                    0
                } ^ if index >= 17 {
                    scrambled[index - 17]
                } else {
                    0
                };
            scrambled.push(encoded);
            previous ^= u8::from(encoded == 0);
            if previous == 1 { 1.0 } else { -1.0 }
        })
        .collect()
}

#[test]
fn soft_cli_enforces_ui_and_rejects_ignored_options_and_unbounded_preparation() {
    let data = json!({"soft":malformed_crc_valid_soft_levels(), "threshold":0.0,
        "budget":{"maximum_flips":0,"maximum_attempts":32,"maximum_regions":1,
          "maximum_map_seed_states":1,"maximum_output_frames":8,"least_reliable_symbols":1},"options":{}});
    let value = cli(&["soft-decode-symbols", "--method", "list"], Some(&data)).json();
    assert_eq!(value["result"]["frames"], json!([]));
    assert_eq!(value["rejected_by_cli_integrity"], 1);
    assert_eq!(value["native_trusted"], false);
    for field in ["stop_after_first_frame", "stop_after_uncorrected_frame"] {
        let mut bad = data.clone();
        bad["options"][field] = true.into();
        cli(&["soft-decode-symbols", "--method", "list"], Some(&bad)).failure();
    }
    let mut bad = data.clone();
    bad["options"]["descramble"] = false.into();
    cli(&["soft-decode-symbols", "--method", "list"], Some(&bad)).failure();
    let mut bad = data.clone();
    bad["budget"]["maximum_flips"] = json!(u64::MAX);
    cli(&["soft-decode-symbols", "--method", "list"], Some(&bad)).failure();
    let mut bad = data;
    bad["budget"]["maximum_attempts_per_region"] = 1.into();
    cli(&["soft-decode-symbols", "--method", "list"], Some(&bad)).failure();
}

#[test]
fn ber_cli_rejects_nonbinary_and_excessive_work_without_hiding_missing_bits() {
    let valid = json!({"candidate":[0,1,0,1],"truth":[0,1,0,1],"policy":{},"qpsk":false});
    let result = cli(&["score-bits"], Some(&valid)).json();
    assert_eq!(result["validated_telemetry_frames_claimed"], false);
    for field in ["candidate", "truth"] {
        let mut bad = valid.clone();
        bad[field] = json!([0, 2, 1]);
        cli(&["score-bits"], Some(&bad)).failure();
    }
    let bad = json!({"candidate":vec![0;20_000],"truth":vec![0;20_000],
        "policy":{"max_shift_bits":u64::MAX},"qpsk":false});
    cli(&["score-bits"], Some(&bad)).failure();
}

#[test]
fn cw_cli_recovers_sos_as_pending_text_not_telemetry() {
    let dir = tempfile::tempdir().unwrap();
    let input = dir.path().join("sos.ci16");
    fs::write(&input, include_bytes!("cw_legacy_sos.ci16")).unwrap();
    let config = dir.path().join("config.json");
    write_json(
        &config,
        &json!({"sample_rate_hz":8000.0,"preview_fft_size":2048,
        "preview_slices_per_window":2,"carrier_half_bandwidth_hz":150.0}),
    );
    let output = dir.path().join("inspection.json");
    let args = [
        "inspect-cw",
        "--input",
        string(&input),
        "--config",
        string(&config),
        "--output",
        string(&output),
    ];
    let value = cli(&args, None).json();
    assert_eq!(value["native_trusted"], false);
    assert_eq!(value["telemetry_validation"], false);
    assert_eq!(value["result"]["classification"], "keyed");
    assert_eq!(value["result"]["candidates"][0]["text"], "SOS");
    assert_eq!(value["result"]["candidates"][0]["wpm"], 20);
    assert!(
        value["result"]["candidates"]
            .as_array()
            .unwrap()
            .iter()
            .all(|candidate| candidate["candidate_validation"] == "pending")
    );
    let before = fs::read(&output).unwrap();
    cli(&args, None).failure();
    assert_eq!(fs::read(&output).unwrap(), before);
    write_json(&config, &json!({"preview_fft_size":2048}));
    cli(&args, None).failure();
}

#[test]
fn legacy_afsk_cli_and_resumable_generic_route_preserve_the_positive_frame() {
    let dir = tempfile::tempdir().unwrap();
    let fixture = include_bytes!("afsk_legacy_fixtures/positive.cf32");
    let count = fixture.len() / 8;
    let input = dir.path().join("positive.cf32");
    let mut bytes = vec![0; 177 * 8];
    bytes.extend(fixture);
    fs::write(&input, bytes).unwrap();
    let oracle: Value = serde_json::from_str(include_str!("afsk_legacy_oracle.json")).unwrap();
    let case = &oracle["iq_cases"][0];
    let config = dir.path().join("afsk.json");
    write_json(&config, &case["config"]);
    let output = dir.path().join("legacy");
    let sample_count = count.to_string();
    let value = cli(
        &[
            "decode-afsk-legacy",
            "--input",
            string(&input),
            "--format",
            "cf32_le",
            "--config",
            string(&config),
            "--output",
            string(&output),
            "--start-sample",
            "177",
            "--sample-count",
            &sample_count,
        ],
        None,
    )
    .json();
    assert_eq!(value["result"], case["expected"]);
    assert_eq!(value["rejected_frames_are_validated_telemetry"], false);
    assert!(!value["result"]["frames"].as_array().unwrap().is_empty());

    let rate = case["config"]["sample_rate_hz"].as_u64().unwrap();
    let seconds = (count + 1) as f64 / rate as f64;
    let plan = json!({"format":"cf32_le","sample_rate_hz":rate,"window_seconds":seconds,
        "hop_seconds":seconds/2.0,"segment_start_sample":177,"segment_sample_count":count,
        "protocols":{"ax25":{"type":"ax25","g3ruh_modes":[false]}},
        "hypotheses":[{"protocol_id":"ax25","waveform":{
            "hypothesis_id":"legacy-afsk", "demodulator_id":"bell202_afsk_legacy",
            "dsp":{"baud":case["config"]["baudrate"],"mode":"afsk","bank":"global",
                "rate_errors_ppm":case["config"]["timing_rate_errors_ppm"],
                "phase_bins":case["config"]["timing_phase_bins"],"top_timing":case["config"]["timing_top_n"]},
            "decimation":rate/case["config"]["audio_sample_rate_hz"].as_u64().unwrap(),
            "cutoff_hz":null,"carrier_hz":null,"mark_hz":case["config"]["mark_hz"],
            "space_hz":case["config"]["space_hz"]}}]});
    let plan_path = dir.path().join("plan.json");
    write_json(&plan_path, &plan);
    let generic_output = dir.path().join("generic");
    let mut args = vec![
        "decode",
        "--input",
        string(&input),
        "--plan",
        string(&plan_path),
        "--output",
        string(&generic_output),
        "--threads",
        "2",
    ];
    let decoded = cli(&args, None).json();
    let mut expected: Vec<String> = case["expected"]["frames"]
        .as_array()
        .unwrap()
        .iter()
        .map(|frame| {
            hex::encode(serde_json::from_value::<Vec<u8>>(frame["frame_with_fcs"].clone()).unwrap())
        })
        .collect();
    expected.sort();
    assert_eq!(frame_hexes(&decoded, "frame_hex"), expected);
    args.push("--resume");
    assert_eq!(cli(&args, None).json(), decoded);
}
