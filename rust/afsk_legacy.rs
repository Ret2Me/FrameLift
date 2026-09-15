//! Explicit packaged-AFSK compatibility path, separate from the generic bank.
//!
//! The old AFSK receiver uses sixteen guard symbols and a different symbol
//! count for every rate/phase. Neither the qualified FSK nor audio bank is
//! changed here. CRC-valid structural rejections remain rejected candidates.

use crate::{dsp, protocol};
use num_complex::Complex64;
use serde::{Deserialize, Serialize};
use std::collections::{BTreeMap, HashSet};

pub const LEGACY_GUARD_SYMBOLS: usize = 16;
const MAX_GRID: usize = 65_536;

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(default, deny_unknown_fields)]
pub struct Afsk1200Config {
    pub sample_rate_hz: u32,
    pub baudrate: f64,
    pub mark_hz: f64,
    pub space_hz: f64,
    pub audio_sample_rate_hz: u32,
    pub window_blocks: usize,
    pub stride_blocks: usize,
    pub permutation_block_samples: usize,
    pub timing_rate_errors_ppm: Vec<f64>,
    pub timing_phase_bins: usize,
    pub timing_top_n: usize,
    pub max_candidate_records: usize,
}
impl Default for Afsk1200Config {
    fn default() -> Self {
        Self {
            sample_rate_hz: 57_600,
            baudrate: 1200.,
            mark_hz: 1200.,
            space_hz: 2200.,
            audio_sample_rate_hz: 9600,
            window_blocks: 280,
            stride_blocks: 140,
            permutation_block_samples: 4096,
            timing_rate_errors_ppm: vec![-2000., -1000., -500., 0., 500., 1000., 2000.],
            timing_phase_bins: 16,
            timing_top_n: 6,
            max_candidate_records: 4096,
        }
    }
}
impl Afsk1200Config {
    pub fn validate(&self) -> Result<(), String> {
        if self.sample_rate_hz <= 6000
            || self.audio_sample_rate_hz == 0
            || !self
                .sample_rate_hz
                .is_multiple_of(self.audio_sample_rate_hz)
            || !self.baudrate.is_finite()
            || self.baudrate <= 0.
            || self.audio_sample_rate_hz as f64 / self.baudrate <= 2.
            || !self.mark_hz.is_finite()
            || !self.space_hz.is_finite()
            || self.mark_hz <= 0.
            || self.space_hz <= 0.
            || self.mark_hz == self.space_hz
            || self.mark_hz >= self.audio_sample_rate_hz as f64 / 2.
            || self.space_hz >= self.audio_sample_rate_hz as f64 / 2.
        {
            return Err("legacy AFSK needs explicit compatible sample rates, baud and distinct Bell202 tones".into());
        }
        if self.window_blocks < 2
            || self.stride_blocks == 0
            || self.stride_blocks >= self.window_blocks
            || self.permutation_block_samples < 32
            || self.max_candidate_records == 0
            || self.max_candidate_records > 1_000_000
            || self
                .window_blocks
                .checked_mul(self.permutation_block_samples)
                .is_none()
        {
            return Err("invalid legacy AFSK window/candidate budget".into());
        }
        validate_timing(
            self.audio_sample_rate_hz as f64 / self.baudrate,
            &self.timing_rate_errors_ppm,
            self.timing_phase_bins,
            self.timing_top_n,
        )
    }
    fn dsp_config(&self) -> dsp::DspConfig {
        dsp::DspConfig {
            baud: self.baudrate,
            mode: "afsk".into(),
            rate_errors_ppm: self.timing_rate_errors_ppm.clone(),
            phase_bins: self.timing_phase_bins,
            top_timing: self.timing_top_n,
            bank: "global".into(),
        }
    }
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct AfskTiming {
    #[serde(flatten)]
    pub timing: dsp::TimingHypothesis,
    pub phase_index: usize,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct AfskStrictFrame {
    pub frame_with_fcs: Vec<u8>,
    pub normalized_pdu: Vec<u8>,
    pub ax25: protocol::Ax25UiFrame,
    pub inner_ccsds: Option<protocol::CcsdsPrimaryHeader>,
    pub window_start_sample: u64,
    pub rate_error_ppm: f64,
    pub phase_index: usize,
    pub polarity: String,
}
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct AfskRejectedFrame {
    pub frame_with_fcs: Vec<u8>,
    pub rejection_reason: String,
    pub window_start_sample: u64,
    pub rate_error_ppm: f64,
    pub phase_index: usize,
    pub polarity: String,
}
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct AfskDecodeResult {
    pub frames: Vec<AfskStrictFrame>,
    pub windows_examined: usize,
    pub timing_hypotheses_examined: usize,
    pub crc_valid_candidates: usize,
    pub strict_ax25_rejections: usize,
    pub degenerate_windows: usize,
    pub rejected_frames: Vec<AfskRejectedFrame>,
}
fn empty_result(degenerate: bool) -> AfskDecodeResult {
    AfskDecodeResult {
        frames: vec![],
        windows_examined: 1,
        timing_hypotheses_examined: 0,
        crc_valid_candidates: 0,
        strict_ax25_rejections: 0,
        degenerate_windows: usize::from(degenerate),
        rejected_frames: vec![],
    }
}

fn validate_timing(sps: f64, rates: &[f64], phase_bins: usize, top: usize) -> Result<(), String> {
    if !sps.is_finite()
        || sps <= 2.
        || rates.is_empty()
        || phase_bins < 4
        || top == 0
        || phase_bins
            .checked_mul(rates.len())
            .is_none_or(|n| n > MAX_GRID)
        || rates.iter().any(|rate| {
            !rate.is_finite()
                || !((sps * (1. + rate * 1e-6)).is_finite())
                || sps * (1. + rate * 1e-6) <= 1.
        })
    {
        return Err("legacy AFSK timing requires finite valid steps and a grid of at most65536; nothing was pruned".into());
    }
    Ok(())
}
fn interpolate(values: &[f64], position: f64) -> f64 {
    if position <= 0. {
        return values[0];
    }
    let i = position as usize;
    if i >= values.len() - 1 {
        return values[values.len() - 1];
    }
    (values[i + 1] - values[i]) * (position - i as f64) + values[i]
}
fn median(values: &mut [f64]) -> f64 {
    let middle = values.len() / 2;
    let even = values.len().is_multiple_of(2);
    let (lower, center, _) = values.select_nth_unstable_by(middle, |a, b| a.total_cmp(b));
    if even {
        (lower.iter().copied().fold(f64::NEG_INFINITY, f64::max) + *center) / 2.
    } else {
        *center
    }
}

fn ranked(
    soft: &[f64],
    sps: f64,
    rates: &[f64],
    phases: usize,
    top: usize,
) -> Result<Vec<AfskTiming>, String> {
    validate_timing(sps, rates, phases, top)?;
    if soft.len() > dsp::MAX_PCM_WINDOW_SAMPLES || soft.iter().any(|x| !x.is_finite()) {
        return Err("legacy AFSK soft input must be finite and bounded to4194304 samples".into());
    }
    let mut rates = rates.to_vec();
    rates.sort_by(|a, b| a.partial_cmp(b).unwrap());
    rates.dedup_by(|a, b| *a == *b);
    let mut output = Vec::with_capacity(rates.len() * phases);
    let (mut symbols, mut scratch, mut lower, mut upper) =
        (Vec::new(), Vec::new(), Vec::new(), Vec::new());
    for rate in rates {
        let step = sps * (1. + rate * 1e-6);
        for phase_index in 0..phases {
            let phase = step * (phase_index as f64 + 0.5) / phases as f64;
            // Python int truncates toward zero; negative/short spans are empty.
            let raw_count = ((soft.len() as f64 - phase) / step).trunc();
            if raw_count < 160. {
                continue;
            }
            let count = raw_count as usize - 2 * LEGACY_GUARD_SYMBOLS;
            symbols.clear();
            symbols.extend(
                (LEGACY_GUARD_SYMBOLS..LEGACY_GUARD_SYMBOLS + count)
                    .map(|index| interpolate(soft, phase + index as f64 * step)),
            );
            scratch.clear();
            scratch.extend_from_slice(&symbols);
            let mut threshold = median(&mut scratch);
            lower.clear();
            upper.clear();
            for &symbol in &symbols {
                if symbol < threshold {
                    lower.push(symbol);
                } else {
                    upper.push(symbol);
                }
            }
            let score = if lower.len() < 16 || upper.len() < 16 {
                f64::NEG_INFINITY
            } else {
                let low = dsp::pairwise_sum(&lower) / lower.len() as f64;
                let high = dsp::pairwise_sum(&upper) / upper.len() as f64;
                threshold = 0.5 * (low + high);
                for x in &mut lower {
                    *x = (*x - low) * (*x - low);
                }
                for x in &mut upper {
                    *x = (*x - high) * (*x - high);
                }
                let within = (0.5
                    * (dsp::pairwise_sum(&lower) / lower.len() as f64
                        + dsp::pairwise_sum(&upper) / upper.len() as f64)
                    + 1e-15)
                    .sqrt();
                (high - low) / within
            };
            output.push(AfskTiming {
                phase_index,
                timing: dsp::TimingHypothesis {
                    rate_error_ppm: rate,
                    phase_samples: phase,
                    step_samples: step,
                    threshold,
                    score,
                    symbol_count: count,
                },
            });
        }
    }
    output.sort_by(|a, b| {
        b.timing
            .score
            .total_cmp(&a.timing.score)
            .then_with(|| {
                a.timing
                    .rate_error_ppm
                    .abs()
                    .total_cmp(&b.timing.rate_error_ppm.abs())
            })
            .then_with(|| a.timing.rate_error_ppm.total_cmp(&b.timing.rate_error_ppm))
            .then_with(|| a.timing.phase_samples.total_cmp(&b.timing.phase_samples))
    });
    output.truncate(top);
    Ok(output)
}

pub fn timing_candidates(soft: &[f64], config: &Afsk1200Config) -> Result<Vec<AfskTiming>, String> {
    config.validate()?;
    ranked(
        soft,
        config.audio_sample_rate_hz as f64 / config.baudrate,
        &config.timing_rate_errors_ppm,
        config.timing_phase_bins,
        config.timing_top_n,
    )
}

/// Generic integration must pair this bank with `legacy_soft_symbols` below.
/// The explicit legacy profile keeps top-N selection; it does not silently
/// reinterpret a caller's diverse/full bank as the old standalone bank.
pub fn timing_bank(
    frontend: &dsp::Frontend,
    config: &dsp::DspConfig,
) -> Result<Vec<dsp::TimingHypothesis>, String> {
    if config.mode != "afsk" || config.bank != "global" {
        return Err("legacy AFSK timing profile requires mode afsk and bank global".into());
    }
    Ok(ranked(
        &frontend.samples,
        frontend.samples_per_symbol,
        &config.rate_errors_ppm,
        config.phase_bins,
        config.top_timing,
    )?
    .into_iter()
    .map(|c| c.timing)
    .collect())
}

fn extract_symbols(soft: &[f64], timing: &dsp::TimingHypothesis) -> Result<Vec<f64>, String> {
    if soft.len() > dsp::MAX_PCM_WINDOW_SAMPLES
        || soft.iter().any(|x| !x.is_finite())
        || !timing.phase_samples.is_finite()
        || !timing.step_samples.is_finite()
        || timing.step_samples <= 0.
        || !timing.threshold.is_finite()
    {
        return Err("legacy AFSK timing/soft samples must be finite and bounded".into());
    }
    if timing.symbol_count == 0 {
        return Ok(vec![]);
    }
    let end = LEGACY_GUARD_SYMBOLS
        .checked_add(timing.symbol_count)
        .ok_or("legacy AFSK symbol count overflow")?;
    let last = timing.phase_samples + (end - 1) as f64 * timing.step_samples;
    if soft.is_empty() || !last.is_finite() || last > (soft.len() - 1) as f64 {
        return Err("legacy AFSK timing extends beyond soft input".into());
    }
    Ok((LEGACY_GUARD_SYMBOLS..end)
        .map(|index| {
            interpolate(
                soft,
                timing.phase_samples + index as f64 * timing.step_samples,
            )
        })
        .collect())
}
pub fn soft_symbols(soft: &[f64], timing: &AfskTiming) -> Result<Vec<f64>, String> {
    extract_symbols(soft, &timing.timing)
}
pub fn legacy_soft_symbols(
    frontend: &dsp::Frontend,
    timing: &dsp::TimingHypothesis,
) -> Result<Vec<f64>, String> {
    extract_symbols(&frontend.samples, timing)
}

/// Exact old candidate extractor: first NRZI bit is forced to zero, no G3RUH,
/// complete adjacent flags, exact stuffing/alignment, original FCS preserved.
pub fn crc_frames_from_levels(levels: &[u8]) -> Result<Vec<Vec<u8>>, String> {
    if levels.len() > dsp::MAX_PCM_WINDOW_SAMPLES || levels.iter().any(|bit| *bit > 1) {
        return Err("legacy AFSK levels must be binary and bounded".into());
    }
    if levels.len() < 16 {
        return Ok(vec![]);
    }
    let mut bits = Vec::with_capacity(levels.len());
    bits.push(0);
    bits.extend(levels.windows(2).map(|pair| u8::from(pair[0] == pair[1])));
    let max_unstuffed = (protocol::AX25_MAX_DECODER_FRAME_BYTES - 1) * 8;
    let max_stuffed = max_unstuffed + max_unstuffed.div_ceil(5);
    let (mut previous, mut seen, mut frames) = (None, HashSet::new(), Vec::new());
    for (right, flag) in bits.windows(8).enumerate() {
        if flag != [0, 1, 1, 1, 1, 1, 1, 0] {
            continue;
        }
        if let Some(left) = previous
            && right > left + 8
            && right - left - 8 <= max_stuffed
            && let Ok(unstuffed) = protocol::hdlc_unstuff(&bits[left + 8..right])
            && let Ok(frame) = protocol::bits_to_bytes(&unstuffed, true)
            && frame.len() >= protocol::AX25_MIN_PAYLOAD_BYTES + 2
            && frame.len() < protocol::AX25_MAX_DECODER_FRAME_BYTES
            && protocol::valid_ax25_fcs(&frame)
            && seen.insert(frame.clone())
        {
            frames.push(frame);
        }
        previous = Some(right);
    }
    Ok(frames)
}

pub fn decode_afsk1200_iq(
    iq: &[Complex64],
    config: &Afsk1200Config,
    window_start_sample: u64,
) -> Result<AfskDecodeResult, String> {
    config.validate()?;
    let frontend = match dsp::bell202_iq_frontend(
        iq,
        config.sample_rate_hz,
        &config.dsp_config(),
        (config.sample_rate_hz / config.audio_sample_rate_hz) as usize,
        config.mark_hz,
        config.space_hz,
    ) {
        Ok(frontend) => frontend,
        Err(error) if error.contains("degenerate") => return Ok(empty_result(true)),
        Err(error) => return Err(error),
    };
    decode_soft(&frontend.samples, config, window_start_sample)
}

pub fn decode_soft(
    soft: &[f64],
    config: &Afsk1200Config,
    window_start_sample: u64,
) -> Result<AfskDecodeResult, String> {
    let bank = timing_candidates(soft, config)?;
    let mut result = empty_result(false);
    let (mut accepted, mut rejected) = (BTreeMap::new(), BTreeMap::new());
    for timing in bank {
        let symbols = soft_symbols(soft, &timing)?;
        let normal: Vec<u8> = symbols
            .iter()
            .map(|value| u8::from(*value >= timing.timing.threshold))
            .collect();
        for polarity in ["normal", "inverted"] {
            result.timing_hypotheses_examined += 1;
            let levels: Vec<u8> = if polarity == "normal" {
                normal.clone()
            } else {
                normal.iter().map(|bit| 1 - bit).collect()
            };
            for frame in crc_frames_from_levels(&levels)? {
                result.crc_valid_candidates += 1;
                let normalized = frame[..frame.len() - 2].to_vec();
                if let Some(ax25) = protocol::parse_ax25_ui(&normalized) {
                    accepted
                        .entry(normalized.clone())
                        .or_insert_with(|| AfskStrictFrame {
                            frame_with_fcs: frame,
                            normalized_pdu: normalized,
                            inner_ccsds: protocol::parse_ccsds_space_packet(
                                &ax25.information,
                                true,
                            ),
                            ax25,
                            window_start_sample,
                            rate_error_ppm: timing.timing.rate_error_ppm,
                            phase_index: timing.phase_index,
                            polarity: polarity.into(),
                        });
                } else {
                    result.strict_ax25_rejections += 1;
                    rejected
                        .entry(frame.clone())
                        .or_insert_with(|| AfskRejectedFrame {
                            frame_with_fcs: frame,
                            rejection_reason: "strict_parse_ax25_ui_rejected_after_fcs".into(),
                            window_start_sample,
                            rate_error_ppm: timing.timing.rate_error_ppm,
                            phase_index: timing.phase_index,
                            polarity: polarity.into(),
                        });
                }
                if accepted.len() + rejected.len() > config.max_candidate_records {
                    return Err("AFSK candidate record bound exceeded".into());
                }
            }
        }
    }
    result.frames = accepted.into_values().collect();
    result.rejected_frames = rejected.into_values().collect();
    Ok(result)
}

#[cfg(test)]
#[path = "tests/afsk_legacy_tests.rs"]
mod tests;
