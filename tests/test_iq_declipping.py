from __future__ import annotations

import unittest

import numpy as np

from telemetry_yield.iq_declipping import projected_bandlimited_declipping


class IQDeclippingTests(unittest.TestCase):
    def test_constraints_determinism_and_synthetic_improvement(self) -> None:
        count = 4_096
        index = np.arange(count, dtype=np.float64)
        original = 44_000.0 * np.exp(2j * np.pi * 3_100.0 * index / 57_600.0)
        components = np.column_stack((original.real, original.imag))
        observed = np.clip(np.rint(components), -32_768, 32_767).astype("<i2")
        first = projected_bandlimited_declipping(
            observed,
            iteration_checkpoints=(5, 20),
        )
        second = projected_bandlimited_declipping(
            observed,
            iteration_checkpoints=(5, 20),
        )
        reconstructed, metrics = first[20]
        self.assertEqual(reconstructed.tobytes(), second[20][0].tobytes())
        positive = observed == 32_767
        negative = observed == -32_768
        unclipped = ~(positive | negative)
        self.assertTrue(np.array_equal(reconstructed[unclipped], observed[unclipped]))
        self.assertTrue(np.all(reconstructed[positive] >= 32_767))
        self.assertTrue(np.all(reconstructed[negative] <= -32_768))
        clipped = positive | negative
        raw_error = np.mean((observed.astype(np.float64)[clipped] - components[clipped]) ** 2)
        reconstructed_error = np.mean((reconstructed[clipped] - components[clipped]) ** 2)
        self.assertLess(reconstructed_error, raw_error)
        self.assertEqual(metrics.maximum_unclipped_constraint_error, 0.0)

    def test_input_validation(self) -> None:
        with self.assertRaises(ValueError):
            projected_bandlimited_declipping(np.zeros((255, 2), dtype=np.int16))
        with self.assertRaises(ValueError):
            projected_bandlimited_declipping(
                np.zeros((256, 2), dtype=np.int16),
                bandlimit_hz=28_800,
            )
        with self.assertRaises(ValueError):
            projected_bandlimited_declipping(
                np.zeros((256, 2), dtype=np.int16),
                iteration_checkpoints=(0,),
            )


if __name__ == "__main__":
    unittest.main()
