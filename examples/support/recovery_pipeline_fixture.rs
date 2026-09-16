//! Independent fixed-link transmitter. Never supplies payload truth to a receiver.
use num_complex::Complex64;
use std::f64::consts::{PI, TAU};
use telemetry_yield_rs::{
    advanced_iq::{Config, RecoveryOptions},
    advanced_waveform::Waveform,
    fec::{ReedSolomonConfig, SymbolBasis},
    psk::MatchedFilter,
    recovery_code::{CodeProfile, ConvolutionalConfig, ConvolutionalTermination},
};
#[path = "multimode_fixture.rs"]
#[allow(dead_code)]
pub mod waveform;
pub use waveform::{LENGTH, MODES, RATE, base};

pub fn matrix() -> Vec<(&'static str, &'static str)> {
    let mut cases: Vec<_> = MODES
        .iter()
        .flat_map(|mode| [(*mode, "uncoded"), (*mode, "conv")])
        .collect();
    cases.extend(
        ["bpsk", "gmsk"]
            .into_iter()
            .flat_map(|mode| ["rs", "conv_rs", "ldpc"].map(|code| (mode, code))),
    );
    cases
}

pub fn config(mode: &str, family: &str, workers: usize, coherent: bool) -> Config {
    let mut c = waveform::config(mode);
    let convolutional = ConvolutionalConfig {
        generators: [79, 109],
        invert: [false, false],
        initial_state: 0,
        termination: ConvolutionalTermination::ZeroTail,
    };
    let reed_solomon = ReedSolomonConfig {
        field_polynomial: 0x11d,
        first_root: 0,
        primitive_step: 1,
        parity_symbols: 16,
        shortening: 231,
        interleaving: 1,
        basis: SymbolBasis::Conventional,
    };
    c.code = match family {
        "uncoded" => CodeProfile::Uncoded { frame_bytes: 8 },
        "conv" => CodeProfile::ConvolutionalK7 {
            frame_bytes: 8,
            config: convolutional,
        },
        "rs" => CodeProfile::ReedSolomon {
            config: reed_solomon,
        },
        "conv_rs" => CodeProfile::ConvolutionalReedSolomon {
            convolutional,
            reed_solomon,
            interstage_randomizer: telemetry_yield_rs::coded::Randomizer::None,
        },
        "ldpc" => c.code,
        _ => panic!("unrecognized engineering fixture family"),
    };
    c.cancellation = None;
    c.joint = None;
    c.turbo_iterations = 3;
    c.maximum_work = 5_000_000_000;
    c.maximum_candidates = 16;
    c.recovery = Some(RecoveryOptions {
        workers,
        coherent_cpm: coherent,
        tracking: None,
    });
    c
}

fn bytes_to_bits(bytes: &[u8]) -> Vec<u8> {
    bytes
        .iter()
        .flat_map(|b| (0..8).rev().map(move |i| (b >> i) & 1))
        .collect()
}

fn convolutional(bits: &[u8], c: &ConvolutionalConfig) -> Vec<u8> {
    let mut delay: Vec<u8> = (0..6).map(|i| (c.initial_state >> i) & 1).collect();
    let tail = if c.termination == ConvolutionalTermination::ZeroTail {
        6
    } else {
        0
    };
    let mut out = Vec::new();
    for bit in bits.iter().copied().chain(std::iter::repeat_n(0, tail)) {
        delay.insert(0, bit);
        for j in 0..2 {
            out.push((0..7).fold(u8::from(c.invert[j]), |p, k| {
                p ^ (delay[k] & ((c.generators[j] >> k) & 1))
            }));
        }
        delay.pop();
    }
    out
}

/// Bitwise GF multiply and feedback shift register, independent of production
/// RS tables/encoder. This engineering fixture uses a conventional single lane.
fn reed_solomon(bytes: &[u8], c: &ReedSolomonConfig) -> Vec<u8> {
    fn multiply(mut a: u16, mut b: u8, polynomial: u16) -> u8 {
        let mut out = 0_u16;
        for _ in 0..8 {
            if b & 1 != 0 {
                out ^= a;
            }
            b >>= 1;
            a <<= 1;
            if a & 0x100 != 0 {
                a ^= polynomial;
            }
        }
        out as u8
    }
    assert_eq!(c.basis, SymbolBasis::Conventional);
    assert_eq!(c.interleaving, 1);
    let mut coefficients = vec![1];
    for j in 0..c.parity_symbols {
        let mut root = 1;
        for _ in 0..((c.first_root + j) * c.primitive_step) % 255 {
            root = multiply(root as u16, 2, c.field_polynomial);
        }
        let mut next = vec![0; coefficients.len() + 1];
        for (i, coefficient) in coefficients.iter().copied().enumerate() {
            next[i] ^= coefficient;
            next[i + 1] ^= multiply(coefficient as u16, root, c.field_polynomial);
        }
        coefficients = next;
    }
    let mut shift = vec![0; c.parity_symbols];
    for byte in bytes {
        let feedback = byte ^ shift[0];
        shift.rotate_left(1);
        *shift.last_mut().unwrap() = 0;
        for (i, x) in shift.iter_mut().enumerate() {
            *x ^= multiply(feedback as u16, coefficients[i + 1], c.field_polynomial);
        }
    }
    [bytes, &shift].concat()
}

