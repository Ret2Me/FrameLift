//! Diagnostic full-recording M&M bank; established detector, no novelty claim.
#[path = "../rust/mm_clock.rs"]
mod mm_clock;
use clap::Parser;
use serde_json::json;
use std::{collections::BTreeSet, path::PathBuf, time::Instant};
use telemetry_yield_rs::{input, protocol};
#[derive(Parser)]
struct Args {
    #[arg(long)]
    input: PathBuf,
    #[arg(long)]
    output: PathBuf,
    #[arg(long, default_value_t = 9600.0)]
    baud: f64,
}
fn run(a: Args) -> Result<(), String> {
    let total_started = Instant::now();
    let executable = input::identity(&std::env::current_exe().map_err(|e| e.to_string())?)?;
    let root = input::existing_new_dir(&a.output)?;
    let audio = input::load_audio(&a.input, &root)?;
    let source = audio.source_identity.clone();
    let started = Instant::now();
    let mut union = BTreeSet::new();
    let mut branches = Vec::new();
    for interpolation in [
        mm_clock::Interpolation::Linear,
        mm_clock::Interpolation::Cubic,
    ] {
        for phase in [0.0, 0.25, 0.5, 0.75] {
            for gain in [0.5, 1.0, 2.0] {
                let c = mm_clock::Config {
                    samples_per_symbol: audio.sample_rate as f64 / a.baud,
                    initial_phase: phase,
                    gain_omega: std::f64::consts::TAU / 100.0,
                    gain_mu: 0.0625,
                    relative_limit: 0.01,
                    input_gain: gain,
                    interpolation,
                };
                let branch = (|| -> Result<_, String> {
                    let samples = mm_clock::recover(&audio.samples, c)?;
                    let frames = protocol::decode_ax25(&samples, 0.0, &[false, true])?;
                    let mut set = BTreeSet::new();
                    for f in frames {
                        if !protocol::valid_ax25_fcs(&f)
                            || !protocol::valid_ax25_ui(&f[..f.len() - 2])
                        {
                            return Err("invalid decoder frame".into());
                        }
                        set.insert(hex::encode(f));
                    }
                    Ok((samples.len(), set))
                })();
                let mut row = json!({"interpolation":format!("{interpolation:?}"),"phase":phase,"input_gain":gain});
                match branch {
                    Ok((count, set)) => {
                        row["status"] = "complete".into();
                        row["symbols"] = json!(count);
                        row["frame_with_fcs_hex"] = json!(set);
                        union.extend(set);
                    }
                    Err(error) => {
                        row["status"] = "failed".into();
                        row["error"] = error.into();
                    }
                }
                branches.push(row);
            }
        }
    }
    if input::identity(&a.input)?.sha256 != source.sha256 {
        return Err("source changed".into());
    }
    let complete = branches.iter().all(|x| x["status"] == "complete");
    let result = json!({"schema":"mm-clock-development-probe-v1","source":source,"executable":executable,"prepared":audio.wav_identity,"conversion_command":audio.conversion_command,"sample_rate_hz":audio.sample_rate,"baud":a.baud,"configuration":{"gain_omega":std::f64::consts::TAU/100.0,"gain_mu":0.0625,"relative_limit":0.01,"interpolations":["Linear","Cubic"],"initial_phases":[0.0,0.25,0.5,0.75],"input_gains":[0.5,1.0,2.0],"threshold":0.0,"g3ruh_modes":[false,true]},"status":if complete {"complete"} else {"incomplete"},"union_count":union.len(),"frame_with_fcs_hex":union,"branches":branches,"decode_wall_seconds":started.elapsed().as_secs_f64(),"total_wall_seconds":total_started.elapsed().as_secs_f64(),"scope":"real pre-clock audio, explicit19200Hz/9600baud is supported; no IQfrontend or sample-rate conversion","novel_algorithm":false,"packet_guided_clock":false,"publication_ready":false});
    input::write_json_new(&root.join("result.json"), &result)?;
    println!(
        "{}",
        json!({"status":result["status"],"union_count":result["union_count"],"output":root})
    );
    if complete {
        Ok(())
    } else {
        Err("one or more clock branches failed".into())
    }
}
fn main() {
    if let Err(e) = run(Args::parse()) {
        eprintln!("{e}");
        std::process::exit(1);
    }
}
