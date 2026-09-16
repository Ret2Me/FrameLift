use super::*;
#[path = "../../examples/support/multimode_fixture.rs"]
mod fixture;
use fixture::{LENGTH, RATE, Tx, base, config};
const SOURCE: &str = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef";

#[test]
fn multimode_clean_iq_all_waveforms_validate_and_cancel() {
    for mode in fixture::MODES {
        let c = config(mode);
        let mut iq = base::Noise(765).fill(LENGTH, 0.001);
        let bytes = base::frame(17);
        fixture::add(&mut iq, &c, &bytes, None, Tx::default());
        let report = decode(&iq, RATE, &c, SOURCE).unwrap();
        assert_eq!(
            report
                .frames
                .iter()
                .map(|f| &f.frame_hex)
                .collect::<Vec<_>>(),
            vec![&hex::encode(&bytes)],
            "{mode}: {report:?}"
        );
        assert!(
            report.cancellation.iter().any(|r| r.accepted),
            "{mode}: {report:?}"
        );
    }
}

#[test]
fn multimode_collision_preserves_baseline_and_finds_second_packet() {
    for mode in fixture::MODES {
        let c = config(mode);
        let mut iq = base::Noise(1449).fill(LENGTH, 0.001);
        let first = base::frame(17);
        let second = base::frame(43);
        fixture::add(
            &mut iq,
            &c,
            &first,
            None,
            Tx {
                amplitude: 5.,
                ..Tx::default()
            },
        );
        fixture::add(
            &mut iq,
            &c,
            &second,
            None,
            Tx {
                start: 416,
                phase: -0.6,
                ..Tx::default()
            },
        );
        let report = decode(&iq, RATE, &c, SOURCE).unwrap();
        assert!(
            report
                .frames
                .iter()
                .any(|f| f.frame_hex == hex::encode(&first)),
            "{mode}: {report:?}"
        );
        assert!(
            report
                .frames
                .iter()
                .any(|f| f.frame_hex == hex::encode(&second)),
            "{mode}: {report:?}"
        );
        assert!(
            report
                .baseline_frames
                .iter()
                .all(|h| report.frames.iter().any(|f| &f.frame_hex == h))
        );
    }
}

#[test]
fn multimode_bad_crc_and_noise_have_no_validated_or_cancellable_frames() {
    for mode in fixture::MODES {
        let c = config(mode);
        let mut bytes = base::frame(17);
        bytes[7] ^= 1;
        let mut iq = base::Noise(15).fill(LENGTH, 0.002);
        fixture::add(&mut iq, &c, &bytes, None, Tx::default());
        for samples in [iq, base::Noise(8299).fill(LENGTH, 1.)] {
            let report = decode(&samples, RATE, &c, SOURCE).unwrap();
            assert!(
                report.frames.is_empty() && report.cancellation.is_empty(),
                "{mode}: {report:?}"
            );
        }
    }
}

#[test]
fn multimode_repeats_use_disjoint_source_intervals() {
    for mode in fixture::MODES {
        let mut c = config(mode);
        fixture::repetition(&mut c);
        let mut iq = base::Noise(345).fill(32768, 0.002);
        let bytes = base::frame(19);
        for copy in 0..4 {
            fixture::add(
                &mut iq,
                &c,
                &bytes,
                Some(19),
                Tx {
                    start: 256 + copy * 8192,
                    ..Tx::default()
                },
            );
        }
        let report = decode(&iq, RATE, &c, SOURCE).unwrap();
        assert_eq!(report.frames.len(), 1, "{mode}: {report:?}");
        assert!(report.combined_groups > 0, "{mode}: {report:?}");
        let copies: Vec<_> = report.frames[0]
            .provenance
            .iter()
            .filter(|p| p.lane == "independent_repeats")
            .collect();
        assert_eq!(copies.len(), 4, "{mode}: {report:?}");
        assert!(
            copies
                .windows(2)
                .all(|p| p[0].end_sample <= p[1].start_sample)
        );
    }
}

#[test]
fn quadrature_ambiguities_interleaver_randomizer_and_carrier_are_preserved() {
    for mode in ["qpsk", "oqpsk", "qpsk_rrc", "oqpsk_rrc"] {
        for delayed_q in [true, false] {
            for conjugated in [true, false] {
                let mut c = config(mode);
                c.wire_to_code = (0..128).map(|i| (37 * i + 11) % 128).collect();
                c.randomizer = coded::Randomizer::CcsdsTc255;
                let bytes = base::frame(29);
                let mut iq = base::Noise(517).fill(LENGTH, 0.001);
                fixture::add(
                    &mut iq,
                    &c,
                    &bytes,
                    None,
                    Tx {
                        phase: 2.1,
                        carrier: 23.5,
                        delayed_q,
                        conjugated,
                        ..Tx::default()
                    },
                );
                let report = decode(&iq, RATE, &c, SOURCE).unwrap();
                assert_eq!(
                    report.frames.len(),
                    1,
                    "{mode}/{delayed_q}/{conjugated}: {report:?}"
                );
                assert_eq!(report.frames[0].frame_hex, hex::encode(&bytes));
                assert!(
                    report.cancellation.iter().any(|r| r.accepted),
                    "{mode}/{delayed_q}/{conjugated}: {report:?}"
                );
            }
        }
    }
}

#[test]
fn mismatched_waveform_or_aliased_parameters_fail_closed() {
    let mut c = config("gmsk");
    c.waveform = Some(crate::advanced_waveform::Waveform::Fsk {
        deviation_hz: 900.,
        gaussian_bt: Some(0.5),
    });
    assert!(c.validate(RATE, LENGTH).is_err());
    c = config("qpsk");
    c.residual_carrier_bound_hz = RATE as f64 / 7.;
    assert!(c.validate(RATE, LENGTH).is_err());
    c = config("fsk");
    c.waveform = None;
    assert!(c.validate(RATE, LENGTH).is_err());
    c = config("afsk");
    c.modulation = "qpsk".into();
    assert!(c.validate(RATE, LENGTH).is_err());
}
