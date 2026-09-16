use super::*;

#[path = "../../examples/support/multimode_fixture.rs"]
#[allow(dead_code)]
mod fixture;

fn geometry() -> Geometry {
    Geometry {
        start_sample: 256.,
        samples_per_symbol: 8.,
        carrier_hz: 100.,
        carrier_drift_hz_per_s: 0.,
        carrier_reference_sample: 256.,
        delayed_q: true,
        conjugated: false,
    }
}

fn bits(n: usize) -> Vec<f64> {
    let mut state = 0xd421_e382_2738_5339u64;
    (0..n)
        .map(|_| {
            state ^= state << 13;
            state ^= state >> 7;
            state ^= state << 17;
            if state & 1 == 0 { -1. } else { 1. }
        })
        .collect()
}

// Independent analytic rectangular transmitter. It does not call Model,
// carrier_phase, fit, tracking remodulation, or the acquisition implementation.
fn transmit(
    means: &[f64],
    mode: &str,
    g: &Geometry,
    count: usize,
    gain: Complex64,
) -> Vec<Complex64> {
    let quadrature = mode != "bpsk";
    let stagger = mode == "oqpsk";
    let symbol = |position: f64, arm: usize| {
        let k = position.floor();
        if k < 0. {
            return 0.;
        }
        means
            .get(k as usize * if quadrature { 2 } else { 1 } + arm)
            .copied()
            .unwrap_or(0.)
    };
    (0..count)
        .map(|i| {
            let t = (i as f64 - g.start_sample) / g.samples_per_symbol;
            let re = symbol(t - if stagger && !g.delayed_q { 0.5 } else { 0. }, 0);
            let im = if quadrature {
                symbol(t - if stagger && g.delayed_q { 0.5 } else { 0. }, 1)
            } else {
                0.
            };
            let seconds = (i as f64 - g.carrier_reference_sample) / 8000.;
            let angle = 2. * PI * g.carrier_hz * seconds
                + PI * g.carrier_drift_hz_per_s * seconds * seconds;
            gain * Complex64::new(re, if g.conjugated { -im } else { im })
                * Complex64::new(angle.cos(), angle.sin())
                + Complex64::new(
                    0.012 * (i as f64 * 0.733).sin(),
                    0.009 * (i as f64 * 1.891).cos(),
                )
        })
        .collect()
}

#[test]
fn soft_decoder_guidance_refines_timing_carrier_and_drift_without_changing_source() {
    let data = bits(640);
    let initial = geometry();
    let mut actual = initial.clone();
    actual.start_sample += 1.;
    actual.samples_per_symbol += 0.008;
    actual.carrier_hz += 1.5;
    actual.carrier_drift_hz_per_s = 6.25;
    let iq = transmit(&data, "bpsk", &actual, 6000, Complex64::new(0.7, 0.5));
    let original = iq.clone();
    let means: Vec<_> = data
        .iter()
        .enumerate()
        .map(|(i, b)| b * if i < 64 { 1. } else { 0.96 })
        .collect();
    let config = Config {
        carrier_bound_hz: 6.,
        drift_bound_hz_per_s: 25.,
        clock_bound_ppm: 4000.,
        iterations: 5,
        ..Config::default()
    };
    let result = refine_psk(
        &iq,
        8000,
        "bpsk",
        &MatchedFilter::Rectangular,
        &initial,
        &means,
        &config,
    )
    .unwrap();
    assert!(result.accepted, "{result:#?}");
    assert!(result.holdout_improvement > 0.65, "{result:#?}");
    assert!(result.training_residual_after < result.training_residual_before * 0.35);
    assert!(
        (result.geometry.start_sample - actual.start_sample).abs() < 1.,
        "{result:#?}"
    );
    assert!(
        (result.geometry.carrier_hz - actual.carrier_hz).abs() < 0.75,
        "{result:#?}"
    );
    assert!(
        (result.geometry.carrier_drift_hz_per_s - actual.carrier_drift_hz_per_s).abs() < 1.6,
        "{result:#?}"
    );
    assert_eq!(iq, original);
    assert!(result.evaluated_hypotheses <= 81);
    assert!(result.work <= config.maximum_work);
    let corrected = corrected_iq(&iq, 8000, &result).unwrap();
    assert_eq!(corrected.len(), iq.len());
    assert!(corrected.iter().all(|x| finite(*x)));
}

