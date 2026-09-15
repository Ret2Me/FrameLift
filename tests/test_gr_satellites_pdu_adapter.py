from __future__ import annotations

import numpy as np
import pytest

from telemetry_yield.camras_replay import bytes_to_bits
from telemetry_yield.crc import append_ax25_fcs
from telemetry_yield.gr_satellites_pdu_adapter import (
    ADAPTER_SCHEMA_VERSION,
    decode_gr_satellites_g3ruh_levels,
    extract_gr_satellites_hdlc_pdus,
)
from telemetry_yield.symbol_boundary import (
    AX25_FLAG_BITS,
    g3ruh_input_for_plain_bits,
    nrzi_encode_satnogs,
    stuff_hdlc_bits,
)


OBSERVATION_4704_PDU = bytes.fromhex("6073d927ea3ae8157bfd2b60")
STRICT_AX25_UI = bytes.fromhex(
    "94a662b2a0826094a662b29eb2e103f00018ad8001020304"
)


def _decoded_stream(payload: bytes, *, drop_leading_bits: int = 0) -> bytes:
    frame = append_ax25_fcs(payload)
    region = stuff_hdlc_bits(bytes_to_bits(frame, lsb_first=True))
    return bytes(AX25_FLAG_BITS) + region[drop_leading_bits:] + bytes(AX25_FLAG_BITS)


def _g3ruh_levels(decoded_bits: bytes) -> np.ndarray:
    scrambled = g3ruh_input_for_plain_bits(decoded_bits)
    levels = nrzi_encode_satnogs(scrambled, initial_level=0)
    return np.frombuffer(levels, dtype=np.uint8)


def test_four_bit_left_padding_reproduces_observation_4704_pdu() -> None:
    # The official clock dump contains 108 unstuffed bits between flags.  GNU
    # prepends four zeros before packing, obtaining this 14-byte frame + FCS.
    candidates = extract_gr_satellites_hdlc_pdus(
        _decoded_stream(OBSERVATION_4704_PDU, drop_leading_bits=4)
    )

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.pdu == OBSERVATION_4704_PDU
    assert candidate.frame_with_fcs.hex() == "6073d927ea3ae8157bfd2b6089bb"
    assert candidate.classification == "crc_valid_undersized_pdu"
    assert candidate.undersized_for_ax25 is True
    assert candidate.strict_ax25_ui is None
    assert candidate.provenance.adapter_schema_version == ADAPTER_SCHEMA_VERSION
    assert candidate.provenance.raw_unstuffed_bit_count == 108
    assert candidate.provenance.left_padding_bits == 4
    assert candidate.provenance.fcs_checked is True
    assert candidate.provenance.fcs_trimmed_from_pdu is True


def test_full_g3ruh_pipeline_accepts_numpy_scalars_and_preserves_strict_ax25() -> None:
    decoded = _decoded_stream(STRICT_AX25_UI)
    candidates = decode_gr_satellites_g3ruh_levels(_g3ruh_levels(decoded))

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.pdu == STRICT_AX25_UI
    assert candidate.classification == "strict_ax25_ui"
    assert candidate.undersized_for_ax25 is False
    assert candidate.strict_ax25_ui is not None
    assert candidate.strict_ax25_ui.destination.callsign == "JS1YPA"
    assert candidate.strict_ax25_ui.source.callsign == "JS1YOY"
    assert candidate.provenance.left_padding_bits == 0
    assert candidate.provenance.pipeline[:2] == (
        "nrzi_decode(initial_level=0,unchanged=1)",
        "descrambler_bb(mask=0x21,seed=0,length=16)",
    )


def test_crc_valid_aligned_non_ax25_pdu_is_not_promoted_to_ax25() -> None:
    payload = b"not-an-ax25-pdu"
    assert len(payload) >= 14
    (candidate,) = extract_gr_satellites_hdlc_pdus(_decoded_stream(payload))

    assert candidate.pdu == payload
    assert candidate.classification == "crc_valid_non_ax25_pdu"
    assert candidate.undersized_for_ax25 is False
    assert candidate.strict_ax25_ui is None


def test_bad_fcs_is_silently_not_emitted_like_gnu_deframer() -> None:
    frame = bytearray(append_ax25_fcs(STRICT_AX25_UI))
    frame[-1] ^= 0x80
    region = stuff_hdlc_bits(bytes_to_bits(bytes(frame), lsb_first=True))
    stream = bytes(AX25_FLAG_BITS) + region + bytes(AX25_FLAG_BITS)

    assert extract_gr_satellites_hdlc_pdus(stream) == ()


def test_two_byte_crc_of_empty_payload_is_rejected_like_gnu_crc_checker() -> None:
    decoded = _decoded_stream(b"")
    assert extract_gr_satellites_hdlc_pdus(decoded) == ()


@pytest.mark.parametrize("bad", [(0, 2), (0, -1), (0, 0.0)])
def test_bit_inputs_must_be_integer_zero_or_one(bad: tuple[object, ...]) -> None:
    with pytest.raises(ValueError):
        extract_gr_satellites_hdlc_pdus(bad)


@pytest.mark.parametrize("bad", [0, -1, True, 1.5])
def test_max_length_is_bounded_positive_integer(bad: object) -> None:
    with pytest.raises(ValueError):
        extract_gr_satellites_hdlc_pdus((), max_length=bad)  # type: ignore[arg-type]
