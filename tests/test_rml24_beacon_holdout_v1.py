from __future__ import annotations

import importlib.util
import copy
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "work/rml24/freeze_beacon_holdout_v1.py"
SPEC = importlib.util.spec_from_file_location("freeze_beacon_holdout_v1", MODULE_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
BEACON_PATH = ROOT / "work/confirmatory/nist_beacon_v2.py"
BEACON_SPEC = importlib.util.spec_from_file_location("nist_beacon_v2_test", BEACON_PATH)
assert BEACON_SPEC and BEACON_SPEC.loader
BEACON = importlib.util.module_from_spec(BEACON_SPEC)
BEACON_SPEC.loader.exec_module(BEACON)


def test_exclusion_policy_is_stable() -> None:
    plan = MODULE.load_object(ROOT / "reports/rml24-beacon-holdout-v1-prereg.json")
    indices = MODULE.eligible_indices(plan)
    assert len(indices) == 665
    assert len(set(indices)) == len(indices)
    assert not set(range(0, 64)).intersection(indices)
    assert not set(range(768, 832)).intersection(indices)
    assert not {887, 911, 977}.intersection(indices)


def test_group_universe_uses_metadata_only() -> None:
    plan = MODULE.load_object(ROOT / "reports/rml24-beacon-holdout-v1-prereg.json")
    manifest = MODULE.load_object(ROOT / "work/nature-dataset/shards/manifest.json")
    groups = MODULE.eligible_groups(plan, manifest)
    assert len(groups) == 252
    assert {row["modulation"] for row in groups} == {"BPSK", "GMSK", "OQPSK", "QPSK"}
    assert {row["symbol_rate_hz"] for row in groups} == {100000, 250000, 500000}
    assert len({row["snr_db"] for row in groups}) == 21


def test_signature_message_has_all_fields() -> None:
    zero = "00" * 64
    pulse = {
        "uri": "u", "version": "Version 2.0", "cipherSuite": 0, "period": 60000,
        "certificateId": zero, "chainIndex": 2, "pulseIndex": 3,
        "timeStamp": "2026-09-03T12:30:00.000Z", "localRandomValue": zero,
        "external": {"sourceId": zero, "statusCode": 0, "value": zero},
        "listValues": [{"type": name, "value": zero} for name in ("previous", "hour", "day", "month", "year")],
        "precommitmentValue": zero, "statusCode": 0,
    }
    message = BEACON.signature_message(pulse)
    assert message.startswith((1).to_bytes(4, "big") + b"u")
    assert len(message) > 5 + 7 * 68


def test_archived_live_signature_and_synthetic_tampering() -> None:
    fixture = json.loads(
        (ROOT / "reports/nist-beacon-v2-live-verification-v1-pulse.json").read_text()
    )
    certificate = (
        ROOT / "reports/nist-beacon-v2-live-verification-v1-certificate.pem"
    ).read_bytes()
    pulse = fixture["pulse"]
    verified = BEACON.verify_pulse(
        pulse,
        certificate,
        exact_timestamp_utc=pulse["timeStamp"],
        require_system_trust=False,
    )
    assert verified["rsa_pkcs1v15_sha512_signature"] is True
    assert verified["output_value_sha512_f1_through_f20"] is True

    changed_field = copy.deepcopy(pulse)
    changed_field["localRandomValue"] = "00" * 64
    with pytest.raises(ValueError, match="signature"):
        BEACON.verify_pulse(
            changed_field,
            certificate,
            exact_timestamp_utc=pulse["timeStamp"],
            require_system_trust=False,
        )

    changed_signature = copy.deepcopy(pulse)
    changed_signature["signatureValue"] = "00" * 512
    with pytest.raises(ValueError, match="signature"):
        BEACON.verify_pulse(
            changed_signature,
            certificate,
            exact_timestamp_utc=pulse["timeStamp"],
            require_system_trust=False,
        )

    changed_output = copy.deepcopy(pulse)
    changed_output["outputValue"] = "00" * 64
    with pytest.raises(ValueError, match="outputValue"):
        BEACON.verify_pulse(
            changed_output,
            certificate,
            exact_timestamp_utc=pulse["timeStamp"],
            require_system_trust=False,
        )
