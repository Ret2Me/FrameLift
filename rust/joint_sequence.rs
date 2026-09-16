//! Damped, pilot-regularized channel updates from exact BCJR triple marginals.
//! No CRC or expected payload is an optimization objective. Carrier/timing
//! acquisition is upstream; this module tracks a real, slowly varying ISI channel.
use crate::soft_sequence::{Channel, Statistics};

pub(crate) fn solve(mut a: [[f64; 4]; 4], mut b: [f64; 4]) -> Option<[f64; 4]> {
    for i in 0..4 {
        let pivot = (i..4).max_by(|&u, &v| a[u][i].abs().total_cmp(&a[v][i].abs()))?;
        if a[pivot][i].abs() < 1e-10 {
            return None;
        }
        a.swap(i, pivot);
        b.swap(i, pivot);
        let divisor = a[i][i];
        for value in &mut a[i][i..] {
            *value /= divisor;
        }
        b[i] /= divisor;
        for j in 0..4 {
            if j == i {
                continue;
            }
            let weight = a[j][i];
            let pivot = a[i];
            for (value, reference) in a[j][i..].iter_mut().zip(&pivot[i..]) {
                *value -= weight * reference;
            }
            b[j] -= weight * b[i];
        }
    }
    b.iter().all(|v| v.is_finite()).then_some(b)
}

pub(crate) fn estimate(
    stats: &Statistics,
    anchor: &Channel,
    previous: &Channel,
    damping: f64,
) -> Option<Channel> {
    if stats.count < 8 {
        return None;
    }
    let initial = [anchor.bias, anchor.taps[0], anchor.taps[1], anchor.taps[2]];
    let mut gram = stats.gram;
    let mut cross = stats.cross;
    // Fixed prior strength, not tuned using CRC results or transmitted bytes.
    let ridge = 8.0;
    for i in 0..4 {
        gram[i][i] += ridge;
        cross[i] += ridge * initial[i];
    }
    let fit = solve(gram, cross)?;
    let scale = anchor
        .taps
        .iter()
        .map(|v| v * v)
        .sum::<f64>()
        .sqrt()
        .max(1e-6);
    if fit
        .iter()
        .zip(initial)
        .any(|(a, b)| (a - b).abs() > 2.0 * scale)
    {
        return None;
    }
    let mut residual = stats.energy;
    for i in 0..4 {
        residual -= 2.0 * fit[i] * stats.cross[i];
        for j in 0..4 {
            residual += fit[i] * fit[j] * stats.gram[i][j];
        }
    }
    let variance = (residual / stats.count as f64)
        .clamp(anchor.noise_variance * 0.25, anchor.noise_variance * 4.0);
    let blend = |a: f64, b: f64| (1.0 - damping) * a + damping * b;
    let channel = Channel {
        bias: blend(previous.bias, fit[0]),
        taps: std::array::from_fn(|i| blend(previous.taps[i], fit[i + 1])),
        noise_variance: blend(previous.noise_variance, variance).clamp(1e-12, 1e12),
    };
    channel.validate().ok()?;
    Some(channel)
}

/// Preamble-only least-squares initialization, independent of packet contents.
pub(crate) fn pilot(samples: &[f64], bits: &[u8]) -> Result<Channel, String> {
    if samples.len() != bits.len()
        || bits.len() < 32
        || samples.iter().any(|v| !v.is_finite() || v.abs() > 1e6)
        || bits.iter().any(|b| *b > 1)
    {
        return Err("channel pilot needs 32 or more finite known binary symbols".into());
    }
    let mut stats = Statistics::default();
    for i in 1..bits.len() - 1 {
        let x = [
            1.0,
            2.0 * f64::from(bits[i - 1]) - 1.0,
            2.0 * f64::from(bits[i]) - 1.0,
            2.0 * f64::from(bits[i + 1]) - 1.0,
        ];
        stats.count += 1;
        stats.energy += samples[i] * samples[i];
        for j in 0..4 {
            stats.cross[j] += x[j] * samples[i];
            for k in 0..4 {
                stats.gram[j][k] += x[j] * x[k];
            }
        }
    }
    let fit = solve(stats.gram, stats.cross).ok_or("rank-deficient channel pilot")?;
    let power = fit[1..].iter().map(|v| v * v).sum::<f64>();
    if power < 1e-12 {
        return Err("pilot has no channel energy".into());
    }
    let residual =
        (stats.energy - fit.iter().zip(stats.cross).map(|(a, b)| a * b).sum::<f64>()).max(0.0);
    let channel = Channel {
        bias: fit[0],
        taps: [fit[1], fit[2], fit[3]],
        noise_variance: (residual / (stats.count - 4) as f64)
            .max(power * 1e-3)
            .clamp(1e-12, 1e12),
    };
    channel.validate()?;
    Ok(channel)
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn solves_full_rank_and_rejects_singular() {
        let a = [
            [2., 0., 0., 0.],
            [0., 3., 0., 0.],
            [0., 0., 4., 0.],
            [0., 0., 0., 5.],
        ];
        assert_eq!(solve(a, [2., 6., 12., 20.]).unwrap(), [1., 2., 3., 4.]);
        assert!(solve([[0.; 4]; 4], [0.; 4]).is_none());
    }
    #[test]
    fn posterior_statistics_track_known_varying_channel() {
        // A full-rank sync word, not a periodic three-state pattern whose
        // adjacent-symbol columns are linearly dependent.
        let marker = [0x1au8, 0xcf, 0xfc, 0x1d];
        let bits: Vec<_> = (0..256)
            .map(|i| (marker[(i / 8) % 4] >> (7 - i % 8)) & 1)
            .collect();
        let x: Vec<_> = bits.iter().map(|b| 2.0 * f64::from(*b) - 1.0).collect();
        let y: Vec<_> = (0..256)
            .map(|i| (1.0 + 0.25 * i as f64 / 256.0) * x[i])
            .collect();
        let initial = Channel {
            taps: [0., 1., 0.],
            bias: 0.,
            noise_variance: 0.1,
        };
        let prior: Vec<_> = x.iter().map(|v| v * 30.0).collect();
        let (_, stats) =
            crate::soft_sequence::detect_statistics(&y, std::slice::from_ref(&initial), &prior, 64)
                .unwrap();
        let fitted: Vec<_> = stats
            .iter()
            .map(|s| estimate(s, &initial, &initial, 1.0).unwrap())
            .collect();
        assert!(fitted[3].taps[1] > fitted[0].taps[1] + 0.1);
        let pilot = pilot(&y[..64], &bits[..64]).unwrap();
        assert!(pilot.taps[1] > 0.9);
        assert!(super::pilot(&[0.; 32], &[0; 32]).is_err());
    }
}
