//! Checked subtractive interference cancellation with held-out waveform fits.
//! A parity-and-CRC verified codeword is reconstructed upstream. The waveform
//! model is fitted on even symbols and tested on odd symbols before subtraction.
use num_complex::Complex64;
use serde::{Deserialize, Serialize};

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Config {
    pub rounds: usize,
    pub minimum_holdout_reduction: f64,
}
impl Config {
    pub fn validate(&self) -> Result<(), String> {
        if !(1..=3).contains(&self.rounds)
            || !self.minimum_holdout_reduction.is_finite()
            || !(0.05..=0.95).contains(&self.minimum_holdout_reduction)
        {
            return Err("SIC requires 1..3 rounds and holdout reduction in [0.05,0.95]".into());
        }
        Ok(())
    }
}

#[derive(Clone, Debug, Serialize)]
pub struct Receipt {
    pub round: usize,
    pub frame_sha256: Option<String>,
    pub start_sample: usize,
    pub end_sample: usize,
    pub holdout_reduction: f64,
    pub accepted: bool,
    pub reason: &'static str,
}

/// Complex gain/DC fit on alternating symbol intervals. The other intervals
/// must improve, and total source energy must decrease before ANY mutation.
/// DC is a fitted nuisance; only the decoded waveform is subtracted.
pub(crate) fn subtract_model(
    iq: &mut [Complex64],
    model: &[Complex64],
    begin: usize,
    step: f64,
    minimum_reduction: f64,
    carrier_correction: [f64; 2],
) -> Result<Receipt, String> {
    let end = begin.checked_add(model.len()).ok_or("SIC range overflow")?;
    if end > iq.len()
        || model.len() < 32
        || !step.is_finite()
        || !(2.0..=128.).contains(&step)
        || !(0.05..=0.95).contains(&minimum_reduction)
        || carrier_correction.iter().any(|v| !v.is_finite())
        || carrier_correction[0] > carrier_correction[1]
        || carrier_correction
            .iter()
            .any(|v| v.abs() >= std::f64::consts::PI)
        || iq[begin..end]
            .iter()
            .chain(model)
            .any(|z| !z.re.is_finite() || !z.im.is_finite())
    {
        return Err("invalid waveform SIC geometry or nonfinite samples".into());
    }
    let training = |i: usize| training_sample(begin + i, step);
    // A pilot-only frequency estimate can be biased by the weaker transmitter.
    // Refine residual phase slope using verified symbols on TRAINING intervals
    // only. No holdout value enters this fit or a parameter search.
    let (mut weight, mut sum_t, mut sum_p, mut sum_tt, mut sum_tp) = (0., 0., 0., 0., 0.);
    let mut previous: Option<f64> = None;
    for (i, x) in model.iter().enumerate() {
        if !training(i) || x.norm_sqr() < 1e-8 {
            continue;
        }
        let cross = iq[begin + i] * x.conj();
        if cross.norm_sqr() < 1e-12 {
            continue;
        }
        let raw = cross.arg();
        let phase = previous.map_or(raw, |p| {
            p + (raw - p + std::f64::consts::PI).rem_euclid(std::f64::consts::TAU)
                - std::f64::consts::PI
        });
        previous = Some(phase);
        let w = x.norm_sqr();
        let t = i as f64;
        weight += w;
        sum_t += w * t;
        sum_p += w * phase;
        sum_tt += w * t * t;
        sum_tp += w * t * phase;
    }
    let determinant = weight * sum_tt - sum_t * sum_t;
    let slope = if determinant > 1e-12 {
        (weight * sum_tp - sum_t * sum_p) / determinant
    } else {
        0.
    };
    // Out-of-profile channel fits are rejected, not clipped into acceptance.
    if !slope.is_finite() || slope < carrier_correction[0] || slope > carrier_correction[1] {
        return Ok(Receipt {
            round: 0,
            frame_sha256: None,
            start_sample: begin,
            end_sample: end,
            holdout_reduction: 0.,
            accepted: false,
            reason: "carrier_refinement_rejected",
        });
    }
    let refined: Vec<_> = model
        .iter()
        .enumerate()
        .map(|(i, z)| z * Complex64::from_polar(1., slope * i as f64))
        .collect();
    let model = refined.as_slice();
    let mut count = 0.;
    let mut x_mean = Complex64::new(0., 0.);
    let mut y_mean = x_mean;
    for (i, x) in model.iter().enumerate() {
        if training(i) {
            count += 1.;
            x_mean += x;
            y_mean += iq[begin + i];
        }
    }
    x_mean /= count;
    y_mean /= count;
    let mut energy = 0.;
    let mut cross = Complex64::new(0., 0.);
    for (i, x) in model.iter().enumerate() {
        if training(i) {
            let x = x - x_mean;
            energy += x.norm_sqr();
            cross += x.conj() * (iq[begin + i] - y_mean);
        }
    }
    let mut receipt = Receipt {
        round: 0,
        frame_sha256: None,
        start_sample: begin,
        end_sample: end,
        holdout_reduction: 0.,
        accepted: false,
        reason: "rank_deficient",
    };
    if energy < 1e-12 {
        return Ok(receipt);
    }
    let gain = cross / energy;
    let (mut before, mut after, mut total_before, mut total_after) = (0., 0., 0., 0.);
    for (i, x) in model.iter().enumerate() {
        let y = iq[begin + i];
        let residual = y - gain * x;
        total_before += y.norm_sqr();
        total_after += residual.norm_sqr();
        if !training(i) {
            before += y.norm_sqr();
            after += residual.norm_sqr();
        }
    }
    if before < 1e-12 {
        receipt.reason = "no_holdout_energy";
        return Ok(receipt);
    }
    receipt.holdout_reduction = 1. - after / before;
    if !receipt.holdout_reduction.is_finite() || !total_after.is_finite() {
        return Err("nonfinite waveform cancellation residual".into());
    }
    if receipt.holdout_reduction < minimum_reduction {
        receipt.reason = "holdout_rejected";
        return Ok(receipt);
    }
    if total_after >= total_before {
        receipt.reason = "total_energy_rejected";
        return Ok(receipt);
    }
    for (y, x) in iq[begin..end].iter_mut().zip(model) {
        *y -= gain * x;
    }
    receipt.accepted = true;
    receipt.reason = "validated_reconstruction_subtracted";
    Ok(receipt)
}

