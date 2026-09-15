//! Full frozen receiver on synthetic noise. This is not a real-station FPR study.
#[allow(dead_code)] // The shared acquisition helper also serves the network runner.
#[path = "support/holdout_run_io.rs"]
mod io;
use clap::Parser;
use serde_json::{Value, json};
use std::{
    collections::{BTreeMap, BTreeSet},
    fs::{self, OpenOptions},
    os::fd::AsRawFd,
    path::{Path, PathBuf},
    sync::{
        Mutex,
        atomic::{AtomicBool, AtomicUsize, Ordering},
    },
};
use telemetry_yield_rs::{input, protocol};

const PROBE_SHA: &str = "455a9622ba7c839fbba1c575b8139502a9b24c2b3f99421adb2e9ab74bb37ab3";
const DEFAULT_PROBE: &str = "/home/ubuntu/telemetry-yield/work/receiver-improvements-20260908/tracking-v1-release/tracking_audio_probe";
const VARIANTS: [&str; 4] = [
    "legacy-fir512/fixed-full160",
    "boxcar32-rms50/fixed-full160",
    "legacy-fir512/gardner16",
    "boxcar32-rms50/gardner16",
];
type Set = BTreeSet<String>;

#[derive(Parser)]
struct Args {
    #[arg(long)]
    manifest: PathBuf,
    #[arg(long)]
    output: PathBuf,
    #[arg(long, default_value = DEFAULT_PROBE)]
    probe: PathBuf,
    #[arg(long, default_value_t = 2)]
    workers: usize,
    #[arg(long, default_value_t = 2)]
    threads: usize,
    #[arg(long)]
    resume: bool,
}

fn require(ok: bool, why: &str) -> Result<(), String> {
    if ok { Ok(()) } else { Err(why.into()) }
}

fn verified_identity(value: &Value) -> Result<input::Identity, String> {
    let expected: input::Identity =
        serde_json::from_value(value.clone()).map_err(|e| e.to_string())?;
    let actual = input::identity(Path::new(&expected.path))?;
    require(
        actual.sha256 == expected.sha256
            && actual.bytes == expected.bytes
            && actual.path == expected.path,
        "control artifact identity changed",
    )?;
    Ok(actual)
}

fn manifest_contract(document: &Value) -> Result<Vec<Value>, String> {
    require(
        document["schema"] == "holdout-synthetic-controls-v1",
        "unsupported controls manifest",
    )?;
    require(
        document["total_seconds"] == 3840
            && document["no_known_frames_inserted"] == true
            && document["real_station_false_alarm_population"] == false
            && document["decoder_executed"] == false,
        "control exposure or ground-truth contract mismatch",
    )?;
    let rows = document["controls"]
        .as_array()
        .ok_or("controls array missing")?;
    require(rows.len() == 64, "frozen protocol requires all 64 controls")?;
    let mut seen = BTreeSet::new();
    let mut ordered = rows.clone();
    for row in &ordered {
        let index = row["index"]
            .as_u64()
            .filter(|index| *index < 64)
            .ok_or("control index must be0..63")?;
        require(seen.insert(index), "duplicate control index")?;
        require(
            row["kind"] == if index < 32 { "white" } else { "ar1_0.85" },
            "control kind/index mismatch",
        )?;
        require(
            row["seed"] == 20260908 + index * 104729 && row["seconds"] == 60,
            "frozen control seed or duration mismatch",
        )?;
        let _: input::Identity =
            serde_json::from_value(row["input"].clone()).map_err(|e| e.to_string())?;
    }
    ordered.sort_by_key(|row| row["index"].as_u64());
    Ok(ordered)
}

