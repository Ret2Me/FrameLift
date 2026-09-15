from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import numpy as np

from telemetry_yield.cw_probe import CwProbeConfig, NarrowbandCwExtractor


CODE = {"S": "...", "O": "---"}


def morse_envelope(text: str, *, sample_rate: int, wpm: int) -> np.ndarray:
    unit_samples = round(sample_rate * 1.2 / wpm)
    values = [0.0] * (7 * unit_samples)
    for char_index, character in enumerate(text):
        code = CODE[character]
        for symbol_index, symbol in enumerate(code):
            values.extend([1.0] * (unit_samples * (3 if symbol == "-" else 1)))
            if symbol_index + 1 < len(code):
                values.extend([0.0] * unit_samples)
        if char_index + 1 < len(text):
            values.extend([0.0] * (3 * unit_samples))
    values.extend([0.0] * (7 * unit_samples))
    return np.asarray(values)


class CwProbeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.sample_rate = 8000
        self.config = CwProbeConfig(
            sample_rate_hz=self.sample_rate,
            preview_fft_size=2048,
            preview_slices_per_window=2,
            carrier_half_bandwidth_hz=150,
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def write_signal(self, name: str, samples: np.ndarray) -> Path:
        path = self.root / name
        values = np.column_stack((samples.real, samples.imag))
        np.clip(np.rint(values), -32768, 32767).astype("<i2").tofile(path)
        return path

    def test_known_morse_is_keyed_candidate_but_validation_stays_pending(self) -> None:
        rng = np.random.default_rng(12)
        envelope = morse_envelope("SOS", sample_rate=self.sample_rate, wpm=20)
        time = np.arange(envelope.size) / self.sample_rate
        noise = rng.normal(0, 30, envelope.size) + 1j * rng.normal(0, 30, envelope.size)
        samples = 9000 * envelope * np.exp(2j * np.pi * 1000 * time) + noise
        result = NarrowbandCwExtractor(self.config).extract(
            self.write_signal("sos.iq", samples)
        )
        self.assertEqual(result.classification, "keyed")
        self.assertTrue(any(item.text == "SOS" for item in result.candidates))
        self.assertTrue(all(item.candidate_validation == "pending" for item in result.candidates))
        self.assertEqual(
            NarrowbandCwExtractor(self.config).capabilities.output_kind,
            "untrusted_text_candidates",
        )

    def test_continuous_carrier_is_not_decoded_as_keyed(self) -> None:
        count = 4 * self.sample_rate
        time = np.arange(count) / self.sample_rate
        samples = 8000 * np.exp(2j * np.pi * 700 * time)
        result = NarrowbandCwExtractor(self.config).extract(
            self.write_signal("carrier.iq", samples)
        )
        self.assertEqual(result.classification, "non_keyed")
        self.assertEqual(result.candidates, ())

    def test_noise_negative_has_no_text_candidate(self) -> None:
        rng = np.random.default_rng(99)
        samples = rng.normal(0, 500, 5 * self.sample_rate) + 1j * rng.normal(
            0, 500, 5 * self.sample_rate
        )
        result = NarrowbandCwExtractor(self.config).extract(
            self.write_signal("noise.iq", samples)
        )
        self.assertNotEqual(result.classification, "keyed")
        self.assertEqual(result.candidates, ())


if __name__ == "__main__":
    unittest.main()
