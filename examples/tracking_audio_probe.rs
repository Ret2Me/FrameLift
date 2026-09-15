//! Opt-in two-by-two development experiment: frontend x clock recovery.
//! Fixed and tracking outputs are retained separately, plus their set union.
//! References, expected payloads, and frame timestamps are not inputs.
use clap::Parser;
use rayon::prelude::*;
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use std::{
    collections::{BTreeMap, BTreeSet},
    path::PathBuf,
    time::Instant,
};
use telemetry_yield_rs::{dsp, input, protocol, receiver, tracking};

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

#[derive(Clone, Debug, Serialize, Deserialize)]
struct Trial {
    frontend: String,
    clock: String,
    window_start_seconds: f64,
    elapsed_seconds: f64,
    hypotheses: usize,
    frame_with_fcs_hex: BTreeSet<String>,
}

fn collect_frames(
    soft: &[f64],
    threshold: f64,
    frames: &mut BTreeSet<String>,
) -> Result<(), String> {
    for frame in protocol::decode_ax25(soft, threshold, &[false, true])? {
        frames.insert(hex::encode(frame));
    }
    Ok(())
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
    // Fixed in advance; no choosing phase or bandwidth from decoded frames.
    let clocks: Vec<_> = [0.02, 0.06]
        .into_iter()
        .flat_map(|bandwidth| {
            (0..8).map(move |phase| tracking::GardnerConfig {
                bandwidth,
                initial_phase_symbols: (phase as f64 + 0.5) / 8.0,
                ..Default::default()
            })
        })
        .collect();
    let executable = input::identity(&std::env::current_exe().map_err(|e| e.to_string())?)?;
    input::write_json_new(
        &out.join("plan.json"),
        &json!({
            "schema":"tracking-audio-development-probe-v1", "observation_id":args.observation_id,
            "fixed_config":config,"tracking_configs":clocks,"threads":args.threads,
            "frontends":["legacy-fir512", "boxcar32-rms50"],
            "input":audio.wav_identity,"source":audio.source_identity,
            "conversion_command":audio.conversion_command,"executable":executable,
            "window_seconds":6.0,"hop_seconds":3.0,"reference_bytes_used_for_search":false,
            "coherent_iq_processing":false,"bit_repair_enabled":false,
            "baseline_bit_exactness_claimed":false,"publication_ready":false
        }),
    )?;
    let bounds = receiver::window_bounds(audio.samples.len(), audio.sample_rate, 6.0, 3.0)?;
    let pool = rayon::ThreadPoolBuilder::new()
        .num_threads(args.threads)
        .build()
        .map_err(|e| e.to_string())?;
    let started = Instant::now();
    let windows: Result<Vec<Vec<Trial>>, String> = pool.install(|| {
        bounds
            .par_iter()
            .map(|&(left, right)| {
                let pcm = &audio.samples[left..right];
                let mut trials = vec![];
                for name in ["legacy-fir512", "boxcar32-rms50"] {
                    let front_start = Instant::now();
                    let front = if name == "legacy-fir512" {
                        dsp::frontend(pcm, audio.sample_rate, &config)?
                    } else {
                        tracking::boxcar_frontend(pcm, audio.sample_rate, config.baud)?
                    };
                    let front_elapsed = front_start.elapsed().as_secs_f64();
                    let clock_start = Instant::now();
                    let mut fixed = BTreeSet::new();
                    let hypotheses = dsp::timing_bank(&front, &config)?;
                    for timing in &hypotheses {
                        collect_frames(
                            &dsp::soft_symbols(&front, timing)?,
                            timing.threshold,
                            &mut fixed,
                        )?;
                    }
                    trials.push(Trial {
                        frontend: name.into(),
                        clock: "fixed-full160".into(),
                        window_start_seconds: left as f64 / audio.sample_rate as f64,
                        elapsed_seconds: front_elapsed + clock_start.elapsed().as_secs_f64(),
                        hypotheses: hypotheses.len(),
                        frame_with_fcs_hex: fixed,
                    });
                    let clock_start = Instant::now();
                    let mut adaptive = BTreeSet::new();
                    for clock in &clocks {
                        let symbols = tracking::gardner(&front, clock)?;
                        collect_frames(&symbols.soft, 0.0, &mut adaptive)?;
                    }
                    trials.push(Trial {
                        frontend: name.into(),
                        clock: "gardner16".into(),
                        window_start_seconds: left as f64 / audio.sample_rate as f64,
                        elapsed_seconds: front_elapsed + clock_start.elapsed().as_secs_f64(),
                        hypotheses: clocks.len(),
                        frame_with_fcs_hex: adaptive,
                    });
                }
                Ok(trials)
            })
            .collect()
    });
    let windows: Vec<_> = windows?.into_iter().flatten().collect();
    let mut variants: BTreeMap<String, BTreeSet<String>> = BTreeMap::new();
    for window in &windows {
        variants
            .entry(format!("{}/{}", window.frontend, window.clock))
            .or_default()
            .extend(window.frame_with_fcs_hex.iter().cloned());
    }
    let union: BTreeSet<_> = variants.values().flatten().cloned().collect();
    let old = variants
        .get("legacy-fir512/fixed-full160")
        .cloned()
        .unwrap_or_default();
    let added: Vec<_> = union.difference(&old).cloned().collect();
    let counts: BTreeMap<_, _> = variants
        .iter()
        .map(|(name, frames)| (name.clone(), frames.len()))
        .collect();
    let result = json!({
        "schema":"tracking-audio-development-probe-v1","status":"complete",
        "observation_id":args.observation_id,"source":audio.source_identity,"input":audio.wav_identity,
        "variant_counts":counts,"variant_full_frames":variants,"union_full_frames":union,
        "added_vs_legacy_full":added,"added_vs_legacy_full_count":added.len(),
        "window_trials":windows,"window_count":bounds.len(),"elapsed_seconds":started.elapsed().as_secs_f64(),
        "frontends_share_preprocessing_between_clock_trials":true,
        "window_elapsed_includes_frontend_cost_for_each_variant":true,
        "timing_is_diagnostic_not_matched_isolated_benchmark":true,
        "reference_bytes_used_for_search":false,"bit_repair_enabled":false,
        "publication_ready":false,"deployment_ready":false
    });
    input::write_json_new(&out.join("result.json"), &result)?;
    Ok(
        json!({"observation_id":args.observation_id,"status":"complete", "variant_counts":counts,
        "union_count":union.len(),"added_vs_legacy_full_count":added.len()}),
    )
}

fn main() {
    match run(&Args::parse()) {
        Ok(result) => println!("{result}"),
        Err(error) => {
            eprintln!("{error}");
            std::process::exit(1);
        }
    }
}
