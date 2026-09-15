from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np

from telemetry_yield.afsk1200_orchestration import (
    API_VERSION,
    Afsk1200RunConfig,
    run_afsk1200_file,
)
from telemetry_yield.afsk1200_plugin import Afsk1200Config
from telemetry_yield.cli import main
from telemetry_yield.generic_receiver import (
    ReceiverHypothesis,
    WaveformHypothesis,
    default_receiver,
)
from tests.test_afsk1200_plugin import REFERENCE, _synthetic_afsk_iq


def _write_ci16(path: Path, iq: np.ndarray) -> None:
    output = np.empty((iq.size, 2), dtype="<i2")
    output[:, 0] = np.clip(np.real(iq) * 12_000, -32_768, 32_767).astype("<i2")
    output[:, 1] = np.clip(np.imag(iq) * 12_000, -32_768, 32_767).astype("<i2")
    output.tofile(path)


def _small_config(**changes) -> Afsk1200RunConfig:
    plugin = Afsk1200Config(window_blocks=16, stride_blocks=8)
    return Afsk1200RunConfig(plugin=replace(plugin, **changes))


class Afsk1200OrchestrationTests(unittest.TestCase):
    def test_default_receiver_routes_bell202_to_plain_ax25(self) -> None:
        receiver = default_receiver()
        waveform = WaveformHypothesis(
            hypothesis_id="afsk1200-bell202",
            demodulator_id="bell202_afsk",
            symbol_rate=1_200.0,
            decimation=6,
            rate_errors_ppm=(0.0,),
            phase_bins=16,
            top_timing_hypotheses=16,
            parameters={"mark_hz": 1_200.0, "space_hz": 2_200.0},
            modulation_family="afsk",
        )
        result = receiver.decode_iq(
            _synthetic_afsk_iq(),
            sample_rate_hz=57_600.0,
            plan=(ReceiverHypothesis(waveform, "ax25_plain"),),
        )
        self.assertIn(REFERENCE, {frame.payload[:-2] for frame in result.frames})

    def test_cli_routes_synthetic_positive_into_trusted_ledger(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "positive.iq"
            output = root / "result.json"
            _write_ci16(source, _synthetic_afsk_iq())
            status = main(
                [
                    "decode-afsk1200",
                    str(source),
                    "--output",
                    str(output),
                    "--window-blocks",
                    "16",
                    "--stride-blocks",
                    "8",
                ]
            )
            document = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(status, 0)
        self.assertEqual(document["api_version"], API_VERSION)
        self.assertEqual(document["status"], "completed")
        self.assertEqual(document["route"]["demodulator_id"], "bell202_afsk")
        self.assertEqual(document["route"]["protocol_adapter_id"], "ax25_plain")
        self.assertGreaterEqual(len(document["candidates"]["trusted"]), 1)
        self.assertEqual(document["candidate_ledger"]["union_count"], 1)
        self.assertEqual(
            document["candidate_ledger"]["frames"][0]["normalized_payload_hex"],
            REFERENCE.hex(),
        )

    def test_python_api_rejects_unknown_contract_version(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "positive.iq"
            _write_ci16(source, _synthetic_afsk_iq())
            with self.assertRaisesRegex(ValueError, "unsupported AFSK1200 API version"):
                run_afsk1200_file(
                    source,
                    root / "result.json",
                    config=_small_config(),
                    api_version="telemetry-yield-afsk1200-file-api-v999",
                )

    def test_zero_and_seeded_noise_are_fail_closed(self) -> None:
        rng = np.random.default_rng(20_260_902)
        streams = (
            np.zeros(70_000, dtype=np.complex64),
            np.asarray(
                rng.normal(size=70_000) + 1j * rng.normal(size=70_000),
                dtype=np.complex64,
            ),
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for index, iq in enumerate(streams):
                source = root / f"negative-{index}.iq"
                output = root / f"negative-{index}.json"
                _write_ci16(source, iq)
                document = run_afsk1200_file(
                    source, output, config=_small_config()
                )
                self.assertEqual(document["candidates"]["trusted"], [])
                self.assertEqual(document["candidate_ledger"]["union_count"], 0)

    def test_crc_valid_non_ax25_is_raw_and_rejected_not_trusted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "invalid-ax25.iq"
            output = root / "invalid-ax25.json"
            _write_ci16(source, _synthetic_afsk_iq(b"not-an-ax25-ui-frame"))
            document = run_afsk1200_file(source, output, config=_small_config())
        self.assertGreaterEqual(len(document["candidates"]["raw"]), 1)
        self.assertGreaterEqual(len(document["candidates"]["rejected"]), 1)
        self.assertEqual(document["candidates"]["trusted"], [])
        self.assertEqual(document["candidate_ledger"]["union_count"], 0)

    def test_corrupt_truncated_and_mismatched_inputs_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            truncated = root / "truncated.iq"
            truncated.write_bytes(b"\x00\x01\x02")
            with self.assertRaisesRegex(ValueError, "whole complex samples"):
                run_afsk1200_file(truncated, root / "truncated.json")

            source = root / "valid-size.iq"
            _write_ci16(source, np.zeros(20_000, dtype=np.complex64))
            mismatch = Afsk1200RunConfig(expected_input_sha256="0" * 64)
            with self.assertRaisesRegex(ValueError, "SHA-256"):
                run_afsk1200_file(source, root / "mismatch.json", config=mismatch)
            outside = Afsk1200RunConfig(segment_start_sample=19_000, segment_sample_count=2_000)
            with self.assertRaisesRegex(ValueError, "extends beyond"):
                run_afsk1200_file(source, root / "outside.json", config=outside)

    def test_partial_resume_matches_fresh_run_and_reports_memory_bound(self) -> None:
        iq = np.tile(_synthetic_afsk_iq(), 3)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "long.iq"
            _write_ci16(source, iq)
            config = _small_config()
            partial = run_afsk1200_file(
                source,
                root / "resumed.json",
                config=config,
                checkpoint_path=root / "resume.checkpoint.json",
                max_windows=1,
            )
            self.assertEqual(partial["status"], "partial")
            resumed = run_afsk1200_file(
                source,
                root / "resumed.json",
                config=config,
                checkpoint_path=root / "resume.checkpoint.json",
            )
            fresh = run_afsk1200_file(
                source,
                root / "fresh.json",
                config=config,
                checkpoint_path=root / "fresh.checkpoint.json",
                resume=False,
            )
            resumed_bytes = (root / "resumed.json").read_bytes()
            fresh_bytes = (root / "fresh.json").read_bytes()
        self.assertEqual(resumed, fresh)
        self.assertEqual(resumed_bytes, fresh_bytes)
        counters = resumed["resource_counters"]
        expected_window = (
            config.plugin.window_blocks * config.plugin.permutation_block_samples
        )
        self.assertLessEqual(
            counters["maximum_complex_samples_materialized_at_once"],
            expected_window,
        )
        self.assertGreater(counters["windows_total"], 1)

    def test_tampered_resume_checkpoint_is_rejected(self) -> None:
        iq = np.tile(_synthetic_afsk_iq(), 2)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "resume-source.iq"
            checkpoint = root / "checkpoint.json"
            _write_ci16(source, iq)
            run_afsk1200_file(
                source,
                root / "partial.json",
                config=_small_config(),
                checkpoint_path=checkpoint,
                max_windows=1,
            )
            value = json.loads(checkpoint.read_text(encoding="utf-8"))
            first = next(iter(value["windows"].values()))
            first["metrics"]["windows_examined"] = 999
            checkpoint.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "digest does not match"):
                run_afsk1200_file(
                    source,
                    root / "resumed.json",
                    config=_small_config(),
                    checkpoint_path=checkpoint,
                )

    def test_external_validated_baseline_unions_without_inflation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "positive.iq"
            _write_ci16(source, _synthetic_afsk_iq())
            native = run_afsk1200_file(
                source, root / "native.json", config=_small_config()
            )
            trusted = native["candidates"]["trusted"][0]
            source_sha = hashlib.sha256(source.read_bytes()).hexdigest()
            baseline = {
                "schema_version": "candidate-ledger-origins-v1",
                "detections": [
                    {
                        "capture_sha256": source_sha,
                        "segment_start_sample": 0,
                        "segment_sample_count": source.stat().st_size // 4,
                        "branch_id": "external-baseline",
                        "source_role": "baseline",
                        "plugin_id": "fixture-baseline",
                        "plugin_version": "1",
                        "protocol_id": "ax25",
                        "original_frame_hex": trusted["normalized_payload_hex"],
                        "normalized_payload_hex": trusted["normalized_payload_hex"],
                        "validation_layers": ["fixture_independent_validation"],
                        "validated": True,
                        "hypothesis_fingerprint": "fixture",
                        "config_fingerprint": "fixture",
                        "event_key": "capture-segment",
                        "event_time_seconds": 0.0,
                        "provenance": {"fixture": True},
                    }
                ],
            }
            baseline_path = root / "baseline.json"
            baseline_path.write_text(json.dumps(baseline), encoding="utf-8")
            union = run_afsk1200_file(
                source,
                root / "union.json",
                config=_small_config(),
                checkpoint_path=root / "union.checkpoint.json",
                external_baseline_path=baseline_path,
            )
        ledger = union["candidate_ledger"]
        self.assertEqual(ledger["baseline_count"], 1)
        self.assertEqual(ledger["candidate_count"], 1)
        self.assertEqual(ledger["union_count"], 1)
        self.assertGreaterEqual(ledger["origin_count"], 2)


if __name__ == "__main__":
    unittest.main()
