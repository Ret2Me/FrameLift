from __future__ import annotations

import hashlib
import importlib.util
import os
from pathlib import Path
import stat
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
BUILDER_SCRIPT = ROOT / "work/blind-phase-confirmatory-v2/build_runtime_guard_v3.py"
LAUNCHER_SCRIPT = (
    ROOT / "work/blind-phase-confirmatory-v2/campaign_launcher_amended_v3.py"
)


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


BUILDER = _load("blind_phase_builder_v3_small_authority_test", BUILDER_SCRIPT)
LAUNCHER = _load("blind_phase_launcher_v3_small_authority_test", LAUNCHER_SCRIPT)


def _identity(path: Path, payload: bytes) -> dict[str, object]:
    return {
        "path": str(path.absolute()),
        "size_bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }


def _mount_record(target: Path, *, options: list[str] | None = None) -> dict[str, object]:
    status = target.lstat()
    return {
        "mount_id": 90,
        "parent_id": 1,
        "major_minor": f"{os.major(status.st_dev)}:{os.minor(status.st_dev)}",
        "root": "/",
        "mount_point": str(target.absolute()),
        "mount_options": options or ["nodev", "noexec", "nosuid", "ro"],
        "optional_fields": [],
        "fs_type": "tmpfs",
        "mount_source": "tmpfs",
        "super_options": ["ro"],
    }


def _snapshot_fixture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, payload: bytes = b"exact"
) -> tuple[Path, Path, dict[str, object], dict[str, object]]:
    project = tmp_path / "project"
    control = tmp_path / "control"
    target = control / BUILDER.SEALED_PROJECT_ARTIFACTS_NAME
    source = project / "work" / "tool.py"
    sealed = target / "work" / "tool.py"
    source.parent.mkdir(parents=True)
    sealed.parent.mkdir(parents=True)
    source.write_bytes(payload)
    sealed.write_bytes(payload)
    source_identity = _identity(source, payload)
    record = _mount_record(target)
    monkeypatch.setattr(BUILDER, "PROJECT_ROOT", project)
    monkeypatch.setattr(BUILDER, "DEFAULT_CONTROL_PARENT", control)
    monkeypatch.setattr(
        BUILDER, "_sealed_project_artifact_identities", lambda _context: [source_identity]
    )
    monkeypatch.setattr(
        BUILDER,
        "_parse_mountinfo",
        lambda _text: [record],
    )
    original_read_text = Path.read_text

    def read_text(path: Path, *args: object, **kwargs: object) -> str:
        if path == Path("/proc/self/mountinfo"):
            return "synthetic"
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read_text)
    original_lstat = Path.lstat
    original_regular_identity = BUILDER._regular_file_identity

    def root_owned_lstat(path: Path):
        current = original_lstat(path)
        if path.absolute().is_relative_to(target.absolute()):
            values = list(current)
            values[4] = 0
            values[5] = 0
            if stat.S_ISREG(current.st_mode):
                values[0] = stat.S_IFREG | 0o444
            return os.stat_result(values)
        return current

    monkeypatch.setattr(Path, "lstat", root_owned_lstat)

    def regular_identity(path: Path, **kwargs: object) -> dict[str, object]:
        absolute = Path(path).absolute()
        if absolute.is_relative_to(target.absolute()):
            return _identity(absolute, absolute.read_bytes())
        return original_regular_identity(path, **kwargs)

    monkeypatch.setattr(BUILDER, "_regular_file_identity", regular_identity)
    return source, sealed, source_identity, record


def test_exact_small_project_mapping_is_used(monkeypatch: pytest.MonkeyPatch) -> None:
    source = LAUNCHER.ROOT / "work" / "frozen-tool.py"
    sealed = LAUNCHER.SEALED_PROJECT_ARTIFACTS_ROOT / "work" / "frozen-tool.py"
    monkeypatch.setattr(LAUNCHER, "_ACTIVE_IQ_AUTHORITY_MAPPING", {})
    monkeypatch.setattr(
        LAUNCHER,
        "_ACTIVE_PROJECT_ARTIFACT_MAPPING",
        {str(source.absolute()): str(sealed.absolute())},
    )
    monkeypatch.setattr(LAUNCHER, "_AUTHORITY_MAPPING_ACTIVE", True)
    assert LAUNCHER._authority_path(source) == sealed


