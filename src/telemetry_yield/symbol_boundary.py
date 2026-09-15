"""Bit-exact helpers for localizing the RSP-03 replay boundary.

The routines mirror the order used by gr-satnogs 2.3.4.0's AX.25 decoder:
hard sliced NRZI levels are NRZI-decoded, optionally passed through GNU
Radio's ``lfsr(0x21, 0, 16)`` descrambler, and then HDLC-deframed LSB first.
They intentionally operate below the IQ/demodulator layer so a failure here
can be distinguished from symbol-clock recovery failure.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence

from .camras_replay import (
    ReplayError,
    bits_to_bytes,
    bytes_to_bits,
    hdlc_unstuff,
)
from .crc import append_ax25_fcs, validate_ax25_fcs


G3RUH_MASK = 0x21
G3RUH_ORDER = 16
AX25_FLAG_BITS = bytes_to_bits(b"\x7e", lsb_first=True)


@dataclass(frozen=True)
class HDLCScan:
    """Summary of complete flag-delimited regions in one decoded stream."""

    flag_count: int
    nonempty_regions: int
    octet_aligned_regions: int
    valid_fcs_regions: int
    undersized_valid_fcs_regions: int
    accepted_frames: tuple[bytes, ...]


def nrzi_decode_satnogs(
    levels: Iterable[int], *, initial_level: int = 0
) -> bytes:
    """Mirror gr-satnogs' NRZI rule: unchanged=1 and transition=0."""

    if initial_level not in (0, 1):
        raise ValueError("initial_level must be zero or one")
    previous = initial_level
    output = bytearray()
    for value in levels:
        if value not in (0, 1):
            raise ValueError("levels must contain only zero and one")
        output.append(1 if value == previous else 0)
        previous = value
    return bytes(output)


def g3ruh_descramble(
    bits: Iterable[int],
    *,
    seed: int = 0,
    mask: int = G3RUH_MASK,
    order: int = G3RUH_ORDER,
) -> bytes:
    """Mirror GNU Radio ``lfsr.next_bit_descramble`` one bit at a time.

    With mask ``0x21`` and order ``16``, the output is the input XORed with
    received bits delayed by 12 and 17 positions, implementing the usual
    :math:`x^{17} + x^{12} + 1` G3RUH descrambler.  The input itself is shifted
    into the 17-bit state, so any seed effect disappears after 17 bits.
    """

    if mask < 0 or order < 0 or seed < 0:
        raise ValueError("LFSR parameters must be non-negative")
    state = seed
    output = bytearray()
    for value in bits:
        if value not in (0, 1):
            raise ValueError("bits must contain only zero and one")
        output.append(((state & mask).bit_count() ^ value) & 1)
        state = (state >> 1) | (value << order)
    return bytes(output)


def g3ruh_input_for_plain_bits(
    plain_bits: Iterable[int],
    *,
    seed: int = 0,
    mask: int = G3RUH_MASK,
    order: int = G3RUH_ORDER,
) -> bytes:
    """Construct input bits that the matching descrambler maps to plaintext."""

    if mask < 0 or order < 0 or seed < 0:
        raise ValueError("LFSR parameters must be non-negative")
    state = seed
    output = bytearray()
    for value in plain_bits:
        if value not in (0, 1):
            raise ValueError("bits must contain only zero and one")
        encoded = ((state & mask).bit_count() ^ value) & 1
        output.append(encoded)
        state = (state >> 1) | (encoded << order)
    return bytes(output)


def nrzi_encode_satnogs(
    bits: Iterable[int], *, initial_level: int = 0
) -> bytes:
    """Inverse of :func:`nrzi_decode_satnogs` for a named initial state."""

    if initial_level not in (0, 1):
        raise ValueError("initial_level must be zero or one")
    level = initial_level
    output = bytearray()
    for value in bits:
        if value not in (0, 1):
            raise ValueError("bits must contain only zero and one")
        if value == 0:
            level ^= 1
        output.append(level)
    return bytes(output)


def decode_satnogs_symbols(
    levels: Iterable[int], *, descramble: bool
) -> bytes:
    """Apply the exact gr-satnogs bit-layer order to hard sliced levels."""

    nrzi = nrzi_decode_satnogs(levels, initial_level=0)
    return g3ruh_descramble(nrzi) if descramble else nrzi


def stuff_hdlc_bits(bits: Iterable[int]) -> bytes:
    """Insert a zero after every run of five data ones."""

    output = bytearray()
    ones = 0
    for value in bits:
        if value not in (0, 1):
            raise ValueError("bits must contain only zero and one")
        output.append(value)
        if value:
            ones += 1
            if ones == 5:
                output.append(0)
                ones = 0
        else:
            ones = 0
    return bytes(output)


def reference_hdlc_region(payload: bytes) -> bytes:
    """Return stuffed LSB-first frame-plus-FCS bits without surrounding flags."""

    framed = append_ax25_fcs(payload)
    return stuff_hdlc_bits(bytes_to_bits(framed, lsb_first=True))


def synthetic_sliced_levels(payload: bytes, *, preamble_flags: int = 3) -> bytes:
    """Build a source-faithful positive-control sliced stream."""

    if preamble_flags < 1:
        raise ValueError("at least one preamble flag is required")
    plain = bytes(AX25_FLAG_BITS) * preamble_flags
    plain += reference_hdlc_region(payload)
    plain += bytes(AX25_FLAG_BITS)
    scrambled = g3ruh_input_for_plain_bits(plain)
    return nrzi_encode_satnogs(scrambled, initial_level=0)


def scan_hdlc(
    decoded_bits: Sequence[int],
    *,
    min_payload_bytes: int = 14,
    max_frame_bytes: int = 1024,
) -> HDLCScan:
    """Scan complete HDLC regions using gr-satnogs' size/FCS contract.

    The returned accepted frames have their validated two-byte FCS removed.
    As in gr-satnogs 2.3.4.0, a received frame must contain at least the
    14-byte AX.25 address field plus FCS and must remain below the configured
    maximum of 1024 received bytes.
    """

    if min_payload_bytes < 0 or max_frame_bytes <= 2:
        raise ValueError("invalid frame size limits")
    if any(value not in (0, 1) for value in decoded_bits):
        raise ValueError("decoded_bits must contain only zero and one")

    shift = 0
    flag_starts: list[int] = []
    for index, value in enumerate(decoded_bits):
        shift = (shift >> 1) | (value << 7)
        if shift == 0x7E:
            flag_starts.append(index - 7)

    nonempty = 0
    aligned = 0
    valid_fcs = 0
    undersized = 0
    accepted: list[bytes] = []
    maximum_encoded_bits = 2 * max_frame_bytes * 8
    for left, right in zip(flag_starts, flag_starts[1:]):
        region = decoded_bits[left + 8 : right]
        if not region:
            continue
        nonempty += 1
        if len(region) > maximum_encoded_bits:
            continue
        try:
            unstuffed = hdlc_unstuff(region)
            frame = bits_to_bytes(unstuffed, lsb_first=True)
        except ReplayError:
            continue
        aligned += 1
        if not validate_ax25_fcs(frame):
            continue
        valid_fcs += 1
        payload = frame[:-2]
        if len(payload) < min_payload_bytes:
            undersized += 1
            continue
        if len(frame) >= max_frame_bytes:
            continue
        accepted.append(payload)

    return HDLCScan(
        flag_count=len(flag_starts),
        nonempty_regions=nonempty,
        octet_aligned_regions=aligned,
        valid_fcs_regions=valid_fcs,
        undersized_valid_fcs_regions=undersized,
        accepted_frames=tuple(accepted),
    )
