//! Small, preregistered historical audio coverage pilot; not a holdout claim.
//! Metadata selection, acquisition and decoding are separate commands. Every
//! selected record is retained, including acquisition or decoder failures.
#[allow(dead_code, clippy::type_complexity, clippy::collapsible_if)]
#[path = "support/innovation_acquire_io.rs"]
mod io;

use clap::{Parser, Subcommand};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::{
    collections::BTreeSet,
    fs,
    path::{Path, PathBuf},
};
use telemetry_yield_rs::{archive::baselines, input, protocol, recovery_guard::Guard};

#[derive(Parser)]
struct Args {
    #[command(subcommand)]
    command: Action,
}
#[derive(Subcommand)]
enum Action {
    Freeze {
        #[arg(long)]
        output: PathBuf,
    },
    Acquire {
        #[arg(long)]
        output: PathBuf,
    },
    Run {
        #[arg(long)]
        output: PathBuf,
        #[arg(long)]
        old: PathBuf,
        #[arg(long)]
        new: PathBuf,
        #[arg(long, default_value = "audio-run-v2")]
        run_name: String,
        /// Separate PCM workspace, e.g. an explicitly documented tmpfs directory.
        #[arg(long)]
        pcm_root: Option<PathBuf>,
    },
}
const MISSIONS: [(&str, &str, u64, u64, &str); 3] = [
    ("iss", "ZJxCeQmih9zDfYNVrB4wRN", 25544, 1200, "afsk"),
    ("sonate", "Fo5WYJpLBKNyNQyqiuNXnw", 59112, 9600, "fsk"),
    ("canvas", "GCmN6RULea8dAT7Qoat8z2", 68635, 9600, "fsk"),
];
fn hash(value: &impl serde::Serialize) -> Result<String, String> {
    Ok(hex::encode(Sha256::digest(
        serde_json::to_vec(value).map_err(|e| e.to_string())?,
    )))
}
fn mkdir(path: &Path) -> Result<(), String> {
    fs::create_dir_all(path).map_err(|e| e.to_string())
}
fn storage_guard(path: &Path, allowance: u64) -> Result<(), String> {
    use std::os::unix::ffi::OsStrExt;
    let name = std::ffi::CString::new(path.as_os_str().as_bytes()).map_err(|e| e.to_string())?;
    let mut stat: libc::statvfs = unsafe { std::mem::zeroed() };
    if unsafe { libc::statvfs(name.as_ptr(), &mut stat) } != 0 {
        return Err(std::io::Error::last_os_error().to_string());
    }
    if stat.f_bavail.saturating_mul(stat.f_frsize) < 4 * 1024 * 1024 * 1024 + allowance {
        return Err("four GiB safety reserve plus next-artifact allowance unavailable".into());
    }
    Ok(())
}
fn read(path: &Path) -> Result<Value, String> {
    input::read_json(path)
}
fn s<'a>(v: &'a Value, key: &str) -> Result<&'a str, String> {
    v[key].as_str().ok_or_else(|| format!("missing {key}"))
}
fn check(v: &Value) -> Result<(), String> {
    if input::identity(Path::new(s(v, "path")?))?.sha256 != s(v, "sha256")? {
        return Err("frozen artifact changed".into());
    }
    Ok(())
}
fn freeze(root: &Path) -> Result<(), String> {
    let mut rows = Vec::new();
    let mut metadata = Vec::new();
    for (mission, tx, norad, baud, mode) in MISSIONS {
        let path = root.join(format!("{mission}-metadata.json"));
        let source = read(&path)?;
        let source = source.as_array().ok_or("metadata array required")?;
        for row in source {
            if row["transmitter_uuid"] != tx
                || row["norad_cat_id"] != norad
                || row["transmitter_baud"].as_f64() != Some(baud as f64)
            {
                return Err("API returned mismatched mission/rate".into());
            }
            let start = chrono::DateTime::parse_from_rfc3339(s(row, "start")?)
                .map_err(|e| e.to_string())?;
            let lower = chrono::DateTime::parse_from_rfc3339("2026-06-01T00:00:00Z").unwrap();
            let upper = chrono::DateTime::parse_from_rfc3339("2026-06-02T00:00:00Z").unwrap();
            if !(lower..upper).contains(&start) {
                return Err("metadata outside declared date".into());
            }
        }
        let mut eligible = source
            .iter()
            .filter(|r| r["payload"].as_str().is_some())
            .collect::<Vec<_>>();
        eligible
            .sort_by_key(|r| hash(&json!(["framelift-field-pilot-v1", mission, r["id"]])).unwrap());
        if eligible.len() < 4 {
            return Err(format!("{mission} has fewer than four downloadable rows"));
        }
        metadata.push(json!({"mission":mission,"source":input::identity(&path)?,"prefix_count":source.len(),"eligible_count":eligible.len()}));
        for row in eligible.into_iter().take(4) {
            let name = match mission {
                "iss" => "field-iss-afsk1200.yml",
                "sonate" => "field-sonate-gmsk9600.yml",
                _ => "field-canvas-gmsk9600.yml",
            };
            rows.push(json!({"mission":mission,"mode":mode,"baud":baud,"metadata":row,
                "profile":input::identity(&Path::new(env!("CARGO_MANIFEST_DIR")).join("configs/protocols").join(name))?}));
        }
    }
    input::write_json_new(
        &root.join("cohort.json"),
        &json!({
            "schema":"framelift-field-audio-cohort-v1","frozen_utc":io::utc(),"rows":rows,"metadata":metadata,
            "selection":"Four available OGG URLs per mission, smallest SHA256 of [framelift-field-pilot-v1,mission,id], from frozen first API page of June1,2026. No outcome selection. Finite page-prefix coverage pilot, not a representative archive sample.",
            "exposure":"unknown","independent_holdout_claim":false,
            "native_config":{"bank":"full","top_timing":16,"window_seconds":6,"hop_seconds":3,"threads":4},
            "arm_timeout_seconds":300,"decoder_protocol":"AX25 strict UI payloads; received FCS for native, decoder attestation for external; external no bit repair",
            "signal_group":"Pre-existing archive waterfall_status=with-signal; unknown is not no signal.",
            "arms":["old_audio","new_audio","direwolf","gr_satellites"],
            "limitations":["New IQ-only mechanisms are not exercised by audio arms", "Native search portfolio differs from external decoder compute", "No population or superiority claim", "Full runtime dependency closure is not sandbox-pinned"]
        }),
    )
}
fn acquire(root: &Path) -> Result<(), String> {
    let cohort_id = input::identity(&root.join("cohort.json"))?;
    let cohort = read(&root.join("cohort.json"))?;
    for row in cohort["rows"].as_array().ok_or("rows")? {
        let id = row["metadata"]["id"].as_u64().ok_or("id")?;
        let dir = root.join(format!("obs-{id}"));
        mkdir(&dir)?;
        if dir.join("acquisition.json").exists() {
            continue;
        }
        check(&row["profile"])?;
        let result = storage_guard(root, 64 * 1024 * 1024).and_then(|_| {
            io::fetch(
                s(&row["metadata"], "payload")?,
                &dir.join("source.ogg"),
                64 * 1024 * 1024,
            )
        });
        let receipt = match result {
            Ok(v) => {
                json!({"status":"completed","download":v,"source":input::identity(&dir.join("source.ogg"))?})
            }
            Err(e) => json!({"status":"failed","error":e}),
        };
        input::write_json_new(&dir.join("acquisition.json"), &receipt)?;
    }
    if input::identity(&root.join("cohort.json"))?.sha256 != cohort_id.sha256 {
        return Err("cohort changed".into());
    }
    Ok(())
}
fn frames(name: &str, dir: &Path) -> Result<Vec<Value>, String> {
    if name.ends_with("audio") {
        let result = read(&dir.join("decode/result.json"))?;
        if result["status"] != "complete" || result["failed_window_count"] != 0 {
            return Err("native partial/failed run".into());
        }
        result["frames"].as_array().ok_or("native frames")?.iter().map(|f|{
            let payload=s(f,"payload_hex")?;let raw=s(f,"frame_with_fcs_hex")?;
            let bytes=hex::decode(raw).map_err(|e|e.to_string())?;
            if !protocol::valid_ax25_fcs(&bytes) || bytes.len()<2 || hex::encode(&bytes[..bytes.len()-2])!=payload || protocol::parse_ax25_ui(&bytes[..bytes.len()-2]).is_none(){return Err("invalid native frame".into());}
            Ok(json!({"payload_hex":payload,"validation":{"type":"received_ax25_fcs","frame_hex":raw}}))
        }).collect()
    } else {
        let data = if name == "direwolf" {
            baselines::parse_direwolf_atest(&String::from_utf8_lossy(
                &fs::read(dir.join("receiver.stdout.log")).map_err(|e| e.to_string())?,
            ))?
        } else {
            baselines::parse_gr_satellites_kiss(
                &fs::read(dir.join("frames.kiss")).map_err(|e| e.to_string())?,
            )?
        };
        Ok(data.strict_ui_payloads.into_iter().map(|payload|json!({"payload_hex":payload,"validation":{"type":"external_decoder_attested","decoder":name}})).collect())
    }
}
fn pcm_hash(path: &Path) -> Result<String, String> {
    let mut wav = hound::WavReader::open(path).map_err(|e| e.to_string())?;
    if wav.spec().channels != 1
        || wav.spec().bits_per_sample != 16
        || wav.spec().sample_format != hound::SampleFormat::Int
    {
        return Err("comparison requires mono signed PCM16".into());
    }
    let mut digest = Sha256::new();
    for sample in wav.samples::<i16>() {
        digest.update(sample.map_err(|e| e.to_string())?.to_le_bytes());
    }
    Ok(hex::encode(digest.finalize()))
}
fn retain_unavailable(dir: &Path, id: u64, stage: &str, evidence: Value) -> Result<Value, String> {
    let row = json!({"id":id,"stage":stage,"status":format!("{stage}_failed"),"evidence":evidence});
    input::write_json_new(&dir.join("unavailable.json"), &row)?;
    Ok(row)
}
fn rebaseline(study: &Value, baseline: &str) -> Result<Value, String> {
    let mut arms = vec![s(study, "baseline_arm")?.to_owned()];
    arms.extend(
        study["candidate_arms"]
            .as_array()
            .ok_or("candidate arms missing")?
            .iter()
            .map(|v| {
                v.as_str()
                    .map(str::to_owned)
                    .ok_or("invalid candidate name")
            })
            .collect::<Result<Vec<_>, _>>()?,
    );
    if !arms.iter().any(|name| name == baseline) {
        return Err("baseline is not a registered arm".into());
    }
    let mut result = study.clone();
    result["baseline_arm"] = json!(baseline);
    result["candidate_arms"] = json!(
        arms.into_iter()
            .filter(|name| name != baseline)
            .collect::<Vec<_>>()
    );
    Ok(result)
}
fn run(
    root: &Path,
    old: &Path,
    new: &Path,
    run_name: &str,
    pcm_root: Option<&Path>,
) -> Result<(), String> {
    if run_name.is_empty()
        || !run_name
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b == b'-')
    {
        return Err("invalid run directory name".into());
    }
    let out = root.join(run_name);
    fs::create_dir(&out).map_err(|e| format!("fresh run directory required: {e}"))?;
    let cohort = read(&root.join("cohort.json"))?;
    let runner_guard = Guard::open(&std::env::current_exe().map_err(|e| e.to_string())?)?;
    let cohort_guard = Guard::open(&root.join("cohort.json"))?;
    let ffmpeg_guard = Guard::open(Path::new("/usr/bin/ffmpeg"))?;
    let runtimes = [
        ("old_audio", old),
        ("new_audio", new),
        ("direwolf", Path::new("/usr/bin/atest")),
        ("gr_satellites", Path::new("/usr/bin/gr_satellites")),
    ];
    let identities = runtimes
        .iter()
        .map(|(n, p)| Ok((n.to_string(), json!(input::identity(p)?))))
        .collect::<Result<std::collections::BTreeMap<_, _>, String>>()?;
    let runtime_guards = runtimes
        .iter()
        .map(|(_, p)| Guard::open(p))
        .collect::<Result<Vec<_>, _>>()?;
    let verify_runtime = || -> Result<(), String> {
        runner_guard.verify()?;
        cohort_guard.verify()?;
        ffmpeg_guard.verify()?;
        for guard in &runtime_guards {
            guard.verify()?;
        }
        Ok(())
    };
    let pcm_base = pcm_root.unwrap_or(&out).join("pcm");
    fs::create_dir(&pcm_base).map_err(|e| e.to_string())?;
    input::write_json_new(
        &out.join("freeze.json"),
        &json!({"frozen_utc":io::utc(),"cohort":cohort_guard.identity(),"runner":runner_guard.identity(),"runtimes":identities,"ffmpeg":ffmpeg_guard.identity(),"runtime_scope":"held-descriptor guards for executables and explicit profiles; installed dynamic dependency closure not sealed", "amendment":"v2 uses a metadata-free bitexact WAV container. Identical PCM is proven against v1 where available. All 12 observations and all four arms are repeated without DSP changes.", "pcm_workspace":pcm_base,"pcm_workspace_is_volatile":pcm_root.is_some(),"maximum_aggregate_pcm_bytes":524288000}),
    )?;
    let mut observations = Vec::new();
    let mut unavailable = Vec::new();
    let mut pcm_bytes = 0u64;
    for row in cohort["rows"].as_array().ok_or("rows")? {
        let id = row["metadata"]["id"].as_u64().ok_or("id")?;
        let acquired = root.join(format!("obs-{id}"));
        let dir = out.join(format!("obs-{id}"));
        mkdir(&dir)?;
        let acquisition = read(&acquired.join("acquisition.json"))?;
        // An unavailable input cannot truthfully be given a fabricated input hash.
        if acquisition["status"] != "completed" {
            unavailable.push(retain_unavailable(&dir, id, "acquisition", acquisition)?);
            continue;
        }
        check(&acquisition["source"])?;
        check(&row["profile"])?;
        let source_guard = Guard::open(&acquired.join("source.ogg"))?;
        let profile_guard = Guard::open(Path::new(s(&row["profile"], "path")?))?;
        verify_runtime()?;
        let wav = pcm_base.join(format!("obs-{id}.wav"));
        let args = vec![
            "-nostdin".into(),
            "-v".into(),
            "error".into(),
            "-i".into(),
            acquired.join("source.ogg").display().to_string(),
            "-map".into(),
            "0:a:0".into(),
            "-c:a".into(),
            "pcm_s16le".into(),
            "-fflags".into(),
            "+bitexact".into(),
            "-flags:a".into(),
            "+bitexact".into(),
            "-map_metadata".into(),
            "-1".into(),
            wav.display().to_string(),
        ];
        storage_guard(
            root,
            if pcm_root.is_some() {
                16 * 1024 * 1024
            } else {
                128 * 1024 * 1024
            },
        )?;
        let converted = io::execute(
            Path::new("/usr/bin/ffmpeg"),
            &args,
            &dir,
            "convert",
            120,
            512 * 1024 * 1024,
        )?;
        if let Err(error) = io::require_success(&converted) {
            unavailable.push(retain_unavailable(
                &dir,
                id,
                "conversion",
                json!({"error":error,"source":source_guard.identity()}),
            )?);
            continue;
        }
        source_guard.verify()?;
        profile_guard.verify()?;
        verify_runtime()?;
        let pcm_guard = Guard::open(&wav)?;
        let wav_id = input::identity(&wav)?;
        pcm_bytes += wav_id.bytes;
        if pcm_bytes > 524_288_000 {
            return Err("aggregate PCM workspace exceeds the 500 MiB bound".into());
        }
        let sample_digest = pcm_hash(&wav)?;
        let prior = root
            .join("audio-run")
            .join(format!("obs-{id}/common-pcm16.wav"));
        let prior_digest = if prior.is_file() {
            Some(pcm_hash(&prior)?)
        } else {
            None
        };
        if prior_digest.as_ref().is_some_and(|d| d != &sample_digest) {
            return Err("container amendment changed PCM samples".into());
        }
        input::write_json_new(
            &dir.join("pcm-evidence.json"),
            &json!({"container":wav_id,"pcm16_le_sha256":sample_digest,"prior_v1_pcm16_le_sha256":prior_digest,"numeric_parity_with_v1":prior_digest.is_some(),"volatile_workspace":pcm_root.is_some()}),
        )?;
        let audio = hound::WavReader::open(&wav).map_err(|e| e.to_string())?;
        if audio.spec().channels != 1 {
            return Err("mono common input required".into());
        }
        let duration = audio.duration() as f64 / audio.spec().sample_rate as f64;
        let mut arms = Vec::new();
        for (name, program) in runtimes {
            check(&identities[name])?;
            verify_runtime()?;
            source_guard.verify()?;
            profile_guard.verify()?;
            pcm_guard.verify()?;
            let arm_dir = dir.join(name);
            mkdir(&arm_dir)?;
            let baud = row["baud"].as_u64().ok_or("baud")?.to_string();
            let args = if name.ends_with("audio") {
                vec![
                    "decode-audio".into(),
                    "--input".into(),
                    wav.display().to_string(),
                    "--output".into(),
                    arm_dir.join("decode").display().to_string(),
                    "--mode".into(),
                    s(row, "mode")?.into(),
                    "--baud".into(),
                    baud,
                    "--bank".into(),
                    "full".into(),
                    "--top-timing".into(),
                    "16".into(),
                    "--threads".into(),
                    "4".into(),
                ]
            } else if name == "direwolf" {
                vec![
                    "-B".into(),
                    baud,
                    "-F".into(),
                    "0".into(),
                    "-h".into(),
                    wav.display().to_string(),
                ]
            } else {
                vec![
                    s(&row["profile"], "path")?.into(),
                    "--wavfile".into(),
                    wav.display().to_string(),
                    "--kiss_out".into(),
                    arm_dir.join("frames.kiss").display().to_string(),
                    "--hexdump".into(),
                ]
            };
            let receipt =
                io::execute(program, &args, &arm_dir, "receiver", 300, 512 * 1024 * 1024)?;
            check(&identities[name])?;
            check(&row["profile"])?;
            verify_runtime()?;
            source_guard.verify()?;
            profile_guard.verify()?;
            pcm_guard.verify()?;
            if input::identity(&wav)?.sha256 != wav_id.sha256 {
                return Err("shared waveform changed".into());
            }
            let decoded = if receipt["success"] == true {
                frames(name, &arm_dir)
            } else {
                Err(format!(
                    "process exit {}; timeout {}",
                    receipt["returncode"], receipt["timed_out"]
                ))
            };
            let (status, accepted, error) = match decoded {
                Ok(f) => ("completed", f, Value::Null),
                Err(e) => (
                    if receipt["timed_out"] == true {
                        "timed_out"
                    } else {
                        "failed"
                    },
                    vec![],
                    json!(e),
                ),
            };
            let cpu = receipt["user_cpu_seconds"]
                .as_f64()
                .zip(receipt["system_cpu_seconds"].as_f64())
                .map(|(u, s)| u + s);
            let arm = json!({"name":name,"status":status,"input_sha256":wav_id.sha256,"runtime_sha256":identities[name]["sha256"],"profile_sha256":hash(&args)?,"wall_seconds":receipt["wall_seconds"],"cpu_seconds":cpu,"error":error,"frames":accepted});
            input::write_json_new(&arm_dir.join("arm.json"), &arm)?;
            arms.push(arm);
        }
        let label = row["metadata"]["waterfall_status"].as_str();
        let confirmed = match label {
            Some("with-signal") => Some(true),
            Some("without-signal") => Some(false),
            _ => None,
        };
        let observation = json!({"id":id.to_string(),"mission":row["mission"],"station":row["metadata"]["ground_station"],"pass_group":format!("{}-20260601",s(row,"mission")?),"input_sha256":wav_id.sha256,"exposure":"unknown","confirmed_signal":confirmed,"signal_evidence":confirmed.map(|_|format!("Frozen archive waterfall_status {}; cohort SHA256 {}",label.unwrap(),input::identity(&root.join("cohort.json")).unwrap().sha256)),"negative_control":false,"duration_seconds":duration,"arms":arms});
        input::write_json_new(&dir.join("observation.json"), &observation)?;
        observations.push(observation);
    }
    let study = json!({"schema":"framelift-recovery-study-v1","baseline_arm":"old_audio","candidate_arms":["new_audio","direwolf","gr_satellites"],"observations":observations});
    input::write_json_new(&out.join("study.json"), &study)?;
    for baseline in ["direwolf", "gr_satellites"] {
        input::write_json_new(
            &out.join(format!("study-vs-{baseline}.json")),
            &rebaseline(&study, baseline)?,
        )?;
    }
    let unique = observations
        .iter()
        .map(|r| r["id"].clone())
        .map(|v| v.to_string())
        .collect::<BTreeSet<_>>();
    input::write_json_new(
        &out.join("denominator.json"),
        &json!({"selected":12,"observations_with_common_input":unique.len(),"unavailable_count":unavailable.len(),"unavailable":unavailable,"claims":{"representative":false,"independent_holdout":false,"new_iq_algorithms_tested":false}}),
    )
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn rebaseline_preserves_all_receipts_and_registered_arms() {
        let original = json!({"baseline_arm":"old","candidate_arms":["new","direwolf","gr"],"observations":[{"id":"1","arms":["old","new","direwolf","gr"]}]});
        let result = rebaseline(&original, "direwolf").unwrap();
        assert_eq!(result["observations"], original["observations"]);
        assert_eq!(result["candidate_arms"], json!(["old", "new", "gr"]));
        assert!(rebaseline(&original, "absent").is_err());
    }
    fn wave(path: &Path, samples: &[i16]) {
        let mut w = hound::WavWriter::create(
            path,
            hound::WavSpec {
                channels: 1,
                sample_rate: 48000,
                bits_per_sample: 16,
                sample_format: hound::SampleFormat::Int,
            },
        )
        .unwrap();
        for s in samples {
            w.write_sample(*s).unwrap();
        }
        w.finalize().unwrap();
    }
    #[test]
    fn list_metadata_does_not_change_pcm_identity() {
        let d = tempfile::tempdir().unwrap();
        let clean = d.path().join("clean.wav");
        let listed = d.path().join("listed.wav");
        wave(&clean, &[-32768, 0, 17, 32767, -12]);
        let mut bytes = fs::read(&clean).unwrap();
        let pos = bytes.windows(4).position(|b| b == b"data").unwrap();
        bytes.splice(pos..pos, b"LIST\x04\x00\x00\x00INFO".iter().copied());
        let size = (bytes.len() - 8) as u32;
        bytes[4..8].copy_from_slice(&size.to_le_bytes());
        fs::write(&listed, bytes).unwrap();
        assert_ne!(
            input::identity(&clean).unwrap().sha256,
            input::identity(&listed).unwrap().sha256
        );
        assert_eq!(pcm_hash(&clean).unwrap(), pcm_hash(&listed).unwrap());
    }
    #[test]
    fn numerical_pcm_change_is_detected() {
        let d = tempfile::tempdir().unwrap();
        let a = d.path().join("a.wav");
        let b = d.path().join("b.wav");
        wave(&a, &[0, 1, -2]);
        wave(&b, &[0, 1, -3]);
        assert_ne!(pcm_hash(&a).unwrap(), pcm_hash(&b).unwrap());
    }
    #[test]
    fn conversion_failure_is_retained_without_fake_frames_or_input_hash() {
        let d = tempfile::tempdir().unwrap();
        let row =
            retain_unavailable(d.path(), 42, "conversion", json!({"error":"invalid OGG"})).unwrap();
        assert_eq!(read(&d.path().join("unavailable.json")).unwrap(), row);
        assert_eq!(row["status"], "conversion_failed");
        assert_eq!(row["id"], 42);
        assert!(row.get("frames").is_none());
        assert!(row.get("input_sha256").is_none());
        assert!(retain_unavailable(d.path(), 42, "conversion", json!({})).is_err());
    }
}
fn main() {
    let result = match Args::parse().command {
        Action::Freeze { output } => freeze(&output),
        Action::Acquire { output } => acquire(&output),
        Action::Run {
            output,
            old,
            new,
            run_name,
            pcm_root,
        } => run(&output, &old, &new, &run_name, pcm_root.as_deref()),
    };
    if let Err(e) = result {
        eprintln!("{e}");
        std::process::exit(1);
    }
}
