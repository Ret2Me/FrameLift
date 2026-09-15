"""Strict, dependency-free audit of IPv4/ICMP packets in classic PCAP files."""

from __future__ import annotations

from collections import Counter
import hashlib
import ipaddress
from pathlib import Path
import struct


PCAP_AUDIT_SCHEMA = "telemetry-yield-pcap-ipv4-audit-v1"
PDU_AUDIT_SCHEMA = "telemetry-yield-length-prefixed-ipv4-audit-v1"
DLT_RAW = 101
DLT_LINUX_SLL2 = 276


def internet_checksum(payload: bytes) -> int:
    """Return the RFC 1071 Internet checksum; valid protected data returns zero."""

    if len(payload) % 2:
        payload += b"\x00"
    total = sum(struct.unpack(f"!{len(payload) // 2}H", payload))
    while total >> 16:
        total = (total & 0xFFFF) + (total >> 16)
    return (~total) & 0xFFFF


def _pcap_layout(header: bytes) -> tuple[str, str]:
    if len(header) != 24:
        raise ValueError("truncated PCAP global header")
    magic = header[:4]
    layouts = {
        b"\xd4\xc3\xb2\xa1": ("<", "microseconds"),
        b"\xa1\xb2\xc3\xd4": (">", "microseconds"),
        b"\x4d\x3c\xb2\xa1": ("<", "nanoseconds"),
        b"\xa1\xb2\x3c\x4d": (">", "nanoseconds"),
    }
    try:
        return layouts[magic]
    except KeyError as error:
        raise ValueError("unsupported PCAP magic") from error


def _ip_from_link(packet: bytes, link_type: int) -> bytes:
    if link_type == DLT_RAW:
        return packet
    if link_type == DLT_LINUX_SLL2:
        if len(packet) < 20:
            raise ValueError("truncated Linux cooked v2 header")
        protocol = struct.unpack("!H", packet[:2])[0]
        if protocol != 0x0800:
            raise ValueError("non-IPv4 Linux cooked packet")
        return packet[20:]
    raise ValueError(f"unsupported PCAP link type {link_type}")


def _audit_echo_request(packet: bytes) -> dict[str, object]:
    if len(packet) < 20:
        raise ValueError("truncated IPv4 header")
    version = packet[0] >> 4
    ihl = (packet[0] & 0x0F) * 4
    if version != 4 or ihl < 20 or ihl > len(packet):
        raise ValueError("invalid IPv4 version or header length")
    total_length = struct.unpack("!H", packet[2:4])[0]
    if total_length < ihl + 8 or total_length != len(packet):
        raise ValueError("IPv4 total length does not equal captured packet length")
    ip = packet[:total_length]
    if internet_checksum(ip[:ihl]) != 0:
        raise ValueError("invalid IPv4 header checksum")
    fragment = struct.unpack("!H", ip[6:8])[0]
    if fragment & 0x3FFF:
        raise ValueError("fragmented IPv4 packet")
    if ip[9] != 1:
        raise ValueError("non-ICMP IPv4 packet")
    icmp = ip[ihl:]
    if icmp[0] != 8 or icmp[1] != 0:
        raise ValueError("non-echo-request ICMP packet")
    if internet_checksum(icmp) != 0:
        raise ValueError("invalid ICMP checksum")
    identifier, sequence = struct.unpack("!HH", icmp[4:8])
    return {
        "sha256": hashlib.sha256(ip).hexdigest(),
        "length_bytes": total_length,
        "source": str(ipaddress.IPv4Address(ip[12:16])),
        "destination": str(ipaddress.IPv4Address(ip[16:20])),
        "icmp_identifier": identifier,
        "icmp_sequence": sequence,
    }


