//! Linux-supervised, resumable audio research portfolio.
//!
//! Completed tasks are immutable. A killed, uncommitted task may run again;
//! no completed timing hypotheses are deliberately rerun on resume. Waveform
//! conditioning is reused within one invocation when the bounded cache admits
//! a window. It is not persisted across process restarts.
use crate::{adaptive, input, receiver};
use rayon::prelude::*;
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::collections::{BTreeMap, BTreeSet};
use std::fs::{self, File, OpenOptions};
use std::io::{BufWriter, Write};
use std::path::{Path, PathBuf};
use std::sync::{Mutex, OnceLock};
use std::time::{Duration, Instant};

const SCHEMA: &str = "progressive-audio-session-v3";
const TASK_SCHEMA: &str = "progressive-audio-task-v1";
pub const DEFAULT_CACHE_MIB: usize = 2048;
const MAX_CACHE_MIB: usize = 4096;

#[path = "compute_session_audit.rs"]
pub mod compute_audit;
#[path = "progressive_scheduler.rs"]
pub mod scheduler;
#[cfg(target_os = "linux")]
#[path = "progressive_signals.rs"]
mod signals;

fn default_cache_mib() -> usize {
    DEFAULT_CACHE_MIB
}

fn preparation_reservations(bounds: &[(usize, usize)], mut budget: u128) -> Vec<usize> {
    bounds
        .iter()
        .map(|(start, end)| {
            // Two full-length f64 buffers plus timing banks/container metadata.
            // Actual retained capacities are checked before publication as well.
            let reservation = (end - start) as u128 * 16 + 32768;
            if reservation <= budget && reservation <= usize::MAX as u128 {
                budget -= reservation;
                reservation as usize
            } else {
                0
            }
        })
        .collect()
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Options {
    #[serde(default)]
    pub compute: crate::compute::Options,
    pub mode: String,
    pub budget_ms: Option<u64>,
    pub threads: usize,
    /// Invocation-local exact preparation cache; zero disables retention, not work.
    #[serde(default = "default_cache_mib")]
    pub cache_mib: usize,
    #[serde(default)]
    pub scheduler: scheduler::Policy,
    pub baud: f64,
    pub no_blind: bool,
    pub no_multi_anchor: bool,
}
impl Default for Options {
    fn default() -> Self {
        Self {
            compute: crate::compute::Options::default(),
            mode: "quick".into(),
            budget_ms: None,
            threads: 2,
            cache_mib: DEFAULT_CACHE_MIB,
            scheduler: scheduler::Policy::default(),
            baud: 9600.0,
            no_blind: false,
            no_multi_anchor: false,
        }
    }
}
impl Options {
    fn budget(&self) -> Result<Option<Duration>, String> {
        self.compute.validate()?;
        if !(1..=16).contains(&self.threads) || !self.baud.is_finite() || self.baud <= 0.0 {
            return Err("threads must be 1..16 and baud positive finite".into());
        }
        if self.cache_mib > MAX_CACHE_MIB {
            return Err("cache-mib must be in 0..=4096".into());
        }
        let default = match self.mode.as_str() {
            "quick" => Some(3000),
            "deep" => Some(60_000),
            "full" => None,
            _ => return Err("mode must be quick, deep or full".into()),
        };
        let value = self.budget_ms.or(default);
        if value.is_some_and(|v| !(50..=86_400_000).contains(&v)) {
            return Err("budget-ms must be 50..86400000".into());
        }
        Ok(value.map(Duration::from_millis))
    }
    fn policy(&self) -> Value {
        let mut policy = json!({"baud":self.baud,"blind":!self.no_blind,"multi_anchor":!self.no_multi_anchor,
            "window_seconds":6.0,"hop_seconds":3.0,"fast_timings":8,"fast_gardner":2,
            "protocol":"AX25_UI_received_FCS_plain_or_G3RUH","version":2,
            "anchor_generations":"frozen quick prefix for exploratory tasks; frozen complete baseline for final tasks"});
        // Preserve the established fixed-order policy identity. Executable
        // identity still forbids resuming sessions produced by another build.
        if self.scheduler != scheduler::Policy::Fixed {
            policy["scheduler"] = self.scheduler.identity();
        }
        policy
    }
}

#[derive(Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Manifest {
    schema: String,
    #[serde(default)]
    compute_identity: Value,
    audio: crate::progressive_audio::PreparedMetadata,
    executable_sha256: String,
    policy: Value,
}

