from __future__ import annotations

import io
import hashlib
import json
import pickle
import pickletools
from pathlib import Path
import struct
import zipfile

import numpy as np
import pytest

from telemetry_yield.rml24_benchmark import (
    ArrayBatchSource,
    HDF5_CLASS_MAP_SCHEMA_VERSION,
    NPY_SCHEMA_VERSION,
    NpyDirectoryBatchSource,
    Rml24GroupShardSource,
    Rml24Hdf5PhysicalSource,
    run_rml24_benchmark,
)
from telemetry_yield.rml24_archive import (
    inventory_rml24_hdf5,
    write_rml24_hdf5_inventory,
)
from telemetry_yield.rml24_pickle_convert import (
    PICKLE_MEMO_BYTES_RETAIN_LIMIT,
    PICKLE_TOTAL_MEMO_BYTES_LIMIT,
    SHARD_SCHEMA_VERSION,
    convert_rml24_pickle_zip,
    extract_rml24_pickle_stream,
    probe_rml24_pickle_stream,
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_hdf_contract_fixture(
    root: Path,
    *,
    class_mapping: dict[int, str] | None = None,
    allocate_bit_truth: bool = True,
    bit_length: int = 14000,
) -> tuple[Path, Path, Path, Path, np.ndarray, np.ndarray]:
    import h5py

    mapping = class_mapping or {0: "BPSK", 1: "QPSK"}
    identifiers = np.array(sorted(mapping), dtype=np.int32)
    count = len(identifiers)
    iq = np.arange(count * 2048 * 2, dtype=np.float32).reshape(count, 2048, 2)
    bits = np.zeros((count, 14000), dtype=np.int32)
    for row in range(count):
        bits[row, :8] = np.array([(row + bit) % 2 for bit in range(8)])
    hdf5 = root / "RML24_IQdata.h5"
    with h5py.File(hdf5, "w") as handle:
        handle.create_dataset("IQ_data", data=iq)
        handle.create_dataset("class", data=identifiers)
        if allocate_bit_truth:
            handle.create_dataset("Bit_data", data=bits)
            handle.create_dataset(
                "Bit_len", data=np.full(count, bit_length, dtype=np.int32)
            )
        else:
            handle.create_dataset("Bit_data", shape=bits.shape, dtype=np.int32)
            handle.create_dataset("Bit_len", shape=(count,), dtype=np.int32)
        handle.create_dataset("snr", data=np.arange(count, dtype=np.int32))
        handle.create_dataset(
            "symbol_rate", data=np.full(count, 100_000, dtype=np.int32)
        )

    inventory_path = root / "inventory.json"
    write_rml24_hdf5_inventory(
        inventory_path,
        inventory_rml24_hdf5(hdf5, batch_size=1),
    )
    extraction_path = root / "extraction.json"
    extraction_path.write_text(
        json.dumps(
            {
                "schema_version": "verified-zip-extraction-v1",
                "status": "extracted_verified",
                "path_traversal_checked": True,
                "output": str(hdf5),
                "member_uncompressed_bytes": hdf5.stat().st_size,
                "output_sha256": _sha256(hdf5),
            }
        ),
        encoding="utf-8",
    )
    evidence = root / "mapping-evidence.json"
    evidence.write_text(
        json.dumps(
            {
                "method": "fixture record identity",
                "mapping": {str(key): value for key, value in mapping.items()},
            }
        ),
        encoding="utf-8",
    )
    class_map_path = root / "class-map.json"
    class_map_path.write_text(
        json.dumps(
            {
                "schema_version": HDF5_CLASS_MAP_SCHEMA_VERSION,
                "mapping": {str(key): value for key, value in mapping.items()},
                "provenance": {
                    "verified": True,
                    "method": "cross_format_record_identity",
                    "source_artifact": evidence.name,
                    "source_sha256": _sha256(evidence),
                    "inventory_report_sha256": _sha256(inventory_path),
                    "extraction_report_sha256": _sha256(extraction_path),
                    "hdf5_sha256": _sha256(hdf5),
                },
            }
        ),
        encoding="utf-8",
    )
    return hdf5, inventory_path, extraction_path, class_map_path, iq, bits


def _npy_item(path: Path, array: np.ndarray) -> dict[str, object]:
    np.save(path, array)
    return {
        "file": path.name,
        "shape": list(array.shape),
        "dtype": str(array.dtype),
        "array_payload_bytes": array.nbytes,
        "file_bytes": path.stat().st_size,
        "sha256": _sha256(path),
    }


def _write_equivalent_group_shards(
    root: Path,
    iq: np.ndarray,
    bits: np.ndarray,
    mapping: dict[int, str],
) -> Path:
    groups: list[dict[str, object]] = []
    for row, identifier in enumerate(sorted(mapping)):
        groups.append(
            {
                "modulation": mapping[identifier],
                "snr_db": row,
                "symbol_rate_hz": 100_000,
                "iq": _npy_item(root / f"iq-{row}.npy", iq[row : row + 1]),
                "bits": _npy_item(root / f"bits-{row}.npy", bits[row : row + 1]),
            }
        )
    manifest = root / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": SHARD_SCHEMA_VERSION,
                "complete": True,
                "groups": groups,
            }
        ),
        encoding="utf-8",
    )
    return manifest


