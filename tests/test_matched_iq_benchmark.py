from __future__ import annotations

import hashlib
import json
from pathlib import Path
import struct
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from telemetry_yield.crc import append_ax25_fcs
from telemetry_yield.matched_iq_benchmark import (
    CHECKPOINT_SCHEMA,
    PositiveBenchmarkCandidate,
    ReferencePayload,
    audit_catalogue_references,
    build_observation_comparison,
    documented_reference_payload_variants,
    load_benchmark_events,
    run_positive_benchmark,
    select_positive_explicit_g3ruh,
)


def address(callsign: str, *, final: bool) -> bytes:
    return bytes(ord(character) << 1 for character in callsign.ljust(6)) + bytes(
        (0x60 | int(final),)
    )


VALID_PAYLOAD = address("CQ", final=False) + address("N0CALL", final=True) + b"\x03\xf0data"
VALID_FRAME = append_ax25_fcs(VALID_PAYLOAD)
COLLISION = append_ax25_fcs(bytes.fromhex("e0470c2aab79e4"))


def candidate(observation_id: int = 1, *, size: int = 8) -> PositiveBenchmarkCandidate:
    return PositiveBenchmarkCandidate(
        observation_id,
        size,
        "SAT",
        (
            ReferencePayload(1, VALID_PAYLOAD, "https://example.test/frame"),
            ReferencePayload(2, b"not-ax25", None),
        ),
    )


def phase_artifact(path: Path, observation_id: int, frames: list[bytes], input_sha: str = "a" * 64) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "stage": "decode-only",
                "observation_id": observation_id,
                "input_sha256": input_sha,
                "unique_crc_valid_frame_count": len(frames),
                "unique_crc_valid_frames": [
                    {
                        "frame_with_fcs_hex": frame.hex(),
                        "frame_with_fcs_sha256": hashlib.sha256(frame).hexdigest(),
                    }
                    for frame in frames
                ],
            }
        )
    )


class MatchedIQBenchmarkTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_selection_is_exact_and_binds_raw_reference_bytes(self) -> None:
        def observation(identifier: int, mode: str, recovered: bool) -> dict[str, object]:
            return {
                "observation_id": identifier,
                "mode": mode,
                "frames_recovered": recovered,
                "links": {"iq": "https://example.test/iq"},
                "artifacts": {"iq": {"size_bytes": 8}},
                "satellite_id": "SAT",
                "frames": [
                    {
                        "frame_id": 1,
                        "payload_hex": VALID_PAYLOAD.hex(),
                        "size_bytes": len(VALID_PAYLOAD),
                        "url": "https://example.test/frame",
                    }
                ],
            }

        selected = select_positive_explicit_g3ruh(
            {
                "observations": [
                    observation(3, "FSK", True),
                    observation(2, "FSK AX.25 G3RUH", False),
                    observation(1, "FSK AX.25 G3RUH", True),
                ]
            }
        )
        self.assertEqual([item.observation_id for item in selected], [1])
        self.assertEqual(selected[0].references[0].payload, VALID_PAYLOAD)
        self.assertIsNone(selected[0].references[0].filename)

    def test_documented_container_contract_never_guesses_ax25_offset(self) -> None:
        # A byte scan would find VALID_PAYLOAD at offset four. The official
        # upload contract says those four bytes are part of the stored PDU, so
        # the only permitted attempt is the complete object at offset zero.
        prefixed = b"\x00\x00\xca\x00" + VALID_PAYLOAD
        reference = ReferencePayload(
            7,
            prefixed,
            "https://example.test/frame",
            "data_4048_2026-08-29T05-09-33",
        )
        variants = documented_reference_payload_variants(reference)
        self.assertEqual(len(variants), 1)
        self.assertEqual(variants[0].kind, "raw_uploaded_pdu")
        self.assertEqual(variants[0].offset_bytes, 0)
        self.assertEqual(variants[0].payload, prefixed)

        audited, trusted = audit_catalogue_references(
            PositiveBenchmarkCandidate(4048, 8, "SAT", (reference,))
        )
        self.assertEqual(trusted, set())
        self.assertFalse(audited["arbitrary_byte_offset_scan"])
        record = audited["records"][0]
        self.assertIsNone(record["selected_variant"])
        self.assertEqual(
            [
                (item["variant_kind"], item["offset_bytes"])
                for item in record["validation_attempts"]
            ],
            [("raw_uploaded_pdu", 0)],
        )

    def test_gr_satellites_filename_records_already_unwrapped_kiss_contract(
        self,
    ) -> None:
        reference = ReferencePayload(
            8,
            VALID_PAYLOAD,
            None,
            "data_3413_2026-08-23T05-53-50_g0",
        )
        variant = documented_reference_payload_variants(reference)[0]
        self.assertEqual(
            variant.container_contract,
            "gr_satellites_kiss_already_unwrapped_before_upload",
        )
        audited, trusted = audit_catalogue_references(
            PositiveBenchmarkCandidate(3413, 8, "SAT", (reference,))
        )
        self.assertEqual(trusted, {VALID_PAYLOAD})
        self.assertEqual(
            audited["records"][0]["selected_variant"]["offset_bytes"], 0
        )

    def test_invalid_reference_makes_incremental_claim_non_comparable(self) -> None:
        reference = ReferencePayload(
            9,
            b"\x00\x00\xca\x00" + VALID_PAYLOAD,
            None,
            "data_4048_2026-08-29T05-09-33",
        )
        benchmark_candidate = PositiveBenchmarkCandidate(
            4048, 8, "SAT", (reference,)
        )
        phase = self.root / "phase-non-comparable.json"
        phase_artifact(phase, 4048, [VALID_FRAME])
        iq = self.root / "input-non-comparable.iq"
        iq.write_bytes(b"12345678")
        cf32 = self.root / "input-non-comparable.cf32"
        cf32.write_bytes(b"0" * 16)
        result = build_observation_comparison(
            benchmark_candidate,
            iq_path=iq,
            iq_sha256=hashlib.sha256(iq.read_bytes()).hexdigest(),
            cf32_path=cf32,
            cf32_sha256=hashlib.sha256(cf32.read_bytes()).hexdigest(),
            phase_artifact_path=phase,
        )
        self.assertEqual(
            result["sets"]["comparison_status"],
            "non_comparable_no_trusted_baseline",
        )
        self.assertIsNone(result["sets"]["incremental_over_baseline"])
        self.assertEqual(
            result["sets"]["native_not_in_trusted_baseline_count"], 1
        )

    def test_comparison_never_counts_crc_collision_or_malformed_reference(self) -> None:
        phase = self.root / "phase.json"
        phase_artifact(phase, 1, [VALID_FRAME, COLLISION])
        iq = self.root / "input.iq"
        iq.write_bytes(b"12345678")
        cf32 = self.root / "input.cf32"
        cf32.write_bytes(b"0" * 16)
        result = build_observation_comparison(
            candidate(),
            iq_path=iq,
            iq_sha256=hashlib.sha256(iq.read_bytes()).hexdigest(),
            cf32_path=cf32,
            cf32_sha256=hashlib.sha256(cf32.read_bytes()).hexdigest(),
            phase_artifact_path=phase,
        )
        self.assertEqual(result["baseline"]["raw_unique_payload_count"], 2)
        self.assertEqual(result["baseline"]["trusted_unique_payload_count"], 1)
        self.assertEqual(result["native"]["raw_crc_valid_candidate_count"], 2)
        self.assertEqual(result["native"]["trusted_ax25_ui_unique_payload_count"], 1)
        self.assertEqual(result["native"]["rejected_crc_collision_count"], 1)
        self.assertEqual(
            result["sets"],
            {
                **result["sets"],
                "baseline_count": 1,
                "native_count": 1,
                "overlap_count": 1,
                "union_count": 1,
                "incremental_over_baseline": 0,
                "baseline_only_count": 0,
            },
        )

    def test_runner_checkpoints_continues_and_reuses_complete_result(self) -> None:
        raw = struct.pack("<hhhh", 1, -1, 2, -2)
        (self.root / "observation_1.iq").write_bytes(raw)
        good = candidate(1, size=len(raw))
        missing = candidate(2, size=len(raw))
        decoder = self.root / "decoder.py"
        decoder.write_text("placeholder")
        checkpoint = self.root / "checkpoint.jsonl"
        manifest = self.root / "manifest.json"
        calls = 0

        def fake_runner(argv: list[str], **kwargs: object) -> SimpleNamespace:
            nonlocal calls
            calls += 1
            self.assertFalse(kwargs["shell"])
            output = Path(argv[argv.index("--output") + 1])
            input_path = Path(argv[argv.index("--input") + 1])
            phase_artifact(
                output,
                int(argv[argv.index("--observation-id") + 1]),
                [VALID_FRAME],
                hashlib.sha256(input_path.read_bytes()).hexdigest(),
            )
            return SimpleNamespace(returncode=0)

        first = run_positive_benchmark(
            (good, missing),
            (good, missing),
            self.root,
            legacy_root=None,
            decoder_script=decoder,
            checkpoint_path=checkpoint,
            manifest_path=manifest,
            runner=fake_runner,
        )
        self.assertEqual([item["status"] for item in first], ["processed", "error"])
        self.assertEqual(calls, 1)
        snapshot = json.loads(manifest.read_text())
        self.assertFalse(snapshot["complete"])
        self.assertEqual(snapshot["latest_result_count"], 2)
        self.assertEqual(snapshot["aggregate"]["union_count"], 1)

        def forbidden(*args: object, **kwargs: object) -> SimpleNamespace:
            raise AssertionError("decoder must be skipped")

        second = run_positive_benchmark(
            (good,),
            (good, missing),
            self.root,
            legacy_root=None,
            decoder_script=decoder,
            checkpoint_path=checkpoint,
            manifest_path=manifest,
            runner=forbidden,
        )
        self.assertEqual(second[0]["status"], "skipped_complete")

    def test_empty_shard_writes_incomplete_manifest(self) -> None:
        expected = candidate()
        manifest = self.root / "manifest.json"
        results = run_positive_benchmark(
            (),
            (expected,),
            self.root,
            legacy_root=None,
            decoder_script=self.root / "decoder.py",
            checkpoint_path=self.root / "checkpoint.jsonl",
            manifest_path=manifest,
        )
        self.assertEqual(results, ())
        snapshot = json.loads(manifest.read_text())
        self.assertFalse(snapshot["complete"])
        self.assertEqual(snapshot["remaining_observation_ids"], [1])

    def test_foreign_checkpoint_schema_fails_closed(self) -> None:
        checkpoint = self.root / "checkpoint.jsonl"
        checkpoint.write_text(
            json.dumps(
                {
                    "schema_version": CHECKPOINT_SCHEMA + "-other",
                    "observation_id": 1,
                    "status": "processed",
                }
            )
            + "\n"
        )
        with self.assertRaisesRegex(ValueError, "invalid benchmark checkpoint"):
            load_benchmark_events(checkpoint)


if __name__ == "__main__":
    unittest.main()
