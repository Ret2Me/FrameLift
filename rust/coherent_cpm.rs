//! Coherent binary CPM log-MAP detector for a finite, sampled frequency pulse.
//!
//! Unlike the frequency-discriminator lane, branch likelihoods use complex IQ
//! and retain the rational-index phase state. Gaussian shaping is the exact
//! discrete +/- two-symbol, unit-DC filter used by `advanced_waveform`, at an
//! INTEGER samples/symbol. It is not an exact detector for an infinite Gaussian
//! pulse, an unknown pulse, arbitrary timing, or a time-varying channel.
//!
//! Positive LLR means ONE. The Gaussian is delayed by two symbols internally,
//! producing a causal five-symbol pulse and four binary history symbols. The
//! delayed observations are mapped back without adding fictitious bits.
use num_complex::Complex64;
use serde::{Deserialize, Serialize};
use std::f64::consts::{PI, TAU};

const MAX_SYMBOLS: usize = 4096;
const MAX_METRIC_SAMPLES: usize = 160_000_000;

#[derive(Clone, Copy, Debug, Default, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum CpmBoundary {
    /// Outside amplitudes are zero, NOT binary zeros (-1 levels).
    ZeroPaddedBurst,
    /// Retain all real observations and marginalize unknown exterior symbols.
    /// Preamble fitting anchors the common phase after prehistory has retired.
    #[default]
    UnknownOutsideWindow,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CoherentCpmConfig {
    pub samples_per_symbol: usize,
    pub modulation_index_numerator: u8,
    pub modulation_index_denominator: u8,
    pub gaussian_bt: Option<f64>,
    /// Complex noise power E[|w|^2], before fitted-gain normalization.
    pub noise_variance: f64,
    pub llr_clip: f64,
    pub max_carrier_offset_radians_per_sample: f64,
    pub min_training_coherence: f64,
    pub boundary: CpmBoundary,
}

impl Default for CoherentCpmConfig {
    fn default() -> Self {
        Self {
            samples_per_symbol: 8,
            modulation_index_numerator: 1,
            modulation_index_denominator: 2,
            gaussian_bt: Some(0.5),
            noise_variance: 0.1,
            llr_clip: 100.,
            max_carrier_offset_radians_per_sample: 0.05,
            min_training_coherence: 0.25,
            boundary: CpmBoundary::UnknownOutsideWindow,
        }
    }
}

#[derive(Clone, Debug, Serialize)]
pub struct CpmDiagnostics {
    pub trellis_states: usize,
    pub phase_states: usize,
    pub pulse_symbols: usize,
    pub training_samples: usize,
    pub observed_samples: usize,
    pub carrier_offset_radians_per_sample: f64,
    pub fitted_gain_re: f64,
    pub fitted_gain_im: f64,
    pub training_coherence: f64,
    pub training_residual_noise_power: f64,
    pub boundary: CpmBoundary,
}

#[derive(Clone, Debug, Serialize)]
pub struct CoherentCpmResult {
    pub posterior_llr: Vec<f64>,
    /// Unclipped APP minus incoming prior, then clipped. Known sync bits emit
    /// zero extrinsic information; they are constraints, not decoded evidence.
    pub extrinsic_llr: Vec<f64>,
    pub hard_bits: Vec<u8>,
    pub diagnostics: CpmDiagnostics,
}

/// Immutable channel-only branch metrics. Reuse across FEC iterations so that
/// incoming priors cannot alter training, timing or the captured observations.
pub struct PreparedCpm {
    symbols: usize,
    steps: usize,
    states: usize,
    known: Vec<Option<u8>>,
    metrics: Vec<[f64; 2]>,
    transitions: Vec<[usize; 2]>,
    llr_clip: f64,
    diagnostics: CpmDiagnostics,
    initial_states: Vec<usize>,
}

struct Pulse {
    sps: usize,
    memory: usize,
    delay: usize,
    phases: usize,
    phase_increment: usize,
    h: f64,
    response: Vec<f64>,
}

fn gcd(mut a: usize, mut b: usize) -> usize {
    while b != 0 {
        (a, b) = (b, a % b);
    }
    a
}

