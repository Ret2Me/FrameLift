//! Experimental source-only uncertainty-aware three-tap sequence detection.
//!
//! The point estimate is exactly [`crate::sequence::fit_channel`]. Parameter
//! covariance is a 32-symbol cluster-sandwich estimate of its residual scores,
//! not a calibrated Bayesian posterior. The decoder sums local Gaussian
//! predictive costs; this is *not* exact marginalization of the shared unknown
//! channel, which would correlate observations. Neither fit nor detection
//! receives target bits, packet contents, CRCs, or protocol hints. The caller
//! must enforce source/target separation. This module is not a default receiver.

use crate::sequence::{self, ChannelModel};
use serde::{Deserialize, Serialize};

pub const COVARIANCE_BLOCK_SYMBOLS: usize = 32;
pub const MAX_COVARIANCE_STRENGTH: f64 = 16.0;
const RIDGE: f64 = 1.0e-6;
const MAX_ABS_SOFT: f64 = 1.0e100;
const MAX_ABS_VARIANCE: f64 = 1.0e200;
const MIN_VARIANCE: f64 = 1.0e-200;
const NORMALIZED_VARIANCE_FLOOR: f64 = 1.0e-12;
const PSD_ROUNDOFF_TOLERANCE: f64 = 128.0 * f64::EPSILON;

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct CovarianceDiagnostics {
    pub estimator: String,
    pub block_symbols: usize,
    pub training_blocks: usize,
    pub residual_degrees_of_freedom: usize,
    pub training_scale: f64,
    pub normalized_noise_variance: f64,
    pub finite_sample_factor: f64,
    /// False: residual stationarity, block independence, and out-of-source
    /// uncertainty calibration have not been established by this estimator.
    pub calibration_established: bool,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct UncertainChannel {
    pub model: ChannelModel,
    /// Covariance in physical amplitude-squared units. Parameter order is
    /// `[bias, left tap, center tap, right tap]`.
    pub covariance: [[f64; 4]; 4],
    /// Source residual variance, before the detector's explicit gain scaling.
    pub noise_variance: f64,
    pub diagnostics: CovarianceDiagnostics,
}

fn regressors(levels: &[u8], index: usize) -> [f64; 4] {
    [
        1.0,
        2.0 * f64::from(levels[index - 1]) - 1.0,
        2.0 * f64::from(levels[index]) - 1.0,
        2.0 * f64::from(levels[index + 1]) - 1.0,
    ]
}

/// Called only after fit_channel validated the same arrays and spans.
fn guarded_interiors(spans: &[(usize, usize)]) -> Vec<(usize, usize)> {
    let guard = sequence::TRAINING_GUARD_SYMBOLS;
    let mut interiors: Vec<_> = spans
        .iter()
        .filter(|&&(start, end)| end - start > 2 * guard)
        .map(|&(start, end)| (start + guard, end - guard))
        .collect();
    interiors.sort_unstable();
    let mut merged: Vec<(usize, usize)> = Vec::with_capacity(interiors.len());
    for (start, end) in interiors {
        if let Some(previous) = merged.last_mut()
            && start <= previous.1
        {
            previous.1 = previous.1.max(end);
        } else {
            merged.push((start, end));
        }
    }
    merged
}

fn inverse_positive_definite(matrix: [[f64; 4]; 4]) -> Result<[[f64; 4]; 4], String> {
    let mut lower = [[0.0; 4]; 4];
    for row in 0..4 {
        for column in 0..=row {
            let mut value = matrix[row][column];
            for (a, b) in lower[row][..column].iter().zip(&lower[column][..column]) {
                value -= a * b;
            }
            if row == column {
                if !value.is_finite() || value <= 0.0 {
                    return Err("uncertainty fit has a singular regression matrix".into());
                }
                lower[row][column] = value.sqrt();
            } else {
                lower[row][column] = value / lower[column][column];
            }
        }
    }
    let mut inverse = [[0.0; 4]; 4];
    #[expect(
        clippy::needless_range_loop,
        reason = "column-wise triangular solve reads previously solved rows"
    )]
    for rhs_column in 0..4 {
        let mut intermediate = [0.0; 4];
        for row in 0..4 {
            let mut value = if row == rhs_column { 1.0 } else { 0.0 };
            for k in 0..row {
                value -= lower[row][k] * intermediate[k];
            }
            intermediate[row] = value / lower[row][row];
        }
        for row in (0..4).rev() {
            let mut value = intermediate[row];
            for k in row + 1..4 {
                value -= lower[k][row] * inverse[k][rhs_column];
            }
            inverse[row][rhs_column] = value / lower[row][row];
        }
    }
    Ok(inverse)
}

