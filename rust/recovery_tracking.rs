//! Bounded decoder-assisted synchronization for linearly modulated PSK.
//!
//! Positive soft means denote bit one. A known pilot uses exactly +/-1;
//! decoder feedback uses `tanh(extrinsic_llr / 2)`, never hard decisions chosen
//! to satisfy a CRC. The optimizer only sees received IQ and those means.
//! It minimizes weighted signal residual on a fixed training partition, then
//! checks one winning hypothesis on the other partition. The partition is
//! anchored at source sample zero and does not move with a timing hypothesis.
//!
//! This guard is conditional waveform consistency, NOT an independent false
//! alarm test: decoder messages or initial acquisition may have used held-out
//! samples. Keep the original decode branch and require the normal independent
//! integrity checks on every additional decoded frame. No nonlinear FSK/CPM or
//! AFSK approximation is silently substituted for this linear PSK model.

use crate::psk::MatchedFilter;
use num_complex::Complex64;
use serde::{Deserialize, Serialize};
use std::f64::consts::{PI, TAU};

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(default, deny_unknown_fields)]
pub struct Config {
    /// Maximum displacement from the initial start, in original symbols.
    pub timing_bound_symbols: f64,
    /// Maximum relative symbol-clock change from the initial estimate.
    pub clock_bound_ppm: f64,
    pub carrier_bound_hz: f64,
    pub drift_bound_hz_per_s: f64,
    /// Coordinate sweeps; each sweep halves the search steps.
    pub iterations: usize,
    /// Required relative improvement over the original fitted waveform.
    pub minimum_holdout_improvement: f64,
    /// Reject a model explaining too little received held-out signal energy.
    pub minimum_holdout_explained: f64,
    /// Bound sum of rendered sample/pulse-tap operations, not elapsed time.
    pub maximum_work: u64,
}

impl Default for Config {
    fn default() -> Self {
        Self {
            timing_bound_symbols: 0.5,
            clock_bound_ppm: 2_000.,
            carrier_bound_hz: 20.,
            drift_bound_hz_per_s: 50.,
            iterations: 3,
            minimum_holdout_improvement: 0.01,
            minimum_holdout_explained: 0.1,
            maximum_work: 64_000_000,
        }
    }
}

impl Config {
    pub fn validate(&self) -> Result<(), String> {
        if !self.timing_bound_symbols.is_finite()
            || !(0.0..=1.0).contains(&self.timing_bound_symbols)
            || !self.clock_bound_ppm.is_finite()
            || !(0.0..=10_000.).contains(&self.clock_bound_ppm)
            || !self.carrier_bound_hz.is_finite()
            || !(0.0..=100_000.).contains(&self.carrier_bound_hz)
            || !self.drift_bound_hz_per_s.is_finite()
            || !(0.0..=100_000.).contains(&self.drift_bound_hz_per_s)
            || !(1..=6).contains(&self.iterations)
            || !self.minimum_holdout_improvement.is_finite()
            || !(0.001..=0.9).contains(&self.minimum_holdout_improvement)
            || !self.minimum_holdout_explained.is_finite()
            || !(0.05..=0.95).contains(&self.minimum_holdout_explained)
            || !(1..=256_000_000).contains(&self.maximum_work)
        {
            return Err("invalid bounded PSK recovery tracking configuration".into());
        }
        Ok(())
    }
}

/// Carrier frequency is instantaneous at `carrier_reference_sample`.
/// OQPSK start refers to the undelayed arm; conjugation changes Q polarity.
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Geometry {
    pub start_sample: f64,
    pub samples_per_symbol: f64,
    pub carrier_hz: f64,
    pub carrier_drift_hz_per_s: f64,
    pub carrier_reference_sample: f64,
    pub delayed_q: bool,
    pub conjugated: bool,
}

