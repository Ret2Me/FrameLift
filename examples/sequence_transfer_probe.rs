//! Deterministic synthetic AX.25/G3RUH transfer experiment at symbol interface.
//!
//! This deliberately does NOT compare complete audio receivers or SatNOGS. It
//! checks that a CRC-validated strong packet can supply aggregate channel taps
//! to decode *different* noisy payloads, without giving their truth bits to the
//! learner/detector. The fixed three-tap channel matches the detector family:
//! positive results establish implementation feasibility, not generalization
//! to real recordings or a publication-ready receiver advantage.

use clap::Parser;
use serde::Serialize;
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::{collections::BTreeSet, path::PathBuf, time::Instant};
use telemetry_yield_rs::{anchors, input, protocol, sequence};

const SEED: u64 = 0x4370_9600_2026_0910;
const TAPS: [f64; 3] = [0.35, 0.8, 0.35];
const BIAS: f64 = 0.04;
const NOISE_GRID: [f64; 4] = [0.0, 0.1, 0.2, 0.3];
const CASES_PER_GROUP: usize = 16;
const FLAG: [u8; 8] = [0, 1, 1, 1, 1, 1, 1, 0];

#[derive(Parser)]
struct Args {
    /// An exclusively created result directory; existing outputs are refused.
    #[arg(long)]
    output: PathBuf,
}

struct Prng(u64);

impl Prng {
    fn next(&mut self) -> u64 {
        self.0 ^= self.0 << 13;
        self.0 ^= self.0 >> 7;
        self.0 ^= self.0 << 17;
        self.0
    }

    fn uniform(&mut self) -> f64 {
        // Open unit interval, so Box-Muller never evaluates ln(0).
        ((self.next() >> 12) as f64 + 0.5) / 4_503_599_627_370_496.0
    }

    fn gaussian(&mut self) -> f64 {
        (-2.0 * self.uniform().ln()).sqrt()
            * (std::f64::consts::TAU * self.uniform()).cos()
    }
}

fn case_seed(index: usize) -> u64 {
    SEED ^ (index as u64 + 1).wrapping_mul(0x9e37_79b9_7f4a_7c15)
}

fn frame(index: u32, rng: &mut Prng) -> Vec<u8> {
    // Two strict, valid AX.25 addresses, UI control=0x03 and no-layer-3 PID.
    let mut bytes = hex::decode("94a662b2a0826094a662b29eb2e103f0").expect("fixed valid header");
    bytes.extend_from_slice(b"TRANSFER-V1");
    bytes.extend_from_slice(&index.to_le_bytes());
    bytes.extend((0..64).map(|_| rng.next() as u8));
    bytes.extend_from_slice(&protocol::crc16_x25(&bytes).to_le_bytes());
    bytes
}

fn encode_frame(frame: &[u8]) -> Vec<u8> {
    let mut bits = FLAG.repeat(32);
    let mut ones = 0;
    for byte in frame {
        for bit in 0..8 {
            let value = (byte >> bit) & 1;
            bits.push(value);
            ones = if value == 1 { ones + 1 } else { 0 };
            if ones == 5 {
                bits.push(0);
                ones = 0;
            }
        }
    }
    bits.extend(FLAG.repeat(32));
    let mut scrambled = Vec::with_capacity(bits.len());
    for (index, bit) in bits.into_iter().enumerate() {
        let mut value = bit;
        if index >= 12 {
            value ^= scrambled[index - 12];
        }
        if index >= 17 {
            value ^= scrambled[index - 17];
        }
        scrambled.push(value);
    }
    let mut level = 0;
    scrambled
        .into_iter()
        .map(|bit| {
            if bit == 0 {
                level ^= 1;
            }
            level
        })
        .collect()
}

fn transmitted_symbols(levels: &[u8], noise_amplitude: f64, rng: &mut Prng) -> Vec<f64> {
    let bipolar: Vec<f64> = levels.iter().map(|level| 2.0 * f64::from(*level) - 1.0).collect();
    (0..levels.len())
        .map(|index| {
            BIAS
                + TAPS[0] * bipolar[index.saturating_sub(1)]
                + TAPS[1] * bipolar[index]
                + TAPS[2] * bipolar[(index + 1).min(levels.len() - 1)]
                + noise_amplitude * (2.0 * rng.uniform() - 1.0)
        })
        .collect()
}

