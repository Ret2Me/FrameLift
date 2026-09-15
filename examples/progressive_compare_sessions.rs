//! Independent completed-session comparison; never invokes a decoder.
//! Digests mirror the exact v2 manifest/v1 task serialization contract.
use clap::Parser;
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::collections::{BTreeMap, BTreeSet};
use std::fs::{self, File};
use std::path::{Path, PathBuf};
use telemetry_yield_rs::{adaptive, input, progressive_audio::PreparedMetadata};

#[derive(Parser)]
struct Args {
    #[arg(long)]
    left: PathBuf,
    #[arg(long)]
    right: PathBuf,
    #[arg(long)]
    output: PathBuf,
}

// Declaration order is significant for the producer's serde_json digest.
#[derive(Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Manifest {
    schema: String,
    audio: PreparedMetadata,
    executable_sha256: String,
    policy: Value,
}
#[derive(Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct TaskRecord {
    schema: String,
    session_sha256: String,
    stage: String,
    window: usize,
    elapsed_seconds: f64,
    frame_with_fcs_hex: BTreeSet<String>,
    detail: Value,
}
#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct TaskCommit {
    sha256: String,
    task: TaskRecord,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Policy {
    baud: f64,
    blind: bool,
    multi_anchor: bool,
    window_seconds: f64,
    hop_seconds: f64,
    fast_timings: usize,
    fast_gardner: usize,
    protocol: String,
    version: usize,
    anchor_generations: String,
}

struct Session {
    path: PathBuf,
    manifest: Manifest,
    session_sha256: String,
    tasks: BTreeMap<String, TaskRecord>,
    frames: BTreeSet<String>,
    windows: usize,
    _lock: File,
}

fn hash(value: &impl Serialize) -> Result<String, String> {
    Ok(hex::encode(Sha256::digest(
        serde_json::to_vec(value).map_err(|e| e.to_string())?,
    )))
}
fn typed<T: for<'a> Deserialize<'a>>(path: &Path) -> Result<T, String> {
    serde_json::from_value(input::read_json(path)?).map_err(|e| format!("{}: {e}", path.display()))
}
fn require(condition: bool, reason: impl Into<String>) -> Result<(), String> {
    if condition {
        Ok(())
    } else {
        Err(reason.into())
    }
}
fn sha_shape(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}

/// Independent bitwise reflected X25 residue plus structural UI validation.
/// No production protocol/CRC helper is called.
fn valid_frame(value: &str) -> bool {
    let Ok(frame) = hex::decode(value) else {
        return false;
    };
    if frame.len() < 18 || frame.len() > 1024 || hex::encode(&frame) != value {
        return false;
    }
    let mut crc = 0xffffu16;
    for byte in &frame {
        crc ^= u16::from(*byte);
        for _ in 0..8 {
            crc = (crc >> 1) ^ if crc & 1 != 0 { 0x8408 } else { 0 };
        }
    }
    if crc != 0xf0b8 {
        return false;
    }
    let payload = &frame[..frame.len() - 2];
    let mut cursor = 0;
    for count in 1..=10 {
        let Some(address) = payload.get(cursor..cursor + 7) else {
            return false;
        };
        if address[6] & 0x60 != 0x60 || address[..6].iter().any(|byte| byte & 1 != 0) {
            return false;
        }
        let mut nonspace = false;
        let mut padding = false;
        for raw in &address[..6] {
            match raw >> 1 {
                b' ' => padding = true,
                b'A'..=b'Z' | b'0'..=b'9' if !padding => nonspace = true,
                _ => return false,
            }
        }
        if !nonspace {
            return false;
        }
        cursor += 7;
        if address[6] & 1 != 0 {
            return count >= 2 && payload.len() >= cursor + 2 && payload[cursor] == 3;
        }
    }
    false
}