pub fn encoded(c: &Config, frame: &[u8]) -> Vec<u8> {
    match &c.code {
        CodeProfile::Ldpc { config } => {
            assert_eq!(config.codeword_bits, 128);
            assert_eq!(frame.len(), 8);
            base::encode(frame)
        }
        CodeProfile::Uncoded { .. } => bytes_to_bits(frame),
        CodeProfile::ConvolutionalK7 { config, .. } => convolutional(&bytes_to_bits(frame), config),
        CodeProfile::ReedSolomon { config } => bytes_to_bits(&reed_solomon(frame, config)),
        CodeProfile::ConvolutionalReedSolomon {
            convolutional: conv,
            reed_solomon: rs,
            interstage_randomizer,
        } => {
            assert!(matches!(
                interstage_randomizer,
                telemetry_yield_rs::coded::Randomizer::None
            ));
            convolutional(&bytes_to_bits(&reed_solomon(frame, rs)), conv)
        }
    }
}

fn rrc(t: f64, a: f64) -> f64 {
    if t.abs() < 1e-10 {
        return 1. + a * (4. / PI - 1.);
    }
    if (t.abs() - 1. / (4. * a)).abs() < 1e-10 {
        return a / 2f64.sqrt()
            * ((1. + 2. / PI) * (PI / (4. * a)).sin() + (1. - 2. / PI) * (PI / (4. * a)).cos());
    }
    ((PI * (1. - a) * t).sin() / t + 4. * a * (PI * (1. + a) * t).cos())
        / (PI * (1. - 16. * a * a * t * t))
}

/// Direct engineering waveform, not production remodulation. This function
/// accepts independently encoded bits and never invokes a receive algorithm.
pub fn add(iq: &mut [Complex64], c: &Config, frame: &[u8], carrier: f64) {
    assert!(c.wire_to_code.is_empty());
    assert!(matches!(
        c.randomizer,
        telemetry_yield_rs::coded::Randomizer::None
    ));
    let mut bits = c.syncword.clone();
    bits.extend(encoded(c, frame));
    let sps = (RATE as f64 / c.symbol_rate) as usize;
    let levels: Vec<_> = bits.iter().map(|b| 2. * f64::from(*b) - 1.).collect();
    let start = 256;
    match c.waveform.as_ref().unwrap() {
        Waveform::Psk { pulse } => {
            let stride = if matches!(c.modulation.as_str(), "qpsk" | "oqpsk") {
                2
            } else {
                1
            };
            let delay = if c.modulation == "oqpsk" { sps / 2 } else { 0 };
            let half = match pulse {
                MatchedFilter::Rectangular => 0,
                MatchedFilter::RootRaisedCosine { span_symbols, .. } => span_symbols * sps / 2,
            };
            let end = start + bits.len() / stride * sps + delay + half;
            for (i, dest) in iq.iter_mut().enumerate().take(end).skip(start - half) {
                let arm = |branch: usize| {
                    let relative =
                        i as isize - start as isize - if branch == 1 { delay as isize } else { 0 };
                    match pulse {
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
                                .map(|k| levels[k * stride + branch] * rrc(t - k as f64, *rolloff))
                                .sum()
                        }
                    }
                };
                let z = Complex64::new(arm(0), if stride == 2 { arm(1) } else { 0. });
                *dest +=
                    z * Complex64::from_polar(1., 0.37 + TAU * carrier * i as f64 / RATE as f64);
            }
        }
        Waveform::Fsk {
            deviation_hz,
            gaussian_bt,
        } => {
            let levels: Vec<_> = levels
                .iter()
                .flat_map(|v| std::iter::repeat_n(*v, sps))
                .collect();
            let frequency = if let Some(bt) = gaussian_bt {
                let sigma = 2f64.ln().sqrt() / (TAU * bt) * sps as f64;
                let taps: Vec<_> = (-(2 * sps as isize)..=2 * sps as isize)
                    .map(|k| (-0.5 * (k as f64 / sigma).powi(2)).exp())
                    .collect();
                let norm = taps.iter().sum::<f64>();
                (0..levels.len())
                    .map(|i| {
                        taps.iter()
                            .enumerate()
                            .filter_map(|(j, h)| {
                                let k = i as isize + j as isize - 2 * sps as isize;
                                levels.get(usize::try_from(k).ok()?).map(|v| h * v / norm)
                            })
                            .sum()
                    })
                    .collect()
            } else {
                levels
            };
            let mut phase = 0.37;
            for (j, f) in frequency.iter().enumerate() {
                phase += TAU * deviation_hz * f / RATE as f64;
                let i = start + j;
                iq[i] += Complex64::from_polar(1., phase + TAU * carrier * i as f64 / RATE as f64);
            }
        }
        Waveform::Afsk {
            mark_hz,
            space_hz,
            fm_deviation_hz,
        } => {
            let mut tone = 0.81;
            let mut phase = 0.37;
            for (k, b) in bits.iter().enumerate() {
                let f = if *b == 1 { mark_hz } else { space_hz };
                for j in 0..sps {
                    tone += TAU * f / RATE as f64;
                    phase += TAU * fm_deviation_hz * tone.sin() / RATE as f64;
                    let i = start + k * sps + j;
                    iq[i] +=
                        Complex64::from_polar(1., phase + TAU * carrier * i as f64 / RATE as f64);
                }
            }
        }
    }
}

pub fn is_cpm(mode: &str) -> bool {
    matches!(mode, "fsk" | "gfsk" | "gmsk")
}
