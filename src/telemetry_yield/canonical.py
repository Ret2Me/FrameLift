"""Canonical JSON and stable fingerprints for decoder configurations."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from typing import Any

from telemetry_yield.models import EffectiveConfig


def _json_value(value: Any, *, path: str = "$") -> Any:
    """Return a JSON-compatible copy or fail with a useful location."""

    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"non-finite float at {path}")
        return value
    if isinstance(value, Mapping):
        normalized: dict[str, Any] = {}
        for key, nested in value.items():
            if not isinstance(key, str):
                raise TypeError(f"JSON object key at {path} must be a string")
            normalized[key] = _json_value(nested, path=f"{path}.{key}")
        return normalized
    if isinstance(value, Sequence) and not isinstance(
        value, (str, bytes, bytearray, memoryview)
    ):
        return [
            _json_value(nested, path=f"{path}[{index}]")
            for index, nested in enumerate(value)
        ]
    raise TypeError(f"unsupported value at {path}: {type(value).__name__}")


def canonical_json(value: Any) -> str:
    """Serialize a JSON-compatible value deterministically as UTF-8 text."""

    return json.dumps(
        _json_value(value),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def config_hash(
    effective_config: EffectiveConfig | None = None,
    *,
    protocol_id: str | None = None,
    decoder: str | None = None,
    decoder_version: str | None = None,
    seed: int | None = None,
    config: Mapping[str, Any] | None = None,
) -> str:
    """Hash every input that can change a decoding attempt.

    Callers may pass an :class:`EffectiveConfig` or all five named fields. A
    mixed call is rejected so there is one unambiguous fingerprint contract.
    """

    named_values = (protocol_id, decoder, decoder_version, seed, config)
    if effective_config is not None:
        if any(value is not None for value in named_values):
            raise TypeError("pass EffectiveConfig or named fields, not both")
        value = effective_config
    else:
        if any(value is None for value in named_values):
            raise TypeError(
                "protocol_id, decoder, decoder_version, seed, and config are required"
            )
        value = EffectiveConfig(
            protocol_id=protocol_id,
            decoder=decoder,
            decoder_version=decoder_version,
            seed=seed,
            config=config,
        )

    document = {
        "protocol_id": value.protocol_id,
        "decoder": value.decoder,
        "decoder_version": value.decoder_version,
        "seed": value.seed,
        "config": value.config,
    }
    return hashlib.sha256(canonical_json(document).encode("utf-8")).hexdigest()

