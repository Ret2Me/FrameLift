//! Candidate-only gr-satellites 5.9.0 symbol-stage compatibility.
//!
//! GNU HDLC left-pads partial octets and treats runs of more than five ones as
//! delimiters/aborts. Those observable quirks are reproduced here, separately
//! from the strict native deframer. CRC-valid emission is NOT a telemetry
//! claim. Structural AX.25 validation is reported independently, using the
//! unchanged `protocol::parse_ax25_ui`; this module updates no trusted counts.
//!
//! No GNU Radio, Python, reference PDU, mission metadata or event time is used
//! by the decoder. Float-to-hard-slice decisions belong to the caller.

use crate::protocol::{self, Ax25UiFrame};
use serde::{Deserialize, Serialize};
use std::collections::VecDeque;

pub const ADAPTER_SCHEMA_VERSION: &str = "gr-satellites-g3ruh-hdlc-pdu-adapter-v1";
pub const GR_SATELLITES_VERSION: &str = "5.9.0";
pub const G3RUH_MASK: u32 = 0x21;
pub const G3RUH_SEED: u32 = 0;
pub const G3RUH_ORDER: u32 = 16;
pub const GNU_HDLC_MAX_LENGTH: usize = 10_000;
pub const AX25_MIN_ADDRESS_BYTES: usize = 14;

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum PduClassification {
    StrictAx25Ui,
    CrcValidUndersizedPdu,
    CrcValidNonAx25Pdu,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct GrSatellitesPduProvenance {
    pub adapter_schema_version: String,
    pub gr_satellites_version: String,
    pub input_stage: String,
    pub pipeline: Vec<String>,
    pub opening_flag_end_bit_index: Option<usize>,
    pub closing_flag_end_bit_index: usize,
    pub raw_unstuffed_bit_count: usize,
    pub left_padding_bits: usize,
    pub stuffed_zero_count: usize,
    pub truncated_unstuffed_bits: usize,
    pub fcs_checked: bool,
    pub fcs_trimmed_from_pdu: bool,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct GrSatellitesPduCandidate {
    pub pdu: Vec<u8>,
    pub frame_with_fcs: Vec<u8>,
    pub classification: PduClassification,
    pub undersized_for_ax25: bool,
    /// Uses the shared Rust representation (`final_address`, not Python's
    /// `final` field spelling). Presence is structural validation, not an
    /// automatic promotion from this compatibility lane into trusted counts.
    pub strict_ax25_ui: Option<Ax25UiFrame>,
    pub provenance: GrSatellitesPduProvenance,
}

fn checked_bit(bit: u8, label: &str) -> Result<u8, String> {
    if bit <= 1 {
        Ok(bit)
    } else {
        Err(format!("{label} must contain only integer zero and one"))
    }
}

fn pipeline(max_length: usize, g3ruh: bool) -> Vec<String> {
    let mut steps = Vec::new();
    if g3ruh {
        steps.push("nrzi_decode(initial_level=0,unchanged=1)".into());
        steps.push("descrambler_bb(mask=0x21,seed=0,length=16)".into());
    }
    steps.extend([
        format!("hdlc_deframer(check_fcs=True,max_length={max_length})"),
        "left_zero_pad_to_octet_boundary".into(),
        "lsb_first_pack".into(),
        "crc16_x25_check".into(),
        "trim_two_byte_fcs".into(),
    ]);
    steps
}

fn extract_pdus(
    decoded_bits: impl IntoIterator<Item = Result<u8, String>>,
    max_length: usize,
    input_stage: &str,
    pipeline: Vec<String>,
) -> Result<Vec<GrSatellitesPduCandidate>, String> {
    if max_length == 0 {
        return Err("max_length must be positive".into());
    }
    let capacity = max_length
        .checked_add(2)
        .and_then(|x| x.checked_mul(8))
        .and_then(|x| x.checked_add(7))
        .ok_or("max_length overflows the GNU HDLC buffer capacity")?;
    // Do not reserve an arbitrary caller-sized allocation up front. Actual
    // storage grows only with received bits, up to this exact GNU capacity.
    let mut bits = VecDeque::new();
    let mut ones = 0;
    let mut stuffed_zero_count = 0;
    let mut truncated_unstuffed_bits = 0;
    let mut last_flag_end = None;
    let mut candidates = Vec::new();
    for (bit_index, raw) in decoded_bits.into_iter().enumerate() {
        let value = raw?;
        if value != 0 {
            ones += 1;
            if bits.len() == capacity {
                bits.pop_front();
                truncated_unstuffed_bits += 1;
            }
            bits.push_back(value);
            continue;
        }
        if ones == 5 {
            stuffed_zero_count += 1;
        } else if ones > 5 {
            for _ in 0..7.min(bits.len()) {
                bits.pop_back();
            }
            let raw_unstuffed_bit_count = bits.len();
            let left_padding_bits = (8 - raw_unstuffed_bit_count % 8) % 8;
            for _ in 0..left_padding_bits {
                bits.push_front(0);
            }
            let packed = protocol::bits_to_bytes(bits.make_contiguous(), true)?;
            bits.clear();
            // GNU rejects the empty payload's two-byte CRC, even if valid.
            if packed.len() > 2 && protocol::valid_ax25_fcs(&packed) {
                let pdu = packed[..packed.len() - 2].to_vec();
                let undersized = pdu.len() < AX25_MIN_ADDRESS_BYTES;
                let parsed = protocol::parse_ax25_ui(&pdu);
                let classification = if parsed.is_some() {
                    PduClassification::StrictAx25Ui
                } else if undersized {
                    PduClassification::CrcValidUndersizedPdu
                } else {
                    PduClassification::CrcValidNonAx25Pdu
                };
                candidates.push(GrSatellitesPduCandidate {
                    pdu,
                    frame_with_fcs: packed,
                    classification,
                    undersized_for_ax25: undersized,
                    strict_ax25_ui: parsed,
                    provenance: GrSatellitesPduProvenance {
                        adapter_schema_version: ADAPTER_SCHEMA_VERSION.into(),
                        gr_satellites_version: GR_SATELLITES_VERSION.into(),
                        input_stage: input_stage.into(),
                        pipeline: pipeline.clone(),
                        opening_flag_end_bit_index: last_flag_end,
                        closing_flag_end_bit_index: bit_index,
                        raw_unstuffed_bit_count,
                        left_padding_bits,
                        stuffed_zero_count,
                        truncated_unstuffed_bits,
                        fcs_checked: true,
                        fcs_trimmed_from_pdu: true,
                    },
                });
            }
            last_flag_end = Some(bit_index);
            stuffed_zero_count = 0;
            truncated_unstuffed_bits = 0;
        } else {
            if bits.len() == capacity {
                bits.pop_front();
                truncated_unstuffed_bits += 1;
            }
            bits.push_back(value);
        }
        ones = 0;
    }
    Ok(candidates)
}

/// GNU-compatible HDLC extraction after optional line-code descrambling.
/// Unaligned and structurally invalid PDUs remain explicitly classified.
pub fn extract_gr_satellites_hdlc_pdus(
    decoded_bits: &[u8],
    max_length: usize,
) -> Result<Vec<GrSatellitesPduCandidate>, String> {
    extract_pdus(
        decoded_bits
            .iter()
            .map(|bit| checked_bit(*bit, "decoded_bits")),
        max_length,
        "descrambled_binary_bits",
        pipeline(max_length, false),
    )
}

/// Hard-sliced levels -> zero-initialized NRZI -> G3RUH -> compatible HDLC.
/// Decoded bits stream directly into the bounded deframer without a second
/// whole-input vector. No reference bytes are accepted by this API.
pub fn decode_gr_satellites_g3ruh_levels(
    levels: &[u8],
    max_length: usize,
) -> Result<Vec<GrSatellitesPduCandidate>, String> {
    let mut previous_level = 0;
    let mut state = G3RUH_SEED;
    let decoded = levels.iter().map(|raw| {
        let level = checked_bit(*raw, "levels")?;
        let nrzi = u8::from(level == previous_level);
        previous_level = level;
        let bit = (((state & G3RUH_MASK).count_ones() as u8) ^ nrzi) & 1;
        state = (state >> 1) | (u32::from(nrzi) << G3RUH_ORDER);
        Ok(bit)
    });
    extract_pdus(
        decoded,
        max_length,
        "hard_sliced_nrzi_g3ruh_levels",
        pipeline(max_length, true),
    )
}

#[cfg(test)]
#[path = "tests/compat_tests.rs"]
mod tests;
