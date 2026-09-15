//! Experimental matched-lane residual/codec-aware audio receiver.
//!
//! Source CRC is used to locate training symbols, never to optimize a target.
//! The entire pre-existing adaptive union is retained. No qualified decoder
//! default, progressive task policy or acceptance rule is changed.

use crate::{
    adaptive, anchors, codec_pool, codec_reliability, dsp, innovation, input, receiver, sequence,
};
use rayon::prelude::*;
use serde::Serialize;
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::{
    collections::{BTreeMap, BTreeSet},
    path::Path,
    time::Instant,
};

const FRONTENDS: [&str; 2] = ["legacy-fir512", "boxcar32-rms50"];
const GAINS: [f64; 3] = [0.75, 1.0, 1.5];
const SPLIT_GUARD: usize = 64;

#[derive(Clone, Debug, Serialize)]
pub struct SourceModel {
    pub anchor: adaptive::AnchorModel,
    pub channel: sequence::ChannelModel,
    pub noise: innovation::NoiseModel,
    pub codec_fit: Option<codec_reliability::VarianceFit>,
    pub channel_training_spans: Vec<(usize, usize)>,
    pub residual_spans: Vec<(usize, usize)>,
    pub noise_fit_spans: Vec<(usize, usize)>,
    pub residual_sha256: String,
    // Original positions and residuals remain in memory only; the pool report
    // retains source IDs, hashes, normalization and selection provenance.
    #[serde(skip)]
    pool_source: Option<codec_pool::PoolSource>,
}

#[derive(Clone, Debug, Serialize)]
pub struct InnovationTrial {
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
    /// Same accepted-pool target/timing cohort; the unweighted control does not
    /// use this calibration in its metric.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub pooled_comparison_id: Option<String>,
}

#[derive(Serialize)]
pub struct InnovationReport {
    pub baseline: adaptive::AdaptiveReport,
    pub source_models: Vec<SourceModel>,
    pub source_rejections: Vec<Value>,
    pub trials: Vec<InnovationTrial>,
    pub skips: Vec<Value>,
    pub lane_full_frames: BTreeMap<String, BTreeSet<String>>,
    pub union_full_frames: BTreeSet<String>,
    pub added_vs_existing_adaptive: BTreeSet<String>,
    pub innovations_added_vs_matched_white: BTreeSet<String>,
    pub innovations_lost_vs_matched_white: BTreeSet<String>,
    pub codec_added_vs_innovations: BTreeSet<String>,
    pub pooled_codec_enabled: bool,
    pub pooled_calibrations: Vec<Value>,
    pub pooled_matched_unweighted_full_frames: BTreeSet<String>,
    pub pooled_added_vs_matched_unweighted: BTreeSet<String>,
    pub pooled_lost_vs_matched_unweighted: BTreeSet<String>,
    pub previous_innovation_union_full_frames: BTreeSet<String>,
    pub added_vs_previous_innovation: BTreeSet<String>,
    pub model_preparation_seconds: f64,
    pub new_lanes_seconds: f64,
    pub total_decode_seconds: f64,
}

/// Dominant impulse-response center in original PCM coordinates, not the full
/// frontend support. DC/AGC dependence is wider; this is a codec feature proxy.
pub fn symbol_pcm_position(
    frontend: &str,
    sample_rate: u32,
    baud: f64,
    window_left: usize,
    timing: &dsp::TimingHypothesis,
    symbol: usize,
) -> Result<f64, String> {
    if sample_rate == 0
        || !baud.is_finite()
        || baud <= 0.0
        || !timing.phase_samples.is_finite()
        || !timing.step_samples.is_finite()
        || timing.phase_samples < 0.0
        || timing.step_samples <= 0.0
        || symbol >= timing.symbol_count
    {
        return Err("invalid absolute symbol mapping geometry".into());
    }
    let sps = f64::from(sample_rate) / baud;
    let position = timing.phase_samples + symbol as f64 * timing.step_samples;
    let local = match frontend {
        "legacy-fir512" => {
            let blocker = (512.0 * sps / 2.0).round_ties_even().max(32.0);
            // FIR emits at PCM114,116,...; remove blocker-1 outputs. Its
            // dominant symmetric FIR impulse center is57samples earlier.
            57.0 + 2.0 * (blocker - 1.0 + position)
        }
        "boxcar32-rms50" => {
            let pulse = sps.floor();
            let dc = (32.0 * sps).ceil();
            2.0 * (dc - 1.0) + (pulse - 1.0) / 2.0 + position
        }
        _ => return Err("unknown frontend for absolute symbol mapping".into()),
    };
    let result = window_left as f64 + local;
    if !result.is_finite() || result < window_left as f64 {
        return Err("absolute symbol mapping overflow".into());
    }
    Ok(result)
}

