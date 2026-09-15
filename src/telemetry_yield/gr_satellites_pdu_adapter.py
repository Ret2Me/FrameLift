"""Bit-exact gr-satellites G3RUH/HDLC PDU compatibility adapter.

This module intentionally exposes gr-satellites' *PDU emission* contract as a
separate compatibility lane.  It does not relax :mod:`ax25_validation`: a PDU
that passes HDLC FCS but is too short or malformed as AX.25 remains explicitly
classified as such.

The implementation mirrors the locally pinned gr-satellites 5.9.0 pipeline::

    binary slicer -> NRZI decoder -> descrambler_bb(0x21, 0, 16)
        -> hdlc_deframer(check_fcs=True, max_length=10000)

In particular, ``hdlc_deframer`` removes stuffing while streaming and pads an
unaligned region with zero bits on the *left* before LSB-first packing and CRC
checking.  This unusual but externally observable rule is required to recover
the 12-byte PDU emitted by the official replay of observation 4704.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from operator import index
from typing import Iterable, Literal

from .ax25_validation import AX25UIFrame, parse_ax25_ui
from .crc import validate_ax25_fcs


ADAPTER_SCHEMA_VERSION = "gr-satellites-g3ruh-hdlc-pdu-adapter-v1"
GR_SATELLITES_VERSION = "5.9.0"
G3RUH_MASK = 0x21
G3RUH_SEED = 0
G3RUH_ORDER = 16
GNU_HDLC_MAX_LENGTH = 10_000
AX25_MIN_ADDRESS_BYTES = 14

PDUClassification = Literal[
    "strict_ax25_ui",
    "crc_valid_undersized_pdu",
    "crc_valid_non_ax25_pdu",
]


@dataclass(frozen=True)
class GrSatellitesPDUProvenance:
    """Observable decisions made while reproducing the GNU deframer."""

    adapter_schema_version: str
    gr_satellites_version: str
    input_stage: str
    pipeline: tuple[str, ...]
    opening_flag_end_bit_index: int | None
    closing_flag_end_bit_index: int
    raw_unstuffed_bit_count: int
    left_padding_bits: int
    stuffed_zero_count: int
    truncated_unstuffed_bits: int
    fcs_checked: bool
    fcs_trimmed_from_pdu: bool


@dataclass(frozen=True)
class GrSatellitesPDUCandidate:
    """One CRC-valid PDU emitted under gr-satellites compatibility semantics."""

    pdu: bytes
    frame_with_fcs: bytes
    classification: PDUClassification
    undersized_for_ax25: bool
    strict_ax25_ui: AX25UIFrame | None
    provenance: GrSatellitesPDUProvenance


def _bit(value: object, *, label: str) -> int:
    """Return an integer bit while accepting GNU/NumPy integer scalars."""

    try:
        normalized = index(value)
    except TypeError as error:
        raise ValueError(f"{label} must contain integer zero/one values") from error
    if normalized not in (0, 1):
        raise ValueError(f"{label} must contain only zero and one")
    return normalized


def _pack_lsb_first(bits: Iterable[int]) -> bytes:
    """Mirror ``satellites.hdlc_deframer.pack`` exactly."""

    checked = tuple(bits)
    if len(checked) % 8:
        raise ValueError("internal error: padded bit sequence is not octet-aligned")
    output = bytearray()
    for offset in range(0, len(checked), 8):
        value = 0
        for reverse_index in range(7, -1, -1):
            value <<= 1
            value += checked[offset + reverse_index]
        output.append(value)
    return bytes(output)


def _classification(
    pdu: bytes,
) -> tuple[PDUClassification, bool, AX25UIFrame | None]:
    undersized = len(pdu) < AX25_MIN_ADDRESS_BYTES
    parsed = parse_ax25_ui(pdu)
    if parsed is not None:
        return "strict_ax25_ui", undersized, parsed
    if undersized:
        return "crc_valid_undersized_pdu", True, None
    return "crc_valid_non_ax25_pdu", False, None


def _extract_pdus(
    decoded_bits: Iterable[object],
    *,
    max_length: int,
    input_stage: str,
    pipeline: tuple[str, ...],
) -> tuple[GrSatellitesPDUCandidate, ...]:
    if not isinstance(max_length, int) or isinstance(max_length, bool):
        raise ValueError("max_length must be an integer")
    if max_length < 1:
        raise ValueError("max_length must be positive")

    # This is the exact capacity expression in gr-satellites 5.9.0.  A deque
    # silently discards its oldest element on overflow, which is also part of
    # the compatibility behavior and is disclosed per candidate below.
    bits: deque[int] = deque(maxlen=(max_length + 2) * 8 + 7)
    ones = 0
    stuffed_zero_count = 0
    truncated_unstuffed_bits = 0
    last_flag_end: int | None = None
    candidates: list[GrSatellitesPDUCandidate] = []

    def append_bit(value: int) -> None:
        nonlocal truncated_unstuffed_bits
        if len(bits) == bits.maxlen:
            truncated_unstuffed_bits += 1
        bits.append(value)

    for bit_index, raw_value in enumerate(decoded_bits):
        value = _bit(raw_value, label="decoded_bits")
        if value:
            ones += 1
            append_bit(value)
            continue

        if ones == 5:
            # GNU treats this zero as stuffing and does not append it.
            stuffed_zero_count += 1
        elif ones > 5:
            # GNU treats any run longer than five as a delimiter/abort and
            # removes exactly seven already-buffered bits.  For a normal flag
            # these are its leading zero plus six ones.
            for _ in range(min(7, len(bits))):
                bits.pop()

            raw_unstuffed_bit_count = len(bits)
            left_padding_bits = (-raw_unstuffed_bit_count) % 8
            bits.extendleft([0] * left_padding_bits)
            frame_with_fcs = _pack_lsb_first(bits)
            bits.clear()

            # gr-satellites' hdlc_crc_check rejects lengths <= 2 even when the
            # two bytes happen to equal the CRC of an empty payload.
            if len(frame_with_fcs) > 2 and validate_ax25_fcs(frame_with_fcs):
                pdu = frame_with_fcs[:-2]
                classification, undersized, parsed = _classification(pdu)
                candidates.append(
                    GrSatellitesPDUCandidate(
                        pdu=pdu,
                        frame_with_fcs=frame_with_fcs,
                        classification=classification,
                        undersized_for_ax25=undersized,
                        strict_ax25_ui=parsed,
                        provenance=GrSatellitesPDUProvenance(
                            adapter_schema_version=ADAPTER_SCHEMA_VERSION,
                            gr_satellites_version=GR_SATELLITES_VERSION,
                            input_stage=input_stage,
                            pipeline=pipeline,
                            opening_flag_end_bit_index=last_flag_end,
                            closing_flag_end_bit_index=bit_index,
                            raw_unstuffed_bit_count=raw_unstuffed_bit_count,
                            left_padding_bits=left_padding_bits,
                            stuffed_zero_count=stuffed_zero_count,
                            truncated_unstuffed_bits=truncated_unstuffed_bits,
                            fcs_checked=True,
                            fcs_trimmed_from_pdu=True,
                        ),
                    )
                )

            last_flag_end = bit_index
            stuffed_zero_count = 0
            truncated_unstuffed_bits = 0
        else:
            append_bit(value)
        ones = 0

    return tuple(candidates)


def extract_gr_satellites_hdlc_pdus(
    decoded_bits: Iterable[object],
    *,
    max_length: int = GNU_HDLC_MAX_LENGTH,
) -> tuple[GrSatellitesPDUCandidate, ...]:
    """Extract CRC-valid PDUs using gr-satellites' exact HDLC semantics.

    ``decoded_bits`` must be the stream after optional line-code
    descrambling.  Returned PDUs have their two-byte FCS removed, exactly as
    gr-satellites does.  Structural AX.25 trust is reported separately.
    """

    return _extract_pdus(
        decoded_bits,
        max_length=max_length,
        input_stage="descrambled_binary_bits",
        pipeline=(
            f"hdlc_deframer(check_fcs=True,max_length={max_length})",
            "left_zero_pad_to_octet_boundary",
            "lsb_first_pack",
            "crc16_x25_check",
            "trim_two_byte_fcs",
        ),
    )


def decode_gr_satellites_g3ruh_levels(
    levels: Iterable[object],
    *,
    max_length: int = GNU_HDLC_MAX_LENGTH,
) -> tuple[GrSatellitesPDUCandidate, ...]:
    """Run the exact post-clock gr-satellites AX.25 G3RUH bit pipeline.

    Input values are hard-sliced NRZI/G3RUH levels.  GNU's NRZI decoder starts
    at level zero, then the self-synchronizing descrambler uses mask ``0x21``,
    seed ``0`` and order ``16``.  The function is streaming and accepts NumPy
    integer scalars without depending on NumPy at runtime.
    """

    def decoded() -> Iterable[int]:
        previous_level = 0
        descrambler_state = G3RUH_SEED
        for raw_level in levels:
            level = _bit(raw_level, label="levels")
            nrzi_bit = 1 if level == previous_level else 0
            previous_level = level
            output = ((descrambler_state & G3RUH_MASK).bit_count() ^ nrzi_bit) & 1
            descrambler_state = (
                (descrambler_state >> 1) | (nrzi_bit << G3RUH_ORDER)
            )
            yield output

    return _extract_pdus(
        decoded(),
        max_length=max_length,
        input_stage="hard_sliced_nrzi_g3ruh_levels",
        pipeline=(
            "nrzi_decode(initial_level=0,unchanged=1)",
            "descrambler_bb(mask=0x21,seed=0,length=16)",
            f"hdlc_deframer(check_fcs=True,max_length={max_length})",
            "left_zero_pad_to_octet_boundary",
            "lsb_first_pack",
            "crc16_x25_check",
            "trim_two_byte_fcs",
        ),
    )


__all__ = [
    "ADAPTER_SCHEMA_VERSION",
    "AX25_MIN_ADDRESS_BYTES",
    "G3RUH_MASK",
    "G3RUH_ORDER",
    "G3RUH_SEED",
    "GNU_HDLC_MAX_LENGTH",
    "GR_SATELLITES_VERSION",
    "GrSatellitesPDUCandidate",
    "GrSatellitesPDUProvenance",
    "PDUClassification",
    "decode_gr_satellites_g3ruh_levels",
    "extract_gr_satellites_hdlc_pdus",
]
