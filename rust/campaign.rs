//! Durable, local-only replays of a committed archive cohort.
//!
//! No reference PDU is passed to the decoder. A selected work item contains
//! only IDs, paths, source/PCM identities and committed metadata identities.
//! Resume never mixes executables/configurations. Completed artifacts are
//! no-clobber published for the existing flat-directory audit API.
//!
//! SIGINT/SIGTERM request a stop *between* bounded observations. Rust workers
//! cannot safely be forcibly cancelled in-process; codecs have their own
//! owned-process timeouts in `input`. This is not a hard decoder wall timeout.

use crate::{input, receiver};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use std::collections::{BTreeMap, BTreeSet};
use std::fs::{self, File, OpenOptions};
use std::io::Read;
#[cfg(test)]
use std::io::Write;
use std::path::{Path, PathBuf};
use std::sync::{
    Mutex,
    atomic::{AtomicBool, Ordering},
};
use std::time::{Instant, SystemTime, UNIX_EPOCH};

const DISK_RESERVE: u64 = 1024 * 1024 * 1024;
const MAX_CAMPAIGN_BYTES: u64 = 3 * 1024 * 1024 * 1024;
const METADATA_HEADROOM: u64 = 32 * 1024 * 1024;
const MAX_ENTRIES: usize = 100_000;
static STOP_REQUESTED: AtomicBool = AtomicBool::new(false);
static PROCESS_CAMPAIGN: Mutex<()> = Mutex::new(());

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CampaignOptions {
    pub threads: usize,
    pub observation_id: Option<u64>,
    pub limit: Option<usize>,
    pub resume: bool,
}

impl Default for CampaignOptions {
    fn default() -> Self {
        Self {
            threads: 1,
            observation_id: None,
            limit: None,
            resume: false,
        }
    }
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct SelectedInput {
    observation_id: u64,
    source: input::Identity,
    expected_pcm: input::Identity,
    reference_artifacts: Vec<input::Identity>,
}

#[derive(Clone, Debug)]
struct Reference {
    summary: input::Identity,
    anchors: Vec<input::Identity>,
    selected: Vec<SelectedInput>,
    frozen_count: u64,
    complete_reference_count: usize,
}

fn require(value: bool, message: impl Into<String>) -> Result<(), String> {
    if value { Ok(()) } else { Err(message.into()) }
}
fn same_identity(a: &input::Identity, b: &input::Identity) -> bool {
    a.path == b.path && a.sha256 == b.sha256 && a.bytes == b.bytes
}
fn identity_value(value: &Value) -> Result<input::Identity, String> {
    let identity: input::Identity =
        serde_json::from_value(value.clone()).map_err(|e| e.to_string())?;
    require(
        identity.sha256.len() == 64
            && identity
                .sha256
                .bytes()
                .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b)),
        "invalid artifact SHA-256",
    )?;
    require(
        Path::new(&identity.path).is_absolute(),
        "artifact identity must use an absolute path",
    )?;
    Ok(identity)
}
fn verify_identity(expected: &input::Identity) -> Result<(), String> {
    require(
        same_identity(&input::identity(Path::new(&expected.path))?, expected),
        format!("artifact identity mismatch: {}", expected.path),
    )
}
fn json_with_identity(path: &Path) -> Result<(Value, input::Identity), String> {
    let before = input::identity(path)?;
    let value = input::read_json(path)?;
    verify_identity(&before)?;
    Ok((value, before))
}
fn canonical_inside(root: &Path, path: &Path) -> Result<PathBuf, String> {
    let canonical = path
        .canonicalize()
        .map_err(|e| format!("{}: {e}", path.display()))?;
    require(
        canonical.starts_with(root),
        format!("artifact escapes its root: {}", path.display()),
    )?;
    Ok(canonical)
}
fn no_symlink(path: &Path) -> Result<(), String> {
    let metadata = fs::symlink_metadata(path).map_err(|e| format!("{}: {e}", path.display()))?;
    require(
        !metadata.file_type().is_symlink(),
        format!(
            "symlink is not an owned campaign artifact: {}",
            path.display()
        ),
    )
}
fn id_field(value: &Value, key: &str) -> Result<u64, String> {
    value[key]
        .as_u64()
        .ok_or_else(|| format!("invalid nonnegative integer {key}"))
}
fn array<'a>(value: &'a Value, key: &str) -> Result<&'a Vec<Value>, String> {
    value[key]
        .as_array()
        .ok_or_else(|| format!("missing array {key}"))
}

