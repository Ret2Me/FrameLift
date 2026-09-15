import base64
import hashlib
import json
import tempfile
import unittest
from unittest.mock import patch
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from telemetry_yield.planning.cohort import (
    design_expanded_cohort,
    design_station_expansion,
)
from telemetry_yield.planning.campaign_runner import (
    CampaignRunnerError,
    _blockers,
    _evidence,
    _targets,
    _verify_runtime_source_manifest,
    reconcile_shadow_network,
    run_shadow_once,
)
from telemetry_yield.planning.audit import (
    _recompute_publication_count_requirements,
)
from telemetry_yield.planning.covariates import (
    CovariateError,
    GfzKpClient,
    HardwareRecord,
    KpRecord,
    NasaPowerHourlyClient,
    OpenMeteoForecastClient,
    WeatherRecord,
    _merged_weather_intervals,
    build_hardware_archive_from_declarations,
    enrich_rows,
    fetch_forecast_covariates,
    fetch_historical_covariates,
    read_covariate_archive,
    validate_covariate_evidence_manifest,
    write_covariate_archive,
    write_covariate_evidence_manifest,
)
from telemetry_yield.planning.dataset import (
    DatasetIntegrityError,
    NormalizedObservation,
    write_normalized_jsonl,
)
from telemetry_yield.planning.evaluation import (
    _fit_logit,
    deterministic_group_holdout,
    evaluate_covariate_ablation,
    evaluate_predeclared_group_holdout,
    fold_examples,
)
from telemetry_yield.planning.learned_probability import (
    FrozenLogitProbabilityEstimator,
    LearnedProbabilityModelError,
    build_frozen_probability_model,
)
from telemetry_yield.planning.manuscript import write_publication_results
from telemetry_yield.planning.probability import ReceptionFeatures
from telemetry_yield.planning.prospective import (
    ProspectiveError,
    append_event,
    commit_plan,
    initialize_campaign,
    prospective_report,
    read_ledger,
    record_outcomes,
)
from telemetry_yield.planning.provenance import (
    _historical_antenna_truth_audit_paths,
    write_planning_source_manifest,
)
from telemetry_yield.planning.v4_protocol import (
    validate_v4_method_contract,
    validate_v4_publication_config_contract,
)
from telemetry_yield.planning.publication import (
    _covariate_bootstraps,
    _publication_count_requirements,
)
from telemetry_yield.planning.satnogs_dataset import PublicationDatasetConfig
from telemetry_yield.planning.models import (
    GroundStation,
    ObservationPlan,
    Opportunity,
    ProbabilityEstimate,
    ReceiverResource,
)
from telemetry_yield.planning.store import plan_document
from telemetry_yield.satnogs import SatNOGSResponse


TLE1 = "1 25544U 98067A   24123.50000000  .00016717  00000+0  30210-3 0  9997"
TLE2 = "2 25544  51.6400 100.0000 0005000  50.0000 310.0000 15.50000000450003"


def normalized_row(index: int) -> NormalizedObservation:
    start = datetime(2024, 1, 1, tzinfo=UTC) + timedelta(hours=index)
    positive = int(index % 5 in {2, 3, 4})
    return NormalizedObservation(
        schema_version="satnogs-observation-features-v1",
        observation_id=index + 1,
        start=start,
        end=start + timedelta(minutes=5),
        duration_seconds=300,
        norad_id=100 + index % 5,
        station_id=200 + index % 10,
        station_name=f"station-{index % 10}",
        station_latitude_deg=52.2,
        station_longitude_deg=21.0,
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
        decode_success_given_signal=index % 2 if positive else None,
        source_status="good",
        source_waterfall_status="with-signal" if positive else "without-signal",
        client_metadata_parse_status="parsed_location_valid",
        station_location_source="satnogs-client-metadata",
        station_location_recorded_at=start,
    )


