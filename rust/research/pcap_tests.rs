use super::*;

#[test]
fn complete_report_matches_frozen_python_reference() {
    // Python pcap_ipv4_audit.audit_pcap_echo_requests, captured 2026-09-15.
    // Literal input and expected hashes avoid a shared checksum-generator oracle.
    let bytes = hex::decode(concat!(
        "a1b2c3d40002000400000000000000000000ffff00000065",
        "000000640003d090000000200000002045000020007b00004001e50ec0a80a01c0a80a02080010140007000b74657374",
        "000000650003d090000000200000002045000020007b00004001e50ec0a80a01c0a80a02080010140007000b74657374",
        "000000660003d090000000200000002045000020007b00004001e50ec0a80a01c0a80a02080010140007000b74657375"
    )).unwrap();
    let expected = json!({
        "schema_version":"telemetry-yield-pcap-ipv4-audit-v1","path":"reference.pcap",
        "pcap_sha256":"3809863198fd2ac9590a6deaf576f527bf448518b04c78572a76b1db0898b7cd",
        "pcap_size_bytes":168,"link_type":101,"records":3,"accepted_echo_requests":2,
        "unique_echo_requests":1,"duplicate_echo_requests":1,"rejected_records":1,
        "rejection_reasons":{"invalid ICMP checksum":1},"first_timestamp":100.25,"last_timestamp":102.25,
        "packets":[{"sha256":"7d7cf28f27ab69bde9331076158ced9cab7871beb17bf728e3680405e0179953",
        "length_bytes":32,"source":"192.168.10.1","destination":"192.168.10.2","icmp_identifier":7,"icmp_sequence":11}]
    });
    assert_eq!(
        audit_pcap_bytes(&bytes, "reference.pcap").unwrap(),
        expected
    );
}

fn echo(payload: &[u8]) -> Vec<u8> {
    let mut icmp = vec![8, 0, 0, 0, 0, 7, 0, 11];
    icmp.extend_from_slice(payload);
    let sum = internet_checksum(&icmp);
    icmp[2..4].copy_from_slice(&sum.to_be_bytes());
    let mut ip = vec![0_u8; 20];
    ip[0] = 0x45;
    ip[2..4].copy_from_slice(&((20 + icmp.len()) as u16).to_be_bytes());
    ip[4..6].copy_from_slice(&123_u16.to_be_bytes());
    ip[8] = 64;
    ip[9] = 1;
    ip[12..16].copy_from_slice(&[192, 168, 10, 1]);
    ip[16..20].copy_from_slice(&[192, 168, 10, 2]);
    let sum = internet_checksum(&ip);
    ip[10..12].copy_from_slice(&sum.to_be_bytes());
    ip.extend(icmp);
    ip
}

fn pcap(packets: &[Vec<u8>], little: bool, nano: bool, link: u32) -> Vec<u8> {
    let int = |v: u32| {
        if little {
            v.to_le_bytes()
        } else {
            v.to_be_bytes()
        }
    };
    let short = |v: u16| {
        if little {
            v.to_le_bytes()
        } else {
            v.to_be_bytes()
        }
    };
    let mut out = int(if nano { 0xa1b23c4d } else { 0xa1b2c3d4 }).to_vec();
    out.extend(short(2));
    out.extend(short(4));
    for v in [0, 0, 65535, link] {
        out.extend(int(v));
    }
    for (i, packet) in packets.iter().enumerate() {
        for v in [
            100 + i as u32,
            if nano { 250_000_000 } else { 250_000 },
            packet.len() as u32,
            packet.len() as u32,
        ] {
            out.extend(int(v));
        }
        out.extend(packet);
    }
    out
}

#[test]
fn checksum_handles_rfc_example_and_odd_lengths() {
    assert_eq!(
        internet_checksum(&[0x00, 0x01, 0xf2, 0x03, 0xf4, 0xf5, 0xf6, 0xf7]),
        0x220d
    );
    assert_eq!(internet_checksum(&[]), 0xffff);
    assert_eq!(internet_checksum(&[1]), 0xfeff);
    assert_eq!(internet_checksum(&vec![255; 1_000_000]), 0);
}