def audit_pcap_echo_requests(path: str | Path) -> dict[str, object]:
    """Validate every record and inventory strict IPv4 ICMP echo requests.

    A record is accepted only when the complete IPv4 and ICMP checksums validate.
    Rejected records remain counted by a non-disclosing reason.
    """

    source = Path(path)
    pcap_sha = hashlib.sha256()
    accepted: list[dict[str, object]] = []
    rejected: Counter[str] = Counter()
    records = 0
    first_timestamp: float | None = None
    last_timestamp: float | None = None
    with source.open("rb") as handle:
        global_header = handle.read(24)
        pcap_sha.update(global_header)
        endian, resolution = _pcap_layout(global_header)
        _, major, minor, _, _, snaplen, link_type = struct.unpack(
            f"{endian}IHHIIII", global_header
        )
        if (major, minor) != (2, 4):
            raise ValueError(f"unsupported PCAP version {major}.{minor}")
        divisor = 1_000_000_000 if resolution == "nanoseconds" else 1_000_000
        while True:
            record_header = handle.read(16)
            if not record_header:
                break
            pcap_sha.update(record_header)
            if len(record_header) != 16:
                raise ValueError("truncated PCAP record header")
            seconds, fraction, included, original = struct.unpack(
                f"{endian}IIII", record_header
            )
            if included > snaplen or included > original:
                raise ValueError("invalid PCAP record lengths")
            packet = handle.read(included)
            pcap_sha.update(packet)
            if len(packet) != included:
                raise ValueError("truncated PCAP packet data")
            timestamp = seconds + fraction / divisor
            first_timestamp = timestamp if first_timestamp is None else first_timestamp
            last_timestamp = timestamp
            records += 1
            try:
                accepted.append(_audit_echo_request(_ip_from_link(packet, link_type)))
            except ValueError as error:
                rejected[str(error)] += 1
    unique = {row["sha256"]: row for row in accepted}
    return {
        "schema_version": PCAP_AUDIT_SCHEMA,
        "path": str(source),
        "pcap_sha256": pcap_sha.hexdigest(),
        "pcap_size_bytes": source.stat().st_size,
        "link_type": link_type,
        "records": records,
        "accepted_echo_requests": len(accepted),
        "unique_echo_requests": len(unique),
        "duplicate_echo_requests": len(accepted) - len(unique),
        "rejected_records": sum(rejected.values()),
        "rejection_reasons": dict(sorted(rejected.items())),
        "first_timestamp": first_timestamp,
        "last_timestamp": last_timestamp,
        "packets": [unique[digest] for digest in sorted(unique)],
    }


def audit_length_prefixed_echo_requests(path: str | Path) -> dict[str, object]:
    """Audit big-endian uint32-length-prefixed IPv4 PDUs from a file sink."""

    source = Path(path)
    payload = source.read_bytes()
    accepted: list[dict[str, object]] = []
    rejected: Counter[str] = Counter()
    offset = 0
    records = 0
    while offset < len(payload):
        if len(payload) - offset < 4:
            raise ValueError("truncated PDU record header")
        size = struct.unpack("!I", payload[offset : offset + 4])[0]
        offset += 4
        if size == 0 or size > 65_535:
            raise ValueError("invalid PDU record length")
        if len(payload) - offset < size:
            raise ValueError("truncated PDU record")
        packet = payload[offset : offset + size]
        offset += size
        records += 1
        try:
            accepted.append(_audit_echo_request(packet))
        except ValueError as error:
            rejected[str(error)] += 1
    unique = {row["sha256"]: row for row in accepted}
    return {
        "schema_version": PDU_AUDIT_SCHEMA,
        "path": str(source),
        "file_sha256": hashlib.sha256(payload).hexdigest(),
        "file_size_bytes": len(payload),
        "records": records,
        "accepted_echo_requests": len(accepted),
        "unique_echo_requests": len(unique),
        "duplicate_echo_requests": len(accepted) - len(unique),
        "rejected_records": sum(rejected.values()),
        "rejection_reasons": dict(sorted(rejected.items())),
        "packets": [unique[digest] for digest in sorted(unique)],
    }
