//! Log-MAP sequence detection for the existing centered three-tap channel.
//!
//! Positive LLRs mean ONE. The first/last observations are omitted, matching
//! `sequence::detect_sequence`; endpoint *bits* are inferred from their other
//! observations. Boundary states are unknown, not silently fixed to zero.
//! `detect_complete` is an explicit alternative that includes both endpoint
//! observations while marginalizing unknown exterior symbols.
//! LLRs are conditional on the supplied Gaussian channel/noise model: they are
//! not a claim of calibrated probabilities on arbitrary real recordings.

use serde::{Deserialize, Serialize};

pub const MAX_SYMBOLS: usize = 1_048_576;
pub const LLR_LIMIT: f64 = 100.0;

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Channel {
    pub taps: [f64; 3],
    pub bias: f64,
    /// Observation noise variance, in the same units as samples squared.
    pub noise_variance: f64,
}

impl Channel {
    pub fn validate(&self) -> Result<(), String> {
        if self
            .taps
            .iter()
            .chain([&self.bias])
            .any(|x| !x.is_finite() || x.abs() > 1e6)
            || self.taps.iter().all(|x| *x == 0.0)
            || !self.noise_variance.is_finite()
            || !(1e-12..=1e12).contains(&self.noise_variance)
        {
            return Err(
                "BCJR requires finite bounded channel taps and variance in 1e-12..=1e12".into(),
            );
        }
        Ok(())
    }
}

#[derive(Debug, Serialize)]
pub struct SoftOutput {
    pub posterior_llr: Vec<f64>,
    /// APP minus the input prior, computed BEFORE either output is clipped.
    pub extrinsic_llr: Vec<f64>,
    pub hard_bits: Vec<u8>,
    pub llr_clip: f64,
}

#[inline]
fn add(a: f64, b: f64) -> f64 {
    if a == f64::NEG_INFINITY {
        return b;
    }
    if b == f64::NEG_INFINITY {
        return a;
    }
    let large = a.max(b);
    large + (-(a - b).abs()).exp().ln_1p()
}

fn normalize(row: &mut [f64; 4]) {
    let maximum = row.iter().copied().fold(f64::NEG_INFINITY, f64::max);
    for x in row {
        *x -= maximum;
    }
}

fn level(bit: usize) -> f64 {
    2.0 * bit as f64 - 1.0
}

/// Exact sum-product under the stated fixed-channel, independent-noise model.
/// `prior` is either empty (uniform bits) or exactly one bounded LLR per bit.
/// No packet contents, CRC checks or reference frames enter this function.
pub fn detect(samples: &[f64], channel: &Channel, prior: &[f64]) -> Result<SoftOutput, String> {
    detect_inner(samples, std::slice::from_ref(channel), prior, None).map(|v| v.0)
}

/// Time-varying centered channel: either one model, or one per observation.
pub fn detect_varying(
    samples: &[f64],
    channels: &[Channel],
    prior: &[f64],
) -> Result<SoftOutput, String> {
    detect_inner(samples, channels, prior, None).map(|v| v.0)
}

/// Use every received observation, including the first and last. Two virtual
/// exterior bits are marginalized with uniform priors; neither exterior bit
/// is assumed to be zero or equal to a decoded endpoint. This is deliberately
/// separate from `detect`, whose legacy omission contract remains unchanged.
pub fn detect_complete(
    samples: &[f64],
    channel: &Channel,
    prior: &[f64],
) -> Result<SoftOutput, String> {
    detect_complete_inner(samples, std::slice::from_ref(channel), prior, None).map(|v| v.0)
}

#[derive(Clone, Debug, Default)]
pub(crate) struct Statistics {
    pub gram: [[f64; 4]; 4],
    pub cross: [f64; 4],
    pub energy: f64,
    pub count: usize,
}

pub(crate) fn detect_statistics(
    samples: &[f64],
    channels: &[Channel],
    prior: &[f64],
    segment: usize,
) -> Result<(SoftOutput, Vec<Statistics>), String> {
    if !(16..=4096).contains(&segment) {
        return Err("channel segment must contain 16..4096 symbols".into());
    }
    detect_inner(samples, channels, prior, Some(segment))
}

