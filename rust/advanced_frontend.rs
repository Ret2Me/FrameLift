//! Bounded signal-only acquisition. Protocol validation never ranks hypotheses.
use super::{Candidate, Config};
use crate::{
    advanced_waveform::{self as wave, Geometry, Waveform},
    joint_sequence,
};
use num_complex::Complex64;
use std::f64::consts::{FRAC_PI_2, TAU};

fn mean(prefix: &[Complex64], start: f64, step: f64) -> Complex64 {
    let begin = start.ceil().max(0.) as usize;
    let end = (start + step).ceil().max(0.) as usize;
    if end <= begin || end >= prefix.len() {
        return Complex64::new(0., 0.);
    }
    (prefix[end] - prefix[begin]) / (end - begin) as f64
}
fn at(values: &[Complex64], position: f64) -> Complex64 {
    if position < 0. || position >= (values.len() - 1) as f64 {
        return Complex64::new(0., 0.);
    }
    let i = position.floor() as usize;
    values[i] + (values[i + 1] - values[i]) * (position - i as f64)
}
fn prefix(values: &[Complex64]) -> Vec<Complex64> {
    let mut out = Vec::with_capacity(values.len() + 1);
    out.push(Complex64::new(0., 0.));
    for value in values {
        out.push(*out.last().unwrap() + value);
    }
    out
}

fn scan(
    values: &[f64],
    c: &Config,
    rate: u32,
    geometry: &Geometry,
    iq_len: usize,
    found: &mut Vec<Candidate>,
    search: &mut Option<crate::acquisition::Search<'_>>,
) -> Result<(), String> {
    let arms = if wave::quadrature(c) { 2 } else { 1 };
    let header_bits = c
        .repetition
        .as_ref()
        .map(|r| r.header_bytes() * 8)
        .unwrap_or(0);
    let width = c.syncword.len() + header_bits + c.coded_bits();
    if values.len() < width {
        return Ok(());
    }
    let pattern = c
        .syncword
        .iter()
        .fold(0u128, |w, b| (w << 1) | u128::from(*b));
    let mask = if c.syncword.len() == 128 {
        u128::MAX
    } else {
        (1u128 << c.syncword.len()) - 1
    };
    let mut observed = values[..c.syncword.len()]
        .iter()
        .fold(0u128, |w, x| (w << 1) | u128::from(*x > 0.));
    for start in 0..=values.len() - width {
        if start > 0 {
            observed =
                ((observed << 1) & mask) | u128::from(values[start + c.syncword.len() - 1] > 0.);
        }
        if !start.is_multiple_of(arms) {
            continue;
        }
        let errors = (observed ^ pattern).count_ones() as usize;
        let soft_sync = if errors <= c.maximum_sync_hamming {
            None
        } else if let Some(search) = search {
            let Some(score) = search.real(&values[start..start + c.syncword.len()], errors)? else {
                continue;
            };
            Some(score)
        } else {
            continue;
        };
        let mut channels = Vec::with_capacity(arms);
        for arm in 0..arms {
            let pilot: Vec<_> = values[start..start + c.syncword.len()]
                .iter()
                .skip(arm)
                .step_by(arms)
                .copied()
                .collect();
            let bits: Vec<_> = c.syncword.iter().skip(arm).step_by(arms).copied().collect();
            let Ok(channel) = joint_sequence::pilot(&pilot, &bits) else {
                break;
            };
            if channel.taps[1] <= 0. || channel.taps[1].powi(2) / channel.noise_variance < 1. {
                break;
            }
            channels.push(channel);
        }
        if channels.len() != arms {
            continue;
        }
        let payload = start + c.syncword.len() + header_bits;
        let header: Vec<_> = values[start + c.syncword.len()..payload]
            .as_chunks::<8>()
            .0
            .iter()
            .map(|chunk| {
                chunk.iter().enumerate().fold(0u8, |v, (i, x)| {
                    (v << 1) | u8::from(*x > channels[i % arms].bias)
                })
            })
            .collect();
        if !header.is_empty() && c.repetition.as_ref().unwrap().key(&header).is_err() {
            continue;
        }
        let mut g = geometry.clone();
        g.start += start as f64 / arms as f64 * g.step;
        if matches!(wave::resolved(c), Waveform::Fsk { .. }) {
            let residual = channels[0].bias * rate as f64 / TAU;
            if residual.abs() > c.residual_carrier_bound_hz {
                continue;
            }
            g.carrier += residual;
        }
        let (_, end) = wave::bounds(c, &g, width);
        if end > iq_len {
            continue;
        }
        let score = channels
            .iter()
            .map(|h| h.taps[1].powi(2) / h.noise_variance)
            .fold(f64::INFINITY, f64::min);
        found.push(Candidate {
            soft_sync: soft_sync.clone(),
            samples: values[payload..payload + c.coded_bits()].to_vec(),
            channel: channels[0].clone(),
            branch_channels: channels,
            header,
            start: g.start,
            step: g.step,
            carrier: g.carrier,
            score,
            geometry: Some(g),
            channel_cache: std::sync::OnceLock::new(),
        });
        if soft_sync.is_some() {
            search.as_mut().unwrap().admit_candidate()?;
        }
        if found.len()
            - search
                .as_ref()
                .map(|s| s.receipt.raw_candidates)
                .unwrap_or(0)
            > c.maximum_candidates * 256
        {
            return Err("raw acquisition candidate budget exceeded".into());
        }
    }
    Ok(())
}

