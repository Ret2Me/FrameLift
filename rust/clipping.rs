//! Reference-free CI16 salvage receiver and explicitly reconstructed IQ.
//!
//! Constant-radius endpoint hypotheses are the historical heuristic, NOT the
//! recorded IQ and NOT a constraint-preserving inverse. Alternating bandlimit
//! projection is a separate experiment. Soft-repaired CRC-valid frames remain
//! candidates, even when correlated frontends agree; consensus never promotes
//! a repaired frame to native-trusted telemetry.

use crate::{dsp, input, soft};
use num_complex::Complex64;
use rustfft::FftPlanner;
use serde::{Deserialize, Serialize};
use std::cmp::Ordering;
use std::collections::{BTreeMap, BTreeSet};
use std::fs::File;
use std::io::{Read, Seek, SeekFrom};
use std::path::Path;
use std::sync::Arc;

const MAX_WINDOW_SAMPLES: usize = 4_194_304;
const MAX_ANALYSIS_WINDOWS: usize = 1_000_000;
const MAX_PROJECTION_ITERATIONS: usize = 10_000;
const MAX_PROJECTION_OUTPUT_BYTES: usize = 512 * 1024 * 1024;
type Result<T> = std::result::Result<T, String>;

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(default, deny_unknown_fields)]
pub struct BlindPhaseFskConfig {
    pub sample_rate_hz: u32,
    pub baudrate: f64,
    pub analysis_window_seconds: f64,
    pub decoder_window_seconds: f64,
    pub decoder_window_lead_seconds: f64,
    pub candidate_window_limit: usize,
    pub window_nms_seconds: f64,
    pub constant_radius_factors: Vec<f64>,
    pub phase_difference_lags: Vec<usize>,
    pub descramble_modes: Vec<bool>,
    pub rate_errors_ppm: Vec<f64>,
    pub phase_bins: usize,
    pub short_search_timing_hypotheses: usize,
    pub deep_search_timing_hypotheses: usize,
    pub minimum_consecutive_flags: usize,
    pub minimum_frame_body_bits: usize,
    pub short_least_reliable_symbols: usize,
    pub short_maximum_flips: usize,
    pub short_maximum_attempts: usize,
    pub deep_least_reliable_symbols: usize,
    pub deep_maximum_flips: usize,
    pub deep_maximum_attempts: usize,
    pub maximum_regions_per_start: usize,
    pub repair_path_maximum_attempts: usize,
    pub repair_path_maximum_unique_frames: usize,
    pub repair_region_maximum_attempts: usize,
    pub repair_event_maximum_attempts: usize,
    pub repair_event_maximum_unique_frames: usize,
    pub repair_window_maximum_events: usize,
    pub repair_window_maximum_attempts: usize,
    pub repair_window_maximum_unique_frames: usize,
    pub event_cluster_tolerance_symbols: usize,
    pub candidate_neighbor_radius: usize,
    pub error_unit_penalty: f64,
    pub maximum_map_seed_states: usize,
    pub maximum_receiver_paths_per_window: usize,
    pub maximum_frame_detections: usize,
}

impl Default for BlindPhaseFskConfig {
    fn default() -> Self {
        Self {
            sample_rate_hz: 57_600,
            baudrate: 9_600.0,
            analysis_window_seconds: 0.5,
            decoder_window_seconds: 2.75,
            decoder_window_lead_seconds: 1.25,
            candidate_window_limit: 32,
            window_nms_seconds: 2.0,
            constant_radius_factors: vec![3.0],
            phase_difference_lags: vec![1],
            descramble_modes: vec![false, true],
            rate_errors_ppm: vec![0.0],
            phase_bins: 64,
            short_search_timing_hypotheses: 16,
            deep_search_timing_hypotheses: 16,
            minimum_consecutive_flags: 4,
            minimum_frame_body_bits: 128,
            short_least_reliable_symbols: 64,
            short_maximum_flips: 2,
            short_maximum_attempts: 20_000,
            deep_least_reliable_symbols: 64,
            deep_maximum_flips: 5,
            deep_maximum_attempts: 400_000,
            maximum_regions_per_start: 8,
            repair_path_maximum_attempts: 100_000,
            repair_path_maximum_unique_frames: 8,
            repair_region_maximum_attempts: 50_000,
            repair_event_maximum_attempts: 400_000,
            repair_event_maximum_unique_frames: 8,
            repair_window_maximum_events: 16,
            repair_window_maximum_attempts: 1_600_000,
            repair_window_maximum_unique_frames: 16,
            event_cluster_tolerance_symbols: 16,
            candidate_neighbor_radius: 1,
            error_unit_penalty: 0.05,
            maximum_map_seed_states: 512,
            maximum_receiver_paths_per_window: 20_000,
            maximum_frame_detections: 20_000,
        }
    }
}

