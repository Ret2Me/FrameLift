//! Outcome-independent replay of the complete, previously exposed CANVAS pilot.
//! Freeze before decoding; keep failed windows and failed arms in the denominator.
use clap::Parser;
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::{
    collections::{BTreeMap, BTreeSet},
    fs,
    path::PathBuf,
    time::Instant,
};
use telemetry_yield_rs::{
    generic, input, protocol, receiver, recovery_guard::Guard, recovery_hdlc,
};

#[derive(Parser)]
struct Args {
    #[arg(long)]
    output: PathBuf,
    #[arg(
        long,
        default_value = "/home/ubuntu/framelift-field-20260916.QAl5x5/iq-run-v2/freeze.json"
    )]
    source_freeze: PathBuf,
    /// Predeclare the additional engineering-profile CPM arm before decoding.
    #[arg(long)]
    include_cpm: bool,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
struct ArmPlan {
    name: String,
    receiver: recovery_hdlc::Config,
    memory: Option<recovery_hdlc::memory::Config>,
}

fn hash(value: &impl Serialize) -> Result<String, String> {
    Ok(hex::encode(Sha256::digest(
        serde_json::to_vec(value).map_err(|e| e.to_string())?,
    )))
}

fn cpu() -> Result<f64, String> {
    let mut usage: libc::rusage = unsafe { std::mem::zeroed() };
    if unsafe { libc::getrusage(libc::RUSAGE_SELF, &mut usage) } != 0 {
        return Err(std::io::Error::last_os_error().to_string());
    }
    Ok(usage.ru_utime.tv_sec as f64
        + usage.ru_stime.tv_sec as f64
        + (usage.ru_utime.tv_usec + usage.ru_stime.tv_usec) as f64 / 1e6)
}

fn plans(mut receiver: recovery_hdlc::Config, cpm: bool) -> Vec<ArmPlan> {
    receiver.sequence = Some(recovery_hdlc::SequenceConfig {
        maximum_training_frames: 8,
    });
    let memory = recovery_hdlc::memory::Config {
        maximum_age_samples: 60 * 57600,
        maximum_models: 8,
        blind_bootstrap: false,
        work_budget: 100_000_000_000,
    };
    let mut result = vec![
        ArmPlan {
            name: "local_sequence".into(),
            receiver: receiver.clone(),
            memory: None,
        },
        ArmPlan {
            name: "causal_memory".into(),
            receiver: receiver.clone(),
            memory: Some(memory.clone()),
        },
        ArmPlan {
            name: "causal_memory_blind".into(),
            receiver: receiver.clone(),
            memory: Some(recovery_hdlc::memory::Config {
                blind_bootstrap: true,
                ..memory
            }),
        },
    ];
    if cpm {
        receiver.work_budget = 20_000_000_000;
        receiver.coherent_cpm = Some(recovery_hdlc::CoherentCpmConfig {
            modulation_index_numerator: 1,
            modulation_index_denominator: 2,
            gaussian_bt: Some(0.5),
            phase_offsets_samples: (0..6).collect(),
            carrier_centers_hz: vec![0.0],
            max_residual_carrier_hz: 300.0,
            preamble_flags: 8,
            maximum_candidate_symbols: 2048,
            maximum_candidates: 64,
            minimum_training_coherence: 0.65,
        });
        result.push(ArmPlan {
            name: "local_sequence_cpm".into(),
            receiver,
            memory: None,
        });
    }
    result
}

fn checked_frame(frame: &recovery_hdlc::Frame) -> Result<(String, Value), String> {
    let raw = hex::decode(&frame.hex).map_err(|e| e.to_string())?;
    if raw.len() < 2
        || !protocol::valid_ax25_fcs(&raw)
        || !protocol::valid_ax25_ui(&raw[..raw.len() - 2])
    {
        return Err(
            "arm emitted a frame without independent received FCS and AX.25 UI structure".into(),
        );
    }
    let payload = hex::encode(&raw[..raw.len() - 2]);
    Ok((
        payload.clone(),
        json!({"payload_hex":payload,
        "validation":{"type":"received_ax25_fcs","frame_hex":frame.hex}}),
    ))
}

