from __future__ import annotations

import unittest

from telemetry_yield.ax25_validation import parse_ax25_ui


class AX25ValidationTests(unittest.TestCase):
    def test_parses_canonical_kostka_ui_frame(self) -> None:
        payload = bytes.fromhex(
            "86a240404040609e966096a6a86103f0"
            "3235352c302c31342c343139343833312c343635313533342c"
            "313738383034323132342c302c32312e32332c31312e3439"
        )
        frame = parse_ax25_ui(payload)
        self.assertIsNotNone(frame)
        assert frame is not None
        self.assertEqual(frame.destination.callsign, "CQ")
        self.assertEqual(frame.source.callsign, "OK0KST")
        self.assertEqual(frame.pid, 0xF0)
        self.assertTrue(frame.information.startswith(b"255,0,14,"))

    def test_rejects_crc_valid_but_non_ax25_candidate(self) -> None:
        candidate_without_fcs = bytes.fromhex(
            "5916d3059c60ef22cf10f0676150e8e6"
        )
        self.assertIsNone(parse_ax25_ui(candidate_without_fcs))

    def test_rejects_non_ui_control(self) -> None:
        payload = bytearray.fromhex(
            "86a240404040609e966096a6a86103f074657374"
        )
        payload[14] = 0x13
        self.assertIsNone(parse_ax25_ui(bytes(payload)))


if __name__ == "__main__":
    unittest.main()
