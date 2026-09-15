"""No test signals a live process or accesses a campaign IQ/outcome artifact."""
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import types

import pytest

PATH = Path(__file__).parents[1] / "work/blind-phase-confirmatory-v2/abort_retry4_preactivation_v1.py"
spec = importlib.util.spec_from_file_location("abort_retry4", PATH)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def fixture_mounts(monkeypatch):
    original = b"114 93 8:1 / / rw,relatime - ext4 /dev/vda1 rw\n"
    added = (f"165 114 0:48 / {m.RESULTS} rw,nosuid,nodev,noexec,relatime - tmpfs tmpfs "
             "rw,size=2097152k,mode=700,uid=1000,gid=1000,inode64\n").encode()
    rows = [{"mount_id": 114, "mount_point": "/"}, {
        "mount_id": 165, "parent_id": 114, "major_minor": "0:48", "root": "/",
        "mount_point": m.RESULTS, "fs_type": "tmpfs", "mount_source": "tmpfs",
        "mount_options": ["nodev", "noexec", "nosuid", "relatime", "rw"],
        "super_options": ["gid=1000", "inode64", "mode=700", "rw", "size=2097152k", "uid=1000"]}]
    b = types.SimpleNamespace(_mountinfo_bytes=lambda p: original + added,
                              _parse_mountinfo=lambda p: rows,
                              _root_private_attestation=lambda p: None)
    monkeypatch.setattr(m, "check_empty_results", lambda: None)
    a = {"root_private_attestation": {"mountinfo_sha256": hashlib.sha256(original).hexdigest()}}
    return b, a, rows


def test_exact_one_results_mount_allowed(monkeypatch):
    b, a, _ = fixture_mounts(monkeypatch)
    assert len(m.check_mounts(b, a)) == 64


@pytest.mark.parametrize("change", ["project", "nested_project", "stacked", "source", "size", "parent", "old_hash"])
def test_mount_drift_rejected(monkeypatch, change):
    b, a, rows = fixture_mounts(monkeypatch)
    if change == "project":
        rows.append({"mount_point": m.PROJECT})
    elif change == "nested_project":
        rows.append({"mount_point": m.PROJECT + "/runtime"})
    elif change == "stacked":
        rows.append(copy.deepcopy(rows[1]))
    elif change == "source":
        rows[1]["mount_source"] = "foreign"
    elif change == "size":
        rows[1]["super_options"].remove("size=2097152k")
    elif change == "parent":
        rows[1]["parent_id"] = 1
    else:
        a["root_private_attestation"]["mountinfo_sha256"] = "0" * 64
    with pytest.raises(ValueError):
        m.check_mounts(b, a)


def test_nonempty_results_blocks_abort(monkeypatch):
    b, a, _ = fixture_mounts(monkeypatch)
    def nonempty():
        raise ValueError("results tmpfs contains data")
    monkeypatch.setattr(m, "check_empty_results", nonempty)
    with pytest.raises(ValueError, match="contains data"):
        m.check_mounts(b, a)


def operation(monkeypatch, *, holders=None, external=None, poll_flags=None):
    op = m.Abort.__new__(m.Abort)
    op.root, op.pidfd = 98, 99
    events = []
    baseline = {"inventory_sha256": "baseline"}
    op.active = {"external_inventory_after": baseline}
    op.proof = lambda intent_exists=False: events.append(("proof", intent_exists)) or {"proof": 1}
    op.authorities = lambda intent_exists=False: events.append(("authorities", intent_exists))
    empty = {"processes": [], "namespace_fd_handles": []}
    op.builder = types.SimpleNamespace(
        _namespace_holders=lambda ns: empty if holders is None else holders,
        _capture_external_fixed_point=lambda: baseline if external is None else external)
    def publish(root, name, document):
        events.append(("publish", name, document))
        return "intentdigest"
    monkeypatch.setattr(m, "publish", publish)
    monkeypatch.setattr(m.signal, "pidfd_send_signal", lambda *args: events.append(("signal", args)))
    class Poll:
        def register(self, *args):
            events.append(("poll_register", args))
        def poll(self, timeout):
            events.append(("poll", timeout))
            return [(99, m.select.POLLIN)] if poll_flags is None else poll_flags
    monkeypatch.setattr(m.select, "poll", Poll)
    return op, events


def test_default_is_read_only(monkeypatch):
    op, events = operation(monkeypatch)
    assert op.run()["mutation"] is False
    assert events == [("proof", False)]


