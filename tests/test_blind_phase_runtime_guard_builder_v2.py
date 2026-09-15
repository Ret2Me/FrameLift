from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import types

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "work/blind-phase-confirmatory-v2/build_runtime_guard_v2.py"

def _module():
    name = "blind_phase_runtime_guard_builder_v2_test_module"
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
    assert not (tmp_path / "seal-transaction-intent-v2.json").exists()
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
    monkeypatch.setattr(BUILDER.os, "geteuid", lambda: 0)
    monkeypatch.setattr(BUILDER, "_validate_control_parent", lambda path, create: path)
    monkeypatch.setattr(BUILDER, "_validate_external_output_parent", lambda path, create: path)
    monkeypatch.setattr(BUILDER, "_mount_records_at_project", lambda: [])
    monkeypatch.setattr(BUILDER, "_parse_mountinfo", lambda _: records)
    monkeypatch.setattr(BUILDER, "_publish_contract_recoverable", lambda *a: None)
    monkeypatch.setattr(BUILDER, "_mount_contract", lambda *a, **k: {"status":"active"})
    def command(argv):
        commands.append(tuple(argv)); target = Path(argv[-1])
        if argv[1] == "--bind": records.append(_mount_record(target, ro=target != BUILDER.PROJECT_ROOT))
        elif target == BUILDER.PROJECT_ROOT: records[0] = {**records[0], "mount_options":["ro"]}
        else:
            index = next(i for i,r in enumerate(records) if r["mount_point"] == str(target))
            records[index] = {**records[index], "mount_options":["rw"]}
    monkeypatch.setattr(BUILDER, "_run_mount_command", command)
    BUILDER.activate_readonly_project(tmp_path, tmp_path/"out", roots, {})
    first_child_bind = next(i for i,c in enumerate(commands) if c[1] == "--bind" and c[-1] != str(BUILDER.PROJECT_ROOT))
    project_ro = next(i for i,c in enumerate(commands) if "remount,bind,ro" in c)
    assert project_ro < first_child_bind

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
    assert BUILDER._regular_file_identity(BUILDER.LAUNCHER_PATH)["sha256"] == BUILDER.EXPECTED_LAUNCHER_V2_SHA256

def test_freeze_handoffs_are_unique_append_only_paths() -> None:
    first = BUILDER._handoff_paths(BUILDER.DEFAULT_CONTROL_PARENT, "1" * 64)[0]
    second = BUILDER._handoff_paths(BUILDER.DEFAULT_CONTROL_PARENT, "2" * 64)[0]
    assert first != second and first.parent == second.parent == BUILDER.DEFAULT_CONTROL_PARENT
    with pytest.raises(ValueError, match="attempt id"):
        BUILDER._handoff_paths(BUILDER.DEFAULT_CONTROL_PARENT, "short")

def test_rollback_without_project_mount_requires_durable_ready_marker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    roots = {"candidate": BUILDER.EXPECTED_CANDIDATE_ROOT, "component": BUILDER.EXPECTED_COMPONENT_ROOT}
    control = tmp_path / "control"; control.mkdir(); output = tmp_path / "output"; output.mkdir()
    intent_path = control / "rollback-sealed-runtimes-intent-v2.json"; intent_path.write_text("x")
    metadata = {label: {"path": f"/{label}", "size_bytes": 1, "sha256": label[0] * 64}
        for label in ("plan", "acquisition", "source", "amendment")}
    context = {"roots": roots, **{f"{label}_identity": value for label, value in metadata.items()}}
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
    intent_path = control / "rollback-sealed-runtimes-intent-v2.json"
    intent_path.write_text("x", encoding="ascii")
    roots = {"candidate": BUILDER.EXPECTED_CANDIDATE_ROOT,
        "component": BUILDER.EXPECTED_COMPONENT_ROOT}
    metadata = {label: {"path": f"/{label}", "size_bytes": 1,
        "sha256": label[0] * 64} for label in ("plan", "acquisition", "source", "amendment")}
    context = {"roots": roots,
        **{f"{label}_identity": value for label, value in metadata.items()}}
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
    intent_path = control / "seal-transaction-intent-v2.json"
    intent_path.write_text("x", encoding="ascii")
    metadata = {label: {"path": f"/{label}", "size_bytes": 1,
        "sha256": label[0] * 64} for label in ("plan", "acquisition", "source", "amendment")}
    closure_identity = {"path": "/closure", "size_bytes": 1, "sha256": "c" * 64}
    generator_identity = {"path": "/generator", "size_bytes": 1, "sha256": "g" * 64}
    context = {**{f"{label}_identity": value for label, value in metadata.items()},
        "component_closure_identity": closure_identity,
        "component_closure_generator_identity": generator_identity,
        "component_closure": {"raw_manifest_sha256": "1" * 64,
            "content_manifest_sha256": "2" * 64}}
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
    intent_path = control / "rollback-sealed-runtimes-intent-v2.json"
    intent_path.write_text("intent", encoding="ascii")
    closed_path = control / "readonly-project-closed-v2.json"
    closed_path.write_text("closed", encoding="ascii")
    metadata = {label: {"path": f"/{label}", "size_bytes": 1,
        "sha256": label[0] * 64} for label in ("plan", "acquisition", "source", "amendment")}
    context = {"roots": roots,
        **{f"{label}_identity": value for label, value in metadata.items()}}
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
    assert "rollback-sealed-runtimes-ready-to-unmount-v2.json" in publications
    assert "rollback-sealed-runtimes-complete-v2.json" in publications


def test_rollback_repairs_partial_ready_and_complete_publications_on_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "project"
    roots = {"candidate": _tree(project / "candidate-parent", "runtime"),
        "component": _tree(project / "component-parent", "runtime")}
    control = tmp_path / "control"
    control.mkdir()
    intent_path = control / "rollback-sealed-runtimes-intent-v2.json"
    intent_path.write_text("intent", encoding="ascii")
    ready_path = control / "rollback-sealed-runtimes-ready-to-unmount-v2.json"
    ready_path.write_text("payload-only", encoding="ascii")
    metadata = {label: {"path": f"/{label}", "size_bytes": 1,
        "sha256": label[0] * 64} for label in ("plan", "acquisition", "source", "amendment")}
    context = {"roots": roots,
        **{f"{label}_identity": value for label, value in metadata.items()}}
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
        if path.name == "rollback-sealed-runtimes-complete-v2.json":
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
    assert (control / "rollback-sealed-runtimes-complete-v2.json").exists()
    result = BUILDER.rollback_sealed_runtimes(context, control, tmp_path / "output")
    assert result["status"] == "rollback_complete_project_unmounted"
    assert completion_attempts == 2
    assert (control / "rollback-sealed-runtimes-complete-v2.json.sha256").exists()
