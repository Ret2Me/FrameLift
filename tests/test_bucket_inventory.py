from __future__ import annotations

from urllib.parse import parse_qs, urlsplit
import unittest

from telemetry_yield.bucket_inventory import (
    ListingResult,
    S3Object,
    build_bucket_inventory,
    compare_bucket_inventories,
    list_objects_v1,
    parse_list_objects_v1,
)


def page(
    objects: list[tuple[str, int]],
    *,
    truncated: bool,
    marker: str = "",
    next_marker: str | None = None,
    namespace: bool = True,
) -> bytes:
    xmlns = ' xmlns="http://s3.amazonaws.com/doc/2006-03-01/"' if namespace else ""
    next_xml = f"<NextMarker>{next_marker}</NextMarker>" if next_marker is not None else ""
    contents = "".join(
        f"<Contents><Key>{key}</Key><LastModified>2026-01-01T00:00:00Z</LastModified>"
        f"<ETag>&quot;etag-{size}&quot;</ETag><Size>{size}</Size></Contents>"
        for key, size in objects
    )
    return (
        f"<ListBucketResult{xmlns}><Prefix>observation_</Prefix><Marker>{marker}</Marker>"
        f"{next_xml}<IsTruncated>{str(truncated).lower()}</IsTruncated>{contents}"
        "</ListBucketResult>"
    ).encode()


def catalogue(items: list[tuple[int, int]]) -> dict[str, object]:
    return {
        "observations": [
            {
                "observation_id": observation_id,
                "artifacts": {
                    "iq": {
                        "object_key": f"observation_{observation_id}.iq",
                        "size_bytes": size,
                    }
                },
            }
            for observation_id, size in items
        ]
    }


