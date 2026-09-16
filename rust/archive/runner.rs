//! Frozen, sequential paired experiments. No Python orchestration.
use super::{Sealed, baselines, cohort::Cohort, read, reserve_space, transport};
use crate::{input, progressive};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use std::collections::{BTreeMap, BTreeSet};
use std::fs::{self, File, OpenOptions};
use std::os::fd::AsRawFd;
use std::os::unix::fs::OpenOptionsExt;
use std::path::{Path, PathBuf};

#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Release {
    pub schema: String,
    pub cohort_sha256: String,
    pub frozen_utc: String,
    pub executables: BTreeMap<String, input::Identity>,
    pub scheduler_policy: Value,
    pub dependency_files: Vec<input::Identity>,
    pub baseline_timeout_seconds: u64,
    pub representation: String,
}

impl Release {
    pub fn validate(&self) -> Result<(), String> {
        if self.schema != "framelift-archive-release-v1"
            || self.baseline_timeout_seconds != 120
            || self
                .executables
                .keys()
                .map(String::as_str)
                .collect::<BTreeSet<_>>()
                != BTreeSet::from(["receiver", "direwolf", "gr_satellites", "ffmpeg"])
            || self.scheduler_policy != progressive::scheduler::Policy::MarginalYield.identity()
            || self.dependency_files.is_empty()
            || self.representation.trim().is_empty()
        {
            return Err("incomplete or unsupported release registration".into());
        }
        super::cohort::utc(&self.frozen_utc)?;
        for identity in self.executables.values().chain(&self.dependency_files) {
            if !Path::new(&identity.path).is_absolute()
                || identity.bytes == 0
                || identity.sha256.len() != 64
                || !identity
                    .sha256
                    .bytes()
                    .all(|b| b.is_ascii_hexdigit() && !b.is_ascii_uppercase())
            {
                return Err("invalid registered artifact identity".into());
            }
        }
        Ok(())
    }
}

pub fn verify_identity(expected: &input::Identity) -> Result<(), String> {
    let actual = input::identity(Path::new(&expected.path))?;
    if actual.sha256 != expected.sha256 || actual.bytes != expected.bytes {
        return Err(format!("frozen artifact changed: {}", expected.path));
    }
    Ok(())
}

pub fn register(
    cohort_path: &Path,
    programs: &BTreeMap<String, PathBuf>,
    dependencies: &[PathBuf],
    out: &Path,
) -> Result<Value, String> {
    let cohort = Sealed::<Cohort>::read(cohort_path)?;
    cohort.content.validate()?;
    if programs.keys().map(String::as_str).collect::<BTreeSet<_>>()
        != BTreeSet::from(["receiver", "direwolf", "gr_satellites", "ffmpeg"])
    {
        return Err("registration requires receiver, direwolf, gr_satellites and ffmpeg".into());
    }
    let mut executables = BTreeMap::new();
    for (name, path) in programs {
        if !path.is_absolute() {
            return Err("executable paths must be absolute".into());
        }
        executables.insert(name.clone(), input::identity(path)?);
    }
    let mut dependency_files = Vec::new();
    for path in dependencies {
        dependency_files.push(input::identity(path)?);
    }
    dependency_files.push(input::identity(
        &std::env::current_exe().map_err(|e| e.to_string())?,
    )?);
    let release = Release {
        schema: "framelift-archive-release-v1".into(),
        cohort_sha256: cohort.sha256,
        frozen_utc: transport::utc(),
        executables,
        dependency_files,
        scheduler_policy: progressive::scheduler::Policy::MarginalYield.identity(),
        baseline_timeout_seconds: 120,
        representation:
            "same full mono PCM16 WAV at original sample rate; no gain, resampling or crop".into(),
    };
    release.validate()?;
    let sealed = Sealed::write(out, release)?;
    Ok(
        json!({"release":out,"sha256":sealed.sha256,"scope":"frozen named executables and supplied dependency files, not a hermetic OS image"}),
    )
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Arm {
    pub name: String,
    pub status: String,
    pub budget_ms: Option<u64>,
    pub payload_hex: BTreeSet<String>,
    pub process: Option<Value>,
    pub audit: Option<Value>,
    pub error: Option<String>,
}

impl Arm {
    pub fn valid(&self) -> bool {
        matches!(self.status.as_str(), "complete" | "partial")
    }
}

#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Row {
    pub id: u64,
    pub release_sha256: String,
    pub source: Option<input::Identity>,
    pub pcm: Option<input::Identity>,
    pub preparation: Option<Value>,
    pub arms: Vec<Arm>,
    pub error: Option<String>,
}