fn reference_inputs(path: &Path, options: &CampaignOptions) -> Result<Reference, String> {
    require(options.limit != Some(0), "campaign limit must be positive")?;
    let (summary, summary_identity) = json_with_identity(path)?;
    require(
        summary["schema"] == "ogg-archive-campaign-summary-v1"
            && summary["complete_campaign"] == true,
        "reference must be a frozen terminal campaign summary",
    )?;
    let summary_path = Path::new(&summary_identity.path);
    require(
        summary_path
            .parent()
            .and_then(Path::file_name)
            .and_then(|s| s.to_str())
            == Some("summaries"),
        "reference must be under CAMPAIGN/summaries",
    )?;
    let root = summary_path
        .parent()
        .and_then(Path::parent)
        .ok_or("reference root missing")?;
    let (plan, plan_identity) = json_with_identity(&root.join("plan.json"))?;
    require(
        summary["plan_sha256"] == plan_identity.sha256,
        "frozen reference plan mismatch",
    )?;
    let (cohort, cohort_identity) = json_with_identity(&root.join("cohort.json"))?;
    let (freeze, freeze_identity) = json_with_identity(&root.join("freeze.json"))?;
    require(
        same_identity(&identity_value(&freeze["plan"])?, &plan_identity)
            && same_identity(&identity_value(&freeze["cohort"])?, &cohort_identity),
        "reference freeze identities disagree",
    )?;
    let _ = plan;
    let rows = array(&summary, "observations")?;
    let frozen_count = id_field(&summary, "frozen_count")?;
    require(
        !rows.is_empty() && rows.len() <= 10_000 && rows.len() as u64 == frozen_count,
        "invalid frozen cohort size",
    )?;
    let mut ids = BTreeSet::new();
    for row in rows {
        require(
            ids.insert(id_field(row, "observation_id")?),
            "duplicate observation ID in reference",
        )?;
    }
    let cohort_ids: Vec<u64> =
        serde_json::from_value(cohort["ids"].clone()).map_err(|e| e.to_string())?;
    require(
        cohort_ids.len() == ids.len() && cohort_ids.iter().copied().collect::<BTreeSet<_>>() == ids,
        "reference summary/cohort observation IDs disagree",
    )?;
    require(
        id_field(&cohort, "selected_count")? == frozen_count,
        "reference cohort selected count disagrees",
    )?;
    let complete_reference_count = rows.iter().filter(|r| r["status"] == "complete").count();
    require(
        complete_reference_count > 0
            && id_field(&summary, "complete_comparisons")? as usize == complete_reference_count,
        "reference complete-comparison count disagrees",
    )?;
    let mut selected = Vec::new();
    for row in rows {
        let id = id_field(row, "observation_id")?;
        if row["status"] != "complete" || options.observation_id.is_some_and(|wanted| wanted != id)
        {
            continue;
        }
        require(
            row["native_status"] == "complete"
                && row["baseline_status"] == "complete"
                && row["plan_sha256"] == summary["plan_sha256"],
            "reference completed observation has inconsistent status/plan",
        )?;
        let attempt = canonical_inside(
            root,
            Path::new(row["attempt"].as_str().ok_or("reference attempt missing")?),
        )?;
        require(
            attempt
                .parent()
                .and_then(Path::file_name)
                .and_then(|x| x.to_str())
                == Some(id.to_string().as_str()),
            "reference attempt is under the wrong observation ID",
        )?;
        let (commit, commit_identity) = json_with_identity(&attempt.join("commit.json"))?;
        let final_identity = identity_value(&commit["final"])?;
        require(
            canonical_inside(root, Path::new(&final_identity.path))? == attempt.join("final.json"),
            "reference commit points at wrong final",
        )?;
        verify_identity(&final_identity)?;
        require(
            input::read_json(Path::new(&final_identity.path))? == *row,
            "reference summary differs from committed final",
        )?;
        let mut artifacts = BTreeMap::new();
        for value in array(row, "artifacts")? {
            let identity = identity_value(value)?;
            canonical_inside(root, Path::new(&identity.path))?;
            verify_identity(&identity)?;
            require(
                artifacts.insert(identity.path.clone(), identity).is_none(),
                "duplicate committed reference artifact",
            )?;
        }
        for name in [
            "native.json",
            "native.plan.json",
            "native.windows.jsonl",
            "input-manifest.json",
        ] {
            require(
                artifacts.contains_key(&attempt.join(name).display().to_string()),
                format!("missing committed reference artifact {name}"),
            )?;
        }
        let native = input::read_json(&attempt.join("native.json"))?;
        let native_plan = input::read_json(&attempt.join("native.plan.json"))?;
        let manifest = input::read_json(&attempt.join("input-manifest.json"))?;
        require(
            native["schema"] == "native-audio-refinement-result-v2",
            "unexpected native reference schema",
        )?;
        let pcm = identity_value(&native["input"])?;
        require(
            same_identity(&pcm, &identity_value(&manifest["wav"])?)
                && same_identity(&pcm, &identity_value(&native_plan["input"])?)
                && row["comparison"]["same_audio_sha256"] == pcm.sha256,
            "reference PCM identity chain disagrees",
        )?;
        require(
            id_field(&manifest, "observation_id")? == id,
            "input manifest has wrong observation ID",
        )?;
        let native_plan_id = identity_value(&native["plan_identity"])?;
        require(
            same_identity(
                &native_plan_id,
                artifacts
                    .get(&attempt.join("native.plan.json").display().to_string())
                    .unwrap(),
            ),
            "native result plan identity differs from committed plan",
        )?;
        let source_path = attempt
            .parent()
            .ok_or("attempt observation directory missing")?
            .join("capture.ogg");
        let external = array(row, "external_artifacts")?;
        let matching: Vec<_> = external
            .iter()
            .filter(|a| a["path"].as_str() == Some(source_path.to_string_lossy().as_ref()))
            .collect();
        require(
            matching.len() == 1,
            "source OGG must have exactly one external identity",
        )?;
        let source = identity_value(matching[0])?;
        require(
            same_identity(&source, &identity_value(&manifest["ogg"])?)
                && artifacts
                    .get(&source.path)
                    .is_some_and(|a| same_identity(a, &source)),
            "OGG identity chain disagrees",
        )?;
        verify_identity(&source)?;
        let mut reference_artifacts: Vec<_> = artifacts.into_values().collect();
        reference_artifacts.extend([commit_identity, final_identity]);
        selected.push(SelectedInput {
            observation_id: id,
            source,
            expected_pcm: pcm,
            reference_artifacts,
        });
        if options.limit.is_some_and(|limit| selected.len() >= limit) {
            break;
        }
    }
    require(
        !selected.is_empty(),
        "no eligible completed observations selected",
    )?;
    verify_identity(&summary_identity)?;
    Ok(Reference {
        summary: summary_identity,
        anchors: vec![plan_identity, cohort_identity, freeze_identity],
        selected,
        frozen_count,
        complete_reference_count,
    })
}

