from __future__ import annotations

import unittest

from telemetry_yield.ccsds_validation import parse_ccsds_space_packet


class CCSDSValidationTests(unittest.TestCase):
    def test_parses_complete_space_packet(self) -> None:
        # Version 0, telemetry, secondary header, APID 32; unsegmented packet,
        # sequence 5567; four data octets (encoded length is N - 1).
        packet = bytes.fromhex("0820d5bf0003") + b"data"

        header = parse_ccsds_space_packet(packet)

        self.assertIsNotNone(header)
        assert header is not None
        self.assertEqual(header.version, 0)
        self.assertEqual(header.packet_type, 0)
        self.assertTrue(header.secondary_header)
        self.assertEqual(header.apid, 32)
        self.assertEqual(header.sequence_flags, 3)
        self.assertEqual(header.sequence_count, 5567)
        self.assertEqual(header.packet_data_length, 4)
        self.assertEqual(header.total_packet_length, 10)

    def test_rejects_nonzero_version(self) -> None:
        packet = bytes.fromhex("2820d5bf0003") + b"data"
        self.assertIsNone(parse_ccsds_space_packet(packet))

    def test_rejects_truncated_packet(self) -> None:
        packet = bytes.fromhex("0820d5bf0003") + b"dat"
        self.assertIsNone(parse_ccsds_space_packet(packet))

    def test_exact_length_policy_rejects_trailing_bytes(self) -> None:
        packet = bytes.fromhex("0820d5bf0003") + b"data" + b"trailing"
        self.assertIsNone(parse_ccsds_space_packet(packet))

        header = parse_ccsds_space_packet(packet, require_exact_length=False)
        self.assertIsNotNone(header)
        assert header is not None
        self.assertEqual(header.total_packet_length, 10)

    def test_zero_encoded_length_means_one_data_octet(self) -> None:
        packet = bytes.fromhex("0000c0000000") + b"x"
        header = parse_ccsds_space_packet(packet)
        self.assertIsNotNone(header)
        assert header is not None
        self.assertEqual(header.packet_data_length, 1)
        self.assertEqual(header.total_packet_length, 7)


if __name__ == "__main__":
    unittest.main()
