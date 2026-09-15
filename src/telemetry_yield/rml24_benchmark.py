"""Bounded-memory RML24 physical-layer benchmark.

RML24 supplies modulation labels and (in its Pickle edition) transmitted bits.
It does not supply validated AX.25/CCSDS packet truth, so this module deliberately
reports AMR accuracy and BER separately and never turns either into frame yield.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import json
import math
import os
from pathlib import Path
from typing import Any, Protocol


REPORT_SCHEMA_VERSION = "rml24-physical-layer-benchmark-v1"
NPY_SCHEMA_VERSION = "rml24-npy-batches-v1"
HDF5_CLASS_MAP_SCHEMA_VERSION = "rml24-hdf5-class-map-v1"


def _sha256_path(path: Path, *, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(chunk_size), b""):
            digest.update(block)
    return digest.hexdigest()


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value.lower())
    )


def _load_json_object(path: Path, *, role: str) -> tuple[dict[str, object], str]:
    payload = path.read_bytes()
    try:
        value = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{role} must be valid UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{role} must be a JSON object")
    return value, hashlib.sha256(payload).hexdigest()


def _report_path_matches(
    reported: object,
    actual: Path,
    *,
    report_path: Path,
) -> bool:
    if not isinstance(reported, str) or not reported:
        return False
    candidate = Path(reported)
    if candidate.is_absolute():
        return candidate.resolve() == actual.resolve()
    return actual.resolve() in {
        (Path.cwd() / candidate).resolve(),
        (report_path.parent / candidate).resolve(),
    }


def _require_numpy() -> Any:
    try:
        import numpy as np
    except ImportError as exc:  # pragma: no cover - dependency guard
        raise RuntimeError("RML24 benchmarking requires the analysis extra (numpy)") from exc
    return np


@dataclass(frozen=True, slots=True)
class Rml24Batch:
    iq: Any
    modulation: Any
    bits: Any | None = None
    bit_lengths: Any | None = None
    snr_db: Any | None = None
    symbol_rate_hz: Any | None = None

    @property
    def record_count(self) -> int:
        return int(len(self.modulation))


class Rml24BatchSource(Protocol):
    @property
    def record_count(self) -> int: ...

    @property
    def description(self) -> Mapping[str, object]: ...

    def iter_batches(self, *, max_records: int | None = None) -> Iterator[Rml24Batch]: ...


class ArrayBatchSource:
    """Small in-memory source intended for tests and bounded prepared shards."""

    def __init__(
        self,
        iq: Any,
        modulation: Any,
        *,
        bits: Any | None = None,
        bit_lengths: Any | None = None,
        snr_db: Any | None = None,
        symbol_rate_hz: Any | None = None,
        batch_size: int = 256,
    ) -> None:
        if batch_size <= 0:
            raise ValueError("batch_size must be positive")
        self.arrays = {
            "iq": iq,
            "modulation": modulation,
            "bits": bits,
            "bit_lengths": bit_lengths,
            "snr_db": snr_db,
            "symbol_rate_hz": symbol_rate_hz,
        }
        self.batch_size = batch_size
        self._record_count = int(len(modulation))
        if len(iq) != self._record_count:
            raise ValueError("IQ and modulation arrays differ in length")
        for name, value in self.arrays.items():
            if value is not None and len(value) != self._record_count:
                raise ValueError(f"{name} differs in record count")

    @property
    def record_count(self) -> int:
        return self._record_count

    @property
    def description(self) -> Mapping[str, object]:
        return {"source_type": "bounded_arrays", "record_count": self.record_count}

    def iter_batches(self, *, max_records: int | None = None) -> Iterator[Rml24Batch]:
        limit = self.record_count if max_records is None else min(self.record_count, max_records)
        for start in range(0, limit, self.batch_size):
            stop = min(start + self.batch_size, limit)
            sliced = {
                name: value[start:stop] if value is not None else None
                for name, value in self.arrays.items()
            }
            yield Rml24Batch(**sliced)


class Hdf5BatchSource:
    """Slice an HDF5 dataset without the authors' unsafe whole-array ``[:]`` load."""

    DEFAULT_KEYS = {
        "iq": ("IQ_data", "iq", "IQ"),
        "modulation": ("class", "modulation", "Mod.class", "label"),
        "bits": ("Bitdata", "bit_data", "bits"),
        "bit_lengths": ("bit_lengths", "Bitlength"),
        "snr_db": ("SNR", "snr", "snr_db"),
        "symbol_rate_hz": ("symbol_rate", "Symbol_rate", "code_rate"),
    }

    def __init__(
        self,
        path: Path,
        *,
        batch_size: int = 256,
        keys: Mapping[str, str] | None = None,
    ) -> None:
        if batch_size <= 0:
            raise ValueError("batch_size must be positive")
        self.path = Path(path)
        self.batch_size = batch_size
        self.keys = dict(keys or {})
        self._resolved_keys: dict[str, str | None] | None = None
        self._record_count: int | None = None

    def _inspect(self) -> None:
        if self._resolved_keys is not None:
            return
        try:
            import h5py
        except ImportError as exc:  # pragma: no cover - dependency guard
            raise RuntimeError("HDF5 RML24 input requires h5py") from exc
        with h5py.File(self.path, "r") as handle:
            resolved: dict[str, str | None] = {}
            for role, candidates in self.DEFAULT_KEYS.items():
                explicit = self.keys.get(role)
                if explicit is not None:
                    if explicit not in handle:
                        raise ValueError(f"HDF5 dataset {explicit!r} does not exist")
                    resolved[role] = explicit
                else:
                    resolved[role] = next((key for key in candidates if key in handle), None)
            if resolved["iq"] is None or resolved["modulation"] is None:
                raise ValueError("HDF5 must contain IQ and modulation-label datasets")
            count = int(len(handle[resolved["modulation"]]))
            for role, key in resolved.items():
                if key is not None and len(handle[key]) != count:
                    raise ValueError(f"HDF5 dataset for {role} differs in record count")
            self._resolved_keys = resolved
            self._record_count = count

    @property
    def record_count(self) -> int:
        self._inspect()
        assert self._record_count is not None
        return self._record_count

    @property
    def description(self) -> Mapping[str, object]:
        self._inspect()
        return {
            "source_type": "hdf5_streaming_slices",
            "path": str(self.path),
            "record_count": self.record_count,
            "batch_size": self.batch_size,
            "datasets": self._resolved_keys,
            "whole_array_loaded": False,
        }

    def iter_batches(self, *, max_records: int | None = None) -> Iterator[Rml24Batch]:
        self._inspect()
        assert self._resolved_keys is not None
        import h5py

        limit = self.record_count if max_records is None else min(self.record_count, max_records)
        with h5py.File(self.path, "r") as handle:
            for start in range(0, limit, self.batch_size):
                stop = min(start + self.batch_size, limit)
                values: dict[str, Any | None] = {}
                for role, key in self._resolved_keys.items():
                    values[role] = handle[key][start:stop] if key is not None else None
                yield Rml24Batch(**values)


