from __future__ import annotations

import ast
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import stat
import types

import pytest


ROOT = Path("/home/ubuntu/telemetry-yield")
SOURCE = ROOT / "work/blind-phase-confirmatory-v2/build_operational_amendment_v3_retry4_rfc3161_review.py"
SOURCE_BYTES = SOURCE.read_bytes()
EXPECTED_SOURCE_SHA256 = "a9f7d984421b3cfbe20f3a32c949647c58b6c64705ea5879730a688a2acfeb21"
EXPECTED_SOURCE_SIZE = 27635
EXPECTED_BLOCKED_SOURCE_SHA256 = "3f38f5e8c831754ddce3896186a04788d180ce994777002ffd3e0108feca026a"
EXPECTED_BLOCKED_SOURCE_SIZE = 27634
EXPECTED_AST_SHA256 = "de95357504d52bdc0aa7b2b3bfd025de3db5396c5c853ca4af48ba7bc77a8729"
EXPECTED_CALLS_SHA256 = "4cbd29e1e2d285cd7de791e11fc54528ccb66db0135da0f312d295179fa35397"
EXPECTED_ASSIGNMENTS_SHA256 = "d267953d98911fafb4ae10f84a6f120b08b9833065056810c09a3f509dddf4de"
EXPECTED_SIGNATURES_SHA256 = "3ce9471cc510016f639b06bf6694e429d736aea8d04a2be6597ad8dbc9204e39"


def _verified_module(payload: bytes, expected_size: int, expected_sha256: str) -> types.ModuleType:
    if len(payload) != expected_size or hashlib.sha256(payload).hexdigest() != expected_sha256:
        raise AssertionError("RFC3161 review generator differs before execution")
    ast.parse(payload)
    module = types.ModuleType("held_retry4_rfc3161_review")
    module.__file__ = str(SOURCE)
    module.__package__ = None
    exec(compile(payload, str(SOURCE), "exec"), module.__dict__)
    return module


MODULE = _verified_module(SOURCE_BYTES, EXPECTED_SOURCE_SIZE, EXPECTED_SOURCE_SHA256)


def _call_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _call_name(node.value)
        return f"{prefix}.{node.attr}" if prefix else None
    return None


def _scope(node: ast.AST, parents: dict[int, ast.AST]) -> str:
    current = node
    while id(current) in parents:
        current = parents[id(current)]
        if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return current.name
        if isinstance(current, ast.Lambda):
            return "<lambda>"
    return "<module>"


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _expr_dump(expression: str) -> str:
    return ast.dump(ast.parse(expression, mode="eval").body, include_attributes=False)


