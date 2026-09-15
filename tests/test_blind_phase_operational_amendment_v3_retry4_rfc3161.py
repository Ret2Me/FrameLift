from __future__ import annotations

import ast
from collections import Counter
import errno
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import types

import pytest


ROOT = Path("/home/ubuntu/telemetry-yield")
SOURCE = ROOT / "work/blind-phase-confirmatory-v2/build_operational_amendment_v3_retry4_rfc3161.py"
SOURCE_BYTES = SOURCE.read_bytes()
EXPECTED_SOURCE_SHA256 = "7d4074babca87ced6043a1a348d27d9827c7ef6c65abc0ac278698ba78e7ab7b"
EXPECTED_SOURCE_SIZE = 46228
EXPECTED_SOURCE_SEMANTIC_AST_SHA256 = "280af66104c7f1909b35286834c381632cc394d12f6b289199468c502d28fcfa"
EXPECTED_SCOPED_CALL_INVENTORY_SHA256 = "c3981b2f76175ae9bc829282a9df8ee0ba823234945389895260551baaecde48"
EXPECTED_ASSIGNMENT_INVENTORY_SHA256 = "03ae9a7b5968530f4d1e83edca62c125265fd8440ad883696e7f8f0e79bccfd3"
EXPECTED_FUNCTION_SIGNATURE_INVENTORY_SHA256 = "7c1d969f39a387e8d098bcbd1f715823bd647408bd4c7c25949b621460721442"


def _verified_module(payload: bytes, expected_size: int, expected_sha256: str) -> types.ModuleType:
    if len(payload) != expected_size or hashlib.sha256(payload).hexdigest() != expected_sha256:
        raise AssertionError("retry4 RFC3161 helper differs before execution")
    ast.parse(payload)
    module = types.ModuleType("held_retry4_rfc3161")
    module.__file__ = str(SOURCE)
    module.__package__ = None
    exec(compile(payload, str(SOURCE), "exec"), module.__dict__)
    return module


MODULE = _verified_module(SOURCE_BYTES, EXPECTED_SOURCE_SIZE, EXPECTED_SOURCE_SHA256)


def _call_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _call_name(node.value)
        return f"{parent}.{node.attr}" if parent else None
    return None


def _function(node: ast.AST, parents: dict[int, ast.AST]) -> str:
    current = node
    while id(current) in parents:
        current = parents[id(current)]
        if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return current.name
    return "<module>"


