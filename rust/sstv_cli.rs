//! Experimental analog PD receiver. Separate executable; no packet-yield claims.
#[path = "sstv.rs"]
mod sstv;
use clap::Parser;
use sha2::{Digest, Sha256};
use std::{fs, io::Write, path::PathBuf};

#[derive(Parser)]
struct Args {
    /// Mono PCM16 WAV, 12000–48000 samples/s (not IQ).
    #[arg(long)]
    input: PathBuf,
    /// New directory, never overwrite an earlier run.
    #[arg(long)]
    output: PathBuf,
    #[arg(long, default_value = "pd120")]
    mode: String,
    /// Disable global timing fit and missing-sync bridging (ablation, not external reference).
    #[arg(long)]
    local_sync: bool,
}
fn main() -> Result<(), Box<dyn std::error::Error>> {
    let args = Args::parse();
    let mode = sstv::Mode::parse(&args.mode)?;
    let bytes = fs::read(&args.input)?;
    let mut wav = hound::WavReader::new(std::io::Cursor::new(&bytes))?;
    let spec = wav.spec();
    if spec.channels != 1
        || spec.bits_per_sample != 16
        || spec.sample_format != hound::SampleFormat::Int
        || !(12000..=48000).contains(&spec.sample_rate)
    {
        return Err("requires mono PCM16 audio at 12–48 kHz; convert explicitly, not IQ".into());
    }
    let samples: Vec<f32> = wav
        .samples::<i16>()
        .map(|x| x.map(|v| v as f32 / 32768.))
        .collect::<Result<_, _>>()?;
    if samples.len() > spec.sample_rate as usize * 600 {
        return Err("maximum recording duration is 600 seconds".into());
    }
    let start = std::time::Instant::now();
    let result = sstv::decode(&samples, spec.sample_rate as f64, mode, !args.local_sync)?;
    fs::create_dir(&args.output)?;
    let mut ppm = fs::File::create(args.output.join("image.ppm"))?;
    write!(ppm, "P6\n640 {}\n255\n", result.rows)?;
    ppm.write_all(&result.rgb)?;
    let report = serde_json::json!({
        "schema": "sstv-experimental-v1", "input": args.input,
        "input_sha256": format!("{:x}", Sha256::digest(&bytes)),
        "mode": args.mode, "sample_rate": spec.sample_rate,
        "global_timing": !args.local_sync, "elapsed_seconds": start.elapsed().as_secs_f64(),
        "width": 640, "rows": result.rows, "sync_candidates": result.candidates,
        "observed_sync_pairs": result.observed, "predicted_sync_pairs": result.predicted,
        "pair_period_seconds": result.period, "first_sync_seconds": result.first,
        "line_pairs": result.pairs,
        "integrity": "analog; no CRC or independent pixel ground truth",
        "vis_origin_seconds": result.vis_origin,
        "sync_candidate_seconds": result.sync_times,
        "timing_segments": result.timing_segments,
        "alignment": if result.vis_origin.is_some() { "VIS-parity-verified image origin; timing refined from line syncs" } else { "first matched line-pair sync; absolute image origin unknown" },
        "missing_sync_policy": "timing prediction only; pixels always sampled from recorded audio; no inpainting",
        "scope": "single contiguous image segment, at most 248 line pairs; experimental"
    });
    fs::write(
        args.output.join("report.json"),
        serde_json::to_vec_pretty(&report)?,
    )?;
    println!(
        "{} rows, {} observed sync pairs, {} predicted; {:.3}s",
        result.rows,
        result.observed,
        result.predicted,
        start.elapsed().as_secs_f64()
    );
    Ok(())
}
