"""Structural validation for CCSDS Space Packets.

This validator is deliberately independent from AX.25.  A packet may be a
raw CCSDS Space Packet, or (as with CANVAS) the information field carried by
an outer AX.25 frame.
"""

from __future__ import annotations

from dataclasses import dataclass


CCSDS_PRIMARY_HEADER_LENGTH = 6


@dataclass(frozen=True)
class CCSDSPrimaryHeader:
    """Decoded fields from the six-octet CCSDS Space Packet primary header."""

    version: int
    packet_type: int
    secondary_header: bool
    apid: int
    sequence_flags: int
    sequence_count: int
    packet_data_length: int
    total_packet_length: int


def parse_ccsds_space_packet(
    packet: bytes, *, require_exact_length: bool = True
) -> CCSDSPrimaryHeader | None:
    """Validate a complete Space Packet and return its primary header.

    The CCSDS packet-length field is one less than the number of octets after
    the primary header.  Therefore, even a zero-valued field declares one
    data octet.  Version values other than zero, truncated packets, and (by
    default) trailing bytes are rejected.

    Set ``require_exact_length=False`` when parsing a packet at the start of
    a larger byte stream.  The declared packet must still be wholly present.
    """

    if len(packet) < CCSDS_PRIMARY_HEADER_LENGTH:
        return None

    packet_id = int.from_bytes(packet[0:2], "big")
    sequence_control = int.from_bytes(packet[2:4], "big")
    encoded_length = int.from_bytes(packet[4:6], "big")

    version = (packet_id >> 13) & 0x07
    if version != 0:
        return None

    packet_data_length = encoded_length + 1
    total_packet_length = CCSDS_PRIMARY_HEADER_LENGTH + packet_data_length
    if len(packet) < total_packet_length:
        return None
    if require_exact_length and len(packet) != total_packet_length:
        return None

    return CCSDSPrimaryHeader(
        version=version,
        packet_type=(packet_id >> 12) & 0x01,
        secondary_header=bool(packet_id & 0x0800),
        apid=packet_id & 0x07FF,
        sequence_flags=(sequence_control >> 14) & 0x03,
        sequence_count=sequence_control & 0x3FFF,
        packet_data_length=packet_data_length,
        total_packet_length=total_packet_length,
    )
