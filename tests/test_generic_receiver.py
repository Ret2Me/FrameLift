from __future__ import annotations

import unittest

import numpy as np

from telemetry_yield.camras_replay import bits_to_bytes, bytes_to_bits
from telemetry_yield.clock_recovery import HDLC_FLAG, g3ruh_scramble_bits
from telemetry_yield.crc import append_ax25_fcs
from telemetry_yield.generic_receiver import (
    Ax25ProtocolDecoder,
    Ax25CcsdsProtocolDecoder,
    ChannelConditionedPhaseFskDemodulator,
    DemodulatorCapabilities,
    DemodulatedSignal,
    FixedSyncFrameDecoder,
    GenericReceiver,
    GenericSatelliteReceiver,
    RawCcsdsSpacePacketDecoder,
    ReceiverHypothesis,
    WaveformHypothesis,
    default_receiver,
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
    previous = initial
    output: list[int] = []
    for bit in bits:
        if bit == 0:
            previous ^= 1
        output.append(previous)
    return tuple(output)


def _ax25_address(callsign: str, *, final: bool) -> bytes:
    padded = callsign.ljust(6)
    return bytes(ord(character) << 1 for character in padded) + bytes(
        (0x60 | int(final),)
    )


def _plain_ax25_levels(frame: bytes) -> tuple[int, ...]:
    bits = HDLC_FLAG + _stuff(bytes_to_bits(frame, lsb_first=True)) + HDLC_FLAG
    return _nrzi_encode(bits)


def _synthetic_g3ruh_levels(frame: bytes) -> tuple[int, ...]:
    payload = _stuff(bytes_to_bits(frame, lsb_first=True))
    plain = HDLC_FLAG * 40 + (payload + HDLC_FLAG * 5) * 8
    return _nrzi_encode(g3ruh_scramble_bits(plain))


def _fsk_iq(
    levels: tuple[int, ...],
    *,
    sample_rate_hz: int,
    symbol_rate: int,
    carrier_offset_hz: float,
    deviation_hz: float,
    noise_standard_deviation: float,
    adjacent_tone_amplitude: float = 0.0,
) -> np.ndarray:
    samples_per_symbol = sample_rate_hz // symbol_rate
    frequency_hz = carrier_offset_hz + deviation_hz * (
        2.0 * np.asarray(levels, dtype=np.float64) - 1.0
    )
    frequency_hz = np.repeat(frequency_hz, samples_per_symbol)
    phase = 2.0 * np.pi * np.cumsum(frequency_hz) / sample_rate_hz
    rng = np.random.default_rng(20260902)
    noise = noise_standard_deviation * (
        rng.normal(size=phase.size) + 1j * rng.normal(size=phase.size)
    )
    time_samples = np.arange(phase.size, dtype=np.float64)
    adjacent = adjacent_tone_amplitude * np.exp(
        2j * np.pi * 22_000.0 * time_samples / sample_rate_hz
    )
    return np.exp(1j * phase) + adjacent + noise


class GenericReceiverTests(unittest.TestCase):
    def test_fixed_sync_adapter_is_satellite_and_catalogue_neutral(self) -> None:
        sync = (0, 0, 0, 1, 1, 0, 1, 0)
        payload = bytes.fromhex("1ac0ffee")
        frame_bits = bytes_to_bits(payload, lsb_first=False)
        stream = (0, 1) * 200 + frame_bits + (1, 0) * 200
        waveform = np.repeat(2.0 * np.asarray(stream) - 1.0, 2)
        receiver = GenericSatelliteReceiver()
        receiver.register_protocol(
            FixedSyncFrameDecoder(
                protocol_id="mission_independent_sync",
                syncword=sync,
                frame_bits=32,
                validator=lambda frame: frame == payload,
                validation_name="test_crc",
            )
        )
        result = receiver.decode_demodulated(
            DemodulatedSignal(
                samples=tuple(waveform),
                samples_per_symbol=2.0,
                metrics={},
            ),
            waveform=WaveformHypothesis(
                hypothesis_id="unknown_satellite_1200",
                demodulator_id="unused_for_demodulated_input",
                symbol_rate=1200,
                decimation=2,
                rate_errors_ppm=(0.0,),
                phase_bins=8,
                top_timing_hypotheses=2,
            ),
            protocol_id="mission_independent_sync",
        )
        self.assertEqual({frame.payload for frame in result.frames}, {payload})
        self.assertEqual(result.frames[0].validation, "test_crc")

    def test_syncword_alone_never_accepts_a_frame(self) -> None:
        decoder = FixedSyncFrameDecoder(
            protocol_id="strict",
            syncword=(1, 0, 1, 0, 1, 0, 1, 0),
            frame_bits=16,
            validator=lambda _: False,
        )
        soft = tuple(
            1.0 if bit else -1.0
            for bit in (1, 0, 1, 0, 1, 0, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0)
        )
        self.assertEqual(decoder.decode(soft, threshold=0.0), ())

    def test_bits_to_bytes_fixture_is_unambiguous(self) -> None:
        payload = bytes.fromhex("1ac0ffee")
        self.assertEqual(
            bits_to_bytes(bytes_to_bits(payload, lsb_first=False), lsb_first=False),
            payload,
        )

    def test_default_capabilities_are_physical_and_protocol_neutral(self) -> None:
        capabilities = default_receiver().capabilities

        self.assertEqual(
            capabilities.demodulators["phase_fsk"].modulation_families,
            ("fsk", "gfsk", "gmsk"),
        )
        conditioned = capabilities.demodulators[
            "channel_conditioned_phase_fsk"
        ]
        self.assertEqual(
            conditioned.modulation_families,
            ("fsk",),
        )
        self.assertIn("complex_channel_filter", conditioned.features)
        self.assertIn("configurable_sample_rate", conditioned.features)
        self.assertIn("configurable_symbol_rate", conditioned.features)
        self.assertIn("configurable_decimation", conditioned.features)
        self.assertEqual(
            capabilities.protocols["ax25_ccsds_space_packet"].protocol_stack,
            ("ax25", "ccsds_space_packet"),
        )
        self.assertNotIn("satnogs", capabilities.demodulators)

    def test_incompatible_modulation_is_rejected_before_dsp(self) -> None:
        receiver = default_receiver()
        plan = (
            ReceiverHypothesis(
                waveform=WaveformHypothesis(
                    hypothesis_id="qpsk_is_not_phase_fsk",
                    demodulator_id="phase_fsk",
                    symbol_rate=9_600,
                    decimation=3,
                    modulation_family="qpsk",
                ),
                protocol_id="ax25",
            ),
        )

        with self.assertRaisesRegex(ValueError, "does not advertise.*qpsk"):
            receiver.validate_plan(plan)
        result = receiver.decode_iq((0j,), sample_rate_hz=57_600, plan=plan)
        self.assertEqual(result.attempted_timing_hypotheses, 0)
        self.assertEqual(result.frames, ())
        self.assertIn("does not advertise modulation family qpsk", result.failures[0])

    def test_incompatible_symbol_contract_is_rejected_before_demodulation(self) -> None:
        class MustNotRun:
            def demodulate(self, *args: object, **kwargs: object) -> DemodulatedSignal:
                raise AssertionError("incompatible plugin must not be invoked")

        receiver = GenericReceiver()
        receiver.register_demodulator(
            "constellation_plugin",
            MustNotRun(),  # type: ignore[arg-type]
            capabilities=DemodulatorCapabilities(
                modulation_families=("qpsk",),
                output_symbol_kind="complex_constellation_symbols",
            ),
        )
        receiver.register_protocol(
            FixedSyncFrameDecoder(
                protocol_id="binary_frames",
                syncword=(1, 0, 1, 0, 1, 0, 1, 0),
                frame_bits=16,
                validator=lambda _: False,
            )
        )
        plan = (
            ReceiverHypothesis(
                waveform=WaveformHypothesis(
                    hypothesis_id="wrong_symbol_boundary",
                    demodulator_id="constellation_plugin",
                    symbol_rate=1_200,
                    decimation=1,
                    modulation_family="qpsk",
                ),
                protocol_id="binary_frames",
            ),
        )

        result = receiver.decode_iq((0j,), sample_rate_hz=48_000, plan=plan)
        self.assertEqual(result.frames, ())
        self.assertIn("complex_constellation_symbols", result.failures[0])

    def test_raw_ccsds_requires_and_applies_external_integrity_policy(self) -> None:
        packet = bytes.fromhex("0820d5bf0003") + b"data"
        stream = (1, 0, 1, 0, 1) + bytes_to_bits(packet, lsb_first=False) + (0, 1)
        soft = tuple(1.0 if bit else -1.0 for bit in stream)
        decoder = RawCcsdsSpacePacketDecoder(
            packet_validator=lambda candidate, header: (
                candidate == packet and header.apid == 32
            ),
            validation_name="external_integrity",
            allowed_apids=(32,),
        )

        self.assertEqual(
            decoder.decode(soft, threshold=0.0),
            ((packet, "ccsds_primary_header+external_integrity"),),
        )
        rejecting = RawCcsdsSpacePacketDecoder(
            packet_validator=lambda _candidate, _header: False,
            validation_name="external_integrity",
        )
        self.assertEqual(rejecting.decode(soft, threshold=0.0), ())

    def test_ax25_ccsds_adapter_validates_both_protocol_layers(self) -> None:
        packet = bytes.fromhex("0820d5bf0003") + b"data"
        ax25_payload = (
            _ax25_address("CANVAS", final=False)
            + _ax25_address("LASP", final=True)
            + b"\x03\xf0"
            + packet
        )
        valid_frame = append_ax25_fcs(ax25_payload)
        valid_soft = tuple(
            1.0 if bit else -1.0 for bit in _plain_ax25_levels(valid_frame)
        )
        decoder = Ax25CcsdsProtocolDecoder(
            g3ruh_modes=(False,),
            allowed_apids=(32,),
        )

        decoded = decoder.decode(valid_soft, threshold=0.0)
        self.assertEqual(decoded[0][0], valid_frame)
        self.assertEqual(
            decoded[0][1],
            "crc16_x25+ax25_ui+ccsds_primary_header",
        )

        truncated_packet = bytes.fromhex("0820d5bf0003") + b"dat"
        invalid_inner_frame = append_ax25_fcs(
            ax25_payload[:16] + truncated_packet
        )
        invalid_soft = tuple(
            1.0 if bit else -1.0
            for bit in _plain_ax25_levels(invalid_inner_frame)
        )
        self.assertEqual(decoder.decode(invalid_soft, threshold=0.0), ())

    def test_ax25_adapter_rejects_a_short_crc_collision(self) -> None:
        crc_only = append_ax25_fcs(bytes.fromhex("e0470c2aab79e4"))
        invalid_soft = tuple(
            1.0 if bit else -1.0 for bit in _plain_ax25_levels(crc_only)
        )
        decoder = Ax25ProtocolDecoder(g3ruh_modes=(False,))

        self.assertEqual(decoder.decode(invalid_soft, threshold=0.0), ())
        self.assertEqual(
            decoder.capabilities.validation_layers,
            ("crc16_x25", "ax25_ui"),
        )

    def test_channel_conditioned_plugin_decodes_ax25_and_is_additive(self) -> None:
        frame_without_fcs = bytes.fromhex(
            "94a662b2a0826094a662b29eb2e103f00018ad8001020304"
        )
        frame = append_ax25_fcs(frame_without_fcs)
        sample_rate_hz = 57_600
        symbol_rate = 9_600
        levels = _synthetic_g3ruh_levels(frame)
        iq = _fsk_iq(
            levels,
            sample_rate_hz=sample_rate_hz,
            symbol_rate=symbol_rate,
            carrier_offset_hz=3_000.0,
            deviation_hz=4_000.0,
            noise_standard_deviation=0.05,
        )

        def plan_for(demodulator_id: str, hypothesis_id: str) -> ReceiverHypothesis:
            return ReceiverHypothesis(
                waveform=WaveformHypothesis(
                    hypothesis_id=hypothesis_id,
                    demodulator_id=demodulator_id,
                    symbol_rate=symbol_rate,
                    decimation=3,
                    rate_errors_ppm=(0.0,),
                    phase_bins=32,
                    top_timing_hypotheses=32,
                    modulation_family="fsk",
                ),
                protocol_id="ax25",
            )

        receiver = default_receiver()
        legacy_plan = plan_for("phase_fsk", "legacy")
        conditioned_plan = plan_for(
            ChannelConditionedPhaseFskDemodulator().demodulator_id,
            "conditioned",
        )
        legacy = receiver.decode_iq(
            iq,
            sample_rate_hz=sample_rate_hz,
            plan=(legacy_plan,),
        )
        conditioned = receiver.decode_iq(
            iq,
            sample_rate_hz=sample_rate_hz,
            plan=(conditioned_plan,),
        )
        extended = receiver.decode_iq(
            iq,
            sample_rate_hz=sample_rate_hz,
            plan=(legacy_plan, conditioned_plan),
        )

        legacy_frames = {candidate.payload for candidate in legacy.frames}
        conditioned_frames = {
            candidate.payload for candidate in conditioned.frames
        }
        extended_frames = {candidate.payload for candidate in extended.frames}
        self.assertIn(frame, legacy_frames)
        self.assertIn(frame, conditioned_frames)
        self.assertIn(frame, extended_frames)
        self.assertLessEqual(legacy_frames, extended_frames)
        self.assertEqual(legacy.failures, ())
        self.assertEqual(conditioned.failures, ())
        self.assertEqual(extended.failures, ())

    def test_channel_conditioning_differs_on_noisy_low_deviation_fsk(self) -> None:
        frame_without_fcs = bytes.fromhex(
            "94a662b2a0826094a662b29eb2e103f00018ad8001020304"
        )
        frame = append_ax25_fcs(frame_without_fcs)
        sample_rate_hz = 57_600
        symbol_rate = 9_600
        iq = _fsk_iq(
            _synthetic_g3ruh_levels(frame),
            sample_rate_hz=sample_rate_hz,
            symbol_rate=symbol_rate,
            carrier_offset_hz=6_500.0,
            deviation_hz=2_400.0,
            noise_standard_deviation=0.20,
            adjacent_tone_amplitude=0.90,
        )

        def decode(demodulator_id: str) -> set[bytes]:
            result = default_receiver().decode_iq(
                iq,
                sample_rate_hz=sample_rate_hz,
                plan=(
                    ReceiverHypothesis(
                        waveform=WaveformHypothesis(
                            hypothesis_id=demodulator_id,
                            demodulator_id=demodulator_id,
                            symbol_rate=symbol_rate,
                            decimation=3,
                            rate_errors_ppm=(0.0,),
                            phase_bins=32,
                            top_timing_hypotheses=32,
                            modulation_family="fsk",
                        ),
                        protocol_id="ax25",
                    ),
                ),
            )
            self.assertEqual(result.failures, ())
            return {candidate.payload for candidate in result.frames}

        direct_frames = decode("phase_fsk")
        conditioned_frames = decode("channel_conditioned_phase_fsk")

        self.assertNotIn(frame, direct_frames)
        self.assertIn(frame, conditioned_frames)


if __name__ == "__main__":
    unittest.main()
