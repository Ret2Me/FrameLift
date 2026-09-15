from __future__ import annotations

import hashlib
import importlib.util
import unittest

import numpy as np

from telemetry_yield.tag15_frontend import (
    FLOWGRAPHS_FSK_SHA256,
    Tag15FrontendSpec,
    deterministic_moving_average_ff,
    read_tag15_c16le_window,
    run_tag15_clock,
    run_tag15_preclock,
    tag15_filter_taps,
)


@unittest.skipUnless(
    importlib.util.find_spec("gnuradio") is not None,
    "GNU Radio runtime is not visible to this Python interpreter",
)
class Tag15FrontendTests(unittest.TestCase):
    def test_frozen_moving_average_work_partitions(self) -> None:
        values = np.arange(1, 9, dtype=np.float32)
        actual = deterministic_moving_average_ff(
            values,
            length=4,
            scale=0.25,
            max_iter=3,
        )
        expected = np.asarray(
            [0.25, 0.75, 1.5, 2.5, 3.5, 4.5, 5.5, 6.5],
            dtype=np.float32,
        )
        self.assertTrue(np.array_equal(actual, expected))

    def test_frozen_flowgraph_and_generated_taps(self) -> None:
        spec = Tag15FrontendSpec()
        relaxed, channel = tag15_filter_taps(spec)
        self.assertEqual(
            FLOWGRAPHS_FSK_SHA256,
            "dda3c64161bdffacbf516a86e81e87fce6294815ec22c24770e747d0b9f38032",
        )
        self.assertEqual(len(relaxed), 29)
        self.assertEqual(len(channel), 115)
        self.assertEqual(
            hashlib.sha256(relaxed.tobytes()).hexdigest(),
            "6d4e8347f4420d680b8734f41af9f83325493616fe8699a5b432b379c41d73f8",
        )
        self.assertEqual(
            hashlib.sha256(channel.tobytes()).hexdigest(),
            "661fdbacb4984a1ec604c302ba9e3d7d8ac797da0d27b81d77da75dbaa936e2b",
        )

    def test_serialized_frontend_and_clock_are_byte_deterministic(self) -> None:
        count = 12_288
        index = np.arange(count, dtype=np.float32)
        phase = 0.19 * index + 0.21 * np.sin(index / 17.0)
        iq = np.asarray(np.exp(1j * phase), dtype=np.complex64)
        first, first_metrics = run_tag15_preclock(iq)
        second, second_metrics = run_tag15_preclock(iq)
        self.assertEqual(first.tobytes(), second.tobytes())
        self.assertEqual(first_metrics, second_metrics)
        self.assertEqual(first.size, count // 3)
        first_bits = run_tag15_clock(first)
        second_bits = run_tag15_clock(first)
        self.assertEqual(first_bits, second_bits)

    def test_input_validation(self) -> None:
        with self.assertRaises(ValueError):
            run_tag15_preclock(np.zeros(4_095, dtype=np.complex64))
        with self.assertRaises(ValueError):
            run_tag15_preclock(np.zeros(4_097, dtype=np.complex64))
        with self.assertRaises(ValueError):
            run_tag15_clock(np.zeros(255, dtype=np.float32))
        with self.assertRaises(ValueError):
            read_tag15_c16le_window(
                "/definitely/missing",
                start_seconds=-1.0,
                duration_seconds=1.0,
            )


if __name__ == "__main__":
    unittest.main()