/// Fit the unchanged guarded point model and a block-cluster covariance.
///
/// Let `N` be distinct guarded symbols, `K` the number of contiguous blocks,
/// `G = X'X/N + diag(0, 1e-6, 1e-6, 1e-6)`, and `s_k = sum(phi_n * r_n)`.
/// The covariance is `CR1/N^2 * sum((G^-1 s_k)(G^-1 s_k)')`, with
/// `CR1 = K/(K-1) * (N-1)/(N-4)`. Blocks contain at most 32 consecutive source
/// symbols and never bridge a gap. Overlapping guarded spans merge before both
/// fitting and blocking, so duplicate spans cannot increase their weight.
///
/// This cluster-sandwich estimate allows within-block residual correlation but
/// assumes independent clusters. It does not estimate ridge bias, extrapolation
/// error, or between-packet drift. Positive-semidefinite outer products are
/// accumulated explicitly. Source residual noise uses RSS/(N-4), with a fixed
/// normalized floor of 1e-12 and physical-unit safety floor of 1e-200; those
/// modeling choices are reported, not optimized on a
/// target's decoded bits. No calibration guarantee is implied.
pub fn fit(
    soft: &[f64],
    levels: &[u8],
    spans: &[(usize, usize)],
) -> Result<UncertainChannel, String> {
    let model = sequence::fit_channel(soft, levels, spans).map_err(|error| {
        // These are expected model/data quality gates. Malformed arrays/spans,
        // arithmetic failures, and invalid bounds deliberately remain errors
        // without this prefix so a pilot cannot hide them as absent coverage.
        if error.starts_with("channel training needs at least")
            || error.starts_with("channel training has no")
            || error == "channel training regressors are rank deficient"
            || error == "channel model residual exceeds the quality gate"
            || error == "channel model lacks a positive, sufficiently dominant center tap"
        {
            format!("uncertainty rejected: {error}")
        } else {
            error
        }
    })?;
    let interiors = guarded_interiors(spans);
    let scale = interiors
        .iter()
        .flat_map(|&(start, end)| &soft[start..end])
        .fold(0.0_f64, |largest, value| largest.max(value.abs()));
    let mean = [
        model.bias / scale,
        model.taps[0] / scale,
        model.taps[1] / scale,
        model.taps[2] / scale,
    ];
    let count = model.training_symbols;
    let count_f = count as f64;
    let mut gram = [[0.0; 4]; 4];
    for &(start, end) in &interiors {
        for index in start..end {
            let phi = regressors(levels, index);
            for row in 0..4 {
                for column in 0..4 {
                    gram[row][column] += phi[row] * phi[column];
                }
            }
        }
    }
    for (row, values) in gram.iter_mut().enumerate() {
        for value in values.iter_mut() {
            *value /= count_f;
        }
        if row > 0 {
            values[row] += RIDGE;
        }
    }
    let bread = inverse_positive_definite(gram)?;
    let mut covariance = [[0.0; 4]; 4];
    let mut squared_error = 0.0;
    let mut blocks = 0_usize;
    for &(start, end) in &interiors {
        for block_start in (start..end).step_by(COVARIANCE_BLOCK_SYMBOLS) {
            let block_end = (block_start + COVARIANCE_BLOCK_SYMBOLS).min(end);
            let mut score = [0.0; 4];
            for (index, sample) in soft.iter().enumerate().take(block_end).skip(block_start) {
                let phi = regressors(levels, index);
                let prediction: f64 = phi.iter().zip(mean).map(|(a, b)| a * b).sum();
                let residual = sample / scale - prediction;
                squared_error += residual * residual;
                for k in 0..4 {
                    score[k] += phi[k] * residual;
                }
            }
            let transformed: [f64; 4] = std::array::from_fn(|row| {
                bread[row]
                    .iter()
                    .zip(score)
                    .map(|(a, b)| a * b)
                    .sum::<f64>()
                    / count_f
            });
            for row in 0..4 {
                for column in 0..=row {
                    covariance[row][column] += transformed[row] * transformed[column];
                }
            }
            blocks += 1;
        }
    }
    if blocks < 2 {
        return Err("uncertainty rejected: fitting requires at least two residual blocks".into());
    }
    let correction = blocks as f64 / (blocks - 1) as f64 * (count - 1) as f64 / (count - 4) as f64;
    let normalized_noise = (squared_error / (count - 4) as f64).max(NORMALIZED_VARIANCE_FLOOR);
    let amplitude_squared = scale * scale;
    #[expect(
        clippy::needless_range_loop,
        reason = "symmetric update writes both triangles in the original arithmetic order"
    )]
    for row in 0..4 {
        for column in 0..=row {
            covariance[row][column] *= correction * amplitude_squared;
            covariance[column][row] = covariance[row][column];
        }
    }
    let result = UncertainChannel {
        model,
        covariance,
        noise_variance: (normalized_noise * amplitude_squared).max(MIN_VARIANCE),
        diagnostics: CovarianceDiagnostics {
            estimator: "cluster-sandwich-ridge-residual-cr1-v1".into(),
            block_symbols: COVARIANCE_BLOCK_SYMBOLS,
            training_blocks: blocks,
            residual_degrees_of_freedom: count - 4,
            training_scale: scale,
            normalized_noise_variance: normalized_noise,
            finite_sample_factor: correction,
            calibration_established: false,
        },
    };
    covariance_factor(&result)?;
    Ok(result)
}