fn new_dir(path: &Path) -> Result<(), String> {
    fs::create_dir(path).map_err(|e| e.to_string())
}

fn native_arguments(
    name: &str,
    budget: u64,
    wav: &Path,
    dir: &Path,
    threads: usize,
) -> Vec<String> {
    vec![
        "decode-progressive".into(),
        "--input".into(),
        wav.display().to_string(),
        "--output".into(),
        dir.join("session").display().to_string(),
        "--mode".into(),
        "full".into(),
        "--budget-ms".into(),
        budget.to_string(),
        "--threads".into(),
        threads.to_string(),
        "--scheduler".into(),
        name.into(),
        "--cache-mib".into(),
        "512".into(),
        "--compute".into(),
        "cpu".into(),
    ]
}

fn baseline_arguments(name: &str, wav: &Path, dir: &Path, profile: &Path) -> Vec<String> {
    if name == "direwolf" {
        vec![
            "-B".into(),
            "9600".into(),
            "-F".into(),
            "0".into(),
            "-h".into(),
            wav.display().to_string(),
        ]
    } else {
        vec![
            profile.display().to_string(),
            "--wavfile".into(),
            wav.display().to_string(),
            "--kiss_out".into(),
            dir.join("frames.kiss").display().to_string(),
            "--hexdump".into(),
        ]
    }
}

fn native_arm(
    name: &str,
    budget: u64,
    wav: &Path,
    dir: &Path,
    release: &Release,
    threads: usize,
) -> Result<Arm, String> {
    new_dir(dir)?;
    let identity = &release.executables["receiver"];
    verify_identity(identity)?;
    let args = native_arguments(name, budget, wav, dir, threads);
    let process = transport::execute(
        Path::new(&identity.path),
        &args,
        dir,
        "run",
        budget.div_ceil(1000) + 20,
        128 * 1024 * 1024,
    )?;
    let result = (|| {
        transport::require_success(&process)?;
        let receipt: Value = read(&dir.join("run.stdout.log"))?;
        if receipt["input_and_checkpoints_verified"] != true || !receipt["error"].is_null() {
            return Err(
                "budget expired before verified input, or worker failed; not a zero-frame result"
                    .into(),
            );
        }
        let audit = progressive::compute_audit::inspect_partial(&dir.join("session"))?;
        if audit["evidence"]["executable_sha256"] != identity.sha256
            || audit["source"]["sha256"] != input::identity(wav)?.sha256
        {
            return Err("native arm input or executable mismatch".into());
        }
        let frames: BTreeSet<String> = serde_json::from_value(audit["frame_with_fcs_hex"].clone())
            .map_err(|e| e.to_string())?;
        let payloads = frames
            .into_iter()
            .map(|s| s[..s.len() - 4].to_string())
            .collect();
        Ok((audit, payloads))
    })();
    verify_identity(identity)?;
    Ok(match result {
        Ok((audit, payload_hex)) => Arm {
            name: name.into(),
            budget_ms: Some(budget),
            status: if audit["evidence"]["complete"] == true {
                "complete"
            } else {
                "partial"
            }
            .into(),
            payload_hex,
            process: Some(process),
            audit: Some(audit),
            error: None,
        },
        Err(error) => Arm {
            name: name.into(),
            budget_ms: Some(budget),
            status: "invalid_or_unverified".into(),
            payload_hex: BTreeSet::new(),
            process: Some(process),
            audit: None,
            error: Some(error),
        },
    })
}

