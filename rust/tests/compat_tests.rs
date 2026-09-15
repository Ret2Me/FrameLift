use super::*;
use serde_json::Value;

const FLAG: [u8; 8] = [0, 1, 1, 1, 1, 1, 1, 0];
const OBS4704: &str = "6073d927ea3ae8157bfd2b60";

fn frame_stream(payload: &[u8], drop_leading: usize) -> Vec<u8> {
    let mut frame = payload.to_vec();
    frame.extend_from_slice(&protocol::crc16_x25(payload).to_le_bytes());
    let mut stuffed = Vec::new();
    let mut ones = 0;
    for bit in frame
        .iter()
        .flat_map(|byte| (0..8).map(move |i| (byte >> i) & 1))
    {
        stuffed.push(bit);
        ones = if bit == 1 { ones + 1 } else { 0 };
        if ones == 5 {
            stuffed.push(0);
            ones = 0;
        }
    }
    [
        FLAG.to_vec(),
        stuffed[drop_leading..].to_vec(),
        FLAG.to_vec(),
    ]
    .concat()
}
fn encode_levels(bits: &[u8]) -> Vec<u8> {
    let mut state = 0_u32;
    let mut level = 0;
    bits.iter()
        .map(|bit| {
            let encoded = ((state & G3RUH_MASK).count_ones() as u8 ^ bit) & 1;
            state = (state >> 1) | (u32::from(encoded) << G3RUH_ORDER);
            if encoded == 0 {
                level ^= 1;
            }
            level
        })
        .collect()
}

#[test]
fn python_oracles_match_every_candidate_byte_and_provenance_field() {
    let oracle: Value = serde_json::from_str(include_str!("compat_oracle.json")).unwrap();
    for case in oracle["cases"].as_array().unwrap() {
        let input: Vec<u8> = serde_json::from_value(case["input_bits"].clone()).unwrap();
        let maximum = case["max_length"].as_u64().unwrap() as usize;
        let actual = if case["stage"] == "decoded" {
            extract_gr_satellites_hdlc_pdus(&input, maximum)
        } else {
            decode_gr_satellites_g3ruh_levels(&input, maximum)
        }
        .unwrap();
        assert_eq!(
            serde_json::to_value(actual).unwrap(),
            case["expected"],
            "{} {}",
            case["name"],
            case["stage"]
        );
    }
}

#[test]
fn observation4704_left_padding_remains_untrusted_undersized_candidate() {
    let payload = hex::decode(OBS4704).unwrap();
    let bits = frame_stream(&payload, 4);
    let candidates = extract_gr_satellites_hdlc_pdus(&bits, GNU_HDLC_MAX_LENGTH).unwrap();
    assert_eq!(candidates.len(), 1);
    let c = &candidates[0];
    assert_eq!(c.pdu, payload);
    assert_eq!(
        hex::encode(&c.frame_with_fcs),
        "6073d927ea3ae8157bfd2b6089bb"
    );
    assert_eq!(c.classification, PduClassification::CrcValidUndersizedPdu);
    assert!(c.undersized_for_ax25);
    assert!(c.strict_ax25_ui.is_none());
    assert_eq!(c.provenance.raw_unstuffed_bit_count, 108);
    assert_eq!(c.provenance.left_padding_bits, 4);
    let levels = encode_levels(&bits);
    assert_eq!(
        decode_gr_satellites_g3ruh_levels(&levels, GNU_HDLC_MAX_LENGTH).unwrap()[0].pdu,
        payload
    );
    assert!(
        protocol::decode_ax25_levels(&levels, true)
            .unwrap()
            .is_empty(),
        "strict native lane must not start accepting this partial-octet candidate"
    );
    assert!(protocol::parse_ax25_ui(&payload).is_none());
}

#[test]
fn crc_rejection_duplicates_and_buffer_truncation_are_explicit() {
    assert!(
        extract_gr_satellites_hdlc_pdus(&frame_stream(&[], 0), 10000)
            .unwrap()
            .is_empty()
    );
    let payload = hex::decode(OBS4704).unwrap();
    let stream = frame_stream(&payload, 0);
    let repeated = [stream.clone(), stream.clone()].concat();
    assert_eq!(
        extract_gr_satellites_hdlc_pdus(&repeated, 10000)
            .unwrap()
            .len(),
        2,
        "compatibility emissions are not deduplicated"
    );
    let prefixed = [FLAG.to_vec(), vec![0; 257], stream[8..].to_vec()].concat();
    let candidates = extract_gr_satellites_hdlc_pdus(&prefixed, 12).unwrap();
    assert_eq!(candidates.len(), 1);
    assert_eq!(candidates[0].pdu, payload);
    assert_eq!(candidates[0].provenance.truncated_unstuffed_bits, 257);
    let mut bad = stream;
    bad[12] ^= 1;
    assert!(
        extract_gr_satellites_hdlc_pdus(&bad, 10000)
            .unwrap()
            .is_empty()
    );
}

#[test]
fn invalid_bits_and_capacity_fail_without_partial_success_or_allocation() {
    for bits in [vec![2], vec![0, 255], vec![1, 0, 2, 1]] {
        assert!(extract_gr_satellites_hdlc_pdus(&bits, 10000).is_err());
        assert!(decode_gr_satellites_g3ruh_levels(&bits, 10000).is_err());
    }
    for length in [0, usize::MAX, usize::MAX / 8] {
        assert!(extract_gr_satellites_hdlc_pdus(&[], length).is_err());
    }
    let mut valid = frame_stream(&hex::decode(OBS4704).unwrap(), 0);
    valid.push(2);
    assert!(extract_gr_satellites_hdlc_pdus(&valid, 10000).is_err());
    assert!(
        extract_gr_satellites_hdlc_pdus(&[], 100_000_000)
            .unwrap()
            .is_empty(),
        "unused logical buffer capacity is not allocated eagerly"
    );
}

#[test]
fn seven_one_abort_delimiter_can_emit_a_candidate_but_not_a_strict_frame() {
    // Independently checked against the original Python adapter. GNU removes
    // exactly seven buffered bits even when the terminator is not 0x7e.
    let payload = hex::decode(OBS4704).unwrap();
    let mut stream = frame_stream(&payload, 0);
    stream.truncate(stream.len() - 8);
    stream.extend_from_slice(&[1, 1, 1, 1, 1, 1, 1, 0]);
    let candidates = extract_gr_satellites_hdlc_pdus(&stream, 10000).unwrap();
    assert_eq!(candidates.len(), 1);
    let candidate = &candidates[0];
    assert_eq!(candidate.pdu, payload);
    assert_eq!(candidate.provenance.opening_flag_end_bit_index, Some(7));
    assert_eq!(candidate.provenance.closing_flag_end_bit_index, 129);
    assert_eq!(candidate.provenance.raw_unstuffed_bit_count, 112);
    assert_eq!(candidate.provenance.stuffed_zero_count, 2);
    assert_eq!(candidate.provenance.left_padding_bits, 0);
    assert!(
        protocol::decode_ax25_levels(&encode_levels(&stream), true)
            .unwrap()
            .is_empty()
    );
}
