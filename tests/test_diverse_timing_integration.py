"""End-to-end additive-clock regression tests using deterministic real PCM.

These are synthetic correctness controls, not evidence of real-data yield or
population false-alarm rates. Both receivers receive exactly the same PCM,
waveform and strict fast AX.25 adapter; only the clock-selection policy differs.
"""

from __future__ import annotations

from unittest import mock

import numpy as np
import pytest
from scipy.ndimage import gaussian_filter1d

import telemetry_yield.diverse_timing as diverse_module
import telemetry_yield.generic_receiver as generic_module
from telemetry_yield.audio_receiver import decode_audio_pcm
from telemetry_yield.camras_replay import bytes_to_bits
from telemetry_yield.clock_recovery import HDLC_FLAG, g3ruh_scramble_bits
from telemetry_yield.crc import append_ax25_fcs, validate_ax25_fcs
from telemetry_yield.diverse_timing import DiverseTimingReceiver
from telemetry_yield.fast_ax25 import FastAx25ProtocolDecoder
from telemetry_yield.generic_receiver import (
    DemodulatedSignal,
    GenericReceiver,
    ReceiverHypothesis,
    WaveformHypothesis,
)


HEADER = bytes.fromhex("94a662b2a0826094a662b29eb2e103f0")
SAMPLE_RATE_HZ = 48_000
WAVEFORM = WaveformHypothesis(
    hypothesis_id="synthetic-clock-diversity-integration-v1",
    demodulator_id="pcm_fsk",
    symbol_rate=9_600,
    decimation=2,
    rate_errors_ppm=(-500.0, -100.0, 0.0, 100.0, 500.0),
    phase_bins=32,
    top_timing_hypotheses=16,
    modulation_family="fsk",
)
PLAN = (ReceiverHypothesis(WAVEFORM, "ax25"),)


def _receivers():
    original = GenericReceiver()
    diverse = DiverseTimingReceiver(per_rate=2, min_phase_distance=0.20)
    for receiver in (original, diverse):
        receiver.register_protocol(
            FastAx25ProtocolDecoder(protocol_id="ax25", g3ruh_modes=(False, True))
        )
    return original, diverse


def _stuff(bits):
    output, ones = [], 0
    for bit in bits:
        output.append(bit)
        ones = ones + 1 if bit else 0
        if ones == 5:
            output.append(0)
            ones = 0
    return tuple(output)


def _pcm_fixture(*, seed: int, g3ruh: bool, noise_std: float,
                 corrupt_fcs: bool = False, strict_structure: bool = True):
    rng = np.random.default_rng(seed)
    header = HEADER if strict_structure else b"not-ax25-header!!"
    pdus = tuple(header + rng.bytes(48) for _ in range(3))
    frames = tuple(append_ax25_fcs(pdu) for pdu in pdus)
    if corrupt_fcs:
        frames = tuple(frame[:-1] + bytes([frame[-1] ^ 1]) for frame in frames)
    bits = HDLC_FLAG * 80
    for frame in frames * 2:
        bits += _stuff(bytes_to_bits(frame, lsb_first=True)) + HDLC_FLAG * 8
    bits += HDLC_FLAG * 80
    if g3ruh:
        bits = g3ruh_scramble_bits(bits)
    level = 0
    levels = []
    for bit in bits:
        if not bit:
            level ^= 1
        levels.append(level)
    pulses = np.repeat(2.0 * np.asarray(levels) - 1.0, 5)
    pcm = 0.4 * gaussian_filter1d(pulses, 0.8) + 0.08
    pcm += rng.normal(0.0, noise_std, size=pcm.size)
    return pcm, tuple(append_ax25_fcs(pdu) for pdu in pdus)


def _decode_pair(pcm):
    original, diverse = _receivers()
    before = pcm.copy()
    with mock.patch.object(
        generic_module,
        "soft_symbols_with_hypothesis",
        wraps=generic_module.soft_symbols_with_hypothesis,
    ) as original_calls:
        original_result = decode_audio_pcm(
            pcm, sample_rate_hz=SAMPLE_RATE_HZ,
            representation="fm_demodulated", plan=PLAN, receiver=original,
        )
    with mock.patch.object(
        diverse_module,
        "soft_symbols_with_hypothesis",
        wraps=diverse_module.soft_symbols_with_hypothesis,
    ) as diverse_calls:
        diverse_result = decode_audio_pcm(
            pcm, sample_rate_hz=SAMPLE_RATE_HZ,
            representation="fm_demodulated", plan=PLAN, receiver=diverse,
        )
    original_timings = tuple(call.args[1] for call in original_calls.call_args_list)
    diverse_timings = tuple(call.args[1] for call in diverse_calls.call_args_list)
    assert np.array_equal(before, pcm), "Receiver must not modify shared input PCM"
    # Compare complete timing records, including threshold, score, guards and
    # symbol count, not merely the number of clocks or their nominal rates.
    assert diverse_timings[:len(original_timings)] == original_timings
    assert original_result.attempted_timing_hypotheses == len(original_timings)
    assert diverse_result.attempted_timing_hypotheses == len(diverse_timings)
    assert len(diverse_timings) <= WAVEFORM.top_timing_hypotheses + 2 * 5
    assert diverse_result.frames[:len(original_result.frames)] == original_result.frames
    assert {frame.payload for frame in original_result.frames} <= {
        frame.payload for frame in diverse_result.frames
    }
    return original_result, diverse_result, original_timings, diverse_timings


