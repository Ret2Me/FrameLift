#!/usr/bin/env python3
"""Local 6-second operational canary from an already exposed development OGG.

No discovery/download, new-cohort result, archive-gain claim or packet submission.
The canary retains its tiny derived WAV for inspection, unlike the cohort runner.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import soundfile as sf

import ogg_archive_campaign as campaign


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    args = parser.parse_args()
    root = args.root.absolute()
    root.mkdir(parents=True, exist_ok=False)
    source = campaign.ROOT / "work/satnogs-ogg-pilot-20260907/inputs/14366383/capture.ogg"
    identities = [campaign.identity(Path(campaign.__file__)), campaign.identity(source)]
    campaign.durable_json(root / "canary-plan.json", {
        "purpose": "operational canary only, already exposed development audio",
        "seconds": 6, "identities": identities, "reference_input": False,
        "new_cohort_started": False, "telemetry_submission": False,
    })
    campaign.execute(["/usr/bin/ffprobe", "-v", "error", "-show_streams", "-of", "json", str(source)],
                     root, "probe", root, timeout=60)
    wav = root / "audio.wav"
    campaign.execute(["/usr/bin/ffmpeg", "-nostdin", "-v", "error", "-n", "-i", str(source),
                      "-t", "6", "-map", "0:a:0", "-c:a", "pcm_f32le", str(wav)],
                     root, "convert", root, timeout=60, output_limit=campaign.LIMITS["wav_bytes"])
    info = sf.info(wav)
    if (info.frames, info.samplerate, info.channels, info.subtype) != (288000, 48000, 1, "FLOAT"):
        raise ValueError("canary PCM contract changed")
    campaign.execute([str(campaign.PYTHON), str(campaign.NATIVE), "--input", str(wav),
                      "--output", str(root / "native.json"), "--mode", "fsk", "--baud", "9600",
                      "--decoder", "fast", "--bank", "diverse", "--window-seconds", "6",
                      "--hop-seconds", "3", "--top-timing", "16"], root, "native", root, timeout=120)
    campaign.execute([str(campaign.PYTHON), str(campaign.BASELINE), "--input", str(wav),
                      "--output", str(root / "baseline"), "--observation", "14366383",
                      "--mode", "fsk-g3ruh", "--baud", "9600", "--representation", "fm_demodulated",
                      "--timeout", "90"], root, "baseline", root, timeout=110)
    campaign.validate_execution(root, info.frames, info.samplerate)
    native, baseline = campaign.read(root / "native.json"), campaign.read(root / "baseline/result.json")
    if not native["input"]["sha256"] == baseline["input_sha256"] == campaign.identity(wav)["sha256"]:
        raise ValueError("canary same-PCM identity mismatch")
    for item in identities:
        campaign.verify(item)
    result = {"operational_canary_passed": True, "seconds": 6,
              "native_count": native["unique_pdu_count"],
              "baseline_count": baseline["strict_ax25_ui_unique_count"],
              "not_a_scientific_recall_or_novelty_result": True,
              "retained_wav": campaign.identity(wav), "retained_ogg": campaign.identity(source),
              "identities": identities, "no_telemetry_submitted": True}
    campaign.durable_json(root / "canary-result.json", result)
    print("Local operational canary passed; not a new-cohort result.")


if __name__ == "__main__":
    main()
