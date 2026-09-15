from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (
    ROOT
    / "work/blind-phase-confirmatory-v2/build_no_decoder_since_v1_evidence.py"
)


def _module():
    name = "blind_phase_no_decoder_since_v1_evidence_test_module"
    spec = importlib.util.spec_from_file_location(name, SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


EVIDENCE = _module()


def test_live_evidence_is_exact_two_units_six_files_and_timestamped() -> None:
    document = EVIDENCE.build_evidence(generated_at="2026-09-04T17:00:00Z")
    EVIDENCE.validate_evidence(document)
    assert document["schema_version"] == EVIDENCE.SCHEMA_VERSION
    assert document["status"] == "PASS"
    assert document["period_start_utc"] == "2026-09-04T15:28:17Z"
    assert document["supersedes_evidence_v1"]["evidence"]["sha256"] == (
        EVIDENCE.EXPECTED_SUPERSEDED_EVIDENCE_V1_SHA256
    )
    assert document["supersedes_evidence_v1"]["v1_artifact_modified"] is False
    timestamp = document["superseded_amendment_timestamp"]
    assert timestamp["verification_status"] == "PASS"
    assert timestamp["message_imprint_sha256"] == EVIDENCE.EXPECTED_AMENDMENT_V1_SHA256
    inventory = document["campaign_output_inventory"]
    assert inventory["exposed_decoder_unit_count"] == 2
    assert inventory["new_decoder_unit_count"] == 0
    assert inventory["file_count"] == 6
    assert sum(path.endswith("/raw-result.json") for path in inventory["files"]) == 2
    assert sum(path.endswith("/normalized-result.json") for path in inventory["files"]) == 2
    assert sum(path.endswith("/process-receipt.json") for path in inventory["files"]) == 2


def test_timestamp_query_and_reply_are_exact_and_cryptographically_verified() -> None:
    timestamp = EVIDENCE._timestamp_contract()
    assert timestamp["query"]["sha256"] == EVIDENCE.EXPECTED_TIMESTAMP_QUERY_SHA256
    assert timestamp["reply"]["sha256"] == EVIDENCE.EXPECTED_TIMESTAMP_REPLY_SHA256
    assert timestamp["generation_time_utc"] == EVIDENCE.EXPECTED_TIMESTAMP_UTC


def test_rfc3161_parser_rejects_discontinuous_offsets() -> None:
    details = (
        "Message data:\n"
        "    0000 - 00 00 00 00 00 00 00 00-00 00 00 00 00 00 00 00\n"
        "    0020 - 00 00 00 00 00 00 00 00-00 00 00 00 00 00 00 00\n"
        "Serial number: 0x01\n"
    )
    with pytest.raises(ValueError, match="message imprint"):
        EVIDENCE._message_imprint_from_openssl_text(details)


def test_rfc3161_parser_ignores_hex_like_ascii_column() -> None:
    digest = EVIDENCE.EXPECTED_AMENDMENT_V1_SHA256
    octets = [digest[index : index + 2] for index in range(0, len(digest), 2)]
    details = (
        "Message data:\n"
        f"    0000 - {' '.join(octets[:8])}-{' '.join(octets[8:16])}   aa bb cc 00 11 ff\n"
        f"    0010 - {' '.join(octets[16:24])}-{' '.join(octets[24:])}   de ad be ef 12 34\n"
        "Serial number: 0x01\n"
    )
    assert EVIDENCE._message_imprint_from_openssl_text(details) == digest


def test_rfc3161_parser_rejects_extra_hexdump_row() -> None:
    row = "00 00 00 00 00 00 00 00-00 00 00 00 00 00 00 00"
    details = (
        "Message data:\n"
        f"    0000 - {row}\n"
        f"    0010 - {row}\n"
        f"    0020 - {row}\n"
        "Serial number: 0x01\n"
    )
    with pytest.raises(ValueError, match="two 16-byte rows"):
        EVIDENCE._message_imprint_from_openssl_text(details)


def test_inventory_fails_closed_on_extra_raw_result(tmp_path: Path) -> None:
    amendment, _ = EVIDENCE._validate_v1()
    original = amendment["pre_amendment_output_inventory"]
    copied_root = tmp_path / "campaign"
    copied_root.mkdir()
    for directory in ("ephemeral", "null-ledgers", "units"):
        (copied_root / directory).mkdir()
    copied_units: list[dict[str, object]] = []
    for unit in original["units"]:
        unit_id = unit["unit"]["unit_id"]
        unit_root = copied_root / "units" / unit_id
        (unit_root / "output").mkdir(parents=True)
        (unit_root / "scratch").mkdir()
        copied = json.loads(json.dumps(unit))
        for key, relative in {
            "raw_result": Path("output/raw-result.json"),
            "normalized_result": Path("normalized-result.json"),
            "process_receipt": Path("process-receipt.json"),
        }.items():
            source = Path(unit[key]["path"])
            target = unit_root / relative
            target.write_bytes(source.read_bytes())
            timestamp = EVIDENCE._utc(EVIDENCE.EXPECTED_TIMESTAMP_UTC, "timestamp").timestamp() - 1
            os.utime(target, (timestamp, timestamp))
            copied[key] = EVIDENCE.regular_file_identity(target)
        copied_units.append(copied)
    fake = json.loads(json.dumps(amendment))
    fake["pre_amendment_output_inventory"] = {
        **original,
        "root": str(copied_root),
        "units": copied_units,
    }
    extra = copied_root / "units" / "unexpected" / "output"
    extra.mkdir(parents=True)
    (extra / "raw-result.json").write_text("{}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="tree changed"):
        EVIDENCE._verify_output_inventory(
            fake,
            period_start=EVIDENCE._utc(EVIDENCE.EXPECTED_TIMESTAMP_UTC, "timestamp"),
        )


def test_inventory_fails_closed_if_existing_file_is_rewritten_after_timestamp(
    tmp_path: Path,
) -> None:
    amendment, _ = EVIDENCE._validate_v1()
    original = amendment["pre_amendment_output_inventory"]
    copied_root = tmp_path / "campaign"
    copied_root.mkdir()
    for directory in ("ephemeral", "null-ledgers", "units"):
        (copied_root / directory).mkdir()
    copied_units: list[dict[str, object]] = []
    for unit in original["units"]:
        unit_id = unit["unit"]["unit_id"]
        unit_root = copied_root / "units" / unit_id
        (unit_root / "output").mkdir(parents=True)
        (unit_root / "scratch").mkdir()
        copied = json.loads(json.dumps(unit))
        for key, relative in {
            "raw_result": Path("output/raw-result.json"),
            "normalized_result": Path("normalized-result.json"),
            "process_receipt": Path("process-receipt.json"),
        }.items():
            source = Path(unit[key]["path"])
            target = unit_root / relative
            target.write_bytes(source.read_bytes())
            copied[key] = EVIDENCE.regular_file_identity(target)
        copied_units.append(copied)
    fake = json.loads(json.dumps(amendment))
    fake["pre_amendment_output_inventory"] = {
        **original,
        "root": str(copied_root),
        "units": copied_units,
    }
    with pytest.raises(ValueError, match="modified at/after"):
        EVIDENCE._verify_output_inventory(
            fake,
            period_start=EVIDENCE._utc(EVIDENCE.EXPECTED_TIMESTAMP_UTC, "timestamp"),
        )


def test_validation_rejects_overclaim_or_inventory_mutation() -> None:
    document = EVIDENCE.build_evidence(generated_at="2026-09-04T17:00:00Z")
    document["checks"] = dict(document["checks"])
    document["checks"]["new_raw_result_count_since_timestamp"] = 1
    document["evidence_payload_sha256"] = EVIDENCE.sha256_document(
        {key: value for key, value in document.items() if key != "evidence_payload_sha256"}
    )
    with pytest.raises(ValueError, match="not exact PASS"):
        EVIDENCE.validate_evidence(document)


def test_generator_opens_no_iq_or_candidate_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_open = EVIDENCE.os.open
    opened: list[str] = []

    def recording_open(path: object, *args: object, **kwargs: object) -> int:
        opened.append(os.fspath(path))
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(EVIDENCE.os, "open", recording_open)
    EVIDENCE.build_evidence(generated_at="2026-09-04T17:00:00Z")
    assert not any(path.endswith(".iq") for path in opened)
    assert not any("/runtime-r" in path or "/sealed-runtime" in path for path in opened)


def test_publish_is_self_hashed_chmod_0444_and_no_clobber(tmp_path: Path) -> None:
    document = EVIDENCE.build_evidence(generated_at="2026-09-04T17:00:00Z")
    output = tmp_path / "evidence.json"
    digest = EVIDENCE.publish_no_clobber(output, document)
    sidecar = output.with_name(output.name + ".sha256")
    assert digest == hashlib.sha256(output.read_bytes()).hexdigest()
    assert sidecar.read_text(encoding="ascii") == f"{digest}  {output.name}\n"
    assert stat.S_IMODE(output.stat().st_mode) == 0o444
    assert stat.S_IMODE(sidecar.stat().st_mode) == 0o444
    with pytest.raises(ValueError, match="already exists"):
        EVIDENCE.publish_no_clobber(output, document)
