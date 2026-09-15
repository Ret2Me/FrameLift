from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "work/blind-phase-confirmatory-v2/build_amended_evaluator_review_v3.py"
TEMPLATE = ROOT / "work/blind-phase-confirmatory-v2/amended-evaluator-review-template-v3.json"


def load_module():
    spec = importlib.util.spec_from_file_location("amended_evaluator_review_v3", SOURCE)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def identity(name: str) -> dict[str, object]:
    return {"path": f"/control/{name}", "size_bytes": len(name), "sha256": hashlib.sha256(name.encode()).hexdigest()}


def material() -> dict[str, object]:
    return {
        "evaluator_identity": identity("evaluator"),
        "evaluator_test_identity": identity("evaluator-test"),
        "freezer_identity": identity("freezer"),
        "freezer_test_identity": identity("freezer-test"),
        "amendment_identity": identity("amendment"),
        "runtime_guard_identity": identity("guard"),
        "amended_launcher_identity": identity("launcher"),
        "amended_normalizer_identity": identity("normalizer"),
        "no_decoder_evidence_identity": identity("no-decoder"),
        "executed_dependencies": {"amended_evaluator": identity("evaluator")},
        "component_closure_identity": identity("closure"),
        "component_closure_generator_identity": identity("closure-generator"),
        "control_parent": {"path": "/control", "st_dev": 1, "st_ino": 2, "uid": 0, "gid": 0, "mode": 0o755},
        "campaign_results_parent": {"path": "/control/results-v3", "st_dev": 3, "st_ino": 4, "uid": 1000, "gid": 1000, "mode": 0o700},
        "persistent_namespace_identity": identity("active-namespace"),
        "persistent_namespace_live_identity": {"mount_namespace_inode": 55},
    }


def test_review_document_fills_only_dynamic_bindings_and_clean_findings():
    module = load_module()
    template = json.loads(TEMPLATE.read_bytes())
    document = module._review_document(template, material())
    assert document["schema_version"] == module.SCHEMA
    assert document["status"] == "PASS"
    assert document["checks"]["p0_findings"] == 0
    assert document["checks"]["p1_findings"] == 0
    assert document["checks"]["p2_findings"] == 0
    assert document["reviewed_runtime_guard_v3"] == material()["runtime_guard_identity"]
    unhashed = {key: value for key, value in document.items() if key != module.SELF_HASH_FIELD}
    assert document[module.SELF_HASH_FIELD] == module._sha256_document(unhashed)


def test_review_document_rejects_freezer_interface_drift():
    module = load_module()
    template = json.loads(TEMPLATE.read_bytes())
    broken = material()
    broken["new_unreviewed_argument"] = identity("new")
    with pytest.raises(ValueError, match="interface differs"):
        module._review_document(template, broken)


def test_capture_then_full_build_lock_validation_uses_in_memory_review():
    module = load_module()
    expected_material = material()

    class Evaluator:
        pass

    evaluator = Evaluator()
    evaluator._artifact_json = lambda *_args, **_kwargs: (_ for _ in ()).throw(
        AssertionError("only the synthetic review may be loaded in this test")
    )
    freezer = SimpleNamespace(EVALUATOR=evaluator)

    def real_review(path, *, expected_sha256, **values):
        document, actual = evaluator._artifact_json(path, module.MAX_JSON_BYTES)
        assert actual["sha256"] == expected_sha256
        assert document["status"] == "PASS"
        assert values == expected_material
        return document, actual

    freezer._review = real_review

    def build_lock(**kwargs):
        if freezer.EVALUATOR is None:
            freezer.EVALUATOR = evaluator
        return freezer._review(
            kwargs["review_path"],
            expected_sha256=kwargs["expected_review_sha256"],
            **expected_material,
        )

    freezer.build_lock = build_lock
    captured = module._capture_material(freezer, {})
    assert captured == expected_material
    document = module._review_document(json.loads(TEMPLATE.read_bytes()), captured)
    module._validate_in_memory(freezer, {}, document)


def test_generated_template_passes_exact_held_freezer_review_validator():
    module = load_module()
    freezer = module._load_freezer(
        module.LOCK_FREEZER_PATH, module.EXPECTED_LOCK_FREEZER_SHA256
    )
    document = module._review_document(json.loads(TEMPLATE.read_bytes()), material())
    data = module._payload(document)
    digest = hashlib.sha256(data).hexdigest()

    class Evaluator:
        EXPECTED_UNIT_COUNT = 6168
        EXPECTED_OBSERVATION_COUNT = 30
        SENSITIVITY_EXCLUSION = (4491,)

        @staticmethod
        def _artifact_json(_path, _maximum):
            return document, {"path": "/review.json", "size_bytes": len(data), "sha256": digest}

        @staticmethod
        def _verified_self_hash(value, *, schema, field):
            assert value["schema_version"] == schema
            assert value[field] == module._sha256_document(
                {key: item for key, item in value.items() if key != field}
            )
            return value

    freezer.EVALUATOR = Evaluator
    reviewed, _identity = freezer._review(
        Path("/review.json"), expected_sha256=digest, **material()
    )
    assert reviewed == document


def control_identity(path: Path) -> dict[str, object]:
    status = path.stat()
    return {
        "path": str(path), "st_dev": status.st_dev, "st_ino": status.st_ino,
        "uid": status.st_uid, "gid": status.st_gid, "mode": stat.S_IMODE(status.st_mode),
    }


