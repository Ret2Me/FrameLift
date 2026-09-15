from __future__ import annotations

import hashlib
import json
from pathlib import Path
import struct
from tempfile import TemporaryDirectory
import unittest

from telemetry_yield.raw_iq_manifest import (
    build_blind_freeze,
    validate_raw_iq_manifest,
    write_immutable_blind_freeze,
)


def sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


class RawIQManifestTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.raw = self.root / "capture.raw"
        self.raw_bytes = struct.pack("<8h", 10, -10, 20, -20, 30, -30, 40, -40)
        self.raw.write_bytes(self.raw_bytes)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def manifest(self, **updates: object) -> dict[str, object]:
        value: dict[str, object] = {
            "schema_version": "raw-iq-input-v1",
            "source": {
                "path": self.raw.name,
                "sha256": sha256(self.raw_bytes),
                "size_bytes": len(self.raw_bytes),
            },
            "encoding": {
                "dtype": "int16_le",
                "interleaving": "iq",
                "q_sign": 1,
                "scalar_zero": 0,
                "iq_scale": 32768,
                "byte_offset": 0,
                "complex_sample_count": 4,
                "evidence": "recorder format declaration and byte-level probe",
            },
            "capture": {
                "sample_rate_hz": 57_600,
                "center_frequency_hz": 437_500_000,
                "start_utc": "2026-09-01T12:00:00Z",
                "clock_reference": "GPS-disciplined station clock",
                "evidence": "operator capture log row 17",
            },
            "doppler": {
                "state": "pre_correction",
                "evidence": "recorder tap before Doppler rotator",
            },
            "satellite": {
                "norad_id": 99999,
                "evidence": "scheduled pass plus TLE ridge",
            },
            "signal": {
                "modulation": "GFSK/G3RUH",
                "baud": 9_600,
                "evidence": "licensed transmitter specification",
            },
            "clipping": {
                "lower_endpoint": -32_768,
                "upper_endpoint": 32_767,
                "endpoint_source": "dtype_limits",
                "max_endpoint_fraction_for_unclipped_claim": 0.001,
                "if_exceeded": "require_frozen_ab",
                "ab": {"enabled": False},
            },
        }
        value.update(updates)
        return value

    @staticmethod
    def freeze(manifest: dict[str, object]) -> dict[str, object]:
        return build_blind_freeze(
            manifest,
            frozen_at_utc="2026-09-01T12:30:00Z",
            candidate_config_sha256="a" * 64,
            code_sha256="b" * 64,
            parameter_grid_sha256="c" * 64,
            reference_commitment_sha256="d" * 64,
            acceptance_rule="full CRC plus complete byte equality only",
            negative_control_rule="zero CRC-valid frames in two frozen windows",
            deterministic_repeats=2,
        )

    def test_complete_contract_and_freeze_are_ready(self) -> None:
        manifest = self.manifest()
        result = validate_raw_iq_manifest(
            manifest,
            base_dir=self.root,
            blind_freeze=self.freeze(manifest),
        )
        self.assertTrue(result.ready_for_decode)
        self.assertTrue(result.ready_for_blind_test)
        self.assertEqual(result.clipping.endpoint_fraction, 0.0)  # type: ignore[union-attr]

    def test_no_parameter_is_inferred_when_contract_is_missing(self) -> None:
        manifest = self.manifest(
            capture={},
            doppler={"state": "unknown", "evidence": "archive is ambiguous"},
            signal={"modulation": "unknown", "baud": None, "evidence": "none"},
        )
        result = validate_raw_iq_manifest(manifest, base_dir=self.root)
        self.assertFalse(result.ready_for_decode)
        self.assertIn("missing_or_invalid_sample_rate_hz", result.blockers)
        self.assertIn("missing_or_invalid_center_frequency_hz", result.blockers)
        self.assertIn("missing_or_invalid_start_utc", result.blockers)
        self.assertIn("unknown_doppler_state", result.blockers)
        self.assertIn("missing_or_unknown_modulation", result.blockers)
        self.assertIn("missing_or_invalid_baud", result.blockers)

    def test_shape_and_content_hash_are_bound(self) -> None:
        manifest = self.manifest()
        manifest["source"]["sha256"] = "0" * 64  # type: ignore[index]
        manifest["encoding"]["complex_sample_count"] = 5  # type: ignore[index]
        result = validate_raw_iq_manifest(manifest, base_dir=self.root)
        self.assertIn("source_sha256_mismatch", result.blockers)
        self.assertIn("source_shape_size_mismatch", result.blockers)

    def test_clipping_exceedance_requires_enabled_identical_ab(self) -> None:
        self.raw_bytes = struct.pack(
            "<8h", 32_767, -32_768, 20, -20, 30, -30, 40, -40
        )
        self.raw.write_bytes(self.raw_bytes)
        manifest = self.manifest()
        result = validate_raw_iq_manifest(manifest, base_dir=self.root)
        self.assertFalse(result.ready_for_decode)
        self.assertIn("clipping_requires_enabled_ab", result.blockers)
        self.assertEqual(result.clipping.endpoint_fraction, 0.25)  # type: ignore[union-attr]

    def test_identical_ab_window_and_shared_downstream_config_pass(self) -> None:
        self.raw_bytes = struct.pack(
            "<8h", 32_767, -32_768, 20, -20, 30, -30, 40, -40
        )
        self.raw.write_bytes(self.raw_bytes)
        digest = sha256(self.raw_bytes)
        manifest = self.manifest()
        manifest["clipping"]["ab"] = {  # type: ignore[index]
            "enabled": True,
            "input_sha256_a": digest,
            "input_sha256_b": digest,
            "window_start_complex_sample": 0,
            "window_complex_samples": 4,
            "window_sha256_a": digest,
            "window_sha256_b": digest,
            "branch_a": "identity",
            "branch_b": "projected_bandlimited_declipping",
            "branch_b_config_sha256": "e" * 64,
            "shared_downstream_config_sha256": "f" * 64,
            "acceptance_rule": "compare frozen yield metric; never choose per frame",
        }
        result = validate_raw_iq_manifest(manifest, base_dir=self.root)
        self.assertTrue(result.ready_for_decode)
        self.assertEqual(result.clipping.status, "exceeds_declared_limit")  # type: ignore[union-attr]
        self.assertEqual(result.clipping.ab_window_sha256, digest)  # type: ignore[union-attr]

    def test_ab_input_or_window_mismatch_fails_closed(self) -> None:
        manifest = self.manifest()
        manifest["clipping"]["ab"] = {  # type: ignore[index]
            "enabled": True,
            "input_sha256_a": "0" * 64,
            "input_sha256_b": "1" * 64,
            "window_start_complex_sample": 0,
            "window_complex_samples": 4,
            "window_sha256_a": "2" * 64,
            "window_sha256_b": "3" * 64,
            "branch_a": "identity",
            "branch_b": "declipping",
            "branch_b_config_sha256": "4" * 64,
            "shared_downstream_config_sha256": "5" * 64,
            "acceptance_rule": "frozen rule",
        }
        result = validate_raw_iq_manifest(manifest, base_dir=self.root)
        self.assertIn("ab_inputs_not_identical_to_source", result.blockers)
        self.assertIn("ab_window_hash_mismatch", result.blockers)

    def test_tampered_freeze_is_not_blind_ready(self) -> None:
        manifest = self.manifest()
        freeze = self.freeze(manifest)
        freeze["position_blind"] = False
        freeze["input_manifest_sha256"] = "0" * 64
        result = validate_raw_iq_manifest(
            manifest, base_dir=self.root, blind_freeze=freeze
        )
        self.assertTrue(result.ready_for_decode)
        self.assertFalse(result.ready_for_blind_test)
        self.assertIn(
            "blind_freeze_manifest_hash_mismatch", result.blind_freeze_blockers
        )
        self.assertIn(
            "blind_test_must_be_position_blind", result.blind_freeze_blockers
        )

    def test_freeze_writer_is_idempotent_and_immutable(self) -> None:
        manifest = self.manifest()
        freeze = self.freeze(manifest)
        path = self.root / "freeze.json"
        first = write_immutable_blind_freeze(path, freeze, manifest=manifest)
        second = write_immutable_blind_freeze(path, freeze, manifest=manifest)
        self.assertEqual(first, second)
        self.assertEqual(json.loads(path.read_text()), freeze)
        changed = dict(freeze)
        changed["code_sha256"] = "9" * 64
        with self.assertRaises(FileExistsError):
            write_immutable_blind_freeze(path, changed, manifest=manifest)


if __name__ == "__main__":
    unittest.main()