fn frames(value: &Value, context: &str) -> Result<BTreeSet<String>, String> {
    let array = value
        .as_array()
        .ok_or_else(|| format!("{context}: frames must be an array"))?;
    let mut set = BTreeSet::new();
    for value in array {
        let frame = value.as_str().ok_or("frame must be hexadecimal text")?;
        require(
            valid_frame(frame),
            format!("{context}: invalid received CRC/UI frame {frame}"),
        )?;
        require(
            set.insert(frame.to_owned()),
            format!("{context}: duplicate frame {frame}"),
        )?;
    }
    Ok(set)
}

fn check_elapsed(value: &Value) -> Result<(), String> {
    match value {
        Value::Object(map) => {
            for (key, child) in map {
                if key == "elapsed_seconds" {
                    require(
                        child.as_f64().is_some_and(|n| n.is_finite() && n >= 0.0),
                        "invalid elapsed_seconds",
                    )?;
                }
                check_elapsed(child)?;
            }
        }
        Value::Array(items) => {
            for child in items {
                check_elapsed(child)?;
            }
        }
        _ => {}
    }
    Ok(())
}

fn validate_detail(task: &TaskRecord) -> Result<(), String> {
    let trials = match task.stage.as_str() {
        "quick" | "baseline-remainder" => {
            let _: adaptive::ProgressiveBaseline =
                serde_json::from_value(task.detail.clone()).map_err(|e| e.to_string())?;
            let rows = task.detail["trials"]
                .as_array()
                .ok_or("baseline trials absent")?;
            require(
                rows.len() == 4,
                "baseline task must retain four frontend/clock lanes",
            )?;
            rows
        }
        "nearest" | "early-nearest" => {
            let _: Vec<adaptive::SupplementalTrial> =
                serde_json::from_value(task.detail.clone()).map_err(|e| e.to_string())?;
            task.detail
                .as_array()
                .ok_or("nearest trials must be an array")?
        }
        "blind" | "multi-anchor" | "early-multi" => {
            let _: adaptive::ProgressiveChannel =
                serde_json::from_value(task.detail.clone()).map_err(|e| e.to_string())?;
            task.detail["trials"]
                .as_array()
                .ok_or("channel trials absent")?
        }
        _ => return Err("unknown task stage".into()),
    };
    let mut union = BTreeSet::new();
    for trial in trials {
        require(
            trial["window_index"].as_u64() == Some(task.window as u64),
            "trial window provenance mismatch",
        )?;
        require(
            trial["window_start_seconds"].as_f64() == Some(task.window as f64 * 3.0),
            "trial absolute time mismatch",
        )?;
        require(
            matches!(
                trial["frontend"].as_str(),
                Some("legacy-fir512" | "boxcar32-rms50")
            ),
            "unknown frontend",
        )?;
        union.extend(frames(&trial["frame_with_fcs_hex"], "trial")?);
    }
    require(
        union == task.frame_with_fcs_hex,
        "task frame set differs from full trial provenance",
    )?;
    check_elapsed(&task.detail)
}

fn verify_identity(identity: &input::Identity) -> Result<(), String> {
    require(sha_shape(&identity.sha256), "invalid audio SHA256")?;
    let actual = input::identity(Path::new(&identity.path))?;
    require(
        actual.sha256 == identity.sha256 && actual.bytes == identity.bytes,
        format!("audio content does not match manifest: {}", identity.path),
    )
}

