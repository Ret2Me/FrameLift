from __future__ import annotations

import unittest

from telemetry_yield.symbol_boundary import (
    decode_satnogs_symbols,
    g3ruh_descramble,
    g3ruh_input_for_plain_bits,
    nrzi_decode_satnogs,
    nrzi_encode_satnogs,
    reference_hdlc_region,
    scan_hdlc,
    synthetic_sliced_levels,
)


REFERENCE = bytes.fromhex(
    "94a662b2a0826094a662b29eb2e103f00018ad8001020304"
)


class SymbolBoundaryTests(unittest.TestCase):
    def test_nrzi_round_trip_uses_satnogs_initial_zero(self) -> None:
        bits = bytes((1, 1, 0, 1, 0, 0, 1))
        levels = nrzi_encode_satnogs(bits, initial_level=0)
        self.assertEqual(nrzi_decode_satnogs(levels), bits)

    def test_g3ruh_inverse_and_seed_self_synchronization(self) -> None:
        plain = bytes((index * 7 + 1) & 1 for index in range(80))
        encoded = g3ruh_input_for_plain_bits(plain)
        self.assertEqual(g3ruh_descramble(encoded), plain)
        seeded = g3ruh_descramble(encoded, seed=0x1FFFF)
        self.assertEqual(seeded[17:], plain[17:])
        self.assertNotEqual(seeded[:17], plain[:17])

    def test_source_faithful_positive_control_recovers_exact_reference(self) -> None:
        levels = synthetic_sliced_levels(REFERENCE)
        decoded = decode_satnogs_symbols(levels, descramble=True)
        scan = scan_hdlc(decoded)
        self.assertEqual(scan.accepted_frames, (REFERENCE,))
        self.assertEqual(scan.valid_fcs_regions, 1)

    def test_wrong_descrambler_mode_is_a_negative_control(self) -> None:
        levels = synthetic_sliced_levels(REFERENCE)
        decoded = decode_satnogs_symbols(levels, descramble=False)
        self.assertNotIn(REFERENCE, scan_hdlc(decoded).accepted_frames)

    def test_payload_bit_error_is_rejected_by_fcs(self) -> None:
        levels = bytearray(synthetic_sliced_levels(REFERENCE))
        frame_start = 3 * 8
        levels[frame_start + len(reference_hdlc_region(REFERENCE)) // 2] ^= 1
        decoded = decode_satnogs_symbols(levels, descramble=True)
        self.assertNotIn(REFERENCE, scan_hdlc(decoded).accepted_frames)

    def test_valid_crc_region_below_ax25_minimum_is_not_accepted(self) -> None:
        levels = synthetic_sliced_levels(b"\x84\x4c")
        decoded = decode_satnogs_symbols(levels, descramble=True)
        scan = scan_hdlc(decoded)
        self.assertEqual(scan.valid_fcs_regions, 1)
        self.assertEqual(scan.undersized_valid_fcs_regions, 1)
        self.assertFalse(scan.accepted_frames)

    def test_input_validation(self) -> None:
        with self.assertRaises(ValueError):
            nrzi_decode_satnogs((0, 2))
        with self.assertRaises(ValueError):
            g3ruh_descramble((0, -1))
        with self.assertRaises(ValueError):
            synthetic_sliced_levels(REFERENCE, preamble_flags=0)


if __name__ == "__main__":
    unittest.main()
