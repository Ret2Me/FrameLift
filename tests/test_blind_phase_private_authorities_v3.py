from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "work/blind-phase-confirmatory-v2/build_runtime_guard_v3.py"


def _module():
    name = "blind_phase_runtime_guard_builder_v3_private_authority_test_module"
    spec = importlib.util.spec_from_file_location(name, SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


BUILDER = _module()


def _mount_record(
    path: Path,
    *,
    options=None,
    super_options=None,
    fs_type="tmpfs",
    source="tmpfs",
    major="0:77",
):
    return {
        "mount_id": 77,
        "parent_id": 1,
        "major_minor": major,
        "root": "/",
        "mount_point": str(path),
        "mount_options": list(options or ["rw", "nosuid", "nodev", "noexec"]),
        "optional_fields": [],
        "fs_type": fs_type,
        "mount_source": source,
        "super_options": list(super_options or ["rw", "size=2G"]),
    }


def _patch_mountinfo(monkeypatch: pytest.MonkeyPatch, records) -> None:
    monkeypatch.setattr(BUILDER, "_parse_mountinfo", lambda _text: list(records))


def test_results_authority_accepts_only_exact_private_tmpfs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    results = tmp_path / "results-v3"
    results.mkdir(mode=0o700)
    results.chmod(0o700)
    if os.geteuid() == 0:
        os.chown(results, 1000, 1000)
    monkeypatch.setattr(BUILDER, "DEFAULT_RESULTS_PARENT", results)
    record = _mount_record(
        results,
        major=f"{os.major(results.stat().st_dev)}:{os.minor(results.stat().st_dev)}",
    )
    _patch_mountinfo(monkeypatch, [record])
    monkeypatch.setattr(
        BUILDER,
        "_statvfs_bytes",
        lambda _path: {
            "total_bytes": BUILDER.RESULTS_TMPFS_CAPACITY_BYTES,
            "available_bytes": BUILDER.RESULTS_TMPFS_CAPACITY_BYTES,
        },
    )
    assert BUILDER._validate_external_output_parent(results, create=False) == results
    identity = BUILDER._results_authority_identity(results)
    assert identity["path"] == str(results)
    assert identity["uid"] == 1000
    assert identity["gid"] == 1000
    assert identity["mode"] == 0o700
    assert identity["capacity_bytes"] == 2 * 1024**3
    assert identity["minimum_available_bytes_before_work"] == 1536 * 1024**2
    assert identity["available_bytes_formula"] == "statvfs.f_bavail*statvfs.f_frsize"
    assert identity["mount_record"] == record


def test_capacity_observation_requires_exact_2g_and_uses_f_bavail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    results = tmp_path / "results-v3"
    results.mkdir(mode=0o700)
    record = _mount_record(results)
    observed = SimpleNamespace(
        f_frsize=4096,
        f_bsize=4096,
        f_blocks=BUILDER.RESULTS_TMPFS_CAPACITY_BYTES // 4096,
        f_bavail=BUILDER.RESULTS_MINIMUM_AVAILABLE_BYTES_BEFORE_WORK // 4096,
        # Deliberately different: authorizing f_bfree would mask the boundary.
        f_bfree=(BUILDER.RESULTS_MINIMUM_AVAILABLE_BYTES_BEFORE_WORK // 4096) + 99,
    )
    monkeypatch.setattr(BUILDER.os, "statvfs", lambda _path: observed)
    assert BUILDER._results_capacity_observation(results, record) == {
        "capacity_bytes": BUILDER.RESULTS_TMPFS_CAPACITY_BYTES,
        "total_bytes": BUILDER.RESULTS_TMPFS_CAPACITY_BYTES,
        "available_bytes": BUILDER.RESULTS_MINIMUM_AVAILABLE_BYTES_BEFORE_WORK,
    }
    assert BUILDER._require_results_available(
        results, record, phase="exact inclusive boundary"
    )["available_bytes"] == BUILDER.RESULTS_MINIMUM_AVAILABLE_BYTES_BEFORE_WORK

    observed.f_bavail -= 1
    with pytest.raises(OSError) as raised:
        BUILDER._require_results_available(
            results, record, phase="one block below boundary"
        )
    assert raised.value.errno == 28

    observed.f_bavail += 1
    wrong_size = _mount_record(results, super_options=["rw", "size=1G"])
    with pytest.raises(ValueError, match="exactly 2 GiB"):
        BUILDER._results_capacity_observation(results, wrong_size)


def _write_cgroup_v2_fixture(root: Path, relative: str) -> tuple[Path, Path]:
    proc_cgroup = root / "proc-self-cgroup"
    proc_cgroup.write_text(f"0::{relative}\n", encoding="ascii")
    membership = root / "cgroup" / relative.lstrip("/")
    membership.mkdir(parents=True)
    (membership / "cpu.max").write_text("max 100000\n", encoding="ascii")
    (membership / "memory.max").write_text("max\n", encoding="ascii")
    (membership / "memory.current").write_text("4096\n", encoding="ascii")
    (membership / "pids.max").write_text("max\n", encoding="ascii")
    (membership / "pids.current").write_text("7\n", encoding="ascii")
    return proc_cgroup, root / "cgroup"


@pytest.mark.parametrize("relative", ["/", "/user.slice/test.scope"])
def test_cgroup_v2_membership_is_resolved_beneath_explicit_root(
    tmp_path: Path, relative: str
) -> None:
    proc_cgroup, cgroup_root = _write_cgroup_v2_fixture(tmp_path, relative)
    observed = BUILDER._read_cgroup_v2_limits(
        proc_cgroup=proc_cgroup, cgroup_root=cgroup_root
    )
    expected = cgroup_root / relative.lstrip("/")
    assert Path(str(observed["path"])) == expected.absolute()
    assert Path(str(observed["path"])).is_relative_to(cgroup_root.absolute())
    assert observed["cpu_millicores"] == "unlimited"
    assert observed["pids_available"] == "unlimited"


def test_cgroup_v2_membership_rejects_parent_escape(tmp_path: Path) -> None:
    proc_cgroup = tmp_path / "proc-self-cgroup"
    proc_cgroup.write_text("0::/../../etc\n", encoding="ascii")
    cgroup_root = tmp_path / "cgroup"
    cgroup_root.mkdir()
    with pytest.raises(ValueError, match="(?i)(escape|outside|malformed|membership)"):
        BUILDER._read_cgroup_v2_limits(
            proc_cgroup=proc_cgroup, cgroup_root=cgroup_root
        )


def test_current_process_cgroup_v2_path_stays_under_kernel_mount() -> None:
    observed = BUILDER._read_cgroup_v2_limits()
    path = Path(str(observed["path"]))
    assert path.is_relative_to(Path("/sys/fs/cgroup"))
    assert path.is_dir()


@pytest.mark.parametrize(
    "records,match",
    [
        ([], "absent"),
        (["duplicate", "duplicate"], "duplicated"),
    ],
)
def test_results_authority_rejects_missing_or_duplicate_mount(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    records,
    match: str,
) -> None:
    results = tmp_path / "results-v3"
    results.mkdir(mode=0o700)
    monkeypatch.setattr(BUILDER, "DEFAULT_RESULTS_PARENT", results)
    mounted = [] if not records else [_mount_record(results), _mount_record(results)]
    _patch_mountinfo(monkeypatch, mounted)
    with pytest.raises(ValueError, match=match):
        BUILDER._validate_external_output_parent(results, create=False)


@pytest.mark.parametrize(
    "record",
    [
        lambda path: _mount_record(path, fs_type="ext4", source="/dev/root"),
        lambda path: _mount_record(path, options=["ro", "nosuid", "nodev", "noexec"]),
        lambda path: _mount_record(path, options=["rw", "nodev", "noexec"]),
        lambda path: _mount_record(path, options=["rw", "nosuid", "noexec"]),
        lambda path: _mount_record(path, options=["rw", "nosuid", "nodev"]),
    ],
)
def test_results_authority_rejects_foreign_fs_or_mount_options(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, record
) -> None:
    results = tmp_path / "results-v3"
    results.mkdir(mode=0o700)
    monkeypatch.setattr(BUILDER, "DEFAULT_RESULTS_PARENT", results)
    _patch_mountinfo(monkeypatch, [record(results)])
    with pytest.raises(ValueError, match="options differ"):
        BUILDER._validate_external_output_parent(results, create=False)


def test_results_authority_rejects_mount_device_inode_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    results = tmp_path / "results-v3"
    results.mkdir(mode=0o700)
    monkeypatch.setattr(BUILDER, "DEFAULT_RESULTS_PARENT", results)
    _patch_mountinfo(monkeypatch, [_mount_record(results, major="0:999")])
    with pytest.raises(ValueError, match="(?i)(device|inode|major|identity)"):
        BUILDER._validate_external_output_parent(results, create=False)


def test_results_validation_never_mounts_or_repairs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    results = tmp_path / "results-v3"
    results.mkdir(mode=0o700)
    monkeypatch.setattr(BUILDER, "DEFAULT_RESULTS_PARENT", results)
    _patch_mountinfo(monkeypatch, [])
    monkeypatch.setattr(
        BUILDER,
        "_run_mount_command",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("validator attempted mount mutation")
        ),
    )
    with pytest.raises(ValueError, match="absent"):
        BUILDER._validate_external_output_parent(results, create=False)


def _fake_status(*, dev=1, ino=1, uid=0, gid=0, mode=0o555):
    return SimpleNamespace(
        st_dev=dev,
        st_ino=ino,
        st_uid=uid,
        st_gid=gid,
        st_mode=stat.S_IFDIR | mode,
    )


def _runtime_validator_fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    control = tmp_path / "control"
    parent = control / BUILDER.RUNTIME_AUTHORITY_PARENT_NAME
    candidate = parent / "candidate"
    component = parent / "component"
    candidate.mkdir(parents=True)
    component.mkdir()
    roots = {
        "candidate": tmp_path / "project-candidate",
        "component": tmp_path / "project-component",
    }
    roots["candidate"].mkdir()
    roots["component"].mkdir()
    expected = {
        "candidate": {"path": str(roots["candidate"]), "st_dev": 11, "st_ino": 21},
        "component": {"path": str(roots["component"]), "st_dev": 12, "st_ino": 22},
    }
    authorities = {"candidate": candidate, "component": component}
    monkeypatch.setattr(BUILDER, "_runtime_authority_roots", lambda _control: authorities)
    original_lstat = Path.lstat

    def lstat(path: Path):
        absolute = path.absolute()
        if absolute == parent.absolute():
            return _fake_status(mode=0o755)
        if absolute == candidate.absolute():
            return _fake_status(dev=11, ino=21)
        if absolute == component.absolute():
            return _fake_status(dev=12, ino=22)
        return original_lstat(path)

    monkeypatch.setattr(Path, "lstat", lstat)
    monkeypatch.setattr(
        BUILDER,
        "_recursive_alias_identity",
        lambda authority, source: {**dict(source), "path": str(authority.absolute())},
    )
    records = [
        _mount_record(candidate, options=["ro", "nosuid", "nodev"], fs_type="ext4", source="/dev/root", major="11:0"),
        _mount_record(component, options=["ro", "nosuid", "nodev"], fs_type="ext4", source="/dev/root", major="12:0"),
    ]
    context = {"roots": roots}
    return control, context, expected, authorities, records


def test_runtime_authority_validation_is_pure_and_exact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    control, context, expected, authorities, records = _runtime_validator_fixture(
        tmp_path, monkeypatch
    )
    _patch_mountinfo(monkeypatch, records)
    monkeypatch.setattr(
        BUILDER,
        "_run_mount_command",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("pure runtime validator attempted mount mutation")
        ),
    )
    result = BUILDER._validate_runtime_authority_mounts(context, control, expected)
    assert set(result) == {"candidate", "component"}
    assert result["candidate"]["authority"]["path"] == str(authorities["candidate"])


