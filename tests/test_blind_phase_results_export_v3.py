from __future__ import annotations

import errno
import hashlib
import importlib.util
import os
from pathlib import Path
import stat
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "work/blind-phase-confirmatory-v2/build_runtime_guard_v3.py"


def _module():
    name = "blind_phase_runtime_guard_builder_v3_results_export_test_module"
    spec = importlib.util.spec_from_file_location(name, SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


BUILDER = _module()


def _mode_tree(root: Path, *, directory: int = 0o700, file: int = 0o444) -> None:
    for current, directories, files in os.walk(root, topdown=False):
        for name in files:
            (Path(current) / name).chmod(file)
        for name in directories:
            (Path(current) / name).chmod(directory)
    root.chmod(directory)


def _identity(path: Path, payload: bytes = b"x") -> dict[str, object]:
    return {
        "path": str(path.absolute()),
        "size_bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }


def test_results_manifest_binds_exact_tree_and_file_hashes(tmp_path: Path) -> None:
    root = tmp_path / "results"
    nested = root / "units" / "unit-a"
    nested.mkdir(parents=True, mode=0o700)
    payload = b'{"status":"complete"}'
    result = nested / "normalized-result.json"
    result.write_bytes(payload)
    _mode_tree(root)
    manifest = BUILDER._results_tree_manifest(
        root, owner_uid=os.geteuid(), owner_gid=os.getegid(), directory_mode=0o700
    )
    assert manifest["entry_count"] == 3
    assert manifest["records"][-1] == {
        "path": "units/unit-a/normalized-result.json",
        "kind": "file",
        "size_bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }
    assert manifest["semantic_sha256"] == BUILDER._sha256_document(
        manifest["records"]
    )


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "fifo"])
def test_results_manifest_rejects_alias_or_special_entry(
    tmp_path: Path, kind: str
) -> None:
    root = tmp_path / "results"
    root.mkdir(mode=0o700)
    ordinary = root / "ordinary.json"
    ordinary.write_bytes(b"{}")
    ordinary.chmod(0o444)
    if kind == "symlink":
        (root / "unsafe").symlink_to(ordinary.name)
    elif kind == "hardlink":
        os.link(ordinary, root / "alias.json")
    else:
        os.mkfifo(root / "unsafe")
    with pytest.raises(ValueError, match="(?i)(symlink|special|hardlink|mutable|unsafe)"):
        BUILDER._results_tree_manifest(
            root,
            owner_uid=os.geteuid(),
            owner_gid=os.getegid(),
            directory_mode=0o700,
        )


def test_results_copy_uses_pinned_source_fd_and_rejects_path_swap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source.json"
    destination = tmp_path / "destination.json"
    source.write_bytes(b"original exact bytes")
    source.chmod(0o444)
    original_read = BUILDER.os.read
    swapped = False

    def swap_after_open(descriptor: int, size: int) -> bytes:
        nonlocal swapped
        if not swapped:
            swapped = True
            parked = source.with_name("parked.json")
            source.rename(parked)
            source.write_bytes(b"host-path replacement")
            source.chmod(0o444)
        return original_read(descriptor, size)

    monkeypatch.setattr(BUILDER.os, "read", swap_after_open)
    with pytest.raises(ValueError, match="changed"):
        BUILDER._copy_regular_unique(str(source), str(destination))
    assert not destination.exists()


def test_results_copy_never_clobbers_existing_destination(tmp_path: Path) -> None:
    source = tmp_path / "source.json"
    destination = tmp_path / "destination.json"
    source.write_bytes(b"source")
    source.chmod(0o444)
    destination.write_bytes(b"preexisting")
    before = destination.read_bytes()
    with pytest.raises((FileExistsError, ValueError)):
        BUILDER._copy_regular_unique(str(source), str(destination))
    assert destination.read_bytes() == before


def test_export_refuses_foreign_namespace_process_before_tree_read_or_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    control = tmp_path / "control"
    output = tmp_path / "results"
    control.mkdir()
    output.mkdir()
    _patch_export_prelude(monkeypatch, control=control, output=output)
    monkeypatch.setattr(
        BUILDER,
        "_assert_keeper_is_sole_namespace_holder",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            ValueError("foreign namespace process")
        ),
    )
    reads: list[object] = []
    monkeypatch.setattr(
        BUILDER, "_results_tree_manifest", lambda *_args, **_kwargs: reads.append(True)
    )
    copies: list[object] = []
    monkeypatch.setattr(
        BUILDER.shutil, "copytree", lambda *_args, **_kwargs: copies.append(True)
    )
    with pytest.raises(ValueError, match="foreign namespace process"):
        BUILDER.export_results(
            context={}, control_parent=control, output_parent=output,
            campaign_completion_path=control / "campaign-completion.json",
            expected_campaign_completion_sha256="a" * 64,
            evaluator_completion_path=control / "evaluator-completion.json",
            expected_evaluator_completion_sha256="b" * 64,
        )
    assert reads == []
    assert copies == []


