from __future__ import annotations

import subprocess
import tempfile
import unittest
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from telemetry_yield.golden import (
    DireWolfAtestAdapter,
    GOLDEN_FILENAME_BLOCKERS,
    GOLDEN_FILENAME_OVERRIDES,
    GoldenDecodeError,
    GoldenMappingOverride,
    GrSatellitesAdapter,
    build_golden_manifest,
    inspect_golden_wav,
    normalize_satellite_key,
    ordered_frame_digest,
    parse_hex_dump,
    parse_supported_satellites,
    unique_frame_sha256,
    unique_frame_set_digest,
)
from telemetry_yield.harness import run_attempt
from telemetry_yield.store import AttemptStore


AO27_FRAME_1 = bytes.fromhex(
    "9c68aaa6924000829e646e40a80103f04ed02518"
)
AO27_FRAME_2 = bytes.fromhex(
    "9c68aaa6924000829e646e40a80103f04ed02218"
)


class GoldenParserTests(unittest.TestCase):
    def test_parses_gr_satellites_multiline_hexdump(self) -> None:
        output = """
pdu length = 20 bytes
pdu vector contents =
0000: 9c 68 aa a6 92 40 00 82 9e 64 6e 40 a8 01 03 f0
0010: 4e d0 25 18
************************************
pdu length = 20 bytes
pdu vector contents =
0000: 9c 68 aa a6 92 40 00 82 9e 64 6e 40 a8 01 03 f0
0010: 4e d0 22 18
"""
        self.assertEqual(parse_hex_dump(output), (AO27_FRAME_1, AO27_FRAME_2))

    def test_parses_atest_hexdump_with_ansi_and_ascii_columns(self) -> None:
        output = """
\x1b[38;2;0;192;0m  000:  9c 68 aa a6 92 40 00 82  ........
  008:  9e 64 6e 40 a8 01 03 f0  .dn@....
  010:  4e d0 22 18              N.\".
------
  000:  9c 68 aa a6 92 40 00 82  ........
  008:  9e 64 6e 40 a8 01 03 f0  .dn@....
  010:  4e d0 25 18              N.%.
"""
        self.assertEqual(parse_hex_dump(output), (AO27_FRAME_2, AO27_FRAME_1))

    def test_rejects_non_contiguous_hexdump(self) -> None:
        with self.assertRaises(GoldenDecodeError):
            parse_hex_dump("0000: 00 01\n0010: 02 03")

    def test_sequence_and_unique_hashes_have_different_semantics(self) -> None:
        left = (AO27_FRAME_1, AO27_FRAME_2)
        right = (AO27_FRAME_2, AO27_FRAME_1, AO27_FRAME_2)
        self.assertNotEqual(ordered_frame_digest(left), ordered_frame_digest(right))
        self.assertEqual(unique_frame_sha256(left), unique_frame_sha256(right))
        self.assertEqual(unique_frame_set_digest(left), unique_frame_set_digest(right))

    def test_parses_supported_satellites_and_direwolf_capability(self) -> None:
        output = """
Satellites supported by gr_satellites v5.9.0:
* AO-27 (NORAD 22825)
    1k2 AFSK telemetry downlink 436.795 MHz AFSK AX.25
* AALTO-1 (NORAD 42775)
    9k6 FSK AX.25 downlink 437.216 MHz FSK AX.25 G3RUH
"""
        satellites = parse_supported_satellites(output)
        self.assertEqual(satellites[0].name, "AO-27")
        self.assertEqual(satellites[0].norad_id, 22825)
        self.assertEqual(satellites[0].direwolf_baud, 1_200)
        self.assertIsNone(satellites[1].direwolf_baud)

    def test_manifest_uses_only_exact_or_explicit_mapping(self) -> None:
        supported = parse_supported_satellites(
            "* Exact Sat (NORAD 1)\n"
            "    1k2 AFSK downlink 1 MHz AFSK AX.25\n"
            "* Override Target (NORAD 2)\n"
            "    9k6 FSK downlink 2 MHz FSK AX.25 G3RUH\n"
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for filename in ("exact_sat.wav", "alias.wav", "unknown.wav"):
                (root / filename).touch()
            manifest = build_golden_manifest(
                root,
                supported,
                overrides={
                    "alias.wav": GoldenMappingOverride(
                        "Override Target",
                        "fixture evidence",
                    )
                },
                blockers={"unknown.wav": "fixture blocker"},
            )
        by_filename = {entry.filename: entry for entry in manifest}
        self.assertEqual(
            by_filename["exact_sat.wav"].match_method,
            "exact_normalized",
        )
        self.assertEqual(by_filename["exact_sat.wav"].direwolf_baud, 1_200)
        self.assertEqual(
            by_filename["alias.wav"].match_method,
            "explicit_override",
        )
        self.assertIsNone(by_filename["unknown.wav"].satellite)
        self.assertEqual(by_filename["unknown.wav"].mapping_evidence, "fixture blocker")

    def test_normalization_is_not_fuzzy(self) -> None:
        self.assertEqual(normalize_satellite_key("AALTO-1"), "aalto1")
        self.assertNotEqual(
            normalize_satellite_key("Astrocast"),
            normalize_satellite_key("Astrocast 0.1"),
        )


class GoldenWavTests(unittest.TestCase):
    def test_validates_48khz_mono_pcm(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "golden.wav"
            with wave.open(str(path), "wb") as output:
                output.setnchannels(1)
                output.setsampwidth(2)
                output.setframerate(48_000)
                output.writeframes(b"\x00\x00" * 480)
            metadata = inspect_golden_wav(path)
            self.assertEqual(metadata.channels, 1)
            self.assertEqual(metadata.sample_rate_hz, 48_000)
            self.assertAlmostEqual(metadata.duration_seconds, 0.01)

    def test_rejects_stereo_recording(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "stereo.wav"
            with wave.open(str(path), "wb") as output:
                output.setnchannels(2)
                output.setsampwidth(2)
                output.setframerate(48_000)
                output.writeframes(b"\x00\x00\x00\x00")
            with self.assertRaisesRegex(GoldenDecodeError, "mono"):
                inspect_golden_wav(path)


class GoldenAdapterTests(unittest.TestCase):
    def test_gr_adapter_freezes_environment_and_disables_submission(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            executable = root / "gr_satellites"
            executable.touch()
            recording = root / "golden.wav"
            with wave.open(str(recording), "wb") as output:
                output.setnchannels(1)
                output.setsampwidth(2)
                output.setframerate(48_000)
                output.writeframes(b"\x00\x00" * 480)
            completed = SimpleNamespace(returncode=0, stdout="")
            with mock.patch(
                "telemetry_yield.golden.subprocess.run",
                return_value=completed,
            ) as run:
                frames = GrSatellitesAdapter(executable, "test").decode(
                    recording,
                    "fixture",
                    {
                        "satellite": "FixtureSat",
                        "sample_rate_hz": 48_000,
                        "throttle": True,
                    },
                    5.0,
                    seed=7,
                )

        self.assertEqual(frames, ())
        command = run.call_args.args[0]
        environment = run.call_args.kwargs["env"]
        self.assertIn("--throttle", command)
        self.assertEqual(environment["GR_SATELLITES_SUBMIT_TLM"], "0")
        self.assertEqual(environment["GR_SCHEDULER"], "TPB")
        self.assertEqual(environment["PYTHONHASHSEED"], "7")
        self.assertEqual(environment["LC_ALL"], "C")
        self.assertEqual(environment["TZ"], "UTC")

    def test_gr_adapter_rejects_non_boolean_throttle(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            executable = Path(temporary) / "gr_satellites"
            executable.touch()
            with self.assertRaisesRegex(ValueError, "throttle"):
                GrSatellitesAdapter(executable, "test").decode(
                    Path(temporary) / "missing.wav",
                    "fixture",
                    {
                        "satellite": "FixtureSat",
                        "throttle": "yes",
                    },
                    5.0,
                    seed=0,
                )

    def test_gr_adapter_rejects_unavailable_sts_scheduler(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            executable = Path(temporary) / "gr_satellites"
            executable.touch()
            with self.assertRaisesRegex(ValueError, "scheduler"):
                GrSatellitesAdapter(executable, "test").decode(
                    Path(temporary) / "missing.wav",
                    "fixture",
                    {
                        "satellite": "FixtureSat",
                        "scheduler": "STS",
                    },
                    5.0,
                    seed=0,
                )


class RealGoldenIntegrationTests(unittest.TestCase):
    def test_current_repository_manifest_has_explicit_coverage(self) -> None:
        project = Path(__file__).resolve().parents[1]
        recording_dir = project / "work/golden/satellite-recordings"
        executable = project / "work/golden/env/bin/gr_satellites"
        if not recording_dir.exists() or not executable.exists():
            self.skipTest("optional real golden corpus or decoder is unavailable")
        output = subprocess.run(
            [str(executable), "--list_satellites"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        manifest = build_golden_manifest(
            recording_dir,
            parse_supported_satellites(output),
        )
        methods = [entry.match_method for entry in manifest]
        self.assertEqual(len(manifest), 92)
        self.assertEqual(methods.count("exact_normalized"), 69)
        self.assertEqual(methods.count("explicit_override"), 18)
        self.assertEqual(methods.count("blocked"), 5)
        self.assertEqual(
            {entry.filename for entry in manifest if entry.satellite is None},
            set(GOLDEN_FILENAME_BLOCKERS),
        )
        self.assertEqual(len(GOLDEN_FILENAME_OVERRIDES), 18)

    def test_independent_decoders_agree_on_public_ao27_payloads(self) -> None:
        project = Path(__file__).resolve().parents[1]
        recording = project / "work/golden/satellite-recordings/ao27.wav"
        gr_satellites = project / "work/golden/env/bin/gr_satellites"
        atest = Path("/usr/bin/atest")
        required = (recording, gr_satellites, atest)
        if not all(path.exists() for path in required):
            self.skipTest("optional real golden corpus or decoder is unavailable")

        metadata = inspect_golden_wav(recording)
        self.assertEqual(
            metadata.sha256,
            "00315dd4f083c2f21fef597d7d4f83bf0d5e97f8499b3bf2559055b3cfe163e2",
        )
        # This checks frame agreement, not a latency SLO.  GNU Radio startup can
        # exceed ten wall-clock seconds on a shared, CPU-saturated CI host.
        decoder_timeout_s = 60.0
        with tempfile.TemporaryDirectory() as temporary:
            result = run_attempt(
                store=AttemptStore(Path(temporary) / "attempts.sqlite"),
                adapter=GrSatellitesAdapter(gr_satellites, "5.9.0"),
                recording_id="public-ao27",
                recording_path=recording,
                protocol_id="afsk1200_ax25_emitted_payload",
                parameters={"satellite": "AO-27", "sample_rate_hz": 48_000},
                timeout_s=decoder_timeout_s,
            )
        self.assertIsNotNone(result)
        self.assertEqual(result.status, "ok")
        self.assertGreater(result.cpu_seconds, 0.1)
        self.assertGreater(result.peak_rss_mb, 40.0)
        gr_frames = tuple(bytes.fromhex(value) for value in result.raw_frames_hex)
        direwolf_frames = DireWolfAtestAdapter(atest, "1.7").decode(
            recording,
            "afsk1200_ax25",
            {"baud": 1_200, "profile": "E+"},
            decoder_timeout_s,
            seed=0,
        )

        self.assertEqual(gr_frames, (AO27_FRAME_1, AO27_FRAME_2))
        self.assertEqual(
            direwolf_frames,
            (AO27_FRAME_2, AO27_FRAME_1, AO27_FRAME_2),
        )
        self.assertEqual(
            unique_frame_sha256(gr_frames),
            unique_frame_sha256(direwolf_frames),
        )


if __name__ == "__main__":
    unittest.main()
