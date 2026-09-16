use super::*;

fn data(n: usize) -> Vec<u8> {
    let mut state = 0x7456_1ab3_52d0_49cf_u64;
    (0..n)
        .map(|_| {
            state ^= state << 13;
            state ^= state >> 7;
            state ^= state << 17;
            (state & 1) as u8
        })
        .collect()
}

/// Independent transmitter: expand NRZ, convolve the centered Gaussian and
/// integrate instantaneous frequency. No receiver pulse/trellis helper calls.
fn transmit(bits: &[u8], c: &CoherentCpmConfig, gain: Complex64, omega: f64) -> Vec<Complex64> {
    let sps = c.samples_per_symbol;
    let levels: Vec<f64> = bits
        .iter()
        .flat_map(|b| std::iter::repeat_n(2. * f64::from(*b) - 1., sps))
        .collect();
    let frequency = if let Some(bt) = c.gaussian_bt {
        let radius = 2 * sps as isize;
        let taps: Vec<_> = (-radius..=radius)
            .map(|k| (-2. * PI * PI * bt * bt * (k as f64 / sps as f64).powi(2) / 2f64.ln()).exp())
            .collect();
        let sum = taps.iter().sum::<f64>();
        (0..levels.len())
            .map(|i| {
                taps.iter()
                    .enumerate()
                    .filter_map(|(k, tap)| {
                        let j = i as isize + k as isize - radius;
                        (j >= 0 && j < levels.len() as isize)
                            .then(|| tap * levels[j as usize] / sum)
                    })
                    .sum()
            })
            .collect()
    } else {
        levels
    };
    let mut phase = 0.;
    let h = f64::from(c.modulation_index_numerator) / f64::from(c.modulation_index_denominator);
    frequency
        .into_iter()
        .enumerate()
        .map(|(i, level)| {
            phase += PI * h * level / sps as f64;
            gain * Complex64::from_polar(1., phase + omega * i as f64)
        })
        .collect()
}

fn known(bits: &[u8], prefix: usize) -> Vec<Option<u8>> {
    bits.iter()
        .enumerate()
        .map(|(i, b)| (i < prefix).then_some(*b))
        .collect()
}

fn add_noise(iq: &mut [Complex64], sigma: f64, seed: u64) {
    let mut state = seed;
    let mut uniform = || {
        state ^= state << 13;
        state ^= state >> 7;
        state ^= state << 17;
        ((state >> 11) as f64 + 0.5) / (1_u64 << 53) as f64
    };
    for z in iq {
        let radius = (-2. * uniform().ln()).sqrt() * sigma;
        let angle = TAU * uniform();
        *z += Complex64::from_polar(radius, angle);
    }
}

#[test]
fn coherent_cpm_independent_waveforms_preserve_phase_for_rational_indices() {
    let bits = data(96);
    for (numerator, denominator, bt) in [
        (1, 2, Some(0.3)),
        (2, 3, Some(0.5)),
        (3, 4, Some(1.)),
        (1, 2, None),
    ] {
        let c = CoherentCpmConfig {
            modulation_index_numerator: numerator,
            modulation_index_denominator: denominator,
            gaussian_bt: bt,
            boundary: CpmBoundary::ZeroPaddedBurst,
            noise_variance: 0.03,
            ..Default::default()
        };
        let mut iq = transmit(&bits, &c, Complex64::from_polar(0.8, 1.3), 0.0087);
        add_noise(&mut iq, 0.035, 0x1245);
        let out = decode(&iq, &known(&bits, 32), &[], &c).unwrap();
        assert_eq!(
            out.hard_bits, bits,
            "h={numerator}/{denominator}, BT={bt:?}"
        );
        assert!((out.diagnostics.carrier_offset_radians_per_sample - 0.0087).abs() < 0.0003);
        assert!(out.diagnostics.training_coherence > 0.98);
        assert!(
            out.posterior_llr
                .iter()
                .all(|x| x.is_finite() && x.abs() <= 100.)
        );
    }
}

#[test]
fn unknown_exterior_symbols_are_not_assumed_to_be_zero_bits() {
    let all_bits = data(160);
    let c = CoherentCpmConfig {
        noise_variance: 0.01,
        ..Default::default()
    };
    let all_iq = transmit(&all_bits, &c, Complex64::from_polar(1.4, -2.2), -0.012);
    let sps = c.samples_per_symbol;
    let bits = &all_bits[19..139];
    let iq = &all_iq[19 * sps..139 * sps];
    let out = decode(iq, &known(bits, 32), &[], &c).unwrap();
    assert_eq!(out.hard_bits, bits);
    assert_eq!(out.diagnostics.observed_samples, iq.len());
    assert!(out.diagnostics.training_coherence > 0.999999);
}