@pytest.mark.parametrize("g3ruh", [False, True])
@pytest.mark.parametrize("seed,noise_std", [(202609075, 0.0), (202609076, 0.055)])
def test_positive_pcm_preserves_exact_old_clock_prefix_frames_and_validation(
    g3ruh, seed, noise_std
):
    pcm, expected_frames = _pcm_fixture(seed=seed, g3ruh=g3ruh, noise_std=noise_std)
    original, diverse, original_timings, diverse_timings = _decode_pair(pcm)
    # A nonempty old result is required; a vacuous empty subset cannot pass.
    assert {frame.payload for frame in original.frames} == set(expected_frames)
    assert {frame.payload for frame in diverse.frames} == set(expected_frames)
    assert len(original_timings) == 16
    assert len(diverse_timings) >= len(original_timings)
    assert original.failures == diverse.failures == ()
    for result in (original, diverse):
        assert all(validate_ax25_fcs(frame.payload) for frame in result.frames)
        assert all(frame.validation == "crc16_x25+ax25_ui" for frame in result.frames)
        assert all(frame.protocol_id == "ax25" for frame in result.frames)
        assert all(frame.waveform_hypothesis_id.startswith("fm_demodulated:")
                   for frame in result.frames)


@pytest.mark.parametrize("g3ruh", [False, True])
def test_additional_clocks_do_not_repair_corrupted_crc(g3ruh):
    pcm, _ = _pcm_fixture(seed=202609077, g3ruh=g3ruh, noise_std=0.02,
                          corrupt_fcs=True)
    original, diverse, _, _ = _decode_pair(pcm)
    assert original.frames == diverse.frames == ()
    assert original.failures == diverse.failures == ()


@pytest.mark.parametrize("g3ruh", [False, True])
def test_crc_valid_non_ax25_structure_remains_rejected(g3ruh):
    pcm, _ = _pcm_fixture(seed=202609078, g3ruh=g3ruh, noise_std=0.02,
                          strict_structure=False)
    original, diverse, _, _ = _decode_pair(pcm)
    assert original.frames == diverse.frames == ()
    assert original.failures == diverse.failures == ()


def test_seeded_noise_and_silence_remain_zero_without_far_claim():
    noise = np.random.default_rng(202609079).normal(size=SAMPLE_RATE_HZ)
    original, diverse, original_timings, _ = _decode_pair(noise)
    assert original.frames == diverse.frames == ()
    assert original.failures == diverse.failures == ()
    assert len(original_timings) == 16
    original, diverse, original_timings, diverse_timings = _decode_pair(np.zeros(20_001))
    assert original.frames == diverse.frames == ()
    assert original_timings == diverse_timings == ()
    assert original.failures == diverse.failures == ()


@pytest.mark.parametrize("use_diverse", [False, True])
def test_missing_protocol_is_rejected_before_clock_recovery(use_diverse):
    receiver = _receivers()[int(use_diverse)]
    module = diverse_module if use_diverse else generic_module
    signal = DemodulatedSignal(tuple(np.zeros(1024)), 2.5, {})
    with mock.patch.object(module, "recover_timing_hypotheses") as recover:
        with pytest.raises(KeyError, match="unknown protocol"):
            receiver.decode_demodulated(signal, waveform=WAVEFORM,
                                        protocol_id="does_not_exist")
        recover.assert_not_called()


@pytest.mark.parametrize("use_diverse", [False, True])
def test_symbol_kind_mismatch_is_rejected_before_clock_recovery(use_diverse):
    receiver = _receivers()[int(use_diverse)]
    module = diverse_module if use_diverse else generic_module
    signal = DemodulatedSignal(tuple(np.zeros(1024)), 2.5, {},
                               symbol_kind="unsupported_quadrature_symbols")
    with mock.patch.object(module, "recover_timing_hypotheses") as recover:
        with pytest.raises(ValueError, match="incompatible"):
            receiver.decode_demodulated(signal, waveform=WAVEFORM, protocol_id="ax25")
        recover.assert_not_called()