def test_synthetic_amr_ber_are_separate_from_unavailable_frame_yield() -> None:
    iq = np.zeros((4, 2, 16), dtype=np.float32)
    labels = np.array([0, 1, 1, 2], dtype=np.int16)
    truth_bits = np.array(
        [
            [0, 1, 0, 1],
            [1, 1, 0, 0],
            [1, 0, 1, 0],
            [0, 0, 1, 1],
        ],
        dtype=np.uint8,
    )
    bit_lengths = np.array([4, 3, 4, 2], dtype=np.int16)
    source = ArrayBatchSource(
        iq,
        labels,
        bits=truth_bits,
        bit_lengths=bit_lengths,
        snr_db=np.array([0, 0, 2, 2]),
        symbol_rate_hz=np.array([100_000] * 4),
        batch_size=2,
    )

    def amr_predictor(batch_iq, batch):
        prediction = np.asarray(batch.modulation).copy()
        if prediction[0] == 1:
            prediction[0] = 0
        return prediction

    def bit_demodulator(batch_iq, batch):
        prediction = np.asarray(batch.bits).copy()
        prediction[:, 0] ^= 1
        return prediction

    report = run_rml24_benchmark(
        source,
        amr_predictor=amr_predictor,
        bit_demodulator=bit_demodulator,
    )
    amr = report["metrics"]["automatic_modulation_recognition"]
    ber = report["metrics"]["bit_demodulation"]
    assert amr["records"] == 4
    assert amr["correct"] == 3
    assert amr["accuracy"] == 0.75
    assert ber["bits"] == 13
    assert ber["bit_errors"] == 4
    assert ber["ber"] == 4 / 13
    frames = report["metrics"]["validated_telemetry_frames"]
    assert frames["status"] == "unavailable_by_dataset_design"
    assert frames["count"] is None
    assert report["claims"]["packet_yield_comparable_to_satnogs"] is False


def test_homogeneous_fast_path_matches_mixed_batch_accounting() -> None:
    iq = np.zeros((4, 2, 16), dtype=np.float32)
    truth_bits = np.array(
        [[0, 1, 0, 1], [1, 1, 0, 0], [1, 0, 1, 0], [0, 0, 1, 1]],
        dtype=np.uint8,
    )
    labels = np.array(["BPSK", "BPSK", "UNSUPPORTED", "UNSUPPORTED"])
    snr = np.array([0, 0, 2, 2])
    rates = np.array([100_000, 100_000, 250_000, 250_000])

    class SupportedBpsk:
        def supports(self, modulation: object) -> bool:
            return str(modulation) == "BPSK"

        def __call__(self, batch_iq, batch):
            predicted = np.asarray(batch.bits).copy()
            predicted[:, 0] ^= 1
            return predicted

    homogeneous = ArrayBatchSource(
        iq,
        labels,
        bits=truth_bits,
        bit_lengths=np.array([4, 3, 4, 2]),
        snr_db=snr,
        symbol_rate_hz=rates,
        batch_size=2,
    )
    order = np.array([0, 2, 1, 3])
    mixed = ArrayBatchSource(
        iq[order],
        labels[order],
        bits=truth_bits[order],
        bit_lengths=np.array([4, 3, 4, 2])[order],
        snr_db=snr[order],
        symbol_rate_hz=rates[order],
        batch_size=4,
    )
    keyword_arguments = {
        "amr_predictor": lambda batch_iq, batch: batch.modulation,
        "bit_demodulator": SupportedBpsk(),
    }

    fast = run_rml24_benchmark(homogeneous, **keyword_arguments)
    fallback = run_rml24_benchmark(mixed, **keyword_arguments)

    assert fast["records_seen"] == fallback["records_seen"] == 4
    assert fast["metrics"] == fallback["metrics"]
    assert fast["claims"] == fallback["claims"]


