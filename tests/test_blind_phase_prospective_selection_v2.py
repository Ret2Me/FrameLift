from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import ssl
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (
    ROOT
    / "work/blind-phase-confirmatory-v2/freeze_prospective_selection.py"
)
PREREGISTRATION = (
    ROOT
    / "reports/blind-phase-prospective-holdout-selection-v2-preregistration.json"
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


def _module():
    spec = importlib.util.spec_from_file_location("freeze_prospective_selection", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _pulse() -> dict:
    certificate_id = hashlib.sha512(
        ssl.PEM_cert_to_DER_cert(CERTIFICATE)
    ).hexdigest()
    return {
        "pulse": {
            "version": "2.0",
            "period": 60_000,
            "statusCode": 0,
            "timeStamp": "2026-09-03T12:45:00.000Z",
            "outputValue": "BC" * 64,
            "certificateId": certificate_id,
            "signatureValue": "34" * 512,
            "chainIndex": 2,
            "pulseIndex": 999,
            "uri": "https://beacon.nist.gov/beacon/2.0/chain/2/pulse/999",
        }
    }


def test_frozen_preregistration_contains_pool_commitment_but_no_selection() -> None:
    document = json.loads(PREREGISTRATION.read_text(encoding="utf-8"))
    assert document["eligible_pool"]["count"] == 38
    assert document["eligible_pool"]["rows_embedded_in_preregistration"] is False
    assert document["policy"]["manual_salt_used"] is False
    assert document["future_randomness"]["pulse_timestamp"] == (
        "2026-09-03T12:45:00.000Z"
    )
    assert "selected" not in document


def test_materialization_is_deterministic_and_outcome_free() -> None:
    module = _module()
    preregistration = json.loads(PREREGISTRATION.read_text(encoding="utf-8"))
    preregistration_sha256 = hashlib.sha256(PREREGISTRATION.read_bytes()).hexdigest()
    first = module.materialize_selection(
        preregistration=preregistration,
        preregistration_sha256=preregistration_sha256,
        pulse_document=_pulse(),
        certificate_pem=CERTIFICATE,
    )
    second = module.materialize_selection(
        preregistration=preregistration,
        preregistration_sha256=preregistration_sha256,
        pulse_document=_pulse(),
        certificate_pem=CERTIFICATE,
    )
    assert first == second
    assert first["selected_summary"]["observations"] == 30
    assert first["selected_summary"]["satellite_identities"] >= 3
    assert all("frames" not in row and "frame_count" not in row for row in first["selected"])
    assert first["beacon"]["pulse_signature_verified"] is False


def test_materialization_rejects_wrong_preregistration_hash() -> None:
    module = _module()
    preregistration = json.loads(PREREGISTRATION.read_text(encoding="utf-8"))
    with pytest.raises(ValueError, match="hash mismatch"):
        module.materialize_selection(
            preregistration=preregistration,
            preregistration_sha256="0" * 64,
            pulse_document=_pulse(),
            certificate_pem=CERTIFICATE,
        )
