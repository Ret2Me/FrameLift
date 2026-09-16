//! Independent engineering transmitter; not linked into production receivers.
//! These explicit test links are not claimed to describe an orbiting mission.
use num_complex::Complex64;
use std::f64::consts::{PI, TAU};
use telemetry_yield_rs::{
    advanced_iq::{Config, RepeatConfig},
    advanced_waveform::Waveform,
    interference,
    psk::MatchedFilter,
};
#[path = "advanced_iq_fixture.rs"]
#[allow(dead_code)] // Shared transmitter also exposes the legacy BPSK fixture.
pub mod base;

pub const MODES: &[&str] = &[
    "bpsk",
    "qpsk",
    "oqpsk",
    "bpsk_rrc",
    "qpsk_rrc",
    "oqpsk_rrc",
    "fsk",
    "gfsk",
    "gmsk",
    "afsk",
];
pub const RATE: u32 = 24_000;
pub const LENGTH: usize = 8192;

pub fn config(mode: &str) -> Config {
    let mut c = base::config();
    c.modulation = mode.trim_end_matches("_rrc").into();
    c.symbol_rate = if mode == "afsk" { 1200. } else { 3000. };
    c.phase_bins = 8;
    c.waveform = Some(match c.modulation.as_str() {
        "fsk" | "gfsk" | "gmsk" => Waveform::Fsk {
            deviation_hz: if mode == "gmsk" { 750. } else { 1100. },
            gaussian_bt: if mode == "fsk" { None } else { Some(0.5) },
        },
        "afsk" => Waveform::Afsk {
            mark_hz: 1200.,
            space_hz: 2200.,
            fm_deviation_hz: 3000.,
        },
        _ => Waveform::Psk {
            pulse: if mode.ends_with("_rrc") {
                MatchedFilter::RootRaisedCosine {
                    rolloff: 0.35,
                    span_symbols: 8,
                }
            } else {
                MatchedFilter::Rectangular
            },
        },
    });
    c.cancellation = Some(interference::Config {
        rounds: 2,
        minimum_holdout_reduction: 0.2,
    });
    c
}

pub fn repetition(c: &mut Config) {
    c.repetition = Some(RepeatConfig {
        combine: true,
        key_bytes: 4,
        maximum_copies: 4,
        minimum_correlation: 0.1,
        maximum_gap_symbols: 10000,
        mission_header: None,
    });
}

pub fn bits(c: &Config, frame: &[u8], key: Option<u32>) -> Vec<u8> {
    base::symbols(c, frame, key)
        .iter()
        .map(|v| u8::from(*v > 0.))
        .collect()
}

fn pulse(t: f64, alpha: f64) -> f64 {
    if t.abs() < 1e-10 {
        return 1. + alpha * (4. / PI - 1.);
    }
    if (t.abs() - 1. / (4. * alpha)).abs() < 1e-10 {
        let u = PI / (4. * alpha);
        return alpha / 2f64.sqrt() * ((1. + 2. / PI) * u.sin() + (1. - 2. / PI) * u.cos());
    }
    ((PI * (1. - alpha) * t).sin() / t + 4. * alpha * (PI * (1. + alpha) * t).cos())
        / (PI * (1. - 16. * alpha * alpha * t * t))
}

/// Direct sampled transmitter, with an independent optional distortion mask.
/// `erasure_copy` retains common data plus one complementary quarter. It is a
/// constructed symbol-erasure stress case, not a real propagation simulator.
#[derive(Clone, Copy)]
pub struct Tx {
    pub start: usize,
    pub amplitude: f64,
    pub carrier: f64,
    pub phase: f64,
    pub delayed_q: bool,
    pub conjugated: bool,
    pub erasure_copy: Option<usize>,
}
impl Default for Tx {
    fn default() -> Self {
        Self {
            start: 256,
            amplitude: 1.,
            carrier: 0.,
            phase: 0.37,
            delayed_q: true,
            conjugated: false,
            erasure_copy: None,
        }
    }
}

