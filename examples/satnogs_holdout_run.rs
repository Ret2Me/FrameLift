//! Frozen, append-only SatNOGS OGG holdout execution; no DSP tuning here.
#[path = "support/holdout_run_io.rs"]
mod io;
use clap::Parser;
use serde_json::{Value, json};
use std::{
    collections::{BTreeMap, BTreeSet},
    fs::{self, File, OpenOptions},
    os::fd::AsRawFd,
    path::{Path, PathBuf},
    sync::{
        Mutex,
        atomic::{AtomicBool, AtomicUsize, Ordering},
    },
};
use telemetry_yield_rs::{formats, input, protocol};

const PROBE_SHA: &str = "455a9622ba7c839fbba1c575b8139502a9b24c2b3f99421adb2e9ab74bb37ab3";
const DEFAULT_PROBE: &str = "/home/ubuntu/telemetry-yield/work/receiver-improvements-20260908/tracking-v1-release/tracking_audio_probe";
const BASELINE: &str = "/home/ubuntu/telemetry-yield/work/golden/env/bin/gr_satellites";
const BASELINE_SHA: &str = "86059e664af0e12ade1a5c3c52a7c14dc8fbe87f3febafdee1b1626e1f4827f7";
const PROTOCOL_SHA: &str = "ddfe8c45f2c0adf86571eda3603c0796a4c7937db6902f1b076e35cc09607c28";
const PROFILE: &str = "name: Frozen public OGG baseline\nnorad: 0\ndata:\n  &raw Raw: unknown\ntransmitters:\n  selected:\n    frequency: 437000000\n    modulation: FSK\n    baudrate: 9600\n    framing: AX.25 G3RUH\n    data:\n    - *raw\n";

#[derive(Parser)]
struct Args {
    #[arg(long)]
    cohort_root: PathBuf,
    #[arg(long)]
    output: PathBuf,
    #[arg(long)]
    runtime_manifest: PathBuf,
    #[arg(long, default_value_t = 2)]
    workers: usize,
    #[arg(long, default_value_t = 2)]
    threads: usize,
    #[arg(long, default_value = DEFAULT_PROBE)]
    probe: PathBuf,
    #[arg(long)]
    resume: bool,
    #[arg(long)]
    retry_failed: bool,
}

fn identities(value: &Value, out: &mut Vec<input::Identity>) -> Result<(), String> {
    if value.is_object()
        && value.get("path").is_some()
        && value.get("sha256").is_some()
        && value.get("bytes").is_some()
    {
        out.push(serde_json::from_value(value.clone()).map_err(|e| e.to_string())?);
    } else if let Some(map) = value.as_object() {
        for child in map.values() {
            identities(child, out)?;
        }
    } else if let Some(array) = value.as_array() {
        for child in array {
            identities(child, out)?;
        }
    }
    Ok(())
}

fn verify_identity(expected: &input::Identity) -> Result<(), String> {
    let actual = input::identity(Path::new(&expected.path))?;
    if actual.sha256 != expected.sha256 || actual.bytes != expected.bytes {
        return Err(format!("identity mismatch: {}", expected.path));
    }
    Ok(())
}

fn verify_freeze(root: &Path) -> Result<Vec<input::Identity>, String> {
    let freeze = input::read_json(&root.join("freeze.json"))?;
    if freeze["pagination_complete"] != true
        || freeze["outcomes_computed"] != false
        || freeze["audio_downloaded"] != false
    {
        return Err("cohort freeze is not complete metadata-only preanalysis".into());
    }
    let mut found = vec![];
    identities(&freeze, &mut found)?;
    for item in &mut found {
        if !Path::new(&item.path).is_absolute() {
            item.path = root.join(&item.path).display().to_string();
        }
        verify_identity(item)?;
    }
    let cohort = input::identity(&root.join("cohort.json"))?;
    if !found
        .iter()
        .any(|item| item.sha256 == cohort.sha256 && item.bytes == cohort.bytes)
    {
        return Err("freeze does not bind cohort.json".into());
    }
    let document = input::read_json(&root.join("cohort.json"))?;
    let identity: input::Identity =
        serde_json::from_value(document["protocol_identity"].clone())
            .map_err(|e| format!("cohort must bind preanalysis protocol: {e}"))?;
    if identity.sha256 != PROTOCOL_SHA {
        return Err("runner requires frozen v1 preanalysis protocol SHA".into());
    }
    if !found
        .iter()
        .any(|item| item.sha256 == identity.sha256 && item.bytes == identity.bytes)
    {
        return Err("freeze does not bind preanalysis protocol".into());
    }
    Ok(found)
}

