from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

from telemetry_yield.sigmf_validation import (
    acquire_gps_l1,
    gps_ca_code,
    sampled_gps_ca_code,
    validate_sigmf_dataset,
)


class GPSCodeTests(unittest.TestCase):
    def test_ca_code_has_expected_period_and_balance(self) -> None:
        code = gps_ca_code(1)
        self.assertEqual(code.shape, (1023,))
        self.assertEqual(set(code.tolist()), {-1, 1})
        self.assertEqual(abs(int(code.sum())), 1)
        spectrum = np.fft.fft(code)
        autocorrelation = np.rint(np.fft.ifft(spectrum * spectrum.conj()).real)
        self.assertEqual(int(autocorrelation[0]), 1023)
        self.assertLess(int(np.max(autocorrelation[1:])), 1023)

    def test_rejects_unknown_prn(self) -> None:
        with self.assertRaisesRegex(ValueError, "1..32"):
            gps_ca_code(33)

    def test_acquires_synthetic_prn_and_doppler(self) -> None:
        sample_rate = 4_000_000.0
        code = sampled_gps_ca_code(7, sample_rate)
        phase = 731
        doppler_hz = 1500
        time = np.arange(code.size) / sample_rate
        one_ms = np.roll(code, phase) * np.exp(2j * np.pi * doppler_hz * time)
        random = np.random.default_rng(20260831)
        samples = np.concatenate([one_ms] * 5)
        samples += 0.7 * (
            random.standard_normal(samples.size)
            + 1j * random.standard_normal(samples.size)
        )
        detections = acquire_gps_l1(
            samples,
            sample_rate_hz=sample_rate,
            prns=(7, 8),
            threshold=100.0,
        )
        self.assertEqual([item.prn for item in detections], [7])
        self.assertEqual(detections[0].doppler_hz, 1500.0)
        self.assertGreater(detections[0].peak_to_median, 100.0)


class SigMFValidationTests(unittest.TestCase):
    def test_rejects_checksum_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary) / "bad"
            base.with_suffix(".sigmf-data").write_bytes(b"\x00" * 16)
            base.with_suffix(".sigmf-meta").write_text(
                json.dumps(
                    {
                        "global": {
                            "core:datatype": "cf32_le",
                            "core:sample_rate": 1.0,
                            "core:sha512": "0" * 128,
                            "core:version": "1.0.0",
                        },
                        "captures": [{"core:sample_start": 0}],
                        "annotations": [],
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "SHA-512"):
                validate_sigmf_dataset(base.with_suffix(".sigmf-meta"))


if __name__ == "__main__":
    unittest.main()