fn frame_set(soft: &[f64]) -> Result<BTreeSet<String>, String> {
    Ok(anchors::validated_spans(soft, 0.0)?
        .into_iter()
        .map(|span| hex::encode(span.frame))
        .collect())
}

fn noise_training_prefixes(residuals: &[Vec<f64>]) -> Vec<Vec<f64>> {
    residuals
        .iter()
        .map(|values| values[..values.len() * 3 / 5].to_vec())
        .collect()
}

fn source_model(
    samples: &[f64],
    rate: u32,
    config: &adaptive::AdaptiveConfig,
    anchor: &adaptive::AnchorModel,
    codec: Option<&codec_reliability::CodecMetadata>,
    collect_pool: bool,
) -> Result<SourceModel, String> {
    let pcm = samples
        .get(anchor.window_start_sample..anchor.window_end_sample)
        .ok_or("anchor is outside input samples")?;
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
            return Err(
                "source anchor changed or failed independent received-CRC revalidation".into(),
            );
        }
    }
    let levels: Vec<u8> = soft
        .iter()
        .map(|sample| u8::from(*sample >= anchor.timing.threshold))
        .collect();
    let mut coordinates: Vec<_> = verified.iter().map(|span| (span.start, span.end)).collect();
    coordinates.sort_unstable();
    coordinates.dedup();
    let mut channel_spans = Vec::new();
    let mut residual_spans = Vec::new();
    let mut used_frame_hashes = BTreeSet::new();
    let mut previous_end = 0;
    for (start, end) in coordinates {
        if start < previous_end {
            continue;
        } // never double count overlap
        previous_end = end;
        if end - start < 640 {
            continue;
        }
        let split = start + (end - start) * 2 / 5;
        for span in verified
            .iter()
            .filter(|span| span.start == start && span.end == end)
        {
            used_frame_hashes.insert(hex::encode(Sha256::digest(&span.frame)));
        }
        channel_spans.push((start, split));
        residual_spans.push((split + SPLIT_GUARD, end - sequence::TRAINING_GUARD_SYMBOLS));
    }
    if channel_spans.is_empty() {
        return Err(
            "source rejected: no native frame long enough for guarded channel/residual split"
                .into(),
        );
    }
    let channel = sequence::fit_channel(&soft, &levels, &channel_spans)
        .map_err(|reason| format!("source rejected: channel fit: {reason}"))?;
    let mut residuals = Vec::new();
    let mut digest = Sha256::new();
    for &(left, right) in &residual_spans {
        let values: Vec<f64> = (left..right)
            .map(|i| {
                let prediction = channel.bias
                    + channel
                        .taps
                        .iter()
                        .enumerate()
                        .map(|(tap, h)| h * (2.0 * f64::from(levels[i + tap - 1]) - 1.0))
                        .sum::<f64>();
                soft[i] - prediction
            })
            .collect();
        digest.update((left as u64).to_le_bytes());
        digest.update((right as u64).to_le_bytes());
        for value in &values {
            digest.update(value.to_le_bytes());
        }
        residuals.push(values);
    }
    // Codec validation suffix must not participate even in AR order selection.
    // Keep this split fixed also in non-codec runs for a matched noise lane.
    let noise_residuals = noise_training_prefixes(&residuals);
    let noise_fit_spans = residual_spans
        .iter()
        .map(|&(left, right)| (left, left + (right - left) * 3 / 5))
        .collect();
    let noise = innovation::fit_noise_model(&noise_residuals)
        .map_err(|reason| format!("source rejected: residual fit: {reason}"))?;
    let mut pool_source = None;
    let codec_fit = if let Some(metadata) = codec {
        let mut training = Vec::new();
        let mut heldout = Vec::new();
        for (index, (values, &(left, _))) in residuals.iter().zip(&residual_spans).enumerate() {
            let split = values.len() * 3 / 5;
            for (label, begin, end, destination) in [
                ("training", noise.selected_order, split, &mut training),
                ("heldout", split + SPLIT_GUARD, values.len(), &mut heldout),
            ] {
                if end.saturating_sub(begin) < 32 {
                    continue;
                }
                let mut positions = Vec::new();
                let mut innovations = Vec::new();
                for i in begin..end {
                    let innovation = values[i]
                        - noise
                            .ar_coefficients
                            .iter()
                            .enumerate()
                            .map(|(lag, a)| a * values[i - lag - 1])
                            .sum::<f64>();
                    positions.push(symbol_pcm_position(
                        &anchor.frontend,
                        rate,
                        config.baud,
                        anchor.window_start_sample,
                        &anchor.timing,
                        left + i,
                    )?);
                    innovations.push(innovation);
                }
                destination.push(codec_reliability::CalibrationBlock {
                    id: format!("{}/{index}/{label}", anchor.id),
                    absolute_samples: positions,
                    residuals: innovations,
                });
            }
        }
        let train_count = training.iter().map(|b| b.residuals.len()).sum::<usize>();
        let check_count = heldout.iter().map(|b| b.residuals.len()).sum::<usize>();
        if collect_pool {
            pool_source = Some(codec_pool::PoolSource {
                id: anchor.id.clone(),
                source_sha256: metadata.source.sha256.clone(),
                frontend: anchor.frontend.clone(),
                window_start_sample: anchor.window_start_sample,
                window_end_sample: anchor.window_end_sample,
                received_frame_sha256: used_frame_hashes.into_iter().collect(),
                training: training.clone(),
                heldout: heldout.clone(),
            });
        }
        if training.len() + heldout.len() > 32
            || !(128..=131072).contains(&train_count)
            || !(128..=131072).contains(&check_count)
        {
            Some(codec_reliability::VarianceFit {
                accepted: false,
                reason: "insufficient or excessive disjoint CRC calibration evidence".into(),
                model: None,
                training_ids: training.iter().map(|b| b.id.clone()).collect(),
                heldout_ids: heldout.iter().map(|b| b.id.clone()).collect(),
                training_samples: train_count,
                heldout_samples: check_count,
                heldout_log_score_gain_per_sample: 0.0,
                heldout_block_gains: vec![],
            })
        } else {
            Some(codec_reliability::fit_variance(
                metadata, &training, &heldout,
            )?)
        }
    } else {
        None
    };
    Ok(SourceModel {
        anchor: anchor.clone(),
        channel,
        noise,
        codec_fit,
        channel_training_spans: channel_spans,
        residual_spans,
        noise_fit_spans,
        residual_sha256: hex::encode(digest.finalize()),
        pool_source,
    })
}

