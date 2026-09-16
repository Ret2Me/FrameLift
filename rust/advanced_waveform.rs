//! Explicit waveform contracts for the experimental multi-mode receiver.
//! Remodulation is a checked SIC hypothesis, not a replacement for received IQ.
use crate::{advanced_iq::Config, psk::MatchedFilter};
use num_complex::Complex64;
use serde::{Deserialize, Serialize};
use std::f64::consts::{PI, TAU};

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case", deny_unknown_fields)]
pub enum Waveform {
    Psk {
        #[serde(default)]
        pulse: MatchedFilter,
    },
    /// Continuous-phase binary FSK. Gaussian BT is mandatory for GFSK/GMSK.
    Fsk {
        deviation_hz: f64,
        gaussian_bt: Option<f64>,
    },
    /// Bell-202-like audio tones inside an FM carrier, not direct RF two-FSK.
    Afsk {
        mark_hz: f64,
        space_hz: f64,
        fm_deviation_hz: f64,
    },
}

pub(crate) fn quadrature(c: &Config) -> bool {
    matches!(c.modulation.as_str(), "qpsk" | "oqpsk")
}
pub(crate) fn psk(c: &Config) -> bool {
    matches!(
        c.modulation.as_str(),
        "bpsk" | "bpsk_rectangular" | "qpsk" | "oqpsk"
    )
}
pub(crate) fn resolved(c: &Config) -> Waveform {
    c.waveform.clone().unwrap_or(Waveform::Psk {
        pulse: MatchedFilter::Rectangular,
    })
}
pub(crate) fn validate(c: &Config, rate: u32) -> Result<(), String> {
    let nyquist = rate as f64 / 2.;
    let extent = c
        .carrier_centers_hz
        .iter()
        .map(|v| v.abs())
        .fold(0., f64::max)
        + c.residual_carrier_bound_hz;
    match (c.modulation.as_str(), resolved(c)) {
        (
            "bpsk_rectangular",
            Waveform::Psk {
                pulse: MatchedFilter::Rectangular,
            },
        ) => (),
        ("bpsk" | "qpsk" | "oqpsk", Waveform::Psk { pulse }) => {
            if quadrature(c)
                && (c.syncword.len() < 64
                    || !c.syncword.len().is_multiple_of(2)
                    || !c.coded_bits().is_multiple_of(2))
            {
                return Err(
                    "quadrature acquisition needs an even sync of >=64 bits and an even codeword"
                        .into(),
                );
            }
            let order = if quadrature(c) { 4. } else { 2. };
            if c.residual_carrier_bound_hz * order >= nyquist {
                return Err("Mth-power carrier search would alias".into());
            }
            if let MatchedFilter::RootRaisedCosine {
                rolloff,
                span_symbols,
            } = pulse
                && (!rolloff.is_finite()
                    || !(0.01..=1.).contains(&rolloff)
                    || !(2..=16).contains(&span_symbols)
                    || extent + c.symbol_rate * (1. + rolloff) / 2. >= nyquist)
            {
                return Err("invalid or aliased explicit RRC pulse".into());
            }
        }
        (
            mode @ ("fsk" | "gfsk" | "gmsk"),
            Waveform::Fsk {
                deviation_hz,
                gaussian_bt,
            },
        ) => {
            if !deviation_hz.is_finite()
                || deviation_hz <= 0.
                || extent + deviation_hz >= nyquist
                || (mode == "fsk" && gaussian_bt.is_some())
                || (mode != "fsk" && gaussian_bt.is_none())
                || gaussian_bt.is_some_and(|bt| !bt.is_finite() || !(0.2..=1.).contains(&bt))
                || (mode == "gmsk" && (deviation_hz / c.symbol_rate - 0.25).abs() > 1e-9)
            {
                return Err(
                    "FSK requires finite deviation; GFSK/GMSK need BT 0.2..1; GMSK h=0.5".into(),
                );
            }
        }
        (
            "afsk",
            Waveform::Afsk {
                mark_hz,
                space_hz,
                fm_deviation_hz,
            },
        ) => {
            if [mark_hz, space_hz, fm_deviation_hz]
                .iter()
                .any(|v| !v.is_finite() || *v <= 0.)
                || mark_hz == space_hz
                || mark_hz.max(space_hz) >= nyquist
                || extent + fm_deviation_hz >= nyquist
                || rate as f64 / c.symbol_rate < 8.
            {
                return Err(
                    "AFSK needs explicit distinct tones, FM deviation and >=8 samples/bit".into(),
                );
            }
        }
        _ => {
            return Err(
                "modulation/waveform mismatch; supported: bpsk, qpsk, oqpsk, fsk, gfsk, gmsk, afsk"
                    .into(),
            );
        }
    }
    Ok(())
}

