from __future__ import annotations

import hashlib
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
    name = "blind_phase_runtime_guard_builder_v3_tmpfs_contract_test_module"
    spec = importlib.util.spec_from_file_location(name, SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


BUILDER = _module()


def _launcher_module():
    path = ROOT / "work/blind-phase-confirmatory-v2/campaign_launcher_amended_v3.py"
    name = "blind_phase_campaign_launcher_amended_v3_tmpfs_contract_test_module"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


LAUNCHER = _launcher_module()


def _identity(path: Path, payload: bytes) -> dict[str, object]:
    return {
        "path": str(path.absolute()),
        "size_bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }


def _acquisition(count: int) -> dict[str, object]:
    objects = []
    for index in range(count):
        path = (
            BUILDER.PROJECT_ROOT
            / "work/blind-phase-confirmatory-v2/holdout-iq-v4"
            / f"observation-{4500 + index}"
            / f"observation_{4500 + index}.iq"
        )
        objects.append(
            {
                "observation_id": 4500 + index,
                "local_path": str(path),
                "actual_size_bytes": 4,
                "sha256": hashlib.sha256(index.to_bytes(4, "little")).hexdigest(),
            }
        )
    return {"acquisition": {"objects": objects}}


def _snapshot(count: int = 30, *, digest: str = "a" * 64) -> dict[str, object]:
    mounts = [
        {
            "schema_version": BUILDER.SEALED_INPUT_MOUNT_SCHEMA,
            "status": "PASS",
            "source": {
                "path": f"/frozen/observation-{index}.iq",
                "size_bytes": 4,
                "sha256": digest,
            },
            "mount_point": str(
                BUILDER.DEFAULT_CONTROL_PARENT / BUILDER.SEALED_INPUT_TARGET_NAME
            ),
            "sealed_path": str(
                BUILDER.DEFAULT_CONTROL_PARENT
                / BUILDER.SEALED_INPUT_TARGET_NAME
                / f"observation-{index}.iq"
            ),
            "mounted_inode": {
                "st_dev": 1,
                "st_ino": 100 + index,
                "size_bytes": 4,
                "uid": 0,
                "gid": 0,
                "mode": 0o444,
                "nlink": 1,
                "sha256": digest,
            },
            "mount_options": ["ro", "nosuid", "nodev", "noexec"],
            "mount_record": {"mount_id": 42},
        }
        for index in range(count)
    ]
    document = {
        "schema_version": BUILDER.SEALED_INPUT_SNAPSHOT_SCHEMA,
        "status": "PASS",
        "input_count": 30,
        "mounts": mounts,
    }
    document["snapshot_payload_sha256"] = BUILDER._sha256_document(document)
    return document


@pytest.mark.parametrize("count", [29, 31])
def test_frozen_iq_inventory_rejects_29_or_31_entries(count: int) -> None:
    with pytest.raises(ValueError, match="exactly 30"):
        BUILDER._frozen_iq_source_identities(_acquisition(count))


def test_frozen_iq_inventory_rejects_duplicate_source_path() -> None:
    context = _acquisition(30)
    context["acquisition"]["objects"][1]["local_path"] = context["acquisition"][
        "objects"
    ][0]["local_path"]
    with pytest.raises(ValueError, match="duplicated"):
        BUILDER._frozen_iq_source_identities(context)


def test_tmpfs_copy_rejects_digest_mismatch_without_publishing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source.iq"
    destination = tmp_path / "sealed" / "source.iq"
    payload = b"exact acquired IQ"
    source.write_bytes(payload)
    source.chmod(0o444)
    expected = _identity(source, payload)
    expected["sha256"] = "0" * 64
    # Unit tests run unprivileged; ownership mutation itself is exercised by
    # the opt-in root integration smoke below.
    monkeypatch.setattr(BUILDER.os, "fchown", lambda *_args: None)
    with pytest.raises(ValueError, match="(?i)(changed|differs|identity)"):
        BUILDER._copy_iq_to_private_tmpfs(source, destination, expected)
    assert not destination.exists()


def test_tmpfs_copy_is_no_clobber(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source.iq"
    destination = tmp_path / "sealed" / "source.iq"
    payload = b"exact acquired IQ"
    source.write_bytes(payload)
    source.chmod(0o444)
    destination.parent.mkdir()
    destination.write_bytes(b"preexisting authority")
    before = destination.read_bytes()
    monkeypatch.setattr(BUILDER.os, "fchown", lambda *_args: None)
    with pytest.raises((FileExistsError, ValueError), match="(?i)(exists|clobber|destination)"):
        BUILDER._copy_iq_to_private_tmpfs(
            source, destination, _identity(source, payload)
        )
    assert destination.read_bytes() == before


def test_inventory_rejects_any_extra_nested_mount(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = BUILDER.DEFAULT_CONTROL_PARENT / BUILDER.SEALED_INPUT_TARGET_NAME
    records = [
        {
            "mount_id": 10,
            "parent_id": 1,
            "major_minor": "0:99",
            "root": "/",
            "mount_point": str(target),
            "mount_options": ["ro", "nosuid", "nodev", "noexec"],
            "optional_fields": [],
            "fs_type": "tmpfs",
            "mount_source": "tmpfs",
            "super_options": ["ro"],
        },
        {
            "mount_id": 11,
            "parent_id": 10,
            "major_minor": "0:100",
            "root": "/",
            "mount_point": str(target / "observation-4500"),
            "mount_options": ["ro"],
            "optional_fields": [],
            "fs_type": "tmpfs",
            "mount_source": "foreign",
            "super_options": ["ro"],
        },
    ]
    monkeypatch.setattr(BUILDER, "_parse_mountinfo", lambda _text: records)
    monkeypatch.setattr(Path, "read_text", lambda *_args, **_kwargs: "synthetic")
    monkeypatch.setattr(BUILDER, "_frozen_iq_source_identities", lambda _context: [])
    with pytest.raises(ValueError, match="(?i)(extra|nested|foreign|mount set)"):
        BUILDER._sealed_input_mount_inventory({}, full_hash=True)


def test_pre_and_post_role_require_full_hash_and_exact_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = _snapshot()
    calls: list[bool] = []
    monkeypatch.setattr(BUILDER, "_assert_execution_namespace", lambda: None)

    def snapshot(_context, *, full_hash: bool):
        calls.append(full_hash)
        return expected

    monkeypatch.setattr(BUILDER, "_sealed_input_snapshot", snapshot)
    assert len(BUILDER._validate_sealed_inputs_pre_role({}, expected)) == 30
    assert len(BUILDER._validate_sealed_inputs_post_role({}, expected)) == 30
    assert calls == [True, True]


def test_post_role_rejects_changed_hash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = _snapshot()
    changed = _snapshot(digest="b" * 64)
    monkeypatch.setattr(BUILDER, "_assert_execution_namespace", lambda: None)
    monkeypatch.setattr(
        BUILDER, "_sealed_input_snapshot", lambda _context, *, full_hash: changed
    )
    with pytest.raises(ValueError, match="differs after role"):
        BUILDER._validate_sealed_inputs_post_role({}, expected)


def test_pre_role_rejects_stale_namespace_before_inventory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called = False

    def stale() -> None:
        nonlocal called
        called = True
        raise ValueError("stale execution namespace")

    monkeypatch.setattr(BUILDER, "_assert_execution_namespace", stale)
    with pytest.raises(ValueError, match="stale execution namespace"):
        BUILDER._validate_sealed_inputs_pre_role({}, _snapshot())
    assert called


def test_pre_role_rejects_unmounted_authority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(BUILDER, "_assert_execution_namespace", lambda: None)
    monkeypatch.setattr(
        BUILDER,
        "_sealed_input_snapshot",
        lambda _context, *, full_hash: (_ for _ in ()).throw(
            ValueError("sealed input root must have exactly one mount record")
        ),
    )
    with pytest.raises(ValueError, match="exactly one mount record"):
        BUILDER._validate_sealed_inputs_pre_role({}, _snapshot())


def test_null_transform_uses_sealed_authority_and_never_opens_original_iq(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """All post-ACK IQ reads, including null generation, use private tmpfs."""

    original_iq = tmp_path / "mutable-host-view" / "observation.iq"
    sealed_iq = tmp_path / "root-control" / "sealed-input" / "observation.iq"
    original_iq.parent.mkdir()
    sealed_iq.parent.mkdir(parents=True)
    payload = b"\x01\x00\x02\x00"
    original_iq.write_bytes(payload)
    sealed_iq.write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()

    original_config = tmp_path / "mutable-host-view" / "candidate.json"
    sealed_config = tmp_path / "root-control" / "sealed-artifacts" / "candidate.json"
    sealed_config.parent.mkdir(parents=True)
    config_payload = b"{}"
    original_config.write_bytes(config_payload)
    sealed_config.write_bytes(config_payload)
    config_digest = hashlib.sha256(config_payload).hexdigest()

    monkeypatch.setattr(
        LAUNCHER,
        "_ACTIVE_IQ_AUTHORITY_MAPPING",
        {str(original_iq.absolute()): str(sealed_iq.absolute())},
    )
    monkeypatch.setattr(
        LAUNCHER,
        "_ACTIVE_PROJECT_ARTIFACT_MAPPING",
        {str(original_config.absolute()): str(sealed_config.absolute())},
    )
    monkeypatch.setattr(LAUNCHER, "EXPECTED_UNIT_COUNT", 0)
    capacity_phases: list[str] = []
    monkeypatch.setattr(
        LAUNCHER,
        "_require_results_available",
        lambda *, phase: capacity_phases.append(phase) or {"phase": phase},
    )
    monkeypatch.setattr(LAUNCHER.FROZEN, "_limits", lambda _plan: {})
    monkeypatch.setattr(
        LAUNCHER.FROZEN, "_units_for_source", lambda *, plan, source: []
    )
    monkeypatch.setattr(
        LAUNCHER.FROZEN, "build_units", lambda *, plan, source_manifest: []
    )
    monkeypatch.setattr(LAUNCHER.FROZEN, "_null_id", lambda _control: "zero")

    def synthetic_output_directory(path: Path, *, parent: Path, create: bool) -> Path:
        absolute = path.absolute()
        assert absolute.parent == parent.absolute()
        if create:
            absolute.mkdir(mode=0o700, exist_ok=True)
        assert absolute.is_dir()
        return absolute

    # Output ownership/topology has dedicated tests; this test also runs under
    # the root-only kernel smoke and is intentionally scoped to input routing.
    monkeypatch.setattr(LAUNCHER, "_exact_output_directory", synthetic_output_directory)

    transformed_sources: list[Path] = []

    def transform(source: Path, output: Path, **_kwargs: object):
        transformed_sources.append(Path(source).absolute())
        assert Path(source).absolute() == sealed_iq.absolute()
        output.write_bytes(b"\x00" * len(payload))
        return SimpleNamespace(
            output_size_bytes=len(payload),
            output_sha256=hashlib.sha256(b"\x00" * len(payload)).hexdigest(),
            to_dict=lambda: {"source_path": str(Path(source).absolute())},
        )

    monkeypatch.setattr(
        LAUNCHER.FROZEN,
        "NULL_TRANSFORM",
        SimpleNamespace(transform_ci16_null=transform),
    )
    original_identity = LAUNCHER.regular_file_identity

    def reject_original_iq(path: Path, *args: object, **kwargs: object):
        assert Path(path).absolute() != original_iq.absolute(), (
            "post-ACK campaign attempted to open original IQ instead of its authority"
        )
        return original_identity(path, *args, **kwargs)

    monkeypatch.setattr(LAUNCHER, "regular_file_identity", reject_original_iq)

    output_root = tmp_path / "campaign"
    output_root.mkdir(mode=0o700)
    plan = {
        "provenance": {
            "candidate_config": {
                "path": str(original_config.absolute()),
                "size_bytes": len(config_payload),
                "sha256": config_digest,
            }
        },
        "null_controls": {"chunk_complex_samples": 1},
    }
    signal = {
        "observation_id": 4500,
        "satellite_id": "TEST",
        "input_kind": "signal",
        "path": str(original_iq.absolute()),
        "size_bytes": len(payload),
        "sha256": digest,
    }
    manifest = LAUNCHER._campaign_body(
        plan_path=tmp_path / "plan.json",
        acquisition_path=tmp_path / "acquisition.json",
        source_manifest_path=tmp_path / "source.json",
        candidate_config_path=original_config,
        amendment_path=tmp_path / "amendment.json",
        runtime_guard_path=tmp_path / "guard.json",
        output_root=output_root,
        maximum_workers=1,
        plan=plan,
        plan_identity={"path": "/plan", "size_bytes": 1, "sha256": "a" * 64},
        acquisition_identity={
            "path": "/acquisition", "size_bytes": 1, "sha256": "b" * 64
        },
        source_manifest={
            "signals": [signal],
            "controls_per_observation": [{"kind": "all_zero"}],
        },
        source_identity={"path": "/source", "size_bytes": 1, "sha256": "c" * 64},
        amendment_identity={
            "path": "/amendment", "size_bytes": 1, "sha256": "d" * 64
        },
        runtime_guard_identity={
            "path": "/guard", "size_bytes": 1, "sha256": "e" * 64
        },
        runtime_guard={},
        exposure_by_unit={},
        normalizer_identity={
            "path": "/normalizer", "size_bytes": 1, "sha256": "f" * 64
        },
    )
    assert manifest["unit_count"] == 0
    assert transformed_sources == [sealed_iq.absolute()]
    assert capacity_phases == [
        "campaign body and output directory creation",
        "source-unit batch",
        "null transform",
        "source-unit batch",
    ]


def test_execute_campaign_uses_only_authorities_and_never_runs_original_runtime_validator(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The complete v3 execute gate never reopens IQ or invokes ldd by project path."""

    logical_root = LAUNCHER.PROJECT_ROOT / "synthetic-authority-execute-test"
    logical_iq = logical_root / "observation.iq"
    logical_plan = logical_root / "plan.json"
    logical_acquisition = logical_root / "acquisition.json"
    logical_source = logical_root / "source.json"
    logical_amendment = logical_root / "amendment.json"
    sealed_root = tmp_path / "sealed"
    sealed_root.mkdir()
    sealed_iq = sealed_root / "observation.iq"
    iq_payload = b"sealed synthetic bytes"
    sealed_iq.write_bytes(iq_payload)
    iq_sha256 = hashlib.sha256(iq_payload).hexdigest()

    project_mapping: dict[str, str] = {}
    iq_mapping = {str(logical_iq): str(sealed_iq)}
    tool_identities: dict[str, dict[str, object]] = {}
    for index, (label, logical_path) in enumerate(
        LAUNCHER.FROZEN.CAMPAIGN_EXECUTION_TOOL_PATHS.items()
    ):
        payload = f"sealed tool {label} {index}\n".encode("utf-8")
        backing = sealed_root / "tools" / f"{index}-{logical_path.name}"
        backing.parent.mkdir(exist_ok=True)
        backing.write_bytes(payload)
        project_mapping[str(logical_path.absolute())] = str(backing)
        tool_identities[label] = _identity(logical_path, payload)

    normalizer_payload = b"sealed amended normalizer\n"
    normalizer_backing = sealed_root / "amended-normalizer.py"
    normalizer_backing.write_bytes(normalizer_payload)
    project_mapping[str(LAUNCHER.AMENDED_NORMALIZER_PATH.absolute())] = str(
        normalizer_backing
    )

    original_runtime_root = logical_root / "candidate-runtime"
    plan = {
        "provenance": {
            "campaign_execution_tools": tool_identities,
            "candidate_runtime_lock": {
                "runtime_manifest": {
                    "environment": {
                        "launcher_path": str(original_runtime_root / "bin/python")
                    }
                }
            },
        }
    }
    plan_payload = (json.dumps(plan, sort_keys=True) + "\n").encode("utf-8")
    plan_backing = sealed_root / "plan.json"
    plan_backing.write_bytes(plan_payload)
    plan_identity = _identity(logical_plan, plan_payload)

    acquisition = {
        "schema_version": "blind-phase-holdout-acquisition-manifest-v1",
        "status": "complete",
        "execution_plan": plan_identity,
        "objects": [
            {
                "observation_id": 5000 + index,
                "local_path": str(logical_iq),
                "actual_size_bytes": len(iq_payload),
                "sha256": iq_sha256,
            }
            for index in range(30)
        ],
    }
    acquisition["manifest_payload_sha256"] = LAUNCHER.sha256_document(acquisition)
    acquisition_payload = (
        json.dumps(acquisition, sort_keys=True) + "\n"
    ).encode("utf-8")
    acquisition_backing = sealed_root / "acquisition.json"
    acquisition_backing.write_bytes(acquisition_payload)

    source_payload = b"{}\n"
    amendment_payload = b"{}\n"
    source_backing = sealed_root / "source.json"
    amendment_backing = sealed_root / "amendment.json"
    source_backing.write_bytes(source_payload)
    amendment_backing.write_bytes(amendment_payload)
    project_mapping.update(
        {
            str(logical_plan): str(plan_backing),
            str(logical_acquisition): str(acquisition_backing),
            str(logical_source): str(source_backing),
            str(logical_amendment): str(amendment_backing),
        }
    )
    monkeypatch.setattr(LAUNCHER, "_AUTHORITY_MAPPING_ACTIVE", True)
    monkeypatch.setattr(LAUNCHER, "_ACTIVE_IQ_AUTHORITY_MAPPING", iq_mapping)
    monkeypatch.setattr(
        LAUNCHER, "_ACTIVE_PROJECT_ARTIFACT_MAPPING", project_mapping
    )

    guard_path = tmp_path / "runtime-guard.json"
    guard_path.write_bytes(b"guard\n")
    guard_path.chmod(0o444)
    output_parent = tmp_path / "results"
    output_parent.mkdir(mode=0o700)
    output_root = output_parent / "campaign-output"
    guard_identity = {
        "path": str(guard_path), "size_bytes": 6, "sha256": "9" * 64
    }
    guard = {
        "control_parent": {"path": str(tmp_path)},
        "campaign_results_parent": {"path": str(output_parent)},
    }

    original_lstat = Path.lstat

    def synthetic_metadata(path: Path):
        status = original_lstat(path)
        if Path(path) not in {guard_path, output_parent}:
            return status
        values = {
            name: getattr(status, name)
            for name in dir(status)
            if name.startswith("st_")
        }
        if Path(path) == guard_path:
            values.update(st_uid=0, st_gid=0, st_mode=stat.S_IFREG | 0o444)
        else:
            values.update(st_uid=1000, st_gid=1000, st_mode=stat.S_IFDIR | 0o700)
        return SimpleNamespace(**values)

    monkeypatch.setattr(Path, "lstat", synthetic_metadata)
    original_load_json = LAUNCHER.load_json_object

    def load_json(path: Path, maximum_bytes: int = LAUNCHER.FROZEN.MAXIMUM_JSON_BYTES):
        if Path(path).absolute() == guard_path:
            return guard, guard_identity
        return original_load_json(path, maximum_bytes)

    monkeypatch.setattr(LAUNCHER, "load_json_object", load_json)
    monkeypatch.setattr(LAUNCHER, "_require_candidate_interpreter", lambda _plan: {})
    monkeypatch.setattr(LAUNCHER, "_validate_campaign_identity", lambda _guard: None)
    monkeypatch.setattr(LAUNCHER, "_validate_mount_guard", lambda _guard: None)
    monkeypatch.setattr(LAUNCHER, "_validate_release_constants", lambda **_kwargs: None)
    monkeypatch.setattr(LAUNCHER, "_validate_source_manifest", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(LAUNCHER, "validate_amendment", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(LAUNCHER, "validate_runtime_guard", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        LAUNCHER, "_validate_component_runtime_closure", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(LAUNCHER, "_output_binding_document", lambda **_kwargs: {})
    monkeypatch.setattr(LAUNCHER, "_prepare_output_root", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        LAUNCHER, "_validate_runtime_authority_mounts_from_guard",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        LAUNCHER,
        "_campaign_body",
        lambda **_kwargs: {"status": "synthetic-complete-without-decoder"},
    )
    capacity_phases: list[str] = []
    monkeypatch.setattr(
        LAUNCHER,
        "_require_results_available",
        lambda *, phase: capacity_phases.append(phase) or {"phase": phase},
    )

    forbidden_runtime_validation: list[object] = []

    def forbidden_validate_runtime(*_args: object, **_kwargs: object) -> None:
        forbidden_runtime_validation.append(True)
        raise AssertionError("original-path release validator/ldd was invoked")

    fake_release = SimpleNamespace(validate_runtime_lock_live=forbidden_validate_runtime)

    def fake_load_module(_name: str, path: Path):
        return fake_release if Path(path) == LAUNCHER.FROZEN.CANDIDATE_RELEASE_PATH else SimpleNamespace()

    monkeypatch.setattr(LAUNCHER, "_load_module", fake_load_module)
    monkeypatch.setattr(
        LAUNCHER.FROZEN,
        "_activate_plan_bound_modules",
        lambda _plan: forbidden_validate_runtime(),
    )

    original_open = LAUNCHER.os.open
    opened: list[Path] = []

    def reject_original_iq(path, flags, *args, **kwargs):
        candidate = Path(path).absolute()
        if candidate == logical_iq:
            raise AssertionError("execute_campaign opened original IQ after ACK")
        opened.append(candidate)
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(LAUNCHER.os, "open", reject_original_iq)
    expected_locals = dict(LAUNCHER._EXPECTED_LOCAL_MODULE_SHA256)
    frozen_state = {
        name: getattr(LAUNCHER.FROZEN, name)
        for name in (
            "BOUNDED", "SANDBOX", "NORMALIZER", "RELEASE", "NULL_TRANSFORM",
            "_PLAN_BOUND_MODULES_ACTIVE",
        )
    }
    try:
        result = LAUNCHER.execute_campaign(
            plan_path=logical_plan,
            acquisition_path=logical_acquisition,
            source_manifest_path=logical_source,
            candidate_config_path=logical_root / "candidate.json",
            amendment_path=logical_amendment,
            expected_amendment_sha256=hashlib.sha256(amendment_payload).hexdigest(),
            runtime_guard_path=guard_path,
            expected_runtime_guard_sha256=str(guard_identity["sha256"]),
            output_root=output_root,
            maximum_workers=1,
        )
    finally:
        LAUNCHER._EXPECTED_LOCAL_MODULE_SHA256.clear()
        LAUNCHER._EXPECTED_LOCAL_MODULE_SHA256.update(expected_locals)
        for name, value in frozen_state.items():
            setattr(LAUNCHER.FROZEN, name, value)
    assert result == {"status": "synthetic-complete-without-decoder"}
    assert sealed_iq in opened
    assert logical_iq not in opened
    assert forbidden_runtime_validation == []
    assert capacity_phases == ["campaign output-root creation"]


@pytest.mark.skipif(
    os.geteuid() != 0
    or os.environ.get("TELEMETRY_YIELD_RUN_ROOT_TMPFS_TEST") != "1"
    or shutil.which("unshare") is None,
    reason="requires explicit root opt-in and a private throwaway mount namespace",
)
def test_tmpfs_snapshot_survives_copy_fd_close_and_readonly_remount(
    tmp_path: Path,
) -> None:
    """Kernel integration smoke; never mutates the caller's mount namespace."""

    probe = r'''
import hashlib, json, os, pathlib, subprocess, sys
target = pathlib.Path(sys.argv[1])
target.mkdir()
subprocess.run(["/usr/bin/mount", "-t", "tmpfs", "-o",
                "size=16m,mode=0700,nosuid,nodev,noexec", "tmpfs", str(target)], check=True)
payload = b"private tmpfs authority remains after all copy descriptors close"
path = target / "capture.iq"
fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC, 0o400)
os.write(fd, payload)
os.fsync(fd)
os.fchmod(fd, 0o444)
os.close(fd)
subprocess.run(["/usr/bin/mount", "-o", "remount,ro,nosuid,nodev,noexec", str(target)], check=True)
assert path.read_bytes() == payload
print(json.dumps({"sha256": hashlib.sha256(payload).hexdigest()}))
subprocess.run(["/usr/bin/umount", str(target)], check=True)
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
            str(tmp_path / "authority"),
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
    assert len(json.loads(completed.stdout)["sha256"]) == 64
