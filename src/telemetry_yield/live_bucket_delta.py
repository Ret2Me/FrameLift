"""Turn a live bucket inventory delta into verified download candidates."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from urllib.parse import quote, urlsplit, urlunsplit
from typing import Any

from .archive_download import DownloadCandidate


def _object_url(bucket_url: str, key: str) -> str:
    parsed = urlsplit(bucket_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("inventory bucket_url must be an absolute HTTP(S) URL")
    if parsed.query or parsed.fragment:
        raise ValueError("inventory bucket_url must not contain a query or fragment")
    path = parsed.path.rstrip("/") + "/" + quote(key, safe="")
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def select_live_bucket_delta(inventory: Mapping[str, Any]) -> tuple[DownloadCandidate, ...]:
    """Select only positive-size live IQ objects absent from the catalogue.

    The selection is cross-checked against both the report comparison and its
    summary so a stale or hand-edited report fails closed before any download.
    """

    source = inventory.get("source")
    if not isinstance(source, Mapping):
        raise ValueError("inventory source must be an object")
    if source.get("payload_objects_downloaded") is not False:
        raise ValueError("inventory is not marked as metadata-only")
    bucket_url = source.get("bucket_url")
    if not isinstance(bucket_url, str) or not bucket_url.strip():
        raise ValueError("inventory source.bucket_url must be a non-empty string")

    raw_objects = inventory.get("objects")
    if not isinstance(raw_objects, Sequence) or isinstance(
        raw_objects, (str, bytes, bytearray, memoryview)
    ):
        raise ValueError("inventory objects must be an array")

    candidates: list[DownloadCandidate] = []
    seen: set[int] = set()
    for raw in raw_objects:
        if not isinstance(raw, Mapping):
            raise ValueError("inventory object must be an object")
        if raw.get("catalogue_present") is not False:
            continue
        observation_id = raw.get("observation_id")
        if not isinstance(observation_id, int) or isinstance(observation_id, bool):
            raise ValueError("delta observation_id must be an integer")
        key = raw.get("key")
        expected_key = f"observation_{observation_id}.iq"
        if key != expected_key:
            raise ValueError(
                f"delta key {key!r} does not match observation_id {observation_id}"
            )
        size = raw.get("size_bytes")
        if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
            raise ValueError(f"delta object {key!r} must have a positive live size")
        if observation_id in seen:
            raise ValueError("delta observation_id values must be unique")
        seen.add(observation_id)
        candidates.append(
            DownloadCandidate(
                observation_id=observation_id,
                url=_object_url(bucket_url, key),
                expected_size_bytes=size,
            )
        )

    candidates.sort(key=lambda item: item.observation_id)
    selected_ids = [item.observation_id for item in candidates]
    comparison = inventory.get("comparison")
    if not isinstance(comparison, Mapping):
        raise ValueError("inventory comparison must be an object")
    declared_ids = comparison.get("new_in_bucket_observation_ids")
    if declared_ids != selected_ids:
        raise ValueError("inventory delta objects disagree with comparison IDs")
    summary = inventory.get("summary")
    if not isinstance(summary, Mapping):
        raise ValueError("inventory summary must be an object")
    if summary.get("new_in_bucket_count") != len(candidates):
        raise ValueError("inventory delta objects disagree with summary count")
    return tuple(candidates)
