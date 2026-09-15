"""Offline qualification: no public downloads, no archive campaign, no tuning."""
import copy
from datetime import timedelta
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
from urllib.error import HTTPError
from urllib.parse import urlencode

import pytest
import soundfile as sf
import numpy as np

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
spec = importlib.util.spec_from_file_location("ogg_archive_campaign", SCRIPTS / "ogg_archive_campaign.py")
campaign = importlib.util.module_from_spec(spec)
spec.loader.exec_module(campaign)


def row(obs=501, start="2026-09-06T12:00:00Z", **changes):
    value = {"id": obs, "start": start, "norad_cat_id": 68635,
             "transmitter_uuid": campaign.TRANSMITTER, "transmitter": campaign.TRANSMITTER,
             "transmitter_mode": "GMSK", "transmitter_baud": 9600.0,
             "status": "bad", "tle0": "CANVAS", "ground_station": 10,
             "payload": "https://network-satnogs.freetls.fastly.net/test.ogg", "demoddata": []}
    value.update(changes)
    return value


def api_url(cursor=None):
    query = {"transmitter_uuid": campaign.TRANSMITTER, "start": campaign.START,
             "start__lt": campaign.END, "format": "json"}
    if cursor:
        query["cursor"] = cursor
    return campaign.API + "?" + urlencode(query)


def pdu(info=b"test"):
    address = lambda value, final: bytes(ord(char) << 1 for char in value.ljust(6)) + bytes([0x60 | final])
    return address("CANVAS", 0) + address("LASP", 1) + b"\x03\xf0" + info


class FakeFetch:
    def __init__(self, data=None, failures=()):
        self.data = data or {}
        self.failures = failures
        self.calls = []

    def fetch(self, url, target, limit, api=False):
        self.calls.append(url)
        if url in self.failures:
            raise campaign.Unavailable("offline simulated missing object")
        payload, headers = self.data.get(url, (b"retained original OGG stand-in", {}))
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            target.write_bytes(payload)
        return {"url": url, "headers": headers, "identity": campaign.identity(target)}


@pytest.fixture
def no_disk_guard(monkeypatch):
    monkeypatch.setattr(campaign, "disk_guard", lambda *args, **kwargs: None)


def fake_execution(command, attempt, label, root, **kwargs):
    """Fabricated driver artifacts validate orchestration, not demodulator recall."""
    from telemetry_yield.crc import append_ax25_fcs
    wav = attempt / "audio.wav"
    if label == "probe":
        campaign.durable_json(attempt / "probe.stdout.log",
                              {"streams": [{"codec_type": "audio", "channels": 1, "sample_rate": "48000"}],
                               "format": {"duration": "1.0"}})
    elif label == "convert":
        sf.write(wav, np.zeros(48000, dtype=np.float32), 48000, subtype="FLOAT")
    elif label == "native":
        payload = pdu()
        native = {"decoder": "fast", "clock_bank_policy": "diverse", "failed_window_count": 0,
                  "controls": [{"kind": "silence", "frames": 0, "failures": []},
                               {"kind": "gaussian", "frames": 0, "failures": []}],
                  "window_count": 1, "unique_pdu_count": 1, "unique_pdu_bytes": len(payload), "elapsed_seconds": 0.1,
                  "input": campaign.identity(wav), "frames": [{"payload_hex": payload.hex(),
                  "payload_sha256": __import__("hashlib").sha256(payload).hexdigest(),
                  "frame_with_fcs_hex": append_ax25_fcs(payload).hex(),
                  "provenance": [{"window_start_seconds": 0, "timing_rank": 1}]}]}
        campaign.durable_json(attempt / "native.json", native)
        (attempt / "native.windows.jsonl").write_text(json.dumps({"offset_seconds": 0, "samples": 48000, "failures": [],
                                                                  "frame_instances": 1, "unique_total": 1}) + "\n")
    elif label == "baseline":
        (attempt / "baseline").mkdir()
        (attempt / "baseline/frames.kiss").write_bytes(b"")
        campaign.durable_json(attempt / "baseline/result.json",
                              {"completed": True, "returncode": 0, "timed_out": False,
                               "malformed_kiss_records": 0, "mode": "fsk-g3ruh", "baud": 9600,
                               "input_representation": "fm_demodulated", "telemetry_submission": False,
                               "input_sha256": campaign.identity(wav)["sha256"], "pdus": [],
                               "raw_kiss_data_records": 0, "strict_ax25_ui_unique_count": 0, "wall_seconds": 0.1})
    else:
        raise AssertionError(label)


