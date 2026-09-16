//! Optional, explicitly bounded coherent CPM acquisition for HDLC traffic.
//! Repeated protocol flags train the channel; frame lengths come from received
//! flags after detection. G3RUH state is a signal-derived hypothesis, not zero.
use super::{Config as ReceiverConfig, FLAG, LineCoding, Provenance, Scrambler, Span};
use crate::coherent_cpm::{self, CpmBoundary};
use num_complex::Complex64;
use serde::{Deserialize, Serialize};
use std::{collections::BTreeSet, f64::consts::TAU};

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Config {
    pub modulation_index_numerator: u8,
    pub modulation_index_denominator: u8,
    /// None is rectangular CPFSK; Some(BT) is finite +/- two-symbol Gaussian.
    pub gaussian_bt: Option<f64>,
    /// Integer offsets on the original sample grid, explicitly searched.
    pub phase_offsets_samples: Vec<usize>,
    pub carrier_centers_hz: Vec<f64>,
    pub max_residual_carrier_hz: f64,
    /// 8..32 consecutive flags form the channel pilot (64..256 symbols).
    pub preamble_flags: usize,
    /// Includes pilot and variable-length target, at most the kernel's 4096.
    pub maximum_candidate_symbols: usize,
    /// Exceeding this count is an error, never outcome-based pruning.
    pub maximum_candidates: usize,
    pub minimum_training_coherence: f64,
}

