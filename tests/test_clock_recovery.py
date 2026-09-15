from __future__ import annotations

import unittest

import numpy as np

from telemetry_yield.camras_replay import bytes_to_bits
from telemetry_yield.clock_recovery import (
    HDLC_FLAG,
    decode_ax25_levels,
    deterministic_fsk_demodulate,
    extract_valid_ax25_frames,
    g3ruh_descramble_bits,
    g3ruh_scramble_bits,
    recover_timing_hypotheses,
    slice_with_hypothesis,
)
from telemetry_yield.crc import append_ax25_fcs


REFERENCE = bytes.fromhex(
    "94a662b2a0826094a662b29eb2e103f00018ad8001020304"
)


def _stuff(bits: tuple[int, ...]) -> tuple[int, ...]:
    output: list[int] = []
    ones = 0
    for bit in bits:
        output.append(bit)
        if bit:
            ones += 1
            if ones == 5:
                output.append(0)
                ones = 0
        else:
            ones = 0
    return tuple(output)


def _nrzi_encode(bits: tuple[int, ...], initial: int = 0) -> tuple[int, ...]:
    previous = initial
    output: list[int] = []
    for bit in bits:
        if bit == 0:
            previous ^= 1
        output.append(previous)
    return tuple(output)


def _render_levels(
    levels: tuple[int, ...],
    *,
    samples_per_symbol: float,
    offset_samples: float,
    noise_standard_deviation: float = 0.03,
) -> np.ndarray:
    size = int(offset_samples + samples_per_symbol * (len(levels) + 3))
    indices = np.arange(size, dtype=np.float64)
    symbols = np.floor((indices - offset_samples) / samples_per_symbol).astype(int)
    symbols = np.clip(symbols, 0, len(levels) - 1)
    waveform = 2.0 * np.asarray(levels, dtype=np.float64)[symbols] - 1.0
    taps = np.asarray([0.08, 0.17, 0.50, 0.17, 0.08], dtype=np.float64)
    waveform = np.convolve(waveform, taps, mode="same")
    rng = np.random.default_rng(20260901)
    return waveform + rng.normal(0.0, noise_standard_deviation, waveform.size)


def _synthetic_g3ruh_levels(frame_without_fcs: bytes) -> tuple[int, ...]:
    frame = append_ax25_fcs(frame_without_fcs)
    payload = _stuff(bytes_to_bits(frame, lsb_first=True))
    plain = HDLC_FLAG * 40 + (payload + HDLC_FLAG * 5) * 8
    scrambled = g3ruh_scramble_bits(plain)
    return _nrzi_encode(scrambled)


