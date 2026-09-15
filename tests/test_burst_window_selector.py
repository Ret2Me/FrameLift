from __future__ import annotations

import numpy as np
import pytest

from telemetry_yield.burst_window_selector import (
    BurstWindowSelectorConfig,
    select_protocol_neutral_ci16_windows,
)


def _write_ci16(path, values: np.ndarray) -> None:
    scaled = np.column_stack((values.real, values.imag))
    np.rint(np.clip(scaled, -32768, 32767)).astype("<i2").tofile(path)


def test_signal_lane_prefers_modulated_burst_over_strong_carrier(tmp_path) -> None:
    sample_rate = 4096
    rng = np.random.default_rng(20260904)
    seconds = 30
    values = 180.0 * (
        rng.normal(size=sample_rate * seconds)
        + 1j * rng.normal(size=sample_rate * seconds)
    )
    time = np.arange(sample_rate * 6) / sample_rate
    values[5 * sample_rate : 11 * sample_rate] += 6000.0 * np.exp(
        2j * np.pi * 430.0 * time
    )
    bits = rng.integers(0, 2, size=1200)
    frequency = 700.0 + 220.0 * (2.0 * np.repeat(bits, 10) - 1.0)
    phase = 2.0 * np.pi * np.cumsum(frequency) / sample_rate
    values[20 * sample_rate : 20 * sample_rate + len(phase)] += 2800.0 * np.exp(
        1j * phase
    )
    path = tmp_path / "carrier-and-burst.iq"
    _write_ci16(path, values)
    config = BurstWindowSelectorConfig(
        sample_rate_hz=sample_rate,
        signal_window_limit=3,
        change_window_limit=0,
        coverage_hop_seconds=None,
        spectral_fft_size=1024,
    )

    result = select_protocol_neutral_ci16_windows(path, config=config)

    assert result["event_timestamps_available_to_selector"] is False
    signal = [row for row in result["selected_windows"] if "signal" in row["lanes"]]
    assert any(19.0 <= row["analysis_start_seconds"] <= 23.0 for row in signal)
    best = max(signal, key=lambda row: row["signal_score"])
    assert 19.0 <= best["analysis_start_seconds"] <= 23.0
    carrier_scores = [
        row["signal_score"]
        for row in signal
        if 5.0 <= row["analysis_start_seconds"] <= 10.5
    ]
    assert carrier_scores and best["signal_score"] > max(carrier_scores)


def test_coverage_lane_has_no_gap_and_is_deterministic(tmp_path) -> None:
    sample_rate = 2048
    rng = np.random.default_rng(7)
    values = 300.0 * (
        rng.normal(size=sample_rate * 23)
        + 1j * rng.normal(size=sample_rate * 23)
    )
    path = tmp_path / "noise.iq"
    _write_ci16(path, values)
    config = BurstWindowSelectorConfig(
        sample_rate_hz=sample_rate,
        signal_window_limit=0,
        change_window_limit=0,
        burst_window_limit=0,
        decoder_window_seconds=5.0,
        coverage_hop_seconds=4.0,
        spectral_fft_size=1024,
    )

    first = select_protocol_neutral_ci16_windows(path, config=config)
    second = select_protocol_neutral_ci16_windows(path, config=config)

    assert first == second
    assert first["coverage_audit"] == {
        "enabled": True,
        "entire_capture_covered": True,
        "maximum_uncovered_gap_seconds": 0.0,
    }
    starts = [row["decoder_start_seconds"] for row in first["selected_windows"]]
    assert all(row["lanes"] == ["coverage"] for row in first["selected_windows"])
    for event in np.linspace(0.0, 23.0, 1000):
        assert any(start <= event <= start + 5.0 for start in starts)


def test_coverage_hop_cannot_create_gaps() -> None:
    with pytest.raises(ValueError, match="no greater"):
        BurstWindowSelectorConfig(
            decoder_window_seconds=5.0,
            coverage_hop_seconds=5.1,
        )


def test_analysis_window_count_is_hard_bounded(tmp_path) -> None:
    sample_rate = 2048
    values = np.ones(sample_rate * 2, dtype=np.complex128)
    path = tmp_path / "too-long.iq"
    _write_ci16(path, values)
    config = BurstWindowSelectorConfig(
        sample_rate_hz=sample_rate,
        maximum_analysis_windows=3,
        spectral_fft_size=1024,
    )
    with pytest.raises(ValueError, match="bounded analysis-window"):
        select_protocol_neutral_ci16_windows(path, config=config)


def test_incomplete_ci16_is_rejected(tmp_path) -> None:
    path = tmp_path / "broken.iq"
    path.write_bytes(b"123")
    with pytest.raises(ValueError, match="I/Q pairs"):
        select_protocol_neutral_ci16_windows(path)


def test_burst_lane_rejects_carrier_and_impulse_but_keeps_modulation(tmp_path) -> None:
    sample_rate = 4096
    rng = np.random.default_rng(20260904)
    seconds = 20
    values = 80.0 * (
        rng.normal(size=sample_rate * seconds)
        + 1j * rng.normal(size=sample_rate * seconds)
    )
    carrier_time = np.arange(sample_rate * 4) / sample_rate
    values[2 * sample_rate : 6 * sample_rate] += 5000.0 * np.exp(
        2j * np.pi * 500.0 * carrier_time
    )
    values[8 * sample_rate] += 30_000.0 + 30_000.0j
    bit_samples = 16
    bits = rng.integers(0, 2, size=100)
    frequency = 900.0 + 250.0 * (2.0 * np.repeat(bits, bit_samples) - 1.0)
    phase = 2.0 * np.pi * np.cumsum(frequency) / sample_rate
    burst_start = 14 * sample_rate
    values[burst_start : burst_start + phase.size] += 2800.0 * np.exp(1j * phase)
    path = tmp_path / "nuisance-and-burst.iq"
    _write_ci16(path, values)
    config = BurstWindowSelectorConfig(
        sample_rate_hz=sample_rate,
        signal_window_limit=0,
        change_window_limit=0,
        burst_window_limit=3,
        coverage_hop_seconds=None,
        spectral_fft_size=1024,
        burst_fft_size=256,
        burst_hop_samples=128,
        burst_band_width_bins=(4, 8, 16, 32, 64, 128),
    )

    result = select_protocol_neutral_ci16_windows(path, config=config)

    burst = [row for row in result["selected_windows"] if "burst" in row["lanes"]]
    assert burst
    best = max(burst, key=lambda row: row["burst_score"])
    assert 13.5 <= best["analysis_start_seconds"] <= 15.0
    assert all(not 2.0 <= row["analysis_start_seconds"] <= 5.5 for row in burst)
    assert all(not 7.5 <= row["analysis_start_seconds"] <= 8.5 for row in burst)
