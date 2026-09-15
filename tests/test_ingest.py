from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from telemetry_yield.events import EventCandidate
from telemetry_yield.ingest import (
    ConversionEvidence,
    EventMatchEvidence,
    IngestCandidate,
    TimeBasis,
    group_transmission_events,
    plan_bounded_ingest,
    same_bounded_transmission_event,
    write_immutable_manifest,
)


METADATA_SHA256 = "a" * 64


def conversion(**updates: object) -> ConversionEvidence:
    values: dict[str, object] = {
        "sample_rate_hz": 48_000,
        "doppler_state": "pre_correction",
        "spectral_sign": -1,
        "time_basis": TimeBasis(
            reference="satnogs_observation_start",
            raw_seconds_to_reference_scale=1.0,
            raw_start_offset_seconds=22.5,
            evidence="bounded TLE ridge fit",
        ),
        "evidence_refs": ("reports/camras-real-iq-pilot.json",),
    }
    values.update(updates)
    return ConversionEvidence(**values)  # type: ignore[arg-type]


def candidate(path: Path, **updates: object) -> IngestCandidate:
    content = path.read_bytes()
    values: dict[str, object] = {
        "observation_id": "9614528",
        "source_url": "https://data.camras.nl/satnogs/iq_9614528.raw",
        "source_path": path,
        "content_length_bytes": len(content),
        "content_sha256": hashlib.sha256(content).hexdigest(),
        "metadata_sha256": METADATA_SHA256,
        "conversion": conversion(),
        "norad_id": 56992,
        "transmitter_uuid": "tx-1",
        "start_utc": datetime(2026, 1, 1, tzinfo=UTC),
        "end_utc": datetime(2026, 1, 1, 0, 5, tzinfo=UTC),
        "station_id": "PI9RD",
    }
    values.update(updates)
    return IngestCandidate(**values)  # type: ignore[arg-type]


def event_candidate(observation_id: str, **updates: object) -> EventCandidate:
    values: dict[str, object] = {
        "observation_id": observation_id,
        "norad_id": 123,
        "transmitter_uuid": "tx",
        "start_utc": datetime(2026, 1, 1, tzinfo=UTC),
        "end_utc": datetime(2026, 1, 1, 0, 5, tzinfo=UTC),
        "station_id": observation_id,
    }
    values.update(updates)
    return EventCandidate(**values)  # type: ignore[arg-type]


class IngestPlannerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.recording = self.root / "recording.raw"
        self.recording.write_bytes(b"bounded-iq")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_ready_recording_requires_verified_contract_hash_and_storage(self) -> None:
        plan = plan_bounded_ingest(
            [candidate(self.recording)],
            self.root / "derived",
            free_bytes=20_000_000_000,
        )
        self.assertEqual(plan.ready_count, 1)
        self.assertEqual(plan.blocked_count, 0)
        self.assertTrue(plan.storage_preflight_passed)
        manifest = plan.as_manifest(source_snapshots={"snapshot": "b" * 64})
        self.assertFalse(manifest["policy"]["download_iq"])  # type: ignore[index]
        self.assertFalse(manifest["policy"]["publication"])  # type: ignore[index]

    def test_unresolved_spectral_sign_blocks_conversion(self) -> None:
        item = candidate(self.recording, conversion=conversion(spectral_sign=None))
        plan = plan_bounded_ingest(
            [item], self.root / "derived", free_bytes=20_000_000_000
        )
        self.assertEqual(plan.ready_count, 0)
        self.assertIn("missing_or_invalid_spectral_sign", plan.decisions[0].blockers)

    def test_local_content_hash_mismatch_blocks_conversion(self) -> None:
        item = candidate(self.recording, content_sha256="0" * 64)
        plan = plan_bounded_ingest(
            [item], self.root / "derived", free_bytes=20_000_000_000
        )
        self.assertIn("local_content_sha256_mismatch", plan.decisions[0].blockers)

    def test_storage_preflight_failure_blocks_otherwise_ready_recording(self) -> None:
        plan = plan_bounded_ingest(
            [candidate(self.recording)],
            self.root / "derived",
            free_bytes=10_000_000_000,
        )
        self.assertFalse(plan.storage_preflight_passed)
        self.assertEqual(plan.ready_count, 0)
        self.assertIn("storage_preflight_failed", plan.decisions[0].blockers)

    def test_concurrency_and_recording_caps_fail_closed(self) -> None:
        item = candidate(self.recording)
        with self.assertRaisesRegex(ValueError, "max_concurrency"):
            plan_bounded_ingest(
                [item], self.root / "derived", max_concurrency=3
            )
        items = [
            candidate(self.recording, observation_id=str(index)) for index in range(3)
        ]
        with self.assertRaisesRegex(PermissionError, "at most 2"):
            plan_bounded_ingest(items, self.root / "derived")

    def test_manifest_is_idempotent_but_rejects_different_content(self) -> None:
        path = self.root / "manifest.json"
        plan = plan_bounded_ingest(
            [candidate(self.recording)],
            self.root / "derived",
            free_bytes=20_000_000_000,
        )
        first = write_immutable_manifest(
            path, plan, source_snapshots={"snapshot": "b" * 64}
        )
        second = write_immutable_manifest(
            path, plan, source_snapshots={"snapshot": "b" * 64}
        )
        self.assertEqual(first, second)
        changed = plan_bounded_ingest(
            [candidate(self.recording)],
            self.root / "derived",
            max_concurrency=1,
            free_bytes=20_000_000_000,
        )
        with self.assertRaisesRegex(FileExistsError, "immutable manifest"):
            write_immutable_manifest(
                path, changed, source_snapshots={"snapshot": "b" * 64}
            )


