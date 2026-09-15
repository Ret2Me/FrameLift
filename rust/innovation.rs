//! Experimental residual-predictive sequence detection, independent of payloads.
//!
//! An AR(0..=2) model is learned from waveform residuals on *other* trustworthy
//! symbol spans. Every span has its own chronological training/validation split;
//! lagged predictors never cross either a span boundary or that split. The
//! caller must ensure that spans represent distinct source samples and are
//! disjoint from the target. This API cannot infer provenance from sample values.
//!
//! Whitening both the observation and the three-tap channel gives an exact
//! 4/8/16-state Viterbi detector for the selected finite-memory objective. This
//! colored-noise idea is established prior art, not itself a novelty claim.
//! Optional, externally estimated innovation-variance multipliers permit a
//! separate codec-conditioned experiment; accepting them does not demonstrate
//! that codec metadata provides calibrated reliability. No CRC, packet bits,
//! list repair, or success-driven parameter selection is used in this module.

use crate::sequence::{ChannelModel, MAX_SEQUENCE_SYMBOLS, MIN_TRAINING_SYMBOLS};
use serde::{Deserialize, Serialize};

pub const MAX_INNOVATION_SYMBOLS: usize = 1_048_576;
pub const MAX_NOISE_SPANS: usize = 4096;
pub const MAX_AR_ORDER: usize = 2;
pub const MIN_VARIANCE_MULTIPLIER: f64 = 0.01;
pub const MAX_VARIANCE_MULTIPLIER: f64 = 100.0;
const MIN_SPAN_SYMBOLS: usize = 32;
const MIN_FIT_SYMBOLS: usize = 128;
const MIN_VALIDATION_SYMBOLS: usize = 64;
const MAX_ABS_VALUE: f64 = 1.0e100;
const MAX_POLE_RADIUS: f64 = 0.98;
const RIDGE: f64 = 1.0e-4;
const ORDER_PENALTY: f64 = 0.02;

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct NoiseCandidate {
    pub order: usize,
    pub ar_coefficients: Vec<f64>,
    pub training_mean_square: f64,
    pub validation_mean_square: f64,
    pub heldout_variance_ratio: f64,
    pub penalized_ratio: f64,
    pub stability_shrink: f64,
}

/// `residual[n] = sum(ar[j-1] * residual[n-j]) + innovation[n]`.
/// Coefficients are frozen from training data, *not* refit after validation.
/// The variance is the training innovation MSE in original waveform units.
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct NoiseModel {
    pub selected_order: usize,
    pub ar_coefficients: Vec<f64>,
    pub innovation_variance: f64,
    pub training_symbols: usize,
    pub validation_symbols: usize,
    pub heldout_variance_ratio: f64,
    pub span_count: usize,
    pub fit_status: String,
    pub candidates: Vec<NoiseCandidate>,
}

impl NoiseModel {
    /// An explicit white reference model for controlled comparisons; not a fit.
    pub fn white(innovation_variance: f64) -> Result<Self, String> {
        let model = Self {
            selected_order: 0,
            ar_coefficients: vec![],
            innovation_variance,
            training_symbols: 0,
            validation_symbols: 0,
            heldout_variance_ratio: 1.0,
            span_count: 0,
            fit_status: "explicit_white_reference".into(),
            candidates: vec![],
        };
        validate_noise(&model)?;
        Ok(model)
    }
}

fn pole_radius(coefficients: &[f64]) -> f64 {
    match coefficients {
        [] => 0.0,
        [a] => a.abs(),
        [a, b] => {
            let discriminant = a * a + 4.0 * b;
            if discriminant < 0.0 {
                (-b).sqrt()
            } else {
                let root = discriminant.sqrt();
                ((a + root) * 0.5).abs().max(((a - root) * 0.5).abs())
            }
        }
        _ => f64::INFINITY,
    }
}

fn validate_noise(model: &NoiseModel) -> Result<(), String> {
    if model.selected_order > MAX_AR_ORDER
        || model.ar_coefficients.len() != model.selected_order
        || model
            .ar_coefficients
            .iter()
            .any(|a| !a.is_finite() || a.abs() > 2.0)
        || pole_radius(&model.ar_coefficients) > MAX_POLE_RADIUS + 1.0e-12
    {
        return Err("innovation noise model must have stable AR order 0..=2".into());
    }
    if !model.innovation_variance.is_finite()
        || model.innovation_variance <= 0.0
        || model.innovation_variance > 1.0e202
        || !model.heldout_variance_ratio.is_finite()
        || model.heldout_variance_ratio < 0.0
    {
        return Err("innovation noise variance and validation diagnostics are invalid".into());
    }
    Ok(())
}

fn squared_residual(span: &[f64], n: usize, ar: &[f64], scale: f64) -> f64 {
    let residual = span[n] / scale
        - ar.iter()
            .enumerate()
            .map(|(j, a)| a * (span[n - j - 1] / scale))
            .sum::<f64>();
    residual * residual
}

