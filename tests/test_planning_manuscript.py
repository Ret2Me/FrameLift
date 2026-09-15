import csv
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from telemetry_yield.cli import build_parser
from telemetry_yield.planning.covariates import (
    write_covariate_archive,
    write_covariate_evidence_manifest,
)
from telemetry_yield.planning.manuscript import write_publication_results


class PublicationManuscriptTests(unittest.TestCase):
    def test_cli_and_manifest_bind_validated_covariate_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = root / "work" / "covariates.json"
            archive.parent.mkdir()
            write_covariate_archive(archive)
            cache = root / "cache"
            cache.mkdir()
            evidence = root / "covariate-evidence.json"
            write_covariate_evidence_manifest(archive, cache, evidence)
            dataset = root / "reports" / "publication" / "dataset.json"
            dataset.parent.mkdir(parents=True)
            dataset.write_text(
                json.dumps(
                    {
                        "covariate_archive_path": "work/covariates.json",
                        "covariate_archive_sha256": hashlib.sha256(
                            archive.read_bytes()
                        ).hexdigest(),
                    }
                ),
                encoding="utf-8",
            )
            evaluation = root / "evaluation.json"
            evaluation.write_text(json.dumps({"tasks": {}}), encoding="utf-8")
            audit = root / "audit.json"
            audit.write_text(
                json.dumps(
                    {"passed": True, "check_count": 1, "failed_check_count": 0}
                ),
                encoding="utf-8",
            )

            args = build_parser().parse_args(
                [
                    "planning-write-publication-results",
                    "--dataset-manifest",
                    str(dataset),
                    "--evaluation",
                    str(evaluation),
                    "--independent-audit",
                    str(audit),
                    "--covariate-evidence",
                    str(evidence),
                    "--output-dir",
                    str(root / "results"),
                ]
            )
            manifest = write_publication_results(
                dataset_manifest_path=args.dataset_manifest,
                evaluation_path=args.evaluation,
                independent_audit_path=args.independent_audit,
                covariate_evidence_path=args.covariate_evidence,
                output_dir=args.output_dir,
            )

        self.assertEqual(manifest["covariate_evidence_path"], str(evidence))
        self.assertEqual(
            manifest["covariate_evidence_summary"],
            {
                "response_count": 0,
                "weather_response_count": 0,
                "kp_response_count": 0,
                "hardware_response_count": 0,
            },
        )

    def test_negative_result_and_noninformative_replay_are_guarded(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dataset = root / "dataset-manifest.json"
            dataset.write_text(
                json.dumps(
                    {
                        "input_rows": 1200,
                        "normalized_rows": 1100,
                        "known_signal_rows": 1000,
                        "signal_satellites": 10,
                        "signal_stations": 50,
                        "conditional_decode_rows": 300,
                        "publication_count_gate": "pass",
                        "exclusion_counts": {"failed_job": 100},
                        "feature_availability": {
                            "historical_station_location_captured_present": 900,
                            "current_api_station_location_fallback_present": 200,
                            "historical_receiver_configuration_present": 800,
                            "historical_receiver_configuration_value_counts": {
                                "captured_receiver_rf_gain_db": 700
                            },
                            "historical_terrestrial_weather_snapshot_present": 900,
                            "historical_space_weather_snapshot_present": 850,
                            "historical_antenna_configuration_present": 0,
                        },
                    }
                ),
                encoding="utf-8",
            )
            metric = {
                "count": 200,
                "positives": 50,
                "brier": 0.2,
                "log_loss": 0.6,
                "auroc": 0.7,
            }
            evaluation = root / "evaluation.json"
            evaluation.write_text(
                json.dumps(
                    {
                        "tasks": {
                            "signal_present": {
                                "status": "evaluated",
                                "temporal": {
                                    "primary_full_logit_useful_effect": False,
                                    "metrics": {
                                        "global_rate": metric,
                                        "full_logit": metric,
                                    },
                                    "paired_bootstrap_vs_global_rate": {
                                        "full_logit": {"lower_95": -0.01, "upper_95": 0.02}
                                    },
                                },
                                "loso_satellite": {"metrics": {}},
                                "loso_station": {"metrics": {}},
                                "external_validation": {
                                    label: {
                                        "status": "evaluated",
                                        "prediction_rows": 800,
                                        "test_observation_rows": 200,
                                        "held_out_group_ids": list(range(10)),
                                        "evaluated_group_ids": list(range(5)),
                                        "evaluated_group_count": 5,
                                        "metrics": {
                                            "global_rate": {"pooled": metric},
                                            "full_logit": {"pooled": metric},
                                        },
                                        "paired_bootstrap_vs_global_rate": {
                                            "full_logit": {
                                                "lower_95": -0.02,
                                                "upper_95": 0.01,
                                                "cluster_count": 5,
                                            }
                                        },
                                    }
                                    for label in ("satellite", "station")
                                },
                                "scheduling_replay": {
                                    "natural_receiver_conflict_pairs": 0,
                                    "informative_for_scheduler_comparison": False,
                                    "metrics": {
                                        "probability_milp": {
                                            "selected_observations": 10,
                                            "realized_successful_samples": 4,
                                            "predicted_expected_samples": 4.2,
                                            "satellites_covered": 3,
                                        }
                                    },
                                },
                            },
                            "decode_success_given_signal": {
                                "status": "insufficient-data"
                            },
                        },
                        "composed_reception": {
                            "status": "evaluated",
                            "temporal": {
                                "test_count": 200,
                                "metrics": {"full_logit": metric},
                                "decision_metrics_at_0_5": {
                                    "full_logit": {
                                        "accuracy": 0.8,
                                        "sensitivity": 0.7,
                                        "specificity": 0.9,
                                        "positive_predictive_value": 0.75,
                                        "negative_predictive_value": 0.86,
                                        "true_positive": 35,
                                        "true_negative": 135,
                                        "false_positive": 15,
                                        "false_negative": 15,
                                    }
                                },
                            },
                            "external_validation": {},
                            "scheduling_replay": {
                                "outcome_task": "reception_success",
                                "natural_receiver_conflict_pairs": 0,
                                "informative_for_scheduler_comparison": False,
                                "metrics": {
                                    "probability_milp": {
                                        "selected_observations": 10,
                                        "realized_successful_samples": 3,
                                        "predicted_expected_samples": 3.4,
                                        "satellites_covered": 2,
                                    }
                                },
                            },
                        },
                        "tle_refresh_audit": {
                            "transition_count": 4,
                            "thresholds": {
                                "5": {
                                    "material_transition_count": 2,
                                    "material_fraction": 0.5,
                                    "transitions_inside_freeze_proxy": {
                                        "0": 0,
                                        "15": 1,
                                        "30": 1,
                                        "60": 2,
                                    },
                                }
                            },
                        },
                        "synthetic_scheduler_verification": {
                            "instances": 100,
                            "milp_exact_match_count": 100,
                            "multi_asset_instances": 50,
                            "multi_asset_milp_exact_match_count": 50,
                        },
                    }
                ),
                encoding="utf-8",
            )
            audit = root / "audit.json"
            audit.write_text(
                json.dumps({"passed": True, "check_count": 20, "failed_check_count": 0}),
                encoding="utf-8",
            )
            manifest = write_publication_results(
                dataset_manifest_path=dataset,
                evaluation_path=evaluation,
                independent_audit_path=audit,
                output_dir=root / "results",
            )
            narrative = (root / "results" / "results.md").read_text(encoding="utf-8")
            with (root / "results" / "table-probability.csv").open(
                encoding="utf-8", newline=""
            ) as handle:
                probability_rows = list(csv.DictReader(handle))
            with (root / "results" / "table-reception.csv").open(
                encoding="utf-8", newline=""
            ) as handle:
                reception_rows = list(csv.DictReader(handle))
            with (root / "results" / "table-scheduler.csv").open(
                encoding="utf-8", newline=""
            ) as handle:
                scheduler_rows = list(csv.DictReader(handle))

        self.assertIn("did **not** meet", narrative)
        self.assertIn("non-informative", narrative)
        self.assertIn("implementation exactness only", narrative)
        self.assertIn(
            "time-aligned for 900 of 900 rows carrying a capture-time client location",
            narrative,
        )
        self.assertIn("Kp was aligned for 850 rows", narrative)
        self.assertIn("receiver configuration for 800 rows", narrative)
        self.assertIn("current hardware was not backdated", narrative)
        self.assertIn(
            "external-satellite: 200 test observations (800 model predictions) from 5/10",
            narrative,
        )
        self.assertIn(
            "external-station: 200 test observations (800 model predictions) from 5/10",
            narrative,
        )
        self.assertNotIn(
            "terrestrial/space-weather snapshots are unavailable", narrative
        )
        self.assertEqual(len(manifest["artifacts"]), 7)
        self.assertEqual(len(probability_rows), 7)
        self.assertEqual(
            {row["split"] for row in probability_rows},
            {"temporal", "external-satellite", "external-station"},
        )
        self.assertEqual(probability_rows[-1]["task"], "decode_success_given_signal")
        self.assertEqual(len(reception_rows), 1)
        self.assertEqual(reception_rows[0]["accuracy_at_0_5"], "0.8000")
        self.assertIn("End-to-end packet reception temporal: n=200", narrative)
        self.assertEqual(
            {row["endpoint"] for row in scheduler_rows},
            {"signal_present", "reception_success"},
        )
        self.assertEqual(
            next(
                row["realized_successes"]
                for row in scheduler_rows
                if row["endpoint"] == "reception_success"
            ),
            "3",
        )

    def test_reporting_requires_a_passing_independent_audit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name, payload in {
                "dataset.json": {},
                "evaluation.json": {"tasks": {}},
                "audit.json": {"passed": False},
            }.items():
                (root / name).write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "audit must pass"):
                write_publication_results(
                    dataset_manifest_path=root / "dataset.json",
                    evaluation_path=root / "evaluation.json",
                    independent_audit_path=root / "audit.json",
                    output_dir=root / "out",
                )


if __name__ == "__main__":
    unittest.main()
