//! Fixed engineering cohort; acquisition sees IQ and the profile, not truth.
use clap::Parser;
use num_complex::Complex64;
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::{io::Write, path::PathBuf, time::Instant};
use telemetry_yield_rs::{
    advanced_iq::{self, Config, FilePlan, RepeatConfig},
    generic, input, interference,
};
#[path = "support/advanced_iq_fixture.rs"]
mod fixture;

#[derive(Parser)]
struct Args {
    #[arg(long)]
    output: PathBuf,
}

fn waveform(
    group: &str,
    case: usize,
    noise: &mut fixture::Noise,
) -> (Config, Vec<Complex64>, Vec<String>) {
    let mut c = fixture::config();
    c.cancellation = Some(interference::Config {
        rounds: 2,
        minimum_holdout_reduction: 0.2,
    });
    let first = fixture::frame((case + 1) as u8);
    let second = fixture::frame((case + 97) as u8);
    let mut expected = vec![hex::encode(&first)];
    let mut iq = noise.fill(if group == "repetition" { 8192 } else { 2048 }, 0.03);
    match group {
        "clean" => fixture::add(
            &mut iq,
            &fixture::symbols(&c, &first, None),
            128,
            1.,
            [-31.25, 0., 31.25][case % 3],
        ),
        "isi" => {
            let symbols = fixture::symbols(&c, &first, None);
            let blurred: Vec<_> = symbols
                .iter()
                .enumerate()
                .map(|(i, v)| {
                    v + if i > 0 { 0.45 * symbols[i - 1] } else { 0. }
                        + if i + 1 < symbols.len() {
                            0.2 * symbols[i + 1]
                        } else {
                            0.
                        }
                })
                .collect();
            fixture::add(&mut iq, &blurred, 128, 1., 0.);
        }
        "collision" => {
            expected.push(hex::encode(&second));
            fixture::add(
                &mut iq,
                &fixture::symbols(&c, &first, None),
                128,
                [1.5, 2., 3., 4.][case % 4],
                0.,
            );
            fixture::add(
                &mut iq,
                &fixture::symbols(&c, &second, None),
                128 + [32, 64, 96][case % 3],
                1.,
                0.,
            );
        }
        "repetition" => {
            c.repetition = Some(RepeatConfig {
                combine: true,
                key_bytes: 4,
                maximum_copies: 4,
                minimum_correlation: 0.1,
                maximum_gap_symbols: 10000,
                mission_header: None,
            });
            iq = noise.fill(8192, 0.0001);
            for copy in 0..4 {
                let mut symbols = fixture::symbols(&c, &first, Some(case as u32));
                for i in 0..128 {
                    if i >= 16 && (i - 16) % 4 != copy {
                        symbols[128 + i] = 0.;
                    }
                }
                fixture::add(&mut iq, &symbols, 128 + copy * 1536, 1., 0.);
            }
        }
        "wrong_crc" => {
            let mut bad = first;
            bad[7] ^= 1;
            fixture::add(&mut iq, &fixture::symbols(&c, &bad, None), 128, 1., 0.);
            expected.clear();
        }
        "noise" => {
            iq = noise.fill(2048, 1.);
            expected.clear();
        }
        _ => unreachable!(),
    }
    (c, iq, expected)
}

