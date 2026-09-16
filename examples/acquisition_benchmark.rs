//! Replay the frozen CPM stress grid. No transmitter changes or parameter search.
use clap::Parser;
use serde_json::{Value, json};
use std::{collections::BTreeSet, path::PathBuf, time::Instant};
use telemetry_yield_rs::{
    acquisition,
    advanced_iq::{self, FilePlan},
    input,
    recovery_guard::Guard,
};

#[derive(Parser)]
struct Args {
    /// Existing recovery_pipeline_benchmark --stress output; never modified.
    #[arg(long)]
    dataset: PathBuf,
    #[arg(long)]
    output: PathBuf,
}

const ARMS: [&str; 3] = ["hard_current", "soft_serial", "soft_parallel"];

fn soft_config() -> acquisition::Config {
    acquisition::Config {
        minimum_correlation: 0.70,
        maximum_hard_errors: 12,
        maximum_candidates: 32,
        maximum_work: 10_000_000,
    }
}

fn run(args: Args) -> Result<(), String> {
    let dataset = args.dataset.canonicalize().map_err(|e| e.to_string())?;
    let frozen_plan = input::read_json(&dataset.join("plan.json"))?;
    if frozen_plan["schema"] != "framelift-recovery-pipeline-development-v1"
        || frozen_plan["stress"] != true
        || frozen_plan["sample_rate_hz"] != 24_000
        || frozen_plan["sample_count"] != 8192
    {
        return Err("requires the frozen recovery pipeline stress grid".into());
    }
    let output = input::existing_new_dir(&args.output)?;
    let runtime = Guard::open(&std::env::current_exe().map_err(|e| e.to_string())?)?;
    let manifest = input::identity(&dataset.join("plan.json"))?;
    input::write_json_new(
        &output.join("plan.json"),
        &json!({
            "schema":"framelift-soft-acquisition-stress-plan-v1", "dataset":dataset,
            "frozen_manifest":manifest, "runtime":runtime.identity(), "arms":ARMS,
            "soft_acquisition":soft_config(), "positive_files":108,"negative_files":216,
            "matrix":{"mode":["fsk","gfsk","gmsk"],"code":["uncoded","conv","ldpc"],
                "sigma":[0.10,0.35,0.70],"seed_index":[0,1,2,3],"group":["clean","wrong_crc","noise"]},
            "baseline":"current code, same frozen extended_serial profile and coherent CPM",
            "truth_access":"post-decoding scoring only", "parameter_search":false,
            "field_evidence":false,"publication_ready":false,"equal_compute":false,
            "interpretation":"Paired replay of all frozen inputs; no raw data regenerated or marker corruption introduced."
        }),
    )?;
    let mut rows = Vec::new();
    for mode in ["fsk", "gfsk", "gmsk"] {
        for family in ["uncoded", "conv", "ldpc"] {
            for sigma in [10, 35, 70] {
                for seed_index in 0..4 {
                    for group in ["clean", "wrong_crc", "noise"] {
                        let name =
                            format!("{mode}-{family}-sigma{sigma:02}-seed{seed_index}-{group}");
                        let source = dataset.join(&name);
                        let target = output.join(&name);
                        std::fs::create_dir(&target).map_err(|e| e.to_string())?;
                        let guard = Guard::open(&source.join("input.cf32"))?;
                        let expected_record = input::read_json(&source.join("expected.json"))?;
                        if expected_record["input_sha256"].as_str()
                            != Some(guard.identity().sha256.as_str())
                        {
                            return Err(format!("frozen input digest mismatch: {name}"));
                        }
                        let expected: BTreeSet<String> =
                            serde_json::from_value(expected_record["frames"].clone())
                                .map_err(|e| e.to_string())?;
                        if expected.len() != usize::from(group == "clean") {
                            return Err(format!("unexpected frozen truth dimensions: {name}"));
                        }
                        let profile_path = source.join("extended_serial-profile.json");
                        let profile_identity = input::identity(&profile_path)?;
                        let plan: FilePlan =
                            serde_json::from_value(input::read_json(&profile_path)?)
                                .map_err(|e| e.to_string())?;
                        if plan.start_sample != 0
                            || plan.sample_count != 8192
                            || plan.sample_rate_hz != 24_000
                            || plan.receiver.modulation != mode
                            || plan.receiver.recovery.as_ref().is_none_or(|r| {
                                r.soft_acquisition.is_some() || !r.coherent_cpm || r.workers != 1
                            })
                        {
                            return Err(format!("unexpected frozen receiver profile: {name}"));
                        }
                        let samples = guard.read_iq_window(&plan.format, 0, plan.sample_count)?;
                        let old_report =
                            input::read_json(&source.join("extended_serial-report.json"))?;
                        let mut arm_rows = Vec::new();
                        let mut scientific = Vec::new();
                        let mut frame_sets = Vec::new();
                        for (index, arm) in ARMS.iter().enumerate() {
                            let mut plan = plan.clone();
                            if index > 0 {
                                let options = plan.receiver.recovery.as_mut().unwrap();
                                options.soft_acquisition = Some(soft_config());
                                options.workers = if index == 2 { 4 } else { 1 };
                            }
                            input::write_json_new(
                                &target.join(format!("{arm}-profile.json")),
                                &plan,
                            )?;
                            runtime.verify()?;
                            guard.verify()?;
                            let started = Instant::now();
                            let result = advanced_iq::decode(
                                &samples,
                                plan.sample_rate_hz,
                                &plan.receiver,
                                &guard.identity().sha256,
                            );
                            let wall_seconds = started.elapsed().as_secs_f64();
                            runtime.verify()?;
                            guard.verify()?;
                            match result {
                                Ok(report) => {
                                    let frames: BTreeSet<_> = report
                                        .frames
                                        .iter()
                                        .map(|frame| frame.frame_hex.clone())
                                        .collect();
                                    let value =
                                        serde_json::to_value(&report).map_err(|e| e.to_string())?;
                                    input::write_json_new(
                                        &target.join(format!("{arm}-report.json")),
                                        &value,
                                    )?;
                                    arm_rows.push(json!({"arm":arm,"wall_seconds":wall_seconds,
                                        "expected":expected.len(),"completed":true,"frames":frames,
                                        "recovered":frames.intersection(&expected).count(),
                                        "missed":expected.difference(&frames).count(),
                                        "false_acceptances":frames.difference(&expected).count(),
                                        "candidates":report.candidates}));
                                    scientific.push(value);
                                    frame_sets.push(frames);
                                }
                                Err(error) => {
                                    input::write_json_new(
                                        &target.join(format!("{arm}-error.json")),
                                        &json!({"error":error}),
                                    )?;
                                    arm_rows.push(json!({"arm":arm,"wall_seconds":wall_seconds,
                                        "expected":expected.len(),"completed":false,"error":error,
                                        "recovered":0,"missed":expected.len(),"false_acceptances":0}));
                                    scientific.push(Value::Null);
                                    frame_sets.push(BTreeSet::new());
                                }
                            }
                        }
                        rows.push(json!({"mode":mode,"code":family,"sigma":sigma as f64 / 100.,
                            "seed_index":seed_index,"group":group,"input":guard.identity(),
                            "frozen_profile":profile_identity,"arms":arm_rows,
                            "baseline_equals_frozen":scientific[0]==old_report,
                            "serial_parallel_identical":!scientific[1].is_null() && scientific[1]==scientific[2],
                            "added":frame_sets[1].difference(&frame_sets[0]).collect::<Vec<_>>(),
                            "lost":frame_sets[0].difference(&frame_sets[1]).collect::<Vec<_>>()}));
                    }
                }
            }
        }
    }
    let totals: Vec<_> = ARMS.iter().enumerate().map(|(index, arm)| {
        let sum = |key:&str| rows.iter().filter_map(|r|r["arms"][index][key].as_u64()).sum::<u64>();
        json!({"arm":arm,"expected":sum("expected"),"recovered":sum("recovered"),
            "missed":sum("missed"),"false_acceptances":sum("false_acceptances"),
            "errors":rows.iter().filter(|r|r["arms"][index]["completed"]==false).count(),
            "wall_seconds":rows.iter().filter_map(|r|r["arms"][index]["wall_seconds"].as_f64()).sum::<f64>()})
    }).collect();
    runtime.verify()?;
    let report = json!({"schema":"framelift-soft-acquisition-stress-results-v1",
        "files":rows.len(),"totals":totals,
        "all_baselines_equal_frozen":rows.iter().all(|r|r["baseline_equals_frozen"]==true),
        "all_serial_parallel_identical":rows.iter().all(|r|r["serial_parallel_identical"]==true),
        "added_frames":rows.iter().map(|r|r["added"].as_array().unwrap().len()).sum::<usize>(),
        "lost_frames":rows.iter().map(|r|r["lost"].as_array().unwrap().len()).sum::<usize>(),
        "rows":rows,"publication_ready":false,
        "interpretation":"Synthetic engineering stress only. All predeclared cells retained, including failures and errors. No orbital, external-decoder, calibrated false-alarm or equal-compute claim."});
    input::write_json_new(&output.join("report.json"), &report)?;
    println!(
        "{}",
        serde_json::to_string_pretty(&json!({"output":output,"totals":totals,
        "added_frames":report["added_frames"],"lost_frames":report["lost_frames"],
        "all_baselines_equal_frozen":report["all_baselines_equal_frozen"],
        "all_serial_parallel_identical":report["all_serial_parallel_identical"]}))
        .map_err(|e| e.to_string())?
    );
    Ok(())
}

fn main() {
    if let Err(error) = run(Args::parse()) {
        eprintln!("{error}");
        std::process::exit(1);
    }
}
