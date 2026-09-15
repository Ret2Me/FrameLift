import hashlib
import json
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from telemetry_yield.planning.api_contact import (
    ApiContactError,
    load_api_contact,
    publication_user_agent,
)
from telemetry_yield.planning.campaign_materialization import (
    CampaignMaterializationError,
    materialize_campaign_config,
)
from telemetry_yield.planning.prospective import ProspectiveCampaignConfig


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class PublicationV4CampaignWiringTests(unittest.TestCase):
    def test_prospective_targets_are_covered_by_the_runtime_target_pool(self):
        template = json.loads(
            (
                PROJECT_ROOT
                / "configs/prospective/observation-planning-shadow-v4g-template.json"
            ).read_text(encoding="utf-8")
        )
        pool = json.loads(
            (
                PROJECT_ROOT
                / "configs/observation-planning-prospective-target-pool-v4g.json"
            ).read_text(encoding="utf-8")
        )
        campaign_targets = {int(value) for value in template["target_norad_ids"]}
        pool_targets = {int(item["norad_id"]) for item in pool["targets"]}

        self.assertEqual(len(campaign_targets), len(template["target_norad_ids"]))
        self.assertTrue(campaign_targets <= pool_targets)

    def test_runtime_api_contact_rejects_placeholders(self):
        self.assertEqual(
            publication_user_agent("research@university.edu"),
            "telemetry-yield-research/0.2 contact=research@university.edu",
        )
        for contact in (
            "research@example.invalid",
            "research@example.org",
            "http://project.example.edu/contact",
            "localhost",
            "",
        ):
            with self.subTest(contact=contact), self.assertRaises(ApiContactError):
                publication_user_agent(contact)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "contact.json"
            expected_contact = "https://research.university.edu/telemetry-yield"
            path.write_text(
                json.dumps(
                    {
                        "schema_version": "observation-planning-api-contact-v2",
                        "contact": expected_contact,
                        "open_meteo_free_api_usage": {
                            "attested": True,
                            "terms_url": "https://open-meteo.com/en/terms",
                            "use_category": "non-commercial-research",
                        },
                    }
                )
            )
            user_agent, payload = load_api_contact(path)
            self.assertEqual(payload["contact"], expected_contact)
            self.assertEqual(
                user_agent,
                f"telemetry-yield-research/0.2 contact={expected_contact}",
            )
            invalid_usage = dict(payload)
            invalid_usage["open_meteo_free_api_usage"] = {
                **payload["open_meteo_free_api_usage"],
                "attested": False,
            }
            path.write_text(json.dumps(invalid_usage))
            with self.assertRaisesRegex(
                ApiContactError, "non-commercial use was not attested"
            ):
                load_api_contact(path)

            invalid_usage["schema_version"] = "observation-planning-api-contact-v1"
            path.write_text(json.dumps(invalid_usage))
            with self.assertRaisesRegex(ApiContactError, "unsupported API contact"):
                load_api_contact(path)

    def test_v4g_template_is_month_long_and_has_external_targets(self):
        template = json.loads(
            (
            PROJECT_ROOT
                / "configs/prospective/observation-planning-shadow-v4g-template.json"
            ).read_text()
        )
        publication = json.loads(
            (PROJECT_ROOT / "configs/observation-planning-publication-v4.json").read_text()
        )
        historical_targets = {
            int(item["norad_id"]) for item in publication["targets"]
        }
        prospective_targets = set(template["target_norad_ids"])
        sampling_intervals = [
            (
                datetime.fromisoformat(item["start"].replace("Z", "+00:00")),
                datetime.fromisoformat(item["end"].replace("Z", "+00:00")),
            )
            for item in publication["sampling_intervals"]
        ]

        self.assertGreaterEqual(template["minimum_start_lead_hours"], 48)
        self.assertGreaterEqual(template["duration_days"], 30)
        self.assertEqual(len(prospective_targets), 50)
        self.assertEqual(len(set(template["station_ids"])), 13)
        self.assertEqual(len(prospective_targets - historical_targets), 8)
        self.assertEqual(template["minimum_observations_per_target"], 1)
        self.assertGreaterEqual(template["minimum_reconciled_outcomes"], 300)
        self.assertGreaterEqual(template["minimum_scored_targets"], 20)
        self.assertGreaterEqual(template["minimum_scored_stations"], 5)
        self.assertGreaterEqual(template["minimum_signal_positive_outcomes"], 30)
        self.assertGreaterEqual(template["minimum_signal_negative_outcomes"], 30)
        self.assertGreaterEqual(
            template["minimum_conditional_decode_positive_outcomes"], 15
        )
        self.assertGreaterEqual(
            template["minimum_conditional_decode_negative_outcomes"], 15
        )
        self.assertEqual(template["maximum_opportunity_duration_seconds"], 900)
        self.assertEqual(len(sampling_intervals), 5)
        self.assertEqual(
            sum(
                (end - start for start, end in sampling_intervals),
                timedelta(0),
            ),
            timedelta(days=87),
        )
        self.assertEqual(sampling_intervals[-1][1], datetime(2026, 9, 1, tzinfo=UTC))
        self.assertGreaterEqual(
            sampling_intervals[-1][1] - sampling_intervals[-1][0],
            timedelta(days=28),
        )
        self.assertIn("Outcome-blind", publication["sampling_rule"])

    def test_materialization_is_delayed_content_bound_and_immutable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            template = root / "template.json"
            output = root / "work/config.json"
            dataset = root / "dataset.jsonl"
            snapshot = root / "snapshot.json"
            manifest = root / "manifest.json"
            evaluation = root / "evaluation.json"
            audit = root / "audit.json"
            model = root / "model.json"
            api_contact = root / "api-contact.json"
            template.write_text(
                json.dumps(
                    {
                        "schema_version": "observation-planning-prospective-template-v1",
                        "campaign_id_prefix": "delayed-test",
                        "minimum_start_lead_hours": 48,
                        "duration_days": 31,
                        "mode": "shadow",
                        "planning_interval_hours": 24,
                        "minimum_elapsed_days": 30,
                        "minimum_plan_commit_days": 25,
                        "minimum_reconciled_outcomes": 1,
                        "minimum_observations_per_target": 1,
                        "maximum_opportunity_duration_seconds": 900,
                        "station_ids": [1],
                        "target_norad_ids": [2],
                    }
                )
            )
            dataset.write_text("one frozen training row\n")
            dataset_sha = hashlib.sha256(dataset.read_bytes()).hexdigest()
            snapshot.write_text(json.dumps({"complete": True}))
            manifest.write_text(
                json.dumps(
                    {
                        "study_id": "fixture",
                        "publication_count_gate": "pass",
                        "dataset_sha256": dataset_sha,
                    }
                )
            )
            evaluation.write_text(
                json.dumps(
                    {
                        "schema_version": "observation-planning-evaluation-v1",
                        "study_id": "fixture",
                        "publication_count_gate_enforced": True,
                        "dataset_sha256": dataset_sha,
                    }
                )
            )
            manifest_sha = hashlib.sha256(manifest.read_bytes()).hexdigest()
            evaluation_sha = hashlib.sha256(evaluation.read_bytes()).hexdigest()
            audit.write_text(
                json.dumps(
                    {
                        "schema_version": "observation-planning-independent-audit-v1",
                        "passed": True,
                        "failed_check_count": 0,
                        "input_artifacts": {
                            "dataset": {"sha256": dataset_sha},
                            "dataset_manifest": {"sha256": manifest_sha},
                            "evaluation": {"sha256": evaluation_sha},
                        },
                    }
                )
            )
            model.write_text(
                json.dumps(
                    {
                        "study_id": "fixture",
                        "training_dataset_sha256": dataset_sha,
                        "dataset_manifest_sha256": manifest_sha,
                        "evaluation_sha256": evaluation_sha,
                    }
                )
            )
            api_contact.write_text(
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
            completed_at = datetime(2026, 9, 3, 23, tzinfo=UTC)
            estimator = SimpleNamespace(
                training_data_end=datetime(2026, 9, 1, tzinfo=UTC)
            )
            arguments = {
                "project_root": root,
                "template_path": template,
                "output_path": output,
                "snapshot_manifest_path": snapshot,
                "history_dataset_path": dataset,
                "dataset_manifest_path": manifest,
                "evaluation_path": evaluation,
                "independent_audit_path": audit,
                "probability_model_path": model,
                "api_contact_path": api_contact,
            }

            valid_audit = audit.read_text()
            tampered_audit = json.loads(valid_audit)
            tampered_audit["input_artifacts"]["evaluation"]["sha256"] = "0" * 64
            audit.write_text(json.dumps(tampered_audit))
            with mock.patch(
                "telemetry_yield.planning.campaign_materialization."
                "FrozenLogitProbabilityEstimator.load",
                return_value=estimator,
            ), self.assertRaisesRegex(
                CampaignMaterializationError,
                "independent audit failed",
            ):
                materialize_campaign_config(**arguments, now=completed_at)
            audit.write_text(valid_audit)

            valid_model = model.read_text()
            tampered_model = json.loads(valid_model)
            tampered_model["evaluation_sha256"] = "0" * 64
            model.write_text(json.dumps(tampered_model))
            with mock.patch(
                "telemetry_yield.planning.campaign_materialization."
                "FrozenLogitProbabilityEstimator.load",
                return_value=estimator,
            ), self.assertRaisesRegex(
                CampaignMaterializationError,
                "probability model is not bound",
            ):
                materialize_campaign_config(**arguments, now=completed_at)
            model.write_text(valid_model)

            with mock.patch(
                "telemetry_yield.planning.campaign_materialization."
                "FrozenLogitProbabilityEstimator.load",
                return_value=estimator,
            ):
                payload, created = materialize_campaign_config(
                    **arguments, now=completed_at
                )
                original = output.read_bytes()
                repeated, created_again = materialize_campaign_config(
                    **arguments, now=completed_at + timedelta(days=20)
                )

            self.assertTrue(created)
            self.assertFalse(created_again)
            self.assertEqual(payload, repeated)
            self.assertEqual(output.read_bytes(), original)
            self.assertEqual(payload["start"], "2026-09-06T00:00:00Z")
            self.assertEqual(payload["end"], "2026-10-07T00:00:00Z")
            self.assertEqual(payload["campaign_id"], "delayed-test-20260906")
            self.assertEqual(
                payload["runtime_user_agent"],
                "telemetry-yield-research/0.2 contact=research@university.edu",
            )
            self.assertEqual(
                payload["historical_dependencies"]["runtime_api_contact"][
                    "sha256"
                ],
                hashlib.sha256(api_contact.read_bytes()).hexdigest(),
            )
            campaign = ProspectiveCampaignConfig.load(output)
            self.assertGreaterEqual(
                campaign.start - completed_at, timedelta(hours=48)
            )

            changed_dates = json.loads(original)
            changed_dates["start"] = "2026-09-05T00:00:00Z"
            output.write_text(json.dumps(changed_dates))
            with mock.patch(
                "telemetry_yield.planning.campaign_materialization."
                "FrozenLogitProbabilityEstimator.load",
                return_value=estimator,
            ), self.assertRaisesRegex(
                CampaignMaterializationError,
                "campaign start changed",
            ):
                materialize_campaign_config(**arguments, now=completed_at)
            output.write_bytes(original)

            model.write_text('{"changed":true}')
            with mock.patch(
                "telemetry_yield.planning.campaign_materialization."
                "FrozenLogitProbabilityEstimator.load",
                return_value=estimator,
            ), self.assertRaisesRegex(
                CampaignMaterializationError,
                "probability model is not bound",
            ):
                materialize_campaign_config(**arguments, now=completed_at)

    def test_v4g_fails_closed_and_loads_the_enlarged_frozen_model(self):
        script = (
            PROJECT_ROOT / "scripts/prospective-v4g-daily.sh"
        ).read_text(encoding="utf-8")
        materialize = script.index("campaign_materialization")
        initialize = script.index("planning-prospective-init")
        reconcile = script.index("planning-prospective-reconcile-shadow")

        for gate in (
            ".complete == true",
            '.publication_count_gate == "pass"',
            '.publication_count_gate_enforced == true',
            '.passed == true and .failed_check_count == 0',
            'planning-learned-probability-model-v1',
        ):
            self.assertLess(script.index(gate), initialize)
        self.assertLess(materialize, initialize)
        self.assertLess(initialize, reconcile)
        self.assertIn(
            'HISTORY_DATASET="$HISTORICAL_RUN_ROOT/normalized-enriched.jsonl"',
            script,
        )
        self.assertIn('--probability-model "$PROBABILITY_MODEL"', script)
        self.assertIn('--test-attestation "$TEST_ATTESTATION"', script)
        self.assertIn('--api-contact "$API_CONTACT"', script)
        self.assertIn('--user-agent "$RUNTIME_USER_AGENT"', script)
        self.assertIn(
            'configs/observation-planning-prospective-target-pool-v4g.json', script
        )
        self.assertNotIn("research@example.invalid", script)
        self.assertIn('--input-artifact "$API_CONTACT"', script)
        run_block = script[
            script.index("planning-prospective-run-shadow") : script.index(
                '\nfi\n\n"$PYTHON" -m telemetry_yield.cli planning-prospective-report'
            )
        ]
        self.assertEqual(
            run_block.count('--probability-model "$PROBABILITY_MODEL"'), 1
        )
        self.assertNotIn('--input-artifact "$PROBABILITY_MODEL"', run_block)
        self.assertIn('BLOCKER_CONTROL="$RUN_ROOT/blockers.json"', script)
        self.assertIn('BLOCKER_ARGS=(--blockers "$BLOCKER_SNAPSHOT")', script)
        self.assertIn('"$RUN_ROOT/blocker-snapshots"', script)
        self.assertIn(
            'TARGET_OVERRIDE_CONTROL="$RUN_ROOT/target-overrides.json"', script
        )
        self.assertIn(
            'TARGET_ARGS=(--target-overrides "$TARGET_OVERRIDE_SNAPSHOT")', script
        )
        self.assertIn('"$RUN_ROOT/target-override-snapshots"', script)
        self.assertLess(
            script.index('install -m 0444 "$BLOCKER_CONTROL"'),
            script.index("planning-prospective-run-shadow"),
        )
        blocker_template = json.loads(
            (
                PROJECT_ROOT
                / "configs/prospective/observation-planning-blockers-v4g.json"
            ).read_text()
        )
        self.assertEqual(
            blocker_template,
            {
                "schema_version": "observation-planning-blockers-v1",
                "blockers": [],
            },
        )
        target_override_template = json.loads(
            (
                PROJECT_ROOT
                / "configs/prospective/observation-planning-target-overrides-v4g.json"
            ).read_text()
        )
        self.assertEqual(
            target_override_template,
            {
                "schema_version": "observation-planning-target-overrides-v1",
                "targets": [],
            },
        )
        self.assertNotIn("publication-v3", script)

    def test_historical_pipeline_enables_timer_only_after_prestart_success(self):
        script = (
            PROJECT_ROOT / "scripts/build-publication-v4.sh"
        ).read_text(encoding="utf-8")
        model = script.index("planning-build-probability-model")
        audit = script.index("planning-audit-publication")
        prestart = script.index('"$PROJECT_ROOT/scripts/prospective-v4g-daily.sh"')
        final_bootstrap = script.rindex("finish_campaign_bootstrap")
        final_readiness = script.index("planning-publication-readiness")
        enable = script.index(
            "systemctl --user enable --now telemetry-yield-prospective-v4g.timer"
        )
        fetch_snapshot = script.index("planning-fetch-publication")
        contact_gate = script.index("if ! load_runtime_user_agent; then", fetch_snapshot)
        test_run = script.index(
            '"$PROJECT_ROOT/scripts/run-publication-v4-tests.sh"', contact_gate
        )
        build_dataset = script.index("planning-build-dataset", fetch_snapshot)
        count_gate = script.index("publication count gate failed")
        fetch_covariates = script.index("planning-fetch-covariates")

        self.assertLess(model, audit)
        self.assertLess(audit, final_bootstrap)
        self.assertLess(prestart, final_readiness)
        self.assertLess(final_readiness, enable)
        self.assertLess(fetch_snapshot, contact_gate)
        self.assertLess(contact_gate, test_run)
        self.assertLess(test_run, build_dataset)
        self.assertLess(contact_gate, build_dataset)
        self.assertLess(build_dataset, count_gate)
        self.assertLess(count_gate, fetch_covariates)
        self.assertIn('--test-attestation "$TEST_ATTESTATION"', script)
        self.assertIn(
            'install -m 0644 "$BLOCKER_TEMPLATE" '
            '"$PROSPECTIVE_RUN_ROOT/blockers.json"',
            script,
        )
        self.assertIn(
            'install -m 0644 "$TARGET_OVERRIDE_TEMPLATE" '
            '"$PROSPECTIVE_RUN_ROOT/target-overrides.json"',
            script,
        )
        self.assertIn('--api-contact "$API_CONTACT"', script)
        self.assertIn('--user-agent "$RUNTIME_USER_AGENT"', script)
        self.assertNotIn("research@example.invalid", script)

        test_script = (
            PROJECT_ROOT / "scripts/run-publication-v4-tests.sh"
        ).read_text(encoding="utf-8")
        source_freeze = test_script.index("planning-write-provenance")
        pytest_run = test_script.index('"$PYTEST" -q')
        attestation_write = test_script.index(
            "telemetry_yield.planning.test_attestation"
        )
        self.assertLess(source_freeze, pytest_run)
        self.assertLess(pytest_run, attestation_write)

    def test_contact_gate_has_fail_closed_automatic_resume_timer(self):
        service = (
            PROJECT_ROOT
            / "deploy/systemd/telemetry-yield-publication-v4-resume.service"
        ).read_text(encoding="utf-8")
        timer = (
            PROJECT_ROOT
            / "deploy/systemd/telemetry-yield-publication-v4-resume.timer"
        ).read_text(encoding="utf-8")

        self.assertIn(
            "ExecCondition=/usr/bin/test -s "
            "/home/ubuntu/telemetry-yield/work/operations/"
            "publication-api-contact.json",
            service,
        )
        self.assertIn(
            "ExecCondition=/usr/bin/test ! -s "
            "/home/ubuntu/telemetry-yield/work/prospective-v4g/config.json",
            service,
        )
        self.assertIn(
            "ExecStart=/usr/bin/systemctl --user start "
            "telemetry-yield-publication-v4.service",
            service,
        )
        self.assertIn("OnUnitActiveSec=300", timer)
        self.assertIn(
            "Unit=telemetry-yield-publication-v4-resume.service", timer
        )


if __name__ == "__main__":
    unittest.main()