struct WindowResult {
    trials: Vec<InnovationTrial>,
    skips: Vec<Value>,
    pooled_calibrations: Vec<Value>,
}

#[allow(clippy::too_many_arguments)]
fn target_window(
    samples: &[f64],
    rate: u32,
    config: &adaptive::AdaptiveConfig,
    index: usize,
    bounds: (usize, usize),
    source_models: &[SourceModel],
    eligible: &[adaptive::AnchorModel],
    codec: Option<&codec_reliability::CodecMetadata>,
    pool_sources: Option<&[codec_pool::PoolSource]>,
) -> Result<WindowResult, String> {
    let mut trials = Vec::new();
    let mut skips = Vec::new();
    let mut pooled_calibrations = Vec::new();
    let fixed = config.fixed();
    let mut white_workspace = sequence::SequenceWorkspace::new();
    let mut innovation_workspace = innovation::InnovationWorkspace::new();
    for frontend in FRONTENDS {
        let Some(anchor) = adaptive::select_anchor(eligible, index, bounds, frontend, rate) else {
            skips.push(json!({"window_index":index,"frontend":frontend,
                "reason":"no disjoint calibrated source within60s with1s guard"}));
            continue;
        };
        let source = source_models
            .iter()
            .find(|source| source.anchor.id == anchor.id)
            .ok_or("eligible source model missing")?;
        let pool_fit = if let (Some(metadata), Some(pool_sources)) = (codec, pool_sources) {
            let started = Instant::now();
            let fit = codec_pool::fit_pool(metadata, pool_sources, frontend, bounds, rate)?;
            pooled_calibrations.push(json!({
                "id":format!("window-{index}/{frontend}/pool"),
                "window_index":index,"frontend":frontend,
                "elapsed_seconds":started.elapsed().as_secs_f64(),"fit":fit
            }));
            Some(fit)
        } else {
            None
        };
        let pool_variance = pool_fit
            .as_ref()
            .and_then(|fit| fit.variance_fit.as_ref())
            .filter(|fit| fit.accepted);
        let pooled_comparison_id = pool_variance.map(|_| format!("window-{index}/{frontend}/pool"));
        let front = adaptive::frontend(&samples[bounds.0..bounds.1], rate, &fixed, frontend)?;
        let local = dsp::timing_bank(&front, &fixed)?;
        let bank =
            adaptive::supplemental_bank(&front, &fixed, anchor.timing.rate_error_ppm, &local)?;
        let validated = dsp::ValidatedFrontend::new(&front)?;
        let mut soft = Vec::new();
        for (timing_rank, timing) in bank.iter().enumerate() {
            validated.soft_symbols_into(timing, &mut soft)?;
            let weights = if let (Some(metadata), Some(fit)) = (codec, &source.codec_fit) {
                if fit.accepted {
                    Some(
                        (0..soft.len())
                            .map(|i| {
                                let position = symbol_pcm_position(
                                    frontend,
                                    rate,
                                    config.baud,
                                    bounds.0,
                                    timing,
                                    i,
                                )?;
                                fit.model
                                    .as_ref()
                                    .ok_or("accepted codec fit has no model")?
                                    .multiplier(metadata, position)
                            })
                            .collect::<Result<Vec<f64>, String>>()?,
                    )
                } else {
                    None
                }
            } else {
                None
            };
            let pool_weights = if let (Some(metadata), Some(fit)) = (codec, pool_variance) {
                let model = fit
                    .model
                    .as_ref()
                    .ok_or("accepted pool fit has no variance model")?;
                Some(
                    (0..soft.len())
                        .map(|i| {
                            model.multiplier(
                                metadata,
                                symbol_pcm_position(
                                    frontend,
                                    rate,
                                    config.baud,
                                    bounds.0,
                                    timing,
                                    i,
                                )?,
                            )
                        })
                        .collect::<Result<Vec<_>, String>>()?,
                )
            } else {
                None
            };
            for gain in GAINS {
                for lane in [
                    "matched-white",
                    "innovations",
                    "codec-innovations",
                    "pooled-codec-innovations",
                ] {
                    if lane == "codec-innovations" && weights.is_none() {
                        continue;
                    }
                    if lane == "pooled-codec-innovations" && pool_weights.is_none() {
                        continue;
                    }
                    let started = Instant::now();
                    let decisions = if lane == "matched-white" {
                        sequence::detect_sequence_with_workspace(
                            &soft,
                            &source.channel,
                            gain,
                            &mut white_workspace,
                        )?
                    } else {
                        innovation::innovations_sequence_with_workspace(
                            &soft,
                            &source.channel,
                            &source.noise,
                            gain,
                            if lane == "codec-innovations" {
                                weights.as_deref()
                            } else if lane == "pooled-codec-innovations" {
                                pool_weights.as_deref()
                            } else {
                                None
                            },
                            &mut innovation_workspace,
                        )?
                    };
                    let frames = frame_set(decisions)?;
                    trials.push(InnovationTrial {
                        window_index: index,
                        window_start_seconds: bounds.0 as f64 / f64::from(rate),
                        window_end_seconds: bounds.1 as f64 / f64::from(rate),
                        frontend: frontend.into(),
                        anchor_id: anchor.id.clone(),
                        timing_rank,
                        timing: timing.clone(),
                        gain,
                        lane: lane.into(),
                        elapsed_seconds: started.elapsed().as_secs_f64(),
                        frame_with_fcs_hex: frames,
                        pooled_comparison_id: pooled_comparison_id.clone(),
                    });
                }
            }
        }
    }
    Ok(WindowResult {
        trials,
        skips,
        pooled_calibrations,
    })
}

