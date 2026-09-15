//! Opt-in two-pass audio receiver: unchanged four-path portfolio plus MLSE.
//!
//! The legacy decode_samples/decode_file path transfers only signal parameters
//! from native CRC-valid spans in disjoint windows, without target-derived
//! training. Progressive task APIs additionally expose explicitly marked
//! signal-only blind fits on inferred target symbols and multi-anchor blends.
//! Neither path uses archived payloads, CRC-guided bit repair, or supplemental
//! packets as new training anchors. The baseline union is retained by the
//! caller; this is not evidence that sequence-only decoding is universally
//! superior.

use std::{
    borrow::Cow,
    collections::{BTreeMap, BTreeSet},
    path::Path,
    time::Instant,
};

use rayon::prelude::*;
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};

use crate::{anchors, dsp, input, protocol, receiver, sequence, tracking};

const FRONTENDS: [&str; 2] = ["legacy-fir512", "boxcar32-rms50"];
const WINDOW_SECONDS: f64 = 6.0;
const HOP_SECONDS: f64 = 3.0;
const ANCHOR_GUARD_SECONDS: f64 = 1.0;
const MAX_ANCHOR_DISTANCE_SECONDS: f64 = 60.0;
const TRANSFER_PHASES: usize = 8;
const LOCAL_TIMINGS: usize = 8;
const GAINS: [f64; 3] = [0.75, 1.0, 1.5];
// Optional exact waveform reuse, never a search budget. If a window cannot be
// retained within this allowance, the second pass recomputes its frontend.
const FRONTEND_CACHE_BYTES: u128 = 256 * 1024 * 1024;

struct CachedFrontend<'a> {
    front: Option<Cow<'a, dsp::Frontend>>,
    local_timings: Vec<dsp::TimingHypothesis>,
}

/// Invocation-local exact window preparation. No model, anchor, or decision is
/// cached: the two anchor generations remain independent. Private fields and a
/// bitwise PCM identity prevent reuse with a different window/configuration.
pub(crate) struct PreparedWindow {
    sample_digest: [u8; 32],
    sample_rate: u32,
    baud_bits: u64,
    fronts: Vec<(dsp::Frontend, Vec<dsp::TimingHypothesis>)>,
}

fn pcm_digest(samples: &[f64]) -> [u8; 32] {
    let mut hash = Sha256::new();
    // Batched hashing preserves signed zero and every f64 bit without allocating
    // a second full PCM buffer or making one digest update per sample.
    let mut bytes = [0u8; 8192];
    for chunk in samples.chunks(1024) {
        for (sample, target) in chunk.iter().zip(bytes.as_chunks_mut::<8>().0) {
            target.copy_from_slice(&sample.to_bits().to_le_bytes());
        }
        hash.update(&bytes[..chunk.len() * 8]);
    }
    hash.finalize().into()
}

impl PreparedWindow {
    pub(crate) fn new(
        samples: &[f64],
        sample_rate: u32,
        config: &AdaptiveConfig,
    ) -> Result<Self, String> {
        validate_progressive_local_window(samples, sample_rate, (0, samples.len()), config)?;
        let fixed = config.fixed();
        let mut fronts = Vec::with_capacity(FRONTENDS.len());
        for name in FRONTENDS {
            let front = frontend(samples, sample_rate, &fixed, name)?;
            let timings = dsp::timing_bank(&front, &fixed)?;
            fronts.push((front, timings));
        }
        Ok(Self {
            sample_digest: pcm_digest(samples),
            sample_rate,
            baud_bits: config.baud.to_bits(),
            fronts,
        })
    }

    fn verify(
        &self,
        samples: &[f64],
        sample_rate: u32,
        config: &AdaptiveConfig,
    ) -> Result<(), String> {
        if self.sample_rate != sample_rate
            || self.baud_bits != config.baud.to_bits()
            || self.sample_digest != pcm_digest(samples)
        {
            return Err("prepared progressive window input/configuration mismatch".into());
        }
        Ok(())
    }

    fn lane(&self, name: &str) -> &(dsp::Frontend, Vec<dsp::TimingHypothesis>) {
        &self.fronts[FRONTENDS
            .iter()
            .position(|n| *n == name)
            .expect("internal frontend name")]
    }

    pub(crate) fn retained_bytes(&self) -> usize {
        std::mem::size_of::<Self>()
            + self.fronts.capacity()
                * std::mem::size_of::<(dsp::Frontend, Vec<dsp::TimingHypothesis>)>()
            + self
                .fronts
                .iter()
                .map(|(front, timings)| {
                    front.samples.capacity() * 8
                        + timings.capacity() * std::mem::size_of::<dsp::TimingHypothesis>()
                })
                .sum::<usize>()
    }
}

