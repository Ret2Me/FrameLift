from __future__ import annotations

from copy import deepcopy
import unittest

from telemetry_yield.campaign_report import (
    build_campaign_report,
    render_campaign_markdown,
)


def archive() -> dict[str, object]:
    observations = [
        {
            "source_mode": "FSK AX.25 G3RUH",
            "status": "supported_now",
            "iq": {"available": True},
            "reference_frames": {"status": "none"},
            "route": {"required_plugins": []},
        },
        {
            "source_mode": "AFSK",
            "status": "needs_plugin",
            "iq": {"available": True},
            "reference_frames": {"status": "none"},
            "route": {"required_plugins": ["afsk_demodulator"]},
        },
        {
            "source_mode": "CW",
            "status": "no_iq",
            "iq": {"available": False},
            "reference_frames": {"status": "none"},
            "route": {"required_plugins": ["cw_decoder"]},
        },
    ]
    return {
        "schema_version": "polyitan-archive-inventory-v1",
        "summary": {
            "observation_count": 3,
            "iq_available_count": 2,
            "iq_missing_count": 1,
            "status_counts": [
                {"status": "needs_plugin", "count": 1},
                {"status": "no_iq", "count": 1},
                {"status": "supported_now", "count": 1},
            ],
        },
        "observations": observations,
        "capability_contract": {
            "fsk_implies_ax25": False,
            "current_demodulators": ["phase_fsk"],
            "current_waveform_families": ["fsk"],
        },
    }


def live() -> dict[str, object]:
    return {
        "schema_version": "polyitan-live-bucket-inventory-v1",
        "summary": {
            "exact_iq_key_count": 2,
            "listed_unique_key_count": 2,
            "new_in_bucket_count": 1,
            "missing_from_bucket_count": 0,
            "anomaly_count": 0,
            "total_iq_size_bytes": 30,
            "zero_size_count": 0,
        },
        "objects": [{}, {}],
        "comparison": {
            "new_in_bucket_observation_ids": [3],
            "missing_from_bucket_observation_ids": [],
        },
        "anomalies": [],
    }


def download(*, complete: bool) -> dict[str, object]:
    result = {
        "observation_id": 1,
        "status": "downloaded",
        "size_bytes": 10,
        "expected_size_bytes": 10,
        "sha256": "a" * 64,
    }
    return {
        "schema_version": "archive-iq-download-manifest-v1",
        "candidate_count": 1 if complete else 2,
        "known_size_candidate_count": 1 if complete else 2,
        "expected_size_bytes": 10 if complete else 20,
        "latest_result_count": 1,
        "status_counts": {"downloaded": 1},
        "results": [result],
    }


def comparison() -> dict[str, object]:
    return {
        "schema_version": "polyitan-iq-comparison-v1",
        "same_iq_positive_observations": [
            {
                "satnogs_trusted_payloads": 2,
                "phase_first_trusted_payloads": 3,
                "hybrid_union_trusted_payloads": 3,
            }
        ],
        "positive_observation_aggregate": {
            "observation_count": 1,
            "satnogs_trusted_payloads": 2,
            "standalone_phase_first_trusted_payloads": 3,
            "hybrid_union_trusted_payloads": 3,
            "hybrid_union_vs_satnogs": 1,
            "hybrid_relative_gain_over_satnogs": 0.5,
        },
    }


def processing() -> dict[str, object]:
    return {
        "schema_version": "archive-g3ruh-processing-manifest-v1",
        "selection_contract": {
            "frames_recovered": False,
            "mode_exact": "FSK AX.25 G3RUH",
            "generic_fsk_implies_ax25": False,
        },
        "candidate_count": 1,
        "latest_result_count": 1,
        "status_counts": {"processed": 1},
        "results": [
            {
                "observation_id": 1,
                "status": "processed",
                "crc_valid_frame_count": 2,
                "decode_output_path": "/tmp/observation_1-phase.json",
                "decode_output_sha256": "b" * 64,
            }
        ],
    }


