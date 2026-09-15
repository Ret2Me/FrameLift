"""Reference CRC-16/X-25 and AX.25 frame-check-sequence helpers."""

from __future__ import annotations

from collections.abc import Buffer

_CRC16_X25_INIT = 0xFFFF
_CRC16_X25_REFLECTED_POLY = 0x8408
_CRC16_X25_XOR_OUT = 0xFFFF


def _crc16_x25_table() -> tuple[int, ...]:
    """Build the reflected byte-transition table once at import time."""

    entries: list[int] = []
    for byte in range(256):
        value = byte
        for _ in range(8):
            value = (
                (value >> 1) ^ _CRC16_X25_REFLECTED_POLY
                if value & 1
                else value >> 1
            )
        entries.append(value)
    return tuple(entries)


_CRC16_X25_TABLE = _crc16_x25_table()


def crc16_x25(data: Buffer) -> int:
    """Return CRC-16/X-25 for bytes as an unsigned 16-bit integer.

    Parameters are poly=0x1021, init=0xffff, refin=true, refout=true, and
    xorout=0xffff. The check value for ``b"123456789"`` is ``0x906e``.
    """

    crc = _CRC16_X25_INIT
    for byte in memoryview(data).cast("B"):
        crc = (crc >> 8) ^ _CRC16_X25_TABLE[(crc ^ byte) & 0xFF]
    return (crc ^ _CRC16_X25_XOR_OUT) & 0xFFFF


def ax25_fcs(frame_without_fcs: Buffer) -> bytes:
    """Return the AX.25 FCS in on-wire byte order (least-significant first)."""

    return crc16_x25(frame_without_fcs).to_bytes(2, byteorder="little")


def append_ax25_fcs(frame_without_fcs: Buffer) -> bytes:
    """Return an AX.25 frame with its two-byte FCS appended."""

    payload = bytes(frame_without_fcs)
    return payload + ax25_fcs(payload)


def validate_ax25_fcs(frame_with_fcs: Buffer) -> bool:
    """Validate the final two bytes of an unstuffed AX.25 frame.

    HDLC flags and bit stuffing must already have been removed. Frames shorter
    than the two-byte FCS are invalid.
    """

    frame = bytes(frame_with_fcs)
    if len(frame) < 2:
        return False
    payload, received_fcs = frame[:-2], frame[-2:]
    return ax25_fcs(payload) == received_fcs
