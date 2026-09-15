from __future__ import annotations

from dataclasses import replace
import unittest

import numpy as np

from telemetry_yield.audio_receiver import decode_audio_pcm
from telemetry_yield.camras_replay import bytes_to_bits
from telemetry_yield.clock_recovery import HDLC_FLAG, g3ruh_scramble_bits
from telemetry_yield.crc import append_ax25_fcs, validate_ax25_fcs
from telemetry_yield.generic_receiver import ReceiverHypothesis, WaveformHypothesis


REFERENCE = bytes.fromhex("94a662b2a0826094a662b29eb2e103f00018ad8001020304")


def _levels(*, g3ruh: bool, corrupt: bool = False) -> np.ndarray:
    frame = append_ax25_fcs(REFERENCE)
    if corrupt:
        frame = frame[:-1] + bytes([frame[-1] ^ 1])
    stuffed = []
    ones = 0
    for bit in bytes_to_bits(frame, lsb_first=True):
        stuffed.append(bit)
        ones = ones + 1 if bit else 0
        if ones == 5:
            stuffed.append(0)
            ones = 0
    bits = HDLC_FLAG * 80 + (tuple(stuffed) + HDLC_FLAG * 8) * 8
    if g3ruh:
        bits = g3ruh_scramble_bits(bits)
    level = 0
    output = []
    for bit in bits:
        if not bit:
            level ^= 1
        output.append(level)
    return np.asarray(output)


def _plan(mode: str, *, usb: bool = False) -> tuple[ReceiverHypothesis, ...]:
    afsk = mode == "afsk"
    return (ReceiverHypothesis(
        WaveformHypothesis(
            hypothesis_id="fixture",
            demodulator_id=("phase_fsk" if usb else "pcm_fsk") if not afsk else "pcm_bell202",
            symbol_rate=1_200 if afsk else 9_600,
            decimation=5 if afsk else 2,
            rate_errors_ppm=(-500.0, 0.0, 500.0),
            phase_bins=32,
            top_timing_hypotheses=8,
            modulation_family=mode,
        ),
        "ax25_plain" if afsk else "ax25",
    ),)


def _fsk_audio(*, corrupt: bool = False) -> np.ndarray:
    from scipy.ndimage import gaussian_filter1d

    symbols = np.repeat(2.0 * _levels(g3ruh=True, corrupt=corrupt) - 1.0, 5)
    return 0.4 * gaussian_filter1d(symbols, 0.8) + 0.08


def _afsk_audio() -> np.ndarray:
    tones = np.repeat(np.where(_levels(g3ruh=False) > 0, 1_200.0, 2_200.0), 40)
    return 0.75 * np.sin(2 * np.pi * np.cumsum(tones) / 48_000)


