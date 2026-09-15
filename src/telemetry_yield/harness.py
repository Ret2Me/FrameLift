"""Deterministic, resumable replay harness with process isolation."""

from __future__ import annotations

import hashlib
import multiprocessing as mp
import os
import signal
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Protocol, Sequence

try:
    import resource
except ImportError:  # pragma: no cover - resource is unavailable on Windows
    resource = None  # type: ignore[assignment]

from .canonical import config_hash
from .crc import validate_ax25_fcs
from .store import AttemptStore


class DecoderAdapter(Protocol):
    name: str
    version: str

    def decode(
        self,
        recording: Path,
        protocol_id: str,
        config: dict[str, Any],
        timeout_s: float,
        *,
        seed: int,
    ) -> Sequence[bytes]: ...


@dataclass(frozen=True)
class ReplayResult:
    """Outcome and resource use for one isolated attempt.

    ``cpu_seconds`` includes the Python worker and all terminated, waited-for
    decoder descendants where the platform exposes ``RUSAGE_CHILDREN``.
    ``peak_rss_mb`` is the maximum of the worker peak and descendant peak; it
    is deliberately not described as the sum of simultaneous tree memory.
    """

    status: str
    raw_frames_hex: tuple[str, ...]
    frame_sha256: tuple[str, ...]
    crc_valid_payload_sha256: tuple[str, ...]
    n_frames_raw: int
    n_frames_crc_valid: int
    cpu_seconds: float
    wall_seconds: float
    peak_rss_mb: float
    error_class: str | None = None
    error_message: str | None = None


def _rusage(who: int) -> Any | None:
    if resource is None:
        return None
    try:
        return resource.getrusage(who)
    except (AttributeError, OSError, ValueError):
        return None


def _usage_cpu_seconds(usage: Any | None) -> float:
    if usage is None:
        return 0.0
    return max(0.0, float(usage.ru_utime) + float(usage.ru_stime))


def _peak_rss_mb(usage: Any | None) -> float:
    if usage is None:
        return 0.0
    rss = usage.ru_maxrss
    rss_bytes = rss if sys.platform == "darwin" else rss * 1024.0
    return rss_bytes / (1024.0 * 1024.0)


def _resource_snapshot() -> tuple[float, float, float]:
    if resource is None:
        return (0.0, 0.0, 0.0)
    own = _rusage(resource.RUSAGE_SELF)
    child_kind = getattr(resource, "RUSAGE_CHILDREN", None)
    children = _rusage(child_kind) if child_kind is not None else None
    return (
        _usage_cpu_seconds(children),
        _peak_rss_mb(own),
        _peak_rss_mb(children),
    )


def _decode_worker(
    connection: Any,
    adapter: DecoderAdapter,
    recording: Path,
    protocol_id: str,
    parameters: dict[str, Any],
    timeout_s: float,
    seed: int,
    max_rss_mb: float | None,
) -> None:
    if os.name == "posix" and hasattr(os, "setsid"):
        os.setsid()
    cpu_start = time.process_time()
    child_cpu_start, _, _ = _resource_snapshot()
    try:
        if max_rss_mb is not None:
            if resource is None or not hasattr(resource, "RLIMIT_AS"):
                raise RuntimeError("address-space limits are unavailable")
            limit = int(max_rss_mb * 1024 * 1024)
            resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
        frames = tuple(
            bytes(frame)
            for frame in adapter.decode(
                recording,
                protocol_id,
                parameters,
                timeout_s,
                seed=seed,
            )
        )
        child_cpu_end, worker_peak_rss_mb, child_peak_rss_mb = (
            _resource_snapshot()
        )
        connection.send(
            (
                "ok",
                frames,
                time.process_time() - cpu_start
                + max(0.0, child_cpu_end - child_cpu_start),
                max(worker_peak_rss_mb, child_peak_rss_mb),
                None,
                None,
            )
        )
    except Exception as exc:
        child_cpu_end, worker_peak_rss_mb, child_peak_rss_mb = (
            _resource_snapshot()
        )
        connection.send(
            (
                "error",
                (),
                time.process_time() - cpu_start
                + max(0.0, child_cpu_end - child_cpu_start),
                max(worker_peak_rss_mb, child_peak_rss_mb),
                type(exc).__name__,
                str(exc),
            )
        )
    finally:
        connection.close()