def test_export_refuses_nested_results_mount_before_tree_read_or_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    control = tmp_path / "control"
    output = tmp_path / "results"
    control.mkdir()
    output.mkdir()
    _patch_export_prelude(monkeypatch, control=control, output=output)
    monkeypatch.setattr(
        BUILDER,
        "_results_authority_identity",
        lambda _path: (_ for _ in ()).throw(
            ValueError("campaign results authority identity is ambiguous")
        ),
    )
    reads: list[object] = []
    monkeypatch.setattr(
        BUILDER, "_results_tree_manifest", lambda *_args, **_kwargs: reads.append(True)
    )
    copies: list[object] = []
    monkeypatch.setattr(
        BUILDER.shutil, "copytree", lambda *_args, **_kwargs: copies.append(True)
    )
    with pytest.raises(ValueError, match="authority identity is ambiguous"):
        BUILDER.export_results(
            context={}, control_parent=control, output_parent=output,
            campaign_completion_path=control / "campaign-completion.json",
            expected_campaign_completion_sha256="a" * 64,
            evaluator_completion_path=control / "evaluator-completion.json",
            expected_evaluator_completion_sha256="b" * 64,
        )
    assert reads == []
    assert copies == []


def _patch_export_prelude(
    monkeypatch: pytest.MonkeyPatch,
    *, control: Path,
    output: Path,
) -> tuple[dict[str, object], dict[str, object], dict[str, object]]:
    source_mount = {
        "path": str(output), "st_dev": 1, "st_ino": 2,
        "uid": 1000, "gid": 1000, "mode": 0o700, "mount_record": {},
    }
    capacity_contract = {
        "schema_version": BUILDER.CAPACITY_CONTRACT_SCHEMA,
        "results_tmpfs_capacity_bytes": BUILDER.RESULTS_TMPFS_CAPACITY_BYTES,
        "results_minimum_available_bytes_before_work": (
            BUILDER.RESULTS_MINIMUM_AVAILABLE_BYTES_BEFORE_WORK
        ),
        "available_bytes_formula": "statvfs.f_bavail*statvfs.f_frsize",
        "persistent_export_filesystem_minimum_available_bytes": (
            BUILDER.PERSISTENT_EXPORT_MINIMUM_AVAILABLE_BYTES
        ),
    }
    campaign_identity = _identity(control / "campaign-completion.json")
    evaluator_identity = _identity(control / "evaluator-completion.json")
    campaign = {
        "campaign_completion": None,
        "role_output_root": str(output / "run"),
        "results_authority_mount": source_mount,
        "capacity_contract": capacity_contract,
    }
    evaluator = {
        "campaign_completion": campaign_identity,
        "role_output_root": str(output / "run"),
        "results_authority_mount": source_mount,
        "capacity_contract": capacity_contract,
    }
    monkeypatch.setattr(BUILDER.os, "geteuid", lambda: 0)
    monkeypatch.setattr(BUILDER, "_assert_execution_namespace", lambda: None)
    monkeypatch.setattr(
        BUILDER,
        "_validate_active_namespace",
        lambda _control: ({"keeper": {"pid": 123, "starttime_ticks": 456}}, {}),
    )
    monkeypatch.setattr(
        BUILDER,
        "_assert_keeper_is_sole_namespace_holder",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(BUILDER, "_results_authority_identity", lambda _path: source_mount)
    monkeypatch.setattr(BUILDER, "_capacity_contract", lambda _context: capacity_contract)
    monkeypatch.setattr(
        BUILDER,
        "_statvfs_bytes",
        lambda _path: {
            "total_bytes": 16 * 1024**3,
            "available_bytes": BUILDER.PERSISTENT_EXPORT_MINIMUM_AVAILABLE_BYTES,
        },
    )
    monkeypatch.setattr(BUILDER, "_results_process_references", lambda _path: [])
    monkeypatch.setattr(BUILDER, "_contract_metadata", lambda _context: {"frozen": True})

    def completion(_path: Path, _sha: str, *, role: str, control_parent: Path):
        return (
            (campaign, campaign_identity)
            if role == "campaign"
            else (evaluator, evaluator_identity)
        )

    monkeypatch.setattr(BUILDER, "_validate_successful_role_completion", completion)
    return source_mount, campaign_identity, evaluator_identity


def test_existing_pass_attestation_is_idempotent_without_copy_or_new_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    control = tmp_path / "control"
    output = tmp_path / "results"
    control.mkdir()
    output.mkdir()
    source_mount, campaign_identity, evaluator_identity = _patch_export_prelude(
        monkeypatch, control=control, output=output
    )
    capacity_contract = BUILDER._capacity_contract({})
    attestation_path = control / BUILDER.RESULTS_EXPORT_ATTESTATION_NAME
    attestation_path.write_bytes(b"existing immutable attestation")
    attestation_identity = _identity(
        attestation_path, b"existing immutable attestation"
    )
    existing = {
        "campaign_completion": campaign_identity,
        "evaluator_completion": evaluator_identity,
        "source_mount": source_mount,
        "campaign_role_output_attestation": None,
        "campaign_role_output": None,
        "evaluator_role_output_attestation": None,
        "evaluator_role_output": None,
        "capacity_contract": capacity_contract,
    }
    monkeypatch.setattr(
        BUILDER,
        "_validate_results_export_attestation",
        lambda *_args, **_kwargs: attestation_identity,
    )
    monkeypatch.setattr(
        BUILDER,
        "_load_repair_selfhashed",
        lambda *_args, **_kwargs: (existing, attestation_identity),
    )
    monkeypatch.setattr(
        BUILDER,
        "_results_tree_manifest",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("idempotent retry must not rescan results")
        ),
    )
    monkeypatch.setattr(
        BUILDER.shutil,
        "copytree",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("idempotent retry must not recopy results")
        ),
    )
    published: list[object] = []
    monkeypatch.setattr(
        BUILDER,
        "_publish_contract_recoverable",
        lambda *_args, **_kwargs: published.append(True),
    )
    result = BUILDER.export_results(
        context={}, control_parent=control, output_parent=output,
        campaign_completion_path=control / "campaign-completion.json",
        expected_campaign_completion_sha256="a" * 64,
        evaluator_completion_path=control / "evaluator-completion.json",
        expected_evaluator_completion_sha256="b" * 64,
    )
    assert result == {
        "status": "PASS", "results_export_attestation": attestation_identity
    }
    assert published == []


