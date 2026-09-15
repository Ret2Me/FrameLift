"""Safe, single-member extraction and streaming inventory for RML24 HDF5."""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import stat
from typing import Any
import zipfile
import zlib

from .download_guard import DownloadPlan, assert_download_allowed


HDF5_ARCHIVE_BYTES = 20_073_680_616
HDF5_ARCHIVE_MD5 = "a34af2743489f04f78bf6c4a2cf80883"
HDF5_MEMBER = "RML24_IQdata.h5"
HDF5_MEMBER_BYTES = 21_691_910_760


@dataclass(frozen=True, slots=True)
class ZipMemberSpec:
    member: str
    expected_uncompressed_bytes: int
    expected_archive_bytes: int | None = None
    expected_archive_md5: str | None = None

    def __post_init__(self) -> None:
        _validate_member_name(self.member)
        if self.expected_uncompressed_bytes <= 0:
            raise ValueError("expected_uncompressed_bytes must be positive")
        if self.expected_archive_bytes is not None and self.expected_archive_bytes <= 0:
            raise ValueError("expected_archive_bytes must be positive")
        if self.expected_archive_md5 is not None:
            value = self.expected_archive_md5.lower()
            if len(value) != 32 or any(char not in "0123456789abcdef" for char in value):
                raise ValueError("expected_archive_md5 must be hexadecimal")


def _validate_member_name(name: str) -> None:
    path = PurePosixPath(name)
    if not name or path.is_absolute() or ".." in path.parts or "\\" in name:
        raise ValueError(f"unsafe ZIP member path: {name!r}")


def _atomic_json(path: Path, document: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _hash_archive(path: Path, *, chunk_size: int) -> tuple[str, str]:
    md5 = hashlib.md5(usedforsecurity=False)
    sha256 = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(chunk_size), b""):
            md5.update(block)
            sha256.update(block)
    return md5.hexdigest(), sha256.hexdigest()


def _hash_extracted(path: Path, *, chunk_size: int) -> tuple[int, str]:
    crc = 0
    sha256 = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(chunk_size), b""):
            crc = zlib.crc32(block, crc)
            sha256.update(block)
    return crc & 0xFFFFFFFF, sha256.hexdigest()


def extract_verified_zip_member(
    archive: Path,
    output_path: Path,
    spec: ZipMemberSpec,
    *,
    source_id: str,
    plan: DownloadPlan | None = None,
    report_path: Path | None = None,
    chunk_size: int = 8 * 1024 * 1024,
) -> dict[str, object]:
    """Extract exactly one regular member with size, path, CRC and hash checks."""

    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")
    archive = Path(archive)
    output_path = Path(output_path)
    if not archive.is_file():
        raise FileNotFoundError(archive)
    archive_bytes = archive.stat().st_size
    if spec.expected_archive_bytes is not None and archive_bytes != spec.expected_archive_bytes:
        raise ValueError(
            f"archive size mismatch: {archive_bytes} != {spec.expected_archive_bytes}"
        )
    assert_download_allowed(
        source_id,
        spec.expected_uncompressed_bytes,
        output_path.parent,
        plan=plan,
    )
    archive_md5, archive_sha256 = _hash_archive(archive, chunk_size=chunk_size)
    if spec.expected_archive_md5 is not None and archive_md5 != spec.expected_archive_md5.lower():
        raise ValueError("archive MD5 differs from the repository checksum")

    with zipfile.ZipFile(archive) as bundle:
        members = bundle.infolist()
        for candidate in members:
            _validate_member_name(candidate.filename)
        if sum(candidate.filename == spec.member for candidate in members) != 1:
            raise ValueError("required ZIP member must occur exactly once")
        try:
            info = bundle.getinfo(spec.member)
        except KeyError as exc:
            raise ValueError(f"required ZIP member {spec.member!r} is missing") from exc
        if info.is_dir():
            raise ValueError("requested ZIP member is a directory")
        unix_mode = (info.external_attr >> 16) & 0xFFFF
        if unix_mode and stat.S_IFMT(unix_mode) == stat.S_IFLNK:
            raise ValueError("ZIP member must not be a symbolic link")
        if info.flag_bits & 0x1:
            raise ValueError("encrypted ZIP members are not supported")
        if info.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}:
            raise ValueError("unsupported ZIP compression method")
        if info.file_size != spec.expected_uncompressed_bytes:
            raise ValueError(
                f"member size mismatch: {info.file_size} != {spec.expected_uncompressed_bytes}"
            )

        output_path.parent.mkdir(parents=True, exist_ok=True)
        if output_path.exists() and output_path.stat().st_size == info.file_size:
            existing_crc, existing_sha256 = _hash_extracted(output_path, chunk_size=chunk_size)
            if existing_crc == info.CRC:
                document = {
                    "schema_version": "verified-zip-extraction-v1",
                    "generated_at": datetime.now(UTC).isoformat(),
                    "status": "skipped_existing_verified",
                    "archive": str(archive),
                    "archive_bytes": archive_bytes,
                    "archive_md5": archive_md5,
                    "archive_sha256": archive_sha256,
                    "member": info.filename,
                    "member_compressed_bytes": info.compress_size,
                    "member_uncompressed_bytes": info.file_size,
                    "member_crc32": f"{info.CRC:08x}",
                    "output": str(output_path),
                    "output_sha256": existing_sha256,
                    "path_traversal_checked": True,
                }
                if report_path is not None:
                    _atomic_json(report_path, document)
                return document

        temporary = output_path.with_name(output_path.name + ".part")
        if temporary.exists():
            stem = temporary.name + ".incomplete-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
            quarantine = temporary.with_name(stem)
            serial = 1
            while quarantine.exists():
                quarantine = temporary.with_name(f"{stem}.{serial}")
                serial += 1
            os.replace(temporary, quarantine)
        sha256 = hashlib.sha256()
        crc = 0
        written = 0
        with bundle.open(info, "r") as source, temporary.open("wb") as output:
            while True:
                block = source.read(chunk_size)
                if not block:
                    break
                output.write(block)
                written += len(block)
                if written > info.file_size:
                    raise ValueError("ZIP member expanded beyond its declared size")
                crc = zlib.crc32(block, crc)
                sha256.update(block)
            output.flush()
            os.fsync(output.fileno())
        if written != info.file_size:
            raise ValueError(f"short extraction: {written} != {info.file_size}")
        if (crc & 0xFFFFFFFF) != info.CRC:
            raise ValueError("extracted member CRC32 mismatch")
        os.replace(temporary, output_path)
        document = {
            "schema_version": "verified-zip-extraction-v1",
            "generated_at": datetime.now(UTC).isoformat(),
            "status": "extracted_verified",
            "archive": str(archive),
            "archive_bytes": archive_bytes,
            "archive_md5": archive_md5,
            "archive_sha256": archive_sha256,
            "member": info.filename,
            "member_compressed_bytes": info.compress_size,
            "member_uncompressed_bytes": info.file_size,
            "member_crc32": f"{info.CRC:08x}",
            "output": str(output_path),
            "output_sha256": sha256.hexdigest(),
            "path_traversal_checked": True,
        }
        if report_path is not None:
            _atomic_json(report_path, document)
        return document