#[derive(Clone, Debug, Serialize)]
pub struct Report {
    pub accepted: bool,
    pub reason: &'static str,
    pub geometry: Geometry,
    /// Complex gain at carrier reference; stored as [real, imaginary].
    pub gain: [f64; 2],
    pub evaluated_hypotheses: usize,
    pub work: u64,
    pub start_sample: usize,
    pub end_sample: usize,
    pub training_samples: usize,
    pub holdout_samples: usize,
    pub training_residual_before: f64,
    pub training_residual_after: f64,
    pub holdout_residual_before: f64,
    pub holdout_residual_after: f64,
    pub holdout_improvement: f64,
    pub holdout_explained: f64,
}

/// Convert extrinsic (not APP) feedback to bounded expectation values.
pub fn means_from_extrinsic(llr: &[f64]) -> Result<Vec<f64>, String> {
    if llr.is_empty() || llr.len() > 8192 || llr.iter().any(|x| !x.is_finite()) {
        return Err("tracking requires 1..8192 finite extrinsic LLRs".into());
    }
    Ok(llr
        .iter()
        .map(|x| (0.5 * x.clamp(-100., 100.)).tanh())
        .collect())
}

pub fn carrier_phase(sample: f64, rate: u32, geometry: &Geometry) -> f64 {
    let t = (sample - geometry.carrier_reference_sample) / f64::from(rate);
    TAU * (geometry.carrier_hz * t + 0.5 * geometry.carrier_drift_hz_per_s * t * t)
}

/// Apply accepted complex gain/CFO/drift correction without changing the sample
/// grid. Then sample/filter at the returned start and symbol step. The output
/// is baseband (do not remove the carrier a second time). No samples are added,
/// dropped, or replaced by the fitted waveform. Rejected reports are refused.
pub fn corrected_iq(
    iq: &[Complex64],
    rate: u32,
    report: &Report,
) -> Result<Vec<Complex64>, String> {
    let gain = Complex64::new(report.gain[0], report.gain[1]);
    if !report.accepted || rate == 0 || gain.norm_sqr() < 1e-12 || !finite(gain) {
        return Err("carrier correction requires an accepted finite tracking fit".into());
    }
    iq.iter()
        .enumerate()
        .map(|(i, y)| {
            if !finite(*y) {
                return Err("nonfinite tracking input".into());
            }
            let value = *y
                * Complex64::from_polar(1., -carrier_phase(i as f64, rate, &report.geometry))
                / gain;
            if !finite(value) {
                Err("nonfinite corrected IQ".into())
            } else {
                Ok(value)
            }
        })
        .collect()
}

fn finite(z: Complex64) -> bool {
    z.re.is_finite() && z.im.is_finite()
}

fn rrc(t: f64, a: f64) -> f64 {
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

fn arm(means: &[f64], stride: usize, branch: usize, at: f64, pulse: &MatchedFilter) -> (f64, f64) {
    let symbol = |k: isize| {
        if k < 0 || k as usize >= means.len() / stride {
            (0., 0.)
        } else {
            let m = means[k as usize * stride + branch];
            (m, (1. - m * m).max(0.))
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
            let (mut mean, mut variance) = (0., 0.);
            for k in (center - half).ceil() as isize..=(center + half).floor() as isize {
                let (m, v) = symbol(k);
                let h = rrc(center - k as f64, *rolloff);
                mean += h * m;
                variance += h * h * v;
            }
            (mean, variance)
        }
    }
}

struct Model<'a> {
    iq: &'a [Complex64],
    means: &'a [f64],
    pulse: &'a MatchedFilter,
    rate: u32,
    stride: usize,
    staggered: bool,
    begin: usize,
    end: usize,
    partition_step: f64,
    weights: Vec<f64>,
}

