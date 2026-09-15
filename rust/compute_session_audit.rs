//! Read-only, fail-closed same-input/same-executable progressive backend audit.
//! No DSP runs here. A same-count/different-frame result is never parity.
use super::*;

struct CheckedSession {
    _lock: File,
    manifest: Manifest,
    frames: BTreeSet<String>,
    tasks: BTreeMap<(String, usize), Value>,
    evidence: Value,
}

fn without_elapsed(value: &mut Value) {
    match value {
        Value::Object(map) => {
            map.remove("elapsed_seconds");
            for v in map.values_mut() {
                without_elapsed(v);
            }
        }
        Value::Array(values) => {
            for v in values {
                without_elapsed(v);
            }
        }
        _ => {}
    }
}

#[cfg(target_os = "linux")]
fn check(root: &Path) -> Result<CheckedSession, String> {
    use std::os::fd::AsRawFd;
    use std::os::unix::fs::OpenOptionsExt;
    let root = root.canonicalize().map_err(|e| e.to_string())?;
    let lock = OpenOptions::new()
        .read(true)
        .custom_flags(libc::O_NOFOLLOW | libc::O_NONBLOCK)
        .open(root.join("session.lock"))
        .map_err(|e| e.to_string())?;
    if !lock.metadata().map_err(|e| e.to_string())?.is_file()
        || unsafe { libc::flock(lock.as_raw_fd(), libc::LOCK_SH | libc::LOCK_NB) } != 0
    {
        return Err("cannot audit a running session or invalid lock".into());
    }
    let manifest: Manifest = read_typed(&root.join("manifest.json"))?;
    if manifest.schema != SCHEMA
        || manifest.compute_identity["schema"] != "telemetry-compute-v1"
        || ![Some("cpu"), Some("cuda")].contains(&manifest.compute_identity["backend"].as_str())
    {
        return Err("compute audit requires a v3 session with explicit backend identity".into());
    }
    for identity in [&manifest.audio.source, &manifest.audio.wav] {
        let actual = input::identity(Path::new(&identity.path))?;
        if actual.sha256 != identity.sha256 || actual.bytes != identity.bytes {
            return Err("session input bytes changed".into());
        }
    }
    let session = hash_json(&manifest)?;
    let options = Options {
        baud: manifest.policy["baud"].as_f64().ok_or("missing baud")?,
        no_blind: !manifest.policy["blind"]
            .as_bool()
            .ok_or("missing blind policy")?,
        no_multi_anchor: !manifest.policy["multi_anchor"]
            .as_bool()
            .ok_or("missing multi-anchor policy")?,
        ..Default::default()
    };
    if manifest.policy != options.policy() {
        return Err("unknown decoder policy".into());
    }
    let bounds =
        receiver::window_bounds(manifest.audio.samples, manifest.audio.sample_rate, 6.0, 3.0)?;
    if bounds.is_empty() {
        return Err("empty session cannot prove parity".into());
    }
    let stages: Vec<_> = phases(&options).into_iter().flatten().collect();
    let expected = stages.len() * bounds.len();
    let tasks_dir = root.join("tasks");
    if !fs::symlink_metadata(&tasks_dir)
        .map_err(|e| e.to_string())?
        .file_type()
        .is_dir()
    {
        return Err("task directory must not be a symlink".into());
    }
    if fs::read_dir(&tasks_dir).map_err(|e| e.to_string())?.count() != expected {
        return Err("missing or unexpected task files".into());
    }
    let mut state = State::default();
    let mut tasks = BTreeMap::new();
    let mut artifact_hashes = Vec::new();
    for stage in stages {
        for window in 0..bounds.len() {
            let path = task_path(&root, stage, window);
            let record: TaskCommit = read_typed(&path)?;
            if record.sha256 != hash_json(&record.task)?
                || record.task.schema != TASK_SCHEMA
                || record.task.session_sha256 != session
                || record.task.stage != stage
                || record.task.window != window
            {
                return Err("task hash, session binding or geometry mismatch".into());
            }
            for frame in &record.task.frame_with_fcs_hex {
                if !crate::audit::valid_ax25_ui_fcs(&hex::decode(frame).map_err(|e| e.to_string())?)
                {
                    return Err("independent received-FCS/UI validation failed".into());
                }
            }
            state.add(&record.task)?;
            let mut value = serde_json::to_value(&record.task).map_err(|e| e.to_string())?;
            value.as_object_mut().unwrap().remove("session_sha256");
            without_elapsed(&mut value);
            tasks.insert((stage.to_string(), window), value);
            artifact_hashes
                .push(json!({"stage":stage,"window":window,"task_sha256":record.sha256}));
        }
    }
    let snapshot = input::read_json(&root.join("result.json"))?;
    if snapshot != state.snapshot(&session, expected, bounds.len()) {
        return Err(
            "snapshot is partial, stale, tampered or inconsistent with committed tasks".into(),
        );
    }
    Ok(CheckedSession {
        _lock: lock,
        frames: state.frames,
        tasks,
        evidence: json!({"root":root,"manifest_sha256":session,"executable_sha256":manifest.executable_sha256,
            "compute_identity":manifest.compute_identity,"task_commitments":artifact_hashes,
            "result":input::identity(&root.join("result.json"))?}),
        manifest,
    })
}

