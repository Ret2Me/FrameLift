from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
import sys

import pytest


MODULE_PATH = (
    Path(__file__).parents[1]
    / "work/blind-phase-confirmatory-v2/build_retry3_prestart_failure_evidence_v1.py"
)
SPEC = importlib.util.spec_from_file_location("retry3_prestart_failure_evidence", MODULE_PATH)
assert SPEC and SPEC.loader
M = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = M
SPEC.loader.exec_module(M)


def canonical_sha(document: dict[str, object]) -> str:
    return hashlib.sha256(
        json.dumps(document, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def identity(path: str, sha256: str, size: int) -> dict[str, object]:
    return {"path": path, "size_bytes": size, "sha256": sha256}


def unchecked_main_start_prelude_nodes(main: ast.FunctionDef) -> dict[str, ast.AST]:
    parse_index = next(
        index for index, node in enumerate(main.body)
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "args" for target in node.targets)
    )
    common_index = next(
        index for index, node in enumerate(main.body)
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "common" for target in node.targets)
    )
    command_set = next(
        node for node in main.body
        if isinstance(node, ast.If)
        and any(
            isinstance(child, ast.Constant) and child.value == "start-persistent-namespace"
            for child in ast.walk(node.test)
        )
    )
    start_branch = next(
        node for node in command_set.body
        if isinstance(node, ast.If)
        and ast.unparse(node.test) == "args.command == 'start-persistent-namespace'"
    )
    result = {
        f"main.body[{index}]": node
        for index, node in enumerate(main.body[: parse_index + 4])
    }
    for index, node in enumerate(
        main.body[parse_index + 4 : common_index], start=parse_index + 4,
    ):
        result[f"main.body[{index}].test"] = node.test
    result[f"main.body[{common_index}]"] = main.body[common_index]
    result["command_set_if.test"] = command_set.test
    result["command_set_if.body[0]"] = command_set.body[0]
    result["command_set_if.body[1]"] = command_set.body[1]
    result["start_if.test"] = start_branch.test
    result["start_if.body[0]"] = start_branch.body[0]
    return result