pub(crate) fn training_sample(index: usize, step: f64) -> bool {
    ((index as f64 / step).floor() as usize).is_multiple_of(2)
}

/// Coherent training-only score for a bounded timing bank. The partition is
/// anchored at source sample zero, not at a hypothesis-dependent burst start.
pub(crate) fn model_score(iq: &[Complex64], model: &[Complex64], begin: usize, step: f64) -> f64 {
    let (mut count, mut xx, mut yy) = (0., 0., 0.);
    let (mut xsum, mut ysum, mut xy) = (
        Complex64::new(0., 0.),
        Complex64::new(0., 0.),
        Complex64::new(0., 0.),
    );
    for (i, x) in model.iter().enumerate() {
        if training_sample(begin + i, step) {
            let y = iq[begin + i];
            count += 1.;
            xsum += x;
            ysum += y;
            xx += x.norm_sqr();
            yy += y.norm_sqr();
            xy += x.conj() * y;
        }
    }
    if count < 8. {
        return f64::NEG_INFINITY;
    }
    xx -= xsum.norm_sqr() / count;
    yy -= ysum.norm_sqr() / count;
    xy -= xsum.conj() * ysum / count;
    if xx <= 1e-12 || yy <= 1e-12 {
        f64::NEG_INFINITY
    } else {
        xy.norm_sqr() / (xx * yy)
    }
}