pub fn decode_samples(
    samples: &[f64],
    sample_rate: u32,
    config: &adaptive::AdaptiveConfig,
    codec: Option<&codec_reliability::CodecMetadata>,
) -> Result<InnovationReport, String> {
    decode_samples_with_pool(samples, sample_rate, config, codec, false)
}

pub fn decode_samples_with_pool(
    samples: &[f64],
    sample_rate: u32,
    config: &adaptive::AdaptiveConfig,
    codec: Option<&codec_reliability::CodecMetadata>,
    pooled_codec: bool,
) -> Result<InnovationReport, String> {
    let started = Instant::now();
    if pooled_codec && codec.is_none() {
        return Err("pooled codec calibration requires original codec metadata".into());
    }
    if codec.is_some_and(|metadata| {
        metadata.sample_rate != sample_rate || metadata.decoded_samples != samples.len()
    }) {
        return Err("codec metadata does not match audio sample geometry".into());
    }
    // Existing validated receiver enforces sample/resource geometry first.
    let baseline = adaptive::decode_samples(samples, sample_rate, config)?;
    let prepare_started = Instant::now();
    let mut source_models = Vec::new();
    let mut source_rejections = Vec::new();
    for anchor in &baseline.anchor_models {
        match source_model(samples, sample_rate, config, anchor, codec, pooled_codec) {
            Ok(source) => source_models.push(source),
            Err(reason) if reason.starts_with("source rejected:") => {
                source_rejections.push(json!({"anchor_id":anchor.id,"reason":reason}))
            }
            Err(reason) => return Err(reason),
        }
    }
    let eligible: Vec<_> = source_models
        .iter()
        .map(|source| source.anchor.clone())
        .collect();
    let pool_sources: Vec<_> = source_models
        .iter()
        .filter_map(|s| s.pool_source.clone())
        .collect();
    let preparation_seconds = prepare_started.elapsed().as_secs_f64();
    let additional_started = Instant::now();
    let bounds = receiver::window_bounds(samples.len(), sample_rate, 6.0, 3.0)?;
    let pool = rayon::ThreadPoolBuilder::new()
        .num_threads(config.threads)
        .build()
        .map_err(|e| e.to_string())?;
    let results: Result<Vec<_>, String> = pool.install(|| {
        bounds
            .par_iter()
            .enumerate()
            .map(|(index, &bound)| {
                target_window(
                    samples,
                    sample_rate,
                    config,
                    index,
                    bound,
                    &source_models,
                    &eligible,
                    codec,
                    pooled_codec.then_some(pool_sources.as_slice()),
                )
            })
            .collect()
    });
    let mut trials = Vec::new();
    let mut skips = Vec::new();
    let mut pooled_calibrations = Vec::new();
    let mut pooled_matched_unweighted = BTreeSet::new();
    let mut lanes: BTreeMap<String, BTreeSet<String>> = [
        "matched-white",
        "innovations",
        "codec-innovations",
        "pooled-codec-innovations",
    ]
    .into_iter()
    .map(|name| (name.into(), BTreeSet::new()))
    .collect();
    for window in results? {
        for trial in &window.trials {
            if trial.lane == "innovations" && trial.pooled_comparison_id.is_some() {
                pooled_matched_unweighted.extend(trial.frame_with_fcs_hex.iter().cloned());
            }
            lanes
                .get_mut(&trial.lane)
                .ok_or("unknown result lane")?
                .extend(trial.frame_with_fcs_hex.iter().cloned());
        }
        trials.extend(window.trials);
        skips.extend(window.skips);
        pooled_calibrations.extend(window.pooled_calibrations);
    }
    let mut previous_union = baseline.union_full_frames.clone();
    for (name, frames) in &lanes {
        if name != "pooled-codec-innovations" {
            previous_union.extend(frames.iter().cloned());
        }
    }
    let mut union = previous_union.clone();
    union.extend(lanes["pooled-codec-innovations"].iter().cloned());
    Ok(InnovationReport {
        pooled_codec_enabled: pooled_codec,
        pooled_calibrations,
        pooled_added_vs_matched_unweighted: lanes["pooled-codec-innovations"]
            .difference(&pooled_matched_unweighted)
            .cloned()
            .collect(),
        pooled_lost_vs_matched_unweighted: pooled_matched_unweighted
            .difference(&lanes["pooled-codec-innovations"])
            .cloned()
            .collect(),
        pooled_matched_unweighted_full_frames: pooled_matched_unweighted,
        added_vs_previous_innovation: union.difference(&previous_union).cloned().collect(),
        previous_innovation_union_full_frames: previous_union,
        added_vs_existing_adaptive: union
            .difference(&baseline.union_full_frames)
            .cloned()
            .collect(),
        innovations_added_vs_matched_white: lanes["innovations"]
            .difference(&lanes["matched-white"])
            .cloned()
            .collect(),
        innovations_lost_vs_matched_white: lanes["matched-white"]
            .difference(&lanes["innovations"])
            .cloned()
            .collect(),
        codec_added_vs_innovations: lanes["codec-innovations"]
            .difference(&lanes["innovations"])
            .cloned()
            .collect(),
        baseline,
        source_models,
        source_rejections,
        trials,
        skips,
        lane_full_frames: lanes,
        union_full_frames: union,
        model_preparation_seconds: preparation_seconds,
        new_lanes_seconds: additional_started.elapsed().as_secs_f64(),
        total_decode_seconds: started.elapsed().as_secs_f64(),
    })
}