class DeterministicTimingRecoveryTests(unittest.TestCase):
    def test_phase_demodulator_accepts_non_satnogs_sample_and_symbol_rates(
        self,
    ) -> None:
        sample_rate = 48_000
        baudrate = 1_200
        symbol_count = 600
        levels = np.tile(np.asarray((-1.0, 1.0)), symbol_count // 2)
        frequency = 1_500.0 * np.repeat(levels, sample_rate // baudrate)
        phase = 2.0 * np.pi * np.cumsum(frequency) / sample_rate
        iq = np.exp(1j * phase)
        demodulated, metrics = deterministic_fsk_demodulate(
            iq,
            sample_rate_hz=sample_rate,
            baudrate=baudrate,
            decimation=20,
        )
        self.assertGreater(len(demodulated), 100)
        self.assertTrue(np.all(np.isfinite(demodulated)))
        self.assertEqual(metrics.input_complex_samples, len(iq))

    def test_channel_conditioned_phase_demodulator_decodes_low_deviation_fsk(
        self,
    ) -> None:
        sample_rate = 57_600
        baudrate = 9_600
        samples_per_symbol = sample_rate // baudrate
        levels = _synthetic_g3ruh_levels(REFERENCE)
        frequency = 6_500.0 + 2_400.0 * (
            2.0 * np.asarray(levels, dtype=np.float64) - 1.0
        )
        frequency = np.repeat(frequency, samples_per_symbol)
        phase = 2.0 * np.pi * np.cumsum(frequency) / sample_rate
        rng = np.random.default_rng(20260902)
        noise = 0.18 * (
            rng.normal(size=phase.size) + 1j * rng.normal(size=phase.size)
        )
        iq = np.exp(1j * phase) + noise

        demodulated, _metrics = deterministic_fsk_demodulate(
            iq,
            sample_rate_hz=sample_rate,
            baudrate=baudrate,
            decimation=3,
        )
        hypotheses = recover_timing_hypotheses(
            demodulated,
            nominal_samples_per_symbol=2.0,
            rate_errors_ppm=(0.0,),
            phase_bins=32,
            top_n=32,
        )
        frames: set[bytes] = set()
        for hypothesis in hypotheses:
            frames.update(
                decode_ax25_levels(
                    slice_with_hypothesis(demodulated, hypothesis),
                    g3ruh=True,
                )
            )
        self.assertIn(append_ax25_fcs(REFERENCE), frames)

    def test_fractional_offset_and_rate_error_are_recovered_deterministically(
        self,
    ) -> None:
        rng = np.random.default_rng(7)
        levels = tuple(rng.integers(0, 2, size=6000).tolist())
        true_rate_error_ppm = 750.0
        waveform = _render_levels(
            levels,
            samples_per_symbol=2.0 * (1.0 + true_rate_error_ppm * 1e-6),
            offset_samples=0.41,
        )
        kwargs = {
            "nominal_samples_per_symbol": 2.0,
            "rate_errors_ppm": tuple(range(-1000, 1001, 250)),
            "phase_bins": 32,
            "top_n": 8,
        }
        first = recover_timing_hypotheses(waveform, **kwargs)
        second = recover_timing_hypotheses(waveform, **kwargs)
        self.assertEqual(first, second)
        self.assertEqual(first[0].rate_error_ppm, true_rate_error_ppm)
        self.assertGreater(first[0].score, 4.5)

    def test_synthetic_g3ruh_frame_survives_timing_recovery(self) -> None:
        levels = _synthetic_g3ruh_levels(REFERENCE)
        waveform = _render_levels(
            levels,
            samples_per_symbol=2.0 * (1.0 - 500.0e-6),
            offset_samples=0.77,
        )
        hypotheses = recover_timing_hypotheses(
            waveform,
            nominal_samples_per_symbol=2.0,
            rate_errors_ppm=tuple(range(-1000, 1001, 250)),
            phase_bins=32,
            top_n=8,
        )
        frames: set[bytes] = set()
        for hypothesis in hypotheses:
            sliced = slice_with_hypothesis(waveform, hypothesis)
            frames.update(decode_ax25_levels(sliced, g3ruh=True))
        self.assertIn(append_ax25_fcs(REFERENCE), frames)

    def test_g3ruh_scrambler_round_trip_settles(self) -> None:
        bits = HDLC_FLAG * 8 + bytes_to_bits(REFERENCE, lsb_first=True)
        recovered = g3ruh_descramble_bits(g3ruh_scramble_bits(bits))
        self.assertEqual(recovered[17:], bits[:-17])

    def test_crc_invalid_complete_region_is_rejected(self) -> None:
        invalid = REFERENCE + b"\x00\x00"
        bits = HDLC_FLAG + bytes_to_bits(invalid, lsb_first=True) + HDLC_FLAG
        self.assertEqual(extract_valid_ax25_frames(bits), ())

    def test_crc_valid_frame_below_official_minimum_is_rejected(self) -> None:
        too_short = append_ax25_fcs(b"short")
        bits = HDLC_FLAG + bytes_to_bits(too_short, lsb_first=True) + HDLC_FLAG
        self.assertEqual(extract_valid_ax25_frames(bits), ())

    def test_crc_valid_frame_at_decoder_limit_is_rejected(self) -> None:
        boundary = append_ax25_fcs(b"\x00" * 1022)
        bits = HDLC_FLAG + _stuff(
            bytes_to_bits(boundary, lsb_first=True)
        ) + HDLC_FLAG
        self.assertEqual(len(boundary), 1024)
        self.assertEqual(extract_valid_ax25_frames(bits), ())

    def test_crc_valid_frame_greater_than_configured_limit_is_rejected(
        self,
    ) -> None:
        oversized = append_ax25_fcs(b"\x00" * 1024)
        bits = HDLC_FLAG + _stuff(
            bytes_to_bits(oversized, lsb_first=True)
        ) + HDLC_FLAG
        self.assertGreater(len(oversized), 1024)
        self.assertEqual(extract_valid_ax25_frames(bits), ())


if __name__ == "__main__":
    unittest.main()
