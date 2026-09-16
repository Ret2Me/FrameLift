use super::*;
#[path = "../../examples/support/advanced_iq_fixture.rs"]
mod fixture;
const SOURCE: &str = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef";

#[test]
fn existing_mission_header_can_group_repeats_without_legacy_wire_header() {
    let mut c = fixture::config();
    let layout = repetition::HeaderLayout {
        bytes: 12,
        protected_start: 0,
        protected_end: 8,
        checksum_offset: 8,
        checksum: repetition::HeaderChecksum::Crc32cBe,
        identity_fields: vec![
            repetition::ByteRange { start: 0, end: 4 },
            repetition::ByteRange { start: 6, end: 8 },
        ],
        immutable_codeword_contract:
            "Epoch and sequence identify an immutable codeword across all four received copies"
                .into(),
    };
    c.repetition = Some(RepeatConfig {
        combine: true,
        key_bytes: 4,
        maximum_copies: 4,
        minimum_correlation: 0.1,
        maximum_gap_symbols: 10000,
        mission_header: Some(layout),
    });
    let frame = fixture::frame(23);
    let mut iq = fixture::Noise(9119).fill(8192, 0.0001);
    for copy in 0..4 {
        let mut bits = c.syncword.clone();
        let mut header = vec![1, 2, 3, 4, copy as u8, 0, 0, 23];
        header.extend(crate::space_link::csp_crc32c(&header).to_be_bytes());
        bits.extend(
            header
                .iter()
                .flat_map(|b| (0..8).rev().map(move |i| (b >> i) & 1)),
        );
        let payload = bits.len();
        bits.extend(fixture::encode(&frame));
        let mut symbols: Vec<_> = bits.iter().map(|b| 2. * f64::from(*b) - 1.).collect();
        for i in 16..128 {
            if (i - 16) % 4 != copy {
                symbols[payload + i] = 0.;
            }
        }
        fixture::add(&mut iq, &symbols, 128 + copy * 1536, 1., 0.);
    }
    let report = decode(&iq, 4000, &c, SOURCE).unwrap();
    assert!(
        report
            .frames
            .iter()
            .any(|f| f.frame_hex == hex::encode(&frame)
                && f.provenance.iter().any(|p| p.lane == "independent_repeats")),
        "{report:?}"
    );
}

#[test]
fn enormous_repeat_fields_fail_validation_without_arithmetic_panics() {
    let mut c = fixture::config();
    c.modulation = "gmsk".into();
    c.waveform = Some(crate::advanced_waveform::Waveform::Fsk {
        deviation_hz: 250.,
        gaussian_bt: Some(0.5),
    });
    c.recovery = Some(RecoveryOptions {
        coherent_cpm: true,
        ..Default::default()
    });
    c.repetition = Some(RepeatConfig {
        combine: true,
        key_bytes: usize::MAX,
        maximum_copies: 4,
        minimum_correlation: 0.2,
        maximum_gap_symbols: 10000,
        mission_header: None,
    });
    assert!(c.validate(4000, 2048).is_err());
}

#[test]
fn real_iq_path_acquires_offset_and_retains_exact_crc_frame() {
    let c = fixture::config();
    let frame = fixture::frame(7);
    let symbols = fixture::symbols(&c, &frame, None);
    let mut iq = fixture::Noise(713).fill(2048, 0.03);
    fixture::add(&mut iq, &symbols, 128, 1., 31.25);
    let report = decode(&iq, 4000, &c, SOURCE).unwrap();
    assert!(
        report
            .frames
            .iter()
            .any(|f| f.frame_hex == hex::encode(&frame)),
        "{report:?}"
    );
    assert_eq!(report.frames.len(), 1);
    assert!(
        report
            .baseline_frames
            .iter()
            .all(|h| report.frames.iter().any(|f| &f.frame_hex == h))
    );
}

#[test]
fn cancellation_recovers_weaker_overlapping_packet() {
    let mut c = fixture::config();
    c.cancellation = Some(interference::Config {
        rounds: 2,
        minimum_holdout_reduction: 0.2,
    });
    let first = fixture::frame(7);
    let second = fixture::frame(19);
    let mut iq = fixture::Noise(9921).fill(2048, 0.005);
    fixture::add(&mut iq, &fixture::symbols(&c, &first, None), 128, 3., 0.);
    fixture::add(&mut iq, &fixture::symbols(&c, &second, None), 160, 1., 0.);
    let report = decode(&iq, 4000, &c, SOURCE).unwrap();
    assert!(report.cancellation.iter().any(|r| r.accepted), "{report:?}");
    assert!(
        report
            .frames
            .iter()
            .any(|r| r.frame_hex == hex::encode(&first)),
        "{report:?}"
    );
    assert!(
        report
            .frames
            .iter()
            .any(|r| r.frame_hex == hex::encode(&second)),
        "{report:?}"
    );
    assert!(
        report.added_frames.contains(&hex::encode(second)),
        "{report:?}"
    );
}