pub fn decode_file(
    path: &Path,
    output: &Path,
    config: &adaptive::AdaptiveConfig,
    codec_sideinfo: bool,
) -> Result<Value, String> {
    decode_file_with_options(path, output, config, codec_sideinfo, false, None)
}

/// Bind additional codec evidence to the exact canonical PCM16 waveform used
/// by every benchmark receiver. Equal duration alone is never sufficient.
fn bind_codec_source(
    audio: &input::AudioInput,
    original: &Path,
    scratch: &Path,
) -> Result<(codec_reliability::CodecMetadata, Value), String> {
    if audio.conversion_command.is_some() {
        return Err("external codec source requires an already decoded PCM16 WAV input".into());
    }
    let spec = hound::WavReader::new(input::open_regular(Path::new(&audio.wav_identity.path))?)
        .map_err(|e| e.to_string())?
        .spec();
    if spec.channels != 1
        || spec.bits_per_sample != 16
        || spec.sample_format != hound::SampleFormat::Int
    {
        return Err("codec-source binding requires canonical mono PCM16 WAV".into());
    }
    let metadata =
        codec_reliability::CodecMetadata::probe(original, audio.sample_rate, audio.samples.len())?;
    let temporary = tempfile::Builder::new()
        .prefix("codec-source-binding-")
        .tempdir_in(scratch)
        .map_err(|e| e.to_string())?;
    let derived = temporary.path().join("canonical.wav");
    let args = vec![
        "-nostdin".into(),
        "-v".into(),
        "error".into(),
        "-n".into(),
        "-i".into(),
        metadata.source.path.clone(),
        "-map".into(),
        "0:a:0".into(),
        "-c:a".into(),
        "pcm_s16le".into(),
        "-flags:a".into(),
        "+bitexact".into(),
        "-fflags".into(),
        "+bitexact".into(),
        derived.display().to_string(),
    ];
    input::external_with_file_limit("ffmpeg", &args, 120, input::MAX_AUDIO_BYTES)?;
    let derived_identity = input::identity(&derived)?;
    if derived_identity.sha256 != audio.wav_identity.sha256
        || derived_identity.bytes != audio.wav_identity.bytes
    {
        return Err(
            "supplied PCM16 WAV is not the exact canonical decode of the codec source".into(),
        );
    }
    let after = input::identity(original)?;
    if after.sha256 != metadata.source.sha256
        || after.bytes != metadata.source.bytes
        || after.path != metadata.source.path
    {
        return Err("original codec source changed during canonical PCM binding".into());
    }
    let command: Vec<_> = std::iter::once("ffmpeg".to_string()).chain(args).collect();
    let binding = json!({"kind":"canonical_pcm16_wav_byte_identity",
        "codec_source":metadata.source,"analysis_input":audio.wav_identity,
        "derived_pcm16":derived_identity,"conversion_command":command,
        "same_numeric_input_verified":true,"codec_metadata_is_additional_treatment_information":true,
        "no_resampling_downmix_or_unknown_alignment":true});
    Ok((metadata, binding))
}