def test_verified_hdf5_source_matches_equivalent_group_shards(tmp_path: Path) -> None:
    mapping = {0: "BPSK", 1: "QPSK"}
    hdf5, inventory, extraction, class_map, iq, bits = _write_hdf_contract_fixture(
        tmp_path,
        class_mapping=mapping,
    )
    shard_root = tmp_path / "shards"
    shard_root.mkdir()
    manifest = _write_equivalent_group_shards(shard_root, iq, bits, mapping)
    hdf_source = Rml24Hdf5PhysicalSource(
        hdf5,
        inventory_report=inventory,
        extraction_report=extraction,
        class_map=class_map,
        batch_size=2,
    )
    shard_source = Rml24GroupShardSource(manifest, batch_size=2)

    def predicted_bits(batch_iq, batch):
        prediction = np.asarray(batch.bits).copy()
        prediction[:, 0] ^= 1
        return prediction

    keyword_arguments = {
        "amr_predictor": lambda batch_iq, batch: batch.modulation,
        "bit_demodulator": predicted_bits,
    }
    direct = run_rml24_benchmark(hdf_source, **keyword_arguments)
    converted = run_rml24_benchmark(shard_source, **keyword_arguments)

    assert direct["records_seen"] == converted["records_seen"] == 2
    assert direct["metrics"] == converted["metrics"]
    assert direct["claims"] == converted["claims"]
    assert hdf_source.description["whole_array_loaded"] is False
    assert hdf_source.description["class_order_inferred_from_readme_list"] is False
    assert [batch.record_count for batch in hdf_source.iter_batches()] == [2]


def test_hdf5_source_fails_closed_on_report_or_mapping_drift(tmp_path: Path) -> None:
    hdf5, inventory, extraction, class_map, _, _ = _write_hdf_contract_fixture(tmp_path)
    document = json.loads(inventory.read_text(encoding="utf-8"))
    document["datasets"]["Bit_data"]["dtype"] = "uint8"
    inventory.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(ValueError, match="does not bind this inventory report"):
        Rml24Hdf5PhysicalSource(
            hdf5,
            inventory_report=inventory,
            extraction_report=extraction,
            class_map=class_map,
        )

    mapping_document = json.loads(class_map.read_text(encoding="utf-8"))
    mapping_document["provenance"]["inventory_report_sha256"] = _sha256(inventory)
    class_map.write_text(json.dumps(mapping_document), encoding="utf-8")
    with pytest.raises(ValueError, match="inventory dtype mismatch for Bit_data"):
        Rml24Hdf5PhysicalSource(
            hdf5,
            inventory_report=inventory,
            extraction_report=extraction,
            class_map=class_map,
        )


def test_hdf5_source_requires_complete_explicit_class_mapping(tmp_path: Path) -> None:
    hdf5, inventory, extraction, class_map, _, _ = _write_hdf_contract_fixture(tmp_path)
    document = json.loads(class_map.read_text(encoding="utf-8"))
    document["mapping"].pop("1")
    class_map.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(ValueError, match="differs from its verified provenance artifact"):
        Rml24Hdf5PhysicalSource(
            hdf5,
            inventory_report=inventory,
            extraction_report=extraction,
            class_map=class_map,
        )


def test_hdf5_source_rejects_fill_only_bit_truth_datasets(tmp_path: Path) -> None:
    hdf5, inventory, extraction, class_map, _, _ = _write_hdf_contract_fixture(
        tmp_path,
        allocate_bit_truth=False,
    )

    with pytest.raises(ValueError, match="fill-only.*no allocated bit truth"):
        Rml24Hdf5PhysicalSource(
            hdf5,
            inventory_report=inventory,
            extraction_report=extraction,
            class_map=class_map,
        )