#[test]
fn channel_only_metrics_are_reusable_without_prior_feedback_contamination() {
    let bits = data(80);
    let c = CoherentCpmConfig {
        noise_variance: 2.,
        ..Default::default()
    };
    let mut iq = transmit(&bits, &c, Complex64::new(1., 0.), 0.);
    add_noise(&mut iq, 0.4, 0xbeef);
    let prepared = prepare(&iq, &known(&bits, 32), &c).unwrap();
    let before = prepared.detect(&[]).unwrap();
    let priors: Vec<_> = bits
        .iter()
        .map(|b| (2. * f64::from(*b) - 1.) * 0.7)
        .collect();
    let with_prior = prepared.detect(&priors).unwrap();
    let after = prepared.detect(&[]).unwrap();
    assert_eq!(before.posterior_llr, after.posterior_llr);
    assert_eq!(before.extrinsic_llr, after.extrinsic_llr);
    for (i, prior) in priors.iter().enumerate().skip(32) {
        if with_prior.posterior_llr[i].abs() < c.llr_clip - 1.
            && with_prior.extrinsic_llr[i].abs() < c.llr_clip - 1.
        {
            assert!(
                (with_prior.posterior_llr[i] - prior - with_prior.extrinsic_llr[i]).abs() < 1e-10
            );
        }
    }
}

#[test]
fn estimated_noise_preparation_exactly_reuses_training_and_channel_metrics() {
    let bits = data(96);
    for boundary in [
        CpmBoundary::ZeroPaddedBurst,
        CpmBoundary::UnknownOutsideWindow,
    ] {
        for bt in [None, Some(0.3), Some(0.5)] {
            let c = CoherentCpmConfig {
                boundary,
                gaussian_bt: bt,
                ..Default::default()
            };
            let mut iq = transmit(&bits, &c, Complex64::from_polar(0.8, 0.7), 0.009);
            add_noise(&mut iq, 0.35, 0x9912);
            let known = known(&bits, 32);
            let initial = prepare(&iq, &known, &c).unwrap();
            for floor in [1e-6, 10.] {
                let updated = CoherentCpmConfig {
                    noise_variance: initial.diagnostics.training_residual_noise_power.max(floor),
                    ..c.clone()
                };
                let expected = prepare(&iq, &known, &updated).unwrap();
                let actual = prepare_with_training_noise(&iq, &known, &c, floor).unwrap();
                assert_eq!(actual.metrics, expected.metrics);
                assert_eq!(actual.transitions, expected.transitions);
                let prior = vec![0.17; bits.len()];
                assert_eq!(
                    serde_json::to_value(actual.detect(&prior).unwrap()).unwrap(),
                    serde_json::to_value(expected.detect(&prior).unwrap()).unwrap()
                );
            }
            for floor in [0., -1., f64::NAN, f64::INFINITY] {
                assert!(prepare_with_training_noise(&iq, &known, &c, floor).is_err());
            }
        }
    }
}

#[test]
fn bcjr_marginals_match_exhaustive_independent_waveform_enumeration() {
    let bits = data(24);
    let c = CoherentCpmConfig {
        boundary: CpmBoundary::ZeroPaddedBurst,
        max_carrier_offset_radians_per_sample: 0.,
        noise_variance: 4.,
        ..Default::default()
    };
    let mut training = known(&bits, 20);
    training[20..].fill(None);
    let gain = Complex64::from_polar(0.9, 0.73);
    let mut iq = transmit(&bits, &c, gain, 0.);
    // Training is unaffected: the Gaussian's two-symbol anticipation boundary
    // lies before these perturbations; the fit is exact up to rounding.
    add_noise(&mut iq[20 * c.samples_per_symbol..], 0.3, 0x453);
    let prior = vec![0.17; bits.len()];
    let out = decode(&iq, &training, &prior, &c).unwrap();
    let mut marginal = [[f64::NEG_INFINITY; 2]; 4];
    for pattern in 0..16 {
        let mut hypothesis = bits.clone();
        for i in 0..4 {
            hypothesis[20 + i] = ((pattern >> i) & 1) as u8;
        }
        let waveform = transmit(&hypothesis, &c, gain, 0.);
        let likelihood = iq
            .iter()
            .zip(waveform)
            .map(|(y, mu)| -(*y - mu).norm_sqr() / c.noise_variance)
            .sum::<f64>()
            + hypothesis
                .iter()
                .skip(20)
                .map(|b| (f64::from(*b) - 0.5) * 0.17)
                .sum::<f64>();
        for (i, row) in marginal.iter_mut().enumerate() {
            let b = usize::from(hypothesis[20 + i]);
            row[b] = log_add(row[b], likelihood);
        }
    }
    for (i, row) in marginal.iter().enumerate() {
        assert!(
            (out.posterior_llr[20 + i] - (row[1] - row[0])).abs() < 1e-8,
            "bit {i}: trellis={} brute={}",
            out.posterior_llr[20 + i],
            row[1] - row[0]
        );
    }
}