pub fn add(iq: &mut [Complex64], c: &Config, frame: &[u8], key: Option<u32>, tx: Tx) {
    let bits = bits(c, frame, key);
    let payload = c.syncword.len() + if key.is_some() { 64 } else { 0 };
    let levels: Vec<_> = bits
        .iter()
        .enumerate()
        .map(|(i, b)| {
            if i >= payload + 16
                && tx
                    .erasure_copy
                    .is_some_and(|copy| (i - payload - 16) % 4 != copy)
            {
                0.
            } else {
                2. * f64::from(*b) - 1.
            }
        })
        .collect();
    let sps = (RATE as f64 / c.symbol_rate) as usize;
    let quadrature = matches!(c.modulation.as_str(), "qpsk" | "oqpsk");
    let stride = if quadrature { 2 } else { 1 };
    let half_delay = if c.modulation == "oqpsk" { sps / 2 } else { 0 };
    match c.waveform.as_ref().unwrap() {
        Waveform::Psk { pulse: shape } => {
            let half = match shape {
                MatchedFilter::Rectangular => 0,
                MatchedFilter::RootRaisedCosine { span_symbols, .. } => span_symbols * sps / 2,
            };
            let end = tx.start + bits.len() / stride * sps + half_delay + half;
            for (i, dest) in iq.iter_mut().enumerate().take(end).skip(tx.start - half) {
                let arm = |branch: usize, delay: usize| {
                    let relative = i as isize - tx.start as isize - delay as isize;
                    match shape {
                        MatchedFilter::Rectangular => {
                            if relative < 0 || relative as usize / sps >= bits.len() / stride {
                                0.
                            } else {
                                levels[relative as usize / sps * stride + branch]
                            }
                        }
                        MatchedFilter::RootRaisedCosine {
                            rolloff,
                            span_symbols,
                        } => {
                            let t = relative as f64 / sps as f64 - 0.5;
                            (0..bits.len() / stride)
                                .filter(|k| (t - *k as f64).abs() <= *span_symbols as f64 / 2.)
                                .map(|k| {
                                    levels[k * stride + branch] * pulse(t - k as f64, *rolloff)
                                })
                                .sum()
                        }
                    }
                };
                let re = arm(0, if tx.delayed_q { 0 } else { half_delay });
                let im = if quadrature {
                    arm(1, if tx.delayed_q { half_delay } else { 0 })
                } else {
                    0.
                };
                let z = Complex64::new(re, if tx.conjugated { -im } else { im });
                *dest += z * Complex64::from_polar(
                    tx.amplitude,
                    tx.phase + TAU * tx.carrier * i as f64 / RATE as f64,
                );
            }
        }
        Waveform::Fsk {
            deviation_hz,
            gaussian_bt,
        } => {
            let mut frequency: Vec<_> = levels
                .iter()
                .flat_map(|v| std::iter::repeat_n(*v, sps))
                .collect();
            if let Some(bt) = gaussian_bt {
                let sigma = 2f64.ln().sqrt() / (TAU * bt) * sps as f64;
                let h: Vec<_> = (-(2 * sps as isize)..=2 * sps as isize)
                    .map(|k| (-0.5 * (k as f64 / sigma).powi(2)).exp())
                    .collect();
                let norm = h.iter().sum::<f64>();
                frequency = (0..frequency.len())
                    .map(|i| {
                        h.iter()
                            .enumerate()
                            .filter_map(|(j, h)| {
                                let k = i as isize + j as isize - 2 * sps as isize;
                                frequency
                                    .get(usize::try_from(k).ok()?)
                                    .map(|v| h * v / norm)
                            })
                            .sum()
                    })
                    .collect();
            }
            let mut phase = tx.phase;
            for (j, f) in frequency.iter().enumerate() {
                phase += TAU * deviation_hz * f / RATE as f64;
                let i = tx.start + j;
                iq[i] += Complex64::from_polar(
                    tx.amplitude,
                    phase + TAU * tx.carrier * i as f64 / RATE as f64,
                );
            }
        }
        Waveform::Afsk {
            mark_hz,
            space_hz,
            fm_deviation_hz,
        } => {
            let mut tone_phase = 0.81;
            let mut rf_phase = tx.phase;
            for (k, bit) in bits.iter().enumerate() {
                let f = if *bit == 1 { *mark_hz } else { *space_hz };
                for j in 0..sps {
                    tone_phase += TAU * f / RATE as f64;
                    rf_phase += TAU * fm_deviation_hz * tone_phase.sin() / RATE as f64;
                    let i = tx.start + k * sps + j;
                    let amp = if levels[k] == 0. { 0. } else { tx.amplitude };
                    iq[i] += Complex64::from_polar(
                        amp,
                        rf_phase + TAU * tx.carrier * i as f64 / RATE as f64,
                    );
                }
            }
        }
    }
}
