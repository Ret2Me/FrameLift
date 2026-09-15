from __future__ import annotations

import ast
import base64
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import types

import pytest


PROJECT = Path("/home/ubuntu/telemetry-yield")
SOURCE = PROJECT / "work/blind-phase-confirmatory-v2/build_retry4_canonical_control_root_install_terminal_review_v1.py"
TEST = PROJECT / "tests/test_retry4_canonical_control_root_install_terminal_review_v1.py"
EXPECTED_SOURCE_SHA256 = "0a3d0b7a52f2657de80e4ca1abbe1538ab855a2394f83abbcf42afcafdafe23a"
EXPECTED_SOURCE_SIZE = 42216
EXPECTED_GENERATOR_BLOCKED = False
BLOCKED_SOURCE_SHA256 = "2b9675b3ae81f8244f937f25e53d001ee736285abb2e0de44714e73c2d9a0267"
BLOCKED_SOURCE_SIZE = 42215
PREDICTED_EXECUTABLE_SOURCE_SHA256 = "0a3d0b7a52f2657de80e4ca1abbe1538ab855a2394f83abbcf42afcafdafe23a"
PREDICTED_EXECUTABLE_SOURCE_SIZE = 42216
EXPECTED_NORMALIZED_AST_SHA256 = "00a998d5afcdd297199b8eb323472ddabb1692cc031130c7fd1e5411b7e95e79"
EXPECTED_MUTATION_SURFACE_SHA256 = "f8a88e1e07bb5328e2dab2fb6891d7e3dacb4225faffd9db38e4a887ed98daff"

# No verified module bytes are executed before these assertions and AST parse.
SOURCE_BYTES = SOURCE.read_bytes()
assert len(SOURCE_BYTES) == EXPECTED_SOURCE_SIZE
assert hashlib.sha256(SOURCE_BYTES).hexdigest() == EXPECTED_SOURCE_SHA256
SOURCE_TREE = ast.parse(SOURCE_BYTES, filename=str(SOURCE))
MODULE = types.ModuleType("tested_retry4_terminal_review_generator")
MODULE.__file__ = str(SOURCE)
MODULE.__package__ = None
exec(compile(SOURCE_TREE, str(SOURCE), "exec"), MODULE.__dict__)
TEST_BYTES = TEST.read_bytes()


def _sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _blocked_source(payload: bytes) -> bytes:
    blocked = b"\nTERMINAL_REVIEW_GENERATOR_BLOCKED_PENDING_INDEPENDENT_AUDIT = True\n"
    executable = b"\nTERMINAL_REVIEW_GENERATOR_BLOCKED_PENDING_INDEPENDENT_AUDIT = False\n"
    if EXPECTED_GENERATOR_BLOCKED:
        assert payload.count(blocked) == 1 and payload.count(executable) == 0
        return payload
    assert payload.count(executable) == 1 and payload.count(blocked) == 0
    return payload.replace(executable, blocked, 1)


def _callee(call: ast.Call) -> str:
    value = call.func
    if isinstance(value, ast.Name):
        return value.id
    if isinstance(value, ast.Attribute):
        parts = [value.attr]
        cursor = value.value
        while isinstance(cursor, ast.Attribute):
            parts.append(cursor.attr)
            cursor = cursor.value
        if isinstance(cursor, ast.Name):
            parts.append(cursor.id)
            return ".".join(reversed(parts))
    return "<dynamic>"


def _bounded_effect_surface(payload: bytes) -> dict[str, object]:
    tree = ast.parse(payload)
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == "TERMINAL_REVIEW_GENERATOR_BLOCKED_PENDING_INDEPENDENT_AUDIT" for target in node.targets):
            node.value = ast.Constant(value=True)
    functions = {node.name: node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
    if len(functions) != len([node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))]):
        raise AssertionError("duplicate top-level function")
    owners = {id(child): name for name, function in functions.items() for child in ast.walk(function)}
    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)]
    imports: list[tuple[object, ...]] = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            imports.extend((alias.name, alias.asname) for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imports.extend((node.module, alias.name, alias.asname) for alias in node.names)
    if imports != [
        ("__future__", "annotations", None), ("argparse", None), ("ctypes", None),
        ("errno", None), ("fcntl", None), ("hashlib", None), ("json", None),
        ("os", None), ("pathlib", "Path", None), ("stat", None), ("sys", None),
        ("types", None), ("typing", "Mapping", None), ("typing", "Sequence", None),
    ]:
        raise AssertionError("import surface differs")
    forbidden = {
        "eval", "__import__", "globals", "locals", "vars", "setattr", "delattr",
        "os.system", "os.popen", "os.fork", "os.forkpty", "os.execv", "os.execve",
        "os.spawnv", "os.spawnve", "subprocess.run", "subprocess.Popen",
        "Path.write_bytes", "Path.write_text", "Path.touch", "Path.unlink",
        "shutil.copy", "shutil.copyfile", "shutil.rmtree", "os.unlink", "os.remove",
        "os.rename", "os.replace", "os.mkdir", "os.makedirs", "os.symlink",
        "os.truncate", "os.ftruncate", "os.pwrite", "os.kill", "os.unshare",
    }
    names = {_callee(call) for call in calls}
    if names & forbidden:
        raise AssertionError("forbidden call surface")
    protected = set(functions) | {"linkat", "Path", "os", "fcntl", "ctypes"}
    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.NamedExpr)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if any(isinstance(target, ast.Name) and target.id in protected for target in targets):
                if not (
                    isinstance(node, ast.Assign) and len(targets) == 1
                    and isinstance(targets[0], ast.Name) and targets[0].id == "linkat"
                    and isinstance(node.value, ast.Attribute) and node.value.attr == "linkat"
                    and owners.get(id(node)) == "_publish_one"
                ):
                    raise AssertionError("protected callable rebinding")
    mutation_names = {"os.write", "os.fchown", "os.fchmod", "os.fsync", "linkat", "fcntl.flock"}
    mutation = []
    for call in calls:
        name = _callee(call)
        if name in mutation_names:
            mutation.append((
                owners.get(id(call), "module"), name,
                ast.dump(ast.Tuple(elts=call.args, ctx=ast.Load()), include_attributes=False),
                ast.dump(ast.Dict(keys=[ast.Constant(keyword.arg) for keyword in call.keywords], values=[keyword.value for keyword in call.keywords]), include_attributes=False),
            ))
    normalized_ast = _sha(ast.dump(tree, include_attributes=False).encode())
    mutation_sha = _sha(json.dumps(mutation, sort_keys=True, separators=(",", ":")).encode())
    result = {
        "top_level_function_count": len(functions), "call_count": len(calls),
        "exec_count": sum(_callee(call) == "exec" for call in calls),
        "flock_count": sum(_callee(call) == "fcntl.flock" for call in calls),
        "mutation_call_count": len(mutation), "mutation_sha256": mutation_sha,
        "normalized_ast_sha256": normalized_ast,
    }
    if result != {
        "top_level_function_count": 40, "call_count": 399, "exec_count": 2,
        "flock_count": 1, "mutation_call_count": 9,
        "mutation_sha256": EXPECTED_MUTATION_SURFACE_SHA256,
        "normalized_ast_sha256": EXPECTED_NORMALIZED_AST_SHA256,
    }:
        raise AssertionError("bounded exact effect surface differs")
    return result