#[test]
fn unknown_boundaries_match_exhaustive_payload_and_exterior_marginalization() {
    let bits = data(28);
    let c = CoherentCpmConfig {
        boundary: CpmBoundary::UnknownOutsideWindow,
        max_carrier_offset_radians_per_sample: 0.,
        noise_variance: 4.,
        ..Default::default()
    };
    let sps = c.samples_per_symbol;
    let reference = transmit(&bits, &c, Complex64::from_polar(0.9, 0.73), 0.);
    let reference = &reference[2 * sps..26 * sps];
    let mut received = reference.to_vec();
    add_noise(&mut received[20 * sps..], 0.3, 0x7254);
    let observed_bits = &bits[2..26];
    let priors = vec![0.17; 24];
    let out = decode(&received, &known(observed_bits, 20), &priors, &c).unwrap();
    let mut marginal = [[f64::NEG_INFINITY; 2]; 4];
    // Two unknown symbols before and after the 24-symbol observed window,
    // and four unknown payload bits. Every one of the 256 paths is scored.
    for pattern in 0..256 {
        let mut hypothesis = bits.clone();
        for (position, index) in [0, 1, 22, 23, 24, 25, 26, 27].into_iter().enumerate() {
            hypothesis[index] = ((pattern >> position) & 1) as u8;
        }
        let transmitted = transmit(&hypothesis, &c, Complex64::from_polar(0.9, 0.73), 0.);
        let waveform = &transmitted[2 * sps..26 * sps];
        // Match the same training-only phase reference. Exterior histories
        // must not be penalized for an arbitrary phase before this window.
        let alignment = reference[8 * sps] / waveform[8 * sps];
        let likelihood = received
            .iter()
            .zip(waveform)
            .map(|(y, mu)| -(*y - *mu * alignment).norm_sqr() / c.noise_variance)
            .sum::<f64>()
            + hypothesis[22..26]
                .iter()
                .map(|b| (f64::from(*b) - 0.5) * 0.17)
                .sum::<f64>();
        for (i, row) in marginal.iter_mut().enumerate() {
            let bit = usize::from(hypothesis[22 + i]);
            row[bit] = log_add(row[bit], likelihood);
        }
    }
    for (i, row) in marginal.iter().enumerate() {
        assert!(
            (out.posterior_llr[20 + i] - (row[1] - row[0])).abs() < 1e-8,
            "bit{i}: trellis={} enumeration={}",
            out.posterior_llr[20 + i],
            row[1] - row[0]
        );
    }
    assert_eq!(out.diagnostics.observed_samples, received.len());
}

#[test]
fn rejects_invalid_profiles_missing_training_silence_and_invalid_priors() {
    let bits = data(32);
    let c = CoherentCpmConfig::default();
    let iq = transmit(&bits, &c, Complex64::new(1., 0.), 0.);
    let training = known(&bits, 16);
    for bad in [
        CoherentCpmConfig {
            samples_per_symbol: 0,
            ..c.clone()
        },
        CoherentCpmConfig {
            modulation_index_denominator: 0,
            ..c.clone()
        },
        CoherentCpmConfig {
            modulation_index_numerator: 17,
            ..c.clone()
        },
        CoherentCpmConfig {
            gaussian_bt: Some(f64::NAN),
            ..c.clone()
        },
        CoherentCpmConfig {
            noise_variance: 0.,
            ..c.clone()
        },
        CoherentCpmConfig {
            max_carrier_offset_radians_per_sample: 0.21,
            ..c.clone()
        },
        CoherentCpmConfig {
            min_training_coherence: 1.01,
            ..c.clone()
        },
    ] {
        assert!(prepare(&iq, &training, &bad).is_err());
    }
    assert!(prepare(&iq, &known(&bits, 15), &c).is_err());
    assert!(prepare(&vec![Complex64::new(0., 0.); iq.len()], &training, &c).is_err());
    let mut invalid_iq = iq.clone();
    invalid_iq[17].re = f64::NAN;
    assert!(prepare(&invalid_iq, &training, &c).is_err());
    let p = prepare(&iq, &training, &c).unwrap();
    assert!(p.detect(&[0.; 3]).is_err());
    assert!(p.detect(&vec![101.; bits.len()]).is_err());
    assert!(p.detect(&vec![f64::NAN; bits.len()]).is_err());
}

#[test]
fn wrong_known_preamble_and_noise_fail_coherence_gate() {
    let bits = data(96);
    let c = CoherentCpmConfig {
        min_training_coherence: 0.7,
        ..Default::default()
    };
    let mut iq = transmit(&bits, &c, Complex64::new(1., 0.), 0.);
    let mut wrong = known(&bits, 64);
    for b in wrong.iter_mut().take(64) {
        *b = b.map(|v| 1 - v);
    }
    assert!(prepare(&iq, &wrong, &c).is_err());
    iq.fill(Complex64::new(0., 0.));
    add_noise(&mut iq, 1., 0xadd5);
    assert!(prepare(&iq, &known(&bits, 64), &c).is_err());
}
