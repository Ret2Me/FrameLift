from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import signal
import sys
import time

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "work/blind-phase-confirmatory-v2/bounded_process.py"


def _module():
    name = "blind_phase_bounded_process_test_module"
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
