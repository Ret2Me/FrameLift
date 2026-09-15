"""Vectorized implementation of the existing strict binary AX.25 adapter.

Only NRZI, zero-register G3RUH descrambling and flag search are vectorized.
HDLC unstuffing, byte alignment, frame limits, CRC and AX.25 validation retain
the reference implementation's semantics.  Both NRZI starting levels are
examined in reference order, including their effects on G3RUH startup bits.
This is a computational optimization, not a relaxed decoder or bit repair.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import numpy as np

from .afsk1200_plugin import _flag_offsets
from .ax25_validation import parse_ax25_ui
from .camras_replay import bits_to_bytes, hdlc_unstuff
from .clock_recovery import AX25_MAX_DECODER_FRAME_BYTES, AX25_MIN_PAYLOAD_BYTES
from .crc import validate_ax25_fcs
from .generic_receiver import Ax25ProtocolDecoder


def _binary_array(levels: Sequence[int]) -> np.ndarray:
    values = np.asarray(levels)
    if values.ndim != 1:
        raise ValueError("levels must be a one-dimensional binary array")
    if np.any((values != 0) & (values != 1)):
        raise ValueError("levels must contain only zero and one")
    # Validate before conversion: casting directly would silently turn 256 or
    # fractional values into binary data. Equality also avoids complex casts.
    return np.asarray(values == 1, dtype=np.uint8)


def _g3ruh_zero_register(bits: np.ndarray) -> np.ndarray:
    """Equivalent to lfsr(0x21, 0, 16) next_bit_descramble.

    Register taps 5 and 0 are received-bit delays 12 and 17 respectively.
    Missing history is zero, exactly as in the existing scalar implementation.
    This transformation uses received bits, not recursively decoded bits.
    """

    output = bits.copy()
    output[12:] ^= bits[:-12]
    output[17:] ^= bits[:-17]
    return output


def _frames_from_binary_bits(bits: np.ndarray) -> tuple[bytes, ...]:
    flags = _flag_offsets(bits)
    maximum_unstuffed_bits = (AX25_MAX_DECODER_FRAME_BYTES - 1) * 8
    maximum_stuffed_bits = maximum_unstuffed_bits + (maximum_unstuffed_bits + 4) // 5
    frames: list[bytes] = []
    seen: set[bytes] = set()
    for left, right in zip(flags[:-1], flags[1:], strict=True):
        region = bits[int(left) + 8 : int(right)]
        if region.size == 0 or region.size > maximum_stuffed_bits:
            continue
        try:
            unstuffed = hdlc_unstuff(region.tolist())
            frame = bits_to_bytes(unstuffed, lsb_first=True)
        except (ValueError, RuntimeError):
            continue
        if (
            AX25_MIN_PAYLOAD_BYTES + 2 <= len(frame) < AX25_MAX_DECODER_FRAME_BYTES
            and validate_ax25_fcs(frame)
            and frame not in seen
        ):
            seen.add(frame)
            frames.append(frame)
    return tuple(frames)


def fast_decode_ax25_levels(
    levels: Sequence[int], *, g3ruh: bool
) -> tuple[bytes, ...]:
    """Return the same ordered CRC-valid full frames as decode_ax25_levels."""

    checked = _binary_array(levels)
    if checked.size == 0:
        return ()
    bits = np.empty_like(checked)
    bits[1:] = checked[1:] == checked[:-1]
    frames: list[bytes] = []
    seen: set[bytes] = set()
    for initial_level in (0, 1):
        bits[0] = checked[0] == initial_level
        decoded = _g3ruh_zero_register(bits) if g3ruh else bits
        for frame in _frames_from_binary_bits(decoded):
            if frame not in seen:
                seen.add(frame)
                frames.append(frame)
    return tuple(frames)


@dataclass(frozen=True, slots=True)
class FastAx25ProtocolDecoder(Ax25ProtocolDecoder):
    """Drop-in native protocol plugin; validation capabilities are unchanged.

    Register under ``ax25_fast`` alongside existing adapters, or specify an
    explicit protocol_id when creating a separate receiver. This class keeps
    the reference adapter's mode order and full-frame/FCS return contract.
    """

    protocol_id: str = "ax25_fast"

    def decode(
        self, soft_symbols: Sequence[float], *, threshold: float
    ) -> tuple[tuple[bytes, str], ...]:
        # float(value) in the scalar adapter accepts real numeric scalars, but
        # not complex values; reject those before NumPy can discard imaginary
        # components. NaN/Inf slicing preserves the scalar comparison result.
        values = np.asarray(soft_symbols)
        if values.ndim != 1 or np.iscomplexobj(values):
            raise ValueError("soft symbols must be one-dimensional real values")
        real = np.asarray(values, dtype=np.float64)
        levels = np.asarray(real >= threshold, dtype=np.uint8)
        output: list[tuple[bytes, str]] = []
        seen: set[bytes] = set()
        for g3ruh in self.g3ruh_modes:
            for frame in fast_decode_ax25_levels(levels, g3ruh=g3ruh):
                if parse_ax25_ui(frame[:-2]) is not None and frame not in seen:
                    seen.add(frame)
                    output.append((frame, "crc16_x25+ax25_ui"))
        return tuple(output)
