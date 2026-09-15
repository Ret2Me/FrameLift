#!/usr/bin/env python3
"""Small explicit positive OGG feasibility pilot; no telemetry uploads."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import urllib.parse
import urllib.request

from telemetry_yield.ax25_validation import parse_ax25_ui

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ROOT = ROOT / "work/satnogs-ogg-pilot-20260907"


def identity(path: Path) -> dict:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return {"path": str(path.resolve()), "size_bytes": path.stat().st_size,
            "sha256": digest.hexdigest()}


def save_json(path: Path, value: object) -> None:
    encoded = (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()
    if path.exists():
        if path.read_bytes() != encoded:
            raise FileExistsError(f"Refusing to replace {path}")
        return
    with path.open("xb") as stream:
        stream.write(encoded)


def fetch(url: str, path: Path, limit: int) -> None:
    parsed = urllib.parse.urlparse(url)
    allowed = {"network.satnogs.org", "network-satnogs.freetls.fastly.net",
               "s3.eu-central-1.wasabisys.com"}
    if parsed.scheme != "https" or parsed.hostname not in allowed:
        raise ValueError("Only explicit public SatNOGS HTTPS artifacts are allowed")
    if path.exists():
        return
    request = urllib.request.Request(url, headers={"User-Agent": "telemetry-yield-positive-ogg-pilot/1"})
    with urllib.request.urlopen(request, timeout=35) as response:
        if int(response.headers.get("Content-Length", "0")) > limit:
            raise ValueError("Download limit exceeded")
        content = response.read(limit + 1)
    if len(content) > limit:
        raise ValueError("Download limit exceeded")
    with path.open("xb") as stream:
        stream.write(content)


def acquire(obs_ids: list[int], root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    selection = {"schema": "satnogs-positive-ogg-pilot-selection-v1",
                 "observation_ids": obs_ids,
                 "selection": "Convenience positive controls: two previously studied CANVAS passes and one ISS AFSK pass selected from published outcomes, not native audio results.",
                 "pristine_holdout": False, "publication_claim": False,
                 "source": "public SatNOGS Network observation artifacts",
                 "primary_question": "Can known frame bytes be recovered from existing lossy OGG audio?",
                 "reference_contract": "Uploaded PDU bytes are compared unchanged, no guessed prefix removal; references alone do not independently establish CRC validity."}
    save_json(root / "selection.json", selection)
    rows = []
    for obs_id in obs_ids:
        folder = root / "inputs" / str(obs_id)
        folder.mkdir(parents=True, exist_ok=True)
        api_url = f"https://network.satnogs.org/api/observations/?id={obs_id}&format=json"
        metadata = folder / "observation.json"
        fetch(api_url, metadata, 2 << 20)
        observations = json.loads(metadata.read_text())
        if len(observations) != 1 or observations[0]["id"] != obs_id:
            raise ValueError("Observation ID mismatch")
        observation = observations[0]
        url = observation.get("payload")
        if not isinstance(url, str) or not observation.get("demoddata"):
            raise ValueError(f"Observation {obs_id} lacks positive audio/reference artifacts")
        capture = folder / "capture.ogg"
        fetch(url, capture, 64 << 20)
        probe = subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-show_format",
                                "-of", "json", str(capture)], check=True, capture_output=True,
                               text=True, timeout=30)
        probe_data = json.loads(probe.stdout)
        streams = probe_data["streams"]
        if len(streams) != 1 or streams[0].get("codec_type") != "audio" or streams[0].get("channels") != 1:
            raise ValueError("Pilot requires one mono audio stream; no silent channel selection")
        if float(probe_data["format"]["duration"]) > 1800:
            raise ValueError("Pilot capture exceeds 30 minutes")
        save_json(folder / "ffprobe.json", probe_data)
        wav = folder / "audio.wav"
        command = ["ffmpeg", "-nostdin", "-v", "error", "-n", "-i", str(capture),
                   "-map", "0:a:0", "-c:a", "pcm_f32le", str(wav)]
        if not wav.exists():
            subprocess.run(command, check=True, capture_output=True, timeout=90)
        refs = []
        for index, item in enumerate(observation["demoddata"]):
            if index >= 1000:
                raise ValueError("Reference count limit exceeded")
            reference_url = item["payload_demod"]
            reference_path = folder / f"reference-{index:04d}.bin"
            fetch(reference_url, reference_path, 1 << 20)
            data = reference_path.read_bytes()
            parsed_frame = parse_ax25_ui(data)
            refs.append({**identity(reference_path), "url": reference_url,
                         "payload_hex": data.hex(), "strict_ax25_ui_structure": parsed_frame is not None,
                         "independently_verified_crc": False})
        unique = {r["payload_hex"] for r in refs if r["strict_ax25_ui_structure"]}
        row = {"observation_id": obs_id, "observation_url": f"https://network.satnogs.org/observations/{obs_id}/",
               "satellite": observation["tle0"], "norad_cat_id": observation["norad_cat_id"],
               "mode": observation["transmitter_mode"], "baudrate": observation["transmitter_baud"],
               "audio_url": url, "ogg": identity(capture), "wav": identity(wav),
               "sample_rate_hz": int(streams[0]["sample_rate"]),
               "duration_seconds": float(probe_data["format"]["duration"]),
               "metadata": identity(metadata), "ffmpeg_command": command,
               "references": refs, "reference_unique_strict_pdu_count": len(unique),
               "reference_unique_strict_pdu_bytes": sum(len(bytes.fromhex(p)) for p in unique)}
        save_json(folder / "input-manifest.json", row)
        rows.append(row)
        print(json.dumps({"acquired": obs_id, "mode": row["mode"], "rate": row["sample_rate_hz"],
                          "duration_seconds": row["duration_seconds"], "reference_unique": len(unique)}), flush=True)
    save_json(root / "acquisition.json", {"selection": identity(root / "selection.json"), "inputs": rows})


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--observation-ids", nargs="+", type=int, default=[14366383, 14115025, 14206235])
    args = parser.parse_args()
    acquire(args.observation_ids, args.root.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
