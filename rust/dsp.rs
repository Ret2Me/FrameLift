//! Deterministic real-audio conditioning and reference-free clock search.
//!
//! This ports the PCM FSK/Bell-202 and clock-bank contracts, including the
//! original top-N prefix, rather than pruning any hypotheses. All arithmetic
//! uses f64 without fast-math or explicit fused multiply-add. Transcendental
//! functions and FIR reduction order can differ from NumPy/SciPy by a few ulps;
//! numerical closeness is not a universal claim of identical decoded frames.

use num_complex::{Complex32, Complex64};
use rustfft::FftPlanner;
use serde::{Deserialize, Serialize};
use std::cmp::Ordering;
use std::f64::consts::PI;

pub const MAX_PCM_WINDOW_SAMPLES: usize = 4_194_304;
pub const GUARD_SYMBOLS: usize = 32;

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(default, deny_unknown_fields)]
pub struct DspConfig {
    pub baud: f64,
    /// "fsk" is already FM-demodulated PCM, not complex RF IQ.
    pub mode: String,
    pub rate_errors_ppm: Vec<f64>,
    pub phase_bins: usize,
    pub top_timing: usize,
    /// "global", "diverse" (add two separated representatives per rate), or
    /// "full" (every ranked clock, no top-N truncation), or experimental
    /// "burst" (the unchanged diverse prefix plus overlapping local clocks).
    pub bank: String,
}

impl Default for DspConfig {
    fn default() -> Self {
        Self {
            baud: 9_600.0,
            mode: "fsk".into(),
            rate_errors_ppm: vec![-500.0, -100.0, 0.0, 100.0, 500.0],
            phase_bins: 32,
            top_timing: 16,
            bank: "diverse".into(),
        }
    }
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
pub struct TimingHypothesis {
    pub rate_error_ppm: f64,
    pub phase_samples: f64,
    pub step_samples: f64,
    pub threshold: f64,
    pub score: f64,
    pub symbol_count: usize,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Frontend {
    pub samples: Vec<f64>,
    pub samples_per_symbol: f64,
}

impl DspConfig {
    pub fn decimation(&self) -> Result<usize, String> {
        match self.mode.as_str() {
            "fsk" => Ok(2),
            "afsk" => Ok(5),
            _ => Err("PCM mode must be fsk or afsk".into()),
        }
    }