/// Complete observations with sufficient statistics in ORIGINAL observation
/// coordinates. Virtual exterior bits contribute uncertainty, not samples or
/// extra counts; segments contain exactly the actual received observations.
pub(crate) fn detect_complete_statistics(
    samples: &[f64],
    channels: &[Channel],
    prior: &[f64],
    segment: usize,
) -> Result<(SoftOutput, Vec<Statistics>), String> {
    if !(16..=4096).contains(&segment) {
        return Err("channel segment must contain 16..4096 symbols".into());
    }
    detect_complete_inner(samples, channels, prior, Some(segment))
}

fn detect_complete_inner(
    samples: &[f64],
    channels: &[Channel],
    prior: &[f64],
    segment: Option<usize>,
) -> Result<(SoftOutput, Vec<Statistics>), String> {
    let count = samples.len();
    if !(1..=MAX_SYMBOLS).contains(&count)
        || (channels.len() != 1 && channels.len() != count)
        || (!prior.is_empty() && prior.len() != count)
    {
        return Err("complete BCJR needs 1..1048576 observations, matching priors and one or per-observation channels".into());
    }
    let mut observations = Vec::with_capacity(count + 2);
    observations.push(0.);
    observations.extend_from_slice(samples);
    observations.push(0.);
    let mut priors = vec![0.; count + 2];
    if !prior.is_empty() {
        priors[1..=count].copy_from_slice(prior);
    }
    let padded_channels;
    let channels = if channels.len() == 1 {
        channels
    } else {
        padded_channels = std::iter::once(channels[0].clone())
            .chain(channels.iter().cloned())
            .chain(std::iter::once(channels[count - 1].clone()))
            .collect::<Vec<_>>();
        &padded_channels
    };
    let (mut output, statistics) =
        detect_inner_with_origin(&observations, channels, &priors, segment, 1)?;
    // The legacy recursion omitted exactly the two dummy observations. Its
    // first/last posterior entries now belong to marginalized exterior bits.
    output.posterior_llr.truncate(count + 1);
    output.posterior_llr.remove(0);
    output.extrinsic_llr.truncate(count + 1);
    output.extrinsic_llr.remove(0);
    output.hard_bits.truncate(count + 1);
    output.hard_bits.remove(0);
    Ok((output, statistics))
}

fn detect_inner(
    samples: &[f64],
    channels: &[Channel],
    prior: &[f64],
    segment: Option<usize>,
) -> Result<(SoftOutput, Vec<Statistics>), String> {
    detect_inner_with_origin(samples, channels, prior, segment, 0)
}

