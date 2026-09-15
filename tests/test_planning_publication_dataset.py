import hashlib
import json
import tempfile
import unittest
from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest import mock
from urllib.parse import urlencode

from telemetry_yield.planning.dataset import (
    DatasetIntegrityError,
    NormalizedObservation,
    build_normalized_dataset,
    read_normalized_jsonl,
    write_normalized_jsonl,
)
from telemetry_yield.planning.models import OrbitSample, PassWindow
from telemetry_yield.planning.audit import (
    _audit_simulation,
    _recompute_probability_metrics,
    audit_publication_artifacts,
)
from telemetry_yield.planning.monte_carlo import (
    CorrelatedRiskScenario,
    simulate_plan_yield,
    simulate_plan_yield_correlated,
)
from telemetry_yield.planning.publication import (
    _comparison_bootstraps,
    acquire_frozen_cohort,
    build_publication_dataset,
    evaluate_publication_dataset,
)
from telemetry_yield.planning.evaluation import (
    ComposedPredictionRecord,
    PredictionRecord,
    binary_decision_metrics,
    evaluate_composed_reception_group_holdout,
    evaluate_composed_reception_temporal_holdout,
    evaluate_leave_one_group_out,
    evaluate_temporal_holdout,
    fold_examples,
    paired_cluster_bootstrap_brier,
    probability_metrics,
    reception_success_outcome,
)
from telemetry_yield.planning.replay import (
    paired_day_bootstrap_replay,
    scheduling_replay,
)
from telemetry_yield.planning.tle_audit import (
    DEFAULT_TLE_MATCH_TOLERANCE,
    TleDriftRecord,
    _nearest,
    audit_successive_tle_drift,
    summarize_tle_drift,
)
from telemetry_yield.planning.scheduler_benchmark import (
    build_scheduler_robustness_plan,
    run_scheduler_benchmark,
)
from telemetry_yield.planning.store import plan_document
from telemetry_yield.planning.reporting import write_evaluation_figures
from telemetry_yield.planning.provenance import build_planning_source_manifest
from telemetry_yield.planning.readiness import _snapshot_integrity
from telemetry_yield.planning.orbit import OrbitPredictionError, Sgp4PassPredictor
from telemetry_yield.planning.satnogs_dataset import (
    PublicationAcquisitionWindow,
    PublicationDatasetConfig,
    PublicationSamplingInterval,
    PublicationTarget,
    calendar_month_chunks,
    fetch_publication_snapshot,
    load_snapshot_observations,
    publication_config_identity,
)
from telemetry_yield.satnogs import SATNOGS_NETWORK_API, SatNOGSError, SatNOGSResponse


TLE1 = "1 25544U 98067A   24123.50000000  .00016717  00000+0  30210-3 0  9997"
TLE2 = "2 25544  51.6400 100.0000 0005000  50.0000 310.0000 15.50000000450003"


def observation(observation_id=1, *, waterfall="with-signal", demoddata=None):
    return {
        "id": observation_id,
        "start": "2024-05-03T00:00:00Z",
        "end": "2024-05-03T00:05:00Z",
        "status": "good",
        "waterfall_status": waterfall,
        "ground_station": 12,
        "station_name": "fixture",
        "station_lat": 52.2,
        "station_lng": 21.0,
        "station_alt": 100,
        "norad_cat_id": 25544,
        "transmitter": "A" * 22,
        "transmitter_mode": "FSK",
        "transmitter_baud": 9600,
        "transmitter_downlink_low": 145_800_000,
        "transmitter_status": "active",
        "transmitter_unconfirmed": False,
        "demoddata": [] if demoddata is None else demoddata,
        "max_altitude": 61.5,
        "rise_azimuth": 92.0,
        "set_azimuth": 271.0,
        "tle0": "0 ISS",
        "tle1": TLE1,
        "tle2": TLE2,
    }


class FakePublicationClient:
    base_url = SATNOGS_NETWORK_API
    authentication_mode = "anonymous"

    def __init__(self, body, user_agent="study/contact", max_response_bytes=5000):
        self.body = body
        self.user_agent = user_agent
        self.max_response_bytes = max_response_bytes
        self.calls = []

    def get(self, path, *, params=None):
        self.calls.append((path, params))
        url = "https://network.satnogs.org/api/observations/"
        if params:
            url = f"{url}?{urlencode(params)}"
        return SatNOGSResponse(
            url=url,
            body=self.body,
            status=200,
            headers={"Date": "Fri, 03 May 2024 01:00:00 GMT"},
        )


class PagedPublicationClient(FakePublicationClient):
    def __init__(self):
        super().__init__(b"[]")
        self.current_norad = None
        self.current_waterfall = None

    def get(self, path, *, params=None):
        if params is not None:
            self.current_norad = int(params["norad_cat_id"])
            self.current_waterfall = int(params.get("waterfall_status", 1))
            headers = {
                "Link": (
                    f'<https://network.satnogs.org/api/observations/?cursor={self.current_norad}>; '
                    'rel="next"'
                )
            }
            page = 1
        else:
            self.assert_official_url = str(path).startswith(SATNOGS_NETWORK_API)
            headers = {}
            page = 2
        self.calls.append((path, params))
        body = json.dumps(
            [
                {
                    "id": self.current_norad * 100 + self.current_waterfall * 10 + page,
                    "norad_cat_id": self.current_norad,
                    "waterfall_status": (
                        "with-signal" if self.current_waterfall == 1 else "without-signal"
                    ),
                }
            ]
        ).encode()
        return SatNOGSResponse(
            url=(
                f"https://network.satnogs.org/api/observations/?fixture={self.current_norad}&page={page}"
            ),
            body=body,
            status=200,
            headers=headers,
        )


class InterruptAfterFirstPageClient(PagedPublicationClient):
    def get(self, path, *, params=None):
        if params is None:
            self.calls.append((path, params))
            raise SatNOGSError("simulated cursor-page interruption")
        return super().get(path, params=params)


def publication_config():
    targets = tuple(
        PublicationTarget(value, "A" * 22, "FSK", 100, 50, 30)
        for value in (25544, 25545, 25546, 25547, 25548)
    )
    return PublicationDatasetConfig(
        schema_version="observation-planning-publication-config-v1",
        study_id="fixture",
        frozen_at=datetime(2024, 6, 1, tzinfo=UTC),
        start=datetime(2024, 5, 1, tzinfo=UTC),
        end=datetime(2024, 6, 1, tzinfo=UTC),
        targets=targets,
        geometry_step_seconds=30,
        minimum_chunk_hours=6,
        max_response_bytes=5000,
        random_seed=1,
        bootstrap_replicates=10,
        temporal_test_fraction=0.2,
        regularization_grid=(0.1, 1.0),
        user_agent="study/contact",
    )


