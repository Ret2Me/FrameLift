from __future__ import annotations

from pathlib import Path
import struct

import pytest

from telemetry_yield.pcap_ipv4_audit import (
    audit_length_prefixed_echo_requests,
    audit_pcap_echo_requests,
    internet_checksum,
)


def _echo_request(payload: bytes = b"test", *, corrupt_icmp: bool = False) -> bytes:
    icmp = bytearray(struct.pack("!BBHHH", 8, 0, 0, 7, 11) + payload)
    struct.pack_into("!H", icmp, 2, internet_checksum(icmp))
    if corrupt_icmp:
        icmp[-1] ^= 1
    ip = bytearray(20)
    ip[0] = 0x45
    struct.pack_into("!H", ip, 2, len(ip) + len(icmp))
    struct.pack_into("!H", ip, 4, 123)
    ip[8] = 64
    ip[9] = 1
    ip[12:16] = b"\xc0\xa8\x0a\x01"
    ip[16:20] = b"\xc0\xa8\x0a\x02"
    struct.pack_into("!H", ip, 10, internet_checksum(ip))
    return bytes(ip + icmp)


def _write_pcap(path: Path, packets: list[bytes], *, link_type: int = 101) -> None:
    payload = bytearray(struct.pack("<IHHIIII", 0xA1B2C3D4, 2, 4, 0, 0, 65535, link_type))
    for index, packet in enumerate(packets):
        payload.extend(struct.pack("<IIII", 100 + index, 250_000, len(packet), len(packet)))
        payload.extend(packet)
    path.write_bytes(payload)


def test_audit_accepts_strict_packets_and_deduplicates(tmp_path: Path) -> None:
    packet = _echo_request()
    path = tmp_path / "capture.pcap"
    _write_pcap(path, [packet, packet])
    result = audit_pcap_echo_requests(path)
    assert result["records"] == 2
    assert result["accepted_echo_requests"] == 2
    assert result["unique_echo_requests"] == 1
    assert result["duplicate_echo_requests"] == 1
    assert result["rejected_records"] == 0
    assert result["packets"][0]["source"] == "192.168.10.1"
    assert result["packets"][0]["icmp_sequence"] == 11


def test_audit_rejects_bad_icmp_checksum(tmp_path: Path) -> None:
    path = tmp_path / "capture.pcap"
    _write_pcap(path, [_echo_request(corrupt_icmp=True)])
    result = audit_pcap_echo_requests(path)
    assert result["accepted_echo_requests"] == 0
    assert result["rejection_reasons"] == {"invalid ICMP checksum": 1}


def test_audit_supports_linux_cooked_v2(tmp_path: Path) -> None:
    sll2 = struct.pack("!HHIHBB8s", 0x0800, 0, 1, 1, 0, 0, b"\x00" * 8)
    path = tmp_path / "capture.pcap"
    _write_pcap(path, [sll2 + _echo_request()], link_type=276)
    assert audit_pcap_echo_requests(path)["accepted_echo_requests"] == 1


def test_audit_fails_closed_on_truncated_record(tmp_path: Path) -> None:
    path = tmp_path / "capture.pcap"
    _write_pcap(path, [_echo_request()])
    path.write_bytes(path.read_bytes()[:-1])
    with pytest.raises(ValueError, match="truncated PCAP packet data"):
        audit_pcap_echo_requests(path)


def test_length_prefixed_audit_accepts_and_rejects_strictly(tmp_path: Path) -> None:
    valid = _echo_request()
    invalid = _echo_request(corrupt_icmp=True)
    path = tmp_path / "pdus.bin"
    path.write_bytes(
        struct.pack("!I", len(valid))
        + valid
        + struct.pack("!I", len(invalid))
        + invalid
    )
    result = audit_length_prefixed_echo_requests(path)
    assert result["records"] == 2
    assert result["accepted_echo_requests"] == 1
    assert result["rejection_reasons"] == {"invalid ICMP checksum": 1}


def test_length_prefixed_audit_fails_on_truncated_record(tmp_path: Path) -> None:
    path = tmp_path / "pdus.bin"
    path.write_bytes(struct.pack("!I", 100) + b"short")
    with pytest.raises(ValueError, match="truncated PDU record"):
        audit_length_prefixed_echo_requests(path)
