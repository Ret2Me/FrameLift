//! Bounded read-only regression/degenerate-window evidence collector.
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::collections::{BTreeMap, BTreeSet};
use std::fs;
use std::path::Path;
use telemetry_yield_rs::{dsp, input};

#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Task {
    schema: String,
    session_sha256: String,
    stage: String,
    window: usize,
    elapsed_seconds: f64,
    frame_with_fcs_hex: BTreeSet<String>,
    detail: Value,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Commit {
    sha256: String,
    task: Task,
}
#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Manifest {
    schema: String,
    audio: telemetry_yield_rs::progressive_audio::PreparedMetadata,
    executable_sha256: String,
    policy: Value,
}
fn sha(bytes: &[u8]) -> String {
    hex::encode(Sha256::digest(bytes))
}
fn read(path: &Path) -> Value {
    serde_json::from_slice(&fs::read(path).unwrap()).unwrap()
}
fn ignore_timing(value: &mut Value) {
    match value {
        Value::Object(map) => {
            map.remove("elapsed_seconds");
            for v in map.values_mut() {
                ignore_timing(v);
            }
        }
        Value::Array(a) => {
            for v in a {
                ignore_timing(v);
            }
        }
        _ => (),
    }
}
fn fcs_ok(full: &str) -> bool {
    let b = hex::decode(full).unwrap();
    if b.len() < 18 {
        return false;
    }
    let mut crc = 0xffffu16;
    for x in &b[..b.len() - 2] {
        crc ^= *x as u16;
        for _ in 0..8 {
            crc = if crc & 1 == 1 {
                (crc >> 1) ^ 0x8408
            } else {
                crc >> 1
            };
        }
    }
    !crc == u16::from_le_bytes([b[b.len() - 2], b[b.len() - 1]])
}
fn session(root: &Path) -> (Value, BTreeMap<String, Value>, Value) {
    let manifest_path = root.join("manifest.json");
    let m: Manifest = serde_json::from_slice(&fs::read(&manifest_path).unwrap()).unwrap();
    let digest = sha(&serde_json::to_vec(&m).unwrap());
    assert_eq!(m.schema, "progressive-audio-session-v2");
    assert_eq!(
        serde_json::to_value(input::identity(Path::new(&m.audio.source.path)).unwrap()).unwrap(),
        serde_json::to_value(&m.audio.source).unwrap()
    );
    let result = read(&root.join("result.json"));
    assert_eq!(result["status"], "complete");
    assert_eq!(result["complete"], true);
    assert_eq!(result["session_sha256"], digest);
    assert_eq!(result["completed_tasks"], result["total_tasks"]);
    let frames: BTreeSet<String> =
        serde_json::from_value(result["frame_with_fcs_hex"].clone()).unwrap();
    assert_eq!(result["union_count"].as_u64(), Some(frames.len() as u64));
    assert!(frames.iter().all(|f| fcs_ok(f)));
    let mut tasks = BTreeMap::new();
    let mut union = BTreeSet::new();
    let mut ids = vec![];
    for entry in fs::read_dir(root.join("tasks")).unwrap() {
        let path = entry.unwrap().path();
        if path.extension().and_then(|e| e.to_str()) != Some("json") {
            continue;
        }
        let bytes = fs::read(&path).unwrap();
        let c: Commit = serde_json::from_slice(&bytes).unwrap();
        assert_eq!(sha(&serde_json::to_vec(&c.task).unwrap()), c.sha256);
        assert_eq!(c.task.schema, "progressive-audio-task-v1");
        assert_eq!(c.task.session_sha256, digest);
        assert!(c.task.frame_with_fcs_hex.iter().all(|f| fcs_ok(f)));
        union.extend(c.task.frame_with_fcs_hex.iter().cloned());
        let key = format!("{}-{:06}.json", c.task.stage, c.task.window);
        assert_eq!(path.file_name().unwrap().to_str().unwrap(), key);
        let mut semantic = serde_json::to_value(&c.task).unwrap();
        ignore_timing(&mut semantic);
        assert!(tasks.insert(key.clone(), semantic).is_none());
        ids.push(json!({"name":key,"file_sha256":sha(&bytes),"commit_sha256":c.sha256}));
    }
    ids.sort_by_key(|v| v["name"].as_str().unwrap().to_owned());
    assert_eq!(tasks.len() as u64, result["total_tasks"].as_u64().unwrap());
    assert_eq!(union, frames);
    let receipts: Vec<_> = fs::read_dir(root.join("runs"))
        .unwrap()
        .map(|e| e.unwrap().path().join("receipt.json"))
        .filter(|p| p.exists())
        .map(|p| json!({"identity":input::identity(&p).unwrap(),"receipt":read(&p)}))
        .collect();
    let evidence = json!({"root":root.canonicalize().unwrap(),"result_identity":input::identity(&root.join("result.json")).unwrap(),
        "manifest_identity":input::identity(&manifest_path).unwrap(),"manifest":m,"session_sha256":digest,
        "task_count":tasks.len(),"task_manifest_sha256":sha(&serde_json::to_vec(&ids).unwrap()),"tasks":ids,
        "received_fcs_checked_independently":true,"union_equals_all_task_union":true,"receipts":receipts});
    (result, tasks, evidence)
}
fn resume(a: &Path, b: &Path) -> Value {
    let (ra, ta, ea) = session(a);
    let (rb, tb, eb) = session(b);
    let keys: BTreeSet<_> = ta.keys().chain(tb.keys()).cloned().collect();
    let differences: Vec<_> = keys
        .into_iter()
        .filter(|key| ta.get(key) != tb.get(key))
        .collect();
    json!({"schema":"resume-independent-regression-v1","status":if ra==rb && differences.is_empty() && ea["manifest"]==eb["manifest"] {"pass"} else {"fail"},
        "same_complete_final_result":ra==rb,"same_full_session_contract":ea["manifest"]==eb["manifest"],
        "same_task_semantics_excluding_elapsed_seconds":differences.is_empty(),"different_tasks":differences,
        "full_frame_union":ra["frame_with_fcs_hex"],"union_count":ra["union_count"],"resumed":ea,"uninterrupted":eb,
        "scope":"one preserved source, fixed binary/policy, exact received-frame/task semantics; timing is excluded and not comparable across host loads",
        "publication_ready":false,"deployment_ready":false})
}