impl Pulse {
    fn new(c: &CoherentCpmConfig) -> Self {
        let sps = c.samples_per_symbol;
        let p = usize::from(c.modulation_index_numerator);
        let q = usize::from(c.modulation_index_denominator);
        let divisor = gcd(p, 2 * q);
        let (memory, delay, mut response) = if let Some(bt) = c.gaussian_bt {
            let radius = 2 * sps;
            let taps: Vec<_> = (0..=2 * radius)
                .map(|k| {
                    let t = (k as f64 - radius as f64) / sps as f64;
                    (-2. * PI * PI * bt * bt * t * t / 2f64.ln()).exp()
                })
                .collect();
            let norm = taps.iter().sum::<f64>();
            let mut frequency = vec![0.; 5 * sps];
            for r in 0..sps {
                for (k, tap) in taps.iter().enumerate() {
                    frequency[r + k] += tap / norm / sps as f64;
                }
            }
            (4, radius, frequency)
        } else {
            (0, 0, vec![1. / sps as f64; sps])
        };
        let mut integral = 0.;
        for x in &mut response {
            integral += *x;
            *x = integral;
        }
        // Unit area is exact as a contract; suppress cumulative roundoff at
        // retirement where the trellis switches to discrete phase states.
        *response.last_mut().unwrap() = 1.;
        Self {
            sps,
            memory,
            delay,
            phases: 2 * q / divisor,
            phase_increment: p / divisor,
            h: p as f64 / q as f64,
            response,
        }
    }

    fn states(&self) -> usize {
        self.phases * (1 << self.memory)
    }

    fn transition(
        &self,
        t: usize,
        n: usize,
        state: usize,
        bit: usize,
        boundary: CpmBoundary,
    ) -> usize {
        let histories = 1 << self.memory;
        let history = state % histories;
        let mut phase = state / histories;
        if boundary == CpmBoundary::UnknownOutsideWindow
            || (t >= self.memory && t - self.memory < n)
        {
            let retiring = if self.memory == 0 {
                bit
            } else {
                (history >> (self.memory - 1)) & 1
            };
            let step = if retiring == 1 {
                self.phase_increment
            } else {
                self.phases - self.phase_increment
            };
            phase = (phase + step) % self.phases;
        }
        phase * histories + (((history << 1) | bit) & (histories - 1))
    }

    fn waveform(
        &self,
        t: usize,
        n: usize,
        state: usize,
        bit: usize,
        boundary: CpmBoundary,
    ) -> Vec<Complex64> {
        let histories = 1 << self.memory;
        let history = state % histories;
        let phase = TAU * (state / histories) as f64 / self.phases as f64;
        (0..self.sps)
            .map(|r| {
                let mut partial = 0.;
                for age in 0..=self.memory {
                    // A declared padded burst has ZERO exterior amplitude.
                    // Unknown exterior symbols are latent binary variables,
                    // not skipped observations or a fictitious transmitted -1.
                    if boundary == CpmBoundary::ZeroPaddedBurst && (age > t || t - age >= n) {
                        continue;
                    }
                    let b = if age == 0 {
                        bit
                    } else {
                        (history >> (age - 1)) & 1
                    };
                    partial += (2. * b as f64 - 1.) * self.response[age * self.sps + r];
                }
                Complex64::from_polar(1., phase + PI * self.h * partial)
            })
            .collect()
    }

    fn known_template(&self, known: &[Option<u8>], sample: usize) -> Complex64 {
        let at = sample + self.delay;
        let mut phase = 0.;
        for (k, bit) in known.iter().enumerate().take(at / self.sps + 1) {
            let Some(bit) = bit else { break };
            let age = at - k * self.sps;
            let q = self.response.get(age).copied().unwrap_or(1.);
            phase += PI * self.h * (2. * f64::from(*bit) - 1.) * q;
        }
        Complex64::from_polar(1., phase)
    }
}

