"""Deterministic release artifact inventory and non-disclosing secret scan."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
from pathlib import Path
import re
from typing import Iterable, Mapping, Sequence
import zipfile


ARTIFACT_MANIFEST_SCHEMA = "telemetry-yield-artifact-manifest-v1"
SECRET_SCAN_SCHEMA = "telemetry-yield-secret-scan-v1"

SECRET_PATTERNS: tuple[tuple[str, re.Pattern[bytes]], ...] = (
    ("pem_private_key", re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    ("aws_access_key_id", re.compile(rb"(?<![A-Z0-9])(?:AKIA|ASIA)[A-Z0-9]{16}(?![A-Z0-9])")),
    ("github_token", re.compile(rb"(?<![A-Za-z0-9])gh[pousr]_[A-Za-z0-9]{36,255}")),
    ("slack_token", re.compile(rb"(?<![A-Za-z0-9])xox[baprs]-[A-Za-z0-9-]{20,255}")),
    ("google_api_key", re.compile(rb"(?<![A-Za-z0-9])AIza[A-Za-z0-9_-]{35}(?![A-Za-z0-9_-])")),
)


@dataclass(frozen=True, slots=True)
class ArtifactSpec:
    path: str
    role: str

    def __post_init__(self) -> None:
        candidate = Path(self.path)
        if not self.path or candidate.is_absolute() or ".." in candidate.parts:
            raise ValueError("artifact paths must be nonempty root-relative paths")
        if not self.role.strip():
            raise ValueError("artifact role must be nonempty")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _resolve_regular(root: Path, relative: str) -> Path:
    root = root.resolve()
    candidate = root / relative
    resolved = candidate.resolve(strict=True)
    if resolved != root and root not in resolved.parents:
        raise ValueError(f"artifact escapes root: {relative}")
    if candidate.is_symlink() or not resolved.is_file():
        raise ValueError(f"artifact must be a non-symlink regular file: {relative}")
    return resolved


def build_artifact_manifest(
    root: str | Path,
    specs: Sequence[ArtifactSpec],
    *,
    metadata: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Hash an explicit release allow-list in deterministic path order."""

    base = Path(root).resolve()
    if not specs:
        raise ValueError("artifact manifest must not be empty")
    seen: set[str] = set()
    rows: list[dict[str, object]] = []
    for spec in sorted(specs, key=lambda item: item.path):
        if spec.path in seen:
            raise ValueError(f"duplicate artifact path: {spec.path}")
        seen.add(spec.path)
        path = _resolve_regular(base, spec.path)
        rows.append(
            {
                **asdict(spec),
                "size_bytes": path.stat().st_size,
                "sha256": _sha256(path),
            }
        )
    return {
        "schema_version": ARTIFACT_MANIFEST_SCHEMA,
        "metadata": dict(sorted((metadata or {}).items())),
        "artifact_count": len(rows),
        "total_size_bytes": sum(int(row["size_bytes"]) for row in rows),
        "artifacts": rows,
    }


def scan_release_artifacts_for_secrets(
    root: str | Path,
    specs: Iterable[ArtifactSpec],
    *,
    maximum_file_bytes: int = 16 * 1024 * 1024,
    maximum_archive_uncompressed_bytes: int = 64 * 1024 * 1024,
) -> dict[str, object]:
    """Scan text artifacts and ZIP members without copying suspected values."""

    if maximum_file_bytes <= 0:
        raise ValueError("maximum_file_bytes must be positive")
    if maximum_archive_uncompressed_bytes <= 0:
        raise ValueError("maximum_archive_uncompressed_bytes must be positive")
    base = Path(root).resolve()
    findings: list[dict[str, object]] = []
    scanned = 0
    skipped_large: list[str] = []
    skipped_binary: list[str] = []
    seen: set[str] = set()

    def scan_payload(label: str, payload: bytes) -> None:
        nonlocal scanned
        if b"\x00" in payload:
            skipped_binary.append(label)
            return
        scanned += 1
        for pattern_name, pattern in SECRET_PATTERNS:
            for match in pattern.finditer(payload):
                line = payload.count(b"\n", 0, match.start()) + 1
                findings.append(
                    {
                        "path": label,
                        "line": line,
                        "pattern": pattern_name,
                    }
                )

    for spec in sorted(specs, key=lambda item: item.path):
        if spec.path in seen:
            raise ValueError(f"duplicate artifact path: {spec.path}")
        seen.add(spec.path)
        path = _resolve_regular(base, spec.path)
        if zipfile.is_zipfile(path):
            with zipfile.ZipFile(path) as archive:
                members = sorted(
                    (member for member in archive.infolist() if not member.is_dir()),
                    key=lambda member: member.filename,
                )
                total = sum(member.file_size for member in members)
                if total > maximum_archive_uncompressed_bytes:
                    skipped_large.append(f"{spec.path}!<archive-total>")
                    continue
                for member in members:
                    label = f"{spec.path}!{member.filename}"
                    if member.flag_bits & 0x1:
                        skipped_large.append(f"{label}<encrypted>")
                    elif member.file_size > maximum_file_bytes:
                        skipped_large.append(label)
                    else:
                        scan_payload(label, archive.read(member))
            continue

        if path.stat().st_size > maximum_file_bytes:
            skipped_large.append(spec.path)
            continue

        scan_payload(spec.path, path.read_bytes())
    return {
        "schema_version": SECRET_SCAN_SCHEMA,
        "scanner": "bounded_explicit_allowlist_regex_and_zip_v1",
        "matched_values_disclosed": False,
        "scanned_text_files": scanned,
        "skipped_large_files": skipped_large,
        "skipped_binary_files": skipped_binary,
        "findings": findings,
        "passed": not findings and not skipped_large,
    }
