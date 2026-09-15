"""Resumable, artifact-preserving gr-satellites batch execution."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import tempfile
from typing import Any, Mapping, Sequence

from .canonical import canonical_json
from .external_backends import CaptureSegment, ExternalByteCandidate
from .gr_satellites_backend import GrSatellitesBackend, GrSatellitesProfile
from .satyaml_registry import SatYamlRegistry


@dataclass(frozen=True, slots=True)
class GrSatellitesBatchJob:
    observation_id: int
    satellite_id: str
    frequency_hz: float
    iq_url: str
    iq_path: Path
    expected_size_bytes: int | None
    expected_sha256: str | None
    profile: GrSatellitesProfile
    route_document: Mapping[str, Any]

    def __post_init__(self) -> None:
        if (
            isinstance(self.observation_id, bool)
            or not isinstance(self.observation_id, int)
            or self.observation_id < 1
        ):
            raise ValueError("observation_id must be a positive integer")
        for name in ("satellite_id", "iq_url"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be a non-empty string")
        if (
            isinstance(self.frequency_hz, bool)
            or not isinstance(self.frequency_hz, (int, float))
            or not math.isfinite(float(self.frequency_hz))
            or self.frequency_hz <= 0
        ):
            raise ValueError("frequency_hz must be finite and positive")
        if self.expected_size_bytes is not None and (
            isinstance(self.expected_size_bytes, bool)
            or not isinstance(self.expected_size_bytes, int)
            or self.expected_size_bytes <= 0
        ):
            raise ValueError("expected_size_bytes must be positive or None")
        if self.expected_sha256 is not None:
            digest = self.expected_sha256.casefold()
            if len(digest) != 64 or any(
                item not in "0123456789abcdef" for item in digest
            ):
                raise ValueError("expected_sha256 must be a SHA-256 hex digest")
            object.__setattr__(self, "expected_sha256", digest)
        if not isinstance(self.profile, GrSatellitesProfile):
            raise TypeError("profile must be a GrSatellitesProfile")
        canonical_json(dict(self.route_document))
        object.__setattr__(self, "frequency_hz", float(self.frequency_hz))
        object.__setattr__(self, "iq_path", Path(self.iq_path))
        object.__setattr__(self, "route_document", dict(self.route_document))


@dataclass(frozen=True, slots=True)
class GrSatellitesBatchConfig:
    executable: str
    executable_version: str
    artifact_root: Path
    sample_format: str
    sample_rate_hz: float
    timeout_seconds: float
    max_output_bytes: int
    max_kiss_bytes: int
    retry_failures: bool = False

    def __post_init__(self) -> None:
        for name in ("executable", "executable_version", "sample_format"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be a non-empty string")
        for name in ("sample_rate_hz", "timeout_seconds"):
            value = getattr(self, name)
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(float(value))
                or value <= 0
            ):
                raise ValueError(f"{name} must be finite and positive")
            object.__setattr__(self, name, float(value))
        for name in ("max_output_bytes", "max_kiss_bytes"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{name} must be a positive integer")
        if not isinstance(self.retry_failures, bool):
            raise TypeError("retry_failures must be a boolean")
        object.__setattr__(self, "artifact_root", Path(self.artifact_root))

    def to_dict(self) -> dict[str, Any]:
        return {
            "executable": self.executable,
            "executable_version": self.executable_version,
            "artifact_root": str(self.artifact_root),
            "sample_format": self.sample_format,
            "sample_rate_hz": self.sample_rate_hz,
            "timeout_seconds": self.timeout_seconds,
            "max_output_bytes": self.max_output_bytes,
            "max_kiss_bytes": self.max_kiss_bytes,
            "retry_failures": self.retry_failures,
        }


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _atomic_write_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary_path = Path(temporary)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def _atomic_write_json(path: Path, value: Mapping[str, Any]) -> None:
    payload = (
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    ).encode("utf-8")
    _atomic_write_bytes(path, payload)


def _attempt_fingerprint(
    job: GrSatellitesBatchJob, config: GrSatellitesBatchConfig
) -> str:
    document = {
        "observation_id": job.observation_id,
        "satellite_id": job.satellite_id,
        "frequency_hz": job.frequency_hz,
        "iq_path": str(job.iq_path),
        "expected_size_bytes": job.expected_size_bytes,
        "expected_sha256": job.expected_sha256,
        "profile": job.profile.to_dict(),
        "route": dict(job.route_document),
        "backend": {
            "executable": config.executable,
            "executable_version": config.executable_version,
            "sample_format": config.sample_format,
            "sample_rate_hz": config.sample_rate_hz,
            "timeout_seconds": config.timeout_seconds,
            "max_output_bytes": config.max_output_bytes,
            "max_kiss_bytes": config.max_kiss_bytes,
        },
    }
    return hashlib.sha256(canonical_json(document).encode("utf-8")).hexdigest()


def _campaign_fingerprint(
    jobs: Sequence[GrSatellitesBatchJob], config: GrSatellitesBatchConfig
) -> str:
    document = {
        "attempts": [
            {
                "observation_id": job.observation_id,
                "fingerprint": _attempt_fingerprint(job, config),
            }
            for job in sorted(jobs, key=lambda item: item.observation_id)
        ],
        "config": config.to_dict(),
    }
    return hashlib.sha256(canonical_json(document).encode("utf-8")).hexdigest()


def _candidate_document(
    candidate: ExternalByteCandidate, *, index: int, path: Path
) -> dict[str, Any]:
    provenance = candidate.provenance
    return {
        "candidate_index": index,
        "classification": "unvalidated_pdu_candidate",
        "validated": False,
        "validation_layers": [],
        "payload_path": str(path),
        "payload_size_bytes": len(candidate.payload),
        "payload_sha256": _sha256_bytes(candidate.payload),
        "provenance": {
            "backend_id": provenance.backend_id,
            "backend_version": provenance.backend_version,
            "capture_path": provenance.capture_path,
            "sample_format": provenance.sample_format,
            "sample_rate_hz": provenance.sample_rate_hz,
            "start_sample": provenance.start_sample,
            "sample_count": provenance.sample_count,
            "capture_hints": dict(provenance.capture_hints),
            "argv": list(provenance.argv),
            "parser": dict(provenance.parser),
        },
    }


def _terminal_result(path: Path, fingerprint: str, retry_failures: bool) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    if document.get("attempt_fingerprint") != fingerprint:
        return None
    status = document.get("status")
    if status == "completed":
        return document
    if status == "backend_failure" and not retry_failures:
        return document
    return None


def _manifest(
    *,
    campaign_fingerprint: str,
    config: GrSatellitesBatchConfig,
    jobs: Sequence[GrSatellitesBatchJob],
    records: Mapping[int, Mapping[str, Any]],
) -> dict[str, Any]:
    ordered = [
        dict(records.get(job.observation_id, {
            "observation_id": job.observation_id,
            "status": "pending",
            "candidate_count": 0,
        }))
        for job in sorted(jobs, key=lambda item: item.observation_id)
    ]
    status_counts = Counter(item["status"] for item in ordered)
    return {
        "schema_version": "gr-satellites-batch-v1",
        "campaign_fingerprint": campaign_fingerprint,
        "candidate_classification": "unvalidated_pdu_candidate",
        "config": config.to_dict(),
        "job_count": len(jobs),
        "status_counts": dict(sorted(status_counts.items())),
        "unvalidated_candidate_count": sum(
            int(item.get("candidate_count", 0)) for item in ordered
        ),
        "jobs": ordered,
    }


def run_gr_satellites_batch(
    jobs: Sequence[GrSatellitesBatchJob],
    config: GrSatellitesBatchConfig,
    *,
    max_jobs: int | None = None,
) -> dict[str, Any]:
    """Run jobs sequentially, checkpointing before and after every attempt."""

    ordered_jobs = tuple(sorted(jobs, key=lambda item: item.observation_id))
    if len({item.observation_id for item in ordered_jobs}) != len(ordered_jobs):
        raise ValueError("batch jobs must have unique observation IDs")
    if max_jobs is not None and (
        isinstance(max_jobs, bool) or not isinstance(max_jobs, int) or max_jobs < 0
    ):
        raise ValueError("max_jobs must be a non-negative integer or None")
    config.artifact_root.mkdir(parents=True, exist_ok=True)
    manifest_path = config.artifact_root / "manifest.json"
    campaign_fingerprint = _campaign_fingerprint(ordered_jobs, config)
    records: dict[int, Mapping[str, Any]] = {}
    attempts_started = 0

    for job in ordered_jobs:
        fingerprint = _attempt_fingerprint(job, config)
        attempt_directory = (
            config.artifact_root
            / f"obs-{job.observation_id}"
            / f"attempt-{fingerprint[:16]}"
        )
        result_path = attempt_directory / "result.json"
        terminal = _terminal_result(result_path, fingerprint, config.retry_failures)
        if terminal is not None:
            records[job.observation_id] = {
                "observation_id": job.observation_id,
                "status": terminal["status"],
                "candidate_count": terminal.get("candidate_count", 0),
                "attempt_fingerprint": fingerprint,
                "artifact_directory": str(attempt_directory),
                "resumed": True,
            }
            continue
        if max_jobs is not None and attempts_started >= max_jobs:
            continue
        attempts_started += 1
        attempt_directory.mkdir(parents=True, exist_ok=True)
        records[job.observation_id] = {
            "observation_id": job.observation_id,
            "status": "running",
            "candidate_count": 0,
            "attempt_fingerprint": fingerprint,
            "artifact_directory": str(attempt_directory),
            "resumed": False,
        }
        _atomic_write_json(
            manifest_path,
            _manifest(
                campaign_fingerprint=campaign_fingerprint,
                config=config,
                jobs=ordered_jobs,
                records=records,
            ),
        )

        input_problem: str | None = None
        actual_sha256: str | None = None
        actual_size: int | None = None
        if not job.iq_path.is_file():
            input_problem = "IQ path is not a regular file"
        elif job.expected_size_bytes is None or job.expected_sha256 is None:
            input_problem = "download manifest lacks expected size or SHA-256"
        else:
            try:
                actual_size = job.iq_path.stat().st_size
                if actual_size != job.expected_size_bytes:
                    input_problem = (
                        f"IQ size mismatch: expected {job.expected_size_bytes}, "
                        f"got {actual_size}"
                    )
                elif config.sample_format == "ci16_le" and actual_size % 4:
                    input_problem = (
                        "CI16 IQ size is not a whole number of complex samples"
                    )
                elif config.sample_format == "cf32_le" and actual_size % 8:
                    input_problem = (
                        "CF32 IQ size is not a whole number of complex samples"
                    )
                else:
                    actual_sha256 = _sha256_file(job.iq_path)
                    if actual_sha256 != job.expected_sha256:
                        input_problem = (
                            "IQ SHA-256 does not match the download manifest"
                        )
            except OSError as exc:
                input_problem = f"IQ verification failed: {type(exc).__name__}: {exc}"

        if input_problem is not None:
            result_document = {
                "schema_version": "gr-satellites-attempt-v1",
                "observation_id": job.observation_id,
                "attempt_fingerprint": fingerprint,
                "status": "input_error",
                "error": input_problem,
                "candidate_count": 0,
                "candidates": [],
                "validated_candidate_count": 0,
                "input": {
                    "path": str(job.iq_path),
                    "expected_size_bytes": job.expected_size_bytes,
                    "actual_size_bytes": actual_size,
                    "expected_sha256": job.expected_sha256,
                    "actual_sha256": actual_sha256,
                },
            }
            _atomic_write_json(result_path, result_document)
            records[job.observation_id] = {
                **records[job.observation_id],
                "status": "input_error",
                "error": input_problem,
            }
            _atomic_write_json(
                manifest_path,
                _manifest(
                    campaign_fingerprint=campaign_fingerprint,
                    config=config,
                    jobs=ordered_jobs,
                    records=records,
                ),
            )
            continue

        backend = GrSatellitesBackend(
            executable=config.executable,
            executable_version=config.executable_version,
            profile=job.profile,
            timeout_seconds=config.timeout_seconds,
            max_output_bytes=config.max_output_bytes,
            max_kiss_bytes=config.max_kiss_bytes,
        )
        backend_result = backend.run(
            CaptureSegment(
                path=job.iq_path,
                sample_format=config.sample_format,
                sample_rate_hz=config.sample_rate_hz,
                hints={
                    "observation_id": job.observation_id,
                    "satellite_id": job.satellite_id,
                    "frequency_hz": job.frequency_hz,
                },
            )
        )
        stdout_path = attempt_directory / "stdout.bin"
        stderr_path = attempt_directory / "stderr.bin"
        _atomic_write_bytes(stdout_path, backend_result.stdout)
        _atomic_write_bytes(stderr_path, backend_result.stderr)
        candidate_documents: list[dict[str, Any]] = []
        for index, candidate in enumerate(backend_result.candidates):
            payload_path = (
                attempt_directory
                / "candidates"
                / f"{index:04d}-{_sha256_bytes(candidate.payload)}.bin"
            )
            _atomic_write_bytes(payload_path, candidate.payload)
            candidate_documents.append(
                _candidate_document(candidate, index=index, path=payload_path)
            )
        status = "completed" if backend_result.succeeded else "backend_failure"
        failure = None
        if backend_result.failure is not None:
            failure = {
                "code": backend_result.failure.code,
                "message": backend_result.failure.message,
                "returncode": backend_result.failure.returncode,
            }
        result_document = {
            "schema_version": "gr-satellites-attempt-v1",
            "observation_id": job.observation_id,
            "attempt_fingerprint": fingerprint,
            "status": status,
            "backend_status": backend_result.status,
            "backend_failure": failure,
            "backend_argv": list(backend_result.argv),
            "returncode": backend_result.returncode,
            "candidate_classification": "unvalidated_pdu_candidate",
            "candidate_count": len(candidate_documents),
            "validated_candidate_count": 0,
            "candidates": candidate_documents,
            "stdout": {
                "path": str(stdout_path),
                "size_bytes": len(backend_result.stdout),
                "sha256": _sha256_bytes(backend_result.stdout),
            },
            "stderr": {
                "path": str(stderr_path),
                "size_bytes": len(backend_result.stderr),
                "sha256": _sha256_bytes(backend_result.stderr),
            },
            "input": {
                "path": str(job.iq_path),
                "size_bytes": actual_size,
                "sha256": actual_sha256,
                "sample_format": config.sample_format,
                "sample_rate_hz": config.sample_rate_hz,
            },
            "profile": job.profile.to_dict(),
            "route": dict(job.route_document),
        }
        _atomic_write_json(result_path, result_document)
        records[job.observation_id] = {
            **records[job.observation_id],
            "status": status,
            "candidate_count": len(candidate_documents),
            "failure_code": failure["code"] if failure else None,
        }
        _atomic_write_json(
            manifest_path,
            _manifest(
                campaign_fingerprint=campaign_fingerprint,
                config=config,
                jobs=ordered_jobs,
                records=records,
            ),
        )

    final_manifest = _manifest(
        campaign_fingerprint=campaign_fingerprint,
        config=config,
        jobs=ordered_jobs,
        records=records,
    )
    _atomic_write_json(manifest_path, final_manifest)
    return final_manifest


def load_routed_batch_jobs(
    routing_report_path: str | Path,
    download_manifest_path: str | Path,
    registry: SatYamlRegistry,
) -> tuple[GrSatellitesBatchJob, ...]:
    """Join routed observations to verified local download records and profiles."""

    routing = json.loads(Path(routing_report_path).read_text(encoding="utf-8"))
    downloads = json.loads(Path(download_manifest_path).read_text(encoding="utf-8"))
    if not isinstance(routing, Mapping) or not isinstance(routing.get("routes"), list):
        raise ValueError("routing report must contain routes")
    if not isinstance(downloads, Mapping) or not isinstance(
        downloads.get("results"), list
    ):
        raise ValueError("download manifest must contain results")
    by_observation: dict[object, Mapping[str, Any]] = {}
    for item in downloads["results"]:
        if not isinstance(item, Mapping):
            continue
        observation_id = item.get("observation_id")
        if observation_id in by_observation:
            raise ValueError(f"duplicate download observation ID {observation_id}")
        by_observation[observation_id] = item
    jobs: list[GrSatellitesBatchJob] = []
    for route in routing["routes"]:
        if not isinstance(route, Mapping) or route.get("status") != "routed":
            continue
        observation_id = route.get("observation_id")
        download = by_observation.get(observation_id, {})
        norad_id = route.get("norad_id")
        if not isinstance(norad_id, int):
            raise ValueError(f"routed observation {observation_id} has no NORAD")
        profile = registry.get_by_norad(norad_id)
        if profile is None:
            raise ValueError(
                f"routed observation {observation_id} has no unambiguous profile"
            )
        declared_profile = route.get("capability_profile")
        if not isinstance(declared_profile, Mapping) or declared_profile.get(
            "selector"
        ) != profile.selector:
            raise ValueError(
                f"routed observation {observation_id} profile changed since planning"
            )
        fallback_path = (
            Path(download_manifest_path).parent / f"observation_{observation_id}.iq"
        )
        jobs.append(
            GrSatellitesBatchJob(
                observation_id=observation_id,
                satellite_id=route.get("satellite_id"),
                frequency_hz=route.get("frequency_hz"),
                iq_url=route.get("iq_url"),
                iq_path=Path(download.get("path", fallback_path)),
                expected_size_bytes=download.get("size_bytes"),
                expected_sha256=download.get("sha256"),
                profile=profile,
                route_document=route,
            )
        )
    return tuple(sorted(jobs, key=lambda item: item.observation_id))
