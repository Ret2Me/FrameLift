"""Reproducible helpers for the CAMRAS RSP-03 IQ replay follow-up.

The SatNOGS ``demoddata`` artifacts are decoded AX.25 frames.  They are not
raw HDLC byte streams, so comparisons in this module only remove a layer when
there is positive evidence for it: flag octets must bracket the candidate and
an FCS is removed only when CRC-16/X.25 validates.  Prefix, suffix, and
subsequence matches are deliberately unsupported.

GNU Radio is imported lazily by :func:`run_satnogs_fsk_replay`.  This keeps the
normalizers usable in the project's dependency-free test environment while
allowing a system GNU Radio installation to reconstruct the downstream half
of the official SatNOGS FSK flowgraph.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

from .crc import validate_ax25_fcs


HDLC_FLAG = (0, 1, 1, 1, 1, 1, 1, 0)


class ReplayError(RuntimeError):
    """Raised when a replay input or normalization is malformed."""


@dataclass(frozen=True)
class NormalizedFrame:
    """One complete candidate after named, lossless protocol layers."""

    layers: tuple[str, ...]
    payload: bytes

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.payload).hexdigest()


@dataclass(frozen=True)
class ExactFrameMatch:
    """An equality between a complete normalized candidate and reference."""

    candidate_index: int
    reference_index: int
    layers: tuple[str, ...]
    sha256: str


@dataclass(frozen=True)
class FSKReplayParameters:
    """Derived parameters from SatNOGS flowgraphs 2.0 ``generic/fsk.grc``."""

    baudrate: int
    audio_sample_rate_hz: int
    decimation: int
    iq_sample_rate_hz: int
    demod_sample_rate_hz: int
    samples_per_symbol: float


@dataclass(frozen=True)
class FSKReplayResult:
    """Outputs from the reconstructed downstream SatNOGS receiver."""

    parameters: FSKReplayParameters
    swap_components: bool
    invert_symbols: bool
    sliced_bit_count: int
    sliced_bits_sha256: str
    g3ruh_frames: tuple[bytes, ...]
    plain_ax25_frames: tuple[bytes, ...]


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def reverse_bits_per_byte(value: bytes) -> bytes:
    """Reverse bit significance within every octet, preserving octet order."""

    return bytes(int(f"{byte:08b}"[::-1], 2) for byte in value)


def bytes_to_bits(value: bytes, *, lsb_first: bool) -> tuple[int, ...]:
    """Expand octets to an explicit bit sequence."""

    shifts = range(8) if lsb_first else range(7, -1, -1)
    return tuple((byte >> shift) & 1 for byte in value for shift in shifts)


def bits_to_bytes(bits: Sequence[int], *, lsb_first: bool) -> bytes:
    """Pack an aligned explicit bit sequence into octets."""

    checked = _checked_bits(bits)
    if len(checked) % 8:
        raise ReplayError("bit sequence is not octet-aligned")
    output = bytearray()
    for offset in range(0, len(checked), 8):
        chunk = checked[offset : offset + 8]
        if lsb_first:
            output.append(sum(bit << index for index, bit in enumerate(chunk)))
        else:
            output.append(sum(bit << (7 - index) for index, bit in enumerate(chunk)))
    return bytes(output)


def nrzi_decode(
    levels: Sequence[int], *, initial_level: int
) -> tuple[int, ...]:
    """Decode standard HDLC NRZI (transition=0, no transition=1)."""

    checked = _checked_bits(levels)
    if initial_level not in (0, 1):
        raise ValueError("initial_level must be zero or one")
    previous = initial_level
    decoded: list[int] = []
    for level in checked:
        decoded.append(1 if level == previous else 0)
        previous = level
    return tuple(decoded)


def hdlc_unstuff(bits: Sequence[int]) -> tuple[int, ...]:
    """Remove HDLC zero stuffing from bits between two flags.

    A sixth consecutive one is an abort/invalid sequence and is rejected.
    """

    checked = _checked_bits(bits)
    output: list[int] = []
    ones = 0
    for bit in checked:
        if bit:
            ones += 1
            if ones > 5:
                raise ReplayError("invalid HDLC run of six ones between flags")
            output.append(bit)
            continue
        if ones == 5:
            # This is the stuffed zero.  Reset the transmitted run counter,
            # but do not let the five retained data ones make a later data
            # zero look stuffed as well.
            ones = 0
            continue
        output.append(bit)
        ones = 0
    return tuple(output)


def extract_hdlc_frames_from_nrzi_levels(
    levels: Sequence[int], *, initial_level: int
) -> tuple[bytes, ...]:
    """Decode NRZI, split complete flag-delimited frames, and unstuff bits.

    Returned frames retain their FCS.  Invalid or non-octet-aligned regions
    between flags are ignored, never truncated into a partial frame.
    """

    decoded = nrzi_decode(levels, initial_level=initial_level)
    flag_offsets = tuple(
        index
        for index in range(len(decoded) - len(HDLC_FLAG) + 1)
        if decoded[index : index + len(HDLC_FLAG)] == HDLC_FLAG
    )
    frames: list[bytes] = []
    for left, right in zip(flag_offsets, flag_offsets[1:]):
        if right < left + len(HDLC_FLAG):
            continue
        region = decoded[left + len(HDLC_FLAG) : right]
        if not region:
            continue
        try:
            unstuffed = hdlc_unstuff(region)
            frame = bits_to_bytes(unstuffed, lsb_first=True)
        except ReplayError:
            continue
        if frame:
            frames.append(frame)
    return tuple(frames)


def strip_hdlc_flag_octets(value: bytes) -> bytes | None:
    """Remove one or more complete 0x7e octets from both ends.

    ``None`` means that the candidate was not a complete, byte-aligned HDLC
    frame.  One-sided flag stripping is intentionally not performed.
    """

    left = 0
    while left < len(value) and value[left] == 0x7E:
        left += 1
    right = len(value)
    while right > left and value[right - 1] == 0x7E:
        right -= 1
    if left == 0 or right == len(value) or left == right:
        return None
    return value[left:right]


def exact_normalizations(value: bytes) -> tuple[NormalizedFrame, ...]:
    """Enumerate explicit whole-frame AX.25/HDLC normalization layers."""

    variants: list[NormalizedFrame] = []
    seen: set[tuple[tuple[str, ...], bytes]] = set()

    def add(layers: tuple[str, ...], payload: bytes) -> None:
        key = (layers, payload)
        if key not in seen:
            variants.append(NormalizedFrame(layers, payload))
            seen.add(key)

    for bit_layers, bit_value in (
        ((), value),
        (("reverse_bits_per_byte",), reverse_bits_per_byte(value)),
    ):
        add(bit_layers or ("identity",), bit_value)
        candidates = [(bit_layers, bit_value)]
        unflagged = strip_hdlc_flag_octets(bit_value)
        if unflagged is not None:
            flag_layers = bit_layers + ("strip_hdlc_flag_octets",)
            add(flag_layers, unflagged)
            candidates.append((flag_layers, unflagged))
        for layers, candidate in candidates:
            if validate_ax25_fcs(candidate):
                add(layers + ("strip_valid_ax25_fcs",), candidate[:-2])
    return tuple(variants)


def compare_complete_frames(
    candidates: Iterable[bytes], references: Sequence[bytes]
) -> tuple[ExactFrameMatch, ...]:
    """Compare only byte-identical complete normalized frames."""

    matches: list[ExactFrameMatch] = []
    for candidate_index, candidate in enumerate(candidates):
        for normalized in exact_normalizations(candidate):
            for reference_index, reference in enumerate(references):
                if normalized.payload == reference:
                    matches.append(
                        ExactFrameMatch(
                            candidate_index=candidate_index,
                            reference_index=reference_index,
                            layers=normalized.layers,
                            sha256=normalized.sha256,
                        )
                    )
    return tuple(matches)


def satnogs_fsk_parameters(
    baudrate: int, *, audio_sample_rate_hz: int = 48_000
) -> FSKReplayParameters:
    """Reproduce ``find_decimation(baudrate, 2, audio_rate)`` from SatNOGS."""

    if baudrate <= 0 or audio_sample_rate_hz <= 0:
        raise ValueError("sample rates must be positive")
    decimation = 2
    while decimation * baudrate < audio_sample_rate_hz:
        decimation += 1
    if decimation % 2:
        decimation += 1
    iq_rate = baudrate * decimation
    demod_rate = iq_rate // (decimation // 2)
    return FSKReplayParameters(
        baudrate=baudrate,
        audio_sample_rate_hz=audio_sample_rate_hz,
        decimation=decimation,
        iq_sample_rate_hz=iq_rate,
        demod_sample_rate_hz=demod_rate,
        samples_per_symbol=demod_rate / baudrate,
    )


def run_satnogs_fsk_replay(
    input_path: str | Path,
    sliced_bits_path: str | Path,
    *,
    baudrate: int = 9_600,
    swap_components: bool = False,
    invert_symbols: bool = False,
) -> FSKReplayResult:
    """Run the downstream DSP of SatNOGS flowgraphs 2.0 ``generic/fsk``.

    The input contract is the flowgraph's own doppler-corrected, interleaved
    complex-int16 dump.  The final frame layer uses gr-satellites' AX.25
    deframer in both G3RUH and plain modes because ``gr-satnogs`` is not an
    Ubuntu package in the replay environment.  This substitution is surfaced
    in the report and does not claim decoder identity.
    """

    try:
        import pmt
        from gnuradio import analog, blocks, digital, filter as gr_filter, gr
        from gnuradio.filter import firdes
        from gnuradio.fft import window
        from satellites.components.deframers.ax25_deframer import ax25_deframer
    except ImportError as exc:  # pragma: no cover - optional runtime
        raise ReplayError(
            "GNU Radio plus gr-satellites is required for IQ replay"
        ) from exc

    source_path = Path(input_path)
    if (
        not source_path.is_file()
        or source_path.stat().st_size == 0
        or source_path.stat().st_size % 4
    ):
        raise ReplayError("input must be a non-empty interleaved complex-int16 file")
    bit_path = Path(sliced_bits_path)
    bit_path.parent.mkdir(parents=True, exist_ok=True)
    parameters = satnogs_fsk_parameters(baudrate)
    if parameters.decimation // 2 < 1:
        raise ReplayError("invalid SatNOGS decimation")

    top = gr.top_block("camras_rsp03_satnogs_fsk_replay")
    source = blocks.file_source(gr.sizeof_short, str(source_path), False)
    to_complex = blocks.interleaved_short_to_complex(
        False, swap_components, 16_384.0
    )

    relaxed_taps = firdes.low_pass(
        1.0,
        parameters.iq_sample_rate_hz,
        1.25 * baudrate,
        baudrate / 2.0,
        window.WIN_HAMMING,
        6.76,
    )
    relaxed_filter = gr_filter.fir_filter_ccf(1, relaxed_taps)
    afc_demod = analog.quadrature_demod_cf(1.0)
    afc_average = blocks.moving_average_ff(1_024, 1.0 / 1_024.0, 4_096)
    afc_vco = blocks.vco_c(
        parameters.iq_sample_rate_hz,
        -parameters.iq_sample_rate_hz,
        1.0,
    )
    signal_delay = blocks.delay(gr.sizeof_gr_complex, 512)
    correct_frequency = blocks.multiply_cc()

    channel_taps = firdes.low_pass(
        1.0,
        parameters.iq_sample_rate_hz,
        0.625 * baudrate,
        baudrate / 8.0,
        window.WIN_HAMMING,
        6.76,
    )
    channel_filter = gr_filter.fir_filter_ccf(
        parameters.decimation // 2, channel_taps
    )
    fm_demod = analog.quadrature_demod_cf(1.2)
    dc_block = gr_filter.dc_blocker_ff(1_024, True)
    clock = digital.clock_recovery_mm_ff(
        2.0,
        2.0 * math.pi / 100.0,
        0.5,
        0.5 / 8.0,
        0.01,
    )
    symbol_stream = clock
    if invert_symbols:
        symbol_stream = blocks.multiply_const_ff(-1.0)

    slicer = digital.binary_slicer_fb()
    bit_sink = blocks.file_sink(gr.sizeof_char, str(bit_path), False)
    g3ruh = ax25_deframer(True)
    plain = ax25_deframer(False)
    g3ruh_messages = blocks.message_debug()
    plain_messages = blocks.message_debug()

    top.connect(source, to_complex)
    top.connect(to_complex, relaxed_filter, afc_demod, afc_average, afc_vco)
    top.connect(to_complex, signal_delay, (correct_frequency, 0))
    top.connect(afc_vco, (correct_frequency, 1))
    top.connect(correct_frequency, channel_filter, fm_demod, dc_block, clock)
    if invert_symbols:
        top.connect(clock, symbol_stream)
    top.connect(symbol_stream, slicer, bit_sink)
    top.connect(symbol_stream, g3ruh)
    top.connect(symbol_stream, plain)
    top.msg_connect((g3ruh, "out"), (g3ruh_messages, "store"))
    top.msg_connect((plain, "out"), (plain_messages, "store"))
    # A fixed scheduler work quantum makes the stateful M&M synchronizer
    # replayable across runs; GNU Radio's very large default permits varying
    # buffer partitions in this multi-output diagnostic graph.
    top.run(max_noutput_items=8_192)

    def stored_frames(debug) -> tuple[bytes, ...]:
        frames: list[bytes] = []
        for index in range(debug.num_messages()):
            message = debug.get_message(index)
            payload = pmt.cdr(message) if pmt.is_pair(message) else message
            if not pmt.is_u8vector(payload):
                raise ReplayError("AX.25 deframer emitted a non-u8vector PDU")
            frames.append(bytes(pmt.u8vector_elements(payload)))
        return tuple(frames)

    bit_data = bit_path.read_bytes()
    return FSKReplayResult(
        parameters=parameters,
        swap_components=swap_components,
        invert_symbols=invert_symbols,
        sliced_bit_count=len(bit_data),
        sliced_bits_sha256=sha256_bytes(bit_data),
        g3ruh_frames=stored_frames(g3ruh_messages),
        plain_ax25_frames=stored_frames(plain_messages),
    )


def _checked_bits(bits: Sequence[int]) -> list[int]:
    checked = list(bits)
    if any(bit not in (0, 1) for bit in checked):
        raise ValueError("bits must contain only zero and one")
    return checked
