from __future__ import annotations

import importlib.util
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import copy
import types

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "work/blind-phase-confirmatory-v2/campaign_launcher_amended_v3.py"


def _module():
    name = "blind_phase_campaign_launcher_amended_v3_test_module"
    spec = importlib.util.spec_from_file_location(name, SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


LAUNCHER = _module()


def test_v3_schema_and_inherited_scientific_counts_are_exact() -> None:
    assert LAUNCHER.AMENDMENT_SCHEMA == "blind-phase-confirmatory-operational-amendment-v3"
    assert LAUNCHER.RUNTIME_GUARD_SCHEMA == "blind-phase-confirmatory-runtime-guard-v3"
    assert LAUNCHER.EXPECTED_UNIT_COUNT == 6168


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
        ROOT / "work/blind-phase-confirmatory-v2/build_runtime_guard_v3.py"
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
        "persistent_mount_namespace_contract": {
            "schema_version": LAUNCHER.NAMESPACE_CONTRACT_SCHEMA,
            "control_parent": "/var/lib/telemetry-yield-confirmatory-v3",
            "campaign_results_parent": "/var/tmp/telemetry-yield-confirmatory-v3-results",
            "clone_newns_before_mount_mutation": True,
            "first_mount_mutation": ["/usr/bin/mount", "--make-rprivate", "/"],
            "root_recursively_private_before_project_bind": True,
            "expected_external_namespace_inodes": [
                4026531841, 4026532300, 4026532312, 4026532313, 4026532314,
                4026532315, 4026532319, 4026532320, 4026532373, 4026532384,
            ],
            "external_namespace_mount_delta": 0,
            "persistent_keeper_required": True,
            "stale_pid_or_namespace_handle_rejected": True,
            "automatic_respawn_after_ambiguous_start": False,
            "remaining_decoder_unit_count": 6166,
            "prior_exposed_unit_count": 2,
            "prior_exposed_units_rerun": False,
        },
    }


def test_real_v3_freezer_output_reaches_standalone_launcher_metadata_validator(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    freezer_test_path = ROOT / "tests/test_blind_phase_operational_amendment_v3.py"
    spec = importlib.util.spec_from_file_location("v3_freezer_fixture_for_launcher", freezer_test_path)
    assert spec is not None and spec.loader is not None
    fixture_module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = fixture_module
    spec.loader.exec_module(fixture_module)
    values = fixture_module._bundle(tmp_path, monkeypatch)
    freezer = fixture_module.FREEZER

    actual_paths = {
        "launcher_v3": SCRIPT,
        "runtime_guard_builder_v3": LAUNCHER.RUNTIME_GUARD_BUILDER_PATH,
        "normalizer_v2": LAUNCHER.AMENDED_NORMALIZER_PATH,
        "component_closure_generator": LAUNCHER.COMPONENT_CLOSURE_GENERATOR_PATH,
    }
    actual_identities: dict[str, dict[str, object]] = {}
    for name, path in actual_paths.items():
        identity = LAUNCHER.BASE.regular_file_identity(path)
        actual_identities[name] = identity
        values[f"{name}_path"] = path
        values[f"expected_{name}_sha256"] = identity["sha256"]
    closure_identity = LAUNCHER.BASE.regular_file_identity(
        LAUNCHER.COMPONENT_CLOSURE_PATH, maximum_bytes=128 * 1024 * 1024
    )
    values["component_closure_path"] = LAUNCHER.COMPONENT_CLOSURE_PATH
    values["expected_component_closure_sha256"] = closure_identity["sha256"]
    monkeypatch.setattr(freezer, "EXPECTED_NORMALIZER_V2_SHA256", actual_identities["normalizer_v2"]["sha256"])
    monkeypatch.setattr(freezer, "EXPECTED_COMPONENT_CLOSURE_SHA256", closure_identity["sha256"])
    monkeypatch.setattr(freezer, "EXPECTED_COMPONENT_CLOSURE_GENERATOR_SHA256", actual_identities["component_closure_generator"]["sha256"])
    monkeypatch.setattr(freezer, "EXPECTED_COMPONENT_CLOSURE_CONTENT_SHA256", LAUNCHER.EXPECTED_COMPONENT_CONTENT_MANIFEST_SHA256)
    monkeypatch.setattr(freezer, "EXPECTED_COMPONENT_CLOSURE_RAW_SHA256", LAUNCHER.EXPECTED_COMPONENT_RAW_MANIFEST_SHA256)

    dossier_path = Path(values["failed_v3_provenance_path"])
    dossier = json.loads(dossier_path.read_text(encoding="utf-8"))
    dossier["current_implementation"]["runtime_guard_builder_v3"] = (
        actual_identities["runtime_guard_builder_v3"]
    )
    dossier.pop("provenance_payload_sha256")
    dossier["provenance_payload_sha256"] = freezer.sha256_document(dossier)
    dossier_path.write_text(json.dumps(dossier, sort_keys=True) + "\n", encoding="utf-8")
    dossier_identity = LAUNCHER.BASE.regular_file_identity(dossier_path)
    values["expected_failed_v3_provenance_sha256"] = dossier_identity["sha256"]
    monkeypatch.setattr(
        freezer, "EXPECTED_FAILED_V3_PROVENANCE_SHA256", dossier_identity["sha256"]
    )
    monkeypatch.setattr(
        freezer, "EXPECTED_FAILED_V3_PROVENANCE_PAYLOAD_SHA256",
        dossier["provenance_payload_sha256"],
    )

    review_path = Path(values["review_path"])
    review = json.loads(review_path.read_text(encoding="utf-8"))
    review["reviewed_bindings"].update(actual_identities)
    review["reviewed_bindings"]["component_runtime_closure"] = closure_identity
    review["reviewed_bindings"]["failed_v3_activation_provenance"] = dossier_identity
    review.pop("review_payload_sha256")
    review["review_payload_sha256"] = freezer.sha256_document(review)
    review_path.write_text(json.dumps(review, sort_keys=True) + "\n", encoding="utf-8")
    review_sha = LAUNCHER.hashlib.sha256(review_path.read_bytes()).hexdigest()
    values["expected_review_sha256"] = review_sha
    amendment = freezer.build_amendment(
        **values, created_at="2026-09-04T20:01:00Z"
    )
    monkeypatch.setattr(
        LAUNCHER, "_validate_exposure_inventory", lambda document: {"exposure": document["prior_decoder_exposures"][0]}
    )
    monkeypatch.setattr(
        LAUNCHER,
        "_BASE_VALIDATE_AMENDMENT",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("v2 validator called")),
    )
    assert LAUNCHER.validate_amendment(
        amendment,
        plan_identity=amendment["execution_plan"],
        acquisition_identity=amendment["acquisition_manifest"],
        source_identity=amendment["source_manifest"],
        normalizer_identity=actual_identities["normalizer_v2"],
    )["exposure"] == amendment["prior_decoder_exposures"][0]


