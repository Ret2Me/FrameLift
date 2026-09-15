from __future__ import annotations

import binascii
import unittest

from telemetry_yield.camras_replay import bytes_to_bits
from telemetry_yield.ccsds_tm_validation import (
    TMTransferFrameConfig,
    compute_tm_fecf,
    parse_tm_transfer_frame,
    validate_tm_fecf,
    validate_tm_transfer_frame,
)
from telemetry_yield.generic_receiver import (
    CcsdsTmTransferFrameDecoder,
    GenericReceiver,
)


def _primary_header(
    *,
    version: int = 0,
    spacecraft_id: int = 0x155,
    virtual_channel_id: int = 3,
    ocf_present: bool = False,
    master_count: int = 17,
    virtual_count: int = 29,
    secondary_header_present: bool = False,
    synchronization_flag: bool = False,
    packet_order_flag: bool = False,
    segment_length_id: int = 3,
    first_header_pointer: int = 0,
) -> bytes:
    first_word = (
        (version << 14)
        | (spacecraft_id << 4)
        | (virtual_channel_id << 1)
        | int(ocf_present)
    )
    status = (
        (int(secondary_header_present) << 15)
        | (int(synchronization_flag) << 14)
        | (int(packet_order_flag) << 13)
        | (segment_length_id << 11)
        | first_header_pointer
    )
    return (
        first_word.to_bytes(2, "big")
        + bytes((master_count, virtual_count))
        + status.to_bytes(2, "big")
    )


def _append_independent_fecf(frame_without_fecf: bytes) -> bytes:
    # binascii is an independent implementation of the same non-reflected
    # CRC-16 operation and guards against tests merely echoing production code.
    fecf = binascii.crc_hqx(frame_without_fecf, 0xFFFF)
    return frame_without_fecf + fecf.to_bytes(2, "big")