/// Fit bounded AR candidates on residual samples, selecting only by waveform
/// prediction on disjoint chronological validation portions. A 2% per-order
/// penalty favors white noise unless a more complex model improves prediction.
/// All orders use the same rows, skipping two symbols after *every* boundary.
/// Short spans (<32 symbols) are ignored, not joined. At least 128 training and
/// 64 validation rows must remain. Zero-energy residual data is rejected.
///
/// Training does not use a free intercept: source-channel bias has already been
/// subtracted by the caller. A dominant residual DC offset (>half training RMS)
/// forces the white fallback because it is not reliable evidence of covariance.
pub fn fit_noise_model(residual_spans: &[Vec<f64>]) -> Result<NoiseModel, String> {
    if residual_spans.is_empty() || residual_spans.len() > MAX_NOISE_SPANS {
        return Err("innovation residual span count must be in 1..=4096".into());
    }
    let total = residual_spans
        .iter()
        .try_fold(0usize, |sum, span| sum.checked_add(span.len()));
    if total.is_none_or(|n| n > MAX_INNOVATION_SYMBOLS) {
        return Err("innovation residual samples exceed the 1048576-symbol limit".into());
    }
    let mut scale = 0.0_f64;
    for value in residual_spans.iter().flatten() {
        if !value.is_finite() || value.abs() > MAX_ABS_VALUE {
            return Err("innovation residual samples must be finite and bounded by 1e100".into());
        }
        scale = scale.max(value.abs());
    }
    if scale == 0.0 {
        return Err("innovation residual samples have no energy".into());
    }
    let spans: Vec<(&[f64], usize)> = residual_spans
        .iter()
        .filter(|span| span.len() >= MIN_SPAN_SYMBOLS)
        .map(|span| (span.as_slice(), span.len() * 2 / 3))
        .collect();
    let training_symbols: usize = spans.iter().map(|(_, split)| split - MAX_AR_ORDER).sum();
    let validation_symbols: usize = spans
        .iter()
        .map(|(span, split)| span.len() - split - MAX_AR_ORDER)
        .sum();
    if training_symbols < MIN_FIT_SYMBOLS || validation_symbols < MIN_VALIDATION_SYMBOLS {
        return Err("innovation fitting needs 128 training and 64 disjoint validation rows".into());
    }
    let mut gram = [[0.0; 2]; 2];
    let mut rhs = [0.0; 2];
    let mut train_energy = 0.0;
    let mut train_sum = 0.0;
    let mut validation_energy = 0.0;
    for (span, split) in &spans {
        for n in MAX_AR_ORDER..*split {
            let y = span[n] / scale;
            let x = [span[n - 1] / scale, span[n - 2] / scale];
            train_energy += y * y;
            train_sum += y;
            for row in 0..2 {
                rhs[row] += x[row] * y;
                for column in 0..2 {
                    gram[row][column] += x[row] * x[column];
                }
            }
        }
        validation_energy += span[*split + MAX_AR_ORDER..]
            .iter()
            .map(|y| (y / scale).powi(2))
            .sum::<f64>();
    }
    let train_white = train_energy / training_symbols as f64;
    let validate_white = validation_energy / validation_symbols as f64;
    if train_white <= 1.0e-12 || validate_white <= 1.0e-12 {
        return Err(
            "innovation fitting needs nonzero training and validation residual energy".into(),
        );
    }
    let dominant_dc = (train_sum / training_symbols as f64).abs() > 0.5 * train_white.sqrt();
    // Relative regularization is invariant to the absolute residual amplitude.
    let regularization = RIDGE * 0.5 * (gram[0][0] + gram[1][1]).max(1.0e-12);
    let mut ar1 = vec![rhs[0] / (gram[0][0] + regularization)];
    let g00 = gram[0][0] + regularization;
    let g11 = gram[1][1] + regularization;
    let determinant = g00 * g11 - gram[0][1] * gram[1][0];
    if !determinant.is_finite() || determinant <= 0.0 {
        return Err("innovation residual design is numerically singular".into());
    }
    let mut ar2 = vec![
        (rhs[0] * g11 - rhs[1] * gram[0][1]) / determinant,
        (rhs[1] * g00 - rhs[0] * gram[1][0]) / determinant,
    ];
    let mut shrink = [1.0; 3];
    for (order, coefficients) in [(1, &mut ar1), (2, &mut ar2)] {
        let radius = pole_radius(coefficients);
        if !radius.is_finite() {
            return Err("innovation residual fit produced nonfinite poles".into());
        }
        if radius > MAX_POLE_RADIUS {
            // Scaling the j-th AR coefficient by f^j scales every pole by f.
            shrink[order] = MAX_POLE_RADIUS / radius;
            for (j, a) in coefficients.iter_mut().enumerate() {
                *a *= shrink[order].powi((j + 1) as i32);
            }
        }
    }
    let mut candidates = Vec::with_capacity(3);
    let mut selected = 0;
    let mut best_penalized_ratio = 1.0;
    for (order, coefficients) in [vec![], ar1, ar2].into_iter().enumerate() {
        let mut train = 0.0;
        let mut validation = 0.0;
        for (span, split) in &spans {
            train += (MAX_AR_ORDER..*split)
                .map(|n| squared_residual(span, n, &coefficients, scale))
                .sum::<f64>();
            validation += (*split + MAX_AR_ORDER..span.len())
                .map(|n| squared_residual(span, n, &coefficients, scale))
                .sum::<f64>();
        }
        train /= training_symbols as f64;
        validation /= validation_symbols as f64;
        let ratio = validation / validate_white;
        let penalized_ratio = ratio * (1.0 + ORDER_PENALTY * order as f64);
        if order != 0 && !dominant_dc && penalized_ratio < best_penalized_ratio {
            selected = order;
            best_penalized_ratio = penalized_ratio;
        }
        candidates.push(NoiseCandidate {
            order,
            ar_coefficients: coefficients,
            training_mean_square: train * scale * scale,
            validation_mean_square: validation * scale * scale,
            heldout_variance_ratio: ratio,
            penalized_ratio,
            stability_shrink: shrink[order],
        });
    }
    let chosen = &candidates[selected];
    let model = NoiseModel {
        selected_order: selected,
        ar_coefficients: chosen.ar_coefficients.clone(),
        innovation_variance: chosen.training_mean_square,
        training_symbols,
        validation_symbols,
        heldout_variance_ratio: chosen.heldout_variance_ratio,
        span_count: spans.len(),
        fit_status: if dominant_dc {
            "white_fallback_mean_dominated"
        } else if selected == 0 {
            "white_fallback_no_validated_gain"
        } else {
            "colored_prediction_validated"
        }
        .into(),
        candidates,
    };
    validate_noise(&model)?;
    if model.candidates.iter().any(|c| {
        !c.training_mean_square.is_finite()
            || !c.validation_mean_square.is_finite()
            || !c.heldout_variance_ratio.is_finite()
    }) {
        return Err("innovation fit diagnostics overflowed".into());
    }
    Ok(model)
}

