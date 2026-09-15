"""Deterministic union, deduplication, and provenance for decoded candidates.

The ledger is deliberately independent of any receiver implementation.  A
baseline is merely an origin role; it does not give a candidate privileged
validation semantics and does not couple this module to SatNOGS.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import math
from types import MappingProxyType
from typing import Iterable, Literal, Mapping, TypeAlias

from .canonical import canonical_json
from .models import JsonScalar, JsonValue


SourceRole: TypeAlias = Literal["baseline", "candidate"]
LedgerKey: TypeAlias = tuple[str, str, str, str]
FrozenJson: TypeAlias = (
    JsonScalar | tuple["FrozenJson", ...] | Mapping[str, "FrozenJson"]
)


def _non_empty(name: str, value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value


def _sha256(name: str, value: str) -> str:
    _non_empty(name, value)
    normalized = value.casefold()
    if len(normalized) != 64 or any(
        character not in "0123456789abcdef" for character in normalized
    ):
        raise ValueError(f"{name} must be a 64-character SHA-256 hex digest")
    return normalized


def _bytes(name: str, value: bytes) -> bytes:
    if not isinstance(value, bytes) or not value:
        raise ValueError(f"{name} must be non-empty bytes")
    return value


def _freeze_json(value: JsonValue, *, path: str = "$") -> FrozenJson:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"non-finite float at {path}")
        return value
    if isinstance(value, list):
        return tuple(
            _freeze_json(nested, path=f"{path}[{index}]")
            for index, nested in enumerate(value)
        )
    if isinstance(value, Mapping):
        frozen: dict[str, FrozenJson] = {}
        for key, nested in value.items():
            if not isinstance(key, str):
                raise TypeError(f"JSON object key at {path} must be a string")
            frozen[key] = _freeze_json(nested, path=f"{path}.{key}")
        return MappingProxyType(dict(sorted(frozen.items())))
    raise TypeError(f"unsupported provenance value at {path}: {type(value).__name__}")


def _thaw_json(value: FrozenJson) -> JsonValue:
    if isinstance(value, Mapping):
        return {key: _thaw_json(nested) for key, nested in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(nested) for nested in value]
    return value


def _digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


@dataclass(frozen=True, slots=True)
class DetectionRecord:
    """One decoder origin before cross-branch union.

    ``validated`` has no default on purpose.  Callers must make the acceptance
    decision explicit, and :func:`build_candidate_ledger` rejects every record
    for which it is not exactly ``True``.
    """

    capture_sha256: str
    segment_start_sample: int
    segment_sample_count: int
    branch_id: str
    source_role: SourceRole
    plugin_id: str
    plugin_version: str
    protocol_id: str
    original_frame: bytes
    normalized_payload: bytes
    validation_layers: tuple[str, ...]
    validated: bool
    hypothesis_fingerprint: str
    config_fingerprint: str
    event_key: str
    event_time_seconds: float
    provenance: Mapping[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "capture_sha256", _sha256("capture_sha256", self.capture_sha256)
        )
        if (
            isinstance(self.segment_start_sample, bool)
            or not isinstance(self.segment_start_sample, int)
            or self.segment_start_sample < 0
        ):
            raise ValueError("segment_start_sample must be a non-negative integer")
        if (
            isinstance(self.segment_sample_count, bool)
            or not isinstance(self.segment_sample_count, int)
            or self.segment_sample_count <= 0
        ):
            raise ValueError("segment_sample_count must be a positive integer")
        for name in (
            "branch_id",
            "plugin_id",
            "plugin_version",
            "protocol_id",
            "hypothesis_fingerprint",
            "config_fingerprint",
            "event_key",
        ):
            _non_empty(name, getattr(self, name))
        if self.source_role not in ("baseline", "candidate"):
            raise ValueError("source_role must be 'baseline' or 'candidate'")
        object.__setattr__(
            self, "original_frame", _bytes("original_frame", self.original_frame)
        )
        object.__setattr__(
            self,
            "normalized_payload",
            _bytes("normalized_payload", self.normalized_payload),
        )
        layers = tuple(self.validation_layers)
        if any(not isinstance(layer, str) or not layer.strip() for layer in layers):
            raise ValueError("validation_layers values must be non-empty strings")
        if len(set(layers)) != len(layers):
            raise ValueError("validation_layers values must be unique")
        if not isinstance(self.validated, bool):
            raise TypeError("validated must be a boolean")
        if self.validated and not layers:
            raise ValueError("a validated detection must name validation layers")
        if (
            isinstance(self.event_time_seconds, bool)
            or not isinstance(self.event_time_seconds, (int, float))
            or not math.isfinite(float(self.event_time_seconds))
            or self.event_time_seconds < 0
        ):
            raise ValueError("event_time_seconds must be finite and non-negative")
        if not isinstance(self.provenance, Mapping):
            raise TypeError("provenance must be a mapping")
        frozen_provenance = _freeze_json(dict(self.provenance), path="$.provenance")
        assert isinstance(frozen_provenance, Mapping)
        object.__setattr__(self, "validation_layers", layers)
        object.__setattr__(self, "event_time_seconds", float(self.event_time_seconds))
        object.__setattr__(self, "provenance", frozen_provenance)

    @property
    def normalized_payload_sha256(self) -> str:
        return _digest(self.normalized_payload)

    @property
    def original_frame_sha256(self) -> str:
        return _digest(self.original_frame)

    @property
    def ledger_key(self) -> LedgerKey:
        return (
            self.capture_sha256,
            self.event_key,
            self.protocol_id,
            self.normalized_payload_sha256,
        )

    def _origin_document(self) -> dict[str, JsonValue]:
        return {
            "capture_sha256": self.capture_sha256,
            "segment_start_sample": self.segment_start_sample,
            "segment_sample_count": self.segment_sample_count,
            "branch_id": self.branch_id,
            "source_role": self.source_role,
            "plugin_id": self.plugin_id,
            "plugin_version": self.plugin_version,
            "protocol_id": self.protocol_id,
            "original_frame_hex": self.original_frame.hex(),
            "original_frame_sha256": self.original_frame_sha256,
            "normalized_payload_hex": self.normalized_payload.hex(),
            "normalized_payload_sha256": self.normalized_payload_sha256,
            "validation_layers": list(self.validation_layers),
            "validated": self.validated,
            "hypothesis_fingerprint": self.hypothesis_fingerprint,
            "config_fingerprint": self.config_fingerprint,
            "event_key": self.event_key,
            "event_time_seconds": self.event_time_seconds,
            "provenance": _thaw_json(self.provenance),
        }

    @property
    def origin_sha256(self) -> str:
        document = canonical_json(self._origin_document()).encode("utf-8")
        return hashlib.sha256(document).hexdigest()

    def to_dict(self) -> dict[str, JsonValue]:
        return {"origin_sha256": self.origin_sha256, **self._origin_document()}


@dataclass(frozen=True, slots=True)
class UnionFrame:
    """One event-local normalized payload with every independent origin."""

    capture_sha256: str
    event_key: str
    protocol_id: str
    normalized_payload: bytes
    origins: tuple[DetectionRecord, ...]

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "capture_sha256", _sha256("capture_sha256", self.capture_sha256)
        )
        _non_empty("event_key", self.event_key)
        _non_empty("protocol_id", self.protocol_id)
        _bytes("normalized_payload", self.normalized_payload)
        origins = tuple(self.origins)
        if not origins:
            raise ValueError("origins must not be empty")
        expected_key = self.ledger_key
        if any(origin.ledger_key != expected_key for origin in origins):
            raise ValueError("every origin must match the union frame key")
        if any(not origin.validated or not origin.validation_layers for origin in origins):
            raise ValueError("every union origin must be explicitly validated")
        ordered = tuple(sorted(origins, key=lambda origin: origin.origin_sha256))
        if len({origin.origin_sha256 for origin in ordered}) != len(ordered):
            raise ValueError("UnionFrame origins must already be deduplicated")
        object.__setattr__(self, "origins", ordered)

    @property
    def normalized_payload_sha256(self) -> str:
        return _digest(self.normalized_payload)

    @property
    def ledger_key(self) -> LedgerKey:
        return (
            self.capture_sha256,
            self.event_key,
            self.protocol_id,
            self.normalized_payload_sha256,
        )

    @property
    def original_frame_sha256s(self) -> tuple[str, ...]:
        return tuple(sorted({origin.original_frame_sha256 for origin in self.origins}))

    @property
    def branch_ids(self) -> tuple[str, ...]:
        return tuple(sorted({origin.branch_id for origin in self.origins}))

    @property
    def has_baseline_origin(self) -> bool:
        return any(origin.source_role == "baseline" for origin in self.origins)

    @property
    def has_candidate_origin(self) -> bool:
        return any(origin.source_role == "candidate" for origin in self.origins)

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "key": {
                "capture_sha256": self.capture_sha256,
                "event_key": self.event_key,
                "protocol_id": self.protocol_id,
                "normalized_payload_sha256": self.normalized_payload_sha256,
            },
            "normalized_payload_hex": self.normalized_payload.hex(),
            "original_frame_sha256s": list(self.original_frame_sha256s),
            "branch_ids": list(self.branch_ids),
            "has_baseline_origin": self.has_baseline_origin,
            "has_candidate_origin": self.has_candidate_origin,
            "origins": [origin.to_dict() for origin in self.origins],
        }


@dataclass(frozen=True, slots=True)
class CandidateLedgerResult:
    """Validated union and non-inflating per-role/per-branch yield counts.

    Counts are unique union keys, not decoder hits. ``candidate_count`` counts
    keys seen by any non-baseline branch, including overlap with the baseline.
    ``incremental_over_baseline`` counts union keys absent from the baseline.
    """

    frames: tuple[UnionFrame, ...]
    baseline_count: int
    candidate_count: int
    branch_counts: Mapping[str, int]
    union_count: int
    incremental_over_baseline: int
    origin_count: int

    def __post_init__(self) -> None:
        frames = tuple(self.frames)
        ordered = tuple(sorted(frames, key=lambda frame: frame.ledger_key))
        if frames != ordered:
            raise ValueError("frames must be deterministically sorted")
        if len({frame.ledger_key for frame in frames}) != len(frames):
            raise ValueError("frames must have unique ledger keys")
        counts = dict(sorted(self.branch_counts.items()))
        if any(
            not isinstance(branch, str)
            or not branch.strip()
            or isinstance(count, bool)
            or not isinstance(count, int)
            or count < 0
            for branch, count in counts.items()
        ):
            raise ValueError("branch_counts must map branch IDs to non-negative integers")
        for name in (
            "baseline_count",
            "candidate_count",
            "union_count",
            "incremental_over_baseline",
            "origin_count",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        if self.union_count != len(frames):
            raise ValueError("union_count must equal the number of frames")
        if self.incremental_over_baseline != self.union_count - self.baseline_count:
            raise ValueError("incremental_over_baseline is inconsistent")
        expected_baseline = sum(frame.has_baseline_origin for frame in frames)
        expected_candidate = sum(frame.has_candidate_origin for frame in frames)
        expected_origins = sum(len(frame.origins) for frame in frames)
        expected_branch_counts: dict[str, int] = {}
        for frame in frames:
            for branch_id in frame.branch_ids:
                expected_branch_counts[branch_id] = (
                    expected_branch_counts.get(branch_id, 0) + 1
                )
        if self.baseline_count != expected_baseline:
            raise ValueError("baseline_count is inconsistent with frame origins")
        if self.candidate_count != expected_candidate:
            raise ValueError("candidate_count is inconsistent with frame origins")
        if self.origin_count != expected_origins:
            raise ValueError("origin_count is inconsistent with frame origins")
        if counts != dict(sorted(expected_branch_counts.items())):
            raise ValueError("branch_counts are inconsistent with frame origins")
        object.__setattr__(self, "frames", frames)
        object.__setattr__(self, "branch_counts", MappingProxyType(counts))

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "schema_version": 1,
            "baseline_count": self.baseline_count,
            "candidate_count": self.candidate_count,
            "branch_counts": dict(self.branch_counts),
            "union_count": self.union_count,
            "incremental_over_baseline": self.incremental_over_baseline,
            "origin_count": self.origin_count,
            "frames": [frame.to_dict() for frame in self.frames],
        }


def build_candidate_ledger(
    detections: Iterable[DetectionRecord],
) -> CandidateLedgerResult:
    """Union validated detections without inflating repeated decoder origins."""

    unique_origins: dict[str, DetectionRecord] = {}
    for detection in detections:
        if not isinstance(detection, DetectionRecord):
            raise TypeError("detections must contain DetectionRecord objects")
        if detection.validated is not True or not detection.validation_layers:
            raise ValueError(
                "candidate ledger accepts only explicitly validated detections "
                "with non-empty validation layers"
            )
        existing = unique_origins.get(detection.origin_sha256)
        if existing is not None and existing != detection:
            raise ValueError("origin fingerprint collision")
        unique_origins.setdefault(detection.origin_sha256, detection)

    grouped: dict[LedgerKey, list[DetectionRecord]] = {}
    payloads: dict[LedgerKey, bytes] = {}
    for detection in unique_origins.values():
        key = detection.ledger_key
        previous_payload = payloads.setdefault(key, detection.normalized_payload)
        if previous_payload != detection.normalized_payload:
            raise ValueError("normalized payload SHA-256 collision")
        grouped.setdefault(key, []).append(detection)

    frames = tuple(
        UnionFrame(
            capture_sha256=key[0],
            event_key=key[1],
            protocol_id=key[2],
            normalized_payload=payloads[key],
            origins=tuple(origins),
        )
        for key, origins in sorted(grouped.items())
    )

    baseline_keys = {
        frame.ledger_key for frame in frames if frame.has_baseline_origin
    }
    candidate_keys = {
        frame.ledger_key for frame in frames if frame.has_candidate_origin
    }
    branch_keys: dict[str, set[LedgerKey]] = {}
    for frame in frames:
        for branch_id in frame.branch_ids:
            branch_keys.setdefault(branch_id, set()).add(frame.ledger_key)
    branch_counts = {
        branch_id: len(keys) for branch_id, keys in sorted(branch_keys.items())
    }

    return CandidateLedgerResult(
        frames=frames,
        baseline_count=len(baseline_keys),
        candidate_count=len(candidate_keys),
        branch_counts=branch_counts,
        union_count=len(frames),
        incremental_over_baseline=len(frames) - len(baseline_keys),
        origin_count=len(unique_origins),
    )