def _static_surface(payload: bytes) -> dict[str, int]:
    tree = ast.parse(payload)
    parents: dict[int, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[id(child)] = parent
    if hashlib.sha256(ast.dump(tree, include_attributes=False).encode()).hexdigest() != EXPECTED_AST_SHA256:
        raise AssertionError("review generator semantic AST differs")
    calls = sorted(
        (_scope(node, parents), ast.dump(node, include_attributes=False))
        for node in ast.walk(tree) if isinstance(node, ast.Call)
    )
    assignments = sorted(
        ast.dump(node, include_attributes=False)
        for node in ast.walk(tree)
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign, ast.NamedExpr))
    )
    signatures = sorted(
        (node.name, ast.dump(node.args, include_attributes=False), tuple(ast.dump(item, include_attributes=False) for item in node.decorator_list))
        for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    )
    if _digest(calls) != EXPECTED_CALLS_SHA256:
        raise AssertionError("review generator scoped calls differ")
    if _digest(assignments) != EXPECTED_ASSIGNMENTS_SHA256:
        raise AssertionError("review generator alias/assignment inventory differs")
    if _digest(signatures) != EXPECTED_SIGNATURES_SHA256:
        raise AssertionError("review generator signatures/defaults differ")
    imports = [ast.unparse(node) for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom))]
    assert imports == [
        "from __future__ import annotations", "import argparse", "import ctypes",
        "import errno", "import hashlib", "import json", "import os",
        "from pathlib import Path", "import stat", "import sys", "import types",
        "from typing import Any, Mapping, Sequence",
    ]
    functions = Counter(
        node.name for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    )
    assert functions == Counter({name: 1 for name in {
            "_stat_tuple", "_identity", "_read_fd",
        "_read_exact", "_canonical", "_document_hash", "_sidecar",
            "_load_helper", "_reverse_reconstruction", "_close_helper_context",
            "expected_checks", "_validate_candidate", "build_review",
            "_read_at", "_publish_one_at",
        "_require_fresh_timestamp_state", "publish_review",
        "_validate_invocation", "parser", "main",
    }})
    call_nodes = [node for node in ast.walk(tree) if isinstance(node, ast.Call)]
    names = [_call_name(node.func) for node in call_nodes]
    allowed_call_names = {
        "GENERATOR.stat", "GENERATOR_TEST.stat", "OSError", "OUTPUT.with_name",
        "Path", "REPORTS.absolute", "ReviewError", "SystemExit", "_canonical",
        "_close_helper_context", "_document_hash", "_identity",
        "_load_helper", "_publish_one_at", "_read_at", "_read_exact", "_read_fd",
        "_require_fresh_timestamp_state", "_reverse_reconstruction", "_sidecar",
        "_stat_tuple", "_validate_candidate", "_validate_invocation", "absolute.lstat", "absolute.resolve",
        "all", "argparse.ArgumentParser", "build_review", "bytearray", "bytes", "dict",
        "checks.values", "compile", "ctypes.CDLL", "ctypes.get_errno", "exec",
        "document.get", "expected_checks", "getattr", "hashlib.sha256", "helper._acquire_single_writer",
        "helper._close_records", "helper._close_state", "helper._load_authorities",
        "helper._open_reports", "helper._result", "helper._revalidate_authorities",
        "helper._revalidate_reports", "helper._revalidate_state", "helper._timestamp_state",
        "int", "json.dumps", "len", "linkat", "main", "memoryview", "min",
        "module.QUERY.exists", "module.RESPONSE.exists", "os.close", "os.fchmod",
        "os.fchown", "os.fsencode", "os.fstat", "os.fsync", "os.getegid",
        "os.geteuid", "os.lseek", "os.open", "os.read", "os.stat", "os.strerror",
        "os.write", "parser", "path.absolute", "payload.count", "payload.extend",
        "payload.replace", "print", "publish_review", "result.add_argument", "set",
        "stat.S_IMODE", "stat.S_ISLNK", "stat.S_ISREG", "str", "types.ModuleType",
        "unhashed.pop",
    }
    if any(name is not None and name not in allowed_call_names for name in names):
        raise AssertionError("unknown direct or aliased call target")
    allowed_dynamic_calls = Counter({
        "(json.dumps(document, allow_nan=False, indent=2, sort_keys=True) + '\\n').encode('utf-8')": 1,
        "hashlib.sha256(json.dumps(document, allow_nan=False, sort_keys=True, separators=(',', ':')).encode('utf-8')).hexdigest()": 1,
        "f'{main_sha256}  {OUTPUT.name}\\n'.encode('ascii')": 1,
        "parser().parse_args(argv)": 1,
        "hashlib.sha256(payload).hexdigest()": 5,
        "path.absolute().lstat()": 1,
        "hashlib.sha256(reconstructed).hexdigest()": 1,
        "Path(sys.executable).resolve()": 1,
        "json.dumps(document, allow_nan=False, sort_keys=True, separators=(',', ':')).encode('utf-8')": 1,
        "hashlib.sha256(sidecar_payload).hexdigest()": 1,
        "main_after[1].get('st_dev')": 1,
        "main_after[1].get('st_ino')": 1,
        "sidecar_after[1].get('st_dev')": 1,
        "sidecar_after[1].get('st_ino')": 1,
    })
    observed_dynamic_calls = Counter(
        ast.unparse(node) for node in call_nodes if _call_name(node.func) is None
    )
    if observed_dynamic_calls != allowed_dynamic_calls:
        raise AssertionError("dynamic call shape differs")
    forbidden_qualified_attributes = {
        "os.unlink", "os.remove", "os.rmdir", "os.rename", "os.replace",
        "os.mkdir", "os.makedirs", "os.symlink", "os.fork", "os.unshare",
        "os.kill", "os.system", "os.popen", "subprocess.run", "subprocess.Popen",
    }
    forbidden_any_attributes = {"write_text", "write_bytes", "touch"}
    if any(
        isinstance(node, ast.Attribute)
        and (
            _call_name(node) in forbidden_qualified_attributes
            or node.attr in forbidden_any_attributes
        )
        for node in ast.walk(tree)
    ):
        raise AssertionError("forbidden request/network/delete/lifecycle call added")
    for node in call_nodes:
        if _call_name(node.func) == "getattr":
            if len(node.args) < 2 or not isinstance(node.args[1], ast.Constant) or node.args[1].value not in {"O_CLOEXEC", "O_NOFOLLOW", "safe_path"}:
                raise AssertionError("dynamic getattr target differs")
    dangerous_name_values = {
        "open", "eval", "exec", "compile", "__import__", "_publish_one_at",
        "publish_review", "build_review", "main",
    }
    if any(
        isinstance(node, (ast.Assign, ast.AnnAssign, ast.NamedExpr))
        and isinstance(getattr(node, "value", None), ast.Name)
        and getattr(node, "value").id in dangerous_name_values
        for node in ast.walk(tree)
    ):
        raise AssertionError("callable alias or protected rebinding added")
    mutators = Counter(
        (_call_name(node.func), _scope(node, parents))
        for node in call_nodes
        if _call_name(node.func) in {"os.write", "os.fchown", "os.fchmod", "os.fsync", "linkat"}
    )
    assert mutators == Counter({
        ("os.write", "_publish_one_at"): 1,
        ("os.fchown", "_publish_one_at"): 1,
        ("os.fchmod", "_publish_one_at"): 1,
        ("os.fsync", "_publish_one_at"): 2,
        ("linkat", "_publish_one_at"): 1,
    })
    open_calls = Counter(
        (_scope(node, parents), ast.dump(node, include_attributes=False))
        for node in call_nodes if _call_name(node.func) == "os.open"
    )
    assert open_calls == Counter({
        ("_read_exact", _expr_dump("os.open(absolute, os.O_RDONLY | getattr(os, 'O_CLOEXEC', 0) | getattr(os, 'O_NOFOLLOW', 0))")): 1,
        ("_read_at", _expr_dump("os.open(path.name, os.O_RDONLY | getattr(os, 'O_CLOEXEC', 0) | getattr(os, 'O_NOFOLLOW', 0), dir_fd=directory)")): 1,
        ("_publish_one_at", _expr_dump("os.open('.', os.O_RDWR | getattr(os, 'O_CLOEXEC', 0) | os.O_TMPFILE, 0o400, dir_fd=directory)")): 1,
    })
    high_risk_calls = Counter((name, _scope(node, parents)) for name, node in zip(names, call_nodes) if name in {"exec", "main", "build_review", "publish_review", "_publish_one_at", "ctypes.CDLL"})
    assert high_risk_calls == Counter({
        ("exec", "_load_helper"): 1,
        ("main", "<module>"): 1,
        ("build_review", "main"): 1,
        ("build_review", "publish_review"): 1,
        ("publish_review", "main"): 1,
        ("_publish_one_at", "publish_review"): 2,
        ("ctypes.CDLL", "_publish_one_at"): 1,
    })
    opens = Counter(_scope(node, parents) for node in call_nodes if _call_name(node.func) == "os.open")
    assert opens == Counter({"_read_exact": 1, "_read_at": 1, "_publish_one_at": 1})
    main = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "main")
    assert ast.unparse(main.body[0]) == "_validate_invocation()"
    assert isinstance(main.body[1], ast.If) and "BLOCKED" in ast.unparse(main.body[1].test)
    publish_calls = [node for node in ast.walk(main) if isinstance(node, ast.Call) and _call_name(node.func) == "publish_review"]
    assert len(publish_calls) == 1
    assert any(isinstance(parent, ast.If) and ast.unparse(parent.test) == "args.publish" for parent in ast.walk(main) if publish_calls[0] in list(ast.walk(parent)))
    return {"calls": len(calls), "assignments": len(assignments), "signatures": len(signatures)}