fn validate(iq: &[Complex64], known: &[Option<u8>], c: &CoherentCpmConfig) -> Result<(), String> {
    if !(2..=128).contains(&c.samples_per_symbol)
        || !(1..=16).contains(&c.modulation_index_numerator)
        || !(1..=16).contains(&c.modulation_index_denominator)
        || c.modulation_index_numerator > 2 * c.modulation_index_denominator
        || c.gaussian_bt
            .is_some_and(|b| !b.is_finite() || !(0.2..=1.).contains(&b))
        || !c.noise_variance.is_finite()
        || !(1e-10..=1e10).contains(&c.noise_variance)
        || !c.llr_clip.is_finite()
        || !(1. ..=100.).contains(&c.llr_clip)
        || !c.max_carrier_offset_radians_per_sample.is_finite()
        || !(0. ..=0.2).contains(&c.max_carrier_offset_radians_per_sample)
        || !c.min_training_coherence.is_finite()
        || !(0. ..=1.).contains(&c.min_training_coherence)
        || !(16..=MAX_SYMBOLS).contains(&known.len())
        || iq.len() != known.len() * c.samples_per_symbol
        || known.iter().flatten().any(|b| *b > 1)
        || iq
            .iter()
            .any(|x| !x.re.is_finite() || !x.im.is_finite() || x.norm_sqr() > 1e12)
    {
        return Err("coherent CPM requires bounded finite IQ, 16..4096 symbols, integer 2..128 SPS, rational h<=2 (numerator/denominator 1..16), and valid likelihood settings".into());
    }
    if known.iter().take_while(|b| b.is_some()).count() < 16 {
        return Err("coherent CPM needs a known contiguous preamble of at least 16 bits".into());
    }
    Ok(())
}

fn fit_training(
    iq: &[Complex64],
    known: &[Option<u8>],
    c: &CoherentCpmConfig,
    pulse: &Pulse,
) -> Result<(f64, Complex64, f64, f64, usize), String> {
    let prefix = known.iter().take_while(|b| b.is_some()).count();
    let begin = pulse.delay;
    let end = ((prefix * pulse.sps).saturating_sub(pulse.delay)).min(begin + 1024);
    let observations: Vec<_> = (begin..end)
        .map(|i| (i, iq[i] * pulse.known_template(known, i).conj()))
        .collect();
    let energy = observations.iter().map(|(_, z)| z.norm_sqr()).sum::<f64>();
    if energy <= 1e-12 || !energy.is_finite() {
        return Err("coherent CPM training has no usable energy".into());
    }
    let correlation = |omega: f64| -> Complex64 {
        observations
            .iter()
            .map(|(i, z)| *z * Complex64::from_polar(1., -omega * *i as f64))
            .sum()
    };
    let bound = c.max_carrier_offset_radians_per_sample;
    let spacing = 2. * bound / 256.;
    let mut best = 0.;
    let mut score = correlation(0.).norm_sqr();
    for k in 0..=256 {
        let omega = -bound + k as f64 * spacing;
        let candidate = correlation(omega).norm_sqr();
        if candidate > score {
            best = omega;
            score = candidate;
        }
    }
    // A bounded refinement cannot leave the configured search interval.
    let mut left = (best - spacing).max(-bound);
    let mut right = (best + spacing).min(bound);
    for _ in 0..36 {
        let a = left + (right - left) / 3.;
        let b = right - (right - left) / 3.;
        if correlation(a).norm_sqr() >= correlation(b).norm_sqr() {
            right = b;
        } else {
            left = a;
        }
    }
    let omega = (left + right) / 2.;
    let gain = correlation(omega) / observations.len() as f64;
    let coherence = (gain.norm_sqr() * observations.len() as f64 / energy).clamp(0., 1.);
    if gain.norm_sqr() < 1e-12 || coherence < c.min_training_coherence {
        return Err("coherent CPM preamble coherence is below the configured gate".into());
    }
    let residual = observations
        .iter()
        .map(|(i, z)| (*z - gain * Complex64::from_polar(1., omega * *i as f64)).norm_sqr())
        .sum::<f64>()
        / observations.len() as f64;
    Ok((omega, gain, coherence, residual, observations.len()))
}

/// Acquire gain/residual carrier from known preamble ONLY and cache likelihoods.
/// Timing, pulse shape, rational modulation index and noise power are explicit
/// assumptions; no reference payload, CRC or FEC decision enters preparation.
pub fn prepare(
    iq: &[Complex64],
    known: &[Option<u8>],
    c: &CoherentCpmConfig,
) -> Result<PreparedCpm, String> {
    prepare_inner(iq, known, c, None)
}

/// Fit pilot gain/carrier once, estimate noise from that same pilot, and build
/// likelihoods once. This is numerically identical to `prepare`, reading its
/// residual, and preparing again with `max(residual, variance_floor)`.
/// No payload decisions or reference bits enter the noise estimate.
pub fn prepare_with_training_noise(
    iq: &[Complex64],
    known: &[Option<u8>],
    c: &CoherentCpmConfig,
    variance_floor: f64,
) -> Result<PreparedCpm, String> {
    if !variance_floor.is_finite() || variance_floor <= 0. {
        return Err("coherent CPM variance floor must be finite and positive".into());
    }
    prepare_inner(iq, known, c, Some(variance_floor))
}

