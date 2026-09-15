use super::*;
use crate::{generic, protocol};
use serde_json::Value;
use std::{collections::BTreeSet, io::Write};

fn waveform(modulation: &'static str) -> Waveform {
    Waveform {
        hypothesis_id: modulation.into(),
        demodulator_id: format!("iq_{modulation}"),
        dsp: dsp::DspConfig {
            baud: 6000.0,
            mode: modulation.into(),
            rate_errors_ppm: vec![0.0, 250.0],
            phase_bins: 8,
            bank: "full".into(),
            ..Default::default()
        },
        decimation: 1,
        cutoff_hz: None,
        carrier_hz: None,
        mark_hz: 1200.0,
        space_hz: 2200.0,
        psk: None,
    }
}

#[test]
fn carrier_portfolio_default_preserves_serialized_configuration() {
    let old = serde_json::to_value(PskConfig::default()).unwrap();
    assert!(old.get("carrier_candidates").is_none());
    let restored: PskConfig = serde_json::from_value(old).unwrap();
    assert_eq!(restored.carrier_candidates, 1);
    let mut w = waveform("qpsk");
    for count in [0, 4, usize::MAX] {
        w.psk = Some(PskConfig {
            carrier_candidates: count,
            ..Default::default()
        });
        assert!(
            validate_waveform(48000, &w)
                .unwrap_err()
                .contains("carrier_candidates")
        );
    }
    w.psk.as_mut().unwrap().carrier_candidates = 3;
    w.carrier_hz = Some(23500.0);
    assert!(
        validate_waveform(48000, &w)
            .unwrap_err()
            .contains("Nyquist")
    );
    w.psk.as_mut().unwrap().carrier_candidates = 1;
    validate_waveform(48000, &w).unwrap();
}

#[test]
fn carrier_portfolio_retains_all_old_soft_streams_and_unique_new_labels() {
    use sha2::{Digest, Sha256};
    let mut state = 0x42f090fee52aa123u64;
    let iq: Vec<_> = (0..2048)
        .map(|_| {
            let mut component = || {
                state ^= state << 13;
                state ^= state >> 7;
                state ^= state << 17;
                (state >> 11) as f64 / 9007199254740992.0 - 0.5
            };
            Complex64::new(component(), component())
        })
        .collect();
    for mode in ["bpsk", "qpsk", "oqpsk"] {
        for sps in [4.0, 8.0] {
            let mut w = waveform(mode);
            w.dsp.baud = 48000.0 / sps;
            w.dsp.phase_bins = 4;
            w.dsp.rate_errors_ppm = vec![0.0];
            w.psk = Some(PskConfig::default());
            let capture = |wave: &Waveform| {
                let mut streams = Vec::new();
                let count = PskDemodulator(mode)
                    .visit_soft_symbols(
                        Signal::Iq(&iq),
                        48000,
                        wave,
                        &mut |soft, threshold, score, label| {
                            let mut hash = Sha256::new();
                            for value in soft {
                                hash.update(value.to_bits().to_le_bytes());
                            }
                            streams.push((
                                label.unwrap().to_string(),
                                soft.len(),
                                threshold.to_bits(),
                                score.to_bits(),
                                format!("{:x}", hash.finalize()),
                            ));
                            Ok(())
                        },
                    )
                    .unwrap();
                assert_eq!(count, streams.len());
                streams
            };
            let old = capture(&w);
            w.psk.as_mut().unwrap().carrier_candidates = 3;
            let portfolio = capture(&w);
            assert_eq!(portfolio[..old.len()], old);
            assert!(portfolio.len() > old.len());
            assert!(portfolio.len() <= 3 * old.len());
            assert!(
                portfolio[old.len()..]
                    .iter()
                    .all(|s| s.0.contains(":carrier=peak-"))
            );
            let labels: BTreeSet<_> = portfolio.iter().map(|s| &s.0).collect();
            assert_eq!(labels.len(), portfolio.len());
        }
    }
}

