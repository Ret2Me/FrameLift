from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import sys
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (
    ROOT
    / "work/blind-phase-confirmatory-v2/build_runtime_guard_v1.py"
)


def _module():
    name = "blind_phase_runtime_guard_builder_v1_test_module"
    spec = importlib.util.spec_from_file_location(name, SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


BUILDER = _module()


def _identity(label: str, index: int) -> dict[str, object]:
    return {
        "path": f"/{label}",
        "size_bytes": index,
        "sha256": f"{index:x}" * 64,
    }


def _protected(path: Path, marker: str) -> dict[str, object]:
    return {
        "path": str(path.absolute()),
        "st_dev": 1,
        "st_ino": 2 if marker == "c" else 3,
        "uid": 0,
        "gid": 0,
        "mode": 0o555,
        "recursive_entry_count": 1,
        "recursive_manifest_sha256": marker * 64,
    }


def test_guard_is_exactly_accepted_by_amended_launcher(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    candidate = tmp_path / "candidate"
    component = tmp_path / "component"
    candidate.mkdir()
    component.mkdir()
    protected = {
        "candidate": _protected(candidate, "c"),
        "component": _protected(component, "d"),
    }
    plan = {
        "provenance": {
            "candidate_runtime_lock": {"candidate": True},
            "component_baseline_lock": {"component": True},
        }
    }
    context = {
        "plan": plan,
        "plan_identity": _identity("plan", 1),
        "acquisition_identity": _identity("acquisition", 2),
        "source_identity": _identity("source", 3),
        "amendment_identity": _identity("amendment", 4),
        "roots": {"candidate": candidate, "component": component},
    }
    monkeypatch.setattr(BUILDER, "_protected_roots", lambda roots: protected)
    monkeypatch.setattr(
        BUILDER, "_validate_candidate_runtime", lambda value: {"status": "PASS"}
    )
    monkeypatch.setattr(
        BUILDER, "_validate_component_runtime", lambda value: {"status": "PASS"}
    )
    monkeypatch.setattr(
        BUILDER.LAUNCHER.FROZEN,
        "_runtime_root",
        lambda lock: candidate.absolute(),
    )
    monkeypatch.setattr(
        BUILDER.LAUNCHER.FROZEN,
        "_component_root",
        lambda lock: component.absolute(),
    )
    monkeypatch.setattr(
        BUILDER.LAUNCHER,
        "recursive_protected_root_identity",
        lambda path: protected["candidate" if Path(path) == candidate else "component"],
    )
    guard = BUILDER.build_runtime_guard(context)
    unhashed = {
        key: value
        for key, value in guard.items()
        if key != "runtime_guard_payload_sha256"
    }
    assert guard["runtime_guard_payload_sha256"] == BUILDER.LAUNCHER.sha256_document(
        unhashed
    )
    assert guard["runtime_validation"] == {
        "full_live_validation_before_campaign": True,
        "full_live_validation_after_campaign": True,
        "full_live_validation_per_unit": False,
        "per_unit_guard": "light-plan-tool-source-config-iq",
    }
    assert guard["source_protection"]["launcher_write_access_to_source_tree"] is False
    BUILDER.LAUNCHER.validate_runtime_guard(
        guard,
        plan=plan,
        plan_identity=context["plan_identity"],
        acquisition_identity=context["acquisition_identity"],
        source_identity=context["source_identity"],
        amendment_identity=context["amendment_identity"],
    )


def test_acquisition_metadata_validation_never_dereferences_iq(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan_identity = _identity("plan", 1)
    acquisition = {
        "schema_version": "blind-phase-holdout-acquisition-manifest-v1",
        "status": "complete",
        "execution_plan": plan_identity,
        "objects": [
            {
                "observation_id": index,
                "local_path": f"/definitely-absent/holdout-iq-v4/{index}.iq",
                "actual_size_bytes": 4,
                "sha256": hashlib.sha256(str(index).encode()).hexdigest(),
            }
            for index in range(30)
        ],
    }
    acquisition["manifest_payload_sha256"] = BUILDER.LAUNCHER.sha256_document(
        acquisition
    )
    monkeypatch.setattr(
        BUILDER.os,
        "open",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("metadata-only validation opened a filesystem path")
        ),
    )
    BUILDER._verified_acquisition_metadata_only(
        acquisition, plan_identity=plan_identity
    )


def test_scan_rejects_internal_and_escaping_symlinks(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    root.mkdir()
    (root / "target").write_bytes(b"target")
    (root / "internal").symlink_to("target")
    with pytest.raises(ValueError, match="internal symlink"):
        BUILDER._scan_tree(root, require_protected=False)
    (root / "internal").unlink()
    (root / "escape").symlink_to(tmp_path / "outside")
    with pytest.raises(ValueError, match="escape or broken symlink"):
        BUILDER._scan_tree(root, require_protected=False)


def test_scan_rejects_hardlinks_and_special_entries_before_seal(
    tmp_path: Path,
) -> None:
    root = tmp_path / "runtime"
    root.mkdir()
    original = root / "one"
    duplicate = root / "two"
    original.write_bytes(b"same inode")
    os.link(original, duplicate)
    mode_before = stat.S_IMODE(original.stat().st_mode)
    with pytest.raises(ValueError, match="hard-linked"):
        BUILDER._scan_tree(root, require_protected=False)
    assert stat.S_IMODE(original.stat().st_mode) == mode_before
    duplicate.unlink()
    fifo = root / "fifo"
    os.mkfifo(fifo)
    with pytest.raises(ValueError, match="special filesystem"):
        BUILDER._scan_tree(root, require_protected=False)


def test_seal_requires_euid_zero_before_touching_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(BUILDER.os, "geteuid", lambda: 1000)
    with pytest.raises(PermissionError, match="effective uid 0"):
        BUILDER.seal_roots(
            {
                "candidate": Path("/does/not/exist"),
                "component": Path("/also/absent"),
            }
        )


def test_runtime_roots_are_derived_only_from_exact_plan_locks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    candidate = tmp_path / "candidate"
    component = tmp_path / "component"
    candidate.mkdir()
    component.mkdir()
    candidate_lock = {"candidate": "lock"}
    component_lock = {"component": "lock"}
    seen: list[object] = []
    monkeypatch.setattr(
        BUILDER.LAUNCHER.FROZEN,
        "_runtime_root",
        lambda lock: seen.append(lock) or candidate,
    )
    monkeypatch.setattr(
        BUILDER.LAUNCHER.FROZEN,
        "_component_root",
        lambda lock: seen.append(lock) or component,
    )
    roots = BUILDER._runtime_roots(
        {
            "provenance": {
                "candidate_runtime_lock": candidate_lock,
                "component_baseline_lock": component_lock,
            }
        }
    )
    assert roots == {
        "candidate": candidate.absolute(),
        "component": component.absolute(),
    }
    assert seen == [candidate_lock, component_lock]


def test_protected_scan_rejects_current_user_owned_writable_tree(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    root.mkdir(mode=0o755)
    (root / "file").write_bytes(b"bytes")
    with pytest.raises(ValueError, match="non-root-owned or writable|root is non-root"):
        BUILDER._scan_tree(root, require_protected=True)


def test_candidate_validation_uses_bound_tool_gate_and_one_full_live_pass(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lock = {"runtime": "lock"}
    plan = {"provenance": {"candidate_runtime_lock": lock}}
    events: list[str] = []
    fake = SimpleNamespace(
        validate_runtime_lock_live=lambda value: events.append("live")
        or {"status": "PASS"}
    )
    monkeypatch.setattr(
        BUILDER.LAUNCHER.FROZEN,
        "_validate_campaign_execution_tools",
        lambda value: events.append("tools"),
    )
    monkeypatch.setattr(BUILDER, "_load_module", lambda name, path: fake)
    assert BUILDER._validate_candidate_runtime(plan) == {"status": "PASS"}
    assert events == ["tools", "tools", "live"]


@pytest.mark.parametrize(("initial_mode", "expected_mode"), [(0o764, 0o544), (0o666, 0o444)])
def test_file_seal_removes_write_bits_and_preserves_execute_bits(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    initial_mode: int,
    expected_mode: int,
) -> None:
    path = tmp_path / "runtime-file"
    path.write_bytes(b"bytes")
    path.chmod(initial_mode)
    original_fstat = BUILDER.os.fstat

    def root_owned_fstat(descriptor: int):
        status = original_fstat(descriptor)
        values = list(status)
        values[4] = 0
        values[5] = 0
        return os.stat_result(values)

    monkeypatch.setattr(BUILDER.os, "fchown", lambda descriptor, uid, gid: None)
    monkeypatch.setattr(BUILDER.os, "fstat", root_owned_fstat)
    BUILDER._seal_regular_file(path)
    assert stat.S_IMODE(path.stat().st_mode) == expected_mode


def test_publish_is_self_hashed_read_only_and_no_clobber(tmp_path: Path) -> None:
    output = tmp_path / "runtime-guard.json"
    document = {"schema_version": BUILDER.LAUNCHER.RUNTIME_GUARD_SCHEMA, "value": 1}
    document["runtime_guard_payload_sha256"] = BUILDER.LAUNCHER.sha256_document(
        document
    )
    BUILDER.publish_no_clobber(output, document)
    payload = output.read_bytes()
    digest = hashlib.sha256(payload).hexdigest()
    sidecar = output.with_name(output.name + ".sha256")
    assert json.loads(payload) == document
    assert sidecar.read_text(encoding="ascii") == f"{digest}  {output.name}\n"
    assert stat.S_IMODE(output.stat().st_mode) == 0o444
    assert stat.S_IMODE(sidecar.stat().st_mode) == 0o444
    with pytest.raises(ValueError, match="already exists"):
        BUILDER.publish_no_clobber(output, document)


def test_existing_sidecar_prevents_partial_guard(tmp_path: Path) -> None:
    output = tmp_path / "runtime-guard.json"
    output.with_name(output.name + ".sha256").write_text(
        "reserved\n", encoding="ascii"
    )
    with pytest.raises(ValueError, match="already exists"):
        BUILDER.publish_no_clobber(output, {"schema_version": "test"})
    assert not output.exists()


def test_component_validation_recomputes_exact_19_file_manifest_twice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner = tmp_path / "gr_component.py"
    schema = tmp_path / "schema.json"
    audit = tmp_path / "audit.json"
    for path in (runner, schema, audit):
        path.write_text(path.name, encoding="utf-8")
    runtime = {
        "golden_root": str(tmp_path / "component"),
        "files": {f"file-{index}": {"record": index} for index in range(19)},
    }
    lock = {
        "baseline_id": "baseline",
        "runner": BUILDER.LAUNCHER.regular_file_identity(runner),
        "result_schema": BUILDER.LAUNCHER.regular_file_identity(schema),
        "audit": BUILDER.LAUNCHER.regular_file_identity(audit),
        "sample_rate_hz": 57_600,
        "baudrates": [1_200, 4_800, 9_600, 19_200],
        "g3ruh_modes": [False, True],
        "official_component_chain": [
            "gr_satellites.components.demodulators.fsk_demodulator",
            "gr_satellites.components.deframers.ax25_deframer",
        ],
        "runtime": runtime,
    }
    calls: list[Path] = []

    def manifest(path: Path):
        calls.append(path)
        return runtime

    fake = SimpleNamespace(
        BASELINE_ID="baseline",
        RATES=(1_200, 4_800, 9_600, 19_200),
        G3RUH_MODES=(False, True),
        _runtime_manifest=manifest,
    )
    monkeypatch.setattr(BUILDER.LAUNCHER.FROZEN, "COMPONENT_RUNNER", runner)
    monkeypatch.setattr(
        BUILDER.LAUNCHER.FROZEN,
        "_component_root",
        lambda value: Path(runtime["golden_root"]),
    )
    monkeypatch.setattr(BUILDER, "_load_module", lambda name, path: fake)
    result = BUILDER._validate_component_runtime(
        {"provenance": {"component_baseline_lock": lock}}
    )
    assert len(calls) == 2
    assert result["file_count"] == 19


def test_cli_exposes_only_explicit_state_changing_subcommands() -> None:
    with pytest.raises(SystemExit) as error:
        BUILDER.main([])
    assert error.value.code == 2