fn symbols_sha256(symbols: &[f64]) -> String {
    let mut digest = Sha256::new();
    for symbol in symbols {
        digest.update(symbol.to_bits().to_le_bytes());
    }
    hex::encode(digest.finalize())
}

fn accepted_frames(soft: &[f64], threshold: f64) -> Result<BTreeSet<String>, String> {
    Ok(protocol::decode_ax25(soft, threshold, &[false, true])?
        .into_iter()
        .map(hex::encode)
        .collect())
}

/// No target truth argument exists at this processing boundary.
fn recover(
    samples: &[f64],
    model: &sequence::ChannelModel,
) -> Result<(BTreeSet<String>, BTreeSet<String>), String> {
    let slicer = accepted_frames(samples, model.bias)?;
    let detected = sequence::detect_sequence(samples, model, 1.0)?;
    let mlse = accepted_frames(&detected, 0.0)?;
    Ok((slicer, mlse))
}

#[derive(Default, Serialize)]
struct Totals {
    cases: usize,
    expected_frames: usize,
    slicer_correct_frames: usize,
    mlse_correct_frames: usize,
    union_correct_frames: usize,
    mlse_only_correct_frames: usize,
    slicer_only_correct_frames: usize,
    slicer_wrong_accepted_crc_ui_frames: usize,
    mlse_wrong_accepted_crc_ui_frames: usize,
}

impl Totals {
    fn add(
        &mut self,
        truth: &BTreeSet<String>,
        slicer: &BTreeSet<String>,
        mlse: &BTreeSet<String>,
    ) {
        self.cases += 1;
        self.expected_frames += truth.len();
        self.slicer_correct_frames += slicer.intersection(truth).count();
        self.mlse_correct_frames += mlse.intersection(truth).count();
        self.union_correct_frames += slicer.union(mlse).filter(|frame| truth.contains(*frame)).count();
        self.mlse_only_correct_frames += mlse.difference(slicer).filter(|frame| truth.contains(*frame)).count();
        self.slicer_only_correct_frames += slicer.difference(mlse).filter(|frame| truth.contains(*frame)).count();
        self.slicer_wrong_accepted_crc_ui_frames += slicer.difference(truth).count();
        self.mlse_wrong_accepted_crc_ui_frames += mlse.difference(truth).count();
    }
}

