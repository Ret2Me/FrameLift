//! Optional CPU-only robust losses for the existing three-tap channel model.
//!
//! This is an experiment in changing sequence-detection residual costs, not a
//! new modulation frontend, channel estimator, CRC repairer, or default decoder.
//! Huber and Student-t losses are established techniques, not claimed novelty.
//! The caller owns source-only channel/noise estimation and source/target
//! separation. Neither transmitted bits, target truth nor checksums enter this
//! API. Robust losses can lose valid frames as well as gain them; improvement
//! must be measured against the identical point-channel/timing hypothesis.

use crate::sequence::{self, ChannelModel};
use serde::{Deserialize, Serialize};

pub const MIN_NOISE_SCALE: f64 = 1.0e-100;
pub const MAX_NOISE_SCALE: f64 = 1.0e100;
pub const MIN_HUBER_DELTA: f64 = 0.25;
pub const MAX_HUBER_DELTA: f64 = 16.0;
pub const MIN_STUDENT_NU: f64 = 0.5;
pub const MAX_STUDENT_NU: f64 = 128.0;
const MAX_ABS_SOFT: f64 = 1.0e100;

#[derive(Clone, Copy, Debug, PartialEq, Serialize, Deserialize)]
pub enum Metric {
    /// Exact existing squared-error detector; positive noise_scale is validated
    /// but otherwise unused because its common scale cannot change that model.
    Gaussian,
    /// Standard Huber loss, with the threshold in source-noise-scale units.
    Huber { delta: f64 },
    /// Student-t negative log density up to path-independent constants. The
    /// noise_scale argument is its *scale parameter*, not its standard deviation
    /// (which differs from scale, or is undefined for nu <= 2).
    StudentT { nu: f64 },
}

enum PreparedMetric {
    Huber {
        delta: f64,
    },
    StudentT {
        half_nu_plus_one: f64,
        inverse_sqrt_nu: f64,
    },
}

impl PreparedMetric {
    fn loss(&self, residual: f64) -> f64 {
        let absolute = residual.abs();
        match *self {
            Self::Huber { delta } => {
                if absolute <= delta {
                    0.5 * absolute * absolute
                } else {
                    delta * (absolute - 0.5 * delta)
                }
            }
            Self::StudentT {
                half_nu_plus_one,
                inverse_sqrt_nu,
            } => {
                let ratio = absolute * inverse_sqrt_nu;
                // ln(1 + ratio^2), without overflowing ratio^2 for very large
                // finite residuals, or losing accuracy close to zero.
                let log_term = if ratio <= 1.0 {
                    (ratio * ratio).ln_1p()
                } else {
                    2.0 * ratio.ln() + (1.0 / ratio).powi(2).ln_1p()
                };
                half_nu_plus_one * log_term
            }
        }
    }
}

fn prepare(metric: Metric) -> Result<Option<PreparedMetric>, String> {
    match metric {
        Metric::Gaussian => Ok(None),
        Metric::Huber { delta }
            if delta.is_finite() && (MIN_HUBER_DELTA..=MAX_HUBER_DELTA).contains(&delta) =>
        {
            Ok(Some(PreparedMetric::Huber { delta }))
        }
        Metric::StudentT { nu }
            if nu.is_finite() && (MIN_STUDENT_NU..=MAX_STUDENT_NU).contains(&nu) =>
        {
            Ok(Some(PreparedMetric::StudentT {
                half_nu_plus_one: 0.5 * (nu + 1.0),
                inverse_sqrt_nu: 1.0 / nu.sqrt(),
            }))
        }
        Metric::Huber { .. } => Err("Huber delta must be finite and in 0.25..=16".into()),
        Metric::StudentT { .. } => Err("Student-t nu must be finite and in 0.5..=128".into()),
    }
}

