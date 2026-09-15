"""Conservative parsing and integrity validation for CCSDS TM Transfer Frames.

This module implements the Version-1 TM Transfer Frame defined by CCSDS
132.0-B-3.  It deliberately does not parse Space Packets, AOS frames, or USLP
frames.  A structurally plausible primary header is not integrity evidence:
``validate_tm_transfer_frame`` accepts a frame only after verifying its managed
FECF, or after a caller-supplied integrity validator succeeds.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


TM_PRIMARY_HEADER_LENGTH_BYTES = 6
TM_OPERATIONAL_CONTROL_FIELD_LENGTH_BYTES = 4
TM_FRAME_ERROR_CONTROL_FIELD_LENGTH_BYTES = 2
TM_MAX_TRANSFER_FRAME_LENGTH_BYTES = 2_048
TM_FIRST_HEADER_POINTER_ONLY_IDLE = 0x7FE
TM_FIRST_HEADER_POINTER_NO_PACKET_START = 0x7FF


@dataclass(frozen=True, slots=True)
class TMTransferFrameConfig:
    """Managed parameters needed to delimit one TM Transfer Frame.

    Frame length and FECF presence are not inferred from candidate bytes.  Both
    are channel/mission-phase configuration in the CCSDS specifications and
    therefore must be supplied explicitly.
    """

    frame_length_bytes: int
    fecf_present: bool

    def __post_init__(self) -> None:
        minimum = TM_PRIMARY_HEADER_LENGTH_BYTES + 1
        if self.fecf_present:
            minimum += TM_FRAME_ERROR_CONTROL_FIELD_LENGTH_BYTES
        if not minimum <= self.frame_length_bytes <= TM_MAX_TRANSFER_FRAME_LENGTH_BYTES:
            raise ValueError(
                "TM frame length must fit the primary header, a non-empty data "
                f"field, the configured trailer, and be <= "
                f"{TM_MAX_TRANSFER_FRAME_LENGTH_BYTES} octets"
            )


@dataclass(frozen=True, slots=True)
class TMTransferFramePrimaryHeader:
    """Decoded fields of the mandatory six-octet TM primary header."""

    transfer_frame_version: int
    spacecraft_id: int
    virtual_channel_id: int
    operational_control_field_present: bool
    master_channel_frame_count: int
    virtual_channel_frame_count: int
    secondary_header_present: bool
    synchronization_flag: bool
    packet_order_flag: bool
    segment_length_id: int
    first_header_pointer: int


@dataclass(frozen=True, slots=True)
class TMTransferFrameSecondaryHeader:
    """The optional Version-1 secondary header and its opaque data bytes."""

    version: int
    total_length_bytes: int
    data: bytes


@dataclass(frozen=True, slots=True)
class TMTransferFrame:
    """A structurally parsed, but not necessarily integrity-valid, TM frame."""

    primary_header: TMTransferFramePrimaryHeader
    secondary_header: TMTransferFrameSecondaryHeader | None
    data_field: bytes
    operational_control_field: bytes | None
    frame_error_control_field: int | None
    raw: bytes


TMIntegrityValidator = Callable[[bytes, TMTransferFrame], bool]


@dataclass(frozen=True, slots=True)
class TMTransferFrameValidation:
    """Machine-readable outcome that keeps structure separate from trust."""

    frame: TMTransferFrame | None
    accepted: bool
    validation_layers: tuple[str, ...]
    rejection_reason: str | None

    def __post_init__(self) -> None:
        if self.accepted:
            if self.frame is None or not self.validation_layers:
                raise ValueError("accepted validation requires a frame and layers")
            if self.rejection_reason is not None:
                raise ValueError("accepted validation cannot have a rejection reason")
        elif self.rejection_reason is None:
            raise ValueError("rejected validation requires a reason")


def compute_tm_fecf(data: bytes) -> int:
    """Return the CCSDS TM CRC-16 FECF for bytes excluding the FECF.

    This is the non-reflected x^16+x^12+x^5+1 division, with the register
    preset to all ones and no final XOR.  The first transmitted bit is the MSB.
    """

    remainder = 0xFFFF
    for octet in data:
        remainder ^= octet << 8
        for _ in range(8):
            if remainder & 0x8000:
                remainder = ((remainder << 1) ^ 0x1021) & 0xFFFF
            else:
                remainder = (remainder << 1) & 0xFFFF
    return remainder


def validate_tm_fecf(frame: bytes) -> bool:
    """Verify the final two octets as the FECF of the preceding frame bytes."""

    if len(frame) < TM_FRAME_ERROR_CONTROL_FIELD_LENGTH_BYTES:
        return False
    expected = compute_tm_fecf(frame[:-TM_FRAME_ERROR_CONTROL_FIELD_LENGTH_BYTES])
    observed = int.from_bytes(
        frame[-TM_FRAME_ERROR_CONTROL_FIELD_LENGTH_BYTES:], "big"
    )
    return observed == expected


def _parse_tm_transfer_frame(
    frame: bytes,
    *,
    config: TMTransferFrameConfig,
) -> tuple[TMTransferFrame | None, str | None]:
    raw = bytes(frame)
    if len(raw) != config.frame_length_bytes:
        return None, "frame_length_mismatch"

    master_channel_identifier = int.from_bytes(raw[0:2], "big") >> 4
    transfer_frame_version = (master_channel_identifier >> 10) & 0x03
    if transfer_frame_version != 0:
        return None, "unsupported_transfer_frame_version"

    first_word = int.from_bytes(raw[0:2], "big")
    data_field_status = int.from_bytes(raw[4:6], "big")
    primary = TMTransferFramePrimaryHeader(
        transfer_frame_version=transfer_frame_version,
        spacecraft_id=(first_word >> 4) & 0x03FF,
        virtual_channel_id=(first_word >> 1) & 0x07,
        operational_control_field_present=bool(first_word & 0x01),
        master_channel_frame_count=raw[2],
        virtual_channel_frame_count=raw[3],
        secondary_header_present=bool(data_field_status & 0x8000),
        synchronization_flag=bool(data_field_status & 0x4000),
        packet_order_flag=bool(data_field_status & 0x2000),
        segment_length_id=(data_field_status >> 11) & 0x03,
        first_header_pointer=data_field_status & 0x07FF,
    )

    if not primary.synchronization_flag:
        if primary.packet_order_flag:
            return None, "packet_order_flag_reserved_for_packet_data"
        if primary.segment_length_id != 0x03:
            return None, "invalid_packet_data_segment_length_id"

    trailer_length = (
        TM_OPERATIONAL_CONTROL_FIELD_LENGTH_BYTES
        if primary.operational_control_field_present
        else 0
    )
    if config.fecf_present:
        trailer_length += TM_FRAME_ERROR_CONTROL_FIELD_LENGTH_BYTES
    data_zone_end = len(raw) - trailer_length
    if data_zone_end <= TM_PRIMARY_HEADER_LENGTH_BYTES:
        return None, "missing_transfer_frame_data_field"

    data_field_start = TM_PRIMARY_HEADER_LENGTH_BYTES
    secondary: TMTransferFrameSecondaryHeader | None = None
    if primary.secondary_header_present:
        identification = raw[data_field_start]
        version = (identification >> 6) & 0x03
        if version != 0:
            return None, "unsupported_secondary_header_version"
        total_length = (identification & 0x3F) + 1
        if total_length < 2:
            return None, "missing_secondary_header_data"
        if data_field_start + total_length >= data_zone_end:
            return None, "invalid_secondary_header_length"
        secondary = TMTransferFrameSecondaryHeader(
            version=version,
            total_length_bytes=total_length,
            data=raw[data_field_start + 1 : data_field_start + total_length],
        )
        data_field_start += total_length

    data_field = raw[data_field_start:data_zone_end]
    if not data_field:
        return None, "missing_transfer_frame_data_field"
    if (
        not primary.synchronization_flag
        and primary.first_header_pointer
        not in (
            TM_FIRST_HEADER_POINTER_ONLY_IDLE,
            TM_FIRST_HEADER_POINTER_NO_PACKET_START,
        )
        and primary.first_header_pointer >= len(data_field)
    ):
        return None, "first_header_pointer_out_of_range"

    trailer_cursor = data_zone_end
    operational_control_field: bytes | None = None
    if primary.operational_control_field_present:
        operational_control_field = raw[
            trailer_cursor : trailer_cursor
            + TM_OPERATIONAL_CONTROL_FIELD_LENGTH_BYTES
        ]
        trailer_cursor += TM_OPERATIONAL_CONTROL_FIELD_LENGTH_BYTES

    fecf: int | None = None
    if config.fecf_present:
        fecf = int.from_bytes(
            raw[
                trailer_cursor : trailer_cursor
                + TM_FRAME_ERROR_CONTROL_FIELD_LENGTH_BYTES
            ],
            "big",
        )

    return (
        TMTransferFrame(
            primary_header=primary,
            secondary_header=secondary,
            data_field=data_field,
            operational_control_field=operational_control_field,
            frame_error_control_field=fecf,
            raw=raw,
        ),
        None,
    )


def parse_tm_transfer_frame(
    frame: bytes,
    *,
    config: TMTransferFrameConfig,
) -> TMTransferFrame | None:
    """Parse one exact-length Version-1 TM frame without asserting integrity."""

    parsed, _ = _parse_tm_transfer_frame(frame, config=config)
    return parsed


def validate_tm_transfer_frame(
    frame: bytes,
    *,
    config: TMTransferFrameConfig,
    integrity_validator: TMIntegrityValidator | None = None,
    integrity_name: str | None = None,
) -> TMTransferFrameValidation:
    """Validate structure and require a real integrity layer before acceptance.

    A configured FECF must be both present and correct.  With no FECF, an
    explicitly named caller validator is mandatory.  When both are supplied,
    both checks must pass; this prevents a bad declared FECF from being ignored.
    """

    if (integrity_validator is None) != (integrity_name is None):
        raise ValueError(
            "integrity validator and non-empty integrity name must be supplied together"
        )
    if integrity_name is not None and not integrity_name.strip():
        raise ValueError("integrity_name must be non-empty when supplied")
    if integrity_validator is not None and not callable(integrity_validator):
        raise TypeError("integrity_validator must be callable")

    parsed, structural_error = _parse_tm_transfer_frame(frame, config=config)
    if parsed is None:
        return TMTransferFrameValidation(
            frame=None,
            accepted=False,
            validation_layers=(),
            rejection_reason=structural_error,
        )

    layers = ["ccsds_tm_primary_header", "ccsds_tm_exact_frame_length"]
    if config.fecf_present:
        if not validate_tm_fecf(parsed.raw):
            return TMTransferFrameValidation(
                frame=parsed,
                accepted=False,
                validation_layers=tuple(layers),
                rejection_reason="invalid_fecf",
            )
        layers.append("ccsds_tm_fecf_crc16")
    elif integrity_validator is None:
        return TMTransferFrameValidation(
            frame=parsed,
            accepted=False,
            validation_layers=tuple(layers),
            rejection_reason="integrity_evidence_required",
        )

    if integrity_validator is not None:
        try:
            integrity_valid = bool(integrity_validator(parsed.raw, parsed))
        except Exception:
            return TMTransferFrameValidation(
                frame=parsed,
                accepted=False,
                validation_layers=tuple(layers),
                rejection_reason="integrity_validator_error",
            )
        if not integrity_valid:
            return TMTransferFrameValidation(
                frame=parsed,
                accepted=False,
                validation_layers=tuple(layers),
                rejection_reason="integrity_validator_rejected",
            )
        assert integrity_name is not None
        layers.append(integrity_name)

    return TMTransferFrameValidation(
        frame=parsed,
        accepted=True,
        validation_layers=tuple(layers),
        rejection_reason=None,
    )
