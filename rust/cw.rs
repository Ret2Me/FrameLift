//! Source-faithful narrowband/CW inspection, not a telemetry validator.
//!
//! Text always remains `pending` and never enters native/CRC frame counts.
//! Only headerless CI16-LE is accepted. Each IQ window is read separately;
//! bounded carrier history and binned envelopes preserve the original search.
//! Oversized metadata fails explicitly, never through silent downsampling.
//! Floating FFT scores are not asserted bit-identical on all possible inputs.
//! On exactly continuous tones, envelope centers can differ by a few ULPs:
//! two-level duty/transition diagnostics are consequently ill-conditioned.
//! Those computed diagnostics are retained, never forced to reference values.

use crate::triage::{
    centered32, fft, fft32, general4, hann32, mean, mean32, median, peak_index, quantile,
    read_window, source_file, spectrum_power, unchanged,
};
use num_complex::Complex64;
use serde::{Deserialize, Serialize};
use std::collections::{BTreeMap, BTreeSet};
use std::path::Path;

const MAX_WINDOW_SAMPLES: usize = 1_048_576;
const MAX_WINDOWS: usize = 100_000;
const MAX_TRACK_CELLS: usize = 2_000_000;
const MAX_TRANSITIONS: usize = 100_000_000;
const MAX_ENVELOPE_BINS: usize = 8_000_000;

