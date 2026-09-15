from __future__ import annotations

import hashlib
import io
import importlib.util
import json
from pathlib import Path
import shutil
import sys
import tarfile
import zipfile

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "work/blind-phase-confirmatory-v2/build_candidate_release.py"


def _module():
    name = "blind_phase_candidate_release_test_module"
    spec = importlib.util.spec_from_file_location(name, SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


RELEASE = _module()


def _config() -> dict[str, object]:
    return RELEASE.build_candidate_config(
        {
            "schema_version": "blind-phase-fsk-candidate-overrides-v1",
            "receiver_overrides": {
                "candidate_window_limit": 4,
                "phase_bins": 4,
                "short_search_timing_hypotheses": 4,
                "deep_search_timing_hypotheses": 4,
                "descramble_modes": [False, True],
            },
        },
        generator_identity={"path": str(SCRIPT), "sha256": "1" * 64},
    )


def _write_json(path: Path, value: object) -> str:
    payload = RELEASE.V1.canonical_pretty(value)
    path.write_bytes(payload)
    return hashlib.sha256(payload).hexdigest()


def _fake_wheel(
    path: Path,
    *,
    drift_source_name: str | None = None,
    console_entrypoint: str = "telemetry_yield.cli:main",
) -> None:
    closure = tuple(sorted((ROOT / "src/telemetry_yield").rglob("*.py")))
    with zipfile.ZipFile(path, "w") as archive:
        for source in closure:
            payload = source.read_bytes()
            if drift_source_name is not None and source.name == drift_source_name:
                payload = b"# drift\n"
            archive.writestr(
                str(source.relative_to(ROOT / "src")), payload
            )
        for schema in RELEASE.PACKAGED_SCHEMAS:
            archive.writestr(
                f"telemetry_yield-1.data/data/share/telemetry-yield/schemas/{schema.name}",
                schema.read_bytes(),
            )
        archive.writestr(
            "telemetry_yield-1.dist-info/entry_points.txt",
            f"[console_scripts]\ntelemetry-yield = {console_entrypoint}\n",
        )


def _fake_sdist(path: Path) -> None:
    closure = tuple(sorted((ROOT / "src/telemetry_yield").rglob("*.py")))
    sources = [
        *closure,
        *RELEASE.PACKAGED_SCHEMAS,
        ROOT / "pyproject.toml",
        ROOT / "README.md",
        *sorted((ROOT / "tests").glob("*.py")),
    ]
    with tarfile.open(path, "w:gz") as archive:
        for source in sources:
            payload = source.read_bytes()
            info = tarfile.TarInfo(f"telemetry-yield-1/{source.relative_to(ROOT)}")
            info.size = len(payload)
            archive.addfile(info, io.BytesIO(payload))


def _fake_environment(artifacts: Path, wheel_sha256: str) -> dict[str, object]:
    launcher = artifacts / "runtime-env/bin/python"
    module = artifacts / "runtime-env/lib/telemetry_yield/blind_phase_fsk_file.py"
    target = artifacts / "runtime-env/bin/python-target"
    metadata = (
        artifacts
        / "runtime-env/lib/telemetry_yield-0.1.0.dist-info"
    )
    launcher.parent.mkdir(parents=True)
    module.parent.mkdir(parents=True)
    metadata.mkdir(parents=True)
    target.write_bytes(b"python")
    launcher.symlink_to(target.name)
    module.write_bytes((ROOT / "src/telemetry_yield/blind_phase_fsk_file.py").read_bytes())
    (metadata / "RECORD").write_text("bound-record\n", encoding="utf-8")
    direct_url = {
        "archive_info": {"hashes": {"sha256": wheel_sha256}},
        "url": "file:///persistent/candidate.whl",
    }
    (metadata / "direct_url.json").write_text(
        json.dumps(direct_url, sort_keys=True),
        encoding="utf-8",
    )
    closure = RELEASE._runtime_closure(
        launcher,
        persistent_artifact_root=artifacts,
    )
    implementation = {
        "demodulator_id": "blind-clipping-robust-phase-fsk",
        "source_files": {},
    }
    runtime_root = artifacts / "runtime-env"
    query = {
        "python_version": "3.12.0",
        "blind_file_api_path": str(module.absolute()),
        "blind_file_api_sha256": hashlib.sha256(module.read_bytes()).hexdigest(),
        "telemetry_yield_direct_url": direct_url,
        "telemetry_yield_distribution_root": str(metadata.absolute()),
        "telemetry_yield_metadata_paths": {
            "RECORD": str((metadata / "RECORD").absolute()),
            "direct_url.json": str((metadata / "direct_url.json").absolute()),
        },
        "sys_path": [str((runtime_root / "lib").absolute())],
        "sysconfig_paths": {
            "data": str(runtime_root.absolute()),
            "platlib": str((runtime_root / "lib").absolute()),
            "platstdlib": str((runtime_root / "lib").absolute()),
            "purelib": str((runtime_root / "lib").absolute()),
            "scripts": str((runtime_root / "bin").absolute()),
            "stdlib": str((runtime_root / "lib").absolute()),
        },
        "implementation_manifest": implementation,
    }
    return {
        "launcher_path": str(launcher.absolute()),
        "launcher_is_symlink": True,
        "interpreter_target": closure["launcher"]["target"],
        "runtime_closure": closure,
        "python_import_closure": RELEASE._python_import_closure(
            RELEASE._runtime_import_paths(query),
            runtime_root=runtime_root,
        ),
        "query": query,
    }


def _qualification(
    candidate_sha256: str, environment: dict[str, object]
) -> dict[str, object]:
    target = environment["interpreter_target"]
    value = {
        "schema_version": "blind-phase-fsk-4gib-qualification-v1",
        "status": "PASS",
        "scope": "synthetic_deployment_resource_qualification_not_yield_evidence",
        "candidate_config": {"sha256": candidate_sha256},
        "input": {"logical_size_bytes": 4 * 1024**3},
        "checks": {
            name: True for name in sorted(RELEASE.QUALIFICATION_CHECK_NAMES)
        },
        "execution": {
            "runtime_python": {
                "launcher_path": environment["launcher_path"],
                "launcher_is_symlink": environment["launcher_is_symlink"],
                "target_path": target["path"],
                "target_size_bytes": target["size_bytes"],
                "target_sha256": target["sha256"],
                "python_version": environment["query"]["python_version"],
            },
            "rates": [
                {"baudrate": baudrate}
                for baudrate in (1_200, 4_800, 9_600, 19_200)
            ]
        },
        "source_closure": {
            "receiver_implementation": environment["query"]["implementation_manifest"]
        },
        "claim_guard": {
            "synthetic_resource_qualification_only": True,
            "receiver_yield_established": False,
            "publication_superiority_established": False,
        },
    }
    value["report_payload_sha256"] = RELEASE._sha256_document(value)
    return value


def _live_lock(
    artifacts: Path,
    *,
    environment: dict[str, object] | None = None,
) -> tuple[dict[str, object], dict[str, object]]:
    artifacts.mkdir(parents=True, exist_ok=True)
    wheel = artifacts / "candidate.whl"
    sdist = artifacts / "candidate.tar.gz"
    if not wheel.exists():
        wheel.write_bytes(b"wheel")
    if not sdist.exists():
        sdist.write_bytes(b"sdist")
    wheel_sha256 = hashlib.sha256(wheel.read_bytes()).hexdigest()
    if environment is None:
        environment = _fake_environment(artifacts, wheel_sha256)
    runtime_manifest = {
        "persistent_artifact_root": str(artifacts.resolve()),
        "environment": environment,
        "wheel": RELEASE._regular_identity(wheel),
        "sdist": RELEASE._regular_identity(sdist),
    }
    candidate_config = artifacts / "candidate.json"
    qualification = artifacts / "qualification.json"
    candidate_config.write_bytes(b"{}")
    qualification.write_bytes(b"{}")
    source_paths = {
        *sorted((ROOT / "src/telemetry_yield").rglob("*.py")),
        Path(RELEASE.__file__).absolute(),
        RELEASE.QUALIFIER.absolute(),
        RELEASE.RESULT_SCHEMA.absolute(),
        RELEASE.COMPONENT_SCHEMA.absolute(),
    }
    source_manifest = [
        RELEASE._regular_identity(path) for path in sorted(source_paths)
    ]
    lock: dict[str, object] = {
        "schema_version": RELEASE.RUNTIME_LOCK_SCHEMA_VERSION,
        "status": "PASS",
        "candidate_config": RELEASE._regular_identity(candidate_config),
        "candidate_config_sha256": hashlib.sha256(candidate_config.read_bytes()).hexdigest(),
        "qualification_report": RELEASE._regular_identity(qualification),
        "qualification_report_sha256": hashlib.sha256(qualification.read_bytes()).hexdigest(),
        "source_manifest": source_manifest,
        "source_manifest_sha256": RELEASE._sha256_document(source_manifest),
        "runtime_manifest": runtime_manifest,
        "runtime_manifest_sha256": RELEASE._sha256_document(runtime_manifest),
        "wheel_sha256": wheel_sha256,
    }
    lock["runtime_lock_payload_sha256"] = RELEASE._sha256_document(lock)
    return lock, environment


def _refresh_environment_closures(
    artifacts: Path, environment: dict[str, object]
) -> None:
    launcher = Path(str(environment["launcher_path"]))
    closure = RELEASE._runtime_closure(
        launcher,
        persistent_artifact_root=artifacts,
    )
    environment["interpreter_target"] = closure["launcher"]["target"]
    environment["runtime_closure"] = closure
    environment["python_import_closure"] = RELEASE._python_import_closure(
        RELEASE._runtime_import_paths(environment["query"]),
        runtime_root=launcher.parent.parent,
    )


def test_candidate_config_is_complete_uniform_and_plain_plus_g3ruh() -> None:
    config = _config()
    receivers = RELEASE.validate_candidate_document(config)
    assert tuple(receiver.baudrate for receiver in receivers) == (
        1_200,
        4_800,
        9_600,
        19_200,
    )
    expected_fields = {
        field.name for field in RELEASE.fields(RELEASE.BlindPhaseFskConfig)
    }
    for entry in config["rate_configs"]:
        assert set(entry["receiver_config"]) == expected_fields
        assert entry["receiver_config"]["descramble_modes"] == [False, True]
        assert type(entry["receiver_config"]["baudrate"]) is float


def test_candidate_config_rejects_rate_specific_drift_and_unknown_override() -> None:
    config = _config()
    config["rate_configs"][1]["receiver_config"]["candidate_window_limit"] = 5
    with pytest.raises(ValueError, match="differ beyond baudrate"):
        RELEASE.validate_candidate_document(config)
    with pytest.raises(ValueError, match="unknown receiver override"):
        RELEASE.build_candidate_config(
            {
                "schema_version": "blind-phase-fsk-candidate-overrides-v1",
                "receiver_overrides": {"not_a_field": 1},
            },
            generator_identity={"sha256": "1" * 64},
        )


def test_runtime_lock_binds_persistent_package_config_qualification_and_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    artifacts = tmp_path / "dist"
    artifacts.mkdir()
    config_path = tmp_path / "candidate.json"
    config = _config()
    config["generator"] = RELEASE._regular_identity(SCRIPT)
    config_sha256 = _write_json(config_path, config)
    wheel = artifacts / "telemetry_yield-1-py3-none-any.whl"
    sdist = artifacts / "telemetry_yield-1.tar.gz"
    _fake_wheel(wheel)
    _fake_sdist(sdist)
    environment = _fake_environment(
        artifacts, hashlib.sha256(wheel.read_bytes()).hexdigest()
    )
    monkeypatch.setattr(
        RELEASE,
        "_environment_manifest",
        lambda runtime_python, **kwargs: environment,
    )
    qualification_path = tmp_path / "qualification.json"
    qualification_sha256 = _write_json(
        qualification_path, _qualification(config_sha256, environment)
    )
    lock = RELEASE.build_runtime_lock(
        candidate_config=config_path,
        wheel=wheel,
        sdist=sdist,
        qualification_report=qualification_path,
        runtime_python=Path(environment["launcher_path"]),
        persistent_artifact_root=artifacts,
    )
    assert lock["status"] == "PASS"
    assert lock["candidate_config_sha256"] == config_sha256
    assert lock["qualification_report_sha256"] == qualification_sha256
    assert lock["wheel_sha256"] == hashlib.sha256(wheel.read_bytes()).hexdigest()
    assert lock["runtime_manifest"]["wheel_members"]
    assert lock["runtime_manifest"]["sdist_members"]
    assert lock["claim_guard"]["selected_holdout_iq_processed"] is False


def test_runtime_lock_rejects_wrong_qualification_and_ephemeral_package(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path = tmp_path / "candidate.json"
    config = _config()
    config["generator"] = RELEASE._regular_identity(SCRIPT)
    config_sha256 = _write_json(config_path, config)
    wheel = tmp_path / "candidate.whl"
    sdist = tmp_path / "candidate.tar.gz"
    _fake_wheel(wheel)
    _fake_sdist(sdist)
    environment = _fake_environment(
        tmp_path, hashlib.sha256(wheel.read_bytes()).hexdigest()
    )
    monkeypatch.setattr(
        RELEASE,
        "_environment_manifest",
        lambda runtime_python, **kwargs: environment,
    )
    qualification_path = tmp_path / "qualification.json"
    _write_json(qualification_path, _qualification("0" * 64, environment))
    with pytest.raises(ValueError, match="does not bind"):
        RELEASE.build_runtime_lock(
            candidate_config=config_path,
            wheel=wheel,
            sdist=sdist,
            qualification_report=qualification_path,
            runtime_python=Path(environment["launcher_path"]),
            persistent_artifact_root=tmp_path,
        )
    valid_qualification = tmp_path / "valid-qualification.json"
    _write_json(valid_qualification, _qualification(config_sha256, environment))
    outside = tmp_path.parent / f"{tmp_path.name}-outside.whl"
    _fake_wheel(outside)
    try:
        with pytest.raises(ValueError, match="persistent artifact root"):
            RELEASE.build_runtime_lock(
                candidate_config=config_path,
                wheel=outside,
                sdist=sdist,
                qualification_report=valid_qualification,
                runtime_python=Path(environment["launcher_path"]),
                persistent_artifact_root=tmp_path,
            )
    finally:
        outside.unlink(missing_ok=True)


@pytest.mark.parametrize(
    "source_name", ["blind_phase_fsk_file.py", "__init__.py", "cli.py"]
)
def test_runtime_lock_rejects_any_wheel_package_byte_mismatch(
    tmp_path: Path, source_name: str
) -> None:
    wheel = tmp_path / "candidate.whl"
    _fake_wheel(wheel, drift_source_name=source_name)
    package_sources = tuple(sorted((ROOT / "src/telemetry_yield").rglob("*.py")))
    with pytest.raises(ValueError, match="differs from repository"):
        RELEASE._wheel_contents(wheel, package_sources)


def test_runtime_lock_rejects_wheel_console_entrypoint_drift(tmp_path: Path) -> None:
    wheel = tmp_path / "candidate.whl"
    _fake_wheel(wheel, console_entrypoint="telemetry_yield.evil:main")
    package_sources = tuple(sorted((ROOT / "src/telemetry_yield").rglob("*.py")))
    with pytest.raises(ValueError, match="console entry point"):
        RELEASE._wheel_contents(wheel, package_sources)


def test_runtime_tree_merkle_detects_same_version_file_tamper(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime-env"
    package_file = runtime / "lib/python3.12/site-packages/numpy/core.py"
    package_file.parent.mkdir(parents=True)
    package_file.write_bytes(b"version = '2.0.0'\nvalue = 1\n")
    before, _ = RELEASE._runtime_tree_inventory(runtime)
    package_file.write_bytes(b"version = '2.0.0'\nvalue = 2\n")
    after, _ = RELEASE._runtime_tree_inventory(runtime)
    assert before["entry_count"] == after["entry_count"]
    assert before["total_regular_bytes"] == after["total_regular_bytes"]
    assert before["merkle_sha256"] != after["merkle_sha256"]


def test_runtime_tree_binds_symlink_text_and_final_target(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime-env"
    library = runtime / "lib"
    library.mkdir(parents=True)
    first = library / "first.py"
    second = library / "other.py"
    first.write_bytes(b"value = 1\n")
    second.write_bytes(b"value = 1\n")
    active = library / "active.py"
    active.symlink_to(first.name)
    before, _ = RELEASE._runtime_tree_inventory(runtime)
    active.unlink()
    active.symlink_to(second.name)
    after, _ = RELEASE._runtime_tree_inventory(runtime)
    before_link = next(
        row for row in before["entries"] if row["path"] == "lib/active.py"
    )
    assert before_link["type"] == "symlink-file"
    assert before_link["link_target"] == "first.py"
    assert before["merkle_sha256"] != after["merkle_sha256"]


def test_runtime_closure_detects_extra_native_file_and_ldd_dependencies(
    tmp_path: Path,
) -> None:
    runtime = tmp_path / "artifacts/runtime-env"
    launcher = runtime / "bin/python"
    launcher.parent.mkdir(parents=True)
    launcher.symlink_to(Path(sys.executable).resolve())
    before = RELEASE._runtime_closure(
        launcher,
        persistent_artifact_root=tmp_path / "artifacts",
    )
    native = runtime / "lib/injected-extension.so"
    native.parent.mkdir(parents=True)
    shutil.copyfile(Path(sys.executable).resolve(), native)
    after = RELEASE._runtime_closure(
        launcher,
        persistent_artifact_root=tmp_path / "artifacts",
    )
    assert (
        after["tree"]["regular_file_count"]
        == before["tree"]["regular_file_count"] + 1
    )
    assert after["tree"]["directory_count"] == before["tree"]["directory_count"] + 1
    assert after["native"]["elf_root_count"] == before["native"]["elf_root_count"] + 1
    assert after["native"]["dependency_count"] > 0
    assert after["merkle_sha256"] != before["merkle_sha256"]


def test_environment_validation_recomputes_and_rejects_runtime_tamper(
    tmp_path: Path,
) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    wheel_sha256 = "a" * 64
    environment = _fake_environment(artifacts, wheel_sha256)
    RELEASE._validate_environment_manifest(
        environment,
        expected_wheel_sha256=wheel_sha256,
        persistent_artifact_root=artifacts,
    )
    injected = artifacts / "runtime-env/lib/same-version-injection.py"
    injected.write_bytes(b"payload = True\n")
    with pytest.raises(ValueError, match="content drifted"):
        RELEASE._validate_environment_manifest(
            environment,
            expected_wheel_sha256=wheel_sha256,
            persistent_artifact_root=artifacts,
        )


def test_environment_validation_rejects_external_stdlib_tamper(
    tmp_path: Path,
) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    wheel_sha256 = "b" * 64
    environment = _fake_environment(artifacts, wheel_sha256)
    stdlib = artifacts / "shared-python-stdlib"
    stdlib.mkdir()
    module = stdlib / "hashlib.py"
    module.write_bytes(b"trusted = 1\n")
    environment["query"]["sys_path"].append(str(stdlib.absolute()))
    environment["python_import_closure"] = RELEASE._python_import_closure(
        RELEASE._runtime_import_paths(environment["query"]),
        runtime_root=artifacts / "runtime-env",
    )
    RELEASE._validate_environment_manifest(
        environment,
        expected_wheel_sha256=wheel_sha256,
        persistent_artifact_root=artifacts,
    )
    module.write_bytes(b"trusted = 0\n")
    with pytest.raises(ValueError, match="Python import content drifted"):
        RELEASE._validate_environment_manifest(
            environment,
            expected_wheel_sha256=wheel_sha256,
            persistent_artifact_root=artifacts,
        )


def test_live_runtime_lock_verifier_happy_path(tmp_path: Path) -> None:
    artifacts = tmp_path / "artifacts"
    lock, _ = _live_lock(artifacts)
    verified = RELEASE.validate_runtime_lock_live(lock)
    assert verified["wheel_sha256"] == lock["wheel_sha256"]
    assert verified["runtime_closure_sha256"] == lock["runtime_manifest"][
        "environment"
    ]["runtime_closure"]["merkle_sha256"]


def test_live_runtime_lock_rejects_repository_source_manifest_drift(
    tmp_path: Path,
) -> None:
    lock, _ = _live_lock(tmp_path / "artifacts")
    lock["source_manifest"][0]["sha256"] = "0" * 64
    lock["source_manifest_sha256"] = RELEASE._sha256_document(lock["source_manifest"])
    lock["runtime_lock_payload_sha256"] = RELEASE._sha256_document(
        {
            key: value
            for key, value in lock.items()
            if key != "runtime_lock_payload_sha256"
        }
    )
    with pytest.raises(ValueError, match="source content drifted"):
        RELEASE.validate_runtime_lock_live(lock)


def test_live_runtime_lock_rejects_same_version_scipy_drift(tmp_path: Path) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    wheel = artifacts / "candidate.whl"
    wheel.write_bytes(b"wheel")
    environment = _fake_environment(
        artifacts, hashlib.sha256(wheel.read_bytes()).hexdigest()
    )
    scipy = artifacts / "runtime-env/lib/scipy/_core.py"
    scipy.parent.mkdir(parents=True)
    scipy.write_bytes(b"version='1.0'; value=1\n")
    _refresh_environment_closures(artifacts, environment)
    lock, _ = _live_lock(artifacts, environment=environment)
    scipy.write_bytes(b"version='1.0'; value=2\n")
    with pytest.raises(ValueError, match="runtime content drifted"):
        RELEASE.validate_runtime_lock_live(lock)


def test_live_runtime_lock_rejects_external_stdlib_drift(tmp_path: Path) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    wheel = artifacts / "candidate.whl"
    wheel.write_bytes(b"wheel")
    environment = _fake_environment(
        artifacts, hashlib.sha256(wheel.read_bytes()).hexdigest()
    )
    stdlib = tmp_path / "shared-stdlib"
    stdlib.mkdir()
    module = stdlib / "hashlib.py"
    module.write_bytes(b"trusted = 1\n")
    environment["query"]["sys_path"].append(str(stdlib.resolve()))
    _refresh_environment_closures(artifacts, environment)
    lock, _ = _live_lock(artifacts, environment=environment)
    module.write_bytes(b"trusted = 0\n")
    with pytest.raises(ValueError, match="Python import content drifted"):
        RELEASE.validate_runtime_lock_live(lock)


def test_live_runtime_lock_rejects_runtime_symlink_retarget(tmp_path: Path) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    wheel = artifacts / "candidate.whl"
    wheel.write_bytes(b"wheel")
    environment = _fake_environment(
        artifacts, hashlib.sha256(wheel.read_bytes()).hexdigest()
    )
    runtime = artifacts / "runtime-env"
    second_target = runtime / "bin/python-target-two"
    second_target.write_bytes(b"python")
    _refresh_environment_closures(artifacts, environment)
    lock, _ = _live_lock(artifacts, environment=environment)
    launcher = runtime / "bin/python"
    launcher.unlink()
    launcher.symlink_to(second_target.name)
    with pytest.raises(ValueError, match="runtime content drifted"):
        RELEASE.validate_runtime_lock_live(lock)


def test_live_runtime_lock_rejects_added_elf(tmp_path: Path) -> None:
    artifacts = tmp_path / "artifacts"
    lock, _ = _live_lock(artifacts)
    injected = artifacts / "runtime-env/lib/scipy/injected-extension.so"
    injected.parent.mkdir(parents=True)
    shutil.copyfile(Path(sys.executable).resolve(), injected)
    with pytest.raises(ValueError, match="runtime content drifted"):
        RELEASE.validate_runtime_lock_live(lock)


def test_runtime_tree_rejects_external_directory_symlink(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime-env"
    runtime.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (runtime / "escape").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="directory symlink escapes"):
        RELEASE._runtime_tree_inventory(runtime)


def test_runtime_tree_enforces_entry_and_byte_bounds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = tmp_path / "runtime-env"
    runtime.mkdir()
    (runtime / "one").write_bytes(b"12")
    monkeypatch.setattr(RELEASE, "MAXIMUM_RUNTIME_REGULAR_BYTES", 1)
    with pytest.raises(ValueError, match="byte bound"):
        RELEASE._runtime_tree_inventory(runtime)
    monkeypatch.setattr(RELEASE, "MAXIMUM_RUNTIME_REGULAR_BYTES", 100)
    monkeypatch.setattr(RELEASE, "MAXIMUM_RUNTIME_ENTRIES", 0)
    with pytest.raises(ValueError, match="entry-count bound"):
        RELEASE._runtime_tree_inventory(runtime)
    directory_only = tmp_path / "directory-only-runtime"
    (directory_only / "empty").mkdir(parents=True)
    with pytest.raises(ValueError, match="entry-count bound"):
        RELEASE._runtime_tree_inventory(directory_only)


def test_native_dependency_closure_enforces_count_and_byte_bounds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executable = Path(sys.executable).resolve()
    monkeypatch.setattr(RELEASE, "MAXIMUM_RUNTIME_NATIVE_DEPENDENCIES", 0)
    with pytest.raises(ValueError, match="native-dependency count bound"):
        RELEASE._native_dependency_manifest((executable,))
    monkeypatch.setattr(RELEASE, "MAXIMUM_RUNTIME_NATIVE_DEPENDENCIES", 4_096)
    monkeypatch.setattr(RELEASE, "MAXIMUM_RUNTIME_NATIVE_BYTES", 0)
    with pytest.raises(ValueError, match="native-dependency byte bound"):
        RELEASE._native_dependency_manifest((executable,))
