from __future__ import annotations

import ast
import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import types

import pytest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "work/blind-phase-confirmatory-v2/install_retry4_canonical_control_root_v1.py"
EXPECTED_SOURCE_SHA256 = "bebbe189bc8f0100533dfc560a0fca446fcb159315ccc77096ae31e3ef6d7fe0"
EXPECTED_SOURCE_SIZE = 87195
EXPECTED_INSTALLER_BLOCKED = False
SOURCE_BYTES = SOURCE.read_bytes()
assert len(SOURCE_BYTES) == EXPECTED_SOURCE_SIZE
assert hashlib.sha256(SOURCE_BYTES).hexdigest() == EXPECTED_SOURCE_SHA256
SOURCE_TREE = ast.parse(SOURCE_BYTES, filename=str(SOURCE))
MODULE = types.ModuleType("verified_retry4_canonical_installer")
MODULE.__file__ = str(SOURCE)
MODULE.__package__ = None
exec(compile(SOURCE_TREE, str(SOURCE), "exec"), MODULE.__dict__)


def _owner_map(tree: ast.AST) -> dict[ast.AST, ast.AST]:
    result: dict[ast.AST, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            result[child] = parent
    return result


def _owner(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> str:
    current = parents.get(node)
    while current is not None:
        if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return current.name
        current = parents.get(current)
    return "<module>"


def _static_surface(payload: bytes) -> dict[str, object]:
    tree = ast.parse(payload.decode("utf-8"))
    parents = _owner_map(tree)
    imports = [
        ast.unparse(node) for node in tree.body
        if isinstance(node, (ast.Import, ast.ImportFrom))
    ]
    if imports != [
        "from __future__ import annotations", "import argparse", "import ctypes",
        "import errno", "import fcntl", "import hashlib", "import json",
        "import os", "from pathlib import Path", "import stat", "import sys",
        "import types", "from typing import Any, Mapping, Sequence",
    ]:
        raise AssertionError("exact import surface differs")
    forbidden = {
        "unlink", "remove", "rmdir", "removedirs", "rmtree", "copy",
        "copy2", "copytree", "replace", "rename", "renames", "link",
        "symlink", "truncate", "ftruncate", "pwrite", "write_text",
        "write_bytes", "touch", "fork", "unshare", "setns", "mount",
        "umount", "kill", "Popen", "run", "system", "execve", "spawn",
        "socket", "urlopen", "request", "getattr", "setattr", "delattr",
        "__import__", "eval", "globals", "locals", "vars",
    }
    protected = {
        "_publish_file", "_ensure_fragment", "_ensure_pair", "_create_staging",
        "_harden_staging", "_ensure_results", "_rename_staging", "_install",
        "_write_all", "_link_tmpfile", "_durably_replay_fragment",
        "_load_helper", "main",
    }
    function_names = [node.name for node in tree.body if isinstance(node, ast.FunctionDef)]
    if len(function_names) != len(set(function_names)):
        raise AssertionError("duplicate top-level function")
    defaults = {
        node.name: tuple(ast.unparse(value) for value in (*node.args.defaults, *node.args.kw_defaults) if value is not None)
        for node in tree.body if isinstance(node, ast.FunctionDef)
        and any(value is not None for value in (*node.args.defaults, *node.args.kw_defaults))
    }
    if defaults != {"_identity": ("None",), "_selfhash": ("None",), "_read_at": ("292",), "_preflight": ("None",), "main": ("None",)}:
        raise AssertionError("function defaults differ")
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.decorator_list:
                raise AssertionError("executable decorator expression")
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
            if isinstance(value, (ast.Lambda, ast.Call)) and _owner(node, parents) == "<module>":
                allowed_module_initializers = {
                    "Path('/home/ubuntu/telemetry-yield')", "Path('/var/lib')",
                    "Path('/usr/bin/python3.12')",
                    "Path('/usr/bin/openssl')", "Path('/etc/ssl/openssl.cnf')",
                    "Path('/usr/bin/curl')",
                    "INTENT.with_name(INTENT.name + '.sha256')",
                    "PROVENANCE.with_name(PROVENANCE.name + '.sha256')",
                    "AMENDMENT.with_name(AMENDMENT.name + '.sha256')",
                    "AMENDMENT_REVIEW.with_name(AMENDMENT_REVIEW.name + '.sha256')",
                    "RFC_REVIEW.with_name(RFC_REVIEW.name + '.sha256')",
                    "TERMINAL_REVIEW.with_name(TERMINAL_REVIEW.name + '.sha256')",
                    "INSTALLER_REVIEW.with_name(INSTALLER_REVIEW.name + '.sha256')",
                }
                if ast.unparse(value) not in allowed_module_initializers:
                    raise AssertionError("module-scope executable assignment")
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name):
            callee = node.func.id
            exact_safe_path = (
                callee == "getattr" and _owner(node, parents) == "_validate_invocation"
                and ast.unparse(node) == "getattr(flags, 'safe_path', False)"
            )
            if (callee in forbidden and not exact_safe_path) or callee == "open":
                raise AssertionError(f"forbidden call: {callee}")
        elif isinstance(node.func, ast.Attribute):
            callee = node.func.attr
            exact_reverse = (
                callee == "replace" and (
                    (
                        _owner(node, parents) == "_unblock_reconstruction"
                        and ast.unparse(node) == "payload.replace(executable, blocked, 1)"
                    )
                    or (
                        _owner(node, parents) == "_test_unblock_reconstruction"
                        and ast.unparse(node) == "reconstructed.replace(executable, blocked, 1)"
                    )
                )
            )
            if (callee in forbidden and not exact_reverse) or (callee == "open" and not (
                isinstance(node.func.value, ast.Name) and node.func.value.id == "os"
            )):
                raise AssertionError(f"forbidden attribute call: {callee}")
        else:
            raise AssertionError("dynamic call target")
    mutations: list[str] = []
    opens: list[str] = []
    execs: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        owner = _owner(node, parents)
        if isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name):
            module, name = node.func.value.id, node.func.attr
            if module == "os" and name == "open":
                opens.append(f"{owner}:{ast.unparse(node)}")
            if module == "os" and name in {"write", "fchown", "fchmod", "fsync", "mkdir"}:
                mutations.append(f"{owner}:{ast.unparse(node)}")
            if module == "fcntl" and name == "flock":
                mutations.append(f"{owner}:{ast.unparse(node)}")
        if isinstance(node.func, ast.Name) and node.func.id in {"linkat", "function"}:
            mutations.append(f"{owner}:{ast.unparse(node)}")
        if isinstance(node.func, ast.Name) and node.func.id == "exec":
            execs.append(f"{owner}:{ast.unparse(node)}")
    expected_mutations = sorted([
        "_write_all:os.write(descriptor, remaining)",
        "_publish_file:os.fchown(descriptor, 0, 0)",
        "_publish_file:os.fchmod(descriptor, mode)",
        "_publish_file:os.fsync(descriptor)",
        "_publish_file:os.fsync(directory)",
        "_link_tmpfile:linkat(descriptor, b'', directory, os.fsencode(name), AT_EMPTY_PATH)",
        "_acquire_intent_lock:fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)",
        "_durably_replay_fragment:os.fsync(descriptor)",
        "_durably_replay_fragment:os.fsync(directory)",
        "_create_staging:os.mkdir(STAGING.name, 448, dir_fd=parent)",
        "_harden_staging:os.fchown(descriptor, 0, 0)",
        "_harden_staging:os.fchmod(descriptor, 493)",
        "_harden_staging:os.fsync(descriptor)",
        "_harden_staging:os.fsync(parent)",
        "_ensure_results:os.mkdir(RESULTS_NAME, 448, dir_fd=directory)",
        "_ensure_results:os.fchown(descriptor, 0, 0)",
        "_ensure_results:os.fchmod(descriptor, 493)",
        "_ensure_results:os.fsync(descriptor)",
        "_ensure_results:os.fsync(directory)",
        "_rename_staging:function(parent, os.fsencode(STAGING.name), parent, os.fsencode(CANONICAL.name), RENAME_NOREPLACE)",
        "_install:os.fsync(working)",
        "_install:os.fsync(parent)",
        "_install:os.fsync(parent)",
    ])
    if sorted(mutations) != expected_mutations:
        raise AssertionError("mutation call surface differs")
    write_opens = [value for value in opens if any(flag in value for flag in ("O_WRONLY", "O_RDWR", "O_CREAT", "O_TRUNC", "O_APPEND", "O_TMPFILE"))]
    if write_opens != ["_publish_file:os.open('.', os.O_RDWR | os.O_CLOEXEC | os.O_TMPFILE, 256, dir_fd=directory)"]:
        raise AssertionError("write-capable os.open surface differs")
    if execs != ["_load_helper:exec(compile(payload, str(RFC_HELPER), 'exec'), module.__dict__)"]:
        raise AssertionError("dynamic exec surface differs")
    return {
        "top_level_function_count": len(function_names),
        "call_count": sum(isinstance(node, ast.Call) for node in ast.walk(tree)),
        "mutation_call_count": len(mutations),
        "write_open_count": len(write_opens),
        "exec_count": len(execs),
        "mutation_call_sha256": hashlib.sha256(
            json.dumps(sorted(mutations), separators=(",", ":")).encode()
        ).hexdigest(),
    }