#[test]
fn carrier_portfolio_charges_all_work_before_emitting_any_stream() {
    let mut w = waveform("qpsk");
    w.dsp.baud = 500000.0;
    w.dsp.phase_bins = 4;
    w.dsp.rate_errors_ppm = vec![0.0];
    w.psk = Some(PskConfig {
        matched_filter: MatchedFilter::RootRaisedCosine {
            rolloff: 0.35,
            span_symbols: 6,
        },
        ..Default::default()
    });
    let old = work_estimate(2000000, &w, 1000000).unwrap();
    assert!(old.0 <= 200000000 && old.1 <= 500000000);
    w.psk.as_mut().unwrap().carrier_candidates = 3;
    let extra = work_estimate(2000000, &w, 1000000).unwrap();
    assert_eq!(extra.0, 3 * old.0);
    assert_eq!(extra.1, 3 * old.1 + acquisition::work(1000000));
    let iq = vec![Complex64::new(1.0, 0.0); 1000000];
    let mut visited = 0;
    let error = PskDemodulator("qpsk")
        .visit_soft_symbols(Signal::Iq(&iq), 2000000, &w, &mut |_, _, _, _| {
            visited += 1;
            Ok(())
        })
        .unwrap_err();
    assert!(error.contains("shorten explicit windows"));
    assert_eq!(visited, 0);
    let small = work_estimate(2000000, &w, 200000).unwrap();
    assert!(small.0 <= 200000000 && small.1 <= 500000000);
}
fn carrier_portfolio_capture(
    iq: &[Complex64],
    rate: u32,
    w: &Waveform,
) -> Vec<(String, usize, u64, u64, String)> {
    use sha2::{Digest, Sha256};
    let mode = match w.dsp.mode.as_str() {
        "bpsk" => "bpsk",
        "qpsk" => "qpsk",
        "oqpsk" => "oqpsk",
        _ => unreachable!(),
    };
    let mut streams = Vec::new();
    let returned = PskDemodulator(mode)
        .visit_soft_symbols(
            Signal::Iq(iq),
            rate,
            w,
            &mut |soft, threshold, score, label| {
                let mut digest = Sha256::new();
                for value in soft {
                    digest.update(value.to_bits().to_le_bytes());
                }
                streams.push((
                    label.unwrap().to_owned(),
                    soft.len(),
                    threshold.to_bits(),
                    score.to_bits(),
                    format!("{:x}", digest.finalize()),
                ));
                Ok(())
            },
        )
        .unwrap();
    assert_eq!(returned, streams.len());
    streams
}

#[test]
fn carrier_portfolio_nonzero_center_preserves_legacy_and_relative_peak_frame() {
    for mode in ["bpsk", "qpsk", "oqpsk"] {
        for center in [-7000.0, 7000.0] {
            let mut state = 0x5ce1_f309_4827_ad06u64;
            let iq: Vec<_> = (0..4096)
                .map(|i| {
                    state ^= state << 13;
                    state ^= state >> 7;
                    state ^= state << 17;
                    let noise = (state >> 11) as f64 / 9007199254740992.0 - 0.5;
                    Complex64::from_polar(1.0, TAU * (center + 317.0) * i as f64 / 48000.0)
                        + Complex64::new(noise * 0.1, noise * -0.07)
                })
                .collect();
            let mut w = waveform(mode);
            w.dsp.phase_bins = 4;
            w.dsp.rate_errors_ppm = vec![0.0];
            w.carrier_hz = Some(center);
            w.psk = Some(PskConfig::default());
            let old = carrier_portfolio_capture(&iq, 48000, &w);
            let order = if mode == "bpsk" { 2 } else { 4 };
            let mut translated = physical::normalize_record(&iq).unwrap();
            for (i, value) in translated.iter_mut().enumerate() {
                *value *= Complex64::from_polar(1.0, -TAU * center * i as f64 / 48000.0);
            }
            let legacy_peak = physical::carrier_fft(&translated, 48000.0, order, Some(1200.0))
                .unwrap()
                .1;
            let ranked =
                acquisition::peaks(&iq, 48000, Some(center), 6000.0, order, 1200.0, 3).unwrap();
            assert_eq!(ranked[0].offset_hz.to_bits(), legacy_peak.to_bits());
            w.psk.as_mut().unwrap().carrier_candidates = 3;
            let new = carrier_portfolio_capture(&iq, 48000, &w);
            assert_eq!(new[..old.len()], old);
            assert!(new.len() > old.len());
            assert!(new.len() <= 3 * old.len());
            assert_eq!(
                new.iter().map(|s| &s.0).collect::<BTreeSet<_>>().len(),
                new.len()
            );
        }
    }
}

#[test]
fn carrier_portfolio_with_bpsk_drift_retains_banks_and_enforces_compound_admission() {
    let rate = 4800u32;
    let center = 800.0;
    let mut state = 0x15f0_a34c_893d_067eu64;
    let iq: Vec<_> = (0..12000)
        .map(|i| {
            state ^= state << 13;
            state ^= state >> 7;
            state ^= state << 17;
            let noise = (state >> 11) as f64 / 9007199254740992.0 - 0.5;
            let t = i as f64 / rate as f64;
            let sign = if (i / 4 * 73 + i / 4 / 7) % 11 < 5 {
                1.0
            } else {
                -1.0
            };
            sign * Complex64::from_polar(1.0, 0.47 + TAU * ((center + 25.0) * t + 25.0 * t * t))
                + Complex64::new(noise, -0.73 * noise) * 0.03
        })
        .collect();
    let mut w = waveform("bpsk");
    w.dsp.baud = 1200.0;
    w.dsp.phase_bins = 4;
    w.dsp.rate_errors_ppm = vec![0.0];
    w.carrier_hz = Some(center);
    w.psk = Some(PskConfig {
        max_carrier_drift_hz_per_s: Some(200.0),
        ..Default::default()
    });
    let old_work = work_estimate(rate, &w, iq.len()).unwrap();
    let old = carrier_portfolio_capture(&iq, rate, &w);
    assert!(old.iter().any(|s| s.0.ends_with(":carrier=linear-drift")));
    w.psk.as_mut().unwrap().carrier_candidates = 3;
    let new_work = work_estimate(rate, &w, iq.len()).unwrap();
    assert_eq!(new_work.0, 3 * old_work.0);
    assert_eq!(new_work.1, 3 * old_work.1 + acquisition::work(iq.len()));
    let new = carrier_portfolio_capture(&iq, rate, &w);
    assert_eq!(new[..old.len()], old);
    assert!(new.len() > old.len());
    assert!(new.len() <= 3 * old.len());
    assert_eq!(
        new.iter().map(|s| &s.0).collect::<BTreeSet<_>>().len(),
        new.len()
    );

    // The compound bank exceeds the stream cap although its one-carrier
    // constant+drift configuration remains valid. Reject before callbacks.
    w.dsp.phase_bins = 128;
    w.dsp.rate_errors_ppm = (0..8).map(|i| i as f64).collect();
    w.psk.as_mut().unwrap().carrier_candidates = 1;
    validate_waveform(rate, &w).unwrap();
    w.psk.as_mut().unwrap().carrier_candidates = 3;
    let mut visited = 0;
    let error = PskDemodulator("bpsk")
        .visit_soft_symbols(Signal::Iq(&iq), rate, &w, &mut |_, _, _, _| {
            visited += 1;
            Ok(())
        })
        .unwrap_err();
    assert!(error.contains("32768"));
    assert_eq!(visited, 0);
}