def test_enospc_publishes_failure_receipt_and_never_false_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    control = tmp_path / "control"
    output = tmp_path / "results"
    control.mkdir()
    output.mkdir()
    _patch_export_prelude(monkeypatch, control=control, output=output)
    source_manifest = {
        "root": {}, "entry_count": 0, "records": [],
        "semantic_sha256": BUILDER._sha256_document([]),
    }
    monkeypatch.setattr(BUILDER, "_results_tree_manifest", lambda *_args, **_kwargs: source_manifest)
    monkeypatch.setattr(
        BUILDER.shutil,
        "copytree",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            OSError(errno.ENOSPC, "no space left")
        ),
    )
    published: list[tuple[Path, dict[str, object]]] = []
    monkeypatch.setattr(
        BUILDER,
        "_publish_contract_recoverable",
        lambda path, document: published.append((Path(path), dict(document))),
    )
    with pytest.raises(OSError) as raised:
        BUILDER.export_results(
            context={}, control_parent=control, output_parent=output,
            campaign_completion_path=control / "campaign-completion.json",
            expected_campaign_completion_sha256="a" * 64,
            evaluator_completion_path=control / "evaluator-completion.json",
            expected_evaluator_completion_sha256="b" * 64,
        )
    assert raised.value.errno == errno.ENOSPC
    assert any(document.get("status") == "intent_before_copy" for _, document in published)
    assert any(document.get("status") == "FAIL" for _, document in published)
    assert not any(document.get("status") == "PASS" for _, document in published)


