use super::*;

#[test]
fn immutable_python_scalar_and_fast_oracle_515_ordered_cases() {
    // Fixture generated from both pre-migration Python implementations, which
    // had to agree before every case was recorded. Python is not run by tests.
    let document: serde_json::Value =
        serde_json::from_str(include_str!("protocol_oracle.json")).unwrap();
    assert_eq!(document["case_count"], 103);
    let mut comparisons = 0;
    for case in document["cases"].as_array().unwrap() {
        let packed = hex::decode(case["levels_hex"].as_str().unwrap()).unwrap();
        let mut levels = byte_bits(&packed, false);
        levels.truncate(case["symbol_count"].as_u64().unwrap() as usize);
        let symbols: Vec<f64> = levels
            .iter()
            .map(|value| 3.0 * f64::from(*value) - 0.5)
            .collect();
        for expected in case["expected"].as_array().unwrap() {
            let modes: Vec<bool> = serde_json::from_value(expected["modes"].clone()).unwrap();
            let frames = decode_ax25(&symbols, 0.4, &modes).unwrap();
            let frame_hex: Vec<String> = frames.iter().map(hex::encode).collect();
            assert_eq!(
                serde_json::json!(frame_hex),
                expected["frames_hex"],
                "case={} modes={modes:?}",
                case["name"]
            );
            comparisons += 1;
        }
    }
    assert_eq!(comparisons, 515);
}

#[test]
fn immutable_python_ccsds_oracle_704_structural_and_integrity_cases() {
    let document: serde_json::Value =
        serde_json::from_str(include_str!("protocol_ccsds_oracle.json")).unwrap();
    let tm_cases = document["tm_cases"].as_array().unwrap();
    let packet_cases = document["packet_cases"].as_array().unwrap();
    assert_eq!(tm_cases.len(), 384);
    assert_eq!(packet_cases.len(), 320);
    for case in tm_cases {
        let raw = hex::decode(case["frame_hex"].as_str().unwrap()).unwrap();
        let config: TmTransferFrameConfig = serde_json::from_value(case["config"].clone()).unwrap();
        let actual = validate_tm_transfer_frame(&raw, &config, None).unwrap();
        assert_eq!(
            serde_json::to_value(actual).unwrap(),
            case["validation"],
            "TM case={}",
            case["name"]
        );
    }
    for case in packet_cases {
        let raw = hex::decode(case["packet_hex"].as_str().unwrap()).unwrap();
        let actual = parse_ccsds_space_packet(&raw, case["exact"].as_bool().unwrap());
        assert_eq!(serde_json::to_value(actual).unwrap(), case["header"]);
    }
}

fn header() -> Vec<u8> {
    hex::decode("94a662b2a0826094a662b29eb2e103f0").unwrap()
}

fn with_fcs(mut data: Vec<u8>) -> Vec<u8> {
    data.extend_from_slice(&crc16_x25(&data).to_le_bytes());
    data
}

fn full_frame(data: &[u8]) -> Vec<u8> {
    let mut payload = header();
    payload.extend_from_slice(data);
    with_fcs(payload)
}

fn byte_bits(bytes: &[u8], lsb: bool) -> Vec<u8> {
    bytes
        .iter()
        .flat_map(|byte| (0..8).map(move |bit| (byte >> if lsb { bit } else { 7 - bit }) & 1))
        .collect()
}

fn stuff(bits: &[u8]) -> Vec<u8> {
    let mut output = Vec::new();
    let mut ones = 0;
    for bit in bits {
        output.push(*bit);
        ones = if *bit == 1 { ones + 1 } else { 0 };
        if ones == 5 {
            output.push(0);
            ones = 0;
        }
    }
    output
}

fn nrzi(bits: &[u8], initial: u8) -> Vec<u8> {
    let mut level = initial;
    bits.iter()
        .map(|bit| {
            if *bit == 0 {
                level ^= 1;
            }
            level
        })
        .collect()
}

