from __future__ import annotations

import ast
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import types

import pytest


SOURCE = (
    Path(__file__).parents[1]
    / "work/blind-phase-confirmatory-v2/quarantine_fourth_control_root_v1.py"
)
SPEC = importlib.util.spec_from_file_location(
    "quarantine_fourth_control_root_v1", SOURCE
)
assert SPEC is not None and SPEC.loader is not None
Q = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(Q)


def identity(path: str, digest: str = "a" * 64, size: int = 1) -> dict[str, object]:
    return {"path": path, "size_bytes": size, "sha256": digest}


def stage2() -> dict[str, object]:
    return {
        "parent": {"path": str(Q.STAGE2_PARENT)},
        "durable_prefix_length": 9,
        "ordered_receipts": [identity(str(Q.STAGE2_PARENT / f"r{index}.json")) for index in range(9)],
        "result": {
            "path": str(Q.STAGE2_RESULT), "size_bytes": 90_532,
            "sha256": Q.STAGE2_RESULT_SHA256,
        },
        "result_payload_sha256": Q.STAGE2_RESULT_PAYLOAD_SHA256,
        "post_recovery_evidence_sha256": "b" * 64,
        "external_inventory_sha256": "c" * 64,
        "transaction_directory_disposition": {},
    }


def snapshot(root: Path) -> dict[str, object]:
    return {
        "root": {
            "path": str(root), "st_dev": 1, "st_ino": 2, "uid": 0,
            "gid": 0, "mode": 0o755, "nlink": 6, "entry_count": 34,
        },
        "direct_children": ["x"] * 34,
        "manifest": {"entry_count_including_root": 37, "manifest_sha256": "d" * 64, "entries": []},
        "results": {
            "path": str(root / "results-v3"), "st_dev": 1, "st_ino": 3,
            "uid": 0, "gid": 0, "mode": 0o755, "nlink": 2,
            "entry_count": 0,
        },
        "iq_sources": {
            "source_count": 30, "content_opened_by_quarantine": False,
            "metadata_manifest_sha256": "e" * 64, "sources": [],
        },
        "corrected_source": identity(str(Q.CORRECTED_BUILDER), "f" * 64),
    }


def tools() -> dict[str, object]:
    return {
        "runner": identity(str(Q.CONTROL_COPY)),
        "runner_test": identity(str(Q.CONTROL_TEST)),
        "independent_review": identity(str(Q.INDEPENDENT_REVIEW)),
    }


def expected() -> dict[str, object]:
    return {
        "script_sha256": "1" * 64, "test_sha256": "2" * 64,
        "review_sha256": "3" * 64,
        "controller_st_dev": 1, "controller_st_ino": 2,
        "var_lib_st_dev": 1, "var_lib_st_ino": 2,
    }