class CovariateTests(unittest.TestCase):
    def test_raw_source_evidence_manifest_binds_every_archive_response(self):
        captured = datetime(2026, 9, 3, 12, tzinfo=UTC)
        weather_body = b'{"weather":"source"}'
        kp_body = b'[["time","Kp"],["2026-09-03T12:00:00Z","2"]]'
        station_body = b'{"id":12,"antenna":[{"frequency":430000000}]}'
        weather_hash = hashlib.sha256(weather_body).hexdigest()
        kp_hash = hashlib.sha256(kp_body).hexdigest()
        station_hash = hashlib.sha256(station_body).hexdigest()
        weather = WeatherRecord(
            station_id=12,
            latitude_deg=52,
            longitude_deg=21,
            valid_at=captured,
            retrieved_at=captured,
            source="Open-Meteo Forecast API",
            source_response_sha256=weather_hash,
            air_temperature_c=10,
        )
        kp = KpRecord(
            valid_at=captured,
            retrieved_at=captured,
            kp=2,
            status="predicted",
            source="NOAA SWPC planetary K-index forecast",
            source_response_sha256=kp_hash,
        )
        hardware = HardwareRecord(
            station_id=12,
            configuration_id="station-12",
            antenna_type="yagi",
            frequency_ranges_hz=((430_000_000, 440_000_000),),
            effective_from=captured,
            known_at=captured + timedelta(minutes=1),
            source="SatNOGS Network station detail API",
            evidence_sha256=station_hash,
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cache = root / "cache"
            public_cache = cache / "open-meteo"
            public_cache.mkdir(parents=True)
            for key, body, url in (
                ("weather", weather_body, "https://api.open-meteo.com/v1/forecast"),
                (
                    "kp",
                    kp_body,
                    "https://services.swpc.noaa.gov/products/noaa-planetary-k-index-forecast.json",
                ),
            ):
                (public_cache / f"{key}.json").write_bytes(body)
                (public_cache / f"{key}.meta.json").write_text(
                    json.dumps(
                        {
                            "schema_version": "public-covariate-http-cache-v1",
                            "url": url,
                            "retrieved_at": captured.isoformat(),
                            "body_sha256": hashlib.sha256(body).hexdigest(),
                        }
                    )
                )
            # A mutable endpoint may return the same bytes on separate days.
            # The evidence writer must select the cache record whose capture
            # time is bound to this archive rather than whichever file sorts
            # last.
            (public_cache / "zz-weather-old.json").write_bytes(weather_body)
            (public_cache / "zz-weather-old.meta.json").write_text(
                json.dumps(
                    {
                        "schema_version": "public-covariate-http-cache-v1",
                        "url": "https://api.open-meteo.com/v1/forecast",
                        "retrieved_at": (
                            captured - timedelta(days=1)
                        ).isoformat(),
                        "body_sha256": weather_hash,
                    }
                )
            )
            station_cache = cache / "satnogs-stations"
            station_cache.mkdir()
            (station_cache / "station.json").write_text(
                json.dumps(
                    {
                        "url": "https://network.satnogs.org/api/stations/12/",
                        "body_base64": base64.b64encode(station_body).decode(),
                        "fetched_at": captured.isoformat(),
                    }
                )
            )
            archive = root / "forecast.json"
            evidence = root / "evidence.json"
            write_covariate_archive(
                archive, weather=(weather,), kp=(kp,), hardware=(hardware,)
            )
            result = write_covariate_evidence_manifest(archive, cache, evidence)
            verified = validate_covariate_evidence_manifest(evidence, archive)
            self.assertEqual(result["response_count"], 3)
            self.assertEqual(verified["weather_response_count"], 1)
            self.assertEqual(verified["hardware_response_count"], 1)
            tampered = json.loads(evidence.read_text())
            tampered["responses"][0]["body_base64"] = base64.b64encode(
                b"tampered"
            ).decode()
            evidence.write_text(json.dumps(tampered))
            with self.assertRaisesRegex(CovariateError, "inventory|verification"):
                validate_covariate_evidence_manifest(evidence, archive)

            url_archive = root / "forecast-url-source.json"
            url_evidence = root / "evidence-url-source.json"
            write_covariate_archive(
                url_archive,
                weather=(weather,),
                kp=(kp,),
                hardware=(
                    replace(hardware, source="https://network.satnogs.org/api/"),
                ),
            )
            url_result = write_covariate_evidence_manifest(
                url_archive, cache, url_evidence
            )
            url_verified = validate_covariate_evidence_manifest(
                url_evidence, url_archive
            )
            self.assertEqual(url_result["response_count"], 3)
            self.assertEqual(url_verified["hardware_response_count"], 1)

    def test_historical_weather_archive_retains_only_observation_hours(self):
        first_start = datetime(2024, 1, 1, 5, 15, tzinfo=UTC)
        second_start = datetime(2024, 1, 3, 6, 30, tzinfo=UTC)
        base = normalized_row(0)
        rows = (
            replace(
                base,
                start=first_start,
                end=first_start + timedelta(minutes=5),
            ),
            replace(
                base,
                observation_id=2,
                start=second_start,
                end=second_start + timedelta(minutes=5),
            ),
        )
        weather_calls = []

        def fetch(url, _headers, _timeout, _limit):
            parsed = urlparse(url)
            query = parse_qs(parsed.query)
            if "historical-forecast-api.open-meteo.com" in parsed.netloc:
                weather_calls.append(url)
                start = datetime.strptime(query["start_date"][0], "%Y-%m-%d").replace(
                    tzinfo=UTC
                )
                end = datetime.strptime(query["end_date"][0], "%Y-%m-%d").replace(
                    tzinfo=UTC
                ) + timedelta(days=1)
                timestamps = []
                cursor = start
                while cursor < end:
                    timestamps.append(cursor.strftime("%Y-%m-%dT%H:00"))
                    cursor += timedelta(hours=1)
                return json.dumps(
                    {
                        "hourly_units": {
                            "time": "iso8601",
                            "temperature_2m": "°C",
                            "relative_humidity_2m": "%",
                            "surface_pressure": "hPa",
                            "wind_speed_10m": "m/s",
                            "precipitation": "mm",
                        },
                        "hourly": {
                            "time": timestamps,
                            "temperature_2m": [1.0] * len(timestamps),
                            "relative_humidity_2m": [50.0] * len(timestamps),
                            "surface_pressure": [1000.0] * len(timestamps),
                            "wind_speed_10m": [1.0] * len(timestamps),
                            "precipitation": [0.0] * len(timestamps),
                        }
                    }
                ).encode()
            return json.dumps(
                {
                    "datetime": ["2024-01-01T00:00:00Z"],
                    "Kp": [2.0],
                    "status": ["def"],
                }
            ).encode()

        with tempfile.TemporaryDirectory() as directory:
            weather, _kp = fetch_historical_covariates(
                rows,
                user_agent="test/contact",
                cache_dir=Path(directory),
                minimum_request_interval_seconds=0,
                weather_maximum_missing_days=1,
                fetch_bytes=fetch,
                now=lambda: datetime(2024, 2, 1, tzinfo=UTC),
            )
        self.assertEqual(len(weather_calls), 1)
        self.assertTrue(
            all(
                item.source == "Open-Meteo Historical Forecast API"
                for item in weather
            )
        )
        self.assertEqual(
            {item.valid_at for item in weather},
            {
                first_start.replace(minute=0, second=0, microsecond=0),
                second_start.replace(minute=0, second=0, microsecond=0),
            },
        )

    def test_historical_weather_never_uses_current_station_location_fallback(self):
        row = replace(
            normalized_row(0),
            client_metadata_parse_status="absent",
            station_location_source="satnogs-api-current-station",
            station_location_recorded_at=None,
        )
        weather_calls = []

        def fetch(url, *_args):
            if "open-meteo" in url:
                weather_calls.append(url)
                self.fail("fallback coordinates must not trigger a historical weather call")
            return json.dumps(
                {
                    "datetime": ["2024-01-01T00:00:00Z"],
                    "Kp": [2.0],
                    "status": ["def"],
                }
            ).encode()

        with tempfile.TemporaryDirectory() as directory:
            weather, kp = fetch_historical_covariates(
                (row,),
                user_agent="test/contact",
                cache_dir=Path(directory),
                minimum_request_interval_seconds=0,
                fetch_bytes=fetch,
                now=lambda: datetime(2024, 2, 1, tzinfo=UTC),
            )
        self.assertEqual(weather, ())
        self.assertEqual(weather_calls, [])
        self.assertEqual(len(kp), 1)

        candidate = WeatherRecord(
            station_id=row.station_id,
            latitude_deg=row.station_latitude_deg,
            longitude_deg=row.station_longitude_deg,
            valid_at=row.start,
            retrieved_at=row.start + timedelta(days=1),
            source="Open-Meteo Historical Forecast API",
            source_response_sha256="a" * 64,
            air_temperature_c=10,
        )
        enriched = enrich_rows((row,), weather=(candidate,), kp=())[0]
        self.assertIsNone(enriched.weather_valid_at)

    def test_weather_requests_merge_short_gaps_without_expanding_required_hours(self):
        hours = (
            datetime(2024, 1, 1, 5, tzinfo=UTC),
            datetime(2024, 1, 3, 6, tzinfo=UTC),
            datetime(2024, 1, 6, 7, tzinfo=UTC),
        )
        intervals = _merged_weather_intervals(hours, maximum_missing_days=1)
        self.assertEqual(
            intervals,
            (
                (
                    datetime(2024, 1, 1, tzinfo=UTC),
                    datetime(2024, 1, 4, tzinfo=UTC),
                ),
                (
                    datetime(2024, 1, 6, tzinfo=UTC),
                    datetime(2024, 1, 7, tzinfo=UTC),
                ),
            ),
        )
        self.assertEqual(_merged_weather_intervals(()), ())
        with self.assertRaisesRegex(ValueError, "maximum_missing_days"):
            _merged_weather_intervals(hours, maximum_missing_days=32)

    def test_measured_hardware_requires_evidence_hash(self):
        declaration = {
            "schema_version": "station-hardware-declarations-v1",
            "records": [
                {
                    "station_id": 12,
                    "configuration_id": "measured-v1",
                    "antenna_type": "yagi",
                    "frequency_ranges_hz": [[430_000_000, 440_000_000]],
                    "effective_from": "2024-01-01T00:00:00Z",
                    "known_at": "2024-01-01T00:00:00Z",
                    "source": "calibration report",
                    "receive_antenna_gain_dbi": 12,
                    "system_noise_temperature_k": 300,
                }
            ],
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, output = root / "declaration.json", root / "archive.json"
            source.write_text(json.dumps(declaration))
            with self.assertRaisesRegex(ValueError, "evidence_sha256"):
                build_hardware_archive_from_declarations(source, output)
            declaration["records"][0]["evidence_sha256"] = "a" * 64
            source.write_text(json.dumps(declaration))
            archive = build_hardware_archive_from_declarations(source, output)
        self.assertEqual(len(archive["hardware"]), 1)

    def test_open_meteo_forecast_is_timestamped_at_capture(self):
        payload = {
            "hourly_units": {
                "temperature_2m": "°C",
                "relative_humidity_2m": "%",
                "surface_pressure": "hPa",
                "wind_speed_10m": "m/s",
                "precipitation": "mm",
            },
            "hourly": {
                "time": ["2026-09-04T00:00"],
                "temperature_2m": [10.0],
                "relative_humidity_2m": [60.0],
                "surface_pressure": [995.0],
                "wind_speed_10m": [3.0],
                "precipitation": [0.0],
            }
        }
        captured = datetime(2026, 9, 3, 12, tzinfo=UTC)

        def fetch_forecast(url, *_args):
            self.assertEqual(parse_qs(urlparse(url).query)["wind_speed_unit"], ["ms"])
            return json.dumps(payload).encode()

        client = OpenMeteoForecastClient(
            user_agent="test/contact",
            cache_dir=None,
            fetch_bytes=fetch_forecast,
            now=lambda: captured,
        )
        records = client.fetch(
            station_id=12,
            latitude_deg=52.2,
            longitude_deg=21,
            start=datetime(2026, 9, 4, tzinfo=UTC),
            end=datetime(2026, 9, 5, tzinfo=UTC),
        )
        self.assertEqual(records[0].retrieved_at, captured)
        self.assertEqual(records[0].valid_at, datetime(2026, 9, 4, tzinfo=UTC))
        self.assertEqual(records[0].surface_pressure_kpa, 99.5)
        self.assertEqual(records[0].wind_speed_m_s, 3.0)

    def test_each_forecast_capture_refreshes_constant_noaa_url(self):
        first_capture = datetime(2026, 9, 3, 12, tzinfo=UTC)
        second_capture = first_capture + timedelta(days=1)
        noaa_calls = 0

        def fetch_forecast(url, *_args):
            nonlocal noaa_calls
            if "kp.gfz.de" in url:
                return json.dumps(
                    {
                        "datetime": ["2026-09-05T00:00:00Z"],
                        "Kp": [0],
                        "status": ["predicted"],
                    }
                ).encode()
            if "services.swpc.noaa.gov" in url:
                noaa_calls += 1
                return json.dumps(
                    [
                        {
                            "time_tag": "2026-09-05T00:00:00",
                            "kp": noaa_calls,
                            "observed": "predicted",
                        }
                    ]
                ).encode()
            self.fail(f"unexpected forecast URL: {url}")

        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory)
            _, first_kp, _ = fetch_forecast_covariates(
                (),
                start=first_capture,
                end=datetime(2026, 9, 7, tzinfo=UTC),
                user_agent="test/contact",
                cache_dir=cache,
                fetch_bytes=fetch_forecast,
                now=lambda: first_capture,
            )
            _, second_kp, _ = fetch_forecast_covariates(
                (),
                start=first_capture,
                end=datetime(2026, 9, 7, tzinfo=UTC),
                user_agent="test/contact",
                cache_dir=cache,
                fetch_bytes=fetch_forecast,
                now=lambda: second_capture,
            )
            noaa_metadata = list((cache / "noaa-swpc-kp").glob("*.meta.json"))

        self.assertEqual(noaa_calls, 2)
        self.assertEqual(
            next(item.kp for item in first_kp if "NOAA" in item.source), 1
        )
        self.assertEqual(
            next(item.kp for item in second_kp if "NOAA" in item.source), 2
        )
        self.assertEqual(len(noaa_metadata), 2)

    def test_real_source_parsers_and_round_trip_archive(self):
        power = {
            "parameters": {
                "T2M": {"units": "C"},
                "RH2M": {"units": "%"},
                "PS": {"units": "kPa"},
                "WS10M": {"units": "m/s"},
                "PRECTOTCORR": {"units": "mm/hour"},
            },
            "properties": {
                "parameter": {
                    "T2M": {"2024010100": 2.5},
                    "RH2M": {"2024010100": 81.0},
                    "PS": {"2024010100": 100.1},
                    "WS10M": {"2024010100": 4.2},
                    "PRECTOTCORR": {"2024010100": 0.1},
                }
            }
        }

        def fetch_power(url, headers, timeout, limit):
            self.assertIn("power.larc.nasa.gov", url)
            self.assertEqual(headers["Accept"], "application/json")
            return json.dumps(power).encode()

        now = lambda: datetime(2024, 2, 1, tzinfo=UTC)
        client = NasaPowerHourlyClient(
            user_agent="test/contact", cache_dir=None, fetch_bytes=fetch_power, now=now
        )
        weather = client.fetch(
            station_id=12,
            latitude_deg=52.2,
            longitude_deg=21.0,
            start=datetime(2024, 1, 1, tzinfo=UTC),
            end=datetime(2024, 1, 2, tzinfo=UTC),
        )
        self.assertEqual(weather[0].relative_humidity_percent, 81)
        self.assertAlmostEqual(weather[0].precipitation_corrected, 0.1)
        with self.assertRaisesRegex(ValueError, "precipitation must be non-negative"):
            replace(weather[0], precipitation_corrected=-0.1)

        power["parameters"]["PRECTOTCORR"]["units"] = "mm/day"
        with self.assertRaisesRegex(
            CovariateError, "units do not match the frozen contract"
        ):
            client.fetch(
                station_id=12,
                latitude_deg=52.2,
                longitude_deg=21.0,
                start=datetime(2024, 1, 1, tzinfo=UTC),
                end=datetime(2024, 1, 2, tzinfo=UTC),
            )

        def fetch_kp(url, headers, timeout, limit):
            self.assertIn("kp.gfz.de", url)
            return json.dumps(
                {
                    "datetime": [
                        "2024-01-01T00:00:00Z",
                        "2024-01-02T00:00:00Z",
                    ],
                    "Kp": [4.333, 9.0],
                    "status": ["def", "boundary-must-be-excluded"],
                }
            ).encode()

        kp = GfzKpClient(
            user_agent="test/contact", cache_dir=None, fetch_bytes=fetch_kp, now=now
        ).fetch(
            datetime(2024, 1, 1, tzinfo=UTC),
            datetime(2024, 1, 2, tzinfo=UTC),
        )
        self.assertEqual(len(kp), 1)
        self.assertEqual(kp[0].status, "def")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "covariates.json"
            archive = write_covariate_archive(path, weather=weather, kp=kp)
            self.assertEqual(
                archive["contracts"]["weather"]["units"]["surface_pressure_kpa"],
                "kPa",
            )
            restored_weather, restored_kp, restored_hardware = read_covariate_archive(path)
            tampered = json.loads(path.read_text())
            tampered["contracts"]["weather"]["units"]["surface_pressure_kpa"] = "hPa"
            path.write_text(json.dumps(tampered))
            with self.assertRaisesRegex(CovariateError, "contract hash"):
                read_covariate_archive(path)
        self.assertEqual(restored_weather, weather)
        self.assertEqual(restored_kp, kp)
        self.assertEqual(restored_hardware, ())

    def test_hardware_is_never_backfilled_from_a_later_snapshot(self):
        row = normalized_row(0)
        weather = WeatherRecord(
            station_id=row.station_id,
            latitude_deg=row.station_latitude_deg,
            longitude_deg=row.station_longitude_deg,
            valid_at=row.start,
            retrieved_at=row.start + timedelta(days=10),
            source="NASA POWER",
            source_response_sha256="a" * 64,
            air_temperature_c=3,
        )
        future_hardware = HardwareRecord(
            station_id=row.station_id,
            configuration_id="config",
            antenna_type="cross-yagi",
            frequency_ranges_hz=((430_000_000, 440_000_000),),
            effective_from=row.start - timedelta(days=30),
            known_at=row.start + timedelta(days=1),
            source="later snapshot",
        )
        enriched = enrich_rows(
            [row], weather=[weather], kp=[], hardware=[future_hardware]
        )[0]
        self.assertEqual(enriched.weather_air_temperature_c, 3)
        self.assertIsNone(enriched.antenna_configuration_id)
        historical_hardware = replace(
            future_hardware,
            known_at=row.start - timedelta(days=1),
            source="archived station record",
        )
        enriched = enrich_rows(
            [row], weather=[weather], kp=[], hardware=[historical_hardware]
        )[0]
        self.assertEqual(enriched.antenna_configuration_id, "config")
        self.assertTrue(enriched.antenna_frequency_supported)

    def test_covariate_ablation_activates_only_collected_families(self):
        rows = [normalized_row(index) for index in range(80)]
        weather_rows = [
            replace(
                row,
                weather_air_temperature_c=float(index % 20),
                weather_valid_at=row.start.replace(minute=0, second=0, microsecond=0),
            )
            for index, row in enumerate(rows)
        ]
        result = evaluate_covariate_ablation(
            weather_rows,
            task="signal_present",
            test_fraction=0.2,
            regularization_grid=(0.1,),
        )
        self.assertIn("operational_weather_logit", result.available_families)
        self.assertNotIn("operational_hardware_logit", result.available_families)

    def test_covariate_bootstrap_is_persisted_and_content_bound(self):
        rows = [normalized_row(index) for index in range(80)]
        weather_rows = [
            replace(
                row,
                weather_air_temperature_c=float(index % 20),
                weather_valid_at=row.start.replace(minute=0, second=0, microsecond=0),
            )
            for index, row in enumerate(rows)
        ]
        result = evaluate_covariate_ablation(
            weather_rows,
            task="signal_present",
            test_fraction=0.2,
            regularization_grid=(0.1,),
        )
        root = Path(__file__).resolve().parents[1]
        config = PublicationDatasetConfig.load(
            root / "configs/observation-planning-publication-v4.json"
        )
        with tempfile.TemporaryDirectory() as directory:
            report = _covariate_bootstraps(
                result.predictions,
                candidate_models=result.available_families,
                cluster="norad_id",
                config=config,
                output_dir=Path(directory),
                task="signal_present",
            )
            weather = report["operational_weather_logit"]
            artifact = Path(weather["artifact_path"])
            self.assertEqual(weather["replicate_count"], 2_000)
            self.assertEqual(weather["cluster_count"], 5)
            self.assertTrue(artifact.is_file())
            self.assertEqual(
                weather["artifact_sha256"],
                hashlib.sha256(artifact.read_bytes()).hexdigest(),
            )
            self.assertIsInstance(
                weather["passes_outcome_blind_deployment_candidate_gate"], bool
            )


class ExternalValidationTests(unittest.TestCase):
    def test_hash_split_is_deterministic_and_external_groups_are_unseen(self):
        self.assertEqual(
            deterministic_group_holdout(range(20), fraction=0.2, seed=7),
            deterministic_group_holdout(reversed(range(20)), fraction=0.2, seed=7),
        )
        rows = [normalized_row(index) for index in range(100)]
        predictions = evaluate_predeclared_group_holdout(
            rows,
            task="signal_present",
            group="norad_id",
            held_out_values=(104,),
            regularization_grid=(0.1,),
            minimum_test_rows=1,
            cutoff=rows[70].start,
        )
        self.assertTrue(predictions)
        self.assertEqual({item.norad_id for item in predictions}, {104})
        self.assertEqual({item.split for item in predictions}, {"external-satellite"})
        self.assertTrue(all(item.start >= rows[70].start.isoformat() for item in predictions))

        with self.assertRaisesRegex(DatasetIntegrityError, "group minimum"):
            evaluate_predeclared_group_holdout(
                rows,
                task="signal_present",
                group="norad_id",
                held_out_values=(104,),
                regularization_grid=(0.1,),
                minimum_test_rows=1,
                minimum_test_groups=2,
            )


class FrozenProbabilityModelTests(unittest.TestCase):
    def test_serialized_runtime_model_matches_training_pipeline_and_rejects_tampering(self):
        rows = [
            replace(
                normalized_row(index),
                weather_air_temperature_c=float(index % 20),
                weather_relative_humidity_percent=float(40 + index % 30),
                weather_surface_pressure_kpa=99.0 + index % 4,
                weather_wind_speed_m_s=float(index % 7),
                weather_precipitation_corrected=float(index % 2),
                weather_valid_at=normalized_row(index).start,
                space_weather_kp=float(index % 8),
                space_weather_valid_at=normalized_row(index).start,
                antenna_type=f"antenna-{normalized_row(index).station_id}",
                antenna_frequency_supported=True,
                receive_antenna_gain_dbi=12.0,
                system_noise_temperature_k=300.0,
            )
            for index in range(80)
        ]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dataset = root / "dataset.jsonl"
            manifest = root / "dataset-manifest.json"
            evaluation_path = root / "evaluation.json"
            artifact = root / "probability-model.json"
            write_normalized_jsonl(dataset, rows)
            dataset_sha = hashlib.sha256(dataset.read_bytes()).hexdigest()
            manifest.write_text(
                json.dumps(
                    {
                        "schema_version": "observation-planning-normalized-dataset-manifest-v1",
                        "study_id": "frozen-model-fixture",
                        "dataset_sha256": dataset_sha,
                    }
                )
            )
            task_reports = {}
            for task in ("signal_present", "decode_success_given_signal"):
                result = evaluate_covariate_ablation(
                    rows,
                    task=task,
                    test_fraction=0.2,
                    regularization_grid=(0.1,),
                )
                predictions = root / f"{task}-predictions.jsonl"
                predictions.write_text(
                    "".join(
                        json.dumps(record.as_dict(), sort_keys=True) + "\n"
                        for record in result.predictions
                    )
                )
                task_reports[task] = {
                    "status": "evaluated",
                    "covariate_ablation": {
                        "selected_regularization": dict(
                            result.selected_regularization
                        ),
                        "metrics": {
                            model: metric.as_dict()
                            for model, metric in result.metrics.items()
                        },
                        "deployment_candidate_gate": {
                            "rule": "fixture gate",
                            "by_model": {
                                "operational_weather_logit": False,
                                "operational_hardware_logit": False,
                                "full_covariate_logit": True,
                            },
                        },
                        "predictions_path": str(predictions),
                        "predictions_sha256": hashlib.sha256(
                            predictions.read_bytes()
                        ).hexdigest(),
                    },
                }
            evaluation_path.write_text(
                json.dumps(
                    {
                        "schema_version": "observation-planning-evaluation-v1",
                        "study_id": "frozen-model-fixture",
                        "dataset_sha256": dataset_sha,
                        "tasks": task_reports,
                    }
                )
            )
            built = build_frozen_probability_model(
                dataset,
                manifest,
                evaluation_path,
                artifact,
                now=datetime(2024, 2, 1, tzinfo=UTC),
            )
            self.assertEqual(
                built["tasks"]["signal_present"]["selected_model"],
                "full_covariate_logit",
            )
            estimator = FrozenLogitProbabilityEstimator.load(
                artifact, history_dataset_path=dataset
            )
            future = replace(
                normalized_row(82),
                weather_air_temperature_c=float(82 % 20),
                weather_relative_humidity_percent=float(40 + 82 % 30),
                weather_surface_pressure_kpa=99.0 + 82 % 4,
                weather_wind_speed_m_s=float(82 % 7),
                weather_precipitation_corrected=float(82 % 2),
                weather_valid_at=normalized_row(82).start,
                space_weather_kp=float(82 % 8),
                space_weather_valid_at=normalized_row(82).start,
                antenna_type=f"antenna-{normalized_row(82).station_id}",
                antenna_frequency_supported=True,
                receive_antenna_gain_dbi=12.0,
                system_noise_temperature_k=300.0,
            )
            features = ReceptionFeatures(
                norad_id=future.norad_id,
                station_id=f"satnogs-{future.station_id}",
                resource_id="receiver",
                max_elevation_deg=future.max_elevation_deg,
                min_range_km=future.min_range_km,
                duration_seconds=future.duration_seconds,
                tle_age_hours=future.tle_age_hours,
                frequency_hz=future.frequency_hz,
                modulation=future.transmitter_mode,
                baud=future.transmitter_baud,
                satnogs_station_id=future.station_id,
                transmitter_uuid=future.transmitter_uuid,
                transmitter_status=future.transmitter_status,
                rise_azimuth_deg=future.rise_azimuth_deg,
                set_azimuth_deg=future.set_azimuth_deg,
                air_temperature_c=float(82 % 20),
                relative_humidity_percent=float(40 + 82 % 30),
                surface_pressure_kpa=99.0 + 82 % 4,
                wind_speed_m_s=float(82 % 7),
                precipitation_corrected=float(82 % 2),
                space_weather_kp=float(82 % 8),
                antenna_type=f"antenna-{future.station_id}",
                antenna_frequency_supported=True,
                receive_antenna_gain_dbi=12.0,
                system_noise_temperature_k=300.0,
            )
            estimate = estimator.estimate(
                features,
                _evidence(rows),
                transmitter_probability=1.0,
            )
            for task, actual in (
                ("signal_present", estimate.p_signal_present),
                (
                    "decode_success_given_signal",
                    estimate.p_decode_given_signal,
                ),
            ):
                training, test = fold_examples(rows, (future,), task)
                expected = float(
                    _fit_logit(training, "full", 0.1).predict(test)[0]
                )
                self.assertAlmostEqual(actual, expected, places=12)
            self.assertEqual(estimate.p_success, estimate.p_signal_present * estimate.p_decode_given_signal)
            contract = estimator.feature_use_contract(features)
            self.assertEqual(contract, estimator.feature_use_contract_template())
            self.assertIn("training_dataset_sha256", contract)
            self.assertIn(
                "air_temperature_c", contract["active_probability_features"]
            )
            self.assertIn("weather_air_temperature_c", contract["active_model_terms"])
            sparse = estimator.estimate(
                replace(
                    features,
                    frequency_hz=None,
                    modulation=None,
                    baud=None,
                    transmitter_uuid=None,
                    transmitter_status=None,
                    rise_azimuth_deg=None,
                    set_azimuth_deg=None,
                    air_temperature_c=None,
                    relative_humidity_percent=None,
                    surface_pressure_kpa=None,
                    wind_speed_m_s=None,
                    precipitation_corrected=None,
                    space_weather_kp=None,
                    antenna_type=None,
                    antenna_frequency_supported=None,
                    receive_antenna_gain_dbi=None,
                    system_noise_temperature_k=None,
                ),
                _evidence(rows),
                transmitter_probability=1.0,
            )
            self.assertGreater(len(sparse.missing_features), 0)
            self.assertIn("frequency_hz", sparse.missing_features)
            self.assertIn("baud", sparse.missing_features)
            self.assertIn("modulation", sparse.missing_features)
            self.assertIn("air_temperature_c", sparse.missing_features)
            self.assertIn("antenna_type", sparse.missing_features)
            self.assertGreaterEqual(sparse.p_success, 0.0)
            self.assertLessEqual(sparse.p_success, 1.0)
            sparse_audit = estimator.sparse_inference_audit()
            self.assertTrue(sparse_audit["passed"])
            self.assertTrue(sparse_audit["all_scientific_optional_inputs_absent"])
            self.assertTrue(sparse_audit["active_missing_inputs_visible"])
            self.assertEqual(sparse_audit["evidence_count"], 0)

            audit = root / "audit.json"
            audit.write_text(
                json.dumps(
                    {"passed": True, "check_count": 1, "failed_check_count": 0}
                )
            )
            results = write_publication_results(
                dataset_manifest_path=manifest,
                evaluation_path=evaluation_path,
                independent_audit_path=audit,
                probability_model_path=artifact,
                output_dir=root / "manuscript",
            )
            self.assertEqual(
                results["probability_model_sha256"],
                hashlib.sha256(artifact.read_bytes()).hexdigest(),
            )
            self.assertTrue(
                (root / "manuscript/table-deployment-model.csv").is_file()
            )

            different_history = root / "different-history.jsonl"
            different_history.write_text(dataset.read_text() + "\n")
            with self.assertRaisesRegex(
                LearnedProbabilityModelError, "runtime history dataset"
            ):
                FrozenLogitProbabilityEstimator.load(
                    artifact, history_dataset_path=different_history
                )

            tampered = json.loads(artifact.read_text())
            tampered["tasks"]["signal_present"]["coefficients"][0] += 1
            artifact.write_text(json.dumps(tampered))
            with self.assertRaisesRegex(
                LearnedProbabilityModelError, "payload SHA-256"
            ):
                FrozenLogitProbabilityEstimator.load(artifact)


class CohortTests(unittest.TestCase):
    def test_station_expansion_is_outcome_blind_and_covers_every_target(self):
        captured = datetime(2026, 9, 3, 18, tzinfo=UTC)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target_pool_path = root / "targets.json"
            campaign_path = root / "campaign.json"
            transmitter_path = root / "transmitters.json"
            hardware_path = root / "hardware.json"
            stations_path = root / "stations.json"
            target_pool_path.write_text(
                json.dumps(
                    {
                        "schema_version": "observation-planning-publication-config-v1",
                        "targets": [
                            {
                                "norad_id": norad_id,
                                "selection_transmitter_uuid": f"tx-{norad_id}",
                            }
                            for norad_id in (101, 102, 103)
                        ],
                    }
                )
            )
            campaign_path.write_text(
                json.dumps(
                    {
                        "schema_version": "observation-planning-prospective-config-v1",
                        "target_norad_ids": [101, 102, 103],
                        "station_ids": [10],
                    }
                )
            )
            transmitter_path.write_text(
                json.dumps(
                    [
                        {
                            "uuid": f"tx-{norad_id}",
                            "norad_cat_id": norad_id,
                            "downlink_low": frequency,
                        }
                        for norad_id, frequency in (
                            (101, 145_000_000),
                            (102, 1_700_000_000),
                            (103, 2_200_000_000),
                        )
                    ]
                )
            )
            write_covariate_archive(
                hardware_path,
                hardware=(
                    HardwareRecord(
                        station_id=10,
                        configuration_id="base",
                        antenna_type="vhf",
                        frequency_ranges_hz=((140_000_000, 150_000_000),),
                        effective_from=captured,
                        known_at=captured,
                        source="fixture",
                    ),
                ),
            )
            stations = [
                {
                    "id": 20,
                    "name": "two-band",
                    "observations": 1_200,
                    "success_rate": 1,
                    "is_connected": True,
                    "is_available": True,
                    "testing": False,
                    "antenna": [
                        {
                            "frequency": 1_600_000_000,
                            "frequency_max": 2_300_000_000,
                        }
                    ],
                },
                {
                    "id": 21,
                    "name": "high-volume-one-band",
                    "observations": 99_999,
                    "success_rate": 99,
                    "is_connected": True,
                    "is_available": True,
                    "testing": False,
                    "antenna": [
                        {
                            "frequency": 2_100_000_000,
                            "frequency_max": 2_300_000_000,
                        }
                    ],
                },
                {
                    "id": 22,
                    "name": "below-threshold",
                    "observations": 999,
                    "success_rate": 100,
                    "is_connected": True,
                    "is_available": True,
                    "testing": False,
                    "antenna": [
                        {
                            "frequency": 1_000_000_000,
                            "frequency_max": 3_000_000_000,
                        }
                    ],
                },
                {
                    "id": 23,
                    "name": "vhf-backup",
                    "observations": 1_000,
                    "success_rate": 0,
                    "is_connected": True,
                    "is_available": True,
                    "testing": False,
                    "antenna": [
                        {
                            "frequency": 140_000_000,
                            "frequency_max": 150_000_000,
                        }
                    ],
                },
                {
                    "id": 24,
                    "name": "l-band-backup",
                    "observations": 2_000,
                    "success_rate": 0,
                    "is_connected": True,
                    "is_available": True,
                    "testing": False,
                    "antenna": [
                        {
                            "frequency": 1_600_000_000,
                            "frequency_max": 1_800_000_000,
                        }
                    ],
                },
            ]
            stations_path.write_text(json.dumps(stations))
            kwargs = {
                "output_path": root / "selection.json",
                "frozen_at": captured,
                "minimum_observations": 1_000,
            }
            first = design_station_expansion(
                target_pool_path,
                campaign_path,
                transmitter_path,
                hardware_path,
                stations_path,
                **kwargs,
            )
            stations[0]["success_rate"] = 100
            stations[1]["success_rate"] = 0
            stations_path.write_text(json.dumps(stations))
            second = design_station_expansion(
                target_pool_path,
                campaign_path,
                transmitter_path,
                hardware_path,
                stations_path,
                **kwargs,
            )
            redundant = design_station_expansion(
                target_pool_path,
                campaign_path,
                transmitter_path,
                hardware_path,
                stations_path,
                **kwargs,
                required_compatible_stations_per_target=2,
            )
        self.assertEqual(first["initially_covered_norad_ids"], [101])
        self.assertEqual(first["final_covered_target_count"], 3)
        self.assertEqual(
            [item["station_id"] for item in first["selected_stations"]], [20]
        )
        self.assertEqual(first["selected_stations"], second["selected_stations"])
        self.assertFalse(first["screening_used_success_outcomes"])
        self.assertEqual(
            [item["station_id"] for item in redundant["selected_stations"]],
            [20, 21, 24, 23],
        )
        self.assertEqual(redundant["final_covered_target_count"], 3)
        self.assertEqual(redundant["final_minimum_compatible_station_count"], 2)

    def test_cohort_design_is_deterministic_and_volume_only(self):
        inventory = []
        stats = []
        modes = ["FSK", "BPSK", "AFSK", "GMSK"]
        for index in range(20):
            uuid = f"uuid-{index:02d}"
            inventory.append(
                {
                    "uuid": uuid,
                    "norad_cat_id": 50_000 + index,
                    "status": "active",
                    "alive": True,
                    "unconfirmed": False,
                    "mode": modes[index % len(modes)],
                    "downlink_low": 145_000_000 if index % 2 else 437_000_000,
                }
            )
            stats.append(
                {
                    "uuid": uuid,
                    "stats": {
                        "total_count": 2_000 - index,
                        "good_count": index,
                        "bad_count": 2_000 - 2 * index,
                    },
                }
            )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inventory_path, stats_path = root / "tx.json", root / "stats.json"
            inventory_path.write_text(json.dumps(inventory))
            stats_path.write_text(json.dumps(stats))
            kwargs = dict(
                output_path=root / "cohort.json",
                study_id="v4",
                frozen_at=datetime(2024, 7, 1, tzinfo=UTC),
                start=datetime(2024, 1, 1, tzinfo=UTC),
                end=datetime(2024, 7, 1, tzinfo=UTC),
                user_agent="test/contact",
                maximum_targets=12,
                minimum_total_observations=500,
                external_validation_fraction=0.25,
                sampling_intervals=(
                    (
                        datetime(2024, 1, 1, tzinfo=UTC),
                        datetime(2024, 1, 15, tzinfo=UTC),
                    ),
                    (
                        datetime(2024, 6, 1, tzinfo=UTC),
                        datetime(2024, 7, 1, tzinfo=UTC),
                    ),
                ),
            )
            first = design_expanded_cohort(
                inventory_path, stats_path, **kwargs
            )
            second = design_expanded_cohort(
                inventory_path, stats_path, **kwargs
            )
        self.assertEqual(first["targets"], second["targets"])
        self.assertEqual(len(first["targets"]), 12)
        self.assertEqual(len(first["external_validation_norad_ids"]), 3)
        self.assertIn("Good/bad counts were not used", first["selection_rule"])
        self.assertEqual(len(first["sampling_intervals"]), 2)
        self.assertIn("Outcome-blind common UTC intervals", first["sampling_rule"])


class RuntimeSourceManifestTests(unittest.TestCase):
    def test_v4_config_is_bound_to_the_frozen_cohort_contract(self):
        project_root = Path(__file__).resolve().parents[1]
        source_config = (
            project_root / "configs/observation-planning-publication-v4.json"
        )
        validate_v4_publication_config_contract(source_config)
        with tempfile.TemporaryDirectory() as directory:
            tampered = Path(directory) / "publication.json"
            payload = json.loads(source_config.read_text())
            payload["minimum_external_test_rows"] = 29
            tampered.write_text(json.dumps(payload))
            with self.assertRaisesRegex(ValueError, "frozen v4 cohort"):
                validate_v4_publication_config_contract(tampered)

    def test_v4_pre_fit_gate_requires_informative_external_splits(self):
        project_root = Path(__file__).resolve().parents[1]
        config_path = (
            project_root / "configs/observation-planning-publication-v4.json"
        )
        config = PublicationDatasetConfig.load(config_path)
        target_ids = [target.norad_id for target in config.targets]
        rows: list[NormalizedObservation] = []
        for index in range(1_200):
            if index < 800:
                start = datetime(2026, 6, 1, tzinfo=UTC) + timedelta(
                    minutes=index
                )
            else:
                start = datetime(2026, 8, 1, tzinfo=UTC) + timedelta(
                    minutes=index - 800
                )
            batch = index // len(target_ids)
            rows.append(
                replace(
                    normalized_row(index),
                    start=start,
                    end=start + timedelta(minutes=5),
                    norad_id=target_ids[index % len(target_ids)],
                    station_id=200 + index % 30,
                    signal_present=batch % 2,
                    decode_success_given_signal=(batch + index) % 2,
                )
            )

        production = _publication_count_requirements(rows, config=config)
        independent = _recompute_publication_count_requirements(
            [row.as_dict() for row in rows], json.loads(config_path.read_text())
        )
        self.assertEqual(production, independent)
        self.assertTrue(all(production.values()))

        external_ids = set(config.external_validation_norad_ids)
        cutoff = config.sampling_intervals[-1].start
        uninformative = [
            replace(row, signal_present=0)
            if row.start >= cutoff and row.norad_id in external_ids
            else row
            for row in rows
        ]
        rejected = _publication_count_requirements(uninformative, config=config)
        self.assertFalse(
            rejected[
                "v4_signal_present_external_satellite_test_is_informative"
            ]
        )

    def test_v4_method_contract_is_bound_and_fails_closed(self):
        project_root = Path(__file__).resolve().parents[1]
        source_contract = (
            project_root / "configs/observation-planning-method-contract-v4.json"
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "configs/observation-planning-publication-v4.json"
            contract = root / "configs/observation-planning-method-contract-v4.json"
            config.parent.mkdir(parents=True)
            config.write_text("{}\n")
            contract.write_bytes(source_contract.read_bytes())

            self.assertEqual(validate_v4_method_contract(root), contract)
            payload = json.loads(contract.read_text())
            payload["coverage_thresholds"]["capture_time_location_fraction"] = 0.5
            contract.write_text(json.dumps(payload))
            with self.assertRaisesRegex(ValueError, "frozen outcome-blind"):
                validate_v4_method_contract(root)

    def test_historical_antenna_truth_audit_is_bound_and_fails_closed(self):
        project_root = Path(__file__).resolve().parents[1]
        source_audit = (
            project_root
            / "reports/observation-planning-publication-v4/"
            "historical-antenna-truth-audit-20260903T232159Z.json"
        )
        payload = json.loads(source_audit.read_text())
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "configs/observation-planning-publication-v4.json"
            config.parent.mkdir(parents=True)
            config.write_text("{}\n")
            referenced = [
                payload["prospective_coverage"]["station_selection_path"],
                *(
                    item["path"]
                    for field in (
                        "antenna_evidence_artifacts",
                        "antenna_snapshot_artifacts",
                    )
                    for item in payload["prospective_coverage"][field]
                ),
            ]
            for relative in referenced:
                target = root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes((project_root / relative).read_bytes())
            audit = (
                root
                / "reports/observation-planning-publication-v4/"
                "historical-antenna-truth-audit-20260903T232159Z.json"
            )
            audit.parent.mkdir(parents=True)
            audit.write_text(json.dumps(payload))

            self.assertEqual(_historical_antenna_truth_audit_paths(root), [audit])
            payload["implementation_policy"]["historical_current_profile_backfill"] = (
                "allowed"
            )
            audit.write_text(json.dumps(payload))
            with self.assertRaisesRegex(ValueError, "truth boundary"):
                _historical_antenna_truth_audit_paths(root)

    def test_runtime_manifest_is_fresh_content_bound_and_fail_closed(self):
        project_root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            manifest_path = Path(directory) / "source-manifest.json"
            manifest = write_planning_source_manifest(project_root, manifest_path)
            manifest_paths = {str(item["path"]) for item in manifest["files"]}
            self.assertIn(
                (
                    "reports/observation-planning-publication-v4/"
                    "historical-antenna-truth-audit-20260903T232159Z.json"
                ),
                manifest_paths,
            )
            self.assertIn(
                "configs/observation-planning-method-contract-v4.json",
                manifest_paths,
            )
            created_at = datetime.fromisoformat(
                str(manifest["created_at"]).replace("Z", "+00:00")
            )
            self.assertEqual(
                _verify_runtime_source_manifest(
                    manifest_path, current=created_at + timedelta(seconds=1)
                ),
                manifest["source_identity_sha256"],
            )
            with self.assertRaisesRegex(CampaignRunnerError, "within 10 minutes"):
                _verify_runtime_source_manifest(
                    manifest_path, current=created_at + timedelta(minutes=11)
                )

            tampered = json.loads(manifest_path.read_text())
            tampered["files"][0]["sha256"] = "0" * 64
            manifest_path.write_text(json.dumps(tampered))
            with self.assertRaisesRegex(CampaignRunnerError, "canonical runtime"):
                _verify_runtime_source_manifest(
                    manifest_path, current=created_at + timedelta(seconds=1)
                )

            incomplete = write_planning_source_manifest(project_root, manifest_path)
            incomplete["files"] = incomplete["files"][:-1]
            incomplete["file_count"] = len(incomplete["files"])
            incomplete["source_identity_sha256"] = hashlib.sha256(
                json.dumps(
                    incomplete["files"], sort_keys=True, separators=(",", ":")
                ).encode()
            ).hexdigest()
            manifest_path.write_text(json.dumps(incomplete))
            with self.assertRaisesRegex(CampaignRunnerError, "canonical runtime"):
                _verify_runtime_source_manifest(
                    manifest_path, current=created_at + timedelta(seconds=1)
                )

    def test_in_campaign_run_without_manifest_fails_before_external_work(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_path = root / "campaign.json"
            ledger_path = root / "ledger.jsonl"
            config_path.write_text(
                json.dumps(
                    {
                        "schema_version": "observation-planning-prospective-config-v1",
                        "campaign_id": "runtime-source-required",
                        "registered_at": "2024-01-01T00:00:00Z",
                        "start": "2024-01-02T00:00:00Z",
                        "end": "2024-02-02T00:00:00Z",
                        "station_ids": [1],
                        "target_norad_ids": [25544],
                    }
                )
            )
            initialize_campaign(
                config_path,
                ledger_path,
                now=lambda: datetime(2024, 1, 1, tzinfo=UTC),
            )
            with self.assertRaisesRegex(CampaignRunnerError, "requires.*manifest"):
                run_shadow_once(
                    campaign_config_path=config_path,
                    publication_config_path=root / "unused-publication.json",
                    transmitter_inventory_path=root / "unused-transmitters.json",
                    covariate_archive_path=root / "unused-covariates.json",
                    history_dataset_path=root / "unused-history.jsonl",
                    ledger_path=ledger_path,
                    plan_path=root / "unused-plan.json",
                    state_database_path=root / "unused.sqlite3",
                    cache_dir=root / "unused-cache",
                    user_agent="test/contact",
                    now=datetime(2024, 1, 3, tzinfo=UTC),
                )
            events = read_ledger(ledger_path)
            self.assertEqual(len(events), 2)
            self.assertEqual(events[-1].event_type, "plan_failed")
            self.assertIn("source manifest", str(events[-1].payload.get("error")))

    def test_registered_runtime_user_agent_cannot_be_replaced(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_path = root / "campaign.json"
            ledger_path = root / "ledger.jsonl"
            config_path.write_text(
                json.dumps(
                    {
                        "schema_version": (
                            "observation-planning-prospective-config-v1"
                        ),
                        "campaign_id": "runtime-contact-bound",
                        "registered_at": "2024-01-01T00:00:00Z",
                        "start": "2024-01-02T00:00:00Z",
                        "end": "2024-02-02T00:00:00Z",
                        "station_ids": [1],
                        "target_norad_ids": [25544],
                        "runtime_user_agent": (
                            "telemetry-yield-research/0.2 "
                            "contact=research@university.edu"
                        ),
                    }
                )
            )
            initialize_campaign(
                config_path,
                ledger_path,
                now=lambda: datetime(2024, 1, 1, tzinfo=UTC),
            )
            with self.assertRaisesRegex(CampaignRunnerError, "registered campaign"):
                run_shadow_once(
                    campaign_config_path=config_path,
                    publication_config_path=root / "unused-publication.json",
                    transmitter_inventory_path=root / "unused-transmitters.json",
                    covariate_archive_path=root / "unused-covariates.json",
                    history_dataset_path=root / "unused-history.jsonl",
                    ledger_path=ledger_path,
                    plan_path=root / "unused-plan.json",
                    state_database_path=root / "unused.sqlite3",
                    cache_dir=root / "unused-cache",
                    user_agent=(
                        "telemetry-yield-research/0.2 "
                        "contact=other@university.edu"
                    ),
                    now=datetime(2024, 1, 3, tzinfo=UTC),
                )
            events = read_ledger(ledger_path)
            self.assertEqual(events[-1].event_type, "plan_failed")
            self.assertFalse(events[-1].payload["retryable"])


class ProspectiveTests(unittest.TestCase):
    def test_plan_commit_uses_actual_time_and_rejects_started_contacts(self):
        registered = datetime(2024, 1, 1, tzinfo=UTC)
        start = datetime(2024, 1, 2, tzinfo=UTC)
        created_at = start + timedelta(hours=1)
        assignment_start = created_at + timedelta(hours=1)
        config = {
            "schema_version": "observation-planning-prospective-config-v1",
            "campaign_id": "commit-boundary-fixture",
            "registered_at": registered.isoformat(),
            "start": start.isoformat(),
            "end": (start + timedelta(days=31)).isoformat(),
            "station_ids": [12],
            "target_norad_ids": [25544],
        }
        estimate = ProbabilityEstimate(
            p_transmit=0.8,
            p_decode_given_transmit=0.75,
            p_success=0.6,
            standard_deviation=0.1,
            lower_90=0.4,
            upper_90=0.8,
            model_version="fixture",
        )
        assignment = Opportunity(
            opportunity_id="future-contact",
            norad_id=25544,
            satellite_name="ISS",
            station_id="station-12",
            resource_id="receiver-12",
            start=assignment_start,
            end=assignment_start + timedelta(minutes=5),
            tle_fingerprint="f" * 64,
            probability=estimate,
            nominal_unique_samples=100,
            expected_unique_samples=60,
            priority=1,
            max_elevation_deg=45,
            min_range_km=500,
            frequency_hz=145_800_000,
            modulation="AFSK",
            transmitter_uuid="A" * 22,
            satnogs_station_id=12,
        )
        plan = ObservationPlan(
            plan_id="commit-boundary-plan",
            revision=1,
            created_at=created_at,
            horizon_start=created_at,
            horizon_end=start + timedelta(days=31),
            assignments=(assignment,),
            tle_fingerprints={25544: "f" * 64},
            objective_value=assignment.objective_value(),
            solver="fixture",
            trigger="fixture",
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_path = root / "config.json"
            ledger_path = root / "ledger.jsonl"
            plan_path = root / "plan.json"
            config_path.write_text(json.dumps(config))
            plan_path.write_text(json.dumps(plan_document(plan)))
            initialize_campaign(config_path, ledger_path, now=lambda: registered)

            committed_at = created_at + timedelta(minutes=20)
            event = commit_plan(
                config_path,
                ledger_path,
                plan_path,
                now=lambda: committed_at,
            )
            self.assertEqual(event.occurred_at, committed_at)
            self.assertEqual(event.payload["commit_latency_seconds"], 1_200)
            committed_assignment = event.payload["assignments"][0]
            self.assertEqual(committed_assignment["station_id"], "station-12")
            self.assertEqual(committed_assignment["satnogs_station_id"], 12)
            self.assertEqual(committed_assignment["transmitter_uuid"], "A" * 22)
            self.assertEqual(committed_assignment["frequency_hz"], 145_800_000)
            self.assertEqual(committed_assignment["modulation"], "AFSK")

            started = replace(
                plan,
                revision=2,
                assignments=(
                    replace(
                        assignment,
                        opportunity_id="already-started",
                        start=assignment_start + timedelta(minutes=1),
                        end=assignment_start + timedelta(minutes=6),
                    ),
                ),
            )
            plan_path.write_text(json.dumps(plan_document(started)))
            with self.assertRaisesRegex(
                ProspectiveError, "strictly after the plan commitment"
            ):
                commit_plan(
                    config_path,
                    ledger_path,
                    plan_path,
                    now=lambda: assignment_start + timedelta(minutes=1),
                )

            too_short_start = committed_at + timedelta(hours=1)
            too_short = replace(
                plan,
                revision=2,
                assignments=(
                    replace(
                        assignment,
                        opportunity_id="too-short",
                        start=too_short_start,
                        end=too_short_start + timedelta(seconds=30),
                    ),
                ),
            )
            plan_path.write_text(json.dumps(plan_document(too_short)))
            with self.assertRaisesRegex(ProspectiveError, "duration bounds"):
                commit_plan(
                    config_path,
                    ledger_path,
                    plan_path,
                    now=lambda: committed_at,
                )

    def test_shadow_reconciliation_uses_explicit_numeric_network_station_id(self):
        registered = datetime(2024, 1, 1, tzinfo=UTC)
        config = {
            "schema_version": "observation-planning-prospective-config-v1",
            "campaign_id": "reconcile-fixture",
            "registered_at": "2024-01-01T00:00:00Z",
            "start": "2024-01-02T00:00:00Z",
            "end": "2024-02-02T00:00:00Z",
            "station_ids": [12],
            "target_norad_ids": [25544],
            "minimum_reconciled_outcomes": 1,
        }

        class FakeClient:
            calls = []

            def __init__(self, **_kwargs):
                pass

            def get(self, _path, *, params=None):
                self.params = params
                self.calls.append(dict(params or {}))
                return SatNOGSResponse(
                    url="https://network.satnogs.org/api/observations/",
                    body=json.dumps(
                        [
                            {
                                "id": 700,
                                "start": "2024-01-03T00:00:30Z",
                                "end": "2024-01-03T00:04:30Z",
                                "norad_cat_id": 25544,
                                "ground_station": 12,
                                "transmitter_uuid": "tx-selected",
                                "waterfall_status": "with-signal",
                                "demoddata": [{"payload": "fixture"}],
                            },
                            {
                                "id": 701,
                                "start": "2024-01-03T00:10:30Z",
                                "end": "2024-01-03T00:14:30Z",
                                "norad_cat_id": 25544,
                                "ground_station": 12,
                                "transmitter_uuid": "tx-selected",
                                "waterfall_status": "unknown",
                                "demoddata": [],
                            },
                            {
                                "id": 702,
                                "start": "2024-01-03T00:20:30Z",
                                "end": "2024-01-03T00:24:30Z",
                                "norad_cat_id": 25544,
                                "ground_station": 12,
                                "transmitter_uuid": "tx-selected",
                                "waterfall_status": "without-signal",
                                "demoddata": [{"payload": "contradiction"}],
                            },
                            {
                                "id": 703,
                                "start": "2024-01-03T00:30:30Z",
                                "end": "2024-01-03T00:34:30Z",
                                "norad_cat_id": 25544,
                                "ground_station": 12,
                                "transmitter_uuid": "tx-selected",
                                "waterfall_status": "unknown",
                                "demoddata": [{"payload": "decoded"}],
                            },
                            {
                                "id": 704,
                                "start": "2024-01-03T00:40:30Z",
                                "end": "2024-01-03T00:44:30Z",
                                "norad_cat_id": 25544,
                                "ground_station": 12,
                                "transmitter_uuid": "tx-selected",
                                "waterfall_status": "with-signal",
                            },
                            {
                                "id": 705,
                                "start": "2024-01-03T00:00:05Z",
                                "end": "2024-01-03T00:04:55Z",
                                "norad_cat_id": 25544,
                                "ground_station": 12,
                                "transmitter_uuid": "tx-wrong",
                                "waterfall_status": "without-signal",
                                "demoddata": [],
                            }
                        ]
                    ).encode(),
                    status=200,
                    headers={},
                )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_path, ledger = root / "config.json", root / "ledger.jsonl"
            config_path.write_text(json.dumps(config))
            initialize_campaign(config_path, ledger, now=lambda: registered)
            append_event(
                ledger,
                campaign_id="reconcile-fixture",
                event_type="plan_committed",
                occurred_at=datetime(2024, 1, 2, tzinfo=UTC),
                payload={
                    "plan_id": "reconcile-plan",
                    "revision": 1,
                    "horizon_start": "2024-01-02T00:00:00Z",
                    "horizon_end": "2024-02-02T00:00:00Z",
                    "assignments": [
                        {
                            "opportunity_id": "pass-old",
                            "norad_id": 25544,
                            "station_id": "satnogs-12:Fixture",
                            "satnogs_station_id": 12,
                            "transmitter_uuid": "tx-selected",
                            "modulation": "AFSK",
                            "start": "2024-01-03T00:00:00+00:00",
                            "end": "2024-01-03T00:05:00+00:00",
                            "p_signal_present": 0.8,
                            "p_decode_given_signal": 0.9,
                            "p_success": 0.8,
                        }
                    ]
                },
            )
            append_event(
                ledger,
                campaign_id="reconcile-fixture",
                event_type="plan_committed",
                occurred_at=datetime(2024, 1, 2, 12, tzinfo=UTC),
                payload={
                    "plan_id": "reconcile-plan",
                    "revision": 2,
                    "horizon_start": "2024-01-02T12:00:00Z",
                    "horizon_end": "2024-02-02T00:00:00Z",
                    "assignments": [
                        {
                            "opportunity_id": "pass-new",
                            "norad_id": 25544,
                            "station_id": "satnogs-12:Fixture",
                            "satnogs_station_id": 12,
                            "transmitter_uuid": "tx-selected",
                            "modulation": "AFSK",
                            "start": "2024-01-03T00:00:00+00:00",
                            "end": "2024-01-03T00:05:00+00:00",
                            "p_signal_present": 0.81,
                            "p_decode_given_signal": 0.91,
                            "p_success": 0.81,
                        },
                        {
                            "opportunity_id": "pass-unknown",
                            "norad_id": 25544,
                            "station_id": "satnogs-12:Fixture",
                            "satnogs_station_id": 12,
                            "transmitter_uuid": "tx-selected",
                            "modulation": "AFSK",
                            "start": "2024-01-03T00:10:00+00:00",
                            "end": "2024-01-03T00:15:00+00:00",
                            "p_signal_present": 0.7,
                            "p_decode_given_signal": 0.8,
                            "p_success": 0.7,
                        },
                        {
                            "opportunity_id": "pass-contradictory",
                            "norad_id": 25544,
                            "station_id": "satnogs-12:Fixture",
                            "satnogs_station_id": 12,
                            "transmitter_uuid": "tx-selected",
                            "modulation": "AFSK",
                            "start": "2024-01-03T00:20:00+00:00",
                            "end": "2024-01-03T00:25:00+00:00",
                            "p_signal_present": 0.6,
                            "p_decode_given_signal": 0.8,
                            "p_success": 0.6,
                        },
                        {
                            "opportunity_id": "pass-decoded-artifact",
                            "norad_id": 25544,
                            "station_id": "satnogs-12:Fixture",
                            "satnogs_station_id": 12,
                            "transmitter_uuid": "tx-selected",
                            "modulation": "AFSK",
                            "start": "2024-01-03T00:30:00+00:00",
                            "end": "2024-01-03T00:35:00+00:00",
                            "p_signal_present": 0.5,
                            "p_decode_given_signal": 0.75,
                            "p_success": 0.375,
                        },
                        {
                            "opportunity_id": "pass-malformed",
                            "norad_id": 25544,
                            "station_id": "satnogs-12:Fixture",
                            "satnogs_station_id": 12,
                            "transmitter_uuid": "tx-selected",
                            "modulation": "AFSK",
                            "start": "2024-01-03T00:40:00+00:00",
                            "end": "2024-01-03T00:45:00+00:00",
                            "p_signal_present": 0.4,
                            "p_decode_given_signal": 0.7,
                            "p_success": 0.28,
                        }
                    ]
                },
            )
            with patch(
                "telemetry_yield.planning.campaign_runner.SatNOGSClient",
                FakeClient,
            ):
                result = reconcile_shadow_network(
                    campaign_config_path=config_path,
                    ledger_path=ledger,
                    raw_snapshot_path=root / "raw.json",
                    outcomes_path=root / "outcomes.json",
                    cache_dir=root / "cache",
                    user_agent="test/contact",
                    minimum_request_interval_seconds=0,
                    now=datetime(2024, 1, 3, 1, tzinfo=UTC),
                )
                repeated = reconcile_shadow_network(
                    campaign_config_path=config_path,
                    ledger_path=ledger,
                    raw_snapshot_path=root / "raw-repeat.json",
                    outcomes_path=root / "outcomes-repeat.json",
                    cache_dir=root / "cache-repeat",
                    user_agent="test/contact",
                    minimum_request_interval_seconds=0,
                    now=datetime(2024, 1, 3, 2, tzinfo=UTC),
                )
            events = read_ledger(ledger)
            raw_snapshot = json.loads((root / "raw.json").read_text())
            raw_snapshot_sha256 = hashlib.sha256(
                (root / "raw.json").read_bytes()
            ).hexdigest()
        self.assertEqual(result["matched_outcomes"], 2)
        self.assertEqual(result["pending_assignments"], 5)
        self.assertEqual(result["query_pairs"], 1)
        self.assertEqual(result["skipped_unknown_labels"], 1)
        self.assertEqual(result["skipped_contradictory_labels"], 1)
        self.assertEqual(result["skipped_malformed_labels"], 1)
        self.assertEqual(repeated["matched_outcomes"], 0)
        self.assertEqual(repeated["skipped_unknown_labels"], 1)
        self.assertEqual(repeated["skipped_contradictory_labels"], 1)
        self.assertEqual(repeated["skipped_malformed_labels"], 1)
        outcomes = [event for event in events if event.event_type == "outcome_recorded"]
        self.assertEqual(len(outcomes), 2)
        self.assertNotIn(705, {item.payload.get("observation_id") for item in outcomes})
        outcome = next(
            item
            for item in outcomes
            if item.payload["opportunity_id"] == "pass-new"
        )
        self.assertEqual(outcome.payload["opportunity_id"], "pass-new")
        self.assertEqual(outcome.payload["signal_present"], 1)
        self.assertEqual(outcome.payload["decode_success_given_signal"], 1)
        self.assertEqual(
            outcome.payload["raw_source_artifact_sha256"], raw_snapshot_sha256
        )
        artifact_outcome = next(
            item
            for item in outcomes
            if item.payload["opportunity_id"] == "pass-decoded-artifact"
        )
        self.assertIsNone(artifact_outcome.payload["signal_present"])
        self.assertEqual(
            artifact_outcome.payload["decode_success_given_signal"], 1
        )
        self.assertEqual(raw_snapshot["lookback_hours"], 48)
        self.assertEqual(raw_snapshot["query_windows"][0]["station_id"], 12)
        self.assertEqual(raw_snapshot["query_windows"][0]["norad_id"], 25544)
        self.assertEqual(
            raw_snapshot["query_windows"][0]["transmitter_uuid"], "tx-selected"
        )
        self.assertTrue(FakeClient.calls)
        self.assertTrue(
            all(call.get("ground_station") == 12 for call in FakeClient.calls)
        )
        self.assertTrue(
            all(call.get("norad_cat_id") == 25544 for call in FakeClient.calls)
        )
        self.assertTrue(
            all(
                call.get("transmitter_uuid") == "tx-selected"
                for call in FakeClient.calls
            )
        )

    def test_operational_blockers_map_network_ids_and_validate_resources(self):
        station = GroundStation(
            station_id="satnogs-1234",
            satnogs_station_id=1234,
            latitude_deg=52.2,
            longitude_deg=21.0,
            altitude_m=100,
            resources=(ReceiverResource(resource_id="receiver-1"),),
        )
        payload = {
            "schema_version": "observation-planning-blockers-v1",
            "blockers": [
                {
                    "blocker_id": "maintenance-1",
                    "station_id": 1234,
                    "resource_id": "receiver-1",
                    "start": "2026-09-10T08:00:00Z",
                    "end": "2026-09-10T12:00:00Z",
                    "kind": "maintenance",
                }
            ],
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "blockers.json"
            path.write_text(json.dumps(payload))
            blockers = _blockers(path, (station,))
            self.assertEqual(blockers[0].station_id, "satnogs-1234")
            payload["blockers"][0]["resource_id"] = "missing"
            path.write_text(json.dumps(payload))
            with self.assertRaisesRegex(CampaignRunnerError, "absent"):
                _blockers(path, (station,))
            payload["blockers"][0]["resource_id"] = "receiver-1"
            payload["blockers"].append(dict(payload["blockers"][0]))
            path.write_text(json.dumps(payload))
            with self.assertRaisesRegex(CampaignRunnerError, "duplicate blocker_id"):
                _blockers(path, (station,))

    def test_target_overrides_preserve_constraints_and_optional_physics(self):
        campaign = type("Campaign", (), {"target_norad_ids": (40071,)})()
        publication_target = type(
            "PublicationTarget",
            (),
            {
                "norad_id": 40071,
                "selection_transmitter_uuid": "transmitter-1",
                "selection_mode": "FSK",
            },
        )()
        publication = type("Publication", (), {"targets": (publication_target,)})()
        inventory = [
            {
                "uuid": "transmitter-1",
                "norad_cat_id": 40071,
                "sat_id": "TESTSAT",
                "downlink_low": 437_500_000,
                "baud": 9600,
            }
        ]
        overrides = {
            "schema_version": "observation-planning-target-overrides-v1",
            "targets": [
                {
                    "norad_id": 40071,
                    "transmit_power_dbw": 3.0,
                    "transmit_antenna_gain_dbi": 1.0,
                    "samples_per_second": 2.0,
                    "priority": 4.0,
                    "exclusive_transmission": False,
                    "transmission_rule": {
                        "weekdays_utc": [4],
                        "month_days_utc": [11],
                        "daily_windows_utc": [["10:00:00", "12:00:00"]],
                        "transmit_regions": [
                            {
                                "south_deg": 49,
                                "north_deg": 55,
                                "west_deg": 14,
                                "east_deg": 25,
                                "label": "Poland",
                            }
                        ],
                        "probability_when_allowed": 0.95,
                    },
                }
            ],
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inventory_path = root / "inventory.json"
            overrides_path = root / "overrides.json"
            inventory_path.write_text(json.dumps(inventory))
            overrides_path.write_text(json.dumps(overrides))
            target = _targets(
                campaign,
                publication,
                inventory_path,
                overrides_path,
            )[0]
        self.assertEqual(target.transmit_power_dbw, 3.0)
        self.assertEqual(target.samples_per_second, 2.0)
        self.assertEqual(target.priority, 4.0)
        self.assertFalse(target.exclusive_transmission)
        self.assertEqual(target.transmission_rule.weekdays_utc, (4,))
        self.assertEqual(target.transmission_rule.month_days_utc, (11,))
        self.assertEqual(target.transmission_rule.transmit_regions[0].label, "Poland")

    def test_month_gate_and_hash_chain_cannot_be_faked_by_early_report(self):
        registered = datetime(2026, 9, 3, tzinfo=UTC)
        config = {
            "schema_version": "observation-planning-prospective-config-v1",
            "campaign_id": "shadow-v4",
            "registered_at": "2026-09-03T00:00:00Z",
            "start": "2026-09-04T00:00:00Z",
            "end": "2026-10-05T00:00:00Z",
            "station_ids": [12],
            "target_norad_ids": [25544],
            "mode": "shadow",
            "minimum_plan_commit_days": 25,
            "minimum_reconciled_outcomes": 1,
            "maximum_opportunity_duration_seconds": 900,
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_path, ledger = root / "config.json", root / "ledger.jsonl"
            config_path.write_text(json.dumps(config))
            initialized = initialize_campaign(
                config_path, ledger, now=lambda: registered
            )
            self.assertEqual(
                initialized.payload["maximum_opportunity_duration_seconds"],
                900,
            )
            self.assertEqual(
                initialized.payload["minimum_opportunity_duration_seconds"],
                60,
            )
            early = prospective_report(
                config_path, ledger, now=datetime(2026, 9, 20, tzinfo=UTC)
            )
            self.assertFalse(early["publication_complete"])
            self.assertFalse(early["gates"]["elapsed_at_least_30_days"])
            lines = ledger.read_text().splitlines()
            tampered = json.loads(lines[0])
            tampered["payload"]["mode"] = "submission-gated"
            ledger.write_text(json.dumps(tampered) + "\n")
            with self.assertRaisesRegex(ProspectiveError, "hash"):
                read_ledger(ledger)

    def test_simulated_complete_chain_requires_real_elapsed_time_and_coverage(self):
        start = datetime(2024, 1, 2, tzinfo=UTC)
        config = {
            "schema_version": "observation-planning-prospective-config-v1",
            "campaign_id": "completed-fixture",
            "registered_at": "2024-01-01T00:00:00Z",
            "start": "2024-01-02T00:00:00Z",
            "end": "2024-02-02T00:00:00Z",
            "station_ids": [12],
            "target_norad_ids": [25544],
            "minimum_plan_commit_days": 25,
            "minimum_reconciled_outcomes": 1,
            "minimum_observations_per_target": 1,
            "maximum_opportunity_duration_seconds": 900,
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_path, ledger = root / "config.json", root / "ledger.jsonl"
            config_path.write_text(json.dumps(config))
            initialize_campaign(
                config_path,
                ledger,
                now=lambda: datetime(2024, 1, 1, tzinfo=UTC),
            )
            previous_opportunity = "opportunity-0"
            for day in range(25):
                previous_opportunity = f"opportunity-{day}"
                append_event(
                    ledger,
                    campaign_id="completed-fixture",
                    event_type="plan_committed",
                    occurred_at=start + timedelta(days=day, hours=1),
                    payload={
                        "trigger": "initial" if day == 0 else "tle_refresh",
                        "assignments": [
                            {
                                "opportunity_id": previous_opportunity,
                                "norad_id": 25544,
                                "start": (
                                    start + timedelta(days=day, hours=2)
                                ).isoformat(),
                                "end": (
                                    start
                                    + timedelta(days=day, hours=2, minutes=10)
                                ).isoformat(),
                                "p_signal_present": 0.7,
                                "p_decode_given_signal": 0.8,
                                "p_success": 0.56,
                            }
                        ],
                    },
                )
            append_event(
                ledger,
                campaign_id="completed-fixture",
                event_type="outcome_recorded",
                occurred_at=start + timedelta(days=25),
                payload={
                    "opportunity_id": previous_opportunity,
                    "signal_present": 1,
                    "decode_success_given_signal": 1,
                },
            )
            report = prospective_report(
                config_path, ledger, now=datetime(2024, 2, 3, tzinfo=UTC)
            )
        self.assertTrue(report["publication_complete"])
        self.assertTrue(report["gates"]["registered_target_coverage_met"])
        self.assertTrue(report["gates"]["opportunity_duration_bound_met"])
        self.assertEqual(report["opportunity_duration_violation_count"], 0)
        self.assertEqual(report["target_complete_plan_commit_count"], 25)
        self.assertEqual(report["plan_commit_days"], 25)
        self.assertAlmostEqual(report["signal_brier"], 0.09)
        self.assertAlmostEqual(report["conditional_decode_brier"], 0.04)
        self.assertAlmostEqual(report["end_to_end_brier"], (1 - 0.56) ** 2)

    def test_registered_target_coverage_gate_rejects_a_starved_satellite(self):
        start = datetime(2024, 1, 2, tzinfo=UTC)
        config = {
            "schema_version": "observation-planning-prospective-config-v1",
            "campaign_id": "coverage-fixture",
            "registered_at": "2024-01-01T00:00:00Z",
            "start": "2024-01-02T00:00:00Z",
            "end": "2024-02-02T00:00:00Z",
            "station_ids": [12],
            "target_norad_ids": [25544, 40000],
            "minimum_plan_commit_days": 1,
            "minimum_reconciled_outcomes": 1,
            "minimum_observations_per_target": 1,
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_path, ledger = root / "config.json", root / "ledger.jsonl"
            config_path.write_text(json.dumps(config))
            initialize_campaign(
                config_path,
                ledger,
                now=lambda: datetime(2024, 1, 1, tzinfo=UTC),
            )
            append_event(
                ledger,
                campaign_id="coverage-fixture",
                event_type="plan_committed",
                occurred_at=start + timedelta(hours=1),
                payload={
                    "assignments": [
                        {"opportunity_id": "only-one", "norad_id": 25544}
                    ]
                },
            )
            report = prospective_report(
                config_path, ledger, now=datetime(2024, 2, 3, tzinfo=UTC)
            )
        self.assertFalse(report["gates"]["registered_target_coverage_met"])
        self.assertEqual(report["assigned_target_ids"], [25544])
        self.assertEqual(report["target_complete_plan_commit_count"], 0)

    def test_opportunity_duration_gate_rejects_an_oversized_assignment(self):
        start = datetime(2024, 1, 2, tzinfo=UTC)
        config = {
            "schema_version": "observation-planning-prospective-config-v1",
            "campaign_id": "duration-fixture",
            "registered_at": "2024-01-01T00:00:00Z",
            "start": "2024-01-02T00:00:00Z",
            "end": "2024-02-02T00:00:00Z",
            "station_ids": [12],
            "target_norad_ids": [25544],
            "minimum_plan_commit_days": 1,
            "minimum_reconciled_outcomes": 1,
            "maximum_opportunity_duration_seconds": 900,
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_path, ledger = root / "config.json", root / "ledger.jsonl"
            config_path.write_text(json.dumps(config))
            initialize_campaign(
                config_path,
                ledger,
                now=lambda: datetime(2024, 1, 1, tzinfo=UTC),
            )
            append_event(
                ledger,
                campaign_id="duration-fixture",
                event_type="plan_committed",
                occurred_at=start + timedelta(hours=1),
                payload={
                    "assignments": [
                        {
                            "opportunity_id": "too-long",
                            "norad_id": 25544,
                            "start": start.isoformat(),
                            "end": (start + timedelta(minutes=16)).isoformat(),
                        },
                        {
                            "opportunity_id": "too-short",
                            "norad_id": 25544,
                            "start": (start + timedelta(hours=1)).isoformat(),
                            "end": (
                                start + timedelta(hours=1, seconds=30)
                            ).isoformat(),
                        }
                    ]
                },
            )
            report = prospective_report(
                config_path, ledger, now=datetime(2024, 2, 3, tzinfo=UTC)
            )
        self.assertFalse(report["gates"]["opportunity_duration_bound_met"])
        self.assertEqual(report["opportunity_duration_violation_count"], 2)

    def test_unknown_reconciled_outcome_does_not_satisfy_scoring_gates(self):
        start = datetime(2024, 1, 2, tzinfo=UTC)
        config = {
            "schema_version": "observation-planning-prospective-config-v1",
            "campaign_id": "unknown-label-fixture",
            "registered_at": "2024-01-01T00:00:00Z",
            "start": "2024-01-02T00:00:00Z",
            "end": "2024-02-02T00:00:00Z",
            "station_ids": [12],
            "target_norad_ids": [25544],
            "minimum_plan_commit_days": 1,
            "minimum_reconciled_outcomes": 1,
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_path, ledger = root / "config.json", root / "ledger.jsonl"
            config_path.write_text(json.dumps(config))
            initialize_campaign(
                config_path,
                ledger,
                now=lambda: datetime(2024, 1, 1, tzinfo=UTC),
            )
            append_event(
                ledger,
                campaign_id="unknown-label-fixture",
                event_type="plan_committed",
                occurred_at=start + timedelta(hours=1),
                payload={
                    "assignments": [
                        {
                            "opportunity_id": "unknown-1",
                            "p_signal_present": 0.7,
                            "p_decode_given_signal": 0.8,
                            "p_success": 0.56,
                        }
                    ]
                },
            )
            append_event(
                ledger,
                campaign_id="unknown-label-fixture",
                event_type="outcome_recorded",
                occurred_at=start + timedelta(days=1),
                payload={
                    "opportunity_id": "unknown-1",
                    "signal_present": None,
                    "decode_success_given_signal": None,
                },
            )
            report = prospective_report(
                config_path, ledger, now=datetime(2024, 2, 3, tzinfo=UTC)
            )
        self.assertTrue(report["gates"]["reconciled_outcomes_met"])
        self.assertFalse(report["gates"]["scored_signal_outcomes_met"])
        self.assertFalse(report["gates"]["conditional_decode_outcomes_met"])
        self.assertFalse(report["publication_complete"])

    def test_prospective_report_requires_class_and_domain_diversity(self):
        start = datetime(2024, 1, 2, tzinfo=UTC)
        config = {
            "schema_version": "observation-planning-prospective-config-v1",
            "campaign_id": "diversity-fixture",
            "registered_at": "2024-01-01T00:00:00Z",
            "start": "2024-01-02T00:00:00Z",
            "end": "2024-02-02T00:00:00Z",
            "station_ids": [12, 13],
            "target_norad_ids": [25544, 40000],
            "minimum_plan_commit_days": 1,
            "minimum_reconciled_outcomes": 3,
            "minimum_scored_targets": 2,
            "minimum_scored_stations": 2,
            "minimum_signal_positive_outcomes": 1,
            "minimum_signal_negative_outcomes": 1,
            "minimum_conditional_decode_positive_outcomes": 1,
            "minimum_conditional_decode_negative_outcomes": 1,
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_path, ledger = root / "config.json", root / "ledger.jsonl"
            config_path.write_text(json.dumps(config))
            initialize_campaign(
                config_path,
                ledger,
                now=lambda: datetime(2024, 1, 1, tzinfo=UTC),
            )
            assignments = [
                {
                    "opportunity_id": "positive-decode",
                    "norad_id": 25544,
                    "satnogs_station_id": 12,
                    "start": "2024-01-03T00:00:00Z",
                    "end": "2024-01-03T00:05:00Z",
                    "p_signal_present": 0.8,
                    "p_decode_given_signal": 0.7,
                    "p_success": 0.56,
                },
                {
                    "opportunity_id": "failed-decode",
                    "norad_id": 40000,
                    "satnogs_station_id": 13,
                    "start": "2024-01-03T01:00:00Z",
                    "end": "2024-01-03T01:05:00Z",
                    "p_signal_present": 0.7,
                    "p_decode_given_signal": 0.6,
                    "p_success": 0.42,
                },
                {
                    "opportunity_id": "no-signal",
                    "norad_id": 25544,
                    "satnogs_station_id": 12,
                    "start": "2024-01-03T02:00:00Z",
                    "end": "2024-01-03T02:05:00Z",
                    "p_signal_present": 0.4,
                    "p_decode_given_signal": 0.5,
                    "p_success": 0.2,
                },
            ]
            append_event(
                ledger,
                campaign_id="diversity-fixture",
                event_type="plan_committed",
                occurred_at=start + timedelta(hours=1),
                payload={"assignments": assignments},
            )
            append_event(
                ledger,
                campaign_id="diversity-fixture",
                event_type="outcome_recorded",
                occurred_at=start + timedelta(days=2),
                payload={
                    "opportunity_id": "positive-decode",
                    "signal_present": 1,
                    "decode_success_given_signal": 1,
                },
            )
            degenerate = prospective_report(
                config_path, ledger, now=datetime(2024, 2, 3, tzinfo=UTC)
            )
            self.assertFalse(degenerate["publication_complete"])
            self.assertFalse(degenerate["gates"]["reconciled_outcomes_met"])
            self.assertFalse(degenerate["gates"]["scored_target_diversity_met"])
            self.assertFalse(degenerate["gates"]["scored_station_diversity_met"])
            self.assertFalse(degenerate["gates"]["signal_negative_outcomes_met"])
            self.assertFalse(
                degenerate["gates"]["conditional_decode_negative_outcomes_met"]
            )

            for opportunity_id, signal, decode, hour in (
                ("failed-decode", 1, 0, 3),
                ("no-signal", 0, None, 4),
            ):
                append_event(
                    ledger,
                    campaign_id="diversity-fixture",
                    event_type="outcome_recorded",
                    occurred_at=start + timedelta(days=2, hours=hour),
                    payload={
                        "opportunity_id": opportunity_id,
                        "signal_present": signal,
                        "decode_success_given_signal": decode,
                    },
                )
            complete = prospective_report(
                config_path, ledger, now=datetime(2024, 2, 3, tzinfo=UTC)
            )

        self.assertTrue(complete["publication_complete"])
        self.assertEqual(complete["scored_target_ids"], [25544, 40000])
        self.assertEqual(complete["scored_station_ids"], [12, 13])
        self.assertEqual(complete["observed_signals"], 2)
        self.assertEqual(complete["observed_signal_absences"], 1)
        self.assertEqual(complete["successful_decodes"], 1)
        self.assertEqual(complete["failed_decodes_given_signal"], 1)

    def test_outcome_is_bound_to_last_prediction_committed_before_pass(self):
        start = datetime(2024, 1, 2, tzinfo=UTC)
        config = {
            "schema_version": "observation-planning-prospective-config-v1",
            "campaign_id": "prediction-binding-fixture",
            "registered_at": "2024-01-01T00:00:00Z",
            "start": "2024-01-02T00:00:00Z",
            "end": "2024-02-02T00:00:00Z",
            "station_ids": [12],
            "target_norad_ids": [25544],
            "minimum_plan_commit_days": 1,
            "minimum_reconciled_outcomes": 1,
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_path, ledger = root / "config.json", root / "ledger.jsonl"
            config_path.write_text(json.dumps(config))
            initialize_campaign(
                config_path,
                ledger,
                now=lambda: datetime(2024, 1, 1, tzinfo=UTC),
            )
            assignment = {
                "opportunity_id": "same-opportunity",
                "start": "2024-01-03T00:00:00Z",
                "end": "2024-01-03T00:05:00Z",
                "p_signal_present": 0.8,
                "p_decode_given_signal": 0.5,
                "p_success": 0.4,
                "model_version": "fixture",
            }
            first_plan = append_event(
                ledger,
                campaign_id="prediction-binding-fixture",
                event_type="plan_committed",
                occurred_at=start + timedelta(hours=1),
                payload={
                    "assignments": [
                        assignment,
                        {
                            **assignment,
                            "opportunity_id": "other-opportunity",
                        },
                        {
                            **assignment,
                            "opportunity_id": "atomic-opportunity",
                        },
                    ]
                },
            )
            append_event(
                ledger,
                campaign_id="prediction-binding-fixture",
                event_type="plan_committed",
                occurred_at=datetime(2024, 1, 3, tzinfo=UTC),
                payload={
                    "assignments": [
                        {
                            **assignment,
                            "p_signal_present": 0.1,
                            "p_decode_given_signal": 0.1,
                            "p_success": 0.01,
                        }
                    ]
                },
            )
            outcomes = root / "outcomes.json"
            outcomes.write_text(
                json.dumps(
                    [
                        {
                            "opportunity_id": "same-opportunity",
                            "observation_id": 1,
                            "observed_at": "2024-01-03T00:05:00Z",
                            "signal_present": 1,
                            "decode_success_given_signal": 1,
                        }
                    ]
                )
            )
            recorded = record_outcomes(
                config_path,
                ledger,
                outcomes,
                now=lambda: start + timedelta(days=3),
            )
            outcomes.write_text(
                json.dumps(
                    [
                        {
                            "opportunity_id": "other-opportunity",
                            "observation_id": 2,
                            "observed_at": "2024-01-03T00:05:00Z",
                            "signal_present": 1,
                            "decode_success_given_signal": 1,
                        },
                        {
                            "opportunity_id": "atomic-opportunity",
                            "observation_id": 1,
                            "observed_at": "2024-01-03T00:05:00Z",
                            "signal_present": 1,
                            "decode_success_given_signal": 1,
                        }
                    ]
                )
            )
            with self.assertRaisesRegex(
                ProspectiveError, "cannot score multiple opportunities"
            ):
                record_outcomes(
                    config_path,
                    ledger,
                    outcomes,
                    now=lambda: start + timedelta(days=3),
                )
            after_rejected_batch = read_ledger(ledger)
            report = prospective_report(
                config_path, ledger, now=datetime(2024, 2, 3, tzinfo=UTC)
            )
        self.assertEqual(
            recorded[0].payload["prediction_plan_event_sha256"],
            first_plan.event_sha256,
        )
        self.assertEqual(recorded[0].payload["prediction"]["p_signal_present"], 0.8)
        self.assertEqual(
            len(
                [
                    event
                    for event in after_rejected_batch
                    if event.event_type == "outcome_recorded"
                ]
            ),
            1,
        )
        self.assertAlmostEqual(report["signal_brier"], 0.04)


if __name__ == "__main__":
    unittest.main()
