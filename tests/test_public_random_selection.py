from __future__ import annotations

import hashlib
import ssl

import pytest

from telemetry_yield.public_random_selection import (
    derive_selection_salt,
    rank_integer,
    validate_nist_beacon_pulse,
)


CERTIFICATE = """-----BEGIN CERTIFICATE-----
MIIBszCCAVmgAwIBAgIUYMQ2j1JY5PqKdnv1RYLhdUBp3tAwCgYIKoZIzj0EAwIw
FjEUMBIGA1UEAwwLdGVzdC1iZWFjb24wHhcNMjYwOTAzMDAwMDAwWhcNMjYwOTA0
MDAwMDAwWjAWMRQwEgYDVQQDDAt0ZXN0LWJlYWNvbjBZMBMGByqGSM49AgEGCCqG
SM49AwEHA0IABHOVZ+HbEtsh9mPftRy8zWS+qUDb9rWHJcNq0fNH4GZC/Zh1DNNa
dUZDxwevoV1pXUnhf+C/2O7C+YvW+WfBIbijYzBhMB0GA1UdDgQWBBRdzR2kCy7f
qqGmhruY65cP3svWUDAfBgNVHSMEGDAWgBRdzR2kCy7fqqGmhruY65cP3svWUDAP
BgNVHRMBAf8EBTADAQH/MA4GA1UdDwEB/wQEAwIBBjAKBggqhkjOPQQDAgNIADBF
AiEAhRZ3lEOdA8N6vHYMyRgTT+mKEwySPuZxg6zqRR4HODUCIFsjrttWUPzy1X0V
Pj0c4T6qQ0vRwIAiqPOQLtjxoAtf
-----END CERTIFICATE-----
"""


def _document(certificate_id: str) -> dict:
    return {
        "pulse": {
            "version": "2.0",
            "period": 60_000,
            "statusCode": 0,
            "timeStamp": "2026-09-03T12:45:00.000Z",
            "outputValue": "AB" * 64,
            "certificateId": certificate_id,
            "signatureValue": "12" * 64,
            "chainIndex": 2,
            "pulseIndex": 123,
            "uri": "https://beacon.nist.gov/beacon/2.0/chain/2/pulse/123",
        }
    }


def test_exact_pulse_and_certificate_binding() -> None:
    der = ssl.PEM_cert_to_DER_cert(CERTIFICATE)
    identifier = hashlib.sha512(der).hexdigest()
    result = validate_nist_beacon_pulse(
        _document(identifier),
        expected_timestamp="2026-09-03T12:45:00Z",
        certificate_pem=CERTIFICATE,
    )
    assert result["certificate_identifier_verified"] is True
    assert result["pulse_signature_verified"] is False
    assert result["output_value"] == "AB" * 64


def test_wrong_time_or_certificate_fails_closed() -> None:
    identifier = hashlib.sha512(ssl.PEM_cert_to_DER_cert(CERTIFICATE)).hexdigest()
    with pytest.raises(ValueError, match="timestamp"):
        validate_nist_beacon_pulse(
            _document(identifier),
            expected_timestamp="2026-09-03T12:46:00Z",
            certificate_pem=CERTIFICATE,
        )
    with pytest.raises(ValueError, match="certificate SHA-512"):
        validate_nist_beacon_pulse(
            _document("0" * 128),
            expected_timestamp="2026-09-03T12:45:00Z",
            certificate_pem=CERTIFICATE,
        )


def test_ranking_is_deterministic_domain_separated_and_validated() -> None:
    salt = derive_selection_salt(
        domain="polyitan-v2",
        preregistration_sha256="1" * 64,
        output_value="AB" * 64,
    )
    assert salt == derive_selection_salt(
        domain="polyitan-v2",
        preregistration_sha256="1" * 64,
        output_value="AB" * 64,
    )
    assert rank_integer(salt, 1) == rank_integer(salt, 1)
    assert rank_integer(salt, 1) != rank_integer(salt, 2)
    with pytest.raises(ValueError, match="positive integer"):
        rank_integer(salt, 0)
