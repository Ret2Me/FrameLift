from __future__ import annotations

import unittest

from telemetry_yield.live_bucket_delta import select_live_bucket_delta


def inventory() -> dict[str, object]:
    return {
        "source": {
            "bucket_url": "https://example.test/iq-data/",
            "payload_objects_downloaded": False,
        },
        "summary": {"new_in_bucket_count": 2},
        "comparison": {"new_in_bucket_observation_ids": [2, 3]},
        "objects": [
            {
                "observation_id": 1,
                "key": "observation_1.iq",
                "size_bytes": 100,
                "catalogue_present": True,
            },
            {
                "observation_id": 3,
                "key": "observation_3.iq",
                "size_bytes": 303,
                "catalogue_present": False,
            },
            {
                "observation_id": 2,
                "key": "observation_2.iq",
                "size_bytes": 202,
                "catalogue_present": False,
            },
        ],
    }


class LiveBucketDeltaTests(unittest.TestCase):
    def test_selects_only_new_objects_with_exact_live_size_and_url(self) -> None:
        candidates = select_live_bucket_delta(inventory())
        self.assertEqual([item.observation_id for item in candidates], [2, 3])
        self.assertEqual([item.expected_size_bytes for item in candidates], [202, 303])
        self.assertEqual(
            [item.url for item in candidates],
            [
                "https://example.test/iq-data/observation_2.iq",
                "https://example.test/iq-data/observation_3.iq",
            ],
        )

    def test_rejects_nonpositive_size_and_key_id_mismatch(self) -> None:
        bad_size = inventory()
        bad_size["objects"][1]["size_bytes"] = 0
        with self.assertRaisesRegex(ValueError, "positive live size"):
            select_live_bucket_delta(bad_size)

        bad_key = inventory()
        bad_key["objects"][1]["key"] = "observation_30.iq"
        with self.assertRaisesRegex(ValueError, "does not match"):
            select_live_bucket_delta(bad_key)

    def test_report_cross_checks_fail_closed(self) -> None:
        wrong_ids = inventory()
        wrong_ids["comparison"]["new_in_bucket_observation_ids"] = [2]
        with self.assertRaisesRegex(ValueError, "comparison IDs"):
            select_live_bucket_delta(wrong_ids)

        wrong_count = inventory()
        wrong_count["summary"]["new_in_bucket_count"] = 3
        with self.assertRaisesRegex(ValueError, "summary count"):
            select_live_bucket_delta(wrong_count)

    def test_requires_metadata_only_provenance(self) -> None:
        value = inventory()
        value["source"]["payload_objects_downloaded"] = True
        with self.assertRaisesRegex(ValueError, "metadata-only"):
            select_live_bucket_delta(value)


if __name__ == "__main__":
    unittest.main()