fn source_bits() -> (Vec<u8>, BTreeSet<String>) {
    let oracle: Value = serde_json::from_str(include_str!("protocol_oracle.json")).unwrap();
    let c = &oracle["cases"][0];
    let packed = hex::decode(c["levels_hex"].as_str().unwrap()).unwrap();
    let trace: Vec<_> = (0..c["symbol_count"].as_u64().unwrap() as usize)
        .map(|i| (packed[i / 8] >> (7 - i % 8)) & 1)
        .collect();
    let mut bits = Vec::new();
    // Public framing oracle is only a transmitter input, not a receiver hint.
    for _ in 0..2 {
        bits.extend(&trace);
    }
    (
        bits,
        serde_json::from_value(c["expected"][0]["frames_hex"].clone()).unwrap(),
    )
}
fn modulate(
    bits: &[u8],
    modulation: &str,
    phase: f64,
    carrier: f64,
    noise: f64,
    delayed_i: bool,
) -> Vec<Complex64> {
    modulate_channel(
        bits, modulation, phase, carrier, noise, delayed_i, 8.002, 0.0,
    )
}
fn modulate_channel(
    bits: &[u8],
    modulation: &str,
    phase: f64,
    carrier: f64,
    noise: f64,
    delayed_i: bool,
    step: f64,
    drift_hz_per_s: f64,
) -> Vec<Complex64> {
    let mut seed = 0x8123a123u64;
    let mut random = || {
        seed = seed.wrapping_mul(6364136223846793005).wrapping_add(1);
        ((seed >> 32) as f64 / u32::MAX as f64 - 0.5) * noise
    };
    let mut stream: Vec<u8> = (0..128)
        .map(|i| u8::from((i * 73 + i / 7) % 11 < 5))
        .collect();
    stream.extend(bits);
    stream.extend((0..128).map(|i| (i % 2) as u8));
    let branch = |t: f64, parity: usize| {
        let symbol = (t.max(0.0) / step).floor() as usize;
        let index = if modulation == "bpsk" {
            symbol
        } else {
            symbol * 2 + parity
        };
        if stream[index.min(stream.len() - 1)] == 1 {
            1.0
        } else {
            -1.0
        }
    };
    let count =
        (stream.len() as f64 * step / if modulation == "bpsk" { 1.0 } else { 2.0 }) as usize;
    (0..count)
        .map(|i| {
            let t = i as f64 - 2.35;
            let half = if modulation == "oqpsk" {
                step / 2.0
            } else {
                0.0
            };
            let v = Complex64::new(
                branch(t - if delayed_i { half } else { 0.0 }, 0),
                if modulation == "bpsk" {
                    0.0
                } else {
                    branch(t - if delayed_i { 0.0 } else { half }, 1)
                },
            );
            // Independent transmitter mapping, CFO, fractional timing and bounded noise.
            let seconds = i as f64 / 48000.0;
            v * Complex64::from_polar(
                1.0,
                phase + TAU * (carrier * seconds + 0.5 * drift_hz_per_s * seconds * seconds),
            ) + Complex64::new(random(), random())
        })
        .collect()
}