pub fn decode_file_with_options(
    path: &Path,
    output: &Path,
    config: &adaptive::AdaptiveConfig,
    codec_sideinfo: bool,
    pooled_codec: bool,
    codec_source: Option<&Path>,
) -> Result<Value, String> {
    if (pooled_codec || codec_source.is_some()) && !codec_sideinfo {
        return Err("pooled codec and codec source options require codec-sideinfo".into());
    }
    let started = Instant::now();
    let out = input::existing_new_dir(output)?;
    let execution = (|| -> Result<Value, String> {
        let audio = input::load_audio(path, &out)?;
        let executable = input::identity(&std::env::current_exe().map_err(|e| e.to_string())?)?;
        let mut codec_input_binding = None;
        let metadata = if codec_sideinfo {
            let metadata = if let Some(original) = codec_source {
                let (metadata, binding) = bind_codec_source(&audio, original, &out)?;
                codec_input_binding = Some(binding);
                metadata
            } else {
                let metadata = codec_reliability::CodecMetadata::probe(
                    path,
                    audio.sample_rate,
                    audio.samples.len(),
                )?;
                if metadata.source.sha256 != audio.source_identity.sha256
                    || metadata.source.bytes != audio.source_identity.bytes
                    || metadata.source.path != audio.source_identity.path
                {
                    return Err("codec metadata does not match the original audio source".into());
                }
                metadata
            };
            input::write_json_new(&out.join("codec-metadata.json"), &metadata)?;
            Some(metadata)
        } else {
            None
        };
        input::write_json_new(
            &out.join("plan.json"),
            &json!({
                "schema":if pooled_codec || codec_source.is_some(){"innovation-audio-plan-v2"}else{"innovation-audio-plan-v1"},"source":audio.source_identity,
                "input":audio.wav_identity,"executable":executable,"config":config,
                "codec_sideinfo":codec_sideinfo,"conversion_command":audio.conversion_command,
                "pooled_codec":pooled_codec,"codec_input_binding":codec_input_binding,
                "reference_bytes_used_for_search":false,"crc_guided_bit_repair":false,
                "target_truth_used_for_training":false,"recursive_training":false,
                "anchor_guard_seconds":1,"maximum_anchor_distance_seconds":60,
                "source_channel_split_fraction":0.4,"residual_split_guard_symbols":SPLIT_GUARD,
                "codec_validation_scope":"conditional residual holdout only: frontend normalization and baseline timing use the entire source window; not an untouched source-waveform holdout",
                "timing_and_gain_bank":"same across new white/innovations/codec lanes",
                "qualification":"development prototype; known colored-noise MLSE plus experimental codec conditioning",
                "publication_ready":false,"deployment_ready":false
            }),
        )?;
        let report = decode_samples_with_pool(
            &audio.samples,
            audio.sample_rate,
            config,
            metadata.as_ref(),
            pooled_codec,
        )?;
        let after = input::identity(path)?;
        if after.sha256 != audio.source_identity.sha256
            || after.bytes != audio.source_identity.bytes
        {
            return Err("audio source changed during experiment".into());
        }
        if let (Some(original), Some(metadata)) = (codec_source, metadata.as_ref()) {
            let after = input::identity(original)?;
            if after.sha256 != metadata.source.sha256
                || after.bytes != metadata.source.bytes
                || after.path != metadata.source.path
            {
                return Err("original codec source changed during experiment".into());
            }
        }
        let byte_count = |frames: &BTreeSet<String>| -> usize {
            frames.iter().map(|frame| frame.len() / 2 - 2).sum()
        };
        let result = json!({
            "schema":if pooled_codec || codec_source.is_some(){"innovation-audio-result-v2"}else{"innovation-audio-result-v1"},"status":"complete","source":audio.source_identity,
            "pooled_codec":pooled_codec,"codec_input_binding":codec_input_binding,
            "sample_rate_hz":audio.sample_rate,"samples":audio.samples.len(),"config":config,
            "existing_adaptive_count":report.baseline.union_full_frames.len(),
            "union_count":report.union_full_frames.len(),
            "added_count":report.added_vs_existing_adaptive.len(),
            "added_payload_bytes":byte_count(&report.added_vs_existing_adaptive),
            "added_pdu_bytes_excluding_fcs":byte_count(&report.added_vs_existing_adaptive),
            "added_vs_previous_innovation_count":report.added_vs_previous_innovation.len(),
            "pooled_fits_accepted":report.pooled_calibrations.iter().filter(|p|p["fit"]["variance_fit"]["accepted"]==true).count(),
            "codec_fits_accepted":report.source_models.iter().filter(|s|s.codec_fit.as_ref().is_some_and(|f|f.accepted)).count(),
            "total_wall_seconds":started.elapsed().as_secs_f64(),"report":report,
            "publication_ready":false,"deployment_ready":false,"false_alarm_qualification_complete":false,
            "comparison_to_complete_progressive_bank":false,"equal_cpu_advantage_established":false,
            "codec_feature_is_error_variance_measurement":false,
            "received_fcs_independently_verified":true
        });
        input::write_json_new(&out.join("result.json"), &result)?;
        Ok(json!({"status":"complete","result":out.join("result.json"),
            "existing_adaptive_count":result["existing_adaptive_count"],"union_count":result["union_count"],
            "added_count":result["added_count"],"codec_fits_accepted":result["codec_fits_accepted"],
            "added_vs_previous_innovation_count":result["added_vs_previous_innovation_count"],
            "pooled_fits_accepted":result["pooled_fits_accepted"],
            "total_wall_seconds":result["total_wall_seconds"]}))
    })();
    if let Err(reason) = &execution {
        input::write_json_new(
            &out.join("failure.json"),
            &json!({"status":"failed","reason":reason}),
        )?;
    }
    execution
}