def _static_surface(source: bytes) -> dict[str, object]:
    tree = ast.parse(source)
    parents: dict[int, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[id(child)] = parent
    scoped_calls = sorted(
        (_function(node, parents), ast.dump(node, include_attributes=False))
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
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
    def digest(value: object) -> str:
        return hashlib.sha256(
            json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
    if hashlib.sha256(ast.dump(tree, include_attributes=False).encode("utf-8")).hexdigest() != EXPECTED_SOURCE_SEMANTIC_AST_SHA256:
        raise AssertionError("exact semantic AST identity differs")
    if digest(scoped_calls) != EXPECTED_SCOPED_CALL_INVENTORY_SHA256:
        raise AssertionError("scoped call inventory differs")
    if digest(assignments) != EXPECTED_ASSIGNMENT_INVENTORY_SHA256:
        raise AssertionError("assignment/alias inventory differs")
    if digest(signatures) != EXPECTED_FUNCTION_SIGNATURE_INVENTORY_SHA256:
        raise AssertionError("function signature/default inventory differs")
    imports = [ast.unparse(node) for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom))]
    expected_imports = [
        "from __future__ import annotations", "import argparse", "import ctypes",
        "import errno", "import fcntl", "import hashlib", "import json", "import os",
        "from pathlib import Path", "import re", "import selectors", "import stat",
        "import subprocess", "import sys", "import tempfile", "import time",
        "from typing import Any, Mapping, Sequence",
    ]
    if imports != expected_imports:
        raise AssertionError("import allowlist differs")
    forbidden_fragments = (
        "lifecycle", "decoder", "evaluator", "campaign", "outcome", "iq_",
        "unshare", "mount", "umount", "kill", "signal", "fork", "spawn",
    )
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            lowered = node.value.casefold()
            if any(fragment in lowered for fragment in forbidden_fragments):
                # Human-facing negative-scope declarations are the only allowed
                # occurrences of scientific/lifecycle vocabulary.
                if not any(marker in lowered for marker in ("no lifecycle", "no_lifecycle")):
                    raise AssertionError("forbidden lifecycle/IQ string added")

    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)]
    subprocess_calls = [(name := _call_name(node.func), _function(node, parents), node) for node in calls if (name := _call_name(node.func)) and name.startswith("subprocess.")]
    if [(name, owner) for name, owner, _node in subprocess_calls] != [("subprocess.Popen", "_run_pinned")]:
        raise AssertionError("subprocess surface differs")
    popen = subprocess_calls[0][2]
    keywords = {item.arg: ast.unparse(item.value) for item in popen.keywords}
    if keywords.get("shell") != "False" or keywords.get("env") != "minimal_env" or keywords.get("pass_fds") != "inherited":
        raise AssertionError("Popen safety arguments differ")

    exact_mutators = Counter(
        (_call_name(node.func), _function(node, parents))
        for node in calls
        if _call_name(node.func) in {
            "os.write", "os.fchown", "os.fchmod", "os.fsync", "os.unlink",
            "os.rmdir", "os.chmod", "linkat",
        }
    )
    expected_mutators = Counter(
        {
            ("os.chmod", "__init__"): 1,
            ("os.rmdir", "__init__"): 1,
            ("os.write", "write"): 1,
            ("os.fsync", "write"): 1,
            ("os.unlink", "cleanup"): 1,
            ("os.fsync", "cleanup"): 1,
            ("os.rmdir", "cleanup"): 1,
            ("os.write", "_publish_one_at"): 1,
            ("os.fchown", "_publish_one_at"): 1,
            ("os.fchmod", "_publish_one_at"): 1,
            ("os.fsync", "_publish_one_at"): 2,
            ("linkat", "_publish_one_at"): 1,
            ("os.unlink", "_rollback_own_response_at"): 1,
            ("os.fsync", "_rollback_own_response_at"): 1,
        }
    )
    if exact_mutators != expected_mutators:
        raise AssertionError("mutation surface differs")
    forbidden_calls = {
        "os.rename", "os.replace", "os.remove", "os.truncate", "os.ftruncate",
        "os.mkdir", "os.makedirs", "os.link", "os.symlink", "shutil.rmtree",
        "subprocess.run", "subprocess.call", "subprocess.check_call", "os.system",
        "os.fork", "os.unshare", "os.kill",
    }
    observed_names = {_call_name(node.func) for node in calls}
    if observed_names & forbidden_calls:
        raise AssertionError("forbidden mutation/process primitive added")

    open_calls = [(node, _function(node, parents)) for node in calls if _call_name(node.func) == "os.open"]
    expected_open_owners = Counter({
        "_open_exact": 1, "_open_reports": 1, "_target_at": 1,
        "__init__": 1, "create": 1, "_publish_one_at": 1,
    })
    if Counter(owner for _node, owner in open_calls) != expected_open_owners:
        raise AssertionError("os.open owner/count surface differs")
    write_flag_owners = []
    for node, owner in open_calls:
        rendered = ast.unparse(node)
        if any(flag in rendered for flag in ("O_RDWR", "O_WRONLY", "O_CREAT", "O_TMPFILE")):
            write_flag_owners.append((owner, rendered))
    if Counter(owner for owner, _rendered in write_flag_owners) != Counter({"create": 1, "_publish_one_at": 1}):
        raise AssertionError("write-capable os.open scope differs")
    by_owner = dict(write_flag_owners)
    if "O_CREAT" not in by_owner["create"] or "O_EXCL" not in by_owner["create"] or "O_NOFOLLOW" not in by_owner["create"]:
        raise AssertionError("private temp create flags differ")
    if "O_TMPFILE" not in by_owner["_publish_one_at"] or "O_CREAT" in by_owner["_publish_one_at"]:
        raise AssertionError("publisher anonymous inode flags differ")

    main = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "main")
    if ast.unparse(main.body[0]) != "_validate_invocation()":
        raise AssertionError("invocation gate is not first in main")
    if not isinstance(main.body[1], ast.If) or "BLOCKED" not in ast.unparse(main.body[1].test):
        raise AssertionError("block is not before parser")
    request_calls = [(node, _function(node, parents)) for node in calls if _call_name(node.func) == "_request_candidate"]
    publish_calls = [(node, _function(node, parents)) for node in calls if _call_name(node.func) == "_publish_candidate"]
    if len(request_calls) != 1 or request_calls[0][1] != "prepare" or len(publish_calls) != 1 or publish_calls[0][1] != "prepare":
        raise AssertionError("request/publication call surface differs")
    return {
        "subprocess_popen_count": 1,
        "write_capable_open_count": 2,
        "publication_call_count": 1,
        "network_request_call_count": 1,
    }


def _fake_records() -> dict[str, dict[str, object]]:
    return {
        name: {"fd": 100 + index, "payload": b"x", "identity": {"path": name, "sha256": "0" * 64, "size_bytes": 1}}
        for index, name in enumerate(
            ("amendment", "amendment_sidecar", "review", "review_sidecar", "openssl", "openssl_config", "tsa_ca", "curl")
        )
    }


def _state(name: str, query: bytes | None = None, response: bytes | None = None) -> dict[str, object]:
    def record(payload: bytes, descriptor: int) -> dict[str, object]:
        return {
            "fd": descriptor,
            "payload": payload,
            "identity": {"sha256": hashlib.sha256(payload).hexdigest(), "size_bytes": len(payload)},
        }
    return {
        "state": name,
        "query": None if query is None else record(query, 200),
        "response": None if response is None else record(response, 201),
    }


def test_source_compiles_and_is_controlled_executable_refreeze() -> None:
    assert len(SOURCE_BYTES) == EXPECTED_SOURCE_SIZE
    assert hashlib.sha256(SOURCE_BYTES).hexdigest() == EXPECTED_SOURCE_SHA256
    compile(SOURCE_BYTES, str(SOURCE), "exec")
    assert MODULE.RETRY4_RFC3161_BLOCKED_PENDING_INDEPENDENT_REVIEW is False
    assert MODULE.ENDPOINT == "http://timestamp.digicert.com"
    assert MODULE.EXPECTED_CURL_SHA256 == "74b4ce8f74b377f18ef1b3df7279c26cb3cd14c49e39ab1498575b209dc3f70f"
    assert MODULE.EXPECTED_CURL_SIZE == 297288


def test_executable_reverse_reconstructs_exact_audited_blocked_design() -> None:
    executable = b"RETRY4_RFC3161_BLOCKED_PENDING_INDEPENDENT_REVIEW = False\n"
    blocked = b"RETRY4_RFC3161_BLOCKED_PENDING_INDEPENDENT_REVIEW = True\n"
    assert SOURCE_BYTES.count(executable) == 1
    reconstructed = SOURCE_BYTES.replace(executable, blocked)
    assert len(reconstructed) == 46227
    assert hashlib.sha256(reconstructed).hexdigest() == "14817f4424197a709f9f78c7f0802a6797945d59888bdaaec0268195f759d09a"