fn prepare_inner(
    iq: &[Complex64],
    known: &[Option<u8>],
    c: &CoherentCpmConfig,
    variance_floor: Option<f64>,
) -> Result<PreparedCpm, String> {
    validate(iq, known, c)?;
    let pulse = Pulse::new(c);
    let states = pulse.states();
    let n = known.len();
    let steps = n + pulse.delay / pulse.sps;
    if steps * states * 2 * pulse.sps > MAX_METRIC_SAMPLES {
        return Err(
            "coherent CPM branch-sample work exceeds 160 million; reduce SPS or window".into(),
        );
    }
    let (omega, gain, coherence, residual, training_samples) = fit_training(iq, known, c, &pulse)?;
    let corrected: Vec<_> = iq
        .iter()
        .enumerate()
        .map(|(i, z)| *z * Complex64::from_polar(1., -omega * i as f64) / gain)
        .collect();
    let noise_variance = variance_floor.map_or(c.noise_variance, |floor| residual.max(floor));
    let inverse_variance = gain.norm_sqr() / noise_variance;
    let first = 0;
    let last = iq.len();
    let normal: Vec<_> = (0..states)
        .map(|s| {
            [
                pulse.waveform(pulse.memory, n, s, 0, c.boundary),
                pulse.waveform(pulse.memory, n, s, 1, c.boundary),
            ]
        })
        .collect();
    let mut metrics = Vec::with_capacity(steps * states);
    let mut transitions = Vec::with_capacity(steps * states);
    for t in 0..steps {
        for (state, normal_branches) in normal.iter().enumerate() {
            let mut row = [f64::NEG_INFINITY; 2];
            let mut next = [0; 2];
            for bit in 0..2 {
                next[bit] = pulse.transition(t, n, state, bit, c.boundary);
                if (t >= n && bit == 1 && c.boundary == CpmBoundary::ZeroPaddedBurst)
                    || (t < n && known[t].is_some_and(|b| usize::from(b) != bit))
                {
                    continue;
                }
                let boundary_waveform;
                let waveform = if t < pulse.memory || t >= n {
                    boundary_waveform = pulse.waveform(t, n, state, bit, c.boundary);
                    &boundary_waveform
                } else {
                    &normal_branches[bit]
                };
                row[bit] = 0.;
                for (r, mu) in waveform.iter().enumerate() {
                    let causal = t * pulse.sps + r;
                    if causal < pulse.delay {
                        continue;
                    }
                    let sample = causal - pulse.delay;
                    if sample < first || sample >= last {
                        continue;
                    }
                    // The common |y|²+|mu|² terms cancel between paths.
                    row[bit] += 2. * (corrected[sample] * mu.conj()).re * inverse_variance;
                }
            }
            metrics.push(row);
            transitions.push(next);
        }
        let maximum = metrics[t * states..(t + 1) * states]
            .iter()
            .flat_map(|v| v.iter())
            .copied()
            .fold(f64::NEG_INFINITY, f64::max);
        for row in &mut metrics[t * states..(t + 1) * states] {
            for v in row {
                *v -= maximum;
            }
        }
    }
    let histories = 1_usize << pulse.memory;
    let initial_states = if c.boundary == CpmBoundary::UnknownOutsideWindow {
        // Training uses a zero-prehistory reference only AFTER its memory has
        // retired. For each latent history, cancel its completed phase here;
        // all histories then share precisely that same fitted phase anchor.
        // Initial histories are equiprobable; no exterior observations exist.
        (0..histories)
            .map(|history| {
                let sum = 2 * history.count_ones() as isize - pulse.memory as isize;
                let phase = (-(pulse.phase_increment as isize) * sum)
                    .rem_euclid(pulse.phases as isize) as usize;
                phase * histories + history
            })
            .collect()
    } else {
        vec![0]
    };
    Ok(PreparedCpm {
        symbols: n,
        steps,
        states,
        known: known.to_vec(),
        metrics,
        transitions,
        llr_clip: c.llr_clip,
        initial_states,
        diagnostics: CpmDiagnostics {
            trellis_states: states,
            phase_states: pulse.phases,
            pulse_symbols: pulse.memory + 1,
            training_samples,
            observed_samples: last - first,
            carrier_offset_radians_per_sample: omega,
            fitted_gain_re: gain.re,
            fitted_gain_im: gain.im,
            training_coherence: coherence,
            training_residual_noise_power: residual,
            boundary: c.boundary,
        },
    })
}

