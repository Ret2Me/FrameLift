//! Experimental, payload-independent, symbol-rate channel estimation and MLSE.
//!
//! A caller supplies already sampled soft symbols and trustworthy hard-level
//! spans from a *different* packet/window. This module knows nothing about CRCs,
//! payload bytes, radio protocols, or how the caller selected those spans. In
//! particular, sequence detection is never conditioned on a checksum result.
//! The channel model is a deliberately bounded three-tap approximation, not a
//! coherent-IQ demodulator or a general replacement for a modulation frontend.

use serde::{Deserialize, Serialize};

/// Keep allocations and quadratic-cost arithmetic bounded for untrusted input.
pub const MAX_SEQUENCE_SYMBOLS: usize = 4_194_304;
pub const TRAINING_GUARD_SYMBOLS: usize = 20;
pub const MIN_TRAINING_SYMBOLS: usize = 128;
const MAX_TRAINING_SPANS: usize = 100_000;
const MAX_ABS_VALUE: f64 = 1.0e100;
const MAX_NORMALIZED_MSE: f64 = 0.5;
const RIDGE: f64 = 1.0e-6;

/// Symbol-rate model, with bipolar levels `x = 2*bit - 1`:
///
/// ```text
/// y[n] = bias + taps[0]*x[n-1] + taps[1]*x[n] + taps[2]*x[n+1]
/// ```
///
/// Only these aggregate signal parameters need cross-packet transfer. Training
/// symbols and their values are deliberately not stored in the model.
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct ChannelModel {
    pub taps: [f64; 3],
    pub bias: f64,
    pub training_symbols: usize,
    /// Training residual mean-square error divided by centered signal variance.
    pub normalized_mse: f64,
}

fn validate_soft(soft: &[f64]) -> Result<(), String> {
    if soft.is_empty() || soft.len() > MAX_SEQUENCE_SYMBOLS {
        return Err("sequence length must be in 1..=4194304".into());
    }
    if soft
        .iter()
        .any(|value| !value.is_finite() || value.abs() > MAX_ABS_VALUE)
    {
        return Err("soft symbols must be finite and have magnitude <= 1e100".into());
    }
    Ok(())
}

fn validate_model(model: &ChannelModel) -> Result<(), String> {
    if model.training_symbols < MIN_TRAINING_SYMBOLS
        || model.training_symbols > MAX_SEQUENCE_SYMBOLS
    {
        return Err("channel model has an invalid training-symbol count".into());
    }
    if !model.normalized_mse.is_finite()
        || !(0.0..=MAX_NORMALIZED_MSE).contains(&model.normalized_mse)
    {
        return Err("channel model residual exceeds the quality gate".into());
    }
    if model
        .taps
        .iter()
        .chain(std::iter::once(&model.bias))
        .any(|value| !value.is_finite() || value.abs() > MAX_ABS_VALUE)
    {
        return Err("channel parameters must be finite and bounded".into());
    }
    let left = model.taps[0].abs();
    let center = model.taps[1];
    let right = model.taps[2].abs();
    // Permit substantial ISI, including cases where the combined neighboring
    // taps exceed the center. Reject reversed polarity or an off-center lock.
    if center <= 0.0 || center < left.max(right) || center < 0.55 * (left + right) {
        return Err("channel model lacks a positive, sufficiently dominant center tap".into());
    }
    Ok(())
}

fn regressors(levels: &[u8], index: usize) -> [f64; 4] {
    [
        1.0,
        2.0 * f64::from(levels[index - 1]) - 1.0,
        2.0 * f64::from(levels[index]) - 1.0,
        2.0 * f64::from(levels[index + 1]) - 1.0,
    ]
}

fn cholesky(matrix: [[f64; 4]; 4]) -> Result<[[f64; 4]; 4], String> {
    let mut lower = [[0.0; 4]; 4];
    for row in 0..4 {
        for column in 0..=row {
            let mut value = matrix[row][column];
            for (a, b) in lower[row][..column].iter().zip(&lower[column][..column]) {
                value -= a * b;
            }
            if row == column {
                if !value.is_finite() || value <= 1.0e-8 {
                    return Err("channel training regressors are rank deficient".into());
                }
                lower[row][column] = value.sqrt();
            } else {
                lower[row][column] = value / lower[column][column];
            }
        }
    }
    Ok(lower)
}