pub(crate) fn rrc(t: f64, a: f64) -> f64 {
    if t.abs() < 1e-12 {
        1. + a * (4. / PI - 1.)
    } else if (4. * a * t.abs() - 1.).abs() < 1e-10 {
        a / 2f64.sqrt()
            * ((1. + 2. / PI) * (PI / (4. * a)).sin() + (1. - 2. / PI) * (PI / (4. * a)).cos())
    } else {
        ((PI * t * (1. - a)).sin() + 4. * a * t * (PI * t * (1. + a)).cos())
            / (PI * t * (1. - (4. * a * t).powi(2)))
    }
}

fn arm(bits: &[u8], stride: usize, branch: usize, at: f64, pulse: &MatchedFilter) -> f64 {
    let count = bits.len() / stride;
    let symbol = |k: isize| {
        if k < 0 || k as usize >= count {
            0.
        } else {
            2. * f64::from(bits[k as usize * stride + branch]) - 1.
        }
    };
    match pulse {
        MatchedFilter::Rectangular => symbol(at.floor() as isize),
        MatchedFilter::RootRaisedCosine {
            rolloff,
            span_symbols,
        } => {
            let center = at - 0.5;
            let half = *span_symbols as f64 / 2.;
            ((center - half).ceil() as isize..=(center + half).floor() as isize)
                .map(|k| symbol(k) * rrc(center - k as f64, *rolloff))
                .sum()
        }
    }
}

/// Centered sampled Gaussian, unit DC gain, +/- two symbol truncation.
pub(crate) fn gaussian(values: &[f64], sps: f64, bt: f64) -> Vec<f64> {
    let half = (2. * sps).ceil() as isize;
    let taps: Vec<_> = (-half..=half)
        .map(|i| (-2. * PI * PI * bt * bt * (i as f64 / sps).powi(2) / 2f64.ln()).exp())
        .collect();
    let norm = taps.iter().sum::<f64>();
    (0..values.len())
        .map(|i| {
            taps.iter()
                .enumerate()
                .map(|(j, h)| {
                    let k = i as isize + j as isize - half;
                    if k < 0 || k as usize >= values.len() {
                        0.
                    } else {
                        h * values[k as usize] / norm
                    }
                })
                .sum()
        })
        .collect()
}

/// Geometry refers to the beginning of the undelayed symbol, not a filter peak.
#[derive(Clone, Debug)]
pub(crate) struct Geometry {
    pub start: f64,
    pub step: f64,
    pub carrier: f64,
    pub delayed_q: bool,
    pub conjugated: bool,
}

pub(crate) fn bounds(c: &Config, g: &Geometry, bits: usize) -> (usize, usize) {
    let stride = if quadrature(c) { 2 } else { 1 };
    let tail = if c.modulation == "oqpsk" { 0.5 } else { 0. };
    let half = match resolved(c) {
        Waveform::Psk {
            pulse: MatchedFilter::RootRaisedCosine { span_symbols, .. },
        } => span_symbols as f64 / 2.,
        _ => 0.,
    };
    (
        (g.start - half * g.step).ceil().max(0.) as usize,
        (g.start + (bits as f64 / stride as f64 + tail + half) * g.step).ceil() as usize,
    )
}