def protocol_audit() -> dict[str, object]:
    return {
        "schema_version": "archive-g3ruh-protocol-audit-v1",
        "campaign_complete": False,
        "source_manifests": [],
        "audited_observation_count": 1,
        "raw_crc_valid_candidate_count": 2,
        "trusted_ax25_ui_frame_count": 1,
        "ax25_with_exact_inner_ccsds_count": 1,
        "rejected_crc_collision_count": 1,
        "observations": [
            {
                "observation_id": 1,
                "source_artifact": "/tmp/observation_1-phase.json",
                "source_artifact_sha256": "b" * 64,
                "raw_crc_valid_candidate_count": 2,
                "trusted_ax25_ui_frame_count": 1,
                "ax25_with_exact_inner_ccsds_count": 1,
                "rejected_crc_collision_count": 1,
                "candidates": [
                    {
                        "frame_with_fcs_sha256": "c" * 64,
                        "crc16_x25_valid": True,
                        "ax25_ui_structurally_valid": True,
                        "trusted_for_explicit_ax25_campaign": True,
                        "inner_ccsds_space_packet": {"apid": 1},
                    },
                    {
                        "frame_with_fcs_sha256": "d" * 64,
                        "crc16_x25_valid": True,
                        "ax25_ui_structurally_valid": False,
                        "trusted_for_explicit_ax25_campaign": False,
                        "inner_ccsds_space_packet": None,
                    },
                ],
            }
        ],
    }


def triage() -> dict[str, object]:
    return {
        "schema_version": "polyitan-live-delta-signal-triage-v1",
        "summary": {
            "file_count": 1,
            "recommended_count": 1,
            "total_active_windows": 2,
            "total_duration_seconds": 3.0,
            "total_one_second_windows": 3,
            "total_size_bytes": 16,
        },
        "recommended_for_expensive_bank_observation_ids": [9],
        "ranking": [
            {
                "rank": 1,
                "observation_id": 9,
                "file_size_bytes": 16,
                "duration_seconds": 3.0,
                "one_second_window_count": 3,
                "active_window_count": 2,
            }
        ],
    }


def satyaml_routing() -> dict[str, object]:
    return {
        "schema_version": 1,
        "summary": {
            "observation_count": 2,
            "existing_profile_routable_count": 1,
            "not_routable_to_existing_profile_count": 1,
            "routable_unique_profile_count": 1,
            "definite_new_or_updated_profile_count": 1,
            "status_counts": {"routed": 1, "unmatched_satyaml_profile": 1},
            "coverage_caveat": "routing does not prove that a recording decodes",
        },
        "routes": [
            {
                "observation_id": 1,
                "status": "routed",
                "routable": True,
                "capability_profile": {"name": "TEST"},
            },
            {
                "observation_id": 2,
                "status": "unmatched_satyaml_profile",
                "routable": False,
                "capability_profile": None,
            },
        ],
    }


def cw_probe() -> dict[str, object]:
    return {
        "schema_version": "polyitan-live-delta-cw-probe-v1",
        "plugin": {
            "output_kind": "untrusted_text_candidates",
            "validation_level": "pending",
        },
        "interpretation_contract": {
            "candidate_validation": "pending",
            "decoded_text_is_trusted_telemetry": False,
            "mission_or_protocol_validator_applied": False,
        },
        "summary": {
            "analyzed_count": 1,
            "classification_counts": {"unknown": 1},
            "candidate_count": 0,
            "cross_observation_repeated_text_count": 0,
        },
        "cross_observation_repeated_texts": [],
        "results": [
            {"observation_id": 9, "classification": "unknown", "candidates": []}
        ],
    }


