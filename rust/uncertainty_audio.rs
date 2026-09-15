//! Isolated CPU pilot, retaining the existing adaptive + innovations receiver.
//!
//! Reuses its exact source anchors, nearest-source selection, clock/gain bank,
//! and native received-FCS/UI checks. New outcomes never become anchors. This
//! is not the full progressive portfolio, a GPU backend, or a frozen field test.

use crate::{
    adaptive, anchors, dsp, innovation_audio, receiver, robust_sequence, sequence, uncertainty,
};
use rayon::prelude::*;
use serde::Serialize;
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::{
    collections::{BTreeMap, BTreeSet},
    time::Instant,
};

pub const STRENGTHS: [f64; 2] = [1.0, 4.0];
const FRONTENDS: [&str; 2] = ["legacy-fir512", "boxcar32-rms50"];
const GAINS: [f64; 3] = [0.75, 1.0, 1.5];

#[derive(Clone, Copy)]
enum ReplayMetric {
    Point,
    Uncertainty(f64),
    Huber,
    StudentT,
}

impl ReplayMetric {
    fn name(self) -> String {
        match self {
            Self::Point => "matched-point".into(),
            Self::Uncertainty(strength) => format!("uncertainty-{strength}"),
            Self::Huber => "huber-2".into(),
            Self::StudentT => "student-t-3".into(),
        }
    }
}

const UNCERTAINTY_BANK: [ReplayMetric; 3] = [
    ReplayMetric::Point,
    ReplayMetric::Uncertainty(STRENGTHS[0]),
    ReplayMetric::Uncertainty(STRENGTHS[1]),
];
const ROBUST_BANK: [ReplayMetric; 3] = [
    ReplayMetric::Point,
    ReplayMetric::Huber,
    ReplayMetric::StudentT,
];

#[derive(Serialize)]
pub struct Source {
    pub anchor_id: String,
    pub fit: uncertainty::UncertainChannel,
}

#[derive(Serialize)]
pub struct Trial {
    pub window_index: usize,
    pub window_start_seconds: f64,
    pub window_end_seconds: f64,
    pub frontend: String,
    pub anchor_id: String,
    pub timing_rank: usize,
    pub timing: dsp::TimingHypothesis,
    pub gain: f64,
    pub lane: String,
    pub elapsed_seconds: f64,
    pub frame_with_fcs_hex: BTreeSet<String>,
    pub decisions_different_from_point: usize,
}

#[derive(Serialize)]
pub struct Report {
    pub reference: innovation_audio::InnovationReport,
    pub sources: Vec<Source>,
    pub source_rejections: Vec<Value>,
    pub trials: Vec<Trial>,
    pub skips: Vec<Value>,
    pub lane_full_frames: BTreeMap<String, BTreeSet<String>>,
    pub added_vs_matched_point: BTreeMap<String, BTreeSet<String>>,
    pub lost_vs_matched_point: BTreeMap<String, BTreeSet<String>>,
    pub added_vs_reference: BTreeMap<String, BTreeSet<String>>,
    pub union_full_frames: BTreeSet<String>,
    pub added_union_vs_reference: BTreeSet<String>,
    pub lost_union_vs_reference: BTreeSet<String>,
    pub source_preparation_seconds: f64,
    pub paired_replay_seconds: f64,
    pub total_decode_seconds: f64,
}

fn same_channel(a: &sequence::ChannelModel, b: &sequence::ChannelModel) -> bool {
    a.taps
        .iter()
        .zip(b.taps)
        .all(|(x, y)| x.to_bits() == y.to_bits())
        && a.bias.to_bits() == b.bias.to_bits()
        && a.training_symbols == b.training_symbols
        && a.normalized_mse.to_bits() == b.normalized_mse.to_bits()
}