/// Detect bipolar hard levels with a fixed, source-derived channel and scale.
///
/// Let `mu[n] = bias + left*x[n-1] + center*x[n] + right*x[n+1]`, where
/// `x = -1 or +1`. Both gain-adjusted signal and source noise scale are used:
/// `z[n] = (soft[n] - gain*mu[n]) / (gain*noise_scale)`.
///
/// `noise_scale` is a positive source-derived amplitude scale. A caller may use
/// the square root of guarded source residual variance from uncertainty::fit.
/// Such an RMS estimate is not itself robust to training outliers, and using it
/// as a Student-t scale does **not** assert Student-t variance calibration.
/// The caller must not fit or select it from target bits or CRC outcomes.
///
/// Minimized losses (sum over observations 1 through len-2):
/// - Huber: `0.5*z^2` for `abs(z)<=delta`, otherwise `delta*(abs(z)-delta/2)`.
/// - Student-t: `0.5*(nu+1)*ln(1+z^2/nu)`.
///
/// Constants independent of the symbol path are omitted. No comparison between
/// absolute objective values from different gains/models is exposed by the API.
///
/// Gaussian directly delegates to the existing detector, preserving its exact
/// floating-point decisions and ties rather than reimplementing a normalized
/// algebraic equivalent. Other metrics use four states, free initial/final
/// states, full traceback, smaller predecessor/final-state index for equal
/// costs, and the same excluded endpoint observations. Outputs are hard levels,
/// not likelihood ratios or probabilities.
pub fn detect(
    soft: &[f64],
    model: &ChannelModel,
    gain: f64,
    noise_scale: f64,
    metric: Metric,
) -> Result<Vec<f64>, String> {
    if !noise_scale.is_finite() || !(MIN_NOISE_SCALE..=MAX_NOISE_SCALE).contains(&noise_scale) {
        return Err("source noise scale must be finite and in 1e-100..=1e100".into());
    }
    let Some(prepared) = prepare(metric)? else {
        return sequence::detect_sequence(soft, model, gain);
    };
    if !(3..=sequence::MAX_SEQUENCE_SYMBOLS).contains(&soft.len())
        || soft
            .iter()
            .any(|value| !value.is_finite() || value.abs() > MAX_ABS_SOFT)
    {
        return Err("robust detector needs 3..=4194304 finite bounded symbols".into());
    }
    // Reuse authoritative gain/channel validation, including polarity, dominant
    // center tap, training count and source quality. This does not estimate or
    // select anything from these three target observations.
    sequence::detect_sequence(&soft[..3], model, gain)?;
    let scaled_taps = model.taps.map(|tap| gain * tap);
    let scaled_bias = model.bias * gain;
    let scaled_noise = gain * noise_scale;
    let scale = soft
        .iter()
        .chain(scaled_taps.iter())
        .copied()
        .chain([scaled_bias, scaled_noise])
        .fold(0.0_f64, |largest, value| largest.max(value.abs()));
    let taps = scaled_taps.map(|tap| tap / scale);
    let bias = scaled_bias / scale;
    let normalized_noise = scaled_noise / scale;
    if taps[1] <= 0.0 || !normalized_noise.is_finite() || normalized_noise <= 0.0 {
        return Err("robust input/model dynamic range is not representable".into());
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
    // With validated inputs, |z| <= 5e206. Huber uses its square only when
    // |z| <= delta <= 16, and otherwise is linear; Student-t uses a stable log.
    // Even 4,194,304 maximum-length costs sum far below f64::MAX. No clipping,
    // early stopping, reduced traceback or residual-dependent pruning occurs.
    let mut traceback = vec![[0_u8; 4]; soft.len() - 2];
    let mut costs = [0.0_f64; 4];
    for (step, predecessors) in traceback.iter_mut().enumerate() {
        let observation = soft[step + 1] / scale;
        let mut next_costs = [0.0; 4];
        for state in 0..4 {
            let previous_low = state >> 1;
            let previous_high = previous_low | 2;
            let low_cost = costs[previous_low]
                + prepared.loss((observation - predictions[0][state]) / normalized_noise);
            let high_cost = costs[previous_high]
                + prepared.loss((observation - predictions[1][state]) / normalized_noise);
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

    fn model() -> ChannelModel {
        ChannelModel {
            taps: [0.23, 0.91, -0.14],
            bias: 0.08,
            training_symbols: 512,
            normalized_mse: 0.05,
        }
    }

    fn fixture() -> (Vec<f64>, Vec<f64>) {
        let mut rng = 0x9876_5432_u64;
        let levels: Vec<f64> = (0..200)
            .map(|_| {
                rng ^= rng << 13;
                rng ^= rng >> 7;
                rng ^= rng << 17;
                2.0 * (rng & 1) as f64 - 1.0
            })
            .collect();
        let m = model();
        let mut soft = levels.clone();
        for n in 1..levels.len() - 1 {
            soft[n] = m.bias
                + m.taps[0] * levels[n - 1]
                + m.taps[1] * levels[n]
                + m.taps[2] * levels[n + 1];
        }
        (soft, levels)
    }

    fn physical_objective(
        soft: &[f64],
        levels: &[f64],
        model: &ChannelModel,
        gain: f64,
        noise: f64,
        metric: Metric,
    ) -> f64 {
        // Independent physical-unit oracle; moderate fixture amplitudes make
        // the straightforward formula safe. It shares no branch tables, loss
        // preparation, normalization or traceback with the implementation.
        (1..soft.len() - 1)
            .map(|n| {
                let predicted = gain
                    * (model.bias
                        + model.taps[0] * levels[n - 1]
                        + model.taps[1] * levels[n]
                        + model.taps[2] * levels[n + 1]);
                let residual = (soft[n] - predicted) / (gain * noise);
                match metric {
                    Metric::Gaussian => 0.5 * residual * residual,
                    Metric::Huber { delta } => {
                        if residual.abs() <= delta {
                            residual * residual / 2.0
                        } else {
                            delta * residual.abs() - delta * delta / 2.0
                        }
                    }
                    Metric::StudentT { nu } => {
                        (nu + 1.0) / 2.0 * (1.0 + residual * residual / nu).ln()
                    }
                }
            })
            .sum()
    }

    #[test]
    fn gaussian_is_exact_existing_detector_for_every_valid_noise_scale() {
        let (soft, _) = fixture();
        let m = model();
        for gain in [1.0e-6, 0.75, 1.0, 1.5, 1.0e6] {
            let expected = sequence::detect_sequence(&soft, &m, gain).unwrap();
            for noise in [MIN_NOISE_SCALE, 0.2, 1.0, MAX_NOISE_SCALE] {
                assert_eq!(
                    detect(&soft, &m, gain, noise, Metric::Gaussian).unwrap(),
                    expected
                );
            }
        }
    }

    #[test]
    fn exhaustive_oracle_for_short_sequences_gains_scales_and_metrics() {
        let m = model();
        let metrics = [
            Metric::Huber { delta: 0.7 },
            Metric::Huber { delta: 2.0 },
            Metric::StudentT { nu: 1.0 },
            Metric::StudentT { nu: 3.0 },
            Metric::StudentT { nu: 12.0 },
        ];
        for count in 3..=8 {
            for seed in 0..4 {
                let soft: Vec<_> = (0..count)
                    .map(|n| ((n * 17 + seed * 23) as f64 * 0.193).sin() * 1.3)
                    .collect();
                for gain in [0.75, 1.0, 1.5] {
                    for noise in [0.15, 0.4] {
                        for metric in metrics {
                            let actual = detect(&soft, &m, gain, noise, metric).unwrap();
                            let best = (0..1 << count)
                                .map(|bits| {
                                    let levels: Vec<_> = (0..count)
                                        .map(|n| 2.0 * ((bits >> n) & 1) as f64 - 1.0)
                                        .collect();
                                    physical_objective(&soft, &levels, &m, gain, noise, metric)
                                })
                                .fold(f64::INFINITY, f64::min);
                            let got = physical_objective(&soft, &actual, &m, gain, noise, metric);
                            assert!(
                                (got - best).abs() < 1.0e-9 * (1.0 + best.abs()),
                                "n={count} seed={seed} gain={gain} scale={noise} metric={metric:?}: {got} != {best}"
                            );
                        }
                    }
                }
            }
        }
    }

    #[test]
    fn zero_input_has_deterministic_low_index_ties_and_no_frame() {
        let mut m = model();
        m.taps = [0.0, 1.0, 0.0];
        m.bias = 0.0;
        for metric in [
            Metric::Gaussian,
            Metric::Huber { delta: 2.0 },
            Metric::StudentT { nu: 3.0 },
        ] {
            let decoded = detect(&[0.0; 512], &m, 1.0, 0.2, metric).unwrap();
            assert_eq!(decoded, vec![-1.0; 512]);
            assert!(
                crate::anchors::validated_spans(&decoded, 0.0)
                    .unwrap()
                    .is_empty()
            );
        }
    }

    #[test]
    fn clean_synthetic_sequence_is_recovered_without_bit_changes() {
        let (soft, expected) = fixture();
        for metric in [
            Metric::Gaussian,
            Metric::Huber { delta: 2.0 },
            Metric::StudentT { nu: 3.0 },
        ] {
            let actual = detect(&soft, &model(), 1.0, 0.2, metric).unwrap();
            assert_eq!(actual, expected);
            assert_eq!(actual, detect(&soft, &model(), 1.0, 0.2, metric).unwrap());
        }
    }

    #[test]
    fn isolated_large_impulse_illustrates_reduced_neighbor_error_propagation() {
        // Deliberate mechanism-level example, not a Monte Carlo benchmark or
        // evidence of field yield. Gaussian can sacrifice adjacent symbols to
        // explain a large outlier through ISI; robust costs downweight it.
        let mut m = model();
        m.taps = [0.35, 1.0, 0.35];
        m.bias = 0.0;
        let mut soft = vec![-1.7; 41];
        soft[20] = 30.0;
        let errors = |metric| {
            detect(&soft, &m, 1.0, 0.2, metric)
                .unwrap()
                .iter()
                .filter(|&&level| level != -1.0)
                .count()
        };
        let gaussian = errors(Metric::Gaussian);
        let huber = errors(Metric::Huber { delta: 2.0 });
        let student = errors(Metric::StudentT { nu: 3.0 });
        assert!(gaussian > huber, "Gaussian={gaussian}, Huber={huber}");
        assert!(huber >= student, "Huber={huber}, Student-t={student}");
        assert_eq!(student, 0);
    }

    #[test]
    fn gain_scales_channel_and_source_noise_together() {
        let (soft, _) = fixture();
        let m = model();
        for metric in [Metric::Huber { delta: 2.0 }, Metric::StudentT { nu: 3.0 }] {
            for gain in [0.75, 1.5] {
                let mut scaled_model = m.clone();
                scaled_model.bias *= gain;
                scaled_model.taps = scaled_model.taps.map(|x| x * gain);
                assert_eq!(
                    detect(&soft, &m, gain, 0.2, metric).unwrap(),
                    detect(&soft, &scaled_model, 1.0, 0.2 * gain, metric).unwrap()
                );
            }
        }
    }

    #[test]
    fn common_amplitude_scaling_preserves_ordinary_decisions() {
        let (soft, _) = fixture();
        for metric in [Metric::Huber { delta: 2.0 }, Metric::StudentT { nu: 3.0 }] {
            let expected = detect(&soft, &model(), 1.0, 0.2, metric).unwrap();
            for amplitude in [1.0e-80, 1.0e80] {
                let scaled: Vec<_> = soft.iter().map(|x| x * amplitude).collect();
                let mut m = model();
                m.bias *= amplitude;
                m.taps = m.taps.map(|x| x * amplitude);
                assert_eq!(
                    detect(&scaled, &m, 1.0, 0.2 * amplitude, metric).unwrap(),
                    expected
                );
            }
        }
    }

    #[test]
    fn student_loss_stays_finite_when_naive_residual_square_overflows() {
        for nu in [MIN_STUDENT_NU, 3.0, MAX_STUDENT_NU] {
            let prepared = prepare(Metric::StudentT { nu }).unwrap().unwrap();
            for residual in [0.0, 1.0e-200, 1.0e-6, 1.0, 1.0e100, 1.0e206] {
                let cost = prepared.loss(residual);
                assert!(cost.is_finite() && cost >= 0.0);
                assert_eq!(cost, prepared.loss(-residual));
            }
        }
        for metric in [
            Metric::Huber {
                delta: MAX_HUBER_DELTA,
            },
            Metric::StudentT { nu: 3.0 },
        ] {
            let decoded = detect(
                &vec![1.0e100; 4096],
                &model(),
                1.0e-6,
                MIN_NOISE_SCALE,
                metric,
            )
            .unwrap();
            assert!(decoded.iter().all(|&x| x == -1.0 || x == 1.0));
        }
    }

    #[test]
    fn malformed_and_nonfinite_inputs_are_rejected_including_gaussian() {
        let (soft, _) = fixture();
        let m = model();
        for metric in [
            Metric::Gaussian,
            Metric::Huber { delta: 2.0 },
            Metric::StudentT { nu: 3.0 },
        ] {
            for noise in [0.0, -1.0, f64::NAN, f64::INFINITY, 1.0e-101, 1.0e101] {
                assert!(detect(&soft, &m, 1.0, noise, metric).is_err());
            }
            for gain in [0.0, -1.0, f64::NAN, f64::INFINITY, 1.0e-7, 1.0e7] {
                assert!(detect(&soft, &m, gain, 0.2, metric).is_err());
            }
            for invalid_soft in [
                vec![],
                vec![0.0; 2],
                vec![f64::NAN; 3],
                vec![f64::INFINITY; 3],
                vec![1.0e101; 3],
            ] {
                assert!(detect(&invalid_soft, &m, 1.0, 0.2, metric).is_err());
            }
            for invalid_model in [
                ChannelModel {
                    taps: [0.0, -1.0, 0.0],
                    ..m.clone()
                },
                ChannelModel {
                    taps: [2.0, 1.0, 0.0],
                    ..m.clone()
                },
                ChannelModel {
                    bias: f64::NAN,
                    ..m.clone()
                },
                ChannelModel {
                    training_symbols: 127,
                    ..m.clone()
                },
                ChannelModel {
                    normalized_mse: 0.6,
                    ..m.clone()
                },
            ] {
                assert!(detect(&soft, &invalid_model, 1.0, 0.2, metric).is_err());
            }
        }
        for invalid in [f64::NAN, f64::INFINITY, -1.0, 0.0, 0.1, 1.0e6] {
            assert!(detect(&soft, &m, 1.0, 0.2, Metric::Huber { delta: invalid }).is_err());
            assert!(detect(&soft, &m, 1.0, 0.2, Metric::StudentT { nu: invalid }).is_err());
        }
    }

    #[test]
    fn metric_json_round_trip_preserves_results_and_validates_deserialized_parameters() {
        let (soft, _) = fixture();
        for metric in [
            Metric::Gaussian,
            Metric::Huber { delta: 2.0 },
            Metric::StudentT { nu: 3.0 },
        ] {
            let encoded = serde_json::to_string(&metric).unwrap();
            let restored: Metric = serde_json::from_str(&encoded).unwrap();
            assert_eq!(metric, restored);
            assert_eq!(
                detect(&soft, &model(), 1.0, 0.2, metric).unwrap(),
                detect(&soft, &model(), 1.0, 0.2, restored).unwrap()
            );
        }
        let invalid: Metric = serde_json::from_str(r#"{"StudentT":{"nu":0.0}}"#).unwrap();
        assert!(detect(&soft, &model(), 1.0, 0.2, invalid).is_err());
    }
}