def _held_exact_script(body: str) -> str:
    encoded = base64.b64encode(SOURCE_BYTES).decode("ascii")
    return f"""
import base64, hashlib, os, pathlib, types
source=base64.b64decode({encoded!r})
assert len(source)=={EXPECTED_SOURCE_SIZE}
assert hashlib.sha256(source).hexdigest()=={EXPECTED_SOURCE_SHA256!r}
test_sha256={_sha(TEST_BYTES)!r}
p=pathlib.Path({str(SOURCE)!r})
g=types.ModuleType('held_terminal_review'); g.__file__=str(p); g.__package__=None
exec(compile(source,str(p),'exec'),g.__dict__)
{body}
"""


def _candidate() -> tuple[dict[str, object], bytes, bytes, types.SimpleNamespace]:
    fake = types.SimpleNamespace(expected_terminal_review_checks=lambda: {"exact": True})
    unhashed = {
        "schema_version": MODULE.SCHEMA,
        "status": "GO",
        "reviewed_bindings": {"fixed": True},
        "checks": {"exact": True},
        "severity_counts": {"P0": 0, "P1": 0, "P2": 0},
    }
    document = {**unhashed, "review_payload_sha256": MODULE._document_hash(unhashed)}
    payload = MODULE._canonical(document)
    return document, payload, MODULE._sidecar(payload), fake


def _fake_context() -> dict[str, object]:
    document, payload, sidecar, fake = _candidate()
    return {
        "records": {}, "receipts": {}, "module": fake, "runtime": {},
        "verdict": {}, "installer_authority": {}, "snapshot": {"fixed": True},
        "document": document, "payload": payload, "sidecar": sidecar,
    }