def test_runtime_authority_missing_mount_remains_missing_without_repair(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    control, context, expected, _authorities, records = _runtime_validator_fixture(
        tmp_path, monkeypatch
    )
    _patch_mountinfo(monkeypatch, records[1:])
    monkeypatch.setattr(
        BUILDER,
        "_run_mount_command",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("pure runtime validator attempted repair")
        ),
    )
    with pytest.raises(ValueError, match="absent"):
        BUILDER._validate_runtime_authority_mounts(context, control, expected)


def test_runtime_authority_rejects_duplicate_mount(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    control, context, expected, _authorities, records = _runtime_validator_fixture(
        tmp_path, monkeypatch
    )
    _patch_mountinfo(monkeypatch, [records[0], records[0], records[1]])
    with pytest.raises(ValueError, match="duplicated"):
        BUILDER._validate_runtime_authority_mounts(context, control, expected)


@pytest.mark.parametrize("defect", ["rw", "missing_nosuid", "missing_nodev", "inode"])
def test_runtime_authority_rejects_options_or_inode_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, defect: str
) -> None:
    control, context, expected, authorities, records = _runtime_validator_fixture(
        tmp_path, monkeypatch
    )
    if defect == "rw":
        records[0]["mount_options"] = ["rw", "nosuid", "nodev"]
    elif defect == "missing_nosuid":
        records[0]["mount_options"] = ["ro", "nodev"]
    elif defect == "missing_nodev":
        records[0]["mount_options"] = ["ro", "nosuid"]
    else:
        expected["candidate"]["st_ino"] = 999
    _patch_mountinfo(monkeypatch, records)
    with pytest.raises(ValueError, match="identity/options differ"):
        BUILDER._validate_runtime_authority_mounts(context, control, expected)


