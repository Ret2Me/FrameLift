from __future__ import annotations

import unittest

from telemetry_yield.camras_replay import (
    ReplayError,
    bits_to_bytes,
    bytes_to_bits,
    compare_complete_frames,
    exact_normalizations,
    extract_hdlc_frames_from_nrzi_levels,
    hdlc_unstuff,
    nrzi_decode,
    reverse_bits_per_byte,
    satnogs_fsk_parameters,
    strip_hdlc_flag_octets,
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


def _nrzi_encode(bits: tuple[int, ...], initial: int) -> tuple[int, ...]:
    level = initial
    levels: list[int] = []
    for bit in bits:
        if bit == 0:
            level ^= 1
        levels.append(level)
    return tuple(levels)


class CamrasReplayNormalizerTests(unittest.TestCase):
    def test_bit_conversion_and_reversal_are_explicit(self) -> None:
        value = b"\x01\xa5"
        lsb = bytes_to_bits(value, lsb_first=True)
        self.assertEqual(bits_to_bytes(lsb, lsb_first=True), value)
        self.assertEqual(reverse_bits_per_byte(value), b"\x80\xa5")
        with self.assertRaisesRegex(ReplayError, "aligned"):
            bits_to_bytes((1, 0, 1), lsb_first=True)

    def test_nrzi_flag_unstuff_and_valid_fcs_round_trip(self) -> None:
        frame = append_ax25_fcs(REFERENCE)
        frame_bits = _stuff(bytes_to_bits(frame, lsb_first=True))
        flag = bytes_to_bits(b"\x7e", lsb_first=True)
        levels = _nrzi_encode(flag + frame_bits + flag, initial=1)
        decoded = extract_hdlc_frames_from_nrzi_levels(
            levels, initial_level=1
        )
        self.assertEqual(decoded, (frame,))
        matches = compare_complete_frames(decoded, (REFERENCE,))
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].layers, ("strip_valid_ax25_fcs",))

    def test_hdlc_unstuff_rejects_abort_run(self) -> None:
        with self.assertRaisesRegex(ReplayError, "six ones"):
            hdlc_unstuff((1, 1, 1, 1, 1, 1))

    def test_flag_and_fcs_layers_require_complete_positive_evidence(self) -> None:
        framed = b"\x7e\x7e" + append_ax25_fcs(REFERENCE) + b"\x7e"
        self.assertEqual(
            strip_hdlc_flag_octets(framed), append_ax25_fcs(REFERENCE)
        )
        matches = compare_complete_frames((framed,), (REFERENCE,))
        self.assertEqual(len(matches), 1)
        self.assertEqual(
            matches[0].layers,
            ("strip_hdlc_flag_octets", "strip_valid_ax25_fcs"),
        )
        self.assertIsNone(strip_hdlc_flag_octets(b"\x7e" + REFERENCE))

    def test_invalid_fcs_and_partial_payload_are_never_accepted(self) -> None:
        invalid_fcs = REFERENCE + b"\x00\x00"
        partial_container = b"prefix" + REFERENCE + b"suffix"
        self.assertFalse(compare_complete_frames((invalid_fcs,), (REFERENCE,)))
        self.assertFalse(
            compare_complete_frames((partial_container,), (REFERENCE,))
        )
        layers = {variant.layers for variant in exact_normalizations(invalid_fcs)}
        self.assertFalse(
            any(layer_set[-1:] == ("strip_valid_ax25_fcs",) for layer_set in layers)
        )

    def test_per_byte_bit_order_normalization_can_match_complete_frame(self) -> None:
        reversed_reference = reverse_bits_per_byte(REFERENCE)
        matches = compare_complete_frames((reversed_reference,), (REFERENCE,))
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].layers, ("reverse_bits_per_byte",))

    def test_nrzi_initial_state_is_validated(self) -> None:
        self.assertEqual(nrzi_decode((1, 1, 0), initial_level=1), (1, 1, 0))
        with self.assertRaises(ValueError):
            nrzi_decode((0,), initial_level=2)


class SatnogsFlowgraphParameterTests(unittest.TestCase):
    def test_rsp03_rate_is_57600_not_56000(self) -> None:
        parameters = satnogs_fsk_parameters(9_600)
        self.assertEqual(parameters.decimation, 6)
        self.assertEqual(parameters.iq_sample_rate_hz, 57_600)
        self.assertEqual(parameters.demod_sample_rate_hz, 19_200)
        self.assertEqual(parameters.samples_per_symbol, 2.0)

    def test_rates_must_be_positive(self) -> None:
        with self.assertRaises(ValueError):
            satnogs_fsk_parameters(0)


if __name__ == "__main__":
    unittest.main()
