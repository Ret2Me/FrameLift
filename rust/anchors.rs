//! Native, integrity-checked AX.25 frame locations for signal-only training.
//!
//! The indices refer to the supplied physical level decisions, before NRZI
//! decoding, G3RUH descrambling, and HDLC unstuffing. They therefore also index
//! the caller's soft-symbol stream. No archived payload or repaired bit enters
//! this extractor. A valid FCS is integrity evidence, not authentication or a
//! guarantee that every physical level decision is correct.

use std::collections::BTreeSet;

use crate::protocol;

const MAX_SYMBOLS: usize = 4_194_304;
const HDLC_FLAG: [u8; 8] = [0, 1, 1, 1, 1, 1, 1, 0];

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ValidatedSpan {
    /// First physical symbol after the opening flag (inclusive).
    pub start: usize,
    /// First physical symbol of the closing flag (exclusive).
    pub end: usize,
    /// Received, unstuffed frame bytes, including the two-byte received FCS.
    pub frame: Vec<u8>,
    pub g3ruh: bool,
}

/// Independent bitwise residue check over the *received* frame, including its
/// FCS. This does not use the protocol module's table or regenerate an FCS.
fn received_fcs_residue_passes(frame: &[u8]) -> bool {
    if frame.len() < 2 {
        return false;
    }
    let mut state = 0xffff_u16;
    for byte in frame {
        for bit in 0..8 {
            let feedback = (state ^ u16::from(byte >> bit)) & 1;
            state >>= 1;
            if feedback != 0 {
                state ^= 0x8408;
            }
        }
    }
    state == 0xf0b8
}

fn extract_from_bits(bits: &[u8], g3ruh: bool, seen: &mut BTreeSet<(usize, usize, bool, Vec<u8>)>) {
    let max_unstuffed = (protocol::AX25_MAX_DECODER_FRAME_BYTES - 1) * 8;
    let max_stuffed = max_unstuffed + max_unstuffed.div_ceil(5);
    let mut previous_flag = None;
    for (right, candidate) in bits.windows(8).enumerate() {
        if candidate != HDLC_FLAG {
            continue;
        }
        if let Some(left) = previous_flag {
            // Match the existing decoder's treatment of adjacent/overlapping
            // flags and oversized regions, before allocating an unstuffed copy.
            if right > left + 8
                && right - left - 8 <= max_stuffed
                && let Ok(unstuffed) = protocol::hdlc_unstuff(&bits[left + 8..right])
                && let Ok(frame) = protocol::bits_to_bytes(&unstuffed, true)
                && frame.len() >= protocol::AX25_MIN_PAYLOAD_BYTES + 2
                && frame.len() < protocol::AX25_MAX_DECODER_FRAME_BYTES
                && protocol::valid_ax25_fcs(&frame)
                && received_fcs_residue_passes(&frame)
                && protocol::valid_ax25_ui(&frame[..frame.len() - 2])
            {
                // Preserve repeated transmissions; deduplicate only the same
                // occurrence found under both initial NRZI-level hypotheses.
                seen.insert((left + 8, right, g3ruh, frame));
            }
        }
        previous_flag = Some(right);
    }
}