#[cfg(test)]
mod tests {
    use super::*;

    fn codec_fixture(directory: &Path) -> (std::path::PathBuf, std::path::PathBuf) {
        let raw = directory.join("raw.wav");
        let original = directory.join("original.ogg");
        let shared = directory.join("shared.wav");
        let mut writer = hound::WavWriter::create(
            &raw,
            hound::WavSpec {
                channels: 1,
                sample_rate: 48000,
                bits_per_sample: 16,
                sample_format: hound::SampleFormat::Int,
            },
        )
        .unwrap();
        for index in 0..48000 {
            writer
                .write_sample((12000.0 * (index as f64 * 0.137).sin()) as i16)
                .unwrap();
        }
        writer.finalize().unwrap();
        input::external_with_file_limit(
            "ffmpeg",
            &[
                "-nostdin",
                "-v",
                "error",
                "-n",
                "-i",
                raw.to_str().unwrap(),
                "-c:a",
                "libvorbis",
                "-q:a",
                "3",
                original.to_str().unwrap(),
            ]
            .map(String::from),
            10,
            input::MAX_AUDIO_BYTES,
        )
        .unwrap();
        input::external_with_file_limit(
            "ffmpeg",
            &[
                "-nostdin",
                "-v",
                "error",
                "-n",
                "-i",
                original.to_str().unwrap(),
                "-map",
                "0:a:0",
                "-c:a",
                "pcm_s16le",
                "-flags:a",
                "+bitexact",
                "-fflags",
                "+bitexact",
                shared.to_str().unwrap(),
            ]
            .map(String::from),
            10,
            input::MAX_AUDIO_BYTES,
        )
        .unwrap();
        (original, shared)
    }