fn selection(document: &Value) -> Result<Vec<(Value, Value)>, String> {
    let rows = document["observations"]
        .as_array()
        .ok_or("observations list missing")?;
    let choices = document["selection"]
        .as_array()
        .ok_or("selection list missing")?;
    if rows.is_empty() || rows.len() > 336 || rows.len() != choices.len() {
        return Err("cohort must contain 1..336 matched selections and rows".into());
    }
    let mut indexed = BTreeMap::new();
    for row in rows {
        let id = row["id"]
            .as_u64()
            .filter(|id| *id > 0)
            .ok_or("positive integer observation ID required")?;
        if indexed.insert(id, row.clone()).is_some() {
            return Err("duplicate observation ID".into());
        }
    }
    let mut ordered = vec![];
    for choice in choices {
        let id = choice["id"].as_u64().ok_or("selection ID missing")?;
        let row = indexed
            .remove(&id)
            .ok_or("selection IDs duplicate or inconsistent")?;
        for key in ["utc_day", "rank_sha256"] {
            if choice[key].as_str().filter(|s| !s.is_empty()).is_none() {
                return Err(format!("selection {key} missing"));
            }
        }
        if !choice["stratum"]
            .as_u64()
            .is_some_and(|stratum| stratum < 12)
        {
            return Err("selection stratum must be integer0..11".into());
        }
        ordered.push((row, choice.clone()));
    }
    Ok(ordered)
}