fn afsk_symbols(
    audio: &[f64],
    rate: u32,
    offset: f64,
    step: f64,
    mark: f64,
    space: f64,
) -> Vec<f64> {
    let count = ((audio.len() as f64 - offset) / step).floor().max(0.) as usize;
    (0..count)
        .map(|i| {
            let start = (offset + i as f64 * step).ceil() as usize;
            let end = (offset + (i + 1) as f64 * step).ceil() as usize;
            let energy = |tone: f64| {
                // Least-squares sinusoid energy, avoiding the non-orthogonal
                // sin/cos bias of a raw short-window Fourier coefficient.
                let (mut cc, mut ss, mut cs, mut yc, mut ys) = (0., 0., 0., 0., 0.);
                for (j, y) in audio[start..end].iter().enumerate() {
                    let t = TAU * tone * j as f64 / rate as f64;
                    let (s, c) = t.sin_cos();
                    cc += c * c;
                    ss += s * s;
                    cs += c * s;
                    yc += y * c;
                    ys += y * s;
                }
                let determinant = cc * ss - cs * cs;
                if determinant <= 1e-12 {
                    0.
                } else {
                    ((ss * yc - cs * ys) * yc + (cc * ys - cs * yc) * ys) / determinant
                }
            };
            let a = energy(mark);
            let b = energy(space);
            (a - b) / (a + b).max(1e-12)
        })
        .collect()
}

/// Re-observe PSK at an accepted synchronization fit. IQ is already corrected
/// for carrier, drift and complex gain; payload values remain received samples.
pub(super) fn psk_at(
    iq: &[Complex64],
    rate: u32,
    c: &Config,
    g: &Geometry,
) -> Result<Candidate, String> {
    let Waveform::Psk { pulse } = wave::resolved(c) else {
        return Err("PSK re-observation requires PSK pulse".into());
    };
    let rrc = matches!(pulse, crate::psk::MatchedFilter::RootRaisedCosine { .. });
    let filtered = if rrc {
        crate::psk::filter(iq, &crate::psk::taps(&pulse, g.step))?
    } else {
        iq.to_vec()
    };
    let cumulative = prefix(&filtered);
    let arms = if wave::quadrature(c) { 2 } else { 1 };
    let header_bits = c
        .repetition
        .as_ref()
        .map(|r| r.header_bytes() * 8)
        .unwrap_or(0);
    let width = c.syncword.len() + header_bits + c.coded_bits();
    if wave::bounds(c, g, width).1 > iq.len() || !width.is_multiple_of(arms) {
        return Err("refined frame extends past input".into());
    }
    let delay = if c.modulation == "oqpsk" {
        g.step / 2.
    } else {
        0.
    };
    let sample = |t| {
        if rrc {
            at(&filtered, t + g.step * 0.5)
        } else {
            mean(&cumulative, t, g.step)
        }
    };
    let mut values = Vec::with_capacity(width);
    for i in 0..width / arms {
        let start = g.start + i as f64 * g.step;
        values.push(sample(start + if g.delayed_q { 0. } else { delay }).re);
        if arms == 2 {
            values.push(
                sample(start + if g.delayed_q { delay } else { 0. }).im
                    * if g.conjugated { -1. } else { 1. },
            );
        }
    }
    let mut channels = Vec::new();
    for arm in 0..arms {
        let pilot: Vec<_> = values[..c.syncword.len()]
            .iter()
            .skip(arm)
            .step_by(arms)
            .copied()
            .collect();
        let bits: Vec<_> = c.syncword.iter().skip(arm).step_by(arms).copied().collect();
        channels.push(joint_sequence::pilot(&pilot, &bits)?);
    }
    let payload = c.syncword.len() + header_bits;
    let header: Vec<_> = values[c.syncword.len()..payload]
        .as_chunks::<8>()
        .0
        .iter()
        .map(|chunk| {
            chunk.iter().enumerate().fold(0u8, |b, (i, x)| {
                (b << 1) | u8::from(*x > channels[i % arms].bias)
            })
        })
        .collect();
    if let Some(repeat) = &c.repetition {
        repeat.key(&header)?;
    }
    let _ = rate;
    Ok(Candidate {
        soft_sync: None,
        samples: values[payload..].to_vec(),
        channel: channels[0].clone(),
        branch_channels: channels,
        header,
        start: g.start,
        step: g.step,
        carrier: g.carrier,
        score: 0.,
        geometry: Some(g.clone()),
        channel_cache: std::sync::OnceLock::new(),
    })
}