def _terminate_worker_tree(process: mp.Process, *, grace_s: float = 2.0) -> None:
    """Terminate the isolated worker and its external decoder descendants."""
    pid = process.pid
    process_group = False
    if (
        pid is not None
        and os.name == "posix"
        and hasattr(os, "killpg")
        and hasattr(os, "getpgid")
    ):
        try:
            process_group = os.getpgid(pid) == pid
        except ProcessLookupError:
            process_group = False
    if process_group and pid is not None:
        try:
            os.killpg(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    elif process.is_alive():
        process.terminate()
    process.join(timeout=grace_s)
    if process_group and pid is not None:
        try:
            os.killpg(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    elif process.is_alive():
        if hasattr(process, "kill"):
            process.kill()
        else:  # pragma: no cover - for old Python implementations
            process.terminate()
    process.join(timeout=grace_s)


def _run_isolated(
    *,
    adapter: DecoderAdapter,
    recording_path: Path,
    protocol_id: str,
    parameters: dict[str, Any],
    seed: int,
    timeout_s: float,
    max_rss_mb: float | None,
) -> tuple[str, tuple[bytes, ...], float, float, str | None, str | None]:
    methods = mp.get_all_start_methods()
    context = mp.get_context("fork" if "fork" in methods else methods[0])
    receive, send = context.Pipe(duplex=False)
    process = context.Process(
        target=_decode_worker,
        args=(
            send,
            adapter,
            recording_path,
            protocol_id,
            parameters,
            timeout_s,
            seed,
            max_rss_mb,
        ),
        daemon=True,
    )
    process.start()
    send.close()
    try:
        if not receive.poll(timeout_s):
            _terminate_worker_tree(process)
            return (
                "timeout",
                (),
                0.0,
                0.0,
                "TimeoutError",
                f"decoder exceeded {timeout_s:.3f}s",
            )
        try:
            payload = receive.recv()
        except EOFError:
            payload = (
                "error",
                (),
                0.0,
                0.0,
                "DecoderProcessError",
                f"decoder process exited with code {process.exitcode}",
            )
        process.join(timeout=5)
        if process.is_alive():
            _terminate_worker_tree(process)
        return payload
    finally:
        receive.close()


def _uses_ax25_fcs(protocol_id: str, parameters: dict[str, Any]) -> bool:
    return (
        "ax25" in protocol_id.lower()
        or str(parameters.get("framing", "")).lower() == "ax25"
        or str(parameters.get("crc", "")).lower() == "fcs_ccitt_x25"
    )


def run_attempt(
    *,
    store: AttemptStore,
    adapter: DecoderAdapter,
    recording_id: str,
    recording_path: Path,
    protocol_id: str,
    parameters: dict[str, Any],
    seed: int = 0,
    timeout_s: float = 60.0,
    max_rss_mb: float | None = None,
) -> ReplayResult | None:
    """Run once in a worker process; return ``None`` for a cached terminal result."""
    if timeout_s <= 0:
        raise ValueError("timeout_s must be positive")
    if max_rss_mb is not None and max_rss_mb <= 0:
        raise ValueError("max_rss_mb must be positive")
    effective_config = {
        "protocol_id": protocol_id,
        "decoder": adapter.name,
        "decoder_version": adapter.version,
        "seed": seed,
        "config": parameters,
    }
    digest = config_hash(
        protocol_id=protocol_id,
        decoder=adapter.name,
        decoder_version=adapter.version,
        seed=seed,
        config=parameters,
    )
    lease_token = store.reserve(recording_id, digest, effective_config)
    if lease_token is None:
        return None

    wall_start = time.monotonic()
    status, frames, cpu_seconds, peak_rss_mb, error_class, error_message = (
        _run_isolated(
            adapter=adapter,
            recording_path=recording_path,
            protocol_id=protocol_id,
            parameters=parameters,
            seed=seed,
            timeout_s=timeout_s,
            max_rss_mb=max_rss_mb,
        )
    )
    valid_payloads = (
        tuple(frame[:-2] for frame in frames if validate_ax25_fcs(frame))
        if _uses_ax25_fcs(protocol_id, parameters)
        else ()
    )
    result = ReplayResult(
        status=status,
        raw_frames_hex=tuple(frame.hex() for frame in frames),
        frame_sha256=tuple(hashlib.sha256(frame).hexdigest() for frame in frames),
        crc_valid_payload_sha256=tuple(
            hashlib.sha256(payload).hexdigest() for payload in valid_payloads
        ),
        n_frames_raw=len(frames),
        n_frames_crc_valid=len(valid_payloads),
        cpu_seconds=max(0.0, cpu_seconds),
        wall_seconds=max(0.0, time.monotonic() - wall_start),
        peak_rss_mb=max(0.0, peak_rss_mb),
        error_class=error_class,
        error_message=error_message,
    )
    store.finish(
        recording_id,
        digest,
        status,
        asdict(result),
        lease_token=lease_token,
    )
    return result
