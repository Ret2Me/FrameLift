from __future__ import annotations

import importlib.util
from pathlib import Path
import tempfile
import unittest

import numpy as np

from telemetry_yield.camras_replay import bytes_to_bits
from telemetry_yield.clock_recovery import (
    HDLC_FLAG,
    decode_ax25_levels,
    g3ruh_scramble_bits,
)
from telemetry_yield.crc import append_ax25_fcs


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "work/positive-real-iq/decode_legal_fsk_transfer.py"
SPEC = importlib.util.spec_from_file_location("decode_legal_fsk_transfer", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)

REFERENCE = bytes.fromhex(
    "94a662b2a0826094a662b29eb2e103f00018ad8001020304"
)


def stuff(bits: tuple[int, ...]) -> tuple[int, ...]:
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


def nrzi_encode(bits: tuple[int, ...], initial: int = 0) -> tuple[int, ...]:
    previous = initial
    output: list[int] = []
    for bit in bits:
        if bit == 0:
            previous ^= 1
        output.append(previous)
    return tuple(output)


def levels_for_frame(*, g3ruh: bool) -> tuple[int, ...]:
    frame = append_ax25_fcs(REFERENCE)
    payload = stuff(bytes_to_bits(frame, lsb_first=True))
    bits = HDLC_FLAG * 20 + payload + HDLC_FLAG * 5
    if g3ruh:
        bits = g3ruh_scramble_bits(bits)
    return nrzi_encode(bits)


class LegalFskTransferTests(unittest.TestCase):
    def test_vectorized_decoder_equals_frozen_decoder_on_plain_frame(self) -> None:
        levels = levels_for_frame(g3ruh=False)
        expected = decode_ax25_levels(levels, g3ruh=False)
        actual = MODULE.decode_levels_vectorized(levels, g3ruh=False)
        self.assertEqual(actual, expected)
        self.assertIn(append_ax25_fcs(REFERENCE), actual)

    def test_vectorized_decoder_equals_frozen_decoder_on_g3ruh_frame(self) -> None:
        levels = levels_for_frame(g3ruh=True)
        expected = decode_ax25_levels(levels, g3ruh=True)
        actual = MODULE.decode_levels_vectorized(levels, g3ruh=True)
        self.assertEqual(actual, expected)
        self.assertIn(append_ax25_fcs(REFERENCE), actual)

    def test_vectorized_decoder_equals_frozen_decoder_on_random_null(self) -> None:
        levels = tuple(np.random.default_rng(20260902).integers(0, 2, 20_000).tolist())
        for g3ruh in (False, True):
            self.assertEqual(
                MODULE.decode_levels_vectorized(levels, g3ruh=g3ruh),
                decode_ax25_levels(levels, g3ruh=g3ruh),
            )

    def test_ci16_reader_is_bounded_and_exact(self) -> None:
        components = np.arange(40, dtype="<i2")
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "fixture.raw"
            path.write_bytes(components.tobytes())
            actual = MODULE.read_ci16_window(path, 3, 4)
        expected = components.reshape(-1, 2)[3:7]
        np.testing.assert_array_equal(actual.real, expected[:, 0])
        np.testing.assert_array_equal(actual.imag, expected[:, 1])

    def test_decoder_has_no_truth_arguments(self) -> None:
        names = set(MODULE.decode_capture.__annotations__)
        self.assertNotIn("reference", names)
        self.assertNotIn("timestamp", names)


if __name__ == "__main__":
    unittest.main()
