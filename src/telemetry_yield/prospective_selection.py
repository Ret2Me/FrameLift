"""Outcome-blind metadata projection and public-random cohort selection."""

from __future__ import annotations

from collections import Counter
import re
from typing import Mapping, Sequence

from .public_random_selection import rank_integer


OBJECT_PATTERN = re.compile(r"^observation_(?P<id>[1-9][0-9]*)\.iq$")
PERMITTED_CATALOGUE_FIELDS = (
    "observation_id",
    "satellite_id",
    "mode",
    "frequency_hz",
    "start",
    "end",
)


def _positive_integer(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _nonnegative_integer(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a nonnegative integer")
    return value


def _nonempty_text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be non-empty text")
    return value


def build_eligible_pool(
    catalogue: Mapping[str, object],
    download_plan: Mapping[str, object],
    *,
    eligible_modes: Sequence[str],
    development_exclusions: Sequence[int],
) -> tuple[dict[str, object], ...]:
    """Project only permitted metadata; never retain decoder outcome fields."""

    raw_candidates = download_plan.get("candidates")
    raw_observations = catalogue.get("observations")
    if not isinstance(raw_candidates, list) or not isinstance(raw_observations, list):
        raise ValueError("inputs must contain candidate and observation arrays")
    mode_set = frozenset(_nonempty_text(mode, "eligible mode") for mode in eligible_modes)
    exclusion_set = frozenset(
        _positive_integer(value, "development exclusion")
        for value in development_exclusions
    )

    objects: dict[int, dict[str, object]] = {}
    for raw in raw_candidates:
        if not isinstance(raw, Mapping):
            raise ValueError("candidate must be an object")
        observation_id = _positive_integer(raw.get("observation_id"), "observation_id")
        key = _nonempty_text(raw.get("key"), "key")
        match = OBJECT_PATTERN.fullmatch(key)
        if match is None or int(match.group("id")) != observation_id:
            raise ValueError(f"object key/ID mismatch: {key}")
        size_bytes = _nonnegative_integer(raw.get("size_bytes"), "size_bytes")
        if size_bytes == 0:
            continue
        if size_bytes % 4:
            raise ValueError("CI16 object size must be a multiple of four")
        if observation_id in objects:
            raise ValueError(f"duplicate candidate ID {observation_id}")
        objects[observation_id] = {
            "object_key": key,
            "size_bytes": size_bytes,
            "url": _nonempty_text(raw.get("url"), "url"),
        }

    projected: dict[int, dict[str, object]] = {}
    for raw in raw_observations:
        if not isinstance(raw, Mapping):
            raise ValueError("catalogue observation must be an object")
        observation_id = raw.get("observation_id")
        if observation_id not in objects:
            continue
        if observation_id in projected:
            raise ValueError(f"duplicate catalogue observation {observation_id}")
        projected[int(observation_id)] = {
            field: raw.get(field) for field in PERMITTED_CATALOGUE_FIELDS
        }

    pool: list[dict[str, object]] = []
    for observation_id, metadata in projected.items():
        if observation_id in exclusion_set:
            continue
        mode = _nonempty_text(metadata.get("mode"), "mode")
        if mode not in mode_set:
            continue
        satellite_id = _nonempty_text(metadata.get("satellite_id"), "satellite_id")
        frequency_hz = metadata.get("frequency_hz")
        if (
            isinstance(frequency_hz, bool)
            or not isinstance(frequency_hz, (int, float))
            or frequency_hz <= 0
        ):
            raise ValueError("frequency_hz must be positive")
        pool.append(
            {
                **metadata,
                **objects[observation_id],
                "satellite_id": satellite_id,
            }
        )
    pool.sort(key=lambda row: int(row["observation_id"]))
    return tuple(pool)


def select_public_random_cohort(
    pool: Sequence[Mapping[str, object]],
    *,
    selection_salt: bytes,
    target_observations: int,
    maximum_per_satellite: int,
    minimum_satellites: int,
) -> tuple[dict[str, object], ...]:
    """Rank a frozen pool and apply only preregistered diversity bounds."""

    for name, value in {
        "target_observations": target_observations,
        "maximum_per_satellite": maximum_per_satellite,
        "minimum_satellites": minimum_satellites,
    }.items():
        _positive_integer(value, name)
    ranked = []
    seen: set[int] = set()
    for raw in pool:
        observation_id = _positive_integer(raw.get("observation_id"), "observation_id")
        if observation_id in seen:
            raise ValueError("eligible pool contains duplicate observation IDs")
        seen.add(observation_id)
        ranked.append(
            {
                **raw,
                "selection_rank_sha256": rank_integer(selection_salt, observation_id),
            }
        )
    ranked.sort(
        key=lambda row: (str(row["selection_rank_sha256"]), int(row["observation_id"]))
    )
    counts: Counter[str] = Counter()
    selected: list[dict[str, object]] = []
    for row in ranked:
        satellite_id = _nonempty_text(row.get("satellite_id"), "satellite_id")
        if counts[satellite_id] >= maximum_per_satellite:
            continue
        selected.append(row)
        counts[satellite_id] += 1
        if len(selected) == target_observations:
            break
    if len(selected) != target_observations:
        raise ValueError("insufficient eligible observations after diversity cap")
    if len(counts) < minimum_satellites:
        raise ValueError("selected cohort has too few satellite identities")
    return tuple(selected)