fn resolve_codec(name: &str) -> Result<input::Identity, String> {
    let path = std::env::var_os("PATH").ok_or("PATH missing for codec resolution")?;
    for directory in std::env::split_paths(&path) {
        let candidate = directory.join(name);
        if let Ok(metadata) = fs::metadata(&candidate) {
            #[cfg(unix)]
            {
                use std::os::unix::fs::PermissionsExt;
                if metadata.permissions().mode() & 0o111 == 0 {
                    continue;
                }
            }
            if metadata.is_file() {
                return input::identity(&candidate);
            }
        }
    }
    Err(format!("required codec executable {name} not found"))
}
fn codec_identities(reference: &Reference) -> Result<Vec<input::Identity>, String> {
    for item in &reference.selected {
        let mut magic = [0_u8; 4];
        File::open(&item.source.path)
            .and_then(|mut f| f.read_exact(&mut magic))
            .map_err(|e| e.to_string())?;
        if &magic == b"OggS" {
            return Ok(vec![resolve_codec("ffmpeg")?, resolve_codec("ffprobe")?]);
        }
    }
    Ok(vec![])
}
fn timestamp_ms() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis() as u64
}

struct CampaignLock {
    file: File,
    path: PathBuf,
}
impl CampaignLock {
    fn acquire(root: &Path) -> Result<Self, String> {
        let mut options = OpenOptions::new();
        options.read(true).write(true).create(true);
        #[cfg(unix)]
        {
            use std::os::unix::fs::OpenOptionsExt;
            options.custom_flags(libc::O_NOFOLLOW | libc::O_NONBLOCK);
        }
        let file = options
            .open(root.join(".campaign.lock"))
            .map_err(|e| e.to_string())?;
        require(
            file.metadata().map_err(|e| e.to_string())?.is_file(),
            "campaign lock is not a regular file",
        )?;
        #[cfg(unix)]
        {
            use std::os::fd::AsRawFd;
            if unsafe { libc::flock(file.as_raw_fd(), libc::LOCK_EX | libc::LOCK_NB) } != 0 {
                return Err(
                    "campaign already has a live lock owner; no process was signalled".into(),
                );
            }
        }
        #[cfg(not(unix))]
        return Err("durable campaign locking currently requires Unix".into());
        Ok(Self {
            file,
            path: root.join(".campaign.lock"),
        })
    }
    fn verify_owner(&self) -> Result<(), String> {
        no_symlink(&self.path)?;
        #[cfg(unix)]
        {
            use std::os::unix::fs::MetadataExt;
            let opened = self.file.metadata().map_err(|e| e.to_string())?;
            let current = fs::metadata(&self.path).map_err(|e| e.to_string())?;
            require(
                opened.dev() == current.dev() && opened.ino() == current.ino(),
                "campaign lock pathname was replaced",
            )?;
        }
        Ok(())
    }
}
impl Drop for CampaignLock {
    fn drop(&mut self) {
        #[cfg(unix)]
        {
            use std::os::fd::AsRawFd;
            unsafe {
                libc::flock(self.file.as_raw_fd(), libc::LOCK_UN);
            }
        }
    }
}

extern "C" fn stop_signal(_: libc::c_int) {
    STOP_REQUESTED.store(true, Ordering::SeqCst);
}
struct StopSignals {
    previous: Vec<(libc::c_int, libc::sigaction)>,
}
impl StopSignals {
    fn install() -> Result<Self, String> {
        STOP_REQUESTED.store(false, Ordering::SeqCst);
        let mut guard = Self { previous: vec![] };
        for signal in [libc::SIGINT, libc::SIGTERM] {
            let mut previous: libc::sigaction = unsafe { std::mem::zeroed() };
            let mut action: libc::sigaction = unsafe { std::mem::zeroed() };
            action.sa_sigaction = stop_signal as *const () as usize;
            unsafe {
                libc::sigemptyset(&mut action.sa_mask);
            }
            if unsafe { libc::sigaction(signal, &action, &mut previous) } != 0 {
                return Err(std::io::Error::last_os_error().to_string());
            }
            guard.previous.push((signal, previous));
        }
        Ok(guard)
    }
}
impl Drop for StopSignals {
    fn drop(&mut self) {
        for (signal, previous) in self.previous.iter().rev() {
            unsafe {
                libc::sigaction(*signal, previous, std::ptr::null_mut());
            }
        }
    }
}

