"""Versioned, resumable file API for the built-in FSK-family AX.25 route."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Mapping

from .candidate_ledger import DetectionRecord, build_candidate_ledger
from .canonical import canonical_json
from .fsk_ax25_plugin import (
    FskAx25Config,
    FskDecodeResult,
    FskRejectedFrame,
    FskStrictFrame,
    ci16le_window_spans,
    decode_fsk_ax25_iq,
    iter_fsk_ax25_ci16le_windows,
    sha256_file,
)
from .models import JsonValue


API_VERSION = "telemetry-yield-fsk-ax25-file-api-v1"
SCHEMA_VERSION = "fsk-ax25-file-result-v1"
CHECKPOINT_SCHEMA_VERSION = "fsk-ax25-file-checkpoint-v1"
BASELINE_SCHEMA_VERSION = "candidate-ledger-origins-v1"
PLUGIN_ID = "channel_conditioned_phase_fsk"
PLUGIN_VERSION = "1.0.0"
BRANCH_ID = "native-fsk-ax25"
PROTOCOL_ADAPTER_ID = "ax25_g3ruh"  # Effective route records plain vs G3RUH.
PROTOCOL_ID = "ax25"
_MAX_BASELINE_JSON_BYTES = 64 * 1024 * 1024


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_document(value: Mapping[str, Any]) -> str:
    return _sha256_bytes(canonical_json(value).encode("utf-8"))


def _valid_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value.casefold())
    )


def _atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    payload = (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary_path = Path(temporary)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


@dataclass(frozen=True, slots=True)
class FskAx25RunConfig:
    """Fail-closed, content-fingerprinted execution contract."""

    plugin: FskAx25Config = FskAx25Config()
    segment_start_sample: int = 0
    segment_sample_count: int | None = None
    event_key: str = "capture-segment"
    expected_input_size_bytes: int | None = None
    expected_input_sha256: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.plugin, FskAx25Config):
            raise TypeError("plugin must be an FskAx25Config")
        if (
            isinstance(self.segment_start_sample, bool)
            or not isinstance(self.segment_start_sample, int)
            or self.segment_start_sample < 0
        ):
            raise ValueError("segment_start_sample must be a non-negative integer")
        if self.segment_sample_count is not None and (
            isinstance(self.segment_sample_count, bool)
            or not isinstance(self.segment_sample_count, int)
            or self.segment_sample_count <= 0
        ):
            raise ValueError("segment_sample_count must be positive or None")
        if not isinstance(self.event_key, str) or not self.event_key.strip():
            raise ValueError("event_key must be a non-empty string")
        if self.expected_input_size_bytes is not None and (
            isinstance(self.expected_input_size_bytes, bool)
            or not isinstance(self.expected_input_size_bytes, int)
            or self.expected_input_size_bytes <= 0
        ):
            raise ValueError("expected_input_size_bytes must be positive or None")
        if self.expected_input_sha256 is not None:
            if not _valid_sha256(self.expected_input_sha256):
                raise ValueError("expected_input_sha256 must be a SHA-256 digest")
            object.__setattr__(
                self, "expected_input_sha256", self.expected_input_sha256.casefold()
            )

    def to_dict(self) -> dict[str, JsonValue]:
        return json.loads(json.dumps(asdict(self)))

    @property
    def fingerprint(self) -> str:
        return _sha256_document(self.to_dict())


def _address_document(value: Any) -> dict[str, JsonValue]:
    return {
        "callsign": value.callsign,
        "ssid": value.ssid,
        "command_or_repeated": value.command_or_repeated,
        "final": value.final,
    }


def _timing_document(frame: FskStrictFrame | FskRejectedFrame) -> dict[str, Any]:
    return {
        "window_start_sample": frame.window_start_sample,
        "timing_rank": frame.timing_rank,
        "rate_error_ppm": frame.rate_error_ppm,
        "phase_samples": frame.phase_samples,
        "step_samples": frame.step_samples,
        "threshold": frame.threshold,
    }


def _trusted_document(frame: FskStrictFrame, *, window_stop_sample: int) -> dict[str, Any]:
    return {
        "classification": "trusted",
        "original_frame_hex": frame.frame_with_fcs.hex(),
        "original_frame_sha256": _sha256_bytes(frame.frame_with_fcs),
        "normalized_payload_hex": frame.normalized_pdu.hex(),
        "normalized_payload_sha256": _sha256_bytes(frame.normalized_pdu),
        "validation_layers": [
            "complete_hdlc_flags_and_unstuffing",
            "crc16_x25_valid_complete_frame",
            "strict_parse_ax25_ui_after_verified_fcs_removal",
        ],
        "ax25": {
            "destination": _address_document(frame.ax25.destination),
            "source": _address_document(frame.ax25.source),
            "digipeaters": [
                _address_document(value) for value in frame.ax25.digipeaters
            ],
            "pid": frame.ax25.pid,
            "information_hex": frame.ax25.information.hex(),
            "information_sha256": _sha256_bytes(frame.ax25.information),
        },
        "provenance": {
            **_timing_document(frame),
            "window_stop_sample": window_stop_sample,
        },
    }


def _rejected_document(
    frame: FskRejectedFrame, *, window_stop_sample: int
) -> dict[str, Any]:
    return {
        "classification": "rejected",
        "original_frame_hex": frame.frame_with_fcs.hex(),
        "original_frame_sha256": _sha256_bytes(frame.frame_with_fcs),
        "validation_layers": [
            "complete_hdlc_flags_and_unstuffing",
            "crc16_x25_valid_complete_frame",
        ],
        "rejection_reason": frame.rejection_reason,
        "provenance": {
            **_timing_document(frame),
            "window_stop_sample": window_stop_sample,
        },
    }


def _window_document(start: int, stop: int, result: FskDecodeResult) -> dict[str, Any]:
    document = {
        "start_sample": start,
        "stop_sample": stop,
        "sample_count": stop - start,
        "metrics": {
            "timing_hypotheses_examined": result.timing_hypotheses_examined,
            "crc_valid_candidate_detections": result.crc_valid_candidates,
            "strict_ax25_rejection_detections": len(result.rejected_frames),
            "carrier_offset_hz": result.carrier_offset_hz,
            "normalization_scale": result.normalization_scale,
            "degenerate": result.degenerate,
            "failure": result.failure,
        },
        "trusted": [
            _trusted_document(frame, window_stop_sample=stop)
            for frame in result.frames
        ],
        "rejected": [
            _rejected_document(frame, window_stop_sample=stop)
            for frame in result.rejected_frames
        ],
    }
    return {**document, "window_result_sha256": _sha256_document(document)}


def _empty_checkpoint(
    *,
    attempt_fingerprint: str,
    source_sha256: str,
    source_size_bytes: int,
    config: FskAx25RunConfig,
    baseline_sha256: str | None,
) -> dict[str, Any]:
    return {
        "schema_version": CHECKPOINT_SCHEMA_VERSION,
        "attempt_fingerprint": attempt_fingerprint,
        "source_sha256": source_sha256,
        "source_size_bytes": source_size_bytes,
        "config_sha256": config.fingerprint,
        "external_baseline_sha256": baseline_sha256,
        "windows": {},
    }


def _validate_checkpoint_windows(
    windows: Mapping[str, Any], spans: tuple[tuple[int, int], ...]
) -> None:
    expected = {str(start): stop for start, stop in spans}
    for key, raw in windows.items():
        if key not in expected or not isinstance(raw, Mapping):
            raise ValueError("FSK checkpoint contains an unknown or invalid window")
        digest = raw.get("window_result_sha256")
        if not _valid_sha256(digest):
            raise ValueError("FSK checkpoint window digest is invalid")
        content = dict(raw)
        del content["window_result_sha256"]
        if _sha256_document(content) != digest:
            raise ValueError("FSK checkpoint window digest does not match")
        if raw.get("start_sample") != int(key) or raw.get("stop_sample") != expected[key]:
            raise ValueError("FSK checkpoint window coordinates do not match")
        if not isinstance(raw.get("trusted"), list) or not isinstance(
            raw.get("rejected"), list
        ):
            raise ValueError("FSK checkpoint candidate lists are invalid")


def _load_checkpoint(
    path: Path,
    *,
    expected: Mapping[str, Any],
    resume: bool,
) -> dict[str, Any]:
    if not path.exists() or not resume:
        return dict(expected)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f"invalid FSK checkpoint: {error}") from error
    if not isinstance(value, dict) or not isinstance(value.get("windows"), dict):
        raise ValueError("invalid FSK checkpoint structure")
    for key in (
        "schema_version",
        "attempt_fingerprint",
        "source_sha256",
        "source_size_bytes",
        "config_sha256",
        "external_baseline_sha256",
    ):
        if value.get(key) != expected.get(key):
            raise ValueError(f"FSK checkpoint {key} does not match this attempt")
    return value


def _detection_from_document(value: Mapping[str, Any]) -> DetectionRecord:
    required = (
        "capture_sha256",
        "segment_start_sample",
        "segment_sample_count",
        "branch_id",
        "source_role",
        "plugin_id",
        "plugin_version",
        "protocol_id",
        "original_frame_hex",
        "normalized_payload_hex",
        "validation_layers",
        "validated",
        "hypothesis_fingerprint",
        "config_fingerprint",
        "event_key",
        "event_time_seconds",
    )
    missing = [key for key in required if key not in value]
    if missing:
        raise ValueError(f"baseline detection missing fields: {', '.join(missing)}")
    try:
        original = bytes.fromhex(value["original_frame_hex"])
        normalized = bytes.fromhex(value["normalized_payload_hex"])
    except (TypeError, ValueError) as error:
        raise ValueError("baseline detection contains invalid hex") from error
    layers = value["validation_layers"]
    provenance = value.get("provenance", {})
    if not isinstance(layers, list) or not isinstance(provenance, Mapping):
        raise ValueError("baseline validation/provenance fields are invalid")
    return DetectionRecord(
        capture_sha256=value["capture_sha256"],
        segment_start_sample=value["segment_start_sample"],
        segment_sample_count=value["segment_sample_count"],
        branch_id=value["branch_id"],
        source_role=value["source_role"],
        plugin_id=value["plugin_id"],
        plugin_version=value["plugin_version"],
        protocol_id=value["protocol_id"],
        original_frame=original,
        normalized_payload=normalized,
        validation_layers=tuple(layers),
        validated=value["validated"],
        hypothesis_fingerprint=value["hypothesis_fingerprint"],
        config_fingerprint=value["config_fingerprint"],
        event_key=value["event_key"],
        event_time_seconds=value["event_time_seconds"],
        provenance=dict(provenance),
    )


def _load_external_baseline(
    path: Path | None,
    *,
    capture_sha256: str,
    event_key: str,
) -> tuple[str | None, tuple[DetectionRecord, ...]]:
    if path is None:
        return None, ()
    if not path.is_file() or path.stat().st_size > _MAX_BASELINE_JSON_BYTES:
        raise ValueError("external baseline must be a bounded regular JSON file")
    payload = path.read_bytes()
    digest = _sha256_bytes(payload)
    try:
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f"invalid external baseline JSON: {error}") from error
    if not isinstance(value, dict) or value.get("schema_version") != BASELINE_SCHEMA_VERSION:
        raise ValueError("unsupported external baseline schema")
    raw = value.get("detections")
    if not isinstance(raw, list) or any(not isinstance(item, Mapping) for item in raw):
        raise ValueError("external baseline detections must be a list of objects")
    detections = tuple(_detection_from_document(item) for item in raw)
    for detection in detections:
        if detection.source_role != "baseline":
            raise ValueError("external baseline source_role must be baseline")
        if detection.capture_sha256 != capture_sha256:
            raise ValueError("external baseline capture hash does not match IQ")
        if detection.event_key != event_key or detection.protocol_id != PROTOCOL_ID:
            raise ValueError("external baseline route identity does not match")
        if detection.validated is not True or not detection.validation_layers:
            raise ValueError("external baseline contains an unvalidated detection")
    return digest, detections


def _native_detection(
    frame: Mapping[str, Any],
    *,
    capture_sha256: str,
    config: FskAx25RunConfig,
) -> DetectionRecord:
    provenance = frame["provenance"]
    start = int(provenance["window_start_sample"])
    stop = int(provenance["window_stop_sample"])
    hypothesis = {
        "rate_error_ppm": provenance["rate_error_ppm"],
        "phase_samples": provenance["phase_samples"],
        "step_samples": provenance["step_samples"],
        "threshold": provenance["threshold"],
        "plugin_id": PLUGIN_ID,
        "plugin_version": PLUGIN_VERSION,
    }
    return DetectionRecord(
        capture_sha256=capture_sha256,
        segment_start_sample=start,
        segment_sample_count=stop - start,
        branch_id=BRANCH_ID,
        source_role="candidate",
        plugin_id=PLUGIN_ID,
        plugin_version=PLUGIN_VERSION,
        protocol_id=PROTOCOL_ID,
        original_frame=bytes.fromhex(frame["original_frame_hex"]),
        normalized_payload=bytes.fromhex(frame["normalized_payload_hex"]),
        validation_layers=tuple(frame["validation_layers"]),
        validated=True,
        hypothesis_fingerprint=_sha256_document(hypothesis),
        config_fingerprint=config.fingerprint,
        event_key=config.event_key,
        event_time_seconds=start / config.plugin.sample_rate_hz,
        provenance={
            "protocol_adapter_id": (
                PROTOCOL_ADAPTER_ID if config.plugin.g3ruh else "ax25_plain"
            ),
            "window_stop_sample": stop,
            "timing_rank": provenance["timing_rank"],
            "rate_error_ppm": provenance["rate_error_ppm"],
            "phase_samples": provenance["phase_samples"],
            "step_samples": provenance["step_samples"],
            "threshold": provenance["threshold"],
        },
    )


def _build_result(
    *,
    source_path: Path,
    source_sha256: str,
    source_size_bytes: int,
    config: FskAx25RunConfig,
    spans: tuple[tuple[int, int], ...],
    checkpoint: Mapping[str, Any],
    baseline_path: Path | None,
    baseline_sha256: str | None,
    baseline: tuple[DetectionRecord, ...],
) -> dict[str, Any]:
    raw_windows = checkpoint["windows"]
    assert isinstance(raw_windows, Mapping)
    windows = [raw_windows[str(start)] for start, _ in spans if str(start) in raw_windows]
    trusted = [item for window in windows for item in window["trusted"]]
    rejected = [item for window in windows for item in window["rejected"]]
    raw_by_sha: dict[str, dict[str, Any]] = {}
    for item in (*trusted, *rejected):
        digest = item["original_frame_sha256"]
        raw_by_sha.setdefault(
            digest,
            {
                "classification": "raw_crc_valid_candidate",
                "original_frame_hex": item["original_frame_hex"],
                "original_frame_sha256": digest,
            },
        )
    native = tuple(
        _native_detection(frame, capture_sha256=source_sha256, config=config)
        for frame in trusted
    )
    ledger = build_candidate_ledger((*baseline, *native))
    complete = len(windows) == len(spans)
    materialized = sum(int(window["sample_count"]) for window in windows)
    return {
        "api_version": API_VERSION,
        "schema_version": SCHEMA_VERSION,
        "status": "completed" if complete else "partial",
        "attempt_fingerprint": checkpoint["attempt_fingerprint"],
        "source": {
            "path": str(source_path),
            "sample_format": "ci16_le",
            "sample_rate_hz": config.plugin.sample_rate_hz,
            "size_bytes": source_size_bytes,
            "sha256": source_sha256,
            "segment_start_sample": config.segment_start_sample,
            "segment_sample_count": config.segment_sample_count,
        },
        "route": {
            "modulation_family": "binary_fsk_gfsk_gmsk",
            "demodulator_id": PLUGIN_ID,
            "demodulator_version": PLUGIN_VERSION,
            "protocol_adapter_id": (
                PROTOCOL_ADAPTER_ID if config.plugin.g3ruh else "ax25_plain"
            ),
            "protocol_id": PROTOCOL_ID,
            "qualification": "generic_builtin_plugin_not_mission_profile",
        },
        "effective_config": config.to_dict(),
        "config_sha256": config.fingerprint,
        "external_baseline": {
            "path": str(baseline_path) if baseline_path is not None else None,
            "sha256": baseline_sha256,
            "trusted_detection_count": len(baseline),
        },
        "resource_counters": {
            "windows_total": len(spans),
            "windows_completed": len(windows),
            "input_bytes_hashed": source_size_bytes,
            "complex_samples_materialized_cumulative": materialized,
            "maximum_complex_samples_materialized_at_once": max(
                (stop - start for start, stop in spans), default=0
            ),
            "maximum_iq_window_bytes": max(
                ((stop - start) * 16 for start, stop in spans), default=0
            ),
            "timing_hypotheses_examined": sum(
                int(window["metrics"]["timing_hypotheses_examined"])
                for window in windows
            ),
            "crc_valid_candidate_detections": sum(
                int(window["metrics"]["crc_valid_candidate_detections"])
                for window in windows
            ),
            "strict_ax25_rejection_detections": sum(
                int(window["metrics"]["strict_ax25_rejection_detections"])
                for window in windows
            ),
            "degenerate_windows": sum(
                bool(window["metrics"]["degenerate"]) for window in windows
            ),
        },
        "candidates": {
            "raw": [raw_by_sha[key] for key in sorted(raw_by_sha)],
            "rejected": sorted(
                rejected,
                key=lambda item: (
                    item["original_frame_sha256"],
                    item["provenance"]["window_start_sample"],
                ),
            ),
            "trusted": sorted(
                trusted,
                key=lambda item: (
                    item["normalized_payload_sha256"],
                    item["provenance"]["window_start_sample"],
                ),
            ),
        },
        "candidate_ledger": ledger.to_dict(),
        "resume": {
            "supported": True,
            "checkpoint_schema_version": CHECKPOINT_SCHEMA_VERSION,
            "completed_window_starts": sorted(int(key) for key in raw_windows),
        },
        "failures": [
            {
                "window_start_sample": int(window["start_sample"]),
                "reason": window["metrics"]["failure"],
            }
            for window in windows
            if window["metrics"]["failure"] is not None
        ],
    }


def run_fsk_ax25_file(
    source: str | Path,
    output: str | Path,
    *,
    config: FskAx25RunConfig = FskAx25RunConfig(),
    checkpoint_path: str | Path | None = None,
    external_baseline_path: str | Path | None = None,
    resume: bool = True,
    max_windows: int | None = None,
    api_version: str = API_VERSION,
) -> dict[str, Any]:
    """Run or resume one bounded FSK/AX.25 file attempt."""

    if api_version != API_VERSION:
        raise ValueError(f"unsupported FSK AX.25 API version: {api_version!r}")
    source_path = Path(source).resolve()
    output_path = Path(output)
    checkpoint = (
        Path(checkpoint_path)
        if checkpoint_path is not None
        else output_path.with_suffix(output_path.suffix + ".checkpoint.json")
    )
    baseline_path = (
        Path(external_baseline_path).resolve()
        if external_baseline_path is not None
        else None
    )
    if max_windows is not None and (
        isinstance(max_windows, bool)
        or not isinstance(max_windows, int)
        or max_windows < 1
    ):
        raise ValueError("max_windows must be a positive integer or None")
    spans = ci16le_window_spans(
        source_path,
        config=config.plugin,
        start_sample=config.segment_start_sample,
        sample_count=config.segment_sample_count,
    )
    source_size_bytes = source_path.stat().st_size
    if (
        config.expected_input_size_bytes is not None
        and source_size_bytes != config.expected_input_size_bytes
    ):
        raise ValueError("IQ size does not match expected_input_size_bytes")
    source_sha256 = sha256_file(source_path)
    if (
        config.expected_input_sha256 is not None
        and source_sha256 != config.expected_input_sha256
    ):
        raise ValueError("IQ SHA-256 does not match expected_input_sha256")
    baseline_sha256, baseline = _load_external_baseline(
        baseline_path,
        capture_sha256=source_sha256,
        event_key=config.event_key,
    )
    if len(baseline) > config.plugin.max_candidate_records:
        raise ValueError("external baseline exceeds max_candidate_records")
    attempt_fingerprint = _sha256_document({
        "source_sha256": source_sha256,
        "source_size_bytes": source_size_bytes,
        "config_sha256": config.fingerprint,
        "external_baseline_sha256": baseline_sha256,
        "plugin_id": PLUGIN_ID,
        "plugin_version": PLUGIN_VERSION,
        "api_version": API_VERSION,
    })
    expected_checkpoint = _empty_checkpoint(
        attempt_fingerprint=attempt_fingerprint,
        source_sha256=source_sha256,
        source_size_bytes=source_size_bytes,
        config=config,
        baseline_sha256=baseline_sha256,
    )
    state = _load_checkpoint(checkpoint, expected=expected_checkpoint, resume=resume)
    raw_windows = state["windows"]
    assert isinstance(raw_windows, dict)
    _validate_checkpoint_windows(raw_windows, spans)
    try:
        completed = {int(key) for key in raw_windows}
    except (TypeError, ValueError) as error:
        raise ValueError("FSK checkpoint contains a non-integer window key") from error
    if not completed <= {start for start, _ in spans}:
        raise ValueError("FSK checkpoint contains an unknown window boundary")
    _atomic_json(checkpoint, state)

    processed = 0
    for start, stop, window in iter_fsk_ax25_ci16le_windows(
        source_path,
        config=config.plugin,
        start_sample=config.segment_start_sample,
        sample_count=config.segment_sample_count,
        skip_start_samples=tuple(sorted(completed)),
    ):
        result = decode_fsk_ax25_iq(
            window, config=config.plugin, window_start_sample=start
        )
        window_document = _window_document(start, stop, result)
        existing_candidates = sum(
            len(value["trusted"]) + len(value["rejected"])
            for value in raw_windows.values()
        )
        new_candidates = len(window_document["trusted"]) + len(
            window_document["rejected"]
        )
        if existing_candidates + new_candidates > config.plugin.max_candidate_records:
            raise ValueError("FSK campaign candidate record bound exceeded")
        raw_windows[str(start)] = window_document
        _atomic_json(checkpoint, state)
        processed += 1
        if max_windows is not None and processed >= max_windows:
            break

    report = _build_result(
        source_path=source_path,
        source_sha256=source_sha256,
        source_size_bytes=source_size_bytes,
        config=config,
        spans=spans,
        checkpoint=state,
        baseline_path=baseline_path,
        baseline_sha256=baseline_sha256,
        baseline=baseline,
    )
    _atomic_json(output_path, report)
    return report