def test_source_is_exact_blocked_and_compiles_before_execution() -> None:
    assert MODULE.INSTALLER_BLOCKED_PENDING_INDEPENDENT_DESIGN_REVIEW is EXPECTED_INSTALLER_BLOCKED
    assert _static_surface(SOURCE_BYTES) == {
        "top_level_function_count": 71,
        "call_count": 849,
        "mutation_call_count": 23,
        "write_open_count": 1,
        "exec_count": 1,
        "mutation_call_sha256": "74c0cbe3ee01c4946e00680110bb00bcaf66531f70dde75eecbf9d89df3d5482",
    }
    assert _static_surface(SOURCE_BYTES) == MODULE.EXPECTED_INSTALLER_EFFECT_SURFACE
    assert MODULE.EXPECTED_INSTALLER_DESIGN_AUDIT_ASSERTION == {
        "auditor": "retry4_installer_contract",
        "artifact_kind": "embedded_exact_blocked_design_audit_assertion",
        "status": "GO", "severity_counts": {"P0": 0, "P1": 0, "P2": 0},
        "scope": "blocked installer identity and tests are bound separately",
    }
    assert MODULE.EXPECTED_INSTALLER_S0_ASSERTION == {
        "status": "PASS_READ_ONLY_INSTALL_PREFLIGHT", "phase": "S0_FRESH",
        "authority_count": 16, "mount_target_count": 19,
        "fixed_point_sha256": "7889195204b4b3423a52b764d5c3b369ec727fa7e2f39f81ed92a2ffc101b2ce",
        "canonical_control_root_absent": True, "transaction_siblings": [],
        "mutation_performed": False,
    }


def test_tampered_module_scope_code_is_rejected_before_execution(tmp_path: Path) -> None:
    marker = tmp_path / "must-not-exist"
    changed = SOURCE_BYTES + f"\nPath({str(marker)!r}).write_text('bad')\n".encode()
    with pytest.raises(AssertionError):
        if len(changed) != EXPECTED_SOURCE_SIZE or hashlib.sha256(changed).hexdigest() != EXPECTED_SOURCE_SHA256:
            raise AssertionError("pre-exec identity differs")
        exec(compile(changed, str(SOURCE), "exec"), {})
    assert not marker.exists()


@pytest.mark.parametrize(
    "mutant",
    [
        "def rogue():\n os.unlink('/tmp/x')\n",
        "def rogue():\n danger=os.system\n danger('id')\n",
        "from os import unlink as danger\ndef rogue():\n danger('/tmp/x')\n",
        "def rogue():\n getattr(os,'unlink')('/tmp/x')\n",
        "def rogue():\n Path('/tmp/x').write_bytes(b'x')\n",
        "def rogue():\n os.open('/tmp/x', os.O_WRONLY)\n",
        "def rogue():\n exec('pass')\n",
        "def rogue(f=os.system):\n f('id')\n",
        "@os.system('id')\ndef rogue():\n pass\n",
        "_install = open\n",
    ],
)
def test_static_surface_rejects_dead_or_indirect_effect_mutants(mutant: str) -> None:
    with pytest.raises(AssertionError):
        _static_surface(SOURCE_BYTES + b"\n" + mutant.encode())


def test_exact_authority_and_layout_constants() -> None:
    assert MODULE.ATTEMPT_ID == "ffe76ede2d67e03da7b8e682786e552356847cce43fa8b345dc9a9a9f29b6c55"
    assert MODULE.CANONICAL == Path("/var/lib/telemetry-yield-confirmatory-v3")
    assert MODULE.EXPECTED["timestamp_query"][1:3] == ("09ae91e27e1bdbaabc17267b60021f830615e1e29fe7c94b9ec5de3606dc1b18", 70)
    assert MODULE.EXPECTED["timestamp_response"][1:3] == ("c2f10fa901905a1b01fa8f1709a107a1229533c6370ef5a0acb4ae82fc94afc2", 6008)
    assert MODULE.EXPECTED["rfc_review"][1:3] == ("95a0518816572b358aed31add6dfc5fd01dbaaed8b91d8b9b61584ab02460f2c", 12912)
    assert MODULE.EXPECTED["rfc_review_sidecar"][1:3] == ("7b955f9d28b35d525e6cfafcb5c0b96b7388294719dbd798d1bc88066a109ada", 158)
    assert MODULE.EXPECTED["tsa_ca"][1:3] == ("ecd9dc38bc3efb7dbd6431f57e29d2f8d6a0f0d211e1464b3fef2cbfe266fcd2", 182140)
    assert MODULE.EXPECTED["openssl"][1:3] == ("30cc7c491903d6d8bca54406889c0334a167777397458214b5bd498c51b6fd97", 1005368)
    assert MODULE.EXPECTED["openssl_config"][1:3] == ("529815b0dd4bd6608bafeeb3d410b0683374e61aef792b3e3f38b3767d26f747", 12324)
    assert MODULE.EXPECTED["curl"][1:3] == ("74b4ce8f74b377f18ef1b3df7279c26cb3cd14c49e39ab1498575b209dc3f70f", 297288)
    assert MODULE.INSTALLED_FILES == (
        ("builder", "build_runtime_guard_v3.py", 0o555),
        ("timestamp_query", "amendment-v3.tsq", 0o444),
        ("timestamp_response", "amendment-v3.tsr", 0o444),
        ("tsa_ca", "tsa-ca-certificates.pem", 0o444),
    )


def test_embedded_post_timestamp_audits_are_exact_and_not_artifact_placeholders() -> None:
    assert MODULE.POST_TIMESTAMP_AUDITS == (
        {"auditor": "recovery_independent_review", "artifact_kind": "embedded_exact_post_timestamp_audit_assertion", "status": "GO", "severity_counts": {"P0": 0, "P1": 0, "P2": 0}},
        {"auditor": "retry4_rfc3161_plan", "artifact_kind": "embedded_exact_post_timestamp_audit_assertion", "status": "GO", "severity_counts": {"P0": 0, "P1": 0, "P2": 0}},
    )
    assert "posttimestamp" not in " ".join(str(path) for path, *_rest in MODULE.EXPECTED.values())


def test_terminal_review_contract_requires_durable_sidecar_first_independent_publication() -> None:
    contract = MODULE._terminal_review_publication_contract()
    assert contract["installer_publishes_review"] is False
    assert contract["publication_order"] == ["sidecar", "main"]
    assert contract["each_fragment"] == [
        "O_TMPFILE", "bounded_full_write", "fchown_root_root",
        "fchmod_0444", "fsync_inode_before_link",
        "linkat_AT_EMPTY_PATH_noclobber", "fsync_reports_after_link",
        "fd_relative_same_inode_exact_readback",
    ]
    assert contract["crash_grammar"] == {
        "absent_pair": "publish_sidecar_then_main",
        "exact_sidecar_only": "resume_main",
        "exact_pair": "idempotent_read_only_validation",
        "main_only": "reject",
        "foreign_or_mismatched_fragment": "reject",
    }
    checks = MODULE.expected_terminal_review_checks()
    assert checks["terminal_review_publication_is_sidecar_first_noclobber_and_resumable"] is True
    assert checks["terminal_review_inodes_and_reports_directory_are_fsynced_after_each_link"] is True
    assert MODULE._terminal_review_contract()["publication_contract"] == contract