#[derive(Serialize, Deserialize)]
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

fn hash_json(value: &impl Serialize) -> Result<String, String> {
    Ok(hex::encode(Sha256::digest(
        serde_json::to_vec(value).map_err(|e| e.to_string())?,
    )))
}

fn publish(
    path: &Path,
    value: &impl Serialize,
    scratch: &Path,
    replace: bool,
) -> Result<(), String> {
    let parent = path.parent().ok_or("snapshot requires parent directory")?;
    let mut file = tempfile::NamedTempFile::new_in(scratch).map_err(|e| e.to_string())?;
    {
        let mut writer = BufWriter::with_capacity(64 * 1024, &mut file);
        serde_json::to_writer(&mut writer, value).map_err(|e| e.to_string())?;
        writer.flush().map_err(|e| e.to_string())?;
    }
    file.as_file().sync_all().map_err(|e| e.to_string())?;
    if replace {
        file.persist(path).map_err(|e| e.to_string())?;
    } else {
        file.persist_noclobber(path).map_err(|e| e.to_string())?;
    }
    File::open(parent)
        .and_then(|f| f.sync_all())
        .map_err(|e| e.to_string())
}

fn read_typed<T: for<'de> Deserialize<'de>>(path: &Path) -> Result<T, String> {
    serde_json::from_value(input::read_json(path)?).map_err(|e| format!("{}: {e}", path.display()))
}

fn prepare(
    source: &Path,
    out: &Path,
    options: &Options,
    scratch: &Path,
) -> Result<(Manifest, crate::progressive_audio::PreparedAudio), String> {
    let path = out.join("manifest.json");
    let executable = input::identity(&std::env::current_exe().map_err(|e| e.to_string())?)?;
    let previous = if path.exists() {
        Some(read_typed::<Manifest>(&path)?)
    } else {
        None
    };
    if previous.as_ref().is_some_and(|m| {
        m.schema != SCHEMA
            || m.policy != options.policy()
            || m.executable_sha256 != executable.sha256
            || m.compute_identity != crate::compute::current().identity()
    }) {
        return Err(
            "resume refused: algorithm executable, search policy or compute identity changed"
                .into(),
        );
    }
    let audio = crate::progressive_audio::PreparedAudio::open(
        source,
        out,
        scratch,
        previous.as_ref().map(|m| &m.audio),
    )?;
    let manifest = if let Some(m) = previous {
        m
    } else {
        let m = Manifest {
            schema: SCHEMA.into(),
            compute_identity: crate::compute::current().identity(),
            audio: audio.metadata.clone(),
            executable_sha256: executable.sha256,
            policy: options.policy(),
        };
        publish(&path, &m, scratch, false)?;
        m
    };
    Ok((manifest, audio))
}

/// Only phase boundaries constrain dependency order. Within exploration we
/// interleave cheap transfer, blind start and baseline completion. Early tasks
/// always use the immutable quick-generation anchors, including after resume.
fn phases(options: &Options) -> Vec<Vec<&'static str>> {
    let mut explore = vec!["early-nearest", "baseline-remainder"];
    if !options.no_blind {
        explore.push("blind");
    }
    if !options.no_multi_anchor {
        explore.push("early-multi");
    }
    let mut final_tasks = vec!["nearest"];
    if !options.no_multi_anchor {
        final_tasks.push("multi-anchor");
    }
    vec![vec!["quick"], explore, final_tasks]
}
fn task_path(out: &Path, stage: &str, window: usize) -> PathBuf {
    out.join("tasks").join(format!("{stage}-{window:06}.json"))
}