def test_launcher_v3_binds_normalizer_v2_without_modifying_v1_module() -> None:
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


def test_launcher_v3_is_standalone_and_only_binds_frozen_original() -> None:
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
    authority_parent = tmp_path / "authority"
    authority = authority_parent / "candidate/bin/python"
    authority.parent.mkdir(parents=True)
    authority.write_bytes(b"python")
    monkeypatch.setattr(LAUNCHER, "RUNTIME_AUTHORITY_PARENT_V3", authority_parent)
    monkeypatch.setattr(LAUNCHER, "sys", type("S", (), {"flags": type("F", (), {
        "isolated": 0, "no_site": 0, "ignore_environment": 1})(),
        "executable": str(authority)})())
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


def test_authority_mapping_rejects_unmapped_project_path_after_activation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(LAUNCHER, "_AUTHORITY_MAPPING_ACTIVE", True)
    monkeypatch.setattr(LAUNCHER, "_ACTIVE_IQ_AUTHORITY_MAPPING", {})
    monkeypatch.setattr(LAUNCHER, "_ACTIVE_PROJECT_ARTIFACT_MAPPING", {})
    with pytest.raises(PermissionError, match="lacks a sealed"):
        LAUNCHER._authority_path(LAUNCHER.ROOT / "unbound-control.json")


def test_receiver_argv_maps_every_project_file_to_root_control_authority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = LAUNCHER.ROOT / "reports/plan.json"
    runner = LAUNCHER.ROOT / "work/runner.py"
    sealed_plan = LAUNCHER.SEALED_PROJECT_ARTIFACTS_ROOT / "reports/plan.json"
    sealed_runner = LAUNCHER.SEALED_PROJECT_ARTIFACTS_ROOT / "work/runner.py"
    monkeypatch.setattr(LAUNCHER, "_AUTHORITY_MAPPING_ACTIVE", True)
    monkeypatch.setattr(
        LAUNCHER, "_ACTIVE_PROJECT_ARTIFACT_MAPPING",
        {str(plan): str(sealed_plan), str(runner): str(sealed_runner)},
    )
    monkeypatch.setattr(LAUNCHER, "_ACTIVE_IQ_AUTHORITY_MAPPING", {})
    monkeypatch.setattr(
        LAUNCHER, "_FROZEN_RECEIVER_ARGV",
        lambda **kwargs: (
            "/usr/bin/bwrap", "--ro-bind", str(plan), "/input/plan.json",
            "--ro-bind", str(runner), "/runtime/runner.py",
            "--chdir", "/scratch", "--", "/runtime/runner.py",
        ),
    )
    arguments = LAUNCHER._receiver_argv_with_no_user_site(unit={})
    assert str(plan) not in arguments and str(runner) not in arguments
    assert str(sealed_plan) in arguments and str(sealed_runner) in arguments


