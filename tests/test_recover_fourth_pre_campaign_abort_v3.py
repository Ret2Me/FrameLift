from __future__ import annotations

import copy
import importlib.util
import json
import os
from pathlib import Path
import stat
import types

import pytest


SOURCE = (
    Path(__file__).parents[1]
    / "work/blind-phase-confirmatory-v2/recover_fourth_pre_campaign_abort_v3.py"
)
SPEC = importlib.util.spec_from_file_location(
    "recover_fourth_pre_campaign_abort_v3", SOURCE
)
assert SPEC is not None and SPEC.loader is not None
recovery = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(recovery)


def _identity(path: str, digest: str = "a" * 64) -> dict[str, object]:
    return {"path": path, "size_bytes": 1, "sha256": digest}


def _chain() -> dict[str, object]:
    baseline = {"inventory_sha256": "b" * 64, "namespaces": []}
    return {
        "control_inventory": {"fixed": True},
        "incident": _identity("incident"),
        "rollback_recovery": _identity("rollback"),
        "canonical_rollback_ready": _identity("ready"),
        "canonical_rollback_complete": _identity("complete"),
        "source_restoration": _identity("restoration"),
        "parent_open_receipts": {},
        "stage1_independent_review": _identity("review1"),
        "stage2_independent_review": _identity("review2"),
        "retry1_preflight_failure_incident": _identity(
            "retry1-preflight-incident"
        ),
        "rollback_runner": _identity("runner1"),
        "rollback_test": _identity("test1"),
        "abort_test": _identity("test2"),
        "patched_builder": _identity("patched"),
        "historical_quarantine": _identity("historical"),
        "external_baseline": baseline,
        "sealed_document": {"recovery": {}},
        "ready_document": {"restored_roots": {}},
    }


def _expected() -> dict[str, object]:
    return {
        "abort_test": "b" * 64,
        "stage2_review": "c" * 64,
        "recovery_parent_st_dev": 64_512,
        "recovery_parent_st_ino": 9_999,
    }


def _expected_parent(path: Path | None = None) -> dict[str, object]:
    return {
        **recovery.EXPECTED_RECOVERY_PARENT_BASE,
        "path": str(path or recovery.RECOVERY_PARENT),
        "st_dev": 64_512,
        "st_ino": 9_999,
    }