def extract_rml24_hdf5(
    archive: Path,
    output_path: Path,
    *,
    plan: DownloadPlan,
    report_path: Path | None = None,
) -> dict[str, object]:
    return extract_verified_zip_member(
        archive,
        output_path,
        ZipMemberSpec(
            HDF5_MEMBER,
            HDF5_MEMBER_BYTES,
            expected_archive_bytes=HDF5_ARCHIVE_BYTES,
            expected_archive_md5=HDF5_ARCHIVE_MD5,
        ),
        source_id="local:rml24:hdf5-extract",
        plan=plan,
        report_path=report_path,
    )


def _scalar(value: object) -> str | int | float | bool | None:
    if hasattr(value, "item"):
        value = value.item()  # type: ignore[assignment]
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def inventory_rml24_hdf5(
    path: Path,
    *,
    batch_size: int = 65_536,
    max_records: int | None = None,
) -> dict[str, object]:
    """Inventory labels/SNR/rates in slices without reading the IQ dataset."""

    if batch_size <= 0 or (max_records is not None and max_records <= 0):
        raise ValueError("batch_size/max_records must be positive")
    try:
        import h5py
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("RML24 HDF5 inventory requires h5py") from exc

    role_candidates = {
        "modulation": ("class", "modulation", "Mod.class", "label"),
        "snr_db": ("SNR", "snr", "snr_db"),
        "symbol_rate_hz": ("symbol_rate", "Symbol_rate", "code_rate"),
        "iq": ("IQ_data", "iq", "IQ"),
    }
    with h5py.File(path, "r") as handle:
        datasets: dict[str, object] = {}
        for name, value in handle.items():
            if isinstance(value, h5py.Dataset):
                datasets[name] = {
                    "shape": list(value.shape),
                    "dtype": str(value.dtype),
                    "chunks": list(value.chunks) if value.chunks else None,
                    "compression": value.compression,
                }
        resolved = {
            role: next((name for name in candidates if name in handle), None)
            for role, candidates in role_candidates.items()
        }
        if resolved["modulation"] is None:
            raise ValueError("HDF5 has no recognized modulation-label dataset")
        label_dataset = handle[resolved["modulation"]]
        total = int(len(label_dataset))
        limit = total if max_records is None else min(total, max_records)
        for role in ("snr_db", "symbol_rate_hz"):
            name = resolved[role]
            if name is not None and len(handle[name]) != total:
                raise ValueError(f"{role} dataset differs in record count")
        counts: Counter[tuple[object, object, object]] = Counter()
        for start in range(0, limit, batch_size):
            stop = min(start + batch_size, limit)
            labels = label_dataset[start:stop]
            snrs = handle[resolved["snr_db"]][start:stop] if resolved["snr_db"] else None
            rates = (
                handle[resolved["symbol_rate_hz"]][start:stop]
                if resolved["symbol_rate_hz"]
                else None
            )
            for index in range(stop - start):
                counts[
                    (
                        _scalar(labels[index]),
                        _scalar(snrs[index]) if snrs is not None else None,
                        _scalar(rates[index]) if rates is not None else None,
                    )
                ] += 1
    groups = [
        {
            "modulation": key[0],
            "snr_db": key[1],
            "symbol_rate_hz": key[2],
            "records": count,
        }
        for key, count in sorted(counts.items(), key=lambda item: tuple(str(v) for v in item[0]))
    ]
    return {
        "schema_version": "rml24-hdf5-inventory-v1",
        "generated_at": datetime.now(UTC).isoformat(),
        "path": str(path),
        "file_bytes": Path(path).stat().st_size,
        "record_count": total,
        "records_scanned": limit,
        "complete": limit == total,
        "batch_size": batch_size,
        "iq_samples_read": False,
        "whole_array_loaded": False,
        "resolved_datasets": resolved,
        "datasets": datasets,
        "group_count": len(groups),
        "groups": groups,
    }


def write_rml24_hdf5_inventory(path: Path, inventory: dict[str, object]) -> None:
    _atomic_json(path, inventory)