fn require(value: bool, message: &str) -> Result<(), String> {
    if value { Ok(()) } else { Err(message.into()) }
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(default, deny_unknown_fields)]
pub struct CwProbeConfig {
    pub sample_rate_hz: f64,
    pub window_seconds: f64,
    pub preview_fft_size: usize,
    pub preview_slices_per_window: usize,
    pub carrier_track_candidates: usize,
    pub carrier_track_transition_scale_hz: f64,
    pub carrier_half_bandwidth_hz: f64,
    pub envelope_bin_seconds: f64,
    pub padding_windows: usize,
    pub active_power_ratio: f64,
    pub active_peak_ratio: f64,
    pub wpm_min: u32,
    pub wpm_max: u32,
}
impl Default for CwProbeConfig {
    fn default() -> Self {
        Self {
            sample_rate_hz: 57_600.0,
            window_seconds: 1.0,
            preview_fft_size: 8192,
            preview_slices_per_window: 4,
            carrier_track_candidates: 12,
            carrier_track_transition_scale_hz: 200.0,
            carrier_half_bandwidth_hz: 250.0,
            envelope_bin_seconds: 0.01,
            padding_windows: 1,
            active_power_ratio: 1.5,
            active_peak_ratio: 20.0,
            wpm_min: 5,
            wpm_max: 40,
        }
    }
}
impl CwProbeConfig {
    fn dimensions(&self) -> Result<(usize, usize), String> {
        for value in [
            self.sample_rate_hz,
            self.window_seconds,
            self.carrier_track_transition_scale_hz,
            self.carrier_half_bandwidth_hz,
            self.envelope_bin_seconds,
        ] {
            require(
                value.is_finite() && value > 0.0,
                "CW rate/window/bandwidth/transition/bin values must be finite and positive",
            )?;
        }
        require(
            self.active_power_ratio.is_finite() && self.active_peak_ratio.is_finite(),
            "CW activity thresholds must be finite",
        )?;
        require(
            self.preview_fft_size.is_power_of_two(),
            "preview_fft_size must be a positive power of two",
        )?;
        require(
            self.preview_slices_per_window > 0
                && self.preview_slices_per_window <= 1024
                && self.carrier_track_candidates > 0,
            "CW preview slices must be in 1..1024 and carrier candidate count positive",
        )?;
        require(
            5 <= self.wpm_min && self.wpm_min <= self.wpm_max && self.wpm_max <= 40,
            "Morse WPM bank must lie within 5..40",
        )?;
        let samples = (self.sample_rate_hz * self.window_seconds).round_ties_even();
        require(
            samples >= 1.0 && samples <= MAX_WINDOW_SAMPLES as f64,
            "CW analysis window exceeds 1..1048576 sample bound",
        )?;
        let bin = (self.envelope_bin_seconds * self.sample_rate_hz)
            .round_ties_even()
            .max(1.0);
        require(
            bin.is_finite() && bin <= samples,
            "CW envelope bin must fit one analysis window",
        )?;
        require(
            self.preview_fft_size <= samples as usize,
            "recording must contain one complete analysis window",
        )?;
        Ok((samples as usize, bin as usize))
    }
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
pub struct CandidateExtractorCapabilities {
    pub plugin_id: String,
    pub waveform_families: Vec<String>,
    pub output_kind: String,
    pub validation_level: String,
}
#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
pub struct MorseCandidate {
    pub text: String,
    pub wpm: u32,
    pub timing_score: f64,
    pub valid_character_fraction: f64,
    pub decoded_character_count: usize,
    pub region_start_seconds: f64,
    pub region_end_seconds: f64,
    pub repeat_count: usize,
    pub candidate_validation: String,
}
#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
pub struct CwProbeResult {
    pub classification: String,
    pub active_window_count: usize,
    pub selected_window_count_with_padding: usize,
    pub analyzed_region_count: usize,
    pub median_carrier_offset_hz: Option<f64>,
    pub carrier_offset_p10_hz: Option<f64>,
    pub carrier_offset_p90_hz: Option<f64>,
    pub median_peak_to_noise_ratio: f64,
    pub envelope_low_center: Option<f64>,
    pub envelope_high_center: Option<f64>,
    pub envelope_high_to_low_ratio: Option<f64>,
    pub keying_duty_cycle: Option<f64>,
    pub keying_transition_count: usize,
    pub keying_evidence_score: f64,
    pub candidates: Vec<MorseCandidate>,
    pub evidence: Vec<String>,
}
fn checked_result(result: CwProbeResult) -> Result<CwProbeResult, String> {
    let optional = [
        result.median_carrier_offset_hz,
        result.carrier_offset_p10_hz,
        result.carrier_offset_p90_hz,
        result.envelope_low_center,
        result.envelope_high_center,
        result.envelope_high_to_low_ratio,
        result.keying_duty_cycle,
    ];
    require(
        result.median_peak_to_noise_ratio.is_finite()
            && result.keying_evidence_score.is_finite()
            && optional.into_iter().flatten().all(f64::is_finite)
            && result.candidates.iter().all(|candidate| {
                [
                    candidate.timing_score,
                    candidate.valid_character_fraction,
                    candidate.region_start_seconds,
                    candidate.region_end_seconds,
                ]
                .into_iter()
                .all(f64::is_finite)
            }),
        "CW output contains nonfinite diagnostics or candidate times",
    )?;
    Ok(result)
}
pub trait NarrowbandCandidateExtractor {
    fn capabilities(&self) -> CandidateExtractorCapabilities;
    fn extract(&self, path: &Path) -> Result<CwProbeResult, String>;
}
#[derive(Clone, Debug, Default)]
pub struct NarrowbandCwExtractor {
    pub config: CwProbeConfig,
}
impl NarrowbandCwExtractor {
    pub fn capabilities(&self) -> CandidateExtractorCapabilities {
        capabilities()
    }
    pub fn extract(&self, path: &Path) -> Result<CwProbeResult, String> {
        extract_ci16(path, &self.config)
    }
}
impl NarrowbandCandidateExtractor for NarrowbandCwExtractor {
    fn capabilities(&self) -> CandidateExtractorCapabilities {
        self.capabilities()
    }
    fn extract(&self, path: &Path) -> Result<CwProbeResult, String> {
        self.extract(path)
    }
}
pub fn capabilities() -> CandidateExtractorCapabilities {
    CandidateExtractorCapabilities {
        plugin_id: "narrowband_cw_candidate_extractor".into(),
        waveform_families: vec![
            "continuous_carrier".into(),
            "cw_on_off_keying".into(),
            "unknown_narrowband".into(),
        ],
        output_kind: "untrusted_text_candidates".into(),
        validation_level: "pending".into(),
    }
}

// The peak bank uses NumPy 1.26.4 argpartition before its stable descending
// sort. Introselect's index permutation matters for equal-valued peaks; using
// a generic Rust sort/select would introduce a different tie policy.
// Algorithm reference: numpy/core/src/npysort/selection.cpp at v1.26.4.
fn median5(values: &[f64], indices: &mut [usize]) -> usize {
    for (a, b) in [(1, 0), (4, 3), (3, 0), (4, 1), (2, 1)] {
        if values[indices[a]] < values[indices[b]] {
            indices.swap(a, b);
        }
    }
    if values[indices[3]] < values[indices[2]] {
        if values[indices[3]] < values[indices[1]] {
            1
        } else {
            3
        }
    } else {
        2
    }
}
fn introselect(values: &[f64], indices: &mut [usize], kth: usize) {
    let count = indices.len();
    let (mut low, mut high) = (0, count - 1);
    if kth < 3 {
        for i in 0..=kth {
            let mut smallest = i;
            for j in i + 1..count {
                if values[indices[j]] < values[indices[smallest]] {
                    smallest = j;
                }
            }
            indices.swap(i, smallest);
        }
        return;
    }
    if kth == count - 1 {
        let mut biggest = 0;
        for i in 1..count {
            if values[indices[i]] >= values[indices[biggest]] {
                biggest = i;
            }
        }
        indices.swap(kth, biggest);
        return;
    }
    let mut depth = 2 * (usize::BITS - 1 - count.leading_zeros()) as i32;
    while low + 1 < high {
        let (mut left, mut right) = (low + 1, high);
        if depth > 0 || right - left < 5 {
            let middle = low + (high - low) / 2;
            if values[indices[high]] < values[indices[middle]] {
                indices.swap(high, middle);
            }
            if values[indices[high]] < values[indices[low]] {
                indices.swap(high, low);
            }
            if values[indices[low]] < values[indices[middle]] {
                indices.swap(low, middle);
            }
            indices.swap(middle, low + 1);
        } else {
            let medians = (right - left) / 5;
            for i in 0..medians {
                let subleft = left + 5 * i;
                let median = median5(values, &mut indices[subleft..subleft + 5]);
                indices.swap(subleft + median, left + i);
            }
            if medians > 2 {
                introselect(values, &mut indices[left..left + medians], medians / 2);
            }
            indices.swap(left + medians / 2, low);
            left -= 1;
            right += 1;
        }
        depth -= 1;
        let pivot = values[indices[low]];
        loop {
            left += 1;
            while values[indices[left]] < pivot {
                left += 1;
            }
            right -= 1;
            while pivot < values[indices[right]] {
                right -= 1;
            }
            if right < left {
                break;
            }
            indices.swap(left, right);
        }
        indices.swap(low, right);
        if right >= kth {
            high = right - 1;
        }
        if right <= kth {
            low = left;
        }
    }
    if high == low + 1 && values[indices[high]] < values[indices[low]] {
        indices.swap(high, low);
    }
}
fn strongest_peaks(power: &[f64], candidates: usize) -> Vec<usize> {
    let mut local: Vec<_> = (1..power.len().saturating_sub(1))
        .filter(|i| power[*i] > power[*i - 1] && power[*i] >= power[*i + 1])
        .collect();
    if local.is_empty() {
        local.push(peak_index(power));
    }
    let keep = candidates.min(local.len());
    let values: Vec<_> = local.iter().map(|i| power[*i]).collect();
    let mut indices: Vec<_> = (0..local.len()).collect();
    let kth = indices.len() - keep;
    introselect(&values, &mut indices, kth);
    let mut output: Vec<_> = indices[kth..].iter().map(|i| local[*i]).collect();
    output.sort_by(|a, b| power[*b].partial_cmp(&power[*a]).unwrap());
    output
}
fn regions(mask: &[bool]) -> Vec<(usize, usize)> {
    let mut output = Vec::new();
    let mut start = None;
    for (index, value) in mask.iter().enumerate() {
        if *value && start.is_none() {
            start = Some(index);
        }
        if let Some(left) = start
            && (!value || index == mask.len() - 1)
        {
            output.push((
                left,
                if *value && index == mask.len() - 1 {
                    index + 1
                } else {
                    index
                },
            ));
            start = None;
        }
    }
    output
}
fn padded_windows(active: &[bool], padding: usize) -> Vec<bool> {
    let mut padded = active.to_vec();
    let padding = padding.min(active.len());
    let mut filled_end = 0;
    for (index, on) in active.iter().enumerate() {
        if !on {
            continue;
        }
        let start = index.saturating_sub(padding).max(filled_end);
        let end = (index + padding).min(active.len() - 1) + 1;
        if end > filled_end {
            padded[start..end].fill(true);
            filled_end = end;
        }
    }
    padded
}
fn lower_envelope(values: &[f64]) -> f64 {
    let count = ((values.len() as f64 * 0.20).ceil() as usize).max(1);
    let mut sorted = values.to_vec();
    sorted.sort_by(f64::total_cmp);
    median(&sorted[..count])
}
fn kmeans_two(values: &[f64]) -> (f64, f64, Vec<bool>) {
    let mut centers = [quantile(values, 0.20), quantile(values, 0.80)];
    let mut labels = vec![0; values.len()];
    for _ in 0..24 {
        for (label, v) in labels.iter_mut().zip(values) {
            *label = usize::from((v - centers[1]).abs() < (v - centers[0]).abs());
        }
        let mut updated = centers;
        for (index, center) in updated.iter_mut().enumerate() {
            let selected: Vec<_> = values
                .iter()
                .zip(&labels)
                .filter(|(_, label)| **label == index)
                .map(|(v, _)| *v)
                .collect();
            if !selected.is_empty() {
                *center = mean(&selected);
            }
        }
        if (0..2).all(|i| (updated[i] - centers[i]).abs() <= 1e-8 + 1e-5 * centers[i].abs()) {
            break;
        }
        centers = updated;
    }
    let (low, high) = if centers[0] <= centers[1] {
        (0, 1)
    } else {
        (1, 0)
    };
    (
        centers[low],
        centers[high],
        labels.iter().map(|i| *i == high).collect(),
    )
}
fn runs(bits: &[bool], bin_seconds: f64) -> Vec<(bool, f64)> {
    if bits.is_empty() {
        return vec![];
    }
    let (mut value, mut count) = (bits[0], 1);
    let mut output = Vec::new();
    for next in &bits[1..] {
        if *next == value {
            count += 1;
        } else {
            output.push((value, count as f64 * bin_seconds));
            value = *next;
            count = 1;
        }
    }
    output.push((value, count as f64 * bin_seconds));
    output
}
fn morse(code: &str) -> char {
    match code {
        ".-" => 'A',
        "-..." => 'B',
        "-.-." => 'C',
        "-.." => 'D',
        "." => 'E',
        "..-." => 'F',
        "--." => 'G',
        "...." => 'H',
        ".." => 'I',
        ".---" => 'J',
        "-.-" => 'K',
        ".-.." => 'L',
        "--" => 'M',
        "-." => 'N',
        "---" => 'O',
        ".--." => 'P',
        "--.-" => 'Q',
        ".-." => 'R',
        "..." => 'S',
        "-" => 'T',
        "..-" => 'U',
        "...-" => 'V',
        ".--" => 'W',
        "-..-" => 'X',
        "-.--" => 'Y',
        "--.." => 'Z',
        "-----" => '0',
        ".----" => '1',
        "..---" => '2',
        "...--" => '3',
        "....-" => '4',
        "....." => '5',
        "-...." => '6',
        "--..." => '7',
        "---.." => '8',
        "----." => '9',
        _ => '?',
    }
}
fn finish_character(symbols: &mut String, word: &mut String) {
    if !symbols.is_empty() {
        word.push(morse(symbols));
        symbols.clear();
    }
}
fn decode_runs(runs: &[(bool, f64)], wpm: u32) -> (String, f64, f64, usize) {
    let unit = 1.2 / wpm as f64;
    let mut symbols = String::new();
    let mut words = Vec::new();
    let mut word = String::new();
    let mut scores = Vec::new();
    for (index, (on, duration)) in runs.iter().enumerate() {
        let ratio = duration / unit;
        if *on {
            let dot = (ratio - 1.0).abs();
            let dash = (ratio - 3.0).abs() / 3.0;
            if dot <= dash {
                symbols.push('.');
                scores.push((-dot).exp());
            } else {
                symbols.push('-');
                scores.push((-dash).exp());
            }
            continue;
        }
        if index == 0 || index == runs.len() - 1 {
            continue;
        }
        if ratio < 2.0 {
            scores.push((-(ratio - 1.0).abs()).exp());
        } else if ratio < 5.0 {
            finish_character(&mut symbols, &mut word);
            scores.push((-(ratio - 3.0).abs() / 3.0).exp());
        } else {
            finish_character(&mut symbols, &mut word);
            if !word.is_empty() {
                words.push(std::mem::take(&mut word));
            }
            scores.push((-(ratio - 7.0).abs() / 7.0).exp());
        }
    }
    finish_character(&mut symbols, &mut word);
    if !word.is_empty() {
        words.push(word);
    }
    let text = words.join(" ");
    let characters = text.bytes().filter(|c| *c != b' ').count();
    let valid = text.bytes().filter(|c| *c != b' ' && *c != b'?').count();
    let fraction = if characters > 0 {
        valid as f64 / characters as f64
    } else {
        0.0
    };
    // Python 3.12 sum uses compensated summation. Keep its positive-value
    // branch, including the final rounding of the correction.
    let (mut sum, mut correction) = (0.0_f64, 0.0_f64);
    for value in &scores {
        let next = sum + value;
        if sum.abs() >= value.abs() {
            correction += (sum - next) + value;
        } else {
            correction += (value - next) + sum;
        }
        sum = next;
    }
    let timing = if scores.is_empty() {
        0.0
    } else {
        (sum + correction) / scores.len() as f64
    };
    (text, timing, fraction, characters)
}

/// Inspect a CI16-LE capture without changing any trusted telemetry count.
pub fn extract_ci16(path: &Path, c: &CwProbeConfig) -> Result<CwProbeResult, String> {
    let (window_samples, bin_samples) = c.dimensions()?;
    let (mut file, identity, before) = source_file(path, 4)?;
    let samples = usize::try_from(identity.bytes / 4).map_err(|e| e.to_string())?;
    let count = samples / window_samples;
    require(
        (1..=MAX_WINDOWS).contains(&count),
        "CW capture must contain 1..100000 complete analysis windows",
    )?;
    let peak_bound = c
        .carrier_track_candidates
        .min(c.preview_fft_size.div_ceil(2))
        .max(1);
    require(
        count
            .checked_mul(peak_bound)
            .is_some_and(|v| v <= MAX_TRACK_CELLS),
        "CW carrier history exceeds 2000000-cell bound",
    )?;
    require(
        count
            .saturating_sub(1)
            .checked_mul(peak_bound)
            .and_then(|v| v.checked_mul(peak_bound))
            .is_some_and(|v| v <= MAX_TRANSITIONS),
        "CW carrier track exceeds 100000000 transition-work bound",
    )?;
    require(
        count
            .checked_mul(window_samples / bin_samples)
            .is_some_and(|v| v <= MAX_ENVELOPE_BINS),
        "CW envelope exceeds 8000000-bin bound",
    )?;
    let taper = hann32(c.preview_fft_size);
    let bin_width = c.sample_rate_hz / c.preview_fft_size as f64;
    require(
        bin_width.is_finite() && bin_width > 0.0,
        "CW FFT frequency resolution is not representable",
    )?;
    let mut powers = Vec::with_capacity(count);
    let mut candidate_frequencies = Vec::with_capacity(count);
    let mut candidate_ratios = Vec::with_capacity(count);
    for index in 0..count {
        let values = read_window(&mut file, index * window_samples, window_samples, false)?;
        powers.push(mean32(
            &values
                .iter()
                .map(|z| z.re * z.re + z.im * z.im)
                .collect::<Vec<_>>(),
        ));
        let mut combined = vec![0.0_f64; c.preview_fft_size];
        for part in 0..c.preview_slices_per_window {
            let offset = if c.preview_slices_per_window == 1 {
                0
            } else {
                ((window_samples - c.preview_fft_size) as f64 * part as f64
                    / (c.preview_slices_per_window - 1) as f64)
                    .floor() as usize
            };
            let segment = centered32(&values[offset..offset + c.preview_fft_size]);
            let mut spectrum = fft32(&segment, Some(&taper));
            spectrum.rotate_right(c.preview_fft_size / 2);
            for (target, power) in combined.iter_mut().zip(spectrum_power(&spectrum)) {
                *target = target.max(power);
            }
        }
        let floor = median(&combined);
        let strongest = strongest_peaks(&combined, c.carrier_track_candidates);
        candidate_frequencies.push(
            strongest
                .iter()
                .map(|i| (*i as f64 - (c.preview_fft_size / 2) as f64) * bin_width)
                .collect::<Vec<_>>(),
        );
        candidate_ratios.push(
            strongest
                .iter()
                .map(|i| combined[*i] / floor.max(1e-20))
                .collect::<Vec<_>>(),
        );
    }
    let mut previous: Vec<_> = candidate_ratios[0].iter().map(|r| r.ln_1p()).collect();
    let mut pointers = Vec::with_capacity(count - 1);
    for index in 1..count {
        let mut current = Vec::with_capacity(candidate_frequencies[index].len());
        let mut pointer = Vec::with_capacity(current.capacity());
        for (frequency, ratio) in candidate_frequencies[index]
            .iter()
            .zip(&candidate_ratios[index])
        {
            let choices: Vec<_> = candidate_frequencies[index - 1]
                .iter()
                .zip(&previous)
                .map(|(last, score)| {
                    score - (frequency - last).abs() / c.carrier_track_transition_scale_hz
                })
                .collect();
            let best = peak_index(&choices);
            pointer.push(best);
            current.push(ratio.ln_1p() + choices[best]);
        }
        pointers.push(pointer);
        previous = current;
    }
    require(
        previous.iter().all(|v| v.is_finite()),
        "CW carrier scores overflowed the supported finite range",
    )?;
    let mut states = vec![peak_index(&previous)];
    for pointer in pointers.iter().rev() {
        states.push(pointer[*states.last().unwrap()]);
    }
    states.reverse();
    let mut offsets: Vec<_> = states
        .iter()
        .enumerate()
        .map(|(i, state)| candidate_frequencies[i][*state])
        .collect();
    let peaks: Vec<_> = states
        .iter()
        .enumerate()
        .map(|(i, state)| candidate_ratios[i][*state])
        .collect();
    let noise = lower_envelope(&powers);
    let active: Vec<_> = powers
        .iter()
        .zip(&peaks)
        .map(|(p, ratio)| *p >= noise * c.active_power_ratio || *ratio >= c.active_peak_ratio)
        .collect();
    let active_count = active.iter().filter(|v| **v).count();
    let padded = padded_windows(&active, c.padding_windows);
    let selected_regions = regions(&padded);
    if selected_regions.is_empty() {
        unchanged(&identity, &file, &before)?;
        return checked_result(CwProbeResult {
            classification: "unknown".into(),
            active_window_count: 0,
            selected_window_count_with_padding: 0,
            analyzed_region_count: 0,
            median_carrier_offset_hz: None,
            carrier_offset_p10_hz: None,
            carrier_offset_p90_hz: None,
            median_peak_to_noise_ratio: median(&peaks),
            envelope_low_center: None,
            envelope_high_center: None,
            envelope_high_to_low_ratio: None,
            keying_duty_cycle: None,
            keying_transition_count: 0,
            keying_evidence_score: 0.0,
            candidates: vec![],
            evidence: vec!["no 1 s window passed the high-recall energy/spectral gate".into()],
        });
    }
    let active_indices: Vec<_> = active
        .iter()
        .enumerate()
        .filter_map(|(i, v)| v.then_some(i))
        .collect();
    for index in 0..count {
        if padded[index] && !active[index] {
            let at = active_indices.partition_point(|i| *i < index);
            let nearest = if at == 0 {
                active_indices[0]
            } else if at == active_indices.len()
                || index - active_indices[at - 1] <= active_indices[at] - index
            {
                active_indices[at - 1]
            } else {
                active_indices[at]
            };
            offsets[index] = offsets[nearest];
        }
    }
    let mut envelopes = Vec::with_capacity(selected_regions.len());
    let mut used_offsets = Vec::new();
    for (start, end) in &selected_regions {
        let mut region = Vec::with_capacity((end - start) * (window_samples / bin_samples));
        for (index, &offset) in offsets.iter().enumerate().take(*end).skip(*start) {
            let values = read_window(&mut file, index * window_samples, window_samples, false)?;
            let mut spectrum = fft32(&values, None);
            for (bin, value) in spectrum.iter_mut().enumerate() {
                let signed = if bin < window_samples.div_ceil(2) {
                    bin as f64
                } else {
                    bin as f64 - window_samples as f64
                };
                let frequency = signed * (c.sample_rate_hz / window_samples as f64);
                if (frequency - offset).abs() > c.carrier_half_bandwidth_hz {
                    *value = Complex64::new(0.0, 0.0);
                }
            }
            let filtered = fft(&spectrum, true);
            let amplitudes: Vec<_> = filtered.iter().map(|z| z.norm()).collect();
            region.extend(amplitudes.chunks_exact(bin_samples).map(mean));
            used_offsets.push(offset);
        }
        envelopes.push(region);
    }
    let all: Vec<_> = envelopes.iter().flatten().copied().collect();
    require(
        all.iter().all(|v| v.is_finite()),
        "CW envelope contains nonfinite statistics",
    )?;
    let (low, high, high_mask) = kmeans_two(&all);
    let ratio = high / low.max(1e-12);
    let duty = high_mask.iter().filter(|v| **v).count() as f64 / high_mask.len() as f64;
    let transitions = high_mask.windows(2).filter(|v| v[0] != v[1]).count();
    let mut within = Vec::with_capacity(all.len());
    for (center, label) in [(low, false), (high, true)] {
        within.extend(
            all.iter()
                .zip(&high_mask)
                .filter(|(_, v)| **v == label)
                .map(|(v, _)| (v - center).abs()),
        );
    }
    let scatter = median(&within);
    let separation = (high - low) / (scatter * 1.4826).max(1e-12);
    let evidence_score = ((ratio - 1.2) / 2.5).clamp(0.0, 1.0) * (separation / 6.0).min(1.0);
    let strong_peak = if active_count > 0 {
        median(
            &peaks
                .iter()
                .zip(&active)
                .filter(|(_, a)| **a)
                .map(|(p, _)| *p)
                .collect::<Vec<_>>(),
        )
    } else {
        0.0
    };
    let reliable = strong_peak >= c.active_peak_ratio * 0.60;
    let keyed = ratio >= 1.8
        && separation >= 3.0
        && (0.03..=0.90).contains(&duty)
        && transitions >= 4
        && evidence_score >= 0.50
        && reliable;
    let mut raw_candidates = Vec::new();
    if keyed {
        let threshold = (low + high) / 2.0;
        for (envelope, (start, end)) in envelopes.iter().zip(&selected_regions) {
            let bits: Vec<_> = envelope.iter().map(|v| *v >= threshold).collect();
            let run_list = runs(&bits, c.envelope_bin_seconds);
            let mut scored = Vec::new();
            for wpm in c.wpm_min..=c.wpm_max {
                let (text, timing, valid, characters) = decode_runs(&run_list, wpm);
                let score = timing * (0.25 + 0.75 * valid);
                if characters >= 2 && !text.is_empty() && score >= 0.75 && valid >= 0.80 {
                    scored.push((score, characters, wpm, text, valid));
                }
            }
            scored.sort_by(|a, b| {
                b.0.total_cmp(&a.0)
                    .then(b.1.cmp(&a.1))
                    .then(a.2.cmp(&b.2))
                    .then(a.3.cmp(&b.3))
            });
            let mut seen = BTreeSet::new();
            for (score, characters, wpm, text, valid) in scored {
                if !seen.insert(text.clone()) {
                    continue;
                }
                raw_candidates.push(MorseCandidate {
                    text,
                    wpm,
                    timing_score: score,
                    valid_character_fraction: valid,
                    decoded_character_count: characters,
                    region_start_seconds: *start as f64 * c.window_seconds,
                    region_end_seconds: *end as f64 * c.window_seconds,
                    repeat_count: 1,
                    candidate_validation: "pending".into(),
                });
                if seen.len() >= 3 {
                    break;
                }
            }
        }
    }
    let mut repetitions = BTreeMap::new();
    for candidate in &raw_candidates {
        *repetitions.entry(candidate.text.clone()).or_insert(0) += 1;
    }
    for candidate in &mut raw_candidates {
        candidate.repeat_count = repetitions[&candidate.text];
    }
    raw_candidates.sort_by(|a, b| {
        b.repeat_count
            .cmp(&a.repeat_count)
            .then(b.timing_score.total_cmp(&a.timing_score))
            .then(b.decoded_character_count.cmp(&a.decoded_character_count))
            .then(a.region_start_seconds.total_cmp(&b.region_start_seconds))
            .then(a.text.cmp(&b.text))
    });
    raw_candidates.truncate(10);
    unchanged(&identity, &file, &before)?;
    checked_result(CwProbeResult {classification:if keyed{"keyed"}else if reliable{"non_keyed"}else{"unknown"}.into(),
        active_window_count:active_count,selected_window_count_with_padding:padded.iter().filter(|v|**v).count(),analyzed_region_count:selected_regions.len(),
        median_carrier_offset_hz:Some(median(&used_offsets)),carrier_offset_p10_hz:Some(quantile(&used_offsets,0.10)),carrier_offset_p90_hz:Some(quantile(&used_offsets,0.90)),
        median_peak_to_noise_ratio:strong_peak,envelope_low_center:Some(low),envelope_high_center:Some(high),envelope_high_to_low_ratio:Some(ratio),
        keying_duty_cycle:Some(duty),keying_transition_count:transitions,keying_evidence_score:evidence_score,candidates:raw_candidates,
        evidence:vec![format!("high_to_low_envelope_ratio={}",general4(ratio)),format!("two_level_separation={}",general4(separation)),
            format!("duty_cycle={}",general4(duty)),format!("transition_count={transitions}"),format!("median_active_peak_to_noise={}",general4(strong_peak)),
            "keyed requires ratio>=1.8, separation>=3, duty 0.03..0.90, transitions>=4, evidence>=0.50, reliable carrier track".into()]})
}

#[cfg(test)]
#[path = "tests/cw_tests.rs"]
mod tests;
