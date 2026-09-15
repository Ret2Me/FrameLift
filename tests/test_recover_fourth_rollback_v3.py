from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import types

import pytest


SOURCE = (
    Path(__file__).parents[1]
    / "work/blind-phase-confirmatory-v2/recover_fourth_rollback_v3.py"
)
SPEC = importlib.util.spec_from_file_location("recover_fourth_rollback_v3", SOURCE)
assert SPEC is not None and SPEC.loader is not None
recovery = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(recovery)


def _patch_projection_paths(monkeypatch: pytest.MonkeyPatch, root: Path) -> None:
    source_parent = root / "source"
    recovery_parent = root / "recovery"
    source_parent.mkdir()
    recovery_parent.mkdir()
    monkeypatch.setattr(recovery, "SOURCE_PARENT", source_parent)
    monkeypatch.setattr(recovery, "RECOVERY_PARENT", recovery_parent)
    monkeypatch.setattr(recovery, "BUILDER_SOURCE", source_parent / "build_runtime_guard_v3.py")
    monkeypatch.setattr(recovery, "RETAINED_PATCHED_SOURCE", source_parent / ".patched.retained")
    monkeypatch.setattr(recovery, "PROJECTED_SOURCE_STAGE", source_parent / ".historical.stage")
    monkeypatch.setattr(recovery, "QUARANTINED_HISTORICAL_SOURCE", recovery_parent / "historical.py")
    monkeypatch.setattr(recovery, "_fsync_directory", lambda _path: None)


