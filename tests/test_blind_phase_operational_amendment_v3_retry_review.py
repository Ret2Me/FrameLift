from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path("/home/ubuntu/telemetry-yield")
SOURCE = ROOT / "work/blind-phase-confirmatory-v2/build_operational_amendment_v3_retry_review.py"
SPEC = importlib.util.spec_from_file_location("retry_review_builder", SOURCE)
assert SPEC is not None and SPEC.loader is not None
BUILDER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BUILDER)


def test_exact_held_input_constants() -> None:
    assert hashlib.sha256(BUILDER.FREEZER.read_bytes()).hexdigest() == BUILDER.EXPECTED_FREEZER_SHA256
    assert hashlib.sha256(BUILDER.MANIFEST.read_bytes()).hexdigest() == BUILDER.EXPECTED_MANIFEST_SHA256
    template = ROOT / "work/blind-phase-confirmatory-v2/operational-amendment-review-template-v3-retry.json"
    assert hashlib.sha256(template.read_bytes()).hexdigest() == BUILDER.EXPECTED_TEMPLATE_SHA256


def test_build_review_runs_full_live_metadata_preflight() -> None:
    document, _args, freezer = BUILDER.build_review()
    assert document["schema_version"] == freezer["REVIEW_SCHEMA_VERSION"]
    assert document["status"] == "GO"
    assert document["checks"] == freezer["expected_review_checks"]()
    assert document["checks"]["p0_findings"] == 0
    assert document["checks"]["p1_findings"] == 0
    assert document["checks"]["p2_findings"] == 0
    freezer["_validate_review"](document, bindings=document["reviewed_bindings"])


def test_review_self_hash_is_canonical() -> None:
    document, _args, freezer = BUILDER.build_review()
    unhashed = {key: value for key, value in document.items() if key != "review_payload_sha256"}
    assert document["review_payload_sha256"] == freezer["sha256_document"](unhashed)


def test_review_binds_held_freezer_test_template_and_runtime_builder() -> None:
    document, _args, _freezer = BUILDER.build_review()
    bindings = document["reviewed_bindings"]
    assert bindings["retry_freezer"]["sha256"] == BUILDER.EXPECTED_FREEZER_SHA256
    assert bindings["retry_freezer_test"]["sha256"] == "28c0c2afd54b56bec027a7f8460de74979bc5edd737d1897bd677b228d16d9e5"
    assert bindings["retry_review_template"]["sha256"] == BUILDER.EXPECTED_TEMPLATE_SHA256
    assert bindings["runtime_guard_builder"]["sha256"] == "eb528941de423c59e675119af1f9f7468ad39977c3119176728ca8d6ae7408c2"
    assert bindings["runtime_guard_builder_test"]["sha256"] == "5b24fb3ee696e951a0a997a4ca0e078160d820879ec1bd2b07c99f37b6d55263"


def test_manifest_hash_drift_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    changed = tmp_path / "manifest.json"
    changed.write_bytes(BUILDER.MANIFEST.read_bytes() + b" ")
    monkeypatch.setattr(BUILDER, "MANIFEST", changed)
    with pytest.raises(ValueError, match="identity changed or differs"):
        BUILDER._load_manifest()


def test_noncanonical_output_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="output path is not canonical"):
        BUILDER.main(["--output", str(tmp_path / "review.json")])


def test_generator_has_no_iq_or_amendment_publication_interface() -> None:
    help_text = BUILDER.parser().format_help()
    assert "--iq" not in help_text
    assert "amendment" not in {action.dest for action in BUILDER.parser()._actions}
    source = SOURCE.read_text()
    assert "build_amendment" not in source
