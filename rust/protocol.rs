//! Strict binary framing and integrity checks, independent of demodulation.
//!
//! This ports the existing Python adapters; no mission, FEC, or integrity
//! policy is inferred from a plausible syncword/header. AX.25 output retains
//! the received FCS. CCSDS TM output excludes its acquisition sync marker.

use std::collections::HashSet;

use serde::{Deserialize, Serialize};

pub const AX25_MIN_PAYLOAD_BYTES: usize = 14;
pub const AX25_MAX_DECODER_FRAME_BYTES: usize = 1024;
const HDLC_FLAG: [u8; 8] = [0, 1, 1, 1, 1, 1, 1, 0];

const fn x25_table() -> [u16; 256] {
    let mut table = [0; 256];
    let mut index = 0;
    while index < 256 {
        let mut value = index as u16;
        let mut bit = 0;
        while bit < 8 {
            value = if value & 1 != 0 {
                (value >> 1) ^ 0x8408
            } else {
                value >> 1
            };
            bit += 1;
        }
        table[index] = value;
        index += 1;
    }
    table
}

const X25_TABLE: [u16; 256] = x25_table();

/// CRC-16/X-25: reflected 0x1021, init/xorout 0xffff.
pub fn crc16_x25(data: &[u8]) -> u16 {
    data.iter().fold(0xffff, |crc, byte| {
        (crc >> 8) ^ X25_TABLE[usize::from((crc ^ u16::from(*byte)) & 0xff)]
    }) ^ 0xffff
}