fn framed_stream(frames: &[Vec<u8>], g3ruh: bool, initial: u8, leading: usize) -> Vec<u8> {
    let mut bits = HDLC_FLAG.repeat(leading);
    for frame in frames {
        bits.extend(stuff(&byte_bits(frame, true)));
        bits.extend(HDLC_FLAG);
    }
    bits.extend(HDLC_FLAG.repeat(8));
    if g3ruh {
        // Scrambler uses past encoded bits; inverse decoder uses past received.
        let mut encoded = Vec::new();
        for (index, bit) in bits.iter().enumerate() {
            let mut value = *bit;
            if index >= 12 {
                value ^= encoded[index - 12];
            }
            if index >= 17 {
                value ^= encoded[index - 17];
            }
            encoded.push(value);
        }
        bits = encoded;
    }
    nrzi(&bits, initial)
}

fn soft(bits: &[u8]) -> Vec<f64> {
    bits.iter()
        .map(|bit| if *bit == 1 { 1.0 } else { -1.0 })
        .collect()
}

#[test]
fn crc_external_check_vectors_and_all_single_bit_errors() {
    assert_eq!(crc16_x25(b"123456789"), 0x906e);
    assert_eq!(compute_tm_fecf(b"123456789"), 0x29b1);
    assert_eq!(crc16_x25(b""), 0);
    assert!(valid_ax25_fcs(&[0, 0]));
    for data in [b"".as_slice(), b"123456789".as_slice(), &header()] {
        let valid = with_fcs(data.to_vec());
        assert!(valid_ax25_fcs(&valid));
        for index in 0..valid.len() * 8 {
            let mut corrupt = valid.clone();
            corrupt[index / 8] ^= 1 << (index % 8);
            assert!(!valid_ax25_fcs(&corrupt));
        }
    }
    assert!(!valid_ax25_fcs(&[]));
    assert!(!valid_ax25_fcs(&[0]));
    assert!(!validate_tm_fecf(&[]));
    assert!(!validate_tm_fecf(&[0]));
}

#[test]
fn strict_callsigns_reserved_bits_addresses_and_empty_information() {
    let payload = header();
    assert!(valid_ax25_ui(&payload));
    let parsed = parse_ax25_ui(&payload).unwrap();
    assert_eq!(parsed.destination.callsign, "JS1YPA");
    assert_eq!(parsed.source.callsign, "JS1YOY");
    assert_eq!(parsed.source.ssid, 0);
    assert!(parsed.source.command_or_repeated);
    assert!(parsed.information.is_empty());
    for offset in 0..6 {
        let mut invalid = payload.clone();
        invalid[offset] |= 1;
        assert!(!valid_ax25_ui(&invalid));
    }
    for value in *b"@[a!\0" {
        let mut invalid = payload.clone();
        invalid[0] = value << 1;
        assert!(!valid_ax25_ui(&invalid));
    }
    for offset in [6, 13] {
        let mut invalid = payload.clone();
        invalid[offset] &= !0x20;
        assert!(!valid_ax25_ui(&invalid));
    }
    let mut internal_space = payload.clone();
    internal_space[2] = b' ' << 1;
    assert!(!valid_ax25_ui(&internal_space));
    let mut trailing_space = payload.clone();
    trailing_space[5] = b' ' << 1;
    assert!(valid_ax25_ui(&trailing_space));
    let mut blank = payload.clone();
    blank[..6].fill(b' ' << 1);
    assert!(!valid_ax25_ui(&blank));
    let mut early_end = payload.clone();
    early_end[6] |= 1;
    assert!(!valid_ax25_ui(&early_end));
    let mut wrong_control = payload.clone();
    wrong_control[14] = 0x13;
    assert!(!valid_ax25_ui(&wrong_control));
    for pid in 0..=255 {
        let mut arbitrary_pid = payload.clone();
        arbitrary_pid[15] = pid;
        assert!(valid_ax25_ui(&arbitrary_pid));
    }
    for count in [2usize, 10, 11] {
        let mut long = Vec::new();
        for index in 0..count {
            long.extend_from_slice(&payload[..7]);
            if index == count - 1 {
                *long.last_mut().unwrap() |= 1;
            }
        }
        long.extend([3, 0xf0]);
        assert_eq!(valid_ax25_ui(&long), count <= 10);
    }
}