fn solve_ridge(mut gram: [[f64; 4]; 4], rhs: [f64; 4]) -> Result<[f64; 4], String> {
    // Diagnose rank deficiency *before* ridge regularization can hide it.
    cholesky(gram)?;
    for (index, row) in gram.iter_mut().enumerate().skip(1) {
        row[index] += RIDGE;
    }
    let lower = cholesky(gram)?;
    let mut intermediate = [0.0; 4];
    for row in 0..4 {
        let mut value = rhs[row];
        for (column, previous) in intermediate.iter().enumerate().take(row) {
            value -= lower[row][column] * previous;
        }
        intermediate[row] = value / lower[row][row];
    }
    let mut solution = [0.0; 4];
    for row in (0..4).rev() {
        let mut value = intermediate[row];
        for column in row + 1..4 {
            value -= lower[column][row] * solution[column];
        }
        solution[row] = value / lower[row][row];
    }
    Ok(solution)
}

/// Fit a ridge-regularized centered three-tap model on guarded span interiors.
///
/// Spans are half-open `[start, end)` indices into both arrays. Twenty symbols
/// are excluded at *each* span boundary, covering a 17-symbol G3RUH history and
/// neighboring regressors. Overlapping interiors are merged before fitting;
/// duplicate/overlapping spans cannot amplify their statistical weight. Short
/// spans are ignored, but at least 128 distinct interior symbols must remain.
///
/// Rejects non-finite/oversized data, malformed spans, rank-deficient designs,
/// a normalized residual above 0.5, or an implausibly off-center channel. The
/// caller remains responsible for anchor/target separation and provenance.
pub fn fit_channel(
    soft: &[f64],
    levels: &[u8],
    spans: &[(usize, usize)],
) -> Result<ChannelModel, String> {
    validate_soft(soft)?;
    if levels.len() != soft.len() || levels.iter().any(|level| *level > 1) {
        return Err("training levels must be binary and match the soft-symbol length".into());
    }
    if spans.is_empty() || spans.len() > MAX_TRAINING_SPANS {
        return Err("training span count must be in 1..=100000".into());
    }
    let mut interiors = Vec::with_capacity(spans.len());
    for &(start, end) in spans {
        if start >= end || end > soft.len() {
            return Err("training span must satisfy start < end <= symbol count".into());
        }
        if end - start > 2 * TRAINING_GUARD_SYMBOLS {
            interiors.push((start + TRAINING_GUARD_SYMBOLS, end - TRAINING_GUARD_SYMBOLS));
        }
    }
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
    let count: usize = merged.iter().map(|(start, end)| end - start).sum();
    if count < MIN_TRAINING_SYMBOLS {
        return Err("channel training needs at least 128 distinct guarded symbols".into());
    }
    let scale = merged
        .iter()
        .flat_map(|&(start, end)| &soft[start..end])
        .fold(0.0_f64, |largest, value| largest.max(value.abs()));
    if scale == 0.0 {
        return Err("channel training has no signal variation".into());
    }
    let mut gram = [[0.0; 4]; 4];
    let mut rhs = [0.0; 4];
    let mut sum_y = 0.0;
    let mut sum_yy = 0.0;
    for &(start, end) in &merged {
        for (index, sample) in soft.iter().enumerate().take(end).skip(start) {
            let x = regressors(levels, index);
            let y = sample / scale;
            sum_y += y;
            sum_yy += y * y;
            for row in 0..4 {
                rhs[row] += x[row] * y;
                for column in 0..4 {
                    gram[row][column] += x[row] * x[column];
                }
            }
        }
    }
    let count_f = count as f64;
    let variance = sum_yy / count_f - (sum_y / count_f).powi(2);
    if !variance.is_finite() || variance <= 1.0e-12 {
        return Err("channel training has no usable signal variation".into());
    }
    for row in 0..4 {
        rhs[row] /= count_f;
        for value in &mut gram[row] {
            *value /= count_f;
        }
    }
    let solution = solve_ridge(gram, rhs)?;
    let mut squared_error = 0.0;
    for &(start, end) in &merged {
        for (index, sample) in soft.iter().enumerate().take(end).skip(start) {
            let x = regressors(levels, index);
            let prediction: f64 = x.iter().zip(solution).map(|(a, b)| a * b).sum();
            squared_error += (sample / scale - prediction).powi(2);
        }
    }
    let model = ChannelModel {
        taps: [
            solution[1] * scale,
            solution[2] * scale,
            solution[3] * scale,
        ],
        bias: solution[0] * scale,
        training_symbols: count,
        normalized_mse: squared_error / count_f / variance,
    };
    validate_model(&model)?;
    Ok(model)
}

