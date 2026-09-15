"""Development-only fixed oracle generator, never used by the Rust runtime/tests.

Inputs are integer LCG samples and an explicitly frozen integer oscillator.
Tests regenerate identical bytes without NumPy or a Python subprocess.
"""
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import sys
import tempfile

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from telemetry_yield.signal_triage import SignalTriageConfig, triage_ci16le_file
from telemetry_yield.phase_window_selector import PhaseWindowSelectorConfig, select_phase_windows_cf32
from telemetry_yield.burst_window_selector import BurstWindowSelectorConfig, select_protocol_neutral_ci16_windows
from telemetry_yield.waveform_routing import WaveformRoutingConfig, route_ci16le_waveform

OSCILLATOR = [(1000, 0), (924, 383), (707, 707), (383, 924), (0, 1000),
              (-383, 924), (-707, 707), (-924, 383), (-1000, 0),
              (-924, -383), (-707, -707), (-383, -924), (0, -1000),
              (383, -924), (707, -707), (924, -383)]


def components(case):
    state = case["seed"]
    output = []
    phase = 0
    symbol = 1
    for index in range(case["samples"]):
        pair = []
        for _ in range(2):
            state = (state * 6364136223846793005 + 1442695040888963407) & ((1 << 64) - 1)
            pair.append(int((state >> 32) % 401) - 200)
        kind = case["waveform"]
        rate = case["sample_rate"]
        tone = kind == "carrier" or (kind == "mixed" and 2 * rate <= index < 4 * rate)
        fsk = kind == "fsk" or (kind == "mixed" and 8 * rate <= index < 8 * rate + rate // 2)
        if tone:
            phase = (phase + 1) % 16
            pair = [x + 5 * v for x, v in zip(pair, OSCILLATOR[phase])]
        if fsk:
            if index % 6 == 0:
                symbol = 1 if (state >> 43) & 1 else -1
            phase = (phase + symbol + 16) % 16
            pair = [x + 3 * v for x, v in zip(pair, OSCILLATOR[phase])]
        if kind == "mixed" and index == 6 * rate:
            pair = [32767, -32768]
        output.append(pair)
    return np.asarray(output, dtype="<i2")


cases = []
with tempfile.TemporaryDirectory(prefix="triage-python-oracle-") as directory:
    for mode, waveform, rate, samples, seed in [
        ("triage", "noise", 4096, 5 * 4096 + 19, 42),
        ("triage", "carrier", 4096, 5 * 4096, 17),
        ("phase", "mixed", 2048, 12 * 2048 + 11, 61),
        ("burst", "mixed", 4096, 12 * 4096 + 5, 91),
        ("burst", "noise", 2048, 9 * 2048, 31),
        ("routing", "noise", 57600, 3 * 57600, 121),
        ("routing", "carrier", 57600, 3 * 57600, 71),
        ("routing", "fsk", 57600, 3 * 57600, 44),
    ]:
        case = dict(mode=mode, waveform=waveform, sample_rate=rate, samples=samples, seed=seed)
        raw = components(case)
        if mode == "phase":
            encoded = (raw.astype(np.float32) / np.float32(1024)).astype("<f4").tobytes()
        else:
            encoded = raw.tobytes()
        source = Path(directory) / "capture.iq"
        source.write_bytes(encoded)
        if mode == "triage":
            config = SignalTriageConfig(sample_rate_hz=rate, fft_size=256, fft_slices_per_window=3)
            result = triage_ci16le_file(source, config=config).as_dict()
        elif mode == "phase":
            config = PhaseWindowSelectorConfig(sample_rate_hz=rate, top_k=2, padding_seconds=1.0)
            result = select_phase_windows_cf32(source, config=config)
        elif mode == "burst":
            config = BurstWindowSelectorConfig(sample_rate_hz=rate, spectral_fft_size=1024,
                burst_fft_size=256, burst_hop_samples=128, signal_window_limit=3,
                change_window_limit=3, burst_window_limit=3)
            result = select_protocol_neutral_ci16_windows(source, config=config)
        else:
            config = WaveformRoutingConfig(sample_rate_hz=rate, strongest_window_count=2, psd_fft_size=8192)
            result = route_ci16le_waveform(source, config=config).as_dict()
        result.pop("input_path", None)
        case.update(config=asdict(config), input_sha256=hashlib.sha256(encoded).hexdigest(), expected=result)
        cases.append(case)

sources = ["signal_triage.py", "phase_window_selector.py", "burst_window_selector.py", "waveform_routing.py"]
document = dict(schema="python-triage-differential-fixture-v1", numpy=np.__version__, oscillator=OSCILLATOR,
    source_sha256={name: hashlib.sha256((ROOT / "src/telemetry_yield" / name).read_bytes()).hexdigest() for name in sources},
    note="Scheduling outputs only. No frame bytes, truth outcomes, or mission metadata are inputs.", cases=cases)
target = Path(__file__).with_name("triage_oracle.json")
with target.open("x", encoding="utf-8") as stream:
    json.dump(document, stream, indent=2, allow_nan=False)
    stream.write("\n")
print(json.dumps(dict(cases=len(cases), output=str(target), numpy=np.__version__)))
