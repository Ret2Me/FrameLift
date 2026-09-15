"""Focused retry5 regressions; only temporary runtime trees, never lifecycle/IQ."""
from __future__ import annotations

import ast
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import sys
from typing import Mapping

import pytest


ROOT = Path("/home/ubuntu/telemetry-yield")
WORK = ROOT / "work/blind-phase-confirmatory-v2"
BUILDER_PATH = WORK / "build_runtime_guard_v3_retry5.py"
SPEC = importlib.util.spec_from_file_location("retry5_builder_tests", BUILDER_PATH)
B = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = B
SPEC.loader.exec_module(B)


def refresh(document):
    result = copy.deepcopy(document)
    result.pop("resumption_payload_sha256", None)
    result["resumption_payload_sha256"] = B._sha256_document(result)
    return result


@pytest.fixture
def resumption(tmp_path, monkeypatch):
    if os.geteuid() != 0:
        pytest.skip("sealed ownership regression needs root")
    root = tmp_path / "runtime"
    root.mkdir(mode=0o775)
    (root / "lib").mkdir(mode=0o775)
    (root / "lib/payload").write_bytes(b"same scientific runtime bytes")
    (root / "lib/payload").chmod(0o664)
    (root / "lib/program").write_bytes(b"executable bytes")
    (root / "lib/program").chmod(0o775)
    (root / "alias").symlink_to("lib/payload")
    for entry in [root, root / "lib", root / "lib/payload", root / "lib/program", root / "alias"]:
        os.chown(entry, 1000, 1000, follow_symlinks=False)
    original = B._component_closure_live(root)
    B._seal_installed_root(root)
    current = B._component_closure_live(root)
    monkeypatch.setattr(B, "EXPECTED_COMPONENT_ROOT", root)
    monkeypatch.setattr(B, "EXPECTED_COMPONENT_RAW_MANIFEST_SHA256", original["raw_manifest_sha256"])
    monkeypatch.setattr(B, "EXPECTED_COMPONENT_CONTENT_MANIFEST_SHA256", original["content_manifest_sha256"])
    original_identity = {"path": str(tmp_path / "historical.json"), "size_bytes": 1, "sha256": "a" * 64}
    document = refresh({"schema_version": B.COMPONENT_RESUMPTION_SCHEMA,
        "original_closure": original_identity,
        "original_raw_sha256": original["raw_manifest_sha256"],
        "content_manifest_sha256": original["content_manifest_sha256"],
        "historical_raw_exact": False, "current_preseal_exact": True,
        "current_preseal_closure": current,
        "transition_proof": {"semantic_content_unchanged": True,
            "original_raw_manifest_exact": False, "current_preseal_raw_manifest_exact": True,
            "root_owned_readonly_unique_files": True}})
    return document, original, original_identity, root


def test_exact_hardened_state_accepted_original_raw_still_rejected(resumption):
    document, original, identity, root = resumption
    current = B._validate_component_resumption_document(document, original, identity)
    assert current["content_manifest_sha256"] == original["content_manifest_sha256"]
    assert current["raw_manifest_sha256"] != original["raw_manifest_sha256"]
    B._validate_component_closure_raw(current)
    with pytest.raises(ValueError, match="raw closure differs"):
        B._validate_component_closure_raw(original)
    assert root.stat().st_uid == 0 and stat.S_IMODE(root.stat().st_mode) == 0o555


@pytest.mark.parametrize("change", ["byte", "execute", "owner", "writable", "symlink", "extra_file", "hardlink"])
def test_real_live_drift_rejected(resumption, change):
    document, original, identity, root = resumption
    current = B._validate_component_resumption_document(document, original, identity)
    target = root / "lib/payload"
    if change == "byte":
        target.write_bytes(b"different byte")
    elif change == "execute":
        target.chmod(0o555)
    elif change == "owner":
        os.chown(target, 1000, 1000)
    elif change == "writable":
        target.chmod(0o644)
    elif change == "symlink":
        (root / "alias").unlink()
        (root / "alias").symlink_to("lib/program")
    elif change == "extra_file":
        (root / "extra").write_bytes(b"extra")
    else:
        os.link(target, root.parent / "external-alias")
    with pytest.raises(ValueError, match="raw closure differs"):
        B._validate_component_closure_raw(current)