class Rml24Hdf5PhysicalSource:
    """Read the published RML24 HDF5 directly under a verified contract.

    The HDF5 upload stores class IDs without an authoritative ID ordering.  A
    separate, explicitly verified mapping is therefore mandatory: silently
    inferring the order from the README's modulation list is forbidden.
    """

    DATASETS = {
        "iq": "IQ_data",
        "modulation_ids": "class",
        "bits": "Bit_data",
        "bit_lengths": "Bit_len",
        "snr_db": "snr",
        "symbol_rate_hz": "symbol_rate",
    }
    DTYPES = {
        "IQ_data": "float32",
        "class": "int32",
        "Bit_data": "int32",
        "Bit_len": "int32",
        "snr": "int32",
        "symbol_rate": "int32",
    }
    CLASS_MAP_METHODS = frozenset(
        {"official_published_mapping", "cross_format_record_identity"}
    )

    def __init__(
        self,
        path: Path,
        *,
        inventory_report: Path,
        extraction_report: Path,
        class_map: Path,
        batch_size: int = 128,
    ) -> None:
        if batch_size <= 0:
            raise ValueError("batch_size must be positive")
        self.path = Path(path)
        self.inventory_report_path = Path(inventory_report)
        self.extraction_report_path = Path(extraction_report)
        self.class_map_path = Path(class_map)
        self.batch_size = batch_size
        self._record_count = 0
        self._class_mapping: dict[int, str] = {}
        self._inventory_sha256 = ""
        self._extraction_report_sha256 = ""
        self._class_map_sha256 = ""
        self._extracted_hdf5_sha256 = ""
        self._class_map_provenance: dict[str, object] = {}
        self._validate_contract()

    @staticmethod
    def _validate_mapping(
        document: Mapping[str, object],
        *,
        map_path: Path,
        inventory_sha256: str,
        extraction_report_sha256: str,
        extraction_document: Mapping[str, object],
    ) -> tuple[dict[int, str], dict[str, object]]:
        if document.get("schema_version") != HDF5_CLASS_MAP_SCHEMA_VERSION:
            raise ValueError("unsupported RML24 HDF5 class-map schema")
        raw_mapping = document.get("mapping")
        if not isinstance(raw_mapping, dict) or not raw_mapping:
            raise ValueError("RML24 HDF5 class map must contain a nonempty mapping")
        mapping: dict[int, str] = {}
        for raw_id, raw_name in raw_mapping.items():
            if (
                not isinstance(raw_id, str)
                or not raw_id.isascii()
                or not raw_id.isdecimal()
                or str(int(raw_id)) != raw_id
            ):
                raise ValueError("class-map keys must be canonical nonnegative integer strings")
            if not isinstance(raw_name, str) or not raw_name.strip():
                raise ValueError("class-map values must be nonempty modulation names")
            mapping[int(raw_id)] = raw_name
        if len(set(mapping.values())) != len(mapping):
            raise ValueError("class-map modulation names must be unique")

        provenance = document.get("provenance")
        if not isinstance(provenance, dict) or provenance.get("verified") is not True:
            raise ValueError("class-map provenance must be explicitly verified")
        method = provenance.get("method")
        if method not in Rml24Hdf5PhysicalSource.CLASS_MAP_METHODS:
            raise ValueError("class-map provenance method is not accepted")
        source_artifact = provenance.get("source_artifact")
        source_sha256 = provenance.get("source_sha256")
        if not isinstance(source_artifact, str) or not source_artifact:
            raise ValueError("class-map provenance source_artifact is required")
        if not _is_sha256(source_sha256):
            raise ValueError("class-map provenance source_sha256 is invalid")
        source_path = Path(source_artifact)
        if not source_path.is_absolute():
            source_path = map_path.parent / source_path
        if not source_path.is_file():
            raise ValueError("class-map provenance source artifact is unavailable")
        if _sha256_path(source_path) != str(source_sha256).lower():
            raise ValueError("class-map provenance source artifact SHA-256 mismatch")
        source_document, _ = _load_json_object(
            source_path, role="class-map provenance source artifact"
        )
        if source_document.get("mapping") != raw_mapping:
            raise ValueError("class map differs from its verified provenance artifact")
        if provenance.get("inventory_report_sha256") != inventory_sha256:
            raise ValueError("class map does not bind this inventory report")
        if provenance.get("extraction_report_sha256") != extraction_report_sha256:
            raise ValueError("class map does not bind this extraction report")
        output_sha256 = extraction_document.get("output_sha256")
        if not _is_sha256(output_sha256):
            raise ValueError("extraction report output_sha256 is invalid")
        if provenance.get("hdf5_sha256") != output_sha256:
            raise ValueError("class map does not bind the extracted HDF5 SHA-256")
        return mapping, dict(provenance)

    @staticmethod
    def _inventory_class_ids(document: Mapping[str, object]) -> set[int]:
        raw_groups = document.get("groups")
        if not isinstance(raw_groups, list) or not raw_groups:
            raise ValueError("complete HDF5 inventory must contain groups")
        identifiers: set[int] = set()
        seen_groups: set[tuple[int, object, object]] = set()
        records = 0
        for group in raw_groups:
            if not isinstance(group, dict):
                raise ValueError("HDF5 inventory groups must be objects")
            identifier = group.get("modulation")
            count = group.get("records")
            snr = group.get("snr_db")
            rate = group.get("symbol_rate_hz")
            if not isinstance(identifier, int) or isinstance(identifier, bool) or identifier < 0:
                raise ValueError("HDF5 inventory class IDs must be nonnegative integers")
            if not isinstance(count, int) or isinstance(count, bool) or count <= 0:
                raise ValueError("HDF5 inventory group counts must be positive integers")
            if (
                not isinstance(snr, (int, float))
                or isinstance(snr, bool)
                or not math.isfinite(float(snr))
            ):
                raise ValueError("HDF5 inventory SNR must be finite and numeric")
            if (
                not isinstance(rate, (int, float))
                or isinstance(rate, bool)
                or not math.isfinite(float(rate))
                or float(rate) <= 0
            ):
                raise ValueError("HDF5 inventory symbol rate must be positive and finite")
            key = (identifier, snr, rate)
            if key in seen_groups:
                raise ValueError("HDF5 inventory contains a duplicate group")
            seen_groups.add(key)
            identifiers.add(identifier)
            records += count
        if document.get("group_count") != len(raw_groups):
            raise ValueError("HDF5 inventory group_count mismatch")
        if document.get("record_count") != records:
            raise ValueError("HDF5 inventory group totals do not match record_count")
        return identifiers

    def _validate_contract(self) -> None:
        if not self.path.is_file():
            raise FileNotFoundError(self.path)
        inventory, inventory_sha256 = _load_json_object(
            self.inventory_report_path, role="HDF5 inventory report"
        )
        extraction, extraction_sha256 = _load_json_object(
            self.extraction_report_path, role="HDF5 extraction report"
        )
        class_map, class_map_sha256 = _load_json_object(
            self.class_map_path, role="HDF5 class map"
        )
        if inventory.get("schema_version") != "rml24-hdf5-inventory-v1":
            raise ValueError("unsupported RML24 HDF5 inventory schema")
        if (
            inventory.get("complete") is not True
            or inventory.get("records_scanned") != inventory.get("record_count")
            or inventory.get("iq_samples_read") is not False
            or inventory.get("whole_array_loaded") is not False
        ):
            raise ValueError("RML24 HDF5 inventory must be complete and metadata-only")
        if not _report_path_matches(
            inventory.get("path"), self.path, report_path=self.inventory_report_path
        ):
            raise ValueError("HDF5 inventory path does not identify the benchmark input")

        if extraction.get("schema_version") != "verified-zip-extraction-v1":
            raise ValueError("unsupported RML24 HDF5 extraction report schema")
        if extraction.get("status") not in {
            "extracted_verified",
            "skipped_existing_verified",
        } or extraction.get("path_traversal_checked") is not True:
            raise ValueError("RML24 HDF5 extraction report is not verified")
        if not _report_path_matches(
            extraction.get("output"), self.path, report_path=self.extraction_report_path
        ):
            raise ValueError("HDF5 extraction report does not identify the benchmark input")

        file_bytes = self.path.stat().st_size
        if (
            inventory.get("file_bytes") != file_bytes
            or extraction.get("member_uncompressed_bytes") != file_bytes
        ):
            raise ValueError("HDF5 file size differs from its verified reports")
        mapping, provenance = self._validate_mapping(
            class_map,
            map_path=self.class_map_path,
            inventory_sha256=inventory_sha256,
            extraction_report_sha256=extraction_sha256,
            extraction_document=extraction,
        )
        inventory_ids = self._inventory_class_ids(inventory)
        if set(mapping) != inventory_ids:
            raise ValueError("class-map IDs differ from the complete HDF5 inventory")

        raw_datasets = inventory.get("datasets")
        if not isinstance(raw_datasets, dict) or set(raw_datasets) != set(self.DTYPES):
            raise ValueError("HDF5 inventory dataset names differ from the RML24 contract")
        expected_resolved = {
            "iq": "IQ_data",
            "modulation": "class",
            "snr_db": "snr",
            "symbol_rate_hz": "symbol_rate",
        }
        if inventory.get("resolved_datasets") != expected_resolved:
            raise ValueError("HDF5 inventory role resolution differs from the RML24 contract")
        count = inventory.get("record_count")
        if not isinstance(count, int) or isinstance(count, bool) or count <= 0:
            raise ValueError("HDF5 inventory record_count must be positive")

        try:
            import h5py
        except ImportError as exc:  # pragma: no cover - dependency guard
            raise RuntimeError("HDF5 RML24 input requires h5py") from exc
        np = _require_numpy()
        expected_shapes = {
            "IQ_data": (count, 2048, 2),
            "class": (count,),
            "Bit_data": (count, 14000),
            "Bit_len": (count,),
            "snr": (count,),
            "symbol_rate": (count,),
        }
        with h5py.File(self.path, "r") as handle:
            if set(handle.keys()) != set(self.DTYPES):
                raise ValueError("live HDF5 dataset names differ from the RML24 contract")
            for name, dtype_name in self.DTYPES.items():
                dataset = handle.get(name)
                if not isinstance(dataset, h5py.Dataset):
                    raise ValueError(f"HDF5 object {name!r} is not a dataset")
                inventory_entry = raw_datasets.get(name)
                if not isinstance(inventory_entry, dict):
                    raise ValueError(f"HDF5 inventory entry {name!r} is invalid")
                if inventory_entry.get("shape") != list(expected_shapes[name]):
                    raise ValueError(f"HDF5 inventory shape mismatch for {name}")
                if inventory_entry.get("dtype") != dtype_name:
                    raise ValueError(f"HDF5 inventory dtype mismatch for {name}")
                if dataset.shape != expected_shapes[name]:
                    raise ValueError(f"live HDF5 shape mismatch for {name}")
                if dataset.dtype != np.dtype(dtype_name):
                    raise ValueError(f"live HDF5 dtype mismatch for {name}")
                if name in {"Bit_data", "Bit_len"} and dataset.id.get_storage_size() <= 0:
                    raise ValueError(
                        f"live HDF5 {name} is fill-only and contains no allocated bit truth"
                    )
            bit_length_dataset = handle["Bit_len"]
            for start in range(0, count, 65_536):
                lengths = bit_length_dataset[start : min(start + 65_536, count)]
                if np.any(lengths <= 0) or np.any(lengths > expected_shapes["Bit_data"][1]):
                    raise ValueError(
                        "live HDF5 Bit_len must provide positive bit truth for every record"
                    )

        self._record_count = count
        self._class_mapping = mapping
        self._inventory_sha256 = inventory_sha256
        self._extraction_report_sha256 = extraction_sha256
        self._class_map_sha256 = class_map_sha256
        self._extracted_hdf5_sha256 = str(extraction["output_sha256"])
        self._class_map_provenance = provenance

    @property
    def record_count(self) -> int:
        return self._record_count

    @property
    def description(self) -> Mapping[str, object]:
        return {
            "source_type": "verified_rml24_hdf5_streaming_slices",
            "path": str(self.path),
            "record_count": self.record_count,
            "batch_size": self.batch_size,
            "datasets": dict(self.DATASETS),
            "whole_array_loaded": False,
            "bit_truth_available": True,
            "bit_truth_storage_allocated": True,
            "bit_lengths_validated_in_bounded_slices": True,
            "dataset_names_shapes_dtypes_validated": True,
            "file_size_validated": True,
            "hdf5_full_sha256_recomputed_this_run": False,
            "extracted_hdf5_sha256": self._extracted_hdf5_sha256,
            "inventory_report": str(self.inventory_report_path),
            "inventory_report_sha256": self._inventory_sha256,
            "extraction_report": str(self.extraction_report_path),
            "extraction_report_sha256": self._extraction_report_sha256,
            "class_map": str(self.class_map_path),
            "class_map_sha256": self._class_map_sha256,
            "class_id_mapping": {
                str(identifier): name
                for identifier, name in sorted(self._class_mapping.items())
            },
            "class_map_provenance": dict(self._class_map_provenance),
            "class_order_inferred_from_readme_list": False,
        }

    def iter_batches(self, *, max_records: int | None = None) -> Iterator[Rml24Batch]:
        if max_records is not None and max_records <= 0:
            raise ValueError("max_records must be positive")
        import h5py

        np = _require_numpy()
        limit = self.record_count if max_records is None else min(self.record_count, max_records)
        with h5py.File(self.path, "r") as handle:
            for start in range(0, limit, self.batch_size):
                stop = min(start + self.batch_size, limit)
                identifiers = handle[self.DATASETS["modulation_ids"]][start:stop]
                try:
                    modulation = np.asarray(
                        [self._class_mapping[int(identifier)] for identifier in identifiers]
                    )
                except (KeyError, TypeError, ValueError) as exc:
                    raise ValueError("live HDF5 contains an unmapped class ID") from exc
                bits = handle[self.DATASETS["bits"]][start:stop]
                bit_lengths = handle[self.DATASETS["bit_lengths"]][start:stop]
                if np.any(bit_lengths < 0) or np.any(bit_lengths > bits.shape[1]):
                    raise ValueError("live HDF5 contains an invalid Bit_len value")
                yield Rml24Batch(
                    iq=handle[self.DATASETS["iq"]][start:stop],
                    modulation=modulation,
                    bits=bits,
                    bit_lengths=bit_lengths,
                    snr_db=handle[self.DATASETS["snr_db"]][start:stop],
                    symbol_rate_hz=handle[self.DATASETS["symbol_rate_hz"]][start:stop],
                )