def test_timestamp_verdict_requires_dual_exact_validation() -> None:
    helper_authorities = {
        "amendment": {"path": "amendment"}, "amendment_sidecar": {"path": "amendment-sidecar"},
        "review": {"path": "review"}, "review_sidecar": {"path": "review-sidecar"},
        "tsa_ca": {"path": "ca"}, "openssl": {"path": "openssl"},
        "openssl_config": {"path": "config"}, "curl": {"path": "curl"},
    }
    records = {
        "amendment": {"identity": helper_authorities["amendment"]},
        "amendment_sidecar": {"identity": helper_authorities["amendment_sidecar"]},
        "amendment_review": {"identity": helper_authorities["review"]},
        "amendment_review_sidecar": {"identity": helper_authorities["review_sidecar"]},
        "tsa_ca": {"identity": helper_authorities["tsa_ca"]},
        "openssl": {"identity": helper_authorities["openssl"]},
        "openssl_config": {"identity": helper_authorities["openssl_config"]},
        "curl": {"identity": helper_authorities["curl"]},
    }
    expected = {
        "status": "PASS_POINT_IN_TIME_ROOT_OWNED_READONLY_PAIR",
        "initial_state": "PAIR_COMMITTED",
        "network_requested": False,
        "publication_requested": False,
        "publication": None,
        "authorities": helper_authorities,
        "candidate": {
            "query": {"sha256": MODULE.EXPECTED["timestamp_query"][1], "size_bytes": 70},
            "response": {"sha256": MODULE.EXPECTED["timestamp_response"][1], "size_bytes": 6008},
            "query_validation": {"message_imprint_sha256": MODULE.EXPECTED["amendment"][1], "nonce": "0xca48d04831017c3f"},
            "response_validation": {
                "status": "Granted", "message_imprint_sha256": MODULE.EXPECTED["amendment"][1],
                "nonce": "0xca48d04831017c3f", "generation_time_text": "Sep  6 20:32:35 2026 GMT",
                "policy_oid": "2.16.840.1.114412.7.1", "serial_number": "0xB7569821B0AADA7CF7BC4D809A3BCFBE",
                "queryfile_verification": "queryfile", "data_verification": "data",
            },
            "response_content_type": "already-published-not-http-observed",
        },
    }
    helper = type("Helper", (), {"prepare": staticmethod(lambda **_kwargs: expected)})()
    verdict = MODULE._timestamp_verdict(helper, records)
    assert verdict["response_validation"]["status"] == "Granted"
    assert len(verdict["independent_audits"]) == 2
    changed = json.loads(json.dumps(expected))
    changed["candidate"]["response_validation"]["data_verification"] = "not-data"
    helper = type("Helper", (), {"prepare": staticmethod(lambda **_kwargs: changed)})()
    with pytest.raises(MODULE.InstallError, match="post-timestamp"):
        MODULE._timestamp_verdict(helper, records)
    changed = json.loads(json.dumps(expected))
    changed["authorities"]["openssl"]["sha256"] = "0" * 64
    helper = type("Helper", (), {"prepare": staticmethod(lambda **_kwargs: changed)})()
    with pytest.raises(MODULE.InstallError, match="post-timestamp"):
        MODULE._timestamp_verdict(helper, records)


def test_presence_grammar_accepts_only_exact_crash_states() -> None:
    keys = ("canonical", "staging", "intent", "intent_sidecar", "provenance", "provenance_sidecar")
    accepted = {
        (False, False, False, False, False, False): "S0_FRESH",
        (False, False, False, True, False, False): "S1_INTENT_SIDECAR",
        (False, False, True, True, False, False): "S1_INTENT_COMMITTED",
        (False, True, True, True, False, False): "S2_STAGING",
        (True, False, True, True, False, False): "S3_CANONICAL_UNRECORDED",
        (True, False, True, True, False, True): "S4_PROVENANCE_SIDECAR",
        (True, False, True, True, True, True): "S4_TERMINAL_RECORDED",
    }
    for bits in __import__("itertools").product((False, True), repeat=6):
        state = dict(zip(keys, bits, strict=True))
        if bits in accepted:
            assert MODULE._phase(state) == accepted[bits]
        else:
            with pytest.raises(MODULE.InstallError, match="presence grammar"):
                MODULE._phase(state)


def test_receipts_are_deterministic_selfhashed_and_sidecar_first() -> None:
    document = {"schema_version": "test-v1", "status": "PASS", "value": 7}
    first = MODULE._receipt_payload(document, "payload_sha256")
    assert first == MODULE._receipt_payload(document, "payload_sha256")
    parsed = json.loads(first)
    assert parsed["payload_sha256"] == MODULE._document_hash(document)
    assert MODULE._receipt_sidecar(Path("receipt.json"), first) == f"{hashlib.sha256(first).hexdigest()}  receipt.json\n".encode()


def test_blocked_and_executable_states_gate_every_mutation_boundary(monkeypatch: pytest.MonkeyPatch) -> None:
    boundaries = (
        lambda: MODULE._publish_file(-1, Path("x"), b"x", 0o444),
        lambda: MODULE._ensure_fragment(-1, Path("x"), b"x", 1),
        lambda: MODULE._durably_replay_fragment(-1, Path("x"), b"x", 1),
        lambda: MODULE._ensure_pair(-1, Path("x"), Path("x.sha256"), b"x"),
        lambda: MODULE._write_all(-1, b"x"),
        lambda: MODULE._link_tmpfile(-1, -1, "x"),
        lambda: MODULE._acquire_intent_lock(-1, b"x"),
        lambda: MODULE._create_staging(-1),
        lambda: MODULE._harden_staging(-1, -1),
        lambda: MODULE._ensure_results(-1),
        lambda: MODULE._rename_staging(-1),
        lambda: MODULE._install({}, {}, {}),
    )
    if EXPECTED_INSTALLER_BLOCKED:
        monkeypatch.setattr(MODULE, "_validate_invocation", lambda: None)
        monkeypatch.setattr(MODULE, "parser", lambda: pytest.fail("parser reached"))
        with pytest.raises(MODULE.InstallError, match="blocked pending design review"):
            MODULE.main([])
        for boundary in boundaries:
            with pytest.raises(MODULE.InstallError, match="blocked pending design review"):
                boundary()
    else:
        assert MODULE._require_unblocked() is None
        def reject_invocation() -> None:
            raise MODULE.InstallError("exact /usr/bin/python3.12 test rejection")
        monkeypatch.setattr(MODULE, "_validate_invocation", reject_invocation)
        for boundary in boundaries:
            with pytest.raises(MODULE.InstallError, match="exact /usr/bin/python3.12"):
                boundary()
        monkeypatch.setattr(MODULE, "INSTALLER_BLOCKED_PENDING_INDEPENDENT_DESIGN_REVIEW", True)
        monkeypatch.setattr(MODULE, "_validate_invocation", lambda: None)
        monkeypatch.setattr(MODULE, "parser", lambda: pytest.fail("parser reached"))
        with pytest.raises(MODULE.InstallError, match="blocked pending design review"):
            MODULE.main([])


def test_mutation_authority_and_main_invocation_guards_are_first() -> None:
    functions = {node.name: node for node in SOURCE_TREE.body if isinstance(node, ast.FunctionDef)}
    for name in (
        "_write_all", "_link_tmpfile", "_publish_file", "_durably_replay_fragment",
        "_ensure_fragment",
        "_ensure_pair", "_acquire_intent_lock", "_create_staging",
        "_harden_staging", "_ensure_results", "_rename_staging", "_install",
    ):
        assert ast.unparse(functions[name].body[0]) == "_require_mutation_authority()"
    assert ast.unparse(functions["_require_mutation_authority"].body[0]) == "_require_unblocked()"
    assert ast.unparse(functions["main"].body[0]) == "_validate_invocation()"
    assert ast.unparse(functions["main"].body[1]) == "_require_unblocked()"