/// Validate serialized input too, then factor C = magnitude * L L'. A pivoted
/// Cholesky decomposition handles semidefinite/rank-deficient matrices. Only signed
/// roundoff at <= 128 epsilon of the largest entry may be projected to zero;
/// materially indefinite matrices, any negative diagonal, and asymmetry are
/// rejected. The returned factor guarantees nonnegative predictive variance.
fn covariance_factor(model: &UncertainChannel) -> Result<(f64, [[f64; 4]; 4]), String> {
    if !model.noise_variance.is_finite()
        || !(MIN_VARIANCE..=MAX_ABS_VARIANCE).contains(&model.noise_variance)
    {
        return Err("source noise variance must be finite and in 1e-200..=1e200".into());
    }
    let mut magnitude = 0.0_f64;
    for row in 0..4 {
        for column in 0..4 {
            let value = model.covariance[row][column];
            if !value.is_finite() || value.abs() > MAX_ABS_VARIANCE {
                return Err("channel covariance must be finite and bounded by 1e200".into());
            }
            if value != model.covariance[column][row] {
                return Err("channel covariance must be symmetric".into());
            }
            magnitude = magnitude.max(value.abs());
        }
        if model.covariance[row][row] < 0.0 {
            return Err("channel covariance must be positive semidefinite".into());
        }
    }
    if magnitude == 0.0 {
        return Ok((0.0, [[0.0; 4]; 4]));
    }
    let mut residual = model
        .covariance
        .map(|row| row.map(|value| value / magnitude));
    let mut factor = [[0.0; 4]; 4];
    let mut remaining = [true; 4];
    #[expect(
        clippy::needless_range_loop,
        reason = "pivoted factorization updates multiple rows of the same column"
    )]
    for column in 0..4 {
        let pivot = (0..4)
            .filter(|&row| remaining[row])
            .max_by(|&a, &b| residual[a][a].total_cmp(&residual[b][b]))
            .unwrap();
        let value = residual[pivot][pivot];
        if value <= PSD_ROUNDOFF_TOLERANCE {
            if (0..4).any(|row| {
                remaining[row]
                    && (0..4).any(|col| {
                        remaining[col] && residual[row][col].abs() > PSD_ROUNDOFF_TOLERANCE
                    })
            }) {
                return Err("channel covariance must be positive semidefinite".into());
            }
            break;
        }
        factor[pivot][column] = value.sqrt();
        for row in 0..4 {
            if remaining[row] && row != pivot {
                factor[row][column] = residual[row][pivot] / factor[pivot][column];
            }
        }
        remaining[pivot] = false;
        for row in 0..4 {
            if remaining[row] {
                for col in 0..=row {
                    if remaining[col] {
                        residual[row][col] -= factor[row][column] * factor[col][column];
                        residual[col][row] = residual[row][col];
                    }
                }
            }
        }
    }
    Ok((magnitude, factor))
}