fn load(path: &Path) -> Result<Session, String> {
    let lock = File::open(path.join("session.lock")).map_err(|e| format!("session lock: {e}"))?;
    #[cfg(unix)]
    {
        use std::os::fd::AsRawFd;
        require(
            unsafe { libc::flock(lock.as_raw_fd(), libc::LOCK_SH | libc::LOCK_NB) } == 0,
            "session is active/locked; comparison requires a finished stable session",
        )?;
    }
    let manifest: Manifest = typed(&path.join("manifest.json"))?;
    require(
        manifest.schema == "progressive-audio-session-v2",
        "unsupported session schema; expected progressive-audio-session-v2",
    )?;
    require(
        sha_shape(&manifest.executable_sha256),
        "invalid decoder executable SHA256",
    )?;
    let policy: Policy =
        serde_json::from_value(manifest.policy.clone()).map_err(|e| format!("policy: {e}"))?;
    require(
        policy.version == 2
            && policy.baud.is_finite()
            && policy.baud > 0.0
            && policy.window_seconds == 6.0
            && policy.hop_seconds == 3.0
            && policy.fast_timings == 8
            && policy.fast_gardner == 2
            && policy.protocol == "AX25_UI_received_FCS_plain_or_G3RUH"
            && policy.anchor_generations
                == "frozen quick prefix for exploratory tasks; frozen complete baseline for final tasks",
        "unsupported search policy",
    )?;
    verify_identity(&manifest.audio.source)?;
    if manifest.audio.source.path != manifest.audio.wav.path {
        verify_identity(&manifest.audio.wav)?;
    } else {
        require(
            manifest.audio.source.sha256 == manifest.audio.wav.sha256
                && manifest.audio.source.bytes == manifest.audio.wav.bytes,
            "same audio path has conflicting identities",
        )?;
    }
    let wav =
        hound::WavReader::open(&manifest.audio.wav.path).map_err(|e| format!("WAV header: {e}"))?;
    require(
        wav.spec().channels == 1
            && wav.spec().sample_rate == manifest.audio.sample_rate
            && wav.len() as usize == manifest.audio.samples
            && manifest.audio.samples >= 8192
            && manifest.audio.sample_rate > 0
            && manifest.audio.samples as f64 / manifest.audio.sample_rate as f64 <= 1800.0,
        "WAV/sample metadata mismatch or unsupported bounds",
    )?;
    let session_sha256 = hash(&manifest)?;
    let width = manifest.audio.sample_rate as usize * 6;
    let hop = manifest.audio.sample_rate as usize * 3;
    require(
        width >= 8192 && width <= 4_194_304,
        "unsupported task window size",
    )?;
    let mut windows = 0usize;
    let mut start = 0usize;
    while start < manifest.audio.samples {
        let end = (start + width).min(manifest.audio.samples);
        if end - start < 8192 {
            break;
        }
        windows += 1;
        if end == manifest.audio.samples {
            break;
        }
        start += hop;
    }
    require(windows > 0, "session has no eligible windows")?;
    let mut stages = vec!["quick", "early-nearest", "baseline-remainder", "nearest"];
    if policy.blind {
        stages.push("blind");
    }
    if policy.multi_anchor {
        stages.extend(["early-multi", "multi-anchor"]);
    }
    let expected: BTreeSet<_> = stages
        .iter()
        .flat_map(|stage| (0..windows).map(move |window| format!("{stage}-{window:06}.json")))
        .collect();
    let actual: BTreeSet<_> = fs::read_dir(path.join("tasks"))
        .map_err(|e| e.to_string())?
        .map(|entry| {
            entry.map_err(|e| e.to_string()).and_then(|entry| {
                entry
                    .file_name()
                    .into_string()
                    .map_err(|_| "non-UTF8 task filename".into())
            })
        })
        .collect::<Result<_, String>>()?;
    require(
        actual == expected,
        format!(
            "task filename universe mismatch; missing={:?}; extra={:?}",
            expected.difference(&actual).collect::<Vec<_>>(),
            actual.difference(&expected).collect::<Vec<_>>()
        ),
    )?;
    let mut tasks = BTreeMap::new();
    let mut all_frames = BTreeSet::new();
    let mut stage_frames = BTreeMap::<String, BTreeSet<String>>::new();
    let mut stage_counts = BTreeMap::<String, usize>::new();
    for filename in &actual {
        let commit: TaskCommit = typed(&path.join("tasks").join(filename))?;
        require(
            hash(&commit.task)? == commit.sha256,
            format!("{filename}: task checksum mismatch"),
        )?;
        let task = commit.task;
        require(
            task.schema == "progressive-audio-task-v1"
                && task.session_sha256 == session_sha256
                && stages.contains(&task.stage.as_str())
                && task.window < windows
                && *filename == format!("{}-{:06}.json", task.stage, task.window)
                && task.elapsed_seconds.is_finite()
                && task.elapsed_seconds >= 0.0,
            format!("{filename}: incompatible task schema/identity/coordinates"),
        )?;
        for frame in &task.frame_with_fcs_hex {
            require(
                valid_frame(frame),
                format!("{filename}: invalid received frame {frame}"),
            )?;
        }
        validate_detail(&task).map_err(|e| format!("{filename}: {e}"))?;
        all_frames.extend(task.frame_with_fcs_hex.iter().cloned());
        stage_frames
            .entry(task.stage.clone())
            .or_default()
            .extend(task.frame_with_fcs_hex.iter().cloned());
        *stage_counts.entry(task.stage.clone()).or_default() += 1;
        tasks.insert(filename.clone(), task);
    }
    let snapshot = input::read_json(&path.join("result.json"))?;
    require(
        snapshot["schema"] == "progressive-audio-result-v1"
            && snapshot["status"] == "complete"
            && snapshot["complete"] == true
            && snapshot["session_sha256"] == session_sha256,
        "snapshot is incomplete or has incompatible schema/session",
    )?;
    require(
        snapshot["completed_tasks"].as_u64() == Some(tasks.len() as u64)
            && snapshot["total_tasks"].as_u64() == Some(tasks.len() as u64)
            && snapshot["window_count"].as_u64() == Some(windows as u64)
            && snapshot["union_count"].as_u64() == Some(all_frames.len() as u64),
        "snapshot task/window/frame counts mismatch",
    )?;
    require(
        frames(&snapshot["frame_with_fcs_hex"], "snapshot")? == all_frames
            && snapshot["stage_counts"] == json!(stage_counts)
            && snapshot["stage_frame_with_fcs_hex"] == json!(stage_frames),
        "snapshot frame/stage content differs from authoritative tasks",
    )?;
    Ok(Session {
        path: path.to_path_buf(),
        manifest,
        session_sha256,
        tasks,
        frames: all_frames,
        windows,
        _lock: lock,
    })
}

