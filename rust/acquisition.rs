//! Bounded marker acquisition from received samples alone. A soft gate admits
//! hypotheses, never frames: downstream received CRC/FEC validation is unchanged.
use num_complex::Complex64;
use serde::{Deserialize, Serialize};

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Config {
    /// Centered, energy-normalized correlation with the configured marker.
    pub minimum_correlation: f64,
    /// A broad signal-only prefilter, independent of payload/FEC outcomes.
    pub maximum_hard_errors: usize,
    /// Additional physical candidates; existing hard candidates do not count.
    pub maximum_candidates: usize,
    /// Deterministic work proxy: one per window and 64 per evaluated symbol.
    /// This bounds search, not elapsed time or exact instruction count.
    pub maximum_work: u64,
}

impl Config {
    pub fn validate(&self, marker_bits: usize) -> Result<(), String> {
        if !(32..=128).contains(&marker_bits)
            || !self.minimum_correlation.is_finite()
            || !(0.5..=1.).contains(&self.minimum_correlation)
            || self.maximum_hard_errors > marker_bits / 3
            || !(1..=128).contains(&self.maximum_candidates)
            || !(1..=200_000_000).contains(&self.maximum_work)
        {
            return Err("invalid soft acquisition correlation, error or resource limits".into());
        }
        Ok(())
    }
}

#[derive(Clone, Debug, Serialize)]
pub struct Score {
    pub normalized_correlation: f64,
    pub hard_errors: usize,
}

#[derive(Clone, Debug, Serialize)]
pub struct Selected {
    pub start_sample: f64,
    pub carrier_hz: f64,
    pub score: Score,
}

#[derive(Clone, Debug, Serialize)]
pub struct Receipt {
    pub round: usize,
    pub windows_visited: usize,
    pub correlations_evaluated: usize,
    pub gate_passes: usize,
    pub raw_candidates: usize,
    pub consumed_work: u64,
    pub selected: Vec<Selected>,
}

pub(crate) struct Search<'a> {
    config: &'a Config,
    marker: Vec<f64>,
    marker_energy: f64,
    pub receipt: Receipt,
}

impl<'a> Search<'a> {
    pub(crate) fn new(config: &'a Config, bits: &[u8], round: usize) -> Result<Self, String> {
        config.validate(bits.len())?;
        if bits.iter().any(|b| *b > 1) {
            return Err("soft acquisition marker must be binary".into());
        }
        let mean = bits.iter().map(|b| 2. * f64::from(*b) - 1.).sum::<f64>() / bits.len() as f64;
        let marker: Vec<_> = bits
            .iter()
            .map(|b| 2. * f64::from(*b) - 1. - mean)
            .collect();
        let marker_energy = marker.iter().map(|x| x * x).sum::<f64>();
        if marker_energy <= 1e-12 {
            return Err("soft acquisition marker needs both binary levels".into());
        }
        Ok(Self {
            config,
            marker,
            marker_energy,
            receipt: Receipt {
                round,
                windows_visited: 0,
                correlations_evaluated: 0,
                gate_passes: 0,
                raw_candidates: 0,
                consumed_work: 0,
                selected: vec![],
            },
        })
    }

    fn charge(&mut self, cost: u64) -> Result<(), String> {
        self.receipt.consumed_work = self
            .receipt
            .consumed_work
            .checked_add(cost)
            .ok_or("soft acquisition work overflow")?;
        if self.receipt.consumed_work > self.config.maximum_work {
            return Err("soft acquisition work budget exceeded".into());
        }
        Ok(())
    }

    fn admission(&mut self, count: usize, errors: usize) -> Result<bool, String> {
        if count != self.marker.len() {
            return Err("soft acquisition marker dimension mismatch".into());
        }
        self.charge(1)?;
        self.receipt.windows_visited += 1;
        if errors > self.config.maximum_hard_errors {
            return Ok(false);
        }
        self.charge(count as u64 * 64)?;
        self.receipt.correlations_evaluated += 1;
        Ok(true)
    }

    fn finish_score(&mut self, value: f64, errors: usize) -> Option<Score> {
        if value.is_finite() && value >= self.config.minimum_correlation {
            self.receipt.gate_passes += 1;
            Some(Score {
                normalized_correlation: value.clamp(-1., 1.),
                hard_errors: errors,
            })
        } else {
            None
        }
    }

    pub(crate) fn real(&mut self, values: &[f64], errors: usize) -> Result<Option<Score>, String> {
        if !self.admission(values.len(), errors)? {
            return Ok(None);
        }
        let mean = values.iter().sum::<f64>() / values.len() as f64;
        let (mut dot, mut energy) = (0., 0.);
        for (value, symbol) in values.iter().zip(&self.marker) {
            let centered = value - mean;
            dot += centered * symbol;
            energy += centered * centered;
        }
        if !energy.is_finite() || energy <= 1e-24 {
            return Ok(None);
        }
        Ok(self.finish_score(dot / (energy * self.marker_energy).sqrt(), errors))
    }

    /// Legacy BPSK has unresolved constant phase, so use magnitude correlation;
    /// its unchanged pilot estimator subsequently resolves the received phase.
    pub(crate) fn complex(
        &mut self,
        values: &[Complex64],
        errors: usize,
    ) -> Result<Option<Score>, String> {
        if !self.admission(values.len(), errors)? {
            return Ok(None);
        }
        let mean = values.iter().copied().sum::<Complex64>() / values.len() as f64;
        let (mut dot, mut energy) = (Complex64::new(0., 0.), 0.);
        for (value, symbol) in values.iter().zip(&self.marker) {
            let centered = value - mean;
            dot += centered * symbol;
            energy += centered.norm_sqr();
        }
        if !energy.is_finite() || energy <= 1e-24 {
            return Ok(None);
        }
        Ok(self.finish_score(dot.norm() / (energy * self.marker_energy).sqrt(), errors))
    }

    pub(crate) fn admit_candidate(&mut self) -> Result<(), String> {
        self.receipt.raw_candidates += 1;
        if self.receipt.raw_candidates > self.config.maximum_candidates * 256 {
            return Err("soft acquisition raw candidate budget exceeded".into());
        }
        Ok(())
    }

    pub(crate) fn select(
        &mut self,
        start_sample: f64,
        carrier_hz: f64,
        score: Score,
    ) -> Result<(), String> {
        if self.receipt.selected.len() == self.config.maximum_candidates {
            return Err("soft acquisition selected candidate budget exceeded".into());
        }
        self.receipt.selected.push(Selected {
            start_sample,
            carrier_hz,
            score,
        });
        Ok(())
    }
}

#[cfg(test)]
#[path = "tests/acquisition_tests.rs"]
mod tests;
