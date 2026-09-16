//! Outcome-independent engineering matrix; not an orbital-data benchmark.
use clap::Parser;
use num_complex::Complex64;
use serde_json::json;
use sha2::{Digest, Sha256};
use std::{collections::BTreeSet, io::Write, path::PathBuf, time::Instant};
use telemetry_yield_rs::{
    advanced_iq::{self, FilePlan},
    generic, input,
};
#[path = "support/multimode_fixture.rs"]
mod fixture;
use fixture::{Tx, base};

#[derive(Parser)]
struct Args {
    #[arg(long)]
    output: PathBuf,
}

fn run(args: Args) -> Result<serde_json::Value, String> {
    let out = input::existing_new_dir(&args.output)?;
    let runtime = input::identity(&std::env::current_exe().map_err(|e| e.to_string())?)?;
    let seed = 0x2026_0916_71ab;
    let groups = [
        "clean",
        "collision",
        "repetition",
        "symbol_erasure_repetition",
        "wrong_crc",
        "noise",
    ];
    input::write_json_new(
        &out.join("plan.json"),
        &json!({
            "schema":"framelift-multimode-engineering-v1","seed":seed,"runtime":runtime,
            "modes":fixture::MODES,"groups":groups,"cases_per_group":4,
            "arms":["single","all"],"truth_access":"only fixture generation and post-run scoring",
            "comparison":"same new frontend: one-pass versus optional turbo/joint/repeats/SIC; NOT entire previous release",
            "carrier_hz":[-23.5,0.0,23.5,47.0],"collision_amplitudes":[2.0,3.0,5.0,7.0],
            "symbol_erasures":"artificial pre-modulator level mutes; AFSK RF amplitude mutes; not a calibrated physical fading model",
            "field_evidence":false,"publication_ready":false,"equal_compute":false
        }),
    )?;
    let mut rng = base::Noise(seed);
    let mut rows = Vec::new();
    for mode in fixture::MODES {
        for group in groups {
            for case in 0..4 {
                let repeat = group == "repetition" || group == "symbol_erasure_repetition";
                let mut c = fixture::config(mode);
                if repeat {
                    fixture::repetition(&mut c);
                }
                let mut iq = rng.fill(
                    if repeat { 32768 } else { fixture::LENGTH },
                    if group == "noise" { 1. } else { 0.002 },
                );
                let first = base::frame(case + 1);
                let second = base::frame(case + 97);
                let mut expected = BTreeSet::from([hex::encode(&first)]);
                match group {
                    "clean" => fixture::add(
                        &mut iq,
                        &c,
                        &first,
                        None,
                        Tx {
                            carrier: [-23.5, 0., 23.5, 47.][case as usize],
                            ..Tx::default()
                        },
                    ),
                    "collision" => {
                        fixture::add(
                            &mut iq,
                            &c,
                            &first,
                            None,
                            Tx {
                                amplitude: [2., 3., 5., 7.][case as usize],
                                ..Tx::default()
                            },
                        );
                        fixture::add(
                            &mut iq,
                            &c,
                            &second,
                            None,
                            Tx {
                                start: 416,
                                phase: -0.6,
                                ..Tx::default()
                            },
                        );
                        expected.insert(hex::encode(second));
                    }
                    "repetition" | "symbol_erasure_repetition" => {
                        for copy in 0..4 {
                            fixture::add(
                                &mut iq,
                                &c,
                                &first,
                                Some(case as u32),
                                Tx {
                                    start: 256 + copy * 8192,
                                    erasure_copy: if group == "symbol_erasure_repetition" {
                                        Some(copy)
                                    } else {
                                        None
                                    },
                                    ..Tx::default()
                                },
                            );
                        }
                    }
                    "wrong_crc" => {
                        let mut bad = first;
                        bad[7] ^= 1;
                        fixture::add(&mut iq, &c, &bad, None, Tx::default());
                        expected.clear();
                    }
                    "noise" => {
                        expected.clear();
                    }
                    _ => unreachable!(),
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
                let hash = hex::encode(Sha256::digest(&raw));
                let iq: Vec<_> = raw
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
                let directory = out.join(format!("{mode}-{group}-{case:02}"));
                std::fs::create_dir(&directory).map_err(|e| e.to_string())?;
                let mut file = std::fs::OpenOptions::new()
                    .create_new(true)
                    .write(true)
                    .open(directory.join("input.cf32"))
                    .map_err(|e| e.to_string())?;
                file.write_all(&raw).map_err(|e| e.to_string())?;
                file.sync_all().map_err(|e| e.to_string())?;
                input::write_json_new(
                    &directory.join("expected.json"),
                    &json!({"frame_hex":expected}),
                )?;
                let mut arms = serde_json::Map::new();
                let mut sets = std::collections::BTreeMap::new();
                for offset in 0..2 {
                    let name = ["single", "all"][(case as usize + offset) % 2];
                    let mut receiver = c.clone();
                    if name == "single" {
                        receiver.turbo_iterations = 1;
                        receiver.joint = None;
                        receiver.cancellation = None;
                        if let Some(repeat) = &mut receiver.repetition {
                            repeat.combine = false;
                        }
                    }
                    let plan = FilePlan {
                        format: generic::InputFormat::Cf32Le,
                        sample_rate_hz: fixture::RATE,
                        start_sample: 0,
                        sample_count: iq.len(),
                        receiver,
                    };
                    input::write_json_new(&directory.join(format!("{name}-profile.json")), &plan)?;
                    let start = Instant::now();
                    let report = advanced_iq::decode(&iq, fixture::RATE, &plan.receiver, &hash)?;
                    let elapsed = start.elapsed().as_secs_f64();
                    let frames: BTreeSet<_> =
                        report.frames.iter().map(|f| f.frame_hex.clone()).collect();
                    let correct = frames.intersection(&expected).count();
                    let false_frames = frames.difference(&expected).count();
                    input::write_json_new(&directory.join(format!("{name}-report.json")), &report)?;
                    arms.insert(name.into(),json!({"correct_frames":correct,"false_frames":false_frames,
                    "seconds":elapsed,"combined_groups":report.combined_groups,
                    "accepted_cancellations":report.cancellation.iter().filter(|r|r.accepted).count()}));
                    sets.insert(name, frames);
                }
                let added = sets["all"]
                    .difference(&sets["single"])
                    .filter(|h| expected.contains(*h))
                    .count();
                let lost = sets["single"].difference(&sets["all"]).count();
                rows.push(
                    json!({"mode":mode,"group":group,"case":case,"expected":expected.len(),
                "input_sha256":hash,"arms":arms,"added":added,"lost":lost}),
                );
            }
        }
    }
    let total = |arm: &str, field: &str| {
        rows.iter()
            .map(|r| r["arms"][arm][field].as_u64().unwrap())
            .sum::<u64>()
    };
    let report = json!({"schema":"framelift-multimode-engineering-result-v1","field_evidence":false,
        "files":rows.len(),"single_correct":total("single","correct_frames"),"all_correct":total("all","correct_frames"),
        "false_frames":total("single","false_frames")+total("all","false_frames"),
        "added":rows.iter().map(|r|r["added"].as_u64().unwrap()).sum::<u64>(),
        "lost":rows.iter().map(|r|r["lost"].as_u64().unwrap()).sum::<u64>(),"rows":rows});
    if runtime.sha256
        != input::identity(&std::env::current_exe().map_err(|e| e.to_string())?)?.sha256
    {
        return Err("benchmark executable changed".into());
    }
    input::write_json_new(&out.join("report.json"), &report)?;
    Ok(report)
}
fn main() {
    match run(Args::parse()) {
        Ok(mut result) => {
            result.as_object_mut().unwrap().remove("rows");
            println!("{}", result);
        }
        Err(error) => {
            eprintln!("{error}");
            std::process::exit(1);
        }
    }
}
