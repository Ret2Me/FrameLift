from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat

import pytest


ROOT = Path(__file__).parents[1]
SOURCE = ROOT / "work/blind-phase-confirmatory-v2/build_operational_amendment_v3_retry2_review.py"
SPEC = importlib.util.spec_from_file_location("retry2_review_generator", SOURCE)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build():
    return MODULE.build_review(digest(SOURCE), digest(Path(__file__)))


def test_real_dry_run_derives_exact_review_contract():
    document, context = build()
    assert document["schema_version"] == "blind-phase-confirmatory-operational-amendment-v3-retry2-review-v1"
    assert document["status"] == "GO"
    assert len(document["reviewed_bindings"]) == 17
    assert len(document["checks"]) == 26
    assert document["checks"]["p0_findings"] == 0
    assert document["checks"]["p1_findings"] == 0
    assert document["checks"]["p2_findings"] == 0
    assert document["reviewed_bindings"]["root_control_seed"]["expected_entry_count"] == 4
    assert context["audit"]["would_publish_file_sha256"] == MODULE._canonical_file_sha256(document)
    unhashed = {key: value for key, value in document.items() if key != "review_payload_sha256"}
    assert document["review_payload_sha256"] == context["freezer"]["sha256_document"](unhashed)


def test_exact_held_freezer_test_template_and_evidence_bindings():
    document, _ = build()
    bindings = document["reviewed_bindings"]
    assert bindings["retry2_freezer"]["sha256"] == MODULE.EXPECTED_FREEZER_SHA256
    assert bindings["retry2_freezer_test"]["sha256"] == MODULE.EXPECTED_FREEZER_TEST_SHA256
    assert bindings["retry2_review_template"]["sha256"] == MODULE.EXPECTED_TEMPLATE_SHA256
    assert bindings["third_preflight_evidence"]["sha256"] == "05e144388b0c588ea30ef0ec592349be84345cd0d294dc9e96a11539ac4530c3"
    assert bindings["third_preflight_evidence_contract"]["evidence_payload_sha256"] == "c25a4c27147e5edd6d1c6f8bde3508098339ba4880771acf4806ba748ad089db"


@pytest.mark.parametrize("name", ["EXPECTED_FREEZER_SHA256", "EXPECTED_FREEZER_TEST_SHA256", "EXPECTED_TEMPLATE_SHA256"])
def test_held_identity_drift_fails_closed(monkeypatch, name):
    monkeypatch.setattr(MODULE, name, "f" * 64)
    with pytest.raises((MODULE.ReviewError, ValueError)):
        MODULE.build_review(digest(SOURCE), digest(Path(__file__)))


def test_template_placeholder_or_shape_drift_fails_closed(monkeypatch):
    original = MODULE._read_regular
    def altered(path, expected, maximum):
        payload, identity = original(path, expected, maximum)
        if Path(path) == MODULE.TEMPLATE:
            document = json.loads(payload)
            document["checks"]["p0_findings"] = 0
            payload = json.dumps(document).encode()
        return payload, identity
    monkeypatch.setattr(MODULE, "_read_regular", altered)
    with pytest.raises(MODULE.ReviewError, match="template"):
        MODULE.build_review(digest(SOURCE), digest(Path(__file__)))


def test_binding_count_drift_fails_closed(monkeypatch):
    freezer = MODULE._load_freezer()
    original = freezer["_prepare"]
    # MappingProxy prevents mutation of the held namespace, so wrap the loader.
    fake = dict(freezer)
    def changed(args):
        prepared = original(args)
        prepared["review_bindings"] = dict(prepared["review_bindings"])
        prepared["review_bindings"]["unexpected"] = {}
        return prepared
    fake["_prepare"] = changed
    monkeypatch.setattr(MODULE, "_load_freezer", lambda: fake)
    with pytest.raises(MODULE.ReviewError, match="shape"):
        MODULE.build_review(digest(SOURCE), digest(Path(__file__)))


def test_default_main_is_strictly_no_write(monkeypatch, capsys, tmp_path):
    document = {"review_payload_sha256": "a" * 64}
    context = {"audit": {"binding_count": 17, "check_count": 26}, "freezer": {}}
    monkeypatch.setattr(MODULE, "build_review", lambda *args: (document, context))
    monkeypatch.setattr(MODULE, "publish_and_verify", lambda *args: pytest.fail("publication called"))
    assert MODULE.main(["--expected-generator-sha256", "a" * 64, "--expected-generator-tests-sha256", "b" * 64]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "DRY_RUN_PASS_NO_WRITE"
    assert result["published"] is False


def test_publish_requires_explicit_root(monkeypatch):
    monkeypatch.setattr(MODULE.os, "geteuid", lambda: 1000)
    monkeypatch.setattr(MODULE.os, "getegid", lambda: 1000)
    with pytest.raises(MODULE.ReviewError, match="effective root"):
        MODULE.publish_and_verify({}, {"freezer": {}})


@pytest.mark.skipif(os.geteuid() != 0, reason="root-only immutable publication integration")
def test_root_publication_is_0444_nlink1_and_no_clobber(tmp_path, monkeypatch):
    document, context = build()
    output = tmp_path / MODULE.OUTPUT.name
    monkeypatch.setattr(MODULE, "build_review", lambda *args: (document, context))
    monkeypatch.setattr(MODULE, "OUTPUT", output)
    monkeypatch.setattr(MODULE, "REPORTS", tmp_path)
    result = MODULE.publish_and_verify(document, context, output)
    sidecar = output.with_name(output.name + ".sha256")
    assert result["sha256"] == digest(output)
    assert stat.S_IMODE(output.stat().st_mode) == 0o444 and output.stat().st_nlink == 1
    assert stat.S_IMODE(sidecar.stat().st_mode) == 0o444 and sidecar.stat().st_nlink == 1
    # Exact idempotent retry is accepted; different bytes cannot clobber.
    assert MODULE.publish_and_verify(document, context, output)["sha256"] == result["sha256"]
    changed = dict(document)
    changed["status"] = "NO-GO"
    with pytest.raises((ValueError, MODULE.ReviewError)):
        MODULE.publish_and_verify(changed, context, output)


def test_cli_has_no_iq_or_lifecycle_path_and_publish_is_opt_in():
    parser = MODULE.parser()
    actions = {action.dest: action for action in parser._actions}
    assert not any("iq" in name.lower() or "lifecycle" in name.lower() for name in actions)
    assert actions["publish"].default is False