class NpyDirectoryBatchSource:
    """Memory-map a trusted, non-object NPY conversion in constant working memory."""

    def __init__(self, directory: Path, *, batch_size: int = 256) -> None:
        if batch_size <= 0:
            raise ValueError("batch_size must be positive")
        self.directory = Path(directory)
        self.batch_size = batch_size
        manifest = json.loads((self.directory / "manifest.json").read_text(encoding="utf-8"))
        if manifest.get("schema_version") != NPY_SCHEMA_VERSION:
            raise ValueError("unsupported NPY directory manifest")
        raw_arrays = manifest.get("arrays")
        if not isinstance(raw_arrays, dict):
            raise ValueError("NPY manifest arrays must be an object")
        np = _require_numpy()
        arrays: dict[str, Any | None] = {}
        for role in ("iq", "modulation", "bits", "bit_lengths", "snr_db", "symbol_rate_hz"):
            filename = raw_arrays.get(role)
            if filename is None:
                arrays[role] = None
                continue
            if not isinstance(filename, str) or Path(filename).name != filename:
                raise ValueError(f"unsafe NPY filename for {role}")
            arrays[role] = np.load(
                self.directory / filename,
                mmap_mode="r",
                allow_pickle=False,
            )
            if arrays[role].dtype.hasobject:
                raise ValueError("object arrays are forbidden in streaming NPY input")
        if arrays["iq"] is None or arrays["modulation"] is None:
            raise ValueError("NPY directory requires IQ and modulation arrays")
        self.arrays = arrays
        self._record_count = int(len(arrays["modulation"]))
        for role, value in arrays.items():
            if value is not None and len(value) != self._record_count:
                raise ValueError(f"NPY array for {role} differs in record count")

    @property
    def record_count(self) -> int:
        return self._record_count

    @property
    def description(self) -> Mapping[str, object]:
        return {
            "source_type": "npy_memory_map",
            "directory": str(self.directory),
            "record_count": self.record_count,
            "batch_size": self.batch_size,
            "whole_array_loaded": False,
            "bit_truth_available": self.arrays["bits"] is not None,
        }

    def iter_batches(self, *, max_records: int | None = None) -> Iterator[Rml24Batch]:
        limit = self.record_count if max_records is None else min(self.record_count, max_records)
        for start in range(0, limit, self.batch_size):
            stop = min(start + self.batch_size, limit)
            yield Rml24Batch(
                **{
                    role: value[start:stop] if value is not None else None
                    for role, value in self.arrays.items()
                }
            )


