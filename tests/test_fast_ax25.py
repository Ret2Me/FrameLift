from __future__ import annotations

import unittest

import numpy as np

from telemetry_yield.camras_replay import bytes_to_bits
from telemetry_yield.clock_recovery import (
    HDLC_FLAG,
    decode_ax25_levels,
    g3ruh_descramble_bits,
)
from telemetry_yield.crc import append_ax25_fcs
from telemetry_yield.fast_ax25 import (
    FastAx25ProtocolDecoder,
    _g3ruh_zero_register,
    fast_decode_ax25_levels,
)
from telemetry_yield.generic_receiver import (
    Ax25ProtocolDecoder,
    DemodulatedSignal,
    GenericReceiver,
    WaveformHypothesis,
)


HEADER = bytes.fromhex("94a662b2a0826094a662b29eb2e103f0")


def _stuff(bits):
    output, ones = [], 0
    for bit in bits:
        output.append(bit)
        ones = ones + 1 if bit else 0
        if ones == 5:
            output.append(0)
            ones = 0
    return tuple(output)


def _nrzi(bits, initial=0):
    level, output = initial, []
    for bit in bits:
        if not bit:
            level ^= 1
        output.append(level)
    return tuple(output)


def _scramble_direct(bits):
    encoded = []
    for index, bit in enumerate(bits):
        value = bit
        if index >= 12:
            value ^= encoded[index - 12]
        if index >= 17:
            value ^= encoded[index - 17]
        encoded.append(value)
    return tuple(encoded)


def _stream(frames, *, g3ruh=False, initial=0, leading_flags=8, trailing_flags=8):
    bits = HDLC_FLAG * leading_flags
    for frame in frames:
        bits += _stuff(bytes_to_bits(frame, lsb_first=True)) + HDLC_FLAG
    bits += HDLC_FLAG * trailing_flags
    return _nrzi(_scramble_direct(bits) if g3ruh else bits, initial)


