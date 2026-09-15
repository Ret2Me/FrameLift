from __future__ import annotations

import ast
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import argparse

import pytest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "work/blind-phase-confirmatory-v2/install_retry3_prestart_seed_quarantine_controller_v1.py"
SPEC = importlib.util.spec_from_file_location("retry3_quarantine_installer", SOURCE)
assert SPEC and SPEC.loader
INSTALLER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(INSTALLER)


def _install_args() -> list[str]:
    return [
        "--expected-installer-sha256", "1" * 64,
        "--expected-installer-test-sha256", "2" * 64,
        "--expected-installer-review-sha256", "3" * 64,
    ]


def test_source_compiles_and_executable_refreeze_is_unblocked() -> None:
    compile(SOURCE.read_bytes(), str(SOURCE), "exec")
    assert INSTALLER.INSTALLER_BLOCKED_PENDING_DESIGN_REVIEW is False


def test_installer_binds_executable_runner_and_test_exactly() -> None:
    assert INSTALLER.RUNNER_SHA256 == "57193465820276e1e1d0f4c7cbc672a75df401a5e8e903fd6e26fcddd216b12d"
    assert INSTALLER.RUNNER_SIZE == 56342
    assert INSTALLER.RUNNER_TEST_SHA256 == "44c5dd654a8491b557819adde1ed4ba015963eb65e98ee37d0ced9e7226fe48d"
    assert INSTALLER.RUNNER_TEST_SIZE == 40569
    assert INSTALLER.DESIGN_REVIEW_SHA256 == "4c106c82c16e01e491431e44576024289901b34ef04feec1940a88adeedd335d"
    assert INSTALLER.DESIGN_REVIEW_SIZE == 4762
    assert INSTALLER.DESIGN_REVIEW_SIDECAR_SHA256 == "5f217b0940c7da14a1c35743af9e84014773bbaf504ff7ba93eb9c46fbe823ca"
    assert INSTALLER.DESIGN_REVIEW_SIDECAR_SIZE == 125


def test_installer_ast_is_scoped_to_fresh_controller_creation_only() -> None:
    proof = INSTALLER._static_scope_proof(SOURCE.read_bytes())
    assert proof["anonymous_tmpfile_open_count"] == 1
    assert proof["exclusive_nonblocking_controller_flock_count"] == 1
    assert proof["sole_mkdir"] == "_create_staging:STAGING.name:parent"
    assert proof["sole_rename"] == "_rename_staging_noreplace:STAGING-to-CONTROLLER"
    assert proof["renameat2_uses_same_pinned_parent_dirfd"] is True
    assert proof["renameat2_flag"] == "RENAME_NOREPLACE"


@pytest.mark.parametrize(
    "mutant",
    [
        "def bad():\n    os.open('/etc/passwd', os.O_WRONLY)\n",
        "def bad():\n    os.open('/etc/passwd', os.O_RDWR)\n",
        "def bad():\n    os.unlink(SOURCE)\n",
        "def bad():\n    Path('/tmp/x').write_bytes(b'x')\n",
        "def bad():\n    os.system('true')\n",
        "def bad():\n    getattr(ctypes.CDLL(None), 'rename')\n",
    ],
)
def test_static_scope_proof_rejects_unused_mutation_surface(mutant: str) -> None:
    with pytest.raises(INSTALLER.InstallError, match="static mutation scope differs"):
        INSTALLER._static_scope_proof(SOURCE.read_bytes() + b"\n" + mutant.encode())


@pytest.mark.parametrize(
    "replacement",
    [
        "fcntl.flock(descriptor, fcntl.LOCK_SH | fcntl.LOCK_NB)",
        "fcntl.flock(descriptor, fcntl.LOCK_EX)",
    ],
)
def test_static_scope_proof_requires_exact_exclusive_nonblocking_flock(
    replacement: str,
) -> None:
    original = "fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)"
    before, separator, after = SOURCE.read_text(encoding="utf-8").rpartition(original)
    assert separator == original
    changed = before + replacement + after
    with pytest.raises(INSTALLER.InstallError, match="static mutation scope differs"):
        INSTALLER._static_scope_proof(changed.encode())