/// Rebuild only a validated transmission. AFSK audio phase is estimated from
/// received discriminator samples; it is never taken from a fixture/truth file.
pub(crate) fn reconstruct(
    iq: &[Complex64],
    bits: &[u8],
    rate: u32,
    c: &Config,
    g: &Geometry,
) -> Result<(usize, Vec<Complex64>), String> {
    let (begin, end) = bounds(c, g, bits.len());
    if end > iq.len() || begin >= end || bits.iter().any(|b| *b > 1) {
        return Err("waveform reconstruction leaves source bounds".into());
    }
    let mut out = Vec::with_capacity(end - begin);
    let mut reconstructed_carrier = g.carrier;
    match resolved(c) {
        Waveform::Psk { pulse } => {
            let stride = if quadrature(c) { 2 } else { 1 };
            for i in begin..end {
                let t = (i as f64 - g.start) / g.step;
                let delay = if c.modulation == "oqpsk" { 0.5 } else { 0. };
                let re = arm(
                    bits,
                    stride,
                    0,
                    t - if g.delayed_q { 0. } else { delay },
                    &pulse,
                );
                let im = if stride == 2 {
                    arm(bits, 2, 1, t - if g.delayed_q { delay } else { 0. }, &pulse)
                } else {
                    0.
                };
                out.push(Complex64::new(re, if g.conjugated { -im } else { im }));
            }
        }
        Waveform::Fsk {
            deviation_hz,
            gaussian_bt,
        } => {
            let levels: Vec<_> = (begin..end)
                .map(|i| {
                    let k = ((i as f64 - g.start) / g.step).floor() as usize;
                    2. * f64::from(bits[k.min(bits.len() - 1)]) - 1.
                })
                .collect();
            let frequency = if let Some(bt) = gaussian_bt {
                gaussian(&levels, g.step, bt)
            } else {
                levels
            };
            let mut phase = 0.;
            for level in frequency {
                phase += TAU * deviation_hz * level / rate as f64;
                out.push(Complex64::from_polar(1., phase));
            }
        }
        Waveform::Afsk {
            mark_hz,
            space_hz,
            fm_deviation_hz,
        } => {
            let mut theta = 0.;
            let mut tones = Vec::with_capacity(end - begin);
            for i in begin..end {
                let k = ((i as f64 - g.start) / g.step).floor() as usize;
                theta +=
                    TAU * if bits[k.min(bits.len() - 1)] == 1 {
                        mark_hz
                    } else {
                        space_hz
                    } / rate as f64;
                tones.push(theta);
            }
            // Data-aided reconstruction AFTER independent FEC+CRC validation.
            // Fit tone phase/DC on training samples of the verified waveform;
            // acquisition itself still sees only the known sync, never payload.
            let mut gram = [[0.; 4]; 4];
            let mut cross = [0.; 4];
            let fit_end = end;
            for i in (begin + 1)..fit_end {
                // The discriminator also consumes i-1; exclude boundaries
                // that would leak a held-out sample into the tone-phase fit.
                if !crate::interference::training_sample(i, g.step)
                    || !crate::interference::training_sample(i - 1, g.step)
                {
                    continue;
                }
                let t = tones[i - begin];
                let x = [
                    1.,
                    t.sin(),
                    t.cos(),
                    (i - begin) as f64 / (fit_end - begin) as f64,
                ];
                let y = (iq[i] * iq[i - 1].conj()).arg() * rate as f64 / TAU - g.carrier;
                for j in 0..4 {
                    cross[j] += x[j] * y;
                    for k in 0..4 {
                        gram[j][k] += x[j] * x[k];
                    }
                }
            }
            let fit = crate::joint_sequence::solve(gram, cross)
                .ok_or("AFSK pilot phase fit is rank deficient")?;
            let amplitude = fit[1].hypot(fit[2]);
            if fit[0].abs() > c.residual_carrier_bound_hz {
                return Err("AFSK pilot carrier leaves configured search bound".into());
            }
            reconstructed_carrier += fit[0];
            if !(0.5 * fm_deviation_hz..=1.5 * fm_deviation_hz).contains(&amplitude) {
                return Err("AFSK pilot contradicts FM deviation profile".into());
            }
            let phase_audio = fit[2].atan2(fit[1]);
            let mut phase = 0.;
            for t in tones {
                phase += TAU * fm_deviation_hz * (t + phase_audio).sin() / rate as f64;
                out.push(Complex64::from_polar(1., phase));
            }
        }
    }
    for (offset, value) in out.iter_mut().enumerate() {
        *value *= Complex64::from_polar(
            1.,
            TAU * reconstructed_carrier * (begin + offset) as f64 / rate as f64,
        );
    }
    Ok((begin, out))
}

pub(crate) fn refine_reconstruction(
    iq: &[Complex64],
    bits: &[u8],
    rate: u32,
    c: &Config,
    g: &Geometry,
) -> Result<(usize, Vec<Complex64>), String> {
    if c.modulation != "afsk" {
        return reconstruct(iq, bits, rate, c, g);
    }
    let mut best = None;
    let mut best_score = f64::NEG_INFINITY;
    // Parameter selection uses training samples only. Exactly one winner is
    // checked against the holdout; no retry based on holdout or CRC outcomes.
    for trial in -4..=4 {
        let mut geometry = g.clone();
        geometry.start += trial as f64 * g.step / c.phase_bins as f64 / 4.;
        if geometry.start < 0. {
            continue;
        }
        let Ok((begin, model)) = reconstruct(iq, bits, rate, c, &geometry) else {
            continue;
        };
        let score = crate::interference::model_score(iq, &model, begin, g.step);
        if score > best_score {
            best_score = score;
            best = Some((begin, model));
        }
    }
    best.ok_or_else(|| "AFSK training-only timing bank rejected all models".into())
}