def _projection_fixture(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    _patch_projection_paths(monkeypatch, tmp_path)
    patched = b"patched source\n"
    historical = b"historical source\n"
    recovery.BUILDER_SOURCE.write_bytes(patched)
    _, source_identity = recovery._read_regular(recovery.BUILDER_SOURCE)
    monkeypatch.setattr(recovery, "PATCHED_BUILDER_SHA256", hashlib.sha256(patched).hexdigest())
    monkeypatch.setattr(recovery, "PATCHED_BUILDER_SIZE", len(patched))
    monkeypatch.setattr(recovery, "HISTORICAL_BUILDER_SHA256", hashlib.sha256(historical).hexdigest())
    monkeypatch.setattr(recovery, "HISTORICAL_BUILDER_SIZE", len(historical))
    real_read_regular = recovery._read_regular

    def simulate_root_chown(path: Path, *args, **kwargs):
        payload, identity = real_read_regular(path, *args, **kwargs)
        if path == recovery.QUARANTINED_HISTORICAL_SOURCE and identity["mode"] == 0o444:
            identity = {**identity, "uid": 0, "gid": 0}
        return payload, identity

    monkeypatch.setattr(recovery, "_read_regular", simulate_root_chown)
    monkeypatch.setattr(recovery.os, "chown", lambda *_args, **_kwargs: None)
    return patched, historical, source_identity


def test_source_projection_state_machine_recovers_all_named_boundaries(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    patched, historical, source = _projection_fixture(monkeypatch, tmp_path)
    assert recovery._source_projection_state(source, historical)["state"] == "A_PATCHED_PRISTINE"

    recovery._rename_noreplace(recovery.BUILDER_SOURCE, recovery.RETAINED_PATCHED_SOURCE)
    assert recovery._source_projection_state(source, historical)["state"] == "B0_PATCHED_RETAINED_CANONICAL_ABSENT"
    recovery.PROJECTED_SOURCE_STAGE.write_bytes(historical[:4])
    recovery.PROJECTED_SOURCE_STAGE.chmod(0o400)
    assert recovery._source_projection_state(source, historical)["state"] == "B0_PATCHED_RETAINED_CANONICAL_ABSENT"

    recovery._ensure_historical_projection(historical, source)
    assert recovery._source_projection_state(source, historical)["state"] == "B_HISTORICAL_PROJECTED"
    restored = recovery._restore_source(source, historical)
    assert restored["state_after_restoration"] == "D_PATCHED_RESTORED_HISTORICAL_QUARANTINED"
    assert recovery.BUILDER_SOURCE.read_bytes() == patched

    recovery._ensure_historical_projection(historical, source)
    assert recovery._source_projection_state(source, historical)["state"] == "E_RETRY_HISTORICAL_PROJECTED_WITH_PRIOR_QUARANTINE"
    recovery.BUILDER_SOURCE.unlink()
    recovery.PROJECTED_SOURCE_STAGE.write_bytes(historical[:5])
    recovery.PROJECTED_SOURCE_STAGE.chmod(0o400)
    assert recovery._source_projection_state(source, historical)["state"] == "C_HISTORICAL_QUARANTINED_PATCHED_RETAINED"
    recovery._restore_source(source, historical)
    assert recovery._source_projection_state(source, historical)["state"] == "D_PATCHED_RESTORED_HISTORICAL_QUARANTINED"
    assert recovery.BUILDER_SOURCE.read_bytes() == patched


def test_source_projection_rejects_every_unenumerated_combination(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _, _, source = _projection_fixture(monkeypatch, tmp_path)
    recovery.RETAINED_PATCHED_SOURCE.write_bytes(b"foreign")
    with pytest.raises(recovery.RecoveryError, match="not one of"):
        recovery._source_projection_state(source, b"historical source\n")


def test_source_projection_rejects_foreign_partial_stage_bytes_and_metadata(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _, historical, source = _projection_fixture(monkeypatch, tmp_path)
    recovery._rename_noreplace(recovery.BUILDER_SOURCE, recovery.RETAINED_PATCHED_SOURCE)
    recovery.PROJECTED_SOURCE_STAGE.write_bytes(b"not a prefix")
    recovery.PROJECTED_SOURCE_STAGE.chmod(0o400)
    with pytest.raises(recovery.RecoveryError, match="exact historical prefix"):
        recovery._source_projection_state(source, historical)
    recovery.PROJECTED_SOURCE_STAGE.chmod(0o600)
    recovery.PROJECTED_SOURCE_STAGE.write_bytes(historical[:3])
    recovery.PROJECTED_SOURCE_STAGE.chmod(0o600)
    with pytest.raises(recovery.RecoveryError, match="metadata"):
        recovery._source_projection_state(source, historical)


@pytest.mark.parametrize(
    ("staged", "uid", "gid", "mode", "phase"),
    (
        (b"hist", 0, 0, 0o400, "EXACT_PREFIX_WRITE_IN_PROGRESS"),
        (b"historical", 1000, 1000, 0o400, "FULL_BYTES_OWNER_RESTORED_MODE_PENDING"),
        (b"historical", 1000, 1000, 0o664, "FULL_BYTES_AND_METADATA_READY_TO_RENAME"),
    ),
)
def test_projection_stage_accepts_only_enumerated_crash_metadata_phases(
    monkeypatch: pytest.MonkeyPatch,
    staged: bytes,
    uid: int,
    gid: int,
    mode: int,
    phase: str,
) -> None:
    payload = b"historical"
    identity = {
        "path": str(recovery.PROJECTED_SOURCE_STAGE),
        "size_bytes": len(staged),
        "sha256": hashlib.sha256(staged).hexdigest(),
        "st_dev": 1,
        "st_ino": 2,
        "uid": uid,
        "gid": gid,
        "mode": mode,
        "nlink": 1,
    }
    source = {"uid": 1000, "gid": 1000, "mode": 0o664}
    monkeypatch.setattr(recovery, "_read_regular", lambda *_args, **_kwargs: (staged, identity))
    monkeypatch.setattr(recovery.os, "geteuid", lambda: 0)
    monkeypatch.setattr(recovery.os, "getegid", lambda: 0)
    _, observed = recovery._validate_projection_stage(payload, source)
    assert observed == phase


def test_restore_pristine_source_is_idempotent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    patched, _, source = _projection_fixture(monkeypatch, tmp_path)
    result = recovery._restore_source(source, b"historical source\n")
    assert result["state_before_restoration"] == "A_PATCHED_PRISTINE"
    assert result["state_after_restoration"] == "A_PATCHED_PRISTINE"
    assert recovery.BUILDER_SOURCE.read_bytes() == patched


def test_terminal_source_state_rejects_mutable_quarantine_even_when_state_is_d(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _, historical, source = _projection_fixture(monkeypatch, tmp_path)
    recovery.QUARANTINED_HISTORICAL_SOURCE.write_bytes(historical)
    state = recovery._source_projection_state(source, historical)
    assert state["state"] == "D_PATCHED_RESTORED_HISTORICAL_QUARANTINED"
    assert (
        recovery._quarantine_historical_phase(
            state["quarantined_historical"], source
        )
        == "RENAMED_SOURCE_METADATA_PENDING_HARDENING"
    )
    with pytest.raises(recovery.RecoveryError, match="immutable root quarantine"):
        recovery._require_terminal_source_state(state, source)


def test_write_all_handles_partial_writes(monkeypatch: pytest.MonkeyPatch) -> None:
    written = bytearray()

    def partial(_fd: int, payload: bytes) -> int:
        count = min(3, len(payload))
        written.extend(payload[:count])
        return count

    monkeypatch.setattr(recovery.os, "write", partial)
    recovery._write_all(9, b"0123456789")
    assert written == b"0123456789"


def test_write_all_rejects_zero_progress(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(recovery.os, "write", lambda _fd, _payload: 0)
    with pytest.raises(recovery.RecoveryError, match="no forward progress"):
        recovery._write_all(9, b"x")


def _single_receipt_contract(ordinal: int, raw: bytes, output: bytes) -> dict[int, dict[str, object]]:
    return {
        ordinal: {
            "line_sha256": hashlib.sha256(raw).hexdigest(),
            "output_sha256": hashlib.sha256(output).hexdigest(),
            "output_size_bytes": len(output),
            "exit_code": 0,
        }
    }


def test_transcript_validator_drains_unrelated_oversized_record_then_finds_receipt(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    ordinal = 7
    output = b"ok"
    document = {
        "ordinal": ordinal,
        "payload": {"item": {"aggregated_output": output.decode(), "exit_code": 0}},
    }
    relevant = json.dumps(document, separators=(",", ":")).encode() + b"\n"
    unrelated = (
        b'{"ordinal":1,"payload":"'
        + b"x" * (recovery.MAXIMUM_RELEVANT_TRANSCRIPT_LINE_BYTES + 257)
        + b'"}\n'
    )
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_bytes(unrelated + relevant)
    monkeypatch.setattr(recovery, "TRANSCRIPT_RECEIPTS", _single_receipt_contract(ordinal, relevant, output))
    monkeypatch.setattr(recovery, "RELEVANT_ORDINAL_MARKERS", {ordinal: b'"ordinal":7'})
    monkeypatch.setattr(recovery, "EXPECTED_UNRELATED_OVERSIZED_TRANSCRIPT_RECORDS", {})
    result = recovery._validate_transcript_receipts(transcript)
    assert result[str(ordinal)]["physical_line_number"] == 2
    assert result["frozen_unrelated_oversized_records"] == []
    # The unrelated record was fully drained because the exact relevant line
    # immediately after it was still found and verified as physical line 2.
    assert result[str(ordinal)]["physical_line_number"] == 2


def test_transcript_validator_rejects_relevant_oversized_record(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    ordinal = 7
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_bytes(
        b'{"ordinal":7,"payload":"'
        + b"x" * (recovery.MAXIMUM_RELEVANT_TRANSCRIPT_LINE_BYTES + 257)
        + b'"}\n'
    )
    monkeypatch.setattr(recovery, "TRANSCRIPT_RECEIPTS", {ordinal: {}})
    monkeypatch.setattr(recovery, "RELEVANT_ORDINAL_MARKERS", {ordinal: b'"ordinal":7'})
    monkeypatch.setattr(recovery, "EXPECTED_UNRELATED_OVERSIZED_TRANSCRIPT_RECORDS", {})
    with pytest.raises(recovery.RecoveryError, match="frozen transcript ordinal.*oversized"):
        recovery._validate_transcript_receipts(transcript)


def test_bounded_transcript_reader_finds_marker_split_at_both_boundaries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ordinal = 71937
    marker = b'"ordinal":71937'
    monkeypatch.setattr(recovery, "RELEVANT_ORDINAL_MARKERS", {ordinal: marker})
    # The marker starts before the retained-prefix ceiling and ends in a drain
    # chunk; a tiny drain size then forces another overlap boundary.
    payload = b"a" * 13 + marker + b",\"payload\":" + b"z" * 31 + b"}\n"
    record = recovery._read_bounded_transcript_record(
        io.BytesIO(payload), maximum_bytes=20, drain_chunk_bytes=3
    )
    assert record is not None and record["oversized"] is True
    assert record["size_bytes"] == len(payload)
    assert record["sha256"] == hashlib.sha256(payload).hexdigest()
    assert record["relevant_ordinal_markers"] == [ordinal]


def test_bounded_transcript_reader_rejects_numeric_prefix_false_match_and_handles_eof(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ordinal = 71937
    marker = b'"ordinal":71937'
    monkeypatch.setattr(recovery, "RELEVANT_ORDINAL_MARKERS", {ordinal: marker})
    false_record = recovery._read_bounded_transcript_record(
        io.BytesIO(b"x" * 9 + marker + b"0" + b"y" * 20 + b"\n"),
        maximum_bytes=10,
        drain_chunk_bytes=2,
    )
    assert false_record is not None
    assert false_record["relevant_ordinal_markers"] == []
    truncated = recovery._read_bounded_transcript_record(
        io.BytesIO(b"x" * 11 + marker), maximum_bytes=10, drain_chunk_bytes=2
    )
    assert truncated is not None
    assert truncated["relevant_ordinal_markers"] == [ordinal]


def test_parent_open_redirect_is_code_line_path_count_and_mount_gated(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    candidate = tmp_path / "candidate"
    component = tmp_path / "component"
    candidate.mkdir()
    component.mkdir()
    monkeypatch.setattr(
        recovery,
        "RUNTIME_PARENTS",
        {"candidate": candidate, "component": component},
    )
    monkeypatch.setattr(
        recovery,
        "EXPECTED_PARENT_IDENTITIES",
        {
            "candidate": {"st_dev": candidate.stat().st_dev, "st_ino": candidate.stat().st_ino, "child_mount_id": 169},
            "component": {"st_dev": component.stat().st_dev, "st_ino": component.stat().st_ino, "child_mount_id": 170},
        },
    )
    monkeypatch.setattr(recovery, "_validate_pid1_host", lambda: None)
    monkeypatch.setattr(recovery, "_fd_mount_id", lambda _fd: 29)

    source = "\n" * 9 + "def invoke(proxy, path, flags):\n    return proxy(path, flags)\n"
    scope: dict[str, object] = {}
    exec(compile(source, "historical.py", "exec"), scope, scope)
    invoke = scope["invoke"]
    assert callable(invoke)
    monkeypatch.setattr(recovery, "HISTORICAL_OPEN_LINE", invoke.__code__.co_firstlineno + 1)

    opened_paths: list[Path] = []

    def opener(path: object, flags: int, mode: int = 0o777, *, dir_fd=None) -> int:
        del mode, dir_fd
        rendered = Path(os.fsdecode(path))
        opened_paths.append(rendered)
        # Production prefixes /proc/1/root; use the requested basename in this fixture.
        target = candidate if rendered.name == "candidate" else component
        return os.open(target, flags)

    records: list[dict[str, object]] = []
    redirect = recovery._ExactParentOpenRedirect(
        invoke.__code__, opener, record_call=lambda record: records.append(dict(record))
    )
    fd1 = invoke(redirect, candidate, recovery.PARENT_OPEN_FLAGS)
    fd2 = invoke(redirect, component, recovery.PARENT_OPEN_FLAGS)
    os.close(fd1)
    os.close(fd2)
    redirect.require_complete(2)
    assert {record["label"] for record in records} == {"candidate", "component"}
    assert all(record["fdinfo_mount_id"] == 29 for record in records)
    with pytest.raises(recovery.RecoveryError, match="call count"):
        invoke(redirect, candidate, recovery.PARENT_OPEN_FLAGS)


def test_parent_open_gate_rejects_wrong_signature_and_target(tmp_path: Path) -> None:
    code = (lambda: None).__code__
    redirect = recovery._ExactParentOpenRedirect(code, os.open)
    frame = types.SimpleNamespace(f_code=code, f_lineno=recovery.HISTORICAL_OPEN_LINE)
    with pytest.raises(recovery.RecoveryError, match="signature"):
        redirect._gate(frame, tmp_path, recovery.PARENT_OPEN_FLAGS | os.O_NONBLOCK, 0o777, None)
    with pytest.raises(recovery.RecoveryError, match="target"):
        redirect._gate(frame, tmp_path, recovery.PARENT_OPEN_FLAGS, 0o777, None)


def test_load_selfhashed_rejects_nonimmutable_artifact(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path = tmp_path / "receipt.json"
    document = {"status": "PASS"}
    document["payload_sha256"] = recovery._sha256_document(document)
    payload = (json.dumps(document) + "\n").encode()
    path.write_bytes(payload)
    path.with_name(path.name + ".sha256").write_text(
        f"{hashlib.sha256(payload).hexdigest()}  {path.name}\n"
    )
    with pytest.raises(recovery.RecoveryError, match="immutability"):
        recovery._load_selfhashed(path, "payload_sha256")


def test_load_selfhashed_repairs_only_missing_sidecar_after_immutable_main(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path = tmp_path / "receipt.json"
    document = {"status": "PASS"}
    document["payload_sha256"] = recovery._sha256_document(document)
    payload = (json.dumps(document) + "\n").encode()
    path.write_bytes(payload)
    sidecar = path.with_name(path.name + ".sha256")
    repair_calls: list[Path] = []

    def read_regular(target: Path, _maximum: int = 0):
        if target == path:
            return payload, {
                "path": str(path),
                "size_bytes": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
                "st_dev": 1,
                "st_ino": 2,
                "uid": 0,
                "gid": 0,
                "mode": 0o444,
                "nlink": 1,
            }
        sidecar_payload = f"{hashlib.sha256(payload).hexdigest()}  {path.name}\n".encode()
        return sidecar_payload, {
            "path": str(sidecar),
            "size_bytes": len(sidecar_payload),
            "sha256": hashlib.sha256(sidecar_payload).hexdigest(),
            "st_dev": 1,
            "st_ino": 3,
            "uid": 0,
            "gid": 0,
            "mode": 0o444,
            "nlink": 1,
        }

    def repair(target: Path, _identity) -> None:
        repair_calls.append(target)
        sidecar.touch()

    monkeypatch.setattr(recovery, "_read_regular", read_regular)
    monkeypatch.setattr(recovery, "_repair_missing_sidecar", repair)
    loaded, _ = recovery._load_selfhashed(
        path, "payload_sha256", repair_missing_sidecar=True
    )
    assert loaded == document
    assert repair_calls == [path]


def test_load_selfhashed_read_only_mode_never_repairs_missing_sidecar(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path = tmp_path / "receipt.json"
    document = {"status": "PASS"}
    document["payload_sha256"] = recovery._sha256_document(document)
    payload = (json.dumps(document) + "\n").encode()
    path.write_bytes(payload)
    monkeypatch.setattr(
        recovery,
        "_read_regular",
        lambda _path, _maximum=0: (
            payload,
            {
                "path": str(path),
                "size_bytes": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
                "st_dev": 1,
                "st_ino": 2,
                "uid": 0,
                "gid": 0,
                "mode": 0o444,
                "nlink": 1,
            },
        ),
    )
    monkeypatch.setattr(
        recovery,
        "_repair_missing_sidecar",
        lambda *_args: (_ for _ in ()).throw(AssertionError("repair attempted")),
    )
    with pytest.raises(recovery.RecoveryError, match="sidecar is absent"):
        recovery._load_selfhashed(path, "payload_sha256", repair_missing_sidecar=False)


def test_projection_requires_same_filesystem_before_incident(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source = tmp_path / "source"
    recovery_parent = tmp_path / "recovery"
    source.mkdir()
    recovery_parent.mkdir()
    monkeypatch.setattr(recovery, "SOURCE_PARENT", source)
    monkeypatch.setattr(recovery, "RECOVERY_PARENT", recovery_parent)
    real_stat = Path.stat

    def different_devices(path: Path):
        value = real_stat(path)
        if path == recovery_parent:
            return types.SimpleNamespace(st_dev=value.st_dev + 1)
        return types.SimpleNamespace(st_dev=value.st_dev)

    monkeypatch.setattr(Path, "stat", different_devices)
    with pytest.raises(recovery.RecoveryError, match="different filesystems"):
        recovery._validate_projection_filesystems()


def test_failure_receipt_does_not_claim_source_restored_when_restoration_failed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    published: dict[str, object] = {}
    monkeypatch.setattr(recovery, "FAILURE_PATH", tmp_path / "failure.json")

    def capture(_path: Path, document, _hash_field: str):
        published.update(document)
        return {}

    monkeypatch.setattr(recovery, "_publish", capture)
    recovery._publish_failure_once(
        incident_identity={
            "path": "/incident",
            "size_bytes": 1,
            "sha256": "a" * 64,
        },
        source_restoration=None,
        failure_authorization=None,
        error=recovery.RecoveryError("restoration failed"),
    )
    assert published["status"] == "FAIL_CLOSED_SOURCE_RESTORATION_FAILED_OR_AMBIGUOUS"
    assert published["source_restoration_proven"] is False


def test_no_role_gate_matches_actual_handoff_prefix(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / "namespace-role-handoff-v3-campaign-deadbeef.json").write_text("{}")
    monkeypatch.setattr(recovery, "CONTROL_PARENT", tmp_path)
    with pytest.raises(recovery.RecoveryError, match="artifact exists"):
        recovery._validate_no_role_state()


def test_exact_control_inventory_accepts_only_ordered_rollback_publication_prefixes() -> None:
    base = sorted(recovery._base_control_names())
    assert len(base) == 30
    recovery._validate_control_name_set(base, allow_progress=False)
    progress: list[str] = []
    for name in recovery.ROLLBACK_PROGRESS_FILES:
        progress.append(name)
        recovery._validate_control_name_set(base + progress, allow_progress=True)
    with pytest.raises(recovery.RecoveryError, match="inventory"):
        recovery._validate_control_name_set(
            base + [recovery.ROLLBACK_PROGRESS_FILES[1]], allow_progress=True
        )
    with pytest.raises(recovery.RecoveryError, match="inventory"):
        recovery._validate_control_name_set(base + ["foreign"], allow_progress=True)


def test_keeper_holder_fixed_point_requires_exact_singleton_and_no_namespace_fds() -> None:
    expected = {
        "namespace_members": [
            {
                "pid": recovery.EXPECTED_KEEPER["pid"],
                "ppid": 1,
                "state": "S",
                "uid": [0, 0, 0, 0],
                "gid": [0, 0, 0, 0],
                "starttime_ticks": recovery.EXPECTED_KEEPER["starttime_ticks"],
            }
        ],
        "namespace_fd_holders": [],
    }
    recovery._require_holder_fixed_point(expected, expected)
    changed = {**expected, "namespace_fd_holders": [{"pid": 99, "fd": 7}]}
    with pytest.raises(recovery.RecoveryError, match="fixed point"):
        recovery._require_holder_fixed_point(expected, changed)


def test_live_frozen_control_inventory_and_selfhashes_are_exact_when_present() -> None:
    if not recovery.CONTROL_PARENT.exists():
        pytest.skip("incident control tree is not present on this host")
    inventory = recovery._validate_control_inventory(allow_progress=False)
    assert inventory["direct_child_count"] == 30
    assert set(recovery._validate_chain()) == {path.name for path in recovery.EXPECTED_FILES}


def test_live_prior_attempt_and_bounded_transcript_evidence_are_exact_when_present() -> None:
    if not recovery.PRIOR_RECOVERY_PARENT.exists() or not recovery.TRANSCRIPT_PATH.exists():
        pytest.skip("prior recovery evidence or transcript is not present on this host")
    prior = recovery._validate_prior_recovery_attempt()
    assert prior["prior_incident_absent"] is True
    assert prior["prior_lock_absent"] is True
    transcript = recovery._validate_transcript_receipts()
    assert transcript["73868"]["evidence_role"] == "SAFE_READ_ONLY_PRIOR_PREFLIGHT_REJECTION"
    assert transcript["frozen_unrelated_oversized_records"] == [
        {
            "physical_line_number": 43_244,
            "size_bytes": 6_660_546,
            "sha256": "7533a5f64039cbe142f11c7a0e3d3101e9448bfb09b26ece17e39ff0fa6c770b",
            "externally_diagnosed_ordinal": 43_241,
            "type": "response_item",
        }
    ]


def test_preflight_main_does_not_create_or_open_lock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(recovery, "_validate_parent", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        recovery,
        "recover",
        lambda *_args, **_kwargs: {"status": "PASS_READ_ONLY_NO_PUBLICATION_NO_MUTATION"},
    )
    monkeypatch.setattr(
        recovery.os,
        "open",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("lock was opened")),
    )
    assert recovery.main(["--expected-script-sha256", "0" * 64, "--preflight-only"]) == 0


def test_historical_builder_callsite_and_digest_are_still_exact() -> None:
    historical = Path("/var/lib/telemetry-yield-confirmatory-v3/build_runtime_guard_v3.py")
    if not historical.exists():
        pytest.skip("historical root-control builder is not present on this host")
    payload = historical.read_bytes()
    assert hashlib.sha256(payload).hexdigest() == recovery.HISTORICAL_BUILDER_SHA256
    assert len(payload) == recovery.HISTORICAL_BUILDER_SIZE
    assert payload.decode().splitlines()[recovery.HISTORICAL_OPEN_LINE - 1].strip().startswith(
        "parent_fds[label] = os.open(path,"
    )