def test_stale_partial_is_quarantined_before_retry_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    control = tmp_path / "control"
    output = tmp_path / "results"
    control.mkdir()
    output.mkdir()
    stage = control / ".results-export-v3-stage"
    stage.mkdir()
    (stage / "partial").write_bytes(b"partial")
    _patch_export_prelude(monkeypatch, control=control, output=output)
    source_manifest = {
        "root": {}, "entry_count": 0, "records": [],
        "semantic_sha256": BUILDER._sha256_document([]),
    }
    monkeypatch.setattr(BUILDER, "_results_tree_manifest", lambda *_args, **_kwargs: source_manifest)
    monkeypatch.setattr(BUILDER, "_publish_contract_recoverable", lambda *_args: None)
    quarantined: list[Path] = []
    original_rename = BUILDER._rename_noreplace

    def record_rename(source: Path, destination: Path) -> None:
        if source == stage:
            quarantined.append(destination)
            original_rename(source, destination)
        else:
            original_rename(source, destination)

    monkeypatch.setattr(BUILDER, "_rename_noreplace", record_rename)
    monkeypatch.setattr(
        BUILDER.shutil,
        "copytree",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("crash")),
    )
    with pytest.raises(RuntimeError, match="crash"):
        BUILDER.export_results(
            context={}, control_parent=control, output_parent=output,
            campaign_completion_path=control / "campaign-completion.json",
            expected_campaign_completion_sha256="a" * 64,
            evaluator_completion_path=control / "evaluator-completion.json",
            expected_evaluator_completion_sha256="b" * 64,
        )
    assert not stage.exists()
    assert len(quarantined) == 1
    assert quarantined[0].name.startswith("results-export-v3-incomplete-")
    assert (quarantined[0] / "partial").read_bytes() == b"partial"


def test_stop_refuses_before_export_attestation_without_signaling_keeper(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    control = tmp_path / "control"
    control.mkdir()
    signal_attempts: list[object] = []
    monkeypatch.setattr(BUILDER.os, "geteuid", lambda: 0)
    monkeypatch.setattr(BUILDER, "_validate_control_parent", lambda path, create: path)
    monkeypatch.setattr(BUILDER, "_assert_execution_namespace", lambda: None)
    monkeypatch.setattr(
        BUILDER,
        "_load_namespace_document",
        lambda *_args, **_kwargs: (
            {
                "campaign_metadata": {"frozen": True},
                "keeper": {"pid": 123, "mount_namespace_inode": 456},
            },
            {},
        ),
    )
    monkeypatch.setattr(
        BUILDER,
        "_validate_results_export_attestation",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            ValueError("results export attestation absent")
        ),
    )
    monkeypatch.setattr(
        BUILDER.signal, "pidfd_send_signal", lambda *_args: signal_attempts.append(True)
    )
    with pytest.raises(ValueError, match="attestation absent"):
        BUILDER.stop_persistent_namespace(control, {"frozen": True})
    assert signal_attempts == []