def test_hdf5_source_rejects_allocated_but_empty_bit_truth(tmp_path: Path) -> None:
    hdf5, inventory, extraction, class_map, _, _ = _write_hdf_contract_fixture(
        tmp_path,
        bit_length=0,
    )

    with pytest.raises(ValueError, match="positive bit truth for every record"):
        Rml24Hdf5PhysicalSource(
            hdf5,
            inventory_report=inventory,
            extraction_report=extraction,
            class_map=class_map,
        )


def test_npy_directory_uses_non_object_memory_maps(tmp_path: Path) -> None:
    np.save(tmp_path / "iq.npy", np.zeros((3, 2, 8), dtype=np.float32))
    np.save(tmp_path / "class.npy", np.array([1, 2, 3], dtype=np.int16))
    (tmp_path / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": NPY_SCHEMA_VERSION,
                "arrays": {
                    "iq": "iq.npy",
                    "modulation": "class.npy",
                },
            }
        ),
        encoding="utf-8",
    )
    source = NpyDirectoryBatchSource(tmp_path, batch_size=2)
    batches = list(source.iter_batches())
    assert [batch.record_count for batch in batches] == [2, 1]
    report = run_rml24_benchmark(source)
    assert report["records_seen"] == 3
    assert report["metrics"]["bit_demodulation"]["status"] == "unavailable"


def test_strict_pickle_conversion_never_needs_whole_dictionary_load(tmp_path: Path) -> None:
    bits_key = ("BPSK", -2, 100_000.0)
    iq_key = ("BPSK", -2.0, 100_000)
    bits = {
        bits_key: np.array([[[0, 1, 0]], [[1, 1, 0]], [[0, 0, 1]]], dtype=np.int32)
    }
    iq = {
        iq_key: np.arange(3 * 2 * 16_384, dtype=np.float32).reshape(3, 2, 16_384)
    }
    archive = tmp_path / "synthetic.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        bundle.writestr("RML24_BITdata.pkl", pickle.dumps(bits, protocol=4))
        bundle.writestr("RML24_IQdata.pkl", pickle.dumps(iq, protocol=4))
    output = tmp_path / "shards"
    manifest = convert_rml24_pickle_zip(archive, output)
    assert manifest["pickle_executed"] is False
    assert manifest["strict_opcode_allowlist"] is True
    assert manifest["group_count"] == 1
    assert manifest["paired_group_count"] == 1
    assert manifest["pickle_opcode_reads_bounded"] is True
    assert manifest["pickle_memo_bytes_retain_limit"] == PICKLE_MEMO_BYTES_RETAIN_LIMIT
    assert manifest["pickle_total_memo_bytes_limit"] == PICKLE_TOTAL_MEMO_BYTES_LIMIT
    assert manifest["large_memo_payloads_suppressed"] >= 1
    assert manifest["large_memo_payload_bytes_suppressed"] >= iq[iq_key].nbytes
    assert manifest["peak_array_payload_bytes"] == iq[iq_key].nbytes
    assert manifest["peak_retained_pickle_memo_bytes"] <= PICKLE_TOTAL_MEMO_BYTES_LIMIT
    source = Rml24GroupShardSource(output / "manifest.json", batch_size=2)
    report = run_rml24_benchmark(
        source,
        amr_predictor=lambda batch_iq, batch: batch.modulation,
        bit_demodulator=lambda batch_iq, batch: batch.bits,
    )
    assert report["records_seen"] == 3
    assert report["metrics"]["automatic_modulation_recognition"]["accuracy"] == 1.0
    assert report["metrics"]["bit_demodulation"]["ber"] == 0.0
    assert report["source"]["pickle_executed"] is False