struct BranchModels {
    scale: f64,
    predictions: [[f64; 4]; 2],
    inverse_variances: [[f64; 4]; 2],
    log_variances: [[f64; 4]; 2],
}

fn branch_models(
    soft: &[f64],
    model: &UncertainChannel,
    gain: f64,
    strength: f64,
    covariance_magnitude: f64,
    factor: [[f64; 4]; 4],
) -> Result<BranchModels, String> {
    let scaled_taps = model.model.taps.map(|tap| gain * tap);
    let scaled_bias = gain * model.model.bias;
    let covariance_sd = covariance_magnitude.sqrt() * gain;
    let noise_sd = model.noise_variance.sqrt() * gain;
    let scale = soft
        .iter()
        .chain(scaled_taps.iter())
        .copied()
        .chain([scaled_bias, covariance_sd, noise_sd])
        .fold(0.0_f64, |largest, value| largest.max(value.abs()));
    let taps = scaled_taps.map(|tap| tap / scale);
    let bias = scaled_bias / scale;
    let noise = (noise_sd / scale).powi(2);
    let covariance_scale = strength * (covariance_sd / scale).powi(2);
    if taps[1] <= 0.0 || !noise.is_finite() || noise < 1.0e-290 {
        return Err("uncertainty input/model dynamic range is not representable".into());
    }
    let mut result = BranchModels {
        scale,
        predictions: [[0.0; 4]; 2],
        inverse_variances: [[0.0; 4]; 2],
        log_variances: [[0.0; 4]; 2],
    };
    for previous_bit in 0..2 {
        for state in 0..4 {
            let phi = [
                1.0,
                2.0 * previous_bit as f64 - 1.0,
                2.0 * (state >> 1) as f64 - 1.0,
                2.0 * (state & 1) as f64 - 1.0,
            ];
            result.predictions[previous_bit][state] =
                bias + taps[0] * phi[1] + taps[1] * phi[2] + taps[2] * phi[3];
            let quadratic: f64 = (0..4)
                .map(|column| {
                    (0..4)
                        .map(|row| phi[row] * factor[row][column])
                        .sum::<f64>()
                        .powi(2)
                })
                .sum();
            let variance = noise + covariance_scale * quadratic;
            if !variance.is_finite() || variance <= 0.0 {
                return Err("uncertainty branch variance is not finite and positive".into());
            }
            result.inverse_variances[previous_bit][state] = 1.0 / variance;
            result.log_variances[previous_bit][state] = variance.ln();
        }
    }
    Ok(result)
}

