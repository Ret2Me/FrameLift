from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "work/polyitan/evaluate_protocol_neutral_window_selector.py"


def _module():
    name = "protocol_neutral_window_selector_evaluation_test_module"
    spec = importlib.util.spec_from_file_location(name, SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


SELECTOR = _module()


def _write_ci16(path: Path, samples: np.ndarray) -> None:
    clipped = np.clip(
        np.column_stack((samples.real, samples.imag)), -32768, 32767
    ).astype("<i2")
    path.write_bytes(clipped.tobytes())


def test_selector_rejects_constant_carrier_and_broadband_impulse(tmp_path: Path) -> None:
    config = SELECTOR.SelectorConfig(
        sample_rate_hz=8192,
        analysis_window_seconds=0.5,
        fft_samples=256,
        hop_samples=128,
        band_width_bins=(4, 8, 16, 32, 64, 128),
        top_k=3,
    )
    rng = np.random.default_rng(20260904)
    window = config.analysis_samples
    count = 16
    time = np.arange(count * window) / config.sample_rate_hz
    samples = 5000 * np.exp(2j * np.pi * 731 * time)
    samples += rng.normal(0, 80, samples.size) + 1j * rng.normal(0, 80, samples.size)

    # One short, spectrally broad disturbance should be suppressed as a
    # per-frame common-mode lift.
    impulse_index = 4
    impulse_start = impulse_index * window + 1700
    samples[impulse_start : impulse_start + 64] += 12000 * (
        rng.normal(size=64) + 1j * rng.normal(size=64)
    )

    # The desired event is a time-localized, band-limited frequency-switching
    # burst.  Random symbols exist only in the fixture, never in the selector.
    event_index = 11
    burst_start = event_index * window + 1200
    burst_length = 1024
    symbols = rng.choice((-1.0, 1.0), size=burst_length // 32 + 1)
    frequency = 1100 + 420 * np.repeat(symbols, 32)[:burst_length]
    phase = np.cumsum(2 * np.pi * frequency / config.sample_rate_hz)
    samples[burst_start : burst_start + burst_length] += 3500 * np.exp(1j * phase)
    path = tmp_path / "mixed.ci16"
    _write_ci16(path, samples)

    selected = SELECTOR.select_top_windows(path, config=config)
    assert selected[0].index == event_index
    assert all(row.index != impulse_index for row in selected)
    assert tuple(row.index for row in selected) == tuple(
        row.index for row in SELECTOR.select_top_windows(path, config=config)
    )


def test_all_zero_has_no_positive_candidate_and_labels_do_not_change_score(
    tmp_path: Path,
) -> None:
    config = SELECTOR.SelectorConfig(
        sample_rate_hz=1024,
        analysis_window_seconds=0.5,
        fft_samples=64,
        hop_samples=32,
        band_width_bins=(4, 8, 16, 32),
        top_k=2,
    )
    path = tmp_path / "zero.ci16"
    path.write_bytes(b"\x00" * config.analysis_window_bytes * 4)
    assert SELECTOR.select_top_windows(path, config=config) == ()
    first = SELECTOR.evaluate_capture(path, event_seconds=(0.5,), config=config)
    second = SELECTOR.evaluate_capture(path, event_seconds=(1.0,), config=config)
    assert first["selector"] == second["selector"]
    assert first["posthoc_development_labels"][0]["label_used_by_scoring"] is False
    assert first["selector"]["memory_bound"]["retained_iq_window_bytes"] == 2048


def test_config_and_out_of_capture_label_fail_closed(tmp_path: Path) -> None:
    try:
        SELECTOR.SelectorConfig(fft_samples=512, band_width_bins=(1, 4))
    except ValueError as error:
        assert "band widths" in str(error)
    else:
        raise AssertionError("one-bin carrier width was accepted")
    path = tmp_path / "short.ci16"
    path.write_bytes(b"\x00" * 4 * 1024)
    config = SELECTOR.SelectorConfig(
        sample_rate_hz=1024,
        analysis_window_seconds=0.5,
        fft_samples=64,
        hop_samples=32,
        band_width_bins=(4, 8, 16, 32),
    )
    try:
        SELECTOR.evaluate_capture(path, event_seconds=(10.0,), config=config)
    except ValueError as error:
        assert "outside" in str(error)
    else:
        raise AssertionError("out-of-capture label was accepted")