impl Config {
    pub(super) fn validate(&self, receiver: &ReceiverConfig, rate: u32) -> Result<(), String> {
        let sps = rate as f64 / receiver.waveform.dsp.baud;
        let h = f64::from(self.modulation_index_numerator)
            / f64::from(self.modulation_index_denominator);
        let deviation = h * receiver.waveform.dsp.baud / 2.;
        if !matches!(
            receiver.waveform.demodulator_id.as_str(),
            "phase_fsk" | "channel_conditioned_phase_fsk"
        ) || receiver.waveform.dsp.mode != "fsk"
            || !(2. ..=32.).contains(&sps)
            || sps.fract().abs() > 1e-10
            || receiver
                .waveform
                .dsp
                .rate_errors_ppm
                .iter()
                .any(|x| *x != 0.)
            || !(1..=16).contains(&self.modulation_index_numerator)
            || !(1..=16).contains(&self.modulation_index_denominator)
            || self.modulation_index_numerator > self.modulation_index_denominator
            || self
                .gaussian_bt
                .is_some_and(|v| !v.is_finite() || !(0.2..=1.).contains(&v))
            || self.phase_offsets_samples.is_empty()
            || self.phase_offsets_samples.len() > 32
            || self
                .phase_offsets_samples
                .iter()
                .any(|x| *x >= sps as usize)
            || self
                .phase_offsets_samples
                .iter()
                .copied()
                .collect::<BTreeSet<_>>()
                .len()
                != self.phase_offsets_samples.len()
            || self.carrier_centers_hz.is_empty()
            || self.carrier_centers_hz.len() > 8
            || self.carrier_centers_hz.iter().any(|x| {
                !x.is_finite()
                    || x.abs() + self.max_residual_carrier_hz + deviation >= rate as f64 / 2.
            })
            || !self.max_residual_carrier_hz.is_finite()
            || self.max_residual_carrier_hz < 0.
            || TAU * self.max_residual_carrier_hz / rate as f64 > 0.2
            || !(8..=32).contains(&self.preamble_flags)
            || !(256..=4096).contains(&self.maximum_candidate_symbols)
            || self.maximum_candidate_symbols < self.preamble_flags * 8 + 160
            || !(1..=64).contains(&self.maximum_candidates)
            || !self.minimum_training_coherence.is_finite()
            || !(0.5..=1.).contains(&self.minimum_training_coherence)
        {
            return Err("HDLC coherent CPM needs FSK IQ, integer2..32SPS, zero configured clock error, rational0<h<=1, finite Gaussian/pilot/acquisition bounds and <=4096symbols/candidate".into());
        }
        Ok(())
    }
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Receipt {
    pub stream: usize,
    pub start_sample: u64,
    pub end_sample: u64,
    pub carrier_center_hz: f64,
    pub phase_offset_samples: usize,
    pub pilot_symbols: usize,
    pub analysis_symbols: usize,
    pub line_state_source: String,
    pub diagnostics: Option<serde_json::Value>,
    pub reason: String,
}

pub(super) struct Outcome {
    pub frames: Vec<(Span, Provenance)>,
    pub receipts: Vec<Receipt>,
}

fn charge(work: &mut u64, extra: u64, limit: u64) -> Result<(), String> {
    *work = work
        .checked_add(extra)
        .ok_or("coherent HDLC work overflows")?;
    if *work > limit {
        return Err("coherent HDLC work budget exceeded".into());
    }
    Ok(())
}

fn decoded(levels: &[u8], c: &ReceiverConfig) -> Vec<u8> {
    let coded: Vec<_> = levels
        .iter()
        .enumerate()
        .map(|(i, b)| {
            if c.line_coding == LineCoding::Direct {
                *b
            } else if i == 0 {
                0
            } else {
                u8::from(*b == levels[i - 1])
            }
        })
        .collect();
    let mut bits = coded.clone();
    if c.scrambler == Scrambler::G3ruh {
        for i in 12..bits.len() {
            bits[i] ^= coded[i - 12];
        }
        for i in 17..bits.len() {
            bits[i] ^= coded[i - 17];
        }
    }
    bits
}

/// Candidate flag runs require every flag bit, with no tolerance selected from
/// CRC outcomes. Return each maximal run once, after a received non-flag gap.
fn flag_runs(bits: &[u8], minimum_start: usize, flags: usize) -> Vec<(usize, usize)> {
    let mut runs = Vec::new();
    let mut i = minimum_start;
    while i + 8 <= bits.len() {
        if bits[i..i + 8] != FLAG {
            i += 1;
            continue;
        }
        let begin = i;
        while i + 8 <= bits.len() && bits[i..i + 8] == FLAG {
            i += 8;
        }
        if i - begin >= flags * 8 {
            runs.push((begin, i));
        }
    }
    runs
}

/// Starting register bits are taken from RECEIVED acquisition decisions. After
/// this seed, protocol flag bits predict the physical pilot. No assumption is
/// made about the transmitter's power-on/reset scrambler state. The phase fit
/// separately checks this candidate pilot against complex waveform samples.
fn physical_pilot(
    levels: &[u8],
    start: usize,
    count: usize,
    c: &ReceiverConfig,
) -> Option<Vec<u8>> {
    let history = if c.scrambler == Scrambler::G3ruh {
        17
    } else {
        0
    };
    if start < history + usize::from(c.line_coding == LineCoding::Nrzi)
        || start + count > levels.len()
    {
        return None;
    }
    let received_coded = |i: usize| {
        if c.line_coding == LineCoding::Direct {
            levels[i]
        } else {
            u8::from(levels[i] == levels[i - 1])
        }
    };
    let mut coded: Vec<u8> = (start - history..start).map(received_coded).collect();
    let mut previous = if c.line_coding == LineCoding::Nrzi {
        levels[start - 1]
    } else {
        0
    };
    let mut predicted = Vec::with_capacity(count);
    for j in 0..count {
        let mut bit = FLAG[j % 8];
        if c.scrambler == Scrambler::G3ruh {
            bit ^= coded[coded.len() - 12] ^ coded[coded.len() - 17];
        }
        coded.push(bit);
        let level = if c.line_coding == LineCoding::Nrzi {
            previous ^ (1 - bit)
        } else {
            bit
        };
        predicted.push(level);
        previous = level;
    }
    (predicted == levels[start..start + count]).then_some(predicted)
}

fn states(config: &Config) -> usize {
    let p = usize::from(config.modulation_index_numerator);
    let mut a = p;
    let mut b = 2 * usize::from(config.modulation_index_denominator);
    while b != 0 {
        let r = a % b;
        a = b;
        b = r;
    }
    (2 * usize::from(config.modulation_index_denominator) / a)
        * if config.gaussian_bt.is_some() { 16 } else { 1 }
}

pub(super) fn recover(
    iq: &[Complex64],
    rate: u32,
    c: &ReceiverConfig,
    first_stream: usize,
    work: &mut u64,
) -> Result<Outcome, String> {
    let mut outcome = Outcome {
        frames: vec![],
        receipts: vec![],
    };
    let Some(config) = &c.coherent_cpm else {
        return Ok(outcome);
    };
    let sps = (rate as f64 / c.waveform.dsp.baud).round() as usize;
    let pilot_count = config.preamble_flags * 8;
    charge(
        work,
        iq.len() as u64
            * config.carrier_centers_hz.len() as u64
            * (16 + config.phase_offsets_samples.len() as u64 * 8),
        c.work_budget,
    )?;
    for &carrier in &config.carrier_centers_hz {
        let mixed: Vec<_> = iq
            .iter()
            .enumerate()
            .map(|(i, z)| z * Complex64::from_polar(1., -TAU * carrier * i as f64 / rate as f64))
            .collect();
        let mut discriminator = vec![0.; mixed.len()];
        for i in 1..mixed.len() {
            discriminator[i] = (mixed[i] * mixed[i - 1].conj()).arg();
        }
        for &offset in &config.phase_offsets_samples {
            let symbols = (iq.len() - offset) / sps;
            let observed: Vec<_> = (0..symbols)
                .map(|i| {
                    discriminator[offset + i * sps..offset + (i + 1) * sps]
                        .iter()
                        .sum::<f64>()
                        / sps as f64
                })
                .collect();
            // Positive/negative frequency levels are an explicit physical
            // convention. Coarse centers must remove offsets that swamp it;
            // residual carrier is fitted only after flag acquisition.
            let levels: Vec<_> = observed.iter().map(|x| u8::from(*x >= 0.)).collect();
            let logical = decoded(&levels, c);
            let minimum = usize::from(c.line_coding == LineCoding::Nrzi)
                + if c.scrambler == Scrambler::G3ruh {
                    17
                } else {
                    0
                };
            for (_, flag_end) in flag_runs(&logical, minimum, config.preamble_flags) {
                let begin = flag_end - pilot_count;
                let n = config.maximum_candidate_symbols.min(symbols - begin);
                if n < pilot_count + 18 * 8 + 8 {
                    continue;
                }
                let Some(pilot) = physical_pilot(&levels, begin, pilot_count, c) else {
                    continue;
                };
                if outcome.receipts.len() >= config.maximum_candidates {
                    return Err(
                        "coherent HDLC candidate budget exceeded; no candidates pruned".into(),
                    );
                }
                let branches = (n + 2) as u64 * states(config) as u64;
                charge(
                    work,
                    branches * (2 * sps as u64 + 128) + 524288,
                    c.work_budget,
                )?;
                let start = offset + begin * sps;
                let end = start + n * sps;
                let stream = first_stream + outcome.receipts.len();
                let mut receipt = Receipt {
                    stream,
                    start_sample: start as u64,
                    end_sample: end as u64,
                    carrier_center_hz: carrier,
                    phase_offset_samples: offset,
                    pilot_symbols: pilot_count,
                    analysis_symbols: n,
                    line_state_source: if c.scrambler == Scrambler::G3ruh {
                        "received_17_bit_history_and_nrzi_predecessor_conditional_hypothesis"
                    } else {
                        "protocol_flags_and_received_nrzi_predecessor"
                    }
                    .into(),
                    diagnostics: None,
                    reason: String::new(),
                };
                let mut known = vec![None; n];
                for (dst, bit) in known.iter_mut().zip(pilot) {
                    *dst = Some(bit);
                }
                let power = mixed[start..start + pilot_count * sps]
                    .iter()
                    .map(|x| x.norm_sqr())
                    .sum::<f64>()
                    / (pilot_count * sps) as f64;
                let kernel = coherent_cpm::CoherentCpmConfig {
                    samples_per_symbol: sps,
                    modulation_index_numerator: config.modulation_index_numerator,
                    modulation_index_denominator: config.modulation_index_denominator,
                    gaussian_bt: config.gaussian_bt,
                    noise_variance: 0.1,
                    llr_clip: 100.,
                    max_carrier_offset_radians_per_sample: TAU * config.max_residual_carrier_hz
                        / rate as f64,
                    min_training_coherence: config.minimum_training_coherence,
                    boundary: CpmBoundary::UnknownOutsideWindow,
                };
                let detection = coherent_cpm::prepare_with_training_noise(
                    &mixed[start..end],
                    &known,
                    &kernel,
                    (power * 1e-3).clamp(1e-10, 1e10),
                );
                match detection {
                    Ok(prepared) => {
                        receipt.diagnostics = Some(
                            serde_json::to_value(prepared.diagnostics())
                                .map_err(|e| e.to_string())?,
                        );
                        let detected = prepared.detect(&[])?;
                        for span in super::spans(&detected.posterior_llr, 0., c)
                            .into_iter()
                            .filter(|s| s.start >= pilot_count)
                        {
                            let provenance = Provenance {
                                stream,
                                lane: "coherent_cpm:repeated_hdlc_flags".into(),
                                start_symbol: span.start,
                                end_symbol: span.end,
                                start_sample: start as u64,
                                end_sample: end as u64,
                            };
                            outcome.frames.push((span, provenance));
                        }
                        receipt.reason = "complete_cpm_received_fcs_admission".into();
                    }
                    Err(error) => {
                        if error.contains("work") || error.contains("budget") {
                            return Err(error);
                        }
                        receipt.reason = error;
                    }
                }
                outcome.receipts.push(receipt);
            }
        }
    }
    Ok(outcome)
}