fn prior_signal(prior: &Value, id: u64, input_hash: &str) -> Result<bool, String> {
    let expected_id = id.to_string();
    let observation = prior["observations"]
        .as_array()
        .ok_or("prior observations missing")?
        .iter()
        .find(|o| o["id"].as_str() == Some(expected_id.as_str()))
        .ok_or("source absent from prior study")?;
    if observation["input_sha256"] != input_hash {
        return Err("prior signal evidence belongs to different source bytes".into());
    }
    for arm in observation["arms"].as_array().ok_or("prior arms missing")? {
        if arm["status"] != "completed" || arm["input_sha256"] != input_hash {
            continue;
        }
        for frame in arm["frames"].as_array().ok_or("prior frames missing")? {
            if frame["validation"]["type"] != "received_ax25_fcs" {
                continue;
            }
            let raw = frame["validation"]["frame_hex"]
                .as_str()
                .ok_or("prior FCS frame missing")?;
            let (payload, _) = checked_frame(&recovery_hdlc::Frame {
                hex: raw.into(),
                validation_layers: vec![],
                provenance: vec![],
            })?;
            if frame["payload_hex"] != payload {
                return Err("prior signal evidence has inconsistent payload normalization".into());
            }
            return Ok(true);
        }
    }
    Ok(false)
}

fn comparison(observation: &Value) -> Value {
    let arms = observation["arms"].as_array().expect("constructed arms");
    let frames = |arm: &Value| -> BTreeSet<String> {
        arm["frames"]
            .as_array()
            .expect("constructed frame list")
            .iter()
            .map(|f| {
                f["payload_hex"]
                    .as_str()
                    .expect("constructed payload")
                    .to_string()
            })
            .collect()
    };
    let baseline = frames(&arms[0]);
    let mut union = BTreeSet::new();
    let counts: Vec<_> = arms.iter().map(|arm| {
        let complete = arm["status"] == "completed";
        let decoded = frames(arm);
        if complete { union.extend(decoded.iter().cloned()); }
        json!({"name":arm["name"],"status":arm["status"],"unique_frames":decoded.len(),
            "added_vs_local":if complete && arms[0]["status"] == "completed" {json!(decoded.difference(&baseline).count())}else{Value::Null},
            "missed_vs_local":if complete && arms[0]["status"] == "completed" {json!(baseline.difference(&decoded).count())}else{Value::Null},
            "wall_seconds":arm["wall_seconds"],"cpu_seconds":arm["cpu_seconds"]})
    }).collect();
    json!({"id":observation["id"],"arms":counts,"completed_arm_union_frames":union.len(),
        "all_arms_complete":arms.iter().all(|a| a["status"] == "completed")})
}

fn main() {
    if let Err(error) = run(Args::parse()) {
        eprintln!("{error}");
        std::process::exit(1);
    }
}

fn run(args: Args) -> Result<(), String> {
    let root = input::existing_new_dir(&args.output)?;
    let result = run_inner(&args, &root);
    if let Err(error) = &result {
        input::write_json_new(
            &root.join("failure.json"),
            &json!({
                "status":"failed", "error":error, "partial_results_are_diagnostic_only":true
            }),
        )?;
    }
    result
}

