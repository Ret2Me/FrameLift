"""Auditable cohort randomization from a preregistered future NIST pulse."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import re
import ssl
from typing import Any, Mapping


_HEX_512 = re.compile(r"^[0-9A-Fa-f]{128}$")


def _timestamp(value: object) -> datetime:
    if not isinstance(value, str):
        raise ValueError("beacon timeStamp must be text")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("beacon timeStamp is not ISO-8601") from error
    if parsed.tzinfo is None:
        raise ValueError("beacon timeStamp must include UTC offset")
    return parsed.astimezone(timezone.utc)


def validate_nist_beacon_pulse(
    document: Mapping[str, Any],
    *,
    expected_timestamp: str,
    certificate_pem: str,
) -> dict[str, Any]:
    """Validate the fields needed to bind selection to one exact NIST pulse.

    The certificate identifier used by Beacon 2.0 is the SHA-512 digest of its
    DER representation.  This function verifies that binding and the exact
    pulse time.  It intentionally does not claim to verify the pulse signature;
    signature verification is recorded as a separate provenance gate.
    """

    pulse = document.get("pulse")
    if not isinstance(pulse, Mapping):
        raise ValueError("NIST response must contain one pulse object")
    expected = _timestamp(expected_timestamp)
    actual = _timestamp(pulse.get("timeStamp"))
    if actual != expected:
        raise ValueError("NIST pulse timestamp differs from preregistration")
    if pulse.get("version") != "2.0" or pulse.get("period") != 60_000:
        raise ValueError("unexpected NIST beacon version or period")
    if pulse.get("statusCode") != 0:
        raise ValueError("NIST beacon pulse is not healthy")
    output_value = pulse.get("outputValue")
    certificate_id = pulse.get("certificateId")
    signature_value = pulse.get("signatureValue")
    if not isinstance(output_value, str) or not _HEX_512.fullmatch(output_value):
        raise ValueError("NIST outputValue must be exactly 512 bits")
    if not isinstance(certificate_id, str) or not _HEX_512.fullmatch(certificate_id):
        raise ValueError("NIST certificateId must be exactly 512 bits")
    if not isinstance(signature_value, str) or not signature_value or any(
        character not in "0123456789abcdefABCDEF" for character in signature_value
    ):
        raise ValueError("NIST signatureValue must be non-empty hexadecimal")
    try:
        certificate_der = ssl.PEM_cert_to_DER_cert(certificate_pem)
    except ValueError as error:
        raise ValueError("invalid NIST certificate PEM") from error
    certificate_sha512 = hashlib.sha512(certificate_der).hexdigest()
    if certificate_sha512 != certificate_id.casefold():
        raise ValueError("NIST certificate SHA-512 does not match certificateId")
    chain_index = pulse.get("chainIndex")
    pulse_index = pulse.get("pulseIndex")
    if (
        isinstance(chain_index, bool)
        or not isinstance(chain_index, int)
        or chain_index < 1
        or isinstance(pulse_index, bool)
        or not isinstance(pulse_index, int)
        or pulse_index < 1
    ):
        raise ValueError("NIST chain and pulse indices must be positive integers")
    return {
        "version": "2.0",
        "time_stamp": actual.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        "period_milliseconds": 60_000,
        "chain_index": chain_index,
        "pulse_index": pulse_index,
        "uri": pulse.get("uri"),
        "output_value": output_value.upper(),
        "output_value_sha256": hashlib.sha256(bytes.fromhex(output_value)).hexdigest(),
        "certificate_id": certificate_id.casefold(),
        "certificate_der_sha512": certificate_sha512,
        "signature_value_sha256": hashlib.sha256(
            bytes.fromhex(signature_value)
        ).hexdigest(),
        "certificate_identifier_verified": True,
        "pulse_signature_verified": False,
    }


def derive_selection_salt(
    *, domain: str, preregistration_sha256: str, output_value: str
) -> bytes:
    """Domain-separate one future pulse for one immutable preregistration."""

    if not domain or "\n" in domain:
        raise ValueError("selection domain must be non-empty and single-line")
    if not re.fullmatch(r"[0-9a-f]{64}", preregistration_sha256):
        raise ValueError("preregistration_sha256 must be lowercase SHA-256")
    if not _HEX_512.fullmatch(output_value):
        raise ValueError("output_value must be exactly 512 bits")
    payload = (
        domain.encode("utf-8")
        + b"\x00"
        + bytes.fromhex(preregistration_sha256)
        + b"\x00"
        + bytes.fromhex(output_value)
    )
    return hashlib.sha256(payload).digest()


def rank_integer(selection_salt: bytes, identifier: int) -> str:
    """Return a stable rank for a positive integer without text ambiguity."""

    if len(selection_salt) != hashlib.sha256().digest_size:
        raise ValueError("selection_salt must be 32 bytes")
    if isinstance(identifier, bool) or not isinstance(identifier, int) or identifier < 1:
        raise ValueError("identifier must be a positive integer")
    encoded = identifier.to_bytes(max(1, (identifier.bit_length() + 7) // 8), "big")
    return hashlib.sha256(
        b"telemetry-yield/rank-positive-integer/v1\x00"
        + selection_salt
        + len(encoded).to_bytes(2, "big")
        + encoded
    ).hexdigest()