#[test]
fn exact_mode_initial_level_order_and_deduplication() {
    let frames: Vec<Vec<u8>> = (0..4).map(|value| full_frame(&[value; 37])).collect();
    for g3ruh in [false, true] {
        for initial in [0, 1] {
            for leading in [1, 2, 8] {
                let mut duplicates = frames.clone();
                duplicates.extend(frames.clone());
                let levels = framed_stream(&duplicates, g3ruh, initial, leading);
                let decoded = decode_ax25_levels(&levels, g3ruh).unwrap();
                assert_eq!(
                    decoded.iter().collect::<HashSet<_>>(),
                    frames.iter().collect::<HashSet<_>>()
                );
                let mode = vec![g3ruh];
                assert_eq!(decode_ax25(&soft(&levels), 0.0, &mode).unwrap(), decoded);
                assert_eq!(
                    decode_ax25(&soft(&levels), 0.0, &[g3ruh, g3ruh]).unwrap(),
                    decoded
                );
            }
        }
    }
    let frames = vec![full_frame(b"first"), full_frame(b"second")];
    let levels = framed_stream(&frames, false, 1, 1);
    assert_eq!(
        decode_ax25_levels(&levels, false).unwrap(),
        vec![frames[1].clone(), frames[0].clone()]
    );
}

#[test]
fn framing_limits_alignment_abort_and_overlapping_flags() {
    let lengths = [13, 14, 16, 1021, 1022];
    let frames: Vec<Vec<u8>> = lengths
        .iter()
        .map(|length| with_fcs(vec![0xff; *length]))
        .collect();
    let levels = framed_stream(&frames, false, 0, 8);
    assert_eq!(
        decode_ax25_levels(&levels, false).unwrap(),
        vec![frames[1].clone(), frames[2].clone(), frames[3].clone()]
    );
    assert!(
        decode_ax25(&soft(&levels), 0.0, &[false])
            .unwrap()
            .is_empty()
    );
    for length in [0, 1, 7, 8, 15, 16, 63, 128, 57600] {
        for value in [0, 1] {
            assert!(
                decode_ax25_levels(&vec![value; length], false)
                    .unwrap()
                    .is_empty()
            );
        }
    }
    assert!(decode_ax25_levels(&[2], false).is_err());
    assert!(decode_ax25(&[], 0.0, &[]).is_err());
    assert!(hdlc_unstuff(&[1; 6]).is_err());
    assert_eq!(hdlc_unstuff(&[1; 5]).unwrap(), [1; 5]);
    assert_eq!(
        hdlc_unstuff(&[1, 1, 1, 1, 1, 0, 0]).unwrap(),
        [1, 1, 1, 1, 1, 0]
    );
    assert!(bits_to_bytes(&[0; 7], true).is_err());
    assert!(bits_to_bytes(&[2; 8], true).is_err());
    let mut malformed = HDLC_FLAG.to_vec();
    malformed.extend([1; 140]);
    malformed.extend(HDLC_FLAG);
    assert!(
        decode_ax25_levels(&nrzi(&malformed, 0), false)
            .unwrap()
            .is_empty()
    );
    let mut overlapping = HDLC_FLAG.to_vec();
    overlapping.extend(&HDLC_FLAG[1..]);
    assert!(
        decode_ax25_levels(&nrzi(&overlapping, 0), false)
            .unwrap()
            .is_empty()
    );
    let mut corrupt = full_frame(&[0xff; 100]);
    *corrupt.last_mut().unwrap() ^= 1;
    for g3ruh in [false, true] {
        assert!(
            decode_ax25_levels(&framed_stream(&[corrupt.clone()], g3ruh, 0, 8), g3ruh)
                .unwrap()
                .is_empty()
        );
    }
}

