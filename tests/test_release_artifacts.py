from __future__ import annotations

import hashlib
from pathlib import Path
import zipfile

import pytest

from telemetry_yield.release_artifacts import (
    ArtifactSpec,
    build_artifact_manifest,
    scan_release_artifacts_for_secrets,
)


def test_manifest_is_sorted_hashed_and_sized(tmp_path: Path) -> None:
    (tmp_path / "b.txt").write_text("second", encoding="utf-8")
    (tmp_path / "a.txt").write_text("first", encoding="utf-8")
    result = build_artifact_manifest(
        tmp_path,
        [ArtifactSpec("b.txt", "report"), ArtifactSpec("a.txt", "code")],
        metadata={"claim": "test"},
    )
    assert [row["path"] for row in result["artifacts"]] == ["a.txt", "b.txt"]
    assert result["artifact_count"] == 2
    assert result["total_size_bytes"] == 11
    assert result["artifacts"][0]["sha256"] == hashlib.sha256(b"first").hexdigest()


def test_manifest_rejects_escape_symlink_and_duplicates(tmp_path: Path) -> None:
    (tmp_path / "value.txt").write_text("safe", encoding="utf-8")
    with pytest.raises(ValueError, match="root-relative"):
        ArtifactSpec("../value.txt", "code")
    (tmp_path / "link.txt").symlink_to(tmp_path / "value.txt")
    with pytest.raises(ValueError, match="non-symlink"):
        build_artifact_manifest(tmp_path, [ArtifactSpec("link.txt", "code")])
    with pytest.raises(ValueError, match="duplicate"):
        build_artifact_manifest(
            tmp_path,
            [ArtifactSpec("value.txt", "code"), ArtifactSpec("value.txt", "report")],
        )


def test_secret_scan_reports_only_location_and_pattern(tmp_path: Path) -> None:
    secret = "AKIA" + "A" * 16
    (tmp_path / "unsafe.txt").write_text(f"before\n{secret}\nafter\n", encoding="utf-8")
    result = scan_release_artifacts_for_secrets(
        tmp_path, [ArtifactSpec("unsafe.txt", "report")]
    )
    assert result["passed"] is False
    assert result["findings"] == [
        {"path": "unsafe.txt", "line": 2, "pattern": "aws_access_key_id"}
    ]
    assert secret not in repr(result)


def test_secret_scan_fails_closed_when_allowlisted_file_is_too_large(tmp_path: Path) -> None:
    (tmp_path / "large.txt").write_text("0123456789", encoding="utf-8")
    result = scan_release_artifacts_for_secrets(
        tmp_path,
        [ArtifactSpec("large.txt", "report")],
        maximum_file_bytes=5,
    )
    assert result["passed"] is False
    assert result["skipped_large_files"] == ["large.txt"]


def test_secret_scan_opens_zip_packages_and_reports_member_location(tmp_path: Path) -> None:
    secret = "ghp_" + "A" * 36
    archive = tmp_path / "package.whl"
    with zipfile.ZipFile(archive, "w") as handle:
        handle.writestr("safe/module.py", "VALUE = 1\n")
        handle.writestr("unsafe/config.txt", f"token={secret}\n")
    result = scan_release_artifacts_for_secrets(
        tmp_path, [ArtifactSpec("package.whl", "python_wheel")]
    )
    assert result["passed"] is False
    assert result["findings"] == [
        {
            "path": "package.whl!unsafe/config.txt",
            "line": 1,
            "pattern": "github_token",
        }
    ]
    assert secret not in repr(result)


def test_secret_scan_fails_closed_on_oversized_zip_member(tmp_path: Path) -> None:
    archive = tmp_path / "package.whl"
    with zipfile.ZipFile(archive, "w") as handle:
        handle.writestr("large.txt", "0123456789")
    result = scan_release_artifacts_for_secrets(
        tmp_path,
        [ArtifactSpec("package.whl", "python_wheel")],
        maximum_file_bytes=5,
    )
    assert result["passed"] is False
    assert result["skipped_large_files"] == ["package.whl!large.txt"]