fn ensure_dir(path: &Path) -> Result<(), String> {
    if !path.exists() {
        fs::create_dir(path).map_err(|e| e.to_string())?;
    }
    no_symlink(path)?;
    require(path.is_dir(), "owned campaign path is not a directory")
}
fn next_index(path: &Path, prefix: &str, suffix: &str) -> Result<u64, String> {
    let mut largest = 0;
    let mut count = 0;
    for entry in fs::read_dir(path).map_err(|e| e.to_string())? {
        count += 1;
        require(count <= MAX_ENTRIES, "too many campaign artifacts")?;
        let entry = entry.map_err(|e| e.to_string())?;
        no_symlink(&entry.path())?;
        let name = entry
            .file_name()
            .into_string()
            .map_err(|_| "non-UTF8 campaign artifact")?;
        let index = name
            .strip_prefix(prefix)
            .and_then(|s| s.strip_suffix(suffix))
            .and_then(|s| s.parse::<u64>().ok())
            .ok_or("unexpected artifact in indexed campaign directory")?;
        largest = largest.max(index);
    }
    largest
        .checked_add(1)
        .ok_or("campaign index overflow".into())
}
fn campaign_bytes(root: &Path) -> Result<u64, String> {
    let mut stack = vec![root.to_path_buf()];
    let mut total = 0_u64;
    let mut entries = 0;
    while let Some(path) = stack.pop() {
        for entry in fs::read_dir(path).map_err(|e| e.to_string())? {
            entries += 1;
            require(entries <= MAX_ENTRIES, "campaign file count limit exceeded")?;
            let entry = entry.map_err(|e| e.to_string())?;
            let metadata = fs::symlink_metadata(entry.path()).map_err(|e| e.to_string())?;
            require(
                !metadata.file_type().is_symlink(),
                "campaign contains an unowned symlink",
            )?;
            if metadata.is_dir() {
                stack.push(entry.path());
            } else {
                require(
                    metadata.is_file(),
                    "campaign contains a non-regular artifact",
                )?;
                total = total
                    .checked_add(metadata.len())
                    .ok_or("campaign size overflow")?;
            }
        }
    }
    Ok(total)
}
fn available_bytes(root: &Path) -> Result<u64, String> {
    #[cfg(unix)]
    {
        use std::os::unix::ffi::OsStrExt;
        let path =
            std::ffi::CString::new(root.as_os_str().as_bytes()).map_err(|e| e.to_string())?;
        let mut stat: libc::statvfs = unsafe { std::mem::zeroed() };
        if unsafe { libc::statvfs(path.as_ptr(), &mut stat) } != 0 {
            return Err(std::io::Error::last_os_error().to_string());
        }
        Ok((stat.f_bavail as u64).saturating_mul(stat.f_frsize as u64))
    }
    #[cfg(not(unix))]
    Err("disk-reserve check currently requires Unix".into())
}
fn resource_check(root: &Path, expected_pcm_bytes: u64) -> Result<(), String> {
    let headroom = expected_pcm_bytes.saturating_add(METADATA_HEADROOM);
    require(
        campaign_bytes(root)?.saturating_add(headroom) <= MAX_CAMPAIGN_BYTES,
        "campaign disk budget would be exceeded",
    )?;
    require(
        available_bytes(root)? >= DISK_RESERVE.saturating_add(headroom),
        "less than 1 GiB disk reserve plus observation headroom",
    )
}

fn verify_runtime(plan: &Value, reference: &Reference) -> Result<(), String> {
    require(
        same_identity(
            &input::identity(&std::env::current_exe().map_err(|e| e.to_string())?)?,
            &identity_value(&plan["executable"])?,
        ),
        "running executable differs from frozen campaign executable",
    )?;
    verify_identity(&reference.summary)?;
    for anchor in &reference.anchors {
        verify_identity(anchor)?;
    }
    for codec in array(plan, "codecs")? {
        verify_identity(&identity_value(codec)?)?;
    }
    require(
        serde_json::to_value(codec_identities(reference)?).map_err(|e| e.to_string())?
            == plan["codecs"],
        "PATH resolves to different codec executables",
    )
}

fn atomic_copy_new(source: &Path, target: &Path, expected: &input::Identity) -> Result<(), String> {
    if target.exists() {
        no_symlink(target)?;
        let actual = input::identity(target)?;
        return require(
            actual.sha256 == expected.sha256 && actual.bytes == expected.bytes,
            "existing flat artifact differs from committed attempt",
        );
    }
    verify_identity(expected)?;
    let parent = target.parent().ok_or("publication parent missing")?;
    let mut temporary = tempfile::NamedTempFile::new_in(parent).map_err(|e| e.to_string())?;
    let mut input = File::open(source).map_err(|e| e.to_string())?;
    std::io::copy(&mut input, &mut temporary).map_err(|e| e.to_string())?;
    temporary.as_file().sync_all().map_err(|e| e.to_string())?;
    let actual = crate::input::identity(temporary.path())?;
    require(
        actual.sha256 == expected.sha256 && actual.bytes == expected.bytes,
        "artifact changed during publication copy",
    )?;
    temporary
        .persist_noclobber(target)
        .map_err(|e| e.to_string())?;
    File::open(parent)
        .and_then(|f| f.sync_all())
        .map_err(|e| e.to_string())
}

