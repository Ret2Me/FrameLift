import json
import hashlib
import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

import numpy as np

from telemetry_yield.cli import build_parser, main
from telemetry_yield.rml24_archive import (
    inventory_rml24_hdf5,
    write_rml24_hdf5_inventory,
)
from telemetry_yield.rml24_benchmark import HDF5_CLASS_MAP_SCHEMA_VERSION


FIXTURES = Path(__file__).parent / "fixtures"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_hdf_cli_fixture(root: Path) -> tuple[Path, Path, Path, Path]:
    import h5py

    hdf5 = root / "RML24_IQdata.h5"
    with h5py.File(hdf5, "w") as handle:
        handle.create_dataset("IQ_data", data=np.zeros((1, 2048, 2), dtype=np.float32))
        handle.create_dataset("class", data=np.array([4], dtype=np.int32))
        handle.create_dataset("Bit_data", data=np.zeros((1, 14000), dtype=np.int32))
        handle.create_dataset("Bit_len", data=np.array([14000], dtype=np.int32))
        handle.create_dataset("snr", data=np.array([0], dtype=np.int32))
        handle.create_dataset("symbol_rate", data=np.array([100_000], dtype=np.int32))
    inventory = root / "inventory.json"
    write_rml24_hdf5_inventory(inventory, inventory_rml24_hdf5(hdf5, batch_size=1))
    extraction = root / "extraction.json"
    extraction.write_text(
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
        json.dumps({"mapping": {"4": "UNSUPPORTED"}}), encoding="utf-8"
    )
    class_map = root / "class-map.json"
    class_map.write_text(
        json.dumps(
            {
                "schema_version": HDF5_CLASS_MAP_SCHEMA_VERSION,
                "mapping": {"4": "UNSUPPORTED"},
                "provenance": {
                    "verified": True,
                    "method": "cross_format_record_identity",
                    "source_artifact": evidence.name,
                    "source_sha256": _sha256(evidence),
                    "inventory_report_sha256": _sha256(inventory),
                    "extraction_report_sha256": _sha256(extraction),
                    "hdf5_sha256": _sha256(hdf5),
                },
            }
        ),
        encoding="utf-8",
    )
    return hdf5, inventory, extraction, class_map


