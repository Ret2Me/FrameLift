//! Signal-only alternatives to the legacy strongest Mth-power carrier peak.
//! No packet, CRC, reference bits or demodulator scores enter this selection.
use crate::physical;
use num_complex::Complex64;
use rustfft::FftPlanner;
use std::f64::consts::{PI, TAU};

#[derive(Clone, Copy, Debug, PartialEq)]
pub(super) struct Peak {
    pub offset_hz: f64,
    pub amplitude: f64,
    pub unsigned_bin: usize,
}

fn fft_size(samples: usize) -> usize {
    65_536usize
        .max(samples.saturating_mul(32))
        .checked_next_power_of_two()
        .unwrap_or(262_144)
        .min(262_144)
}

/// Additional conservative work for normalization, translation, one FFT and
/// up to three linear scans. Subsequent complete receiver passes are separate.
pub(super) fn work(samples: usize) -> u128 {
    let n = fft_size(samples) as u128;
    samples as u128 * 32 + 8 * n * n.ilog2() as u128 + 128 * n
}

fn rank(
    amplitude: &[f64],
    rate: f64,
    order: usize,
    maximum: f64,
    separation: f64,
    count: usize,
) -> Vec<Peak> {
    let n = amplitude.len();
    let scale = 1.0 / (n as f64 * (1.0 / rate));
    let frequency = |i: usize| {
        let bin = if i <= (n - 1) / 2 {
            i as i64
        } else {
            i as i64 - n as i64
        };
        bin as f64 * scale / order as f64
    };
    let mut chosen: Vec<Peak> = Vec::new();
    // Equivalent to amplitude-descending / unsigned-bin-ascending sorting,
    // followed by greedy separation, without sorting or storing all maxima.
    for _ in 0..count {
        let mut best: Option<Peak> = None;
        for (i, &power) in amplitude.iter().enumerate() {
            let f = frequency(i);
            if f.abs() > maximum
                || chosen.iter().any(|p| (p.offset_hz - f).abs() < separation)
                || [(i + n - 1) % n, (i + 1) % n]
                    .iter()
                    .any(|&j| frequency(j).abs() <= maximum && power < amplitude[j])
            {
                continue;
            }
            if best.is_none_or(|p| power > p.amplitude) {
                best = Some(Peak {
                    offset_hz: f,
                    amplitude: power,
                    unsigned_bin: i,
                });
            }
        }
        if let Some(peak) = best {
            chosen.push(peak);
        } else {
            break;
        }
    }
    chosen
}

/// Preserve the legacy normalization, translation, capped FFT prefix and
/// full-record Hann arithmetic. The caller has validated all waveform fields.
pub(super) fn peaks(
    iq: &[Complex64],
    rate: u32,
    center: Option<f64>,
    baud: f64,
    order: usize,
    maximum: f64,
    count: usize,
) -> Result<Vec<Peak>, String> {
    let mut values = physical::normalize_record(iq)?;
    if let Some(center) = center {
        for (i, value) in values.iter_mut().enumerate() {
            *value *= Complex64::from_polar(1.0, -TAU * center * i as f64 / rate as f64);
        }
    }
    let n = fft_size(values.len());
    let mut spectrum = vec![Complex64::new(0.0, 0.0); n];
    for (i, (value, dest)) in values.iter().zip(&mut spectrum).enumerate() {
        let t = 1.0 - values.len() as f64 + 2.0 * i as f64;
        let hann = 0.5 + 0.5 * (PI * t / (values.len() - 1) as f64).cos();
        let square = value * value;
        *dest = (if order == 2 { square } else { square * square }) * hann;
    }
    FftPlanner::<f64>::new()
        .plan_fft_forward(n)
        .process(&mut spectrum);
    let amplitude: Vec<_> = spectrum.iter().map(|v| v.re.hypot(v.im)).collect();
    if amplitude.iter().any(|v| !v.is_finite()) {
        return Err("nonfinite PSK carrier-peak spectrum".into());
    }
    Ok(rank(
        &amplitude,
        rate as f64,
        order,
        maximum,
        0.02 * baud,
        count,
    ))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn bounded_neighbors_and_unsigned_ties_preserve_edge_peak() {
        let mut a = vec![0.0; 64];
        a[3] = 10.0;
        a[4] = 20.0; // Outside the declared ±3Hz search, must not hide bin3.
        a[61] = 10.0;
        a[0] = 5.0;
        let found = rank(&a, 256.0, 4, 3.0, 2.0, 3);
        assert_eq!(
            found.iter().map(|p| p.unsigned_bin).collect::<Vec<_>>(),
            [3, 61, 0]
        );
    }

    #[test]
    fn unavailable_separated_peaks_are_not_invented() {
        let found = rank(&vec![1.0; 64], 256.0, 4, 0.1, 2.0, 3);
        assert_eq!(found.len(), 1);
        assert_eq!(found[0].offset_hz, 0.0);
    }

    #[test]
    fn strongest_matches_legacy_on_two_orders_and_record_lengths() {
        for count in [257usize, 6000, 270000] {
            let iq: Vec<_> = (0..count)
                .map(|i| {
                    let a = TAU * 317.0 * i as f64 / 48000.0;
                    Complex64::from_polar(1.0, a)
                        + Complex64::new(((i * 73 % 101) as f64 - 50.0) * 0.005, 0.07)
                })
                .collect();
            for order in [2, 4] {
                let old = physical::carrier_fft(
                    &physical::normalize_record(&iq).unwrap(),
                    48000.0,
                    order,
                    Some(1000.0),
                )
                .unwrap()
                .1;
                let found = peaks(&iq, 48000, None, 6000.0, order, 1000.0, 3).unwrap();
                assert_eq!(found[0].offset_hz.to_bits(), old.to_bits());
            }
        }
    }
}
