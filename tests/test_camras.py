import hashlib
import json
import struct
import tempfile
import unittest
from pathlib import Path

from telemetry_yield.camras import (
    CAMRAS_SIGMF_DATATYPE,
    CamrasIndexError,
    TruncatedC16LEError,
    c16le_rms,
    c16le_sample_count,
    convert_camras_raw_to_sigmf,
    iter_c16le_chunks,
    parse_camras_index,
)


FIXTURES = Path(__file__).parent / "fixtures"


class CamrasIndexTests(unittest.TestCase):
    def test_parses_regular_camras_table(self):
        entries = parse_camras_index(
            (FIXTURES / "camras_index.html").read_bytes()
        )

        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0].observation_id, 10138296)
        self.assertEqual(entries[0].size_bytes_estimate, 127_900_000)
        self.assertEqual(entries[0].status, "good")
        self.assertEqual(
            entries[0].recording_url,
            "https://data.camras.nl/satnogs/iq_10138296.raw",
        )
        self.assertEqual(
            entries[0].related_urls,
            ("https://data.camras.nl/satnogs/10138296.txt",),
        )
        self.assertEqual(entries[1].size_bytes_estimate, 1_400_000_000)

    def test_rejects_observation_mismatch(self):
        html = """
        <table><tr><td>iq_12.raw</td><td>4 B</td><td>13</td>
        <td>2024-01-01T00:00:00Z</td><td>X</td><td>good</td></tr></table>
        """
        with self.assertRaises(CamrasIndexError):
            parse_camras_index(html)

    def test_rejects_empty_or_off_origin_index(self):
        with self.assertRaisesRegex(CamrasIndexError, "no valid"):
            parse_camras_index("<html>maintenance</html>")
        html = """
        <table><tr><td><a href="https://evil.example/iq_12.raw">iq_12.raw</a></td>
        <td>4 B</td><td>12</td><td>2024-01-01T00:00:00Z</td>
        <td>X</td><td>good</td></tr></table>
        """
        with self.assertRaisesRegex(CamrasIndexError, "origin"):
            parse_camras_index(html)


class C16LETests(unittest.TestCase):
    def setUp(self):
        self.raw_path = FIXTURES / "synthetic_c16le.raw"

    def test_streams_explicit_little_endian_chunks(self):
        raw = self.raw_path.read_bytes()
        expected = [complex(i, q) for i, q in struct.iter_unpack("<hh", raw)]

        chunks = list(
            iter_c16le_chunks(
                self.raw_path, chunk_samples=2, normalize=False
            )
        )

        self.assertEqual([len(chunk) for chunk in chunks], [2, 1])
        self.assertEqual([value for chunk in chunks for value in chunk], expected)
        self.assertEqual(c16le_sample_count(self.raw_path), 3)
        self.assertGreater(c16le_rms(self.raw_path, chunk_samples=1), 0.0)

    def test_rejects_truncated_complex_sample(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "truncated.raw"
            path.write_bytes(b"12345")
            with self.assertRaises(TruncatedC16LEError):
                list(iter_c16le_chunks(path))

    def test_conversion_copies_without_mutating_source(self):
        original = self.raw_path.read_bytes()
        original_stat = self.raw_path.stat()
        with tempfile.TemporaryDirectory() as directory:
            result = convert_camras_raw_to_sigmf(
                self.raw_path,
                Path(directory) / "observation-10138296",
                sample_rate_hz=48_000.0,
                sample_rate_basis="duration cross-check for fixture",
                doppler_state="unknown",
                center_frequency_hz=436_666_000.0,
                start_utc="2024-08-29T14:35:24Z",
                observation_id=10138296,
                confirm_ci16_le=True,
                chunk_bytes=5,
            )

            self.assertEqual(result.data_path.read_bytes(), original)
            self.assertEqual(result.sample_count, 3)
            self.assertEqual(result.source_sha256, hashlib.sha256(original).hexdigest())
            metadata = json.loads(result.metadata_path.read_text("utf-8"))
            self.assertEqual(metadata["global"]["core:datatype"], CAMRAS_SIGMF_DATATYPE)
            self.assertEqual(metadata["global"]["core:sample_rate"], 48_000.0)
            self.assertEqual(
                metadata["global"]["telemetry_yield:sample_rate_basis"],
                "duration cross-check for fixture",
            )
            self.assertEqual(
                metadata["global"]["telemetry_yield:doppler_state"],
                "unknown",
            )
            self.assertTrue(metadata["global"]["telemetry_yield:datatype_verified"])
            self.assertEqual(metadata["global"]["telemetry_yield:source_size_bytes"], 12)
            self.assertIn("telemetry_yield:transform_config_hash", metadata["global"])
            self.assertEqual(metadata["captures"][0]["core:frequency"], 436_666_000.0)
            self.assertEqual(
                metadata["captures"][0]["core:datetime"],
                "2024-08-29T14:35:24.000000Z",
            )

        self.assertEqual(self.raw_path.read_bytes(), original)
        self.assertEqual(self.raw_path.stat().st_mtime_ns, original_stat.st_mtime_ns)

    def test_conversion_requires_datatype_confirmation(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "explicitly confirmed"):
                convert_camras_raw_to_sigmf(
                    self.raw_path,
                    Path(directory) / "unverified",
                    sample_rate_hz=48_000.0,
                    sample_rate_basis="fixture contract",
                    doppler_state="unknown",
                )

    def test_conversion_rejects_unrecorded_sample_rate_basis(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "sample_rate_basis"):
                convert_camras_raw_to_sigmf(
                    self.raw_path,
                    Path(directory) / "missing-basis",
                    sample_rate_hz=48_000.0,
                    sample_rate_basis=" ",
                    doppler_state="unknown",
                    confirm_ci16_le=True,
                )


if __name__ == "__main__":
    unittest.main()
