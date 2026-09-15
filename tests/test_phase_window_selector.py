from __future__ import annotations

import numpy as np

from telemetry_yield.phase_window_selector import (
    PhaseWindowSelectorConfig,
    select_phase_windows_cf32,
)


def test_reference_free_selector_covers_low_deviation_fsk_burst(tmp_path) -> None:
    sample_rate = 4_000
    seconds = 12
    rng = np.random.default_rng(20260902)
    iq = 0.12 * (
        rng.normal(size=sample_rate * seconds)
        + 1j * rng.normal(size=sample_rate * seconds)
    )
    burst_start = 6
    burst_samples = 2 * sample_rate
    bits = rng.integers(0, 2, size=400)
    frequency = 350.0 + 180.0 * (2.0 * np.repeat(bits, 20) - 1.0)
    phase = 2.0 * np.pi * np.cumsum(frequency) / sample_rate
    iq[burst_start * sample_rate : burst_start * sample_rate + burst_samples] += (
        np.exp(1j * phase)
    )
    path = tmp_path / "capture.cf32"
    np.asarray(iq, dtype="<c8").tofile(path)
    config = PhaseWindowSelectorConfig(
        sample_rate_hz=sample_rate,
        decoder_window_seconds=5.0,
        top_k=2,
        padding_seconds=1.0,
    )

    first = select_phase_windows_cf32(path, config=config)
    second = select_phase_windows_cf32(path, config=config)

    assert first == second
    assert first["reference_paths_available_to_selector"] is False
    assert first["event_timestamps_available_to_selector"] is False
    starts = first["decoder_window_starts_seconds"]
    assert any(start <= burst_start and start + 5.0 >= burst_start + 2.0 for start in starts)


def test_selector_rejects_incomplete_cf32_sample(tmp_path) -> None:
    path = tmp_path / "broken.cf32"
    path.write_bytes(b"123")

    try:
        select_phase_windows_cf32(path)
    except ValueError as exc:
        assert "CF32" in str(exc)
    else:
        raise AssertionError("invalid CF32 input was accepted")