    pub fn validate(&self, sample_rate: u32) -> Result<(), String> {
        if sample_rate == 0 || !self.baud.is_finite() || self.baud <= 0.0 {
            return Err("sample rate and baud must be positive and finite".into());
        }
        let decimation = self.decimation()?;
        let output_rate = sample_rate as f64 / decimation as f64;
        let sps = output_rate / self.baud;
        if sps <= 1.1 {
            return Err("waveform must retain more than 1.1 samples per symbol".into());
        }
        validate_search(self, sps)?;
        if self.mode == "fsk" && 0.75 * self.baud >= output_rate / 2.0 {
            return Err("post-discriminator cutoff must be below output Nyquist".into());
        }
        if self.mode == "afsk"
            && (!sample_rate.is_multiple_of(decimation as u32) || output_rate <= 6_000.0)
        {
            return Err("Bell-202 decimation requires integer rate above 6000 Hz".into());
        }
        Ok(())
    }
}

fn validate_search(config: &DspConfig, sps: f64) -> Result<(), String> {
    if !sps.is_finite() || sps <= 1.0 {
        return Err("nominal samples per symbol must be finite and greater than one".into());
    }
    if config.phase_bins < 4 || config.top_timing == 0 || config.rate_errors_ppm.is_empty() {
        return Err("invalid timing-search budget".into());
    }
    if !matches!(
        config.bank.as_str(),
        "global" | "diverse" | "full" | "burst"
    ) {
        return Err("timing bank must be global, diverse, full or burst".into());
    }
    for rate in &config.rate_errors_ppm {
        let step = sps * (1.0 + rate * 1e-6);
        if !rate.is_finite() || !step.is_finite() || step <= 1.0 {
            return Err("rate error produces an invalid symbol step".into());
        }
    }
    config
        .phase_bins
        .checked_mul(config.rate_errors_ppm.len())
        .ok_or("timing-search budget overflows address space")?;
    Ok(())
}

/// The positive-frequency representation must already be resolved by callers.
/// A constant PCM window is a normal empty result, matching the Python entry.
pub fn frontend(pcm: &[f64], sample_rate: u32, config: &DspConfig) -> Result<Frontend, String> {
    config.validate(sample_rate)?;
    frontend_configured(
        pcm,
        sample_rate,
        config,
        &PcmFrontendConfig {
            decimation: config.decimation()?,
            cutoff_hz: None,
            mark_hz: 1200.0,
            space_hz: 2200.0,
        },
    )
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PcmFrontendConfig {
    pub decimation: usize,
    pub cutoff_hz: Option<f64>,
    pub mark_hz: f64,
    pub space_hz: f64,
}

/// Explicit generic PCM parameters; the convenience frontend keeps all old defaults.
pub fn frontend_configured(
    pcm: &[f64],
    sample_rate: u32,
    config: &DspConfig,
    parameters: &PcmFrontendConfig,
) -> Result<Frontend, String> {
    if sample_rate == 0
        || !config.baud.is_finite()
        || config.baud <= 0.0
        || parameters.decimation == 0
    {
        return Err("positive finite rate/baud and nonzero decimation required".into());
    }
    let output_rate = sample_rate as f64 / parameters.decimation as f64;
    let samples_per_symbol = output_rate / config.baud;
    validate_search(config, samples_per_symbol)?;
    if samples_per_symbol <= 1.1 {
        return Err("waveform requires >1.1 output samples per symbol".into());
    }
    let cutoff = parameters.cutoff_hz.unwrap_or(0.75 * config.baud);
    match config.mode.as_str() {
        "fsk" => {
            if !cutoff.is_finite()
                || cutoff <= 0.0
                || cutoff >= output_rate.min(sample_rate as f64) / 2.0
            {
                return Err(
                    "post discriminator cutoff must be finite, positive and below Nyquist".into(),
                );
            }
        }
        "afsk" => {
            if parameters.cutoff_hz.is_some()
                || !(sample_rate as usize).is_multiple_of(parameters.decimation)
                || output_rate <= 6000.0
                || !parameters.mark_hz.is_finite()
                || !parameters.space_hz.is_finite()
                || parameters.mark_hz == parameters.space_hz
                || parameters.mark_hz <= 500.0
                || parameters.mark_hz >= 3000.0_f64.min(output_rate / 2.0)
                || parameters.space_hz <= 500.0
                || parameters.space_hz >= 3000.0_f64.min(output_rate / 2.0)
            {
                return Err("invalid configured Bell202 passband/tones/decimation".into());
            }
        }
        _ => return Err("PCM mode must be fsk or afsk".into()),
    }
    if !(8_192..=MAX_PCM_WINDOW_SAMPLES).contains(&pcm.len()) {
        return Err(format!(
            "PCM window must contain 8192..{MAX_PCM_WINDOW_SAMPLES} samples"
        ));
    }
    if pcm.iter().any(|x| !x.is_finite()) {
        return Err("PCM samples must be finite".into());
    }
    let decimation = parameters.decimation;
    let output_rate = sample_rate as f64 / decimation as f64;
    let samples_per_symbol = output_rate / config.baud;
    let minimum = pcm.iter().copied().fold(f64::INFINITY, f64::min);
    let maximum = pcm.iter().copied().fold(f64::NEG_INFINITY, f64::max);
    if maximum - minimum <= 1e-12 {
        return Ok(Frontend {
            samples: vec![],
            samples_per_symbol,
        });
    }
    let samples = if config.mode == "fsk" {
        let taps = firwin(115, 0.0, cutoff, sample_rate as f64);
        let filtered = fir_decimated(pcm, &taps, decimation)?;
        let blocker_length = round_ties_even(512.0 * samples_per_symbol).max(32.0) as usize;
        if filtered.len() <= blocker_length {
            return Err("PCM window is too short for the adaptive DC blocker".into());
        }
        let mut cumulative = Vec::with_capacity(filtered.len() + 1);
        cumulative.push(0.0);
        let mut sum = 0.0;
        for &x in &filtered {
            sum += x;
            cumulative.push(sum);
        }
        let mut soft = Vec::with_capacity(filtered.len() + 1 - blocker_length);
        for index in blocker_length - 1..filtered.len() {
            let trend = (cumulative[index + 1] - cumulative[index + 1 - blocker_length])
                / blocker_length as f64;
            soft.push(filtered[index] - trend);
        }
        normalize(soft)?
    } else {
        let taps = firwin(129, 500.0, 3_000.0, sample_rate as f64);
        let audio = fir_decimated(pcm, &taps, decimation)?;
        if audio.len() < 2_048 {
            return Err("not enough Bell-202 audio after filtering".into());
        }
        let audio = normalize(audio)?;
        if audio.is_empty() {
            return Ok(Frontend {
                samples: vec![],
                samples_per_symbol,
            });
        }
        let length = round_ties_even(samples_per_symbol).max(4.0) as usize;
        let mark = moving_tone_energy(&audio, parameters.mark_hz, output_rate, length);
        let space = moving_tone_energy(&audio, parameters.space_hz, output_rate, length);
        if 2 * length >= audio.len() {
            return Err("not enough Bell-202 symbols after tone correlation".into());
        }
        normalize(
            mark.iter()
                .zip(&space)
                .skip(2 * length)
                .map(|(m, s)| m - s)
                .collect(),
        )?
    };
    Ok(Frontend {
        samples,
        samples_per_symbol,
    })
}

fn round_ties_even(value: f64) -> f64 {
    let lower = value.floor();
    if value - lower == 0.5 {
        if lower % 2.0 == 0.0 {
            lower
        } else {
            lower + 1.0
        }
    } else {
        value.round()
    }
}

/// Form analytic *audio* and translate an explicitly supplied positive audio
/// carrier to zero. This does not recover original RF IQ or lost bandwidth.
pub fn analytic_audio(
    pcm: &[f64],
    sample_rate: u32,
    carrier_hz: f64,
) -> Result<Vec<Complex64>, String> {
    if !(8_192..=MAX_PCM_WINDOW_SAMPLES).contains(&pcm.len()) || pcm.iter().any(|x| !x.is_finite())
    {
        return Err("analytic audio requires a bounded finite PCM window".into());
    }
    if sample_rate == 0
        || !carrier_hz.is_finite()
        || carrier_hz <= 0.0
        || carrier_hz >= sample_rate as f64 / 2.0
    {
        return Err("USB audio carrier must be positive and below Nyquist".into());
    }
    let mean = pairwise_sum(pcm) / pcm.len() as f64;
    let mut values: Vec<_> = pcm.iter().map(|x| Complex64::new(x - mean, 0.0)).collect();
    let mut planner = FftPlanner::<f64>::new();
    planner.plan_fft_forward(values.len()).process(&mut values);
    let length = values.len();
    // scipy.signal.hilbert: DC=1, positive frequencies=2, negative=0;
    // for even lengths the unpaired Nyquist coefficient remains unchanged.
    for (index, value) in values.iter_mut().enumerate().skip(1) {
        let factor = if length % 2 == 0 && index == length / 2 {
            1.0
        } else if index <= (length - 1) / 2 {
            2.0
        } else {
            0.0
        };
        *value *= factor;
    }
    planner.plan_fft_inverse(length).process(&mut values);
    for (index, value) in values.iter_mut().enumerate() {
        *value /= length as f64;
        let angle = (-2.0 * PI * carrier_hz) * index as f64 / sample_rate as f64;
        *value *= Complex64::new(angle.cos(), angle.sin());
    }
    Ok(values)
}

/// Independently routed RF-IQ frontends. Explicit decimation and discriminator
/// identity are required: PCM conditioning must never be mistaken for RF FM.
pub fn iq_frontend(
    iq: &[Complex64],
    sample_rate: u32,
    config: &DspConfig,
    demodulator: &str,
    decimation: usize,
    cutoff_hz: Option<f64>,
) -> Result<Frontend, String> {
    if !(8_192..=MAX_PCM_WINDOW_SAMPLES).contains(&iq.len())
        || iq.iter().any(|x| !x.re.is_finite() || !x.im.is_finite())
    {
        return Err("IQ must contain 8192..4194304 finite complex samples".into());
    }
    if sample_rate == 0 || !config.baud.is_finite() || config.baud <= 0.0 || decimation == 0 {
        return Err("sample rate, baud and decimation must be positive".into());
    }
    let samples_per_symbol = sample_rate as f64 / (decimation as f64 * config.baud);
    if samples_per_symbol <= 1.1 {
        return Err("decimation must leave more than 1.1 samples per symbol".into());
    }
    validate_search(config, samples_per_symbol)?;
    let discriminator = match demodulator {
        "phase_fsk" => {
            let cutoff = cutoff_hz.unwrap_or(0.75 * config.baud);
            if !cutoff.is_finite() || cutoff <= 0.0 || cutoff >= sample_rate as f64 / 2.0 {
                return Err(
                    "post-discriminator cutoff must be positive and below input Nyquist".into(),
                );
            }
            let increments = phase_increments(iq);
            let taps = firwin(115, 0.0, cutoff, sample_rate as f64);
            fir_decimated(&increments, &taps, decimation)?
        }
        "channel_conditioned_phase_fsk" => {
            if cutoff_hz.is_some() {
                return Err(
                    "channel-conditioned frontend has no custom post-discriminator cutoff".into(),
                );
            }
            if 0.625 * config.baud >= sample_rate as f64 / 2.0 {
                return Err("sample rate is too low for the requested FSK channel".into());
            }
            let relaxed_taps = firwin(
                129,
                0.0,
                (1.25 * config.baud).min(0.45 * sample_rate as f64),
                sample_rate as f64,
            );
            let relaxed = complex_fir_decimated(iq, &relaxed_taps, 1)?;
            let mut increments = phase_increments(&relaxed);
            let carrier_increment = median(&mut increments);
            let corrected: Vec<_> = iq
                .iter()
                .enumerate()
                .map(|(i, value)| {
                    let angle = -carrier_increment * i as f64;
                    value * Complex64::new(angle.cos(), angle.sin())
                })
                .collect();
            let channel_taps = firwin(129, 0.0, 0.625 * config.baud, sample_rate as f64);
            let channel = complex_fir_decimated(&corrected, &channel_taps, decimation)?;
            phase_increments(&channel).iter().map(|x| 1.2 * x).collect()
        }
        _ => {
            return Err(
                "IQ frontend must explicitly select phase_fsk or channel_conditioned_phase_fsk"
                    .into(),
            );
        }
    };
    let samples = dc_block_and_normalize(&discriminator, samples_per_symbol)?;
    Ok(Frontend {
        samples,
        samples_per_symbol,
    })
}

/// Port of the generic `bell202_afsk` IQ plugin, whose precision and centering
/// contract intentionally differs from the PCM AFSK path. The historical
/// plugin converts IQ and phase increments to f32; filters/tone energies use
/// f64; the final energy difference is q90-scaled without median recentering
/// and rounded to f32. None of those conversions is a speed optimization.
pub fn bell202_iq_frontend(
    iq: &[Complex64],
    sample_rate: u32,
    config: &DspConfig,
    decimation: usize,
    mark_hz: f64,
    space_hz: f64,
) -> Result<Frontend, String> {
    if !(16_384..=MAX_PCM_WINDOW_SAMPLES).contains(&iq.len()) {
        return Err("Bell-202 IQ requires 16384..4194304 complex samples".into());
    }
    if sample_rate <= 6_000
        || decimation == 0
        || !(sample_rate as usize).is_multiple_of(decimation)
        || !config.baud.is_finite()
        || config.baud <= 0.0
    {
        return Err(
            "Bell-202 IQ needs input rate above 6000, dividing decimation and positive baud".into(),
        );
    }
    let output_rate = sample_rate as f64 / decimation as f64;
    let samples_per_symbol = output_rate / config.baud;
    if samples_per_symbol <= 2.0 {
        return Err("Bell-202 IQ audio must retain more than two samples per symbol".into());
    }
    validate_search(config, samples_per_symbol)?;
    if !mark_hz.is_finite()
        || !space_hz.is_finite()
        || mark_hz <= 0.0
        || space_hz <= 0.0
        || mark_hz >= output_rate / 2.0
        || space_hz >= output_rate / 2.0
        || mark_hz == space_hz
    {
        return Err("Bell-202 tones must differ, be positive and lie below audio Nyquist".into());
    }
    let mut values = Vec::with_capacity(iq.len());
    let mut maximum_magnitude = 0.0_f32;
    for value in iq {
        let converted = Complex32::new(value.re as f32, value.im as f32);
        if !converted.re.is_finite() || !converted.im.is_finite() {
            return Err("Bell-202 IQ must remain finite after complex64 conversion".into());
        }
        maximum_magnitude = maximum_magnitude.max(converted.re.hypot(converted.im));
        values.push(converted);
    }
    if maximum_magnitude as f64 <= 1e-12 {
        return Err("degenerate all-zero IQ window".into());
    }
    let increments: Vec<f64> = values
        .windows(2)
        .map(|pair| {
            let product = pair[1] * pair[0].conj();
            product.im.atan2(product.re) as f64
        })
        .collect();
    let taps = firwin(129, 500.0, 3_000.0, sample_rate as f64);
    let audio = fir_decimated(&increments, &taps, decimation)?;
    if audio.len() < 2_048 {
        return Err("not enough Bell-202 discriminator audio after filtering".into());
    }
    let audio = normalize(audio)?;
    let tone_length = round_ties_even(samples_per_symbol).max(4.0) as usize;
    let trim = tone_length
        .checked_mul(2)
        .ok_or("tone correlation length overflows")?;
    if trim >= audio.len() {
        return Err("not enough Bell-202 samples after tone correlation".into());
    }
    let mark = moving_tone_energy(&audio, mark_hz, output_rate, tone_length);
    let space = moving_tone_energy(&audio, space_hz, output_rate, tone_length);
    let difference: Vec<f64> = mark
        .iter()
        .zip(&space)
        .skip(trim)
        .map(|(m, s)| m - s)
        .collect();
    let mut absolute: Vec<f64> = difference.iter().map(|x| x.abs()).collect();
    if absolute.iter().any(|x| !x.is_finite()) {
        return Err("degenerate Bell-202 energy stream".into());
    }
    let scale = quantile(&mut absolute, 0.90);
    if !scale.is_finite() || scale <= 1e-12 {
        return Err("degenerate Bell-202 energy stream".into());
    }
    let samples: Vec<f64> = difference
        .iter()
        .map(|x| ((x / scale) as f32) as f64)
        .collect();
    if samples.iter().any(|x| !x.is_finite()) {
        return Err("Bell-202 soft symbols overflowed float32".into());
    }
    Ok(Frontend {
        samples,
        samples_per_symbol,
    })
}

fn phase_increments(values: &[Complex64]) -> Vec<f64> {
    values
        .windows(2)
        .map(|pair| {
            let product = pair[1] * pair[0].conj();
            product.im.atan2(product.re)
        })
        .collect()
}

fn complex_fir_decimated(
    iq: &[Complex64],
    taps: &[f64],
    decimation: usize,
) -> Result<Vec<Complex64>, String> {
    crate::compute::current().fir_complex(iq, taps, decimation, false)
}

fn dc_block_and_normalize(values: &[f64], samples_per_symbol: f64) -> Result<Vec<f64>, String> {
    let length = round_ties_even(512.0 * samples_per_symbol).max(32.0) as usize;
    if values.len() <= length {
        return Err("window is too short for the adaptive DC blocker".into());
    }
    let mut cumulative = Vec::with_capacity(values.len() + 1);
    cumulative.push(0.0);
    let mut sum = 0.0;
    for value in values {
        sum += value;
        cumulative.push(sum);
    }
    let detrended = (length - 1..values.len())
        .map(|i| {
            let trend = (cumulative[i + 1] - cumulative[i + 1 - length]) / length as f64;
            values[i] - trend
        })
        .collect();
    normalize(detrended)
}

/// NumPy's contiguous float64 reduction uses an eight-accumulator pairwise
/// tree. Keeping this order avoids the much larger drift of a serial fold.
pub(crate) fn pairwise_sum(values: &[f64]) -> f64 {
    if values.len() < 8 {
        return values.iter().fold(-0.0, |sum, value| sum + value);
    }
    if values.len() <= 128 {
        let mut sums = [0.0; 8];
        sums.copy_from_slice(&values[..8]);
        let end = values.len() - values.len() % 8;
        for chunk in values[8..end].as_chunks::<8>().0 {
            for j in 0..8 {
                sums[j] += chunk[j];
            }
        }
        let mut result = ((sums[0] + sums[1]) + (sums[2] + sums[3]))
            + ((sums[4] + sums[5]) + (sums[6] + sums[7]));
        for value in &values[end..] {
            result += value;
        }
        result
    } else {
        let middle = (values.len() / 2) & !7;
        pairwise_sum(&values[..middle]) + pairwise_sum(&values[middle..])
    }
}

fn sinc(value: f64) -> f64 {
    if value == 0.0 {
        1.0
    } else {
        let argument = PI * value;
        argument.sin() / argument
    }
}

pub(crate) fn firwin(length: usize, low: f64, high: f64, sample_rate: f64) -> Vec<f64> {
    let left = low / (sample_rate / 2.0);
    let right = high / (sample_rate / 2.0);
    let alpha = 0.5 * (length - 1) as f64;
    let scale_frequency = if left == 0.0 {
        0.0
    } else {
        0.5 * (left + right)
    };
    let mut taps = Vec::with_capacity(length);
    let mut scaled = Vec::with_capacity(length);
    let step = 2.0 * PI / (length - 1) as f64;
    for i in 0..length {
        let m = i as f64 - alpha;
        // scipy.general_hamming uses [0.54, 1.0-0.54], linspace(-pi,pi).
        let angle = if i + 1 == length {
            PI
        } else {
            i as f64 * step - PI
        };
        let window = 0.54 + (1.0 - 0.54) * angle.cos();
        let tap = (right * sinc(right * m) - left * sinc(left * m)) * window;
        taps.push(tap);
        scaled.push(tap * (PI * m * scale_frequency).cos());
    }
    let scale = pairwise_sum(&scaled);
    for value in &mut taps {
        *value /= scale;
    }
    taps
}

/// Evaluate only retained FIR outputs. This has exactly the same causal
/// window and startup removal as lfilter(taps,[1],pcm)[taps.len()-1::decim].
pub(crate) fn fir_decimated(
    pcm: &[f64],
    taps: &[f64],
    decimation: usize,
) -> Result<Vec<f64>, String> {
    crate::compute::current().fir_real(pcm, taps, decimation)
}

fn quantile(values: &mut [f64], fraction: f64) -> f64 {
    let index = (values.len() - 1) as f64 * fraction;
    let lower = index.floor() as usize;
    let weight = index - lower as f64;
    let (_, element, upper_part) = values.select_nth_unstable_by(lower, |a, b| a.total_cmp(b));
    let a = *element;
    if weight == 0.0 {
        return a;
    }
    let b = upper_part.iter().copied().fold(f64::INFINITY, f64::min);
    // NumPy _lerp uses the complementary expression when t >= 0.5.
    if weight >= 0.5 {
        b - (b - a) * (1.0 - weight)
    } else {
        a + (b - a) * weight
    }
}

pub(crate) fn normalize(mut values: Vec<f64>) -> Result<Vec<f64>, String> {
    if values.is_empty() || values.iter().any(|x| !x.is_finite()) {
        return Err("degenerate real-audio symbol stream".into());
    }
    let mut scratch = values.clone();
    let center = median(&mut scratch);
    for (value, absolute) in values.iter_mut().zip(&mut scratch) {
        *value -= center;
        *absolute = value.abs();
    }
    // Subtraction itself can overflow even when both operands were finite.
    // This is malformed numeric input, never a completed empty waveform.
    if scratch.iter().any(|x| !x.is_finite()) {
        return Err("degenerate real-audio symbol stream".into());
    }
    let mut scale = quantile(&mut scratch, 0.90);
    if scale <= 1e-12 {
        // A vanishing percentile does not imply absence of symbols: saturated
        // plateaus or a short burst can occupy less than 10% of a window. Keep
        // every finite nonzero residual and use a bounded peak scale only on
        // the formerly failing branch. Ordinary percentile arithmetic remains
        // unchanged. No CRC, decoded packet or expected bytes guide this choice.
        let peak = scratch.iter().copied().fold(0.0_f64, f64::max);
        if peak == 0.0 {
            return Ok(vec![]);
        }
        scale = peak;
    }
    for value in &mut values {
        *value /= scale;
        if !value.is_finite() {
            return Err("degenerate real-audio symbol stream".into());
        }
    }
    Ok(values)
}

fn median(values: &mut [f64]) -> f64 {
    let middle = values.len() / 2;
    let even = values.len().is_multiple_of(2);
    let (lower, center, _) = values.select_nth_unstable_by(middle, |a, b| a.total_cmp(b));
    if even {
        (lower.iter().copied().fold(f64::NEG_INFINITY, f64::max) + *center) / 2.0
    } else {
        *center
    }
}

fn moving_tone_energy(values: &[f64], frequency: f64, sample_rate: f64, length: usize) -> Vec<f64> {
    let correlated: Vec<(f64, f64)> = values
        .iter()
        .enumerate()
        .map(|(i, &sample)| {
            // Preserve Python's oscillator order: (-2*pi*f)*indices / rate.
            let phase = (-2.0 * PI * frequency) * i as f64 / sample_rate;
            (sample * phase.cos(), sample * phase.sin())
        })
        .collect();
    let tap = 1.0 / length as f64;
    let mut result = Vec::with_capacity(values.len());
    for index in 0..values.len() {
        let mut real = 0.0;
        let mut imag = 0.0;
        for offset in 0..length.min(index + 1) {
            real += tap * correlated[index - offset].0;
            imag += tap * correlated[index - offset].1;
        }
        let absolute = real.hypot(imag);
        result.push(absolute * absolute);
    }
    result
}

fn interpolate(values: &[f64], position: f64) -> f64 {
    if position <= 0.0 {
        return values[0];
    }
    let index = position as usize;
    if index >= values.len() - 1 {
        return values[values.len() - 1];
    }
    let slope = values[index + 1] - values[index];
    slope * (position - index as f64) + values[index]
}

/// Rank the complete phase/rate bank; a stable explicit key breaks score ties.
pub fn ranked_timing(
    frontend: &Frontend,
    config: &DspConfig,
) -> Result<Vec<TimingHypothesis>, String> {
    validate_search(config, frontend.samples_per_symbol)?;
    let values = &frontend.samples;
    if values.len() < 256 || values.iter().any(|x| !x.is_finite()) {
        return Err("timing samples must be finite and have length >= 256".into());
    }
    let mut rates = config.rate_errors_ppm.clone();
    rates.sort_by(|a, b| a.partial_cmp(b).unwrap_or(Ordering::Equal));
    rates.dedup_by(|a, b| *a == *b);
    let max_step = frontend.samples_per_symbol * (1.0 + rates[rates.len() - 1] * 1e-6);
    let count = ((values.len() as f64 - max_step) / max_step).floor() as usize;
    let shared = count
        .checked_sub(2 * GUARD_SYMBOLS)
        .ok_or("not enough samples for the requested guard")?;
    if shared < 128 {
        return Err("not enough samples for the requested guard".into());
    }
    let mut hypotheses = Vec::with_capacity(rates.len() * config.phase_bins);
    let mut symbols = Vec::with_capacity(shared);
    let mut scratch = Vec::with_capacity(shared);
    let mut lower = Vec::with_capacity(shared);
    let mut upper = Vec::with_capacity(shared);
    for rate in rates {
        let step = frontend.samples_per_symbol * (1.0 + rate * 1e-6);
        for phase_index in 0..config.phase_bins {
            let phase = step * (phase_index as f64 + 0.5) / config.phase_bins as f64;
            symbols.clear();
            for index in GUARD_SYMBOLS..GUARD_SYMBOLS + shared {
                symbols.push(interpolate(values, phase + index as f64 * step));
            }
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
                let lower_mean = pairwise_sum(&lower) / lower.len() as f64;
                let upper_mean = pairwise_sum(&upper) / upper.len() as f64;
                threshold = 0.5 * (lower_mean + upper_mean);
                for value in &mut lower {
                    let residual = *value - lower_mean;
                    *value = residual * residual;
                }
                for value in &mut upper {
                    let residual = *value - upper_mean;
                    *value = residual * residual;
                }
                let within = (0.5
                    * (pairwise_sum(&lower) / lower.len() as f64
                        + pairwise_sum(&upper) / upper.len() as f64)
                    + 1e-15)
                    .sqrt();
                (upper_mean - lower_mean) / within
            };
            hypotheses.push(TimingHypothesis {
                rate_error_ppm: rate,
                phase_samples: phase,
                step_samples: step,
                threshold,
                score,
                symbol_count: shared,
            });
        }
    }
    hypotheses.sort_by(|a, b| {
        b.score
            .total_cmp(&a.score)
            .then_with(|| a.rate_error_ppm.abs().total_cmp(&b.rate_error_ppm.abs()))
            .then_with(|| a.rate_error_ppm.total_cmp(&b.rate_error_ppm))
            .then_with(|| a.phase_samples.total_cmp(&b.phase_samples))
    });
    Ok(hypotheses)
}

pub fn additive_diverse_bank(
    ranked: &[TimingHypothesis],
    global_top: usize,
    per_rate: usize,
    min_phase_distance: f64,
) -> Result<Vec<TimingHypothesis>, String> {
    if global_top == 0
        || per_rate == 0
        || !min_phase_distance.is_finite()
        || !(0.0..=0.5).contains(&min_phase_distance)
    {
        return Err("invalid clock-diversity budget or phase distance".into());
    }
    for item in ranked {
        if !item.rate_error_ppm.is_finite()
            || !item.phase_samples.is_finite()
            || !item.step_samples.is_finite()
            || item.step_samples <= 0.0
        {
            return Err("clock metadata must be finite with a positive step".into());
        }
    }
    let mut selected = ranked[..global_top.min(ranked.len())].to_vec();
    let mut rates: Vec<_> = ranked.iter().map(|h| h.rate_error_ppm).collect();
    rates.sort_by(|a, b| a.abs().total_cmp(&b.abs()).then_with(|| a.total_cmp(b)));
    rates.dedup_by(|a, b| *a == *b);
    for rate in rates {
        let mut representatives: Vec<f64> = Vec::new();
        for h in ranked.iter().filter(|h| h.rate_error_ppm == rate) {
            let phase = (h.phase_samples / h.step_samples).rem_euclid(1.0);
            if representatives.iter().any(|previous| {
                let distance = (phase - previous).abs();
                distance.min(1.0 - distance) < min_phase_distance
            }) {
                continue;
            }
            representatives.push(phase);
            if !selected.iter().any(|s| {
                s.rate_error_ppm == h.rate_error_ppm
                    && s.phase_samples == h.phase_samples
                    && s.step_samples == h.step_samples
            }) {
                selected.push(h.clone());
            }
            if representatives.len() == per_rate {
                break;
            }
        }
    }
    Ok(selected)
}

pub fn timing_bank(
    frontend: &Frontend,
    config: &DspConfig,
) -> Result<Vec<TimingHypothesis>, String> {
    validate_search(config, frontend.samples_per_symbol)?;
    if frontend.samples.is_empty() {
        return Ok(vec![]);
    }
    let mut ranked = ranked_timing(frontend, config)?;
    if config.bank == "burst" {
        additive_burst_bank(frontend, config, &ranked)
    } else if config.bank == "diverse" {
        additive_diverse_bank(&ranked, config.top_timing, 2, 0.20)
    } else if config.bank == "global" {
        ranked.truncate(config.top_timing);
        Ok(ranked)
    } else {
        Ok(ranked)
    }
}

/// Experimental reference-free local acquisition. Retains every legacy diverse
/// hypothesis, in the same order, and appends clocks fitted to 4096-symbol
/// regions with 50% overlap. This reduces domination by other bursts/noise in a
/// long analysis window; it is not a continuous carrier or timing tracking loop.
///
/// Local thresholds and scores come only from waveform samples. Neither CRC,
/// decoded payloads nor archive matches participate in fitting or selection.
/// Extra searches are not independent confirmations or calibrated confidence.
fn additive_burst_bank(
    frontend: &Frontend,
    config: &DspConfig,
    ranked: &[TimingHypothesis],
) -> Result<Vec<TimingHypothesis>, String> {
    let mut selected = additive_diverse_bank(ranked, config.top_timing, 2, 0.20)?;
    let width_f64 = (4096.0 * frontend.samples_per_symbol).ceil();
    if !width_f64.is_finite() || width_f64 >= frontend.samples.len() as f64 {
        return Ok(selected);
    }
    let width = width_f64 as usize;
    let stride = width / 2;
    if stride == 0 || frontend.samples.len().div_ceil(stride) > 4096 {
        return Err("local timing search exceeds 4096 regions; no regions were pruned".into());
    }
    let last_start = frontend.samples.len() - width;
    let mut start = 0;
    loop {
        let local = Frontend {
            samples: frontend.samples[start..start + width].to_vec(),
            samples_per_symbol: frontend.samples_per_symbol,
        };
        let local_ranked = ranked_timing(&local, config)?;
        for mut timing in additive_diverse_bank(&local_ranked, 4, 1, 0.20)? {
            timing.phase_samples += start as f64;
            selected.push(timing);
        }
        if start == last_start {
            break;
        }
        start = (start + stride).min(last_start);
    }
    Ok(selected)
}

/// Immutable borrow whose samples have passed the public soft-symbol finite
/// check. Keeping the borrow private prevents invalidation between hypotheses.
/// Metadata not used by soft-symbol interpolation is deliberately not validated.
pub(crate) struct ValidatedFrontend<'a> {
    values: &'a [f64],
}

impl<'a> ValidatedFrontend<'a> {
    pub(crate) fn new(frontend: &'a Frontend) -> Result<Self, String> {
        if frontend.samples.iter().any(|x| !x.is_finite()) {
            return Err("soft-symbol inputs must be finite with a positive step".into());
        }
        Ok(Self {
            values: &frontend.samples,
        })
    }