class PublicationDatasetTests(unittest.TestCase):
    def test_snapshot_manifest_checkpoint_is_written_atomically(self):
        config = publication_config()
        client = FakePublicationClient(b"[]")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest_path = root / "manifest.json"
            from telemetry_yield.planning import satnogs_dataset

            with mock.patch.object(
                satnogs_dataset,
                "_write_bytes_atomic",
                wraps=satnogs_dataset._write_bytes_atomic,
            ) as atomic_write:
                fetch_publication_snapshot(
                    client,  # type: ignore[arg-type]
                    config,
                    raw_dir=root / "raw",
                    manifest_path=manifest_path,
                    now=lambda: datetime(2024, 6, 2, tzinfo=UTC),
                )

            manifest_calls = [
                call
                for call in atomic_write.call_args_list
                if call.args and call.args[0] == manifest_path
            ]
            self.assertGreaterEqual(len(manifest_calls), 2)
            self.assertFalse(manifest_path.with_suffix(".json.tmp").exists())
            self.assertTrue(json.loads(manifest_path.read_text())["complete"])

    def test_publication_acquisition_avoids_redundant_http_cache_copy(self):
        config = publication_config()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_path = root / "config.json"
            config_path.write_text("{}", encoding="utf-8")
            manifest_path = root / "manifest.json"
            with (
                mock.patch(
                    "telemetry_yield.planning.publication.PublicationDatasetConfig.load",
                    return_value=config,
                ),
                mock.patch(
                    "telemetry_yield.planning.publication.SatNOGSClient"
                ) as client_class,
                mock.patch(
                    "telemetry_yield.planning.publication.fetch_publication_snapshot",
                    return_value={"total_rows_before_deduplication": 0},
                ),
            ):
                acquire_frozen_cohort(
                    config_path,
                    raw_dir=root / "raw",
                    manifest_path=manifest_path,
                    cache_dir=root / "legacy-http-cache",
                    shared_rate_limit_path=root / "rate-limit",
                )

            self.assertIsNone(client_class.call_args.kwargs["cache_dir"])
            self.assertFalse((root / "legacy-http-cache").exists())

    def test_authenticated_acquisition_uses_documented_higher_rate(self):
        config = publication_config()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_path = root / "config.json"
            config_path.write_text("{}", encoding="utf-8")
            with (
                mock.patch(
                    "telemetry_yield.planning.publication.PublicationDatasetConfig.load",
                    return_value=config,
                ),
                mock.patch(
                    "telemetry_yield.planning.publication.SatNOGSClient"
                ) as client_class,
                mock.patch(
                    "telemetry_yield.planning.publication.fetch_publication_snapshot",
                    return_value={"total_rows_before_deduplication": 0},
                ) as fetch,
            ):
                acquire_frozen_cohort(
                    config_path,
                    raw_dir=root / "raw",
                    manifest_path=root / "manifest.json",
                    cache_dir=root / "cache",
                    shared_rate_limit_path=root / "rate-limit",
                    api_token="secret-token",
                )

            self.assertEqual(client_class.call_args.kwargs["api_token"], "secret-token")
            self.assertEqual(
                client_class.call_args.kwargs["shared_minimum_interval_seconds"],
                15.25,
            )
            self.assertEqual(
                fetch.call_args.kwargs["minimum_request_interval_seconds"],
                15.25,
            )

    def test_external_sample_gate_is_part_of_frozen_identity(self):
        first = replace(
            publication_config(),
            external_validation_norad_ids=(25544,),
            minimum_external_test_rows=10,
        )
        second = replace(first, minimum_external_test_rows=11)
        self.assertNotEqual(
            publication_config_identity(first), publication_config_identity(second)
        )

    def test_dataset_build_rejects_snapshot_from_another_config(self):
        project_root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = root / "snapshot.json"
            manifest.write_text(
                json.dumps(
                    {
                        "schema_version": "satnogs-publication-snapshot-manifest-v1",
                        "study_id": "observation-planning-publication-v1",
                        "source": SATNOGS_NETWORK_API,
                        "config_identity_sha256": "0" * 64,
                        "complete": True,
                        "responses": [],
                    }
                )
            )
            with self.assertRaisesRegex(DatasetIntegrityError, "does not belong"):
                build_publication_dataset(
                    project_root / "configs" / "observation-planning-publication-v1.json",
                    manifest,
                    dataset_path=root / "dataset.jsonl",
                    dataset_manifest_path=root / "dataset-manifest.json",
                )

    def test_normalization_uses_embedded_tle_and_independent_labels(self):
        result = build_normalized_dataset(
            [
                observation(1, waterfall="with-signal", demoddata=[{"payload": "x"}]),
                observation(2, waterfall="without-signal", demoddata=[]),
                observation(3, waterfall="unknown", demoddata=[{"payload": "x"}]),
            ],
            retrieved_at=datetime(2024, 5, 4, tzinfo=UTC),
            predictor=Sgp4PassPredictor(step_seconds=30),
        )
        self.assertEqual([row.signal_present for row in result.rows], [1, 0, None])
        self.assertEqual(
            [row.decode_success_given_signal for row in result.rows], [1, None, 1]
        )
        self.assertEqual(result.known_signal_count, 2)
        self.assertGreater(result.rows[0].min_range_km, 0)
        self.assertGreater(result.rows[0].max_abs_doppler_hz, 0)
        self.assertEqual(result.rows[0].max_elevation_deg, 61.5)
        self.assertEqual(result.rows[0].rise_azimuth_deg, 92.0)
        self.assertEqual(result.rows[0].set_azimuth_deg, 271.0)
        self.assertEqual(result.rows[0].tle_line1, TLE1)

    def test_normalization_prefers_capture_time_client_location_and_allowlisted_receiver(self):
        captured = observation(7)
        captured["station_lat"] = 1.0
        captured["station_lng"] = 2.0
        captured["station_alt"] = 3.0
        captured["client_metadata"] = json.dumps(
            {
                "latitude": 52.21,
                "longitude": 21.01,
                "elevation": 101,
                "radio": {
                    "name": "gr-satnogs",
                    "version": "v2.3.5",
                    "parameters": {
                        "soapy-rx-device": "driver=rtlsdr,serial=SECRET",
                        "samp-rate-rx": "2.048e6",
                        "gain-mode": "Overall",
                        "gain": "40.2",
                        "antenna": "RX",
                        "ppm": "4.5",
                        "file-path": "/must/not/be/retained",
                    },
                },
            }
        )
        row = build_normalized_dataset(
            [captured], retrieved_at=datetime(2024, 5, 4, tzinfo=UTC)
        ).rows[0]
        self.assertEqual(row.schema_version, "satnogs-observation-features-v3")
        self.assertEqual(row.station_latitude_deg, 52.21)
        self.assertEqual(row.station_longitude_deg, 21.01)
        self.assertEqual(row.station_altitude_m, 101)
        self.assertEqual(row.station_location_source, "satnogs-client-metadata")
        self.assertEqual(row.station_location_recorded_at, row.start)
        self.assertEqual(row.client_metadata_parse_status, "parsed_location_valid")
        self.assertEqual(row.captured_receiver_driver, "rtlsdr")
        self.assertEqual(row.captured_receiver_rf_gain_db, 40.2)
        self.assertEqual(row.captured_receiver_sample_rate_hz, 2_048_000)
        self.assertNotIn("SECRET", json.dumps(row.as_dict()))
        self.assertNotIn("file-path", json.dumps(row.as_dict()))

        without_current_location = observation(8)
        without_current_location["station_lat"] = None
        without_current_location["station_lng"] = None
        without_current_location["station_alt"] = None
        without_current_location["client_metadata"] = captured["client_metadata"]
        restored = build_normalized_dataset(
            [without_current_location],
            retrieved_at=datetime(2024, 5, 4, tzinfo=UTC),
        )
        self.assertEqual(len(restored.rows), 1)
        self.assertEqual(restored.rows[0].station_latitude_deg, 52.21)

    def test_malformed_client_metadata_uses_flagged_current_location_fallback(self):
        malformed = observation(9)
        malformed["client_metadata"] = "{not-json"
        row = build_normalized_dataset(
            [malformed], retrieved_at=datetime(2024, 5, 4, tzinfo=UTC)
        ).rows[0]
        self.assertEqual(row.station_location_source, "satnogs-api-current-station")
        self.assertIsNone(row.station_location_recorded_at)
        self.assertEqual(row.client_metadata_parse_status, "malformed_json")
        self.assertIsNone(row.captured_receiver_configuration_source)

    def test_fail_closed_exclusions_and_conflicting_duplicates(self):
        failed = observation(1)
        failed["status"] = "failed"
        result = build_normalized_dataset(
            [failed, failed], retrieved_at=datetime(2024, 5, 4, tzinfo=UTC)
        )
        self.assertEqual(result.exclusion_counts, {"failed_job": 1})
        self.assertEqual(result.duplicate_count, 1)
        bad_tle = observation(2)
        bad_tle["tle1"] = str(bad_tle["tle1"])[:-1] + "0"
        checksum_result = build_normalized_dataset(
            [bad_tle], retrieved_at=datetime(2024, 5, 4, tzinfo=UTC)
        )
        self.assertEqual(checksum_result.exclusion_counts, {"tle_checksum_mismatch": 1})
        deleted_station = observation(3)
        deleted_station["ground_station"] = None
        deleted_station["station_lat"] = None
        deleted_station_result = build_normalized_dataset(
            [deleted_station], retrieved_at=datetime(2024, 5, 4, tzinfo=UTC)
        )
        self.assertEqual(deleted_station_result.exclusion_counts, {"missing_station": 1})
        missing_demoddata = observation(4, waterfall="with-signal")
        missing_demoddata.pop("demoddata")
        malformed_demoddata_result = build_normalized_dataset(
            [missing_demoddata],
            retrieved_at=datetime(2024, 5, 4, tzinfo=UTC),
        )
        self.assertEqual(
            malformed_demoddata_result.exclusion_counts,
            {"malformed_demoddata": 1},
        )
        contradictory = observation(
            5, waterfall="without-signal", demoddata=[{"payload": "x"}]
        )
        contradictory_result = build_normalized_dataset(
            [contradictory],
            retrieved_at=datetime(2024, 5, 4, tzinfo=UTC),
        )
        self.assertEqual(
            contradictory_result.exclusion_counts,
            {"contradictory_signal_and_demoddata": 1},
        )
        cw_contradictory = observation(
            6, waterfall="without-signal", demoddata=[{"payload": "x"}]
        )
        cw_contradictory["transmitter_mode"] = "CW"
        cw_contradictory_result = build_normalized_dataset(
            [cw_contradictory],
            retrieved_at=datetime(2024, 5, 4, tzinfo=UTC),
        )
        self.assertEqual(
            cw_contradictory_result.exclusion_counts,
            {"contradictory_signal_and_demoddata": 1},
        )
        conflict = observation(1)
        conflict["station_lat"] = 1
        with self.assertRaisesRegex(DatasetIntegrityError, "conflicting duplicate"):
            build_normalized_dataset(
                [observation(1), conflict],
                retrieved_at=datetime(2024, 5, 4, tzinfo=UTC),
            )

    def test_normalized_jsonl_roundtrip(self):
        result = build_normalized_dataset(
            [observation()], retrieved_at=datetime(2024, 5, 4, tzinfo=UTC)
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "rows.jsonl"
            write_normalized_jsonl(path, result.rows)
            loaded = read_normalized_jsonl(path)
        self.assertEqual(loaded, result.rows)

    def test_month_chunks_are_closed_and_contiguous(self):
        chunks = calendar_month_chunks(
            datetime(2024, 1, 15, tzinfo=UTC),
            datetime(2024, 3, 2, tzinfo=UTC),
        )
        self.assertEqual(len(chunks), 3)
        self.assertEqual(chunks[0][1], chunks[1][0])
        self.assertEqual(chunks[-1][1], datetime(2024, 3, 2, tzinfo=UTC))

    def test_snapshot_manifest_rechecks_hashes_and_counts(self):
        payload = json.dumps([]).encode()
        client = FakePublicationClient(payload)
        config = publication_config()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest_path = root / "manifest.json"
            manifest = fetch_publication_snapshot(
                client,  # type: ignore[arg-type]
                config,
                raw_dir=root / "raw",
                manifest_path=manifest_path,
                now=lambda: datetime(2024, 6, 2, tzinfo=UTC),
            )
            self.assertEqual(len(manifest["responses"]), 5)
            self.assertEqual(load_snapshot_observations(manifest_path), ())
            first = next(
                path
                for path in (root / "raw").iterdir()
                if not path.name.endswith(".meta.json")
            )
            first.write_bytes(b"[] ")
            with self.assertRaisesRegex(DatasetIntegrityError, "byte-length mismatch"):
                load_snapshot_observations(manifest_path)

    def test_snapshot_follows_cursor_link_and_retains_every_page(self):
        client = PagedPublicationClient()
        config = publication_config()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest_path = root / "manifest.json"
            manifest = fetch_publication_snapshot(
                client,  # type: ignore[arg-type]
                config,
                raw_dir=root / "raw",
                manifest_path=manifest_path,
                now=lambda: datetime(2024, 6, 2, tzinfo=UTC),
            )
            self.assertEqual(len(manifest["responses"]), 10)
            self.assertEqual(len(load_snapshot_observations(manifest_path)), 10)
            self.assertEqual([entry["page_index"] for entry in manifest["responses"][:2]], [1, 2])
            self.assertTrue(client.assert_official_url)

    def test_snapshot_resume_continues_from_saved_cursor_without_duplicate_page(self):
        config = publication_config()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest_path = root / "manifest.json"
            interrupted = InterruptAfterFirstPageClient()
            with self.assertRaisesRegex(DatasetIntegrityError, "rerun resumes"):
                fetch_publication_snapshot(
                    interrupted,  # type: ignore[arg-type]
                    config,
                    raw_dir=root / "raw",
                    manifest_path=manifest_path,
                    now=lambda: datetime(2024, 6, 2, tzinfo=UTC),
                )
            checkpoint = json.loads(manifest_path.read_text())
            self.assertEqual(len(checkpoint["responses"]), 1)
            self.assertIsNotNone(checkpoint["active_request"])

            resumed = PagedPublicationClient()
            active = checkpoint["active_request"]
            resumed.current_norad = int(active["norad_id"])
            resumed.current_waterfall = int(active["waterfall_status"])
            manifest = fetch_publication_snapshot(
                resumed,  # type: ignore[arg-type]
                config,
                raw_dir=root / "raw",
                manifest_path=manifest_path,
                now=lambda: datetime(2024, 6, 2, tzinfo=UTC),
            )
            observations = load_snapshot_observations(manifest_path)
        self.assertTrue(manifest["complete"])
        self.assertEqual(len(manifest["responses"]), 10)
        self.assertEqual(len(observations), 10)
        self.assertEqual(len({item["id"] for item in observations}), 10)
        self.assertIsNone(resumed.calls[0][1])
        self.assertIn("cursor=", str(resumed.calls[0][0]))

    def test_target_specific_acquisition_window_extends_only_declared_target(self):
        base = publication_config()
        config = PublicationDatasetConfig(
            **{
                **asdict(base),
                "targets": base.targets,
                "acquisition_windows": (
                    PublicationAcquisitionWindow(
                        norad_id=25548,
                        start=datetime(2024, 4, 1, tzinfo=UTC),
                        end=datetime(2024, 6, 1, tzinfo=UTC),
                    ),
                ),
            }
        )
        client = FakePublicationClient(b"[]")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = fetch_publication_snapshot(
                client,  # type: ignore[arg-type]
                config,
                raw_dir=root / "raw",
                manifest_path=root / "manifest.json",
                now=lambda: datetime(2024, 6, 2, tzinfo=UTC),
            )
        self.assertEqual(len(manifest["responses"]), 6)
        target_windows = [
            (entry["start"], entry["end"])
            for entry in manifest["responses"]
            if entry["norad_id"] == 25548
        ]
        self.assertEqual(
            target_windows,
            [
                ("2024-04-01T00:00:00Z", "2024-05-01T00:00:00Z"),
                ("2024-05-01T00:00:00Z", "2024-06-01T00:00:00Z"),
            ],
        )

    def test_supplemental_window_applies_frozen_transmitter_and_label_filters(self):
        base = publication_config()
        selected = base.targets[0]
        supplement = PublicationAcquisitionWindow(
            norad_id=selected.norad_id,
            start=datetime(2024, 4, 1, tzinfo=UTC),
            end=datetime(2024, 5, 1, tzinfo=UTC),
            transmitter_uuid=selected.selection_transmitter_uuid,
            waterfall_status=1,
        )
        config = PublicationDatasetConfig(
            **{
                **asdict(base),
                "targets": base.targets,
                "acquisition_windows": (),
                "supplemental_acquisition_windows": (supplement,),
            }
        )
        client = FakePublicationClient(b"[]")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = fetch_publication_snapshot(
                client,  # type: ignore[arg-type]
                config,
                raw_dir=root / "raw",
                manifest_path=root / "manifest.json",
                now=lambda: datetime(2024, 6, 2, tzinfo=UTC),
            )
        filtered_call = next(
            params
            for _path, params in client.calls
            if params is not None and "transmitter_uuid" in params
        )
        self.assertEqual(filtered_call["transmitter_uuid"], selected.selection_transmitter_uuid)
        self.assertEqual(filtered_call["waterfall_status"], 1)
        filtered_entry = next(
            entry
            for entry in manifest["responses"]
            if entry.get("transmitter_uuid_filter") is not None
        )
        self.assertEqual(filtered_entry["waterfall_status_filter"], 1)

    def test_common_sampling_intervals_apply_identically_to_every_target(self):
        base = publication_config()
        intervals = (
            PublicationSamplingInterval(
                start=datetime(2024, 5, 1, tzinfo=UTC),
                end=datetime(2024, 5, 8, tzinfo=UTC),
            ),
            PublicationSamplingInterval(
                start=datetime(2024, 5, 15, tzinfo=UTC),
                end=datetime(2024, 5, 22, tzinfo=UTC),
            ),
        )
        config = PublicationDatasetConfig(
            **{
                **asdict(base),
                "targets": base.targets,
                "sampling_intervals": intervals,
            }
        )
        client = FakePublicationClient(b"[]")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = fetch_publication_snapshot(
                client,  # type: ignore[arg-type]
                config,
                raw_dir=root / "raw",
                manifest_path=root / "manifest.json",
                now=lambda: datetime(2024, 6, 2, tzinfo=UTC),
            )
        self.assertEqual(len(manifest["responses"]), len(base.targets) * 2)
        self.assertEqual(
            {
                (entry["norad_id"], entry["start"], entry["end"])
                for entry in manifest["responses"]
            },
            {
                (
                    target.norad_id,
                    interval.start.isoformat().replace("+00:00", "Z"),
                    interval.end.isoformat().replace("+00:00", "Z"),
                )
                for target in base.targets
                for interval in intervals
            },
        )
        changed = PublicationDatasetConfig(
            **{
                **asdict(config),
                "targets": config.targets,
                "sampling_intervals": intervals[:1],
            }
        )
        self.assertNotEqual(
            publication_config_identity(config), publication_config_identity(changed)
        )

    def test_readiness_audits_exact_sampling_request_coverage_and_sidecars(self):
        base = publication_config()
        config = replace(
            base,
            sampling_intervals=(
                PublicationSamplingInterval(
                    start=datetime(2024, 5, 1, tzinfo=UTC),
                    end=datetime(2024, 5, 8, tzinfo=UTC),
                ),
                PublicationSamplingInterval(
                    start=datetime(2024, 5, 15, tzinfo=UTC),
                    end=datetime(2024, 5, 22, tzinfo=UTC),
                ),
            ),
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_path = root / "config.json"
            config_path.write_text(
                json.dumps(
                    asdict(config),
                    default=lambda value: value.isoformat()
                    if isinstance(value, datetime)
                    else value,
                ),
                encoding="utf-8",
            )
            manifest_path = root / "manifest.json"
            manifest = dict(
                fetch_publication_snapshot(
                    FakePublicationClient(b"[]"),  # type: ignore[arg-type]
                    config,
                    raw_dir=root / "raw",
                    manifest_path=manifest_path,
                    now=lambda: datetime(2024, 6, 2, tzinfo=UTC),
                )
            )
            config_sha = hashlib.sha256(config_path.read_bytes()).hexdigest()
            manifest["config_file_sha256"] = config_sha
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            dataset = {
                "study_id": config.study_id,
                "config_sha256": config_sha,
                "source_snapshot_manifest_sha256": hashlib.sha256(
                    manifest_path.read_bytes()
                ).hexdigest(),
            }
            valid, detail = _snapshot_integrity(
                manifest,
                snapshot_manifest_path=manifest_path,
                dataset=dataset,
                publication_config_path=config_path,
            )
            self.assertTrue(valid, detail)
            self.assertIn("expected_request_groups=10", detail)

            tampered = {**manifest, "responses": manifest["responses"][:-1]}
            manifest_path.write_text(json.dumps(tampered), encoding="utf-8")
            tampered_dataset = {
                **dataset,
                "source_snapshot_manifest_sha256": hashlib.sha256(
                    manifest_path.read_bytes()
                ).hexdigest(),
            }
            tampered_valid, tampered_detail = _snapshot_integrity(
                tampered,
                snapshot_manifest_path=manifest_path,
                dataset=tampered_dataset,
                publication_config_path=config_path,
            )
            self.assertFalse(tampered_valid)
            self.assertIn("coverage mismatch", tampered_detail)

    def test_sampling_intervals_reject_overlap(self):
        base = publication_config()
        config = PublicationDatasetConfig(
            **{
                **asdict(base),
                "targets": base.targets,
                "sampling_intervals": (
                    PublicationSamplingInterval(
                        start=datetime(2024, 5, 1, tzinfo=UTC),
                        end=datetime(2024, 5, 20, tzinfo=UTC),
                    ),
                    PublicationSamplingInterval(
                        start=datetime(2024, 5, 15, tzinfo=UTC),
                        end=datetime(2024, 5, 22, tzinfo=UTC),
                    ),
                ),
            }
        )
        with self.assertRaisesRegex(DatasetIntegrityError, "must not overlap"):
            config.validate()


def normalized_row(index):
    start = datetime(2024, 1, 1, tzinfo=UTC) + timedelta(hours=index)
    positive = int(index % 5 in {2, 3, 4})
    return NormalizedObservation(
        schema_version="satnogs-observation-features-v1",
        observation_id=index + 1,
        start=start,
        end=start + timedelta(minutes=5),
        duration_seconds=300,
        norad_id=100 + index % 5,
        station_id=200 + index % 6,
        station_name=f"station-{index % 6}",
        station_latitude_deg=-50 + index % 6 * 20,
        station_longitude_deg=-100 + index % 6 * 30,
        station_altitude_m=100,
        transmitter_uuid=chr(65 + index % 5) * 22,
        transmitter_mode="FSK",
        transmitter_baud=9600,
        frequency_hz=437_000_000,
        transmitter_status="active",
        tle_name="fixture",
        tle_line1=TLE1,
        tle_line2=TLE2,
        tle_epoch=start - timedelta(hours=12),
        tle_fingerprint="f" * 64,
        tle_age_hours=12,
        max_elevation_deg=10 + positive * 60 + index % 3,
        min_range_km=1800 - positive * 1000,
        max_abs_doppler_hz=5000,
        rise_azimuth_deg=index % 360,
        set_azimuth_deg=(index + 180) % 360,
        signal_present=positive,
        decode_success_given_signal=(index % 2 if positive else None),
        source_status="good",
        source_waterfall_status="with-signal" if positive else "without-signal",
        client_metadata_parse_status="parsed_location_valid",
        station_location_source="satnogs-client-metadata",
        station_location_recorded_at=start,
    )


class PublicationEvaluationTests(unittest.TestCase):
    def test_composed_reception_temporal_holdout_scores_probability_product(self):
        rows = [normalized_row(index) for index in range(80)]
        cutoff = datetime(2024, 1, 3, 12, tzinfo=UTC)
        result = evaluate_composed_reception_temporal_holdout(
            rows,
            test_fraction=0.2,
            regularization_grid=(0.1, 1.0),
            cutoff=cutoff,
        )
        self.assertEqual(result.test_count, 20)
        self.assertEqual(len(result.predictions), 80)
        self.assertEqual(set(result.metrics), {
            "global_rate",
            "group_rate",
            "geometry_logit",
            "full_logit",
        })
        for record in result.predictions:
            self.assertAlmostEqual(
                record.probability,
                record.signal_probability
                * record.decode_probability_given_signal,
            )
            self.assertEqual(record.task, "reception_success")
        self.assertEqual(result.metrics["full_logit"].count, 20)
        self.assertEqual(result.decision_metrics["full_logit"]["count"], 20)

    def test_composed_reception_group_holdout_is_future_and_disjoint(self):
        rows = [normalized_row(index) for index in range(120)]
        cutoff = datetime(2024, 1, 4, 8, tzinfo=UTC)
        result = evaluate_composed_reception_group_holdout(
            rows,
            group="norad_id",
            held_out_values=(103, 104),
            regularization_grid=(0.1,),
            test_fraction=0.2,
            minimum_test_rows=10,
            minimum_test_groups=2,
            cutoff=cutoff,
        )
        self.assertGreaterEqual(result.test_count, 10)
        self.assertEqual(
            {record.norad_id for record in result.predictions},
            {103, 104},
        )
        self.assertTrue(
            all(record.start >= "2024-01-04T08:00:00Z" for record in result.predictions)
        )

    def test_reception_endpoint_is_observable_only_for_packet_modes(self):
        positive = normalized_row(3)
        negative = normalized_row(0)
        self.assertEqual(reception_success_outcome(positive), 1)
        self.assertEqual(reception_success_outcome(negative), 0)
        self.assertIsNone(
            reception_success_outcome(replace(positive, transmitter_mode="CW"))
        )

    def test_binary_decision_metrics_exposes_yes_and_no_error_rates(self):
        metrics = binary_decision_metrics(
            [1, 1, 0, 0],
            [0.9, 0.4, 0.6, 0.1],
        )
        self.assertEqual(metrics["true_positive"], 1)
        self.assertEqual(metrics["true_negative"], 1)
        self.assertEqual(metrics["false_positive"], 1)
        self.assertEqual(metrics["false_negative"], 1)
        self.assertEqual(metrics["accuracy"], 0.5)

    def test_probability_metrics_reject_invalid_labels_and_probabilities(self):
        for function in (probability_metrics, binary_decision_metrics):
            with self.subTest(function=function.__name__, invalid="outcome"):
                with self.assertRaisesRegex(DatasetIntegrityError, "binary"):
                    function([0, 2], [0.1, 0.9])
            with self.subTest(function=function.__name__, invalid="probability"):
                with self.assertRaisesRegex(DatasetIntegrityError, "inside"):
                    function([0, 1], [0.1, 1.1])

    def test_fold_history_never_uses_held_out_or_future_outcomes(self):
        rows = [normalized_row(index) for index in range(12)]
        training, test = fold_examples(rows[4:], rows[:4], "signal_present")
        self.assertGreater(training[-1].history["global_count"], 0)
        self.assertTrue(all(example.history["global_count"] == 0 for example in test))

    def test_fold_history_does_not_leak_contemporaneous_outcomes(self):
        rows = [normalized_row(index) for index in range(4)]
        common_start = "2024-01-01T00:00:00Z"
        common_end = "2024-01-01T00:05:00Z"
        simultaneous = [
            NormalizedObservation.from_dict(
                {**row.as_dict(), "start": common_start, "end": common_end}
            )
            for row in rows[:3]
        ]
        later = NormalizedObservation.from_dict(
            {
                **rows[3].as_dict(),
                "start": "2024-01-01T01:00:00Z",
                "end": "2024-01-01T01:05:00Z",
            }
        )
        training, _ = fold_examples(simultaneous + [later], [], "signal_present")
        self.assertEqual([row.history["global_count"] for row in training[:3]], [0, 0, 0])
        self.assertEqual(training[3].history["global_count"], 3)

    def test_fold_history_waits_until_an_observation_has_ended(self):
        rows = [normalized_row(index) for index in range(3)]
        overlapping = replace(rows[0], end=rows[0].start + timedelta(hours=2))
        training, _ = fold_examples([overlapping, rows[1], rows[2]], [], "signal_present")
        self.assertEqual(training[0].history["global_count"], 0)
        self.assertEqual(training[1].history["global_count"], 0)
        self.assertEqual(training[2].history["global_count"], 1)

    def test_temporal_splits_never_divide_equal_timestamps(self):
        rows = [normalized_row(index) for index in range(60)]
        common_start = "2024-01-03T00:00:00Z"
        common_end = "2024-01-03T00:05:00Z"
        for index in range(46, 51):
            rows[index] = NormalizedObservation.from_dict(
                {
                    **rows[index].as_dict(),
                    "start": common_start,
                    "end": common_end,
                }
            )
        result = evaluate_temporal_holdout(
            rows,
            task="signal_present",
            test_fraction=0.2,
            regularization_grid=(0.1, 1.0),
        )
        self.assertLess(result.train_end, result.test_start)
        self.assertEqual(result.train_count, 46)
        self.assertEqual(result.test_count, 14)

        transfer = evaluate_leave_one_group_out(
            rows,
            task="signal_present",
            group="norad_id",
            regularization_grid=(0.1,),
            temporal_test_fraction=0.2,
        )
        predicted_ids = {record.observation_id for record in transfer}
        self.assertEqual(predicted_ids, {row.observation_id for row in rows[46:]})

    def test_tle_pass_match_rejects_an_adjacent_orbit(self):
        row = normalized_row(0)

        def window(offset: timedelta) -> PassWindow:
            culmination = row.start + offset
            sample = OrbitSample(culmination, 30, 90, 700, 0, 0, 0)
            return PassWindow(
                norad_id=row.norad_id,
                station_id=str(row.station_id),
                start=culmination - timedelta(minutes=2),
                end=culmination + timedelta(minutes=2),
                max_elevation_deg=30,
                culmination_at=culmination,
                min_range_km=700,
                max_abs_doppler_hz=None,
                samples=(sample,),
                tle_fingerprint=row.tle_fingerprint,
            )

        nearby = window(timedelta(minutes=10))
        adjacent = window(timedelta(minutes=45))
        self.assertIs(
            _nearest((nearby,), row, tolerance=DEFAULT_TLE_MATCH_TOLERANCE),
            nearby,
        )
        self.assertIsNone(
            _nearest((adjacent,), row, tolerance=DEFAULT_TLE_MATCH_TOLERANCE)
        )

    def test_temporal_and_loso_evaluation_emit_aligned_models(self):
        rows = [normalized_row(index) for index in range(100)]
        result = evaluate_temporal_holdout(
            rows,
            task="signal_present",
            test_fraction=0.2,
            regularization_grid=(0.01, 0.1),
        )
        self.assertEqual(result.test_count, 20)
        self.assertEqual(len(result.predictions), 80)
        self.assertEqual(set(result.metrics), {"global_rate", "group_rate", "geometry_logit", "full_logit"})
        self.assertLess(result.metrics["geometry_logit"].brier, result.metrics["global_rate"].brier)
        for model, reported in result.metrics.items():
            selected = [
                record for record in result.predictions if record.model == model
            ]
            recomputed = _recompute_probability_metrics(
                [record.outcome for record in selected],
                [record.probability for record in selected],
            )
            for name, expected in reported.as_dict().items():
                actual = recomputed[name]
                if expected is None:
                    self.assertIsNone(actual)
                else:
                    self.assertAlmostEqual(float(actual), float(expected), places=10)
        loso = evaluate_leave_one_group_out(
            rows,
            task="signal_present",
            group="norad_id",
            regularization_grid=(0.1,),
        )
        self.assertEqual(len(loso), 80)

    def test_predeclared_cutoff_overrides_row_fraction_for_all_temporal_splits(self):
        rows = [normalized_row(index) for index in range(100)]
        cutoff = rows[70].start
        temporal = evaluate_temporal_holdout(
            rows,
            task="signal_present",
            test_fraction=0.2,
            regularization_grid=(0.1,),
            cutoff=cutoff,
        )
        self.assertEqual(temporal.train_count, 70)
        self.assertEqual(temporal.test_count, 30)
        self.assertEqual(
            {record.fold for record in temporal.predictions},
            {"predeclared-final-panel"},
        )
        transfer = evaluate_leave_one_group_out(
            rows,
            task="signal_present",
            group="norad_id",
            regularization_grid=(0.1,),
            temporal_test_fraction=0.2,
            cutoff=cutoff,
        )
        self.assertEqual(
            {record.observation_id for record in transfer},
            {row.observation_id for row in rows[70:]},
        )

    def test_cluster_bootstrap_is_deterministic_and_paired(self):
        rows = [normalized_row(index) for index in range(100)]
        result = evaluate_temporal_holdout(
            rows,
            task="signal_present",
            test_fraction=0.2,
            regularization_grid=(0.1,),
        )
        first = paired_cluster_bootstrap_brier(
            result.predictions,
            model="geometry_logit",
            replicates=50,
            seed=7,
        )
        second = paired_cluster_bootstrap_brier(
            result.predictions,
            model="geometry_logit",
            replicates=50,
            seed=7,
        )
        self.assertEqual(first.replicates, second.replicates)
        self.assertEqual(len(first.replicates), 50)

    def test_useful_effect_is_disabled_below_five_evaluated_clusters(self):
        predictions = []
        for observation_id in range(10):
            outcome = observation_id % 2
            for model in (
                "global_rate",
                "group_rate",
                "geometry_logit",
                "full_logit",
            ):
                probability = (
                    0.5
                    if model == "global_rate"
                    else 0.9
                    if outcome
                    else 0.1
                )
                predictions.append(
                    PredictionRecord(
                        observation_id=observation_id,
                        task="signal_present",
                        split="temporal",
                        fold="holdout",
                        model=model,
                        outcome=outcome,
                        probability=probability,
                        norad_id=1,
                        station_id=2,
                        start=f"2024-01-01T{observation_id:02d}:00:00Z",
                    )
                )
        with tempfile.TemporaryDirectory() as directory:
            summary = _comparison_bootstraps(
                predictions,
                cluster="norad_id",
                config=publication_config(),
                output_dir=Path(directory),
                task="signal_present",
                split="temporal",
            )
        self.assertEqual(summary["full_logit"]["cluster_count"], 1)
        self.assertGreater(
            summary["full_logit"]["relative_brier_reduction"], 0.05
        )
        self.assertFalse(
            summary["full_logit"]["passes_predeclared_useful_effect"]
        )

    def test_scheduling_replay_uses_identical_conflicts_and_oracle_bound(self):
        rows = [normalized_row(index) for index in range(8)]
        # Force all rows into two simultaneous receiver conflicts.
        rows = [
            NormalizedObservation.from_dict(
                {
                    **row.as_dict(),
                    "start": "2024-01-01T00:00:00Z",
                    "end": "2024-01-01T00:05:00Z",
                    "station_id": 1,
                    "signal_present": int(index in {2, 6}),
                }
            )
            for index, row in enumerate(rows)
        ]
        predictions = tuple(
            PredictionRecord(
                observation_id=row.observation_id,
                task="signal_present",
                split="temporal",
                fold="test",
                model="full_logit",
                outcome=int(row.signal_present),
                probability=(0.9 if index == 2 else 0.1),
                norad_id=row.norad_id,
                station_id=row.station_id,
                start=row.start.isoformat(),
            )
            for index, row in enumerate(rows)
        )
        replay = scheduling_replay(rows, predictions)
        self.assertTrue(replay.informative_for_scheduler_comparison)
        self.assertEqual(replay.natural_receiver_conflict_pairs, 28)
        self.assertEqual(replay.metrics["probability_milp"].realized_successful_samples, 1)
        self.assertEqual(replay.metrics["oracle_milp_upper_bound"].realized_successful_samples, 1)
        bootstrap = paired_day_bootstrap_replay(
            replay.assignments,
            baseline="chronological",
            replicates=20,
            seed=3,
        )
        self.assertEqual(len(bootstrap["replicates"]), 20)

    def test_scheduling_replay_can_optimize_end_to_end_packet_reception(self):
        rows = [normalized_row(index) for index in range(8)]
        rows = [
            NormalizedObservation.from_dict(
                {
                    **row.as_dict(),
                    "start": "2024-01-01T00:00:00Z",
                    "end": "2024-01-01T00:05:00Z",
                    "station_id": 1,
                    "signal_present": 1,
                    "decode_success_given_signal": int(index == 6),
                }
            )
            for index, row in enumerate(rows)
        ]
        predictions = tuple(
            ComposedPredictionRecord(
                observation_id=row.observation_id,
                task="reception_success",
                split="temporal-composed",
                fold="test",
                model="full_logit",
                outcome=int(index == 6),
                probability=(0.9 if index == 6 else 0.1),
                signal_probability=(0.95 if index == 6 else 0.5),
                decode_probability_given_signal=(
                    0.9 / 0.95 if index == 6 else 0.2
                ),
                norad_id=row.norad_id,
                station_id=row.station_id,
                start=row.start.isoformat(),
            )
            for index, row in enumerate(rows)
        )
        replay = scheduling_replay(
            rows,
            predictions,
            outcome_task="reception_success",
        )
        self.assertTrue(replay.informative_for_scheduler_comparison)
        self.assertEqual(
            replay.metrics["probability_milp"].realized_successful_samples,
            1,
        )
        self.assertEqual(
            replay.metrics["oracle_milp_upper_bound"].realized_successful_samples,
            1,
        )

    def test_tle_drift_summary_applies_frozen_thresholds(self):
        record = TleDriftRecord(
            norad_id=1,
            station_id=2,
            observation_id=3,
            older_fingerprint="a",
            reference_fingerprint="b",
            older_epoch="2024-01-01T00:00:00Z",
            reference_epoch="2024-01-01T01:00:00Z",
            reference_window_start="2024-01-01T01:10:00Z",
            reference_window_end="2024-01-01T01:20:00Z",
            older_window_start="2024-01-01T01:09:40Z",
            older_window_end="2024-01-01T01:19:40Z",
            window_added=False,
            window_removed=False,
            start_shift_seconds=20,
            end_shift_seconds=20,
            duration_shift_seconds=0,
            reference_lead_from_tle_epoch_seconds=600,
        )
        summary = summarize_tle_drift([record])
        self.assertEqual(summary["thresholds"]["15"]["material_transition_count"], 1)
        self.assertEqual(summary["thresholds"]["30"]["material_transition_count"], 0)
        self.assertEqual(
            summary["thresholds"]["15"]["transitions_inside_freeze_proxy"]["15"],
            1,
        )

    def test_tle_drift_summary_counts_unmatched_both_as_indeterminate(self):
        record = TleDriftRecord(
            norad_id=1,
            station_id=2,
            observation_id=3,
            older_fingerprint="a",
            reference_fingerprint="b",
            older_epoch="2024-01-01T00:00:00Z",
            reference_epoch="2024-01-01T01:00:00Z",
            reference_window_start=None,
            reference_window_end=None,
            older_window_start=None,
            older_window_end=None,
            window_added=False,
            window_removed=False,
            start_shift_seconds=None,
            end_shift_seconds=None,
            duration_shift_seconds=None,
            reference_lead_from_tle_epoch_seconds=None,
            indeterminate_unmatched_both=True,
        )
        summary = summarize_tle_drift([record])
        self.assertEqual(summary["indeterminate_unmatched_both_count"], 1)
        self.assertEqual(
            summary["thresholds"]["15"]["material_transition_count"], 0
        )

    def test_tle_drift_records_propagation_failure_as_indeterminate(self):
        class FailedPredictor:
            def predict(self, *_args, **_kwargs):
                raise OrbitPredictionError("SGP4 failed with error code 6")

        first = normalized_row(0)
        second = NormalizedObservation(
            **{
                **asdict(normalized_row(1)),
                "norad_id": first.norad_id,
                "tle_fingerprint": "e" * 64,
            }
        )
        records = audit_successive_tle_drift(
            [first, second], predictor=FailedPredictor()  # type: ignore[arg-type]
        )
        self.assertEqual(len(records), 1)
        self.assertIn("error code 6", records[0].propagation_error)
        summary = summarize_tle_drift(records)
        self.assertEqual(summary["propagation_error_count"], 1)
        self.assertEqual(summary["paired_window_count"], 0)

    def test_independent_audit_recomputes_dataset_hashes_and_counts(self):
        import hashlib

        row = normalized_row(2)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dataset = root / "dataset.jsonl"
            dataset.write_text(json.dumps(row.as_dict(), sort_keys=True) + "\n")
            digest = hashlib.sha256(dataset.read_bytes()).hexdigest()
            dataset_manifest = root / "dataset-manifest.json"
            dataset_manifest.write_text(
                json.dumps(
                    {
                        "study_id": "fixture",
                        "config_sha256": "0" * 64,
                        "dataset_sha256": digest,
                        "known_signal_rows": 1,
                        "known_signal_positive_rows": 1,
                        "conditional_decode_rows": 1,
                        "conditional_decode_positive_rows": int(
                            row.decode_success_given_signal == 1
                        ),
                    }
                )
            )
            evaluation = root / "evaluation.json"
            evaluation.write_text(
                json.dumps(
                    {
                        "study_id": "fixture",
                        "config_sha256": "0" * 64,
                        "dataset_sha256": digest,
                        "tasks": {},
                    }
                )
            )
            audit = audit_publication_artifacts(
                dataset_path=dataset,
                dataset_manifest_path=dataset_manifest,
                evaluation_path=evaluation,
            )
        self.assertTrue(audit["passed"])
        self.assertEqual(audit["failed_check_count"], 0)

    def test_independent_audit_recomputes_bootstrap_quantiles(self):
        import hashlib
        import math

        row = normalized_row(2)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dataset = root / "dataset.jsonl"
            dataset.write_text(json.dumps(row.as_dict(), sort_keys=True) + "\n")
            dataset_digest = hashlib.sha256(dataset.read_bytes()).hexdigest()
            dataset_manifest = root / "dataset-manifest.json"
            dataset_manifest.write_text(
                json.dumps(
                    {
                        "study_id": "fixture",
                        "config_sha256": "0" * 64,
                        "dataset_sha256": dataset_digest,
                        "known_signal_rows": 1,
                        "known_signal_positive_rows": 1,
                        "conditional_decode_rows": 1,
                        "conditional_decode_positive_rows": int(
                            row.decode_success_given_signal == 1
                        ),
                    }
                )
            )
            predictions = root / "predictions.jsonl"
            prediction_rows = [
                {
                    "observation_id": row.observation_id,
                    "task": "signal_present",
                    "split": "temporal",
                    "fold": "final-20-percent",
                    "model": model,
                    "outcome": 1,
                    "probability": 0.5,
                    "norad_id": row.norad_id,
                    "station_id": row.station_id,
                    "start": row.start.isoformat().replace("+00:00", "Z"),
                }
                for model in (
                    "global_rate",
                    "group_rate",
                    "geometry_logit",
                    "full_logit",
                )
            ]
            predictions.write_text(
                "".join(json.dumps(item) + "\n" for item in prediction_rows)
            )
            draws = [0.0, 0.0, 0.0, 0.0]
            bootstrap = root / "bootstrap.json"
            bootstrap_payload = {
                "schema_version": "paired-cluster-bootstrap-v1",
                "cluster": "norad_id",
                "model": "full_logit",
                "baseline": "global_rate",
                "aligned_prediction_count": 1,
                "cluster_count": 1,
                "estimate": 0.0,
                "lower_95": 0.0,
                "upper_95": 0.0,
                "relative_brier_reduction": 0.0,
                "replicates": draws,
            }
            bootstrap.write_text(json.dumps(bootstrap_payload))
            metric = {
                "count": 1,
                "positives": 1,
                "brier": 0.25,
                "log_loss": -math.log(0.5),
                "auroc": None,
                "calibration_intercept": None,
                "calibration_slope": None,
                "expected_calibration_error": 0.5,
                "interval_coverage_90": None,
            }
            comparison = {
                key: value
                for key, value in bootstrap_payload.items()
                if key not in {"schema_version", "replicates"}
            }
            comparison.update(
                {
                    "replicate_count": 4,
                    "artifact_path": str(bootstrap),
                    "artifact_sha256": hashlib.sha256(
                        bootstrap.read_bytes()
                    ).hexdigest(),
                }
            )
            evaluation = root / "evaluation.json"
            evaluation.write_text(
                json.dumps(
                    {
                        "study_id": "fixture",
                        "config_sha256": "0" * 64,
                        "dataset_sha256": dataset_digest,
                        "random_seed": 3,
                        "bootstrap_replicates": 4,
                        "tasks": {
                            "signal_present": {
                                "status": "evaluated",
                                "predictions_path": str(predictions),
                                "predictions_sha256": hashlib.sha256(
                                    predictions.read_bytes()
                                ).hexdigest(),
                                "temporal": {
                                    "metrics": {
                                        "global_rate": metric,
                                        "group_rate": metric,
                                        "geometry_logit": metric,
                                        "full_logit": metric,
                                    },
                                    "paired_bootstrap_vs_global_rate": {
                                        "full_logit": comparison
                                    },
                                },
                            }
                        },
                    }
                )
            )
            audit = audit_publication_artifacts(
                dataset_path=dataset,
                dataset_manifest_path=dataset_manifest,
                evaluation_path=evaluation,
            )
            prediction_rows[0]["outcome"] = 0
            predictions.write_text(
                "".join(json.dumps(item) + "\n" for item in prediction_rows)
            )
            evaluation_payload = json.loads(evaluation.read_text())
            evaluation_payload["tasks"]["signal_present"][
                "predictions_sha256"
            ] = hashlib.sha256(predictions.read_bytes()).hexdigest()
            evaluation.write_text(json.dumps(evaluation_payload))
            tampered = audit_publication_artifacts(
                dataset_path=dataset,
                dataset_manifest_path=dataset_manifest,
                evaluation_path=evaluation,
            )
        self.assertTrue(audit["passed"])
        self.assertTrue(
            any(
                check["name"].endswith("bootstrap_lower_95")
                for check in audit["checks"]
            )
        )
        self.assertTrue(
            any(
                check["name"].endswith("bootstrap_draws_recomputed")
                for check in audit["checks"]
            )
        )
        self.assertTrue(
            any(
                check["name"].endswith("predictions_match_normalized_dataset")
                and not check["passed"]
                for check in tampered["checks"]
            )
        )

    def test_synthetic_scheduler_benchmark_matches_independent_dynamic_program(self):
        result = run_scheduler_benchmark(
            instances=4,
            candidates_per_instance=15,
            multi_asset_instances=4,
            multi_asset_candidates_per_instance=8,
            seed=5,
        )
        self.assertEqual(result.milp_exact_match_count, 4)
        self.assertEqual(result.multi_asset_milp_exact_match_count, 4)
        self.assertGreaterEqual(result.chronological_mean_relative_regret, 0)
        self.assertGreaterEqual(result.probability_greedy_mean_relative_regret, 0)

    def test_month_like_robustness_plan_is_deterministic_and_conflict_free(self):
        first = build_scheduler_robustness_plan(
            days=3, candidates_per_day=12, seed=9
        )
        second = build_scheduler_robustness_plan(
            days=3, candidates_per_day=12, seed=9
        )
        self.assertEqual(first.canonical_fingerprint(), second.canonical_fingerprint())
        self.assertGreater(len(first.assignments), 0)
        for index, left in enumerate(first.assignments):
            for right in first.assignments[index + 1 :]:
                self.assertTrue(left.end <= right.start or right.end <= left.start)

    def test_independent_audit_recomputes_monte_carlo_draws_and_detects_tamper(self):
        plan = build_scheduler_robustness_plan(
            days=2, candidates_per_day=8, seed=4
        )
        assignments = plan_document(plan)["assignments"]
        self.assertIsInstance(assignments, list)
        expected = sum(item.expected_unique_samples for item in plan.assignments)
        independent = asdict(simulate_plan_yield(plan, trials=30, seed=7))
        scenario = CorrelatedRiskScenario(
            station_day_availability=0.9,
            satellite_day_transmit_availability=0.8,
            severe_environment_probability=0.2,
            severe_environment_success_multiplier=0.5,
        )
        correlated = asdict(
            simulate_plan_yield_correlated(
                plan, scenario=scenario, trials=30, seed=7
            )
        )
        checks = []
        _audit_simulation(
            checks,
            name="independent",
            simulation=independent,
            assignments=assignments,
            nominal_expected=expected,
            expected_trials=30,
            expected_seed=7,
        )
        _audit_simulation(
            checks,
            name="correlated",
            simulation=correlated,
            assignments=assignments,
            nominal_expected=expected,
            expected_trials=30,
            expected_seed=7,
        )
        self.assertTrue(all(check.passed for check in checks))

        tampered = dict(correlated)
        tampered["mean_unique_samples"] = float(
            tampered["mean_unique_samples"]
        ) + 1.0
        tamper_checks = []
        _audit_simulation(
            tamper_checks,
            name="correlated",
            simulation=tampered,
            assignments=assignments,
            nominal_expected=expected,
            expected_trials=30,
            expected_seed=7,
        )
        self.assertTrue(
            any(
                check.name.endswith("recomputed_mean_unique_samples")
                and not check.passed
                for check in tamper_checks
            )
        )

    def test_publication_audit_enforces_frozen_risk_scenarios(self):
        plan = build_scheduler_robustness_plan(
            days=1, candidates_per_day=6, seed=11
        )
        mild = CorrelatedRiskScenario(
            station_day_availability=0.98,
            satellite_day_transmit_availability=0.95,
            severe_environment_probability=0.10,
            severe_environment_success_multiplier=0.70,
        )
        stress = CorrelatedRiskScenario(
            station_day_availability=0.90,
            satellite_day_transmit_availability=0.85,
            severe_environment_probability=0.25,
            severe_environment_success_multiplier=0.50,
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dataset = root / "dataset.jsonl"
            dataset.write_text("")
            dataset_digest = hashlib.sha256(dataset.read_bytes()).hexdigest()
            dataset_manifest = root / "dataset-manifest.json"
            dataset_manifest.write_text(
                json.dumps(
                    {
                        "study_id": "fixture",
                        "config_sha256": "0" * 64,
                        "dataset_sha256": dataset_digest,
                        "known_signal_rows": 0,
                        "known_signal_positive_rows": 0,
                        "conditional_decode_rows": 0,
                        "conditional_decode_positive_rows": 0,
                    }
                )
            )
            plan_path = root / "plan.json"
            plan_path.write_text(json.dumps(plan_document(plan)))
            risk = {
                "plan_path": str(plan_path),
                "plan_sha256": hashlib.sha256(plan_path.read_bytes()).hexdigest(),
                "plan_canonical_fingerprint": plan.canonical_fingerprint(),
                "selected_opportunities": len(plan.assignments),
                "solver_status": "optimal",
                "nominal_expected_samples": sum(
                    item.expected_unique_samples for item in plan.assignments
                ),
                "independent": asdict(
                    simulate_plan_yield(plan, trials=20, seed=7538)
                ),
                "scenarios": {
                    "mild_correlated": asdict(
                        simulate_plan_yield_correlated(
                            plan, scenario=mild, trials=20, seed=7538
                        )
                    ),
                    "stress_correlated": asdict(
                        simulate_plan_yield_correlated(
                            plan, scenario=stress, trials=20, seed=7538
                        )
                    ),
                },
            }
            evaluation = root / "evaluation.json"
            evaluation_payload = {
                "study_id": "fixture",
                "config_sha256": "0" * 64,
                "dataset_sha256": dataset_digest,
                "random_seed": 7538,
                "bootstrap_replicates": 20,
                "tasks": {},
                "correlated_risk_sensitivity": risk,
            }
            evaluation.write_text(json.dumps(evaluation_payload))
            audit = audit_publication_artifacts(
                dataset_path=dataset,
                dataset_manifest_path=dataset_manifest,
                evaluation_path=evaluation,
            )
            self.assertTrue(audit["passed"])

            risk["scenarios"]["mild_correlated"]["scenario"][
                "station_day_availability"
            ] = 0.5
            evaluation.write_text(json.dumps(evaluation_payload))
            tampered = audit_publication_artifacts(
                dataset_path=dataset,
                dataset_manifest_path=dataset_manifest,
                evaluation_path=evaluation,
            )
        self.assertFalse(tampered["passed"])
        self.assertTrue(
            any(
                check["name"] == "risk.mild_correlated.frozen_scenario"
                and not check["passed"]
                for check in tampered["checks"]
            )
        )

    def test_full_synthetic_evaluation_is_independently_auditable(self):
        rows = [normalized_row(index) for index in range(80)]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_path = root / "config.json"
            config_payload = {
                "schema_version": "observation-planning-publication-config-v1",
                "study_id": "synthetic-integration-fixture",
                "frozen_at": "2024-03-02T00:00:00Z",
                "start": "2024-01-01T00:00:00Z",
                "end": "2024-03-01T00:00:00Z",
                "targets": [
                    {
                        "norad_id": norad_id,
                        "selection_transmitter_uuid": chr(65 + offset) * 22,
                        "selection_mode": "FSK",
                        "selection_total_count": 100,
                        "selection_good_count": 50,
                        "selection_bad_count": 30,
                    }
                    for offset, norad_id in enumerate(range(100, 105))
                ],
                "geometry_step_seconds": 30,
                "minimum_chunk_hours": 6,
                "max_response_bytes": 5000,
                "random_seed": 7538,
                "bootstrap_replicates": 8,
                "temporal_test_fraction": 0.2,
                "regularization_grid": [0.1, 1.0],
                "user_agent": "synthetic-study/contact",
                "external_validation_norad_ids": [103, 104],
                "external_station_holdout_fraction": 0.2,
                "minimum_external_test_rows": 1,
            }
            config_path.write_text(json.dumps(config_payload))
            dataset_path = root / "dataset.jsonl"
            write_normalized_jsonl(dataset_path, rows)
            dataset_sha = hashlib.sha256(dataset_path.read_bytes()).hexdigest()
            config_sha = hashlib.sha256(config_path.read_bytes()).hexdigest()
            dataset_manifest_path = root / "dataset-manifest.json"
            dataset_manifest_path.write_text(
                json.dumps(
                    {
                        "study_id": config_payload["study_id"],
                        "config_path": str(config_path),
                        "config_sha256": config_sha,
                        "dataset_sha256": dataset_sha,
                        "known_signal_rows": len(rows),
                        "known_signal_positive_rows": sum(
                            row.signal_present == 1 for row in rows
                        ),
                        "conditional_decode_rows": sum(
                            row.decode_success_given_signal in {0, 1} for row in rows
                        ),
                        "conditional_decode_positive_rows": sum(
                            row.decode_success_given_signal == 1 for row in rows
                        ),
                    }
                )
            )
            with self.assertRaisesRegex(
                DatasetIntegrityError, "performance evaluation is prohibited"
            ):
                evaluate_publication_dataset(
                    config_path, dataset_path, output_dir=root / "rejected-analysis"
                )
            evaluation = evaluate_publication_dataset(
                config_path,
                dataset_path,
                output_dir=root / "analysis",
                enforce_publication_count_gate=False,
            )
            audit = audit_publication_artifacts(
                dataset_path=dataset_path,
                dataset_manifest_path=dataset_manifest_path,
                evaluation_path=root / "analysis" / "evaluation.json",
            )
            covariate_predictions_path = Path(
                evaluation["tasks"]["signal_present"]["covariate_ablation"][
                    "predictions_path"
                ]
            )
            covariate_predictions_body = covariate_predictions_path.read_text()
            covariate_prediction_rows = [
                json.loads(line)
                for line in covariate_predictions_body.splitlines()
                if line
            ]
            covariate_prediction_rows[0]["probability"] = 0.999
            covariate_predictions_path.write_text(
                "".join(
                    json.dumps(row) + "\n" for row in covariate_prediction_rows
                )
            )
            tampered_covariate_audit = audit_publication_artifacts(
                dataset_path=dataset_path,
                dataset_manifest_path=dataset_manifest_path,
                evaluation_path=root / "analysis" / "evaluation.json",
            )
            covariate_predictions_path.write_text(covariate_predictions_body)
            composed_predictions_path = Path(
                evaluation["composed_reception"]["predictions_path"]
            )
            composed_predictions_body = composed_predictions_path.read_text()
            composed_prediction_rows = [
                json.loads(line)
                for line in composed_predictions_body.splitlines()
                if line
            ]
            composed_prediction_rows[0]["signal_probability"] = 0.999
            composed_predictions_path.write_text(
                "".join(
                    json.dumps(row) + "\n" for row in composed_prediction_rows
                )
            )
            tampered_composed_audit = audit_publication_artifacts(
                dataset_path=dataset_path,
                dataset_manifest_path=dataset_manifest_path,
                evaluation_path=root / "analysis" / "evaluation.json",
            )
            composed_predictions_path.write_text(composed_predictions_body)
            composed_replay_path = Path(
                evaluation["composed_reception"]["scheduling_replay"][
                    "assignments_path"
                ]
            )
            composed_replay_body = composed_replay_path.read_text()
            composed_replay_rows = [
                json.loads(line)
                for line in composed_replay_body.splitlines()
                if line
            ]
            composed_replay_rows[0]["predicted_probability"] = 0.999
            composed_replay_path.write_text(
                "".join(json.dumps(row) + "\n" for row in composed_replay_rows)
            )
            tampered_composed_replay_audit = audit_publication_artifacts(
                dataset_path=dataset_path,
                dataset_manifest_path=dataset_manifest_path,
                evaluation_path=root / "analysis" / "evaluation.json",
            )
            composed_replay_path.write_text(composed_replay_body)
            plan = json.loads(
                Path(
                    evaluation["correlated_risk_sensitivity"]["plan_path"]
                ).read_text()
            )
            evaluation_path = root / "analysis" / "evaluation.json"
            tampered_payload = json.loads(evaluation_path.read_text())
            tampered_payload["tasks"]["signal_present"]["external_validation"][
                "satellite"
            ]["evaluated_group_count"] = 1
            evaluation_path.write_text(json.dumps(tampered_payload))
            tampered_audit = audit_publication_artifacts(
                dataset_path=dataset_path,
                dataset_manifest_path=dataset_manifest_path,
                evaluation_path=evaluation_path,
            )
        self.assertTrue(audit["passed"])
        self.assertEqual(
            len({check["name"] for check in audit["checks"]}),
            audit["check_count"],
        )
        self.assertFalse(tampered_covariate_audit["passed"])
        self.assertTrue(
            any(
                check["name"]
                == "signal_present.covariate_ablation.predictions_sha"
                and not check["passed"]
                for check in tampered_covariate_audit["checks"]
            )
        )
        self.assertFalse(tampered_composed_replay_audit["passed"])
        self.assertTrue(
            any(
                check["name"]
                == "reception_success.scheduling.assignments_match_dataset_predictions"
                and not check["passed"]
                for check in tampered_composed_replay_audit["checks"]
            )
        )
        self.assertFalse(tampered_composed_audit["passed"])
        self.assertTrue(
            any(
                check["name"]
                == "reception_success.temporal.probability_factors_multiply_exactly"
                and not check["passed"]
                for check in tampered_composed_audit["checks"]
            )
        )
        self.assertGreater(audit["check_count"], 800)
        self.assertEqual(
            evaluation["composed_reception"]["scheduling_replay"]["outcome_task"],
            "reception_success",
        )
        self.assertEqual(
            evaluation["composed_reception"]["scheduling_replay"][
                "candidate_count"
            ],
            evaluation["composed_reception"]["temporal"]["test_count"],
        )
        self.assertEqual(plan["schema_version"], "observation-plan-v2")
        for task in ("signal_present", "decode_success_given_signal"):
            for label in ("satellite", "station"):
                split = evaluation["tasks"][task]["external_validation"][label]
                self.assertEqual(split["status"], "evaluated")
                expected_groups = min(5, len(split["held_out_group_ids"]))
                self.assertEqual(split["minimum_evaluated_groups"], expected_groups)
                self.assertEqual(split["evaluated_group_count"], expected_groups)
                self.assertGreaterEqual(
                    split["test_observation_rows"], expected_groups
                )
                self.assertEqual(
                    split["prediction_rows"],
                    4 * split["test_observation_rows"],
                )
                self.assertEqual(
                    set(split["evaluated_group_ids"]),
                    set(split["held_out_group_ids"]),
                )
        self.assertFalse(tampered_audit["passed"])
        self.assertTrue(
            any(
                check["name"]
                == "signal_present.external_satellite.group_coverage"
                and not check["passed"]
                for check in tampered_audit["checks"]
            )
        )

    def test_publication_figures_are_dependency_free_svg(self):
        rows = [normalized_row(index) for index in range(50)]
        result = evaluate_temporal_holdout(
            rows,
            task="signal_present",
            test_fraction=0.2,
            regularization_grid=(0.1,),
        )
        composed_result = evaluate_composed_reception_temporal_holdout(
            rows,
            test_fraction=0.2,
            regularization_grid=(0.1,),
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            predictions = root / "predictions.jsonl"
            predictions.write_text(
                "".join(
                    json.dumps(record.as_dict()) + "\n" for record in result.predictions
                )
            )
            composed_predictions = root / "composed-predictions.jsonl"
            composed_predictions.write_text(
                "".join(
                    json.dumps(record.as_dict()) + "\n"
                    for record in composed_result.predictions
                )
            )
            evaluation = root / "evaluation.json"
            evaluation.write_text(
                json.dumps(
                    {
                        "tasks": {
                            "signal_present": {
                                "status": "evaluated",
                                "predictions_path": str(predictions),
                                "temporal": {
                                    "metrics": {
                                        name: metric.as_dict()
                                        for name, metric in result.metrics.items()
                                    }
                                },
                            }
                        },
                        "composed_reception": {
                            "status": "evaluated",
                            "predictions_path": str(composed_predictions),
                            "temporal": {
                                "metrics": {
                                    name: metric.as_dict()
                                    for name, metric in composed_result.metrics.items()
                                }
                            },
                        },
                    }
                )
            )
            figures = write_evaluation_figures(evaluation, root / "figures")
            contents = [path.read_text() for path in figures]
        self.assertEqual(len(figures), 4)
        self.assertIn(
            "reception_success-reliability.svg",
            {path.name for path in figures},
        )
        self.assertTrue(all(content.startswith("<svg") for content in contents))

    def test_source_manifest_identity_is_deterministic(self):
        root = Path(__file__).resolve().parents[1]
        first = build_planning_source_manifest(root)
        second = build_planning_source_manifest(root)
        self.assertEqual(first["source_identity_sha256"], second["source_identity_sha256"])
        self.assertGreater(first["file_count"], 20)
        # Keep the complete preregistration lineage in the source identity:
        # three target-pool inputs, the v4c set-cover inputs, the three
        # additional artifacts introduced by the redundant v4f multicover,
        # and the zero-exposure cohort amendment plus its superseded config.
        self.assertEqual(first["selection_snapshot_count"], 13)
        paths = {item["path"] for item in first["files"]}
        self.assertIn(
            "work/observation-planning-publication-v1/network-transmitter-stats-selection-2026-09-02.json",
            paths,
        )
        self.assertIn(
            "configs/observation-planning-prospective-target-pool-v4b.json",
            paths,
        )
        self.assertIn(
            "configs/observation-planning-prospective-target-pool-v4g.json",
            paths,
        )
        self.assertIn(
            "configs/observation-planning-prospective-station-selection-v4c.json",
            paths,
        )
        self.assertIn(
            "configs/observation-planning-prospective-station-selection-v4f.json",
            paths,
        )
        self.assertIn(
            "configs/observation-planning-release-attestation-template.json",
            paths,
        )
        self.assertIn(
            "configs/observation-planning-api-contact-template.json",
            paths,
        )
        self.assertIn(
            "work/prospective-v4f/station-selection/antenna-snapshot-20260903T192000Z.json",
            paths,
        )
        self.assertIn(
            "work/prospective-v4f/station-selection/antenna-evidence-20260903T192000Z.json",
            paths,
        )

    def test_source_manifest_validates_target_pool_selection_snapshots(self):
        root = Path(__file__).resolve().parents[1]
        original_read_text = Path.read_text

        def read_text(path, *args, **kwargs):
            body = original_read_text(path, *args, **kwargs)
            if path.name == "observation-planning-prospective-target-pool-v4b.json":
                payload = json.loads(body)
                payload["selection_snapshots"][0]["sha256"] = "0" * 64
                return json.dumps(payload)
            return body

        with mock.patch.object(Path, "read_text", autospec=True, side_effect=read_text):
            with self.assertRaisesRegex(ValueError, "selection snapshot SHA-256"):
                build_planning_source_manifest(root)


if __name__ == "__main__":
    unittest.main()