class Rml24GroupShardSource:
    """Iterate paired IQ/bit group shards emitted by the strict Pickle converter."""

    def __init__(self, manifest_path: Path, *, batch_size: int = 256) -> None:
        if batch_size <= 0:
            raise ValueError("batch_size must be positive")
        from .rml24_pickle_convert import SHARD_SCHEMA_VERSION

        self.manifest_path = Path(manifest_path)
        self.root = self.manifest_path.parent
        self.batch_size = batch_size
        document = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        if document.get("schema_version") != SHARD_SCHEMA_VERSION:
            raise ValueError("unsupported RML24 group-shard manifest")
        if document.get("complete") is not True:
            raise ValueError("RML24 group-shard conversion is incomplete")
        raw_groups = document.get("groups")
        if not isinstance(raw_groups, list) or not raw_groups:
            raise ValueError("group-shard manifest contains no groups")
        groups: list[dict[str, object]] = []
        total = 0
        seen_keys: set[tuple[str, float, float]] = set()
        seen_files: set[str] = set()
        np = _require_numpy()
        for raw in raw_groups:
            if not isinstance(raw, dict) or not isinstance(raw.get("iq"), dict):
                raise ValueError("each benchmark group requires an IQ shard")
            if not isinstance(raw.get("bits"), dict):
                raise ValueError("each benchmark group requires a paired bit shard")
            modulation = raw.get("modulation")
            snr_db = raw.get("snr_db")
            symbol_rate_hz = raw.get("symbol_rate_hz")
            if not isinstance(modulation, str) or not modulation:
                raise ValueError("group modulation must be a nonempty string")
            if (
                not isinstance(snr_db, (int, float))
                or isinstance(snr_db, bool)
                or not math.isfinite(float(snr_db))
            ):
                raise ValueError("group SNR must be finite and numeric")
            if (
                not isinstance(symbol_rate_hz, (int, float))
                or isinstance(symbol_rate_hz, bool)
                or not math.isfinite(float(symbol_rate_hz))
                or float(symbol_rate_hz) <= 0
            ):
                raise ValueError("group symbol rate must be positive and finite")
            group_key = (
                modulation,
                float(snr_db),
                float(symbol_rate_hz),
            )
            if group_key in seen_keys:
                raise ValueError(f"duplicate group-shard key {group_key}")
            seen_keys.add(group_key)
            iq = raw["iq"]
            assert isinstance(iq, dict)
            shape = iq.get("shape")
            if (
                not isinstance(shape, list)
                or len(shape) != 3
                or any(not isinstance(value, int) or value <= 0 for value in shape)
                or 2 not in shape[1:]
            ):
                raise ValueError("IQ shard shape must be (records,2,N) or (records,N,2)")
            bits = raw.get("bits")
            assert isinstance(bits, dict)
            bit_shape = bits.get("shape")
            if (
                not isinstance(bit_shape, list)
                or len(bit_shape) not in {2, 3}
                or any(not isinstance(value, int) or value <= 0 for value in bit_shape)
                or bit_shape[0] != shape[0]
                or (len(bit_shape) == 3 and bit_shape[1] != 1)
            ):
                raise ValueError("bit shard must use (records,L) or (records,1,L)")
            for role, item in (("iq", iq), ("bits", bits)):
                filename = item.get("file")
                dtype_name = item.get("dtype")
                item_shape = item.get("shape")
                payload_bytes = item.get("array_payload_bytes")
                file_bytes = item.get("file_bytes")
                digest = item.get("sha256")
                if not isinstance(filename, str) or not filename:
                    raise ValueError(f"{role} shard filename is missing")
                relative_path = Path(filename)
                if relative_path.is_absolute() or ".." in relative_path.parts or "\\" in filename:
                    raise ValueError("unsafe shard path")
                resolved_path = (self.root / filename).resolve()
                if self.root.resolve() not in resolved_path.parents:
                    raise ValueError("shard path escapes manifest directory")
                if filename in seen_files:
                    raise ValueError(f"shard file is reused by multiple groups: {filename}")
                seen_files.add(filename)
                if not isinstance(dtype_name, str):
                    raise ValueError(f"{role} shard dtype is missing")
                dtype = np.dtype(dtype_name)
                if dtype.hasobject or dtype.kind not in ("fc" if role == "iq" else "biu"):
                    raise ValueError(f"invalid {role} shard dtype {dtype}")
                assert isinstance(item_shape, list)
                expected_payload = math.prod(item_shape) * dtype.itemsize
                if payload_bytes != expected_payload:
                    raise ValueError(f"{role} shard payload size differs from shape/dtype")
                if not isinstance(file_bytes, int) or file_bytes < expected_payload:
                    raise ValueError(f"invalid {role} shard file size")
                if (
                    not isinstance(digest, str)
                    or len(digest) != 64
                    or any(char not in "0123456789abcdef" for char in digest.lower())
                ):
                    raise ValueError(f"invalid {role} shard SHA-256")
            total += shape[0]
            groups.append(raw)
        self.groups = tuple(groups)
        self._record_count = total

    @property
    def record_count(self) -> int:
        return self._record_count

    @property
    def description(self) -> Mapping[str, object]:
        return {
            "source_type": "strict_pickle_to_group_npy_memory_maps",
            "manifest": str(self.manifest_path),
            "record_count": self.record_count,
            "group_count": len(self.groups),
            "batch_size": self.batch_size,
            "whole_array_loaded": False,
            "pickle_executed": False,
            "paired_shards_required": True,
            "manifest_shapes_dtypes_sizes_validated": True,
        }

    def _load(self, item: Mapping[str, object]) -> Any:
        filename = item.get("file")
        if not isinstance(filename, str):
            raise ValueError("shard file is missing")
        path = (self.root / filename).resolve()
        if self.root.resolve() not in path.parents:
            raise ValueError("shard path escapes manifest directory")
        np = _require_numpy()
        array = np.load(path, mmap_mode="r", allow_pickle=False)
        if array.dtype.hasobject:
            raise ValueError("object shard arrays are forbidden")
        expected_shape = tuple(item.get("shape", ()))
        if array.shape != expected_shape:
            raise ValueError(f"shard array shape differs from manifest: {path}")
        expected_dtype = item.get("dtype")
        if not isinstance(expected_dtype, str) or array.dtype != np.dtype(expected_dtype):
            raise ValueError(f"shard array dtype differs from manifest: {path}")
        expected_file_bytes = item.get("file_bytes")
        if not isinstance(expected_file_bytes, int) or path.stat().st_size != expected_file_bytes:
            raise ValueError(f"shard file size differs from manifest: {path}")
        return array

    def iter_batches(self, *, max_records: int | None = None) -> Iterator[Rml24Batch]:
        np = _require_numpy()
        remaining = self.record_count if max_records is None else min(self.record_count, max_records)
        for group in self.groups:
            if remaining <= 0:
                break
            iq_info = group["iq"]
            assert isinstance(iq_info, dict)
            iq = self._load(iq_info)
            raw_bits = group.get("bits")
            bits = self._load(raw_bits) if isinstance(raw_bits, dict) else None
            group_count = min(len(iq), remaining)
            for start in range(0, group_count, self.batch_size):
                stop = min(start + self.batch_size, group_count)
                iq_batch = iq[start:stop]
                bits_batch = bits[start:stop] if bits is not None else None
                if bits_batch is not None and bits_batch.ndim == 3 and bits_batch.shape[1] == 1:
                    bits_batch = bits_batch[:, 0, :]
                count = stop - start
                yield Rml24Batch(
                    iq=iq_batch,
                    modulation=np.full(count, group["modulation"]),
                    bits=bits_batch,
                    bit_lengths=(
                        np.full(count, bits_batch.shape[1], dtype=np.int64)
                        if bits_batch is not None
                        else None
                    ),
                    snr_db=np.full(count, group["snr_db"]),
                    symbol_rate_hz=np.full(count, group["symbol_rate_hz"]),
                )
            remaining -= group_count