fn validate_input(row: &Value) -> Result<input::Identity, String> {
    let identity = verified_identity(&row["input"])?;
    let mut wav = hound::WavReader::open(&identity.path).map_err(|e| e.to_string())?;
    let spec = wav.spec();
    require(
        spec.channels == 1
            && spec.sample_rate == 48000
            && spec.bits_per_sample == 32
            && spec.sample_format == hound::SampleFormat::Float
            && wav.duration() == 48000 * 60,
        "control must be complete mono48kHz float32 60-second WAV",
    )?;
    for sample in wav.samples::<f32>() {
        require(
            sample.map_err(|e| e.to_string())?.is_finite(),
            "control has nonfinite sample",
        )?;
    }
    verified_identity(&row["input"])
}

fn pinned_probe(path: &Path) -> Result<input::Identity, String> {
    let identity = input::identity(path)?;
    require(
        identity.sha256 == PROBE_SHA,
        "only frozen tracking-v1 probe is allowed",
    )?;
    Ok(identity)
}

fn fcs_valid(frame: &[u8]) -> bool {
    if frame.len() < 2 {
        return false;
    }
    let mut crc = 0xffffu16;
    for byte in &frame[..frame.len() - 2] {
        crc ^= u16::from(*byte);
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

fn full_frame_set(value: &Value) -> Result<Set, String> {
    let mut result = Set::new();
    for raw in value.as_array().ok_or("full frame set must be array")? {
        let raw = raw.as_str().ok_or("full frame hex must be string")?;
        let frame = hex::decode(raw).map_err(|e| e.to_string())?;
        require(
            hex::encode(&frame) == raw,
            "noncanonical received frame hex",
        )?;
        require(
            fcs_valid(&frame),
            "noise output frame failed independent bitwise FCS",
        )?;
        require(
            protocol::parse_ax25_ui(&frame[..frame.len() - 2]).is_some(),
            "noise output frame failed strict AX25 UI",
        )?;
        require(result.insert(raw.into()), "duplicate in declared frame set")?;
    }
    Ok(result)
}

fn audit_probe(result: &Value, index: u64, pcm: &input::Identity) -> Result<Value, String> {
    require(
        result["status"] == "complete" && result["observation_id"] == index,
        "probe completion or control index mismatch",
    )?;
    require(
        result["input"]["sha256"] == pcm.sha256
            && result["source"]["sha256"] == pcm.sha256
            && result["input"]["bytes"] == pcm.bytes
            && result["source"]["bytes"] == pcm.bytes,
        "probe control PCM identity mismatch",
    )?;
    require(
        result["reference_bytes_used_for_search"] == false && result["bit_repair_enabled"] == false,
        "unexpected reference use or bit repair",
    )?;
    require(
        result["window_count"] == 19,
        "60-second control must execute all19 full6-second windows at3-second hop",
    )?;
    let raw_variants = result["variant_full_frames"]
        .as_object()
        .ok_or("probe variants missing")?;
    require(
        raw_variants
            .keys()
            .map(String::as_str)
            .collect::<BTreeSet<_>>()
            == VARIANTS.into_iter().collect(),
        "four-variant contract mismatch",
    )?;
    let mut variants = BTreeMap::new();
    for (name, frames) in raw_variants {
        let set = full_frame_set(frames)?;
        require(
            result["variant_counts"][name] == set.len(),
            "variant count mismatch",
        )?;
        variants.insert(name.clone(), set);
    }
    let union = full_frame_set(&result["union_full_frames"])?;
    require(
        union == variants.values().flatten().cloned().collect(),
        "native union differs from full variant union",
    )?;
    let windows = result["window_trials"]
        .as_array()
        .ok_or("native window provenance missing")?;
    require(
        windows.len() == 76,
        "all four variants must execute every control window",
    )?;
    let mut provenance: BTreeMap<String, Set> = BTreeMap::new();
    let mut slots = BTreeSet::new();
    let mut received_frame_instances = 0usize;
    for window in windows {
        let name = format!(
            "{}/{}",
            window["frontend"].as_str().ok_or("frontend missing")?,
            window["clock"].as_str().ok_or("clock missing")?
        );
        require(
            VARIANTS.contains(&name.as_str()),
            "unknown provenance variant",
        )?;
        let seconds = window["window_start_seconds"]
            .as_f64()
            .ok_or("window start missing")?;
        require(
            seconds.is_finite() && (0.0..=54.0).contains(&seconds) && seconds % 3.0 == 0.0,
            "window outside frozen full-control schedule",
        )?;
        require(
            slots.insert((name.clone(), seconds as u64)),
            "duplicate window/variant",
        )?;
        let frames = full_frame_set(&window["frame_with_fcs_hex"])?;
        received_frame_instances += frames.len();
        provenance.entry(name).or_default().extend(frames);
    }
    require(
        provenance == variants,
        "received frame sets do not match retained window provenance",
    )?;
    let payloads: Set = union
        .iter()
        .map(|full| full[..full.len() - 4].to_owned())
        .collect();
    Ok(
        json!({"independent_bitwise_crc":true,"strict_ax25_ui":true,"all_full_windows_executed":true,
        "original_received_frames_retained":true,"frame_provenance_consistent":true,
        "variant_counts":result["variant_counts"],"union_full_frames":union,"false_accepted_payloads":payloads,
        "false_accepted_pdu_count":payloads.len(),"received_frame_instances":received_frame_instances,
        "classification":"Any accepted native PDU is false positive on this declared no-transmission synthetic control."}),
    )
}

fn artifacts(dir: &Path, found: &mut Vec<input::Identity>) -> Result<(), String> {
    let mut entries = fs::read_dir(dir)
        .map_err(|e| e.to_string())?
        .collect::<Result<Vec<_>, _>>()
        .map_err(|e| e.to_string())?;
    entries.sort_by_key(|entry| entry.file_name());
    for entry in entries {
        let kind = entry.file_type().map_err(|e| e.to_string())?;
        require(!kind.is_symlink(), "symlink in owned control attempt")?;
        if kind.is_dir() {
            artifacts(&entry.path(), found)?;
        } else if kind.is_file() && entry.file_name() != "commit.json" {
            found.push(input::identity(&entry.path())?);
        }
    }
    Ok(())
}

fn compact(state: &Value, result: input::Identity, commit: input::Identity) -> Value {
    json!({"index":state["control"]["index"],"kind":state["control"]["kind"],"seconds":state["control"]["seconds"],
        "status":state["status"],"false_accepted_payloads":state["audit"]["false_accepted_payloads"],
        "false_accepted_pdu_count":state["audit"]["false_accepted_pdu_count"],"reported_union_count":state["reported_union_count"],
        "result_identity":result,"commit_identity":commit})
}

fn finish(dir: &Path, mut state: Value) -> Result<Value, String> {
    state["finished_utc"] = io::utc().into();
    input::write_json_new(&dir.join("result.json"), &state)?;
    let result = input::identity(&dir.join("result.json"))?;
    let mut files = vec![];
    artifacts(dir, &mut files)?;
    input::write_json_new(
        &dir.join("commit.json"),
        &json!({"schema":"holdout-synthetic-control-commit-v1","result_identity":result,"input_identity":state["control"]["input"],"artifacts":files}),
    )?;
    Ok(compact(
        &state,
        result,
        input::identity(&dir.join("commit.json"))?,
    ))
}

fn one(args: &Args, row: &Value, plan: &input::Identity) -> Result<Value, String> {
    let index = row["index"].as_u64().ok_or("control index missing")?;
    let parent = args.output.join("controls").join(format!("{index:03}"));
    fs::create_dir_all(&parent).map_err(|e| e.to_string())?;
    let mut number = 1;
    while parent.join(format!("attempt-{number:03}")).exists() {
        number += 1;
    }
    require(number <= 3, "three-attempt cap for interrupted control")?;
    let dir = parent.join(format!("attempt-{number:03}"));
    fs::create_dir(&dir).map_err(|e| e.to_string())?;
    let mut state = json!({"schema":"holdout-synthetic-control-result-v1","status":"failed","control":row,"plan_identity":plan,"started_utc":io::utc(),
        "audit":null,"reported_union_count":null,"real_station_false_positive_rate_claimed":false});
    input::write_json_new(&dir.join("start.json"), &state)?;
    let outcome = (|| {
        io::disk_guard(&args.output)?;
        let pcm = validate_input(row)?;
        let probe = pinned_probe(&args.probe)?;
        state["probe_identity"] = json!(probe);
        let process = io::execute(
            &args.probe,
            &[
                "--input".into(),
                pcm.path.clone(),
                "--output".into(),
                dir.join("probe").display().to_string(),
                "--observation-id".into(),
                index.to_string(),
                "--threads".into(),
                args.threads.to_string(),
            ],
            &dir,
            "probe",
            1200,
            64 * 1024 * 1024,
        )?;
        state["process"] = process.clone();
        io::require_success(&process)?;
        let result = input::read_json(&dir.join("probe/result.json"))?;
        state["reported_union_count"] = json!(result["union_full_frames"].as_array().map(Vec::len));
        state["probe_result_identity"] = json!(input::identity(&dir.join("probe/result.json"))?);
        state["audit"] = audit_probe(&result, index, &pcm)?;
        verified_identity(&row["input"])?;
        pinned_probe(&args.probe)?;
        state["status"] = "complete".into();
        Ok::<(), String>(())
    })();
    if let Err(error) = outcome {
        state["error"] = error.into();
    }
    finish(&dir, state)
}

fn previous(output: &Path, row: &Value) -> Result<Option<Value>, String> {
    let parent = output.join("controls").join(format!(
        "{:03}",
        row["index"].as_u64().ok_or("control ID missing")?
    ));
    if !parent.exists() {
        return Ok(None);
    }
    let mut entries = fs::read_dir(parent)
        .map_err(|e| e.to_string())?
        .collect::<Result<Vec<_>, _>>()
        .map_err(|e| e.to_string())?;
    entries.sort_by_key(|entry| entry.file_name());
    for entry in entries.iter().rev() {
        let path = entry.path().join("commit.json");
        if !path.is_file() {
            continue;
        }
        let commit = input::read_json(&path)?;
        let result_id = verified_identity(&commit["result_identity"])?;
        verified_identity(&commit["input_identity"])?;
        for artifact in commit["artifacts"]
            .as_array()
            .ok_or("committed artifact list missing")?
        {
            verified_identity(artifact)?;
        }
        let state = input::read_json(Path::new(&result_id.path))?;
        require(
            state["control"] == *row,
            "committed control does not match manifest",
        )?;
        return Ok(Some(compact(&state, result_id, input::identity(&path)?)));
    }
    Ok(None)
}

fn summary(rows: &[Value], running: bool) -> Value {
    let completed: Vec<_> = rows
        .iter()
        .filter(|row| row["status"] == "complete")
        .collect();
    let false_count: u64 = completed
        .iter()
        .filter_map(|row| row["false_accepted_pdu_count"].as_u64())
        .sum();
    let global: Set = completed
        .iter()
        .filter_map(|row| row["false_accepted_payloads"].as_array())
        .flatten()
        .filter_map(|pdu| pdu.as_str().map(str::to_owned))
        .collect();
    let nonzero = completed
        .iter()
        .filter(|row| {
            row["false_accepted_pdu_count"]
                .as_u64()
                .is_some_and(|count| count > 0)
        })
        .count();
    let unaudited_reported: u64 = rows
        .iter()
        .filter(|row| row["status"] != "complete")
        .filter_map(|row| row["reported_union_count"].as_u64())
        .sum();
    let seconds = completed.len() * 60;
    let mut by_kind = BTreeMap::new();
    for kind in ["white", "ar1_0.85"] {
        let selected: Vec<_> = completed.iter().filter(|row| row["kind"] == kind).collect();
        by_kind.insert(kind,json!({"completed":selected.len(),"exposure_seconds":selected.len()*60,"false_accepted_pdus_per_control_sum":selected.iter().filter_map(|row|row["false_accepted_pdu_count"].as_u64()).sum::<u64>()}));
    }
    let full = completed.len() == 64;
    json!({"schema":"holdout-synthetic-control-summary-v1","updated_utc":io::utc(),"running":running,"selected":64,"completed":completed.len(),"failed":rows.len()-completed.len(),"pending":64-rows.len(),
        "completed_zero_controls":completed.len()-nonzero,"completed_nonzero_controls":nonzero,
        "planned_exposure_seconds":3840,"completed_exposure_seconds":seconds,"false_accepted_pdus_per_control_sum":false_count,
        "global_unique_false_accepted_pdus":global.len(),"global_false_accepted_payloads":global,"by_kind":by_kind,"controls":rows,
        "unaudited_reported_union_frames_in_failed_controls":unaudited_reported,
        "all_controls_completed_without_false_acceptance":full && false_count==0,
        "conditional_zero_count_poisson_95_upper_per_hour":(full && false_count==0).then(|| -0.05f64.ln()/(seconds as f64/3600.0)),
        "poisson_bound_interpretation":"Descriptive model-dependent bound conditional on these synthetic white/AR(1) controls, assuming a stationary Poisson process. Overlapping hypothesis searches do not establish that model; not a real-station population FPR confidence bound.",
        "frame_found_is_not_a_stopping_rule":true,"real_station_false_positive_rate_claimed":false,
        "failures_are_not_zero_controls":true,"publication_ready":false})
}

fn run(args: &mut Args) -> Result<Value, String> {
    require(
        args.workers == 2 && args.threads == 2,
        "frozen controls require workers2 and threads2",
    )?;
    args.manifest = args.manifest.canonicalize().map_err(|e| e.to_string())?;
    args.probe = args.probe.canonicalize().map_err(|e| e.to_string())?;
    let document = input::read_json(&args.manifest)?;
    let controls = manifest_contract(&document)?;
    let manifest = input::identity(&args.manifest)?;
    let probe = pinned_probe(&args.probe)?;
    verified_identity(&document["generator"])?;
    for row in &controls {
        validate_input(row)?;
    }
    if args.resume {
        require(args.output.is_dir(), "resume output missing")?;
    } else {
        fs::create_dir(&args.output).map_err(|e| format!("new output directory required: {e}"))?;
    }
    args.output = args.output.canonicalize().map_err(|e| e.to_string())?;
    let lock = OpenOptions::new()
        .read(true)
        .write(true)
        .create(true)
        .truncate(false)
        .open(args.output.join("controls.lock"))
        .map_err(|e| e.to_string())?;
    require(
        unsafe { libc::flock(lock.as_raw_fd(), libc::LOCK_EX | libc::LOCK_NB) } == 0,
        "controls campaign already locked",
    )?;
    let plan = json!({"schema":"holdout-synthetic-control-run-plan-v1","manifest":manifest,"generator":document["generator"],"probe":probe,"runner":input::identity(&std::env::current_exe().map_err(|e|e.to_string())?)?,
        "process_usage_collector":input::identity(Path::new("/usr/bin/time"))?,"workers":2,"threads":2,"controls":64,"seconds_per_control":60,"stop_when_frame_found":false,"maximum_probe_seconds":1200,"network_requests":false,
        "receiver_configuration":"Frozen four-variant tracking-v1 portfolio, whole mono48kHz float32 audio, no bit repairs or reference-guided search"});
    if args.resume {
        require(
            input::read_json(&args.output.join("plan.json"))? == plan,
            "resume manifest/probe/runner plan mismatch",
        )?;
    } else {
        input::write_json_new(&args.output.join("plan.json"), &plan)?;
    }
    let plan_identity = input::identity(&args.output.join("plan.json"))?;
    let (mut records, mut todo) = (vec![], vec![]);
    for row in &controls {
        if let Some(record) = previous(&args.output, row)? {
            records.push(record);
        } else {
            todo.push(row.clone());
        }
    }
    records.sort_by_key(|row| row["index"].as_u64());
    let records = Mutex::new(records);
    let errors = Mutex::new(Vec::new());
    let cursor = AtomicUsize::new(0);
    let stop = AtomicBool::new(false);
    io::replace_json(
        &args.output.join("summary.json"),
        &summary(&records.lock().unwrap(), true),
    )?;
    std::thread::scope(|scope| {
        for _ in 0..2 {
            let args = &*args;
            let (todo, records, errors, cursor, stop, plan) =
                (&todo, &records, &errors, &cursor, &stop, &plan_identity);
            scope.spawn(move || {
                while !stop.load(Ordering::SeqCst) {
                    let index = cursor.fetch_add(1, Ordering::SeqCst);
                    let Some(row) = todo.get(index) else {
                        break;
                    };
                    match one(args, row, plan) {
                        Ok(record) => {
                            let mut rows = records.lock().unwrap();
                            rows.push(record);
                            rows.sort_by_key(|row| row["index"].as_u64());
                            if let Err(error) = io::replace_json(
                                &args.output.join("summary.json"),
                                &summary(&rows, true),
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
    for expected in [
        &plan["manifest"],
        &plan["probe"],
        &plan["generator"],
        &plan["process_usage_collector"],
    ] {
        if let Err(error) = verified_identity(expected) {
            errors.lock().unwrap().push(error);
        }
    }
    for control in &controls {
        if let Err(error) = verified_identity(&control["input"]) {
            errors.lock().unwrap().push(error);
        }
    }
    let mut report = summary(&records.into_inner().unwrap(), false);
    let errors = errors.into_inner().unwrap();
    report["runtime_integrity_at_finish"] = errors.is_empty().into();
    report["fatal_errors"] = json!(errors);
    if report["runtime_integrity_at_finish"] != true {
        report["all_controls_completed_without_false_acceptance"] = false.into();
        report["conditional_zero_count_poisson_95_upper_per_hour"] = Value::Null;
    }
    io::replace_json(&args.output.join("summary.json"), &report)?;
    drop(lock);
    Ok(report)
}

fn main() {
    match run(&mut Args::parse()) {
        Ok(report) => {
            println!(
                "{}",
                json!({"completed":report["completed"],"failed":report["failed"],"pending":report["pending"],"false_accepted_pdus_per_control_sum":report["false_accepted_pdus_per_control_sum"]})
            );
            if report["failed"] != 0
                || report["pending"] != 0
                || report["runtime_integrity_at_finish"] != true
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
    fn fixture_manifest() -> Value {
        json!({"schema":"holdout-synthetic-controls-v1","controls":(0..64u64).map(|index|json!({"index":index,"kind":if index<32 {"white"} else {"ar1_0.85"},"seconds":60,"seed":20260908+index*104729,"input":{"path":"/fixture","sha256":"fixture","bytes":1}})).collect::<Vec<_>>(),"total_seconds":3840,"no_known_frames_inserted":true,"real_station_false_alarm_population":false,"decoder_executed":false})
    }
    #[test]
    fn frozen_control_contract_rejects_selection_and_seed_changes() {
        let mut manifest = fixture_manifest();
        assert_eq!(manifest_contract(&manifest).unwrap().len(), 64);
        manifest["controls"][1]["index"] = 0.into();
        assert!(manifest_contract(&manifest).is_err());
        manifest = fixture_manifest();
        manifest["controls"][1]["seed"] = 0.into();
        assert!(manifest_contract(&manifest).is_err());
        manifest = fixture_manifest();
        manifest["controls"].as_array_mut().unwrap().pop();
        assert!(manifest_contract(&manifest).is_err());
    }
    #[test]
    fn missing_or_failed_control_is_not_zero_or_full_exposure() {
        let report = summary(
            &[
                json!({"index":0,"kind":"white","status":"complete","false_accepted_pdu_count":0,"false_accepted_payloads":[]}),
                json!({"index":1,"kind":"white","status":"failed"}),
            ],
            false,
        );
        assert_eq!(report["completed_zero_controls"], 1);
        assert_eq!(report["failed"], 1);
        assert_eq!(report["pending"], 62);
        assert_eq!(report["completed_exposure_seconds"], 60);
        assert!(report["conditional_zero_count_poisson_95_upper_per_hour"].is_null());
    }
    #[test]
    fn false_positive_is_counted_and_does_not_request_stop() {
        let report = summary(
            &[
                json!({"index":0,"kind":"white","status":"complete","false_accepted_pdu_count":1,"false_accepted_payloads":["abcd"]}),
            ],
            true,
        );
        assert_eq!(report["false_accepted_pdus_per_control_sum"], 1);
        assert_eq!(report["global_unique_false_accepted_pdus"], 1);
        assert_eq!(report["frame_found_is_not_a_stopping_rule"], true);
        assert_eq!(report["real_station_false_positive_rate_claimed"], false);
    }
    #[test]
    fn crc_known_vector_and_strict_structure() {
        let full = hex::decode("3132333435363738396e90").unwrap();
        assert!(fcs_valid(&full));
        assert!(!fcs_valid(&full[..full.len() - 1]));
        assert!(full_frame_set(&json!([hex::encode(full)])).is_err());
        assert!(full_frame_set(&json!([])).unwrap().is_empty());
    }
    #[test]
    fn nonfrozen_probe_is_rejected_without_execution() {
        let temp = tempfile::tempdir().unwrap();
        io::new_text(&temp.path().join("probe"), "not the frozen executable").unwrap();
        assert!(pinned_probe(&temp.path().join("probe")).is_err());
    }
    #[test]
    fn all_window_provenance_is_required_even_for_zero_frames() {
        let pcm = input::Identity {
            path: "/fixture.wav".into(),
            sha256: "fixture".into(),
            bytes: 123,
        };
        let mut variants = serde_json::Map::new();
        let mut counts = serde_json::Map::new();
        let mut trials = vec![];
        for variant in VARIANTS {
            variants.insert(variant.into(), json!([]));
            counts.insert(variant.into(), json!(0));
            let (frontend, clock) = variant.split_once('/').unwrap();
            for window in 0..19 {
                trials.push(json!({"frontend":frontend,"clock":clock,"window_start_seconds":window*3,"frame_with_fcs_hex":[]}));
            }
        }
        let mut result = json!({"status":"complete","observation_id":0,"input":pcm,"source":pcm,
            "reference_bytes_used_for_search":false,"bit_repair_enabled":false,"window_count":19,
            "variant_full_frames":variants,"variant_counts":counts,"union_full_frames":[],"window_trials":trials});
        let audit = audit_probe(&result, 0, &pcm).unwrap();
        assert_eq!(audit["false_accepted_pdu_count"], 0);
        result["window_trials"].as_array_mut().unwrap().pop();
        assert!(audit_probe(&result, 0, &pcm).is_err());
    }
    #[test]
    fn complete_zero_controls_have_only_conditional_descriptive_bound() {
        let rows: Vec<_> = (0..64).map(|index|json!({"index":index,"kind":if index<32 {"white"} else {"ar1_0.85"},"status":"complete","false_accepted_payloads":[],"false_accepted_pdu_count":0})).collect();
        let report = summary(&rows, false);
        assert_eq!(report["completed_exposure_seconds"], 3840);
        assert_eq!(
            report["all_controls_completed_without_false_acceptance"],
            true
        );
        let bound = report["conditional_zero_count_poisson_95_upper_per_hour"]
            .as_f64()
            .unwrap();
        assert!((bound - 2.808498).abs() < 0.00001);
        assert_eq!(report["real_station_false_positive_rate_claimed"], false);
    }
}