/// Crate-private: callers cannot assert integrity with a boolean. Only the IQ
/// pipeline passes a codeword witness obtained from its FEC+CRC decoder.
pub(crate) fn subtract(
    iq: &mut [Complex64],
    bits: &[u8],
    start: f64,
    step: f64,
    carrier_hz: f64,
    rate: u32,
    minimum_reduction: f64,
) -> Result<Receipt, String> {
    if bits.len() < 32
        || bits.len() > 8192
        || bits.iter().any(|v| *v > 1)
        || !start.is_finite()
        || start < 0.0
        || !step.is_finite()
        || !(2.0..=128.0).contains(&step)
        || rate == 0
        || !carrier_hz.is_finite()
        || carrier_hz.abs() >= rate as f64 / 2.0
        || !minimum_reduction.is_finite()
        || !(0.05..=0.95).contains(&minimum_reduction)
        || iq.iter().any(|v| !v.re.is_finite() || !v.im.is_finite())
    {
        return Err("invalid bounded BPSK cancellation geometry".into());
    }
    let begin = start.ceil() as usize;
    let end = (start + step * bits.len() as f64).ceil() as usize;
    if begin >= end || end > iq.len() {
        return Err("cancellation range exceeds received IQ".into());
    }
    let features = |symbol: usize| {
        [
            1.0,
            if symbol > 0 {
                2.0 * f64::from(bits[symbol - 1]) - 1.0
            } else {
                0.0
            },
            2.0 * f64::from(bits[symbol]) - 1.0,
            if symbol + 1 < bits.len() {
                2.0 * f64::from(bits[symbol + 1]) - 1.0
            } else {
                0.0
            },
        ]
    };
    let mut gram = [[0.; 4]; 4];
    let mut real = [0.; 4];
    let mut imag = [0.; 4];
    for (index, value) in iq.iter().enumerate().take(end).skip(begin) {
        let symbol = ((index as f64 - start) / step).floor() as usize;
        if symbol == 0 || symbol + 1 == bits.len() || !symbol.is_multiple_of(2) {
            continue;
        }
        let x = features(symbol);
        let z = *value
            * Complex64::from_polar(
                1.,
                -std::f64::consts::TAU * carrier_hz * index as f64 / rate as f64,
            );
        for j in 0..4 {
            real[j] += x[j] * z.re;
            imag[j] += x[j] * z.im;
            for k in 0..4 {
                gram[j][k] += x[j] * x[k];
            }
        }
    }
    let mut receipt = Receipt {
        round: 0,
        frame_sha256: None,
        start_sample: begin,
        end_sample: end,
        holdout_reduction: 0.,
        accepted: false,
        reason: "rank_deficient",
    };
    let (Some(real), Some(imag)) = (
        crate::joint_sequence::solve(gram, real),
        crate::joint_sequence::solve(gram, imag),
    ) else {
        return Ok(receipt);
    };
    // Do not cancel fitted DC: it is nuisance, not the decoded transmission.
    let model = |index: usize| {
        let symbol = ((index as f64 - start) / step).floor() as usize;
        let x = features(symbol);
        let z = (1..4)
            .map(|j| Complex64::new(real[j], imag[j]) * x[j])
            .sum::<Complex64>();
        z * Complex64::from_polar(
            1.,
            std::f64::consts::TAU * carrier_hz * index as f64 / rate as f64,
        )
    };
    let mut before = 0.;
    let mut after = 0.;
    for (index, value) in iq.iter().enumerate().take(end).skip(begin) {
        let symbol = ((index as f64 - start) / step).floor() as usize;
        if symbol == 0 || symbol + 1 == bits.len() || symbol.is_multiple_of(2) {
            continue;
        }
        before += value.norm_sqr();
        after += (*value - model(index)).norm_sqr();
    }
    if before < 1e-12 {
        receipt.reason = "no_holdout_energy";
        return Ok(receipt);
    }
    receipt.holdout_reduction = 1. - after / before;
    if !receipt.holdout_reduction.is_finite() {
        return Err("nonfinite cancellation residual".into());
    }
    if receipt.holdout_reduction < minimum_reduction {
        receipt.reason = "holdout_rejected";
        return Ok(receipt);
    }
    let power_before = iq[begin..end].iter().map(|z| z.norm_sqr()).sum::<f64>();
    let power_after = iq
        .iter()
        .enumerate()
        .take(end)
        .skip(begin)
        .map(|(i, z)| (*z - model(i)).norm_sqr())
        .sum::<f64>();
    if !power_after.is_finite() || power_after >= power_before {
        receipt.reason = "total_energy_rejected";
        return Ok(receipt);
    }
    for (index, value) in iq.iter_mut().enumerate().take(end).skip(begin) {
        *value -= model(index);
    }
    receipt.accepted = true;
    receipt.reason = "validated_reconstruction_subtracted";
    Ok(receipt)
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn waveform_sic_refines_frequency_and_rejects_wrong_model_without_mutation() {
        let model: Vec<_> = (0..512)
            .map(|i| {
                Complex64::new(
                    if (i / 4 * 37 + i / 12) % 11 < 5 {
                        1.
                    } else {
                        -1.
                    },
                    if (i / 4 * 19 + i / 20) % 13 < 6 {
                        1.
                    } else {
                        -1.
                    },
                )
            })
            .collect();
        let original: Vec<_> = model
            .iter()
            .enumerate()
            .map(|(i, x)| x * Complex64::from_polar(2., 0.23 + 0.001 * i as f64))
            .collect();
        let mut iq = original.clone();
        assert!(
            subtract_model(&mut iq, &model, 0, 4., 0.2, [-0.01, 0.01])
                .unwrap()
                .accepted
        );
        assert!(iq.iter().map(|v| v.norm_sqr()).sum::<f64>() < 1e-8);
        let wrong: Vec<_> = (0..512)
            .map(|i| Complex64::from_polar(1., i as f64 * 0.13))
            .collect();
        let mut untouched = original.clone();
        assert!(
            !subtract_model(&mut untouched, &wrong, 0, 4., 0.5, [-0.01, 0.01])
                .unwrap()
                .accepted
        );
        assert_eq!(untouched, original);
        assert!(subtract_model(&mut untouched, &model, 0, 4., f64::NAN, [-0.01, 0.01]).is_err());
    }
    #[test]
    fn correct_reconstruction_reduces_energy_and_wrong_model_is_nonmutating() {
        let bits: Vec<_> = (0..128)
            .map(|i| u8::from((i * 37 + i / 5) % 11 < 5))
            .collect();
        let iq: Vec<_> = bits
            .iter()
            .flat_map(|b| vec![Complex64::new(2. * f64::from(*b) - 1., 0.); 4])
            .collect();
        let mut correct = iq.clone();
        let out = subtract(&mut correct, &bits, 0., 4., 0., 4000, 0.1).unwrap();
        assert!(out.accepted);
        assert!(correct.iter().map(|z| z.norm_sqr()).sum::<f64>() < 1e-8);
        let wrong: Vec<_> = (0..128)
            .map(|i| u8::from((i * 19 + i / 3) % 13 < 6))
            .collect();
        let mut untouched = iq.clone();
        assert!(
            !subtract(&mut untouched, &wrong, 0., 4., 0., 4000, 0.5)
                .unwrap()
                .accepted
        );
        assert_eq!(untouched, iq);
    }
}
