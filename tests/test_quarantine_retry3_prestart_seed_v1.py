from __future__ import annotations

import ast
import copy
import errno
import importlib.util
import json
import os
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "work/blind-phase-confirmatory-v2/quarantine_retry3_prestart_seed_v1.py"
SPEC = importlib.util.spec_from_file_location("quarantine_retry3_prestart_seed_v1", SOURCE)
assert SPEC and SPEC.loader
RUNNER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RUNNER)


def _identity(path: Path, marker: str = "a") -> dict[str, object]:
    return {"path": str(path), "size_bytes": 1, "sha256": marker * 64}


def _snapshot(path: Path = RUNNER.SOURCE) -> dict[str, object]:
    entries = []
    for name, spec in sorted(RUNNER.SEED_SPECS.items(), key=lambda item: os.fsencode(item[0])):
        entries.append(
            {
                "path": str(path / name), "st_dev": 64512,
                "st_ino": spec["st_ino"], "uid": 0, "gid": 0,
                "mode": spec["mode"], "nlink": 1,
                "size_bytes": spec["size_bytes"], "sha256": spec["sha256"],
            }
        )
    value = {
        "root": {**RUNNER.SOURCE_IDENTITY, "path": str(path)},
        "entry_count": 4, "entries": entries,
        "results_directory_present": False, "lifecycle_artifact_count": 0,
    }
    value["snapshot_sha256"] = RUNNER._sha256_document(value)
    return value


def _zero() -> dict[str, object]:
    return {
        "keeper_absent": True,
        "relevant_mount_count": 0,
        "role_decoder_evaluator_processes": {
            "namespace_member_pids": [], "namespace_fd_handles": [],
            "suspicious_processes": [],
        },
        "external_inventory_sha256": RUNNER.EXTERNAL_SHA256,
        "iq_content_opened": False,
        "scientific_outcome_generated": False,
    }


def _expected() -> dict[str, object]:
    return {
        "runner_sha256": "1" * 64,
        "test_sha256": "2" * 64,
        "review_sha256": "3" * 64,
        "controller_st_dev": 64512,
        "controller_st_ino": 42,
        "var_lib_st_dev": 64512,
        "var_lib_st_ino": 1179656,
    }


def _install_temp_seed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> tuple[Path, Path, dict[str, object]]:
    parent = tmp_path / "var-lib"
    source = parent / "source"
    destination = parent / "destination"
    parent.mkdir()
    source.mkdir()
    payloads = {
        "amendment-v3.tsq": b"query",
        "amendment-v3.tsr": b"response",
        "build_runtime_guard_v3.py": b"builder",
        "tsa-ca-certificates.pem": b"certificate",
    }
    specs: dict[str, dict[str, object]] = {}
    for name, payload in payloads.items():
        path = source / name
        path.write_bytes(payload)
        path.chmod(0o444)
        status = path.lstat()
        specs[name] = {
            "st_ino": status.st_ino,
            "size_bytes": len(payload),
            "mode": 0o444,
            "sha256": RUNNER._sha256(payload),
        }
    root = source.lstat()
    parent_status = parent.lstat()
    source_identity = {
        "path": str(source), "st_dev": root.st_dev, "st_ino": root.st_ino,
        "uid": root.st_uid, "gid": root.st_gid,
        "mode": root.st_mode & 0o777, "nlink": root.st_nlink,
    }
    expected_parent = {
        "path": str(parent), "st_dev": parent_status.st_dev,
        "st_ino": parent_status.st_ino, "uid": parent_status.st_uid,
        "gid": parent_status.st_gid, "mode": parent_status.st_mode & 0o777,
    }
    monkeypatch.setattr(RUNNER, "VAR_LIB", parent)
    monkeypatch.setattr(RUNNER, "SOURCE", source)
    monkeypatch.setattr(RUNNER, "DESTINATION", destination)
    monkeypatch.setattr(RUNNER, "SOURCE_IDENTITY", source_identity)
    monkeypatch.setattr(RUNNER, "SEED_SPECS", specs)
    return source, destination, expected_parent