#[test]
fn joint_tracking_recovers_off_grid_clock_with_carrier_drift() {
    let (bits, expected) = source_bits();
    // Longer transmitter-only lead-in allows initial acquisition. No clock or
    // phase truth, lead-in contents or expected bytes reach the receiver.
    let guarded: Vec<_> = (0..2048)
        .map(|i| u8::from((i * 73 + i / 7) % 11 < 5))
        .chain(bits)
        .collect();
    for modulation in ["bpsk", "qpsk", "oqpsk"] {
        for ppm in [-7300.0, 7300.0] {
            for delayed_i in if modulation == "oqpsk" {
                &[false, true][..]
            } else {
                &[false][..]
            } {
                let iq = modulate_channel(
                    &guarded,
                    modulation,
                    0.73,
                    41.0,
                    0.1,
                    *delayed_i,
                    8.0 * (1.0 + ppm * 1e-6),
                    40.0,
                );
                let mut w = waveform(modulation);
                w.dsp.rate_errors_ppm = vec![0.0];
                let mut got = BTreeSet::new();
                PskDemodulator(modulation)
                    .visit_soft_symbols(
                        Signal::Iq(&iq),
                        48000,
                        &w,
                        &mut |soft, threshold, _, label| {
                            if label.is_some_and(|v| v.ends_with(":sync=joint")) {
                                got.extend(
                                    protocol::decode_ax25(soft, threshold, &[false, true])?
                                        .into_iter()
                                        .map(hex::encode),
                                );
                            }
                            Ok(())
                        },
                    )
                    .unwrap();
                assert_eq!(
                    got, expected,
                    "{modulation} ppm={ppm} delayed_i={delayed_i}"
                );
            }
        }
    }
}

#[test]
fn frontend_budget_counts_fft_floor_and_joint_streams() {
    let mut w = waveform("bpsk");
    let tracked = work_estimate(48000, &w, 128).unwrap();
    assert!(tracked.1 >= 8 * 65536 * 16);
    w.psk = Some(PskConfig {
        loop_bandwidth: 0.0,
        ..Default::default()
    });
    let fixed = work_estimate(48000, &w, 128).unwrap();
    assert!(tracked.0 > 2 * fixed.0);
    assert!(tracked.1 > fixed.1);
    for length in [128, 1024] {
        let values: Vec<_> = (0..length)
            .map(|i| Complex64::from_polar(1.0, i as f64 * 0.013))
            .collect();
        for step in [1.98, 8.0, 129.28] {
            for branch in [0, 1] {
                for cubic in [false, true] {
                    let symbols = joint_symbols(
                        &values,
                        JointTracking {
                            step,
                            phase: step * 0.99,
                            bpsk: false,
                            oqpsk: true,
                            branch,
                            bandwidth: 0.2,
                            timing_bandwidth: 0.2,
                            power: 1.0,
                            cubic,
                        },
                    );
                    assert!(symbols.len() <= (length as f64 / (step * 0.79)).ceil() as usize + 2);
                    assert!(symbols.iter().all(|v| v.re.is_finite() && v.im.is_finite()));
                }
            }
        }
    }
}

#[test]
fn low_sps_fast_timing_lane_covers_all_psk_modes_and_is_fully_budgeted() {
    let iq: Vec<_> = (0..160)
        .map(|i| Complex64::new((i as f64 * 0.193).sin(), (i as f64 * 0.417).cos()))
        .collect();
    for mode in ["bpsk", "qpsk", "oqpsk"] {
        for sps in [2.0, 4.0, 8.0] {
            for bandwidth in [0.0, 0.01, 0.2] {
                let mut w = waveform(mode);
                w.dsp.baud = 48000.0 / sps;
                w.dsp.phase_bins = 4;
                w.dsp.rate_errors_ppm = vec![0.0];
                w.psk = Some(PskConfig {
                    loop_bandwidth: bandwidth,
                    max_carrier_offset_hz: Some(100.0),
                    ..Default::default()
                });
                let expected_lanes = if bandwidth == 0.0 {
                    1
                } else if sps > 4.0 {
                    2
                } else if bandwidth < 0.2 {
                    4
                } else {
                    3
                };
                assert_eq!(synchronization_lanes(sps, bandwidth), expected_lanes);
                let variants = match mode {
                    "bpsk" => 2,
                    "qpsk" => 8,
                    _ => 48,
                };
                let expected_streams = 4 * variants * expected_lanes;
                let mut visits = 0u128;
                let mut fast = 0;
                let emitted = PskDemodulator(mode)
                    .visit_soft_symbols(Signal::Iq(&iq), 48000, &w, &mut |soft, _, _, label| {
                        visits += soft.len() as u128;
                        fast +=
                            usize::from(label.unwrap().contains(":sync=joint-cubic-fast-timing"));
                        assert!(soft.iter().all(|v| v.is_finite()));
                        Ok(())
                    })
                    .unwrap();
                assert_eq!(emitted, expected_streams);
                assert_eq!(fast, if expected_lanes == 4 { 4 * variants } else { 0 });
                assert!(visits <= work_estimate(48000, &w, iq.len()).unwrap().0);
            }
        }
    }
    let mut w = waveform("oqpsk");
    w.dsp.baud = 24000.0;
    w.dsp.phase_bins = 96;
    w.dsp.rate_errors_ppm = vec![0.0, 1000.0];
    w.psk = Some(PskConfig {
        max_carrier_offset_hz: Some(100.0),
        ..Default::default()
    });
    // 96 * 2 * 48 * 3 would fit, but all four lanes exceed the stream cap.
    assert!(validate_waveform(48000, &w).is_err());
    w.dsp.phase_bins = 64;
    assert!(validate_waveform(48000, &w).is_ok());
}