@pytest.mark.skipif(os.geteuid() != 0, reason="root ownership is part of publication contract")
def test_publish_is_root_immutable_idempotent_and_sidecar_recoverable(tmp_path: Path):
    module = load_module()
    os.chmod(tmp_path, 0o755)
    output = tmp_path / module.OUTPUT_NAME
    document = module._review_document(json.loads(TEMPLATE.read_bytes()), material())
    first = module.publish_review(output, document, control_identity(tmp_path))
    second = module.publish_review(output, document, control_identity(tmp_path))
    assert first == second
    for path in (output, output.with_name(output.name + ".sha256")):
        status = path.lstat()
        assert status.st_uid == status.st_gid == 0
        assert stat.S_IMODE(status.st_mode) == 0o444
        assert status.st_nlink == 1
    output.unlink()
    recovered = module.publish_review(output, document, control_identity(tmp_path))
    assert recovered == first


@pytest.mark.skipif(os.geteuid() != 0, reason="root ownership is part of publication contract")
def test_publish_rejects_no_clobber_difference(tmp_path: Path):
    module = load_module()
    os.chmod(tmp_path, 0o755)
    output = tmp_path / module.OUTPUT_NAME
    document = module._review_document(json.loads(TEMPLATE.read_bytes()), material())
    module.publish_review(output, document, control_identity(tmp_path))
    changed = dict(document)
    changed["status"] = "DIFFERENT"
    with pytest.raises(ValueError):
        module.publish_review(output, changed, control_identity(tmp_path))


def _dummy_cli(output: Path) -> list[str]:
    names = (
        "execution-plan", "acquisition-manifest", "source-manifest", "amendment",
        "runtime-guard", "no-decoder-evidence", "no-decoder-evidence-generator",
        "component-closure", "component-closure-generator", "amended-launcher",
        "amended-normalizer", "evaluator-test", "freezer-test",
    )
    argv: list[str] = []
    for name in names:
        argv += [f"--{name}", "/unused", f"--expected-{name}-sha256", "0" * 64]
    argv += ["--lock-freezer", "/unused", "--expected-lock-freezer-sha256", "0" * 64]
    argv += ["--template", "/unused", "--expected-template-sha256", "0" * 64]
    argv += ["--expected-generator-sha256", "0" * 64]
    argv += ["--output", str(output)]
    return argv


def test_default_main_is_dry_run_and_does_not_publish(monkeypatch, tmp_path: Path, capsys):
    module = load_module()
    document = module._review_document(json.loads(TEMPLATE.read_bytes()), material())
    output = tmp_path / module.OUTPUT_NAME
    document["reviewed_control_parent"] = {
        **document["reviewed_control_parent"], "path": str(tmp_path),
    }
    monkeypatch.setattr(module, "build_review", lambda _args: (document, object()))
    monkeypatch.setattr(module, "_validate_executing_generator", lambda *_args: identity("generator"))
    assert module.main(_dummy_cli(output)) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "PASS_PREFLIGHT"
    assert result["mode"] == "dry-run"
    assert result["iq_or_campaign_outcome_paths_accepted_or_opened"] is False
    assert not output.exists()


def test_cli_has_no_iq_or_outcome_argument():
    module = load_module()
    destinations = {action.dest for action in module.parser()._actions}
    assert not any("iq" in name or "outcome" in name or "unit_manifest" in name for name in destinations)


def test_direct_mutable_project_generator_is_rejected():
    module = load_module()
    digest = hashlib.sha256(SOURCE.read_bytes()).hexdigest()
    with pytest.raises(PermissionError, match="canonical root-control copy"):
        module._validate_executing_generator(Path("/var/lib/telemetry-yield-confirmatory-v3"), digest)


def test_freezer_and_template_are_compiled_before_namespace_entry(monkeypatch):
    module = load_module()
    args = SimpleNamespace(
        lock_freezer=module.LOCK_FREEZER_PATH,
        expected_lock_freezer_sha256="0" * 64,
        template=module.TEMPLATE_PATH,
        expected_template_sha256=module.EXPECTED_TEMPLATE_SHA256,
    )
    entered = False

    def forbidden(*_args):
        nonlocal entered
        entered = True

    monkeypatch.setattr(module, "_enter_guard_namespace", forbidden)
    with pytest.raises(ValueError, match="compiled review contract"):
        module.build_review(args)
    assert entered is False


def test_namespace_entry_rejects_guard_without_active_binding_before_setns(monkeypatch):
    module = load_module()
    monkeypatch.setattr(module, "_require_root_control_file", lambda *_args: None)
    monkeypatch.setattr(module, "_load_json", lambda *_args, **_kwargs: ({}, identity("guard")))
    called = False

    def forbidden(*_args):
        nonlocal called
        called = True

    monkeypatch.setattr(os, "setns", forbidden)
    with pytest.raises(ValueError, match="post-freeze closed guard"):
        module._enter_guard_namespace(Path("/control/guard.json"), "0" * 64)
    assert called is False


def test_source_contains_no_lifecycle_or_decoder_execution_surface():
    text = SOURCE.read_text(encoding="utf-8")
    assert "subprocess" not in text
    assert "mount(" not in text
    assert "umount" not in text
    assert "decoder" not in {action.dest for action in load_module().parser()._actions}