#[derive(Default)]
struct State {
    frames: BTreeSet<String>,
    stage_frames: BTreeMap<String, BTreeSet<String>>,
    done: BTreeSet<(String, usize)>,
    anchors: Vec<adaptive::AnchorModel>,
    quick_anchors: Vec<adaptive::AnchorModel>,
    evidence: BTreeMap<scheduler::Key, scheduler::Evidence>,
    skipped: BTreeSet<scheduler::Key>,
}
impl State {
    fn add(&mut self, task: &TaskRecord) -> Result<(), String> {
        if self.done.contains(&(task.stage.clone(), task.window)) {
            return Err("duplicate committed task".into());
        }
        for frame in &task.frame_with_fcs_hex {
            let bytes = hex::decode(frame).map_err(|e| e.to_string())?;
            if !crate::protocol::valid_ax25_fcs(&bytes)
                || bytes.len() < 2
                || crate::protocol::parse_ax25_ui(&bytes[..bytes.len() - 2]).is_none()
            {
                return Err("checkpoint contains an invalid received frame".into());
            }
        }
        if task.stage == "quick" || task.stage == "baseline-remainder" {
            let baseline: adaptive::ProgressiveBaseline =
                serde_json::from_value(task.detail.clone()).map_err(|e| e.to_string())?;
            let actual: BTreeSet<String> = baseline
                .trials
                .iter()
                .flat_map(|t| t.frame_with_fcs_hex.iter().cloned())
                .collect();
            if actual != task.frame_with_fcs_hex {
                return Err("task frame provenance mismatch".into());
            }
            if task.stage == "quick" {
                self.quick_anchors.extend(baseline.anchors.iter().cloned());
            }
            self.anchors.extend(baseline.anchors);
        }
        self.frames.extend(task.frame_with_fcs_hex.iter().cloned());
        if !task.elapsed_seconds.is_finite() || task.elapsed_seconds < 0.0 {
            return Err("invalid elapsed time in task checkpoint".into());
        }
        self.evidence.insert(
            scheduler::Key {
                stage: task.stage.clone(),
                window: task.window,
            },
            scheduler::Evidence {
                frames: task.frame_with_fcs_hex.clone(),
                elapsed_seconds: task.elapsed_seconds,
            },
        );
        self.stage_frames
            .entry(task.stage.clone())
            .or_default()
            .extend(task.frame_with_fcs_hex.iter().cloned());
        self.done.insert((task.stage.clone(), task.window));
        Ok(())
    }
    fn snapshot(&self, session: &str, total: usize, windows: usize) -> Value {
        let mut counts = BTreeMap::<String, usize>::new();
        for (stage, _) in &self.done {
            *counts.entry(stage.clone()).or_default() += 1;
        }
        let mut skipped_counts = BTreeMap::<String, usize>::new();
        for key in &self.skipped {
            *skipped_counts.entry(key.stage.clone()).or_default() += 1;
        }
        json!({"schema":"progressive-audio-result-v1","session_sha256":session,
            "complete":self.done.len()+self.skipped.len()==total,
            "status":if self.done.len()+self.skipped.len()==total {"complete"}else{"partial"},
            "completed_tasks":self.done.len(),"total_tasks":total,"window_count":windows,
            "stage_counts":counts,"skipped_tasks":self.skipped.len(),
            "skipped_stage_counts":skipped_counts,
            "stage_frame_with_fcs_hex":self.stage_frames,
            "frame_with_fcs_hex":self.frames,"union_count":self.frames.len(),
            "publication_ready":false,"deployment_ready":false,"bit_repair_enabled":false,
            "frame_validation":"received CRC16/X25 plus AX25 UI structure",
            "input_validation_scope":"whole-file content hash and container header; finite samples checked in processed windows",
            "anchor_generation_policy":"early tasks use frozen quick anchors; final tasks use frozen complete baseline anchors",
            "checkpoint_note":"immutable task files are authoritative; snapshot may lag one commit after interruption",
            "scope":"mono FM-demodulated audio; not universal IQ/protocol support"})
    }
}