fn row_from_result(result: &Value, id: u64, reused: bool) -> Value {
    json!({
    "observation_id":id,"status":"complete","reused":reused,"unique_pdu_count":result["unique_pdu_count"],
    "elapsed_seconds":result["elapsed_seconds"],"total_wall_seconds":result["total_wall_seconds"]})
}

fn validate_attempt(
    ready: &Value,
    ready_path: &Path,
    item: &SelectedInput,
    plan_identity: &input::Identity,
) -> Result<Vec<input::Identity>, String> {
    require(
        ready["schema"] == "rust-local-campaign-ready-v1"
            && ready["status"] == "complete"
            && id_field(ready, "observation_id")? == item.observation_id,
        "invalid completed attempt marker",
    )?;
    require(
        same_identity(&identity_value(&ready["campaign_plan"])?, plan_identity),
        "attempt belongs to a different campaign plan",
    )?;
    let decoder = ready_path
        .parent()
        .ok_or("ready marker lacks attempt parent")?
        .join("decoder");
    let mut identities = Vec::new();
    for (key, name) in [
        ("result", "result.json"),
        ("journal", "windows.jsonl"),
        ("decoder_plan", "plan.json"),
    ] {
        let expected = identity_value(&ready[key])?;
        require(
            Path::new(&expected.path) == decoder.join(name),
            "completed attempt points at wrong decoder artifact",
        )?;
        no_symlink(Path::new(&expected.path))?;
        verify_identity(&expected)?;
        identities.push(expected);
    }
    let result = input::read_json(&decoder.join("result.json"))?;
    let decoder_plan = input::read_json(&decoder.join("plan.json"))?;
    require(
        result["schema"] == "rust-native-audio-result-v1"
            && result["status"] == "complete"
            && id_field(&result, "observation_id")? == item.observation_id
            && result["failed_window_count"] == 0,
        "attempt decoder did not complete successfully",
    )?;
    let observed_pcm = identity_value(&result["input"])?;
    require(
        observed_pcm.sha256 == item.expected_pcm.sha256
            && observed_pcm.bytes == item.expected_pcm.bytes,
        "decoded PCM is not the exact committed reference WAV",
    )?;
    require(
        same_identity(&identity_value(&result["source_input"])?, &item.source)
            && same_identity(&identity_value(&decoder_plan["source"])?, &item.source),
        "attempt source identity disagrees",
    )?;
    let campaign_plan = input::read_json(Path::new(&plan_identity.path))?;
    require(
        result["config"] == campaign_plan["config"]
            && decoder_plan["config"] == campaign_plan["config"],
        "attempt decoder configuration changed",
    )?;
    require(
        decoder_plan["executable"] == campaign_plan["executable"],
        "attempt decoder executable differs from campaign",
    )?;
    let planned_pcm = identity_value(&decoder_plan["input"])?;
    require(
        planned_pcm.sha256 == observed_pcm.sha256 && planned_pcm.bytes == observed_pcm.bytes,
        "attempt plan/result PCM identities disagree",
    )?;
    require(
        result["reference_bytes_used_for_search"] == false
            && decoder_plan["reference_bytes_used_for_search"] == false,
        "attempt truth-isolation contract missing",
    )?;
    require(
        identities[1].bytes <= 64 * 1024 * 1024,
        "attempt journal exceeds 64 MiB",
    )?;
    let journal = fs::read_to_string(decoder.join("windows.jsonl")).map_err(|e| e.to_string())?;
    require(journal.ends_with('\n'), "attempt journal is truncated")?;
    let windows = journal
        .lines()
        .map(|line| serde_json::from_str::<Value>(line).map_err(|e| e.to_string()))
        .collect::<Result<Vec<_>, _>>()?;
    require(
        !windows.is_empty() && windows.len() as u64 == id_field(&result, "window_count")?,
        "attempt journal/result window counts disagree",
    )?;
    require(
        windows
            .iter()
            .all(|row| row["failures"].as_array().is_some_and(Vec::is_empty)),
        "completed attempt has failed journal windows",
    )?;
    let rate = u32::try_from(id_field(&result, "sample_rate_hz")?).map_err(|e| e.to_string())?;
    let samples =
        usize::try_from(id_field(&result, "input_samples")?).map_err(|e| e.to_string())?;
    let config: receiver::DecodeConfig =
        serde_json::from_value(campaign_plan["config"].clone()).map_err(|e| e.to_string())?;
    let expected =
        receiver::window_bounds(samples, rate, config.window_seconds, config.hop_seconds)?;
    require(
        expected.len() == windows.len(),
        "attempt journal omitted or added windows",
    )?;
    for ((start, end), row) in expected.iter().zip(&windows) {
        require(
            row["offset_seconds"].as_f64() == Some(*start as f64 / rate as f64)
                && id_field(row, "samples")? as usize == end - start,
            "attempt journal window geometry changed",
        )?;
    }
    verify_identity(plan_identity)?;
    Ok(identities)
}