pub fn valid_ax25_fcs(frame: &[u8]) -> bool {
    frame.len() >= 2
        && crc16_x25(&frame[..frame.len() - 2])
            == u16::from_le_bytes([frame[frame.len() - 2], frame[frame.len() - 1]])
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct Ax25Address {
    pub callsign: String,
    pub ssid: u8,
    pub command_or_repeated: bool,
    pub final_address: bool,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct Ax25UiFrame {
    pub destination: Ax25Address,
    pub source: Ax25Address,
    pub digipeaters: Vec<Ax25Address>,
    pub pid: u8,
    pub information: Vec<u8>,
}

fn ax25_address(raw: &[u8]) -> Option<Ax25Address> {
    if raw.len() != 7 || raw[..6].iter().any(|value| value & 1 != 0) || raw[6] & 0x60 != 0x60 {
        return None;
    }
    let decoded: Vec<u8> = raw[..6].iter().map(|value| value >> 1).collect();
    if decoded
        .iter()
        .any(|value| !matches!(value, b' ' | b'0'..=b'9' | b'A'..=b'Z'))
    {
        return None;
    }
    let last_non_space = decoded.iter().rposition(|value| *value != b' ')?;
    let callsign = &decoded[..=last_non_space];
    if callsign.contains(&b' ') {
        return None;
    }
    Some(Ax25Address {
        callsign: String::from_utf8(callsign.to_vec()).ok()?,
        ssid: (raw[6] >> 1) & 0x0f,
        command_or_repeated: raw[6] & 0x80 != 0,
        final_address: raw[6] & 1 != 0,
    })
}

/// Structural validation without FCS; arbitrary PID and empty information are
/// accepted just as in the reference. At most ten addresses are allowed.
pub fn parse_ax25_ui(payload: &[u8]) -> Option<Ax25UiFrame> {
    let mut addresses = Vec::new();
    let mut offset = 0;
    for _ in 0..10 {
        let parsed = ax25_address(payload.get(offset..offset + 7)?)?;
        let final_address = parsed.final_address;
        addresses.push(parsed);
        offset += 7;
        if final_address {
            break;
        }
    }
    if addresses.len() < 2
        || !addresses.last()?.final_address
        || payload.get(offset) != Some(&3)
        || payload.len() < offset + 2
    {
        return None;
    }
    Some(Ax25UiFrame {
        destination: addresses[0].clone(),
        source: addresses[1].clone(),
        digipeaters: addresses[2..].to_vec(),
        pid: payload[offset + 1],
        information: payload[offset + 2..].to_vec(),
    })
}

pub fn valid_ax25_ui(payload: &[u8]) -> bool {
    parse_ax25_ui(payload).is_some()
}

fn check_bits(bits: &[u8]) -> Result<(), String> {
    if bits.iter().any(|bit| *bit > 1) {
        return Err("bits must contain only zero and one".into());
    }
    Ok(())
}

pub fn bits_to_bytes(bits: &[u8], lsb_first: bool) -> Result<Vec<u8>, String> {
    check_bits(bits)?;
    if !bits.len().is_multiple_of(8) {
        return Err("bit sequence is not octet-aligned".into());
    }
    Ok(bits
        .as_chunks::<8>()
        .0
        .iter()
        .map(|chunk| {
            chunk.iter().enumerate().fold(0u8, |byte, (index, bit)| {
                byte | (bit << if lsb_first { index } else { 7 - index })
            })
        })
        .collect())
}

/// Retains the reference's treatment of a region ending in five ones: no
/// untransmitted stuffed zero is invented and byte alignment is checked later.
pub fn hdlc_unstuff(bits: &[u8]) -> Result<Vec<u8>, String> {
    check_bits(bits)?;
    let mut output = Vec::with_capacity(bits.len());
    let mut ones = 0;
    for bit in bits {
        if *bit == 1 {
            ones += 1;
            if ones > 5 {
                return Err("invalid HDLC run of six ones between flags".into());
            }
            output.push(1);
        } else if ones == 5 {
            ones = 0;
        } else {
            output.push(0);
            ones = 0;
        }
    }
    Ok(output)
}

/// Internal binary-input equivalent of `hdlc_unstuff` followed by LSB packing.
/// All callers obtain bits from validated NRZI levels. Keeping the candidate
/// buffer avoids two allocations for every (usually invalid) inter-flag region.
fn unstuff_frame_into(bits: &[u8], frame: &mut Vec<u8>) -> bool {
    frame.clear();
    let mut ones = 0;
    let mut byte = 0u8;
    let mut byte_bits = 0;
    for &bit in bits {
        if bit == 1 {
            ones += 1;
            if ones > 5 {
                return false;
            }
        } else if ones == 5 {
            ones = 0;
            continue;
        } else {
            ones = 0;
        }
        byte |= bit << byte_bits;
        byte_bits += 1;
        if byte_bits == 8 {
            frame.push(byte);
            byte = 0;
            byte_bits = 0;
        }
    }
    // Just like hdlc_unstuff, a final run of five ones does not require an
    // invented stuffed zero. Only the resulting octet alignment matters.
    byte_bits == 0
}

fn frames_from_bits(bits: &[u8], candidate_frame: &mut Vec<u8>) -> Vec<Vec<u8>> {
    let max_unstuffed = (AX25_MAX_DECODER_FRAME_BYTES - 1) * 8;
    let max_stuffed = max_unstuffed + max_unstuffed.div_ceil(5);
    let mut previous_flag = None;
    let mut seen = HashSet::new();
    let mut output = Vec::new();
    for (right, candidate) in bits.windows(8).enumerate() {
        if candidate != HDLC_FLAG {
            continue;
        }
        if let Some(left) = previous_flag {
            // Overlapping flags produce an empty Python slice, never a panic.
            if right > left + 8
                && right - left - 8 <= max_stuffed
                && unstuff_frame_into(&bits[left + 8..right], candidate_frame)
                && candidate_frame.len() >= AX25_MIN_PAYLOAD_BYTES + 2
                && candidate_frame.len() < AX25_MAX_DECODER_FRAME_BYTES
                && valid_ax25_fcs(candidate_frame)
                && seen.insert(candidate_frame.clone())
            {
                output.push(std::mem::take(candidate_frame));
            }
        }
        previous_flag = Some(right);
    }
    output
}

/// CRC-valid full frames in exact initial-level (0 then 1) order. This lower
/// level helper, like fast_decode_ax25_levels, does not assert UI structure.
pub fn decode_ax25_levels(levels: &[u8], g3ruh: bool) -> Result<Vec<Vec<u8>>, String> {
    check_bits(levels)?;
    let mut nrzi = nrzi_initial_zero(levels);
    Ok(decode_nrzi_initial_zero(&mut nrzi, g3ruh))
}

fn nrzi_initial_zero(levels: &[u8]) -> Vec<u8> {
    let mut nrzi = vec![0u8; levels.len()];
    if let Some(first) = levels.first() {
        nrzi[0] = u8::from(*first == 0);
    }
    for index in 1..levels.len() {
        nrzi[index] = u8::from(levels[index] == levels[index - 1]);
    }
    nrzi
}

fn flip_initial_nrzi_level(bits: &mut [u8], g3ruh: bool) {
    // Changing the initial NRZI level flips only input bit zero. For
    // y[i] = x[i] ^ x[i-12] ^ x[i-17], its complete effect is exactly indices
    // 0, 12 and 17 (when present), including arbitrarily short prefixes.
    bits[0] ^= 1;
    if g3ruh {
        for index in [12, 17] {
            if let Some(bit) = bits.get_mut(index) {
                *bit ^= 1;
            }
        }
    }
}

/// Receives binary NRZI with the first symbol decoded at initial level zero.
/// Restores it before returning so all requested modes share the same input.
fn decode_nrzi_initial_zero(nrzi: &mut [u8], g3ruh: bool) -> Vec<Vec<u8>> {
    if nrzi.is_empty() {
        return Vec::new();
    }
    let mut descrambled;
    let bits = if g3ruh {
        descrambled = nrzi.to_vec();
        for index in 12..nrzi.len() {
            descrambled[index] ^= nrzi[index - 12];
        }
        for index in 17..nrzi.len() {
            descrambled[index] ^= nrzi[index - 17];
        }
        descrambled.as_mut_slice()
    } else {
        nrzi
    };
    let mut candidate_frame = Vec::new();
    let mut output = Vec::new();
    let mut seen = HashSet::new();
    for initial in [0, 1] {
        if initial == 1 {
            flip_initial_nrzi_level(bits, g3ruh);
        }
        for frame in frames_from_bits(bits, &mut candidate_frame) {
            if seen.insert(frame.clone()) {
                output.push(frame);
            }
        }
    }
    if !g3ruh {
        bits[0] ^= 1;
    }
    output
}

/// Float comparison intentionally preserves Python/NumPy NaN/Inf slicing.
pub fn decode_ax25(
    soft: &[f64],
    threshold: f64,
    g3ruh_modes: &[bool],
) -> Result<Vec<Vec<u8>>, String> {
    if g3ruh_modes.is_empty() {
        return Err("at least one G3RUH mode is required".into());
    }
    let levels: Vec<u8> = soft
        .iter()
        .map(|value| u8::from(*value >= threshold))
        .collect();
    // Float slicing produces only binary values. Decode adjacent levels once,
    // not once for every mode, while retaining the requested mode order.
    let mut nrzi = nrzi_initial_zero(&levels);
    let mut output = Vec::new();
    let mut seen = HashSet::new();
    for g3ruh in g3ruh_modes {
        for frame in decode_nrzi_initial_zero(&mut nrzi, *g3ruh) {
            if valid_ax25_ui(&frame[..frame.len() - 2]) && seen.insert(frame.clone()) {
                output.push(frame);
            }
        }
    }
    Ok(output)
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct ProtocolFrame {
    pub frame: Vec<u8>,
    pub validation_layers: Vec<String>,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct FixedSyncConfig {
    pub protocol_id: String,
    pub syncword: Vec<u8>,
    /// Includes the syncword, unlike CCSDS TM's separate marker API.
    pub frame_bits: usize,
    pub lsb_first: bool,
    pub maximum_sync_hamming: usize,
    pub validation_name: String,
}

impl FixedSyncConfig {
    pub fn validate(&self) -> Result<(), String> {
        if self.protocol_id.trim().is_empty() || self.validation_name.trim().is_empty() {
            return Err("protocol and validation names must be non-empty".into());
        }
        validate_sync(&self.syncword, self.maximum_sync_hamming)?;
        if self.frame_bits < self.syncword.len() || !self.frame_bits.is_multiple_of(8) {
            return Err("frame_bits must include syncword and be byte-aligned".into());
        }
        Ok(())
    }
}

fn validate_sync(sync: &[u8], maximum_hamming: usize) -> Result<(), String> {
    check_bits(sync)?;
    if sync.is_empty() || maximum_hamming > sync.len() {
        return Err("non-empty binary syncword and valid Hamming bound required".into());
    }
    Ok(())
}

fn matches_sync(observed: &[u8], sync: &[u8], maximum_hamming: usize) -> bool {
    observed
        .iter()
        .zip(sync)
        .filter(|(a, b)| a != b)
        .take(maximum_hamming + 1)
        .count()
        <= maximum_hamming
}

pub type BitTransform<'a> = dyn Fn(&[u8]) -> Result<Vec<u8>, String> + 'a;

/// Caller-provided transform may perform channel decoding/derandomization.
/// A named integrity validator is mandatory; sync agreement alone accepts none.
pub fn decode_fixed_sync(
    soft: &[f64],
    threshold: f64,
    config: &FixedSyncConfig,
    transform: Option<&BitTransform<'_>>,
    validator: &dyn Fn(&[u8]) -> bool,
) -> Result<Vec<ProtocolFrame>, String> {
    config.validate()?;
    let mut bits: Vec<u8> = soft
        .iter()
        .map(|value| u8::from(*value >= threshold))
        .collect();
    if let Some(transform) = transform {
        bits = transform(&bits)?;
        check_bits(&bits)?;
    }
    let mut output = Vec::new();
    let mut seen = HashSet::new();
    for region in bits.windows(config.frame_bits) {
        if !matches_sync(
            &region[..config.syncword.len()],
            &config.syncword,
            config.maximum_sync_hamming,
        ) {
            continue;
        }
        let frame = bits_to_bytes(region, config.lsb_first)?;
        if !seen.contains(&frame) && validator(&frame) {
            seen.insert(frame.clone());
            output.push(ProtocolFrame {
                frame,
                validation_layers: vec![config.validation_name.clone()],
            });
        }
    }
    Ok(output)
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct CcsdsPrimaryHeader {
    pub version: u8,
    pub packet_type: u8,
    pub secondary_header: bool,
    pub apid: u16,
    pub sequence_flags: u8,
    pub sequence_count: u16,
    pub packet_data_length: usize,
    pub total_packet_length: usize,
}

/// Structure only, never standalone evidence of received telemetry.
pub fn parse_ccsds_space_packet(
    packet: &[u8],
    require_exact_length: bool,
) -> Option<CcsdsPrimaryHeader> {
    if packet.len() < 6 {
        return None;
    }
    let packet_id = u16::from_be_bytes([packet[0], packet[1]]);
    let sequence = u16::from_be_bytes([packet[2], packet[3]]);
    let encoded_length = u16::from_be_bytes([packet[4], packet[5]]);
    let version = (packet_id >> 13) as u8;
    let data_length = usize::from(encoded_length) + 1;
    let total_length = 6 + data_length;
    if version != 0
        || packet.len() < total_length
        || (require_exact_length && packet.len() != total_length)
    {
        return None;
    }
    Some(CcsdsPrimaryHeader {
        version,
        packet_type: ((packet_id >> 12) & 1) as u8,
        secondary_header: packet_id & 0x0800 != 0,
        apid: packet_id & 0x07ff,
        sequence_flags: (sequence >> 14) as u8,
        sequence_count: sequence & 0x3fff,
        packet_data_length: data_length,
        total_packet_length: total_length,
    })
}

fn validate_apids(apids: Option<&[u16]>) -> Result<(), String> {
    if apids.is_some_and(|apids| apids.is_empty() || apids.iter().any(|apid| *apid > 0x7ff)) {
        return Err("allowed APIDs must be a non-empty list in 0..2047".into());
    }
    Ok(())
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct RawCcsdsConfig {
    pub allowed_apids: Option<Vec<u16>>,
    pub bit_offsets: Vec<usize>,
    pub invert_modes: Vec<bool>,
    pub maximum_packet_bytes: usize,
    pub validation_name: String,
}

impl RawCcsdsConfig {
    pub fn validate(&self) -> Result<(), String> {
        validate_apids(self.allowed_apids.as_deref())?;
        if self.bit_offsets.is_empty()
            || self.bit_offsets.iter().any(|offset| *offset > 7)
            || self.invert_modes.is_empty()
            || self.maximum_packet_bytes < 7
            || self.validation_name.trim().is_empty()
        {
            return Err(
                "invalid CCSDS bit offsets, polarity modes, size or validation name".into(),
            );
        }
        Ok(())
    }
}

pub type PacketValidator<'a> = dyn Fn(&[u8], &CcsdsPrimaryHeader) -> bool + 'a;

pub fn decode_raw_ccsds(
    soft: &[f64],
    threshold: f64,
    config: &RawCcsdsConfig,
    validator: &PacketValidator<'_>,
) -> Result<Vec<ProtocolFrame>, String> {
    config.validate()?;
    let sliced: Vec<u8> = soft
        .iter()
        .map(|value| u8::from(*value >= threshold))
        .collect();
    let mut seen = HashSet::new();
    let mut output = Vec::new();
    let mut seen_modes = HashSet::new();
    for invert in &config.invert_modes {
        if !seen_modes.insert(*invert) {
            continue;
        }
        let bits: Vec<u8> = sliced.iter().map(|bit| bit ^ u8::from(*invert)).collect();
        let mut seen_offsets = HashSet::new();
        for offset in &config.bit_offsets {
            if !seen_offsets.insert(*offset) || *offset >= bits.len() {
                continue;
            }
            let available = (bits.len() - offset) / 8 * 8;
            if available < 56 {
                continue;
            }
            let stream = bits_to_bytes(&bits[*offset..*offset + available], false)?;
            for start in 0..stream.len() - 5 {
                let length =
                    7 + usize::from(u16::from_be_bytes([stream[start + 4], stream[start + 5]]));
                if length > config.maximum_packet_bytes || length > stream.len() - start {
                    continue;
                }
                let packet = &stream[start..start + length];
                let Some(header) = parse_ccsds_space_packet(packet, true) else {
                    continue;
                };
                if config
                    .allowed_apids
                    .as_ref()
                    .is_some_and(|apids| !apids.contains(&header.apid))
                {
                    continue;
                }
                if !seen.contains(packet) && validator(packet, &header) {
                    seen.insert(packet.to_vec());
                    output.push(ProtocolFrame {
                        frame: packet.to_vec(),
                        validation_layers: vec![
                            "ccsds_primary_header".into(),
                            config.validation_name.clone(),
                        ],
                    });
                }
            }
        }
    }
    Ok(output)
}

/// AX.25's original FCS supplies the integrity envelope for the packet.
pub fn decode_ax25_ccsds(
    soft: &[f64],
    threshold: f64,
    g3ruh_modes: &[bool],
    allowed_apids: Option<&[u16]>,
    extra_validator: Option<(&PacketValidator<'_>, &str)>,
) -> Result<Vec<ProtocolFrame>, String> {
    validate_apids(allowed_apids)?;
    if extra_validator.is_some_and(|(_, name)| name.trim().is_empty()) {
        return Err("validation name must be non-empty".into());
    }
    let mut output = Vec::new();
    for frame in decode_ax25(soft, threshold, g3ruh_modes)? {
        let Some(ax25) = parse_ax25_ui(&frame[..frame.len() - 2]) else {
            continue;
        };
        let Some(header) = parse_ccsds_space_packet(&ax25.information, true) else {
            continue;
        };
        if allowed_apids.is_some_and(|apids| !apids.contains(&header.apid)) {
            continue;
        }
        let mut validation_layers = vec![
            "crc16_x25".into(),
            "ax25_ui".into(),
            "ccsds_primary_header".into(),
        ];
        if let Some((validator, name)) = extra_validator {
            if !validator(&ax25.information, &header) {
                continue;
            }
            validation_layers.push(name.into());
        }
        output.push(ProtocolFrame {
            frame,
            validation_layers,
        });
    }
    Ok(output)
}

pub const TM_FIRST_HEADER_POINTER_ONLY_IDLE: u16 = 0x7fe;
pub const TM_FIRST_HEADER_POINTER_NO_PACKET_START: u16 = 0x7ff;

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct TmTransferFrameConfig {
    pub frame_length_bytes: usize,
    pub fecf_present: bool,
}

impl TmTransferFrameConfig {
    pub fn validate(&self) -> Result<(), String> {
        let minimum = 7 + if self.fecf_present { 2 } else { 0 };
        if !(minimum..=2048).contains(&self.frame_length_bytes) {
            return Err(
                "TM length must fit header, non-empty data, configured FECF and be <=2048 bytes"
                    .into(),
            );
        }
        Ok(())
    }
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct TmPrimaryHeader {
    pub transfer_frame_version: u8,
    pub spacecraft_id: u16,
    pub virtual_channel_id: u8,
    pub operational_control_field_present: bool,
    pub master_channel_frame_count: u8,
    pub virtual_channel_frame_count: u8,
    pub secondary_header_present: bool,
    pub synchronization_flag: bool,
    pub packet_order_flag: bool,
    pub segment_length_id: u8,
    pub first_header_pointer: u16,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct TmSecondaryHeader {
    pub version: u8,
    pub total_length_bytes: usize,
    pub data: Vec<u8>,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct TmTransferFrame {
    pub primary_header: TmPrimaryHeader,
    pub secondary_header: Option<TmSecondaryHeader>,
    pub data_field: Vec<u8>,
    pub operational_control_field: Option<Vec<u8>>,
    pub frame_error_control_field: Option<u16>,
    pub raw: Vec<u8>,
}

pub fn compute_tm_fecf(data: &[u8]) -> u16 {
    let mut remainder = 0xffff;
    for byte in data {
        remainder ^= u16::from(*byte) << 8;
        for _ in 0..8 {
            remainder = if remainder & 0x8000 != 0 {
                (remainder << 1) ^ 0x1021
            } else {
                remainder << 1
            };
        }
    }
    remainder
}

pub fn validate_tm_fecf(frame: &[u8]) -> bool {
    frame.len() >= 2
        && compute_tm_fecf(&frame[..frame.len() - 2])
            == u16::from_be_bytes([frame[frame.len() - 2], frame[frame.len() - 1]])
}

/// A successful parse is not an integrity acceptance.
pub fn parse_tm_transfer_frame(
    raw: &[u8],
    config: &TmTransferFrameConfig,
) -> Result<TmTransferFrame, String> {
    config.validate()?;
    if raw.len() != config.frame_length_bytes {
        return Err("frame_length_mismatch".into());
    }
    let first_word = u16::from_be_bytes([raw[0], raw[1]]);
    let version = (first_word >> 14) as u8;
    if version != 0 {
        return Err("unsupported_transfer_frame_version".into());
    }
    let status = u16::from_be_bytes([raw[4], raw[5]]);
    let primary = TmPrimaryHeader {
        transfer_frame_version: version,
        spacecraft_id: (first_word >> 4) & 0x03ff,
        virtual_channel_id: ((first_word >> 1) & 7) as u8,
        operational_control_field_present: first_word & 1 != 0,
        master_channel_frame_count: raw[2],
        virtual_channel_frame_count: raw[3],
        secondary_header_present: status & 0x8000 != 0,
        synchronization_flag: status & 0x4000 != 0,
        packet_order_flag: status & 0x2000 != 0,
        segment_length_id: ((status >> 11) & 3) as u8,
        first_header_pointer: status & 0x07ff,
    };
    if !primary.synchronization_flag {
        if primary.packet_order_flag {
            return Err("packet_order_flag_reserved_for_packet_data".into());
        }
        if primary.segment_length_id != 3 {
            return Err("invalid_packet_data_segment_length_id".into());
        }
    }
    let trailer_length = if primary.operational_control_field_present {
        4
    } else {
        0
    } + if config.fecf_present { 2 } else { 0 };
    let data_end = raw.len() - trailer_length;
    if data_end <= 6 {
        return Err("missing_transfer_frame_data_field".into());
    }
    let mut data_start = 6;
    let mut secondary = None;
    if primary.secondary_header_present {
        let version = raw[data_start] >> 6;
        if version != 0 {
            return Err("unsupported_secondary_header_version".into());
        }
        let length = usize::from(raw[data_start] & 0x3f) + 1;
        if length < 2 {
            return Err("missing_secondary_header_data".into());
        }
        if data_start + length >= data_end {
            return Err("invalid_secondary_header_length".into());
        }
        secondary = Some(TmSecondaryHeader {
            version,
            total_length_bytes: length,
            data: raw[data_start + 1..data_start + length].to_vec(),
        });
        data_start += length;
    }
    let data_field = raw[data_start..data_end].to_vec();
    if !primary.synchronization_flag
        && ![
            TM_FIRST_HEADER_POINTER_ONLY_IDLE,
            TM_FIRST_HEADER_POINTER_NO_PACKET_START,
        ]
        .contains(&primary.first_header_pointer)
        && usize::from(primary.first_header_pointer) >= data_field.len()
    {
        return Err("first_header_pointer_out_of_range".into());
    }
    let ocf = if primary.operational_control_field_present {
        Some(raw[data_end..data_end + 4].to_vec())
    } else {
        None
    };
    let fecf = if config.fecf_present {
        Some(u16::from_be_bytes([raw[raw.len() - 2], raw[raw.len() - 1]]))
    } else {
        None
    };
    Ok(TmTransferFrame {
        primary_header: primary,
        secondary_header: secondary,
        data_field,
        operational_control_field: ocf,
        frame_error_control_field: fecf,
        raw: raw.to_vec(),
    })
}

pub type TmIntegrityValidator<'a> = dyn Fn(&[u8], &TmTransferFrame) -> Result<bool, String> + 'a;

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct TmValidation {
    pub frame: Option<TmTransferFrame>,
    pub accepted: bool,
    pub validation_layers: Vec<String>,
    pub rejection_reason: Option<String>,
}

pub fn validate_tm_transfer_frame(
    raw: &[u8],
    config: &TmTransferFrameConfig,
    extra_validator: Option<(&TmIntegrityValidator<'_>, &str)>,
) -> Result<TmValidation, String> {
    config.validate()?;
    if extra_validator.is_some_and(|(_, name)| name.trim().is_empty()) {
        return Err("integrity name must be non-empty".into());
    }
    let parsed = match parse_tm_transfer_frame(raw, config) {
        Ok(frame) => frame,
        Err(reason) => {
            return Ok(TmValidation {
                frame: None,
                accepted: false,
                validation_layers: Vec::new(),
                rejection_reason: Some(reason),
            });
        }
    };
    let mut layers = vec![
        "ccsds_tm_primary_header".into(),
        "ccsds_tm_exact_frame_length".into(),
    ];
    let mut rejection = None;
    if config.fecf_present {
        if validate_tm_fecf(raw) {
            layers.push("ccsds_tm_fecf_crc16".into());
        } else {
            rejection = Some("invalid_fecf".into());
        }
    } else if extra_validator.is_none() {
        rejection = Some("integrity_evidence_required".into());
    }
    if rejection.is_none()
        && let Some((validator, name)) = extra_validator
    {
        match validator(raw, &parsed) {
            Ok(true) => layers.push(name.into()),
            Ok(false) => rejection = Some("integrity_validator_rejected".into()),
            Err(_) => rejection = Some("integrity_validator_error".into()),
        }
    }
    Ok(TmValidation {
        frame: Some(parsed),
        accepted: rejection.is_none(),
        validation_layers: layers,
        rejection_reason: rejection,
    })
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct CcsdsTmDecoderConfig {
    pub frame: TmTransferFrameConfig,
    pub sync_marker: Vec<u8>,
    pub maximum_sync_hamming: usize,
    pub invert_modes: Vec<bool>,
}

pub fn decode_ccsds_tm(
    soft: &[f64],
    threshold: f64,
    config: &CcsdsTmDecoderConfig,
    extra_validator: Option<(&TmIntegrityValidator<'_>, &str)>,
) -> Result<Vec<ProtocolFrame>, String> {
    config.frame.validate()?;
    validate_sync(&config.sync_marker, config.maximum_sync_hamming)?;
    if config.invert_modes.is_empty() {
        return Err("at least one polarity mode is required".into());
    }
    if !config.frame.fecf_present && extra_validator.is_none() {
        return Err("TM without FECF requires an integrity validator".into());
    }
    if extra_validator.is_some_and(|(_, name)| name.trim().is_empty()) {
        return Err("integrity name must be non-empty".into());
    }
    let frame_bits = config.frame.frame_length_bytes * 8;
    let candidate_bits = frame_bits
        .checked_add(config.sync_marker.len())
        .ok_or("candidate bit count overflow")?;
    let sliced: Vec<u8> = soft
        .iter()
        .map(|value| u8::from(*value >= threshold))
        .collect();
    let mut seen_modes = HashSet::new();
    let mut seen = HashSet::new();
    let mut output = Vec::new();
    for invert in &config.invert_modes {
        if !seen_modes.insert(*invert) {
            continue;
        }
        let bits: Vec<u8> = sliced.iter().map(|bit| bit ^ u8::from(*invert)).collect();
        for region in bits.windows(candidate_bits) {
            if !matches_sync(
                &region[..config.sync_marker.len()],
                &config.sync_marker,
                config.maximum_sync_hamming,
            ) {
                continue;
            }
            let frame = bits_to_bytes(&region[config.sync_marker.len()..], false)?;
            if seen.contains(&frame) {
                continue;
            }
            let validation = validate_tm_transfer_frame(&frame, &config.frame, extra_validator)?;
            if validation.accepted {
                seen.insert(frame.clone());
                output.push(ProtocolFrame {
                    frame,
                    validation_layers: validation.validation_layers,
                });
            }
        }
    }
    Ok(output)
}

#[cfg(test)]
#[path = "tests/protocol_tests.rs"]
mod tests;

#[cfg(test)]
mod lossless_optimization_tests {
    use super::*;

    // Frozen pre-optimization framing/NRZI/descrambling path, deliberately not
    // expressed in terms of the new internal helpers. Public bit conversion,
    // HDLC unstuffing, UI validation and CRC functions were not changed.
    fn old_frames_from_bits(bits: &[u8]) -> Vec<Vec<u8>> {
        let max_unstuffed = (AX25_MAX_DECODER_FRAME_BYTES - 1) * 8;
        let max_stuffed = max_unstuffed + max_unstuffed.div_ceil(5);
        let mut previous_flag = None;
        let mut seen = HashSet::new();
        let mut output = Vec::new();
        for (right, candidate) in bits.windows(8).enumerate() {
            if candidate != HDLC_FLAG {
                continue;
            }
            if let Some(left) = previous_flag {
                if right > left + 8
                    && right - left - 8 <= max_stuffed
                    && let Ok(unstuffed) = hdlc_unstuff(&bits[left + 8..right])
                    && let Ok(frame) = bits_to_bytes(&unstuffed, true)
                    && frame.len() >= AX25_MIN_PAYLOAD_BYTES + 2
                    && frame.len() < AX25_MAX_DECODER_FRAME_BYTES
                    && valid_ax25_fcs(&frame)
                    && seen.insert(frame.clone())
                {
                    output.push(frame);
                }
            }
            previous_flag = Some(right);
        }
        output
    }

    fn old_decode_levels(levels: &[u8], g3ruh: bool) -> Result<Vec<Vec<u8>>, String> {
        check_bits(levels)?;
        if levels.is_empty() {
            return Ok(Vec::new());
        }
        let mut nrzi = vec![0u8; levels.len()];
        for index in 1..levels.len() {
            nrzi[index] = u8::from(levels[index] == levels[index - 1]);
        }
        let mut output = Vec::new();
        let mut seen = HashSet::new();
        for initial in [0, 1] {
            nrzi[0] = u8::from(levels[0] == initial);
            let mut descrambled;
            let bits = if g3ruh {
                descrambled = nrzi.clone();
                for index in 12..nrzi.len() {
                    descrambled[index] ^= nrzi[index - 12];
                }
                for index in 17..nrzi.len() {
                    descrambled[index] ^= nrzi[index - 17];
                }
                &descrambled
            } else {
                &nrzi
            };
            for frame in old_frames_from_bits(bits) {
                if seen.insert(frame.clone()) {
                    output.push(frame);
                }
            }
        }
        Ok(output)
    }

    fn old_decode(soft: &[f64], threshold: f64, modes: &[bool]) -> Result<Vec<Vec<u8>>, String> {
        if modes.is_empty() {
            return Err("at least one G3RUH mode is required".into());
        }
        let levels: Vec<u8> = soft
            .iter()
            .map(|value| u8::from(*value >= threshold))
            .collect();
        let mut output = Vec::new();
        let mut seen = HashSet::new();
        for &g3ruh in modes {
            for frame in old_decode_levels(&levels, g3ruh)? {
                if valid_ax25_ui(&frame[..frame.len() - 2]) && seen.insert(frame.clone()) {
                    output.push(frame);
                }
            }
        }
        Ok(output)
    }

    fn next_random(state: &mut u64) -> u64 {
        *state ^= *state << 13;
        *state ^= *state >> 7;
        *state ^= *state << 17;
        *state
    }

    fn old_decoded_bits(levels: &[u8], initial: u8, g3ruh: bool) -> Vec<u8> {
        let mut nrzi = vec![u8::from(levels[0] == initial)];
        nrzi.extend(levels.windows(2).map(|pair| u8::from(pair[0] == pair[1])));
        let mut bits = nrzi.clone();
        if g3ruh {
            for index in 12..nrzi.len() {
                bits[index] ^= nrzi[index - 12];
            }
            for index in 17..nrzi.len() {
                bits[index] ^= nrzi[index - 17];
            }
        }
        bits
    }

    fn frame(mut payload: Vec<u8>) -> Vec<u8> {
        payload.extend_from_slice(&crc16_x25(&payload).to_le_bytes());
        payload
    }

    fn ui_frame(information: &[u8]) -> Vec<u8> {
        let mut payload = hex::decode("94a662b2a0826094a662b29eb2e103f0").unwrap();
        payload.extend_from_slice(information);
        frame(payload)
    }

    fn encode(frames: &[Vec<u8>], scrambled: bool, initial: u8, leading: usize) -> Vec<u8> {
        let mut bits = HDLC_FLAG.repeat(leading);
        for frame in frames {
            let mut ones = 0;
            for byte in frame {
                for shift in 0..8 {
                    let bit = (byte >> shift) & 1;
                    bits.push(bit);
                    ones = if bit == 1 { ones + 1 } else { 0 };
                    if ones == 5 {
                        bits.push(0);
                        ones = 0;
                    }
                }
            }
            bits.extend(HDLC_FLAG);
        }
        if scrambled {
            for index in 0..bits.len() {
                if index >= 12 {
                    bits[index] ^= bits[index - 12];
                }
                if index >= 17 {
                    bits[index] ^= bits[index - 17];
                }
            }
        }
        let mut previous = initial;
        bits.into_iter()
            .map(|bit| {
                if bit == 0 {
                    previous ^= 1;
                }
                previous
            })
            .collect()
    }

    fn assert_decoders_equal(levels: &[u8]) {
        for scrambled in [false, true] {
            assert_eq!(
                decode_ax25_levels(levels, scrambled),
                old_decode_levels(levels, scrambled),
                "lower-level exact ordered frames, g3ruh={scrambled}"
            );
        }
        let soft: Vec<f64> = levels
            .iter()
            .map(|bit| f64::from(*bit) * 2.0 - 1.0)
            .collect();
        for modes in [
            vec![],
            vec![false],
            vec![true],
            vec![false, true],
            vec![true, false],
            vec![false, true, false, true],
        ] {
            assert_eq!(
                decode_ax25(&soft, 0.0, &modes),
                old_decode(&soft, 0.0, &modes),
                "UI exact ordered frames, modes={modes:?}"
            );
        }
    }

    #[test]
    fn initial_state_shortcut_matches_every_intermediate_bit() {
        let compare = |levels: &[u8]| {
            for g3ruh in [false, true] {
                let original = old_decoded_bits(levels, 0, g3ruh);
                let mut flipped = original.clone();
                flip_initial_nrzi_level(&mut flipped, g3ruh);
                assert_eq!(flipped, old_decoded_bits(levels, 1, g3ruh));
                flip_initial_nrzi_level(&mut flipped, g3ruh);
                assert_eq!(flipped, original);
            }
        };
        for length in 1..=13 {
            for pattern in 0u32..(1 << length) {
                let levels: Vec<u8> = (0..length)
                    .map(|index| ((pattern >> index) & 1) as u8)
                    .collect();
                compare(&levels);
            }
        }
        let mut random = 0x612e_6bb5_c5e7_e863;
        for length in [14, 15, 16, 17, 18, 19, 32, 128, 4096] {
            for _ in 0..64 {
                let levels: Vec<u8> = (0..length)
                    .map(|_| (next_random(&mut random) & 1) as u8)
                    .collect();
                compare(&levels);
            }
        }
    }

    #[test]
    fn fused_unstuff_matches_old_two_stage_path_exhaustive_and_random() {
        let mut candidate = Vec::new();
        let mut compare = |bits: &[u8]| {
            let expected = hdlc_unstuff(bits).and_then(|bits| bits_to_bytes(&bits, true));
            let valid = unstuff_frame_into(bits, &mut candidate);
            assert_eq!(valid, expected.is_ok(), "stuffed bits={bits:?}");
            if let Ok(expected) = expected {
                assert_eq!(candidate, expected);
            }
        };
        for length in 0..=16 {
            for pattern in 0u32..(1 << length) {
                let bits: Vec<u8> = (0..length)
                    .map(|index| ((pattern >> index) & 1) as u8)
                    .collect();
                compare(&bits);
            }
        }
        let mut random = 0x85a4_e31d_27cb_6079;
        for length in [17, 18, 23, 24, 127, 128, 1023, 8191, 9821] {
            for _ in 0..32 {
                let bits: Vec<u8> = (0..length)
                    .map(|_| (next_random(&mut random) & 1) as u8)
                    .collect();
                compare(&bits);
            }
        }
    }

    #[test]
    fn exact_ordered_frames_match_frozen_path_on_prefix_cuts_and_polarities() {
        let first = ui_frame(b"first payload: \\x00 \\xff");
        let second = ui_frame(&[0xff; 39]);
        let mut bad_fcs = first.clone();
        bad_fcs[19] ^= 1;
        let frames = vec![first.clone(), frame(vec![0xff; 14]), second, first, bad_fcs];
        for scrambled in [false, true] {
            for initial in [0, 1] {
                // A leading flag at symbol zero specifically exercises cases
                // where initial-level iteration determines the output order.
                for leading in [1, 8] {
                    let levels = encode(&frames, scrambled, initial, leading);
                    for cut in 0..=64 {
                        for polarity in [0, 1] {
                            let cut_levels: Vec<u8> =
                                levels[cut..].iter().map(|bit| bit ^ polarity).collect();
                            assert_decoders_equal(&cut_levels);
                        }
                    }
                }
            }
        }
    }

    #[test]
    fn exact_frozen_path_handles_size_limits_aborts_noise_and_invalid_input() {
        let frames: Vec<Vec<u8>> = [13, 14, 16, 1021, 1022]
            .into_iter()
            .map(|length| frame(vec![0xff; length]))
            .collect();
        for scrambled in [false, true] {
            assert_decoders_equal(&encode(&frames, scrambled, 1, 8));
        }
        let mut random = 0x4f32_dac8_3167_920b;
        for length in [0, 1, 7, 8, 11, 12, 13, 16, 17, 18, 63, 128, 1024, 8192] {
            for value in [0, 1] {
                assert_decoders_equal(&vec![value; length]);
            }
            for _ in 0..8 {
                let levels: Vec<u8> = (0..length)
                    .map(|_| (next_random(&mut random) & 1) as u8)
                    .collect();
                assert_decoders_equal(&levels);
            }
        }
        for invalid in [vec![2], vec![0, 1, 255, 0], vec![255; 1024]] {
            for scrambled in [false, true] {
                assert_eq!(
                    decode_ax25_levels(&invalid, scrambled),
                    old_decode_levels(&invalid, scrambled)
                );
            }
        }
        let mut bits = HDLC_FLAG.to_vec();
        bits.extend(&HDLC_FLAG[1..]); // overlapping flags
        bits.extend([1; 140]); // abort run
        bits.extend(HDLC_FLAG);
        let mut scratch = Vec::new();
        assert_eq!(
            frames_from_bits(&bits, &mut scratch),
            old_frames_from_bits(&bits)
        );
    }

    #[test]
    fn float_slicing_special_values_and_mode_error_behavior_are_unchanged() {
        let levels = encode(&[ui_frame(b"float slicing remains exact")], true, 1, 8);
        let original: Vec<f64> = levels
            .iter()
            .map(|bit| f64::from(*bit) * 2.0 - 1.0)
            .collect();
        for special in [f64::NAN, f64::INFINITY, f64::NEG_INFINITY, -0.0, 0.0] {
            let mut soft = original.clone();
            for index in [0, 12, 17, 64, 127] {
                soft[index] = special;
            }
            for threshold in [
                -1.0,
                -0.0,
                0.0,
                1.0,
                f64::NAN,
                f64::INFINITY,
                f64::NEG_INFINITY,
            ] {
                for modes in [vec![], vec![false, true], vec![true, false, true]] {
                    assert_eq!(
                        decode_ax25(&soft, threshold, &modes),
                        old_decode(&soft, threshold, &modes)
                    );
                }
            }
        }
    }
}
