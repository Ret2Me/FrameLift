from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import numpy as np

from telemetry_yield.signal_triage import (
    SignalTriageConfig,
    rank_signal_triage,
    triage_ci16le_file,
)


class SignalTriageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.config = SignalTriageConfig(
            sample_rate_hz=1024,
            fft_size=256,
            fft_slices_per_window=2,
            active_fft_ratio=20,
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def write_iq(self, name: str, values: np.ndarray) -> Path:
        path = self.root / name
        values.astype("<i2").tofile(path)
        return path

    def test_tone_windows_rank_above_seeded_noise(self) -> None:
        rng = np.random.default_rng(123)
        noise = rng.integers(-500, 501, size=(4 * 1024, 2), dtype=np.int16)
        time = np.arange(4 * 1024)
        tone = np.column_stack(
            (
                np.rint(6000 * np.cos(2 * np.pi * 128 * time / 1024)),
                np.rint(6000 * np.sin(2 * np.pi * 128 * time / 1024)),
            )
        )
        noise_metrics = triage_ci16le_file(
            self.write_iq("noise.iq", noise), config=self.config
        )
        tone_metrics = triage_ci16le_file(
            self.write_iq("tone.iq", tone), config=self.config
        )
        self.assertGreater(tone_metrics.p90_fft_peak_to_median_ratio, 1000)
        self.assertGreater(tone_metrics.ranking_score, noise_metrics.ranking_score)
        ranked = rank_signal_triage([(2, noise_metrics), (1, tone_metrics)])
        self.assertEqual([item[0] for item in ranked], [1, 2])

    def test_duration_window_count_hash_and_frequency_are_explicit(self) -> None:
        rng = np.random.default_rng(4)
        values = rng.integers(-100, 101, size=(2 * 1024 + 100, 2), dtype=np.int16)
        path = self.write_iq("capture.iq", values)
        metrics = triage_ci16le_file(path, config=self.config)
        self.assertEqual(metrics.file_size_bytes, path.stat().st_size)
        self.assertEqual(metrics.complex_sample_count, 2148)
        self.assertEqual(metrics.one_second_window_count, 2)
        self.assertEqual(metrics.trailing_sample_count, 100)
        self.assertAlmostEqual(metrics.duration_seconds, 2148 / 1024)
        self.assertEqual(len(metrics.sha256), 64)
        self.assertLessEqual(abs(metrics.median_peak_frequency_offset_hz), 512)

    def test_invalid_layout_and_duplicate_ranking_ids_fail_closed(self) -> None:
        bad = self.root / "bad.iq"
        bad.write_bytes(b"abc")
        with self.assertRaisesRegex(ValueError, "multiple of four"):
            triage_ci16le_file(bad, config=self.config)

        rng = np.random.default_rng(8)
        values = rng.integers(-10, 11, size=(1024, 2), dtype=np.int16)
        metrics = triage_ci16le_file(
            self.write_iq("short.iq", values), config=self.config
        )
        with self.assertRaisesRegex(ValueError, "unique"):
            rank_signal_triage([(1, metrics), (1, metrics)])


if __name__ == "__main__":
    unittest.main()
