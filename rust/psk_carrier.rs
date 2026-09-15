//! Bounded, signal-only feed-forward BPSK frequency-drift estimation.
//! Local square-law spectral peaks are fitted before any bit slicing/framing.
//! This is a standard weighted spectral regression, not a novelty claim.
use num_complex::Complex64;
use rustfft::FftPlanner;
use std::f64::consts::TAU;

const SEGMENT_SYMBOLS: f64 = 128.0;
const MAX_SEGMENTS: usize = 96;

fn geometry(samples: usize, sps: f64) -> (usize, usize, usize) {
    let length = ((SEGMENT_SYMBOLS * sps).ceil() as usize).min(samples);
    if length < 8 {
        return (length, 0, 0);
    }
    let count = ((samples - length) / (length / 2).max(1) + 1).min(MAX_SEGMENTS);
    (length, length.next_power_of_two(), count)
}

pub(super) fn work(samples: usize, sps: f64) -> u128 {
    let (length, fft, count) = geometry(samples, sps);
    if count < 3 {
        return 0;
    }
    count as u128 * (8 * fft as u128 * fft.ilog2() as u128 + 16 * length as u128)
        + 16 * samples as u128
}

#[derive(Debug)]
pub(super) struct LinearFit {
    pub frequency_at_start_hz: f64,
    pub drift_hz_per_s: f64,
}

/// None means the signal does not support an in-bounds fit, not a decode error.
/// The caller retains its complete constant-CFO bank irrespective of this fit.
pub(super) fn fit(
    iq: &[Complex64],
    rate: f64,
    baud: f64,
    maximum_hz: f64,
    maximum_drift_hz_per_s: f64,
) -> Option<LinearFit> {
    let (length, fft_size, count) = geometry(iq.len(), rate / baud);
    if count < 3 {
        return None;
    }
    let mut planner = FftPlanner::<f64>::new();
    let fft = planner.plan_fft_forward(fft_size);
    let mut spectrum = vec![Complex64::new(0.0, 0.0); fft_size];
    let mut scratch = vec![Complex64::new(0.0, 0.0); fft.get_inplace_scratch_len()];
    let hann: Vec<_> = (0..length)
        .map(|i| 0.5 - 0.5 * (TAU * i as f64 / (length - 1) as f64).cos())
        .collect();
    let mut points = Vec::with_capacity(count);
    for index in 0..count {
        // Fixed positions cover the full record, independently of received data.
        let start = index * (iq.len() - length) / (count - 1);
        spectrum.fill(Complex64::new(0.0, 0.0));
        let mut power = 0.0;
        for (i, (&x, &h)) in iq[start..start + length].iter().zip(&hann).enumerate() {
            spectrum[i] = x * x * h;
            power += x.norm_sqr() * h;
        }
        if !power.is_finite() || power <= 1e-30 {
            continue;
        }
        fft.process_with_scratch(&mut spectrum, &mut scratch);
        let mut best_power = 0.0;
        let mut best_hz = 0.0;
        for (i, x) in spectrum.iter().enumerate() {
            let bin = if i < fft_size / 2 {
                i as f64
            } else {
                i as f64 - fft_size as f64
            };
            let hz = bin * rate / (2.0 * fft_size as f64);
            if hz.abs() <= maximum_hz && x.norm_sqr() > best_power {
                best_power = x.norm_sqr();
                best_hz = hz;
            }
        }
        let coherence = (best_power.sqrt() / power).clamp(0.0, 1.0);
        let weight = coherence.powi(4);
        let time = (start as f64 + (length - 1) as f64 * 0.5) / rate;
        points.push((time, best_hz, weight));
    }
    let sum_w = points.iter().map(|p| p.2).sum::<f64>();
    if points.len() < 3 || !sum_w.is_finite() || sum_w <= 1e-20 {
        return None;
    }
    let mean_t = points.iter().map(|p| p.0 * p.2).sum::<f64>() / sum_w;
    let mean_f = points.iter().map(|p| p.1 * p.2).sum::<f64>() / sum_w;
    let variance = points
        .iter()
        .map(|p| p.2 * (p.0 - mean_t).powi(2))
        .sum::<f64>();
    let covariance = points
        .iter()
        .map(|p| p.2 * (p.0 - mean_t) * (p.1 - mean_f))
        .sum::<f64>();
    if !variance.is_finite() || variance <= 1e-20 {
        return None;
    }
    let slope = covariance / variance;
    let intercept = mean_f - slope * mean_t;
    let final_hz = intercept + slope * (iq.len() - 1) as f64 / rate;
    if !slope.is_finite()
        || slope.abs() > maximum_drift_hz_per_s
        || !intercept.is_finite()
        || intercept.abs().max(final_hz.abs()) > maximum_hz
    {
        return None;
    }
    Some(LinearFit {
        frequency_at_start_hz: intercept,
        drift_hz_per_s: slope,
    })
}