def _coherently_rehash_surface(
    monkeypatch: pytest.MonkeyPatch, payload: bytes,
) -> None:
    tree = ast.parse(payload)
    parents: dict[int, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[id(child)] = parent
    calls = sorted(
        (_scope(node, parents), ast.dump(node, include_attributes=False))
        for node in ast.walk(tree) if isinstance(node, ast.Call)
    )
    assignments = sorted(
        ast.dump(node, include_attributes=False)
        for node in ast.walk(tree)
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign, ast.NamedExpr))
    )
    signatures = sorted(
        (
            node.name,
            ast.dump(node.args, include_attributes=False),
            tuple(ast.dump(item, include_attributes=False) for item in node.decorator_list),
        )
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    )
    monkeypatch.setitem(
        globals(), "EXPECTED_AST_SHA256",
        hashlib.sha256(ast.dump(tree, include_attributes=False).encode()).hexdigest(),
    )
    monkeypatch.setitem(globals(), "EXPECTED_CALLS_SHA256", _digest(calls))
    monkeypatch.setitem(globals(), "EXPECTED_ASSIGNMENTS_SHA256", _digest(assignments))
    monkeypatch.setitem(globals(), "EXPECTED_SIGNATURES_SHA256", _digest(signatures))


def _build() -> tuple[dict[str, object], bytes, bytes]:
    original = MODULE.RFC3161_REVIEW_GENERATOR_BLOCKED_PENDING_INDEPENDENT_AUDIT
    MODULE.RFC3161_REVIEW_GENERATOR_BLOCKED_PENDING_INDEPENDENT_AUDIT = False
    try:
        return MODULE.build_review(
            expected_generator_sha256=hashlib.sha256(SOURCE_BYTES).hexdigest(),
            expected_generator_test_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        )
    finally:
        MODULE.RFC3161_REVIEW_GENERATOR_BLOCKED_PENDING_INDEPENDENT_AUDIT = original


def test_source_is_exact_prehash_executable_refreeze_and_compiles() -> None:
    assert len(SOURCE_BYTES) == EXPECTED_SOURCE_SIZE
    assert hashlib.sha256(SOURCE_BYTES).hexdigest() == EXPECTED_SOURCE_SHA256
    compile(SOURCE_BYTES, str(SOURCE), "exec")
    assert MODULE.RFC3161_REVIEW_GENERATOR_BLOCKED_PENDING_INDEPENDENT_AUDIT is False
    assert MODULE.OUTPUT.name == "blind-phase-confirmatory-operational-amendment-v3-retry4-rfc3161-v1-independent-review.json"
    assert MODULE.REVIEW_SCHEMA == "blind-phase-confirmatory-operational-amendment-v3-retry4-rfc3161-review-v1"


def test_executable_source_diff_is_only_audited_block_flag_flip() -> None:
    executable = b"RFC3161_REVIEW_GENERATOR_BLOCKED_PENDING_INDEPENDENT_AUDIT = False\n"
    blocked = b"RFC3161_REVIEW_GENERATOR_BLOCKED_PENDING_INDEPENDENT_AUDIT = True\n"
    assert SOURCE_BYTES.count(executable) == 1
    assert SOURCE_BYTES.count(blocked) == 0
    reconstructed = SOURCE_BYTES.replace(executable, blocked)
    assert len(reconstructed) == EXPECTED_BLOCKED_SOURCE_SIZE
    assert hashlib.sha256(reconstructed).hexdigest() == EXPECTED_BLOCKED_SOURCE_SHA256


def test_static_surface_exact() -> None:
    assert _static_surface(SOURCE_BYTES) == {"calls": 243, "assignments": 113, "signatures": 20}