#[test]
fn cubic_interpolator_reproduces_polynomials_and_keeps_linear_endpoints() {
    let polynomial = |t: f64| {
        Complex64::new(
            0.01 * t * t * t - 0.02 * t * t + 0.5 * t + 0.3,
            -0.013 * t * t * t + 0.25 * t - 0.7,
        )
    };
    let values: Vec<_> = (0..32).map(|i| polynomial(i as f64)).collect();
    for i in 1..30 {
        for fraction in [0.0, 0.13, 0.5, 0.99] {
            let t = i as f64 + fraction;
            assert!((interpolate_cubic(&values, t) - polynomial(t)).norm() < 1e-12);
        }
    }
    for t in [0.0, 0.5, 30.0, 30.5, 31.0] {
        let got = interpolate_cubic(&values, t);
        let expected = interpolate(&values, t);
        assert_eq!(got.re.to_bits(), expected.re.to_bits());
        assert_eq!(got.im.to_bits(), expected.im.to_bits());
    }
}

#[test]
fn full_five_second_bpsk_window_is_admitted_with_complete_tracking_cost() {
    // Original real-capture geometry, independent of its packet contents.
    // A constant input exercises admission without an expensive FIR/bit scan.
    let mut w = waveform("bpsk");
    w.dsp.baud = 9600.0;
    w.dsp.phase_bins = 16;
    w.dsp.rate_errors_ppm = vec![-250.0, 0.0, 250.0];
    w.psk = Some(PskConfig {
        matched_filter: MatchedFilter::RootRaisedCosine {
            rolloff: 0.35,
            span_symbols: 6,
        },
        ..Default::default()
    });
    let iq = vec![Complex64::new(0.0, 0.0); 240000];
    for drift in [None, Some(200.0)] {
        w.psk.as_mut().unwrap().max_carrier_drift_hz_per_s = drift;
        let (soft, frontend) = work_estimate(48000, &w, iq.len()).unwrap();
        assert!(soft <= 200_000_000 && frontend > 200_000_000 && frontend <= 500_000_000);
        let attempts = PskDemodulator("bpsk")
            .visit_soft_symbols(Signal::Iq(&iq), 48000, &w, &mut |_, _, _, _| {
                panic!("constant input emitted symbols")
            })
            .unwrap();
        assert_eq!(attempts, 0);
    }
}

