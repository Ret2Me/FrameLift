from __future__ import annotations

from pathlib import Path
import tempfile
from unittest.mock import patch

import numpy as np
import pytest

from telemetry_yield.camras_replay import bytes_to_bits
from telemetry_yield.clock_recovery import HDLC_FLAG, g3ruh_scramble_bits
from telemetry_yield.crc import append_ax25_fcs
from telemetry_yield.fsk_ax25_plugin import (
    FskAx25Config,
    ci16le_window_spans,
    decode_fsk_ax25_iq,
    iter_fsk_ax25_ci16le_windows,
)


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


def _nrzi_encode(bits: tuple[int, ...], initial: int = 0) -> tuple[int, ...]:
    level = initial
    output: list[int] = []
    for bit in bits:
        if bit == 0:
            level ^= 1
        output.append(level)
    return tuple(output)


def _synthetic_iq(*, g3ruh: bool) -> np.ndarray:
    frame = append_ax25_fcs(REFERENCE)
    payload = _stuff(bytes_to_bits(frame, lsb_first=True))
    plain = HDLC_FLAG * 40 + (payload + HDLC_FLAG * 5) * 8
    bits = g3ruh_scramble_bits(plain) if g3ruh else plain
    levels = _nrzi_encode(bits)
    sample_rate = 57_600
    baudrate = 9_600
    frequency = 6_500.0 + 2_400.0 * (
        2.0 * np.asarray(levels, dtype=np.float64) - 1.0
    )
    frequency = np.repeat(frequency, sample_rate // baudrate)
    phase = 2.0 * np.pi * np.cumsum(frequency) / sample_rate
    rng = np.random.default_rng(20260902)
    noise = 0.12 * (rng.normal(size=phase.size) + 1j * rng.normal(size=phase.size))
    return np.exp(1j * phase) + noise


@pytest.mark.parametrize("g3ruh", [False, True])
def test_fsk_plugin_recovers_strict_ax25_with_or_without_scrambler(g3ruh: bool) -> None:
    config = FskAx25Config(
        sample_rate_hz=57_600,
        baudrate=9_600,
        decimation=3,
        g3ruh=g3ruh,
        timing_rate_errors_ppm=(0.0,),
    )
    result = decode_fsk_ax25_iq(_synthetic_iq(g3ruh=g3ruh), config=config)
    assert REFERENCE in {frame.normalized_pdu for frame in result.frames}
    assert result.crc_valid_candidates >= 1
    assert result.degenerate is False


def test_fsk_plugin_zero_window_fails_closed() -> None:
    result = decode_fsk_ax25_iq(np.zeros(48_000, dtype=np.complex64))
    assert result.frames == ()
    assert result.rejected_frames == ()
    assert result.degenerate is True
    assert result.failure is None


def test_fsk_plugin_nonzero_constant_window_fails_closed_before_dsp() -> None:
    iq = np.full(48_000, 123.0 - 45.0j, dtype=np.complex64)
    with patch(
        "telemetry_yield.fsk_ax25_plugin.deterministic_fsk_demodulate"
    ) as demodulate:
        result = decode_fsk_ax25_iq(iq)
    demodulate.assert_not_called()
    assert result.frames == ()
    assert result.rejected_frames == ()
    assert result.degenerate is True
    assert result.failure is None


def test_ci16_window_iterator_is_bounded_and_checks_boundaries() -> None:
    config = FskAx25Config(
        sample_rate_hz=100,
        baudrate=10,
        decimation=2,
        window_seconds=2.0,
        hop_seconds=1.0,
        timing_rate_errors_ppm=(0.0,),
        timing_phase_bins=4,
        timing_top_n=1,
    )
    samples = np.arange(550, dtype=np.int16).reshape(275, 2)
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "capture.raw"
        path.write_bytes(samples.astype("<i2").tobytes())
        spans = ci16le_window_spans(path, config=config)
        assert spans == ((0, 200), (75, 275))
        with patch(
            "telemetry_yield.fsk_ax25_plugin.np.memmap", wraps=np.memmap
        ) as mapped:
            windows = tuple(iter_fsk_ax25_ci16le_windows(path, config=config))
        assert [(start, stop, len(iq)) for start, stop, iq in windows] == [
            (0, 200, 200),
            (75, 275, 200),
        ]
        assert len(mapped.call_args_list) == 2
        assert all(call.kwargs["shape"] == (200, 2) for call in mapped.call_args_list)
        assert all("offset" in call.kwargs for call in mapped.call_args_list)
        with pytest.raises(ValueError, match="unknown boundary"):
            tuple(
                iter_fsk_ax25_ci16le_windows(
                    path, config=config, skip_start_samples=(1,)
                )
            )


def test_fsk_config_rejects_nonintegral_decimation() -> None:
    with pytest.raises(ValueError, match="divide"):
        FskAx25Config(sample_rate_hz=48_000, decimation=7)
