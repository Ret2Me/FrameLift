from __future__ import annotations

import ast
import hashlib
import json
import os
from pathlib import Path
import stat
import types

import pytest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "work/blind-phase-confirmatory-v2/build_retry4_canonical_control_root_installer_independent_review_v1.py"
EXPECTED_SOURCE_SHA256 = "2563916e4ccbb00e045dee5815db603d728b3e97438bbc9dd32620bf16142bc6"
EXPECTED_SOURCE_SIZE = 32067
EXPECTED_GENERATOR_BLOCKED = False
EXPECTED_EXECUTABLE_INSTALLER_SHA256 = "bebbe189bc8f0100533dfc560a0fca446fcb159315ccc77096ae31e3ef6d7fe0"
EXPECTED_EXECUTABLE_INSTALLER_SIZE = 87195
EXPECTED_EXECUTABLE_INSTALLER_TEST_SHA256 = "5eb6e2e5ee25850943ebe24300977b77c47c03aedd95b3807369cddceab1238b"
EXPECTED_EXECUTABLE_INSTALLER_TEST_SIZE = 63784
EXPECTED_BLOCKED_GENERATOR_SHA256 = "cf80041a039159a646b6245c01d6c81b127c4a66fffc0ef6544e8e8c34fcc4f7"
EXPECTED_BLOCKED_GENERATOR_SIZE = 31940
SOURCE_BYTES = SOURCE.read_bytes()


def _verified_module(payload: bytes = SOURCE_BYTES) -> types.ModuleType:
    if payload is SOURCE_BYTES:
        assert len(payload) == EXPECTED_SOURCE_SIZE
        assert hashlib.sha256(payload).hexdigest() == EXPECTED_SOURCE_SHA256
    tree = ast.parse(payload, filename=str(SOURCE))
    module = types.ModuleType("verified_retry4_installer_review_generator")
    module.__file__ = str(SOURCE)
    module.__package__ = None
    exec(compile(tree, str(SOURCE), "exec"), module.__dict__)
    return module


