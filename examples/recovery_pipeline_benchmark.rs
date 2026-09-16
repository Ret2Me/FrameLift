//! Reproducible fixed-frame integration matrix; NOT an orbital or external-decoder benchmark.
use clap::Parser;
use num_complex::Complex64;
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::{collections::BTreeSet, io::Write, path::PathBuf, time::Instant};
use telemetry_yield_rs::{
    advanced_iq::{self, FilePlan},
    generic, input,
};
#[path = "support/recovery_pipeline_fixture.rs"]
mod fixture;

#[derive(Parser)]
struct Args {
    #[arg(long)]
    output: PathBuf,
    /// Fixed 108-positive CPM noise grid, plus matched bad-CRC and noise controls.
    #[arg(long)]
    stress: bool,
}

#[derive(Clone, serde::Serialize)]
struct Case {
    mode: &'static str,
    family: &'static str,
    sigma: f64,
    seed_index: Option<u64>,
}

fn cases(stress: bool) -> Vec<Case> {
    if stress {
        ["fsk", "gfsk", "gmsk"]
            .into_iter()
            .flat_map(|mode| {
                ["uncoded", "conv", "ldpc"]
                    .into_iter()
                    .flat_map(move |family| {
                        [0.10, 0.35, 0.70].into_iter().flat_map(move |sigma| {
                            (0..4).map(move |seed_index| Case {
                                mode,
                                family,
                                sigma,
                                seed_index: Some(seed_index),
                            })
                        })
                    })
            })
            .collect()
    } else {
        fixture::matrix()
            .into_iter()
            .map(|(mode, family)| Case {
                mode,
                family,
                sigma: 0.001,
                seed_index: None,
            })
            .collect()
    }
}