def _scalar_for_report(value: object) -> object:
    return value.item() if hasattr(value, "item") else value


def _group_key(batch: Rml24Batch, row: int) -> tuple[str, str, str]:
    def value(array: Any | None) -> str:
        if array is None:
            return "unknown"
        return str(_scalar_for_report(array[row]))

    return value(batch.modulation), value(batch.snr_db), value(batch.symbol_rate_hz)


def _slice_batch(batch: Rml24Batch, indices: Any) -> Rml24Batch:
    np = _require_numpy()
    return Rml24Batch(
        **{
            role: np.asarray(value)[indices] if value is not None else None
            for role, value in {
                "iq": batch.iq,
                "modulation": batch.modulation,
                "bits": batch.bits,
                "bit_lengths": batch.bit_lengths,
                "snr_db": batch.snr_db,
                "symbol_rate_hz": batch.symbol_rate_hz,
            }.items()
        }
    )


def _homogeneous_group_key(batch: Rml24Batch) -> tuple[str, str, str] | None:
    """Return one group key when the complete batch has identical metadata."""

    np = _require_numpy()
    if batch.record_count == 0:
        return None
    for value in (batch.modulation, batch.snr_db, batch.symbol_rate_hz):
        if value is not None:
            flattened = np.asarray(value).reshape(-1)
            if len(flattened) != batch.record_count or not np.all(
                flattened == flattened[0]
            ):
                return None
    return _group_key(batch, 0)