def test_selection_exact_boundaries_sort_and_outcome_independence():
    values = [row(1, campaign.START), row(2, campaign.END), row(3, "2026-09-06T14:00:00+02:00"),
              row(4), row(5, "2026-08-30T23:59:59Z"), row(campaign.EXCLUDED[0])]
    assert [value["id"] for value in campaign.choose(values)] == [4, 3, 1]
    changed = copy.deepcopy(values)
    for value in changed:
        value.update(status="good", payload=None, demoddata=[{"payload_demod": "anything"}], waterfall=None)
    assert [value["id"] for value in campaign.choose(changed)] == [4, 3, 1]


def test_selection_global_limit_duplicate_and_conflict():
    values = [row(index) for index in range(1, 110)]
    assert [value["id"] for value in campaign.choose(values + values)] == list(range(109, 9, -1))
    with pytest.raises(ValueError, match="conflicting duplicate"):
        campaign.choose([row(), row(status="good")])
    with pytest.raises(ValueError, match="naive"):
        campaign.choose([row(start="2026-09-06T12:00:00")])


@pytest.mark.parametrize("changes", [{"norad_cat_id": 1}, {"transmitter_mode": "FSK"},
                                      {"transmitter_baud": 1200},
                                      {"transmitter": "other", "transmitter_uuid": "other"}])
def test_selection_rejects_wrong_mission_mode_baud(changes):
    assert campaign.choose([row(**changes)]) == []


@pytest.mark.parametrize("url", ["http://network.satnogs.org/x", "https://evil.example/x",
                                  "https://user@network.satnogs.org/x", "https://network.satnogs.org:444/x",
                                  "https://network.satnogs.org/x#fragment"])
def test_network_allowlist(url):
    with pytest.raises(ValueError):
        campaign.safe_url(url)


def test_pagination_requires_frozen_filters_and_has_no_page_or_status():
    assert campaign.next_page(f'<{api_url("abc")}>; rel="next", <{api_url()}>; rel="prev"') == api_url("abc")
    for url in (api_url() + "&status=good", api_url() + "&page=2", campaign.API + "?cursor=x"):
        with pytest.raises(ValueError):
            campaign.safe_url(url, api=True)


def test_prepare_metadata_only_preserves_missing_ogg_and_global_order(tmp_path, no_disk_guard):
    root = tmp_path / "campaign"
    fetch = FakeFetch({api_url(): (campaign.encoded([row(1, payload=None)]), {"Link": f'<{api_url("next")}>; rel="next"'}),
                       api_url("next"): (campaign.encoded([row(2)]), {})})
    result = campaign.prepare(root, fetcher=fetch, snapshotter=lambda: {"test_sources": True})
    assert result["ids"] == [2, 1]
    assert len(fetch.calls) == 2 and all("/api/observations/" in url for url in fetch.calls)
    assert campaign.read(root / "freeze.json")["outcomes_computed"] is False
    assert not (root / "observations").exists()


def test_prepare_cycle_never_freezes_partial_cohort(tmp_path, no_disk_guard):
    root = tmp_path / "campaign"
    fetch = FakeFetch({api_url(): (campaign.encoded([row()]), {"Link": f'<{api_url()}>; rel="next"'})})
    with pytest.raises(campaign.Pause, match="cycle"):
        campaign.prepare(root, fetcher=fetch, snapshotter=lambda: {})
    assert (root / "plan.json").exists() and not (root / "freeze.json").exists()