def test_parser_requires_noncyclic_installer_test_and_review_hashes() -> None:
    parser = MODULE.parser()
    with pytest.raises(SystemExit):
        parser.parse_args([])
    args = parser.parse_args([
        "--expected-installer-sha256", "1" * 64,
        "--expected-installer-test-sha256", "2" * 64,
        "--expected-installer-review-generator-sha256", "a" * 64,
        "--expected-installer-review-generator-test-sha256", "b" * 64,
        "--expected-installer-review-sha256", "3" * 64,
        "--expected-blocked-installer-sha256", "4" * 64,
        "--expected-blocked-installer-size", "123",
        "--expected-blocked-installer-test-sha256", "c" * 64,
        "--expected-blocked-installer-test-size", "456",
    ])
    assert args.install is False


def test_exact_invocation_gate_rejects_test_interpreter() -> None:
    with pytest.raises(MODULE.InstallError, match="exact /usr/bin/python3.12"):
        MODULE._validate_invocation()


def test_mountinfo_rejects_a_target_that_is_already_a_mountpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(MODULE, "CANONICAL", Path("/proc"))
    with pytest.raises(MODULE.InstallError, match="mount point"):
        MODULE._target_mounts_absent()


def test_mount_exclusion_contract_covers_all_six_objects_receipts_terminal_pair_and_quarantine() -> None:
    targets = set(MODULE._mount_targets())
    expected = {
        str(MODULE.QUARANTINED_RETRY3),
        str(MODULE.INTENT), str(MODULE.INTENT_SIDECAR),
        str(MODULE.PROVENANCE), str(MODULE.PROVENANCE_SIDECAR),
        str(MODULE.TERMINAL_REVIEW), str(MODULE.TERMINAL_REVIEW_SIDECAR),
    }
    for root in (MODULE.CANONICAL, MODULE.STAGING):
        expected.add(str(root))
        expected.update(str(root / name) for _source, name, _mode in MODULE.INSTALLED_FILES)
        expected.add(str(root / MODULE.RESULTS_NAME))
    assert len(expected) == 19
    assert targets == expected
    assert MODULE._mount_exclusion_contract() == {
        "checked_paths": sorted(expected), "matching_mount_points": [],
    }


@pytest.mark.parametrize("target", MODULE._mount_targets())
def test_each_controlled_target_mount_is_detected(target: str) -> None:
    payload = f"1 0 0:1 / {target} rw - ext4 /dev/exact rw\n".encode()
    assert MODULE._mount_matches(payload) == [target]


def test_full_six_object_disjointness_includes_root_results_and_quarantine_directory() -> None:
    records = {"source": {"identity": {"st_dev": 1, "st_ino": 10}}}
    quarantine = {"directory": {"st_dev": 1, "st_ino": 20}, "children": {"old": {"st_dev": 1, "st_ino": 21}}}
    root = {"st_dev": 1, "st_ino": 30}
    installed = [{"st_dev": 1, "st_ino": value} for value in (31, 32, 33, 34, 35)]
    previous = dict(MODULE.EXPECTED_VAR_LIB)
    MODULE.EXPECTED_VAR_LIB = {**previous, "st_dev": 1}
    try:
        evidence = MODULE._disjoint(root, installed, records, quarantine)
        assert evidence["new_object_count"] == 6
        assert len(evidence["objects"]) == 6
        assert evidence["pairwise_distinct_inodes"] is True
        assert evidence["same_device_as_var_lib"] is True
        assert evidence["disjoint_from_all_source_authorities_and_retry3_quarantine"] is True
        with pytest.raises(MODULE.InstallError, match="aliases"):
            MODULE._disjoint({"st_dev": 1, "st_ino": 20}, installed, records, quarantine)
        changed = list(installed)
        changed[-1] = {"st_dev": 1, "st_ino": 10}
        with pytest.raises(MODULE.InstallError, match="aliases"):
            MODULE._disjoint(root, changed, records, quarantine)
        with pytest.raises(MODULE.InstallError, match="aliases"):
            MODULE._disjoint(root, [*installed[:-1], {"st_dev": 2, "st_ino": 99}], records, quarantine)
    finally:
        MODULE.EXPECTED_VAR_LIB = previous


@pytest.mark.parametrize("function_name", ["_preflight", "_install"])
def test_reports_open_failure_closes_already_open_parent_fd(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, function_name: str) -> None:
    parent = tmp_path / "parent"
    parent.mkdir()
    captured = -1
    def open_parent() -> tuple[int, os.stat_result]:
        nonlocal captured
        captured = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
        return captured, os.fstat(captured)
    monkeypatch.setattr(MODULE, "_open_parent", open_parent)
    monkeypatch.setattr(MODULE, "_open_reports", lambda: (_ for _ in ()).throw(MODULE.InstallError("injected reports open")))
    monkeypatch.setattr(MODULE, "_require_mutation_authority", lambda: None)
    with pytest.raises(MODULE.InstallError, match="injected reports open"):
        getattr(MODULE, function_name)({}, {}, {})
    with pytest.raises(OSError):
        os.fstat(captured)


