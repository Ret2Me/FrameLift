"""Independent integrity and set accounting for the OGG smoke pilot."""
import importlib.util
import hashlib
import json
from pathlib import Path
import sys

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
spec = importlib.util.spec_from_file_location("ogg_native_pilot", SCRIPTS / "ogg_native_pilot.py")
native = importlib.util.module_from_spec(spec)
spec.loader.exec_module(native)
sys.modules["ogg_native_pilot"] = native
spec = importlib.util.spec_from_file_location("ogg_pilot_report", SCRIPTS / "ogg_pilot_report.py")
report = importlib.util.module_from_spec(spec)
spec.loader.exec_module(report)


def test_independent_crc_known_check_and_corruption():
    from telemetry_yield.crc import append_ax25_fcs
    payload = bytes(range(60))
    frame = append_ax25_fcs(payload)
    assert native.independently_valid_fcs(frame)
    assert not native.independently_valid_fcs(frame[:-1] + bytes([frame[-1] ^ 1]))
    assert not native.independently_valid_fcs(b"\xff\xff")


def test_full_byte_comparison_separates_archive_and_component_increment():
    row = report.compare_sets({b"known", b"lost"}, {b"known", b"existing-extra"},
                              {b"known", b"existing-extra", b"native-extra"})
    assert row["native_new_vs_archive"]["count"] == 2
    assert row["native_new_vs_archive_and_baseline"]["count"] == 1
    assert row["native_only_vs_baseline"]["pdus"] == [b"native-extra".hex()]
    assert row["archive_not_reproduced_by_audio"]["pdus"] == [b"lost".hex()]
    assert row["archive_plus_audio_union"]["count"] == 4


def test_no_count_gain_for_repeat_or_equal_length_different_bytes():
    row = report.compare_sets({b"abc"}, {b"abc"}, {b"abc", b"xyz", b"xyz"})
    assert row["native_same_audio"]["count"] == 2
    assert row["native_only_vs_baseline"]["bytes"] == 3


@pytest.fixture
def retained_case(tmp_path):
    from telemetry_yield.crc import append_ax25_fcs

    def address(value, final):
        return bytes(ord(c) << 1 for c in value.ljust(6)) + bytes([0x60 | final])

    pdu = address("GROUND", 0) + address("N0CALL", 1) + b"\x03\xf0pilot"
    folder = tmp_path / "input"
    folder.mkdir()
    wav = folder / "audio.wav"
    wav.write_bytes(b"identical input content placeholder, not decoded by scorer")
    ref = folder / "reference.bin"
    ref.write_bytes(pdu)
    digest = hashlib.sha256(wav.read_bytes()).hexdigest()
    manifest = {"observation_id": 123, "satellite": "test", "observation_url": "https://example.invalid/123",
                "mode": "GMSK", "duration_seconds": 1, "wav": {"sha256": digest},
                "references": [{"path": str(ref), "sha256": hashlib.sha256(pdu).hexdigest(),
                                "payload_hex": pdu.hex(), "url": "https://example.invalid/pdu"}]}
    (folder / "input-manifest.json").write_text(json.dumps(manifest))
    native_path = tmp_path / "native.json"
    native_record = {"input": {"sha256": digest}, "failed_window_count": 0, "unique_pdu_count": 1,
                     "elapsed_seconds": 1, "controls": [], "frames": [{"payload_hex": pdu.hex(),
                     "frame_with_fcs_hex": append_ax25_fcs(pdu).hex(),
                     "payload_sha256": hashlib.sha256(pdu).hexdigest()}]}
    native_path.write_text(json.dumps(native_record))
    baseline_path = tmp_path / "baseline.json"
    baseline_record = {"input_sha256": digest, "completed": True, "malformed_kiss_records": 0,
                       "strict_ax25_ui_unique_count": 1, "wall_seconds": 1,
                       "pdus": [{"hex": pdu.hex(), "strict_ax25_ui": True}]}
    baseline_path.write_text(json.dumps(baseline_record))
    return folder, native_path, baseline_path


def test_retained_identical_payload_case(retained_case):
    row = report.compare_one(*retained_case)
    assert row["positive_reference_eligible"]
    assert row["counts"]["native_matching_archive"]["count"] == 1
    assert row["counts"]["native_new_vs_archive_and_baseline"]["count"] == 0


@pytest.mark.parametrize("mutation", ["input", "crc", "count", "failed", "reference", "baseline_failed"])
def test_corrupt_or_incomplete_artifacts_never_scored_as_success(retained_case, mutation):
    folder, native_path, baseline_path = retained_case
    record = json.loads(native_path.read_text())
    if mutation == "input":
        (folder / "audio.wav").write_bytes(b"changed")
    elif mutation == "crc":
        frame = bytearray.fromhex(record["frames"][0]["frame_with_fcs_hex"])
        frame[-1] ^= 1
        record["frames"][0]["frame_with_fcs_hex"] = frame.hex()
    elif mutation == "count":
        record["unique_pdu_count"] = 2
    elif mutation == "failed":
        record["failed_window_count"] = 1
    elif mutation == "reference":
        (folder / "reference.bin").write_bytes(b"changed")
    else:
        baseline = json.loads(baseline_path.read_text())
        baseline["completed"] = False
        baseline_path.write_text(json.dumps(baseline))
    native_path.write_text(json.dumps(record))
    with pytest.raises(ValueError):
        report.compare_one(*retained_case)
