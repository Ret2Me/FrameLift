import base64
import hashlib
import json
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from telemetry_yield.planning.covariates import (
    HardwareRecord,
    KpRecord,
    WeatherRecord,
    write_covariate_archive,
    write_covariate_evidence_manifest,
)
from telemetry_yield.planning.prospective import (
    append_event,
    initialize_campaign,
    record_outcomes,
)
from telemetry_yield.planning.readiness import (
    _audit_assignment_blockers,
    _audit_assignment_covariates,
    _audit_assignment_target_overrides,
    _audit_plan_tle_evidence,
    _contract_accounts_for_feature_group,
    _contract_matches_frozen_probability_model,
    _release_attestation_evidence,
    _v4_extension_checks,
    assess_planning_publication_readiness,
)
from telemetry_yield.planning.tle import parse_tle_text
from telemetry_yield.planning.test_attestation import (
    PlanningTestAttestationError,
    validate_test_attestation,
    write_test_attestation,
)


def external_data_attribution_review():
    return {
        "attested": True,
        "sources": {
            "open_meteo": {
                "source": "https://open-meteo.com/",
                "license": "CC-BY-4.0",
                "license_url": "https://creativecommons.org/licenses/by/4.0/",
                "citation": "https://doi.org/10.5281/zenodo.7970649",
                "terms_url": "https://open-meteo.com/en/terms",
            },
            "gfz_kp": {
                "source": "https://kp.gfz.de/",
                "license": "CC-BY-4.0",
                "license_url": "https://creativecommons.org/licenses/by/4.0/",
                "citation": "https://doi.org/10.5880/Kp.0001",
            },
            "noaa_swpc": {
                "source": "https://services.swpc.noaa.gov/",
                "license": "US-PUBLIC-DOMAIN",
                "terms_url": "https://www.weather.gov/disclaimer/",
            },
            "celestrak": {
                "source": "https://celestrak.org/",
                "usage_policy_url": "https://celestrak.org/usage-policy.php",
            },
        },
    }


