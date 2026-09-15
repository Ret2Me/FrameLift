import math
import struct
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from telemetry_yield.iq_analysis import (
    IQAnalysisError,
    analyze_iq_amplitudes,
    infer_doppler_state,
    infer_sample_rate_from_timing,
    make_tle_frequency_tracks,
    predict_tle_doppler_hz,
    raw_complex_sample_count,
    score_frequency_tracks,
    spectrogram_window_times,
)


class IQAmplitudeTests(unittest.TestCase):
    def test_rejects_partial_complex_sample(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "partial.raw"
            path.write_bytes(b"12345")
            with self.assertRaisesRegex(IQAnalysisError, "multiple of 4"):
                raw_complex_sample_count(path)

    def test_measures_explicit_endian_and_component_order(self):
        samples = [(32_767, -32_768), (1_000, -2_000), (-3_000, 4_000)]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "samples.raw"
            path.write_bytes(b"".join(struct.pack("<hh", *pair) for pair in samples))

            little = analyze_iq_amplitudes(path, byte_order="little", stride=1)
            swapped = analyze_iq_amplitudes(
                path,
                byte_order="little",
                component_order="qi",
                stride=1,
            )
            big = analyze_iq_amplitudes(path, byte_order="big", stride=1)

            expected = math.sqrt(
                sum(i * i + q * q for i, q in samples) / len(samples)
            ) / 32_768.0
            self.assertAlmostEqual(little.rms_normalized, expected)
            self.assertAlmostEqual(little.endpoint_clip_fraction, 2 / 6)
            self.assertAlmostEqual(
                little.dc_i_normalized,
                swapped.dc_q_normalized,
            )
            self.assertNotAlmostEqual(
                little.rms_normalized,
                big.rms_normalized,
            )


class FailClosedInferenceTests(unittest.TestCase):
    def test_accepts_only_unique_rate_inside_explicit_margin(self):
        inferred = infer_sample_rate_from_timing(
            file_size_bytes=57_600 * 4,
            observation_start_utc="2025-01-01T00:00:00Z",
            observation_end_utc="2025-01-01T00:00:01Z",
            max_edge_margin_seconds=0.0,
        )
        self.assertEqual(inferred.state, "accepted")
        self.assertEqual(inferred.sample_rate_hz, 57_600.0)
        self.assertAlmostEqual(
            inferred.implied_full_coverage_rate_hz,
            57_600.0,
        )

    def test_rsp03_timing_alone_does_not_override_flowgraph_rate(self):
        inferred = infer_sample_rate_from_timing(
            file_size_bytes=31_768_235 * 4,
            observation_start_utc="2025-10-06T09:07:43Z",
            observation_end_utc="2025-10-06T09:17:11Z",
            max_edge_margin_seconds=2.0,
        )
        self.assertEqual(inferred.state, "unresolved")
        self.assertIsNone(inferred.sample_rate_hz)
        self.assertAlmostEqual(
            inferred.implied_full_coverage_rate_hz,
            55_929.991197183095,
        )

    def test_no_rate_inside_margin_remains_unresolved(self):
        inferred = infer_sample_rate_from_timing(
            file_size_bytes=28_128_646 * 4,
            observation_start_utc="2024-05-30T12:32:21Z",
            observation_end_utc="2024-05-30T12:42:30Z",
            max_edge_margin_seconds=2.0,
        )
        self.assertEqual(inferred.state, "unresolved")
        self.assertIsNone(inferred.sample_rate_hz)

    def test_multiple_rates_inside_margin_remains_unresolved(self):
        inferred = infer_sample_rate_from_timing(
            file_size_bytes=48_000 * 4,
            observation_start_utc="2025-01-01T00:00:00Z",
            observation_end_utc="2025-01-01T00:00:01Z",
            max_edge_margin_seconds=0.2,
        )
        self.assertEqual(inferred.state, "unresolved")
        self.assertIsNone(inferred.sample_rate_hz)
        self.assertIn("multiple candidates", inferred.reason)

    def test_doppler_state_requires_absolute_and_relative_evidence(self):
        pre = infer_doppler_state(
            pre_correction_score=131.58,
            stationary_score=10.26,
        )
        post = infer_doppler_state(
            pre_correction_score=26.91,
            stationary_score=1_363.36,
        )
        ambiguous = infer_doppler_state(
            pre_correction_score=12.0,
            stationary_score=10.0,
        )
        self.assertEqual(pre.state, "pre_correction")
        self.assertEqual(post.state, "post_correction")
        self.assertEqual(ambiguous.state, "unknown")


class FrequencyRidgeTests(unittest.TestCase):
    @staticmethod
    def _write_chirp(path: Path, *, swap: bool = False) -> tuple[int, float]:
        sample_rate = 8_000
        seconds = 8
        phase = 0.0
        values = bytearray()
        for index in range(sample_rate * seconds):
            time_value = index / sample_rate
            frequency = 400.0 + 75.0 * time_value
            phase += 2.0 * math.pi * frequency / sample_rate
            i_value = round(12_000 * math.cos(phase))
            q_value = round(12_000 * math.sin(phase))
            pair = (q_value, i_value) if swap else (i_value, q_value)
            values.extend(struct.pack("<hh", *pair))
        path.write_bytes(values)
        return sample_rate * seconds, float(sample_rate)

    def test_matching_chirp_beats_wrong_sign_and_swap_reverses_sign(self):
        try:
            import numpy  # noqa: F401
        except ImportError:
            self.skipTest("numpy analysis dependency is not installed")

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "chirp.raw"
            sample_count, sample_rate = self._write_chirp(path)
            times = spectrogram_window_times(
                sample_count,
                sample_rate_hz=sample_rate,
                fft_size=1_024,
                step_seconds=0.25,
            )
            positive = tuple(400.0 + 75.0 * value for value in times)
            negative = tuple(-value for value in positive)
            scores = score_frequency_tracks(
                path,
                {"positive": positive, "negative": negative},
                sample_rate_hz=sample_rate,
                fft_size=1_024,
                step_seconds=0.25,
                search_half_width_hz=20.0,
            )
            by_name = {score.model: score for score in scores}
            self.assertGreater(
                by_name["positive"].p90_ridge_to_noise,
                by_name["negative"].p90_ridge_to_noise * 100,
            )

            swapped_scores = score_frequency_tracks(
                path,
                {"positive": positive, "negative": negative},
                sample_rate_hz=sample_rate,
                component_order="qi",
                fft_size=1_024,
                step_seconds=0.25,
                search_half_width_hz=20.0,
            )
            swapped_by_name = {score.model: score for score in swapped_scores}
            self.assertGreater(
                swapped_by_name["negative"].p90_ridge_to_noise,
                swapped_by_name["positive"].p90_ridge_to_noise * 100,
            )


class TLEDopplerTests(unittest.TestCase):
    TLE1 = (
        "1 56992U 23084BU  24151.10961885  .00025221  "
        "00000-0  94691-3 0  9993"
    )
    TLE2 = (
        "2 56992  97.5295 270.2902 0012855  92.9959 "
        "267.2751 15.27111910 53428"
    )

    def test_reference_track_changes_sign_during_pass(self):
        try:
            import sgp4  # noqa: F401
        except ImportError:
            self.skipTest("sgp4 analysis dependency is not installed")

        start = datetime(2024, 5, 30, 12, 32, 21, tzinfo=timezone.utc)
        predicted = predict_tle_doppler_hz(
            [start, start + timedelta(seconds=540)],
            tle1=self.TLE1,
            tle2=self.TLE2,
            station_latitude_deg=52.812,
            station_longitude_deg=6.396,
            station_altitude_m=10.0,
            carrier_frequency_hz=436_888_000.0,
        )
        self.assertAlmostEqual(predicted[0], 9_148.1, delta=2.0)
        self.assertAlmostEqual(predicted[1], -8_551.4, delta=2.0)

    def test_grid_tracks_include_offset_sign_and_carrier_offset(self):
        try:
            import sgp4  # noqa: F401
        except ImportError:
            self.skipTest("sgp4 analysis dependency is not installed")

        tracks = make_tle_frequency_tracks(
            [0.0, 1.0],
            observation_start_utc="2024-05-30T12:32:21Z",
            raw_start_offsets_seconds=[23.0],
            spectral_signs=[-1, 1],
            carrier_offsets_hz=[0.0, 100.0],
            tle1=self.TLE1,
            tle2=self.TLE2,
            station_latitude_deg=52.812,
            station_longitude_deg=6.396,
            station_altitude_m=10.0,
            carrier_frequency_hz=436_888_000.0,
        )
        self.assertEqual(len(tracks), 4)
        self.assertTrue(all(len(values) == 2 for values in tracks.values()))
        self.assertTrue(all(":raw_time_scale=1:" in name for name in tracks))

    def test_affine_time_scale_is_recorded_in_track_identity(self):
        try:
            import sgp4  # noqa: F401
        except ImportError:
            self.skipTest("sgp4 analysis dependency is not installed")

        tracks = make_tle_frequency_tracks(
            [10.0],
            observation_start_utc="2024-05-30T12:32:21Z",
            raw_start_offsets_seconds=[0.0],
            raw_time_scales=[6 / 7],
            spectral_signs=[-1],
            carrier_offsets_hz=[0.0],
            tle1=self.TLE1,
            tle2=self.TLE2,
            station_latitude_deg=52.812,
            station_longitude_deg=6.396,
            station_altitude_m=10.0,
            carrier_frequency_hz=436_888_000.0,
        )
        self.assertIn("raw_time_scale=0.857143", next(iter(tracks)))


if __name__ == "__main__":
    unittest.main()
