"""Offline unchanged-Python CW oracle; never used by Rust runtime/tests."""
from dataclasses import asdict
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from telemetry_yield.cw_probe import CwProbeConfig, NarrowbandCwExtractor, _decode_runs

spec = importlib.util.spec_from_file_location("frozen_cw_tests", ROOT / "tests/test_cw_probe.py")
legacy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(legacy)
legacy_result = unittest.TestResult()
unittest.defaultTestLoader.loadTestsFromTestCase(legacy.CwProbeTests).run(legacy_result)
assert legacy_result.wasSuccessful(), (legacy_result.errors, legacy_result.failures)

RATE = 8000
OSCILLATOR = [(1000, 0), (707, 707), (0, 1000), (-707, 707),
              (-1000, 0), (-707, -707), (0, -1000), (707, -707)]
config = CwProbeConfig(sample_rate_hz=RATE, preview_fft_size=2048,
    preview_slices_per_window=2, carrier_half_bandwidth_hz=150)


def encode(values):
    pairs = np.column_stack((values.real, values.imag))
    return np.clip(np.rint(pairs), -32768, 32767).astype("<i2").tobytes()


def generated(case):
    state = case["seed"]
    output = []
    envelope = legacy.morse_envelope("SOS", sample_rate=RATE, wpm=20)
    for index in range(case["samples"]):
        pair = []
        for _ in range(2):
            state = (state * 6364136223846793005 + 1442695040888963407) & ((1 << 64) - 1)
            pair.append(int((state >> 32) % 61) - 30)
        enabled = case["waveform"] == "carrier"
        if case["waveform"] == "sos_twice":
            enabled = any(0 <= index - start < len(envelope) and envelope[index-start]
                for start in (0, 10 * RATE))
        if enabled:
            pair = [x + 9 * y for x, y in zip(pair, OSCILLATOR[index % 8])]
        if case["waveform"] == "zero":
            pair = [0, 0]
        output.append(pair)
    return np.asarray(output, dtype="<i2").tobytes()


cases = []
with tempfile.TemporaryDirectory(prefix="cw-oracle-") as directory:
    source = Path(directory) / "capture.iq"
    for kind in ("sos", "carrier", "noise"):
        if kind == "sos":
            rng = np.random.default_rng(12)
            envelope = legacy.morse_envelope("SOS", sample_rate=RATE, wpm=20)
            t = np.arange(len(envelope)) / RATE
            noise = rng.normal(0, 30, len(envelope)) + 1j * rng.normal(0, 30, len(envelope))
            samples = 9000 * envelope * np.exp(2j * np.pi * 1000 * t) + noise
        elif kind == "carrier":
            t = np.arange(4 * RATE) / RATE
            samples = 8000 * np.exp(2j * np.pi * 700 * t)
        else:
            rng = np.random.default_rng(99)
            samples = rng.normal(0, 500, 5 * RATE) + 1j * rng.normal(0, 500, 5 * RATE)
        data = encode(samples)
        target = Path(__file__).with_name(f"cw_legacy_{kind}.ci16")
        with target.open("xb") as stream:
            stream.write(data)
        source.write_bytes(data)
        result = NarrowbandCwExtractor(config).extract(source)
        cases.append(dict(name=f"legacy_{kind}", input_file=target.name,
            input_sha256=hashlib.sha256(data).hexdigest(), config=asdict(config), expected=asdict(result)))
    for waveform, samples, seed in (("sos_twice", 13 * RATE, 42), ("carrier", 4 * RATE, 71),
                                   ("noise", 5 * RATE + 17, 12), ("zero", 2 * RATE, 91)):
        case = dict(name=f"generated_{waveform}", waveform=waveform, samples=samples, seed=seed)
        data = generated(case)
        source.write_bytes(data)
        result = NarrowbandCwExtractor(config).extract(source)
        case.update(input_sha256=hashlib.sha256(data).hexdigest(), config=asdict(config), expected=asdict(result))
        cases.append(case)

partition = []
for length in (1, 2, 3, 4, 8, 15, 16, 31, 64, 127, 1024):
    for mode in ("same", "increasing", "decreasing", "ties"):
        values = np.asarray([0.0 if mode == "same" else float(i if mode == "increasing" else length-i if mode == "decreasing" else (i*17+3)%7) for i in range(length)])
        for keep in sorted(set((1, min(3, length), min(12, length), length))):
            partition.append(dict(values=values.tolist(), keep=keep,
                expected=np.argpartition(values, -keep).tolist()))
runs = []
for index in range(36):
    values = [(bool(i % 2), ((i * 11 + index) % 9 + 1) * 0.01) for i in range(2 + index)]
    wpm = 5 + index
    runs.append(dict(runs=values, wpm=wpm, expected=_decode_runs(values, wpm=wpm)))

document = dict(schema="python-cw-differential-fixture-v1", numpy=np.__version__, python=sys.version,
    legacy_tests_passed=legacy_result.testsRun, oscillator=OSCILLATOR, cases=cases, partitions=partition, runs=runs,
    source_sha256=hashlib.sha256((ROOT / "src/telemetry_yield/cw_probe.py").read_bytes()).hexdigest(),
    note="Untrusted text candidates only. Three small binary fixtures preserve exact original NumPy-generated tests; other waveforms regenerate from integer arithmetic.")
target = Path(__file__).with_name("cw_oracle.json")
with target.open("x", encoding="utf-8") as stream:
    json.dump(document, stream, indent=2, allow_nan=False)
    stream.write("\n")
print(json.dumps(dict(cases=len(cases), partitions=len(partition), runs=len(runs), legacy_passed=legacy_result.testsRun, output=str(target))))
