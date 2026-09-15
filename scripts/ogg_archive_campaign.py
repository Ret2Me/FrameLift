#!/usr/bin/env python3
"""Frozen CANVAS archive orchestration. prepare fetches metadata only; run is separate.

No receiver tuning, reference-byte inference, telemetry submission or OGG deletion.
Every completed attempt is immutable. Failures remain in the frozen denominator.
Linux /proc and flock are required for conservative interrupted-process detection.
"""
from __future__ import annotations

import argparse
import binascii
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import importlib.metadata
import json
import math
import os
from pathlib import Path
import re
import resource
import shutil
import signal
import stat
import subprocess
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
from telemetry_yield.ax25_validation import parse_ax25_ui
from telemetry_yield.gr_satellites_backend import parse_kiss
from ogg_pilot_report import compare_one

START = "2026-08-31T00:00:00Z"
END = "2026-09-07T00:00:00Z"
TRANSMITTER = "GCmN6RULea8dAT7Qoat8z2"
EXCLUDED = (14366383, 14115025, 14956101, 14936397)
HOSTS = frozenset(("network.satnogs.org", "network-satnogs.freetls.fastly.net",
                   "s3.eu-central-1.wasabisys.com"))
API = "https://network.satnogs.org/api/observations/"
NATIVE = ROOT / "scripts/ogg_refinement_pilot.py"
BASELINE = ROOT / "work/satnogs-ogg-pilot-20260907/baseline/ogg_baseline_run.py"
GOLDEN = ROOT / "work/golden/env"
SAT = GOLDEN / "lib/python3.12/site-packages/satellites"
PYTHON = ROOT / ".venv/bin/python"
MiB = 1 << 20
GiB = 1 << 30
LIMITS = {"page_bytes": 4 * MiB, "metadata_bytes": 128 * MiB, "pages": 300,
          "ogg_bytes": 64 * MiB, "reference_bytes": MiB, "reference_count": 1000,
          "reference_total_bytes": 32 * MiB, "duration_seconds": 1800,
          "wav_bytes": 512 * MiB, "reserve_bytes": 1536 * MiB,
          "campaign_bytes": 3 * GiB, "process_output_bytes": 32 * MiB,
          "process_address_bytes": 6 * GiB, "http_timeout_seconds": 30,
          "http_attempts": 3, "attempts_per_observation": 3}
CONTRACT = {"start_inclusive": START, "start_exclusive": END, "norad": 68635,
            "transmitter": TRANSMITTER, "mode": "GMSK", "baud": 9600,
            "excluded": list(EXCLUDED), "maximum": 100,
            "ordering": "start descending, integer id descending",
            "selection_uses_outcomes": False,
            "representation": "fm_demodulated", "sample_rate_hz": 48000,
            "audio_channels": 1, "pcm_subtype": "FLOAT", "resampling": False,
            "receiver": "fast/diverse", "window_seconds": 6, "hop_seconds": 3,
            "top_timing": 16, "telemetry_submission": False}


class Pause(RuntimeError):
    """Campaign-level condition; do not convert it into an observation zero."""


class Unavailable(RuntimeError):
    """Bounded acquisition exhausted; not a decoding result."""


def utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def encoded(value) -> bytes:
    return (json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()


def identity(path: Path) -> dict:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(MiB), b""):
            digest.update(block)
    return {"path": str(path.absolute()), "sha256": digest.hexdigest(),
            "bytes": path.stat().st_size}


def verify(item: dict) -> None:
    current = identity(Path(item["path"]))
    if current != item:
        raise Pause("artifact/source identity drift: " + item["path"])


def verify_expected(item: dict, expected: Path) -> None:
    if item["path"] != str(expected.absolute()):
        raise Pause("identity points outside the exact expected artifact path")
    verify(item)


def read(path: Path):
    with path.open() as stream:
        return json.load(stream)


