from __future__ import annotations

import importlib.util
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import signal
import sys
import time

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "work/blind-phase-confirmatory-v2/bounded_process_revised_v2.py"


def _module():
    name = "blind_phase_bounded_process_revised_v2_test_module"
    spec = importlib.util.spec_from_file_location(name, SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


SUPERVISOR = _module()


def _limits(**overrides):
    values = {
        "wall_timeout_seconds": 2.0,
        "rlimit_cpu_seconds": 2,
        "maximum_process_tree_rss_bytes": 256 * 1024 * 1024,
        "rlimit_address_space_bytes_per_process": 512 * 1024 * 1024,
        "rlimit_file_size_bytes": 8 * 1024 * 1024,
        "maximum_stdout_bytes": 8 * 1024,
        "maximum_stderr_bytes": 8 * 1024,
        "rss_sample_interval_seconds": 0.02,
        "termination_grace_seconds": 0.15,
        "natural_exit_grace_seconds": 0.25,
    }
    values.update(overrides)
    return SUPERVISOR.ProcessLimits(**values)


def _run(tmp_path: Path, program: str, *, limits=None):
    result = tmp_path / "result.json"
    receipt = tmp_path / "receipt.json"
    document = SUPERVISOR.run_bounded_subprocess(
        [sys.executable, "-c", program, str(result)],
        unit={"observation_id": 123, "arm": "candidate", "baudrate": 1200},
        result_path=result,
        receipt_path=receipt,
        limits=limits or _limits(),
    )
    return result, receipt, document


def test_success_is_content_bound_bounded_and_idempotent(tmp_path: Path) -> None:
    program = (
        "import pathlib,sys; "
        "pathlib.Path(sys.argv[1]).write_text('{\"status\":\"ok\"}'); "
        "print('bounded output')"
    )
    result, receipt, document = _run(tmp_path, program)
    assert document["status"] == "completed"
    assert document["process_tree_cleanup_passed"] is True
    assert document["stdout"]["size_bytes"] == len(b"bounded output\n")
    assert document["result"]["sha256"] == SUPERVISOR._sha256_file(result)
    assert receipt.stat().st_mode & 0o777 == 0o444

    repeated = SUPERVISOR.run_bounded_subprocess(
        [sys.executable, "-c", program, str(result)],
        unit={"observation_id": 123, "arm": "candidate", "baudrate": 1200},
        result_path=result,
        receipt_path=receipt,
        limits=_limits(),
    )
    assert repeated == document

    corrupted = json.loads(receipt.read_text(encoding="utf-8"))
    corrupted["unit"]["observation_id"] = 999
    receipt.chmod(0o600)
    receipt.write_text(json.dumps(corrupted), encoding="utf-8")
    with pytest.raises(ValueError, match="self-hash"):
        SUPERVISOR.run_bounded_subprocess(
            [sys.executable, "-c", program, str(result)],
            unit={"observation_id": 123, "arm": "candidate", "baudrate": 1200},
            result_path=result,
            receipt_path=receipt,
            limits=_limits(),
        )


def test_timeout_kills_process_group_and_records_terminal_failure(tmp_path: Path) -> None:
    program = (
        "import pathlib,subprocess,sys,time; "
        "child=subprocess.Popen(['/bin/sleep','30']); "
        "print(child.pid, flush=True); time.sleep(30)"
    )
    _, _, document = _run(
        tmp_path,
        program,
        limits=_limits(wall_timeout_seconds=0.20),
    )
    assert document["status"] == "timeout"
    assert document["process_tree_cleanup_passed"] is True
    assert document["elapsed_seconds"] < 2.0
    child_pid = int(document["stdout"]["tail_utf8"].strip())
    deadline = time.monotonic() + 1.0
    while Path(f"/proc/{child_pid}").exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert not Path(f"/proc/{child_pid}").exists()


def test_root_exit_with_descendant_holding_pipe_is_bounded_and_cleaned(
    tmp_path: Path,
) -> None:
    program = (
        "import pathlib,subprocess,sys; "
        "child=subprocess.Popen(['/bin/sleep','30']); "
        "print(child.pid, flush=True); "
        "pathlib.Path(sys.argv[1]).write_text('{}')"
    )
    _, _, document = _run(tmp_path, program, limits=_limits())
    assert document["status"] == "descendant_cgroup_survivor"
    assert document["elapsed_seconds"] < 2.0
    child_pid = int(document["stdout"]["tail_utf8"].strip())
    deadline = time.monotonic() + 1.0
    while Path(f"/proc/{child_pid}").exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert not Path(f"/proc/{child_pid}").exists()


def test_cgroup_catches_setsid_double_fork_after_pipes_are_closed(
    tmp_path: Path,
) -> None:
    result = tmp_path / "result.json"
    pid_file = tmp_path / "escaped.pid"
    receipt = tmp_path / "receipt.json"
    grandchild = (
        "import os,pathlib,sys,time; pid=os.fork(); "
        "os._exit(0) if pid else None; "
        "pathlib.Path(sys.argv[1]).write_text(str(os.getpid())); time.sleep(30)"
    )
    root = (
        "import pathlib,subprocess,sys,time; "
        "subprocess.Popen([sys.executable,'-c',sys.argv[3],sys.argv[2]],"
        "stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,"
        "start_new_session=True,close_fds=True); "
        "pathlib.Path(sys.argv[1]).write_text('{}'); time.sleep(.1)"
    )
    document = SUPERVISOR.run_bounded_subprocess(
        [sys.executable, "-c", root, str(result), str(pid_file), grandchild],
        unit={"observation_id": 123, "arm": "candidate", "baudrate": 1200},
        result_path=result,
        receipt_path=receipt,
        limits=_limits(),
    )
    assert document["status"] == "descendant_cgroup_survivor"
    assert document["process_tree_cleanup_passed"] is True
    escaped_pid = int(pid_file.read_text())
    deadline = time.monotonic() + 1.0
    while Path(f"/proc/{escaped_pid}").exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert not Path(f"/proc/{escaped_pid}").exists()


def test_output_limit_does_not_retain_unbounded_stream(tmp_path: Path) -> None:
    _, _, document = _run(
        tmp_path,
        "import sys,time; sys.stdout.write('x'*1000000); sys.stdout.flush(); time.sleep(1)",
        limits=_limits(maximum_stdout_bytes=1024),
    )
    assert document["status"] == "stdout_limit"
    assert document["stdout"]["size_bytes"] > 1024
    assert len(document["stdout"]["tail_utf8"].encode()) <= SUPERVISOR.TAIL_BYTES
    assert document["stdout"]["truncated_for_receipt"] is True


def test_cgroup_memory_max_is_a_hard_aggregate_gate(tmp_path: Path) -> None:
    _, _, document = _run(
        tmp_path,
        "import time; value=bytearray(128*1024*1024); time.sleep(2)",
        limits=_limits(
            maximum_process_tree_rss_bytes=32 * 1024 * 1024,
            wall_timeout_seconds=3.0,
        ),
    )
    assert document["status"] == "memory_limit"
    assert document["cgroup"]["memory_max_bytes"] == 32 * 1024 * 1024
    memory_events = document["cgroup"]["memory_events_delta"]
    direct_kernel_event = (
        memory_events.get("max", 0) > 0 or memory_events.get("oom_kill", 0) > 0
    )
    # Under load, systemd can collect the empty transient scope between the
    # kernel SIGKILL and the supervisor's final counter read. The receipt has a
    # deliberately narrow fallback for exactly that observable teardown race.
    collected_scope_fallback = (
        document["cgroup"]["scope_removed_before_receipt"] is True
        and document["return_code"] == -signal.SIGKILL
        and document["elapsed_seconds"] < document["limits"]["rlimit_cpu_seconds"]
    )
    assert direct_kernel_event or collected_scope_fallback
    assert document["peak_cgroup_memory_bytes"] <= 32 * 1024 * 1024


def test_cgroup_pids_max_is_a_hard_aggregate_gate(tmp_path: Path) -> None:
    program = (
        "import subprocess,time; children=[]; "
        "exec(\"for _ in range(100):\\n"
        " try: children.append(subprocess.Popen(['/bin/sleep','30']))\\n"
        " except OSError: break\"); time.sleep(.5)"
    )
    _, _, document = _run(
        tmp_path,
        program,
        limits=_limits(maximum_process_count=8, wall_timeout_seconds=3.0),
    )
    assert document["status"] == "pids_limit"
    assert document["cgroup"]["pids_max"] == 8
    assert document["cgroup"]["pids_events_delta"]["max"] > 0


def test_cgroup_aggregate_cpu_budget_is_enforced(tmp_path: Path) -> None:
    program = (
        "import subprocess,sys,time; "
        "children=[subprocess.Popen([sys.executable,'-c','while True: pass']) for _ in range(4)]; "
        "time.sleep(10)"
    )
    _, _, document = _run(
        tmp_path,
        program,
        limits=_limits(rlimit_cpu_seconds=2, wall_timeout_seconds=5.0),
    )
    assert document["status"] == "cpu_limit"
    assert document["cgroup"]["cpu_usage_usec"] >= 2_000_000


def test_receipt_publication_race_never_clobbers_winner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "receipt.json"
    winner = b"concurrent winner\n"
    original_link = os.link

    def lose_race(source, destination):
        Path(destination).write_bytes(winner)
        raise FileExistsError(destination)

    monkeypatch.setattr(SUPERVISOR.os, "link", lose_race)
    with pytest.raises(ValueError, match="created concurrently"):
        SUPERVISOR._publish_receipt(output, {"status": "completed"})
    monkeypatch.setattr(SUPERVISOR.os, "link", original_link)
    assert output.read_bytes() == winner
    assert not list(tmp_path.glob(".receipt.json.*.tmp"))


def test_resume_rejects_semantically_incomplete_completed_receipt(tmp_path: Path) -> None:
    receipt = tmp_path / "receipt.json"
    document = {
        "schema_version": SUPERVISOR.RECEIPT_SCHEMA_VERSION,
        "status": "completed",
        "attempt_fingerprint": "a" * 64,
        "process_tree_cleanup_passed": True,
        "result": None,
    }
    document["receipt_payload_sha256"] = SUPERVISOR._sha256_document(document)
    receipt.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(ValueError, match="verified result"):
        SUPERVISOR._load_existing_receipt(
            receipt,
            attempt_fingerprint="a" * 64,
            expected_result_path=tmp_path / "result.json",
        )


def _closed_pipe_descendant_program(child: str) -> str:
    # Hold root long enough for the real systemd scope's hard limits to be
    # observed, then let a child outlive root without retaining output FDs.
    return (
        "import pathlib,subprocess,sys,time; "
        f"child=subprocess.Popen([sys.executable,'-c',{child!r}],"
        "stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL); "
        "print(child.pid,flush=True); "
        "pathlib.Path(sys.argv[1]).write_text('{}'); time.sleep(.05)"
    )


def test_natural_exit_with_closed_pipes_waits_for_actual_cgroup_empty(tmp_path: Path) -> None:
    _, _, document = _run(
        tmp_path, _closed_pipe_descendant_program("import time; time.sleep(.20)"),
        limits=_limits(natural_exit_grace_seconds=.75),
    )
    assert document["status"] == "completed"
    assert document["return_code"] == 0
    assert document["process_tree_cleanup_passed"] is True
    assert document["natural_exit"]["cgroup_populated_at_root_exit"] is True
    assert document["natural_exit"]["cgroup_populated_after_grace"] is False
    assert document["natural_exit"]["wait_seconds"] >= .05
    child_pid = int(document["stdout"]["tail_utf8"].strip())
    assert not Path(f"/proc/{child_pid}").exists()


def test_zero_natural_grace_reproduces_closed_pipe_survivor(tmp_path: Path) -> None:
    _, _, document = _run(
        tmp_path, _closed_pipe_descendant_program("import time; time.sleep(.20)"),
        limits=_limits(natural_exit_grace_seconds=0),
    )
    assert document["status"] == "descendant_cgroup_survivor"
    assert document["return_code"] == 0
    assert document["process_tree_cleanup_passed"] is True
    assert document["inherited_pipe_failure_detected"] is False


def test_persistent_closed_pipe_descendant_is_never_success(tmp_path: Path) -> None:
    _, _, document = _run(
        tmp_path, _closed_pipe_descendant_program("import time; time.sleep(30)"),
    )
    assert document["status"] == "descendant_cgroup_survivor"
    assert document["natural_exit"]["cgroup_populated_after_grace"] is True
    assert document["process_tree_cleanup_passed"] is True
    child_pid = int(document["stdout"]["tail_utf8"].strip())
    assert not Path(f"/proc/{child_pid}").exists()


def test_natural_exit_does_not_extend_wall_budget(tmp_path: Path) -> None:
    _, _, document = _run(
        tmp_path, _closed_pipe_descendant_program("import time; time.sleep(30)"),
        limits=_limits(wall_timeout_seconds=.20, natural_exit_grace_seconds=1.0),
    )
    assert document["status"] == "timeout"
    assert document["elapsed_seconds"] < 1.0
    assert document["process_tree_cleanup_passed"] is True


def test_pids_limit_remains_live_during_natural_exit(tmp_path: Path) -> None:
    child = (
        "import subprocess,time; time.sleep(.15); children=[]; "
        "exec(\"for _ in range(100):\\n"
        " try: children.append(subprocess.Popen(['/bin/sleep','30']))\\n"
        " except OSError: break\"); time.sleep(30)"
    )
    _, _, document = _run(
        tmp_path, _closed_pipe_descendant_program(child),
        limits=_limits(maximum_process_count=8, natural_exit_grace_seconds=1.0),
    )
    assert document["status"] == "pids_limit"
    assert document["natural_exit"]["cgroup_populated_at_root_exit"] is True
    assert document["cgroup"]["pids_events_delta"]["max"] > 0
    assert document["process_tree_cleanup_passed"] is True


def test_cpu_limit_remains_live_during_natural_exit(tmp_path: Path) -> None:
    child = (
        "import subprocess,sys,time; "
        "children=[subprocess.Popen([sys.executable,'-c','while True: pass']) for _ in range(2)]; "
        "time.sleep(30)"
    )
    _, _, document = _run(
        tmp_path, _closed_pipe_descendant_program(child),
        limits=_limits(rlimit_cpu_seconds=1, wall_timeout_seconds=4.0, natural_exit_grace_seconds=3.0),
    )
    assert document["status"] == "cpu_limit"
    assert document["natural_exit"]["cgroup_populated_at_root_exit"] is True
    assert document["cgroup"]["cpu_usage_usec"] > 1_000_000
    assert document["process_tree_cleanup_passed"] is True


def test_memory_limit_remains_live_during_natural_exit(tmp_path: Path) -> None:
    child = (
        "import subprocess,sys,time; time.sleep(.15); "
        "subprocess.Popen([sys.executable,'-c','value=bytearray(128*1024*1024)']); "
        "time.sleep(30)"
    )
    _, _, document = _run(
        tmp_path, _closed_pipe_descendant_program(child),
        limits=_limits(maximum_process_tree_rss_bytes=32 * 1024 * 1024,
                       natural_exit_grace_seconds=1.0, rss_sample_interval_seconds=.005),
    )
    assert document["status"] == "memory_limit"
    assert document["natural_exit"]["cgroup_populated_at_root_exit"] is True
    assert document["cgroup"]["memory_events_delta"]["max"] > 0
    assert document["process_tree_cleanup_passed"] is True


@pytest.mark.parametrize("value", [-1, 61, float("nan"), float("inf"), True])
def test_natural_exit_grace_must_be_finite_and_bounded(value) -> None:
    with pytest.raises(ValueError, match="grace|timing"):
        _limits(natural_exit_grace_seconds=value).validate()


def test_natural_exit_grace_is_attempt_bound(tmp_path: Path) -> None:
    common = dict(argv=["/bin/true"], unit={"unit_id": "synthetic"}, result_path=tmp_path / "result")
    assert SUPERVISOR.process_attempt_fingerprint(**common, limits=_limits(natural_exit_grace_seconds=.1)) != SUPERVISOR.process_attempt_fingerprint(**common, limits=_limits(natural_exit_grace_seconds=.2))


def test_original_supervisor_reproduces_closed_pipe_failure(tmp_path: Path) -> None:
    original_path = ROOT / "work/blind-phase-confirmatory-v2/bounded_process.py"
    assert SUPERVISOR._sha256_file(original_path) == "e4c687d9b5c721b61e59c9832b12f581057962bcf6c75c6dc544debb564feb49"
    name = "original_bounded_process_closed_pipe_reproduction"
    spec = importlib.util.spec_from_file_location(name, original_path)
    original = importlib.util.module_from_spec(spec)
    sys.modules[name] = original
    spec.loader.exec_module(original)
    settings = SUPERVISOR.asdict(_limits())
    settings.pop("natural_exit_grace_seconds")
    # Compare the exact original supervisor, not just zero grace in the fix.
    document = original.run_bounded_subprocess(
        [sys.executable, "-c", _closed_pipe_descendant_program("import time; time.sleep(.35)"), str(tmp_path / "result.json")],
        unit={"observation_id": 123, "arm": "candidate", "baudrate": 1200},
        result_path=tmp_path / "result.json", receipt_path=tmp_path / "receipt.json",
        limits=original.ProcessLimits(**settings),
    )
    assert document["status"] == "descendant_cgroup_survivor"
    assert document["return_code"] == 0
    assert document["inherited_pipe_failure_detected"] is False
    assert document["process_tree_cleanup_passed"] is True


def test_fast_parallel_bubblewrap_processes_exit_cleanly(tmp_path: Path) -> None:
    # Exercise the real wrapper used by the scientific runner. Very short
    # decoders can finish before cgroupfs/systemd scope teardown becomes
    # visible; 40 trials with four workers exercise that scheduling race.
    def run_one(number):
        root = tmp_path / str(number)
        root.mkdir()
        result = root / "result.json"
        document = SUPERVISOR.run_bounded_subprocess(
            ["/usr/bin/sudo", "-n", "/usr/bin/setpriv", "--ruid", "1000", "--euid", "0",
             "--rgid", "1000", "--egid", "1000", "--clear-groups", "--",
             "/usr/bin/bwrap", "--die-with-parent", "--unshare-all", "--new-session",
             "--cap-drop", "ALL", "--uid", "1000", "--gid", "1000", "--ro-bind", "/", "/",
             "--dev", "/dev", "--proc", "/proc", "--bind", str(root), str(root),
             sys.executable, "-c",
             "import pathlib,sys,time; time.sleep(.04); pathlib.Path(sys.argv[1]).write_text('{}')", str(result)],
            unit={"unit_id": f"synthetic-fast-{number}"}, result_path=result,
            receipt_path=root / "receipt.json", limits=_limits(natural_exit_grace_seconds=1.0),
        )
        assert document["status"] == "completed", document
        assert document["process_tree_cleanup_passed"] is True
        assert document["natural_exit"]["cgroup_populated_after_grace"] is False
        return document

    with ThreadPoolExecutor(max_workers=4) as pool:
        documents = list(pool.map(run_one, range(40)))
    assert len(documents) == 40
