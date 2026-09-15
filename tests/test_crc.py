from __future__ import annotations

import unittest

from telemetry_yield.crc import (
    append_ax25_fcs,
    ax25_fcs,
    crc16_x25,
    validate_ax25_fcs,
)


class Crc16X25Tests(unittest.TestCase):
    def test_standard_check_vector(self) -> None:
        self.assertEqual(crc16_x25(b"123456789"), 0x906E)

    def test_byte_table_matches_independent_bitwise_recurrence(self) -> None:
        def reference(data: bytes) -> int:
            crc = 0xFFFF
            for byte in data:
                crc ^= byte
                for _ in range(8):
                    crc = (crc >> 1) ^ 0x8408 if crc & 1 else crc >> 1
            return (crc ^ 0xFFFF) & 0xFFFF

        vectors = (
            bytes(range(256)),
            b"\x00" * 1024,
            b"\xff" * 1024,
            bytes((index * 73 + 19) & 0xFF for index in range(4097)),
        )
        for vector in vectors:
            self.assertEqual(crc16_x25(vector), reference(vector))

    def test_ax25_fcs_uses_low_byte_first(self) -> None:
        self.assertEqual(ax25_fcs(b"123456789"), bytes.fromhex("6e90"))

    def test_appended_fcs_validates(self) -> None:
        frame = append_ax25_fcs(b"telemetry payload")
        self.assertTrue(validate_ax25_fcs(frame))

    def test_payload_corruption_is_detected(self) -> None:
        frame = bytearray(append_ax25_fcs(b"telemetry payload"))
        frame[0] ^= 0x01
        self.assertFalse(validate_ax25_fcs(frame))

    def test_short_frame_is_invalid(self) -> None:
        self.assertFalse(validate_ax25_fcs(b""))
        self.assertFalse(validate_ax25_fcs(b"\x00"))


if __name__ == "__main__":
    unittest.main()
