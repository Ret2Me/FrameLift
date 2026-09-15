//! Strict, byte-oriented parsers for complete CSP, CCSDS AOS, and CCSDS USLP frames.
//!
//! These parsers do not infer managed parameters from plausible bytes. In particular,
//! AOS frame length/trailer layout and both protocols' SDLS use are explicit configuration.
//! Unsupported security, fragmentation, truncated-header, and header-error-correction modes
//! fail closed.

use crate::protocol;
use serde::{Deserialize, Serialize};

const CSP_V1_HEADER_BYTES: usize = 4;
const CSP_V2_HEADER_BYTES: usize = 6;
const CSP_CRC32_BYTES: usize = 4;
const CSP_FLAG_CRC32: u8 = 0x01;
const AOS_PRIMARY_HEADER_BYTES: usize = 6;
const OCF_BYTES: usize = 4;
const FECF_BYTES: usize = 2;
const USLP_FIXED_PRIMARY_HEADER_BYTES: usize = 7;

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum CspCrc32Mode {
    /// No CRC trailer is present. This is structural parsing, not an integrity check.
    Absent,
    /// libcsp 2.1 transmit default: CRC-32C over the packed CSP header and payload.
    RequiredHeaderAndPayload,
    /// libcsp 1.x-compatible CRC-32C over payload only.
    RequiredPayloadOnlyLegacy,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case", deny_unknown_fields)]
pub enum SpaceLinkConfig {
    CspV1 {
        crc32: CspCrc32Mode,
    },
    CspV2 {
        crc32: CspCrc32Mode,
    },
    Aos {
        /// AOS has no in-frame length field; this managed value is therefore mandatory.
        frame_length_bytes: usize,
        insert_zone_length_bytes: usize,
        operational_control_field: bool,
        frame_error_control_field: bool,
        /// Reserved for a future RS(15,11) implementation. `true` is rejected.
        frame_header_error_control: bool,
        /// SDLS parsing is not implemented. `true` is rejected.
        security: bool,
    },
    Uslp {
        /// Optional additional managed-length constraint. The encoded length is always checked.
        expected_frame_length_bytes: Option<usize>,
        insert_zone_length_bytes: usize,
        frame_error_control_field: bool,
        /// SDLS parsing is not implemented. `true` is rejected.
        security: bool,
    },
}

impl SpaceLinkConfig {
    pub fn validate(&self) -> Result<(), String> {
        match self {
            Self::CspV1 { .. } | Self::CspV2 { .. } => Ok(()),
            Self::Aos {
                frame_length_bytes,
                insert_zone_length_bytes,
                operational_control_field,
                frame_error_control_field,
                frame_header_error_control,
                security,
            } => {
                if *frame_header_error_control {
                    return Err(
                        "AOS Frame Header Error Control is unsupported; RS(15,11) validation is required"
                            .into(),
                    );
                }
                if *security {
                    return Err("AOS SDLS security fields are unsupported".into());
                }
                let minimum = AOS_PRIMARY_HEADER_BYTES
                    .checked_add(*insert_zone_length_bytes)
                    .and_then(|value| {
                        value.checked_add(if *operational_control_field {
                            OCF_BYTES
                        } else {
                            0
                        })
                    })
                    .and_then(|value| {
                        value.checked_add(if *frame_error_control_field {
                            FECF_BYTES
                        } else {
                            0
                        })
                    })
                    .and_then(|value| value.checked_add(1))
                    .ok_or_else(|| "AOS configured layout overflows usize".to_string())?;
                if *frame_length_bytes < minimum {
                    return Err(format!(
                        "AOS frame length {frame_length_bytes} is smaller than configured fields plus one data-field octet ({minimum})"
                    ));
                }
                Ok(())
            }
            Self::Uslp {
                expected_frame_length_bytes,
                insert_zone_length_bytes,
                frame_error_control_field,
                security,
            } => {
                if *security {
                    return Err("USLP SDLS security fields are unsupported".into());
                }
                let minimum = USLP_FIXED_PRIMARY_HEADER_BYTES
                    .checked_add(*insert_zone_length_bytes)
                    .and_then(|value| {
                        value.checked_add(if *frame_error_control_field {
                            FECF_BYTES
                        } else {
                            0
                        })
                    })
                    .and_then(|value| value.checked_add(1))
                    .ok_or_else(|| "USLP configured layout overflows usize".to_string())?;
                if let Some(expected) = expected_frame_length_bytes
                    && (*expected < minimum || *expected > 65_536)
                {
                    return Err(format!(
                        "USLP expected frame length {expected} is outside the configured minimum {minimum}..=65536"
                    ));
                }
                Ok(())
            }
        }
    }

    pub fn has_integrity_check(&self) -> bool {
        match self {
            Self::CspV1 { crc32 } | Self::CspV2 { crc32 } => !matches!(crc32, CspCrc32Mode::Absent),
            Self::Aos {
                frame_error_control_field,
                ..
            }
            | Self::Uslp {
                frame_error_control_field,
                ..
            } => *frame_error_control_field,
        }
    }

    pub fn decode(&self, frame: &[u8]) -> Result<SpaceLinkFrame, String> {
        self.validate()?;
        match self {
            Self::CspV1 { crc32 } => decode_csp(frame, CspVersion::V1, crc32),
            Self::CspV2 { crc32 } => decode_csp(frame, CspVersion::V2, crc32),
            Self::Aos {
                frame_length_bytes,
                insert_zone_length_bytes,
                operational_control_field,
                frame_error_control_field,
                ..
            } => decode_aos(
                frame,
                *frame_length_bytes,
                *insert_zone_length_bytes,
                *operational_control_field,
                *frame_error_control_field,
            ),
            Self::Uslp {
                expected_frame_length_bytes,
                insert_zone_length_bytes,
                frame_error_control_field,
                ..
            } => decode_uslp(
                frame,
                *expected_frame_length_bytes,
                *insert_zone_length_bytes,
                *frame_error_control_field,
            ),
        }
    }
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum CspVersion {
    V1,
    V2,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CspHeader {
    pub version: CspVersion,
    pub priority: u8,
    pub source: u16,
    pub destination: u16,
    pub destination_port: u8,
    pub source_port: u8,
    pub flags: u8,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct AosPrimaryHeader {
    pub transfer_frame_version_number: u8,
    pub master_channel_id: u16,
    /// Ten-bit Issue-5 SCID, including bits 42-43 of the primary header.
    pub spacecraft_id: u16,
    pub virtual_channel_id: u8,
    pub virtual_channel_frame_count: u32,
    pub replay: bool,
    pub virtual_channel_frame_count_cycle_used: bool,
    pub virtual_channel_frame_count_cycle: u8,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum UslpIdentifierUse {
    Source,
    Destination,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct UslpPrimaryHeader {
    pub transfer_frame_version_number: u8,
    pub spacecraft_id: u16,
    pub spacecraft_identifier_use: UslpIdentifierUse,
    pub virtual_channel_id: u8,
    pub map_id: u8,
    pub frame_length_bytes: usize,
    pub bypass_sequence_control: bool,
    pub protocol_control_command: bool,
    pub operational_control_field_present: bool,
    pub virtual_channel_frame_count_length_bytes: u8,
    pub virtual_channel_frame_count: Option<u64>,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum UslpTfdzConstructionRule {
    PacketsSpanningFrames,
    StartMapaOrVca,
    ContinueMapaOrVca,
    OctetStream,
    StartingSegment,
    ContinuingSegment,
    LastSegment,
    NoSegmentation,
}

impl UslpTfdzConstructionRule {
    fn from_bits(value: u8) -> Self {
        match value {
            0 => Self::PacketsSpanningFrames,
            1 => Self::StartMapaOrVca,
            2 => Self::ContinueMapaOrVca,
            3 => Self::OctetStream,
            4 => Self::StartingSegment,
            5 => Self::ContinuingSegment,
            6 => Self::LastSegment,
            7 => Self::NoSegmentation,
            _ => unreachable!("three-bit construction rule"),
        }
    }

    fn has_pointer(&self) -> bool {
        matches!(
            self,
            Self::PacketsSpanningFrames | Self::StartMapaOrVca | Self::ContinueMapaOrVca
        )
    }
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct UslpDataFieldHeader {
    pub construction_rule: UslpTfdzConstructionRule,
    pub protocol_identifier: u8,
    pub first_header_or_last_valid_octet_pointer: Option<u16>,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "type", content = "header", rename_all = "snake_case")]
pub enum SpaceLinkHeader {
    Csp(CspHeader),
    Aos(AosPrimaryHeader),
    Uslp(UslpPrimaryHeader),
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SpaceLinkFrame {
    pub header: SpaceLinkHeader,
    pub insert_zone: Vec<u8>,
    /// USLP TFDF header. `None` for CSP and for the intentionally opaque AOS data field.
    pub data_field_header: Option<UslpDataFieldHeader>,
    /// CSP application data, the complete opaque AOS Transfer Frame Data Field, or USLP TFDZ.
    pub payload: Vec<u8>,
    pub operational_control_field: Option<[u8; OCF_BYTES]>,
    pub frame_error_control_field: Option<u16>,
    pub csp_crc32: Option<u32>,
    pub validation_layers: Vec<String>,
}

/// libcsp 2.1's CRC implementation is reflected CRC-32C (Castagnoli),
/// init/xorout `0xffff_ffff`.
pub fn csp_crc32c(data: &[u8]) -> u32 {
    let mut crc = 0xffff_ffffu32;
    for byte in data {
        crc ^= u32::from(*byte);
        for _ in 0..8 {
            crc = if crc & 1 != 0 {
                (crc >> 1) ^ 0x82f6_3b78
            } else {
                crc >> 1
            };
        }
    }
    crc ^ 0xffff_ffff
}

fn decode_csp(
    frame: &[u8],
    version: CspVersion,
    crc32_mode: &CspCrc32Mode,
) -> Result<SpaceLinkFrame, String> {
    let header_bytes = match version {
        CspVersion::V1 => CSP_V1_HEADER_BYTES,
        CspVersion::V2 => CSP_V2_HEADER_BYTES,
    };
    if frame.len() < header_bytes {
        return Err(format!(
            "truncated CSP {} header: need {header_bytes} bytes, got {}",
            match version {
                CspVersion::V1 => "v1",
                CspVersion::V2 => "v2",
            },
            frame.len()
        ));
    }
    let raw = frame[..header_bytes]
        .iter()
        .fold(0u64, |value, byte| (value << 8) | u64::from(*byte));
    let (priority, source, destination, destination_port, source_port, flags) = match version {
        CspVersion::V1 => (
            ((raw >> 30) & 0x03) as u8,
            ((raw >> 25) & 0x1f) as u16,
            ((raw >> 20) & 0x1f) as u16,
            ((raw >> 14) & 0x3f) as u8,
            ((raw >> 8) & 0x3f) as u8,
            (raw & 0xff) as u8,
        ),
        CspVersion::V2 => (
            ((raw >> 46) & 0x03) as u8,
            ((raw >> 18) & 0x3fff) as u16,
            ((raw >> 32) & 0x3fff) as u16,
            ((raw >> 12) & 0x3f) as u8,
            ((raw >> 6) & 0x3f) as u8,
            (raw & 0x3f) as u8,
        ),
    };
    if flags & !CSP_FLAG_CRC32 != 0 {
        return Err(format!(
            "unsupported CSP flags 0x{:02x}; fragmentation, HMAC, RDP, and reserved extensions are rejected",
            flags & !CSP_FLAG_CRC32
        ));
    }

    let crc_flag = flags & CSP_FLAG_CRC32 != 0;
    let crc_required = !matches!(crc32_mode, CspCrc32Mode::Absent);
    if crc_flag != crc_required {
        return Err(format!(
            "CSP CRC32 flag/configuration mismatch: flag={crc_flag}, configured_required={crc_required}"
        ));
    }
    let trailer_bytes = if crc_required { CSP_CRC32_BYTES } else { 0 };
    if frame.len() < header_bytes + trailer_bytes {
        return Err("truncated CSP CRC32 trailer".into());
    }
    let payload_end = frame.len() - trailer_bytes;
    let payload = frame[header_bytes..payload_end].to_vec();
    let mut validation_layers = vec![match version {
        CspVersion::V1 => "csp_v1_primary_header".into(),
        CspVersion::V2 => "csp_v2_primary_header".into(),
    }];
    validation_layers.push("csp_no_unsupported_extensions".into());

    let csp_crc32 = if crc_required {
        let received = u32::from_be_bytes(
            frame[payload_end..]
                .try_into()
                .map_err(|_| "truncated CSP CRC32 trailer")?,
        );
        let (scope, layer) = match crc32_mode {
            CspCrc32Mode::RequiredHeaderAndPayload => {
                (&frame[..payload_end], "csp_crc32c_header_and_payload")
            }
            CspCrc32Mode::RequiredPayloadOnlyLegacy => (
                &frame[header_bytes..payload_end],
                "csp_crc32c_payload_only_legacy",
            ),
            CspCrc32Mode::Absent => unreachable!("crc_required excludes absent"),
        };
        let computed = csp_crc32c(scope);
        if received != computed {
            return Err(format!(
                "CSP CRC-32C mismatch for explicit scope: received {received:08x}, computed {computed:08x}"
            ));
        }
        validation_layers.push(layer.into());
        Some(received)
    } else {
        None
    };

    Ok(SpaceLinkFrame {
        header: SpaceLinkHeader::Csp(CspHeader {
            version,
            priority,
            source,
            destination,
            destination_port,
            source_port,
            flags,
        }),
        insert_zone: Vec::new(),
        data_field_header: None,
        payload,
        operational_control_field: None,
        frame_error_control_field: None,
        csp_crc32,
        validation_layers,
    })
}

fn reject_sdls_ocf(ocf: &[u8; OCF_BYTES], protocol_name: &str) -> Result<(), String> {
    // OCF bit 0 is the MSB of octet zero. Type-2 + use bit 1 denotes an SDLS FSR.
    if ocf[0] & 0xc0 == 0xc0 {
        return Err(format!(
            "{protocol_name} SDLS Frame Security Report in OCF is unsupported"
        ));
    }
    Ok(())
}

fn decode_aos(
    frame: &[u8],
    expected_frame_length: usize,
    insert_zone_length: usize,
    ocf_present: bool,
    fecf_present: bool,
) -> Result<SpaceLinkFrame, String> {
    if frame.len() != expected_frame_length {
        return Err(format!(
            "AOS exact frame length mismatch: expected {expected_frame_length}, got {}",
            frame.len()
        ));
    }
    let header = frame
        .get(..AOS_PRIMARY_HEADER_BYTES)
        .ok_or_else(|| "truncated AOS primary header".to_string())?;
    let transfer_frame_version_number = header[0] >> 6;
    if transfer_frame_version_number != 1 {
        return Err(format!(
            "AOS transfer frame version must be binary 01, got {transfer_frame_version_number:02b}"
        ));
    }
    let spacecraft_id_lsb = (u16::from(header[0] & 0x3f) << 2) | u16::from(header[1] >> 6);
    let spacecraft_id_msb = u16::from((header[5] >> 4) & 0x03);
    let spacecraft_id = (spacecraft_id_msb << 8) | spacecraft_id_lsb;
    let virtual_channel_id = header[1] & 0x3f;
    if virtual_channel_id == 0x3f {
        return Err("AOS Only-Idle-Data VCID 63 is unsupported".into());
    }
    let virtual_channel_frame_count =
        (u32::from(header[2]) << 16) | (u32::from(header[3]) << 8) | u32::from(header[4]);
    let replay = header[5] & 0x80 != 0;
    let virtual_channel_frame_count_cycle_used = header[5] & 0x40 != 0;
    let virtual_channel_frame_count_cycle = header[5] & 0x0f;
    if !virtual_channel_frame_count_cycle_used && virtual_channel_frame_count_cycle != 0 {
        return Err(
            "AOS VC frame-count cycle must be zero when its signaling use flag is zero".into(),
        );
    }

    if fecf_present && !protocol::validate_tm_fecf(frame) {
        return Err("AOS FECF CRC-16/CCITT-FALSE mismatch".into());
    }
    let trailer_start = frame
        .len()
        .checked_sub(if fecf_present { FECF_BYTES } else { 0 })
        .and_then(|value| value.checked_sub(if ocf_present { OCF_BYTES } else { 0 }))
        .ok_or_else(|| "truncated AOS trailer".to_string())?;
    let data_start = AOS_PRIMARY_HEADER_BYTES
        .checked_add(insert_zone_length)
        .ok_or_else(|| "AOS insert-zone length overflows usize".to_string())?;
    if data_start >= trailer_start {
        return Err("AOS Transfer Frame Data Field must contain at least one octet".into());
    }
    let insert_zone = frame[AOS_PRIMARY_HEADER_BYTES..data_start].to_vec();
    let payload = frame[data_start..trailer_start].to_vec();
    let operational_control_field = if ocf_present {
        let ocf: [u8; OCF_BYTES] = frame[trailer_start..trailer_start + OCF_BYTES]
            .try_into()
            .map_err(|_| "truncated AOS Operational Control Field")?;
        reject_sdls_ocf(&ocf, "AOS")?;
        Some(ocf)
    } else {
        None
    };
    let frame_error_control_field = if fecf_present {
        Some(u16::from_be_bytes(
            frame[frame.len() - FECF_BYTES..]
                .try_into()
                .map_err(|_| "truncated AOS FECF")?,
        ))
    } else {
        None
    };
    let mut validation_layers = vec![
        "ccsds_aos_primary_header_issue_5_semantics".into(),
        "ccsds_aos_exact_frame_length".into(),
        "ccsds_aos_managed_field_layout".into(),
        "ccsds_aos_configured_without_sdls".into(),
    ];
    if fecf_present {
        validation_layers.push("ccsds_aos_fecf_crc16".into());
    }

    Ok(SpaceLinkFrame {
        header: SpaceLinkHeader::Aos(AosPrimaryHeader {
            transfer_frame_version_number,
            master_channel_id: (u16::from(transfer_frame_version_number) << 10) | spacecraft_id,
            spacecraft_id,
            virtual_channel_id,
            virtual_channel_frame_count,
            replay,
            virtual_channel_frame_count_cycle_used,
            virtual_channel_frame_count_cycle,
        }),
        insert_zone,
        data_field_header: None,
        payload,
        operational_control_field,
        frame_error_control_field,
        csp_crc32: None,
        validation_layers,
    })
}

fn decode_uslp(
    frame: &[u8],
    expected_frame_length: Option<usize>,
    insert_zone_length: usize,
    fecf_present: bool,
) -> Result<SpaceLinkFrame, String> {
    let fixed = frame
        .get(..USLP_FIXED_PRIMARY_HEADER_BYTES)
        .ok_or_else(|| "truncated USLP non-truncated primary header".to_string())?;
    let transfer_frame_version_number = fixed[0] >> 4;
    if transfer_frame_version_number != 0x0c {
        return Err(format!(
            "USLP transfer frame version must be binary 1100, got {transfer_frame_version_number:04b}"
        ));
    }
    if fixed[3] & 1 != 0 {
        return Err("USLP truncated Transfer Frame Primary Header is unsupported".into());
    }
    let encoded_frame_length = usize::from(u16::from_be_bytes([fixed[4], fixed[5]])) + 1;
    if encoded_frame_length != frame.len() {
        return Err(format!(
            "USLP encoded frame length mismatch: header says {encoded_frame_length}, got {}",
            frame.len()
        ));
    }
    if let Some(expected) = expected_frame_length
        && expected != frame.len()
    {
        return Err(format!(
            "USLP managed frame length mismatch: expected {expected}, got {}",
            frame.len()
        ));
    }
    let bypass_sequence_control = fixed[6] & 0x80 != 0;
    let protocol_control_command = fixed[6] & 0x40 != 0;
    if fixed[6] & 0x30 != 0 {
        return Err("USLP reserved primary-header spare bits must be zero".into());
    }
    if !bypass_sequence_control && protocol_control_command {
        return Err(
            "USLP bypass/protocol-control combination 0/1 is reserved for future use".into(),
        );
    }
    let operational_control_field_present = fixed[6] & 0x08 != 0;
    let virtual_channel_frame_count_length_bytes = fixed[6] & 0x07;
    let primary_header_length =
        USLP_FIXED_PRIMARY_HEADER_BYTES + usize::from(virtual_channel_frame_count_length_bytes);
    let primary = frame
        .get(..primary_header_length)
        .ok_or_else(|| "truncated USLP Virtual Channel Frame Count".to_string())?;
    let virtual_channel_frame_count = if virtual_channel_frame_count_length_bytes == 0 {
        None
    } else {
        Some(
            primary[USLP_FIXED_PRIMARY_HEADER_BYTES..]
                .iter()
                .fold(0u64, |value, byte| (value << 8) | u64::from(*byte)),
        )
    };
    let spacecraft_id =
        (u16::from(fixed[0] & 0x0f) << 12) | (u16::from(fixed[1]) << 4) | u16::from(fixed[2] >> 4);
    let spacecraft_identifier_use = if fixed[2] & 0x08 == 0 {
        UslpIdentifierUse::Source
    } else {
        UslpIdentifierUse::Destination
    };
    let virtual_channel_id = ((fixed[2] & 0x07) << 3) | (fixed[3] >> 5);
    if virtual_channel_id == 0x3f {
        return Err("USLP Only-Idle-Data VCID 63 is unsupported".into());
    }
    let map_id = (fixed[3] >> 1) & 0x0f;

    if fecf_present && !protocol::validate_tm_fecf(frame) {
        return Err("USLP FECF CRC-16/CCITT-FALSE mismatch".into());
    }
    let ocf_bytes = if operational_control_field_present {
        OCF_BYTES
    } else {
        0
    };
    let trailer_start = frame
        .len()
        .checked_sub(if fecf_present { FECF_BYTES } else { 0 })
        .and_then(|value| value.checked_sub(ocf_bytes))
        .ok_or_else(|| "truncated USLP trailer".to_string())?;
    let data_field_start = primary_header_length
        .checked_add(insert_zone_length)
        .ok_or_else(|| "USLP insert-zone length overflows usize".to_string())?;
    if data_field_start >= trailer_start {
        return Err("USLP Transfer Frame Data Field header is missing".into());
    }
    let insert_zone = frame[primary_header_length..data_field_start].to_vec();
    let data_field = &frame[data_field_start..trailer_start];
    let construction_rule = UslpTfdzConstructionRule::from_bits(data_field[0] >> 5);
    let protocol_identifier = data_field[0] & 0x1f;
    let data_field_header_length = if construction_rule.has_pointer() {
        3
    } else {
        1
    };
    if data_field.len() < data_field_header_length {
        return Err("truncated USLP Transfer Frame Data Field header pointer".into());
    }
    let first_header_or_last_valid_octet_pointer = if construction_rule.has_pointer() {
        Some(u16::from_be_bytes([data_field[1], data_field[2]]))
    } else {
        None
    };
    let payload = data_field[data_field_header_length..].to_vec();
    if let Some(pointer) = first_header_or_last_valid_octet_pointer
        && pointer != u16::MAX
        && usize::from(pointer) >= payload.len()
    {
        return Err(format!(
            "USLP TFDF pointer {pointer} is outside the {}-octet TFDZ",
            payload.len()
        ));
    }
    let operational_control_field = if operational_control_field_present {
        let ocf: [u8; OCF_BYTES] = frame[trailer_start..trailer_start + OCF_BYTES]
            .try_into()
            .map_err(|_| "truncated USLP Operational Control Field")?;
        reject_sdls_ocf(&ocf, "USLP")?;
        Some(ocf)
    } else {
        None
    };
    let frame_error_control_field = if fecf_present {
        Some(u16::from_be_bytes(
            frame[frame.len() - FECF_BYTES..]
                .try_into()
                .map_err(|_| "truncated USLP FECF")?,
        ))
    } else {
        None
    };
    let mut validation_layers = vec![
        "ccsds_uslp_non_truncated_primary_header".into(),
        "ccsds_uslp_encoded_frame_length".into(),
        "ccsds_uslp_tfdf_header".into(),
        "ccsds_uslp_configured_without_sdls".into(),
    ];
    if expected_frame_length.is_some() {
        validation_layers.push("ccsds_uslp_managed_frame_length".into());
    }
    if fecf_present {
        validation_layers.push("ccsds_uslp_fecf_crc16".into());
    }

    Ok(SpaceLinkFrame {
        header: SpaceLinkHeader::Uslp(UslpPrimaryHeader {
            transfer_frame_version_number,
            spacecraft_id,
            spacecraft_identifier_use,
            virtual_channel_id,
            map_id,
            frame_length_bytes: encoded_frame_length,
            bypass_sequence_control,
            protocol_control_command,
            operational_control_field_present,
            virtual_channel_frame_count_length_bytes,
            virtual_channel_frame_count,
        }),
        insert_zone,
        data_field_header: Some(UslpDataFieldHeader {
            construction_rule,
            protocol_identifier,
            first_header_or_last_valid_octet_pointer,
        }),
        payload,
        operational_control_field,
        frame_error_control_field,
        csp_crc32: None,
        validation_layers,
    })
}

#[cfg(test)]
#[path = "tests/space_link_tests.rs"]
mod tests;