fn publish_ready(
    root: &Path,
    ready_path: &Path,
    item: &SelectedInput,
    plan_identity: &input::Identity,
    reused: bool,
) -> Result<Value, String> {
    let (ready, ready_identity) = json_with_identity(ready_path)?;
    let identities = validate_attempt(&ready, ready_path, item, plan_identity)?;
    let observation = root.join(item.observation_id.to_string());
    for (name, identity) in ["result.json", "windows.jsonl", "plan.json"]
        .into_iter()
        .zip(&identities)
    {
        atomic_copy_new(Path::new(&identity.path), &observation.join(name), identity)?;
    }
    let flat: Vec<_> = ["result.json", "windows.jsonl", "plan.json"]
        .iter()
        .map(|name| input::identity(&observation.join(name)))
        .collect::<Result<_, _>>()?;
    let commit = json!({"schema":"rust-local-campaign-commit-v1","observation_id":item.observation_id,
        "campaign_plan":plan_identity,"ready":ready_identity,"published":flat});
    let commit_path = observation.join("commit.json");
    if commit_path.exists() {
        require(
            input::read_json(&commit_path)? == commit,
            "published commit differs from ready attempt",
        )?;
    } else {
        input::write_json_new(&commit_path, &commit)?;
    }
    verify_identity(&ready_identity)?;
    let mut row = row_from_result(
        &input::read_json(&observation.join("result.json"))?,
        item.observation_id,
        reused,
    );
    row["commit_identity"] =
        serde_json::to_value(input::identity(&commit_path)?).map_err(|e| e.to_string())?;
    Ok(row)
}

fn verify_saved_progress(root: &Path, plan_identity: &input::Identity) -> Result<(), String> {
    // Historical immutable checkpoints bind completed commit bytes, not only
    // their filenames. Rewriting result+ready+commit together is detected.
    let mut paths = Vec::new();
    for directory in ["checkpoints", "summaries"] {
        let path = root.join(directory);
        if !path.exists() {
            continue;
        }
        for entry in fs::read_dir(path).map_err(|e| e.to_string())? {
            let path = entry.map_err(|e| e.to_string())?.path();
            paths.push(path);
            require(
                paths.len() <= MAX_ENTRIES,
                "too many saved progress artifacts",
            )?;
        }
    }
    if root.join("summary.json").exists() {
        paths.push(root.join("summary.json"));
    }
    for path in paths {
        no_symlink(&path)?;
        let value = input::read_json(&path)?;
        require(
            value["schema"] == "rust-local-campaign-progress-v1"
                || value["schema"] == "rust-local-campaign-summary-v1",
            "invalid saved progress schema",
        )?;
        require(
            same_identity(&identity_value(&value["campaign_plan"])?, plan_identity),
            "saved progress belongs to a different plan",
        )?;
        let rows = array(&value, "observations")?;
        let mut seen = BTreeSet::new();
        for row in rows {
            let id = id_field(row, "observation_id")?;
            require(seen.insert(id), "duplicate observation in saved progress")?;
            if row["status"] != "complete" {
                continue;
            }
            let identity = identity_value(&row["commit_identity"])?;
            require(
                Path::new(&identity.path) == root.join(format!("{id}/commit.json")),
                "progress commit points outside its observation",
            )?;
            verify_identity(&identity)?;
        }
        if value["pass"] == true {
            require(
                rows.len() as u64 == id_field(&value, "selected")?
                    && rows.iter().all(|r| r["status"] == "complete"),
                "completed summary does not contain its whole selected cohort",
            )?;
        }
    }
    Ok(())
}

fn reuse_complete(
    root: &Path,
    item: &SelectedInput,
    plan_identity: &input::Identity,
) -> Result<Option<Value>, String> {
    let observation = root.join(item.observation_id.to_string());
    if !observation.exists() {
        return Ok(None);
    }
    no_symlink(&observation)?;
    let commit_path = observation.join("commit.json");
    if commit_path.exists() {
        no_symlink(&commit_path)?;
        let commit = input::read_json(&commit_path)?;
        require(
            commit["schema"] == "rust-local-campaign-commit-v1"
                && id_field(&commit, "observation_id")? == item.observation_id,
            "invalid published campaign commit",
        )?;
        require(
            same_identity(&identity_value(&commit["campaign_plan"])?, plan_identity),
            "published commit belongs to other plan",
        )?;
        let ready_identity = identity_value(&commit["ready"])?;
        canonical_inside(&observation, Path::new(&ready_identity.path))?;
        verify_identity(&ready_identity)?;
        for artifact in array(&commit, "published")? {
            verify_identity(&identity_value(artifact)?)?;
        }
        return publish_ready(
            root,
            Path::new(&ready_identity.path),
            item,
            plan_identity,
            true,
        )
        .map(Some);
    }
    let attempts = observation.join("attempts");
    if !attempts.exists() {
        return Ok(None);
    }
    no_symlink(&attempts)?;
    let mut ready_paths = Vec::new();
    for entry in fs::read_dir(&attempts).map_err(|e| e.to_string())? {
        let path = entry.map_err(|e| e.to_string())?.path();
        no_symlink(&path)?;
        if path.join("ready.json").exists() {
            ready_paths.push(path.join("ready.json"));
        }
    }
    require(
        ready_paths.len() <= 1,
        "multiple completed attempts without a single published commit",
    )?;
    if let Some(path) = ready_paths.first() {
        return publish_ready(root, path, item, plan_identity, true).map(Some);
    }
    for name in ["result.json", "windows.jsonl", "plan.json"] {
        require(
            !observation.join(name).exists(),
            "uncommitted flat artifacts have no verified ready attempt",
        )?;
    }
    Ok(None)
}