#[test]
fn clock_refinement_improves_long_burst_with_fixed_carrier() {
    let data = bits(1024);
    let mut initial = geometry();
    initial.samples_per_symbol = 4.;
    let mut actual = initial.clone();
    actual.samples_per_symbol = 4.012;
    let iq = transmit(&data, "bpsk", &actual, 4800, Complex64::new(1., 0.));
    let config = Config {
        timing_bound_symbols: 0.,
        carrier_bound_hz: 0.,
        drift_bound_hz_per_s: 0.,
        clock_bound_ppm: 5000.,
        iterations: 5,
        ..Config::default()
    };
    let result = refine_psk(
        &iq,
        8000,
        "bpsk",
        &MatchedFilter::Rectangular,
        &initial,
        &data,
        &config,
    )
    .unwrap();
    assert!(result.accepted, "{result:#?}");
    assert!((result.geometry.samples_per_symbol - actual.samples_per_symbol).abs() < 0.001);
    assert!(result.holdout_improvement > 0.9);
}

#[test]
fn qpsk_and_both_oqpsk_arms_preserve_wire_mapping_and_conjugation() {
    let data = bits(512);
    for mode in ["qpsk", "oqpsk"] {
        for delayed_q in [false, true] {
            for conjugated in [false, true] {
                let mut initial = geometry();
                initial.delayed_q = delayed_q;
                initial.conjugated = conjugated;
                let mut actual = initial.clone();
                actual.start_sample += 1.;
                let iq = transmit(&data, mode, &actual, 2600, Complex64::new(0.3, -0.8));
                let config = Config {
                    carrier_bound_hz: 0.,
                    drift_bound_hz_per_s: 0.,
                    clock_bound_ppm: 0.,
                    ..Config::default()
                };
                let result = refine_psk(
                    &iq,
                    8000,
                    mode,
                    &MatchedFilter::Rectangular,
                    &initial,
                    &data,
                    &config,
                )
                .unwrap();
                assert!(
                    result.accepted,
                    "{mode}/{delayed_q}/{conjugated}: {result:#?}"
                );
                assert!(result.holdout_improvement > 0.9);
                let baseband = corrected_iq(&iq, 8000, &result).unwrap();
                assert_eq!(baseband.len(), iq.len());
            }
        }
    }
}

#[test]
fn rrc_tracking_recovers_offset_on_an_independent_impulse_train_transmitter() {
    let data = bits(512);
    let initial = geometry();
    let mut iq = vec![Complex64::new(0., 0.); 4500];
    let alpha = 0.35;
    // Transmit by explicit finite convolution of symbol impulses. This does
    // not use the receiver's RRC evaluator or its waveform renderer.
    for (k, bit) in data.iter().enumerate() {
        let center = 258 + k * 8 + 4;
        for offset in -24isize..=24 {
            let i = center as isize + offset;
            if i < 0 || i as usize >= iq.len() {
                continue;
            }
            let t = offset as f64 / 8.;
            let amplitude = if offset == 0 {
                1. - alpha + 4. * alpha / PI
            } else {
                ((PI * (1. - alpha) * t).sin() + 4. * alpha * t * (PI * (1. + alpha) * t).cos())
                    / (PI * t * (1. - 16. * alpha * alpha * t * t))
            };
            iq[i as usize].re += bit * amplitude;
        }
    }
    for (i, y) in iq.iter_mut().enumerate() {
        let angle = 2. * PI * 100. * (i as f64 - 256.) / 8000.;
        *y *= Complex64::new(0.6, 0.3) * Complex64::new(angle.cos(), angle.sin());
        *y += Complex64::new(
            0.01 * (i as f64 * 0.41).sin(),
            0.01 * (i as f64 * 0.73).cos(),
        );
    }
    let config = Config {
        carrier_bound_hz: 0.,
        drift_bound_hz_per_s: 0.,
        clock_bound_ppm: 0.,
        ..Config::default()
    };
    let result = refine_psk(
        &iq,
        8000,
        "bpsk",
        &MatchedFilter::RootRaisedCosine {
            rolloff: alpha,
            span_symbols: 6,
        },
        &initial,
        &data,
        &config,
    )
    .unwrap();
    assert!(result.accepted, "{result:#?}");
    assert!((result.geometry.start_sample - 258.).abs() < 0.5);
    assert!(result.holdout_improvement > 0.95);
}

