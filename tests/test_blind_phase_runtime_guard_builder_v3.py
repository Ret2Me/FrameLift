from __future__ import annotations

import importlib.util
import ast
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import types
from typing import Mapping

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "work/blind-phase-confirmatory-v2/build_runtime_guard_v3.py"

def _module():
    name = "blind_phase_runtime_guard_builder_v3_test_module"
    spec = importlib.util.spec_from_file_location(name, SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module

BUILDER = _module()


def _closure_generator_module():
    path = ROOT / "work/blind-phase-confirmatory-v2/build_component_closure_manifest.py"
    spec = importlib.util.spec_from_file_location(
        "blind_phase_component_closure_cross_test_module", path
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def _tree(tmp_path: Path, name: str = "runtime") -> Path:
    root = tmp_path / name
    (root / "lib").mkdir(parents=True)
    first = root / "lib/first"
    first.write_bytes(b"identical bytes")
    first.chmod(0o754)
    os.link(first, root / "lib/second")
    (root / "lib64").symlink_to("lib", target_is_directory=True)
    return root

def test_import_executes_no_project_dependency() -> None:
    assert BUILDER.LAUNCHER is None
    assert BUILDER.PROJECT_ROOT == Path("/home/ubuntu/telemetry-yield")

def test_materialized_three_copies_are_exact_nlink1(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    original = BUILDER._logical_tree_manifest(root)
    prepared = BUILDER._prepare_root("candidate", root)
    try:
        for copy in (prepared.stage, prepared.recovery, prepared.superseded):
            assert BUILDER._logical_tree_manifest(copy) == original
            assert os.readlink(copy / "lib64") == "lib"
            assert (copy / "lib/first").stat().st_nlink == 1
            assert (copy / "lib/second").stat().st_nlink == 1
            assert (copy / "lib/first").stat().st_ino != (copy / "lib/second").stat().st_ino
    finally:
        shutil.rmtree(prepared.transaction)


def test_complete_component_closure_projection_matches_frozen_generator(
    tmp_path: Path,
) -> None:
    root = _tree(tmp_path)
    generator = _closure_generator_module()
    generated = generator.scan_runtime(root)
    live = BUILDER._component_closure_live(root)
    for key in (
        "runtime_root",
        "entries",
        "entry_count",
        "regular_file_count",
        "directory_count",
        "symlink_count",
        "hardlink_topology",
        "raw_manifest_sha256",
        "content_manifest_sha256",
    ):
        assert live[key] == generated[key]
    assert live["content_manifest_sha256"] == generated["content_manifest_sha256"]
    prepared = BUILDER._prepare_root("component", root)
    try:
        assert BUILDER._component_closure_live(prepared.stage)[
            "content_manifest_sha256"
        ] == generated["content_manifest_sha256"]
    finally:
        shutil.rmtree(prepared.transaction)


def test_complete_component_closure_rejects_any_raw_file_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _tree(tmp_path)
    generator = _closure_generator_module()
    frozen = generator.scan_runtime(root)
    monkeypatch.setattr(BUILDER, "EXPECTED_COMPONENT_ROOT", root)
    BUILDER._validate_component_closure_raw(frozen)
    (root / "lib/first").write_bytes(b"changed bytes")
    with pytest.raises(ValueError, match="component raw closure differs"):
        BUILDER._validate_component_closure_raw(frozen)


def test_seal_rechecks_raw_component_closure_after_both_runtime_copies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    candidate = _tree(tmp_path, "candidate")
    component = _tree(tmp_path, "component")
    frozen = _closure_generator_module().scan_runtime(component)
    context = {"roots": {"candidate": candidate, "component": component},
        "component_closure": frozen,
        "component_closure_identity": {"path": "/closure", "size_bytes": 1,
            "sha256": "c" * 64},
        "component_closure_generator_identity": {"path": "/generator",
            "size_bytes": 1, "sha256": "d" * 64},
        **{f"{label}_identity": {"path": f"/{label}", "size_bytes": 1,
            "sha256": label[0] * 64}
            for label in ("plan", "acquisition", "source", "amendment")}}
    monkeypatch.setattr(BUILDER, "EXPECTED_COMPONENT_ROOT", component)
    original_prepare = BUILDER._prepare_root
    transactions: list[Path] = []

    def prepare(label: str, root: Path):
        result = original_prepare(label, root)
        transactions.append(result.transaction)
        if label == "component":
            status = (component / "lib/first").stat()
            os.utime(component / "lib/first", ns=(status.st_atime_ns, status.st_mtime_ns + 1))
        return result

    monkeypatch.setattr(BUILDER, "_prepare_root", prepare)
    with pytest.raises(ValueError, match="component raw closure differs"):
        BUILDER.seal_with_journal(context, tmp_path)
    assert not (tmp_path / "seal-transaction-intent-v3.json").exists()
    assert transactions and all(not transaction.exists() for transaction in transactions)

@pytest.mark.parametrize("kind", ["escaping", "broken", "cycle", "special"])
def test_preflight_rejects_unsafe_entries(tmp_path: Path, kind: str) -> None:
    root = tmp_path / "runtime"
    root.mkdir()
    if kind == "escaping":
        outside = tmp_path / "outside"; outside.write_bytes(b"x"); (root / "bad").symlink_to(outside); match = "escapes"
    elif kind == "broken":
        (root / "bad").symlink_to("absent"); match = "broken or cyclic"
    elif kind == "cycle":
        (root / "a").symlink_to("b"); (root / "b").symlink_to("a"); match = "broken or cyclic"
    else:
        os.mkfifo(root / "fifo"); match = "special"
    with pytest.raises(ValueError, match=match):
        BUILDER._prepare_root("candidate", root)

def test_copy_and_rename_are_no_clobber(tmp_path: Path) -> None:
    source = tmp_path / "source"; destination = tmp_path / "destination"
    source.write_bytes(b"new"); destination.write_bytes(b"old")
    with pytest.raises(FileExistsError): BUILDER._copy_regular_unique(str(source), str(destination))
    assert destination.read_bytes() == b"old"
    source.unlink(); source.mkdir(); destination.unlink(); destination.mkdir()
    with pytest.raises(FileExistsError): BUILDER._rename_noreplace(source, destination)

def _prepared_pair(tmp_path: Path):
    return [BUILDER._prepare_root(label, _tree(tmp_path, label)) for label in ("candidate", "component")]

def test_two_root_commit_retains_backups_and_originals(tmp_path: Path) -> None:
    items = _prepared_pair(tmp_path)
    assert BUILDER._install_both(items, lambda: "PASS") == "PASS"
    for item in items:
        assert item.root.exists() and item.backup.exists() and item.superseded.exists()
        assert item.quarantined_original.exists()
        assert item.backup.stat().st_ino != item.quarantined_original.stat().st_ino

def test_atomic_boundary_late_mutation_causes_no_data_loss(tmp_path: Path) -> None:
    items = _prepared_pair(tmp_path)
    changed = items[0].root / "late"; changed.write_bytes(b"late user write")
    with pytest.raises(ValueError, match="atomic swap boundary"):
        BUILDER._install_both(items, lambda: "unreachable")
    assert changed.read_bytes() == b"late user write"
    assert items[1].root.exists()

def test_resume_root_swap_accepts_partially_sealed_modes(tmp_path: Path) -> None:
    item = BUILDER._prepare_root("candidate", _tree(tmp_path))
    BUILDER._resume_root_swap(item)
    (item.root / "lib/first").chmod(0o554)
    (item.backup / "lib/first").chmod(0o554)
    BUILDER._resume_root_swap(item)
    assert BUILDER._content_tree_hash(item.root) == item.content_manifest_sha256

@pytest.mark.parametrize("step", ["moved_original", "installed_backup", "installed_root"])
def test_resume_root_swap_recovers_every_rename_boundary(tmp_path: Path, step: str) -> None:
    item = BUILDER._prepare_root("candidate", _tree(tmp_path))
    BUILDER._rename_noreplace(item.root, item.quarantined_original)
    if step in {"installed_backup", "installed_root"}:
        BUILDER._rename_noreplace(item.recovery, item.backup)
    if step == "installed_root":
        BUILDER._rename_noreplace(item.stage, item.root)
    BUILDER._resume_root_swap(item)
    assert BUILDER._content_tree_hash(item.root) == item.content_manifest_sha256
    assert BUILDER._content_tree_hash(item.backup) == item.content_manifest_sha256

def test_external_hardlink_mutation_cannot_change_private_backup(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    external = tmp_path / "external-link"
    os.link(root / "lib/first", external)
    item = BUILDER._prepare_root("candidate", root)
    BUILDER._resume_root_swap(item)
    external.write_bytes(b"mutated through external inode")
    assert (item.backup / "lib/first").read_bytes() == b"identical bytes"
    assert item.backup.stat().st_ino != item.quarantined_original.stat().st_ino

def test_post_swap_failure_rolls_both_roots_back(tmp_path: Path) -> None:
    items = _prepared_pair(tmp_path)
    with pytest.raises(RuntimeError, match="validation failure"):
        BUILDER._install_both(items, lambda: (_ for _ in ()).throw(RuntimeError("validation failure")))
    for item in items: assert (item.root / "lib/first").read_bytes() == b"identical bytes"

def test_seal_phase_never_calls_live_runtime_validator(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    roots = {"candidate": _tree(tmp_path, "candidate"), "component": _tree(tmp_path, "component")}
    monkeypatch.setattr(BUILDER.os, "geteuid", lambda: 0)
    monkeypatch.setattr(BUILDER, "_validate_runtimes", lambda plan: (_ for _ in ()).throw(AssertionError("executed")))
    monkeypatch.setattr(BUILDER, "_prepare_root", lambda label, root: types.SimpleNamespace(label=label, root=root,
        transaction=tmp_path / f"tx-{label}", stage=root, backup=root, recovery=root, superseded=root,
        quarantined_original=root, logical_manifest_sha256=BUILDER._sha256_document(BUILDER._logical_tree_manifest(root)),
        content_manifest_sha256=BUILDER._content_tree_hash(root)))
    monkeypatch.setattr(BUILDER, "_lock_runtime_parents", lambda roots: {})
    monkeypatch.setattr(BUILDER, "_install_both", lambda prepared, callback: {"x": "y"})
    monkeypatch.setattr(BUILDER, "_seal_installed_root", lambda path: None)
    monkeypatch.setattr(BUILDER, "_seal_directory", lambda path: None)
    monkeypatch.setattr(BUILDER, "_recursive_protected_root_identity", lambda path: {"path": str(path)})
    result = BUILDER.materialize_and_seal({"plan": {}, "roots": roots})
    assert result["protected_roots"] == {"x": "y"}

def test_wrong_compiled_hash_fails_before_file_or_mount(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(BUILDER, "_static_expected_json", lambda *a, **k: (_ for _ in ()).throw(AssertionError("read")))
    with pytest.raises(ValueError, match="compiled frozen campaign identity"):
        BUILDER._static_metadata_context(execution_plan=Path("x"), expected_execution_plan_sha256="0" * 64,
            acquisition_manifest=Path("x"), expected_acquisition_manifest_sha256=BUILDER.EXPECTED_ACQUISITION_MANIFEST_SHA256,
            source_manifest=Path("x"), expected_source_manifest_sha256=BUILDER.EXPECTED_SOURCE_MANIFEST_SHA256,
            amendment=Path("x"), expected_amendment_sha256="1" * 64)


def test_candidate_metadata_context_derives_and_binds_capacity_contract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    capacity = {"schema_version": "test-capacity", "minimum_memavailable_bytes": 123}
    normalizer = {"path": "/normalizer", "size_bytes": 1, "sha256": "n" * 64}
    launcher = {"path": "/launcher", "size_bytes": 1, "sha256": "l" * 64}
    builder = {"path": "/builder", "size_bytes": 1, "sha256": "b" * 64}
    closure = {"content_manifest_sha256": "c" * 64}
    closure_identity = {"path": "/closure", "size_bytes": 1, "sha256": "c" * 64}
    closure_generator = {"path": "/closure-generator", "size_bytes": 1,
        "sha256": "g" * 64}
    amendment = {
        "schema_version": BUILDER.AMENDMENT_V3_SCHEMA,
        "amended_implementation": {"launcher": launcher,
            "runtime_guard_builder": builder},
        "amended_normalizer": normalizer,
        "component_runtime_closure": closure_identity,
        "component_runtime_closure_generator": closure_generator,
        "capacity_preflight_contract": capacity,
    }
    amendment["amendment_payload_sha256"] = BUILDER._sha256_document(amendment)
    documents = {"plan": {}, "acquisition": {}, "source": {}, "amendment": amendment}
    paths = {label: tmp_path / label for label in documents}
    identities = {label: {"path": str(paths[label]), "size_bytes": 1,
        "sha256": label[0] * 64} for label in documents}
    by_path = {path: (documents[label], identities[label]) for label, path in paths.items()}

    fake_launcher = types.SimpleNamespace(
        AMENDMENT_SCHEMA="compat",
        AMENDED_NORMALIZER_PATH=Path("/normalizer"),
        sha256_document=BUILDER._sha256_document,
        _validate_release_constants=lambda **kwargs: None,
        _validate_source_manifest=lambda *args, **kwargs: None,
        validate_amendment=lambda *args, **kwargs: None,
        regular_file_identity=lambda path, **kwargs: {
            BUILDER.LAUNCHER_PATH: launcher,
            BUILDER.HERE / "build_runtime_guard_v3.py": builder,
            BUILDER.COMPONENT_CLOSURE_GENERATOR_PATH: closure_generator,
            Path("/normalizer"): normalizer,
        }[Path(path)],
    )
    monkeypatch.setattr(BUILDER, "_load_launcher_after_seal", lambda: None)
    monkeypatch.setattr(BUILDER, "LAUNCHER", fake_launcher)
    monkeypatch.setattr(BUILDER, "_expected_json", lambda path, digest: by_path[Path(path)])
    monkeypatch.setattr(BUILDER, "_verified_acquisition_metadata_only", lambda *a, **k: None)
    monkeypatch.setattr(BUILDER, "_load_component_closure",
        lambda: (closure, closure_identity))
    monkeypatch.setattr(BUILDER, "_regular_file_identity", lambda path, **kwargs: closure_generator)
    monkeypatch.setattr(BUILDER, "_runtime_roots", lambda plan: {"candidate": Path("/c"),
        "component": Path("/g")})
    monkeypatch.setattr(BUILDER, "_capacity_contract", lambda context: capacity)

    kwargs = {"execution_plan": paths["plan"], "expected_execution_plan_sha256": "p",
        "acquisition_manifest": paths["acquisition"],
        "expected_acquisition_manifest_sha256": "a",
        "source_manifest": paths["source"], "expected_source_manifest_sha256": "s",
        "amendment": paths["amendment"], "expected_amendment_sha256": "m"}
    assert BUILDER._metadata_context(**kwargs)["capacity_contract"] == capacity
    amendment["capacity_preflight_contract"] = {"schema_version": "wrong"}
    amendment["amendment_payload_sha256"] = BUILDER._sha256_document(
        {key: value for key, value in amendment.items()
         if key != "amendment_payload_sha256"}
    )
    with pytest.raises(ValueError, match="capacity preflight contract differs"):
        BUILDER._metadata_context(**kwargs)

def test_runtime_roots_are_compiled_exactly() -> None:
    plan = {"provenance": {"candidate_runtime_lock": {"runtime_manifest": {"environment": {
        "launcher_path": str(BUILDER.PROJECT_ROOT / "src/telemetry_yield/bin/python")}}},
        "component_baseline_lock": {"runtime": {"golden_root": str(BUILDER.EXPECTED_COMPONENT_ROOT)}}}}
    with pytest.raises(ValueError, match="frozen campaign roots"): BUILDER._static_runtime_roots(plan)

def test_control_parent_is_exact(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="frozen root-only"):
        BUILDER._validate_control_parent(tmp_path / "root-owned-looking", create=False)

def test_relocated_builder_copy_mismatch_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(BUILDER, "DEFAULT_CONTROL_PARENT", tmp_path)
    with pytest.raises(ValueError, match="exact relocated control copy"):
        BUILDER._validate_control_copy(tmp_path, "0" * 64)

@pytest.mark.parametrize("isolated,no_site,ignore_environment,executable,path", [
    (0,1,1,"/usr/bin/python3.12",BUILDER.STATIC_SYS_PATH), (1,0,1,"/usr/bin/python3.12",BUILDER.STATIC_SYS_PATH),
    (1,1,0,"/usr/bin/python3.12",BUILDER.STATIC_SYS_PATH), (1,1,1,"/tmp/python",BUILDER.STATIC_SYS_PATH),
    (1,1,1,"/usr/bin/python3.12",("/tmp",))])
def test_static_interpreter_contract_is_fail_closed(monkeypatch, isolated, no_site, ignore_environment, executable, path) -> None:
    fake = types.SimpleNamespace(flags=types.SimpleNamespace(isolated=isolated, no_site=no_site,
        ignore_environment=ignore_environment), executable=executable, path=list(path))
    monkeypatch.setattr(BUILDER, "sys", fake); monkeypatch.setattr(BUILDER.os, "geteuid", lambda: 0)
    with pytest.raises(PermissionError, match="-I -S"): BUILDER._require_static_root_interpreter()

def test_system_isolated_no_site_ignores_pythonpath_and_local_hashlib(tmp_path: Path) -> None:
    (tmp_path / "hashlib.py").write_text("raise SystemExit('FAKE')\n", encoding="utf-8")
    completed = subprocess.run(["/usr/bin/python3.12", "-I", "-S", "-c",
        "import hashlib,json,sys; print(json.dumps([hashlib.__file__,sys.path]))"], cwd=tmp_path,
        env={"PYTHONPATH": str(tmp_path)}, text=True, capture_output=True, check=True)
    module_path, search = json.loads(completed.stdout)
    assert not module_path.startswith(str(tmp_path)); assert search == list(BUILDER.STATIC_SYS_PATH)

def test_publish_failure_leaves_partial_artifact(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "guard.json"; original = BUILDER.os.open; calls = 0
    def fail_second(path, flags, mode=0o777):
        nonlocal calls; calls += 1
        if calls == 2: raise OSError("injected sidecar failure")
        return original(path, flags, mode)
    monkeypatch.setattr(BUILDER.os, "open", fail_second)
    with pytest.raises(OSError, match="sidecar"): BUILDER.publish_no_clobber(target, {"x": 1})
    assert target.exists()

def test_recoverable_publisher_completes_exact_payload_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "lifecycle.json"; document = {"schema_version":"x", "value":1}
    payload, _ = BUILDER._document_bytes(document)
    target.write_bytes(payload); target.chmod(0o444)
    real_lstat = Path.lstat
    def root_lstat(path):
        status = real_lstat(path); values = list(status); values[4] = 0; values[5] = 0
        return os.stat_result(values)
    monkeypatch.setattr(Path, "lstat", root_lstat)
    BUILDER._publish_contract_recoverable(target, document)
    assert target.with_name(target.name + ".sha256").exists()

def _mount_record(path: Path, *, ro: bool) -> dict[str, object]:
    status = path.lstat()
    return {"mount_id": hash(str(path)) & 65535, "parent_id": 1,
        "major_minor": f"{os.major(status.st_dev)}:{os.minor(status.st_dev)}", "root": str(path),
        "mount_point": str(path), "mount_options": ["ro" if ro else "rw"], "optional_fields": [],
        "fs_type": "ext4", "mount_source": "/dev/test", "super_options": ["rw"]}

def test_seal_window_topology_rejects_extra_nested_mount(monkeypatch: pytest.MonkeyPatch) -> None:
    roots = {"candidate": BUILDER.EXPECTED_CANDIDATE_ROOT, "component": BUILDER.EXPECTED_COMPONENT_ROOT}
    records = [_mount_record(BUILDER.PROJECT_ROOT, ro=True),
        *[_mount_record(path, ro=False) for path in BUILDER._runtime_mount_parents(roots).values()],
        {**_mount_record(BUILDER.PROJECT_ROOT, ro=False), "mount_point": str(BUILDER.PROJECT_ROOT / "src")}]
    monkeypatch.setattr(BUILDER, "_parse_mountinfo", lambda payload: records)
    with pytest.raises(ValueError, match="not exact"): BUILDER._seal_window_mount_identity(roots)

def test_activation_recovery_accepts_only_expected_partial_progress(monkeypatch: pytest.MonkeyPatch) -> None:
    roots = {"candidate": BUILDER.EXPECTED_CANDIDATE_ROOT, "component": BUILDER.EXPECTED_COMPONENT_ROOT}
    parents = BUILDER._runtime_mount_parents(roots)
    project = _mount_record(BUILDER.PROJECT_ROOT, ro=False)
    first_label, first_path = next(iter(parents.items()))
    child = _mount_record(first_path, ro=False)
    monkeypatch.setattr(BUILDER, "_parse_mountinfo", lambda _: [project, child])
    topology = BUILDER._activation_partial_topology(roots)
    assert set(topology["children"]) == {first_label}
    project_ro = {**project, "mount_options": ["ro"]}
    monkeypatch.setattr(BUILDER, "_parse_mountinfo", lambda _: [project_ro, child])
    topology = BUILDER._activation_partial_topology(roots)
    assert set(topology["children"]) == {first_label}

def test_activation_makes_project_ro_before_first_child_bind(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    roots = {"candidate": BUILDER.EXPECTED_CANDIDATE_ROOT, "component": BUILDER.EXPECTED_COMPONENT_ROOT}
    parents = BUILDER._runtime_mount_parents(roots); records = []; commands = []
    artifact_contexts = []
    monkeypatch.setattr(BUILDER.os, "geteuid", lambda: 0)
    monkeypatch.setattr(BUILDER, "_validate_control_parent", lambda path, create: path)
    monkeypatch.setattr(BUILDER, "_validate_external_output_parent", lambda path, create: path)
    monkeypatch.setattr(BUILDER, "_mount_records_at_project", lambda: [])
    monkeypatch.setattr(BUILDER, "_validate_active_namespace", lambda path: (
        {"keeper": {"mount_namespace_inode": 999}}, {"path": "/active"}
    ))
    monkeypatch.setattr(BUILDER, "_assert_execution_namespace", lambda: None)
    monkeypatch.setattr(BUILDER, "_parse_mountinfo", lambda _: records)
    monkeypatch.setattr(BUILDER, "_publish_contract_recoverable", lambda *a: None)
    monkeypatch.setattr(BUILDER, "_mount_contract", lambda *a, **k: {"status":"active"})
    monkeypatch.setattr(BUILDER, "_frozen_iq_source_identities", lambda *a, **k: [])
    monkeypatch.setattr(BUILDER, "_sealed_project_artifact_identities", lambda *a, **k: [])
    monkeypatch.setattr(BUILDER, "_activate_sealed_input_tmpfs", lambda *a, **k: {})
    monkeypatch.setattr(
        BUILDER,
        "_activate_sealed_project_artifacts_tmpfs",
        lambda context, *_a, **_k: artifact_contexts.append(context) or {},
    )
    monkeypatch.setattr(
        BUILDER,
        "_static_expected_json",
        lambda path, sha: ({}, {"path": str(path), "size_bytes": 1, "sha256": sha}),
    )
    monkeypatch.setattr(
        BUILDER, "_regular_file_identity",
        lambda path, **k: {"path": str(path), "size_bytes": 1, "sha256": "a" * 64},
    )
    def command(argv):
        commands.append(tuple(argv)); target = Path(argv[-1])
        if argv[1] == "--bind": records.append(_mount_record(target, ro=target != BUILDER.PROJECT_ROOT))
        elif target == BUILDER.PROJECT_ROOT: records[0] = {**records[0], "mount_options":["ro"]}
        else:
            index = next(i for i,r in enumerate(records) if r["mount_point"] == str(target))
            records[index] = {**records[index], "mount_options":["rw"]}
    monkeypatch.setattr(BUILDER, "_run_mount_command", command)
    metadata = {
        label: {"path": f"/{label}.json", "size_bytes": 1, "sha256": "a" * 64}
        for label in ("plan", "acquisition", "source", "amendment")
    }
    metadata["capacity_contract"] = {"schema_version": "test-capacity"}
    BUILDER.activate_readonly_project(tmp_path, tmp_path/"out", roots, metadata)
    assert len(artifact_contexts) == 1
    for label in ("plan", "acquisition", "source", "amendment"):
        assert artifact_contexts[0][f"{label}_identity"] == metadata[label]
    first_child_bind = next(i for i,c in enumerate(commands) if c[1] == "--bind" and c[-1] != str(BUILDER.PROJECT_ROOT))
    project_ro = next(i for i,c in enumerate(commands) if "remount,bind,ro" in c)
    assert project_ro < first_child_bind


def test_activation_rejects_reopened_identity_drift_before_intent_or_mount(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    roots = {
        "candidate": BUILDER.EXPECTED_CANDIDATE_ROOT,
        "component": BUILDER.EXPECTED_COMPONENT_ROOT,
    }
    metadata = {
        label: {"path": f"/{label}.json", "size_bytes": 1, "sha256": "a" * 64}
        for label in ("plan", "acquisition", "source", "amendment")
    }
    metadata["capacity_contract"] = {"schema_version": "test-capacity"}
    monkeypatch.setattr(BUILDER.os, "geteuid", lambda: 0)
    monkeypatch.setattr(BUILDER, "_validate_control_parent", lambda path, create: path)
    monkeypatch.setattr(BUILDER, "_validate_external_output_parent", lambda path, create: path)
    monkeypatch.setattr(
        BUILDER,
        "_static_expected_json",
        lambda *a, **k: ({}, {"path": "/changed", "size_bytes": 1, "sha256": "a" * 64}),
    )
    monkeypatch.setattr(
        BUILDER,
        "_activation_intent",
        lambda *a, **k: pytest.fail("intent published after identity drift"),
    )
    monkeypatch.setattr(
        BUILDER,
        "_run_mount_command",
        lambda *a, **k: pytest.fail("mount changed after identity drift"),
    )
    with pytest.raises(ValueError, match="identity changed while reopening"):
        BUILDER.activate_readonly_project(tmp_path, tmp_path / "out", roots, metadata)


@pytest.mark.parametrize(
    "failing_inventory",
    ["_frozen_iq_source_identities", "_sealed_project_artifact_identities"],
)
def test_activation_inventory_failure_precedes_intent_publication_and_mounts(
    failing_inventory: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    roots = {
        "candidate": BUILDER.EXPECTED_CANDIDATE_ROOT,
        "component": BUILDER.EXPECTED_COMPONENT_ROOT,
    }
    metadata = {
        label: {"path": f"/{label}.json", "size_bytes": 1, "sha256": "a" * 64}
        for label in ("plan", "acquisition", "source", "amendment")
    }
    metadata["capacity_contract"] = {"schema_version": "test-capacity"}
    monkeypatch.setattr(BUILDER.os, "geteuid", lambda: 0)
    monkeypatch.setattr(BUILDER, "_validate_control_parent", lambda path, create: path)
    monkeypatch.setattr(BUILDER, "_validate_external_output_parent", lambda path, create: path)
    monkeypatch.setattr(
        BUILDER, "_static_expected_json",
        lambda path, sha: ({}, {"path": str(path), "size_bytes": 1, "sha256": sha}),
    )
    monkeypatch.setattr(
        BUILDER, "_regular_file_identity",
        lambda path, **_k: {"path": str(path), "size_bytes": 1, "sha256": "a" * 64},
    )
    monkeypatch.setattr(BUILDER, "_frozen_iq_source_identities", lambda *_a: [])
    monkeypatch.setattr(BUILDER, "_sealed_project_artifact_identities", lambda *_a: [])
    monkeypatch.setattr(
        BUILDER,
        failing_inventory,
        lambda *_a: (_ for _ in ()).throw(ValueError("inventory preflight failed")),
    )
    monkeypatch.setattr(
        BUILDER,
        "_activation_intent",
        lambda *a, **k: pytest.fail("intent published after inventory failure"),
    )
    monkeypatch.setattr(
        BUILDER,
        "_publish_contract_recoverable",
        lambda *a, **k: pytest.fail("artifact published after inventory failure"),
    )
    monkeypatch.setattr(
        BUILDER,
        "_run_mount_command",
        lambda *a, **k: pytest.fail("mount changed after inventory failure"),
    )

    with pytest.raises(ValueError, match="inventory preflight failed"):
        BUILDER.activate_readonly_project(tmp_path, tmp_path / "out", roots, metadata)


def test_mount_contract_reuses_complete_expanded_artifact_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project"
    control = tmp_path / "control"
    output = tmp_path / "output"
    for path in (project, control, output):
        path.mkdir()
    roots = {"candidate": project / "candidate", "component": project / "component"}
    metadata = {"capacity_contract": {"schema_version": "capacity"}}
    expanded = {"complete": object()}
    observed = []
    monkeypatch.setattr(BUILDER, "PROJECT_ROOT", project)
    monkeypatch.setattr(BUILDER, "_validate_active_namespace", lambda _p: (
        {"keeper": {"mount_namespace_inode": 123}}, {"path": "/active"}
    ))
    monkeypatch.setattr(
        BUILDER, "_expanded_activation_context",
        lambda actual_metadata, actual_roots: (
            expanded
            if actual_metadata is metadata and actual_roots is roots
            else pytest.fail("mount contract expanded different inputs")
        ),
    )
    monkeypatch.setattr(BUILDER, "_assert_execution_namespace", lambda: None)
    monkeypatch.setattr(
        BUILDER, "_seal_window_mount_identity",
        lambda _roots: {"project": {"mount_point": str(project)}, "runtime_children": {}},
    )
    monkeypatch.setattr(BUILDER, "_results_authority_identity", lambda _p: {"path": str(output)})
    monkeypatch.setattr(
        BUILDER, "_capacity_contract",
        lambda context: observed.append(("capacity", context)) or {"schema_version": "capacity"},
    )
    monkeypatch.setattr(BUILDER, "_campaign_execution_identity", lambda: {"status": "PASS"})
    monkeypatch.setattr(
        BUILDER, "_sealed_input_snapshot",
        lambda context, **_k: observed.append(("input", context)) or {"status": "PASS"},
    )
    monkeypatch.setattr(
        BUILDER, "_sealed_project_artifact_snapshot",
        lambda context, **_k: observed.append(("artifacts", context)) or {"status": "PASS"},
    )
    monkeypatch.setattr(
        BUILDER, "_regular_file_identity",
        lambda path, **_k: {"path": str(path), "size_bytes": 1, "sha256": "a" * 64},
    )

    document = BUILDER._mount_contract(
        control, output, roots, metadata, status="seal_window_active"
    )

    assert document["sealed_project_artifacts"] == {"status": "PASS"}
    assert observed == [
        ("capacity", expanded), ("input", expanded), ("artifacts", expanded)
    ]


def test_small_artifact_inventory_is_static_before_launcher_load(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(BUILDER, "LAUNCHER", None)
    monkeypatch.setattr(BUILDER, "_collect_regular_identities", lambda *_a: None)
    monkeypatch.setattr(
        BUILDER, "_planned_artifact_mirror_bindings", lambda *_a: {}
    )

    def identity(path: Path, **_kwargs: object) -> dict[str, object]:
        return {"path": str(Path(path).absolute()), "size_bytes": 1, "sha256": "a" * 64}

    monkeypatch.setattr(BUILDER, "_regular_file_identity", identity)
    implementation = {
        "launcher": identity(BUILDER.LAUNCHER_PATH),
        "runtime_guard_builder": identity(BUILDER.HERE / "build_runtime_guard_v3.py"),
        "normalizer": identity(BUILDER.NORMALIZER_PATH),
        "evaluator": identity(BUILDER.EVALUATOR_PATH),
        "evaluator_lock_freezer": identity(BUILDER.EVALUATOR_LOCK_FREEZER_PATH),
    }
    context = {
        label: {} for label in ("plan", "acquisition", "source")
    }
    context.update({
        "amendment": {
            "amended_implementation": implementation,
            BUILDER.PLANNED_ARTIFACT_MIRROR_AMENDMENT_KEY: (
                BUILDER._planned_artifact_mirror_contract()
            ),
        },
        "roots": {
            "candidate": BUILDER.EXPECTED_CANDIDATE_ROOT,
            "component": BUILDER.EXPECTED_COMPONENT_ROOT,
        },
        **{
            f"{label}_identity": identity(Path(f"/{label}.json"))
            for label in ("plan", "acquisition", "source", "amendment")
        },
        "component_closure_identity": identity(BUILDER.COMPONENT_CLOSURE_PATH),
        "component_closure_generator_identity": identity(
            BUILDER.COMPONENT_CLOSURE_GENERATOR_PATH
        ),
    })

    artifacts = BUILDER._sealed_project_artifact_identities(context)

    assert BUILDER.LAUNCHER is None
    assert identity(BUILDER.FROZEN_LAUNCHER_PATH) in artifacts
    assert identity(BUILDER.NORMALIZER_PATH) in artifacts


def test_real_component_closure_over_four_mib_uses_fixed_bounded_identity() -> None:
    expected = BUILDER._regular_file_identity(
        BUILDER.COMPONENT_CLOSURE_PATH,
        maximum_bytes=BUILDER.MAXIMUM_SEALED_PROJECT_ARTIFACT_BYTES,
    )
    assert int(expected["size_bytes"]) > 4 * 1024 * 1024
    assert BUILDER._bounded_frozen_artifact_identity(
        BUILDER.COMPONENT_CLOSURE_PATH, expected
    ) == expected


def test_bounded_frozen_artifact_rejects_size_or_content_drift(tmp_path: Path) -> None:
    artifact = tmp_path / "artifact.bin"
    artifact.write_bytes(b"first")
    expected = BUILDER._regular_file_identity(artifact, maximum_bytes=64)
    artifact.write_bytes(b"other")
    with pytest.raises(ValueError, match="differs from frozen identity"):
        BUILDER._bounded_frozen_artifact_identity(
            artifact, expected, maximum_bytes=64
        )


def test_bounded_frozen_artifact_rejects_declared_or_live_oversize(
    tmp_path: Path,
) -> None:
    artifact = tmp_path / "artifact.bin"
    artifact.write_bytes(b"x" * 65)
    expected = {
        "path": str(artifact.absolute()),
        "size_bytes": 65,
        "sha256": "a" * 64,
    }
    with pytest.raises(ValueError, match="identity or byte ceiling"):
        BUILDER._bounded_frozen_artifact_identity(
            artifact, expected, maximum_bytes=64
        )
    with pytest.raises(ValueError, match="exceeds byte bound"):
        BUILDER._regular_file_identity(artifact, maximum_bytes=64)

@pytest.mark.parametrize("mutation", ["major_minor", "mount_source", "options", "parent_id",
    "optional_fields", "fs_type", "super_options", "nonwrite_option"])
def test_rollback_child_mount_rejects_foreign_traits(mutation: str) -> None:
    path = BUILDER.EXPECTED_CANDIDATE_ROOT.parent
    expected = _mount_record(path, ro=False)
    actual = dict(expected)
    if mutation == "major_minor": actual["major_minor"] = "0:999"
    elif mutation == "mount_source": actual["mount_source"] = "/dev/foreign"
    elif mutation == "options": actual["mount_options"] = ["ro", "rw"]
    elif mutation == "parent_id": actual["parent_id"] = int(expected["parent_id"]) + 1
    elif mutation == "optional_fields": actual["optional_fields"] = ["shared:99"]
    elif mutation == "fs_type": actual["fs_type"] = "tmpfs"
    elif mutation == "super_options": actual["super_options"] = ["ro"]
    else: actual["mount_options"] = ["rw", "nosuid"]
    with pytest.raises(ValueError, match="runtime child mount"):
        BUILDER._validate_runtime_child_mount(actual, path, expected, allow_readonly=True)

def test_close_retries_exact_partial_child_subset(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    roots = {"candidate": BUILDER.EXPECTED_CANDIDATE_ROOT, "component": BUILDER.EXPECTED_COMPONENT_ROOT}
    parents = BUILDER._runtime_mount_parents(roots); project_record = _mount_record(BUILDER.PROJECT_ROOT, ro=True)
    children = {label: _mount_record(path, ro=False) for label, path in parents.items()}; present = set(parents)
    active = {"status":"seal_window_active","campaign_metadata":{"m":1},"control_parent":{"path":str(tmp_path)},
        "external_output_parent":{"path":str(tmp_path/"out")},"readonly_mount":project_record,
        "seal_window_topology":{"runtime_children":children}}
    fail = {"component": True}
    monkeypatch.setattr(BUILDER.os, "geteuid", lambda: 0)
    monkeypatch.setattr(BUILDER, "_validate_control_parent", lambda path, create: path)
    monkeypatch.setattr(BUILDER, "_validate_external_output_parent", lambda path, create: path)
    monkeypatch.setattr(BUILDER, "_load_mount_contract", lambda path: (active, {}))
    monkeypatch.setattr(BUILDER, "_activate_runtime_authority_mounts", lambda *a, **k: {})
    protected = {label: {"path": str(path)} for label, path in roots.items()}
    monkeypatch.setattr(BUILDER, "_load_repair_selfhashed", lambda path, **kwargs:
        (({"campaign_metadata":{"m":1}, "protected_roots":protected}, {})
         if "sealed-runtime" in path.name else (active, {})))
    monkeypatch.setattr(BUILDER, "_recursive_protected_root_identity", lambda path: {"path": str(path)})
    monkeypatch.setattr(BUILDER, "_parse_mountinfo", lambda _: [project_record, *(children[x] for x in present)])
    def command(argv):
        label = next(k for k,v in parents.items() if v == Path(argv[-1]))
        if label == "component" and fail[label]: fail[label]=False; raise RuntimeError("second unmount")
        present.remove(label)
    monkeypatch.setattr(BUILDER, "_run_mount_command", command)
    closed_mount = {**project_record,"path":str(BUILDER.PROJECT_ROOT),"st_dev":BUILDER.PROJECT_ROOT.stat().st_dev,
        "st_ino":BUILDER.PROJECT_ROOT.stat().st_ino,"uid":BUILDER.PROJECT_ROOT.stat().st_uid,
        "gid":BUILDER.PROJECT_ROOT.stat().st_gid,"mode":stat.S_IMODE(BUILDER.PROJECT_ROOT.stat().st_mode)}
    monkeypatch.setattr(BUILDER, "_readonly_project_mount_identity", lambda: closed_mount)
    monkeypatch.setattr(BUILDER, "_mount_contract", lambda *a, **k: {"readonly_mount":closed_mount})
    monkeypatch.setattr(BUILDER, "_publish_contract_recoverable", lambda *a: None)
    with pytest.raises(RuntimeError, match="second unmount"):
        BUILDER.close_seal_window(tmp_path, tmp_path/"out", roots, {"m":1})
    BUILDER.close_seal_window(tmp_path, tmp_path/"out", roots, {"m":1})
    assert not present

def test_compiled_launcher_identity_is_current() -> None:
    assert BUILDER._regular_file_identity(BUILDER.LAUNCHER_PATH)["sha256"] == BUILDER.EXPECTED_LAUNCHER_V3_SHA256


def test_compiled_evaluator_identity_is_current() -> None:
    assert (
        BUILDER._regular_file_identity(BUILDER.EVALUATOR_PATH)["sha256"]
        == BUILDER.EXPECTED_EVALUATOR_SHA256
    )

def test_freeze_handoffs_are_unique_append_only_paths() -> None:
    first = BUILDER._handoff_paths(BUILDER.DEFAULT_CONTROL_PARENT, "1" * 64)[0]
    second = BUILDER._handoff_paths(BUILDER.DEFAULT_CONTROL_PARENT, "2" * 64)[0]
    assert first != second and first.parent == second.parent == BUILDER.DEFAULT_CONTROL_PARENT
    with pytest.raises(ValueError, match="attempt id"):
        BUILDER._handoff_paths(BUILDER.DEFAULT_CONTROL_PARENT, "short")

def test_rollback_without_project_mount_requires_durable_ready_marker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    roots = {"candidate": BUILDER.EXPECTED_CANDIDATE_ROOT, "component": BUILDER.EXPECTED_COMPONENT_ROOT}
    control = tmp_path / "control"; control.mkdir(); output = tmp_path / "output"; output.mkdir()
    intent_path = control / "rollback-sealed-runtimes-intent-v3.json"; intent_path.write_text("x")
    metadata = {label: {"path": f"/{label}", "size_bytes": 1, "sha256": label[0] * 64}
        for label in ("plan", "acquisition", "source", "amendment")}
    capacity = {"schema_version": "test-capacity"}
    context = {"roots": roots, "capacity_contract": capacity,
        **{f"{label}_identity": value for label, value in metadata.items()}}
    metadata["capacity_contract"] = capacity
    recovery = {label: {"backup": {"path": str(path)}, "transaction_path": str(tmp_path/f"tx-{label}"),
        "logical_manifest_sha256": "1" * 64, "content_manifest_sha256": "2" * 64}
        for label, path in roots.items()}
    sealed = {"recovery": recovery, "protected_roots": {}}
    builder_identity = BUILDER._regular_file_identity(SCRIPT)
    rollback_intent = {"campaign_metadata": metadata, "sealed_runtime_state": {"sha256":"s"},
        "runtime_guard_builder_source": builder_identity,
        "runtime_guard_builder_control_copy": builder_identity}
    monkeypatch.setattr(BUILDER.os, "geteuid", lambda: 0)
    monkeypatch.setattr(BUILDER, "_load_sealed_state", lambda path: (sealed, {"sha256":"s"}))
    monkeypatch.setattr(BUILDER, "_load_repair_selfhashed", lambda *a, **k: (rollback_intent, {}))
    monkeypatch.setattr(BUILDER, "_parse_mountinfo", lambda payload: [])
    with pytest.raises(ValueError, match="vanished before durable rollback-ready"):
        BUILDER.rollback_sealed_runtimes(context, control, output)


def test_rollback_resume_rejects_builder_identity_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    control = tmp_path / "control"
    control.mkdir()
    intent_path = control / "rollback-sealed-runtimes-intent-v3.json"
    intent_path.write_text("x", encoding="ascii")
    roots = {"candidate": BUILDER.EXPECTED_CANDIDATE_ROOT,
        "component": BUILDER.EXPECTED_COMPONENT_ROOT}
    metadata = {label: {"path": f"/{label}", "size_bytes": 1,
        "sha256": label[0] * 64} for label in ("plan", "acquisition", "source", "amendment")}
    capacity = {"schema_version": "test-capacity"}
    context = {"roots": roots, "capacity_contract": capacity,
        **{f"{label}_identity": value for label, value in metadata.items()}}
    metadata["capacity_contract"] = capacity
    live_builder = BUILDER._regular_file_identity(SCRIPT)
    intent = {"campaign_metadata": metadata, "sealed_runtime_state": {"sha256": "s"},
        "runtime_guard_builder_source": {**live_builder, "sha256": "0" * 64},
        "runtime_guard_builder_control_copy": live_builder}
    monkeypatch.setattr(BUILDER.os, "geteuid", lambda: 0)
    monkeypatch.setattr(BUILDER, "_load_sealed_state",
        lambda path: ({"recovery": {}}, {"sha256": "s"}))
    monkeypatch.setattr(BUILDER, "_load_repair_selfhashed",
        lambda *args, **kwargs: (intent, {}))
    with pytest.raises(ValueError, match="rollback intent differs"):
        BUILDER.rollback_sealed_runtimes(context, control, tmp_path / "output")


def test_seal_resume_rejects_builder_control_copy_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    control = tmp_path / "control"
    control.mkdir()
    intent_path = control / "seal-transaction-intent-v3.json"
    intent_path.write_text("x", encoding="ascii")
    metadata = {label: {"path": f"/{label}", "size_bytes": 1,
        "sha256": label[0] * 64} for label in ("plan", "acquisition", "source", "amendment")}
    capacity = {"schema_version": "test-capacity"}
    closure_identity = {"path": "/closure", "size_bytes": 1, "sha256": "c" * 64}
    generator_identity = {"path": "/generator", "size_bytes": 1, "sha256": "g" * 64}
    context = {**{f"{label}_identity": value for label, value in metadata.items()},
        "capacity_contract": capacity,
        "component_closure_identity": closure_identity,
        "component_closure_generator_identity": generator_identity,
        "component_closure": {"raw_manifest_sha256": "1" * 64,
            "content_manifest_sha256": "2" * 64}}
    metadata["capacity_contract"] = capacity
    live_builder = BUILDER._regular_file_identity(SCRIPT)
    intent = {"campaign_metadata": metadata,
        "runtime_guard_builder_control_copy": {**live_builder, "sha256": "0" * 64},
        "component_runtime_closure": closure_identity,
        "component_runtime_closure_generator": generator_identity,
        "component_raw_manifest_sha256": "1" * 64,
        "component_content_manifest_sha256": "2" * 64}
    monkeypatch.setattr(BUILDER, "_load_repair_selfhashed",
        lambda *args, **kwargs: (intent, {}))
    with pytest.raises(ValueError, match="seal transaction intent"):
        BUILDER.seal_with_journal(context, control)


def test_rollback_retries_after_first_child_unmount_failure_and_completes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "project"
    candidate = _tree(project / "candidate-parent", "runtime")
    component = _tree(project / "component-parent", "runtime")
    roots = {"candidate": candidate, "component": component}
    parents = {label: root.parent for label, root in roots.items()}
    control = tmp_path / "control"
    control.mkdir()
    intent_path = control / "rollback-sealed-runtimes-intent-v3.json"
    intent_path.write_text("intent", encoding="ascii")
    closed_path = control / "readonly-project-closed-v3.json"
    closed_path.write_text("closed", encoding="ascii")
    metadata = {label: {"path": f"/{label}", "size_bytes": 1,
        "sha256": label[0] * 64} for label in ("plan", "acquisition", "source", "amendment")}
    capacity = {"schema_version": "test-capacity"}
    context = {"roots": roots, "capacity_contract": capacity,
        **{f"{label}_identity": value for label, value in metadata.items()}}
    metadata["capacity_contract"] = capacity
    project_record = _mount_record(project, ro=True)
    child_records = {label: _mount_record(path, ro=False)
        for label, path in parents.items()}
    records = [project_record, *child_records.values()]
    parent_metadata = {label: {"uid": path.stat().st_uid, "gid": path.stat().st_gid,
        "mode": stat.S_IMODE(path.stat().st_mode), "st_dev": path.stat().st_dev,
        "st_ino": path.stat().st_ino} for label, path in parents.items()}
    recovery = {}
    intent_roots = {}
    for label, root in roots.items():
        transaction = root.parent / f"tx-{label}"
        backup = transaction / "backup"
        shutil.copytree(root, backup, symlinks=True, copy_function=shutil.copy2)
        content = BUILDER._content_tree_hash(root)
        recovery[label] = {"transaction_path": str(transaction),
            "backup": {"path": str(backup)}, "content_manifest_sha256": content}
        intent_roots[label] = {"root": str(root), "stage": str(transaction / "rollback-stage"),
            "failed": str(transaction / "failed-full-validation-root")}
    sealed_identity = {"path": "/sealed", "size_bytes": 1, "sha256": "s" * 64}
    sealed = {"recovery": recovery, "runtime_parent_original_metadata": parent_metadata}
    builder_identity = BUILDER._regular_file_identity(SCRIPT)
    closed_identity = BUILDER._regular_file_identity(closed_path)
    intent = {"campaign_metadata": metadata, "sealed_runtime_state": sealed_identity,
        "runtime_guard_builder_source": builder_identity,
        "runtime_guard_builder_control_copy": builder_identity,
        "closed_mount_contract": closed_identity,
        "expected_runtime_children": child_records, "roots": intent_roots}
    intent["rollback_payload_sha256"] = BUILDER._sha256_document(intent)
    closed_document = {"readonly_mount": project_record}
    publications: dict[str, dict[str, object]] = {}
    fail_once = {"candidate": True}

    monkeypatch.setattr(BUILDER, "PROJECT_ROOT", project)
    monkeypatch.setattr(BUILDER.os, "geteuid", lambda: 0)
    monkeypatch.setattr(BUILDER.os, "fchown", lambda *args: None)
    monkeypatch.setattr(BUILDER.os, "fchmod", lambda *args: None)
    monkeypatch.setattr(BUILDER.os, "fsync", lambda *args: None)
    monkeypatch.setattr(BUILDER, "_load_sealed_state", lambda path: (sealed, sealed_identity))
    monkeypatch.setattr(BUILDER, "_load_repair_selfhashed", lambda *args, **kwargs: (intent, {}))
    monkeypatch.setattr(BUILDER, "_load_mount_contract",
        lambda path: (closed_document, closed_identity))
    monkeypatch.setattr(BUILDER, "_parse_mountinfo", lambda payload: list(records))
    monkeypatch.setattr(BUILDER, "_runtime_parent_metadata", lambda current: parent_metadata)
    monkeypatch.setattr(BUILDER, "_seal_installed_root", lambda path: None)
    monkeypatch.setattr(BUILDER, "_recursive_protected_root_identity",
        lambda path: {"path": str(path), "content": BUILDER._content_tree_hash(path)})
    for label in recovery:
        recovery[label]["backup"] = BUILDER._recursive_protected_root_identity(
            Path(str(recovery[label]["backup"]["path"]))
        )

    def publish(path: Path, document: dict[str, object]) -> None:
        publications[path.name] = dict(document)

    def mount(argv) -> None:
        target = Path(argv[-1])
        if argv[0] == str(BUILDER.UMOUNT_PATH):
            open_targets: set[Path] = set()
            for descriptor in Path("/proc/self/fd").iterdir():
                try:
                    link = os.readlink(descriptor)
                except FileNotFoundError:
                    continue
                if link.startswith("/"):
                    open_targets.add(Path(link))
            assert target not in open_targets, "rollback retained an EBUSY self-holder"
            if target == parents["candidate"] and fail_once["candidate"]:
                fail_once["candidate"] = False
                raise RuntimeError("injected first-child retry boundary")
            records[:] = [record for record in records if record["mount_point"] != str(target)]
        elif argv[1] == "--bind":
            label = next(label for label, path in parents.items() if path == target)
            records.append(child_records[label])

    monkeypatch.setattr(BUILDER, "_publish_contract_recoverable", publish)
    monkeypatch.setattr(BUILDER, "_run_mount_command", mount)
    with pytest.raises(RuntimeError, match="retry boundary"):
        BUILDER.rollback_sealed_runtimes(context, control, tmp_path / "output")
    assert {record["mount_point"] for record in records} == {
        str(project), str(parents["candidate"])}
    result = BUILDER.rollback_sealed_runtimes(context, control, tmp_path / "output")
    assert result["status"] == "rollback_complete_project_unmounted"
    assert records == []
    assert "rollback-sealed-runtimes-ready-to-unmount-v3.json" in publications
    assert "rollback-sealed-runtimes-complete-v3.json" in publications


def test_rollback_repairs_partial_ready_and_complete_publications_on_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "project"
    roots = {"candidate": _tree(project / "candidate-parent", "runtime"),
        "component": _tree(project / "component-parent", "runtime")}
    control = tmp_path / "control"
    control.mkdir()
    intent_path = control / "rollback-sealed-runtimes-intent-v3.json"
    intent_path.write_text("intent", encoding="ascii")
    ready_path = control / "rollback-sealed-runtimes-ready-to-unmount-v3.json"
    ready_path.write_text("payload-only", encoding="ascii")
    metadata = {label: {"path": f"/{label}", "size_bytes": 1,
        "sha256": label[0] * 64} for label in ("plan", "acquisition", "source", "amendment")}
    capacity = {"schema_version": "test-capacity"}
    context = {"roots": roots, "capacity_contract": capacity,
        **{f"{label}_identity": value for label, value in metadata.items()}}
    metadata["capacity_contract"] = capacity
    parent_metadata = {label: {"uid": root.parent.stat().st_uid,
        "gid": root.parent.stat().st_gid, "mode": stat.S_IMODE(root.parent.stat().st_mode),
        "st_dev": root.parent.stat().st_dev, "st_ino": root.parent.stat().st_ino}
        for label, root in roots.items()}
    identity = lambda path: {"path": str(path), "content": BUILDER._content_tree_hash(path)}
    recovery = {label: {"content_manifest_sha256": BUILDER._content_tree_hash(root),
        "backup": identity(root), "transaction_path": str(root.parent / f"tx-{label}")}
        for label, root in roots.items()}
    sealed_identity = {"path": "/sealed", "size_bytes": 1, "sha256": "s" * 64}
    sealed = {"recovery": recovery, "runtime_parent_original_metadata": parent_metadata}
    builder_identity = BUILDER._regular_file_identity(SCRIPT)
    intent = {"campaign_metadata": metadata, "sealed_runtime_state": sealed_identity,
        "runtime_guard_builder_source": builder_identity,
        "runtime_guard_builder_control_copy": builder_identity,
        "rollback_payload_sha256": "i" * 64}
    ready = {"rollback_intent": BUILDER._regular_file_identity(intent_path),
        "sealed_runtime_state": sealed_identity, "restored_runtime_parents": parent_metadata,
        "restored_roots": {label: identity(root) for label, root in roots.items()}}
    repaired: list[str] = []
    completion_attempts = 0

    monkeypatch.setattr(BUILDER, "PROJECT_ROOT", project)
    monkeypatch.setattr(BUILDER.os, "geteuid", lambda: 0)
    monkeypatch.setattr(BUILDER, "_load_sealed_state", lambda path: (sealed, sealed_identity))
    monkeypatch.setattr(BUILDER, "_parse_mountinfo", lambda payload: [])
    monkeypatch.setattr(BUILDER, "_runtime_parent_metadata", lambda current: parent_metadata)
    monkeypatch.setattr(BUILDER, "_recursive_protected_root_identity", identity)

    def load_repair(path: Path, **kwargs):
        if path == intent_path:
            return intent, BUILDER._regular_file_identity(intent_path)
        assert path == ready_path
        repaired.append(path.name)
        ready_path.with_name(ready_path.name + ".sha256").write_text("repaired", encoding="ascii")
        return ready, {"path": str(path)}

    def publish(path: Path, document: dict[str, object]) -> None:
        nonlocal completion_attempts
        if path.name == "rollback-sealed-runtimes-complete-v3.json":
            completion_attempts += 1
            if completion_attempts == 1:
                path.write_text("payload-only", encoding="ascii")
                raise OSError("injected complete-sidecar crash")
            path.with_name(path.name + ".sha256").write_text("repaired", encoding="ascii")

    monkeypatch.setattr(BUILDER, "_load_repair_selfhashed", load_repair)
    monkeypatch.setattr(BUILDER, "_publish_contract_recoverable", publish)
    with pytest.raises(OSError, match="complete-sidecar crash"):
        BUILDER.rollback_sealed_runtimes(context, control, tmp_path / "output")
    assert repaired == [ready_path.name]
    assert (control / "rollback-sealed-runtimes-complete-v3.json").exists()
    result = BUILDER.rollback_sealed_runtimes(context, control, tmp_path / "output")
    assert result["status"] == "rollback_complete_project_unmounted"
    assert completion_attempts == 2
    assert (control / "rollback-sealed-runtimes-complete-v3.json.sha256").exists()


def _private_mountinfo(*, shared: bool = False) -> bytes:
    optional = " shared:1" if shared else ""
    return (
        f"1 0 0:1 / / rw,relatime{optional} - ext4 /dev/root rw\n"
        "2 1 0:2 / /proc rw,nosuid - proc proc rw\n"
    ).encode("utf-8")


def test_root_private_attestation_rejects_shared_master_slave_topology() -> None:
    with pytest.raises(ValueError, match="not recursively private"):
        BUILDER._root_private_attestation(_private_mountinfo(shared=True))
    attestation = BUILDER._root_private_attestation(_private_mountinfo())
    assert attestation["make_rprivate_completed_before_project_bind"] is True
    assert attestation["forbidden_propagation_fields"] == []


def test_keeper_first_mount_mutation_is_root_make_rprivate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    events: list[object] = []
    monkeypatch.setattr(BUILDER.os, "chdir", lambda value: events.append(("chdir", value)))
    monkeypatch.setattr(BUILDER.os, "unshare", lambda value: events.append(("unshare", value)))
    monkeypatch.setattr(
        BUILDER,
        "_run_mount_command_raw",
        lambda argv: events.append(("mount", tuple(argv))),
    )
    monkeypatch.setattr(
        BUILDER,
        "_keeper_registration_fields",
        lambda intent: events.append(("attest", intent["attempt_id"])) or {
            "attempt_id": intent["attempt_id"]
        },
    )
    monkeypatch.setattr(
        BUILDER,
        "_publish_contract_recoverable",
        lambda path, document: events.append(("publish", path.name, document["status"])),
    )
    result = BUILDER._initialize_keeper_namespace(tmp_path, {"attempt_id": "a"})
    assert events[:4] == [
        ("chdir", str(tmp_path)),
        ("unshare", BUILDER.CLONE_NEWNS),
        ("mount", (str(BUILDER.MOUNT_PATH), "--make-rprivate", "/")),
        ("attest", "a"),
    ]
    assert events[4][0] == "publish"
    assert result["status"] == "keeper_registered_root_recursively_private"


def test_source_has_no_project_bind_before_make_rprivate() -> None:
    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    function = next(
        node for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == "_initialize_keeper_namespace"
    )
    calls = [ast.unparse(node) for node in ast.walk(function) if isinstance(node, ast.Call)]
    mount_calls = [call for call in calls if "_run_mount_command_raw" in call]
    assert mount_calls == [
        "_run_mount_command_raw((str(MOUNT_PATH), '--make-rprivate', '/'))"
    ]
    assert all("--bind" not in call for call in calls)
    raw_call_owners = []
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if any(
            isinstance(child, ast.Call)
            and isinstance(child.func, ast.Name)
            and child.func.id == "_run_mount_command_raw"
            for child in ast.walk(node)
        ):
            raw_call_owners.append(node.name)
    assert raw_call_owners == ["_initialize_keeper_namespace", "_run_mount_command"]


def test_external_inventory_requires_exact_host_and_nine_service_namespaces(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    inodes = tuple(range(100, 110))
    monkeypatch.setattr(BUILDER, "EXPECTED_EXTERNAL_NAMESPACE_INODES", inodes)
    namespace_by_pid: dict[str, int] = {}
    payload_by_pid: dict[str, bytes] = {}
    for offset, inode in enumerate(inodes, start=10):
        pid = tmp_path / str(offset)
        pid.mkdir()
        namespace_by_pid[str(pid / "ns/mnt")] = inode
        payload_by_pid[str(pid / "mountinfo")] = (
            f"1 0 0:1 / / rw shared:{inode} - ext4 /dev/root rw\n"
        ).encode()
    monkeypatch.setattr(BUILDER, "_pid_starttime", lambda path: 7)
    monkeypatch.setattr(
        BUILDER.os,
        "readlink",
        lambda path: f"mnt:[{namespace_by_pid[str(path)]}]",
    )
    monkeypatch.setattr(
        BUILDER,
        "_mountinfo_bytes",
        lambda path: payload_by_pid[str(path)],
    )
    snapshot = BUILDER._capture_external_namespace_inventory(proc_root=tmp_path)
    assert [item["namespace_inode"] for item in snapshot["namespaces"]] == list(inodes)
    assert len(snapshot["inventory_sha256"]) == 64


def test_mount_mutation_is_wrapped_by_external_zero_delta(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    baseline = {"inventory_sha256": "b" * 64}
    events: list[object] = []
    monkeypatch.setattr(BUILDER, "_EXECUTION_NAMESPACE_INODE", 4242)
    monkeypatch.setattr(BUILDER, "_EXECUTION_NAMESPACE_EXTERNAL_BASELINE", baseline)
    monkeypatch.setattr(BUILDER, "_assert_execution_namespace", lambda: events.append("attest"))
    monkeypatch.setattr(
        BUILDER,
        "_capture_external_fixed_point",
        lambda **kwargs: events.append(("scan", kwargs["exclude_inode"])) or baseline,
    )
    monkeypatch.setattr(
        BUILDER,
        "_run_mount_command_raw",
        lambda argv: events.append(("command", tuple(argv))),
    )
    BUILDER._run_mount_command((str(BUILDER.MOUNT_PATH), "--bind", "/x", "/x"))
    assert events == [
        "attest",
        ("scan", 4242),
        ("command", (str(BUILDER.MOUNT_PATH), "--bind", "/x", "/x")),
        "attest",
        ("scan", 4242),
    ]


def test_mount_mutation_rejects_service_namespace_delta(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    baseline = {"inventory_sha256": "b" * 64}
    changed = {"inventory_sha256": "c" * 64}
    snapshots = iter((baseline, changed))
    monkeypatch.setattr(BUILDER, "_EXECUTION_NAMESPACE_INODE", 4242)
    monkeypatch.setattr(BUILDER, "_EXECUTION_NAMESPACE_EXTERNAL_BASELINE", baseline)
    monkeypatch.setattr(BUILDER, "_assert_execution_namespace", lambda: None)
    monkeypatch.setattr(
        BUILDER, "_capture_external_fixed_point", lambda **kwargs: next(snapshots)
    )
    monkeypatch.setattr(BUILDER, "_run_mount_command_raw", lambda argv: None)
    with pytest.raises(ValueError, match="escaped the persistent namespace"):
        BUILDER._run_mount_command((str(BUILDER.MOUNT_PATH), "--bind", "/x", "/x"))


def test_shared_host_and_nine_slave_namespaces_remain_byte_stable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    namespaces = []
    for index, inode in enumerate(BUILDER.EXPECTED_EXTERNAL_NAMESPACE_INODES):
        propagation = "shared:1" if index == 0 else f"shared:{index + 1} master:1"
        line = f"1 0 0:1 / / rw {propagation} - ext4 /dev/root rw"
        namespaces.append({
            "namespace_inode": inode,
            "mountinfo_sha256": "a" * 64,
            "mountinfo_size_bytes": len(line),
            "mountinfo": [line],
        })
    baseline = {
        "expected_namespace_inodes": list(BUILDER.EXPECTED_EXTERNAL_NAMESPACE_INODES),
        "namespaces": namespaces,
        "ignored_empty_namespaces": [],
        "anonymous_or_unexpected_namespace_fd_handles": [],
        "inventory_sha256": "b" * 64,
    }
    monkeypatch.setattr(BUILDER, "_EXECUTION_NAMESPACE_INODE", 99999)
    monkeypatch.setattr(BUILDER, "_EXECUTION_NAMESPACE_EXTERNAL_BASELINE", baseline)
    monkeypatch.setattr(BUILDER, "_assert_execution_namespace", lambda: None)
    monkeypatch.setattr(BUILDER, "_capture_external_fixed_point", lambda **kwargs: baseline)
    commands: list[tuple[str, ...]] = []
    monkeypatch.setattr(
        BUILDER, "_run_mount_command_raw", lambda argv: commands.append(tuple(argv))
    )
    BUILDER._run_mount_command((str(BUILDER.MOUNT_PATH), "--bind", "/x", "/x"))
    assert commands == [(str(BUILDER.MOUNT_PATH), "--bind", "/x", "/x")]


def test_namespace_intent_claim_collision_has_no_spawn_authority(tmp_path: Path) -> None:
    path = tmp_path / BUILDER.NAMESPACE_INTENT_NAME
    path.write_bytes(b"foreign-existing-bytes")
    assert BUILDER._publish_namespace_claim(path, {"different": True}) is False
    assert path.read_bytes() == b"foreign-existing-bytes"


def test_enter_namespace_rejects_stale_namespace_handle_inode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    baseline = {"inventory_sha256": "a" * 64}
    keeper = {"pid": 777, "mount_namespace_inode": 4242}
    active = {"keeper": keeper, "external_inventory_after": baseline}
    monkeypatch.setattr(BUILDER, "_validate_active_namespace", lambda path: (active, {}))
    monkeypatch.setattr(BUILDER, "_capture_external_fixed_point", lambda **kwargs: baseline)
    monkeypatch.setattr(BUILDER.os, "readlink", lambda path: "mnt:[4242]")
    monkeypatch.setattr(BUILDER.os, "open", lambda *args: 91)
    monkeypatch.setattr(BUILDER.os, "fstat", lambda descriptor: types.SimpleNamespace(st_ino=9999))
    closed: list[int] = []
    monkeypatch.setattr(BUILDER.os, "close", lambda descriptor: closed.append(descriptor))
    called: list[int] = []
    monkeypatch.setattr(BUILDER.os, "setns", lambda *args: called.append(1))
    with pytest.raises(ValueError, match="handle inode mismatch"):
        BUILDER._enter_persistent_namespace(Path("/control"))
    assert closed == [91]
    assert called == []


def test_enter_namespace_pins_exact_keeper_and_attests_after_setns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    baseline = {"inventory_sha256": "a" * 64}
    keeper = {"pid": 777, "mount_namespace_inode": 4242, "mount_namespace_st_dev": 17}
    active = {"keeper": keeper, "external_inventory_after": baseline}
    monkeypatch.setattr(BUILDER, "_validate_active_namespace", lambda path: (active, {}))
    monkeypatch.setattr(BUILDER, "_capture_external_fixed_point", lambda **kwargs: baseline)
    monkeypatch.setattr(BUILDER.os, "readlink", lambda path: "mnt:[4242]")
    monkeypatch.setattr(BUILDER.os, "open", lambda *args: 91)
    monkeypatch.setattr(
        BUILDER.os, "fstat", lambda descriptor: types.SimpleNamespace(st_ino=4242, st_dev=17)
    )
    monkeypatch.setattr(BUILDER, "_keeper_live_identity", lambda pid: keeper)
    calls: list[tuple[int, int]] = []
    monkeypatch.setattr(BUILDER.os, "setns", lambda fd, kind: calls.append((fd, kind)))
    monkeypatch.setattr(BUILDER.os, "close", lambda descriptor: None)
    monkeypatch.setattr(BUILDER, "_mountinfo_bytes", lambda path: _private_mountinfo())
    monkeypatch.setattr(BUILDER, "_root_private_attestation", lambda payload: {})
    result = BUILDER._enter_persistent_namespace(Path("/control"))
    assert result is active
    assert calls == [(91, BUILDER.CLONE_NEWNS)]
    assert BUILDER._EXECUTION_NAMESPACE_INODE == 4242


def test_existing_start_intent_without_registration_fails_closed_without_second_spawn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    control = tmp_path / "control"
    control.mkdir()
    intent_path = control / BUILDER.NAMESPACE_INTENT_NAME
    intent_path.write_text("existing", encoding="ascii")
    metadata = {"plan": {"sha256": "p" * 64}}
    source_identity = {"path": str(SCRIPT)}
    intent = BUILDER._namespace_document(
        "intent_before_unshare",
        {
            "attempt_id": "old",
            "campaign_metadata": metadata,
            "project_root": str(BUILDER.PROJECT_ROOT),
            "control_parent": str(control),
            "external_inventory_before": {},
            "runtime_guard_builder_source": source_identity,
            "runtime_guard_builder_control_copy": source_identity,
        },
    )
    monkeypatch.setattr(BUILDER.os, "geteuid", lambda: 0)
    monkeypatch.setattr(BUILDER, "_validate_control_parent", lambda path, create: path)
    monkeypatch.setattr(BUILDER, "_capture_external_fixed_point", lambda **kwargs: {})
    monkeypatch.setattr(BUILDER, "_regular_file_identity", lambda path, **kwargs: source_identity)
    monkeypatch.setattr(BUILDER, "_publish_namespace_claim", lambda path, document: False)
    monkeypatch.setattr(BUILDER, "_load_namespace_document", lambda path, status: (intent, {}))
    monkeypatch.setattr(BUILDER, "NAMESPACE_STARTUP_POLL_LIMIT", 1)
    monkeypatch.setattr(BUILDER.time, "sleep", lambda value: None)
    spawned: list[int] = []
    with pytest.raises(RuntimeError, match="did not publish registration"):
        BUILDER.start_persistent_namespace(
            control, metadata, spawn=lambda *args: spawned.append(1) or 123
        )
    assert spawned == []
    assert (control / BUILDER.NAMESPACE_ABANDONED_NAME).exists()


def test_keeper_failure_receipt_is_published_after_inherited_fd_close(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    events: list[object] = []
    monkeypatch.setattr(BUILDER.os, "setsid", lambda: events.append("setsid"))
    monkeypatch.setattr(BUILDER.signal, "signal", lambda *args: events.append("sighup"))
    monkeypatch.setattr(BUILDER.os, "open", lambda *args, **kwargs: 9)
    monkeypatch.setattr(
        BUILDER.os, "dup2", lambda source, target, **kwargs: events.append(("dup2", source, target))
    )
    monkeypatch.setattr(BUILDER.os, "close", lambda fd: events.append(("close-one", fd)))
    monkeypatch.setattr(BUILDER.os, "sysconf", lambda name: 256)
    monkeypatch.setattr(
        BUILDER.os, "closerange", lambda first, last: events.append(("close", first, last))
    )
    monkeypatch.setattr(
        BUILDER,
        "_initialize_keeper_namespace",
        lambda *args: (_ for _ in ()).throw(RuntimeError("unshare refused")),
    )
    monkeypatch.setattr(BUILDER.os, "getpid", lambda: 321)
    monkeypatch.setattr(
        BUILDER,
        "_publish_contract_recoverable",
        lambda path, document: events.append(("failure", path.name, document)),
    )
    monkeypatch.setattr(
        BUILDER.os,
        "_exit",
        lambda code: (_ for _ in ()).throw(SystemExit(code)),
    )
    intent = {"attempt_id": "a", BUILDER.NAMESPACE_HASH_FIELD: "h" * 64}
    with pytest.raises(SystemExit) as stopped:
        BUILDER._keeper_child(tmp_path, intent)
    assert stopped.value.code == 125
    assert ("close", 3, 256) in events
    failure = next(item for item in events if isinstance(item, tuple) and item[0] == "failure")
    assert failure[0:2] == ("failure", BUILDER.NAMESPACE_KEEPER_FAILURE_NAME)
    assert failure[2]["error"] == "unshare refused"
    assert failure[2]["status"] == "keeper_startup_failed_before_active_contract"


def test_pidfd_rejects_recycled_process_before_signal(tmp_path: Path) -> None:
    path = tmp_path / "self/fdinfo"
    path.mkdir(parents=True)
    (path / "41").write_text("pos:\t0\nPid:\t-1\n", encoding="ascii")
    with pytest.raises(ValueError, match="pidfd is stale"):
        BUILDER._validate_pidfd(41, 9001, proc_root=tmp_path)
    (path / "41").write_text("pos:\t0\nPid:\t9001\n", encoding="ascii")
    BUILDER._validate_pidfd(41, 9001, proc_root=tmp_path)


def test_stop_uses_pidfd_and_never_path_pid_signal() -> None:
    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    function = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "stop_persistent_namespace"
    )
    source = ast.unparse(function)
    assert "os.pidfd_open" in source
    assert "signal.pidfd_send_signal" in source
    assert "os.kill" not in source


def test_stop_rejects_foreign_process_or_fd_holder_of_execution_namespace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    keeper = {"pid": 10, "starttime_ticks": 7, "mount_namespace_inode": 4242}
    foreign = {
        "processes": [
            {"pid": 10, "starttime_ticks": 7},
            {"pid": 11, "starttime_ticks": 9},
        ],
        "namespace_fd_handles": [{"pid": 12, "fd": 4, "starttime_ticks": 3}],
    }
    monkeypatch.setattr(BUILDER, "_namespace_holders", lambda inode: foreign)
    with pytest.raises(ValueError, match="foreign processes or stale"):
        BUILDER._assert_keeper_is_sole_namespace_holder(keeper)


def test_stop_retry_infers_terminated_keeper_only_after_durable_intent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    control = tmp_path / "control"
    control.mkdir()
    paths = BUILDER._namespace_contract_paths(control)
    paths["active"].write_text("active", encoding="ascii")
    paths["stop_intent"].write_text("intent", encoding="ascii")
    metadata = {"plan": {"sha256": "p" * 64}}
    keeper = {"pid": 777, "starttime_ticks": 3, "mount_namespace_inode": 4242}
    active_identity = {"path": str(paths["active"]), "sha256": "a" * 64}
    active = {"campaign_metadata": metadata, "keeper": keeper}
    baseline = {"inventory_sha256": "b" * 64}
    stop_intent = {
        "campaign_metadata": metadata,
        "active_contract": active_identity,
        "keeper": keeper,
        "project_mount_count": 0,
        "namespace_fd_handle_count": 0,
        "external_inventory_before": baseline,
        "results_export_attestation": {"path": "/control/export.json", "sha256": "e" * 64},
    }
    monkeypatch.setattr(BUILDER.os, "geteuid", lambda: 0)
    monkeypatch.setattr(
        BUILDER,
        "_enter_persistent_namespace",
        lambda *_args: (_ for _ in ()).throw(
            AssertionError("dead-keeper recovery must remain in the host namespace")
        ),
    )
    monkeypatch.setattr(
        BUILDER,
        "_assert_execution_namespace",
        lambda: (_ for _ in ()).throw(
            AssertionError("dead-keeper recovery cannot attest a vanished namespace")
        ),
    )
    monkeypatch.setattr(
        BUILDER, "_validate_results_export_attestation",
        lambda *a, **k: stop_intent["results_export_attestation"],
    )
    monkeypatch.setattr(BUILDER, "_validate_control_parent", lambda path, create: path)
    monkeypatch.setattr(
        BUILDER,
        "_load_namespace_document",
        lambda path, status: (active, active_identity)
        if path.name == BUILDER.NAMESPACE_ACTIVE_NAME
        else (stop_intent, {"path": str(path)}),
    )
    monkeypatch.setattr(
        BUILDER,
        "_keeper_live_identity",
        lambda pid: (_ for _ in ()).throw(FileNotFoundError()),
    )
    monkeypatch.setattr(BUILDER, "_capture_external_fixed_point", lambda **kwargs: baseline)
    monkeypatch.setattr(
        BUILDER,
        "_regular_file_identity",
        lambda path, **kwargs: {"path": str(path), "sha256": "i" * 64},
    )
    published: list[dict[str, object]] = []
    monkeypatch.setattr(
        BUILDER,
        "_publish_contract_recoverable",
        lambda path, document: published.append(dict(document)),
    )
    monkeypatch.setattr(
        BUILDER.os,
        "pidfd_open",
        lambda *args: (_ for _ in ()).throw(AssertionError("must not signal")),
    )
    result = BUILDER.stop_persistent_namespace(control, metadata)
    assert result["termination_inferred_after_durable_stop_intent"] is True
    assert published == [result]


def test_stop_dead_keeper_without_durable_intent_refuses_before_setns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    control = tmp_path / "control"
    control.mkdir()
    paths = BUILDER._namespace_contract_paths(control)
    paths["active"].write_text("active", encoding="ascii")
    metadata = {"plan": {"sha256": "p" * 64}}
    keeper = {"pid": 778, "starttime_ticks": 4, "mount_namespace_inode": 4243}
    active = {"campaign_metadata": metadata, "keeper": keeper}
    monkeypatch.setattr(BUILDER.os, "geteuid", lambda: 0)
    monkeypatch.setattr(BUILDER, "_validate_control_parent", lambda path, create: path)
    monkeypatch.setattr(
        BUILDER, "_load_namespace_document",
        lambda path, status: (active, {"path": str(path), "sha256": "a" * 64}),
    )
    monkeypatch.setattr(
        BUILDER, "_keeper_live_identity",
        lambda _pid: (_ for _ in ()).throw(FileNotFoundError()),
    )
    monkeypatch.setattr(
        BUILDER, "_validate_results_export_attestation",
        lambda *_args, **_kwargs: {"path": "/control/export", "sha256": "e" * 64},
    )
    monkeypatch.setattr(
        BUILDER, "_enter_persistent_namespace",
        lambda *_args: (_ for _ in ()).throw(AssertionError("must not setns")),
    )
    with pytest.raises(ValueError, match="without a durable exact stop intent"):
        BUILDER.stop_persistent_namespace(control, metadata)


def test_stop_dead_keeper_rejects_external_inventory_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    control = tmp_path / "control"
    control.mkdir()
    paths = BUILDER._namespace_contract_paths(control)
    paths["active"].write_text("active", encoding="ascii")
    paths["stop_intent"].write_text("intent", encoding="ascii")
    metadata = {"plan": {"sha256": "p" * 64}}
    keeper = {"pid": 779, "starttime_ticks": 5, "mount_namespace_inode": 4244}
    active_identity = {"path": str(paths["active"]), "sha256": "a" * 64}
    export = {"path": "/control/export", "sha256": "e" * 64}
    active = {"campaign_metadata": metadata, "keeper": keeper}
    intent = {
        "campaign_metadata": metadata, "active_contract": active_identity,
        "keeper": keeper, "project_mount_count": 0,
        "namespace_fd_handle_count": 0,
        "external_inventory_before": {"inventory_sha256": "before"},
        "results_export_attestation": export,
    }
    monkeypatch.setattr(BUILDER.os, "geteuid", lambda: 0)
    monkeypatch.setattr(BUILDER, "_validate_control_parent", lambda path, create: path)
    monkeypatch.setattr(
        BUILDER, "_load_namespace_document",
        lambda path, status: (active, active_identity)
        if path == paths["active"] else (intent, {"path": str(path)}),
    )
    monkeypatch.setattr(
        BUILDER, "_keeper_live_identity",
        lambda _pid: {**keeper, "starttime_ticks": 999},
    )
    monkeypatch.setattr(
        BUILDER, "_validate_results_export_attestation",
        lambda *_args, **_kwargs: export,
    )
    monkeypatch.setattr(
        BUILDER, "_capture_external_fixed_point",
        lambda **_kwargs: {"inventory_sha256": "after"},
    )
    with pytest.raises(ValueError, match="external namespace drift"):
        BUILDER.stop_persistent_namespace(control, metadata)


def test_results_export_retry_returns_existing_exact_pass_without_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    control = tmp_path / "control"
    output = tmp_path / "results"
    control.mkdir()
    output.mkdir()
    attestation_path = control / BUILDER.RESULTS_EXPORT_ATTESTATION_NAME
    attestation_path.write_text("pass", encoding="ascii")
    source_mount = {"path": str(output), "mount_record": {}}
    capacity_contract = {"schema_version": "test-capacity"}
    campaign_identity = {"path": str(control / "campaign"), "size_bytes": 1, "sha256": "a" * 64}
    evaluator_identity = {"path": str(control / "evaluator"), "size_bytes": 1, "sha256": "b" * 64}
    identities = {
        "campaign_role_output_attestation": {"path": str(control / "ca"), "size_bytes": 1, "sha256": "c" * 64},
        "campaign_role_output": {"path": str(output / "run/unit-manifest-amended.json"), "size_bytes": 1, "sha256": "d" * 64},
        "evaluator_role_output_attestation": {"path": str(control / "ea"), "size_bytes": 1, "sha256": "e" * 64},
        "evaluator_role_output": {"path": str(output / "run/amended-evaluation-v3.json"), "size_bytes": 1, "sha256": "f" * 64},
    }
    campaign = {
        "role_output_root": str(output / "run"),
        "results_authority_mount": source_mount,
        "role_output_attestation": identities["campaign_role_output_attestation"],
        "role_output": identities["campaign_role_output"],
        "capacity_contract": capacity_contract,
    }
    evaluator = {
        "campaign_completion": campaign_identity,
        "role_output_root": str(output / "run"),
        "results_authority_mount": source_mount,
        "role_output_attestation": identities["evaluator_role_output_attestation"],
        "role_output": identities["evaluator_role_output"],
        "capacity_contract": capacity_contract,
    }
    existing = {
        "campaign_completion": campaign_identity,
        "evaluator_completion": evaluator_identity,
        "source_mount": source_mount,
        "capacity_contract": capacity_contract,
        **identities,
    }
    monkeypatch.setattr(BUILDER.os, "geteuid", lambda: 0)
    monkeypatch.setattr(BUILDER, "_contract_metadata", lambda _context: {"frozen": True})
    monkeypatch.setattr(BUILDER, "_assert_execution_namespace", lambda: None)
    monkeypatch.setattr(
        BUILDER, "_validate_active_namespace",
        lambda _control: ({"keeper": {"pid": 1}}, {}),
    )
    monkeypatch.setattr(
        BUILDER, "_assert_keeper_is_sole_namespace_holder", lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(BUILDER, "_results_authority_identity", lambda _path: source_mount)
    monkeypatch.setattr(BUILDER, "_capacity_contract", lambda _context: capacity_contract)
    monkeypatch.setattr(
        BUILDER, "_validate_successful_role_completion",
        lambda _path, _sha, *, role, control_parent: (
            (campaign, campaign_identity) if role == "campaign" else (evaluator, evaluator_identity)
        ),
    )
    expected_export = {"path": str(attestation_path), "size_bytes": 4, "sha256": "9" * 64}
    monkeypatch.setattr(
        BUILDER, "_validate_results_export_attestation",
        lambda *_args, **_kwargs: expected_export,
    )
    monkeypatch.setattr(
        BUILDER, "_load_repair_selfhashed", lambda *_args, **_kwargs: (existing, expected_export),
    )
    monkeypatch.setattr(
        BUILDER.shutil, "copytree",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not recopy")),
    )
    assert BUILDER.export_results(
        context={}, control_parent=control, output_parent=output,
        campaign_completion_path=control / "campaign",
        expected_campaign_completion_sha256="a" * 64,
        evaluator_completion_path=control / "evaluator",
        expected_evaluator_completion_sha256="b" * 64,
    ) == {"status": "PASS", "results_export_attestation": expected_export}


def test_results_authority_rejects_nested_mount_before_export(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "results"
    nested = output / "foreign"
    nested.mkdir(parents=True)
    monkeypatch.setattr(
        BUILDER, "_validate_external_output_parent", lambda path, create: Path(path)
    )
    monkeypatch.setattr(
        BUILDER, "_parse_mountinfo",
        lambda _text: [
            {"mount_point": str(output)},
            {"mount_point": str(nested)},
        ],
    )
    with pytest.raises(ValueError, match="ambiguous"):
        BUILDER._results_authority_identity(output)


def test_stop_cli_dead_keeper_recovery_never_loads_host_project_metadata(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    observed: list[object] = []
    monkeypatch.setattr(BUILDER, "_require_exact_project_root", lambda path: None)
    monkeypatch.setattr(
        BUILDER, "_validate_control_parent", lambda path, create: BUILDER.DEFAULT_CONTROL_PARENT
    )
    monkeypatch.setattr(
        BUILDER, "_validate_control_copy",
        lambda parent, digest, *, require_source: observed.append(require_source) or {},
    )
    monkeypatch.setattr(BUILDER, "_require_static_root_interpreter", lambda: None)
    monkeypatch.setattr(
        BUILDER, "_static_metadata_context",
        lambda **kwargs: (_ for _ in ()).throw(
            AssertionError("dead-keeper recovery must not load host project metadata")
        ),
    )
    monkeypatch.setattr(
        BUILDER, "stop_persistent_namespace",
        lambda parent: {"status": "stopped_after_empty_namespace_attestation"},
    )
    missing = "/host-mutable/missing.json"
    arguments = [
        "stop-persistent-namespace",
        "--execution-plan", missing,
        "--expected-execution-plan-sha256", "1" * 64,
        "--acquisition-manifest", missing,
        "--expected-acquisition-manifest-sha256", "2" * 64,
        "--source-manifest", missing,
        "--expected-source-manifest-sha256", "3" * 64,
        "--amendment", missing,
        "--expected-amendment-sha256", "4" * 64,
        "--project-root", str(BUILDER.PROJECT_ROOT),
        "--control-parent", str(BUILDER.DEFAULT_CONTROL_PARENT),
        "--external-output-parent", str(BUILDER.DEFAULT_RESULTS_PARENT),
        "--expected-runtime-guard-builder-sha256", "5" * 64,
    ]
    assert BUILDER.main(arguments) == 0
    assert observed == [False]
    assert "stopped_after_empty_namespace_attestation" in capsys.readouterr().out


def test_external_inventory_tolerates_disappearing_unrelated_pid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    inodes = tuple(range(100, 110))
    monkeypatch.setattr(BUILDER, "EXPECTED_EXTERNAL_NAMESPACE_INODES", inodes)
    transient = tmp_path / "1"
    transient.mkdir()
    namespace_by_pid: dict[str, int] = {}
    for offset, inode in enumerate(inodes, start=10):
        pid = tmp_path / str(offset)
        pid.mkdir()
        namespace_by_pid[str(pid / "ns/mnt")] = inode
    monkeypatch.setattr(BUILDER, "_pid_starttime", lambda path: (
        (_ for _ in ()).throw(FileNotFoundError()) if path == transient else 5
    ))
    monkeypatch.setattr(
        BUILDER.os,
        "readlink",
        lambda path: f"mnt:[{namespace_by_pid[str(path)]}]",
    )
    monkeypatch.setattr(BUILDER, "_mountinfo_bytes", lambda path: _private_mountinfo())
    snapshot = BUILDER._capture_external_namespace_inventory(proc_root=tmp_path)
    assert len(snapshot["namespaces"]) == 10


def test_external_inventory_rejects_anonymous_stale_namespace_fd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    inodes = tuple(range(100, 110))
    monkeypatch.setattr(BUILDER, "EXPECTED_EXTERNAL_NAMESPACE_INODES", inodes)
    namespace_by_path: dict[str, str] = {}
    payload_paths: set[str] = set()
    for offset, inode in enumerate(inodes, start=10):
        pid = tmp_path / str(offset)
        (pid / "fd").mkdir(parents=True)
        namespace_by_path[str(pid / "ns/mnt")] = f"mnt:[{inode}]"
        payload_paths.add(str(pid / "mountinfo"))
    stale = tmp_path / "10/fd/7"
    stale.touch()
    namespace_by_path[str(stale)] = "mnt:[99999]"
    monkeypatch.setattr(BUILDER, "_pid_starttime", lambda path: 5)
    monkeypatch.setattr(BUILDER.os, "readlink", lambda path: namespace_by_path[str(path)])
    monkeypatch.setattr(BUILDER, "_mountinfo_bytes", lambda path: _private_mountinfo())
    with pytest.raises(ValueError, match="anonymous or stale"):
        BUILDER._capture_external_namespace_inventory(proc_root=tmp_path)


def test_cli_exposes_explicit_persistent_namespace_boundaries(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        BUILDER.main(["--help"])
    output = capsys.readouterr().out
    assert "start-persistent-namespace" in output
    assert "stop-persistent-namespace" in output
    assert "exec-campaign" in output
    assert "exec-evaluator" in output


def test_exec_evaluator_cli_requires_campaign_completion(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        BUILDER.main(["exec-evaluator", "--help"])
    output = capsys.readouterr().out
    assert "--campaign-completion" in output
    assert "--expected-campaign-completion-sha256" in output


def test_v3_control_and_results_parents_are_not_operator_selectable(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="frozen v3 results parent"):
        BUILDER._validate_external_output_parent(tmp_path / "results", create=False)
    with pytest.raises(ValueError, match="frozen root-only control"):
        BUILDER._validate_control_parent(tmp_path / "control", create=False)


def test_role_handoff_rejects_role_confusion_before_any_fork() -> None:
    with pytest.raises(ValueError, match="role is not exact"):
        BUILDER._run_role_handoff(
            role="campaign-as-evaluator",
            context={},
            control_parent=Path("/control"),
            output_parent=Path("/results"),
            role_output_root=Path("/results/output"),
            role_arguments=[],
            bindings={},
        )


def test_role_handoff_uses_no_shell_and_fixed_role_script_selection() -> None:
    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    function = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_run_role_handoff"
    )
    source = ast.unparse(function)
    assert "subprocess" not in source
    assert "shell" not in source.lower() or "shell_used" in source
    assert "LAUNCHER_PATH if role == 'campaign' else EVALUATOR_PATH" in source


def test_drop_privilege_order_closes_fds_and_verifies_zero_capabilities(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    events: list[object] = []
    executable = tmp_path / "python"
    executable.write_bytes(b"python")
    identity = {"path": str(executable), "size_bytes": 6, "sha256": "p" * 64}
    monkeypatch.setattr(BUILDER.os, "read", lambda fd, size: b"1")
    monkeypatch.setattr(BUILDER.os, "close", lambda fd: events.append(("close", fd)))
    monkeypatch.setattr(
        BUILDER, "_close_fds_except", lambda fds: events.append(("close_fds", set(fds)))
    )
    monkeypatch.setattr(BUILDER.os, "chdir", lambda path: events.append(("chdir", path)))
    monkeypatch.setattr(BUILDER.os, "umask", lambda mode: events.append(("umask", mode)) or 0)
    monkeypatch.setattr(
        BUILDER, "_assert_execution_namespace", lambda: events.append("namespace")
    )
    monkeypatch.setattr(BUILDER.os, "setgroups", lambda groups: events.append(("groups", groups)))
    monkeypatch.setattr(
        BUILDER, "_prctl", lambda option, argument=0: events.append(("prctl", option, argument)) or 0
    )
    monkeypatch.setattr(BUILDER.os, "setgid", lambda gid: events.append(("gid", gid)))
    monkeypatch.setattr(BUILDER.os, "setuid", lambda uid: events.append(("uid", uid)))
    monkeypatch.setattr(BUILDER, "_process_security_state", lambda: {
        "euid": 1000, "egid": 1000, "supplementary_gids": [],
        "cap_inheritable": 0, "cap_permitted": 0, "cap_effective": 0,
        "cap_ambient": 0, "no_new_privileges": 1, "dumpable": 0,
    })
    monkeypatch.setattr(
        BUILDER, "_regular_file_identity", lambda path, **_kwargs: identity
    )
    monkeypatch.setattr(BUILDER, "_verify_pinned_script_fd", lambda fd, expected: expected)
    monkeypatch.setattr(
        BUILDER,
        "_write_all",
        lambda fd, payload: events.append(("diagnostic", json.loads(payload))),
    )
    monkeypatch.setattr(
        BUILDER.os,
        "_exit",
        lambda code: (_ for _ in ()).throw(SystemExit(code)),
    )
    def execve(path, argv, env):
        events.append(("execve", path, tuple(argv), dict(env)))
        return None
    with pytest.raises(SystemExit):
        BUILDER._drop_privileges_and_exec_role(
            gate_fd=7,
            error_fd=8,
            ack_fd=9,
            cwd=tmp_path,
            executable=executable,
            executable_identity=identity,
            role_script=executable,
            role_script_identity=identity,
            role_script_fd=10,
            argv=[str(executable), "-I", "/role.py"],
            environment={"PATH": "/usr/bin:/bin"},
            execve=execve,
        )
    order = [event[0] if isinstance(event, tuple) else event for event in events]
    assert order.index("close_fds") < order.index("groups") < order.index("gid") < order.index("uid") < order.index("execve")
    assert ("close_fds", {0, 1, 2, 8, 9, 10}) in events
    assert ("prctl", BUILDER.PR_SET_NO_NEW_PRIVS, 1) in events
    assert ("prctl", BUILDER.PR_SET_DUMPABLE, 0) in events


def test_privilege_or_capability_failure_never_reaches_exec(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable = tmp_path / "python"
    executable.write_bytes(b"python")
    identity = {"path": str(executable)}
    monkeypatch.setattr(BUILDER.os, "read", lambda fd, size: b"1")
    monkeypatch.setattr(BUILDER.os, "close", lambda fd: None)
    monkeypatch.setattr(BUILDER, "_close_fds_except", lambda fds: None)
    monkeypatch.setattr(BUILDER.os, "chdir", lambda path: None)
    monkeypatch.setattr(BUILDER.os, "umask", lambda mode: 0)
    monkeypatch.setattr(BUILDER, "_assert_execution_namespace", lambda: None)
    monkeypatch.setattr(BUILDER.os, "setgroups", lambda groups: None)
    monkeypatch.setattr(BUILDER, "_prctl", lambda *args: 0)
    monkeypatch.setattr(BUILDER.os, "setgid", lambda gid: None)
    monkeypatch.setattr(BUILDER.os, "setuid", lambda uid: None)
    monkeypatch.setattr(BUILDER, "_verify_pinned_script_fd", lambda fd, expected: expected)
    monkeypatch.setattr(BUILDER, "_process_security_state", lambda: {
        "euid": 1000, "egid": 1000, "supplementary_gids": [],
        "cap_inheritable": 0, "cap_permitted": 1, "cap_effective": 0,
        "cap_ambient": 0, "no_new_privileges": 1, "dumpable": 0,
    })
    diagnostics: list[dict[str, object]] = []
    monkeypatch.setattr(
        BUILDER,
        "_write_all",
        lambda fd, payload: diagnostics.append(json.loads(payload)),
    )
    monkeypatch.setattr(
        BUILDER.os,
        "_exit",
        lambda code: (_ for _ in ()).throw(SystemExit(code)),
    )
    with pytest.raises(SystemExit):
        BUILDER._drop_privileges_and_exec_role(
            gate_fd=7, error_fd=8, ack_fd=9, cwd=tmp_path, executable=executable,
            executable_identity=identity, role_script=executable,
            role_script_identity=identity, role_script_fd=10,
            argv=[str(executable)], environment={},
            execve=lambda *args: (_ for _ in ()).throw(AssertionError("exec reached")),
        )
    assert diagnostics and "retained privilege" in diagnostics[0]["error"]


def _install_role_parent_harness(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    read_blocks: list[bytes] | None = None,
    wait_results: list[object] | None = None,
    publish_error_at: int | None = None,
) -> tuple[dict[str, object], list[dict[str, object]], list[int], list[object]]:
    candidate = tmp_path / "python"
    script = tmp_path / "launcher.py"
    candidate.write_bytes(b"python")
    script.write_bytes(b"launcher")
    candidate_identity = {"path": str(candidate), "size_bytes": 6, "sha256": "a" * 64}
    script_identity = {"path": str(script), "size_bytes": 8, "sha256": "b" * 64}
    bindings: dict[str, object] = {
        "candidate_interpreter": candidate_identity,
        "launcher": script_identity,
        "evaluator": script_identity,
        "persistent_mount_namespace": {"path": "/control/active.json"},
        "runtime_guard": {"path": "/control/guard.json"},
        "evaluator_lock": {"path": "/control/lock.json"},
        "rfc3161_attestation": {"path": "/control/rfc.json"},
        "no_decoder_evidence": {"path": "/control/no-decoder.json"},
        "sealed_input_snapshot": {"status": "PASS"},
        "sealed_project_artifacts": {"status": "PASS"},
        "runtime_authority_mounts": {
            "candidate": {"source": {"path": "/candidate"}},
            "component": {"source": {"path": "/component"}},
        },
        "results_authority_mount": {"path": str(tmp_path), "mount_record": {}},
        "capacity_contract": {"schema_version": "test-capacity"},
    }
    monkeypatch.setattr(BUILDER, "LAUNCHER_PATH", script)
    monkeypatch.setattr(BUILDER, "EVALUATOR_PATH", script)
    monkeypatch.setattr(BUILDER, "_assert_execution_namespace", lambda: None)
    monkeypatch.setattr(
        BUILDER, "_closed_static_attestation", lambda *args: ({"path": "closed"}, {"path": "sealed"})
    )
    monkeypatch.setattr(
        BUILDER, "_candidate_interpreter_identity", lambda plan: (candidate, candidate_identity)
    )
    monkeypatch.setattr(
        BUILDER,
        "_regular_file_identity",
        lambda path, **kwargs: (
            script_identity if Path(path) == script else {"path": str(path), "sha256": "c" * 64}
        ),
    )
    monkeypatch.setattr(
        BUILDER, "_open_amendment_bound_script",
        lambda path, expected: (16, dict(expected)),
    )
    monkeypatch.setattr(BUILDER, "_contract_metadata", lambda context: {"plan": "exact"})
    monkeypatch.setattr(
        BUILDER,
        "_system_capacity_preflight",
        lambda context, **_kwargs: {"status": "PASS"},
    )
    monkeypatch.setattr(
        BUILDER, "_require_results_available", lambda *args, **kwargs: {"status": "PASS"},
    )
    monkeypatch.setattr(BUILDER, "_campaign_execution_identity", lambda: {"uid": 1000})
    monkeypatch.setattr(BUILDER, "_validate_sealed_inputs_post_role", lambda *a, **k: [])
    monkeypatch.setattr(
        BUILDER, "_sealed_project_artifact_snapshot",
        lambda *a, **k: bindings["sealed_project_artifacts"],
    )
    monkeypatch.setattr(
        BUILDER, "_validate_runtime_authority_mounts",
        lambda *a, **k: bindings["runtime_authority_mounts"],
    )
    monkeypatch.setattr(
        BUILDER, "_results_authority_identity",
        lambda *a, **k: bindings["results_authority_mount"],
    )
    monkeypatch.setattr(BUILDER.secrets, "token_hex", lambda size: "1" * 64)
    monkeypatch.setattr(
        BUILDER,
        "_root_control_artifact_identity",
        lambda path, control: {"path": str(path), "size_bytes": 1, "sha256": "d" * 64},
    )
    monkeypatch.setattr(
        BUILDER,
        "_attest_role_output",
        lambda **kwargs: (
            {"role_output": {"path": "/results/unit-manifest-amended.json", "size_bytes": 1, "sha256": "e" * 64}},
            {"path": "/control/attestation.json", "size_bytes": 1, "sha256": "f" * 64},
        ),
    )
    pipe_pairs = iter(((10, 11), (12, 13), (14, 15)))
    monkeypatch.setattr(BUILDER.os, "pipe2", lambda flags: next(pipe_pairs))
    monkeypatch.setattr(BUILDER.os, "fork", lambda: 222)
    closed: list[int] = []
    monkeypatch.setattr(BUILDER.os, "close", lambda fd: closed.append(fd))
    monkeypatch.setattr(BUILDER.os, "write", lambda fd, payload: len(payload))
    monkeypatch.setattr(BUILDER, "_role_ack_payload", lambda *args: b"ACK")
    default_blocks = [b"", b"ACK", b""]
    blocks = iter(read_blocks if read_blocks is not None else default_blocks)
    monkeypatch.setattr(BUILDER.os, "read", lambda fd, size: next(blocks))
    waits = iter(wait_results if wait_results is not None else [(222, 0)])
    wait_events: list[object] = []

    def waitpid(pid: int, options: int):
        wait_events.append((pid, options))
        value = next(waits)
        if isinstance(value, BaseException):
            raise value
        return value

    monkeypatch.setattr(BUILDER.os, "waitpid", waitpid)
    monkeypatch.setattr(BUILDER.os, "pidfd_open", lambda pid, flags: 90)
    monkeypatch.setattr(BUILDER, "_validate_pidfd", lambda fd, pid: None)
    monkeypatch.setattr(BUILDER.signal, "pidfd_send_signal", lambda fd, sig: wait_events.append((fd, sig)))
    published: list[dict[str, object]] = []

    def publish(path: Path, document: dict[str, object]) -> None:
        published.append(document)
        if publish_error_at is not None and len(published) == publish_error_at:
            raise OSError("synthetic publication failure")

    monkeypatch.setattr(BUILDER, "_publish_contract_recoverable", publish)
    return bindings, published, closed, wait_events


def _run_campaign_parent_handoff(
    tmp_path: Path, bindings: dict[str, object]
) -> int:
    return BUILDER._run_role_handoff(
        role="campaign",
        context={"plan": {}},
        control_parent=tmp_path,
        output_parent=tmp_path,
        role_output_root=tmp_path / "campaign-output",
        role_arguments=["--fixed", "value"],
        bindings=bindings,
    )


def test_role_capacity_refusal_is_durable_and_precedes_fork(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    bindings, published, _closed, _waited = _install_role_parent_harness(
        monkeypatch, tmp_path
    )
    monkeypatch.setattr(
        BUILDER,
        "_open_amendment_bound_script",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("pinned script FD must not open before capacity gates")
        ),
    )
    monkeypatch.setattr(
        BUILDER,
        "_require_results_available",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            OSError(BUILDER.errno.ENOSPC, "synthetic reserve failure")
        ),
    )
    monkeypatch.setattr(
        BUILDER.os,
        "fork",
        lambda: (_ for _ in ()).throw(AssertionError("fork must not run")),
    )
    with pytest.raises(OSError) as error:
        _run_campaign_parent_handoff(tmp_path, bindings)
    assert error.value.errno == BUILDER.errno.ENOSPC
    assert published[-1]["status"] == "capacity_preflight_refused_before_fork"
    assert published[-1]["capacity_contract"] == bindings["capacity_contract"]


def test_role_parent_eof_publishes_success_completion_and_closes_all_fds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bindings, published, closed, waits = _install_role_parent_harness(monkeypatch, tmp_path)
    assert _run_campaign_parent_handoff(tmp_path, bindings) == 0
    assert [item["schema_version"] for item in published] == [
        BUILDER.ROLE_HANDOFF_SCHEMA,
        BUILDER.ROLE_COMPLETION_SCHEMA,
    ]
    assert published[-1]["status"] == "exec_reached_role_exited_zero"
    assert sorted(closed) == [10, 11, 12, 13, 14, 15, 16]
    assert waits == [(222, 0)]


def test_role_parent_emits_machine_readable_completion_and_attestation_identities(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    bindings, _published, _closed, _waits = _install_role_parent_harness(
        monkeypatch, tmp_path
    )
    assert _run_campaign_parent_handoff(tmp_path, bindings) == 0
    result = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert result["schema_version"] == "blind-phase-confirmatory-role-execution-result-v3"
    assert result["status"] == "complete"
    assert result["role"] == "campaign"
    assert result["completion"]["sha256"] == "d" * 64
    assert result["role_output_attestation"]["sha256"] == "f" * 64
    unhashed = dict(result)
    payload_hash = unhashed.pop("result_payload_sha256")
    assert payload_hash == BUILDER._sha256_document(unhashed)


def test_role_parent_eof_without_positive_ack_is_never_exec_reached(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bindings, published, closed, waits = _install_role_parent_harness(
        monkeypatch, tmp_path, read_blocks=[b"", b""], wait_results=[(222, 9)]
    )
    with pytest.raises(RuntimeError, match="acknowledgment is absent"):
        _run_campaign_parent_handoff(tmp_path, bindings)
    assert published[-1]["status"] == "post_exec_role_acknowledgment_missing_or_invalid"
    assert not any(
        item.get("status", "").startswith("exec_reached") for item in published
    )
    assert sorted(closed) == [10, 11, 12, 13, 14, 15, 16]
    assert waits == [(222, 0)]


def test_role_parent_nonzero_program_has_distinct_completion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bindings, published, _closed, _waits = _install_role_parent_harness(
        monkeypatch, tmp_path, wait_results=[(222, 7 << 8)]
    )
    assert _run_campaign_parent_handoff(tmp_path, bindings) == 7
    assert published[-1]["status"] == "exec_reached_role_exited_nonzero"
    assert published[-1]["return_code"] == 7


def test_role_parent_exec_return_diagnostic_publishes_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    diagnostic = json.dumps({"error_type": "RuntimeError", "error": "exec returned"}).encode()
    bindings, published, _closed, _waits = _install_role_parent_harness(
        monkeypatch, tmp_path, read_blocks=[diagnostic, b"", b""], wait_results=[(222, 126 << 8)]
    )
    with pytest.raises(RuntimeError, match="immutable receipt"):
        _run_campaign_parent_handoff(tmp_path, bindings)
    assert published[-1]["schema_version"] == BUILDER.ROLE_FAILURE_SCHEMA
    assert published[-1]["diagnostic_utf8"] == diagnostic.decode()


def test_role_parent_publication_failure_kills_reaps_and_closes_child_fds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bindings, _published, closed, waits = _install_role_parent_harness(
        monkeypatch, tmp_path, publish_error_at=1, wait_results=[(222, 9)]
    )
    with pytest.raises(OSError, match="publication failure"):
        _run_campaign_parent_handoff(tmp_path, bindings)
    assert sorted(closed) == [10, 11, 12, 13, 14, 15, 16, 90]
    assert waits[0] == (90, BUILDER.signal.SIGKILL)
    assert waits[-1] == (222, 0)


def test_role_parent_oversized_diagnostic_kills_and_reaps(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bindings, _published, closed, waits = _install_role_parent_harness(
        monkeypatch, tmp_path, read_blocks=[b"x" * (64 * 1024 + 1)], wait_results=[(222, 9)]
    )
    with pytest.raises(RuntimeError, match="exceeded bound"):
        _run_campaign_parent_handoff(tmp_path, bindings)
    assert sorted(closed) == [10, 11, 12, 13, 14, 15, 16, 90]
    assert waits[-1] == (222, 0)


def test_role_parent_wait_failure_kills_and_reaps_without_fd_leak(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bindings, _published, closed, waits = _install_role_parent_harness(
        monkeypatch, tmp_path,
        wait_results=[OSError("synthetic wait failure"), (222, 9)],
    )
    with pytest.raises(OSError, match="wait failure"):
        _run_campaign_parent_handoff(tmp_path, bindings)
    assert sorted(closed) == [10, 11, 12, 13, 14, 15, 16, 90]
    assert waits[-1] == (222, 0)


def test_role_parent_completion_publication_failure_records_failure_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bindings, published, closed, waits = _install_role_parent_harness(
        monkeypatch, tmp_path, publish_error_at=2
    )
    with pytest.raises(OSError, match="publication failure"):
        _run_campaign_parent_handoff(tmp_path, bindings)
    assert published[1]["schema_version"] == BUILDER.ROLE_COMPLETION_SCHEMA
    assert published[2]["schema_version"] == BUILDER.ROLE_FAILURE_SCHEMA
    assert published[2]["status"] == "parent_failed_before_verified_exec_completion"
    assert sorted(closed) == [10, 11, 12, 13, 14, 15, 16]
    assert waits == [(222, 0)]


def test_role_child_rechecks_script_identity_immediately_before_exec(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable = tmp_path / "python"
    script = tmp_path / "role.py"
    executable.write_bytes(b"python")
    script.write_bytes(b"role")
    executable_identity = {"path": str(executable), "sha256": "a" * 64}
    script_identity = {"path": str(script), "sha256": "b" * 64}
    monkeypatch.setattr(BUILDER.os, "read", lambda fd, size: b"1")
    monkeypatch.setattr(BUILDER.os, "close", lambda fd: None)
    monkeypatch.setattr(BUILDER, "_close_fds_except", lambda fds: None)
    monkeypatch.setattr(BUILDER.os, "chdir", lambda path: None)
    monkeypatch.setattr(BUILDER.os, "umask", lambda mode: 0)
    monkeypatch.setattr(BUILDER, "_assert_execution_namespace", lambda: None)
    monkeypatch.setattr(BUILDER.os, "setgroups", lambda groups: None)
    monkeypatch.setattr(BUILDER, "_prctl", lambda *args: 0)
    monkeypatch.setattr(BUILDER.os, "setgid", lambda gid: None)
    monkeypatch.setattr(BUILDER.os, "setuid", lambda uid: None)
    monkeypatch.setattr(
        BUILDER,
        "_process_security_state",
        lambda: {
            "euid": 1000, "egid": 1000, "supplementary_gids": [],
            "cap_inheritable": 0, "cap_permitted": 0, "cap_effective": 0,
            "cap_ambient": 0, "no_new_privileges": 1, "dumpable": 0,
        },
    )
    monkeypatch.setattr(
        BUILDER,
        "_regular_file_identity",
        lambda path, **kwargs: (
            executable_identity if Path(path) == executable
            else {**script_identity, "sha256": "f" * 64}
        ),
    )
    monkeypatch.setattr(
        BUILDER, "_verify_pinned_script_fd",
        lambda fd, expected: (_ for _ in ()).throw(
            ValueError("pinned script FD identity changed before exec")
        ),
    )
    diagnostics: list[dict[str, object]] = []
    monkeypatch.setattr(
        BUILDER, "_write_all", lambda fd, value: diagnostics.append(json.loads(value))
    )
    monkeypatch.setattr(
        BUILDER.os, "_exit", lambda code: (_ for _ in ()).throw(SystemExit(code))
    )
    with pytest.raises(SystemExit):
        BUILDER._drop_privileges_and_exec_role(
            gate_fd=7, error_fd=8, ack_fd=9, cwd=tmp_path,
            executable=executable, executable_identity=executable_identity,
            role_script=script, role_script_identity=script_identity,
            role_script_fd=10,
            argv=[str(executable), "-I", str(script)], environment={},
            execve=lambda *args: (_ for _ in ()).throw(AssertionError("exec reached")),
        )
    assert diagnostics and "script FD identity changed" in diagnostics[0]["error"]


def test_role_output_reader_uses_nofollow_dirfd_chain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    parent = tmp_path / "results"
    root = parent / "run"
    root.mkdir(parents=True)
    parent.chmod(0o700)
    root.chmod(0o700)
    document = {"schema_version": "unit", "status": "complete"}
    document["unit_manifest_payload_sha256"] = BUILDER._sha256_document(document)
    artifact = root / "unit-manifest-amended.json"
    artifact.write_text(json.dumps(document), encoding="utf-8")
    artifact.chmod(0o444)
    monkeypatch.setattr(BUILDER, "DEFAULT_RESULTS_PARENT", parent)
    loaded, identity, root_identity = BUILDER._read_role_output_via_dirfds(
        output_parent=parent, output_root=root,
        filename="unit-manifest-amended.json",
    )
    assert loaded == document
    assert identity["path"] == str(artifact)
    assert root_identity["st_ino"] == root.stat().st_ino
    moved = parent / "moved"
    root.rename(moved)
    root.symlink_to(moved, target_is_directory=True)
    with pytest.raises(OSError):
        BUILDER._read_role_output_via_dirfds(
            output_parent=parent, output_root=root,
            filename="unit-manifest-amended.json",
        )


def test_zero_exit_completion_binds_root_output_attestation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bindings, published, _closed, _waits = _install_role_parent_harness(
        monkeypatch, tmp_path
    )
    assert _run_campaign_parent_handoff(tmp_path, bindings) == 0
    completion = published[-1]
    assert completion["role_output_attestation"]["sha256"] == "f" * 64
    assert completion["role_output"]["sha256"] == "e" * 64
    assert completion["campaign_metadata"] == {"plan": "exact"}
    assert completion["persistent_mount_namespace"] == bindings[
        "persistent_mount_namespace"
    ]


def test_campaign_completion_transitively_binds_handoff_and_unit_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    control = tmp_path / "control"
    results = tmp_path / "results"
    output_root = results / "run"
    completion_path = control / "completion.json"
    handoff_path = control / "handoff.json"
    attestation_path = control / "attestation.json"
    metadata = {
        "plan": {"path": "/plan", "size_bytes": 1, "sha256": "1" * 64},
        "acquisition": {"path": "/acq", "size_bytes": 1, "sha256": "2" * 64},
        "source": {"path": "/source", "size_bytes": 1, "sha256": "3" * 64},
        "amendment": {"path": "/amend", "size_bytes": 1, "sha256": "4" * 64},
    }
    namespace = {"path": "/control/active", "size_bytes": 1, "sha256": "5" * 64}
    guard = {"path": "/control/guard", "size_bytes": 1, "sha256": "6" * 64}
    lock = {"path": "/control/lock", "size_bytes": 1, "sha256": "7" * 64}
    bindings = {
        "persistent_mount_namespace": namespace,
        "runtime_guard": guard,
        "evaluator_lock": lock,
        "rfc3161_attestation": {"path": "/control/rfc.json", "size_bytes": 1, "sha256": "d" * 64},
        "sealed_input_snapshot": {"status": "PASS"},
        "sealed_project_artifacts": {"status": "PASS"},
        "runtime_authority_mounts": {"candidate": {}, "component": {}},
        "results_authority_mount": {"path": str(results)},
        "capacity_contract": {"schema_version": "test-capacity"},
    }
    manifest = {
        "schema_version": BUILDER.AMENDED_UNIT_MANIFEST_SCHEMA,
        "status": "complete",
    }
    manifest["unit_manifest_payload_sha256"] = BUILDER._sha256_document(manifest)
    manifest_identity = {
        "path": str(output_root / "unit-manifest-amended.json"),
        "size_bytes": 12, "sha256": "8" * 64,
    }
    root_identity = {
        "path": str(output_root), "st_dev": 1, "st_ino": 2,
        "uid": 1000, "gid": 1000, "mode": 0o700,
    }
    attempt_id = "9" * 64
    handoff = {
        "schema_version": BUILDER.ROLE_HANDOFF_SCHEMA,
        "status": "authorized_before_uid_drop_and_exec",
        "role": "campaign", "attempt_id": attempt_id, "child_pid": 44,
        "campaign_metadata": metadata,
        "persistent_mount_namespace": namespace,
        "runtime_guard": guard, "evaluator_lock": lock,
        "rfc3161_attestation": bindings["rfc3161_attestation"],
        "sealed_input_snapshot": bindings["sealed_input_snapshot"],
        "sealed_project_artifacts": bindings["sealed_project_artifacts"],
        "runtime_authority_mounts": bindings["runtime_authority_mounts"],
        "results_authority_mount": bindings["results_authority_mount"],
        "capacity_contract": bindings["capacity_contract"],
        "role_output_root": str(output_root),
        "role_output_filename": "unit-manifest-amended.json",
        "campaign_completion": None,
    }
    handoff["handoff_payload_sha256"] = BUILDER._sha256_document(handoff)
    handoff_identity = {"path": str(handoff_path), "size_bytes": 20, "sha256": "a" * 64}
    attestation = {
        "schema_version": BUILDER.ROLE_OUTPUT_ATTESTATION_SCHEMA,
        "status": "root_attested_after_zero_exit", "role": "campaign",
        "attempt_id": attempt_id, "handoff": handoff_identity,
        "output_root": root_identity, "role_output": manifest_identity,
        "role_output_schema": manifest["schema_version"],
        "role_output_self_hash_field": "unit_manifest_payload_sha256",
        "role_output_payload_sha256": manifest["unit_manifest_payload_sha256"],
        "capacity_contract": bindings["capacity_contract"],
    }
    attestation["attestation_payload_sha256"] = BUILDER._sha256_document(attestation)
    attestation_identity = {"path": str(attestation_path), "size_bytes": 20, "sha256": "b" * 64}
    completion = {
        "schema_version": BUILDER.ROLE_COMPLETION_SCHEMA,
        "status": "exec_reached_role_exited_zero", "role": "campaign",
        "attempt_id": attempt_id, "handoff": handoff_identity,
        "child_pid": 44, "child_wait_status": 0, "return_code": 0,
        "exec_failure_diagnostic_bytes": 0,
        "post_exec_role_acknowledgment_verified": True,
        "role_acknowledgment_sha256": BUILDER.hashlib.sha256(
            BUILDER._role_ack_payload("campaign", attempt_id, handoff["handoff_payload_sha256"])
        ).hexdigest(),
        "role_output_attestation": attestation_identity,
        "role_output": manifest_identity, "campaign_metadata": metadata,
        "persistent_mount_namespace": namespace, "runtime_guard": guard,
        "evaluator_lock": lock,
        "rfc3161_attestation": bindings["rfc3161_attestation"],
        "sealed_input_snapshot": bindings["sealed_input_snapshot"],
        "sealed_project_artifacts": bindings["sealed_project_artifacts"],
        "runtime_authority_mounts": bindings["runtime_authority_mounts"],
        "results_authority_mount": bindings["results_authority_mount"],
        "capacity_contract": bindings["capacity_contract"],
        "campaign_completion": None,
        "role_output_root": str(output_root),
    }
    completion["completion_payload_sha256"] = BUILDER._sha256_document(completion)
    completion_identity = {"path": str(completion_path), "size_bytes": 20, "sha256": "c" * 64}
    documents = {
        completion_path: (completion, completion_identity),
        handoff_path: (handoff, handoff_identity),
        attestation_path: (attestation, attestation_identity),
    }
    monkeypatch.setattr(BUILDER, "_static_expected_json", lambda path, expected: documents[Path(path)])
    monkeypatch.setattr(
        BUILDER, "_root_control_artifact_identity",
        lambda path, parent: documents[Path(path)][1],
    )
    monkeypatch.setattr(
        BUILDER, "_read_role_output_via_dirfds",
        lambda **kwargs: (_ for _ in ()).throw(
            AssertionError("evaluator parent must not read campaign outcomes before ACK")
        ),
    )
    monkeypatch.setattr(BUILDER, "_contract_metadata", lambda context: metadata)
    assert BUILDER._validate_campaign_completion(
        path=completion_path, expected_sha256=completion_identity["sha256"],
        context={}, control_parent=control, output_parent=results,
        output_root=output_root, bindings=bindings,
    ) == completion_identity
    completion["return_code"] = 1
    with pytest.raises(ValueError, match="transitive contract"):
        BUILDER._validate_campaign_completion(
            path=completion_path, expected_sha256=completion_identity["sha256"],
            context={}, control_parent=control, output_parent=results,
            output_root=output_root, bindings=bindings,
        )


def _openssl_reply(imprint: str) -> str:
    octets = [imprint[index:index + 2] for index in range(0, 64, 2)]
    return (
        "Status: Granted.\nHash Algorithm: sha256\nMessage data:\n"
        f"    0000 - {' '.join(octets[:8])}-{' '.join(octets[8:16])}\n"
        f"    0010 - {' '.join(octets[16:24])}-{' '.join(octets[24:])}\n"
        "Serial number: 1\nTime stamp: Sep 06 00:00:00 2026 GMT\n"
    )


def test_rfc3161_pair_requires_query_data_granted_sha256_and_exact_imprint() -> None:
    identity = {"path": "/data", "size_bytes": 1, "sha256": "a" * 64}
    query = {"path": "/query", "size_bytes": 1, "sha256": "b" * 64}
    response = {"path": "/response", "size_bytes": 1, "sha256": "c" * 64}
    outputs = iter(("Verification: OK\n", "Verification: OK\n", _openssl_reply("a" * 64)))

    class Completed:
        returncode = 0
        stdout = ""

    def runner(*args, **kwargs):
        value = Completed()
        value.stdout = next(outputs)
        return value

    result = BUILDER._verify_timestamp_pair(
        label="test", data_path=Path("/data"), data_identity=identity,
        query_path=Path("/query"), query_identity=query,
        response_path=Path("/response"), response_identity=response,
        ca_certificate_path=Path("/ca.pem"),
        runner=runner,
    )
    assert result["message_imprint_sha256"] == "a" * 64
    outputs = iter(("Verification: OK\n", "Verification: OK\n", _openssl_reply("d" * 64)))
    with pytest.raises(ValueError, match="does not bind"):
        BUILDER._verify_timestamp_pair(
            label="test", data_path=Path("/data"), data_identity=identity,
            query_path=Path("/query"), query_identity=query,
            response_path=Path("/response"), response_identity=response,
            ca_certificate_path=Path("/ca.pem"),
            runner=runner,
        )


def test_rfc3161_attestation_rejects_stale_guard_binding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / BUILDER.RFC3161_ATTESTATION_NAME
    context = {
        "plan_identity": {"path": "/plan", "size_bytes": 1, "sha256": "1" * 64},
        "acquisition_identity": {"path": "/acq", "size_bytes": 1, "sha256": "2" * 64},
        "source_identity": {"path": "/source", "size_bytes": 1, "sha256": "3" * 64},
        "amendment_identity": {"path": "/amend", "size_bytes": 1, "sha256": "4" * 64},
    }
    monkeypatch.setattr(BUILDER, "_contract_metadata", lambda value: {"exact": True})
    guard = {"path": "/guard", "size_bytes": 1, "sha256": "5" * 64}
    lock = {"path": "/lock", "size_bytes": 1, "sha256": "6" * 64}
    freeze_attestation = {"path": "/freeze", "size_bytes": 1, "sha256": "8" * 64}
    timestamp = lambda data: {
        "status": "PASS", "data": data,
        "query": {"path": "/query", "size_bytes": 1, "sha256": "a" * 64},
        "response": {"path": "/response", "size_bytes": 1, "sha256": "b" * 64},
        "message_imprint_sha256": data["sha256"],
        "generation_time_utc": "2026-09-06T00:00:00Z",
        "query_verification": "OK", "data_verification": "OK",
    }
    document = {
        "schema_version": BUILDER.RFC3161_ATTESTATION_SCHEMA, "status": "PASS",
        "campaign_metadata": {"exact": True}, "amendment": context["amendment_identity"],
        "runtime_guard": guard, "evaluator_lock": lock, "offline_verification": True,
        "evaluator_lock_freeze_attestation": freeze_attestation,
        "openssl": {"path": "/usr/bin/openssl", "size_bytes": 1, "sha256": "c" * 64},
        "tsa_ca_certificate": {"path": "/ca.pem", "size_bytes": 1, "sha256": "d" * 64},
        "timestamps": {
            "amendment_v3": timestamp(context["amendment_identity"]),
            "evaluator_lock": timestamp(lock),
        },
    }
    document["attestation_payload_sha256"] = BUILDER._sha256_document(document)
    identity = {"path": str(path), "size_bytes": 1, "sha256": "7" * 64}
    monkeypatch.setattr(BUILDER, "_static_expected_json", lambda *args: (document, identity))
    monkeypatch.setattr(BUILDER, "_root_control_artifact_identity", lambda *args: identity)
    def root_identity(path, parent):
        if Path(path) == Path("/ca.pem"):
            return document["tsa_ca_certificate"]
        return identity
    monkeypatch.setattr(BUILDER, "_root_control_artifact_identity", root_identity)
    monkeypatch.setattr(
        BUILDER, "_validate_evaluator_lock_freeze_attestation",
        lambda **kwargs: freeze_attestation,
    )
    assert BUILDER._validate_rfc3161_attestation(
        path=path, expected_sha256=identity["sha256"], context=context,
        control_parent=tmp_path, runtime_guard_identity=guard,
        evaluator_lock_identity=lock,
    ) == identity
    with pytest.raises(ValueError, match="contract differs"):
        BUILDER._validate_rfc3161_attestation(
            path=path, expected_sha256=identity["sha256"], context=context,
            control_parent=tmp_path,
            runtime_guard_identity={**guard, "sha256": "f" * 64},
            evaluator_lock_identity=lock,
        )


def test_freeze_rfc3161_attestation_roundtrips_its_single_self_hash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    control = tmp_path / "control"
    control.mkdir()
    output = control / BUILDER.RFC3161_ATTESTATION_NAME
    amendment = {"path": "/project/amendment.json", "size_bytes": 10, "sha256": "1" * 64}
    lock = {"path": str(control / "lock.json"), "size_bytes": 11, "sha256": "2" * 64}
    guard = {"path": str(control / "guard.json"), "size_bytes": 12, "sha256": "3" * 64}
    context = {
        "plan_identity": {"path": "/plan", "size_bytes": 1, "sha256": "4" * 64},
        "acquisition_identity": {"path": "/acq", "size_bytes": 1, "sha256": "5" * 64},
        "source_identity": {"path": "/source", "size_bytes": 1, "sha256": "6" * 64},
        "amendment_identity": amendment,
    }
    args = types.SimpleNamespace(
        runtime_guard=Path(guard["path"]), expected_runtime_guard_sha256=guard["sha256"],
        evaluator_lock=Path(lock["path"]), expected_evaluator_lock_sha256=lock["sha256"],
        evaluator_lock_freeze_attestation=control / "freeze-attestation.json",
        expected_evaluator_lock_freeze_attestation_sha256="d" * 64,
        expected_openssl_sha256="7" * 64,
        tsa_ca_certificate=control / "tsa-ca.pem",
        expected_tsa_ca_certificate_sha256="e" * 64,
        amendment_v3_timestamp_query=control / "amendment.tsq",
        expected_amendment_v3_timestamp_query_sha256="8" * 64,
        amendment_v3_timestamp_response=control / "amendment.tsr",
        expected_amendment_v3_timestamp_response_sha256="9" * 64,
        evaluator_lock_timestamp_query=control / "lock.tsq",
        expected_evaluator_lock_timestamp_query_sha256="a" * 64,
        evaluator_lock_timestamp_response=control / "lock.tsr",
        expected_evaluator_lock_timestamp_response_sha256="b" * 64,
        output=output,
    )
    published: dict[str, object] = {}
    monkeypatch.setattr(BUILDER, "_closed_static_attestation", lambda *args: ({}, {}))
    monkeypatch.setattr(
        BUILDER, "_validate_metadata_runtime_guard", lambda **kwargs: ({}, guard)
    )
    monkeypatch.setattr(
        BUILDER, "_validate_frozen_evaluator_lock", lambda **kwargs: lock
    )
    freeze_attestation = {
        "path": str(args.evaluator_lock_freeze_attestation),
        "size_bytes": 1,
        "sha256": args.expected_evaluator_lock_freeze_attestation_sha256,
    }
    monkeypatch.setattr(
        BUILDER, "_validate_evaluator_lock_freeze_attestation",
        lambda **kwargs: freeze_attestation,
    )

    def identity(path: Path, expected: str, **kwargs):
        if Path(path) == Path(lock["path"]):
            return lock
        return {"path": str(Path(path).absolute()), "size_bytes": 1, "sha256": expected}

    monkeypatch.setattr(BUILDER, "_identity_from_cli", identity)
    token_expected = {
        args.tsa_ca_certificate: args.expected_tsa_ca_certificate_sha256,
        args.amendment_v3_timestamp_query: args.expected_amendment_v3_timestamp_query_sha256,
        args.amendment_v3_timestamp_response: args.expected_amendment_v3_timestamp_response_sha256,
        args.evaluator_lock_timestamp_query: args.expected_evaluator_lock_timestamp_query_sha256,
        args.evaluator_lock_timestamp_response: args.expected_evaluator_lock_timestamp_response_sha256,
    }
    monkeypatch.setattr(
        BUILDER, "_root_control_artifact_identity",
        lambda path, parent: (
            {"path": str(output), "size_bytes": 99, "sha256": "c" * 64}
            if Path(path) == output else identity(
                Path(path), token_expected[Path(path)]
            )
        ),
    )
    monkeypatch.setattr(BUILDER, "_contract_metadata", lambda value: {"exact": True})

    def verified(**kwargs):
        data = dict(kwargs["data_identity"])
        return {
            "status": "PASS", "data": data,
            "query": dict(kwargs["query_identity"]),
            "response": dict(kwargs["response_identity"]),
            "message_imprint_sha256": data["sha256"],
            "generation_time_utc": "2026-09-06T00:00:00Z",
            "query_verification": "OK", "data_verification": "OK",
        }

    monkeypatch.setattr(BUILDER, "_verify_timestamp_pair", verified)
    monkeypatch.setattr(
        BUILDER, "_publish_contract_recoverable",
        lambda path, document: published.update(document),
    )
    output_identity = {"path": str(output), "size_bytes": 99, "sha256": "c" * 64}
    monkeypatch.setattr(
        BUILDER, "_static_expected_json", lambda path, expected: (dict(published), output_identity)
    )
    result = BUILDER.freeze_rfc3161_attestation(
        args=args, context=context, control_parent=control,
        output_parent=tmp_path / "results",
    )
    unhashed = dict(published)
    payload_hash = unhashed.pop("attestation_payload_sha256")
    assert payload_hash == BUILDER._sha256_document(unhashed)
    assert result["rfc3161_attestation"] == output_identity


def test_root_metadata_preexec_failure_publishes_unique_immutable_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    published: list[tuple[Path, dict[str, object]]] = []
    monkeypatch.setattr(BUILDER.secrets, "token_hex", lambda size: "8" * 64)
    monkeypatch.setattr(
        BUILDER, "_publish_contract_recoverable",
        lambda path, document: published.append((Path(path), dict(document))),
    )
    monkeypatch.setattr(
        BUILDER, "_root_control_artifact_identity",
        lambda path, parent: {"path": str(path), "size_bytes": 1, "sha256": "9" * 64},
    )
    with pytest.raises(RuntimeError, match="immutable receipt"):
        BUILDER._run_root_metadata_command(
            label="evaluator-lock", argv=["/python", "-I", "/freezer"],
            control_parent=tmp_path, output_validator=lambda: {},
            popen=lambda *args, **kwargs: (_ for _ in ()).throw(OSError("exec failed")),
        )
    assert len(published) == 1
    path, receipt = published[0]
    assert path.name.startswith("root-metadata-execution-receipt-v3-evaluator-lock-")
    assert receipt["status"] == "FAIL"
    assert receipt["error_type"] == "OSError"


def test_root_metadata_child_failure_kills_reaps_and_records_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    events: list[str] = []
    published: list[dict[str, object]] = []

    class Child:
        returncode = None
        calls = 0

        def communicate(self, **kwargs):
            self.calls += 1
            if self.calls == 1:
                raise TimeoutError("stuck child")
            events.append("drain")
            return b"partial-out", b"partial-err"

        def poll(self):
            return None

        def kill(self):
            events.append("kill")
            self.returncode = 9

        def wait(self):
            events.append("wait")
            return 9

    monkeypatch.setattr(BUILDER.secrets, "token_hex", lambda size: "a" * 64)
    monkeypatch.setattr(
        BUILDER, "_publish_contract_recoverable",
        lambda path, document: published.append(dict(document)),
    )
    monkeypatch.setattr(
        BUILDER, "_root_control_artifact_identity",
        lambda path, parent: {"path": str(path), "size_bytes": 1, "sha256": "b" * 64},
    )
    with pytest.raises(RuntimeError, match="immutable receipt"):
        BUILDER._run_root_metadata_command(
            label="evaluator-lock", argv=["/python", "-I", "/freezer"],
            control_parent=tmp_path, output_validator=lambda: {},
            popen=lambda *args, **kwargs: Child(),
        )
    assert events == ["kill", "drain"]
    assert published[-1]["status"] == "FAIL"
    assert published[-1]["stdout_size_bytes"] == len(b"partial-out")
    assert published[-1]["stderr_size_bytes"] == len(b"partial-err")


def test_root_metadata_success_is_no_shell_close_fds_and_unique_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[dict[str, object]] = []
    published: list[Path] = []

    class Child:
        returncode = 0

        def communicate(self, **kwargs):
            return b"", b""

        def poll(self):
            return 0

    def popen(argv, **kwargs):
        calls.append({"argv": argv, **kwargs})
        return Child()

    nonces = iter(("b" * 64, "c" * 64))
    monkeypatch.setattr(BUILDER.secrets, "token_hex", lambda size: next(nonces))
    monkeypatch.setattr(
        BUILDER, "_publish_contract_recoverable",
        lambda path, document: published.append(Path(path)),
    )
    monkeypatch.setattr(
        BUILDER, "_root_control_artifact_identity",
        lambda path, parent: {"path": str(path), "size_bytes": 1, "sha256": "d" * 64},
    )
    for _ in range(2):
        BUILDER._run_root_metadata_command(
            label="evaluator-lock", argv=["/python", "-I", "/freezer"],
            control_parent=tmp_path,
            output_validator=lambda: {"path": "/lock", "size_bytes": 1, "sha256": "e" * 64},
            popen=popen,
        )
    assert published[0] != published[1]
    assert all(call["shell"] is False and call["close_fds"] is True for call in calls)


def test_freeze_evaluator_lock_uses_amendment_bound_freezer_and_candidate_python(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    candidate = tmp_path / "candidate-python"
    output = tmp_path / "amended-evaluator-lock-v2.json"
    files = {
        "evaluator_lock_freezer": BUILDER.EVALUATOR_LOCK_FREEZER_PATH,
        "evaluator": BUILDER.EVALUATOR_PATH,
        "launcher": BUILDER.LAUNCHER_PATH,
        "normalizer": BUILDER.NORMALIZER_PATH,
    }
    identities = {
        key: {"path": str(path), "size_bytes": 1, "sha256": character * 64}
        for (key, path), character in zip(files.items(), "1234", strict=True)
    }
    evaluator_test = {"path": "/eval-test", "size_bytes": 1, "sha256": "5" * 64}
    freezer_test = {"path": "/freezer-test", "size_bytes": 1, "sha256": "6" * 64}
    context = {
        "plan": {},
        "component_closure_identity": {"path": "/closure", "size_bytes": 1, "sha256": "7" * 64},
        "component_closure_generator_identity": {"path": "/generator", "size_bytes": 1, "sha256": "8" * 64},
        "amendment": {"amended_implementation": {**identities, "tests": [evaluator_test, freezer_test]}},
    }
    args = types.SimpleNamespace(
        execution_plan=Path("/plan"), expected_execution_plan_sha256="a" * 64,
        acquisition_manifest=Path("/acq"), expected_acquisition_manifest_sha256="b" * 64,
        source_manifest=Path("/source"), expected_source_manifest_sha256="c" * 64,
        amendment=Path("/amend"), expected_amendment_sha256="d" * 64,
        runtime_guard=Path("/guard"), expected_runtime_guard_sha256="e" * 64,
        no_decoder_evidence=Path("/evidence"), expected_no_decoder_evidence_sha256="f" * 64,
        no_decoder_evidence_generator=Path("/evidence-gen"),
        expected_no_decoder_evidence_generator_sha256="0" * 64,
        evaluator_test=Path(evaluator_test["path"]), expected_evaluator_test_sha256=evaluator_test["sha256"],
        freezer_test=Path(freezer_test["path"]), expected_freezer_test_sha256=freezer_test["sha256"],
        review=Path("/review"), expected_review_sha256="9" * 64,
        output=output,
    )
    monkeypatch.setattr(BUILDER, "_closed_static_attestation", lambda *args: ({}, {}))
    monkeypatch.setattr(BUILDER, "_validate_metadata_runtime_guard", lambda **kwargs: ({}, {"sha256": "e" * 64}))
    monkeypatch.setattr(BUILDER, "_candidate_interpreter_identity", lambda plan: (candidate, {"path": str(candidate)}))
    monkeypatch.setattr(
        BUILDER, "_amendment_bound_implementation",
        lambda context, key, path: identities[key],
    )
    monkeypatch.setattr(
        BUILDER, "_open_amendment_bound_script",
        lambda path, expected: (17, dict(expected)),
    )
    monkeypatch.setattr(
        BUILDER, "_identity_from_cli",
        lambda path, expected, **kwargs: (
            evaluator_test if Path(path) == Path(evaluator_test["path"])
            else freezer_test if Path(path) == Path(freezer_test["path"])
            else {"path": str(path), "size_bytes": 1, "sha256": expected}
        ),
    )
    captured: dict[str, object] = {}

    def run(**kwargs):
        captured.update(kwargs)
        return ({"path": str(output), "size_bytes": 1, "sha256": "a" * 64},
                {"path": "/receipt", "size_bytes": 1, "sha256": "b" * 64})

    monkeypatch.setattr(BUILDER, "_run_root_metadata_command", run)
    monkeypatch.setattr(BUILDER, "_contract_metadata", lambda context: {})
    monkeypatch.setattr(BUILDER, "_publish_contract_recoverable", lambda *args: None)
    freeze_identity = {
        "path": str(tmp_path / BUILDER.EVALUATOR_LOCK_FREEZE_ATTESTATION_NAME),
        "size_bytes": 1,
        "sha256": "c" * 64,
    }
    monkeypatch.setattr(
        BUILDER, "_regular_file_identity", lambda path, **kwargs: freeze_identity
    )
    monkeypatch.setattr(
        BUILDER, "_validate_evaluator_lock_freeze_attestation",
        lambda **kwargs: freeze_identity,
    )
    result = BUILDER.freeze_evaluator_lock(
        args=args, context=context, control_parent=tmp_path, output_parent=tmp_path / "results"
    )
    argv = captured["argv"]
    assert argv[:7] == [
        str(candidate), "-I", "-c", BUILDER.PINNED_SCRIPT_BOOTSTRAP, "17",
        str(BUILDER.EVALUATOR_LOCK_FREEZER_PATH), identities["evaluator_lock_freezer"]["sha256"],
    ]
    assert captured["label"] == "evaluator-lock"
    assert result["status"] == "PASS"


def test_runtime_help_exposes_metadata_and_timestamp_gates(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exited:
        BUILDER.main(["--help"])
    assert exited.value.code == 0
    output = capsys.readouterr().out
    assert "freeze-evaluator-lock" in output
    assert "freeze-rfc3161-attestation" in output


def _capacity_context() -> dict[str, object]:
    return {
        "acquisition": {
            "objects": [
                {
                    "observation_id": index,
                    "local_path": str(BUILDER.PROJECT_ROOT / f"iq/{index}.ci16"),
                    "actual_size_bytes": 1024,
                    "sha256": f"{index:064x}",
                }
                for index in range(1, 31)
            ]
        }
    }


def test_capacity_contract_is_exact_17_key_static_projection() -> None:
    contract = BUILDER._capacity_contract(_capacity_context())
    assert len(contract) == 17
    assert contract["results_tmpfs_capacity_bytes"] == 2_147_483_648
    assert contract["results_minimum_available_bytes_before_work"] == 1_610_612_736
    assert contract["persistent_export_filesystem_minimum_available_bytes"] == 5_368_709_120
    assert contract["available_bytes_formula"] == "statvfs.f_bavail*statvfs.f_frsize"
    assert contract["sealed_input_bytes"] == 30 * 1024
    assert contract["minimum_memavailable_bytes"] == (
        30 * 1024 + 2_147_483_648 + 8 * 536_870_912 + 4_294_967_296
    )
    assert contract["candidate_scratch_limit_bytes"] == 16_777_216
    assert contract["candidate_scratch_limit_enforced"] is False


def test_results_reserve_boundary_is_inclusive_and_rejects_one_byte_less(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record = {"super_options": ["rw", "size=2097152k"]}
    exact = {
        "capacity_bytes": BUILDER.RESULTS_TMPFS_CAPACITY_BYTES,
        "total_bytes": BUILDER.RESULTS_TMPFS_CAPACITY_BYTES,
        "available_bytes": BUILDER.RESULTS_MINIMUM_AVAILABLE_BYTES_BEFORE_WORK,
    }
    monkeypatch.setattr(BUILDER, "_results_capacity_observation", lambda *a: exact)
    assert BUILDER._require_results_available(Path("/results"), record, phase="boundary")[
        "available_bytes"
    ] == BUILDER.RESULTS_MINIMUM_AVAILABLE_BYTES_BEFORE_WORK
    monkeypatch.setattr(
        BUILDER,
        "_results_capacity_observation",
        lambda *a: {**exact, "available_bytes": exact["available_bytes"] - 1},
    )
    with pytest.raises(OSError) as error:
        BUILDER._require_results_available(Path("/results"), record, phase="boundary")
    assert error.value.errno == BUILDER.errno.ENOSPC


def test_results_capacity_uses_bavail_and_requires_exact_total_and_mount_size(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    blocks = BUILDER.RESULTS_TMPFS_CAPACITY_BYTES // 4096
    available = BUILDER.RESULTS_MINIMUM_AVAILABLE_BYTES_BEFORE_WORK // 4096
    monkeypatch.setattr(
        BUILDER.os,
        "statvfs",
        lambda path: types.SimpleNamespace(
            f_frsize=4096, f_bsize=4096, f_blocks=blocks,
            f_bavail=available, f_bfree=blocks * 100,
        ),
    )
    record = {"super_options": ["rw", "size=2097152k"]}
    observed = BUILDER._results_capacity_observation(Path("/results"), record)
    assert observed["available_bytes"] == available * 4096
    with pytest.raises(ValueError, match="exactly 2 GiB"):
        BUILDER._results_capacity_observation(
            Path("/results"), {"super_options": ["rw", "size=1G"]}
        )
    with pytest.raises(ValueError, match="finite size"):
        BUILDER._results_capacity_observation(
            Path("/results"), {"super_options": ["rw"]}
        )


def test_system_capacity_preflight_accepts_exact_boundaries_and_rejects_storage_minus_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _capacity_context()
    contract = BUILDER._capacity_contract(context)
    storage = {
        "total_bytes": 10 * 1024**3,
        "available_bytes": BUILDER.PERSISTENT_EXPORT_MINIMUM_AVAILABLE_BYTES,
    }
    monkeypatch.setattr(BUILDER, "_statvfs_bytes", lambda path: dict(storage))
    monkeypatch.setattr(
        BUILDER, "_read_memavailable_bytes", lambda: contract["minimum_memavailable_bytes"]
    )
    monkeypatch.setattr(
        BUILDER.resource,
        "getrlimit",
        lambda which: (
            BUILDER.MINIMUM_RLIMIT_NOFILE if which == BUILDER.resource.RLIMIT_NOFILE
            else BUILDER.MINIMUM_RLIMIT_NPROC,
            BUILDER.resource.RLIM_INFINITY,
        ),
    )
    monkeypatch.setattr(BUILDER.os, "sched_getaffinity", lambda pid: set(range(8)))
    monkeypatch.setattr(
        BUILDER,
        "_read_cgroup_v2_limits",
        lambda: {
            "path": "/sys/fs/cgroup/test",
            "cpu_millicores": 8000,
            "memory_max_bytes": contract["minimum_memavailable_bytes"],
            "memory_current_bytes": 0,
            "memory_available_bytes": contract["minimum_memavailable_bytes"],
            "pids_max": 576,
            "pids_current": 0,
            "pids_available": 576,
        },
    )
    assert BUILDER._system_capacity_preflight(context)["capacity_contract"] == contract
    storage["available_bytes"] = BUILDER.PERSISTENT_EXPORT_MINIMUM_AVAILABLE_BYTES - 1
    with pytest.raises(OSError) as error:
        BUILDER._system_capacity_preflight(context)
    assert error.value.errno == BUILDER.errno.ENOSPC


def test_cgroup_membership_is_resolved_beneath_exact_root(
    tmp_path: Path,
) -> None:
    proc = tmp_path / "cgroup"
    root = tmp_path / "sys-fs-cgroup"
    member = root / "user.slice/campaign.scope"
    member.mkdir(parents=True)
    proc.write_text("0::/user.slice/campaign.scope\n", encoding="ascii")
    for name, value in {
        "cpu.max": "800000 100000\n",
        "memory.max": "max\n",
        "memory.current": "1\n",
        "pids.max": "576\n",
        "pids.current": "0\n",
    }.items():
        (member / name).write_text(value, encoding="ascii")
    observed = BUILDER._read_cgroup_v2_limits(
        proc_cgroup=proc, cgroup_root=root
    )
    assert observed["path"] == str(member)
    assert observed["cpu_millicores"] == 8000
    for unsafe in ("0:://etc\n", "0::/../etc\n"):
        proc.write_text(unsafe, encoding="ascii")
        with pytest.raises(ValueError, match="escapes|unsafe"):
            BUILDER._read_cgroup_v2_limits(proc_cgroup=proc, cgroup_root=root)


def test_live_cgroup_v2_inventory_smoke_is_inside_system_root() -> None:
    observed = BUILDER._read_cgroup_v2_limits()
    assert Path(str(observed["path"])).is_relative_to(Path("/sys/fs/cgroup"))
    assert observed["pids_current"] >= 0


def test_activation_capacity_preflight_precedes_namespace_entry_and_mounts() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    branch = source[source.index('if args.command == "activate-readonly-project":') :]
    assert branch.index("_system_capacity_preflight(context)") < branch.index(
        "_enter_persistent_namespace(control_parent)"
    )
    assert branch.index("_expanded_activation_context(") < branch.index(
        "_enter_persistent_namespace(control_parent)"
    )
    assert branch.index("_expanded_activation_context(") < branch.index(
        "_validate_external_output_parent("
    )


def test_start_namespace_refuses_before_fork_unless_mirror_inventory_passes() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    branch = source[source.index('if args.command == "start-persistent-namespace":') :]
    assert branch.index("_expanded_activation_context(") < branch.index(
        "start_persistent_namespace("
    )


def test_main_start_namespace_mirror_preflight_failure_never_forks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    control = tmp_path / "control"
    roots = {
        "candidate": tmp_path / "candidate",
        "component": tmp_path / "component",
    }
    context = {"roots": roots}
    metadata = {"campaign": "exact"}
    args = types.SimpleNamespace(
        command="start-persistent-namespace",
        project_root=BUILDER.PROJECT_ROOT,
        control_parent=control,
        external_output_parent=tmp_path / "output",
        expected_runtime_guard_builder_sha256="a" * 64,
        execution_plan=tmp_path / "plan.json",
        expected_execution_plan_sha256="b" * 64,
        acquisition_manifest=tmp_path / "acquisition.json",
        expected_acquisition_manifest_sha256="c" * 64,
        source_manifest=tmp_path / "source.json",
        expected_source_manifest_sha256="d" * 64,
        amendment=tmp_path / "amendment.json",
        expected_amendment_sha256="e" * 64,
    )
    monkeypatch.setattr(
        BUILDER.argparse.ArgumentParser, "parse_args", lambda _self, _argv: args
    )
    monkeypatch.setattr(BUILDER, "_require_exact_project_root", lambda _path: None)
    monkeypatch.setattr(
        BUILDER, "_validate_control_parent", lambda path, **_kwargs: path
    )
    monkeypatch.setattr(BUILDER, "_validate_control_copy", lambda *_a, **_k: None)
    monkeypatch.setattr(BUILDER, "_require_static_root_interpreter", lambda: None)
    monkeypatch.setattr(BUILDER, "_static_metadata_context", lambda **_kwargs: context)
    monkeypatch.setattr(BUILDER, "_contract_metadata", lambda actual: metadata)
    monkeypatch.setattr(
        BUILDER,
        "_expanded_activation_context",
        lambda *_a: (_ for _ in ()).throw(ValueError("mirror preflight rejected")),
    )
    monkeypatch.setattr(
        BUILDER,
        "start_persistent_namespace",
        lambda *_a: pytest.fail("namespace keeper was started before mirror preflight"),
    )
    with pytest.raises(ValueError, match="mirror preflight rejected"):
        BUILDER.main([])


@pytest.mark.parametrize("inventory_fails", [False, True])
def test_main_activation_inventories_precede_namespace_and_create_true(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, inventory_fails: bool,
) -> None:
    control = tmp_path / "control"
    output = tmp_path / "output"
    roots = {
        "candidate": tmp_path / "candidate",
        "component": tmp_path / "component",
    }
    context = {"roots": roots, "component_closure": {"status": "complete"}}
    metadata = {"campaign": "exact"}
    events: list[str] = []
    args = types.SimpleNamespace(
        command="activate-readonly-project",
        project_root=BUILDER.PROJECT_ROOT,
        control_parent=control,
        external_output_parent=output,
        expected_runtime_guard_builder_sha256="a" * 64,
        execution_plan=tmp_path / "plan.json",
        expected_execution_plan_sha256="b" * 64,
        acquisition_manifest=tmp_path / "acquisition.json",
        expected_acquisition_manifest_sha256="c" * 64,
        source_manifest=tmp_path / "source.json",
        expected_source_manifest_sha256="d" * 64,
        amendment=tmp_path / "amendment.json",
        expected_amendment_sha256="e" * 64,
    )
    monkeypatch.setattr(
        BUILDER.argparse.ArgumentParser, "parse_args", lambda _self, _argv: args
    )
    monkeypatch.setattr(BUILDER, "_require_exact_project_root", lambda _path: None)
    monkeypatch.setattr(
        BUILDER, "_validate_control_parent", lambda path, **_kwargs: path
    )
    monkeypatch.setattr(BUILDER, "_validate_control_copy", lambda *_a, **_k: None)
    monkeypatch.setattr(BUILDER, "_require_static_root_interpreter", lambda: None)
    monkeypatch.setattr(BUILDER, "_static_metadata_context", lambda **_kwargs: context)
    monkeypatch.setattr(BUILDER, "_validate_component_closure_raw", lambda _value: None)
    monkeypatch.setattr(
        BUILDER, "_system_capacity_preflight",
        lambda actual: events.append("capacity") if actual is context else None,
    )
    monkeypatch.setattr(BUILDER, "_contract_metadata", lambda actual: metadata)
    def inventories(actual_metadata: object, actual_roots: object) -> None:
        if actual_metadata is not metadata or actual_roots is not roots:
            pytest.fail("main supplied the wrong activation inventory context")
        events.append("inventories")
        if inventory_fails:
            raise ValueError("pre-mutation inventory rejected drift")

    monkeypatch.setattr(BUILDER, "_expanded_activation_context", inventories)
    monkeypatch.setattr(
        BUILDER, "_enter_persistent_namespace",
        lambda _control: events.append("enter"),
    )

    def output_parent(path: Path, *, create: bool) -> Path:
        assert create is True
        events.append("output-create")
        return path

    monkeypatch.setattr(BUILDER, "_validate_external_output_parent", output_parent)
    monkeypatch.setattr(
        BUILDER, "activate_readonly_project",
        lambda *_a, **_k: events.append("activate") or {"status": "active"},
    )

    if inventory_fails:
        with pytest.raises(ValueError, match="pre-mutation inventory rejected"):
            BUILDER.main([])
        assert events == ["capacity", "inventories"]
    else:
        assert BUILDER.main([]) == 0
        assert events == [
            "capacity", "inventories", "enter", "output-create", "activate"
        ]


def _planned_mirror_fixture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> tuple[dict[str, object], dict[str, dict[str, object]], list[dict[str, object]]]:
    project = tmp_path / "project"
    candidate = project / "candidate"
    logical_paths = [
        project / "src/telemetry_yield/cli.py",
        project / "src/telemetry_yield/planning/manuscript.py",
    ]
    mirror_paths = [
        candidate / "lib/python3.12/site-packages/telemetry_yield/cli.py",
        candidate / "lib/python3.12/site-packages/telemetry_yield/planning/manuscript.py",
    ]
    planned_payloads = [b"planned-cli-bytes", b"planned-manuscript-bytes"]
    live_payloads = [b"known-live-cli-drift", b"known-live-manuscript-drift"]
    specs: list[dict[str, object]] = []
    found: dict[str, dict[str, object]] = {}
    for logical, mirror, planned, live in zip(
        logical_paths, mirror_paths, planned_payloads, live_payloads, strict=True
    ):
        logical.parent.mkdir(parents=True, exist_ok=True)
        mirror.parent.mkdir(parents=True, exist_ok=True)
        logical.write_bytes(live)
        mirror.write_bytes(planned)
        logical_expected = {
            "path": str(logical.absolute()),
            "size_bytes": len(planned),
            "sha256": hashlib.sha256(planned).hexdigest(),
        }
        spec = {
            "logical_source": logical_expected,
            "required_live_drift": {
                "path": str(logical.absolute()),
                "size_bytes": len(live),
                "sha256": hashlib.sha256(live).hexdigest(),
            },
            "byte_authority": {
                "path": str(mirror.absolute()),
                "size_bytes": len(planned),
                "sha256": hashlib.sha256(planned).hexdigest(),
            },
            "byte_authority_runtime_root": "candidate",
            "sealed_destination_relative_path": logical.relative_to(project).as_posix(),
        }
        specs.append(spec)
        found[str(logical.absolute())] = dict(logical_expected)
    monkeypatch.setattr(BUILDER, "PROJECT_ROOT", project)
    monkeypatch.setattr(BUILDER, "EXPECTED_CANDIDATE_ROOT", candidate)
    monkeypatch.setattr(BUILDER, "PLANNED_ARTIFACT_MIRROR_SPECS", tuple(specs))
    contract = BUILDER._planned_artifact_mirror_contract()
    context: dict[str, object] = {
        "amendment": {
            BUILDER.PLANNED_ARTIFACT_MIRROR_AMENDMENT_KEY: contract,
        },
        "roots": {"candidate": candidate, "component": project / "component"},
    }
    return context, found, specs


def test_planned_artifact_mirror_contract_accepts_only_exact_two_known_drifts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    context, found, specs = _planned_mirror_fixture(tmp_path, monkeypatch)
    bindings = BUILDER._planned_artifact_mirror_bindings(context, found)
    assert set(bindings) == set(found)
    contract = context["amendment"][BUILDER.PLANNED_ARTIFACT_MIRROR_AMENDMENT_KEY]
    unhashed = dict(contract)
    assert unhashed.pop("planned_artifact_mirror_payload_sha256") == (
        BUILDER._sha256_document(unhashed)
    )
    assert contract["entry_count"] == 2
    assert contract["fallback_permitted"] is False

    logical = Path(str(specs[0]["logical_source"]["path"]))
    planned = Path(str(specs[0]["byte_authority"]["path"])).read_bytes()
    logical.write_bytes(planned)
    with pytest.raises(ValueError, match="small project artifact differs"):
        BUILDER._planned_artifact_mirror_bindings(context, found)
    logical.write_bytes(b"unapproved-third-state")
    with pytest.raises(ValueError, match="small project artifact differs"):
        BUILDER._planned_artifact_mirror_bindings(context, found)


def test_planned_artifact_mirror_rejects_amendment_plan_or_mirror_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    context, found, specs = _planned_mirror_fixture(tmp_path, monkeypatch)
    context["amendment"] = {}
    with pytest.raises(ValueError, match="amendment planned-artifact"):
        BUILDER._planned_artifact_mirror_bindings(context, found)
    context["amendment"] = {
        BUILDER.PLANNED_ARTIFACT_MIRROR_AMENDMENT_KEY: (
            BUILDER._planned_artifact_mirror_contract()
        )
    }
    found.pop(str(specs[1]["logical_source"]["path"]))
    with pytest.raises(ValueError, match="binding is malformed"):
        BUILDER._planned_artifact_mirror_bindings(context, found)
    found[str(specs[1]["logical_source"]["path"])] = dict(
        specs[1]["logical_source"]
    )
    Path(str(specs[1]["byte_authority"]["path"])).write_bytes(b"mirror-drift")
    with pytest.raises(ValueError, match="small project artifact differs"):
        BUILDER._planned_artifact_mirror_bindings(context, found)


def test_small_artifact_inventory_treats_mirror_contract_as_authorization_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    context, found, _specs = _planned_mirror_fixture(tmp_path, monkeypatch)
    context.update({
        "plan": {"frozen": list(found.values())},
        "acquisition": {},
        "source": {},
        "plan_identity": {"path": "/plan", "size_bytes": 1, "sha256": "1" * 64},
        "acquisition_identity": {
            "path": "/acquisition", "size_bytes": 1, "sha256": "2" * 64,
        },
        "source_identity": {"path": "/source", "size_bytes": 1, "sha256": "3" * 64},
        "amendment_identity": {
            "path": "/amendment", "size_bytes": 1, "sha256": "4" * 64,
        },
        "component_closure_identity": {
            "path": "/closure", "size_bytes": 1, "sha256": "5" * 64,
        },
        "component_closure_generator_identity": {
            "path": "/generator", "size_bytes": 1, "sha256": "6" * 64,
        },
    })
    implementation: dict[str, object] = {}
    for key, path in (
        ("launcher", BUILDER.LAUNCHER_PATH),
        ("runtime_guard_builder", BUILDER.HERE / "build_runtime_guard_v3.py"),
        ("normalizer", BUILDER.NORMALIZER_PATH),
        ("evaluator", BUILDER.EVALUATOR_PATH),
        ("evaluator_lock_freezer", BUILDER.EVALUATOR_LOCK_FREEZER_PATH),
    ):
        implementation[key] = BUILDER._regular_file_identity(path)
    context["amendment"]["amended_implementation"] = implementation
    identities = BUILDER._sealed_project_artifact_identities(context)
    assert identities == sorted(
        found.values(), key=lambda item: os.fsencode(str(item["path"]))
    )

    extra = Path(str(BUILDER.PROJECT_ROOT)) / "src/unapproved.py"
    extra.parent.mkdir(parents=True, exist_ok=True)
    extra.write_bytes(b"third-live-drift")
    context["plan"]["frozen"].append({
        "path": str(extra),
        "size_bytes": len(b"third-planned-state"),
        "sha256": hashlib.sha256(b"third-planned-state").hexdigest(),
    })
    with pytest.raises(ValueError, match="small project artifact differs"):
        BUILDER._sealed_project_artifact_identities(context)


def test_sealed_project_materialization_uses_mirror_bytes_at_logical_destination(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    context, found, specs = _planned_mirror_fixture(tmp_path, monkeypatch)
    target = tmp_path / "sealed-project-artifacts"
    target.mkdir()
    calls: list[tuple[Path, Path, dict[str, object]]] = []

    def copy(source: Path, destination: Path, expected: Mapping[str, object]):
        calls.append((source, destination, dict(expected)))
        return {**dict(expected), "path": str(destination)}

    monkeypatch.setattr(BUILDER, "_copy_iq_to_private_tmpfs", copy)
    source = found[str(specs[0]["logical_source"]["path"])]
    binding = BUILDER._materialize_sealed_project_artifact(context, target, source)
    assert binding["kind"] == "candidate_runtime_content_addressed_mirror"
    assert calls == [
        (
            Path(str(specs[0]["byte_authority"]["path"])),
            target / str(specs[0]["sealed_destination_relative_path"]),
            dict(specs[0]["byte_authority"]),
        )
    ]
    assert calls[0][0] != Path(str(specs[0]["logical_source"]["path"]))


def test_guard_schema_binds_snapshot_mirror_contract_and_byte_authority() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    assert 'PLANNED_ARTIFACT_MIRROR_AMENDMENT_KEY: (' in source
    assert '"byte_authority": byte_authority' in source
    assert '"byte_authority": dict(record["byte_authority"])' in source