def test_authority_json_loader_never_opens_logical_project_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    logical = LAUNCHER.ROOT / "reports/authority-json-test.json"
    backing = tmp_path / "sealed.json"
    backing.write_text('{"value":7}', encoding="utf-8")
    monkeypatch.setattr(LAUNCHER, "_AUTHORITY_MAPPING_ACTIVE", True)
    monkeypatch.setattr(LAUNCHER, "_ACTIVE_IQ_AUTHORITY_MAPPING", {})
    monkeypatch.setattr(
        LAUNCHER, "_ACTIVE_PROJECT_ARTIFACT_MAPPING",
        {str(logical): str(backing)},
    )
    original_open = LAUNCHER.os.open
    opened: list[Path] = []

    def record_open(path, flags, *args, **kwargs):
        opened.append(Path(path))
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(LAUNCHER.os, "open", record_open)
    document, identity = LAUNCHER.load_authority_json_object(logical)
    assert document == {"value": 7}
    assert identity["path"] == str(logical)
    assert opened == [backing]


def test_verified_module_loader_executes_sealed_bytes_with_logical_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    logical = LAUNCHER.ROOT / "work/authority-module-test.py"
    backing = tmp_path / "sealed-module.py"
    payload = b"VALUE = 17\n"
    backing.write_bytes(payload)
    expected = LAUNCHER.hashlib.sha256(payload).hexdigest()
    monkeypatch.setitem(
        LAUNCHER._EXPECTED_LOCAL_MODULE_SHA256, str(logical), expected
    )
    monkeypatch.setattr(LAUNCHER, "_AUTHORITY_MAPPING_ACTIVE", True)
    monkeypatch.setattr(LAUNCHER, "_ACTIVE_IQ_AUTHORITY_MAPPING", {})
    monkeypatch.setattr(
        LAUNCHER, "_ACTIVE_PROJECT_ARTIFACT_MAPPING",
        {str(logical): str(backing)},
    )
    name = "blind_phase_authority_module_test"
    module = LAUNCHER._load_module(name, logical)
    try:
        assert module.VALUE == 17
        assert module.__file__ == str(logical)
    finally:
        sys.modules.pop(name, None)


def test_frozen_acquisition_preflight_opens_only_sealed_iq_after_ack_setup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    logical = LAUNCHER.SEALED_INPUT_SOURCE_ROOT / "authority-preflight.ci16"
    backing = tmp_path / "sealed-input.ci16"
    payload = b"exact sealed IQ"
    backing.write_bytes(payload)
    monkeypatch.setattr(LAUNCHER, "_AUTHORITY_MAPPING_ACTIVE", True)
    monkeypatch.setattr(
        LAUNCHER, "_ACTIVE_IQ_AUTHORITY_MAPPING", {str(logical): str(backing)}
    )
    monkeypatch.setattr(LAUNCHER, "_ACTIVE_PROJECT_ARTIFACT_MAPPING", {})
    plan_identity = {"path": "/plan", "size_bytes": 1, "sha256": "a" * 64}
    acquisition = {
        "schema_version": "blind-phase-holdout-acquisition-manifest-v1",
        "status": "complete",
        "execution_plan": plan_identity,
        "objects": [{
            "observation_id": 1,
            "local_path": str(logical),
            "actual_size_bytes": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
        }],
    }
    # The frozen validator requires exactly 30 unique records.
    acquisition["objects"] = [
        {**acquisition["objects"][0], "observation_id": index}
        for index in range(30)
    ]
    acquisition["manifest_payload_sha256"] = LAUNCHER.sha256_document(acquisition)
    opened: list[Path] = []
    original_open = LAUNCHER.os.open

    def record_open(path, flags, *args, **kwargs):
        opened.append(Path(path))
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(LAUNCHER.os, "open", record_open)

    def frozen_preflight(**_kwargs):
        return LAUNCHER.FROZEN._verified_acquisition(
            acquisition, plan_identity=plan_identity
        )

    monkeypatch.setattr(LAUNCHER, "_BASE_EXECUTE_CAMPAIGN", frozen_preflight)
    assert len(LAUNCHER._execute_base_with_authority_regular_files({})) == 30
    assert opened and set(opened) == {backing}
    assert logical not in opened