def _patch_stage_paths(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    names = [
        "abort", "stopped", "cleanup-intent", "candidate-q", "candidate-d",
        "component-q", "component-d", "cleanup-pass", "result",
    ]
    ordered = tuple((tmp_path / f"{name}.json", "payload_sha256") for name in names)
    monkeypatch.setattr(recovery, "STAGE2_ORDERED_ARTIFACTS", ordered)


def test_retry2_parent_identity_is_exactly_composed_from_required_cli_values() -> None:
    expected = _expected()
    assert recovery._expected_recovery_parent(expected) == _expected_parent()
    for changed in (
        {**expected, "recovery_parent_st_dev": 0},
        {**expected, "recovery_parent_st_ino": "9999"},
        {key: value for key, value in expected.items() if key != "recovery_parent_st_ino"},
    ):
        with pytest.raises(recovery.RecoveryError, match="device/inode"):
            recovery._expected_recovery_parent(changed)


def test_write_all_retries_partial_writes(monkeypatch: pytest.MonkeyPatch) -> None:
    writes: list[bytes] = []

    def partial(_fd: int, payload: bytes) -> int:
        amount = min(3, len(payload))
        writes.append(payload[:amount])
        return amount

    monkeypatch.setattr(recovery.os, "write", partial)
    recovery._write_all(9, b"abcdefghij")
    assert b"".join(writes) == b"abcdefghij"


def test_write_all_rejects_zero_progress(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(recovery.os, "write", lambda *_args: 0)
    with pytest.raises(recovery.RecoveryError, match="no forward progress"):
        recovery._write_all(9, b"x")


def test_stage_prefix_rejects_gap_before_any_repair(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_stage_paths(monkeypatch, tmp_path)
    recovery.STAGE2_ORDERED_ARTIFACTS[0][0].write_text("main")
    recovery.STAGE2_ORDERED_ARTIFACTS[2][0].write_text("later")
    calls: list[object] = []
    monkeypatch.setattr(recovery, "_load_selfhashed", lambda *args, **kwargs: calls.append(args))
    with pytest.raises(recovery.RecoveryError, match="ordered prefix"):
        recovery._load_stage2_prefix(repair_missing_sidecars=True)
    assert calls == []


def test_stage_prefix_rejects_orphan_sidecar(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_stage_paths(monkeypatch, tmp_path)
    recovery.STAGE2_ORDERED_ARTIFACTS[0][0].with_name("abort.json.sha256").write_text("x")
    with pytest.raises(recovery.RecoveryError, match="orphan"):
        recovery._load_stage2_prefix(repair_missing_sidecars=False)


def test_stage_prefix_defers_missing_sidecar_policy_to_loader(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_stage_paths(monkeypatch, tmp_path)
    path = recovery.STAGE2_ORDERED_ARTIFACTS[0][0]
    path.write_text("main")
    observed: list[bool] = []

    def load(*_args, **kwargs):
        observed.append(kwargs["repair_missing_sidecar"])
        return {"ok": True}, _identity(str(path))

    monkeypatch.setattr(recovery, "_load_selfhashed", load)
    documents, identities = recovery._load_stage2_prefix(repair_missing_sidecars=True)
    assert documents == [{"ok": True}]
    assert identities[0]["path"] == str(path)
    assert observed == [True]


def test_publication_temp_preflight_is_read_only(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_stage_paths(monkeypatch, tmp_path)
    main, _ = recovery.STAGE2_ORDERED_ARTIFACTS[0]
    temporary = main.with_name(f".{main.name}.stage2.tmp")
    temporary.write_bytes(b"partial")
    with pytest.raises(recovery.RecoveryError, match="requires execution"):
        recovery._recover_publication_temps(repair=False)
    assert temporary.read_bytes() == b"partial"


def test_partial_publication_temp_is_discarded_before_reconstruction(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_stage_paths(monkeypatch, tmp_path)
    main, _ = recovery.STAGE2_ORDERED_ARTIFACTS[0]
    temporary = main.with_name(f".{main.name}.stage2.tmp")
    temporary.write_bytes(b"partial")
    monkeypatch.setattr(recovery, "_fsync_directory", lambda _path: None)
    recovery._recover_publication_temps(repair=True)
    assert not temporary.exists()
    assert not main.exists()


def test_complete_publication_temp_is_atomically_resumed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_stage_paths(monkeypatch, tmp_path)
    main, hash_field = recovery.STAGE2_ORDERED_ARTIFACTS[0]
    unhashed = {"schema_version": "test", "status": "PASS"}
    document = {**unhashed, hash_field: recovery._sha256_document(unhashed)}
    payload = (json.dumps(document, sort_keys=True, indent=2) + "\n").encode()
    temporary = main.with_name(f".{main.name}.stage2.tmp")
    temporary.write_bytes(payload)
    temporary.chmod(0o444)
    real_read = recovery._read_regular

    def pretend_root(path: Path, *args, **kwargs):
        data, identity = real_read(path, *args, **kwargs)
        if path == temporary:
            identity = {**identity, "uid": 0, "gid": 0}
        return data, identity

    monkeypatch.setattr(recovery, "_read_regular", pretend_root)
    monkeypatch.setattr(recovery, "_fsync_directory", lambda _path: None)
    recovery._recover_publication_temps(repair=True)
    assert main.read_bytes() == payload
    assert not temporary.exists()


def test_invalid_early_temp_with_later_artifact_rejects_before_discard(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_stage_paths(monkeypatch, tmp_path)
    first = recovery.STAGE2_ORDERED_ARTIFACTS[0][0]
    partial = first.with_name(f".{first.name}.stage2.tmp")
    partial.write_bytes(b"partial")
    recovery.STAGE2_ORDERED_ARTIFACTS[1][0].write_text("later committed")
    with pytest.raises(recovery.RecoveryError, match="ordered prefix"):
        recovery._recover_publication_temps(repair=True)
    assert partial.read_bytes() == b"partial"


def test_immutable_chain_and_exact_parent_gate_precede_any_repair(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    order: list[str] = []
    chain = _chain()
    monkeypatch.setattr(recovery, "_validate_runner", lambda _sha: _identity("runner"))
    monkeypatch.setattr(recovery, "_validate_recovery_parent_metadata", lambda _expected: {})
    monkeypatch.setattr(
        recovery,
        "_validate_chain",
        lambda *_args: order.append("immutable-chain") or chain,
    )
    monkeypatch.setattr(
        recovery,
        "_validate_recovery_parent_before_repair",
        lambda _expected: (
            order == ["immutable-chain"]
            and order.append("exact-parent")
        ) or {},
    )

    def repair(**_kwargs) -> None:
        assert order == ["immutable-chain", "exact-parent"]
        order.append("repair")

    monkeypatch.setattr(recovery, "_recover_publication_temps", repair)

    def failure(*_args, **_kwargs):
        assert order == ["immutable-chain", "exact-parent", "repair"]
        order.append("failure-sidecar-repair")
        return None

    monkeypatch.setattr(recovery, "_failure_resume", failure)
    monkeypatch.setattr(
        recovery, "_load_and_validate_progress", lambda **_kwargs: ([], [])
    )
    monkeypatch.setattr(recovery, "_validate_failure_binding", lambda *_args: None)
    monkeypatch.setattr(recovery, "_keeper_state", lambda: "present")
    monkeypatch.setattr(recovery, "_pre_signal_evidence", lambda _chain: {"fixed": True})
    result = recovery.recover("a" * 64, expected=_expected(), preflight_only=True)
    assert result["status"] == "PASS_READ_ONLY_NO_PUBLICATION_NO_SIGNAL_NO_CLEANUP"
    assert order == [
        "immutable-chain", "exact-parent", "repair", "failure-sidecar-repair"
    ]


def test_pre_repair_parent_inventory_rejects_foreign_entry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(recovery, "EXPECTED_RECOVERY_BASE_NAMES", {"base"})
    monkeypatch.setattr(
        recovery,
        "_directory_state",
        lambda _path: (["base", "foreign"], {**_expected_parent(), "nlink": 2}),
    )
    with pytest.raises(recovery.RecoveryError, match="foreign state before repair"):
        recovery._validate_recovery_parent_before_repair(_expected_parent())
    repairs: list[bool] = []
    monkeypatch.setattr(recovery, "_validate_runner", lambda _sha: _identity("runner"))
    monkeypatch.setattr(recovery, "_validate_recovery_parent_metadata", lambda _expected: {})
    monkeypatch.setattr(recovery, "_validate_chain", lambda *_args: _chain())
    monkeypatch.setattr(
        recovery,
        "_recover_publication_temps",
        lambda **_kwargs: repairs.append(True),
    )
    with pytest.raises(recovery.RecoveryError, match="foreign state before repair"):
        recovery.recover("a" * 64, expected=_expected(), preflight_only=False)
    assert repairs == []


def test_pre_repair_parent_inventory_accepts_only_fixed_modeled_temp(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    _patch_stage_paths(monkeypatch, tmp_path)
    monkeypatch.setattr(recovery, "RECOVERY_PARENT", tmp_path)
    monkeypatch.setattr(recovery, "EXPECTED_RECOVERY_BASE_NAMES", {"base"})
    main, hash_field = recovery.STAGE2_ORDERED_ARTIFACTS[0]
    temp_name = f".{main.name}.stage2.tmp"
    temporary = tmp_path / temp_name
    unhashed = {"schema_version": "test", "status": "PASS"}
    document = {**unhashed, hash_field: recovery._sha256_document(unhashed)}
    temporary.write_text(json.dumps(document), encoding="utf-8")
    temporary.chmod(0o444)
    real_read = recovery._read_regular

    def pretend_root(path: Path, *args, **kwargs):
        payload, identity = real_read(path, *args, **kwargs)
        if path == temporary:
            identity = {**identity, "uid": 0, "gid": 0}
        return payload, identity

    monkeypatch.setattr(recovery, "_read_regular", pretend_root)
    monkeypatch.setattr(
        recovery,
        "_directory_state",
        lambda _path: (
            ["base", temp_name],
            {**_expected_parent(tmp_path), "nlink": 2},
        ),
    )
    evidence = recovery._validate_recovery_parent_before_repair(
        _expected_parent(tmp_path)
    )
    assert evidence["modeled_residue"] == [temp_name]
    assert evidence["effective_prefix_length"] == 1


def test_later_orphan_sidecar_blocks_earlier_temp_repair_without_mutation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    _patch_stage_paths(monkeypatch, tmp_path)
    monkeypatch.setattr(recovery, "RECOVERY_PARENT", tmp_path)
    (tmp_path / "base").write_text("static", encoding="utf-8")
    monkeypatch.setattr(recovery, "EXPECTED_RECOVERY_BASE_NAMES", {"base"})
    status = tmp_path.stat()
    expected_parent = {
        "path": str(tmp_path), "st_dev": status.st_dev, "st_ino": status.st_ino,
        "uid": status.st_uid, "gid": status.st_gid,
        "mode": stat.S_IMODE(status.st_mode),
    }
    monkeypatch.setattr(recovery, "EXPECTED_RECOVERY_PARENT_BASE", expected_parent)
    first, hash_field = recovery.STAGE2_ORDERED_ARTIFACTS[0]
    unhashed = {"schema_version": "test", "status": "PASS"}
    document = {**unhashed, hash_field: recovery._sha256_document(unhashed)}
    first_temp = first.with_name(f".{first.name}.stage2.tmp")
    first_temp.write_text(json.dumps(document), encoding="utf-8")
    first_temp.chmod(0o444)
    original_temp = first_temp.read_bytes()
    second = recovery.STAGE2_ORDERED_ARTIFACTS[1][0]
    second.with_name(second.name + ".sha256").write_text("orphan", encoding="utf-8")
    real_read = recovery._read_regular

    def pretend_root(path: Path, *args, **kwargs):
        payload, identity = real_read(path, *args, **kwargs)
        if path == first_temp:
            identity = {**identity, "uid": 0, "gid": 0}
        return payload, identity

    monkeypatch.setattr(recovery, "_read_regular", pretend_root)
    monkeypatch.setattr(recovery, "_validate_runner", lambda _sha: _identity("runner"))
    monkeypatch.setattr(recovery, "_validate_recovery_parent_metadata", lambda _expected: {})
    monkeypatch.setattr(recovery, "_validate_chain", lambda *_args: _chain())
    repairs: list[bool] = []
    monkeypatch.setattr(
        recovery,
        "_recover_publication_temps",
        lambda **_kwargs: repairs.append(True),
    )
    expected = {
        **_expected(),
        "recovery_parent_st_dev": status.st_dev,
        "recovery_parent_st_ino": status.st_ino,
    }
    with pytest.raises(recovery.RecoveryError, match="orphan committed"):
        recovery.recover("a" * 64, expected=expected, preflight_only=False)
    assert repairs == []
    assert first_temp.read_bytes() == original_temp


def test_incomplete_cleanup_intent_cannot_authorize_candidate_tombstone_repair(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    _patch_stage_paths(monkeypatch, tmp_path)
    monkeypatch.setattr(recovery, "RECOVERY_PARENT", tmp_path)
    (tmp_path / "base").write_text("static", encoding="utf-8")
    monkeypatch.setattr(recovery, "EXPECTED_RECOVERY_BASE_NAMES", {"base"})
    candidate_tombstone = tmp_path / "candidate-tombstone"
    component_tombstone = tmp_path / "component-tombstone"
    candidate_tombstone.mkdir()
    monkeypatch.setattr(
        recovery,
        "TRANSACTION_SPECS",
        {
            "candidate": {"tombstone": candidate_tombstone},
            "component": {"tombstone": component_tombstone},
        },
    )

    def publication_payload(hash_field: str) -> bytes:
        unhashed = {"schema_version": "test", "status": "PASS"}
        document = {**unhashed, hash_field: recovery._sha256_document(unhashed)}
        return json.dumps(document, sort_keys=True).encode()

    root_owned: set[Path] = set()
    for main, hash_field in recovery.STAGE2_ORDERED_ARTIFACTS[:2]:
        payload = publication_payload(hash_field)
        main.write_bytes(payload)
        main.chmod(0o444)
        sidecar = main.with_name(main.name + ".sha256")
        sidecar.write_bytes(f"{recovery._sha256(payload)}  {main.name}\n".encode())
        sidecar.chmod(0o444)
        root_owned.update((main, sidecar))
    cleanup_main, cleanup_hash_field = recovery.STAGE2_ORDERED_ARTIFACTS[2]
    cleanup_temp = cleanup_main.with_name(f".{cleanup_main.name}.stage2.tmp")
    cleanup_temp.write_bytes(publication_payload(cleanup_hash_field))
    cleanup_temp.chmod(0o444)
    root_owned.add(cleanup_temp)
    original_temp = cleanup_temp.read_bytes()
    real_read = recovery._read_regular

    def pretend_root(path: Path, *args, **kwargs):
        payload, identity = real_read(path, *args, **kwargs)
        if path in root_owned:
            identity = {**identity, "uid": 0, "gid": 0}
        return payload, identity

    monkeypatch.setattr(recovery, "_read_regular", pretend_root)
    status = tmp_path.stat()
    expected_parent = {
        "path": str(tmp_path), "st_dev": status.st_dev, "st_ino": status.st_ino,
        "uid": status.st_uid, "gid": status.st_gid,
        "mode": stat.S_IMODE(status.st_mode),
    }
    monkeypatch.setattr(recovery, "EXPECTED_RECOVERY_PARENT_BASE", expected_parent)
    monkeypatch.setattr(recovery, "_validate_runner", lambda _sha: _identity("runner"))
    monkeypatch.setattr(recovery, "_validate_recovery_parent_metadata", lambda _expected: {})
    monkeypatch.setattr(recovery, "_validate_chain", lambda *_args: _chain())
    repairs: list[bool] = []
    monkeypatch.setattr(
        recovery,
        "_recover_publication_temps",
        lambda **_kwargs: repairs.append(True),
    )
    expected = {
        **_expected(),
        "recovery_parent_st_dev": status.st_dev,
        "recovery_parent_st_ino": status.st_ino,
    }
    with pytest.raises(recovery.RecoveryError, match="fully committed prerequisites"):
        recovery.recover("a" * 64, expected=expected, preflight_only=False)
    assert repairs == []
    assert cleanup_temp.read_bytes() == original_temp
    assert candidate_tombstone.is_dir()


@pytest.mark.parametrize(
    ("prefix", "keeper"),
    [(0, "present"), (1, "present"), (1, "absent"), (2, "absent"), (5, "absent"), (9, "absent")],
)
def test_preflight_accepts_every_resumable_lifecycle_class(
    monkeypatch: pytest.MonkeyPatch, prefix: int, keeper: str
) -> None:
    documents = [{"index": index} for index in range(prefix)]
    identities = [_identity(f"artifact-{index}") for index in range(prefix)]
    monkeypatch.setattr(recovery, "_recover_publication_temps", lambda **_kwargs: None)
    monkeypatch.setattr(recovery, "_validate_recovery_parent_metadata", lambda _expected: {})
    monkeypatch.setattr(recovery, "_validate_recovery_parent_before_repair", lambda _expected: {})
    monkeypatch.setattr(recovery, "_validate_runner", lambda _sha: _identity("runner"))
    monkeypatch.setattr(recovery, "_failure_resume", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(recovery, "_validate_chain", lambda *_args: _chain())
    monkeypatch.setattr(recovery, "_load_and_validate_progress", lambda **_kwargs: (documents, identities))
    monkeypatch.setattr(recovery, "_validate_progress_documents", lambda *_args: None)
    monkeypatch.setattr(recovery, "_keeper_state", lambda: keeper)
    monkeypatch.setattr(recovery, "_pre_signal_evidence", lambda _chain: {"phase": "pre"})
    monkeypatch.setattr(recovery, "_post_keeper_evidence", lambda _chain: {"phase": "post"})
    monkeypatch.setattr(recovery, "_validate_cleanup_filesystem", lambda *_args: None)
    monkeypatch.setattr(recovery, "_cleanup_mount_gate", lambda *_args: None)
    monkeypatch.setattr(recovery, "_persistent_capacity", lambda: {"available_bytes": 9})
    monkeypatch.setattr(recovery, "_inventory_cleanup_targets", lambda *_args: ({}, {}, {}))
    result = recovery.recover(
        "a" * 64,
        expected=_expected(),
        preflight_only=True,
    )
    assert result["durable_prefix_length"] == prefix
    assert result["keeper_state"] == keeper


@pytest.mark.parametrize(("prefix", "keeper"), [(0, "absent"), (2, "present"), (8, "present")])
def test_preflight_rejects_impossible_keeper_checkpoint_pair(
    monkeypatch: pytest.MonkeyPatch, prefix: int, keeper: str
) -> None:
    monkeypatch.setattr(recovery, "_validate_runner", lambda _sha: _identity("runner"))
    monkeypatch.setattr(recovery, "_recover_publication_temps", lambda **_kwargs: None)
    monkeypatch.setattr(recovery, "_validate_recovery_parent_metadata", lambda _expected: {})
    monkeypatch.setattr(recovery, "_validate_recovery_parent_before_repair", lambda _expected: {})
    monkeypatch.setattr(recovery, "_failure_resume", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(recovery, "_validate_chain", lambda *_args: _chain())
    monkeypatch.setattr(
        recovery, "_load_and_validate_progress",
        lambda **_kwargs: (
            [{"index": index} for index in range(prefix)],
            [_identity(f"artifact-{index}") for index in range(prefix)],
        ),
    )
    monkeypatch.setattr(recovery, "_validate_progress_documents", lambda *_args: None)
    monkeypatch.setattr(recovery, "_keeper_state", lambda: keeper)
    with pytest.raises(recovery.RecoveryError):
        recovery.recover(
            "a" * 64,
            expected=_expected(),
            preflight_only=True,
        )


def test_preflight_main_never_opens_or_creates_lock(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(recovery, "STAGE2_TEMPLATE_BLOCKED_PENDING_RETRY2_REVIEW", False)
    monkeypatch.setattr(recovery, "RECOVERY_PARENT", Path("/"))
    root_status = Path("/").stat()
    monkeypatch.setattr(
        recovery,
        "_expected_recovery_parent",
        lambda _expected: {
            "path": "/", "st_dev": root_status.st_dev, "st_ino": root_status.st_ino,
            "uid": root_status.st_uid, "gid": root_status.st_gid,
            "mode": stat.S_IMODE(root_status.st_mode),
        },
    )
    monkeypatch.setattr(recovery, "recover", lambda *_args, **_kwargs: {"status": "PASS"})
    monkeypatch.setattr(
        recovery.os, "open", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("lock opened"))
    )
    assert recovery.main([
        "--expected-script-sha256", "a" * 64,
        "--expected-abort-test-sha256", "b" * 64,
        "--expected-stage2-review-sha256", "c" * 64,
        "--expected-recovery-parent-st-dev", str(root_status.st_dev),
        "--expected-recovery-parent-st-ino", str(root_status.st_ino),
        "--preflight-only",
    ]) == 0
    assert '"status":"PASS"' in capsys.readouterr().out


def _fake_cleanup(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    quarantine_parent = tmp_path / "quarantine"
    quarantine_parent.mkdir()
    monkeypatch.setattr(recovery, "RECOVERY_PARENT", quarantine_parent)
    specifications: dict[str, dict[str, object]] = {}
    transactions: dict[str, object] = {}
    roots: dict[str, object] = {}
    parents: dict[str, object] = {}
    for index, label in enumerate(("candidate", "component"), start=1):
        parent = tmp_path / label
        parent.mkdir()
        root = parent / "root"
        root.mkdir()
        original = parent / "transaction"
        original.mkdir()
        (original / "payload").write_bytes(label.encode())
        tombstone = quarantine_parent / f"{label}-tombstone"
        tree = recovery._tree_identity(original)
        specifications[label] = {
            "root": root, "transaction": original, "tombstone": tombstone,
            "content_manifest_sha256": "d" * 64,
            "entry_count_including_transaction_root": tree["entry_count"] + 1,
            "allocated_bytes": tree["allocated_bytes"],
        }
        transactions[label] = {
            "tree": tree,
            "members": {name: {} for name in recovery.EXPECTED_TRANSACTION_MEMBERS},
            "tombstone_path": str(tombstone),
        }
        _, parent_identity = recovery._directory_state(parent)
        _, quarantine_identity = recovery._directory_state(quarantine_parent)
        parents[label] = {
            "source": recovery._parent_anchor(parent_identity),
            "quarantine": recovery._parent_anchor(quarantine_identity),
        }
        roots[label] = {"path": str(root), "label": label}
    monkeypatch.setattr(recovery, "TRANSACTION_SPECS", specifications)
    monkeypatch.setattr(recovery, "_protected_tree_identity", lambda path: roots[path.parent.name])
    return {
        "transaction_directories": transactions,
        "restored_roots_before_cleanup": roots,
        "transaction_parents": parents,
    }


@pytest.mark.parametrize("prefix", [3, 4, 5, 6, 7, 8, 9])
def test_cleanup_filesystem_accepts_each_crash_boundary(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, prefix: int
) -> None:
    document = _fake_cleanup(monkeypatch, tmp_path)
    candidate = recovery.TRANSACTION_SPECS["candidate"]
    component = recovery.TRANSACTION_SPECS["component"]
    if prefix >= 4:
        os.rename(candidate["transaction"], candidate["tombstone"])
    if prefix >= 5:
        os.rmdir(Path(candidate["tombstone"]) / "payload") if False else None
        import shutil
        shutil.rmtree(candidate["tombstone"])
    if prefix >= 6:
        os.rename(component["transaction"], component["tombstone"])
    if prefix >= 7:
        import shutil
        shutil.rmtree(component["tombstone"])
    recovery._validate_cleanup_filesystem(prefix, document)


def test_cleanup_filesystem_accepts_partial_tombstone_only_after_quarantine_receipt(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    document = _fake_cleanup(monkeypatch, tmp_path)
    spec = recovery.TRANSACTION_SPECS["candidate"]
    os.rename(spec["transaction"], spec["tombstone"])
    (Path(spec["tombstone"]) / "payload").unlink()
    with pytest.raises(recovery.RecoveryError):
        recovery._validate_cleanup_filesystem(3, document)
    recovery._validate_cleanup_filesystem(4, document)


def test_cleanup_filesystem_rejects_original_reappearing_after_delete_receipt(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    document = _fake_cleanup(monkeypatch, tmp_path)
    with pytest.raises(recovery.RecoveryError, match="remains after deletion"):
        recovery._validate_cleanup_filesystem(5, document)


def test_atomic_quarantine_uses_exact_direct_sibling_and_is_idempotent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    document = _fake_cleanup(monkeypatch, tmp_path)
    monkeypatch.setattr(recovery, "_cleanup_mount_gate", lambda *_args: None)
    recovery._quarantine_target("candidate", document, _chain())
    spec = recovery.TRANSACTION_SPECS["candidate"]
    assert not Path(spec["transaction"]).exists()
    assert Path(spec["tombstone"]).is_dir()
    recovery._quarantine_target("candidate", document, _chain())


def test_fd_relative_delete_resumes_from_partial_tombstone(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    document = _fake_cleanup(monkeypatch, tmp_path)
    monkeypatch.setattr(recovery, "_cleanup_mount_gate", lambda *_args: None)
    recovery._quarantine_target("candidate", document, _chain())
    spec = recovery.TRANSACTION_SPECS["candidate"]
    (Path(spec["tombstone"]) / "payload").unlink()
    recovery._delete_tombstone("candidate", document, _chain())
    assert not Path(spec["tombstone"]).exists()
    recovery._delete_tombstone("candidate", document, _chain())


def test_delete_rejects_symlink_tombstone(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    document = _fake_cleanup(monkeypatch, tmp_path)
    spec = recovery.TRANSACTION_SPECS["candidate"]
    import shutil
    shutil.rmtree(spec["transaction"])
    Path(spec["tombstone"]).symlink_to(spec["root"], target_is_directory=True)
    with pytest.raises(recovery.RecoveryError):
        recovery._delete_tombstone("candidate", document, _chain())


def test_pidfd_identity_requires_exact_pid_and_nspid(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        recovery, "_read_proc",
        lambda *_args: f"Pid:\t{recovery.EXPECTED_KEEPER['pid']}\nNSpid:\t999\n".encode(),
    )
    with pytest.raises(recovery.RecoveryError, match="exact keeper"):
        recovery._pidfd_identity(7)


def test_pidfd_identity_matches_linux_fdinfo_for_current_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    keeper = {**recovery.EXPECTED_KEEPER, "pid": os.getpid()}
    monkeypatch.setattr(recovery, "EXPECTED_KEEPER", keeper)
    descriptor = os.pidfd_open(os.getpid(), 0)
    try:
        identity = recovery._pidfd_identity(descriptor)
    finally:
        os.close(descriptor)
    assert identity["pid"] == os.getpid()
    assert identity["nspid"] == str(os.getpid())
    assert identity["fdinfo_inode"] == identity["fd_st_ino"]


def test_signal_commitment_survives_failure_after_successful_send(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    commitment = {"committed": False}
    monkeypatch.setattr(recovery.os, "pidfd_open", lambda *_args: 7)
    monkeypatch.setattr(recovery, "_pidfd_identity", lambda _fd: {})
    monkeypatch.setattr(recovery, "_pre_signal_evidence", lambda _chain: {"fixed": True})
    monkeypatch.setattr(recovery.signal, "pidfd_send_signal", lambda *_args: None)
    monkeypatch.setattr(recovery.os, "close", lambda _fd: None)

    class Poll:
        def register(self, *_args) -> None:
            return None

        def poll(self, _milliseconds: int):
            return []

    monkeypatch.setattr(recovery.select, "poll", Poll)
    with pytest.raises(recovery.RecoveryError, match="did not exit"):
        recovery._terminate_keeper(0.1, _chain(), commitment)
    assert commitment == {"committed": True}


def _valid_abort_document(chain: dict[str, object], runner: dict[str, object]):
    results = {
        "host_backing": {
            "path": str(recovery.RESULTS_PARENT), "st_dev": 64_512,
            "st_ino": 1_180_687, "uid": 0, "gid": 0, "mode": 0o755,
            "nlink": 2, "entry_count": 0,
        },
        "keeper_private_tmpfs": {
            "path": str(Path("/proc") / str(recovery.EXPECTED_KEEPER["pid"]) / "root" / str(recovery.RESULTS_PARENT).lstrip("/")),
            "st_dev": 48, "st_ino": 1, "uid": 1_000, "gid": 1_000,
            "mode": 0o700, "nlink": 2, "entry_count": 0,
        },
    }
    evidence = {
        "topology": {
            **recovery.EXPECTED_POST_ROLLBACK_MOUNTINFO,
            "remaining_mounts": recovery.EXPECTED_REMAINING_MOUNTS,
        },
        "processes": {
            "namespace_member_pids": [recovery.EXPECTED_KEEPER["pid"]],
            "namespace_fd_handles": [], "suspicious_processes": [],
        },
        "results": results, "control": chain["control_inventory"],
        "external": chain["external_baseline"],
        "source_restoration_state": {
            "patched_builder": chain["patched_builder"],
            "historical_quarantine": chain["historical_quarantine"],
        },
    }
    document = recovery._make_abort_document(runner, chain, evidence)
    document["recorded_at_utc"] = "2026-09-06T12:34:56Z"
    return json.loads(json.dumps(document))


@pytest.mark.parametrize(
    "mutation",
    [
        lambda doc: doc["empty_results"].clear(),
        lambda doc: doc["process_exclusivity"].update({"namespace_member_pids": []}),
        lambda doc: doc["authorized_action"].update({"manual_unmounts": True}),
        lambda doc: doc["scientific_exposure"].update({"iq_opened_by_abort": True}),
        lambda doc: doc.update({"control_inventory": {}}),
    ],
)
def test_abort_resume_validator_rejects_underbound_claims(mutation) -> None:
    chain = _chain()
    runner = _identity("runner")
    document = _valid_abort_document(chain, runner)
    recovery._validate_abort_intent(document, _identity("abort"), chain, runner)
    mutation(document)
    with pytest.raises(recovery.RecoveryError, match="abort intent"):
        recovery._validate_abort_intent(document, _identity("abort"), chain, runner)


def _valid_stopped_document(chain: dict[str, object], abort: dict[str, object]):
    post = {
        "results": {
            "host_backing": {
                "path": str(recovery.RESULTS_PARENT), "st_dev": 64_512,
                "st_ino": 1_180_687, "uid": 0, "gid": 0, "mode": 0o755,
                "nlink": 2, "entry_count": 0,
            }
        },
        "processes": {"namespace_member_pids": [], "namespace_fd_handles": [], "suspicious_processes": []},
        "external": chain["external_baseline"], "control": chain["control_inventory"],
        "source_restoration_state": {
            "patched_builder": chain["patched_builder"],
            "historical_quarantine": chain["historical_quarantine"],
        },
    }
    return {
        "schema_version": "blind-phase-confirmatory-fourth-pre-campaign-abort-stopped-v1",
        "status": "PASS", "attempt_id": recovery.ATTEMPT_ID,
        "recorded_at_utc": "2026-09-06T12:34:56Z", "abort_intent": abort,
        "keeper_termination": {
            "authorized_method": "pidfd_send_signal", "authorized_signal": "SIGTERM",
            "exact_generation_absent": True, "automatic_sigkill": False,
            "pidfd_identity_before_signal": None,
            "resumed_after_committed_signal_or_exit": True,
        },
        "namespace_teardown": {
            "manual_mount_operations": False,
            "released_lifetime_mount_ids": sorted(recovery.EXPECTED_REMAINING_MOUNTS),
            "private_namespace_absent": True,
        },
        "post_teardown_evidence": post,
        "external_namespace_zero_delta": {
            "before": chain["external_baseline"]["inventory_sha256"],
            "after": chain["external_baseline"]["inventory_sha256"],
            "byte_identical": True,
        },
        "runtime_guard_role_decoder_evaluator_export_absent": True,
    }


@pytest.mark.parametrize(
    "field",
    ["keeper_termination", "namespace_teardown", "post_teardown_evidence", "external_namespace_zero_delta"],
)
def test_stopped_resume_validator_rejects_underbound_claims(field: str) -> None:
    chain = _chain()
    abort = _identity("abort")
    document = _valid_stopped_document(chain, abort)
    recovery._validate_stopped(document, abort, chain)
    document[field] = {}
    with pytest.raises(recovery.RecoveryError, match="stopped receipt"):
        recovery._validate_stopped(document, abort, chain)


def test_fixed_sidecar_repair_temp_is_modeled_in_crash_recovery() -> None:
    source = SOURCE.read_text(encoding="utf-8")
    assert '.stage2.tmp")' in source
    assert ".repair.tmp" not in source


def test_result_commit_failure_does_not_create_incompatible_failure_lockout(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    result_path = tmp_path / "result.json"
    monkeypatch.setattr(recovery, "RESULT_PATH", result_path)
    runner = _identity("runner")
    chain = _chain()
    chain["control_inventory"] = {
        "files": {recovery.CANONICAL_ROLLBACK_INTENT_PATH.name: _identity("intent")}
    }
    documents = [{"index": index} for index in range(8)]
    documents[2] = {"transaction_directory_disposition": {}, "transaction_directories": {}}
    documents[7] = {"transaction_directory_disposition": {"candidate": {}, "component": {}}}
    identities = [_identity(f"artifact-{index}") for index in range(8)]
    monkeypatch.setattr(recovery, "_validate_runner", lambda _sha: runner)
    monkeypatch.setattr(recovery, "_validate_recovery_parent_metadata", lambda _expected: {})
    monkeypatch.setattr(recovery, "_validate_recovery_parent_before_repair", lambda _expected: {})
    monkeypatch.setattr(recovery, "_recover_publication_temps", lambda **_kwargs: None)
    monkeypatch.setattr(recovery, "_failure_resume", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(recovery, "_validate_chain", lambda *_args: chain)
    monkeypatch.setattr(recovery, "_load_and_validate_progress", lambda **_kwargs: (documents, identities))
    monkeypatch.setattr(recovery, "_validate_progress_documents", lambda *_args: None)
    monkeypatch.setattr(recovery, "_validate_failure_binding", lambda *_args: None)
    monkeypatch.setattr(recovery, "_keeper_state", lambda: "absent")
    monkeypatch.setattr(recovery, "_post_keeper_evidence", lambda _chain: {"post": True})
    monkeypatch.setattr(recovery, "_validate_cleanup_filesystem", lambda *_args: None)
    monkeypatch.setattr(recovery, "_cleanup_mount_gate", lambda *_args: None)
    monkeypatch.setattr(recovery, "_persistent_capacity", lambda: {"available_bytes": 9})
    failures: list[object] = []
    monkeypatch.setattr(recovery, "_publish_failure_once", lambda **kwargs: failures.append(kwargs))

    def commit_then_fail(path: Path, *_args):
        assert path == result_path
        path.write_text("durable main")
        raise OSError("fsync-after-rename fault")

    monkeypatch.setattr(recovery, "_publish", commit_then_fail)
    with pytest.raises(OSError, match="fsync-after-rename"):
        recovery.recover(
            "a" * 64,
            expected=_expected(),
        )
    assert failures == []

    terminal = {"status": "PASS", "recovery_payload_sha256": "d" * 64}
    monkeypatch.setattr(
        recovery, "_load_and_validate_progress",
        lambda **_kwargs: ([*documents, terminal], [*identities, _identity(str(result_path))]),
    )
    monkeypatch.setattr(recovery, "_validate_recovery_parent_inventory", lambda *_args, **_kwargs: {})
    assert recovery.recover(
        "a" * 64,
        expected=_expected(),
    ) == terminal


@pytest.mark.parametrize(
    ("start_prefix", "committed_prefix"),
    [(0, 1), (1, 2), (2, 3), (3, 4), (4, 5), (5, 6), (6, 7), (7, 8)],
)
def test_failure_checkpoint_advances_immediately_after_each_durable_publish(
    monkeypatch: pytest.MonkeyPatch, start_prefix: int, committed_prefix: int
) -> None:
    runner = _identity("runner")
    chain = _chain()
    transaction = {
        "tree": {"entry_count": 0, "allocated_bytes": 0},
        "members": {},
    }
    cleanup = {
        "transaction_directories": {
            "candidate": transaction, "component": transaction,
        },
        "restored_roots_before_cleanup": {},
    }
    documents = [{"index": index} for index in range(start_prefix)]
    if start_prefix >= 3:
        documents[2] = cleanup
    identities = [_identity(f"artifact-{index}") for index in range(start_prefix)]
    calls = 0

    def progress(**_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return documents, identities
        raise recovery.RecoveryError("post-publish reload fault")

    monkeypatch.setattr(recovery, "_validate_runner", lambda _sha: runner)
    monkeypatch.setattr(recovery, "_validate_recovery_parent_metadata", lambda _expected: {})
    monkeypatch.setattr(recovery, "_validate_recovery_parent_before_repair", lambda _expected: {})
    monkeypatch.setattr(recovery, "_recover_publication_temps", lambda **_kwargs: None)
    monkeypatch.setattr(recovery, "_failure_resume", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(recovery, "_validate_chain", lambda *_args: chain)
    monkeypatch.setattr(recovery, "_load_and_validate_progress", progress)
    monkeypatch.setattr(recovery, "_validate_failure_binding", lambda *_args: None)
    monkeypatch.setattr(recovery, "_keeper_state", lambda: "present" if start_prefix == 0 else "absent")
    monkeypatch.setattr(recovery, "_pre_signal_evidence", lambda _chain: {
        "topology": {}, "processes": {}, "results": {}, "control": {},
        "source_restoration_state": {}, "external": chain["external_baseline"],
    })
    monkeypatch.setattr(
        recovery, "_post_keeper_evidence",
        lambda _chain: {"post": True, "external": chain["external_baseline"]},
    )
    monkeypatch.setattr(recovery, "_validate_cleanup_filesystem", lambda *_args: None)
    monkeypatch.setattr(recovery, "_cleanup_mount_gate", lambda *_args: None)
    monkeypatch.setattr(recovery, "_inventory_cleanup_targets", lambda *_args: ({}, {}, {}))
    monkeypatch.setattr(recovery, "_quarantine_target", lambda *_args: None)
    monkeypatch.setattr(recovery, "_delete_tombstone", lambda *_args: None)
    monkeypatch.setattr(recovery, "_validate_restored_roots", lambda *_args: {})
    monkeypatch.setattr(recovery, "_persistent_capacity", lambda: {})
    monkeypatch.setattr(recovery, "_publish", lambda path, *_args: _identity(str(path)))
    failures: list[dict[str, object]] = []
    monkeypatch.setattr(
        recovery, "_publish_failure_once",
        lambda **kwargs: failures.append(kwargs) or _identity("failure"),
    )
    with pytest.raises(recovery.RecoveryError, match="reload fault"):
        recovery.recover(
            "a" * 64,
            expected=_expected(),
        )
    assert failures[0]["checkpoint"] == committed_prefix


def test_failure_lockout_requires_exact_semantic_shape() -> None:
    valid = {
        "schema_version": "blind-phase-confirmatory-fourth-pre-campaign-abort-failure-v1",
        "status": "FAIL_CLOSED_AT_DURABLE_STAGE2_CHECKPOINT",
        "attempt_id": recovery.ATTEMPT_ID,
        "recorded_at_utc": "2026-09-06T00:00:00Z",
        "abort_intent": _identity("abort"),
        "durable_prefix_length": 4,
        "signal_committed_this_invocation": True,
        "error": {"type": "RecoveryError", "message": "injected fault"},
        "recovery_payload_sha256": "f" * 64,
    }
    assert recovery._validate_failure_document(valid) == 4
    mutations: list[dict[str, object]] = []
    for field, replacement in (
        ("recorded_at_utc", "not-a-time"),
        ("signal_committed_this_invocation", 1),
        ("durable_prefix_length", 0),
        ("error", {"type": "RecoveryError", "message": ""}),
        ("error", {"type": "RecoveryError", "message": "fault", "extra": True}),
    ):
        changed = copy.deepcopy(valid)
        changed[field] = replacement
        mutations.append(changed)
    extra = copy.deepcopy(valid)
    extra["unbound"] = True
    mutations.append(extra)
    for changed in mutations:
        with pytest.raises(recovery.RecoveryError, match="failure lockout semantics"):
            recovery._validate_failure_document(changed)


def test_mutable_patched_builder_drift_blocks_post_keeper_and_terminal_evidence(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    chain = _chain()
    chain["control_inventory"] = {
        "control_parent": {}, "direct_children": [],
        "direct_child_count": 0, "files": {},
    }
    monkeypatch.setattr(recovery, "_keeper_state", lambda: "absent")
    monkeypatch.setattr(recovery, "_check_results_empty", lambda **_kwargs: {})
    monkeypatch.setattr(
        recovery, "_scan_processes",
        lambda **_kwargs: {"namespace_member_pids": [], "namespace_fd_handles": [], "suspicious_processes": []},
    )
    monkeypatch.setattr(recovery, "_capture_external_fixed_point", lambda: chain["external_baseline"])
    monkeypatch.setattr(
        recovery, "_validate_control_inventory",
        lambda: {**chain["control_inventory"], "documents": {}},
    )
    monkeypatch.setattr(
        recovery, "_exact_source_restoration_state",
        lambda: (_ for _ in ()).throw(recovery.RecoveryError("patched builder drift")),
    )
    with pytest.raises(recovery.RecoveryError, match="patched builder drift"):
        recovery._post_keeper_evidence(chain)


def test_control_inventory_rejects_real_role_artifact_name_before_file_reads(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    expected = set(recovery.CONTROL_DIRECTORIES) | set(recovery.EXPECTED_STATIC_CONTROL_FILES)
    for name in recovery.EXPECTED_CONTROL_JSONS:
        expected.update((name, name + ".sha256"))
    expected.add("namespace-role-handoff-v3-decoder-deadbeef.json")
    monkeypatch.setattr(
        recovery, "_directory_state",
        lambda _path: (sorted(expected), dict(recovery.EXPECTED_CONTROL_PARENT)),
    )
    with pytest.raises(recovery.RecoveryError, match="exact 34-entry"):
        recovery._validate_control_inventory()


def test_validate_chain_and_pre_signal_use_same_real_control_projection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression for the retry1 preflight's full-vs-projected composition bug."""

    baseline = {
        "inventory_sha256": "e" * 64,
        "namespaces": [],
        "ignored_empty_namespaces": [],
    }
    active = {
        "attempt_id": recovery.ATTEMPT_ID,
        "keeper": recovery.EXPECTED_KEEPER,
        "external_inventory_after": baseline,
    }
    sealed = {
        "recovery": {
            label: {
                "transaction_path": str(spec["transaction"]),
                "content_manifest_sha256": spec["content_manifest_sha256"],
            }
            for label, spec in recovery.TRANSACTION_SPECS.items()
        }
    }
    control_documents = {
        name: (
            active if name == recovery.ACTIVE_PATH.name
            else sealed if name == recovery.SEALED_PATH.name
            else {}
        )
        for name in recovery.EXPECTED_CONTROL_JSONS
    }
    expected_control_names = set(recovery.CONTROL_DIRECTORIES) | set(
        recovery.EXPECTED_STATIC_CONTROL_FILES
    )
    for name in recovery.EXPECTED_CONTROL_JSONS:
        expected_control_names.update((name, name + ".sha256"))
    control_directory_calls = 0

    def control_directory_state(path: Path):
        nonlocal control_directory_calls
        assert path == recovery.CONTROL_PARENT
        control_directory_calls += 1
        return sorted(expected_control_names), dict(recovery.EXPECTED_CONTROL_PARENT)

    monkeypatch.setattr(recovery, "_directory_state", control_directory_state)
    retry1_incident = _identity("retry1-preflight-incident", "9" * 64)
    monkeypatch.setattr(
        recovery, "_validate_retry1_failed_preflight_history", lambda: retry1_incident
    )

    identities = {
        key: _identity(key, str(index) * 64)
        for index, key in enumerate(
            (
                "incident", "rollback_recovery", "canonical_ready",
                "canonical_complete", "source_restoration", "candidate_open",
                "component_open", "stage1_review",
            ),
            start=1,
        )
    }
    raw_identities: dict[str, dict[str, object]] = {}

    def raw(path: Path, expected: dict[str, object], *, mode: int):
        identity = {
            "path": str(path), "size_bytes": expected["size_bytes"],
            "sha256": expected["sha256"], "st_dev": 64_512,
            "st_ino": expected.get("st_ino", len(raw_identities) + 100),
            "uid": expected.get("uid", 0), "gid": expected.get("gid", 0),
            "mode": expected.get("mode", mode), "nlink": 1,
        }
        raw_identities[str(path)] = identity
        return identity

    monkeypatch.setattr(recovery, "_read_exact_raw", raw)
    monkeypatch.setattr(
        recovery,
        "_read_regular",
        lambda path, _maximum=0: (
            b"static",
            {
                "path": str(path),
                "size_bytes": recovery.EXPECTED_STATIC_CONTROL_FILES[path.name][0],
                "sha256": recovery.EXPECTED_STATIC_CONTROL_FILES[path.name][1],
                "uid": 0, "gid": 0,
                "mode": recovery.EXPECTED_STATIC_CONTROL_FILES[path.name][2],
                "nlink": 1,
            },
        ),
    )
    monkeypatch.setattr(recovery, "CONTROL_TEST", SOURCE)
    rollback_runner = raw(
        recovery.ROLLBACK_RUNNER, recovery.EXPECTED_STAGE1["rollback_runner"], mode=0o555
    )
    rollback_test = raw(
        recovery.ROLLBACK_TEST, recovery.EXPECTED_STAGE1["rollback_test"], mode=0o555
    )
    patched = raw(
        Path(str(recovery.EXPECTED_STAGE1["patched_builder"]["path"])),
        recovery.EXPECTED_STAGE1["patched_builder"], mode=0o664,
    )
    historical = raw(
        Path(str(recovery.EXPECTED_STAGE1["historical_quarantine"]["path"])),
        recovery.EXPECTED_STAGE1["historical_quarantine"], mode=0o444,
    )
    abort_test = raw(
        SOURCE,
        {"size_bytes": SOURCE.stat().st_size, "sha256": "b" * 64},
        mode=0o555,
    )
    stage1_documents: dict[str, dict[str, object]] = {
        "incident": {
            "attempt_id": recovery.ATTEMPT_ID,
            "status": "COMMITTED_BEFORE_RECOVERY",
        },
        "canonical_ready": {"restored_roots": {}},
        "canonical_complete": {"status": "rollback_complete_project_unmounted"},
        "source_restoration": {
            "status": "PASS",
            "restored_patched_source": patched,
            "quarantined_projection": historical,
        },
        "candidate_open": {
            "schema_version": "blind-phase-confirmatory-fourth-rollback-parent-open-v1",
            "status": "PASS_HOST_PARENT_FD_OPENED_BEFORE_HISTORICAL_UNMOUNT",
            "attempt_id": recovery.ATTEMPT_ID,
            "incident": _identity("incident", "1" * 64),
            "open_evidence": {
                "excluded_child_mount_id": 169, "fdinfo_mount_id": 29,
                "historical_function": "rollback_sealed_runtimes", "historical_line": 4947,
                "label": "candidate",
                "opened_path": "/proc/1/root/home/ubuntu/telemetry-yield/releases/blind-phase-confirmatory-v2/runtime-r4",
                "requested_path": "/home/ubuntu/telemetry-yield/releases/blind-phase-confirmatory-v2/runtime-r4",
                "st_dev": 64_512, "st_ino": 2_129_580,
            },
        },
        "component_open": {
            "schema_version": "blind-phase-confirmatory-fourth-rollback-parent-open-v1",
            "status": "PASS_HOST_PARENT_FD_OPENED_BEFORE_HISTORICAL_UNMOUNT",
            "attempt_id": recovery.ATTEMPT_ID,
            "incident": _identity("incident", "1" * 64),
            "open_evidence": {
                "excluded_child_mount_id": 170, "fdinfo_mount_id": 29,
                "historical_function": "rollback_sealed_runtimes", "historical_line": 4947,
                "label": "component",
                "opened_path": "/proc/1/root/home/ubuntu/telemetry-yield/work/golden",
                "requested_path": "/home/ubuntu/telemetry-yield/work/golden",
                "st_dev": 64_512, "st_ino": 1_061_022,
            },
        },
        "stage1_review": {
            "schema_version": (
                "blind-phase-confirmatory-fourth-rollback-recovery-retry1-independent-review-v1"
            ),
            "status": "GO",
            "reviewed_bindings": {
                "recovery_runner_retry1": recovery._simple(rollback_runner),
                "recovery_runner_retry1_test": recovery._simple(rollback_test),
            },
        },
    }
    stage1_documents["rollback_recovery"] = {
        "status": "PASS",
        "incident": _identity("incident", "1" * 64),
        "canonical_rollback_ready": _identity("canonical_ready", "3" * 64),
        "canonical_rollback_complete": _identity("canonical_complete", "4" * 64),
        "source_restoration": _identity("source_restoration", "5" * 64),
        "redirected_parent_open_receipts": {
            "candidate": _identity("candidate_open", "6" * 64),
            "component": _identity("component_open", "7" * 64),
        },
        "patched_builder_restored": recovery._simple(patched),
        "post_rollback_mountinfo": recovery.EXPECTED_POST_ROLLBACK_MOUNTINFO,
    }

    def load_stage1(key: str, _hash_field: str):
        return copy.deepcopy(stage1_documents[key]), identities[key]

    monkeypatch.setattr(recovery, "_load_expected_stage1", load_stage1)
    runner = _identity(str(recovery.CONTROL_COPY), "a" * 64)
    stage2_review_identity = _identity(str(recovery.STAGE2_INDEPENDENT_REVIEW), "c" * 64)

    def load_review(path: Path, *_args, **_kwargs):
        if path.parent == recovery.CONTROL_PARENT and path.name in control_documents:
            digest = recovery.EXPECTED_CONTROL_JSONS[path.name][0]
            return copy.deepcopy(control_documents[path.name]), _identity(str(path), digest)
        assert path == recovery.STAGE2_INDEPENDENT_REVIEW
        return {
            "schema_version": (
                "blind-phase-confirmatory-fourth-pre-campaign-abort-retry2-independent-review-v1"
            ),
            "status": "GO",
            "reviewed_bindings": {
                "abort_runner_retry2": runner,
                "abort_runner_retry2_test": recovery._simple(abort_test),
                "retry1_preflight_failure_incident": retry1_incident,
            },
        }, stage2_review_identity

    monkeypatch.setattr(recovery, "_load_selfhashed", load_review)
    full_control = recovery._validate_control_inventory()
    assert full_control["documents"] == control_documents
    chain = recovery._validate_chain(_expected(), runner)
    monkeypatch.setattr(recovery, "_keeper_state", lambda: "present")
    monkeypatch.setattr(recovery, "_check_results_empty", lambda **_kwargs: {"empty": True})
    monkeypatch.setattr(recovery, "_validate_post_rollback_topology", lambda: {"exact": True})
    monkeypatch.setattr(
        recovery, "_scan_processes",
        lambda **_kwargs: {
            "namespace_member_pids": [recovery.EXPECTED_KEEPER["pid"]],
            "namespace_fd_handles": [], "suspicious_processes": [],
        },
    )
    monkeypatch.setattr(recovery, "_capture_external_fixed_point", lambda: baseline)
    monkeypatch.setattr(
        recovery, "_exact_source_restoration_state",
        lambda: {
            "patched_builder": recovery._simple(patched),
            "historical_quarantine": recovery._simple(historical),
        },
    )
    evidence = recovery._pre_signal_evidence(chain)
    assert control_directory_calls == 3
    assert "documents" not in chain["control_inventory"]
    assert evidence["control"] == chain["control_inventory"]


def test_retry1_failed_preflight_history_is_exactly_bound(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    incident_source = (
        SOURCE.parent
        / "fourth-pre-campaign-abort-retry1-preflight-failure-incident-v1.json"
    )
    incident = json.loads(incident_source.read_text(encoding="utf-8"))
    incident_identity = {
        **recovery.EXPECTED_RETRY1_PREFLIGHT_INCIDENT,
        "st_dev": 64_512, "st_ino": 8_888,
        "uid": 0, "gid": 0, "mode": 0o444, "nlink": 1,
    }
    installed = incident["retry1_install"]["artifacts"]
    old_review = {
        "schema_version": (
            "blind-phase-confirmatory-fourth-pre-campaign-abort-independent-review-v1"
        ),
        "status": "GO",
        "reviewed_bindings": {
            "abort_runner": recovery._simple(installed["abort_runner"]),
            "abort_runner_test": recovery._simple(installed["abort_runner_test"]),
        },
    }
    monkeypatch.setattr(
        recovery, "_directory_state",
        lambda path: (
            sorted(recovery.EXPECTED_RETRY1_PARENT_NAMES),
            {**recovery.EXPECTED_RETRY1_PARENT, "entry_count": 22},
        ),
    )
    monkeypatch.setattr(
        recovery, "_read_exact_raw",
        lambda path, expected, **_kwargs: {
            **expected, "path": str(path), "st_dev": 64_512, "nlink": 1,
        },
    )
    monkeypatch.setattr(
        recovery, "_read_regular",
        lambda path, _maximum=0: (
            b"sidecar",
            installed["independent_review_sidecar"],
        ),
    )

    def load(path: Path, *_args, **_kwargs):
        if path == recovery.RETRY1_PREFLIGHT_INCIDENT_PATH:
            return copy.deepcopy(incident), incident_identity
        return old_review, installed["independent_review"]

    monkeypatch.setattr(recovery, "_load_selfhashed", load)
    assert recovery._validate_retry1_failed_preflight_history() == recovery._simple(
        incident_identity
    )
    incident["failure_cause"]["mutation_reached"] = True
    with pytest.raises(recovery.RecoveryError, match="incident semantics differ"):
        recovery._validate_retry1_failed_preflight_history()


def test_retry1_preflight_failure_incident_repo_bytes_and_selfhash_are_frozen() -> None:
    path = (
        SOURCE.parent
        / "fourth-pre-campaign-abort-retry1-preflight-failure-incident-v1.json"
    )
    payload = path.read_bytes()
    assert len(payload) == recovery.EXPECTED_RETRY1_PREFLIGHT_INCIDENT["size_bytes"]
    assert recovery._sha256(payload) == recovery.EXPECTED_RETRY1_PREFLIGHT_INCIDENT["sha256"]
    document = json.loads(payload)
    stored = document.pop("incident_payload_sha256")
    assert stored == recovery._sha256_document(document)
    assert path.with_name(path.name + ".sha256").read_bytes() == (
        f"{recovery.EXPECTED_RETRY1_PREFLIGHT_INCIDENT['sha256']}  {path.name}\n".encode()
    )


def test_recovery_parent_inventory_models_only_expected_tombstone_phase(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_stage_paths(monkeypatch, tmp_path)
    monkeypatch.setattr(recovery, "RECOVERY_PARENT", tmp_path)
    monkeypatch.setattr(recovery, "EXPECTED_RECOVERY_BASE_NAMES", set())
    candidate = tmp_path / "candidate-tombstone"
    component = tmp_path / "component-tombstone"
    specifications = {
        "candidate": {"tombstone": candidate},
        "component": {"tombstone": component},
    }
    monkeypatch.setattr(recovery, "TRANSACTION_SPECS", specifications)

    def snapshot(_path: Path):
        names = sorted(path.name for path in tmp_path.iterdir())
        parent = {
            **_expected_parent(tmp_path),
            "nlink": 2 + sum(path.is_dir() for path in tmp_path.iterdir()),
            "entry_count": len(names),
        }
        return names, parent

    monkeypatch.setattr(recovery, "_directory_state", snapshot)
    for path, _ in recovery.STAGE2_ORDERED_ARTIFACTS[:3]:
        path.write_text("main")
        path.with_name(path.name + ".sha256").write_text("sidecar")
    candidate.mkdir()
    recovery._validate_recovery_parent_inventory(
        3, failure_present=False, expected_parent=_expected_parent(tmp_path)
    )
    with pytest.raises(recovery.RecoveryError, match="tombstone"):
        recovery._validate_recovery_parent_inventory(
            2, failure_present=False, expected_parent=_expected_parent(tmp_path)
        )


def test_recovery_parent_inventory_rejects_foreign_temp(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_stage_paths(monkeypatch, tmp_path)
    monkeypatch.setattr(recovery, "RECOVERY_PARENT", tmp_path)
    monkeypatch.setattr(recovery, "EXPECTED_RECOVERY_BASE_NAMES", set())
    monkeypatch.setattr(recovery, "TRANSACTION_SPECS", {
        "candidate": {"tombstone": tmp_path / "candidate-tombstone"},
        "component": {"tombstone": tmp_path / "component-tombstone"},
    })
    (tmp_path / ".foreign.tmp").write_text("x")

    def snapshot(_path: Path):
        names = [".foreign.tmp"]
        return names, {**_expected_parent(tmp_path), "nlink": 2, "entry_count": 1}

    monkeypatch.setattr(recovery, "_directory_state", snapshot)
    with pytest.raises(recovery.RecoveryError, match="unexpected"):
        recovery._validate_recovery_parent_inventory(
            0, failure_present=False, expected_parent=_expected_parent(tmp_path)
        )


def test_cleanup_intent_rejects_mutated_manifest_binding() -> None:
    stopped = _identity("stopped")
    chain = _chain()
    chain["ready_document"] = {"restored_roots": {"candidate": {}, "component": {}}}
    document = {
        "schema_version": "blind-phase-confirmatory-fourth-transaction-cleanup-intent-v1",
        "status": "COMMITTED_BEFORE_IRREVERSIBLE_DUPLICATE_DELETION",
        "attempt_id": recovery.ATTEMPT_ID,
        "abort_stopped": stopped,
        "transaction_directories": {},
        "restored_roots_before_cleanup": {},
        "transaction_parents": {},
        "authorized_exact_targets": [],
        "authorized_exact_tombstones": [],
    }
    with pytest.raises(recovery.RecoveryError, match="cleanup intent"):
        recovery._validate_cleanup_intent(
            document, stopped, chain, _expected_parent()
        )


def _exact_cleanup_intent_fixture() -> tuple[
    dict[str, object], dict[str, object], dict[str, object]
]:
    stopped = _identity("stopped")
    chain = _chain()
    sealed_recovery: dict[str, object] = {}
    transactions: dict[str, object] = {}
    roots: dict[str, object] = {}
    parents: dict[str, object] = {}
    for label, spec in recovery.TRANSACTION_SPECS.items():
        backup = {"path": f"{label}/backup", "manifest": f"{label}-backup"}
        superseded = {"path": f"{label}/superseded", "manifest": f"{label}-superseded"}
        quarantined_hash = ("1" if label == "candidate" else "2") * 64
        sealed_recovery[label] = {
            "backup": backup,
            "superseded": superseded,
            "quarantined_original": {
                "logical_manifest_sha256_at_swap": quarantined_hash,
            },
        }
        transactions[label] = {
            "tree": {
                "path": str(spec["transaction"]),
                "entry_count": int(spec["entry_count_including_transaction_root"]) - 1,
                "allocated_bytes": spec["allocated_bytes"],
                **recovery.EXPECTED_TRANSACTION_ROOTS[label],
            },
            "members": {
                "backup": backup,
                "superseded-original": superseded,
                "quarantined-original-hardlinks": {
                    "inventory_manifest_sha256": quarantined_hash,
                },
                "failed-full-validation-root": {
                    "content_manifest_sha256": spec["content_manifest_sha256"],
                },
            },
            "tombstone_path": str(spec["tombstone"]),
        }
        roots[label] = {"path": str(spec["root"]), "label": label}
        parents[label] = {
            "source": dict(recovery.EXPECTED_CLEANUP_SOURCE_PARENTS[label]),
            "quarantine": {
                **_expected_parent(),
            },
        }
    chain["sealed_document"] = {"recovery": sealed_recovery}
    chain["ready_document"] = {"restored_roots": roots}
    document = {
        "schema_version": "blind-phase-confirmatory-fourth-transaction-cleanup-intent-v1",
        "status": "COMMITTED_BEFORE_IRREVERSIBLE_DUPLICATE_DELETION",
        "attempt_id": recovery.ATTEMPT_ID,
        "recorded_at_utc": "2026-09-06T00:00:00Z",
        "abort_stopped": stopped,
        "transaction_directories": transactions,
        "restored_roots_before_cleanup": roots,
        "transaction_parents": parents,
        "authorized_exact_targets": [
            str(recovery.TRANSACTION_SPECS[label]["transaction"])
            for label in ("candidate", "component")
        ],
        "authorized_exact_tombstones": [
            str(recovery.TRANSACTION_SPECS[label]["tombstone"])
            for label in ("candidate", "component")
        ],
        "expected_total_allocated_bytes": sum(
            int(recovery.TRANSACTION_SPECS[label]["allocated_bytes"])
            for label in ("candidate", "component")
        ),
        "irreversible_deletion_disclosure": (
            "operational runtime duplicates only; no IQ, source, results, or canonical reports"
        ),
    }
    return document, stopped, chain


@pytest.mark.parametrize("label", ["candidate", "component"])
def test_cleanup_intent_rejects_replacement_historical_parent_inode(label: str) -> None:
    document, stopped, chain = _exact_cleanup_intent_fixture()
    recovery._validate_cleanup_intent(document, stopped, chain, _expected_parent())
    replaced = copy.deepcopy(document)
    replaced["transaction_parents"][label]["source"]["st_ino"] += 1
    with pytest.raises(recovery.RecoveryError, match="cleanup intent binding differs"):
        recovery._validate_cleanup_intent(replaced, stopped, chain, _expected_parent())


@pytest.mark.parametrize("label", ["candidate", "component"])
def test_cleanup_intent_rejects_replacement_transaction_root_inode(label: str) -> None:
    document, stopped, chain = _exact_cleanup_intent_fixture()
    recovery._validate_cleanup_intent(document, stopped, chain, _expected_parent())
    replaced = copy.deepcopy(document)
    replaced["transaction_directories"][label]["tree"]["st_ino"] += 1
    with pytest.raises(recovery.RecoveryError, match="cleanup intent binding differs"):
        recovery._validate_cleanup_intent(replaced, stopped, chain, _expected_parent())


def test_source_contains_no_normal_stop_or_manual_unmount_path() -> None:
    source = SOURCE.read_text(encoding="utf-8")
    assert "exec-stop" not in source
    assert "SIGKILL" in source
    assert "signal.SIGKILL" not in source
    assert "os.umount" not in source
    assert "umount2" not in source