/// Invoked only by our CLI supervisor while it holds the session lock.
#[cfg(target_os = "linux")]
pub fn worker(
    source: &Path,
    out: &Path,
    options: &Options,
    lock_fd: i32,
    invocation: &Path,
    scratch: &Path,
) -> Result<Value, String> {
    use std::os::unix::fs::MetadataExt;
    options.budget()?;
    // Borrow the inherited descriptor without assuming ownership. Verify that
    // it is precisely the session lock, not a caller-supplied arbitrary handle.
    let mut stat: libc::stat = unsafe { std::mem::zeroed() };
    let expected = fs::metadata(out.join("session.lock")).map_err(|e| e.to_string())?;
    if lock_fd < 0
        || unsafe { libc::fstat(lock_fd, &mut stat) } != 0
        || stat.st_dev != expected.dev()
        || stat.st_ino != expected.ino()
        || unsafe { libc::flock(lock_fd, libc::LOCK_EX | libc::LOCK_NB) } != 0
    {
        return Err("worker requires inherited owned session lock".into());
    }
    if unsafe { libc::fcntl(lock_fd, libc::F_SETFD, libc::FD_CLOEXEC) } != 0 {
        return Err(std::io::Error::last_os_error().to_string());
    }
    crate::compute::initialize(&options.compute)?;
    input::write_json_new(
        &invocation.join("compute-start.json"),
        &crate::compute::current().report(),
    )?;
    let (manifest, audio) = prepare(source, out, options, scratch)?;
    let session = hash_json(&manifest)?;
    let bounds =
        receiver::window_bounds(manifest.audio.samples, manifest.audio.sample_rate, 6.0, 3.0)?;
    let ordered_phases = phases(options);
    let total = bounds.len() * ordered_phases.iter().map(Vec::len).sum::<usize>();
    fs::create_dir_all(out.join("tasks")).map_err(|e| e.to_string())?;
    let mut state = State::default();
    let mut preceding_complete = true;
    for phase in &ordered_phases {
        let mut stage_complete = true;
        for stage in phase {
            for index in 0..bounds.len() {
                let path = task_path(out, stage, index);
                if path.exists() {
                    if !preceding_complete && options.scheduler == scheduler::Policy::Fixed {
                        return Err(
                            "checkpoint has tasks before dependency stages completed".into()
                        );
                    }
                    let commit: TaskCommit = read_typed(&path)?;
                    if hash_json(&commit.task)? != commit.sha256 {
                        return Err("task checkpoint checksum mismatch".into());
                    }
                    let task = commit.task;
                    if task.schema != TASK_SCHEMA
                        || task.session_sha256 != session
                        || task.stage != *stage
                        || task.window != index
                    {
                        return Err("incompatible task checkpoint".into());
                    }
                    state.add(&task)?;
                } else {
                    stage_complete = false;
                }
            }
        }
        preceding_complete &= stage_complete;
    }
    let schedule_dir = out.join("schedule");
    if options.scheduler != scheduler::Policy::Fixed {
        fs::create_dir_all(&schedule_dir).map_err(|e| e.to_string())?;
        let mut paths = fs::read_dir(&schedule_dir)
            .map_err(|e| e.to_string())?
            .map(|e| e.map(|e| e.path()).map_err(|e| e.to_string()))
            .collect::<Result<Vec<_>, _>>()?;
        paths.sort();
        let mut decisions = Vec::new();
        for (serial, path) in paths.iter().enumerate() {
            if *path != schedule_dir.join(format!("batch-{serial:09}.json")) {
                return Err("unexpected scheduler journal entry or sequence gap".into());
            }
            decisions.push(read_typed::<scheduler::Decision>(path)?);
        }
        state.skipped = scheduler::audit(
            &session,
            options.scheduler,
            &ordered_phases,
            bounds.len(),
            &decisions,
            &state.evidence,
        )?;
    }
    publish(
        &out.join("result.json"),
        &state.snapshot(&session, total, bounds.len()),
        scratch,
        true,
    )?;
    // A deadline during verification must never label an old snapshot current.
    publish(
        &invocation.join("validated.json"),
        &json!({"session_sha256":session}),
        scratch,
        false,
    )?;
    let state = Mutex::new(state);
    let largest = bounds
        .iter()
        .map(|(start, end)| end - start)
        .max()
        .unwrap_or(0);
    let working_estimate = largest as u128 * 8 * 33 * options.threads as u128;
    let memory_estimate_limit = 6_u128 * 1024 * 1024 * 1024;
    if working_estimate > memory_estimate_limit {
        return Err("progressive working-set estimate exceeds 6 GiB; lower threads".into());
    }
    let pool = rayon::ThreadPoolBuilder::new()
        .num_threads(options.threads)
        .build()
        .map_err(|e| e.to_string())?;
    let config = adaptive::AdaptiveConfig {
        baud: options.baud,
        threads: options.threads,
    };
    let reservations = preparation_reservations(
        &bounds,
        (options.cache_mib as u128 * 1024 * 1024).min(memory_estimate_limit - working_estimate),
    );
    // Each cell belongs to exactly one source window in this invocation. No
    // cross-window lock is held during DSP, and exhausted cache never drops work.
    let prepared_windows: Vec<OnceLock<Result<Option<adaptive::PreparedWindow>, String>>> =
        (0..bounds.len()).map(|_| OnceLock::new()).collect();
    let mut scheduler_engine =
        scheduler::Engine::new(&session, options.scheduler, &ordered_phases, bounds.len());
    for (phase_index, phase) in ordered_phases.into_iter().enumerate() {
        let anchors = {
            let state = state.lock().map_err(|e| e.to_string())?;
            adaptive::progressive_merge_anchors(if phase_index == 1 {
                &state.quick_anchors
            } else {
                &state.anchors
            })
        };
        let pending: Vec<(&str, usize)> = {
            let state = state.lock().map_err(|e| e.to_string())?;
            (0..bounds.len())
                .flat_map(|i| phase.iter().map(move |&stage| (stage, i)))
                .filter(|(stage, i)| !state.done.contains(&(stage.to_string(), *i)))
                .collect()
        };
        let execute_batch = |pending: &[(&str, usize)]| -> Result<(), String> {
            pool.install(|| {
                pending
                    .par_iter()
                    .try_for_each(|&(stage, index)| -> Result<(), String> {
                        let started = Instant::now();
                        let samples = audio.get_window(bounds[index].0, bounds[index].1)?;
                        let prepared = if reservations[index] == 0 {
                            None
                        } else {
                            prepared_windows[index]
                                .get_or_init(|| {
                                    let window = adaptive::PreparedWindow::new(
                                        &samples,
                                        manifest.audio.sample_rate,
                                        &config,
                                    )?;
                                    Ok((window.retained_bytes() <= reservations[index])
                                        .then_some(window))
                                })
                                .as_ref()
                                .map_err(Clone::clone)?
                                .as_ref()
                        };
                        let (detail, frames) = match stage {
                            "quick" | "baseline-remainder" => {
                                let r = adaptive::progressive_baseline_local_prepared(
                                    &samples,
                                    manifest.audio.sample_rate,
                                    index,
                                    bounds[index],
                                    &config,
                                    stage == "quick",
                                    prepared,
                                )?;
                                let f = r
                                    .trials
                                    .iter()
                                    .flat_map(|t| t.frame_with_fcs_hex.iter().cloned())
                                    .collect();
                                (serde_json::to_value(r).map_err(|e| e.to_string())?, f)
                            }
                            "nearest" | "early-nearest" => {
                                let r = adaptive::progressive_supplemental_local_prepared(
                                    &samples,
                                    manifest.audio.sample_rate,
                                    index,
                                    bounds[index],
                                    &config,
                                    &anchors,
                                    prepared,
                                )?;
                                let f = r
                                    .iter()
                                    .flat_map(|t| t.frame_with_fcs_hex.iter().cloned())
                                    .collect();
                                (serde_json::to_value(r).map_err(|e| e.to_string())?, f)
                            }
                            "blind" | "multi-anchor" | "early-multi" => {
                                let r = adaptive::progressive_new_channel_local_prepared(
                                    &samples,
                                    manifest.audio.sample_rate,
                                    index,
                                    bounds[index],
                                    &config,
                                    &anchors,
                                    stage == "blind",
                                    stage != "blind",
                                    prepared,
                                )?;
                                let f = r
                                    .trials
                                    .iter()
                                    .flat_map(|t| t.frame_with_fcs_hex.iter().cloned())
                                    .collect();
                                (serde_json::to_value(r).map_err(|e| e.to_string())?, f)
                            }
                            _ => unreachable!(),
                        };
                        let task = TaskRecord {
                            schema: TASK_SCHEMA.into(),
                            session_sha256: session.clone(),
                            stage: stage.into(),
                            window: index,
                            elapsed_seconds: started.elapsed().as_secs_f64(),
                            frame_with_fcs_hex: frames,
                            detail,
                        };
                        let commit = TaskCommit {
                            sha256: hash_json(&task)?,
                            task,
                        };
                        publish(&task_path(out, stage, index), &commit, scratch, false)?;
                        let mut state = state.lock().map_err(|e| e.to_string())?;
                        state.add(&commit.task)?;
                        publish(
                            &out.join("result.json"),
                            &state.snapshot(&session, total, bounds.len()),
                            scratch,
                            true,
                        )
                    })
            })
        };
        if options.scheduler == scheduler::Policy::Fixed {
            execute_batch(&pending)?;
        } else {
            while scheduler_engine.phase == phase_index {
                let Some(decision) = scheduler_engine.next() else {
                    break;
                };
                let path = schedule_dir.join(format!("batch-{:09}.json", decision.serial));
                if path.exists() {
                    if read_typed::<scheduler::Decision>(&path)? != decision {
                        return Err("scheduler decision differs on resume".into());
                    }
                } else {
                    publish(&path, &decision, scratch, false)?;
                }
                let batch: Vec<_> = {
                    let state = state.lock().map_err(|e| e.to_string())?;
                    decision
                        .tasks
                        .iter()
                        .filter(|key| !state.evidence.contains_key(*key))
                        .map(|key| (key.stage.as_str(), key.window))
                        .collect()
                };
                execute_batch(&batch)?;
                scheduler_engine.commit(
                    &decision,
                    &state.lock().map_err(|e| e.to_string())?.evidence,
                )?;
                let mut state = state.lock().map_err(|e| e.to_string())?;
                state.skipped = scheduler_engine.skipped().clone();
                publish(
                    &out.join("result.json"),
                    &state.snapshot(&session, total, bounds.len()),
                    scratch,
                    true,
                )?;
            }
        }
    }
    input::write_json_new(
        &invocation.join("compute-finish.json"),
        &crate::compute::current().report(),
    )?;
    Ok(json!({"status":"complete","result":out.join("result.json")}))
}