fn candidate(a: &Path, b: &Path) -> Value {
    let (mut ra, mut ta, ea) = session(a);
    let (mut rb, mut tb, eb) = session(b);
    ra.as_object_mut().unwrap().remove("session_sha256");
    rb.as_object_mut().unwrap().remove("session_sha256");
    for task in ta.values_mut().chain(tb.values_mut()) {
        task.as_object_mut().unwrap().remove("session_sha256");
    }
    let mut ma = ea["manifest"].clone();
    let mut mb = eb["manifest"].clone();
    let xa = ma
        .as_object_mut()
        .unwrap()
        .remove("executable_sha256")
        .unwrap();
    let xb = mb
        .as_object_mut()
        .unwrap()
        .remove("executable_sha256")
        .unwrap();
    let keys: BTreeSet<_> = ta.keys().chain(tb.keys()).cloned().collect();
    let differences: Vec<_> = keys
        .into_iter()
        .filter(|key| ta.get(key) != tb.get(key))
        .collect();
    json!({"schema":"candidate-independent-regression-v1","status":if ra==rb && differences.is_empty() && ma==mb && xa!=xb {"pass"} else {"fail"},
        "same_complete_result_except_session_identity":ra==rb,"same_audio_and_policy_contract":ma==mb,
        "same_task_semantics_except_elapsed_and_session_identity":differences.is_empty(),"different_tasks":differences,
        "different_executable_identity_as_expected":xa!=xb,"candidate_executable_sha256":xa,"prior_executable_sha256":xb,
        "candidate":ea,"prior":eb,"union_count":ra["union_count"],
        "scope":"one complete frozen public input; newly repaired binary compared with historical binary; only elapsed_seconds and expected session/executable identity changes excluded",
        "publication_ready":false,"deployment_ready":false})
}
fn windows(wav: &Path, journal: &Path) -> Value {
    let mut reader = hound::WavReader::open(wav).unwrap();
    let spec = reader.spec();
    assert_eq!(spec.channels, 1);
    assert_eq!(spec.bits_per_sample, 16);
    assert_eq!(spec.sample_format, hound::SampleFormat::Int);
    let pcm: Vec<f64> = reader
        .samples::<i16>()
        .map(|v| v.unwrap() as f64 / 32768.0)
        .collect();
    let mut rows = vec![];
    for line in fs::read_to_string(journal).unwrap().lines() {
        let row: Value = serde_json::from_str(line).unwrap();
        if row["failures"].as_array().unwrap().is_empty() {
            continue;
        }
        let start =
            (row["offset_seconds"].as_f64().unwrap() * spec.sample_rate as f64).round() as usize;
        let values = &pcm[start..start + row["samples"].as_u64().unwrap() as usize];
        let mut absolute: Vec<_> = values.iter().map(|v| v.abs()).collect();
        absolute.sort_by(f64::total_cmp);
        let result = dsp::frontend(values, spec.sample_rate, &dsp::DspConfig::default());
        rows.push(json!({"offset_seconds":row["offset_seconds"],"samples":values.len(),"zero_samples":values.iter().filter(|v|**v==0.0).count(),
            "raw_absolute_q90":absolute[((absolute.len()-1) as f64*0.9).floor() as usize],"raw_absolute_peak":absolute.last(),
            "frontend_status":if result.is_ok(){"complete"} else {"failed"},"frontend_error":result.as_ref().err(),
            "frontend_samples":result.as_ref().ok().map(|r|r.samples.len())}));
    }
    json!({"schema":"degenerate-window-diagnostic-v1","input":input::identity(wav).unwrap(),"journal":input::identity(journal).unwrap(),"rows":rows})
}
fn main() {
    let args: Vec<_> = std::env::args().collect();
    assert_eq!(args.len(), 5, "mode input_a input_b output");
    let value = match args[1].as_str() {
        "resume" => resume(Path::new(&args[2]), Path::new(&args[3])),
        "candidate" => candidate(Path::new(&args[2]), Path::new(&args[3])),
        "windows" => windows(Path::new(&args[2]), Path::new(&args[3])),
        _ => panic!("unknown mode"),
    };
    input::write_json_new(Path::new(&args[4]), &value).unwrap();
    println!(
        "{}",
        json!({"output":args[4],"schema":value["schema"],"status":value["status"],"rows":value["rows"].as_array().map(|r|r.len()),"union_count":value["union_count"]})
    );
}