pub(super) fn correct(iq: &[Complex64], rate: f64, model: &LinearFit) -> Vec<Complex64> {
    iq.iter()
        .enumerate()
        .map(|(i, x)| {
            let t = i as f64 / rate;
            x * Complex64::from_polar(
                1.0,
                -TAU * (model.frequency_at_start_hz * t + 0.5 * model.drift_hz_per_s * t * t),
            )
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn weighted_drift_fit_ignores_noise_guards_without_packet_knowledge() {
        for drift in [-50.0, 50.0] {
            let rate = 4800.0;
            let mut state = 0x449f52b663791355u64;
            let mut random = || {
                state = state.wrapping_mul(6364136223846793005).wrapping_add(1);
                ((state >> 32) as f64 / u32::MAX as f64) * 2.0 - 1.0
            };
            let iq: Vec<_> = (0..16000)
                .map(|i| {
                    let t = i as f64 / rate;
                    let noise = Complex64::new(random(), random());
                    if !(3000..13000).contains(&i) {
                        return noise;
                    }
                    let sign = if (i / 4 * 73 + i / 4 / 7) % 11 < 5 {
                        1.0
                    } else {
                        -1.0
                    };
                    sign * Complex64::from_polar(1.0, 0.47 + TAU * (25.0 * t + 0.5 * drift * t * t))
                        + noise * 0.05
                })
                .collect();
            let result = fit(&iq, rate, 1200.0, 240.0, 100.0).unwrap();
            assert!(
                (result.frequency_at_start_hz - 25.0).abs() < 5.0,
                "{result:?}"
            );
            assert!((result.drift_hz_per_s - drift).abs() < 3.0, "{result:?}");
            assert!(fit(&iq, rate, 1200.0, 240.0, 1.0).is_none());
            let corrected = correct(&iq, rate, &result);
            for (x, y) in iq.iter().zip(corrected) {
                assert!((x.norm_sqr() - y.norm_sqr()).abs() < 1e-12);
            }
        }
    }

    #[test]
    fn carrier_fit_geometry_and_empty_signal_are_bounded() {
        for samples in [0, 7, 128, 1024, 1_000_000, 8_000_000] {
            for sps in [2.0, 2.5, 8.0, 128.0] {
                let (len, n, count) = geometry(samples, sps);
                assert!(len <= samples && len <= 16384 && n <= 16384 && count <= 96);
                if count >= 3 {
                    assert!(work(samples, sps) > 0);
                }
            }
        }
        assert!(
            fit(
                &vec![Complex64::new(0.0, 0.0); 4096],
                4800.0,
                1200.0,
                240.0,
                100.0
            )
            .is_none()
        );
        assert!(
            fit(
                &vec![Complex64::new(1.0, 0.0); 511],
                4800.0,
                1200.0,
                240.0,
                100.0
            )
            .is_none()
        );
    }

    #[test]
    fn carrier_fit_rejects_extrapolated_frequency_outside_search_band() {
        for (start, drift) in [
            (100.0, 100.0),
            (-100.0, -100.0),
            (300.0, -100.0),
            (-300.0, 100.0),
        ] {
            let iq: Vec<_> = (0..10000)
                .map(|i| {
                    let t = i as f64 / 4800.0;
                    Complex64::from_polar(1.0, 0.3 + TAU * (start * t + 0.5 * drift * t * t))
                })
                .collect();
            assert!(fit(&iq, 4800.0, 1200.0, 600.0, 200.0).is_some());
            assert!(
                fit(&iq, 4800.0, 1200.0, 240.0, 200.0).is_none(),
                "start={start} drift={drift}"
            );
        }
    }
}
