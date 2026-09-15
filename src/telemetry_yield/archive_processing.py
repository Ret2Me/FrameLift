"""Resumable processing coordinator for the explicit G3RUH archive lane.

This module intentionally selects only catalogue rows whose mode is exactly
``FSK AX.25 G3RUH``.  It makes no framing inference from generic FSK, GFSK, or
GMSK labels.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any

from .archive_download import sha256_file
from .canonical import canonical_json


EXPLICIT_MODE = "FSK AX.25 G3RUH"
SAMPLE_RATE_HZ = 57_600
SCALE_DIVISOR = 32_768
CONVERSION_SCHEMA_VERSION = "sigmf-ci16-to-shared-cf32-v1"
CHECKPOINT_SCHEMA_VERSION = "archive-g3ruh-processing-checkpoint-v1"
MANIFEST_SCHEMA_VERSION = "archive-g3ruh-processing-manifest-v1"


@dataclass(frozen=True, slots=True)
class ProcessingCandidate:
    observation_id: int
    expected_ci16_size_bytes: int | None
    mode: str
    satellite_id: str | None = None

    @property
    def iq_filename(self) -> str:
        return f"observation_{self.observation_id}.iq"


@dataclass(frozen=True, slots=True)
class ConversionArtifact:
    ci16_path: Path
    ci16_size_bytes: int
    ci16_sha256: str
    cf32_path: Path
    cf32_size_bytes: int
    cf32_sha256: str
    manifest_path: Path
    complex_samples: int
    reused: bool


@dataclass(frozen=True, slots=True)
class ProcessingResult:
    observation_id: int
    status: str
    ci16_path: str | None
    ci16_size_bytes: int | None
    ci16_sha256: str | None
    cf32_path: str | None
    cf32_size_bytes: int | None
    cf32_sha256: str | None
    conversion_manifest_path: str | None
    conversion_status: str | None
    decoder_status: str | None
    decode_output_path: str | None
    decode_output_size_bytes: int | None
    decode_output_sha256: str | None
    crc_valid_frame_count: int | None
    error: str | None = None

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def select_explicit_g3ruh_zero_iq(
    catalogue: Mapping[str, Any],
) -> tuple[ProcessingCandidate, ...]:
    """Select only zero-frame IQ rows with an explicit G3RUH catalogue mode."""

    observations = catalogue.get("observations")
    if not isinstance(observations, Sequence) or isinstance(
        observations, (str, bytes, bytearray, memoryview)
    ):
        raise ValueError("observations must be an array")
    selected: list[ProcessingCandidate] = []
    seen: set[int] = set()
    for raw in observations:
        if not isinstance(raw, Mapping):
            raise ValueError("observation must be an object")
        if raw.get("frames_recovered") is not False or raw.get("mode") != EXPLICIT_MODE:
            continue
        links = raw.get("links")
        iq_url = links.get("iq") if isinstance(links, Mapping) else None
        if not isinstance(iq_url, str) or not iq_url.strip():
            continue
        observation_id = raw.get("observation_id")
        if not isinstance(observation_id, int) or isinstance(observation_id, bool):
            raise ValueError("observation_id must be an integer")
        if observation_id in seen:
            raise ValueError("observation_id values must be unique")
        seen.add(observation_id)
        artifacts = raw.get("artifacts")
        iq_artifact = artifacts.get("iq") if isinstance(artifacts, Mapping) else None
        raw_size = iq_artifact.get("size_bytes") if isinstance(iq_artifact, Mapping) else None
        expected_size = (
            raw_size
            if isinstance(raw_size, int)
            and not isinstance(raw_size, bool)
            and raw_size > 0
            else None
        )
        satellite_id = raw.get("satellite_id")
        selected.append(
            ProcessingCandidate(
                observation_id=observation_id,
                expected_ci16_size_bytes=expected_size,
                mode=EXPLICIT_MODE,
                satellite_id=(
                    satellite_id.strip()
                    if isinstance(satellite_id, str) and satellite_id.strip()
                    else None
                ),
            )
        )
    return tuple(sorted(selected, key=lambda item: item.observation_id))


def _candidate_input_paths(
    candidate: ProcessingCandidate,
    campaign_root: Path,
    legacy_root: Path | None,
) -> tuple[Path, ...]:
    paths = [
        campaign_root / candidate.iq_filename,
        campaign_root / f"obs-{candidate.observation_id}" / candidate.iq_filename,
    ]
    if legacy_root is not None:
        paths.append(
            legacy_root / f"obs-{candidate.observation_id}" / candidate.iq_filename
        )
    unique: list[Path] = []
    for path in paths:
        if path not in unique:
            unique.append(path)
    return tuple(unique)


def locate_ci16_input(
    candidate: ProcessingCandidate,
    campaign_root: Path,
    *,
    legacy_root: Path | None = None,
) -> Path:
    """Resolve flat, campaign ``obs-ID/``, then legacy ``obs-ID/`` layouts."""

    invalid: list[str] = []
    for path in _candidate_input_paths(candidate, campaign_root, legacy_root):
        if not path.is_file():
            continue
        size = path.stat().st_size
        if size == 0 or size % 4:
            invalid.append(f"{path}: invalid ci16 size {size}")
            continue
        expected = candidate.expected_ci16_size_bytes
        if expected is not None and size != expected:
            invalid.append(f"{path}: size {size} != catalogue {expected}")
            continue
        return path
    if invalid:
        raise ValueError("; ".join(invalid))
    raise FileNotFoundError(f"no local IQ for observation {candidate.observation_id}")


def _write_json_atomic(path: Path, document: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    os.replace(temporary, path)


def _conversion_manifest_document(
    *,
    ci16_path: Path,
    ci16_size: int,
    ci16_sha256: str,
    cf32_path: Path,
    cf32_size: int,
    cf32_sha256: str,
    complex_samples: int,
    chunk_complex_samples: int,
) -> dict[str, object]:
    return {
        "schema_version": CONVERSION_SCHEMA_VERSION,
        "input": {
            "data_path": str(ci16_path.resolve()),
            "datatype": "ci16_le",
            "sample_rate_hz": SAMPLE_RATE_HZ,
            "size_bytes": ci16_size,
            "complex_samples": complex_samples,
            "sha256": ci16_sha256,
        },
        "conversion": {
            "rule": (
                "For every interleaved signed int16 pair (I,Q), emit "
                "complex64(float32(I)/32768 + j*float32(Q)/32768)"
            ),
            "component_order": "I then Q",
            "scale_divisor": SCALE_DIVISOR,
            "chunk_complex_samples": chunk_complex_samples,
            "filtering": False,
            "resampling": False,
            "clipping_or_declipping": False,
            "normalization_from_signal_statistics": False,
        },
        "output": {
            "path": str(cf32_path.resolve()),
            "datatype": "cf32_le (<c8)",
            "sample_rate_hz": SAMPLE_RATE_HZ,
            "size_bytes": cf32_size,
            "complex_samples": complex_samples,
            "duration_seconds": complex_samples / SAMPLE_RATE_HZ,
            "sha256": cf32_sha256,
        },
    }


def validate_conversion_artifact(
    manifest_path: Path,
    *,
    ci16_path: Path,
    ci16_sha256: str,
    cf32_path: Path,
) -> ConversionArtifact | None:
    if not manifest_path.is_file() or not cf32_path.is_file():
        return None
    try:
        document = json.loads(manifest_path.read_text(encoding="utf-8"))
        input_record = document["input"]
        output_record = document["output"]
        if document.get("schema_version") != CONVERSION_SCHEMA_VERSION:
            return None
        ci16_size = ci16_path.stat().st_size
        complex_samples = ci16_size // 4
        expected_cf32_size = complex_samples * 8
        if (
            input_record.get("datatype") != "ci16_le"
            or input_record.get("sample_rate_hz") != SAMPLE_RATE_HZ
            or input_record.get("size_bytes") != ci16_size
            or input_record.get("complex_samples") != complex_samples
            or input_record.get("sha256") != ci16_sha256
            or output_record.get("datatype") != "cf32_le (<c8)"
            or output_record.get("sample_rate_hz") != SAMPLE_RATE_HZ
            or output_record.get("size_bytes") != expected_cf32_size
            or output_record.get("complex_samples") != complex_samples
            or cf32_path.stat().st_size != expected_cf32_size
        ):
            return None
        output_sha = sha256_file(cf32_path)
        if output_record.get("sha256") != output_sha:
            return None
    except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError):
        return None
    return ConversionArtifact(
        ci16_path=ci16_path,
        ci16_size_bytes=ci16_size,
        ci16_sha256=ci16_sha256,
        cf32_path=cf32_path,
        cf32_size_bytes=expected_cf32_size,
        cf32_sha256=output_sha,
        manifest_path=manifest_path,
        complex_samples=complex_samples,
        reused=True,
    )


def convert_ci16_to_cf32(
    ci16_path: Path,
    cf32_path: Path,
    manifest_path: Path,
    *,
    chunk_complex_samples: int = 1_048_576,
) -> ConversionArtifact:
    """Convert confirmed interleaved CI16-LE to normalized CF32-LE atomically."""

    if chunk_complex_samples <= 0:
        raise ValueError("chunk_complex_samples must be positive")
    ci16_size = ci16_path.stat().st_size
    if ci16_size == 0 or ci16_size % 4:
        raise ValueError("CI16 input must be non-empty and divisible by four bytes")
    ci16_hash = sha256_file(ci16_path)
    existing = validate_conversion_artifact(
        manifest_path,
        ci16_path=ci16_path,
        ci16_sha256=ci16_hash,
        cf32_path=cf32_path,
    )
    if existing is not None:
        return existing

    # NumPy is also a dependency of the invoked phase-first decoder.  Keep the
    # import local so inventory/selection remains usable without DSP extras.
    import numpy as np

    cf32_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = cf32_path.with_name(cf32_path.name + ".part")
    chunk_bytes = chunk_complex_samples * 4
    with ci16_path.open("rb") as source, temporary.open("wb") as output:
        while block := source.read(chunk_bytes):
            if len(block) % 4:
                raise ValueError("short final CI16 complex sample")
            components = np.frombuffer(block, dtype="<i2")
            normalized = components.astype("<f4")
            normalized /= np.float32(SCALE_DIVISOR)
            output.write(normalized.tobytes(order="C"))
        output.flush()
        os.fsync(output.fileno())

    complex_samples = ci16_size // 4
    expected_output_size = complex_samples * 8
    if temporary.stat().st_size != expected_output_size:
        raise RuntimeError("CF32 conversion size mismatch")
    os.replace(temporary, cf32_path)
    cf32_hash = sha256_file(cf32_path)
    document = _conversion_manifest_document(
        ci16_path=ci16_path,
        ci16_size=ci16_size,
        ci16_sha256=ci16_hash,
        cf32_path=cf32_path,
        cf32_size=expected_output_size,
        cf32_sha256=cf32_hash,
        complex_samples=complex_samples,
        chunk_complex_samples=chunk_complex_samples,
    )
    _write_json_atomic(manifest_path, document)
    return ConversionArtifact(
        ci16_path=ci16_path,
        ci16_size_bytes=ci16_size,
        ci16_sha256=ci16_hash,
        cf32_path=cf32_path,
        cf32_size_bytes=expected_output_size,
        cf32_sha256=cf32_hash,
        manifest_path=manifest_path,
        complex_samples=complex_samples,
        reused=False,
    )


def _possible_work_dirs(
    observation_id: int, campaign_root: Path, legacy_root: Path | None
) -> tuple[Path, ...]:
    values = [campaign_root / f"obs-{observation_id}"]
    if legacy_root is not None:
        values.append(legacy_root / f"obs-{observation_id}")
    unique: list[Path] = []
    for value in values:
        if value not in unique:
            unique.append(value)
    return tuple(unique)


def prepare_conversion(
    candidate: ProcessingCandidate,
    campaign_root: Path,
    *,
    legacy_root: Path | None = None,
    chunk_complex_samples: int = 1_048_576,
) -> ConversionArtifact:
    ci16_path = locate_ci16_input(
        candidate, campaign_root, legacy_root=legacy_root
    )
    ci16_hash = sha256_file(ci16_path)
    for work_dir in _possible_work_dirs(
        candidate.observation_id, campaign_root, legacy_root
    ):
        reused = validate_conversion_artifact(
            work_dir / "conversion-manifest.json",
            ci16_path=ci16_path,
            ci16_sha256=ci16_hash,
            cf32_path=work_dir / f"observation_{candidate.observation_id}.cf32",
        )
        if reused is not None:
            return reused

    nested_name = f"obs-{candidate.observation_id}"
    work_dir = (
        ci16_path.parent
        if ci16_path.parent.name == nested_name
        else campaign_root / nested_name
    )
    return convert_ci16_to_cf32(
        ci16_path,
        work_dir / f"observation_{candidate.observation_id}.cf32",
        work_dir / "conversion-manifest.json",
        chunk_complex_samples=chunk_complex_samples,
    )


def validate_decode_artifact(
    path: Path,
    *,
    observation_id: int,
    input_sha256: str,
) -> tuple[int, str] | None:
    if not path.is_file():
        return None
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
        frame_count = document.get("unique_crc_valid_frame_count")
        frames = document.get("unique_crc_valid_frames")
        if (
            document.get("stage") != "decode-only"
            or document.get("observation_id") != observation_id
            or document.get("input_sha256") != input_sha256
            or not isinstance(frame_count, int)
            or isinstance(frame_count, bool)
            or frame_count < 0
            or not isinstance(frames, list)
            or len(frames) != frame_count
        ):
            return None
        digest = sha256_file(path)
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    return frame_count, digest


def run_phase_first_decoder(
    *,
    python_executable: Path,
    decoder_script: Path,
    input_path: Path,
    output_path: Path,
    observation_id: int,
    workers: int,
    timeout_seconds: float,
    runner: Callable[..., Any] = subprocess.run,
) -> tuple[int, str]:
    """Invoke the existing decoder with an explicit argv and ``shell=False``."""

    if not 1 <= workers <= 4:
        raise ValueError("workers must be in 1..4")
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    partial_output = output_path.with_name(output_path.name + ".part")
    stdout_path = output_path.with_name(output_path.name + ".stdout.log")
    stderr_path = output_path.with_name(output_path.name + ".stderr.log")
    argv = [
        str(python_executable),
        str(decoder_script),
        "decode",
        "--input",
        str(input_path),
        "--output",
        str(partial_output),
        "--workers",
        str(workers),
        "--observation-id",
        str(observation_id),
    ]
    # Never accept a stale artifact left by a previously failed subprocess.
    partial_output.unlink(missing_ok=True)
    with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
        completed = runner(
            argv,
            shell=False,
            check=False,
            stdout=stdout,
            stderr=stderr,
            timeout=timeout_seconds,
        )
    if completed.returncode != 0:
        raise RuntimeError(f"phase-first decoder exited {completed.returncode}")
    input_hash = sha256_file(input_path)
    validated = validate_decode_artifact(
        partial_output,
        observation_id=observation_id,
        input_sha256=input_hash,
    )
    if validated is None:
        raise RuntimeError("phase-first decoder did not produce a valid artifact")
    os.replace(partial_output, output_path)
    final = validate_decode_artifact(
        output_path,
        observation_id=observation_id,
        input_sha256=input_hash,
    )
    if final is None:
        raise RuntimeError("promoted phase-first artifact failed validation")
    return final


def _append_checkpoint(path: Path, result: ProcessingResult) -> dict[str, object]:
    event: dict[str, object] = {
        "schema_version": CHECKPOINT_SCHEMA_VERSION,
        "recorded_at": datetime.now(UTC).isoformat(),
        **result.as_dict(),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, sort_keys=True, separators=(",", ":")) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    return event


def load_processing_events(path: Path) -> tuple[dict[str, object], ...]:
    if not path.exists():
        return ()
    lines = path.read_text(encoding="utf-8").splitlines()
    events: list[dict[str, object]] = []
    for number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            if number == len(lines):
                break
            raise ValueError(f"malformed processing checkpoint line {number}")
        if not isinstance(event, dict):
            raise ValueError(f"processing checkpoint line {number} is not an object")
        events.append(event)
    return tuple(events)


def _write_processing_manifest(
    path: Path,
    *,
    candidates: Sequence[ProcessingCandidate],
    checkpoint_path: Path,
) -> dict[str, object]:
    selected_ids = {item.observation_id for item in candidates}
    latest: dict[int, dict[str, object]] = {}
    for event in load_processing_events(checkpoint_path):
        observation_id = event.get("observation_id")
        if isinstance(observation_id, int) and observation_id in selected_ids:
            latest[observation_id] = event
    statuses = Counter(str(event.get("status")) for event in latest.values())
    document: dict[str, object] = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "updated_at": datetime.now(UTC).isoformat(),
        "selection_contract": {
            "frames_recovered": False,
            "mode_exact": EXPLICIT_MODE,
            "generic_fsk_implies_ax25": False,
            "input_datatype": "ci16_le interleaved I,Q",
            "sample_rate_hz": SAMPLE_RATE_HZ,
        },
        "candidate_count": len(candidates),
        "latest_result_count": len(latest),
        "status_counts": dict(sorted(statuses.items())),
        "results": [latest[key] for key in sorted(latest)],
    }
    _write_json_atomic(path, document)
    return document


def process_candidate(
    candidate: ProcessingCandidate,
    campaign_root: Path,
    *,
    legacy_root: Path | None,
    python_executable: Path,
    decoder_script: Path,
    workers: int,
    timeout_seconds: float,
    chunk_complex_samples: int,
    runner: Callable[..., Any],
) -> ProcessingResult:
    conversion = prepare_conversion(
        candidate,
        campaign_root,
        legacy_root=legacy_root,
        chunk_complex_samples=chunk_complex_samples,
    )
    output_path = conversion.cf32_path.parent / "phase-first-full-blind.json"
    existing = validate_decode_artifact(
        output_path,
        observation_id=candidate.observation_id,
        input_sha256=conversion.cf32_sha256,
    )
    if existing is not None:
        frame_count, output_hash = existing
        decoder_status = "reused"
    else:
        frame_count, output_hash = run_phase_first_decoder(
            python_executable=python_executable,
            decoder_script=decoder_script,
            input_path=conversion.cf32_path,
            output_path=output_path,
            observation_id=candidate.observation_id,
            workers=workers,
            timeout_seconds=timeout_seconds,
            runner=runner,
        )
        decoder_status = "decoded"
    skipped = conversion.reused and decoder_status == "reused"
    return ProcessingResult(
        observation_id=candidate.observation_id,
        status="skipped_complete" if skipped else "processed",
        ci16_path=str(conversion.ci16_path),
        ci16_size_bytes=conversion.ci16_size_bytes,
        ci16_sha256=conversion.ci16_sha256,
        cf32_path=str(conversion.cf32_path),
        cf32_size_bytes=conversion.cf32_size_bytes,
        cf32_sha256=conversion.cf32_sha256,
        conversion_manifest_path=str(conversion.manifest_path),
        conversion_status="reused" if conversion.reused else "converted",
        decoder_status=decoder_status,
        decode_output_path=str(output_path),
        decode_output_size_bytes=output_path.stat().st_size,
        decode_output_sha256=output_hash,
        crc_valid_frame_count=frame_count,
    )


def run_processing_campaign(
    candidates: Sequence[ProcessingCandidate],
    campaign_root: Path,
    *,
    legacy_root: Path | None,
    decoder_script: Path,
    checkpoint_path: Path,
    manifest_path: Path,
    python_executable: Path = Path(sys.executable),
    workers: int = 4,
    timeout_seconds: float = 7_200.0,
    chunk_complex_samples: int = 1_048_576,
    runner: Callable[..., Any] = subprocess.run,
) -> tuple[ProcessingResult, ...]:
    """Process every selected row, durably recording errors and continuing."""

    results: list[ProcessingResult] = []
    for candidate in candidates:
        try:
            result = process_candidate(
                candidate,
                campaign_root,
                legacy_root=legacy_root,
                python_executable=python_executable,
                decoder_script=decoder_script,
                workers=workers,
                timeout_seconds=timeout_seconds,
                chunk_complex_samples=chunk_complex_samples,
                runner=runner,
            )
        except Exception as error:
            result = ProcessingResult(
                observation_id=candidate.observation_id,
                status="error",
                ci16_path=None,
                ci16_size_bytes=None,
                ci16_sha256=None,
                cf32_path=None,
                cf32_size_bytes=None,
                cf32_sha256=None,
                conversion_manifest_path=None,
                conversion_status=None,
                decoder_status=None,
                decode_output_path=None,
                decode_output_size_bytes=None,
                decode_output_sha256=None,
                crc_valid_frame_count=None,
                error=f"{type(error).__name__}: {error}",
            )
        _append_checkpoint(checkpoint_path, result)
        _write_processing_manifest(
            manifest_path,
            candidates=candidates,
            checkpoint_path=checkpoint_path,
        )
        results.append(result)
    return tuple(results)


def _manifest_nonnegative_int(value: object, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"{name} must be a nonnegative integer")
    return value


def _manifest_sha256(value: object, name: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError(f"{name} must be a lowercase SHA-256")
    return value


def _processing_result_semantics(result: Mapping[str, Any]) -> tuple[object, ...]:
    status = result.get("status")
    if status in {"processed", "skipped_complete"}:
        output_path = result.get("decode_output_path")
        if not isinstance(output_path, str) or not output_path:
            raise ValueError("successful result must have decode_output_path")
        return (
            "success",
            _manifest_sha256(
                result.get("decode_output_sha256"), "decode_output_sha256"
            ),
            _manifest_nonnegative_int(
                result.get("crc_valid_frame_count"), "crc_valid_frame_count"
            ),
        )
    if status == "error":
        return ("error",)
    raise ValueError(f"unsupported processing result status: {status!r}")


def merge_processing_manifest_documents(
    catalogue: Mapping[str, Any],
    manifests: Sequence[Mapping[str, Any]],
    *,
    source_manifests: Sequence[Mapping[str, str]] = (),
) -> dict[str, object]:
    """Merge deterministic shards without rerunning any decoder.

    Overlap is accepted only when both records have the same success/error
    semantics and, for successful records, the same decode output SHA-256 and
    CRC-valid candidate count.
    """

    if not manifests:
        raise ValueError("at least one processing manifest is required")
    expected_ids = tuple(
        candidate.observation_id
        for candidate in select_explicit_g3ruh_zero_iq(catalogue)
    )
    expected_set = set(expected_ids)
    if not expected_ids:
        raise ValueError("catalogue contains no explicit G3RUH zero-frame candidates")

    first_contract: dict[str, Any] | None = None
    merged: dict[int, dict[str, Any]] = {}
    semantics: dict[int, tuple[object, ...]] = {}
    for manifest_index, raw_manifest in enumerate(manifests):
        if not isinstance(raw_manifest, Mapping):
            raise ValueError(f"processing manifest {manifest_index} must be an object")
        if raw_manifest.get("schema_version") != MANIFEST_SCHEMA_VERSION:
            raise ValueError(
                f"processing manifest {manifest_index} has an unsupported schema"
            )
        raw_contract = raw_manifest.get("selection_contract")
        if not isinstance(raw_contract, Mapping):
            raise ValueError("processing selection_contract must be an object")
        contract = dict(raw_contract)
        if (
            contract.get("frames_recovered") is not False
            or contract.get("mode_exact") != EXPLICIT_MODE
            or contract.get("generic_fsk_implies_ax25") is not False
            or contract.get("input_datatype") != "ci16_le interleaved I,Q"
            or contract.get("sample_rate_hz") != SAMPLE_RATE_HZ
        ):
            raise ValueError("processing manifest selection contract is incompatible")
        if first_contract is None:
            first_contract = contract
        elif canonical_json(contract) != canonical_json(first_contract):
            raise ValueError("processing manifest selection contracts differ")

        candidate_count = _manifest_nonnegative_int(
            raw_manifest.get("candidate_count"), "manifest candidate_count"
        )
        latest_count = _manifest_nonnegative_int(
            raw_manifest.get("latest_result_count"), "manifest latest_result_count"
        )
        if candidate_count > len(expected_ids) or latest_count > candidate_count:
            raise ValueError("processing manifest counters exceed expected campaign size")
        raw_results = raw_manifest.get("results")
        if not isinstance(raw_results, Sequence) or isinstance(
            raw_results, (str, bytes, bytearray, memoryview)
        ):
            raise ValueError("processing manifest results must be an array")
        if len(raw_results) != latest_count:
            raise ValueError("processing manifest result count disagrees")
        raw_status_counts = raw_manifest.get("status_counts")
        if not isinstance(raw_status_counts, Mapping):
            raise ValueError("processing manifest status_counts must be an object")

        local_ids: set[int] = set()
        local_statuses: Counter[str] = Counter()
        for result_index, raw_result in enumerate(raw_results):
            if not isinstance(raw_result, Mapping):
                raise ValueError(
                    f"processing result {manifest_index}:{result_index} must be an object"
                )
            result = dict(raw_result)
            observation_id = result.get("observation_id")
            if (
                not isinstance(observation_id, int)
                or isinstance(observation_id, bool)
                or observation_id not in expected_set
            ):
                raise ValueError("processing result observation ID is outside expected set")
            if observation_id in local_ids:
                raise ValueError("duplicate observation ID within processing manifest")
            local_ids.add(observation_id)
            semantic = _processing_result_semantics(result)
            status = str(result["status"])
            local_statuses[status] += 1
            previous = semantics.get(observation_id)
            if previous is not None and previous != semantic:
                raise ValueError(
                    f"conflicting overlap for observation {observation_id}: "
                    "status/output SHA/count semantics differ"
                )
            if previous is None:
                semantics[observation_id] = semantic
                merged[observation_id] = result
            else:
                # Select a stable representative independent of shard order.
                merged[observation_id] = min(
                    (merged[observation_id], result), key=canonical_json
                )

        declared_statuses = {
            str(key): _manifest_nonnegative_int(
                value, f"manifest status_counts.{key}"
            )
            for key, value in raw_status_counts.items()
        }
        if dict(local_statuses) != declared_statuses:
            raise ValueError("processing manifest status_counts disagree with results")
        declared_complete = raw_manifest.get("complete")
        if declared_complete is not None:
            if not isinstance(declared_complete, bool):
                raise ValueError("processing manifest complete must be boolean")
            calculated = latest_count == candidate_count and all(
                result.get("status") in {"processed", "skipped_complete"}
                for result in raw_results
            )
            if declared_complete != calculated:
                raise ValueError("processing manifest complete flag disagrees")

    assert first_contract is not None
    sorted_results = [merged[observation_id] for observation_id in sorted(merged)]
    statuses = Counter(str(result["status"]) for result in sorted_results)
    missing_ids = sorted(expected_set - set(merged))
    complete = not missing_ids and all(
        value[0] == "success" for value in semantics.values()
    )

    normalized_sources: list[dict[str, str]] = []
    for index, raw_source in enumerate(source_manifests):
        if not isinstance(raw_source, Mapping):
            raise ValueError(f"source manifest {index} must be an object")
        path = raw_source.get("path")
        if not isinstance(path, str) or not path:
            raise ValueError("source manifest path must be text")
        digest = _manifest_sha256(raw_source.get("sha256"), "source manifest sha256")
        normalized_sources.append({"path": path, "sha256": digest})
    normalized_sources.sort(key=lambda item: (item["path"], item["sha256"]))

    return {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "complete": complete,
        "selection_contract": first_contract,
        "candidate_count": len(expected_ids),
        "expected_observation_ids": list(expected_ids),
        "latest_result_count": len(sorted_results),
        "missing_observation_ids": missing_ids,
        "status_counts": dict(sorted(statuses.items())),
        "source_manifests": normalized_sources,
        "results": sorted_results,
    }


def merge_processing_manifest_paths(
    catalogue_path: Path,
    manifest_paths: Sequence[Path],
    output_path: Path,
) -> dict[str, object]:
    """Load shard snapshots once and atomically write their merged manifest."""

    if not manifest_paths:
        raise ValueError("at least one --manifest path is required")
    catalogue = json.loads(catalogue_path.read_text(encoding="utf-8"))
    if not isinstance(catalogue, Mapping):
        raise ValueError("catalogue must be an object")
    documents: list[Mapping[str, Any]] = []
    sources: list[dict[str, str]] = []
    seen_paths: set[Path] = set()
    for path in manifest_paths:
        resolved = path.resolve()
        if resolved in seen_paths:
            continue
        seen_paths.add(resolved)
        source_bytes = path.read_bytes()
        document = json.loads(source_bytes)
        if not isinstance(document, Mapping):
            raise ValueError(f"processing manifest must be an object: {path}")
        documents.append(document)
        sources.append(
            {
                "path": str(resolved),
                "sha256": hashlib.sha256(source_bytes).hexdigest(),
            }
        )
    merged = merge_processing_manifest_documents(
        catalogue, documents, source_manifests=sources
    )
    _write_json_atomic(output_path, merged)
    return merged
