"""Development-only immutable legacy AFSK fixtures; never a Rust dependency."""
from dataclasses import asdict, is_dataclass, replace
import hashlib
import json
from pathlib import Path

import numpy as np
import telemetry_yield.afsk1200_plugin as legacy
from tests.test_afsk1200_plugin import REFERENCE, _synthetic_afsk_iq, _nrzi, _stuff
from telemetry_yield.camras_replay import bytes_to_bits
from telemetry_yield.clock_recovery import HDLC_FLAG
from telemetry_yield.crc import append_ax25_fcs

ROOT = Path(__file__).resolve().parent
FIXTURES = ROOT / "afsk_legacy_fixtures"
FIXTURES.mkdir(exist_ok=True)

def encode(value):
    if is_dataclass(value):
        return encode(asdict(value))
    if isinstance(value, bytes):
        return list(value)
    if isinstance(value, (tuple, list)):
        return [encode(item) for item in value]
    if isinstance(value, dict):
        return {("final_address" if key == "final" else key): encode(item) for key, item in value.items()}
    if isinstance(value, np.generic):
        return value.item()
    return value

def publish(path, data):
    with path.open("xb") as file:
        file.write(data)

base = legacy.Afsk1200Config()
iq_cases = []
for name, payload, config in [
    ("positive", REFERENCE, base),
    ("rejected", b"not-an-ax25-ui-frame", base),
    ("inner_ccsds", REFERENCE[:16] + bytes.fromhex("0001c0050001aabb"), base),
    ("wrong_baud", REFERENCE, replace(base, baudrate=2400.0)),
]:
    iq = _synthetic_afsk_iq(payload)
    iq_bytes = iq.astype("<c8").tobytes()
    path = FIXTURES / (name + ".cf32")
    publish(path, iq_bytes)
    result = legacy.decode_afsk1200_iq(iq, config=config, window_start_sample=177)
    soft = legacy.bell202_soft_discriminator(iq, config)
    soft_path = FIXTURES / (name + ".soft-f32")
    publish(soft_path, soft.astype("<f4").tobytes())
    iq_cases.append({"name": name, "input": path.name, "input_sha256": hashlib.sha256(iq_bytes).hexdigest(),
                     "soft": soft_path.name, "config": encode(config), "expected": encode(result)})

rng = np.random.default_rng(20260908)
timing_cases = []
for index in range(20):
    size = [1200, 2049, 3001, 4099, 6017][index % 5]
    soft = rng.normal(size=size).astype(np.float32)
    if index == 0:
        soft[:] = 0
    if index == 1:
        soft[:] = (np.arange(size) // 7) % 2
    config = replace(base, timing_phase_bins=4 + index % 4 * 4, timing_top_n=1 + index % 8,
                     timing_rate_errors_ppm=(0.0, -2000.0, 2000.0, 0.0))
    expected = []
    for score, rate, phase, phase_index, levels in legacy._timing_candidates(soft, config):
        step = config.audio_sample_rate_hz / config.baudrate * (1 + rate * 1e-6)
        positions = phase + np.arange(16, int((soft.size-phase)/step)-16) * step
        symbols = np.interp(positions, np.arange(soft.size, dtype=np.float64), soft)
        threshold = float(np.median(symbols))
        lower, upper = symbols[symbols < threshold], symbols[symbols >= threshold]
        if lower.size >= 16 and upper.size >= 16:
            threshold = .5 * (float(lower.mean()) + float(upper.mean()))
        expected.append({"score": float(score) if np.isfinite(score) else "-inf", "rate_error_ppm": rate,
                         "phase_samples": phase, "phase_index": phase_index, "step_samples": step,
                         "threshold": threshold, "symbol_count": int(levels.size), "levels": levels.tolist()})
    timing_cases.append({"name": f"timing-{index}", "soft": soft.tolist(), "config": encode(config), "expected": expected})

level_cases = []
for index, payload in enumerate([REFERENCE, b"not-an-ax25-ui-frame", bytes(range(14)), bytes(range(256))*3]):
    frame = append_ax25_fcs(payload)
    stuffed = _stuff(bytes_to_bits(frame, lsb_first=True))
    for prefix in [(), HDLC_FLAG * 2, (1,)*9]:
        bits = prefix + HDLC_FLAG + stuffed + HDLC_FLAG
        for initial in [0, 1]:
            levels = np.array(_nrzi(bits, initial=initial), dtype=np.uint8)
            for form, stream in [("valid", levels), ("inverted", 1-levels), ("truncated", levels[:-11])]:
                level_cases.append({"name": f"levels-{index}-{len(prefix)}-{initial}-{form}", "levels": stream.tolist(),
                                    "expected": encode(legacy._crc_frames_from_levels(stream))})
document = {"schema": "legacy-afsk1200-oracle-v1", "iq_cases": iq_cases,
            "timing_cases": timing_cases, "level_cases": level_cases,
            "legacy_source_sha256": hashlib.sha256(Path(legacy.__file__).read_bytes()).hexdigest()}
publish(ROOT / "afsk_legacy_oracle.json", (json.dumps(document, separators=(",", ":"), allow_nan=False)+"\n").encode())
print(json.dumps({"iq_cases": len(iq_cases), "timing_cases": len(timing_cases), "level_cases":len(level_cases)}))