    fn symbol_end(&self, timing: &TimingHypothesis) -> Result<usize, String> {
        if !timing.phase_samples.is_finite()
            || !timing.step_samples.is_finite()
            || timing.step_samples <= 0.0
        {
            return Err("soft-symbol inputs must be finite with a positive step".into());
        }
        if timing.symbol_count == 0 {
            return Ok(GUARD_SYMBOLS);
        }
        let end = GUARD_SYMBOLS
            .checked_add(timing.symbol_count)
            .ok_or("symbol count overflow")?;
        let last = timing.phase_samples + (end - 1) as f64 * timing.step_samples;
        if self.values.is_empty() || !last.is_finite() || last > (self.values.len() - 1) as f64 {
            return Err("timing hypothesis extends beyond the sample stream".into());
        }
        Ok(end)
    }

    pub(crate) fn soft_symbols(&self, timing: &TimingHypothesis) -> Result<Vec<f64>, String> {
        let mut output = Vec::new();
        self.soft_symbols_into(timing, &mut output)?;
        Ok(output)
    }

    /// Reuse an allocation without changing interpolation arithmetic or guards.
    /// Every fallible check completes before touching output, so it is unchanged
    /// on Err. A successful zero-symbol hypothesis clears output but retains its
    /// capacity. The original negative-phase endpoint clamping is retained.
    pub(crate) fn soft_symbols_into(
        &self,
        timing: &TimingHypothesis,
        output: &mut Vec<f64>,
    ) -> Result<(), String> {
        let end = self.symbol_end(timing)?;
        output.clear();
        output.extend((GUARD_SYMBOLS..end).map(|index| {
            interpolate(
                self.values,
                timing.phase_samples + index as f64 * timing.step_samples,
            )
        }));
        Ok(())
    }
}

pub fn soft_symbols(frontend: &Frontend, timing: &TimingHypothesis) -> Result<Vec<f64>, String> {
    ValidatedFrontend::new(frontend)?.soft_symbols(timing)
}

#[cfg(test)]
mod tests {
    use super::*;