fn source_fit(
    samples: &[f64],
    rate: u32,
    config: &adaptive::AdaptiveConfig,
    anchor: &adaptive::AnchorModel,
) -> Result<Source, String> {
    let pcm = samples
        .get(anchor.window_start_sample..anchor.window_end_sample)
        .ok_or("source outside PCM")?;
    let front = adaptive::frontend(pcm, rate, &config.fixed(), &anchor.frontend)?;
    let soft = dsp::soft_symbols(&front, &anchor.timing)?;
    let verified = anchors::validated_spans(&soft, anchor.timing.threshold)?;
    for expected in &anchor.training_spans {
        if !verified.iter().any(|span| {
            span.start == expected.start_symbol
                && span.end == expected.end_symbol
                && span.g3ruh == expected.g3ruh
                && hex::encode(Sha256::digest(&span.frame)) == expected.received_frame_sha256
        }) {
            return Err("source integrity: anchor no longer independently validates".into());
        }
    }
    let levels: Vec<_> = soft
        .iter()
        .map(|x| u8::from(*x >= anchor.timing.threshold))
        .collect();
    let spans: Vec<_> = anchor
        .training_spans
        .iter()
        .map(|s| (s.start_symbol, s.end_symbol))
        .collect();
    let fit = uncertainty::fit(&soft, &levels, &spans)?;
    if !same_channel(&fit.model, &anchor.model) {
        return Err("source integrity: uncertainty fitter changed the point model".into());
    }
    Ok(Source {
        anchor_id: anchor.id.clone(),
        fit,
    })
}

type TrialKey = (usize, String, String, usize, u64);
type ExpectedTrial = (dsp::TimingHypothesis, BTreeSet<String>);

fn same_timing(a: &dsp::TimingHypothesis, b: &dsp::TimingHypothesis) -> bool {
    a.symbol_count == b.symbol_count
        && [
            a.rate_error_ppm,
            a.phase_samples,
            a.step_samples,
            a.threshold,
            a.score,
        ]
        .iter()
        .zip([
            b.rate_error_ppm,
            b.phase_samples,
            b.step_samples,
            b.threshold,
            b.score,
        ])
        .all(|(x, y)| x.to_bits() == y.to_bits())
}

/// Immutable, shared inputs for all target windows in one replay.
struct ReplayContext<'a> {
    samples: &'a [f64],
    rate: u32,
    config: &'a adaptive::AdaptiveConfig,
    all_anchors: &'a [adaptive::AnchorModel],
    sources: &'a [Source],
    expected: &'a BTreeMap<TrialKey, ExpectedTrial>,
    bank: &'a [ReplayMetric; 3],
}

