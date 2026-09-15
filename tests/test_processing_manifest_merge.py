from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from telemetry_yield.archive_processing import (
    EXPLICIT_MODE,
    merge_processing_manifest_documents,
    merge_processing_manifest_paths,
)
from telemetry_yield.canonical import canonical_json


CONTRACT = {
    "frames_recovered": False,
    "generic_fsk_implies_ax25": False,
    "input_datatype": "ci16_le interleaved I,Q",
    "mode_exact": EXPLICIT_MODE,
    "sample_rate_hz": 57_600,
}


def catalogue() -> dict[str, object]:
    return {
        "observations": [
            {
                "observation_id": observation_id,
                "mode": EXPLICIT_MODE,
                "frames_recovered": False,
                "links": {"iq": f"https://example.test/{observation_id}.iq"},
                "artifacts": {"iq": {"size_bytes": 8}},
                "satellite_id": "SAT",
            }
            for observation_id in (1, 2)
        ]
    }


def success(
    observation_id: int,
    *,
    digest: str | None = None,
    count: int = 0,
    status: str = "processed",
) -> dict[str, object]:
    return {
        "observation_id": observation_id,
        "status": status,
        "decode_output_path": f"/tmp/observation_{observation_id}.json",
        "decode_output_sha256": digest or str(observation_id) * 64,
        "crc_valid_frame_count": count,
        "error": None,
    }


def error(observation_id: int) -> dict[str, object]:
    return {
        "observation_id": observation_id,
        "status": "error",
        "decode_output_path": None,
        "decode_output_sha256": None,
        "crc_valid_frame_count": None,
        "error": "failed",
    }


def manifest(results: list[dict[str, object]], *, candidate_count: int = 2) -> dict[str, object]:
    statuses: dict[str, int] = {}
    for result in results:
        status = str(result["status"])
        statuses[status] = statuses.get(status, 0) + 1
    return {
        "schema_version": "archive-g3ruh-processing-manifest-v1",
        "selection_contract": dict(CONTRACT),
        "candidate_count": candidate_count,
        "latest_result_count": len(results),
        "status_counts": statuses,
        "results": results,
    }


class ProcessingManifestMergeTests(unittest.TestCase):
    def test_partial_manifest_reports_missing_expected_ids(self) -> None:
        merged = merge_processing_manifest_documents(
            catalogue(), [manifest([success(1)])]
        )
        self.assertFalse(merged["complete"])
        self.assertEqual(merged["candidate_count"], 2)
        self.assertEqual(merged["latest_result_count"], 1)
        self.assertEqual(merged["missing_observation_ids"], [2])

    def test_disjoint_shards_form_complete_sorted_campaign(self) -> None:
        merged = merge_processing_manifest_documents(
            catalogue(),
            [
                manifest([success(2, status="skipped_complete")], candidate_count=1),
                manifest([success(1)], candidate_count=1),
            ],
        )
        self.assertTrue(merged["complete"])
        self.assertEqual(
            [item["observation_id"] for item in merged["results"]], [1, 2]
        )
        self.assertEqual(
            merged["status_counts"], {"processed": 1, "skipped_complete": 1}
        )

    def test_semantically_identical_overlap_is_deduplicated_deterministically(self) -> None:
        first = manifest([success(1, count=2, status="processed")])
        second = manifest([success(1, count=2, status="skipped_complete")])
        left = merge_processing_manifest_documents(catalogue(), [first, second])
        right = merge_processing_manifest_documents(catalogue(), [second, first])
        self.assertEqual(canonical_json(left), canonical_json(right))
        self.assertEqual(left["latest_result_count"], 1)
        self.assertEqual(left["results"][0]["status"], "processed")

    def test_overlap_conflicts_fail_closed(self) -> None:
        base = manifest([success(1, count=2)])
        conflicts = [
            manifest([success(1, digest="a" * 64, count=2)]),
            manifest([success(1, count=3)]),
            manifest([error(1)]),
        ]
        for conflict in conflicts:
            with self.subTest(conflict=conflict["results"][0]["status"]):
                with self.assertRaisesRegex(ValueError, "conflicting overlap"):
                    merge_processing_manifest_documents(
                        catalogue(), [base, conflict]
                    )

    def test_missing_inputs_and_out_of_campaign_ids_fail_closed(self) -> None:
        with self.assertRaisesRegex(ValueError, "at least one"):
            merge_processing_manifest_documents(catalogue(), [])
        with self.assertRaisesRegex(ValueError, "outside expected"):
            merge_processing_manifest_documents(
                catalogue(), [manifest([success(3)])]
            )

    def test_path_merge_writes_atomic_full_campaign_manifest(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            catalogue_path = root / "catalogue.json"
            first_path = root / "first.json"
            second_path = root / "second.json"
            output = root / "merged.json"
            catalogue_path.write_text(json.dumps(catalogue()))
            first_path.write_text(json.dumps(manifest([success(1)], candidate_count=1)))
            second_path.write_text(json.dumps(manifest([success(2)], candidate_count=1)))
            merged = merge_processing_manifest_paths(
                catalogue_path, [first_path, second_path], output
            )
            self.assertTrue(merged["complete"])
            self.assertEqual(json.loads(output.read_text()), merged)
            self.assertFalse(output.with_name(output.name + ".tmp").exists())


if __name__ == "__main__":
    unittest.main()
