//! Frozen, resumable five-receiver benchmark. No target/reference data are used.
#[path = "support/today20_baselines.rs"]
mod baselines;
#[path = "support/innovation_download_receipt.rs"]
mod download_receipt;
#[allow(dead_code)]
#[path = "support/holdout_run_io.rs"]
mod io;
#[path = "support/native_result_json.rs"]
mod native_result_json;

use clap::Parser;
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::{
    collections::{BTreeMap, BTreeSet},
    fs::{self, OpenOptions},
    os::{fd::AsRawFd, unix::fs::OpenOptionsExt},
    path::{Path, PathBuf},
    sync::atomic::{AtomicUsize, Ordering},
    time::{Duration, Instant},
};
use telemetry_yield_rs::{input, protocol};

const V1: &str =
    "/home/ubuntu/telemetry-yield/work/innovation-field-20260910-v1/bin/innovation_audio_probe";
const V1_SHA: &str = "47493db215ba9e42e922524f13839596b02a977864802807362acbf23a4a59d0";
// Compile-time paired overrides produce a different frozen runner. Runtime
// environment changes cannot switch a saved campaign to another receiver.
const V3: &str = match option_env!("TELEMETRY_BENCHMARK_PROGRESSIVE_PATH") {
    Some(value) => value,
    None => "/home/ubuntu/telemetry-yield/work/progressive-20260910-v3/bin/telemetry-yield-rs",
};
const V3_SHA: &str = match option_env!("TELEMETRY_BENCHMARK_PROGRESSIVE_SHA256") {
    Some(value) => value,
    None => "633f1f4ecaf7d4617f46fda83bf0866223bc123fa5013e3146cc3e32edefbb91",
};
const PROTOCOL_PATH: &str = match option_env!("TELEMETRY_BENCHMARK_PROTOCOL_PATH") {
    Some(value) => value,
    None => "/home/ubuntu/telemetry-yield/docs/innovation-benchmark-protocol-20260910.md",
};
const BASE_PROTOCOL_PATH: &str =
    "/home/ubuntu/telemetry-yield/docs/innovation-benchmark-protocol-20260910.md";
const ANALYSIS_PROTOCOL_PATH: &str =
    "/home/ubuntu/telemetry-yield/docs/innovation-prospective-analysis-20260911.md";
const SELECTION_REGISTRATION_PATH: &str =
    "/home/ubuntu/telemetry-yield/work/innovation-prospective-20260912-v2/protocol.json";
const SELECTION_AMENDMENT_PATH: &str = "/home/ubuntu/telemetry-yield/work/innovation-prospective-20260912-v2/pass-dependence-amendment-v2.json";
const GRSAT: &str = "/home/ubuntu/telemetry-yield/work/golden/env/bin/gr_satellites";
const REPAIRED_BUILD: bool = option_env!("TELEMETRY_BENCHMARK_PROGRESSIVE_PATH").is_some();
const REPAIRED_PROGRESSIVE_SHA: &str =
    "e59e8dac6991984de1b7b1e7238835dd3ad740150ae5004070c215290ee2fda3";
const REPAIRED_INNOVATION_SHA: &str =
    "638066b9c3edee6227373aa7919c4ee8d9705ca4ad89426f30398987b6e5042a";
// A new immutable runner is required for the lossless restart. Never allow a
// runtime environment variable to replace a receiver in an existing campaign.
const RECEIVER_REVISION: Option<&str> = option_env!("TELEMETRY_BENCHMARK_RECEIVER_REVISION");
fn repaired_receiver_hashes(revision: Option<&str>) -> Result<(&'static str, &'static str), String> {
    match revision {
        None => Ok((REPAIRED_PROGRESSIVE_SHA, REPAIRED_INNOVATION_SHA)),
        Some("lossless-20260912-v1") => Ok((
            "cd2c8adcf9db34e5f177753357e7915ac29189816ce73e11e90814c0f3d96acf",
            "c406199366ecccef6ce14d4c13600d8cd6e3019ac9a69921430d80e59424960b",
        )),
        Some(_) => Err("unknown compiled receiver revision".into()),
    }
}
const MANIFEST_SCHEMA: &str = "innovation-benchmark-manifest-v3";
const ARM_SCHEMA: &str = "innovation-benchmark-arm-v3";
const STAGES: [&str; 7] = [
    "quick",
    "early-nearest",
    "baseline-remainder",
    "blind",
    "early-multi",
    "nearest",
    "multi-anchor",
];
const ARMS: [&str; 5] = [
    "innovation_v2",
    "innovation_v1_no_codec",
    "progressive_v3",
    "direwolf",
    "gr_satellites",
];
const PROFILE: &str = "name: Frozen benchmark FSK9600\nnorad: 0\ndata:\n  &raw Raw: unknown\ntransmitters:\n  selected:\n    frequency: 437000000\n    modulation: FSK\n    baudrate: 9600\n    framing: AX.25 G3RUH\n    data:\n    - *raw\n";

#[derive(Parser)]
struct Args {
    #[arg(long)]
    acquisition: PathBuf,
    #[arg(long)]
    output: PathBuf,
    #[arg(long)]
    improved: PathBuf,
    #[arg(long, default_value_t = 4)]
    workers: usize,
    #[arg(long, default_value_t = 2)]
    threads: usize,
    #[arg(long, default_value_t = 900)]
    decoder_seconds: u64,
    #[arg(long, default_value_t = 120)]
    acquisition_wait_seconds: u64,
    /// Operational milestone only. Selection and receiver configuration stay frozen.
    #[arg(long)]
    max_new_observations: Option<usize>,
    #[arg(long)]
    resume: bool,
    #[arg(long)]
    retry_failed: bool,
    #[arg(long)]
    analyze_only: bool,
    /// Required for repaired builds; a candidate cohort is not a release.
    #[arg(long)]
    release_receipt: Option<PathBuf>,
    /// Refreshed admission check on resume, for the same immutable cohort/exposure.
    #[arg(long)]
    current_exposure_check: Option<PathBuf>,
    /// Verify metadata/runtime/release only; never read waveform or start jobs.
    #[arg(long)]
    preflight_only: bool,
}

fn verify(expected: &Value) -> Result<(), String> {
    let id: input::Identity =
        serde_json::from_value(expected.clone()).map_err(|e| e.to_string())?;
    let actual = input::identity(Path::new(&id.path))?;
    if actual.sha256 != id.sha256 || actual.bytes != id.bytes {
        return Err(format!("artifact changed: {}", id.path));
    }
    Ok(())
}
fn same_bytes(a: &Value, b: &Value) -> bool {
    a["sha256"].as_str().is_some() && a["sha256"] == b["sha256"] && a["bytes"] == b["bytes"]
}
fn fcs(frame: &[u8]) -> bool {
    if frame.len() < 2 {
        return false;
    }
    let mut crc = 0xffffu16;
    for &byte in &frame[..frame.len() - 2] {
        crc ^= u16::from(byte);
        for _ in 0..8 {
            crc = if crc & 1 != 0 {
                (crc >> 1) ^ 0x8408
            } else {
                crc >> 1
            };
        }
    }
    (crc ^ 0xffff) == u16::from_le_bytes([frame[frame.len() - 2], frame[frame.len() - 1]])
}
fn native_set(value: &Value) -> Result<BTreeSet<String>, String> {
    let mut result = BTreeSet::new();
    for item in value.as_array().ok_or("native frame list absent")? {
        let frame =
            hex::decode(item.as_str().ok_or("frame not hex string")?).map_err(|e| e.to_string())?;
        if !fcs(&frame) || protocol::parse_ax25_ui(&frame[..frame.len() - 2]).is_none() {
            return Err(
                "frame failed independent received FCS / strict AX.25 UI validation".into(),
            );
        }
        result.insert(hex::encode(&frame[..frame.len() - 2]));
    }
    Ok(result)
}
fn string_set(value: &Value) -> Result<BTreeSet<String>, String> {
    value
        .as_array()
        .ok_or("PDU array absent")?
        .iter()
        .map(|v| {
            v.as_str()
                .map(str::to_owned)
                .ok_or_else(|| "PDU string absent".into())
        })
        .collect()
}
fn diff(a: &BTreeSet<String>, b: &BTreeSet<String>) -> Vec<String> {
    a.difference(b).cloned().collect()
}

fn runtime_digest(manifest: &Value) -> Result<String, String> {
    let runtime = manifest["runtime"].as_array().ok_or("runtime absent")?;
    Ok(hex::encode(Sha256::digest(
        serde_json::to_vec(runtime).map_err(|e| e.to_string())?,
    )))
}
fn verify_runtime(manifest: &Value) -> Result<(), String> {
    for artifact in manifest["runtime"].as_array().ok_or("runtime absent")? {
        verify(artifact)?;
    }
    Ok(())
}
fn selected_runtime_digest(manifest: &Value) -> Result<String, String> {
    let profile = &manifest["profile"];
    let mut entries = manifest["runtime"]
        .as_array()
        .ok_or("runtime absent")?
        .clone();
    let mut profile_count = 0;
    for entry in &mut entries {
        if entry == profile {
            // Fresh output directories change only this generated path, never
            // the compiled SatYAML content or any actual installed module path.
            entry["path"] = "generated:grsat-profile.yml".into();
            profile_count += 1;
        }
    }
    if profile_count != 1 {
        return Err("selected runtime must contain exact generated profile identity once".into());
    }
    entries.sort_by(|a, b| a["path"].as_str().cmp(&b["path"].as_str()));
    Ok(hex::encode(Sha256::digest(
        serde_json::to_vec(&entries).map_err(|e| e.to_string())?,
    )))
}
fn full_policy() -> Value {
    json!({"baud":9600.0,"blind":true,"multi_anchor":true,"window_seconds":6.0,"hop_seconds":3.0,
        "fast_timings":8,"fast_gardner":2,"protocol":"AX25_UI_received_FCS_plain_or_G3RUH","version":2,
        "anchor_generations":"frozen quick prefix for exploratory tasks; frozen complete baseline for final tasks"})
}
fn checked_task(bytes: &[u8]) -> Result<Value, String> {
    let wrapper: Value = serde_json::from_slice(bytes).map_err(|e| e.to_string())?;
    let digest = wrapper["sha256"].as_str().ok_or("task digest absent")?;
    let prefix = format!("{{\"sha256\":\"{digest}\",\"task\":");
    if !bytes.starts_with(prefix.as_bytes()) || !bytes.ends_with(b"}") {
        return Err("noncanonical task commitment".into());
    }
    let inner = &bytes[prefix.len()..bytes.len() - 1];
    if hex::encode(Sha256::digest(inner)) != digest {
        return Err("task commitment digest mismatch".into());
    }
    let decoded: Value = serde_json::from_slice(inner).map_err(|e| e.to_string())?;
    if decoded != wrapper["task"] {
        return Err("task wrapper/inner disagreement".into());
    }
    Ok(wrapper["task"].clone())
}
fn progressive_evidence(dir: &Path, result: &Value, pcm: &Value) -> Result<Vec<Value>, String> {
    let manifest_path = dir.join("decode/manifest.json");
    let manifest_id = input::identity(&manifest_path)?;
    let m = input::read_json(&manifest_path)?;
    if m["schema"] != "progressive-audio-session-v2"
        || m["executable_sha256"] != V3_SHA
        || m["policy"] != full_policy()
        || !same_bytes(&m["audio"]["source"], pcm)
        || !same_bytes(&m["audio"]["wav"], pcm)
        || !m["audio"]["conversion_command"].is_null()
        || m["audio"]["sample_rate"] != 48000
        || result["schema"] != "progressive-audio-result-v1"
        || result["complete"] != true
        || result["status"] != "complete"
        || result["session_sha256"] != manifest_id.sha256
    {
        return Err("progressive schema/completion/session/full policy/input mismatch".into());
    }
    let samples = m["audio"]["samples"]
        .as_u64()
        .filter(|n| (9600..=86_400_000).contains(n))
        .ok_or("progressive sample geometry absent")?;
    if pcm["samples"].as_u64() != Some(samples)
        || pcm["sample_rate"] != 48000
        || pcm["bits_per_sample"] != 16
    {
        return Err("progressive common PCM geometry mismatch".into());
    }
    // Stop at the first window reaching EOF, retaining that partial tail.
    let windows = if samples <= 288_000 {
        1
    } else {
        (samples - 288_000).div_ceil(144_000) + 1
    };
    let total = windows * STAGES.len() as u64;
    if result["window_count"].as_u64() != Some(windows)
        || result["total_tasks"].as_u64() != Some(total)
        || result["completed_tasks"].as_u64() != Some(total)
        || result["stage_counts"]
            .as_object()
            .is_none_or(|x| x.len() != STAGES.len())
    {
        return Err("progressive full task geometry incomplete".into());
    }
    let mut artifacts = vec![json!(manifest_id)];
    let mut union = BTreeSet::new();
    let mut paths = fs::read_dir(dir.join("decode/tasks"))
        .map_err(|e| e.to_string())?
        .map(|e| e.map(|e| e.path()).map_err(|e| e.to_string()))
        .collect::<Result<Vec<_>, _>>()?;
    paths.sort();
    let mut expected = BTreeSet::new();
    for stage in STAGES {
        if result["stage_counts"][stage].as_u64() != Some(windows) {
            return Err("progressive stage count mismatch".into());
        }
        let mut stage_union = BTreeSet::new();
        for window in 0..windows {
            let path = dir.join(format!("decode/tasks/{stage}-{window:06}.json"));
            if !fs::symlink_metadata(&path)
                .map_err(|e| e.to_string())?
                .file_type()
                .is_file()
            {
                return Err("task commitment must be a real regular file".into());
            }
            expected.insert(path.clone());
            let id = input::identity(&path)?;
            let raw = input::read_bytes_bounded(&path, 64 * 1024 * 1024)?;
            if id.sha256 != hex::encode(Sha256::digest(&raw)) {
                return Err("task changed during verification".into());
            }
            let task = checked_task(&raw)?;
            if task["schema"] != "progressive-audio-task-v1"
                || task["session_sha256"] != manifest_id.sha256
                || task["stage"] != stage
                || task["window"].as_u64() != Some(window)
                || task["elapsed_seconds"]
                    .as_f64()
                    .is_none_or(|x| !x.is_finite() || x < 0.0)
            {
                return Err("progressive task identity/geometry mismatch".into());
            }
            stage_union.extend(native_set(&task["frame_with_fcs_hex"])?);
            artifacts.push(json!(id));
        }
        if native_set(&result["stage_frame_with_fcs_hex"][stage])? != stage_union {
            return Err("progressive stage union mismatch".into());
        }
        union.extend(stage_union);
    }
    if paths.into_iter().collect::<BTreeSet<_>>() != expected
        || union != native_set(&result["frame_with_fcs_hex"])?
    {
        return Err("progressive unexpected tasks or full union mismatch".into());
    }
    Ok(artifacts)
}