def patch_paths(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    source = tmp_path / "canonical-control"
    source.mkdir()
    monkeypatch.setattr(Q, "VAR_LIB", tmp_path)
    monkeypatch.setattr(Q, "SOURCE_CONTROL_ROOT", source)
    monkeypatch.setattr(Q, "QUARANTINE_ROOT", tmp_path / "quarantined-control")
    controller = tmp_path / "controller"
    controller.mkdir()
    monkeypatch.setattr(Q, "QUARANTINE_CONTROL", controller)
    monkeypatch.setattr(Q, "CONTROL_COPY", controller / "runner.py")
    monkeypatch.setattr(Q, "CONTROL_TEST", controller / "test.py")
    monkeypatch.setattr(Q, "INDEPENDENT_REVIEW", controller / "review.json")
    monkeypatch.setattr(Q, "LOCK_PATH", controller / "lock")
    monkeypatch.setattr(Q, "INTENT_PATH", controller / "intent.json")
    monkeypatch.setattr(Q, "PROVENANCE_PATH", controller / "provenance.json")
    monkeypatch.setattr(
        Q,
        "EXPECTED_CONTROLLER_BASE_NAMES",
        {"runner.py", "test.py", "review.json", "review.json.sha256", "lock"},
    )


def intent_document(before: dict[str, object] | None = None) -> dict[str, object]:
    document = {
        "schema_version": "blind-phase-confirmatory-fourth-control-root-quarantine-intent-v1",
        "status": "COMMITTED_BEFORE_ATOMIC_NO_CLOBBER_CONTROL_ROOT_RENAME",
        "attempt_id": Q.ATTEMPT_ID,
        "created_at_utc": "2026-09-06T11:00:00Z",
        "source_control_root": str(Q.SOURCE_CONTROL_ROOT),
        "quarantine_root": str(Q.QUARANTINE_ROOT),
        "controller_parent": {
            "path": str(Q.QUARANTINE_CONTROL), "st_dev": 1, "st_ino": 2,
            "uid": 0, "gid": 0, "mode": 0o755,
        },
        "rename_parent": {
            "path": str(Q.VAR_LIB), "st_dev": 1, "st_ino": 2,
            "uid": 0, "gid": 0, "mode": 0o755,
        },
        "tools": tools(), "stage2": stage2(),
        "pre_rename_snapshot": before or snapshot(Q.SOURCE_CONTROL_ROOT),
        "zero_runtime_state": {
            "keeper_absent": True, "relevant_mount_count": 0,
            "role_decoder_evaluator_processes": {
                "namespace_member_pids": [], "namespace_fd_handles": [],
                "suspicious_processes": [],
            },
            "external_inventory_sha256": "c" * 64,
        },
        "authorized_operation": {
            "operation": "renameat2(RENAME_NOREPLACE)",
            "source_parent": str(Q.VAR_LIB),
            "destination_parent": str(Q.VAR_LIB),
            "recursive_delete": False, "content_copy": False,
            "iq_content_open": False,
        },
    }
    document["quarantine_intent_payload_sha256"] = Q._sha256_document(document)
    return document


def patch_common(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> tuple[dict[str, object], dict[str, object], dict[str, object]]:
    patch_paths(monkeypatch, tmp_path)
    expected_values = expected()
    tool_values = tools()
    stage2_values = stage2()
    zero = {
        "keeper_absent": True, "relevant_mount_count": 0,
        "role_decoder_evaluator_processes": {
            "namespace_member_pids": [], "namespace_fd_handles": [],
            "suspicious_processes": [],
        },
        "external_inventory_sha256": "c" * 64,
    }
    monkeypatch.setattr(Q, "_validate_toolchain", lambda *_args: tool_values)
    monkeypatch.setattr(Q, "_validate_controller_inventory", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(Q, "_load_stage2_module", lambda: types.SimpleNamespace())
    monkeypatch.setattr(Q, "_stage2_prefix_snapshot", lambda: copy.deepcopy(stage2_values))
    monkeypatch.setattr(Q, "_zero_runtime_state", lambda *_args: copy.deepcopy(zero))
    monkeypatch.setattr(Q, "_validate_stage2_snapshot", lambda _value: None)
    return expected_values, tool_values, zero


def test_repository_candidate_is_executable_and_has_no_delete_primitive() -> None:
    assert Q.QUARANTINE_TEMPLATE_BLOCKED_PENDING_REVIEW is False
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    attributes = {
        node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
    }
    assert not {"unlink", "rmtree", "remove", "removedirs"} & attributes


def test_preflight_fresh_state_never_publishes_or_renames(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    expected_values, _tool_values, _zero = patch_common(monkeypatch, tmp_path)
    before = snapshot(Q.SOURCE_CONTROL_ROOT)
    monkeypatch.setattr(Q, "_root_state", lambda: "SOURCE_CANONICAL")
    monkeypatch.setattr(Q, "_snapshot_root", lambda _root: copy.deepcopy(before))
    monkeypatch.setattr(
        Q, "_publish_pair", lambda *_args: pytest.fail("preflight published")
    )
    monkeypatch.setattr(
        Q, "_rename_noreplace", lambda **_kwargs: pytest.fail("preflight renamed")
    )
    result = Q.quarantine(
        expected=expected_values, created_at_utc="2026-09-06T11:00:00Z",
        completed_at_utc="2026-09-06T11:01:00Z", preflight_only=True,
    )
    assert result["status"] == "PASS_READ_ONLY_NO_PUBLICATION_NO_RENAME"
    assert result["durable_prefix_length"] == 0


@pytest.mark.parametrize("fragment", ["main", "sidecar"])
def test_preflight_validates_partial_intent_without_repair_or_rename(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fragment: str,
) -> None:
    expected_values, _tool_values, _zero = patch_common(monkeypatch, tmp_path)
    path = Q.INTENT_PATH if fragment == "main" else Q.INTENT_PATH.with_name(
        Q.INTENT_PATH.name + ".sha256"
    )
    path.write_text("partial", encoding="utf-8")
    before = snapshot(Q.SOURCE_CONTROL_ROOT)
    monkeypatch.setattr(Q, "_root_state", lambda: "SOURCE_CANONICAL")
    monkeypatch.setattr(Q, "_snapshot_root", lambda _root: copy.deepcopy(before))
    validated: list[tuple[bool, bool]] = []
    monkeypatch.setattr(
        Q, "_validate_pair_fragments",
        lambda _path, _doc, _field, presence: validated.append(presence),
    )
    monkeypatch.setattr(
        Q, "_publish_pair", lambda *_args: pytest.fail("preflight repaired partial")
    )
    monkeypatch.setattr(
        Q, "_rename_noreplace", lambda **_kwargs: pytest.fail("preflight renamed")
    )
    result = Q.quarantine(
        expected=expected_values, created_at_utc="2026-09-06T11:00:00Z",
        completed_at_utc="2026-09-06T11:01:00Z", preflight_only=True,
    )
    assert result["validated_partial_intent"] is True
    assert validated == [(fragment == "main", fragment == "sidecar")]


@pytest.mark.parametrize("fragment", ["main", "sidecar"])
def test_preflight_after_rename_validates_partial_provenance_without_repair(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fragment: str,
) -> None:
    expected_values, _tool_values, _zero = patch_common(monkeypatch, tmp_path)
    for path in (Q.INTENT_PATH, Q.INTENT_PATH.with_name(Q.INTENT_PATH.name + ".sha256")):
        path.write_text("fragment", encoding="utf-8")
    partial = (
        Q.PROVENANCE_PATH if fragment == "main"
        else Q.PROVENANCE_PATH.with_name(Q.PROVENANCE_PATH.name + ".sha256")
    )
    partial.write_text("fragment", encoding="utf-8")
    Q.SOURCE_CONTROL_ROOT.rename(Q.QUARANTINE_ROOT)
    before = snapshot(Q.SOURCE_CONTROL_ROOT)
    intent = intent_document(before)
    after = Q._project_snapshot(before, Q.QUARANTINE_ROOT)
    monkeypatch.setattr(Q, "_root_state", lambda: "DESTINATION_QUARANTINED")
    monkeypatch.setattr(Q, "_validate_snapshot_at_root", lambda *_args: after)
    loads: list[Path] = []

    def load(path: Path, *_args, **_kwargs):
        loads.append(path)
        return intent, identity(str(path), "9" * 64)

    monkeypatch.setattr(Q, "_load_selfhashed", load)
    observed: list[tuple[bool, bool]] = []
    monkeypatch.setattr(
        Q, "_validate_pair_fragments",
        lambda path, _doc, _field, presence: (
            observed.append(presence) if path == Q.PROVENANCE_PATH else None
        ),
    )
    monkeypatch.setattr(
        Q, "_publish_pair", lambda *_args: pytest.fail("preflight repaired provenance")
    )
    result = Q.quarantine(
        expected=expected_values, created_at_utc="2026-09-06T11:00:00Z",
        completed_at_utc="2026-09-06T11:01:00Z", preflight_only=True,
    )
    assert result["validated_partial_provenance"] is True
    assert result["provenance_committed"] is False
    assert result["durable_prefix_length"] == 1
    assert observed == [(fragment == "main", fragment == "sidecar")]
    assert loads == [Q.INTENT_PATH]


def test_preflight_complete_provenance_loads_and_validates_terminal_prefix_two(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    expected_values, tool_values, zero = patch_common(monkeypatch, tmp_path)
    for path in (
        Q.INTENT_PATH, Q.INTENT_PATH.with_name(Q.INTENT_PATH.name + ".sha256"),
        Q.PROVENANCE_PATH,
        Q.PROVENANCE_PATH.with_name(Q.PROVENANCE_PATH.name + ".sha256"),
    ):
        path.write_text("committed", encoding="utf-8")
    Q.SOURCE_CONTROL_ROOT.rename(Q.QUARANTINE_ROOT)
    before = snapshot(Q.SOURCE_CONTROL_ROOT)
    intent = intent_document(before)
    after = Q._project_snapshot(before, Q.QUARANTINE_ROOT)
    intent_identity = identity(str(Q.INTENT_PATH), "9" * 64)
    provenance_identity = identity(str(Q.PROVENANCE_PATH), "8" * 64)
    provenance = {
        "schema_version": "blind-phase-confirmatory-fourth-freeze-failure-quarantine-v1",
        "status": "PASS", "attempt_id": Q.ATTEMPT_ID,
        "completed_at_utc": "2026-09-06T11:01:00Z",
        "intent": intent_identity,
        "stage2_recovery_result": intent["stage2"]["result"],
        "tools": tool_values,
        "source_control_root": str(Q.SOURCE_CONTROL_ROOT),
        "quarantine_root": str(Q.QUARANTINE_ROOT),
        "pre_rename_snapshot": before, "post_rename_snapshot": after,
        "root_inode_preserved": True,
        "full_relative_manifest_preserved": True,
        "all_source_entries_accounted_for": True,
        "canonical_control_root_absent_after_recovery": True,
        "source_results_and_iq_preservation": {
            "corrected_source_unchanged": True,
            "empty_results_directory_inode_preserved": True,
            "iq_source_metadata_unchanged": True,
            "iq_content_opened_by_quarantine": False,
            "recursive_delete_performed": False,
        },
        "post_quarantine_state": zero,
    }
    provenance["quarantine_payload_sha256"] = Q._sha256_document(provenance)
    monkeypatch.setattr(Q, "_root_state", lambda: "DESTINATION_QUARANTINED")
    monkeypatch.setattr(Q, "_validate_snapshot_at_root", lambda *_args: after)
    monkeypatch.setattr(Q, "_validate_pair_fragments", lambda *_args: None)
    loads: list[Path] = []

    def load(path: Path, *_args, **_kwargs):
        loads.append(path)
        if path == Q.INTENT_PATH:
            return intent, intent_identity
        return provenance, provenance_identity

    monkeypatch.setattr(Q, "_load_selfhashed", load)
    real_semantics = Q._provenance_semantics
    semantic_calls: list[dict[str, object]] = []

    def validate_semantics(value: dict[str, object], **kwargs):
        semantic_calls.append(value)
        real_semantics(value, **kwargs)

    monkeypatch.setattr(Q, "_provenance_semantics", validate_semantics)
    monkeypatch.setattr(
        Q, "_publish_pair", lambda *_args: pytest.fail("terminal preflight published")
    )
    monkeypatch.setattr(
        Q, "_rename_noreplace", lambda **_kwargs: pytest.fail("terminal preflight renamed")
    )
    result = Q.quarantine(
        expected=expected_values, created_at_utc="2026-09-06T11:00:00Z",
        completed_at_utc="2026-09-06T11:01:00Z", preflight_only=True,
    )
    assert loads == [Q.INTENT_PATH, Q.PROVENANCE_PATH]
    assert semantic_calls == [provenance]
    assert result["status"] == (
        "PASS_READ_ONLY_TERMINAL_PROVENANCE_VALIDATED_NO_PUBLICATION_NO_RENAME"
    )
    assert result["durable_prefix_length"] == 2
    assert result["validated_partial_provenance"] is False
    assert result["provenance_committed"] is True
    assert result["provenance"] == provenance_identity


def test_live_order_is_intent_then_rename_then_provenance(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    expected_values, tool_values, zero = patch_common(monkeypatch, tmp_path)
    before = snapshot(Q.SOURCE_CONTROL_ROOT)
    states = iter(("SOURCE_CANONICAL", "SOURCE_CANONICAL", "DESTINATION_QUARANTINED"))
    monkeypatch.setattr(Q, "_root_state", lambda: next(states))
    monkeypatch.setattr(Q, "_snapshot_root", lambda _root: copy.deepcopy(before))
    monkeypatch.setattr(
        Q, "_validate_snapshot_at_root",
        lambda _frozen, root: Q._project_snapshot(before, root),
    )
    calls: list[str] = []
    published: dict[Path, dict[str, object]] = {}

    def publish(path: Path, document: dict[str, object], field: str):
        calls.append("intent" if path == Q.INTENT_PATH else "provenance")
        stored = copy.deepcopy(document)
        stored[field] = Q._sha256_document(stored)
        published[path] = stored
        return identity(str(path), "9" * 64)

    monkeypatch.setattr(Q, "_publish_pair", publish)
    monkeypatch.setattr(
        Q,
        "_load_selfhashed",
        lambda path, *_args, **_kwargs: (
            published[path], identity(str(path), "9" * 64)
        ),
    )
    def rename(**_kwargs):
        calls.append("rename")
        Q.SOURCE_CONTROL_ROOT.rename(Q.QUARANTINE_ROOT)

    monkeypatch.setattr(Q, "_rename_noreplace", rename)
    result = Q.quarantine(
        expected=expected_values, created_at_utc="2026-09-06T11:00:00Z",
        completed_at_utc="2026-09-06T11:01:00Z", preflight_only=False,
    )
    assert calls == ["intent", "rename", "provenance"]
    assert result["status"] == "PASS"
    assert result["tools"] == tool_values
    assert result["post_quarantine_state"] == zero


def test_resume_after_atomic_rename_publishes_only_provenance(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    expected_values, _tool_values, _zero = patch_common(monkeypatch, tmp_path)
    Q.INTENT_PATH.write_text("committed", encoding="utf-8")
    Q.INTENT_PATH.with_name(Q.INTENT_PATH.name + ".sha256").write_text(
        "committed", encoding="utf-8"
    )
    Q.SOURCE_CONTROL_ROOT.rename(Q.QUARANTINE_ROOT)
    before = snapshot(Q.SOURCE_CONTROL_ROOT)
    intent = intent_document(before)
    monkeypatch.setattr(Q, "_root_state", lambda: "DESTINATION_QUARANTINED")
    monkeypatch.setattr(
        Q, "_validate_snapshot_at_root",
        lambda _frozen, root: Q._project_snapshot(before, root),
    )
    published: dict[Path, dict[str, object]] = {}
    calls: list[str] = []

    def load(path: Path, *_args, **_kwargs):
        value = intent if path == Q.INTENT_PATH else published[path]
        return value, identity(str(path), "9" * 64)

    monkeypatch.setattr(Q, "_load_selfhashed", load)

    def publish(path: Path, document: dict[str, object], field: str):
        calls.append(path.name)
        stored = copy.deepcopy(document)
        stored[field] = Q._sha256_document(stored)
        published[path] = stored
        return identity(str(path), "8" * 64)

    monkeypatch.setattr(Q, "_publish_pair", publish)
    monkeypatch.setattr(
        Q, "_rename_noreplace", lambda **_kwargs: pytest.fail("resume renamed twice")
    )
    result = Q.quarantine(
        expected=expected_values, created_at_utc="2026-09-06T11:00:00Z",
        completed_at_utc="2026-09-06T11:01:00Z", preflight_only=False,
    )
    assert calls == [Q.PROVENANCE_PATH.name]
    assert result["canonical_control_root_absent_after_recovery"] is True


def test_resume_rejects_drifted_committed_zero_runtime_projection(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    expected_values, _tool_values, _zero = patch_common(monkeypatch, tmp_path)
    Q.INTENT_PATH.write_text("committed", encoding="utf-8")
    Q.INTENT_PATH.with_name(Q.INTENT_PATH.name + ".sha256").write_text(
        "committed", encoding="utf-8"
    )
    intent = intent_document(snapshot(Q.SOURCE_CONTROL_ROOT))
    intent["zero_runtime_state"] = {
        **intent["zero_runtime_state"], "external_inventory_sha256": "0" * 64,
    }
    monkeypatch.setattr(
        Q, "_load_selfhashed",
        lambda path, *_args, **_kwargs: (intent, identity(str(path), "9" * 64)),
    )
    monkeypatch.setattr(Q, "_root_state", lambda: "SOURCE_CANONICAL")
    with pytest.raises(Q.QuarantineError, match="zero-runtime projection"):
        Q.quarantine(
            expected=expected_values, created_at_utc="2026-09-06T11:00:00Z",
            completed_at_utc="2026-09-06T11:01:00Z", preflight_only=True,
        )


def test_provenance_resume_rejects_semantic_drift(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    expected_values, tool_values, zero = patch_common(monkeypatch, tmp_path)
    Q.INTENT_PATH.write_text("committed", encoding="utf-8")
    Q.PROVENANCE_PATH.write_text("committed", encoding="utf-8")
    Q.INTENT_PATH.with_name(Q.INTENT_PATH.name + ".sha256").write_text(
        "committed", encoding="utf-8"
    )
    Q.PROVENANCE_PATH.with_name(Q.PROVENANCE_PATH.name + ".sha256").write_text(
        "committed", encoding="utf-8"
    )
    Q.SOURCE_CONTROL_ROOT.rename(Q.QUARANTINE_ROOT)
    before = snapshot(Q.SOURCE_CONTROL_ROOT)
    intent = intent_document(before)
    after = Q._project_snapshot(before, Q.QUARANTINE_ROOT)
    provenance = {
        "schema_version": "blind-phase-confirmatory-fourth-freeze-failure-quarantine-v1",
        "status": "PASS", "attempt_id": Q.ATTEMPT_ID,
        "completed_at_utc": "2026-09-06T11:01:00Z",
        "intent": identity(str(Q.INTENT_PATH), "9" * 64),
        "stage2_recovery_result": intent["stage2"]["result"], "tools": tool_values,
        "source_control_root": str(Q.SOURCE_CONTROL_ROOT),
        "quarantine_root": str(Q.QUARANTINE_ROOT),
        "pre_rename_snapshot": before, "post_rename_snapshot": after,
        "root_inode_preserved": True, "full_relative_manifest_preserved": True,
        "all_source_entries_accounted_for": True,
        "canonical_control_root_absent_after_recovery": True,
        "source_results_and_iq_preservation": {
            "corrected_source_unchanged": True,
            "empty_results_directory_inode_preserved": True,
            "iq_source_metadata_unchanged": True,
            "iq_content_opened_by_quarantine": False,
            "recursive_delete_performed": True,
        },
        "post_quarantine_state": zero,
    }
    provenance["quarantine_payload_sha256"] = Q._sha256_document(provenance)
    monkeypatch.setattr(Q, "_root_state", lambda: "DESTINATION_QUARANTINED")
    monkeypatch.setattr(Q, "_validate_snapshot_at_root", lambda *_args: after)
    monkeypatch.setattr(
        Q,
        "_load_selfhashed",
        lambda path, *_args, **_kwargs: (
            (intent, identity(str(path), "9" * 64))
            if path == Q.INTENT_PATH
            else (provenance, identity(str(path), "8" * 64))
        ),
    )
    with pytest.raises(
        Q.QuarantineError, match="publication fragment conflicts|provenance semantics"
    ):
        Q.quarantine(
            expected=expected_values, created_at_utc="2026-09-06T11:00:00Z",
            completed_at_utc="2026-09-06T11:01:00Z", preflight_only=False,
        )


def test_relative_manifest_is_path_independent_across_atomic_rename(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    for name in ("results-v3", "sealed-input-v3", "sealed-project-artifacts-v3"):
        (source / name).mkdir()
    (source / "runtime").mkdir()
    (source / "runtime/candidate").mkdir()
    (source / "runtime/component").mkdir()
    (source / "metadata.json").write_text("{}", encoding="utf-8")
    before = Q._relative_manifest(source)
    destination = tmp_path / "destination"
    source.rename(destination)
    after = Q._relative_manifest(destination)
    assert before == after


def test_relative_manifest_rejects_symlink(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / "results-v3").mkdir()
    (root / "runtime").mkdir()
    (root / "runtime/candidate").mkdir()
    (root / "runtime/component").mkdir()
    (root / "sealed-input-v3").mkdir()
    (root / "sealed-project-artifacts-v3").mkdir()
    (root / "escape").symlink_to(tmp_path)
    with pytest.raises(Q.QuarantineError, match="symlink"):
        Q._relative_manifest(root)


def test_iq_snapshot_reads_only_metadata_not_content(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    holdout = tmp_path / "holdout"
    holdout.mkdir()
    mounts = []
    for index in range(30):
        path = holdout / f"{index}.iq"
        path.write_bytes(b"iq")
        mounts.append(
            {"source": {"path": str(path), "size_bytes": 2, "sha256": f"{index:064x}"}}
        )
    monkeypatch.setattr(Q, "PROJECT_ROOT", tmp_path)
    expected_root = tmp_path / "work/blind-phase-confirmatory-v2/holdout-iq-v4"
    expected_root.mkdir(parents=True)
    for index, mount in enumerate(mounts):
        new_path = expected_root / f"{index}.iq"
        (holdout / f"{index}.iq").rename(new_path)
        mount["source"]["path"] = str(new_path)
    monkeypatch.setattr(
        Q,
        "_load_selfhashed",
        lambda *_args, **_kwargs: (
            {"sealed_input_snapshot": {"mounts": mounts}}, identity("closed")
        ),
    )
    reads: list[Path] = []
    real_read = Q._read_regular

    def read(path: Path, *args, **kwargs):
        reads.append(path)
        return real_read(path, *args, **kwargs)

    monkeypatch.setattr(Q, "_read_regular", read)
    result = Q._iq_metadata_snapshot(tmp_path / "control")
    assert result["source_count"] == 30
    assert result["content_opened_by_quarantine"] is False
    assert reads == []


def test_atomic_rename_is_no_clobber_and_preserves_inode(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    source.mkdir()
    inode = source.stat().st_ino
    monkeypatch.setattr(Q, "VAR_LIB", tmp_path)
    monkeypatch.setattr(Q, "SOURCE_CONTROL_ROOT", source)
    monkeypatch.setattr(Q, "QUARANTINE_ROOT", destination)
    source_status = source.stat()
    monkeypatch.setattr(Q, "SOURCE_CONTROL_IDENTITY", {
        "path": str(source), "st_dev": source_status.st_dev,
        "st_ino": source_status.st_ino, "uid": source_status.st_uid,
        "gid": source_status.st_gid,
        "mode": stat.S_IMODE(source_status.st_mode), "nlink": source_status.st_nlink,
    })
    status = tmp_path.stat()
    expected_parent = {
        "path": str(tmp_path), "st_dev": status.st_dev, "st_ino": status.st_ino,
        "uid": status.st_uid, "gid": status.st_gid,
        "mode": stat.S_IMODE(status.st_mode),
    }
    Q._rename_noreplace(expected_var_lib=expected_parent)
    assert not source.exists()
    assert destination.stat().st_ino == inode
    source.mkdir()
    replacement = source.stat()
    monkeypatch.setattr(Q, "SOURCE_CONTROL_IDENTITY", {
        "path": str(source), "st_dev": replacement.st_dev,
        "st_ino": replacement.st_ino, "uid": replacement.st_uid,
        "gid": replacement.st_gid,
        "mode": stat.S_IMODE(replacement.st_mode), "nlink": replacement.st_nlink,
    })
    with pytest.raises(OSError):
        Q._rename_noreplace(expected_var_lib=expected_parent)
    assert source.is_dir() and destination.is_dir()


def test_controller_inventory_rejects_foreign_entry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(Q, "EXPECTED_CONTROLLER_BASE_NAMES", {"base"})
    monkeypatch.setattr(
        Q,
        "_directory_state",
        lambda _path: (["base", "foreign"], {
            "path": str(Q.QUARANTINE_CONTROL), "st_dev": 1, "st_ino": 2,
            "uid": 0, "gid": 0, "mode": 0o755, "nlink": 2,
        }),
    )
    with pytest.raises(Q.QuarantineError, match="inventory differs"):
        Q._validate_controller_inventory(
            Q._controller_parent(expected()),
            intent_presence=(False, False),
            provenance_presence=(False, False),
        )


def test_controller_inventory_accepts_only_modeled_partial_publication(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(Q, "EXPECTED_CONTROLLER_BASE_NAMES", {"base"})
    parent = {
        "path": str(Q.QUARANTINE_CONTROL), "st_dev": 1, "st_ino": 2,
        "uid": 0, "gid": 0, "mode": 0o755, "nlink": 2,
    }
    monkeypatch.setattr(
        Q, "_directory_state",
        lambda _path: (["base", Q.INTENT_PATH.name + ".sha256"], parent),
    )
    Q._validate_controller_inventory(
        Q._controller_parent(expected()),
        intent_presence=(False, True), provenance_presence=(False, False),
    )
    with pytest.raises(Q.QuarantineError, match="committed intent"):
        Q._validate_controller_inventory(
            Q._controller_parent(expected()),
            intent_presence=(False, True), provenance_presence=(False, True),
        )


def test_pair_fragment_repair_gate_rejects_wrong_bytes_or_metadata(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    document = {"schema_version": "x", "status": "PASS"}
    target = tmp_path / "record.json"
    _payload, sidecar_template = Q._publication_payload(document, "payload_sha256")
    target.with_name(target.name + ".sha256").write_bytes(
        sidecar_template.replace(b"{name}", target.name.encode())
    )
    with pytest.raises(Q.QuarantineError, match="fragment conflicts"):
        Q._validate_pair_fragments(
            target, document, "payload_sha256", (False, True)
        )


def test_snapshot_root_rejects_replaced_control_inode(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    root = tmp_path / "replacement"
    root.mkdir()
    status = root.stat()
    monkeypatch.setattr(
        Q, "_directory_state",
        lambda _path: ([f"entry-{index}" for index in range(34)], {
            "path": str(root), "st_dev": status.st_dev,
            "st_ino": status.st_ino, "uid": 0, "gid": 0,
            "mode": 0o755, "nlink": 6, "entry_count": 34,
        }),
    )
    monkeypatch.setattr(Q, "_relative_manifest", lambda _root: {})
    monkeypatch.setattr(Q, "_results_snapshot", lambda _root: {})
    monkeypatch.setattr(Q, "_iq_metadata_snapshot", lambda _root: {})
    monkeypatch.setattr(Q, "_corrected_source_snapshot", lambda: {})
    with pytest.raises(Q.QuarantineError, match="inode/metadata"):
        Q._snapshot_root(root)


def test_stage2_snapshot_requires_exact_result_and_prefix() -> None:
    valid = stage2()
    # File reads happen only after these immutable top-level gates pass.
    for field, replacement in (
        ("durable_prefix_length", 8),
        ("result_payload_sha256", "0" * 64),
        ("ordered_receipts", valid["ordered_receipts"][:-1]),
    ):
        changed = copy.deepcopy(valid)
        changed[field] = replacement
        with pytest.raises(Q.QuarantineError, match="snapshot shape"):
            Q._validate_stage2_snapshot(changed)
