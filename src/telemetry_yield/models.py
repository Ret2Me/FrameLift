"""Small, dependency-free domain models shared by the project."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, TypeAlias

JsonScalar: TypeAlias = None | bool | int | float | str
JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]


def _require_non_empty(name: str, value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")


@dataclass(frozen=True, slots=True)
class EffectiveConfig:
    """All inputs that may affect a decoder attempt.

    The corresponding fingerprint must include every field. Keeping decoder
    identity and version in this model prevents cached attempts from being
    silently reused after an implementation change.
    """

    protocol_id: str
    decoder: str
    decoder_version: str
    seed: int
    config: Mapping[str, JsonValue]

    def __post_init__(self) -> None:
        _require_non_empty("protocol_id", self.protocol_id)
        _require_non_empty("decoder", self.decoder)
        _require_non_empty("decoder_version", self.decoder_version)
        if isinstance(self.seed, bool) or not isinstance(self.seed, int):
            raise TypeError("seed must be an integer")
        if not isinstance(self.config, Mapping):
            raise TypeError("config must be a mapping")


@dataclass(frozen=True, slots=True)
class FrameRecord:
    """A raw decoder frame used by yield metrics."""

    transmission_event_id: str
    payload_sha256: str
    crc_valid: bool

    def __post_init__(self) -> None:
        _require_non_empty("transmission_event_id", self.transmission_event_id)
        _require_non_empty("payload_sha256", self.payload_sha256)
        if not isinstance(self.crc_valid, bool):
            raise TypeError("crc_valid must be a boolean")


@dataclass(frozen=True, slots=True)
class LicenseMetadata:
    """Publication-relevant licence state for one input source."""

    source_id: str
    license_spdx: str
    license_verified: bool
    publication_reviewed: bool = False

    def __post_init__(self) -> None:
        _require_non_empty("source_id", self.source_id)
        _require_non_empty("license_spdx", self.license_spdx)
        if not isinstance(self.license_verified, bool):
            raise TypeError("license_verified must be a boolean")
        if not isinstance(self.publication_reviewed, bool):
            raise TypeError("publication_reviewed must be a boolean")