#[test]
fn rejection_keeps_geometry_and_disallows_correction() {
    let data = bits(512);
    let initial = geometry();
    let iq = transmit(&data, "bpsk", &initial, 4500, Complex64::new(0.7, 0.5));
    let result = refine_psk(
        &iq,
        8000,
        "bpsk",
        &MatchedFilter::Rectangular,
        &initial,
        &vec![0.; data.len()],
        &Config::default(),
    )
    .unwrap();
    assert!(!result.accepted);
    assert_eq!(result.reason, "insufficient_reliable_training");
    assert_eq!(result.geometry.start_sample, initial.start_sample);
    assert_eq!(result.geometry.carrier_hz, initial.carrier_hz);
    assert!(corrected_iq(&iq, 8000, &result).is_err());
}

#[test]
fn noise_and_wrong_waveform_do_not_pass_signal_explanation_guard() {
    let data = bits(512);
    let initial = geometry();
    let random = bits(9000);
    let iq: Vec<_> = random
        .as_chunks::<2>()
        .0
        .iter()
        .map(|b| Complex64::new(b[0], b[1]))
        .collect();
    let config = Config {
        minimum_holdout_explained: 0.5,
        ..Config::default()
    };
    let result = refine_psk(
        &iq,
        8000,
        "bpsk",
        &MatchedFilter::Rectangular,
        &initial,
        &data,
        &config,
    )
    .unwrap();
    assert!(!result.accepted, "{result:#?}");
    let wrong = data
        .iter()
        .enumerate()
        .map(|(i, b)| if i % 3 == 0 { *b } else { -*b })
        .collect::<Vec<_>>();
    let wrong_iq = transmit(&wrong, "bpsk", &initial, 4500, Complex64::new(1., 0.));
    let result = refine_psk(
        &wrong_iq,
        8000,
        "bpsk",
        &MatchedFilter::Rectangular,
        &initial,
        &data,
        &config,
    )
    .unwrap();
    assert!(!result.accepted, "{result:#?}");
}

#[test]
fn training_only_improvement_does_not_override_bad_holdout() {
    let data = bits(512);
    let initial = geometry();
    let mut actual = initial.clone();
    actual.start_sample += 1.;
    let mut iq = transmit(&data, "bpsk", &actual, 4500, Complex64::new(1., 0.));
    for (i, y) in iq.iter_mut().enumerate() {
        if !(i / 8).is_multiple_of(2) {
            *y = -*y;
        }
    }
    let config = Config {
        clock_bound_ppm: 0.,
        carrier_bound_hz: 0.,
        drift_bound_hz_per_s: 0.,
        ..Config::default()
    };
    let result = refine_psk(
        &iq,
        8000,
        "bpsk",
        &MatchedFilter::Rectangular,
        &initial,
        &data,
        &config,
    )
    .unwrap();
    assert!(!result.accepted, "{result:#?}");
    assert!(result.training_residual_after < result.training_residual_before);
    assert_eq!(result.geometry.start_sample, initial.start_sample);
}

#[test]
fn bounds_nonfinite_inputs_and_nonlinear_modes_are_explicitly_rejected() {
    let data = bits(512);
    let initial = geometry();
    let iq = transmit(&data, "bpsk", &initial, 4500, Complex64::new(1., 0.));
    for mode in ["fsk", "gfsk", "gmsk", "afsk", "unknown"] {
        assert!(
            refine_psk(
                &iq,
                8000,
                mode,
                &MatchedFilter::Rectangular,
                &initial,
                &data,
                &Config::default()
            )
            .unwrap_err()
            .contains("PSK only")
        );
    }
    let config = Config {
        maximum_work: 1,
        ..Config::default()
    };
    assert!(
        refine_psk(
            &iq,
            8000,
            "bpsk",
            &MatchedFilter::Rectangular,
            &initial,
            &data,
            &config
        )
        .unwrap_err()
        .contains("budget")
    );
    let mut bad = data.clone();
    bad[20] = f64::NAN;
    assert!(
        refine_psk(
            &iq,
            8000,
            "bpsk",
            &MatchedFilter::Rectangular,
            &initial,
            &bad,
            &Config::default()
        )
        .is_err()
    );
    let bad_config = Config {
        minimum_holdout_improvement: 0.,
        ..Config::default()
    };
    assert!(bad_config.validate().is_err());
    let mut bad_geometry = initial.clone();
    bad_geometry.carrier_hz = 3999.;
    assert!(
        refine_psk(
            &iq,
            8000,
            "bpsk",
            &MatchedFilter::Rectangular,
            &bad_geometry,
            &data,
            &Config::default()
        )
        .is_err()
    );
}

