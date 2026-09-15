//! Truth-isolated physical-layer decisions for the RML24 profiles.
//!
//! This module emits unaligned bits, not validated telemetry. Reference bits
//! exist only in the separately named BER scoring functions. The legacy
//! weighted BPSK slope uses an equivalent centered weighted fit rather than
//! NumPy's SVD; RustFFT/libm also permit small floating-point differences.

use num_complex::Complex64;
use rustfft::FftPlanner;
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use std::{cmp::Reverse, f64::consts::PI};

const MAX_RECORD_SAMPLES: usize = 4_194_304;

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(default, deny_unknown_fields)]
pub struct PhysicalConfig {
    pub sample_rate_hz: f64,
    pub symbol_rate_hz: f64,
    pub modulation: String,
    pub recovery_version: String,
    pub max_carrier_offset_hz: f64,
    /// None retains both historical OQPSK stagger hypotheses in q, i order.
    pub delayed_branch: Option<String>,
}

impl Default for PhysicalConfig {
    fn default() -> Self {
        Self {
            sample_rate_hz: 1_000_000.0,
            // Nominal symbol rate/modulation are required metadata, not inferred.
            symbol_rate_hz: 0.0,
            modulation: String::new(),
            recovery_version: "legacy".into(),
            max_carrier_offset_hz: 2_000.0,
            delayed_branch: None,
        }
    }
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct UnalignedBits {
    pub bit_variants: Vec<Vec<u8>>,
    pub variant_names: Vec<String>,
    pub diagnostics: Value,
}

impl PhysicalConfig {
    fn validate(&self) -> Result<(String, f64, bool), String> {
        if !self.sample_rate_hz.is_finite()
            || !self.symbol_rate_hz.is_finite()
            || self.sample_rate_hz <= 0.0
            || self.symbol_rate_hz <= 0.0
        {
            return Err("physical sample and symbol rates must be finite and positive".into());
        }
        let modulation = self.modulation.to_uppercase();
        if !matches!(
            modulation.as_str(),
            "BPSK" | "QPSK" | "OQPSK" | "GMSK" | "2FSK" | "FSK"
        ) {
            return Err(format!("unsupported physical modulation {modulation}"));
        }
        let version2 = match self.recovery_version.as_str() {
            "legacy" => false,
            "carrier_timing_v2" => true,
            _ => return Err("recovery version must be legacy or carrier_timing_v2".into()),
        };
        if let Some(branch) = &self.delayed_branch
            && (modulation != "OQPSK" || !matches!(branch.as_str(), "q" | "i"))
        {
            return Err("delayed branch is only q or i and only for OQPSK".into());
        }
        let sps = self.sample_rate_hz / self.symbol_rate_hz;
        if !sps.is_finite() || sps < if modulation == "OQPSK" { 2.0 } else { 1.5 } {
            return Err("physical timing requires >=1.5 sps, or >=2 for OQPSK".into());
        }
        Ok((modulation, sps, version2))
    }
}

fn sum(values: &[f64]) -> f64 {
    if values.len() < 8 {
        return values.iter().fold(-0.0, |a, b| a + b);
    }
    if values.len() > 128 {
        let middle = (values.len() / 2) & !7;
        return sum(&values[..middle]) + sum(&values[middle..]);
    }
    let mut accumulators = [0.0; 8];
    accumulators.copy_from_slice(&values[..8]);
    let end = values.len() & !7;
    for chunk in values[8..end].as_chunks::<8>().0 {
        for i in 0..8 {
            accumulators[i] += chunk[i];
        }
    }
    let mut result = ((accumulators[0] + accumulators[1]) + (accumulators[2] + accumulators[3]))
        + ((accumulators[4] + accumulators[5]) + (accumulators[6] + accumulators[7]));
    for value in &values[end..] {
        result += value;
    }
    result
}
fn mean(values: &[f64]) -> f64 {
    sum(values) / values.len() as f64
}
fn complex_mean(values: &[Complex64]) -> Complex64 {
    Complex64::new(
        mean(&values.iter().map(|x| x.re).collect::<Vec<_>>()),
        mean(&values.iter().map(|x| x.im).collect::<Vec<_>>()),
    )
}
fn complex_power(value: Complex64, order: usize) -> Complex64 {
    let square = value * value;
    if order == 2 { square } else { square * square }
}
pub(crate) fn normalize_record(iq: &[Complex64]) -> Result<Vec<Complex64>, String> {
    if iq.len() < 8
        || iq.len() > MAX_RECORD_SAMPLES
        || iq.iter().any(|x| !x.re.is_finite() || !x.im.is_finite())
    {
        return Err("physical IQ must contain 8..4194304 finite complex samples".into());
    }
    let center = complex_mean(iq);
    let mut values: Vec<_> = iq.iter().map(|x| x - center).collect();
    let power = mean(
        &values
            .iter()
            .map(|x| {
                let a = x.re.hypot(x.im);
                a * a
            })
            .collect::<Vec<_>>(),
    );
    if !power.is_finite() || power <= 1e-15 {
        return Err("IQ record has no finite signal power".into());
    }
    let scale = power.sqrt();
    for x in &mut values {
        *x /= scale;
    }
    Ok(values)
}
fn round_even(value: f64) -> usize {
    value.round_ties_even().max(1.0) as usize
}

/// NumPy mode="same" convolution including its zero-padded boundaries.
fn boxcar_same(values: &[Complex64], width: usize) -> Vec<Complex64> {
    if width <= 1 {
        return values.to_vec();
    }
    let length = values.len().max(width);
    let start = (values.len().min(width) - 1) / 2;
    let tap = 1.0 / width as f64;
    (0..length)
        .map(|i| {
            let full = i + start;
            let left = full.saturating_sub(width - 1);
            let right = full.min(values.len() - 1);
            let mut value = Complex64::new(0.0, 0.0);
            // Direct causal dot product, no cumulative-sum drift.
            if left <= right {
                for index in (left..=right).rev() {
                    value += values[index] * tap;
                }
            }
            value
        })
        .collect()
}
fn interpolate(values: &[Complex64], position: f64) -> Complex64 {
    if position <= 0.0 {
        return values[0];
    }
    let index = position as usize;
    if index >= values.len() - 1 {
        return values[values.len() - 1];
    }
    (values[index + 1] - values[index]) * (position - index as f64) + values[index]
}
fn eye_score(values: &[Complex64], qpsk: bool) -> f64 {
    let square: Vec<_> = values
        .iter()
        .map(|x| {
            if qpsk {
                let a = x.re.hypot(x.im);
                a * a
            } else {
                x.re * x.re
            }
        })
        .collect();
    let opening: Vec<_> = values
        .iter()
        .map(|x| {
            if qpsk {
                x.re.abs().min(x.im.abs())
            } else {
                x.re.abs()
            }
        })
        .collect();
    mean(&opening) / (mean(&square).sqrt() + 1e-12)
}
fn timing_slice(
    values: &[Complex64],
    sps: f64,
    qpsk: bool,
    delayed: Option<&str>,
) -> Result<(Vec<Complex64>, f64, f64), String> {
    let phase_count = (sps * 4.0).ceil().max(8.0) as usize;
    // Every successful timing grid has >=4 symbols; reject impossible grids
    // before allocating/searching rather than quietly capping hypotheses.
    let half = if delayed.is_some() { 0.5 * sps } else { 0.0 };
    if values.len() as f64 - 1.0 - half < 3.0 * sps {
        return Err("timing search yielded no symbols".into());
    }
    let phase_step = sps / phase_count as f64;
    let mut best = None;
    let mut best_score = f64::NEG_INFINITY;
    let mut best_phase = 0.0;
    for index in 0..phase_count {
        let phase = index as f64 * phase_step;
        let count = ((values.len() as f64 - 1.0 - phase - half) / sps).floor() as usize + 1;
        if count < 4 {
            continue;
        }
        let symbols: Vec<_> = (0..count)
            .map(|symbol| {
                let early = phase + symbol as f64 * sps;
                match delayed {
                    Some("q") => Complex64::new(
                        interpolate(values, early).re,
                        interpolate(values, early + half).im,
                    ),
                    Some("i") => Complex64::new(
                        interpolate(values, early + half).re,
                        interpolate(values, early).im,
                    ),
                    _ => interpolate(values, early),
                }
            })
            .collect();
        let score = eye_score(&symbols, qpsk);
        if score > best_score {
            best = Some(symbols);
            best_score = score;
            best_phase = phase;
        }
    }
    Ok((
        best.ok_or("timing search yielded no symbols")?,
        best_phase,
        best_score,
    ))
}

pub(crate) fn carrier_fft(
    signal: &[Complex64],
    rate: f64,
    order: usize,
    bound: Option<f64>,
) -> Result<(Vec<Complex64>, f64, f64), String> {
    if let Some(maximum) = bound
        && (!maximum.is_finite() || maximum <= 0.0 || order as f64 * maximum >= rate / 2.0)
    {
        return Err("carrier search bound is outside the sampled spectrum".into());
    }
    let target = if bound.is_some() {
        65_536.max(signal.len() * 32)
    } else {
        4096.max(signal.len() * 16)
    };
    let fft_size = target.next_power_of_two().min(262_144);
    let mut spectrum = vec![Complex64::new(0.0, 0.0); fft_size];
    for (i, (&value, destination)) in signal.iter().zip(&mut spectrum).enumerate() {
        let n = 1.0 - signal.len() as f64 + 2.0 * i as f64;
        let hann = 0.5 + 0.5 * (PI * n / (signal.len() - 1) as f64).cos();
        *destination = complex_power(value, order) * hann;
    }
    FftPlanner::<f64>::new()
        .plan_fft_forward(fft_size)
        .process(&mut spectrum);
    let frequency_scale = 1.0 / (fft_size as f64 * (1.0 / rate));
    let mut best = -1.0;
    let mut peak_frequency = 0.0;
    for (i, value) in spectrum.iter().enumerate() {
        let bin = if i <= (fft_size - 1) / 2 {
            i as i64
        } else {
            i as i64 - fft_size as i64
        };
        let frequency = bin as f64 * frequency_scale;
        if bound.is_some_and(|maximum| frequency.abs() > order as f64 * maximum) {
            continue;
        }
        let amplitude = value.re.hypot(value.im);
        if amplitude > best {
            best = amplitude;
            peak_frequency = frequency;
        }
    }
    let carrier = peak_frequency / order as f64;
    let mut corrected: Vec<_> = signal
        .iter()
        .enumerate()
        .map(|(i, x)| {
            let angle = if bound.is_some() {
                (-2.0 * PI * carrier) * i as f64 / rate
            } else {
                -2.0 * PI * (peak_frequency / (4.0 * rate)) * i as f64
            };
            x * Complex64::new(angle.cos(), angle.sin())
        })
        .collect();
    let powered: Vec<_> = corrected.iter().map(|&x| complex_power(x, order)).collect();
    let reference = complex_mean(&powered) * if order == 4 { -1.0 } else { 1.0 };
    let phase = reference.im.atan2(reference.re) / order as f64;
    let rotation = Complex64::new((-phase).cos(), (-phase).sin());
    for x in &mut corrected {
        *x *= rotation;
    }
    Ok((corrected, carrier, phase))
}

fn bpsk_legacy(signal: &[Complex64], rate: f64) -> Result<(Vec<Complex64>, f64, f64), String> {
    let squared: Vec<_> = signal.iter().map(|&x| x * x).collect();
    let mut phases = Vec::with_capacity(signal.len());
    let mut previous = 0.0;
    let mut correction = 0.0;
    for (i, value) in squared.iter().enumerate() {
        let phase = value.im.atan2(value.re);
        if i > 0 {
            let delta = phase - previous;
            let mut reduced = (delta + PI).rem_euclid(2.0 * PI) - PI;
            if reduced == -PI && delta > 0.0 {
                reduced = PI;
            }
            if delta.abs() >= PI {
                correction += reduced - delta;
            }
        }
        phases.push(phase + correction);
        previous = phase;
    }
    // np.polyfit w=weights minimizes sum((weights*residual)^2), so the
    // statistical weights here are squared, not the raw magnitude weights.
    let weights: Vec<_> = squared
        .iter()
        .map(|x| x.re.hypot(x.im).max(1e-3).powi(2))
        .collect();
    let total = sum(&weights);
    let xbar = sum(&weights
        .iter()
        .enumerate()
        .map(|(i, w)| i as f64 * w)
        .collect::<Vec<_>>())
        / total;
    let ybar = sum(&weights
        .iter()
        .zip(&phases)
        .map(|(w, y)| w * y)
        .collect::<Vec<_>>())
        / total;
    let numerator = sum(&weights
        .iter()
        .zip(&phases)
        .enumerate()
        .map(|(i, (w, y))| w * (i as f64 - xbar) * (y - ybar))
        .collect::<Vec<_>>());
    let denominator = sum(&weights
        .iter()
        .enumerate()
        .map(|(i, w)| w * (i as f64 - xbar).powi(2))
        .collect::<Vec<_>>());
    let slope = numerator / denominator;
    if !slope.is_finite() {
        return Err("weighted BPSK carrier slope is degenerate".into());
    }
    let cfo_cycles = slope / (4.0 * PI);
    let mut corrected: Vec<_> = signal
        .iter()
        .enumerate()
        .map(|(i, x)| {
            let angle = (-2.0 * PI * cfo_cycles) * i as f64;
            x * Complex64::new(angle.cos(), angle.sin())
        })
        .collect();
    let mean_square = complex_mean(&corrected.iter().map(|&x| x * x).collect::<Vec<_>>());
    let carrier_phase = 0.5 * mean_square.im.atan2(mean_square.re);
    let rotation = Complex64::new((-carrier_phase).cos(), (-carrier_phase).sin());
    for x in &mut corrected {
        *x *= rotation;
    }
    Ok((corrected, cfo_cycles * rate, carrier_phase))
}

fn percentile(values: &[f64], fraction: f64) -> f64 {
    let mut sorted = values.to_vec();
    sorted.sort_by(|a, b| a.total_cmp(b));
    let virtual_index = (sorted.len() - 1) as f64 * fraction;
    let index = virtual_index.floor() as usize;
    let weight = virtual_index - index as f64;
    let a = sorted[index];
    let b = sorted[(index + 1).min(sorted.len() - 1)];
    if weight >= 0.5 {
        b - (b - a) * (1.0 - weight)
    } else {
        a + (b - a) * weight
    }
}
fn fsk_signal(signal: &[Complex64], rate: f64) -> (Vec<Complex64>, f64, f64) {
    let discriminator: Vec<_> = signal
        .windows(2)
        .map(|x| {
            let p = x[1] * x[0].conj();
            p.im.atan2(p.re)
        })
        .collect();
    let mut lower = percentile(&discriminator, 0.25);
    let mut upper = percentile(&discriminator, 0.75);
    for _ in 0..12 {
        let midpoint = 0.5 * (lower + upper);
        let low: Vec<_> = discriminator
            .iter()
            .copied()
            .filter(|x| *x <= midpoint)
            .collect();
        let high: Vec<_> = discriminator
            .iter()
            .copied()
            .filter(|x| *x > midpoint)
            .collect();
        if low.is_empty() || high.is_empty() {
            break;
        }
        let new_lower = mean(&low);
        let new_upper = mean(&high);
        let converged = (new_lower - lower).abs() + (new_upper - upper).abs() < 1e-10;
        lower = new_lower;
        upper = new_upper;
        if converged {
            break;
        }
    }
    let midpoint = 0.5 * (lower + upper);
    (
        discriminator
            .iter()
            .map(|x| Complex64::new(x - midpoint, 0.0))
            .collect(),
        midpoint * rate / (2.0 * PI),
        (upper - lower).abs() * rate / (2.0 * PI),
    )
}
fn quadrant_bits(symbols: &[Complex64]) -> Vec<u8> {
    symbols
        .iter()
        .flat_map(|x| [u8::from(x.re < 0.0), u8::from(x.im < 0.0)])
        .collect()
}

/// Waveform decisions without any reference-bit or protocol access.
pub fn demodulate_unaligned(
    iq: &[Complex64],
    config: &PhysicalConfig,
) -> Result<UnalignedBits, String> {
    let (modulation, sps, version2) = config.validate()?;
    if (iq.len() as f64) < 3.0 * sps + 1.0 {
        return Err("record too short for requested symbol rate".into());
    }
    let signal = normalize_record(iq)?;
    if modulation == "BPSK" || matches!(modulation.as_str(), "FSK" | "2FSK" | "GMSK") {
        let (mut soft, cfo, other) = if modulation == "BPSK" {
            if version2 {
                carrier_fft(
                    &signal,
                    config.sample_rate_hz,
                    2,
                    Some(config.max_carrier_offset_hz),
                )?
            } else {
                bpsk_legacy(&signal, config.sample_rate_hz)?
            }
        } else {
            fsk_signal(&signal, config.sample_rate_hz)
        };
        // BPSK projected real branch; no information is taken from Q.
        for x in &mut soft {
            x.im = 0.0;
        }
        let width = round_even(sps * if modulation == "BPSK" { 0.65 } else { 0.8 });
        let filtered = boxcar_same(&soft, width);
        let (symbols, phase, score) = timing_slice(&filtered, sps, false, None)?;
        let mut diagnostics =
            json!({"cfo_hz":cfo,"timing_phase_samples":phase,"timing_score":score});
        diagnostics[if modulation == "BPSK" {
            "carrier_phase_rad_mod_pi"
        } else {
            "tone_separation_hz"
        }] = json!(other);
        return Ok(UnalignedBits {
            bit_variants: vec![symbols.iter().map(|x| u8::from(x.re < 0.0)).collect()],
            variant_names: vec!["binary_timing".into()],
            diagnostics,
        });
    }
    let offset = modulation == "OQPSK";
    let (corrected, cfo, phase) = carrier_fft(
        &signal,
        config.sample_rate_hz,
        4,
        if version2 {
            Some(config.max_carrier_offset_hz)
        } else {
            None
        },
    )?;
    let filtered = boxcar_same(
        &corrected,
        round_even(sps * if offset { 0.3 } else { 0.65 }),
    );
    let mut variants = Vec::new();
    let mut names = Vec::new();

    let timing = if !offset {
        let (symbols, timing_phase, score) = timing_slice(&filtered, sps, true, None)?;
        variants.push(quadrant_bits(&symbols));
        names.push("common_iq_timing".into());
        json!({"selected_phase_samples":timing_phase,"eye_score":score})
    } else {
        let mut hypotheses = serde_json::Map::new();
        for branch in ["q", "i"] {
            if config
                .delayed_branch
                .as_deref()
                .is_some_and(|selected| selected != branch)
            {
                continue;
            }
            let (symbols, timing_phase, score) = timing_slice(&filtered, sps, true, Some(branch))?;
            for pairing in if version2 { -1_i32..=1 } else { 0_i32..=0 } {
                let paired: Vec<_> = match pairing {
                    -1 => symbols[1..]
                        .iter()
                        .zip(&symbols[..symbols.len() - 1])
                        .map(|(i, q)| Complex64::new(i.re, q.im))
                        .collect(),
                    1 => symbols[..symbols.len() - 1]
                        .iter()
                        .zip(&symbols[1..])
                        .map(|(i, q)| Complex64::new(i.re, q.im))
                        .collect(),
                    _ => symbols.clone(),
                };
                let name = if version2 {
                    format!("{branch}_delayed_half_symbol_pair_offset_{pairing:+}")
                } else {
                    format!("{branch}_delayed_half_symbol")
                };
                variants.push(quadrant_bits(&paired));
                names.push(name.clone());
                hypotheses.insert(
                    name,
                    json!({"selected_phase_samples":timing_phase,"eye_score":score}),
                );
            }
        }
        Value::Object(hypotheses)
    };
    Ok(UnalignedBits {
        bit_variants: variants,
        variant_names: names,
        diagnostics: json!({
        "cfo_hz":cfo,"carrier_phase_rad_mod_pi_over_2":phase,"timing":timing}),
    })
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(default, deny_unknown_fields)]
pub struct AlignmentPolicy {
    pub max_shift_bits: usize,
    pub allow_global_polarity_inversion: bool,
    pub allow_iq_reflection: bool,
    pub missing_truth_bits_count_as_errors: bool,
}
impl Default for AlignmentPolicy {
    fn default() -> Self {
        Self {
            max_shift_bits: 8,
            allow_global_polarity_inversion: true,
            allow_iq_reflection: true,
            missing_truth_bits_count_as_errors: true,
        }
    }
}
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct AlignmentResult {
    pub aligned: Vec<u8>,
    pub diagnostics: Value,
}

fn alignment_segment(
    predicted: &[u8],
    truth: &[u8],
    shift: i64,
    polarity: u8,
    policy: &AlignmentPolicy,
) -> Option<(usize, usize, Vec<u8>)> {
    let truth_start = (-shift).max(0) as usize;
    let predicted_start = shift.max(0) as usize;
    let overlap = truth
        .len()
        .saturating_sub(truth_start)
        .min(predicted.len().saturating_sub(predicted_start));
    if overlap == 0 {
        return None;
    }
    let segment: Vec<_> = predicted[predicted_start..predicted_start + overlap]
        .iter()
        .map(|x| (x & 1) ^ polarity)
        .collect();
    let mismatches = segment
        .iter()
        .zip(&truth[truth_start..truth_start + overlap])
        .filter(|(a, b)| **a != (**b & 1))
        .count();
    let errors = mismatches
        + if policy.missing_truth_bits_count_as_errors {
            truth.len() - overlap
        } else {
            0
        };
    let mut aligned: Vec<_> = truth
        .iter()
        .map(|x| {
            if policy.missing_truth_bits_count_as_errors {
                1 - (x & 1)
            } else {
                0
            }
        })
        .collect();
    aligned[truth_start..truth_start + overlap].copy_from_slice(&segment);
    Some((errors, overlap, aligned))
}

/// Explicit truth-based BER scoring, never a demodulator or telemetry validator.
pub fn align_for_ber(
    candidate: &[u8],
    truth: &[u8],
    policy: &AlignmentPolicy,
) -> Result<AlignmentResult, String> {
    if truth.is_empty() {
        return Err("truth sequence is empty".into());
    }
    let limit = policy.max_shift_bits.min(truth.len().max(candidate.len())) as i64;
    let mut winner = None;
    let mut best_rank = None;
    for shift in -limit..=limit {
        for polarity in 0..=u8::from(policy.allow_global_polarity_inversion) {
            let Some((errors, overlap, aligned)) =
                alignment_segment(candidate, truth, shift, polarity, policy)
            else {
                continue;
            };
            let rank = (errors, Reverse(overlap), shift.abs(), polarity);
            if best_rank.is_none_or(|best| rank < best) {
                best_rank = Some(rank);
                winner = Some(AlignmentResult {
                    aligned,
                    diagnostics: json!({"shift_bits":shift,
                    "polarity_inverted":polarity!=0,"overlap_bits":overlap,"truth_bits":truth.len(),
                    "errors_including_missing":errors}),
                });
            }
        }
    }
    winner.ok_or("no permitted alignment has any overlap".into())
}

fn symmetry(bits: &[u8], turns: usize, reflected: bool) -> Vec<u8> {
    bits.as_chunks::<2>()
        .0
        .iter()
        .flat_map(|pair| {
            let mut i = pair[0] & 1;
            let mut q = (pair[1] & 1) ^ u8::from(reflected);
            for _ in 0..turns {
                let new_i = q ^ 1;
                q = i;
                i = new_i;
            }
            [i, q]
        })
        .collect()
}

/// Explicit final OQPSK variant, square-constellation and symbol-shift scoring.
/// Like the Python contract, the global-polarity flag does not prune QPSK D4
/// rotations; the separate reflection flag controls only reflection choices.
pub fn align_qpsk_for_ber(
    candidate: &UnalignedBits,
    truth: &[u8],
    policy: &AlignmentPolicy,
) -> Result<AlignmentResult, String> {
    if truth.is_empty() {
        return Err("truth sequence is empty".into());
    }
    if candidate.bit_variants.is_empty()
        || candidate.bit_variants.len() != candidate.variant_names.len()
    {
        return Err("QPSK decision variants and names must be nonempty and paired".into());
    }
    let longest = candidate
        .bit_variants
        .iter()
        .map(Vec::len)
        .max()
        .unwrap_or(0);
    let limit = (policy.max_shift_bits / 2).min(truth.len().max(longest) / 2 + 1) as i64;
    let mut winner = None;
    let mut best_rank = None;
    for (variant_index, raw) in candidate.bit_variants.iter().enumerate() {
        for reflected in 0..=u8::from(policy.allow_iq_reflection) {
            for turns in 0..4 {
                let predicted = symmetry(raw, turns, reflected != 0);
                for symbol_shift in -limit..=limit {
                    let Some((errors, overlap, aligned)) =
                        alignment_segment(&predicted, truth, 2 * symbol_shift, 0, policy)
                    else {
                        continue;
                    };
                    let rank = (
                        errors,
                        Reverse(overlap),
                        symbol_shift.abs(),
                        reflected,
                        turns,
                        variant_index,
                    );
                    if best_rank.is_none_or(|best| rank < best) {
                        best_rank = Some(rank);
                        winner = Some(AlignmentResult {
                            aligned,
                            diagnostics: json!({
                            "shift_bits":2*symbol_shift,"shift_symbols":symbol_shift,"constellation_rotation_quarter_turns":turns,
                            "constellation_reflected":reflected!=0,"timing_hypothesis":candidate.variant_names[variant_index],
                            "overlap_bits":overlap,"truth_bits":truth.len(),"errors_including_missing":errors}),
                        });
                    }
                }
            }
        }
    }
    winner.ok_or("no permitted QPSK alignment has any overlap".into())
}

#[cfg(test)]
#[path = "tests/physical_tests.rs"]
mod tests;