#[test]
fn seeded_framing_roundtrips_all_byte_values_and_truncation() {
    let mut state = 0x827634a5719bcd01u64;
    for index in 0..120 {
        let length = index * 8;
        let information: Vec<u8> = (0..length)
            .map(|_| {
                state ^= state << 13;
                state ^= state >> 7;
                state ^= state << 17;
                state as u8
            })
            .collect();
        let frame = full_frame(&information);
        for g3ruh in [false, true] {
            let levels = framed_stream(std::slice::from_ref(&frame), g3ruh, (index % 2) as u8, 8);
            assert_eq!(
                decode_ax25(&soft(&levels), 0.0, &[false, true])
                    .unwrap()
                    .as_slice(),
                std::slice::from_ref(&frame)
            );
            for cut in [0, 8, levels.len() / 2] {
                assert!(
                    decode_ax25(&soft(&levels[..cut]), 0.0, &[false, true])
                        .unwrap()
                        .is_empty()
                );
            }
        }
    }
}

#[test]
fn nonfinite_comparison_semantics_are_explicit() {
    let frame = full_frame(b"finite symbols");
    let levels = framed_stream(std::slice::from_ref(&frame), false, 0, 8);
    let mut symbols: Vec<f64> = levels
        .iter()
        .map(|bit| {
            if *bit == 1 {
                f64::INFINITY
            } else {
                f64::NEG_INFINITY
            }
        })
        .collect();
    assert_eq!(
        decode_ax25(&symbols, 0.0, &[false]).unwrap().as_slice(),
        std::slice::from_ref(&frame)
    );
    assert_eq!(
        decode_ax25(&symbols, f64::INFINITY, &[false]).unwrap(),
        [frame]
    );
    assert!(
        decode_ax25(&symbols, f64::NAN, &[false, true])
            .unwrap()
            .is_empty()
    );
    symbols.fill(f64::NAN);
    assert!(
        decode_ax25(&symbols, 0.0, &[false, true])
            .unwrap()
            .is_empty()
    );
}

fn packet(apid: u16, data: &[u8]) -> Vec<u8> {
    assert!(!data.is_empty());
    let mut packet = apid.to_be_bytes().to_vec();
    packet.extend([0xc0, 7]);
    packet.extend(((data.len() - 1) as u16).to_be_bytes());
    packet.extend(data);
    packet
}

#[test]
fn ccsds_header_exact_length_and_version() {
    let packet = packet(0x321, b"payload");
    let parsed = parse_ccsds_space_packet(&packet, true).unwrap();
    assert_eq!(parsed.apid, 0x321);
    assert_eq!(parsed.sequence_count, 7);
    assert_eq!(parsed.sequence_flags, 3);
    assert_eq!(parsed.packet_data_length, 7);
    assert_eq!(parsed.total_packet_length, 13);
    for length in 0..packet.len() {
        assert!(parse_ccsds_space_packet(&packet[..length], false).is_none());
    }
    let mut trailing = packet.clone();
    trailing.push(0);
    assert!(parse_ccsds_space_packet(&trailing, true).is_none());
    assert!(parse_ccsds_space_packet(&trailing, false).is_some());
    for version in 1..8 {
        let mut wrong = packet.clone();
        wrong[0] |= version << 5;
        assert!(parse_ccsds_space_packet(&wrong, true).is_none());
    }
    let maximal = super::tests::packet(0x7ff, &vec![0; 65536]);
    assert_eq!(
        parse_ccsds_space_packet(&maximal, true)
            .unwrap()
            .total_packet_length,
        65542
    );
    assert!(parse_ccsds_space_packet(&[0; 6], true).is_none());
    assert!(parse_ccsds_space_packet(&[0; 7], true).is_some());
}