def _install_fake_lifecycle(
    monkeypatch: pytest.MonkeyPatch, *, initial_state: str = "SOURCE_CANONICAL"
) -> tuple[list[str], dict[str, object], dict[Path, tuple[dict[str, object], dict[str, object]]]]:
    events: list[str] = []
    state: dict[str, object] = {"root": initial_state, "presence": {}}
    stored: dict[Path, tuple[dict[str, object], dict[str, object]]] = {}
    before = _snapshot()
    stage2 = {"durable_prefix_length": 9, "result": _identity(Path("/stage2"), "9")}

    monkeypatch.setattr(
        RUNNER,
        "_controller_inventory",
        lambda _parent, intent, provenance: (
            RUNNER._expected_controller_names(intent, provenance), {}
        )[1],
    )
    monkeypatch.setattr(
        RUNNER,
        "_toolchain",
        lambda *_args, **_kwargs: {"runner": _identity(RUNNER.RUNNER)},
    )
    monkeypatch.setattr(
        RUNNER,
        "_evidence_chain",
        lambda: {"evidence": _identity(RUNNER.EVIDENCE, "e")},
    )
    monkeypatch.setattr(
        RUNNER,
        "_stage2",
        lambda: (object(), stage2, _identity(RUNNER.HISTORICAL_STAGE2_AUTHORITY, "8")),
    )
    monkeypatch.setattr(RUNNER, "_zero_state", lambda _module: copy.deepcopy(_zero()))
    monkeypatch.setattr(RUNNER, "_root_state", lambda _expected: str(state["root"]))
    monkeypatch.setattr(
        RUNNER, "_snapshot_seed", lambda _root, _expected: copy.deepcopy(before)
    )
    monkeypatch.setattr(
        RUNNER,
        "_validate_snapshot",
        lambda snapshot, root, _expected: RUNNER._project_snapshot(snapshot, root),
    )
    monkeypatch.setattr(RUNNER.BASE, "_validate_pair_fragments", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        RUNNER,
        "_pair_presence",
        lambda path: state["presence"].get(
            path, (True, True) if path in stored else (False, False)
        ),
    )

    def publish(path: Path, document: dict[str, object], field: str, _parent: dict[str, object]) -> dict[str, object]:
        events.append(f"publish:{path.name}")
        final = copy.deepcopy(document)
        final[field] = RUNNER._sha256_document(document)
        identity = _identity(path, "a" if path == RUNNER.INTENT else "b")
        stored[path] = (final, identity)
        state["presence"][path] = (True, True)
        return identity

    def load(path: Path, *_args: object) -> tuple[dict[str, object], dict[str, object]]:
        return copy.deepcopy(stored[path])

    def rename(_expected_parent: dict[str, object]) -> None:
        assert RUNNER.INTENT in stored
        events.append("rename")
        state["root"] = "DESTINATION_QUARANTINED"

    monkeypatch.setattr(RUNNER, "_publish", publish)
    monkeypatch.setattr(RUNNER, "_load_committed", load)
    monkeypatch.setattr(RUNNER, "_rename", rename)
    monkeypatch.setattr(
        RUNNER,
        "_durably_validate_destination",
        lambda _expected, *, commit_durability: events.append("fsync-destination")
        if commit_durability
        else None,
    )
    return events, state, stored


def test_source_compiles_and_executable_refreeze_binds_final_evidence() -> None:
    compile(SOURCE.read_bytes(), str(SOURCE), "exec")
    assert RUNNER.QUARANTINE_TEMPLATE_BLOCKED_PENDING_EVIDENCE_AND_REVIEW is False
    assert RUNNER.EVIDENCE_SHA256 == "5c900c7ddd41137833b30fdc1085bc986cd01013ffb22b214a147f8d15c00a80"
    assert RUNNER.EVIDENCE_PAYLOAD_SHA256 == "fb04518187837a141b20b91952c49a54e2d29f4eb7b0eda139fdfb77675d4cbd"
    assert RUNNER.EVIDENCE_REVIEW_SHA256 == "60279f5a9f138769b8c6bcdc32c6736661e724899ac1984549ee6dd202862e75"
    assert RUNNER.EVIDENCE_REVIEW_PAYLOAD_SHA256 == "801b708c58a4010e50e104f103195080faa5f761c76f6d24002c7ce8c30dc2eb"
    assert RUNNER.EVIDENCE_SIDECAR_SHA256 == "451de18353f44ddafd72746d39c494265bf08eed1e61a177b045ea8e45a400a8"
    assert RUNNER.EVIDENCE_REVIEW_SIDECAR_SHA256 == "eccf94596eeade846c1fdab73929b6dcc732ac15285d83d220dc6a7ce9f4271c"


def test_full64_external_sibling_paths_are_exact() -> None:
    assert len(RUNNER.ATTEMPT_ID) == 64
    assert RUNNER.SOURCE.parent == RUNNER.DESTINATION.parent == RUNNER.CONTROLLER.parent
    assert RUNNER.SOURCE not in {RUNNER.DESTINATION, RUNNER.CONTROLLER}
    assert RUNNER.DESTINATION != RUNNER.CONTROLLER
    assert RUNNER.ATTEMPT_ID in RUNNER.DESTINATION.name
    assert RUNNER.ATTEMPT_ID in RUNNER.CONTROLLER.name


def test_live_seed_read_only_snapshot_is_exact4() -> None:
    snapshot = RUNNER._snapshot_seed(RUNNER.SOURCE)
    assert snapshot["root"] == RUNNER.SOURCE_IDENTITY
    assert snapshot["entry_count"] == 4
    assert snapshot["results_directory_present"] is False
    assert snapshot["lifecycle_artifact_count"] == 0
    assert {Path(item["path"]).name for item in snapshot["entries"]} == set(RUNNER.SEED_SPECS)
    assert snapshot["snapshot_sha256"] == RUNNER._sha256_document(
        {key: value for key, value in snapshot.items() if key != "snapshot_sha256"}
    )


def test_snapshot_projection_changes_only_rooted_paths_and_hash() -> None:
    before = _snapshot()
    after = RUNNER._project_snapshot(before, RUNNER.DESTINATION)
    assert after["root"]["st_ino"] == before["root"]["st_ino"]
    assert after["root"]["path"] == str(RUNNER.DESTINATION)
    assert all(Path(item["path"]).parent == RUNNER.DESTINATION for item in after["entries"])
    assert after["snapshot_sha256"] != before["snapshot_sha256"]


