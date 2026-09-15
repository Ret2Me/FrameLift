//! Opt-in signal-only bootstrap and local, reliability-weighted channel fusion.
//!
//! Neither estimator accepts packets, reference bits, checksums, or satellite
//! metadata. Blind fitting alternates MLSE decisions and least squares on one
//! contiguous training block, then checks waveform fit on a disjoint block.
//! These are *inferred* symbols, not trusted/CRC-validated training anchors.
//! A successful fit is not evidence of a transmission: even random bipolar
//! noise can satisfy this channel model. Protocol validation and false-positive
//! controls remain the caller's responsibility.
//!
//! The estimators are experimental, deterministic and bounded. The original
//! decoder and its nearest-anchor branch must remain additive alternatives.

use crate::sequence::{self, ChannelModel};
use serde::{Deserialize, Serialize};

pub const BLIND_ALGORITHM_VERSION: &str = "decision-directed-split-v1";
pub const BLEND_ALGORITHM_VERSION: &str = "local-reliability-medoid-v1";
pub const MIN_BLIND_SYMBOLS: usize = 1024;
pub const MAX_BLIND_BLOCK_SYMBOLS: usize = 4096;
pub const MAX_BLIND_ITERATIONS: usize = 5;
pub const MAX_BLEND_MODELS: usize = 16;
const MAX_ABS_VALUE: f64 = 1.0e100;
const MAX_VALIDATION_NMSE: f64 = 0.20;
const MAX_FOURTH_MOMENT_RATIO: f64 = 2.70;
const MIN_TRANSITION_FRACTION: f64 = 0.08;
const MAX_TRANSITION_FRACTION: f64 = 0.92;
const MAX_ANCHOR_DISTANCE_SECONDS: f64 = 60.0;
const SEEDS: [[f64; 3]; 5] = [
    [0.0, 1.0, 0.0],
    [0.6, 1.0, 0.6],
    [-0.6, 1.0, -0.6],
    [0.6, 1.0, -0.6],
    [-0.6, 1.0, 0.6],
];