#[test]
fn raw_ccsds_requires_integrity_offsets_polarity_and_dedup_order() {
    let first = packet(42, b"first");
    let second = packet(7, b"second");
    let mut bits = vec![1, 0, 1];
    bits.extend(byte_bits(&first, false));
    bits.extend(byte_bits(&second, false));
    bits.extend(byte_bits(&first, false));
    let mut config = RawCcsdsConfig {
        allowed_apids: Some(vec![42, 7]),
        bit_offsets: (0..8).collect(),
        invert_modes: vec![false, true],
        maximum_packet_bytes: 65542,
        validation_name: "fixture_exact_bytes".into(),
    };
    let validator =
        |candidate: &[u8], _: &CcsdsPrimaryHeader| candidate == first || candidate == second;
    for invert in [false, true] {
        let symbols = soft(
            &bits
                .iter()
                .map(|bit| bit ^ u8::from(invert))
                .collect::<Vec<_>>(),
        );
        let results = decode_raw_ccsds(&symbols, 0.0, &config, &validator).unwrap();
        assert_eq!(
            results.iter().map(|r| &r.frame).collect::<Vec<_>>(),
            [&first, &second]
        );
        assert!(
            decode_raw_ccsds(&symbols, 0.0, &config, &|_, _| false)
                .unwrap()
                .is_empty()
        );
    }
    config.allowed_apids = Some(vec![7]);
    assert_eq!(
        decode_raw_ccsds(&soft(&bits), 0.0, &config, &validator).unwrap()[0].frame,
        second
    );
    config.allowed_apids = Some(vec![]);
    assert!(config.validate().is_err());
    config.allowed_apids = None;
    config.bit_offsets = vec![8];
    assert!(config.validate().is_err());
}

#[test]
fn ax25_ccsds_outer_integrity_and_inner_length_required() {
    let packet = packet(42, b"ccsds");
    let frame = full_frame(&packet);
    let symbols = soft(&framed_stream(std::slice::from_ref(&frame), true, 0, 8));
    assert_eq!(
        decode_ax25_ccsds(&symbols, 0.0, &[false, true], Some(&[42]), None).unwrap()[0].frame,
        frame
    );
    assert!(
        decode_ax25_ccsds(&symbols, 0.0, &[false, true], Some(&[1]), None)
            .unwrap()
            .is_empty()
    );
    assert!(
        decode_ax25_ccsds(
            &symbols,
            0.0,
            &[false, true],
            None,
            Some((&|_, _| false, "test"))
        )
        .unwrap()
        .is_empty()
    );
    let mut trailing = packet.clone();
    trailing.push(0);
    assert!(
        decode_ax25_ccsds(
            &soft(&framed_stream(&[full_frame(&trailing)], false, 0, 8)),
            0.0,
            &[false],
            None,
            None
        )
        .unwrap()
        .is_empty()
    );
}

#[test]
fn fixed_sync_includes_marker_transform_hamming_and_integrity() {
    let bytes = [0x1a, 0x07, 0xff];
    let mut bits = vec![1, 1, 0];
    bits.extend(byte_bits(&bytes, false));
    bits.extend(byte_bits(&bytes, false));
    let mut config = FixedSyncConfig {
        protocol_id: "fixture".into(),
        syncword: byte_bits(&bytes[..1], false),
        frame_bits: 24,
        lsb_first: false,
        maximum_sync_hamming: 0,
        validation_name: "fixture_exact_bytes".into(),
    };
    assert_eq!(
        decode_fixed_sync(&soft(&bits), 0.0, &config, None, &|v| v == bytes).unwrap()[0].frame,
        bytes
    );
    assert!(
        decode_fixed_sync(&soft(&bits), 0.0, &config, None, &|_| false)
            .unwrap()
            .is_empty()
    );
    let inverted: Vec<u8> = bits.iter().map(|b| b ^ 1).collect();
    let transform = |v: &[u8]| Ok(v.iter().map(|b| b ^ 1).collect());
    assert_eq!(
        decode_fixed_sync(&soft(&inverted), 0.0, &config, Some(&transform), &|v| v
            == bytes)
        .unwrap()
        .len(),
        1
    );
    assert!(
        decode_fixed_sync(
            &soft(&bits),
            0.0,
            &config,
            Some(&|_| Ok(vec![2; 24])),
            &|_| true
        )
        .is_err()
    );
    config.syncword[0] ^= 1;
    assert!(
        decode_fixed_sync(&soft(&bits), 0.0, &config, None, &|v| v == bytes)
            .unwrap()
            .is_empty()
    );
    config.maximum_sync_hamming = 1;
    assert_eq!(
        decode_fixed_sync(&soft(&bits), 0.0, &config, None, &|v| v == bytes)
            .unwrap()
            .len(),
        1
    );
    config.frame_bits = 7;
    assert!(config.validate().is_err());
}