fn detect_inner_with_origin(
    samples: &[f64],
    channels: &[Channel],
    prior: &[f64],
    segment: Option<usize>,
    observation_origin: usize,
) -> Result<(SoftOutput, Vec<Statistics>), String> {
    let n = samples.len();
    if channels.len() != 1 && channels.len() != n {
        return Err("BCJR needs one channel or one per observation".into());
    }
    for channel in channels {
        channel.validate()?;
    }
    if !(3..=MAX_SYMBOLS + 2 * observation_origin).contains(&n)
        || samples.iter().any(|x| !x.is_finite() || x.abs() > 1e6)
        || (!prior.is_empty() && prior.len() != n)
        || prior.iter().any(|x| !x.is_finite() || x.abs() > LLR_LIMIT)
    {
        return Err("BCJR needs 3..=1048576 bounded samples and empty or length-matched LLR priors in [-100,100]".into());
    }
    let p = |i: usize| prior.get(i).copied().unwrap_or(0.0);
    let prediction = |channel: &Channel| -> [f64; 8] {
        std::array::from_fn(|triple| {
            channel.bias
                + channel.taps[0] * level(triple >> 2)
                + channel.taps[1] * level((triple >> 1) & 1)
                + channel.taps[2] * level(triple & 1)
        })
    };
    let constant = prediction(&channels[0]);
    // Precompute eight observation metrics per step. Subtracting a common
    // maximum keeps long forward/backward recursions numerically stable.
    let metrics: Vec<[f64; 8]> = samples[1..n - 1]
        .iter()
        .enumerate()
        .map(|(index, y)| {
            let channel = &channels[if channels.len() == 1 { 0 } else { index + 1 }];
            let predictions = if channels.len() == 1 {
                constant
            } else {
                prediction(channel)
            };
            let mut row = predictions.map(|mu| -0.5 * (y - mu).powi(2) / channel.noise_variance);
            let maximum = row.iter().copied().fold(f64::NEG_INFINITY, f64::max);
            for x in &mut row {
                *x -= maximum;
            }
            row
        })
        .collect();
    let mut alpha = vec![[0.0; 4]; n - 1];
    for (state, value) in alpha[0].iter_mut().enumerate() {
        *value = 0.5 * (level(state >> 1) * p(0) + level(state & 1) * p(1));
    }
    normalize(&mut alpha[0]);
    for t in 0..n - 2 {
        let mut next = [f64::NEG_INFINITY; 4];
        for state in 0..4 {
            for bit in 0..2 {
                let next_state = ((state & 1) << 1) | bit;
                let metric = metrics[t][state * 2 + bit] + 0.5 * level(bit) * p(t + 2);
                next[next_state] = add(next[next_state], alpha[t][state] + metric);
            }
        }
        normalize(&mut next);
        alpha[t + 1] = next;
    }
    let mut posterior = vec![0.0; n];
    let mut beta = [0.0; 4];
    let mut statistics = segment
        .map(|s| vec![Statistics::default(); (n - 2 * observation_origin).div_ceil(s)])
        .unwrap_or_default();
    for t in (0..n - 2).rev() {
        let mut masses = [f64::NEG_INFINITY; 2];
        let mut previous = [f64::NEG_INFINITY; 4];
        let mut joint = [0.0; 8];
        for state in 0..4 {
            for (bit, mass) in masses.iter_mut().enumerate() {
                let next_state = ((state & 1) << 1) | bit;
                let metric = metrics[t][state * 2 + bit] + 0.5 * level(bit) * p(t + 2);
                let suffix = metric + beta[next_state];
                joint[state * 2 + bit] = alpha[t][state] + suffix;
                *mass = add(*mass, alpha[t][state] + suffix);
                previous[state] = add(previous[state], suffix);
            }
        }
        posterior[t + 2] = masses[1] - masses[0];
        if let Some(segment) = segment {
            let normalizer = add(masses[0], masses[1]);
            let row = &mut statistics[(t + 1 - observation_origin) / segment];
            let y = samples[t + 1];
            row.count += 1;
            row.energy += y * y;
            for (triple, log_probability) in joint.iter().enumerate() {
                let probability = (log_probability - normalizer).exp();
                let x = [
                    1.0,
                    level(triple >> 2),
                    level((triple >> 1) & 1),
                    level(triple & 1),
                ];
                for i in 0..4 {
                    row.cross[i] += probability * y * x[i];
                    for j in 0..4 {
                        row.gram[i][j] += probability * x[i] * x[j];
                    }
                }
            }
        }
        normalize(&mut previous);
        beta = previous;
    }
    for (i, output) in posterior.iter_mut().enumerate().take(2) {
        let mut masses = [f64::NEG_INFINITY; 2];
        for (state, b) in beta.iter().enumerate() {
            let bit = (state >> (1 - i)) & 1;
            masses[bit] = add(masses[bit], alpha[0][state] + b);
        }
        *output = masses[1] - masses[0];
    }
    if posterior.iter().any(|x| !x.is_finite()) {
        return Err("BCJR produced nonfinite likelihoods".into());
    }
    let extrinsic_llr = posterior
        .iter()
        .enumerate()
        .map(|(i, x)| (x - p(i)).clamp(-LLR_LIMIT, LLR_LIMIT))
        .collect();
    let hard_bits = posterior.iter().map(|x| u8::from(*x > 0.0)).collect();
    for x in &mut posterior {
        *x = x.clamp(-LLR_LIMIT, LLR_LIMIT);
    }
    Ok((
        SoftOutput {
            posterior_llr: posterior,
            extrinsic_llr,
            hard_bits,
            llr_clip: LLR_LIMIT,
        },
        statistics,
    ))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn channel() -> Channel {
        Channel {
            taps: [0.35, 1.0, -0.25],
            bias: 0.1,
            noise_variance: 0.7,
        }
    }

    // Independent exhaustive enumeration of whole bit strings, not trellis
    // states or recurrence. Includes both boundary bits and nonzero priors.
    fn exhaustive(y: &[f64], c: &Channel, prior: &[f64]) -> Vec<f64> {
        let mut sums = vec![[0.0; 2]; y.len()];
        for word in 0..1usize << y.len() {
            let bits: Vec<_> = (0..y.len()).map(|i| (word >> i) & 1).collect();
            let mut log_weight = bits
                .iter()
                .zip(prior)
                .map(|(b, p)| 0.5 * level(*b) * p)
                .sum::<f64>();
            for i in 1..y.len() - 1 {
                let mean = c.bias
                    + (0..3)
                        .map(|j| c.taps[j] * level(bits[i + j - 1]))
                        .sum::<f64>();
                log_weight -= (y[i] - mean).powi(2) / (2.0 * c.noise_variance);
            }
            let weight = log_weight.exp();
            for i in 0..y.len() {
                sums[i][bits[i]] += weight;
            }
        }
        sums.iter().map(|s| (s[1] / s[0]).ln()).collect()
    }

    #[test]
    fn matches_exhaustive_bit_marginals_and_extrinsic() {
        for n in 3..=10 {
            let y: Vec<_> = (0..n).map(|i| (i as f64 * 1.73).sin()).collect();
            let prior: Vec<_> = (0..n).map(|i| (i as f64 * 0.79).cos()).collect();
            let actual = detect(&y, &channel(), &prior).unwrap();
            for (i, expected) in exhaustive(&y, &channel(), &prior).iter().enumerate() {
                assert!((actual.posterior_llr[i] - expected).abs() < 1e-10);
                assert!((actual.extrinsic_llr[i] - (expected - prior[i])).abs() < 1e-10);
            }
        }
    }

    #[test]
    fn memoryless_llr_and_prior_exclusion_are_analytic() {
        let c = Channel {
            taps: [0.0, 1.0, 0.0],
            bias: 0.2,
            noise_variance: 0.5,
        };
        let y = [9.0, -0.8, 0.45, 1.2, 9.0];
        let prior = [5.0, -3.0, 2.0, 4.0, -5.0];
        let out = detect(&y, &c, &prior).unwrap();
        for (i, &sample) in y.iter().enumerate() {
            let expected = if i == 0 || i == 4 {
                0.0
            } else {
                4.0 * (sample - 0.2)
            };
            assert!((out.extrinsic_llr[i] - expected).abs() < 1e-10);
        }
    }

    #[test]
    fn subtracts_prior_before_saturation() {
        let c = Channel {
            taps: [0.0, 1.0, 0.0],
            bias: 0.0,
            noise_variance: 1.0,
        };
        let out = detect(&[0.0, 40.0, 0.0], &c, &[0.0, 80.0, 0.0]).unwrap();
        assert_eq!(out.posterior_llr[1], 100.0);
        assert!((out.extrinsic_llr[1] - 80.0).abs() < 1e-10);
    }

    #[test]
    fn no_data_evidence_does_not_create_confidence() {
        let c = Channel {
            taps: [0.0, 1.0, 0.0],
            bias: 0.0,
            noise_variance: 1.0,
        };
        let out = detect(&[0.0; 32], &c, &[]).unwrap();
        assert!(out.posterior_llr.iter().all(|x| x.abs() < 1e-12));
    }

    #[test]
    fn rejects_invalid_work_and_numeric_inputs() {
        assert!(detect(&[0.0; 2], &channel(), &[]).is_err());
        assert!(detect(&[0.0, f64::NAN, 0.0], &channel(), &[]).is_err());
        assert!(detect(&[0.0; 3], &channel(), &[0.0]).is_err());
        assert!(detect(&[0.0; 3], &channel(), &[101.0; 3]).is_err());
        let mut c = channel();
        c.noise_variance = 0.0;
        assert!(detect(&[0.0; 3], &c, &[]).is_err());
    }

    #[test]
    fn varying_channel_matches_independent_enumeration_and_constant_path() {
        let y: Vec<_> = (0..8).map(|i| (i as f64 * 1.1).sin()).collect();
        let channels: Vec<_> = (0..8)
            .map(|i| Channel {
                taps: [0.2, 0.7 + i as f64 * 0.07, -0.15],
                bias: 0.02 * i as f64,
                noise_variance: 0.7,
            })
            .collect();
        let prior: Vec<_> = (0..8).map(|i| i as f64 * 0.03 - 0.1).collect();
        let mut sums = [[0.; 2]; 8];
        let mut weights = Vec::new();
        for word in 0..256usize {
            let bits: Vec<_> = (0..8).map(|i| (word >> i) & 1).collect();
            let mut score = bits
                .iter()
                .zip(&prior)
                .map(|(b, p)| 0.5 * level(*b) * p)
                .sum::<f64>();
            for i in 1..7 {
                let c = &channels[i];
                let mean = c.bias
                    + (0..3)
                        .map(|j| c.taps[j] * level(bits[i + j - 1]))
                        .sum::<f64>();
                score -= 0.5 * (y[i] - mean).powi(2) / c.noise_variance;
            }
            let weight = score.exp();
            for i in 0..8 {
                sums[i][bits[i]] += weight;
            }
            weights.push((bits, weight));
        }
        let (result, stats) = detect_statistics(&y, &channels, &prior, 16).unwrap();
        for (i, sum) in sums.iter().enumerate() {
            assert!((result.posterior_llr[i] - (sum[1] / sum[0]).ln()).abs() < 1e-10);
        }
        let mass = weights.iter().map(|(_, w)| w).sum::<f64>();
        let mut cross = [0.; 4];
        let mut gram = [[0.; 4]; 4];
        for (bits, weight) in weights {
            for i in 1..7 {
                let x = [1., level(bits[i - 1]), level(bits[i]), level(bits[i + 1])];
                for j in 0..4 {
                    cross[j] += weight / mass * x[j] * y[i];
                    for k in 0..4 {
                        gram[j][k] += weight / mass * x[j] * x[k];
                    }
                }
            }
        }
        for j in 0..4 {
            assert!((stats[0].cross[j] - cross[j]).abs() < 1e-10);
            for (k, value) in gram[j].iter().enumerate() {
                assert!((stats[0].gram[j][k] - value).abs() < 1e-10);
            }
        }
        let constant = channel();
        let a = detect(&y, &constant, &prior).unwrap();
        let b = detect_varying(&y, &vec![constant; 8], &prior).unwrap();
        assert_eq!(a.posterior_llr, b.posterior_llr);
        assert_eq!(a.extrinsic_llr, b.extrinsic_llr);
        assert!(detect_varying(&y, &channels[..3], &prior).is_err());
    }

    #[test]
    fn complete_boundaries_match_exhaustive_marginals_and_statistics() {
        // Enumerate full bit strings including two independent exterior bits.
        // Every received observation enters the likelihood exactly once.
        for n in 1..=8 {
            let y: Vec<_> = (0..n).map(|i| (i as f64 * 1.29 + 0.2).sin()).collect();
            let prior: Vec<_> = (0..n).map(|i| i as f64 * 0.07 - 0.3).collect();
            let channels: Vec<_> = (0..n)
                .map(|i| Channel {
                    taps: [0.25, 0.9 + 0.03 * i as f64, -0.35],
                    bias: 0.04 * i as f64,
                    noise_variance: 0.6,
                })
                .collect();
            let mut sums = vec![[0.; 2]; n];
            let mut mass = 0.;
            let mut expected = Statistics::default();
            for word in 0..1usize << (n + 2) {
                let bits: Vec<_> = (0..n + 2).map(|i| (word >> i) & 1).collect();
                let mut score = prior
                    .iter()
                    .enumerate()
                    .map(|(i, p)| 0.5 * p * level(bits[i + 1]))
                    .sum::<f64>();
                for i in 0..n {
                    let c = &channels[i];
                    let mean = c.bias + (0..3).map(|j| c.taps[j] * level(bits[i + j])).sum::<f64>();
                    score -= 0.5 * (y[i] - mean).powi(2) / c.noise_variance;
                }
                let weight = score.exp();
                mass += weight;
                for i in 0..n {
                    sums[i][bits[i + 1]] += weight;
                    let x = [1., level(bits[i]), level(bits[i + 1]), level(bits[i + 2])];
                    for j in 0..4 {
                        expected.cross[j] += weight * y[i] * x[j];
                        for k in 0..4 {
                            expected.gram[j][k] += weight * x[j] * x[k];
                        }
                    }
                }
            }
            let (out, stats) = detect_complete_statistics(&y, &channels, &prior, 16).unwrap();
            assert_eq!(out.hard_bits.len(), n);
            assert_eq!(stats.len(), 1);
            assert_eq!(stats[0].count, n);
            assert!((stats[0].energy - y.iter().map(|v| v * v).sum::<f64>()).abs() < 1e-10);
            for i in 0..n {
                let llr = (sums[i][1] / sums[i][0]).ln();
                assert!((out.posterior_llr[i] - llr).abs() < 1e-10, "n={n}, i={i}");
                assert!((out.extrinsic_llr[i] - (llr - prior[i])).abs() < 1e-10);
            }
            for j in 0..4 {
                assert!((stats[0].cross[j] - expected.cross[j] / mass).abs() < 1e-10);
                for k in 0..4 {
                    assert!((stats[0].gram[j][k] - expected.gram[j][k] / mass).abs() < 1e-10);
                }
            }
        }
    }

    #[test]
    fn complete_near_memoryless_recovers_uncoded_endpoint_bits_legacy_is_unchanged() {
        let bits = [1u8, 0, 1, 1, 0, 0, 1, 0, 1, 1, 0, 1];
        let c = Channel {
            taps: [0.002, 1., -0.001],
            bias: 0.1,
            noise_variance: 0.02,
        };
        let y: Vec<_> = bits
            .iter()
            .enumerate()
            .map(|(i, b)| {
                let previous = if i == 0 {
                    -1.
                } else {
                    level(bits[i - 1] as usize)
                };
                let next = if i + 1 == bits.len() {
                    1.
                } else {
                    level(bits[i + 1] as usize)
                };
                c.bias + c.taps[0] * previous + level(*b as usize) + c.taps[2] * next
            })
            .collect();
        let complete = detect_complete(&y, &c, &[]).unwrap();
        assert_eq!(complete.hard_bits, bits);
        assert!(complete.extrinsic_llr[0] > 90.);
        assert!(complete.extrinsic_llr[bits.len() - 1] > 90.);
        let memoryless = Channel {
            taps: [0., 1., 0.],
            ..c.clone()
        };
        let legacy = detect(&y, &memoryless, &[]).unwrap();
        assert_eq!(legacy.extrinsic_llr[0], 0.);
        assert_eq!(legacy.extrinsic_llr[y.len() - 1], 0.);
        let full = detect_complete(&y, &memoryless, &[]).unwrap();
        assert_eq!(full.hard_bits, bits);
        let (varying, _) =
            detect_complete_statistics(&y, &vec![memoryless; y.len()], &[], 16).unwrap();
        assert_eq!(full.posterior_llr, varying.posterior_llr);
        assert_eq!(full.extrinsic_llr, varying.extrinsic_llr);
    }

    #[test]
    fn complete_segments_are_aligned_to_real_observations_not_padding() {
        let y: Vec<_> = (0..34).map(|i| (i as f64 * 0.9).sin()).collect();
        let channels: Vec<_> = (0..34)
            .map(|i| Channel {
                taps: [0., 1. + 0.01 * i as f64, 0.],
                bias: 0.02 * i as f64,
                noise_variance: 0.8,
            })
            .collect();
        let (out, stats) = detect_complete_statistics(&y, &channels, &[], 16).unwrap();
        assert_eq!(
            stats.iter().map(|s| s.count).collect::<Vec<_>>(),
            vec![16, 16, 2]
        );
        for (segment, samples) in stats.iter().zip(y.chunks(16)) {
            assert!((segment.energy - samples.iter().map(|v| v * v).sum::<f64>()).abs() < 1e-10);
            assert!((segment.gram[0][0] - samples.len() as f64).abs() < 1e-10);
        }
        for i in 0..y.len() {
            let c = &channels[i];
            let expected = 2. * c.taps[1] * (y[i] - c.bias) / c.noise_variance;
            assert!((out.extrinsic_llr[i] - expected).abs() < 1e-10);
        }
    }

    #[test]
    fn complete_api_rejects_invalid_lengths_and_preserves_prior_exclusion() {
        assert!(detect_complete(&[], &channel(), &[]).is_err());
        assert!(detect_complete(&[f64::NAN], &channel(), &[]).is_err());
        assert!(detect_complete(&[0.], &channel(), &[0., 0.]).is_err());
        assert!(detect_complete(&[0.], &channel(), &[101.]).is_err());
        assert!(detect_complete_statistics(&[0.; 3], &[], &[], 16).is_err());
        assert!(detect_complete_statistics(&[0.; 3], &[channel()], &[], 15).is_err());
        let c = Channel {
            taps: [0., 1., 0.],
            bias: 0.,
            noise_variance: 1.,
        };
        let out = detect_complete(&[40.], &c, &[80.]).unwrap();
        assert_eq!(out.posterior_llr, vec![100.]);
        assert!((out.extrinsic_llr[0] - 80.).abs() < 1e-10);
    }
}