/// Residuals use inferred decisions, so they are waveform diagnostics, not BER,
/// calibrated detection probabilities, or an independently verified noise rate.
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct BlindFit {
    pub algorithm: String,
    pub model: ChannelModel,
    pub training_start_symbol: usize,
    pub training_end_symbol: usize,
    pub validation_start_symbol: usize,
    pub validation_end_symbol: usize,
    pub selected_seed: usize,
    pub iterations: usize,
    pub validation_symbols: usize,
    pub validation_waveform_nmse: f64,
    pub validation_fourth_moment_ratio: f64,
    pub validation_transition_fraction: f64,
    pub crc_or_reference_bits_used: bool,
    pub training_symbols_are_inferred: bool,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct BlendFit {
    pub algorithm: String,
    pub model: ChannelModel,
    pub medoid_input_index: usize,
    pub included_input_indices: Vec<usize>,
    pub excluded_input_indices: Vec<usize>,
    /// Same order as `included_input_indices`; these weights sum to one.
    pub normalized_weights: Vec<f64>,
    pub normalized_shape_disagreement: f64,
    /// `model.normalized_mse` is an average of source residuals, not a residual
    /// measured on the target. Do not interpret it as a target accuracy metric.
    pub model_residual_is_weighted_source_proxy: bool,
    /// The API has no source coordinates: caller must enforce guard intervals,
    /// source uniqueness and absence of overlap before providing the models.
    pub caller_must_verify_disjoint_training: bool,
}

fn rejected(message: &str) -> String {
    format!("blind rejected: {message}")
}

fn moments(samples: &[f64], scale: f64) -> (f64, f64, f64) {
    let mean = samples.iter().map(|sample| sample / scale).sum::<f64>() / samples.len() as f64;
    let mut second = 0.0;
    let mut fourth = 0.0;
    for sample in samples {
        let centered = sample / scale - mean;
        let square = centered * centered;
        second += square;
        fourth += square * square;
    }
    let variance = second / samples.len() as f64;
    let ratio = if variance > 0.0 {
        fourth / samples.len() as f64 / variance.powi(2)
    } else {
        f64::INFINITY
    };
    (mean, variance, ratio)
}

/// Select by *training* variance only, before fitting any candidate. This gives
/// a bounded amount of fitting work even for a very long symbol stream. A
/// stronger noise burst can win this selection; callers should retain their
/// window/timing diversity rather than treating this as an exhaustive search.
fn select_block(soft: &[f64], scale: f64) -> Result<(usize, usize, usize), String> {
    let width = 2 * MAX_BLIND_BLOCK_SYMBOLS;
    let mut selected = None;
    let mut best_variance = 0.0;
    for start in (0..soft.len()).step_by(width) {
        let end = (start + width).min(soft.len());
        if end - start < MIN_BLIND_SYMBOLS {
            continue;
        }
        let middle = start + (end - start) / 2;
        let (_, variance, _) = moments(&soft[start..middle], scale);
        if variance > best_variance {
            best_variance = variance;
            selected = Some((start, middle, end));
        }
    }
    selected.ok_or_else(|| rejected("no usable training variation"))
}

fn transition_fraction(levels: &[f64]) -> f64 {
    levels.windows(2).filter(|pair| pair[0] != pair[1]).count() as f64 / (levels.len() - 1) as f64
}

fn waveform_nmse(soft: &[f64], decisions: &[f64], model: &ChannelModel) -> f64 {
    let guard = sequence::TRAINING_GUARD_SYMBOLS;
    let scale = soft.iter().fold(0.0_f64, |a, b| a.max(b.abs()));
    let interior = &soft[guard..soft.len() - guard];
    let (_, variance, _) = moments(interior, scale);
    let taps = model.taps.map(|tap| tap / scale);
    let bias = model.bias / scale;
    let squared_error = (guard..soft.len() - guard)
        .map(|index| {
            let predicted = bias
                + taps[0] * decisions[index - 1]
                + taps[1] * decisions[index]
                + taps[2] * decisions[index + 1];
            (soft[index] / scale - predicted).powi(2)
        })
        .sum::<f64>();
    squared_error / interior.len() as f64 / variance
}

/// Bootstrap without a decoded packet. See [`fit_blind_diagnostic`] for the
/// inferred-label provenance and waveform-only quality diagnostics.
///
/// Invalid input errors start with `blind input:`; ordinary absent/unsupported
/// channel and quality-gate rejections start with `blind rejected:`.
pub fn fit_blind(soft: &[f64]) -> Result<ChannelModel, String> {
    Ok(fit_blind_diagnostic(soft)?.model)
}

pub fn fit_blind_diagnostic(soft: &[f64]) -> Result<BlindFit, String> {
    if soft.len() > sequence::MAX_SEQUENCE_SYMBOLS {
        return Err("blind input: symbol count exceeds 4194304".into());
    }
    if soft
        .iter()
        .any(|v| !v.is_finite() || v.abs() > MAX_ABS_VALUE)
    {
        return Err("blind input: symbols must be finite with magnitude <= 1e100".into());
    }
    if soft.len() < MIN_BLIND_SYMBOLS {
        return Err(rejected(
            "at least 1024 symbols are needed for disjoint fit/check blocks",
        ));
    }
    let scale = soft.iter().fold(0.0_f64, |a, b| a.max(b.abs()));
    if scale == 0.0 {
        return Err(rejected("no signal variation"));
    }
    let (start, middle, end) = select_block(soft, scale)?;
    let training = &soft[start..middle];
    let validation = &soft[middle..end];
    let (mean, variance, fourth) = moments(training, scale);
    if variance <= 1.0e-12 || !fourth.is_finite() || fourth > MAX_FOURTH_MOMENT_RATIO {
        return Err(rejected(
            "training waveform lacks bounded binary-channel structure",
        ));
    }
    let mut best: Option<(ChannelModel, usize, usize)> = None;
    for (seed_index, seed) in SEEDS.iter().enumerate() {
        let energy = seed.iter().map(|tap| tap * tap).sum::<f64>();
        let amplitude = scale * (variance / energy).sqrt();
        let mut current = ChannelModel {
            taps: seed.map(|tap| tap * amplitude),
            bias: mean * scale,
            training_symbols: training.len() - 2 * sequence::TRAINING_GUARD_SYMBOLS,
            normalized_mse: 0.0,
        };
        let mut previous_decisions = None;
        for iteration in 1..=MAX_BLIND_ITERATIONS {
            let decisions = sequence::detect_sequence(training, &current, 1.0)
                .map_err(|error| rejected(&format!("unusable initialization: {error}")))?;
            let transition = transition_fraction(&decisions);
            if !(MIN_TRANSITION_FRACTION..=MAX_TRANSITION_FRACTION).contains(&transition) {
                break;
            }
            let levels: Vec<u8> = decisions
                .iter()
                .map(|value| u8::from(*value > 0.0))
                .collect();
            // The same least-squares primitive is used, but labels are inferred
            // by MLSE, not trusted protocol anchors. BlindFit records that fact.
            let fitted = match sequence::fit_channel(training, &levels, &[(0, training.len())]) {
                Ok(model) => model,
                Err(_) => break,
            };
            let is_better = best
                .as_ref()
                .is_none_or(|(model, _, _)| fitted.normalized_mse < model.normalized_mse);
            if is_better {
                best = Some((fitted.clone(), seed_index, iteration));
            }
            let stable = previous_decisions.as_ref() == Some(&levels);
            current = fitted;
            previous_decisions = Some(levels);
            if stable {
                break;
            }
        }
    }
    let (model, selected_seed, iterations) =
        best.ok_or_else(|| rejected("no stable centered three-tap fit"))?;
    // Select the winner on training alone, then inspect the untouched block
    // once. No candidate is refitted or selected based on the check residual.
    let (_, validation_variance, validation_fourth) = moments(validation, scale);
    if validation_variance <= 1.0e-12
        || !validation_fourth.is_finite()
        || validation_fourth > MAX_FOURTH_MOMENT_RATIO
    {
        return Err(rejected(
            "check waveform lacks bounded binary-channel structure",
        ));
    }
    let decisions = sequence::detect_sequence(validation, &model, 1.0)
        .map_err(|error| rejected(&format!("validation detector failed: {error}")))?;
    let transition = transition_fraction(&decisions);
    if !(MIN_TRANSITION_FRACTION..=MAX_TRANSITION_FRACTION).contains(&transition) {
        return Err(rejected(
            "check decisions have implausible transition density",
        ));
    }
    let nmse = waveform_nmse(validation, &decisions, &model);
    if !nmse.is_finite() || !(0.0..=MAX_VALIDATION_NMSE).contains(&nmse) {
        return Err(rejected("disjoint waveform residual exceeds 0.20"));
    }
    Ok(BlindFit {
        algorithm: BLIND_ALGORITHM_VERSION.into(),
        model,
        training_start_symbol: start,
        training_end_symbol: middle,
        validation_start_symbol: middle,
        validation_end_symbol: end,
        selected_seed,
        iterations,
        validation_symbols: validation.len() - 2 * sequence::TRAINING_GUARD_SYMBOLS,
        validation_waveform_nmse: nmse,
        validation_fourth_moment_ratio: validation_fourth,
        validation_transition_fraction: transition,
        crc_or_reference_bits_used: false,
        training_symbols_are_inferred: true,
    })
}

fn validate_blend_input(model: &ChannelModel, distance: f64) -> Result<(), String> {
    if !distance.is_finite() || !(0.0..=MAX_ANCHOR_DISTANCE_SECONDS).contains(&distance) {
        return Err("blend input: distance must be finite and in 0..=60 seconds".into());
    }
    // Public sequence validation is exercised on three harmless dummy symbols;
    // no training or target observations are required to validate a model.
    sequence::detect_sequence(&[0.0; 3], model, 1.0)
        .map_err(|error| format!("blend input: invalid source model: {error}"))?;
    Ok(())
}

fn shape_distance(a: &ChannelModel, b: &ChannelModel) -> f64 {
    ((a.taps[0] / a.taps[1] - b.taps[0] / b.taps[1]).powi(2)
        + (a.taps[2] / a.taps[1] - b.taps[2] / b.taps[1]).powi(2))
    .sqrt()
}

fn compatible(a: &ChannelModel, b: &ChannelModel) -> bool {
    let largest = a.taps[1].max(b.taps[1]);
    let smallest = a.taps[1].min(b.taps[1]);
    shape_distance(a, b) <= 0.75
        && smallest / largest >= 0.25
        && (a.bias / a.taps[1] - b.bias / b.taps[1]).abs() <= 1.0
}

/// Form one additional local model from compatible, time-near reliable anchors.
/// The caller must verify that anchors are distinct, trustworthy and disjoint
/// from each other and the target. Keep the original nearest model as another
/// branch: this aggregate is not guaranteed to outperform it on every target.
///
/// `distance_seconds` is nonnegative absolute time distance, not a signed
/// timestamp. Thus this implements local averaging, not temporal interpolation.
pub fn blend_models(models: &[(ChannelModel, f64)]) -> Result<ChannelModel, String> {
    Ok(blend_models_diagnostic(models)?.model)
}

pub fn blend_models_diagnostic(models: &[(ChannelModel, f64)]) -> Result<BlendFit, String> {
    if models.len() > MAX_BLEND_MODELS {
        return Err("blend input: at most 16 source models are allowed".into());
    }
    for (model, distance) in models {
        validate_blend_input(model, *distance)?;
    }
    if models.len() < 2 {
        return Err("blend rejected: at least two source models are needed".into());
    }
    let weights: Vec<f64> = models
        .iter()
        .map(|(model, distance)| {
            (model.training_symbols.min(4096) as f64).sqrt()
                / (0.02 + model.normalized_mse)
                / (1.0 + (distance / 15.0).powi(2))
        })
        .collect();
    // Robust local support: choose the source with the highest total weight of
    // compatible neighbors. Fixed input order is the tie breaker.
    let mut medoid = 0;
    let mut best_support = -1.0;
    for (index, (model, _)) in models.iter().enumerate() {
        let support = models
            .iter()
            .zip(&weights)
            .filter(|((other, _), _)| compatible(model, other))
            .map(|(_, weight)| weight)
            .sum::<f64>();
        if support > best_support {
            best_support = support;
            medoid = index;
        }
    }
    let mut included = Vec::new();
    let mut excluded = Vec::new();
    for (index, (model, _)) in models.iter().enumerate() {
        if compatible(&models[medoid].0, model) {
            included.push(index);
        } else {
            excluded.push(index);
        }
    }
    if included.len() < 2 {
        return Err("blend rejected: no pair of compatible source models".into());
    }
    let total_weight = included.iter().map(|&index| weights[index]).sum::<f64>();
    let normalized_weights: Vec<f64> = included
        .iter()
        .map(|&index| weights[index] / total_weight)
        .collect();
    let mut model = ChannelModel {
        taps: [0.0; 3],
        bias: 0.0,
        training_symbols: 0,
        normalized_mse: 0.0,
    };
    for (&index, weight) in included.iter().zip(&normalized_weights) {
        let source = &models[index].0;
        for (tap, value) in model.taps.iter_mut().zip(source.taps) {
            *tap += value * weight;
        }
        model.bias += source.bias * weight;
        model.normalized_mse += source.normalized_mse * weight;
        model.training_symbols =
            (model.training_symbols + source.training_symbols).min(sequence::MAX_SEQUENCE_SYMBOLS);
    }
    let disagreement = included
        .iter()
        .zip(&normalized_weights)
        .map(|(&index, weight)| weight * shape_distance(&model, &models[index].0).powi(2))
        .sum::<f64>()
        .sqrt();
    if !disagreement.is_finite() || disagreement > 0.45 {
        return Err("blend rejected: source shape disagreement exceeds 0.45".into());
    }
    validate_blend_input(&model, 0.0)?;
    Ok(BlendFit {
        algorithm: BLEND_ALGORITHM_VERSION.into(),
        model,
        medoid_input_index: medoid,
        included_input_indices: included,
        excluded_input_indices: excluded,
        normalized_weights,
        normalized_shape_disagreement: disagreement,
        model_residual_is_weighted_source_proxy: true,
        caller_must_verify_disjoint_training: true,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn uniform(state: &mut u64) -> f64 {
        *state ^= *state << 13;
        *state ^= *state >> 7;
        *state ^= *state << 17;
        ((*state >> 11) as f64 + 0.5) / ((1_u64 << 53) as f64)
    }

    fn bits(length: usize, mut state: u64) -> Vec<f64> {
        (0..length)
            .map(|_| if uniform(&mut state) > 0.5 { 1.0 } else { -1.0 })
            .collect()
    }

    fn model(taps: [f64; 3], bias: f64) -> ChannelModel {
        ChannelModel {
            taps,
            bias,
            training_symbols: 2048,
            normalized_mse: 0.02,
        }
    }

    fn channel(bits: &[f64], model: &ChannelModel) -> Vec<f64> {
        (0..bits.len())
            .map(|n| {
                model.bias
                    + model.taps[0] * bits[n.saturating_sub(1)]
                    + model.taps[1] * bits[n]
                    + model.taps[2] * bits[(n + 1).min(bits.len() - 1)]
            })
            .collect()
    }

    fn errors(actual: &[f64], truth: &[f64]) -> usize {
        actual
            .iter()
            .zip(truth)
            .filter(|(a, b)| (**a >= 0.0) != (**b >= 0.0))
            .count()
    }

    fn gaussian(length: usize, mut seed: u64) -> Vec<f64> {
        (0..length)
            .map(|_| {
                (-2.0 * uniform(&mut seed).ln()).sqrt()
                    * (std::f64::consts::TAU * uniform(&mut seed)).cos()
            })
            .collect()
    }

    #[test]
    fn blind_recovers_known_isi_without_receiving_reference_bits() {
        let truth = bits(8192, 731);
        for taps in [
            [0.55, 0.75, 0.55],
            [0.48, 0.80, -0.48],
            [-0.55, 0.75, -0.55],
        ] {
            let received = channel(&truth, &model(taps, 0.12));
            let fitted = fit_blind_diagnostic(&received).unwrap();
            let detected = sequence::detect_sequence(&received, &fitted.model, 1.0).unwrap();
            assert!(errors(&received[20..8172], &truth[20..8172]) > 500);
            assert_eq!(errors(&detected[20..8172], &truth[20..8172]), 0);
            assert!(fitted.validation_waveform_nmse < 1.0e-8);
            assert!(fitted.training_symbols_are_inferred);
            assert!(!fitted.crc_or_reference_bits_used);
            assert!(fitted.training_end_symbol <= fitted.validation_start_symbol);
        }
    }

    #[test]
    fn blind_model_transfers_to_independent_known_bits_with_noise() {
        let training_truth = bits(8192, 3917);
        let target_truth = bits(4096, 99191);
        let source = model([0.54, 0.77, 0.52], 0.07);
        let mut training = channel(&training_truth, &source);
        let mut target = channel(&target_truth, &source);
        for (value, noise) in training.iter_mut().zip(gaussian(8192, 191)) {
            *value += noise * 0.06;
        }
        for (value, noise) in target.iter_mut().zip(gaussian(4096, 919)) {
            *value += noise * 0.06;
        }
        let fitted = fit_blind(&training).unwrap();
        let detected = sequence::detect_sequence(&target, &fitted, 1.0).unwrap();
        assert!(errors(&target[20..4076], &target_truth[20..4076]) > 300);
        assert_eq!(errors(&detected[20..4076], &target_truth[20..4076]), 0);
    }

    #[test]
    fn bounded_fit_is_deterministic_and_records_exact_selected_blocks() {
        let mut received = vec![0.0; 8192];
        received.extend(channel(&bits(8192, 1871), &model([0.3, 1.0, 0.2], -0.2)));
        let a = fit_blind_diagnostic(&received).unwrap();
        let b = fit_blind_diagnostic(&received).unwrap();
        assert_eq!(
            serde_json::to_vec(&a).unwrap(),
            serde_json::to_vec(&b).unwrap()
        );
        assert_eq!(
            (a.training_start_symbol, a.training_end_symbol),
            (8192, 12288)
        );
        assert_eq!(
            (a.validation_start_symbol, a.validation_end_symbol),
            (12288, 16384)
        );
        assert!(a.iterations <= MAX_BLIND_ITERATIONS);
        assert!(a.model.training_symbols <= MAX_BLIND_BLOCK_SYMBOLS);
        assert!(a.validation_symbols <= MAX_BLIND_BLOCK_SYMBOLS);
    }

    #[test]
    fn blank_rank_deficient_invalid_and_nonfinite_inputs_are_rejected() {
        for length in [0, 3, 1023, 1024, 8192] {
            assert!(
                fit_blind(&vec![0.0; length])
                    .unwrap_err()
                    .starts_with("blind rejected:")
            );
        }
        assert!(fit_blind(&vec![5.0; 8192]).is_err());
        let alternating: Vec<f64> = (0..8192)
            .map(|n| if n % 2 == 0 { 1.0 } else { -1.0 })
            .collect();
        assert!(fit_blind(&alternating).is_err());
        for value in [f64::NAN, f64::INFINITY, -f64::INFINITY, 1.0e101] {
            let mut received = bits(8192, 51);
            received[8191] = value;
            assert!(
                fit_blind(&received)
                    .unwrap_err()
                    .starts_with("blind input:")
            );
        }
    }

    #[test]
    fn fixed_white_and_colored_gaussian_controls_do_not_pass_gate() {
        for seed in 1..=24 {
            let white = gaussian(8192, seed * 1783);
            assert!(fit_blind(&white).is_err(), "white seed={seed}");
            let colored = channel(&white, &model([0.55, 0.75, 0.55], 0.0));
            assert!(fit_blind(&colored).is_err(), "colored seed={seed}");
        }
    }

    #[test]
    fn random_binary_noise_is_not_misrepresented_as_verified_transmission() {
        // A binary random process is indistinguishable from unknown scrambled
        // bits to a waveform-only estimator. Fitting it must not set evidence
        // flags claiming a known packet, calibrated BER or received CRC.
        let fit = fit_blind_diagnostic(&bits(8192, 731917)).unwrap();
        assert!(fit.training_symbols_are_inferred);
        assert!(!fit.crc_or_reference_bits_used);
    }

    #[test]
    fn disjoint_noise_check_rejects_a_clean_training_fit() {
        let mut received = channel(&bits(4096, 731), &model([0.3, 1.0, 0.2], 0.0));
        received.extend(gaussian(4096, 817));
        assert!(
            fit_blind(&received)
                .unwrap_err()
                .starts_with("blind rejected:")
        );
    }

    #[test]
    fn scale_does_not_change_known_bit_recovery() {
        let truth = bits(4096, 9187);
        let received = channel(&truth, &model([0.55, 0.75, 0.55], 0.13));
        for gain in [1.0e-80, 1.0, 1.0e80] {
            let scaled: Vec<f64> = received.iter().map(|value| value * gain).collect();
            let fit = fit_blind(&scaled).unwrap();
            let detected = sequence::detect_sequence(&scaled, &fit, 1.0).unwrap();
            assert_eq!(errors(&detected[20..4076], &truth[20..4076]), 0);
        }
    }

    #[test]
    fn blend_weights_both_reliability_and_temporal_distance() {
        let good = model([0.25, 0.9, 0.20], 0.1);
        let mut weak = model([0.45, 0.9, 0.35], 0.2);
        weak.normalized_mse = 0.3;
        let fit = blend_models_diagnostic(&[(good.clone(), 2.0), (weak, 40.0)]).unwrap();
        assert_eq!(fit.included_input_indices, [0, 1]);
        assert!(fit.normalized_weights[0] > 0.95);
        assert!((fit.normalized_weights.iter().sum::<f64>() - 1.0).abs() < 1.0e-12);
        assert!((fit.model.taps[0] - good.taps[0]).abs() < 0.01);
        assert!(fit.model_residual_is_weighted_source_proxy);
        assert!(fit.caller_must_verify_disjoint_training);
    }

    #[test]
    fn blend_excludes_incompatible_shape_and_retains_auditable_indices() {
        let source = model([0.5, 0.8, 0.5], 0.1);
        let neighbor = model([0.48, 0.81, 0.51], 0.11);
        let alien = model([-0.5, 0.8, -0.5], 0.1);
        let fit = blend_models_diagnostic(&[(source, 3.0), (neighbor, 5.0), (alien, 4.0)]).unwrap();
        assert_eq!(fit.included_input_indices, [0, 1]);
        assert_eq!(fit.excluded_input_indices, [2]);
        assert!(fit.normalized_shape_disagreement < 0.1);
        assert_eq!(fit.model.training_symbols, 4096);
    }

    #[test]
    fn blending_identical_models_is_stable_and_bounded() {
        let source = model([0.4e80, 0.8e80, 0.3e80], 0.1e80);
        let inputs = vec![(source.clone(), 5.0); MAX_BLEND_MODELS];
        let a = blend_models_diagnostic(&inputs).unwrap();
        let b = blend_models_diagnostic(&inputs).unwrap();
        assert_eq!(
            serde_json::to_vec(&a).unwrap(),
            serde_json::to_vec(&b).unwrap()
        );
        assert!(a.normalized_shape_disagreement < 1.0e-12);
        for (actual, expected) in a.model.taps.iter().zip(source.taps) {
            assert!((actual / expected - 1.0).abs() < 1.0e-12);
        }
    }

    #[test]
    fn blend_rejects_invalid_inputs_and_incompatible_singleton_support() {
        let valid = model([0.5, 0.8, 0.5], 0.0);
        assert!(
            blend_models(&[])
                .unwrap_err()
                .starts_with("blend rejected:")
        );
        assert!(blend_models(&[(valid.clone(), 1.0)]).is_err());
        for distance in [-1.0, 60.1, f64::NAN, f64::INFINITY] {
            assert!(
                blend_models(&[(valid.clone(), distance), (valid.clone(), 1.0)])
                    .unwrap_err()
                    .starts_with("blend input:")
            );
        }
        let reversed = model([-0.5, 0.8, -0.5], 0.0);
        assert!(
            blend_models(&[(valid.clone(), 1.0), (reversed, 1.0)])
                .unwrap_err()
                .starts_with("blend rejected:")
        );
        let mut invalid = valid.clone();
        invalid.normalized_mse = 0.7;
        assert!(
            blend_models(&[(valid.clone(), 1.0), (invalid, 1.0)])
                .unwrap_err()
                .starts_with("blend input:")
        );
        assert!(blend_models(&vec![(valid, 1.0); MAX_BLEND_MODELS + 1]).is_err());
    }
}