#[test]
fn drift_acquisition_is_opt_in_bounded_and_keeps_exact_original_bank() {
    let (bits, _) = source_bits();
    let iq = modulate_channel(&bits, "bpsk", 0.71, 70.0, 0.1, false, 8.0, 25.0);
    let mut w = waveform("bpsk");
    w.dsp.phase_bins = 4;
    w.dsp.rate_errors_ppm = vec![0.0];
    let capture = |w: &Waveform| {
        let mut streams = Vec::new();
        PskDemodulator("bpsk")
            .visit_soft_symbols(
                Signal::Iq(&iq),
                48000,
                w,
                &mut |soft, threshold, score, label| {
                    streams.push((
                        soft.iter().map(|x| x.to_bits()).collect::<Vec<_>>(),
                        threshold.to_bits(),
                        score.to_bits(),
                        label.unwrap().to_owned(),
                    ));
                    Ok(())
                },
            )
            .unwrap();
        streams
    };
    let original = capture(&w);
    let default_from_json: PskConfig = serde_json::from_str("{}").unwrap();
    assert!(default_from_json.max_carrier_drift_hz_per_s.is_none());
    let old_work = work_estimate(48000, &w, iq.len()).unwrap();
    w.psk = Some(PskConfig {
        max_carrier_drift_hz_per_s: Some(200.0),
        ..Default::default()
    });
    let expanded = capture(&w);
    assert_eq!(&expanded[..original.len()], &original);
    assert_eq!(expanded.len(), original.len() * 2);
    assert!(
        expanded[original.len()..]
            .iter()
            .all(|x| x.3.ends_with(":carrier=linear-drift"))
    );
    let new_work = work_estimate(48000, &w, iq.len()).unwrap();
    assert_eq!(new_work.0, old_work.0 * 2);
    assert!(new_work.1 > old_work.1 * 2);
    w.psk.as_mut().unwrap().max_carrier_drift_hz_per_s = Some(0.0001);
    assert_eq!(
        capture(&w),
        original,
        "an out-of-bounds drift fit must retain the original bank"
    );
    for limit in [f64::NAN, f64::INFINITY, -1.0, 0.0, 1_000_001.0] {
        w.psk.as_mut().unwrap().max_carrier_drift_hz_per_s = Some(limit);
        assert!(validate_waveform(48000, &w).is_err());
    }
    w.psk.as_mut().unwrap().max_carrier_drift_hz_per_s = Some(200.0);
    w.dsp.mode = "qpsk".into();
    assert!(validate_waveform(48000, &w).is_err());
}
fn recover(iq: &[Complex64], modulation: &'static str, w: &Waveform) -> BTreeSet<String> {
    let mut frames = BTreeSet::new();
    PskDemodulator(modulation)
        .visit_soft_symbols(Signal::Iq(iq), 48000, w, &mut |soft, threshold, _, _| {
            for f in protocol::decode_ax25(soft, threshold, &[false, true])? {
                frames.insert(hex::encode(f));
            }
            Ok(())
        })
        .unwrap();
    frames
}
#[test]
fn psk_to_crc_frames_with_cfo_phase_clock_noise_and_both_oqpsk_branches() {
    let (bits, expected) = source_bits();
    assert!(!expected.is_empty());
    for modulation in ["bpsk", "qpsk", "oqpsk"] {
        for delayed_i in [false, true] {
            let iq = modulate(&bits, modulation, 1.17, 73.25, 0.25, delayed_i);
            assert_eq!(
                recover(&iq, modulation, &waveform(modulation)),
                expected,
                "{modulation} delayed_i={delayed_i}"
            );
        }
    }
}
#[test]
fn quadrant_and_spectral_ambiguities_do_not_need_payload_truth() {
    let (bits, expected) = source_bits();
    for modulation in ["bpsk", "qpsk", "oqpsk"] {
        for quadrant in 0..4 {
            let mut iq = modulate(
                &bits,
                modulation,
                quadrant as f64 * PI / 2.0 + 0.13,
                -41.75,
                0.0,
                false,
            );
            iq.iter_mut().for_each(|v| *v = v.conj());
            assert_eq!(
                recover(&iq, modulation, &waveform(modulation)),
                expected,
                "{modulation} quadrant {quadrant}"
            );
        }
    }
}
#[test]
fn psk_file_pipeline_is_resumable_and_preserves_one_four_thread_results() {
    let temp = tempfile::tempdir().unwrap();
    let (bits, expected) = source_bits();
    for modulation in ["bpsk", "qpsk", "oqpsk"] {
        let iq = modulate(&bits, modulation, 0.71, 25.0, 0.1, false);
        let path = temp.path().join(format!("{modulation}.cf32"));
        let mut file = std::fs::File::create(&path).unwrap();
        for v in &iq {
            for x in [v.re, v.im] {
                file.write_all(&(x as f32).to_le_bytes()).unwrap();
            }
        }
        drop(file);
        let p = generic::Plan {
            format: generic::InputFormat::Cf32Le,
            sample_rate_hz: 48000,
            window_seconds: iq.len() as f64 / 48000.0,
            hop_seconds: iq.len() as f64 / 48000.0,
            segment_start_sample: 0,
            segment_sample_count: None,
            protocols: std::collections::BTreeMap::from([(
                "ax25".into(),
                generic::ProtocolConfig::Ax25 {
                    g3ruh_modes: vec![false, true],
                },
            )]),
            hypotheses: vec![generic::PlannedHypothesis {
                waveform: waveform(modulation),
                protocol_id: "ax25".into(),
            }],
        };
        let registry = generic::GenericReceiver::with_builtin_demodulators();
        let out = temp.path().join(format!("{modulation}-one"));
        let a = registry.decode_file(&path, &out, &p, 1).unwrap();
        let b = registry
            .decode_file(
                &path,
                &temp.path().join(format!("{modulation}-four")),
                &p,
                4,
            )
            .unwrap();
        assert_eq!(a["frames"], b["frames"]);
        let actual: BTreeSet<_> = a["frames"]
            .as_array()
            .unwrap()
            .iter()
            .map(|f| f["frame_hex"].as_str().unwrap().to_owned())
            .collect();
        assert_eq!(actual, expected, "{modulation}");
        assert_eq!(
            registry
                .decode_file_resumable(&path, &out, &p, 1, true)
                .unwrap()["frames"],
            a["frames"]
        );
        let mut bad = p.clone();
        bad.hypotheses[0].waveform.psk = Some(PskConfig {
            loop_bandwidth: 0.02,
            ..Default::default()
        });
        assert!(
            registry
                .decode_file_resumable(&path, &out, &bad, 1, true)
                .is_err()
        );
    }
}
#[test]
fn invalid_psk_contracts_and_noise_cannot_be_validated_frames() {
    for modulation in ["bpsk", "qpsk", "oqpsk"] {
        let mut w = waveform(modulation);
        assert!(
            PskDemodulator(modulation)
                .visit_soft_symbols(
                    Signal::Pcm(&vec![0.0; 1024]),
                    48000,
                    &w,
                    &mut |_, _, _, _| Ok(())
                )
                .is_err()
        );
        assert!(recover(&vec![Complex64::new(0.0, 0.0); 1024], modulation, &w).is_empty());
        let iq: Vec<_> = (0..2048)
            .map(|n| Complex64::new((n as f64 * 1.731).sin(), (n as f64 * 2.931).cos()))
            .collect();
        assert!(recover(&iq, modulation, &w).is_empty());
        w.dsp.bank = "global".into();
        assert!(validate_waveform(48000, &w).is_err());
        w.dsp.bank = "full".into();
        w.decimation = 2;
        assert!(validate_waveform(48000, &w).is_err());
        w.decimation = 1;
        w.psk = Some(PskConfig {
            max_carrier_offset_hz: Some(f64::NAN),
            ..Default::default()
        });
        assert!(validate_waveform(48000, &w).is_err());
    }
}