#[test]
fn disjoint_protected_repeats_recover_complementary_erasures() {
    let mut c = fixture::config();
    c.repetition = Some(RepeatConfig {
        combine: true,
        key_bytes: 4,
        maximum_copies: 4,
        minimum_correlation: 0.1,
        maximum_gap_symbols: 10000,
        mission_header: None,
    });
    let frame = fixture::frame(23);
    let mut iq = fixture::Noise(119).fill(8192, 0.0001);
    for copy in 0..4 {
        let mut symbols = fixture::symbols(&c, &frame, Some(23));
        for i in 0..128 {
            if i >= 16 && (i - 16) % 4 != copy {
                symbols[128 + i] = 0.;
            }
        }
        fixture::add(&mut iq, &symbols, 128 + copy * 1536, 1., 0.);
    }
    let report = decode(&iq, 4000, &c, SOURCE).unwrap();
    assert!(report.combined_groups > 0, "{report:?}");
    assert!(
        report
            .frames
            .iter()
            .any(|r| r.frame_hex == hex::encode(&frame)
                && r.provenance.iter().any(|p| p.lane == "independent_repeats")),
        "{report:?}"
    );
}

#[test]
fn invalid_crc_and_noise_never_become_cancellable_telemetry() {
    let mut c = fixture::config();
    c.cancellation = Some(interference::Config {
        rounds: 2,
        minimum_holdout_reduction: 0.2,
    });
    let mut bytes = fixture::frame(23);
    bytes[7] ^= 1;
    let mut iq = fixture::Noise(87).fill(2048, 0.02);
    fixture::add(&mut iq, &fixture::symbols(&c, &bytes, None), 128, 1., 0.);
    let report = decode(&iq, 4000, &c, SOURCE).unwrap();
    assert!(report.frames.is_empty());
    assert!(report.cancellation.is_empty());
    for seed in 1..=8 {
        let noise = fixture::Noise(seed).fill(2048, 1.);
        assert!(decode(&noise, 4000, &c, SOURCE).unwrap().frames.is_empty());
    }
}

#[test]
fn file_output_is_bound_and_cannot_overwrite_or_accept_wrong_formats() {
    use std::io::Write;
    let c = fixture::config();
    let mut iq = fixture::Noise(132).fill(2048, 0.01);
    fixture::add(
        &mut iq,
        &fixture::symbols(&c, &fixture::frame(9), None),
        128,
        1.,
        0.,
    );
    let temp = tempfile::tempdir().unwrap();
    let path = temp.path().join("signal.cf32");
    let mut file = std::fs::File::create(&path).unwrap();
    for z in iq {
        file.write_all(&(z.re as f32).to_le_bytes()).unwrap();
        file.write_all(&(z.im as f32).to_le_bytes()).unwrap();
    }
    drop(file);
    let plan = FilePlan {
        format: generic::InputFormat::Cf32Le,
        sample_rate_hz: 4000,
        start_sample: 0,
        sample_count: 2048,
        receiver: c,
    };
    let out = temp.path().join("result");
    let summary = decode_file(&path, &plan, &out).unwrap();
    assert_eq!(summary["frames"], 1);
    assert!(decode_file(&path, &plan, &out).is_err());
    let mut invalid = plan;
    invalid.format = generic::InputFormat::Audio;
    assert!(decode_file(&path, &invalid, &temp.path().join("invalid")).is_err());
}

#[test]
fn invalid_geometry_and_total_work_fail_closed() {
    let mut c = fixture::config();
    let mut iq = fixture::Noise(713).fill(2048, 0.01);
    fixture::add(
        &mut iq,
        &fixture::symbols(&c, &fixture::frame(7), None),
        128,
        1.,
        0.,
    );
    c.maximum_work = 1;
    assert!(decode(&iq, 4000, &c, SOURCE).is_err());
    c.maximum_work = 500_000_000;
    c.modulation = "unsupported".into();
    assert!(decode(&iq, 4000, &c, SOURCE).is_err());
    c.modulation = "bpsk_rectangular".into();
    iq[0].re = f64::NAN;
    assert!(decode(&iq, 4000, &c, SOURCE).is_err());
}

#[test]
fn wire_mapping_randomization_and_cancellation_round_trip() {
    let mut c = fixture::config();
    c.randomizer = coded::Randomizer::CcsdsTc255;
    c.wire_to_code = (0..128).map(|i| (37 * i + 11) % 128).collect();
    c.cancellation = Some(interference::Config {
        rounds: 1,
        minimum_holdout_reduction: 0.2,
    });
    let bytes = fixture::frame(11);
    let mut iq = fixture::Noise(190).fill(2048, 0.01);
    fixture::add(&mut iq, &fixture::symbols(&c, &bytes, None), 128, 1., 0.);
    let report = decode(&iq, 4000, &c, SOURCE).unwrap();
    assert_eq!(report.frames.len(), 1);
    assert_eq!(report.frames[0].frame_hex, hex::encode(bytes));
    assert!(report.cancellation.iter().any(|r| r.accepted));
}