fn process_one(
    root: &Path,
    item: &SelectedInput,
    config: &receiver::DecodeConfig,
    plan_identity: &input::Identity,
    run_id: u64,
) -> Result<Value, String> {
    for artifact in &item.reference_artifacts {
        verify_identity(artifact)?;
    }
    verify_identity(&item.source)?;
    if let Some(row) = reuse_complete(root, item, plan_identity)? {
        return Ok(row);
    }
    resource_check(root, item.expected_pcm.bytes)?;
    let observation = root.join(item.observation_id.to_string());
    ensure_dir(&observation)?;
    let attempts = observation.join("attempts");
    ensure_dir(&attempts)?;
    let index = next_index(&attempts, "attempt-", "")?;
    let attempt = attempts.join(format!("attempt-{index:06}"));
    fs::create_dir(&attempt).map_err(|e| e.to_string())?;
    input::write_json_new(
        &attempt.join("begin.json"),
        &json!({"schema":"rust-local-campaign-attempt-v1",
        "observation_id":item.observation_id,"campaign_plan":plan_identity,"run_id":run_id,
        "started_unix_ms":timestamp_ms(),"source":item.source,"expected_pcm":item.expected_pcm,
        "reference_bytes_used_for_search":false}),
    )?;
    let outcome = receiver::decode_file(
        Path::new(&item.source.path),
        &attempt.join("decoder"),
        Some(item.observation_id),
        config,
    );
    let result = match outcome {
        Ok(value) if value["status"] == "complete" => value,
        other => {
            let error = match other {
                Err(error) => error,
                Ok(value) => format!("decoder failed: {}", value["failed_window_count"]),
            };
            let row = json!({"observation_id":item.observation_id,"status":"failed","error":error,"attempt":attempt,
                "finished_unix_ms":timestamp_ms(),"retained_for_diagnosis":true});
            input::write_json_new(&attempt.join("failure.json"), &row)?;
            return Ok(row);
        }
    };
    let ready = json!({"schema":"rust-local-campaign-ready-v1","status":"complete","observation_id":item.observation_id,
        "campaign_plan":plan_identity,"result":input::identity(&attempt.join("decoder/result.json"))?,
        "journal":input::identity(&attempt.join("decoder/windows.jsonl"))?,"decoder_plan":input::identity(&attempt.join("decoder/plan.json"))?});
    // Validate before committing anything reusable. The expected PCM identity
    // is checked even if this run found zero frames.
    validate_attempt(&ready, &attempt.join("ready.json"), item, plan_identity)?;
    verify_identity(&item.source)?;
    require(
        result["reference_bytes_used_for_search"] == false,
        "decoder truth-isolation contract missing",
    )?;
    input::write_json_new(&attempt.join("ready.json"), &ready)?;
    publish_ready(
        root,
        &attempt.join("ready.json"),
        item,
        plan_identity,
        false,
    )
}

fn checkpoint(
    root: &Path,
    run_id: u64,
    rows: &[Value],
    selected: usize,
    state: &str,
) -> Result<(), String> {
    let directory = root.join("checkpoints");
    let index = next_index(&directory, "checkpoint-", ".json")?;
    input::write_json_new(
        &directory.join(format!("checkpoint-{index:06}.json")),
        &json!({
        "schema":"rust-local-campaign-progress-v1","recorded_unix_ms":timestamp_ms(),"run_id":run_id,
        "campaign_plan":input::identity(&root.join("batch-plan.json"))?,
        "status":state,"selected":selected,"processed_this_run":rows.len(),"observations":rows}),
    )
}