impl Model<'_> {
    fn sample(&self, i: usize, g: &Geometry) -> (Complex64, f64) {
        let t = (i as f64 - g.start_sample) / g.samples_per_symbol;
        let delay = if self.staggered { 0.5 } else { 0. };
        let (re, vr) = arm(
            self.means,
            self.stride,
            0,
            t - if g.delayed_q { 0. } else { delay },
            self.pulse,
        );
        let (im, vi) = if self.stride == 2 {
            arm(
                self.means,
                2,
                1,
                t - if g.delayed_q { delay } else { 0. },
                self.pulse,
            )
        } else {
            (0., 0.)
        };
        let mean = Complex64::new(re, if g.conjugated { -im } else { im });
        let power = mean.norm_sqr();
        let reliability = if power + vr + vi > 1e-12 {
            power / (power + vr + vi)
        } else {
            0.
        };
        (
            mean * Complex64::from_polar(1., carrier_phase(i as f64, self.rate, g)),
            reliability,
        )
    }

    fn training(&self, i: usize) -> bool {
        ((i as f64 / self.partition_step).floor() as usize).is_multiple_of(2)
    }

    fn fit(&self, g: &Geometry) -> Option<Fit> {
        let (mut xx, mut yy) = (0., 0.);
        let mut xy = Complex64::new(0., 0.);
        let mut count = 0;
        for i in self.begin..self.end {
            if !self.training(i) {
                continue;
            }
            let (x, _) = self.sample(i, g);
            let w = self.weights[i - self.begin];
            if w < 0.01 {
                continue;
            }
            xx += w * x.norm_sqr();
            yy += w * self.iq[i].norm_sqr();
            xy += w * x.conj() * self.iq[i];
            count += 1;
        }
        if xx < 1e-10 || yy < 1e-10 || count < 32 {
            return None;
        }
        let gain = xy / xx;
        // Normalization keeps changing confidence weights from favoring a
        // hypothesis merely because it selects a lower-energy sample subset.
        let residual = (1. - xy.norm_sqr() / (xx * yy)).max(0.);
        if !finite(gain) || !residual.is_finite() {
            return None;
        }
        Some(Fit {
            gain,
            residual,
            count,
        })
    }

    fn holdout(&self, g: &Geometry, gain: Complex64) -> (f64, usize) {
        let (mut yy, mut residual, mut count) = (0., 0., 0);
        for i in self.begin..self.end {
            if self.training(i) {
                continue;
            }
            let (x, _) = self.sample(i, g);
            let w = self.weights[i - self.begin];
            if w < 0.01 {
                continue;
            }
            yy += w * self.iq[i].norm_sqr();
            residual += w * (self.iq[i] - gain * x).norm_sqr();
            count += 1;
        }
        if yy < 1e-10 {
            (1., count)
        } else {
            (residual / yy, count)
        }
    }
}

#[derive(Clone, Copy)]
struct Fit {
    gain: Complex64,
    residual: f64,
    count: usize,
}