@pytest.mark.parametrize(
    "fragment",
    [
        b"\ndef rogue(): os.system('id')\n",
        b"\ndef rogue(): f=os.unlink; f('/tmp/x')\n",
        b"\ndef rogue(): getattr(os,'unlink')('/tmp/x')\n",
        b"\ndef rogue(): __import__('subprocess').run(['id'])\n",
        b"\ndef rogue(): Path('/tmp/x').write_bytes(b'x')\n",
        b"\ndef rogue(): publish_review(b'x',b'y')\n",
        b"\ndef rogue(): exec('pass')\n",
        b"\ndef rogue(callback=os.system): callback('id')\n",
    ],
)
def test_static_surface_rejects_effect_mutants(fragment: bytes) -> None:
    with pytest.raises(AssertionError):
        _static_surface(SOURCE_BYTES + fragment)


@pytest.mark.parametrize(
    "injected",
    [
        '    danger = os.unlink\n    danger("/tmp/forbidden")\n',
        '    getattr(os, "unlink")("/tmp/forbidden")\n',
        '    Path("/tmp/forbidden").write_bytes(b"x")\n',
        '    os.open("/tmp/forbidden", os.O_WRONLY)\n',
        '    exec("pass")\n',
        '    _publish_one_at(1, "rogue", b"x")\n',
        '    build_review(expected_generator_sha256="0" * 64, expected_generator_test_sha256="0" * 64)\n',
        '    publish_review(b"x", b"y")\n',
    ],
)
def test_structural_surface_rejects_coherently_rehashed_effect_mutants(
    monkeypatch: pytest.MonkeyPatch, injected: str,
) -> None:
    source = SOURCE_BYTES.decode()
    marker = "    args = parser().parse_args(argv)\n"
    assert marker in source
    mutated = source.replace(marker, injected + marker, 1).encode()
    _coherently_rehash_surface(monkeypatch, mutated)
    with pytest.raises(AssertionError):
        _static_surface(mutated)


def test_tampered_generator_rejected_before_execution(tmp_path: Path) -> None:
    sentinel = tmp_path / "must-not-exist"
    tampered = SOURCE_BYTES + f"\nPath({str(sentinel)!r}).write_text('bad')\n".encode()
    with pytest.raises(AssertionError, match="before execution"):
        _verified_module(tampered, EXPECTED_SOURCE_SIZE, EXPECTED_SOURCE_SHA256)
    assert not sentinel.exists()


def test_real_double_build_is_deterministic_selfhashed_and_fresh() -> None:
    first = _build()
    second = _build()
    assert first == second
    document, payload, sidecar = first
    assert set(document) == {
        "schema_version", "status", "severity_counts", "reviewed_bindings",
        "reconstruction", "audit_history", "test_evidence", "fresh_state",
        "checks", "publication_scope", "residual_risks", "authority_scope",
        "review_payload_sha256",
    }
    unhashed = dict(document)
    observed = unhashed.pop("review_payload_sha256")
    assert observed == MODULE._document_hash(unhashed)
    assert payload == MODULE._canonical(document)
    assert sidecar == f"{hashlib.sha256(payload).hexdigest()}  {MODULE.OUTPUT.name}\n".encode()
    assert document["status"] == "GO"
    assert document["severity_counts"] == {"P0": 0, "P1": 0, "P2": 0}
    assert document["fresh_state"]["initial_state"] == "FRESH"
    assert document["fresh_state"]["network_requested"] is False
    assert document["fresh_state"]["publication_requested"] is False
    assert len(document["fresh_state"]["authorities"]) == 8
    assert not MODULE.OUTPUT.exists() and not MODULE.SIDECAR.exists()


def test_all_eight_authorities_have_exact_full_identity_bindings() -> None:
    document, _payload, _sidecar = _build()
    authorities = document["fresh_state"]["authorities"]
    expected = {
        "amendment": ("9ef67a21b69cc4fc52fdbe7402ffa9411d9501d75a203e1d9d113c7e9d318d71", 135095, 0o444),
        "amendment_sidecar": ("f976b6cf05735f351f0b0132406d559e04f139e131f221d8bdafffc5ac35a6fc", 131, 0o444),
        "review": ("cbb0ad7043f18daab0257545479eca91e7be449af1431afca12fa07157147028", 9112, 0o444),
        "review_sidecar": ("f6ba310e2429126d55058004062a8a403997b4a1acffdd68ed492c12b955f25e", 150, 0o444),
        "openssl": ("30cc7c491903d6d8bca54406889c0334a167777397458214b5bd498c51b6fd97", 1005368, 0o755),
        "openssl_config": ("529815b0dd4bd6608bafeeb3d410b0683374e61aef792b3e3f38b3767d26f747", 12324, 0o644),
        "tsa_ca": ("ecd9dc38bc3efb7dbd6431f57e29d2f8d6a0f0d211e1464b3fef2cbfe266fcd2", 182140, 0o444),
        "curl": ("74b4ce8f74b377f18ef1b3df7279c26cb3cd14c49e39ab1498575b209dc3f70f", 297288, 0o755),
    }
    assert set(authorities) == set(expected)
    for role, (digest, size, mode) in expected.items():
        identity = authorities[role]
        assert (identity["sha256"], identity["size_bytes"], identity["mode"]) == (digest, size, mode)
        assert (identity["uid"], identity["gid"], identity["nlink"], identity["st_dev"]) == (0, 0, 1, 64512)
        assert isinstance(identity["st_ino"], int) and identity["st_ino"] > 0
        assert Path(identity["path"]).is_absolute()
    assert document["reviewed_bindings"]["exact_authorities"] == authorities