def test_durable_intent_then_reproof_then_pidfd_then_receipt(monkeypatch):
    op, events = operation(monkeypatch)
    receipt = op.run(True)
    assert [e[0] for e in events] == ["proof", "publish", "proof", "signal", "poll_register", "poll", "authorities", "publish"]
    assert events[1][1] == m.INTENT and events[-1][1] == m.RECEIPT
    assert events[3][1] == (99, m.signal.SIGTERM)
    assert receipt["results_export_completed"] is False
    assert receipt["campaign_completed"] is False


def test_reproof_drift_never_signals(monkeypatch):
    op, events = operation(monkeypatch)
    op.proof = lambda intent_exists=False: {"changed": intent_exists}
    with pytest.raises(ValueError, match="proof changed"):
        op.run(True)
    assert not any(e[0] == "signal" for e in events)
    assert [e[1] for e in events if e[0] == "publish"] == [m.INTENT]


@pytest.mark.parametrize("failure", ["holders", "external", "timeout"])
def test_no_false_success_receipt(monkeypatch, failure):
    options = {"holders": {"processes": [{"pid": 42}], "namespace_fd_handles": []}} if failure == "holders" else (
        {"external": {"drift": True}} if failure == "external" else {"poll_flags": []})
    op, events = operation(monkeypatch, **options)
    with pytest.raises(ValueError):
        op.run(True)
    assert [e[1] for e in events if e[0] == "publish"] == [m.INTENT]


def test_intent_publication_failure_never_signals(monkeypatch):
    op, events = operation(monkeypatch)
    def failure(*args):
        raise OSError("fsync failure")
    monkeypatch.setattr(m, "publish", failure)
    with pytest.raises(OSError):
        op.run(True)
    assert not any(e[0] == "signal" for e in events)


def test_publish_no_clobber_and_root_readonly(tmp_path, monkeypatch):
    if os.geteuid() != 0:
        monkeypatch.setattr(m.os, "fchown", lambda *args: None)
    fd = os.open(tmp_path, m.DIRECTORY_FLAGS)
    try:
        digest = m.publish(fd, "intent.json", {"a": 1})
        data = (tmp_path / "intent.json").read_bytes()
        assert digest == hashlib.sha256(data).hexdigest()
        assert json.loads(data) == {"a": 1}
        assert (tmp_path / "intent.json").stat().st_mode & 0o777 == 0o444
        with pytest.raises(FileExistsError):
            m.publish(fd, "intent.json", {"a": 2})
        assert (tmp_path / "intent.json").read_bytes() == data
        (tmp_path / "link.json").symlink_to("intent.json")
        with pytest.raises(FileExistsError):
            m.publish(fd, "link.json", {"a": 2})
    finally:
        os.close(fd)


def test_close_releases_all_held_descriptors(monkeypatch):
    op = m.Abort.__new__(m.Abort)
    op.fds = [10, 11, 12]
    closed = []
    monkeypatch.setattr(m.os, "close", closed.append)
    op.close()
    assert closed == [12, 11, 10] and op.fds == []


@pytest.mark.parametrize("failure", ["keeper_pid", "keeper_start", "keeper_namespace", "live_identity", "foreign_holder", "external_drift", "pidfd"])
def test_real_proof_rejects_identity_and_namespace_failures(monkeypatch, failure):
    op = m.Abort.__new__(m.Abort)
    op.pidfd = 99
    op.authorities = lambda intent_exists=False: None
    keeper = {"pid": m.PID, "starttime_ticks": m.START, "mount_namespace_inode": m.NS}
    op.active = {"attempt_id": m.ATTEMPT, "status": "active_root_recursively_private",
                 "keeper": keeper, "external_inventory_after": {"inventory_sha256": "before"}}
    live = dict(keeper)
    def holder_check(value):
        if failure == "foreign_holder":
            raise ValueError("foreign holder or namespace FD")
    def pidfd_check(fd, pid):
        if failure == "pidfd":
            raise ValueError("stale pidfd")
    op.builder = types.SimpleNamespace(
        _validate_pidfd=pidfd_check, _keeper_live_identity=lambda pid: live,
        _assert_keeper_is_sole_namespace_holder=holder_check,
        _capture_external_fixed_point=lambda **kwargs: {"inventory_sha256": "after" if failure == "external_drift" else "before"})
    if failure == "keeper_pid":
        keeper["pid"] += 1
    elif failure == "keeper_start":
        keeper["starttime_ticks"] += 1
    elif failure == "keeper_namespace":
        keeper["mount_namespace_inode"] += 1
    elif failure == "live_identity":
        live["executable"] = "wrong"
    monkeypatch.setattr(m, "check_mounts", lambda *args: "mountsha")
    with pytest.raises(ValueError):
        op.proof()