def _bit_error_totals(differences: Any, widths: Any) -> tuple[int, int]:
    """Vectorize the exact per-row bounded-width BER accounting."""

    np = _require_numpy()
    if differences.ndim < 2:
        raise ValueError("bit truth must have records on axis 0")
    positions = np.arange(differences.shape[1], dtype=np.int64)
    valid = (positions[None, :] < widths[:, None]).reshape(
        (len(widths), differences.shape[1]) + (1,) * (differences.ndim - 2)
    )
    return int(np.count_nonzero(differences & valid)), int(np.sum(widths))


def _group_rows(counters: Mapping[tuple[str, str, str], list[int]], kind: str) -> list[dict[str, object]]:
    output: list[dict[str, object]] = []
    for (modulation, snr, rate), values in sorted(counters.items()):
        if kind == "amr":
            correct, total = values
            metric = {"correct": correct, "records": total, "accuracy": correct / total}
        else:
            errors, total = values
            metric = {"bit_errors": errors, "bits": total, "ber": errors / total}
        output.append(
            {
                "modulation": modulation,
                "snr_db": snr,
                "symbol_rate_hz": rate,
                **metric,
            }
        )
    return output


def run_rml24_benchmark(
    source: Rml24BatchSource,
    *,
    amr_predictor: Callable[[Any, Rml24Batch], Any] | None = None,
    bit_demodulator: Callable[[Any, Rml24Batch], Any] | None = None,
    max_records: int | None = None,
) -> dict[str, object]:
    """Evaluate AMR and BER independently while keeping packet yield unavailable."""

    if max_records is not None and max_records <= 0:
        raise ValueError("max_records must be positive")
    np = _require_numpy()
    records_seen = 0
    bit_truth_seen = False
    amr_correct = amr_total = 0
    bit_errors = bit_total = 0
    supported_bit_records = 0
    unsupported_bit_records: dict[str, int] = {}
    amr_groups: dict[tuple[str, str, str], list[int]] = {}
    ber_groups: dict[tuple[str, str, str], list[int]] = {}

    for batch in source.iter_batches(max_records=max_records):
        count = batch.record_count
        if len(batch.iq) != count:
            raise ValueError("batch IQ and modulation arrays differ in length")
        records_seen += count

        if amr_predictor is not None:
            predicted = np.asarray(amr_predictor(batch.iq, batch)).reshape(-1)
            truth = np.asarray(batch.modulation).reshape(-1)
            if predicted.shape != truth.shape:
                raise ValueError("AMR predictor output shape differs from labels")
            matches = predicted == truth
            amr_correct += int(np.count_nonzero(matches))
            amr_total += count
            homogeneous_key = _homogeneous_group_key(batch)
            if homogeneous_key is not None:
                values = amr_groups.setdefault(homogeneous_key, [0, 0])
                values[0] += int(np.count_nonzero(matches))
                values[1] += count
            else:
                for row in range(count):
                    values = amr_groups.setdefault(_group_key(batch, row), [0, 0])
                    values[0] += int(bool(matches[row]))
                    values[1] += 1

        if batch.bits is not None:
            bit_truth_seen = True
            if bit_demodulator is not None:
                supports = getattr(bit_demodulator, "supports", None)
                homogeneous_key = _homogeneous_group_key(batch)
                homogeneous_modulation = (
                    str(_scalar_for_report(batch.modulation[0]))
                    if homogeneous_key is not None
                    else None
                )
                if (
                    homogeneous_modulation is not None
                    and callable(supports)
                    and not bool(supports(batch.modulation[0]))
                ):
                    unsupported_bit_records[homogeneous_modulation] = (
                        unsupported_bit_records.get(homogeneous_modulation, 0) + count
                    )
                    grouped_batches: tuple[tuple[Rml24Batch, tuple[str, str, str] | None], ...] = ()
                elif homogeneous_modulation is not None:
                    # Group-shard batches are already homogeneous.  Preserve the
                    # memory-map slice and avoid an advanced-index copy of IQ.
                    grouped_batches = ((batch, homogeneous_key),)
                else:
                    supported_indices: list[int] = []
                    for row in range(count):
                        modulation = str(_scalar_for_report(batch.modulation[row]))
                        if callable(supports) and not bool(supports(batch.modulation[row])):
                            unsupported_bit_records[modulation] = (
                                unsupported_bit_records.get(modulation, 0) + 1
                            )
                        else:
                            supported_indices.append(row)
                    # Keep each modulation homogeneous so a profile router cannot
                    # accidentally use another row's dataset label.
                    groups: dict[str, list[int]] = {}
                    for row in supported_indices:
                        groups.setdefault(
                            str(_scalar_for_report(batch.modulation[row])), []
                        ).append(row)
                    grouped_batches = tuple(
                        (
                            _slice_batch(batch, np.asarray(indices, dtype=np.int64)),
                            None,
                        )
                        for indices in groups.values()
                    )
                for subbatch, known_group_key in grouped_batches:
                    subcount = subbatch.record_count
                    truth_bits = np.asarray(subbatch.bits)
                    predicted_bits = np.asarray(bit_demodulator(subbatch.iq, subbatch))
                    if predicted_bits.shape != truth_bits.shape:
                        raise ValueError("demodulator bit shape differs from ground truth")
                    if truth_bits.ndim < 2 or truth_bits.shape[0] != subcount:
                        raise ValueError("bit truth must have records on axis 0")
                    differences = predicted_bits != truth_bits
                    widths = np.full(subcount, truth_bits.shape[1], dtype=np.int64)
                    if subbatch.bit_lengths is not None:
                        widths = np.asarray(subbatch.bit_lengths, dtype=np.int64).reshape(-1)
                        if (
                            len(widths) != subcount
                            or np.any(widths < 0)
                            or np.any(widths > truth_bits.shape[1])
                        ):
                            raise ValueError("invalid bit lengths")
                    supported_bit_records += subcount
                    if known_group_key is not None:
                        errors, bits = _bit_error_totals(differences, widths)
                        bit_errors += errors
                        bit_total += bits
                        values = ber_groups.setdefault(known_group_key, [0, 0])
                        values[0] += errors
                        values[1] += bits
                    else:
                        for row in range(subcount):
                            width = int(widths[row])
                            errors = int(np.count_nonzero(differences[row, :width]))
                            bit_errors += errors
                            bit_total += width
                            values = ber_groups.setdefault(
                                _group_key(subbatch, row), [0, 0]
                            )
                            values[0] += errors
                            values[1] += width

    amr: dict[str, object]
    if amr_predictor is None:
        amr = {"status": "not_run", "reason": "no AMR predictor supplied"}
    else:
        amr = {
            "status": "complete",
            "correct": amr_correct,
            "records": amr_total,
            "accuracy": amr_correct / amr_total if amr_total else None,
            "by_modulation_snr_symbol_rate": _group_rows(amr_groups, "amr"),
        }

    ber: dict[str, object]
    if not bit_truth_seen:
        ber = {"status": "unavailable", "reason": "source contains no ground-truth bits"}
    elif bit_demodulator is None:
        ber = {"status": "not_run", "reason": "no bit demodulator supplied"}
    elif supported_bit_records == 0:
        ber = {
            "status": "no_supported_records",
            "reason": "the demodulator does not support any labelled modulation in the source",
            "supported_records": 0,
            "unsupported_records": sum(unsupported_bit_records.values()),
            "unsupported_by_modulation": dict(sorted(unsupported_bit_records.items())),
        }
    else:
        ber = {
            "status": "complete_supported_subset" if unsupported_bit_records else "complete",
            "bit_errors": bit_errors,
            "bits": bit_total,
            "ber": bit_errors / bit_total if bit_total else None,
            "supported_records": supported_bit_records,
            "unsupported_records": sum(unsupported_bit_records.values()),
            "unsupported_by_modulation": dict(sorted(unsupported_bit_records.items())),
            "by_modulation_snr_symbol_rate": _group_rows(ber_groups, "ber"),
        }
        metadata = getattr(bit_demodulator, "benchmark_metadata", None)
        if callable(metadata):
            ber["adapter"] = metadata()

    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "generated_at": datetime.now(UTC).isoformat(),
        "source": dict(source.description),
        "records_seen": records_seen,
        "metrics": {
            "automatic_modulation_recognition": amr,
            "bit_demodulation": ber,
            "validated_telemetry_frames": {
                "status": "unavailable_by_dataset_design",
                "count": None,
                "reason": (
                    "RML24 does not publish record-level AX.25 or CCSDS frame/packet "
                    "ground truth, sync/FEC configuration, or protocol integrity labels."
                ),
            },
        },
        "claims": {
            "packet_yield_comparable_to_satnogs": False,
            "physical_layer_amr_comparable": amr.get("status") == "complete",
            "physical_layer_ber_comparable": ber.get("status")
            in {"complete", "complete_supported_subset"},
        },
    }


def write_rml24_benchmark_report(path: Path, report: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)