    // Frozen pre-optimization implementation. Keep its checks and arithmetic
    // separate from ValidatedFrontend so the regression is not a self-oracle.
    fn frozen_soft_symbols(
        frontend: &Frontend,
        timing: &TimingHypothesis,
    ) -> Result<Vec<f64>, String> {
        let values = &frontend.samples;
        if values.iter().any(|x| !x.is_finite())
            || !timing.phase_samples.is_finite()
            || !timing.step_samples.is_finite()
            || timing.step_samples <= 0.0
        {
            return Err("soft-symbol inputs must be finite with a positive step".into());
        }
        if timing.symbol_count == 0 {
            return Ok(vec![]);
        }
        let end = GUARD_SYMBOLS
            .checked_add(timing.symbol_count)
            .ok_or("symbol count overflow")?;
        let last = timing.phase_samples + (end - 1) as f64 * timing.step_samples;
        if values.is_empty() || !last.is_finite() || last > (values.len() - 1) as f64 {
            return Err("timing hypothesis extends beyond the sample stream".into());
        }
        Ok((GUARD_SYMBOLS..end)
            .map(|index| {
                interpolate(
                    values,
                    timing.phase_samples + index as f64 * timing.step_samples,
                )
            })
            .collect())
    }

    fn symbol_result_bits(result: Result<Vec<f64>, String>) -> Result<Vec<u64>, String> {
        result.map(|values| values.into_iter().map(f64::to_bits).collect())
    }

