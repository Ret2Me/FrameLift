//! Fixed synthetic smoke test of the complete PCM-to-adaptive-receiver path.
//!
//! Four 18-second recordings contain a common strong AX.25/G3RUH packet near
//! one second and a different packet or null signal near eleven seconds. No
//! transmitted levels, payload bytes, or signal parameters enter the receiver.
//! This is neither a SatNOGS benchmark nor a calibrated false-alarm experiment.
//! Zero supplemental gain is a valid result; safety and actual transfer-path
//! coverage are reported separately from recovery effectiveness.
//!
//! The default linear-ISI renderer is preserved after its first smoke run
//! produced no baseline frames and therefore exercised no learned transfer.
//! `--rendering clean-rectangular` is a separate v2 coverage repair: ordinary
//! sample-and-hold levels, following the existing positive PCM fixture. It is
//! not a receiver change or evidence of improved receiver effectiveness.

use clap::Parser;
use serde_json::{Value, json};
use std::{collections::BTreeSet, path::PathBuf, time::Instant};
use telemetry_yield_rs::{adaptive, input, protocol};

const SAMPLE_RATE: u32 = 48_000;
const BAUD: f64 = 9_600.0;
const SECONDS: usize = 18;
const SAMPLES_PER_SYMBOL: f64 = 5.0;
const SEED: u64 = 0x4370_9600_0910_5043;
const TAPS: [f64; 3] = [0.35, 0.8, 0.35];
const BIAS: f64 = 0.04;
const FLAG: [u8; 8] = [0, 1, 1, 1, 1, 1, 1, 0];
const ANCHOR_START_SECONDS: f64 = 0.25;
const TARGET_START_SECONDS: f64 = 10.25;
const PREAMBLE_FLAGS: usize = 900;

#[derive(Clone, Copy, Debug, PartialEq, Eq, clap::ValueEnum)]
enum Rendering {
    LinearIsi,
    CleanRectangular,
}

impl Rendering {
    fn name(self) -> &'static str {
        match self {
            Self::LinearIsi => "linear-isi",
            Self::CleanRectangular => "clean-rectangular",
        }
    }

    fn taps(self) -> [f64; 3] {
        match self {
            Self::LinearIsi => TAPS,
            Self::CleanRectangular => [0.0, 1.0, 0.0],
        }
    }

    fn schema_version(self) -> &'static str {
        match self {
            Self::LinearIsi => "v1",
            Self::CleanRectangular => "v2",
        }
    }
}

#[derive(Parser)]
struct Args {
    #[arg(long)]
    output: PathBuf,
    #[arg(long, default_value_t = 2)]
    threads: usize,
    /// Original difficult waveform, or a separately labeled coverage fixture.
    #[arg(long, value_enum, default_value = "linear-isi")]
    rendering: Rendering,
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
        ((self.next() >> 12) as f64 + 0.5) / 4_503_599_627_370_496.0
    }

    fn gaussian(&mut self) -> f64 {
        (-2.0 * self.uniform().ln()).sqrt()
            * (std::f64::consts::TAU * self.uniform()).cos()
    }
}

fn frame(id: u32, rng: &mut Prng) -> Vec<u8> {
    let mut bytes = hex::decode("94a662b2a0826094a662b29eb2e103f0").expect("fixed valid header");
    bytes.extend_from_slice(b"PCM-TRANSFER-V1");
    bytes.extend_from_slice(&id.to_le_bytes());
    bytes.extend((0..64).map(|_| rng.next() as u8));
    bytes.extend_from_slice(&protocol::crc16_x25(&bytes).to_le_bytes());
    bytes
}

fn encode(frame: &[u8]) -> Vec<f64> {
    let mut bits = FLAG.repeat(PREAMBLE_FLAGS);
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
    bits.extend(FLAG.repeat(PREAMBLE_FLAGS));
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
    let mut level = 0_u8;
    scrambled
        .into_iter()
        .map(|bit| {
            if bit == 0 {
                level ^= 1;
            }
            2.0 * f64::from(level) - 1.0
        })
        .collect()
}

