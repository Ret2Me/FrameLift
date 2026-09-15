"""Strict structural validation for decoded AX.25 information frames.

CRC validity alone is not enough when millions of soft-decision candidates
are searched.  These helpers independently validate the address chain and UI
control/PID fields, so random CRC collisions are not reported as telemetry.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AX25Address:
    callsign: str
    ssid: int
    command_or_repeated: bool
    final: bool


@dataclass(frozen=True)
class AX25UIFrame:
    destination: AX25Address
    source: AX25Address
    digipeaters: tuple[AX25Address, ...]
    pid: int
    information: bytes


def _address(raw: bytes) -> AX25Address | None:
    if len(raw) != 7 or any(value & 1 for value in raw[:6]):
        return None
    decoded = bytes(value >> 1 for value in raw[:6])
    if any(
        not (value == 0x20 or 0x30 <= value <= 0x39 or 0x41 <= value <= 0x5A)
        for value in decoded
    ):
        return None
    callsign = decoded.decode("ascii").rstrip()
    if not callsign or " " in callsign:
        return None
    ssid_byte = raw[6]
    if ssid_byte & 0x60 != 0x60:
        return None
    return AX25Address(
        callsign=callsign,
        ssid=(ssid_byte >> 1) & 0x0F,
        command_or_repeated=bool(ssid_byte & 0x80),
        final=bool(ssid_byte & 0x01),
    )


def parse_ax25_ui(payload: bytes) -> AX25UIFrame | None:
    """Parse one complete AX.25 UI payload without its two FCS octets.

    The address chain must contain destination and source, terminate within
    ten addresses, use shifted uppercase/digit callsigns and the required
    reserved SSID bits, and be followed by UI control 0x03 plus a PID octet.
    """

    addresses: list[AX25Address] = []
    offset = 0
    for _ in range(10):
        if offset + 7 > len(payload):
            return None
        parsed = _address(payload[offset : offset + 7])
        if parsed is None:
            return None
        addresses.append(parsed)
        offset += 7
        if parsed.final:
            break
    else:
        return None
    if len(addresses) < 2 or not addresses[-1].final:
        return None
    if offset + 2 > len(payload) or payload[offset] != 0x03:
        return None
    return AX25UIFrame(
        destination=addresses[0],
        source=addresses[1],
        digipeaters=tuple(addresses[2:]),
        pid=payload[offset + 1],
        information=payload[offset + 2 :],
    )