class Response(io.BytesIO):
    status = 200
    url = "https://network.satnogs.org/file"

    def __init__(self, data, headers=None):
        super().__init__(data)
        self.headers = headers or {}


class Opener:
    def __init__(self, data, headers=None):
        self.data, self.headers = data, headers
        self.calls = 0

    def open(self, *args, **kwargs):
        self.calls += 1
        return Response(self.data, self.headers)


def test_streaming_limit_content_length_and_interrupted_partial(tmp_path, no_disk_guard):
    opener = Opener(b"x" * 15)
    fetcher = campaign.Fetcher(tmp_path, opener=opener, sleeper=lambda _: None)
    with pytest.raises(campaign.Unavailable):
        fetcher.fetch(Response.url, tmp_path / "capture.ogg", 10)
    assert not (tmp_path / "capture.ogg").exists()
    assert len(list(tmp_path.glob("*.partial-*"))) == 3
    opener = Opener(b"tiny", {"Content-Length": "7"})
    with pytest.raises(campaign.Unavailable):
        campaign.Fetcher(tmp_path, opener=opener, sleeper=lambda _: None).fetch(Response.url, tmp_path / "second", 10)


def test_cache_is_identity_verified_not_merely_existing(tmp_path, no_disk_guard):
    opener = Opener(b"valid", {"Content-Length": "5"})
    fetcher = campaign.Fetcher(tmp_path, opener=opener, sleeper=lambda _: None)
    target = tmp_path / "capture.ogg"
    original = fetcher.fetch(Response.url, target, 10)
    assert fetcher.fetch(Response.url, target, 10) == original and opener.calls == 1
    target.write_bytes(b"other")
    with pytest.raises(campaign.Pause, match="drift"):
        fetcher.fetch(Response.url, target, 10)
    target2 = tmp_path / "uncommitted"
    target2.write_bytes(b"anything")
    with pytest.raises(campaign.Pause, match="uncommitted"):
        fetcher.fetch(Response.url, target2, 10)


@pytest.mark.parametrize("status,attempts", [(404, 1), (503, 3), (429, 3)])
def test_http_failures_are_bounded_and_never_zero(tmp_path, no_disk_guard, status, attempts):
    class FailedOpener:
        calls = 0
        def open(self, *args, **kwargs):
            self.calls += 1
            raise HTTPError(Response.url, status, "simulated", {"Retry-After": "0"}, None)
    opener = FailedOpener()
    with pytest.raises(campaign.Unavailable):
        campaign.Fetcher(tmp_path, opener=opener, sleeper=lambda _: None).fetch(Response.url, tmp_path / "target", 100)
    assert opener.calls == attempts


def test_references_all_required_missing_and_empty_are_different(tmp_path):
    known_url = "https://network.satnogs.org/known"
    missing_url = "https://network.satnogs.org/missing"
    fetch = FakeFetch({known_url: (pdu(), {})}, failures={missing_url})
    result = campaign.references(fetch, row(demoddata=[{"payload_demod": known_url}, {"payload_demod": missing_url}]), tmp_path)
    assert not result["complete"] and len(result["objects"]) == 1
    assert campaign.references(fetch, row(demoddata=[]), tmp_path)["complete"]
    assert not campaign.references(fetch, row(demoddata=None), tmp_path)["complete"]
    assert not campaign.references(fetch, row(demoddata=[{}]), tmp_path)["complete"]


def test_no_ogg_stays_in_denominator_and_still_acquires_references(tmp_path, no_disk_guard):
    calls = []
    result = campaign.one_observation(tmp_path, row(payload=None), {"sources": {}}, "plan", fetcher=FakeFetch(),
                                      executor=lambda *args, **kwargs: calls.append(args), source_checker=lambda _: None)
    assert result["status"] == "ogg_absent_in_snapshot" and result["references"]["complete"]
    assert not calls and result["comparison"] is None