def _repo_fixture_file(path: Path, payload: bytes) -> None:
    path.write_bytes(payload)
    path.chmod(0o664)
    if os.geteuid() == 0:
        os.chown(path, 1000, 1000)


def test_installer_authority_binds_self_test_review_sidecar_and_zero_severity(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    installer_path = tmp_path / INSTALLER.INSTALLER_SOURCE.name
    test_path = tmp_path / INSTALLER.INSTALLER_TEST.name
    review_path = tmp_path / INSTALLER.INSTALLER_REVIEW.name
    sidecar_path = tmp_path / INSTALLER.INSTALLER_REVIEW_SIDECAR.name
    installer_payload = SOURCE.read_bytes()
    test_payload = b"exact installer tests\n"
    _repo_fixture_file(installer_path, installer_payload)
    _repo_fixture_file(test_path, test_payload)
    monkeypatch.setattr(INSTALLER, "INSTALLER_SOURCE", installer_path)
    monkeypatch.setattr(INSTALLER, "INSTALLER_TEST", test_path)
    monkeypatch.setattr(INSTALLER, "INSTALLER_REVIEW", review_path)
    monkeypatch.setattr(INSTALLER, "INSTALLER_REVIEW_SIDECAR", sidecar_path)
    monkeypatch.setattr(INSTALLER, "__file__", str(installer_path))
    installer_sha = hashlib.sha256(installer_payload).hexdigest()
    test_sha = hashlib.sha256(test_payload).hexdigest()
    _payload, installer_identity = INSTALLER._read_source_sha(installer_path, installer_sha)
    _payload, test_identity = INSTALLER._read_source_sha(test_path, test_sha)
    bindings = {
        "installer": INSTALLER._simple(installer_identity),
        "installer_tests": INSTALLER._simple(test_identity),
        "quarantine_runner": {
            "path": str(INSTALLER.RUNNER_SOURCE), "sha256": INSTALLER.RUNNER_SHA256,
            "size_bytes": INSTALLER.RUNNER_SIZE,
        },
        "quarantine_runner_tests": {
            "path": str(INSTALLER.RUNNER_TEST_SOURCE),
            "sha256": INSTALLER.RUNNER_TEST_SHA256,
            "size_bytes": INSTALLER.RUNNER_TEST_SIZE,
        },
        "quarantine_design_review": {
            "path": str(INSTALLER.DESIGN_REVIEW_SOURCE),
            "sha256": INSTALLER.DESIGN_REVIEW_SHA256,
            "size_bytes": INSTALLER.DESIGN_REVIEW_SIZE,
        },
        "quarantine_design_review_sidecar": {
            "path": str(INSTALLER.DESIGN_REVIEW_SIDECAR_SOURCE),
            "sha256": INSTALLER.DESIGN_REVIEW_SIDECAR_SHA256,
            "size_bytes": INSTALLER.DESIGN_REVIEW_SIDECAR_SIZE,
        },
        "static_scope_proof": INSTALLER._static_scope_proof(installer_payload),
    }
    unhashed = {
        "schema_version": INSTALLER.INSTALLER_REVIEW_SCHEMA,
        "status": "GO", "reviewed_bindings": bindings,
        "checks": INSTALLER.expected_installer_review_checks(),
        "severity_counts": {"P0": 0, "P1": 0, "P2": 0},
    }
    document = {
        **unhashed, "review_payload_sha256": INSTALLER._document_sha(unhashed),
    }
    review_payload = (
        json.dumps(document, allow_nan=False, indent=2, sort_keys=True) + "\n"
    ).encode()
    _repo_fixture_file(review_path, review_payload)
    review_sha = hashlib.sha256(review_payload).hexdigest()
    _repo_fixture_file(
        sidecar_path, f"{review_sha}  {review_path.name}\n".encode("ascii")
    )
    args = argparse.Namespace(
        expected_installer_sha256=installer_sha,
        expected_installer_test_sha256=test_sha,
        expected_installer_review_sha256=review_sha,
    )
    authority = INSTALLER._installer_authority(args)
    assert authority["installer"] == bindings["installer"]
    assert authority["static_scope_proof"] == bindings["static_scope_proof"]
    document["severity_counts"]["P1"] = 1
    malformed = (
        json.dumps(document, allow_nan=False, indent=2, sort_keys=True) + "\n"
    ).encode()
    _repo_fixture_file(review_path, malformed)
    malformed_sha = hashlib.sha256(malformed).hexdigest()
    _repo_fixture_file(
        sidecar_path, f"{malformed_sha}  {review_path.name}\n".encode("ascii")
    )
    args.expected_installer_review_sha256 = malformed_sha
    with pytest.raises(INSTALLER.InstallError, match="review semantics differ"):
        INSTALLER._installer_authority(args)


def test_sources_refuse_unfrozen_design_review_and_then_are_exact4(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(INSTALLER, "DESIGN_REVIEW_SHA256", None)
    with pytest.raises(INSTALLER.InstallError, match="not frozen"):
        INSTALLER._sources()
    monkeypatch.setattr(INSTALLER, "DESIGN_REVIEW_SHA256", "1" * 64)
    monkeypatch.setattr(INSTALLER, "DESIGN_REVIEW_SIZE", 100)
    monkeypatch.setattr(INSTALLER, "DESIGN_REVIEW_SIDECAR_SHA256", "2" * 64)
    monkeypatch.setattr(INSTALLER, "DESIGN_REVIEW_SIDECAR_SIZE", 110)
    sources = INSTALLER._sources()
    assert len(sources) == 4
    assert [row[4] for row in sources] == [0o555, 0o555, 0o444, 0o444]


def test_load_runner_requires_final_evidence_and_full64_paths() -> None:
    payload = INSTALLER.RUNNER_SOURCE.read_bytes()
    module = INSTALLER._load_runner(payload)
    assert module.QUARANTINE_TEMPLATE_BLOCKED_PENDING_EVIDENCE_AND_REVIEW is False
    assert Path(module.CONTROLLER) == INSTALLER.CONTROLLER
    changed = payload.replace(
        b"QUARANTINE_TEMPLATE_BLOCKED_PENDING_EVIDENCE_AND_REVIEW = False",
        b"QUARANTINE_TEMPLATE_BLOCKED_PENDING_EVIDENCE_AND_REVIEW = True ",
        1,
    )
    with pytest.raises(INSTALLER.InstallError, match="contract differs"):
        INSTALLER._load_runner(changed)


def test_exact_invocation_gate_precedes_credentials_and_source_reads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(INSTALLER, "INSTALLER_BLOCKED_PENDING_DESIGN_REVIEW", False)
    monkeypatch.setattr(
        INSTALLER.os, "geteuid", lambda: pytest.fail("credentials reached")
    )
    with pytest.raises(INSTALLER.InstallError, match="exact /usr/bin/python3.12"):
        INSTALLER.main()


def _install_main_fixture(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> tuple[Path, Path, Path, Path, tuple[tuple[Path, Path, str, int, int], ...]]:
    parent = tmp_path / "var-lib"
    parent.mkdir()
    parent.chmod(0o755)
    source = parent / "source"
    source.mkdir()
    destination = parent / "destination"
    controller = parent / "controller"
    staging = parent / "controller.installing-v1"
    definitions = (
        (INSTALLER.RUNNER_SOURCE.name, 0o555),
        (INSTALLER.RUNNER_TEST_SOURCE.name, 0o555),
        (INSTALLER.DESIGN_REVIEW_SOURCE.name, 0o444),
        (INSTALLER.DESIGN_REVIEW_SIDECAR_SOURCE.name, 0o444),
    )
    source_files: list[tuple[Path, Path, str, int, int]] = []
    for index, (name, mode) in enumerate(definitions):
        path = tmp_path / f"source-{index}"
        payload = f"payload-{index}".encode()
        path.write_bytes(payload)
        source_files.append(
            (path, controller / name, hashlib.sha256(payload).hexdigest(), len(payload), mode)
        )
    parent_status = parent.lstat()
    monkeypatch.setattr(INSTALLER, "INSTALLER_BLOCKED_PENDING_DESIGN_REVIEW", False)
    monkeypatch.setattr(INSTALLER, "VAR_LIB", parent)
    monkeypatch.setattr(INSTALLER, "SOURCE", source)
    monkeypatch.setattr(INSTALLER, "DESTINATION", destination)
    monkeypatch.setattr(INSTALLER, "CONTROLLER", controller)
    monkeypatch.setattr(INSTALLER, "STAGING", staging)
    monkeypatch.setattr(
        INSTALLER, "EXPECTED_VAR_LIB",
        {
            "path": str(parent), "st_dev": parent_status.st_dev,
            "st_ino": parent_status.st_ino, "uid": 0, "gid": 0,
            "mode": 0o755,
        },
    )
    monkeypatch.setattr(INSTALLER, "_sources", lambda: tuple(source_files))
    runner = type("Runner", (), {"_validate_invocation": staticmethod(lambda: None)})()
    monkeypatch.setattr(INSTALLER, "_load_runner", lambda _payload: runner)
    monkeypatch.setattr(INSTALLER, "_validate_invocation", lambda: None)
    monkeypatch.setattr(
        INSTALLER, "_installer_authority", lambda _args: {"review": "exact"}
    )
    gate = {"snapshot": "same", "zero": True}
    monkeypatch.setattr(INSTALLER, "_preinstall_gate", lambda _module: gate)
    monkeypatch.setattr(INSTALLER, "_source_gate", lambda _module: gate)
    return parent, source, destination, controller, tuple(source_files)


@pytest.mark.skipif(os.geteuid() != 0, reason="root O_TMPFILE/linkat metadata integration")
def test_install_one_is_root_immutable_no_clobber_and_same_inode(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    controller = tmp_path / "controller"
    controller.mkdir()
    controller.chmod(0o755)
    monkeypatch.setattr(INSTALLER, "CONTROLLER", controller)
    descriptor = os.open(
        controller, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW
    )
    try:
        target = controller / "artifact"
        identity = INSTALLER._install_one(descriptor, target, b"payload", 0o444)
        status = target.lstat()
        assert identity["sha256"] == hashlib.sha256(b"payload").hexdigest()
        assert identity["st_ino"] == status.st_ino
        assert (status.st_uid, status.st_gid, status.st_mode & 0o777) == (0, 0, 0o444)
        with pytest.raises(FileExistsError):
            INSTALLER._install_one(descriptor, target, b"replacement", 0o444)
        assert target.read_bytes() == b"payload"
        assert target.lstat().st_ino == status.st_ino
    finally:
        os.close(descriptor)


@pytest.mark.skipif(os.geteuid() != 0, reason="root exact5 controller integration")
def test_main_installs_exact5_in_temp_without_quarantine_or_lifecycle(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    parent = tmp_path / "var-lib"
    parent.mkdir()
    parent.chmod(0o755)
    source = parent / "source"
    source.mkdir()
    destination = parent / "destination"
    controller = parent / "controller"
    source_files: list[tuple[Path, Path, str, int, int]] = []
    for index, (name, mode) in enumerate(
        (
            (INSTALLER.RUNNER_SOURCE.name, 0o555),
            (INSTALLER.RUNNER_TEST_SOURCE.name, 0o555),
            (INSTALLER.DESIGN_REVIEW_SOURCE.name, 0o444),
            (INSTALLER.DESIGN_REVIEW_SIDECAR_SOURCE.name, 0o444),
        )
    ):
        path = tmp_path / f"source-{index}"
        payload = f"payload-{index}".encode()
        path.write_bytes(payload)
        source_files.append(
            (path, controller / name, hashlib.sha256(payload).hexdigest(), len(payload), mode)
        )
    parent_status = parent.lstat()
    monkeypatch.setattr(INSTALLER, "INSTALLER_BLOCKED_PENDING_DESIGN_REVIEW", False)
    monkeypatch.setattr(INSTALLER, "VAR_LIB", parent)
    monkeypatch.setattr(INSTALLER, "SOURCE", source)
    monkeypatch.setattr(INSTALLER, "DESTINATION", destination)
    monkeypatch.setattr(INSTALLER, "CONTROLLER", controller)
    monkeypatch.setattr(INSTALLER, "STAGING", parent / "controller.installing-v1")
    monkeypatch.setattr(
        INSTALLER, "EXPECTED_VAR_LIB",
        {
            "path": str(parent), "st_dev": parent_status.st_dev,
            "st_ino": parent_status.st_ino, "uid": 0, "gid": 0,
            "mode": 0o755,
        },
    )
    monkeypatch.setattr(INSTALLER, "_sources", lambda: tuple(source_files))
    runner = type("Runner", (), {"_validate_invocation": staticmethod(lambda: None)})()
    monkeypatch.setattr(INSTALLER, "_load_runner", lambda _payload: runner)
    monkeypatch.setattr(INSTALLER, "_validate_invocation", lambda: None)
    monkeypatch.setattr(
        INSTALLER, "_installer_authority", lambda _args: {"review": "exact"}
    )
    gate = {"snapshot": "same", "zero": True}
    monkeypatch.setattr(INSTALLER, "_preinstall_gate", lambda _module: gate)
    monkeypatch.setattr(INSTALLER, "_source_gate", lambda _module: gate)
    assert INSTALLER.main(_install_args()) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "PASS_EXACT5_OTMPFILE_LINKAT_NO_CLOBBER_INSTALL"
    assert output["quarantine_or_lifecycle_executed"] is False
    assert sorted(path.name for path in controller.iterdir()) == [
        "quarantine_retry3_prestart_seed_v1.py",
        "retry3-prestart-seed-quarantine-independent-review-v1.json",
        "retry3-prestart-seed-quarantine-independent-review-v1.json.sha256",
        "retry3-prestart-seed-quarantine.lock",
        "test_quarantine_retry3_prestart_seed_v1.py",
    ]
    assert source.exists() and not destination.exists()


@pytest.mark.skipif(os.geteuid() != 0, reason="root exact-prefix crash resume")
@pytest.mark.parametrize("boundary", [1, 2, 3, 4, 5])
def test_main_resumes_after_each_durable_artifact_prefix(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, boundary: int,
) -> None:
    parent, source, destination, controller, _sources = _install_main_fixture(
        monkeypatch, tmp_path
    )
    staging = parent / "controller.installing-v1"
    original = INSTALLER._install_one
    calls = 0

    def fail_after_link(*args: object, **kwargs: object) -> dict[str, object]:
        nonlocal calls
        identity = original(*args, **kwargs)
        calls += 1
        if calls == boundary:
            raise RuntimeError(f"fault after artifact {boundary}")
        return identity

    monkeypatch.setattr(INSTALLER, "_install_one", fail_after_link)
    with pytest.raises(RuntimeError, match=f"artifact {boundary}"):
        INSTALLER.main(_install_args())
    assert not controller.exists() and staging.is_dir()
    assert len(list(staging.iterdir())) == boundary
    monkeypatch.setattr(INSTALLER, "_install_one", original)
    assert INSTALLER.main(_install_args()) == 0
    assert controller.is_dir() and not staging.exists()
    assert source.is_dir() and not destination.exists()
    # Terminal replay validates and durably re-fsyncs the exact same controller.
    inode = controller.stat().st_ino
    assert INSTALLER.main(_install_args()) == 0
    assert controller.stat().st_ino == inode


@pytest.mark.skipif(os.geteuid() != 0, reason="root post-link crash resume")
def test_main_resumes_linked_artifact_before_directory_fsync(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    parent, _source, _destination, controller, _sources = _install_main_fixture(
        monkeypatch, tmp_path
    )
    original = INSTALLER._link_tmpfile
    fired = False

    def link_then_fail(descriptor: int, directory: int, name: str) -> None:
        nonlocal fired
        original(descriptor, directory, name)
        if not fired:
            fired = True
            raise RuntimeError("fault after linkat before directory fsync")

    monkeypatch.setattr(INSTALLER, "_link_tmpfile", link_then_fail)
    with pytest.raises(RuntimeError, match="after linkat"):
        INSTALLER.main(_install_args())
    staging = parent / "controller.installing-v1"
    assert not controller.exists() and len(list(staging.iterdir())) == 1
    monkeypatch.setattr(INSTALLER, "_link_tmpfile", original)
    assert INSTALLER.main(_install_args()) == 0
    assert controller.is_dir() and not staging.exists()


@pytest.mark.skipif(os.geteuid() != 0, reason="root atomic promotion crash resume")
def test_main_resumes_before_and_after_atomic_promotion(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    parent, _source, _destination, controller, _sources = _install_main_fixture(
        monkeypatch, tmp_path
    )
    staging = parent / "controller.installing-v1"
    original_rename = INSTALLER._rename_staging_noreplace
    monkeypatch.setattr(
        INSTALLER, "_rename_staging_noreplace",
        lambda _parent: (_ for _ in ()).throw(RuntimeError("fault before renameat2")),
    )
    with pytest.raises(RuntimeError, match="before renameat2"):
        INSTALLER.main(_install_args())
    assert staging.is_dir() and len(list(staging.iterdir())) == 5
    monkeypatch.setattr(INSTALLER, "_rename_staging_noreplace", original_rename)
    original_fsync = INSTALLER._fsync_rename_parent
    fired = False

    def fail_before_parent_fsync(descriptor: int) -> None:
        nonlocal fired
        if not fired:
            fired = True
            raise RuntimeError("fault after renameat2 before parent fsync")
        original_fsync(descriptor)

    monkeypatch.setattr(INSTALLER, "_fsync_rename_parent", fail_before_parent_fsync)
    with pytest.raises(RuntimeError, match="after renameat2"):
        INSTALLER.main(_install_args())
    assert controller.is_dir() and not staging.exists()
    inode = controller.stat().st_ino
    monkeypatch.setattr(INSTALLER, "_fsync_rename_parent", original_fsync)
    assert INSTALLER.main(_install_args()) == 0
    assert controller.stat().st_ino == inode


@pytest.mark.skipif(os.geteuid() != 0, reason="root mkdir/open race regression")
def test_staging_mkdir_open_swap_is_rejected_before_artifact_install(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    parent, _source, _destination, controller, _sources = _install_main_fixture(
        monkeypatch, tmp_path
    )
    staging = parent / "controller.installing-v1"
    original_open = INSTALLER._open_directory_at
    swapped = False

    def swap_before_open(parent_fd: int, path: Path) -> tuple[int, os.stat_result]:
        nonlocal swapped
        if path == staging and not swapped:
            swapped = True
            moved = parent / "created-but-moved"
            replacement = parent / "replacement"
            replacement.mkdir()
            os.rename(staging, moved)
            os.rename(replacement, staging)
        return original_open(parent_fd, path)

    monkeypatch.setattr(INSTALLER, "_open_directory_at", swap_before_open)
    with pytest.raises(INSTALLER.InstallError, match="changed between mkdirat and openat"):
        INSTALLER.main(_install_args())
    assert not controller.exists() and list(staging.iterdir()) == []


@pytest.mark.skipif(os.geteuid() != 0, reason="root final child-swap regression")
def test_final_child_swap_during_postinstall_gate_is_rejected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    _parent, _source, _destination, controller, sources = _install_main_fixture(
        monkeypatch, tmp_path
    )
    gate_calls = 0

    def gate_with_swap(_module: object) -> dict[str, object]:
        nonlocal gate_calls
        gate_calls += 1
        if gate_calls == 1:
            target = controller / sources[0][1].name
            target.unlink()
            target.write_bytes(b"replacement")
            target.chmod(0o555)
            os.chown(target, 0, 0)
        return {"snapshot": "same", "zero": True}

    monkeypatch.setattr(INSTALLER, "_source_gate", gate_with_swap)
    with pytest.raises(INSTALLER.InstallError, match="installed artifact differs"):
        INSTALLER.main(_install_args())


@pytest.mark.skipif(os.geteuid() != 0, reason="root strict prefix grammar")
@pytest.mark.parametrize("mutation", ["hole", "foreign", "wrong-bytes", "hardlink"])
def test_staging_prefix_mutations_are_rejected_before_resume_write(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, mutation: str,
) -> None:
    parent, _source, _destination, controller, sources = _install_main_fixture(
        monkeypatch, tmp_path
    )
    staging = parent / "controller.installing-v1"
    staging.mkdir(mode=0o755)
    os.chown(staging, 0, 0)
    first_payload = sources[0][0].read_bytes()
    if mutation == "hole":
        target = staging / sources[1][1].name
        target.write_bytes(sources[1][0].read_bytes())
        target.chmod(0o555)
        os.chown(target, 0, 0)
    elif mutation == "foreign":
        target = staging / "foreign"
        target.write_bytes(b"foreign")
        target.chmod(0o444)
        os.chown(target, 0, 0)
    elif mutation == "wrong-bytes":
        target = staging / sources[0][1].name
        target.write_bytes(b"wrong")
        target.chmod(0o555)
        os.chown(target, 0, 0)
    else:
        target = staging / sources[0][1].name
        backing = tmp_path / "backing"
        backing.write_bytes(first_payload)
        backing.chmod(0o555)
        os.chown(backing, 0, 0)
        os.link(backing, target)
    with pytest.raises(
        INSTALLER.InstallError,
        match="controller inventory|artifact differs|bounded unaliased regular file",
    ):
        INSTALLER.main(_install_args())
    assert not controller.exists()


@pytest.mark.skipif(os.geteuid() != 0, reason="root final path-swap regression")
def test_controller_disappearance_during_postinstall_gate_is_rejected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    parent, _source, _destination, controller, _sources = _install_main_fixture(
        monkeypatch, tmp_path
    )
    gate_calls = 0

    def gate_with_disappearance(_module: object) -> dict[str, object]:
        nonlocal gate_calls
        gate_calls += 1
        if gate_calls == 1:
            os.rename(controller, parent / "moved-controller")
        return {"snapshot": "same", "zero": True}

    monkeypatch.setattr(INSTALLER, "_source_gate", gate_with_disappearance)
    with pytest.raises(FileNotFoundError):
        INSTALLER.main(_install_args())


@pytest.mark.skipif(os.geteuid() != 0, reason="root terminal validation before fsync")
def test_terminal_state_is_fully_validated_before_parent_fsync(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    parent, _source, _destination, controller, _sources = _install_main_fixture(
        monkeypatch, tmp_path
    )
    controller.mkdir(mode=0o755)
    os.chown(controller, 0, 0)
    (controller / "foreign").write_bytes(b"foreign")
    monkeypatch.setattr(
        INSTALLER, "_fsync_rename_parent",
        lambda _descriptor: pytest.fail("parent fsync before terminal validation"),
    )
    with pytest.raises(INSTALLER.InstallError, match="exact prefix"):
        INSTALLER.main(_install_args())


@pytest.mark.skipif(os.geteuid() != 0, reason="root flock concurrency regression")
def test_controller_lock_blocks_runner_during_promotion_post_gate_and_terminal_replay(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    _parent, source, destination, controller, _sources = _install_main_fixture(
        monkeypatch, tmp_path
    )
    gate = {"snapshot": "same", "zero": True}
    blocked = 0

    def assert_runner_cannot_lock() -> None:
        nonlocal blocked
        descriptor = os.open(
            controller / INSTALLER.LOCK_NAME,
            os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW,
        )
        try:
            with pytest.raises(BlockingIOError):
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            blocked += 1
        finally:
            os.close(descriptor)

    def pre_gate(_module: object) -> dict[str, object]:
        if controller.exists():
            assert_runner_cannot_lock()
        return gate

    def post_gate(_module: object) -> dict[str, object]:
        assert controller.exists()
        assert_runner_cannot_lock()
        return gate

    monkeypatch.setattr(INSTALLER, "_preinstall_gate", pre_gate)
    monkeypatch.setattr(INSTALLER, "_source_gate", post_gate)
    assert INSTALLER.main(_install_args()) == 0
    assert blocked == 1
    assert source.is_dir() and not destination.exists()
    assert INSTALLER.main(_install_args()) == 0
    assert blocked == 4
    assert source.is_dir() and not destination.exists()


@pytest.mark.skipif(os.geteuid() != 0, reason="root authority fixed-point regression")
def test_authority_drift_after_terminal_install_rejects_and_terminal_resume_recovers(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    _parent, source, destination, controller, _sources = _install_main_fixture(
        monkeypatch, tmp_path
    )
    calls = 0

    def drifting_authority(_args: argparse.Namespace) -> dict[str, object]:
        nonlocal calls
        calls += 1
        return {"authority_generation": calls}

    monkeypatch.setattr(INSTALLER, "_installer_authority", drifting_authority)
    with pytest.raises(INSTALLER.InstallError, match="authority changed"):
        INSTALLER.main(_install_args())
    assert controller.is_dir()
    assert source.is_dir() and not destination.exists()
    monkeypatch.setattr(
        INSTALLER, "_installer_authority", lambda _args: {"authority": "stable"}
    )
    assert INSTALLER.main(_install_args()) == 0
