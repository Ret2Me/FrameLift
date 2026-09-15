from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

import telemetry_yield.afsk1200_plugin as afsk_module
from telemetry_yield.afsk1200_plugin import (
    Afsk1200Config,
    decode_afsk1200_ci16le,
    decode_afsk1200_iq,
)
from telemetry_yield.camras_replay import bytes_to_bits
from telemetry_yield.clock_recovery import HDLC_FLAG
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


def _nrzi(bits: tuple[int, ...], initial: int = 0) -> tuple[int, ...]:
    level = initial
    output: list[int] = []
    for bit in bits:
        if not bit:
            level ^= 1
        output.append(level)
    return tuple(output)


def _synthetic_afsk_iq(frame_without_fcs: bytes = REFERENCE) -> np.ndarray:
    frame = append_ax25_fcs(frame_without_fcs)
    payload = _stuff(bytes_to_bits(frame, lsb_first=True))
    bits = HDLC_FLAG * 24 + (payload + HDLC_FLAG * 8) * 5
    levels = np.asarray(_nrzi(bits), dtype=np.uint8)
    samples_per_symbol = 48
    frequencies = np.repeat(
        np.where(levels > 0, 1_200.0, 2_200.0), samples_per_symbol
    )
    audio_phase = 2.0 * np.pi * np.cumsum(frequencies) / 57_600.0
    audio = 0.75 * np.sin(audio_phase)
    rf_phase = 2.0 * np.pi * np.cumsum(3_000.0 * audio) / 57_600.0
    rng = np.random.default_rng(20_260_902)
    noise = 0.035 * (
        rng.normal(size=rf_phase.size) + 1j * rng.normal(size=rf_phase.size)
    )
    return np.asarray(np.exp(1j * rf_phase) + noise, dtype=np.complex64)


class Afsk1200PluginTests(unittest.TestCase):
    def test_independent_tone_energy_path_recovers_strict_ax25(self) -> None:
        result = decode_afsk1200_iq(_synthetic_afsk_iq())
        self.assertIn(REFERENCE, {frame.normalized_pdu for frame in result.frames})
        self.assertGreater(result.timing_hypotheses_examined, 0)
        self.assertGreater(result.crc_valid_candidates, 0)

    def test_all_zero_iq_is_fail_closed(self) -> None:
        result = decode_afsk1200_iq(np.zeros(20_000, dtype=np.complex64))
        self.assertEqual(result.frames, ())
        self.assertEqual(result.degenerate_windows, 1)

    def test_crc_valid_non_ax25_payload_is_not_counted_as_telemetry(self) -> None:
        result = decode_afsk1200_iq(_synthetic_afsk_iq(b"not-an-ax25-ui-frame"))
        self.assertEqual(result.frames, ())
        self.assertGreater(result.crc_valid_candidates, 0)
        self.assertGreater(result.strict_ax25_rejections, 0)

    def test_file_permutation_control_is_deterministic_and_strict_zero(self) -> None:
        iq = _synthetic_afsk_iq()
        scaled = np.empty((iq.size, 2), dtype="<i2")
        scaled[:, 0] = np.clip(np.real(iq) * 12_000, -32_768, 32_767).astype("<i2")
        scaled[:, 1] = np.clip(np.imag(iq) * 12_000, -32_768, 32_767).astype("<i2")
        config = Afsk1200Config(window_blocks=8, stride_blocks=4)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "synthetic.iq"
            scaled.tofile(path)
            first = decode_afsk1200_ci16le(
                path,
                config=config,
                control="deterministic_sample_permutation",
            )
            second = decode_afsk1200_ci16le(
                path,
                config=config,
                control="deterministic_sample_permutation",
            )
        self.assertEqual(first, second)
        self.assertEqual(first.frames, ())

    def test_wrong_baud_does_not_validate_synthetic_1200_baud_frame(self) -> None:
        config = Afsk1200Config(baudrate=2_400.0)
        result = decode_afsk1200_iq(_synthetic_afsk_iq(), config=config)
        self.assertEqual(result.frames, ())

    def test_file_decoder_never_materializes_the_complete_memmap(self) -> None:
        config = Afsk1200Config(window_blocks=4, stride_blocks=2)
        window_samples = config.window_blocks * config.permutation_block_samples
        sample_count = 3 * window_samples
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sentinel.iq"
            path.write_bytes(b"\x00" * (sample_count * 4))
            for control in ("identity", "deterministic_sample_permutation"):
                mappings: list[tuple[int, tuple[int, int]]] = []

                def bounded_memmap(_path, *, dtype, mode, offset, shape):
                    self.assertEqual(dtype, "<i2")
                    self.assertEqual(mode, "r")
                    self.assertEqual(offset % 4, 0)
                    self.assertLess(shape[0], sample_count)
                    self.assertEqual(shape[1], 2)
                    mappings.append((offset, shape))
                    return np.zeros(shape, dtype="<i2")

                empty = afsk_module.AfskDecodeResult((), 1, 0, 0, 0, 1)
                with (
                    mock.patch.object(
                        afsk_module.np, "memmap", side_effect=bounded_memmap
                    ),
                    mock.patch.object(
                        afsk_module, "decode_afsk1200_iq", return_value=empty
                    ),
                ):
                    result = decode_afsk1200_ci16le(
                        path, config=config, control=control
                    )
                self.assertGreater(result.windows_examined, 1)
                self.assertTrue(mappings)
                expected_limit = (
                    config.permutation_block_samples
                    if control == "deterministic_sample_permutation"
                    else window_samples
                )
                self.assertLessEqual(max(shape[0] for _, shape in mappings), expected_limit)
                self.assertGreater(len({offset for offset, _ in mappings}), 1)


if __name__ == "__main__":
    unittest.main()
