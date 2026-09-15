//! Experimental Mueller-Muller timing recovery for real pre-clock soft samples.
//! This is an established timing detector, not a novel algorithm or a bit-exact
//! GNU Radio port. Its interpolation is explicitly linear or cubic Lagrange.
//! No packet bytes, CRC results, or mission identifiers affect clock decisions.

#[derive(Clone, Copy, Debug)]
pub enum Interpolation {
    Linear,
    Cubic,
}

#[derive(Clone, Copy, Debug)]
pub struct Config {
    pub samples_per_symbol: f64,
    pub initial_phase: f64,
    pub gain_omega: f64,
    pub gain_mu: f64,
    pub relative_limit: f64,
    pub input_gain: f64,
    pub interpolation: Interpolation,
}

fn interpolate(values: &[f64], i: usize, t: f64, mode: Interpolation) -> f64 {
    match mode {
        Interpolation::Linear => values[i] * (1.0 - t) + values[i + 1] * t,
        Interpolation::Cubic => {
            -values[i - 1] * t * (t - 1.0) * (t - 2.0) / 6.0
                + values[i] * (t + 1.0) * (t - 1.0) * (t - 2.0) / 2.0
                - values[i + 1] * (t + 1.0) * t * (t - 2.0) / 2.0
                + values[i + 2] * (t + 1.0) * t * (t - 1.0) / 6.0
        }
    }
}

pub fn recover(values: &[f64], c: Config) -> Result<Vec<f64>, String> {
    if values.len() < 8 || values.len() > 134_217_728 || values.iter().any(|x| !x.is_finite()) {
        return Err("clock requires8..134217728 finite soft samples".into());
    }
    if !c.samples_per_symbol.is_finite()
        || !(1.1..=64.0).contains(&c.samples_per_symbol)
        || !c.initial_phase.is_finite()
        || !(0.0..1.0).contains(&c.initial_phase)
        || !c.gain_omega.is_finite()
        || !(0.0..=0.2).contains(&c.gain_omega)
        || !c.gain_mu.is_finite()
        || !(0.0..=0.2).contains(&c.gain_mu)
        || !c.relative_limit.is_finite()
        || !(0.0..=0.1).contains(&c.relative_limit)
        || !c.input_gain.is_finite()
        || !(0.01..=100.0).contains(&c.input_gain)
    {
        return Err("invalid bounded clock configuration".into());
    }
    let mut position = 1.0 + c.initial_phase;
    let mut omega = c.samples_per_symbol;
    let mut previous = 0.0;
    let mut result = Vec::with_capacity((values.len() as f64 / c.samples_per_symbol) as usize + 8);
    let sign = |x: f64| if x >= 0.0 { 1.0 } else { -1.0 };
    while position + 2.0 < values.len() as f64 {
        let i = position.floor() as usize;
        let sample = interpolate(values, i, position - i as f64, c.interpolation) * c.input_gain;
        let error = sign(previous) * sample - sign(sample) * previous;
        if !sample.is_finite() || !error.is_finite() {
            return Err("clock arithmetic overflow".into());
        }
        omega = (omega + c.gain_omega * error).clamp(
            c.samples_per_symbol * (1.0 - c.relative_limit),
            c.samples_per_symbol * (1.0 + c.relative_limit),
        );
        let step = omega + c.gain_mu * error;
        if !step.is_finite() || step < 0.25 || step > 128.0 {
            return Err("clock lost bounded forward progress".into());
        }
        result.push(sample);
        previous = sample;
        position += step;
        if result.len() > values.len() * 4 {
            return Err("clock output bound exceeded".into());
        }
    }
    Ok(result)
}

#[cfg(test)]
mod tests {
    use super::*;
    fn config() -> Config {
        Config {
            samples_per_symbol: 2.0,
            initial_phase: 0.5,
            gain_omega: 0.0,
            gain_mu: 0.0,
            relative_limit: 0.01,
            input_gain: 1.0,
            interpolation: Interpolation::Linear,
        }
    }
    #[test]
    fn zero_gains_match_uniform_fractional_sampling() {
        let input: Vec<_> = (0..64).map(|x| x as f64).collect();
        let output = recover(&input, config()).unwrap();
        for (i, x) in output.iter().enumerate() {
            assert_eq!(*x, 1.5 + 2.0 * i as f64);
        }
    }
    #[test]
    fn cubic_reproduces_cubic_polynomial() {
        let input: Vec<_> = (0..8).map(|x| (x as f64).powi(3)).collect();
        for t in [0.0, 0.1, 0.5, 0.9] {
            assert!(
                (interpolate(&input, 3, t, Interpolation::Cubic) - (3.0 + t).powi(3)).abs() < 1e-12
            );
        }
    }
    #[test]
    fn finite_negative_controls_remain_finite_and_bounded() {
        let c = Config {
            gain_omega: 0.06283185307179587,
            gain_mu: 0.0625,
            ..config()
        };
        let output = recover(&vec![0.0; 1000], c).unwrap();
        assert!(output.len() < 1000);
        assert!(output.iter().all(|x| *x == 0.0));
    }
    #[test]
    fn rejects_nonfinite_or_invalid_parameters() {
        assert!(recover(&[f64::NAN; 20], config()).is_err());
        for sps in [0.0, 1.0, f64::NAN, 100.0] {
            assert!(
                recover(
                    &[0.0; 20],
                    Config {
                        samples_per_symbol: sps,
                        ..config()
                    }
                )
                .is_err()
            );
        }
        assert!(
            recover(
                &[0.0; 20],
                Config {
                    initial_phase: 1.0,
                    ..config()
                }
            )
            .is_err()
        );
    }
    #[test]
    fn amplitude_overflow_is_not_a_valid_empty_decode() {
        assert!(
            recover(
                &[f64::MAX; 20],
                Config {
                    input_gain: 100.0,
                    ..config()
                }
            )
            .is_err()
        );
    }
}
