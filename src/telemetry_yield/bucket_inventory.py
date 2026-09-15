"""Read-only inventory of public S3 ListObjects v1 buckets.

The scanner deliberately requests only bucket-listing XML.  It never opens an
object URL, which makes it suitable for discovering large IQ recordings
without downloading their payloads.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET


SCHEMA_VERSION = "polyitan-live-bucket-inventory-v1"
OBJECT_PATTERN = re.compile(r"^observation_([0-9]+)\.iq$")
DEFAULT_MAX_XML_BYTES = 16 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class S3Object:
    key: str
    size: int
    etag: str | None = None
    last_modified: str | None = None


@dataclass(frozen=True, slots=True)
class ListObjectsV1Page:
    objects: tuple[S3Object, ...]
    is_truncated: bool
    next_marker: str | None
    marker: str | None
    prefix: str | None


@dataclass(frozen=True, slots=True)
class ListingResult:
    objects: tuple[S3Object, ...]
    page_count: int
    object_occurrence_count: int
    anomalies: tuple[dict[str, object], ...]


Fetch = Callable[[str, float], bytes]


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _children(element: ET.Element, name: str) -> list[ET.Element]:
    return [child for child in element if _local_name(child.tag) == name]


def _optional_text(element: ET.Element, name: str) -> str | None:
    matches = _children(element, name)
    if len(matches) > 1:
        raise ValueError(f"ListObjects response contains duplicate {name} fields")
    if not matches:
        return None
    return matches[0].text or ""


def _required_text(element: ET.Element, name: str) -> str:
    value = _optional_text(element, name)
    if value is None:
        raise ValueError(f"ListObjects response is missing {name}")
    return value


def parse_list_objects_v1(xml_bytes: bytes) -> ListObjectsV1Page:
    """Parse one S3 ListObjects v1 response, including namespaced XML."""

    if len(xml_bytes) > DEFAULT_MAX_XML_BYTES:
        raise ValueError("ListObjects response exceeds the XML size limit")
    if b"<!DOCTYPE" in xml_bytes.upper():
        raise ValueError("DOCTYPE is not allowed in ListObjects responses")
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as exc:
        raise ValueError("invalid ListObjects XML") from exc
    if _local_name(root.tag) != "ListBucketResult":
        raise ValueError("XML root is not ListBucketResult")

    truncated_text = _required_text(root, "IsTruncated").strip().lower()
    if truncated_text == "true":
        is_truncated = True
    elif truncated_text == "false":
        is_truncated = False
    else:
        raise ValueError("IsTruncated must be true or false")

    objects: list[S3Object] = []
    for contents in _children(root, "Contents"):
        key = _required_text(contents, "Key")
        if not key:
            raise ValueError("ListObjects returned an empty object key")
        size_text = _required_text(contents, "Size").strip()
        try:
            size = int(size_text, 10)
        except ValueError as exc:
            raise ValueError(f"invalid Size for object {key!r}") from exc
        if size < 0:
            raise ValueError(f"negative Size for object {key!r}")
        etag = _optional_text(contents, "ETag")
        if etag is not None and len(etag) >= 2 and etag[0] == etag[-1] == '"':
            etag = etag[1:-1]
        objects.append(
            S3Object(
                key=key,
                size=size,
                etag=etag,
                last_modified=_optional_text(contents, "LastModified"),
            )
        )

    return ListObjectsV1Page(
        objects=tuple(objects),
        is_truncated=is_truncated,
        next_marker=_optional_text(root, "NextMarker"),
        marker=_optional_text(root, "Marker"),
        prefix=_optional_text(root, "Prefix"),
    )


def _list_url(base_url: str, *, prefix: str, marker: str | None, max_keys: int) -> str:
    parsed = urlsplit(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("bucket URL must be an absolute HTTP(S) URL")
    if parsed.fragment:
        raise ValueError("bucket URL must not contain a fragment")
    query = parse_qsl(parsed.query, keep_blank_values=True)
    reserved = {"marker", "max-keys", "prefix", "delimiter", "list-type"}
    if any(key.lower() in reserved for key, _value in query):
        raise ValueError("bucket URL contains reserved ListObjects query parameters")
    query.extend((("prefix", prefix), ("max-keys", str(max_keys))))
    if marker is not None:
        query.append(("marker", marker))
    return urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path, urlencode(query), "")
    )


def _fetch_xml(url: str, timeout: float) -> bytes:
    request = Request(
        url,
        headers={"Accept": "application/xml", "User-Agent": "telemetry-yield-inventory/1"},
        method="GET",
    )
    with urlopen(request, timeout=timeout) as response:  # noqa: S310 - URL is caller supplied
        requested = urlsplit(url)
        final = urlsplit(response.geturl())
        if (final.scheme, final.netloc, final.path) != (
            requested.scheme,
            requested.netloc,
            requested.path,
        ):
            raise ValueError("ListObjects request redirected outside the bucket endpoint")
        payload = response.read(DEFAULT_MAX_XML_BYTES + 1)
    if len(payload) > DEFAULT_MAX_XML_BYTES:
        raise ValueError("ListObjects response exceeds the XML size limit")
    return payload


def list_objects_v1(
    base_url: str,
    *,
    prefix: str = "observation_",
    max_keys: int = 1000,
    timeout: float = 30.0,
    max_pages: int = 10_000,
    fetch: Fetch | None = None,
) -> ListingResult:
    """List object metadata with cycle detection and cross-page deduplication."""

    if not 1 <= max_keys <= 1000:
        raise ValueError("max_keys must be between 1 and 1000")
    if timeout <= 0:
        raise ValueError("timeout must be positive")
    if max_pages <= 0:
        raise ValueError("max_pages must be positive")
    fetch_xml = fetch or _fetch_xml
    marker: str | None = None
    used_markers: set[str] = set()
    objects_by_key: dict[str, S3Object] = {}
    anomalies: list[dict[str, object]] = []
    occurrences = 0

    for page_number in range(1, max_pages + 1):
        url = _list_url(base_url, prefix=prefix, marker=marker, max_keys=max_keys)
        page = parse_list_objects_v1(fetch_xml(url, timeout))
        if page.prefix is not None and page.prefix != prefix:
            raise ValueError("ListObjects response echoed a different prefix")
        if marker is not None and page.marker not in {None, marker}:
            raise ValueError("ListObjects response echoed a different marker")

        occurrences += len(page.objects)
        for item in page.objects:
            previous = objects_by_key.get(item.key)
            if previous is None:
                objects_by_key[item.key] = item
                continue
            kind = "duplicate_key" if previous == item else "duplicate_key_conflict"
            anomaly: dict[str, object] = {"kind": kind, "key": item.key}
            if previous != item:
                anomaly["first"] = {
                    "size": previous.size,
                    "etag": previous.etag,
                    "last_modified": previous.last_modified,
                }
                anomaly["duplicate"] = {
                    "size": item.size,
                    "etag": item.etag,
                    "last_modified": item.last_modified,
                }
            anomalies.append(anomaly)

        if not page.is_truncated:
            return ListingResult(
                objects=tuple(objects_by_key[key] for key in sorted(objects_by_key)),
                page_count=page_number,
                object_occurrence_count=occurrences,
                anomalies=tuple(anomalies),
            )

        next_marker = page.next_marker
        if not next_marker:
            next_marker = page.objects[-1].key if page.objects else None
        if not next_marker:
            raise ValueError("truncated ListObjects page has no usable next marker")
        if next_marker == marker or next_marker in used_markers:
            raise ValueError("ListObjects pagination marker did not advance")
        used_markers.add(next_marker)
        marker = next_marker

    raise ValueError("ListObjects pagination exceeded max_pages")


def _catalogue_iq(
    catalogue: Mapping[str, Any],
) -> tuple[dict[int, tuple[str, int]], list[dict[str, object]]]:
    observations = catalogue.get("observations")
    if not isinstance(observations, Sequence) or isinstance(
        observations, (str, bytes, bytearray)
    ):
        raise ValueError("catalogue observations must be an array")
    result: dict[int, tuple[str, int]] = {}
    anomalies: list[dict[str, object]] = []
    for raw in observations:
        if not isinstance(raw, Mapping):
            raise ValueError("catalogue observation must be an object")
        artifacts = raw.get("artifacts")
        iq = artifacts.get("iq") if isinstance(artifacts, Mapping) else None
        if iq is None:
            continue
        if not isinstance(iq, Mapping):
            raise ValueError("catalogue IQ artifact must be an object or null")
        key = iq.get("object_key")
        size = iq.get("size_bytes")
        match = OBJECT_PATTERN.fullmatch(key) if isinstance(key, str) else None
        if match is None:
            anomalies.append({"kind": "catalogue_nonmatching_iq_key", "key": key})
            continue
        if not isinstance(size, int) or isinstance(size, bool) or size < 0:
            anomalies.append(
                {"kind": "catalogue_invalid_size", "key": key, "size": size}
            )
            continue
        observation_id = int(match.group(1))
        if observation_id in result:
            anomalies.append(
                {"kind": "catalogue_duplicate_observation_id", "observation_id": observation_id}
            )
            continue
        result[observation_id] = (key, size)
        if size == 0:
            anomalies.append(
                {"kind": "catalogue_zero_size", "observation_id": observation_id, "key": key}
            )
    return result, anomalies


def build_bucket_inventory(
    listing: ListingResult,
    catalogue: Mapping[str, Any],
    *,
    bucket_url: str,
    catalogue_sha256: str | None = None,
    generated_at: str | None = None,
) -> dict[str, object]:
    """Compare exact ``observation_<id>.iq`` keys with a catalogue snapshot."""

    catalogue_iq, catalogue_anomalies = _catalogue_iq(catalogue)
    anomalies = [dict(item) for item in listing.anomalies]
    anomalies.extend(catalogue_anomalies)
    live_by_id: dict[int, S3Object] = {}
    exact_objects: list[tuple[int, S3Object]] = []

    for item in listing.objects:
        match = OBJECT_PATTERN.fullmatch(item.key)
        if match is None:
            anomalies.append({"kind": "nonmatching_listed_key", "key": item.key})
            continue
        observation_id = int(match.group(1))
        previous = live_by_id.get(observation_id)
        if previous is not None:
            anomalies.append(
                {
                    "kind": "duplicate_observation_id",
                    "observation_id": observation_id,
                    "keys": [previous.key, item.key],
                }
            )
        else:
            live_by_id[observation_id] = item
        exact_objects.append((observation_id, item))
        if item.size == 0:
            anomalies.append(
                {"kind": "zero_size", "observation_id": observation_id, "key": item.key}
            )

    live_ids = set(live_by_id)
    catalogue_ids = set(catalogue_iq)
    new_ids = sorted(live_ids - catalogue_ids)
    missing_ids = sorted(catalogue_ids - live_ids)
    size_mismatches = [
        {
            "observation_id": observation_id,
            "catalogue_size_bytes": catalogue_iq[observation_id][1],
            "live_size_bytes": live_by_id[observation_id].size,
        }
        for observation_id in sorted(live_ids & catalogue_ids)
        if catalogue_iq[observation_id][1] != live_by_id[observation_id].size
    ]

    records: list[dict[str, object]] = []
    for observation_id, item in sorted(exact_objects, key=lambda pair: (pair[0], pair[1].key)):
        catalogue_entry = catalogue_iq.get(observation_id)
        records.append(
            {
                "observation_id": observation_id,
                "key": item.key,
                "size_bytes": item.size,
                "etag": item.etag,
                "last_modified": item.last_modified,
                "catalogue_present": catalogue_entry is not None,
                "catalogue_size_bytes": catalogue_entry[1] if catalogue_entry else None,
                "size_matches_catalogue": (
                    item.size == catalogue_entry[1] if catalogue_entry else None
                ),
            }
        )

    source: dict[str, object] = {
        "bucket_url": bucket_url,
        "list_api": "S3 ListObjects v1",
        "list_prefix": "observation_",
        "payload_objects_downloaded": False,
    }
    if catalogue_sha256 is not None:
        source["catalogue_sha256"] = catalogue_sha256

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at
        or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "source": source,
        "summary": {
            "list_page_count": listing.page_count,
            "listed_object_occurrence_count": listing.object_occurrence_count,
            "listed_unique_key_count": len(listing.objects),
            "exact_iq_key_count": len(exact_objects),
            "exact_iq_observation_id_count": len(live_ids),
            "total_iq_size_bytes": sum(item.size for _id, item in exact_objects),
            "zero_size_count": sum(item.size == 0 for _id, item in exact_objects),
            "catalogue_iq_count": len(catalogue_iq),
            "new_in_bucket_count": len(new_ids),
            "missing_from_bucket_count": len(missing_ids),
            "size_mismatch_count": len(size_mismatches),
            "anomaly_count": len(anomalies),
        },
        "comparison": {
            "new_in_bucket_observation_ids": new_ids,
            "missing_from_bucket_observation_ids": missing_ids,
            "size_mismatches": size_mismatches,
        },
        "anomalies": anomalies,
        "objects": records,
    }


def write_live_bucket_inventory(
    bucket_url: str,
    catalogue_path: Path,
    output_path: Path,
    *,
    timeout: float = 30.0,
) -> dict[str, object]:
    """Run a metadata-only live scan and write its catalogue comparison."""

    catalogue_bytes = catalogue_path.read_bytes()
    parsed = json.loads(catalogue_bytes)
    if not isinstance(parsed, Mapping):
        raise ValueError("catalogue root must be an object")
    listing = list_objects_v1(bucket_url, timeout=timeout)
    report = build_bucket_inventory(
        listing,
        parsed,
        bucket_url=bucket_url,
        catalogue_sha256=hashlib.sha256(catalogue_bytes).hexdigest(),
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_name(output_path.name + ".tmp")
    temporary.write_text(
        json.dumps(report, ensure_ascii=False, allow_nan=False, sort_keys=True, indent=2)
        + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, output_path)
    return report


def compare_bucket_inventories(
    previous: Mapping[str, Any], current: Mapping[str, Any]
) -> dict[str, object]:
    """Compare two validated inventory snapshots by exact key and live size."""

    def records(document: Mapping[str, Any], name: str) -> dict[str, tuple[int, int]]:
        if document.get("schema_version") != SCHEMA_VERSION:
            raise ValueError(f"{name} inventory schema_version is invalid")
        raw_objects = document.get("objects")
        if not isinstance(raw_objects, Sequence) or isinstance(
            raw_objects, (str, bytes, bytearray, memoryview)
        ):
            raise ValueError(f"{name} inventory objects must be an array")
        output: dict[str, tuple[int, int]] = {}
        seen_ids: set[int] = set()
        for raw in raw_objects:
            if not isinstance(raw, Mapping):
                raise ValueError(f"{name} inventory object must be an object")
            key = raw.get("key")
            match = OBJECT_PATTERN.fullmatch(key) if isinstance(key, str) else None
            observation_id = raw.get("observation_id")
            size = raw.get("size_bytes")
            if (
                match is None
                or not isinstance(observation_id, int)
                or isinstance(observation_id, bool)
                or observation_id != int(match.group(1))
                or not isinstance(size, int)
                or isinstance(size, bool)
                or size < 0
            ):
                raise ValueError(f"{name} inventory contains an invalid IQ object")
            if key in output or observation_id in seen_ids:
                raise ValueError(f"{name} inventory contains duplicate IQ objects")
            output[key] = (observation_id, size)
            seen_ids.add(observation_id)
        summary = document.get("summary")
        if not isinstance(summary, Mapping) or summary.get("exact_iq_key_count") != len(output):
            raise ValueError(f"{name} inventory object count disagrees with summary")
        return output

    old = records(previous, "previous")
    new = records(current, "current")
    added_keys = sorted(set(new) - set(old), key=lambda key: new[key][0])
    removed_keys = sorted(set(old) - set(new), key=lambda key: old[key][0])
    changed_keys = sorted(
        (key for key in old.keys() & new.keys() if old[key][1] != new[key][1]),
        key=lambda key: new[key][0],
    )
    return {
        "previous_object_count": len(old),
        "current_object_count": len(new),
        "previous_highest_observation_id": max((item[0] for item in old.values()), default=None),
        "current_highest_observation_id": max((item[0] for item in new.values()), default=None),
        "added": [
            {"observation_id": new[key][0], "key": key, "size_bytes": new[key][1]}
            for key in added_keys
        ],
        "removed": [
            {"observation_id": old[key][0], "key": key, "size_bytes": old[key][1]}
            for key in removed_keys
        ],
        "size_changes": [
            {
                "observation_id": new[key][0],
                "key": key,
                "previous_size_bytes": old[key][1],
                "current_size_bytes": new[key][1],
            }
            for key in changed_keys
        ],
    }