#[test]
fn rrc_and_explicit_carrier_paths_retain_crc_frames() {
    let (bits, expected) = source_bits();
    for modulation in ["bpsk", "qpsk", "oqpsk"] {
        let iq = modulate(&bits, modulation, 1.27, 7025.0, 0.1, false);
        let mut w = waveform(modulation);
        w.carrier_hz = Some(7000.0);
        w.psk = Some(PskConfig {
            matched_filter: MatchedFilter::RootRaisedCosine {
                rolloff: 0.35,
                span_symbols: 6,
            },
            ..Default::default()
        });
        assert_eq!(recover(&iq, modulation, &w), expected, "{modulation} RRC");
    }
}

#[test]
fn psk_plan_aggregate_work_is_bounded_without_pruning_hypotheses() {
    let p: generic::Plan = serde_json::from_value(serde_json::json!({
        "format":"cf32_le","sample_rate_hz":48000,"window_seconds":6.0,"hop_seconds":3.0,
        "protocols":{"ax25":{"type":"ax25","g3ruh_modes":[false]}},
        "hypotheses":[{"waveform":waveform("oqpsk"),"protocol_id":"ax25"}]
    }))
    .unwrap();
    let receiver = generic::GenericReceiver::with_builtin_demodulators();
    receiver.validate(&p).unwrap();
    let mut many = p.clone();
    for n in 1..32 {
        let mut h = p.hypotheses[0].clone();
        h.waveform.hypothesis_id = format!("oqpsk-{n}");
        many.hypotheses.push(h);
    }
    assert!(receiver.validate(&many).unwrap_err().contains("aggregate"));
}

#[test]
fn final_valid_symbol_at_window_boundary_is_not_dropped() {
    for modulation in ["bpsk", "qpsk", "oqpsk"] {
        let step = 10.0;
        let half = if modulation == "oqpsk" { 5.0 } else { 0.0 };
        for length in [100, 101, 105, 106, 8192] {
            for phase in [0.0, 0.25, 3.0, 9.75] {
                let count = symbol_count(length, phase, half, step);
                assert!(count > 0);
                assert!(phase + (count - 1) as f64 * step + half <= length as f64 - 1.0);
                assert!(phase + count as f64 * step + half > length as f64 - 1.0);
            }
        }
        assert_eq!(
            symbol_count(101, 0.0, half, step),
            if modulation == "oqpsk" { 10 } else { 11 }
        );
    }
}

#[test]
fn all_three_psk_modes_recover_csp_aos_uslp_through_rs_and_independent_crc() {
    use crate::{coded, fec, space_link};
    use generic::ProtocolDecoder;
    let fixtures = [
        (
            "a23a8501414243de918426",
            space_link::SpaceLinkConfig::CspV1 {
                crc32: space_link::CspCrc32Mode::RequiredHeaderAndPayload,
            },
        ),
        (
            "4234048ea1414142434cef685e",
            space_link::SpaceLinkConfig::CspV2 {
                crc32: space_link::CspCrc32Mode::RequiredHeaderAndPayload,
            },
        ),
        (
            "6ad1123456e9aabb010203102030406795",
            space_link::SpaceLinkConfig::Aos {
                frame_length_bytes: 17,
                insert_zone_length_bytes: 2,
                operational_control_field: true,
                frame_error_control_field: true,
                frame_header_error_control: false,
                security: false,
            },
        ),
        (
            "c12340b200138a0102eee2deadbe001122336ef1",
            space_link::SpaceLinkConfig::Uslp {
                expected_frame_length_bytes: Some(20),
                insert_zone_length_bytes: 1,
                frame_error_control_field: true,
                security: false,
            },
        ),
    ];
    let sync: Vec<_> = [0x1au8, 0xcf, 0xfc, 0x1d]
        .iter()
        .flat_map(|b| (0..8).rev().map(move |i| (b >> i) & 1))
        .collect();
    for (literal, link) in fixtures {
        let frame = hex::decode(literal).unwrap();
        let rs = fec::ReedSolomonConfig::ccsds_rs255_223(1, 223 - frame.len());
        let mut word = rs.encode(&frame).unwrap();
        for i in 0..4 {
            word[i * 7] ^= 0xb7;
        }
        let mut soft: Vec<_> = word
            .iter()
            .flat_map(|b| {
                (0..8)
                    .rev()
                    .map(move |i| if b & (1 << i) != 0 { 1.0 } else { -1.0 })
            })
            .collect();
        coded::derandomize(&mut soft, &coded::Randomizer::CcsdsTm255);
        let trace: Vec<_> = sync
            .iter()
            .copied()
            .chain(soft.iter().map(|&v| u8::from(v > 0.0)))
            .collect();
        let trace: Vec<_> = (0..4).flat_map(|_| trace.iter().copied()).collect();
        let p = generic::ProtocolConfig::CodedSync {
            config: coded::CodedSyncConfig {
                syncword: sync.clone(),
                maximum_sync_hamming: 1,
                frame_bytes: frame.len(),
                code: fec::FrameCode::ReedSolomon { config: rs },
                randomizer: coded::Randomizer::CcsdsTm255,
                validator: coded::FrameValidator::SpaceLink { config: link },
                maximum_candidates: 64,
            },
        };
        for modulation in ["bpsk", "qpsk", "oqpsk"] {
            let iq = modulate(&trace, modulation, 1.27, -61.5, 0.1, false);
            let mut got = BTreeSet::new();
            PskDemodulator(modulation)
                .visit_soft_symbols(
                    Signal::Iq(&iq),
                    48000,
                    &waveform(modulation),
                    &mut |soft, threshold, _, _| {
                        for f in p.decode(soft, threshold)? {
                            got.insert(f.frame);
                        }
                        Ok(())
                    },
                )
                .unwrap();
            assert_eq!(
                got,
                BTreeSet::from([frame.clone()]),
                "{modulation} {literal}"
            );
        }
    }
}