/// Independent bit-at-a-time reflected X.25, not the receiver's lookup table.
fn independent_fcs(frame: &[u8]) -> bool {
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

fn validated_payloads(value: &Value) -> Result<BTreeSet<String>, String> {
    let mut out = BTreeSet::new();
    for frame in value.as_array().ok_or("native full frames must be array")? {
        let frame =
            hex::decode(frame.as_str().ok_or("frame hex required")?).map_err(|e| e.to_string())?;
        if !independent_fcs(&frame) {
            return Err("native frame failed independent FCS".into());
        }
        let payload = &frame[..frame.len() - 2];
        if protocol::parse_ax25_ui(payload).is_none() {
            return Err("native frame failed strict AX25 UI structure".into());
        }
        out.insert(hex::encode(payload));
    }
    Ok(out)
}

fn difference(left: &BTreeSet<String>, right: &BTreeSet<String>) -> Vec<String> {
    left.difference(right).cloned().collect()
}

fn references(row: &Value, dir: &Path) -> Value {
    let Some(list) = row["demoddata"]
        .as_array()
        .filter(|list| list.len() <= 1000)
    else {
        return json!({"complete":false,"objects":[],"failures":["reference list absent, malformed, or above 1000 objects"]});
    };
    let (mut objects, mut failures, mut bytes) = (vec![], vec![], 0u64);
    for (index, entry) in list.iter().enumerate() {
        let attempt = (|| {
            let remaining = (32 * 1024 * 1024u64).saturating_sub(bytes);
            if remaining == 0 {
                return Err("reference aggregate 32 MiB cap".to_string());
            }
            let url = entry["payload_demod"]
                .as_str()
                .ok_or("payload_demod URL missing")?;
            let target = dir
                .join("references")
                .join(format!("reference-{index:04}.bin"));
            let receipt = io::fetch(url, &target, remaining.min(1024 * 1024))?;
            let raw = input::read_bytes_bounded(&target, 1024 * 1024)?;
            Ok(
                json!({"url":url,"identity":receipt["identity"],"payload_hex":hex::encode(&raw),
                "strict_ax25_ui":protocol::parse_ax25_ui(&raw).is_some(),"independently_verified_crc":false}),
            )
        })();
        match attempt {
            Ok(object) => objects.push(object),
            Err(error) => failures.push(json!({"index":index,"error":error})),
        }
        // Count failed/redirect HTTP bodies too, but not hard-linked final copies.
        bytes = fs::read_dir(dir.join("references"))
            .map(|entries| {
                entries
                    .filter_map(Result::ok)
                    .filter(|entry| entry.file_name().to_string_lossy().ends_with(".body"))
                    .filter_map(|entry| entry.metadata().ok().map(|m| m.len()))
                    .sum()
            })
            .unwrap_or(0);
    }
    json!({"complete":failures.is_empty() && objects.len() == list.len(),"listed_count":list.len(),"objects":objects,"failures":failures,
        "bytes":bytes,"raw_archive_bytes_are_not_prefix_stripped":true,"empty_complete_list_is_unlabeled_not_negative_control":list.is_empty()})
}

fn process(
    state: &mut Value,
    dir: &Path,
    label: &str,
    program: &Path,
    args: Vec<String>,
    seconds: u64,
    cap: u64,
) -> Result<(), String> {
    state["stage"] = label.into();
    let receipt = io::execute(program, &args, dir, label, seconds, cap)?;
    state["processes"][label] = receipt.clone();
    io::require_success(&receipt)
}

fn score(state: &mut Value, dir: &Path, expected_pcm: &input::Identity) -> Result<(), String> {
    let result = input::read_json(&dir.join("probe/result.json"))?;
    if result["status"] != "complete"
        || result["observation_id"] != state["observation_id"]
        || result["input"]["sha256"] != expected_pcm.sha256
        || result["source"]["sha256"] != expected_pcm.sha256
    {
        return Err("probe identity or completion mismatch".into());
    }
    if result["bit_repair_enabled"] != false || result["reference_bytes_used_for_search"] != false {
        return Err("unexpected repair or reference search".into());
    }
    let variants = result["variant_full_frames"]
        .as_object()
        .ok_or("variant frames missing")?;
    let expected: BTreeSet<_> = [
        "legacy-fir512/fixed-full160",
        "boxcar32-rms50/fixed-full160",
        "legacy-fir512/gardner16",
        "boxcar32-rms50/gardner16",
    ]
    .into_iter()
    .collect();
    if variants.keys().map(String::as_str).collect::<BTreeSet<_>>() != expected {
        return Err("probe four-variant contract mismatch".into());
    }
    let mut sets = BTreeMap::new();
    for (variant, frames) in variants {
        sets.insert(variant.clone(), validated_payloads(frames)?);
    }
    let union = validated_payloads(&result["union_full_frames"])?;
    if union != sets.values().flatten().cloned().collect() {
        return Err("probe union differs from variant union".into());
    }
    let kiss = input::read_bytes_bounded(&dir.join("baseline.kiss"), 64 * 1024 * 1024)?;
    let parsed = formats::parse_kiss(&kiss)?;
    if parsed.malformed_records != 0 {
        return Err("baseline KISS malformed".into());
    }
    let (mut baseline, mut other) = (BTreeSet::new(), BTreeSet::new());
    for frame in &parsed.data_frames {
        if protocol::parse_ax25_ui(&frame.payload).is_some() {
            baseline.insert(hex::encode(&frame.payload));
        } else {
            other.insert(hex::encode(&frame.payload));
        }
    }
    let archive: BTreeSet<_> = state["references"]["objects"]
        .as_array()
        .ok_or("reference objects missing")?
        .iter()
        .filter_map(|item| item["payload_hex"].as_str().map(str::to_owned))
        .collect();
    state["variant_payloads"] = json!(sets);
    state["union_payloads"] = json!(union);
    state["baseline_payloads"] = json!(baseline);
    state["baseline_other_payloads"] = json!(other);
    state["baseline_raw_kiss_records"] = json!(parsed.data_frames.len());
    state["baseline_fcs_observable"] = false.into();
    state["archive_payloads"] = json!(archive);
    let archive_complete = state["references"]["complete"] == true;
    state["comparison"] = json!({"added_vs_baseline":difference(&union, &baseline),"lost_vs_baseline":difference(&baseline, &union),
        "added_vs_archive":archive_complete.then(|| difference(&union,&archive)),
        "added_vs_archive_and_baseline":archive_complete.then(|| difference(&union, &archive.union(&baseline).cloned().collect()))});
    state["validation"] = json!({"independent_bitwise_fcs":true,"all_native_strict_ax25_ui":true,"same_pcm_sha256":true,
        "baseline_fcs_is_decoder_acceptance_not_independent_recheck":true,"transmitter_authenticated":false,
        "extra_candidate_secondary_replay":"pending","extra_candidate_origin_audit":"pending"});
    state["probe_result_identity"] = json!(input::identity(&dir.join("probe/result.json"))?);
    verify_identity(expected_pcm)?;
    Ok(())
}

fn all_files(root: &Path, paths: &mut Vec<PathBuf>) -> Result<(), String> {
    for entry in fs::read_dir(root).map_err(|e| e.to_string())? {
        let entry = entry.map_err(|e| e.to_string())?;
        let kind = entry.file_type().map_err(|e| e.to_string())?;
        if kind.is_symlink() {
            return Err("symlink in owned attempt".into());
        }
        if kind.is_dir() {
            all_files(&entry.path(), paths)?;
        } else if kind.is_file() && entry.file_name() != "commit.json" {
            paths.push(entry.path());
        }
    }
    Ok(())
}

fn finish(dir: &Path, mut state: Value) -> Result<Value, String> {
    state["finished_utc"] = io::utc().into();
    input::write_json_new(&dir.join("result.json"), &state)?;
    let result_identity = input::identity(&dir.join("result.json"))?;
    let mut paths = vec![];
    all_files(dir, &mut paths)?;
    paths.sort();
    let artifacts = paths
        .iter()
        .map(|path| input::identity(path))
        .collect::<Result<Vec<_>, _>>()?;
    let commit = json!({"schema":"satnogs-holdout-observation-commit-v1","result_identity":result_identity,"artifacts":artifacts});
    input::write_json_new(&dir.join("commit.json"), &commit)?;
    Ok(
        json!({"id":state["observation_id"],"status":state["status"],"reference_complete":state["references"]["complete"],
        "result_identity":result_identity,"commit_identity":input::identity(&dir.join("commit.json"))?}),
    )
}

fn one(
    args: &Args,
    row: &Value,
    choice: &Value,
    runtime: &[input::Identity],
) -> Result<Value, String> {
    let id = row["id"].as_u64().ok_or("ID missing")?;
    let parent = args.output.join("observations").join(id.to_string());
    fs::create_dir_all(&parent).map_err(|e| e.to_string())?;
    let mut number = 1;
    while parent.join(format!("attempt-{number:03}")).exists() {
        number += 1;
    }
    if number > 3 {
        return Err(format!("observation {id}: three-attempt cap"));
    }
    let dir = parent.join(format!("attempt-{number:03}"));
    fs::create_dir(&dir).map_err(|e| e.to_string())?;
    let mut state = json!({"schema":"satnogs-holdout-observation-v1","observation_id":id,"status":"failed","stage":"initialize","started_utc":io::utc(),
        "selection":choice,"metadata":{"start":row["start"],"end":row["end"],"ground_station":row["ground_station"],"satellite":row["satellite"],"transmitter":row["transmitter"],"demodulated":row["demodulated"]},
        "processes":{},"references":{"complete":false,"objects":[]},"comparison":null,
        "candidate_attribution":"unverified; observation metadata does not prove transmitter origin"});
    input::write_json_new(&dir.join("observation.json"), row)?;
    input::write_json_new(&dir.join("start.json"), &state)?;
    let outcome = (|| {
        for item in runtime {
            verify_identity(item)?;
        }
        io::disk_guard(&args.output)?;
        state["stage"] = "references".into();
        state["references"] = references(row, &dir);
        state["archive_payloads"] = json!(
            state["references"]["objects"]
                .as_array()
                .ok_or("reference objects missing")?
                .iter()
                .filter_map(|item| item["payload_hex"].as_str().map(str::to_owned))
                .collect::<BTreeSet<_>>()
        );
        let Some(url) = row["payload"].as_str().filter(|url| !url.is_empty()) else {
            state["status"] = "no_audio".into();
            return Ok(());
        };
        state["stage"] = "audio_download".into();
        let source = dir.join("capture.ogg");
        state["source_receipt"] = io::fetch(url, &source, 64 * 1024 * 1024)?;
        state["source"] = json!(input::identity(&source)?);
        process(
            &mut state,
            &dir,
            "ffprobe",
            Path::new("/usr/bin/ffprobe"),
            vec![
                "-v".into(),
                "error".into(),
                "-show_streams".into(),
                "-show_format".into(),
                "-of".into(),
                "json".into(),
                source.display().to_string(),
            ],
            60,
            1024 * 1024,
        )?;
        let meta = input::read_json(&dir.join("ffprobe.stdout.log"))?;
        let streams = meta["streams"].as_array().ok_or("audio streams missing")?;
        let duration = meta["format"]["duration"]
            .as_str()
            .ok_or("audio duration missing")?
            .parse::<f64>()
            .map_err(|e| e.to_string())?;
        if streams.len() != 1
            || streams[0]["codec_type"] != "audio"
            || streams[0]["channels"] != 1
            || streams[0]["sample_rate"] != "48000"
            || !duration.is_finite()
            || !(8192.0 / 48000.0..=1800.0).contains(&duration)
        {
            state["status"] = "unsupported_audio".into();
            return Err("requires whole observation mono 48000 Hz and duration 8192/48000..1800 s; no resampling".into());
        }
        let wav = dir.join("audio.wav");
        io::disk_guard(&args.output)?;
        process(
            &mut state,
            &dir,
            "convert",
            Path::new("/usr/bin/ffmpeg"),
            vec![
                "-nostdin".into(),
                "-v".into(),
                "error".into(),
                "-n".into(),
                "-i".into(),
                source.display().to_string(),
                "-map".into(),
                "0:a:0".into(),
                "-c:a".into(),
                "pcm_f32le".into(),
                wav.display().to_string(),
            ],
            120,
            512 * 1024 * 1024,
        )?;
        let reader = hound::WavReader::open(&wav).map_err(|e| e.to_string())?;
        let spec = reader.spec();
        if spec.channels != 1
            || spec.sample_rate != 48000
            || spec.sample_format != hound::SampleFormat::Float
            || spec.bits_per_sample != 32
            || !(8192..=48000 * 1800).contains(&reader.duration())
        {
            return Err("PCM conversion contract failed".into());
        }
        state["audio_seconds"] = json!(f64::from(reader.duration()) / 48000.0);
        drop(reader);
        let pcm = input::identity(&wav)?;
        state["input"] = json!(pcm);
        input::write_json_new(
            &dir.join("pcm-ownership.json"),
            &json!({"generated_by_this_attempt":true,"identity":pcm,"source":state["source"],"conversion":state["processes"]["convert"]}),
        )?;
        process(
            &mut state,
            &dir,
            "probe",
            &args.probe,
            vec![
                "--input".into(),
                wav.display().to_string(),
                "--output".into(),
                dir.join("probe").display().to_string(),
                "--observation-id".into(),
                id.to_string(),
                "--threads".into(),
                args.threads.to_string(),
            ],
            1200,
            64 * 1024 * 1024,
        )?;
        verify_identity(&pcm)?;
        io::new_text(&dir.join("baseline-profile.yml"), PROFILE)?;
        process(
            &mut state,
            &dir,
            "baseline",
            Path::new(BASELINE),
            vec![
                dir.join("baseline-profile.yml").display().to_string(),
                "--wavfile".into(),
                wav.display().to_string(),
                "--kiss_out".into(),
                dir.join("baseline.kiss").display().to_string(),
                "--hexdump".into(),
            ],
            1200,
            64 * 1024 * 1024,
        )?;
        state["stage"] = "score".into();
        score(&mut state, &dir, &pcm)?;
        let expected_source: input::Identity =
            serde_json::from_value(state["source"].clone()).map_err(|e| e.to_string())?;
        verify_identity(&expected_source)?;
        for item in runtime {
            verify_identity(item)?;
        }
        state["status"] = "complete".into();
        Ok::<(), String>(())
    })();
    if let Err(error) = outcome {
        if error.starts_with("disk guard:") {
            state["status"] = "paused_disk".into();
        }
        state["error"] = error.into();
    }
    finish(&dir, state)
}

fn prior(output: &Path, id: u64) -> Result<Option<Value>, String> {
    let dir = output.join("observations").join(id.to_string());
    if !dir.exists() {
        return Ok(None);
    }
    let mut entries = fs::read_dir(dir)
        .map_err(|e| e.to_string())?
        .collect::<Result<Vec<_>, _>>()
        .map_err(|e| e.to_string())?;
    entries.sort_by_key(|entry| entry.file_name());
    for entry in entries.iter().rev() {
        let commit_path = entry.path().join("commit.json");
        if !commit_path.is_file() {
            continue;
        }
        let commit = input::read_json(&commit_path)?;
        let identity: input::Identity =
            serde_json::from_value(commit["result_identity"].clone()).map_err(|e| e.to_string())?;
        verify_identity(&identity)?;
        let mut artifacts = vec![];
        identities(&commit["artifacts"], &mut artifacts)?;
        for item in &artifacts {
            verify_identity(item)?;
        }
        let result = input::read_json(Path::new(&identity.path))?;
        if result["observation_id"] != id {
            return Err("committed observation ID mismatch".into());
        }
        return Ok(Some(
            json!({"id":id,"status":result["status"],"reference_complete":result["references"]["complete"],"result_identity":identity,"commit_identity":input::identity(&commit_path)?}),
        ));
    }
    Ok(None)
}

fn summary(results: &[Value], total: usize, running: bool) -> Value {
    let count = |status: &str| results.iter().filter(|row| row["status"] == status).count();
    let completed = count("complete");
    let no_audio = count("no_audio");
    json!({"schema":"satnogs-holdout-summary-v1","updated_utc":io::utc(),"running":running,"total":total,"completed":completed,
        "no_audio":no_audio,"failed":results.len()-completed-no_audio,"pending":total-results.len(),
        "reference_incomplete":results.iter().filter(|row| row["reference_complete"] != true).count(),"observations":results,
        "archive_novelty_scope":"only frozen selected archive objects, not the entire SatNOGS database","timing_is_concurrent_not_isolated":true})
}

fn run(args: &mut Args) -> Result<Value, String> {
    if args.workers != 2 || args.threads != 2 {
        return Err("frozen v1 protocol requires workers2 and threads2".into());
    }
    if args.retry_failed && !args.resume {
        return Err("--retry-failed requires --resume".into());
    }
    args.cohort_root = args.cohort_root.canonicalize().map_err(|e| e.to_string())?;
    args.probe = args.probe.canonicalize().map_err(|e| e.to_string())?;
    args.runtime_manifest = args
        .runtime_manifest
        .canonicalize()
        .map_err(|e| e.to_string())?;
    let runtime_manifest = input::read_json(&args.runtime_manifest)?;
    let mut closure = vec![];
    identities(&runtime_manifest["files"], &mut closure)?;
    if closure.is_empty() {
        return Err("runtime manifest must contain full baseline file identities".into());
    }
    for identity in &closure {
        verify_identity(identity)?;
    }
    let frozen = verify_freeze(&args.cohort_root)?;
    let cohort = input::read_json(&args.cohort_root.join("cohort.json"))?;
    let selected = selection(&cohort)?;
    let probe = input::identity(&args.probe)?;
    if probe.sha256 != PROBE_SHA {
        return Err("only frozen tracking-v1 probe SHA permitted".into());
    }
    if input::identity(Path::new(BASELINE))?.sha256 != BASELINE_SHA {
        return Err("baseline executable differs from frozen preanalysis identity".into());
    }
    let mut runtime = [
        args.probe.as_path(),
        Path::new(BASELINE),
        Path::new("/usr/bin/ffmpeg"),
        Path::new("/usr/bin/ffprobe"),
        Path::new("/usr/bin/curl"),
        Path::new("/usr/bin/time"),
    ]
    .into_iter()
    .map(input::identity)
    .collect::<Result<Vec<_>, _>>()?;
    identities(&runtime_manifest["critical_files"], &mut runtime)?;
    for identity in &runtime {
        verify_identity(identity)?;
    }
    if args.resume {
        if !args.output.is_dir() {
            return Err("--resume output does not exist".into());
        }
    } else {
        fs::create_dir(&args.output).map_err(|e| format!("new output directory required: {e}"))?;
    }
    args.output = args.output.canonicalize().map_err(|e| e.to_string())?;
    let lock = OpenOptions::new()
        .read(true)
        .write(true)
        .create(true)
        .truncate(false)
        .open(args.output.join("campaign.lock"))
        .map_err(|e| e.to_string())?;
    if unsafe { libc::flock(lock.as_raw_fd(), libc::LOCK_EX | libc::LOCK_NB) } != 0 {
        return Err("campaign already locked".into());
    }
    let plan = json!({"schema":"satnogs-holdout-run-plan-v1","cohort":input::identity(&args.cohort_root.join("cohort.json"))?,
        "freeze":input::identity(&args.cohort_root.join("freeze.json"))?,"frozen_artifacts":frozen,"runtime":runtime,
        "runtime_manifest":input::identity(&args.runtime_manifest)?,
        "runner":input::identity(&std::env::current_exe().map_err(|e| e.to_string())?)?,"workers":args.workers,"threads":args.threads,"baseline_profile":PROFILE,
        "duration_max_seconds":1800,"process_timeout_seconds":1200,"ogg_max_bytes":64*1024*1024,"reference_object_max_bytes":1024*1024,
        "reference_total_max_bytes":32*1024*1024,"no_resampling":true,"baseline":"gr-satellites installed FSK9600 AX.25 G3RUH, not historical station pipeline"});
    if args.resume {
        if input::read_json(&args.output.join("plan.json"))? != plan {
            return Err("resume frozen runtime/plan identity mismatch".into());
        }
    } else {
        input::write_json_new(&args.output.join("plan.json"), &plan)?;
    }
    let mut records = vec![];
    let mut todo = vec![];
    for (row, choice) in &selected {
        match prior(&args.output, row["id"].as_u64().unwrap())? {
            Some(record)
                if !args.retry_failed
                    || record["status"] == "complete"
                    || record["status"] == "no_audio"
                    || record["status"] == "unsupported_audio" =>
            {
                records.push(record)
            }
            _ => todo.push((row.clone(), choice.clone())),
        }
    }
    records.sort_by_key(|row| row["id"].as_u64());
    let results = Mutex::new(records);
    let errors = Mutex::new(Vec::<String>::new());
    let cursor = AtomicUsize::new(0);
    let stop = AtomicBool::new(false);
    io::replace_json(
        &args.output.join("summary.json"),
        &summary(&results.lock().unwrap(), selected.len(), true),
    )?;
    std::thread::scope(|scope| {
        for _ in 0..args.workers {
            let args = &*args;
            let (todo, runtime, results, errors, cursor, stop) =
                (&todo, &runtime, &results, &errors, &cursor, &stop);
            let total = selected.len();
            scope.spawn(move || {
                while !stop.load(Ordering::SeqCst) {
                    let index = cursor.fetch_add(1, Ordering::SeqCst);
                    let Some((row, choice)) = todo.get(index) else {
                        break;
                    };
                    match one(args, row, choice, runtime) {
                        Ok(record) => {
                            if record["status"] == "paused_disk" {
                                stop.store(true, Ordering::SeqCst);
                            }
                            let mut list = results.lock().unwrap();
                            list.push(record);
                            list.sort_by_key(|row| row["id"].as_u64());
                            if let Err(error) = io::replace_json(
                                &args.output.join("summary.json"),
                                &summary(&list, total, true),
                            ) {
                                errors.lock().unwrap().push(error);
                                stop.store(true, Ordering::SeqCst);
                            }
                        }
                        Err(error) => {
                            errors.lock().unwrap().push(error);
                            stop.store(true, Ordering::SeqCst);
                        }
                    }
                }
            });
        }
    });
    for identity in closure.iter().chain(frozen.iter()).chain(runtime.iter()) {
        if let Err(error) = verify_identity(identity) {
            errors.lock().unwrap().push(error);
        }
    }
    let mut final_summary = summary(&results.into_inner().unwrap(), selected.len(), false);
    final_summary["fatal_errors"] = json!(errors.into_inner().unwrap());
    final_summary["runtime_integrity_at_finish"] = json!(
        final_summary["fatal_errors"]
            .as_array()
            .is_some_and(Vec::is_empty)
    );
    io::replace_json(&args.output.join("summary.json"), &final_summary)?;
    File::open(&args.output)
        .and_then(|file| file.sync_all())
        .map_err(|e| e.to_string())?;
    drop(lock);
    Ok(final_summary)
}

fn main() {
    match run(&mut Args::parse()) {
        Ok(value) => {
            println!(
                "{}",
                json!({"total":value["total"],"completed":value["completed"],"failed":value["failed"],"no_audio":value["no_audio"],"pending":value["pending"]})
            );
            if value["pending"].as_u64().unwrap_or(1) != 0
                || !value["fatal_errors"].as_array().is_some_and(Vec::is_empty)
            {
                std::process::exit(2);
            }
        }
        Err(error) => {
            eprintln!("{error}");
            std::process::exit(1);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn fixture_payload() -> Vec<u8> {
        let mut payload: Vec<u8> = b"CANVAS".iter().map(|c| c << 1).collect();
        payload.push(0x60);
        payload.extend(b"LASP  ".iter().map(|c| c << 1));
        payload.extend([0x61, 3, 0xf0, 1, 2, 3]);
        assert!(protocol::parse_ax25_ui(&payload).is_some());
        payload
    }
    #[test]
    fn independent_crc_known_vector_and_corruption() {
        let mut data = b"123456789".to_vec();
        data.extend([0x6e, 0x90]);
        assert!(independent_fcs(&data));
        data[0] ^= 1;
        assert!(!independent_fcs(&data));
        assert!(!independent_fcs(&[]));
    }
    #[test]
    fn valid_crc_is_not_enough_for_payload_acceptance() {
        assert!(validated_payloads(&json!(["3132333435363738396e90"])).is_err());
        assert!(validated_payloads(&json!([])).unwrap().is_empty());
        assert!(validated_payloads(&Value::Null).is_err());
    }
    #[test]
    fn selection_rejects_duplicate_and_mismatched_ids() {
        let choice = json!({"id":1,"utc_day":"2026-08-01","stratum":0,"rank_sha256":"abc"});
        assert!(
            selection(&json!({"observations":[{"id":1}],"selection":[choice.clone()]})).is_ok()
        );
        assert!(
            selection(&json!({"observations":[{"id":2}],"selection":[choice.clone()]})).is_err()
        );
        assert!(
            selection(
                &json!({"observations":[{"id":1},{"id":1}],"selection":[choice.clone(),choice]})
            )
            .is_err()
        );
    }
    #[test]
    fn empty_complete_is_not_failed() {
        let summary = summary(
            &[
                json!({"id":1,"status":"complete","reference_complete":true}),
                json!({"id":2,"status":"failed","reference_complete":false}),
            ],
            3,
            false,
        );
        assert_eq!(summary["completed"], 1);
        assert_eq!(summary["failed"], 1);
        assert_eq!(summary["pending"], 1);
    }
    #[test]
    fn raw_reference_prefix_and_set_dedup_are_preserved() {
        let values: BTreeSet<String> = ["0000ca00", "0000ca00", "beef"]
            .into_iter()
            .map(str::to_owned)
            .collect();
        assert_eq!(values.len(), 2);
        assert_eq!(
            difference(&values, &BTreeSet::from(["beef".into()])),
            vec!["0000ca00"]
        );
    }
    #[test]
    fn committed_artifacts_are_immutable_and_tamper_checked() {
        use std::io::Write;
        let temp = tempfile::tempdir().unwrap();
        let attempt = temp.path().join("observations/1/attempt-001");
        fs::create_dir_all(&attempt).unwrap();
        io::new_text(&attempt.join("retained.bin"), "evidence").unwrap();
        let state = json!({"observation_id":1,"status":"complete","references":{"complete":true}});
        let record = finish(&attempt, state.clone()).unwrap();
        assert_eq!(prior(temp.path(), 1).unwrap(), Some(record));
        assert!(finish(&attempt, state).is_err());
        let mut artifact = OpenOptions::new()
            .append(true)
            .open(attempt.join("retained.bin"))
            .unwrap();
        artifact.write_all(b"tampered").unwrap();
        assert!(prior(temp.path(), 1).is_err());
    }
    #[test]
    fn scoring_keeps_nonui_baseline_and_archive_unknown_separate() {
        let temp = tempfile::tempdir().unwrap();
        fs::create_dir(temp.path().join("probe")).unwrap();
        io::new_text(
            &temp.path().join("audio.wav"),
            "fixture identity, audio never decoded by this test",
        )
        .unwrap();
        let pcm = input::identity(&temp.path().join("audio.wav")).unwrap();
        let payload = fixture_payload();
        let mut full = payload.clone();
        full.extend(protocol::crc16_x25(&payload).to_le_bytes());
        let full_hex = hex::encode(full);
        let result = json!({"status":"complete","observation_id":1,"input":pcm,"source":pcm,
            "bit_repair_enabled":false,"reference_bytes_used_for_search":false,
            "variant_full_frames":{"legacy-fir512/fixed-full160":[full_hex],"boxcar32-rms50/fixed-full160":[],"legacy-fir512/gardner16":[],"boxcar32-rms50/gardner16":[]},
            "union_full_frames":[full_hex]});
        input::write_json_new(&temp.path().join("probe/result.json"), &result).unwrap();
        let mut kiss = formats::encode_kiss_record(0, 0, &payload).unwrap();
        kiss.extend(formats::encode_kiss_record(0, 0, &[0, 0, 0xca, 0]).unwrap());
        fs::write(temp.path().join("baseline.kiss"), kiss).unwrap();
        let mut state = json!({"observation_id":1,"references":{"complete":false,"objects":[]}});
        score(&mut state, temp.path(), &pcm).unwrap();
        assert_eq!(state["baseline_payloads"], json!([hex::encode(&payload)]));
        assert_eq!(state["baseline_other_payloads"], json!(["0000ca00"]));
        assert!(state["comparison"]["added_vs_archive"].is_null());
        assert_eq!(state["comparison"]["added_vs_baseline"], json!([]));
        state["references"]["complete"] = true.into();
        score(&mut state, temp.path(), &pcm).unwrap();
        assert_eq!(
            state["comparison"]["added_vs_archive"],
            json!([hex::encode(&payload)])
        );
    }
}
