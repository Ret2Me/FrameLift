//! Development-only paired CPU replay; all experimental strengths are reported.
use clap::Parser;
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::{path::PathBuf, time::Instant};
use telemetry_yield_rs::{adaptive, input, uncertainty_audio};

#[derive(Parser)]
struct Args {
    #[arg(long)]
    input: PathBuf,
    #[arg(long)]
    output: PathBuf,
    #[arg(long, default_value_t = 0.0)]
    start_seconds: f64,
    #[arg(long)]
    duration_seconds: Option<f64>,
    #[arg(long, default_value_t = 9600.0)]
    baud: f64,
    #[arg(long, default_value_t = 2)]
    threads: usize,
}

fn run(args: &Args) -> Result<Value, String> {
    if !args.start_seconds.is_finite()
        || args.start_seconds < 0.0
        || args
            .duration_seconds
            .is_some_and(|x| !x.is_finite() || x <= 0.0 || x > 120.0)
        || !(1..=16).contains(&args.threads)
    {
        return Err(
            "pilot requires finite nonnegative start, duration in (0,120] and 1..=16 threads"
                .into(),
        );
    }
    let out = input::existing_new_dir(&args.output)?;
    let started = Instant::now();
    let audio = input::load_audio(&args.input, &out)?;
    let rate = audio.sample_rate;
    let left = (args.start_seconds * rate as f64).round() as usize;
    let right = match args.duration_seconds {
        Some(d) => left
            .checked_add((d * rate as f64).round() as usize)
            .ok_or("crop overflow")?,
        None => audio.samples.len(),
    };
    let samples = audio
        .samples
        .get(left..right)
        .ok_or("crop is outside input; no silent truncation")?;
    if samples.len() < 8192 || samples.len() as f64 / rate as f64 > 120.0 {
        return Err("pilot needs >=8192 samples and <=120 seconds".into());
    }
    let mut digest = Sha256::new();
    for sample in samples {
        digest.update(sample.to_bits().to_le_bytes());
    }
    let executable = input::identity(&std::env::current_exe().map_err(|e| e.to_string())?)?;
    input::write_json_new(
        &out.join("plan.json"),
        &json!({
            "schema":"uncertainty-audio-cpu-pilot-v1","publication_ready":false,
            "development_only":true,"gpu":false,"reference":"existing-adaptive-plus-innovations-no-codec",
            "not_full_progressive_portfolio":true,"crop_start_sample":left,"crop_end_sample":right,
            "sample_rate":rate,"crop_seconds":samples.len() as f64/rate as f64,
            "pcm_f64_le_sha256":hex::encode(digest.finalize()),"source":audio.source_identity,
            "decoded_wav":audio.wav_identity,"conversion_command":audio.conversion_command,
            "executable":executable,"threads":args.threads,"baud":args.baud,
            "strengths":uncertainty_audio::STRENGTHS,"primary_strength":1.0,"secondary_sensitivity_strength":4.0,
            "source_target_window_guard_seconds":1,"max_anchor_distance_seconds":60,
            "target_truth_used":false,"crc_guided_optimization":false,
            "retains_reference_union":true,"timing_scope":"shared host, reference followed by paired replay; conversions excluded from decoder times"
        }),
    )?;
    let result = uncertainty_audio::decode_samples(
        samples,
        rate,
        &adaptive::AdaptiveConfig {
            baud: args.baud,
            threads: args.threads,
        },
    );
    match result {
        Err(reason) => {
            input::write_json_new(
                &out.join("failure.json"),
                &json!({"status":"failed","reason":reason}),
            )?;
            Err(reason)
        }
        Ok(report) => {
            let mut lane_times = std::collections::BTreeMap::<String, f64>::new();
            for trial in &report.trials {
                *lane_times.entry(trial.lane.clone()).or_default() += trial.elapsed_seconds;
            }
            let counts: std::collections::BTreeMap<_, _> = report
                .lane_full_frames
                .iter()
                .map(|(k, v)| (k.clone(), v.len()))
                .collect();
            let summary = json!({"status":"complete","reference_frames":report.reference.union_full_frames.len(),
                "adaptive_frames":report.reference.baseline.union_full_frames.len(),"lane_frames":counts,
                "union_frames":report.union_full_frames.len(),"added_union":report.added_union_vs_reference.len(),
                "lost_union":report.lost_union_vs_reference.len(),"added_vs_matched_point":report.added_vs_matched_point,
                "lost_vs_matched_point":report.lost_vs_matched_point,"added_vs_reference":report.added_vs_reference,
                "source_models":report.sources.len(),"source_rejections":report.source_rejections.len(),
                "trials":report.trials.len(),"skips":report.skips.len(),"summed_trial_wall_seconds":lane_times,
                "reference_seconds":report.reference.total_decode_seconds,"paired_replay_seconds":report.paired_replay_seconds,
                "source_preparation_seconds":report.source_preparation_seconds,"decode_seconds":report.total_decode_seconds,
                "pre_output_wall_seconds":started.elapsed().as_secs_f64()});
            input::write_json_new(&out.join("report.json"), &report)?;
            input::write_json_new(&out.join("summary.json"), &summary)?;
            Ok(summary)
        }
    }
}
fn main() {
    match run(&Args::parse()) {
        Ok(v) => println!("{v}"),
        Err(e) => {
            eprintln!("{e}");
            std::process::exit(1);
        }
    }
}