fn strip_elapsed(value: &mut Value) {
    match value {
        Value::Object(map) => {
            map.remove("elapsed_seconds");
            for child in map.values_mut() {
                strip_elapsed(child);
            }
        }
        Value::Array(values) => {
            for child in values {
                strip_elapsed(child);
            }
        }
        _ => {}
    }
}
fn normalized(task: &TaskRecord) -> Result<Value, String> {
    let mut value = serde_json::to_value(task).map_err(|e| e.to_string())?;
    strip_elapsed(&mut value);
    value
        .as_object_mut()
        .ok_or("task is not an object")?
        .remove("session_sha256");
    Ok(value)
}

fn difference_paths(
    left: &Value,
    right: &Value,
    path: &str,
    count: &mut usize,
    examples: &mut Vec<String>,
) {
    if left == right {
        return;
    }
    match (left, right) {
        (Value::Object(a), Value::Object(b)) => {
            let keys: BTreeSet<_> = a.keys().chain(b.keys()).collect();
            for key in keys {
                let next = format!("{path}/{}", key.replace('~', "~0").replace('/', "~1"));
                match (a.get(key), b.get(key)) {
                    (Some(x), Some(y)) => difference_paths(x, y, &next, count, examples),
                    _ => {
                        *count += 1;
                        if examples.len() < 256 {
                            examples.push(next);
                        }
                    }
                }
            }
        }
        (Value::Array(a), Value::Array(b)) => {
            for index in 0..a.len().max(b.len()) {
                let next = format!("{path}/{index}");
                match (a.get(index), b.get(index)) {
                    (Some(x), Some(y)) => difference_paths(x, y, &next, count, examples),
                    _ => {
                        *count += 1;
                        if examples.len() < 256 {
                            examples.push(next);
                        }
                    }
                }
            }
        }
        _ => {
            *count += 1;
            if examples.len() < 256 {
                examples.push(path.into());
            }
        }
    }
}