@pytest.mark.parametrize("invalid_leaf", ["manifest", "receipt"])
def test_export_attestation_rejects_noncanonical_transitive_control_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, invalid_leaf: str
) -> None:
    control = tmp_path / "control"
    alias = tmp_path / "alias"
    control.mkdir()
    alias.mkdir()
    attestation_path = control / BUILDER.RESULTS_EXPORT_ATTESTATION_NAME
    manifest_path = (
        alias / BUILDER.RESULTS_EXPORT_MANIFEST_NAME
        if invalid_leaf == "manifest"
        else control / BUILDER.RESULTS_EXPORT_MANIFEST_NAME
    )
    receipt_name = "results-export-v3-execution-receipt-" + "c" * 64 + ".json"
    receipt_path = (
        alias / receipt_name if invalid_leaf == "receipt" else control / receipt_name
    )
    attestation_identity = _identity(attestation_path)
    manifest_identity = _identity(manifest_path)
    receipt_identity = _identity(receipt_path)
    role_identity = _identity(control / "role-output.json")
    role_bindings = {
        "campaign_role_output_attestation": role_identity,
        "campaign_role_output": role_identity,
        "evaluator_role_output_attestation": role_identity,
        "evaluator_role_output": role_identity,
    }
    attestation = {
        "status": "PASS", "campaign_metadata": {"frozen": True},
        "campaign_completion": role_identity,
        "evaluator_completion": role_identity,
        "source_mount": {"path": "/private/results"},
        "tree_manifest": manifest_identity,
        "execution_receipt": receipt_identity,
        **role_bindings,
    }
    manifest = {
        "status": "PASS", "campaign_completion": role_identity,
        "evaluator_completion": role_identity,
        "source_mount": attestation["source_mount"],
        "records": [], "entry_count": 0,
        "semantic_sha256": BUILDER._sha256_document([]),
        **role_bindings,
    }
    receipt = {
        "status": "PASS", "campaign_completion": role_identity,
        "evaluator_completion": role_identity,
        "tree_manifest": manifest_identity,
    }

    def load(path: Path, **_kwargs):
        documents = {
            attestation_path: (attestation, attestation_identity),
            manifest_path: (manifest, manifest_identity),
            receipt_path: (receipt, receipt_identity),
        }
        return documents[Path(path)]

    monkeypatch.setattr(BUILDER, "_load_repair_selfhashed", load)
    monkeypatch.setattr(
        BUILDER,
        "_root_control_artifact_identity",
        lambda path, _control: {
            attestation_path: attestation_identity,
            control / BUILDER.RESULTS_EXPORT_MANIFEST_NAME: manifest_identity,
            control / receipt_name: receipt_identity,
        }.get(Path(path), _identity(Path(path))),
    )
    monkeypatch.setattr(
        BUILDER,
        "_results_tree_manifest",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("noncanonical transitive artifact must fail before tree read")
        ),
    )
    with pytest.raises(ValueError, match="receipt/manifest binding differs"):
        BUILDER._validate_results_export_attestation(
            control, {"frozen": True}, require_live_source=False
        )


def test_dead_keeper_stop_recovers_only_from_exact_durable_intent_and_export(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    control = tmp_path / "control"
    control.mkdir()
    paths = BUILDER._namespace_contract_paths(control)
    paths["stop_intent"].write_bytes(b"durable exact stop intent")
    metadata = {"frozen": True}
    keeper = {"pid": 123, "mount_namespace_inode": 456}
    active_identity = _identity(paths["active"])
    export_identity = _identity(control / BUILDER.RESULTS_EXPORT_ATTESTATION_NAME)
    inventory = {"namespaces": ["unchanged"]}
    active = {"campaign_metadata": metadata, "keeper": keeper}
    stop_intent = {
        "campaign_metadata": metadata,
        "active_contract": active_identity,
        "keeper": keeper,
        "project_mount_count": 0,
        "namespace_fd_handle_count": 0,
        "results_export_attestation": export_identity,
        "external_inventory_before": inventory,
    }
    monkeypatch.setattr(BUILDER.os, "geteuid", lambda: 0)
    monkeypatch.setattr(BUILDER, "_validate_control_parent", lambda path, create: path)

    def load(path: Path, *, status: str):
        if Path(path) == paths["active"]:
            return active, active_identity
        if Path(path) == paths["stop_intent"]:
            return stop_intent, _identity(paths["stop_intent"], b"durable exact stop intent")
        raise AssertionError((path, status))

    monkeypatch.setattr(BUILDER, "_load_namespace_document", load)
    monkeypatch.setattr(
        BUILDER,
        "_keeper_live_identity",
        lambda _pid: (_ for _ in ()).throw(FileNotFoundError("keeper exited")),
    )
    monkeypatch.setattr(
        BUILDER,
        "_validate_results_export_attestation",
        lambda *_args, **_kwargs: export_identity,
    )
    monkeypatch.setattr(BUILDER, "_capture_external_fixed_point", lambda **_kwargs: inventory)
    monkeypatch.setattr(
        BUILDER,
        "_enter_persistent_namespace",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("dead-keeper recovery must not attempt setns")
        ),
    )
    signals: list[object] = []
    monkeypatch.setattr(
        BUILDER.signal, "pidfd_send_signal", lambda *_args: signals.append(True)
    )
    published: list[tuple[Path, dict[str, object]]] = []
    monkeypatch.setattr(
        BUILDER,
        "_publish_contract_recoverable",
        lambda path, document: published.append((Path(path), dict(document))),
    )
    # The recovery CLI intentionally supplies no project-derived metadata; the
    # immutable active root-control contract is the sole metadata authority.
    stopped = BUILDER.stop_persistent_namespace(control)
    assert stopped["status"] == "stopped_after_empty_namespace_attestation"
    assert stopped["termination_inferred_after_durable_stop_intent"] is True
    assert stopped["results_export_attestation"] == export_identity
    assert published == [(paths["stopped"], stopped)]
    assert signals == []


