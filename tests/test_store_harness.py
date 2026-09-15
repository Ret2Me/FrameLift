from __future__ import annotations

import subprocess
import os
import sys
import tempfile
import time
import unittest
from datetime import timedelta
from pathlib import Path

from telemetry_yield.crc import append_ax25_fcs
from telemetry_yield.harness import run_attempt
from telemetry_yield.store import AttemptStore

try:
    import resource as test_resource
except ImportError:  # pragma: no cover - resource is unavailable on Windows
    test_resource = None


HAS_CHILD_RUSAGE = test_resource is not None and hasattr(
    test_resource,
    "RUSAGE_CHILDREN",
)


class EchoAdapter:
    name = "echo"
    version = "1.0"

    def decode(
        self, recording: Path, protocol_id: str, config: dict, timeout_s: float,
        *, seed: int,
    ):
        return [recording.read_bytes(), f"{protocol_id}:{seed}".encode()]


class FailingAdapter(EchoAdapter):
    name = "failing"

    def decode(
        self, recording: Path, protocol_id: str, config: dict, timeout_s: float,
        *, seed: int,
    ):
        raise RuntimeError("isolated decoder failure")


class ValidAX25Adapter(EchoAdapter):
    name = "valid-ax25"

    def decode(
        self, recording: Path, protocol_id: str, config: dict, timeout_s: float,
        *, seed: int,
    ):
        return [append_ax25_fcs(recording.read_bytes())]


class BlockingAdapter(EchoAdapter):
    name = "blocking"

    def decode(
        self, recording: Path, protocol_id: str, config: dict, timeout_s: float,
        *, seed: int,
    ):
        time.sleep(1)
        return []


class SubprocessAdapter(EchoAdapter):
    name = "subprocess"

    def decode(
        self, recording: Path, protocol_id: str, config: dict, timeout_s: float,
        *, seed: int,
    ):
        program = """
import time
payload = bytearray(48 * 1024 * 1024)
for index in range(0, len(payload), 4096):
    payload[index] = 1
deadline = time.process_time() + 0.15
while time.process_time() < deadline:
    sum(range(10000))
assert len(payload) == 48 * 1024 * 1024
"""
        subprocess.run(
            [sys.executable, "-c", program],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=timeout_s,
        )
        return [recording.read_bytes()]


class BlockingSubprocessAdapter(EchoAdapter):
    name = "blocking-subprocess"

    def decode(
        self, recording: Path, protocol_id: str, config: dict, timeout_s: float,
        *, seed: int,
    ):
        child = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        recording.write_text(str(child.pid), encoding="utf-8")
        time.sleep(30)
        return []


class HarnessTests(unittest.TestCase):
    def test_idempotent_success(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            recording = root / "recording.bin"
            recording.write_bytes(b"samples")
            store = AttemptStore(root / "attempts.sqlite")
            first = run_attempt(
                store=store,
                adapter=EchoAdapter(),
                recording_id="r1",
                recording_path=recording,
                protocol_id="ax25",
                parameters={"baud": 1200},
            )
            second = run_attempt(
                store=store,
                adapter=EchoAdapter(),
                recording_id="r1",
                recording_path=recording,
                protocol_id="ax25",
                parameters={"baud": 1200},
            )
            self.assertEqual(first.status, "ok")
            self.assertEqual(first.n_frames_raw, 2)
            self.assertEqual(first.n_frames_crc_valid, 0)
            self.assertEqual(len(first.raw_frames_hex), 2)
            self.assertEqual(bytes.fromhex(first.raw_frames_hex[1]), b"ax25:0")
            self.assertGreater(first.peak_rss_mb, 1.0)
            self.assertIsNone(second)

    def test_adapter_error_is_persisted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            recording = root / "recording.bin"
            recording.write_bytes(b"samples")
            result = run_attempt(
                store=AttemptStore(root / "attempts.sqlite"),
                adapter=FailingAdapter(),
                recording_id="r1",
                recording_path=recording,
                protocol_id="ax25",
                parameters={},
            )
            self.assertEqual(result.status, "error")
            self.assertEqual(result.error_class, "RuntimeError")

    def test_reference_crc_and_process_timeout(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            recording = root / "recording.bin"
            recording.write_bytes(b"payload")
            store = AttemptStore(root / "attempts.sqlite")
            valid = run_attempt(
                store=store,
                adapter=ValidAX25Adapter(),
                recording_id="valid",
                recording_path=recording,
                protocol_id="afsk1200_ax25",
                parameters={},
            )
            timed_out = run_attempt(
                store=store,
                adapter=BlockingAdapter(),
                recording_id="blocking",
                recording_path=recording,
                protocol_id="afsk1200_ax25",
                parameters={},
                timeout_s=0.05,
            )
            self.assertEqual(valid.n_frames_crc_valid, 1)
            self.assertEqual(len(valid.crc_valid_payload_sha256), 1)
            self.assertEqual(timed_out.status, "timeout")

    def test_stale_worker_cannot_finish_reclaimed_lease(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = AttemptStore(Path(tmp) / "attempts.sqlite")
            first = store.reserve("r", "h", {})
            second = store.reserve("r", "h", {}, stale_after=timedelta(seconds=-1))
            self.assertNotEqual(first, second)
            with self.assertRaises(RuntimeError):
                store.finish("r", "h", "ok", {}, lease_token=first)
            store.finish("r", "h", "ok", {}, lease_token=second)

    @unittest.skipUnless(HAS_CHILD_RUSAGE, "RUSAGE_CHILDREN is unavailable")
    def test_resource_metrics_include_decoder_subprocess(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            recording = root / "recording.bin"
            recording.write_bytes(b"samples")
            result = run_attempt(
                store=AttemptStore(root / "attempts.sqlite"),
                adapter=SubprocessAdapter(),
                recording_id="subprocess",
                recording_path=recording,
                protocol_id="test",
                parameters={},
                timeout_s=5.0,
            )
            self.assertEqual(result.status, "ok")
            self.assertGreaterEqual(result.cpu_seconds, 0.1)
            self.assertGreaterEqual(result.peak_rss_mb, 40.0)

    @unittest.skipUnless(os.name == "posix", "process groups require POSIX")
    def test_timeout_terminates_external_decoder_descendants(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            child_pid_path = root / "child.pid"
            result = run_attempt(
                store=AttemptStore(root / "attempts.sqlite"),
                adapter=BlockingSubprocessAdapter(),
                recording_id="blocking-subprocess",
                recording_path=child_pid_path,
                protocol_id="test",
                parameters={},
                timeout_s=0.5,
            )
            self.assertEqual(result.status, "timeout")
            child_pid = int(child_pid_path.read_text(encoding="utf-8"))
            child_proc = Path(f"/proc/{child_pid}")
            deadline = time.monotonic() + 2.0
            while child_proc.exists() and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertFalse(child_proc.exists())


if __name__ == "__main__":
    unittest.main()