/// Deterministic four-state, full-traceback local-predictive detector.
///
/// For fixed source-only `theta, C, R`, a branch with bipolar regressors `phi`
/// uses `mu = gain * phi' theta`, `S = gain^2 * (R + strength * phi' C phi)`
/// and `(y-mu)^2/S + log(S)`. In particular gain scales source noise as well as
/// signal/covariance. A common amplitude normalization removes only a
/// path-independent logarithmic constant and bounds numerical arithmetic.
/// No target-dependent channel updates or target covariance fitting occur.
///
/// Free initial/final states, excluded first/last observations, full traceback,
/// predecessor/final-state ties, and hard bipolar output follow sequence.rs.
/// Strength zero, or identically zero covariance, delegates directly to the
/// existing detector, preserving its exact floating-point decisions rather than
/// relying on algebraic equality after variance normalization.
pub fn detect(
    soft: &[f64],
    model: &UncertainChannel,
    gain: f64,
    covariance_strength: f64,
) -> Result<Vec<f64>, String> {
    if !covariance_strength.is_finite()
        || !(0.0..=MAX_COVARIANCE_STRENGTH).contains(&covariance_strength)
    {
        return Err("covariance strength must be finite and in 0..=16".into());
    }
    if !(3..=sequence::MAX_SEQUENCE_SYMBOLS).contains(&soft.len())
        || soft
            .iter()
            .any(|value| !value.is_finite() || value.abs() > MAX_ABS_SOFT)
    {
        return Err("uncertainty detector needs 3..=4194304 finite bounded symbols".into());
    }
    let (magnitude, factor) = covariance_factor(model)?;
    if covariance_strength == 0.0 || magnitude == 0.0 {
        return sequence::detect_sequence(soft, &model.model, gain);
    }
    // Reuse the authoritative model/gain gate instead of silently drifting from
    // it. Three observations suffice for validation, not training or selection.
    sequence::detect_sequence(&soft[..3], &model.model, gain)?;
    let branches = branch_models(soft, model, gain, covariance_strength, magnitude, factor)?;
    let mut traceback = vec![[0_u8; 4]; soft.len() - 2];
    let mut costs = [0.0_f64; 4];
    for (step, predecessors) in traceback.iter_mut().enumerate() {
        let observation = soft[step + 1] / branches.scale;
        let mut next_costs = [0.0; 4];
        for state in 0..4 {
            let previous_low = state >> 1;
            let previous_high = previous_low | 2;
            let low_cost = costs[previous_low]
                + ((observation - branches.predictions[0][state]).powi(2)
                    * branches.inverse_variances[0][state]
                    + branches.log_variances[0][state]);
            let high_cost = costs[previous_high]
                + ((observation - branches.predictions[1][state]).powi(2)
                    * branches.inverse_variances[1][state]
                    + branches.log_variances[1][state]);
            if low_cost <= high_cost {
                next_costs[state] = low_cost;
                predecessors[state] = previous_low as u8;
            } else {
                next_costs[state] = high_cost;
                predecessors[state] = previous_high as u8;
            }
        }
        costs = next_costs;
    }
    let mut state = 0;
    for candidate in 1..4 {
        if costs[candidate] < costs[state] {
            state = candidate;
        }
    }
    let mut levels = vec![-1.0; soft.len()];
    levels[soft.len() - 1] = 2.0 * (state & 1) as f64 - 1.0;
    for step in (0..traceback.len()).rev() {
        levels[step + 1] = 2.0 * (state >> 1) as f64 - 1.0;
        state = usize::from(traceback[step][state]);
    }
    levels[0] = 2.0 * (state >> 1) as f64 - 1.0;
    Ok(levels)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn fixture(count: usize) -> (Vec<f64>, Vec<u8>) {
        let mut rng = 0x9876_5432_u64;
        let levels: Vec<u8> = (0..count)
            .map(|_| {
                rng ^= rng << 13;
                rng ^= rng >> 7;
                rng ^= rng << 17;
                (rng & 1) as u8
            })
            .collect();
        let mut soft: Vec<f64> = levels
            .iter()
            .map(|&bit| 2.0 * f64::from(bit) - 1.0)
            .collect();
        for index in 1..count - 1 {
            let phi = regressors(&levels, index);
            soft[index] = 0.08 + 0.22 * phi[1] + 0.91 * phi[2] - 0.16 * phi[3]
                + 0.05 * (index as f64 * 0.193).sin();
        }
        (soft, levels)
    }

    fn model() -> UncertainChannel {
        let (soft, levels) = fixture(512);
        fit(&soft, &levels, &[(0, soft.len())]).unwrap()
    }

    fn bits_to_levels(bits: usize, count: usize) -> Vec<f64> {
        (0..count)
            .map(|index| 2.0 * ((bits >> index) & 1) as f64 - 1.0)
            .collect()
    }

    // Independent direct covariance quadratic, physical-unit formula. Exhaustive
    // enumeration checks the objective rather than sharing the detector's
    // factorization, normalization, branch tables, or traceback implementation.
    fn objective(
        soft: &[f64],
        levels: &[f64],
        m: &UncertainChannel,
        gain: f64,
        strength: f64,
    ) -> f64 {
        let theta = [
            m.model.bias,
            m.model.taps[0],
            m.model.taps[1],
            m.model.taps[2],
        ];
        (1..soft.len() - 1)
            .map(|index| {
                let phi = [1.0, levels[index - 1], levels[index], levels[index + 1]];
                let mean: f64 = phi.iter().zip(theta).map(|(a, b)| a * b).sum::<f64>() * gain;
                let covariance: f64 = (0..4)
                    .map(|row| {
                        (0..4)
                            .map(|column| phi[row] * m.covariance[row][column] * phi[column])
                            .sum::<f64>()
                    })
                    .sum();
                let variance = gain * gain * (m.noise_variance + strength * covariance);
                (soft[index] - mean).powi(2) / variance + variance.ln()
            })
            .sum()
    }

    #[test]
    fn fitted_mean_is_exact_and_duplicate_spans_do_not_overweight() {
        let (soft, levels) = fixture(512);
        let one = fit(&soft, &levels, &[(0, 512)]).unwrap();
        let duplicated = fit(&soft, &levels, &[(0, 512), (0, 512), (40, 400)]).unwrap();
        let point = sequence::fit_channel(&soft, &levels, &[(0, 512)]).unwrap();
        assert_eq!(
            serde_json::to_value(&one.model).unwrap(),
            serde_json::to_value(point).unwrap()
        );
        assert_eq!(
            serde_json::to_value(&one).unwrap(),
            serde_json::to_value(duplicated).unwrap()
        );
        assert!(!one.diagnostics.calibration_established);
        assert_eq!(one.model.training_symbols, 472);
        assert_eq!(one.diagnostics.training_blocks, 15);
    }

    #[test]
    fn strength_zero_and_zero_covariance_are_bit_exact_baseline() {
        let (soft, _) = fixture(512);
        let mut model = model();
        for gain in [0.75, 1.0, 1.5] {
            let baseline = sequence::detect_sequence(&soft, &model.model, gain).unwrap();
            assert_eq!(detect(&soft, &model, gain, 0.0).unwrap(), baseline);
            let original = model.covariance;
            model.covariance = [[0.0; 4]; 4];
            assert_eq!(detect(&soft, &model, gain, 16.0).unwrap(), baseline);
            model.covariance = original;
        }
    }

    #[test]
    fn exhaustive_oracle_short_sequences_and_gains() {
        let mut m = model();
        // Nontrivial off-diagonal PSD covariance with pattern-dependent variance.
        let a = [
            [0.03, 0.01, 0.0, 0.0],
            [0.02, 0.06, 0.0, 0.0],
            [-0.01, 0.02, 0.07, 0.0],
            [0.0, -0.03, 0.01, 0.04],
        ];
        m.covariance = std::array::from_fn(|r| {
            std::array::from_fn(|c| (0..4).map(|k| a[r][k] * a[c][k]).sum())
        });
        m.noise_variance = 0.11;
        for count in 3..=9 {
            for seed in 0..8 {
                let soft: Vec<_> = (0..count)
                    .map(|n| ((n * 17 + seed * 23) as f64 * 0.193).sin() * 1.3)
                    .collect();
                for gain in [0.75, 1.0, 1.5] {
                    for strength in [0.25, 1.0, 4.0, 16.0] {
                        let actual = detect(&soft, &m, gain, strength).unwrap();
                        let best = (0..1 << count)
                            .map(|bits| {
                                objective(&soft, &bits_to_levels(bits, count), &m, gain, strength)
                            })
                            .fold(f64::INFINITY, f64::min);
                        let actual_cost = objective(&soft, &actual, &m, gain, strength);
                        assert!(
                            (actual_cost - best).abs() < 1.0e-9,
                            "count={count} seed={seed} gain={gain} strength={strength}: {actual_cost} != {best}"
                        );
                    }
                }
            }
        }
    }

    #[test]
    fn clean_source_independent_target_recovers_interior_deterministically() {
        let (soft, expected) = fixture(700);
        let m = fit(&soft, &expected, &[(0, 240)]).unwrap();
        let target = &soft[300..650];
        let decoded = detect(target, &m, 1.0, 4.0).unwrap();
        assert_eq!(decoded, detect(target, &m, 1.0, 4.0).unwrap());
        let expected: Vec<f64> = expected[301..649]
            .iter()
            .map(|&bit| 2.0 * f64::from(bit) - 1.0)
            .collect();
        assert_eq!(&decoded[1..decoded.len() - 1], expected);
    }

    #[test]
    fn rejects_hostile_covariance_noise_gain_strength_and_symbols() {
        let (soft, _) = fixture(200);
        let m = model();
        for invalid in [f64::NAN, f64::INFINITY, -1.0, 1.0e201] {
            let mut bad = m.clone();
            bad.covariance[0][0] = invalid;
            assert!(detect(&soft, &bad, 1.0, 1.0).is_err());
            bad = m.clone();
            bad.noise_variance = invalid;
            assert!(detect(&soft, &bad, 1.0, 1.0).is_err());
        }
        let mut bad = m.clone();
        bad.covariance[0][1] += 0.01;
        assert!(detect(&soft, &bad, 1.0, 1.0).is_err());
        bad.covariance = [[0.0; 4]; 4];
        bad.covariance[0][0] = 1.0;
        bad.covariance[1][1] = 1.0;
        bad.covariance[0][1] = 2.0;
        bad.covariance[1][0] = 2.0;
        assert!(detect(&soft, &bad, 1.0, 1.0).is_err());
        for invalid in [f64::NAN, f64::INFINITY, -1.0, 17.0] {
            assert!(detect(&soft, &m, 1.0, invalid).is_err());
        }
        for invalid in [f64::NAN, f64::INFINITY, -1.0, 0.0, 1.0e7] {
            assert!(detect(&soft, &m, invalid, 1.0).is_err());
        }
        assert!(detect(&[], &m, 1.0, 1.0).is_err());
        assert!(detect(&[f64::NAN, 0.0, 1.0], &m, 1.0, 1.0).is_err());
        assert!(detect(&[1.0e101, 0.0, 1.0], &m, 1.0, 1.0).is_err());
    }

    #[test]
    fn rank_one_covariance_and_extreme_valid_scale_are_supported() {
        let (soft, _) = fixture(256);
        let mut m = model();
        let direction = [1.0, 2.0, -1.0, 0.5];
        m.covariance =
            std::array::from_fn(|r| std::array::from_fn(|c| direction[r] * direction[c] * 0.001));
        let reference = detect(&soft, &m, 1.0, 1.0).unwrap();
        let amplitude = 1.0e60;
        m.model.bias *= amplitude;
        m.model.taps = m.model.taps.map(|value| value * amplitude);
        m.noise_variance *= amplitude * amplitude;
        m.covariance = m
            .covariance
            .map(|row| row.map(|value| value * amplitude * amplitude));
        let scaled = soft
            .iter()
            .map(|value| value * amplitude)
            .collect::<Vec<_>>();
        assert_eq!(detect(&scaled, &m, 1.0, 1.0).unwrap(), reference);
    }

    #[test]
    fn source_validation_is_inherited_and_unrepresentable_target_is_rejected() {
        let (soft, levels) = fixture(200);
        assert!(fit(&soft, &levels, &[(0, 100)]).is_err());
        assert!(fit(&soft, &levels, &[(100, 90)]).is_err());
        assert!(fit(&soft, &vec![0; 200], &[(0, 200)]).is_err());
        let mut m = model();
        m.noise_variance = MIN_VARIANCE;
        assert!(detect(&[1.0e100, 1.0e100, 1.0e100], &m, 1.0, 1.0).is_err());
    }

    #[test]
    fn serialized_round_trip_retains_exact_decisions() {
        let m = model();
        let (soft, _) = fixture(400);
        let serialized = serde_json::to_string(&m).unwrap();
        let restored: UncertainChannel = serde_json::from_str(&serialized).unwrap();
        assert_eq!(
            detect(&soft, &m, 0.75, 4.0).unwrap(),
            detect(&soft, &restored, 0.75, 4.0).unwrap()
        );
    }

    #[test]
    fn covariance_fit_respects_gaps_guards_and_amplitude_units() {
        let (soft, levels) = fixture(600);
        let spans = [(0, 200), (260, 460)];
        let m = fit(&soft, &levels, &spans).unwrap();
        assert_eq!(m.model.training_symbols, 320);
        assert_eq!(m.diagnostics.training_blocks, 10);
        let mut unrelated = soft.clone();
        unrelated[210..250].fill(1.0e60);
        unrelated[500..550].fill(-1.0e60);
        let unchanged = fit(&unrelated, &levels, &spans).unwrap();
        assert_eq!(
            serde_json::to_value(&m).unwrap(),
            serde_json::to_value(unchanged).unwrap()
        );

        let amplitude = 1.0e40;
        let scaled: Vec<_> = soft.iter().map(|value| value * amplitude).collect();
        let scaled_model = fit(&scaled, &levels, &spans).unwrap();
        let relative_noise = scaled_model.noise_variance / (amplitude * amplitude);
        assert!((relative_noise - m.noise_variance).abs() < m.noise_variance * 1.0e-10);
        for row in 0..4 {
            for column in 0..4 {
                let relative_covariance =
                    scaled_model.covariance[row][column] / (amplitude * amplitude);
                assert!((relative_covariance - m.covariance[row][column]).abs() < 1.0e-14);
            }
        }
        assert_eq!(
            detect(&soft, &m, 1.0, 4.0).unwrap(),
            detect(&scaled, &scaled_model, 1.0, 4.0).unwrap()
        );
    }

    #[test]
    fn psd_gate_checks_full_matrix_not_just_diagonals_or_pairs() {
        let mut m = model();
        // Positive diagonal and positive two-by-two principal minors do not
        // imply a PSD three-by-three principal minor.
        m.covariance = [
            [1.0, 0.9, 0.9, 0.0],
            [0.9, 1.0, -0.9, 0.0],
            [0.9, -0.9, 1.0, 0.0],
            [0.0, 0.0, 0.0, 0.0],
        ];
        assert!(covariance_factor(&m).is_err());
        m.covariance = [[0.0; 4]; 4];
        m.covariance[0][1] = 0.1;
        m.covariance[1][0] = 0.1;
        m.covariance[1][1] = 1.0;
        assert!(covariance_factor(&m).is_err());

        // A rank-deficient PSD matrix with a zero row must still be accepted.
        m.covariance = [
            [1.0, 0.0, -2.0, 0.0],
            [0.0, 0.0, 0.0, 0.0],
            [-2.0, 0.0, 4.0, 0.0],
            [0.0, 0.0, 0.0, 3.0],
        ];
        let (magnitude, factor) = covariance_factor(&m).unwrap();
        for row in 0..4 {
            for column in 0..4 {
                let reconstructed = magnitude
                    * (0..4)
                        .map(|k| factor[row][k] * factor[column][k])
                        .sum::<f64>();
                assert!((reconstructed - m.covariance[row][column]).abs() < 1.0e-12);
            }
        }
    }

    #[test]
    fn large_finite_costs_do_not_overflow_or_produce_nonbinary_output() {
        let mut m = model();
        m.noise_variance = 1.0e-80;
        m.covariance = [[0.0; 4]; 4];
        for index in 0..4 {
            m.covariance[index][index] = 1.0e-100;
        }
        let soft = vec![1.0e100; 2048];
        let decoded = detect(&soft, &m, 1.0, 1.0).unwrap();
        assert!(decoded.iter().all(|value| *value == -1.0 || *value == 1.0));
        assert_eq!(decoded, detect(&soft, &m, 1.0, 1.0).unwrap());
    }
}