def durable_json(path: Path, value) -> None:
    """No replacement: atomic hard-link publication on the same filesystem."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".writing-" + uuid.uuid4().hex)
    with temporary.open("xb") as stream:
        stream.write(encoded(value))
        stream.flush()
        os.fsync(stream.fileno())
    os.link(temporary, path)  # Fails closed if an earlier result already exists.
    temporary.unlink()  # Only this disposable JSON staging name, not user data.
    directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def no_symlinks(path: Path) -> None:
    for component in (path, *path.parents):
        if component.is_symlink():
            raise Pause("symlink is not an owned campaign path: " + str(component))


@contextmanager
def locked(root: Path):
    no_symlinks(root)
    with (root / "campaign.lock").open("a+b") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise Pause("another campaign coordinator holds the lock") from error
        yield


def tree_bytes(root: Path) -> int:
    total = 0
    for directory, dirs, files in os.walk(root, followlinks=False):
        for name in dirs + files:
            path = Path(directory) / name
            if path.is_symlink():
                raise Pause("symlink in campaign artifact tree")
        total += sum((Path(directory) / name).stat().st_size for name in files)
    return total


def disk_guard(root: Path, anticipated: int = 0, limits=LIMITS) -> None:
    if shutil.disk_usage(root).free < limits["reserve_bytes"] + anticipated:
        raise Pause("disk_pause: free-space reserve would be exceeded")
    if tree_bytes(root) + anticipated > limits["campaign_bytes"]:
        raise Pause("disk_pause: campaign retained-artifact budget would be exceeded")


def parsed_time(value: str) -> datetime:
    date = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if date.tzinfo is None:
        raise ValueError("naive observation timestamp")
    return date.astimezone(timezone.utc)


def choose(rows: list[dict]) -> list[dict]:
    """Pure metadata-only selection; incomplete/conflicting metadata fails closed."""
    unique = {}
    for row in rows:
        obs = row["id"]
        if isinstance(obs, bool) or not isinstance(obs, int) or obs <= 0:
            raise ValueError("invalid integer observation ID")
        if obs in unique and encoded(unique[obs]) != encoded(row):
            raise ValueError("conflicting duplicate observation ID")
        unique[obs] = row
    selected = []
    for obs, row in unique.items():
        start = parsed_time(row["start"])
        transmitter = row.get("transmitter_uuid", row.get("transmitter"))
        if "transmitter_uuid" in row and "transmitter" in row and row["transmitter_uuid"] != row["transmitter"]:
            raise ValueError("conflicting transmitter metadata")
        if (parsed_time(START) <= start < parsed_time(END) and obs not in EXCLUDED
                and row["norad_cat_id"] == 68635 and transmitter == TRANSMITTER
                and row["transmitter_mode"] == "GMSK" and row["transmitter_baud"] == 9600):
            selected.append(row)
    return sorted(selected, key=lambda row: (parsed_time(row["start"]), row["id"]), reverse=True)[:100]


def cohort_coverage(rows: list[dict]) -> dict:
    dates = [parsed_time(row["start"]) for row in rows]
    stations = {row["ground_station"] for row in rows if row.get("ground_station") is not None}
    return {"start_min": min(dates).isoformat() if dates else None,
            "start_max": max(dates).isoformat() if dates else None,
            "distinct_stations": len(stations), "station_ids": sorted(stations, key=str),
            "distinct_utc_days": len({date.date() for date in dates}),
            "utc_days": sorted({date.date().isoformat() for date in dates}),
            "interpretation": "At most 100 newest observations within the frozen week; "
                              "not full-week coverage or 100 independent transmissions/passes"}


def safe_url(url: str, *, api: bool = False) -> str:
    parts = urlsplit(url)
    if (parts.scheme != "https" or parts.hostname not in HOSTS or parts.port not in (None, 443)
            or parts.username or parts.password or parts.fragment):
        raise ValueError("URL outside the explicit public HTTPS allowlist")
    if api:
        query = parse_qs(parts.query)
        if parts.hostname != "network.satnogs.org" or parts.path != "/api/observations/":
            raise ValueError("pagination left the observation endpoint")
        required = {"transmitter_uuid": TRANSMITTER, "start": START, "start__lt": END,
                    "format": "json"}
        if any(query.get(key) != [value] for key, value in required.items()):
            raise ValueError("pagination dropped or changed frozen filters")
        if set(query) - set(required) - {"cursor"}:
            raise ValueError("unplanned pagination/outcome filter")
    return url


class SafeRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        safe_url(newurl, api=urlsplit(req.full_url).path == "/api/observations/")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def next_page(link: str) -> str | None:
    matches = re.findall(r'<([^>]+)>\s*;\s*rel="?next"?(?:\s*,|$)', link)
    if len(matches) > 1:
        raise ValueError("ambiguous pagination")
    return safe_url(matches[0], api=True) if matches else None


class Fetcher:
    """One transfer at a time. Receipts validate cache; partial bodies are retained."""
    def __init__(self, root: Path, limits=LIMITS, opener=None, sleeper=time.sleep):
        self.root, self.limits = root, limits
        self.opener = opener or build_opener(SafeRedirect())
        self.sleeper = sleeper

    def fetch(self, url: str, target: Path, limit: int, *, api=False) -> dict:
        safe_url(url, api=api)
        receipt = target.with_name(target.name + ".receipt.json")
        if receipt.exists():
            result = read(receipt)
            if result["url"] != url or result["limit"] != limit:
                raise Pause("cache request contract mismatch")
            verify_expected(result["identity"], target)
            return result
        if target.exists():
            raise Pause("uncommitted cache exists; retain and audit rather than overwrite")
        errors = []
        for attempt in range(self.limits["http_attempts"]):
            disk_guard(self.root, limit, self.limits)
            partial = target.with_name(target.name + ".partial-" + uuid.uuid4().hex)
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                self.sleeper(0.2)
                request = Request(url, headers={"User-Agent": "telemetry-yield-ogg-research/1",
                                               "Accept-Encoding": "identity"})
                with self.opener.open(request, timeout=self.limits["http_timeout_seconds"]) as response:
                    safe_url(response.url, api=api)
                    if response.status != 200:
                        raise Unavailable("unexpected non-200 HTTP response")
                    advertised = response.headers.get("Content-Length")
                    if advertised and int(advertised) > limit:
                        raise Unavailable("advertised object exceeds frozen limit")
                    count = 0
                    with partial.open("xb") as stream:
                        while block := response.read(min(256 * 1024, limit + 1 - count)):
                            count += len(block)
                            if count > limit:
                                raise Unavailable("streamed object exceeds frozen limit")
                            disk_guard(self.root, len(block), self.limits)
                            stream.write(block)
                        stream.flush()
                        os.fsync(stream.fileno())
                    if advertised and count != int(advertised):
                        raise Unavailable("truncated or inconsistent Content-Length")
                    os.link(partial, target)
                    partial.unlink()  # Same retained bytes now have the final cache name.
                    result = {"url": url, "final_url": response.url, "retrieved_utc": utc(),
                              "headers": dict(response.headers), "status": response.status,
                              "limit": limit, "identity": identity(target), "previous_errors": errors}
                    durable_json(receipt, result)
                    return result
            except HTTPError as error:
                errors.append({"http_status": error.code, "attempt": attempt + 1})
                if error.code not in (429, 500, 502, 503, 504):
                    break
                retry_after = error.headers.get("Retry-After", "0")
                # Never hammer a server whose requested pause exceeds this bounded invocation.
                if not retry_after.isdecimal() or int(retry_after) > 30:
                    raise Unavailable("HTTP Retry-After requires a later invocation") from error
                self.sleeper(max(1 + attempt, int(retry_after)))
            except (URLError, TimeoutError, OSError, Unavailable) as error:
                errors.append({"error": str(error), "attempt": attempt + 1})
                self.sleeper(1 + attempt)
        raise Unavailable(json.dumps(errors))


def source_snapshot() -> dict:
    """Overinclusive project/package code closure avoids omitting dynamic CCSDS imports."""
    paths = {Path(__file__).absolute(), NATIVE, BASELINE,
             ROOT / "scripts/ogg_native_pilot.py", ROOT / "scripts/ogg_pilot_report.py",
             ROOT / "scripts/ogg_positive_pilot.py",
             ROOT / "reports/ogg-archive-week-preanalysis-v1.md",
             ROOT / "docs/ogg-archive-campaign-design-v1.md"}
    paths.update((ROOT / "src/telemetry_yield").rglob("*.py"))
    paths.update(SAT.rglob("*.py"))
    paths.update(SAT.rglob("*.so"))
    paths.update((PYTHON, PYTHON.resolve(), GOLDEN / "bin/python", (GOLDEN / "bin/python").resolve(),
                  GOLDEN / "bin/gr_satellites", Path("/usr/bin/ffmpeg"), Path("/usr/bin/ffprobe")))
    versions = {}
    # RECORD plus every installed distribution file pins native scientific libraries,
    # including bundled BLAS/libsndfile shared objects; pycache is not source.
    for name in ("numpy", "scipy", "soundfile", "cffi", "pycparser"):
        distribution = importlib.metadata.distribution(name)
        versions[name] = distribution.version
        paths.update(Path(distribution.locate_file(item)).absolute()
                     for item in distribution.files or ()
                     if "__pycache__" not in item.parts and Path(distribution.locate_file(item)).is_file())
    runtime_code = """