/// Reusable, caller-owned storage for four-state sequence detection.
///
/// Keep one workspace per sequential worker to avoid allocating traceback and
/// output arrays for every model/gain hypothesis. Capacity tracks the largest
/// valid input seen (at most [`MAX_SEQUENCE_SYMBOLS`]); dropping the workspace
/// releases it. No signal values, model, or decisions are reused across calls:
/// every live traceback/output element is overwritten on each successful call.
#[derive(Debug, Default)]
pub struct SequenceWorkspace {
    traceback: Vec<[u8; 4]>,
    levels: Vec<f64>,
}

impl SequenceWorkspace {
    pub fn new() -> Self {
        Self::default()
    }
}

/// Exact, deterministic, four-state Viterbi sequence estimation for the model.
///
/// Minimizes squared prediction error over `soft[1..len-1]`; the first/last
/// observations lack a complete three-symbol neighborhood and are excluded.
/// Initial and final states are free, and full traceback returns one bipolar
/// level (`-1.0` or `1.0`) per input symbol, in the same index coordinates.
/// This returns hard decisions, **not** calibrated likelihood ratios.
///
/// `gain` scales both bias and taps. Cost arithmetic is normalized by a common
/// amplitude scale, preserving the objective without overflow on large input.
/// Equal costs choose the smaller predecessor/final-state index. Memory use is
/// linear: four traceback bytes and eight output bytes per symbol, plus small
/// fixed work arrays. No payload, CRC, list search, or bit repair is involved.
pub fn detect_sequence(soft: &[f64], model: &ChannelModel, gain: f64) -> Result<Vec<f64>, String> {
    let mut workspace = SequenceWorkspace::new();
    detect_sequence_with_workspace(soft, model, gain, &mut workspace)?;
    Ok(workspace.levels)
}