fn invocation(
    name: &str,
    dir: &Path,
    pcm: &Value,
    source: &Value,
    manifest: &Value,
) -> Result<(PathBuf, Vec<String>), String> {
    let program = PathBuf::from(
        manifest["receiver_bindings"][name]["path"]
            .as_str()
            .ok_or("receiver binding absent")?,
    );
    let wav = pcm["path"].as_str().ok_or("PCM path absent")?.to_owned();
    let threads = manifest["threads"].as_u64().ok_or("threads absent")?;
    let seconds = manifest["decoder_seconds"]
        .as_u64()
        .filter(|x| *x >= 60)
        .ok_or("receiver bound absent")?;
    let mut command = vec![
        "--input".into(),
        wav.clone(),
        "--output".into(),
        dir.join("decode").display().to_string(),
        "--threads".into(),
        threads.to_string(),
    ];
    match name {
        "innovation_v2" => command.extend([
            "--codec-sideinfo".into(),
            "--pooled-codec".into(),
            "--codec-source".into(),
            source["path"].as_str().ok_or("codec source absent")?.into(),
        ]),
        "innovation_v1_no_codec" => {}
        "progressive_v3" => {
            command.insert(0, "decode-progressive".into());
            command.extend([
                "--mode".into(),
                "full".into(),
                "--budget-ms".into(),
                ((seconds - 20) * 1000).to_string(),
            ]);
        }
        "direwolf" => {
            command = vec![
                "-B".into(),
                "9600".into(),
                "-F".into(),
                "0".into(),
                "-h".into(),
                wav,
            ]
        }
        "gr_satellites" => {
            command = vec![
                manifest["profile"]["path"]
                    .as_str()
                    .ok_or("profile absent")?
                    .into(),
                "--wavfile".into(),
                wav,
                "--kiss_out".into(),
                dir.join("frames.kiss").display().to_string(),
                "--hexdump".into(),
            ]
        }
        _ => return Err("unknown receiver arm".into()),
    }
    if !program.is_absolute() {
        return Err("receiver path must be absolute".into());
    }
    Ok((program, command))
}
fn process_evidence(
    name: &str,
    dir: &Path,
    pcm: &Value,
    source: &Value,
    manifest: &Value,
    process: &Value,
) -> Result<(), String> {
    let (program, command) = invocation(name, dir, pcm, source, manifest)?;
    let saved = input::read_json(&dir.join("run.process.json"))?;
    if &saved != process
        || process["success"] != true
        || process["returncode"] != 0
        || process["timed_out"] != false
        || process["program"] != json!(program)
        || process["args"] != json!(command)
    {
        return Err("successful exact frozen process invocation not attested".into());
    }
    for (field, filename) in [("stdout", "run.stdout.log"), ("stderr", "run.stderr.log")] {
        if process[field] != json!(input::identity(&dir.join(filename))?) {
            return Err("process log binding mismatch".into());
        }
    }
    if process["usage_path"] != json!(dir.join("run.rusage.txt")) {
        return Err("rusage path mismatch".into());
    }
    for field in [
        "wall_seconds",
        "user_cpu_seconds",
        "system_cpu_seconds",
        "maximum_rss_kib",
    ] {
        if process[field]
            .as_f64()
            .is_none_or(|x| !x.is_finite() || x < 0.0)
        {
            return Err("invalid completed process resources".into());
        }
    }
    if process["environment"]
        != json!({"GR_SATELLITES_SUBMIT_TLM":"0","OPENBLAS_NUM_THREADS":"1","OMP_NUM_THREADS":"1","MKL_NUM_THREADS":"1"})
    {
        return Err("process environment attestation mismatch".into());
    }
    Ok(())
}

fn score_native(
    arm: &str,
    dir: &Path,
    pcm: &Value,
    source: &Value,
    frozen: &Value,
) -> Result<Value, String> {
    let result_path = dir.join("decode/result.json");
    let result = native_result_json::read_native_report(&result_path)?;
    let mut artifacts = vec![json!(input::identity(&result_path)?)];
    let frames = if arm == "progressive_v3" {
        artifacts.extend(progressive_evidence(dir, &result, pcm)?);
        &result["frame_with_fcs_hex"]
    } else {
        let plan_path = dir.join("decode/plan.json");
        let plan = input::read_json(&plan_path)?;
        let v2 = arm == "innovation_v2";
        let config = json!({"baud":9600.0,"threads":frozen["threads"]});
        if result["schema"]
            != if v2 {
                "innovation-audio-result-v2"
            } else {
                "innovation-audio-result-v1"
            }
            || plan["schema"]
                != if v2 {
                    "innovation-audio-plan-v2"
                } else {
                    "innovation-audio-plan-v1"
                }
            || result["status"] != "complete"
            || !same_bytes(&result["source"], pcm)
            || plan["executable"] != frozen["receiver_bindings"][arm]
            || !same_bytes(&plan["source"], pcm)
            || !same_bytes(&plan["input"], pcm)
            || plan["config"] != config
            || result["config"] != config
            || plan["codec_sideinfo"] != v2
            || (v2 && (plan["pooled_codec"] != true || result["pooled_codec"] != true))
            || (!v2
                && (!matches!(plan["pooled_codec"], Value::Null | Value::Bool(false))
                    || !matches!(result["pooled_codec"], Value::Null | Value::Bool(false))))
            || !plan["conversion_command"].is_null()
            || plan["reference_bytes_used_for_search"] != false
            || plan["crc_guided_bit_repair"] != false
            || plan["target_truth_used_for_training"] != false
            || plan["recursive_training"] != false
            || result["sample_rate_hz"] != 48000
            || result["samples"] != pcm["samples"]
            || pcm["sample_rate"] != 48000
            || pcm["bits_per_sample"] != 16
            || result["samples"]
                .as_u64()
                .is_none_or(|n| !(9600..=86_400_000).contains(&n))
        {
            return Err("innovation completion / shared input identity mismatch".into());
        }
        artifacts.push(json!(input::identity(&plan_path)?));
        if arm == "innovation_v2" {
            // The improved file entry point additionally binds the original codec
            // source and checks that its PCM16 samples equal this common input.
            let binding = &result["codec_input_binding"];
            if binding["kind"] != "canonical_pcm16_wav_byte_identity"
                || !same_bytes(&binding["codec_source"], source)
                || !same_bytes(&binding["analysis_input"], pcm)
                || !same_bytes(&binding["derived_pcm16"], pcm)
                || binding["same_numeric_input_verified"] != true
                || result["pooled_codec"] != true
                || plan["codec_input_binding"] != *binding
            {
                return Err(
                    "v2 original-codec source / common numeric input attestation mismatch".into(),
                );
            }
            let metadata_path = dir.join("decode/codec-metadata.json");
            let metadata = input::read_json(&metadata_path)?;
            if metadata["schema"] != "vorbis-packet-reliability-evidence-v1"
                || metadata["sample_rate"] != 48000
                || metadata["decoded_samples"] != result["samples"]
                || !same_bytes(&metadata["source"], source)
            {
                return Err("codec metadata original source/geometry mismatch".into());
            }
            artifacts.push(json!(input::identity(&metadata_path)?));
        } else if !result["codec_input_binding"].is_null() || !plan["codec_input_binding"].is_null()
        {
            return Err("no-codec arm contains codec binding".into());
        }
        &result["report"]["union_full_frames"]
    };
    let pdus = native_set(frames)?;
    if result["union_count"].as_u64() != Some(pdus.len() as u64) {
        return Err("native reported union count differs from verified exact PDU set".into());
    }
    let mut scored = json!({"strict_ui_payloads":pdus,"all_payloads":pdus,"strict_ui_unique_count":pdus.len(),
        "received_fcs_present":true,"independent_fcs_verified":true,"artifacts":artifacts,
        "codec_fits_accepted":result["codec_fits_accepted"],"pooled_fits_accepted":result["pooled_fits_accepted"],
        "crc_evidence":"bitwise received FCS rechecked, strict AX.25 UI; not source authentication"});
    if arm == "innovation_v2" {
        let previous = native_set(&result["report"]["previous_innovation_union_full_frames"])?;
        let pooled = native_set(&result["report"]["lane_full_frames"]["pooled-codec-innovations"])?;
        let matched = native_set(&result["report"]["pooled_matched_unweighted_full_frames"])?;
        if !previous.is_subset(&pdus)
            || native_set(&result["report"]["added_vs_previous_innovation"])?
                != pdus.difference(&previous).cloned().collect()
            || native_set(&result["report"]["pooled_added_vs_matched_unweighted"])?
                != pooled.difference(&matched).cloned().collect()
            || native_set(&result["report"]["pooled_lost_vs_matched_unweighted"])?
                != matched.difference(&pooled).cloned().collect()
        {
            return Err("v2 internal ablation sets are inconsistent".into());
        }
        scored["ablation"] = json!({"added_vs_same_run_previous":diff(&pdus,&previous),
            "pooled_added_vs_matched_unweighted":diff(&pooled,&matched),"pooled_lost_vs_matched_unweighted":diff(&matched,&pooled)});
    }
    Ok(scored)
}

/// Read-only recovery of a completed native decoder rejected solely by the old
/// report-size bound. Preserve the original campaign/attempt; never rerun DSP.
pub(crate) fn recover_large_native_report(observation_result: &Path) -> Result<Value, String> {
    let original_id = json!(input::identity(observation_result)?);
    let observation = input::read_json(observation_result)?;
    let root = observation_result
        .parent()
        .ok_or("observation directory absent")?
        .canonicalize()
        .map_err(|e| e.to_string())?;
    let manifest_path = root
        .parent()
        .ok_or("campaign directory absent")?
        .join("manifest.json");
    let manifest_id = json!(input::identity(&manifest_path)?);
    let manifest = input::read_json(&manifest_path)?;
    let old = &observation["decoders"]["innovation_v2"];
    if observation["status"] != "incomplete"
        || old["schema"] != ARM_SCHEMA
        || old["arm"] != "innovation_v2"
        || old["status"] != "failed_or_incomplete"
        || old["error"] != "JSON exceeds 64 MiB limit"
        || old["manifest_sha256"] != manifest_id["sha256"]
        || old["input"] != observation["input"]
        || old["source"] != observation["source"]
    {
        return Err("not an unchanged report-size-only native failure".into());
    }
    let log = Path::new(
        old["process"]["stdout"]["path"]
            .as_str()
            .ok_or("process log absent")?,
    );
    let attempt = log
        .parent()
        .ok_or("attempt directory absent")?
        .canonicalize()
        .map_err(|e| e.to_string())?;
    if attempt.parent() != Some(root.join("arms/innovation_v2").as_path())
        || !attempt
            .file_name()
            .is_some_and(|n| n.to_string_lossy().starts_with("attempt-"))
    {
        return Err("attempt is outside its observation arm".into());
    }
    verify(&manifest["cohort"])?;
    verify(&observation["source"])?;
    verify_runtime(&manifest)?;
    process_evidence(
        "innovation_v2",
        &attempt,
        &observation["input"],
        &observation["source"],
        &manifest,
        &old["process"],
    )?;
    let report_id = json!(input::identity(&attempt.join("decode/result.json"))?);
    let scored = score_native(
        "innovation_v2",
        &attempt,
        &observation["input"],
        &observation["source"],
        &manifest,
    )?;
    verify(&report_id)?;
    verify(&original_id)?;
    verify(&manifest_id)?;
    Ok(json!({
        "schema":"native-report-size-recovery-v1", "status":"complete",
        "observation_id":observation["observation_id"], "arm":"innovation_v2",
        "original_observation":original_id, "original_manifest":manifest_id,
        "original_native_report":report_id, "original_process":old["process"],
        "scored":scored, "decoder_rerun":false, "original_artifacts_modified":false,
        "reason":"completed decoder report exceeded old 64 MiB metadata reader; revalidated with bounded 256 MiB native report reader",
        "scope":"supplementary postprocessing receipt, not an overwritten original campaign result"
    }))
}

fn committed(
    arm_root: &Path,
    source: &Value,
    pcm: Option<&Value>,
) -> Result<Option<Value>, String> {
    let path = arm_root.join("committed.json");
    if !path.exists() {
        return Ok(None);
    }
    let link = input::read_json(&path)?;
    verify(&link["record"])?;
    let record_path = Path::new(
        link["record"]["path"]
            .as_str()
            .ok_or("record path absent")?,
    );
    let canonical_record = record_path.canonicalize().map_err(|e| e.to_string())?;
    let canonical_root = arm_root.canonicalize().map_err(|e| e.to_string())?;
    if canonical_record != record_path
        || canonical_record
            .file_name()
            .is_none_or(|x| x != "result.json")
        || canonical_record.parent().and_then(Path::parent) != Some(canonical_root.as_path())
    {
        return Err("record escaped arm directory".into());
    }
    let value = input::read_json(record_path)?;
    let expected_arm = arm_root
        .file_name()
        .and_then(|name| name.to_str())
        .ok_or("arm directory name invalid")?;
    if value["arm"].as_str() != Some(expected_arm) {
        return Err("committed arm label differs from arm directory".into());
    }
    let frozen_manifest = arm_root
        .ancestors()
        .nth(3)
        .ok_or("arm hierarchy invalid")?
        .join("manifest.json");
    let frozen = input::read_json(&frozen_manifest)?;
    if value["manifest_sha256"] != input::identity(&frozen_manifest)?.sha256 {
        return Err("committed arm belongs to a different frozen manifest".into());
    }
    if !same_bytes(&value["source"], source) || pcm.is_some_and(|p| !same_bytes(&value["input"], p))
    {
        return Err("committed arm source/input differs".into());
    }
    for item in value["artifacts"]
        .as_array()
        .ok_or("committed artifacts absent")?
    {
        verify(item)?;
        let artifact = Path::new(item["path"].as_str().ok_or("artifact path absent")?)
            .canonicalize()
            .map_err(|e| e.to_string())?;
        if !artifact.starts_with(canonical_record.parent().unwrap()) {
            return Err("artifact escaped owned attempt".into());
        }
    }
    if value["status"] == "complete" {
        if value["schema"] != ARM_SCHEMA
            || frozen["schema"] != MANIFEST_SCHEMA
            || value["runtime_checks"]
                != json!({"before":true,"after":true,"runtime_sha256":runtime_digest(&frozen)?})
        {
            return Err("completed arm lacks current per-arm runtime attestations".into());
        }
        let attempt_dir = record_path.parent().unwrap();
        process_evidence(
            expected_arm,
            attempt_dir,
            &value["input"],
            source,
            &frozen,
            &value["process"],
        )?;
        for filename in [
            "run.stdout.log",
            "run.stderr.log",
            "run.process.json",
            "run.rusage.txt",
        ] {
            let id = json!(input::identity(&attempt_dir.join(filename))?);
            if !value["artifacts"].as_array().unwrap().contains(&id) {
                return Err("required process artifact uncommitted".into());
            }
        }
        if value["arm"] == "direwolf" || value["arm"] == "gr_satellites" {
            let parsed = if value["arm"] == "direwolf" {
                baselines::parse_direwolf_atest(&String::from_utf8_lossy(
                    &input::read_bytes_bounded(
                        &record_path.parent().unwrap().join("run.stdout.log"),
                        64 * 1024 * 1024,
                    )?,
                ))?
            } else {
                baselines::parse_gr_satellites_kiss(&input::read_bytes_bounded(
                    &record_path.parent().unwrap().join("frames.kiss"),
                    64 * 1024 * 1024,
                )?)?
            };
            if json!(parsed.strict_ui_payloads) != value["strict_ui_payloads"] {
                return Err("baseline committed score drift".into());
            }
            if value["strict_ui_unique_count"].as_u64()
                != Some(parsed.strict_ui_payloads.len() as u64)
                || json!(parsed.all_payloads) != value["all_payloads"]
                || json!(parsed.emitted_count) != value["emitted_count"]
            {
                return Err("baseline committed counts/all-PDU set drift".into());
            }
            if value["arm"] == "gr_satellites"
                && !value["artifacts"]
                    .as_array()
                    .unwrap()
                    .contains(&json!(input::identity(&attempt_dir.join("frames.kiss"))?))
            {
                return Err("KISS evidence artifact uncommitted".into());
            }
        } else {
            let scored = score_native(
                value["arm"].as_str().ok_or("arm absent")?,
                record_path.parent().unwrap(),
                &value["input"],
                source,
                &frozen,
            )?;
            if scored["strict_ui_payloads"] != value["strict_ui_payloads"] {
                return Err("native committed score drift".into());
            }
            if scored["strict_ui_unique_count"] != value["strict_ui_unique_count"]
                || scored["all_payloads"] != value["all_payloads"]
            {
                return Err("native committed count/all-PDU set drift".into());
            }
            for id in scored["artifacts"]
                .as_array()
                .ok_or("scored artifacts absent")?
            {
                if !value["artifacts"].as_array().unwrap().contains(id) {
                    return Err("native evidence artifact uncommitted".into());
                }
            }
        }
    }
    Ok(Some(value))
}

