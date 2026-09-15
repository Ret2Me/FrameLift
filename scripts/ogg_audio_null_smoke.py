#!/usr/bin/env python3
"""Independent synthetic audio smoke controls; NOT a station false-alarm study."""
from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import time

import numpy as np
from scipy.signal import lfilter

from ogg_refinement_pilot import file_identity, independently_valid_fcs
from telemetry_yield.audio_receiver import decode_audio_pcm
from telemetry_yield.diverse_timing import DiverseTimingReceiver
from telemetry_yield.fast_ax25 import FastAx25ProtocolDecoder
from telemetry_yield.generic_receiver import ReceiverHypothesis, WaveformHypothesis


FAMILIES = ("white_gaussian", "colored_ar1", "bursty_tone_interference")


def merge_identities(*groups: list[dict]) -> list[dict]:
    """Merge frozen manifests without silently choosing a conflicting hash."""
    merged = {}
    for group in groups:
        for identity in group:
            path = str(Path(identity["path"]).resolve())
            row = {"path": path, "sha256": identity["sha256"]}
            if path in merged and merged[path] != row:
                raise ValueError("conflicting frozen source identities")
            merged[path] = row
    return [merged[path] for path in sorted(merged)]


def make_control(family: str, seed: int, count: int, rate: int) -> np.ndarray:
    """Generate fresh deterministic PCM without encoded packets or archive input."""
    if family not in FAMILIES or count < 8192 or rate <= 0:
        raise ValueError("invalid control specification")
    rng = np.random.default_rng(seed)
    noise = rng.normal(size=count)
    if family == "white_gaussian":
        return noise
    if family == "colored_ar1":
        return lfilter([1.0], [1.0, -0.94], noise)
    times = np.arange(count, dtype=np.float64) / rate
    result = 0.08 * noise
    for _ in range(4):
        frequency = rng.uniform(400, min(9000, rate * 0.4))
        phase = rng.uniform(0, 2 * np.pi)
        burst_frequency = rng.uniform(0.4, 5.0)
        envelope = np.sin(2 * np.pi * burst_frequency * times + phase) > 0.25
        result += rng.uniform(0.1, 1.0) * envelope * np.sin(2 * np.pi * frequency * times + phase)
    return np.clip(result, -1.0, 1.0)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--rounds-per-family", type=int, default=30)
    args = parser.parse_args()
    if not 1 <= args.rounds_per_family <= 1000:
        raise ValueError("rounds must be in 1..1000")
    if args.output.exists():
        raise FileExistsError(args.output)
    project = Path(__file__).resolve().parents[1]
    source_plan_path = project / "work/satnogs-ogg-refinement-20260907/validation-plan.json"
    execution_plan_path = project / "work/satnogs-ogg-refinement-20260907/validation-native/14936397-diverse.plan.json"
    source_plan = json.loads(source_plan_path.read_text())
    execution_plan = json.loads(execution_plan_path.read_text())
    identities = merge_identities(
        source_plan["identities"], execution_plan["code"], execution_plan["source_dependencies"],
        [file_identity(source_plan_path), file_identity(execution_plan_path), file_identity(Path(__file__))],
    )
    for identity in identities:
        if file_identity(Path(identity["path"]))["sha256"] != identity["sha256"]:
            raise ValueError("frozen validation identity changed")
    rate, seconds = 48000, 6
    plan = (ReceiverHypothesis(WaveformHypothesis(
        hypothesis_id="positive-audio-pilot-fsk-9600-v1",
        demodulator_id="pcm_fsk", symbol_rate=9600.0, decimation=2,
        rate_errors_ppm=(-500.0, -100.0, 0.0, 100.0, 500.0),
        phase_bins=32, top_timing_hypotheses=16, modulation_family="fsk"), "ax25"),)
    receiver = DiverseTimingReceiver()
    receiver.register_protocol(FastAx25ProtocolDecoder(protocol_id="ax25", g3ruh_modes=(False, True)))
    seed_base = 202609072040
    controls = [{"family": family, "seed": seed_base + index * 10000 + repetition}
                for index, family in enumerate(FAMILIES)
                for repetition in range(args.rounds_per_family)]
    prerecord = {
        "schema": "ogg-synthetic-null-smoke-plan-v1", "sample_rate_hz": rate,
        "seconds_per_control": seconds, "controls": controls,
        "receiver_plan": [asdict(item) for item in plan], "clock_bank_policy": "diverse",
        "generator_identity": file_identity(Path(__file__)),
        "frozen_validation_identities": identities,
        "purpose": "smoke only; synthetic controls do not establish station false-accept rate",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.with_suffix(".plan.json").open("x") as stream:
        json.dump(prerecord, stream, indent=2)
    rows = []
    start = time.monotonic()
    with args.output.with_suffix(".windows.jsonl").open("x") as stream:
        for control in controls:
            pcm = make_control(control["family"], control["seed"], rate * seconds, rate)
            result = decode_audio_pcm(pcm, sample_rate_hz=rate, representation="fm_demodulated", plan=plan, receiver=receiver)
            frames = [{"frame_with_fcs_hex": frame.payload.hex(),
                       "independent_fcs_valid": independently_valid_fcs(frame.payload)} for frame in result.frames]
            row = {**control, "frames": frames, "failures": list(result.failures),
                   "timing_attempts": result.attempted_timing_hypotheses}
            rows.append(row)
            stream.write(json.dumps(row) + "\n")
            stream.flush()
    for identity in identities:
        if file_identity(Path(identity["path"]))["sha256"] != identity["sha256"]:
            raise ValueError("frozen identity changed during run")
    summary = {
        "schema": "ogg-synthetic-null-smoke-result-v1", "elapsed_seconds": time.monotonic() - start,
        "plan_identity": file_identity(args.output.with_suffix(".plan.json")),
        "controls": len(rows), "synthetic_audio_seconds": seconds * len(rows),
        "accepted_frame_instances": sum(len(row["frames"]) for row in rows),
        "failed_controls": sum(bool(row["failures"]) for row in rows),
        "by_family": {family: {"controls": sum(row["family"] == family for row in rows),
                               "accepted_frame_instances": sum(len(row["frames"]) for row in rows if row["family"] == family)}
                      for family in FAMILIES},
        "passed_smoke": all(not row["frames"] and not row["failures"] for row in rows),
        "station_false_accept_rate_qualified": False,
        "limitations": ["Only 3 synthetic distributions; no real station interference ground truth",
                        "No claim of zero false-accept probability or low population false-accept rate"],
    }
    with args.output.open("x") as stream:
        json.dump(summary, stream, indent=2)
    print(json.dumps(summary, indent=2))
    return 0 if summary["passed_smoke"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