impl BlindPhaseFskConfig {
    pub fn validate(&self) -> Result<()> {
        if self.sample_rate_hz == 0 || !self.baudrate.is_finite() || self.baudrate <= 0.0 {
            return Err("sample rate and baudrate must be positive and finite".into());
        }
        if !self.analysis_window_seconds.is_finite()
            || self.analysis_window_seconds <= 0.0
            || !self.decoder_window_seconds.is_finite()
            || self.decoder_window_seconds <= 0.0
        {
            return Err("window durations must be positive and finite".into());
        }
        if !self.decoder_window_lead_seconds.is_finite()
            || self.decoder_window_lead_seconds < 0.0
            || self.decoder_window_lead_seconds >= self.decoder_window_seconds
        {
            return Err("decoder window lead is outside the window".into());
        }
        if self.candidate_window_limit == 0
            || self.candidate_window_limit > MAX_ANALYSIS_WINDOWS
            || !self.window_nms_seconds.is_finite()
            || self.window_nms_seconds < 0.0
        {
            return Err("invalid candidate-window limits".into());
        }
        if self.constant_radius_factors.is_empty()
            || self
                .constant_radius_factors
                .iter()
                .any(|x| !x.is_finite() || *x < 1.0)
            || self.phase_difference_lags.is_empty()
            || self.phase_difference_lags.contains(&0)
        {
            return Err("invalid radius factors or phase-difference lags".into());
        }
        if self.descramble_modes.is_empty()
            || self
                .descramble_modes
                .iter()
                .copied()
                .collect::<BTreeSet<_>>()
                .len()
                != self.descramble_modes.len()
        {
            return Err("descramble modes must be unique booleans".into());
        }
        let (_, sps) = frontend_decimation_and_sps(self)?;
        let mut rates = self.rate_errors_ppm.clone();
        if rates.is_empty()
            || rates
                .iter()
                .any(|x| !x.is_finite() || sps * (1.0 + x * 1e-6) <= 1.0)
            || self.phase_bins < 4
        {
            return Err("invalid timing bank".into());
        }
        rates.sort_by(f64::total_cmp);
        rates.dedup_by(|a, b| *a == *b);
        let bank = rates
            .len()
            .checked_mul(self.phase_bins)
            .ok_or("timing bank overflows")?;
        if self.short_search_timing_hypotheses == 0
            || self.deep_search_timing_hypotheses == 0
            || self.short_search_timing_hypotheses > bank
            || self.deep_search_timing_hypotheses > bank
            || bank > 1_000_000
        {
            return Err("timing budget exceeds bounded bank".into());
        }
        if self.minimum_consecutive_flags < 2 || self.minimum_frame_body_bits == 0 {
            return Err("invalid preamble constraints".into());
        }
        if [
            self.maximum_regions_per_start,
            self.repair_path_maximum_attempts,
            self.repair_path_maximum_unique_frames,
            self.repair_region_maximum_attempts,
            self.repair_event_maximum_attempts,
            self.repair_event_maximum_unique_frames,
            self.repair_window_maximum_events,
            self.repair_window_maximum_attempts,
            self.repair_window_maximum_unique_frames,
            self.event_cluster_tolerance_symbols,
            self.maximum_receiver_paths_per_window,
            self.maximum_frame_detections,
            self.maximum_map_seed_states,
        ]
        .contains(&0)
        {
            return Err("repair scheduler bounds must be positive".into());
        }
        if self.repair_path_maximum_attempts > self.repair_event_maximum_attempts
            || self.repair_event_maximum_attempts > self.repair_window_maximum_attempts
        {
            return Err("repair path/event budget exceeds enclosing budget".into());
        }
        checked_window_samples(self.analysis_window_seconds, self.sample_rate_hz)?;
        checked_window_samples(self.decoder_window_seconds, self.sample_rate_hz)?;
        self.deep_budget().validate()?;
        Ok(())
    }