fn arm(
    args: &Args,
    name: &str,
    root: &Path,
    pcm: &Value,
    source: &Value,
    manifest: &Value,
) -> Result<Value, String> {
    fs::create_dir_all(root).map_err(|e| e.to_string())?;
    if let Some(previous) = committed(root, source, Some(pcm))?
        && (previous["status"] == "complete" || !args.retry_failed)
    {
        return Ok(previous);
    }
    let dir = tempfile::Builder::new()
        .prefix("attempt-")
        .tempdir_in(root)
        .map_err(|e| e.to_string())?
        .keep();
    let mut record = json!({"schema":ARM_SCHEMA,"arm":name,"source":source,"input":pcm,
        "started_utc":io::utc(),"status":"running","artifacts":[],"manifest_sha256":manifest["manifest_sha256"]});
    let attempt = (|| -> Result<Value, String> {
        verify(source)?;
        verify(pcm)?;
        verify_runtime(manifest)?;
        record["runtime_checks"] =
            json!({"before":true,"after":false,"runtime_sha256":runtime_digest(manifest)?});
        let (program, command) = invocation(name, &dir, pcm, source, manifest)?;
        let process = io::execute(
            &program,
            &command,
            &dir,
            "run",
            args.decoder_seconds,
            512 * 1024 * 1024,
        )?;
        record["process"] = process.clone();
        io::require_success(&process)?;
        verify(source)?;
        verify(pcm)?;
        verify_runtime(manifest)?;
        process_evidence(name, &dir, pcm, source, manifest, &process)?;
        record["runtime_checks"]["after"] = true.into();
        let mut scored = if name.starts_with("innovation") || name == "progressive_v3" {
            score_native(name, &dir, pcm, source, manifest)?
        } else {
            let parsed = if name == "direwolf" {
                baselines::parse_direwolf_atest(&String::from_utf8_lossy(
                    &input::read_bytes_bounded(&dir.join("run.stdout.log"), 64 * 1024 * 1024)?,
                ))?
            } else {
                baselines::parse_gr_satellites_kiss(&input::read_bytes_bounded(
                    &dir.join("frames.kiss"),
                    64 * 1024 * 1024,
                )?)?
            };
            let artifacts = if name == "gr_satellites" {
                vec![json!(input::identity(&dir.join("frames.kiss"))?)]
            } else {
                vec![]
            };
            json!({"strict_ui_payloads":parsed.strict_ui_payloads,"strict_ui_unique_count":parsed.strict_ui_payloads.len(),
                "all_payloads":parsed.all_payloads,"emitted_count":parsed.emitted_count,"crc_evidence":parsed.crc_evidence,
                "received_fcs_present":false,"independent_fcs_verified":false,"artifacts":artifacts})
        };
        for path in [
            "run.stdout.log",
            "run.stderr.log",
            "run.process.json",
            "run.rusage.txt",
        ] {
            scored["artifacts"]
                .as_array_mut()
                .unwrap()
                .push(json!(input::identity(&dir.join(path))?));
        }
        Ok(scored)
    })();
    match attempt {
        Ok(scored) => {
            for (key, value) in scored.as_object().unwrap() {
                record[key] = value.clone();
            }
            record["status"] = "complete".into();
        }
        Err(error) => {
            record["status"] = if record["process"]["timed_out"] == true {
                "timeout"
            } else {
                "failed_or_incomplete"
            }
            .into();
            record["error"] = error.into();
        }
    }
    record["finished_utc"] = io::utc().into();
    // Failed arms keep and hash their diagnostics too; absence remains explicit.
    if record["status"] != "complete" {
        for path in [
            "run.stdout.log",
            "run.stderr.log",
            "run.process.json",
            "run.rusage.txt",
        ] {
            if dir.join(path).is_file() {
                record["artifacts"]
                    .as_array_mut()
                    .unwrap()
                    .push(json!(input::identity(&dir.join(path))?));
            }
        }
    }
    input::write_json_new(&dir.join("result.json"), &record)?;
    io::replace_json(
        &root.join("committed.json"),
        &json!({"record":input::identity(&dir.join("result.json"))?}),
    )?;
    Ok(record)
}

fn run_observation(
    args: &Args,
    row: &Value,
    index: usize,
    manifest: &Value,
) -> Result<Value, String> {
    let id = row["id"].as_u64().ok_or("ID absent")?;
    let root = args.output.join(format!("obs-{id}"));
    fs::create_dir_all(&root).map_err(|e| e.to_string())?;
    let source_path = args.acquisition.join(format!("obs-{id}/capture.ogg"));
    let downloaded_path = args.acquisition.join(format!("obs-{id}/downloaded.json"));
    let mut state = json!({"observation_id":id,"row":row,"status":"running","decoders":{},"started_utc":io::utc()});
    let attempt = (|| -> Result<(), String> {
        io::disk_guard(&args.output)?;
        verify(&manifest["cohort"])?;
        let start = Instant::now();
        while !downloaded_path.is_file()
            && start.elapsed() < Duration::from_secs(args.acquisition_wait_seconds)
        {
            let failure = args.acquisition.join(format!("download-records/{id}.json"));
            if failure.exists() {
                break;
            }
            std::thread::sleep(Duration::from_millis(500));
        }
        let downloaded = input::read_json(&downloaded_path)?;
        if REPAIRED_BUILD {
            download_receipt::validate(&downloaded, row)?;
        }
        let source = json!(input::identity(&source_path)?);
        if !same_bytes(&source, &downloaded["identity"]) {
            return Err("download receipt/source mismatch".into());
        }
        state["source"] = source.clone();
        for artifact in manifest["runtime"].as_array().ok_or("runtime absent")? {
            verify(artifact)?;
        }
        let mut existing = BTreeMap::new();
        for name in ARMS {
            if let Some(old) = committed(&root.join("arms").join(name), &source, None)? {
                existing.insert(name, old);
            }
        }
        if existing.len() == ARMS.len()
            && existing
                .values()
                .all(|v| v["status"] == "complete" || !args.retry_failed)
        {
            let shared = existing.values().next().ok_or("existing arms absent")?["input"].clone();
            if existing
                .values()
                .any(|record| !same_bytes(&record["input"], &shared))
            {
                return Err("existing arms do not share one PCM identity".into());
            }
            state["input"] = shared;
            if let Ok(previous) = input::read_json(&root.join("result.json")) {
                state["audio_seconds"] = previous["audio_seconds"].clone();
            }
            state["decoders"] = json!(existing);
            return Ok(());
        }
        // Only these newly created scratch files are auto-deleted. Source and
        // immutable decoder evidence never enter this directory.
        let scratch = tempfile::Builder::new()
            .prefix("pcm-scratch-")
            .tempdir_in(&root)
            .map_err(|e| e.to_string())?;
        let prep = tempfile::Builder::new()
            .prefix("preparation-")
            .tempdir_in(&root)
            .map_err(|e| e.to_string())?
            .keep();
        let probe = io::execute(
            Path::new("/usr/bin/ffprobe"),
            &[
                "-v".into(),
                "error".into(),
                "-show_streams".into(),
                "-show_format".into(),
                "-of".into(),
                "json".into(),
                source_path.display().to_string(),
            ],
            &prep,
            "probe",
            60,
            1024 * 1024,
        )?;
        io::require_success(&probe)?;
        let metadata = input::read_json(&prep.join("probe.stdout.log"))?;
        let streams = metadata["streams"]
            .as_array()
            .ok_or("audio streams absent")?;
        let duration = metadata["format"]["duration"]
            .as_str()
            .ok_or("audio duration absent")?
            .parse::<f64>()
            .map_err(|e| e.to_string())?;
        if streams.len() != 1
            || streams[0]["channels"] != 1
            || streams[0]["sample_rate"] != "48000"
            || streams[0]["codec_name"] != "vorbis"
            || !duration.is_finite()
            || !(0.2..=1800.0).contains(&duration)
        {
            return Err("unsupported audio geometry; no implicit resampling/downmix".into());
        }
        let wav = scratch.path().join("shared-pcm16.wav");
        let conversion = io::execute(
            Path::new("/usr/bin/ffmpeg"),
            &[
                "-nostdin".into(),
                "-v".into(),
                "error".into(),
                "-n".into(),
                "-i".into(),
                source_path.display().to_string(),
                "-map".into(),
                "0:a:0".into(),
                "-c:a".into(),
                "pcm_s16le".into(),
                "-flags:a".into(),
                "+bitexact".into(),
                "-fflags".into(),
                "+bitexact".into(),
                wav.display().to_string(),
            ],
            &prep,
            "convert",
            120,
            512 * 1024 * 1024,
        )?;
        io::require_success(&conversion)?;
        let mut pcm = json!(input::identity(&wav)?);
        let reader = hound::WavReader::open(&wav).map_err(|e| e.to_string())?;
        let spec = reader.spec();
        if spec.channels != 1
            || spec.sample_rate != 48000
            || spec.bits_per_sample != 16
            || spec.sample_format != hound::SampleFormat::Int
        {
            return Err("PCM conversion violated contract".into());
        }
        state["audio_seconds"] = json!(f64::from(reader.duration()) / 48000.0);
        pcm["samples"] = json!(reader.duration());
        pcm["sample_rate"] = 48000.into();
        pcm["bits_per_sample"] = 16.into();
        state["input"] = pcm.clone();
        state["conversion_process"] = conversion;
        drop(reader);
        let order: Vec<_> = (0..ARMS.len())
            .map(|offset| ARMS[(index + offset) % ARMS.len()])
            .collect();
        state["decoder_order"] = json!(order);
        for name in order {
            state["stage"] = name.into();
            io::replace_json(&root.join("progress.json"), &state)?;
            state["decoders"][name] = arm(
                args,
                name,
                &root.join("arms").join(name),
                &pcm,
                &source,
                manifest,
            )?;
            io::replace_json(&root.join("progress.json"), &state)?;
        }
        verify(&source)?;
        verify(&pcm)?;
        verify(&manifest["cohort"])?;
        for artifact in manifest["runtime"].as_array().ok_or("runtime absent")? {
            verify(artifact)?;
        }
        scratch.close().map_err(|e| e.to_string())?;
        state["owned_pcm_scratch_removed"] = true.into();
        Ok(())
    })();
    state["finished_utc"] = io::utc().into();
    state["status"] = if attempt.is_ok()
        && ARMS
            .iter()
            .all(|a| state["decoders"][*a]["status"] == "complete")
    {
        "complete"
    } else {
        "incomplete"
    }
    .into();
    if let Err(error) = attempt {
        state["error"] = error.into();
    }
    io::replace_json(&root.join("result.json"), &state)?;
    Ok(state)
}

fn groups(rows: &[Value]) -> Result<BTreeMap<u64, usize>, String> {
    let mut parsed = vec![];
    for row in rows {
        let id = row["id"].as_u64().ok_or("ID absent")?;
        let start =
            chrono::DateTime::parse_from_rfc3339(row["start"].as_str().ok_or("start absent")?)
                .map_err(|e| e.to_string())?
                .timestamp();
        let end = chrono::DateTime::parse_from_rfc3339(row["end"].as_str().ok_or("end absent")?)
            .map_err(|e| e.to_string())?
            .timestamp();
        if end <= start {
            return Err("observation interval reversed".into());
        }
        let satellite = row
            .get("norad_cat_id")
            .or_else(|| row.get("norad"))
            .ok_or("NORAD grouping metadata absent")?
            .to_string();
        parsed.push((satellite, start, end, id));
    }
    parsed.sort();
    let (mut previous, mut until, mut group) = (String::new(), i64::MIN, 0);
    let mut result = BTreeMap::new();
    for (satellite, start, end, id) in parsed {
        if satellite != previous || start > until {
            group += 1;
            until = end;
            previous = satellite;
        } else {
            until = until.max(end);
        }
        result.insert(id, group);
    }
    Ok(result)
}

fn verify_cohort(cohort: &Value) -> Result<&Vec<Value>, String> {
    if cohort["schema"] != "innovation-benchmark-cohort-v2" {
        return Err("unsupported cohort schema".into());
    }
    let rows = cohort["observations"]
        .as_array()
        .ok_or("cohort observations absent")?;
    let ids: Vec<_> = rows
        .iter()
        .map(|r| {
            r["id"]
                .as_u64()
                .filter(|id| *id > 0)
                .ok_or("positive ID required")
        })
        .collect::<Result<_, _>>()?;
    let unique: BTreeSet<_> = ids.iter().copied().collect();
    if rows.is_empty()
        || rows.len() > 1000
        || unique.len() != rows.len()
        || cohort["selected_count"].as_u64() != Some(rows.len() as u64)
    {
        return Err(
            "cohort must contain1..1000 unique positive IDs and consistent selected_count".into(),
        );
    }
    let canonical = ids
        .iter()
        .map(u64::to_string)
        .collect::<Vec<_>>()
        .join("\n");
    let digest = hex::encode(Sha256::digest(canonical.as_bytes()));
    if cohort["selection_ids_sha256"] != digest {
        return Err("cohort frozen selection order hash mismatch".into());
    }
    let frozen = chrono::DateTime::parse_from_rfc3339(
        cohort["frozen_utc"]
            .as_str()
            .ok_or("cohort frozen UTC absent")?,
    )
    .map_err(|e| format!("invalid frozen UTC: {e}"))?;
    let plan = &cohort["plan"];
    let norad = plan.get("norad_cat_id").or_else(|| plan.get("norad"));
    if (plan.get("norad_cat_id").is_some()
        && plan.get("norad").is_some()
        && plan["norad_cat_id"] != plan["norad"])
        || !matches!(plan["mode"].as_str(), Some("GMSK" | "FSK"))
        || cohort["plan"]["baud"].as_f64() != Some(9600.0)
        || norad.and_then(Value::as_u64) != Some(68635)
    {
        return Err("cohort plan does not prove supported CANVAS GMSK/FSK9600 profile".into());
    }
    for row in rows {
        if row["norad_cat_id"].as_u64() != Some(68635)
            || row["transmitter"].as_str() != Some("GCmN6RULea8dAT7Qoat8z2")
        {
            return Err("observation profile differs from the frozen CANVAS transmitter".into());
        }
        let end = chrono::DateTime::parse_from_rfc3339(row["end"].as_str().ok_or("end absent")?)
            .map_err(|e| e.to_string())?;
        if end > frozen {
            return Err("cohort freeze precedes a selected observation end".into());
        }
    }
    groups(rows)?;
    Ok(rows)
}