/// [`detect_sequence`] with reusable storage and a borrowed output slice.
///
/// Arithmetic, validation order, and equal-cost tie breaking are identical to
/// the owned-output API. The returned decisions remain valid until the next
/// mutable use of `workspace`; callers needing them longer must copy them.
/// Invalid input leaves the workspace unchanged. A successful call overwrites
/// all active cells, including after a shorter input or a different model.
pub fn detect_sequence_with_workspace<'a>(
    soft: &[f64],
    model: &ChannelModel,
    gain: f64,
    workspace: &'a mut SequenceWorkspace,
) -> Result<&'a [f64], String> {
    validate_soft(soft)?;
    if soft.len() < 3 {
        return Err("sequence detection needs at least three symbols".into());
    }
    validate_model(model)?;
    if !gain.is_finite() || !(1.0e-6..=1.0e6).contains(&gain) {
        return Err("sequence gain must be finite and in 1e-6..=1e6".into());
    }
    let scaled_taps = model.taps.map(|tap| gain * tap);
    let scaled_bias = gain * model.bias;
    if scaled_taps[1] <= 0.0 {
        return Err("sequence gain underflows the channel model".into());
    }
    let scale = soft
        .iter()
        .chain(scaled_taps.iter())
        .chain(std::iter::once(&scaled_bias))
        .fold(0.0_f64, |largest, value| largest.max(value.abs()));
    // The validated positive center tap guarantees a positive scale.
    let taps = scaled_taps.map(|tap| tap / scale);
    let bias = scaled_bias / scale;
    if taps[1] <= 0.0 {
        return Err("sequence input/model dynamic range is not representable".into());
    }
    let mut predictions = [[0.0; 4]; 2];
    for (previous_bit, row) in predictions.iter_mut().enumerate() {
        for (state, prediction) in row.iter_mut().enumerate() {
            let left = 2.0 * previous_bit as f64 - 1.0;
            let center = 2.0 * (state >> 1) as f64 - 1.0;
            let right = 2.0 * (state & 1) as f64 - 1.0;
            *prediction = bias + taps[0] * left + taps[1] * center + taps[2] * right;
        }
    }
    let traceback_len = soft.len() - 2;
    if workspace.traceback.len() < traceback_len {
        workspace
            .traceback
            .reserve_exact(traceback_len - workspace.traceback.len());
        workspace.traceback.resize(traceback_len, [0_u8; 4]);
    }
    let traceback = &mut workspace.traceback[..traceback_len];
    let mut costs = [0.0_f64; 4];
    for (step, predecessors) in traceback.iter_mut().enumerate() {
        let observation = soft[step + 1] / scale;
        let mut next_costs = [0.0; 4];
        for state in 0..4 {
            let previous_low = state >> 1;
            let previous_high = previous_low | 2;
            let low_cost = costs[previous_low] + (observation - predictions[0][state]).powi(2);
            let high_cost = costs[previous_high] + (observation - predictions[1][state]).powi(2);
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
    if workspace.levels.len() < soft.len() {
        workspace
            .levels
            .reserve_exact(soft.len() - workspace.levels.len());
        workspace.levels.resize(soft.len(), -1.0);
    }
    let levels = &mut workspace.levels[..soft.len()];
    levels[soft.len() - 1] = 2.0 * (state & 1) as f64 - 1.0;
    for step in (0..traceback.len()).rev() {
        // State holds (x[step+1], x[step+2]) before this backstep.
        levels[step + 1] = 2.0 * (state >> 1) as f64 - 1.0;
        state = usize::from(traceback[step][state]);
    }
    levels[0] = 2.0 * (state >> 1) as f64 - 1.0;
    Ok(levels)
}

#[cfg(test)]
mod tests {
    use super::*;

    // Copied from the pre-workspace detector (2026-09-11). Keep this allocation-
    // per-call oracle independent of the optimized implementation so changes to
    // traceback storage cannot silently change arithmetic or tie breaking.
    fn legacy_detect_sequence(
        soft: &[f64],
        model: &ChannelModel,
        gain: f64,
    ) -> Result<Vec<f64>, String> {
        validate_soft(soft)?;
        if soft.len() < 3 {
            return Err("sequence detection needs at least three symbols".into());
        }
        validate_model(model)?;
        if !gain.is_finite() || !(1.0e-6..=1.0e6).contains(&gain) {
            return Err("sequence gain must be finite and in 1e-6..=1e6".into());
        }
        let scaled_taps = model.taps.map(|tap| gain * tap);
        let scaled_bias = gain * model.bias;
        if scaled_taps[1] <= 0.0 {
            return Err("sequence gain underflows the channel model".into());
        }
        let scale = soft
            .iter()
            .chain(scaled_taps.iter())
            .chain(std::iter::once(&scaled_bias))
            .fold(0.0_f64, |largest, value| largest.max(value.abs()));
        let taps = scaled_taps.map(|tap| tap / scale);
        let bias = scaled_bias / scale;
        if taps[1] <= 0.0 {
            return Err("sequence input/model dynamic range is not representable".into());
        }
        let mut predictions = [[0.0; 4]; 2];
        for (previous_bit, row) in predictions.iter_mut().enumerate() {
            for (state, prediction) in row.iter_mut().enumerate() {
                let left = 2.0 * previous_bit as f64 - 1.0;
                let center = 2.0 * (state >> 1) as f64 - 1.0;
                let right = 2.0 * (state & 1) as f64 - 1.0;
                *prediction = bias + taps[0] * left + taps[1] * center + taps[2] * right;
            }
        }
        let mut traceback = vec![[0_u8; 4]; soft.len() - 2];
        let mut costs = [0.0_f64; 4];
        for (step, predecessors) in traceback.iter_mut().enumerate() {
            let observation = soft[step + 1] / scale;
            let mut next_costs = [0.0; 4];
            for state in 0..4 {
                let previous_low = state >> 1;
                let previous_high = previous_low | 2;
                let low_cost = costs[previous_low] + (observation - predictions[0][state]).powi(2);
                let high_cost =
                    costs[previous_high] + (observation - predictions[1][state]).powi(2);
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

    fn random_levels(length: usize, mut state: u64) -> Vec<u8> {
        (0..length)
            .map(|_| {
                state ^= state << 13;
                state ^= state >> 7;
                state ^= state << 17;
                (state & 1) as u8
            })
            .collect()
    }

    fn model(taps: [f64; 3], bias: f64) -> ChannelModel {
        ChannelModel {
            taps,
            bias,
            training_symbols: 1024,
            normalized_mse: 0.0,
        }
    }

    fn signal(levels: &[u8], model: &ChannelModel, gain: f64) -> Vec<f64> {
        let bipolar: Vec<f64> = levels
            .iter()
            .map(|bit| 2.0 * f64::from(*bit) - 1.0)
            .collect();
        (0..levels.len())
            .map(|index| {
                gain * (model.bias
                    + model.taps[0] * bipolar[index.saturating_sub(1)]
                    + model.taps[1] * bipolar[index]
                    + model.taps[2] * bipolar[(index + 1).min(levels.len() - 1)])
            })
            .collect()
    }

    fn errors(soft: &[f64], truth: &[u8], threshold: f64) -> usize {
        soft.iter()
            .zip(truth)
            .filter(|(a, b)| u8::from(**a >= threshold) != **b)
            .count()
    }

    #[test]
    fn fits_known_channel_with_guarded_nonduplicated_spans() {
        let levels = random_levels(2048, 1773);
        let truth = model([0.31, 0.89, -0.22], 0.17);
        let soft = signal(&levels, &truth, 1.0);
        let fitted = fit_channel(&soft, &levels, &[(0, 2048)]).unwrap();
        assert_eq!(fitted.training_symbols, 2008);
        assert!(fitted.normalized_mse < 1.0e-10);
        assert!((fitted.bias - truth.bias).abs() < 1.0e-5);
        for (actual, expected) in fitted.taps.iter().zip(truth.taps) {
            assert!((actual - expected).abs() < 1.0e-5);
        }
        let overlapped = fit_channel(&soft, &levels, &[(100, 1500), (0, 2048), (0, 2048)]).unwrap();
        assert_eq!(fitted.training_symbols, overlapped.training_symbols);
        assert_eq!(fitted.taps, overlapped.taps);
        let mut contaminated = soft.clone();
        contaminated[..20].fill(1.0e80);
        contaminated[2028..].fill(-1.0e80);
        let guarded = fit_channel(&contaminated, &levels, &[(0, 2048)]).unwrap();
        assert_eq!(fitted.taps, guarded.taps);
    }

    #[test]
    fn distinct_symbol_transfer_recovers_isi_errors_without_payload_knowledge() {
        let anchor = random_levels(4096, 17);
        let target = random_levels(2048, 719);
        let channel = model([0.55, 0.75, 0.55], 0.07);
        let fitted = fit_channel(
            &signal(&anchor, &channel, 1.0),
            &anchor,
            &[(0, anchor.len())],
        )
        .unwrap();
        let received = signal(&target, &channel, 1.0);
        let detected = detect_sequence(&received, &fitted, 1.0).unwrap();
        assert!(errors(&received[1..2047], &target[1..2047], fitted.bias) > 100);
        assert_eq!(errors(&detected[1..2047], &target[1..2047], 0.0), 0);
        assert_ne!(anchor[..target.len()], target);
    }

    fn sequence_cost(samples: &[f64], levels: &[f64], channel: &ChannelModel) -> f64 {
        (1..samples.len() - 1)
            .map(|index| {
                let predicted = channel.bias
                    + channel.taps[0] * levels[index - 1]
                    + channel.taps[1] * levels[index]
                    + channel.taps[2] * levels[index + 1];
                (samples[index] - predicted).powi(2)
            })
            .sum()
    }

    #[test]
    fn exact_mlse_matches_exhaustive_small_stream_cost_oracle() {
        let channel = model([0.43, 0.72, 0.31], 0.12);
        for length in 3..=10 {
            for seed in 1..=9 {
                let source = random_levels(length, seed);
                let mut received = signal(&source, &channel, 1.0);
                for (index, value) in received.iter_mut().enumerate() {
                    *value += (((index * 13 + seed as usize * 7) % 11) as f64 - 5.0) * 0.19;
                }
                let output = detect_sequence(&received, &channel, 1.0).unwrap();
                let actual_cost = sequence_cost(&received, &output, &channel);
                let oracle_cost = (0..1_usize << length)
                    .map(|mask| {
                        let candidate: Vec<f64> = (0..length)
                            .map(|bit| if mask & (1 << bit) == 0 { -1.0 } else { 1.0 })
                            .collect();
                        sequence_cost(&received, &candidate, &channel)
                    })
                    .fold(f64::INFINITY, f64::min);
                assert!(
                    (actual_cost - oracle_cost).abs() < 1.0e-10,
                    "length={length} seed={seed}: actual={actual_cost}, oracle={oracle_cost}"
                );
            }
        }
    }

    #[test]
    fn gain_scales_bias_and_taps_together() {
        let truth = random_levels(512, 51);
        let channel = model([0.25, 0.9, -0.15], 1.1);
        let received = signal(&truth, &channel, 2.7);
        let detected = detect_sequence(&received, &channel, 2.7).unwrap();
        assert_eq!(errors(&detected[1..511], &truth[1..511], 0.0), 0);
    }

    #[test]
    fn large_amplitudes_are_normalized_without_overflow() {
        let truth = random_levels(1024, 559);
        let channel = model([0.2e80, 0.75e80, 0.3e80], 0.1e80);
        let received = signal(&truth, &channel, 1.0);
        let fitted = fit_channel(&received, &truth, &[(0, truth.len())]).unwrap();
        assert!(fitted.taps.iter().all(|value| value.is_finite()));
        let detected = detect_sequence(&received, &fitted, 1.0).unwrap();
        assert_eq!(errors(&detected[1..1023], &truth[1..1023], 0.0), 0);
        assert!(detect_sequence(&[1.0e101; 5], &channel, 1.0).is_err());
        let subnormal = model([0.0, 1.0e-320, 0.0], 0.0);
        assert!(detect_sequence(&[0.0; 5], &subnormal, 1.0e-6).is_err());
    }

    #[test]
    fn rejects_invalid_nonfinite_and_insufficient_training() {
        let levels = random_levels(512, 55);
        let channel = model([0.1, 0.9, 0.1], 0.0);
        let mut soft = signal(&levels, &channel, 1.0);
        for spans in [
            vec![],
            vec![(0, 0)],
            vec![(5, 4)],
            vec![(0, 513)],
            vec![(0, 100)],
        ] {
            assert!(fit_channel(&soft, &levels, &spans).is_err());
        }
        assert!(fit_channel(&soft, &levels[..511], &[(0, 511)]).is_err());
        let mut bad_levels = levels.clone();
        bad_levels[5] = 2;
        assert!(fit_channel(&soft, &bad_levels, &[(0, 512)]).is_err());
        for value in [f64::NAN, f64::INFINITY, f64::NEG_INFINITY, 1.0e101] {
            soft[0] = value;
            assert!(fit_channel(&soft, &levels, &[(0, 512)]).is_err());
            assert!(detect_sequence(&soft, &channel, 1.0).is_err());
        }
    }

    #[test]
    fn rejects_rank_deficient_silent_noisy_and_off_center_models() {
        let levels = random_levels(2048, 335);
        let channel = model([0.2, 0.9, 0.1], 0.0);
        for constant in [0.0, 5.0] {
            assert!(fit_channel(&vec![constant; 2048], &levels, &[(0, 2048)]).is_err());
        }
        let soft = signal(&levels, &channel, 1.0);
        for regressor in [
            vec![1; 2048],
            (0..2048).map(|index| (index & 1) as u8).collect(),
        ] {
            assert!(fit_channel(&soft, &regressor, &[(0, 2048)]).is_err());
        }
        let unrelated = random_levels(2048, 183_271);
        assert!(fit_channel(&soft, &unrelated, &[(0, 2048)]).is_err());
        for bad_channel in [model([0.1, -0.9, 0.1], 0.0), model([1.1, 0.9, 0.1], 0.0)] {
            assert!(
                fit_channel(&signal(&levels, &bad_channel, 1.0), &levels, &[(0, 2048)]).is_err()
            );
        }
    }

    #[test]
    fn boundary_silence_and_invalid_model_behavior_is_deterministic() {
        let channel = model([0.2, 0.9, 0.1], 0.0);
        for length in 0..3 {
            assert!(detect_sequence(&vec![0.0; length], &channel, 1.0).is_err());
        }
        for length in [3, 4, 127] {
            let first = detect_sequence(&vec![0.0; length], &channel, 1.0).unwrap();
            let second = detect_sequence(&vec![0.0; length], &channel, 1.0).unwrap();
            assert_eq!(first, second);
            assert_eq!(first.len(), length);
            assert!(first.iter().all(|level| *level == -1.0 || *level == 1.0));
        }
        for gain in [0.0, -1.0, f64::NAN, f64::INFINITY, 1.0e-7, 1.0e7] {
            assert!(detect_sequence(&[0.0; 8], &channel, gain).is_err());
        }
        let mut bad = channel.clone();
        bad.taps[1] = 0.0;
        assert!(detect_sequence(&[0.0; 8], &bad, 1.0).is_err());
        bad = channel.clone();
        bad.normalized_mse = 0.6;
        assert!(detect_sequence(&[0.0; 8], &bad, 1.0).is_err());
        bad = channel.clone();
        bad.bias = f64::NAN;
        assert!(detect_sequence(&[0.0; 8], &bad, 1.0).is_err());
        bad = channel;
        bad.training_symbols = 127;
        assert!(detect_sequence(&[0.0; 8], &bad, 1.0).is_err());
        assert!(detect_sequence(&vec![0.0; MAX_SEQUENCE_SYMBOLS + 1], &bad, 1.0).is_err());
    }

    #[test]
    fn workspace_matches_preoptimization_oracle_across_hypotheses() {
        let channels = [
            model([0.0, 1.0, 0.0], 0.0),
            model([0.55, 0.75, 0.55], 0.07),
            model([-0.3, 0.9, 0.2], -0.4),
            model([0.2e80, 0.75e80, -0.3e80], 0.1e80),
            model([0.2e-80, 0.75e-80, 0.3e-80], -0.1e-80),
        ];
        let mut workspace = SequenceWorkspace::new();
        let mut cases = 0;
        // Nonmonotonic lengths and changing models/gains exercise stale-buffer
        // hazards; zero observations include many exactly tied state costs.
        for length in [3, 4096, 4, 257, 7, 1024, 31, 128, 5] {
            for channel in &channels {
                for seed in 0..=7 {
                    for gain in [1.0e-6, 0.5, 0.9, 1.0, 1.1, 2.7, 1.0e6] {
                        let levels = random_levels(length, seed);
                        let mut soft = signal(&levels, channel, 1.0);
                        for (index, value) in soft.iter_mut().enumerate() {
                            *value = if seed == 0 {
                                if index % 2 == 0 { 0.0 } else { -0.0 }
                            } else {
                                *value
                                    + channel.taps[1]
                                        * (((index * 13 + seed as usize * 7) % 11) as f64 - 5.0)
                                        * 0.19
                            };
                        }
                        let expected = legacy_detect_sequence(&soft, channel, gain).unwrap();
                        let actual =
                            detect_sequence_with_workspace(&soft, channel, gain, &mut workspace)
                                .unwrap();
                        assert_eq!(actual.len(), expected.len());
                        assert!(
                            actual
                                .iter()
                                .zip(&expected)
                                .all(|(a, b)| a.to_bits() == b.to_bits()),
                            "length={length}, seed={seed}, gain={gain}, channel={channel:?}"
                        );
                        assert_eq!(detect_sequence(&soft, channel, gain).unwrap(), expected);
                        cases += 1;
                    }
                }
            }
        }
        assert_eq!(cases, 2520);
    }

    #[test]
    fn workspace_reuses_allocations_and_overwrites_poisoned_active_cells() {
        let channel = model([0.55, 0.75, -0.4], 0.07);
        let mut workspace = SequenceWorkspace::new();
        let longest = signal(&random_levels(65_536, 901), &channel, 1.0);
        detect_sequence_with_workspace(&longest, &channel, 1.0, &mut workspace).unwrap();
        let pointers = (workspace.traceback.as_ptr(), workspace.levels.as_ptr());
        let capacities = (workspace.traceback.capacity(), workspace.levels.capacity());
        for length in [3, 8192, 4, 65_536, 17, 65_535] {
            workspace.traceback.fill([255; 4]);
            workspace.levels.fill(f64::NAN);
            let soft = signal(&random_levels(length, 71), &channel, 1.0);
            let expected = legacy_detect_sequence(&soft, &channel, 0.9).unwrap();
            let actual =
                detect_sequence_with_workspace(&soft, &channel, 0.9, &mut workspace).unwrap();
            assert_eq!(actual, expected);
            assert_eq!(actual.len(), length);
            assert_eq!(
                (workspace.traceback.as_ptr(), workspace.levels.as_ptr()),
                pointers
            );
            assert_eq!(
                (workspace.traceback.capacity(), workspace.levels.capacity()),
                capacities
            );
        }
    }

    #[test]
    fn workspace_keeps_validation_order_and_does_not_mutate_on_error() {
        let channel = model([0.2, 0.9, 0.1], 0.0);
        let mut workspace = SequenceWorkspace::new();
        detect_sequence_with_workspace(&[0.0; 32], &channel, 1.0, &mut workspace).unwrap();
        let before_traceback = workspace.traceback.clone();
        let before_levels = workspace.levels.clone();
        let before_capacities = (workspace.traceback.capacity(), workspace.levels.capacity());
        let mut cases = vec![
            (vec![], channel.clone(), 1.0),
            (vec![0.0], channel.clone(), 1.0),
            (vec![0.0; 2], channel.clone(), 1.0),
            (vec![0.0; MAX_SEQUENCE_SYMBOLS + 1], channel.clone(), 1.0),
            (vec![f64::NAN; 3], channel.clone(), f64::NAN),
            (vec![f64::INFINITY; 3], channel.clone(), 1.0),
            (vec![f64::NEG_INFINITY; 3], channel.clone(), 1.0),
            (vec![1.0e101; 3], channel.clone(), 1.0),
            (vec![0.0; 3], model([0.0, 1.0e-320, 0.0], 0.0), 1.0e-6),
            (vec![1.0e100; 3], model([0.0, 1.0e-320, 0.0], 0.0), 1.0),
        ];
        for gain in [0.0, -1.0, f64::NAN, f64::INFINITY, 1.0e-7, 1.0e7] {
            cases.push((vec![0.0; 8], channel.clone(), gain));
        }
        for mutation in 0..8 {
            let mut invalid = channel.clone();
            match mutation {
                0 => invalid.taps[1] = 0.0,
                1 => invalid.taps[1] = -0.9,
                2 => invalid.taps[0] = 2.0,
                3 => invalid.normalized_mse = 0.6,
                4 => invalid.bias = f64::NAN,
                5 => invalid.training_symbols = 127,
                6 => invalid.training_symbols = MAX_SEQUENCE_SYMBOLS + 1,
                _ => invalid.normalized_mse = f64::INFINITY,
            }
            cases.push((vec![0.0; 8], invalid.clone(), 1.0));
            cases.push((vec![f64::NAN], invalid, f64::NAN));
        }
        for (soft, model, gain) in cases {
            let expected = legacy_detect_sequence(&soft, &model, gain).unwrap_err();
            assert_eq!(detect_sequence(&soft, &model, gain).unwrap_err(), expected);
            assert_eq!(
                detect_sequence_with_workspace(&soft, &model, gain, &mut workspace).unwrap_err(),
                expected,
            );
            assert_eq!(workspace.traceback, before_traceback);
            assert_eq!(workspace.levels, before_levels);
            assert_eq!(
                (workspace.traceback.capacity(), workspace.levels.capacity()),
                before_capacities
            );
        }
    }

    #[test]
    #[ignore = "manual release-mode paired microbenchmark; no timing assertion on shared hosts"]
    fn workspace_paired_microbenchmark() {
        use std::hint::black_box;
        use std::time::Instant;
        let channel = model([0.55, 0.75, 0.55], 0.07);
        let mut workspace = SequenceWorkspace::new();
        for length in [576, 5760, 57_600, 230_400] {
            let soft = signal(&random_levels(length, 901), &channel, 1.0);
            let expected = legacy_detect_sequence(&soft, &channel, 1.0).unwrap();
            assert_eq!(
                detect_sequence_with_workspace(&soft, &channel, 1.0, &mut workspace).unwrap(),
                expected
            );
            let iterations = 128;
            let mut old_seconds = Vec::new();
            let mut workspace_seconds = Vec::new();
            for trial in 0..6 {
                for slot in 0..2 {
                    let reusable = (slot + trial) % 2 == 1;
                    let started = Instant::now();
                    for _ in 0..iterations {
                        if reusable {
                            black_box(
                                detect_sequence_with_workspace(
                                    black_box(&soft),
                                    black_box(&channel),
                                    black_box(1.0),
                                    &mut workspace,
                                )
                                .unwrap(),
                            );
                        } else {
                            black_box(
                                legacy_detect_sequence(
                                    black_box(&soft),
                                    black_box(&channel),
                                    black_box(1.0),
                                )
                                .unwrap(),
                            );
                        }
                    }
                    let elapsed = started.elapsed().as_secs_f64();
                    if reusable {
                        workspace_seconds.push(elapsed);
                    } else {
                        old_seconds.push(elapsed);
                    }
                }
            }
            println!(
                "{{\"symbols\":{length},\"calls_per_trial\":{iterations},\"legacy_seconds\":{old_seconds:?},\"workspace_seconds\":{workspace_seconds:?}}}"
            );
        }
    }
}
