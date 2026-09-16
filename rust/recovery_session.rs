//! Resumable, immutable window worklist for advanced IQ recovery.
//! Completed windows are reused only with the same source, executable, compute
//! identity and scientific plan. A window is atomic; no hard wall-time SLA.
use crate::{advanced_iq, generic, input};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::{
    collections::BTreeMap,
    fs::{self, OpenOptions},
    path::Path,
};

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Window {
    pub start_sample: usize,
    pub sample_count: usize,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Plan {
    pub format: generic::InputFormat,
    pub sample_rate_hz: u32,
    pub windows: Vec<Window>,
    pub receiver: advanced_iq::Config,
}

fn hash(value: &Value) -> Result<String, String> {
    Ok(hex::encode(Sha256::digest(
        serde_json::to_vec(value).map_err(|e| e.to_string())?,
    )))
}

fn checkpoint_hash(record: &Value) -> Result<String, String> {
    let mut record = record.clone();
    record
        .as_object_mut()
        .ok_or("checkpoint must be an object")?
        .remove("checkpoint_sha256");
    hash(&record)
}

fn verify_guards(
    source: &crate::recovery_guard::Guard,
    runtime: &crate::recovery_guard::Guard,
    out: &Path,
) -> Result<(), String> {
    if let Err(error) = source.verify().and_then(|()| runtime.verify()) {
        let invalidated = out.join("invalidated.json");
        if !invalidated.exists() {
            input::write_json_new(
                &invalidated,
                &json!({"status":"invalidated","reason":error}),
            )?;
        }
        return Err(error);
    }
    Ok(())
}

fn guarded_window(
    source: &crate::recovery_guard::Guard,
    runtime: &crate::recovery_guard::Guard,
    out: &Path,
    format: &generic::InputFormat,
    window: &Window,
) -> Result<Vec<num_complex::Complex64>, String> {
    let result = source.read_iq_window(format, window.start_sample, window.sample_count);
    verify_guards(source, runtime, out)?;
    result
}

fn admit_report(report: &Value, plan: &Plan, window: &Window) -> Result<(), String> {
    use std::collections::BTreeSet;
    let frames = report["frames"]
        .as_array()
        .ok_or("checkpoint missing frames")?;
    let end = window
        .start_sample
        .checked_add(window.sample_count)
        .ok_or("window coordinate overflow")?;
    let mut identities = BTreeSet::new();
    for frame in frames {
        let hex = frame["frame_hex"]
            .as_str()
            .ok_or("checkpoint missing frame hex")?;
        let bytes = hex::decode(hex).map_err(|e| e.to_string())?;
        if bytes.len() != plan.receiver.code.frame_bytes()? || !identities.insert(hex.to_string()) {
            return Err("checkpoint frame size/uniqueness mismatch".into());
        }
        let required = plan.receiver.validator.decode(&bytes)?;
        let layers = frame["validation_layers"]
            .as_array()
            .ok_or("checkpoint missing integrity layers")?;
        if required.iter().any(|layer| !layers.contains(&json!(layer))) {
            return Err("checkpoint lacks required integrity evidence".into());
        }
        let provenance = frame["provenance"]
            .as_array()
            .ok_or("checkpoint missing provenance")?;
        if provenance.is_empty() {
            return Err("checkpoint empty frame provenance".into());
        }
        for p in provenance {
            let begin = p["start_sample"]
                .as_u64()
                .ok_or("checkpoint invalid start")?;
            let stop = p["end_sample"].as_u64().ok_or("checkpoint invalid end")?;
            if begin < window.start_sample as u64 || begin >= stop || stop > end as u64 {
                return Err("checkpoint provenance outside scheduled window".into());
            }
        }
    }
    let set = |key: &str| -> Result<BTreeSet<String>, String> {
        let values = report[key]
            .as_array()
            .ok_or("checkpoint missing frame set")?;
        let set = values
            .iter()
            .map(|v| {
                v.as_str()
                    .map(str::to_string)
                    .ok_or("checkpoint invalid frame set".to_string())
            })
            .collect::<Result<BTreeSet<_>, _>>()?;
        if set.len() != values.len() {
            return Err("checkpoint duplicate frame set".into());
        }
        Ok(set)
    };
    let baseline = set("baseline_frames")?;
    if !baseline.is_subset(&identities)
        || set("added_frames")? != identities.difference(&baseline).cloned().collect()
    {
        return Err("checkpoint baseline/addition mismatch".into());
    }
    if report["consumed_work"]
        .as_u64()
        .is_none_or(|n| n > plan.receiver.maximum_work)
    {
        return Err("checkpoint invalid work accounting".into());
    }
    Ok(())
}

fn lock(output: &Path) -> Result<std::fs::File, String> {
    let mut options = OpenOptions::new();
    options.read(true).write(true).create(true).truncate(false);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.custom_flags(libc::O_NOFOLLOW | libc::O_NONBLOCK);
    }
    let file = options
        .open(output.join("session.lock"))
        .map_err(|e| e.to_string())?;
    if !file.metadata().map_err(|e| e.to_string())?.is_file() {
        return Err("session lock must be regular file".into());
    }
    #[cfg(unix)]
    {
        use std::os::fd::AsRawFd;
        if unsafe { libc::flock(file.as_raw_fd(), libc::LOCK_EX | libc::LOCK_NB) } != 0 {
            return Err("recovery session is already running".into());
        }
    }
    #[cfg(not(unix))]
    return Err("resumable IQ sessions currently require Unix advisory file locking".into());
    Ok(file)
}