#[test]
fn all_psk_modes_recover_ldpc_coded_csp_payload_not_just_header() {
    use crate::{coded, fec, space_link};
    use generic::ProtocolDecoder;
    // Independent CCSDS231 table4-2 generator rows, not the decoder's H.
    const ROWS: [[u64; 4]; 4] = [
        [
            0x1d21794a22761fae,
            0x59945014257e130d,
            0x74d6054003794014,
            0x2dadeb9ca25ef12e,
        ],
        [
            0x60e0b6623c5ce512,
            0x4d2c81ecc7f469ab,
            0x20678dbfb7523ece,
            0x2b54b906a9dbe98c,
        ],
        [
            0xf6739bcf54273e77,
            0x167bda120c6c4774,
            0x4c071eff5e32a759,
            0x3138670c095c39b5,
        ],
        [
            0x28706bd045300258,
            0x2dab85f05b9201d0,
            0x8dfdee2d9d84ca88,
            0xb371fae63a4eb07e,
        ],
    ];
    let mut frame = vec![0xa2, 0x3a, 0x85, 0x01];
    frame.extend(0u8..24);
    frame.extend(space_link::csp_crc32c(&frame).to_be_bytes());
    let mut parity = [0u64; 4];
    for bit in 0..256 {
        if frame[bit / 8] & (1 << (7 - bit % 8)) != 0 {
            for block in 0..4 {
                parity[block] ^= ROWS[bit / 64][block].rotate_right((bit % 64) as u32);
            }
        }
    }
    let word: Vec<_> = frame
        .iter()
        .copied()
        .chain(parity.into_iter().flat_map(u64::to_be_bytes))
        .collect();
    let mut soft: Vec<_> = word
        .iter()
        .flat_map(|b| {
            (0..8)
                .rev()
                .map(move |i| if b & (1 << i) != 0 { 1.0 } else { -1.0 })
        })
        .collect();
    // One transmitted bit deliberately wrong: CRC requires actual FEC recovery.
    soft[137] *= -1.0;
    coded::derandomize(&mut soft, &coded::Randomizer::CcsdsTc255);
    let sync: Vec<_> = [0x1au8, 0xcf, 0xfc, 0x1d]
        .iter()
        .flat_map(|b| (0..8).rev().map(move |i| (b >> i) & 1))
        .collect();
    let trace: Vec<_> = sync
        .iter()
        .copied()
        .chain(soft.iter().map(|&v| u8::from(v > 0.0)))
        .collect();
    let trace: Vec<_> = (0..4).flat_map(|_| trace.iter().copied()).collect();
    let p = generic::ProtocolConfig::CodedSync {
        config: coded::CodedSyncConfig {
            syncword: sync,
            maximum_sync_hamming: 1,
            frame_bytes: 32,
            code: fec::FrameCode::Ldpc {
                config: fec::LdpcConfig::ccsds_tc512(),
            },
            randomizer: coded::Randomizer::CcsdsTc255,
            maximum_candidates: 64,
            validator: coded::FrameValidator::SpaceLink {
                config: space_link::SpaceLinkConfig::CspV1 {
                    crc32: space_link::CspCrc32Mode::RequiredHeaderAndPayload,
                },
            },
        },
    };
    for modulation in ["bpsk", "qpsk", "oqpsk"] {
        let iq = modulate(&trace, modulation, 0.97, 45.5, 0.1, false);
        let mut got = BTreeSet::new();
        PskDemodulator(modulation)
            .visit_soft_symbols(
                Signal::Iq(&iq),
                48000,
                &waveform(modulation),
                &mut |soft, threshold, _, _| {
                    for f in p.decode(soft, threshold)? {
                        got.insert(f.frame);
                    }
                    Ok(())
                },
            )
            .unwrap();
        assert_eq!(got, BTreeSet::from([frame.clone()]), "{modulation} TC512");
    }
}
