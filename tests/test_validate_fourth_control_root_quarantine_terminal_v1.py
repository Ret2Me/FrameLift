from __future__ import annotations

import ast
import copy
import importlib.util
import os
from pathlib import Path
import stat
import types

import pytest


SOURCE = (
    Path(__file__).parents[1]
    / "work/blind-phase-confirmatory-v2/validate_fourth_control_root_quarantine_terminal_v1.py"
)
SPEC = importlib.util.spec_from_file_location(
    "validate_fourth_control_root_quarantine_terminal_v1", SOURCE
)
assert SPEC is not None and SPEC.loader is not None
V = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(V)


def simple(path: Path, digest: str, size: int) -> dict[str, object]:
    return {"path": str(path), "sha256": digest, "size_bytes": size}


def test_validator_has_full_attempt_siblings_and_no_mutation_primitive() -> None:
    assert V.ATTEMPT_ID == (
        "4aa20676a1940685d1a9dad01ba580840d6608f4df32cc73ed838d469fc1b478"
    )
    assert V.VALIDATOR_CONTROL.parent == V.HISTORICAL_CONTROL.parent
    assert V.VALIDATOR_CONTROL not in {V.HISTORICAL_CONTROL, V.DESTINATION}
    assert V.ATTEMPT_ID in V.VALIDATOR_CONTROL.name
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    attributes = {
        node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
    }
    assert not {
        "write", "pwrite", "rename", "replace", "renameat2", "unlink",
        "remove", "rmdir", "removedirs", "mkdir", "makedirs", "chmod",
        "chown", "fchmod", "fchown", "flock",
    } & attributes


def test_historical_inventory_requires_exact_nine_names(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    parent = copy.deepcopy(V.EXPECTED_HISTORICAL_CONTROL)
    status = tmp_path.lstat()
    parent.update(
        size_bytes=status.st_size, mtime_ns=status.st_mtime_ns,
        ctime_ns=status.st_ctime_ns,
    )
    monkeypatch.setattr(
        V, "_open_directory", lambda _path: (
            os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY),
            [*V.HISTORICAL_ARTIFACTS, "foreign"], parent,
        )
    )
    with pytest.raises(V.ValidationError, match="inventory differs"):
        V._validate_historical_inventory()


def test_historical_inventory_rejects_artifact_digest_drift(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    parent = copy.deepcopy(V.EXPECTED_HISTORICAL_CONTROL)
    status = tmp_path.lstat()
    parent.update(
        size_bytes=status.st_size, mtime_ns=status.st_mtime_ns,
        ctime_ns=status.st_ctime_ns,
    )
    monkeypatch.setattr(
        V, "_open_directory", lambda _path: (
            os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY),
            list(V.HISTORICAL_ARTIFACTS), parent,
        )
    )

    def read(path: Path, _maximum: int = V.MAX_BYTES, **_kwargs):
        digest, size, mode = V.HISTORICAL_ARTIFACTS[path.name]
        if path.name == V.HISTORICAL_PROVENANCE.name:
            digest = "0" * 64
        return b"", {
            "path": str(path), "sha256": digest, "size_bytes": size,
            "st_dev": 64_512, "st_ino": V.HISTORICAL_INODES[path.name],
            "uid": 0, "gid": 0, "mode": mode, "nlink": 1,
        }

    monkeypatch.setattr(V, "_read_regular", read)
    with pytest.raises(V.ValidationError, match="historical artifact differs"):
        V._validate_historical_inventory()