def test_reference_failure_blocks_absence_claims_and_all_decoders(tmp_path, no_disk_guard):
    result = campaign.one_observation(tmp_path, row(demoddata=[{}]), {"sources": {}}, "plan", fetcher=FakeFetch(),
                                      executor=lambda *args, **kwargs: pytest.fail("must not decode"), source_checker=lambda _: None)
    assert result["status"] == "reference_incomplete" and result["comparison"] is None


def test_end_to_end_fake_artifacts_use_exact_scorer_before_own_wav_delete(tmp_path, no_disk_guard):
    result = campaign.one_observation(tmp_path, row(), {"sources": {}}, "plan", fetcher=FakeFetch(),
                                      executor=fake_execution, source_checker=lambda _: None)
    assert result["status"] == "complete"
    attempt = Path(result["attempt"])
    assert result["comparison"]["counts"]["native_new_vs_archive"]["count"] == 1
    assert result["comparison"]["counts"]["native_new_vs_archive_and_baseline"]["count"] == 1
    assert result["extra_candidates"][0]["origin_audit"] == "pending"
    assert not (attempt / "audio.wav").exists()
    assert (attempt.parent / "capture.ogg").exists()
    assert (attempt / "wav-delete-intent.json").exists() and (attempt / "wav-deleted.json").exists()
    assert campaign.committed_state(attempt, "plan") == result


@pytest.mark.parametrize("mutation", ["missing_kiss", "failed_window", "bad_crc", "incomplete_journal", "smoke_frame"])
def test_invalid_driver_result_is_failure_not_zero_and_keeps_wav(tmp_path, no_disk_guard, mutation):
    def broken(command, attempt, label, root, **kwargs):
        fake_execution(command, attempt, label, root, **kwargs)
        if label != "baseline":
            return
        native_path = attempt / "native.json"
        native = campaign.read(native_path)
        if mutation == "missing_kiss":
            (attempt / "baseline/frames.kiss").unlink()
        elif mutation == "failed_window":
            native["failed_window_count"] = 1
        elif mutation == "bad_crc":
            native["frames"][0]["frame_with_fcs_hex"] = native["frames"][0]["frame_with_fcs_hex"][:-4] + "0000"
        elif mutation == "incomplete_journal":
            (attempt / "native.windows.jsonl").write_text("")
        elif mutation == "smoke_frame":
            native["controls"][0]["frames"] = 1
        native_path.write_text(json.dumps(native))
    result = campaign.one_observation(tmp_path, row(), {"sources": {}}, "plan", fetcher=FakeFetch(),
                                      executor=broken, source_checker=lambda _: None)
    assert result["status"] == "score_failed" and result["comparison"] is None
    assert (Path(result["attempt"]) / "audio.wav").exists()


def cleanup_fixture(folder):
    folder.mkdir()
    wav = folder / "audio.wav"
    wav.write_bytes(b"own reproducible WAV fixture")
    campaign.durable_json(folder / "wav-ownership.json", campaign.ownership(wav))
    final = {"status": "complete", "comparison": {"checked": True}, "artifacts": []}
    campaign.durable_json(folder / "final.json", final)
    campaign.durable_json(folder / "commit.json", {"final": campaign.identity(folder / "final.json")})
    return wav