def _patch_tmp_reports(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    output = tmp_path / MODULE.OUTPUT.name
    monkeypatch.setattr(MODULE, "REPORTS", tmp_path)
    monkeypatch.setattr(MODULE, "OUTPUT", output)
    monkeypatch.setattr(MODULE, "SIDECAR", output.with_name(output.name + ".sha256"))
    status = tmp_path.stat()
    monkeypatch.setattr(MODULE, "EXPECTED_REPORTS", {
        "st_dev": status.st_dev, "st_ino": status.st_ino,
        "uid": status.st_uid, "gid": status.st_gid,
        "mode": stat.S_IMODE(status.st_mode), "nlink": status.st_nlink,
    })
    monkeypatch.setattr(MODULE, "TERMINAL_REVIEW_GENERATOR_BLOCKED_PENDING_INDEPENDENT_AUDIT", False)
    monkeypatch.setattr(MODULE, "_validate_invocation", lambda: None)
    monkeypatch.setattr(MODULE.os, "geteuid", lambda: 0)
    monkeypatch.setattr(MODULE.os, "getegid", lambda: 0)


def _install_fake_publication_context(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    context = _fake_context()
    monkeypatch.setattr(MODULE, "_build_context", lambda *_args, **_kwargs: context)
    monkeypatch.setattr(MODULE, "_close_context", lambda _context: None)
    monkeypatch.setattr(MODULE, "_canonical_snapshot", lambda *_args, **_kwargs: {"fixed": True})
    return context


def test_source_is_blocked_and_compiles() -> None:
    assert MODULE.TERMINAL_REVIEW_GENERATOR_BLOCKED_PENDING_INDEPENDENT_AUDIT is EXPECTED_GENERATOR_BLOCKED
    assert len(_blocked_source(SOURCE_BYTES)) == BLOCKED_SOURCE_SIZE
    assert _sha(_blocked_source(SOURCE_BYTES)) == BLOCKED_SOURCE_SHA256
    if EXPECTED_GENERATOR_BLOCKED:
        executable = SOURCE_BYTES.replace(
            b"\nTERMINAL_REVIEW_GENERATOR_BLOCKED_PENDING_INDEPENDENT_AUDIT = True\n",
            b"\nTERMINAL_REVIEW_GENERATOR_BLOCKED_PENDING_INDEPENDENT_AUDIT = False\n", 1,
        )
        assert len(executable) == PREDICTED_EXECUTABLE_SOURCE_SIZE
        assert _sha(executable) == PREDICTED_EXECUTABLE_SOURCE_SHA256
    compile(SOURCE_BYTES, str(SOURCE), "exec")


def test_exact_paths_and_schema_match_installer_contract() -> None:
    assert MODULE.OUTPUT.name == "blind-phase-confirmatory-retry4-canonical-control-root-install-terminal-independent-review-v1.json"
    assert MODULE.SCHEMA == "blind-phase-confirmatory-retry4-canonical-control-root-install-terminal-review-v1"
    assert MODULE.CANONICAL == Path("/var/lib/telemetry-yield-confirmatory-v3")
    assert MODULE.EXPECTED_CANONICAL["st_ino"] == 1594248


def test_all_exact_source_authorities_are_frozen() -> None:
    assert len(MODULE.EXPECTED_INPUTS) == 22
    assert MODULE.EXPECTED_INPUTS["timestamp_query"][1:3] == ("09ae91e27e1bdbaabc17267b60021f830615e1e29fe7c94b9ec5de3606dc1b18", 70)
    assert MODULE.EXPECTED_INPUTS["timestamp_response"][1:3] == ("c2f10fa901905a1b01fa8f1709a107a1229533c6370ef5a0acb4ae82fc94afc2", 6008)
    assert MODULE.EXPECTED_INPUTS["tsa_ca"][1] == "ecd9dc38bc3efb7dbd6431f57e29d2f8d6a0f0d211e1464b3fef2cbfe266fcd2"


def test_receipt_and_canonical_identities_are_frozen() -> None:
    assert MODULE.EXPECTED_RECEIPTS["intent"][1:4] == ("7f2d2673617c32ab594812ff6ef212788013f61a8e987cccfbf326404a8b930b", 32112, 1182422)
    assert MODULE.EXPECTED_RECEIPTS["provenance"][1:4] == ("f6e87250904c0c253faad52acb2baf582982a9e2333026c1eaace362092a61a5", 35625, 1182425)
    assert [MODULE.EXPECTED_CHILDREN[name][3] for name in MODULE.EXPECTED_CHILDREN] == [1594309, 1594310, 1594311, 1594312, 1594313]


@pytest.mark.skipif(not EXPECTED_GENERATOR_BLOCKED, reason="blocked-design-only direct gate fixture")
@pytest.mark.parametrize("function", ["build_review", "publish_review"])
def test_direct_build_and_publish_are_blocked_before_reads(monkeypatch: pytest.MonkeyPatch, function: str) -> None:
    monkeypatch.setattr(MODULE, "_build_context", lambda *_a, **_k: pytest.fail("read occurred"))
    with pytest.raises(MODULE.ReviewError, match="blocked"):
        getattr(MODULE, function)(expected_generator_sha256="0" * 64, expected_generator_test_sha256="1" * 64)


def test_main_validates_invocation_then_blocks_before_parser(monkeypatch: pytest.MonkeyPatch) -> None:
    if not EXPECTED_GENERATOR_BLOCKED:
        pytest.skip("blocked-design-only parser fixture")
    calls: list[str] = []
    monkeypatch.setattr(MODULE, "_validate_invocation", lambda: calls.append("invocation"))
    monkeypatch.setattr(MODULE, "parser", lambda: pytest.fail("parser reached"))
    with pytest.raises(MODULE.ReviewError, match="blocked"):
        MODULE.main([])
    assert calls == ["invocation"]


def test_source_and_test_reconstructions_are_exact() -> None:
    installer = MODULE.INSTALLER.read_bytes()
    tests = MODULE.INSTALLER_TEST.read_bytes()
    assert MODULE._source_reconstruction(installer)["replacement_count"] == 1
    assert MODULE._test_reconstruction(tests)["replacement_count"] == 3


def test_candidate_is_canonical_selfhashed_exact_six_keys() -> None:
    document, payload, sidecar, fake = _candidate()
    MODULE._validate_candidate(document, payload, sidecar, fake)
    assert set(document) == {"schema_version", "status", "reviewed_bindings", "checks", "severity_counts", "review_payload_sha256"}
    assert MODULE._selfhash(document, "review_payload_sha256") == document["review_payload_sha256"]
    assert sidecar == f"{_sha(payload)}  {MODULE.OUTPUT.name}\n".encode()


@pytest.mark.parametrize("mutation", ["schema", "status", "severity", "selfhash", "sidecar"])
def test_candidate_mutations_are_rejected(mutation: str) -> None:
    document, payload, sidecar, fake = _candidate()
    changed = dict(document)
    if mutation == "schema":
        changed["schema_version"] = "wrong"
    elif mutation == "status":
        changed["status"] = "NO_GO"
    elif mutation == "severity":
        changed["severity_counts"] = {"P0": 1, "P1": 0, "P2": 0}
    elif mutation == "selfhash":
        changed["review_payload_sha256"] = "0" * 64
    elif mutation == "sidecar":
        sidecar = b"wrong\n"
    with pytest.raises(MODULE.ReviewError):
        MODULE._validate_candidate(changed, MODULE._canonical(changed) if mutation != "sidecar" else payload, sidecar, fake)


def test_lock_is_exact_exclusive_nonblocking(monkeypatch: pytest.MonkeyPatch) -> None:
    called: list[tuple[int, int]] = []
    monkeypatch.setattr(MODULE, "TERMINAL_REVIEW_GENERATOR_BLOCKED_PENDING_INDEPENDENT_AUDIT", False)
    monkeypatch.setattr(MODULE, "_validate_invocation", lambda: None)
    monkeypatch.setattr(MODULE.fcntl, "flock", lambda fd, flags: called.append((fd, flags)))
    read_fd, write_fd = os.pipe()
    try:
        opened = os.fstat(read_fd)
        MODULE._acquire_intent_lock({"intent": {"fd": read_fd, "opened": opened}})
    finally:
        os.close(read_fd)
        os.close(write_fd)
    assert called == [(read_fd, MODULE.fcntl.LOCK_EX | MODULE.fcntl.LOCK_NB)]


def _install_temporal_mocks(monkeypatch: pytest.MonkeyPatch, snapshots: list[dict[str, object]], events: list[str]) -> None:
    fake_module = types.SimpleNamespace(
        _terminal_review_bindings=lambda *_args: {"binding": True},
        expected_terminal_review_checks=lambda: {"check": True},
    )
    monkeypatch.setattr(MODULE, "TERMINAL_REVIEW_GENERATOR_BLOCKED_PENDING_INDEPENDENT_AUDIT", False)
    monkeypatch.setattr(MODULE, "_validate_invocation", lambda: events.append("invocation"))
    monkeypatch.setattr(MODULE, "_load_inputs", lambda *_args: events.append("pin-all-inputs") or {"terminal_review_generator": {"identity": {}}, "terminal_review_generator_tests": {"identity": {}}})
    monkeypatch.setattr(MODULE, "_load_installer", lambda _records: events.append("reverse-then-exec-installer") or fake_module)
    monkeypatch.setattr(MODULE, "_installer_authority", lambda *_args: events.append("validate-installer-review") or {})
    monkeypatch.setattr(MODULE, "_runtime_records", lambda _records: {})
    monkeypatch.setattr(MODULE, "_timestamp_verdict", lambda *_args: events.append("dual-rfc-validation") or {})
    monkeypatch.setattr(MODULE, "_open_receipts", lambda: events.append("pin-receipts") or {"intent": {"identity": {}}})
    monkeypatch.setattr(MODULE, "_acquire_intent_lock", lambda _records: events.append("lock-exclusive-nonblocking"))
    def snapshot(*_args: object) -> dict[str, object]:
        events.append(f"snapshot-{sum(value.startswith('snapshot-') for value in events) + 1}")
        return snapshots.pop(0)
    monkeypatch.setattr(MODULE, "_canonical_snapshot", snapshot)
    monkeypatch.setattr(MODULE, "_revalidate_records", lambda value: events.append("revalidate-receipts" if "intent" in value else "revalidate-inputs"))
    monkeypatch.setattr(MODULE, "_close_records", lambda value: events.append("unlock-close-receipts" if "intent" in value else "close-inputs"))


def _temporal_snapshot(marker: int) -> dict[str, object]:
    return {
        "marker": marker, "quarantined": {}, "intent_pair": ({}, {}),
        "provenance_pair": ({}, {}), "canonical_identity": {},
        "installed": [], "disjoint": {}, "mount_state": {},
    }


def test_temporal_order_pins_then_reconstructs_then_locks_three_scans_and_closes(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[str] = []
    _install_temporal_mocks(monkeypatch, [_temporal_snapshot(1), _temporal_snapshot(1), _temporal_snapshot(1)], events)
    MODULE.build_review(expected_generator_sha256="0" * 64, expected_generator_test_sha256="1" * 64)
    assert events == [
        "invocation", "pin-all-inputs", "reverse-then-exec-installer",
        "validate-installer-review", "dual-rfc-validation", "pin-receipts",
        "lock-exclusive-nonblocking", "snapshot-1", "snapshot-2",
        "revalidate-inputs", "revalidate-receipts", "snapshot-3",
        "unlock-close-receipts", "close-inputs",
    ]


@pytest.mark.parametrize("snapshots", [
    [_temporal_snapshot(1), _temporal_snapshot(2), _temporal_snapshot(1)],
    [_temporal_snapshot(1), _temporal_snapshot(1), _temporal_snapshot(3)],
])
def test_second_or_final_s4_scan_drift_rejects_and_unlocks(monkeypatch: pytest.MonkeyPatch, snapshots: list[dict[str, object]]) -> None:
    events: list[str] = []
    _install_temporal_mocks(monkeypatch, [dict(value) for value in snapshots], events)
    with pytest.raises(MODULE.ReviewError):
        MODULE.build_review(expected_generator_sha256="0" * 64, expected_generator_test_sha256="1" * 64)
    assert "unlock-close-receipts" in events and events[-1] == "close-inputs"


def test_context_close_releases_exact_intent_lock_and_all_fds(tmp_path: Path) -> None:
    lock_path = tmp_path / "intent"
    lock_path.write_bytes(b"intent")
    lock_fd = os.open(lock_path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    source_fd = os.open(SOURCE, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    MODULE.fcntl.flock(lock_fd, MODULE.fcntl.LOCK_EX | MODULE.fcntl.LOCK_NB)
    MODULE._close_context({"receipts": {"intent": {"fd": lock_fd}}, "records": {"source": {"fd": source_fd}}})
    for descriptor in (lock_fd, source_fd):
        with pytest.raises(OSError):
            os.fstat(descriptor)
    contender = os.open(lock_path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    try:
        MODULE.fcntl.flock(contender, MODULE.fcntl.LOCK_EX | MODULE.fcntl.LOCK_NB)
    finally:
        os.close(contender)


def test_publisher_uses_sidecar_then_main(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(MODULE, "TERMINAL_REVIEW_GENERATOR_BLOCKED_PENDING_INDEPENDENT_AUDIT", False)
    monkeypatch.setattr(MODULE, "_validate_invocation", lambda: None)
    monkeypatch.setattr(MODULE.os, "geteuid", lambda: 0)
    monkeypatch.setattr(MODULE.os, "getegid", lambda: 0)
    context = _fake_context()
    order: list[str] = []
    monkeypatch.setattr(MODULE, "_build_context", lambda *_a, **_k: context)
    monkeypatch.setattr(MODULE, "_close_context", lambda _context: None)
    monkeypatch.setattr(MODULE, "_open_reports", lambda: (99, object()))
    monkeypatch.setattr(MODULE, "_read_at", lambda *_a, **_k: None if len(order) < 2 else (context["sidecar"] if _a[1].name.endswith(".sha256") else context["payload"], {"st_dev": 1, "st_ino": 2 if _a[1].name.endswith(".sha256") else 3}))
    monkeypatch.setattr(MODULE, "_ensure_fragment", lambda _fd, path, *_a: order.append(path.name) or {"path": str(path)})
    monkeypatch.setattr(MODULE, "_revalidate_reports", lambda *_a: None)
    monkeypatch.setattr(MODULE, "_revalidate_records", lambda *_a: None)
    monkeypatch.setattr(MODULE, "_canonical_snapshot", lambda *_a: {"fixed": True})
    monkeypatch.setattr(MODULE.os, "close", lambda _fd: None)
    MODULE.publish_review(expected_generator_sha256="0" * 64, expected_generator_test_sha256="1" * 64)
    assert order == [MODULE.SIDECAR.name, MODULE.OUTPUT.name]


@pytest.mark.skipif(os.geteuid() != 0, reason="root-only O_TMPFILE publication integration")
@pytest.mark.parametrize("initial", ["fresh", "sidecar", "pair"])
def test_root_tmp_publication_fresh_resume_and_idempotent(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, initial: str) -> None:
    _patch_tmp_reports(monkeypatch, tmp_path)
    context = _install_fake_publication_context(monkeypatch)
    if initial in {"sidecar", "pair"}:
        MODULE.SIDECAR.write_bytes(context["sidecar"])
        os.chown(MODULE.SIDECAR, 0, 0)
        os.chmod(MODULE.SIDECAR, 0o444)
    if initial == "pair":
        MODULE.OUTPUT.write_bytes(context["payload"])
        os.chown(MODULE.OUTPUT, 0, 0)
        os.chmod(MODULE.OUTPUT, 0o444)
    document, payload, sidecar, receipt = MODULE.publish_review(expected_generator_sha256="0" * 64, expected_generator_test_sha256="1" * 64)
    assert document == context["document"]
    assert MODULE.OUTPUT.read_bytes() == payload and MODULE.SIDECAR.read_bytes() == sidecar
    assert receipt["publication_order"] == ["sidecar", "main"]
    assert MODULE.OUTPUT.stat().st_ino != MODULE.SIDECAR.stat().st_ino


@pytest.mark.skipif(os.geteuid() != 0, reason="root-only collision integration")
@pytest.mark.parametrize("initial", ["main_only", "foreign_sidecar", "foreign_pair"])
def test_root_tmp_publication_rejects_impossible_or_foreign_state(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, initial: str) -> None:
    _patch_tmp_reports(monkeypatch, tmp_path)
    _install_fake_publication_context(monkeypatch)
    target = MODULE.OUTPUT if initial == "main_only" else MODULE.SIDECAR
    target.write_bytes(b"foreign\n")
    os.chown(target, 0, 0)
    os.chmod(target, 0o444)
    if initial == "foreign_pair":
        MODULE.OUTPUT.write_bytes(b"foreign-main\n")
        os.chown(MODULE.OUTPUT, 0, 0)
        os.chmod(MODULE.OUTPUT, 0o444)
    with pytest.raises(MODULE.ReviewError):
        MODULE.publish_review(expected_generator_sha256="0" * 64, expected_generator_test_sha256="1" * 64)


class InjectedBoundaryFailure(RuntimeError):
    pass


@pytest.mark.skipif(os.geteuid() != 0, reason="root-only durable fragment fault matrix")
@pytest.mark.parametrize("fragment", ["sidecar", "main"])
@pytest.mark.parametrize("boundary", ["write", "fchown", "fchmod", "inode_fsync", "linkat", "parent_fsync", "readback"])
def test_root_fragment_fault_matrix_is_absent_or_exact_and_resumable(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fragment: str, boundary: str) -> None:
    _patch_tmp_reports(monkeypatch, tmp_path)
    payload = b"fragment-payload\n"
    path = tmp_path / ("fragment.sha256" if fragment == "sidecar" else "fragment.json")
    directory = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW)
    real_write, real_fchown, real_fchmod, real_fsync = MODULE.os.write, MODULE.os.fchown, MODULE.os.fchmod, MODULE.os.fsync
    real_cdll, real_read_at = MODULE.ctypes.CDLL, MODULE._read_at
    events: list[str] = []

    def after(name: str, function: object, *arguments: object) -> object:
        result = function(*arguments)
        events.append(name)
        if boundary == name:
            raise InjectedBoundaryFailure(name)
        return result

    monkeypatch.setattr(MODULE.os, "write", lambda *args: after("write", real_write, *args))
    monkeypatch.setattr(MODULE.os, "fchown", lambda *args: after("fchown", real_fchown, *args))
    monkeypatch.setattr(MODULE.os, "fchmod", lambda *args: after("fchmod", real_fchmod, *args))
    monkeypatch.setattr(MODULE.os, "fsync", lambda fd: after("parent_fsync" if fd == directory else "inode_fsync", real_fsync, fd))

    real_library = real_cdll(None, use_errno=True)
    real_linkat = real_library.linkat

    class WrappedLinkat:
        argtypes = None
        restype = None

        def __call__(self, *arguments: object) -> int:
            real_linkat.argtypes = self.argtypes
            real_linkat.restype = self.restype
            return int(after("linkat", real_linkat, *arguments))

    class WrappedLibrary:
        linkat = WrappedLinkat()

    monkeypatch.setattr(MODULE.ctypes, "CDLL", lambda *_args, **_kwargs: WrappedLibrary())

    def readback(*args: object, **kwargs: object) -> object:
        result = real_read_at(*args, **kwargs)
        events.append("readback")
        if boundary == "readback":
            raise InjectedBoundaryFailure("readback")
        return result

    monkeypatch.setattr(MODULE, "_read_at", readback)
    try:
        descriptor_count = len(os.listdir("/proc/self/fd"))
        with pytest.raises(InjectedBoundaryFailure, match=boundary):
            MODULE._publish_one(directory, path, payload)
        assert len(os.listdir("/proc/self/fd")) == descriptor_count
        expected_events = ["write", "fchown", "fchmod", "inode_fsync", "linkat", "parent_fsync", "readback"]
        assert events == expected_events[:expected_events.index(boundary) + 1]
        expected_linked = boundary in {"linkat", "parent_fsync", "readback"}
        assert path.exists() is expected_linked
        if path.exists():
            assert path.read_bytes() == payload
        monkeypatch.setattr(MODULE.os, "write", real_write)
        monkeypatch.setattr(MODULE.os, "fchown", real_fchown)
        monkeypatch.setattr(MODULE.os, "fchmod", real_fchmod)
        monkeypatch.setattr(MODULE.os, "fsync", real_fsync)
        monkeypatch.setattr(MODULE.ctypes, "CDLL", real_cdll)
        monkeypatch.setattr(MODULE, "_read_at", real_read_at)
        identity = MODULE._ensure_fragment(directory, path, payload, 4096)
        assert identity["sha256"] == _sha(payload)
        assert path.read_bytes() == payload
        assert stat.S_IMODE(path.stat().st_mode) == 0o444 and path.stat().st_nlink == 1
    finally:
        os.close(directory)


@pytest.mark.skipif(os.geteuid() != 0, reason="root-only EEXIST durability replay")
@pytest.mark.parametrize("foreign", [False, True])
def test_root_linkat_eexist_race_replays_exact_or_rejects_foreign_untouched(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, foreign: bool) -> None:
    _patch_tmp_reports(monkeypatch, tmp_path)
    path = tmp_path / "race-fragment"
    expected = b"exact\n"
    raced = b"foreign\n" if foreign else expected
    directory = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW)
    real_publish = MODULE._publish_one
    real_fsync = MODULE.os.fsync
    syncs: list[int] = []

    def raced_publish(_directory: int, _path: Path, _payload: bytes) -> dict[str, object]:
        path.write_bytes(raced)
        os.chown(path, 0, 0)
        os.chmod(path, 0o444)
        raise FileExistsError(path.name)

    monkeypatch.setattr(MODULE, "_publish_one", raced_publish)
    monkeypatch.setattr(MODULE.os, "fsync", lambda fd: syncs.append(fd) or real_fsync(fd))
    try:
        if foreign:
            with pytest.raises(MODULE.ReviewError, match="differs"):
                MODULE._ensure_fragment(directory, path, expected, 4096)
            assert path.read_bytes() == raced and syncs == []
        else:
            identity = MODULE._ensure_fragment(directory, path, expected, 4096)
            assert identity["sha256"] == _sha(expected)
            assert len(syncs) == 2 and directory in syncs
    finally:
        monkeypatch.setattr(MODULE, "_publish_one", real_publish)
        os.close(directory)


@pytest.mark.skipif(os.geteuid() != 0, reason="root-only full publisher crash-prefix integration")
@pytest.mark.parametrize("boundary,expected_state", [("before_sidecar", "F"), ("after_sidecar", "S"), ("after_main", "T")])
def test_root_full_publisher_crash_prefixes_are_only_f_s_t_and_resume(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, boundary: str, expected_state: str) -> None:
    _patch_tmp_reports(monkeypatch, tmp_path)
    _install_fake_publication_context(monkeypatch)
    real_ensure = MODULE._ensure_fragment
    calls = 0

    def failing(directory: int, path: Path, payload: bytes, maximum: int) -> dict[str, object]:
        nonlocal calls
        calls += 1
        if boundary == "before_sidecar" and calls == 1:
            raise InjectedBoundaryFailure(boundary)
        result = real_ensure(directory, path, payload, maximum)
        if (boundary == "after_sidecar" and calls == 1) or (boundary == "after_main" and calls == 2):
            raise InjectedBoundaryFailure(boundary)
        return result

    monkeypatch.setattr(MODULE, "_ensure_fragment", failing)
    with pytest.raises(InjectedBoundaryFailure, match=boundary):
        MODULE.publish_review(expected_generator_sha256="0" * 64, expected_generator_test_sha256="1" * 64)
    observed = (MODULE.SIDECAR.exists(), MODULE.OUTPUT.exists())
    assert observed == {"F": (False, False), "S": (True, False), "T": (True, True)}[expected_state]
    monkeypatch.setattr(MODULE, "_ensure_fragment", real_ensure)
    _document, payload, sidecar, _receipt = MODULE.publish_review(expected_generator_sha256="0" * 64, expected_generator_test_sha256="1" * 64)
    assert MODULE.SIDECAR.read_bytes() == sidecar and MODULE.OUTPUT.read_bytes() == payload


@pytest.mark.skipif(os.geteuid() != 0, reason="root-only exact intent-lock lifetime integration")
def test_root_intent_lock_covers_both_links_and_final_scan_then_releases(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _patch_tmp_reports(monkeypatch, tmp_path)
    real_ensure = MODULE._ensure_fragment
    real_snapshot = MODULE._canonical_snapshot
    probes: list[str] = []

    def assert_contended(label: str) -> None:
        descriptor = os.open(MODULE.INTENT, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
        try:
            with pytest.raises(BlockingIOError):
                MODULE.fcntl.flock(descriptor, MODULE.fcntl.LOCK_EX | MODULE.fcntl.LOCK_NB)
            probes.append(label)
        finally:
            os.close(descriptor)

    def held_ensure(directory: int, path: Path, payload: bytes, maximum: int) -> dict[str, object]:
        assert_contended(path.name)
        return real_ensure(directory, path, payload, maximum)

    def held_snapshot(*args: object, **kwargs: object) -> dict[str, object]:
        assert_contended("final-snapshot")
        return real_snapshot(*args, **kwargs)

    monkeypatch.setattr(MODULE, "_ensure_fragment", held_ensure)
    monkeypatch.setattr(MODULE, "_canonical_snapshot", held_snapshot)
    MODULE.publish_review(expected_generator_sha256=EXPECTED_SOURCE_SHA256, expected_generator_test_sha256=_sha(TEST_BYTES))
    assert probes[-3:] == [MODULE.SIDECAR.name, MODULE.OUTPUT.name, "final-snapshot"]
    contender = os.open(MODULE.INTENT, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    try:
        MODULE.fcntl.flock(contender, MODULE.fcntl.LOCK_EX | MODULE.fcntl.LOCK_NB)
    finally:
        os.close(contender)


def test_publication_boundaries_gate_direct_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    if not EXPECTED_GENERATOR_BLOCKED:
        pytest.skip("blocked-design-only mutation-boundary fixture")
    monkeypatch.setattr(MODULE, "TERMINAL_REVIEW_GENERATOR_BLOCKED_PENDING_INDEPENDENT_AUDIT", True)
    for function, arguments in (
        (MODULE._publish_one, (0, MODULE.OUTPUT, b"x")),
        (MODULE._ensure_fragment, (0, MODULE.OUTPUT, b"x", 1)),
    ):
        with pytest.raises(MODULE.ReviewError, match="blocked"):
            function(*arguments)


def test_source_has_no_network_lifecycle_iq_or_outcome_surface() -> None:
    imports = {alias.name.split(".")[0] for node in SOURCE_TREE.body if isinstance(node, (ast.Import, ast.ImportFrom)) for alias in node.names}
    assert not imports & {"socket", "subprocess", "urllib", "requests", "http", "ssl"}
    forbidden = {"unshare", "mount", "umount", "kill", "Popen", "run", "check_call", "check_output", "unlink", "remove", "rename", "rmtree", "copy", "copyfile"}
    calls = {node.func.attr if isinstance(node.func, ast.Attribute) else node.func.id if isinstance(node.func, ast.Name) else "<dynamic>" for node in ast.walk(SOURCE_TREE) if isinstance(node, ast.Call)}
    assert not calls & forbidden
    lowered = SOURCE_BYTES.lower()
    assert b"--request" not in lowered and b"start-persistent-namespace" not in lowered and b"iq_content" not in lowered


def test_bounded_exact_effect_surface_is_frozen() -> None:
    result = _bounded_effect_surface(SOURCE_BYTES)
    assert result["top_level_function_count"] == 40
    assert result["call_count"] == 399
    assert result["mutation_call_count"] == 9


@pytest.mark.parametrize("mutant", [
    b"\ndef rogue():\n os.system('id')\n",
    b"\ndef rogue():\n danger=os.unlink\n danger('/tmp/x')\n",
    b"\nfrom os import unlink as danger\ndef rogue(): danger('/tmp/x')\n",
    b"\ndef rogue(): getattr(os,'unlink')('/tmp/x')\n",
    b"\ndef rogue(): (lambda f:f('/tmp/x'))(os.unlink)\n",
    b"\ndef rogue(): __import__('os').system('id')\n",
    b"\ndef rogue(): Path('/tmp/x').write_bytes(b'x')\n",
    b"\ndef rogue(): exec('pass')\n",
    b"\ndef rogue(): publish_review(expected_generator_sha256='0'*64,expected_generator_test_sha256='1'*64)\n",
    b"\ndef _simple(open=os.system): return open('id')\n",
    b"\nlinkat = open\n",
])
def test_bounded_effect_surface_rejects_alias_dynamic_and_extra_effect_mutants(mutant: bytes) -> None:
    with pytest.raises((AssertionError, SyntaxError)):
        _bounded_effect_surface(SOURCE_BYTES + mutant)


def test_all_mutation_boundaries_have_direct_authority_gate() -> None:
    functions = {node.name: node for node in SOURCE_TREE.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
    for name in ("_publish_one", "_ensure_fragment", "publish_review"):
        first = functions[name].body[0]
        assert isinstance(first, ast.Expr) and isinstance(first.value, ast.Call)
        assert isinstance(first.value.func, ast.Name) and first.value.func.id == "_require_publication_authority"


def test_exact_publication_mutation_surface_and_flags() -> None:
    functions = {node.name: node for node in SOURCE_TREE.body if isinstance(node, ast.FunctionDef)}
    owner: dict[int, str] = {}
    for name, function in functions.items():
        for node in ast.walk(function):
            owner[id(node)] = name
    calls = [node for node in ast.walk(SOURCE_TREE) if isinstance(node, ast.Call)]
    write_opens = []
    mutators: list[tuple[str, str]] = []
    for call in calls:
        tail = call.func.attr if isinstance(call.func, ast.Attribute) else call.func.id if isinstance(call.func, ast.Name) else "<dynamic>"
        if tail in {"write", "fchown", "fchmod", "fsync", "linkat"}:
            mutators.append((owner.get(id(call), "module"), tail))
        if tail == "open" and isinstance(call.func, ast.Attribute) and isinstance(call.func.value, ast.Name) and call.func.value.id == "os":
            flags = ast.dump(call.args[1], include_attributes=False) if len(call.args) > 1 else ""
            if "O_RDWR" in flags or "O_WRONLY" in flags or "O_CREAT" in flags or "O_TMPFILE" in flags:
                write_opens.append((owner.get(id(call), "module"), flags))
    assert write_opens == [("_publish_one", "BinOp(left=BinOp(left=Attribute(value=Name(id='os', ctx=Load()), attr='O_RDWR', ctx=Load()), op=BitOr(), right=Attribute(value=Name(id='os', ctx=Load()), attr='O_CLOEXEC', ctx=Load())), op=BitOr(), right=Attribute(value=Name(id='os', ctx=Load()), attr='O_TMPFILE', ctx=Load()))")]
    assert all(function in {"_publish_one", "_ensure_fragment"} for function, _name in mutators)
    assert sum(name == "linkat" for _function, name in mutators) == 1


def test_only_publish_branch_reaches_publisher() -> None:
    functions = {node.name: node for node in SOURCE_TREE.body if isinstance(node, ast.FunctionDef)}
    main = functions["main"]
    calls = [(node.func.id, node.lineno) for node in ast.walk(main) if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)]
    assert sum(name == "publish_review" for name, _line in calls) == 1
    assert sum(name == "build_review" for name, _line in calls) == 1


@pytest.mark.skipif(os.geteuid() != 0, reason="root-only installer S5 parser integration")
def test_root_tmp_exact_candidate_is_accepted_by_installer_s5_parser(tmp_path: Path) -> None:
    script = _held_exact_script(f"""
g.TERMINAL_REVIEW_GENERATOR_BLOCKED_PENDING_INDEPENDENT_AUDIT=False
c=g._build_context({EXPECTED_SOURCE_SHA256!r},test_sha256)
try:
 d=pathlib.Path({str(tmp_path)!r}); main=d/g.OUTPUT.name; side=d/g.SIDECAR.name
 main.write_bytes(c['payload']); side.write_bytes(c['sidecar']); os.chown(main,0,0); os.chown(side,0,0); os.chmod(main,0o444); os.chmod(side,0o444)
 m=c['module']
 fd=os.open(d,os.O_RDONLY|os.O_DIRECTORY|os.O_CLOEXEC|os.O_NOFOLLOW)
 try:
  s=c['snapshot']; result=m._terminal_review_state(fd,{{'review':hashlib.sha256(c['payload']).hexdigest(),'generator':{EXPECTED_SOURCE_SHA256!r},'generator_tests':test_sha256}},c['runtime'],c['verdict'],s['quarantined'],c['installer_authority'],s['intent_pair'],s['provenance_pair'],s['canonical_identity'],s['installed'],s['disjoint'])
  assert result['state']=='PAIR_COMMITTED_GO'
 finally: os.close(fd)
finally: g._close_context(c)
""")
    result = subprocess.run(["/usr/bin/python3.12", "-I", "-S", "-B", "-c", script], text=True, capture_output=True, timeout=120, check=False)
    assert result.returncode == 0, result.stderr


@pytest.mark.skipif(os.geteuid() != 0, reason="root-only exact live read-only fixed-point")
def test_root_exact_live_two_pass_build_is_deterministic_and_output_absent() -> None:
    script = _held_exact_script(f"""
g.TERMINAL_REVIEW_GENERATOR_BLOCKED_PENDING_INDEPENDENT_AUDIT=False
a=g.build_review(expected_generator_sha256={EXPECTED_SOURCE_SHA256!r},expected_generator_test_sha256=test_sha256)
built=g.build_review(expected_generator_sha256={EXPECTED_SOURCE_SHA256!r},expected_generator_test_sha256=test_sha256)
assert a==built
assert len(a[0])==6 and len(a[0]['reviewed_bindings'])==15 and len(a[0]['checks'])==14
assert not g.OUTPUT.exists() and not g.SIDECAR.exists()
print(hashlib.sha256(a[1]).hexdigest(),len(a[1]),hashlib.sha256(a[2]).hexdigest())
""")
    result = subprocess.run(["/usr/bin/python3.12", "-I", "-S", "-B", "-c", script], text=True, capture_output=True, timeout=120, check=False)
    assert result.returncode == 0, result.stderr
    assert len(result.stdout.strip().split()) == 3


@pytest.mark.skipif(os.geteuid() != 0, reason="root-only exact virtual executable default CLI")
def test_root_exact_virtual_executable_default_cli_is_read_only() -> None:
    script = _held_exact_script(f"""
g.TERMINAL_REVIEW_GENERATOR_BLOCKED_PENDING_INDEPENDENT_AUDIT=False
raise SystemExit(g.main(['--expected-generator-sha256',{EXPECTED_SOURCE_SHA256!r},'--expected-generator-test-sha256',test_sha256]))
""")
    result = subprocess.run(["/usr/bin/python3.12", "-I", "-S", "-B", "-c", script], text=True, capture_output=True, timeout=120, check=False)
    assert result.returncode == 0, result.stderr
    receipt = json.loads(result.stdout)
    assert receipt["status"] == "PASS_RETRY4_TERMINAL_REVIEW_CANDIDATE"
    assert receipt["published"] is None
    assert not MODULE.OUTPUT.exists() and not MODULE.SIDECAR.exists()


def test_tampered_executable_input_is_rejected_before_execution(tmp_path: Path) -> None:
    sentinel = tmp_path / "sentinel"
    tampered = MODULE.INSTALLER.read_bytes() + f"\nopen({str(sentinel)!r},'w').write('bad')\n".encode()
    records = {
        "installer": {"payload": tampered},
        "installer_tests": {"payload": MODULE.INSTALLER_TEST.read_bytes()},
    }
    with pytest.raises(MODULE.ReviewError, match="reconstruction"):
        MODULE._load_installer(records)
    assert not sentinel.exists()