fn tm(data: &[u8], status: u16, ocf: Option<[u8; 4]>, fecf: bool) -> Vec<u8> {
    let mut raw = vec![0x15, 0x56 | u8::from(ocf.is_some()), 17, 29];
    raw.extend(status.to_be_bytes());
    raw.extend(data);
    if let Some(ocf) = ocf {
        raw.extend(ocf);
    }
    if fecf {
        raw.extend(compute_tm_fecf(&raw).to_be_bytes());
    }
    raw
}

#[test]
fn tm_primary_secondary_ocf_and_fecf_fields() {
    let raw = tm(b"\x02shdata!", 0x9800, Some([1, 2, 3, 4]), true);
    let config = TmTransferFrameConfig {
        frame_length_bytes: raw.len(),
        fecf_present: true,
    };
    let parsed = parse_tm_transfer_frame(&raw, &config).unwrap();
    assert_eq!(parsed.primary_header.spacecraft_id, 0x155);
    assert_eq!(parsed.primary_header.virtual_channel_id, 3);
    assert_eq!(parsed.primary_header.master_channel_frame_count, 17);
    assert_eq!(parsed.primary_header.virtual_channel_frame_count, 29);
    assert_eq!(parsed.secondary_header.unwrap().data, b"sh");
    assert_eq!(parsed.data_field, b"data!");
    assert_eq!(parsed.operational_control_field.unwrap(), [1, 2, 3, 4]);
    assert!(
        validate_tm_transfer_frame(&raw, &config, None)
            .unwrap()
            .accepted
    );
    let mut corrupt = raw.clone();
    let size = corrupt.len();
    corrupt[size - 3] ^= 1;
    let validation = validate_tm_transfer_frame(
        &corrupt,
        &config,
        Some((&|_, _| Ok(true), "must_not_override_bad_fecf")),
    )
    .unwrap();
    assert!(!validation.accepted);
    assert_eq!(validation.rejection_reason.as_deref(), Some("invalid_fecf"));
}

#[test]
fn tm_structure_and_integrity_are_separate_and_fail_closed() {
    let raw = tm(b"candidate", 0x1800, None, false);
    let config = TmTransferFrameConfig {
        frame_length_bytes: raw.len(),
        fecf_present: false,
    };
    assert!(parse_tm_transfer_frame(&raw, &config).is_ok());
    assert_eq!(
        validate_tm_transfer_frame(&raw, &config, None)
            .unwrap()
            .rejection_reason
            .as_deref(),
        Some("integrity_evidence_required")
    );
    assert!(
        validate_tm_transfer_frame(&raw, &config, Some((&|_, _| Ok(true), "fixture_integrity")))
            .unwrap()
            .accepted
    );
    for (response, expected) in [
        (Ok(false), "integrity_validator_rejected"),
        (Err("fixture_error".into()), "integrity_validator_error"),
    ] {
        assert_eq!(
            validate_tm_transfer_frame(
                &raw,
                &config,
                Some((&|_, _| response.clone(), "fixture_integrity"))
            )
            .unwrap()
            .rejection_reason
            .as_deref(),
            Some(expected)
        );
    }
    assert!(validate_tm_transfer_frame(&raw, &config, Some((&|_, _| Ok(true), " "))).is_err());
    for (status, expected) in [
        (0x3800, "packet_order_flag_reserved_for_packet_data"),
        (0x1000, "invalid_packet_data_segment_length_id"),
        (0x1803, "first_header_pointer_out_of_range"),
    ] {
        let candidate = tm(b"abc", status, None, false);
        assert_eq!(
            parse_tm_transfer_frame(
                &candidate,
                &TmTransferFrameConfig {
                    frame_length_bytes: candidate.len(),
                    fecf_present: false
                }
            )
            .unwrap_err(),
            expected
        );
    }
    for status in [0x1ffe, 0x1fff, 0x6000, 0x4000] {
        let candidate = tm(b"abc", status, None, false);
        assert!(
            parse_tm_transfer_frame(
                &candidate,
                &TmTransferFrameConfig {
                    frame_length_bytes: candidate.len(),
                    fecf_present: false
                }
            )
            .is_ok()
        );
    }
    assert!(parse_tm_transfer_frame(&raw[..raw.len() - 1], &config).is_err());
    let mut wrong_version = raw.clone();
    wrong_version[0] |= 0x40;
    assert_eq!(
        parse_tm_transfer_frame(&wrong_version, &config).unwrap_err(),
        "unsupported_transfer_frame_version"
    );
    for length in [0, 1, 6, 2049, usize::MAX] {
        assert!(
            TmTransferFrameConfig {
                frame_length_bytes: length,
                fecf_present: false
            }
            .validate()
            .is_err()
        );
    }
}