@pytest.mark.parametrize("mutation", ["symlink", "changed_content", "different_inode", "foreign_path", "not_committed"])
def test_cleanup_refuses_foreign_or_modified_wav(tmp_path, mutation):
    attempt = tmp_path / "attempt-001"
    wav = cleanup_fixture(attempt)
    if mutation == "symlink":
        original = tmp_path / "must-remain"
        wav.rename(original)
        wav.symlink_to(original)
    elif mutation == "changed_content":
        wav.write_bytes(b"changed bytes")
    elif mutation == "different_inode":
        wav.rename(tmp_path / "old")
        wav.write_bytes(b"own reproducible WAV fixture")
    elif mutation == "foreign_path":
        value = campaign.read(attempt / "wav-ownership.json")
        value["path"] = str(tmp_path / "outside.wav")
        (attempt / "wav-ownership.json").write_text(json.dumps(value))
    else:
        (attempt / "commit.json").unlink()
    with pytest.raises((campaign.Pause, FileNotFoundError)):
        campaign.cleanup_wav(attempt)
    assert wav.exists()


def test_cleanup_recovers_crash_after_intent_and_unlink(tmp_path):
    attempt = tmp_path / "attempt-001"
    wav = cleanup_fixture(attempt)
    owner = campaign.read(attempt / "wav-ownership.json")
    campaign.durable_json(attempt / "wav-delete-intent.json", {"original_identity": owner})
    wav.unlink()
    campaign.cleanup_wav(attempt)
    assert campaign.read(attempt / "wav-deleted.json")["crash_recovered_after_intent"]
    campaign.cleanup_wav(attempt)


def test_disk_guard_reserve_and_campaign_cap(tmp_path, monkeypatch):
    from collections import namedtuple
    Disk = namedtuple("Disk", "total used free")
    monkeypatch.setattr(campaign.shutil, "disk_usage", lambda _: Disk(10000, 9950, 50))
    with pytest.raises(campaign.Pause, match="reserve"):
        campaign.disk_guard(tmp_path, 1, {"reserve_bytes": 50, "campaign_bytes": 10000})
    (tmp_path / "retained-original.ogg").write_bytes(b"x" * 20)
    with pytest.raises(campaign.Pause, match="budget"):
        campaign.disk_guard(tmp_path, 0, {"reserve_bytes": 0, "campaign_bytes": 10})
    assert (tmp_path / "retained-original.ogg").exists()


def test_second_coordinator_and_source_drift_fail_closed(tmp_path):
    with campaign.locked(tmp_path):
        with pytest.raises(campaign.Pause, match="coordinator"):
            with campaign.locked(tmp_path):
                pass
    source = tmp_path / "source.py"
    source.write_text("original")
    original = campaign.identity(source)
    source.write_text("modified")
    with pytest.raises(campaign.Pause, match="drift"):
        campaign.verify(original)


def test_live_old_process_token_blocks_launch(tmp_path, monkeypatch):
    receipt = tmp_path / "observations/501/attempt-001/process-native.json"
    campaign.durable_json(receipt, {"token": "test-token", "process_boundary": {}})
    monkeypatch.setattr(campaign, "active_token_pids", lambda token, boundary: [123] if token == "test-token" else [])
    with pytest.raises(campaign.Pause, match="old owned"):
        campaign.ensure_no_old_processes(tmp_path)


def test_bounded_real_child_timeout_does_not_become_success(tmp_path, no_disk_guard):
    attempt = tmp_path / "attempt-001"
    attempt.mkdir()
    with pytest.raises(TimeoutError):
        campaign.execute([sys.executable, "-c", "import time; time.sleep(3)"], attempt, "test", tmp_path, timeout=0.15)
    token = campaign.read(attempt / "process-test.json")["token"]
    boundary = campaign.read(attempt / "process-test.json")["process_boundary"]
    assert campaign.active_token_pids(token, boundary) == []