fn regular_entry(path: &Path) -> Result<bool, String> {
    match fs::symlink_metadata(path) {
        Ok(meta) if meta.file_type().is_file() => Ok(true),
        Ok(_) => Err(format!(
            "session entry is not a regular file: {}",
            path.display()
        )),
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(false),
        Err(e) => Err(e.to_string()),
    }
}

pub fn run(
    source_path: &Path,
    plan: &Plan,
    output: &Path,
    resume: bool,
    max_windows: usize,
) -> Result<Value, String> {
    if !cfg!(target_os = "linux") {
        return Err("immutable recovery sessions require Linux change notifications".into());
    }
    if plan.windows.is_empty()
        || plan.windows.len() > 4096
        || max_windows == 0
        || max_windows > 4096
    {
        return Err(
            "recovery sessions require 1..4096 windows and invocation window budget".into(),
        );
    }
    let width = match plan.format {
        generic::InputFormat::Ci16Le => 4,
        generic::InputFormat::Cf32Le => 8,
        generic::InputFormat::Cf64Le => 16,
        _ => return Err("recovery sessions require explicit IQ, not OGG/WAV".into()),
    };
    let source_guard = crate::recovery_guard::Guard::open(source_path)?;
    let source = source_guard.identity();
    for window in &plan.windows {
        plan.receiver
            .validate(plan.sample_rate_hz, window.sample_count)?;
        if window
            .start_sample
            .checked_add(window.sample_count)
            .is_none_or(|n| n as u128 * width > source.bytes as u128)
        {
            return Err("session window exceeds source length".into());
        }
    }
    if source.bytes % width as u64 != 0 {
        return Err("source IQ has incomplete samples".into());
    }
    let executable = std::env::current_exe().map_err(|e| e.to_string())?;
    let runtime_guard = crate::recovery_guard::Guard::open(&executable)?;
    let runtime = runtime_guard.identity();
    let compute = crate::compute::current().identity();
    let scientific = json!({"schema":"framelift-recovery-session-plan-v1","source":source,"runtime":runtime,"compute":compute,"plan":plan});
    let identity = hash(&scientific)?;
    let out = if resume {
        let meta = fs::symlink_metadata(output).map_err(|e| e.to_string())?;
        if !meta.is_dir() || meta.file_type().is_symlink() {
            return Err("session output must be a real directory".into());
        }
        output.canonicalize().map_err(|e| e.to_string())?
    } else {
        input::existing_new_dir(output)?
    };
    let _lock = lock(&out)?;
    if fs::symlink_metadata(out.join("invalidated.json")).is_ok() {
        return Err("recovery session was invalidated; start a fresh session".into());
    }
    verify_guards(&source_guard, &runtime_guard, &out)?;
    let plan_path = out.join("plan.json");
    if regular_entry(&plan_path)? {
        if !resume || input::read_json(&plan_path)? != scientific {
            return Err("resume plan/source/runtime/compute identity mismatch".into());
        }
    } else {
        if resume {
            return Err("resume session has no committed plan".into());
        }
        input::write_json_new(&plan_path, &scientific)?;
    }
    let mut results = Vec::new();
    let mut executed = 0usize;
    let mut reused = 0usize;
    for (index, window) in plan.windows.iter().enumerate() {
        let path = out.join(format!("window-{index:06}.json"));
        let record = if regular_entry(&path)? {
            let record = input::read_json(&path)?;
            if record["session_sha256"] != identity
                || record["window_index"] != index
                || record["checkpoint_sha256"] != checkpoint_hash(&record)?
            {
                return Err(format!(
                    "cached window {index} failed identity/integrity check"
                ));
            }
            let iq = guarded_window(&source_guard, &runtime_guard, &out, &plan.format, window)?;
            let mut consumed = Sha256::new();
            for z in &iq {
                consumed.update(z.re.to_bits().to_le_bytes());
                consumed.update(z.im.to_bits().to_le_bytes());
            }
            if record["iq_f64_le_sha256"] != hex::encode(consumed.finalize()) {
                return Err("cached consumed-IQ identity mismatch".into());
            }
            reused += 1;
            record
        } else {
            if executed >= max_windows {
                continue;
            }
            let iq = guarded_window(&source_guard, &runtime_guard, &out, &plan.format, window)?;
            let mut consumed = Sha256::new();
            for z in &iq {
                consumed.update(z.re.to_bits().to_le_bytes());
                consumed.update(z.im.to_bits().to_le_bytes());
            }
            let mut report =
                advanced_iq::decode(&iq, plan.sample_rate_hz, &plan.receiver, &source.sha256)?;
            advanced_iq::offset_report(&mut report, window.start_sample)?;
            let report = serde_json::to_value(report).map_err(|e| e.to_string())?;
            let mut record = json!({"session_sha256":identity,"window_index":index,"window":window,"iq_f64_le_sha256":hex::encode(consumed.finalize()),"report":report});
            record["checkpoint_sha256"] = json!(checkpoint_hash(&record)?);
            admit_report(&record["report"], plan, window)?;
            // Recheck source before any reusable result becomes visible.
            verify_guards(&source_guard, &runtime_guard, &out)?;
            input::write_json_new(&path, &record)?;
            executed += 1;
            record
        };
        admit_report(&record["report"], plan, window)?;
        results.push(record);
    }
    verify_guards(&source_guard, &runtime_guard, &out)?;
    let mut frames = BTreeMap::<String, Vec<Value>>::new();
    for record in &results {
        for frame in record["report"]["frames"].as_array().unwrap() {
            frames
                .entry(frame["frame_hex"].as_str().unwrap().into())
                .or_default()
                .extend(
                    frame["provenance"]
                        .as_array()
                        .ok_or("cached frame missing provenance")?
                        .iter()
                        .cloned(),
                );
        }
    }
    let summary = json!({"schema":"framelift-recovery-session-v1","session_sha256":identity,
        "status":if results.len()==plan.windows.len(){"complete"}else{"paused"},
        "completed_windows":results.len(),"total_windows":plan.windows.len(),"unique_frames":frames.len(),
        "frames":frames,"experimental":true,"publication_ready":false});
    if results.len() == plan.windows.len() {
        let path = out.join("summary.json");
        if regular_entry(&path)? {
            if input::read_json(&path)? != summary {
                return Err("committed summary differs from verified windows".into());
            }
        } else {
            input::write_json_new(&path, &summary)?;
        }
    }
    Ok(json!({"summary":summary,"executed_windows":executed,"reused_windows":reused}))
}

#[cfg(test)]
#[path = "tests/recovery_session_tests.rs"]
mod tests;