class BucketInventoryTests(unittest.TestCase):
    def test_parses_namespaced_and_plain_listobjects_xml(self) -> None:
        for namespace in (True, False):
            parsed = parse_list_objects_v1(
                page(
                    [("observation_7.iq", 123)],
                    truncated=True,
                    next_marker="opaque marker",
                    namespace=namespace,
                )
            )
            self.assertTrue(parsed.is_truncated)
            self.assertEqual(parsed.next_marker, "opaque marker")
            self.assertEqual(parsed.objects[0].etag, "etag-123")
            self.assertEqual(parsed.objects[0].size, 123)

    def test_paginates_with_exact_next_marker_and_deduplicates(self) -> None:
        requested_markers: list[str | None] = []

        def fetch(url: str, timeout: float) -> bytes:
            self.assertEqual(timeout, 4.0)
            query = parse_qs(urlsplit(url).query)
            self.assertEqual(query["prefix"], ["observation_"])
            self.assertEqual(query["max-keys"], ["2"])
            marker = query.get("marker", [None])[0]
            requested_markers.append(marker)
            if marker is None:
                return page(
                    [("observation_1.iq", 10), ("observation_2.iq", 20)],
                    truncated=True,
                    next_marker="opaque [marker:1]",
                )
            return page(
                [("observation_2.iq", 20), ("observation_3.iq", 30)],
                truncated=False,
                marker="opaque [marker:1]",
            )

        result = list_objects_v1(
            "https://example.test/bucket/",
            max_keys=2,
            timeout=4.0,
            fetch=fetch,
        )
        self.assertEqual(requested_markers, [None, "opaque [marker:1]"])
        self.assertEqual([item.key for item in result.objects], [
            "observation_1.iq",
            "observation_2.iq",
            "observation_3.iq",
        ])
        self.assertEqual(result.object_occurrence_count, 4)
        self.assertEqual(result.anomalies, ({"kind": "duplicate_key", "key": "observation_2.iq"},))

    def test_missing_next_marker_falls_back_to_last_key(self) -> None:
        calls = 0

        def fetch(url: str, _timeout: float) -> bytes:
            nonlocal calls
            calls += 1
            marker = parse_qs(urlsplit(url).query).get("marker", [None])[0]
            if calls == 1:
                return page([("observation_5.iq", 5)], truncated=True)
            self.assertEqual(marker, "observation_5.iq")
            return page([], truncated=False, marker="observation_5.iq")

        result = list_objects_v1("https://example.test/bucket/", fetch=fetch)
        self.assertEqual(result.page_count, 2)

    def test_repeated_pagination_marker_fails_closed(self) -> None:
        def fetch(url: str, _timeout: float) -> bytes:
            marker = parse_qs(urlsplit(url).query).get("marker", [""])[0]
            return page([], truncated=True, marker=marker, next_marker="same")

        with self.assertRaisesRegex(ValueError, "did not advance"):
            list_objects_v1("https://example.test/bucket/", fetch=fetch)

    def test_comparison_reports_new_missing_size_zero_and_malformed_key(self) -> None:
        listing = ListingResult(
            objects=(
                S3Object("observation_1.iq", 10, "a"),
                S3Object("observation_2.iq", 0, "b"),
                S3Object("observation_4.iq", 41, "c"),
                S3Object("observation_bad.iq", 12, "d"),
            ),
            page_count=1,
            object_occurrence_count=4,
            anomalies=(),
        )
        report = build_bucket_inventory(
            listing,
            catalogue([(1, 10), (3, 30), (4, 40)]),
            bucket_url="https://example.test/bucket/",
            generated_at="2026-01-01T00:00:00Z",
        )
        self.assertEqual(report["comparison"]["new_in_bucket_observation_ids"], [2])
        self.assertEqual(report["comparison"]["missing_from_bucket_observation_ids"], [3])
        self.assertEqual(
            report["comparison"]["size_mismatches"],
            [{"observation_id": 4, "catalogue_size_bytes": 40, "live_size_bytes": 41}],
        )
        kinds = {item["kind"] for item in report["anomalies"]}
        self.assertEqual(kinds, {"zero_size", "nonmatching_listed_key"})
        self.assertEqual(report["summary"]["exact_iq_key_count"], 3)

    def test_duplicate_numeric_ids_and_conflicting_key_metadata_are_anomalies(self) -> None:
        listing = ListingResult(
            objects=(S3Object("observation_01.iq", 1), S3Object("observation_1.iq", 2)),
            page_count=1,
            object_occurrence_count=2,
            anomalies=(
                {"kind": "duplicate_key_conflict", "key": "observation_1.iq"},
            ),
        )
        report = build_bucket_inventory(
            listing,
            catalogue([]),
            bucket_url="https://example.test/bucket/",
            generated_at="2026-01-01T00:00:00Z",
        )
        kinds = [item["kind"] for item in report["anomalies"]]
        self.assertIn("duplicate_key_conflict", kinds)
        self.assertIn("duplicate_observation_id", kinds)

    def test_invalid_and_negative_sizes_are_rejected(self) -> None:
        invalid = page([("observation_1.iq", 1)], truncated=False).replace(
            b"<Size>1</Size>", b"<Size>wat</Size>"
        )
        with self.assertRaisesRegex(ValueError, "invalid Size"):
            parse_list_objects_v1(invalid)
        negative = page([("observation_1.iq", -1)], truncated=False)
        with self.assertRaisesRegex(ValueError, "negative Size"):
            parse_list_objects_v1(negative)

    def test_snapshot_comparison_reports_added_removed_and_size_changes(self) -> None:
        def snapshot(items: list[tuple[int, int]]) -> dict[str, object]:
            return {
                "schema_version": "polyitan-live-bucket-inventory-v1",
                "summary": {"exact_iq_key_count": len(items)},
                "objects": [
                    {
                        "observation_id": observation_id,
                        "key": f"observation_{observation_id}.iq",
                        "size_bytes": size,
                    }
                    for observation_id, size in items
                ],
            }

        comparison = compare_bucket_inventories(
            snapshot([(1, 10), (2, 20), (4, 40)]),
            snapshot([(2, 21), (3, 30), (4, 40)]),
        )
        self.assertEqual(
            comparison["added"],
            [{"observation_id": 3, "key": "observation_3.iq", "size_bytes": 30}],
        )
        self.assertEqual(
            comparison["removed"],
            [{"observation_id": 1, "key": "observation_1.iq", "size_bytes": 10}],
        )
        self.assertEqual(
            comparison["size_changes"],
            [
                {
                    "observation_id": 2,
                    "key": "observation_2.iq",
                    "previous_size_bytes": 20,
                    "current_size_bytes": 21,
                }
            ],
        )


if __name__ == "__main__":
    unittest.main()