@pytest.mark.parametrize("ticks,raises", [(99, False), (101, True)])
def test_only_potentially_new_unreadable_process_blocks(tmp_path, monkeypatch, ticks, raises):
    boot = tmp_path / "sys/kernel/random/boot_id"
    boot.parent.mkdir(parents=True)
    boot.write_text("fixture-boot")
    process = tmp_path / "123456789"
    process.mkdir()
    (process / "cgroup").write_text("0::/fixture")
    fields = ["S"] + ["0"] * 18 + [str(ticks)]
    (process / "stat").write_text("123456789 (a process (name)) " + " ".join(fields))
    original_read = Path.read_bytes
    def restricted(path):
        if path == process / "environ":
            raise PermissionError("simulated same-user inaccessible environment")
        return original_read(path)
    monkeypatch.setattr(Path, "read_bytes", restricted)
    boundary = {"boot_id": "fixture-boot", "minimum_start_ticks": 100, "cgroup": "0::/fixture"}
    if raises:
        with pytest.raises(campaign.Pause, match="potentially new"):
            campaign.active_token_pids("token", boundary, proc_root=tmp_path)
    else:
        assert campaign.active_token_pids("token", boundary, proc_root=tmp_path) == []
    assert campaign.active_token_pids("token", {**boundary, "boot_id": "old-boot"}, proc_root=tmp_path) == []


@pytest.mark.parametrize("transition", ["zombie", "disappeared"])
def test_environ_permission_race_rechecks_process_liveness(tmp_path, monkeypatch, transition):
    boot = tmp_path / "sys/kernel/random/boot_id"
    boot.parent.mkdir(parents=True)
    boot.write_text("fixture-boot")
    process = tmp_path / "123456789"
    process.mkdir()
    (process / "cgroup").write_text("0::/fixture")
    stat_path = process / "stat"
    fields = ["S"] + ["0"] * 18 + ["101"]
    stat_path.write_text("123456789 (name) " + " ".join(fields))
    original_read = Path.read_bytes
    def raced(path):
        if path == process / "environ":
            if transition == "zombie":
                fields[0] = "Z"
                stat_path.write_text("123456789 (name) " + " ".join(fields))
            else:
                stat_path.unlink()
            raise PermissionError("exited between reads")
        return original_read(path)
    monkeypatch.setattr(Path, "read_bytes", raced)
    assert campaign.active_token_pids("token", {"boot_id": "fixture-boot", "minimum_start_ticks": 100,
                                              "cgroup": "0::/fixture"}, proc_root=tmp_path) == []


def test_foreign_cgroup_is_rejected_before_environment_read(tmp_path, monkeypatch):
    boot = tmp_path / "sys/kernel/random/boot_id"
    boot.parent.mkdir(parents=True)
    boot.write_text("fixture-boot")
    process = tmp_path / "123456789"
    process.mkdir()
    (process / "stat").write_text("123456789 (foreign) " + " ".join(["S"] + ["0"] * 18 + ["101"]))
    (process / "cgroup").write_text("0::/other-systemd-unit")
    monkeypatch.setattr(Path, "read_bytes", lambda _: pytest.fail("foreign environ must not be accessed"))
    assert campaign.active_token_pids("token", {"boot_id": "fixture-boot", "minimum_start_ticks": 100,
                                              "cgroup": "0::/own-systemd-unit"}, proc_root=tmp_path) == []


def test_permission_linger_retries_boundedly_until_zombie(tmp_path, monkeypatch):
    boot = tmp_path / "sys/kernel/random/boot_id"
    boot.parent.mkdir(parents=True)
    boot.write_text("fixture-boot")
    process = tmp_path / "123456789"
    process.mkdir()
    (process / "cgroup").write_text("0::/fixture")
    stat_path = process / "stat"
    stat_path.write_text("123456789 (exiting) " + " ".join(["S"] + ["0"] * 18 + ["101"]))
    calls, sleeps = [], []
    def denied(path):
        calls.append(path)
        raise PermissionError("temporarily exiting, stat is still S")
    def tick(delay):
        sleeps.append(delay)
        if len(sleeps) == 3:
            stat_path.write_text("123456789 (exiting) " + " ".join(["Z"] + ["0"] * 18 + ["101"]))
    monkeypatch.setattr(Path, "read_bytes", denied)
    monkeypatch.setattr(campaign.time, "sleep", tick)
    assert campaign.active_token_pids("token", {"boot_id": "fixture-boot", "minimum_start_ticks": 100,
                                              "cgroup": "0::/fixture"}, proc_root=tmp_path) == []
    assert len(calls) == 3 and sleeps == [0.03, 0.03, 0.03]


