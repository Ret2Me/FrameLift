//! Development-only, fixed short-window timing bank. No reference packet input.
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

fn normalized(samples: &[f64]) -> Result<Option<Vec<f64>>, String> {
    if samples.is_empty() || samples.iter().any(|x| !x.is_finite()) {
        return Err("finite nonempty samples required".into());
    }
    let peak = samples.iter().map(|x| x.abs()).fold(0.0, f64::max);
    if peak == 0.0 {
        return Ok(None);
    }
    // Scaling first avoids energy overflow; there is no amplitude cutoff that
    // could silently discard a weak but finite waveform.
    let scaled: Vec<_> = samples.iter().map(|x| x / peak).collect();
    let mean = scaled.iter().sum::<f64>() / scaled.len() as f64;
    let centered: Vec<_> = scaled.iter().map(|x| x - mean).collect();
    let rms = (centered.iter().map(|x| x * x).sum::<f64>() / centered.len() as f64).sqrt();
    if !rms.is_finite() {
        return Err("normalization arithmetic failure".into());
    }
    if rms == 0.0 {
        return Ok(None);
    }
    let result: Vec<_> = centered.iter().map(|x| x / rms).collect();
    if result.iter().any(|x| !x.is_finite()) {
        return Err("normalization produced nonfinite samples".into());
    }
    Ok(Some(result))
}

fn windows(length: usize, rate: usize) -> Result<Vec<(usize, usize)>, String> {
    if rate == 0 || rate > 1_000_000 || length < 8 {
        return Err("invalid window layout".into());
    }
    let width = rate.checked_mul(6).ok_or("window size overflow")?;
    let hop = rate.checked_mul(3).ok_or("hop size overflow")?;
    if width < 8 {
        return Err("window must contain at least eight samples".into());
    }
    Ok((0..length)
        .step_by(hop)
        .filter_map(|start| {
            let end = start.saturating_add(width).min(length);
            (end - start >= 8).then_some((start, end))
        })
        .collect())
}