fn run(args: &Args) -> Result<Value, String> {
    let out = input::existing_new_dir(&args.output)?;
    let executable = input::identity(&std::env::current_exe().map_err(|error| error.to_string())?)?;
    input::write_json_new(&out.join("plan.json"), &json!({
        "schema":"sequence-transfer-synthetic-symbol-probe-v1",
        "synthetic_only":true,
        "publication_ready":false,
        "full_receiver_comparison":false,
        "satnogs_comparison":false,
        "interface":"already-timed-symbol-rate-soft-levels",
        "modulation_frontend_included":false,
        "protocol":"AX25-UI-FCS-G3RUH-NRZI",
        "seed_hex":format!("{SEED:016x}"),
        "prng":"xorshift64(13,7,17), open52-bit-unit-uniform",
        "channel_taps":TAPS,"channel_bias":BIAS,
        "channel_family_matches_detector":true,
        "anchor_packets":1,"anchor_uniform_noise_half_width":0.02,
        "signal_uniform_noise_half_widths":NOISE_GRID,
        "cases_per_group":CASES_PER_GROUP,
        "signal_cases":64,"learned_anchor_null_cases":64,
        "null_groups":["silence","gaussian-sigma-0.1","gaussian-sigma-0.5","gaussian-sigma-1.0"],
        "null_symbols_per_case":2048,
        "mlse_gains":[1.0],
        "slicer_threshold":"learned channel bias (same anchor available to both methods)",
        "training_levels":"hard sliced received anchor, native CRC/UI validated spans only",
        "target_truth_available_to_fit_or_detection":false,
        "truth_used_only_for_generation_and_post_decode_scoring":true,
        "bit_repair_enabled":false,"crc_guided_search":false,
        "executable":executable
    }))?;
    let started = Instant::now();
    let computation = (|| -> Result<Value, String> {
        let mut anchor_rng = Prng(SEED);
        let anchor_truth = frame(u32::MAX, &mut anchor_rng);
        let anchor_levels = encode_frame(&anchor_truth);
        let anchor_received = transmitted_symbols(&anchor_levels, 0.02, &mut anchor_rng);
        let validated = anchors::validated_spans(&anchor_received, 0.0)?;
        let received_levels: Vec<u8> = anchor_received.iter().map(|sample| u8::from(*sample >= 0.0)).collect();
        let spans: Vec<_> = validated.iter().map(|span| (span.start, span.end)).collect();
        let fitted = sequence::fit_channel(&anchor_received, &received_levels, &spans)?;
        // This correctness check occurs after model creation; neither true
        // physical levels nor expected frame bytes are fit inputs.
        let anchor_expected_hex = hex::encode(anchor_truth);
        let anchor_correct = validated.iter().any(|span| hex::encode(&span.frame) == anchor_expected_hex);
        if !anchor_correct {
            return Err("synthetic anchor did not independently decode to its expected frame".into());
        }
        input::write_json_new(&out.join("anchor.json"), &json!({
            "seed_hex":format!("{SEED:016x}"),
            "symbols":anchor_received.len(),
            "received_symbols_sha256":symbols_sha256(&anchor_received),
            "expected_frame_with_fcs_hex":anchor_expected_hex,
            "native_validated_spans":validated.iter().map(|span| json!({
                "start":span.start,"end":span.end,"g3ruh":span.g3ruh,
                "frame_with_fcs_hex":hex::encode(&span.frame)
            })).collect::<Vec<_>>(),
            "model":fitted,
            "model_fit_used_expected_bytes":false,
            "model_fit_used_transmitted_levels":false
        }))?;
        let mut signal_totals = Totals::default();
        let mut noise_groups = Vec::new();
        for (group_index, amplitude) in NOISE_GRID.into_iter().enumerate() {
            let mut group_totals = Totals::default();
            for local_index in 0..CASES_PER_GROUP {
                let index = group_index * CASES_PER_GROUP + local_index;
                let seed = case_seed(index);
                let mut rng = Prng(seed);
                let truth_frame = frame(index as u32, &mut rng);
                let physical_levels = encode_frame(&truth_frame);
                let received = transmitted_symbols(&physical_levels, amplitude, &mut rng);
                // Recover first, score against generated truth afterwards.
                let (slicer, mlse) = recover(&received, &fitted)?;
                let expected: BTreeSet<String> = [hex::encode(truth_frame)].into_iter().collect();
                group_totals.add(&expected, &slicer, &mlse);
                signal_totals.add(&expected, &slicer, &mlse);
                input::write_json_new(&out.join(format!("case-{index:03}-signal.json")), &json!({
                    "case":index,"kind":"signal","seed_hex":format!("{seed:016x}"),
                    "uniform_noise_half_width":amplitude,
                    "symbols":received.len(),"received_symbols_sha256":symbols_sha256(&received),
                    "expected_frame_with_fcs_hex":expected,
                    "slicer_frame_with_fcs_hex":slicer,"mlse_frame_with_fcs_hex":mlse,
                    "target_bytes_used_for_processing":false
                }))?;
            }
            noise_groups.push(json!({"uniform_noise_half_width":amplitude,"totals":group_totals}));
        }
        let mut null_totals = Totals::default();
        let mut null_groups = Vec::new();
        for (group_index, sigma) in [0.0, 0.1, 0.5, 1.0].into_iter().enumerate() {
            let mut group_totals = Totals::default();
            for local_index in 0..CASES_PER_GROUP {
                let index = 64 + group_index * CASES_PER_GROUP + local_index;
                let seed = case_seed(index);
                let mut rng = Prng(seed);
                let received: Vec<f64> = (0..2048).map(|_| sigma * rng.gaussian()).collect();
                // A real learned anchor is intentionally supplied even though
                // these targets contain no transmission. This exercises the
                // supplemental detector, not merely a no-anchor bypass.
                let (slicer, mlse) = recover(&received, &fitted)?;
                let expected = BTreeSet::new();
                group_totals.add(&expected, &slicer, &mlse);
                null_totals.add(&expected, &slicer, &mlse);
                input::write_json_new(&out.join(format!("case-{index:03}-null.json")), &json!({
                    "case":index,"kind":"null","seed_hex":format!("{seed:016x}"),
                    "gaussian_sigma":sigma,"symbols":received.len(),
                    "received_symbols_sha256":symbols_sha256(&received),
                    "expected_frame_with_fcs_hex":expected,
                    "slicer_frame_with_fcs_hex":slicer,"mlse_frame_with_fcs_hex":mlse,
                    "learned_anchor_model_was_used":true
                }))?;
            }
            null_groups.push(json!({"gaussian_sigma":sigma,"totals":group_totals}));
        }
        Ok(json!({
            "schema":"sequence-transfer-synthetic-symbol-probe-result-v1",
            "status":"complete","synthetic_only":true,"publication_ready":false,
            "satnogs_comparison":false,"full_receiver_comparison":false,
            "model":fitted,"signal_totals":signal_totals,"null_totals":null_totals,
            "signal_noise_groups":noise_groups,"null_groups":null_groups,
            "elapsed_seconds":started.elapsed().as_secs_f64(),
            "limitations":[
                "Known symbol timing and a stationary synthetic three-tap channel matching the detector family.",
                "No OGG/Vorbis, RF propagation, carrier estimation, IQ frontend, or clock drift tested here.",
                "One fixed seed family and small null corpus do not establish a field false-positive rate.",
                "MLSE is an existing algorithm; this is a correctness/feasibility experiment, not a novelty claim."
            ]
        }))
    })();
    match computation {
        Ok(result) => {
            input::write_json_new(&out.join("result.json"), &result)?;
            Ok(result)
        }
        Err(error) => {
            input::write_json_new(&out.join("result.json"), &json!({
                "status":"error","error":error,"synthetic_only":true,"publication_ready":false
            }))?;
            Err(error)
        }
    }
}