#[derive(Clone, Copy)]
struct Render {
    rendering: Rendering,
    gain: f64,
    dc: f64,
    phase_samples: f64,
    noise_sigma: f64,
}

fn add_packet(
    pcm: &mut [f64],
    levels: &[f64],
    start_seconds: f64,
    settings: Render,
    rng: &mut Prng,
) {
    let taps = settings.rendering.taps();
    let symbols: Vec<f64> = (0..levels.len())
        .map(|index| {
            BIAS
                + taps[0] * levels[index.saturating_sub(1)]
                + taps[1] * levels[index]
                + taps[2] * levels[(index + 1).min(levels.len() - 1)]
        })
        .collect();
    let start = (start_seconds * f64::from(SAMPLE_RATE)) as usize;
    let length = (symbols.len() as f64 * SAMPLES_PER_SYMBOL).ceil() as usize;
    for (relative, sample) in pcm.iter_mut().skip(start).take(length).enumerate() {
        let position = ((relative as f64 - settings.phase_samples) / SAMPLES_PER_SYMBOL)
            .clamp(0.0, (symbols.len() - 1) as f64);
        let index = position.floor() as usize;
        let rendered = match settings.rendering {
            Rendering::LinearIsi => {
                let fraction = position - index as f64;
                let right = symbols[(index + 1).min(symbols.len() - 1)];
                symbols[index] * (1.0 - fraction) + right * fraction
            }
            // Same basic physical-level hold used by positive_samples() in
            // rust/tests/cli_integration.rs: five PCM samples per symbol.
            Rendering::CleanRectangular => symbols[index],
        };
        *sample += settings.gain * rendered + settings.dc + settings.noise_sigma * rng.gaussian();
    }
}

fn add_null(pcm: &mut [f64], colored_impulsive: bool, rng: &mut Prng) {
    let start = (TARGET_START_SECONDS * f64::from(SAMPLE_RATE)) as usize;
    let length = (1.75 * f64::from(SAMPLE_RATE)) as usize;
    let mut colored = 0.0;
    for sample in pcm.iter_mut().skip(start).take(length) {
        if colored_impulsive {
            colored = 0.98 * colored + (1.0_f64 - 0.98_f64.powi(2)).sqrt() * rng.gaussian();
            *sample += 0.25 * colored + 0.04 * rng.gaussian();
            if rng.next() & 4095 == 0 {
                *sample += if rng.next() & 1 == 0 { -5.0 } else { 5.0 };
            }
        } else {
            *sample += 0.35 * rng.gaussian();
        }
    }
}

fn write_exact_float_wav(path: &std::path::Path, pcm: &mut [f64]) -> Result<(), String> {
    let spec = hound::WavSpec {
        channels: 1,
        sample_rate: SAMPLE_RATE,
        bits_per_sample: 32,
        sample_format: hound::SampleFormat::Float,
    };
    let mut writer = hound::WavWriter::create(path, spec).map_err(|error| error.to_string())?;
    for value in pcm {
        // Decode exactly the same numeric samples as the saved WAV represents.
        let sample = *value as f32;
        *value = f64::from(sample);
        writer.write_sample(sample).map_err(|error| error.to_string())?;
    }
    writer.finalize().map_err(|error| error.to_string())
}

fn separated_transfers(report: &adaptive::AdaptiveReport) -> bool {
    report.supplemental_trials.iter().all(|trial| {
        report.anchor_models.iter().find(|anchor| anchor.id == trial.anchor_id).is_some_and(|anchor| {
            let target_start = trial.window_start_seconds;
            let target_end = (target_start + 6.0).min(SECONDS as f64);
            anchor.window_end_seconds + 1.0 <= target_start
                || target_end + 1.0 <= anchor.window_start_seconds
        })
    })
}