def test_default_path_is_read_only_and_explicit_install_has_two_preflights(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(MODULE, "INSTALLER_BLOCKED_PENDING_INDEPENDENT_DESIGN_REVIEW", False)
    monkeypatch.setattr(MODULE, "_validate_invocation", lambda: None)
    monkeypatch.setattr(MODULE, "_installer_authority", lambda _args: ({}, {"installer": "exact"}))
    monkeypatch.setattr(MODULE, "_load_authorities", lambda: ({}, {"timestamp": "exact"}))
    monkeypatch.setattr(MODULE, "_close_records", lambda _records: None)
    monkeypatch.setattr(MODULE, "_preflight", lambda *_args: {"status": "PASS_READ_ONLY_INSTALL_PREFLIGHT", "mutation_performed": False})
    monkeypatch.setattr(MODULE, "_install", lambda *_args: pytest.fail("install reached"))
    authority_args = [
        "--expected-installer-sha256", "1" * 64,
        "--expected-installer-test-sha256", "2" * 64,
        "--expected-installer-review-generator-sha256", "a" * 64,
        "--expected-installer-review-generator-test-sha256", "b" * 64,
        "--expected-installer-review-sha256", "3" * 64,
        "--expected-blocked-installer-sha256", "4" * 64,
        "--expected-blocked-installer-size", "123",
        "--expected-blocked-installer-test-sha256", "c" * 64,
        "--expected-blocked-installer-test-size", "456",
    ]
    assert MODULE.main(authority_args) == 0
    assert json.loads(capsys.readouterr().out)["mutation_performed"] is False
    calls = 0
    def preflight(*_args: object) -> dict[str, object]:
        nonlocal calls
        calls += 1
        return {"status": "fixed", "phase": "S0_FRESH", "value": 1}
    monkeypatch.setattr(MODULE, "_preflight", preflight)
    monkeypatch.setattr(MODULE, "_install", lambda *_args: {"status": "installed"})
    assert MODULE.main([*authority_args, "--install"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert calls == 2 and result["status"] == "installed"
    expected_preflight = {"status": "fixed", "phase": "S0_FRESH", "value": 1}
    assert result["preflight_fixed_point_sha256"] == MODULE._document_hash(expected_preflight)
    unhashed = dict(result)
    receipt_hash = unhashed.pop("receipt_payload_sha256")
    assert receipt_hash == MODULE._document_hash(unhashed)


def test_install_flag_in_s5_returns_read_only_terminal_result_without_install(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(MODULE, "INSTALLER_BLOCKED_PENDING_INDEPENDENT_DESIGN_REVIEW", False)
    monkeypatch.setattr(MODULE, "_validate_invocation", lambda: None)
    monkeypatch.setattr(MODULE, "_installer_authority", lambda _args: ({}, {"installer": "exact"}))
    monkeypatch.setattr(MODULE, "_load_authorities", lambda: ({}, {"timestamp": "exact"}))
    monkeypatch.setattr(MODULE, "_close_records", lambda _records: None)
    terminal = {
        "status": "PASS_READ_ONLY_TERMINAL_INDEPENDENT_REVIEW_GO",
        "phase": "S5_TERMINAL_INDEPENDENT_REVIEWED",
        "mutation_performed": False,
    }
    calls = 0
    def preflight(*_args: object) -> dict[str, object]:
        nonlocal calls
        calls += 1
        return dict(terminal)
    monkeypatch.setattr(MODULE, "_preflight", preflight)
    monkeypatch.setattr(MODULE, "_install", lambda *_args: pytest.fail("install reached in S5"))
    args = [
        "--install",
        "--expected-installer-sha256", "1" * 64,
        "--expected-installer-test-sha256", "2" * 64,
        "--expected-installer-review-generator-sha256", "a" * 64,
        "--expected-installer-review-generator-test-sha256", "b" * 64,
        "--expected-installer-review-sha256", "3" * 64,
        "--expected-blocked-installer-sha256", "4" * 64,
        "--expected-blocked-installer-size", "123",
        "--expected-blocked-installer-test-sha256", "c" * 64,
        "--expected-blocked-installer-test-size", "456",
        "--expected-terminal-review-sha256", "5" * 64,
        "--expected-terminal-review-generator-sha256", "6" * 64,
        "--expected-terminal-review-generator-test-sha256", "7" * 64,
    ]
    assert MODULE.main(args) == 0
    assert calls == 2
    assert json.loads(capsys.readouterr().out) == terminal


def test_real_authorities_and_timestamp_pair_validate_read_only() -> None:
    records, verdict = MODULE._load_authorities()
    try:
        assert len(records) == 16
        assert verdict["response_validation"] == {
            "status": "Granted",
            "message_imprint_sha256": MODULE.EXPECTED["amendment"][1],
            "nonce": "0xca48d04831017c3f",
            "generation_time_text": "Sep  6 20:32:35 2026 GMT",
            "policy_oid": "2.16.840.1.114412.7.1",
            "serial_number": "0xB7569821B0AADA7CF7BC4D809A3BCFBE",
            "queryfile_verification": "queryfile",
            "data_verification": "data",
        }
        MODULE._revalidate_records(records)
    finally:
        MODULE._close_records(records)


def _repo_file(path: Path, payload: bytes) -> None:
    path.write_bytes(payload)
    path.chmod(0o664)
    if os.geteuid() == 0:
        os.chown(path, 1000, 1000)


def test_installer_independent_review_authority_is_noncyclic_exact_and_zero_severity(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    source = tmp_path / MODULE.INSTALLER_SOURCE.name
    tests = tmp_path / MODULE.INSTALLER_TEST.name
    review_generator = tmp_path / MODULE.INSTALLER_REVIEW_GENERATOR.name
    review_generator_tests = tmp_path / MODULE.INSTALLER_REVIEW_GENERATOR_TEST.name
    review = tmp_path / MODULE.INSTALLER_REVIEW.name
    sidecar = review.with_name(review.name + ".sha256")
    test_payload = Path(__file__).read_bytes()
    if EXPECTED_INSTALLER_BLOCKED:
        blocked_payload = SOURCE_BYTES
        blocked_test_payload = test_payload
        executable_payload = blocked_payload.replace(
            b"\nINSTALLER_BLOCKED_PENDING_INDEPENDENT_DESIGN_REVIEW = True\n",
            b"\nINSTALLER_BLOCKED_PENDING_INDEPENDENT_DESIGN_REVIEW = False\n", 1,
        )
        executable_sha256 = hashlib.sha256(executable_payload).hexdigest()
        executable_test_payload = blocked_test_payload
        for blocked, executable in (
            (
                f'EXPECTED_SOURCE_SHA256 = "{hashlib.sha256(blocked_payload).hexdigest()}"\n'.encode(),
                f'EXPECTED_SOURCE_SHA256 = "{executable_sha256}"\n'.encode(),
            ),
            (
                f"EXPECTED_SOURCE_SIZE = {len(blocked_payload)}\n".encode(),
                f"EXPECTED_SOURCE_SIZE = {len(executable_payload)}\n".encode(),
            ),
            (b"EXPECTED_INSTALLER_BLOCKED = True\n", b"EXPECTED_INSTALLER_BLOCKED = False\n"),
        ):
            assert executable_test_payload.count(blocked) == 1
            executable_test_payload = executable_test_payload.replace(blocked, executable, 1)
    else:
        executable_payload = SOURCE_BYTES
        executable_sha256 = hashlib.sha256(executable_payload).hexdigest()
        executable_test_payload = test_payload
        blocked_payload = executable_payload.replace(
            b"\nINSTALLER_BLOCKED_PENDING_INDEPENDENT_DESIGN_REVIEW = False\n",
            b"\nINSTALLER_BLOCKED_PENDING_INDEPENDENT_DESIGN_REVIEW = True\n", 1,
        )
        blocked_test_payload = executable_test_payload
        for executable, blocked in (
            (
                f'EXPECTED_SOURCE_SHA256 = "{executable_sha256}"\n'.encode(),
                f'EXPECTED_SOURCE_SHA256 = "{hashlib.sha256(blocked_payload).hexdigest()}"\n'.encode(),
            ),
            (
                f"EXPECTED_SOURCE_SIZE = {len(executable_payload)}\n".encode(),
                f"EXPECTED_SOURCE_SIZE = {len(blocked_payload)}\n".encode(),
            ),
            (b"EXPECTED_INSTALLER_BLOCKED = False\n", b"EXPECTED_INSTALLER_BLOCKED = True\n"),
        ):
            assert blocked_test_payload.count(executable) == 1
            blocked_test_payload = blocked_test_payload.replace(executable, blocked, 1)
    review_generator_payload = b"exact installer review generator bytes\n"
    review_generator_test_payload = b"exact installer review generator test bytes\n"
    _repo_file(source, executable_payload)
    _repo_file(tests, executable_test_payload)
    _repo_file(review_generator, review_generator_payload)
    _repo_file(review_generator_tests, review_generator_test_payload)
    source_identity = {"path": str(source), "sha256": executable_sha256, "size_bytes": len(executable_payload)}
    test_identity = {
        "path": str(tests), "sha256": hashlib.sha256(executable_test_payload).hexdigest(),
        "size_bytes": len(executable_test_payload),
    }
    review_generator_identity = {
        "path": str(review_generator), "sha256": hashlib.sha256(review_generator_payload).hexdigest(),
        "size_bytes": len(review_generator_payload),
    }
    review_generator_test_identity = {
        "path": str(review_generator_tests),
        "sha256": hashlib.sha256(review_generator_test_payload).hexdigest(),
        "size_bytes": len(review_generator_test_payload),
    }
    reconstruction = MODULE._unblock_reconstruction(
        executable_payload, hashlib.sha256(blocked_payload).hexdigest(), len(blocked_payload)
    )
    test_reconstruction = MODULE._test_unblock_reconstruction(
        executable_test_payload, hashlib.sha256(blocked_test_payload).hexdigest(),
        len(blocked_test_payload), executable_sha256, len(executable_payload),
        hashlib.sha256(blocked_payload).hexdigest(), len(blocked_payload),
    )
    bindings = MODULE._installer_review_bindings(
        source_identity, test_identity, review_generator_identity,
        review_generator_test_identity, reconstruction, test_reconstruction,
    )
    unhashed = {
        "schema_version": MODULE.INSTALLER_REVIEW_SCHEMA,
        "status": "GO", "reviewed_bindings": bindings,
        "checks": MODULE.expected_installer_review_checks(),
        "severity_counts": {"P0": 0, "P1": 0, "P2": 0},
    }
    document = {**unhashed, "review_payload_sha256": MODULE._document_hash(unhashed)}
    review_payload = MODULE._canonical(document)
    review_sha = hashlib.sha256(review_payload).hexdigest()
    _repo_file(review, review_payload)
    _repo_file(sidecar, f"{review_sha}  {review.name}\n".encode())
    monkeypatch.setattr(MODULE, "INSTALLER_SOURCE", source)
    monkeypatch.setattr(MODULE, "INSTALLER_TEST", tests)
    monkeypatch.setattr(MODULE, "INSTALLER_REVIEW_GENERATOR", review_generator)
    monkeypatch.setattr(MODULE, "INSTALLER_REVIEW_GENERATOR_TEST", review_generator_tests)
    monkeypatch.setattr(MODULE, "INSTALLER_REVIEW", review)
    monkeypatch.setattr(MODULE, "INSTALLER_REVIEW_SIDECAR", sidecar)
    monkeypatch.setattr(MODULE, "__file__", str(source))
    args = argparse.Namespace(
        expected_installer_sha256=source_identity["sha256"],
        expected_installer_test_sha256=test_identity["sha256"],
        expected_installer_review_generator_sha256=review_generator_identity["sha256"],
        expected_installer_review_generator_test_sha256=review_generator_test_identity["sha256"],
        expected_installer_review_sha256=review_sha,
        expected_blocked_installer_sha256=hashlib.sha256(blocked_payload).hexdigest(),
        expected_blocked_installer_size=len(blocked_payload),
        expected_blocked_installer_test_sha256=hashlib.sha256(blocked_test_payload).hexdigest(),
        expected_blocked_installer_test_size=len(blocked_test_payload),
    )
    records, authority = MODULE._installer_authority(args)
    try:
        assert authority["installer"] == source_identity
        assert authority["installer_tests"] == test_identity
        assert authority["review_generator"] == review_generator_identity
        assert authority["review_generator_tests"] == review_generator_test_identity
        assert authority["blocked_test_design_reconstruction"] == test_reconstruction
        assert authority["exact_effect_surface"] == MODULE.EXPECTED_INSTALLER_EFFECT_SURFACE
        assert authority["embedded_design_audit_assertion"] == MODULE.EXPECTED_INSTALLER_DESIGN_AUDIT_ASSERTION
        assert authority["real_read_only_s0_assertion"] == MODULE.EXPECTED_INSTALLER_S0_ASSERTION
        assert authority["independent_review"]["sha256"] == review_sha
        MODULE._revalidate_records(records)
    finally:
        MODULE._close_records(records)
    document["severity_counts"]["P1"] = 1
    malformed = MODULE._canonical(document)
    malformed_sha = hashlib.sha256(malformed).hexdigest()
    _repo_file(review, malformed)
    _repo_file(sidecar, f"{malformed_sha}  {review.name}\n".encode())
    args.expected_installer_review_sha256 = malformed_sha
    with pytest.raises(MODULE.InstallError, match="review semantics differ"):
        MODULE._installer_authority(args)


def _root_record(path: Path, payload: bytes) -> dict[str, object]:
    path.write_bytes(payload)
    os.chown(path, 0, 0)
    path.chmod(0o444)
    descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    status = os.fstat(descriptor)
    return {"fd": descriptor, "opened": status, "payload": payload, "identity": MODULE._identity(path, status, payload)}


def _root_fixture(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> tuple[dict[str, dict[str, object]], dict[str, object]]:
    parent = tmp_path / "var-lib"
    reports = tmp_path / "reports"
    parent.mkdir()
    reports.mkdir()
    os.chown(parent, 0, 0)
    os.chown(reports, 0, 0)
    parent.chmod(0o755)
    reports.chmod(0o755)
    quarantine = parent / "quarantined-retry3"
    quarantine.mkdir()
    os.chown(quarantine, 0, 0)
    quarantine.chmod(0o755)
    quarantine_expected: dict[str, tuple[str, int, int, int]] = {}
    for index, name in enumerate(("build_runtime_guard_v3.py", "amendment-v3.tsq", "amendment-v3.tsr", "tsa-ca-certificates.pem")):
        payload = f"old-{index}".encode()
        path = quarantine / name
        path.write_bytes(payload)
        os.chown(path, 0, 0)
        path.chmod(0o444 if index else 0o555)
        status = path.lstat()
        quarantine_expected[name] = (hashlib.sha256(payload).hexdigest(), len(payload), stat.S_IMODE(status.st_mode), status.st_ino)
    authority_dir = tmp_path / "authorities"
    authority_dir.mkdir()
    records: dict[str, dict[str, object]] = {}
    expected: dict[str, tuple[Path, str, int, int, int, int]] = {}
    for key, payload in (
        ("builder", b"new-builder"), ("timestamp_query", b"new-query"),
        ("timestamp_response", b"new-response"), ("tsa_ca", b"new-ca"),
    ):
        path = authority_dir / key
        records[key] = _root_record(path, payload)
        expected[key] = (path, hashlib.sha256(payload).hexdigest(), len(payload), 0, 0, 0o444)
    canonical = parent / "canonical"
    staging = parent / ("staging-" + MODULE.ATTEMPT_ID)
    intent = parent / ("intent-" + MODULE.ATTEMPT_ID + ".json")
    provenance = parent / ("provenance-" + MODULE.ATTEMPT_ID + ".json")
    terminal = reports / "terminal-review.json"
    monkeypatch.setattr(MODULE, "INSTALLER_BLOCKED_PENDING_INDEPENDENT_DESIGN_REVIEW", False)
    monkeypatch.setattr(MODULE, "VAR_LIB", parent)
    monkeypatch.setattr(MODULE, "REPORTS", reports)
    monkeypatch.setattr(MODULE, "CANONICAL", canonical)
    monkeypatch.setattr(MODULE, "STAGING", staging)
    monkeypatch.setattr(MODULE, "INTENT", intent)
    monkeypatch.setattr(MODULE, "INTENT_SIDECAR", intent.with_name(intent.name + ".sha256"))
    monkeypatch.setattr(MODULE, "PROVENANCE", provenance)
    monkeypatch.setattr(MODULE, "PROVENANCE_SIDECAR", provenance.with_name(provenance.name + ".sha256"))
    monkeypatch.setattr(MODULE, "QUARANTINED_RETRY3", quarantine)
    monkeypatch.setattr(MODULE, "TERMINAL_REVIEW", terminal)
    monkeypatch.setattr(MODULE, "TERMINAL_REVIEW_SIDECAR", terminal.with_name(terminal.name + ".sha256"))
    monkeypatch.setattr(MODULE, "EXPECTED", expected)
    monkeypatch.setattr(MODULE, "EXPECTED_VAR_LIB", {
        "st_dev": parent.lstat().st_dev, "st_ino": parent.lstat().st_ino,
        "uid": 0, "gid": 0, "mode": 0o755, "fresh_nlink": parent.lstat().st_nlink,
    })
    monkeypatch.setattr(MODULE, "EXPECTED_REPORTS", {
        "st_dev": reports.lstat().st_dev, "st_ino": reports.lstat().st_ino,
        "uid": 0, "gid": 0, "mode": 0o755, "nlink": reports.lstat().st_nlink,
    })
    monkeypatch.setattr(MODULE, "EXPECTED_QUARANTINED_RETRY3", {
        "st_dev": quarantine.lstat().st_dev, "st_ino": quarantine.lstat().st_ino,
        "uid": 0, "gid": 0, "mode": 0o755, "nlink": quarantine.lstat().st_nlink,
    })
    monkeypatch.setattr(MODULE, "EXPECTED_QUARANTINED_CHILDREN", quarantine_expected)
    monkeypatch.setattr(MODULE, "INSTALLED_FILES", (
        ("builder", "build_runtime_guard_v3.py", 0o555),
        ("timestamp_query", "amendment-v3.tsq", 0o444),
        ("timestamp_response", "amendment-v3.tsr", 0o444),
        ("tsa_ca", "tsa-ca-certificates.pem", 0o444),
    ))
    monkeypatch.setattr(MODULE, "_validate_invocation", lambda: None)
    verdict = {"status": "exact-post-timestamp", "independent_audits": list(MODULE.POST_TIMESTAMP_AUDITS)}
    return records, verdict


@pytest.mark.skipif(os.geteuid() != 0, reason="root terminal-review exact-pair integration")
def test_root_s5_sidecar_resume_then_exact_terminal_review_go(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    records, verdict = _root_fixture(monkeypatch, tmp_path)
    generator = tmp_path / "terminal-review-generator.py"
    generator_tests = tmp_path / "terminal-review-generator-tests.py"
    _repo_file(generator, b"terminal review generator\n")
    _repo_file(generator_tests, b"terminal review generator tests\n")
    monkeypatch.setattr(MODULE, "TERMINAL_REVIEW_GENERATOR", generator)
    monkeypatch.setattr(MODULE, "TERMINAL_REVIEW_GENERATOR_TEST", generator_tests)
    reports_fd = -1
    try:
        installed_receipt = MODULE._install(records, verdict, {})
        generator_identity = {
            "path": str(generator), "sha256": hashlib.sha256(generator.read_bytes()).hexdigest(),
            "size_bytes": generator.stat().st_size,
        }
        generator_test_identity = {
            "path": str(generator_tests), "sha256": hashlib.sha256(generator_tests.read_bytes()).hexdigest(),
            "size_bytes": generator_tests.stat().st_size,
        }
        bindings = MODULE._terminal_review_bindings(
            generator_identity, generator_test_identity, records, verdict,
            installed_receipt["quarantined_retry3_preserved"], {},
            (installed_receipt["intent"], installed_receipt["intent_sidecar"]),
            (installed_receipt["provenance"], installed_receipt["provenance_sidecar"]),
            installed_receipt["canonical_control_root"], installed_receipt["installed_layout"],
            installed_receipt["fresh_six_object_disjointness"],
        )
        unhashed = {
            "schema_version": MODULE.TERMINAL_REVIEW_SCHEMA, "status": "GO",
            "reviewed_bindings": bindings, "checks": MODULE.expected_terminal_review_checks(),
            "severity_counts": {"P0": 0, "P1": 0, "P2": 0},
        }
        document = {**unhashed, "review_payload_sha256": MODULE._document_hash(unhashed)}
        payload = MODULE._canonical(document)
        digest = hashlib.sha256(payload).hexdigest()
        sidecar_payload = f"{digest}  {MODULE.TERMINAL_REVIEW.name}\n".encode()
        reports_fd = os.open(MODULE.REPORTS, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
        fsync_calls: list[tuple[int, int]] = []
        original_fsync = MODULE.os.fsync
        def observed_fsync(fd: int) -> None:
            status = os.fstat(fd)
            fsync_calls.append((status.st_dev, status.st_ino))
            original_fsync(fd)
        monkeypatch.setattr(MODULE.os, "fsync", observed_fsync)
        sidecar_identity = MODULE._publish_file(
            reports_fd, MODULE.TERMINAL_REVIEW_SIDECAR, sidecar_payload, 0o444,
        )
        expectations = {
            "review": digest, "generator": generator_identity["sha256"],
            "generator_tests": generator_test_identity["sha256"],
        }
        pending = MODULE._preflight(records, verdict, {}, expectations)
        assert pending["phase"] == "S5_TERMINAL_REVIEW_SIDECAR_PENDING"
        assert pending["status"] == "PENDING_READ_ONLY_TERMINAL_REVIEW_MAIN"
        with pytest.raises(MODULE.InstallError, match="malformed"):
            MODULE._preflight(records, verdict, {}, {**expectations, "review": "bad"})
        main_identity = MODULE._publish_file(
            reports_fd, MODULE.TERMINAL_REVIEW, payload, 0o444,
        )
        reports_identity = (MODULE.REPORTS.stat().st_dev, MODULE.REPORTS.stat().st_ino)
        assert fsync_calls == [
            (sidecar_identity["st_dev"], sidecar_identity["st_ino"]), reports_identity,
            (main_identity["st_dev"], main_identity["st_ino"]), reports_identity,
        ]
        os.close(reports_fd)
        reports_fd = -1
        complete = MODULE._preflight(records, verdict, {}, expectations)
        assert complete["phase"] == "S5_TERMINAL_INDEPENDENT_REVIEWED"
        assert complete["status"] == "PASS_READ_ONLY_TERMINAL_INDEPENDENT_REVIEW_GO"
        assert complete["terminal_independent_review"]["severity_counts"] == {"P0": 0, "P1": 0, "P2": 0}
    finally:
        if reports_fd >= 0:
            os.close(reports_fd)
        MODULE._close_records(records)


@pytest.mark.skipif(os.geteuid() != 0, reason="root terminal-review main-only rejection")
def test_root_s5_main_only_is_rejected(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    records, verdict = _root_fixture(monkeypatch, tmp_path)
    reports_fd = os.open(MODULE.REPORTS, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        MODULE._publish_file(reports_fd, MODULE.TERMINAL_REVIEW, b"{}\n", 0o444)
        with pytest.raises(MODULE.InstallError, match="main-only"):
            MODULE._terminal_review_state(
                reports_fd, None, records, verdict, {}, {}, None, None, None, [], None,
            )
    finally:
        os.close(reports_fd)
        MODULE._close_records(records)


@pytest.mark.skipif(os.geteuid() != 0, reason="root fsync replay integration")
def test_root_existing_fragment_replay_fsyncs_inode_and_parent(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    directory = tmp_path / "directory"
    directory.mkdir()
    os.chown(directory, 0, 0)
    descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    target = directory / "fragment"
    target.write_bytes(b"exact")
    os.chown(target, 0, 0)
    target.chmod(0o444)
    calls: list[tuple[int, int]] = []
    original = MODULE.os.fsync
    def observed(fd: int) -> None:
        status = os.fstat(fd)
        calls.append((status.st_dev, status.st_ino))
        original(fd)
    monkeypatch.setattr(MODULE.os, "fsync", observed)
    monkeypatch.setattr(MODULE, "_validate_invocation", lambda: None)
    monkeypatch.setattr(MODULE, "INSTALLER_BLOCKED_PENDING_INDEPENDENT_DESIGN_REVIEW", False)
    try:
        identity = MODULE._ensure_fragment(descriptor, target, b"exact", 100)
        assert identity["sha256"] == hashlib.sha256(b"exact").hexdigest()
        assert (target.stat().st_dev, target.stat().st_ino) in calls
        assert (directory.stat().st_dev, directory.stat().st_ino) in calls
    finally:
        os.close(descriptor)


@pytest.mark.skipif(os.geteuid() != 0, reason="root EEXIST durability integration")
def test_root_eexist_race_replays_winning_inode_and_parent_fsync(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    directory = tmp_path / "directory"
    directory.mkdir()
    os.chown(directory, 0, 0)
    descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    target = directory / "fragment"
    payload = b"competitor-linked-before-fsync"
    fsync_calls: list[tuple[int, int]] = []
    original_fsync = MODULE.os.fsync
    def observed(fd: int) -> None:
        status = os.fstat(fd)
        fsync_calls.append((status.st_dev, status.st_ino))
        original_fsync(fd)
    def competing_link(_directory: int, path: Path, content: bytes, _mode: int) -> dict[str, object]:
        created = os.open(
            path.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC,
            0o400, dir_fd=descriptor,
        )
        try:
            assert os.write(created, content) == len(content)
            os.fchown(created, 0, 0)
            os.fchmod(created, 0o444)
        finally:
            os.close(created)
        raise FileExistsError(path.name)
    monkeypatch.setattr(MODULE, "_publish_file", competing_link)
    monkeypatch.setattr(MODULE.os, "fsync", observed)
    monkeypatch.setattr(MODULE, "_validate_invocation", lambda: None)
    monkeypatch.setattr(MODULE, "INSTALLER_BLOCKED_PENDING_INDEPENDENT_DESIGN_REVIEW", False)
    try:
        identity = MODULE._ensure_fragment(descriptor, target, payload, 100)
        target_identity = (target.stat().st_dev, target.stat().st_ino)
        directory_identity = (directory.stat().st_dev, directory.stat().st_ino)
        assert identity["sha256"] == hashlib.sha256(payload).hexdigest()
        assert fsync_calls == [target_identity, directory_identity]
    finally:
        os.close(descriptor)


@pytest.mark.skipif(os.geteuid() != 0, reason="root results directory durability integration")
def test_root_resumed_0755_results_directory_is_always_fsynced(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    root = tmp_path / "root"
    results = root / MODULE.RESULTS_NAME
    root.mkdir()
    results.mkdir()
    for path in (root, results):
        os.chown(path, 0, 0)
        path.chmod(0o755)
    directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    calls: list[tuple[int, int]] = []
    original = MODULE.os.fsync
    def observed(fd: int) -> None:
        status = os.fstat(fd)
        calls.append((status.st_dev, status.st_ino))
        original(fd)
    monkeypatch.setattr(MODULE.os, "fsync", observed)
    monkeypatch.setattr(MODULE, "_validate_invocation", lambda: None)
    monkeypatch.setattr(MODULE, "INSTALLER_BLOCKED_PENDING_INDEPENDENT_DESIGN_REVIEW", False)
    try:
        MODULE._ensure_results(directory)
        assert (results.stat().st_dev, results.stat().st_ino) in calls
        assert (root.stat().st_dev, root.stat().st_ino) in calls
    finally:
        os.close(directory)


@pytest.mark.skipif(os.geteuid() != 0, reason="root O_TMPFILE/linkat/ownership integration")
def test_root_preflight_and_full_install_are_resumable_idempotent_and_disjoint(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    records, verdict = _root_fixture(monkeypatch, tmp_path)
    try:
        before = MODULE._preflight(records, verdict, {})
        assert before["phase"] == "S0_FRESH" and before["mutation_performed"] is False
        first = MODULE._install(records, verdict, {})
        assert first["phase"] == "S4_TERMINAL_RECORDED"
        assert first["status"] == "PASS_EXACT5_INSTALLED_PENDING_TERMINAL_INDEPENDENT_REVIEW"
        assert first["lifecycle_iq_roles_or_outcome_executed"] is False
        unhashed = dict(first)
        receipt_hash = unhashed.pop("receipt_payload_sha256")
        assert receipt_hash == MODULE._document_hash(unhashed)
        provenance_document = json.loads(MODULE.PROVENANCE.read_bytes())
        assert provenance_document["intent"] == {
            "main": first["intent"], "sidecar": first["intent_sidecar"],
        }
        assert provenance_document["target_mount_exclusion_contract"] == MODULE._mount_exclusion_contract()
        assert provenance_document["fresh_six_object_disjointness"] == first["fresh_six_object_disjointness"]
        assert provenance_document["terminal_review_contract"]["publication_contract"] == MODULE._terminal_review_publication_contract()
        assert [Path(item["path"]).name for item in first["installed_layout"]] == [
            "build_runtime_guard_v3.py", "amendment-v3.tsq", "amendment-v3.tsr",
            "tsa-ca-certificates.pem", "results-v3",
        ]
        source_inodes = {(record["opened"].st_dev, record["opened"].st_ino) for record in records.values()}
        assert all((item["st_dev"], item["st_ino"]) not in source_inodes for item in first["installed_layout"][:-1])
        second = MODULE._install(records, verdict, {})
        assert second == first
        after = MODULE._preflight(records, verdict, {})
        assert after["phase"] == "S4_TERMINAL_RECORDED"
    finally:
        MODULE._close_records(records)


@pytest.mark.skipif(os.geteuid() != 0, reason="root two-installer flock interleaving")
def test_root_locked_phase_refresh_turns_stale_s1_into_idempotent_s4(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    records, verdict = _root_fixture(monkeypatch, tmp_path)
    parent, _opened = MODULE._open_parent()
    quarantine_fd = -1
    try:
        quarantined, quarantine_fd = MODULE._quarantined_snapshot(parent)
        intent_payload = MODULE._receipt_payload(
            MODULE._intent_document(records, verdict, quarantined, {}),
            "intent_payload_sha256",
        )
        MODULE._ensure_pair(parent, MODULE.INTENT, MODULE.INTENT_SIDECAR, intent_payload)
    finally:
        if quarantine_fd >= 0:
            os.close(quarantine_fd)
        os.close(parent)
    original_acquire = MODULE._acquire_intent_lock
    competitor: dict[str, object] | None = None
    def interleaving_acquire(directory: int, payload: bytes) -> tuple[int, dict[str, object]]:
        nonlocal competitor
        monkeypatch.setattr(MODULE, "_acquire_intent_lock", original_acquire)
        competitor = MODULE._install(records, verdict, {})
        monkeypatch.setattr(
            MODULE, "_create_staging",
            lambda _parent: pytest.fail("stale outer installer created a second staging root"),
        )
        return original_acquire(directory, payload)
    monkeypatch.setattr(MODULE, "_acquire_intent_lock", interleaving_acquire)
    try:
        resumed = MODULE._install(records, verdict, {})
        assert competitor is not None
        assert resumed == competitor
        assert MODULE.CANONICAL.is_dir()
        assert not MODULE.STAGING.exists()
        assert MODULE._phase({
            "canonical": True, "staging": False,
            "intent": True, "intent_sidecar": True,
            "provenance": True, "provenance_sidecar": True,
        }) == "S4_TERMINAL_RECORDED"
    finally:
        MODULE._close_records(records)


@pytest.mark.skipif(os.geteuid() != 0, reason="root crash/resume integration")
@pytest.mark.parametrize("boundary", [1, 2, 3, 4])
def test_root_resumes_each_staging_file_prefix(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, boundary: int) -> None:
    records, verdict = _root_fixture(monkeypatch, tmp_path)
    original = MODULE._publish_file
    count = 0
    def crash(directory: int, path: Path, payload: bytes, mode: int) -> dict[str, object]:
        nonlocal count
        identity = original(directory, path, payload, mode)
        if path.parent == MODULE.STAGING:
            count += 1
            if count == boundary:
                raise RuntimeError("injected post-link crash")
        return identity
    monkeypatch.setattr(MODULE, "_publish_file", crash)
    try:
        with pytest.raises(RuntimeError, match="injected"):
            MODULE._install(records, verdict, {})
        monkeypatch.setattr(MODULE, "_publish_file", original)
        result = MODULE._install(records, verdict, {})
        assert result["phase"] == "S4_TERMINAL_RECORDED"
    finally:
        MODULE._close_records(records)


@pytest.mark.skipif(os.geteuid() != 0, reason="root crash/resume integration")
@pytest.mark.parametrize("boundary", ["intent-sidecar", "results", "rename", "provenance-sidecar"])
def test_root_resumes_receipt_and_namespace_crash_boundaries(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, boundary: str) -> None:
    records, verdict = _root_fixture(monkeypatch, tmp_path)
    original_pair = MODULE._ensure_pair
    original_results = MODULE._ensure_results
    original_rename = MODULE._rename_staging
    fired = False
    def pair(directory: int, main: Path, sidecar: Path, payload: bytes) -> tuple[dict[str, object], dict[str, object]]:
        nonlocal fired
        if not fired and ((boundary == "intent-sidecar" and main == MODULE.INTENT) or (boundary == "provenance-sidecar" and main == MODULE.PROVENANCE)):
            fired = True
            MODULE._ensure_fragment(directory, sidecar, MODULE._receipt_sidecar(main, payload), MODULE.MAX_SIDECAR_BYTES)
            raise RuntimeError("injected receipt crash")
        return original_pair(directory, main, sidecar, payload)
    def results(directory: int) -> None:
        nonlocal fired
        original_results(directory)
        if not fired and boundary == "results":
            fired = True
            raise RuntimeError("injected results crash")
    def rename(parent: int) -> None:
        nonlocal fired
        original_rename(parent)
        if not fired and boundary == "rename":
            fired = True
            raise RuntimeError("injected rename crash")
    monkeypatch.setattr(MODULE, "_ensure_pair", pair)
    monkeypatch.setattr(MODULE, "_ensure_results", results)
    monkeypatch.setattr(MODULE, "_rename_staging", rename)
    try:
        with pytest.raises(RuntimeError, match="injected"):
            MODULE._install(records, verdict, {})
        monkeypatch.setattr(MODULE, "_ensure_pair", original_pair)
        monkeypatch.setattr(MODULE, "_ensure_results", original_results)
        monkeypatch.setattr(MODULE, "_rename_staging", original_rename)
        result = MODULE._install(records, verdict, {})
        assert fired is True and result["phase"] == "S4_TERMINAL_RECORDED"
    finally:
        MODULE._close_records(records)


@pytest.mark.skipif(os.geteuid() != 0, reason="root exact-prefix negative integration")
@pytest.mark.parametrize("mutation", ["foreign", "hole", "symlink", "hardlink", "nonempty-results"])
def test_root_exact5_rejects_foreign_holes_aliases_and_nonempty_results(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, mutation: str) -> None:
    records, verdict = _root_fixture(monkeypatch, tmp_path)
    try:
        MODULE._install(records, verdict, {})
        root = MODULE.CANONICAL
        if mutation == "foreign":
            (root / "foreign").write_bytes(b"x")
        elif mutation == "hole":
            os.unlink(root / "amendment-v3.tsq")
        elif mutation == "symlink":
            os.unlink(root / "amendment-v3.tsq")
            (root / "amendment-v3.tsq").symlink_to(root / "amendment-v3.tsr")
        elif mutation == "hardlink":
            os.unlink(root / "amendment-v3.tsq")
            os.link(root / "amendment-v3.tsr", root / "amendment-v3.tsq")
        else:
            (root / "results-v3/foreign").write_bytes(b"x")
        with pytest.raises(MODULE.InstallError):
            MODULE._preflight(records, verdict, {})
    finally:
        MODULE._close_records(records)