def _owner_map(tree: ast.AST) -> dict[ast.AST, ast.AST]:
    result: dict[ast.AST, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            result[child] = parent
    return result


def _owner(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> str:
    current = parents.get(node)
    while current is not None:
        if isinstance(current, ast.FunctionDef):
            return current.name
        current = parents.get(current)
    return "<module>"


def _static_surface(payload: bytes) -> dict[str, object]:
    tree = ast.parse(payload)
    parents = _owner_map(tree)
    imports = [
        ast.unparse(node) for node in tree.body
        if isinstance(node, (ast.Import, ast.ImportFrom))
    ]
    if imports != [
        "from __future__ import annotations", "import argparse", "import ctypes",
        "import errno", "import hashlib", "import json", "import os",
        "from pathlib import Path", "import stat", "import sys", "import types",
        "from typing import Mapping, Sequence",
    ]:
        raise AssertionError("exact import surface differs")
    functions = [node.name for node in tree.body if isinstance(node, ast.FunctionDef)]
    if len(functions) != 30 or len(functions) != len(set(functions)):
        raise AssertionError("duplicate top-level function")
    forbidden = {
        "unlink", "remove", "rmdir", "removedirs", "rmtree", "rename",
        "replace", "copy", "copy2", "copytree", "symlink", "truncate",
        "ftruncate", "pwrite", "write_text", "write_bytes", "touch",
        "fork", "unshare", "setns", "mount", "umount", "kill", "Popen",
        "run", "system", "execve", "spawn", "socket", "urlopen", "request",
        "getattr", "setattr", "delattr", "__import__", "eval", "globals",
        "locals", "vars",
    }
    protected = {
        "build_review", "publish_review", "_prepare", "_publish_one",
        "_ensure_fragment", "_source_unblock_reconstruction",
        "_test_unblock_reconstruction", "main",
    }
    mutations: list[str] = []
    write_opens: list[str] = []
    execs: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.decorator_list:
            raise AssertionError("executable decorator")
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.NamedExpr)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                for name in (item.id for item in ast.walk(target) if isinstance(item, ast.Name)):
                    if name in protected and _owner(node, parents) == "<module>":
                        raise AssertionError("protected callable rebound")
            value = node.value
            if isinstance(value, ast.Name) and value.id in forbidden:
                raise AssertionError("forbidden callable alias")
            if isinstance(value, ast.Attribute) and value.attr in forbidden:
                raise AssertionError("forbidden attribute alias")
        if not isinstance(node, ast.Call):
            continue
        call_owner = _owner(node, parents)
        if isinstance(node.func, ast.Name):
            exact_safe_path = (
                node.func.id == "getattr" and call_owner == "_validate_invocation"
                and ast.unparse(node) == "getattr(flags, 'safe_path', False)"
            )
            if (node.func.id in forbidden and not exact_safe_path) or node.func.id == "open":
                raise AssertionError("forbidden direct call")
            if node.func.id == "exec":
                execs.append(f"{call_owner}:{ast.unparse(node)}")
            if node.func.id == "linkat":
                mutations.append(f"{call_owner}:{ast.unparse(node)}")
        elif isinstance(node.func, ast.Attribute):
            exact_reverse = (
                node.func.attr == "replace"
                and (
                    (
                        call_owner == "_source_unblock_reconstruction"
                        and ast.unparse(node) == "payload.replace(executable, blocked, 1)"
                    )
                    or (
                        call_owner == "_test_unblock_reconstruction"
                        and ast.unparse(node) == "reconstructed.replace(executable, blocked, 1)"
                    )
                )
            )
            if (node.func.attr in forbidden and not exact_reverse) or (node.func.attr == "open" and not (
                isinstance(node.func.value, ast.Name) and node.func.value.id == "os"
            )):
                raise AssertionError("forbidden attribute call")
            if isinstance(node.func.value, ast.Name):
                module, name = node.func.value.id, node.func.attr
                if module == "os" and name == "open":
                    text = ast.unparse(node)
                    if any(flag in text for flag in ("O_WRONLY", "O_RDWR", "O_CREAT", "O_TRUNC", "O_APPEND", "O_TMPFILE")):
                        write_opens.append(f"{call_owner}:{text}")
                if module == "os" and name in {"write", "fchown", "fchmod", "fsync"}:
                    mutations.append(f"{call_owner}:{ast.unparse(node)}")
        else:
            raise AssertionError("dynamic call target")
    expected_write_opens = [
        "_publish_one:os.open('.', os.O_RDWR | os.O_CLOEXEC | os.O_TMPFILE, 384, dir_fd=directory)"
    ]
    expected_execs = [
        "_load_installer:exec(compile(payload, str(INSTALLER), 'exec'), module.__dict__)"
    ]
    if write_opens != expected_write_opens or execs != expected_execs:
        raise AssertionError("exact write-open or exec surface differs")
    return {
        "function_count": len(functions),
        "call_count": sum(isinstance(node, ast.Call) for node in ast.walk(tree)),
        "mutations": sorted(mutations), "write_opens": write_opens,
        "execs": execs,
    }


def _repo_file(path: Path, payload: bytes) -> None:
    path.write_bytes(payload)
    path.chmod(0o664)
    if os.geteuid() == 0:
        os.chown(path, 1000, 1000)


def _virtual_context(monkeypatch: pytest.MonkeyPatch, module: types.ModuleType, tmp_path: Path) -> tuple[str, str]:
    installer_source = module.INSTALLER.read_bytes()
    installer_test_source = module.INSTALLER_TEST.read_bytes()
    if module.REVIEW_GENERATOR_BLOCKED_PENDING_INDEPENDENT_AUDIT:
        executable = installer_source.replace(
            b"\nINSTALLER_BLOCKED_PENDING_INDEPENDENT_DESIGN_REVIEW = True\n",
            b"\nINSTALLER_BLOCKED_PENDING_INDEPENDENT_DESIGN_REVIEW = False\n", 1,
        )
        assert len(executable) == len(installer_source) + 1
        executable_sha256 = hashlib.sha256(executable).hexdigest()
        executable_test = installer_test_source
        for blocked, replacement in (
            (
                f'EXPECTED_SOURCE_SHA256 = "{hashlib.sha256(installer_source).hexdigest()}"\n'.encode(),
                f'EXPECTED_SOURCE_SHA256 = "{executable_sha256}"\n'.encode(),
            ),
            (
                f"EXPECTED_SOURCE_SIZE = {len(installer_source)}\n".encode(),
                f"EXPECTED_SOURCE_SIZE = {len(executable)}\n".encode(),
            ),
            (b"EXPECTED_INSTALLER_BLOCKED = True\n", b"EXPECTED_INSTALLER_BLOCKED = False\n"),
        ):
            assert executable_test.count(blocked) == 1
            executable_test = executable_test.replace(blocked, replacement, 1)
    else:
        executable = installer_source
        executable_sha256 = hashlib.sha256(executable).hexdigest()
        executable_test = installer_test_source
    installer = tmp_path / module.INSTALLER.name
    installer_test = tmp_path / module.INSTALLER_TEST.name
    _repo_file(installer, executable)
    _repo_file(installer_test, executable_test)
    monkeypatch.setattr(module, "INSTALLER", installer)
    monkeypatch.setattr(module, "INSTALLER_TEST", installer_test)
    monkeypatch.setattr(module, "EXPECTED_EXECUTABLE_INSTALLER_SHA256", executable_sha256)
    monkeypatch.setattr(module, "EXPECTED_EXECUTABLE_INSTALLER_SIZE", len(executable))
    monkeypatch.setattr(module, "EXPECTED_EXECUTABLE_INSTALLER_TEST_SHA256", hashlib.sha256(executable_test).hexdigest())
    monkeypatch.setattr(module, "EXPECTED_EXECUTABLE_INSTALLER_TEST_SIZE", installer_test.stat().st_size)
    monkeypatch.setattr(module, "REVIEW_GENERATOR_BLOCKED_PENDING_INDEPENDENT_AUDIT", False)
    return hashlib.sha256(SOURCE_BYTES).hexdigest(), hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def test_source_is_prehashed_and_matches_expected_transition_state() -> None:
    module = _verified_module()
    assert module.REVIEW_GENERATOR_BLOCKED_PENDING_INDEPENDENT_AUDIT is EXPECTED_GENERATOR_BLOCKED
    assert module.EXPECTED_BLOCKED_INSTALLER_SHA256 == "3ca6c9e1f51f49b4061b7c55cb1f1ea2f0e063eb2f8328bb297404103d86c630"
    assert module.EXPECTED_BLOCKED_INSTALLER_SIZE == 87194
    assert module.EXPECTED_BLOCKED_INSTALLER_TEST_SHA256 == "237168d6c449813b94180dc3bd4ebe3088b6d25ff1ab910cd9a2df46281f8bee"
    assert module.EXPECTED_BLOCKED_INSTALLER_TEST_SIZE == 63783
    if EXPECTED_GENERATOR_BLOCKED:
        assert module.EXPECTED_EXECUTABLE_INSTALLER_SHA256 is None
        assert module.EXPECTED_EXECUTABLE_INSTALLER_SIZE is None
        assert module.EXPECTED_EXECUTABLE_INSTALLER_TEST_SHA256 is None
        assert module.EXPECTED_EXECUTABLE_INSTALLER_TEST_SIZE is None
    else:
        assert module.EXPECTED_EXECUTABLE_INSTALLER_SHA256 == EXPECTED_EXECUTABLE_INSTALLER_SHA256
        assert module.EXPECTED_EXECUTABLE_INSTALLER_SIZE == EXPECTED_EXECUTABLE_INSTALLER_SIZE
        assert module.EXPECTED_EXECUTABLE_INSTALLER_TEST_SHA256 == EXPECTED_EXECUTABLE_INSTALLER_TEST_SHA256
        assert module.EXPECTED_EXECUTABLE_INSTALLER_TEST_SIZE == EXPECTED_EXECUTABLE_INSTALLER_TEST_SIZE
        reconstructed = SOURCE_BYTES
        for executable, blocked in (
            (b"REVIEW_GENERATOR_BLOCKED_PENDING_INDEPENDENT_AUDIT = False\n", b"REVIEW_GENERATOR_BLOCKED_PENDING_INDEPENDENT_AUDIT = True\n"),
            (f'EXPECTED_EXECUTABLE_INSTALLER_SHA256: str | None = "{EXPECTED_EXECUTABLE_INSTALLER_SHA256}"\n'.encode(), b"EXPECTED_EXECUTABLE_INSTALLER_SHA256: str | None = None\n"),
            (f"EXPECTED_EXECUTABLE_INSTALLER_SIZE: int | None = {EXPECTED_EXECUTABLE_INSTALLER_SIZE}\n".encode(), b"EXPECTED_EXECUTABLE_INSTALLER_SIZE: int | None = None\n"),
            (f'EXPECTED_EXECUTABLE_INSTALLER_TEST_SHA256: str | None = "{EXPECTED_EXECUTABLE_INSTALLER_TEST_SHA256}"\n'.encode(), b"EXPECTED_EXECUTABLE_INSTALLER_TEST_SHA256: str | None = None\n"),
            (f"EXPECTED_EXECUTABLE_INSTALLER_TEST_SIZE: int | None = {EXPECTED_EXECUTABLE_INSTALLER_TEST_SIZE}\n".encode(), b"EXPECTED_EXECUTABLE_INSTALLER_TEST_SIZE: int | None = None\n"),
        ):
            assert reconstructed.count(executable) == 1 and reconstructed.count(blocked) == 0
            reconstructed = reconstructed.replace(executable, blocked, 1)
        assert len(reconstructed) == EXPECTED_BLOCKED_GENERATOR_SIZE
        assert hashlib.sha256(reconstructed).hexdigest() == EXPECTED_BLOCKED_GENERATOR_SHA256


def test_embedded_design_audit_assertion_is_exact() -> None:
    module = _verified_module()
    assert module.EMBEDDED_DESIGN_AUDIT_ASSERTION == {
        "auditor": "retry4_installer_contract",
        "artifact_kind": "embedded_exact_blocked_design_audit_assertion",
        "status": "GO", "severity_counts": {"P0": 0, "P1": 0, "P2": 0},
        "scope": "blocked installer identity and tests are bound separately",
    }
    assert module.EXPECTED_EFFECT_SURFACE == {
        "top_level_function_count": 71, "call_count": 849,
        "mutation_call_count": 23, "write_open_count": 1, "exec_count": 1,
        "mutation_call_sha256": "74c0cbe3ee01c4946e00680110bb00bcaf66531f70dde75eecbf9d89df3d5482",
    }
    assert module.EXPECTED_S0_ASSERTION == {
        "status": "PASS_READ_ONLY_INSTALL_PREFLIGHT", "phase": "S0_FRESH",
        "authority_count": 16, "mount_target_count": 19,
        "fixed_point_sha256": "7889195204b4b3423a52b764d5c3b369ec727fa7e2f39f81ed92a2ffc101b2ce",
        "canonical_control_root_absent": True, "transaction_siblings": [],
        "mutation_performed": False,
    }


def test_source_and_test_reverse_reconstruction_precede_installer_execution() -> None:
    module = _verified_module()
    current_source = module.INSTALLER.read_bytes()
    current_test = module.INSTALLER_TEST.read_bytes()
    if EXPECTED_GENERATOR_BLOCKED:
        blocked_source = current_source
        blocked_test = current_test
        executable_source = blocked_source.replace(
            b"\nINSTALLER_BLOCKED_PENDING_INDEPENDENT_DESIGN_REVIEW = True\n",
            b"\nINSTALLER_BLOCKED_PENDING_INDEPENDENT_DESIGN_REVIEW = False\n", 1,
        )
        executable_sha256 = hashlib.sha256(executable_source).hexdigest()
        executable_test = blocked_test
        for blocked, executable in (
            (
                f'EXPECTED_SOURCE_SHA256 = "{hashlib.sha256(blocked_source).hexdigest()}"\n'.encode(),
                f'EXPECTED_SOURCE_SHA256 = "{executable_sha256}"\n'.encode(),
            ),
            (
                f"EXPECTED_SOURCE_SIZE = {len(blocked_source)}\n".encode(),
                f"EXPECTED_SOURCE_SIZE = {len(executable_source)}\n".encode(),
            ),
            (b"EXPECTED_INSTALLER_BLOCKED = True\n", b"EXPECTED_INSTALLER_BLOCKED = False\n"),
        ):
            assert executable_test.count(blocked) == 1
            executable_test = executable_test.replace(blocked, executable, 1)
    else:
        executable_source = current_source
        executable_sha256 = hashlib.sha256(executable_source).hexdigest()
        blocked_source = executable_source.replace(
            b"\nINSTALLER_BLOCKED_PENDING_INDEPENDENT_DESIGN_REVIEW = False\n",
            b"\nINSTALLER_BLOCKED_PENDING_INDEPENDENT_DESIGN_REVIEW = True\n", 1,
        )
        executable_test = current_test
        blocked_test = executable_test
        for executable, blocked in (
            (
                f'EXPECTED_SOURCE_SHA256 = "{executable_sha256}"\n'.encode(),
                f'EXPECTED_SOURCE_SHA256 = "{hashlib.sha256(blocked_source).hexdigest()}"\n'.encode(),
            ),
            (
                f"EXPECTED_SOURCE_SIZE = {len(executable_source)}\n".encode(),
                f"EXPECTED_SOURCE_SIZE = {len(blocked_source)}\n".encode(),
            ),
            (b"EXPECTED_INSTALLER_BLOCKED = False\n", b"EXPECTED_INSTALLER_BLOCKED = True\n"),
        ):
            assert blocked_test.count(executable) == 1
            blocked_test = blocked_test.replace(executable, blocked, 1)
    source_receipt = module._source_unblock_reconstruction(executable_source)
    test_receipt = module._test_unblock_reconstruction(
        executable_test, executable_sha256, len(executable_source),
    )
    assert source_receipt["all_other_source_bytes_unchanged"] is True
    assert test_receipt["replacement_count"] == 3
    assert test_receipt["all_other_test_bytes_unchanged"] is True
    for malformed in (executable_source + b"x", blocked_source):
        with pytest.raises(module.ReviewError, match="reconstruction|shape"):
            module._source_unblock_reconstruction(malformed)
    for malformed in (executable_test + b"x", blocked_test):
        with pytest.raises(module.ReviewError, match="reconstruction|shape"):
            module._test_unblock_reconstruction(
                malformed, executable_sha256, len(executable_source),
            )
    tree = ast.parse(SOURCE_BYTES)
    prepare = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "_prepare")
    calls = [ast.unparse(node.func) for node in ast.walk(prepare) if isinstance(node, ast.Call)]
    assert calls.index("_source_unblock_reconstruction") < calls.index("_load_installer")
    assert calls.index("_test_unblock_reconstruction") < calls.index("_load_installer")
    assert calls.index("_effect_surface") < calls.index("_load_installer")