/// Locate native AX.25 UI frames under plain NRZI and NRZI/G3RUH decoding.
///
/// Returned half-open spans exclude both flags and include stuffed symbols and
/// received FCS symbols. Every symbol coordinate is relative to `soft`, not to
/// the unstuffed payload. The two initial NRZI levels are tested just as in
/// [`protocol::decode_ax25`], but repeated physical occurrences are retained.
/// Results are sorted deterministically by start, end, G3RUH mode, then frame.
///
/// Training callers must enforce their own temporal separation from targets
/// and leave enough context for their signal model's memory. This routine does
/// not infer absolute times, transmitter identity, or channel stationarity.
pub fn validated_spans(soft: &[f64], threshold: f64) -> Result<Vec<ValidatedSpan>, String> {
    if soft.len() > MAX_SYMBOLS {
        return Err(format!("anchor symbol count exceeds {MAX_SYMBOLS}"));
    }
    if !threshold.is_finite() || soft.iter().any(|sample| !sample.is_finite()) {
        return Err("anchor symbols and threshold must be finite".into());
    }
    if soft.is_empty() {
        return Ok(Vec::new());
    }
    let levels: Vec<u8> = soft
        .iter()
        .map(|sample| u8::from(*sample >= threshold))
        .collect();
    let mut nrzi = vec![0_u8; levels.len()];
    for index in 1..levels.len() {
        nrzi[index] = u8::from(levels[index] == levels[index - 1]);
    }
    let mut seen = BTreeSet::new();
    for g3ruh in [false, true] {
        for initial in [0, 1] {
            nrzi[0] = u8::from(levels[0] == initial);
            if g3ruh {
                let mut bits = nrzi.clone();
                // Descrambler taps read received NRZI bits, not earlier output.
                for index in 12..nrzi.len() {
                    bits[index] ^= nrzi[index - 12];
                }
                for index in 17..nrzi.len() {
                    bits[index] ^= nrzi[index - 17];
                }
                extract_from_bits(&bits, true, &mut seen);
            } else {
                extract_from_bits(&nrzi, false, &mut seen);
            }
        }
    }
    Ok(seen
        .into_iter()
        .map(|(start, end, g3ruh, frame)| ValidatedSpan {
            start,
            end,
            frame,
            g3ruh,
        })
        .collect())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn frame(information: &[u8]) -> Vec<u8> {
        let mut payload = hex::decode("94a662b2a0826094a662b29eb2e103f0").unwrap();
        payload.extend_from_slice(information);
        payload.extend_from_slice(&protocol::crc16_x25(&payload).to_le_bytes());
        payload
    }

    fn stuffed(frame: &[u8]) -> Vec<u8> {
        let mut result = Vec::new();
        let mut ones = 0;
        for byte in frame {
            for bit_index in 0..8 {
                let bit = (byte >> bit_index) & 1;
                result.push(bit);
                ones = if bit == 1 { ones + 1 } else { 0 };
                if ones == 5 {
                    result.push(0);
                    ones = 0;
                }
            }
        }
        result
    }

    fn physical_levels(bits: &[u8], g3ruh: bool, initial: u8) -> Vec<f64> {
        let mut scrambled = Vec::with_capacity(bits.len());
        for (index, bit) in bits.iter().enumerate() {
            let mut value = *bit;
            if g3ruh && index >= 12 {
                value ^= scrambled[index - 12];
            }
            if g3ruh && index >= 17 {
                value ^= scrambled[index - 17];
            }
            scrambled.push(value);
        }
        let mut level = initial;
        scrambled
            .iter()
            .map(|bit| {
                level ^= 1 - bit;
                if level == 1 { 1.0 } else { -1.0 }
            })
            .collect()
    }

    fn stream(
        frames: &[Vec<u8>],
        g3ruh: bool,
        initial: u8,
        leading_flags: usize,
    ) -> (Vec<f64>, Vec<(usize, usize)>) {
        let mut bits = HDLC_FLAG.repeat(leading_flags);
        let mut expected = Vec::new();
        for frame in frames {
            let start = bits.len();
            bits.extend(stuffed(frame));
            expected.push((start, bits.len()));
            bits.extend(HDLC_FLAG);
        }
        bits.extend(HDLC_FLAG.repeat(8));
        (physical_levels(&bits, g3ruh, initial), expected)
    }

    #[test]
    fn immutable_oracle_515_mode_filtered_frame_sets() {
        let document: serde_json::Value =
            serde_json::from_str(include_str!("tests/protocol_oracle.json")).unwrap();
        assert_eq!(document["case_count"], 103);
        let mut comparisons = 0;
        for case in document["cases"].as_array().unwrap() {
            let packed = hex::decode(case["levels_hex"].as_str().unwrap()).unwrap();
            let soft: Vec<f64> = packed
                .iter()
                .flat_map(|byte| (0..8).map(move |bit| (byte >> (7 - bit)) & 1))
                .take(case["symbol_count"].as_u64().unwrap() as usize)
                .map(|bit| 3.0 * f64::from(bit) - 0.5)
                .collect();
            let spans = validated_spans(&soft, 0.4).unwrap();
            for expected in case["expected"].as_array().unwrap() {
                let modes: Vec<bool> = serde_json::from_value(expected["modes"].clone()).unwrap();
                let actual: BTreeSet<String> = spans
                    .iter()
                    .filter(|span| modes.contains(&span.g3ruh))
                    .map(|span| hex::encode(&span.frame))
                    .collect();
                let reference: BTreeSet<String> =
                    serde_json::from_value(expected["frames_hex"].clone()).unwrap();
                assert_eq!(actual, reference, "case={} modes={modes:?}", case["name"]);
                let existing: BTreeSet<String> = protocol::decode_ax25(&soft, 0.4, &modes)
                    .unwrap()
                    .iter()
                    .map(hex::encode)
                    .collect();
                assert_eq!(actual, existing);
                comparisons += 1;
            }
            for span in spans {
                assert!(span.start < span.end && span.end + 8 <= soft.len());
                assert!(received_fcs_residue_passes(&span.frame));
            }
        }
        assert_eq!(comparisons, 515);
    }

    #[test]
    fn repeated_frames_keep_exact_physical_coordinates_both_modes_and_initial_levels() {
        let first = frame(&[0xff; 80]);
        let second = frame(b"different information; never an archived training label");
        let frames = vec![first.clone(), first, second];
        for g3ruh in [false, true] {
            for initial in [0, 1] {
                for leading in [1, 2, 8] {
                    let (soft, coordinates) = stream(&frames, g3ruh, initial, leading);
                    let spans = validated_spans(&soft, 0.0).unwrap();
                    assert_eq!(spans.len(), frames.len());
                    for (index, span) in spans.iter().enumerate() {
                        assert_eq!((span.start, span.end), coordinates[index]);
                        assert_eq!(span.frame, frames[index]);
                        assert_eq!(span.g3ruh, g3ruh);
                        assert_eq!(span.end - span.start, stuffed(&span.frame).len());
                    }
                    // The coordinates cannot be byte/unstuffed-bit positions.
                    assert!(spans[0].end - spans[0].start > frames[0].len() * 8);
                }
            }
        }
    }

    #[test]
    fn g3ruh_prefix_cuts_and_inverted_polarity_preserve_original_span_alignment() {
        let first = frame(&[0xff; 43]);
        let second = frame(b"G3RUH reacquisition after an arbitrary physical-symbol prefix cut");
        let frames = vec![first.clone(), second, first];
        let expected_frames: BTreeSet<_> = frames.iter().cloned().collect();
        for initial in [0, 1] {
            // Even the largest cut leaves 32 symbols before the first packet,
            // enough for descrambler reacquisition before its opening flag.
            let (original, original_coordinates) = stream(&frames, true, initial, 12);
            for cut in 0..=64 {
                for inverted in [false, true] {
                    let observed: Vec<f64> = original[cut..]
                        .iter()
                        .map(|sample| if inverted { -*sample } else { *sample })
                        .collect();
                    let spans = validated_spans(&observed, 0.0).unwrap();
                    let actual_frames: BTreeSet<_> =
                        spans.iter().map(|span| span.frame.clone()).collect();
                    assert_eq!(
                        actual_frames, expected_frames,
                        "initial={initial}, cut={cut}, inverted={inverted}"
                    );
                    assert_eq!(spans.len(), frames.len());
                    for (index, span) in spans.iter().enumerate() {
                        assert!(span.g3ruh);
                        assert_eq!(span.frame, frames[index]);
                        assert_eq!(
                            (span.start + cut, span.end + cut),
                            original_coordinates[index],
                            "initial={initial}, cut={cut}, inverted={inverted}, frame={index}"
                        );
                    }
                }
            }
        }
    }

    #[test]
    fn strict_finite_inputs_and_bounded_allocation() {
        assert!(validated_spans(&[], 0.0).unwrap().is_empty());
        for value in [f64::NAN, f64::INFINITY, f64::NEG_INFINITY] {
            assert!(validated_spans(&[value], 0.0).is_err());
            assert!(validated_spans(&[], value).is_err());
        }
        assert!(validated_spans(&vec![0.0; MAX_SYMBOLS + 1], 0.0).is_err());
        for length in [0, 1, 7, 8, 15, 16, 63, 128, 57_600] {
            assert!(validated_spans(&vec![0.0; length], 0.0).unwrap().is_empty());
        }
    }

    #[test]
    fn received_fcs_check_vector_and_every_single_bit_corruption() {
        let mut known = b"123456789".to_vec();
        known.extend([0x6e, 0x90]);
        assert!(received_fcs_residue_passes(&known));
        assert!(received_fcs_residue_passes(&[0, 0]));
        assert!(!received_fcs_residue_passes(&[]));
        assert!(!received_fcs_residue_passes(&[0]));
        for index in 0..known.len() * 8 {
            let mut corrupt = known.clone();
            corrupt[index / 8] ^= 1 << (index % 8);
            assert!(!received_fcs_residue_passes(&corrupt));
        }
    }

    #[test]
    fn corruption_non_ui_and_missing_closing_flag_do_not_become_anchors() {
        let mut corrupt_fcs = frame(b"intact bytes with corrupted received FCS");
        *corrupt_fcs.last_mut().unwrap() ^= 1;
        let mut non_ui = frame(b"bad structure with correct FCS");
        non_ui[14] = 0x13;
        non_ui.truncate(non_ui.len() - 2);
        non_ui.extend_from_slice(&protocol::crc16_x25(&non_ui).to_le_bytes());
        assert!(protocol::valid_ax25_fcs(&non_ui));
        for g3ruh in [false, true] {
            for bad in [&corrupt_fcs, &non_ui] {
                let (soft, _) = stream(std::slice::from_ref(bad), g3ruh, 0, 8);
                assert!(validated_spans(&soft, 0.0).unwrap().is_empty());
            }
            let good = frame(b"closed framing is mandatory");
            let (mut soft, bounds) = stream(&[good], g3ruh, 0, 8);
            soft.truncate(bounds[0].1 + 7);
            assert!(validated_spans(&soft, 0.0).unwrap().is_empty());
        }
    }

    #[test]
    fn exact_frame_length_boundary_and_threshold_equality() {
        let accepted = frame(&vec![0xff; protocol::AX25_MAX_DECODER_FRAME_BYTES - 19]);
        let rejected = frame(&vec![0xff; protocol::AX25_MAX_DECODER_FRAME_BYTES - 18]);
        assert_eq!(accepted.len(), protocol::AX25_MAX_DECODER_FRAME_BYTES - 1);
        assert_eq!(rejected.len(), protocol::AX25_MAX_DECODER_FRAME_BYTES);
        for g3ruh in [false, true] {
            let (soft, _) = stream(&[accepted.clone(), rejected.clone()], g3ruh, 1, 8);
            let soft: Vec<f64> = soft
                .iter()
                .map(|value| if *value > 0.0 { 0.4 } else { -0.4 })
                .collect();
            let spans = validated_spans(&soft, 0.4).unwrap();
            assert_eq!(spans.len(), 1);
            assert_eq!(spans[0].frame, accepted);
        }
    }
}