#[test]
fn tm_secondary_header_and_empty_data_boundaries() {
    for (data, reason) in [
        (
            b"\x40abc".as_slice(),
            "unsupported_secondary_header_version",
        ),
        (b"\x00abc".as_slice(), "missing_secondary_header_data"),
        (b"\x03abc".as_slice(), "invalid_secondary_header_length"),
    ] {
        let raw = tm(data, 0x9800, None, true);
        let config = TmTransferFrameConfig {
            frame_length_bytes: raw.len(),
            fecf_present: true,
        };
        assert_eq!(parse_tm_transfer_frame(&raw, &config).unwrap_err(), reason);
    }
    // Legal global minimum can still have no data after a present OCF.
    let raw = tm(&[], 0x1800, Some([0; 4]), true);
    assert_eq!(
        parse_tm_transfer_frame(
            &raw,
            &TmTransferFrameConfig {
                frame_length_bytes: raw.len(),
                fecf_present: true
            }
        )
        .unwrap_err(),
        "missing_transfer_frame_data_field"
    );
}

#[test]
fn tm_marker_removed_bit_alignment_inversion_and_hamming() {
    let raw = tm(b"payload", 0x1800, None, true);
    let marker = byte_bits(&[0x1a, 0xcf, 0xfc, 0x1d], false);
    let mut bits = vec![1, 0, 1];
    bits.extend_from_slice(&marker);
    bits.extend(byte_bits(&raw, false));
    bits.extend_from_slice(&marker);
    bits.extend(byte_bits(&raw, false));
    let mut config = CcsdsTmDecoderConfig {
        frame: TmTransferFrameConfig {
            frame_length_bytes: raw.len(),
            fecf_present: true,
        },
        sync_marker: marker,
        maximum_sync_hamming: 0,
        invert_modes: vec![false, true, false],
    };
    for polarity in [0, 1] {
        let symbols = soft(&bits.iter().map(|b| b ^ polarity).collect::<Vec<_>>());
        let output = decode_ccsds_tm(&symbols, 0.0, &config, None).unwrap();
        assert_eq!(output.len(), 1);
        assert_eq!(output[0].frame, raw);
        assert_eq!(output[0].validation_layers.len(), 3);
    }
    config.sync_marker[0] ^= 1;
    assert!(
        decode_ccsds_tm(&soft(&bits), 0.0, &config, None)
            .unwrap()
            .is_empty()
    );
    config.maximum_sync_hamming = 1;
    assert_eq!(
        decode_ccsds_tm(&soft(&bits), 0.0, &config, None).unwrap()[0].frame,
        raw
    );
    config.frame.fecf_present = false;
    assert!(decode_ccsds_tm(&soft(&bits), 0.0, &config, None).is_err());
}