    fn assert_soft_symbols_match_frozen(frontend: &Frontend, timing: &TimingHypothesis) {
        let expected = symbol_result_bits(frozen_soft_symbols(frontend, timing));
        assert_eq!(
            symbol_result_bits(soft_symbols(frontend, timing)),
            expected,
            "public API mismatch: {timing:?}"
        );
        let validated = ValidatedFrontend::new(frontend);
        assert_eq!(
            symbol_result_bits(
                validated
                    .as_ref()
                    .map_err(Clone::clone)
                    .and_then(|front| { front.soft_symbols(timing) })
            ),
            expected,
            "validated API mismatch: {timing:?}"
        );
        let mut output = vec![f64::from_bits(0x7ff8_0000_0000_0042), -0.0, 9.0];
        let previous: Vec<_> = output.iter().map(|x| x.to_bits()).collect();
        let actual = validated.and_then(|front| front.soft_symbols_into(timing, &mut output));
        match expected {
            Ok(expected) => {
                assert!(actual.is_ok(), "scratch API failed: {timing:?}");
                assert_eq!(
                    output.iter().map(|x| x.to_bits()).collect::<Vec<_>>(),
                    expected,
                    "scratch output mismatch: {timing:?}"
                );
            }
            Err(expected) => {
                assert_eq!(actual, Err(expected), "scratch error mismatch: {timing:?}");
                assert_eq!(
                    output.iter().map(|x| x.to_bits()).collect::<Vec<_>>(),
                    previous,
                    "scratch changed on error: {timing:?}"
                );
            }
        }
    }

    #[test]
    fn validated_soft_symbols_preserve_frozen_bits_and_edge_errors() {
        let cases = [
            vec![],
            vec![-0.0],
            vec![-f64::MAX, f64::MAX, 0.0, -0.0],
            (0..512)
                .map(|i| ((i * 97 % 211) as f64 - 105.0) / 32.0)
                .collect(),
        ];
        for samples in cases {
            let front = Frontend {
                samples,
                // This metadata was never a soft-symbol validation condition.
                samples_per_symbol: f64::NAN,
            };
            for phase in [
                -1e300,
                -256.0,
                -80.0,
                -32.5,
                -0.0,
                0.0,
                0.125,
                1.0,
                10.0,
                511.0,
                f64::MAX,
                f64::NAN,
                f64::NEG_INFINITY,
                f64::INFINITY,
            ] {
                for step in [
                    f64::from_bits(1),
                    0.5,
                    1.0,
                    2.5,
                    10.0,
                    f64::MAX,
                    0.0,
                    -0.0,
                    -1.0,
                    f64::NAN,
                    f64::INFINITY,
                ] {
                    for symbol_count in [0, 1, 17, 128, 1024, usize::MAX] {
                        let timing = TimingHypothesis {
                            rate_error_ppm: f64::NAN,
                            phase_samples: phase,
                            step_samples: step,
                            threshold: f64::INFINITY,
                            score: f64::NAN,
                            symbol_count,
                        };
                        assert_soft_symbols_match_frozen(&front, &timing);
                    }
                }
            }
        }
    }

    #[test]
    fn validated_soft_symbols_reject_nonfinite_unused_samples_before_empty_output() {
        for invalid in [f64::NAN, f64::INFINITY, f64::NEG_INFINITY] {
            let mut front = Frontend {
                samples: vec![1.0; 512],
                samples_per_symbol: 2.5,
            };
            front.samples[511] = invalid;
            for symbol_count in [0, 1, usize::MAX] {
                let timing = TimingHypothesis {
                    symbol_count,
                    ..hypothesis(0.0, 0.0, 1.0)
                };
                assert_soft_symbols_match_frozen(&front, &timing);
            }
        }
    }

    #[test]
    fn validated_soft_symbols_reuse_capacity_for_complete_ranked_bank() {
        let front = Frontend {
            samples: (0..4096)
                .map(|i| ((i * 97 % 211) as f64 - 105.0) / 32.0)
                .collect(),
            samples_per_symbol: 2.5,
        };
        let config = DspConfig {
            bank: "full".into(),
            ..Default::default()
        };
        let bank = timing_bank(&front, &config).unwrap();
        assert_eq!(bank.len(), 160);
        let validated = ValidatedFrontend::new(&front).unwrap();
        let mut scratch = Vec::with_capacity(front.samples.len());
        let original_pointer = scratch.as_ptr();
        let original_capacity = scratch.capacity();
        for timing in bank {
            let expected = symbol_result_bits(frozen_soft_symbols(&front, &timing)).unwrap();
            validated.soft_symbols_into(&timing, &mut scratch).unwrap();
            assert_eq!(scratch.as_ptr(), original_pointer);
            assert_eq!(scratch.capacity(), original_capacity);
            assert_eq!(
                scratch.iter().map(|x| x.to_bits()).collect::<Vec<_>>(),
                expected
            );
        }
        let empty = TimingHypothesis {
            symbol_count: 0,
            ..hypothesis(0.0, 0.0, 1.0)
        };
        validated.soft_symbols_into(&empty, &mut scratch).unwrap();
        assert!(scratch.is_empty());
        assert_eq!(scratch.as_ptr(), original_pointer);
        assert_eq!(scratch.capacity(), original_capacity);
    }