fn run(args: &Args) -> Result<Value, String> {
    if !(1..=16).contains(&args.threads) {
        return Err("threads must be in 1..=16".into());
    }
    let out = input::existing_new_dir(&args.output)?;
    let config = adaptive::AdaptiveConfig { baud: BAUD, threads: args.threads };
    let executable = input::identity(&std::env::current_exe().map_err(|error| error.to_string())?)?;
    let source = input::identity(&PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("examples/adaptive_pcm_probe.rs"))?;
    input::write_json_new(&out.join("plan.json"), &json!({
        "schema":format!("adaptive-pcm-synthetic-smoke-{}",args.rendering.schema_version()),
        "synthetic_only":true,"publication_ready":false,"satnogs_comparison":false,
        "false_alarm_rate_certification":false,
        "input_representation":"mono-post-FM-PCM-float32",
        "full_pcm_frontend_and_timing_recovery_exercised":true,
        "sample_rate":SAMPLE_RATE,"baud":BAUD,"duration_seconds":SECONDS,
        "seed_hex":format!("{SEED:016x}"),
        "prng":"xorshift64(13,7,17), open52-bit uniform, Box-Muller Gaussian",
        "rendering":args.rendering.name(),
        "render":match args.rendering {
            Rendering::LinearIsi => "centered three-tap physical-level ISI, linear interpolation at five samples/symbol",
            Rendering::CleanRectangular => "zero-ISI physical levels, sample-and-hold at five samples/symbol"
        },
        "channel_taps":args.rendering.taps(),"channel_bias":BIAS,
        "coverage_repair_reason":if args.rendering == Rendering::CleanRectangular {
            Some("The preserved linear-ISI v1 smoke run decoded no baseline frames and produced no anchors or supplemental trials. This separately labeled ordinary rectangular fixture repairs missing pipeline coverage, not receiver effectiveness.")
        } else { None },
        "positive_pcm_rendering_reference":"rust/tests/cli_integration.rs::positive_samples (independent protocol_oracle levels repeated five times at 48 kHz)",
        "prior_failed_smoke_artifact":"work/adaptive-sequence-20260910-v1/pcm-probe/result.json",
        "receiver_or_model_changed_for_coverage_repair":false,
        "preamble_and_postamble_flags_each":PREAMBLE_FLAGS,
        "anchor_start_seconds":ANCHOR_START_SECONDS,
        "target_start_seconds":TARGET_START_SECONDS,
        "approximate_payload_seconds":[1.0,11.0],
        "background_gaussian_sigma":0.003,
        "anchor":{"gain":1.0,"dc":0.0,"phase_samples":0.25,"gaussian_sigma":0.005},
        "cases":[
            {"id":"weak-distinct-packet","target":{"gain":0.75,"dc":0.0,"phase_samples":0.25,"gaussian_sigma":0.2}},
            {"id":"gaussian-only-target","target":{"gaussian_sigma":0.35,"duration_seconds":1.75}},
            {"id":"colored-impulsive-null-target","target":{"ar1_coefficient":0.98,"colored_sigma":0.25,"white_sigma":0.04,"impulse_amplitude":5.0,"impulse_probability":0.000244140625,"duration_seconds":1.75}},
            {"id":"gain-dc-phase-distinct-packet","target":{"gain":0.65,"dc":0.12,"phase_samples":1.6,"gaussian_sigma":0.12}}
        ],
        "settings":config,"executable":executable,"source":source,
        "receiver_given_truth_or_channel_parameters":false,
        "truth_used_for_generation_and_post_decode_scoring_only":true,
        "superiority_asserted":false,
        "safety_requirements":["no wrong accepted CRC/UI frames","baseline frames retained in union","all supplemental anchor windows are disjoint with one-second guard"],
        "coverage_requirement":"each case has a strong-packet anchor and a supplemental trial covering the target region near eleven seconds",
        "coverage_failure_is_fatal":args.rendering == Rendering::CleanRectangular,
        "limitations":["Four fixed synthetic cases are smoke coverage, not broad generalization or FAR certification.","No OGG codec, coherent IQ, or measured RF channel is exercised."]
    }))?;
    let started = Instant::now();
    let computation = (|| -> Result<Value, String> {
        let mut anchor_rng = Prng(SEED);
        let anchor_truth = frame(u32::MAX, &mut anchor_rng);
        let anchor_levels = encode(&anchor_truth);
        let anchor_hex = hex::encode(&anchor_truth);
        let names = ["weak-distinct-packet", "gaussian-only-target", "colored-impulsive-null-target", "gain-dc-phase-distinct-packet"];
        let mut cases = Vec::new();
        let mut all_safe = true;
        let mut all_exercised = true;
        for (case_index, name) in names.into_iter().enumerate() {
            let seed = SEED ^ (case_index as u64 + 1).wrapping_mul(0x9e37_79b9_7f4a_7c15);
            let mut rng = Prng(seed);
            let mut pcm: Vec<f64> = (0..SECONDS * SAMPLE_RATE as usize).map(|_| 0.003 * rng.gaussian()).collect();
            let target_truth = if case_index == 0 || case_index == 3 {
                Some(frame(case_index as u32, &mut rng))
            } else {
                None
            };
            let mut anchor_noise_rng = Prng(SEED ^ 0x414e_4348_4f52);
            add_packet(&mut pcm, &anchor_levels, ANCHOR_START_SECONDS, Render {
                rendering: args.rendering,
                gain: 1.0, dc: 0.0, phase_samples: 0.25, noise_sigma: 0.005
            }, &mut anchor_noise_rng);
            if let Some(target) = &target_truth {
                let settings = if case_index == 0 {
                    Render { rendering: args.rendering, gain: 0.75, dc: 0.0, phase_samples: 0.25, noise_sigma: 0.2 }
                } else {
                    Render { rendering: args.rendering, gain: 0.65, dc: 0.12, phase_samples: 1.6, noise_sigma: 0.12 }
                };
                add_packet(&mut pcm, &encode(target), TARGET_START_SECONDS, settings, &mut rng);
            } else {
                add_null(&mut pcm, case_index == 2, &mut rng);
            }
            let case_out = input::existing_new_dir(&out.join(format!("case-{case_index:02}-{name}")))?;
            let wav_path = case_out.join("input.wav");
            write_exact_float_wav(&wav_path, &mut pcm)?;
            let wav_identity = input::identity(&wav_path)?;
            input::write_json_new(&case_out.join("input.json"), &json!({
                "case":name,"seed_hex":format!("{seed:016x}"),"input":wav_identity,
                "rendering":args.rendering.name(),"channel_taps":args.rendering.taps(),
                "settings":config,"transmitted_payloads_supplied_to_receiver":false
            }))?;
            // This call receives only PCM, sample rate and ordinary receiver
            // settings; reference frame bytes are not reachable through its API.
            let report = adaptive::decode_samples(&pcm, SAMPLE_RATE, &config)?;
            // Construct reference sets and evaluate only after decoding ends.
            let mut expected: BTreeSet<String> = [anchor_hex.clone()].into_iter().collect();
            let target_hex = target_truth.as_ref().map(hex::encode);
            if let Some(target) = &target_hex {
                expected.insert(target.clone());
            }
            let wrong_baseline: BTreeSet<_> = report.baseline_full_frames.difference(&expected).cloned().collect();
            let wrong_supplemental: BTreeSet<_> = report.supplemental_full_frames.difference(&expected).cloned().collect();
            let wrong_union: BTreeSet<_> = report.union_full_frames.difference(&expected).cloned().collect();
            let baseline_loss: BTreeSet<_> = report.baseline_full_frames.difference(&report.union_full_frames).cloned().collect();
            let transfer_separation_passed = separated_transfers(&report);
            let safe = wrong_baseline.is_empty() && wrong_supplemental.is_empty()
                && wrong_union.is_empty() && baseline_loss.is_empty()
                && report.lost_vs_baseline.is_empty() && transfer_separation_passed;
            let has_strong_packet_anchor = report.anchor_models.iter().any(|anchor| {
                anchor.window_start_seconds <= 1.0 && anchor.window_end_seconds > 1.0
            });
            let target_region_trials = report.supplemental_trials.iter().filter(|trial| {
                trial.window_start_seconds <= 11.0 && trial.window_start_seconds + 6.0 > 11.0
            }).count();
            let exercised = has_strong_packet_anchor && target_region_trials > 0;
            all_safe &= safe;
            all_exercised &= exercised;
            let score = json!({
                "case":name,"rendering":args.rendering.name(),"expected_frame_with_fcs_hex":expected,
                "baseline_correct_frames":report.baseline_full_frames.intersection(&expected).count(),
                "supplemental_correct_frames":report.supplemental_full_frames.intersection(&expected).count(),
                "union_correct_frames":report.union_full_frames.intersection(&expected).count(),
                "additional_correct_frames":report.added_vs_baseline.intersection(&expected).count(),
                "baseline_wrong_accepted_crc_ui_frames":wrong_baseline,
                "supplemental_wrong_accepted_crc_ui_frames":wrong_supplemental,
                "union_wrong_accepted_crc_ui_frames":wrong_union,
                "lost_baseline_frames":baseline_loss,
                "anchor_correctly_recovered":report.baseline_full_frames.contains(&anchor_hex),
                "target_exists":target_hex.is_some(),
                "target_baseline_recovered":target_hex.as_ref().is_some_and(|target| report.baseline_full_frames.contains(target)),
                "target_supplemental_recovered":target_hex.as_ref().is_some_and(|target| report.supplemental_full_frames.contains(target)),
                "anchor_models":report.anchor_models.len(),
                "strong_packet_anchor_available":has_strong_packet_anchor,
                "supplemental_trials":report.supplemental_trials.len(),
                "target_region_supplemental_trials":target_region_trials,
                "supplemental_skips":report.supplemental_skips.len(),
                "anchor_target_window_separation_passed":transfer_separation_passed,
                "safety_checks_passed":safe,
                "anchor_and_transfer_path_exercised":exercised,
                "stage_wall_seconds":report.stage_wall_seconds
            });
            input::write_json_new(&case_out.join("receiver.json"), &report)?;
            input::write_json_new(&case_out.join("score.json"), &score)?;
            cases.push(score);
        }
        Ok(json!({
            "schema":format!("adaptive-pcm-synthetic-smoke-result-{}",args.rendering.schema_version()),"status":"complete",
            "rendering":args.rendering.name(),"channel_taps":args.rendering.taps(),
            "receiver_or_model_changed_for_coverage_repair":false,
            "synthetic_only":true,"publication_ready":false,"satnogs_comparison":false,
            "safety_checks_passed":all_safe,"all_cases_exercised_anchor_and_transfer":all_exercised,
            "smoke_checks_passed":all_safe && all_exercised,
            "superiority_asserted":false,"cases":cases,
            "elapsed_seconds":started.elapsed().as_secs_f64()
        }))
    })();
    match computation {
        Ok(result) => {
            input::write_json_new(&out.join("result.json"), &result)?;
            // Persist all evidence before failing a safety assertion. Failure
            // to exercise transfer is separately visible as incomplete coverage.
            if result["safety_checks_passed"] != true {
                return Err("synthetic PCM safety checks failed; inspect persisted result.json".into());
            }
            if args.rendering == Rendering::CleanRectangular
                && result["all_cases_exercised_anchor_and_transfer"] != true
            {
                return Err("clean-rectangular PCM smoke did not exercise required transfer coverage; inspect persisted result.json".into());
            }
            Ok(result)
        }
        Err(error) => {
            input::write_json_new(&out.join("result.json"), &json!({
                "status":"error","error":error,"synthetic_only":true,"publication_ready":false,
                "rendering":args.rendering.name()
            }))?;
            Err(error)
        }
    }
}