fn run(args: Args) -> Result<(), String> {
    let output = input::existing_new_dir(&args.output)?;
    let seed = 0x2026_0917_1375;
    let cases = cases(args.stress);
    let runtime = input::identity(&std::env::current_exe().map_err(|e| e.to_string())?)?;
    input::write_json_new(
        &output.join("plan.json"),
        &json!({
            "schema":"framelift-recovery-pipeline-development-v1", "seed":seed,
            "runtime":runtime,"matrix":cases,"groups":["clean","wrong_crc","noise"],
            "stress":args.stress,
            "stress_seeds":"base seed XOR seed index; paired identical noise across positive/bad-CRC/control within each grid cell",
            "arms":["baseline","extended_serial","extended_parallel"],
            "truth_access":"transmitter and post-decoding scoring only",
            "baseline":"same current frontend and FEC, coherent CPM disabled",
            "extended":"coherent CPM enabled for FSK/GFSK/GMSK; identical algorithm for other modulations",
            "comparison":"fixed development smoke matrix; NOT a previous full release or an external decoder",
            "field_evidence":false,"publication_ready":false,"equal_compute":false,
            "sample_rate_hz":fixture::RATE,"sample_count":fixture::LENGTH,
            "carrier_hz":0.,"clean_complex_noise_component_sigma":0.001,
            "negative_complex_noise_component_sigma":1.,
            "independent_encoder":"TC128 circulants; tapped-delay K7; GF bitwise shift-register RS",
        }),
    )?;
    let mut rng = fixture::base::Noise(seed);
    let mut rows = Vec::new();
    for case in &cases {
        let (mode, family) = (case.mode, case.family);
        for group in ["clean", "wrong_crc", "noise"] {
            let label = case.seed_index.map_or_else(String::new, |index| {
                format!(
                    "-sigma{:02}-seed{index}",
                    (case.sigma * 100.).round() as u32
                )
            });
            let directory = output.join(format!("{mode}-{family}{label}-{group}"));
            std::fs::create_dir(&directory).map_err(|e| e.to_string())?;
            let baseline = fixture::config(mode, family, 1, false);
            let mut frame = fixture::base::frame(17);
            let expected: BTreeSet<String> = if group == "clean" {
                [hex::encode(&frame)].into()
            } else {
                BTreeSet::new()
            };
            if group == "wrong_crc" {
                frame[7] ^= 1;
            }
            let sigma = if args.stress || group != "noise" {
                case.sigma
            } else {
                1.
            };
            let mut iq = if let Some(index) = case.seed_index {
                fixture::base::Noise(seed ^ index).fill(fixture::LENGTH, sigma)
            } else {
                rng.fill(fixture::LENGTH, sigma)
            };
            if group != "noise" {
                fixture::add(&mut iq, &baseline, &frame, 0.);
            }
            let raw: Vec<_> = iq
                .iter()
                .flat_map(|z| {
                    (z.re as f32)
                        .to_le_bytes()
                        .into_iter()
                        .chain((z.im as f32).to_le_bytes())
                })
                .collect();
            let digest = hex::encode(Sha256::digest(&raw));
            std::fs::File::create_new(directory.join("input.cf32"))
                .map_err(|e| e.to_string())?
                .write_all(&raw)
                .map_err(|e| e.to_string())?;
            let samples: Vec<_> = raw
                .as_chunks::<8>()
                .0
                .iter()
                .map(|b| {
                    Complex64::new(
                        f32::from_le_bytes(b[..4].try_into().unwrap()) as f64,
                        f32::from_le_bytes(b[4..].try_into().unwrap()) as f64,
                    )
                })
                .collect();
            input::write_json_new(
                &directory.join("expected.json"),
                &json!({"frames":expected,"input_sha256":digest}),
            )?;
            let mut arm_rows = Vec::new();
            let mut scientific = Vec::new();
            for (arm, workers, coherent) in [
                ("baseline", 1, false),
                ("extended_serial", 1, fixture::is_cpm(mode)),
                ("extended_parallel", 4, fixture::is_cpm(mode)),
            ] {
                let config = fixture::config(mode, family, workers, coherent);
                input::write_json_new(
                    &directory.join(format!("{arm}-profile.json")),
                    &FilePlan {
                        format: generic::InputFormat::Cf32Le,
                        sample_rate_hz: fixture::RATE,
                        start_sample: 0,
                        sample_count: samples.len(),
                        receiver: config.clone(),
                    },
                )?;
                let started = Instant::now();
                let report = advanced_iq::decode(&samples, fixture::RATE, &config, &digest);
                let wall_seconds = started.elapsed().as_secs_f64();
                match report {
                    Ok(report) => {
                        let actual: BTreeSet<_> =
                            report.frames.iter().map(|f| f.frame_hex.clone()).collect();
                        let value = serde_json::to_value(&report).map_err(|e| e.to_string())?;
                        input::write_json_new(
                            &directory.join(format!("{arm}-report.json")),
                            &value,
                        )?;
                        scientific.push(value);
                        arm_rows.push(json!({"arm":arm,"wall_seconds":wall_seconds,"expected":expected.len(),
                            "recovered":actual.intersection(&expected).count(),"false_acceptances":actual.difference(&expected).count(),
                            "missed":expected.difference(&actual).count(),"frames":actual,
                            "coherent_accepted_candidates":report.coherent.iter().filter(|c|c.get("accepted")==Some(&json!(true))).count()}));
                    }
                    Err(error) => {
                        input::write_json_new(
                            &directory.join(format!("{arm}-error.json")),
                            &json!({"error":error}),
                        )?;
                        scientific.push(Value::Null);
                        arm_rows.push(json!({"arm":arm,"wall_seconds":wall_seconds,"error":error,"expected":expected.len()}));
                    }
                }
            }
            rows.push(json!({"mode":mode,"code":family,"group":group,"sigma":sigma,"seed_index":case.seed_index,"input_sha256":digest,"arms":arm_rows,
                "serial_parallel_identical":!scientific[1].is_null() && scientific[1]==scientific[2]}));
        }
    }
    let mut totals = Vec::new();
    for index in 0..3 {
        let sum = |field: &str| {
            rows.iter()
                .filter_map(|r| r["arms"][index][field].as_u64())
                .sum::<u64>()
        };
        totals.push(json!({"arm":(["baseline","extended_serial","extended_parallel"][index]),
            "expected":sum("expected"),"recovered":sum("recovered"),"false_acceptances":sum("false_acceptances"),"missed":sum("missed"),
            "errors":rows.iter().filter(|r|r["arms"][index].get("error").is_some()).count(),
            "wall_seconds":rows.iter().filter_map(|r|r["arms"][index]["wall_seconds"].as_f64()).sum::<f64>()}));
    }
    let parity = rows.iter().all(|r| r["serial_parallel_identical"] == true);
    input::write_json_new(
        &output.join("report.json"),
        &json!({
            "schema":"framelift-recovery-pipeline-development-results-v1","files":rows.len(),
            "totals":totals,"all_serial_parallel_identical":parity,"rows":rows,
            "interpretation":"Synthetic engineering integration only; all declared failures retained. No external decoder, RF sensitivity, orbital yield or speedup claim."
        }),
    )?;
    println!("{}",serde_json::to_string_pretty(&json!({"files":rows.len(),"totals":totals,"all_serial_parallel_identical":parity,"output":output})).map_err(|e|e.to_string())?);
    Ok(())
}

fn main() {
    if let Err(error) = run(Args::parse()) {
        eprintln!("{error}");
        std::process::exit(1);
    }
}
