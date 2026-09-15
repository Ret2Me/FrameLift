from __future__ import annotations

import hashlib
import json
from pathlib import Path
import struct
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from telemetry_yield.archive_processing import (
    EXPLICIT_MODE,
    ProcessingCandidate,
    convert_ci16_to_cf32,
    locate_ci16_input,
    load_processing_events,
    run_processing_campaign,
    select_explicit_g3ruh_zero_iq,
)


def catalog_item(
    observation_id: int,
    *,
    mode: str = EXPLICIT_MODE,
    frames_recovered: bool = False,
    iq: bool = True,
    size: int = 8,
) -> dict[str, object]:
    return {
        "observation_id": observation_id,
        "mode": mode,
        "frames_recovered": frames_recovered,
        "links": {"iq": "https://example.test/sample.iq" if iq else None},
        "artifacts": {"iq": {"size_bytes": size}},
        "satellite_id": "SAT",
    }


class ArchiveProcessingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_selection_requires_explicit_mode_zero_frames_and_iq(self) -> None:
        catalogue = {
            "observations": [
                catalog_item(5, mode="GMSK"),
                catalog_item(4, mode="FSK"),
                catalog_item(3, frames_recovered=True),
                catalog_item(2, iq=False),
                catalog_item(1),
            ]
        }
        selected = select_explicit_g3ruh_zero_iq(catalogue)
        self.assertEqual([item.observation_id for item in selected], [1])
        self.assertEqual(selected[0].mode, EXPLICIT_MODE)

    def test_input_resolution_supports_flat_campaign_and_legacy_layout(self) -> None:
        campaign = self.root / "campaign"
        legacy = self.root / "legacy"
        campaign.mkdir()
        candidate = ProcessingCandidate(7, 8, EXPLICIT_MODE)
        nested = legacy / "obs-7" / "observation_7.iq"
        nested.parent.mkdir(parents=True)
        nested.write_bytes(b"12345678")
        self.assertEqual(
            locate_ci16_input(candidate, campaign, legacy_root=legacy), nested
        )
        flat = campaign / "observation_7.iq"
        flat.write_bytes(b"abcdefgh")
        self.assertEqual(
            locate_ci16_input(candidate, campaign, legacy_root=legacy), flat
        )

    def test_conversion_is_exact_manifested_and_reused(self) -> None:
        source = self.root / "observation_1.iq"
        source.write_bytes(struct.pack("<hhhh", -32768, 32767, 16384, -16384))
        output = self.root / "observation_1.cf32"
        manifest = self.root / "conversion-manifest.json"
        first = convert_ci16_to_cf32(source, output, manifest, chunk_complex_samples=1)
        values = struct.unpack("<ffff", output.read_bytes())
        self.assertEqual(values, (-1.0, 32767 / 32768, 0.5, -0.5))
        self.assertFalse(first.reused)
        document = json.loads(manifest.read_text())
        self.assertEqual(document["input"]["sha256"], hashlib.sha256(source.read_bytes()).hexdigest())
        self.assertEqual(document["input"]["size_bytes"], 8)
        self.assertEqual(document["output"]["size_bytes"], 16)
        second = convert_ci16_to_cf32(source, output, manifest, chunk_complex_samples=1)
        self.assertTrue(second.reused)
        self.assertEqual(second.cf32_sha256, first.cf32_sha256)

    def test_campaign_continues_after_missing_input_and_skips_completed(self) -> None:
        campaign = self.root / "campaign"
        campaign.mkdir()
        raw = struct.pack("<hhhh", 1, -1, 2, -2)
        (campaign / "observation_1.iq").write_bytes(raw)
        checkpoint = campaign / "processing-checkpoint.jsonl"
        manifest = campaign / "processing-manifest.json"
        decoder = self.root / "decoder.py"
        decoder.write_text("placeholder")
        calls: list[tuple[list[str], bool]] = []

        def fake_runner(argv: list[str], **kwargs: object) -> SimpleNamespace:
            calls.append((argv, bool(kwargs["shell"])))
            output = Path(argv[argv.index("--output") + 1])
            input_path = Path(argv[argv.index("--input") + 1])
            observation_id = int(argv[argv.index("--observation-id") + 1])
            output.write_text(
                json.dumps(
                    {
                        "stage": "decode-only",
                        "observation_id": observation_id,
                        "input_sha256": hashlib.sha256(input_path.read_bytes()).hexdigest(),
                        "unique_crc_valid_frame_count": 0,
                        "unique_crc_valid_frames": [],
                    }
                )
            )
            return SimpleNamespace(returncode=0)

        candidates = (
            ProcessingCandidate(1, len(raw), EXPLICIT_MODE),
            ProcessingCandidate(2, len(raw), EXPLICIT_MODE),
        )
        first = run_processing_campaign(
            candidates,
            campaign,
            legacy_root=None,
            decoder_script=decoder,
            checkpoint_path=checkpoint,
            manifest_path=manifest,
            workers=2,
            runner=fake_runner,
        )
        self.assertEqual([item.status for item in first], ["processed", "error"])
        self.assertEqual(len(calls), 1)
        self.assertFalse(calls[0][1])
        self.assertIn("decode", calls[0][0])
        self.assertEqual(len(load_processing_events(checkpoint)), 2)
        snapshot = json.loads(manifest.read_text())
        self.assertFalse(snapshot["selection_contract"]["generic_fsk_implies_ax25"])
        self.assertEqual(snapshot["status_counts"], {"error": 1, "processed": 1})

        def forbidden_runner(*args: object, **kwargs: object) -> SimpleNamespace:
            raise AssertionError("completed decoder must not run again")

        second = run_processing_campaign(
            candidates[:1],
            campaign,
            legacy_root=None,
            decoder_script=decoder,
            checkpoint_path=checkpoint,
            manifest_path=manifest,
            runner=forbidden_runner,
        )
        self.assertEqual(second[0].status, "skipped_complete")
        self.assertEqual(second[0].conversion_status, "reused")
        self.assertEqual(second[0].decoder_status, "reused")


if __name__ == "__main__":
    unittest.main()