def rml24() -> dict[str, object]:
    return {
        "schema_version": 1,
        "identity": {"short_name": "RML24"},
        "access": {
            "repository": "Zenodo",
            "full_download_status": "running_resumable_background_campaign",
        },
        "files": [
            {
                "name": "dataset.zip",
                "bytes": 100,
                "zenodo_checksum": "md5:" + "a" * 32,
                "ground_truth_bits": True,
                "zip_members": [
                    {"compressed_bytes": 90, "uncompressed_bytes": 200}
                ],
            }
        ],
        "dataset": {
            "paper_record_count": 1_386_000,
            "currently_uploaded_record_count_inferred": 1_323_000,
            "samples_per_record": 2048,
            "sample_rate_hz": 1_000_000,
            "duration_per_record_seconds": 0.002048,
        },
        "modulations_as_published": {
            "single": ["BPSK"],
            "composite": ["PCM-BPSK-PM"],
        },
        "fit_for_telemetry_yield_project": {
            "not_a_valid_direct_test_of": [
                "end-to-end count of validated telemetry packets"
            ],
            "recommended_evaluation_unit": "modulation accuracy and BER",
        },
    }


class CampaignReportTests(unittest.TestCase):
    def build(self, **updates: object) -> dict[str, object]:
        values = {
            "archive_inventory": archive(),
            "live_bucket_inventory": live(),
            "catalogue_download_manifest": download(complete=True),
            "live_delta_download_manifest": download(complete=True),
            "comparison": comparison(),
            "processing_manifest": processing(),
        }
        values.update(updates)
        return build_campaign_report(**values)

    def test_complete_report_separates_crc_candidates_from_trusted_frames(self) -> None:
        report = self.build()
        self.assertTrue(report["complete"])
        campaign = report["evidence"]["current_campaign"]
        self.assertEqual(campaign["raw_crc_valid_candidate_count"], 2)
        self.assertIsNone(campaign["trusted_protocol_valid_frame_count"])
        self.assertIn("CRC-valid candidates only", campaign["label"])
        baseline = report["evidence"]["same_iq_protocol_validated_baseline"]
        self.assertEqual(baseline["satnogs_trusted_payloads"], 2)
        self.assertEqual(baseline["protocol_validated_union_trusted_payloads"], 3)

    def test_in_progress_and_missing_processing_are_not_filled_in(self) -> None:
        report = self.build(
            catalogue_download_manifest=download(complete=False),
            processing_manifest=None,
        )
        self.assertFalse(report["complete"])
        self.assertTrue(report["inventory"]["complete"])
        self.assertFalse(report["downloads"]["complete"])
        processing_stage = report["processing"]["explicit_g3ruh"]
        self.assertFalse(processing_stage["started"])
        self.assertIsNone(processing_stage["raw_crc_valid_candidate_count"])
        self.assertIsNone(processing_stage["candidate_count"])

    def test_completed_subset_is_not_full_processing_completion(self) -> None:
        wider_archive = archive()
        wider_archive["observations"].append(
            {
                "source_mode": "FSK AX.25 G3RUH",
                "status": "supported_now",
                "iq": {"available": True},
                "reference_frames": {"status": "none"},
                "route": {"required_plugins": []},
            }
        )
        wider_archive["summary"]["observation_count"] = 4
        wider_archive["summary"]["iq_available_count"] = 3
        for row in wider_archive["summary"]["status_counts"]:
            if row["status"] == "supported_now":
                row["count"] = 2
        report = self.build(archive_inventory=wider_archive)
        stage = report["processing"]["explicit_g3ruh"]
        self.assertTrue(stage["batch_complete"])
        self.assertFalse(stage["scope_complete"])
        self.assertEqual(stage["unselected_eligible_count"], 1)
        self.assertFalse(report["complete"])

    def test_required_plugin_backlog_counts_only_available_iq(self) -> None:
        report = self.build()
        self.assertEqual(
            report["capabilities"]["required_plugins"],
            [{"plugin": "afsk_demodulator", "observation_count": 1}],
        )

    def test_signal_triage_is_not_mislabeled_as_frame_evidence(self) -> None:
        report = self.build(live_delta_triage=triage())
        stage = report["processing"]["live_delta_triage"]
        self.assertTrue(stage["complete"])
        self.assertEqual(stage["recommended_for_expensive_bank_count"], 1)
        self.assertIsNone(stage["raw_crc_valid_candidate_count"])
        self.assertIsNone(stage["trusted_protocol_valid_frame_count"])
        self.assertIn("scheduling triage only", stage["evidence_classification"])

    def test_optional_routing_cw_and_rml24_preserve_evidence_boundaries(self) -> None:
        report = self.build(
            satyaml_routing=satyaml_routing(),
            live_delta_cw_probe=cw_probe(),
            rml24_dataset=rml24(),
        )
        routing = report["processing"]["satyaml_routing"]
        self.assertEqual(routing["routable_to_existing_profile_count"], 1)
        self.assertIsNone(routing["trusted_protocol_valid_frame_count"])
        cw = report["processing"]["live_delta_cw_probe"]
        self.assertEqual(cw["pending_candidate_count"], 0)
        self.assertEqual(cw["candidate_validation"], "pending")
        self.assertIsNone(cw["trusted_protocol_valid_frame_count"])
        dataset = report["datasets"]["rml24"]
        self.assertEqual(dataset["benchmark_unit"], "modulation classification and BER")
        self.assertFalse(dataset["frame_yield_benchmark"])
        self.assertIsNone(dataset["trusted_protocol_valid_frame_count"])
        markdown = render_campaign_markdown(report)
        self.assertIn("tekst bez walidatora nie jest telemetrią", markdown)
        self.assertIn("benchmark modulacji/BER, nie liczby ramek", markdown)

    def test_optional_artifact_schema_and_counter_mismatches_fail_closed(self) -> None:
        broken_routing = satyaml_routing()
        broken_routing["summary"]["existing_profile_routable_count"] = 2
        with self.assertRaisesRegex(ValueError, "routable counters"):
            self.build(satyaml_routing=broken_routing)

        broken_cw = cw_probe()
        broken_cw["summary"]["candidate_count"] = 1
        with self.assertRaisesRegex(ValueError, "candidate_count"):
            self.build(live_delta_cw_probe=broken_cw)

        unsafe_cw = cw_probe()
        unsafe_cw["interpretation_contract"][
            "decoded_text_is_trusted_telemetry"
        ] = True
        with self.assertRaisesRegex(ValueError, "trust contract"):
            self.build(live_delta_cw_probe=unsafe_cw)

        broken_rml = rml24()
        broken_rml["dataset"]["duration_per_record_seconds"] = 1.0
        with self.assertRaisesRegex(ValueError, "duration"):
            self.build(rml24_dataset=broken_rml)

    def test_protocol_audit_supplies_real_trusted_rejected_and_ccsds_counts(self) -> None:
        report = self.build(protocol_audit=protocol_audit())
        audit = report["processing"]["protocol_audit"]
        self.assertTrue(audit["available"])
        self.assertFalse(audit["complete"])
        self.assertEqual(audit["trusted_ax25_ui_frame_count"], 1)
        self.assertEqual(audit["rejected_crc_collision_count"], 1)
        self.assertEqual(audit["ax25_with_exact_inner_ccsds_count"], 1)
        campaign = report["evidence"]["current_campaign"]
        self.assertEqual(campaign["trusted_protocol_valid_frame_count"], 1)
        self.assertEqual(campaign["rejected_crc_collision_count"], 1)
        self.assertEqual(campaign["ax25_with_exact_inner_ccsds_count"], 1)

    def test_protocol_audit_must_match_completed_source_artifact_and_hash(self) -> None:
        wrong_hash = protocol_audit()
        wrong_hash["observations"][0]["source_artifact_sha256"] = "e" * 64
        with self.assertRaisesRegex(ValueError, "artifact hash mismatch"):
            self.build(protocol_audit=wrong_hash)
        wrong_id = protocol_audit()
        wrong_id["observations"][0]["observation_id"] = 2
        with self.assertRaisesRegex(ValueError, "not a completed processing result"):
            self.build(protocol_audit=wrong_id)

    def test_protocol_audit_cannot_claim_completion_before_28_of_28(self) -> None:
        premature = protocol_audit()
        premature["campaign_complete"] = True
        with self.assertRaisesRegex(ValueError, "28/28"):
            self.build(protocol_audit=premature)

    def test_counter_mismatch_fails_closed(self) -> None:
        broken = download(complete=True)
        broken["status_counts"] = {"downloaded": 2}
        with self.assertRaisesRegex(ValueError, "status_counts"):
            self.build(catalogue_download_manifest=broken)
        broken_archive = archive()
        broken_archive["summary"]["iq_available_count"] = 3
        with self.assertRaisesRegex(ValueError, "IQ counters"):
            self.build(archive_inventory=broken_archive)

    def test_comparison_aggregate_mismatch_fails_closed(self) -> None:
        broken = deepcopy(comparison())
        broken["positive_observation_aggregate"]["hybrid_union_trusted_payloads"] = 4
        with self.assertRaisesRegex(ValueError, "trusted counts"):
            self.build(comparison=broken)

    def test_comparison_excludes_raw_pdu_agreement_from_trusted_sum(self) -> None:
        document = comparison()
        document["same_iq_positive_observations"][0]["observation_id"] = 1
        document["same_iq_positive_observations"][0][
            "trusted_aggregate_included"
        ] = True
        document["same_iq_positive_observations"].append(
            {
                "observation_id": 2,
                "trusted_aggregate_included": False,
                "trusted_exclusion_reason": "No confirmed payload grammar.",
                "raw_pdu_agreement": {
                    "satnogs_crc_valid_pdu_count": 1,
                    "phase_first_crc_valid_pdu_count": 1,
                    "raw_union_pdu_count": 1,
                    "byte_identical": True,
                    "exact_shared_payload_sha256": "a" * 64,
                },
            }
        )
        aggregate = document["positive_observation_aggregate"]
        aggregate.update(
            {
                "listed_observation_count": 2,
                "excluded_from_trusted_aggregate_count": 1,
                "listed_observation_ids": [1, 2],
                "included_observation_ids": [1],
                "excluded_observation_ids": [2],
            }
        )
        report = self.build(comparison=document)
        baseline = report["evidence"]["same_iq_protocol_validated_baseline"]
        self.assertEqual(baseline["observation_count"], 1)
        self.assertEqual(baseline["listed_observation_count"], 2)
        self.assertEqual(baseline["excluded_observation_count"], 1)
        self.assertEqual(baseline["satnogs_trusted_payloads"], 2)

        document["same_iq_positive_observations"][1][
            "satnogs_trusted_payloads"
        ] = 1
        with self.assertRaisesRegex(ValueError, "has trusted payloads"):
            self.build(comparison=document)

    def test_markdown_states_campaign_is_not_telemetry_without_audit(self) -> None:
        markdown = render_campaign_markdown(self.build())
        self.assertIn("Sam CRC nie jest liczony jako telemetria", markdown)
        self.assertIn("SatNOGS 2", markdown)
        self.assertIn("complete: true", markdown)
        self.assertIn("próby 1/1, poprawne: 1, błędy: 0", markdown)

    def test_markdown_reports_protocol_audit_counts_separately(self) -> None:
        markdown = render_campaign_markdown(
            self.build(protocol_audit=protocol_audit())
        )
        self.assertIn("Audyt protokołu G3RUH: 1/1, complete: false", markdown)
        self.assertIn("zaufane ramki AX.25 UI: **1**", markdown)
        self.assertIn("odrzucone kolizje CRC: **1**", markdown)
        self.assertIn("wewnętrznym CCSDS: **1**", markdown)

    def test_markdown_does_not_render_none_as_a_progress_denominator(self) -> None:
        report = self.build(processing_manifest=None)
        markdown = render_campaign_markdown(report)
        self.assertIn("nie rozpoczęto (kwalifikujące rekordy: 1)", markdown)
        self.assertNotIn("/None", markdown)


if __name__ == "__main__":
    unittest.main()
