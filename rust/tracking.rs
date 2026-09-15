//! Experimental real post-FM FSK frontend and continuous Gardner timing.
//! No payload, CRC, mission identity, or reference timing participates in DSP.
//! This is not a bit-exact implementation of GNU Radio's PFB synchronizer.

use crate::dsp::{Frontend, GUARD_SYMBOLS, MAX_PCM_WINDOW_SAMPLES};
use serde::{Deserialize, Serialize};

/// Alternative to the legacy 115-tap FIR / 512-symbol DC frontend.
/// Square-pulse filter, four-boxcar long-form DC rejection (32 symbols),
/// and causal RMS AGC (50-symbol time constant). Full sample rate is retained.
/// Startup is removed; sample indices returned by timing refer to this output,
/// not directly to absolute RF or PCM timestamps.
pub fn boxcar_frontend(pcm: &[f64], sample_rate: u32, baud: f64) -> Result<Frontend, String> {
    let sps = f64::from(sample_rate) / baud;
    if sample_rate == 0
        || !baud.is_finite()
        || baud <= 0.0
        || !sps.is_finite()
        || !(2.0..=128.0).contains(&sps)
        || pcm.len() > MAX_PCM_WINDOW_SAMPLES
        || pcm.iter().any(|x| !x.is_finite())
    {
        return Err(
            "boxcar frontend requires finite PCM, bounded length and 2..128 samples/symbol".into(),
        );
    }
    let peak = pcm.iter().fold(0.0_f64, |a, x| a.max(x.abs()));
    if peak == 0.0 {
        return Ok(Frontend {
            samples: vec![],
            samples_per_symbol: sps,
        });
    }
    // Scaling avoids overflow without changing the subsequent normalized signal.
    let scaled: Vec<_> = pcm.iter().map(|x| x / peak).collect();
    let pulse_length = sps.floor() as usize;
    let filtered = moving_average(&scaled, pulse_length);
    let dc_length = (32.0 * sps).ceil() as usize;
    let delay = 2 * (dc_length - 1);
    let mut trend = filtered.clone();
    for _ in 0..4 {
        trend = moving_average(&trend, dc_length);
    }
    let startup = 4 * (dc_length - 1) + pulse_length - 1;
    let alpha = 1.0 / (50.0 * sps);
    let mut power = 0.0;
    let mut output = Vec::with_capacity(pcm.len().saturating_sub(startup));
    for (i, average) in trend.into_iter().enumerate() {
        let delayed = if i >= delay { filtered[i - delay] } else { 0.0 };
        let x = delayed - average;
        power += alpha * (x * x - power);
        let normalized = x / power.max(1e-24).sqrt();
        if i >= startup {
            output.push(normalized);
        }
    }
    Ok(Frontend {
        samples: output,
        samples_per_symbol: sps,
    })
}

