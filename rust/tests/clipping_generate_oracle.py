"""One-shot offline fixture builder from immutable Python research receivers.

Not installed, imported, or called by the Rust receiver. Generated fixture bytes
and source identities are the only inputs used by the Rust unit tests.
"""
from dataclasses import asdict
import hashlib
import json
from pathlib import Path

import numpy as np
import scipy

from telemetry_yield.clipping_robust_fsk import (
    BlindPhaseFskConfig, PhaseWindowCandidate, _constant_radius_declip_exact_endpoints,
    _phase_first, decode_clipping_robust_ax25_ci16, select_ci16_phase_windows,
)
from telemetry_yield.iq_declipping import projected_bandlimited_declipping
from telemetry_yield.symbol_boundary import (
    AX25_FLAG_BITS, reference_hdlc_region, g3ruh_input_for_plain_bits, nrzi_encode_satnogs,
)


def converted(value):
    if isinstance(value, bytes):
        return list(value)
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, dict):
        return {key: converted(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [converted(item) for item in value]
    return value


def main():
    root = Path(__file__).resolve().parents[2]
    target = Path(__file__).resolve().parent / "clipping_fixtures"
    if target.exists() and any(target.iterdir()):
        raise FileExistsError(target)
    target.mkdir(exist_ok=True)
    sources = [root / "src/telemetry_yield" / name for name in (
        "clipping_robust_fsk.py", "iq_declipping.py", "clock_recovery.py", "soft_sync.py",
        "symbol_boundary.py", "crc.py", "ax25_validation.py",
    )]
    document = {"schema": "clipping-offline-python-oracle-v1",
                "runtime_dependency": False, "numpy": np.__version__, "scipy": scipy.__version__,
                "sources": [{"path": str(p), "sha256": hashlib.sha256(p.read_bytes()).hexdigest()} for p in sources],
                "cases": []}
    payload = bytes(c << 1 for c in b"CQ    ") + b"\x60"
    payload += bytes(c << 1 for c in b"RUST01") + b"\x61\x03\xf0fixture-data-v1"
    plain = bytes(AX25_FLAG_BITS) * 200 + reference_hdlc_region(payload) + bytes(AX25_FLAG_BITS) * 500
    for name, descramble, amplitude in (("unclipped_plain", False, 12000), ("clipped_g3ruh", True, 100000)):
        coded = g3ruh_input_for_plain_bits(plain) if descramble else plain
        levels = np.frombuffer(nrzi_encode_satnogs(coded), dtype=np.uint8).astype(np.float64)
        angles = np.cumsum(np.repeat(2.0 * levels - 1.0, 6) * 0.32)
        noise = np.random.default_rng(817).normal(0.0, 0.7, (len(angles), 2))
        iq = np.column_stack((amplitude * np.cos(angles), amplitude * np.sin(angles))) + noise
        raw = np.clip(np.rint(iq), -32768, 32767).astype("<i2")[:46080]
        path = target / f"{name}.ci16"
        path.write_bytes(raw.tobytes())
        config = BlindPhaseFskConfig(analysis_window_seconds=0.1, decoder_window_seconds=0.5,
            decoder_window_lead_seconds=0.1, candidate_window_limit=3, window_nms_seconds=0.2,
            phase_bins=16, short_search_timing_hypotheses=4, deep_search_timing_hypotheses=4,
            constant_radius_factors=(3.0,), phase_difference_lags=(1, 2),
            deep_maximum_attempts=3000, repair_path_maximum_attempts=3000,
            repair_region_maximum_attempts=1000, repair_event_maximum_attempts=6000,
            repair_window_maximum_attempts=12000, repair_window_maximum_events=2)
        selected = (PhaseWindowCandidate(0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0),)
        native = decode_clipping_robust_ax25_ci16(path, config=config, selected_windows=selected)
        projected = _constant_radius_declip_exact_endpoints(raw[:28800], 3.0)
        document["cases"].append({"name": name, "filename": path.name,
            "input_sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "config": asdict(config),
            "selected": [asdict(v) for v in selected],
            "window_selector": [asdict(v) for v in select_ci16_phase_windows(path, config=config)],
            "phase_first_lag1": _phase_first(projected, lag=1, config=config),
            "decode": asdict(native), "known_synthetic_frame_present": True,
            "reference_recovered_known_frame": any(f.payload == payload for f in native.frames)})
        print(json.dumps({"case": name, "frames": len(native.frames), "attempted": native.protocol_candidates_attempted}), flush=True)
    angle = np.arange(512) * 0.017
    components = np.clip(np.rint(np.column_stack((50000*np.cos(angle), 44000*np.sin(angle)))), -32768, 32767).astype("<i2")
    checkpoints = projected_bandlimited_declipping(components, sample_rate_hz=57600, bandlimit_hz=12000, iteration_checkpoints=(1, 3, 5))
    document["projection"] = {"components": components, "sample_rate_hz": 57600.0, "bandlimit_hz": 12000.0,
        "checkpoints": {str(k): {"reconstruction": a, "metrics": asdict(m)} for k, (a,m) in checkpoints.items()}}
    with (target / "oracle.json").open("x") as stream:
        json.dump(converted(document), stream, allow_nan=False)


if __name__ == "__main__":
    main()