fn prepared_lane<'a>(
    samples: &[f64],
    sample_rate: u32,
    fixed: &dsp::DspConfig,
    name: &str,
    prepared: Option<&'a PreparedWindow>,
) -> Result<(Cow<'a, dsp::Frontend>, Cow<'a, [dsp::TimingHypothesis]>), String> {
    if let Some(prepared) = prepared {
        let (front, timings) = prepared.lane(name);
        Ok((Cow::Borrowed(front), Cow::Borrowed(timings)))
    } else {
        let front = frontend(samples, sample_rate, fixed, name)?;
        let timings = dsp::timing_bank(&front, fixed)?;
        Ok((Cow::Owned(front), Cow::Owned(timings)))
    }
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct AdaptiveConfig {
    pub baud: f64,
    pub threads: usize,
}

impl Default for AdaptiveConfig {
    fn default() -> Self {
        Self {
            baud: 9600.0,
            threads: 2,
        }
    }
}

impl AdaptiveConfig {
    pub(crate) fn fixed(&self) -> dsp::DspConfig {
        dsp::DspConfig {
            baud: self.baud,
            bank: "full".into(),
            ..Default::default()
        }
    }

    fn validate(&self, sample_rate: u32) -> Result<(), String> {
        if !(1..=16).contains(&self.threads) {
            return Err("adaptive threads must be in 1..=16".into());
        }
        self.fixed().validate(sample_rate)?;
        // Both fixed frontends and both Gardner lanes must remain supported;
        // do not silently skip a baseline lane for an unsupported baud rate.
        let input_sps = f64::from(sample_rate) / self.baud;
        if !(2.0..=128.0).contains(&input_sps) || !(2.0..=128.0).contains(&(input_sps / 2.0)) {
            return Err("adaptive portfolio requires 4..=128 input samples per symbol".into());
        }
        Ok(())
    }
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct BaselineTrial {
    pub window_index: usize,
    pub frontend: String,
    pub clock: String,
    pub window_start_seconds: f64,
    pub elapsed_seconds: f64,
    pub hypotheses: usize,
    pub frame_with_fcs_hex: BTreeSet<String>,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct TrainingSpan {
    pub start_symbol: usize,
    pub end_symbol: usize,
    pub g3ruh: bool,
    pub received_frame_sha256: String,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct AnchorModel {
    pub id: String,
    pub window_index: usize,
    pub frontend: String,
    pub window_start_sample: usize,
    pub window_end_sample: usize,
    pub window_start_seconds: f64,
    pub window_end_seconds: f64,
    pub timing_rank: usize,
    pub timing: dsp::TimingHypothesis,
    pub model: sequence::ChannelModel,
    pub training_spans: Vec<TrainingSpan>,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct FitRejection {
    pub window_index: usize,
    pub frontend: String,
    pub timing_rank: usize,
    pub reason: String,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct SupplementalTrial {
    pub window_index: usize,
    pub window_start_seconds: f64,
    pub frontend: String,
    pub anchor_id: String,
    pub timing_rank: usize,
    pub timing: dsp::TimingHypothesis,
    pub gain: f64,
    pub elapsed_seconds: f64,
    pub frame_with_fcs_hex: BTreeSet<String>,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct SupplementalSkip {
    pub window_index: usize,
    pub frontend: String,
    pub reason: String,
}

#[derive(Clone, Debug, Serialize)]
pub struct StageWallTimes {
    pub baseline_decode_and_anchor_fit_seconds: f64,
    pub supplemental_decode_seconds: f64,
    pub total_decode_seconds: f64,
}

#[derive(Clone, Debug, Serialize)]
pub struct AdaptiveReport {
    pub window_count: usize,
    pub baseline_variant_counts: BTreeMap<String, usize>,
    pub baseline_variant_full_frames: BTreeMap<String, BTreeSet<String>>,
    pub baseline_full_frames: BTreeSet<String>,
    pub supplemental_full_frames: BTreeSet<String>,
    pub union_full_frames: BTreeSet<String>,
    pub added_vs_baseline: BTreeSet<String>,
    pub lost_vs_baseline: BTreeSet<String>,
    pub baseline_window_trials: Vec<BaselineTrial>,
    pub anchor_models: Vec<AnchorModel>,
    pub anchor_fit_rejections: Vec<FitRejection>,
    pub supplemental_trials: Vec<SupplementalTrial>,
    pub supplemental_skips: Vec<SupplementalSkip>,
    pub stage_wall_seconds: StageWallTimes,
}

struct FirstWindow {
    trials: Vec<BaselineTrial>,
    anchors: Vec<AnchorModel>,
    rejections: Vec<FitRejection>,
    cache: Vec<CachedFrontend<'static>>,
}

/// One serializable, independently commit-able part of a baseline window.
/// The fast prefix and remainder partition the original hypothesis bank;
/// neither part includes results from the other part.
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct ProgressiveBaseline {
    pub trials: Vec<BaselineTrial>,
    pub anchors: Vec<AnchorModel>,
    pub rejections: Vec<FitRejection>,
}

/// New additive branches are kept separate from CRC-trained baseline anchors.
/// A blind model's training_symbols count refers to inferred, not known, bits.
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct ProgressiveChannelTrial {
    pub method: String,
    pub window_index: usize,
    pub window_start_seconds: f64,
    pub frontend: String,
    /// Actual contributing anchors after the robust compatibility gate.
    pub anchor_ids: Vec<String>,
    /// Candidate order used by blend_fit's included/excluded input indexes.
    pub candidate_anchor_ids: Vec<String>,
    pub timing_rank: usize,
    pub timing: dsp::TimingHypothesis,
    pub model: sequence::ChannelModel,
    pub blind_fit: Option<crate::progressive_channel::BlindFit>,
    pub blend_fit: Option<crate::progressive_channel::BlendFit>,
    pub gain: f64,
    pub elapsed_seconds: f64,
    pub frame_with_fcs_hex: BTreeSet<String>,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct ProgressiveChannel {
    pub trials: Vec<ProgressiveChannelTrial>,
    pub rejections: Vec<FitRejection>,
    pub skips: Vec<SupplementalSkip>,
}

#[derive(Clone, Copy)]
enum BaselinePartition {
    Full,
    Fast,
    Remaining,
}

impl BaselinePartition {
    fn range(self, length: usize, prefix: usize) -> std::ops::Range<usize> {
        match self {
            Self::Full => 0..length,
            Self::Fast => 0..length.min(prefix),
            Self::Remaining => length.min(prefix)..length,
        }
    }

    fn label(self, full: &str, prefix: &str, remaining: &str) -> String {
        match self {
            Self::Full => full,
            Self::Fast => prefix,
            Self::Remaining => remaining,
        }
        .into()
    }
}

fn clocks() -> Vec<tracking::GardnerConfig> {
    [0.02, 0.06]
        .into_iter()
        .flat_map(|bandwidth| {
            (0..8).map(move |phase| tracking::GardnerConfig {
                bandwidth,
                initial_phase_symbols: (phase as f64 + 0.5) / 8.0,
                ..Default::default()
            })
        })
        .collect()
}

pub(crate) fn frontend(
    pcm: &[f64],
    sample_rate: u32,
    config: &dsp::DspConfig,
    name: &str,
) -> Result<dsp::Frontend, String> {
    match name {
        "legacy-fir512" => dsp::frontend(pcm, sample_rate, config),
        "boxcar32-rms50" => tracking::boxcar_frontend(pcm, sample_rate, config.baud),
        _ => Err("unknown adaptive frontend".into()),
    }
}

fn frame_set(soft: &[f64], threshold: f64) -> Result<BTreeSet<String>, String> {
    Ok(protocol::decode_ax25(soft, threshold, &[false, true])?
        .iter()
        .map(hex::encode)
        .collect())
}

fn checked_spans(
    soft: &[f64],
    threshold: f64,
    baseline: &BTreeSet<String>,
) -> Result<Vec<anchors::ValidatedSpan>, String> {
    let spans = anchors::validated_spans(soft, threshold)?;
    let extracted: BTreeSet<_> = spans.iter().map(|span| hex::encode(&span.frame)).collect();
    if &extracted != baseline {
        return Err("native decoder and independently checked anchor frame sets disagree".into());
    }
    Ok(spans)
}

fn expected_model_rejection(reason: &str) -> bool {
    matches!(
        reason,
        "channel training needs at least 128 distinct guarded symbols"
            | "channel training has no signal variation"
            | "channel training has no usable signal variation"
            | "channel training regressors are rank deficient"
            | "channel model residual exceeds the quality gate"
            | "channel model lacks a positive, sufficiently dominant center tap"
    )
}

fn first_window(
    samples: &[f64],
    sample_rate: u32,
    window_index: usize,
    bounds: (usize, usize),
    fixed_config: &dsp::DspConfig,
    clocks: &[tracking::GardnerConfig],
    retain_frontend: bool,
) -> Result<FirstWindow, String> {
    first_window_partition(
        samples,
        sample_rate,
        window_index,
        bounds,
        fixed_config,
        clocks,
        retain_frontend,
        BaselinePartition::Full,
    )
}

#[allow(clippy::too_many_arguments)]
fn first_window_partition(
    samples: &[f64],
    sample_rate: u32,
    window_index: usize,
    bounds: (usize, usize),
    fixed_config: &dsp::DspConfig,
    clocks: &[tracking::GardnerConfig],
    retain_frontend: bool,
    partition: BaselinePartition,
) -> Result<FirstWindow, String> {
    first_window_partition_prepared(
        samples,
        sample_rate,
        window_index,
        bounds,
        fixed_config,
        clocks,
        retain_frontend,
        partition,
        None,
    )
}

#[allow(clippy::too_many_arguments)]
fn first_window_partition_prepared(
    samples: &[f64],
    sample_rate: u32,
    window_index: usize,
    bounds: (usize, usize),
    fixed_config: &dsp::DspConfig,
    clocks: &[tracking::GardnerConfig],
    retain_frontend: bool,
    partition: BaselinePartition,
    prepared: Option<&PreparedWindow>,
) -> Result<FirstWindow, String> {
    let (left, right) = bounds;
    let mut output = FirstWindow {
        trials: Vec::new(),
        anchors: Vec::new(),
        rejections: Vec::new(),
        cache: Vec::with_capacity(FRONTENDS.len()),
    };
    for name in FRONTENDS {
        let frontend_started = Instant::now();
        let (front, timings) = prepared_lane(
            &samples[left..right],
            sample_rate,
            fixed_config,
            name,
            prepared,
        )?;
        let frontend_elapsed = frontend_started.elapsed().as_secs_f64();
        let fixed_started = Instant::now();
        let validated = dsp::ValidatedFrontend::new(&front)?;
        let mut soft = Vec::new();
        let mut fixed_frames = BTreeSet::new();
        let mut best: Option<AnchorModel> = None;
        let timing_range = partition.range(timings.len(), LOCAL_TIMINGS);
        for timing_rank in timing_range.clone() {
            let timing = &timings[timing_rank];
            validated.soft_symbols_into(timing, &mut soft)?;
            // The same protocol entry point and full timing bank as the frozen
            // four-path probe define baseline output, independently of fitting.
            let frames = frame_set(&soft, timing.threshold)?;
            fixed_frames.extend(frames.iter().cloned());
            if frames.is_empty() {
                continue;
            }
            let spans = checked_spans(&soft, timing.threshold, &frames)?;
            let levels: Vec<u8> = soft
                .iter()
                .map(|value| u8::from(*value >= timing.threshold))
                .collect();
            let coordinates: Vec<_> = spans.iter().map(|span| (span.start, span.end)).collect();
            let model = match sequence::fit_channel(&soft, &levels, &coordinates) {
                Ok(model) => model,
                Err(reason) if expected_model_rejection(&reason) => {
                    // Insufficient training, rank and channel-quality rejection
                    // remain recorded outcomes; never manufacture a model.
                    output.rejections.push(FitRejection {
                        window_index,
                        frontend: name.into(),
                        timing_rank,
                        reason,
                    });
                    continue;
                }
                Err(reason) => return Err(format!("anchor fitting failed: {reason}")),
            };
            let improves = best.as_ref().is_none_or(|old| {
                model
                    .normalized_mse
                    .total_cmp(&old.model.normalized_mse)
                    .then_with(|| old.model.training_symbols.cmp(&model.training_symbols))
                    .then_with(|| timing_rank.cmp(&old.timing_rank))
                    .is_lt()
            });
            if improves {
                best = Some(AnchorModel {
                    id: format!("window-{window_index}/{name}"),
                    window_index,
                    frontend: name.into(),
                    window_start_sample: left,
                    window_end_sample: right,
                    window_start_seconds: left as f64 / f64::from(sample_rate),
                    window_end_seconds: right as f64 / f64::from(sample_rate),
                    timing_rank,
                    timing: timing.clone(),
                    model,
                    training_spans: spans
                        .iter()
                        .map(|span| TrainingSpan {
                            start_symbol: span.start,
                            end_symbol: span.end,
                            g3ruh: span.g3ruh,
                            received_frame_sha256: hex::encode(Sha256::digest(&span.frame)),
                        })
                        .collect(),
                });
            }
        }
        output.trials.push(BaselineTrial {
            window_index,
            frontend: name.into(),
            clock: partition.label("fixed-full160", "fixed-prefix8", "fixed-remainder152"),
            window_start_seconds: left as f64 / f64::from(sample_rate),
            elapsed_seconds: frontend_elapsed + fixed_started.elapsed().as_secs_f64(),
            hypotheses: timing_range.len(),
            frame_with_fcs_hex: fixed_frames,
        });
        if let Some(anchor) = best {
            output.anchors.push(anchor);
        }
        let tracking_started = Instant::now();
        let mut tracked_frames = BTreeSet::new();
        let clock_range = partition.range(clocks.len(), 2);
        for clock in &clocks[clock_range.clone()] {
            let symbols = tracking::gardner(&front, clock)?;
            let frames = frame_set(&symbols.soft, 0.0)?;
            if !frames.is_empty() {
                // Independent FCS evidence applies to the retained baseline as
                // well, but Gardner decisions are not used to train this model.
                checked_spans(&symbols.soft, 0.0, &frames)?;
            }
            tracked_frames.extend(frames);
        }
        output.trials.push(BaselineTrial {
            window_index,
            frontend: name.into(),
            clock: partition.label("gardner16", "gardner-prefix2", "gardner-remainder14"),
            window_start_seconds: left as f64 / f64::from(sample_rate),
            elapsed_seconds: frontend_elapsed + tracking_started.elapsed().as_secs_f64(),
            hypotheses: clock_range.len(),
            frame_with_fcs_hex: tracked_frames,
        });
        // The borrow is no longer used. Each admission reserves two full input
        // buffers; check actual capacity too, so allocator growth cannot exceed
        // that reservation. All caches are invocation-local: no stale inputs,
        // configuration changes or whole-recording filter boundary changes.
        let fits_reservation = front.samples.capacity() <= right - left;
        output.cache.push(CachedFrontend {
            front: (retain_frontend && fits_reservation).then(|| Cow::Owned(front.into_owned())),
            local_timings: timings.iter().take(LOCAL_TIMINGS).cloned().collect(),
        });
    }
    Ok(output)
}

pub(crate) fn select_anchor<'a>(
    models: &'a [AnchorModel],
    target_index: usize,
    target: (usize, usize),
    frontend: &str,
    sample_rate: u32,
) -> Option<&'a AnchorModel> {
    let guard = (ANCHOR_GUARD_SECONDS * f64::from(sample_rate)) as usize;
    let distance_limit = (MAX_ANCHOR_DISTANCE_SECONDS * f64::from(sample_rate)) as usize;
    models
        .iter()
        .filter(|anchor| {
            anchor.window_index != target_index
                && anchor.frontend == frontend
                && anchor.window_start_sample.abs_diff(target.0) <= distance_limit
                && (anchor.window_end_sample.saturating_add(guard) <= target.0
                    || target.1.saturating_add(guard) <= anchor.window_start_sample)
        })
        .min_by_key(|anchor| {
            (
                anchor.window_start_sample.abs_diff(target.0),
                anchor.window_index,
            )
        })
}

pub(crate) fn supplemental_bank(
    front: &dsp::Frontend,
    fixed_config: &dsp::DspConfig,
    anchor_rate: f64,
    local: &[dsp::TimingHypothesis],
) -> Result<Vec<dsp::TimingHypothesis>, String> {
    let transferred = dsp::DspConfig {
        rate_errors_ppm: vec![anchor_rate],
        phase_bins: TRANSFER_PHASES,
        ..fixed_config.clone()
    };
    let mut result = dsp::timing_bank(front, &transferred)?;
    result.extend(local.iter().take(LOCAL_TIMINGS).cloned());
    let mut seen = BTreeSet::new();
    result.retain(|timing| {
        seen.insert((
            timing.step_samples.to_bits(),
            timing.phase_samples.to_bits(),
            timing.symbol_count,
        ))
    });
    Ok(result)
}

fn supplemental_window(
    samples: &[f64],
    sample_rate: u32,
    window_index: usize,
    bounds: (usize, usize),
    fixed_config: &dsp::DspConfig,
    models: &[AnchorModel],
    cache: &[CachedFrontend<'_>],
) -> Result<(Vec<SupplementalTrial>, Vec<SupplementalSkip>), String> {
    let mut trials = Vec::new();
    let mut skips = Vec::new();
    let mut workspace = sequence::SequenceWorkspace::new();
    for (frontend_index, name) in FRONTENDS.into_iter().enumerate() {
        let Some(anchor) = select_anchor(models, window_index, bounds, name, sample_rate) else {
            skips.push(SupplementalSkip {
                window_index,
                frontend: name.into(),
                reason: "no disjoint same-frontend anchor within 60s and with 1s guard".into(),
            });
            continue;
        };
        let cached = &cache[frontend_index];
        let recomputed;
        let front = if let Some(front) = &cached.front {
            front.as_ref()
        } else {
            let pcm = samples.get(bounds.0..bounds.1).ok_or_else(|| {
                "supplemental task needs a prepared frontend or matching recording coordinates"
                    .to_string()
            })?;
            recomputed = frontend(pcm, sample_rate, fixed_config, name)?;
            &recomputed
        };
        let timings = supplemental_bank(
            front,
            fixed_config,
            anchor.timing.rate_error_ppm,
            &cached.local_timings,
        )?;
        let validated = dsp::ValidatedFrontend::new(front)?;
        let mut soft = Vec::new();
        if timings.is_empty() {
            skips.push(SupplementalSkip {
                window_index,
                frontend: name.into(),
                reason: "empty target frontend has no timing hypotheses".into(),
            });
        }
        for (timing_rank, timing) in timings.iter().enumerate() {
            validated.soft_symbols_into(timing, &mut soft)?;
            for gain in GAINS {
                let started = Instant::now();
                let decisions = sequence::detect_sequence_with_workspace(
                    &soft,
                    &anchor.model,
                    gain,
                    &mut workspace,
                )?;
                let spans = anchors::validated_spans(decisions, 0.0)?;
                trials.push(SupplementalTrial {
                    window_index,
                    window_start_seconds: bounds.0 as f64 / f64::from(sample_rate),
                    frontend: name.into(),
                    anchor_id: anchor.id.clone(),
                    timing_rank,
                    timing: timing.clone(),
                    gain,
                    elapsed_seconds: started.elapsed().as_secs_f64(),
                    frame_with_fcs_hex: spans.iter().map(|span| hex::encode(&span.frame)).collect(),
                });
            }
        }
    }
    Ok((trials, skips))
}

fn validate_progressive_window(
    samples: &[f64],
    sample_rate: u32,
    bounds: (usize, usize),
    config: &AdaptiveConfig,
) -> Result<(), String> {
    config.validate(sample_rate)?;
    let (left, right) = bounds;
    if left >= right || right > samples.len() {
        return Err("progressive task window must be nonempty and inside the input".into());
    }
    if right - left > (WINDOW_SECONDS * f64::from(sample_rate)).ceil() as usize
        || right - left > dsp::MAX_PCM_WINDOW_SAMPLES
    {
        return Err("progressive task window exceeds six seconds or DSP sample limit".into());
    }
    if samples[left..right]
        .iter()
        .any(|sample| !sample.is_finite() || sample.abs() > 1.0e100)
    {
        return Err("progressive task window has non-finite or oversized samples".into());
    }
    Ok(())
}

fn validate_progressive_local_window(
    samples: &[f64],
    sample_rate: u32,
    absolute_bounds: (usize, usize),
    config: &AdaptiveConfig,
) -> Result<(), String> {
    if absolute_bounds.1.checked_sub(absolute_bounds.0) != Some(samples.len()) {
        return Err("local progressive samples must exactly match absolute window bounds".into());
    }
    validate_progressive_window(samples, sample_rate, (0, samples.len()), config)
}

/// Execute the first eight ranked fixed clocks and first two Gardner clocks
/// per frontend (`fast=true`), or exactly the remaining clocks (`false`).
/// No decoded hypothesis is repeated between the two tasks. This public wrapper
/// recomputes preparation; the internal scheduler may reuse exact preparation
/// within one process. Neither API persists frontends across invocations.
pub fn progressive_baseline(
    samples: &[f64],
    sample_rate: u32,
    window_index: usize,
    bounds: (usize, usize),
    config: &AdaptiveConfig,
    fast: bool,
) -> Result<ProgressiveBaseline, String> {
    validate_progressive_window(samples, sample_rate, bounds, config)?;
    let first = first_window_partition(
        samples,
        sample_rate,
        window_index,
        bounds,
        &config.fixed(),
        &clocks(),
        false,
        if fast {
            BaselinePartition::Fast
        } else {
            BaselinePartition::Remaining
        },
    )?;
    Ok(ProgressiveBaseline {
        trials: first.trials,
        anchors: first.anchors,
        rejections: first.rejections,
    })
}

/// Baseline task for an already loaded window. Filtering still starts at the
/// same window boundary; only input ownership changes. Reported sample/time
/// coordinates refer to the complete recording, while symbol spans and timing
/// hypotheses remain relative to this frontend exactly as in the full API.
pub fn progressive_baseline_local(
    samples: &[f64],
    sample_rate: u32,
    window_index: usize,
    absolute_bounds: (usize, usize),
    config: &AdaptiveConfig,
    fast: bool,
) -> Result<ProgressiveBaseline, String> {
    progressive_baseline_local_prepared(
        samples,
        sample_rate,
        window_index,
        absolute_bounds,
        config,
        fast,
        None,
    )
}

pub(crate) fn progressive_baseline_local_prepared(
    samples: &[f64],
    sample_rate: u32,
    window_index: usize,
    absolute_bounds: (usize, usize),
    config: &AdaptiveConfig,
    fast: bool,
    prepared: Option<&PreparedWindow>,
) -> Result<ProgressiveBaseline, String> {
    validate_progressive_local_window(samples, sample_rate, absolute_bounds, config)?;
    if let Some(prepared) = prepared {
        prepared.verify(samples, sample_rate, config)?;
    }
    let first = first_window_partition_prepared(
        samples,
        sample_rate,
        window_index,
        (0, samples.len()),
        &config.fixed(),
        &clocks(),
        false,
        if fast {
            BaselinePartition::Fast
        } else {
            BaselinePartition::Remaining
        },
        prepared,
    )?;
    let mut result = ProgressiveBaseline {
        trials: first.trials,
        anchors: first.anchors,
        rejections: first.rejections,
    };
    let start_seconds = absolute_bounds.0 as f64 / f64::from(sample_rate);
    let end_seconds = absolute_bounds.1 as f64 / f64::from(sample_rate);
    for trial in &mut result.trials {
        trial.window_start_seconds = start_seconds;
    }
    for anchor in &mut result.anchors {
        anchor.window_start_sample = absolute_bounds.0;
        anchor.window_end_sample = absolute_bounds.1;
        anchor.window_start_seconds = start_seconds;
        anchor.window_end_seconds = end_seconds;
    }
    Ok(result)
}

/// Merge independently committed baseline anchor candidates with the original
/// selection rule. Supplemental/blind model outputs must never be passed here.
/// For a complete fast+remainder partition, this returns the same anchors as
/// the original full baseline, ordered by window and original frontend order.
pub fn progressive_merge_anchors(candidates: &[AnchorModel]) -> Vec<AnchorModel> {
    let mut best: BTreeMap<(usize, String), AnchorModel> = BTreeMap::new();
    for candidate in candidates {
        let key = (candidate.window_index, candidate.frontend.clone());
        let improves = best.get(&key).is_none_or(|old| {
            candidate
                .model
                .normalized_mse
                .total_cmp(&old.model.normalized_mse)
                .then_with(|| {
                    old.model
                        .training_symbols
                        .cmp(&candidate.model.training_symbols)
                })
                .then_with(|| candidate.timing_rank.cmp(&old.timing_rank))
                .is_lt()
        });
        if improves {
            best.insert(key, candidate.clone());
        }
    }
    let mut result: Vec<_> = best.into_values().collect();
    result.sort_by_key(|anchor| {
        (
            anchor.window_index,
            FRONTENDS
                .iter()
                .position(|&name| name == anchor.frontend)
                .unwrap_or(FRONTENDS.len()),
            anchor.frontend.clone(),
        )
    });
    result
}

/// Replay the unchanged nearest-disjoint-anchor branch for one window.
/// The caller owns the immutable anchor generation dependency and checkpoint.
/// A later anchor generation requires a new task identity, not reuse of an
/// earlier empty/no-anchor outcome. This public wrapper does not retain a cache;
/// the scheduler's prepared variant can share waveforms, never anchor selection.
pub fn progressive_supplemental(
    samples: &[f64],
    sample_rate: u32,
    window_index: usize,
    bounds: (usize, usize),
    config: &AdaptiveConfig,
    models: &[AnchorModel],
) -> Result<Vec<SupplementalTrial>, String> {
    validate_progressive_window(samples, sample_rate, bounds, config)?;
    progressive_supplemental_local(
        &samples[bounds.0..bounds.1],
        sample_rate,
        window_index,
        bounds,
        config,
        models,
    )
}

/// Nearest-anchor task for a preloaded window, with selection and provenance
/// in absolute recording coordinates. Every eligible frontend is prepared
/// below; the shared supplemental core therefore never slices the local PCM
/// using absolute bounds. Non-eligible lanes are skipped before cache access.
pub fn progressive_supplemental_local(
    samples: &[f64],
    sample_rate: u32,
    window_index: usize,
    absolute_bounds: (usize, usize),
    config: &AdaptiveConfig,
    models: &[AnchorModel],
) -> Result<Vec<SupplementalTrial>, String> {
    progressive_supplemental_local_prepared(
        samples,
        sample_rate,
        window_index,
        absolute_bounds,
        config,
        models,
        None,
    )
}

pub(crate) fn progressive_supplemental_local_prepared(
    samples: &[f64],
    sample_rate: u32,
    window_index: usize,
    absolute_bounds: (usize, usize),
    config: &AdaptiveConfig,
    models: &[AnchorModel],
    cached: Option<&PreparedWindow>,
) -> Result<Vec<SupplementalTrial>, String> {
    validate_progressive_local_window(samples, sample_rate, absolute_bounds, config)?;
    if let Some(cached) = cached {
        cached.verify(samples, sample_rate, config)?;
    }
    let fixed = config.fixed();
    let mut prepared = Vec::with_capacity(FRONTENDS.len());
    for name in FRONTENDS {
        if select_anchor(models, window_index, absolute_bounds, name, sample_rate).is_none() {
            prepared.push(CachedFrontend {
                front: None,
                local_timings: Vec::new(),
            });
            continue;
        }
        let (front, timings) = prepared_lane(samples, sample_rate, &fixed, name, cached)?;
        let local_timings = timings.iter().take(LOCAL_TIMINGS).cloned().collect();
        prepared.push(CachedFrontend {
            // Keep the prepared waveform borrowed for this call. The owned
            // fallback still lives in this container until decoding completes.
            front: Some(front),
            local_timings,
        });
    }
    supplemental_window(
        samples,
        sample_rate,
        window_index,
        absolute_bounds,
        &fixed,
        models,
        &prepared,
    )
    .map(|(trials, _)| trials)
}

/// Select up to four distinct same-frontend baseline windows. Every selected
/// window is disjoint from both the target and every other selected window,
/// including the same one-second guard as the original transfer path. This
/// avoids presenting overlapping training symbols as independent evidence.
fn progressive_multi_anchors<'a>(
    models: &'a [AnchorModel],
    target_index: usize,
    target: (usize, usize),
    name: &str,
    sample_rate: u32,
) -> Vec<&'a AnchorModel> {
    let guard = (ANCHOR_GUARD_SECONDS * f64::from(sample_rate)) as usize;
    let limit = (MAX_ANCHOR_DISTANCE_SECONDS * f64::from(sample_rate)) as usize;
    let mut candidates: Vec<_> = models
        .iter()
        .filter(|anchor| {
            anchor.window_index != target_index
                && anchor.frontend == name
                && anchor.window_start_sample < anchor.window_end_sample
                && !anchor.training_spans.is_empty()
                && anchor.window_start_sample.abs_diff(target.0) <= limit
                && (anchor.window_end_sample.saturating_add(guard) <= target.0
                    || target.1.saturating_add(guard) <= anchor.window_start_sample)
        })
        .collect();
    candidates.sort_by(|a, b| {
        a.window_start_sample
            .abs_diff(target.0)
            .cmp(&b.window_start_sample.abs_diff(target.0))
            .then_with(|| a.model.normalized_mse.total_cmp(&b.model.normalized_mse))
            .then_with(|| a.window_index.cmp(&b.window_index))
    });
    let mut selected: Vec<&AnchorModel> = Vec::new();
    for candidate in candidates {
        if selected.iter().all(|other| {
            candidate.window_index != other.window_index
                && (candidate.window_end_sample.saturating_add(guard) <= other.window_start_sample
                    || other.window_end_sample.saturating_add(guard)
                        <= candidate.window_start_sample)
        }) {
            selected.push(candidate);
            if selected.len() == 4 {
                break;
            }
        }
    }
    selected
}

/// Execute signal-only bootstrap and/or multi-anchor transfer as additive,
/// separately attributable branches. Model selection never observes whether a
/// candidate decodes a frame. Only the final unchanged received-FCS validator
/// accepts a frame; no CRC-guided bit search or supplemental-anchor training
/// occurs. The scheduler should give blind and multi separate task identities
/// for ablation, and bind multi tasks to an immutable baseline anchor set.
#[allow(clippy::too_many_arguments)]
pub fn progressive_new_channel(
    samples: &[f64],
    sample_rate: u32,
    window_index: usize,
    bounds: (usize, usize),
    config: &AdaptiveConfig,
    models: &[AnchorModel],
    blind: bool,
    multi: bool,
) -> Result<ProgressiveChannel, String> {
    validate_progressive_window(samples, sample_rate, bounds, config)?;
    progressive_new_channel_local(
        &samples[bounds.0..bounds.1],
        sample_rate,
        window_index,
        bounds,
        config,
        models,
        blind,
        multi,
    )
}

/// Signal-only and multiple-anchor task for preloaded PCM. Absolute bounds
/// continue to govern disjoint source selection and all emitted provenance.
#[allow(clippy::too_many_arguments)]
pub fn progressive_new_channel_local(
    samples: &[f64],
    sample_rate: u32,
    window_index: usize,
    bounds: (usize, usize),
    config: &AdaptiveConfig,
    models: &[AnchorModel],
    blind: bool,
    multi: bool,
) -> Result<ProgressiveChannel, String> {
    progressive_new_channel_local_prepared(
        samples,
        sample_rate,
        window_index,
        bounds,
        config,
        models,
        blind,
        multi,
        None,
    )
}

#[allow(clippy::too_many_arguments)]
pub(crate) fn progressive_new_channel_local_prepared(
    samples: &[f64],
    sample_rate: u32,
    window_index: usize,
    bounds: (usize, usize),
    config: &AdaptiveConfig,
    models: &[AnchorModel],
    blind: bool,
    multi: bool,
    prepared: Option<&PreparedWindow>,
) -> Result<ProgressiveChannel, String> {
    validate_progressive_local_window(samples, sample_rate, bounds, config)?;
    if let Some(prepared) = prepared {
        prepared.verify(samples, sample_rate, config)?;
    }
    let mut output = ProgressiveChannel {
        trials: Vec::new(),
        rejections: Vec::new(),
        skips: Vec::new(),
    };
    if !blind && !multi {
        return Ok(output);
    }
    let fixed = config.fixed();
    let mut workspace = sequence::SequenceWorkspace::new();
    for name in FRONTENDS {
        let selected = if multi {
            progressive_multi_anchors(models, window_index, bounds, name, sample_rate)
        } else {
            Vec::new()
        };
        let blended = if multi && selected.len() >= 2 {
            let weighted: Vec<_> = selected
                .iter()
                .map(|anchor| {
                    (
                        anchor.model.clone(),
                        anchor.window_start_sample.abs_diff(bounds.0) as f64
                            / f64::from(sample_rate),
                    )
                })
                .collect();
            match crate::progressive_channel::blend_models_diagnostic(&weighted) {
                Ok(fit) => Some(fit),
                Err(reason) if reason.starts_with("blend rejected:") => {
                    output.skips.push(SupplementalSkip {
                        window_index,
                        frontend: name.into(),
                        reason,
                    });
                    None
                }
                Err(reason) => return Err(reason),
            }
        } else {
            if multi {
                output.skips.push(SupplementalSkip {
                    window_index,
                    frontend: name.into(),
                    reason: "multi-anchor requires at least two mutually disjoint CRC-trained baseline windows within 60s and 1s guard".into(),
                });
            }
            None
        };
        if !blind && blended.is_none() {
            continue;
        }
        let (front, timings) = prepared_lane(samples, sample_rate, &fixed, name, prepared)?;
        let validated = dsp::ValidatedFrontend::new(&front)?;
        let mut soft = Vec::new();
        if timings.is_empty() {
            output.skips.push(SupplementalSkip {
                window_index,
                frontend: name.into(),
                reason: "empty target frontend has no timing hypotheses".into(),
            });
        }
        for (timing_rank, timing) in timings.iter().take(LOCAL_TIMINGS).enumerate() {
            validated.soft_symbols_into(timing, &mut soft)?;
            let fit_started = Instant::now();
            let inferred = if blind {
                match crate::progressive_channel::fit_blind_diagnostic(&soft) {
                    Ok(fit) => Some(fit),
                    Err(reason) if reason.starts_with("blind rejected:") => {
                        output.rejections.push(FitRejection {
                            window_index,
                            frontend: name.into(),
                            timing_rank,
                            reason,
                        });
                        None
                    }
                    Err(reason) => return Err(reason),
                }
            } else {
                None
            };
            let fit_elapsed = fit_started.elapsed().as_secs_f64();
            for (method, model, blind_fit, blend_fit) in [
                (
                    "blind",
                    inferred.as_ref().map(|fit| &fit.model),
                    inferred.as_ref(),
                    None,
                ),
                (
                    "multi-anchor",
                    blended.as_ref().map(|fit| &fit.model),
                    None,
                    blended.as_ref(),
                ),
            ] {
                let Some(model) = model else { continue };
                let candidate_anchor_ids: Vec<_> = blend_fit
                    .map(|_| selected.iter().map(|anchor| anchor.id.clone()).collect())
                    .unwrap_or_default();
                let anchor_ids: Vec<_> = blend_fit
                    .map(|fit| {
                        fit.included_input_indices
                            .iter()
                            .map(|&index| selected[index].id.clone())
                            .collect()
                    })
                    .unwrap_or_default();
                for gain in GAINS {
                    let started = Instant::now();
                    let decisions = sequence::detect_sequence_with_workspace(
                        &soft,
                        model,
                        gain,
                        &mut workspace,
                    )?;
                    let spans = anchors::validated_spans(decisions, 0.0)?;
                    output.trials.push(ProgressiveChannelTrial {
                        method: method.into(),
                        window_index,
                        window_start_seconds: bounds.0 as f64 / f64::from(sample_rate),
                        frontend: name.into(),
                        anchor_ids: anchor_ids.clone(),
                        candidate_anchor_ids: candidate_anchor_ids.clone(),
                        timing_rank,
                        timing: timing.clone(),
                        model: model.clone(),
                        blind_fit: blind_fit.cloned(),
                        blend_fit: blend_fit.cloned(),
                        gain,
                        // Blind fit is repeated in trial accounting, just as
                        // frontend time in legacy baseline variants. Task wall
                        // time, not a sum of trial times, is the cost metric.
                        elapsed_seconds: started.elapsed().as_secs_f64()
                            + if method == "blind" { fit_elapsed } else { 0.0 },
                        frame_with_fcs_hex: spans
                            .iter()
                            .map(|span| hex::encode(&span.frame))
                            .collect(),
                    });
                }
            }
        }
    }
    Ok(output)
}

/// Decode one bounded mono post-FM recording. Inputs never include references.
pub fn decode_samples(
    samples: &[f64],
    sample_rate: u32,
    config: &AdaptiveConfig,
) -> Result<AdaptiveReport, String> {
    decode_samples_with_cache(samples, sample_rate, config, FRONTEND_CACHE_BYTES)
}

fn cache_admission(bounds: &[(usize, usize)], budget: u128) -> Vec<bool> {
    let mut used = 0;
    bounds
        .iter()
        .map(|&(left, right)| {
            let reservation = (right - left) as u128 * 8 * FRONTENDS.len() as u128;
            if reservation <= budget.saturating_sub(used) {
                used += reservation;
                true
            } else {
                false
            }
        })
        .collect()
}

fn decode_samples_with_cache(
    samples: &[f64],
    sample_rate: u32,
    config: &AdaptiveConfig,
    cache_budget: u128,
) -> Result<AdaptiveReport, String> {
    config.validate(sample_rate)?;
    if samples.len() as u64 > input::MAX_LOADED_AUDIO_SAMPLES
        || samples.len() as f64 / f64::from(sample_rate) > input::MAX_AUDIO_SECONDS
        || samples
            .iter()
            .any(|sample| !sample.is_finite() || sample.abs() > 1.0e100)
    {
        return Err("adaptive audio is oversized, non-finite, or exceeds magnitude 1e100".into());
    }
    let bounds = receiver::window_bounds(samples.len(), sample_rate, WINDOW_SECONDS, HOP_SECONDS)?;
    let largest = bounds
        .iter()
        .map(|&(left, right)| right - left)
        .max()
        .unwrap_or(0);
    let estimate = samples.len() as u128 * 8 + largest as u128 * 8 * 32 * config.threads as u128;
    if estimate > 6_u128 * 1024 * 1024 * 1024 {
        return Err("estimated adaptive audio+worker working set exceeds 6 GiB".into());
    }
    // Do not reject an input accepted before this optimization just because
    // optional reuse needs memory. Reserve only existing estimate headroom.
    let cache_enabled = cache_admission(
        &bounds,
        cache_budget.min(6_u128 * 1024 * 1024 * 1024 - estimate),
    );
    let pool = rayon::ThreadPoolBuilder::new()
        .num_threads(config.threads)
        .build()
        .map_err(|error| error.to_string())?;
    let fixed = config.fixed();
    let clocks = clocks();
    let total_started = Instant::now();
    let baseline_started = Instant::now();
    let first: Result<Vec<_>, String> = pool.install(|| {
        bounds
            .par_iter()
            .enumerate()
            .map(|(index, &bound)| {
                first_window(
                    samples,
                    sample_rate,
                    index,
                    bound,
                    &fixed,
                    &clocks,
                    cache_enabled[index],
                )
                .map_err(|error| format!("baseline window {index}: {error}"))
            })
            .collect()
    });
    let mut baseline_window_trials = Vec::new();
    let mut models = Vec::new();
    let mut rejections = Vec::new();
    let mut caches = Vec::with_capacity(bounds.len());
    let mut variants: BTreeMap<String, BTreeSet<String>> = FRONTENDS
        .iter()
        .flat_map(|name| {
            ["fixed-full160", "gardner16"].map(|clock| (format!("{name}/{clock}"), BTreeSet::new()))
        })
        .collect();
    for window in first? {
        for trial in &window.trials {
            variants
                .entry(format!("{}/{}", trial.frontend, trial.clock))
                .or_default()
                .extend(trial.frame_with_fcs_hex.iter().cloned());
        }
        baseline_window_trials.extend(window.trials);
        models.extend(window.anchors);
        rejections.extend(window.rejections);
        caches.push(window.cache);
    }
    let baseline_elapsed = baseline_started.elapsed().as_secs_f64();
    let supplemental_started = Instant::now();
    let second: Result<Vec<_>, String> = pool.install(|| {
        bounds
            .par_iter()
            .enumerate()
            .map(|(index, &bound)| {
                supplemental_window(
                    samples,
                    sample_rate,
                    index,
                    bound,
                    &fixed,
                    &models,
                    &caches[index],
                )
                .map_err(|error| format!("supplemental window {index}: {error}"))
            })
            .collect()
    });
    let mut supplemental_trials = Vec::new();
    let mut supplemental_skips = Vec::new();
    let mut supplemental_frames = BTreeSet::new();
    for (trials, skips) in second? {
        supplemental_frames.extend(
            trials
                .iter()
                .flat_map(|trial| trial.frame_with_fcs_hex.iter().cloned()),
        );
        supplemental_trials.extend(trials);
        supplemental_skips.extend(skips);
    }
    let supplemental_elapsed = supplemental_started.elapsed().as_secs_f64();
    let baseline: BTreeSet<_> = variants.values().flatten().cloned().collect();
    let union: BTreeSet<_> = baseline.union(&supplemental_frames).cloned().collect();
    Ok(AdaptiveReport {
        window_count: bounds.len(),
        baseline_variant_counts: variants
            .iter()
            .map(|(name, set)| (name.clone(), set.len()))
            .collect(),
        baseline_variant_full_frames: variants,
        added_vs_baseline: union.difference(&baseline).cloned().collect(),
        lost_vs_baseline: baseline.difference(&union).cloned().collect(),
        baseline_full_frames: baseline,
        supplemental_full_frames: supplemental_frames,
        union_full_frames: union,
        baseline_window_trials,
        anchor_models: models,
        anchor_fit_rejections: rejections,
        supplemental_trials,
        supplemental_skips,
        stage_wall_seconds: StageWallTimes {
            baseline_decode_and_anchor_fit_seconds: baseline_elapsed,
            supplemental_decode_seconds: supplemental_elapsed,
            total_decode_seconds: total_started.elapsed().as_secs_f64(),
        },
    })
}

fn payload_bytes(frames: &BTreeSet<String>) -> usize {
    frames.iter().map(|frame| frame.len() / 2 - 2).sum()
}

pub fn decode_file(
    path: &Path,
    output: &Path,
    observation_id: Option<u64>,
    config: &AdaptiveConfig,
) -> Result<Value, String> {
    let started = Instant::now();
    if !(1..=16).contains(&config.threads) || !config.baud.is_finite() || config.baud <= 0.0 {
        return Err("adaptive baud must be positive finite and threads in 1..=16".into());
    }
    let out = input::existing_new_dir(output)?;
    let execution = (|| -> Result<Value, String> {
        let audio = input::load_audio(path, &out)?;
        config.validate(audio.sample_rate)?;
        let executable =
            input::identity(&std::env::current_exe().map_err(|error| error.to_string())?)?;
        input::write_json_new(
            &out.join("plan.json"),
            &json!({
                "schema":"adaptive-sequence-audio-plan-v1", "observation_id":observation_id,
                "config":config,"fixed_config":config.fixed(),"tracking_configs":clocks(),
                "frontends":FRONTENDS,"input":audio.wav_identity,"source":audio.source_identity,
                "conversion_command":audio.conversion_command,"executable":executable,
                "window_seconds":WINDOW_SECONDS,"hop_seconds":HOP_SECONDS,
                "anchor_guard_seconds":ANCHOR_GUARD_SECONDS,"maximum_anchor_start_distance_seconds":MAX_ANCHOR_DISTANCE_SECONDS,
                "training_guard_symbols":sequence::TRAINING_GUARD_SYMBOLS,"minimum_training_symbols":sequence::MIN_TRAINING_SYMBOLS,
                "channel_taps":3,"channel_bias":true,"model_selection":"lowest anchor training NMSE, then more training symbols, then timing rank",
                "target_anchor_selection":"nearest disjoint same-frontend window; ties use lower window index",
                "transferred_parameters":["channel taps","channel bias","anchor rate"],
                "transfer_phases":TRANSFER_PHASES,"local_top_timings":LOCAL_TIMINGS,"gains":GAINS,
                "viterbi_states":4,"traceback":"full","boundaries":"free initial and final states; first and last observations excluded from cost",
                "reference_bytes_used_for_search":false,"target_bits_used_for_training":false,
                "recursive_supplemental_training":false,"coherent_iq_processing":false,"bit_repair_enabled":false,
                "network_submission":false,"input_representation":"mono FM-demodulated audio",
                "protocol_scope":"AX.25 UI with received CRC; plain NRZI or G3RUH",
                "publication_ready":false,"deployment_ready":false
            }),
        )?;
        let report = decode_samples(&audio.samples, audio.sample_rate, config)?;
        let result = json!({
            "schema":"adaptive-sequence-audio-result-v1","status":"complete","observation_id":observation_id,
            "input":audio.wav_identity,"source":audio.source_identity,"sample_rate_hz":audio.sample_rate,
            "input_samples":audio.samples.len(),"duration_seconds":audio.samples.len() as f64 / f64::from(audio.sample_rate),
            "config":config,"baseline_count":report.baseline_full_frames.len(),
            "supplemental_count":report.supplemental_full_frames.len(),"union_count":report.union_full_frames.len(),
            "added_vs_baseline_count":report.added_vs_baseline.len(),"lost_vs_baseline_count":report.lost_vs_baseline.len(),
            "baseline_payload_bytes":payload_bytes(&report.baseline_full_frames),
            "union_payload_bytes":payload_bytes(&report.union_full_frames),
            "added_payload_bytes":payload_bytes(&report.added_vs_baseline),
            "total_wall_seconds":started.elapsed().as_secs_f64(),"report":report,
            "frame_provenance":"exact frame sets in baseline_window_trials and supplemental_trials; anchor IDs link to anchor_models",
            "trial_timing_note":"per-worker elapsed times overlap; fixed baseline trial includes anchor fitting; frontend cost repeated across baseline variants",
            "matched_isolated_baseline_timing":false,
            "native_validation":"received CRC16/X25, independent full-frame bitwise residue, AX.25 UI structure",
            "reference_bytes_used_for_search":false,"target_bits_used_for_training":false,
            "bit_repair_enabled":false,"coherent_iq_processing":false,
            "false_positive_qualification_complete":false,"publication_ready":false,"deployment_ready":false
        });
        input::write_json_new(&out.join("result.json"), &result)?;
        Ok(json!({
            "status":"complete","observation_id":observation_id,"result":out.join("result.json"),
            "baseline_count":result["baseline_count"],"supplemental_count":result["supplemental_count"],
            "union_count":result["union_count"],"added_vs_baseline_count":result["added_vs_baseline_count"],
            "lost_vs_baseline_count":result["lost_vs_baseline_count"],"added_payload_bytes":result["added_payload_bytes"],
            "anchor_count":result["report"]["anchor_models"].as_array().map(Vec::len),
            "total_wall_seconds":result["total_wall_seconds"],"publication_ready":false,"deployment_ready":false
        }))
    })();
    if let Err(error) = &execution {
        // The directory is exclusively ours; never overwrite a prior result.
        input::write_json_new(
            &out.join("result.json"),
            &json!({
                "schema":"adaptive-sequence-audio-result-v1","status":"failed","observation_id":observation_id,
                "error":error,"total_wall_seconds":started.elapsed().as_secs_f64(),
                "publication_ready":false,"deployment_ready":false
            }),
        )?;
    }
    execution
}

#[cfg(test)]
mod tests {
    use super::*;

    fn progressive_positive_samples() -> (Vec<f64>, BTreeSet<String>) {
        // Independently frozen framing oracle: these expected bytes are used
        // only by assertions, never passed to a decoder or fitting API.
        let oracle: Value =
            serde_json::from_str(include_str!("tests/protocol_oracle.json")).unwrap();
        let case = &oracle["cases"][0];
        let packed = hex::decode(case["levels_hex"].as_str().unwrap()).unwrap();
        let count = case["symbol_count"].as_u64().unwrap() as usize;
        let samples = (0..3)
            .flat_map(|_| {
                (0..count).flat_map(|index| {
                    let value = if (packed[index / 8] >> (7 - index % 8)) & 1 == 1 {
                        0.75
                    } else {
                        -0.75
                    };
                    std::iter::repeat_n(value, 5)
                })
            })
            .collect();
        let expected = serde_json::from_value(case["expected"][0]["frames_hex"].clone()).unwrap();
        (samples, expected)
    }

    #[test]
    fn progressive_fast_and_remainder_exactly_partition_original_positive_baseline() {
        let (samples, expected) = progressive_positive_samples();
        let config = AdaptiveConfig::default();
        let bounds = (0, samples.len());
        let original = first_window(
            &samples,
            48000,
            0,
            bounds,
            &config.fixed(),
            &clocks(),
            false,
        )
        .unwrap();
        let fast = progressive_baseline(&samples, 48000, 0, bounds, &config, true).unwrap();
        let remainder = progressive_baseline(&samples, 48000, 0, bounds, &config, false).unwrap();
        assert_eq!(
            fast.trials
                .iter()
                .map(|trial| trial.hypotheses)
                .sum::<usize>(),
            20
        );
        assert_eq!(
            remainder
                .trials
                .iter()
                .map(|trial| trial.hypotheses)
                .sum::<usize>(),
            332
        );
        let expected_original: BTreeSet<_> = original
            .trials
            .iter()
            .flat_map(|trial| trial.frame_with_fcs_hex.iter().cloned())
            .collect();
        assert!(
            !expected_original.is_empty(),
            "positive control must decode"
        );
        assert_eq!(expected_original, expected);
        for original_trial in &original.trials {
            let clock_family = original_trial.clock.split('-').next().unwrap();
            let matching: Vec<_> = fast
                .trials
                .iter()
                .chain(&remainder.trials)
                .filter(|trial| {
                    trial.frontend == original_trial.frontend
                        && trial
                            .clock
                            .split('-')
                            .next()
                            .unwrap()
                            .starts_with(clock_family.trim_end_matches("16"))
                })
                .collect();
            let combined: BTreeSet<_> = matching
                .iter()
                .flat_map(|trial| trial.frame_with_fcs_hex.iter().cloned())
                .collect();
            assert_eq!(combined, original_trial.frame_with_fcs_hex);
            assert_eq!(
                matching.iter().map(|trial| trial.hypotheses).sum::<usize>(),
                original_trial.hypotheses
            );
        }
        let candidates: Vec<_> = fast
            .anchors
            .iter()
            .chain(&remainder.anchors)
            .cloned()
            .collect();
        assert!(
            !candidates.is_empty(),
            "positive control must train real CRC anchors"
        );
        let merged = progressive_merge_anchors(&candidates);
        assert_eq!(
            serde_json::to_value(&merged).unwrap(),
            serde_json::to_value(&original.anchors).unwrap()
        );
        let mut reversed = candidates;
        reversed.reverse();
        assert_eq!(
            serde_json::to_value(progressive_merge_anchors(&reversed)).unwrap(),
            serde_json::to_value(merged).unwrap()
        );
        let encoded = serde_json::to_vec(&fast).unwrap();
        let decoded: ProgressiveBaseline = serde_json::from_slice(&encoded).unwrap();
        assert_eq!(serde_json::to_vec(&decoded).unwrap(), encoded);
    }

    #[test]
    fn progressive_partition_has_no_duplicate_hypothesis_indexes() {
        for count in [0, 1, 7, 8, 16, 160] {
            for prefix in [2, 8] {
                let fast: BTreeSet<_> = BaselinePartition::Fast.range(count, prefix).collect();
                let remaining: BTreeSet<_> =
                    BaselinePartition::Remaining.range(count, prefix).collect();
                assert!(fast.is_disjoint(&remaining));
                assert_eq!(
                    fast.union(&remaining).copied().collect::<Vec<_>>(),
                    (0..count).collect::<Vec<_>>()
                );
            }
        }
    }

    #[test]
    fn progressive_validates_window_before_indexing_and_bounds_work() {
        let samples = vec![0.0; 48000 * 7];
        let config = AdaptiveConfig::default();
        for bounds in [(0, 0), (3, 2), (0, samples.len() + 1), (0, 48000 * 6 + 1)] {
            assert!(progressive_baseline(&samples, 48000, 0, bounds, &config, true).is_err());
            assert!(progressive_supplemental(&samples, 48000, 0, bounds, &config, &[]).is_err());
            assert!(
                progressive_new_channel(&samples, 48000, 0, bounds, &config, &[], false, false)
                    .is_err()
            );
        }
        assert!(progressive_baseline(&[f64::NAN; 100], 48000, 0, (0, 100), &config, true).is_err());
    }

    #[test]
    fn progressive_new_channel_silence_never_manufactures_a_model_or_frame() {
        let samples = vec![0.0; 8192];
        let report = progressive_new_channel(
            &samples,
            48000,
            0,
            (0, samples.len()),
            &AdaptiveConfig::default(),
            &[],
            true,
            true,
        )
        .unwrap();
        assert!(report.trials.is_empty());
        assert!(report.skips.len() >= FRONTENDS.len());
        let encoded = serde_json::to_vec(&report).unwrap();
        let decoded: ProgressiveChannel = serde_json::from_slice(&encoded).unwrap();
        assert_eq!(serde_json::to_vec(&decoded).unwrap(), encoded);
    }

    #[test]
    fn progressive_local_windows_match_full_inputs_with_absolute_anchor_provenance() {
        let (local, _) = progressive_positive_samples();
        let config = AdaptiveConfig::default();
        let offsets = [48000, 48000 * 8, 48000 * 16];
        let target_bounds = (offsets[2], offsets[2] + local.len());
        let mut recording = vec![0.0; target_bounds.1];
        for offset in offsets {
            recording[offset..offset + local.len()].copy_from_slice(&local);
        }
        let mut models = Vec::new();
        for (index, offset) in offsets[..2].iter().enumerate() {
            let source = progressive_baseline(
                &recording,
                48000,
                index,
                (*offset, *offset + local.len()),
                &config,
                true,
            )
            .unwrap();
            assert!(
                !source.anchors.is_empty(),
                "source must contain real CRC anchor"
            );
            models.extend(source.anchors);
        }
        for fast in [true, false] {
            let full =
                progressive_baseline(&recording, 48000, 7, target_bounds, &config, fast).unwrap();
            let sliced =
                progressive_baseline_local(&local, 48000, 7, target_bounds, &config, fast).unwrap();
            assert_eq!(
                without_times(serde_json::to_value(&full).unwrap()),
                without_times(serde_json::to_value(&sliced).unwrap())
            );
            for anchor in &sliced.anchors {
                assert_eq!(
                    (anchor.window_start_sample, anchor.window_end_sample),
                    target_bounds
                );
                assert_eq!(anchor.window_start_seconds, 16.0);
                assert_eq!(anchor.window_end_seconds, target_bounds.1 as f64 / 48000.0);
                assert!(
                    anchor
                        .training_spans
                        .iter()
                        .all(|span| span.end_symbol < local.len())
                );
            }
        }
        let full = progressive_supplemental(&recording, 48000, 7, target_bounds, &config, &models)
            .unwrap();
        let sliced =
            progressive_supplemental_local(&local, 48000, 7, target_bounds, &config, &models)
                .unwrap();
        assert!(
            !sliced.is_empty(),
            "nearest branch must exercise a selected anchor"
        );
        assert_eq!(
            without_times(serde_json::to_value(&full).unwrap()),
            without_times(serde_json::to_value(&sliced).unwrap())
        );
        assert!(
            sliced
                .iter()
                .all(|trial| trial.window_start_seconds == 16.0)
        );
        let full = progressive_new_channel(
            &recording,
            48000,
            7,
            target_bounds,
            &config,
            &models,
            true,
            true,
        )
        .unwrap();
        let sliced = progressive_new_channel_local(
            &local,
            48000,
            7,
            target_bounds,
            &config,
            &models,
            true,
            true,
        )
        .unwrap();
        assert!(
            sliced
                .trials
                .iter()
                .any(|trial| trial.method == "multi-anchor")
        );
        assert_eq!(
            without_times(serde_json::to_value(&full).unwrap()),
            without_times(serde_json::to_value(&sliced).unwrap())
        );
        assert!(
            sliced
                .trials
                .iter()
                .all(|trial| trial.window_start_seconds == 16.0)
        );
    }

    #[test]
    fn prepared_progressive_window_matches_every_uncached_lane_and_anchor_generation() {
        let (local, _) = progressive_positive_samples();
        let config = AdaptiveConfig::default();
        let cache = PreparedWindow::new(&local, 48000, &config).unwrap();
        assert!(cache.retained_bytes() <= local.len() * 16 + 32768);
        for name in FRONTENDS {
            let expected = frontend(&local, 48000, &config.fixed(), name).unwrap();
            let (actual, timings) = cache.lane(name);
            assert_eq!(
                actual
                    .samples
                    .iter()
                    .map(|x| x.to_bits())
                    .collect::<Vec<_>>(),
                expected
                    .samples
                    .iter()
                    .map(|x| x.to_bits())
                    .collect::<Vec<_>>()
            );
            assert_eq!(
                serde_json::to_vec(timings).unwrap(),
                serde_json::to_vec(&dsp::timing_bank(&expected, &config.fixed()).unwrap()).unwrap()
            );
        }
        let mut models = Vec::new();
        for (i, start) in [48000, 48000 * 8].into_iter().enumerate() {
            models.extend(
                progressive_baseline_local(
                    &local,
                    48000,
                    i,
                    (start, start + local.len()),
                    &config,
                    true,
                )
                .unwrap()
                .anchors,
            );
        }
        assert!(!models.is_empty());
        let bounds = (48000 * 16, 48000 * 16 + local.len());
        let same = |a: Value, b: Value| {
            assert_eq!(
                serde_json::to_vec(&without_times(a)).unwrap(),
                serde_json::to_vec(&without_times(b)).unwrap()
            )
        };
        for fast in [true, false] {
            let a = progressive_baseline_local(&local, 48000, 7, bounds, &config, fast).unwrap();
            let b = progressive_baseline_local_prepared(
                &local,
                48000,
                7,
                bounds,
                &config,
                fast,
                Some(&cache),
            )
            .unwrap();
            same(
                serde_json::to_value(a).unwrap(),
                serde_json::to_value(b).unwrap(),
            );
        }
        // Empty, early single-source and full multi-source generations. The
        // prepared waveform must not memoize model selection or stage results.
        for count in [0, 1, models.len()] {
            let anchors = &models[..count];
            let a =
                progressive_supplemental_local(&local, 48000, 7, bounds, &config, anchors).unwrap();
            let b = progressive_supplemental_local_prepared(
                &local,
                48000,
                7,
                bounds,
                &config,
                anchors,
                Some(&cache),
            )
            .unwrap();
            same(
                serde_json::to_value(a).unwrap(),
                serde_json::to_value(b).unwrap(),
            );
            for (blind, multi) in [(true, false), (false, true), (true, true)] {
                let a = progressive_new_channel_local(
                    &local, 48000, 7, bounds, &config, anchors, blind, multi,
                )
                .unwrap();
                let b = progressive_new_channel_local_prepared(
                    &local,
                    48000,
                    7,
                    bounds,
                    &config,
                    anchors,
                    blind,
                    multi,
                    Some(&cache),
                )
                .unwrap();
                same(
                    serde_json::to_value(a).unwrap(),
                    serde_json::to_value(b).unwrap(),
                );
            }
        }
    }

    #[test]
    fn prepared_window_rejects_changed_pcm_rate_baud_and_validates_even_without_anchor() {
        let config = AdaptiveConfig::default();
        let mut pcm = vec![0.0; 8192];
        let cache = PreparedWindow::new(&pcm, 48000, &config).unwrap();
        pcm[100] = -0.0;
        assert!(cache.verify(&pcm, 48000, &config).is_err());
        assert!(
            progressive_supplemental_local_prepared(
                &pcm,
                48000,
                0,
                (0, 8192),
                &config,
                &[],
                Some(&cache)
            )
            .is_err()
        );
        pcm[100] = 0.0;
        assert!(cache.verify(&pcm, 96000, &config).is_err());
        let mut changed = config.clone();
        changed.baud = 9500.0;
        assert!(cache.verify(&pcm, 48000, &changed).is_err());
        changed = config.clone();
        changed.threads = 4;
        assert!(cache.verify(&pcm, 48000, &changed).is_ok());
        pcm[100] = f64::NAN;
        assert!(PreparedWindow::new(&pcm, 48000, &config).is_err());
    }

    #[test]
    fn progressive_local_rejects_mismatched_reversed_or_overflowing_absolute_bounds() {
        let local = vec![0.0; 1024];
        let config = AdaptiveConfig::default();
        for bounds in [(0, 1023), (7, 1032), (1024, 0), (usize::MAX, 1023)] {
            assert!(progressive_baseline_local(&local, 48000, 0, bounds, &config, true).is_err());
            assert!(
                progressive_supplemental_local(&local, 48000, 0, bounds, &config, &[]).is_err()
            );
            assert!(
                progressive_new_channel_local(&local, 48000, 0, bounds, &config, &[], true, true)
                    .is_err()
            );
        }
    }

    #[test]
    fn optional_frontend_cache_never_prunes_windows_when_budget_is_exhausted() {
        let bounds = [(0, 100), (50, 150), (100, 120)];
        assert_eq!(cache_admission(&bounds, 0), vec![false; 3]);
        assert_eq!(cache_admission(&bounds, 1600), vec![true, false, false]);
        assert_eq!(cache_admission(&bounds, 1920), vec![true, false, true]);
        assert_eq!(cache_admission(&bounds, 3520), vec![true; 3]);
    }

    fn without_times(mut value: Value) -> Value {
        match &mut value {
            Value::Object(map) => {
                map.remove("elapsed_seconds");
                for child in map.values_mut() {
                    *child = without_times(child.take());
                }
            }
            Value::Array(items) => {
                for child in items {
                    *child = without_times(child.take());
                }
            }
            _ => {}
        }
        value
    }

    #[test]
    fn cached_and_recomputed_frontends_produce_identical_supplemental_trials() {
        let samples: Vec<f64> = (0..12000)
            .map(|i| ((i * 719 % 1009) as f64 - 504.0) / 505.0)
            .collect();
        let fixed = AdaptiveConfig::default().fixed();
        let mut retained = Vec::new();
        let mut uncached = Vec::new();
        let mut models = Vec::new();
        for name in FRONTENDS {
            let front = frontend(&samples, 48000, &fixed, name).unwrap();
            let timings = dsp::timing_bank(&front, &fixed).unwrap();
            let local: Vec<_> = timings.into_iter().take(LOCAL_TIMINGS).collect();
            retained.push(CachedFrontend {
                front: Some(Cow::Owned(front)),
                local_timings: local.clone(),
            });
            uncached.push(CachedFrontend {
                front: None,
                local_timings: local,
            });
            models.push(test_anchor(7, 60000, 72000, name));
        }
        let a = supplemental_window(
            &samples,
            48000,
            0,
            (0, samples.len()),
            &fixed,
            &models,
            &retained,
        )
        .unwrap();
        let b = supplemental_window(
            &samples,
            48000,
            0,
            (0, samples.len()),
            &fixed,
            &models,
            &uncached,
        )
        .unwrap();
        assert!(!a.0.is_empty());
        assert!(a.1.is_empty());
        assert_eq!(
            serde_json::to_vec(&without_times(serde_json::to_value(a).unwrap())).unwrap(),
            serde_json::to_vec(&without_times(serde_json::to_value(b).unwrap())).unwrap()
        );
        let progressive = progressive_supplemental(
            &samples,
            48000,
            0,
            (0, samples.len()),
            &AdaptiveConfig::default(),
            &models,
        )
        .unwrap();
        let original = supplemental_window(
            &samples,
            48000,
            0,
            (0, samples.len()),
            &fixed,
            &models,
            &retained,
        )
        .unwrap()
        .0;
        assert_eq!(
            without_times(serde_json::to_value(progressive).unwrap()),
            without_times(serde_json::to_value(original).unwrap()),
        );
    }

    fn test_anchor(index: usize, start: usize, end: usize, frontend: &str) -> AnchorModel {
        AnchorModel {
            id: format!("window-{index}/{frontend}"),
            window_index: index,
            frontend: frontend.into(),
            window_start_sample: start,
            window_end_sample: end,
            window_start_seconds: start as f64,
            window_end_seconds: end as f64,
            timing_rank: 0,
            timing: dsp::TimingHypothesis {
                rate_error_ppm: 0.0,
                phase_samples: 0.5,
                step_samples: 2.5,
                threshold: 0.0,
                score: 1.0,
                symbol_count: 1000,
            },
            model: sequence::ChannelModel {
                taps: [0.1, 1.0, 0.1],
                bias: 0.0,
                training_symbols: 128,
                normalized_mse: 0.0,
            },
            training_spans: Vec::new(),
        }
    }

    #[test]
    fn anchor_selection_excludes_self_overlap_nearby_wrong_frontend_and_far_windows() {
        let name = FRONTENDS[0];
        let candidates = vec![
            test_anchor(5, 10, 16, name), // self, even if coordinates are forged distant
            test_anchor(6, 38, 44, name), // overlapping [40,46)
            test_anchor(7, 34, 40, name), // touching, no one-second guard
            test_anchor(8, 47, 53, FRONTENDS[1]), // wrong frontend
            test_anchor(9, 101, 107, name), // start distance 61
        ];
        assert!(select_anchor(&candidates, 5, (40, 46), name, 1).is_none());
        let eligible = vec![test_anchor(3, 33, 39, name), test_anchor(4, 47, 53, name)];
        assert_eq!(
            select_anchor(&eligible, 5, (40, 46), name, 1)
                .unwrap()
                .window_index,
            3
        );
        assert!(select_anchor(&eligible, 5, (40, 46), name, 2).is_none());
        let at_limit = vec![test_anchor(2, 100, 106, name)];
        assert!(select_anchor(&at_limit, 5, (40, 46), name, 1).is_some());
    }

    #[test]
    fn progressive_multiple_anchors_reject_self_overlap_and_reused_training_windows() {
        let name = FRONTENDS[0];
        let mut candidates = vec![
            test_anchor(1, 30, 36, name),
            test_anchor(2, 33, 39, name),
            test_anchor(3, 47, 53, name),
            test_anchor(4, 50, 56, name),
            test_anchor(5, 40, 46, name),
            test_anchor(6, 56, 62, name),
            test_anchor(7, 47, 53, FRONTENDS[1]),
            test_anchor(8, 101, 107, name),
        ];
        // Candidates without validated-span provenance cannot be used here.
        assert!(progressive_multi_anchors(&candidates, 5, (40, 46), name, 1).is_empty());
        for candidate in &mut candidates {
            candidate.training_spans.push(TrainingSpan {
                start_symbol: 20,
                end_symbol: 180,
                g3ruh: false,
                received_frame_sha256: "test-selection-only".into(),
            });
        }
        let selected = progressive_multi_anchors(&candidates, 5, (40, 46), name, 1);
        assert_eq!(
            selected
                .iter()
                .map(|anchor| anchor.window_index)
                .collect::<Vec<_>>(),
            vec![2, 3, 6]
        );
        for (index, anchor) in selected.iter().enumerate() {
            for other in &selected[index + 1..] {
                assert!(
                    anchor.window_end_sample + 1 <= other.window_start_sample
                        || other.window_end_sample + 1 <= anchor.window_start_sample
                );
            }
        }
    }

    #[test]
    fn progressive_blend_trial_records_only_actual_contributors_and_excluded_candidates() {
        let samples: Vec<f64> = (0..12000)
            .map(|i| ((i * 719 % 1009) as f64 - 504.0) / 505.0)
            .collect();
        let mut candidates: Vec<_> = (1..=3)
            .map(|index| test_anchor(index, index * 96000, index * 96000 + 12000, FRONTENDS[0]))
            .collect();
        for candidate in &mut candidates {
            candidate.model.taps = [0.5, 0.8, 0.5];
            candidate.training_spans.push(TrainingSpan {
                start_symbol: 20,
                end_symbol: 180,
                g3ruh: false,
                received_frame_sha256: "test-provenance-only".into(),
            });
        }
        candidates[2].model.taps = [-0.5, 0.8, -0.5];
        let result = progressive_new_channel(
            &samples,
            48000,
            0,
            (0, samples.len()),
            &AdaptiveConfig::default(),
            &candidates,
            false,
            true,
        )
        .unwrap();
        assert_eq!(result.trials.len(), LOCAL_TIMINGS * GAINS.len());
        for trial in &result.trials {
            assert_eq!(trial.method, "multi-anchor");
            assert!(trial.blind_fit.is_none());
            assert_eq!(trial.candidate_anchor_ids.len(), 3);
            assert_eq!(
                trial.anchor_ids,
                [candidates[0].id.clone(), candidates[1].id.clone()]
            );
            assert_eq!(
                trial.blend_fit.as_ref().unwrap().excluded_input_indices,
                [2]
            );
        }
        let encoded = serde_json::to_vec(&result).unwrap();
        let decoded: ProgressiveChannel = serde_json::from_slice(&encoded).unwrap();
        assert_eq!(serde_json::to_vec(&decoded).unwrap(), encoded);
    }

    #[test]
    fn validation_happens_before_silence_can_skip_a_lane() {
        let samples = vec![0.0; 8192];
        for threads in [0, 17] {
            assert!(
                decode_samples(
                    &samples,
                    48000,
                    &AdaptiveConfig {
                        threads,
                        ..Default::default()
                    }
                )
                .is_err()
            );
        }
        for baud in [0.0, -1.0, f64::NAN, f64::INFINITY, 20000.0, 100.0] {
            assert!(
                decode_samples(
                    &samples,
                    48000,
                    &AdaptiveConfig {
                        baud,
                        ..Default::default()
                    }
                )
                .is_err()
            );
        }
        assert!(decode_samples(&samples, 0, &AdaptiveConfig::default()).is_err());
        assert!(decode_samples(&[f64::NAN; 8192], 48000, &AdaptiveConfig::default()).is_err());
        assert!(decode_samples(&[f64::INFINITY; 8192], 48000, &AdaptiveConfig::default()).is_err());
        assert!(decode_samples(&[], 48000, &AdaptiveConfig::default()).is_err());
        assert!(!expected_model_rejection(
            "channel parameters must be finite and bounded"
        ));
        assert!(!expected_model_rejection("unexpected numerical failure"));
        assert!(expected_model_rejection(
            "channel training needs at least 128 distinct guarded symbols"
        ));
    }

    #[test]
    fn no_anchor_silence_is_explicit_and_deterministic_across_worker_counts() {
        let samples = vec![0.0; 48000 * 7];
        let a = decode_samples(
            &samples,
            48000,
            &AdaptiveConfig {
                threads: 1,
                ..Default::default()
            },
        )
        .unwrap();
        let b = decode_samples(
            &samples,
            48000,
            &AdaptiveConfig {
                threads: 4,
                ..Default::default()
            },
        )
        .unwrap();
        assert_eq!(a.window_count, 2);
        assert_eq!(
            a.baseline_variant_full_frames,
            b.baseline_variant_full_frames
        );
        assert_eq!(a.union_full_frames, b.union_full_frames);
        assert!(a.union_full_frames.is_empty());
        assert!(a.anchor_models.is_empty());
        assert!(a.supplemental_trials.is_empty());
        assert!(a.added_vs_baseline.is_empty() && a.lost_vs_baseline.is_empty());
        assert_eq!(a.supplemental_skips.len(), 4);
        assert_eq!(
            serde_json::to_value(a.supplemental_skips).unwrap(),
            serde_json::to_value(b.supplemental_skips).unwrap()
        );
    }

    #[test]
    fn transfer_bank_is_bounded_and_has_eight_even_anchor_phases() {
        let front = dsp::Frontend {
            samples: (0..4096)
                .map(|index| ((index as f64) * 0.7).sin())
                .collect(),
            samples_per_symbol: 2.5,
        };
        let config = AdaptiveConfig::default().fixed();
        let local = dsp::timing_bank(&front, &config).unwrap();
        let bank = supplemental_bank(&front, &config, 100.0, &local).unwrap();
        assert!(bank.len() <= TRANSFER_PHASES + LOCAL_TIMINGS);
        let expected_step = front.samples_per_symbol * (1.0 + 100.0 * 1.0e-6);
        for phase in 0..8 {
            let expected_phase = expected_step * (phase as f64 + 0.5) / 8.0;
            assert!(
                bank.iter()
                    .any(|timing| timing.step_samples == expected_step
                        && timing.phase_samples == expected_phase)
            );
        }
    }
}