@pytest.mark.parametrize("change", ["semantic", "raw_hash", "proof", "selfhash", "writable", "external_link"])
def test_forged_supplement_rejected(resumption, change):
    document, original, identity, _ = resumption
    changed = copy.deepcopy(document)
    current = changed["current_preseal_closure"]
    if change == "semantic":
        next(item for item in current["entries"] if item["kind"] == "file")["sha256"] = "b" * 64
    elif change == "raw_hash":
        current["raw_manifest_sha256"] = "b" * 64
    elif change == "proof":
        changed["transition_proof"]["root_owned_readonly_unique_files"] = False
    elif change == "selfhash":
        changed["historical_raw_exact"] = True
    elif change == "writable":
        next(item for item in current["entries"] if item["kind"] == "file")["mode"] |= 0o200
    else:
        current["hardlink_topology"]["groups"][0]["st_nlink"] = 2
    if change != "selfhash":
        changed = refresh(changed)
    with pytest.raises(ValueError):
        B._validate_component_resumption_document(changed, original, identity)


def guard_contract_function(path, content_hash):
    tree = ast.parse(path.read_bytes())
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                    and node.name == "_component_resumption_guard_contract")
    namespace = {"Mapping": Mapping, "re": re,
        "COMPONENT_RESUMPTION_PATH": B.COMPONENT_RESUMPTION_PATH,
        "EXPECTED_COMPONENT_CONTENT_MANIFEST_SHA256": content_hash,
        "EXPECTED_COMPONENT_CLOSURE_CONTENT_SHA256": content_hash}
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(path), "exec"), namespace)
    return namespace[function.name]


def test_all_three_consumers_require_identical_honest_guard_contract():
    identity = {"path": str(B.COMPONENT_RESUMPTION_PATH), "size_bytes": 12345, "sha256": "a" * 64}
    amendment = {"component_preseal_hardening_resumption": identity}
    expected = B._component_resumption_guard_contract(amendment)
    for name in ("campaign_launcher_amended_v3_retry5.py", "evaluate_campaign_amended_v3_retry5.py"):
        function = guard_contract_function(WORK / name, B.EXPECTED_COMPONENT_CONTENT_MANIFEST_SHA256)
        assert function(amendment) == expected
        with pytest.raises(ValueError):
            function({"component_preseal_hardening_resumption": {**identity, "path": "/tmp/arbitrary.json"}})
    assert expected["preseal_original_raw_manifest_exact"] is False
    assert expected["preseal_current_raw_manifest_exact"] is True
    assert "preseal_raw_manifest_exact" not in expected


def test_builder_rejects_arbitrary_supplement_path_and_mismatching_size(monkeypatch):
    identity = {"path": str(B.COMPONENT_RESUMPTION_PATH), "size_bytes": 100, "sha256": "a" * 64}
    with pytest.raises(ValueError):
        B._resumption_identity({"component_preseal_hardening_resumption": {**identity, "path": "/tmp/foreign"}})
    monkeypatch.setattr(B, "_static_expected_json", lambda *a: ({}, {**identity, "size_bytes": 101}))
    with pytest.raises(ValueError, match="exact identity differs"):
        B._attach_component_resumption({"amendment": {"component_preseal_hardening_resumption": identity}})


def test_complete_six_entry_dependency_map_is_verified(monkeypatch):
    def file_identity(path):
        return {"path": str(path), "size_bytes": 1, "sha256": hashlib.sha256(str(path).encode()).hexdigest()}
    paths = {"campaign_evaluator_v3": B.EVALUATOR_PATH, "campaign_launcher_v3": B.LAUNCHER_PATH,
        "evaluator_lock_freezer_v2": B.EVALUATOR_LOCK_FREEZER_PATH,
        "frozen_campaign_launcher": B.FROZEN_LAUNCHER_PATH, "normalizer_v2": B.NORMALIZER_PATH,
        "runtime_guard_builder_v3": BUILDER_PATH}
    expected = {name: file_identity(path) for name, path in paths.items()}
    amendment = {"amended_implementation": {"transitive_code_dependencies": expected},
        "transitive_code_dependency_contract": {"dependencies": expected}}
    monkeypatch.setattr(B, "_regular_file_identity", file_identity)
    assert B._validated_transitive_dependencies(amendment) == expected
    broken = copy.deepcopy(amendment)
    broken["amended_implementation"]["transitive_code_dependencies"].pop("campaign_evaluator_v3")
    with pytest.raises(ValueError, match="dependency map differs"):
        B._validated_transitive_dependencies(broken)


def test_guard_producer_and_launcher_include_identical_byte_authority_mapping():
    # Execute the actual producer/consumer list expressions on a mirror record,
    # so dropping byte_authority on either side fails without running lifecycle.
    outputs = []
    sample = {"source": {"path": "source"}, "byte_authority": {"path": "runtime"}, "sealed": {"path": "sealed"}}
    for path in (BUILDER_PATH, WORK / "campaign_launcher_amended_v3_retry5.py"):
        tree = ast.parse(path.read_bytes())
        candidates = [node for node in ast.walk(tree) if isinstance(node, ast.ListComp)
            and isinstance(node.elt, ast.Dict)
            and {key.value for key in node.elt.keys if isinstance(key, ast.Constant)}
                == {"source", "byte_authority", "sealed"}]
        assert candidates, path
        expression = copy.deepcopy(candidates[-1])
        expression.generators[0].iter = ast.Name(id="records", ctx=ast.Load())
        ast.fix_missing_locations(expression)
        outputs.append(eval(compile(ast.Expression(expression), str(path), "eval"), {"records": [sample]}))
    assert outputs == [[sample], [sample]]