def lift_control_flow_anchors(
    monkeypatch, payload: bytes, *, allow_invalid_start_layout: bool = False,
) -> None:
    tree = ast.parse(payload.decode("utf-8"))
    functions = {
        node.name: node for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    main = functions["main"]
    prelude_nodes = (
        unchecked_main_start_prelude_nodes(main)
        if allow_invalid_start_layout else M._main_start_prelude_nodes(main)
    )
    prelude_ast = json.dumps({
        name: ast.dump(node, annotate_fields=True, include_attributes=False)
        for name, node in sorted(prelude_nodes.items())
    }, sort_keys=True, separators=(",", ":")).encode()
    _, _, prestart_ast = M._reachable_closure(
        functions, M.PRESTART_HELPER_ROOTS, label="test main pre-start",
    )
    _, _, expanded_ast = M._reachable_closure(
        functions, {"_expanded_activation_context"}, label="test expanded-context",
    )
    monkeypatch.setattr(M, "BUILDER_SHA256", hashlib.sha256(payload).hexdigest())
    monkeypatch.setattr(
        M, "BUILDER_MODULE_AST_SHA256",
        hashlib.sha256(
            ast.dump(tree, annotate_fields=True, include_attributes=False).encode()
        ).hexdigest(),
    )
    monkeypatch.setattr(
        M, "BUILDER_MODULE_INITIALIZER_AST_SHA256",
        hashlib.sha256(
            ast.dump(
                M._module_initializer_projection(tree),
                annotate_fields=True, include_attributes=False,
            ).encode()
        ).hexdigest(),
    )
    monkeypatch.setattr(
        M, "BUILDER_MAIN_AST_SHA256",
        hashlib.sha256(
            ast.dump(main, annotate_fields=True, include_attributes=False).encode()
        ).hexdigest(),
    )
    monkeypatch.setattr(
        M, "MAIN_START_PRELUDE_AST_SHA256", hashlib.sha256(prelude_ast).hexdigest(),
    )
    monkeypatch.setattr(
        M, "PRESTART_CLOSURE_AST_SHA256", hashlib.sha256(prestart_ast).hexdigest(),
    )
    monkeypatch.setattr(
        M, "EXPANDED_CLOSURE_AST_SHA256", hashlib.sha256(expanded_ast).hexdigest(),
    )


def mirror_contract() -> dict[str, object]:
    paths = (
        "/home/ubuntu/telemetry-yield/src/telemetry_yield/cli.py",
        "/home/ubuntu/telemetry-yield/src/telemetry_yield/planning/manuscript.py",
    )
    return {
        "entries": [
            {
                "logical_source": identity(path, str(index) * 64, index),
                "required_live_drift": identity(path, str(index + 2) * 64, index + 2),
            }
            for index, path in enumerate(paths, 1)
        ]
    }


def conflict_documents() -> dict[str, object]:
    tests: list[object] = [None] * 13 + [dict(M.CURRENT_TEST_IDENTITY)]
    return {
        "plan": {"one": identity("/absolute/a", "a" * 64, 1)},
        "acquisition": {"two": identity("/absolute/b", "b" * 64, 2)},
        "source": {},
        "amendment": {
            "amended_implementation": {
                "runtime_guard_builder_test": dict(M.CURRENT_TEST_IDENTITY),
                "runtime_guard_builder_v3_tests": dict(M.STALE_TEST_IDENTITY),
                "tests": tests,
            },
            "planned_artifact_mirror_contract": mirror_contract(),
        },
    }


def test_exact_conflict_projection_and_only_authorized_mirror_exception(monkeypatch):
    monkeypatch.setattr(M, "EXPECTED_UNIQUE_PATH_COUNT", 3)
    monkeypatch.setattr(M, "_expected_mirror_contract", mirror_contract)
    result = M._validate_conflict(conflict_documents())
    assert result["collector_projection"] == {
        "documents_in_order": ["plan", "acquisition", "source", "amendment"],
        "amendment_excluded_subtree": "planned_artifact_mirror_contract",
        "unique_path_count": 3,
        "unauthorized_conflict_path_count": 1,
        "authorized_mirror_dual_identity_path_count": 2,
    }
    assert result["current_selectors"] == list(M.CURRENT_SELECTORS)
    assert result["stale_selectors"] == list(M.STALE_SELECTORS)


@pytest.mark.parametrize("mutation", ["remove", "second_conflict", "move_alias", "third_test_state"])
def test_conflict_projection_is_fail_closed(monkeypatch, mutation):
    monkeypatch.setattr(M, "EXPECTED_UNIQUE_PATH_COUNT", 3)
    monkeypatch.setattr(M, "_expected_mirror_contract", mirror_contract)
    documents = conflict_documents()
    implementation = documents["amendment"]["amended_implementation"]
    if mutation == "remove":
        implementation["runtime_guard_builder_v3_tests"] = dict(M.CURRENT_TEST_IDENTITY)
    elif mutation == "second_conflict":
        documents["acquisition"]["other"] = identity("/absolute/a", "f" * 64, 3)
    elif mutation == "move_alias":
        implementation["renamed"] = implementation.pop("runtime_guard_builder_v3_tests")
    else:
        implementation["tests"][13] = identity(M.CONFLICT_PATH, "f" * 64, 99)
    with pytest.raises(M.EvidenceError):
        M._validate_conflict(documents)


def test_exact_mirror_contract_rejects_extra_or_irrelevant_key(monkeypatch):
    monkeypatch.setattr(M, "EXPECTED_UNIQUE_PATH_COUNT", 3)
    expected = mirror_contract()
    monkeypatch.setattr(M, "_expected_mirror_contract", lambda: expected)
    documents = conflict_documents()
    documents["amendment"]["planned_artifact_mirror_contract"]["entries"][0]["logical_source"]["irrelevant"] = True
    with pytest.raises(M.EvidenceError, match="exact contract"):
        M._validate_conflict(documents)


def test_exact_builder_control_flow_proves_pre_mutation_boundary():
    payload = M.BUILDER_SOURCE.read_bytes()
    assert hashlib.sha256(payload).hexdigest() == M.BUILDER_SHA256
    result = M._validate_control_flow(payload)
    assert result["status"] == "PASS"
    assert result["failure_precedes_activation_intent"] is True
    assert result["failure_precedes_unshare"] is True
    assert result["failure_precedes_keeper"] is True
    assert result["failure_precedes_mount_mutation"] is True
    assert result["frozen_iq_collector_is_metadata_only"] is True


@pytest.mark.parametrize("mutation", ["exception", "collector", "order"])
def test_builder_control_flow_mutations_reject(mutation):
    source = M.BUILDER_SOURCE.read_text()
    if mutation == "exception":
        source = source.replace("embedded artifact identity conflicts", "different conflict", 1)
    elif mutation == "collector":
        source = source.replace("def _collect_regular_identities", "def _disabled_regular_identities", 1)
    else:
        old = "_expanded_activation_context(_contract_metadata(context), context[\"roots\"])\n            document = start_persistent_namespace("
        new = "document = start_persistent_namespace(\n                control_parent, _contract_metadata(context)\n            )\n            _expanded_activation_context(_contract_metadata(context), context[\"roots\"])\n            document = start_persistent_namespace("
        source = source.replace(old, new, 1)
    with pytest.raises((M.EvidenceError, SyntaxError)):
        M._validate_control_flow(source.encode())


@pytest.mark.parametrize("mutation", ["pre_unshare", "indirect_iq_reader", "prelude_helper_write"])
def test_hash_lifted_control_flow_adversaries_still_reject(monkeypatch, mutation):
    source = M.BUILDER_SOURCE.read_text()
    if mutation == "pre_unshare":
        marker = "            _expanded_activation_context(_contract_metadata(context), context[\"roots\"])"
        source = source.replace(marker, "            os.unshare(0)\n" + marker, 1)
    elif mutation == "indirect_iq_reader":
        marker = ") -> list[dict[str, object]]:\n    acquisition = context.get(\"acquisition\")"
        source = source.replace(marker, ") -> list[dict[str, object]]:\n    hidden_iq_reader()\n    acquisition = context.get(\"acquisition\")", 1)
        source += "\n\ndef hidden_iq_reader():\n    Path('/forbidden-iq').read_bytes()\n"
    else:
        marker = "def _static_metadata_context(**kwargs: object) -> dict[str, object]:\n"
        source = source.replace(marker, marker + "    Path('/forbidden').write_bytes(b'x')\n", 1)
    payload = source.encode()
    if mutation != "pre_unshare":
        lift_control_flow_anchors(monkeypatch, payload)
    else:
        monkeypatch.setattr(M, "BUILDER_SHA256", hashlib.sha256(payload).hexdigest())
    with pytest.raises(M.EvidenceError):
        M._validate_control_flow(payload)


def test_coherently_lifted_static_metadata_unshare_is_semantically_rejected(monkeypatch):
    source = M.BUILDER_SOURCE.read_text()
    marker = "def _static_metadata_context(**kwargs: object) -> dict[str, object]:\n"
    assert source.count(marker) == 1
    source = source.replace(marker, marker + "    os.unshare(0)\n", 1)
    payload = source.encode()
    lift_control_flow_anchors(monkeypatch, payload)
    with pytest.raises(M.EvidenceError, match="pre-start dotted call set differs"):
        M._validate_control_flow(payload)


@pytest.mark.parametrize("location", ["before_start_if", "between_expanded_and_start"])
def test_coherently_lifted_start_branch_gap_unshare_is_rejected(monkeypatch, location):
    source = M.BUILDER_SOURCE.read_text()
    if location == "before_start_if":
        helper = "def hidden_prelude_mutation():\n    os.unshare(0)\n\n\n"
        source = source.replace("def main(argv: Sequence[str] | None = None) -> int:\n", helper + "def main(argv: Sequence[str] | None = None) -> int:\n", 1)
        marker = "        if args.command == \"start-persistent-namespace\":\n"
        source = source.replace(marker, "        hidden_prelude_mutation()\n" + marker, 1)
    else:
        marker = "            _expanded_activation_context(_contract_metadata(context), context[\"roots\"])\n"
        source = source.replace(marker, marker + "            os.unshare(0)\n", 1)
    payload = source.encode()
    lift_control_flow_anchors(monkeypatch, payload, allow_invalid_start_layout=True)
    with pytest.raises(M.EvidenceError, match="intervenes|body length"):
        M._validate_control_flow(payload)


@pytest.mark.parametrize("operation", ["os.unshare(0)", "Path('/forbidden-iq').read_bytes()"])
def test_coherently_lifted_top_level_effect_is_rejected(monkeypatch, operation):
    source = M.BUILDER_SOURCE.read_text()
    marker = "PROJECT_ROOT = EXPECTED_PROJECT_ROOT\n"
    assert source.count(marker) == 1
    source = source.replace(marker, operation + "\n" + marker, 1)
    payload = source.encode()
    lift_control_flow_anchors(monkeypatch, payload)
    with pytest.raises(M.EvidenceError, match="module initializer call set differs"):
        M._validate_control_flow(payload)


@pytest.mark.parametrize(
    "rebinding",
    ["assign", "destructure", "annassign", "namedexpr", "attribute", "subscript"],
)
def test_coherently_lifted_initializer_callable_rebinding_is_rejected(
    monkeypatch, rebinding,
):
    source = M.BUILDER_SOURCE.read_text()
    marker = "PROJECT_ROOT = EXPECTED_PROJECT_ROOT\n"
    proxy = (
        "_AuditOriginalPath = Path\n"
        "class _AuditPathProxy:\n"
        "    def __new__(cls, *args, **kwargs):\n"
        "        os.unshare(0)\n"
        "        return _AuditOriginalPath(*args, **kwargs)\n"
    )
    bindings = {
        "assign": "Path = _AuditPathProxy\n",
        "destructure": "Path, _audit_unused = (_AuditPathProxy, None)\n",
        "annassign": "Path: object = _AuditPathProxy\n",
        "namedexpr": "(Path := _AuditPathProxy)\n",
        "attribute": "Path.__new__ = _AuditPathProxy.__new__\n",
        "subscript": "globals()['Path'] = _AuditPathProxy\n",
    }
    assert source.count(marker) == 1
    source = source.replace(marker, proxy + bindings[rebinding] + marker, 1)
    payload = source.encode()
    lift_control_flow_anchors(monkeypatch, payload)
    with pytest.raises(M.EvidenceError, match="initializer|shadows"):
        M._validate_control_flow(payload)


def test_exact_transcript_receipt_is_bounded_and_semantically_exact():
    result = M._validate_transcript()
    assert result["physical_line_number"] == 11060
    assert result["ordinal"] == 11059
    assert result["raw_record"] == {
        "sha256": M.TRANSCRIPT_RAW_SHA256,
        "size_bytes": M.TRANSCRIPT_RAW_SIZE,
    }
    assert result["aggregated_output"] == {
        "sha256": M.OUTPUT_SHA256,
        "size_bytes": M.OUTPUT_SIZE,
    }
    assert result["duration"] == {"secs": 0, "nanos": 743766585}


def test_transcript_semantic_mutation_rejects(monkeypatch):
    raw = M._read_bounded_transcript_line()
    document = json.loads(raw)
    document["payload"]["item"]["duration"]["nanos"] += 1
    changed = (json.dumps(document, separators=(",", ":")) + "\n").encode()
    monkeypatch.setattr(M, "_read_bounded_transcript_line", lambda: changed)
    monkeypatch.setattr(M, "TRANSCRIPT_RAW_SHA256", hashlib.sha256(changed).hexdigest())
    monkeypatch.setattr(M, "TRANSCRIPT_RAW_SIZE", len(changed))
    with pytest.raises(M.EvidenceError, match="execution semantics"):
        M._validate_transcript()


def test_transcript_prefix_has_global_byte_bound(tmp_path, monkeypatch):
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_bytes(b"x" * (M.MAX_TRANSCRIPT_LINE_BYTES + 1) + b"\n{}\n")
    monkeypatch.setattr(M, "TRANSCRIPT_LINE", 2)
    with pytest.raises(M.EvidenceError, match="global"):
        M._read_bounded_transcript_line(
            transcript, maximum_scan_bytes=M.MAX_TRANSCRIPT_LINE_BYTES,
            timeout_seconds=1.0,
        )


def test_transcript_scan_has_global_time_bound(tmp_path, monkeypatch):
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_bytes(b"{}\n")
    monkeypatch.setattr(M, "TRANSCRIPT_LINE", 2)
    moments = iter((0.0, 2.0))
    monkeypatch.setattr(M.time, "monotonic", lambda: next(moments))
    with pytest.raises(M.EvidenceError, match="timed out"):
        M._read_bounded_transcript_line(
            transcript, maximum_scan_bytes=M.MAX_TRANSCRIPT_LINE_BYTES,
            timeout_seconds=1.0,
        )


def test_transcript_deadline_is_checked_after_successful_target_read(tmp_path, monkeypatch):
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_bytes(b"{}\n")
    monkeypatch.setattr(M, "TRANSCRIPT_LINE", 1)
    moments = iter((0.0, 0.0, 2.0))
    monkeypatch.setattr(M.time, "monotonic", lambda: next(moments))
    with pytest.raises(M.EvidenceError, match="global byte bound|timed out"):
        M._read_bounded_transcript_line(
            transcript, maximum_scan_bytes=M.MAX_TRANSCRIPT_LINE_BYTES,
            timeout_seconds=1.0,
        )


def test_pinned_exact_four_seed_and_foreign_entry_rejection(tmp_path, monkeypatch):
    control = tmp_path / "control"
    control.mkdir(mode=0o755)
    specs: dict[str, tuple[str, int, int, int]] = {}
    for index, name in enumerate(("a", "b", "c", "d"), 1):
        path = control / name
        path.write_bytes(bytes([index]) * index)
        path.chmod(0o444)
        status = path.stat()
        specs[name] = (hashlib.sha256(path.read_bytes()).hexdigest(), index, 0o444, status.st_ino)
    root_status = control.stat()
    monkeypatch.setattr(M, "CONTROL", control)
    monkeypatch.setattr(M, "CONTROL_ROOT_IDENTITY", {
        "path": str(control), "st_dev": root_status.st_dev, "st_ino": root_status.st_ino,
        "uid": root_status.st_uid, "gid": root_status.st_gid,
        "mode": stat.S_IMODE(root_status.st_mode), "nlink": root_status.st_nlink,
    })
    monkeypatch.setattr(M, "SEED_SPECS", specs)
    result = M._pinned_seed_inventory()
    assert result["entry_count"] == 4
    assert result["lifecycle_artifact_count"] == 0
    (control / "foreign").write_bytes(b"x")
    with pytest.raises(M.EvidenceError, match="not exact4"):
        M._pinned_seed_inventory()


def test_pinned_regular_revalidation_rejects_restored_path_swap(tmp_path):
    path = tmp_path / "input"
    path.write_bytes(b"exact")
    path.chmod(0o444)
    descriptor, _identity, opened = M._open_pinned_expected(
        path, hashlib.sha256(b"exact").hexdigest(), 5,
        expected_mode=0o444, expected_uid=os.geteuid(),
    )
    try:
        old = tmp_path / "old"
        path.rename(old)
        path.write_bytes(b"exact")
        path.chmod(0o444)
        with pytest.raises(M.EvidenceError, match="changed"):
            M._revalidate_pinned_expected(path, descriptor, opened)
    finally:
        os.close(descriptor)


def test_seed_full_scan_revalidates_every_earlier_dirent(tmp_path, monkeypatch):
    control = tmp_path / "control"
    control.mkdir(mode=0o755)
    specs = {}
    for index, name in enumerate(("a", "b", "c", "d"), 1):
        path = control / name
        path.write_bytes(bytes([index]) * index)
        path.chmod(0o444)
        status = path.stat()
        specs[name] = (hashlib.sha256(path.read_bytes()).hexdigest(), index, 0o444, status.st_ino)
    root = control.stat()
    monkeypatch.setattr(M, "CONTROL", control)
    monkeypatch.setattr(M, "CONTROL_ROOT_IDENTITY", {
        "path": str(control), "st_dev": root.st_dev, "st_ino": root.st_ino,
        "uid": root.st_uid, "gid": root.st_gid,
        "mode": stat.S_IMODE(root.st_mode), "nlink": root.st_nlink,
    })
    monkeypatch.setattr(M, "SEED_SPECS", specs)
    original = M._read_fd
    calls = 0

    def swapping_read(descriptor, maximum):
        nonlocal calls
        result = original(descriptor, maximum)
        calls += 1
        if calls == 4:
            first = control / "a"
            first.rename(control / "old-a")
            first.write_bytes(b"\x01")
            first.chmod(0o444)
        return result

    monkeypatch.setattr(M, "_read_fd", swapping_read)
    with pytest.raises(M.EvidenceError, match="changed after full scan"):
        M._pinned_seed_inventory()


def test_retry3_protocol_selfhash_sidecars_and_live_conflict_are_exact():
    protocol, documents = M._validate_protocol()
    assert protocol["retry3_amendment"]["sha256"] == M.AMENDMENT_SHA256
    assert protocol["retry3_independent_review"]["sha256"] == M.REVIEW_SHA256
    assert protocol["review_is_self_hashed_exact_repo_artifact_but_not_root_immutable"] is True
    result = M._validate_conflict(documents)
    assert result["collector_projection"]["unique_path_count"] == 524
    assert result["collector_projection"]["unauthorized_conflict_path_count"] == 1


def test_rfc3161_verifies_exact_retry3_amendment_by_query_and_data():
    seed = M._pinned_seed_inventory()
    result = M._verify_rfc3161(seed)
    assert result["query_matches_exact_retry3_amendment"] is True
    assert result["response_verifies_by_query_and_data"] is True
    assert [item["mode"] for item in result["checks"]] == ["queryfile", "data"]
    assert result["pinned_inputs"]["openssl_config"] == {
        "path": str(M.OPENSSL_CONFIG),
        "sha256": M.OPENSSL_CONFIG_SHA256,
        "size_bytes": 12324,
        "st_dev": 64512,
        "st_ino": 787930,
        "uid": 0,
        "gid": 0,
        "mode": 0o644,
        "nlink": 1,
    }
    assert all(
        item["logical_argv"][3:5] == ["-config", str(M.OPENSSL_CONFIG)]
        and item["all_inputs_and_executable_opened_by_pinned_fd"] is True
        for item in result["checks"]
    )


def test_rfc3161_explicit_config_fd_is_passed_and_revalidated(monkeypatch):
    observed_runs = []
    observed_revalidations = []
    original_revalidate = M._revalidate_pinned_expected

    def fake_run(argv, maximum, *, timeout_seconds=10.0, pass_fds=(), executable=None):
        config_path = argv[argv.index("-config") + 1]
        assert config_path.startswith("/proc/self/fd/")
        config_fd = int(config_path.rsplit("/", 1)[1])
        assert config_fd in pass_fds
        observed_runs.append((config_fd, tuple(pass_fds), executable))
        return subprocess.CompletedProcess(
            argv, 0, b"Verification: OK\n",
            f"Using configuration from {config_path}\n".encode(),
        )

    def recording_revalidate(path, descriptor, opened):
        observed_revalidations.append((Path(path), descriptor))
        return original_revalidate(path, descriptor, opened)

    monkeypatch.setattr(M, "_run_bounded", fake_run)
    monkeypatch.setattr(M, "_revalidate_pinned_expected", recording_revalidate)
    result = M._verify_rfc3161(M._pinned_seed_inventory())
    assert len(observed_runs) == 2
    assert len({item[0] for item in observed_runs}) == 1
    assert any(path == M.OPENSSL_CONFIG for path, _descriptor in observed_revalidations)
    assert result["pinned_inputs"]["openssl_config"]["sha256"] == M.OPENSSL_CONFIG_SHA256


def test_bounded_verifier_output_rejects_overflow():
    with pytest.raises(M.EvidenceError, match="exceeded bound"):
        M._run_bounded(
            ["/usr/bin/python3.12", "-I", "-S", "-c", "import os;os.write(1,b'x'*65536)"],
            1024,
        )


def test_bounded_verifier_timeout_reaps_only_its_own_child(monkeypatch):
    original = subprocess.Popen
    captured = []

    def recording_popen(*args, **kwargs):
        process = original(*args, **kwargs)
        captured.append(process)
        return process

    monkeypatch.setattr(M.subprocess, "Popen", recording_popen)
    with pytest.raises(M.EvidenceError, match="timed out"):
        M._run_bounded(
            ["/usr/bin/python3.12", "-I", "-S", "-c", "import time;time.sleep(30)"],
            1024, timeout_seconds=0.05,
        )
    assert len(captured) == 1
    assert captured[0].poll() is not None


def test_selector_setup_failure_occurs_before_child_creation(monkeypatch):
    created = []

    def broken_selector():
        raise RuntimeError("selector construction failed")

    def forbidden_popen(*args, **kwargs):
        created.append((args, kwargs))
        raise AssertionError("child must not be created")

    monkeypatch.setattr(M.selectors, "DefaultSelector", broken_selector)
    monkeypatch.setattr(M.subprocess, "Popen", forbidden_popen)
    with pytest.raises(RuntimeError, match="selector construction"):
        M._run_bounded(["/usr/bin/true"], 1024)
    assert created == []


def test_process_lookup_during_terminate_still_waits_to_reap():
    class Process:
        waits = 0

        def poll(self):
            return None

        def terminate(self):
            raise ProcessLookupError

        def wait(self, timeout):
            self.waits += 1
            return 0

    process = Process()
    M._stop_owned_child(process)
    assert process.waits == 1


def test_build_document_shape_selfhash_and_zero_state(monkeypatch):
    generator_id = identity(str(MODULE_PATH), "a" * 64, 1)
    test_id = identity(str(Path(__file__)), "b" * 64, 1)

    def fake_read(path, maximum=M.MAX_METADATA_BYTES):
        return b"", generator_id if Path(path) == MODULE_PATH else test_id

    live = {"inventory_sha256": M.EXTERNAL_NAMESPACE_BASELINE_SHA256, "relevant_mount_count": 0}
    seed = {
        "directory_identity": dict(M.CONTROL_ROOT_IDENTITY), "entry_count": 4,
        "entries": [identity(str(M.CONTROL_TSQ), M.TSQ_SHA256, 70), identity(str(M.CONTROL_TSR), M.TSR_SHA256, 6008)],
        "lifecycle_artifact_count": 0, "results_directory_present": False,
    }
    documents = {"tsq": b"q", "tsr": b"r", "builder_source": b""}
    seed["entries"][0]["sha256"] = hashlib.sha256(b"q").hexdigest()
    seed["entries"][1]["sha256"] = hashlib.sha256(b"r").hexdigest()
    monkeypatch.setattr(M, "_read_regular", fake_read)
    monkeypatch.setattr(M, "_capture_live_state", lambda: dict(live))
    monkeypatch.setattr(M, "_pinned_seed_inventory", lambda: json.loads(json.dumps(seed)))
    monkeypatch.setattr(M, "_validate_protocol", lambda: ({"retry3_amendment": {}}, documents))
    monkeypatch.setattr(M, "_validate_transcript", lambda: {"exit_code": 1})
    monkeypatch.setattr(M, "_validate_conflict", lambda value: {"collector_projection": {"unique_path_count": 524}})
    monkeypatch.setattr(M, "_validate_control_flow", lambda value: {"status": "PASS"})
    monkeypatch.setattr(M, "_verify_rfc3161", lambda value: {"response_verifies_by_query_and_data": True})
    document = M.build_evidence("a" * 64, "b" * 64, "2026-09-06T14:20:00Z")
    projected = dict(document)
    assert projected.pop(M.HASH_FIELD) == canonical_sha(projected)
    assert document["status"] == M.STATUS
    assert all(value is False for value in document["negative_state"].values())
    assert document["authority_limits"]["evidence_authorizes_lifecycle_or_quarantine"] is False


def test_build_rechecks_mutable_protocol_transcript_and_own_sources(monkeypatch):
    generator_id = identity(str(MODULE_PATH), "a" * 64, 1)
    test_id = identity(str(Path(__file__)), "b" * 64, 1)
    reads = {"protocol": 0, "transcript": 0, "self": 0}

    def fake_read(path, maximum=M.MAX_METADATA_BYTES):
        reads["self"] += 1
        return b"", generator_id if Path(path) == MODULE_PATH else test_id

    live = {"inventory_sha256": M.EXTERNAL_NAMESPACE_BASELINE_SHA256}
    seed = {
        "directory_identity": {}, "entry_count": 4,
        "entries": [identity(str(M.CONTROL_TSQ), hashlib.sha256(b"q").hexdigest(), 1), identity(str(M.CONTROL_TSR), hashlib.sha256(b"r").hexdigest(), 1)],
        "lifecycle_artifact_count": 0, "results_directory_present": False,
    }
    documents = {"tsq": b"q", "tsr": b"r", "builder_source": b"source"}

    def protocol():
        reads["protocol"] += 1
        return {"pass": reads["protocol"]}, dict(documents)

    def transcript():
        reads["transcript"] += 1
        return {"pass": reads["transcript"]}

    monkeypatch.setattr(M, "_read_regular", fake_read)
    monkeypatch.setattr(M, "_capture_live_state", lambda: dict(live))
    monkeypatch.setattr(M, "_pinned_seed_inventory", lambda: json.loads(json.dumps(seed)))
    monkeypatch.setattr(M, "_validate_protocol", protocol)
    monkeypatch.setattr(M, "_validate_transcript", transcript)
    monkeypatch.setattr(M, "_validate_conflict", lambda value: {})
    monkeypatch.setattr(M, "_validate_control_flow", lambda value: {})
    monkeypatch.setattr(M, "_verify_rfc3161", lambda value: {})
    with pytest.raises(M.EvidenceError, match="two-pass"):
        M.build_evidence("a" * 64, "b" * 64, "2026-09-06T14:20:00Z")
    assert reads["protocol"] == 2
    assert reads["transcript"] == 2
    assert reads["self"] == 4


def test_cli_requires_explicit_read_only_mode_and_never_has_publish_arguments(monkeypatch, capsys):
    parser = M._parser()
    destinations = {action.dest for action in parser._actions}
    assert destinations == {
        "help", "expected_generator_sha256", "expected_generator_tests_sha256",
        "created_at_utc", "preflight_only",
    }
    assert not any(token in name.lower() for name in destinations for token in ("publish", "output", "iq", "outcome"))
    argv = [
        "--expected-generator-sha256", "a" * 64,
        "--expected-generator-tests-sha256", "b" * 64,
        "--created-at-utc", "2026-09-06T14:20:00Z",
    ]
    with pytest.raises(M.EvidenceError, match="read-only"):
        M.main(argv)
    monkeypatch.setattr(M, "build_evidence", lambda *args: {M.HASH_FIELD: "c" * 64})
    assert M.main([*argv, "--preflight-only"]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["status"] == M.DRY_STATUS
    assert output["candidate"]["basename"] == M.OUTPUT_BASENAME


def test_generator_source_contains_no_filesystem_or_lifecycle_mutation_primitives():
    source = MODULE_PATH.read_text()
    tree = ast.parse(source)
    calls: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name):
            calls.add(node.func.id)
        elif isinstance(node.func, ast.Attribute):
            prefix = node.func.value.id if isinstance(node.func.value, ast.Name) else ""
            calls.add(f"{prefix}.{node.func.attr}" if prefix else node.func.attr)
    forbidden = {
        "open", "os.write", "os.mkdir", "os.makedirs", "os.rename", "os.replace",
        "os.unlink", "os.remove", "os.rmdir", "Path.write_bytes", "Path.write_text",
        "Path.mkdir", "Path.rename", "Path.replace", "Path.unlink", "shutil.rmtree",
        "os.unshare", "os.kill", "subprocess.run",
    }
    assert calls.isdisjoint(forbidden)
    assert "def publish" not in source


@pytest.mark.skipif(os.geteuid() != 0, reason="complete procfs namespace audit requires root")
def test_root_live_full_build_is_read_only_and_deterministic():
    source_before = M.CONTROL.lstat()
    document1 = M.build_evidence(
        hashlib.sha256(MODULE_PATH.read_bytes()).hexdigest(),
        hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "2026-09-06T14:20:00Z",
    )
    document2 = M.build_evidence(
        hashlib.sha256(MODULE_PATH.read_bytes()).hexdigest(),
        hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "2026-09-06T14:20:00Z",
    )
    source_after = M.CONTROL.lstat()
    assert document1 == document2
    assert (source_before.st_dev, source_before.st_ino) == (source_after.st_dev, source_after.st_ino)