def test_root_state_rejects_both_neither_symlink_and_wrong_type(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    source, destination, expected_parent = _install_temp_seed(monkeypatch, tmp_path)
    assert RUNNER._root_state(expected_parent) == "SOURCE_CANONICAL"

    destination.mkdir()
    with pytest.raises(RUNNER.QuarantineError, match="ambiguous"):
        RUNNER._root_state(expected_parent)
    destination.rmdir()

    hidden = source.with_name("hidden")
    source.rename(hidden)
    with pytest.raises(RUNNER.QuarantineError, match="ambiguous"):
        RUNNER._root_state(expected_parent)

    source.symlink_to(hidden, target_is_directory=True)
    with pytest.raises(RUNNER.QuarantineError, match="not an exact directory"):
        RUNNER._root_state(expected_parent)
    source.unlink()

    source.write_bytes(b"not-a-directory")
    with pytest.raises(RUNNER.QuarantineError, match="not an exact directory"):
        RUNNER._root_state(expected_parent)


def test_atomic_rename_preserves_exact_inode_and_is_no_clobber(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    source, destination, expected_parent = _install_temp_seed(monkeypatch, tmp_path)
    source_inode = source.stat().st_ino
    RUNNER._rename(expected_parent)
    assert not source.exists()
    assert destination.stat().st_ino == source_inode


def test_atomic_rename_refuses_existing_destination_and_retains_source(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    source, destination, expected_parent = _install_temp_seed(monkeypatch, tmp_path)
    destination.mkdir()
    source_inode = source.stat().st_ino
    destination_inode = destination.stat().st_ino
    with pytest.raises(OSError) as caught:
        RUNNER._rename(expected_parent)
    assert caught.value.errno == errno.EEXIST
    assert source.stat().st_ino == source_inode
    assert destination.stat().st_ino == destination_inode


@pytest.mark.parametrize(
    "mutation",
    ["extra", "missing", "symlink", "hardlink", "hash", "mode", "inode"],
)
def test_snapshot_seed_rejects_exact4_member_mutations(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, mutation: str,
) -> None:
    source, _destination, expected_parent = _install_temp_seed(monkeypatch, tmp_path)
    baseline = RUNNER._snapshot_seed(source, expected_parent)
    assert baseline["entry_count"] == 4
    name = sorted(RUNNER.SEED_SPECS)[0]
    member = source / name
    original = member.read_bytes()
    if mutation == "extra":
        (source / "foreign").write_bytes(b"x")
    elif mutation == "missing":
        member.unlink()
    elif mutation == "symlink":
        outside = source.parent / "outside"
        outside.write_bytes(original)
        member.unlink()
        member.symlink_to(outside)
    elif mutation == "hardlink":
        os.link(member, source.parent / "alias")
    elif mutation == "hash":
        member.chmod(0o644)
        member.write_bytes(b"X" * len(original))
        member.chmod(0o444)
    elif mutation == "mode":
        member.chmod(0o400)
    elif mutation == "inode":
        replacement = source / ".replacement"
        replacement.write_bytes(original)
        replacement.chmod(0o444)
        assert replacement.stat().st_ino != member.stat().st_ino
        member.unlink()
        replacement.rename(member)
    with pytest.raises((RUNNER.QuarantineError, OSError)):
        RUNNER._snapshot_seed(source, expected_parent)


def test_snapshot_seed_rejects_final_enumeration_race(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    source, _destination, expected_parent = _install_temp_seed(monkeypatch, tmp_path)
    real_listdir = RUNNER.os.listdir
    calls = 0

    def racing_listdir(path: object) -> list[str]:
        nonlocal calls
        calls += 1
        result = list(real_listdir(path))
        return result if calls == 1 else result + ["foreign-after-scan"]

    monkeypatch.setattr(RUNNER.os, "listdir", racing_listdir)
    with pytest.raises(RUNNER.QuarantineError, match="inventory changed"):
        RUNNER._snapshot_seed(source, expected_parent)


@pytest.mark.parametrize(
    ("intent", "provenance"),
    [
        ((False, False), (False, False)),
        ((False, True), (False, False)),
        ((True, True), (False, False)),
        ((True, True), (False, True)),
        ((True, True), (True, True)),
    ],
)
def test_controller_presence_grammar_accepts_only_crash_prefixes(
    intent: tuple[bool, bool], provenance: tuple[bool, bool]
) -> None:
    names = RUNNER._expected_controller_names(intent, provenance)
    assert RUNNER.RUNNER.name in names
    assert RUNNER.LOCK.name in names


@pytest.mark.parametrize(
    ("intent", "provenance"),
    [
        ((True, False), (False, False)),
        ((False, False), (False, True)),
        ((False, True), (False, True)),
        ((True, True), (True, False)),
    ],
)
def test_controller_presence_grammar_rejects_impossible_states(
    intent: tuple[bool, bool], provenance: tuple[bool, bool]
) -> None:
    with pytest.raises(RUNNER.QuarantineError):
        RUNNER._expected_controller_names(intent, provenance)


def test_controller_inventory_is_parent_pinned_and_rejects_foreign_name(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    controller = tmp_path / "controller"
    controller.mkdir()
    runner = controller / "runner.py"
    test = controller / "test.py"
    review = controller / "review.json"
    lock = controller / "lock"
    for path in (runner, test, review, review.with_name(review.name + ".sha256"), lock):
        path.write_bytes(b"x")
    monkeypatch.setattr(RUNNER, "CONTROLLER", controller)
    monkeypatch.setattr(RUNNER, "RUNNER", runner)
    monkeypatch.setattr(RUNNER, "RUNNER_TEST", test)
    monkeypatch.setattr(RUNNER, "REVIEW", review)
    monkeypatch.setattr(RUNNER, "LOCK", lock)
    monkeypatch.setattr(RUNNER, "INTENT", controller / "intent.json")
    monkeypatch.setattr(RUNNER, "PROVENANCE", controller / "provenance.json")
    status = controller.lstat()
    expected = {
        "path": str(controller), "st_dev": status.st_dev, "st_ino": status.st_ino,
        "uid": status.st_uid, "gid": status.st_gid,
        "mode": status.st_mode & 0o777, "nlink": status.st_nlink,
    }
    assert set(RUNNER._controller_inventory(expected, (False, False), (False, False))) == {
        path.name for path in (runner, test, review, review.with_name(review.name + ".sha256"), lock)
    }
    (controller / "foreign").write_bytes(b"x")
    with pytest.raises(RUNNER.QuarantineError, match="inventory differs"):
        RUNNER._controller_inventory(expected, (False, False), (False, False))


@pytest.mark.skipif(os.geteuid() != 0, reason="root-only O_TMPFILE publication metadata")
def test_parent_pinned_publication_resumes_sidecar_only_and_is_idempotent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    controller = tmp_path / "controller"
    controller.mkdir()
    controller.chmod(0o755)
    runner = controller / "runner.py"
    test = controller / "test.py"
    review = controller / "review.json"
    lock = controller / "lock"
    for path, mode, payload in (
        (runner, 0o555, b"runner"), (test, 0o555, b"test"),
        (review, 0o444, b"review"),
        (review.with_name(review.name + ".sha256"), 0o444, b"review-sidecar"),
        (lock, 0o600, b""),
    ):
        path.write_bytes(payload)
        path.chmod(mode)
    intent = controller / "intent.json"
    provenance = controller / "provenance.json"
    monkeypatch.setattr(RUNNER, "CONTROLLER", controller)
    monkeypatch.setattr(RUNNER, "RUNNER", runner)
    monkeypatch.setattr(RUNNER, "RUNNER_TEST", test)
    monkeypatch.setattr(RUNNER, "REVIEW", review)
    monkeypatch.setattr(RUNNER, "LOCK", lock)
    monkeypatch.setattr(RUNNER, "INTENT", intent)
    monkeypatch.setattr(RUNNER, "PROVENANCE", provenance)
    status = controller.lstat()
    expected_parent = {
        "path": str(controller), "st_dev": status.st_dev,
        "st_ino": status.st_ino, "uid": 0, "gid": 0,
        "mode": 0o755, "nlink": status.st_nlink,
    }
    document = {
        "schema_version": RUNNER.INTENT_SCHEMA,
        "status": RUNNER.INTENT_STATUS,
        "attempt_id": RUNNER.ATTEMPT_ID,
    }
    original = RUNNER.BASE._publish_one
    calls = 0

    def crash_after_sidecar(directory_fd: int, name: str, payload: bytes) -> None:
        nonlocal calls
        calls += 1
        original(directory_fd, name, payload)
        if calls == 1:
            raise RuntimeError("after sidecar commit")

    monkeypatch.setattr(RUNNER.BASE, "_publish_one", crash_after_sidecar)
    with pytest.raises(RuntimeError, match="after sidecar commit"):
        RUNNER._publish(intent, document, RUNNER.INTENT_HASH_FIELD, expected_parent)
    assert RUNNER._pair_presence(intent) == (False, True)
    monkeypatch.setattr(RUNNER.BASE, "_publish_one", original)
    identity = RUNNER._publish(
        intent, document, RUNNER.INTENT_HASH_FIELD, expected_parent
    )
    before = {
        path.name: (path.lstat().st_ino, path.read_bytes())
        for path in (intent, intent.with_name(intent.name + ".sha256"))
    }
    assert identity["path"] == str(intent)
    RUNNER._publish(intent, document, RUNNER.INTENT_HASH_FIELD, expected_parent)
    after = {
        path.name: (path.lstat().st_ino, path.read_bytes())
        for path in (intent, intent.with_name(intent.name + ".sha256"))
    }
    assert after == before


def test_publication_rejects_controller_swap_before_any_fragment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    controller = tmp_path / "controller"
    controller.mkdir()
    replacement = tmp_path / "replacement"
    replacement.mkdir()
    original_status = controller.lstat()
    expected_parent = {
        "path": str(controller), "st_dev": original_status.st_dev,
        "st_ino": original_status.st_ino, "uid": original_status.st_uid,
        "gid": original_status.st_gid,
        "mode": original_status.st_mode & 0o777,
        "nlink": original_status.st_nlink,
    }
    monkeypatch.setattr(RUNNER, "CONTROLLER", controller)
    monkeypatch.setattr(RUNNER, "INTENT", controller / "intent.json")
    monkeypatch.setattr(RUNNER, "PROVENANCE", controller / "provenance.json")
    real_open = RUNNER.os.open
    swapped = False

    def swapping_open(path: object, *args: object, **kwargs: object) -> int:
        nonlocal swapped
        if not swapped and Path(path) == controller:
            swapped = True
            controller.rename(tmp_path / "original")
            replacement.rename(controller)
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(RUNNER.os, "open", swapping_open)
    monkeypatch.setattr(
        RUNNER.BASE, "_publish_one",
        lambda *_args, **_kwargs: pytest.fail("publication preceded parent pin"),
    )
    with pytest.raises(RUNNER.QuarantineError, match="identity differs before publication"):
        RUNNER._publish(
            RUNNER.INTENT,
            {"schema_version": RUNNER.INTENT_SCHEMA, "status": RUNNER.INTENT_STATUS},
            RUNNER.INTENT_HASH_FIELD,
            expected_parent,
        )
    assert swapped is True
    assert list(controller.iterdir()) == []


def test_fresh_preflight_is_read_only_and_does_not_publish_or_rename(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events, _state, _stored = _install_fake_lifecycle(monkeypatch)
    result = RUNNER.quarantine(
        expected=_expected(), created_at_utc="2026-09-06T17:00:00Z",
        completed_at_utc="2026-09-06T17:01:00Z", preflight_only=True,
    )
    assert result["status"] == "PASS_READ_ONLY_NO_PUBLICATION_NO_RENAME"
    assert result["durable_prefix_length"] == 0
    assert events == []


def test_execution_commits_intent_before_atomic_rename_and_provenance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events, state, stored = _install_fake_lifecycle(monkeypatch)
    result = RUNNER.quarantine(
        expected=_expected(), created_at_utc="2026-09-06T17:00:00Z",
        completed_at_utc="2026-09-06T17:01:00Z", preflight_only=False,
    )
    assert events == [
        f"publish:{RUNNER.INTENT.name}", "rename", "fsync-destination",
        f"publish:{RUNNER.PROVENANCE.name}",
    ]
    assert state["root"] == "DESTINATION_QUARANTINED"
    assert RUNNER.INTENT in stored and RUNNER.PROVENANCE in stored
    assert result["status"] == RUNNER.PROVENANCE_STATUS


def test_resume_after_rename_uses_committed_intent_without_second_rename(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events, state, stored = _install_fake_lifecycle(monkeypatch)
    original_rename = RUNNER._rename

    def crash_after_rename(expected: dict[str, object]) -> None:
        original_rename(expected)
        raise RuntimeError("injected crash after rename")

    monkeypatch.setattr(RUNNER, "_rename", crash_after_rename)
    with pytest.raises(RuntimeError, match="injected crash"):
        RUNNER.quarantine(
            expected=_expected(), created_at_utc="2026-09-06T17:00:00Z",
            completed_at_utc="2026-09-06T17:01:00Z", preflight_only=False,
        )
    assert state["root"] == "DESTINATION_QUARANTINED"
    assert RUNNER.INTENT in stored and RUNNER.PROVENANCE not in stored
    monkeypatch.setattr(RUNNER, "_rename", lambda _expected: pytest.fail("resume renamed twice"))
    result = RUNNER.quarantine(
        expected=_expected(), created_at_utc="2026-09-06T17:00:00Z",
        completed_at_utc="2026-09-06T17:01:00Z", preflight_only=False,
    )
    assert result["status"] == RUNNER.PROVENANCE_STATUS
    assert events.count("rename") == 1
    assert events.count("fsync-destination") == 1


def test_terminal_preflight_validates_pair_without_publication_or_rename(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events, _state, _stored = _install_fake_lifecycle(monkeypatch)
    RUNNER.quarantine(
        expected=_expected(), created_at_utc="2026-09-06T17:00:00Z",
        completed_at_utc="2026-09-06T17:01:00Z", preflight_only=False,
    )
    baseline = list(events)
    monkeypatch.setattr(RUNNER, "_rename", lambda _expected: pytest.fail("terminal preflight renamed"))
    result = RUNNER.quarantine(
        expected=_expected(), created_at_utc="2026-09-06T17:00:00Z",
        completed_at_utc="2026-09-06T17:01:00Z", preflight_only=True,
    )
    assert result["durable_prefix_length"] == 2
    assert result["status"].startswith("PASS_READ_ONLY_TERMINAL")
    assert events == baseline


def test_destination_resume_is_read_only_in_preflight_and_fsyncs_for_commit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    destination = tmp_path / "destination"
    destination.mkdir()
    source = tmp_path / "source"
    parent_status = tmp_path.lstat()
    destination_status = destination.lstat()
    expected_parent = {
        "path": str(tmp_path), "st_dev": parent_status.st_dev,
        "st_ino": parent_status.st_ino, "uid": parent_status.st_uid,
        "gid": parent_status.st_gid, "mode": parent_status.st_mode & 0o777,
    }
    monkeypatch.setattr(RUNNER, "VAR_LIB", tmp_path)
    monkeypatch.setattr(RUNNER, "SOURCE", source)
    monkeypatch.setattr(RUNNER, "DESTINATION", destination)
    monkeypatch.setattr(
        RUNNER,
        "SOURCE_IDENTITY",
        {
            "path": str(source), "st_dev": destination_status.st_dev,
            "st_ino": destination_status.st_ino, "uid": destination_status.st_uid,
            "gid": destination_status.st_gid,
            "mode": destination_status.st_mode & 0o777,
            "nlink": destination_status.st_nlink,
        },
    )
    calls: list[int] = []
    real_fsync = RUNNER.os.fsync
    monkeypatch.setattr(RUNNER.os, "fsync", lambda descriptor: calls.append(descriptor))
    RUNNER._durably_validate_destination(expected_parent, commit_durability=False)
    assert calls == []
    RUNNER._durably_validate_destination(expected_parent, commit_durability=True)
    assert len(calls) == 1
    monkeypatch.setattr(RUNNER.os, "fsync", real_fsync)


def test_intent_sidecar_only_preflight_validates_without_repair(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events, state, _stored = _install_fake_lifecycle(monkeypatch)
    state["presence"][RUNNER.INTENT] = (False, True)
    result = RUNNER.quarantine(
        expected=_expected(), created_at_utc="2026-09-06T17:00:00Z",
        completed_at_utc="2026-09-06T17:01:00Z", preflight_only=True,
    )
    assert result["durable_prefix_length"] == 0
    assert events == []
    assert state["presence"][RUNNER.INTENT] == (False, True)


def test_complete_intent_pre_rename_preflight_is_resumable_without_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events, state, _stored = _install_fake_lifecycle(monkeypatch)
    monkeypatch.setattr(
        RUNNER, "_rename", lambda _expected: (_ for _ in ()).throw(RuntimeError("before rename"))
    )
    with pytest.raises(RuntimeError, match="before rename"):
        RUNNER.quarantine(
            expected=_expected(), created_at_utc="2026-09-06T17:00:00Z",
            completed_at_utc="2026-09-06T17:01:00Z", preflight_only=False,
        )
    baseline = list(events)
    result = RUNNER.quarantine(
        expected=_expected(), created_at_utc="2026-09-06T17:00:00Z",
        completed_at_utc="2026-09-06T17:01:00Z", preflight_only=True,
    )
    assert result["durable_prefix_length"] == 1
    assert result["source_state"] == "SOURCE_CANONICAL"
    assert events == baseline
    assert state["presence"][RUNNER.INTENT] == (True, True)


def test_provenance_sidecar_only_preflight_is_read_only_resume(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events, state, _stored = _install_fake_lifecycle(monkeypatch)
    original_publish = RUNNER._publish

    def crash_with_sidecar(
        path: Path, document: dict[str, object], field: str,
        parent: dict[str, object],
    ) -> dict[str, object]:
        if path == RUNNER.PROVENANCE:
            state["presence"][path] = (False, True)
            raise RuntimeError("after provenance sidecar")
        return original_publish(path, document, field, parent)

    monkeypatch.setattr(RUNNER, "_publish", crash_with_sidecar)
    with pytest.raises(RuntimeError, match="after provenance sidecar"):
        RUNNER.quarantine(
            expected=_expected(), created_at_utc="2026-09-06T17:00:00Z",
            completed_at_utc="2026-09-06T17:01:00Z", preflight_only=False,
        )
    baseline = list(events)
    result = RUNNER.quarantine(
        expected=_expected(), created_at_utc="2026-09-06T17:00:00Z",
        completed_at_utc="2026-09-06T17:01:00Z", preflight_only=True,
    )
    assert result["durable_prefix_length"] == 1
    assert result["validated_partial_provenance"] is True
    assert events == baseline
    assert state["presence"][RUNNER.PROVENANCE] == (False, True)


@pytest.mark.parametrize(
    ("intent", "provenance"),
    [((True, False), (False, False)), ((False, False), (False, True))],
)
def test_impossible_main_only_or_provenance_before_intent_fails_before_mutation(
    monkeypatch: pytest.MonkeyPatch,
    intent: tuple[bool, bool], provenance: tuple[bool, bool],
) -> None:
    events, state, _stored = _install_fake_lifecycle(monkeypatch)
    state["presence"][RUNNER.INTENT] = intent
    state["presence"][RUNNER.PROVENANCE] = provenance
    with pytest.raises(RUNNER.QuarantineError):
        RUNNER.quarantine(
            expected=_expected(), created_at_utc="2026-09-06T17:00:00Z",
            completed_at_utc="2026-09-06T17:01:00Z", preflight_only=True,
        )
    assert events == []


def test_main_invocation_gate_fails_before_parser_or_lock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(RUNNER, "parser", lambda: pytest.fail("invocation gate reached parser"))
    with pytest.raises(RUNNER.QuarantineError, match="requires exact"):
        RUNNER.main([])


def test_ast_has_no_delete_copy_namespace_iq_or_subprocess_primitives() -> None:
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    forbidden_names = {"unlink", "remove", "rmdir", "rmtree", "copy", "copy2", "fork", "unshare", "setns", "Popen", "run"}
    seen = {
        node.attr for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and node.attr in forbidden_names
    } | {
        node.id for node in ast.walk(tree)
        if isinstance(node, ast.Name) and node.id in forbidden_names
    }
    assert seen == set()


@pytest.mark.parametrize(
    "addition",
    [
        "def rogue():\n    os.unlink('/tmp/x')\n",
        "def rogue():\n    os.rmtree('/tmp/x')\n",
        "def rogue():\n    os.system('true')\n",
        "def rogue():\n    os.posix_spawn('/bin/true', ['/bin/true'], {})\n",
        "def rogue():\n    Path('/tmp/x').write_text('x')\n",
        "def rogue():\n    os.rename('/tmp/x', '/tmp/y')\n",
        "def rogue():\n    os.open('/tmp/x', os.O_CREAT | os.O_WRONLY)\n",
        "def rogue():\n    os.open('/tmp/x', os.O_RDWR)\n",
        "def rogue(fd):\n    os.write(fd, b'x')\n",
        "def rogue(fd):\n    os.pwrite(fd, b'x', 0)\n",
        "def rogue():\n    os.makedirs('/tmp/x')\n",
        "def rogue():\n    os.link('/tmp/x', '/tmp/y')\n",
        "def rogue():\n    exec('pass')\n",
        "def rogue():\n    BASE._unexpected_mutation()\n",
        "def rogue():\n    return '/tmp/sample.sigmf-data'\n",
        "def rogue():\n    return getattr(ctypes.CDLL(None), 'unlink')\n",
    ],
)
def test_static_scope_proof_rejects_adversarial_mutation_or_data_plane_addition(
    addition: str,
) -> None:
    mutated = SOURCE.read_bytes() + b"\n" + addition.encode()
    with pytest.raises(RUNNER.QuarantineError, match="static mutation/data-plane scope differs"):
        RUNNER._static_scope_proof(mutated)


def test_static_scope_proof_rejects_changed_or_second_rename_binding() -> None:
    source = SOURCE.read_text(encoding="utf-8")
    changed = source.replace("function = libc.renameat2", "function = libc.renameat", 1)
    with pytest.raises(RUNNER.QuarantineError, match="rename_bindings=0"):
        RUNNER._static_scope_proof(changed.encode())
    extra = source + "\ndef rogue():\n    libc = ctypes.CDLL(None)\n    other = libc.renameat2\n"
    with pytest.raises(RUNNER.QuarantineError, match="rename_bindings=2"):
        RUNNER._static_scope_proof(extra.encode())


def test_static_scope_proof_binds_exact_same_parent_noreplace_shape() -> None:
    proof = RUNNER._static_scope_proof(SOURCE.read_bytes())
    assert proof["renameat2_call_count"] == 1
    assert proof["sole_ctypes_binding"] == "_rename:renameat2"
    assert proof["renameat2_uses_same_pinned_parent_dirfd"] is True
    assert proof["renameat2_flag"] == "RENAME_NOREPLACE"
    assert proof["os_open_disallowed_write_flag_count"] == 0


def test_review_template_contract_has_all_safety_checks() -> None:
    checks = RUNNER.expected_review_checks()
    assert checks and all(value is True for value in checks.values())
    assert "no_copy_delete_iq_read_namespace_role_decoder_or_outcome" in checks
    assert "crash_resume_grammar_is_fail_closed" in checks


def test_toolchain_requires_exact_review_keys_bindings_and_zero_severity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = _expected()
    evidence = {"evidence": _identity(RUNNER.EVIDENCE, "e")}

    def installed(path: Path, digest: str, mode: int, payload: bytes = b"x") -> tuple[bytes, dict[str, object]]:
        return payload, {
            "path": str(path), "size_bytes": len(payload), "sha256": digest,
            "uid": 0, "gid": 0, "mode": mode, "nlink": 1,
        }

    records = {
        RUNNER.RUNNER.name: installed(
            RUNNER.RUNNER,
            expected["runner_sha256"],
            0o555,
            SOURCE.read_bytes(),
        ),
        RUNNER.RUNNER_TEST.name: installed(RUNNER.RUNNER_TEST, expected["test_sha256"], 0o555),
        RUNNER.REVIEW.name: installed(RUNNER.REVIEW, expected["review_sha256"], 0o444),
        RUNNER.REVIEW.name + ".sha256": installed(
            RUNNER.REVIEW.with_name(RUNNER.REVIEW.name + ".sha256"),
            "4" * 64, 0o444,
            f"{expected['review_sha256']}  {RUNNER.REVIEW.name}\n".encode(),
        ),
        RUNNER.LOCK.name: installed(RUNNER.LOCK, "5" * 64, 0o600, b""),
    }
    bindings = {
        "runner": RUNNER._simple(records[RUNNER.RUNNER.name][1]),
        "runner_tests": RUNNER._simple(records[RUNNER.RUNNER_TEST.name][1]),
        "hardened_base": {
            "path": str(RUNNER.BASE_RUNNER), "size_bytes": 52933,
            "sha256": RUNNER.BASE_RUNNER_SHA256,
        },
        "hardened_base_tests": {
            "path": str(RUNNER.BASE_TEST), "size_bytes": 29687,
            "sha256": RUNNER.BASE_TEST_SHA256,
        },
        "prestart_failure_evidence_chain": evidence,
        "static_scope_proof": RUNNER._static_scope_proof(records[RUNNER.RUNNER.name][0]),
    }
    review = {
        "schema_version": RUNNER.REVIEW_SCHEMA,
        "status": "GO",
        "reviewed_bindings": bindings,
        "checks": RUNNER.expected_review_checks(),
        "severity_counts": {"P0": 0, "P1": 0, "P2": 0},
        "review_payload_sha256": "6" * 64,
    }
    monkeypatch.setattr(RUNNER, "_selfhashed", lambda *_args, **_kwargs: review)
    assert RUNNER._toolchain(expected, records, evidence)["runner"] == bindings["runner"]
    review["severity_counts"]["P1"] = 1
    with pytest.raises(RUNNER.QuarantineError, match="independent quarantine review differs"):
        RUNNER._toolchain(expected, records, evidence)


def _install_evidence_fixture(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *,
    wrong_review_binding: bool = False, severity_p1: int = 0,
) -> tuple[Path, Path]:
    evidence_path = tmp_path / "evidence.json"
    review_path = tmp_path / "review.json"
    evidence_unhashed = {
        "schema_version": "blind-phase-confirmatory-retry3-prestart-failure-evidence-v1",
        "status": "PASS_METADATA_ONLY_RETRY3_START_REFUSED_BEFORE_INTENT_UNSHARE_IQ",
        "incident": "retry3-prestart",
    }
    evidence = {
        **evidence_unhashed,
        "evidence_payload_sha256": RUNNER._sha256_document(evidence_unhashed),
    }
    evidence_path.write_text(json.dumps(evidence, sort_keys=True) + "\n", encoding="utf-8")
    evidence_payload, evidence_identity = RUNNER._read_regular(evidence_path)
    evidence_sidecar = evidence_path.with_name(evidence_path.name + ".sha256")
    evidence_sidecar.write_text(
        f"{evidence_identity['sha256']}  {evidence_path.name}\n", encoding="ascii"
    )

    review_unhashed = {
        "schema_version": "blind-phase-confirmatory-retry3-prestart-failure-evidence-independent-review-v1",
        "status": "GO",
        "evidence": (
            _identity(evidence_path, "f")
            if wrong_review_binding else RUNNER._simple(evidence_identity)
        ),
        "severity_counts": {"P0": 0, "P1": severity_p1, "P2": 0},
    }
    review = {
        **review_unhashed,
        "review_payload_sha256": RUNNER._sha256_document(review_unhashed),
    }
    review_path.write_text(json.dumps(review, sort_keys=True) + "\n", encoding="utf-8")
    review_payload, review_identity = RUNNER._read_regular(review_path)
    review_sidecar = review_path.with_name(review_path.name + ".sha256")
    review_sidecar.write_text(
        f"{review_identity['sha256']}  {review_path.name}\n", encoding="ascii"
    )
    evidence_sidecar_identity = RUNNER._read_regular(evidence_sidecar, 4096)[1]
    review_sidecar_identity = RUNNER._read_regular(review_sidecar, 4096)[1]
    monkeypatch.setattr(RUNNER, "EVIDENCE", evidence_path)
    monkeypatch.setattr(RUNNER, "EVIDENCE_SIDECAR", evidence_sidecar)
    monkeypatch.setattr(RUNNER, "EVIDENCE_REVIEW", review_path)
    monkeypatch.setattr(RUNNER, "EVIDENCE_REVIEW_SIDECAR", review_sidecar)
    monkeypatch.setattr(RUNNER, "EVIDENCE_SHA256", evidence_identity["sha256"])
    monkeypatch.setattr(
        RUNNER, "EVIDENCE_PAYLOAD_SHA256", evidence["evidence_payload_sha256"]
    )
    monkeypatch.setattr(RUNNER, "EVIDENCE_REVIEW_SHA256", review_identity["sha256"])
    monkeypatch.setattr(
        RUNNER, "EVIDENCE_REVIEW_PAYLOAD_SHA256", review["review_payload_sha256"]
    )
    monkeypatch.setattr(
        RUNNER, "EVIDENCE_SIDECAR_SHA256", evidence_sidecar_identity["sha256"]
    )
    monkeypatch.setattr(
        RUNNER, "EVIDENCE_REVIEW_SIDECAR_SHA256", review_sidecar_identity["sha256"]
    )
    monkeypatch.setattr(RUNNER, "_repo_artifact", lambda *_args, **_kwargs: None)
    assert evidence_payload and review_payload
    return evidence_path, review_path


def test_evidence_chain_accepts_exact_main_sidecars_review_and_zero_severity(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    _install_evidence_fixture(monkeypatch, tmp_path)
    chain = RUNNER._evidence_chain()
    assert chain["evidence"]["sha256"] == RUNNER.EVIDENCE_SHA256
    assert chain["independent_review"]["sha256"] == RUNNER.EVIDENCE_REVIEW_SHA256


@pytest.mark.parametrize(
    "mutation",
    ["main", "sidecar", "review_binding", "review_sidecar", "severity"],
)
def test_evidence_chain_rejects_wrong_main_sidecar_review_binding_or_severity(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, mutation: str,
) -> None:
    evidence, review = _install_evidence_fixture(
        monkeypatch, tmp_path,
        wrong_review_binding=mutation == "review_binding",
        severity_p1=1 if mutation == "severity" else 0,
    )
    if mutation == "main":
        monkeypatch.setattr(RUNNER, "EVIDENCE_SHA256", "0" * 64)
    elif mutation in {"sidecar", "review_sidecar"}:
        target = (
            evidence.with_name(evidence.name + ".sha256")
            if mutation == "sidecar"
            else review.with_name(review.name + ".sha256")
        )
        target.write_text(f"{'0' * 64}  {target.name.removesuffix('.sha256')}\n", encoding="ascii")
        digest = RUNNER._read_regular(target, 4096)[1]["sha256"]
        monkeypatch.setattr(
            RUNNER,
            "EVIDENCE_SIDECAR_SHA256" if mutation == "sidecar" else "EVIDENCE_REVIEW_SIDECAR_SHA256",
            digest,
        )
    with pytest.raises(RUNNER.QuarantineError):
        RUNNER._evidence_chain()


def test_validate_exact_committed_rejects_extra_key_operation_timestamp_and_selfhash() -> None:
    expected = {
        "schema_version": "example-v1",
        "status": "PASS",
        "created_at_utc": "2026-09-06T17:00:00Z",
        "authorized_operation": {"operation": "renameat2(RENAME_NOREPLACE)"},
    }
    field = "payload_sha256"
    baseline = {**expected, field: RUNNER._sha256_document(expected)}
    RUNNER._validate_exact_committed(baseline, expected, field)
    mutations = []
    extra = copy.deepcopy(baseline)
    extra["extra"] = True
    mutations.append(extra)
    operation = copy.deepcopy(baseline)
    operation["authorized_operation"]["operation"] = "rename"
    mutations.append(operation)
    timestamp = copy.deepcopy(baseline)
    timestamp["created_at_utc"] = "2026-09-06T17:00:01Z"
    mutations.append(timestamp)
    selfhash = copy.deepcopy(baseline)
    selfhash[field] = "0" * 64
    mutations.append(selfhash)
    for mutated in mutations:
        with pytest.raises(RUNNER.QuarantineError):
            RUNNER._validate_exact_committed(mutated, expected, field)
