use super::*;

fn csp_v1(mode: CspCrc32Mode) -> SpaceLinkConfig {
    SpaceLinkConfig::CspV1 { crc32: mode }
}

fn aos(length: usize, insert: usize, ocf: bool, fecf: bool) -> SpaceLinkConfig {
    SpaceLinkConfig::Aos {
        frame_length_bytes: length,
        insert_zone_length_bytes: insert,
        operational_control_field: ocf,
        frame_error_control_field: fecf,
        frame_header_error_control: false,
        security: false,
    }
}

fn uslp(expected: Option<usize>, insert: usize, fecf: bool) -> SpaceLinkConfig {
    SpaceLinkConfig::Uslp {
        expected_frame_length_bytes: expected,
        insert_zone_length_bytes: insert,
        frame_error_control_field: fecf,
        security: false,
    }
}

#[test]
fn config_is_strictly_tagged_and_reports_integrity() {
    let config: SpaceLinkConfig =
        serde_json::from_str(r#"{"type":"csp_v2","crc32":"required_header_and_payload"}"#).unwrap();
    assert!(config.has_integrity_check());
    assert_eq!(
        serde_json::to_value(&config).unwrap(),
        serde_json::json!({
            "type": "csp_v2",
            "crc32": "required_header_and_payload"
        })
    );
    assert!(
        serde_json::from_str::<SpaceLinkConfig>(
            r#"{"type":"csp_v2","crc32":"absent","unexpected":true}"#
        )
        .is_err()
    );
    assert!(!csp_v1(CspCrc32Mode::Absent).has_integrity_check());
    assert!(!aos(7, 0, false, false).has_integrity_check());
    assert!(uslp(None, 0, true).has_integrity_check());
}

#[test]
fn libcsp_crc32c_check_vector_and_explicit_v1_scope() {
    // The libcsp v2.1 lookup table is reflected Castagnoli CRC-32C.
    assert_eq!(csp_crc32c(b"123456789"), 0xe306_9283);

    // Official libcsp v1 layout: prio=2, src=17, dst=3, dport=42,
    // sport=5, flags=CSP_FCRC32. CRC literals were independently computed
    // from the official v2.1 csp_crc32.c algorithm.
    let mut header_and_payload = hex::decode("a23a8501414243de918426").unwrap();
    let decoded = csp_v1(CspCrc32Mode::RequiredHeaderAndPayload)
        .decode(&header_and_payload)
        .unwrap();
    assert_eq!(decoded.payload, b"ABC");
    assert_eq!(decoded.csp_crc32, Some(0xde91_8426));
    assert!(
        decoded
            .validation_layers
            .contains(&"csp_crc32c_header_and_payload".to_string())
    );
    match decoded.header {
        SpaceLinkHeader::Csp(header) => {
            assert_eq!(header.version, CspVersion::V1);
            assert_eq!(header.priority, 2);
            assert_eq!(header.source, 17);
            assert_eq!(header.destination, 3);
            assert_eq!(header.destination_port, 42);
            assert_eq!(header.source_port, 5);
            assert_eq!(header.flags, 1);
        }
        other => panic!("unexpected header: {other:?}"),
    }

    assert!(
        csp_v1(CspCrc32Mode::RequiredPayloadOnlyLegacy)
            .decode(&header_and_payload)
            .is_err()
    );
    header_and_payload[7..].copy_from_slice(&0x8839_a97fu32.to_be_bytes());
    let legacy = csp_v1(CspCrc32Mode::RequiredPayloadOnlyLegacy)
        .decode(&header_and_payload)
        .unwrap();
    assert!(
        legacy
            .validation_layers
            .contains(&"csp_crc32c_payload_only_legacy".to_string())
    );
    assert!(
        csp_v1(CspCrc32Mode::RequiredHeaderAndPayload)
            .decode(&header_and_payload)
            .is_err()
    );
}

#[test]
fn csp_v2_parses_six_octet_official_layout() {
    // prio=1, dst=0x234, src=0x123, dport=42, sport=5, FCRC32.
    let frame = hex::decode("4234048ea1414142434cef685e").unwrap();
    let decoded = SpaceLinkConfig::CspV2 {
        crc32: CspCrc32Mode::RequiredHeaderAndPayload,
    }
    .decode(&frame)
    .unwrap();
    match decoded.header {
        SpaceLinkHeader::Csp(header) => {
            assert_eq!(header.version, CspVersion::V2);
            assert_eq!(header.priority, 1);
            assert_eq!(header.destination, 0x234);
            assert_eq!(header.source, 0x123);
            assert_eq!(header.destination_port, 42);
            assert_eq!(header.source_port, 5);
        }
        other => panic!("unexpected header: {other:?}"),
    }
}

#[test]
fn csp_structural_only_has_no_integrity_layer() {
    let decoded = csp_v1(CspCrc32Mode::Absent)
        .decode(&hex::decode("a23a8500414243").unwrap())
        .unwrap();
    assert_eq!(decoded.payload, b"ABC");
    assert_eq!(decoded.csp_crc32, None);
    assert!(
        decoded
            .validation_layers
            .iter()
            .all(|layer| !layer.contains("crc"))
    );
}

#[test]
fn csp_rejects_truncation_flag_mismatch_and_extensions() {
    assert!(csp_v1(CspCrc32Mode::Absent).decode(&[0, 0, 0]).is_err());
    assert!(
        csp_v1(CspCrc32Mode::Absent)
            .decode(&hex::decode("0000000100000000").unwrap())
            .is_err()
    );
    assert!(
        csp_v1(CspCrc32Mode::RequiredHeaderAndPayload)
            .decode(&hex::decode("0000000000000000").unwrap())
            .is_err()
    );
    for unsupported_flag in [0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80] {
        let mut frame = vec![0, 0, 0, unsupported_flag];
        frame.extend_from_slice(b"data");
        assert!(csp_v1(CspCrc32Mode::Absent).decode(&frame).is_err());
    }
}

#[test]
fn aos_issue5_literal_frame_decodes_split_ten_bit_scid() {
    // Issue-5 primary header: TFVN=01, SCID=0x2ab (MSBs in bits 42-43),
    // VCID=17, VCFC=0x123456, replay=1, cycle-use=1, cycle=9.
    let frame = hex::decode("6ad1123456e9aabb010203102030406795").unwrap();
    assert!(protocol::validate_tm_fecf(&frame));
    let decoded = aos(17, 2, true, true).decode(&frame).unwrap();
    assert_eq!(decoded.insert_zone, [0xaa, 0xbb]);
    assert_eq!(decoded.payload, [1, 2, 3]);
    assert_eq!(
        decoded.operational_control_field,
        Some([0x10, 0x20, 0x30, 0x40])
    );
    assert_eq!(decoded.frame_error_control_field, Some(0x6795));
    assert!(
        decoded
            .validation_layers
            .contains(&"ccsds_aos_fecf_crc16".to_string())
    );
    assert!(
        decoded
            .validation_layers
            .contains(&"ccsds_aos_primary_header_issue_5_semantics".to_string())
    );
    assert!(
        decoded
            .validation_layers
            .contains(&"ccsds_aos_configured_without_sdls".to_string())
    );
    match decoded.header {
        SpaceLinkHeader::Aos(header) => {
            assert_eq!(header.transfer_frame_version_number, 1);
            assert_eq!(header.spacecraft_id, 0x2ab);
            assert_eq!(header.master_channel_id, 0x6ab);
            assert_eq!(header.virtual_channel_id, 17);
            assert_eq!(header.virtual_channel_frame_count, 0x123456);
            assert!(header.replay);
            assert!(header.virtual_channel_frame_count_cycle_used);
            assert_eq!(header.virtual_channel_frame_count_cycle, 9);
        }
        other => panic!("unexpected header: {other:?}"),
    }
}

#[test]
fn aos_issue5_does_not_silently_apply_issue4_eight_bit_scid_semantics() {
    // The first frame has an Issue-4-compatible zero SCID-MSB. The second sets
    // Issue-5 bits 42-43; an old parser would incorrectly report both as 0xab.
    let low = aos(7, 0, false, false)
        .decode(&hex::decode("6ad10000000099").unwrap())
        .unwrap();
    let high = aos(7, 0, false, false)
        .decode(&hex::decode("6ad10000002099").unwrap())
        .unwrap();
    match (low.header, high.header) {
        (SpaceLinkHeader::Aos(low), SpaceLinkHeader::Aos(high)) => {
            assert_eq!(low.spacecraft_id, 0x0ab);
            assert_eq!(high.spacecraft_id, 0x2ab);
        }
        other => panic!("unexpected headers: {other:?}"),
    }
}

#[test]
fn aos_rejects_invalid_managed_modes_and_header_invariants() {
    assert!(
        SpaceLinkConfig::Aos {
            frame_length_bytes: 9,
            insert_zone_length_bytes: 0,
            operational_control_field: false,
            frame_error_control_field: false,
            frame_header_error_control: true,
            security: false,
        }
        .validate()
        .is_err()
    );
    assert!(
        SpaceLinkConfig::Aos {
            frame_length_bytes: 9,
            insert_zone_length_bytes: 0,
            operational_control_field: false,
            frame_error_control_field: false,
            frame_header_error_control: false,
            security: true,
        }
        .validate()
        .is_err()
    );
    assert!(aos(6, 0, false, false).validate().is_err());
    assert!(
        aos(7, 0, false, false)
            .decode(&hex::decode("aad10000000099").unwrap())
            .is_err()
    );
    assert!(
        aos(7, 0, false, false)
            .decode(&hex::decode("6ad10000000199").unwrap())
            .is_err()
    );
    assert!(
        aos(7, 0, false, false)
            .decode(&hex::decode("6aff0000000099").unwrap())
            .is_err()
    );
}

#[test]
fn aos_rejects_bad_fecf_wrong_length_and_sdls_report_ocf() {
    let frame = hex::decode("6ad1123456e9aabb010203102030406795").unwrap();
    let mut bad_fecf = frame.clone();
    *bad_fecf.last_mut().unwrap() ^= 1;
    assert!(aos(17, 2, true, true).decode(&bad_fecf).is_err());
    assert!(aos(18, 2, true, true).decode(&frame).is_err());

    let sdls_ocf = hex::decode("6ad10000000001c0000000").unwrap();
    assert!(aos(11, 0, true, false).decode(&sdls_ocf).is_err());
}

#[test]
fn ccsds_fecf_published_check_value_is_reused() {
    let mut value = b"123456789".to_vec();
    value.extend_from_slice(&0x29b1u16.to_be_bytes());
    assert!(protocol::validate_tm_fecf(&value));
}

#[test]
fn uslp_literal_full_transfer_frame_decodes_all_regions() {
    // Non-truncated header: TFVN=12, SCID=0x1234, source ID, VCID=5,
    // MAP=9, 20-octet frame, expedited user data, OCF, 2-byte VCF count.
    // TFDF uses rule 111 (no segmentation), UPID=2.
    let frame = hex::decode("c12340b200138a0102eee2deadbe001122336ef1").unwrap();
    assert!(protocol::validate_tm_fecf(&frame));
    let decoded = uslp(Some(20), 1, true).decode(&frame).unwrap();
    assert_eq!(decoded.insert_zone, [0xee]);
    assert_eq!(decoded.payload, [0xde, 0xad, 0xbe]);
    assert_eq!(
        decoded.operational_control_field,
        Some([0, 0x11, 0x22, 0x33])
    );
    assert_eq!(decoded.frame_error_control_field, Some(0x6ef1));
    assert_eq!(
        decoded.data_field_header,
        Some(UslpDataFieldHeader {
            construction_rule: UslpTfdzConstructionRule::NoSegmentation,
            protocol_identifier: 2,
            first_header_or_last_valid_octet_pointer: None,
        })
    );
    assert!(
        decoded
            .validation_layers
            .contains(&"ccsds_uslp_configured_without_sdls".to_string())
    );
    match decoded.header {
        SpaceLinkHeader::Uslp(header) => {
            assert_eq!(header.transfer_frame_version_number, 12);
            assert_eq!(header.spacecraft_id, 0x1234);
            assert_eq!(header.spacecraft_identifier_use, UslpIdentifierUse::Source);
            assert_eq!(header.virtual_channel_id, 5);
            assert_eq!(header.map_id, 9);
            assert_eq!(header.frame_length_bytes, 20);
            assert!(header.bypass_sequence_control);
            assert!(!header.protocol_control_command);
            assert!(header.operational_control_field_present);
            assert_eq!(header.virtual_channel_frame_count_length_bytes, 2);
            assert_eq!(header.virtual_channel_frame_count, Some(0x0102));
        }
        other => panic!("unexpected header: {other:?}"),
    }
}

#[test]
fn uslp_parses_three_octet_tfdf_header_and_checks_pointer() {
    // 12-byte full frame, no count/OCF/FECF: header 7, rule 000 + FHP 1,
    // two-octet TFDZ.
    let frame = hex::decode("c0000020000b000100010203").unwrap();
    let decoded = uslp(Some(12), 0, false).decode(&frame).unwrap();
    assert_eq!(decoded.payload, [2, 3]);
    assert_eq!(
        decoded
            .data_field_header
            .unwrap()
            .first_header_or_last_valid_octet_pointer,
        Some(1)
    );

    let out_of_bounds = hex::decode("c0000020000b000100020203").unwrap();
    assert!(uslp(Some(12), 0, false).decode(&out_of_bounds).is_err());
    let sentinel = hex::decode("c0000020000b0001ffff0203").unwrap();
    assert!(uslp(Some(12), 0, false).decode(&sentinel).is_ok());
}

#[test]
fn uslp_rejects_truncation_lengths_reserved_bits_and_reserved_flag_state() {
    let valid = hex::decode("c0000020000700e0").unwrap();
    assert!(uslp(None, 0, false).decode(&valid).is_ok());

    let mut truncated_header = valid.clone();
    truncated_header[3] |= 1;
    assert!(uslp(None, 0, false).decode(&truncated_header).is_err());
    let mut bad_encoded_length = valid.clone();
    bad_encoded_length[5] = 8;
    assert!(uslp(None, 0, false).decode(&bad_encoded_length).is_err());
    let mut reserved_spares = valid.clone();
    reserved_spares[6] |= 0x10;
    assert!(uslp(None, 0, false).decode(&reserved_spares).is_err());
    let mut reserved_flag_state = valid.clone();
    reserved_flag_state[6] = 0x40;
    assert!(uslp(None, 0, false).decode(&reserved_flag_state).is_err());
    assert!(uslp(Some(9), 0, false).decode(&valid).is_err());
}

#[test]
fn uslp_rejects_truncated_count_pointer_bad_fecf_idle_and_sdls() {
    // Count length 7 cannot fit in this 8-byte frame.
    let count_truncated = hex::decode("c0000020000707e0").unwrap();
    assert!(uslp(None, 0, false).decode(&count_truncated).is_err());

    // Rule 000 requires its two-octet pointer.
    let pointer_truncated = hex::decode("c000002000070000").unwrap();
    assert!(uslp(None, 0, false).decode(&pointer_truncated).is_err());

    let mut bad_fecf = hex::decode("c12340b200138a0102eee2deadbe001122336ef1").unwrap();
    *bad_fecf.last_mut().unwrap() ^= 1;
    assert!(uslp(Some(20), 1, true).decode(&bad_fecf).is_err());

    let idle = hex::decode("c00007e0000700e0").unwrap();
    assert!(uslp(None, 0, false).decode(&idle).is_err());

    let sdls_ocf = hex::decode("c0000020000b08e0c0000000").unwrap();
    assert!(uslp(None, 0, false).decode(&sdls_ocf).is_err());
}

#[test]
fn uslp_security_and_impossible_lengths_fail_configuration_validation() {
    assert!(
        SpaceLinkConfig::Uslp {
            expected_frame_length_bytes: None,
            insert_zone_length_bytes: 0,
            frame_error_control_field: false,
            security: true,
        }
        .validate()
        .is_err()
    );
    assert!(uslp(Some(7), 0, false).validate().is_err());
    assert!(uslp(Some(65_537), 0, false).validate().is_err());
}

#[test]
fn adversarial_lengths_and_trailer_flags_never_panic() {
    let aos_frame = hex::decode("6ad1123456e9aabb010203102030406795").unwrap();
    for end in 0..=aos_frame.len() {
        for config in [
            aos(end, 0, false, false),
            aos(end, usize::MAX, true, true),
            aos(end, end.saturating_add(1), true, true),
        ] {
            assert!(std::panic::catch_unwind(|| config.decode(&aos_frame[..end])).is_ok());
        }
    }

    // Exercise every possible flags/count-length octet at every short length.
    for length in 0..=32usize {
        for control in 0..=u8::MAX {
            let mut frame = vec![0u8; length];
            if length >= 7 {
                frame[0] = 0xc0;
                let encoded = u16::try_from(length - 1).unwrap();
                frame[4..6].copy_from_slice(&encoded.to_be_bytes());
                frame[6] = control;
            }
            for config in [
                uslp(None, 0, false),
                uslp(Some(length), usize::MAX, true),
                uslp(Some(length), length.saturating_add(1), control & 1 != 0),
            ] {
                assert!(std::panic::catch_unwind(|| config.decode(&frame)).is_ok());
            }
        }
    }
}
