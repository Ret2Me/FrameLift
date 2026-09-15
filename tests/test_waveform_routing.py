from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import numpy as np

from telemetry_yield.waveform_routing import (
    WaveformRoutingConfig,
    route_ci16le_waveform,
)


class WaveformRoutingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.config = WaveformRoutingConfig(
            sample_rate_hz=57_600,
            strongest_window_count=2,
            psd_fft_size=32_768,
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def write_complex(self, name: str, samples: np.ndarray) -> Path:
        scale = 12_000 / max(float(np.max(np.abs(samples))), 1e-12)
        values = np.column_stack((samples.real * scale, samples.imag * scale))
        path = self.root / name
        np.rint(values).astype("<i2").tofile(path)
        return path

    def test_narrow_constant_carrier_routes_away_from_g3ruh(self) -> None:
        count = 2 * 57_600
        time = np.arange(count) / 57_600
        carrier = np.exp(2j * np.pi * 1200 * time)
        result = route_ci16le_waveform(
            self.write_complex("cw.iq", carrier), config=self.config
        )
        self.assertEqual(result.primary_routing, "continuous_carrier_or_cw_like")
        self.assertLess(result.occupied_bandwidth_99_hz, 1000)
        self.assertFalse(result.g3ruh_9600_probe_physically_sensible)

    def test_rectangular_9600_fsk_exposes_clock_candidate(self) -> None:
        rng = np.random.default_rng(44)
        symbols = rng.choice(np.asarray([-1.0, 1.0]), size=2 * 9600)
        deviation = np.repeat(symbols, 6) * 5000.0
        phase = np.cumsum(2 * np.pi * deviation / 57_600)
        samples = np.exp(1j * phase)
        result = route_ci16le_waveform(
            self.write_complex("fsk.iq", samples), config=self.config
        )
        candidates = {item.baud: item for item in result.symbol_rate_candidates}
        self.assertIn(9600, candidates)
        self.assertGreater(candidates[9600].clock_line_snr_db, 3.0)
        self.assertGreater(result.occupied_bandwidth_99_hz, 4000)
        self.assertTrue(result.g3ruh_9600_probe_physically_sensible)

    def test_invalid_size_fails_closed(self) -> None:
        path = self.root / "bad.iq"
        path.write_bytes(b"123")
        with self.assertRaisesRegex(ValueError, "multiple of four"):
            route_ci16le_waveform(path, config=self.config)


if __name__ == "__main__":
    unittest.main()