def test_historical_composite_scan_rejects_parent_swap_at_child_boundary(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    historical = tmp_path / "historical"
    replacement = tmp_path / "replacement"
    displaced = tmp_path / "displaced"
    historical.mkdir()
    replacement.mkdir()
    name = "evidence.json"
    payload = b"frozen"
    (historical / name).write_bytes(payload)
    (historical / name).chmod(0o444)
    (replacement / name).write_bytes(b"replacement")
    original_status = historical.lstat()
    original_inode = (historical / name).lstat().st_ino
    monkeypatch.setattr(V, "HISTORICAL_CONTROL", historical)
    monkeypatch.setattr(
        V, "EXPECTED_HISTORICAL_CONTROL",
        {
            "path": str(historical), "st_dev": original_status.st_dev,
            "st_ino": original_status.st_ino, "uid": original_status.st_uid,
            "gid": original_status.st_gid,
            "mode": stat.S_IMODE(original_status.st_mode),
            "nlink": original_status.st_nlink, "entry_count": 1,
        },
    )
    monkeypatch.setattr(
        V, "HISTORICAL_ARTIFACTS",
        {name: (V._sha256(payload), len(payload), 0o444)},
    )
    monkeypatch.setattr(V, "HISTORICAL_INODES", {name: original_inode})
    real_read = V._read_regular
    swapped = False

    def read(path: Path, maximum: int = V.MAX_BYTES, *, directory_fd=None):
        nonlocal swapped
        if not swapped:
            historical.rename(displaced)
            replacement.rename(historical)
            swapped = True
        data, identity = real_read(path, maximum, directory_fd=directory_fd)
        identity["uid"] = 0
        identity["gid"] = 0
        return data, identity

    monkeypatch.setattr(V, "_read_regular", read)
    with pytest.raises(V.ValidationError, match="path changed during composite scan"):
        V._validate_historical_inventory()


def test_var_lib_sibling_grammar_rejects_extra_full_attempt_collision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    names = [
        "unrelated", V.DESTINATION.name, V.HISTORICAL_CONTROL.name,
        V.VALIDATOR_CONTROL.name,
        "telemetry-yield-confirmatory-v3-aborted-fourth-freeze-" + "0" * 64,
    ]
    parent = {
        "path": str(V.VAR_LIB), "st_dev": 64_512, "st_ino": 1_179_656,
        "uid": 0, "gid": 0, "mode": 0o755, "nlink": 1,
        "entry_count": len(names),
    }
    monkeypatch.setattr(V, "_directory", lambda _path: (sorted(names), parent))
    with pytest.raises(V.ValidationError, match="collision-domain grammar"):
        V._validate_var_lib_siblings()


def test_validator_toolchain_rejects_missing_or_foreign_name(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setattr(V, "VALIDATOR_CONTROL", tmp_path)
    expected_parent = {**V._identity(tmp_path, tmp_path.lstat()), "entry_count": 3}
    descriptor = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    monkeypatch.setattr(
        V, "_open_directory",
        lambda _path: (descriptor, ["runner.py", "test.py", "foreign"], expected_parent),
    )
    with pytest.raises(V.ValidationError, match="inventory differs"):
        V._validate_validator_toolchain({}, expected_parent)


def test_validator_toolchain_rejects_review_binding_mismatch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    runner = tmp_path / "validator.py"
    test = tmp_path / "validator-test.py"
    review = tmp_path / "validator-review.json"
    sidecar = tmp_path / "validator-review.json.sha256"
    for path in (runner, test, review, sidecar):
        path.write_bytes(b"")
    monkeypatch.setattr(V, "VALIDATOR_CONTROL", tmp_path)
    monkeypatch.setattr(V, "VALIDATOR_COPY", runner)
    monkeypatch.setattr(V, "VALIDATOR_TEST", test)
    monkeypatch.setattr(V, "VALIDATOR_REVIEW", review)
    expected_parent = {**V._identity(tmp_path, tmp_path.lstat()), "entry_count": 4}
    runner_payload = b"runner"
    runner_sha = V._sha256(runner_payload)
    test_sha = V._sha256(b"test")

    def read(path: Path, _maximum: int = V.MAX_BYTES, **_kwargs):
        if path == runner:
            payload, digest, mode = runner_payload, runner_sha, 0o555
        else:
            payload, digest, mode = b"test", test_sha, 0o555
        return payload, {
            "path": str(path), "sha256": digest, "size_bytes": len(payload),
            "st_dev": 1, "st_ino": 2, "uid": 0, "gid": 0,
            "mode": mode, "nlink": 1, "mtime_ns": 1, "ctime_ns": 1,
        }

    monkeypatch.setattr(V, "_read_regular", read)
    monkeypatch.setattr(
        V, "_load_selfhashed",
        lambda *_args, **_kwargs: (
            {
                "schema_version": (
                    "blind-phase-confirmatory-fourth-control-quarantine-"
                    "terminal-validator-independent-review-v1"
                ),
                "status": "GO", "findings": {"P0": 0, "P1": 0, "P2": 0},
                "reviewed_bindings": {"terminal_validator": {"wrong": True}},
            },
            {"path": str(review), "sha256": "3" * 64, "size_bytes": 1},
        ),
    )
    expected = {
        "validator_sha256": runner_sha,
        "validator_test_sha256": test_sha,
        "validator_review_sha256": "3" * 64,
    }
    with pytest.raises(V.ValidationError, match="independent review differs"):
        V._validate_validator_toolchain(expected, expected_parent)


def terminal_fixture(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> tuple[types.SimpleNamespace, dict[str, object]]:
    destination = tmp_path / "destination"
    destination.mkdir()
    source = tmp_path / "canonical"
    monkeypatch.setattr(V, "DESTINATION", destination)
    monkeypatch.setattr(V, "SOURCE", source)
    status = destination.lstat()
    monkeypatch.setattr(
        V,
        "EXPECTED_DESTINATION",
        {
            "path": str(destination), "st_dev": status.st_dev,
            "st_ino": status.st_ino, "uid": status.st_uid,
            "gid": status.st_gid, "mode": stat.S_IMODE(status.st_mode),
            "nlink": status.st_nlink,
        },
    )
    zero = {
        "keeper_absent": True, "relevant_mount_count": 0,
        "role_decoder_evaluator_processes": {
            "namespace_member_pids": [], "namespace_fd_handles": [],
            "suspicious_processes": [],
        },
        "external_inventory_sha256": "c" * 64,
    }
    historical_tools = {
        "runner": simple(V.HISTORICAL_RUNNER, V.HISTORICAL_ARTIFACTS[V.HISTORICAL_RUNNER.name][0], 52_244),
        "runner_test": simple(V.HISTORICAL_TEST, V.HISTORICAL_ARTIFACTS[V.HISTORICAL_TEST.name][0], 25_638),
        "independent_review": simple(V.HISTORICAL_REVIEW, V.HISTORICAL_ARTIFACTS[V.HISTORICAL_REVIEW.name][0], 7_649),
    }
    intent_identity = simple(V.HISTORICAL_INTENT, V.HISTORICAL_ARTIFACTS[V.HISTORICAL_INTENT.name][0], 44_662)
    provenance_identity = simple(V.HISTORICAL_PROVENANCE, V.HISTORICAL_ARTIFACTS[V.HISTORICAL_PROVENANCE.name][0], 67_372)
    intent = {
        "quarantine_intent_payload_sha256": V.HISTORICAL_INTENT_PAYLOAD_SHA256,
        "stage2": {
            "external_inventory_sha256": "c" * 64,
            "result": {"path": "/stage2/result", "sha256": "d" * 64, "size_bytes": 1},
        },
        "zero_runtime_state": zero,
        "pre_rename_snapshot": {"root": "snapshot"},
    }
    provenance = {
        "quarantine_payload_sha256": V.HISTORICAL_PROVENANCE_PAYLOAD_SHA256,
    }
    calls: list[object] = []
    parent = {
        "path": str(V.HISTORICAL_CONTROL), "st_dev": 64_512,
        "st_ino": 1_572_866, "uid": 0, "gid": 0, "mode": 0o755,
    }

    def load(path: Path, _field: str):
        calls.append(("load", path))
        if path == V.HISTORICAL_INTENT:
            return intent, intent_identity
        return provenance, provenance_identity

    module = types.SimpleNamespace(
        _controller_parent=lambda _expected: parent,
        _validate_toolchain=lambda _expected, _parent: historical_tools,
        _validate_controller_inventory=lambda *_args, **_kwargs: calls.append("inventory"),
        _load_selfhashed=load,
        _intent_semantics=lambda value, **_kwargs: calls.append(("intent", value)),
        _validate_stage2_snapshot=lambda value: calls.append(("stage2", value)),
        _load_stage2_module=lambda: object(),
        _zero_runtime_state=lambda *_args: copy.deepcopy(zero),
        _root_state=lambda: "DESTINATION_QUARANTINED",
        _validate_snapshot_at_root=lambda frozen, root: {
            "frozen": frozen, "root": str(root)
        },
        _provenance_semantics=lambda value, **kwargs: calls.append(
            ("provenance", value, kwargs)
        ),
        _sha256_document=lambda value: (
            V.EXPECTED_PRE_RENAME_CONTROL_SNAPSHOT_SHA256
            if value == intent["pre_rename_snapshot"]
            else V.EXPECTED_POST_RENAME_CONTROL_SNAPSHOT_SHA256
        ),
    )
    module.calls = calls
    return module, zero


def test_terminal_projection_loads_and_semantically_validates_complete_pairs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    module, zero = terminal_fixture(monkeypatch, tmp_path)
    validator_tools = {"terminal_validator": {"sha256": "f" * 64}}
    result = V._terminal_projection(module, validator_tools)
    assert result["durable_prefix_length"] == 2
    assert result["provenance_committed"] is True
    assert result["validated_partial_provenance"] is False
    assert result["zero_runtime_state"] == zero
    assert ("load", V.HISTORICAL_INTENT) in module.calls
    assert ("load", V.HISTORICAL_PROVENANCE) in module.calls
    assert len([call for call in module.calls if call == "inventory"]) == 2
    provenance_calls = [
        call for call in module.calls
        if isinstance(call, tuple) and call[0] == "provenance"
    ]
    assert len(provenance_calls) == 1
    assert provenance_calls[0][2]["intent_identity"]["sha256"] == (
        V.HISTORICAL_ARTIFACTS[V.HISTORICAL_INTENT.name][0]
    )


def test_terminal_projection_rejects_recreated_canonical_source(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    module, _zero = terminal_fixture(monkeypatch, tmp_path)
    V.SOURCE.mkdir()
    with pytest.raises(V.ValidationError, match="canonical source unexpectedly exists"):
        V._terminal_projection(module, {})


def test_terminal_projection_rejects_wrong_root_state(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    module, _zero = terminal_fixture(monkeypatch, tmp_path)
    module._root_state = lambda: "SOURCE_CANONICAL"
    with pytest.raises(V.ValidationError, match="not terminal"):
        V._terminal_projection(module, {})


def test_validate_requires_two_identical_full_terminal_passes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(V, "_validator_parent", lambda _expected: {"parent": True})
    monkeypatch.setattr(V, "_validate_var_lib_siblings", lambda: {"stable": True})
    monkeypatch.setattr(V, "_validate_validator_toolchain", lambda *_args: {"tool": True})
    inventories = [{"stable": True}, {"stable": True}, {"stable": True}]
    monkeypatch.setattr(V, "_validate_historical_inventory", lambda: inventories.pop(0))
    monkeypatch.setattr(V, "_load_historical_module", lambda: object())
    projections = [
        {
            "durable_prefix_length": 2, "source_state": "DESTINATION_QUARANTINED",
            "provenance_committed": True, "validated_partial_provenance": False,
        },
        {
            "durable_prefix_length": 2, "source_state": "DESTINATION_QUARANTINED",
            "provenance_committed": True, "validated_partial_provenance": False,
        },
    ]
    monkeypatch.setattr(V, "_terminal_projection", lambda *_args: projections.pop(0))
    result = V.validate({})
    assert result["status"] == (
        "PASS_READ_ONLY_TERMINAL_PROVENANCE_VALIDATED_NO_PUBLICATION_NO_RENAME"
    )
    assert result["durable_prefix_length"] == 2
    assert result["lifecycle_action_executed"] is False
    assert result["publication_executed"] is False
    assert result["rename_executed"] is False


def test_validate_rejects_historical_inventory_drift_between_passes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(V, "_validator_parent", lambda _expected: {})
    monkeypatch.setattr(V, "_validate_var_lib_siblings", lambda: {"stable": True})
    monkeypatch.setattr(V, "_validate_validator_toolchain", lambda *_args: {})
    inventories = [{"version": 1}, {"version": 2}, {"version": 2}]
    monkeypatch.setattr(V, "_validate_historical_inventory", lambda: inventories.pop(0))
    monkeypatch.setattr(V, "_load_historical_module", lambda: object())
    monkeypatch.setattr(V, "_terminal_projection", lambda *_args: {"stable": True})
    with pytest.raises(V.ValidationError, match="historical controller changed"):
        V.validate({})


def test_validate_rejects_non_fixed_terminal_projection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(V, "_validator_parent", lambda _expected: {})
    monkeypatch.setattr(V, "_validate_var_lib_siblings", lambda: {"stable": True})
    monkeypatch.setattr(V, "_validate_validator_toolchain", lambda *_args: {})
    monkeypatch.setattr(V, "_validate_historical_inventory", lambda: {"stable": True})
    monkeypatch.setattr(V, "_load_historical_module", lambda: object())
    projections = [{"version": 1}, {"version": 2}]
    monkeypatch.setattr(V, "_terminal_projection", lambda *_args: projections.pop(0))
    with pytest.raises(V.ValidationError, match="not a fixed point"):
        V.validate({})


def test_main_rejects_malformed_expected_hash() -> None:
    with pytest.raises(V.ValidationError, match="malformed"):
        V.main(
            [
                "--expected-validator-sha256", "not-a-hash",
                "--expected-validator-test-sha256", "1" * 64,
                "--expected-validator-review-sha256", "2" * 64,
                "--expected-validator-st-dev", "1",
                "--expected-validator-st-ino", "2",
            ]
        )


def test_main_rejects_execution_outside_installed_validator_path() -> None:
    with pytest.raises(V.ValidationError, match="exact installed path"):
        V.main(
            [
                "--expected-validator-sha256", "0" * 64,
                "--expected-validator-test-sha256", "1" * 64,
                "--expected-validator-review-sha256", "2" * 64,
                "--expected-validator-st-dev", "1",
                "--expected-validator-st-ino", "2",
            ]
        )