def test_runtime_authority_paths_are_direct_children_of_root_control_anchor() -> None:
    control = Path("/var/lib/telemetry-yield-confirmatory-v3")
    roots = BUILDER._runtime_authority_roots(control)
    assert set(roots) == {"candidate", "component"}
    assert all(path.parent == control / BUILDER.RUNTIME_AUTHORITY_PARENT_NAME for path in roots.values())
    assert all(path.is_relative_to(control) for path in roots.values())


@pytest.mark.skipif(
    os.geteuid() != 0
    or os.environ.get("TELEMETRY_YIELD_RUN_ROOT_AUTHORITY_TEST") != "1"
    or shutil.which("unshare") is None,
    reason="requires explicit root opt-in and a private throwaway mount namespace",
)
def test_root_control_runtime_and_results_mounts_survive_source_path_rename(
    tmp_path: Path,
) -> None:
    probe = r'''
import json, os, pathlib, subprocess, sys
base = pathlib.Path(sys.argv[1])
control = base / "control"
source = base / "uid-owned-source"
runtime = control / "runtime" / "candidate"
results = control / "results-v3"
runtime.mkdir(parents=True)
results.mkdir()
source.mkdir()
(source / "python").write_bytes(b"sealed candidate bytes")
subprocess.run(["/usr/bin/mount", "--bind", str(source), str(runtime)], check=True)
subprocess.run(["/usr/bin/mount", "-o", "remount,bind,ro,nosuid,nodev", str(runtime)], check=True)
source.rename(base / "renamed-source")
assert (runtime / "python").read_bytes() == b"sealed candidate bytes"
subprocess.run(["/usr/bin/mount", "-t", "tmpfs", "-o",
                "size=2g,mode=0700,uid=1000,gid=1000,nosuid,nodev,noexec",
                "tmpfs", str(results)], check=True)
status = results.stat()
assert status.st_uid == 1000 and status.st_gid == 1000 and (status.st_mode & 0o777) == 0o700
filesystem = os.statvfs(results)
assert filesystem.f_blocks * filesystem.f_frsize == 2 * 1024**3
(results / "probe").write_bytes(b"private role output")
assert (results / "probe").read_bytes() == b"private role output"
print(json.dumps({"runtime_survived": True, "results_private": True}))
subprocess.run(["/usr/bin/umount", str(results)], check=True)
subprocess.run(["/usr/bin/umount", str(runtime)], check=True)
'''
    completed = subprocess.run(
        [
            "/usr/bin/unshare",
            "--mount",
            "--propagation",
            "private",
            "/usr/bin/python3.12",
            "-I",
            "-S",
            "-c",
            probe,
            str(tmp_path),
        ],
        check=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=30,
        close_fds=True,
        env={"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C"},
    )
    assert json.loads(completed.stdout) == {
        "runtime_survived": True,
        "results_private": True,
    }