fn compare(left: &Path, right: &Path) -> Value {
    let left_loaded = load(left);
    let right_loaded = load(right);
    let mut errors = Vec::new();
    if let Err(error) = &left_loaded {
        errors.push(json!({"side":"left","error":error}));
    }
    if let Err(error) = &right_loaded {
        errors.push(json!({"side":"right","error":error}));
    }
    let (Ok(left), Ok(right)) = (left_loaded, right_loaded) else {
        return json!({"schema":"progressive-session-comparison-v1","equal":false,"status":"invalid-input","errors":errors});
    };
    let a = &left.manifest;
    let b = &right.manifest;
    let identity_checks = json!({"same_policy":a.policy == b.policy,"same_executable_sha256":a.executable_sha256 == b.executable_sha256,
        "same_source_sha256_and_bytes":a.audio.source.sha256 == b.audio.source.sha256 && a.audio.source.bytes == b.audio.source.bytes,
        "same_wav_sha256_and_bytes":a.audio.wav.sha256 == b.audio.wav.sha256 && a.audio.wav.bytes == b.audio.wav.bytes,
        "same_sample_metadata":a.audio.sample_rate == b.audio.sample_rate && a.audio.samples == b.audio.samples,
        "same_conversion_presence":a.audio.conversion_command.is_some() == b.audio.conversion_command.is_some()});
    let metadata_equal = identity_checks
        .as_object()
        .unwrap()
        .values()
        .all(|v| *v == true);
    let left_names: BTreeSet<_> = left.tasks.keys().cloned().collect();
    let right_names: BTreeSet<_> = right.tasks.keys().cloned().collect();
    let only_left: Vec<_> = left_names.difference(&right_names).cloned().collect();
    let only_right: Vec<_> = right_names.difference(&left_names).cloned().collect();
    let mut differences = Vec::new();
    for name in left_names.intersection(&right_names) {
        let l = normalized(&left.tasks[name]).expect("validated serializable task");
        let r = normalized(&right.tasks[name]).expect("validated serializable task");
        if serde_json::to_vec(&l).unwrap() != serde_json::to_vec(&r).unwrap() {
            let mut count = 0;
            let mut paths = Vec::new();
            difference_paths(&l, &r, "", &mut count, &mut paths);
            differences.push(json!({"task":name,"left_normalized_sha256":hash(&l).unwrap(),"right_normalized_sha256":hash(&r).unwrap(),
                "different_json_value_count":count,"json_pointer_examples":paths,"pointer_examples_truncated":count>256,
                "frames_only_left":left.tasks[name].frame_with_fcs_hex.difference(&right.tasks[name].frame_with_fcs_hex).collect::<Vec<_>>(),
                "frames_only_right":right.tasks[name].frame_with_fcs_hex.difference(&left.tasks[name].frame_with_fcs_hex).collect::<Vec<_>>() }));
        }
    }
    let equal = metadata_equal
        && only_left.is_empty()
        && only_right.is_empty()
        && differences.is_empty()
        && left.frames == right.frames;
    json!({"schema":"progressive-session-comparison-v1","equal":equal,"status":if equal {"equal"}else{"different"},
        "left_session":left.path,"right_session":right.path,"left_session_sha256":left.session_sha256,"right_session_sha256":right.session_sha256,
        "identity_checks":identity_checks,"audio_files_rehashed_and_wav_header_checked":true,"every_task_commit_sha256_verified":true,
        "complete_snapshot_and_task_universe_verified":true,"independent_bitwise_received_fcs_and_ui_verified":true,
        "normalization":"remove only elapsed_seconds recursively and top-level task session_sha256; keep every other task/detail value and array order",
        "manifest_comparison":"policy, executable SHA, source/wav SHA+bytes and sample metadata; path differences allowed only because content identities are independently checked",
        "decoder_executable_file_rehashed":false,"left_task_count":left.tasks.len(),"right_task_count":right.tasks.len(),
        "left_window_count":left.windows,"right_window_count":right.windows,"tasks_only_left":only_left,"tasks_only_right":only_right,
        "different_tasks":differences,"left_frame_count":left.frames.len(),"right_frame_count":right.frames.len(),
        "frames_only_left":left.frames.difference(&right.frames).collect::<Vec<_>>(),"frames_only_right":right.frames.difference(&left.frames).collect::<Vec<_>>(),
        "publication_ready":false,"comparison_is_decoder_accuracy_measurement":false})
}

