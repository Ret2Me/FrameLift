from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import shutil
import stat
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "work/blind-phase-confirmatory-v2/build_component_closure_manifest.py"


def _module():
    name = "blind_phase_component_closure_manifest_test_module"
    spec = importlib.util.spec_from_file_location(name, SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


BUILDER = _module()


def _tree(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "runtime"
    library = root / "lib"
    library.mkdir(parents=True, mode=0o755)
    executable = library / "decoder"
    executable.write_bytes(b"decoder bytes")
    executable.chmod(0o755)
    os.link(executable, library / "decoder-alias")
    external = tmp_path / "external-hardlink"
    os.link(executable, external)
    (root / "lib64").symlink_to("lib", target_is_directory=True)
    return root, external


def test_manifest_is_deterministic_complete_and_self_hashed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(BUILDER, "AMENDMENT_V1_RFC3161_NS", 2**63 - 1)
    root, _ = _tree(tmp_path)
    generator = {"path": "/generator.py", "size_bytes": 1, "sha256": "a" * 64}
    first = BUILDER.build_manifest(root, generator_identity=generator)
    second = BUILDER.build_manifest(root, generator_identity=generator)
    assert first == second
    assert first["schema_version"] == BUILDER.SCHEMA_VERSION
    assert first["status"] == "complete"
    assert first["entry_count"] == len(first["entries"]) == 4
    assert first["regular_file_count"] == 2
    assert first["directory_count"] == 1
    assert first["symlink_count"] == 1
    topology = first["hardlink_topology"]
    assert topology["group_count"] == 1
    assert topology["internal_multi_path_group_count"] == 1
    assert topology["external_hardlink_path_count"] == 2
    group = topology["groups"][0]
    assert group["paths"] == ["lib/decoder", "lib/decoder-alias"]
    assert group["in_tree_link_count"] == 2
    assert group["external_link_count"] == 1
    assert first["manifest_payload_sha256"] == BUILDER.sha256_document(
        {key: value for key, value in first.items() if key != "manifest_payload_sha256"}
    )


def test_rfc3161_cutoff_is_derived_exactly_and_newer_tree_cannot_complete(
    tmp_path: Path,
) -> None:
    assert BUILDER.AMENDMENT_V1_RFC3161_NS == 1_788_535_697_000_000_000
    root, _ = _tree(tmp_path)
    generator = {"path": "/generator.py", "size_bytes": 1, "sha256": "a" * 64}
    with pytest.raises(ValueError, match="newer than amendment v1 RFC3161"):
        BUILDER.build_manifest(root, generator_identity=generator)


def test_semantic_hash_ignores_write_bits_and_hardlink_inode_topology(
    tmp_path: Path,
) -> None:
    root, external = _tree(tmp_path)
    before = BUILDER.scan_runtime(root)
    alias = root / "lib/decoder-alias"
    payload = alias.read_bytes()
    alias.unlink()
    alias.write_bytes(payload)
    alias.chmod(0o555)
    (root / "lib/decoder").chmod(0o555)
    (root / "lib").chmod(0o555)
    external.unlink()
    after = BUILDER.scan_runtime(root)
    assert before["raw_manifest_sha256"] != after["raw_manifest_sha256"]
    assert before["content_manifest_sha256"] == after["content_manifest_sha256"]


def test_raw_tree_materializes_and_seals_to_same_canonical_semantic_hash(
    tmp_path: Path,
) -> None:
    root, _ = _tree(tmp_path)
    before = BUILDER.scan_runtime(root)
    sealed = tmp_path / "sealed"
    shutil.copytree(root, sealed, symlinks=True, copy_function=shutil.copy2)
    for current, directories, files in os.walk(sealed, topdown=False, followlinks=False):
        for name in files:
            path = Path(current) / name
            if not path.is_symlink():
                path.chmod(stat.S_IMODE(path.stat().st_mode) & ~0o222)
        for name in directories:
            path = Path(current) / name
            if not path.is_symlink():
                path.chmod(stat.S_IMODE(path.stat().st_mode) & ~0o222)
    sealed.chmod(stat.S_IMODE(sealed.stat().st_mode) & ~0o222)
    after = BUILDER.scan_runtime(sealed)
    assert before["raw_manifest_sha256"] != after["raw_manifest_sha256"]
    assert before["content_manifest_sha256"] == after["content_manifest_sha256"]


@pytest.mark.parametrize("mutation", ["extra", "missing", "bytes", "exec", "link"])
def test_canonical_semantic_hash_rejects_scientific_closure_drift(
    tmp_path: Path, mutation: str
) -> None:
    root, _ = _tree(tmp_path)
    before = BUILDER.scan_runtime(root)["content_manifest_sha256"]
    if mutation == "extra":
        (root / "lib/extra").write_bytes(b"extra")
    elif mutation == "missing":
        (root / "lib/decoder-alias").unlink()
    elif mutation == "bytes":
        (root / "lib/decoder").write_bytes(b"changed")
    elif mutation == "exec":
        (root / "lib/decoder").chmod(0o644)
    else:
        (root / "lib64").unlink()
        (root / "lib64").symlink_to("lib/decoder")
    assert BUILDER.scan_runtime(root)["content_manifest_sha256"] != before


def test_internal_symlink_records_raw_and_resolved_semantics(tmp_path: Path) -> None:
    root, _ = _tree(tmp_path)
    manifest = BUILDER.scan_runtime(root)
    link = next(record for record in manifest["entries"] if record["kind"] == "symlink")
    assert link["path"] == "lib64"
    assert link["link_target"] == "lib"
    assert link["resolved_relative_path"] == "lib"
    assert link["resolved_kind"] == "directory"


@pytest.mark.parametrize("kind", ["escaping", "broken", "cycle", "special"])
def test_scan_rejects_unsafe_closure_entries(tmp_path: Path, kind: str) -> None:
    root = tmp_path / "runtime"
    root.mkdir()
    if kind == "escaping":
        outside = tmp_path / "outside"
        outside.write_bytes(b"outside")
        (root / "bad").symlink_to(outside)
        match = "escapes"
    elif kind == "broken":
        (root / "bad").symlink_to("absent")
        match = "broken or cyclic"
    elif kind == "cycle":
        (root / "a").symlink_to("b")
        (root / "b").symlink_to("a")
        match = "broken or cyclic"
    else:
        os.mkfifo(root / "fifo")
        match = "special"
    with pytest.raises(ValueError, match=match):
        BUILDER.scan_runtime(root)


def test_no_clobber_publication_has_exact_sidecar_and_mode(tmp_path: Path) -> None:
    output = tmp_path / "closure.json"
    document = {"schema_version": BUILDER.SCHEMA_VERSION, "status": "complete"}
    BUILDER.publish_no_clobber(output, document)
    payload = output.read_bytes()
    digest = __import__("hashlib").sha256(payload).hexdigest()
    assert output.with_name(output.name + ".sha256").read_text() == f"{digest}  {output.name}\n"
    assert stat.S_IMODE(output.stat().st_mode) == 0o444
    BUILDER.publish_no_clobber(output, document)
    sidecar = output.with_name(output.name + ".sha256")
    sidecar.unlink()
    BUILDER.publish_no_clobber(output, document)
    output.unlink()
    BUILDER.publish_no_clobber(output, document)
    with pytest.raises(ValueError, match="conflicting"):
        BUILDER.publish_no_clobber(output, {**document, "status": "changed"})


def test_cli_rejects_non_frozen_runtime_root(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="differs from the frozen"):
        BUILDER.main(["--runtime-root", str(tmp_path), "--output", str(tmp_path / "x")])
