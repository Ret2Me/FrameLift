//! Development-only robust CPU replay; fixed Huber and Student-t metrics, both reported.
use clap::Parser;
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::{
    fs::OpenOptions,
    path::{Path, PathBuf},
    time::Instant,
};
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

// The follow-up progressive ablations consume this same PCM, rather than a
// separately quantized conversion. Refuse any sample that float32 cannot
// represent exactly (the original OGG loader already produces float32).
fn export_crop(path: &Path, samples: &[f64], rate: u32) -> Result<(), String> {
    if samples
        .iter()
        .any(|s| !s.is_finite() || ((*s as f32) as f64).to_bits() != s.to_bits())
    {
        return Err("shared float32 crop would change source samples".into());
    }
    let file = OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(path)
        .map_err(|e| e.to_string())?;
    let spec = hound::WavSpec {
        channels: 1,
        sample_rate: rate,
        bits_per_sample: 32,
        sample_format: hound::SampleFormat::Float,
    };
    let mut writer =
        hound::WavWriter::new(std::io::BufWriter::new(file), spec).map_err(|e| e.to_string())?;
    for value in samples {
        writer
            .write_sample(*value as f32)
            .map_err(|e| e.to_string())?;
    }
    writer.finalize().map_err(|e| e.to_string())?;
    let read = input::load_audio(path, path.parent().ok_or("crop has no parent")?)?;
    if read.sample_rate != rate
        || read.samples.len() != samples.len()
        || read
            .samples
            .iter()
            .zip(samples)
            .any(|(a, b)| a.to_bits() != b.to_bits())
    {
        return Err("shared crop roundtrip changed samples".into());
    }
    Ok(())
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
    let shared_crop = out.join("shared-crop.wav");
    export_crop(&shared_crop, samples, rate)?;
    input::write_json_new(
        &out.join("plan.json"),
        &json!({
            "schema":"robust-audio-cpu-pilot-v1","publication_ready":false,
            "development_only":true,"gpu":false,"reference":"existing-adaptive-plus-innovations-no-codec",
            "not_full_progressive_portfolio":true,"crop_start_sample":left,"crop_end_sample":right,
            "sample_rate":rate,"crop_seconds":samples.len() as f64/rate as f64,
            "pcm_f64_le_sha256":hex::encode(digest.finalize()),"source":audio.source_identity,
            "decoded_wav":audio.wav_identity,"conversion_command":audio.conversion_command,
            "executable":executable,"threads":args.threads,"baud":args.baud,
            "shared_crop_wav":input::identity(&shared_crop)?,"shared_crop_roundtrip_bit_exact":true,
            "metrics":[{"lane":"huber-2","delta":2.0},{"lane":"student-t-3","nu":3.0}],
            "noise_scale":"source-only residual standard deviation, scaled with candidate gain",
            "student_t_scale_note":"source residual SD used as distribution scale; no variance calibration claimed",
            "eligibility_note":"shared source fitter also requires finite covariance although robust lanes only consume mean and residual variance",
            "source_target_window_guard_seconds":1,"max_anchor_distance_seconds":60,
            "target_truth_used":false,"crc_guided_optimization":false,
            "retains_reference_union":true,"timing_scope":"shared host, reference followed by paired replay; conversions excluded from decoder times"
        }),
    )?;
    let result = uncertainty_audio::decode_robust_samples(
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

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn shared_crop_keeps_float32_samples_including_signed_zero() {
        let tmp = tempfile::tempdir().unwrap();
        let path = tmp.path().join("crop.wav");
        let samples: Vec<f64> = [-0.0_f32, 0.0, 0.3, -1.5, f32::MIN_POSITIVE]
            .into_iter()
            .cycle()
            .take(8192)
            .map(f64::from)
            .collect();
        export_crop(&path, &samples, 48000).unwrap();
        assert!(export_crop(&path, &samples, 48000).is_err());
    }
    #[test]
    fn shared_crop_refuses_quantization_and_nonfinite_samples() {
        let tmp = tempfile::tempdir().unwrap();
        for (n, sample) in [0.1_f64, f64::NAN].into_iter().enumerate() {
            let path = tmp.path().join(format!("crop-{n}.wav"));
            assert!(export_crop(&path, &[sample], 48000).is_err());
            assert!(!path.exists());
        }
    }
}