fn baseline_arm(
    name: &str,
    wav: &Path,
    dir: &Path,
    profile: &Path,
    release: &Release,
) -> Result<Arm, String> {
    new_dir(dir)?;
    let identity = &release.executables[name];
    verify_identity(identity)?;
    let args = baseline_arguments(name, wav, dir, profile);
    let process = transport::execute(
        Path::new(&identity.path),
        &args,
        dir,
        "run",
        release.baseline_timeout_seconds,
        64 * 1024 * 1024,
    )?;
    let parsed = (|| {
        transport::require_success(&process)?;
        if name == "direwolf" {
            baselines::parse_direwolf_atest(&String::from_utf8_lossy(
                &crate::research::input::read_regular_bounded(
                    &dir.join("run.stdout.log"),
                    64 * 1024 * 1024,
                )?,
            ))
        } else {
            baselines::parse_gr_satellites_kiss(&crate::research::input::read_regular_bounded(
                &dir.join("frames.kiss"),
                64 * 1024 * 1024,
            )?)
        }
    })();
    verify_identity(identity)?;
    Ok(match parsed {
        Ok(pdus) => Arm {
            name: name.into(),
            status: "complete".into(),
            budget_ms: None,
            payload_hex: pdus.strict_ui_payloads.clone(),
            process: Some(process),
            audit: Some(json!({"pdus":pdus,"profile":input::identity(profile)?,
                "kiss":if name == "gr_satellites" {Some(input::identity(&dir.join("frames.kiss"))?)} else {None}})),
            error: None,
        },
        Err(error) => Arm {
            name: name.into(),
            status: "invalid_or_unverified".into(),
            budget_ms: None,
            payload_hex: BTreeSet::new(),
            process: Some(process),
            audit: None,
            error: Some(error),
        },
    })
}

fn preparation_arguments(source: &Path, wav: &Path) -> Vec<String> {
    vec![
        "-nostdin".into(),
        "-v".into(),
        "error".into(),
        "-i".into(),
        source.display().to_string(),
        "-map".into(),
        "0:a:0".into(),
        "-map_metadata".into(),
        "-1".into(),
        // atest expects the data chunk immediately after fmt; FFmpeg's default
        // encoder metadata adds a LIST chunk. These flags omit metadata, not
        // audio samples. Every arm still receives this same complete PCM file.
        "-fflags".into(),
        "+bitexact".into(),
        "-c:a".into(),
        "pcm_s16le".into(),
        "-n".into(),
        wav.display().to_string(),
    ]
}

fn execute_row(
    id: u64,
    cohort: &Cohort,
    release: &Sealed<Release>,
    acquisition: &Path,
    root: &Path,
) -> Result<Row, String> {
    let selected = cohort
        .observations
        .iter()
        .find(|r| r.id == id)
        .ok_or("not in frozen cohort")?;
    let mut row = Row {
        id,
        release_sha256: release.sha256.clone(),
        source: None,
        pcm: None,
        preparation: None,
        arms: Vec::new(),
        error: None,
    };
    let run = (|| {
        let source = acquisition.join(id.to_string()).join("source.ogg");
        let acquisition_receipt: Value =
            read(&acquisition.join(id.to_string()).join("acquisition.json"))?;
        let expected: input::Identity =
            serde_json::from_value(acquisition_receipt["download"]["identity"].clone())
                .map_err(|e| e.to_string())?;
        verify_identity(&expected)?;
        if acquisition_receipt["cohort_sha256"] != release.content.cohort_sha256
            || acquisition_receipt["success"] != true
            || input::identity(&source)?.sha256 != expected.sha256
        {
            return Err("acquisition not bound to frozen cohort/source".into());
        }
        row.source = Some(expected);
        let wav = root.join("common.wav");
        let args = preparation_arguments(&source, &wav);
        let codec = &release.content.executables["ffmpeg"];
        verify_identity(codec)?;
        let preparation = transport::execute(
            Path::new(&codec.path),
            &args,
            root,
            "prepare",
            120,
            256 * 1024 * 1024,
        )?;
        row.preparation = Some(preparation.clone());
        transport::require_success(&preparation)?;
        let reader = hound::WavReader::open(&wav).map_err(|e| e.to_string())?;
        let spec = reader.spec();
        if spec.channels != 1
            || spec.sample_format != hound::SampleFormat::Int
            || spec.bits_per_sample != 16
            || spec.sample_rate < 19_200
            || reader.duration() == 0
            || reader.duration() as f64 / spec.sample_rate as f64 > 1800.0
        {
            return Err("unsupported prepared audio geometry".into());
        }
        row.pcm = Some(input::identity(&wav)?);
        let mission = cohort
            .protocol
            .missions
            .iter()
            .find(|m| m.norad == selected.norad)
            .ok_or("missing mission profile")?;
        let profile = root.join("profile.yml");
        transport::new_text(
            &profile,
            &format!(
                "name: Frozen FSK9600 component baseline\nnorad: 0\ndata:\n  &raw Raw: unknown\ntransmitters:\n  selected:\n    frequency: 437000000\n    modulation: FSK\n    baudrate: 9600\n    deviation: {}\n    framing: AX.25 G3RUH\n    data:\n    - *raw\n",
                mission.deviation_hz
            ),
        )?;
        // Counterbalance order without looking at any waveform/decoder result.
        let mut schedule = vec![("direwolf", None), ("gr_satellites", None)];
        for budget in &cohort.protocol.budgets_ms {
            schedule.extend([("fixed", Some(*budget)), ("marginal-yield", Some(*budget))]);
        }
        let rotate = (id as usize) % schedule.len();
        schedule.rotate_left(rotate);
        if id.is_multiple_of(2) {
            schedule.reverse();
        }
        for (name, budget) in schedule {
            reserve_space(root, 256 * 1024 * 1024)?;
            let dir = root.join(format!("{name}-{}", budget.unwrap_or(0)));
            let arm = match budget {
                Some(ms) => native_arm(
                    name,
                    ms,
                    &wav,
                    &dir,
                    &release.content,
                    cohort.protocol.threads,
                ),
                None => baseline_arm(name, &wav, &dir, &profile, &release.content),
            };
            row.arms.push(arm.unwrap_or_else(|error| Arm {
                name: name.into(),
                budget_ms: budget,
                status: "failed".into(),
                payload_hex: BTreeSet::new(),
                process: None,
                audit: None,
                error: Some(error),
            }));
            verify_identity(row.pcm.as_ref().unwrap())?;
        }
        verify_identity(row.source.as_ref().unwrap())?;
        Ok::<_, String>(())
    })();
    row.error = run.err();
    Ok(row)
}