def test_start_and_activation_raw_preflight_precedes_keeper_and_results_mutation():
    tree = ast.parse(BUILDER_PATH.read_bytes())
    main = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "main")
    before = next(node for node in ast.walk(main) if isinstance(node, ast.If)
        and ast.unparse(node.test) == "args.command in {'start-persistent-namespace', 'activate-readonly-project'}")
    assert "_validate_component_closure_raw(context['component_preseal_closure'])" in ast.unparse(before)
    calls = [node for node in ast.walk(main) if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
             and node.func.id in {"start_persistent_namespace", "_validate_external_output_parent"}]
    assert calls and all(before.end_lineno < node.lineno for node in calls)


def test_scientific_constants_and_old_sources_preserved():
    historical = {"build_runtime_guard_v3.py": "6d5ad06002ee940291e754af2bb357dea11e2782ca5073b656db7cb6e4f82054",
        "campaign_launcher_amended_v3.py": "c925f35ff93283be1a2a8e1c87c480d85aa0bf4176c11081a20af72dd3436d4f",
        "evaluate_campaign_amended_v3.py": "a857b1d85eaf035b028d3bbb00ce5cbad0e25e9b1702b9a24bdd99e5ce1843a5",
        "freeze_amended_evaluator_lock_v2.py": "31e65d888e6f7f773484a23455072e159e32fac9659abd24dea00b401b224ad0",
        "build_amended_evaluator_review_v3.py": "957283eb0359e0df3ffd8819dd4a34e7a599eb83223c4d93dc311d41a2a790b1"}
    for name, expected in historical.items():
        assert hashlib.sha256((WORK / name).read_bytes()).hexdigest() == expected
    assert B.EXPECTED_EXECUTION_PLAN_SHA256 == "061e1c68acb34a04d766a3bd21d6d80bdee9fd14f484e378101264d81e23391d"
    assert B.EXPECTED_ACQUISITION_MANIFEST_SHA256 == "f0da03513b23ff2aa9e5cd091ff0a32f4cb1a5581b6f637db7542f8edaea8dcf"
    assert B.EXPECTED_SOURCE_MANIFEST_SHA256 == "742ba722dd065940666c376b8c52a626a57e943f69321b4da1825214369ced6b"


def test_new_source_paths_propagated_without_changing_control_basename():
    assert B.LAUNCHER_PATH.name == "campaign_launcher_amended_v3_retry5.py"
    assert B.EVALUATOR_PATH.name == "evaluate_campaign_amended_v3_retry5.py"
    assert B.EVALUATOR_LOCK_FREEZER_PATH.name == "freeze_amended_evaluator_lock_v2_retry5.py"
    source = BUILDER_PATH.read_text()
    assert 'expected_path = control_parent / "build_runtime_guard_v3.py"' in source
    assert 'HERE / "build_runtime_guard_v3.py"' not in source


def byte_authority_function(path):
    tree = ast.parse(path.read_bytes())
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                    and node.name == "_validate_project_byte_authorities")
    namespace = {"Mapping": Mapping, "hashlib": hashlib, "json": json,
        "_valid_file_identity": B._valid_file_identity_record}
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(path), "exec"), namespace)
    return namespace[function.name]


def mirror_artifacts():
    contract = B._planned_artifact_mirror_contract()
    artifacts = []
    for spec in contract["entries"]:
        source = spec["logical_source"]
        artifacts.append({"source": source,
            "byte_authority": {"kind": "candidate_runtime_content_addressed_mirror", **spec},
            "sealed": {**source, "path": "/sealed/" + Path(source["path"]).name},
            "mounted_inode": {}})
    return {"planned_artifact_mirror_contract": contract, "artifacts": artifacts}, {
        "planned_artifact_mirror_contract": contract}