#[test]
fn feedback_means_are_finite_bounded_and_do_not_harden_uncertainty() {
    let means = means_from_extrinsic(&[-1000., -2., 0., 2., 1000.]).unwrap();
    assert_eq!(means[2], 0.);
    assert!(means[1] > -1. && means[1] < 0.);
    assert!(means[3] > 0. && means[3] < 1.);
    assert_eq!(means[0], -1.);
    assert_eq!(means[4], 1.);
    assert!(means_from_extrinsic(&[f64::INFINITY]).is_err());
}

#[test]
fn receiver_tracking_preserves_baseline_and_validates_all_psk_mappings() {
    use crate::{advanced_iq, coded};
    let source = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef";
    let mut accepted_refinements = 0;
    for mode in ["bpsk", "qpsk", "oqpsk", "bpsk_rrc", "qpsk_rrc", "oqpsk_rrc"] {
        let mut config = fixture::config(mode);
        config.cancellation = None;
        config.joint = None;
        config.turbo_iterations = 2;
        config.phase_bins = 4;
        config.wire_to_code = (0..128).map(|i| (37 * i + 11) % 128).collect();
        config.randomizer = coded::Randomizer::CcsdsTc255;
        let frame = fixture::base::frame(39);
        let mut iq = fixture::base::Noise(713).fill(fixture::LENGTH, 0.004);
        fixture::add(
            &mut iq,
            &config,
            &frame,
            None,
            fixture::Tx {
                start: 257,
                carrier: 23.5,
                phase: 2.1,
                delayed_q: false,
                conjugated: true,
                ..fixture::Tx::default()
            },
        );
        let original = iq.clone();
        let baseline = advanced_iq::decode(&iq, fixture::RATE, &config, source).unwrap();
        assert_eq!(baseline.frames.len(), 1, "{mode}: {baseline:#?}");
        config.recovery = Some(advanced_iq::RecoveryOptions {
            tracking: Some(Config {
                carrier_bound_hz: 10.,
                drift_bound_hz_per_s: 50.,
                iterations: 4,
                ..Config::default()
            }),
            ..advanced_iq::RecoveryOptions::default()
        });
        let tracked = advanced_iq::decode(&iq, fixture::RATE, &config, source).unwrap();
        assert_eq!(iq, original);
        assert_eq!(tracked.baseline_frames, baseline.baseline_frames, "{mode}");
        assert_eq!(tracked.frames.len(), 1, "{mode}: {tracked:#?}");
        assert_eq!(tracked.frames[0].frame_hex, hex::encode(frame), "{mode}");
        assert!(!tracked.refinement.is_empty(), "{mode}: {tracked:#?}");
        accepted_refinements += tracked.refinement.iter().filter(|r| r.accepted).count();
        for receipt in &tracked.refinement {
            assert!(receipt.end_sample <= iq.len());
            assert!(receipt.work <= Config::default().maximum_work);
        }
    }
    assert!(
        accepted_refinements > 0,
        "fixture must exercise accepted re-observation, not only rejection"
    );
}

#[test]
fn receiver_tracking_does_not_promote_crc_invalid_frames() {
    use crate::advanced_iq;
    let source = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef";
    for mode in ["bpsk", "qpsk", "oqpsk"] {
        let mut config = fixture::config(mode);
        config.cancellation = None;
        config.joint = None;
        config.recovery = Some(advanced_iq::RecoveryOptions {
            tracking: Some(Config::default()),
            ..advanced_iq::RecoveryOptions::default()
        });
        let mut frame = fixture::base::frame(39);
        frame[7] ^= 1;
        let mut iq = fixture::base::Noise(767).fill(fixture::LENGTH, 0.002);
        fixture::add(&mut iq, &config, &frame, None, fixture::Tx::default());
        let tracked = advanced_iq::decode(&iq, fixture::RATE, &config, source).unwrap();
        assert!(tracked.frames.is_empty(), "{mode}: {tracked:#?}");
        assert!(!tracked.refinement.is_empty(), "{mode}: {tracked:#?}");
        assert!(tracked.cancellation.is_empty());
    }
}