import importlib.metadata, json, pathlib, sys
import numpy, pmt
from gnuradio import gr
from satellites.components.demodulators import fsk_demodulator
from satellites.components.deframers import ax25_deframer
files = {str(pathlib.Path(module.__file__).absolute()) for module in list(sys.modules.values())
         if getattr(module, '__file__', None) and pathlib.Path(module.__file__).is_file()}
for line in pathlib.Path('/proc/self/maps').read_text().splitlines():
    name = line.split()[-1]
    if name.startswith('/') and pathlib.Path(name).is_file():
        files.add(name)
print(json.dumps({'python_version': sys.version, 'python_executable': sys.executable,
                  'numpy': numpy.__version__,
                  'gnuradio': gr.version(), 'files': sorted(files)}))
"""
    environment = dict(os.environ, GR_SATELLITES_SUBMIT_TLM="0", PYTHONDONTWRITEBYTECODE="1",
                       OPENBLAS_NUM_THREADS="1", OMP_NUM_THREADS="1", MKL_NUM_THREADS="1")
    probe = subprocess.run([str(GOLDEN / "bin/python"), "-c", runtime_code], env=environment,
                           capture_output=True, check=True, timeout=60)
    if len(probe.stdout) > 4 * MiB:
        raise Pause("baseline runtime identity probe exceeded its metadata cap")
    baseline_runtime = json.loads(probe.stdout)
    paths.update(Path(name) for name in baseline_runtime["files"])
    for line in Path("/proc/self/maps").read_text().splitlines():
        name = line.split()[-1]
        if name.startswith("/") and Path(name).is_file():
            paths.add(Path(name))
    return {"recorded_utc": utc(), "python_executable": str(Path(sys.executable).absolute()),
            "python_version": sys.version, "versions": versions,
            "baseline_runtime": baseline_runtime,
            "scope": "All telemetry_yield Python sources; all satellites Python/extension sources; "
                     "native scientific distribution files; loaded baseline modules and mapped shared libraries; "
                     "drivers/scorer and executable identities. "
                     "Not a container or exhaustive OS shared-library image.",
            "files": [identity(path) for path in sorted(paths)]}


def verify_sources(snapshot: dict) -> None:
    if snapshot["python_version"] != sys.version or snapshot["python_executable"] != str(Path(sys.executable).absolute()):
        raise Pause("source_drift: different native interpreter")
    for item in snapshot["files"]:
        verify(item)
    for name, version in snapshot["versions"].items():
        if importlib.metadata.version(name) != version:
            raise Pause("source_drift: different scientific library version")


def prepare(root: Path, *, fetcher=None, snapshotter=source_snapshot) -> dict:
    no_symlinks(root)
    root.mkdir(parents=True, exist_ok=False)
    with locked(root):
        plan = {"schema": "ogg-archive-campaign-plan-v1", "created_utc": utc(),
                "contract": CONTRACT, "limits": LIMITS, "sources": snapshotter(),
                "preanalysis": identity(ROOT / "reports/ogg-archive-week-preanalysis-v1.md"),
                "selection_source": "Metadata-only retrospective live API snapshot, not transactional",
                "api_filter_sources": ["https://gitlab.com/librespacefoundation/satnogs/satnogs-network/-/raw/master/network/api/filters.py",
                                       "https://gitlab.com/librespacefoundation/satnogs/satnogs-network/-/raw/master/network/api/views.py"],
                "publication_ready": False, "deployment_ready": False}
        durable_json(root / "plan.json", plan)  # BEFORE metadata and any candidate results.
        fetcher = fetcher or Fetcher(root)
        url = API + "?" + urlencode({"transmitter_uuid": TRANSMITTER, "start": START,
                                      "start__lt": END, "format": "json"})
        rows, pages, seen, total = [], [], set(), 0
        while url:
            if url in seen or len(pages) >= LIMITS["pages"]:
                raise Pause("incomplete preparation: pagination cycle or page cap before EOF")
            seen.add(url)
            page = root / "metadata" / f"page-{len(pages):03d}.json"
            item = fetcher.fetch(url, page, LIMITS["page_bytes"], api=True)
            total += item["identity"]["bytes"]
            if total > LIMITS["metadata_bytes"]:
                raise Pause("incomplete preparation: metadata byte cap")
            values = read(page)
            if not isinstance(values, list) or any(not isinstance(row, dict) for row in values):
                raise ValueError("unexpected API schema")
            rows.extend(values)
            pages.append(item)
            url = next_page(item["headers"].get("Link", item["headers"].get("link", "")))
        selected = choose(rows)
        cohort = {"schema": "ogg-archive-frozen-cohort-v1", "frozen_utc": utc(),
                  "observations": selected, "ids": [row["id"] for row in selected],
                  "raw_record_count": len(rows), "selected_count": len(selected),
                  "pages": pages, "pagination_complete": True, "contract": CONTRACT,
                  "actual_coverage": cohort_coverage(selected)}
        durable_json(root / "cohort.json", cohort)
        durable_json(root / "freeze.json", {"plan": identity(root / "plan.json"),
                                            "cohort": identity(root / "cohort.json"),
                                            "recorded_utc": utc(), "outcomes_computed": False})
        return cohort


def process_boundary() -> dict:
    return {"boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
            "cgroup": Path("/proc/self/cgroup").read_text().strip(),
            "minimum_start_ticks": max(0, int(float(Path("/proc/uptime").read_text().split()[0])
                                               * os.sysconf("SC_CLK_TCK")) - 1)}


def active_token_pids(token: str, boundary: dict | None = None, *, proc_root=Path("/proc")) -> list[int]:
    boundary = boundary or process_boundary()
    if (proc_root / "sys/kernel/random/boot_id").read_text().strip() != boundary["boot_id"]:
        return []  # No process survives a kernel boot; never reuse old PID numbers.
    if not boundary.get("cgroup"):
        raise Pause("process boundary lacks the required inherited cgroup identity")
    needle = ("OGG_ARCHIVE_ATTEMPT_TOKEN=" + token).encode()
    found = []
    for directory in proc_root.iterdir():
        if not directory.name.isdecimal() or int(directory.name) == os.getpid():
            continue
        diagnostic = {"pid": int(directory.name), "state": None, "comm": None,
                      "start_ticks": None, "cgroup": None}
        # PF_EXITING can make environ inaccessible before stat reports Z.
        # Retry for at most 150 ms; a persistently live unreadable process in
        # our inherited group still blocks. Never log its environment contents.
        for retry in range(6):
            try:
                if directory.stat().st_uid != os.getuid():
                    break
                raw_stat = (directory / "stat").read_text()
                fields = raw_stat.rsplit(")", 1)[1].split()
                diagnostic.update(state=fields[0], start_ticks=int(fields[19]),
                                  comm=raw_stat.split("(", 1)[1].rsplit(")", 1)[0][:80])
                if fields[0] in ("Z", "X") or int(fields[19]) < boundary["minimum_start_ticks"]:
                    break
                diagnostic["cgroup"] = (directory / "cgroup").read_text().strip()
                if diagnostic["cgroup"] != boundary["cgroup"]:
                    break  # sessions may differ; only the inherited cgroup is relevant.
                if needle in (directory / "environ").read_bytes().split(b"\0"):
                    found.append(int(directory.name))
                break
            except (FileNotFoundError, ProcessLookupError):
                break
            except PermissionError as error:
                if retry == 5:
                    raise Pause("cannot inspect a potentially new same-user process after 150 ms: "
                                + json.dumps(diagnostic, sort_keys=True)) from error
                time.sleep(0.03)
    return found


def ensure_no_old_processes(root: Path) -> None:
    for receipt in root.glob("observations/*/attempt-*/process-*.json"):
        record = read(receipt)
        if active_token_pids(record["token"], record["process_boundary"]):
            raise Pause("an old owned child process remains alive; no duplicate launch")


def execute(command: list[str], attempt: Path, label: str, root: Path, *, timeout: float,
            output_limit: int = LIMITS["process_output_bytes"]) -> None:
    """Bounded direct logs, process resource caps and inherited-token orphan checks."""
    disk_guard(root, output_limit)
    token = uuid.uuid4().hex
    boundary = process_boundary()
    durable_json(attempt / ("process-" + label + ".json"),
                 {"command": command, "token": token, "process_boundary": boundary, "started_utc": utc(),
                  "timeout_seconds": timeout, "network_submission": False})
    env = dict(os.environ)
    env.update(OGG_ARCHIVE_ATTEMPT_TOKEN=token, GR_SATELLITES_SUBMIT_TLM="0",
               OPENBLAS_NUM_THREADS="1", OMP_NUM_THREADS="1", MKL_NUM_THREADS="1",
               PYTHONDONTWRITEBYTECODE="1", PYTHONPATH=str(ROOT / "src"))

    def child_limits():
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        resource.setrlimit(resource.RLIMIT_AS, (LIMITS["process_address_bytes"],) * 2)
        resource.setrlimit(resource.RLIMIT_FSIZE, (max(output_limit, LIMITS["wav_bytes"]),) * 2)

    before = tree_bytes(attempt)
    started = time.monotonic()
    with (attempt / (label + ".stdout.log")).open("xb") as stdout, (attempt / (label + ".stderr.log")).open("xb") as stderr:
        proc = subprocess.Popen(command, stdout=stdout, stderr=stderr, cwd=ROOT, env=env,
                                start_new_session=True, preexec_fn=child_limits)
        try:
            while proc.poll() is None:
                if time.monotonic() - started > timeout:
                    raise TimeoutError(label + " process timeout")
                disk_guard(root)
                if tree_bytes(attempt) - before > output_limit:
                    raise Pause(label + " exceeded its output budget")
                time.sleep(0.2)
            if proc.returncode:
                raise RuntimeError(label + " nonzero exit " + str(proc.returncode))
            if tree_bytes(attempt) - before > output_limit:
                raise Pause(label + " exceeded its output budget")
        except BaseException:
            # Baseline starts a separate grandchild session. Kill only processes
            # carrying our exact inherited token, never broad shared-user groups.
            scan_failure = None
            for sig in (signal.SIGTERM, signal.SIGKILL):
                # The Popen child is owned even if a later /proc audit is blocked.
                if proc.poll() is None:
                    try:
                        os.killpg(proc.pid, sig)
                    except ProcessLookupError:
                        pass
                try:
                    pids = active_token_pids(token, boundary)
                except Pause as error:
                    scan_failure, pids = error, []
                for pid in pids:
                    try:
                        os.kill(pid, sig)
                    except ProcessLookupError:
                        pass
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    pass
            if scan_failure:
                raise scan_failure
            raise
    if active_token_pids(token, boundary):
        raise Pause("owned subprocess descendants still alive after process exit")


def ownership(wav: Path) -> dict:
    no_symlinks(wav)
    info = wav.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1:
        raise Pause("WAV is not a uniquely owned regular file")
    return {**identity(wav), "device": info.st_dev, "inode": info.st_ino, "uid": info.st_uid}


def cleanup_wav(attempt: Path) -> None:
    """The only material deletion: exact own audio.wav after committed comparison."""
    no_symlinks(attempt)
    commit = read(attempt / "commit.json")
    verify_expected(commit["final"], attempt / "final.json")
    final = read(attempt / "final.json")
    if final["status"] != "complete" or not final.get("comparison"):
        raise Pause("WAV deletion requires a committed successful comparison")
    for item in final["artifacts"]:
        verify(item)
    wav = attempt / "audio.wav"
    owner = read(attempt / "wav-ownership.json")
    if owner["path"] != str(wav.absolute()):
        raise Pause("foreign WAV path in ownership receipt")
    intent = attempt / "wav-delete-intent.json"
    done = attempt / "wav-deleted.json"
    if intent.exists() and read(intent)["original_identity"] != owner:
        raise Pause("inconsistent deletion intent")
    if not wav.exists() and not wav.is_symlink():
        if not intent.exists():
            raise Pause("WAV disappeared without a committed deletion intent")
        if not done.exists():
            durable_json(done, {"deleted_utc": utc(), "crash_recovered_after_intent": True,
                                "original_identity": owner})
        return
    if done.exists() or ownership(wav) != owner:
        raise Pause("WAV ownership or content changed; nothing deleted")
    if not intent.exists():
        durable_json(intent, {"original_identity": owner, "committed_final": commit["final"],
                              "created_utc": utc()})
    elif read(intent)["original_identity"] != owner:
        raise Pause("inconsistent deletion intent")
    wav.unlink()
    durable_json(done, {"deleted_utc": utc(), "crash_recovered_after_intent": False,
                        "original_identity": owner})


def independent_crc(frame: bytes) -> bool:
    """binascii polynomial 0x1021 with reflection, independent of project bit loop."""
    if len(frame) < 18:
        return False
    reflect = lambda value: int(f"{value:08b}"[::-1], 2)
    register = binascii.crc_hqx(bytes(reflect(value) for value in frame[:-2]), 0xffff)
    result = int(f"{register:016b}"[::-1], 2) ^ 0xffff
    return result.to_bytes(2, "little") == frame[-2:]


def validate_execution(attempt: Path, samples: int, sample_rate: int) -> None:
    native = read(attempt / "native.json")
    baseline = read(attempt / "baseline/result.json")
    if native["decoder"] != "fast" or native["clock_bank_policy"] != "diverse":
        raise ValueError("wrong native treatment")
    if native["failed_window_count"] or any(row["frames"] or row["failures"] for row in native["controls"]):
        raise ValueError("native window or smoke control failure")
    if len(native["controls"]) != 2:
        raise ValueError("missing native smoke control")
    if (not baseline["completed"] or baseline["returncode"] != 0 or baseline["timed_out"]
            or baseline["malformed_kiss_records"] or baseline["mode"] != "fsk-g3ruh"
            or baseline["baud"] != 9600 or baseline["input_representation"] != "fm_demodulated"
            or baseline["telemetry_submission"] is not False
            or not (attempt / "baseline/frames.kiss").is_file()):
        raise ValueError("incomplete/wrong baseline, including missing KISS")
    parsed = parse_kiss((attempt / "baseline/frames.kiss").read_bytes())
    raw_pdus = {record.payload.hex() for record in parsed.data_frames}
    reported_pdus = {item["hex"] for item in baseline["pdus"]}
    if (parsed.malformed_records or len(parsed.data_frames) != baseline["raw_kiss_data_records"]
            or raw_pdus != reported_pdus or len(baseline["pdus"]) != len(reported_pdus)):
        raise ValueError("baseline JSON does not faithfully match original raw KISS")
    for item in baseline["pdus"]:
        payload = bytes.fromhex(item["hex"])
        if (item["strict_ax25_ui"] != (parse_ax25_ui(payload) is not None)
                or item["sha256"] != hashlib.sha256(payload).hexdigest() or item["length"] != len(payload)):
            raise ValueError("baseline PDU digest/structure/length mismatch")
    journal = [json.loads(line) for line in (attempt / "native.windows.jsonl").read_text().splitlines()]
    expected = []
    for offset in range(0, samples, sample_rate * 3):
        count = min(sample_rate * 6, samples - offset)
        if count < 8192:
            break
        expected.append((offset / sample_rate, count))
        if offset + sample_rate * 6 >= samples:
            break
    if (not expected or [(row["offset_seconds"], row["samples"]) for row in journal] != expected
            or len(journal) != native["window_count"] or any(row["failures"] for row in journal)):
        raise ValueError("incomplete or inconsistent raw-audio window coverage")
    frames = native["frames"]
    if (len(frames) != native["unique_pdu_count"]
            or len({row["payload_hex"] for row in frames}) != len(frames)
            or sum(len(bytes.fromhex(row["payload_hex"])) for row in frames) != native["unique_pdu_bytes"]):
        raise ValueError("native full-PDU count/byte mismatch")
    instances = {offset: [] for offset, _ in expected}
    for row in native["frames"]:
        raw = bytes.fromhex(row["frame_with_fcs_hex"])
        if not independent_crc(raw) or raw[:-2].hex() != row["payload_hex"] or parse_ax25_ui(raw[:-2]) is None:
            raise ValueError("independent original received FCS/structure check failed")
        if not row["provenance"]:
            raise ValueError("native PDU has no replay provenance")
        for provenance in row["provenance"]:
            offset = provenance["window_start_seconds"]
            if offset not in instances:
                raise ValueError("native provenance refers to an unexecuted window")
            instances[offset].append(row["payload_hex"])
    cumulative = set()
    for row in journal:
        present = instances[row["offset_seconds"]]
        cumulative.update(present)
        if len(present) != row["frame_instances"] or len(cumulative) != row["unique_total"]:
            raise ValueError("native PDU/provenance does not faithfully match window journal")


def references(fetcher: Fetcher, row: dict, observation: Path) -> dict:
    values = row.get("demoddata")
    if not isinstance(values, list) or len(values) > LIMITS["reference_count"]:
        return {"complete": False, "reason": "reference list missing/malformed/over limit", "objects": []}
    found, failures, total = [], [], 0
    for number, entry in enumerate(values):
        try:
            if total >= LIMITS["reference_total_bytes"]:
                failures.append({"index": number, "error": "reference aggregate cap; remaining objects not fetched"})
                break
            url = entry["payload_demod"]
            target = observation / "references" / f"reference-{number:04d}.bin"
            receipt = fetcher.fetch(url, target, min(LIMITS["reference_bytes"], LIMITS["reference_total_bytes"] - total))
            total += receipt["identity"]["bytes"]
            if total > LIMITS["reference_total_bytes"]:
                raise Unavailable("reference aggregate cap")
            data = target.read_bytes()
            found.append({**receipt["identity"], "url": url, "payload_hex": data.hex(),
                          "strict_ax25_ui_structure": parse_ax25_ui(data) is not None,
                          "independently_verified_crc": False})
        except (Unavailable, ValueError, KeyError, TypeError) as error:
            failures.append({"index": number, "error": str(error)})
    return {"complete": not failures and len(found) == len(values), "listed_count": len(values),
            "objects": found, "failures": failures,
            "empty_complete_list_is_unlabeled_not_a_negative_control": not values}


def finish(attempt: Path, state: dict) -> dict:
    # Files retained elsewhere (original OGG/references) are explicitly inventoried.
    paths = {Path(item["path"]) for item in state.get("external_artifacts", [])}
    paths.update(path for path in attempt.rglob("*") if path.is_file() and path.name != "audio.wav")
    state["artifacts"] = [identity(path) for path in sorted(paths)]
    state["finished_utc"] = utc()
    durable_json(attempt / "final.json", state)
    durable_json(attempt / "commit.json", {"final": identity(attempt / "final.json")})
    if state["status"] == "complete":
        cleanup_wav(attempt)
    return state


def one_observation(root: Path, row: dict, plan: dict, plan_sha: str, *, fetcher=None,
                    executor=execute, source_checker=verify_sources) -> dict:
    observation = root / "observations" / str(row["id"])
    observation.mkdir(parents=True, exist_ok=True)
    attempts = sorted(observation.glob("attempt-*"))
    if len(attempts) >= LIMITS["attempts_per_observation"]:
        raise Pause("per-observation attempt cap reached")
    attempt = observation / f"attempt-{len(attempts) + 1:03d}"
    attempt.mkdir()
    state = {"schema": "ogg-archive-observation-result-v1", "observation_id": row["id"],
             "plan_sha256": plan_sha, "started_utc": utc(), "status": "initializing",
             "native_status": "not_run", "baseline_status": "not_run", "ogg_status": "not_acquired",
             "comparison": None, "references": {"complete": False, "objects": []},
             "candidate_attribution": "unverified; CANVAS selection is not transmitter proof",
             "external_artifacts": [], "attempt": str(attempt)}
    fetcher = fetcher or Fetcher(root)
    try:
        source_checker(plan["sources"])
        refs = references(fetcher, row, observation)
        state["references"] = refs
        state["external_artifacts"].extend({key: item[key] for key in ("path", "sha256", "bytes")} for item in refs["objects"])
        # Acquire references even without OGG, so global archive completeness is honest.
        if not row.get("payload"):
            state["ogg_status"] = state["status"] = "ogg_absent_in_snapshot"
            return finish(attempt, state)
        if not refs["complete"]:
            state["status"] = "reference_incomplete"
            return finish(attempt, state)
        state["status"] = "ogg_unavailable"
        capture = observation / "capture.ogg"
        receipt = fetcher.fetch(row["payload"], capture, LIMITS["ogg_bytes"])
        state["external_artifacts"].append(receipt["identity"])
        state["ogg_status"] = "available"
        state["status"] = "unsupported_audio"
        executor(["/usr/bin/ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(capture)],
                 attempt, "probe", root, timeout=60)
        probe = read(attempt / "probe.stdout.log")
        streams = probe["streams"]
        if (len(streams) != 1 or streams[0]["codec_type"] != "audio"
                or streams[0]["channels"] != 1 or int(streams[0]["sample_rate"]) != 48000):
            raise ValueError("unsupported audio; v1 freezes mono 48 kHz, without implicit resampling")
        duration = float(probe["format"]["duration"])
        if not math.isfinite(duration) or not 8192 / 48000 <= duration <= LIMITS["duration_seconds"]:
            raise ValueError("audio duration outside frozen limits")
        wav = attempt / "audio.wav"
        command = ["/usr/bin/ffmpeg", "-nostdin", "-v", "error", "-n", "-i", str(capture),
                   "-map", "0:a:0", "-c:a", "pcm_f32le", str(wav)]
        disk_guard(root, LIMITS["wav_bytes"])
        executor(command, attempt, "convert", root, timeout=120, output_limit=LIMITS["wav_bytes"])
        import soundfile as sf
        info = sf.info(wav)
        if info.channels != 1 or info.samplerate != 48000 or info.subtype != "FLOAT" or not 8192 <= info.frames <= 48000 * 1800:
            raise ValueError("converted audio violates frozen PCM contract")
        durable_json(attempt / "wav-ownership.json", ownership(wav))
        manifest = {"observation_id": row["id"], "observation_url": f"https://network.satnogs.org/observations/{row['id']}/",
                    "satellite": row.get("tle0"), "mode": "GMSK", "baudrate": 9600,
                    "duration_seconds": info.frames / info.samplerate, "sample_rate_hz": info.samplerate,
                    "samples": info.frames, "ogg": receipt["identity"], "wav": identity(wav),
                    "ffmpeg_command": command, "references": refs["objects"],
                    "reference_list_complete": True, "frozen_plan_sha256": plan_sha}
        durable_json(attempt / "input-manifest.json", manifest)
        state["status"] = "native_failed"
        source_checker(plan["sources"])
        executor([str(PYTHON), str(NATIVE), "--input", str(wav), "--output", str(attempt / "native.json"),
                  "--mode", "fsk", "--baud", "9600", "--decoder", "fast", "--bank", "diverse",
                  "--window-seconds", "6", "--hop-seconds", "3", "--top-timing", "16"],
                 attempt, "native", root, timeout=1800)
        source_checker(plan["sources"])
        state["native_status"] = "process_finished_pending_validation"
        state["status"] = "baseline_failed"
        executor([str(PYTHON), str(BASELINE), "--input", str(wav), "--output", str(attempt / "baseline"),
                  "--observation", str(row["id"]), "--mode", "fsk-g3ruh", "--baud", "9600",
                  "--representation", "fm_demodulated", "--timeout", "900"],
                 attempt, "baseline", root, timeout=940)
        source_checker(plan["sources"])
        state["baseline_status"] = "process_finished_pending_validation"
        state["status"] = "score_failed"
        validate_execution(attempt, info.frames, info.samplerate)
        # This exact scorer sees reference bytes; decoder commands never receive them.
        comparison = compare_one(attempt, attempt / "native.json", attempt / "baseline/result.json")
        state.update(status="complete", native_status="complete", baseline_status="complete", comparison=comparison)
        new = set(comparison["counts"]["native_new_vs_archive"]["pdus"])
        state["extra_candidates"] = [{**frame, "secondary_replay": "pending", "origin_audit": "pending",
                                      "independent_binascii_original_fcs": True}
                                     for frame in read(attempt / "native.json")["frames"] if frame["payload_hex"] in new]
    except Pause:
        raise  # Uncommitted attempt is retained; resume may use a new numbered attempt.
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as error:
        state["error"] = {"type": type(error).__name__, "message": str(error)}
        if state["status"] == "native_failed":
            state["native_status"] = "failed"
        if state["status"] == "baseline_failed":
            state["baseline_status"] = "failed"
        if state["status"] == "ogg_unavailable":
            state["ogg_status"] = "unavailable"
    return finish(attempt, state)


def committed_state(attempt: Path, plan_sha: str) -> dict | None:
    if not (attempt / "commit.json").exists():
        return None
    verify_expected(read(attempt / "commit.json")["final"], attempt / "final.json")
    final = read(attempt / "final.json")
    if final["plan_sha256"] != plan_sha:
        raise Pause("completed attempt belongs to another frozen plan")
    for item in final["artifacts"]:
        verify(item)
    if final["status"] == "complete":
        cleanup_wav(attempt)
    return final


def set_summary(values: set[str]) -> dict:
    return {"count": len(values), "bytes": sum(len(bytes.fromhex(value)) for value in values),
            "pdus": sorted(values)}


def aggregate(ids: list[int], states: list[dict]) -> dict:
    if len({state["observation_id"] for state in states}) != len(states) or any(state["observation_id"] not in ids for state in states):
        raise ValueError("duplicate or foreign observation in aggregate")
    complete = [state for state in states if state["status"] == "complete"]
    if any(not state["references"]["complete"] for state in complete):
        raise ValueError("incomplete references cannot enter complete archive-absence scoring")
    counts = Counter(state["status"] for state in states)
    counts["not_processed"] = len(ids) - len(states)
    keys = ("archive", "baseline_same_audio", "native_same_audio", "native_only_vs_baseline",
            "baseline_only_vs_native", "native_new_vs_archive", "baseline_new_vs_archive",
            "native_new_vs_archive_and_baseline", "native_matching_archive", "archive_not_reproduced_by_audio")
    per_observation, global_local_unions = {}, {}
    for key in keys:
        values = [state["comparison"]["counts"][key] for state in complete]
        per_observation[key] = {metric: sum(value[metric] for value in values) for metric in ("count", "bytes")}
        global_local_unions[key] = set_summary({pdu for value in values for pdu in value["pdus"]})
    all_references_complete = len(states) == len(ids) and all(state["references"]["complete"] for state in states)
    archive_all = {item["payload_hex"] for state in states for item in state["references"]["objects"] if item["strict_ax25_ui_structure"]}
    native_all = set(global_local_unions["native_same_audio"]["pdus"])
    global_novel = set_summary(native_all - archive_all) if all_references_complete else None
    return {"schema": "ogg-archive-campaign-summary-v1", "recorded_utc": utc(),
            "frozen_count": len(ids), "statuses": dict(counts), "complete_comparisons": len(complete),
            "ogg_available": sum(state.get("ogg_status") == "available" for state in states),
            "references_complete": sum(state["references"]["complete"] for state in states),
            "observations_with_native_new_vs_archive": sum(bool(state["comparison"]["counts"]["native_new_vs_archive"]["count"]) for state in complete),
            "per_observation_deduplicated_totals": per_observation,
            "global_union_of_observation_local_sets": global_local_unions,
            "globally_absent_from_entire_cohort_archive": global_novel,
            "entire_cohort_references_complete": all_references_complete,
            "archive_absence_claims_unavailable_for_incomplete_reference_rows": True,
            "extra_frames_are_unattributed_candidates_pending_secondary_replay": True,
            "no_false_accept_rate_confidence_claim": True,
            "publication_ready": False, "deployment_ready": False,
            "observations": states}


def run(root: Path, *, retry_failed=False) -> dict:
    with locked(root):
        frozen = read(root / "freeze.json")
        verify_expected(frozen["plan"], root / "plan.json")
        verify_expected(frozen["cohort"], root / "cohort.json")
        plan, cohort = read(root / "plan.json"), read(root / "cohort.json")
        if plan["contract"] != CONTRACT or plan["limits"] != LIMITS or cohort["contract"] != CONTRACT:
            raise Pause("unsupported or changed frozen campaign contract")
        if cohort["observations"] != choose(cohort["observations"]) or cohort["ids"] != [row["id"] for row in cohort["observations"]]:
            raise Pause("inconsistent frozen cohort")
        for page in cohort["pages"]:
            verify(page["identity"])
        verify_sources(plan["sources"])
        ensure_no_old_processes(root)
        states = []
        pause = None

        def checkpoint(pause_reason=None, finished=False):
            summary = aggregate(cohort["ids"], states)
            summary.update(plan_sha256=frozen["plan"]["sha256"], pause=pause_reason,
                           actual_coverage=cohort["actual_coverage"],
                           complete_campaign=finished and pause_reason is None and len(states) == len(cohort["ids"]))
            if not finished:
                # Per-ID progress must not quadratically duplicate all retained
                # frame bytes/provenance. Full sets remain in observation commits
                # and the single terminal/pause aggregate, never these checkpoints.
                summary.pop("observations")
                summary["schema"] = "ogg-archive-compact-progress-v1"
                summary["observation_commits"] = [identity(Path(state["attempt"]) / "commit.json") for state in states]
                for value in summary["global_union_of_observation_local_sets"].values():
                    value.pop("pdus")
                if summary["globally_absent_from_entire_cohort_archive"] is not None:
                    summary["globally_absent_from_entire_cohort_archive"].pop("pdus")
            durable_json(root / "summaries" / (uuid.uuid4().hex + ".json"), summary)
            return summary

        try:
            for row in cohort["observations"]:
                attempts = sorted((root / "observations" / str(row["id"])).glob("attempt-*"))
                final = committed_state(attempts[-1], frozen["plan"]["sha256"]) if attempts else None
                if final is None or (retry_failed and final["status"] != "complete"):
                    disk_guard(root)
                    ensure_no_old_processes(root)
                    final = one_observation(root, row, plan, frozen["plan"]["sha256"])
                states.append(final)
                checkpoint()
        except (Pause, KeyboardInterrupt) as error:
            pause = str(error) or "interrupted"
        return checkpoint(pause, finished=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("prepare", "run"):
        command = commands.add_parser(name)
        command.add_argument("--root", required=True, type=Path)
        if name == "run":
            command.add_argument("--retry-failed", action="store_true",
                                 help="new numbered attempt, never replace a frozen cohort member")
    args = parser.parse_args()
    if Path(sys.executable).absolute() != PYTHON:
        raise SystemExit("Use the frozen project .venv/bin/python executable")
    # External TERM reaches the same cleanup path as an interactive interruption.
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    root = args.root.absolute()
    result = prepare(root) if args.command == "prepare" else run(root, retry_failed=args.retry_failed)
    print(json.dumps({key: result[key] for key in ("selected_count", "complete_comparisons", "pause") if key in result}))
    return 2 if result.get("pause") else 0


if __name__ == "__main__":
    raise SystemExit(main())