def test_dead_keeper_without_durable_stop_intent_fails_without_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    control = tmp_path / "control"
    control.mkdir()
    paths = BUILDER._namespace_contract_paths(control)
    metadata = {"frozen": True}
    keeper = {"pid": 123, "mount_namespace_inode": 456}
    monkeypatch.setattr(BUILDER.os, "geteuid", lambda: 0)
    monkeypatch.setattr(BUILDER, "_validate_control_parent", lambda path, create: path)
    monkeypatch.setattr(
        BUILDER,
        "_load_namespace_document",
        lambda *_args, **_kwargs: (
            {"campaign_metadata": metadata, "keeper": keeper},
            _identity(paths["active"]),
        ),
    )
    monkeypatch.setattr(
        BUILDER,
        "_keeper_live_identity",
        lambda _pid: (_ for _ in ()).throw(FileNotFoundError("keeper exited")),
    )
    monkeypatch.setattr(
        BUILDER,
        "_validate_results_export_attestation",
        lambda *_args, **_kwargs: _identity(
            control / BUILDER.RESULTS_EXPORT_ATTESTATION_NAME
        ),
    )
    published: list[object] = []
    monkeypatch.setattr(
        BUILDER,
        "_publish_contract_recoverable",
        lambda *_args, **_kwargs: published.append(True),
    )
    with pytest.raises(ValueError, match="vanished without a durable exact stop intent"):
        BUILDER.stop_persistent_namespace(control)
    assert published == []


def test_results_export_schema_names_are_versioned_and_distinct() -> None:
    assert BUILDER.RESULTS_EXPORT_SCHEMA == "blind-phase-confirmatory-results-export-v3"
    assert BUILDER.RESULTS_EXPORT_MANIFEST_SCHEMA.endswith("-manifest-v3")
    assert BUILDER.RESULTS_EXPORT_RECEIPT_SCHEMA.endswith("-receipt-v3")
    assert len(
        {
            BUILDER.RESULTS_EXPORT_SCHEMA,
            BUILDER.RESULTS_EXPORT_MANIFEST_SCHEMA,
            BUILDER.RESULTS_EXPORT_RECEIPT_SCHEMA,
        }
    ) == 3


@pytest.mark.skipif(
    os.geteuid() != 0
    or os.environ.get("TELEMETRY_YIELD_RUN_ROOT_EXPORT_TEST") != "1",
    reason="requires explicit root opt-in for chown in a throwaway pytest tree",
)
def test_root_sealed_export_tree_has_exact_immutable_metadata(tmp_path: Path) -> None:
    root = tmp_path / "results-export"
    nested = root / "units" / "unit-a"
    nested.mkdir(parents=True, mode=0o700)
    output = nested / "normalized-result.json"
    output.write_bytes(b'{"status":"complete"}')
    _mode_tree(root, directory=0o700, file=0o444)
    BUILDER._seal_export_tree(root)
    manifest = BUILDER._results_tree_manifest(
        root, owner_uid=0, owner_gid=0, directory_mode=0o555
    )
    assert manifest["entry_count"] == 3
    for current, directories, files in os.walk(root):
        assert Path(current).lstat().st_uid == 0
        assert stat.S_IMODE(Path(current).lstat().st_mode) == 0o555
        for name in files:
            status = (Path(current) / name).lstat()
            assert status.st_uid == 0
            assert stat.S_IMODE(status.st_mode) == 0o444
            assert status.st_nlink == 1
