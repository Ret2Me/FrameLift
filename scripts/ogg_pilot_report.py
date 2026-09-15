#!/usr/bin/env python3
"""Validate and compare retained pilot frame bytes; never reruns receivers."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from telemetry_yield.ax25_validation import parse_ax25_ui
from ogg_native_pilot import independently_valid_fcs


def compare_sets(reference: set[bytes], baseline: set[bytes], native: set[bytes]) -> dict:
    sets = {
        "archive": reference, "baseline_same_audio": baseline, "native_same_audio": native,
        "native_matching_archive": native & reference,
        "baseline_matching_archive": baseline & reference,
        "native_only_vs_baseline": native - baseline,
        "baseline_only_vs_native": baseline - native,
        "native_new_vs_archive": native - reference,
        "baseline_new_vs_archive": baseline - reference,
        "native_new_vs_archive_and_baseline": native - (reference | baseline),
        "audio_union": native | baseline,
        "archive_plus_audio_union": reference | native | baseline,
        "archive_not_reproduced_by_audio": reference - (native | baseline),
    }
    return {key: {"count": len(values), "bytes": sum(map(len, values)),
                  "pdus": [p.hex() for p in sorted(values)]}
            for key, values in sets.items()}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def compare_one(folder: Path, native_path: Path, baseline_path: Path) -> dict:
    manifest = json.loads((folder / "input-manifest.json").read_text())
    native = json.loads(native_path.read_text())
    baseline = json.loads(baseline_path.read_text())
    actual_wav_sha = sha(folder / "audio.wav")
    if {actual_wav_sha, manifest["wav"]["sha256"], native["input"]["sha256"], baseline["input_sha256"]} != {actual_wav_sha}:
        raise ValueError("Same-audio identity mismatch")
    if not baseline["completed"] or baseline["malformed_kiss_records"] or native["failed_window_count"]:
        raise ValueError("An incomplete/malformed run cannot be scored as zero")
    nframes = set()
    for item in native["frames"]:
        frame = bytes.fromhex(item["frame_with_fcs_hex"])
        pdu = bytes.fromhex(item["payload_hex"])
        if frame[:-2] != pdu or not independently_valid_fcs(frame) or parse_ax25_ui(pdu) is None:
            raise ValueError("Native FCS/PDU replay failed")
        if hashlib.sha256(pdu).hexdigest() != item["payload_sha256"]:
            raise ValueError("Native payload digest mismatch")
        nframes.add(pdu)
    bframes = {bytes.fromhex(item["hex"]) for item in baseline["pdus"] if item["strict_ax25_ui"]}
    if any(parse_ax25_ui(pdu) is None for pdu in bframes):
        raise ValueError("Baseline AX25 validation mismatch")
    refs = set()
    invalid = []
    for item in manifest["references"]:
        data = Path(item["path"]).read_bytes()
        if data.hex() != item["payload_hex"] or hashlib.sha256(data).hexdigest() != item["sha256"]:
            raise ValueError("Reference bytes drift")
        if parse_ax25_ui(data) is not None:
            refs.add(data)
        else:
            invalid.append({"url": item["url"], "bytes": len(data), "sha256": item["sha256"]})
    if len(nframes) != native["unique_pdu_count"] or len(bframes) != baseline["strict_ax25_ui_unique_count"]:
        raise ValueError("Receiver count mismatch")
    return {"observation_id": manifest["observation_id"], "satellite": manifest["satellite"],
            "observation_url": manifest["observation_url"], "mode": manifest["mode"],
            "duration_seconds": manifest["duration_seconds"], "positive_reference_eligible": bool(refs),
            "same_audio_sha256": actual_wav_sha,
            "reference_invalid_objects": invalid,
            "counts": compare_sets(refs, bframes, nframes),
            "native_wall_seconds": native["elapsed_seconds"],
            "baseline_wall_seconds": baseline["wall_seconds"],
            "controls": native["controls"],
            "artifacts": {str(p): sha(p) for p in (folder / "input-manifest.json", native_path, baseline_path)}}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("work/satnogs-ogg-pilot-20260907"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--observation-ids", type=int, nargs="+", default=[14366383, 14115025])
    args = parser.parse_args()
    rows = [compare_one(args.root / "inputs" / str(obs), args.root / "native" / f"{obs}-v1.json",
                        args.root / "baseline" / f"{obs}-fsk-g3ruh" / "result.json")
            for obs in args.observation_ids]
    totals = {key: {metric: sum(row["counts"][key][metric] for row in rows) for metric in ("count", "bytes")}
              for key in rows[0]["counts"]}
    summary = {"schema": "public-satnogs-positive-ogg-feasibility-pilot-v1", "observations": rows,
               "totals_per_observation_deduplication": totals,
               "qualification": "Exploratory positive convenience pilot; not a blind holdout or population estimate",
               "identity_unit": "observation ID plus entire AX25 PDU bytes excluding independently checked FCS",
               "publication_ready": False, "deployment_ready": False,
               "baseline_contract": "gr_satellites 5.9.0 component, same PCM; not exact historical station runtime",
               "reference_contract": "unchanged public uploaded PDU; structure verified, original FCS unavailable",
               "compute_caveat": "Wall times measured with concurrent IQ campaign; baseline and native use different search budgets",
               "source_information": "Lossy, FM-demodulated OGG decoded to mono 48kHz float PCM, not original RF IQ"}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as stream:
        json.dump(summary, stream, indent=2)
    lines = ["# Pilotaż odzyskiwania ramek z publicznych OGG SatNOGS", "",
             "Mała, celowo dobrana próba dodatnia. Nie jest testem ślepym ani dowodem szerokiej przewagi.", "",
             "| Obserwacja | W archiwum | gr-satellites z OGG | Nasza metoda z OGG | Nasze dodatkowe wobec obu |",
             "|---|---:|---:|---:|---:|"]
    for row in rows:
        c = row["counts"]
        lines.append(f"| {row['observation_id']} | {c['archive']['count']} | {c['baseline_same_audio']['count']} | {c['native_same_audio']['count']} | {c['native_new_vs_archive_and_baseline']['count']} |")
    lines += ["", "Każdy wynik natywny przeszedł ponowną bitową kontrolę oryginalnej FCS i struktury AX.25.",
              "Porównanie metod używa identycznych plików PCM. Wynik archiwalny nie jest pełną prawdą o wszystkich nadanych ramkach.",
              "Krótkie kontrole ciszy i szumu są tylko testem podstawowym, nie pomiarem niskiej częstości fałszywych alarmów.",
              "Nie dopasowywano zdekodowanych bajtów do wzorca w procesie dekodowania. Wyniki archiwalne służą wyłącznie porównaniu.",
              "Koszt obliczeń jest nierówny: metoda natywna przeszukuje bank hipotez; czas mierzono przy równoległym innym eksperymencie.",
              "Pełne bajty, przyrosty, braki, SHA-256 oraz czasy są w towarzyszącym pliku JSON."]
    with args.output.with_suffix(".md").open("x") as stream:
        stream.write("\n".join(lines) + "\n")
    print(json.dumps(totals, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