fn run_inner(args: &Args, root: &std::path::Path) -> Result<(), String> {
    let source_freeze_guard = Guard::open(&args.source_freeze)?;
    let prior = input::read_json(&args.source_freeze)?;
    source_freeze_guard.verify()?;
    if prior["schema"] != "framelift-field-hdlc-development-v1"
        || prior["sample_rate_hz"] != 57600
        || prior["window_samples"] != 921600
        || prior["hop_samples"] != 864000
        || prior["exposure"] != "development"
    {
        return Err("source freeze is not the specified complete CANVAS development pilot".into());
    }
    let prior_study_path = args
        .source_freeze
        .parent()
        .ok_or("source freeze parent missing")?
        .join("study.json");
    let prior_study_guard = Guard::open(&prior_study_path)?;
    let prior_study = input::read_json(&prior_study_path)?;
    prior_study_guard.verify()?;
    if prior_study["schema"] != "framelift-recovery-study-v1" {
        return Err("unexpected prior signal-evidence schema".into());
    }
    let runtime = Guard::open(&std::env::current_exe().map_err(|e| e.to_string())?)?;
    let profile_guard = Guard::open(std::path::Path::new(
        prior["profile"]["path"]
            .as_str()
            .ok_or("missing profile path")?,
    ))?;
    if prior["profile"]["sha256"] != profile_guard.identity().sha256 {
        return Err("source mission profile differs from frozen pilot".into());
    }
    let native: recovery_hdlc::Config =
        serde_json::from_value(prior["receiver"].clone()).map_err(|e| e.to_string())?;
    let arms = plans(native, args.include_cpm);
    let mut sources = Vec::new();
    let rows = prior["sources"].as_array().ok_or("missing source cohort")?;
    if rows.len() != 2 {
        return Err("cohort must contain both complete original recordings".into());
    }
    for (row, expected) in rows.iter().zip([14115025u64, 14366383]) {
        if row["id"] != expected {
            return Err("source order/identity differs from frozen complete cohort".into());
        }
        let path = PathBuf::from(row["input"]["path"].as_str().ok_or("source path missing")?);
        let guard = Guard::open(&path)?;
        if row["input"]["sha256"] != guard.identity().sha256
            || row["input"]["bytes"] != guard.identity().bytes
            || !guard.identity().bytes.is_multiple_of(4)
        {
            return Err("source bytes differ from frozen IQ cohort".into());
        }
        let count = usize::try_from(guard.identity().bytes / 4).map_err(|e| e.to_string())?;
        let windows = receiver::window_bounds(count, 57600, 16.0, 15.0)?;
        let confirmed = prior_signal(&prior_study, expected, &guard.identity().sha256)?;
        sources.push((expected, guard, windows, confirmed));
    }
    let verify = || -> Result<(), String> {
        runtime.verify()?;
        source_freeze_guard.verify()?;
        prior_study_guard.verify()?;
        profile_guard.verify()
    };
    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map_err(|e| e.to_string())?;
    input::write_json_new(
        &root.join("freeze.json"),
        &json!({
            "schema":"framelift-hdlc-memory-development-v1", "frozen_unix_seconds":now.as_secs(),
        "source_freeze":source_freeze_guard.identity(), "runtime":runtime.identity(),
        "prior_signal_evidence":prior_study_guard.identity(),
            "mission_profile":profile_guard.identity(), "arms":arms,
            "sample_rate_hz":57600, "format":"ci16_le", "exposure":"development",
        "sources":sources.iter().map(|(id, guard, windows, confirmed)| json!({"id":id,
            "input":guard.identity(),"windows":windows,"confirmed_signal":confirmed})).collect::<Vec<_>>(),
            "selection":"Both complete, previously exposed CANVAS files; exact original 16-second/15-second window schedule; no outcome-based crop or exclusion",
            "timing":"Arm end-to-end wall and self-process CPU include IQ reads, receipt serialization and source checks. Same shared VM, fixed declared arm order; not isolated or matched-cost.",
            "cpm_profile_evidence":"9600 GMSK is the mission profile; h=1/2 and BT=0.5 are explicit engineering assumptions, not independently certified transmitter values.",
            "statistical_unit":"observation, not overlapping window; both are one station",
            "publication_ready":false, "runtime_closure_sealed":false
        }),
    )?;
    // No decoder may run until all source identities, windows and arms are frozen.
    let mut observations = Vec::new();
    for (id, guard, windows, confirmed) in sources {
        let observation_dir = root.join(format!("obs-{id}"));
        fs::create_dir(&observation_dir).map_err(|e| e.to_string())?;
        let mut receipts = Vec::new();
        for arm in &arms {
            verify()?;
            guard.verify()?;
            let lane = observation_dir.join(&arm.name);
            fs::create_dir(&lane).map_err(|e| e.to_string())?;
            let begin = Instant::now();
            let cpu_begin = cpu()?;
            let mut memory = arm
                .memory
                .as_ref()
                .map(|options| {
                    recovery_hdlc::memory::Session::new(
                        &guard.identity().sha256,
                        57600,
                        arm.receiver.clone(),
                        options.clone(),
                    )
                })
                .transpose()?;
            let mut frames = BTreeMap::new();
            let mut errors = Vec::new();
            for (index, &(start, end)) in windows.iter().enumerate() {
                let decoded = (|| -> Result<Value, String> {
                    let iq =
                        guard.read_iq_window(&generic::InputFormat::Ci16Le, start, end - start)?;
                    let (record, recovered) = if let Some(memory) = &mut memory {
                        let report =
                            memory.decode_window(&guard.identity().sha256, start as u64, &iq)?;
                        (
                            serde_json::to_value(&report).map_err(|e| e.to_string())?,
                            report.frames,
                        )
                    } else {
                        let report = recovery_hdlc::decode(
                            &iq,
                            57600,
                            &arm.receiver,
                            &guard.identity().sha256,
                        )?;
                        (
                            serde_json::to_value(&report).map_err(|e| e.to_string())?,
                            report.frames,
                        )
                    };
                    let accepted = recovered
                        .iter()
                        .map(checked_frame)
                        .collect::<Result<Vec<_>, _>>()?;
                    frames.extend(accepted);
                    Ok(record)
                })();
                guard.verify()?;
                verify()?;
                let row = match decoded {
                    Ok(report) => {
                        json!({"status":"completed","start_sample":start,"end_sample":end,"report":report})
                    }
                    Err(error) => {
                        errors.push(json!({"index":index,"start_sample":start,"end_sample":end,"error":error}));
                        json!({"status":"failed","start_sample":start,"end_sample":end,"error":error})
                    }
                };
                input::write_json_new(&lane.join(format!("window-{index:03}.json")), &row)?;
            }
            let wall_seconds = begin.elapsed().as_secs_f64();
            let cpu_seconds = cpu()? - cpu_begin;
            let complete = errors.is_empty();
            input::write_json_new(
                &lane.join("windows-summary.json"),
                &json!({
                    "selected_windows":windows.len(), "failed_windows":errors.len(), "errors":errors,
                    "partial_unique_frames":frames.len(), "partial_frames_are_not_scored":!complete
                }),
            )?;
            let receipt = json!({
                "name":arm.name,"status":if complete {"completed"}else{"failed"},
                "input_sha256":guard.identity().sha256,"runtime_sha256":runtime.identity().sha256,
                "profile_sha256":hash(arm)?,"wall_seconds":wall_seconds,"cpu_seconds":cpu_seconds,
                "error":if complete {Value::Null}else{json!(serde_json::to_string(&errors).map_err(|e|e.to_string())?)},
                "frames":if complete {frames.into_values().collect::<Vec<_>>()}else{vec![]}
            });
            input::write_json_new(&lane.join("arm.json"), &receipt)?;
            receipts.push(receipt);
        }
        observations.push(json!({"id":id.to_string(),"mission":"CANVAS","station":106,
            "pass_group":format!("CANVAS-{id}"), "input_sha256":guard.identity().sha256,
            "exposure":"development","confirmed_signal":if confirmed {json!(true)}else{Value::Null},
            "signal_evidence":if confirmed {json!(format!("Received FCS and AX.25 UI replay of prior development study {} (sha256 {}) for the identical input bytes; predates current decoding",prior_study_path.display(),prior_study_guard.identity().sha256))}else{Value::Null},
            "negative_control":false,"duration_seconds":guard.identity().bytes as f64 / 4.0 / 57600.0,
            "arms":receipts}));
    }
    verify()?;
    let study = json!({"schema":"framelift-recovery-study-v1", "baseline_arm":"local_sequence",
        "candidate_arms":arms.iter().skip(1).map(|a| &a.name).collect::<Vec<_>>(), "observations":observations});
    input::write_json_new(&root.join("study.json"), &study)?;
    input::write_json_new(
        &root.join("comparison.json"),
        &json!({
            "schema":"framelift-hdlc-memory-development-comparison-v1",
            "observations":study["observations"].as_array().unwrap().iter().map(comparison).collect::<Vec<_>>(),
            "unit":"unique payload per observation; overlapping windows are not independent",
            "publication_ready":false,"development_only":true
        }),
    )?;
    let failures = study["observations"]
        .as_array()
        .unwrap()
        .iter()
        .flat_map(|o| o["arms"].as_array().unwrap())
        .filter(|a| a["status"] != "completed")
        .count();
    input::write_json_new(
        &root.join("completion.json"),
        &json!({
            "status":if failures == 0 {"complete"}else{"complete_with_failed_arms"},
            "selected_observations":2,"failed_arms":failures,"publication_ready":false
        }),
    )?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn receipt_rejects_invalid_crc_and_structure() {
        let frame = recovery_hdlc::Frame {
            hex: "0000".into(),
            validation_layers: vec!["claimed_crc".into()],
            provenance: vec![],
        };
        assert!(checked_frame(&frame).is_err());
    }

    #[test]
    fn prior_signal_needs_matching_source_and_independent_frame_validation() {
        let hash = "a".repeat(64);
        let mut evidence = json!({"observations":[{"id":"7","input_sha256":hash,
            "arms":[{"status":"completed","input_sha256":hash,"frames":[]}]}]});
        assert!(!prior_signal(&evidence, 7, &hash).unwrap());
        assert!(prior_signal(&evidence, 7, &"b".repeat(64)).is_err());
        evidence["observations"][0]["arms"][0]["frames"] = json!([{
            "payload_hex":"00", "validation":{"type":"received_ax25_fcs","frame_hex":"000000"}
        }]);
        assert!(prior_signal(&evidence, 7, &hash).is_err());
        evidence["observations"][0]["arms"][0]["status"] = json!("failed");
        assert!(!prior_signal(&evidence, 7, &hash).unwrap());
    }

    #[test]
    fn failed_arm_is_not_a_scored_zero_yield_comparison() {
        let result = comparison(&json!({"id":"7","arms":[
            {"name":"local","status":"completed","frames":[{"payload_hex":"1234"}],"wall_seconds":1,"cpu_seconds":1},
            {"name":"failed","status":"failed","frames":[],"wall_seconds":1,"cpu_seconds":1}
        ]}));
        assert_eq!(result["completed_arm_union_frames"], 1);
        assert_eq!(result["all_arms_complete"], false);
        assert!(result["arms"][1]["added_vs_local"].is_null());
        assert!(result["arms"][1]["missed_vs_local"].is_null());
    }
}