class EventGroupingTests(unittest.TestCase):
    def test_time_overlap_and_payload_alone_are_insufficient(self) -> None:
        left = event_candidate("1", payload_hashes=frozenset({"same"}))
        right = event_candidate("2", payload_hashes=frozenset({"same"}))
        self.assertFalse(
            same_bounded_transmission_event(
                left,
                right,
                evidence=EventMatchEvidence(),
                time_tolerance=timedelta(seconds=30),
            )
        )

    def test_doppler_or_geometric_evidence_can_confirm_identity_and_overlap(self) -> None:
        left = event_candidate("1")
        right = event_candidate("2")
        for evidence in (
            EventMatchEvidence(doppler_tracks_consistent=True),
            EventMatchEvidence(geometric_visibility_consistent=True),
        ):
            with self.subTest(evidence=evidence):
                self.assertTrue(
                    same_bounded_transmission_event(
                        left,
                        right,
                        evidence=evidence,
                        time_tolerance=timedelta(seconds=30),
                    )
                )

    def test_identity_mismatch_and_conflicting_payloads_reject(self) -> None:
        physical = EventMatchEvidence(doppler_tracks_consistent=True)
        left = event_candidate("1", payload_hashes=frozenset({"a"}))
        wrong_identity = event_candidate("2", norad_id=124)
        self.assertFalse(
            same_bounded_transmission_event(
                left,
                wrong_identity,
                evidence=physical,
                time_tolerance=timedelta(seconds=30),
            )
        )
        conflicting = event_candidate("2", payload_hashes=frozenset({"b"}))
        self.assertFalse(
            same_bounded_transmission_event(
                left,
                conflicting,
                evidence=physical,
                time_tolerance=timedelta(seconds=30),
            )
        )

    def test_explicit_tolerance_and_complete_link_grouping(self) -> None:
        first = event_candidate("1")
        second = event_candidate(
            "2",
            start_utc=datetime(2026, 1, 1, 0, 5, 20, tzinfo=UTC),
            end_utc=datetime(2026, 1, 1, 0, 6, tzinfo=UTC),
        )
        third = event_candidate(
            "3",
            start_utc=datetime(2026, 1, 1, 0, 5, 25, tzinfo=UTC),
            end_utc=datetime(2026, 1, 1, 0, 6, tzinfo=UTC),
        )
        physical = EventMatchEvidence(geometric_visibility_consistent=True)
        self.assertTrue(
            same_bounded_transmission_event(
                first,
                second,
                evidence=physical,
                time_tolerance=timedelta(seconds=30),
            )
        )
        groups = group_transmission_events(
            [first, second, third],
            {("1", "2"): physical, ("2", "3"): physical},
            time_tolerance=timedelta(seconds=30),
        )
        self.assertEqual(
            [[item.observation_id for item in group] for group in groups],
            [["1", "2"], ["3"]],
        )


if __name__ == "__main__":
    unittest.main()