def test_authority_activation_never_calls_original_runtime_validator_or_ldd(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    frozen_tools = {
        label: {
            "path": str(path.absolute()), "size_bytes": 1,
            "sha256": f"{index:064x}",
        }
        for index, (label, path) in enumerate(
            LAUNCHER.FROZEN.CAMPAIGN_EXECUTION_TOOL_PATHS.items(), start=1
        )
    }
    plan = {"provenance": {"campaign_execution_tools": frozen_tools}}
    prior = {
        name: getattr(LAUNCHER.FROZEN, name)
        for name in (
            "_PLAN_BOUND_MODULES_ACTIVE", "BOUNDED", "SANDBOX", "NORMALIZER",
            "RELEASE", "NULL_TRANSFORM",
        )
    }
    calls: list[Path] = []

    class Loaded:
        def validate_runtime_lock_live(self, _lock):
            raise AssertionError("original runtime validator/ldd must be unreachable")

    monkeypatch.setattr(LAUNCHER.FROZEN, "_PLAN_BOUND_MODULES_ACTIVE", False)
    monkeypatch.setattr(
        LAUNCHER, "authority_regular_file_identity",
        lambda path, maximum_bytes=None: dict(
            frozen_tools[next(
                label for label, candidate in LAUNCHER.FROZEN.CAMPAIGN_EXECUTION_TOOL_PATHS.items()
                if candidate == path
            )]
        ),
    )
    monkeypatch.setattr(
        LAUNCHER, "_load_module",
        lambda _name, path: (calls.append(Path(path)), Loaded())[1],
    )
    monkeypatch.setattr(
        LAUNCHER.FROZEN, "_validate_campaign_execution_tools",
        lambda _plan: (_ for _ in ()).throw(AssertionError("original activation reached")),
    )
    try:
        LAUNCHER._activate_plan_bound_modules_from_authority(plan)
        assert calls == [
            LAUNCHER.FROZEN.BOUNDED_PATH,
            LAUNCHER.FROZEN.SANDBOX_PATH,
            LAUNCHER.FROZEN.NORMALIZER_PATH,
            LAUNCHER.FROZEN.CANDIDATE_RELEASE_PATH,
            LAUNCHER.FROZEN.NULL_TRANSFORM_PATH,
        ]
    finally:
        for name, value in prior.items():
            setattr(LAUNCHER.FROZEN, name, value)


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


def test_launcher_requires_current_process_inside_guarded_persistent_namespace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    control = tmp_path / "control"
    control.mkdir()
    active_path = control / "persistent-mount-namespace-active-v3.json"
    external = {"inventory_sha256": "e" * 64}
    document = {
        "schema_version": LAUNCHER.NAMESPACE_CONTRACT_SCHEMA,
        "status": "active_root_recursively_private",
        "keeper": {"pid": 12, "starttime_ticks": 77, "mount_namespace_inode": 4242},
        "external_inventory_after": external,
    }
    document[LAUNCHER.NAMESPACE_HASH_FIELD] = LAUNCHER.BASE.sha256_document(document)
    active_path.write_text(json.dumps(document), encoding="utf-8")
    identity = {"path": str(active_path), "size_bytes": 1, "sha256": "a" * 64}
    guard = {
        "control_parent": {"path": str(control)},
        "persistent_mount_namespace": identity,
        "persistent_mount_namespace_attestation": {
            "mount_namespace_inode": 4242,
            "root_recursively_private": True,
            "make_rprivate_completed_before_project_bind": True,
            "external_namespace_mount_delta": 0,
            "external_inventory_sha256": "e" * 64,
        },
    }
    monkeypatch.setattr(LAUNCHER, "load_json_object", lambda path: (document, identity))
    monkeypatch.setattr(LAUNCHER, "_validate_private_namespace_mountinfo", lambda: None)
    monkeypatch.setattr(LAUNCHER, "_process_starttime", lambda pid: 77)
    monkeypatch.setattr(LAUNCHER.os, "readlink", lambda path: "mnt:[4242]")
    LAUNCHER._validate_persistent_namespace_guard(guard)
    monkeypatch.setattr(LAUNCHER.os, "readlink", lambda path: "mnt:[9999]")
    with pytest.raises(ValueError, match="outside the guarded"):
        LAUNCHER._validate_persistent_namespace_guard(guard)


def test_launcher_rejects_shared_or_slave_mount_namespace(
    tmp_path: Path,
) -> None:
    mountinfo = tmp_path / "mountinfo"
    mountinfo.write_text(
        "1 0 0:1 / / rw shared:1 - ext4 /dev/root rw\n"
        "2 1 0:2 / /project ro master:1 - ext4 /dev/root rw\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="not recursively private"):
        LAUNCHER._validate_private_namespace_mountinfo(mountinfo_path=mountinfo)


def _install_campaign_handoff_fixture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, argv: list[str]
) -> tuple[Path, dict[str, object]]:
    control = tmp_path / "control"
    control.mkdir()
    monkeypatch.setattr(LAUNCHER, "CONTROL_PARENT_V3", control)
    results = Path.cwd()
    monkeypatch.setattr(LAUNCHER, "RESULTS_PARENT_V3", results)
    nonce = "2" * 64
    attempt_id = LAUNCHER.hashlib.sha256(nonce.encode("ascii")).hexdigest()
    handoff_path = control / f"namespace-role-handoff-v3-campaign-{attempt_id}.json"
    handoff_path.write_text("placeholder\n", encoding="ascii")
    handoff_path.chmod(0o444)
    active_path = control / "persistent-mount-namespace-active-v3.json"
    active_path.write_text("placeholder\n", encoding="ascii")
    attestation_path = control / LAUNCHER.RFC3161_ATTESTATION_NAME
    attestation_path.write_text("placeholder\n", encoding="ascii")
    attestation_path.chmod(0o444)
    attestation_identity = {
        "path": str(attestation_path), "size_bytes": attestation_path.stat().st_size,
        "sha256": "d" * 64,
    }
    argv.extend([
        "--rfc3161-attestation", str(attestation_path),
        "--expected-rfc3161-attestation-sha256", attestation_identity["sha256"],
    ])
    script_identity = {
        "path": str(SCRIPT), "size_bytes": SCRIPT.stat().st_size, "sha256": "a" * 64
    }
    candidate = tmp_path / "candidate-python"
    monkeypatch.setattr(LAUNCHER.sys, "executable", str(candidate))
    monkeypatch.setattr(LAUNCHER.os, "getpid", lambda: 4321)
    monkeypatch.setattr(
        LAUNCHER, "_execution_identity", lambda: {"euid": 1000, "egid": 1000}
    )
    monkeypatch.setattr(LAUNCHER, "_validate_private_namespace_mountinfo", lambda: None)
    monkeypatch.setattr(LAUNCHER.os, "readlink", lambda path: "mnt:[4242]")
    monkeypatch.setenv("TY_NAMESPACE_ROLE", "campaign")
    monkeypatch.setenv("TY_NAMESPACE_ROLE_HANDOFF", str(handoff_path))
    monkeypatch.setenv("TY_NAMESPACE_ROLE_NONCE", nonce)
    active_identity = {"path": str(active_path), "sha256": "b" * 64, "size_bytes": 12}
    document: dict[str, object] = {
        "schema_version": "blind-phase-confirmatory-namespace-role-handoff-v3",
        "status": "authorized_before_uid_drop_and_exec",
        "role": "campaign",
        "attempt_id": attempt_id,
        "nonce_sha256": attempt_id,
        "child_pid": 4321,
        "persistent_mount_namespace": active_identity,
        "campaign_metadata": {"amendment": {"path": "/amendment", "sha256": "e" * 64, "size_bytes": 1}},
        "runtime_guard": {"path": "/guard", "sha256": "f" * 64, "size_bytes": 1},
        "evaluator_lock": {"path": "/lock", "sha256": "1" * 64, "size_bytes": 1},
        "rfc3161_attestation": attestation_identity,
        "sealed_input_snapshot": {"status": "PASS"},
        "sealed_project_artifacts": {"status": "PASS"},
        "runtime_authority_mounts": {"candidate": {}, "component": {}},
        "results_authority_mount": {"path": str(results)},
        "capacity_contract": {"schema_version": "test-capacity"},
        "role_script": script_identity,
        "role_script_fd": 10,
        "role_script_bootstrap_schema": LAUNCHER.PINNED_SCRIPT_BOOTSTRAP_SCHEMA,
        "role_script_bootstrap_sha256": hashlib.sha256(
            LAUNCHER.PINNED_SCRIPT_BOOTSTRAP.encode("utf-8")
        ).hexdigest(),
        "role_arguments": argv,
        "exec_argv": [
            str(candidate), "-I", "-c", LAUNCHER.PINNED_SCRIPT_BOOTSTRAP,
            "10", str(SCRIPT), script_identity["sha256"], *argv,
        ],
        "cwd": str(results),
        "role_output_root": str(results / "campaign-output"),
        "role_output_filename": "unit-manifest-amended.json",
        "campaign_completion": None,
        "target_identity": {"euid": 1000, "egid": 1000},
        "preserved_stdio_fds": [0, 1, 2],
        "role_ack_fd": 9,
        "role_ack_schema": LAUNCHER.ROLE_ACK_SCHEMA,
        "role_ack_nonce_sha256": attempt_id,
        "shell_used": False,
        "no_new_privileges_required": True,
        "zero_capabilities_required": True,
    }
    document["handoff_payload_sha256"] = LAUNCHER.BASE.sha256_document(document)
    active = {"keeper": {"mount_namespace_inode": 4242}}
    attestation = {
        "schema_version": LAUNCHER.RFC3161_ATTESTATION_SCHEMA,
        "status": "PASS",
        "campaign_metadata": document["campaign_metadata"],
        "amendment": document["campaign_metadata"]["amendment"],
        "runtime_guard": document["runtime_guard"],
        "evaluator_lock": document["evaluator_lock"],
        "offline_verification": True,
    }
    attestation["attestation_payload_sha256"] = LAUNCHER.BASE.sha256_document(attestation)
    monkeypatch.setattr(
        LAUNCHER.BASE,
        "regular_file_identity",
        lambda path, **kwargs: script_identity if Path(path) == SCRIPT else active_identity,
    )
    monkeypatch.setattr(
        LAUNCHER,
        "_load_root_control_json_exact",
        lambda path: (
            (document, {"path": str(handoff_path), "size_bytes": 12, "sha256": "c" * 64})
            if Path(path) == handoff_path
            else ((attestation, attestation_identity) if Path(path) == attestation_path else (active, active_identity))
        ),
    )
    monkeypatch.setattr(
        LAUNCHER,
        "_role_process_security_state",
        lambda: {
            "euid": 1000, "egid": 1000, "supplementary_gids": [],
            "cap_inheritable": 0, "cap_permitted": 0, "cap_effective": 0,
            "cap_ambient": 0, "no_new_privileges": 1, "dumpable": 0,
        },
    )
    monkeypatch.setattr(
        LAUNCHER, "_write_role_acknowledgment", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(LAUNCHER, "_require_bootstrap_closed_fd", lambda fd: None)
    monkeypatch.setattr(
        LAUNCHER, "_configure_authority_mappings_from_guard",
        lambda identity: {
            key: document[key] for key in (
                "sealed_input_snapshot", "sealed_project_artifacts",
                "runtime_authority_mounts", "results_authority_mount",
                "capacity_contract",
            )
        },
    )
    monkeypatch.setattr(LAUNCHER, "_validate_rfc3161_consumer", lambda *a, **k: None)
    for key in list(os.environ):
        monkeypatch.delenv(key)
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    monkeypatch.setenv("LANG", "C")
    monkeypatch.setenv("LC_ALL", "C")
    monkeypatch.setenv("PYTHONNOUSERSITE", "1")
    monkeypatch.setenv("TY_NAMESPACE_ROLE", "campaign")
    monkeypatch.setenv("TY_NAMESPACE_ROLE_HANDOFF", str(handoff_path))
    monkeypatch.setenv("TY_NAMESPACE_ROLE_NONCE", nonce)
    monkeypatch.setenv("TY_ROLE_ACK_FD", "9")
    monkeypatch.setenv("TY_ROLE_ACK_NONCE", nonce)
    return handoff_path, document


def _campaign_test_argv() -> list[str]:
    return [
        "--execution-plan", "/project/plan.json",
        "--output-root", str(Path.cwd() / "campaign-output"),
    ]


def test_campaign_requires_exact_role_bound_handoff(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    argv = _campaign_test_argv()
    _install_campaign_handoff_fixture(tmp_path, monkeypatch, argv)
    base_argv = LAUNCHER._require_campaign_role_handoff(argv)
    assert "--rfc3161-attestation" not in base_argv
    assert "--expected-rfc3161-attestation-sha256" not in base_argv
    assert "TY_NAMESPACE_ROLE" not in os.environ
    assert "TY_NAMESPACE_ROLE_HANDOFF" not in os.environ
    assert "TY_NAMESPACE_ROLE_NONCE" not in os.environ
    assert "TY_ROLE_ACK_FD" not in os.environ
    assert "TY_ROLE_ACK_NONCE" not in os.environ


def test_campaign_direct_or_stale_rfc3161_attestation_fails_before_ack(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    argv = _campaign_test_argv()
    _path, document = _install_campaign_handoff_fixture(tmp_path, monkeypatch, argv)
    calls: list[object] = []
    monkeypatch.setattr(
        LAUNCHER, "_write_role_acknowledgment",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )
    document["rfc3161_attestation"] = {
        **document["rfc3161_attestation"], "sha256": "0" * 64,
    }
    unhashed = dict(document)
    unhashed.pop("handoff_payload_sha256")
    document["handoff_payload_sha256"] = LAUNCHER.BASE.sha256_document(unhashed)
    with pytest.raises(PermissionError, match="content differs"):
        LAUNCHER._require_campaign_role_handoff(argv)
    assert calls == []


def test_campaign_missing_rfc3161_cli_gate_cannot_bypass_handoff(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    argv = _campaign_test_argv()
    _install_campaign_handoff_fixture(tmp_path, monkeypatch, argv)
    direct = list(argv)
    for option in reversed((
        "--rfc3161-attestation",
        "--expected-rfc3161-attestation-sha256",
    )):
        index = direct.index(option)
        del direct[index:index + 2]
    calls: list[object] = []
    monkeypatch.setattr(
        LAUNCHER, "_write_role_acknowledgment",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )
    with pytest.raises(PermissionError, match="lacks exact RFC3161"):
        LAUNCHER._require_campaign_role_handoff(direct)
    assert calls == []


def test_campaign_rejects_evaluator_role_confusion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    argv = _campaign_test_argv()
    _path, document = _install_campaign_handoff_fixture(tmp_path, monkeypatch, argv)
    document["role"] = "evaluator"
    unhashed = dict(document)
    unhashed.pop("handoff_payload_sha256")
    document["handoff_payload_sha256"] = LAUNCHER.BASE.sha256_document(unhashed)
    with pytest.raises(PermissionError, match="content differs"):
        LAUNCHER._require_campaign_role_handoff(argv)


def test_campaign_rejects_argv_injection_after_authorization(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    authorized = _campaign_test_argv()
    _install_campaign_handoff_fixture(tmp_path, monkeypatch, authorized)
    with pytest.raises(PermissionError, match="content differs"):
        LAUNCHER._require_campaign_role_handoff([*authorized, "--unexpected"])


def test_campaign_rejects_active_namespace_identity_tamper(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    argv = _campaign_test_argv()
    active_path = tmp_path / "control/persistent-mount-namespace-active-v3.json"
    handoff_path, _document = _install_campaign_handoff_fixture(tmp_path, monkeypatch, argv)
    original = LAUNCHER._load_root_control_json_exact

    def tampered(path: Path):
        document, identity = original(path)
        if Path(path) == active_path:
            identity = {**identity, "sha256": "f" * 64}
        return document, identity

    monkeypatch.setattr(LAUNCHER, "_load_root_control_json_exact", tampered)
    with pytest.raises(PermissionError, match="identity changed"):
        LAUNCHER._require_campaign_role_handoff(argv)
    assert handoff_path.exists()


def test_campaign_rejects_post_exec_security_state_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    argv = _campaign_test_argv()
    _install_campaign_handoff_fixture(tmp_path, monkeypatch, argv)
    monkeypatch.setattr(
        LAUNCHER,
        "_role_process_security_state",
        lambda: {
            "euid": 1000, "egid": 1000, "supplementary_gids": [],
            "cap_inheritable": 0, "cap_permitted": 0, "cap_effective": 1,
            "cap_ambient": 0, "no_new_privileges": 1, "dumpable": 0,
        },
    )
    with pytest.raises(PermissionError, match="retained privilege"):
        LAUNCHER._require_campaign_role_handoff(argv)


def test_campaign_rejects_environment_injection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    argv = _campaign_test_argv()
    _install_campaign_handoff_fixture(tmp_path, monkeypatch, argv)
    monkeypatch.setenv("PYTHONPATH", "/attacker")
    with pytest.raises(PermissionError, match="environment is not exact"):
        LAUNCHER._require_campaign_role_handoff(argv)


def test_campaign_requires_positive_ack_before_clearing_authorization(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    argv = _campaign_test_argv()
    _install_campaign_handoff_fixture(tmp_path, monkeypatch, argv)
    monkeypatch.setattr(
        LAUNCHER,
        "_write_role_acknowledgment",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("ack failed")),
    )
    with pytest.raises(OSError, match="ack failed"):
        LAUNCHER._require_campaign_role_handoff(argv)
    assert os.environ["TY_NAMESPACE_ROLE"] == "campaign"


@pytest.mark.parametrize(
    "mutation",
    [
        lambda text: text.replace("0010", "0020", 1),
        lambda text: text.replace("Serial number:", "    0020 - 00 00 00 00 00 00 00 00-00 00 00 00 00 00 00 00\nSerial number:"),
        lambda text: text.replace(" 0000 -", " 0000: ", 1),
    ],
)
def test_campaign_rfc3161_parser_rejects_discontinuous_extra_or_ascii_rows(
    mutation,
) -> None:
    imprint = "ab" * 32
    octets = [imprint[index:index + 2] for index in range(0, 64, 2)]
    reply = (
        "Message data:\n"
        f"    0000 - {' '.join(octets[:8])}-{' '.join(octets[8:16])}\n"
        f"    0010 - {' '.join(octets[16:24])}-{' '.join(octets[24:])}\n"
        "Serial number: 1\n"
    )
    with pytest.raises(PermissionError, match="imprint|rows|row"):
        LAUNCHER._timestamp_imprint(mutation(reply))


def _launcher_capacity_fixture() -> tuple[dict[str, object], dict[str, object]]:
    snapshot = {
        "mounts": [
            {"source": {"size_bytes": 1024}}
            for _ in range(30)
        ]
    }
    sealed = 30 * 1024
    contract = {
        "schema_version": LAUNCHER.CAPACITY_CONTRACT_SCHEMA,
        "results_tmpfs_capacity_bytes": LAUNCHER.RESULTS_TMPFS_CAPACITY_BYTES,
        "results_minimum_available_bytes_before_work": (
            LAUNCHER.RESULTS_MINIMUM_AVAILABLE_BYTES_BEFORE_WORK
        ),
        "available_bytes_formula": LAUNCHER.CAPACITY_AVAILABLE_FORMULA,
        "persistent_export_filesystem_minimum_available_bytes": (
            LAUNCHER.PERSISTENT_EXPORT_MINIMUM_AVAILABLE_BYTES
        ),
        "maximum_workers": LAUNCHER.MAXIMUM_WORKERS,
        "per_worker_memory_budget_bytes": LAUNCHER.PER_WORKER_MEMORY_BUDGET_BYTES,
        "fixed_memory_headroom_bytes": LAUNCHER.FIXED_MEMORY_HEADROOM_BYTES,
        "sealed_input_bytes": sealed,
        "minimum_memavailable_bytes": (
            sealed
            + LAUNCHER.RESULTS_TMPFS_CAPACITY_BYTES
            + LAUNCHER.MAXIMUM_WORKERS * LAUNCHER.PER_WORKER_MEMORY_BUDGET_BYTES
            + LAUNCHER.FIXED_MEMORY_HEADROOM_BYTES
        ),
        "minimum_rlimit_nofile": LAUNCHER.MINIMUM_RLIMIT_NOFILE,
        "minimum_rlimit_nproc": LAUNCHER.MINIMUM_RLIMIT_NPROC,
        "minimum_cgroup_available_pids": LAUNCHER.MINIMUM_CGROUP_AVAILABLE_PIDS,
        "minimum_effective_cpu_count": LAUNCHER.MINIMUM_EFFECTIVE_CPU_COUNT,
        "candidate_scratch_limit_bytes": LAUNCHER.CANDIDATE_SCRATCH_LIMIT_BYTES,
        "candidate_scratch_limit_enforced": False,
        "candidate_scratch_limit_reason": LAUNCHER.CANDIDATE_SCRATCH_LIMIT_REASON,
    }
    return snapshot, contract


def test_launcher_capacity_contract_exact_shape_and_mutation_rejection() -> None:
    snapshot, contract = _launcher_capacity_fixture()
    assert len(contract) == 17
    assert LAUNCHER._validate_capacity_contract(contract, snapshot) == contract
    with pytest.raises(PermissionError, match="capacity contract differs"):
        LAUNCHER._validate_capacity_contract(
            {**contract, "results_tmpfs_capacity_bytes": 0}, snapshot
        )


def test_launcher_results_reserve_boundary_uses_bavail_and_exact_total(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    blocks = LAUNCHER.RESULTS_TMPFS_CAPACITY_BYTES // 4096
    available = LAUNCHER.RESULTS_MINIMUM_AVAILABLE_BYTES_BEFORE_WORK // 4096
    record = {
        "mount_point": str(tmp_path), "root": "/", "fs_type": "tmpfs",
        "mount_source": "tmpfs", "major_minor": "0:1",
        "mount_options": ["rw", "nosuid", "nodev", "noexec"],
        "super_options": ["rw", "size=2097152k"],
    }
    monkeypatch.setattr(LAUNCHER, "RESULTS_PARENT_V3", tmp_path)
    monkeypatch.setattr(LAUNCHER, "_parse_mountinfo", lambda text: [record])
    monkeypatch.setattr(Path, "read_text", lambda self, **kwargs: "mountinfo")
    real_lstat = Path.lstat
    status = real_lstat(tmp_path)
    record["major_minor"] = f"{os.major(status.st_dev)}:{os.minor(status.st_dev)}"
    state = {
        "available": available,
    }
    monkeypatch.setattr(
        LAUNCHER.os,
        "statvfs",
        lambda path: types.SimpleNamespace(
            f_frsize=4096, f_bsize=4096, f_blocks=blocks,
            f_bavail=state["available"], f_bfree=blocks * 100,
        ),
    )
    assert LAUNCHER._require_results_available(phase="boundary")[
        "available_bytes"
    ] == LAUNCHER.RESULTS_MINIMUM_AVAILABLE_BYTES_BEFORE_WORK
    state["available"] -= 1
    with pytest.raises(OSError) as error:
        LAUNCHER._require_results_available(phase="boundary")
    assert error.value.errno == LAUNCHER.errno.ENOSPC


def test_campaign_capacity_checks_precede_output_batch_decoder_and_null_work() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    body = source[source.index("def _campaign_body(") : source.index("def execute_campaign(")]
    assert body.index('_require_results_available(phase="campaign body') < body.index(
        "_exact_output_directory(output_root"
    )
    assert body.index('_require_results_available(phase="source-unit batch")') < body.index(
        "_parallel_map_ordered("
    )
    assert body.index('_require_results_available(phase="decoder process') < body.index(
        "FROZEN._run_one_unit("
    )
    assert body.index('_require_results_available(phase="null transform")') < body.index(
        "FROZEN.NULL_TRANSFORM.transform_ci16_null("
    )
