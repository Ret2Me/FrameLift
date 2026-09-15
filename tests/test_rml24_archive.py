from __future__ import annotations

import hashlib
import json
from pathlib import Path
import zipfile

import pytest

from telemetry_yield.rml24_archive import (
    ZipMemberSpec,
    extract_verified_zip_member,
    inventory_rml24_hdf5,
)


def _archive(path: Path, *, malicious: bool = False) -> bytes:
    payload = bytes(range(251)) * 20
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        bundle.writestr("sample.h5", payload)
        if malicious:
            bundle.writestr("../escape", b"bad")
    return payload


def test_verified_member_extraction_checks_hash_size_crc_and_reuses(tmp_path: Path) -> None:
    archive = tmp_path / "sample.zip"
    payload = _archive(archive)
    archive_md5 = hashlib.md5(archive.read_bytes(), usedforsecurity=False).hexdigest()
    output = tmp_path / "out" / "sample.h5"
    report_path = tmp_path / "report.json"
    spec = ZipMemberSpec(
        "sample.h5",
        len(payload),
        expected_archive_bytes=archive.stat().st_size,
        expected_archive_md5=archive_md5,
    )
    first = extract_verified_zip_member(
        archive,
        output,
        spec,
        source_id="test:small",
        report_path=report_path,
    )
    assert first["status"] == "extracted_verified"
    assert output.read_bytes() == payload
    assert first["output_sha256"] == hashlib.sha256(payload).hexdigest()
    assert json.loads(report_path.read_text())["path_traversal_checked"] is True
    second = extract_verified_zip_member(
        archive,
        output,
        spec,
        source_id="test:small",
    )
    assert second["status"] == "skipped_existing_verified"


def test_rejects_archive_with_any_traversal_member(tmp_path: Path) -> None:
    archive = tmp_path / "bad.zip"
    payload = _archive(archive, malicious=True)
    with pytest.raises(ValueError, match="unsafe ZIP member"):
        extract_verified_zip_member(
            archive,
            tmp_path / "sample.h5",
            ZipMemberSpec("sample.h5", len(payload)),
            source_id="test:small",
        )
    assert not (tmp_path / "escape").exists()


def test_hdf5_inventory_reads_only_metadata_axes_in_batches(tmp_path: Path) -> None:
    h5py = pytest.importorskip("h5py")
    np = pytest.importorskip("numpy")
    path = tmp_path / "sample.h5"
    with h5py.File(path, "w") as handle:
        handle.create_dataset("IQ_data", data=np.zeros((5, 2, 8), dtype=np.float32))
        handle.create_dataset("class", data=np.array([0, 0, 1, 1, 1]))
        handle.create_dataset("SNR", data=np.array([-2, -2, 0, 0, 2]))
        handle.create_dataset("symbol_rate", data=np.array([100, 100, 200, 200, 200]))
    report = inventory_rml24_hdf5(path, batch_size=2)
    assert report["complete"] is True
    assert report["record_count"] == 5
    assert report["records_scanned"] == 5
    assert report["group_count"] == 3
    assert report["iq_samples_read"] is False
    assert report["whole_array_loaded"] is False
