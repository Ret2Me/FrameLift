//! Bounded coherent PSK -> soft bits, independent of frame content and truth.
//! The finite timing/ambiguity bank is streamed to the existing protocol layer.
//! No decoded bytes, CRC outcomes or reference bits steer synchronization.
use crate::{
    dsp,
    generic::{Demodulator, Signal, Waveform},
    physical,
};
use num_complex::Complex64;
use serde::{Deserialize, Serialize};
use std::f64::consts::{PI, TAU};

#[path = "psk_acquisition.rs"]
mod acquisition;
#[path = "psk_carrier.rs"]
mod carrier;

#[derive(Clone, Debug, Default, Serialize, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case", deny_unknown_fields)]
pub enum MatchedFilter {
    #[default]
    Rectangular,
    RootRaisedCosine {
        rolloff: f64,
        span_symbols: usize,
    },
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(default, deny_unknown_fields)]
pub struct PskConfig {
    /// Residual CFO search after subtracting waveform.carrier_hz (if given).
    pub max_carrier_offset_hz: Option<f64>,
    /// Opt-in signal-ranked carrier portfolio (1..=3). One is the exact legacy
    /// pass; alternatives retain it and require separately accounted work.
    #[serde(skip_serializing_if = "single_carrier")]
    pub carrier_candidates: usize,
    /// Opt-in additional BPSK linear-drift acquisition bank, Hz/second.
    /// The complete constant-CFO bank remains enabled. Both fitted endpoint
    /// frequencies must also lie inside max_carrier_offset_hz.
    pub max_carrier_drift_hz_per_s: Option<f64>,
    pub matched_filter: MatchedFilter,
    /// Second-order carrier/joint timing loops, radians per symbol. Zero disables
    /// the carrier loop and the additional joint-tracking bank.
    pub loop_bandwidth: f64,
    /// NRZ-M on serialized bits, distinct from AX.25 NRZI and differential QPSK.
    pub differential_binary: bool,
}
fn single_carrier(count: &usize) -> bool {
    *count == 1
}
impl Default for PskConfig {
    fn default() -> Self {
        Self {
            max_carrier_offset_hz: None,
            carrier_candidates: 1,
            max_carrier_drift_hz_per_s: None,
            matched_filter: MatchedFilter::Rectangular,
            loop_bandwidth: 0.01,
            differential_binary: false,
        }
    }
}