def test_review_records_honest_audit_history_and_reconstruction() -> None:
    document, _payload, _sidecar = _build()
    assert document["audit_history"]["blocked_pair"] == {
        "status": "NO_GO",
        "severity_counts": {"P0": 0, "P1": 1, "P2": 0},
        "finding": "test_executed_helper_before_exact_hash_assertion",
        "artifact_kind": "embedded_exact_audit_assertion",
    }
    assert document["audit_history"]["executable_pair"]["status"] == "GO"
    assert document["audit_history"]["executable_pair"]["independent_exact_auditor_count"] == 2
    assert document["reconstruction"]["reverse_reconstruction_exact"] is True
    assert document["reviewed_bindings"]["executable"]["sha256"] == MODULE.EXPECTED_EXECUTABLE_SHA256
    assert document["reviewed_bindings"]["executable_tests"]["sha256"] == MODULE.EXPECTED_EXECUTABLE_TEST_SHA256


def test_residual_risks_and_authority_scope_are_explicit() -> None:
    document, _payload, _sidecar = _build()
    assert document["residual_risks"] == MODULE.RESIDUAL_THREAT_LIMITS
    assert all(document["residual_risks"].values())
    assert document["authority_scope"] == {
        "review_only": True,
        "timestamp_request_authorized": False,
        "timestamp_publication_authorized": False,
        "lifecycle_iq_or_outcome_authorized": False,
    }
    assert document["publication_scope"]["reports_namespace_immutable"] is False
    assert document["publication_scope"]["mandatory_downstream_exact_revalidation"] is True


def test_main_block_precedes_parser(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(MODULE, "RFC3161_REVIEW_GENERATOR_BLOCKED_PENDING_INDEPENDENT_AUDIT", True)
    monkeypatch.setattr(MODULE, "_validate_invocation", lambda: None)
    monkeypatch.setattr(MODULE, "parser", lambda: (_ for _ in ()).throw(AssertionError("parser reached")))
    with pytest.raises(MODULE.ReviewError, match="blocked"):
        MODULE.main([])


def test_block_is_enforced_at_direct_build_and_mutation_boundaries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(MODULE, "RFC3161_REVIEW_GENERATOR_BLOCKED_PENDING_INDEPENDENT_AUDIT", True)
    monkeypatch.setattr(
        MODULE, "_read_exact",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("read reached")),
    )
    with pytest.raises(MODULE.ReviewError, match="blocked"):
        MODULE.build_review(
            expected_generator_sha256="a" * 64,
            expected_generator_test_sha256="b" * 64,
        )
    with pytest.raises(MODULE.ReviewError, match="blocked"):
        MODULE.publish_review(
            expected_generator_sha256="a" * 64,
            expected_generator_test_sha256="b" * 64,
        )


@pytest.mark.parametrize("corruption", ["schema", "selfhash", "payload", "sidecar"])
def test_publisher_rebuilds_and_rejects_malformed_candidate_before_reads(
    monkeypatch: pytest.MonkeyPatch, corruption: str,
) -> None:
    document, payload, sidecar_payload = _candidate()
    if corruption == "schema":
        document = dict(document)
        document["schema_version"] = "foreign"
        payload = MODULE._canonical(document)
    elif corruption == "selfhash":
        document = dict(document)
        document["review_payload_sha256"] = "0" * 64
        payload = MODULE._canonical(document)
    elif corruption == "payload":
        payload += b"foreign"
    else:
        sidecar_payload = b"0" * len(sidecar_payload)
    monkeypatch.setattr(MODULE, "RFC3161_REVIEW_GENERATOR_BLOCKED_PENDING_INDEPENDENT_AUDIT", False)
    monkeypatch.setattr(MODULE.os, "geteuid", lambda: 0)
    monkeypatch.setattr(MODULE.os, "getegid", lambda: 0)
    monkeypatch.setattr(
        MODULE, "build_review", lambda **_kwargs: (document, payload, sidecar_payload),
    )
    monkeypatch.setattr(
        MODULE, "_read_exact",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("read reached")),
    )
    with pytest.raises(MODULE.ReviewError, match="contract differs|canonical bytes"):
        MODULE.publish_review(
            expected_generator_sha256="a" * 64,
            expected_generator_test_sha256="b" * 64,
        )


