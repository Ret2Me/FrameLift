"""Deterministic, protocol-neutral campaign planning for IQ archives.

The catalogue's ``mode`` is only a routing hint.  In particular, an FSK-like
waveform is never treated as evidence of AX.25 framing.  The inventory records
physical-layer support and protocol hints separately so the same plan can feed
any receiver implementation or future plugin bank.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
from typing import Any

from .canonical import canonical_json


SCHEMA_VERSION = "polyitan-archive-inventory-v1"
STATUS_SUPPORTED_NOW = "supported_now"
STATUS_NEEDS_PLUGIN = "needs_plugin"
STATUS_NO_IQ = "no_iq"


@dataclass(frozen=True, slots=True)
class ModeProfile:
    """Normalized routing facts extracted from one catalogue mode label."""

    normalized_mode: str
    waveform_family: str
    current_demodulators: tuple[str, ...] = ()
    protocol_hints: tuple[str, ...] = ()
    required_plugins: tuple[str, ...] = ()


_MODE_PROFILES: dict[str, ModeProfile] = {
    "FSK": ModeProfile("fsk", "fsk", ("phase_fsk",)),
    "GFSK": ModeProfile("gfsk", "fsk", ("phase_fsk",)),
    "GMSK": ModeProfile("gmsk", "fsk", ("phase_fsk",)),
    "FSK AX.25 G3RUH": ModeProfile(
        "fsk_g3ruh", "fsk", ("phase_fsk",), ("ax25", "g3ruh")
    ),
    "FSK AX.100 MODE 5": ModeProfile(
        "fsk_ax100_mode5",
        "fsk",
        ("phase_fsk",),
        ("ax100_mode5",),
        ("ax100_mode5_protocol",),
    ),
    "MSK AX.100 MODE 5": ModeProfile(
        "msk_ax100_mode5",
        "msk",
        (),
        ("ax100_mode5",),
        ("msk_demodulator", "ax100_mode5_protocol"),
    ),
    "AFSK": ModeProfile("afsk", "afsk", required_plugins=("afsk_demodulator",)),
    "BPSK": ModeProfile("bpsk", "psk", required_plugins=("bpsk_demodulator",)),
    "CW": ModeProfile("cw", "continuous_wave", required_plugins=("cw_decoder",)),
    "SSTV": ModeProfile(
        "sstv", "analog_image", required_plugins=("sstv_decoder",)
    ),
    # DUV is an operating-mode label rather than enough information to infer a
    # waveform or framing scheme.
    "DUV": ModeProfile(
        "duv", "catalog_mode_unknown", required_plugins=("duv_mode_router",)
    ),
    "APT": ModeProfile("apt", "analog_image", required_plugins=("apt_decoder",)),
}


def _clean_mode(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        return "UNKNOWN"
    return " ".join(value.strip().upper().split())


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
    return slug or "unknown"


def normalize_mode(value: object) -> ModeProfile:
    """Return a conservative mode profile without guessing the protocol."""

    cleaned = _clean_mode(value)
    profile = _MODE_PROFILES.get(cleaned)
    if profile is not None:
        return profile
    slug = _slug(cleaned)
    return ModeProfile(
        normalized_mode=slug,
        waveform_family="unknown",
        required_plugins=(f"mode_{slug}_router",),
    )


def _mapping(value: object, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be an object")
    return value


def _sequence(value: object, name: str) -> Sequence[Any]:
    if not isinstance(value, Sequence) or isinstance(
        value, (str, bytes, bytearray, memoryview)
    ):
        raise ValueError(f"{name} must be an array")
    return value


def _nonempty_text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _nonnegative_int(value: object, *, default: int = 0) -> int:
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return default


def _frame_reference(observation: Mapping[str, Any]) -> dict[str, object]:
    frames = _sequence(observation.get("frames", []), "observation.frames")
    payload_count = 0
    downloadable_count = 0
    for index, raw_frame in enumerate(frames):
        frame = _mapping(raw_frame, f"observation.frames[{index}]")
        if _nonempty_text(frame.get("payload_hex")) is not None:
            payload_count += 1
        if (
            frame.get("storage_available") is True
            and _nonempty_text(frame.get("url")) is not None
        ):
            downloadable_count += 1

    declared_count = _nonnegative_int(observation.get("frame_count"))
    metadata_present = declared_count > 0 or bool(frames)
    if payload_count:
        status = "payload_available"
    elif metadata_present:
        status = "metadata_only"
    else:
        status = "none"
    return {
        "status": status,
        "metadata_present": metadata_present,
        "declared_frame_count": declared_count,
        "catalog_frame_record_count": len(frames),
        "payload_available": payload_count > 0,
        "payload_count": payload_count,
        "downloadable_frame_count": downloadable_count,
    }


def _iq_reference(observation: Mapping[str, Any]) -> dict[str, object]:
    links = _mapping(observation.get("links", {}), "observation.links")
    artifacts = _mapping(observation.get("artifacts", {}), "observation.artifacts")
    url = _nonempty_text(links.get("iq"))
    raw_artifact = artifacts.get("iq")
    artifact = raw_artifact if isinstance(raw_artifact, Mapping) else {}
    object_key = _nonempty_text(artifact.get("object_key"))
    available = url is not None or object_key is not None
    return {
        "available": available,
        "url": url,
        "object_key": object_key,
        "format": _nonempty_text(artifact.get("format")),
        "size_bytes": (
            _nonnegative_int(artifact.get("size_bytes"))
            if artifact.get("size_bytes") is not None
            else None
        ),
    }


def _campaign_record(raw: object) -> dict[str, object]:
    observation = _mapping(raw, "observation")
    observation_id = observation.get("observation_id")
    if not isinstance(observation_id, int) or isinstance(observation_id, bool):
        raise ValueError("observation_id must be an integer")

    source_mode = _nonempty_text(observation.get("mode")) or "UNKNOWN"
    profile = normalize_mode(source_mode)
    iq = _iq_reference(observation)
    reference = _frame_reference(observation)

    if not iq["available"]:
        status = STATUS_NO_IQ
        action = "await_iq"
        lane = "blocked_archive"
    elif profile.required_plugins:
        status = STATUS_NEEDS_PLUGIN
        action = "install_or_implement_plugins"
        lane = "plugin_development"
    else:
        status = STATUS_SUPPORTED_NOW
        action = "enqueue_current_waveform_bank"
        lane = (
            "missing_telemetry_discovery"
            if reference["status"] == "none"
            else "reference_calibration"
        )

    station = observation.get("station")
    station_map = station if isinstance(station, Mapping) else {}
    protocol_strategy = (
        "catalog_protocol_hint"
        if profile.protocol_hints
        else "protocol_neutral_probe_bank"
    )
    return {
        "observation_id": observation_id,
        "satellite_id": _nonempty_text(observation.get("satellite_id")),
        "start": _nonempty_text(observation.get("start")),
        "end": _nonempty_text(observation.get("end")),
        "station_id": station_map.get("id"),
        "frequency_hz": observation.get("frequency_hz"),
        "source_mode": source_mode,
        "normalized_mode": profile.normalized_mode,
        "waveform_family": profile.waveform_family,
        "protocol_hints": list(profile.protocol_hints),
        "iq": iq,
        "reference_frames": reference,
        "status": status,
        "campaign_lane": lane,
        "route": {
            "action": action,
            "segment_detector": "ai_sensitive_segment_detector",
            "demodulator_candidates": list(profile.current_demodulators),
            "protocol_strategy": protocol_strategy,
            "required_plugins": list(profile.required_plugins),
        },
    }


def _count_table(counter: Counter[str], key: str) -> list[dict[str, object]]:
    return [
        {key: name, "count": count}
        for name, count in sorted(counter.items(), key=lambda item: (-item[1], item[0]))
    ]


def build_archive_inventory(
    catalogue: Mapping[str, Any], *, source_sha256: str | None = None
) -> dict[str, object]:
    """Build a stable full-catalogue processing plan.

    ``supported_now`` means the current waveform front end can accept the IQ;
    it does not claim that the link protocol is known.  Unknown framing is sent
    to a protocol-neutral probe bank rather than being labelled AX.25.
    """

    observations = _sequence(catalogue.get("observations"), "observations")
    records = [_campaign_record(item) for item in observations]
    records.sort(key=lambda item: int(item["observation_id"]))
    ids = [int(item["observation_id"]) for item in records]
    if len(ids) != len(set(ids)):
        raise ValueError("observation_id values must be unique")

    declared_count = catalogue.get("observation_count")
    if declared_count is not None and declared_count != len(records):
        raise ValueError("declared observation_count does not match observations")

    source_modes = Counter(str(item["source_mode"]) for item in records)
    normalized_modes = Counter(str(item["normalized_mode"]) for item in records)
    statuses = Counter(str(item["status"]) for item in records)
    lanes = Counter(str(item["campaign_lane"]) for item in records)
    iq_available = sum(bool(item["iq"]["available"]) for item in records)  # type: ignore[index]
    iq_downloadable = sum(
        item["iq"]["url"] is not None for item in records  # type: ignore[index]
    )
    iq_object_key_available = sum(
        item["iq"]["object_key"] is not None for item in records  # type: ignore[index]
    )
    reference_statuses = Counter(
        str(item["reference_frames"]["status"]) for item in records  # type: ignore[index]
    )
    payload_frames = sum(
        int(item["reference_frames"]["payload_count"]) for item in records  # type: ignore[index]
    )
    metadata_frames = sum(
        int(item["reference_frames"]["catalog_frame_record_count"])
        for item in records  # type: ignore[index]
    )

    source: dict[str, object] = {
        "name": catalogue.get("source"),
        "catalog_generated_at": catalogue.get("generated_at"),
        "excluded_bands": list(catalogue.get("excluded_bands", [])),
        "excluded_station_ids": list(catalogue.get("excluded_station_ids", [])),
    }
    if source_sha256 is not None:
        source["sha256"] = source_sha256

    return {
        "schema_version": SCHEMA_VERSION,
        "source": source,
        "capability_contract": {
            "supported_now_means": (
                "current waveform demodulator available; protocol may still require probing"
            ),
            "current_waveform_families": ["fsk"],
            "current_demodulators": ["phase_fsk"],
            "protocol_default": None,
            "fsk_implies_ax25": False,
            "segment_detector_is_scheduling_only": True,
        },
        "summary": {
            "observation_count": len(records),
            "iq_available_count": iq_available,
            "iq_downloadable_count": iq_downloadable,
            "iq_object_key_available_count": iq_object_key_available,
            "iq_missing_count": len(records) - iq_available,
            "reference_payload_observation_count": reference_statuses[
                "payload_available"
            ],
            "reference_metadata_only_observation_count": reference_statuses[
                "metadata_only"
            ],
            "no_reference_observation_count": reference_statuses["none"],
            "catalog_frame_record_count": metadata_frames,
            "reference_payload_frame_count": payload_frames,
            "status_counts": _count_table(statuses, "status"),
            "campaign_lane_counts": _count_table(lanes, "campaign_lane"),
            "source_mode_counts": _count_table(source_modes, "mode"),
            "normalized_mode_counts": _count_table(
                normalized_modes, "normalized_mode"
            ),
        },
        "observations": records,
    }


def inventory_from_path(path: Path) -> dict[str, object]:
    """Load a catalogue and bind the inventory to its exact source bytes."""

    source_bytes = path.read_bytes()
    parsed = json.loads(source_bytes)
    catalogue = _mapping(parsed, "catalogue")
    return build_archive_inventory(
        catalogue, source_sha256=hashlib.sha256(source_bytes).hexdigest()
    )


def write_archive_inventory(source_path: Path, output_path: Path) -> dict[str, object]:
    """Write the deterministic plan as readable, key-sorted JSON."""

    inventory = inventory_from_path(source_path)
    # Round-trip through canonical_json to exercise the same strict JSON value
    # contract used elsewhere, then pretty-print for review.
    canonical = canonical_json(inventory)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(
            json.loads(canonical),
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return inventory
