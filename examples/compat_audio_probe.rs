//! Development diagnosis: compare strict and GNU-compatible framing on the
//! exact same soft symbols. No reference payload or mission hint is accepted.
//! All compatibility-only outputs remain candidates, never trusted yield.
use clap::Parser;
use rayon::prelude::*;
use serde_json::{Value, json};
use std::{collections::BTreeMap, path::PathBuf, time::Instant};
use telemetry_yield_rs::{compat, dsp, input, protocol, receiver};

#[derive(Parser)]
struct Args {
    #[arg(long)]
    input: PathBuf,
    #[arg(long)]
    output: PathBuf,
    #[arg(long)]
    observation_id: u64,
    #[arg(long, default_value_t = 2)]
    threads: usize,
}

fn run(args: &Args) -> Result<Value, String> {
    if !(1..=16).contains(&args.threads) {
        return Err("threads must be 1..16".into());
    }
    let out = input::existing_new_dir(&args.output)?;
    let audio = input::load_audio(&args.input, &out)?;
    let config = dsp::DspConfig {
        bank: "full".into(),
        ..Default::default()
    };
    input::write_json_new(
        &out.join("plan.json"),
        &json!({
            "schema":"compat-audio-development-probe-v1", "config":config,
            "observation_id":args.observation_id,"input":audio.wav_identity,"source":audio.source_identity,
            "executable":input::identity(&std::env::current_exe().map_err(|e|e.to_string())?)?,
            "threads":args.threads,"window_seconds":6.0,"hop_seconds":3.0,
            "reference_bytes_used_for_search":false,"compatibility_outputs_are_candidates_only":true
        }),
    )?;
    let bounds = receiver::window_bounds(audio.samples.len(), audio.sample_rate, 6.0, 3.0)?;
    let pool = rayon::ThreadPoolBuilder::new()
        .num_threads(args.threads)
        .build()
        .map_err(|e| e.to_string())?;
    let start = Instant::now();
    let windows: Result<Vec<_>, String> = pool.install(|| bounds.par_iter().map(|&(left, right)| {
        let front = dsp::frontend(&audio.samples[left..right], audio.sample_rate, &config)?;
        let mut strict = BTreeMap::new();
        let mut compatible = BTreeMap::new();
        let mut rejected_structure = 0;
        for (rank, timing) in dsp::timing_bank(&front, &config)?.iter().enumerate() {
            let soft = dsp::soft_symbols(&front, timing)?;
            let origin = json!({"window_start_seconds":left as f64/audio.sample_rate as f64,"timing_rank":rank});
            for frame in protocol::decode_ax25(&soft, timing.threshold, &[false, true])? {
                strict.entry(hex::encode(frame)).or_insert_with(|| origin.clone());
            }
            let levels: Vec<u8> = soft.iter().map(|value| u8::from(*value >= timing.threshold)).collect();
            for candidate in compat::decode_gr_satellites_g3ruh_levels(&levels, 10_000)? {
                if candidate.strict_ax25_ui.is_none() {
                    rejected_structure += 1;
                    continue;
                }
                compatible.entry(hex::encode(&candidate.frame_with_fcs)).or_insert_with(|| json!({
                    "candidate":candidate,"origin":origin
                }));
            }
        }
        Ok((strict, compatible, rejected_structure))
    }).collect());
    let mut strict = BTreeMap::new();
    let mut compatible = BTreeMap::new();
    let mut rejected_structure = 0;
    for (a, b, rejected) in windows? {
        for (frame, origin) in a {
            strict.entry(frame).or_insert(origin);
        }
        for (frame, candidate) in b {
            compatible.entry(frame).or_insert(candidate);
        }
        rejected_structure += rejected;
    }
    let only: Vec<_> = compatible
        .iter()
        .filter(|(frame, _)| !strict.contains_key(*frame))
        .map(|(frame, evidence)| json!({"frame_with_fcs_hex":frame,"evidence":evidence}))
        .collect();
    let result = json!({"schema":"compat-audio-development-probe-v1","status":"complete",
        "observation_id":args.observation_id,"input":audio.wav_identity,
        "window_count":bounds.len(),"strict_full_frames":strict,"compatibility_only":only,
        "strict_unique_count":strict.len(),"compatibility_unique_count":compatible.len(),
        "compatibility_only_count":only.len(),"rejected_structure_instances":rejected_structure,
        "elapsed_seconds":start.elapsed().as_secs_f64(),"candidate_only":true,
        "reference_bytes_used_for_search":false,"publication_ready":false});
    input::write_json_new(&out.join("result.json"), &result)?;
    Ok(
        json!({"status":"complete","observation_id":args.observation_id,
        "strict_unique_count":strict.len(),"compatibility_only_count":only.len(),"candidate_only":true}),
    )
}
fn main() {
    match run(&Args::parse()) {
        Ok(value) => println!("{value}"),
        Err(error) => {
            eprintln!("{error}");
            std::process::exit(1);
        }
    }
}