def test_default_unblocked_main_never_publishes(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(MODULE, "RFC3161_REVIEW_GENERATOR_BLOCKED_PENDING_INDEPENDENT_AUDIT", False)
    monkeypatch.setattr(MODULE, "_validate_invocation", lambda: None)
    class Parser:
        def parse_args(self, _argv: object) -> object:
            return types.SimpleNamespace(
                expected_generator_sha256="a" * 64,
                expected_generator_test_sha256="b" * 64,
                publish=False,
            )
    monkeypatch.setattr(MODULE, "parser", lambda: Parser())
    document = {"review_payload_sha256": "c" * 64}
    monkeypatch.setattr(MODULE, "build_review", lambda **_kwargs: (document, b"json\n", b"sidecar\n"))
    monkeypatch.setattr(MODULE, "publish_review", lambda *_args: (_ for _ in ()).throw(AssertionError("publish reached")))
    assert MODULE.main([]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["published"] is None


def test_publish_main_uses_only_publishers_single_candidate_and_receipt(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    document, _payload, _sidecar_payload = _candidate()
    unhashed = dict(document)
    unhashed.pop("review_payload_sha256")
    unhashed["reviewed_bindings"] = {
        "same_exact_bytes_new_inode_observed_by_single_build": {"st_ino": 2}
    }
    published_document = {
        **unhashed, "review_payload_sha256": MODULE._document_hash(unhashed),
    }
    published_payload = MODULE._canonical(published_document)
    published_sidecar = MODULE._sidecar(hashlib.sha256(published_payload).hexdigest())
    publication = {"review": {"st_ino": 2}}
    calls = 0

    class Parser:
        def parse_args(self, _argv: object) -> object:
            return types.SimpleNamespace(
                expected_generator_sha256="a" * 64,
                expected_generator_test_sha256="b" * 64,
                publish=True,
            )

    def publish(**_kwargs: object) -> tuple[dict[str, object], bytes, bytes, dict[str, object]]:
        nonlocal calls
        calls += 1
        return published_document, published_payload, published_sidecar, publication

    monkeypatch.setattr(MODULE, "RFC3161_REVIEW_GENERATOR_BLOCKED_PENDING_INDEPENDENT_AUDIT", False)
    monkeypatch.setattr(MODULE, "_validate_invocation", lambda: None)
    monkeypatch.setattr(MODULE, "parser", lambda: Parser())
    monkeypatch.setattr(
        MODULE, "build_review",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("split pre-publication build reached")),
    )
    monkeypatch.setattr(MODULE, "publish_review", publish)
    assert MODULE.main([]) == 0
    result = json.loads(capsys.readouterr().out)
    assert calls == 1
    assert result["document"] == published_document
    assert result["file_sha256"] == hashlib.sha256(published_payload).hexdigest()
    assert result["sidecar_sha256"] == hashlib.sha256(published_sidecar).hexdigest()
    assert result["published"] == publication


def _fake_helper(events: list[str]) -> object:
    class Helper:
        def _load_authorities(self) -> dict[str, object]:
            events.append("authorities")
            return {"amendment": {"fd": 9}}
        def _acquire_single_writer(self, _records: object) -> None:
            events.append("lock")
        def _open_reports(self) -> dict[str, object]:
            events.append("reports")
            return {"fd": 10}
        def _timestamp_state(self, _reports: object) -> dict[str, object]:
            events.append("fresh")
            return {"state": "FRESH", "query": None, "response": None}
        def _close_state(self, _state: object) -> None:
            pass
        def _revalidate_reports(self, _reports: object) -> None:
            events.append("parent")
        def _revalidate_authorities(self, _records: object) -> None:
            events.append("authority-paths")
        def _close_records(self, _records: object) -> None:
            events.append("close-authorities")
    return Helper()


def _candidate() -> tuple[dict[str, object], bytes, bytes]:
    unhashed: dict[str, object] = {
        "schema_version": MODULE.REVIEW_SCHEMA,
        "status": "GO",
        "severity_counts": dict(MODULE.SEVERITY_COUNTS),
        "reviewed_bindings": {},
        "reconstruction": {},
        "audit_history": {},
        "test_evidence": {},
        "fresh_state": {},
        "checks": MODULE.expected_checks(),
        "publication_scope": dict(MODULE.PUBLICATION_SCOPE),
        "residual_risks": dict(MODULE.RESIDUAL_THREAT_LIMITS),
        "authority_scope": {},
    }
    document = {**unhashed, "review_payload_sha256": MODULE._document_hash(unhashed)}
    payload = MODULE._canonical(document)
    return document, payload, MODULE._sidecar(hashlib.sha256(payload).hexdigest())


def _enable_publication_candidate(monkeypatch: pytest.MonkeyPatch) -> tuple[bytes, bytes]:
    document, payload, sidecar_payload = _candidate()
    monkeypatch.setattr(
        MODULE, "RFC3161_REVIEW_GENERATOR_BLOCKED_PENDING_INDEPENDENT_AUDIT", False,
    )
    monkeypatch.setattr(
        MODULE, "build_review",
        lambda **_kwargs: (document, payload, sidecar_payload),
    )
    return payload, sidecar_payload


@pytest.mark.parametrize(
    ("main_before", "sidecar_before", "expected_order"),
    [
        (None, None, [MODULE.SIDECAR.name, MODULE.OUTPUT.name]),
        (None, "sidecar", [MODULE.OUTPUT.name]),
        ("main", "sidecar", []),
    ],
)
def test_publication_fresh_sidecar_resume_and_pair_idempotence(
    monkeypatch: pytest.MonkeyPatch,
    main_before: str | None,
    sidecar_before: str | None,
    expected_order: list[str],
) -> None:
    payload, sidecar_payload = _enable_publication_candidate(monkeypatch)
    store: dict[str, bytes] = {}
    if main_before:
        store[MODULE.OUTPUT.name] = payload
    if sidecar_before:
        store[MODULE.SIDECAR.name] = sidecar_payload
    events: list[str] = []
    helper = _fake_helper(events)
    monkeypatch.setattr(MODULE.os, "geteuid", lambda: 0)
    monkeypatch.setattr(MODULE.os, "getegid", lambda: 0)
    monkeypatch.setattr(MODULE, "_read_exact", lambda *_args, **_kwargs: (b"helper", {}))
    monkeypatch.setattr(MODULE, "_load_helper", lambda _payload: helper)
    monkeypatch.setattr(MODULE.os, "close", lambda _fd: events.append("close-reports"))
    def read(_directory: int, path: Path, _maximum: int) -> object:
        value = store.get(path.name)
        return None if value is None else (
            value,
            {
                "path": str(path),
                "sha256": hashlib.sha256(value).hexdigest(),
                "st_dev": 1,
                "st_ino": 11 if path == MODULE.OUTPUT else 12,
            },
        )
    monkeypatch.setattr(MODULE, "_read_at", read)
    published: list[str] = []
    def publish(_directory: int, name: str, value: bytes) -> dict[str, object]:
        published.append(name)
        store[name] = value
        return {"sha256": hashlib.sha256(value).hexdigest()}
    monkeypatch.setattr(MODULE, "_publish_one_at", publish)
    published_document, published_payload, published_sidecar, result = MODULE.publish_review(
        expected_generator_sha256="a" * 64,
        expected_generator_test_sha256="b" * 64,
    )
    assert (published_payload, published_sidecar) == (payload, sidecar_payload)
    assert MODULE._canonical(published_document) == payload
    assert published == expected_order
    assert result["status"] == "PASS_ROOT_OWNED_READONLY_FILES_IN_MUTABLE_REPORTS_NAMESPACE"
    assert events.index("lock") < events.index("reports") < events.index("fresh")
    assert events.count("fresh") >= 5


@pytest.mark.parametrize("failure_ordinal", range(1, 7))
def test_timestamp_targets_are_rechecked_at_every_publication_boundary(
    monkeypatch: pytest.MonkeyPatch, failure_ordinal: int,
) -> None:
    payload, sidecar_payload = _enable_publication_candidate(monkeypatch)
    store: dict[str, bytes] = {}
    events: list[str] = []
    calls = 0

    class Helper:
        def _load_authorities(self) -> dict[str, object]:
            return {"amendment": {"fd": 9}}
        def _acquire_single_writer(self, _records: object) -> None:
            events.append("lock")
        def _open_reports(self) -> dict[str, object]:
            return {"fd": 10}
        def _timestamp_state(self, _reports: object) -> dict[str, object]:
            nonlocal calls
            calls += 1
            if calls == failure_ordinal:
                return {"state": "QUERY_COMMITTED", "query": {"fd": 21}, "response": None}
            return {"state": "FRESH", "query": None, "response": None}
        def _close_state(self, _state: object) -> None:
            pass
        def _revalidate_reports(self, _reports: object) -> None:
            pass
        def _revalidate_authorities(self, _records: object) -> None:
            pass
        def _close_records(self, _records: object) -> None:
            pass

    monkeypatch.setattr(MODULE.os, "geteuid", lambda: 0)
    monkeypatch.setattr(MODULE.os, "getegid", lambda: 0)
    monkeypatch.setattr(MODULE, "_read_exact", lambda *_args, **_kwargs: (b"helper", {}))
    monkeypatch.setattr(MODULE, "_load_helper", lambda _payload: Helper())
    monkeypatch.setattr(MODULE.os, "close", lambda _fd: None)

    def read(_directory: int, path: Path, _maximum: int) -> object:
        value = store.get(path.name)
        if value is None:
            return None
        return value, {
            "path": str(path), "sha256": hashlib.sha256(value).hexdigest(),
            "st_dev": 1, "st_ino": 11 if path == MODULE.OUTPUT else 12,
        }

    def publish(_directory: int, name: str, value: bytes) -> dict[str, object]:
        events.append(name)
        store[name] = value
        return {"sha256": hashlib.sha256(value).hexdigest()}

    monkeypatch.setattr(MODULE, "_read_at", read)
    monkeypatch.setattr(MODULE, "_publish_one_at", publish)
    with pytest.raises(MODULE.ReviewError, match="no longer both absent"):
        MODULE.publish_review(
            expected_generator_sha256="a" * 64,
            expected_generator_test_sha256="b" * 64,
        )
    assert calls == failure_ordinal
    if failure_ordinal <= 2:
        assert MODULE.SIDECAR.name not in store and MODULE.OUTPUT.name not in store
    elif failure_ordinal <= 4:
        assert store == {MODULE.SIDECAR.name: sidecar_payload}
    else:
        assert store == {MODULE.SIDECAR.name: sidecar_payload, MODULE.OUTPUT.name: payload}


@pytest.mark.parametrize(
    ("main", "sidecar", "message"),
    [
        (None, b"foreign-sidecar\n", "sidecar differs"),
        (b"foreign-main\n", None, "document differs"),
    ],
)
def test_publication_rejects_foreign_exact_names_before_mutation(
    monkeypatch: pytest.MonkeyPatch,
    main: bytes | None,
    sidecar: bytes | None,
    message: str,
) -> None:
    events: list[str] = []
    helper = _fake_helper(events)
    _payload, expected_sidecar = _enable_publication_candidate(monkeypatch)
    store = {MODULE.SIDECAR.name: expected_sidecar if sidecar is None else sidecar}
    if main is not None:
        store[MODULE.OUTPUT.name] = main
    monkeypatch.setattr(MODULE.os, "geteuid", lambda: 0)
    monkeypatch.setattr(MODULE.os, "getegid", lambda: 0)
    monkeypatch.setattr(MODULE, "_read_exact", lambda *_args, **_kwargs: (b"helper", {}))
    monkeypatch.setattr(MODULE, "_load_helper", lambda _payload: helper)
    monkeypatch.setattr(MODULE.os, "close", lambda _fd: None)
    monkeypatch.setattr(
        MODULE, "_read_at",
        lambda _fd, path, _maximum: None if path.name not in store else (store[path.name], {}),
    )
    monkeypatch.setattr(
        MODULE, "_publish_one_at",
        lambda *_args: (_ for _ in ()).throw(AssertionError("mutation reached")),
    )
    with pytest.raises(MODULE.ReviewError, match=message):
        MODULE.publish_review(
            expected_generator_sha256="a" * 64,
            expected_generator_test_sha256="b" * 64,
        )


def test_publication_rejects_json_only_before_mutation(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[str] = []
    helper = _fake_helper(events)
    _enable_publication_candidate(monkeypatch)
    monkeypatch.setattr(MODULE.os, "geteuid", lambda: 0)
    monkeypatch.setattr(MODULE.os, "getegid", lambda: 0)
    monkeypatch.setattr(MODULE, "_read_exact", lambda *_args, **_kwargs: (b"helper", {}))
    monkeypatch.setattr(MODULE, "_load_helper", lambda _payload: helper)
    monkeypatch.setattr(MODULE.os, "close", lambda _fd: None)
    monkeypatch.setattr(MODULE, "_read_at", lambda _fd, path, _max: (b"document\n", {}) if path == MODULE.OUTPUT else None)
    monkeypatch.setattr(MODULE, "_publish_one_at", lambda *_args: (_ for _ in ()).throw(AssertionError("mutation reached")))
    with pytest.raises(MODULE.ReviewError, match="main-only"):
        MODULE.publish_review(
            expected_generator_sha256="a" * 64,
            expected_generator_test_sha256="b" * 64,
        )


def test_publication_requires_root_before_reads(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(MODULE, "RFC3161_REVIEW_GENERATOR_BLOCKED_PENDING_INDEPENDENT_AUDIT", False)
    monkeypatch.setattr(MODULE.os, "geteuid", lambda: 1000)
    monkeypatch.setattr(MODULE, "_read_exact", lambda *_args: (_ for _ in ()).throw(AssertionError("read reached")))
    with pytest.raises(MODULE.ReviewError, match="root"):
        MODULE.publish_review(
            expected_generator_sha256="a" * 64,
            expected_generator_test_sha256="b" * 64,
        )


class _FakeLink:
    def __init__(self, code: int) -> None:
        self.code = code
        self.argtypes: object = None
        self.restype: object = None
    def __call__(self, *_args: object) -> int:
        MODULE.ctypes.set_errno(self.code)
        return -1


def test_publish_one_partial_write_and_collision(monkeypatch: pytest.MonkeyPatch) -> None:
    writes: list[int] = []
    monkeypatch.setattr(MODULE.os, "open", lambda *_args, **_kwargs: 7)
    monkeypatch.setattr(MODULE.os, "write", lambda _fd, view: writes.append(len(view)) or 1)
    monkeypatch.setattr(MODULE.os, "fchown", lambda *_args: None)
    monkeypatch.setattr(MODULE.os, "fchmod", lambda *_args: None)
    monkeypatch.setattr(MODULE.os, "fsync", lambda *_args: None)
    monkeypatch.setattr(MODULE.os, "fstat", lambda _fd: types.SimpleNamespace(st_uid=0, st_gid=0, st_mode=stat.S_IFREG | 0o444, st_nlink=0, st_size=3))
    monkeypatch.setattr(MODULE.os, "close", lambda _fd: None)
    monkeypatch.setattr(MODULE.ctypes, "CDLL", lambda *_args, **_kwargs: types.SimpleNamespace(linkat=_FakeLink(MODULE.errno.EEXIST)))
    with pytest.raises(MODULE.ReviewError, match="overwrite"):
        MODULE._publish_one_at(8, "x", b"abc")
    assert writes == [3, 2, 1]


@pytest.mark.skipif(os.geteuid() != 0, reason="root O_TMPFILE integration")
def test_root_publish_one_no_clobber(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    os.chown(tmp_path, 0, 0)
    os.chmod(tmp_path, 0o755)
    descriptor = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    monkeypatch.setattr(MODULE, "REPORTS", tmp_path)
    try:
        identity = MODULE._publish_one_at(descriptor, "review", b"payload")
        assert identity["sha256"] == hashlib.sha256(b"payload").hexdigest()
        status = (tmp_path / "review").lstat()
        assert (status.st_uid, status.st_gid, stat.S_IMODE(status.st_mode), status.st_nlink) == (0, 0, 0o444, 1)
        with pytest.raises(MODULE.ReviewError, match="overwrite"):
            MODULE._publish_one_at(descriptor, "review", b"different")
    finally:
        os.close(descriptor)


@pytest.mark.skipif(os.geteuid() != 0, reason="root publication metadata integration")
def test_root_read_at_rejects_symlink_and_hardlink(tmp_path: Path) -> None:
    os.chown(tmp_path, 0, 0)
    os.chmod(tmp_path, 0o755)
    target = tmp_path / "target"
    target.write_bytes(b"payload")
    os.chown(target, 0, 0)
    os.chmod(target, 0o444)
    symlink = tmp_path / "symlink"
    symlink.symlink_to(target.name)
    directory = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    original_reports = MODULE.REPORTS
    MODULE.REPORTS = tmp_path
    try:
        with pytest.raises(MODULE.ReviewError, match="metadata differs"):
            MODULE._read_at(directory, symlink, 64)
        hardlink = tmp_path / "hardlink"
        os.link(target, hardlink)
        with pytest.raises(MODULE.ReviewError, match="metadata differs"):
            MODULE._read_at(directory, target, 64)
    finally:
        MODULE.REPORTS = original_reports
        os.close(directory)