fn intervals(samples: &[(usize, i64, u64, u64)]) -> Value {
    let mut clusters: BTreeMap<usize, (i64, u64, u64)> = BTreeMap::new();
    for &(cluster, delta, baseline, n) in samples {
        let item = clusters.entry(cluster).or_default();
        item.0 += delta;
        item.1 += baseline;
        item.2 += n;
    }
    if clusters.len() < 10 {
        return json!({"available":false,"reason":"fewer than 10 pass clusters","clusters":clusters.len()});
    }
    let rows: Vec<_> = clusters.into_values().collect();
    let mut rng = 0xd451_4bca_7f92_2026u64;
    let mut means = Vec::new();
    let mut relative = Vec::new();
    for _ in 0..10000 {
        let (mut delta, mut base, mut n) = (0i64, 0u64, 0u64);
        for _ in 0..rows.len() {
            rng ^= rng << 13;
            rng ^= rng >> 7;
            rng ^= rng << 17;
            let item = rows[(rng as usize) % rows.len()];
            delta += item.0;
            base += item.1;
            n += item.2;
        }
        means.push(delta as f64 / n as f64);
        if base > 0 {
            relative.push(100.0 * delta as f64 / base as f64);
        }
    }
    let bounds = |mut x: Vec<f64>| -> Value {
        if x.is_empty() {
            return Value::Null;
        }
        x.sort_by(f64::total_cmp);
        json!([
            x[x.len() * 25 / 1000],
            x[(x.len() * 975 / 1000).min(x.len() - 1)]
        ])
    };
    let defined = relative.len();
    let relative_bounds = if defined == 10000 {
        bounds(relative)
    } else {
        Value::Null
    };
    json!({"available":true,"clusters":rows.len(),"resamples":10000,"seed_hex":"d4514bca7f922026",
        "net_additional_pdus_per_observation_percentile95":bounds(means),"relative_pdu_gain_percent_percentile95":relative_bounds,
        "relative_defined_resamples":defined,"relative_zero_baseline_resamples":10000-defined,
        "relative_interval_policy":"null if any bootstrap draw has zero baseline; undefined draws are not silently conditionally discarded",
        "cluster_definition":"connected overlapping time intervals of same NORAD across stations; no overlapping window pseudoreplicates",
        "qualification":"exploratory paired cluster bootstrap; assumes independent passes, not a significance or publication gate"})
}

fn summarize(args: &Args, rows: &[Value]) -> Result<Value, String> {
    let clustering = groups(rows)?;
    let mut totals: BTreeMap<String, usize> = BTreeMap::new();
    let mut pdu_bytes: BTreeMap<String, usize> = BTreeMap::new();
    let mut global: BTreeMap<String, BTreeSet<String>> = BTreeMap::new();
    let mut completion: BTreeMap<String, usize> = BTreeMap::new();
    let mut comparison: BTreeMap<String, Vec<(usize, i64, u64, u64)>> = BTreeMap::new();
    let mut records = vec![];
    let mut missing = vec![];
    let mut errors = vec![];
    let mut costs: BTreeMap<String, (f64, f64, f64)> = BTreeMap::new();
    for row in rows {
        let id = row["id"].as_u64().ok_or("ID absent")?;
        let root = args.output.join(format!("obs-{id}"));
        let source_path = args.acquisition.join(format!("obs-{id}/capture.ogg"));
        if !root.join("result.json").exists() {
            missing.push(id);
            continue;
        }
        let state = input::read_json(&root.join("result.json"))?;
        require_observation_label(&state, id)?;
        if state["source"].is_null() {
            errors.push(json!({"id":id,"reason":state["error"]}));
            continue;
        }
        let source = json!(input::identity(&source_path)?);
        if !same_bytes(&source, &state["source"]) {
            return Err(format!("source changed after observation{id}"));
        }
        let mut sets = BTreeMap::new();
        let mut statuses = BTreeMap::new();
        let mut common_pcm = true;
        for name in ARMS {
            let value = committed(&root.join("arms").join(name), &source, None)?;
            statuses.insert(
                name,
                value
                    .as_ref()
                    .map(|v| v["status"].clone())
                    .unwrap_or(json!("missing")),
            );
            if let Some(record) = value.filter(|v| v["status"] == "complete") {
                common_pcm &= same_bytes(&record["input"], &state["input"]);
                *completion.entry(name.into()).or_default() += 1;
                sets.insert(name, string_set(&record["strict_ui_payloads"])?);
                let cost = costs.entry(name.into()).or_default();
                cost.0 += record["process"]["wall_seconds"]
                    .as_f64()
                    .ok_or("completed process wall time absent")?;
                cost.1 += record["process"]["user_cpu_seconds"]
                    .as_f64()
                    .ok_or("completed process CPU time absent")?
                    + record["process"]["system_cpu_seconds"]
                        .as_f64()
                        .ok_or("completed process system CPU time absent")?;
                cost.2 = cost.2.max(
                    record["process"]["maximum_rss_kib"]
                        .as_f64()
                        .ok_or("completed process RSS absent")?,
                );
            }
        }
        if sets.len() != ARMS.len() || state["status"] != "complete" || !common_pcm {
            errors.push(json!({"id":id,"statuses":statuses,"observation_status":state["status"],"common_pcm_identity_verified":common_pcm,"reason":state["error"]}));
            continue;
        }
        for (name, pdus) in &sets {
            *totals.entry((*name).into()).or_default() += pdus.len();
            *pdu_bytes.entry((*name).into()).or_default() +=
                pdus.iter().map(|pdu| pdu.len() / 2).sum::<usize>();
            global
                .entry((*name).into())
                .or_default()
                .extend(pdus.iter().cloned());
        }
        let ours = &sets["innovation_v2"];
        let mut row_comparisons = BTreeMap::new();
        for name in &ARMS[1..] {
            let base = &sets[name];
            row_comparisons.insert(
                *name,
                json!({"added":diff(ours,base),"lost":diff(base,ours)}),
            );
            comparison.entry((*name).into()).or_default().push((
                clustering[&id],
                ours.len() as i64 - base.len() as i64,
                base.len() as u64,
                1,
            ));
        }
        let external: BTreeSet<_> = sets["direwolf"]
            .union(&sets["gr_satellites"])
            .cloned()
            .collect();
        row_comparisons.insert(
            "external_union",
            json!({"added":diff(ours,&external),"lost":diff(&external,ours)}),
        );
        records.push(json!({"id":id,"pass_cluster":clustering[&id],"counts":sets.iter().map(|(k,v)|(*k,v.len())).collect::<BTreeMap<_,_>>(),"comparisons":row_comparisons}));
    }
    let cis: BTreeMap<_, _> = comparison.iter().map(|(k, v)| (k, intervals(v))).collect();
    let mut gains = BTreeMap::new();
    for name in ARMS.iter().skip(1).copied().chain(["external_union"]) {
        let (mut added, mut lost) = (0, 0);
        let (mut added_global, mut lost_global) = (BTreeSet::new(), BTreeSet::new());
        for r in &records {
            let a = string_set(&r["comparisons"][name]["added"])?;
            let l = string_set(&r["comparisons"][name]["lost"])?;
            added += a.len();
            lost += l.len();
            added_global.extend(a);
            lost_global.extend(l);
        }
        gains.insert(name,json!({"added_observation_pdu_pairs":added,"lost_observation_pdu_pairs":lost,"globally_distinct_added_somewhere":added_global.len(),"globally_distinct_lost_somewhere":lost_global.len()}));
    }
    let global_counts: BTreeMap<_, _> = global.iter().map(|(k, v)| (k, v.len())).collect();
    let empty = BTreeSet::new();
    let ours = global.get("innovation_v2").unwrap_or(&empty);
    let mut global_exclusive = BTreeMap::new();
    for name in ARMS.iter().skip(1) {
        let base = global.get(*name).unwrap_or(&empty);
        let added = diff(ours, base);
        global_exclusive.insert(*name,json!({"added_distinct_pdus":added.len(),"added_pdu_bytes_excluding_fcs":added.iter().map(|p|p.len()/2).sum::<usize>(),"lost_distinct_pdus":base.difference(ours).count()}));
    }
    let summary = json!({"schema":"innovation-benchmark-summary-v2","updated_utc":io::utc(),"selected":rows.len(),
        "all_five_complete":records.len(),"not_yet_attempted":missing,"incomplete_observations":errors,"per_arm_complete":completion,
        "primary_complete_case_observation_pdu_counts":totals,"primary_complete_case_globally_distinct_pdu_counts":global_counts,
        "comparisons":gains,"globally_absent_from_comparator_everywhere":global_exclusive,
        "globally_absent_scope":"all-five-complete analyzed observations only, not the incomplete/unattempted cohort or public archive",
        "primary_complete_case_pdu_bytes_excluding_fcs":pdu_bytes,"cluster_bootstrap":cis,"rows":records,
        "per_arm_completed_costs_wall_cpu_peak_rss_kib":costs,"cost_denominator":"each arm's own completed observations, not necessarily common complete cases",
        "input_policy":"same original OGG decoded once to shared mono48k PCM16, no resampling/downmix; v2 additionally original-codec metadata with numeric PCM verification",
        "v1_policy":"frozen innovation v1 no codec; not the entire previous OGG codec stack",
        "external_policy":"Dire Wolf atest -F0 and installed gr-satellites FSK9600 AX.25 G3RUH; not historical full gr-satnogs IQ chain",
        "failure_policy":"failed/incomplete/missing arms are not zero recovery; complete-case comparisons may be biased and require denominators",
        "fcs_evidence":"native received FCS independently verified; external FCS checked by decoder then stripped, never synthesized as evidence",
        "timing_policy":"individual child wall/CPU/RSS incl startup, bounded concurrent observations on shared host, conversion separately recorded; not isolated or equal-compute dominance",
        "scientific_novelty_established":false,"false_alarm_qualification_complete":false,"publication_ready":false});
    io::replace_json(&args.output.join("summary.json"), &summary)?;
    Ok(summary)
}

fn require_observation_label(state: &Value, expected_id: u64) -> Result<(), String> {
    if state["observation_id"].as_u64() != Some(expected_id) {
        return Err("observation result label differs from selected ID".into());
    }
    Ok(())
}

fn require_no_attempt_failures(summary: &Value) -> Result<(), String> {
    if !summary["incomplete_observations"]
        .as_array()
        .ok_or("incomplete observation list absent")?
        .is_empty()
    {
        return Err("one or more attempted observations incomplete; consult summary, failures are not zero recovery".into());
    }
    // Unattempted remainder is intentional when a fixed-size milestone is used.
    Ok(())
}

fn runtime_files(path: &Path, output: &mut Vec<Value>) -> Result<(), String> {
    let mut entries: Vec<_> = fs::read_dir(path)
        .map_err(|e| e.to_string())?
        .collect::<Result<_, _>>()
        .map_err(|e| e.to_string())?;
    entries.sort_by_key(|e| e.file_name());
    for item in entries {
        let p = item.path();
        let ft = item.file_type().map_err(|e| e.to_string())?;
        if ft.is_dir() && item.file_name() != "__pycache__" {
            runtime_files(&p, output)?;
        } else if ft.is_file() && p.extension().is_some_and(|e| e == "py" || e == "so") {
            output.push(json!(input::identity(&p)?));
        }
    }
    Ok(())
}