def test_unmapped_project_path_is_rejected_after_ack(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(LAUNCHER, "_ACTIVE_IQ_AUTHORITY_MAPPING", {})
    monkeypatch.setattr(LAUNCHER, "_ACTIVE_PROJECT_ARTIFACT_MAPPING", {})
    monkeypatch.setattr(LAUNCHER, "_AUTHORITY_MAPPING_ACTIVE", True)
    with pytest.raises(PermissionError, match="lacks a sealed"):
        LAUNCHER._authority_path(LAUNCHER.ROOT / "work" / "unbound.py")


def test_unmapped_nonproject_result_path_remains_available(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "ephemeral-null.ci16"
    monkeypatch.setattr(LAUNCHER, "_ACTIVE_IQ_AUTHORITY_MAPPING", {})
    monkeypatch.setattr(LAUNCHER, "_ACTIVE_PROJECT_ARTIFACT_MAPPING", {})
    monkeypatch.setattr(LAUNCHER, "_AUTHORITY_MAPPING_ACTIVE", True)
    assert LAUNCHER._authority_path(path) == path.absolute()


def test_runtime_tree_uses_runtime_authority_without_per_file_mapping(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = LAUNCHER.CANDIDATE_RUNTIME_SOURCE_ROOT / "bin" / "python"
    expected = LAUNCHER.RUNTIME_AUTHORITY_PARENT_V3 / "candidate" / "bin" / "python"
    monkeypatch.setattr(LAUNCHER, "_ACTIVE_IQ_AUTHORITY_MAPPING", {})
    monkeypatch.setattr(LAUNCHER, "_ACTIVE_PROJECT_ARTIFACT_MAPPING", {})
    monkeypatch.setattr(LAUNCHER, "_AUTHORITY_MAPPING_ACTIVE", True)
    assert LAUNCHER._authority_path(source) == expected


def test_receiver_argv_maps_exact_small_artifact_and_rejects_original(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = LAUNCHER.ROOT / "work" / "runner.py"
    sealed = LAUNCHER.SEALED_PROJECT_ARTIFACTS_ROOT / "work" / "runner.py"
    monkeypatch.setattr(
        LAUNCHER,
        "_FROZEN_RECEIVER_ARGV",
        lambda **_kwargs: (
            "/usr/bin/bwrap", "--ro-bind", str(source), "/runtime/runner.py",
            "--chdir", "/scratch",
        ),
    )
    monkeypatch.setattr(LAUNCHER, "_ACTIVE_IQ_AUTHORITY_MAPPING", {})
    monkeypatch.setattr(
        LAUNCHER,
        "_ACTIVE_PROJECT_ARTIFACT_MAPPING",
        {str(source.absolute()): str(sealed.absolute())},
    )
    monkeypatch.setattr(LAUNCHER, "_AUTHORITY_MAPPING_ACTIVE", True)
    argv = LAUNCHER._receiver_argv_with_no_user_site()
    assert str(source) not in argv
    assert str(sealed) in argv
    assert argv[argv.index("--chdir") - 3 : argv.index("--chdir")] == (
        "--setenv", "PYTHONNOUSERSITE", "1"
    )


def test_receiver_argv_rejects_unmapped_project_bind(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = LAUNCHER.ROOT / "work" / "unbound-runner.py"
    monkeypatch.setattr(
        LAUNCHER,
        "_FROZEN_RECEIVER_ARGV",
        lambda **_kwargs: (
            "/usr/bin/bwrap", "--ro-bind", str(source), "/runtime/runner.py",
            "--chdir", "/scratch",
        ),
    )
    monkeypatch.setattr(LAUNCHER, "_ACTIVE_IQ_AUTHORITY_MAPPING", {})
    monkeypatch.setattr(LAUNCHER, "_ACTIVE_PROJECT_ARTIFACT_MAPPING", {})
    monkeypatch.setattr(LAUNCHER, "_AUTHORITY_MAPPING_ACTIVE", True)
    with pytest.raises(PermissionError, match="lacks a sealed"):
        LAUNCHER._receiver_argv_with_no_user_site()


def test_small_artifact_snapshot_binds_exact_source_and_sealed_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _source, sealed, expected, record = _snapshot_fixture(tmp_path, monkeypatch)
    snapshot = BUILDER._sealed_project_artifact_snapshot({}, full_hash=True)
    assert snapshot["status"] == "PASS"
    assert snapshot["artifact_count"] == 1
    assert snapshot["mount_record"] == record
    assert snapshot["artifacts"][0]["source"] == expected
    assert snapshot["artifacts"][0]["sealed"] == {
        **expected,
        "path": str(sealed.absolute()),
    }
    unhashed = dict(snapshot)
    assert unhashed.pop("snapshot_payload_sha256") == BUILDER._sha256_document(unhashed)


def test_small_artifact_snapshot_rejects_byte_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _source, sealed, _expected, _record = _snapshot_fixture(tmp_path, monkeypatch)
    sealed.write_bytes(b"wrong")
    with pytest.raises(ValueError, match="inode differs"):
        BUILDER._sealed_project_artifact_snapshot({}, full_hash=True)


@pytest.mark.parametrize(
    ("replacement", "message"),
    [
        ({"mount_options": ["nodev", "noexec", "nosuid", "rw"]}, "options differ"),
        ({"fs_type": "ext4", "mount_source": "/dev/foreign"}, "options differ"),
    ],
)
def test_small_artifact_snapshot_rejects_foreign_or_writable_mount(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    replacement: dict[str, object],
    message: str,
) -> None:
    _source, _sealed, _expected, record = _snapshot_fixture(tmp_path, monkeypatch)
    record.update(replacement)
    with pytest.raises(ValueError, match=message):
        BUILDER._sealed_project_artifact_snapshot({}, full_hash=True)


def test_small_artifact_snapshot_rejects_nested_mount(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _source, sealed, _expected, record = _snapshot_fixture(tmp_path, monkeypatch)
    nested = {**record, "mount_id": 91, "mount_point": str(sealed.parent)}
    monkeypatch.setattr(BUILDER, "_parse_mountinfo", lambda _text: [record, nested])
    with pytest.raises(ValueError, match="topology differs"):
        BUILDER._sealed_project_artifact_snapshot({}, full_hash=True)


def test_missing_small_artifact_authority_validation_does_not_create_or_mount(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    control = tmp_path / "control"
    target = control / BUILDER.SEALED_PROJECT_ARTIFACTS_NAME
    control.mkdir()
    monkeypatch.setattr(BUILDER, "DEFAULT_CONTROL_PARENT", control)
    mount_calls: list[object] = []
    monkeypatch.setattr(
        BUILDER, "_run_mount_command", lambda argv: mount_calls.append(tuple(argv))
    )
    monkeypatch.setattr(BUILDER, "_parse_mountinfo", lambda _text: [])
    monkeypatch.setattr(Path, "read_text", lambda *_args, **_kwargs: "synthetic")
    with pytest.raises((FileNotFoundError, ValueError)):
        BUILDER._sealed_project_artifact_snapshot({}, full_hash=True)
    assert not target.exists()
    assert mount_calls == []
