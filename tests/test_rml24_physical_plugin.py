from __future__ import annotations

import numpy as np

import telemetry_yield.rml24_physical_plugin as physical
from telemetry_yield.rml24_benchmark import ArrayBatchSource, Rml24Batch, run_rml24_benchmark
from telemetry_yield.rml24_physical_plugin import (
    AlignmentPolicy,
    QpskUnaligned,
    Rml24PhysicalBerPlugin,
    align_for_ber,
    align_qpsk_for_ber,
)


SAMPLE_RATE = 1_000_000.0


def _noise(signal: np.ndarray, snr_db: float, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    power = np.mean(np.abs(signal) ** 2)
    variance = power / (10 ** (snr_db / 10.0))
    return signal + np.sqrt(variance / 2) * (
        rng.normal(size=len(signal)) + 1j * rng.normal(size=len(signal))
    )


def _as_iq(signal: np.ndarray) -> np.ndarray:
    return np.stack((signal.real, signal.imag)).astype(np.float32)


def _fractional_circular_delay(signal: np.ndarray, delay_samples: float) -> np.ndarray:
    positions = np.arange(len(signal), dtype=np.float64)
    delayed_positions = positions - delay_samples
    return np.interp(
        delayed_positions, positions, signal.real, period=len(signal)
    ) + 1j * np.interp(
        delayed_positions, positions, signal.imag, period=len(signal)
    )


def _bpsk_fixture(seed: int = 1) -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(seed)
    bits = rng.integers(0, 2, size=205, dtype=np.uint8)
    symbols = 1.0 - 2.0 * bits.astype(np.float64)
    baseband = np.repeat(symbols, 10)[:2048].astype(np.complex128)
    n = np.arange(len(baseband))
    impaired = baseband * np.exp(1j * (0.83 + 2 * np.pi * 2_300.0 * n / SAMPLE_RATE))
    return _as_iq(_noise(impaired, 9.0, seed + 10)), bits


def _fsk_fixture(seed: int = 2) -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(seed)
    bits = rng.integers(0, 2, size=512, dtype=np.uint8)
    symbols = np.repeat(2.0 * bits.astype(np.float64) - 1.0, 4)
    instantaneous_hz = 1_700.0 + 31_000.0 * symbols
    phase = 2 * np.pi * np.cumsum(instantaneous_hz) / SAMPLE_RATE + 1.2
    signal = np.exp(1j * phase)
    return _as_iq(_noise(signal, 8.0, seed + 10)), bits


def _gmsk_fixture(seed: int = 3) -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(seed)
    bits = rng.integers(0, 2, size=512, dtype=np.uint8)
    nrz = np.repeat(2.0 * bits.astype(np.float64) - 1.0, 4)
    taps = np.arange(-12, 13, dtype=np.float64)
    gaussian = np.exp(-0.5 * (taps / 2.2) ** 2)
    gaussian /= np.sum(gaussian)
    shaped = np.convolve(nrz, gaussian, mode="same")
    instantaneous_hz = -1_300.0 + 55_000.0 * shaped
    phase = 2 * np.pi * np.cumsum(instantaneous_hz) / SAMPLE_RATE - 0.4
    signal = np.exp(1j * phase)
    return _as_iq(_noise(signal, 9.0, seed + 10)), bits


def _qpsk_fixture(seed: int = 4) -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(seed)
    bits = rng.integers(0, 2, size=408, dtype=np.uint8)
    pairs = bits.reshape(-1, 2)
    symbols = (
        (1.0 - 2.0 * pairs[:, 0].astype(np.float64))
        + 1j * (1.0 - 2.0 * pairs[:, 1].astype(np.float64))
    ) / np.sqrt(2.0)
    baseband = _fractional_circular_delay(np.repeat(symbols, 10), 3.35)
    n = np.arange(len(baseband))
    impaired = baseband * np.exp(1j * (1.17 + 2 * np.pi * 1_900.0 * n / SAMPLE_RATE))
    return _as_iq(_noise(impaired, 10.0, seed + 10)), bits


def _oqpsk_fixture(seed: int = 5) -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(seed)
    bits = rng.integers(0, 2, size=408, dtype=np.uint8)
    pairs = bits.reshape(-1, 2)
    i_symbols = 1.0 - 2.0 * pairs[:, 0].astype(np.float64)
    q_symbols = 1.0 - 2.0 * pairs[:, 1].astype(np.float64)
    i_wave = np.repeat(i_symbols, 10)
    q_wave = np.repeat(q_symbols, 10)
    q_delayed = np.concatenate((np.full(5, q_symbols[0]), q_wave))[: len(i_wave)]
    baseband = _fractional_circular_delay(
        (i_wave + 1j * q_delayed) / np.sqrt(2.0),
        2.25,
    )
    n = np.arange(len(baseband))
    # 1.21 rad intentionally leaves a pi/2 ambiguity after fourth-power
    # recovery, exercising the alternate OQPSK stagger hypothesis.
    impaired = baseband * np.exp(1j * (1.21 - 2 * np.pi * 2_100.0 * n / SAMPLE_RATE))
    return _as_iq(_noise(impaired, 10.0, seed + 10)), bits


def _batch(iq: np.ndarray, bits: np.ndarray, modulation: str, rate: int) -> Rml24Batch:
    return Rml24Batch(
        iq=iq[None, ...],
        modulation=np.array([modulation]),
        bits=bits[None, ...],
        bit_lengths=np.array([len(bits)]),
        snr_db=np.array([8]),
        symbol_rate_hz=np.array([rate]),
    )


def test_bpsk_recovery_handles_cfo_phase_timing_and_polarity() -> None:
    iq, truth = _bpsk_fixture()
    plugin = Rml24PhysicalBerPlugin(alignment_policy=AlignmentPolicy(max_shift_bits=8))
    batch = _batch(iq, truth, "BPSK", 100_000)
    raw = plugin.demodulate_unaligned(batch.iq, batch)[0]
    aligned, diagnostics = align_for_ber(raw, truth, policy=plugin.alignment_policy)
    assert np.mean(aligned != truth) < 0.03
    assert abs(int(diagnostics["shift_bits"])) <= 8


def test_binary_fsk_uses_blind_discriminator_with_cfo_and_noise() -> None:
    iq, truth = _fsk_fixture()
    policy = AlignmentPolicy(max_shift_bits=8)
    plugin = Rml24PhysicalBerPlugin(alignment_policy=policy)
    batch = _batch(iq, truth, "2FSK", 250_000)
    raw = plugin.demodulate_unaligned(batch.iq, batch)[0]
    aligned, _ = align_for_ber(raw, truth, policy=policy)
    assert np.mean(aligned != truth) < 0.03


def test_gmsk_gaussian_frequency_pulse_with_cfo_and_noise() -> None:
    iq, truth = _gmsk_fixture()
    policy = AlignmentPolicy(max_shift_bits=8)
    plugin = Rml24PhysicalBerPlugin(alignment_policy=policy)
    batch = _batch(iq, truth, "GMSK", 250_000)
    raw = plugin.demodulate_unaligned(batch.iq, batch)[0]
    aligned, _ = align_for_ber(raw, truth, policy=policy)
    assert np.mean(aligned != truth) < 0.06


def test_qpsk_handles_cfo_noise_timing_and_constellation_rotation() -> None:
    iq, truth = _qpsk_fixture()
    policy = AlignmentPolicy(max_shift_bits=8)
    plugin = Rml24PhysicalBerPlugin(alignment_policy=policy)
    batch = _batch(iq, truth, "QPSK", 100_000)
    raw = plugin.demodulate_unaligned(batch.iq, batch)[0]
    assert isinstance(raw, QpskUnaligned)
    aligned, diagnostics = align_qpsk_for_ber(raw, truth, policy=policy)
    assert np.mean(aligned != truth) < 0.035
    assert abs(int(diagnostics["shift_symbols"])) <= 4


def test_oqpsk_handles_half_symbol_stagger_cfo_noise_and_rotation() -> None:
    iq, truth = _oqpsk_fixture()
    policy = AlignmentPolicy(max_shift_bits=8)
    plugin = Rml24PhysicalBerPlugin(alignment_policy=policy)
    batch = _batch(iq, truth, "OQPSK", 100_000)
    raw = plugin.demodulate_unaligned(batch.iq, batch)[0]
    assert isinstance(raw, QpskUnaligned)
    assert set(raw.variant_names) == {
        "i_delayed_half_symbol",
        "q_delayed_half_symbol",
    }
    aligned, diagnostics = align_qpsk_for_ber(raw, truth, policy=policy)
    assert np.mean(aligned != truth) < 0.05
    assert diagnostics["timing_hypothesis"] in raw.variant_names


def test_qpsk_and_oqpsk_truth_bits_do_not_change_waveform_recovery() -> None:
    plugin = Rml24PhysicalBerPlugin()
    for fixture, modulation in ((_qpsk_fixture, "QPSK"), (_oqpsk_fixture, "OQPSK")):
        iq, truth = fixture()
        first = _batch(iq, truth, modulation, 100_000)
        second = _batch(iq, 1 - truth, modulation, 100_000)
        recovered_first = plugin.demodulate_unaligned(first.iq, first)[0]
        recovered_second = plugin.demodulate_unaligned(second.iq, second)[0]
        assert isinstance(recovered_first, QpskUnaligned)
        assert isinstance(recovered_second, QpskUnaligned)
        assert recovered_first.variant_names == recovered_second.variant_names
        for left, right in zip(
            recovered_first.bit_variants,
            recovered_second.bit_variants,
            strict=True,
        ):
            np.testing.assert_array_equal(left, right)


def test_cached_dsp_constants_preserve_cold_and_warm_outputs() -> None:
    for cache in (
        physical._sample_axis,
        physical._boxcar,
        physical._hanning,
        physical._fft_frequencies,
        physical._timing_grid,
        physical._oqpsk_timing_grid,
    ):
        cache.cache_clear()
    plugin = Rml24PhysicalBerPlugin()
    for fixture, modulation, rate in (
        (_bpsk_fixture, "BPSK", 100_000),
        (_fsk_fixture, "2FSK", 250_000),
        (_gmsk_fixture, "GMSK", 250_000),
        (_qpsk_fixture, "QPSK", 100_000),
        (_oqpsk_fixture, "OQPSK", 100_000),
    ):
        iq, truth = fixture(71)
        batch = _batch(iq, truth, modulation, rate)
        cold = plugin.demodulate_unaligned(batch.iq, batch)[0]
        warm = plugin.demodulate_unaligned(batch.iq, batch)[0]
        if isinstance(cold, QpskUnaligned):
            assert isinstance(warm, QpskUnaligned)
            assert cold.variant_names == warm.variant_names
            for left, right in zip(cold.bit_variants, warm.bit_variants, strict=True):
                np.testing.assert_array_equal(left, right)
        else:
            np.testing.assert_array_equal(cold, warm)


def test_cached_qpsk_fft_constants_match_uncached_reference() -> None:
    iq, _truth = _qpsk_fixture(83)
    signal = physical._complex_record(iq)
    sample_index = np.arange(len(signal), dtype=np.float64)
    fourth = signal**4
    target_fft = max(4096, len(signal) * 16)
    fft_size = min(262_144, 1 << int(np.ceil(np.log2(target_fft))))
    spectrum = np.fft.fft(fourth * np.hanning(len(fourth)), n=fft_size)
    peak = int(np.argmax(np.abs(spectrum)))
    fourth_power_hz = float(np.fft.fftfreq(fft_size, d=1.0 / SAMPLE_RATE)[peak])
    cfo_cycles_per_sample = fourth_power_hz / (4.0 * SAMPLE_RATE)
    corrected = signal * np.exp(-2j * np.pi * cfo_cycles_per_sample * sample_index)
    carrier_phase = float(0.25 * np.angle(-np.mean(corrected**4)))
    expected = corrected * np.exp(-1j * carrier_phase)

    actual, cfo_hz, actual_phase = physical._qpsk_carrier_correct(signal, SAMPLE_RATE)

    np.testing.assert_array_equal(actual, expected)
    assert cfo_hz == cfo_cycles_per_sample * SAMPLE_RATE
    assert actual_phase == carrier_phase


def test_only_exact_qpsk_families_are_newly_supported() -> None:
    plugin = Rml24PhysicalBerPlugin()
    assert plugin.supports("QPSK")
    assert plugin.supports("OQPSK")
    assert not plugin.supports("SOQPSK-TG")
    assert not plugin.supports("PCM-QPSK-PM")


def test_benchmark_scores_qpsk_and_oqpsk_as_supported_profiles() -> None:
    qpsk_iq, qpsk_truth = _qpsk_fixture()
    oqpsk_iq, oqpsk_truth = _oqpsk_fixture()
    source = ArrayBatchSource(
        np.stack((qpsk_iq, oqpsk_iq)),
        np.array(["QPSK", "OQPSK"]),
        bits=np.stack((qpsk_truth, oqpsk_truth)),
        bit_lengths=np.array([len(qpsk_truth), len(oqpsk_truth)]),
        snr_db=np.array([10, 10]),
        symbol_rate_hz=np.array([100_000, 100_000]),
        batch_size=2,
    )
    report = run_rml24_benchmark(source, bit_demodulator=Rml24PhysicalBerPlugin())
    ber = report["metrics"]["bit_demodulation"]
    assert ber["status"] == "complete"
    assert ber["supported_records"] == 2
    assert ber["unsupported_records"] == 0
    assert ber["bit_errors"] <= 4
    assert ber["adapter"]["supported_modulations"] == [
        "2FSK",
        "BPSK",
        "FSK",
        "GMSK",
        "OQPSK",
        "QPSK",
    ]


def test_truth_bits_do_not_change_waveform_recovery() -> None:
    iq, truth = _bpsk_fixture()
    plugin = Rml24PhysicalBerPlugin()
    first = _batch(iq, truth, "BPSK", 100_000)
    second = _batch(iq, 1 - truth, "BPSK", 100_000)
    recovered_first = plugin.demodulate_unaligned(first.iq, first)[0]
    recovered_second = plugin.demodulate_unaligned(second.iq, second)[0]
    np.testing.assert_array_equal(recovered_first, recovered_second)


def test_carrier_timing_v2_bpsk_is_truth_independent_and_recovers_cfo() -> None:
    iq, truth = _bpsk_fixture(91)
    # The shared legacy fixture uses 2.3 kHz CFO.  Shift it into the article's
    # declared CFO+Doppler bound exercised by the bounded v2 search.
    signal = iq[0].astype(np.float64) + 1j * iq[1].astype(np.float64)
    samples = np.arange(len(signal), dtype=np.float64)
    signal *= np.exp(-2j * np.pi * 1_600.0 * samples / SAMPLE_RATE)
    iq = _as_iq(signal)
    policy = AlignmentPolicy(max_shift_bits=8)
    plugin = Rml24PhysicalBerPlugin(
        alignment_policy=policy,
        recovery_version="carrier_timing_v2",
    )
    first = _batch(iq, truth, "BPSK", 100_000)
    second = _batch(iq, 1 - truth, "BPSK", 100_000)
    recovered_first = plugin.demodulate_unaligned(first.iq, first)[0]
    recovered_second = plugin.demodulate_unaligned(second.iq, second)[0]
    np.testing.assert_array_equal(recovered_first, recovered_second)
    aligned, _ = align_for_ber(recovered_first, truth, policy=policy)
    assert np.mean(aligned != truth) < 0.03


def test_carrier_timing_v2_oqpsk_emits_all_boundary_pairings_before_truth() -> None:
    iq, truth = _oqpsk_fixture(93)
    policy = AlignmentPolicy(max_shift_bits=8)
    plugin = Rml24PhysicalBerPlugin(
        alignment_policy=policy,
        recovery_version="carrier_timing_v2",
    )
    batch = _batch(iq, truth, "OQPSK", 100_000)
    recovered = plugin.demodulate_unaligned(batch.iq, batch)[0]
    assert isinstance(recovered, QpskUnaligned)
    assert len(recovered.bit_variants) == 6
    assert set(recovered.variant_names) == {
        f"{branch}_delayed_half_symbol_pair_offset_{offset:+d}"
        for branch in ("i", "q")
        for offset in (-1, 0, 1)
    }
    aligned, details = align_qpsk_for_ber(recovered, truth, policy=policy)
    assert np.mean(aligned != truth) < 0.05
    assert details["timing_hypothesis"] in recovered.variant_names


def test_explicit_legacy_recovery_matches_default_bit_for_bit() -> None:
    default = Rml24PhysicalBerPlugin()
    explicit = Rml24PhysicalBerPlugin(recovery_version="legacy")
    for fixture, modulation, rate in (
        (_bpsk_fixture, "BPSK", 100_000),
        (_gmsk_fixture, "GMSK", 250_000),
        (_qpsk_fixture, "QPSK", 100_000),
        (_oqpsk_fixture, "OQPSK", 100_000),
    ):
        iq, truth = fixture(95)
        batch = _batch(iq, truth, modulation, rate)
        left = default.demodulate_unaligned(batch.iq, batch)[0]
        right = explicit.demodulate_unaligned(batch.iq, batch)[0]
        if isinstance(left, QpskUnaligned):
            assert isinstance(right, QpskUnaligned)
            assert left.variant_names == right.variant_names
            for left_bits, right_bits in zip(
                left.bit_variants, right.bit_variants, strict=True
            ):
                np.testing.assert_array_equal(left_bits, right_bits)
        else:
            np.testing.assert_array_equal(left, right)


def test_unknown_recovery_version_fails_closed() -> None:
    with np.testing.assert_raises_regex(ValueError, "recovery_version"):
        Rml24PhysicalBerPlugin(recovery_version="future")


def test_benchmark_reports_unsupported_classes_instead_of_claiming_all() -> None:
    iq, truth = _bpsk_fixture()
    source = ArrayBatchSource(
        np.stack((iq, iq)),
        np.array(["BPSK", "PCM-2FSK-PM"]),
        bits=np.stack((truth, truth)),
        bit_lengths=np.array([len(truth), len(truth)]),
        snr_db=np.array([9, 9]),
        symbol_rate_hz=np.array([100_000, 100_000]),
        batch_size=2,
    )
    report = run_rml24_benchmark(source, bit_demodulator=Rml24PhysicalBerPlugin())
    ber = report["metrics"]["bit_demodulation"]
    assert ber["status"] == "complete_supported_subset"
    assert ber["supported_records"] == 1
    assert ber["unsupported_records"] == 1
    assert ber["unsupported_by_modulation"] == {"PCM-2FSK-PM": 1}
    assert ber["adapter"]["amr_claimed"] is False
    assert ber["adapter"]["truth_bits_visible_to_waveform_recovery"] is False