class FastAx25Tests(unittest.TestCase):
    def test_g3ruh_vectorized_delays_match_zero_register_scalar(self):
        rng = np.random.default_rng(202609071)
        for length in (0, 1, 11, 12, 13, 16, 17, 18, 33, 8192):
            bits = rng.integers(0, 2, length, dtype=np.uint8)
            self.assertEqual(tuple(_g3ruh_zero_register(bits)),
                             g3ruh_descramble_bits(bits.tolist()))

    def test_both_nrzi_starting_levels_and_modes_preserve_exact_order(self):
        frames = tuple(append_ax25_fcs(HEADER + bytes([n]) * 37) for n in range(4))
        for g3ruh in (False, True):
            for initial in (0, 1):
                for leading in (1, 2, 8):
                    levels = _stream(frames, g3ruh=g3ruh, initial=initial,
                                     leading_flags=leading)
                    self.assertEqual(fast_decode_ax25_levels(levels, g3ruh=g3ruh),
                                     decode_ax25_levels(levels, g3ruh=g3ruh))
                    self.assertEqual(set(fast_decode_ax25_levels(levels, g3ruh=g3ruh)),
                                     set(frames))

    def test_first_flag_nrzi_ambiguity_preserves_reference_nonchronological_order(self):
        first = append_ax25_fcs(HEADER + b"first")
        second = append_ax25_fcs(HEADER + b"second")
        levels = _stream((first, second), initial=1, leading_flags=1)
        self.assertEqual(decode_ax25_levels(levels, g3ruh=False), (second, first))
        self.assertEqual(fast_decode_ax25_levels(levels, g3ruh=False), (second, first))

    def test_frame_minimum_maximum_stuffing_and_duplicates_are_unchanged(self):
        frames = tuple(append_ax25_fcs(b"\xff" * size)
                       for size in (13, 14, 16, 1021, 1022))
        levels = _stream(frames + frames)
        expected = decode_ax25_levels(levels, g3ruh=False)
        self.assertEqual(fast_decode_ax25_levels(levels, g3ruh=False), expected)
        self.assertEqual(set(expected), {frames[1], frames[2], frames[3]})

    def test_corrupted_fcs_and_illegal_stuffing_are_not_repaired(self):
        good = append_ax25_fcs(HEADER + b"\xff" * 100)
        bad = good[:-1] + bytes([good[-1] ^ 1])
        for g3ruh in (False, True):
            levels = _stream((bad,), g3ruh=g3ruh)
            self.assertEqual(fast_decode_ax25_levels(levels, g3ruh=g3ruh), ())
            self.assertEqual(decode_ax25_levels(levels, g3ruh=g3ruh), ())
        malformed = _nrzi(HDLC_FLAG + (1,) * 140 + HDLC_FLAG)
        self.assertEqual(fast_decode_ax25_levels(malformed, g3ruh=False), ())

    def test_random_noise_and_mutated_framed_streams_match_reference(self):
        rng = np.random.default_rng(202609072)
        for case in range(40):
            g3ruh = bool(case % 2)
            frames = tuple(append_ax25_fcs(HEADER + rng.bytes(int(rng.integers(0, 160))))
                           for _ in range(3))
            levels = np.asarray(_stream(frames, g3ruh=g3ruh, initial=case % 2),
                                dtype=np.uint8)
            if case % 3:
                for index in rng.integers(0, len(levels), size=case % 9):
                    levels[index] ^= 1
            prefix = rng.integers(0, 2, size=case * 3, dtype=np.uint8)
            suffix = rng.integers(0, 2, size=100 + case * 2, dtype=np.uint8)
            levels = np.concatenate((prefix, levels, suffix))
            self.assertEqual(fast_decode_ax25_levels(levels, g3ruh=g3ruh),
                             decode_ax25_levels(levels.tolist(), g3ruh=g3ruh))
        for length in (0, 1, 7, 8, 15, 16, 128, 4096, 57600):
            levels = rng.integers(0, 2, size=length, dtype=np.uint8)
            for g3ruh in (False, True):
                self.assertEqual(fast_decode_ax25_levels(levels, g3ruh=g3ruh),
                                 decode_ax25_levels(levels.tolist(), g3ruh=g3ruh))

    def test_protocol_strict_structure_and_mode_order_unchanged(self):
        frames = (append_ax25_fcs(b"not-an-ax25-ui-payload"),
                  append_ax25_fcs(HEADER + b"payload"))
        for g3ruh in (False, True):
            levels = _stream(frames, g3ruh=g3ruh)
            soft = 3.0 * np.asarray(levels) - 0.5
            for modes in ((False,), (True,), (False, True), (True, False)):
                slow = Ax25ProtocolDecoder(g3ruh_modes=modes)
                fast = FastAx25ProtocolDecoder(g3ruh_modes=modes)
                self.assertEqual(fast.capabilities, slow.capabilities)
                self.assertEqual(fast.decode(soft, threshold=0.4),
                                 slow.decode(soft, threshold=0.4))
        self.assertEqual(len(FastAx25ProtocolDecoder().decode(soft, threshold=0.4)), 1)

    def test_generic_receiver_accepts_fast_plugin_without_orchestrator_changes(self):
        receiver = GenericReceiver()
        receiver.register_protocol(FastAx25ProtocolDecoder())
        frame = append_ax25_fcs(HEADER + b"generic")
        levels = np.asarray(_stream((frame,), leading_flags=80, trailing_flags=80))
        signal = DemodulatedSignal(tuple(np.repeat(levels * 2.0 - 1.0, 4)), 4.0, {})
        waveform = WaveformHypothesis("fixture", "unused", 9600, 1,
                                      phase_bins=8, top_timing_hypotheses=2)
        result = receiver.decode_demodulated(signal, waveform=waveform,
                                             protocol_id="ax25_fast")
        self.assertEqual({record.payload for record in result.frames}, {frame})

    def test_invalid_binary_values_cannot_wrap_to_binary(self):
        for levels in ([256, 0], [257, 1], [-1, 0], [0.5, 1],
                       [float("nan")], [float("inf")], [[0, 1]], ["0", "1"]):
            with self.subTest(levels=levels), self.assertRaises(ValueError):
                fast_decode_ax25_levels(levels, g3ruh=False)

    def test_nonfinite_soft_comparisons_match_reference(self):
        soft = np.asarray([float("nan"), float("inf"), -float("inf"), 0.0] * 20)
        for threshold in (0.0, float("nan"), float("inf")):
            self.assertEqual(FastAx25ProtocolDecoder().decode(soft, threshold=threshold),
                             Ax25ProtocolDecoder().decode(soft, threshold=threshold))


if __name__ == "__main__":
    unittest.main()