/// Causal, zero-padded boxcar with exactly `width` samples in its denominator.
fn moving_average(values: &[f64], width: usize) -> Vec<f64> {
    let mut sum = 0.0;
    values
        .iter()
        .enumerate()
        .map(|(i, x)| {
            sum += x;
            if i >= width {
                sum -= values[i - width];
            }
            sum / width as f64
        })
        .collect()
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct GardnerConfig {
    /// Normalized loop bandwidth; zero is a diagnostic fixed-clock control.
    pub bandwidth: f64,
    pub damping: f64,
    pub ted_gain: f64,
    pub max_rate_error_ppm: f64,
    pub initial_phase_symbols: f64,
}

impl Default for GardnerConfig {
    fn default() -> Self {
        Self {
            bandwidth: 0.02,
            damping: 1.0,
            ted_gain: 1.47,
            max_rate_error_ppm: 4000.0,
            initial_phase_symbols: 0.5,
        }
    }
}

#[derive(Debug)]
pub struct TrackedSymbols {
    pub soft: Vec<f64>,
    pub final_rate_error_ppm: f64,
    pub rate_limit_hits: usize,
}

/// Second-order, non-data-aided symbol clock. Linear interpolation is an
/// explicit experimental choice; GNU Radio uses a different interpolator.
/// e = midpoint * (previous_symbol - current_symbol). Feedback is bounded;
/// no output is accepted/rejected based on the resulting frame contents.
pub fn gardner(front: &Frontend, config: &GardnerConfig) -> Result<TrackedSymbols, String> {
    let nominal = front.samples_per_symbol;
    let values = &front.samples;
    if !nominal.is_finite()
        || !(2.0..=128.0).contains(&nominal)
        || values.len() > MAX_PCM_WINDOW_SAMPLES
        || values.iter().any(|x| !x.is_finite())
        || !config.bandwidth.is_finite()
        || !(0.0..=0.1).contains(&config.bandwidth)
        || !config.damping.is_finite()
        || !(0.1..=4.0).contains(&config.damping)
        || !config.ted_gain.is_finite()
        || !(0.1..=10.0).contains(&config.ted_gain)
        || !config.max_rate_error_ppm.is_finite()
        || !(0.0..=20_000.0).contains(&config.max_rate_error_ppm)
        || !config.initial_phase_symbols.is_finite()
        || !(0.0..1.0).contains(&config.initial_phase_symbols)
    {
        return Err("invalid bounded Gardner timing configuration or waveform".into());
    }
    let mut result = TrackedSymbols {
        soft: vec![],
        final_rate_error_ppm: 0.0,
        rate_limit_hits: 0,
    };
    let mut at = (GUARD_SYMBOLS as f64 + config.initial_phase_symbols) * nominal;
    if at + nominal >= values.len() as f64 {
        return Ok(result);
    }
    let denominator = 1.0 + 2.0 * config.damping * config.bandwidth + config.bandwidth.powi(2);
    let proportional = 4.0 * config.damping * config.bandwidth / denominator / config.ted_gain;
    let integral = 4.0 * config.bandwidth.powi(2) / denominator / config.ted_gain;
    let limit = config.max_rate_error_ppm * 1e-6;
    let mut rate = 0.0_f64;
    let mut previous_at = at - nominal;
    let mut previous = interpolate(values, previous_at);
    while at < (values.len() - 1) as f64 {
        let current = interpolate(values, at);
        let middle = interpolate(values, 0.5 * (previous_at + at));
        // Clip detector operands, not the soft output, to bound numeric error
        // and prevent a transient amplitude spike from destabilizing the loop.
        let error = (middle.clamp(-4.0, 4.0)
            * (previous.clamp(-4.0, 4.0) - current.clamp(-4.0, 4.0)))
        .clamp(-1.0, 1.0);
        let update = rate + integral * error;
        rate = update.clamp(-limit, limit);
        result.rate_limit_hits += usize::from(update != rate);
        result.soft.push(current);
        previous_at = at;
        previous = current;
        let correction = (proportional * error).clamp(-0.2, 0.2);
        at += nominal * (1.0 + rate + correction);
    }
    result.final_rate_error_ppm = rate * 1e6;
    Ok(result)
}

fn interpolate(values: &[f64], at: f64) -> f64 {
    let left = at.floor() as usize;
    let fraction = at - left as f64;
    // Weighted sum avoids overflow for opposite-sign finite extremes.
    values[left] * (1.0 - fraction) + values[left + 1] * fraction
}

#[cfg(test)]
mod tests {
    use super::*;

    fn waveform(period: f64, count: usize) -> (Frontend, Vec<bool>) {
        let mut state = 0x26a5_b9c3_u32;
        let bits: Vec<_> = (0..count)
            .map(|_| {
                state ^= state << 13;
                state ^= state >> 17;
                state ^= state << 5;
                state & 1 != 0
            })
            .collect();
        let samples = (0..((count - 2) as f64 * period) as usize)
            .map(|i| {
                let time = (i as f64 / period - 0.5).max(0.0);
                let index = time.floor() as usize;
                let frac = time - index as f64;
                let blend = 0.5 - 0.5 * (std::f64::consts::PI * frac).cos();
                let a = if bits[index] { 1.0 } else { -1.0 };
                let b = if bits[index + 1] { 1.0 } else { -1.0 };
                a * (1.0 - blend) + b * blend
            })
            .collect();
        (
            Frontend {
                samples,
                samples_per_symbol: 5.0,
            },
            bits,
        )
    }

    fn bit_errors(soft: &[f64], truth: &[bool]) -> usize {
        soft.iter()
            .zip(&truth[GUARD_SYMBOLS..])
            .skip(500)
            .filter(|(value, expected)| (**value >= 0.0) != **expected)
            .count()
    }

    #[test]
    fn gardner_tracks_positive_and_negative_clock_error() {
        for period in [4.992, 5.008] {
            let (front, truth) = waveform(period, 12_000);
            let tracked = gardner(&front, &GardnerConfig::default()).unwrap();
            let fixed = gardner(
                &front,
                &GardnerConfig {
                    bandwidth: 0.0,
                    ..Default::default()
                },
            )
            .unwrap();
            let errors = bit_errors(&tracked.soft, &truth);
            assert!(
                errors < 10,
                "period={period}, tracked errors={errors}, rate={}",
                tracked.final_rate_error_ppm
            );
            assert!(bit_errors(&fixed.soft, &truth) > 1000);
            assert!(tracked.final_rate_error_ppm.abs() <= 4000.0);
        }
    }

    #[test]
    fn gardner_acquires_both_sides_of_symbol_center() {
        let (front, truth) = waveform(5.0, 4000);
        for phase in [0.2, 0.8] {
            let config = GardnerConfig {
                initial_phase_symbols: phase,
                ..Default::default()
            };
            let a = gardner(&front, &config).unwrap();
            let b = gardner(&front, &config).unwrap();
            assert_eq!(a.soft, b.soft);
            assert!(bit_errors(&a.soft, &truth) < 10);
        }
    }

    #[test]
    fn zero_bandwidth_preserves_the_sample_grid() {
        let (front, _) = waveform(5.0, 500);
        let config = GardnerConfig {
            bandwidth: 0.0,
            ..Default::default()
        };
        let result = gardner(&front, &config).unwrap();
        for (i, value) in result.soft.iter().enumerate() {
            assert_eq!(*value, interpolate(&front.samples, (i as f64 + 32.5) * 5.0));
        }
    }

    #[test]
    fn invalid_inputs_fail_closed_and_short_streams_are_empty() {
        let mut front = Frontend {
            samples: vec![],
            samples_per_symbol: 5.0,
        };
        assert!(
            gardner(&front, &GardnerConfig::default())
                .unwrap()
                .soft
                .is_empty()
        );
        front.samples = vec![f64::NAN; 1000];
        assert!(gardner(&front, &GardnerConfig::default()).is_err());
        front.samples = vec![0.0; 1000];
        for bandwidth in [-0.01, f64::NAN, 0.2] {
            assert!(
                gardner(
                    &front,
                    &GardnerConfig {
                        bandwidth,
                        ..Default::default()
                    }
                )
                .is_err()
            );
        }
        assert!(boxcar_frontend(&[f64::INFINITY], 48_000, 9600.0).is_err());
        assert!(boxcar_frontend(&[1.0], 48_000, f64::NAN).is_err());
        assert!(
            boxcar_frontend(&[0.0; 1000], 48_000, 9600.0)
                .unwrap()
                .samples
                .is_empty()
        );
    }

    #[test]
    fn four_boxcar_dc_rejection_removes_constant_and_agc_is_scale_invariant() {
        let constant = boxcar_frontend(&vec![0.375; 4000], 48_000, 9600.0).unwrap();
        assert!(constant.samples.iter().all(|x| x.abs() < 1e-8));
        let (front, _) = waveform(5.0, 2000);
        let scaled: Vec<_> = front.samples.iter().map(|x| x * 1e-100).collect();
        let a = boxcar_frontend(&front.samples, 48_000, 9600.0).unwrap();
        let b = boxcar_frontend(&scaled, 48_000, 9600.0).unwrap();
        assert_eq!(a.samples.len(), b.samples.len());
        assert!(
            a.samples
                .iter()
                .zip(b.samples)
                .all(|(a, b)| (a - b).abs() < 1e-10)
        );
    }

    #[test]
    fn noise_free_empty_signal_cannot_emit_ax25_frames() {
        let front = Frontend {
            samples: vec![0.0; 6000],
            samples_per_symbol: 5.0,
        };
        let soft = gardner(&front, &GardnerConfig::default()).unwrap().soft;
        assert!(
            crate::protocol::decode_ax25(&soft, 0.0, &[false, true])
                .unwrap()
                .is_empty()
        );
    }
}
