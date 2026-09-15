from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import stat
import sys
import copy

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "work/blind-phase-confirmatory-v2/campaign_launcher_amended_v2.py"


def _module():
    name = "blind_phase_campaign_launcher_amended_v2_test_module"
    spec = importlib.util.spec_from_file_location(name, SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


LAUNCHER = _module()


def _root_owned_status(path: Path):
    actual = path.lstat()
    values = list(actual)
    if stat.S_ISLNK(actual.st_mode):
        values[0] = stat.S_IFLNK | 0o777
    elif stat.S_ISDIR(actual.st_mode):
        values[0] = stat.S_IFDIR | 0o555
    elif stat.S_ISREG(actual.st_mode):
        values[0] = stat.S_IFREG | 0o444
    else:
        values[0] = stat.S_IFMT(actual.st_mode) | 0o444
    values[4] = 0
    values[5] = 0
    return os.stat_result(values)


def _patch_owned(monkeypatch: pytest.MonkeyPatch, root: Path) -> None:
    monkeypatch.setattr(LAUNCHER, "_stable_lstat", _root_owned_status)
    monkeypatch.setattr(
        LAUNCHER.BASE,
        "_protected_root_lstat",
        lambda path: {
            "path": str(Path(path).absolute()),
            "st_dev": path.lstat().st_dev,
            "st_ino": path.lstat().st_ino,
            "uid": 0,
            "gid": 0,
            "mode": 0o555,
        },
    )


def test_recursive_identity_accepts_and_binds_only_internal_symlink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "runtime"
    target = root / "lib"
    target.mkdir(parents=True)
    (target / "module.py").write_bytes(b"module")
    (root / "lib64").symlink_to("lib", target_is_directory=True)
    _patch_owned(monkeypatch, root)
    captured: list[object] = []
    original_hash = LAUNCHER.BASE.sha256_document
    monkeypatch.setattr(
        LAUNCHER.BASE,
        "sha256_document",
        lambda value: captured.append(value) or original_hash(value),
    )
    identity = LAUNCHER.recursive_protected_root_identity(root)
    records = captured[-1]
    link = next(record for record in records if record["path"] == "lib64")
    assert link == {
        "path": "lib64",
        "kind": "symlink",
        "mode": 0o777,
        "uid": 0,
        "gid": 0,
        "link_target": "lib",
        "resolved_relative_path": "lib",
        "resolved_kind": "directory",
    }
    assert identity["recursive_entry_count"] == 3


@pytest.mark.parametrize("kind", ["escaping", "broken", "cycle"])
def test_recursive_identity_rejects_unsafe_symlink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    root = tmp_path / "runtime"
    root.mkdir()
    if kind == "escaping":
        outside = tmp_path / "outside"
        outside.write_bytes(b"outside")
        (root / "link").symlink_to(outside)
        message = "escapes"
    elif kind == "broken":
        (root / "link").symlink_to("absent")
        message = "broken or cyclic"
    else:
        (root / "one").symlink_to("two")
        (root / "two").symlink_to("one")
        message = "broken or cyclic"
    _patch_owned(monkeypatch, root)
    with pytest.raises(ValueError, match=message):
        LAUNCHER.recursive_protected_root_identity(root)


def test_recursive_identity_rejects_special_and_hardlinked_entries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "runtime"
    root.mkdir()
    fifo = root / "fifo"
    os.mkfifo(fifo)
    _patch_owned(monkeypatch, root)
    with pytest.raises(ValueError, match="special"):
        LAUNCHER.recursive_protected_root_identity(root)
    fifo.unlink()
    first = root / "first"
    first.write_bytes(b"same")
    os.link(first, root / "second")
    with pytest.raises(ValueError, match="hard-linked"):
        LAUNCHER.recursive_protected_root_identity(root)


def _mount_line(root: Path, *, options: str = "ro,relatime", mount_root: str | None = None) -> str:
    status = root.stat()
    major_minor = f"{os.major(status.st_dev)}:{os.minor(status.st_dev)}"
    return (
        f"91 24 {major_minor} {mount_root or root} {root} {options} shared:7 "
        f"- ext4 /dev/test rw,errors=remount-ro\n"
    )


def test_mountinfo_parser_and_exact_readonly_self_bind(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    mountinfo = tmp_path / "mountinfo"
    mountinfo.write_text(_mount_line(root), encoding="utf-8")
    identity = LAUNCHER._readonly_project_mount_identity(
        root, mountinfo_path=mountinfo
    )
    assert identity["mount_options"] == ["relatime", "ro"]
    assert identity["root"] == str(root)
    mountinfo.write_text(_mount_line(root, options="rw,relatime"), encoding="utf-8")
    with pytest.raises(ValueError, match="read-only self-bind"):
        LAUNCHER._readonly_project_mount_identity(root, mountinfo_path=mountinfo)
    mountinfo.write_text(_mount_line(root, mount_root="/"), encoding="utf-8")
    with pytest.raises(ValueError, match="read-only self-bind"):
        LAUNCHER._readonly_project_mount_identity(root, mountinfo_path=mountinfo)


def test_mountinfo_parser_decodes_kernel_path_escapes() -> None:
    parsed = LAUNCHER._parse_mountinfo(
        "1 0 8:1 /x\\040y /m\\040p ro - ext4 /dev/x rw\n"
    )
    assert parsed[0]["root"] == "/x y"
    assert parsed[0]["mount_point"] == "/m p"


def test_readonly_project_rejects_nested_mount_that_could_bypass_ro(
    tmp_path: Path,
) -> None:
    root = tmp_path / "project"
    nested = root / "nested"
    nested.mkdir(parents=True)
    mountinfo = tmp_path / "mountinfo"
    mountinfo.write_text(
        _mount_line(root)
        + _mount_line(nested, options="rw,relatime", mount_root="/nested"),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="nested mount"):
        LAUNCHER._readonly_project_mount_identity(root, mountinfo_path=mountinfo)


def test_campaign_identity_is_rechecked_exactly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    guard = {
        "execution_identity": {
            "euid": 1000,
            "egid": 1000,
            "supplementary_gids": [4, 1000],
        }
    }
    monkeypatch.setattr(
        LAUNCHER,
        "_execution_identity",
        lambda: dict(guard["execution_identity"]),
    )
    LAUNCHER._validate_campaign_identity(guard)
    monkeypatch.setattr(
        LAUNCHER,
        "_execution_identity",
        lambda: {"euid": 1000, "egid": 1000, "supplementary_gids": [1000]},
    )
    with pytest.raises(ValueError, match="identity differs"):
        LAUNCHER._validate_campaign_identity(guard)


def test_light_unit_gate_rechecks_mount_before_base_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    guard = {"execution_identity": {"euid": 1000, "egid": 1000, "supplementary_gids": []}}
    monkeypatch.setattr(LAUNCHER, "_validate_campaign_identity", lambda value: events.append("uid"))
    monkeypatch.setattr(LAUNCHER, "_validate_mount_guard", lambda value: events.append("mount"))
    monkeypatch.setattr(LAUNCHER, "_BASE_LIGHT_UNIT_GATE", lambda **kwargs: events.append("base"))
    LAUNCHER._light_unit_gate(runtime_guard=guard)
    assert events == ["uid", "mount", "base"]


def _amendment_contract(normalizer: dict[str, object]) -> dict[str, object]:
    launcher = LAUNCHER.BASE.regular_file_identity(SCRIPT)
    builder = LAUNCHER.BASE.regular_file_identity(
        ROOT / "work/blind-phase-confirmatory-v2/build_runtime_guard_v2.py"
    )
    return {
        "amended_launcher": launcher,
        "amended_normalizer": normalizer,
        "component_runtime_closure": LAUNCHER.BASE.regular_file_identity(
            LAUNCHER.COMPONENT_CLOSURE_PATH, maximum_bytes=128 * 1024 * 1024
        ),
        "component_runtime_closure_generator": LAUNCHER.BASE.regular_file_identity(
            LAUNCHER.COMPONENT_CLOSURE_GENERATOR_PATH
        ),
        "runtime_history_disclosure": {
            "remaining_decoder_run_count_with_full_component_closure_bound": 6_166,
            "prior_exposed_decoder_unit_count": 2,
            "full_component_closure_for_prior_exposures_cryptographically_established": False,
            "full_30_observation_cohort_runtime_history": "mixed_and_non_pristine",
            "mandatory_sensitivity_observation_count": 29,
            "mandatory_sensitivity_excludes_entire_observation_ids": [4491],
        },
        "runtime_materialization_contract": {"component_runtime_closure": {
            "manifest": LAUNCHER.BASE.regular_file_identity(
                LAUNCHER.COMPONENT_CLOSURE_PATH, maximum_bytes=128 * 1024 * 1024
            ),
            "generator": LAUNCHER.BASE.regular_file_identity(
                LAUNCHER.COMPONENT_CLOSURE_GENERATOR_PATH
            ),
            "entry_count": LAUNCHER.EXPECTED_COMPONENT_ENTRY_COUNT,
            "content_manifest_sha256": LAUNCHER.EXPECTED_COMPONENT_CONTENT_MANIFEST_SHA256,
            "raw_manifest_sha256": LAUNCHER.EXPECTED_COMPONENT_RAW_MANIFEST_SHA256,
            "preseal_raw_exact_validation_required": True,
            "sealed_semantic_one_to_one_validation_required": True,
            "pre_and_post_campaign_validation_required": True,
            "executable_pth_included": True,
        }},
        "amended_implementation": {
            "launcher": launcher,
            "runtime_guard_builder": builder,
            "normalizer": normalizer,
        },
        "operational_scope": {
            "normalizer_or_scoring_changed": True,
            "scientific_normalization_or_scoring_changed": False,
            "scoring_changed": False,
            "normalizer_implementation_changed_for_bookkeeping_only_projection_fix": True,
        },
        "determinism_projection_fix": {
            "status": "approved_pre_outcome",
            "normalizer_v2": normalizer,
            "hashes_retained_in_provenance": [
                "process_receipt_sha256",
                "raw_result_payload_sha256",
            ],
            "hashes_removed_only_from_semantic_receiver_projection": [
                "process_receipt_sha256",
                "raw_result_payload_sha256",
            ],
            "frozen_endpoint_changed": False,
            "frozen_metric_changed": False,
            "selection_changed": False,
            "scoring_changed": False,
            "required_synthetic_tests": {
                "equal_semantic_output_different_bookkeeping": "PASS",
                "true_receiver_output_difference": "FAIL_REPEAT_GATE",
            },
            "applied_before_any_remaining_confirmatory_outcome": True,
        },
        "no_decoder_execution_since_v1": {"status": "PASS"},
    }


def test_amendment_v2_requires_exact_bookkeeping_projection_authorization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    normalizer = LAUNCHER.BASE.regular_file_identity(LAUNCHER.AMENDED_NORMALIZER_PATH)
    amendment = _amendment_contract(normalizer)
    monkeypatch.setattr(LAUNCHER, "_BASE_VALIDATE_AMENDMENT", lambda *args, **kwargs: {"u": {}})
    assert LAUNCHER.validate_amendment(
        amendment,
        plan_identity={},
        acquisition_identity={},
        source_identity={},
        normalizer_identity=normalizer,
    ) == {"u": {}}
    changed = copy.deepcopy(amendment)
    changed["determinism_projection_fix"]["scoring_changed"] = True
    with pytest.raises(ValueError, match="determinism correction"):
        LAUNCHER.validate_amendment(
            changed,
            plan_identity={},
            acquisition_identity={},
            source_identity={},
            normalizer_identity=normalizer,
        )


def test_launcher_v2_binds_normalizer_v2_without_modifying_v1_module() -> None:
    assert LAUNCHER.AMENDED_NORMALIZER_PATH.name == "normalize_result_amended_v2.py"
    assert LAUNCHER.BASE.AMENDED_NORMALIZER_PATH == LAUNCHER.AMENDED_NORMALIZER_PATH
    assert LAUNCHER.BASE.AMENDMENT_SCHEMA == LAUNCHER.AMENDMENT_SCHEMA


def test_output_binding_v1_schema_binds_actual_v2_launcher(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        LAUNCHER,
        "_BASE_OUTPUT_BINDING_DOCUMENT",
        lambda **kwargs: {
            "schema_version": LAUNCHER.OUTPUT_BINDING_SCHEMA,
            "amended_launcher": {"sha256": "0" * 64},
            "output_binding_payload_sha256": "1" * 64,
        },
    )
    document = LAUNCHER._output_binding_document()
    assert document["schema_version"] == LAUNCHER.OUTPUT_BINDING_SCHEMA
    assert document["amended_launcher"] == LAUNCHER.BASE.regular_file_identity(SCRIPT)
    unhashed = dict(document)
    digest = unhashed.pop("output_binding_payload_sha256")
    assert digest == LAUNCHER.BASE.sha256_document(unhashed)


def test_verified_loader_rejects_fake_base_before_execution(tmp_path: Path) -> None:
    fake = tmp_path / "fake_base.py"
    fake.write_text("raise AssertionError('must not execute')\n", encoding="utf-8")
    name = "fake_untrusted_launcher_base"
    with pytest.raises(RuntimeError, match="unbound local module"):
        LAUNCHER._load_module(name, fake)
    assert name not in sys.modules


def test_verified_loader_detects_dependency_mutation_during_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dependency = tmp_path / "racing_base.py"
    dependency.write_bytes(b"VALUE = 1\n")
    expected = LAUNCHER.hashlib.sha256(dependency.read_bytes()).hexdigest()
    monkeypatch.setitem(
        LAUNCHER._EXPECTED_LOCAL_MODULE_SHA256, str(dependency.absolute()), expected
    )
    original_read = LAUNCHER.os.read
    changed = False

    def racing_read(descriptor: int, count: int) -> bytes:
        nonlocal changed
        block = original_read(descriptor, count)
        if block and not changed:
            changed = True
            dependency.write_bytes(b"VALUE = 2\n")
        return block

    monkeypatch.setattr(LAUNCHER.os, "read", racing_read)
    name = "racing_untrusted_launcher_base"
    with pytest.raises(RuntimeError, match="identity mismatch"):
        LAUNCHER._load_module(name, dependency)
    assert name not in sys.modules


def test_launcher_v2_is_standalone_and_only_binds_frozen_original() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    assert "campaign_launcher_amended.py" not in source
    assert set(LAUNCHER._EXPECTED_LOCAL_MODULE_SHA256) == {
        str(LAUNCHER.FROZEN_LAUNCHER_PATH.absolute()),
        str(LAUNCHER.AMENDED_NORMALIZER_PATH.absolute()),
    }


def test_candidate_interpreter_requires_isolated_mode_before_campaign(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = tmp_path / "venv"
    interpreter = runtime / "bin/python"
    interpreter.parent.mkdir(parents=True)
    interpreter.write_bytes(b"python")
    identity = LAUNCHER.BASE.regular_file_identity(interpreter)
    plan = {"provenance": {"candidate_runtime_lock": {"runtime_manifest": {
        "environment": {"launcher_path": str(interpreter), "interpreter_target": identity,
            "launcher_is_symlink": False}}}}}
    monkeypatch.setattr(LAUNCHER.FROZEN, "_runtime_root", lambda lock: runtime)
    monkeypatch.setattr(LAUNCHER, "sys", type("S", (), {"flags": type("F", (), {
        "isolated": 0, "no_site": 0, "ignore_environment": 1})(),
        "executable": str(interpreter)})())
    with pytest.raises(PermissionError, match="sealed candidate Python with -I"):
        LAUNCHER._require_candidate_interpreter(plan)


def test_output_directory_rejects_symlink_and_wrong_mode(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir(mode=0o700)
    link = tmp_path / "units"
    link.symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="real uid/gid1000 mode0700"):
        LAUNCHER._exact_output_directory(link, parent=tmp_path, create=False)
    link.unlink()
    link.mkdir(mode=0o755)
    with pytest.raises(ValueError, match="mode0700"):
        LAUNCHER._exact_output_directory(link, parent=tmp_path, create=False)


@pytest.mark.parametrize("child", ["scratch", "output"])
def test_unit_workspace_rejects_descendant_symlink(tmp_path: Path, child: str) -> None:
    output = tmp_path / "campaign"
    units = output / "units"
    unit = units / "abc"
    unit.mkdir(parents=True, mode=0o700)
    output.chmod(0o700)
    units.chmod(0o700)
    outside = tmp_path / "outside"
    outside.mkdir(mode=0o700)
    (unit / child).symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="real uid/gid1000 mode0700"):
        LAUNCHER._validate_unit_workspace(output, "abc", require_children=False)


def test_unit_workspace_rejects_moved_units_directory_reintroduced_by_symlink(
    tmp_path: Path,
) -> None:
    output = tmp_path / "campaign"
    units = output / "units"
    unit = units / "abc"
    unit.mkdir(parents=True, mode=0o700)
    output.chmod(0o700)
    units.chmod(0o700)
    moved = output / "moved-units"
    units.rename(moved)
    units.symlink_to(moved, target_is_directory=True)
    with pytest.raises(ValueError, match="real uid/gid1000 mode0700"):
        LAUNCHER._validate_unit_workspace(output, "abc", require_children=False)


def test_receiver_argv_explicitly_disables_user_site_before_sandbox_chdir(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    frozen = ("/usr/bin/bwrap", "--clearenv", "--chdir", "/scratch", "--", "/runtime/x")
    monkeypatch.setattr(LAUNCHER, "_FROZEN_RECEIVER_ARGV", lambda **kwargs: frozen)
    hardened = LAUNCHER._receiver_argv_with_no_user_site(unit={})
    assert hardened == (
        "/usr/bin/bwrap",
        "--clearenv",
        "--setenv",
        "PYTHONNOUSERSITE",
        "1",
        "--chdir",
        "/scratch",
        "--",
        "/runtime/x",
    )


def test_receiver_argv_rejects_preexisting_user_site_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        LAUNCHER,
        "_FROZEN_RECEIVER_ARGV",
        lambda **kwargs: (
            "/usr/bin/bwrap",
            "--setenv",
            "PYTHONNOUSERSITE",
            "0",
            "--chdir",
            "/scratch",
            "--",
            "/runtime/x",
        ),
    )
    with pytest.raises(ValueError, match="unexpectedly sets PYTHONNOUSERSITE"):
        LAUNCHER._receiver_argv_with_no_user_site(unit={})


def test_component_closure_is_bound_and_live_semantics_are_rechecked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest_identity = LAUNCHER.BASE.regular_file_identity(
        LAUNCHER.COMPONENT_CLOSURE_PATH, maximum_bytes=128 * 1024 * 1024
    )
    generator_identity = LAUNCHER.BASE.regular_file_identity(
        LAUNCHER.COMPONENT_CLOSURE_GENERATOR_PATH
    )
    amendment = {"component_runtime_closure": manifest_identity,
        "component_runtime_closure_generator": generator_identity}
    guard = {"component_runtime_closure": manifest_identity,
        "component_runtime_closure_generator": generator_identity,
        "component_runtime_closure_validation": {
            "content_manifest_sha256": LAUNCHER.EXPECTED_COMPONENT_CONTENT_MANIFEST_SHA256,
            "preseal_raw_manifest_exact": True, "sealed_semantic_manifest_exact": True,
            "pre_campaign_semantic_manifest_exact": True,
            "post_campaign_semantic_manifest_required": True},
        "component_runtime_python_startup": {"bubblewrap_clearenv": True,
            "python_no_user_site": "1",
            "script_directory_inside_readonly_runtime_mount": "/runtime",
            "all_interpreter_library_paths_inside_component_runtime": True}}
    plan = {"provenance": {"component_baseline_lock": {}}}
    monkeypatch.setattr(LAUNCHER.FROZEN, "_component_root", lambda lock: Path("/runtime/component"))
    monkeypatch.setattr(LAUNCHER, "_component_content_manifest_sha256",
        lambda root: LAUNCHER.EXPECTED_COMPONENT_CONTENT_MANIFEST_SHA256)
    LAUNCHER._validate_component_runtime_closure(
        amendment=amendment, guard=guard, plan=plan
    )
    monkeypatch.setattr(LAUNCHER, "_component_content_manifest_sha256",
        lambda root: "0" * 64)
    with pytest.raises(ValueError, match="live sealed component tree differs"):
        LAUNCHER._validate_component_runtime_closure(
            amendment=amendment, guard=guard, plan=plan
        )


def test_runtime_guard_input_rejects_symlinked_parent_alias(tmp_path: Path) -> None:
    control = tmp_path / "control"
    control.mkdir()
    guard_path = control / "runtime-guard.json"
    guard_path.write_text("{}\n", encoding="ascii")
    guard = {"control_parent": {"path": str(control)}}
    LAUNCHER._validate_runtime_guard_input_path(guard_path, guard)
    alias = tmp_path / "control-alias"
    alias.symlink_to(control, target_is_directory=True)
    with pytest.raises(ValueError, match="exact canonical root-control child"):
        LAUNCHER._validate_runtime_guard_input_path(alias / guard_path.name, guard)