def test_tampered_module_scope_bytes_are_rejected_before_execution(tmp_path: Path) -> None:
    sentinel = tmp_path / "must-not-exist"
    tampered = SOURCE_BYTES + f"\nPath({str(sentinel)!r}).write_text('executed')\n".encode()
    with pytest.raises(AssertionError, match="before execution"):
        _verified_module(tampered, EXPECTED_SOURCE_SIZE, EXPECTED_SOURCE_SHA256)
    assert not sentinel.exists()


def test_static_effect_surface_is_exact() -> None:
    assert _static_surface(SOURCE_BYTES) == {
        "subprocess_popen_count": 1,
        "write_capable_open_count": 2,
        "publication_call_count": 1,
        "network_request_call_count": 1,
    }


@pytest.mark.parametrize(
    "fragment",
    [
        b"\ndef rogue():\n    os.system('id')\n",
        b"\ndef rogue():\n    os.open('/etc/passwd', os.O_WRONLY)\n",
        b"\ndef rogue():\n    os.rename('/tmp/a', '/tmp/b')\n",
        b"\ndef rogue():\n    subprocess.run(['/bin/true'])\n",
        b"\ndef rogue():\n    os.unshare(1)\n",
        b"\ndef rogue():\n    _publish_candidate({}, {}, {})\n",
        b"\ndef rogue():\n    _request_candidate({}, None, None)\n",
        b"\ndef rogue():\n    f = os.system\n    f('id')\n",
        b"\ndef rogue():\n    getattr(os, 'system')('id')\n",
        b"\ndef rogue(callback=os.system):\n    callback('id')\n",
        b"\ndef rogue():\n    __import__('os').system('id')\n",
        b"\ndef rogue():\n    Path('/tmp/forbidden').write_bytes(b'x')\n",
        b"\ndef rogue():\n    f = _request_candidate\n    f({}, None, None)\n",
        b"\ndef rogue():\n    f = _publish_candidate\n    f({}, {}, {})\n",
        b"\ndef rogue():\n    exec('pass')\n",
    ],
)
def test_static_surface_rejects_mutants(fragment: bytes) -> None:
    with pytest.raises(AssertionError):
        _static_surface(SOURCE_BYTES + fragment)