fn validate_channel(channel: &ChannelModel, gain: f64) -> Result<(), String> {
    // Match the original detector's public model contract without executing it.
    // Its validation helper is private, deliberately leaving the baseline intact.
    if !(MIN_TRAINING_SYMBOLS..=MAX_SEQUENCE_SYMBOLS).contains(&channel.training_symbols)
        || !channel.normalized_mse.is_finite()
        || !(0.0..=0.5).contains(&channel.normalized_mse)
        || channel
            .taps
            .iter()
            .chain(std::iter::once(&channel.bias))
            .any(|v| !v.is_finite() || v.abs() > MAX_ABS_VALUE)
    {
        return Err("innovation channel parameters or quality diagnostics are invalid".into());
    }
    let [left, center, right] = channel.taps;
    if center <= 0.0
        || center < left.abs().max(right.abs())
        || center < 0.55 * (left.abs() + right.abs())
    {
        return Err("innovation channel lacks a sufficiently dominant positive center tap".into());
    }
    if !gain.is_finite() || !(1.0e-6..=1.0e6).contains(&gain) || gain * center <= 0.0 {
        return Err("innovation channel gain must be representable and in 1e-6..=1e6".into());
    }
    Ok(())
}

/// Reusable storage for innovations detection. Retains at most the largest
/// valid traceback and output seen, plus the four-state fallback workspace.
#[derive(Debug, Default)]
pub struct InnovationWorkspace {
    traceback: Vec<u8>,
    levels: Vec<f64>,
    white: crate::sequence::SequenceWorkspace,
}

impl InnovationWorkspace {
    pub fn new() -> Self {
        Self::default()
    }
}

/// Exact finite-memory innovations MLSE, returning one bipolar level per input.
///
/// For AR order `p`, minimizes `sum((e[n]-sum(a[j-1]*e[n-j]))^2 / v[n])`
/// over `n = 1+p .. soft.len()-1`, where `e[n] = soft[n] - gain *
/// (bias + taps[0]*x[n-1] + taps[1]*x[n] + taps[2]*x[n+1])`.
/// This excludes observations without a complete symbol/AR neighborhood. Initial
/// and final symbol states are free; output indices are never shifted or cropped.
///
/// `variance_multipliers`, when present, must match the *input* length. They
/// describe innovation variance, not raw residual variance before whitening.
/// Every value must be finite and in 0.01..=100. The common scalar variance in
/// `NoiseModel` cancels from decisions and is intentionally not divided into the
/// branch metric. A candidate-independent log-variance term likewise cancels.
/// AR(0) with constant/unweighted variance delegates to the original detector,
/// preserving its exact arithmetic and deterministic tie behavior.
pub fn innovations_sequence(
    soft: &[f64],
    channel: &ChannelModel,
    noise: &NoiseModel,
    gain: f64,
    variance_multipliers: Option<&[f64]>,
) -> Result<Vec<f64>, String> {
    let mut workspace = InnovationWorkspace::new();
    Ok(innovations_sequence_with_workspace(
        soft,
        channel,
        noise,
        gain,
        variance_multipliers,
        &mut workspace,
    )?
    .to_vec())
}