fn run(args: Args) -> Result<Value, String> {
    let out = input::existing_new_dir(&args.output)?;
    let seed = 0x20260916ab11;
    let groups = [
        ("clean", 16),
        ("isi", 16),
        ("collision", 32),
        ("repetition", 16),
        ("wrong_crc", 16),
        ("noise", 16),
    ];
    let runtime = input::identity(&std::env::current_exe().map_err(|e| e.to_string())?)?;
    input::write_json_new(
        &out.join("plan.json"),
        &json!({"schema":"advanced-iq-development-v1","seed":seed,"groups":groups,"arms":["single","turbo_joint","all"],"profile":fixture::config(),"channel":"estimated from received sync, not supplied oracle","noise":"complex Gaussian sigma 0.03; erasure controls sigma 0.0001; noise-only sigma 1","collision_amplitudes":[1.5,2.0,3.0,4.0],"collision_offsets_samples":[32,64,96],"isi_taps":[0.45,1.0,0.2],"repetition":"four copies, common 16 symbols plus interleaved complementary 28-symbol sets","runtime":runtime,"field_evidence":false,"equal_compute":false,"publication_ready":false}),
    )?;
    let mut noise = fixture::Noise(seed);
    let mut rows = Vec::new();
    for (group, count) in groups {
        for case in 0..count {
            let (config, iq, expected) = waveform(group, case, &mut noise);
            let name = format!("{group}-{case:02}");
            let case_dir = out.join(&name);
            std::fs::create_dir(&case_dir).map_err(|e| e.to_string())?;
            // Replay the exact retained cf32 bytes, not a more precise simulator.
            let raw: Vec<u8> = iq
                .iter()
                .flat_map(|z| {
                    (z.re as f32)
                        .to_le_bytes()
                        .into_iter()
                        .chain((z.im as f32).to_le_bytes())
                })
                .collect();
            let source_hash = hex::encode(Sha256::digest(&raw));
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
            let mut file = std::fs::OpenOptions::new()
                .create_new(true)
                .write(true)
                .open(case_dir.join("input.cf32"))
                .map_err(|e| e.to_string())?;
            file.write_all(&raw).map_err(|e| e.to_string())?;
            file.sync_all().map_err(|e| e.to_string())?;
            input::write_json_new(
                &case_dir.join("expected.json"),
                &json!({"frame_hex":expected}),
            )?;
            let mut arms = serde_json::Map::new();
            for offset in 0..3 {
                let arm = ["single", "turbo_joint", "all"][(case + offset) % 3];
                let mut receiver = config.clone();
                if arm != "all" {
                    receiver.cancellation = None;
                    if let Some(r) = &mut receiver.repetition {
                        r.combine = false;
                    }
                }
                if arm == "single" {
                    receiver.turbo_iterations = 1;
                    receiver.joint = None;
                }
                let plan = FilePlan {
                    format: generic::InputFormat::Cf32Le,
                    sample_rate_hz: 4000,
                    start_sample: 0,
                    sample_count: iq.len(),
                    receiver,
                };
                input::write_json_new(&case_dir.join(format!("{arm}-profile.json")), &plan)?;
                let start = Instant::now();
                let report = advanced_iq::decode(&iq, 4000, &plan.receiver, &source_hash)?;
                let elapsed = start.elapsed().as_secs_f64();
                let recovered: Vec<_> = report.frames.iter().map(|f| f.frame_hex.clone()).collect();
                let exact = recovered.iter().filter(|h| expected.contains(h)).count();
                let false_accept = recovered.iter().filter(|h| !expected.contains(h)).count();
                input::write_json_new(&case_dir.join(format!("{arm}-report.json")), &report)?;
                arms.insert(arm.into(),json!({"recovered":recovered,"exact":exact,"false_accept":false_accept,"wall_seconds":elapsed,"candidates":report.candidates,"combined_groups":report.combined_groups,"accepted_cancellations":report.cancellation.iter().filter(|r|r.accepted).count()}));
            }
            rows.push(json!({"group":group,"case":case,"input_sha256":source_hash,"expected":expected,"arms":arms}));
        }
    }
    let mut summary = Vec::new();
    for (group, _) in groups {
        let selected: Vec<_> = rows.iter().filter(|r| r["group"] == group).collect();
        let expected = selected
            .iter()
            .map(|r| r["expected"].as_array().unwrap().len())
            .sum::<usize>();
        let mut counts = serde_json::Map::new();
        for arm in ["single", "turbo_joint", "all"] {
            counts.insert(arm.into(),json!({"exact":selected.iter().map(|r|r["arms"][arm]["exact"].as_u64().unwrap()).sum::<u64>(),"false_accept":selected.iter().map(|r|r["arms"][arm]["false_accept"].as_u64().unwrap()).sum::<u64>()}));
        }
        summary.push(json!({"group":group,"recordings":selected.len(),"expected_frames":expected,"counts":counts}));
    }
    input::write_json_new(
        &out.join("report.json"),
        &json!({"schema":"advanced-iq-development-results-v1","summary":summary,"rows":rows}),
    )?;
    Ok(json!({"summary":summary,"recordings":rows.len()}))
}
fn main() {
    match run(Args::parse()) {
        Ok(value) => println!("{value}"),
        Err(error) => {
            eprintln!("{error}");
            std::process::exit(1);
        }
    }
}