class CcsdsTmValidationTests(unittest.TestCase):
    def test_fecf_matches_standard_crc_parameters(self) -> None:
        self.assertEqual(compute_tm_fecf(b"123456789"), 0x29B1)

    def test_parses_primary_header_and_accepts_only_valid_fecf(self) -> None:
        data = bytes.fromhex("08200000616263")
        raw = _append_independent_fecf(_primary_header() + data)
        config = TMTransferFrameConfig(
            frame_length_bytes=len(raw),
            fecf_present=True,
        )

        parsed = parse_tm_transfer_frame(raw, config=config)
        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual(parsed.primary_header.transfer_frame_version, 0)
        self.assertEqual(parsed.primary_header.spacecraft_id, 0x155)
        self.assertEqual(parsed.primary_header.virtual_channel_id, 3)
        self.assertEqual(parsed.primary_header.master_channel_frame_count, 17)
        self.assertEqual(parsed.primary_header.virtual_channel_frame_count, 29)
        self.assertEqual(parsed.data_field, data)
        self.assertEqual(
            parsed.frame_error_control_field,
            int.from_bytes(raw[-2:], "big"),
        )

        result = validate_tm_transfer_frame(raw, config=config)
        self.assertTrue(result.accepted)
        self.assertEqual(
            result.validation_layers,
            (
                "ccsds_tm_primary_header",
                "ccsds_tm_exact_frame_length",
                "ccsds_tm_fecf_crc16",
            ),
        )
        self.assertTrue(validate_tm_fecf(raw))

        corrupted = raw[:-3] + bytes((raw[-3] ^ 0x01,)) + raw[-2:]
        rejected = validate_tm_transfer_frame(corrupted, config=config)
        self.assertFalse(rejected.accepted)
        self.assertEqual(rejected.rejection_reason, "invalid_fecf")

    def test_parses_optional_secondary_header_and_ocf_boundaries(self) -> None:
        # ID=0x02 means secondary-header version 0 and total length 3 octets.
        secondary = b"\x02sh"
        data = b"data!"
        ocf = bytes.fromhex("01020304")
        raw = _append_independent_fecf(
            _primary_header(
                ocf_present=True,
                secondary_header_present=True,
            )
            + secondary
            + data
            + ocf
        )
        config = TMTransferFrameConfig(len(raw), fecf_present=True)

        parsed = parse_tm_transfer_frame(raw, config=config)
        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertIsNotNone(parsed.secondary_header)
        assert parsed.secondary_header is not None
        self.assertEqual(parsed.secondary_header.version, 0)
        self.assertEqual(parsed.secondary_header.total_length_bytes, 3)
        self.assertEqual(parsed.secondary_header.data, b"sh")
        self.assertEqual(parsed.data_field, data)
        self.assertEqual(parsed.operational_control_field, ocf)

    def test_exact_length_and_version_are_fail_closed(self) -> None:
        raw = _append_independent_fecf(_primary_header() + b"payload")
        config = TMTransferFrameConfig(len(raw), fecf_present=True)

        self.assertIsNone(parse_tm_transfer_frame(raw[:-1], config=config))
        wrong_version = bytes((raw[0] | 0x40,)) + raw[1:]
        result = validate_tm_transfer_frame(wrong_version, config=config)
        self.assertFalse(result.accepted)
        self.assertEqual(
            result.rejection_reason,
            "unsupported_transfer_frame_version",
        )

    def test_packet_status_invariants_and_pointer_are_checked(self) -> None:
        invalid_order = _primary_header(packet_order_flag=True) + b"abc"
        order_config = TMTransferFrameConfig(len(invalid_order), fecf_present=False)
        self.assertIsNone(
            parse_tm_transfer_frame(invalid_order, config=order_config)
        )

        invalid_segment = _primary_header(segment_length_id=2) + b"abc"
        segment_config = TMTransferFrameConfig(
            len(invalid_segment), fecf_present=False
        )
        self.assertIsNone(
            parse_tm_transfer_frame(invalid_segment, config=segment_config)
        )

        invalid_pointer = _primary_header(first_header_pointer=3) + b"abc"
        pointer_config = TMTransferFrameConfig(
            len(invalid_pointer), fecf_present=False
        )
        self.assertIsNone(
            parse_tm_transfer_frame(invalid_pointer, config=pointer_config)
        )

    def test_header_only_never_becomes_telemetry_without_integrity(self) -> None:
        raw = _primary_header() + b"candidate"
        config = TMTransferFrameConfig(len(raw), fecf_present=False)

        structural = parse_tm_transfer_frame(raw, config=config)
        self.assertIsNotNone(structural)
        untrusted = validate_tm_transfer_frame(raw, config=config)
        self.assertFalse(untrusted.accepted)
        self.assertEqual(
            untrusted.rejection_reason,
            "integrity_evidence_required",
        )

        trusted = validate_tm_transfer_frame(
            raw,
            config=config,
            integrity_validator=lambda candidate, parsed: (
                candidate == raw and parsed.primary_header.spacecraft_id == 0x155
            ),
            integrity_name="mission_mac",
        )
        self.assertTrue(trusted.accepted)
        self.assertEqual(trusted.validation_layers[-1], "mission_mac")

        rejected = validate_tm_transfer_frame(
            raw,
            config=config,
            integrity_validator=lambda _candidate, _parsed: False,
            integrity_name="mission_mac",
        )
        self.assertFalse(rejected.accepted)
        self.assertEqual(
            rejected.rejection_reason,
            "integrity_validator_rejected",
        )

    def test_callback_failure_is_a_rejection_not_a_campaign_crash(self) -> None:
        raw = _primary_header() + b"candidate"
        config = TMTransferFrameConfig(len(raw), fecf_present=False)

        def broken_validator(*_args: object) -> bool:
            raise RuntimeError("validator unavailable")

        result = validate_tm_transfer_frame(
            raw,
            config=config,
            integrity_validator=broken_validator,
            integrity_name="external_integrity",
        )
        self.assertFalse(result.accepted)
        self.assertEqual(result.rejection_reason, "integrity_validator_error")

    def test_generic_receiver_adapter_exposes_narrow_capabilities(self) -> None:
        marker = tuple(bytes_to_bits(bytes.fromhex("1acffc1d"), lsb_first=False))
        raw = _append_independent_fecf(_primary_header() + b"payload")
        decoder = CcsdsTmTransferFrameDecoder(
            config=TMTransferFrameConfig(len(raw), fecf_present=True),
            sync_marker=marker,
        )
        receiver = GenericReceiver()
        receiver.register_protocol(decoder)

        capabilities = receiver.capabilities.protocols[decoder.protocol_id]
        self.assertEqual(
            capabilities.protocol_stack,
            ("ccsds_tm_transfer_frame",),
        )
        self.assertNotIn("aos", capabilities.protocol_stack)
        self.assertNotIn("uslp", capabilities.protocol_stack)
        self.assertEqual(
            capabilities.validation_layers[-1],
            "ccsds_tm_fecf_crc16",
        )

        bits = (1, 0, 1) + marker + bytes_to_bits(raw, lsb_first=False) + (0, 1)
        soft = tuple(1.0 if bit else -1.0 for bit in bits)
        decoded = decoder.decode(soft, threshold=0.0)
        self.assertEqual(decoded[0][0], raw)
        self.assertEqual(
            decoded[0][1],
            "ccsds_tm_primary_header+ccsds_tm_exact_frame_length+"
            "ccsds_tm_fecf_crc16",
        )

        corrupted = raw[:-3] + bytes((raw[-3] ^ 0x80,)) + raw[-2:]
        corrupt_bits = marker + bytes_to_bits(corrupted, lsb_first=False)
        corrupt_soft = tuple(1.0 if bit else -1.0 for bit in corrupt_bits)
        self.assertEqual(decoder.decode(corrupt_soft, threshold=0.0), ())

    def test_generic_adapter_requires_an_integrity_path(self) -> None:
        with self.assertRaisesRegex(ValueError, "integrity validator"):
            CcsdsTmTransferFrameDecoder(
                config=TMTransferFrameConfig(12, fecf_present=False),
                sync_marker=(1, 0, 1, 0),
            )


if __name__ == "__main__":
    unittest.main()