fn run(a: Args) -> Result<(), String> {
    if !a.baud.is_finite() || a.baud <= 0.0 {
        return Err("positive finite baud required".into());
    }
    let began = Instant::now();
    let executable = input::identity(&std::env::current_exe().map_err(|e| e.to_string())?)?;
    let root = input::existing_new_dir(&a.output)?;
    let audio = input::load_audio(&a.input, &root)?;
    let sps = audio.sample_rate as f64 / a.baud;
    if !(1.1..=64.0).contains(&sps) {
        return Err("unsupported sample-rate/baud ratio".into());
    }
    let layout = windows(audio.samples.len(), audio.sample_rate as usize)?;
    let decode_began = Instant::now();
    let mut union = BTreeSet::new();
    let mut rows = Vec::new();
    let mut complete = true;
    for (start, end) in &layout {
        let raw = &audio.samples[*start..*end];
        let scaled = normalized(raw)?;
        for (frontend, data) in [("raw", Some(raw)), ("dc_rms", scaled.as_deref())] {
            let Some(data) = data else {
                rows.push(json!({"start_sample":start,"end_sample":end,"frontend":frontend,"status":"complete_constant_input","frame_with_fcs_hex":[]}));
                continue;
            };
            for interpolation in [
                mm_clock::Interpolation::Linear,
                mm_clock::Interpolation::Cubic,
            ] {
                for phase in [0.0, 0.25, 0.5, 0.75] {
                    for gain in [0.5, 1.0, 2.0] {
                        let config = mm_clock::Config {
                            samples_per_symbol: sps,
                            initial_phase: phase,
                            gain_omega: std::f64::consts::TAU / 100.0,
                            gain_mu: 0.0625,
                            relative_limit: 0.01,
                            input_gain: gain,
                            interpolation,
                        };
                        let result = (|| -> Result<_, String> {
                            let symbols = mm_clock::recover(data, config)?;
                            let frames = protocol::decode_ax25(&symbols, 0.0, &[false, true])?;
                            let mut set = BTreeSet::new();
                            for frame in frames {
                                if frame.len() < 2
                                    || !protocol::valid_ax25_fcs(&frame)
                                    || !protocol::valid_ax25_ui(&frame[..frame.len() - 2])
                                {
                                    return Err("invalid accepted frame".into());
                                }
                                set.insert(hex::encode(frame));
                            }
                            Ok(set)
                        })();
                        let mut row = json!({"start_sample":start,"end_sample":end,"frontend":frontend,"interpolation":format!("{interpolation:?}"),"phase":phase,"input_gain":gain});
                        match result {
                            Ok(frames) => {
                                row["status"] = "complete".into();
                                row["frame_with_fcs_hex"] = json!(frames);
                                union.extend(frames);
                            }
                            Err(error) => {
                                complete = false;
                                row["status"] = "failed".into();
                                row["error"] = error.into();
                            }
                        }
                        rows.push(row);
                    }
                }
            }
        }
    }
    if input::identity(&a.input)?.sha256 != audio.source_identity.sha256 {
        return Err("input changed during experiment".into());
    }
    let result = json!({
        "schema":"windowed-mm-development-probe-v1","source":audio.source_identity,
        "prepared":audio.wav_identity,"conversion_command":audio.conversion_command,
        "executable":executable,"sample_rate_hz":audio.sample_rate,"baud":a.baud,
        "configuration":{"window_seconds":6,"hop_seconds":3,"frontends":["raw","dc_rms"],"interpolations":["Linear","Cubic"],"initial_phases":[0.0,0.25,0.5,0.75],"input_gains":[0.5,1.0,2.0],"gain_omega":std::f64::consts::TAU/100.0,"gain_mu":0.0625,"relative_limit":0.01,"threshold":0.0,"g3ruh_modes":[false,true]},
        "window_count":layout.len(),"branches":rows,"union_count":union.len(),
        "frame_with_fcs_hex":union,"status":if complete {"complete"} else {"incomplete"},
        "decode_wall_seconds":decode_began.elapsed().as_secs_f64(),"total_wall_seconds":began.elapsed().as_secs_f64(),
        "reference_bytes_used_for_search":false,"novel_algorithm":false,
        "publication_ready":false,"deployment_ready":false,
        "scope":"Fixed development bank; short-window reset and local DC/RMS conditioning. No IQ frontend, equal-budget gain claim, bit repair, or population generalization."
    });
    input::write_json_new(&root.join("result.json"), &result)?;
    println!(
        "{}",
        json!({"output":root,"status":result["status"],"union_count":result["union_count"]})
    );
    if complete {
        Ok(())
    } else {
        Err("failed branches retained".into())
    }
}
fn main() {
    if let Err(error) = run(Args::parse()) {
        eprintln!("{error}");
        std::process::exit(1);
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn normalized_shape_is_affine_invariant() {
        let original = [-1.0, 0.0, 1.0, 0.5];
        let changed: Vec<_> = original.iter().map(|x| 2.0 * x + 10.0).collect();
        for (a, b) in normalized(&original)
            .unwrap()
            .unwrap()
            .iter()
            .zip(normalized(&changed).unwrap().unwrap())
        {
            assert!((a - b).abs() < 1e-12);
        }
    }
    #[test]
    fn constant_and_invalid_are_different() {
        assert!(normalized(&[0.0; 20]).unwrap().is_none());
        assert!(normalized(&[2.0; 20]).unwrap().is_none());
        assert!(normalized(&[f64::NAN]).is_err());
        assert!(normalized(&[]).is_err());
    }
    #[test]
    fn tiny_and_huge_finite_bursts_are_not_discarded() {
        for amplitude in [1e-300, 1e300] {
            let mut samples = vec![0.0; 1000];
            samples[99] = amplitude;
            let result = normalized(&samples).unwrap().unwrap();
            assert!(result.iter().all(|x| x.is_finite()));
            assert!(result[99] > 30.0);
        }
    }
    #[test]
    fn overlapping_windows_cover_input_without_padding() {
        assert_eq!(
            windows(100, 10).unwrap(),
            vec![(0, 60), (30, 90), (60, 100), (90, 100)]
        );
        assert!(windows(100, 0).is_err());
        assert!(windows(7, 10).is_err());
        assert!(windows(8, 1).is_err());
    }
}