fn main() {
    match run(&Args::parse()) {
        Ok(result) => println!("{}", json!({
            "status":result["status"],
            "signal_totals":result["signal_totals"],
            "null_totals":result["null_totals"],
            "synthetic_only":true,"publication_ready":false
        })),
        Err(error) => {
            eprintln!("{error}");
            std::process::exit(1);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn synthetic_encoding_roundtrips_strict_g3ruh_native_decoder() {
        let mut rng = Prng(SEED);
        for index in 0..8 {
            let truth = frame(index, &mut rng);
            let levels = encode_frame(&truth);
            let soft: Vec<f64> = levels.iter().map(|level| 2.0 * f64::from(*level) - 1.0).collect();
            let frames = accepted_frames(&soft, 0.0).unwrap();
            assert_eq!(frames, [hex::encode(&truth)].into_iter().collect());
            let spans = anchors::validated_spans(&soft, 0.0).unwrap();
            assert!(spans.iter().any(|span| span.frame == truth && span.g3ruh));
        }
    }

    #[test]
    fn learned_anchor_and_processing_are_independent_of_target_truth() {
        let mut rng = Prng(SEED);
        let anchor = frame(u32::MAX, &mut rng);
        let received = transmitted_symbols(&encode_frame(&anchor), 0.02, &mut rng);
        let spans = anchors::validated_spans(&received, 0.0).unwrap();
        let hard: Vec<u8> = received.iter().map(|value| u8::from(*value >= 0.0)).collect();
        let ranges: Vec<_> = spans.iter().map(|span| (span.start, span.end)).collect();
        let fitted = sequence::fit_channel(&received, &hard, &ranges).unwrap();
        let target = frame(0, &mut rng);
        assert_ne!(target, anchor);
        let target_received = transmitted_symbols(&encode_frame(&target), 0.2, &mut rng);
        let first = recover(&target_received, &fitted).unwrap();
        let second = recover(&target_received, &fitted).unwrap();
        assert_eq!(first, second);
        assert!(first.1.contains(&hex::encode(target)));
    }
}