fn target_window(
    context: &ReplayContext<'_>,
    index: usize,
    bounds: (usize, usize),
) -> Result<(Vec<Trial>, Vec<Value>), String> {
    let ReplayContext {
        samples,
        rate,
        config,
        all_anchors,
        sources,
        expected,
        bank,
    } = *context;
    let mut trials = Vec::new();
    let mut skips = Vec::new();
    let fixed = config.fixed();
    let mut workspace = sequence::SequenceWorkspace::new();
    for name in FRONTENDS {
        let Some(anchor) = adaptive::select_anchor(all_anchors, index, bounds, name, rate) else {
            skips.push(json!({"window_index":index,"frontend":name,"reason":"no disjoint source within 60s, 1s guard"}));
            continue;
        };
        // Select with the unchanged full anchor population BEFORE checking fit
        // availability. A rejection must not silently substitute another source.
        let Some(source) = sources.iter().find(|s| s.anchor_id == anchor.id) else {
            skips.push(json!({"window_index":index,"frontend":name,"anchor_id":anchor.id,"reason":"selected source uncertainty unavailable"}));
            continue;
        };
        let front = adaptive::frontend(&samples[bounds.0..bounds.1], rate, &fixed, name)?;
        let local = dsp::timing_bank(&front, &fixed)?;
        let timings =
            adaptive::supplemental_bank(&front, &fixed, anchor.timing.rate_error_ppm, &local)?;
        let validated = dsp::ValidatedFrontend::new(&front)?;
        let mut soft = Vec::new();
        for (timing_rank, timing) in timings.iter().enumerate() {
            validated.soft_symbols_into(timing, &mut soft)?;
            for gain in GAINS {
                let mut outputs = Vec::new();
                // Alternate local execution order; these are shared-host CPU
                // diagnostics, not isolated timing/throughput measurements.
                let mut order = bank.to_vec();
                if (index + timing_rank) % 2 == 1 {
                    order.reverse();
                }
                for metric in order {
                    let started = Instant::now();
                    let decisions = match metric {
                        ReplayMetric::Point => sequence::detect_sequence_with_workspace(
                            &soft,
                            &anchor.model,
                            gain,
                            &mut workspace,
                        )?
                        .to_vec(),
                        ReplayMetric::Uncertainty(strength) => {
                            uncertainty::detect(&soft, &source.fit, gain, strength)?
                        }
                        ReplayMetric::Huber | ReplayMetric::StudentT => robust_sequence::detect(
                            &soft,
                            &source.fit.model,
                            gain,
                            source.fit.noise_variance.sqrt(),
                            match metric {
                                ReplayMetric::Huber => {
                                    robust_sequence::Metric::Huber { delta: 2.0 }
                                }
                                _ => robust_sequence::Metric::StudentT { nu: 3.0 },
                            },
                        )?,
                    };
                    if decisions.len() != soft.len() {
                        return Err(
                            "sequence decision count differs from target symbol count".into()
                        );
                    }
                    let frames: BTreeSet<_> = anchors::validated_spans(&decisions, 0.0)?
                        .iter()
                        .map(|span| hex::encode(&span.frame))
                        .collect();
                    outputs.push((metric, decisions, frames, started.elapsed().as_secs_f64()));
                }
                let point = outputs
                    .iter()
                    .find(|o| matches!(o.0, ReplayMetric::Point))
                    .expect("fixed point lane");
                let key = (
                    index,
                    name.to_string(),
                    anchor.id.clone(),
                    timing_rank,
                    gain.to_bits(),
                );
                if !expected.get(&key).is_some_and(|(old_timing, old_frames)| {
                    same_timing(old_timing, timing) && old_frames == &point.2
                }) {
                    return Err("matched point replay differs from unchanged adaptive trial".into());
                }
                for (metric, decisions, frames, elapsed) in &outputs {
                    trials.push(Trial {
                        window_index: index,
                        window_start_seconds: bounds.0 as f64 / rate as f64,
                        window_end_seconds: bounds.1 as f64 / rate as f64,
                        frontend: name.into(),
                        anchor_id: anchor.id.clone(),
                        timing_rank,
                        timing: timing.clone(),
                        gain,
                        lane: metric.name(),
                        elapsed_seconds: *elapsed,
                        frame_with_fcs_hex: frames.clone(),
                        decisions_different_from_point: decisions
                            .iter()
                            .zip(&point.1)
                            .filter(|(a, b)| a != b)
                            .count(),
                    });
                }
            }
        }
    }
    Ok((trials, skips))
}

pub fn decode_samples(
    samples: &[f64],
    sample_rate: u32,
    config: &adaptive::AdaptiveConfig,
) -> Result<Report, String> {
    decode_bank(samples, sample_rate, config, &UNCERTAINTY_BANK)
}

/// Development-only paired robust metrics. Source parameters and target clock
/// hypotheses remain identical to the existing point receiver. Results never
/// train another model and are not used for CRC-guided parameter selection.
pub fn decode_robust_samples(
    samples: &[f64],
    sample_rate: u32,
    config: &adaptive::AdaptiveConfig,
) -> Result<Report, String> {
    decode_bank(samples, sample_rate, config, &ROBUST_BANK)
}