pub struct PskDemodulator(pub &'static str);

// One source of truth for generation, admission and work accounting. The extra
// low-SPS lane changes timing bandwidth only; every existing lane remains.
fn synchronization_lanes(sps: f64, bandwidth: f64) -> usize {
    if bandwidth == 0.0 {
        1
    } else if sps > 4.0 {
        2
    } else {
        3 + usize::from(bandwidth < 0.2)
    }
}

/// Soft-bit visits and conservative frontend work units (FIR, FFT, interpolation
/// and tracking), separate from protocol/FEC cost. Neither is a wall deadline.
pub fn work_estimate(rate: u32, w: &Waveform, samples: usize) -> Result<(u128, u128), String> {
    validate_waveform(rate, w)?;
    let c = w.psk.clone().unwrap_or_default();
    let sps = rate as f64 / w.dsp.baud;
    let min_step = w
        .dsp
        .rate_errors_ppm
        .iter()
        .map(|ppm| sps * (1.0 + ppm * 1e-6))
        .fold(f64::INFINITY, f64::min);
    let bits_per_symbol = if w.dsp.mode == "bpsk" { 1u128 } else { 2 };
    let variants = match w.dsp.mode.as_str() {
        "bpsk" => 2,
        "qpsk" => 8,
        _ => 48,
    };
    // Joint timing can advance by as little as 0.79 nominal symbols (bounded
    // integral rate +/-1% plus proportional timing correction +/-0.2).
    let count = (samples as f64 / min_step).ceil() as u128 + 2;
    let tracked_count = if c.loop_bandwidth > 0.0 {
        (samples as f64 / (min_step * 0.79)).ceil() as u128 + 2
    } else {
        0
    };
    // Low-rate input needs more accurate fractional delay. Keep the original
    // linear joint bank and add a four-tap bank only at <=4 samples/symbol.
    let tracked_lanes = synchronization_lanes(sps, c.loop_bandwidth) - 1;
    let visits = (count + tracked_count * tracked_lanes as u128)
        * bits_per_symbol
        * w.dsp.phase_bins as u128
        * w.dsp.rate_errors_ppm.len() as u128
        * variants;
    let tap_count = match c.matched_filter {
        MatchedFilter::Rectangular => sps.round() as usize,
        MatchedFilter::RootRaisedCosine { span_symbols, .. } => {
            2 * (span_symbols as f64 * sps / 2.0).ceil() as usize + 1
        }
    };
    // Match physical::carrier_fft's bounded transform length, including its
    // minimum transform cost on short records. Charge 8 units/butterfly stage.
    let fft_size = 65_536usize
        .max(samples.saturating_mul(32))
        .checked_next_power_of_two()
        .unwrap_or(262_144)
        .min(262_144);
    let fft_work = 8 * fft_size as u128 * fft_size.ilog2() as u128;
    let tracking_work = tracked_count
        * w.dsp.phase_bins as u128
        * w.dsp.rate_errors_ppm.len() as u128
        * if w.dsp.mode == "oqpsk" { 2 } else { 1 }
        * (64 + tracked_lanes.saturating_sub(1) as u128 * 96);
    let banks = if c.max_carrier_drift_hz_per_s.is_some() {
        2
    } else {
        1
    };
    let single_frontend = (samples as u128 * (tap_count as u128 + 8) + fft_work + tracking_work)
        * banks
        + if banks == 2 {
            carrier::work(samples, sps)
        } else {
            0
        };
    Ok((
        visits * banks * c.carrier_candidates as u128,
        single_frontend * c.carrier_candidates as u128
            + if c.carrier_candidates > 1 {
                acquisition::work(samples)
            } else {
                0
            },
    ))
}

/// All configured phases/rates and eight quadrant/spectral mappings are tried.
/// OQPSK also tries both delayed branches and adjacent I/Q symbol pairing.
pub fn validate_waveform(rate: u32, w: &Waveform) -> Result<(), String> {
    let c = w.psk.clone().unwrap_or_default();
    if !(1..=3).contains(&c.carrier_candidates) {
        return Err("PSK carrier_candidates must be 1..3".into());
    }
    if !matches!(w.dsp.mode.as_str(), "bpsk" | "qpsk" | "oqpsk")
        || rate == 0
        || !w.dsp.baud.is_finite()
        || w.dsp.baud <= 0.0
        || w.decimation != 1
        || w.cutoff_hz.is_some()
    {
        return Err("PSK requires bpsk/qpsk/oqpsk, finite positive symbol baud, decimation=1 and no FSK cutoff".into());
    }
    let sps = rate as f64 / w.dsp.baud;
    let variants = match w.dsp.mode.as_str() {
        "bpsk" => 2,
        "qpsk" => 8,
        _ => 48,
    };
    if !(2.0..=128.0).contains(&sps)
        || w.dsp.bank != "full"
        || !(4..=128).contains(&w.dsp.phase_bins)
        || w.dsp.rate_errors_ppm.is_empty()
        || w.dsp
            .phase_bins
            .checked_mul(w.dsp.rate_errors_ppm.len())
            .and_then(|v| v.checked_mul(variants))
            .and_then(|v| v.checked_mul(synchronization_lanes(sps, c.loop_bandwidth)))
            .and_then(|v| v.checked_mul(c.carrier_candidates))
            .and_then(|v| {
                v.checked_mul(if c.max_carrier_drift_hz_per_s.is_some() {
                    2
                } else {
                    1
                })
            })
            .is_none_or(|v| v > 32768)
    {
        return Err(
            "PSK requires 2..128 samples/symbol, full bank, 4..128 phases, and <=32768 streams"
                .into(),
        );
    }
    if w.dsp
        .rate_errors_ppm
        .iter()
        .any(|v| !v.is_finite() || v.abs() > 10000.0)
        || !c.loop_bandwidth.is_finite()
        || !(0.0..=0.2).contains(&c.loop_bandwidth)
        || w.carrier_hz
            .is_some_and(|v| !v.is_finite() || v.abs() >= rate as f64 / 2.0)
    {
        return Err("invalid PSK clock error, loop bandwidth or carrier".into());
    }
    let order = if w.dsp.mode == "bpsk" { 2.0 } else { 4.0 };
    if c.max_carrier_drift_hz_per_s.is_some_and(|drift| {
        w.dsp.mode != "bpsk" || !drift.is_finite() || drift <= 0.0 || drift > 1_000_000.0
    }) {
        return Err(
            "linear carrier drift requires BPSK and a finite positive bound <=1000000 Hz/s".into(),
        );
    }
    let maximum = c.max_carrier_offset_hz.unwrap_or(w.dsp.baud * 0.2);
    if !maximum.is_finite() || maximum <= 0.0 || maximum * order >= rate as f64 / 2.0 {
        return Err("PSK residual carrier bound aliases the Mth-power spectrum".into());
    }
    if c.carrier_candidates > 1 && w.carrier_hz.unwrap_or(0.0).abs() + maximum >= rate as f64 / 2.0
    {
        return Err("PSK carrier portfolio requires its entire coarse search domain strictly inside IQ Nyquist; recenter the input explicitly".into());
    }
    if let MatchedFilter::RootRaisedCosine {
        rolloff,
        span_symbols,
    } = c.matched_filter
        && (!rolloff.is_finite()
            || !(0.01..=1.0).contains(&rolloff)
            || !(2..=16).contains(&span_symbols))
    {
        return Err("RRC requires rolloff 0.01..1 and span 2..16 symbols".into());
    }
    Ok(())
}

pub(crate) fn taps(filter: &MatchedFilter, sps: f64) -> Vec<f64> {
    match filter {
        MatchedFilter::Rectangular => vec![1.0 / sps.round(); sps.round() as usize],
        MatchedFilter::RootRaisedCosine {
            rolloff: a,
            span_symbols,
        } => {
            let half = (*span_symbols as f64 * sps / 2.0).ceil() as usize;
            let mut result: Vec<_> = (0..2 * half + 1)
                .map(|i| {
                    let t = (i as f64 - half as f64) / sps;
                    if t.abs() < 1e-12 {
                        1.0 + a * (4.0 / PI - 1.0)
                    } else if (4.0 * a * t.abs() - 1.0).abs() < 1e-10 {
                        a / 2.0_f64.sqrt()
                            * ((1.0 + 2.0 / PI) * (PI / (4.0 * a)).sin()
                                + (1.0 - 2.0 / PI) * (PI / (4.0 * a)).cos())
                    } else {
                        ((PI * t * (1.0 - a)).sin() + 4.0 * a * t * (PI * t * (1.0 + a)).cos())
                            / (PI * t * (1.0 - (4.0 * a * t).powi(2)))
                    }
                })
                .collect();
            let norm = result.iter().map(|x| x * x).sum::<f64>().sqrt();
            result.iter_mut().for_each(|x| *x /= norm);
            result
        }
    }
}
pub(crate) fn filter(signal: &[Complex64], taps: &[f64]) -> Result<Vec<Complex64>, String> {
    crate::compute::current().fir_complex(signal, taps, 1, true)
}
fn interpolate(values: &[Complex64], t: f64) -> Complex64 {
    let i = t.floor() as usize;
    values[i] + (values[(i + 1).min(values.len() - 1)] - values[i]) * (t - i as f64)
}

/// Four-point Lagrange fractional delay. At record endpoints use the exact
/// legacy linear rule: no invented samples and no shorter output schedule.
fn interpolate_cubic(values: &[Complex64], t: f64) -> Complex64 {
    let i = t.floor() as usize;
    if i == 0 || i + 2 >= values.len() {
        return interpolate(values, t);
    }
    let u = t - i as f64;
    let a = -u * (u - 1.0) * (u - 2.0) / 6.0;
    let b = (u + 1.0) * (u - 1.0) * (u - 2.0) / 2.0;
    let c = -(u + 1.0) * u * (u - 2.0) / 2.0;
    let d = (u + 1.0) * u * (u - 1.0) / 6.0;
    values[i - 1] * a + values[i] * b + values[i + 1] * c + values[i + 2] * d
}

fn symbol_count(length: usize, phase: f64, half: f64, step: f64) -> usize {
    let available = length as f64 - 1.0 - phase - half;
    if available < 0.0 {
        0
    } else {
        (available / step).floor() as usize + 1
    }
}
fn track(symbols: &mut [Complex64], bpsk: bool, bandwidth: f64) {
    if bandwidth == 0.0 {
        return;
    }
    let damping = 0.5_f64.sqrt();
    let d = 1.0 + 2.0 * damping * bandwidth + bandwidth * bandwidth;
    let alpha = 4.0 * damping * bandwidth / d;
    let beta = 4.0 * bandwidth * bandwidth / d;
    let mut phase = 0.0_f64;
    let mut frequency = 0.0_f64;
    for value in symbols {
        *value *= Complex64::from_polar(1.0, -phase);
        let error = if bpsk {
            value.re.signum() * value.im
        } else {
            value.re.signum() * value.im - value.im.signum() * value.re
        };
        let error = (error / value.norm().max(1e-12)).clamp(-1.0, 1.0);
        frequency = (frequency + beta * error).clamp(-0.2, 0.2);
        phase = (phase + frequency + alpha * error + PI).rem_euclid(TAU) - PI;
    }
}

/// One carrier/timing hypothesis; field names keep the two bandwidths and
/// staggered branches distinguishable at call sites.
struct JointTracking {
    step: f64,
    phase: f64,
    bpsk: bool,
    oqpsk: bool,
    branch: usize,
    bandwidth: f64,
    timing_bandwidth: f64,
    power: f64,
    cubic: bool,
}

/// Additional signal-only bank. Unlike rotating already staggered scalar I/Q,
/// rotate each *complex* observation at its own time before projecting an arm.
/// A stagger-aware Costas detector and per-arm Gardner detector then track
/// carrier and timing jointly. No decisions from a protocol enter these loops.
fn joint_symbols(values: &[Complex64], tracking: JointTracking) -> Vec<Complex64> {
    let JointTracking {
        step,
        phase,
        bpsk,
        oqpsk,
        branch,
        bandwidth,
        timing_bandwidth,
        power,
        cubic,
    } = tracking;
    let damping = 0.5_f64.sqrt();
    let denominator = 1.0 + 2.0 * damping * bandwidth + bandwidth * bandwidth;
    let alpha = 4.0 * damping * bandwidth / denominator;
    let beta = 4.0 * bandwidth * bandwidth / denominator;
    let (timing_alpha, timing_beta) = if timing_bandwidth == bandwidth {
        // Preserve the old floating-point operations exactly for existing lanes.
        (alpha, beta)
    } else {
        let d = 1.0 + 2.0 * damping * timing_bandwidth + timing_bandwidth * timing_bandwidth;
        (
            4.0 * damping * timing_bandwidth / d,
            4.0 * timing_bandwidth * timing_bandwidth / d,
        )
    };
    let mut carrier_phase = 0.0;
    let mut frequency = 0.0;
    let mut clock_rate = 0.0_f64;
    let mut previous_at = phase;
    let mut at = phase + step;
    let mut out = Vec::new();
    while at + if oqpsk { step * 0.505 } else { 0.0 } <= (values.len() - 1) as f64 {
        // One NCO reference for all early/middle/late samples in this update.
        // Extrapolation uses their actual input-time difference, not symbol index.
        let sample = |t: f64| {
            (if cubic {
                interpolate_cubic(values, t)
            } else {
                interpolate(values, t)
            }) * Complex64::from_polar(1.0, -carrier_phase - frequency * (t - at) / step)
        };
        let half = if oqpsk {
            step * (1.0 + clock_rate) / 2.0
        } else {
            0.0
        };
        let (i_delay, q_delay) = if branch == 1 {
            (half, 0.0)
        } else {
            (0.0, half)
        };
        let i_now = sample(at + i_delay);
        let q_now = if oqpsk { sample(at + q_delay) } else { i_now };
        let current = Complex64::new(i_now.re, if bpsk { 0.0 } else { q_now.im });
        let midpoint = (previous_at + at) * 0.5;
        let timing_error = if oqpsk {
            sample(midpoint + i_delay).re * (sample(previous_at + i_delay).re - i_now.re)
                + sample(midpoint + q_delay).im * (sample(previous_at + q_delay).im - q_now.im)
        } else {
            let mid = sample(midpoint);
            let difference = sample(previous_at) - i_now;
            (mid.conj() * difference).re
        };
        let timing_error = (timing_error / power).clamp(-1.0, 1.0);
        let carrier_error = if bpsk {
            i_now.re.signum() * i_now.im / i_now.norm().max(1e-12)
        } else {
            (i_now.re.signum() * i_now.im - q_now.im.signum() * q_now.re)
                / ((i_now.norm() + q_now.norm()) * 0.5).max(1e-12)
        }
        .clamp(-1.0, 1.0);
        out.push(current);
        clock_rate = (clock_rate + timing_beta * timing_error).clamp(-0.01, 0.01);
        let advance = step * (1.0 + clock_rate + (timing_alpha * timing_error).clamp(-0.2, 0.2));
        frequency = (frequency + beta * carrier_error).clamp(-0.2, 0.2);
        carrier_phase = (carrier_phase + frequency * advance / step + alpha * carrier_error + PI)
            .rem_euclid(TAU)
            - PI;
        previous_at = at;
        at += advance;
    }
    out
}

impl Demodulator for PskDemodulator {
    fn modulation_families(&self) -> Vec<String> {
        vec![self.0.into()]
    }
    fn features(&self) -> Vec<String> {
        vec![
            "coherent_iq",
            "soft_bits",
            "full_timing_bank",
            "explicit_phase_ambiguities",
            "optional_rrc",
            "joint_carrier_timing",
            "low_sps_cubic_joint_bank",
            "low_sps_fast_timing_bank",
            "optional_bpsk_linear_carrier_drift",
            "optional_signal_ranked_carrier_portfolio",
        ]
        .into_iter()
        .map(str::to_owned)
        .collect()
    }
    fn resume_identity(&self) -> Option<String> {
        Some(format!("rust-psk-soft-v8:{}", self.0))
    }
    fn accepts(&self, iq: bool) -> bool {
        iq
    }
    fn demodulate(&self, _: Signal<'_>, _: u32, _: &Waveform) -> Result<dsp::Frontend, String> {
        Err("coherent PSK uses visit_soft_symbols, not a real FSK discriminator".into())
    }
    fn visit_soft_symbols<'visit>(
        &self,
        signal: Signal<'_>,
        rate: u32,
        w: &Waveform,
        visit: &'visit mut crate::generic::SoftSymbolVisitor<'visit>,
    ) -> Result<usize, String> {
        validate_waveform(rate, w)?;
        if w.dsp.mode != self.0 {
            return Err("PSK modulation/profile mismatch".into());
        }
        let Signal::Iq(iq) = signal else {
            return Err("PSK requires complex IQ, not FM-demodulated OGG".into());
        };
        let c = w.psk.clone().unwrap_or_default();
        let bpsk = self.0 == "bpsk";
        let oqpsk = self.0 == "oqpsk";
        let sps = rate as f64 / w.dsp.baud;
        if iq.len() > dsp::MAX_PCM_WINDOW_SAMPLES {
            return Err("PSK window exceeds sample bound".into());
        }
        if (iq.len() as f64) < 16.0 * sps {
            return Err("PSK window has fewer than 16 symbols".into());
        }
        let (visits, frontend_work) = work_estimate(rate, w, iq.len())?;
        // Account for all tracking work without rejecting the previously
        // admitted 5-second PicSat plan. This CPU-only allowance is common to
        // both acquisition modes; memory/stream/soft/FEC/aggregate caps remain.
        let frontend_limit = 500_000_000;
        if visits > 200_000_000 || frontend_work > frontend_limit {
            return Err(format!(
                "PSK bank exceeds 200 million soft-bit visits or {frontend_limit} frontend work units; shorten explicit windows"
            ));
        }
        // Constant/zero windows are valid negative observations, never frames.
        if iq.len() >= 8
            && iq
                .iter()
                .all(|v| v.re.is_finite() && v.im.is_finite() && *v == iq[0])
        {
            return Ok(0);
        }
        if c.carrier_candidates > 1 {
            let maximum = c.max_carrier_offset_hz.unwrap_or(w.dsp.baud * 0.2);
            let peaks = acquisition::peaks(
                iq,
                rate,
                w.carrier_hz,
                w.dsp.baud,
                if bpsk { 2 } else { 4 },
                maximum,
                c.carrier_candidates,
            )?;
            let mut baseline = w.clone();
            baseline.psk = Some(PskConfig {
                carrier_candidates: 1,
                ..c.clone()
            });
            // Never replace or relabel the legacy bank. Recursive calls have
            // one carrier only, so the full portfolio is bounded and finite.
            let mut attempts = self.visit_soft_symbols(Signal::Iq(iq), rate, &baseline, visit)?;
            for (rank, peak) in peaks.iter().enumerate().skip(1) {
                let bound = (0.01 * w.dsp.baud).min(maximum - peak.offset_hz.abs());
                // A peak exactly at the search boundary has no positive local
                // interval. It is not replaced by an outcome-selected guess.
                if bound <= 0.0 {
                    continue;
                }
                let mut alternative = baseline.clone();
                alternative.carrier_hz = Some(w.carrier_hz.unwrap_or(0.0) + peak.offset_hz);
                alternative.psk.as_mut().unwrap().max_carrier_offset_hz = Some(bound);
                attempts += self.visit_soft_symbols(
                    Signal::Iq(iq),
                    rate,
                    &alternative,
                    &mut |soft, threshold, score, label| {
                        let label = format!(
                            "{}:carrier=peak-{rank}:offset_bits={:016x}",
                            label.ok_or("PSK carrier sub-bank has no stream label")?,
                            peak.offset_hz.to_bits()
                        );
                        visit(soft, threshold, score, Some(&label))
                    },
                )?;
            }
            return Ok(attempts);
        }
        let mut normalized = physical::normalize_record(iq)?;
        if let Some(carrier) = w.carrier_hz {
            for (i, v) in normalized.iter_mut().enumerate() {
                *v *= Complex64::from_polar(1.0, -TAU * carrier * i as f64 / rate as f64);
            }
        }
        let maximum_carrier = c.max_carrier_offset_hz.unwrap_or(w.dsp.baud * 0.2);
        let drift_model = c.max_carrier_drift_hz_per_s.and_then(|maximum_drift| {
            carrier::fit(
                &normalized,
                rate as f64,
                w.dsp.baud,
                maximum_carrier,
                maximum_drift,
            )
        });
        let mut attempts = 0;
        let mut soft = Vec::new();
        for carrier_lane in 0..if drift_model.is_some() { 2 } else { 1 } {
            let drift_corrected;
            let source = if carrier_lane == 1 {
                drift_corrected =
                    carrier::correct(&normalized, rate as f64, drift_model.as_ref().unwrap());
                &drift_corrected
            } else {
                &normalized
            };
            let (corrected, _, _) = physical::carrier_fft(
                source,
                rate as f64,
                if bpsk { 2 } else { 4 },
                Some(maximum_carrier),
            )?;
            let coefficients = taps(&c.matched_filter, sps);
            let filtered = filter(&corrected, &coefficients)?;
            // Identical reduction to the former per-hypothesis normalization, once
            // per window. Shared immutable scalar; no regrouping or precision change.
            let power = (filtered.iter().map(Complex64::norm_sqr).sum::<f64>()
                / filtered.len().max(1) as f64)
                .max(1e-12);
            // These are signal-only timing hypotheses; no known frame is used here.
            for &ppm in &w.dsp.rate_errors_ppm {
                let step = sps * (1.0 + ppm * 1e-6);
                for phase_index in 0..w.dsp.phase_bins {
                    let phase = phase_index as f64 * step / w.dsp.phase_bins as f64;
                    for branch in 0..if oqpsk { 2 } else { 1 } {
                        for joint in 0..synchronization_lanes(sps, c.loop_bandwidth) {
                            let half = if oqpsk { step / 2.0 } else { 0.0 };
                            let count = symbol_count(filtered.len(), phase, half, step);
                            let symbols: Vec<_> = if joint > 0 {
                                joint_symbols(
                                    &filtered,
                                    JointTracking {
                                        step,
                                        phase,
                                        bpsk,
                                        oqpsk,
                                        branch,
                                        bandwidth: c.loop_bandwidth,
                                        timing_bandwidth: if joint == 3 {
                                            (2.0 * c.loop_bandwidth).min(0.2)
                                        } else {
                                            c.loop_bandwidth
                                        },
                                        power,
                                        cubic: joint >= 2,
                                    },
                                )
                            } else {
                                (0..count)
                                    .map(|n| {
                                        let t = phase + n as f64 * step;
                                        if !oqpsk {
                                            interpolate(&filtered, t)
                                        } else {
                                            Complex64::new(
                                                interpolate(
                                                    &filtered,
                                                    t + if branch == 1 { half } else { 0.0 },
                                                )
                                                .re,
                                                interpolate(
                                                    &filtered,
                                                    t + if branch == 0 { half } else { 0.0 },
                                                )
                                                .im,
                                            )
                                        }
                                    })
                                    .collect()
                            };
                            for pairing in if oqpsk { -1_i32..=1 } else { 0_i32..=0 } {
                                let mut paired: Vec<_> = if pairing == 0 {
                                    symbols.clone()
                                } else {
                                    symbols
                                        .windows(2)
                                        .map(|p| {
                                            if pairing < 0 {
                                                Complex64::new(p[1].re, p[0].im)
                                            } else {
                                                Complex64::new(p[0].re, p[1].im)
                                            }
                                        })
                                        .collect()
                                };
                                if joint == 0 {
                                    track(&mut paired, bpsk, c.loop_bandwidth);
                                }
                                let score = paired
                                    .iter()
                                    .map(|v| {
                                        if bpsk {
                                            v.re.abs()
                                        } else {
                                            v.re.abs().min(v.im.abs())
                                        }
                                    })
                                    .sum::<f64>()
                                    / (paired.len() as f64).max(1.0);
                                for mapping in 0..if bpsk { 2 } else { 8 } {
                                    soft.clear();
                                    for v in &paired {
                                        if bpsk {
                                            soft.push(v.re * if mapping == 0 { 1.0 } else { -1.0 });
                                        } else {
                                            let (i, q) = if mapping & 4 == 0 {
                                                (v.re, v.im)
                                            } else {
                                                (v.im, v.re)
                                            };
                                            soft.push(
                                                i * if mapping & 1 == 0 { 1.0 } else { -1.0 },
                                            );
                                            soft.push(
                                                q * if mapping & 2 == 0 { 1.0 } else { -1.0 },
                                            );
                                        }
                                    }
                                    if c.differential_binary {
                                        // Soft XOR: sign disagreement means ONE. First bit unknown.
                                        for n in (1..soft.len()).rev() {
                                            let (a, b) = (soft[n - 1], soft[n]);
                                            soft[n] =
                                                -a.signum() * b.signum() * a.abs().min(b.abs());
                                        }
                                        if !soft.is_empty() {
                                            soft.remove(0);
                                        }
                                    }
                                    let mut label = format!(
                                        "{}:ppm={ppm}:phase={phase_index}:branch={branch}:pair={pairing}:map={mapping}",
                                        self.0
                                    );
                                    if joint == 1 {
                                        label.push_str(":sync=joint");
                                    } else if joint == 2 {
                                        label.push_str(":sync=joint-cubic");
                                    } else if joint == 3 {
                                        label.push_str(":sync=joint-cubic-fast-timing");
                                    }
                                    if carrier_lane == 1 {
                                        label.push_str(":carrier=linear-drift");
                                    }
                                    visit(&soft, 0.0, score, Some(&label))?;
                                    attempts += 1;
                                }
                            }
                        }
                    }
                }
            }
        }
        Ok(attempts)
    }
}

#[cfg(test)]
#[path = "tests/psk_tests.rs"]
mod tests;