fn run(args: Args) -> Result<bool, String> {
    let mut report = compare(&args.left, &args.right);
    report["comparator_executable"] = json!(input::identity(
        &std::env::current_exe().map_err(|e| e.to_string())?
    )?);
    input::write_json_new(&args.output, &report)?;
    println!(
        "{}",
        json!({"status":report["status"],"equal":report["equal"],"different_tasks":report["different_tasks"].as_array().map(Vec::len),"output":args.output})
    );
    Ok(report["equal"] == true)
}
fn main() {
    match run(Args::parse()) {
        Ok(true) => {}
        Ok(false) => std::process::exit(1),
        Err(error) => {
            eprintln!("{error}");
            std::process::exit(2);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn create_audio(root: &Path) -> PreparedMetadata {
        let path = root.join("input.wav");
        let mut wav = hound::WavWriter::create(
            &path,
            hound::WavSpec {
                channels: 1,
                sample_rate: 48000,
                bits_per_sample: 16,
                sample_format: hound::SampleFormat::Int,
            },
        )
        .unwrap();
        for _ in 0..8192 {
            wav.write_sample(0i16).unwrap();
        }
        wav.finalize().unwrap();
        let identity = input::identity(&path).unwrap();
        PreparedMetadata {
            source: identity.clone(),
            wav: identity,
            sample_rate: 48000,
            samples: 8192,
            conversion_command: None,
        }
    }
    fn fixture(path: &Path, audio: &PreparedMetadata, elapsed: f64) {
        fs::create_dir(path).unwrap();
        File::create(path.join("session.lock")).unwrap();
        fs::create_dir(path.join("tasks")).unwrap();
        let manifest = Manifest {
            schema: "progressive-audio-session-v2".into(),
            audio: audio.clone(),
            executable_sha256: "ab".repeat(32),
            policy: json!({"baud":9600.0,"blind":true,"multi_anchor":true,"window_seconds":6.0,"hop_seconds":3.0,"fast_timings":8,"fast_gardner":2,"protocol":"AX25_UI_received_FCS_plain_or_G3RUH","version":2,"anchor_generations":"frozen quick prefix for exploratory tasks; frozen complete baseline for final tasks"}),
        };
        input::write_json_new(&path.join("manifest.json"), &manifest).unwrap();
        let session = hash(&manifest).unwrap();
        let mut counts = BTreeMap::new();
        let mut stage_frames = BTreeMap::<String, Vec<String>>::new();
        for stage in [
            "quick",
            "early-nearest",
            "baseline-remainder",
            "nearest",
            "blind",
            "early-multi",
            "multi-anchor",
        ] {
            let detail = if stage == "quick" || stage == "baseline-remainder" {
                let trials: Vec<_> = ["legacy-fir512", "boxcar32-rms50"]
                    .into_iter()
                    .flat_map(|frontend| {
                        ["fixed", "gardner"].map(move |clock| {
                            json!({"window_index":0,"frontend":frontend,"clock":clock,
                                "window_start_seconds":0.0,"elapsed_seconds":elapsed,
                                "hypotheses":0,"frame_with_fcs_hex":[]})
                        })
                    })
                    .collect();
                json!({"anchors":[],"rejections":[],"trials":trials})
            } else if stage == "nearest" || stage == "early-nearest" {
                json!([])
            } else {
                json!({"trials":[],"rejections":[],"skips":[]})
            };
            let task = TaskRecord {
                schema: "progressive-audio-task-v1".into(),
                session_sha256: session.clone(),
                stage: stage.into(),
                window: 0,
                elapsed_seconds: elapsed,
                frame_with_fcs_hex: BTreeSet::new(),
                detail,
            };
            let commit = TaskCommit {
                sha256: hash(&task).unwrap(),
                task,
            };
            input::write_json_new(
                &path.join("tasks").join(format!("{stage}-000000.json")),
                &commit,
            )
            .unwrap();
            counts.insert(stage, 1);
            stage_frames.insert(stage.into(), vec![]);
        }
        input::write_json_new(&path.join("result.json"), &json!({"schema":"progressive-audio-result-v1","session_sha256":session,"status":"complete","complete":true,"completed_tasks":7,"total_tasks":7,"window_count":1,"union_count":0,"frame_with_fcs_hex":[],"stage_counts":counts,"stage_frame_with_fcs_hex":stage_frames})).unwrap();
    }
    fn replace_json(path: &Path, value: &impl Serialize) {
        fs::write(path, serde_json::to_vec(value).unwrap()).unwrap();
    }
    #[test]
    fn complete_sessions_ignore_only_elapsed_and_preserve_full_details() {
        let root = tempfile::tempdir().unwrap();
        let audio = create_audio(root.path());
        let left = root.path().join("left");
        let right = root.path().join("right");
        fixture(&left, &audio, 1.0);
        fixture(&right, &audio, 8.0);
        assert_eq!(compare(&left, &right)["equal"], true);
        let path = right.join("tasks/quick-000000.json");
        let mut commit: TaskCommit = typed(&path).unwrap();
        commit.task.detail["trials"][0]["hypotheses"] = json!(1);
        commit.sha256 = hash(&commit.task).unwrap();
        replace_json(&path, &commit);
        let report = compare(&left, &right);
        assert_eq!(report["status"], "different");
        assert_eq!(report["different_tasks"].as_array().unwrap().len(), 1);
        assert_eq!(
            report["different_tasks"][0]["json_pointer_examples"],
            json!(["/detail/trials/0/hypotheses"])
        );
    }
    #[test]
    fn corrupt_commit_and_missing_or_extra_task_never_vacuously_pass() {
        let root = tempfile::tempdir().unwrap();
        let audio = create_audio(root.path());
        let left = root.path().join("left");
        let right = root.path().join("right");
        fixture(&left, &audio, 1.0);
        fixture(&right, &audio, 1.0);
        let path = right.join("tasks/quick-000000.json");
        let mut commit: TaskCommit = typed(&path).unwrap();
        commit.task.elapsed_seconds = 2.0;
        replace_json(&path, &commit);
        assert!(load(&right).err().unwrap().contains("checksum"));
        commit.sha256 = hash(&commit.task).unwrap();
        replace_json(&path, &commit);
        fs::rename(&path, right.join("tasks/not-a-task.json")).unwrap();
        let report = compare(&left, &right);
        assert_eq!(report["status"], "invalid-input");
        assert!(
            report["errors"][0]["error"]
                .as_str()
                .unwrap()
                .contains("missing=")
        );
    }
    #[test]
    fn unsupported_schema_partial_snapshot_and_wrong_counts_rejected() {
        let root = tempfile::tempdir().unwrap();
        let audio = create_audio(root.path());
        let path = root.path().join("session");
        fixture(&path, &audio, 1.0);
        let snapshot_path = path.join("result.json");
        let original = input::read_json(&snapshot_path).unwrap();
        for changed in [
            json!({"status":"partial"}),
            json!({"union_count":1}),
            json!({"total_tasks":0}),
        ] {
            let mut snapshot = original.clone();
            for (key, value) in changed.as_object().unwrap() {
                snapshot[key] = value.clone();
            }
            replace_json(&snapshot_path, &snapshot);
            assert!(load(&path).is_err());
        }
        replace_json(&snapshot_path, &original);
        let manifest_path = path.join("manifest.json");
        let mut manifest: Manifest = typed(&manifest_path).unwrap();
        manifest.schema = "unknown".into();
        replace_json(&manifest_path, &manifest);
        assert!(
            load(&path)
                .err()
                .unwrap()
                .contains("unsupported session schema")
        );
    }
    #[test]
    fn independent_frame_checker_accepts_frozen_oracle_and_rejects_corruption() {
        let oracle: Value =
            serde_json::from_str(include_str!("../rust/tests/protocol_oracle.json")).unwrap();
        let frame = oracle["cases"][0]["expected"][0]["frames_hex"][0]
            .as_str()
            .unwrap();
        assert!(valid_frame(frame));
        let mut corrupted = hex::decode(frame).unwrap();
        corrupted[16] ^= 1;
        assert!(!valid_frame(&hex::encode(corrupted)));
        assert!(!valid_frame("0000"));
    }
    #[test]
    fn normalization_does_not_hide_other_timing_or_nested_session_fields() {
        let mut value = json!({"elapsed_seconds":1.0,"timing":{"elapsed_seconds":2.0,"phase_samples":0.25},"session_sha256":"nested-must-remain"});
        strip_elapsed(&mut value);
        assert_eq!(
            value,
            json!({"timing":{"phase_samples":0.25},"session_sha256":"nested-must-remain"})
        );
    }

    #[test]
    fn relocated_identical_audio_allows_different_verified_session_hashes() {
        let root = tempfile::tempdir().unwrap();
        let second = root.path().join("relocated");
        fs::create_dir(&second).unwrap();
        let audio_a = create_audio(root.path());
        let audio_b = create_audio(&second);
        let left = root.path().join("left");
        let right = root.path().join("right");
        fixture(&left, &audio_a, 1.0);
        fixture(&right, &audio_b, 2.0);
        let report = compare(&left, &right);
        assert_eq!(report["equal"], true);
        assert_ne!(
            report["left_session_sha256"],
            report["right_session_sha256"]
        );
    }

    #[test]
    fn exact_pdu_difference_is_reported_after_valid_commit_and_snapshot_checks() {
        let root = tempfile::tempdir().unwrap();
        let audio = create_audio(root.path());
        let left = root.path().join("left");
        let right = root.path().join("right");
        fixture(&left, &audio, 1.0);
        fixture(&right, &audio, 1.0);
        let oracle: Value =
            serde_json::from_str(include_str!("../rust/tests/protocol_oracle.json")).unwrap();
        let frame = oracle["cases"][0]["expected"][0]["frames_hex"][0]
            .as_str()
            .unwrap();
        let path = right.join("tasks/quick-000000.json");
        let mut commit: TaskCommit = typed(&path).unwrap();
        commit.task.frame_with_fcs_hex.insert(frame.into());
        commit.task.detail["trials"][0]["frame_with_fcs_hex"] = json!([frame]);
        commit.sha256 = hash(&commit.task).unwrap();
        replace_json(&path, &commit);
        let snapshot_path = right.join("result.json");
        let mut snapshot = input::read_json(&snapshot_path).unwrap();
        snapshot["frame_with_fcs_hex"] = json!([frame]);
        snapshot["union_count"] = json!(1);
        snapshot["stage_frame_with_fcs_hex"]["quick"] = json!([frame]);
        replace_json(&snapshot_path, &snapshot);
        let report = compare(&left, &right);
        assert_eq!(report["status"], "different");
        assert_eq!(report["frames_only_right"], json!([frame]));
        assert_eq!(
            report["different_tasks"][0]["frames_only_right"],
            json!([frame])
        );
        // Even with a recomputed task digest, dropping nested provenance fails.
        commit.task.detail["trials"][0]["frame_with_fcs_hex"] = json!([]);
        commit.sha256 = hash(&commit.task).unwrap();
        replace_json(&path, &commit);
        assert!(load(&right).err().unwrap().contains("provenance"));
    }
}
