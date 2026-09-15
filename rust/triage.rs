//! Reference-free, protocol-neutral scheduling statistics. These outputs are
//! hypotheses, never waveform identifications or validated telemetry frames.
//! Nothing here prunes the qualified decoder's timing bank or time windows.
//!
//! CI16-LE and CF32-LE are explicit, headerless layouts. Files are processed one
//! analysis window at a time. Oversized configurations fail, never subsample.
//! Float32 preprocessing follows the Python contract; FFT/reduction rounding
//! may differ from NumPy, so continuous scores are not claimed bit-identical.

use crate::{dsp::pairwise_sum, input};
use num_complex::{Complex32, Complex64};
use rustfft::FftPlanner;
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use std::collections::{BTreeMap, BTreeSet};
use std::f64::consts::PI;
use std::fs::{self, File};
use std::io::{Read, Seek, SeekFrom};
use std::path::Path;

const MAX_WINDOWS: usize = 100_000;
const MAX_WINDOW_SAMPLES: usize = 8_388_608;
const MAX_STFT_VALUES: usize = 4_194_304;
pub const SELECTOR_VERSION: &str = "protocol-neutral-four-lane-v2";

fn require(ok: bool, message: &str) -> Result<(), String> {
    if ok { Ok(()) } else { Err(message.into()) }
}
fn positive(x: f64) -> bool {
    x.is_finite() && x > 0.0
}
fn nonnegative(x: f64) -> bool {
    x.is_finite() && x >= 0.0
}
fn window_samples(rate: f64, seconds: f64) -> Result<usize, String> {
    require(
        positive(rate) && positive(seconds),
        "sample rate and window must be finite and positive",
    )?;
    let count = (rate * seconds).round_ties_even();
    require(
        count >= 1.0 && count <= MAX_WINDOW_SAMPLES as f64,
        "analysis window exceeds supported 1..8388608 sample bound",
    )?;
    Ok(count as usize)
}
fn same_metadata(a: &fs::Metadata, b: &fs::Metadata) -> bool {
    let same =
        a.is_file() && b.is_file() && a.len() == b.len() && a.modified().ok() == b.modified().ok();
    #[cfg(unix)]
    {
        use std::os::unix::fs::MetadataExt;
        same && a.dev() == b.dev()
            && a.ino() == b.ino()
            && a.ctime() == b.ctime()
            && a.ctime_nsec() == b.ctime_nsec()
    }
    #[cfg(not(unix))]
    same
}
fn verify_opened_source(
    file: &File,
    before: &fs::Metadata,
    expected: &input::Identity,
) -> Result<(), String> {
    let opened = file.metadata().map_err(|e| e.to_string())?;
    let current = fs::symlink_metadata(&expected.path).map_err(|e| e.to_string())?;
    require(
        same_metadata(before, &opened)
            && same_metadata(&opened, &current)
            && opened.len() == expected.bytes,
        "opened IQ source identity changed",
    )
}
pub(crate) fn source_file(
    path: &Path,
    bytes_per_sample: u64,
) -> Result<(File, input::Identity, fs::Metadata), String> {
    let meta = fs::symlink_metadata(path).map_err(|e| e.to_string())?;
    require(
        meta.is_file() && !meta.file_type().is_symlink(),
        "IQ input must be a regular non-symlink file",
    )?;
    require(
        meta.len() > 0 && meta.len() % bytes_per_sample == 0,
        "IQ input must be nonempty and contain complete I/Q pairs",
    )?;
    // Nonblocking/no-follow open prevents a FIFO replacement from hanging us.
    // Bind the actual decoding descriptor to the separately hashed pathname.
    let file = input::open_regular(path)?;
    let before = file.metadata().map_err(|e| e.to_string())?;
    require(
        same_metadata(&meta, &before),
        "IQ source changed before opening",
    )?;
    let identity = input::identity(path)?;
    verify_opened_source(&file, &before, &identity)?;
    Ok((file, identity, before))
}
pub(crate) fn unchanged(
    expected: &input::Identity,
    file: &File,
    before: &fs::Metadata,
) -> Result<(), String> {
    verify_opened_source(file, before, expected)?;
    let actual = input::identity(Path::new(&expected.path))?;
    require(
        actual.sha256 == expected.sha256 && actual.bytes == expected.bytes,
        "IQ input changed while being analyzed",
    )?;
    verify_opened_source(file, before, expected)
}
pub(crate) fn read_window(
    file: &mut File,
    start: usize,
    count: usize,
    cf32: bool,
) -> Result<Vec<Complex32>, String> {
    let width = if cf32 { 8 } else { 4 };
    let byte_start = start.checked_mul(width).ok_or("IQ offset overflow")?;
    let mut raw = vec![0; count.checked_mul(width).ok_or("IQ window overflow")?];
    file.seek(SeekFrom::Start(byte_start as u64))
        .map_err(|e| e.to_string())?;
    file.read_exact(&mut raw).map_err(|e| e.to_string())?;
    let values: Vec<_> = raw
        .chunks_exact(width)
        .map(|b| {
            if cf32 {
                Complex32::new(
                    f32::from_le_bytes(b[..4].try_into().unwrap()),
                    f32::from_le_bytes(b[4..].try_into().unwrap()),
                )
            } else {
                Complex32::new(
                    i16::from_le_bytes(b[..2].try_into().unwrap()) as f32,
                    i16::from_le_bytes(b[2..].try_into().unwrap()) as f32,
                )
            }
        })
        .collect();
    require(
        values.iter().all(|z| z.re.is_finite() && z.im.is_finite()),
        "IQ contains nonfinite samples",
    )?;
    Ok(values)
}
pub(crate) fn quantile(values: &[f64], fraction: f64) -> f64 {
    if values.is_empty() {
        return 0.0;
    }
    let mut sorted = values.to_vec();
    sorted.sort_by(f64::total_cmp);
    let position = (sorted.len() - 1) as f64 * fraction;
    let lower = position.floor() as usize;
    let upper = position.ceil() as usize;
    let difference = sorted[upper] - sorted[lower];
    let weight = position - lower as f64;
    if weight >= 0.5 {
        sorted[upper] - difference * (1.0 - weight)
    } else {
        sorted[lower] + difference * weight
    }
}
pub(crate) fn median(values: &[f64]) -> f64 {
    quantile(values, 0.5)
}
pub(crate) fn mean(values: &[f64]) -> f64 {
    pairwise_sum(values) / values.len() as f64
}
fn sum32(values: &[f32]) -> f32 {
    if values.len() < 8 {
        return values.iter().fold(-0.0, |a, b| a + b);
    }
    if values.len() <= 128 {
        let mut r: [f32; 8] = values[..8].try_into().unwrap();
        let end = values.len() - values.len() % 8;
        for chunk in values[8..end].as_chunks::<8>().0 {
            for i in 0..8 {
                r[i] += chunk[i];
            }
        }
        let mut sum = ((r[0] + r[1]) + (r[2] + r[3])) + ((r[4] + r[5]) + (r[6] + r[7]));
        for x in &values[end..] {
            sum += x;
        }
        sum
    } else {
        let middle = (values.len() / 2) & !7;
        sum32(&values[..middle]) + sum32(&values[middle..])
    }
}
pub(crate) fn mean32(values: &[f32]) -> f64 {
    (sum32(values) / values.len() as f32) as f64
}
fn complex_sum32(values: &[Complex32]) -> Complex32 {
    // NumPy's complex pairwise accumulator has four complex lanes (eight scalars).
    if values.len() < 4 {
        return values
            .iter()
            .copied()
            .fold(Complex32::new(-0.0, -0.0), |a, b| a + b);
    }
    if values.len() <= 64 {
        let mut r: [Complex32; 4] = values[..4].try_into().unwrap();
        let end = values.len() - values.len() % 4;
        for chunk in values[4..end].as_chunks::<4>().0 {
            for i in 0..4 {
                r[i] += chunk[i];
            }
        }
        let mut sum = (r[0] + r[1]) + (r[2] + r[3]);
        for x in &values[end..] {
            sum += x;
        }
        sum
    } else {
        let middle = (values.len() / 2) & !3;
        complex_sum32(&values[..middle]) + complex_sum32(&values[middle..])
    }
}
pub(crate) fn centered32(values: &[Complex32]) -> Vec<Complex32> {
    let center = complex_sum32(values) / values.len() as f32;
    values.iter().map(|z| z - center).collect()
}
pub(crate) fn hann32(count: usize) -> Vec<f32> {
    if count == 1 {
        return vec![1.0];
    }
    (0..count)
        .map(|i| (0.5 - 0.5 * (2.0 * PI * i as f64 / (count - 1) as f64).cos()) as f32)
        .collect()
}
pub(crate) fn fft(values: &[Complex64], inverse: bool) -> Vec<Complex64> {
    let mut planner = FftPlanner::<f64>::new();
    let plan = if inverse {
        planner.plan_fft_inverse(values.len())
    } else {
        planner.plan_fft_forward(values.len())
    };
    let mut output = values.to_vec();
    plan.process(&mut output);
    if inverse {
        for x in &mut output {
            *x /= values.len() as f64;
        }
    }
    output
}
pub(crate) fn fft32(values: &[Complex32], taper: Option<&[f32]>) -> Vec<Complex64> {
    let scaled: Vec<_> = values
        .iter()
        .enumerate()
        .map(|(i, z)| {
            let v = if let Some(t) = taper { *z * t[i] } else { *z };
            Complex64::new(v.re as f64, v.im as f64)
        })
        .collect();
    fft(&scaled, false)
}
fn shifted<T>(mut values: Vec<T>) -> Vec<T> {
    let n = values.len();
    values.rotate_right(n / 2);
    values
}
pub(crate) fn spectrum_power(values: &[Complex64]) -> Vec<f64> {
    values.iter().map(|z| z.norm().powi(2)).collect()
}
pub(crate) fn peak_index(values: &[f64]) -> usize {
    let mut best = 0;
    for i in 1..values.len() {
        if values[i] > values[best] {
            best = i;
        }
    }
    best
}
fn lower_baseline(values: &[f64], fraction: f64, positive_required: bool) -> Result<f64, String> {
    let mut sorted = values.to_vec();
    sorted.sort_by(f64::total_cmp);
    let count = ((values.len() as f64 * fraction).ceil() as usize)
        .max(1)
        .min(values.len());
    let result = median(&sorted[..count]);
    require(
        result.is_finite() && (!positive_required || result > 0.0),
        "signal statistics have no finite positive baseline",
    )?;
    Ok(result)
}
fn connected_bounds(
    power: &[f64],
    peak: usize,
    threshold: f64,
    allowed_misses: usize,
) -> (usize, usize) {
    let (mut left, mut right) = (peak, peak);
    let mut misses = 0;
    for i in (0..peak).rev() {
        if power[i] >= threshold {
            left = i;
            misses = 0;
        } else {
            misses += 1;
            if misses > allowed_misses {
                break;
            }
        }
    }
    misses = 0;
    for (i, p) in power.iter().enumerate().skip(peak + 1) {
        if *p >= threshold {
            right = i;
            misses = 0;
        } else {
            misses += 1;
            if misses > allowed_misses {
                break;
            }
        }
    }
    (left, right)
}
fn coherence(values: &[Complex32]) -> f64 {
    let products: Vec<_> = values.windows(2).map(|p| p[1] * p[0].conj()).collect();
    let magnitudes: Vec<_> = products.iter().map(|z| z.norm() as f64).collect();
    let denominator = pairwise_sum(&magnitudes);
    if denominator <= 0.0 {
        return 0.0;
    }
    let real: Vec<_> = products.iter().map(|z| z.re as f64).collect();
    let imag: Vec<_> = products.iter().map(|z| z.im as f64).collect();
    Complex64::new(pairwise_sum(&real), pairwise_sum(&imag)).norm() / denominator
}
fn stable_rank(values: &[f64]) -> Vec<usize> {
    let mut order: Vec<_> = (0..values.len()).collect();
    order.sort_by(|a, b| values[*b].total_cmp(&values[*a]));
    order
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(default, deny_unknown_fields)]
pub struct SignalTriageConfig {
    pub sample_rate_hz: f64,
    pub window_seconds: f64,
    pub fft_size: usize,
    pub fft_slices_per_window: usize,
    pub noise_lower_fraction: f64,
    pub active_power_ratio: f64,
    pub active_fft_ratio: f64,
    pub active_fft_baseline_multiplier: f64,
}
impl Default for SignalTriageConfig {
    fn default() -> Self {
        Self {
            sample_rate_hz: 57_600.0,
            window_seconds: 1.0,
            fft_size: 4096,
            fft_slices_per_window: 4,
            noise_lower_fraction: 0.20,
            active_power_ratio: 1.5,
            active_fft_ratio: 20.0,
            active_fft_baseline_multiplier: 1.5,
        }
    }
}
pub fn triage_ci16le_file(path: &Path, c: &SignalTriageConfig) -> Result<Value, String> {
    let size = window_samples(c.sample_rate_hz, c.window_seconds)?;
    require(
        c.fft_size.is_power_of_two() && c.fft_size <= size,
        "analysis window must contain a positive power-of-two FFT",
    )?;
    require(
        (1..=4096).contains(&c.fft_slices_per_window),
        "FFT slice count must be in 1..4096",
    )?;
    require(
        positive(c.noise_lower_fraction) && c.noise_lower_fraction <= 0.5,
        "noise_lower_fraction must be in (0,0.5]",
    )?;
    require(
        [
            c.active_power_ratio,
            c.active_fft_ratio,
            c.active_fft_baseline_multiplier,
        ]
        .into_iter()
        .all(positive),
        "activity thresholds must be finite and positive",
    )?;
    let (mut file, identity, before) = source_file(path, 4)?;
    let samples = usize::try_from(identity.bytes / 4).map_err(|e| e.to_string())?;
    let count = samples / size;
    require(
        count > 0 && count <= MAX_WINDOWS,
        "capture must contain 1..100000 complete analysis windows",
    )?;
    let hann = hann32(c.fft_size);
    let bin = c.sample_rate_hz / c.fft_size as f64;
    let mut powers = Vec::with_capacity(count);
    let mut ratios = Vec::with_capacity(count);
    let mut frequencies = Vec::with_capacity(count);
    let mut bandwidths = Vec::with_capacity(count);
    for index in 0..count {
        let values = read_window(&mut file, index * size, size, false)?;
        let amplitudes: Vec<_> = values.iter().map(|z| z.re * z.re + z.im * z.im).collect();
        powers.push(mean32(&amplitudes) / (32768.0 * 32768.0));
        let (mut best_ratio, mut best_frequency, mut best_bandwidth) =
            (f64::NEG_INFINITY, 0.0, bin);
        for part in 0..c.fft_slices_per_window {
            let start = if c.fft_slices_per_window == 1 {
                0
            } else {
                ((size - c.fft_size) as f64 * part as f64 / (c.fft_slices_per_window - 1) as f64)
                    .floor() as usize
            };
            let segment = centered32(&values[start..start + c.fft_size]);
            let power = spectrum_power(&shifted(fft32(&segment, Some(&hann))));
            let floor = median(&power);
            let (ratio, peak, width) = if floor > 0.0 && floor.is_finite() {
                let peak = peak_index(&power);
                let bounds =
                    connected_bounds(&power, peak, (floor * 4.0).max(power[peak] / 4.0), 2);
                (
                    power[peak] / floor,
                    peak,
                    (bounds.1 - bounds.0 + 1) as f64 * bin,
                )
            } else {
                (0.0, 0, 0.0)
            };
            if ratio > best_ratio {
                best_ratio = ratio;
                best_frequency = (peak as f64 - (c.fft_size / 2) as f64) * bin;
                best_bandwidth = width;
            }
        }
        ratios.push(best_ratio);
        frequencies.push(best_frequency);
        bandwidths.push(best_bandwidth);
    }
    let noise = lower_baseline(&powers, c.noise_lower_fraction, true)?;
    let power_ratios: Vec<_> = powers.iter().map(|x| x / noise).collect();
    let fft_noise = lower_baseline(&ratios, c.noise_lower_fraction, false)?;
    let threshold = c
        .active_fft_ratio
        .max(fft_noise * c.active_fft_baseline_multiplier);
    let active: Vec<_> = power_ratios
        .iter()
        .zip(&ratios)
        .map(|(p, r)| *p >= c.active_power_ratio || *r >= threshold)
        .collect();
    let active_count = active.iter().filter(|b| **b).count();
    let fraction = active_count as f64 / count as f64;
    let (mut longest, mut current) = (0, 0);
    for value in active {
        current = if value { current + 1 } else { 0 };
        longest = longest.max(current);
    }
    let p90_power = quantile(&power_ratios, 0.9);
    let p90_fft = quantile(&ratios, 0.9);
    let max_fft = ratios[peak_index(&ratios)];
    let ranking = 2.0 * fraction.sqrt()
        + (p90_power - 1.0).max(0.0).ln_1p()
        + 0.75 * (p90_fft - 1.0).max(0.0).ln_1p()
        + 0.10 * (max_fft - 1.0).max(0.0).ln_1p();
    unchanged(&identity, &file, &before)?;
    Ok(
        json!({"file_size_bytes":identity.bytes,"sha256":identity.sha256,"complex_sample_count":samples,
        "duration_seconds":samples as f64/c.sample_rate_hz,"one_second_window_count":count,"trailing_sample_count":samples-count*size,
        "robust_noise_power_normalized":noise,"median_power_normalized":median(&powers),"p90_power_normalized":quantile(&powers,0.9),
        "median_power_to_noise_ratio":median(&power_ratios),"p90_power_to_noise_ratio":p90_power,"max_power_to_noise_ratio":power_ratios[peak_index(&power_ratios)],
        "fft_noise_peak_to_median_ratio":fft_noise,"median_fft_peak_to_median_ratio":median(&ratios),"p90_fft_peak_to_median_ratio":p90_fft,"max_fft_peak_to_median_ratio":max_fft,
        "median_peak_frequency_offset_hz":median(&frequencies),"p10_peak_frequency_offset_hz":quantile(&frequencies,0.1),"p90_peak_frequency_offset_hz":quantile(&frequencies,0.9),
        "median_occupied_bandwidth_hz":median(&bandwidths),"p90_occupied_bandwidth_hz":quantile(&bandwidths,0.9),
        "active_window_count":active_count,"active_window_fraction":fraction,"longest_active_run_seconds":longest as f64*c.window_seconds,"ranking_score":ranking}),
    )
}
pub fn rank_signal_triage(records: &[(u64, Value)]) -> Result<Vec<(u64, Value)>, String> {
    let mut ids = BTreeSet::new();
    for (id, row) in records {
        require(ids.insert(*id), "observation IDs must be unique")?;
        for key in [
            "ranking_score",
            "active_window_fraction",
            "max_fft_peak_to_median_ratio",
        ] {
            require(
                row[key].as_f64().is_some_and(f64::is_finite),
                "ranking metrics must be finite",
            )?;
        }
    }
    let mut output = records.to_vec();
    output.sort_by(|a, b| {
        for key in [
            "ranking_score",
            "active_window_fraction",
            "max_fft_peak_to_median_ratio",
        ] {
            let order = b.1[key]
                .as_f64()
                .unwrap()
                .total_cmp(&a.1[key].as_f64().unwrap());
            if !order.is_eq() {
                return order;
            }
        }
        a.0.cmp(&b.0)
    });
    Ok(output)
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(default, deny_unknown_fields)]
pub struct PhaseWindowSelectorConfig {
    pub sample_rate_hz: u32,
    pub analysis_window_seconds: f64,
    pub decoder_window_seconds: f64,
    pub top_k: usize,
    pub active_power_ratio: f64,
    pub active_coherence_ratio: f64,
    pub minimum_phase_coherence: f64,
    pub padding_seconds: f64,
    pub baseline_fraction: f64,
}
impl Default for PhaseWindowSelectorConfig {
    fn default() -> Self {
        Self {
            sample_rate_hz: 57_600,
            analysis_window_seconds: 1.0,
            decoder_window_seconds: 5.0,
            top_k: 64,
            active_power_ratio: 20.0,
            active_coherence_ratio: 2.0,
            minimum_phase_coherence: 0.60,
            padding_seconds: 2.0,
            baseline_fraction: 0.25,
        }
    }
}
pub fn select_phase_windows_cf32(
    path: &Path,
    c: &PhaseWindowSelectorConfig,
) -> Result<Value, String> {
    let size = window_samples(c.sample_rate_hz as f64, c.analysis_window_seconds)?;
    require(
        positive(c.decoder_window_seconds) && c.decoder_window_seconds >= c.analysis_window_seconds,
        "decoder window must not be shorter than analysis window",
    )?;
    require(
        c.active_power_ratio.is_finite()
            && c.active_power_ratio >= 1.0
            && c.active_coherence_ratio.is_finite()
            && c.active_coherence_ratio >= 1.0,
        "relative thresholds must be finite and at least one",
    )?;
    require(
        c.minimum_phase_coherence.is_finite()
            && (0.0..=1.0).contains(&c.minimum_phase_coherence)
            && nonnegative(c.padding_seconds),
        "invalid coherence or padding",
    )?;
    require(
        positive(c.baseline_fraction) && c.baseline_fraction <= 0.5,
        "baseline_fraction must be in (0,0.5]",
    )?;
    let (mut file, identity, before) = source_file(path, 8)?;
    let samples = usize::try_from(identity.bytes / 8).map_err(|e| e.to_string())?;
    let count = samples / size;
    require(
        (2..=MAX_WINDOWS).contains(&count),
        "capture must contain 2..100000 analysis windows",
    )?;
    let mut powers = Vec::with_capacity(count);
    let mut coherences = Vec::with_capacity(count);
    for i in 0..count {
        let values = read_window(&mut file, i * size, size, true)?;
        let power: Vec<_> = values.iter().map(|z| z.norm().powi(2)).collect();
        powers.push(mean32(&power));
        coherences.push(coherence(&values));
    }
    require(
        powers.iter().chain(&coherences).all(|x| x.is_finite()),
        "CF32 derived power/coherence statistics are nonfinite",
    )?;
    let baseline = lower_baseline(&powers, c.baseline_fraction, true)?;
    let coherence_baseline = lower_baseline(
        &coherences
            .iter()
            .map(|x| x.max(f64::EPSILON))
            .collect::<Vec<_>>(),
        c.baseline_fraction,
        true,
    )?;
    let power_ratios: Vec<_> = powers.iter().map(|x| x / baseline).collect();
    let coherence_ratios: Vec<_> = coherences.iter().map(|x| x / coherence_baseline).collect();
    let threshold = c
        .minimum_phase_coherence
        .max(coherence_baseline * c.active_coherence_ratio);
    let mut active: Vec<_> = power_ratios
        .iter()
        .zip(&coherences)
        .map(|(p, z)| *p >= c.active_power_ratio || *z >= threshold)
        .collect();
    let scores: Vec<_> = power_ratios
        .iter()
        .zip(&coherence_ratios)
        .map(|(p, z)| (p - 1.0).max(0.0).ln_1p().max((z - 1.0).max(0.0).ln_1p()))
        .collect();
    require(
        threshold.is_finite()
            && power_ratios
                .iter()
                .chain(&coherence_ratios)
                .chain(&scores)
                .all(|x| x.is_finite()),
        "CF32 derived ratio/score statistics are nonfinite",
    )?;
    for index in stable_rank(&scores).into_iter().take(c.top_k) {
        active[index] = true;
    }
    let padding = (c.padding_seconds / c.analysis_window_seconds)
        .ceil()
        .min(count as f64) as usize;
    let mut padded = active.clone();
    for (i, keep) in active.iter().enumerate() {
        if *keep {
            padded[i.saturating_sub(padding)..=(i + padding).min(count - 1)].fill(true);
        }
    }
    let duration = samples as f64 / c.sample_rate_hz as f64;
    let maximum_start = (duration - c.decoder_window_seconds).max(0.0);
    let mut starts: Vec<_> = padded
        .iter()
        .enumerate()
        .filter(|(_, b)| **b)
        .map(|(i, _)| {
            (i as f64 * c.analysis_window_seconds - c.padding_seconds)
                .max(0.0)
                .min(maximum_start)
        })
        .collect();
    starts.sort_by(f64::total_cmp);
    starts.dedup();
    let rows:Vec<_>=(0..count).map(|i|json!({"window_index":i,"start_seconds":i as f64*c.analysis_window_seconds,
        "mean_power":powers[i],"power_ratio":power_ratios[i],"lag1_phase_coherence":coherences[i],"coherence_ratio":coherence_ratios[i],"score":scores[i],
        "selected_before_padding":active[i],"selected_after_padding":padded[i]})).collect();
    unchanged(&identity, &file, &before)?;
    Ok(
        json!({"schema_version":"phase-window-selector-v1","input_path":identity.path,"input_sha256":identity.sha256,
        "input_complex_samples":samples,"input_duration_seconds":duration,"reference_paths_available_to_selector":false,"reference_hashes_available_to_selector":false,
        "event_timestamps_available_to_selector":false,"config":c,"baselines":{"mean_power":baseline,"lag1_phase_coherence":coherence_baseline,"active_coherence_threshold":threshold},
        "analysis_window_count":count,"selected_before_padding_count":active.iter().filter(|b|**b).count(),"selected_after_padding_count":padded.iter().filter(|b|**b).count(),
        "decoder_window_start_count":starts.len(),"decoder_window_starts_seconds":starts,"windows":rows}),
    )
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(default, deny_unknown_fields)]
pub struct BurstWindowSelectorConfig {
    pub sample_rate_hz: u32,
    pub analysis_window_seconds: f64,
    pub decoder_window_seconds: f64,
    pub decoder_window_lead_seconds: f64,
    pub signal_window_limit: usize,
    pub change_window_limit: usize,
    pub burst_window_limit: usize,
    pub signal_nms_seconds: f64,
    pub burst_nms_seconds: f64,
    pub coverage_hop_seconds: Option<f64>,
    pub baseline_fraction: f64,
    pub spectral_fft_size: usize,
    pub persistence_subwindows: usize,
    pub burst_fft_size: usize,
    pub burst_hop_samples: usize,
    pub burst_band_width_bins: Vec<usize>,
    pub maximum_analysis_windows: usize,
}
impl Default for BurstWindowSelectorConfig {
    fn default() -> Self {
        Self {
            sample_rate_hz: 57_600,
            analysis_window_seconds: 0.5,
            decoder_window_seconds: 5.0,
            decoder_window_lead_seconds: 0.25,
            signal_window_limit: 16,
            change_window_limit: 16,
            burst_window_limit: 32,
            signal_nms_seconds: 2.0,
            burst_nms_seconds: 5.0,
            coverage_hop_seconds: Some(4.0),
            baseline_fraction: 0.25,
            spectral_fft_size: 2048,
            persistence_subwindows: 8,
            burst_fft_size: 512,
            burst_hop_samples: 256,
            burst_band_width_bins: vec![4, 8, 16, 32, 64, 128],
            maximum_analysis_windows: 100_000,
        }
    }
}
fn rank_with_nms(scores: &[f64], limit: usize, seconds: f64, nms: f64) -> Vec<usize> {
    let mut selected = Vec::new();
    if limit == 0 {
        return selected;
    }
    for index in stable_rank(scores) {
        let timestamp = index as f64 * seconds;
        if selected
            .iter()
            .any(|kept| (timestamp - *kept as f64 * seconds).abs() < nms)
        {
            continue;
        }
        selected.push(index);
        if selected.len() >= limit {
            break;
        }
    }
    selected
}
fn coverage_starts(duration: f64, c: &BurstWindowSelectorConfig) -> Result<Vec<f64>, String> {
    let Some(hop) = c.coverage_hop_seconds else {
        return Ok(Vec::new());
    };
    let maximum = (duration - c.decoder_window_seconds).max(0.0);
    require(
        (maximum / hop).ceil() <= MAX_WINDOWS as f64,
        "coverage lane exceeds 100000-window bound",
    )?;
    let mut starts = Vec::new();
    let mut current = 0.0;
    while current < maximum {
        starts.push(current);
        current += hop;
    }
    starts.push(maximum);
    starts.dedup();
    Ok(starts)
}
fn coverage_audit(starts: &[f64], duration: f64, seconds: f64) -> Value {
    if starts.is_empty() {
        return json!({"enabled":false,"entire_capture_covered":false,"maximum_uncovered_gap_seconds":null});
    }
    let mut cursor: f64 = 0.0;
    let mut gap: f64 = 0.0;
    for start in starts {
        gap = gap.max((start - cursor).max(0.0));
        cursor = cursor.max(start + seconds);
    }
    gap = gap.max((duration - cursor).max(0.0));
    json!({"enabled":true,"entire_capture_covered":gap<=1e-9,"maximum_uncovered_gap_seconds":gap})
}
fn localized_burst(
    values: &[Complex32],
    c: &BurstWindowSelectorConfig,
    taper: &[f32],
) -> (f64, f64) {
    let width = c.burst_fft_size;
    let mut frames: Vec<Vec<f64>> = (0..=values.len() - width)
        .step_by(c.burst_hop_samples)
        .map(|start| {
            spectrum_power(&shifted(fft32(&values[start..start + width], Some(taper))))
                .into_iter()
                .map(|p| (p + 1.0).ln())
                .collect()
        })
        .collect();
    let mut baselines = vec![0.0; width];
    for frequency in 0..width {
        baselines[frequency] = median(
            &frames
                .iter()
                .map(|frame| frame[frequency])
                .collect::<Vec<_>>(),
        );
    }
    let mut sums = Vec::with_capacity(frames.len());
    let mut cumulative = Vec::with_capacity(frames.len());
    for frame in &mut frames {
        for (p, b) in frame.iter_mut().zip(&baselines) {
            *p -= b;
        }
        let center = median(frame);
        for p in frame.iter_mut() {
            *p = (*p - center).max(0.0);
        }
        sums.push(pairwise_sum(frame));
        let mut row = vec![0.0; frame.len() + 1];
        for (i, p) in frame.iter().enumerate() {
            row[i + 1] = row[i] + p;
        }
        cumulative.push(row);
    }
    let (mut localized, mut fraction) = (0.0_f64, 0.0_f64);
    for band in &c.burst_band_width_bins {
        let per_frame: Vec<_> = cumulative
            .iter()
            .map(|row| {
                (0..row.len() - band)
                    .map(|i| row[i + band] - row[i])
                    .fold(f64::NEG_INFINITY, f64::max)
            })
            .collect();
        let fractions: Vec<_> = per_frame
            .iter()
            .zip(&sums)
            .map(|(p, total)| p / (total + 1e-12))
            .collect();
        localized = localized.max(quantile(&per_frame, 0.85) / (*band as f64).sqrt());
        fraction = fraction.max(quantile(&fractions, 0.85));
    }
    (localized, fraction)
}
pub fn select_protocol_neutral_ci16_windows(
    path: &Path,
    c: &BurstWindowSelectorConfig,
) -> Result<Value, String> {
    let size = window_samples(c.sample_rate_hz as f64, c.analysis_window_seconds)?;
    require(
        positive(c.decoder_window_seconds) && c.decoder_window_seconds >= c.analysis_window_seconds,
        "decoder window cannot be shorter than analysis window",
    )?;
    require(
        nonnegative(c.decoder_window_lead_seconds)
            && nonnegative(c.signal_nms_seconds)
            && nonnegative(c.burst_nms_seconds),
        "lead and NMS durations must be finite and nonnegative",
    )?;
    require(
        c.coverage_hop_seconds
            .is_none_or(|x| positive(x) && x <= c.decoder_window_seconds),
        "coverage hop must be positive and no greater than decoder window",
    )?;
    require(
        positive(c.baseline_fraction) && c.baseline_fraction <= 0.5,
        "baseline fraction must be in (0,0.5]",
    )?;
    require(
        c.spectral_fft_size >= 16
            && c.spectral_fft_size.is_power_of_two()
            && c.spectral_fft_size <= size,
        "spectral FFT must be a power of two >=16 fitting analysis window",
    )?;
    require(
        c.persistence_subwindows >= 2 && c.persistence_subwindows <= size,
        "persistence subwindows must be >=2 and fit analysis window",
    )?;
    require(
        (2..=MAX_WINDOWS).contains(&c.maximum_analysis_windows),
        "maximum analysis windows must be in 2..100000",
    )?;
    require(
        c.burst_fft_size >= 16
            && c.burst_fft_size <= size
            && c.burst_hop_samples >= 1
            && c.burst_hop_samples <= c.burst_fft_size,
        "burst FFT/hop must fit analysis window",
    )?;
    require(
        !c.burst_band_width_bins.is_empty()
            && c.burst_band_width_bins
                .iter()
                .all(|x| *x >= 2 && *x <= c.burst_fft_size / 2)
            && c.burst_band_width_bins.windows(2).all(|x| x[0] < x[1]),
        "burst width bank must be sorted unique and fit FFT",
    )?;
    let frame_count = (size - c.burst_fft_size) / c.burst_hop_samples + 1;
    require(
        frame_count
            .checked_mul(c.burst_fft_size)
            .is_some_and(|x| x <= MAX_STFT_VALUES),
        "burst STFT exceeds 4194304-cell workspace bound",
    )?;
    let (mut file, identity, before) = source_file(path, 4)?;
    let samples = usize::try_from(identity.bytes / 4).map_err(|e| e.to_string())?;
    let count = samples / size;
    require(
        count >= 2 && count <= c.maximum_analysis_windows,
        "capture exceeds bounded 2..maximum analysis-window limit",
    )?;
    let mut powers = Vec::with_capacity(count);
    let mut coherences = Vec::with_capacity(count);
    let mut persistence = Vec::with_capacity(count);
    let mut entropies = Vec::with_capacity(count);
    let mut peak_ratios = Vec::with_capacity(count);
    let mut clips = Vec::with_capacity(count);
    let mut burst_scores = Vec::with_capacity(count);
    let mut burst_excess = Vec::with_capacity(count);
    let mut burst_fractions = Vec::with_capacity(count);
    let taper = hann32(c.spectral_fft_size);
    let burst_taper = hann32(c.burst_fft_size);
    for index in 0..count {
        let values = read_window(&mut file, index * size, size, false)?;
        let amplitudes: Vec<_> = values.iter().map(|z| z.re * z.re + z.im * z.im).collect();
        powers.push(mean32(&amplitudes));
        coherences.push(coherence(&values));
        let endpoints = values
            .iter()
            .map(|z| {
                [z.re, z.im]
                    .into_iter()
                    .filter(|x| *x == -32768.0 || *x == 32767.0)
                    .count()
            })
            .sum::<usize>();
        clips.push(endpoints as f64 / (2 * size) as f64);
        let mut subs = Vec::with_capacity(c.persistence_subwindows);
        let mut offset = 0;
        for index in 0..c.persistence_subwindows {
            let length = size / c.persistence_subwindows
                + usize::from(index < size % c.persistence_subwindows);
            subs.push(mean32(&amplitudes[offset..offset + length]));
            offset += length;
        }
        persistence.push(quantile(&subs, 0.25) / quantile(&subs, 0.75).max(f64::EPSILON));
        let middle = (size - c.spectral_fft_size) / 2;
        let segment = centered32(&values[middle..middle + c.spectral_fft_size]);
        let spectrum = spectrum_power(&shifted(fft32(&segment, Some(&taper))));
        let total = pairwise_sum(&spectrum);
        if total <= 0.0 {
            entropies.push(0.0);
            peak_ratios.push(0.0);
        } else {
            let terms: Vec<_> = spectrum
                .iter()
                .map(|x| x / total)
                .filter(|x| *x > 0.0)
                .map(|p| p * p.ln())
                .collect();
            entropies.push(-pairwise_sum(&terms) / (c.spectral_fft_size as f64).ln());
            peak_ratios.push(spectrum[peak_index(&spectrum)] / median(&spectrum).max(f64::EPSILON));
        }
        let (excess, fraction) = localized_burst(&values, c, &burst_taper);
        burst_excess.push(excess);
        burst_fractions.push(fraction);
        burst_scores.push(excess * fraction);
    }
    let baseline = lower_baseline(
        &powers
            .iter()
            .map(|x| x.max(f64::EPSILON))
            .collect::<Vec<_>>(),
        c.baseline_fraction,
        true,
    )?;
    let ratios: Vec<_> = powers.iter().map(|x| x / baseline).collect();
    let signal_scores: Vec<_> = (0..count)
        .map(|i| {
            (ratios[i] - 1.0).max(0.0).ln_1p()
                * (1.0 - coherences[i]).max(0.0).sqrt()
                * (0.25 + entropies[i])
                * (0.25 + persistence[i].clamp(0.0, 1.0))
        })
        .collect();
    let features: Vec<[f64; 5]> = (0..count)
        .map(|i| {
            [
                ratios[i].max(0.0).ln_1p(),
                coherences[i],
                entropies[i],
                peak_ratios[i].max(0.0).ln_1p(),
                persistence[i],
            ]
        })
        .collect();
    let mut scales = [0.0; 5];
    for j in 0..5 {
        let column: Vec<_> = features.iter().map(|row| row[j]).collect();
        let center = median(&column);
        scales[j] = median(
            &column
                .iter()
                .map(|v| (v - center).abs())
                .collect::<Vec<_>>(),
        )
        .max(1e-6);
    }
    let mut changes = vec![0.0_f64; count];
    for i in 0..count - 1 {
        let terms: Vec<_> = (0..5)
            .map(|j| ((features[i + 1][j] - features[i][j]).abs() / scales[j]).powi(2))
            .collect();
        let score = pairwise_sum(&terms).sqrt();
        changes[i] = changes[i].max(score);
        changes[i + 1] = changes[i + 1].max(score);
    }
    let signal = rank_with_nms(
        &signal_scores,
        c.signal_window_limit,
        c.analysis_window_seconds,
        c.signal_nms_seconds,
    );
    let change = rank_with_nms(
        &changes,
        c.change_window_limit,
        c.analysis_window_seconds,
        c.signal_nms_seconds,
    );
    let burst = rank_with_nms(
        &burst_scores,
        c.burst_window_limit,
        c.analysis_window_seconds,
        c.burst_nms_seconds,
    );
    let duration = samples as f64 / c.sample_rate_hz as f64;
    let maximum = (duration - c.decoder_window_seconds).max(0.0);
    let coverage = coverage_starts(duration, c)?;
    let mut candidates: BTreeMap<i64, Value> = BTreeMap::new();
    let mut add = |index: usize, start: f64, lane: &str| -> Result<(), String> {
        let bounded = start.max(0.0).min(maximum);
        require(
            bounded * 1e12 < i64::MAX as f64,
            "candidate timestamp exceeds precise key bound",
        )?;
        let key = (bounded * 1e12).round_ties_even() as i64;
        let row=candidates.entry(key).or_insert_with(||json!({"decoder_start_seconds":bounded,"analysis_index":index,
            "analysis_start_seconds":index as f64*c.analysis_window_seconds,"lanes":[],"mean_power":powers[index],"power_ratio":ratios[index],
            "lag1_phase_coherence":coherences[index],"within_window_power_persistence":persistence[index],"spectral_entropy":entropies[index],
            "spectral_peak_to_median_ratio":peak_ratios[index],"endpoint_clip_fraction":clips[index],"signal_score":signal_scores[index],"change_score":changes[index],
            "burst_score":burst_scores[index],"burst_localized_log_excess":burst_excess[index],"burst_localized_excess_fraction":burst_fractions[index]}));
        let lanes = row["lanes"].as_array_mut().unwrap();
        if !lanes.iter().any(|x| x == lane) {
            lanes.push(json!(lane));
        }
        Ok(())
    };
    for (lane, indices) in [("signal", &signal), ("change", &change), ("burst", &burst)] {
        for index in indices {
            add(
                *index,
                *index as f64 * c.analysis_window_seconds - c.decoder_window_lead_seconds,
                lane,
            )?;
        }
    }
    for start in &coverage {
        let center = start + 0.5 * c.decoder_window_seconds;
        let index = ((center / c.analysis_window_seconds) as usize).min(count - 1);
        add(index, *start, "coverage")?;
    }
    let mut ordered: Vec<_> = candidates.into_values().collect();
    for row in &mut ordered {
        row["lanes"]
            .as_array_mut()
            .unwrap()
            .sort_by(|a, b| a.as_str().cmp(&b.as_str()));
    }
    unchanged(&identity, &file, &before)?;
    Ok(
        json!({"schema_version":"protocol-neutral-window-selection-v2","selector_version":SELECTOR_VERSION,
        "input_path":identity.path,"input_size_bytes":identity.bytes,"input_duration_seconds":duration,
        "reference_paths_available_to_selector":false,"frame_bytes_available_to_selector":false,"event_timestamps_available_to_selector":false,"mission_metadata_available_to_selector":false,
        "config":c,"analysis_window_count":count,"lane_counts_before_merge":{"signal":signal.len(),"change":change.len(),"burst":burst.len(),"coverage":coverage.len()},
        "selected_decoder_window_count":ordered.len(),"coverage_audit":coverage_audit(&coverage,duration,c.decoder_window_seconds),"selected_windows":ordered}),
    )
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(default, deny_unknown_fields)]
pub struct WaveformRoutingConfig {
    pub sample_rate_hz: f64,
    pub window_seconds: f64,
    pub strongest_window_count: usize,
    pub psd_fft_size: usize,
    pub symbol_rate_bank_baud: Vec<u32>,
}
impl Default for WaveformRoutingConfig {
    fn default() -> Self {
        Self {
            sample_rate_hz: 57_600.0,
            window_seconds: 1.0,
            strongest_window_count: 8,
            psd_fft_size: 32_768,
            symbol_rate_bank_baud: vec![50, 100, 200, 300, 400, 600, 1200, 2400, 4800, 9600, 19200],
        }
    }
}
fn roll<T>(mut values: Vec<T>, shift: isize) -> Vec<T> {
    let amount = shift.rem_euclid(values.len() as isize) as usize;
    values.rotate_right(amount);
    values
}
fn complex_mean(values: &[Complex64]) -> Complex64 {
    let real: Vec<_> = values.iter().map(|z| z.re).collect();
    let imag: Vec<_> = values.iter().map(|z| z.im).collect();
    Complex64::new(mean(&real), mean(&imag))
}
fn centered_phase(values: &[Complex64]) -> Vec<f64> {
    let phase: Vec<_> = values
        .windows(2)
        .map(|p| (p[1] * p[0].conj()).arg())
        .collect();
    let unit: Vec<_> = phase
        .iter()
        .map(|p| Complex64::from_polar(1.0, *p))
        .collect();
    let center = complex_mean(&unit).arg();
    phase
        .iter()
        .map(|p| Complex64::from_polar(1.0, p - center).arg())
        .collect()
}
fn two_cluster_improvement(values: &[f64]) -> f64 {
    let center = mean(values);
    let total = pairwise_sum(
        &values
            .iter()
            .map(|x| (x - center).powi(2))
            .collect::<Vec<_>>(),
    );
    if total <= 1e-18 {
        return 0.0;
    }
    let mut centers = [quantile(values, 0.25), quantile(values, 0.75)];
    let mut labels = vec![0; values.len()];
    for _ in 0..16 {
        for (label, value) in labels.iter_mut().zip(values) {
            *label = usize::from((value - centers[1]).abs() < (value - centers[0]).abs());
        }
        let mut updated = centers;
        for (group, updated_center) in updated.iter_mut().enumerate() {
            let selected: Vec<_> = values
                .iter()
                .zip(&labels)
                .filter(|(_, l)| **l == group)
                .map(|(v, _)| *v)
                .collect();
            if !selected.is_empty() {
                *updated_center = mean(&selected);
            }
        }
        if (0..2).all(|i| (updated[i] - centers[i]).abs() <= 1e-8 + 1e-5 * centers[i].abs()) {
            break;
        }
        centers = updated;
    }
    let residual = pairwise_sum(
        &values
            .iter()
            .zip(&labels)
            .map(|(v, l)| (v - centers[*l]).powi(2))
            .collect::<Vec<_>>(),
    );
    (1.0 - residual / total).clamp(0.0, 1.0)
}
fn line_snr_db(windows: &mut [Vec<f64>], sample_rate: f64, rate: u32) -> f64 {
    let length = windows[0].len();
    let bins = length / 2 + 1;
    let mut power = vec![0.0; bins];
    for values in windows.iter_mut() {
        let center = mean(values);
        for value in values.iter_mut() {
            *value -= center;
        }
        let tapered: Vec<_> = values
            .iter()
            .enumerate()
            .map(|(i, v)| {
                Complex64::new(
                    v * (0.5 - 0.5 * (2.0 * PI * i as f64 / (length - 1) as f64).cos()),
                    0.0,
                )
            })
            .collect();
        let spectrum = fft(&tapered, false);
        for (p, z) in power.iter_mut().zip(&spectrum) {
            *p += z.norm().powi(2);
        }
    }
    for p in &mut power {
        *p /= windows.len() as f64;
    }
    let mut target = 0;
    let mut distance = f64::INFINITY;
    for i in 0..bins {
        let d = (i as f64 * sample_rate / length as f64 - rate as f64).abs();
        if d < distance {
            target = i;
            distance = d;
        }
    }
    let half = 12.max((0.02 * rate as f64).round_ties_even() as usize);
    let lower = target.saturating_sub(half).max(1);
    let upper = (target + half + 1).min(bins);
    if upper.saturating_sub(lower) < 5 {
        return 0.0;
    }
    let signal_lower = lower.max(target.saturating_sub(2));
    let signal_upper = upper.min(target + 3);
    let signal = power[signal_lower..signal_upper]
        .iter()
        .copied()
        .fold(f64::NEG_INFINITY, f64::max);
    let local: Vec<_> = (lower..upper)
        .filter(|i| *i < signal_lower || *i >= signal_upper)
        .map(|i| power[i])
        .collect();
    let noise = median(&local);
    if signal <= 0.0 || noise <= 0.0 {
        0.0
    } else {
        10.0 * (signal / noise).log10()
    }
}
pub(crate) fn general4(value: f64) -> String {
    if value == 0.0 {
        return if value.is_sign_negative() {
            "-0".into()
        } else {
            "0".into()
        };
    }
    // Python's general format chooses notation after rounding to four
    // significant figures, including carry across a power-of-ten boundary.
    let scientific = format!("{value:.3e}");
    let (mantissa, power) = scientific.split_once('e').unwrap();
    let exponent: i32 = power.parse().unwrap();
    if !(-4..4).contains(&exponent) {
        let mantissa = mantissa.trim_end_matches('0').trim_end_matches('.');
        format!(
            "{mantissa}e{}{power:02}",
            if exponent >= 0 { "+" } else { "-" },
            power = exponent.abs()
        )
    } else {
        let precision = (3 - exponent).max(0) as usize;
        let raw = format!("{value:.precision$}");
        if raw.contains('.') {
            raw.trim_end_matches('0').trim_end_matches('.').to_string()
        } else {
            raw
        }
    }
}
#[allow(
    clippy::too_many_arguments,
    reason = "Mirrors the frozen Python evidence contract; keep each named metric and expression unchanged."
)]
fn routing_hypotheses(
    cv: f64,
    papr: f64,
    concentration: f64,
    near: f64,
    cluster: f64,
    occupied: f64,
    connected: f64,
    fraction: f64,
    clock_db: f64,
) -> Vec<Value> {
    let clamp = |x: f64| x.clamp(0.0, 1.0);
    let constant = clamp((0.45 - cv) / 0.40);
    let narrow = clamp((1200.0 - occupied) / 1100.0)
        .max(clamp((500.0 - connected) / 450.0) * clamp((fraction - 0.30) / 0.60));
    let wide = clamp((occupied - 600.0) / 5000.0);
    let clock = clamp((clock_db - 4.0) / 12.0);
    let cw = (0.50 + 0.50 * constant) * narrow * clamp((concentration - 0.85) / 0.15);
    let fsk = constant * wide * (1.0 - narrow) * (0.55 * cluster + 0.45 * clock);
    let psk =
        constant * wide * (1.0 - narrow) * (0.60 * clamp((near - 0.50) / 0.45) + 0.40 * clock);
    let analog = 0.70 * wide * (1.0 - narrow) * clamp((cv - 0.12) / 0.45) * (1.0 - 0.70 * clock);
    let unknown = 0.20_f64.max(0.55 * (1.0 - cw.max(fsk).max(psk).max(analog)));
    let evidence: Vec<_> = [
        ("envelope_cv", cv),
        ("p99_power_to_mean", papr),
        ("occupied_bandwidth_99_hz", occupied),
        ("peak_connected_bandwidth_hz", connected),
        ("dominant_connected_excess_fraction", fraction),
        ("phase_increment_concentration", concentration),
        ("best_clock_line_snr_db", clock_db),
    ]
    .into_iter()
    .map(|(key, v)| format!("{key}={}", general4(v)))
    .collect();
    let mut scores = vec![
        ("continuous_carrier_or_cw_like", cw),
        ("fsk_like", fsk),
        ("psk_like", psk),
        ("analog_like", analog),
        ("unknown", unknown),
    ];
    scores.sort_by(|a, b| b.1.total_cmp(&a.1).then_with(|| a.0.cmp(b.0)));
    scores.into_iter().map(|(kind,score)|json!({"signal_type":kind,"confidence":clamp(score),"evidence":evidence})).collect()
}
pub fn route_ci16le_waveform(path: &Path, c: &WaveformRoutingConfig) -> Result<Value, String> {
    let size = window_samples(c.sample_rate_hz, c.window_seconds)?;
    require(
        size >= 16_381,
        "routing preview requires at least 16381 samples per window",
    )?;
    require(
        c.psd_fft_size >= 16 && c.psd_fft_size.is_power_of_two() && c.psd_fft_size <= size,
        "routing PSD FFT must be a power of two >=16 fitting one window",
    )?;
    require(
        c.strongest_window_count > 0,
        "strongest window count must be positive",
    )?;
    let rates: BTreeSet<_> = c.symbol_rate_bank_baud.iter().copied().collect();
    require(
        rates.len() == c.symbol_rate_bank_baud.len()
            && rates.contains(&9600)
            && rates.iter().all(|r| (50..=19200).contains(r)),
        "routing bank must contain unique 50..19200 baud rates including 9600",
    )?;
    let (mut file, identity, before) = source_file(path, 4)?;
    let samples = usize::try_from(identity.bytes / 4).map_err(|e| e.to_string())?;
    let count = samples / size;
    require(
        (1..=MAX_WINDOWS).contains(&count),
        "capture must contain 1..100000 routing windows",
    )?;
    require(
        c.strongest_window_count
            .min(count)
            .checked_mul(c.psd_fft_size)
            .is_some_and(|x| x <= MAX_STFT_VALUES),
        "selected routing spectra exceed workspace bound",
    )?;
    let mut powers = Vec::with_capacity(count);
    let mut peaks = Vec::with_capacity(count);
    let preview_hann = hann32(4096);
    for i in 0..count {
        let raw = read_window(&mut file, i * size, size, false)?;
        let downsampled: Vec<_> = raw.into_iter().step_by(4).collect();
        powers.push(mean32(
            &downsampled
                .iter()
                .map(|z| z.re * z.re + z.im * z.im)
                .collect::<Vec<_>>(),
        ));
        let preview = centered32(&downsampled[..4096]);
        let spectrum = spectrum_power(&fft32(&preview, Some(&preview_hann)));
        let floor = median(&spectrum);
        peaks.push(if floor > 0.0 {
            spectrum[peak_index(&spectrum)] / floor
        } else {
            0.0
        });
    }
    let power_floor = quantile(&powers, 0.20).max(1e-12);
    let peak_floor = quantile(&peaks, 0.20).max(1e-12);
    let score: Vec<_> = powers
        .iter()
        .zip(&peaks)
        .map(|(p, k)| (p / power_floor).ln_1p() + (k / peak_floor).ln_1p())
        .collect();
    let mut chosen: Vec<_> = stable_rank(&score)
        .into_iter()
        .take(c.strongest_window_count)
        .collect();
    chosen.sort_unstable();
    let taper = hann32(c.psd_fft_size);
    let center = c.psd_fft_size / 2;
    let bin = c.sample_rate_hz / c.psd_fft_size as f64;
    let mut raw_spectra = Vec::with_capacity(chosen.len());
    let mut aligned_psd = Vec::with_capacity(chosen.len());
    let mut peak_ratios = Vec::with_capacity(chosen.len());
    for index in &chosen {
        let values = centered32(&read_window(&mut file, *index * size, size, false)?);
        let middle = (size - c.psd_fft_size) / 2;
        let segment = &values[middle..middle + c.psd_fft_size];
        let raw = shifted(fft32(segment, None));
        let power = spectrum_power(&shifted(fft32(segment, Some(&taper))));
        let floor = median(&power);
        let peak = peak_index(&power);
        let shift = center as isize - peak as isize;
        peak_ratios.push(power[peak] / floor.max(1e-20));
        raw_spectra.push(roll(raw, shift));
        aligned_psd.push(roll(power, shift));
    }
    let mut average_psd = vec![0.0; c.psd_fft_size];
    for row in &aligned_psd {
        for (a, p) in average_psd.iter_mut().zip(row) {
            *a += p;
        }
    }
    for p in &mut average_psd {
        *p /= chosen.len() as f64;
    }
    let floor = median(&average_psd);
    let peak = peak_index(&average_psd);
    let excess: Vec<_> = average_psd
        .iter()
        .map(|p| (p - 4.0 * floor).max(0.0))
        .collect();
    let total = pairwise_sum(&excess);
    let occupied = if total > 0.0 {
        let mut cumulative = 0.0;
        let mut lower = None;
        let mut upper = excess.len();
        for (i, p) in excess.iter().enumerate() {
            cumulative += p;
            let normalized = cumulative / total;
            if lower.is_none() && normalized >= 0.005 {
                lower = Some(i);
            }
            if normalized >= 0.995 {
                upper = i;
                break;
            }
        }
        (upper - lower.unwrap_or(excess.len()) + 1) as f64 * bin
    } else {
        0.0
    };
    let bounds = connected_bounds(
        &average_psd,
        peak,
        (floor * 4.0).max(average_psd[peak] / 100.0),
        4,
    );
    let connected = (bounds.1 - bounds.0 + 1) as f64 * bin;
    let fraction = if total > 0.0 {
        pairwise_sum(&excess[bounds.0..=bounds.1]) / total
    } else {
        0.0
    };
    let peak_ratio = median(&peak_ratios);
    let narrow = connected <= 500.0 && peak_ratio >= 20.0 && fraction >= 0.50;
    let half_band = if narrow {
        250.0
    } else {
        12_000.0_f64.min(250.0_f64.max(occupied * 1.10).max(connected * 2.0))
    };
    // A very small positive sample rate can make the requested half-band
    // wider than usize::MAX bins. Clipping to the existing FFT is exact.
    let half_bins = ((half_band / bin).ceil().min(c.psd_fft_size as f64) as usize).max(1);
    let left = center.saturating_sub(half_bins);
    let right = (center + half_bins + 1).min(c.psd_fft_size);
    let mut envelope_windows = Vec::new();
    let mut phase_windows = Vec::new();
    let mut phase_transition = Vec::new();
    let mut envelope_transition = Vec::new();
    for mut spectrum in raw_spectra {
        for (i, z) in spectrum.iter_mut().enumerate() {
            if i < left || i >= right {
                *z = Complex64::new(0.0, 0.0);
            }
        }
        spectrum.rotate_left(c.psd_fft_size / 2);
        let filtered = fft(&spectrum, true);
        let trim = 512.min(filtered.len() / 16);
        let filtered = if trim > 0 {
            &filtered[trim..filtered.len() - trim]
        } else {
            &filtered
        };
        let envelope: Vec<_> = filtered.iter().map(|z| z.norm()).collect();
        let phase = centered_phase(filtered);
        phase_transition.push(phase.windows(2).map(|p| (p[1] - p[0]).abs()).collect());
        envelope_transition.push(envelope.windows(2).map(|p| (p[1] - p[0]).abs()).collect());
        phase_windows.push(phase);
        envelope_windows.push(envelope);
    }
    let envelope: Vec<_> = envelope_windows.into_iter().flatten().collect();
    let envelope_mean = mean(&envelope);
    let cv = mean(
        &envelope
            .iter()
            .map(|x| (x - envelope_mean).powi(2))
            .collect::<Vec<_>>(),
    )
    .sqrt()
        / envelope_mean.max(1e-12);
    let robust =
        (quantile(&envelope, 0.9) - quantile(&envelope, 0.1)) / median(&envelope).max(1e-12);
    let power: Vec<_> = envelope.iter().map(|v| v * v).collect();
    let papr = quantile(&power, 0.99) / mean(&power).max(1e-12);
    let phase: Vec<_> = phase_windows.into_iter().flatten().collect();
    let unit: Vec<_> = phase
        .iter()
        .map(|p| Complex64::from_polar(1.0, *p))
        .collect();
    let concentration = complex_mean(&unit).norm();
    let phase_median = median(&phase);
    let sigma = 1.4826
        * median(
            &phase
                .iter()
                .map(|p| (p - phase_median).abs())
                .collect::<Vec<_>>(),
        );
    let near = phase
        .iter()
        .filter(|p| p.abs() <= 0.03_f64.max(sigma * 0.5))
        .count() as f64
        / phase.len() as f64;
    let cluster = two_cluster_improvement(
        &phase
            .iter()
            .step_by((phase.len() / 200_000).max(1))
            .copied()
            .collect::<Vec<_>>(),
    );
    let mut candidates = Vec::new();
    for rate in &c.symbol_rate_bank_baud {
        let phase_db = line_snr_db(&mut phase_transition, c.sample_rate_hz, *rate);
        let envelope_db = line_snr_db(&mut envelope_transition, c.sample_rate_hz, *rate);
        let lag = ((c.sample_rate_hz / *rate as f64).round_ties_even() as usize).max(1);
        let mut correlations = Vec::new();
        for values in &phase_transition {
            let center = mean(values);
            let values: Vec<_> = values.iter().map(|v| v - center).collect();
            let denominator = pairwise_sum(&values.iter().map(|v| v * v).collect::<Vec<_>>());
            correlations.push(if denominator > 0.0 && lag < values.len() {
                pairwise_sum(
                    &values[..values.len() - lag]
                        .iter()
                        .zip(&values[lag..])
                        .map(|(a, b)| a * b)
                        .collect::<Vec<_>>(),
                ) / denominator
            } else {
                0.0
            });
        }
        candidates.push(json!({"baud":rate,"clock_line_snr_db":phase_db.max(envelope_db),"phase_transition_line_snr_db":phase_db,
            "envelope_transition_line_snr_db":envelope_db,"phase_transition_autocorrelation":median(&correlations)}));
    }
    candidates.sort_by(|a, b| {
        b["clock_line_snr_db"]
            .as_f64()
            .unwrap()
            .total_cmp(&a["clock_line_snr_db"].as_f64().unwrap())
            .then_with(|| a["baud"].as_u64().cmp(&b["baud"].as_u64()))
    });
    let best_clock = candidates[0]["clock_line_snr_db"].as_f64().unwrap();
    let hypotheses = routing_hypotheses(
        cv,
        papr,
        concentration,
        near,
        cluster,
        occupied,
        connected,
        fraction,
        best_clock,
    );
    let primary = hypotheses[0]["signal_type"].as_str().unwrap();
    let confidence = hypotheses[0]["confidence"].as_f64().unwrap();
    let fsk = hypotheses
        .iter()
        .find(|v| v["signal_type"] == "fsk_like")
        .unwrap()["confidence"]
        .as_f64()
        .unwrap();
    let rate9600 = candidates.iter().find(|v| v["baud"] == 9600).unwrap();
    let line9600 = rate9600["clock_line_snr_db"].as_f64().unwrap();
    let corr9600 = rate9600["phase_transition_autocorrelation"]
        .as_f64()
        .unwrap();
    let sensible = fsk >= 0.35
        && (4000.0..=28000.0).contains(&occupied)
        && (line9600 >= 2.5 || corr9600.abs() >= 0.15)
        && primary != "continuous_carrier_or_cw_like";
    let evidence=vec![format!("fsk_like_confidence={}",general4(fsk)),format!("occupied_bandwidth_99_hz={}",general4(occupied)),format!("9600_clock_line_snr_db={}",general4(line9600)),
        "decision requires FSK-like>=0.35, BW 4..28 kHz, 9600 line>=2.5 dB or |autocorr|>=0.15, non-CW primary".into()];
    unchanged(&identity, &file, &before)?;
    Ok(
        json!({"strongest_window_start_seconds":chosen.iter().map(|i|*i as f64*c.window_seconds).collect::<Vec<_>>(),
        "envelope_coefficient_of_variation":cv,"envelope_robust_variation":robust,"power_p99_to_mean_papr":papr,
        "phase_increment_circular_concentration":concentration,"phase_increment_robust_sigma_rad":sigma,"phase_increment_near_center_fraction":near,
        "phase_increment_two_cluster_improvement":cluster,"spectral_peak_to_median_ratio":peak_ratio,"occupied_bandwidth_99_hz":occupied,
        "peak_connected_bandwidth_hz":connected,"dominant_connected_excess_fraction":fraction,"symbol_rate_candidates":candidates,
        "hypotheses":hypotheses,"primary_routing":primary,"primary_confidence":confidence,"g3ruh_9600_probe_physically_sensible":sensible,"g3ruh_9600_evidence":evidence}),
    )
}

#[cfg(test)]
#[path = "tests/triage_tests.rs"]
mod tests;