    #[test]
    fn default_audio_lengths_match_python_contract() {
        let samples: Vec<_> = (0..9_001)
            .map(|i| ((i * 17 % 113) as f64 - 56.0) / 71.0)
            .collect();
        let conditioned = frontend(&samples, 48_000, &DspConfig::default()).unwrap();
        assert_eq!(conditioned.samples.len(), (9_001 - 115) / 2 + 1 - 1_280 + 1);
        assert_eq!(conditioned.samples_per_symbol, 2.5);
        assert_eq!(
            timing_bank(&conditioned, &DspConfig::default()).unwrap(),
            timing_bank(&conditioned, &DspConfig::default()).unwrap()
        );
    }

    #[test]
    fn validation_precedes_silence() {
        let constant = vec![0.3; 20_001];
        let front = frontend(&constant, 48_000, &DspConfig::default()).unwrap();
        assert!(
            timing_bank(&front, &DspConfig::default())
                .unwrap()
                .is_empty()
        );
        let bad = DspConfig {
            baud: f64::NAN,
            ..Default::default()
        };
        assert!(frontend(&constant, 48_000, &bad).is_err());
        assert!(frontend(&[0.0; 100], 48_000, &DspConfig::default()).is_err());
        let mut nan = constant.clone();
        nan[19] = f64::NAN;
        assert!(frontend(&nan, 48_000, &DspConfig::default()).is_err());
    }

    #[test]
    fn sparse_residuals_survive_zero_percentile_without_fabricated_silence() {
        let mut sparse = vec![0.0; 1000];
        sparse[400] = 0.25;
        sparse[401] = -0.5;
        let out = normalize(sparse).unwrap();
        assert_eq!(out.len(), 1000);
        assert_eq!(out[400], 0.5);
        assert_eq!(out[401], -1.0);
        assert_eq!(out.iter().filter(|x| **x != 0.0).count(), 2);
        let mut tiny = vec![0.0; 1000];
        tiny[99] = 1e-100;
        assert_eq!(normalize(tiny).unwrap()[99], 1.0);
    }

    #[test]
    fn exactly_constant_post_filter_data_is_empty_but_invalid_is_error() {
        assert!(normalize(vec![0.0; 1024]).unwrap().is_empty());
        assert!(normalize(vec![0.75; 1024]).unwrap().is_empty());
        assert!(normalize(vec![]).is_err());
        for x in [f64::NAN, f64::INFINITY, f64::NEG_INFINITY] {
            let mut values = vec![0.0; 1024];
            values[30] = x;
            assert!(normalize(values).is_err());
        }
        assert!(normalize(vec![-f64::MAX, f64::MAX, f64::MAX]).is_err());
    }

    #[test]
    fn previously_valid_percentile_normalization_is_bit_exact() {
        let values: Vec<_> = (0..1000)
            .map(|i| ((i * 137 % 997) as f64 - 499.0) / 997.0)
            .collect();
        let mut expected = values.clone();
        let mut scratch = values.clone();
        let center = median(&mut scratch);
        for (v, a) in expected.iter_mut().zip(&mut scratch) {
            *v -= center;
            *a = v.abs();
        }
        let scale = quantile(&mut scratch, 0.90);
        assert!(scale > 1e-12);
        for v in &mut expected {
            *v /= scale;
        }
        assert_eq!(normalize(values).unwrap(), expected);
    }

    #[test]
    fn sparse_saturated_plateau_frontend_completes_and_preserves_residuals() {
        let mut pcm = vec![-1.0; 48000 * 6];
        for i in 120000..120048 {
            pcm[i] = if i % 2 == 0 { -0.75 } else { -0.5 };
        }
        let front = frontend(&pcm, 48000, &DspConfig::default()).unwrap();
        assert!(!front.samples.is_empty());
        assert!(front.samples.iter().all(|v| v.is_finite()));
        assert!(front.samples.iter().any(|v| v.abs() > 0.5));
        assert!(
            !timing_bank(&front, &DspConfig::default())
                .unwrap()
                .is_empty()
        );
    }

    #[test]
    fn quantiles_rounding_and_reductions() {
        assert_eq!(median(&mut [9.0, 1.0, 3.0, 5.0]), 4.0);
        assert_eq!(quantile(&mut [3.0, 0.0, 2.0, 1.0], 0.90), 2.7);
        assert_eq!(round_ties_even(2.5), 2.0);
        assert_eq!(round_ties_even(3.5), 4.0);
        let sequence: Vec<_> = (0..1_024).map(|i| i as f64).collect();
        assert_eq!(pairwise_sum(&sequence), 523_776.0);
    }

    fn hypothesis(rate: f64, phase: f64, score: f64) -> TimingHypothesis {
        TimingHypothesis {
            rate_error_ppm: rate,
            phase_samples: phase * 5.0,
            step_samples: 5.0,
            threshold: 0.0,
            score,
            symbol_count: 256,
        }
    }

    #[test]
    fn diversity_preserves_prefix_and_circular_separation() {
        let ranked: Vec<_> = [0.99, 0.01, 0.30, 0.50]
            .iter()
            .map(|&phase| hypothesis(0.0, phase, 1.0))
            .collect();
        let selected = additive_diverse_bank(&ranked, 1, 2, 0.20).unwrap();
        assert_eq!(selected, vec![ranked[0].clone(), ranked[2].clone()]);
        let ranked: Vec<_> = [-100.0, 0.0, 100.0]
            .iter()
            .flat_map(|&rate| {
                (0..32).map(move |i| hypothesis(rate, i as f64 / 32.0, 100.0 - i as f64))
            })
            .collect();
        let selected = additive_diverse_bank(&ranked, 16, 2, 0.20).unwrap();
        assert_eq!(&selected[..16], &ranked[..16]);
        assert!(selected.len() <= 22);
        assert!(additive_diverse_bank(&ranked, 1, 2, f64::NAN).is_err());
    }

    #[test]
    fn tied_scores_and_duplicate_rates_are_stable() {
        let front = Frontend {
            samples: vec![1.0; 2_048],
            samples_per_symbol: 2.5,
        };
        let config = DspConfig {
            rate_errors_ppm: vec![100.0, -100.0, 0.0, 0.0],
            phase_bins: 4,
            bank: "global".into(),
            ..Default::default()
        };
        let ranked = ranked_timing(&front, &config).unwrap();
        assert_eq!(ranked.len(), 12);
        assert_eq!(ranked[0].rate_error_ppm, 0.0);
        assert_eq!(ranked[4].rate_error_ppm, -100.0);
        assert_eq!(ranked[8].rate_error_ppm, 100.0);
        assert!(ranked.iter().all(|h| h.score == f64::NEG_INFINITY));
        assert!(soft_symbols(&front, &hypothesis(0.0, 0.0, 1.0)).is_ok());
        let mut beyond = hypothesis(0.0, 0.0, 1.0);
        beyond.symbol_count = 10_000;
        assert!(soft_symbols(&front, &beyond).is_err());
    }

    #[test]
    fn fir_is_causal_and_normalized() {
        let taps = firwin(115, 0.0, 7_200.0, 48_000.0);
        assert!((pairwise_sum(&taps) - 1.0).abs() < 1e-14);
        assert!(
            taps.iter()
                .zip(taps.iter().rev())
                .all(|(a, b)| (a - b).abs() < 1e-15)
        );
        assert_eq!(
            fir_decimated(&[1.0, 2.0, 3.0, 4.0], &[0.25, 0.75], 2).unwrap(),
            vec![1.25, 3.25]
        );
    }

