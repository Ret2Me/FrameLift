from __future__ import annotations

import unittest

from telemetry_yield.canonical import canonical_json, config_hash
from telemetry_yield.models import EffectiveConfig


class CanonicalJsonTests(unittest.TestCase):
    def test_mapping_order_does_not_change_output(self) -> None:
        left = {"z": 1, "a": {"b": 2, "a": 1}}
        right = {"a": {"a": 1, "b": 2}, "z": 1}
        self.assertEqual(canonical_json(left), canonical_json(right))

    def test_non_finite_float_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            canonical_json({"snr": float("nan")})

    def test_non_string_mapping_key_is_rejected(self) -> None:
        with self.assertRaises(TypeError):
            canonical_json({1: "value"})


class ConfigHashTests(unittest.TestCase):
    def setUp(self) -> None:
        self.base = {
            "protocol_id": "afsk1200_ax25",
            "decoder": "direwolf",
            "decoder_version": "1.7",
            "seed": 7,
            "config": {"baud": 1200, "agc": "slow"},
        }

    def test_hash_is_sha256_and_stable_across_config_key_order(self) -> None:
        left = config_hash(**self.base)
        reordered = dict(self.base)
        reordered["config"] = {"agc": "slow", "baud": 1200}
        right = config_hash(**reordered)
        self.assertEqual(left, right)
        self.assertEqual(len(left), 64)

    def test_every_effective_field_changes_hash(self) -> None:
        baseline = config_hash(**self.base)
        variants = (
            {"protocol_id": "fsk9600_g3ruh"},
            {"decoder": "gr_satellites"},
            {"decoder_version": "1.8"},
            {"seed": 8},
            {"config": {"baud": 1201, "agc": "slow"}},
        )
        for changed in variants:
            with self.subTest(changed=changed):
                candidate = self.base | changed
                self.assertNotEqual(baseline, config_hash(**candidate))

    def test_model_and_named_calls_match(self) -> None:
        model = EffectiveConfig(**self.base)
        self.assertEqual(config_hash(model), config_hash(**self.base))

    def test_partial_named_call_is_rejected(self) -> None:
        with self.assertRaises(TypeError):
            config_hash(protocol_id="ax25")


if __name__ == "__main__":
    unittest.main()

