//! Exact-output engineering microbenchmark, not an end-to-end receiver speed claim.
use clap::Parser;
use serde_json::json;
use sha2::{Digest, Sha256};
use std::{hint::black_box, path::PathBuf, time::Instant};
use telemetry_yield_rs::{coherent_cpm, input};
#[path = "support/recovery_pipeline_fixture.rs"]
#[allow(dead_code)]
mod fixture;

#[derive(Parser)]
struct Args {
    #[arg(long)]
    output: PathBuf,
    #[arg(long, default_value_t = 128)]
    iterations: usize,
}

fn run(args: Args) -> Result<(), String> {
    if !(1..=10_000).contains(&args.iterations) {
        return Err("iterations must be in 1..10000".into());
    }
    let output = input::existing_new_dir(&args.output)?;
    let runtime = input::identity(&std::env::current_exe().map_err(|e| e.to_string())?)?;
    let mut rows = Vec::new();
    for mode in ["fsk", "gfsk", "gmsk"] {
        let config = fixture::config(mode, "ldpc", 1, true);
        let bits = config.syncword.len() + config.code.encoded_bits()?;
        let mut iq = fixture::base::Noise(0x2026_0917_3375).fill(fixture::LENGTH, 0.35);
        fixture::add(&mut iq, &config, &fixture::base::frame(17), 0.);
        let iq = &iq[256..256 + bits * 8];
        let mut known: Vec<_> = config.syncword.iter().copied().map(Some).collect();
        known.resize(bits, None);
        let cpm = coherent_cpm::CoherentCpmConfig {
            modulation_index_numerator: if mode == "gmsk" { 1 } else { 11 },
            modulation_index_denominator: if mode == "gmsk" { 2 } else { 15 },
            gaussian_bt: (mode != "fsk").then_some(0.5),
            noise_variance: 2. * 0.35 * 0.35,
            max_carrier_offset_radians_per_sample: 0.,
            ..Default::default()
        };
        let started = Instant::now();
        let prepared = coherent_cpm::prepare(iq, &known, &cpm)?;
        let prepare_seconds = started.elapsed().as_secs_f64();
        // Arbitrary bounded priors, independent of transmitter payload bits.
        let prior: Vec<_> = (0..bits).map(|i| (i as f64 * 0.17).sin() * 0.8).collect();
        let result = prepared.detect(&prior)?;
        let result = serde_json::to_value(result).map_err(|e| e.to_string())?;
        input::write_json_new(&output.join(format!("{mode}-result.json")), &result)?;
        let digest = hex::encode(Sha256::digest(
            serde_json::to_vec(&result).map_err(|e| e.to_string())?,
        ));
        for _ in 0..4 {
            black_box(prepared.detect(black_box(&prior))?);
        }
        let started = Instant::now();
        for _ in 0..args.iterations {
            black_box(prepared.detect(black_box(&prior))?);
        }
        let detect_seconds = started.elapsed().as_secs_f64();
        let dimensions_iterations = 10_000;
        let started = Instant::now();
        for _ in 0..dimensions_iterations {
            black_box(black_box(&config.code).encoded_bits()?);
        }
        let dimensions_seconds = started.elapsed().as_secs_f64();
        let prepare_iterations = args.iterations.min(32);
        let started = Instant::now();
        let mut reference = None;
        for _ in 0..prepare_iterations {
            let initial = coherent_cpm::prepare(iq, &known, &cpm)?;
            let updated = coherent_cpm::CoherentCpmConfig {
                noise_variance: initial
                    .diagnostics()
                    .training_residual_noise_power
                    .max(1e-6),
                ..cpm.clone()
            };
            reference = Some(black_box(coherent_cpm::prepare(iq, &known, &updated)?));
        }
        let two_stage_prepare_seconds = started.elapsed().as_secs_f64();
        let started = Instant::now();
        let mut reused = None;
        for _ in 0..prepare_iterations {
            reused = Some(black_box(coherent_cpm::prepare_with_training_noise(
                iq, &known, &cpm, 1e-6,
            )?));
        }
        let single_stage_prepare_seconds = started.elapsed().as_secs_f64();
        let preparation_identical = serde_json::to_value(reference.unwrap().detect(&prior)?)
            .map_err(|e| e.to_string())?
            == serde_json::to_value(reused.unwrap().detect(&prior)?).map_err(|e| e.to_string())?;
        if !preparation_identical {
            return Err(format!("{mode}: reused training changed scientific output"));
        }
        rows.push(json!({"mode":mode,"bits":bits,"states":prepared.diagnostics().trellis_states,
            "prepare_seconds":prepare_seconds,"detect_seconds":detect_seconds,
            "iterations":args.iterations,"output_sha256":digest,
            "preparation_iterations":prepare_iterations,"two_stage_prepare_seconds":two_stage_prepare_seconds,
            "single_stage_prepare_seconds":single_stage_prepare_seconds,"preparation_identical":preparation_identical,
            "validated_dimensions_calls":dimensions_iterations,"validated_dimensions_seconds":dimensions_seconds}));
    }
    input::write_json_new(
        &output.join("report.json"),
        &json!({
            "schema":"framelift-recovery-kernels-development-v1","runtime":runtime,"rows":rows,
            "interpretation":"Prepared-kernel CPU timing on a shared host; no end-to-end speedup or sensitivity claim. Exact complete result hashes include all floating-point LLRs."
        }),
    )?;
    println!("{}", output.display());
    Ok(())
}

fn main() {
    if let Err(error) = run(Args::parse()) {
        eprintln!("{error}");
        std::process::exit(1);
    }
}
