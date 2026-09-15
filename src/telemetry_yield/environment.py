"""Reproducible environment inventory without importing heavy dependencies."""

from __future__ import annotations

import importlib.metadata
import json
import os
import platform
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path


PACKAGES = (
    "httpx",
    "hypothesis",
    "numpy",
    "pyarrow",
    "pytest",
    "PyYAML",
    "scipy",
    "sgp4",
    "sigmf",
    "skyfield",
    "soundfile",
    "tenacity",
)
TOOLS = (
    "atest",
    "ffmpeg",
    "sox",
    "gnuradio-config-info",
    "direwolf",
    "gr_satellites",
    "satdump",
    "psql",
)


def _tool_version(tool: str, path: str) -> str | None:
    try:
        if tool in {"atest", "satdump"}:
            package = "direwolf" if tool == "atest" else "satdump"
            completed = subprocess.run(
                ["dpkg-query", "-W", "-f=${Version}", package],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
        else:
            completed = subprocess.run(
                [path, "--version"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
    except subprocess.TimeoutExpired:
        return "unavailable:version-command-timeout"
    except OSError as exc:
        return f"unavailable:{type(exc).__name__}"
    output = completed.stdout or completed.stderr
    return output.splitlines()[0][:200] if output else None


def collect_environment() -> dict[str, object]:
    packages: dict[str, str | None] = {}
    for package in PACKAGES:
        try:
            packages[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            packages[package] = None
    tools: dict[str, dict[str, str | None]] = {}
    for tool in TOOLS:
        path = shutil.which(tool)
        version = _tool_version(tool, path) if path else None
        tools[tool] = {"path": path, "version": version}
    try:
        memory_bytes = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
    except (AttributeError, OSError, ValueError):
        memory_bytes = None
    disk = shutil.disk_usage(Path.cwd())
    return {
        "captured_at": datetime.now(UTC).isoformat(),
        "platform": platform.platform(),
        "python": sys.version,
        "cpu_count": os.cpu_count(),
        "memory_bytes": memory_bytes,
        "disk": {
            "path": str(Path.cwd()),
            "total_bytes": disk.total,
            "free_bytes": disk.free,
        },
        "packages": packages,
        "tools": tools,
    }


def write_environment(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(collect_environment(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