fn decode_bank(
    samples: &[f64],
    sample_rate: u32,
    config: &adaptive::AdaptiveConfig,
    bank: &[ReplayMetric; 3],
) -> Result<Report, String> {
    let started = Instant::now();
    let reference = innovation_audio::decode_samples(samples, sample_rate, config, None)?;
    let prep = Instant::now();
    let mut sources = Vec::new();
    let mut source_rejections = Vec::new();
    for anchor in &reference.baseline.anchor_models {
        match source_fit(samples, sample_rate, config, anchor) {
            Ok(source) => sources.push(source),
            Err(reason) if reason.starts_with("uncertainty rejected:") => {
                source_rejections.push(json!({"anchor_id":anchor.id,"reason":reason}))
            }
            Err(reason) => return Err(reason),
        }
    }
    let source_preparation_seconds = prep.elapsed().as_secs_f64();
    let replay = Instant::now();
    let expected: BTreeMap<_, _> = reference
        .baseline
        .supplemental_trials
        .iter()
        .map(|t| {
            (
                (
                    t.window_index,
                    t.frontend.clone(),
                    t.anchor_id.clone(),
                    t.timing_rank,
                    t.gain.to_bits(),
                ),
                (t.timing.clone(), t.frame_with_fcs_hex.clone()),
            )
        })
        .collect();
    if expected.len() != reference.baseline.supplemental_trials.len() {
        return Err("duplicate baseline trial identity".into());
    }
    let bounds = receiver::window_bounds(samples.len(), sample_rate, 6.0, 3.0)?;
    let pool = rayon::ThreadPoolBuilder::new()
        .num_threads(config.threads)
        .build()
        .map_err(|e| e.to_string())?;
    let results: Result<Vec<_>, String> = pool.install(|| {
        bounds
            .par_iter()
            .enumerate()
            .map(|(i, &b)| {
                target_window(
                    &ReplayContext {
                        samples,
                        rate: sample_rate,
                        config,
                        all_anchors: &reference.baseline.anchor_models,
                        sources: &sources,
                        expected: &expected,
                        bank,
                    },
                    i,
                    b,
                )
            })
            .collect()
    });
    let mut trials = Vec::new();
    let mut skips = Vec::new();
    let mut lanes: BTreeMap<String, BTreeSet<String>> =
        bank.iter().map(|s| (s.name(), BTreeSet::new())).collect();
    for (part, skipped) in results? {
        for trial in &part {
            lanes
                .get_mut(&trial.lane)
                .expect("fixed lane")
                .extend(trial.frame_with_fcs_hex.iter().cloned());
        }
        trials.extend(part);
        skips.extend(skipped);
    }
    let paired_replay_seconds = replay.elapsed().as_secs_f64();
    let mut added = BTreeMap::new();
    let mut lost = BTreeMap::new();
    let mut vs_reference = BTreeMap::new();
    let mut union = reference.union_full_frames.clone();
    for metric in &bank[1..] {
        let name = metric.name();
        added.insert(
            name.clone(),
            lanes[&name]
                .difference(&lanes["matched-point"])
                .cloned()
                .collect(),
        );
        lost.insert(
            name.clone(),
            lanes["matched-point"]
                .difference(&lanes[&name])
                .cloned()
                .collect(),
        );
        vs_reference.insert(
            name.clone(),
            lanes[&name]
                .difference(&reference.union_full_frames)
                .cloned()
                .collect(),
        );
        union.extend(lanes[&name].iter().cloned());
    }
    Ok(Report {
        added_union_vs_reference: union
            .difference(&reference.union_full_frames)
            .cloned()
            .collect(),
        lost_union_vs_reference: reference
            .union_full_frames
            .difference(&union)
            .cloned()
            .collect(),
        reference,
        sources,
        source_rejections,
        trials,
        skips,
        lane_full_frames: lanes,
        added_vs_matched_point: added,
        lost_vs_matched_point: lost,
        added_vs_reference: vs_reference,
        union_full_frames: union,
        source_preparation_seconds,
        paired_replay_seconds,
        total_decode_seconds: started.elapsed().as_secs_f64(),
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn model_identity_includes_all_parameters() {
        let a = sequence::ChannelModel {
            taps: [0.1, 1.0, 0.1],
            bias: 0.0,
            training_symbols: 128,
            normalized_mse: 0.1,
        };
        assert!(same_channel(&a, &a));
        let mut b = a.clone();
        b.bias = -0.0;
        assert!(!same_channel(&a, &b));
        b = a.clone();
        b.training_symbols += 1;
        assert!(!same_channel(&a, &b));
    }
    #[test]
    fn invalid_audio_is_rejected_before_experiment() {
        assert!(
            decode_samples(
                &[f64::NAN; 8192],
                48000,
                &adaptive::AdaptiveConfig::default()
            )
            .is_err()
        );
    }

    #[test]
    fn timing_identity_checks_parameters_even_for_empty_results() {
        let a = dsp::TimingHypothesis {
            rate_error_ppm: 0.0,
            phase_samples: 0.5,
            step_samples: 2.5,
            threshold: 0.0,
            score: 1.0,
            symbol_count: 100,
        };
        assert!(same_timing(&a, &a));
        let mut b = a.clone();
        b.threshold = -0.0;
        assert!(!same_timing(&a, &b));
        b = a.clone();
        b.step_samples += 1.0e-10;
        assert!(!same_timing(&a, &b));
        b = a.clone();
        b.symbol_count += 1;
        assert!(!same_timing(&a, &b));
    }
}