/// Refine a PSK hypothesis, preserving the original result in rejected reports.
/// `wire_means` starts with the known sync/pilot and follows actual wire order,
/// including any known header. Do not provide guessed user payload or fixture
/// truth. For unverified payload use decoder extrinsic expectations. At least
/// 32 useful source samples in each fixed partition are required.
pub fn refine_psk(
    iq: &[Complex64],
    rate: u32,
    modulation: &str,
    pulse: &MatchedFilter,
    initial: &Geometry,
    wire_means: &[f64],
    config: &Config,
) -> Result<Report, String> {
    config.validate()?;
    let stride = match modulation {
        "bpsk" | "bpsk_rectangular" => 1,
        "qpsk" | "oqpsk" => 2,
        _ => return Err("decoder-assisted linear tracking supports PSK only; nonlinear CPM/AFSK require a different likelihood".into()),
    };
    if rate == 0
        || !(64..=1_048_576).contains(&iq.len())
        || !(32..=8192).contains(&wire_means.len())
        || !wire_means.len().is_multiple_of(stride)
        || wire_means.iter().any(|v| !v.is_finite() || v.abs() > 1.)
        || iq.iter().any(|v| !finite(*v) || v.norm_sqr() > 1e12)
        || !initial.start_sample.is_finite()
        || initial.start_sample < 0.
        || !initial.samples_per_symbol.is_finite()
        || !(2.0..=128.).contains(&initial.samples_per_symbol)
        || !initial.carrier_hz.is_finite()
        || !initial.carrier_drift_hz_per_s.is_finite()
        || !initial.carrier_reference_sample.is_finite()
        || !(0.0..iq.len() as f64).contains(&initial.carrier_reference_sample)
    {
        return Err("invalid finite bounded PSK tracking samples, means or geometry".into());
    }
    let pulse_width = match pulse {
        MatchedFilter::Rectangular => 1,
        MatchedFilter::RootRaisedCosine {
            rolloff,
            span_symbols,
        } => {
            if !rolloff.is_finite()
                || !(0.01..=1.).contains(rolloff)
                || !(2..=16).contains(span_symbols)
            {
                return Err("tracking requires a valid finite RRC pulse".into());
            }
            span_symbols + 1
        }
    };
    let timing_bound = initial.samples_per_symbol * config.timing_bound_symbols;
    let clock_bound = initial.samples_per_symbol * config.clock_bound_ppm * 1e-6;
    let duration = iq.len() as f64 / f64::from(rate);
    if initial.carrier_hz.abs()
        + config.carrier_bound_hz
        + (initial.carrier_drift_hz_per_s.abs() + config.drift_bound_hz_per_s) * duration
        >= f64::from(rate) / 2.
        || initial.samples_per_symbol - clock_bound < 2.
        || initial.samples_per_symbol + clock_bound > 128.
    {
        return Err("tracking search crosses the carrier Nyquist or symbol-step bound".into());
    }
    // Use the common interior of every allowed model. This keeps the evaluated
    // source interval fixed; an optimizer cannot win by cropping hard samples.
    let symbols = wire_means.len() as f64 / stride as f64;
    let guard_symbols = pulse_width as f64 / 2. + if modulation == "oqpsk" { 0.5 } else { 0. };
    let begin = (initial.start_sample
        + timing_bound
        + guard_symbols * (initial.samples_per_symbol + clock_bound))
        .ceil()
        .max(0.) as usize;
    let end = (initial.start_sample - timing_bound
        + (symbols - guard_symbols) * (initial.samples_per_symbol - clock_bound))
        .floor()
        .max(0.) as usize;
    if end > iq.len() || end <= begin || end - begin < 128 {
        return Err("tracking needs at least 128 common interior source samples".into());
    }
    let mut model = Model {
        iq,
        means: wire_means,
        pulse,
        rate,
        stride,
        staggered: modulation == "oqpsk",
        begin,
        end,
        partition_step: initial.samples_per_symbol,
        weights: Vec::new(),
    };
    let evaluation_work = (end - begin) as u64 * pulse_width as u64 * stride as u64;
    let maximum_evaluations = 1 + config.iterations * 4 * 4;
    let bound = evaluation_work
        .checked_mul((maximum_evaluations + 3) as u64)
        .ok_or("tracking work overflow")?;
    if bound > config.maximum_work {
        return Err("PSK tracking exceeds configured sample/pulse work budget".into());
    }
    // Freeze the uncertainty weights before any search. Moving uncertainty
    // masks must not let a trial win by excluding inconvenient observations.
    model.weights = (begin..end).map(|i| model.sample(i, initial).1).collect();
    let initial_fit = model.fit(initial);
    let mut report = Report {
        accepted: false,
        reason: "insufficient_reliable_training",
        geometry: initial.clone(),
        gain: [1., 0.],
        evaluated_hypotheses: 1,
        work: 2 * evaluation_work,
        start_sample: begin,
        end_sample: end,
        training_samples: 0,
        holdout_samples: 0,
        training_residual_before: 1.,
        training_residual_after: 1.,
        holdout_residual_before: 1.,
        holdout_residual_after: 1.,
        holdout_improvement: 0.,
        holdout_explained: 0.,
    };
    let Some(initial_fit) = initial_fit else {
        return Ok(report);
    };
    report.gain = [initial_fit.gain.re, initial_fit.gain.im];
    report.training_samples = initial_fit.count;
    report.training_residual_before = initial_fit.residual;
    let limits = [
        timing_bound,
        clock_bound,
        config.carrier_bound_hz,
        config.drift_bound_hz_per_s,
    ];
    let origin = [
        initial.start_sample,
        initial.samples_per_symbol,
        initial.carrier_hz,
        initial.carrier_drift_hz_per_s,
    ];
    let mut best_values = origin;
    let mut best_fit = initial_fit;
    let mut best_geometry = initial.clone();
    // CFO and drift are strongly correlated when frequency is represented at
    // the burst start. Vary drift around the fixed interval center while
    // preserving its instantaneous center frequency, then express it back at
    // the caller's reference. This joint direction avoids a coordinate-search
    // trap in which neither a drift-only nor a CFO-only step can improve.
    let pivot_seconds =
        ((begin + end) as f64 * 0.5 - initial.carrier_reference_sample) / f64::from(rate);
    let pivot_symbols =
        ((begin + end) as f64 * 0.5 - initial.start_sample) / initial.samples_per_symbol;
    for sweep in 0..config.iterations {
        for axis in 0..4 {
            if limits[axis] == 0. {
                continue;
            }
            let anchor = best_values;
            for factor in [-1., -0.5, 0.5, 1.] {
                let mut values = anchor;
                values[axis] = (anchor[axis] + factor * limits[axis] / 2f64.powi(sweep as i32))
                    .clamp(origin[axis] - limits[axis], origin[axis] + limits[axis]);
                if axis == 1 && timing_bound > 0. {
                    values[0] -= (values[1] - anchor[1]) * pivot_symbols;
                }
                if axis == 3 && config.carrier_bound_hz > 0. {
                    values[2] -= (values[3] - anchor[3]) * pivot_seconds;
                }
                if values[0] < 0.
                    || (values[0] - origin[0]).abs() > timing_bound + 1e-12
                    || (values[2] - origin[2]).abs() > config.carrier_bound_hz + 1e-12
                {
                    continue;
                }
                let mut g = initial.clone();
                g.start_sample = values[0];
                g.samples_per_symbol = values[1];
                g.carrier_hz = values[2];
                g.carrier_drift_hz_per_s = values[3];
                report.evaluated_hypotheses += 1;
                report.work += evaluation_work;
                if let Some(fit) = model.fit(&g)
                    && fit.residual + 1e-12 < best_fit.residual
                {
                    best_values = values;
                    best_fit = fit;
                    best_geometry = g;
                }
            }
        }
    }
    // Exactly one selected hypothesis is checked; no retries or selection on
    // held-out score, CRC outcomes, accepted frame count or fixture payload.
    let (before, original_holdout_count) = model.holdout(initial, initial_fit.gain);
    let (after, count) = model.holdout(&best_geometry, best_fit.gain);
    report.work += 2 * evaluation_work;
    report.training_residual_after = best_fit.residual;
    report.holdout_residual_before = before;
    report.holdout_residual_after = after;
    report.holdout_samples = count.min(original_holdout_count);
    report.holdout_improvement = if before > 1e-12 {
        1. - after / before
    } else {
        0.
    };
    report.holdout_explained = 1. - after;
    report.reason = if count < 32 || original_holdout_count < 32 {
        "insufficient_reliable_holdout"
    } else if best_fit.residual + 1e-12 >= initial_fit.residual {
        "no_training_improvement"
    } else if !after.is_finite() || report.holdout_explained < config.minimum_holdout_explained {
        "holdout_signal_not_explained"
    } else if report.holdout_improvement < config.minimum_holdout_improvement {
        "holdout_did_not_improve"
    } else {
        "conditional_waveform_improvement"
    };
    if report.reason == "conditional_waveform_improvement" {
        report.accepted = true;
        report.geometry = best_geometry;
        report.gain = [best_fit.gain.re, best_fit.gain.im];
    }
    Ok(report)
}

#[cfg(test)]
#[path = "tests/recovery_tracking_tests.rs"]
mod tests;