def _write_group_cli_fixture(root: Path) -> Path:
    rng = np.random.default_rng(211)
    bits = rng.integers(0, 2, size=205, dtype=np.int32)
    symbols = 1.0 - 2.0 * bits.astype(np.float32)
    signal = np.repeat(symbols, 10)[:2048]
    iq = np.stack((signal, np.zeros_like(signal)))[None, ...].astype(np.float32)
    bit_array = bits[None, None, ...]
    iq_path = root / "iq.npy"
    bit_path = root / "bits.npy"
    np.save(iq_path, iq, allow_pickle=False)
    np.save(bit_path, bit_array, allow_pickle=False)
    manifest = root / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": "rml24-group-shards-v1",
                "complete": True,
                "groups": [
                    {
                        "modulation": "BPSK",
                        "snr_db": 20,
                        "symbol_rate_hz": 100_000,
                        "iq": {
                            "file": iq_path.name,
                            "shape": list(iq.shape),
                            "dtype": iq.dtype.str,
                            "array_payload_bytes": iq.nbytes,
                            "file_bytes": iq_path.stat().st_size,
                            "sha256": _sha256(iq_path),
                        },
                        "bits": {
                            "file": bit_path.name,
                            "shape": list(bit_array.shape),
                            "dtype": bit_array.dtype.str,
                            "array_payload_bytes": bit_array.nbytes,
                            "file_bytes": bit_path.stat().st_size,
                            "sha256": _sha256(bit_path),
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return manifest


class CLITests(unittest.TestCase):
    def test_rml24_streaming_commands_are_registered(self) -> None:
        extract = build_parser().parse_args(
            [
                "extract-rml24-hdf5",
                "archive.zip",
                "output.h5",
                "--plan",
                "plan.json",
            ]
        )
        self.assertEqual(extract.command, "extract-rml24-hdf5")
        inventory = build_parser().parse_args(
            [
                "inventory-rml24-hdf5",
                "output.h5",
                "--output",
                "inventory.json",
                "--batch-size",
                "1024",
            ]
        )
        self.assertEqual(inventory.batch_size, 1024)
        pickle_conversion = build_parser().parse_args(
            [
                "convert-rml24-pickle",
                "dataset.zip",
                "shards",
                "--plan",
                "plan.json",
            ]
        )
        self.assertEqual(pickle_conversion.max_array_mib, 64)
        benchmark = build_parser().parse_args(
            [
                "benchmark-rml24-physical",
                "shards/manifest.json",
                "--output",
                "report.json",
            ]
        )
        self.assertEqual(benchmark.max_shift_bits, 8)
        self.assertEqual(benchmark.recovery_version, "legacy")
        versioned_benchmark = build_parser().parse_args(
            [
                "benchmark-rml24-physical",
                "shards/manifest.json",
                "--output",
                "report.json",
                "--recovery-version",
                "carrier_timing_v2",
            ]
        )
        self.assertEqual(versioned_benchmark.recovery_version, "carrier_timing_v2")
        hdf5_benchmark = build_parser().parse_args(
            [
                "benchmark-rml24-hdf5-physical",
                "dataset.h5",
                "--inventory-report",
                "inventory.json",
                "--extraction-report",
                "extraction.json",
                "--class-map",
                "class-map.json",
                "--output",
                "report.json",
            ]
        )
        self.assertEqual(hdf5_benchmark.batch_size, 128)

    def test_rml24_hdf5_physical_cli_streams_verified_fixture(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            hdf5, inventory, extraction, class_map = _write_hdf_cli_fixture(root)
            output = root / "report.json"
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                self.assertEqual(
                    main(
                        [
                            "benchmark-rml24-hdf5-physical",
                            str(hdf5),
                            "--inventory-report",
                            str(inventory),
                            "--extraction-report",
                            str(extraction),
                            "--class-map",
                            str(class_map),
                            "--output",
                            str(output),
                            "--batch-size",
                            "1",
                            "--max-records",
                            "1",
                        ]
                    ),
                    0,
                )
            report = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(report["records_seen"], 1)
            self.assertEqual(
                report["metrics"]["bit_demodulation"]["status"],
                "no_supported_records",
            )
            self.assertFalse(report["source"]["whole_array_loaded"])
            self.assertIn("BER status=no_supported_records", stdout.getvalue())

    def test_rml24_versioned_recovery_is_recorded_in_report(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = _write_group_cli_fixture(root)
            output = root / "report.json"
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                self.assertEqual(
                    main(
                        [
                            "benchmark-rml24-physical",
                            str(manifest),
                            "--output",
                            str(output),
                            "--batch-size",
                            "1",
                            "--recovery-version",
                            "carrier_timing_v2",
                        ]
                    ),
                    0,
                )
            report = json.loads(output.read_text(encoding="utf-8"))
            adapter = report["metrics"]["bit_demodulation"]["adapter"]
            self.assertEqual(adapter["recovery_version"], "carrier_timing_v2")
            self.assertEqual(adapter["name"], "rml24-carrier-timing-v2-v1")
            self.assertEqual(adapter["alignment_policy"]["max_shift_bits"], 8)
            self.assertIn("recovery=carrier_timing_v2", stdout.getvalue())

    def test_rml24_physical_cli_refuses_incomplete_shards(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = root / "manifest.json"
            manifest.write_text(
                json.dumps(
                    {
                        "schema_version": "rml24-group-shards-v1",
                        "complete": False,
                        "groups": [],
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "conversion is incomplete"):
                main(
                    [
                        "benchmark-rml24-physical",
                        str(manifest),
                        "--output",
                        str(root / "report.json"),
                    ]
                )

    def test_capabilities_are_machine_readable_and_honest_about_scope(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "capabilities.json"
            self.assertEqual(main(["capabilities", "--output", str(output)]), 0)
            payload = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(payload["schema_version"], "receiver-capabilities-v1")
            self.assertFalse(payload["scope"]["satnogs_is_required"])
            self.assertFalse(payload["scope"]["all_protocols_implemented"])
            demodulators = payload["capabilities"]["demodulators"]
            self.assertEqual(
                demodulators["phase_fsk"]["modulation_families"],
                ["fsk", "gfsk", "gmsk"],
            )

    def test_inventory_archive_command(self) -> None:
        catalogue = {
            "observation_count": 1,
            "observations": [
                {
                    "observation_id": 7,
                    "mode": "BPSK",
                    "links": {"iq": "https://example.invalid/observation_7.iq"},
                    "artifacts": {},
                    "frames": [],
                    "frame_count": 0,
                }
            ],
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "catalogue.json"
            output = root / "inventory.json"
            source.write_text(json.dumps(catalogue), encoding="utf-8")
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                self.assertEqual(
                    main(
                        [
                            "inventory-archive",
                            str(source),
                            "--output",
                            str(output),
                        ]
                    ),
                    0,
                )
            payload = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(payload["summary"]["iq_available_count"], 1)
            self.assertEqual(payload["observations"][0]["status"], "needs_plugin")
            self.assertIn("1 observations, 1 with IQ", stdout.getvalue())

    def test_profiles_command_builds_a_registry(self) -> None:
        profiles = FIXTURES.parent / "fixtures" / "satyaml_registry"
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "profiles.json"
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                self.assertEqual(
                    main(["profiles", str(profiles), "--output", str(output)]),
                    0,
                )
            payload = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(payload["summary"]["profile_count"], 4)
            self.assertGreater(payload["summary"]["diagnostic_count"], 0)
            self.assertFalse(
                payload["summary"]["executable_compatibility_verified"]
            )
            self.assertIn("4 profiles", stdout.getvalue())

    def test_scrape_and_convert_fixture(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            index = root / "index.json"
            self.assertEqual(
                main(
                    [
                        "scrape-camras",
                        str(FIXTURES / "camras_index.html"),
                        "--output",
                        str(index),
                    ]
                ),
                0,
            )
            self.assertEqual(len(json.loads(index.read_text(encoding="utf-8"))), 2)

            output = root / "converted" / "sample"
            self.assertEqual(
                main(
                    [
                        "convert-camras",
                        str(FIXTURES / "synthetic_c16le.raw"),
                        str(output),
                        "--sample-rate",
                        "48000",
                        "--sample-rate-basis",
                        "fixture contract",
                        "--doppler-state",
                        "unknown",
                        "--frequency",
                        "436650000",
                        "--start-utc",
                        "2022-01-14T10:20:46Z",
                        "--observation-id",
                        "5293127",
                        "--confirm-ci16-le",
                    ]
                ),
                0,
            )
            metadata = json.loads(
                output.with_suffix(".sigmf-meta").read_text(encoding="utf-8")
            )
            self.assertEqual(
                metadata["global"]["telemetry_yield:observation_id"], 5293127
            )


if __name__ == "__main__":
    unittest.main()