pub(super) fn acquire(
    iq: &[Complex64],
    rate: u32,
    c: &Config,
    search: &mut Option<crate::acquisition::Search<'_>>,
) -> Result<Vec<Candidate>, String> {
    let mut found = Vec::new();
    let quadrature = wave::quadrature(c);
    let psk = wave::psk(c);
    for center in &c.carrier_centers_hz {
        let mixed: Vec<_> = iq
            .iter()
            .enumerate()
            .map(|(i, z)| z * Complex64::from_polar(1., -TAU * center * i as f64 / rate as f64))
            .collect();
        let (corrected, carrier) = if psk {
            let (values, residual, _) = crate::physical::carrier_fft(
                &mixed,
                rate as f64,
                if quadrature { 4 } else { 2 },
                Some(c.residual_carrier_bound_hz),
            )?;
            (values, center + residual)
        } else {
            (mixed, *center)
        };
        let mut audio = vec![0.; iq.len()];
        if !psk {
            for i in 1..iq.len() {
                audio[i] = (corrected[i] * corrected[i - 1].conj()).arg();
            }
        }
        // The discriminator and its prefix sums do not depend on the clock
        // hypothesis. Cache once without changing any addition order.
        let audio_prefix = (!psk).then(|| {
            prefix(
                &audio
                    .iter()
                    .map(|v| Complex64::new(*v, 0.))
                    .collect::<Vec<_>>(),
            )
        });
        for ppm in &c.clock_errors_ppm {
            let step = rate as f64 / c.symbol_rate * (1. + ppm * 1e-6);
            let model = wave::resolved(c);
            let rrc = matches!(
                model,
                Waveform::Psk {
                    pulse: crate::psk::MatchedFilter::RootRaisedCosine { .. }
                }
            );
            let filtered = if let Waveform::Psk { ref pulse } = model
                && rrc
            {
                std::borrow::Cow::Owned(crate::psk::filter(
                    &corrected,
                    &crate::psk::taps(pulse, step),
                )?)
            } else {
                std::borrow::Cow::Borrowed(corrected.as_slice())
            };
            let cumulative = if psk {
                std::borrow::Cow::Owned(prefix(&filtered))
            } else {
                std::borrow::Cow::Borrowed(audio_prefix.as_ref().unwrap().as_slice())
            };
            for phase in 0..c.phase_bins {
                let offset = phase as f64 * step / c.phase_bins as f64;
                let geometry = Geometry {
                    start: offset,
                    step,
                    carrier,
                    delayed_q: true,
                    conjugated: false,
                };
                if !psk {
                    let values = if let Waveform::Afsk {
                        mark_hz, space_hz, ..
                    } = model
                    {
                        afsk_symbols(&audio, rate, offset, step, mark_hz, space_hz)
                    } else {
                        let count = ((iq.len() as f64 - offset) / step).floor().max(0.) as usize;
                        (0..count)
                            .map(|i| mean(&cumulative, offset + i as f64 * step, step).re)
                            .collect()
                    };
                    scan(&values, c, rate, &geometry, iq.len(), &mut found, search)?;
                    continue;
                }
                let delays: &[bool] = if c.modulation == "oqpsk" {
                    &[true, false]
                } else {
                    &[true]
                };
                for &delayed_q in delays {
                    let delay = if c.modulation == "oqpsk" {
                        step / 2.
                    } else {
                        0.
                    };
                    let count =
                        ((iq.len() as f64 - offset - delay) / step).floor().max(0.) as usize;
                    let sample = |t| {
                        if rrc {
                            at(&filtered, t + step * 0.5)
                        } else {
                            mean(&cumulative, t, step)
                        }
                    };
                    for quadrant in 0..if quadrature { 4 } else { 2 } {
                        let rotation = Complex64::from_polar(
                            1.,
                            quadrant as f64
                                * if quadrature {
                                    FRAC_PI_2
                                } else {
                                    std::f64::consts::PI
                                },
                        );
                        for conjugated in
                            [false, true]
                                .into_iter()
                                .take(if quadrature { 2 } else { 1 })
                        {
                            let mut values =
                                Vec::with_capacity(count * if quadrature { 2 } else { 1 });
                            for i in 0..count {
                                let start = offset + i as f64 * step;
                                values.push(
                                    (sample(start + if delayed_q { 0. } else { delay }) * rotation)
                                        .re,
                                );
                                if quadrature {
                                    values.push(
                                        (sample(start + if delayed_q { delay } else { 0. })
                                            * rotation)
                                            .im
                                            * if conjugated { -1. } else { 1. },
                                    );
                                }
                            }
                            let mut geometry = geometry.clone();
                            geometry.delayed_q = delayed_q;
                            geometry.conjugated = conjugated;
                            scan(&values, c, rate, &geometry, iq.len(), &mut found, search)?;
                        }
                    }
                }
            }
        }
    }
    super::select_candidates(found, c, search)
}
