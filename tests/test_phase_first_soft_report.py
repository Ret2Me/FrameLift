from __future__ import annotations

import hashlib
import json
from pathlib import Path
import unittest

from telemetry_yield.crc import append_ax25_fcs


ROOT = Path(__file__).resolve().parents[1]
REPORT_PATH = ROOT / "reports/phase-first-soft-list.json"


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class PhaseFirstSoftReportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.report = json.loads(REPORT_PATH.read_text())

    def test_strict_yield_and_negative_control_acceptance(self) -> None:
        acceptance = self.report["acceptance"]
        self.assertTrue(acceptance["passed"])
        self.assertEqual(acceptance["matched_reference_count"], 4)
        self.assertEqual(acceptance["required_reference_count"], 4)
        self.assertTrue(acceptance["two_repeats_equal"])
        self.assertEqual(acceptance["negative_control_crc_valid_frames"], 0)
        self.assertTrue(acceptance["negative_controls_passed"])

    def test_each_event_has_one_repeat_stable_exact_match(self) -> None:
        self.assertEqual(len(self.report["events"]), 4)
        for event in self.report["events"]:
            with self.subTest(event=event["event"]):
                self.assertTrue(event["repeat_byte_equal"])
                self.assertEqual(len(event["byte_identical_reference_matches"]), 1)
                self.assertEqual(
                    event["byte_identical_reference_matches"][0]["payload_sha256"],
                    event["reference_sha256_diagnostic_only"],
                )
                reference = (
                    ROOT / "work/camras/12511021" / event["reference_name_diagnostic_only"]
                ).read_bytes()
                match = event["byte_identical_reference_matches"][0]
                self.assertEqual(bytes.fromhex(match["payload_hex"]), reference)
                self.assertEqual(
                    bytes.fromhex(match["frame_with_fcs_hex"]),
                    append_ax25_fcs(reference),
                )
                self.assertEqual(
                    event["candidate_repeats"][0], event["candidate_repeats"][1]
                )

    def test_soft_left_boundary_and_diversity_are_disclosed(self) -> None:
        event_250 = next(
            event for event in self.report["events"] if event["event"] == "09-09-37-1"
        )
        match_250 = event_250["byte_identical_reference_matches"][0]
        self.assertEqual(match_250["left_flag_hamming"], 3)
        self.assertIn("Hamming<=3", self.report["acceptance"]["criterion"])
        late = next(
            event for event in self.report["events"] if event["event"] == "09-13-35"
        )
        pool = late["candidate_repeats"][0]["candidate_pool"]
        self.assertEqual(pool["items"], 36)
        self.assertFalse(pool["reference_bytes_used"])
        self.assertIn(
            "legacy-abs-ge-32767",
            self.report["candidate"]["legacy_clipping_mask_disclosure"],
        )

    def test_embedded_input_hashes_match_files(self) -> None:
        inputs = self.report["input"]
        for path_key, hash_key in (
            ("manifest", "manifest_sha256"),
            ("upstream_runner", "upstream_runner_sha256"),
            ("campaign_runner", "campaign_runner_sha256"),
        ):
            path = ROOT / inputs[path_key]
            self.assertEqual(sha256_file(path), inputs[hash_key])
        for event in self.report["events"]:
            artifact = event["soft_artifact"]
            self.assertEqual(
                sha256_file(ROOT / artifact["path"]), artifact["verified_sha256"]
            )
            if event["event"] != "09-13-35":
                continue
            for diversity in event["candidate_repeats"][0]["diversity_inputs"]:
                self.assertEqual(
                    sha256_file(ROOT / diversity["manifest_path"]),
                    diversity["manifest_sha256"],
                )
                self.assertEqual(
                    sha256_file(ROOT / diversity["soft_path"]),
                    diversity["soft_sha256"],
                )
                self.assertEqual(
                    sha256_file(ROOT / diversity["levels_path"]),
                    diversity["levels_sha256"],
                )

    def test_negative_windows_are_repeat_stable_and_empty(self) -> None:
        self.assertEqual(len(self.report["negative_controls"]), 2)
        for control in self.report["negative_controls"]:
            with self.subTest(window=control["window"]):
                self.assertTrue(control["repeat_byte_equal"])
                self.assertEqual(
                    control["candidate_repeats"][0],
                    control["candidate_repeats"][1],
                )
                self.assertFalse(
                    control["candidate_repeats"][0]["accepted_crc_valid_frames"]
                )


if __name__ == "__main__":
    unittest.main()