    // Independent development oracle: Python audio_receiver._pcm_fsk /
    // _pcm_bell202, clock_recovery.recover_timing_hypotheses, and
    // diverse_timing.additive_diverse_bank, NumPy 2.5.2 / SciPy 1.18.1.
    // PCM is deterministic integer LCG, so the input itself has no libm drift.
    // These fixtures establish numerical agreement on two finite vectors,
    // not a guarantee of bit-identical floating point on every possible input.
    #[test]
    fn python_fsk_and_bell202_numeric_oracles() {
        for (mode, size, baud, length, first, last, threshold, score, expected_bank) in [
            (
                "fsk",
                20_001,
                9_600.0,
                8_665,
                [
                    0.5287756199865468,
                    1.2871690221889776,
                    0.5355195261730397,
                    -0.794355064576215,
                    -0.8926971038884934,
                    -0.17092082045646187,
                    -0.011378494204157156,
                    -0.0342472474099834,
                ],
                [
                    0.611525375104292,
                    -0.2587658324672126,
                    -0.5837977681000736,
                    -0.13786680857705097,
                    0.46851636021273907,
                    0.936236165121992,
                    1.0521068265683744,
                    0.8620454054555187,
                ],
                0.011551936276749608,
                2.7877254615650875,
                vec![
                    (500, 14),
                    (500, 15),
                    (500, 17),
                    (500, 16),
                    (-500, 25),
                    (500, 13),
                    (500, 18),
                    (0, 10),
                    (0, 9),
                    (-500, 26),
                    (0, 8),
                    (0, 7),
                    (100, 2),
                    (100, 3),
                    (100, 4),
                    (-100, 20),
                    (0, 3),
                    (-100, 6),
                    (100, 27),
                    (-500, 4),
                    (500, 26),
                ],
            ),
            (
                "afsk",
                24_001,
                1_200.0,
                4_759,
                [
                    0.6045584090804708,
                    0.6331010960341448,
                    0.4314991749333656,
                    0.295159313749752,
                    0.2118843452278217,
                    0.2349521137862645,
                    -0.17799773732401622,
                    -0.26860012796487714,
                ],
                [
                    0.336952087005701,
                    0.40814398434683324,
                    0.3694842690557503,
                    -0.1191265966103067,
                    0.437070513932851,
                    0.371657564257068,
                    -0.06820966010228639,
                    0.33656446254918443,
                ],
                -0.027492029797096695,
                2.1659714956607488,
                vec![
                    (-500, 17),
                    (-500, 18),
                    (500, 13),
                    (500, 14),
                    (-500, 25),
                    (-500, 16),
                    (-100, 14),
                    (-100, 15),
                    (500, 31),
                    (-500, 24),
                    (0, 18),
                    (500, 30),
                    (-500, 15),
                    (0, 14),
                    (-500, 14),
                    (-100, 19),
                    (0, 10),
                    (-100, 21),
                    (100, 17),
                    (100, 9),
                ],
            ),
        ] {
            let mut state = 42_u64;
            let pcm: Vec<_> = (0..size)
                .map(|_| {
                    state = state
                        .wrapping_mul(6_364_136_223_846_793_005)
                        .wrapping_add(1_442_695_040_888_963_407);
                    ((state >> 32) as f64 - 2_147_483_648.0) / 2_147_483_648.0
                })
                .collect();
            let config = DspConfig {
                mode: mode.into(),
                baud,
                ..Default::default()
            };
            let front = frontend(&pcm, 48_000, &config).unwrap();
            assert_eq!(front.samples.len(), length);
            for (a, b) in front.samples[..8]
                .iter()
                .zip(first)
                .chain(front.samples[length - 8..].iter().zip(last))
            {
                assert!((a - b).abs() < 2e-11, "{mode} sample {a} != {b}");
            }
            let bank = timing_bank(&front, &config).unwrap();
            assert!((bank[0].threshold - threshold).abs() < 2e-11);
            assert!((bank[0].score - score).abs() < 2e-11);
            let identities: Vec<_> = bank
                .iter()
                .map(|h| {
                    (
                        h.rate_error_ppm as i32,
                        (h.phase_samples / h.step_samples * 32.0 - 0.5).round() as i32,
                    )
                })
                .collect();
            assert_eq!(identities, expected_bank, "{mode} ranked/diverse clocks");
        }
    }

    #[test]
    fn full_bank_retains_every_unique_rate_and_phase() {
        let samples: Vec<_> = (0..10_001)
            .map(|i| ((i * 17 % 113) as f64 - 56.0) / 71.0)
            .collect();
        let config = DspConfig {
            bank: "full".into(),
            ..Default::default()
        };
        let conditioned = frontend(&samples, 48_000, &config).unwrap();
        assert_eq!(timing_bank(&conditioned, &config).unwrap().len(), 160);
    }

    #[test]
    fn burst_bank_keeps_legacy_prefix_and_bounds_local_symbols() {
        let front = Frontend {
            samples: (0..27_731)
                .map(|i| ((i * 17 % 113) as f64 - 56.0) / 71.0)
                .collect(),
            samples_per_symbol: 2.5,
        };
        let mut config = DspConfig::default();
        let legacy = timing_bank(&front, &config).unwrap();
        config.bank = "burst".into();
        let burst = timing_bank(&front, &config).unwrap();
        assert_eq!(&burst[..legacy.len()], legacy.as_slice());
        assert!(burst.len() > legacy.len());
        assert_eq!(burst, timing_bank(&front, &config).unwrap());
        for timing in &burst[legacy.len()..] {
            let symbols = soft_symbols(&front, timing).unwrap();
            assert!(symbols.len() < 4096);
            assert!(symbols.iter().all(|sample| sample.is_finite()));
        }
        // The final local region is end-aligned, even for a partial stride.
        let last = burst.last().unwrap();
        assert!(last.phase_samples >= (front.samples.len() - 10_240) as f64);
    }

    #[test]
    fn burst_bank_short_input_is_exact_legacy_bank() {
        let front = Frontend {
            samples: (0..9000).map(|i| (i as f64 * 0.71).sin()).collect(),
            samples_per_symbol: 2.5,
        };
        let mut config = DspConfig::default();
        let legacy = timing_bank(&front, &config).unwrap();
        config.bank = "burst".into();
        assert_eq!(legacy, timing_bank(&front, &config).unwrap());
    }

    #[test]
    fn burst_local_coordinate_translation_preserves_samples() {
        let front = Frontend {
            samples: (0..27_731)
                .map(|i| ((i * 31 % 107) as f64 - 50.0) / 60.0)
                .collect(),
            samples_per_symbol: 2.5,
        };
        let config = DspConfig {
            bank: "burst".into(),
            ..Default::default()
        };
        let ranked = ranked_timing(&front, &config).unwrap();
        let prefix = additive_diverse_bank(&ranked, config.top_timing, 2, 0.20)
            .unwrap()
            .len();
        let actual = timing_bank(&front, &config).unwrap();
        let mut index = prefix;
        for start in [0, 5120, 10_240, 15_360, 17_491] {
            let local = Frontend {
                samples: front.samples[start..start + 10_240].to_vec(),
                samples_per_symbol: 2.5,
            };
            let local_ranked = ranked_timing(&local, &config).unwrap();
            for timing in additive_diverse_bank(&local_ranked, 4, 1, 0.20).unwrap() {
                let expected = soft_symbols(&local, &timing).unwrap();
                let translated = soft_symbols(&front, &actual[index]).unwrap();
                assert_eq!(expected.len(), translated.len());
                assert_eq!(actual[index].threshold, timing.threshold);
                for (a, b) in expected.iter().zip(translated) {
                    assert!((a - b).abs() < 2e-11);
                }
                index += 1;
            }
        }
        assert_eq!(index, actual.len());
    }

    #[test]
    fn burst_bank_preserves_independent_positive_framing_fixture() {
        let oracle: serde_json::Value =
            serde_json::from_str(include_str!("tests/protocol_oracle.json")).unwrap();
        let case = &oracle["cases"][0];
        let packed = hex::decode(case["levels_hex"].as_str().unwrap()).unwrap();
        let count = case["symbol_count"].as_u64().unwrap() as usize;
        let front = Frontend {
            samples: (0..(count * 15))
                .map(|i| {
                    let n = (i / 5) % count;
                    if (packed[n / 8] >> (7 - n % 8)) & 1 == 1 {
                        0.75
                    } else {
                        -0.75
                    }
                })
                .collect(),
            samples_per_symbol: 5.0,
        };
        let mut config = DspConfig::default();
        let mut sets = Vec::new();
        for bank in ["diverse", "burst"] {
            config.bank = bank.into();
            let mut decoded = std::collections::BTreeSet::new();
            for timing in timing_bank(&front, &config).unwrap() {
                let symbols = soft_symbols(&front, &timing).unwrap();
                decoded.extend(
                    crate::protocol::decode_ax25(&symbols, timing.threshold, &[false, true])
                        .unwrap(),
                );
            }
            sets.push(decoded);
        }
        let expected: std::collections::BTreeSet<Vec<u8>> = case["expected"][0]["frames_hex"]
            .as_array()
            .unwrap()
            .iter()
            .map(|p| hex::decode(p.as_str().unwrap()).unwrap())
            .collect();
        assert!(!expected.is_empty());
        assert_eq!(sets[0], expected);
        assert_eq!(sets[1], expected);
    }

