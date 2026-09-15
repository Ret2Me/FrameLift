#!/usr/bin/env python3
"""Native positive-audio pilot, fixed windows with reference-free search."""
from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import time

import numpy as np
import soundfile as sf

from telemetry_yield.audio_receiver import decode_audio_pcm
from telemetry_yield.ax25_validation import parse_ax25_ui
from telemetry_yield.generic_receiver import ReceiverHypothesis, WaveformHypothesis


def independently_valid_fcs(frame: bytes) -> bool:
    if len(frame) < 18:
        return False
    register = 0xffff
    for octet in frame[:-2]:
        register ^= octet
        for _ in range(8):
            register = (register >> 1) ^ (0x8408 if register & 1 else 0)
    return ((register ^ 0xffff) & 0xffff).to_bytes(2, "little") == frame[-2:]


def file_identity(path: Path) -> dict:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return {"path": str(path.resolve()), "sha256": digest.hexdigest(), "bytes": path.stat().st_size}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--mode", choices=("fsk", "afsk"), required=True)
    parser.add_argument("--baud", type=float, required=True)
    parser.add_argument("--window-seconds", type=float, default=6)
    parser.add_argument("--hop-seconds", type=float, default=3)
    parser.add_argument("--top-timing", type=int, default=16)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    info = sf.info(args.input)
    if info.channels != 1:
        raise ValueError("No silent stereo/downmix choice")
    sample_rate = int(info.samplerate)
    is_fsk = args.mode == "fsk"
    decimation = 2 if is_fsk else 5
    plan = (ReceiverHypothesis(WaveformHypothesis(
        hypothesis_id=f"positive-audio-pilot-{args.mode}-{args.baud:g}-v1",
        demodulator_id="pcm_fsk" if is_fsk else "pcm_bell202",
        symbol_rate=args.baud, decimation=decimation,
        rate_errors_ppm=(-500.0, -100.0, 0.0, 100.0, 500.0),
        phase_bins=32, top_timing_hypotheses=args.top_timing,
        modulation_family=args.mode), "ax25" if is_fsk else "ax25_plain"),)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    config = {"schema": "native-positive-ogg-pilot-plan-v1", "input": file_identity(args.input),
              "representation": "fm_demodulated", "plan": [asdict(item) for item in plan],
              "window_seconds": args.window_seconds, "hop_seconds": args.hop_seconds,
              "reference_bytes_used_for_search": False,
              "code": [file_identity(Path(__file__)),
                       file_identity(Path(__file__).resolve().parents[1] / "src/telemetry_yield/audio_receiver.py")]}
    with args.output.with_suffix(".plan.json").open("x") as stream:
        json.dump(config, stream, indent=2)
    start = time.monotonic()
    window_size = int(sample_rate * args.window_seconds)
    hop = int(sample_rate * args.hop_seconds)
    if not 0 < hop <= window_size:
        raise ValueError("invalid window/hop")
    frames = {}
    runs = []
    journal = args.output.with_suffix(".windows.jsonl")
    with sf.SoundFile(args.input) as audio, journal.open("x") as log:
        for offset in range(0, info.frames, hop):
            audio.seek(offset)
            samples = audio.read(window_size, dtype="float64")
            if len(samples) < 8192:
                break
            result = decode_audio_pcm(samples, sample_rate_hz=sample_rate,
                                      representation="fm_demodulated", plan=plan)
            for frame in result.frames:
                if not independently_valid_fcs(frame.payload) or parse_ax25_ui(frame.payload[:-2]) is None:
                    raise ValueError("Native result failed independent FCS/structure replay")
                pdu = frame.payload[:-2]
                key = pdu.hex()
                provenance = {"window_start_seconds": offset / sample_rate,
                              "waveform": frame.waveform_hypothesis_id,
                              "timing_rank": frame.timing_rank, "timing_score": frame.timing_score}
                if key not in frames:
                    frames[key] = {"payload_hex": key, "frame_with_fcs_hex": frame.payload.hex(),
                                   "payload_sha256": hashlib.sha256(pdu).hexdigest(),
                                   "payload_bytes": len(pdu), "validation": frame.validation,
                                   "independent_bitwise_crc_passed": True,
                                   "provenance": []}
                frames[key]["provenance"].append(provenance)
            row = {"offset_seconds": offset / sample_rate, "samples": len(samples),
                   "frame_instances": len(result.frames), "unique_total": len(frames),
                   "failures": list(result.failures), "timing_attempts": result.attempted_timing_hypotheses}
            log.write(json.dumps(row) + "\n")
            log.flush()
            runs.append(row)
            if len(runs) % 10 == 0 or result.frames:
                print(json.dumps({"window": len(runs), "unique": len(frames),
                                  "elapsed_seconds": round(time.monotonic() - start, 2)}), flush=True)
            if offset + window_size >= info.frames:
                break
    controls = []
    for kind, samples in (("silence", np.zeros(window_size)),
                          ("gaussian_seed_20260907", np.random.default_rng(20260907).normal(size=window_size))):
        result = decode_audio_pcm(samples, sample_rate_hz=sample_rate,
                                  representation="fm_demodulated", plan=plan)
        controls.append({"kind": kind, "duration_seconds": args.window_seconds,
                         "frames": len(result.frames), "failures": list(result.failures)})
    summary = {"schema": "native-positive-ogg-pilot-result-v1", "input": config["input"],
               "plan_identity": file_identity(args.output.with_suffix(".plan.json")),
               "elapsed_seconds": time.monotonic() - start, "window_count": len(runs),
               "failed_window_count": sum(bool(row["failures"]) for row in runs),
               "unique_pdu_count": len(frames), "unique_pdu_bytes": sum(p["payload_bytes"] for p in frames.values()),
               "frames": sorted(frames.values(), key=lambda row: row["payload_hex"]),
               "controls": controls, "publication_ready": False,
               "limitations": ["Positive convenience pilot, not an independent holdout",
                               "Tiny noise smoke tests do not establish population false-alarm rate",
                               "Audio-derived results do not imply equal-input comparison with station RF IQ"]}
    with args.output.open("x") as stream:
        json.dump(summary, stream, indent=2)
    print(json.dumps({key: summary[key] for key in ("unique_pdu_count", "unique_pdu_bytes", "window_count", "failed_window_count", "elapsed_seconds")}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