#[cfg(target_os = "linux")]
fn cpu_seconds(who: libc::c_int) -> f64 {
    let mut usage: libc::rusage = unsafe { std::mem::zeroed() };
    if unsafe { libc::getrusage(who, &mut usage) } != 0 {
        return f64::NAN;
    }
    usage.ru_utime.tv_sec as f64
        + usage.ru_utime.tv_usec as f64 / 1e6
        + usage.ru_stime.tv_sec as f64
        + usage.ru_stime.tv_usec as f64 / 1e6
}

/// Budget covers setup, input conversion, verification and decoding. Linux
/// scheduling, process teardown and final receipt I/O are measured overhead,
/// not a real-time guarantee of return at exactly 3.000 seconds.
#[cfg(target_os = "linux")]
pub fn supervise(
    source: &Path,
    output: &Path,
    options: &Options,
    resume: bool,
) -> Result<Value, String> {
    use std::os::fd::AsRawFd;
    use std::os::unix::{fs::OpenOptionsExt, process::CommandExt};
    use std::process::{Command, Stdio};
    let start = Instant::now();
    let budget = options.budget()?;
    let signals = signals::Signals::install()?;
    let cpu_start = cpu_seconds(libc::RUSAGE_CHILDREN) + cpu_seconds(libc::RUSAGE_SELF);
    if resume {
        if !fs::symlink_metadata(output)
            .map_err(|e| e.to_string())?
            .file_type()
            .is_dir()
        {
            return Err("resume requires an existing real session directory".into());
        }
    } else {
        fs::create_dir(output)
            .map_err(|e| format!("new session requires unused output path: {e}"))?;
    }
    let out = output.canonicalize().map_err(|e| e.to_string())?;
    let lock = OpenOptions::new()
        .read(true)
        .write(true)
        .create(true)
        .truncate(false)
        .custom_flags(libc::O_NOFOLLOW | libc::O_NONBLOCK)
        .open(out.join("session.lock"))
        .map_err(|e| e.to_string())?;
    if !lock.metadata().map_err(|e| e.to_string())?.is_file()
        || unsafe { libc::flock(lock.as_raw_fd(), libc::LOCK_EX | libc::LOCK_NB) } != 0
    {
        return Err("session is already running or lock is not a regular file".into());
    }
    fs::create_dir_all(out.join("runs")).map_err(|e| e.to_string())?;
    let run = tempfile::Builder::new()
        .prefix("invocation-")
        .tempdir_in(out.join("runs"))
        .map_err(|e| e.to_string())?
        .keep();
    input::write_json_new(&run.join("options.json"), options)?;
    // Supervisor-owned lifetime: SIGKILL of the worker cannot leak conversion
    // WAVs or half-written f64/checkpoint files between repeated quick runs.
    let scratch = tempfile::Builder::new()
        .prefix("scratch-")
        .tempdir_in(&run)
        .map_err(|e| e.to_string())?;
    let log = File::create(run.join("worker.stderr")).map_err(|e| e.to_string())?;
    let mut command = Command::new(std::env::current_exe().map_err(|e| e.to_string())?);
    let fd = lock.as_raw_fd();
    command
        .arg("progressive-worker")
        .arg("--input")
        .arg(source)
        .arg("--output")
        .arg(&out)
        .arg("--options")
        .arg(run.join("options.json"))
        .arg("--scratch")
        .arg(scratch.path())
        .arg("--lock-fd")
        .arg(fd.to_string())
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(log);
    let parent = unsafe { libc::getpid() };
    unsafe {
        command.pre_exec(move || {
            if libc::setpgid(0, 0) != 0
                || libc::prctl(libc::PR_SET_PDEATHSIG, libc::SIGKILL) != 0
                || libc::fcntl(fd, libc::F_SETFD, 0) != 0
            {
                return Err(std::io::Error::last_os_error());
            }
            if libc::getppid() != parent {
                return Err(std::io::Error::from_raw_os_error(libc::ESRCH));
            }
            Ok(())
        });
    }
    let child = command.spawn().map_err(|e| e.to_string())?;
    let mut group = input::OwnedProcessGroup::new(child)?;
    let mut expired = false;
    let mut stop_signal = None;
    loop {
        if group.exited_without_reaping()? {
            break;
        }
        if let Some(signal) = signals.requested() {
            stop_signal = Some(signal);
            break;
        }
        // Reserve a small margin for process teardown and final receipt.
        if budget.is_some_and(|b| start.elapsed() >= b.saturating_sub(Duration::from_millis(40))) {
            expired = true;
            break;
        }
        std::thread::sleep(Duration::from_millis(5));
    }
    let status = group.cleanup_and_wait()?;
    scratch.close().map_err(|e| e.to_string())?;
    let validated = run.join("validated.json").exists();
    let snapshot = if validated && out.join("result.json").exists() {
        Some(input::read_json(&out.join("result.json"))?)
    } else {
        None
    };
    let successful = expired || stop_signal.is_some() || status.success();
    let error = if successful {
        None
    } else {
        Some(
            String::from_utf8_lossy(&input::read_bytes_bounded(
                &run.join("worker.stderr"),
                1024 * 1024,
            )?)
            .into_owned(),
        )
    };
    let receipt = json!({"schema":"progressive-audio-invocation-v1","mode":options.mode,
        "cancelled":stop_signal.is_some(),"stop_signal":stop_signal,
        "stop_reason":if stop_signal.is_some() {"signal"} else if expired {"deadline"} else if status.success() {"completed"} else {"worker_error"},
        "compute_options":options.compute,
        "compute_start":if run.join("compute-start.json").exists() {Some(input::read_json(&run.join("compute-start.json"))?)}else{None},
        "compute_finish":if run.join("compute-finish.json").exists() {Some(input::read_json(&run.join("compute-finish.json"))?)}else{None},
        "budget_ms":budget.map(|b| b.as_millis()),"budget_exhausted":expired,"worker_success":status.success(),
        "invocation_wall_seconds":start.elapsed().as_secs_f64(),
        "invocation_cpu_seconds":cpu_seconds(libc::RUSAGE_CHILDREN)+cpu_seconds(libc::RUSAGE_SELF)-cpu_start,
        "timing_scope":"supervisor and waited descendants through cleanup/snapshot read; final receipt write and CLI output excluded; use outer timer for end-to-end latency",
        "complete":successful&&snapshot.as_ref().is_some_and(|s|s["complete"]==true),
        "completed_tasks":snapshot.as_ref().map(|s|&s["completed_tasks"]),
        "union_count":snapshot.as_ref().map(|s|&s["union_count"]),
        "result":if validated {Some(out.join("result.json"))}else{None},
        "input_and_checkpoints_verified":validated,
        "preparation_complete":out.join("manifest.json").exists(),
        "error":error,"publication_ready":false,"deployment_ready":false});
    input::write_json_new(&run.join("receipt.json"), &receipt)?;
    if let Some(error) = error {
        return Err(format!(
            "progressive worker failed: {error}; receipt {}",
            run.join("receipt.json").display()
        ));
    }
    Ok(receipt)
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn preparation_budget_never_becomes_a_search_budget() {
        let bounds = [(0, 8192), (4096, 12288), (8192, 20000)];
        assert_eq!(preparation_reservations(&bounds, 0), vec![0, 0, 0]);
        let one = 8192 * 16 + 32768;
        assert_eq!(
            preparation_reservations(&bounds, one as u128),
            vec![one, 0, 0]
        );
        let budget = DEFAULT_CACHE_MIB as u128 * 1024 * 1024;
        let all = preparation_reservations(&bounds, budget);
        assert!(all.iter().all(|n| *n > 0));
        assert!(all.iter().sum::<usize>() as u128 <= budget);
    }

    #[test]
    fn cache_budget_is_not_a_decoder_policy_and_old_options_still_load() {
        let original = Options::default();
        assert_eq!(original.cache_mib, 2048);
        for cache_mib in [0, 512, 2048, MAX_CACHE_MIB] {
            let options = Options {
                cache_mib,
                ..original.clone()
            };
            assert!(options.budget().is_ok());
            assert_eq!(options.policy(), original.policy());
            let roundtrip: Options =
                serde_json::from_value(serde_json::to_value(&options).unwrap()).unwrap();
            assert_eq!(roundtrip.cache_mib, cache_mib);
        }
        assert!(
            Options {
                cache_mib: MAX_CACHE_MIB + 1,
                ..original.clone()
            }
            .budget()
            .is_err()
        );
        let mut legacy = serde_json::to_value(&original).unwrap();
        legacy.as_object_mut().unwrap().remove("cache_mib");
        assert_eq!(
            serde_json::from_value::<Options>(legacy).unwrap().cache_mib,
            DEFAULT_CACHE_MIB
        );
        // A ten-minute recording exceeds the old cache but fits the new default.
        let bounds: Vec<_> = (0..199)
            .map(|i| (i * 144000, i * 144000 + 288000))
            .collect();
        assert!(preparation_reservations(&bounds, 512 * 1024 * 1024).contains(&0));
        assert!(
            !preparation_reservations(&bounds, DEFAULT_CACHE_MIB as u128 * 1024 * 1024)
                .contains(&0)
        );
    }

    #[test]
    fn buffered_publish_keeps_bytes_and_never_replaces_a_commit() {
        let root = tempfile::tempdir().unwrap();
        let target = root.path().join("task.json");
        let value = json!({"unicode":"zażółć", "large":vec![0.123456789f64;32768], "zero":-0.0});
        publish(&target, &value, root.path(), false).unwrap();
        assert_eq!(
            std::fs::read(&target).unwrap(),
            serde_json::to_vec(&value).unwrap()
        );
        assert!(publish(&target, &json!({"changed":true}), root.path(), false).is_err());
        assert_eq!(
            std::fs::read(&target).unwrap(),
            serde_json::to_vec(&value).unwrap()
        );
        publish(&target, &json!({"complete":true}), root.path(), true).unwrap();
        assert_eq!(input::read_json(&target).unwrap(), json!({"complete":true}));
    }

    #[test]
    fn buffered_publish_serialization_failure_cannot_replace_a_snapshot() {
        struct FailsAfterWrite;
        impl Serialize for FailsAfterWrite {
            fn serialize<S: serde::Serializer>(&self, serializer: S) -> Result<S::Ok, S::Error> {
                use serde::ser::{Error, SerializeSeq};
                let mut seq = serializer.serialize_seq(None)?;
                for _ in 0..10000 {
                    seq.serialize_element("partial data")?;
                }
                Err(S::Error::custom("intentional serialization failure"))
            }
        }
        let root = tempfile::tempdir().unwrap();
        let target = root.path().join("result.json");
        publish(&target, &json!({"old":true}), root.path(), false).unwrap();
        assert!(publish(&target, &FailsAfterWrite, root.path(), true).is_err());
        assert_eq!(input::read_json(&target).unwrap(), json!({"old":true}));
        let new = root.path().join("new.json");
        assert!(publish(&new, &FailsAfterWrite, root.path(), false).is_err());
        assert!(!new.exists());
    }
    #[test]
    fn budgets_and_policy_identity_are_separate() {
        let a = Options::default();
        assert_eq!(a.budget().unwrap(), Some(Duration::from_secs(3)));
        let b = Options {
            mode: "deep".into(),
            threads: 4,
            ..a.clone()
        };
        assert_eq!(b.budget().unwrap(), Some(Duration::from_secs(60)));
        assert_eq!(a.policy(), b.policy());
        assert_ne!(
            a.policy(),
            Options {
                no_blind: true,
                ..a.clone()
            }
            .policy()
        );
        assert!(
            Options {
                budget_ms: Some(0),
                ..a
            }
            .budget()
            .is_err()
        );
    }
    #[test]
    fn corrupt_frame_checkpoint_is_rejected() {
        let mut state = State::default();
        let t = TaskRecord {
            schema: TASK_SCHEMA.into(),
            session_sha256: "x".into(),
            stage: "nearest".into(),
            window: 0,
            elapsed_seconds: 0.,
            frame_with_fcs_hex: BTreeSet::from(["1234".into()]),
            detail: json!([]),
        };
        assert!(state.add(&t).is_err());
        assert!(state.done.is_empty());
    }
}