/// Same arithmetic, validation order and ties as the owned API, with storage
/// retained between hypotheses. Every live decision/traceback cell is written
/// afresh. Errors leave storage unchanged; the returned slice is invocation-local.
pub fn innovations_sequence_with_workspace<'a>(
    soft: &[f64],
    channel: &ChannelModel,
    noise: &NoiseModel,
    gain: f64,
    variance_multipliers: Option<&[f64]>,
    workspace: &'a mut InnovationWorkspace,
) -> Result<&'a [f64], String> {
    validate_noise(noise)?;
    validate_channel(channel, gain)?;
    let memory = 2 + noise.selected_order;
    if soft.len() <= memory || soft.len() > MAX_INNOVATION_SYMBOLS {
        return Err("innovation sequence length must exceed memory and be <=1048576".into());
    }
    if soft
        .iter()
        .any(|v| !v.is_finite() || v.abs() > MAX_ABS_VALUE)
    {
        return Err("innovation samples must be finite and bounded by 1e100".into());
    }
    if let Some(values) = variance_multipliers
        && (values.len() != soft.len()
            || values.iter().any(|v| {
                !v.is_finite() || !(MIN_VARIANCE_MULTIPLIER..=MAX_VARIANCE_MULTIPLIER).contains(v)
            }))
    {
        return Err("innovation variance multipliers must match input and be in 0.01..=100".into());
    }
    if noise.selected_order == 0
        && variance_multipliers.is_none_or(|values| values.iter().all(|v| *v == values[0]))
    {
        return crate::sequence::detect_sequence_with_workspace(
            soft,
            channel,
            gain,
            &mut workspace.white,
        );
    }
    let scaled_taps = channel.taps.map(|tap| tap * gain);
    let scaled_bias = channel.bias * gain;
    let scale = soft
        .iter()
        .chain(scaled_taps.iter())
        .chain(std::iter::once(&scaled_bias))
        .fold(0.0_f64, |largest, value| largest.max(value.abs()));
    let taps = scaled_taps.map(|tap| tap / scale);
    if taps[1] <= 0.0 {
        return Err("innovation input/channel dynamic range is not representable".into());
    }
    let bias = scaled_bias / scale * (1.0 - noise.ar_coefficients.iter().sum::<f64>());
    // Descending-time coefficient order: x[n+1], x[n], x[n-1], ... .
    let base = [taps[2], taps[1], taps[0]];
    let mut effective = [0.0; 5];
    for (j, tap) in base.iter().enumerate() {
        effective[j] += tap;
        for (lag, a) in noise.ar_coefficients.iter().enumerate() {
            effective[j + lag + 1] -= a * tap;
        }
    }
    let states = 1usize << memory;
    let mut predictions = [[0.0; 16]; 2];
    for (dropped, row) in predictions.iter_mut().enumerate() {
        for (state, prediction) in row.iter_mut().enumerate().take(states) {
            *prediction = bias + effective[memory] * (2.0 * dropped as f64 - 1.0);
            for (lag, coefficient) in effective.iter().enumerate().take(memory) {
                *prediction += coefficient * (2.0 * ((state >> lag) & 1) as f64 - 1.0);
            }
        }
    }
    let steps = soft.len() - memory;
    let traceback_len = steps * states;
    if workspace.traceback.len() < traceback_len {
        workspace
            .traceback
            .reserve_exact(traceback_len - workspace.traceback.len());
        workspace.traceback.resize(traceback_len, 0);
    }
    let traceback = &mut workspace.traceback[..traceback_len];
    let mut costs = [0.0; 16];
    for step in 0..steps {
        let n = step + memory - 1;
        let observed = soft[n] / scale
            - noise
                .ar_coefficients
                .iter()
                .enumerate()
                .map(|(j, a)| a * (soft[n - j - 1] / scale))
                .sum::<f64>();
        let weight = variance_multipliers.map_or(1.0, |values| 1.0 / values[n]);
        let mut next = [0.0; 16];
        for state in 0..states {
            let low = state >> 1;
            let high = low | (states >> 1);
            let low_cost = costs[low] + (observed - predictions[0][state]).powi(2) * weight;
            let high_cost = costs[high] + (observed - predictions[1][state]).powi(2) * weight;
            let (value, predecessor) = if low_cost <= high_cost {
                (low_cost, low)
            } else {
                (high_cost, high)
            };
            next[state] = value;
            traceback[step * states + state] = predecessor as u8;
        }
        costs = next;
    }
    let mut state = 0;
    for candidate in 1..states {
        if costs[candidate] < costs[state] {
            state = candidate;
        }
    }
    if workspace.levels.len() < soft.len() {
        workspace
            .levels
            .reserve_exact(soft.len() - workspace.levels.len());
        workspace.levels.resize(soft.len(), -1.0);
    }
    let levels = &mut workspace.levels[..soft.len()];
    for step in (0..steps).rev() {
        levels[step + memory] = 2.0 * (state & 1) as f64 - 1.0;
        state = usize::from(traceback[step * states + state]);
    }
    for (index, level) in levels.iter_mut().enumerate().take(memory) {
        *level = 2.0 * ((state >> (memory - index - 1)) & 1) as f64 - 1.0;
    }
    Ok(levels)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn reusable_innovations_match_preoptimization_oracle_bit_for_bit() {
        let mut workspace = InnovationWorkspace::new();
        for seed in 0..12 {
            for length in [2048, 5, 127, 16] {
                let soft = if seed == 0 {
                    vec![0.0; length]
                } else {
                    random(seed, length)
                };
                let constant = vec![3.0; length];
                let varied: Vec<_> = (0..length).map(|i| 0.1 + (i % 17) as f64).collect();
                for ar in [vec![0.63, -0.27], vec![], vec![0.72]] {
                    let noise = colored(&ar);
                    for gain in [0.75, 1.0, 1.5] {
                        for weights in [None, Some(constant.as_slice()), Some(varied.as_slice())] {
                            workspace.traceback.fill(255);
                            workspace.levels.fill(f64::NAN);
                            let expected = legacy_innovations_sequence(
                                &soft,
                                &channel(),
                                &noise,
                                gain,
                                weights,
                            )
                            .unwrap();
                            let actual = innovations_sequence_with_workspace(
                                &soft,
                                &channel(),
                                &noise,
                                gain,
                                weights,
                                &mut workspace,
                            )
                            .unwrap();
                            assert_eq!(actual.len(), length);
                            assert!(
                                actual
                                    .iter()
                                    .zip(&expected)
                                    .all(|(a, b)| a.to_bits() == b.to_bits()),
                                "seed={seed},length={length},ar={ar:?},gain={gain}"
                            );
                        }
                    }
                }
            }
        }
    }

    #[test]
    fn innovation_workspace_reuses_capacity_and_errors_do_not_mutate_it() {
        let mut workspace = InnovationWorkspace::new();
        let soft = random(18, 4096);
        let noise = colored(&[0.63, -0.27]);
        innovations_sequence_with_workspace(&soft, &channel(), &noise, 1.0, None, &mut workspace)
            .unwrap();
        let storage = (
            workspace.traceback.as_ptr(),
            workspace.traceback.capacity(),
            workspace.levels.as_ptr(),
            workspace.levels.capacity(),
        );
        for length in [32, 4096, 5, 2048] {
            innovations_sequence_with_workspace(
                &soft[..length],
                &channel(),
                &noise,
                0.75,
                None,
                &mut workspace,
            )
            .unwrap();
            assert_eq!(
                storage,
                (
                    workspace.traceback.as_ptr(),
                    workspace.traceback.capacity(),
                    workspace.levels.as_ptr(),
                    workspace.levels.capacity()
                )
            );
        }
        let before = format!("{workspace:?}");
        let mut bad_noise = noise.clone();
        bad_noise.selected_order = 3;
        let mut bad_channel = channel();
        bad_channel.taps[1] = f64::NAN;
        let invalid_soft = [f64::NAN; 16];
        let invalid_weights = [0.0; 16];
        for (samples, model, noise, gain, weights) in [
            (&soft[..4], channel(), noise.clone(), 1.0, None),
            (&invalid_soft[..], channel(), noise.clone(), 1.0, None),
            (&soft[..16], channel(), bad_noise, 1.0, None),
            (&soft[..16], bad_channel, noise.clone(), 1.0, None),
            (&soft[..16], channel(), noise.clone(), 0.0, None),
            (
                &soft[..16],
                channel(),
                noise.clone(),
                1.0,
                Some(&invalid_weights[..]),
            ),
            (
                &soft[..16],
                channel(),
                noise.clone(),
                1.0,
                Some(&invalid_weights[..15]),
            ),
        ] {
            let expected =
                legacy_innovations_sequence(samples, &model, &noise, gain, weights).unwrap_err();
            let actual = innovations_sequence_with_workspace(
                samples,
                &model,
                &noise,
                gain,
                weights,
                &mut workspace,
            )
            .unwrap_err();
            assert_eq!(actual, expected);
            assert_eq!(format!("{workspace:?}"), before);
        }
    }

    // Independent allocation-per-call oracle copied before the workspace change.
    fn legacy_innovations_sequence(
        soft: &[f64],
        channel: &ChannelModel,
        noise: &NoiseModel,
        gain: f64,
        variance_multipliers: Option<&[f64]>,
    ) -> Result<Vec<f64>, String> {
        validate_noise(noise)?;
        validate_channel(channel, gain)?;
        let memory = 2 + noise.selected_order;
        if soft.len() <= memory || soft.len() > MAX_INNOVATION_SYMBOLS {
            return Err("innovation sequence length must exceed memory and be <=1048576".into());
        }
        if soft
            .iter()
            .any(|v| !v.is_finite() || v.abs() > MAX_ABS_VALUE)
        {
            return Err("innovation samples must be finite and bounded by 1e100".into());
        }
        if let Some(values) = variance_multipliers
            && (values.len() != soft.len()
                || values.iter().any(|v| {
                    !v.is_finite()
                        || !(MIN_VARIANCE_MULTIPLIER..=MAX_VARIANCE_MULTIPLIER).contains(v)
                }))
        {
            return Err(
                "innovation variance multipliers must match input and be in 0.01..=100".into(),
            );
        }
        if noise.selected_order == 0
            && variance_multipliers.is_none_or(|values| values.iter().all(|v| *v == values[0]))
        {
            return crate::sequence::detect_sequence(soft, channel, gain);
        }
        let scaled_taps = channel.taps.map(|tap| tap * gain);
        let scaled_bias = channel.bias * gain;
        let scale = soft
            .iter()
            .chain(scaled_taps.iter())
            .chain(std::iter::once(&scaled_bias))
            .fold(0.0_f64, |largest, value| largest.max(value.abs()));
        let taps = scaled_taps.map(|tap| tap / scale);
        if taps[1] <= 0.0 {
            return Err("innovation input/channel dynamic range is not representable".into());
        }
        let bias = scaled_bias / scale * (1.0 - noise.ar_coefficients.iter().sum::<f64>());
        // Descending-time coefficient order: x[n+1], x[n], x[n-1], ... .
        let base = [taps[2], taps[1], taps[0]];
        let mut effective = [0.0; 5];
        for (j, tap) in base.iter().enumerate() {
            effective[j] += tap;
            for (lag, a) in noise.ar_coefficients.iter().enumerate() {
                effective[j + lag + 1] -= a * tap;
            }
        }
        let states = 1usize << memory;
        let mut predictions = [[0.0; 16]; 2];
        for (dropped, row) in predictions.iter_mut().enumerate() {
            for (state, prediction) in row.iter_mut().enumerate().take(states) {
                *prediction = bias + effective[memory] * (2.0 * dropped as f64 - 1.0);
                for (lag, coefficient) in effective.iter().enumerate().take(memory) {
                    *prediction += coefficient * (2.0 * ((state >> lag) & 1) as f64 - 1.0);
                }
            }
        }
        let steps = soft.len() - memory;
        let mut traceback = vec![0u8; steps * states];
        let mut costs = [0.0; 16];
        for step in 0..steps {
            let n = step + memory - 1;
            let observed = soft[n] / scale
                - noise
                    .ar_coefficients
                    .iter()
                    .enumerate()
                    .map(|(j, a)| a * (soft[n - j - 1] / scale))
                    .sum::<f64>();
            let weight = variance_multipliers.map_or(1.0, |values| 1.0 / values[n]);
            let mut next = [0.0; 16];
            for state in 0..states {
                let low = state >> 1;
                let high = low | (states >> 1);
                let low_cost = costs[low] + (observed - predictions[0][state]).powi(2) * weight;
                let high_cost = costs[high] + (observed - predictions[1][state]).powi(2) * weight;
                let (value, predecessor) = if low_cost <= high_cost {
                    (low_cost, low)
                } else {
                    (high_cost, high)
                };
                next[state] = value;
                traceback[step * states + state] = predecessor as u8;
            }
            costs = next;
        }
        let mut state = 0;
        for candidate in 1..states {
            if costs[candidate] < costs[state] {
                state = candidate;
            }
        }
        let mut levels = vec![-1.0; soft.len()];
        for step in (0..steps).rev() {
            levels[step + memory] = 2.0 * (state & 1) as f64 - 1.0;
            state = usize::from(traceback[step * states + state]);
        }
        for (index, level) in levels.iter_mut().enumerate().take(memory) {
            *level = 2.0 * ((state >> (memory - index - 1)) & 1) as f64 - 1.0;
        }
        Ok(levels)
    }

    fn channel() -> ChannelModel {
        ChannelModel {
            taps: [0.28, 0.9, 0.22],
            bias: 0.13,
            training_symbols: 1024,
            normalized_mse: 0.1,
        }
    }

    fn colored(ar: &[f64]) -> NoiseModel {
        let mut model = NoiseModel::white(1.0).unwrap();
        model.selected_order = ar.len();
        model.ar_coefficients = ar.to_vec();
        model
    }

    fn random(mut seed: u64, length: usize) -> Vec<f64> {
        (0..length)
            .map(|_| {
                seed ^= seed << 13;
                seed ^= seed >> 7;
                seed ^= seed << 17;
                ((seed >> 11) as f64 / ((1u64 << 53) as f64)) * 2.0 - 1.0
            })
            .collect()
    }

    fn signal(bits: &[f64], model: &ChannelModel, gain: f64) -> Vec<f64> {
        (0..bits.len())
            .map(|n| {
                gain * (model.bias
                    + model.taps[0] * bits[n.saturating_sub(1)]
                    + model.taps[1] * bits[n]
                    + model.taps[2] * bits[(n + 1).min(bits.len() - 1)])
            })
            .collect()
    }

    fn residuals(seed: u64, length: usize, ar: &[f64], amplitude: f64) -> Vec<f64> {
        let mut result = random(seed, length);
        for n in 0..length {
            result[n] = amplitude * result[n]
                + ar.iter()
                    .enumerate()
                    .filter(|(j, _)| n > *j)
                    .map(|(j, a)| a * result[n - j - 1])
                    .sum::<f64>();
        }
        result
    }

    fn objective(
        soft: &[f64],
        bits: &[f64],
        model: &ChannelModel,
        noise: &NoiseModel,
        gain: f64,
        weights: &[f64],
    ) -> f64 {
        let predicted = signal(bits, model, gain);
        let residual: Vec<f64> = soft.iter().zip(predicted).map(|(y, p)| y - p).collect();
        (1 + noise.selected_order..soft.len() - 1)
            .map(|n| {
                let value = residual[n]
                    - noise
                        .ar_coefficients
                        .iter()
                        .enumerate()
                        .map(|(j, a)| a * residual[n - j - 1])
                        .sum::<f64>();
                value * value / weights[n]
            })
            .sum()
    }

    #[test]
    fn exhaustive_short_stream_oracle_all_orders_and_heteroscedastic_weights() {
        let model = channel();
        for ar in [vec![], vec![0.72], vec![0.63, -0.27]] {
            let noise = colored(&ar);
            for length in 5..=10 {
                for seed in 1..=4 {
                    let soft = random(seed, length);
                    let weights: Vec<f64> =
                        (0..length).map(|i| [0.1, 0.4, 1.0, 3.0][i % 4]).collect();
                    for gain in [0.75, 1.0, 1.5] {
                        let decoded =
                            innovations_sequence(&soft, &model, &noise, gain, Some(&weights))
                                .unwrap();
                        let actual = objective(&soft, &decoded, &model, &noise, gain, &weights);
                        let optimal = (0..1usize << length)
                            .map(|mask| {
                                let candidate: Vec<f64> = (0..length)
                                    .map(|i| 2.0 * ((mask >> i) & 1) as f64 - 1.0)
                                    .collect();
                                objective(&soft, &candidate, &model, &noise, gain, &weights)
                            })
                            .fold(f64::INFINITY, f64::min);
                        assert!(
                            (actual - optimal).abs() < 1e-10,
                            "order={} len={length} seed={seed} gain={gain}: {actual} != {optimal}",
                            ar.len()
                        );
                    }
                }
            }
        }
    }

    #[test]
    fn ar_zero_exactly_preserves_original_decisions_including_ties_and_gains() {
        let model = channel();
        let noise = NoiseModel::white(0.01).unwrap();
        for length in [3, 4, 127, 4096] {
            for samples in [vec![0.0; length], random(91, length)] {
                for gain in [0.75, 1.0, 1.5] {
                    let expected =
                        crate::sequence::detect_sequence(&samples, &model, gain).unwrap();
                    assert_eq!(
                        innovations_sequence(&samples, &model, &noise, gain, None).unwrap(),
                        expected
                    );
                    assert_eq!(
                        innovations_sequence(
                            &samples,
                            &model,
                            &noise,
                            gain,
                            Some(&vec![3.0; length])
                        )
                        .unwrap(),
                        expected
                    );
                }
            }
        }
    }

    #[test]
    fn independent_colored_residual_training_recovers_more_known_bits() {
        let model = channel();
        let spans = vec![
            residuals(1729, 8192, &[0.9], 0.37),
            residuals(993, 8192, &[0.9], 0.37),
        ];
        let noise = fit_noise_model(&spans).unwrap();
        assert_eq!(noise.selected_order, 1);
        assert!((noise.ar_coefficients[0] - 0.9).abs() < 0.03);
        assert!(noise.heldout_variance_ratio < 0.3);
        let bits: Vec<f64> = random(31, 8192)
            .iter()
            .map(|v| if *v < 0.0 { -1.0 } else { 1.0 })
            .collect();
        let mut received = signal(&bits, &model, 1.0);
        for (sample, error) in received.iter_mut().zip(residuals(8761, 8192, &[0.9], 0.37)) {
            *sample += error;
        }
        let old = crate::sequence::detect_sequence(&received, &model, 1.0).unwrap();
        let new = innovations_sequence(&received, &model, &noise, 1.0, None).unwrap();
        let old_errors = (8..bits.len() - 8).filter(|i| old[*i] != bits[*i]).count();
        let new_errors = (8..bits.len() - 8).filter(|i| new[*i] != bits[*i]).count();
        assert!(old_errors > 30, "old errors {old_errors}");
        assert!(
            new_errors * 3 < old_errors,
            "old={old_errors} new={new_errors}"
        );
    }

    #[test]
    fn second_order_coloration_is_selected_and_white_control_stays_white() {
        let second = fit_noise_model(&[residuals(501, 12000, &[0.75, -0.6], 0.3)]).unwrap();
        assert_eq!(second.selected_order, 2);
        assert!((second.ar_coefficients[0] - 0.75).abs() < 0.04);
        assert!((second.ar_coefficients[1] + 0.6).abs() < 0.04);
        for seed in 1..=12 {
            let white = fit_noise_model(&[random(seed * 337, 4096)]).unwrap();
            assert_eq!(
                white.selected_order, 0,
                "seed {seed} selected {}",
                white.selected_order
            );
        }
    }

    #[test]
    fn span_reordering_is_numerically_equivalent_and_boundaries_are_not_joined() {
        let mut a = residuals(789, 512, &[0.83], 0.3);
        let mut b = residuals(5127, 512, &[0.83], 0.3);
        // The final two validation samples of A cannot become training lags in B.
        let first = fit_noise_model(&[a.clone(), b.clone()]).unwrap();
        a[510] = 3.0;
        a[511] = -3.0;
        let boundary = fit_noise_model(&[a.clone(), b.clone()]).unwrap();
        for (before, after) in first.candidates.iter().zip(&boundary.candidates) {
            for (x, y) in before.ar_coefficients.iter().zip(&after.ar_coefficients) {
                assert!((x - y).abs() < 1e-12);
            }
        }
        let reordered = fit_noise_model(&[b.clone(), a.clone()]).unwrap();
        assert_eq!(boundary.selected_order, reordered.selected_order);
        for (x, y) in boundary
            .ar_coefficients
            .iter()
            .zip(&reordered.ar_coefficients)
        {
            assert!((x - y).abs() < 1e-12);
        }
        // First validation lag rows skip the split; changing last training value
        // must not directly enter a fixed candidate's validation prediction.
        let split = b.len() * 2 / 3;
        let ar = [0.5, -0.2];
        let score_before: f64 = (split + 2..b.len())
            .map(|n| squared_residual(&b, n, &ar, 1.0))
            .sum();
        b[split - 1] = 1e3;
        let score_after: f64 = (split + 2..b.len())
            .map(|n| squared_residual(&b, n, &ar, 1.0))
            .sum();
        assert_eq!(score_before, score_after);
    }

    #[test]
    fn validation_rejects_nontransferable_training_coloration() {
        let mut span = residuals(271, 6000, &[0.9], 0.2);
        let split = span.len() * 2 / 3;
        span[split..].copy_from_slice(&random(9121, 6000 - split));
        let fitted = fit_noise_model(&[span]).unwrap();
        assert_eq!(fitted.selected_order, 0);
        assert!(fitted.candidates[1].heldout_variance_ratio > 1.0);
        let dc = fit_noise_model(&[vec![1.0; 600]]).unwrap();
        assert_eq!(dc.selected_order, 0);
        assert_eq!(dc.fit_status, "white_fallback_mean_dominated");
    }

    #[test]
    fn scale_invariance_stability_and_common_variance_cancel() {
        let spans = vec![residuals(1991, 4096, &[0.83], 0.3)];
        let fitted = fit_noise_model(&spans).unwrap();
        let scaled: Vec<Vec<f64>> = spans
            .iter()
            .map(|s| s.iter().map(|v| v * 1e80).collect())
            .collect();
        let large = fit_noise_model(&scaled).unwrap();
        assert_eq!(fitted.selected_order, large.selected_order);
        for (x, y) in fitted.ar_coefficients.iter().zip(&large.ar_coefficients) {
            assert!((x - y).abs() < 1e-12);
        }
        let soft = random(199, 513);
        let expected = innovations_sequence(&soft, &channel(), &fitted, 1.0, None).unwrap();
        let mut varied = fitted.clone();
        varied.innovation_variance *= 1000.0;
        assert_eq!(
            innovations_sequence(&soft, &channel(), &varied, 1.0, None).unwrap(),
            expected
        );
        let mut large_channel = channel();
        large_channel.taps = large_channel.taps.map(|v| v * 1e80);
        large_channel.bias *= 1e80;
        let large_soft: Vec<f64> = soft.iter().map(|v| v * 1e80).collect();
        assert_eq!(
            innovations_sequence(&large_soft, &large_channel, &large, 1.0, None).unwrap(),
            expected
        );
        for ar in [vec![1.0], vec![2.0, -1.0], vec![0.0, -1.0]] {
            assert!(validate_noise(&colored(&ar)).is_err());
        }
    }

    #[test]
    fn malformed_nonfinite_and_allocation_limits_are_rejected() {
        for spans in [
            vec![],
            vec![vec![]],
            vec![vec![0.0; 1024]],
            vec![vec![1.0; 100]],
            vec![vec![1.0; MAX_INNOVATION_SYMBOLS + 1]],
            vec![vec![1.0; 32]; MAX_NOISE_SPANS + 1],
        ] {
            assert!(fit_noise_model(&spans).is_err());
        }
        for value in [f64::NAN, f64::INFINITY, f64::NEG_INFINITY, 1e101] {
            let mut samples = vec![0.2; 512];
            samples[300] = value;
            assert!(fit_noise_model(&[samples.clone()]).is_err());
            assert!(
                innovations_sequence(&samples, &channel(), &colored(&[0.3]), 1.0, None).is_err()
            );
        }
        for ar in [
            vec![0.1, 0.2, 0.3],
            vec![f64::NAN],
            vec![2.0],
            vec![0.2, 1.1],
        ] {
            assert!(
                innovations_sequence(&[0.0; 16], &channel(), &colored(&ar), 1.0, None).is_err()
            );
        }
        for length in [0, 1, 2, 3, 4] {
            assert!(
                innovations_sequence(
                    &vec![0.0; length],
                    &channel(),
                    &colored(&[0.4, -0.1]),
                    1.0,
                    None
                )
                .is_err()
            );
        }
        assert!(
            innovations_sequence(
                &vec![0.0; MAX_INNOVATION_SYMBOLS + 1],
                &channel(),
                &colored(&[]),
                1.0,
                None
            )
            .is_err()
        );
        for weights in [
            vec![1.0; 15],
            vec![0.0; 16],
            vec![f64::NAN; 16],
            vec![101.0; 16],
            vec![-1.0; 16],
        ] {
            assert!(
                innovations_sequence(&[0.0; 16], &channel(), &colored(&[]), 1.0, Some(&weights))
                    .is_err()
            );
        }
        for gain in [0.0, -1.0, 1e-7, 1e7, f64::NAN] {
            assert!(
                innovations_sequence(&[0.0; 16], &channel(), &colored(&[0.3]), gain, None).is_err()
            );
        }
        let mut malformed = colored(&[0.3]);
        malformed.selected_order = 2;
        assert!(innovations_sequence(&[0.0; 16], &channel(), &malformed, 1.0, None).is_err());
        let mut malformed = channel();
        malformed.taps[1] = -0.9;
        assert!(innovations_sequence(&[0.0; 16], &malformed, &colored(&[0.3]), 1.0, None).is_err());
    }

    #[test]
    fn weighted_detector_and_native_crc_gate_reject_bounded_noise_controls() {
        // A functional negative test of the symbol detector + native acceptance
        // gate, NOT a calibrated whole-receiver false-alarm bound. Models come
        // from independent source residuals, never from the target noise/CRC.
        let models = [
            NoiseModel::white(1.0).unwrap(),
            fit_noise_model(&[residuals(17881, 8192, &[0.85], 0.3)]).unwrap(),
            fit_noise_model(&[residuals(51791, 8192, &[0.75, -0.6], 0.3)]).unwrap(),
        ];
        assert_eq!(models[1].selected_order, 1);
        assert_eq!(models[2].selected_order, 2);
        let multipliers: Vec<f64> = (0..8192)
            .map(|n| [0.25, 0.5, 1.0, 4.0][(n / 43) % 4])
            .collect();
        for seed in 1..=8 {
            let white = random(seed * 7981, 8192);
            let colored_noise = residuals(seed * 97871, 8192, &[0.9], 0.3);
            let impulses: Vec<f64> = random(seed * 65771, 8192)
                .iter()
                .enumerate()
                .map(|(n, value)| value * if n % 97 < 3 { 5.0 } else { 0.05 })
                .collect();
            for soft in [&white, &colored_noise, &impulses] {
                for noise in &models {
                    for weights in [None, Some(multipliers.as_slice())] {
                        let decoded =
                            innovations_sequence(soft, &channel(), noise, 1.0, weights).unwrap();
                        assert!(
                            crate::anchors::validated_spans(&decoded, 0.0)
                                .unwrap()
                                .is_empty(),
                            "unexpected CRC-valid UI candidate: seed={seed}, order={}, weighted={}",
                            noise.selected_order,
                            weights.is_some()
                        );
                    }
                }
            }
        }
    }
}