def test_strict_pickle_parser_updates_memoized_dtype_after_build(tmp_path: Path) -> None:
    """Match the published protocol-4 stream's dtype BUILD then BINGET reuse."""

    arrays = {
        ("BPSK", -20, 100_000.0): np.zeros((100, 1, 205), dtype=np.int32),
        ("BPSK", -20, 250_000.0): np.ones((100, 1, 512), dtype=np.int32),
    }
    payload = pickle.dumps(arrays, protocol=4)
    opcodes = [opcode.name for opcode, _, _ in pickletools.genops(io.BytesIO(payload))]
    # The first dtype is built and memoized; NumPy reuses it with BINGET for
    # the second ndarray instead of issuing another dtype BUILD.
    assert opcodes.count("BUILD") == 3
    assert "BINGET" in opcodes

    stats: dict[str, int] = {}
    records = extract_rml24_pickle_stream(
        io.BytesIO(payload),
        tmp_path / "shards",
        role="bits",
        stats=stats,
    )

    assert len(records) == 2
    assert {record["dtype"] for record in records} == {"<i4"}
    assert {record["array_payload_bytes"] for record in records} == {82_000, 204_800}
    assert stats["nested_memo_payloads_suppressed"] == 2
    assert stats["nested_memo_payload_bytes_suppressed"] == 286_800
    assert stats["peak_retained_pickle_memo_bytes"] <= PICKLE_TOTAL_MEMO_BYTES_LIMIT


def test_pickle_probe_stops_on_complete_array_without_writing_shards() -> None:
    arrays = {
        ("BPSK", -20, 100_000.0): np.zeros((100, 1, 205), dtype=np.int32),
        ("BPSK", -20, 250_000.0): np.ones((100, 1, 512), dtype=np.int32),
    }
    stats: dict[str, int] = {}

    records = probe_rml24_pickle_stream(
        io.BytesIO(pickle.dumps(arrays, protocol=4)),
        role="bits",
        record_limit=1,
        stats=stats,
    )

    assert len(records) == 1
    assert records[0]["probe_only"] is True
    assert records[0]["file_bytes"] == 0
    assert stats["probe_record_limit"] == 1
    assert stats["probe_records_read"] == 1
    assert stats["probe_stopped_before_pickle_stop"] == 1


def test_strict_parser_refuses_reuse_of_sanitized_nested_raw_state(
    tmp_path: Path,
) -> None:
    shared_state = (b"x" * (PICKLE_MEMO_BYTES_RETAIN_LIMIT + 1),)
    payload = pickle.dumps(
        {"first": shared_state, "second": shared_state},
        protocol=4,
    )

    with pytest.raises(
        ValueError,
        match="reuse a released payload containing large bytes",
    ):
        extract_rml24_pickle_stream(
            io.BytesIO(payload),
            tmp_path / "shards",
            role="bits",
        )


def test_pickle_opcode_reader_rejects_large_declared_payload_before_read(tmp_path: Path) -> None:
    declared = 2 * 1024 * 1024
    stream = io.BytesIO(b"\x80\x04B" + struct.pack("<I", declared))
    with pytest.raises(ValueError, match="read request exceeds bounded limit"):
        extract_rml24_pickle_stream(
            stream,
            tmp_path / "shards",
            role="iq",
            max_array_bytes=1024,
        )


def test_failed_pickle_conversion_leaves_fail_closed_manifest(tmp_path: Path) -> None:
    key = ("BPSK", 0, 100_000)
    bits = {key: np.zeros((1, 1, 4), dtype=np.int32)}
    archive = tmp_path / "broken.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        bundle.writestr("RML24_BITdata.pkl", pickle.dumps(bits, protocol=4))
        bundle.writestr("RML24_IQdata.pkl", b"\x80\x04N.")
    output = tmp_path / "shards"
    with pytest.raises(ValueError, match="expected dictionary"):
        convert_rml24_pickle_zip(archive, output)
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "error"
    assert manifest["complete"] is False
    with pytest.raises(ValueError, match="conversion is incomplete"):
        Rml24GroupShardSource(output / "manifest.json")


def test_group_shard_benchmark_refuses_incomplete_conversion(tmp_path: Path) -> None:
    (tmp_path / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "rml24-group-shards-v1",
                "complete": False,
                "groups": [],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="conversion is incomplete"):
        Rml24GroupShardSource(tmp_path / "manifest.json")


def test_group_shard_source_refuses_complete_manifest_without_paired_bits(
    tmp_path: Path,
) -> None:
    (tmp_path / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "rml24-group-shards-v1",
                "complete": True,
                "groups": [
                    {
                        "modulation": "BPSK",
                        "snr_db": 0,
                        "symbol_rate_hz": 100_000,
                        "iq": {},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="paired bit shard"):
        Rml24GroupShardSource(tmp_path / "manifest.json")