class PublicationReadinessTests(unittest.TestCase):
    def test_test_attestation_binds_source_junit_time_and_planning_modules(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_file = root / "src/telemetry_yield/planning/example.py"
            test_file = root / "tests/test_planning_example.py"
            source_file.parent.mkdir(parents=True)
            test_file.parent.mkdir(parents=True)
            source_file.write_text("VALUE = 1\n")
            test_file.write_text("def test_example():\n    assert True\n")
            records = []
            for path in (source_file, test_file):
                body = path.read_bytes()
                records.append(
                    {
                        "path": path.relative_to(root).as_posix(),
                        "byte_length": len(body),
                        "sha256": hashlib.sha256(body).hexdigest(),
                    }
                )
            records.sort(key=lambda item: item["path"])
            identity = hashlib.sha256(
                json.dumps(
                    records, sort_keys=True, separators=(",", ":")
                ).encode()
            ).hexdigest()
            source_manifest = root / "source.json"
            source_manifest.write_text(
                json.dumps(
                    {
                        "schema_version": "observation-planning-source-manifest-v1",
                        "created_at": "2024-01-01T00:00:00Z",
                        "project_root": str(root),
                        "file_count": len(records),
                        "source_identity_sha256": identity,
                        "files": records,
                    }
                )
            )
            junit = root / "junit.xml"
            junit.write_text(
                '<testsuites><testsuite tests="1" failures="0" errors="0" '
                'skipped="0" time="2" timestamp="2024-01-01T00:01:00+00:00">'
                '<testcase classname="tests.test_planning_example.Example" '
                'name="test_example"/></testsuite></testsuites>'
            )
            attestation = root / "attestation.json"
            write_test_attestation(
                project_root=root,
                source_manifest_path=source_manifest,
                junit_path=junit,
                output_path=attestation,
                now=datetime(2024, 1, 1, 0, 2, tzinfo=UTC),
            )
            valid = validate_test_attestation(
                attestation,
                project_root=root,
                source_manifest_path=source_manifest,
                junit_path=junit,
            )
            self.assertTrue(valid["passed"])
            self.assertEqual(valid["missing_test_modules"], [])

            junit_body = junit.read_text()
            junit.write_text(junit_body.replace('failures="0"', 'failures="1"'))
            tampered_junit = validate_test_attestation(
                attestation,
                project_root=root,
                source_manifest_path=source_manifest,
                junit_path=junit,
            )
            self.assertFalse(tampered_junit["passed"])
            self.assertFalse(tampered_junit["checks"]["junit_sha256"])

            junit.write_text(junit_body)
            source_file.write_text("VALUE = 2\n")
            with self.assertRaisesRegex(
                PlanningTestAttestationError, "source file identity mismatch"
            ):
                validate_test_attestation(
                    attestation,
                    project_root=root,
                    source_manifest_path=source_manifest,
                    junit_path=junit,
                )

    def test_runtime_contract_is_bound_to_one_frozen_model(self):
        expected = {
            "schema_version": "probability-feature-use-v1",
            "model_version": "frozen-logit-v1:abc",
            "model_artifact_payload_sha256": "1" * 64,
            "model_artifact_file_sha256": "2" * 64,
            "training_dataset_sha256": "3" * 64,
            "active_probability_features": ["space_weather_kp"],
            "evaluation_only_features": ["air_temperature_c"],
            "evaluation_only_reason": "failed frozen gate",
            "active_model_terms": ["space_weather_kp"],
            "selected_task_models": {
                "signal_present": {
                    "model": "full_covariate_logit",
                    "family": "full",
                }
            },
            "uncertainty_semantics": "empirical envelope",
            "history_aggregation": "bound observations",
            "active_feasibility_constraints": [],
        }
        contract = {
            **expected,
            "active_feasibility_constraints": [
                "receiver_antenna_frequency_range"
            ],
            "receiver_antenna": {
                "antenna_id": "yagi",
                "frequency_ranges_hz": [[430_000_000, 440_000_000]],
                "gain_supplied": False,
                "system_noise_temperature_supplied": False,
            },
        }
        probability = {"model_version": "frozen-logit-v1:abc"}
        self.assertTrue(
            _contract_matches_frozen_probability_model(
                contract, expected, probability
            )
        )
        for field, replacement in (
            ("model_artifact_file_sha256", "4" * 64),
            ("training_dataset_sha256", "5" * 64),
            ("active_model_terms", ["max_elevation_deg"]),
        ):
            tampered = json.loads(json.dumps(contract))
            tampered[field] = replacement
            self.assertFalse(
                _contract_matches_frozen_probability_model(
                    tampered, expected, probability
                )
            )
        duplicate_constraint = json.loads(json.dumps(contract))
        duplicate_constraint["active_feasibility_constraints"].append(
            "receiver_antenna_frequency_range"
        )
        self.assertFalse(
            _contract_matches_frozen_probability_model(
                duplicate_constraint, expected, probability
            )
        )

    def test_covariate_contract_accepts_active_or_explained_rejection(self):
        weather = {"air_temperature_c", "relative_humidity_percent"}
        self.assertTrue(
            _contract_accounts_for_feature_group(
                {
                    "active_probability_features": ["air_temperature_c"],
                    "evaluation_only_features": [],
                    "evaluation_only_reason": "",
                },
                weather,
            )
        )
        self.assertTrue(
            _contract_accounts_for_feature_group(
                {
                    "active_probability_features": [],
                    "evaluation_only_features": ["air_temperature_c"],
                    "evaluation_only_reason": "failed the frozen held-out gate",
                },
                weather,
            )
        )
        invalid_contracts = (
            {
                "active_probability_features": [],
                "evaluation_only_features": ["air_temperature_c"],
                "evaluation_only_reason": "",
            },
            {
                "active_probability_features": ["air_temperature_c"],
                "evaluation_only_features": ["air_temperature_c"],
                "evaluation_only_reason": "ambiguous",
            },
            {
                "active_probability_features": "air_temperature_c",
                "evaluation_only_features": [],
            },
        )
        for contract in invalid_contracts:
            self.assertFalse(
                _contract_accounts_for_feature_group(contract, weather)
            )

    def test_persistent_release_attestation_is_hash_bound_and_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "release-attestation.json"
            payload = {
                "schema_version": "observation-planning-release-attestation-v2",
                "study_id": "fixture-v4",
                "dataset_config_sha256": "a" * 64,
                "source_identity_sha256": "b" * 64,
                "attestor": "reviewer-17",
                "attested_at": "2026-09-04T00:00:00Z",
                "project_code_license": {
                    "attested": True,
                    "spdx_identifier": "Apache-2.0",
                },
                "reachable_api_contact": {
                    "attested": True,
                    "contact": "research@project-domain.pl",
                },
                "satnogs_attribution_review": {
                    "attested": True,
                    "source": "https://network.satnogs.org/api/",
                    "license": "CC-BY-SA-4.0",
                    "license_url": "https://creativecommons.org/licenses/by-sa/4.0/",
                },
                "external_data_attribution_review": (
                    external_data_attribution_review()
                ),
            }
            path.write_text(json.dumps(payload), encoding="utf-8")

            valid = _release_attestation_evidence(
                path,
                study_id="fixture-v4",
                dataset_config_sha256="a" * 64,
                source_identity_sha256="b" * 64,
            )
            self.assertTrue(valid["valid"])
            self.assertEqual(
                valid["artifact_sha256"], hashlib.sha256(path.read_bytes()).hexdigest()
            )
            self.assertTrue(
                all(item["passed"] for item in valid["gates"].values())
            )

            payload["reachable_api_contact"]["contact"] = "research@example.invalid"
            path.write_text(json.dumps(payload), encoding="utf-8")
            placeholder = _release_attestation_evidence(
                path,
                study_id="fixture-v4",
                dataset_config_sha256="a" * 64,
                source_identity_sha256="b" * 64,
            )
            self.assertFalse(placeholder["valid"])
            self.assertTrue(
                placeholder["gates"]["project_code_license_attested"]["passed"]
            )
            self.assertFalse(
                placeholder["gates"]["reachable_api_contact_attested"]["passed"]
            )
            self.assertTrue(
                placeholder["gates"]["satnogs_attribution_review_attested"]["passed"]
            )
            self.assertTrue(
                placeholder["gates"]["external_data_attribution_review_attested"][
                    "passed"
                ]
            )

            payload["reachable_api_contact"]["contact"] = (
                "research@project-domain.pl"
            )
            payload["external_data_attribution_review"]["sources"]["gfz_kp"][
                "license"
            ] = "CC0-1.0"
            path.write_text(json.dumps(payload), encoding="utf-8")
            external_mismatch = _release_attestation_evidence(
                path,
                study_id="fixture-v4",
                dataset_config_sha256="a" * 64,
                source_identity_sha256="b" * 64,
            )
            self.assertFalse(external_mismatch["valid"])
            self.assertTrue(
                external_mismatch["gates"]["project_code_license_attested"][
                    "passed"
                ]
            )
            self.assertTrue(
                external_mismatch["gates"]["reachable_api_contact_attested"][
                    "passed"
                ]
            )
            self.assertTrue(
                external_mismatch["gates"]["satnogs_attribution_review_attested"][
                    "passed"
                ]
            )
            self.assertFalse(
                external_mismatch["gates"][
                    "external_data_attribution_review_attested"
                ]["passed"]
            )

            wrong_binding = _release_attestation_evidence(
                path,
                study_id="fixture-v4",
                dataset_config_sha256="c" * 64,
                source_identity_sha256="b" * 64,
            )
            self.assertFalse(wrong_binding["valid"])
            self.assertTrue(
                all(not item["passed"] for item in wrong_binding["gates"].values())
            )

    def test_v4_extension_requires_real_covariates_external_groups_and_month(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            method_contract = (
                root / "configs/observation-planning-method-contract-v4.json"
            )
            method_contract.parent.mkdir(parents=True)
            method_contract.write_bytes(
                (
                    Path(__file__).resolve().parents[1]
                    / "configs/observation-planning-method-contract-v4.json"
                ).read_bytes()
            )

            def public_cache_entry(cache, key, body, url, retrieved_at):
                cache.mkdir(parents=True, exist_ok=True)
                (cache / f"{key}.json").write_bytes(body)
                (cache / f"{key}.meta.json").write_text(
                    json.dumps(
                        {
                            "schema_version": "public-covariate-http-cache-v1",
                            "url": url,
                            "retrieved_at": retrieved_at.isoformat(),
                            "body_sha256": hashlib.sha256(body).hexdigest(),
                        }
                    )
                )

            publication_path = root / "publication.json"
            publication = {
                "study_id": "fixture-v4",
                "start": "2024-01-01T00:00:00Z",
                "end": "2024-04-01T00:00:00Z",
                "sampling_rule": "Outcome-blind common UTC panel fixture",
                "sampling_intervals": [
                    {"start": "2024-01-01T00:00:00Z", "end": "2024-01-15T00:00:00Z"},
                    {"start": "2024-01-15T00:00:00Z", "end": "2024-01-29T00:00:00Z"},
                    {"start": "2024-01-29T00:00:00Z", "end": "2024-02-12T00:00:00Z"},
                    {"start": "2024-02-12T00:00:00Z", "end": "2024-03-01T00:00:00Z"},
                    {"start": "2024-03-01T00:00:00Z", "end": "2024-04-01T00:00:00Z"},
                ],
                "targets": [{"norad_id": 10_000 + index} for index in range(50)],
                "external_validation_norad_ids": [
                    10_000 + index for index in range(10)
                ],
                "external_station_holdout_fraction": 0.2,
                "minimum_external_test_rows": 1,
            }
            publication_path.write_text(json.dumps(publication))
            publication_sha = hashlib.sha256(publication_path.read_bytes()).hexdigest()
            historical_archive = root / "historical-covariates.json"
            captured = datetime(2024, 1, 1, tzinfo=UTC)
            historical_weather_body = b'{"historical":"weather"}'
            historical_kp_body = b'{"historical":"kp"}'
            historical_weather = WeatherRecord(
                station_id=12,
                latitude_deg=52,
                longitude_deg=21,
                valid_at=captured,
                retrieved_at=captured + timedelta(days=40),
                source="Open-Meteo Historical Forecast API",
                source_response_sha256=hashlib.sha256(
                    historical_weather_body
                ).hexdigest(),
                air_temperature_c=5,
            )
            historical_kp = KpRecord(
                valid_at=captured,
                retrieved_at=captured + timedelta(days=40),
                kp=2,
                status="def",
                source="GFZ Potsdam Kp API",
                source_response_sha256=hashlib.sha256(historical_kp_body).hexdigest(),
            )
            write_covariate_archive(
                historical_archive,
                weather=(historical_weather,),
                kp=(historical_kp,),
            )
            historical_cache = root / "historical-cache"
            public_cache_entry(
                historical_cache / "open-meteo-historical-forecast",
                "weather",
                historical_weather_body,
                "https://historical-forecast-api.open-meteo.com/v1/forecast",
                captured + timedelta(days=40),
            )
            public_cache_entry(
                historical_cache / "gfz",
                "kp",
                historical_kp_body,
                "https://kp.gfz.de/app/json/",
                captured + timedelta(days=40),
            )
            historical_evidence = root / "historical-evidence.json"
            write_covariate_evidence_manifest(
                historical_archive, historical_cache, historical_evidence
            )
            dataset = {
                "study_id": "fixture-v4",
                "config_sha256": publication_sha,
                "normalized_rows": 100,
                "normalized_satellites": 50,
                "signal_satellites": 50,
                "conditional_decode_satellites": 50,
                "per_target_counts": [
                    {
                        "norad_id": 10_000 + index,
                        "normalized_rows": 2,
                    }
                    for index in range(50)
                ],
                "covariate_archive_path": historical_archive.name,
                "covariate_archive_sha256": hashlib.sha256(
                    historical_archive.read_bytes()
                ).hexdigest(),
                "feature_availability": {
                    "historical_station_location_captured_present": 90,
                    "current_api_station_location_fallback_present": 10,
                    "client_metadata_parse_status_counts": {
                        "parsed_location_valid": 90,
                        "absent": 10,
                    },
                    "historical_receiver_configuration_present": 80,
                    "historical_receiver_configuration_value_counts": {
                        "captured_receiver_driver": 80,
                        "captured_receiver_rf_gain_db": 70,
                    },
                    "historical_terrestrial_weather_snapshot_present": 90,
                    "historical_terrestrial_weather_any_value_present": 90,
                    "historical_terrestrial_weather_complete_vector_present": 90,
                    "historical_space_weather_snapshot_present": 100,
                },
                "covariate_source_summary": {
                    "weather_records": 1,
                    "weather_sources": {
                        "Open-Meteo Historical Forecast API": 1
                    },
                    "space_weather_records": 1,
                    "space_weather_sources": {"GFZ Potsdam Kp API": 1},
                    "hardware_records": 0,
                    "hardware_sources": {},
                },
            }
            external = {
                "satellite": {
                    "status": "evaluated",
                    "prediction_rows": 20,
                    "test_observation_rows": 5,
                    "group_disjoint": True,
                    "future_only": True,
                    "test_start": "2024-03-01T00:00:00Z",
                    "metrics": {
                        "full_logit": {
                            "pooled": {
                                "count": 5,
                                "positives": 2,
                                "brier": 0.1,
                                "auroc": 0.8,
                            }
                        }
                    },
                    "held_out_group_ids": [10_000 + index for index in range(10)],
                    "minimum_evaluated_groups": 5,
                    "evaluated_group_ids": [10_000 + index for index in range(5)],
                    "evaluated_group_count": 5,
                    "held_out_group_coverage_fraction": 0.5,
                },
                "station": {
                    "status": "evaluated",
                    "prediction_rows": 20,
                    "test_observation_rows": 5,
                    "group_disjoint": True,
                    "future_only": True,
                    "test_start": "2024-03-01T00:00:00Z",
                    "metrics": {
                        "full_logit": {
                            "pooled": {
                                "count": 5,
                                "positives": 2,
                                "brier": 0.1,
                                "auroc": 0.8,
                            }
                        }
                    },
                    "held_out_group_ids": [12, 13, 14, 15, 16],
                    "minimum_evaluated_groups": 5,
                    "evaluated_group_ids": [12, 13, 14, 15, 16],
                    "evaluated_group_count": 5,
                    "held_out_group_coverage_fraction": 1.0,
                },
            }
            ablation_predictions = root / "covariate-ablation-predictions.jsonl"
            ablation_predictions.write_text(
                json.dumps(
                    {
                        "observation_id": 1,
                        "model": "operational_weather_logit",
                        "outcome": 1,
                        "probability": 0.8,
                    }
                )
                + "\n"
            )
            covariate_bootstrap = root / "covariate-bootstrap.json"
            covariate_bootstrap.write_text(
                json.dumps(
                    {
                        "schema_version": "paired-cluster-bootstrap-v1",
                        "replicates": [0.01] * 1000,
                    }
                )
            )
            bootstrap_comparison = {
                "cluster_count": 5,
                "replicate_count": 1000,
                "artifact_path": covariate_bootstrap.name,
                "artifact_sha256": hashlib.sha256(
                    covariate_bootstrap.read_bytes()
                ).hexdigest(),
                "passes_outcome_blind_deployment_candidate_gate": True,
            }
            covariate_ablation = {
                "train_count": 80,
                "test_count": 20,
                "available_families": [
                    "operational_logit",
                    "operational_weather_logit",
                ],
                "metrics": {
                    "operational_logit": {"brier": 0.2},
                    "operational_weather_logit": {"brier": 0.19},
                },
                "paired_bootstrap_vs_operational": {
                    "satellite_cluster": {
                        "operational_weather_logit": bootstrap_comparison
                    },
                    "station_cluster_sensitivity": {
                        "operational_weather_logit": bootstrap_comparison
                    },
                },
                "deployment_candidate_gate": {
                    "by_model": {"operational_weather_logit": True}
                },
                "predictions_path": ablation_predictions.name,
                "predictions_sha256": hashlib.sha256(
                    ablation_predictions.read_bytes()
                ).hexdigest(),
            }
            composed_probability_metrics = {
                "count": 5,
                "positives": 2,
                "brier": 0.1,
                "auroc": 0.8,
            }
            composed_decision_metrics = {
                "count": 5,
                "accuracy": 0.8,
                "true_positive": 2,
                "true_negative": 2,
                "false_positive": 1,
                "false_negative": 0,
            }
            composed_external = {
                label: {
                    **split,
                    "metrics": {
                        "full_logit": composed_probability_metrics,
                    },
                    "decision_metrics_at_0_5": {
                        "full_logit": composed_decision_metrics,
                    },
                }
                for label, split in external.items()
            }
            evaluation = {
                "predeclared_temporal_cutoff": "2024-03-01T00:00:00Z",
                "tasks": {
                    "signal_present": {
                        "status": "evaluated",
                        "temporal": {
                            "train_end": "2024-02-29T23:59:59Z",
                            "test_start": "2024-03-01T00:00:00Z",
                        },
                        "external_validation": external,
                        "covariate_ablation": covariate_ablation,
                    },
                    "decode_success_given_signal": {
                        "status": "evaluated",
                        "temporal": {
                            "train_end": "2024-02-29T23:59:59Z",
                            "test_start": "2024-03-01T00:00:00Z",
                        },
                        "external_validation": external,
                        "covariate_ablation": covariate_ablation,
                    },
                },
                "composed_reception": {
                    "status": "evaluated",
                    "definition": "P(signal) * P(decode artifact | signal)",
                    "temporal": {
                        "test_count": 5,
                        "metrics": {
                            "full_logit": composed_probability_metrics,
                        },
                        "decision_metrics_at_0_5": {
                            "full_logit": composed_decision_metrics,
                        },
                    },
                    "external_validation": composed_external,
                    "scheduling_replay": {
                        "outcome_task": "reception_success",
                        "candidate_count": 5,
                        "metrics": {
                            scheduler: {}
                            for scheduler in (
                                "chronological",
                                "maximum_elevation",
                                "longest_duration",
                                "probability_milp",
                                "oracle_milp_upper_bound",
                            )
                        },
                    },
                },
            }
            campaign_path = root / "campaign.json"
            runtime_contact = root / "runtime-contact.json"
            runtime_contact.write_text(
                json.dumps(
                    {
                        "schema_version": "observation-planning-api-contact-v2",
                        "contact": "research@university.edu",
                        "open_meteo_free_api_usage": {
                            "attested": True,
                            "terms_url": "https://open-meteo.com/en/terms",
                            "use_category": "non-commercial-research",
                        },
                    }
                )
            )
            runtime_contact_sha = hashlib.sha256(
                runtime_contact.read_bytes()
            ).hexdigest()
            campaign = {
                "schema_version": "observation-planning-prospective-config-v1",
                "campaign_id": "fixture-month",
                "registered_at": "2024-01-01T00:00:00Z",
                "start": "2024-01-02T00:00:00Z",
                "end": "2024-02-02T00:00:00Z",
                # Station 99 models a registered backup that is unavailable
                # during every captured plan. Publication evidence is required
                # for stations actually assigned, not unused offline backups.
                "station_ids": [12, 99],
                "target_norad_ids": [10_000 + index for index in range(50)],
                "minimum_plan_commit_days": 25,
                "minimum_reconciled_outcomes": 3,
                "minimum_scored_targets": 1,
                "minimum_scored_stations": 1,
                "minimum_signal_positive_outcomes": 1,
                "minimum_signal_negative_outcomes": 1,
                "minimum_conditional_decode_positive_outcomes": 1,
                "minimum_conditional_decode_negative_outcomes": 1,
                "runtime_user_agent": (
                    "telemetry-yield-research/0.2 "
                    "contact=research@university.edu"
                ),
                "historical_dependencies": {
                    "runtime_api_contact": {
                        "path": runtime_contact.name,
                        "byte_length": runtime_contact.stat().st_size,
                        "sha256": runtime_contact_sha,
                    }
                },
            }
            campaign_path.write_text(json.dumps(campaign))
            ledger = root / "ledger.jsonl"
            initialize_campaign(campaign_path, ledger, now=lambda: captured)
            forecast = root / "forecast.json"
            start = datetime(2024, 1, 2, tzinfo=UTC)
            forecast_weather_body = b'{"forecast":"weather"}'
            forecast_kp_body = b'{"forecast":"kp"}'
            station_body = b'{"id":12,"antenna":"yagi"}'
            forecast_weather_hash = hashlib.sha256(forecast_weather_body).hexdigest()
            forecast_kp_hash = hashlib.sha256(forecast_kp_body).hexdigest()
            station_hash = hashlib.sha256(station_body).hexdigest()
            forecast_weather = tuple(
                WeatherRecord(
                    station_id=12,
                    latitude_deg=52,
                    longitude_deg=21,
                    valid_at=start + timedelta(days=day, hours=2),
                    retrieved_at=captured,
                    source="Open-Meteo Forecast API",
                    source_response_sha256=forecast_weather_hash,
                    air_temperature_c=5 + day,
                )
                for day in range(25)
            )
            forecast_kp = tuple(
                KpRecord(
                    valid_at=start + timedelta(days=day),
                    retrieved_at=captured,
                    kp=2,
                    status="predicted",
                    source="NOAA SWPC planetary K-index forecast",
                    source_response_sha256=forecast_kp_hash,
                )
                for day in range(25)
            )
            forecast_hardware = (
                HardwareRecord(
                    station_id=12,
                    configuration_id="station-12-v1",
                    antenna_type="yagi",
                    frequency_ranges_hz=((430_000_000, 440_000_000),),
                    effective_from=captured,
                    known_at=captured,
                    source="SatNOGS Network station detail API",
                    evidence_sha256=station_hash,
                ),
            )
            write_covariate_archive(
                forecast,
                weather=forecast_weather,
                kp=forecast_kp,
                hardware=forecast_hardware,
            )
            forecast_cache = root / "forecast-cache"
            public_cache_entry(
                forecast_cache / "open-meteo",
                "weather",
                forecast_weather_body,
                "https://api.open-meteo.com/v1/forecast",
                captured,
            )
            public_cache_entry(
                forecast_cache / "noaa",
                "kp",
                forecast_kp_body,
                "https://services.swpc.noaa.gov/products/noaa-planetary-k-index-forecast.json",
                captured,
            )
            station_cache = forecast_cache / "satnogs-stations"
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
            forecast_evidence = root / "forecast-evidence.json"
            write_covariate_evidence_manifest(
                forecast, forecast_cache, forecast_evidence
            )
            history_dataset = root / "history.jsonl"
            history_dataset.write_text("{}\n")
            probability_model = root / "probability-model.json"
            probability_model.write_text(
                json.dumps(
                    {"schema_version": "planning-learned-probability-model-v1"}
                )
            )
            history_dataset_sha = hashlib.sha256(
                history_dataset.read_bytes()
            ).hexdigest()
            probability_model_sha = hashlib.sha256(
                probability_model.read_bytes()
            ).hexdigest()
            expected_probability_contract = {
                "schema_version": "probability-feature-use-v1",
                "model_version": "hierarchical-beta-signal-v2",
                "model_artifact_payload_sha256": "1" * 64,
                "model_artifact_file_sha256": probability_model_sha,
                "training_dataset_sha256": history_dataset_sha,
                "active_probability_features": ["space_weather_kp"],
                "evaluation_only_features": ["air_temperature_c"],
                "evaluation_only_reason": "frozen learned out-of-sample ablation",
                "active_model_terms": ["space_weather_kp"],
                "selected_task_models": {
                    "signal_present": {
                        "model": "operational_weather_logit",
                        "family": "weather",
                    }
                },
                "uncertainty_semantics": "empirical envelope",
                "history_aggregation": "bound observations",
                "active_feasibility_constraints": [],
            }
            model_artifact = {
                "path": probability_model.name,
                "sha256": probability_model_sha,
            }
            history_artifact = {
                "path": history_dataset.name,
                "sha256": history_dataset_sha,
            }
            artifact = {
                "path": forecast.name,
                "sha256": hashlib.sha256(forecast.read_bytes()).hexdigest(),
            }
            evidence_artifact = {
                "path": forecast_evidence.name,
                "sha256": hashlib.sha256(forecast_evidence.read_bytes()).hexdigest(),
            }

            def tle_line(base, norad_id):
                body = base[:2] + f"{norad_id:05d}" + base[7:-1]
                checksum = sum(
                    int(character)
                    if character.isdigit()
                    else 1
                    if character == "-"
                    else 0
                    for character in body
                ) % 10
                return body + str(checksum)

            base_line1 = (
                "1 25544U 98067A   24123.50000000  .00016717  00000+0  "
                "30210-3 0  9997"
            )
            base_line2 = (
                "2 25544  51.6400 100.0000 0005000  50.0000 310.0000 "
                "15.50000000450003"
            )

            def tle_evidence(created_at):
                evidence = {}
                fingerprints = {}
                for norad_id in campaign["target_norad_ids"]:
                    line1 = tle_line(base_line1, norad_id)
                    line2 = tle_line(base_line2, norad_id)
                    source = (
                        "https://celestrak.org/NORAD/elements/gp.php?"
                        f"CATNR={norad_id}&FORMAT=TLE"
                    )
                    snapshot = parse_tle_text(
                        f"fixture-{norad_id}\n{line1}\n{line2}",
                        norad_id=norad_id,
                        fetched_at=created_at,
                        source=source,
                    )
                    fingerprints[str(norad_id)] = snapshot.fingerprint
                    evidence[str(norad_id)] = {
                        "name": snapshot.name,
                        "line1": snapshot.line1,
                        "line2": snapshot.line2,
                        "epoch": snapshot.epoch.isoformat(),
                        "fetched_at": created_at.isoformat(),
                        "source": source,
                        "fingerprint": snapshot.fingerprint,
                    }
                return fingerprints, evidence

            def plan_artifact(filename, plan_id, revision, day=0):
                assignment_start = start + timedelta(days=day, hours=2)
                assignment_end = assignment_start + timedelta(minutes=5)
                created_at = (
                    captured + timedelta(hours=12)
                    if plan_id == "pre-start-plan"
                    else start + timedelta(days=day, hours=1)
                )
                fingerprints, evidence = tle_evidence(created_at)
                plan_path = root / filename
                plan_path.write_text(
                    json.dumps(
                        {
                            "schema_version": "observation-plan-v2",
                            "plan_id": plan_id,
                            "revision": revision,
                            "created_at": created_at.isoformat(),
                            "tle_fingerprints": fingerprints,
                            "diagnostics": {"tle_snapshot_evidence": evidence},
                            "assignments": [
                                {
                                    "norad_id": 10_000,
                                    "start": assignment_start.isoformat(),
                                    "end": assignment_end.isoformat(),
                                    "satnogs_station_id": 12,
                                    "resource_id": "rx-1",
                                    "frequency_hz": 435_000_000,
                                    "probability": {
                                        "model_version": "hierarchical-beta-signal-v2"
                                    },
                                    "metadata": {
                                        "culmination_at": assignment_start.isoformat(),
                                        "feature_snapshot": {
                                            "satnogs_station_id": 12,
                                            "frequency_hz": 435_000_000,
                                            "air_temperature_c": 5 + day,
                                            "relative_humidity_percent": None,
                                            "surface_pressure_kpa": None,
                                            "wind_speed_m_s": None,
                                            "precipitation_corrected": None,
                                            "space_weather_kp": 2,
                                            "environment_source": (
                                                "Open-Meteo Forecast API + NOAA SWPC "
                                                "planetary K-index forecast"
                                            ),
                                            "antenna_type": "yagi",
                                            "antenna_frequency_supported": True,
                                            "receive_antenna_gain_dbi": None,
                                            "system_noise_temperature_k": None,
                                        },
                                        "feature_use_contract": {
                                            "schema_version": "probability-feature-use-v1",
                                            "model_version": (
                                                "hierarchical-beta-signal-v2"
                                            ),
                                            "active_probability_features": [
                                                "space_weather_kp"
                                            ],
                                            "evaluation_only_features": [
                                                "air_temperature_c"
                                            ],
                                            "evaluation_only_reason": (
                                                "frozen learned out-of-sample ablation"
                                            ),
                                            "model_artifact_payload_sha256": "1" * 64,
                                            "model_artifact_file_sha256": (
                                                probability_model_sha
                                            ),
                                            "training_dataset_sha256": (
                                                history_dataset_sha
                                            ),
                                            "active_model_terms": [
                                                "space_weather_kp"
                                            ],
                                            "selected_task_models": {
                                                "signal_present": {
                                                    "model": (
                                                        "operational_weather_logit"
                                                    ),
                                                    "family": "weather",
                                                }
                                            },
                                            "uncertainty_semantics": (
                                                "empirical envelope"
                                            ),
                                            "history_aggregation": (
                                                "bound observations"
                                            ),
                                            "active_feasibility_constraints": [
                                                "receiver_antenna_frequency_range"
                                            ],
                                            "receiver_antenna": {
                                                "antenna_id": "yagi",
                                                "frequency_ranges_hz": [
                                                    [430_000_000, 440_000_000]
                                                ],
                                                "gain_supplied": False,
                                                "system_noise_temperature_supplied": False,
                                            },
                                        }
                                    },
                                }
                            ],
                        }
                    )
                )
                return (
                    plan_path,
                    hashlib.sha256(plan_path.read_bytes()).hexdigest(),
                    fingerprints,
                )

            plan, plan_sha, prestart_tles = plan_artifact(
                "plan.json", "pre-start-plan", 1
            )
            daily_plans = []
            source_records = [
                {
                    "path": f"source-{index}.py",
                    "byte_length": index,
                    "sha256": f"{index:064x}",
                }
                for index in range(20)
            ]
            source_identity = hashlib.sha256(
                json.dumps(
                    source_records, sort_keys=True, separators=(",", ":")
                ).encode()
            ).hexdigest()

            def runtime_source_artifact(occurred_at, planning_started_at):
                source_path = root / f"source-{occurred_at.date()}.json"
                source_path.write_text(
                    json.dumps(
                        {
                            "schema_version": "observation-planning-source-manifest-v1",
                            "created_at": (
                                planning_started_at - timedelta(seconds=1)
                            ).isoformat().replace("+00:00", "Z"),
                            "project_root": str(root),
                            "file_count": len(source_records),
                            "selection_snapshot_count": 0,
                            "source_identity_sha256": source_identity,
                            "files": source_records,
                        }
                    )
                )
                return {
                    "path": source_path.name,
                    "sha256": hashlib.sha256(source_path.read_bytes()).hexdigest(),
                }

            append_event(
                ledger,
                campaign_id="fixture-month",
                event_type="plan_committed",
                occurred_at=captured + timedelta(hours=12),
                payload={
                    "plan_path": plan.name,
                    "plan_sha256": plan_sha,
                    "plan_id": "pre-start-plan",
                    "revision": 1,
                    "tle_fingerprints": prestart_tles,
                    "input_artifacts": [
                        artifact,
                        evidence_artifact,
                        model_artifact,
                        history_artifact,
                    ],
                    "assignments": [
                        {
                            "opportunity_id": "pre-start",
                            "p_signal_present": 0.8,
                            "p_decode_given_signal": 0.9,
                            "p_success": 0.8,
                        }
                    ],
                },
            )
            for day in range(25):
                planning_started_at = start + timedelta(days=day, hours=1)
                occurred_at = planning_started_at + timedelta(minutes=15)
                daily_plan, daily_plan_sha, daily_tles = plan_artifact(
                    f"plan-{day}.json",
                    "prospective-fixture-month",
                    day + 1,
                    day,
                )
                daily_plans.append(daily_plan)
                blocker_snapshot = root / f"blockers-{day}.json"
                blocker_snapshot.write_text(
                    json.dumps(
                        {
                            "schema_version": "observation-planning-blockers-v1",
                            "blockers": [],
                        }
                    )
                )
                target_override_snapshot = root / f"target-overrides-{day}.json"
                target_override_snapshot.write_text(
                    json.dumps(
                        {
                            "schema_version": (
                                "observation-planning-target-overrides-v1"
                            ),
                            "targets": [],
                        }
                    )
                )
                append_event(
                    ledger,
                    campaign_id="fixture-month",
                    event_type="plan_committed",
                    occurred_at=occurred_at,
                    payload={
                        "plan_path": daily_plan.name,
                        "plan_sha256": daily_plan_sha,
                        "plan_id": "prospective-fixture-month",
                        "revision": day + 1,
                        "tle_fingerprints": daily_tles,
                        "input_artifacts": [
                            artifact,
                            evidence_artifact,
                            model_artifact,
                            history_artifact,
                            {
                                "path": blocker_snapshot.name,
                                "sha256": hashlib.sha256(
                                    blocker_snapshot.read_bytes()
                                ).hexdigest(),
                            },
                            {
                                "path": target_override_snapshot.name,
                                "sha256": hashlib.sha256(
                                    target_override_snapshot.read_bytes()
                                ).hexdigest(),
                            },
                            runtime_source_artifact(
                                occurred_at, planning_started_at
                            ),
                        ],
                        "assignments": [
                            {
                                "opportunity_id": f"pass-{day}",
                                "start": (
                                    start + timedelta(days=day, hours=2)
                                ).isoformat(),
                                "end": (
                                    start
                                    + timedelta(days=day, hours=2, minutes=5)
                                ).isoformat(),
                                "p_signal_present": 0.8,
                                "p_decode_given_signal": 0.9,
                                "p_success": 0.8,
                                "model_version": "fixture-model",
                                "norad_id": 10_000,
                                "station_id": "satnogs-12:Fixture",
                                "satnogs_station_id": 12,
                                "transmitter_uuid": "tx-selected",
                                "modulation": "BPSK",
                            }
                        ],
                    },
                )
            network_snapshot = root / "network-outcomes.json"
            network_snapshot.write_text(
                json.dumps(
                    {
                        "observations": [
                            {
                                "id": 1,
                                "end": (
                                    start
                                    + timedelta(days=24, hours=2, minutes=5)
                                ).isoformat(),
                                "ground_station": 12,
                                "norad_cat_id": 10_000,
                                "transmitter_uuid": "tx-selected",
                                "waterfall_status": "with-signal",
                                "demoddata": [{"payload": "decoded"}],
                            }
                        ]
                    }
                )
            )
            network_snapshot_sha256 = hashlib.sha256(
                network_snapshot.read_bytes()
            ).hexdigest()
            outcome_source = root / "outcomes.json"
            outcome_source.write_text(
                json.dumps(
                    [
                        {
                            "opportunity_id": "pass-22",
                            "observed_at": (
                                start
                                + timedelta(days=22, hours=2, minutes=5)
                            ).isoformat(),
                            "signal_present": 0,
                            "decode_success_given_signal": None,
                        },
                        {
                            "opportunity_id": "pass-23",
                            "observed_at": (
                                start
                                + timedelta(days=23, hours=2, minutes=5)
                            ).isoformat(),
                            "signal_present": 1,
                            "decode_success_given_signal": 0,
                        },
                        {
                            "opportunity_id": "pass-24",
                            "observation_id": 1,
                            "observed_at": (
                                start
                                + timedelta(days=24, hours=2, minutes=5)
                            ).isoformat(),
                            "signal_present": 1,
                            "decode_success_given_signal": 1,
                            "source": "SatNOGS Network natural shadow-match",
                            "raw_source_artifact_path": str(network_snapshot),
                            "raw_source_artifact_sha256": network_snapshot_sha256,
                        }
                    ]
                )
            )
            record_outcomes(
                campaign_path,
                ledger,
                outcome_source,
                now=lambda: start + timedelta(days=25),
            )
            checks, summary = _v4_extension_checks(
                publication_config_path=publication_path,
                prospective_config_path=campaign_path,
                prospective_ledger_path=ledger,
                dataset=dataset,
                evaluation=evaluation,
                source={
                    "project_root": str(root),
                    "source_identity_sha256": source_identity,
                },
                covariate_evidence_path=historical_evidence,
                probability_model_path=probability_model,
                history_dataset_path=history_dataset,
                frozen_probability_contract=expected_probability_contract,
            )
            weak_evaluation = json.loads(json.dumps(evaluation))
            for task in weak_evaluation["tasks"].values():
                station_split = task["external_validation"]["station"]
                station_split["evaluated_group_ids"] = [12]
                station_split["evaluated_group_count"] = 1
            weak_checks, _ = _v4_extension_checks(
                publication_config_path=publication_path,
                prospective_config_path=campaign_path,
                prospective_ledger_path=ledger,
                dataset=dataset,
                evaluation=weak_evaluation,
                source={
                    "project_root": str(root),
                    "source_identity_sha256": source_identity,
                },
                covariate_evidence_path=historical_evidence,
            )
            empty_weather_values = json.loads(json.dumps(dataset))
            empty_weather_values["feature_availability"][
                "historical_terrestrial_weather_any_value_present"
            ] = 0
            empty_weather_values["feature_availability"][
                "historical_terrestrial_weather_complete_vector_present"
            ] = 0
            empty_weather_checks, _ = _v4_extension_checks(
                publication_config_path=publication_path,
                prospective_config_path=campaign_path,
                prospective_ledger_path=ledger,
                dataset=empty_weather_values,
                evaluation=evaluation,
                source={
                    "project_root": str(root),
                    "source_identity_sha256": source_identity,
                },
                covariate_evidence_path=historical_evidence,
            )
            source_mismatch_checks, _ = _v4_extension_checks(
                publication_config_path=publication_path,
                prospective_config_path=campaign_path,
                prospective_ledger_path=ledger,
                dataset=dataset,
                evaluation=evaluation,
                source={
                    "project_root": str(root),
                    "source_identity_sha256": "0" * 64,
                },
                covariate_evidence_path=historical_evidence,
            )
            outcome_source_body = outcome_source.read_text()
            outcome_source.write_text("[]")
            tampered_outcome_checks, _ = _v4_extension_checks(
                publication_config_path=publication_path,
                prospective_config_path=campaign_path,
                prospective_ledger_path=ledger,
                dataset=dataset,
                evaluation=evaluation,
                source={
                    "project_root": str(root),
                    "source_identity_sha256": source_identity,
                },
                covariate_evidence_path=historical_evidence,
            )
            outcome_source.write_text(outcome_source_body)
            exact_assignments = json.loads(daily_plans[0].read_text())["assignments"]
            exact_valid, exact_counts = _audit_assignment_covariates(
                exact_assignments,
                weather=forecast_weather,
                kp=forecast_kp,
                hardware=forecast_hardware,
                as_of=(start + timedelta(hours=1)).isoformat(),
            )
            empty_blockers_valid, empty_blocker_count = _audit_assignment_blockers(
                exact_assignments,
                {
                    "schema_version": "observation-planning-blockers-v1",
                    "blockers": [],
                },
                campaign_station_ids=campaign["station_ids"],
            )
            overlapping_blocker_valid, _ = _audit_assignment_blockers(
                exact_assignments,
                {
                    "schema_version": "observation-planning-blockers-v1",
                    "blockers": [
                        {
                            "blocker_id": "maintenance-overlap",
                            "station_id": 12,
                            "resource_id": "rx-1",
                            "start": (
                                start + timedelta(hours=2, minutes=1)
                            ).isoformat(),
                            "end": (
                                start + timedelta(hours=2, minutes=4)
                            ).isoformat(),
                            "kind": "maintenance",
                        }
                    ],
                },
                campaign_station_ids=campaign["station_ids"],
            )
            overridden_assignments = json.loads(json.dumps(exact_assignments))
            overridden_assignments[0].update(
                {
                    "priority": 4.0,
                    "exclusive_transmission": False,
                    "nominal_unique_samples": 600.0,
                }
            )
            overridden_assignments[0]["metadata"]["feature_snapshot"].update(
                {
                    "transmit_power_dbw": 3.0,
                    "transmit_antenna_gain_dbi": 1.0,
                }
            )
            target_override_payload = {
                "schema_version": "observation-planning-target-overrides-v1",
                "targets": [
                    {
                        "norad_id": 10_000,
                        "transmit_power_dbw": 3.0,
                        "transmit_antenna_gain_dbi": 1.0,
                        "samples_per_second": 2.0,
                        "priority": 4.0,
                        "exclusive_transmission": False,
                        "transmission_rule": {
                            "weekdays_utc": [1],
                            "month_days_utc": [2],
                            "daily_windows_utc": [["01:00:00", "03:00:00"]],
                            "transmit_regions": [
                                {
                                    "south_deg": -90,
                                    "north_deg": 90,
                                    "west_deg": -180,
                                    "east_deg": 180,
                                    "label": "global",
                                }
                            ],
                            "probability_when_allowed": 0.95,
                        },
                    }
                ],
            }
            target_override_valid, target_override_count = (
                _audit_assignment_target_overrides(
                    overridden_assignments,
                    target_override_payload,
                    campaign_target_ids=campaign["target_norad_ids"],
                )
            )
            invalid_target_override = json.loads(
                json.dumps(target_override_payload)
            )
            invalid_target_override["targets"][0]["transmission_rule"][
                "weekdays_utc"
            ] = [4]
            invalid_target_override_valid, _ = _audit_assignment_target_overrides(
                overridden_assignments,
                invalid_target_override,
                campaign_target_ids=campaign["target_norad_ids"],
            )
            tampered_assignments = json.loads(json.dumps(exact_assignments))
            tampered_assignments[0]["metadata"]["feature_snapshot"][
                "air_temperature_c"
            ] = 99
            tampered_exact_valid, _ = _audit_assignment_covariates(
                tampered_assignments,
                weather=forecast_weather,
                kp=forecast_kp,
                hardware=forecast_hardware,
                as_of=(start + timedelta(hours=1)).isoformat(),
            )
            tampered_antenna = json.loads(json.dumps(exact_assignments))
            tampered_antenna[0]["metadata"]["feature_use_contract"][
                "receiver_antenna"
            ]["frequency_ranges_hz"] = [[100_000_000, 110_000_000]]
            tampered_antenna_valid, _ = _audit_assignment_covariates(
                tampered_antenna,
                weather=forecast_weather,
                kp=forecast_kp,
                hardware=forecast_hardware,
                as_of=(start + timedelta(hours=1)).isoformat(),
            )
            invented_gain = json.loads(json.dumps(exact_assignments))
            invented_gain[0]["metadata"]["feature_snapshot"][
                "receive_antenna_gain_dbi"
            ] = 12
            invented_gain[0]["metadata"]["feature_use_contract"][
                "receiver_antenna"
            ]["gain_supplied"] = True
            invented_gain_valid, _ = _audit_assignment_covariates(
                invented_gain,
                weather=forecast_weather,
                kp=forecast_kp,
                hardware=forecast_hardware,
                as_of=(start + timedelta(hours=1)).isoformat(),
            )
            tampered_tle_plan = json.loads(daily_plans[0].read_text())
            tampered_tle_plan["diagnostics"]["tle_snapshot_evidence"]["10000"][
                "line1"
            ] = (
                tampered_tle_plan["diagnostics"]["tle_snapshot_evidence"]["10000"][
                    "line1"
                ][:-1]
                + str(
                    (
                        int(
                            tampered_tle_plan["diagnostics"][
                                "tle_snapshot_evidence"
                            ]["10000"]["line1"][-1]
                        )
                        + 1
                    )
                    % 10
                )
            )
            tampered_tle_valid = _audit_plan_tle_evidence(
                tampered_tle_plan,
                expected_norad_ids=campaign["target_norad_ids"],
                committed_at=(start + timedelta(hours=1)).isoformat(),
            )
            for daily_plan in daily_plans:
                daily_plan.write_text("{}")
            tampered_checks, _ = _v4_extension_checks(
                publication_config_path=publication_path,
                prospective_config_path=campaign_path,
                prospective_ledger_path=ledger,
                dataset=dataset,
                evaluation=evaluation,
                source={
                    "project_root": str(root),
                    "source_identity_sha256": source_identity,
                },
                covariate_evidence_path=historical_evidence,
            )

        self.assertTrue(
            all(check.passed for check in checks),
            [(check.name, check.detail) for check in checks if not check.passed],
        )
        self.assertEqual(summary["valid_covariate_days"], 25)
        self.assertEqual(summary["audited_covariate_use_days"], 25)
        self.assertEqual(summary["raw_covariate_evidence_days"], 25)
        self.assertEqual(summary["audited_raw_covariate_response_count"], 75)
        self.assertEqual(summary["registered_prospective_target_count"], 50)
        self.assertEqual(summary["valid_tle_evidence_event_count"], 25)
        self.assertEqual(summary["tle_evidence_days"], 25)
        self.assertEqual(summary["valid_blocker_event_count"], 25)
        self.assertEqual(summary["blocker_snapshot_count"], 25)
        self.assertEqual(summary["audited_blocker_count"], 0)
        self.assertEqual(summary["valid_target_override_event_count"], 25)
        self.assertEqual(summary["target_override_snapshot_count"], 25)
        self.assertEqual(summary["audited_target_override_count"], 0)
        self.assertEqual(summary["audited_covariate_assignment_count"], 25)
        self.assertEqual(summary["audited_weather_match_count"], 25)
        self.assertEqual(summary["audited_weather_value_count"], 25)
        self.assertEqual(summary["audited_kp_match_count"], 25)
        self.assertEqual(summary["audited_hardware_match_count"], 25)
        self.assertEqual(summary["audited_antenna_gain_value_count"], 0)
        self.assertEqual(summary["audited_antenna_noise_value_count"], 0)
        self.assertTrue(exact_valid)
        self.assertEqual(exact_counts["assignment_count"], 1)
        self.assertTrue(empty_blockers_valid)
        self.assertEqual(empty_blocker_count, 0)
        self.assertFalse(overlapping_blocker_valid)
        self.assertTrue(target_override_valid)
        self.assertEqual(target_override_count, 1)
        self.assertFalse(invalid_target_override_valid)
        self.assertFalse(tampered_exact_valid)
        self.assertFalse(tampered_antenna_valid)
        self.assertFalse(invented_gain_valid)
        self.assertFalse(tampered_tle_valid)
        self.assertEqual(summary["runtime_source_manifest_days"], 25)
        self.assertEqual(summary["runtime_source_identities"], [source_identity])
        self.assertEqual(summary["expected_campaign_plan_id"], "prospective-fixture-month")
        self.assertEqual(summary["plan_revision_lineage_event_count"], 25)
        self.assertEqual(summary["latest_plan_revision"], 25)
        self.assertEqual(
            summary["valid_probability_model_binding_event_count"], 25
        )
        self.assertTrue(summary["probability_model_binding_configuration_valid"])
        self.assertTrue(summary["prospective_report"]["publication_complete"])
        by_name = {check.name: check.passed for check in checks}
        self.assertTrue(by_name["v4_prospective_plan_revision_lineage"])
        self.assertTrue(by_name["v4_prospective_raw_covariate_evidence"])
        self.assertTrue(by_name["v4_prospective_50_target_tle_evidence"])
        self.assertTrue(by_name["v4_prospective_blocker_snapshots"])
        self.assertTrue(by_name["v4_prospective_target_override_snapshots"])
        self.assertTrue(by_name["v4_prospective_probability_model_binding"])
        self.assertTrue(by_name["v4_prospective_runtime_api_contact"])
        self.assertTrue(by_name["v4_prospective_outcome_sample_design"])
        self.assertTrue(summary["runtime_api_contact_valid"])
        weak_by_name = {check.name: check.passed for check in weak_checks}
        self.assertFalse(weak_by_name["v4_external_station_validation"])
        empty_weather_by_name = {
            check.name: check.passed for check in empty_weather_checks
        }
        self.assertFalse(empty_weather_by_name["v4_real_historical_environment"])
        source_mismatch_by_name = {
            check.name: check.passed for check in source_mismatch_checks
        }
        self.assertFalse(
            source_mismatch_by_name["v4_prospective_runtime_source_frozen"]
        )
        tampered_outcome_by_name = {
            check.name: check.passed for check in tampered_outcome_checks
        }
        self.assertFalse(
            tampered_outcome_by_name["v4_prospective_outcome_provenance"]
        )
        self.assertFalse(
            tampered_outcome_by_name["v4_prospective_outcome_sample_design"]
        )
        tampered_by_name = {check.name: check.passed for check in tampered_checks}
        self.assertFalse(tampered_by_name["v4_prospective_plan_hashes"])
        self.assertFalse(tampered_by_name["v4_prospective_plan_revision_lineage"])
        self.assertFalse(tampered_by_name["v4_prospective_covariate_use_audited"])

    def test_technical_and_human_gates_are_separate_and_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_root = root / "source-root"
            config_path = source_root / "configs" / "observation-planning-publication-v1.json"
            config_path.parent.mkdir(parents=True)
            config_path.write_text("{}\n", encoding="utf-8")
            config_digest = hashlib.sha256(config_path.read_bytes()).hexdigest()
            raw = root / "raw"
            raw.mkdir()
            raw_page = raw / "page.json"
            raw_page.write_text("[]", encoding="utf-8")
            raw_body = raw_page.read_bytes()
            snapshot_payload = {
                "study_id": "fixture",
                "complete": True,
                "raw_directory": str(raw),
                "config_file_sha256": config_digest,
                "total_rows_before_deduplication": 0,
                "responses": [
                    {
                        "relative_path": "page.json",
                        "byte_length": len(raw_body),
                        "sha256": hashlib.sha256(raw_body).hexdigest(),
                        "row_count": 0,
                    }
                ],
            }
            (root / "snapshot.json").write_text(
                json.dumps(snapshot_payload), encoding="utf-8"
            )
            source_records = []
            for index in range(20):
                path = source_root / f"source-{index:02d}.txt"
                path.write_text(f"source {index}\n", encoding="utf-8")
                body = path.read_bytes()
                source_records.append(
                    {
                        "path": path.name,
                        "byte_length": len(body),
                        "sha256": hashlib.sha256(body).hexdigest(),
                    }
                )
            source_records.append(
                {
                    "path": config_path.relative_to(source_root).as_posix(),
                    "byte_length": len(config_path.read_bytes()),
                    "sha256": config_digest,
                }
            )
            fixtures = {
                "dataset.json": {
                    "study_id": "fixture",
                    "config_path": str(config_path),
                    "dataset_sha256": "d" * 64,
                    "publication_count_gate": "pass",
                    "publication_requirements": {"known_signal": 1000},
                    "source_snapshot_manifest_sha256": hashlib.sha256(
                        (root / "snapshot.json").read_bytes()
                    ).hexdigest(),
                    "config_sha256": config_digest,
                },
                "evaluation.json": {
                    "study_id": "fixture",
                    "config_sha256": config_digest,
                    "analysis_hierarchy": {
                        "confirmatory": {
                            "task": "signal_present",
                            "split": "temporal",
                            "model": "full_logit",
                            "baseline": "global_rate",
                            "metric": "brier",
                        }
                    },
                    "tasks": {
                        "signal_present": {
                            "status": "evaluated",
                            "eligible_rows": 1000,
                            "temporal": {
                                "primary_full_logit_useful_effect": True,
                                "paired_bootstrap_vs_global_rate": {
                                    "full_logit": {"cluster_count": 10}
                                },
                                "paired_bootstrap_station_cluster_sensitivity": {
                                    "full_logit": {"cluster_count": 20}
                                },
                            },
                            "loso_satellite": {
                                "paired_bootstrap_vs_global_rate": {
                                    "full_logit": {"cluster_count": 10}
                                }
                            },
                            "loso_station": {
                                "paired_bootstrap_vs_global_rate": {
                                    "full_logit": {"cluster_count": 20}
                                }
                            },
                            "scheduling_replay": {
                                "informative_for_scheduler_comparison": False
                            },
                        },
                        "decode_success_given_signal": {
                            "status": "evaluated",
                            "eligible_rows": 300,
                            "temporal": {
                                "paired_bootstrap_vs_global_rate": {
                                    "full_logit": {"cluster_count": 8}
                                },
                                "paired_bootstrap_station_cluster_sensitivity": {
                                    "full_logit": {"cluster_count": 15}
                                },
                            },
                            "loso_satellite": {
                                "paired_bootstrap_vs_global_rate": {
                                    "full_logit": {"cluster_count": 8}
                                }
                            },
                            "loso_station": {
                                "paired_bootstrap_vs_global_rate": {
                                    "full_logit": {"cluster_count": 15}
                                }
                            },
                        },
                    },
                    "synthetic_scheduler_verification": {
                        "instances": 100,
                        "milp_exact_match_count": 100,
                        "multi_asset_instances": 50,
                        "multi_asset_milp_exact_match_count": 50,
                    },
                    "correlated_risk_sensitivity": {
                        "schema_version": "synthetic-correlated-risk-sensitivity-v1",
                        "solver_status": "optimal",
                        "scenarios": {"mild": {}, "stress": {}},
                    },
                },
                "audit.json": {
                    "passed": True,
                    "check_count": 12,
                    "failed_check_count": 0,
                },
                "source.json": {
                    "file_count": len(source_records),
                    "project_root": str(source_root),
                    "files": source_records,
                    "source_identity_sha256": hashlib.sha256(
                        json.dumps(
                            source_records, sort_keys=True, separators=(",", ":")
                        ).encode()
                    ).hexdigest(),
                },
            }
            for name, payload in fixtures.items():
                (root / name).write_text(json.dumps(payload), encoding="utf-8")
            audit_payload = {
                **fixtures["audit.json"],
                "input_artifacts": {
                    "dataset": {"sha256": "d" * 64},
                    "dataset_manifest": {
                        "sha256": hashlib.sha256(
                            (root / "dataset.json").read_bytes()
                        ).hexdigest()
                    },
                    "evaluation": {
                        "sha256": hashlib.sha256(
                            (root / "evaluation.json").read_bytes()
                        ).hexdigest()
                    },
                },
            }
            (root / "audit.json").write_text(
                json.dumps(audit_payload), encoding="utf-8"
            )
            (root / "junit.xml").write_text(
                '<testsuites><testsuite tests="500" failures="0" errors="0"/></testsuites>',
                encoding="utf-8",
            )
            (root / "results.md").write_text("result", encoding="utf-8")
            body = (root / "results.md").read_bytes()
            (root / "results.json").write_text(
                json.dumps(
                    {
                        "schema_version": "observation-planning-publication-results-manifest-v1",
                        "dataset_manifest_sha256": hashlib.sha256(
                            (root / "dataset.json").read_bytes()
                        ).hexdigest(),
                        "evaluation_sha256": hashlib.sha256(
                            (root / "evaluation.json").read_bytes()
                        ).hexdigest(),
                        "independent_audit_sha256": hashlib.sha256(
                            (root / "audit.json").read_bytes()
                        ).hexdigest(),
                        "artifacts": [
                            {
                                "relative_path": "results.md",
                                "sha256": hashlib.sha256(body).hexdigest(),
                                "bytes": len(body),
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            readiness = assess_planning_publication_readiness(
                snapshot_manifest_path=root / "snapshot.json",
                dataset_manifest_path=root / "dataset.json",
                evaluation_path=root / "evaluation.json",
                independent_audit_path=root / "audit.json",
                source_manifest_path=root / "source.json",
                results_manifest_path=root / "results.json",
                junit_path=root / "junit.xml",
            )
            release_attestation = root / "release-attestation.json"
            release_attestation.write_text(
                json.dumps(
                    {
                        "schema_version": "observation-planning-release-attestation-v2",
                        "study_id": "fixture",
                        "dataset_config_sha256": config_digest,
                        "source_identity_sha256": fixtures["source.json"][
                            "source_identity_sha256"
                        ],
                        "attestor": "release-reviewer",
                        "attested_at": "2026-09-04T00:00:00Z",
                        "project_code_license": {
                            "attested": True,
                            "spdx_identifier": "Apache-2.0",
                        },
                        "reachable_api_contact": {
                            "attested": True,
                            "contact": "research@project-domain.pl",
                        },
                        "satnogs_attribution_review": {
                            "attested": True,
                            "source": "https://network.satnogs.org/api/",
                            "license": "CC-BY-SA-4.0",
                            "license_url": "https://creativecommons.org/licenses/by-sa/4.0/",
                        },
                        "external_data_attribution_review": (
                            external_data_attribution_review()
                        ),
                    }
                ),
                encoding="utf-8",
            )
            persistently_attested = assess_planning_publication_readiness(
                snapshot_manifest_path=root / "snapshot.json",
                dataset_manifest_path=root / "dataset.json",
                evaluation_path=root / "evaluation.json",
                independent_audit_path=root / "audit.json",
                source_manifest_path=root / "source.json",
                results_manifest_path=root / "results.json",
                junit_path=root / "junit.xml",
                release_attestation_path=release_attestation,
            )
            audit_payload["input_artifacts"]["evaluation"]["sha256"] = "0" * 64
            (root / "audit.json").write_text(
                json.dumps(audit_payload), encoding="utf-8"
            )
            mismatched = assess_planning_publication_readiness(
                snapshot_manifest_path=root / "snapshot.json",
                dataset_manifest_path=root / "dataset.json",
                evaluation_path=root / "evaluation.json",
                independent_audit_path=root / "audit.json",
                source_manifest_path=root / "source.json",
                results_manifest_path=root / "results.json",
                junit_path=root / "junit.xml",
            )

        self.assertTrue(readiness["technical_publication_ready"])
        self.assertFalse(readiness["external_release_ready"])
        self.assertEqual(len(readiness["human_release_gates"]), 4)
        self.assertTrue(persistently_attested["external_release_ready"])
        self.assertEqual(persistently_attested["human_release_gates"], [])
        self.assertTrue(persistently_attested["release_attestation"]["valid"])
        self.assertEqual(
            readiness["probability_claim"], "predeclared useful calibrated improvement"
        )
        self.assertFalse(readiness["scheduler_empirical_claim_informative"])
        self.assertFalse(mismatched["technical_publication_ready"])
        self.assertIn("independent_audit", mismatched["technical_failed_checks"])

    def test_failed_test_suite_blocks_technical_readiness(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name, payload in {
                "snapshot.json": {"complete": True},
                "dataset.json": {"publication_count_gate": "pass"},
                "evaluation.json": {
                    "tasks": {
                        "signal_present": {"status": "evaluated"},
                        "decode_success_given_signal": {"status": "evaluated"},
                    },
                    "synthetic_scheduler_verification": {
                        "instances": 1,
                        "milp_exact_match_count": 1,
                        "multi_asset_instances": 1,
                        "multi_asset_milp_exact_match_count": 1,
                    },
                    "correlated_risk_sensitivity": {
                        "schema_version": "synthetic-correlated-risk-sensitivity-v1",
                        "solver_status": "optimal",
                        "scenarios": {"mild": {}, "stress": {}},
                    },
                },
                "audit.json": {"passed": True},
                "source.json": {
                    "file_count": 20,
                    "source_identity_sha256": "a" * 64,
                },
                "results.json": {
                    "schema_version": "observation-planning-publication-results-manifest-v1",
                    "artifacts": [],
                },
            }.items():
                (root / name).write_text(json.dumps(payload), encoding="utf-8")
            (root / "junit.xml").write_text(
                '<testsuite tests="1" failures="1" errors="0"/>',
                encoding="utf-8",
            )
            readiness = assess_planning_publication_readiness(
                snapshot_manifest_path=root / "snapshot.json",
                dataset_manifest_path=root / "dataset.json",
                evaluation_path=root / "evaluation.json",
                independent_audit_path=root / "audit.json",
                source_manifest_path=root / "source.json",
                results_manifest_path=root / "results.json",
                junit_path=root / "junit.xml",
            )
        self.assertFalse(readiness["technical_publication_ready"])
        self.assertIn("full_test_suite", readiness["technical_failed_checks"])


if __name__ == "__main__":
    unittest.main()
