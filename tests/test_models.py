from __future__ import annotations

import unittest

from telemetry_yield.models import EffectiveConfig, FrameRecord, LicenseMetadata


class EffectiveConfigTests(unittest.TestCase):
    def test_valid_config(self) -> None:
        value = EffectiveConfig("ax25", "direwolf", "1.7", 42, {"baud": 1200})
        self.assertEqual(value.seed, 42)

    def test_empty_identity_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            EffectiveConfig("", "direwolf", "1.7", 42, {})

    def test_boolean_seed_is_rejected(self) -> None:
        with self.assertRaises(TypeError):
            EffectiveConfig("ax25", "direwolf", "1.7", True, {})


class RecordValidationTests(unittest.TestCase):
    def test_frame_requires_boolean_crc_state(self) -> None:
        with self.assertRaises(TypeError):
            FrameRecord("event", "digest", 1)

    def test_license_requires_boolean_verification(self) -> None:
        with self.assertRaises(TypeError):
            LicenseMetadata("source", "CC-BY-4.0", True, 1)


if __name__ == "__main__":
    unittest.main()