    fn deep_budget(&self) -> soft::SoftListBudget {
        soft::SoftListBudget {
            least_reliable_symbols: self.deep_least_reliable_symbols,
            maximum_flips: self.deep_maximum_flips,
            maximum_regions: self.maximum_regions_per_start,
            maximum_attempts: self.deep_maximum_attempts,
            maximum_attempts_per_region: Some(self.repair_region_maximum_attempts),
            candidate_neighbor_radius: self.candidate_neighbor_radius,
            error_unit_penalty: self.error_unit_penalty,
            maximum_map_seed_states: self.maximum_map_seed_states,
            maximum_output_frames: self.repair_path_maximum_unique_frames,
            ..Default::default()
        }
    }
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
pub struct PhaseWindowCandidate {
    pub analysis_index: usize,
    pub analysis_start_seconds: f64,
    pub decoder_start_seconds: f64,
    pub mean_power: f64,
    pub endpoint_clip_fraction: f64,
    pub lag1_phase_coherence: f64,
    pub score: f64,
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
pub struct ClippingRobustAx25Frame {
    pub payload: Vec<u8>,
    pub fcs: Vec<u8>,
    pub decoder_start_seconds: f64,
    pub estimated_frame_start_seconds: f64,
    pub constant_radius_factor: f64,
    pub phase_difference_lag: usize,
    pub g3ruh_descramble: bool,
    pub timing: dsp::TimingHypothesis,
    pub frame_start_symbol: usize,
    pub frame_stop_symbol: usize,
    pub flipped_symbol_indices: Vec<usize>,
    pub flipped_symbol_reliabilities: Vec<f64>,
    pub search_stage: String,
    pub attempted_candidates: usize,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct BlindPhaseFskResult {
    pub input_path: String,
    pub input_sha256: String,
    pub input_complex_samples: u64,
    pub input_duration_seconds: f64,
    pub selected_windows: Vec<PhaseWindowCandidate>,
    pub decoded_windows: usize,
    pub timing_hypotheses_examined: usize,
    pub protocol_candidates_attempted: usize,
    pub native_protocol_candidates_attempted: usize,
    pub repair_protocol_candidates_attempted: usize,
    pub frames: Vec<ClippingRobustAx25Frame>,
    pub frontend_input_representation: String,
    pub repaired_frames_require_external_verification: bool,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Ax25FrameConsensus {
    pub payload: Vec<u8>,
    pub fcs: Vec<u8>,
    pub estimated_frame_start_seconds: f64,
    pub detections: Vec<ClippingRobustAx25Frame>,
    pub independent_frontends: Vec<(f64, usize)>,
    pub g3ruh_descramble_modes: Vec<bool>,
    pub has_uncorrected_detection: bool,
    pub has_correction_consensus: bool,
    pub native_trusted: bool,
}

fn float_cmp(a: f64, b: f64) -> Ordering {
    a.partial_cmp(&b).unwrap_or_else(|| a.total_cmp(&b))
}
fn median(values: &mut [f64]) -> f64 {
    let n = values.len();
    let middle = n / 2;
    let (left, center, _) = values.select_nth_unstable_by(middle, |a, b| float_cmp(*a, *b));
    if n.is_multiple_of(2) {
        (left.iter().copied().fold(f64::NEG_INFINITY, f64::max) + *center) / 2.0
    } else {
        *center
    }
}
fn lower_baseline(values: &[f64]) -> Result<f64> {
    let count = ((values.len() as f64 * 0.25).ceil() as usize).max(1);
    let mut sorted = values.to_vec();
    sorted.select_nth_unstable_by(count - 1, |a, b| float_cmp(*a, *b));
    let result = median(&mut sorted[..count]);
    if !result.is_finite() || result <= 0.0 {
        return Err("signal statistics have no finite positive baseline".into());
    }
    Ok(result)
}
fn checked_window_samples(seconds: f64, rate: u32) -> Result<usize> {
    let count = (seconds * rate as f64).round_ties_even();
    if !count.is_finite() || count < 2.0 || count > MAX_WINDOW_SAMPLES as f64 {
        return Err("CI16 window exceeds 2..4194304 sample bound".into());
    }
    Ok(count as usize)
}
fn ci16_sample_count(path: &Path) -> Result<u64> {
    let metadata = input::open_regular(path)?
        .metadata()
        .map_err(|e| e.to_string())?;
    if !metadata.is_file() || metadata.len() == 0 || metadata.len() % 4 != 0 {
        return Err("CI16 input must be a regular file of complete nonempty IQ pairs".into());
    }
    Ok(metadata.len() / 4)
}
fn read_pairs(file: &mut File, samples: usize) -> Result<Vec<[i16; 2]>> {
    let mut bytes = vec![0_u8; samples.checked_mul(4).ok_or("CI16 byte count overflow")?];
    file.read_exact(&mut bytes)
        .map_err(|e| format!("CI16 read is incomplete: {e}"))?;
    Ok(bytes
        .as_chunks::<4>()
        .0
        .iter()
        .map(|p| {
            [
                i16::from_le_bytes([p[0], p[1]]),
                i16::from_le_bytes([p[2], p[3]]),
            ]
        })
        .collect())
}

pub fn select_ci16_phase_windows(
    path: &Path,
    config: &BlindPhaseFskConfig,
) -> Result<Vec<PhaseWindowCandidate>> {
    config.validate()?;
    let count = ci16_sample_count(path)?;
    let analysis = checked_window_samples(config.analysis_window_seconds, config.sample_rate_hz)?;
    let windows = count / analysis as u64;
    if windows < 2 || windows > MAX_ANALYSIS_WINDOWS as u64 {
        return Err("capture requires 2..1000000 analysis windows".into());
    }
    let mut file = input::open_regular(path)?;
    let mut powers = Vec::new();
    let mut clips = Vec::new();
    let mut coherences = Vec::new();
    for _ in 0..windows {
        let raw = read_pairs(&mut file, analysis)?;
        let power: Vec<_> = raw
            .iter()
            .map(|[i, q]| (*i as f64).powi(2) + (*q as f64).powi(2))
            .collect();
        powers.push(dsp::pairwise_sum(&power) / analysis as f64);
        clips.push(
            raw.iter()
                .flatten()
                .filter(|x| **x == i16::MIN || **x == i16::MAX)
                .count() as f64
                / (2 * analysis) as f64,
        );
        let mut real = Vec::with_capacity(analysis - 1);
        let mut imag = Vec::with_capacity(analysis - 1);
        let mut magnitudes = Vec::with_capacity(analysis - 1);
        for pair in raw.windows(2) {
            let [i0, q0] = pair[0].map(f64::from);
            let [i1, q1] = pair[1].map(f64::from);
            let re = i1 * i0 + q1 * q0;
            let im = q1 * i0 - i1 * q0;
            real.push(re);
            imag.push(im);
            magnitudes.push(re.hypot(im));
        }
        let total = dsp::pairwise_sum(&magnitudes);
        coherences.push(if total > 0.0 {
            dsp::pairwise_sum(&real).hypot(dsp::pairwise_sum(&imag)) / total
        } else {
            0.0
        });
    }
    let power_base = lower_baseline(&powers)?;
    let coherence_base = lower_baseline(
        &coherences
            .iter()
            .map(|x| x.max(f64::EPSILON))
            .collect::<Vec<_>>(),
    )?;
    let clip_base = lower_baseline(
        &clips
            .iter()
            .map(|x| x.max(f64::EPSILON))
            .collect::<Vec<_>>(),
    )?;
    let scores: Vec<_> = (0..windows as usize)
        .map(|i| {
            [
                (powers[i] / power_base - 1.0).max(0.0).ln_1p(),
                (coherences[i] / coherence_base - 1.0).max(0.0).ln_1p(),
                (clips[i] / clip_base - 1.0).max(0.0).ln_1p(),
            ]
            .into_iter()
            .fold(f64::NEG_INFINITY, f64::max)
        })
        .collect();
    let mut ranked: Vec<_> = (0..windows as usize).collect();
    ranked.sort_by(|a, b| float_cmp(scores[*b], scores[*a]));
    let max_start =
        (count as f64 / config.sample_rate_hz as f64 - config.decoder_window_seconds).max(0.0);
    let mut chosen: Vec<PhaseWindowCandidate> = Vec::new();
    for index in ranked {
        let analysis_start = index as f64 * config.analysis_window_seconds;
        let center = analysis_start + 0.5 * config.analysis_window_seconds;
        if chosen.iter().any(|old| {
            (center - (old.analysis_start_seconds + 0.5 * config.analysis_window_seconds)).abs()
                < config.window_nms_seconds
        }) {
            continue;
        }
        chosen.push(PhaseWindowCandidate {
            analysis_index: index,
            analysis_start_seconds: analysis_start,
            decoder_start_seconds: max_start
                .min((analysis_start - config.decoder_window_lead_seconds).max(0.0)),
            mean_power: powers[index],
            endpoint_clip_fraction: clips[index],
            lag1_phase_coherence: coherences[index],
            score: scores[index],
        });
        if chosen.len() >= config.candidate_window_limit {
            break;
        }
    }
    Ok(chosen)
}

pub fn constant_radius_declip_exact_endpoints(
    raw: &[[i16; 2]],
    factor: f64,
) -> Result<Vec<Complex64>> {
    if !factor.is_finite() || factor < 1.0 {
        return Err("radius factor must be finite and >=1".into());
    }
    let radius = factor * 32767.0;
    if !radius.is_finite() || !(radius * radius).is_finite() {
        return Err("radius factor overflows".into());
    }
    Ok(raw
        .iter()
        .map(|pair| {
            let mut values = pair.map(f64::from);
            let saturated = pair.map(|x| x == i16::MIN || x == i16::MAX);
            match saturated {
                [true, true] => {
                    values = values.map(|x| x.signum() * (radius / std::f64::consts::SQRT_2));
                }
                [true, false] => {
                    values[0] = values[0].signum()
                        * (radius * radius - values[1] * values[1])
                            .max(32767.0_f64.powi(2))
                            .sqrt()
                }
                [false, true] => {
                    values[1] = values[1].signum()
                        * (radius * radius - values[0] * values[0])
                            .max(32767.0_f64.powi(2))
                            .sqrt()
                }
                _ => {}
            }
            Complex64::new(values[0], values[1])
        })
        .collect())
}

pub fn frontend_decimation_and_sps(config: &BlindPhaseFskConfig) -> Result<(usize, f64)> {
    let source_sps = config.sample_rate_hz as f64 / config.baudrate;
    if !source_sps.is_finite() || source_sps < 2.0 {
        return Err("sample rate must provide >=2 samples/symbol".into());
    }
    let decimation = (source_sps / 2.0).floor().max(1.0) as usize;
    Ok((decimation, source_sps / decimation as f64))
}

pub fn phase_first(
    iq: &[Complex64],
    lag: usize,
    config: &BlindPhaseFskConfig,
) -> Result<dsp::Frontend> {
    let (decimation, sps) = frontend_decimation_and_sps(config)?;
    if lag == 0
        || lag >= iq.len()
        || iq.len() > MAX_WINDOW_SAMPLES
        || iq.iter().any(|x| !x.re.is_finite() || !x.im.is_finite())
    {
        return Err("invalid bounded phase-difference input or lag".into());
    }
    let increments: Vec<_> = iq[lag..]
        .iter()
        .zip(&iq[..iq.len() - lag])
        .map(|(a, b)| (*a * b.conj()).arg() / lag as f64)
        .collect();
    let taps = dsp::firwin(
        115,
        0.0,
        0.5 * config.baudrate,
        config.sample_rate_hz as f64,
    );
    let filtered = dsp::fir_decimated(&increments, &taps, decimation)?;
    if filtered.len() <= 1024 {
        return Err("decoder window too short after phase discrimination".into());
    }
    let mut cumulative = Vec::with_capacity(filtered.len() + 1);
    cumulative.push(0.0);
    for value in &filtered {
        cumulative.push(cumulative.last().unwrap() + value);
    }
    let output: Vec<_> = (1023..filtered.len())
        .map(|i| filtered[i] - (cumulative[i + 1] - cumulative[i + 1 - 1024]) / 1024.0)
        .collect();
    Ok(dsp::Frontend {
        samples: dsp::normalize(output)?,
        samples_per_symbol: sps,
    })
}

fn decode_levels(levels: &[u8], descramble: bool) -> Vec<u8> {
    let mut previous = 0;
    let mut state = 0_u32;
    levels
        .iter()
        .map(|level| {
            let bit = u8::from(*level == previous);
            previous = *level;
            if !descramble {
                return bit;
            }
            let out = ((state & 0x21).count_ones() as u8 & 1) ^ bit;
            state = (state >> 1) | (u32::from(bit) << 16);
            out
        })
        .collect()
}

#[derive(Clone)]
struct ReceiverPath {
    radius: f64,
    lag: usize,
    descramble: bool,
    rank: usize,
    timing: dsp::TimingHypothesis,
    soft: Arc<Vec<f64>>,
    start: usize,
    estimated: f64,
}
fn estimated_start(
    decoder_start: f64,
    start: usize,
    timing: &dsp::TimingHypothesis,
    lag: usize,
    config: &BlindPhaseFskConfig,
) -> Result<f64> {
    let (decimation, _) = frontend_decimation_and_sps(config)?;
    let zero = 57.0 + 1023.0 * decimation as f64 + lag as f64 / 2.0;
    let preclock = timing.phase_samples + (32 + start) as f64 * timing.step_samples;
    Ok(decoder_start + (zero + decimation as f64 * preclock) / config.sample_rate_hz as f64)
}
fn path_compare(a: &ReceiverPath, b: &ReceiverPath) -> Ordering {
    float_cmp(-a.timing.score, -b.timing.score)
        .then(a.rank.cmp(&b.rank))
        .then(a.lag.cmp(&b.lag))
        .then(a.descramble.cmp(&b.descramble))
        .then(a.start.cmp(&b.start))
}
fn cluster_paths(mut paths: Vec<ReceiverPath>, tolerance: f64) -> Vec<Vec<ReceiverPath>> {
    paths.sort_by(|a, b| {
        float_cmp(a.estimated, b.estimated)
            .then(a.descramble.cmp(&b.descramble))
            .then(a.rank.cmp(&b.rank))
            .then(a.lag.cmp(&b.lag))
            .then(float_cmp(a.radius, b.radius))
            .then(a.start.cmp(&b.start))
    });
    let mut groups: Vec<Vec<ReceiverPath>> = Vec::new();
    for path in paths {
        if let Some(group) = groups.iter_mut().find(|g| {
            (g.iter().map(|x| x.estimated).sum::<f64>() / g.len() as f64 - path.estimated).abs()
                <= tolerance
        }) {
            group.push(path);
        } else {
            groups.push(vec![path]);
        }
    }
    groups.sort_by(|a, b| {
        b.len()
            .cmp(&a.len())
            .then(float_cmp(
                -a.iter()
                    .map(|p| p.timing.score)
                    .fold(f64::NEG_INFINITY, f64::max),
                -b.iter()
                    .map(|p| p.timing.score)
                    .fold(f64::NEG_INFINITY, f64::max),
            ))
            .then(float_cmp(
                a.iter().map(|p| p.estimated).fold(f64::INFINITY, f64::min),
                b.iter().map(|p| p.estimated).fold(f64::INFINITY, f64::min),
            ))
    });
    groups
}
fn materialize(
    path: &ReceiverPath,
    decoded: &soft::SoftDecodedFrame,
    stage: &str,
    attempts: usize,
    window: f64,
    config: &BlindPhaseFskConfig,
) -> Result<ClippingRobustAx25Frame> {
    let length = decoded.frame_with_fcs.len();
    if length < 2 {
        return Err("soft decoder returned undersized frame".into());
    }
    Ok(ClippingRobustAx25Frame {
        payload: decoded.frame_with_fcs[..length - 2].to_vec(),
        fcs: decoded.frame_with_fcs[length - 2..].to_vec(),
        decoder_start_seconds: window,
        estimated_frame_start_seconds: estimated_start(
            window,
            decoded.left_flag_bit,
            &path.timing,
            path.lag,
            config,
        )?,
        constant_radius_factor: path.radius,
        phase_difference_lag: path.lag,
        g3ruh_descramble: path.descramble,
        timing: path.timing.clone(),
        frame_start_symbol: decoded.left_flag_bit,
        frame_stop_symbol: decoded.right_flag_bit,
        flipped_symbol_indices: decoded.flipped_symbol_indices.clone(),
        flipped_symbol_reliabilities: decoded.flipped_symbol_reliabilities.clone(),
        search_stage: stage.into(),
        attempted_candidates: attempts,
    })
}
fn append_frame(
    frames: &mut Vec<ClippingRobustAx25Frame>,
    frame: ClippingRobustAx25Frame,
    limit: usize,
) -> Result<()> {
    if frames.len() >= limit {
        return Err("decoder frame detection bound exceeded".into());
    }
    frames.push(frame);
    Ok(())
}
fn repaired_compare(
    a: &ClippingRobustAx25Frame,
    b: &ClippingRobustAx25Frame,
    penalty: f64,
) -> Ordering {
    let cost = |f: &ClippingRobustAx25Frame| {
        penalty * f.flipped_symbol_indices.len() as f64
            + f.flipped_symbol_reliabilities.iter().sum::<f64>()
    };
    float_cmp(cost(a), cost(b))
        .then(
            a.flipped_symbol_indices
                .len()
                .cmp(&b.flipped_symbol_indices.len()),
        )
        .then(a.flipped_symbol_indices.cmp(&b.flipped_symbol_indices))
        .then_with(|| {
            a.payload
                .iter()
                .chain(&a.fcs)
                .cmp(b.payload.iter().chain(&b.fcs))
        })
}

/// Signal-processing replay only. Resumable attempt stores/cgroups/SQLite
/// selection from the old file-orchestration layer are a separate contract.
pub fn decode_clipping_robust_ax25_ci16(
    path: &Path,
    config: &BlindPhaseFskConfig,
    selected_windows: Option<&[PhaseWindowCandidate]>,
) -> Result<BlindPhaseFskResult> {
    decode_ci16_with(
        path,
        config,
        selected_windows,
        cluster_paths,
        soft::protocol_constrained_syndrome_decode,
    )
}

// Private dependency injection makes scheduler ordering and shared-budget
// invariants testable without pretending fabricated frames are telemetry.
fn decode_ci16_with<C, D>(
    path: &Path,
    config: &BlindPhaseFskConfig,
    selected_windows: Option<&[PhaseWindowCandidate]>,
    mut cluster: C,
    mut decode: D,
) -> Result<BlindPhaseFskResult>
where
    C: FnMut(Vec<ReceiverPath>, f64) -> Vec<Vec<ReceiverPath>>,
    D: FnMut(
        &[f64],
        f64,
        &soft::SoftListBudget,
        &soft::SoftDecodeOptions,
    ) -> Result<soft::SoftListResult>,
{
    config.validate()?;
    let before = input::identity(path)?;
    if before.bytes == 0 || before.bytes % 4 != 0 {
        return Err("CI16 input must contain complete nonempty IQ pairs".into());
    }
    let input_count = before.bytes / 4;
    let windows = match selected_windows {
        Some(w) => w.to_vec(),
        None => select_ci16_phase_windows(path, config)?,
    };
    if windows.is_empty() || windows.len() > config.candidate_window_limit {
        return Err("selected windows must be nonempty and bounded".into());
    }
    let decoder_samples =
        checked_window_samples(config.decoder_window_seconds, config.sample_rate_hz)?;
    let mut file = input::open_regular(path)?;
    let mut frames = Vec::new();
    let mut hypotheses = 0;
    let mut attempted = 0;
    let mut native_attempted = 0;
    let mut repair_attempted = 0;
    for window in &windows {
        if !window.decoder_start_seconds.is_finite() || window.decoder_start_seconds < 0.0 {
            return Err("invalid selected window start".into());
        }
        let offset =
            (window.decoder_start_seconds * config.sample_rate_hz as f64).round_ties_even();
        if !offset.is_finite() || offset + decoder_samples as f64 > input_count as f64 {
            return Err("decoder window extends beyond CI16 capture".into());
        }
        file.seek(SeekFrom::Start(
            (offset as u64)
                .checked_mul(4)
                .ok_or("CI16 offset overflow")?,
        ))
        .map_err(|e| e.to_string())?;
        let raw = read_pairs(&mut file, decoder_samples)?;
        let mut paths = Vec::new();
        for radius in &config.constant_radius_factors {
            let iq = constant_radius_declip_exact_endpoints(&raw, *radius)?;
            for lag in &config.phase_difference_lags {
                let front = phase_first(&iq, *lag, config)?;
                let timing_config = dsp::DspConfig {
                    baud: config.baudrate,
                    mode: "fsk".into(),
                    rate_errors_ppm: config.rate_errors_ppm.clone(),
                    phase_bins: config.phase_bins,
                    top_timing: config
                        .short_search_timing_hypotheses
                        .max(config.deep_search_timing_hypotheses),
                    bank: "global".into(),
                };
                let mut bank = dsp::ranked_timing(&front, &timing_config)?;
                bank.truncate(timing_config.top_timing);
                for (rank, timing) in bank.into_iter().enumerate() {
                    let symbols = Arc::new(dsp::soft_symbols(&front, &timing)?);
                    let levels: Vec<_> = symbols
                        .iter()
                        .map(|x| u8::from(*x >= timing.threshold))
                        .collect();
                    for descramble in &config.descramble_modes {
                        hypotheses += 1;
                        let plain = decode_levels(&levels, *descramble);
                        for start in soft::preamble_terminated_frame_starts(
                            &plain,
                            config.minimum_consecutive_flags,
                            config.minimum_frame_body_bits,
                        )? {
                            if paths.len() >= config.maximum_receiver_paths_per_window {
                                return Err("receiver path bound exceeded before clustering".into());
                            }
                            paths.push(ReceiverPath {
                                radius: *radius,
                                lag: *lag,
                                descramble: *descramble,
                                rank,
                                timing: timing.clone(),
                                soft: Arc::clone(&symbols),
                                start,
                                estimated: estimated_start(
                                    window.decoder_start_seconds,
                                    start,
                                    &timing,
                                    *lag,
                                    config,
                                )?,
                            });
                        }
                    }
                }
            }
        }
        let events = cluster(
            paths,
            config.event_cluster_tolerance_symbols as f64 / config.baudrate,
        );
        let native_budget = soft::SoftListBudget {
            least_reliable_symbols: 1,
            maximum_flips: 0,
            maximum_regions: config.maximum_regions_per_start,
            maximum_attempts: config.maximum_regions_per_start,
            maximum_attempts_per_region: Some(1),
            maximum_output_frames: 1,
            ..Default::default()
        };
        let mut unresolved = Vec::new();
        // Exhaust strict paths before any correction is allowed to spend budget.
        for mut event in events {
            event.sort_by(path_compare);
            let mut native_found = false;
            for receiver_path in &event {
                let options = soft::SoftDecodeOptions {
                    event_start_symbol: Some(receiver_path.start as f64),
                    require_ax25_ui: true,
                    stop_after_first_frame: true,
                    stop_after_uncorrected_frame: true,
                    descramble: receiver_path.descramble,
                    ..Default::default()
                };
                let result = decode(
                    &receiver_path.soft,
                    receiver_path.timing.threshold,
                    &native_budget,
                    &options,
                )?;
                if result.output_limit_reached {
                    return Err("native path output bound exceeded".into());
                }
                attempted += result.attempted_candidates;
                native_attempted += result.attempted_candidates;
                for decoded in &result.frames {
                    if !decoded.flipped_symbol_indices.is_empty() {
                        continue;
                    }
                    append_frame(
                        &mut frames,
                        materialize(
                            receiver_path,
                            decoded,
                            "native_all_paths",
                            result.attempted_candidates,
                            window.decoder_start_seconds,
                            config,
                        )?,
                        config.maximum_frame_detections,
                    )?;
                    native_found = true;
                    break;
                }
                if native_found {
                    break;
                }
            }
            if !native_found {
                unresolved.push(event);
            }
        }
        let mut window_repaired = Vec::new();
        let mut window_attempts = 0;
        for event in unresolved
            .into_iter()
            .take(config.repair_window_maximum_events)
        {
            let mut event_attempts = 0;
            let mut native_found = false;
            let mut repaired: BTreeMap<Vec<u8>, ClippingRobustAx25Frame> = BTreeMap::new();
            for receiver_path in &event {
                let remaining = config
                    .repair_event_maximum_attempts
                    .saturating_sub(event_attempts)
                    .min(
                        config
                            .repair_window_maximum_attempts
                            .saturating_sub(window_attempts),
                    );
                if remaining == 0 {
                    break;
                }
                let limit = config
                    .deep_maximum_attempts
                    .min(config.repair_path_maximum_attempts)
                    .min(remaining);
                let budget = soft::SoftListBudget {
                    maximum_attempts: limit,
                    maximum_attempts_per_region: Some(
                        config.repair_region_maximum_attempts.min(limit),
                    ),
                    ..config.deep_budget()
                };
                let options = soft::SoftDecodeOptions {
                    event_start_symbol: Some(receiver_path.start as f64),
                    require_ax25_ui: true,
                    stop_after_first_frame: false,
                    stop_after_uncorrected_frame: true,
                    descramble: receiver_path.descramble,
                    ..Default::default()
                };
                let result = decode(
                    &receiver_path.soft,
                    receiver_path.timing.threshold,
                    &budget,
                    &options,
                )?;
                if result.output_limit_reached {
                    return Err("repair path output bound exceeded".into());
                }
                event_attempts += result.attempted_candidates;
                window_attempts += result.attempted_candidates;
                attempted += result.attempted_candidates;
                repair_attempted += result.attempted_candidates;
                for decoded in &result.frames {
                    let frame = materialize(
                        receiver_path,
                        decoded,
                        "global_shared_repair",
                        result.attempted_candidates,
                        window.decoder_start_seconds,
                        config,
                    )?;
                    if decoded.flipped_symbol_indices.is_empty() {
                        append_frame(&mut frames, frame, config.maximum_frame_detections)?;
                        native_found = true;
                        break;
                    }
                    if repaired.get(&decoded.frame_with_fcs).is_none_or(|old| {
                        repaired_compare(&frame, old, config.error_unit_penalty) == Ordering::Less
                    }) {
                        repaired.insert(decoded.frame_with_fcs.clone(), frame);
                    }
                }
                if native_found {
                    break;
                }
            }
            if !native_found {
                let mut ranked: Vec<_> = repaired.into_values().collect();
                ranked.sort_by(|a, b| repaired_compare(a, b, config.error_unit_penalty));
                window_repaired.extend(
                    ranked
                        .into_iter()
                        .take(config.repair_event_maximum_unique_frames),
                );
            }
        }
        window_repaired.sort_by(|a, b| repaired_compare(a, b, config.error_unit_penalty));
        for frame in window_repaired
            .into_iter()
            .take(config.repair_window_maximum_unique_frames)
        {
            append_frame(&mut frames, frame, config.maximum_frame_detections)?;
        }
    }
    let after = input::identity(path)?;
    if before.sha256 != after.sha256 || before.bytes != after.bytes || before.path != after.path {
        return Err("CI16 source changed during replay".into());
    }
    Ok(BlindPhaseFskResult {
        input_path: before.path,
        input_sha256: before.sha256,
        input_complex_samples: input_count,
        input_duration_seconds: input_count as f64 / config.sample_rate_hz as f64,
        decoded_windows: windows.len(),
        selected_windows: windows,
        timing_hypotheses_examined: hypotheses,
        protocol_candidates_attempted: attempted,
        native_protocol_candidates_attempted: native_attempted,
        repair_protocol_candidates_attempted: repair_attempted,
        frames,
        frontend_input_representation: "constant_radius_endpoint_reconstruction_not_recorded_IQ"
            .into(),
        repaired_frames_require_external_verification: true,
    })
}

/// Groups detections that the caller has already validated. This function is
/// NOT a parser or an integrity check: `native_trusted` is the legacy policy
/// flag for an uncorrected detection, not an independent FCS-validation result.
pub fn group_ax25_frame_consensus(
    detections: &[ClippingRobustAx25Frame],
    tolerance: f64,
) -> Result<Vec<Ax25FrameConsensus>> {
    if !tolerance.is_finite() || tolerance < 0.0 {
        return Err("consensus tolerance must be finite and nonnegative".into());
    }
    let mut ordered = detections.to_vec();
    if ordered.iter().any(|x| {
        !x.estimated_frame_start_seconds.is_finite() || !x.constant_radius_factor.is_finite()
    }) {
        return Err("nonfinite consensus provenance".into());
    }
    ordered.sort_by(|a, b| {
        float_cmp(
            a.estimated_frame_start_seconds,
            b.estimated_frame_start_seconds,
        )
        .then(a.payload.cmp(&b.payload))
        .then(float_cmp(
            a.constant_radius_factor,
            b.constant_radius_factor,
        ))
        .then(a.phase_difference_lag.cmp(&b.phase_difference_lag))
        .then(a.g3ruh_descramble.cmp(&b.g3ruh_descramble))
    });
    let mut groups: Vec<Vec<ClippingRobustAx25Frame>> = Vec::new();
    for detection in ordered {
        if let Some(group) = groups.iter_mut().find(|g| {
            g[0].payload == detection.payload
                && (g[0].estimated_frame_start_seconds - detection.estimated_frame_start_seconds)
                    .abs()
                    <= tolerance
        }) {
            group.push(detection);
        } else {
            groups.push(vec![detection]);
        }
    }
    let mut output = Vec::new();
    for mut group in groups {
        group.sort_by(|a, b| {
            float_cmp(a.constant_radius_factor, b.constant_radius_factor)
                .then(a.phase_difference_lag.cmp(&b.phase_difference_lag))
                .then(a.g3ruh_descramble.cmp(&b.g3ruh_descramble))
                .then(float_cmp(a.timing.rate_error_ppm, b.timing.rate_error_ppm))
                .then(float_cmp(a.timing.phase_samples, b.timing.phase_samples))
        });
        let mut frontends: Vec<_> = group
            .iter()
            .map(|x| (x.constant_radius_factor, x.phase_difference_lag))
            .collect();
        frontends.sort_by(|a, b| float_cmp(a.0, b.0).then(a.1.cmp(&b.1)));
        frontends.dedup();
        let modes = group
            .iter()
            .map(|x| x.g3ruh_descramble)
            .collect::<BTreeSet<_>>()
            .into_iter()
            .collect();
        let uncorrected = group.iter().any(|x| x.flipped_symbol_indices.is_empty());
        output.push(Ax25FrameConsensus {
            payload: group[0].payload.clone(),
            fcs: group[0].fcs.clone(),
            estimated_frame_start_seconds: group
                .iter()
                .map(|x| x.estimated_frame_start_seconds)
                .sum::<f64>()
                / group.len() as f64,
            has_correction_consensus: frontends.len() >= 2,
            independent_frontends: frontends,
            g3ruh_descramble_modes: modes,
            has_uncorrected_detection: uncorrected,
            native_trusted: uncorrected,
            detections: group,
        });
    }
    output.sort_by(|a, b| {
        float_cmp(
            a.estimated_frame_start_seconds,
            b.estimated_frame_start_seconds,
        )
        .then(a.payload.cmp(&b.payload))
    });
    Ok(output)
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct DeclippingMetrics {
    pub iterations: usize,
    pub scalar_count: usize,
    pub positive_clipped_scalars: usize,
    pub negative_clipped_scalars: usize,
    pub clipped_fraction: f64,
    pub out_of_band_energy_fraction_before: f64,
    pub out_of_band_energy_fraction_after_constraint_projection: f64,
    pub maximum_absolute_component: f64,
    pub maximum_unclipped_constraint_error: f64,
    pub minimum_positive_clip_margin: f64,
    pub minimum_negative_clip_margin: f64,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct DeclippingCheckpoint {
    pub reconstruction: Vec<[f64; 2]>,
    pub metrics: DeclippingMetrics,
    pub representation: String,
}

pub fn projected_bandlimited_declipping(
    components: &[[i16; 2]],
    sample_rate: f64,
    bandlimit: f64,
    checkpoints: &[usize],
) -> Result<BTreeMap<usize, DeclippingCheckpoint>> {
    if !(256..=MAX_WINDOW_SAMPLES).contains(&components.len())
        || !sample_rate.is_finite()
        || !bandlimit.is_finite()
        || sample_rate <= 0.0
        || bandlimit <= 0.0
        || bandlimit >= sample_rate / 2.0
    {
        return Err("invalid bounded bandlimit projection input".into());
    }
    let checkpoints: BTreeSet<_> = checkpoints.iter().copied().collect();
    if checkpoints.is_empty()
        || checkpoints.contains(&0)
        || *checkpoints.last().unwrap() > MAX_PROJECTION_ITERATIONS
    {
        return Err("projection checkpoints must be in 1..10000".into());
    }
    let n = components.len();
    if checkpoints
        .len()
        .checked_mul(n)
        .and_then(|x| x.checked_mul(16))
        .is_none_or(|bytes| bytes > MAX_PROJECTION_OUTPUT_BYTES)
    {
        return Err("projection checkpoint output exceeds 512 MiB bound".into());
    }
    let mut planner = FftPlanner::<f64>::new();
    let forward = planner.plan_fft_forward(n);
    let inverse = planner.plan_fft_inverse(n);
    let passband: Vec<_> = (0..n)
        .map(|i| {
            let bin = if i < n.div_ceil(2) {
                i as f64
            } else {
                i as f64 - n as f64
            };
            (bin * (sample_rate / n as f64)).abs() <= bandlimit
        })
        .collect();
    let oob = |state: &[Complex64]| {
        let mut spectrum = state.to_vec();
        forward.process(&mut spectrum);
        let energy: Vec<_> = spectrum
            .iter()
            .map(|z| {
                let a = z.norm();
                a * a
            })
            .collect();
        let total = dsp::pairwise_sum(&energy);
        if total <= 0.0 {
            0.0
        } else {
            dsp::pairwise_sum(
                &energy
                    .iter()
                    .zip(&passband)
                    .filter_map(|(e, p)| (!p).then_some(*e))
                    .collect::<Vec<_>>(),
            ) / total
        }
    };
    let mut state: Vec<_> = components
        .iter()
        .map(|[i, q]| Complex64::new(*i as f64, *q as f64))
        .collect();
    let before = oob(&state);
    let positive = components
        .iter()
        .flatten()
        .filter(|x| **x == i16::MAX)
        .count();
    let negative = components
        .iter()
        .flatten()
        .filter(|x| **x == i16::MIN)
        .count();
    let mut output = BTreeMap::new();
    for iteration in 1..=*checkpoints.last().unwrap() {
        let mut spectrum = state.clone();
        forward.process(&mut spectrum);
        for (bin, pass) in spectrum.iter_mut().zip(&passband) {
            if !pass {
                *bin = Complex64::new(0.0, 0.0);
            }
        }
        inverse.process(&mut spectrum);
        let mut reconstructed = Vec::with_capacity(n);
        for (z, observed) in spectrum.iter().zip(components) {
            let mut pair = [z.re / n as f64, z.im / n as f64];
            for j in 0..2 {
                pair[j] = match observed[j] {
                    i16::MAX => pair[j].max(32767.0),
                    i16::MIN => pair[j].min(-32768.0),
                    x => x as f64,
                };
            }
            reconstructed.push(pair);
        }
        state = reconstructed
            .iter()
            .map(|[i, q]| Complex64::new(*i, *q))
            .collect();
        if checkpoints.contains(&iteration) {
            let posmargin = reconstructed
                .iter()
                .zip(components)
                .flat_map(|(r, o)| r.iter().zip(o))
                .filter_map(|(r, o)| (*o == i16::MAX).then_some(*r - 32767.0))
                .fold(f64::INFINITY, f64::min);
            let negmargin = reconstructed
                .iter()
                .zip(components)
                .flat_map(|(r, o)| r.iter().zip(o))
                .filter_map(|(r, o)| (*o == i16::MIN).then_some(-32768.0 - *r))
                .fold(f64::INFINITY, f64::min);
            let metrics = DeclippingMetrics {
                iterations: iteration,
                scalar_count: 2 * n,
                positive_clipped_scalars: positive,
                negative_clipped_scalars: negative,
                clipped_fraction: (positive + negative) as f64 / (2 * n) as f64,
                out_of_band_energy_fraction_before: before,
                out_of_band_energy_fraction_after_constraint_projection: oob(&state),
                maximum_absolute_component: reconstructed
                    .iter()
                    .flatten()
                    .map(|x| x.abs())
                    .fold(0.0, f64::max),
                maximum_unclipped_constraint_error: 0.0,
                minimum_positive_clip_margin: if positive > 0 { posmargin } else { 0.0 },
                minimum_negative_clip_margin: if negative > 0 { negmargin } else { 0.0 },
            };
            output.insert(
                iteration,
                DeclippingCheckpoint {
                    reconstruction: reconstructed,
                    metrics,
                    representation: "constrained_reconstruction_not_recorded_IQ".into(),
                },
            );
        }
    }
    Ok(output)
}

#[cfg(test)]
#[path = "tests/clipping_tests.rs"]
mod tests;