def test_blocked_and_executable_states_gate_every_mutation_boundary(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _verified_module()
    actions = (
        lambda: module.publish_review(expected_generator_sha256="0" * 64, expected_generator_test_sha256="1" * 64),
        lambda: module._publish_one(-1, Path("x"), b"x"),
        lambda: module._ensure_fragment(-1, Path("x"), b"x", 1),
    )
    if EXPECTED_GENERATOR_BLOCKED:
        monkeypatch.setattr(module, "_validate_invocation", lambda: None)
        monkeypatch.setattr(module, "parser", lambda: pytest.fail("parser reached"))
        with pytest.raises(module.ReviewError, match="blocked pending audit"):
            module.main([])
        with pytest.raises(module.ReviewError, match="blocked pending audit"):
            module.build_review(
                expected_generator_sha256="0" * 64,
                expected_generator_test_sha256="1" * 64,
            )
        for action in actions:
            with pytest.raises(module.ReviewError, match="blocked pending audit"):
                action()
    else:
        assert module._require_unblocked() is None
        def reject_invocation() -> None:
            raise module.ReviewError("exact /usr/bin/python3.12 test rejection")
        monkeypatch.setattr(module, "_validate_invocation", reject_invocation)
        for action in actions:
            with pytest.raises(module.ReviewError, match="exact /usr/bin/python3.12"):
                action()
        monkeypatch.setattr(module, "REVIEW_GENERATOR_BLOCKED_PENDING_INDEPENDENT_AUDIT", True)
        monkeypatch.setattr(module, "_validate_invocation", lambda: None)
        monkeypatch.setattr(module, "parser", lambda: pytest.fail("parser reached"))
        with pytest.raises(module.ReviewError, match="blocked pending audit"):
            module.main([])


def test_gate_and_explicit_publish_call_order_is_structural() -> None:
    tree = ast.parse(SOURCE_BYTES)
    functions = {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}
    for name in ("_publish_one", "_ensure_fragment", "publish_review"):
        assert ast.unparse(functions[name].body[0]) == "_require_publication_authority()"
    assert ast.unparse(functions["build_review"].body[0]) == "_require_unblocked()"
    assert ast.unparse(functions["main"].body[0]) == "_validate_invocation()"
    assert ast.unparse(functions["main"].body[1]) == "_require_unblocked()"
    main_calls = [ast.unparse(node) for node in ast.walk(functions["main"]) if isinstance(node, ast.Call)]
    assert sum(call.startswith("publish_review(") for call in main_calls) == 1
    publish_if = next(node for node in functions["main"].body if isinstance(node, ast.If))
    assert ast.unparse(publish_if.test) == "args.publish"


def test_static_effect_surface_is_exact_and_no_network_lifecycle_iq() -> None:
    surface = _static_surface(SOURCE_BYTES)
    assert surface["function_count"] == 30
    assert surface["call_count"] == 359
    assert surface["mutations"] == sorted([
        "_publish_one:os.write(descriptor, view)",
        "_publish_one:os.fchown(descriptor, 1000, 1000)",
        "_publish_one:os.fchmod(descriptor, 436)",
        "_publish_one:os.fsync(descriptor)",
        "_publish_one:linkat(descriptor, b'', directory, os.fsencode(path.name), AT_EMPTY_PATH)",
        "_publish_one:os.fsync(directory)",
        "_ensure_fragment:os.fsync(descriptor)",
        "_ensure_fragment:os.fsync(directory)",
    ])
    assert surface["write_opens"] == [
        "_publish_one:os.open('.', os.O_RDWR | os.O_CLOEXEC | os.O_TMPFILE, 384, dir_fd=directory)"
    ]
    assert surface["execs"] == [
        "_load_installer:exec(compile(payload, str(INSTALLER), 'exec'), module.__dict__)"
    ]
    lowered = SOURCE_BYTES.lower()
    for forbidden in (b"http://", b"subprocess", b"os.unshare", b"iq_content_opened"):
        assert forbidden not in lowered


@pytest.mark.parametrize("mutant", [
    "def rogue():\n os.unlink('/tmp/x')\n",
    "def rogue():\n f=os.system\n f('id')\n",
    "from os import unlink as danger\ndef rogue():\n danger('/tmp/x')\n",
    "def rogue():\n getattr(os,'unlink')('/tmp/x')\n",
    "def rogue():\n Path('/tmp/x').write_bytes(b'x')\n",
    "def rogue():\n os.open('/tmp/x', os.O_WRONLY)\n",
    "def rogue():\n exec('pass')\n",
    "publish_review = open\n",
])
def test_static_surface_rejects_effect_mutants(mutant: str) -> None:
    with pytest.raises(AssertionError):
        _static_surface(SOURCE_BYTES + b"\n" + mutant.encode())


def test_parser_defaults_read_only_and_publish_is_explicit() -> None:
    module = _verified_module()
    parser = module.parser()
    with pytest.raises(SystemExit):
        parser.parse_args([])
    args = parser.parse_args([
        "--expected-generator-sha256", "1" * 64,
        "--expected-generator-test-sha256", "2" * 64,
    ])
    assert args.publish is False
    assert parser.parse_args([
        "--expected-generator-sha256", "1" * 64,
        "--expected-generator-test-sha256", "2" * 64, "--publish",
    ]).publish is True


def test_virtual_executable_build_is_deterministic_exact_and_selfhashed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    module = _verified_module()
    generator_sha, generator_test_sha = _virtual_context(monkeypatch, module, tmp_path)
    first = module.build_review(
        expected_generator_sha256=generator_sha,
        expected_generator_test_sha256=generator_test_sha,
    )
    second = module.build_review(
        expected_generator_sha256=generator_sha,
        expected_generator_test_sha256=generator_test_sha,
    )
    assert first == second
    document, payload, sidecar = first
    assert set(document) == {
        "schema_version", "status", "reviewed_bindings", "checks",
        "severity_counts", "review_payload_sha256",
    }
    assert document["status"] == "GO"
    assert document["severity_counts"] == {"P0": 0, "P1": 0, "P2": 0}
    unhashed = dict(document)
    observed = unhashed.pop("review_payload_sha256")
    assert observed == module._document_hash(unhashed)
    assert payload == module._canonical(document)
    assert sidecar == module._sidecar(payload)
    assert document["reviewed_bindings"]["blocked_design_reconstruction"]["all_other_source_bytes_unchanged"] is True
    assert document["reviewed_bindings"]["blocked_test_design_reconstruction"]["replacement_count"] == 3
    assert document["reviewed_bindings"]["exact_effect_surface"] == module.EXPECTED_EFFECT_SURFACE
    assert document["reviewed_bindings"]["embedded_design_audit_assertion"] == module.EMBEDDED_DESIGN_AUDIT_ASSERTION
    assert document["reviewed_bindings"]["real_read_only_s0_assertion"] == module.EXPECTED_S0_ASSERTION
    assert document["reviewed_bindings"]["review_generator"]["sha256"] == generator_sha
    assert document["reviewed_bindings"]["review_generator_tests"]["sha256"] == generator_test_sha
    assert document["reviewed_bindings"]["target_mount_exclusion_contract"]["matching_mount_points"] == []
    assert document["reviewed_bindings"]["fresh_install_contract"]["new_object_count"] == 6
    installer = module._load_installer(module.INSTALLER.read_bytes())
    review_path = tmp_path / "retry4-canonical-control-root-installer-independent-review-v1.json"
    review_sidecar = review_path.with_name(review_path.name + ".sha256")
    _repo_file(review_path, payload)
    _repo_file(review_sidecar, sidecar)
    installer.INSTALLER_SOURCE = module.INSTALLER
    installer.INSTALLER_TEST = module.INSTALLER_TEST
    installer.INSTALLER_REVIEW_GENERATOR = module.GENERATOR
    installer.INSTALLER_REVIEW_GENERATOR_TEST = module.GENERATOR_TEST
    installer.INSTALLER_REVIEW = review_path
    installer.INSTALLER_REVIEW_SIDECAR = review_sidecar
    installer.__file__ = str(module.INSTALLER)
    args = types.SimpleNamespace(
        expected_installer_sha256=module.EXPECTED_EXECUTABLE_INSTALLER_SHA256,
        expected_installer_test_sha256=module.EXPECTED_EXECUTABLE_INSTALLER_TEST_SHA256,
        expected_installer_review_generator_sha256=generator_sha,
        expected_installer_review_generator_test_sha256=generator_test_sha,
        expected_installer_review_sha256=hashlib.sha256(payload).hexdigest(),
        expected_blocked_installer_sha256=module.EXPECTED_BLOCKED_INSTALLER_SHA256,
        expected_blocked_installer_size=module.EXPECTED_BLOCKED_INSTALLER_SIZE,
        expected_blocked_installer_test_sha256=module.EXPECTED_BLOCKED_INSTALLER_TEST_SHA256,
        expected_blocked_installer_test_size=module.EXPECTED_BLOCKED_INSTALLER_TEST_SIZE,
    )
    accepted, authority = installer._installer_authority(args)
    try:
        assert authority["independent_review"]["sha256"] == hashlib.sha256(payload).hexdigest()
        assert authority["blocked_design_reconstruction"]["all_other_source_bytes_unchanged"] is True
    finally:
        installer._close_records(accepted)


@pytest.mark.parametrize("corrupt", ["installer", "installer_tests"])
def test_prepare_rejects_transition_drift_before_exec_and_closes_records(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, corrupt: str,
) -> None:
    module = _verified_module()
    generator_sha, generator_test_sha = _virtual_context(monkeypatch, module, tmp_path)
    target = module.INSTALLER if corrupt == "installer" else module.INSTALLER_TEST
    changed = target.read_bytes() + b"unexpected transition byte\n"
    _repo_file(target, changed)
    if corrupt == "installer":
        monkeypatch.setattr(module, "EXPECTED_EXECUTABLE_INSTALLER_SHA256", hashlib.sha256(changed).hexdigest())
        monkeypatch.setattr(module, "EXPECTED_EXECUTABLE_INSTALLER_SIZE", len(changed))
    else:
        monkeypatch.setattr(module, "EXPECTED_EXECUTABLE_INSTALLER_TEST_SHA256", hashlib.sha256(changed).hexdigest())
        monkeypatch.setattr(module, "EXPECTED_EXECUTABLE_INSTALLER_TEST_SIZE", len(changed))
    monkeypatch.setattr(module, "_load_installer", lambda _payload: pytest.fail("installer executed before reconstruction"))
    closed: list[dict[str, dict[str, object]]] = []
    original_close = module._close_records
    def observed_close(records: dict[str, dict[str, object]]) -> None:
        closed.append(dict(records))
        original_close(records)
    monkeypatch.setattr(module, "_close_records", observed_close)
    with pytest.raises(module.ReviewError, match="reconstruction|transition"):
        module._prepare(generator_sha, generator_test_sha)
    assert len(closed) == 1 and len(closed[0]) == 4
    for record in closed[0].values():
        with pytest.raises(OSError):
            os.fstat(int(record["fd"]))


def test_default_main_never_reaches_publisher(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    module = _verified_module()
    monkeypatch.setattr(module, "REVIEW_GENERATOR_BLOCKED_PENDING_INDEPENDENT_AUDIT", False)
    monkeypatch.setattr(module, "_validate_invocation", lambda: None)
    document = {
        "schema_version": "x", "status": "GO", "reviewed_bindings": {},
        "checks": {}, "severity_counts": {"P0": 0, "P1": 0, "P2": 0},
        "review_payload_sha256": "x",
    }
    payload = b"payload\n"
    sidecar = b"sidecar\n"
    monkeypatch.setattr(module, "build_review", lambda **_kwargs: (document, payload, sidecar))
    monkeypatch.setattr(module, "publish_review", lambda **_kwargs: pytest.fail("publisher reached"))
    assert module.main([
        "--expected-generator-sha256", "1" * 64,
        "--expected-generator-test-sha256", "2" * 64,
    ]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["published"] is None
    assert output["file_sha256"] == hashlib.sha256(payload).hexdigest()


@pytest.mark.parametrize("mutation", ["status", "severity", "selfhash", "payload", "sidecar"])
def test_candidate_validation_rejects_each_mismatch(mutation: str) -> None:
    module = _verified_module()
    unhashed = {
        "schema_version": "schema", "status": "GO", "reviewed_bindings": {},
        "checks": {}, "severity_counts": {"P0": 0, "P1": 0, "P2": 0},
    }
    document = {**unhashed, "review_payload_sha256": module._document_hash(unhashed)}
    payload = module._canonical(document)
    sidecar = module._sidecar(payload)
    changed = dict(document)
    if mutation == "status":
        changed["status"] = "NO_GO"
    elif mutation == "severity":
        changed["severity_counts"] = {"P0": 0, "P1": 1, "P2": 0}
    elif mutation == "selfhash":
        changed["review_payload_sha256"] = "0" * 64
    elif mutation == "payload":
        payload += b"x"
    else:
        sidecar += b"x"
    with pytest.raises(module.ReviewError, match="candidate semantics"):
        module._validate_candidate(changed, payload, sidecar)


@pytest.mark.skipif(os.geteuid() != 0, reason="root EEXIST durability replay")
def test_raced_exact_fragment_replays_file_and_directory_durability(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    module = _verified_module()
    monkeypatch.setattr(module, "REVIEW_GENERATOR_BLOCKED_PENDING_INDEPENDENT_AUDIT", False)
    monkeypatch.setattr(module, "_validate_invocation", lambda: None)
    work = tmp_path / "work"
    work.mkdir()
    work.chmod(0o775)
    os.chown(work, 1000, 1000)
    fragment = work / "fragment"
    payload = b"exact raced fragment\n"
    _repo_file(fragment, payload)
    directory = os.open(work, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    original_read = module._read_at
    reads = 0
    def raced_read(parent: int, path: Path, maximum: int):
        nonlocal reads
        reads += 1
        if reads == 1:
            return None
        return original_read(parent, path, maximum)
    monkeypatch.setattr(module, "_read_at", raced_read)
    monkeypatch.setattr(module, "_publish_one", lambda *_args: (_ for _ in ()).throw(FileExistsError("fragment")))
    events: list[str] = []
    original_fsync = os.fsync
    def observed_fsync(descriptor: int) -> None:
        events.append("directory" if stat.S_ISDIR(os.fstat(descriptor).st_mode) else "file")
        original_fsync(descriptor)
    monkeypatch.setattr(module.os, "fsync", observed_fsync)
    try:
        identity = module._ensure_fragment(directory, fragment, payload, len(payload))
    finally:
        os.close(directory)
    assert identity["sha256"] == hashlib.sha256(payload).hexdigest()
    assert events == ["file", "directory"]


@pytest.mark.skipif(os.geteuid() != 0, reason="root O_TMPFILE review publication integration")
def test_root_sidecar_first_resume_pair_idempotence_and_main_only_rejection(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    module = _verified_module()
    work = tmp_path / "work"
    work.mkdir()
    os.chown(work, 1000, 1000)
    work.chmod(0o775)
    output = work / module.OUTPUT.name
    sidecar_path = work / module.SIDECAR.name
    monkeypatch.setattr(module, "WORK", work)
    monkeypatch.setattr(module, "OUTPUT", output)
    monkeypatch.setattr(module, "SIDECAR", sidecar_path)
    status = work.stat()
    monkeypatch.setattr(module, "EXPECTED_WORK", {
        "st_dev": status.st_dev, "st_ino": status.st_ino, "uid": 1000,
        "gid": 1000, "mode": 0o775, "nlink": status.st_nlink,
    })
    monkeypatch.setattr(module, "REVIEW_GENERATOR_BLOCKED_PENDING_INDEPENDENT_AUDIT", False)
    monkeypatch.setattr(module, "_validate_invocation", lambda: None)
    unhashed = {
        "schema_version": "schema", "status": "GO", "reviewed_bindings": {},
        "checks": {}, "severity_counts": {"P0": 0, "P1": 0, "P2": 0},
    }
    document = {**unhashed, "review_payload_sha256": module._document_hash(unhashed)}
    payload = module._canonical(document)
    sidecar = module._sidecar(payload)
    fake_installer = type("Installer", (), {
        "_revalidate_records": staticmethod(lambda _records: None),
        "_preflight": staticmethod(lambda *_args: {"phase": "S0_FRESH"}),
        "_close_records": staticmethod(lambda _records: None),
    })()
    def context() -> dict[str, object]:
        return {
            "document": document, "payload": payload, "sidecar": sidecar,
            "own_records": {}, "runtime_records": {},
            "installer_module": fake_installer, "timestamp": {},
            "audit_authority": {}, "preflight": {"phase": "S0_FRESH"},
        }
    monkeypatch.setattr(module, "_prepare", lambda *_args: context())
    monkeypatch.setattr(module, "_close_context", lambda _context: None)
    order: list[str] = []
    original_publish = module._publish_one
    def observed(directory: int, path: Path, content: bytes) -> dict[str, object]:
        order.append(path.name)
        return original_publish(directory, path, content)
    monkeypatch.setattr(module, "_publish_one", observed)
    first = module.publish_review(expected_generator_sha256="1" * 64, expected_generator_test_sha256="2" * 64)
    assert order == [sidecar_path.name, output.name]
    assert first[3]["publication_order"] == ["sidecar", "main"]
    order.clear()
    fsync_events: list[str] = []
    original_fsync = os.fsync
    def observed_fsync(descriptor: int) -> None:
        fsync_events.append("directory" if stat.S_ISDIR(os.fstat(descriptor).st_mode) else "file")
        original_fsync(descriptor)
    monkeypatch.setattr(module.os, "fsync", observed_fsync)
    second = module.publish_review(expected_generator_sha256="1" * 64, expected_generator_test_sha256="2" * 64)
    assert order == [] and second[1:3] == first[1:3]
    assert fsync_events == ["file", "directory", "file", "directory"]
    other = tmp_path / "main-only"
    other.mkdir()
    os.chown(other, 1000, 1000)
    other.chmod(0o775)
    monkeypatch.setattr(module, "WORK", other)
    monkeypatch.setattr(module, "OUTPUT", other / output.name)
    monkeypatch.setattr(module, "SIDECAR", other / sidecar_path.name)
    other_status = other.stat()
    monkeypatch.setattr(module, "EXPECTED_WORK", {
        "st_dev": other_status.st_dev, "st_ino": other_status.st_ino,
        "uid": 1000, "gid": 1000, "mode": 0o775, "nlink": other_status.st_nlink,
    })
    descriptor = os.open(other, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        original_publish(descriptor, module.OUTPUT, payload)
    finally:
        os.close(descriptor)
    with pytest.raises(module.ReviewError, match="main-only"):
        module.publish_review(expected_generator_sha256="1" * 64, expected_generator_test_sha256="2" * 64)