def test_main_gates_invocation_before_default_read_only_path(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    events: list[str] = []
    monkeypatch.setattr(MODULE, "_validate_invocation", lambda: events.append("invocation"))
    class Parser:
        def parse_args(self, argv: object) -> object:
            events.append("parser")
            assert argv == []
            return types.SimpleNamespace(request=False, publish=False)
    monkeypatch.setattr(MODULE, "parser", lambda: Parser())
    monkeypatch.setattr(
        MODULE,
        "prepare",
        lambda *, request, publish: events.append(f"prepare:{request}:{publish}")
        or {"status": "PASS_READ_ONLY_NO_REQUEST_NO_PUBLICATION"},
    )
    assert MODULE.main([]) == 0
    assert events == ["invocation", "parser", "prepare:False:False"]
    assert json.loads(capsys.readouterr().out)["status"] == "PASS_READ_ONLY_NO_REQUEST_NO_PUBLICATION"


def test_block_when_enabled_still_precedes_parser(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(MODULE, "_validate_invocation", lambda: None)
    monkeypatch.setattr(MODULE, "RETRY4_RFC3161_BLOCKED_PENDING_INDEPENDENT_REVIEW", True)
    monkeypatch.setattr(MODULE, "parser", lambda: (_ for _ in ()).throw(AssertionError("parser reached")))
    with pytest.raises(MODULE.TimestampError, match="blocked"):
        MODULE.main([])


def test_publish_requires_separate_request_before_any_read(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(MODULE, "_load_authorities", lambda: (_ for _ in ()).throw(AssertionError("read reached")))
    with pytest.raises(MODULE.TimestampError, match="requires.*--request"):
        MODULE.prepare(request=False, publish=True)


def test_default_fresh_is_no_network_no_temp_no_publication(monkeypatch: pytest.MonkeyPatch) -> None:
    records = _fake_records()
    reports = {"fd": 99}
    monkeypatch.setattr(MODULE, "_load_authorities", lambda: records)
    monkeypatch.setattr(MODULE, "_acquire_single_writer", lambda _records: None)
    monkeypatch.setattr(MODULE, "_open_reports", lambda: reports)
    monkeypatch.setattr(MODULE, "_timestamp_state", lambda _reports: _state("FRESH"))
    monkeypatch.setattr(MODULE, "_revalidate_reports", lambda _reports: None)
    monkeypatch.setattr(MODULE, "_revalidate_authorities", lambda _records: None)
    monkeypatch.setattr(MODULE, "_revalidate_state", lambda _reports, _state: None)
    monkeypatch.setattr(MODULE, "_close_records", lambda _records: None)
    monkeypatch.setattr(MODULE.os, "close", lambda _fd: None)
    monkeypatch.setattr(MODULE, "_PrivateArea", lambda: (_ for _ in ()).throw(AssertionError("temp reached")))
    monkeypatch.setattr(MODULE, "_request_candidate", lambda *_args: (_ for _ in ()).throw(AssertionError("network reached")))
    monkeypatch.setattr(MODULE, "_publish_candidate", lambda *_args: (_ for _ in ()).throw(AssertionError("publisher reached")))
    result = MODULE.prepare(request=False, publish=False)
    assert result["status"] == "PASS_READ_ONLY_NO_REQUEST_NO_PUBLICATION"
    assert result["network_requested"] is False
    assert result["publication_requested"] is False
    assert result["candidate"] is None


def test_query_only_default_validates_without_network_or_temp(monkeypatch: pytest.MonkeyPatch) -> None:
    records = _fake_records()
    state = _state("QUERY_COMMITTED", b"query")
    monkeypatch.setattr(MODULE, "_load_authorities", lambda: records)
    monkeypatch.setattr(MODULE, "_acquire_single_writer", lambda _records: None)
    monkeypatch.setattr(MODULE, "_open_reports", lambda: {"fd": 99})
    monkeypatch.setattr(MODULE, "_timestamp_state", lambda _reports: state)
    monkeypatch.setattr(MODULE, "_validate_query", lambda fd, _records, label: {"fd": fd, "label": label})
    monkeypatch.setattr(MODULE, "_revalidate_reports", lambda _reports: None)
    monkeypatch.setattr(MODULE, "_revalidate_authorities", lambda _records: None)
    monkeypatch.setattr(MODULE, "_revalidate_state", lambda _reports, _state: None)
    monkeypatch.setattr(MODULE, "_close_records", lambda _records: None)
    monkeypatch.setattr(MODULE, "_close_state", lambda _state: None)
    monkeypatch.setattr(MODULE.os, "close", lambda _fd: None)
    monkeypatch.setattr(MODULE, "_PrivateArea", lambda: (_ for _ in ()).throw(AssertionError("temp reached")))
    result = MODULE.prepare(request=False, publish=False)
    assert result["initial_state"] == "QUERY_COMMITTED"


def test_pair_default_performs_read_only_crypto_validation(monkeypatch: pytest.MonkeyPatch) -> None:
    records = _fake_records()
    state = _state("PAIR_COMMITTED", b"query", b"response")
    validation = {
        "message_imprint_sha256": MODULE.EXPECTED_AMENDMENT_SHA256,
        "nonce": "0x01",
        "status": "Granted",
    }
    monkeypatch.setattr(MODULE, "_load_authorities", lambda: records)
    monkeypatch.setattr(MODULE, "_acquire_single_writer", lambda _records: None)
    monkeypatch.setattr(MODULE, "_open_reports", lambda: {"fd": 99})
    monkeypatch.setattr(MODULE, "_timestamp_state", lambda _reports: state)
    monkeypatch.setattr(MODULE, "_validate_response", lambda *_args: validation)
    monkeypatch.setattr(MODULE, "_revalidate_reports", lambda _reports: None)
    monkeypatch.setattr(MODULE, "_revalidate_authorities", lambda _records: None)
    monkeypatch.setattr(MODULE, "_revalidate_state", lambda _reports, _state: None)
    monkeypatch.setattr(MODULE, "_close_records", lambda _records: None)
    monkeypatch.setattr(MODULE, "_close_state", lambda _state: None)
    monkeypatch.setattr(MODULE.os, "close", lambda _fd: None)
    monkeypatch.setattr(MODULE, "_PrivateArea", lambda: (_ for _ in ()).throw(AssertionError("temp reached")))
    result = MODULE.prepare(request=False, publish=False)
    assert result["initial_state"] == "PAIR_COMMITTED"
    assert result["status"] == "PASS_POINT_IN_TIME_ROOT_OWNED_READONLY_PAIR"
    assert result["candidate"]["response_validation"] == validation


def test_request_and_publish_are_independent_gates(monkeypatch: pytest.MonkeyPatch) -> None:
    records = _fake_records()
    reports = {"fd": 99}
    state = _state("FRESH")
    candidate = {
        "query": b"q", "response": b"r", "query_validation": {},
        "response_validation": {}, "response_content_type": "application/timestamp-reply",
    }
    class Area:
        def cleanup(self) -> None:
            pass
    monkeypatch.setattr(MODULE, "_load_authorities", lambda: records)
    monkeypatch.setattr(MODULE, "_acquire_single_writer", lambda _records: None)
    monkeypatch.setattr(MODULE, "_open_reports", lambda: reports)
    published: list[bool] = []
    monkeypatch.setattr(
        MODULE,
        "_timestamp_state",
        lambda _reports: _state("PAIR_COMMITTED", b"q", b"r") if published else state,
    )
    monkeypatch.setattr(MODULE, "_PrivateArea", Area)
    monkeypatch.setattr(MODULE, "_request_candidate", lambda *_args: candidate)
    monkeypatch.setattr(MODULE, "_publish_candidate", lambda *_args: published.append(True) or {"state": "PAIR_COMMITTED"})
    monkeypatch.setattr(MODULE, "_revalidate_reports", lambda _reports: None)
    monkeypatch.setattr(MODULE, "_revalidate_authorities", lambda _records: None)
    monkeypatch.setattr(MODULE, "_revalidate_state", lambda _reports, _state: None)
    monkeypatch.setattr(MODULE, "_close_records", lambda _records: None)
    monkeypatch.setattr(MODULE, "_close_state", lambda _state: None)
    monkeypatch.setattr(MODULE.os, "close", lambda _fd: None)
    no_publish = MODULE.prepare(request=True, publish=False)
    assert no_publish["status"] == "PASS_VERIFIED_CANDIDATE_NOT_PUBLISHED"
    assert published == []
    with_publish = MODULE.prepare(request=True, publish=True)
    assert with_publish["publication"] == {"state": "PAIR_COMMITTED"}
    assert published == [True]


@pytest.mark.parametrize("content_type", ["application/timestamp-reply", "application/timestamp-response"])
def test_allowed_digicert_content_types(content_type: str) -> None:
    headers = f"HTTP/1.1 200 OK\r\nContent-Type: {content_type}\r\n\r\n".encode()
    assert MODULE._response_content_type(headers) == content_type


@pytest.mark.parametrize(
    "headers",
    [
        b"HTTP/1.1 302 Found\r\nContent-Type: application/timestamp-reply\r\n\r\n",
        b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\n\r\n",
        b"HTTP/1.1 200 OK\r\n\r\n",
        b"HTTP/1.1 200 OK\r\nContent-Type: application/timestamp-reply\r\nContent-Type: application/timestamp-reply\r\n\r\n",
    ],
)
def test_rejects_bad_http_status_or_content_type(headers: bytes) -> None:
    with pytest.raises(MODULE.TimestampError):
        MODULE._response_content_type(headers)


def test_imprint_and_nonce_parsers_are_exact() -> None:
    digest = MODULE.EXPECTED_AMENDMENT_SHA256
    spaced = " ".join(digest[index:index + 2] for index in range(0, len(digest), 2))
    text = f"Message data:\n    {spaced}\nPolicy OID: 1.2.3\nNonce: 0xABC\n"
    assert MODULE._extract_imprint(text) == digest
    assert MODULE._extract_nonce(text) == "0xabc"


def test_openssl_config_diagnostic_must_name_pinned_fd() -> None:
    MODULE._require_config_stderr(b"Using configuration from /proc/self/fd/17\n", 17, "query")
    with pytest.raises(MODULE.TimestampError, match="explicit-config"):
        MODULE._require_config_stderr(b"Using configuration from /etc/ssl/openssl.cnf\n", 17, "query")


def test_request_uses_exact_curl_contract_and_existing_query(monkeypatch: pytest.MonkeyPatch) -> None:
    records = _fake_records()
    existing = _state("QUERY_COMMITTED", b"existing")["query"]
    calls: list[tuple[int, list[str], tuple[int, ...], str]] = []
    class Area:
        def create(self, name: str) -> int:
            return {"candidate-response": 310, "response-headers": 311}[name]
    def run(executable: int, arguments: list[str], pass_fds: list[int], label: str, **_kwargs: object) -> tuple[bytes, bytes]:
        calls.append((executable, arguments, tuple(pass_fds), label))
        return b"", b""
    monkeypatch.setattr(MODULE, "_validate_query", lambda *_args: {"message_imprint_sha256": MODULE.EXPECTED_AMENDMENT_SHA256, "nonce": "0x1"})
    monkeypatch.setattr(MODULE, "_run_pinned", run)
    monkeypatch.setattr(MODULE, "_read_fd", lambda fd, _maximum: b"headers" if fd == 311 else b"response")
    monkeypatch.setattr(MODULE, "_response_content_type", lambda _headers: "application/timestamp-reply")
    monkeypatch.setattr(MODULE, "_validate_response", lambda *_args: {"status": "Granted"})
    monkeypatch.setattr(MODULE.os, "close", lambda _fd: None)
    result = MODULE._request_candidate(records, existing, Area())
    assert result["query"] == b"existing"
    curl = calls[0]
    assert curl[0] == records["curl"]["fd"]
    assert curl[1][0] == "--disable"
    assert "--fail" in curl[1] and "--silent" in curl[1] and "--show-error" in curl[1]
    assert ["--proto", "=http"] == curl[1][4:6]
    assert "--max-redirs" in curl[1] and "0" in curl[1]
    assert ["--max-filesize", str(MODULE.MAX_RESPONSE_BYTES)] == curl[1][curl[1].index("--max-filesize"):curl[1].index("--max-filesize") + 2]
    assert curl[1].count("--header") == 2
    assert "Content-Type: application/timestamp-query" in curl[1]
    assert "Accept: application/timestamp-reply" in curl[1]
    assert curl[1][-1] == MODULE.ENDPOINT
    assert "@/proc/self/fd/200" in curl[1]
    assert ["--dump-header", "-"] == curl[1][curl[1].index("--dump-header"):curl[1].index("--dump-header") + 2]
    assert calls[0][3] == "curl"


def test_query_validation_rejects_wrong_imprint(monkeypatch: pytest.MonkeyPatch) -> None:
    text = "Hash Algorithm: sha256\nMessage data:\n    " + " ".join(["00"] * 32) + "\nPolicy OID: 1.2\nNonce: 0x1\nCertificate required: yes\n"
    monkeypatch.setattr(MODULE, "_run_pinned", lambda *_args, **_kwargs: (text.encode(), b""))
    monkeypatch.setattr(MODULE, "_require_config_stderr", lambda *_args: None)
    with pytest.raises(MODULE.TimestampError, match="imprint differs"):
        MODULE._validate_query(3, _fake_records(), "query")


def test_response_requires_granted_and_matching_nonce(monkeypatch: pytest.MonkeyPatch) -> None:
    digest = MODULE.EXPECTED_AMENDMENT_SHA256
    spaced = " ".join(digest[index:index + 2] for index in range(0, 64, 2))
    reply = f"Status: Granted.\nHash Algorithm: sha256\nMessage data:\n  {spaced}\nSerial number: 0x1\nPolicy OID: 1.2.3\nNonce: 0x2\nTime stamp: Sep  6 19:00:00 2026 GMT\n"
    monkeypatch.setattr(MODULE, "_validate_query", lambda *_args: {"message_imprint_sha256": digest, "nonce": "0x1"})
    monkeypatch.setattr(MODULE, "_run_pinned", lambda *_args, **_kwargs: (reply.encode(), b""))
    monkeypatch.setattr(MODULE, "_require_config_stderr", lambda *_args: None)
    with pytest.raises(MODULE.TimestampError, match="nonce differs"):
        MODULE._validate_response(3, 4, _fake_records())


def test_response_rejects_duplicate_security_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    digest = MODULE.EXPECTED_AMENDMENT_SHA256
    spaced = " ".join(digest[index:index + 2] for index in range(0, 64, 2))
    reply = f"Status: Granted.\nStatus: Granted.\nHash Algorithm: sha256\nMessage data:\n  {spaced}\nSerial number: 0x1\nPolicy OID: 1.2.3\nNonce: 0x1\nTime stamp: Sep  6 19:00:00 2026 GMT\n"
    monkeypatch.setattr(MODULE, "_validate_query", lambda *_args: {"message_imprint_sha256": digest, "nonce": "0x1"})
    monkeypatch.setattr(MODULE, "_run_pinned", lambda *_args, **_kwargs: (reply.encode(), b""))
    monkeypatch.setattr(MODULE, "_require_config_stderr", lambda *_args: None)
    with pytest.raises(MODULE.TimestampError, match="not Granted"):
        MODULE._validate_response(3, 4, _fake_records())


def test_single_writer_lock_is_exact_exclusive_nonblocking(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[int, int]] = []
    monkeypatch.setattr(MODULE.fcntl, "flock", lambda descriptor, flags: calls.append((descriptor, flags)))
    MODULE._acquire_single_writer({"amendment": {"fd": 73}})
    assert calls == [(73, MODULE.fcntl.LOCK_EX | MODULE.fcntl.LOCK_NB)]


def test_private_write_failure_closes_created_descriptor(monkeypatch: pytest.MonkeyPatch) -> None:
    area = object.__new__(MODULE._PrivateArea)
    monkeypatch.setattr(area, "create", lambda _name: 44)
    closed: list[int] = []
    monkeypatch.setattr(MODULE.os, "write", lambda *_args: (_ for _ in ()).throw(OSError("fail")))
    monkeypatch.setattr(MODULE.os, "close", lambda fd: closed.append(fd))
    with pytest.raises(OSError, match="fail"):
        area.write("candidate-query", b"q")
    assert closed == [44]


def test_generated_query_fd_closes_when_query_validation_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    records = _fake_records()
    class Area:
        def write(self, _name: str, _payload: bytes) -> int:
            return 55
        def create(self, _name: str) -> int:
            raise AssertionError("response create reached")
    config_fd = records["openssl_config"]["fd"]
    monkeypatch.setattr(
        MODULE,
        "_run_pinned",
        lambda *_args, **_kwargs: (
            b"query",
            f"Using configuration from /proc/self/fd/{config_fd}\n".encode(),
        ),
    )
    monkeypatch.setattr(MODULE, "_validate_query", lambda *_args: (_ for _ in ()).throw(MODULE.TimestampError("bad query")))
    closed: list[int] = []
    monkeypatch.setattr(MODULE.os, "close", lambda fd: closed.append(fd))
    with pytest.raises(MODULE.TimestampError, match="bad query"):
        MODULE._request_candidate(records, None, Area())
    assert closed == [55]


def test_post_drain_deadline_is_enforced_without_one_second_extension(monkeypatch: pytest.MonkeyPatch) -> None:
    class Stream:
        def __init__(self, descriptor: int) -> None:
            self.descriptor = descriptor
        def fileno(self) -> int:
            return self.descriptor
        def close(self) -> None:
            pass
    class Process:
        def __init__(self) -> None:
            self.stdout = Stream(81)
            self.stderr = Stream(82)
            self.killed = False
        def wait(self, timeout: float) -> int:
            if self.killed:
                return -9
            raise AssertionError(f"wait reached with {timeout}")
        def kill(self) -> None:
            self.killed = True
        def poll(self) -> int | None:
            return -9 if self.killed else None
    process = Process()
    class Selector:
        def register(self, *_args: object) -> None:
            pass
        def get_map(self) -> dict[object, object]:
            return {}
        def close(self) -> None:
            pass
    monkeypatch.setattr(MODULE.subprocess, "Popen", lambda *_args, **_kwargs: process)
    monkeypatch.setattr(MODULE.selectors, "DefaultSelector", Selector)
    monotonic = iter([0.0, 31.0])
    monkeypatch.setattr(MODULE.time, "monotonic", lambda: next(monotonic))
    with pytest.raises(MODULE.TimestampError, match="exceeded deadline"):
        MODULE._run_pinned(3, ["version"], [], "deadline", timeout=30)
    assert process.killed is True


def test_selector_setup_failure_kills_waits_and_closes_own_child(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[str] = []
    class Stream:
        def __init__(self, descriptor: int, label: str) -> None:
            self.descriptor = descriptor
            self.label = label
        def fileno(self) -> int:
            return self.descriptor
        def close(self) -> None:
            events.append("close-" + self.label)
    class Process:
        def __init__(self) -> None:
            self.stdout = Stream(91, "stdout")
            self.stderr = Stream(92, "stderr")
            self.killed = False
        def poll(self) -> int | None:
            return -9 if self.killed else None
        def kill(self) -> None:
            self.killed = True
            events.append("kill")
        def wait(self, timeout: float) -> int:
            events.append("wait")
            assert timeout == 5
            return -9
    process = Process()
    monkeypatch.setattr(MODULE.subprocess, "Popen", lambda *_args, **_kwargs: process)
    monkeypatch.setattr(MODULE.selectors, "DefaultSelector", lambda: (_ for _ in ()).throw(OSError("selector fail")))
    with pytest.raises(OSError, match="selector fail"):
        MODULE._run_pinned(3, ["version"], [], "selector")
    assert events == ["kill", "wait", "close-stdout", "close-stderr"]


def test_timestamp_state_grammar_rejects_response_only(monkeypatch: pytest.MonkeyPatch) -> None:
    sequence = iter([None, {"fd": 2, "payload": b"r"}])
    monkeypatch.setattr(MODULE, "_target_at", lambda *_args: next(sequence))
    monkeypatch.setattr(MODULE.os, "close", lambda _fd: None)
    with pytest.raises(MODULE.TimestampError, match="response-only"):
        MODULE._timestamp_state({"fd": 1})


def test_final_child_state_revalidation_rejects_removed_or_replaced_target(monkeypatch: pytest.MonkeyPatch) -> None:
    expected = _state("QUERY_COMMITTED", b"query")
    current = _state("QUERY_COMMITTED", b"replaced")
    monkeypatch.setattr(MODULE, "_timestamp_state", lambda _reports: current)
    monkeypatch.setattr(MODULE, "_close_state", lambda _state: None)
    with pytest.raises(MODULE.TimestampError, match="prefix changed"):
        MODULE._revalidate_state({"fd": 1}, expected)


@pytest.mark.parametrize(
    ("query", "response", "expected"),
    [(None, None, "FRESH"), ({"fd": 2}, None, "QUERY_COMMITTED"), ({"fd": 2}, {"fd": 3}, "PAIR_COMMITTED")],
)
def test_timestamp_state_valid_prefixes(monkeypatch: pytest.MonkeyPatch, query: object, response: object, expected: str) -> None:
    sequence = iter([query, response])
    monkeypatch.setattr(MODULE, "_target_at", lambda *_args: next(sequence))
    assert MODULE._timestamp_state({"fd": 1})["state"] == expected


def test_publication_fresh_orders_query_then_response_and_final_readback(monkeypatch: pytest.MonkeyPatch) -> None:
    candidate = {"query": b"q", "response": b"r"}
    published: list[str] = []
    monkeypatch.setattr(MODULE.os, "geteuid", lambda: 0)
    monkeypatch.setattr(MODULE.os, "getegid", lambda: 0)
    monkeypatch.setattr(MODULE, "_publish_one_at", lambda _fd, name, _payload: published.append(name) or {})
    monkeypatch.setattr(MODULE, "_revalidate_state", lambda _reports, _state: None)
    monkeypatch.setattr(MODULE, "_revalidate_reports", lambda _reports: None)
    final_query = _state("PAIR_COMMITTED", b"q", b"r")["query"]
    final_response = _state("PAIR_COMMITTED", b"q", b"r")["response"]
    monkeypatch.setattr(MODULE, "_target_at", lambda *_args: _state("QUERY_COMMITTED", b"q")["query"])
    monkeypatch.setattr(MODULE, "_timestamp_state", lambda _reports: {"state": "PAIR_COMMITTED", "query": final_query, "response": final_response})
    monkeypatch.setattr(MODULE, "_close_state", lambda _state: None)
    monkeypatch.setattr(MODULE.os, "close", lambda _fd: None)
    result = MODULE._publish_candidate({"fd": 1}, _state("FRESH"), candidate)
    assert published == [MODULE.QUERY.name, MODULE.RESPONSE.name]
    assert result["state"] == "PAIR_COMMITTED"


def test_publication_query_resume_publishes_only_response(monkeypatch: pytest.MonkeyPatch) -> None:
    candidate = {"query": b"q", "response": b"r"}
    published: list[str] = []
    monkeypatch.setattr(MODULE.os, "geteuid", lambda: 0)
    monkeypatch.setattr(MODULE.os, "getegid", lambda: 0)
    monkeypatch.setattr(MODULE, "_publish_one_at", lambda _fd, name, _payload: published.append(name) or {})
    monkeypatch.setattr(MODULE, "_revalidate_state", lambda _reports, _state: None)
    monkeypatch.setattr(MODULE, "_revalidate_reports", lambda _reports: None)
    final = _state("PAIR_COMMITTED", b"q", b"r")
    monkeypatch.setattr(MODULE, "_timestamp_state", lambda _reports: final)
    monkeypatch.setattr(MODULE, "_close_state", lambda _state: None)
    result = MODULE._publish_candidate({"fd": 1}, _state("QUERY_COMMITTED", b"q"), candidate)
    assert published == [MODULE.RESPONSE.name]
    assert result["state"] == "PAIR_COMMITTED"


def test_publication_rejects_foreign_committed_query_before_write(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(MODULE.os, "geteuid", lambda: 0)
    monkeypatch.setattr(MODULE.os, "getegid", lambda: 0)
    monkeypatch.setattr(MODULE, "_publish_one_at", lambda *_args: (_ for _ in ()).throw(AssertionError("write reached")))
    monkeypatch.setattr(MODULE, "_revalidate_state", lambda _reports, _state: None)
    with pytest.raises(MODULE.TimestampError, match="committed query differs"):
        MODULE._publish_candidate({"fd": 1}, _state("QUERY_COMMITTED", b"foreign"), {"query": b"q", "response": b"r"})


def test_publication_rolls_back_only_own_response_when_query_changes_after_link(monkeypatch: pytest.MonkeyPatch) -> None:
    candidate = {"query": b"q", "response": b"r"}
    initial = _state("QUERY_COMMITTED", b"q")
    monkeypatch.setattr(MODULE.os, "geteuid", lambda: 0)
    monkeypatch.setattr(MODULE.os, "getegid", lambda: 0)
    monkeypatch.setattr(MODULE, "_revalidate_state", lambda _reports, _state: None)
    monkeypatch.setattr(MODULE, "_publish_one_at", lambda *_args: {"st_dev": 1, "st_ino": 2})
    monkeypatch.setattr(MODULE, "_revalidate_reports", lambda _reports: None)
    monkeypatch.setattr(MODULE, "_timestamp_state", lambda _reports: _state("PAIR_COMMITTED", b"foreign", b"r"))
    monkeypatch.setattr(MODULE, "_close_state", lambda _state: None)
    rolled_back: list[tuple[object, ...]] = []
    monkeypatch.setattr(MODULE, "_rollback_own_response_at", lambda *args: rolled_back.append(args))
    with pytest.raises(MODULE.TimestampError, match="final.*differs"):
        MODULE._publish_candidate({"fd": 1}, initial, candidate)
    assert len(rolled_back) == 1
    assert rolled_back[0][1] == {"st_dev": 1, "st_ino": 2}


def test_publish_one_partial_write_loop_and_collision(monkeypatch: pytest.MonkeyPatch) -> None:
    writes: list[int] = []
    monkeypatch.setattr(MODULE.os, "open", lambda *_args, **_kwargs: 7)
    monkeypatch.setattr(MODULE.os, "write", lambda _fd, view: writes.append(len(view)) or 1)
    monkeypatch.setattr(MODULE.os, "fchown", lambda *_args: None)
    monkeypatch.setattr(MODULE.os, "fchmod", lambda *_args: None)
    monkeypatch.setattr(MODULE.os, "fsync", lambda *_args: None)
    monkeypatch.setattr(MODULE.os, "fstat", lambda _fd: types.SimpleNamespace(st_uid=0, st_gid=0, st_mode=stat.S_IFREG | 0o444, st_nlink=0, st_size=3))
    monkeypatch.setattr(MODULE.os, "close", lambda _fd: None)
    monkeypatch.setattr(MODULE.ctypes, "CDLL", lambda *_args, **_kwargs: types.SimpleNamespace(linkat=_FakeLink(errno.EEXIST)))
    with pytest.raises(MODULE.TimestampError, match="overwrite"):
        MODULE._publish_one_at(8, "x", b"abc")
    assert writes == [3, 2, 1]


class _FakeLink:
    def __init__(self, error: int) -> None:
        self.error = error
        self.argtypes: object = None
        self.restype: object = None

    def __call__(self, *_args: object) -> int:
        MODULE.ctypes.set_errno(self.error)
        return -1


def test_publication_scope_discloses_mutable_namespace() -> None:
    assert MODULE.PUBLICATION_SCOPE == {
        "reports_parent_uid": 1000,
        "reports_parent_gid": 1000,
        "reports_parent_mode": 509,
        "reports_namespace_immutable": False,
        "published_file_inode_metadata_immutable": True,
        "point_in_time_attestation": True,
        "mandatory_downstream_exact_revalidation": True,
        "single_writer_lock": "exclusive_nonblocking_flock_on_exact_retry4_amendment_fd",
        "noncooperative_reports_namespace_writer_excluded": True,
        "post_return_namespace_durability_claimed": False,
    }


def test_live_exact_authorities_are_readable_and_self_consistent() -> None:
    records = MODULE._load_authorities()
    try:
        assert records["amendment"]["identity"]["sha256"] == MODULE.EXPECTED_AMENDMENT_SHA256
        assert records["review"]["identity"]["sha256"] == MODULE.EXPECTED_REVIEW_SHA256
        assert records["openssl"]["identity"]["sha256"] == MODULE.EXPECTED_OPENSSL_SHA256
        assert records["curl"]["identity"]["size_bytes"] == 297288
    finally:
        MODULE._close_records(records)


def test_authority_path_swap_is_rejected_while_original_fd_is_pinned(tmp_path: Path) -> None:
    path = tmp_path / "authority"
    path.write_bytes(b"original")
    os.chmod(path, 0o400)
    record = MODULE._open_exact(
        path,
        hashlib.sha256(b"original").hexdigest(),
        len(b"original"),
        expected_uid=os.geteuid(),
        expected_gid=os.getegid(),
        expected_mode=0o400,
    )
    replacement = tmp_path / "replacement"
    replacement.write_bytes(b"different")
    os.chmod(replacement, 0o400)
    os.replace(replacement, path)
    try:
        with pytest.raises(MODULE.TimestampError, match="authority path"):
            MODULE._revalidate_authorities({"authority": record})
    finally:
        os.close(int(record["fd"]))


def test_live_reports_parent_and_targets_are_fresh_read_only() -> None:
    reports = MODULE._open_reports()
    try:
        state = MODULE._timestamp_state(reports)
        try:
            assert state["state"] == "FRESH"
            assert not MODULE.QUERY.exists()
            assert not MODULE.RESPONSE.exists()
        finally:
            MODULE._close_state(state)
        MODULE._revalidate_reports(reports)
    finally:
        os.close(int(reports["fd"]))


@pytest.mark.skipif(os.geteuid() != 0, reason="root metadata/O_TMPFILE integration")
def test_publish_one_at_root_temp_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    os.chown(tmp_path, 0, 0)
    os.chmod(tmp_path, 0o755)
    directory = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    monkeypatch.setattr(MODULE, "REPORTS", tmp_path)
    try:
        identity = MODULE._publish_one_at(directory, "fragment", b"payload")
        assert identity["sha256"] == hashlib.sha256(b"payload").hexdigest()
        opened = (tmp_path / "fragment").lstat()
        assert (opened.st_uid, opened.st_gid, stat.S_IMODE(opened.st_mode), opened.st_nlink) == (0, 0, 0o444, 1)
        with pytest.raises(MODULE.TimestampError, match="overwrite"):
            MODULE._publish_one_at(directory, "fragment", b"other")
    finally:
        os.close(directory)
