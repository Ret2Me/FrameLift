from __future__ import annotations

import ast
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path

import pytest


SOURCE = (
    Path(__file__).parents[1]
    / "work/blind-phase-confirmatory-v2/"
    "build_fourth_freeze_failure_evidence_postpublish_corrective_"
    "independent_review_v1.py"
)
SPEC = importlib.util.spec_from_file_location(
    "build_fourth_freeze_failure_evidence_postpublish_corrective_independent_review_v1",
    SOURCE,
)
assert SPEC is not None and SPEC.loader is not None
R = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(R)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _build() -> tuple[dict[str, object], dict[str, object]]:
    return R.build_review(_sha(R.GENERATOR), _sha(R.GENERATOR_TEST))


def _identity(path: Path, payload: bytes, **extra: int) -> dict[str, object]:
    value = {
        "path": str(path), "size_bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }
    value.update(extra)
    return value


def test_live_pair_and_prepublish_review_are_exact() -> None:
    live = R.validate_live_pair()
    assert live["evidence"]["sha256"] == R.EXPECTED_EVIDENCE["sha256"]
    assert live["evidence_sidecar"]["sha256"] == R.EXPECTED_SIDECAR["sha256"]
    assert live["prepublish_review"]["sha256"] == R.EXPECTED_PREPUBLISH_REVIEW["sha256"]
    assert live["semantic_payload_sha256"] == R.EXPECTED_EVIDENCE["payload_sha256"]


def test_clean_selfhashed_go_binds_actual_pair() -> None:
    document, audit = _build()
    unhashed = copy.deepcopy(document)
    stored = unhashed.pop(R.HASH_FIELD)
    assert stored == R._document_sha(unhashed)
    assert document["schema_version"] == R.SCHEMA
    assert document["status"] == "GO"
    assert document["findings"] == {"P0": 0, "P1": 0, "P2": 0}
    assert document["retry3_gate"]["required_evidence"]["sha256"] == R.EXPECTED_EVIDENCE["sha256"]
    assert audit == {
        "status": "DRY_RUN_PASS_NO_WRITE", "published": False,
        "output": str(R.OUTPUT), R.HASH_FIELD: document[R.HASH_FIELD],
    }


def test_literal_backslash_n_cause_is_exact_and_semantics_unchanged() -> None:
    document, _ = _build()
    finding = document["corrective_finding"]
    assert finding["classification"] == "STAGING_FILE_IDENTITY_PREDICTION_USED_LITERAL_BACKSLASH_N"
    assert finding["semantic_document_changed"] is False
    assert finding["publication_invariants_violated"] is False
    assert finding["prepublish_review_is_terminal_authority"] is False
    assert finding["proof"] == {
        "actual_has_one_final_lf": True,
        "operation": "actual_bytes_without_final_LF + bytes([92,110])",
        "result_sha256": R.SUPERSEDED_PREDICTION["sha256"],
        "result_size_bytes": R.SUPERSEDED_PREDICTION["size_bytes"],
    }


def test_read_descriptor_rejects_wrong_inode_or_hash(tmp_path: Path) -> None:
    path = tmp_path / "value"
    path.write_bytes(b"exact")
    descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    try:
        status = os.fstat(descriptor)
        expected = _identity(
            path, b"wrong", st_dev=status.st_dev, st_ino=status.st_ino,
            uid=status.st_uid, gid=status.st_gid,
            mode=stat_mode(status.st_mode), nlink=1,
        )
        with pytest.raises(R.ReviewError, match="identity differs"):
            R._read_descriptor(descriptor, path, expected, 4096)
    finally:
        os.close(descriptor)


def stat_mode(value: int) -> int:
    import stat
    return stat.S_IMODE(value)


def test_selfhash_and_prediction_drift_reject() -> None:
    document = json.loads(R.PREPUBLISH_REVIEW.read_bytes())
    document["read_only_dry_run"]["candidate"]["size_bytes"] += 1
    with pytest.raises(R.ReviewError, match="self-hash differs"):
        R._validate_selfhash(
            document, R.PREPUBLISH_REVIEW_HASH_FIELD,
            R.EXPECTED_PREPUBLISH_REVIEW["payload_sha256"], "mutated review",
        )


def test_reports_parent_swap_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    reports = tmp_path / "reports"
    reports.mkdir(mode=0o775)
    reports.chmod(0o775)
    status = reports.stat()
    monkeypatch.setattr(R, "REPORTS", reports)
    monkeypatch.setattr(R, "EXPECTED_REPORTS_PARENT", {
        "path": str(reports), "st_dev": status.st_dev, "st_ino": status.st_ino,
        "uid": status.st_uid, "gid": status.st_gid,
        "mode": stat_mode(status.st_mode), "nlink": status.st_nlink,
    })
    descriptor, opened = R._open_reports()
    try:
        reports.rename(tmp_path / "displaced")
        reports.mkdir(mode=0o775)
        with pytest.raises(R.ReviewError, match="changed during corrective review"):
            R._revalidate_reports(descriptor, opened)
    finally:
        os.close(descriptor)


def test_generator_has_no_write_publication_or_lifecycle_primitive() -> None:
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    attributes = {
        node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
    }
    assert not {
        "write", "write_text", "write_bytes", "unlink", "rename", "replace",
        "remove", "rmtree", "kill", "mount", "chmod", "chown", "fchmod",
        "fchown", "link", "symlink", "mkdir",
    } & attributes
    text = SOURCE.read_text(encoding="utf-8")
    assert "IQ" not in text
    assert "--publish" not in text


def test_repo_artifact_is_deterministic_when_present() -> None:
    if not R.OUTPUT.exists():
        pytest.skip("corrective review candidate is not materialized")
    document, _ = _build()
    validated = R.validate_repo_artifact(document)
    assert validated["review"]["path"] == str(R.OUTPUT)
    assert json.loads(R.OUTPUT.read_bytes()) == document