@pytest.mark.parametrize("name", ["freeze_amended_evaluator_lock_v2_retry5.py", "evaluate_campaign_amended_v3_retry5.py"])
@pytest.mark.parametrize("change", [None, "authority", "sealed_bytes", "contract", "missing_mirror"])
def test_freezer_evaluator_validate_exact_existing_mirror_authorities(name, change):
    validate = byte_authority_function(WORK / name)
    snapshot, guard = copy.deepcopy(mirror_artifacts())
    if change == "authority":
        snapshot["artifacts"][0]["byte_authority"]["byte_authority"]["path"] = "/arbitrary/runtime"
    elif change == "sealed_bytes":
        snapshot["artifacts"][0]["sealed"]["sha256"] = "b" * 64
    elif change == "contract":
        snapshot["planned_artifact_mirror_contract"]["entry_count"] = 3
    elif change == "missing_mirror":
        snapshot["artifacts"].pop()
    if change:
        with pytest.raises(ValueError):
            validate(snapshot, guard)
    else:
        validate(snapshot, guard)


def test_freezer_actual_sealed_evaluator_mapping_accepts_complete_mirror_snapshot(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("retry5_freezer_tests", WORK / "freeze_amended_evaluator_lock_v2_retry5.py")
    freezer = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = freezer
    spec.loader.exec_module(freezer)
    root = tmp_path / "sealed-project"
    evaluator = freezer.EVALUATOR_PATH
    encoded = evaluator.read_bytes()
    physical = root / evaluator.relative_to(ROOT)
    physical.parent.mkdir(parents=True)
    physical.write_bytes(encoded)
    physical.chmod(0o444)
    source = {"path": str(evaluator), "size_bytes": len(encoded), "sha256": hashlib.sha256(encoded).hexdigest()}
    inode = physical.stat()
    monkeypatch.setattr(freezer, "SEALED_PROJECT_ARTIFACTS_ROOT", root)
    monkeypatch.setattr(freezer, "EXPECTED_EVALUATOR_SHA256", source["sha256"])
    snapshot, guard = mirror_artifacts()
    snapshot["artifacts"].append({"source": source,
        "byte_authority": {"kind": "logical_source_exact", "logical_source": source, "byte_authority": source},
        "sealed": {**source, "path": str(physical)}, "mounted_inode": {
            "st_dev": inode.st_dev, "st_ino": inode.st_ino,
            "uid": 0, "gid": 0, "mode": 0o444, "nlink": 1}})
    snapshot.update({"schema_version": freezer.SEALED_PROJECT_ARTIFACTS_SCHEMA_VERSION,
        "status": "PASS", "artifact_count": len(snapshot["artifacts"]),
        "mount_point": str(root), "mount_record": {}})
    snapshot["snapshot_payload_sha256"] = freezer._sha256_document(snapshot)
    guard["sealed_project_artifacts"] = snapshot
    guard["sealed_project_artifact_mapping"] = [{key: record[key] for key in ("source", "byte_authority", "sealed")}
        for record in snapshot["artifacts"]]
    assert freezer._sealed_evaluator_physical_path(guard) == physical
    guard["sealed_project_artifact_mapping"][-1].pop("byte_authority")
    with pytest.raises(ValueError, match="mapping record"):
        freezer._sealed_evaluator_physical_path(guard)


def test_actual_retry5_amendment_metadata_expansion_without_lifecycle_or_iq(tmp_path):
    if os.geteuid() != 0:
        pytest.skip("exact root-owned published metadata")
    path = WORK / "build_operational_amendment_v3_retry5.py"
    spec = importlib.util.spec_from_file_location("retry5_amendment_tests", path)
    generator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(generator)
    abort = Path("/var/lib/telemetry-yield-confirmatory-v3-aborted-retry4-d19ca6e01fc84f75be86236d8d021c011227a1b7599129a813ab77d72ef9c6d6/retry4-preactivation-abort-receipt-v1.json")
    amendment, selectors = generator.build_document(abort,
        "56656e527ff7a639008063fbc4f4b5d05400d4407dd1fb58c881c460c9546030")
    assert len(selectors) == 17
    encoded = generator.payload(amendment)
    temporary = tmp_path / "candidate-amendment.json"
    temporary.write_bytes(encoded)
    kwargs = {"amendment": temporary, "expected_amendment_sha256": hashlib.sha256(encoded).hexdigest()}
    for name, identity_key in (("execution_plan", "execution_plan"),
                              ("acquisition_manifest", "acquisition_manifest"),
                              ("source_manifest", "source_manifest")):
        identity = amendment[identity_key]
        kwargs[name] = Path(identity["path"])
        kwargs["expected_" + name + "_sha256"] = identity["sha256"]
    context = B._static_metadata_context(**kwargs)
    expanded = B._expanded_activation_context(B._contract_metadata(context), context["roots"])
    identities = B._sealed_project_artifact_identities(expanded)
    assert identities and any(record["path"] == str(B.COMPONENT_RESUMPTION_PATH) for record in identities)
    assert context["component_preseal_closure"]["content_manifest_sha256"] == B.EXPECTED_COMPONENT_CONTENT_MANIFEST_SHA256
    assert B.LAUNCHER is None
