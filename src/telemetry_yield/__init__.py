"""Core primitives for reproducible telemetry-yield experiments."""

from telemetry_yield.canonical import canonical_json, config_hash
from telemetry_yield.crc import (
    append_ax25_fcs,
    ax25_fcs,
    crc16_x25,
    validate_ax25_fcs,
)
from telemetry_yield.gr_satellites_pdu_adapter import (
    GrSatellitesPDUCandidate,
    GrSatellitesPDUProvenance,
    decode_gr_satellites_g3ruh_levels,
    extract_gr_satellites_hdlc_pdus,
)
from telemetry_yield.license_gate import (
    LicenseGateError,
    assert_export_allowed,
    is_export_allowed,
)
from telemetry_yield.metrics import (
    false_accepts_per_hour,
    frames_per_cpu_second,
    unique_crc_frame_keys,
    unique_crc_frames,
)
from telemetry_yield.models import EffectiveConfig, FrameRecord, LicenseMetadata

__all__ = [
    "EffectiveConfig",
    "FrameRecord",
    "GrSatellitesPDUCandidate",
    "GrSatellitesPDUProvenance",
    "LicenseGateError",
    "LicenseMetadata",
    "append_ax25_fcs",
    "assert_export_allowed",
    "ax25_fcs",
    "canonical_json",
    "config_hash",
    "crc16_x25",
    "decode_gr_satellites_g3ruh_levels",
    "extract_gr_satellites_hdlc_pdus",
    "false_accepts_per_hour",
    "frames_per_cpu_second",
    "is_export_allowed",
    "unique_crc_frame_keys",
    "unique_crc_frames",
    "validate_ax25_fcs",
]

__version__ = "0.1.0"