def counted_state(obs, archive, native, baseline=()):
    from ogg_pilot_report import compare_sets
    return {"observation_id": obs, "status": "complete", "ogg_status": "available",
            "comparison": {"counts": compare_sets(set(archive), set(baseline), set(native))},
            "references": {"complete": True, "objects": [{"payload_hex": value.hex(),
                            "strict_ax25_ui_structure": True} for value in archive]}}


def test_global_local_dedup_distinction_and_incomplete_reference_gate():
    a, b = pdu(b"a"), pdu(b"b")
    first, second = counted_state(1, [a], [a, b]), counted_state(2, [b], [a, b])
    total = campaign.aggregate([1, 2], [first, second])
    assert total["per_observation_deduplicated_totals"]["native_same_audio"]["count"] == 4
    assert total["global_union_of_observation_local_sets"]["native_same_audio"]["count"] == 2
    assert total["global_union_of_observation_local_sets"]["native_new_vs_archive"]["count"] == 2
    assert total["globally_absent_from_entire_cohort_archive"]["count"] == 0
    incomplete = {"observation_id": 3, "status": "reference_incomplete", "references": {"complete": False, "objects": []}}
    total = campaign.aggregate([1, 2, 3], [first, second, incomplete])
    assert total["globally_absent_from_entire_cohort_archive"] is None
    assert total["frozen_count"] == 3 and total["complete_comparisons"] == 2


def test_empty_cohort_and_duplicate_aggregate_are_explicit():
    total = campaign.aggregate([], [])
    assert total["frozen_count"] == total["complete_comparisons"] == 0
    assert total["no_false_accept_rate_confidence_claim"]
    state = counted_state(1, [], [])
    with pytest.raises(ValueError, match="duplicate"):
        campaign.aggregate([1], [state, state])


def test_known_independent_crc_and_corruption():
    from telemetry_yield.crc import append_ax25_fcs
    raw = append_ax25_fcs(bytes(range(32)))
    assert raw[-2:] == b"\xe4\x53" and campaign.independent_crc(raw)
    assert not campaign.independent_crc(raw[:-1] + bytes([raw[-1] ^ 1]))
    assert not campaign.independent_crc(b"\xff\xff")


def test_resume_completed_observation_runs_no_decoder_or_download(tmp_path, monkeypatch, no_disk_guard):
    root = tmp_path / "campaign"
    fetch = FakeFetch({api_url(): (campaign.encoded([row()]), {})})
    campaign.prepare(root, fetcher=fetch, snapshotter=lambda: {})
    plan_hash = campaign.read(root / "freeze.json")["plan"]["sha256"]
    campaign.one_observation(root, row(), {"sources": {}}, plan_hash, fetcher=FakeFetch(),
                             executor=fake_execution, source_checker=lambda _: None)
    monkeypatch.setattr(campaign, "verify_sources", lambda _: None)
    monkeypatch.setattr(campaign, "one_observation", lambda *args, **kwargs: pytest.fail("completed must not rerun"))
    result = campaign.run(root)
    assert result["complete_campaign"] and result["complete_comparisons"] == 1
    summaries = [campaign.read(path) for path in (root / "summaries").glob("*.json")]
    compact = next(value for value in summaries if value["schema"] == "ogg-archive-compact-progress-v1")
    assert "observations" not in compact and pdu().hex() not in json.dumps(compact)
    assert len(compact["observation_commits"]) == 1
    assert compact["per_observation_deduplicated_totals"] == result["per_observation_deduplicated_totals"]