pub fn run(
    cohort_path: &Path,
    release_path: &Path,
    acquisition: &Path,
    output: &Path,
    resume: bool,
) -> Result<Value, String> {
    let cohort = Sealed::<Cohort>::read(cohort_path)?;
    let release = Sealed::<Release>::read(release_path)?;
    cohort.content.validate()?;
    release.content.validate()?;
    if release.content.schema != "framelift-archive-release-v1"
        || release.content.cohort_sha256 != cohort.sha256
        || release.content.scheduler_policy
            != progressive::scheduler::Policy::MarginalYield.identity()
    {
        return Err("release/cohort/policy mismatch".into());
    }
    for identity in release
        .content
        .executables
        .values()
        .chain(&release.content.dependency_files)
    {
        verify_identity(identity)?;
    }
    let root = if resume {
        fs::canonicalize(output).map_err(|e| e.to_string())?
    } else {
        input::existing_new_dir(output)?
    };
    let lock = OpenOptions::new()
        .read(true)
        .write(true)
        .create(true)
        .truncate(false)
        .custom_flags(libc::O_NOFOLLOW)
        .open(root.join("campaign.lock"))
        .map_err(|e| e.to_string())?;
    if !lock.metadata().map_err(|e| e.to_string())?.is_file()
        || unsafe { libc::flock(lock.as_raw_fd(), libc::LOCK_EX | libc::LOCK_NB) } != 0
    {
        return Err("campaign is already running or lock is invalid".into());
    }
    let binding = json!({"cohort_sha256":cohort.sha256,"release_sha256":release.sha256,
        "acquisition":fs::canonicalize(acquisition).map_err(|e|e.to_string())?});
    if resume {
        if read::<Value>(&root.join("binding.json"))? != binding {
            return Err("campaign resume binding changed".into());
        }
    } else {
        input::write_json_new(&root.join("binding.json"), &binding)?;
    }
    for selected in &cohort.content.observations {
        let dir = root.join(selected.id.to_string());
        let record = dir.join("row.json");
        if record.exists() {
            let row = Sealed::<Row>::read(&record)?;
            if row.content.id != selected.id || row.content.release_sha256 != release.sha256 {
                return Err("row identity mismatch".into());
            }
            continue;
        }
        reserve_space(&root, 512 * 1024 * 1024)?;
        if dir.exists() {
            // An interrupted observation is not silently rerun or erased. Keep
            // its files and record explicit attrition for this registered run.
            let row = Row {
                id: selected.id,
                release_sha256: release.sha256.clone(),
                source: None,
                pcm: None,
                preparation: None,
                arms: vec![],
                error: Some(
                    "interrupted observation; retained without outcome-dependent rerun".into(),
                ),
            };
            Sealed::write(&record, row)?;
            continue;
        }
        new_dir(&dir)?;
        let row = execute_row(selected.id, &cohort.content, &release, acquisition, &dir)?;
        Sealed::write(&record, row)?;
        transport::replace_json(
            &root.join("progress.json"),
            &json!({"last_completed_observation":selected.id,"release_sha256":release.sha256,"complete":false}),
        )?;
    }
    for identity in release
        .content
        .executables
        .values()
        .chain(&release.content.dependency_files)
    {
        verify_identity(identity)?;
    }
    let finished = json!({"schema":"framelift-archive-run-v1","selected":cohort.content.observations.len(),"release_sha256":release.sha256,"complete":true,
        "complete_means":"every selected observation has a terminal record, not that every decoder succeeded"});
    let completion = root.join("finished.json");
    if completion.exists() {
        if read::<Value>(&completion)? != finished {
            return Err("completed campaign receipt changed".into());
        }
    } else {
        input::write_json_new(&completion, &finished)?;
    }
    File::open(&root)
        .and_then(|f| f.sync_all())
        .map_err(|e| e.to_string())?;
    // The full native workflow publishes its audited report without requiring
    // a second interactive agent turn after a long unattended run.
    let report_path = root.join("report.json");
    if !report_path.exists() {
        super::report::create(cohort_path, release_path, &root, &report_path)?;
    }
    Ok(finished)
}