#[test]
fn all_four_pcap_layouts_preserve_strict_packets_and_timestamps() {
    for little in [false, true] {
        for nano in [false, true] {
            let packet = echo(b"test");
            let result = audit_pcap_bytes(
                &pcap(&[packet.clone(), packet], little, nano, 101),
                "capture.pcap",
            )
            .unwrap();
            assert_eq!(result["records"], 2);
            assert_eq!(result["accepted_echo_requests"], 2);
            assert_eq!(result["unique_echo_requests"], 1);
            assert_eq!(result["duplicate_echo_requests"], 1);
            assert_eq!(result["rejected_records"], 0);
            assert_eq!(result["first_timestamp"], 100.25);
            assert_eq!(result["last_timestamp"], 101.25);
            assert_eq!(result["packets"][0]["source"], "192.168.10.1");
            assert_eq!(result["packets"][0]["icmp_sequence"], 11);
        }
    }
}

#[test]
fn checksum_errors_and_unsupported_links_are_counted_not_accepted() {
    let mut packet = echo(b"odd");
    *packet.last_mut().unwrap() ^= 1;
    let result = audit_pcap_bytes(&pcap(&[packet], true, false, 101), "capture").unwrap();
    assert_eq!(result["accepted_echo_requests"], 0);
    assert_eq!(
        result["rejection_reasons"],
        json!({"invalid ICMP checksum":1})
    );
    let result = audit_pcap_bytes(&pcap(&[echo(b"test")], true, false, 1), "capture").unwrap();
    assert_eq!(
        result["rejection_reasons"],
        json!({"unsupported PCAP link type 1":1})
    );
}

#[test]
fn cooked_v2_and_length_prefixed_formats_keep_the_same_packet_identity() {
    let packet = echo(b"test");
    let mut cooked = vec![0_u8; 20];
    cooked[..2].copy_from_slice(&0x0800_u16.to_be_bytes());
    cooked.extend(&packet);
    let result = audit_pcap_bytes(&pcap(&[cooked], true, false, 276), "capture").unwrap();
    assert_eq!(result["accepted_echo_requests"], 1);
    let mut prefixed = (packet.len() as u32).to_be_bytes().to_vec();
    prefixed.extend(&packet);
    let other = audit_length_prefixed_bytes(&prefixed, "pdus").unwrap();
    assert_eq!(result["packets"], other["packets"]);
    let mut invalid = packet.clone();
    *invalid.last_mut().unwrap() ^= 1;
    prefixed.extend((invalid.len() as u32).to_be_bytes());
    prefixed.extend(invalid);
    let other = audit_length_prefixed_bytes(&prefixed, "pdus").unwrap();
    assert_eq!(other["accepted_echo_requests"], 1);
    assert_eq!(other["rejected_records"], 1);
}

#[test]
fn every_truncated_header_or_packet_fails_closed() {
    let bytes = pcap(&[echo(b"test")], true, false, 101);
    for length in 0..bytes.len() {
        if length != 24 {
            assert!(
                audit_pcap_bytes(&bytes[..length], "capture").is_err(),
                "length {length}"
            );
        }
    }
    for bytes in [
        &[0, 0, 0][..],
        &[0, 0, 0, 100, 1, 2][..],
        &[0, 0, 0, 0][..],
        &[0, 1, 0, 0][..],
    ] {
        assert!(audit_length_prefixed_bytes(bytes, "pdus").is_err());
    }
}

#[test]
fn malformed_packet_headers_never_panic_or_gain_acceptance() {
    for size in 0..100 {
        for first in 0..=255 {
            let mut packet = vec![0; size];
            if !packet.is_empty() {
                packet[0] = first;
            }
            assert!(audit_echo_request(&packet).is_err());
        }
    }
    let mut bytes = pcap(&[echo(b"test")], true, false, 101);
    bytes[28..32].copy_from_slice(&1_000_000_u32.to_le_bytes());
    assert!(
        audit_pcap_bytes(&bytes, "capture")
            .unwrap_err()
            .contains("fraction")
    );
    bytes[28..32].copy_from_slice(&0_u32.to_le_bytes());
    bytes[32..36].copy_from_slice(&u32::MAX.to_le_bytes());
    assert!(
        audit_pcap_bytes(&bytes, "capture")
            .unwrap_err()
            .contains("lengths")
    );
}

#[test]
fn file_apis_hash_actual_bytes_and_reject_symlinks() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("capture.pcap");
    let bytes = pcap(&[echo(b"test")], true, false, 101);
    std::fs::write(&path, &bytes).unwrap();
    let result = audit_pcap_echo_requests(&path).unwrap();
    assert_eq!(result["pcap_sha256"], hex::encode(Sha256::digest(&bytes)));
    let link = dir.path().join("link");
    std::os::unix::fs::symlink(&path, &link).unwrap();
    assert!(audit_pcap_echo_requests(&link).is_err());
    assert!(audit_pcap_echo_requests(dir.path()).is_err());
}