/// Run or resume a fixed local cohort. No downloads, submissions or job kills.
pub fn run_local(
    reference_summary: &Path,
    output: &Path,
    config: &receiver::DecodeConfig,
    options: &CampaignOptions,
) -> Result<Value, String> {
    let _process = PROCESS_CAMPAIGN
        .try_lock()
        .map_err(|_| "another campaign is running in this process")?;
    require(
        (1..=64).contains(&options.threads) && options.threads == config.threads,
        "campaign and decoder threads must match in 1..64",
    )?;
    let reference = reference_inputs(reference_summary, options)?;
    let executable = input::identity(&std::env::current_exe().map_err(|e| e.to_string())?)?;
    let codecs = codec_identities(&reference)?;
    let expected_plan = json!({"schema":"rust-local-campaign-plan-v1","reference_summary":reference.summary,
        "reference_anchors":reference.anchors,"selected":reference.selected,"executable":executable,"codecs":codecs,
        "compute_identity":crate::compute::current().identity(),
        "config":config,"selection":{"observation_id":options.observation_id,"limit":options.limit,"threads":options.threads},
        "frozen_count":reference.frozen_count,"complete_reference_count":reference.complete_reference_count,
        "limits":{"reserve_bytes":DISK_RESERVE,"campaign_bytes":MAX_CAMPAIGN_BYTES},
        "reference_bytes_used_for_search":false,"network_submission":false,
        "stop_policy":"finish bounded current observation then checkpoint; no hard in-process decoder timeout"});
    let root = if options.resume {
        no_symlink(output)?;
        require(output.is_dir(), "resume output is not a directory")?;
        output.canonicalize().map_err(|e| e.to_string())?
    } else {
        input::existing_new_dir(output)?
    };
    let _lock = CampaignLock::acquire(&root)?;
    let plan_path = root.join("batch-plan.json");
    if options.resume {
        require(
            input::read_json(&plan_path)? == expected_plan,
            "resume executable/config/reference/selection differs from frozen plan",
        )?;
    } else {
        input::write_json_new(&plan_path, &expected_plan)?;
    }
    let plan_identity = input::identity(&plan_path)?;
    for directory in ["runs", "checkpoints", "summaries"] {
        ensure_dir(&root.join(directory))?;
    }
    if options.resume {
        verify_saved_progress(&root, &plan_identity)?;
    }
    let selected_ids: BTreeSet<_> = reference
        .selected
        .iter()
        .map(|x| x.observation_id.to_string())
        .collect();
    for entry in fs::read_dir(&root).map_err(|e| e.to_string())? {
        let entry = entry.map_err(|e| e.to_string())?;
        let name = entry.file_name().to_string_lossy().into_owned();
        if name.parse::<u64>().is_ok() {
            require(
                selected_ids.contains(&name),
                "campaign contains an unexpected observation directory",
            )?;
        }
    }
    let _signals = StopSignals::install()?;
    let run_id = next_index(&root.join("runs"), "run-", ".json")?;
    input::write_json_new(
        &root.join(format!("runs/run-{run_id:06}.json")),
        &json!({
        "schema":"rust-local-campaign-owner-v1","pid":std::process::id(),"started_unix_ms":timestamp_ms(),
        "boot_id":fs::read_to_string("/proc/sys/kernel/random/boot_id").ok().map(|x|x.trim().to_string()),
        "process_stat":fs::read_to_string("/proc/self/stat").ok(),"campaign_plan":plan_identity,
        "lock":"kernel flock; stale owner records never trigger process signalling"}),
    )?;
    let started = Instant::now();
    let mut rows = Vec::new();
    let mut pause = None;
    for item in &reference.selected {
        if STOP_REQUESTED.load(Ordering::SeqCst) {
            pause = Some("SIGINT/SIGTERM requested a safe stop".to_string());
            break;
        }
        let outcome = (|| {
            _lock.verify_owner()?;
            resource_check(&root, 0)?;
            verify_identity(&plan_identity)?;
            verify_runtime(&expected_plan, &reference)?;
            let row = process_one(&root, item, config, &plan_identity, run_id)?;
            verify_runtime(&expected_plan, &reference)?;
            for artifact in &item.reference_artifacts {
                verify_identity(artifact)?;
            }
            _lock.verify_owner()?;
            resource_check(&root, 0)?;
            Ok::<Value, String>(row)
        })();
        match outcome {
            Ok(row) => rows.push(row),
            Err(error) => {
                pause = Some(error);
                break;
            }
        }
        checkpoint(&root, run_id, &rows, reference.selected.len(), "running")?;
    }
    let complete = rows.iter().filter(|r| r["status"] == "complete").count();
    let failed = rows.iter().filter(|r| r["status"] == "failed").count();
    let all_complete = pause.is_none() && complete == reference.selected.len();
    let result = json!({"schema":"rust-local-campaign-summary-v1","run_id":run_id,"campaign_plan":plan_identity,
        "status":if all_complete{"complete"}else if pause.is_some(){"paused"}else{"failed"},
        "pass":all_complete,"selected":reference.selected.len(),"complete":complete,"failed":failed,
        "pending":reference.selected.len()-rows.len(),"observations":rows,"pause":pause,
        "wall_seconds":started.elapsed().as_secs_f64(),"comparison_pending":true,
        "publication_ready":false,"deployment_ready":false,"reference_bytes_used_for_search":false});
    checkpoint(
        &root,
        run_id,
        &rows,
        reference.selected.len(),
        result["status"].as_str().unwrap(),
    )?;
    input::write_json_new(
        &root.join(format!("summaries/summary-{run_id:06}.json")),
        &result,
    )?;
    if all_complete {
        let terminal = root.join("summary.json");
        if !terminal.exists() {
            input::write_json_new(&terminal, &result)?;
        } else {
            let original = input::read_json(&terminal)?;
            require(
                original["status"] == "complete"
                    && original["campaign_plan"] == result["campaign_plan"],
                "existing terminal summary is incompatible",
            )?;
        }
    }
    Ok(result)
}

#[cfg(test)]
#[path = "tests/campaign_tests.rs"]
mod tests;