pub fn audit_arm(
    arm: &Arm,
    root: &Path,
    pcm: Option<&input::Identity>,
    release: &Release,
    threads: usize,
) -> Result<(), String> {
    let known = match arm.budget_ms {
        Some(_) => ["fixed", "marginal-yield"].contains(&arm.name.as_str()),
        None => ["direwolf", "gr_satellites"].contains(&arm.name.as_str()),
    };
    if !known {
        return Err("unknown arm identity".into());
    }
    if !arm.valid() {
        if !arm.payload_hex.is_empty() {
            return Err("failed arm cannot contribute accepted packets".into());
        }
        return Ok(());
    }
    let process = arm.process.as_ref().ok_or("missing process receipt")?;
    transport::require_success(process)?;
    let pcm = pcm.ok_or("accepted arm requires prepared input identity")?;
    let wav = root.join("common.wav");
    if input::identity(&wav)?.sha256 != pcm.sha256 {
        return Err("arm input differs from common PCM".into());
    }
    let dir = root.join(format!("{}-{}", arm.name, arm.budget_ms.unwrap_or(0)));
    let (role, args) = match arm.budget_ms {
        Some(budget) => (
            "receiver",
            native_arguments(&arm.name, budget, &wav, &dir, threads),
        ),
        None => (
            arm.name.as_str(),
            baseline_arguments(&arm.name, &wav, &dir, &root.join("profile.yml")),
        ),
    };
    let executable = release
        .executables
        .get(role)
        .ok_or("unregistered arm executable")?;
    if process["program"] != executable.path
        || process["args"] != json!(args)
        || process["returncode"] != 0
        || process["timed_out"] != false
        || read::<Value>(&dir.join("run.process.json"))? != *process
    {
        return Err("process receipt differs from frozen invocation".into());
    }
    for field in [
        "wall_seconds",
        "user_cpu_seconds",
        "system_cpu_seconds",
        "maximum_rss_kib",
    ] {
        if process[field]
            .as_f64()
            .is_none_or(|n| !n.is_finite() || n < 0.0)
        {
            return Err("invalid successful process resource accounting".into());
        }
    }
    for field in ["stdout", "stderr"] {
        let identity: input::Identity =
            serde_json::from_value(process[field].clone()).map_err(|e| e.to_string())?;
        verify_identity(&identity)?;
        if identity.sha256 != input::identity(&dir.join(format!("run.{field}.log")))?.sha256 {
            return Err("process output evidence redirected to different bytes".into());
        }
    }
    let actual = if arm.budget_ms.is_some() {
        let audit = progressive::compute_audit::inspect_partial(&dir.join("session"))?;
        if arm.audit.as_ref() != Some(&audit) {
            return Err("native arm audit changed".into());
        }
        if audit["source"]["sha256"] != pcm.sha256
            || audit["evidence"]["executable_sha256"] != executable.sha256
        {
            return Err("native committed session differs from registered input/executable".into());
        }
        let frames: BTreeSet<String> = serde_json::from_value(audit["frame_with_fcs_hex"].clone())
            .map_err(|e| e.to_string())?;
        frames
            .into_iter()
            .map(|s| s[..s.len() - 4].to_string())
            .collect()
    } else if arm.name == "direwolf" {
        let profile: input::Identity = serde_json::from_value(
            arm.audit.as_ref().ok_or("baseline audit absent")?["profile"].clone(),
        )
        .map_err(|e| e.to_string())?;
        verify_identity(&profile)?;
        baselines::parse_direwolf_atest(&String::from_utf8_lossy(
            &crate::research::input::read_regular_bounded(
                &dir.join("run.stdout.log"),
                64 * 1024 * 1024,
            )?,
        ))?
        .strict_ui_payloads
    } else if arm.name == "gr_satellites" {
        let audit = arm.audit.as_ref().ok_or("baseline audit absent")?;
        for field in ["profile", "kiss"] {
            let expected: input::Identity =
                serde_json::from_value(audit[field].clone()).map_err(|e| e.to_string())?;
            verify_identity(&expected)?;
        }
        baselines::parse_gr_satellites_kiss(&crate::research::input::read_regular_bounded(
            &dir.join("frames.kiss"),
            64 * 1024 * 1024,
        )?)?
        .strict_ui_payloads
    } else {
        return Err("unknown baseline arm".into());
    };
    if arm.payload_hex != actual {
        return Err("reported payloads differ from retained decoder artifacts".into());
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    fn release() -> Release {
        Release {
            schema: "fixture".into(),
            cohort_sha256: "a".repeat(64),
            frozen_utc: "2026-09-16T00:00:00Z".into(),
            executables: BTreeMap::new(),
            scheduler_policy: json!({}),
            dependency_files: vec![],
            baseline_timeout_seconds: 120,
            representation: "fixture".into(),
        }
    }
    #[test]
    fn failed_or_unknown_arms_cannot_smuggle_payloads_or_paths() {
        let mut arm = Arm {
            name: "fixed".into(),
            budget_ms: Some(3000),
            status: "failed".into(),
            payload_hex: BTreeSet::new(),
            process: None,
            audit: None,
            error: Some("fixture".into()),
        };
        assert!(audit_arm(&arm, Path::new("missing"), None, &release(), 4).is_ok());
        arm.payload_hex.insert("aa".into());
        assert!(audit_arm(&arm, Path::new("missing"), None, &release(), 4).is_err());
        arm.payload_hex.clear();
        arm.name = "../../other".into();
        assert!(audit_arm(&arm, Path::new("missing"), None, &release(), 4).is_err());
    }
    #[test]
    fn frozen_invocations_separate_policy_budgets_and_baselines() {
        let wav = Path::new("/fixed/common.wav");
        let dir = Path::new("/fixed/arm");
        let fixed = native_arguments("fixed", 3000, wav, dir, 4);
        let adaptive = native_arguments("marginal-yield", 3000, wav, dir, 4);
        assert_eq!(
            fixed.iter().zip(&adaptive).filter(|(a, b)| a != b).count(),
            1
        );
        assert!(
            baseline_arguments("direwolf", wav, dir, Path::new("/profile"))
                .windows(2)
                .any(|w| w == ["-F", "0"])
        );
    }

    #[test]
    fn common_pcm_omits_incompatible_metadata_without_changing_audio_geometry() {
        let args = preparation_arguments(Path::new("/source.ogg"), Path::new("/common.wav"));
        assert!(args.windows(2).any(|w| w == ["-fflags", "+bitexact"]));
        assert!(args.windows(2).any(|w| w == ["-map_metadata", "-1"]));
        assert!(args.windows(2).any(|w| w == ["-c:a", "pcm_s16le"]));
        for forbidden in ["-ar", "-ac", "-af", "-filter:a", "-t", "-ss"] {
            assert!(!args.iter().any(|a| a == forbidden));
        }
    }
}