    #[test]
    fn python_iq_and_analytic_audio_numeric_oracles() {
        let mut state = 42_u64;
        let scalars: Vec<_> = (0..32_002)
            .map(|_| {
                state = state
                    .wrapping_mul(6_364_136_223_846_793_005)
                    .wrapping_add(1_442_695_040_888_963_407);
                ((state >> 32) as f64 - 2_147_483_648.0) / 2_147_483_648.0
            })
            .collect();
        let iq: Vec<_> = scalars
            .as_chunks::<2>()
            .0
            .iter()
            .map(|x| Complex64::new(x[0], x[1]))
            .collect();
        for (mode, length, first, last) in [
            (
                "phase_fsk",
                4_273,
                [
                    -0.6994417642530109,
                    0.5174595342903562,
                    0.44110202122227593,
                    -0.9828714691671128,
                    -0.5659428716454993,
                    -0.34706131228385106,
                ],
                [
                    -0.2577173644015995,
                    -0.041968869896238145,
                    -0.11399163970286522,
                    -0.26563007469001115,
                    0.24391613781791271,
                    0.16829916418074833,
                ],
            ),
            (
                "channel_conditioned_phase_fsk",
                4_267,
                [
                    -1.0942220598438757,
                    -0.06779638708440464,
                    0.8872744384423886,
                    0.14201491038860886,
                    -0.9276522727033687,
                    -0.7263811567078043,
                ],
                [
                    0.38731011171420227,
                    1.147582258504738,
                    0.06781619257002881,
                    -0.06569405957367486,
                    0.24902214979864792,
                    0.4717147952836509,
                ],
            ),
        ] {
            let front = iq_frontend(&iq, 57_600, &DspConfig::default(), mode, 3, None).unwrap();
            assert_eq!(front.samples.len(), length);
            assert_eq!(front.samples_per_symbol, 2.0);
            for (a, b) in front.samples[..6]
                .iter()
                .zip(first)
                .chain(front.samples[length - 6..].iter().zip(last))
            {
                assert!((a - b).abs() < 2e-10, "{mode} sample {a} != {b}");
            }
        }
        for (length, first, last) in [
            (
                8_192,
                [
                    (0.14089705029624616, 0.4784658336783306),
                    (0.31727743239000045, 0.5446367452095161),
                    (0.1698869655082831, 0.2553581883148194),
                ],
                [
                    (-0.49016946171704845, 0.13535361943640073),
                    (-0.8819888152940956, 0.1901704711311177),
                    (-0.45425388386623633, 0.22549321994318586),
                ],
            ),
            (
                8_193,
                [
                    (0.14096812682747384, 0.2690629196129691),
                    (0.09944661556391324, 0.5445656686782882),
                    (0.16981588897705527, 0.23566939806072265),
                ],
                [
                    (-0.8820598918255356, 0.44606371145999857),
                    (-1.1246506917137444, 0.2255642964745755),
                    (-0.5822589438169773, -0.09330770322316834),
                ],
            ),
        ] {
            let analytic = analytic_audio(&scalars[..length], 48_000, 12_000.0).unwrap();
            for (a, (real, imag)) in analytic[..3]
                .iter()
                .zip(first)
                .chain(analytic[length - 3..].iter().zip(last))
            {
                assert!(
                    (a.re - real).abs() < 2e-11 && (a.im - imag).abs() < 2e-11,
                    "Hilbert {length}: {a} != ({real},{imag})"
                );
            }
        }
        assert!(analytic_audio(&scalars[..8_192], 48_000, 24_000.0).is_err());
        assert!(iq_frontend(&iq, 57_600, &DspConfig::default(), "unknown", 3, None).is_err());
    }

    // Oracle: afsk1200_plugin.bell202_soft_discriminator, complex64 input and
    // float32 output, default and non-default tones. libm/vectorized atan2f
    // can differ by ulps, hence bounded numeric tolerance plus exact bank IDs.
    #[test]
    fn python_bell202_iq_oracles_preserve_precision_and_uncentered_soft() {
        let mut state = 42_u64;
        let scalars: Vec<_> = (0..48_002)
            .map(|_| {
                state = state
                    .wrapping_mul(6_364_136_223_846_793_005)
                    .wrapping_add(1_442_695_040_888_963_407);
                ((state >> 32) as f64 - 2_147_483_648.0) / 2_147_483_648.0
            })
            .collect();
        let iq: Vec<_> = scalars
            .as_chunks::<2>()
            .0
            .iter()
            .map(|x| Complex64::new(x[0], x[1]))
            .collect();
        let config = DspConfig {
            baud: 1_200.0,
            mode: "afsk".into(),
            ..Default::default()
        };
        for (rate, decimation, mark, space, length, first, last, score, threshold, expected_bank) in [
            (
                57_600,
                6,
                1_200.0,
                2_200.0,
                3_963,
                [
                    -0.04180971160531044,
                    -0.2514486312866211,
                    -0.5942384600639343,
                    -1.3084477186203003,
                    -0.9080979824066162,
                    -0.5986239314079285,
                    -0.5562217235565186,
                    0.5816896557807922,
                ],
                [
                    0.13651736080646515,
                    0.22550128400325775,
                    0.23654434084892273,
                    0.15222303569316864,
                    0.19534310698509216,
                    0.46348491311073303,
                    0.49754956364631653,
                    0.5209624767303467,
                ],
                2.0855425671798025,
                -0.03393547387435211,
                vec![
                    (100, 18),
                    (0, 18),
                    (100, 17),
                    (-100, 19),
                    (0, 19),
                    (100, 19),
                    (0, 7),
                    (-100, 8),
                    (-100, 20),
                    (100, 6),
                    (500, 16),
                    (-100, 18),
                    (500, 17),
                    (0, 20),
                    (100, 7),
                    (500, 15),
                    (-500, 20),
                    (-500, 4),
                    (500, 1),
                ],
            ),
            (
                48_000,
                5,
                1_300.0,
                2_300.0,
                4_759,
                [
                    -0.06380419433116913,
                    0.004611980635672808,
                    -0.0112649817019701,
                    -0.0392848402261734,
                    -0.14257903397083282,
                    -0.12488340586423874,
                    -0.9102619886398315,
                    -0.6617953777313232,
                ],
                [
                    0.09578759223222733,
                    0.04104170575737953,
                    0.04554831236600876,
                    0.03631332516670227,
                    -0.10997983068227768,
                    0.009983380325138569,
                    0.28754234313964844,
                    0.39499080181121826,
                ],
                2.105355962757109,
                -0.016273246111986328,
                vec![
                    (500, 29),
                    (-500, 28),
                    (100, 2),
                    (500, 28),
                    (500, 27),
                    (100, 3),
                    (-500, 27),
                    (-100, 3),
                    (0, 3),
                    (0, 4),
                    (0, 2),
                    (100, 1),
                    (-100, 5),
                    (-500, 25),
                    (-500, 29),
                    (-500, 26),
                    (0, 20),
                    (-100, 21),
                    (100, 26),
                    (-500, 6),
                    (500, 4),
                ],
            ),
        ] {
            let front = bell202_iq_frontend(&iq, rate, &config, decimation, mark, space).unwrap();
            assert_eq!(front.samples.len(), length);
            assert_eq!(front.samples_per_symbol, 8.0);
            assert!(front.samples.iter().all(|&x| x == (x as f32) as f64));
            for (a, b) in front.samples[..8]
                .iter()
                .zip(first)
                .chain(front.samples[length - 8..].iter().zip(last))
            {
                assert!((a - b).abs() < 2e-6, "Bell-202 IQ {rate}: {a} != {b}");
            }
            let bank = timing_bank(&front, &config).unwrap();
            assert!((bank[0].score - score).abs() < 2e-6);
            assert!((bank[0].threshold - threshold).abs() < 2e-6);
            let identities: Vec<_> = bank
                .iter()
                .map(|h| {
                    (
                        h.rate_error_ppm as i32,
                        (h.phase_samples / h.step_samples * 32.0 - 0.5).round() as i32,
                    )
                })
                .collect();
            assert_eq!(identities, expected_bank);
        }
    }

    #[test]
    fn bell202_iq_rejects_invalid_contracts_and_silence() {
        let mut iq = vec![Complex64::new(1.0, 0.0); 24_001];
        let config = DspConfig {
            baud: 1_200.0,
            mode: "afsk".into(),
            ..Default::default()
        };
        for (rate, decimation, mark, space) in [
            (48_001, 5, 1_200.0, 2_200.0),
            (48_000, 0, 1_200.0, 2_200.0),
            (48_000, 5, 1_200.0, 1_200.0),
            (48_000, 5, f64::NAN, 2_200.0),
            (48_000, 5, 1_200.0, 4_800.0),
        ] {
            assert!(bell202_iq_frontend(&iq, rate, &config, decimation, mark, space).is_err());
        }
        assert!(bell202_iq_frontend(&iq, 48_000, &config, 5, 1_200.0, 2_200.0).is_err());
        iq[0].re = f64::MAX;
        assert!(bell202_iq_frontend(&iq, 48_000, &config, 5, 1_200.0, 2_200.0).is_err());
        iq.fill(Complex64::new(0.0, 0.0));
        assert!(bell202_iq_frontend(&iq, 48_000, &config, 5, 1_200.0, 2_200.0).is_err());
        assert!(bell202_iq_frontend(&iq[..8_192], 48_000, &config, 5, 1_200.0, 2_200.0).is_err());
    }
}