fn checked_document(identity: &Value) -> Result<Value, String> {
    let path = Path::new(
        identity["path"]
            .as_str()
            .ok_or("document identity path absent")?,
    );
    let actual = json!(input::identity(path)?);
    if &actual != identity {
        return Err("document exact identity mismatch".into());
    }
    let raw = input::read_bytes_bounded(path, 64 * 1024 * 1024)?;
    if identity["sha256"] != hex::encode(Sha256::digest(&raw))
        || identity["bytes"].as_u64() != Some(raw.len() as u64)
    {
        return Err("document changed during admission verification".into());
    }
    serde_json::from_slice(&raw).map_err(|e| e.to_string())
}
fn utc_seconds(value: &Value) -> Result<i64, String> {
    chrono::DateTime::parse_from_rfc3339(value.as_str().ok_or("UTC timestamp absent")?)
        .map(|d| d.timestamp())
        .map_err(|e| e.to_string())
}
fn reviewed(value: &Value, schema: &str, scope: &Value) -> Result<(), String> {
    if value["schema"] != schema
        || value["status"] != "complete"
        || value["complete_review"] != true
        || &value["scope"] != scope
        || value["in_scope_blocking_findings"]
            .as_array()
            .is_none_or(|a| !a.is_empty())
    {
        return Err(
            "release review incomplete, wrong scope/schema or blocking findings remain".into(),
        );
    }
    Ok(())
}
fn release_admission(
    release_id: &Value,
    current_override: Option<&Value>,
    cohort_id: &Value,
    expected_freeze: &Value,
    now: i64,
    require_fresh: bool,
) -> Result<Value, String> {
    let release = checked_document(release_id)?;
    let scope = &release["scope"];
    if !matches!(
        scope.as_str(),
        Some("historical-local-exposure-reviewed" | "prospective-after-freeze")
    ) {
        return Err("unsupported exposure release scope".into());
    }
    reviewed(&release, "innovation-waveform-release-v1", scope)?;
    if release["current_exposure_check_passed"] != true
        || release["evaluation_release_permitted"] != true
        || release["no_development_during_evaluation"] != true
        || !same_bytes(&release["cohort"], cohort_id)
    {
        return Err("exposure release is not authorized for this frozen cohort".into());
    }
    let cohort = checked_document(cohort_id)?;
    if checked_document(&release["cohort"])? != cohort {
        return Err("local cohort differs from registered release cohort".into());
    }
    verify_cohort(&cohort)?;
    if cohort["current_exposure_check_passed"] != true
        || cohort["evaluation_release_permitted"] != true
        || cohort["fresh_independent_holdout_qualified"] != false
        || cohort["source_cohort"] != release["source_cohort"]
        || cohort["exposure_audit"] != release["exposure_manifest"]
        || cohort["selected_count"] != release["selected_count"]
        || cohort["selection_ids_sha256"] != release["selection_ids_sha256"]
    {
        return Err("candidate-only cohort or release/cohort binding mismatch".into());
    }
    let original = checked_document(&release["source_cohort"])?;
    let original_rows = verify_cohort(&original)?;
    let rows = cohort["observations"].as_array().unwrap();
    let ranks = cohort["original_ranks"]
        .as_array()
        .ok_or("original subset ranks absent")?;
    if ranks.len() != rows.len()
        || cohort["plan"] != original["plan"]
        || cohort["source_selection_ids_sha256"] != original["selection_ids_sha256"]
    {
        return Err("release changed source plan or ranking contract".into());
    }
    let mut previous = None;
    for (rank, row) in ranks.iter().zip(rows) {
        let n = rank
            .as_u64()
            .and_then(|n| usize::try_from(n).ok())
            .ok_or("invalid original rank")?;
        if previous.is_some_and(|p| p >= n) || original_rows.get(n) != Some(row) {
            return Err("derived cohort is not an exact original-ranked subset".into());
        }
        previous = Some(n);
    }
    let exposure = checked_document(&release["exposure_manifest"])?;
    if exposure["schema"] != "recording-exposure-manifest-v1" {
        return Err("unknown exposure manifest schema".into());
    }
    let snapshot = utc_seconds(&exposure["finished_utc"])?;
    let lineage = checked_document(&release["lineage_review"])?;
    reviewed(&lineage, "innovation-lineage-review-v1", scope)?;
    if lineage["source_cohort"] != release["source_cohort"]
        || lineage["exposure_manifest"] != release["exposure_manifest"]
    {
        return Err("lineage review refers to another inventory/cohort".into());
    }
    let freeze = checked_document(&release["receiver_freeze"])?;
    if freeze["schema"] != "innovation-receiver-freeze-v1" || freeze["status"] != "complete" {
        return Err("receiver freeze incomplete or unknown schema".into());
    }
    let frozen_timestamp = utc_seconds(&freeze["frozen_utc"])?;
    for (key, value) in expected_freeze
        .as_object()
        .ok_or("expected receiver freeze absent")?
    {
        if &freeze[key] != value {
            return Err(format!("receiver freeze differs: {key}"));
        }
    }
    if scope == "prospective-after-freeze" {
        let registration = checked_document(&release["prospective_protocol"])?;
        let amendment = checked_document(&release["selection_amendment"])?;
        if release["prospective_protocol"] != freeze["selection_registration"]
            || release["selection_amendment"] != freeze["selection_amendment"]
            || original["selection_registration"] != release["prospective_protocol"]
            || original["selection_amendment"] != release["selection_amendment"]
            || amendment["schema"] != "innovation-prospective-selection-amendment-v1"
            || amendment["status"] != "registered_before_observation_window"
            || amendment["source_protocol"] != release["prospective_protocol"]
            || amendment["original_protocol_preserved"] != true
            || registration["target_maximum"] != 500
            || original_rows.len() > 500
            || registration["norad_cat_id"] != 68635
            || registration["baud"] != 9600
            || registration["mode"] != "GMSK"
            || registration["transmitter_uuid"] != "GCmN6RULea8dAT7Qoat8z2"
            || original["plan"]["mode"] != "GMSK"
            || original["plan"]["selection"] != registration["selection"]
            || registration["selection"]["schema"] != "station-day-round-robin-v1"
            || original["plan"]["metadata_filters"] != registration["metadata_query"]["filters"]
            || original["plan"]["outcome_filters"] != json!([])
            || registration["metadata_query"]["outcome_filters"] != json!([])
        {
            return Err(
                "prospective source selection differs from frozen registration/amendment".into(),
            );
        }
        if registration["schema"] != "innovation-prospective-selection-v1"
            || registration["registration_guard_seconds"] != 7200
            || registration["require_full_receiver_freeze_before_window_start_minus_guard"] != true
        {
            return Err("prospective release lacks registered time-window/guard contract".into());
        }
        let start = utc_seconds(&registration["observation_start_inclusive_utc"])?;
        let end = utc_seconds(&registration["observation_start_exclusive_utc"])?;
        let end_limit = utc_seconds(&registration["observation_end_must_not_exceed_utc"])?;
        if registration["metadata_query"]["filters"]
            != json!({"norad_cat_id":68635,
            "start":registration["observation_start_inclusive_utc"],"start__lt":registration["observation_start_exclusive_utc"]})
        {
            return Err(
                "prospective metadata filters not exactly registered mission/time only".into(),
            );
        }
        if end <= start
            || end_limit != end
            || frozen_timestamp > start - 7200
            || utc_seconds(&registration["registered_utc"])? > start - 7200
            || utc_seconds(&amendment["declared_utc"])? > start - 7200
            || utc_seconds(&original["frozen_utc"])? < end
            || utc_seconds(&cohort["frozen_utc"])? < end
        {
            return Err("prospective receiver/analysis freeze missed the pre-window guard".into());
        }
        let review = checked_document(&release["selection_review"])?;
        reviewed(&review, "innovation-selection-review-v1", scope)?;
        let eligible = review["eligible_count"]
            .as_u64()
            .ok_or("selection eligible count absent")?;
        if review["source_cohort"] != release["source_cohort"]
            || review["selection_registration"] != release["prospective_protocol"]
            || review["selection_amendment"] != release["selection_amendment"]
            || review["selected_count"] != original["selected_count"]
            || review["selection_ids_sha256"] != original["selection_ids_sha256"]
            || original_rows.len() as u64 != eligible.min(500)
            || review["true_eof_verified"] != true
            || review["exact_selection_replay_verified"] != true
            || review["no_outcome_filtering_verified"] != true
        {
            return Err("prospective selection/EOF review not complete or source-bound".into());
        }
        let evidence = review["evidence"]
            .as_array()
            .filter(|x| !x.is_empty())
            .ok_or("selection review evidence absent")?;
        for id in evidence {
            verify(id)?;
        }
        // Check the complete new source cohort too; a subset cannot launder
        // old historical rows into the future-window registration.
        for row in original_rows {
            let row_start = utc_seconds(&row["start"])?;
            if row_start < start || row_start >= end || utc_seconds(&row["end"])? > end_limit {
                return Err(
                    "prospective source cohort contains an out-of-window observation".into(),
                );
            }
        }
    }
    // Initial release is immutable; a refreshed, equally bound check may admit
    // a later invocation. Freshness is admission-only, never a mid-run timer.
    let initial = checked_document(&release["current_exposure_check"])?;
    let current_id = current_override.unwrap_or(&release["current_exposure_check"]);
    let current = checked_document(current_id)?;
    for check in [&initial, &current] {
        reviewed(check, "innovation-current-exposure-check-v1", scope)?;
        if check["cohort"] != release["cohort"]
            || check["exposure_manifest"] != release["exposure_manifest"]
            || check["no_new_in_scope_exposure"] != true
            || utc_seconds(&check["checked_utc"])? < snapshot
        {
            return Err(
                "current exposure check is not a post-snapshot same-cohort clean check".into(),
            );
        }
    }
    let checked = utc_seconds(&current["checked_utc"])?;
    let released = utc_seconds(&release["released_utc"])?;
    if released < snapshot
        || released < frozen_timestamp
        || released < utc_seconds(&initial["checked_utc"])?
        || released > now
        || checked > now
        || (require_fresh && now - checked > 3600)
    {
        return Err("release/current exposure check has invalid timing or stale admission".into());
    }
    Ok(
        json!({"schema":"innovation-benchmark-admission-v1","status":"admitted","release":release_id,
        "current_exposure_check":current_id,"cohort":cohort_id,"registered_cohort":release["cohort"],"receiver_freeze":release["receiver_freeze"],
        "admitted_utc":chrono::DateTime::from_timestamp(now,0).ok_or("invalid admission time")?.to_rfc3339(),
        "freshness_checked":require_fresh,"freshness_scope":"at invocation admission only; no development/tuning during frozen evaluation; resume requires a current check",
        "global_independence_proven":false,"waveform_reads":0,"decoder_runs":0}),
    )
}
fn run() -> Result<(), String> {
    if option_env!("TELEMETRY_BENCHMARK_PROGRESSIVE_PATH").is_some()
        != option_env!("TELEMETRY_BENCHMARK_PROGRESSIVE_SHA256").is_some()
        || !Path::new(V3).is_absolute()
        || V3_SHA.len() != 64
        || !V3_SHA
            .bytes()
            .all(|b| b.is_ascii_hexdigit() && !b.is_ascii_uppercase())
        || !Path::new(PROTOCOL_PATH).is_absolute()
    {
        return Err("invalid paired compile-time receiver/protocol binding".into());
    }
    let mut args = Args::parse();
    if !(1..=4).contains(&args.workers)
        || !(1..=8).contains(&args.threads)
        || args.workers * args.threads > 8
        || !(60..=3600).contains(&args.decoder_seconds)
        || args.acquisition_wait_seconds > 3600
        || args.max_new_observations == Some(0)
        || (args.preflight_only
            && (args.analyze_only || args.retry_failed || args.max_new_observations.is_some()))
    {
        return Err("invalid bounded resource arguments".into());
    }
    args.acquisition = args.acquisition.canonicalize().map_err(|e| e.to_string())?;
    args.improved = args.improved.canonicalize().map_err(|e| e.to_string())?;
    let (expected_progressive, expected_innovation) = repaired_receiver_hashes(RECEIVER_REVISION)?;
    if RECEIVER_REVISION.is_some() && !REPAIRED_BUILD {
        return Err("receiver revision requires paired compile-time receiver binding".into());
    }
    if REPAIRED_BUILD
        && (V3_SHA != expected_progressive
            || input::identity(&args.improved)?.sha256 != expected_innovation
            || args.workers != 4
            || args.threads != 2
            || args.decoder_seconds != 900
            || args.release_receipt.is_none())
    {
        return Err(
            "repaired evaluation requires pinned repaired binaries, 4x2/900s and --release-receipt"
                .into(),
        );
    }
    if args.current_exposure_check.is_some() && args.release_receipt.is_none() {
        return Err("current check requires release receipt".into());
    }
    let cohort_path = args.acquisition.join("cohort.json");
    let initial_cohort_id = json!(input::identity(&cohort_path)?);
    let cohort = checked_document(&initial_cohort_id)?;
    let rows = verify_cohort(&cohort)?;
    let release_id = args
        .release_receipt
        .as_ref()
        .map(|p| input::identity(p).map(|id| json!(id)))
        .transpose()?;
    let current_id = args
        .current_exposure_check
        .as_ref()
        .map(|p| input::identity(p).map(|id| json!(id)))
        .transpose()?;
    if !args.output.exists() {
        if args.resume || args.analyze_only {
            return Err("resume/analysis output absent".into());
        }
        fs::create_dir(&args.output).map_err(|e| e.to_string())?;
    }
    args.output = args.output.canonicalize().map_err(|e| e.to_string())?;
    let lock = OpenOptions::new()
        .read(true)
        .write(true)
        .create(true)
        .truncate(false)
        .custom_flags(libc::O_NOFOLLOW | libc::O_NONBLOCK)
        .open(args.output.join("benchmark.lock"))
        .map_err(|e| e.to_string())?;
    if !lock.metadata().map_err(|e| e.to_string())?.is_file()
        || unsafe { libc::flock(lock.as_raw_fd(), libc::LOCK_EX | libc::LOCK_NB) } != 0
    {
        return Err("benchmark already running or invalid lock".into());
    }
    let manifest_path = args.output.join("manifest.json");
    let mut manifest = if args.resume || args.analyze_only {
        let saved = input::read_json(&manifest_path)?;
        if saved["schema"] != MANIFEST_SCHEMA || saved["release_receipt"] != json!(release_id) {
            return Err("resume requires current manifest schema and unchanged release receipt; use original binary for historical sessions".into());
        }
        if saved["selected_runtime_sha256"] != selected_runtime_digest(&saved)? {
            return Err("saved selected runtime digest mismatch".into());
        }
        verify(&saved["cohort"])?;
        if !same_bytes(&saved["cohort"], &initial_cohort_id)
            || saved["workers"] != args.workers
            || saved["threads"] != args.threads
            || saved["decoder_seconds"] != args.decoder_seconds
            || !same_bytes(&saved["improved"], &json!(input::identity(&args.improved)?))
        {
            return Err("resume frozen cohort/receiver/resource mismatch".into());
        }
        for artifact in saved["runtime"].as_array().ok_or("runtime absent")? {
            verify(artifact)?;
        }
        verify(&saved["runner"])?;
        if !same_bytes(
            &saved["runner"],
            &json!(input::identity(
                &std::env::current_exe().map_err(|e| e.to_string())?
            )?),
        ) {
            return Err("resume runner binary changed".into());
        }
        saved
    } else {
        if input::identity(Path::new(V1))?.sha256 != V1_SHA
            || input::identity(Path::new(V3))?.sha256 != V3_SHA
        {
            return Err("frozen historical receiver changed".into());
        }
        io::new_text(&args.output.join("grsat-profile.yml"), PROFILE)?;
        let mut runtime = vec![];
        for path in [
            Path::new(V1),
            Path::new(V3),
            args.improved.as_path(),
            Path::new(GRSAT),
            Path::new("/usr/bin/atest"),
            Path::new("/usr/bin/ffmpeg"),
            Path::new("/usr/bin/ffprobe"),
            Path::new("/usr/bin/time"),
            args.output.join("grsat-profile.yml").as_path(),
        ] {
            runtime.push(json!(input::identity(path)?));
        }
        runtime_files(
            Path::new(
                "/home/ubuntu/telemetry-yield/work/golden/env/lib/python3.12/site-packages/satellites",
            ),
            &mut runtime,
        )?;
        let protocol_path = Path::new(PROTOCOL_PATH);
        runtime.push(json!(input::identity(protocol_path)?));
        if REPAIRED_BUILD {
            runtime.push(json!(input::identity(Path::new(BASE_PROTOCOL_PATH))?));
            runtime.push(json!(input::identity(Path::new(ANALYSIS_PROTOCOL_PATH))?));
            runtime.push(json!(input::identity(Path::new(
                SELECTION_REGISTRATION_PATH
            ))?));
            runtime.push(json!(input::identity(Path::new(SELECTION_AMENDMENT_PATH))?));
        }
        let mut bindings = serde_json::Map::new();
        for (name, path) in [
            ("innovation_v2", args.improved.as_path()),
            ("innovation_v1_no_codec", Path::new(V1)),
            ("progressive_v3", Path::new(V3)),
            ("direwolf", Path::new("/usr/bin/atest")),
            ("gr_satellites", Path::new(GRSAT)),
        ] {
            bindings.insert(name.into(), json!(input::identity(path)?));
        }
        let mut saved = json!({"schema":MANIFEST_SCHEMA,"created_utc":io::utc(),"cohort":initial_cohort_id,
            "runner":input::identity(&std::env::current_exe().map_err(|e|e.to_string())?)?,"improved":input::identity(&args.improved)?,"runtime":runtime,
            "receiver_bindings":bindings,"profile":input::identity(&args.output.join("grsat-profile.yml"))?,"release_receipt":release_id,
            "workers":args.workers,"threads":args.threads,"decoder_seconds":args.decoder_seconds,"arms":ARMS,
            "source_archive_used_for_search":false,"network_submission":false,"pcm_conversion":"ffmpeg -nostdin -v error -n -i ORIGINAL -map 0:a:0 -c:a pcm_s16le -flags:a +bitexact -fflags +bitexact SHARED",
            "v2_additional_information":"original Vorbis packet features, verified original->PCM16 sample equality","v1_sideinfo":false,
            "binary_and_selected_gr_source_hashes_not_full_system_closure":true,"cohort_metadata_frozen_before_runner_started":true,
            "progressive_receiver_binding":{"path":V3,"sha256":V3_SHA,"arm_label_is_algorithm_lineage_not_binary_identity":true},"protocol_path":PROTOCOL_PATH});
        saved["selected_runtime_sha256"] = selected_runtime_digest(&saved)?.into();
        input::write_json_new(&manifest_path, &saved)?;
        saved
    };
    manifest["manifest_sha256"] = input::identity(&manifest_path)?.sha256.into();
    verify_runtime(&manifest)?;
    let mut admission_identity = None;
    if let Some(release) = release_id.as_ref() {
        let mut expected = json!({"runner":manifest["runner"],"progressive":manifest["receiver_bindings"]["progressive_v3"],
            "improved":manifest["improved"],"protocol":input::identity(Path::new(PROTOCOL_PATH))?,
            "baselines":{"innovation_v1_no_codec":manifest["receiver_bindings"]["innovation_v1_no_codec"],
                "direwolf":manifest["receiver_bindings"]["direwolf"],"gr_satellites":manifest["receiver_bindings"]["gr_satellites"]},
            "profile_sha256":manifest["profile"]["sha256"],
            "selected_runtime_sha256":selected_runtime_digest(&manifest)?,
            "workers":args.workers,"threads":args.threads,"decoder_seconds":args.decoder_seconds});
        if REPAIRED_BUILD {
            expected["base_protocol"] = json!(input::identity(Path::new(BASE_PROTOCOL_PATH))?);
            expected["analysis_protocol"] =
                json!(input::identity(Path::new(ANALYSIS_PROTOCOL_PATH))?);
            expected["selection_registration"] =
                json!(input::identity(Path::new(SELECTION_REGISTRATION_PATH))?);
            expected["selection_amendment"] =
                json!(input::identity(Path::new(SELECTION_AMENDMENT_PATH))?);
        }
        let admission = release_admission(
            release,
            current_id.as_ref(),
            &manifest["cohort"],
            &expected,
            utc_seconds(&json!(io::utc()))?,
            !args.analyze_only,
        )?;
        let audit_dir = tempfile::Builder::new()
            .prefix("admission-")
            .tempdir_in(&args.output)
            .map_err(|e| e.to_string())?
            .keep();
        input::write_json_new(&audit_dir.join("admission.json"), &admission)?;
        admission_identity = Some(input::identity(&audit_dir.join("admission.json"))?);
    }
    if args.preflight_only {
        let preflight = json!({"schema":"innovation-benchmark-preflight-v1","status":"preflight_complete",
            "manifest":input::identity(&manifest_path)?,"release_receipt":release_id,"cohort":manifest["cohort"],"admission":admission_identity,
            "waveform_reads":0,"decoder_runs":0,"benchmark_complete":false});
        io::replace_json(&args.output.join("preflight.json"), &preflight)?;
        println!("{preflight}");
        return Ok(());
    }
    if args.analyze_only {
        let value = summarize(&args, rows)?;
        println!(
            "{}",
            json!({"selected":value["selected"],"complete":value["all_five_complete"],"counts":value["primary_complete_case_observation_pdu_counts"]})
        );
        return require_no_attempt_failures(&value);
    }
    let selected: Vec<_> = rows
        .iter()
        .enumerate()
        .filter(|(_, r)| {
            let path = args.output.join(format!("obs-{}/result.json", r["id"]));
            match input::read_json(&path) {
                Ok(v) => v["status"] != "complete",
                Err(_) => true,
            }
        })
        .take(args.max_new_observations.unwrap_or(usize::MAX))
        .collect();
    let next = AtomicUsize::new(0);
    std::thread::scope(|scope| {
        for _ in 0..args.workers {
            scope.spawn(|| loop {
                let n=next.fetch_add(1,Ordering::Relaxed);
                if n>=selected.len(){break;}
                let(index,row)=selected[n];
                match run_observation(&args,row,index,&manifest) {
                    Ok(value)=>println!("{}",json!({"id":value["observation_id"],"status":value["status"],"counts":ARMS.into_iter().map(|a|(a,value["decoders"][a]["strict_ui_unique_count"].clone())).collect::<BTreeMap<_,_>>()})),
                    Err(e)=>eprintln!("observation {}: {e}",row["id"])
                }
            });
        }
    });
    let value = summarize(&args, rows)?;
    println!(
        "{}",
        json!({"selected":value["selected"],"complete":value["all_five_complete"],"counts":value["primary_complete_case_observation_pdu_counts"],"summary":args.output.join("summary.json")})
    );
    require_no_attempt_failures(&value)
}
fn main() {
    if let Err(error) = run() {
        eprintln!("{error}");
        std::process::exit(1);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn compiled_receiver_revision_is_an_exact_pair() {
        assert_eq!(repaired_receiver_hashes(None).unwrap(), (REPAIRED_PROGRESSIVE_SHA, REPAIRED_INNOVATION_SHA));
        let new = repaired_receiver_hashes(Some("lossless-20260912-v1")).unwrap();
        assert_ne!(new.0, REPAIRED_PROGRESSIVE_SHA);
        assert_ne!(new.1, REPAIRED_INNOVATION_SHA);
        assert_eq!(new.0.len(), 64);
        assert_eq!(new.1.len(), 64);
        assert!(repaired_receiver_hashes(Some("")).is_err());
        assert!(repaired_receiver_hashes(Some("latest")).is_err());
    }

    #[test]
    fn report_recovery_refuses_other_failures_and_attempt_path_escape() {
        let temp = tempfile::tempdir().unwrap();
        let observation = temp.path().join("obs-1");
        fs::create_dir(&observation).unwrap();
        let manifest_path = temp.path().join("manifest.json");
        input::write_json_new(&manifest_path, &json!({})).unwrap();
        let path = observation.join("result.json");
        let mut document = json!({"status":"incomplete","decoders":{"innovation_v2":{
            "schema":ARM_SCHEMA,"arm":"innovation_v2","status":"failed_or_incomplete",
            "manifest_sha256":input::identity(&manifest_path).unwrap().sha256,
            "error":"different failure"
        }}});
        input::write_json_new(&path, &document).unwrap();
        assert!(recover_large_native_report(&path).unwrap_err().contains("report-size-only"));
        let outside = temp.path().join("attempt-outside");
        fs::create_dir(&outside).unwrap();
        document["decoders"]["innovation_v2"]["error"] = "JSON exceeds 64 MiB limit".into();
        document["decoders"]["innovation_v2"]["process"] = json!({"stdout":{"path":outside.join("run.stdout.log")}});
        io::replace_json(&path, &document).unwrap();
        let before = input::identity(&path).unwrap();
        assert!(recover_large_native_report(&path).unwrap_err().contains("outside"));
        assert_eq!(input::identity(&path).unwrap().sha256, before.sha256);
    }
    fn fixture_frozen() -> Value {
        let mut bindings = serde_json::Map::new();
        for name in ARMS {
            bindings.insert(name.into(),json!({"path":format!("/fixture/{name}"),"bytes":1,"sha256":if name=="progressive_v3"{V3_SHA}else{"fixture"}}));
        }
        json!({"schema":MANIFEST_SCHEMA,"runtime":[],"receiver_bindings":bindings,"threads":2,"workers":4,"decoder_seconds":900,
            "profile":{"path":"/fixture/profile.yml","bytes":1,"sha256":"fixture"}})
    }
    fn fixture_pcm() -> Value {
        json!({"path":"/fixture/common.wav","sha256":"pcm","bytes":19244,"samples":9600,"sample_rate":48000,"bits_per_sample":16})
    }
    fn fixture_native(dir: &Path, arm: &str, pcm: &Value, source: &Value, frozen: &Value) {
        fs::create_dir_all(dir.join("decode")).unwrap();
        let result = if arm == "progressive_v3" {
            input::write_json_new(&dir.join("decode/manifest.json"),&json!({"schema":"progressive-audio-session-v2","executable_sha256":V3_SHA,
                "audio":{"source":pcm,"wav":pcm,"sample_rate":48000,"samples":9600,"conversion_command":null},"policy":full_policy()})).unwrap();
            let session = input::identity(&dir.join("decode/manifest.json"))
                .unwrap()
                .sha256;
            fs::create_dir(dir.join("decode/tasks")).unwrap();
            for stage in STAGES {
                let task = json!({"schema":"progressive-audio-task-v1","session_sha256":session,"stage":stage,"window":0,
                    "elapsed_seconds":0.0,"frame_with_fcs_hex":[],"detail":[]});
                let hash = hex::encode(Sha256::digest(serde_json::to_vec(&task).unwrap()));
                io::new_text(
                    &dir.join(format!("decode/tasks/{stage}-000000.json")),
                    &serde_json::to_string(&json!({"sha256":hash,"task":task})).unwrap(),
                )
                .unwrap();
            }
            json!({"schema":"progressive-audio-result-v1","complete":true,"status":"complete","session_sha256":session,
                "window_count":1,"total_tasks":7,"completed_tasks":7,"stage_counts":STAGES.into_iter().map(|s|(s,1)).collect::<BTreeMap<_,_>>(),
                "stage_frame_with_fcs_hex":STAGES.into_iter().map(|s|(s,Vec::<String>::new())).collect::<BTreeMap<_,_>>(),"union_count":0,"frame_with_fcs_hex":[]})
        } else {
            let v2 = arm == "innovation_v2";
            let binding = if v2 {
                json!({"kind":"canonical_pcm16_wav_byte_identity","codec_source":source,"analysis_input":pcm,"derived_pcm16":pcm,"same_numeric_input_verified":true})
            } else {
                Value::Null
            };
            let config = json!({"baud":9600.0,"threads":frozen["threads"]});
            input::write_json_new(&dir.join("decode/plan.json"),&json!({"schema":if v2{"innovation-audio-plan-v2"}else{"innovation-audio-plan-v1"},
                "executable":frozen["receiver_bindings"][arm],"source":pcm,"input":pcm,"config":config,"codec_sideinfo":v2,
                "pooled_codec":if v2{json!(true)}else{Value::Null},"codec_input_binding":binding,"conversion_command":null,
                "reference_bytes_used_for_search":false,"crc_guided_bit_repair":false,"target_truth_used_for_training":false,"recursive_training":false})).unwrap();
            if v2 {
                input::write_json_new(&dir.join("decode/codec-metadata.json"),&json!({"schema":"vorbis-packet-reliability-evidence-v1","sample_rate":48000,"decoded_samples":9600,"source":source})).unwrap();
            }
            json!({"schema":if v2{"innovation-audio-result-v2"}else{"innovation-audio-result-v1"},"status":"complete","source":pcm,"pooled_codec":if v2{json!(true)}else{Value::Null},
                "config":config,"sample_rate_hz":48000,"samples":9600,"union_count":0,"codec_input_binding":binding,
                "report":{"union_full_frames":[],"previous_innovation_union_full_frames":[],"lane_full_frames":{"pooled-codec-innovations":[]},"pooled_matched_unweighted_full_frames":[],"added_vs_previous_innovation":[],"pooled_added_vs_matched_unweighted":[],"pooled_lost_vs_matched_unweighted":[]}})
        };
        input::write_json_new(&dir.join("decode/result.json"), &result).unwrap();
    }
    fn fixture_process(
        dir: &Path,
        name: &str,
        pcm: &Value,
        source: &Value,
        frozen: &Value,
    ) -> Value {
        if !dir.join("run.stdout.log").exists() {
            io::new_text(&dir.join("run.stdout.log"), "").unwrap();
        }
        io::new_text(&dir.join("run.stderr.log"), "").unwrap();
        io::new_text(
            &dir.join("run.rusage.txt"),
            "fixture only; no process executed",
        )
        .unwrap();
        let (program, args) = invocation(name, dir, pcm, source, frozen).unwrap();
        let process = json!({"program":program,"args":args,"success":true,"returncode":0,"timed_out":false,
            "stdout":input::identity(&dir.join("run.stdout.log")).unwrap(),"stderr":input::identity(&dir.join("run.stderr.log")).unwrap(),"usage_path":dir.join("run.rusage.txt"),
            "wall_seconds":1.0,"user_cpu_seconds":1.0,"system_cpu_seconds":0.0,"maximum_rss_kib":10.0,
            "environment":{"GR_SATELLITES_SUBMIT_TLM":"0","OPENBLAS_NUM_THREADS":"1","OMP_NUM_THREADS":"1","MKL_NUM_THREADS":"1"}});
        input::write_json_new(&dir.join("run.process.json"), &process).unwrap();
        process
    }
    #[test]
    fn independent_received_crc_rejects_corruption() {
        let mut data = b"123456789".to_vec();
        data.extend([0x6e, 0x90]);
        assert!(fcs(&data));
        data[0] ^= 1;
        assert!(!fcs(&data));
        assert!(!fcs(&[]));
    }
    #[test]
    fn progressive_rejects_task_session_policy_and_coverage_drift() {
        for which in 0..7 {
            let temp = tempfile::tempdir().unwrap();
            let pcm = fixture_pcm();
            let source = json!({});
            let frozen = fixture_frozen();
            fixture_native(temp.path(), "progressive_v3", &pcm, &source, &frozen);
            assert!(score_native("progressive_v3", temp.path(), &pcm, &source, &frozen).is_ok());
            let result_path = temp.path().join("decode/result.json");
            let mut result = input::read_json(&result_path).unwrap();
            match which {
                0 => result["completed_tasks"] = 0.into(),
                1 => result["status"] = "incomplete".into(),
                2 => result["session_sha256"] = "wrong".into(),
                3 => result["stage_counts"]["blind"] = 0.into(),
                4 => {
                    let path = temp.path().join("decode/manifest.json");
                    let mut m = input::read_json(&path).unwrap();
                    m["policy"]["baud"] = 1200.into();
                    io::replace_json(&path, &m).unwrap();
                }
                5 => {
                    let path = temp.path().join("decode/tasks/quick-000000.json");
                    let mut t = input::read_json(&path).unwrap();
                    t["task"]["window"] = 42.into();
                    io::replace_json(&path, &t).unwrap();
                }
                _ => {
                    fs::rename(
                        temp.path().join("decode/tasks/blind-000000.json"),
                        temp.path().join("decode/tasks/unexpected.json"),
                    )
                    .unwrap();
                }
            }
            io::replace_json(&result_path, &result).unwrap();
            assert!(
                score_native("progressive_v3", temp.path(), &pcm, &source, &frozen).is_err(),
                "case {which}"
            );
        }
    }
    #[test]
    fn innovation_rejects_wrong_plan_build_config_and_codec_metadata() {
        for which in 0..5 {
            let temp = tempfile::tempdir().unwrap();
            let pcm = fixture_pcm();
            let source = json!({"sha256":"ogg","bytes":20});
            let frozen = fixture_frozen();
            fixture_native(temp.path(), "innovation_v2", &pcm, &source, &frozen);
            assert!(score_native("innovation_v2", temp.path(), &pcm, &source, &frozen).is_ok());
            let path = temp.path().join(if which == 4 {
                "decode/codec-metadata.json"
            } else {
                "decode/plan.json"
            });
            let mut doc = input::read_json(&path).unwrap();
            match which {
                0 => doc["executable"]["sha256"] = "wrong".into(),
                1 => doc["config"]["baud"] = 1200.into(),
                2 => doc["codec_sideinfo"] = false.into(),
                3 => doc["codec_input_binding"]["codec_source"]["sha256"] = "wrong".into(),
                _ => doc["source"]["sha256"] = "wrong".into(),
            }
            io::replace_json(&path, &doc).unwrap();
            assert!(score_native("innovation_v2", temp.path(), &pcm, &source, &frozen).is_err());
        }
        let temp = tempfile::tempdir().unwrap();
        let pcm = fixture_pcm();
        let frozen = fixture_frozen();
        fixture_native(
            temp.path(),
            "innovation_v1_no_codec",
            &pcm,
            &json!({}),
            &frozen,
        );
        assert!(
            score_native(
                "innovation_v1_no_codec",
                temp.path(),
                &pcm,
                &json!({}),
                &frozen
            )
            .is_ok(),
            "historical V1 omits pooled-codec fields"
        );
    }
    #[test]
    fn runtime_and_exact_process_receipts_fail_closed() {
        let temp = tempfile::tempdir().unwrap();
        let pcm = fixture_pcm();
        let source = json!({"path":"/fixture/source.ogg"});
        let frozen = fixture_frozen();
        let original = fixture_process(temp.path(), "direwolf", &pcm, &source, &frozen);
        assert!(
            process_evidence("direwolf", temp.path(), &pcm, &source, &frozen, &original).is_ok()
        );
        for key in [
            "success",
            "returncode",
            "timed_out",
            "program",
            "args",
            "environment",
        ] {
            let mut changed = original.clone();
            changed[key] = match key {
                "success" => json!(false),
                "returncode" => json!(1),
                "timed_out" => json!(true),
                "program" => json!("/wrong"),
                "args" => json!(["wrong-input"]),
                _ => json!({}),
            };
            io::replace_json(&temp.path().join("run.process.json"), &changed).unwrap();
            assert!(
                process_evidence("direwolf", temp.path(), &pcm, &source, &frozen, &changed)
                    .is_err(),
                "case {key}"
            );
        }
        let runtime = temp.path().join("runtime.json");
        input::write_json_new(&runtime, &json!({"v":1})).unwrap();
        let manifest = json!({"runtime":[input::identity(&runtime).unwrap()]});
        assert!(verify_runtime(&manifest).is_ok());
        io::replace_json(&runtime, &json!({"v":2})).unwrap();
        assert!(verify_runtime(&manifest).is_err());
    }
    #[test]
    fn selected_runtime_freeze_changes_for_modules_but_not_profile_relocation() {
        let mut manifest = json!({"profile":{"path":"/a/profile.yml","sha256":"profile","bytes":20},
            "runtime":[{"path":"/a/profile.yml","sha256":"profile","bytes":20},{"path":"/installed/demod.py","sha256":"module1","bytes":100}]});
        let initial = selected_runtime_digest(&manifest).unwrap();
        manifest["profile"]["path"] = "/b/profile.yml".into();
        manifest["runtime"][0]["path"] = "/b/profile.yml".into();
        assert_eq!(initial, selected_runtime_digest(&manifest).unwrap());
        manifest["runtime"][1]["sha256"] = "module2".into();
        assert_ne!(initial, selected_runtime_digest(&manifest).unwrap());
        manifest["runtime"][0]["sha256"] = "changed-profile".into();
        assert!(selected_runtime_digest(&manifest).is_err());
    }
    fn fixture_release(dir: &Path) -> (Value, Value, Value) {
        let put = |name: &str, value: Value| {
            let path = dir.join(name);
            input::write_json_new(&path, &value).unwrap();
            json!(input::identity(&path).unwrap())
        };
        let row = |id| json!({"id":id,"norad_cat_id":68635,"transmitter":"GCmN6RULea8dAT7Qoat8z2","start":"2026-01-01T00:00:00Z","end":"2026-01-01T00:10:00Z"});
        let original = json!({"schema":"innovation-benchmark-cohort-v2","selected_count":2,"frozen_utc":"2026-01-02T00:00:00Z",
            "plan":{"mode":"GMSK","baud":9600,"norad_cat_id":68635},"selection_ids_sha256":hex::encode(Sha256::digest(b"1\n2")),"observations":[row(1),row(2)]});
        let source = put("source.json", original.clone());
        let exposure = put(
            "exposure.json",
            json!({"schema":"recording-exposure-manifest-v1","finished_utc":"2026-01-02T01:00:00Z"}),
        );
        let cohort = put(
            "cohort.json",
            json!({"schema":"innovation-benchmark-cohort-v2","selected_count":1,"frozen_utc":"2026-01-02T01:00:00Z",
            "plan":original["plan"],"source_cohort":source,"exposure_audit":exposure,"source_selection_ids_sha256":original["selection_ids_sha256"],
            "selection_ids_sha256":hex::encode(Sha256::digest(b"2")),"observations":[row(2)],"original_ranks":[1],
            "current_exposure_check_passed":true,"evaluation_release_permitted":true,"fresh_independent_holdout_qualified":false}),
        );
        let scope = "historical-local-exposure-reviewed";
        let lineage = put(
            "lineage.json",
            json!({"schema":"innovation-lineage-review-v1","status":"complete","complete_review":true,"scope":scope,
            "in_scope_blocking_findings":[],"source_cohort":source,"exposure_manifest":exposure}),
        );
        let expected =
            json!({"runner":{"fixture":true},"workers":4,"threads":2,"decoder_seconds":900});
        let mut freeze = expected.clone();
        freeze["schema"] = "innovation-receiver-freeze-v1".into();
        freeze["status"] = "complete".into();
        freeze["frozen_utc"] = "2026-01-02T00:00:00Z".into();
        let freeze = put("freeze.json", freeze);
        let current = put(
            "current.json",
            json!({"schema":"innovation-current-exposure-check-v1","status":"complete","complete_review":true,"scope":scope,
            "in_scope_blocking_findings":[],"cohort":cohort,"exposure_manifest":exposure,"no_new_in_scope_exposure":true,"checked_utc":"2026-01-02T01:01:00Z"}),
        );
        let release = put(
            "release.json",
            json!({"schema":"innovation-waveform-release-v1","status":"complete","complete_review":true,"scope":scope,
            "in_scope_blocking_findings":[],"cohort":cohort,"source_cohort":source,"exposure_manifest":exposure,"lineage_review":lineage,"receiver_freeze":freeze,
            "current_exposure_check":current,"current_exposure_check_passed":true,"evaluation_release_permitted":true,"no_development_during_evaluation":true,
            "selected_count":1,"selection_ids_sha256":hex::encode(Sha256::digest(b"2")),"released_utc":"2026-01-02T01:02:00Z"}),
        );
        (release, cohort, expected)
    }
    #[test]
    fn release_gate_rejects_candidate_flags_stale_checks_missing_evidence_and_wrong_freeze() {
        let now = utc_seconds(&json!("2026-01-02T01:03:00Z")).unwrap();
        for which in 0..8 {
            let temp = tempfile::tempdir().unwrap();
            let (release, cohort, expected) = fixture_release(temp.path());
            release_admission(&release, None, &cohort, &expected, now, true).unwrap();
            let mut r = checked_document(&release).unwrap();
            match which {
                0 => r["evaluation_release_permitted"] = false.into(),
                1 => r["complete_review"] = false.into(),
                2 => r["in_scope_blocking_findings"] = json!(["unresolved"]),
                3 => r["lineage_review"] = Value::Null,
                4 => r["selected_count"] = 2.into(),
                5 => r["schema"] = "unknown".into(),
                6 => r["no_development_during_evaluation"] = false.into(),
                _ => r["current_exposure_check_passed"] = "true".into(),
            }
            let path = temp.path().join("bad-release.json");
            input::write_json_new(&path, &r).unwrap();
            let bad = json!(input::identity(&path).unwrap());
            assert!(
                release_admission(&bad, None, &cohort, &expected, now, true).is_err(),
                "case {which}"
            );
            assert!(
                release_admission(&release, None, &cohort, &expected, now + 3601, true).is_err()
            );
            let mut wrong = expected.clone();
            wrong["threads"] = 8.into();
            assert!(release_admission(&release, None, &cohort, &wrong, now, true).is_err());
        }
    }
    #[test]
    fn release_rank_and_current_review_gates_are_not_boolean_shortcuts() {
        let now = utc_seconds(&json!("2026-01-02T01:03:00Z")).unwrap();
        for which in 0..5 {
            let temp = tempfile::tempdir().unwrap();
            let (release, cohort, expected) = fixture_release(temp.path());
            let mut c = checked_document(&cohort).unwrap();
            let mut r = checked_document(&release).unwrap();
            match which {
                0 => c["original_ranks"] = json!([0]),
                1 => c["observations"][0]["ground_station"] = 999.into(),
                2 => c["plan"]["mode"] = "FSK".into(),
                3 => c["evaluation_release_permitted"] = false.into(),
                _ => c["fresh_independent_holdout_qualified"] = true.into(),
            }
            let path = temp.path().join("changed-cohort.json");
            input::write_json_new(&path, &c).unwrap();
            let c_id = json!(input::identity(&path).unwrap());
            r["cohort"] = c_id.clone();
            let path = temp.path().join("changed-release.json");
            input::write_json_new(&path, &r).unwrap();
            let r_id = json!(input::identity(&path).unwrap());
            let reason = release_admission(&r_id, None, &c_id, &expected, now, true).unwrap_err();
            assert!(
                reason.contains("rank") || reason.contains("cohort") || reason.contains("plan")
            );
        }
        let temp = tempfile::tempdir().unwrap();
        let (release, cohort, expected) = fixture_release(temp.path());
        let r = checked_document(&release).unwrap();
        let initial = checked_document(&r["current_exposure_check"]).unwrap();
        let copied_path = temp.path().join("copied-cohort.json");
        fs::copy(cohort["path"].as_str().unwrap(), &copied_path).unwrap();
        let copied = json!(input::identity(&copied_path).unwrap());
        let relocated = release_admission(&release, None, &copied, &expected, now, true).unwrap();
        assert_eq!(relocated["cohort"], copied);
        assert_eq!(relocated["registered_cohort"], cohort);
        let mut refreshed = initial.clone();
        refreshed["checked_utc"] = "2026-01-02T02:03:00Z".into();
        let path = temp.path().join("refresh.json");
        input::write_json_new(&path, &refreshed).unwrap();
        let fresh_id = json!(input::identity(&path).unwrap());
        assert!(
            release_admission(
                &release,
                Some(&fresh_id),
                &cohort,
                &expected,
                now + 3601,
                true
            )
            .is_ok(),
            "same frozen cohort may resume under refreshed admission"
        );
        for which in 0..4 {
            let mut check = initial.clone();
            match which {
                0 => check["status"] = "partial".into(),
                1 => check["cohort"]["sha256"] = "wrong".into(),
                2 => check["no_new_in_scope_exposure"] = false.into(),
                _ => check["checked_utc"] = "2026-01-02T00:59:00Z".into(),
            }
            let path = temp.path().join(format!("bad-current-{which}.json"));
            input::write_json_new(&path, &check).unwrap();
            let id = json!(input::identity(&path).unwrap());
            assert!(release_admission(&release, Some(&id), &cohort, &expected, now, true).is_err());
        }
    }
    fn fixture_prospective_release(dir: &Path) -> (Value, Value, Value) {
        let (release, cohort, expected) = fixture_release(dir);
        let mut r = checked_document(&release).unwrap();
        let mut original = checked_document(&r["source_cohort"]).unwrap();
        let mut c = checked_document(&cohort).unwrap();
        let put = |name: &str, doc: Value| {
            let path = dir.join(name);
            input::write_json_new(&path, &doc).unwrap();
            json!(input::identity(&path).unwrap())
        };
        let selection = json!({"schema":"station-day-round-robin-v1","group_rank":"fixed-unit-test-rank","row_rank":"fixed-unit-test-row"});
        let filters = json!({"norad_cat_id":68635,"start":"2026-01-01T00:00:00Z","start__lt":"2026-01-01T01:00:00Z"});
        let registration = put(
            "registration.json",
            json!({"schema":"innovation-prospective-selection-v1","registration_guard_seconds":7200,
            "require_full_receiver_freeze_before_window_start_minus_guard":true,"registered_utc":"2025-12-31T21:00:00Z",
            "observation_start_inclusive_utc":"2026-01-01T00:00:00Z","observation_start_exclusive_utc":"2026-01-01T01:00:00Z",
            "observation_end_must_not_exceed_utc":"2026-01-01T01:00:00Z","target_maximum":500,"norad_cat_id":68635,"baud":9600,"mode":"GMSK",
            "transmitter_uuid":"GCmN6RULea8dAT7Qoat8z2","selection":selection,"metadata_query":{"filters":filters,"outcome_filters":[]}}),
        );
        let amendment = put(
            "amendment.json",
            json!({"schema":"innovation-prospective-selection-amendment-v1","status":"registered_before_observation_window",
            "source_protocol":registration,"original_protocol_preserved":true,"declared_utc":"2025-12-31T21:01:00Z"}),
        );
        original["selection_registration"] = registration.clone();
        original["selection_amendment"] = amendment.clone();
        original["plan"]["selection"] = selection;
        original["plan"]["metadata_filters"] = filters;
        original["plan"]["outcome_filters"] = json!([]);
        let original_id = put("future-source.json", original.clone());
        c["source_cohort"] = original_id.clone();
        c["plan"] = original["plan"].clone();
        let cohort_id = put("future-cohort.json", c);
        r["scope"] = "prospective-after-freeze".into();
        for field in [
            "lineage_review",
            "current_exposure_check",
            "receiver_freeze",
        ] {
            let mut doc = checked_document(&r[field]).unwrap();
            if field == "receiver_freeze" {
                doc["frozen_utc"] = "2025-12-31T21:59:00Z".into();
                doc["selection_registration"] = registration.clone();
                doc["selection_amendment"] = amendment.clone();
            } else {
                doc["scope"] = r["scope"].clone();
                if field == "lineage_review" {
                    doc["source_cohort"] = original_id.clone();
                } else {
                    doc["cohort"] = cohort_id.clone();
                }
            }
            r[field] = put(&format!("future-{field}.json"), doc);
        }
        r["source_cohort"] = original_id.clone();
        r["cohort"] = cohort_id.clone();
        r["prospective_protocol"] = registration.clone();
        r["selection_amendment"] = amendment.clone();
        r["selection_review"] = put(
            "selection-review.json",
            json!({"schema":"innovation-selection-review-v1","status":"complete","complete_review":true,
            "scope":"prospective-after-freeze","in_scope_blocking_findings":[],"source_cohort":original_id,
            "selection_registration":registration,"selection_amendment":amendment,"selected_count":2,"eligible_count":2,
            "selection_ids_sha256":original["selection_ids_sha256"],"true_eof_verified":true,"exact_selection_replay_verified":true,"no_outcome_filtering_verified":true,
            "evidence":[registration]}),
        );
        (put("future-release.json", r), cohort_id, expected)
    }
    #[test]
    fn prospective_admission_requires_future_window_and_pre_window_freeze() {
        let temp = tempfile::tempdir().unwrap();
        let (release, cohort, expected) = fixture_prospective_release(temp.path());
        let now = utc_seconds(&json!("2026-01-02T01:03:00Z")).unwrap();
        release_admission(&release, None, &cohort, &expected, now, true).unwrap();
        let mut r = checked_document(&release).unwrap();
        let mut late = checked_document(&r["receiver_freeze"]).unwrap();
        late["frozen_utc"] = "2025-12-31T22:00:01Z".into();
        let path = temp.path().join("late-freeze.json");
        input::write_json_new(&path, &late).unwrap();
        r["receiver_freeze"] = json!(input::identity(&path).unwrap());
        let path = temp.path().join("late-release.json");
        input::write_json_new(&path, &r).unwrap();
        let id = json!(input::identity(&path).unwrap());
        assert!(
            release_admission(&id, None, &cohort, &expected, now, true)
                .unwrap_err()
                .contains("guard")
        );
    }
    #[test]
    fn prospective_dates_do_not_substitute_for_selection_replay_and_eof_review() {
        let now = utc_seconds(&json!("2026-01-02T01:03:00Z")).unwrap();
        for field in [
            "true_eof_verified",
            "exact_selection_replay_verified",
            "no_outcome_filtering_verified",
            "complete_review",
        ] {
            let temp = tempfile::tempdir().unwrap();
            let (release, cohort, expected) = fixture_prospective_release(temp.path());
            let mut r = checked_document(&release).unwrap();
            let mut review = checked_document(&r["selection_review"]).unwrap();
            review[field] = false.into();
            let path = temp.path().join("bad-review.json");
            input::write_json_new(&path, &review).unwrap();
            r["selection_review"] = json!(input::identity(&path).unwrap());
            let path = temp.path().join("bad-release.json");
            input::write_json_new(&path, &r).unwrap();
            assert!(
                release_admission(
                    &json!(input::identity(&path).unwrap()),
                    None,
                    &cohort,
                    &expected,
                    now,
                    true
                )
                .is_err()
            );
        }
    }
    #[test]
    #[ignore = "read-only scoring of existing development receipts, no new DSP"]
    fn existing_repaired_development_receipts_score_without_redecode() {
        use std::os::unix::fs::symlink;
        let temp = tempfile::tempdir().unwrap();
        let native = temp.path().join("native");
        let innovation = temp.path().join("innovation");
        fs::create_dir(&native).unwrap();
        fs::create_dir(&innovation).unwrap();
        symlink("/home/ubuntu/telemetry-yield/work/decoder-readiness-20260911/shared-lossless-comparison-v2/native",native.join("decode")).unwrap();
        symlink("/home/ubuntu/telemetry-yield/work/decoder-readiness-20260911/repaired-innovation14967362-codec",innovation.join("decode")).unwrap();
        let mut frozen = fixture_frozen();
        let manifest = input::read_json(&native.join("decode/manifest.json")).unwrap();
        let mut pcm = manifest["audio"]["source"].clone();
        pcm["samples"] = manifest["audio"]["samples"].clone();
        pcm["sample_rate"] = 48000.into();
        pcm["bits_per_sample"] = 16.into();
        assert_eq!(
            score_native("progressive_v3", &native, &pcm, &json!({}), &frozen).unwrap()["strict_ui_unique_count"],
            2
        );
        let plan = input::read_json(&innovation.join("decode/plan.json")).unwrap();
        let result = input::read_json(&innovation.join("decode/result.json")).unwrap();
        frozen["receiver_bindings"]["innovation_v2"] = plan["executable"].clone();
        let mut pcm = plan["input"].clone();
        pcm["samples"] = result["samples"].clone();
        pcm["sample_rate"] = 48000.into();
        pcm["bits_per_sample"] = 16.into();
        assert!(
            score_native(
                "innovation_v2",
                &innovation,
                &pcm,
                &plan["codec_input_binding"]["codec_source"],
                &frozen
            )
            .is_ok()
        );
    }
    #[test]
    fn common_sample_identity_ignores_only_path() {
        assert!(same_bytes(
            &json!({"sha256":"abc","bytes":3,"path":"a"}),
            &json!({"sha256":"abc","bytes":3,"path":"b"})
        ));
        assert!(!same_bytes(
            &json!({"sha256":"abc","bytes":3}),
            &json!({"sha256":"abc","bytes":4})
        ));
        assert!(!same_bytes(&Value::Null, &Value::Null));
    }
    #[test]
    fn overlapping_stations_share_pass_cluster() {
        let rows = vec![
            json!({"id":1,"norad_cat_id":1,"start":"2026-01-01T00:00:00Z","end":"2026-01-01T00:10:00Z"}),
            json!({"id":2,"norad_cat_id":1,"start":"2026-01-01T00:09:00Z","end":"2026-01-01T00:20:00Z"}),
            json!({"id":3,"norad_cat_id":1,"start":"2026-01-01T00:21:00Z","end":"2026-01-01T00:22:00Z"}),
            json!({"id":4,"norad_cat_id":2,"start":"2026-01-01T00:00:00Z","end":"2026-01-01T00:10:00Z"}),
        ];
        let g = groups(&rows).unwrap();
        assert_eq!(g[&1], g[&2]);
        assert_ne!(g[&1], g[&3]);
        assert_ne!(g[&1], g[&4]);
    }
    #[test]
    fn bootstrap_is_seeded_and_zero_baseline_is_not_infinite() {
        let rows: Vec<_> = (0..20).map(|n| (n, 1, 0, 1)).collect();
        let first = intervals(&rows);
        assert_eq!(first, intervals(&rows));
        assert_eq!(
            first["net_additional_pdus_per_observation_percentile95"],
            json!([1.0, 1.0])
        );
        assert!(first["relative_pdu_gain_percent_percentile95"].is_null());
        assert_eq!(intervals(&rows[..2])["available"], false);
    }
    #[test]
    fn crc_alone_does_not_accept_non_ax25_ui() {
        let frame = "3132333435363738396e90";
        assert!(native_set(&json!([frame])).is_err());
    }
    #[test]
    fn frozen_selection_rejects_order_and_count_drift() {
        let row = |id| json!({"id":id,"norad_cat_id":68635,"transmitter":"GCmN6RULea8dAT7Qoat8z2","start":"2026-01-01T00:00:00Z","end":"2026-01-01T00:10:00Z"});
        let mut cohort = json!({"schema":"innovation-benchmark-cohort-v2","selected_count":2,"frozen_utc":"2026-01-02T00:00:00Z","plan":{"mode":"GMSK","baud":9600,"norad":68635},
            "selection_ids_sha256":hex::encode(Sha256::digest(b"1\n2")),"observations":[row(1),row(2)]});
        assert!(verify_cohort(&cohort).is_ok());
        cohort["observations"].as_array_mut().unwrap().reverse();
        assert!(verify_cohort(&cohort).is_err());
        cohort["observations"].as_array_mut().unwrap().reverse();
        cohort["selected_count"] = 3.into();
        assert!(verify_cohort(&cohort).is_err());
        cohort["selected_count"] = 2.into();
        cohort["plan"]["norad_cat_id"] = 68635.into();
        cohort["plan"].as_object_mut().unwrap().remove("norad");
        assert!(
            verify_cohort(&cohort).is_ok(),
            "actual acquisition plan uses norad_cat_id"
        );
        cohort["plan"]["norad"] = 1.into();
        assert!(
            verify_cohort(&cohort).is_err(),
            "conflicting aliases must not pass"
        );
        cohort["plan"].as_object_mut().unwrap().remove("norad");
        cohort["plan"]["baud"] = 1200.into();
        assert!(verify_cohort(&cohort).is_err());
        cohort["plan"]["baud"] = 9600.into();
        cohort["frozen_utc"] = "2025-12-31T00:00:00Z".into();
        assert!(verify_cohort(&cohort).is_err());
    }
    #[test]
    fn failed_committed_attempt_is_not_zero_and_manifest_is_bound() {
        let temp = tempfile::tempdir().unwrap();
        let root = temp.path().join("obs-1/arms/innovation_v2");
        let dir = root.join("attempt-test");
        fs::create_dir_all(&dir).unwrap();
        input::write_json_new(&temp.path().join("manifest.json"), &json!({"test":1})).unwrap();
        let source = json!({"path":"source.ogg","bytes":2,"sha256":"test"});
        let value = json!({"arm":"innovation_v2","status":"timeout","source":source,"artifacts":[],
            "manifest_sha256":input::identity(&temp.path().join("manifest.json")).unwrap().sha256});
        input::write_json_new(&dir.join("result.json"), &value).unwrap();
        input::write_json_new(
            &root.join("committed.json"),
            &json!({"record":input::identity(&dir.join("result.json")).unwrap()}),
        )
        .unwrap();
        let saved = committed(&root, &source, None).unwrap().unwrap();
        assert_eq!(saved["status"], "timeout");
        assert!(saved["strict_ui_payloads"].is_null());
        let mut mislabeled = value.clone();
        mislabeled["arm"] = "innovation_v1_no_codec".into();
        io::replace_json(&dir.join("result.json"), &mislabeled).unwrap();
        io::replace_json(
            &root.join("committed.json"),
            &json!({"record":input::identity(&dir.join("result.json")).unwrap()}),
        )
        .unwrap();
        assert!(
            committed(&root, &source, None)
                .unwrap_err()
                .contains("arm label")
        );
        io::replace_json(&dir.join("result.json"), &value).unwrap();
        io::replace_json(
            &root.join("committed.json"),
            &json!({"record":input::identity(&dir.join("result.json")).unwrap()}),
        )
        .unwrap();
        io::replace_json(&temp.path().join("manifest.json"), &json!({"test":2})).unwrap();
        assert!(committed(&root, &source, None).is_err());
    }
    #[test]
    fn common_input_attestation_is_checked_even_for_empty_decode() {
        let temp = tempfile::tempdir().unwrap();
        let pcm = fixture_pcm();
        let source = json!({"path":"/fixture/source.ogg","sha256":"ogg","bytes":20});
        let frozen = fixture_frozen();
        fixture_native(temp.path(), "innovation_v2", &pcm, &source, &frozen);
        assert!(score_native("innovation_v2", temp.path(), &pcm, &source, &frozen).is_ok());
        let path = temp.path().join("decode/result.json");
        let mut result = input::read_json(&path).unwrap();
        result["codec_input_binding"]["derived_pcm16"]["sha256"] = "different".into();
        io::replace_json(&path, &result).unwrap();
        assert!(score_native("innovation_v2", temp.path(), &pcm, &source, &frozen).is_err());
    }
    #[test]
    fn milestones_succeed_but_attempted_failures_do_not() {
        assert!(
            require_no_attempt_failures(
                &json!({"incomplete_observations":[],"not_yet_attempted":[2,3]})
            )
            .is_ok()
        );
        assert!(
            require_no_attempt_failures(
                &json!({"incomplete_observations":[{"id":1,"status":"timeout"}]})
            )
            .is_err()
        );
        assert!(require_no_attempt_failures(&json!({})).is_err());
    }
    #[test]
    fn observation_directory_cannot_relabel_result() {
        assert!(require_observation_label(&json!({"observation_id":12}), 12).is_ok());
        assert!(require_observation_label(&json!({"observation_id":13}), 12).is_err());
        assert!(require_observation_label(&json!({}), 12).is_err());
    }
    #[test]
    fn sparse_baseline_bootstrap_does_not_silently_condition_ratio() {
        let rows: Vec<_> = (0..20).map(|n| (n, 1, u64::from(n == 0), 1)).collect();
        let value = intervals(&rows);
        assert!(value["relative_zero_baseline_resamples"].as_u64().unwrap() > 0);
        assert!(value["relative_defined_resamples"].as_u64().unwrap() > 0);
        assert_eq!(
            value["relative_zero_baseline_resamples"].as_u64().unwrap()
                + value["relative_defined_resamples"].as_u64().unwrap(),
            10000
        );
        assert!(value["relative_pdu_gain_percent_percentile95"].is_null());
        assert_eq!(
            value["net_additional_pdus_per_observation_percentile95"],
            json!([1.0, 1.0])
        );
    }
    #[test]
    fn five_valid_arms_do_not_override_final_observation_failure_or_pcm_mismatch() {
        let temp = tempfile::tempdir().unwrap();
        let acquisition = temp.path().join("acquisition");
        let output = temp.path().join("benchmark");
        fs::create_dir_all(acquisition.join("obs-1")).unwrap();
        fs::create_dir_all(output.join("obs-1")).unwrap();
        io::new_text(&acquisition.join("obs-1/capture.ogg"), "OggS-test-source").unwrap();
        let source = json!(input::identity(&acquisition.join("obs-1/capture.ogg")).unwrap());
        let pcm = fixture_pcm();
        let frozen = fixture_frozen();
        input::write_json_new(&output.join("manifest.json"), &frozen).unwrap();
        let manifest_sha = input::identity(&output.join("manifest.json"))
            .unwrap()
            .sha256;
        for name in ARMS {
            let root = output.join("obs-1/arms").join(name);
            let dir = root.join("attempt-test");
            fs::create_dir_all(&dir).unwrap();
            let mut artifacts = Vec::new();
            if name == "direwolf" {
                io::new_text(
                    &dir.join("run.stdout.log"),
                    "Fix Bits level = 0\n0 packets decoded in 0.0 seconds.\n",
                )
                .unwrap();
            } else if name == "gr_satellites" {
                io::new_text(&dir.join("frames.kiss"), "").unwrap();
                artifacts.push(json!(input::identity(&dir.join("frames.kiss")).unwrap()));
            } else {
                fixture_native(&dir, name, &pcm, &source, &frozen);
                let scored = score_native(name, &dir, &pcm, &source, &frozen).unwrap();
                artifacts.extend(scored["artifacts"].as_array().unwrap().iter().cloned());
            }
            let process = fixture_process(&dir, name, &pcm, &source, &frozen);
            for path in [
                "run.stdout.log",
                "run.stderr.log",
                "run.process.json",
                "run.rusage.txt",
            ] {
                artifacts.push(json!(input::identity(&dir.join(path)).unwrap()));
            }
            let value = json!({"schema":ARM_SCHEMA,"arm":name,"status":"complete","source":source,"input":pcm,"artifacts":artifacts,
                "manifest_sha256":manifest_sha,"strict_ui_payloads":[],"strict_ui_unique_count":0,"all_payloads":[],"emitted_count":0,"process":process,
                "runtime_checks":{"before":true,"after":true,"runtime_sha256":runtime_digest(&frozen).unwrap()}});
            input::write_json_new(&dir.join("result.json"), &value).unwrap();
            input::write_json_new(
                &root.join("committed.json"),
                &json!({"record":input::identity(&dir.join("result.json")).unwrap()}),
            )
            .unwrap();
        }
        let state_path = output.join("obs-1/result.json");
        let mut state = json!({"observation_id":1,"status":"incomplete","source":source,"input":pcm,"error":"final runtime revalidation failed"});
        input::write_json_new(&state_path, &state).unwrap();
        let args = Args {
            acquisition,
            output,
            improved: PathBuf::from("unused"),
            workers: 4,
            threads: 2,
            decoder_seconds: 900,
            acquisition_wait_seconds: 0,
            max_new_observations: None,
            resume: true,
            retry_failed: false,
            analyze_only: true,
            release_receipt: None,
            current_exposure_check: None,
            preflight_only: false,
        };
        let rows = vec![
            json!({"id":1,"norad_cat_id":68635,"start":"2026-01-01T00:00:00Z","end":"2026-01-01T00:10:00Z"}),
        ];
        let value = summarize(&args, &rows).unwrap();
        assert_eq!(value["all_five_complete"], 0);
        for name in ARMS {
            assert_eq!(value["per_arm_complete"][name], 1);
        }
        assert!(require_no_attempt_failures(&value).is_err());
        state["status"] = "complete".into();
        state["error"] = Value::Null;
        io::replace_json(&state_path, &state).unwrap();
        assert_eq!(summarize(&args, &rows).unwrap()["all_five_complete"], 1);
        state["input"]["sha256"] = "different-pcm".into();
        io::replace_json(&state_path, &state).unwrap();
        let mismatch = summarize(&args, &rows).unwrap();
        assert_eq!(mismatch["all_five_complete"], 0);
        assert_eq!(
            mismatch["incomplete_observations"][0]["common_pcm_identity_verified"],
            false
        );
        assert!(require_no_attempt_failures(&mismatch).is_err());
    }
}