class AudioReceiverTests(unittest.TestCase):
    def test_fm_demodulated_fsk_recovers_g3ruh_frame_without_second_discriminator(self):
        result = decode_audio_pcm(_fsk_audio(), sample_rate_hz=48_000,
                                  representation="fm_demodulated", plan=_plan("fsk"))
        self.assertEqual({item.payload for item in result.frames}, {append_ax25_fcs(REFERENCE)})
        self.assertFalse(result.failures)
        self.assertGreater(result.attempted_timing_hypotheses, 0)
        self.assertTrue(all(item.waveform_hypothesis_id.startswith("fm_demodulated:") for item in result.frames))
        self.assertTrue(all(validate_ax25_fcs(item.payload) for item in result.frames))

    def test_fm_demodulated_afsk_recovers_plain_frame(self):
        result = decode_audio_pcm(_afsk_audio(), sample_rate_hz=48_000,
                                  representation="fm_demodulated", plan=_plan("afsk"))
        self.assertEqual({item.payload for item in result.frames}, {append_ax25_fcs(REFERENCE)})
        self.assertFalse(result.failures)

    def test_usb_real_passband_recovers_g3ruh_without_claiming_original_iq(self):
        from scipy.ndimage import gaussian_filter1d

        levels = gaussian_filter1d(np.repeat(2.0 * _levels(g3ruh=True) - 1.0, 5), 0.8)
        phase = 2 * np.pi * np.cumsum(12_000 + 2_400 * levels) / 48_000
        result = decode_audio_pcm(np.cos(phase), sample_rate_hz=48_000,
                                  representation="usb_real_passband", carrier_hz=12_000,
                                  plan=_plan("fsk", usb=True))
        self.assertEqual({item.payload for item in result.frames}, {append_ax25_fcs(REFERENCE)})
        self.assertFalse(result.failures)
        self.assertTrue(result.frames[0].waveform_hypothesis_id.startswith("usb_real_passband:"))

    def test_corrupted_fcs_never_accepted(self):
        result = decode_audio_pcm(_fsk_audio(corrupt=True), sample_rate_hz=48_000,
                                  representation="fm_demodulated", plan=_plan("fsk"))
        self.assertEqual(result.frames, ())

    def test_silence_and_constant_audio_have_no_frames(self):
        for value in (0.0, 0.3):
            result = decode_audio_pcm(np.full(20_001, value), sample_rate_hz=48_000,
                                      representation="fm_demodulated", plan=_plan("fsk"))
            self.assertEqual(result.frames, ())
            self.assertEqual(result.failures, ())
            self.assertEqual(result.attempted_timing_hypotheses, 0)

    def test_odd_mono_length_is_valid_not_mistaken_for_interleaved_iq(self):
        audio = _fsk_audio()
        audio = audio[: len(audio) - 1] if len(audio) % 2 == 0 else audio
        self.assertEqual(len(audio) % 2, 1)
        result = decode_audio_pcm(audio, sample_rate_hz=48_000,
                                  representation="fm_demodulated", plan=_plan("fsk"))
        self.assertEqual({item.payload for item in result.frames}, {append_ax25_fcs(REFERENCE)})

    def test_complex_stereo_nonfinite_and_short_pcm_are_rejected(self):
        bad_inputs = [np.zeros((10_000, 2)), np.zeros(10_000, dtype=complex),
                      np.zeros(10_000, dtype=bool), np.zeros(100),
                      np.full(10_000, np.nan), np.full(10_000, np.inf)]
        for pcm in bad_inputs:
            with self.subTest(shape=pcm.shape, dtype=pcm.dtype):
                with self.assertRaises(ValueError):
                    decode_audio_pcm(pcm, sample_rate_hz=48_000,
                                     representation="fm_demodulated", plan=_plan("fsk"))

    def test_invalid_sample_rate_rejected(self):
        for rate in (0, -1, True, float("nan"), float("inf"), 48_000.2, "48000"):
            with self.subTest(rate=rate), self.assertRaises(ValueError):
                decode_audio_pcm(np.zeros(10_000), sample_rate_hz=rate,
                                 representation="fm_demodulated", plan=_plan("fsk"))

    def test_representation_must_be_explicit_and_consistent(self):
        audio = np.zeros(10_000)
        cases = [dict(representation="unknown", plan=_plan("fsk")),
                 dict(representation="fm_demodulated", plan=_plan("fsk", usb=True)),
                 dict(representation="fm_demodulated", plan=_plan("fsk"), carrier_hz=12_000),
                 dict(representation="usb_real_passband", plan=_plan("fsk", usb=True)),
                 dict(representation="usb_real_passband", plan=_plan("fsk", usb=True), carrier_hz=25_000),
                 dict(representation="fm_demodulated", plan=())]
        for arguments in cases:
            with self.subTest(arguments=arguments), self.assertRaises(ValueError):
                decode_audio_pcm(audio, sample_rate_hz=48_000, **arguments)
        with self.assertRaises(TypeError):
            decode_audio_pcm(audio, sample_rate_hz=48_000, plan=_plan("fsk"))

    def test_wrong_modulation_and_nonfinite_waveform_metadata_rejected(self):
        waveform = _plan("fsk")[0].waveform
        for changed in (replace(waveform, modulation_family="afsk"),
                        replace(waveform, symbol_rate=float("nan")),
                        replace(waveform, rate_errors_ppm=(float("inf"),)),
                        replace(waveform, rate_errors_ppm=(-1_000_000.0,))):
            with self.subTest(waveform=changed), self.assertRaises(ValueError):
                decode_audio_pcm(np.zeros(10_000), sample_rate_hz=48_000,
                                 representation="fm_demodulated",
                                 plan=(ReceiverHypothesis(changed, "ax25"),))

    def test_invalid_frontend_parameters_rejected_even_on_silence(self):
        waveform = _plan("fsk")[0].waveform
        bad_waveforms = [
            replace(waveform, parameters={"post_discriminator_cutoff_hz": value})
            for value in (-1, 24_000, float("nan"), True)
        ]
        afsk = _plan("afsk")[0].waveform
        bad_waveforms.extend(
            replace(afsk, parameters={"mark_hz": value})
            for value in (-1, 2_200, 5_000, float("nan"), True)
        )
        bad_waveforms.extend((replace(waveform, phase_bins=8.5),
                              replace(waveform, top_timing_hypotheses=True)))
        for changed in bad_waveforms:
            with self.subTest(waveform=changed), self.assertRaises(ValueError):
                decode_audio_pcm(np.zeros(10_000), sample_rate_hz=48_000,
                                 representation="fm_demodulated",
                                 plan=(ReceiverHypothesis(changed, "ax25"),))

    def test_signed_pcm_scaling_preserves_decoding(self):
        pcm = np.round(_fsk_audio() * 30_000).astype(np.int16)
        result = decode_audio_pcm(pcm, sample_rate_hz=48_000,
                                  representation="fm_demodulated", plan=_plan("fsk"))
        self.assertEqual({item.payload for item in result.frames}, {append_ax25_fcs(REFERENCE)})

    def test_seeded_noise_has_no_frames(self):
        pcm = np.random.default_rng(20_260_907).normal(size=48_000)
        result = decode_audio_pcm(pcm, sample_rate_hz=48_000,
                                  representation="fm_demodulated", plan=_plan("fsk"))
        self.assertEqual(result.frames, ())


if __name__ == "__main__":
    unittest.main()