    #[test]
    fn canonical_pcm16_binding_accepts_independent_identical_decode() {
        let directory = tempfile::tempdir().unwrap();
        let (original, shared) = codec_fixture(directory.path());
        let audio = input::load_audio(&shared, directory.path()).unwrap();
        let (metadata, binding) = bind_codec_source(&audio, &original, directory.path()).unwrap();
        assert_eq!(
            metadata.source.sha256,
            input::identity(&original).unwrap().sha256
        );
        assert_eq!(binding["same_numeric_input_verified"], true);
        assert_eq!(
            binding["analysis_input"]["sha256"],
            binding["derived_pcm16"]["sha256"]
        );
        assert!(original.is_file() && shared.is_file());
        assert!(!Path::new(binding["derived_pcm16"]["path"].as_str().unwrap()).exists());
    }

    #[test]
    fn canonical_binding_rejects_same_length_modified_waveform() {
        use std::io::{Read, Seek, SeekFrom, Write};
        let directory = tempfile::tempdir().unwrap();
        let (original, shared) = codec_fixture(directory.path());
        let before = input::identity(&shared).unwrap();
        let mut file = std::fs::OpenOptions::new()
            .read(true)
            .write(true)
            .open(&shared)
            .unwrap();
        file.seek(SeekFrom::End(-2)).unwrap();
        let mut last = [0u8; 2];
        file.read_exact(&mut last).unwrap();
        last[0] ^= 1;
        file.seek(SeekFrom::End(-2)).unwrap();
        file.write_all(&last).unwrap();
        drop(file);
        let audio = input::load_audio(&shared, directory.path()).unwrap();
        assert_eq!(audio.wav_identity.bytes, before.bytes);
        assert!(
            bind_codec_source(&audio, &original, directory.path())
                .unwrap_err()
                .contains("not the exact canonical decode")
        );
    }

    #[test]
    fn external_codec_source_rejects_implicit_float_conversion() {
        let directory = tempfile::tempdir().unwrap();
        let (original, _) = codec_fixture(directory.path());
        let audio = input::load_audio(&original, directory.path()).unwrap();
        assert!(
            bind_codec_source(&audio, &original, directory.path())
                .unwrap_err()
                .contains("already decoded PCM16")
        );
    }

    #[test]
    fn pooled_decode_requires_codec_metadata_before_search() {
        let config = adaptive::AdaptiveConfig {
            baud: 9600.0,
            threads: 1,
        };
        assert!(
            decode_samples_with_pool(&[], 48000, &config, None, true)
                .err()
                .unwrap()
                .contains("requires original codec metadata")
        );
    }

    fn timing() -> dsp::TimingHypothesis {
        dsp::TimingHypothesis {
            rate_error_ppm: 0.0,
            phase_samples: 0.0,
            step_samples: 2.5,
            threshold: 0.0,
            score: 1.0,
            symbol_count: 100,
        }
    }
    #[test]
    fn absolute_mapping_accounts_for_filter_delay_and_decimation() {
        let t = timing();
        assert_eq!(
            symbol_pcm_position("legacy-fir512", 48000, 9600.0, 480000, &t, 0).unwrap(),
            482615.0
        );
        assert_eq!(
            symbol_pcm_position("legacy-fir512", 48000, 9600.0, 480000, &t, 1).unwrap(),
            482620.0
        );
        let mut b = t;
        b.step_samples = 5.0;
        assert_eq!(
            symbol_pcm_position("boxcar32-rms50", 48000, 9600.0, 480000, &b, 0).unwrap(),
            480320.0
        );
        assert_eq!(
            symbol_pcm_position("boxcar32-rms50", 48000, 9600.0, 480000, &b, 1).unwrap(),
            480325.0
        );
    }
    #[test]
    fn rejects_invalid_mapping() {
        for name in ["unknown", "legacy-fir512"] {
            assert!(symbol_pcm_position(name, 0, 9600.0, 0, &timing(), 0).is_err());
            assert!(symbol_pcm_position(name, 48000, 9600.0, 0, &timing(), 100).is_err());
        }
    }

    #[test]
    fn codec_check_suffix_cannot_influence_ar_fit_inputs() {
        let mut residuals = vec![
            (0..1200)
                .map(|i| (i as f64 * 1.234).sin())
                .collect::<Vec<_>>(),
        ];
        let before = noise_training_prefixes(&residuals);
        let fit_before = innovation::fit_noise_model(&before).unwrap();
        for value in &mut residuals[0][720..] {
            *value = 1e6;
        }
        let after = noise_training_prefixes(&residuals);
        assert_eq!(before, after);
        assert_eq!(
            serde_json::to_value(fit_before).unwrap(),
            serde_json::to_value(innovation::fit_noise_model(&after).unwrap()).unwrap()
        );
    }
}