#[inline]
fn log_add(a: f64, b: f64) -> f64 {
    if a == f64::NEG_INFINITY {
        return b;
    }
    if b == f64::NEG_INFINITY {
        return a;
    }
    a.max(b) + (-(a - b).abs()).exp().ln_1p()
}

fn normalize(values: &mut [f64]) -> Result<(), String> {
    let maximum = values.iter().copied().fold(f64::NEG_INFINITY, f64::max);
    if !maximum.is_finite() {
        return Err("coherent CPM trellis has no finite path".into());
    }
    for value in values {
        *value -= maximum;
    }
    Ok(())
}

impl PreparedCpm {
    /// Training receipt without running the forward/backward recursion.
    pub fn diagnostics(&self) -> &CpmDiagnostics {
        &self.diagnostics
    }

    /// Sum-product over every reachable phase/history state under the fixed
    /// finite-pulse AWGN model. No max-log or reduced-state approximation.
    pub fn detect(&self, prior_llr: &[f64]) -> Result<CoherentCpmResult, String> {
        if (!prior_llr.is_empty() && prior_llr.len() != self.symbols)
            || prior_llr.iter().any(|p| !p.is_finite() || p.abs() > 100.)
        {
            return Err(
                "coherent CPM priors must be empty or length-matched finite LLRs in [-100,100]"
                    .into(),
            );
        }
        let prior = |t: usize| prior_llr.get(t).copied().unwrap_or(0.);
        let mut alpha = vec![f64::NEG_INFINITY; (self.steps + 1) * self.states];
        for initial in &self.initial_states {
            alpha[*initial] = 0.;
        }
        for t in 0..self.steps {
            let (past, future) = alpha.split_at_mut((t + 1) * self.states);
            let current = &past[t * self.states..];
            let next = &mut future[..self.states];
            for (s, a) in current.iter().enumerate() {
                let index = t * self.states + s;
                for b in 0..2 {
                    let metric = self.metrics[index][b] + (b as f64 - 0.5) * prior(t);
                    let destination = self.transitions[index][b];
                    next[destination] = log_add(next[destination], *a + metric);
                }
            }
            normalize(next)?;
        }
        let mut posterior_llr = vec![0.; self.symbols];
        let mut extrinsic_llr = vec![0.; self.symbols];
        let mut beta = vec![0.; self.states];
        let mut previous = vec![f64::NEG_INFINITY; self.states];
        for t in (0..self.steps).rev() {
            previous.fill(f64::NEG_INFINITY);
            let mut marginal = [f64::NEG_INFINITY; 2];
            for (s, previous_value) in previous.iter_mut().enumerate() {
                let index = t * self.states + s;
                for (b, marginal_value) in marginal.iter_mut().enumerate() {
                    let metric = self.metrics[index][b] + (b as f64 - 0.5) * prior(t);
                    let value = metric + beta[self.transitions[index][b]];
                    *previous_value = log_add(*previous_value, value);
                    *marginal_value = log_add(*marginal_value, alpha[index] + value);
                }
            }
            if t < self.symbols {
                if let Some(bit) = self.known[t] {
                    posterior_llr[t] = (2. * f64::from(bit) - 1.) * self.llr_clip;
                } else {
                    let app = marginal[1] - marginal[0];
                    if !app.is_finite() {
                        return Err(
                            "coherent CPM produced a non-finite unknown-bit marginal".into()
                        );
                    }
                    posterior_llr[t] = app.clamp(-self.llr_clip, self.llr_clip);
                    extrinsic_llr[t] = (app - prior(t)).clamp(-self.llr_clip, self.llr_clip);
                }
            }
            normalize(&mut previous)?;
            std::mem::swap(&mut beta, &mut previous);
        }
        let hard_bits = posterior_llr.iter().map(|l| u8::from(*l > 0.)).collect();
        Ok(CoherentCpmResult {
            posterior_llr,
            extrinsic_llr,
            hard_bits,
            diagnostics: self.diagnostics.clone(),
        })
    }
}

pub fn decode(
    iq: &[Complex64],
    known: &[Option<u8>],
    prior_llr: &[f64],
    config: &CoherentCpmConfig,
) -> Result<CoherentCpmResult, String> {
    prepare(iq, known, config)?.detect(prior_llr)
}

#[cfg(test)]
#[path = "tests/coherent_cpm_tests.rs"]
mod tests;