fn main() {
    match run(&Args::parse()) {
        Ok(result) => println!("{}", json!({
            "status":result["status"],"safety_checks_passed":result["safety_checks_passed"],
            "rendering":result["rendering"],
            "all_cases_exercised_anchor_and_transfer":result["all_cases_exercised_anchor_and_transfer"],
            "cases":result["cases"],"synthetic_only":true,"publication_ready":false
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
    fn encoded_packets_roundtrip_before_pcm_rendering() {
        let mut rng = Prng(SEED);
        for id in 0..4 {
            let expected = frame(id, &mut rng);
            let recovered = protocol::decode_ax25(&encode(&expected), 0.0, &[false, true]).unwrap();
            assert_eq!(recovered, vec![expected]);
        }
    }

    #[test]
    fn render_bounds_and_symbol_rate_are_fixed() {
        let mut rng = Prng(SEED);
        let levels = encode(&frame(0, &mut rng));
        let duration = levels.len() as f64 / BAUD;
        assert!((1.5..1.7).contains(&duration));
        let mut pcm = vec![0.0; SECONDS * SAMPLE_RATE as usize];
        add_packet(&mut pcm, &levels, TARGET_START_SECONDS, Render {
            rendering: Rendering::LinearIsi,
            gain: 0.65, dc: 0.12, phase_samples: 1.6, noise_sigma: 0.12
        }, &mut rng);
        assert!(pcm[..10 * SAMPLE_RATE as usize].iter().all(|value| *value == 0.0));
        assert!(pcm[13 * SAMPLE_RATE as usize..].iter().all(|value| *value == 0.0));
        assert!(pcm.iter().all(|value| value.is_finite()));
        assert_eq!(SAMPLE_RATE as f64 / BAUD, SAMPLES_PER_SYMBOL);
    }

    #[test]
    fn rectangular_renderer_matches_existing_independent_positive_pcm_fixture() {
        let oracle: Value = serde_json::from_str(include_str!("../rust/tests/protocol_oracle.json")).unwrap();
        let case = &oracle["cases"][0];
        let packed = hex::decode(case["levels_hex"].as_str().unwrap()).unwrap();
        let count = case["symbol_count"].as_u64().unwrap() as usize;
        let levels: Vec<f64> = (0..count).map(|index| {
            if (packed[index / 8] >> (7 - index % 8)) & 1 == 1 { 1.0 } else { -1.0 }
        }).collect();
        let expected: Vec<f64> = levels.iter().flat_map(|level| std::iter::repeat_n(0.75 * level, 5)).collect();
        let mut actual = vec![0.0; expected.len()];
        add_packet(&mut actual, &levels, 0.0, Render {
            rendering: Rendering::CleanRectangular,
            gain: 0.75, dc: -0.75 * BIAS, phase_samples: 0.0, noise_sigma: 0.0
        }, &mut Prng(SEED));
        assert_eq!(actual.len(), expected.len());
        assert!(actual.iter().zip(expected).all(|(actual, expected)| (actual - expected).abs() < 1.0e-12));
        assert_eq!(Rendering::CleanRectangular.taps(), [0.0, 1.0, 0.0]);
    }

    #[test]
    fn original_renderer_remains_default_and_rectangular_is_explicit() {
        let default_args = Args::try_parse_from(["probe", "--output", "unused"]).unwrap();
        assert_eq!(default_args.rendering, Rendering::LinearIsi);
        assert_eq!(default_args.rendering.schema_version(), "v1");
        let rectangular = Args::try_parse_from([
            "probe", "--output", "unused", "--rendering", "clean-rectangular"
        ]).unwrap();
        assert_eq!(rectangular.rendering, Rendering::CleanRectangular);
        assert_eq!(rectangular.rendering.schema_version(), "v2");
        assert!(Args::try_parse_from([
            "probe", "--output", "unused", "--rendering", "unknown"
        ]).is_err());
    }
}