fn same_input(a: &Manifest, b: &Manifest) -> bool {
    a.audio.source.sha256 == b.audio.source.sha256
        && a.audio.source.bytes == b.audio.source.bytes
        && a.audio.wav.sha256 == b.audio.wav.sha256
        && a.audio.wav.bytes == b.audio.wav.bytes
        && a.audio.sample_rate == b.audio.sample_rate
        && a.audio.samples == b.audio.samples
}

#[cfg(target_os = "linux")]
pub fn run(cpu: &Path, candidate: &Path, out: &Path) -> Result<Value, String> {
    if out.exists() {
        return Err("audit output must be a new file".into());
    }
    // Even invalid/missing sessions produce an explicit failed receipt, not a
    // successful empty-set comparison. The report never certifies independence.
    let checked = (|| -> Result<Value, String> {
        if cpu.canonicalize().map_err(|e| e.to_string())?
            == candidate.canonicalize().map_err(|e| e.to_string())?
        {
            return Err("two distinct sessions are required".into());
        }
        let a = check(cpu)?;
        let b = check(candidate)?;
        if a.manifest.compute_identity["backend"] != "cpu" {
            return Err("reference session must use CPU".into());
        }
        if !same_input(&a.manifest, &b.manifest)
            || a.manifest.policy != b.manifest.policy
            || a.manifest.executable_sha256 != b.manifest.executable_sha256
        {
            return Err("same source, numeric input, policy and executable are required".into());
        }
        let missing: Vec<_> = a.frames.difference(&b.frames).collect();
        let added: Vec<_> = b.frames.difference(&a.frames).collect();
        let different: Vec<_> = a
            .tasks
            .iter()
            .filter(|(k, v)| b.tasks.get(*k) != Some(*v))
            .map(|(k, _)| k)
            .collect();
        let pass = missing.is_empty()
            && added.is_empty()
            && different.is_empty()
            && a.tasks.len() == b.tasks.len();
        Ok(json!({"pass":pass,"cpu":a.evidence,"candidate":b.evidence,
            "complete_tasks_per_session":a.tasks.len(),"cpu_unique_frames":a.frames.len(),"candidate_unique_frames":b.frames.len(),
            "lost_full_frames":missing,"added_full_frames":added,"different_scientific_tasks":different,
            "normalization":"only session_sha256 and exact elapsed_seconds keys; every trial, model, rank and frame remains compared"}))
    })();
    let (pass, detail) = match checked {
        Ok(v) => (v["pass"] == true, v),
        Err(e) => (false, json!({"error":e})),
    };
    let report = json!({"schema":"telemetry-compute-session-parity-v1","pass":pass,"status":if pass {"passed"} else {"failed"},
        "detail":detail,"publication_ready":false,"deployment_ready":false,
        "scope":"local artifact consistency and exact same-build progressive receiver parity; not independent reproduction, origin authentication, unseen-cohort evaluation or timing qualification"});
    input::write_json_new(out, &report)?;
    if !pass {
        return Err(format!(
            "compute session audit failed; evidence {}",
            out.display()
        ));
    }
    Ok(report)
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn normalization_does_not_hide_changed_decisions_or_model_values() {
        let mut value = json!({"elapsed_seconds":1,"frame_with_fcs_hex":["01"],"detail":{"elapsed_seconds":2,"gain":0.75,"rank":7}});
        without_elapsed(&mut value);
        assert_eq!(
            value,
            json!({"frame_with_fcs_hex":["01"],"detail":{"gain":0.75,"rank":7}})
        );
    }
    #[test]
    fn missing_sessions_produce_failure_not_vacuous_parity() {
        let tmp = tempfile::tempdir().unwrap();
        let out = tmp.path().join("audit.json");
        assert!(
            run(
                &tmp.path().join("missing-a"),
                &tmp.path().join("missing-b"),
                &out
            )
            .is_err()
        );
        let report = input::read_json(&out).unwrap();
        assert_eq!(report["pass"], false);
        assert_eq!(report["publication_ready"], false);
    }
}
