"""Versioned, fail-closed file API for the blind clipping-robust FSK receiver.

The scientific decoder deliberately remains a pure bounded-window routine.  This
module supplies the production boundary around it: content identity checks,
constant-memory candidate discovery backed by bounded scratch storage, stable
JSON serialization, and atomic publication of the result.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3
import stat
import sys
import tempfile
from typing import Any, Mapping

from .canonical import canonical_json
from .clipping_robust_fsk import (
    BlindPhaseFskConfig,
    BlindPhaseFskResult,
    PhaseWindowCandidate,
    decode_clipping_robust_ax25_ci16,
    group_ax25_frame_consensus,
)


API_VERSION = "telemetry-yield-blind-phase-fsk-file-api-v2"
SCHEMA_VERSION = "blind-phase-fsk-file-result-v2"
SELECTOR_VERSION = "sqlite-exact-statistics-v1"
DEMODULATOR_ID = "blind-clipping-robust-phase-fsk"
DEMODULATOR_VERSION = "1.1.0"
PROTOCOL_ADAPTER_ID = "hdlc-crc16-x25-ax25-ui-plain-and-g3ruh"
SUPPORTED_SELECTION_MODULATION_LABELS = (
    "FSK",
    "FSK AX25 G3RUH",
    "GFSK",
    "GMSK",
)
_SHA256_CHUNK_BYTES = 1024 * 1024
_SQLITE_CACHE_KIB = 4096
_SQLITE_ROW_BUDGET_BYTES = 1024
_ANALYSIS_ARRAY_BUDGET_BYTES_PER_SAMPLE = 64
_MAX_ANALYSIS_WINDOW_SAMPLES = 1_000_000
_MAX_COMPLETED_RESULT_BYTES = 128 * 1024 * 1024
_MAX_FRAME_DETECTIONS = 20_000
_MAX_DECODER_WINDOW_SAMPLES = 5_000_000
_MAX_CANDIDATE_WINDOWS = 4096
_MAX_TIMING_BANK_SIZE = 65_536
_MAX_ATTEMPTS_PER_START = 2_000_000
_MAX_FRONTEND_TIMING_WINDOW_WORK = 1_000_000
_MAX_TOTAL_REPAIR_ATTEMPTS = 51_200_000
_MAX_TOTAL_REPAIR_OUTPUTS = 512
_MAX_RECEIVER_PATHS_PER_WINDOW = 20_000
_MAX_HYPOTHESIS_VALUES = 64
_MAX_DECODER_WORKING_SET_MODEL_BYTES = 512 * 1024 * 1024
_FIXED_RUNTIME_WORKING_SET_RESERVE_BYTES = 192 * 1024 * 1024
_DECODER_BYTES_PER_SOURCE_SAMPLE = 128
_TRANSIENT_BYTES_PER_DECIMATED_SYMBOL = 64
_RETAINED_BYTES_PER_SOFT_SYMBOL = 40
_BYTES_PER_RECEIVER_PATH = 512
_BYTES_PER_CANDIDATE_ERROR_UNIT = 256
_BYTES_PER_SEED_ERROR_UNIT = 64
_BYTES_PER_SEED_WEIGHT_STREAM = 384
_BYTES_PER_COMBINATION_ATTEMPT = 512
_RESERVED_SHORT_LIST_SEARCH = {
    "short_least_reliable_symbols": 64,
    "short_maximum_flips": 2,
    "short_maximum_attempts": 20_000,
}
_MAX_SEARCH_LIMITS = {
    "maximum_regions_per_start": 64,
    "deep_least_reliable_symbols": 512,
    "deep_maximum_flips": 16,
    "maximum_map_seed_states": 4096,
}
_MAX_REPAIR_LIMITS = {
    "repair_path_maximum_attempts": 100_000,
    "repair_path_maximum_unique_frames": 8,
    "repair_region_maximum_attempts": 50_000,
    "repair_event_maximum_attempts": 400_000,
    "repair_event_maximum_unique_frames": 8,
    "repair_window_maximum_events": 16,
    "repair_window_maximum_attempts": 1_600_000,
    "repair_window_maximum_unique_frames": 16,
    "event_cluster_tolerance_symbols": 16,
}


class _OutputExistsError(ValueError):
    pass


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_document(value: Mapping[str, Any]) -> str:
    return _sha256_bytes(canonical_json(value).encode("utf-8"))


def _valid_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value.casefold())
    )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb", buffering=0) as stream:
        for chunk in iter(lambda: stream.read(_SHA256_CHUNK_BYTES), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    payload = (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")
    if len(payload) > _MAX_COMPLETED_RESULT_BYTES:
        raise ValueError("blind receiver result exceeds the output memory bound")
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
        _publish_no_clobber(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def _fsync_directory(path: Path) -> None:
    directory_descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory_descriptor)
    finally:
        os.close(directory_descriptor)


def _publish_no_clobber(temporary_path: Path, output_path: Path) -> None:
    try:
        os.link(temporary_path, output_path)
    except FileExistsError as error:
        raise _OutputExistsError(
            "blind receiver output was created concurrently"
        ) from error
    try:
        _fsync_directory(output_path.parent)
    except OSError:
        try:
            temporary_status = temporary_path.stat(follow_symlinks=False)
            output_status = output_path.stat(follow_symlinks=False)
            if (
                temporary_status.st_dev == output_status.st_dev
                and temporary_status.st_ino == output_status.st_ino
            ):
                output_path.unlink()
                try:
                    _fsync_directory(output_path.parent)
                except OSError:
                    pass
        except FileNotFoundError:
            pass
        raise


def _identity(stat_result: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        stat_result.st_dev,
        stat_result.st_ino,
        stat_result.st_size,
        stat_result.st_mtime_ns,
        stat_result.st_ctime_ns,
    )


def _attempt_fingerprint(
    *,
    source_size: int,
    source_sha256: str,
    config: "BlindPhaseFskRunConfig",
    implementation: Mapping[str, Any],
) -> str:
    return _sha256_document(
        {
            "api_version": API_VERSION,
            "source_sha256": source_sha256,
            "source_size_bytes": source_size,
            "config_sha256": config.fingerprint,
            "implementation": implementation,
        }
    )


def _load_matching_completed_result(
    path: Path,
    *,
    source_size: int,
    source_sha256: str,
    config: "BlindPhaseFskRunConfig",
    implementation: Mapping[str, Any],
) -> dict[str, Any]:
    status = path.lstat()
    if stat.S_ISLNK(status.st_mode) or not stat.S_ISREG(status.st_mode):
        raise ValueError("existing blind receiver output must be a regular non-symlink file")
    if not 0 < status.st_size <= _MAX_COMPLETED_RESULT_BYTES:
        raise ValueError("existing blind receiver output exceeds the verification bound")
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("existing blind receiver output is not valid JSON") from error
    if not isinstance(document, dict):
        raise ValueError("existing blind receiver output is not a JSON object")
    recorded_result_hash = document.get("result_payload_sha256")
    if not _valid_sha256(recorded_result_hash):
        raise ValueError("existing blind receiver output has no valid result hash")
    unhashed = {
        key: value for key, value in document.items() if key != "result_payload_sha256"
    }
    if _sha256_document(unhashed) != recorded_result_hash:
        raise ValueError("existing blind receiver output failed its result hash")
    expected_attempt = _attempt_fingerprint(
        source_size=source_size,
        source_sha256=source_sha256,
        config=config,
        implementation=implementation,
    )
    source = document.get("source")
    if (
        document.get("api_version") != API_VERSION
        or document.get("schema_version") != SCHEMA_VERSION
        or document.get("status") != "completed"
        or document.get("attempt_fingerprint") != expected_attempt
        or document.get("config_sha256") != config.fingerprint
        or document.get("effective_config") != config.to_dict()
        or document.get("implementation") != implementation
        or not isinstance(source, dict)
        or source.get("size_bytes") != source_size
        or source.get("sha256") != source_sha256
    ):
        raise ValueError("existing blind receiver output belongs to another attempt")
    return document


def _validated_regular_ci16(path: Path) -> os.stat_result:
    try:
        link_status = path.lstat()
    except FileNotFoundError as error:
        raise ValueError("IQ input does not exist") from error
    if stat.S_ISLNK(link_status.st_mode):
        raise ValueError("IQ input must not be a symbolic link")
    if not stat.S_ISREG(link_status.st_mode):
        raise ValueError("IQ input must be a regular file")
    if link_status.st_size <= 0 or link_status.st_size % 4:
        raise ValueError("CI16-LE input must contain complete non-empty I/Q pairs")
    return link_status


@dataclass(frozen=True, slots=True)
class BlindPhaseFskRunConfig:
    """Content-bound execution contract for one blind file attempt."""

    receiver: BlindPhaseFskConfig
    expected_input_size_bytes: int
    expected_input_sha256: str
    maximum_scratch_bytes: int = 512 * 1024 * 1024

    def __post_init__(self) -> None:
        if not isinstance(self.receiver, BlindPhaseFskConfig):
            raise TypeError("receiver must be a BlindPhaseFskConfig")
        if (
            isinstance(self.expected_input_size_bytes, bool)
            or not isinstance(self.expected_input_size_bytes, int)
            or self.expected_input_size_bytes <= 0
            or self.expected_input_size_bytes % 4
        ):
            raise ValueError(
                "expected_input_size_bytes must be a positive multiple of four"
            )
        if not _valid_sha256(self.expected_input_sha256):
            raise ValueError("expected_input_sha256 must be a SHA-256 digest")
        object.__setattr__(
            self, "expected_input_sha256", self.expected_input_sha256.casefold()
        )
        if (
            isinstance(self.maximum_scratch_bytes, bool)
            or not isinstance(self.maximum_scratch_bytes, int)
            or self.maximum_scratch_bytes < 1024 * 1024
        ):
            raise ValueError("maximum_scratch_bytes must be at least 1 MiB")
        _validate_receiver_bounds(self.receiver)

    def to_dict(self) -> dict[str, Any]:
        return json.loads(json.dumps(asdict(self)))

    @property
    def fingerprint(self) -> str:
        return _sha256_document(self.to_dict())


def _validate_receiver_bounds(config: BlindPhaseFskConfig) -> None:
    integers = {
        "sample_rate_hz": config.sample_rate_hz,
        "candidate_window_limit": config.candidate_window_limit,
        "phase_bins": config.phase_bins,
        "short_search_timing_hypotheses": config.short_search_timing_hypotheses,
        "deep_search_timing_hypotheses": config.deep_search_timing_hypotheses,
        "minimum_consecutive_flags": config.minimum_consecutive_flags,
        "minimum_frame_body_bits": config.minimum_frame_body_bits,
        "short_least_reliable_symbols": config.short_least_reliable_symbols,
        "short_maximum_flips": config.short_maximum_flips,
        "short_maximum_attempts": config.short_maximum_attempts,
        "deep_least_reliable_symbols": config.deep_least_reliable_symbols,
        "deep_maximum_flips": config.deep_maximum_flips,
        "deep_maximum_attempts": config.deep_maximum_attempts,
        "maximum_regions_per_start": config.maximum_regions_per_start,
        "repair_path_maximum_attempts": config.repair_path_maximum_attempts,
        "repair_path_maximum_unique_frames": (
            config.repair_path_maximum_unique_frames
        ),
        "repair_region_maximum_attempts": config.repair_region_maximum_attempts,
        "repair_event_maximum_attempts": config.repair_event_maximum_attempts,
        "repair_event_maximum_unique_frames": (
            config.repair_event_maximum_unique_frames
        ),
        "repair_window_maximum_events": config.repair_window_maximum_events,
        "repair_window_maximum_attempts": config.repair_window_maximum_attempts,
        "repair_window_maximum_unique_frames": (
            config.repair_window_maximum_unique_frames
        ),
        "event_cluster_tolerance_symbols": (
            config.event_cluster_tolerance_symbols
        ),
        "candidate_neighbor_radius": config.candidate_neighbor_radius,
        "maximum_map_seed_states": config.maximum_map_seed_states,
        "maximum_receiver_paths_per_window": (
            config.maximum_receiver_paths_per_window
        ),
        "maximum_frame_detections": config.maximum_frame_detections,
    }
    if any(
        isinstance(value, bool) or not isinstance(value, int)
        for value in integers.values()
    ):
        raise ValueError("receiver integer bounds must be integers, not booleans")
    if not isinstance(config.phase_difference_lags, tuple) or any(
        isinstance(value, bool) or not isinstance(value, int)
        for value in config.phase_difference_lags
    ):
        raise ValueError("phase_difference_lags must be a tuple of integers")
    if not isinstance(config.constant_radius_factors, tuple) or not isinstance(
        config.rate_errors_ppm, tuple
    ):
        raise ValueError("receiver hypothesis banks must be tuples")
    if (
        len(config.constant_radius_factors) > _MAX_HYPOTHESIS_VALUES
        or len(config.phase_difference_lags) > _MAX_HYPOTHESIS_VALUES
        or len(config.rate_errors_ppm) > _MAX_HYPOTHESIS_VALUES
    ):
        raise ValueError("receiver hypothesis list exceeds the production bound")
    if (
        not isinstance(config.descramble_modes, tuple)
        or not config.descramble_modes
        or any(type(mode) is not bool for mode in config.descramble_modes)
        or len(set(config.descramble_modes)) != len(config.descramble_modes)
    ):
        raise ValueError("descramble_modes must be a tuple of unique booleans")
    numeric = (
        config.baudrate,
        config.analysis_window_seconds,
        config.decoder_window_seconds,
        config.decoder_window_lead_seconds,
        config.window_nms_seconds,
        config.error_unit_penalty,
        *config.constant_radius_factors,
        *config.rate_errors_ppm,
    )
    if any(
        isinstance(value, bool) or not isinstance(value, (int, float))
        for value in numeric
    ):
        raise ValueError("receiver numeric bounds must be numbers, not booleans")
    if any(not math.isfinite(value) for value in numeric):
        raise ValueError("receiver configuration values must be finite")
    if config.baudrate > config.sample_rate_hz:
        raise ValueError("baudrate must not exceed sample_rate_hz")
    source_samples_per_symbol = config.sample_rate_hz / config.baudrate
    if not math.isfinite(source_samples_per_symbol) or source_samples_per_symbol < 2:
        raise ValueError("sample rate must provide at least two source samples per symbol")
    decimation = max(1, math.floor(source_samples_per_symbol / 2.0))
    actual_samples_per_symbol = source_samples_per_symbol / decimation
    if not math.isfinite(actual_samples_per_symbol) or actual_samples_per_symbol < 2:
        raise ValueError("decimation produced an invalid actual samples-per-symbol value")
    if any(abs(value) > 500_000 for value in config.rate_errors_ppm):
        raise ValueError("rate-error hypotheses exceed the production bound")
    if max(config.constant_radius_factors) > 100:
        raise ValueError("constant-radius factors exceed the production bound")
    if config.candidate_neighbor_radius > 2:
        raise ValueError("candidate_neighbor_radius exceeds the production bound")
    for name, required in _RESERVED_SHORT_LIST_SEARCH.items():
        if getattr(config, name) != required:
            raise ValueError(
                f"{name} is reserved in file API v2 and must equal {required}"
            )
    if config.deep_least_reliable_symbols < 1:
        raise ValueError("deep_least_reliable_symbols must be positive")
    if (
        min(
            config.short_least_reliable_symbols,
            config.short_maximum_flips,
            config.deep_least_reliable_symbols,
            config.deep_maximum_flips,
            config.candidate_neighbor_radius,
        )
        < 0
        or min(config.short_maximum_attempts, config.deep_maximum_attempts) < 1
        or config.error_unit_penalty < 0
    ):
        raise ValueError("list-search budgets must be non-negative and non-empty")
    analysis_samples = int(
        round(config.analysis_window_seconds * config.sample_rate_hz)
    )
    decoder_samples = int(
        round(config.decoder_window_seconds * config.sample_rate_hz)
    )
    if analysis_samples < 2:
        raise ValueError("analysis window must contain at least two samples")
    if (
        analysis_samples > _MAX_ANALYSIS_WINDOW_SAMPLES
        or analysis_samples > decoder_samples
    ):
        raise ValueError("analysis window exceeds the production memory envelope")
    if decoder_samples > _MAX_DECODER_WINDOW_SAMPLES:
        raise ValueError("decoder window exceeds the production memory envelope")
    decoder_memory = _decoder_working_set_model_bytes(config)
    if decoder_memory > _MAX_DECODER_WORKING_SET_MODEL_BYTES:
        raise ValueError(
            "decoder working-set model exceeds the production bound"
        )
    if max(config.phase_difference_lags) >= decoder_samples - 115:
        raise ValueError("phase-difference lag is too large for the decoder window")
    minimum_step = actual_samples_per_symbol * (
        1.0 + min(config.rate_errors_ppm) * 1e-6
    )
    maximum_step = actual_samples_per_symbol * (
        1.0 + max(config.rate_errors_ppm) * 1e-6
    )
    if minimum_step <= 1.0:
        raise ValueError("rate error produces an invalid symbol step")
    filtered_numerator = (
        decoder_samples - max(config.phase_difference_lags) - 114
    )
    filtered_samples = max(
        0,
        (filtered_numerator + decimation - 1) // decimation,
    )
    phase_output_samples = filtered_samples - 1_023
    if filtered_samples <= 1_024 or phase_output_samples < 256:
        raise ValueError("decoder window is too short after phase discrimination")
    shared_symbols = int(
        (phase_output_samples - maximum_step) // maximum_step
    ) - 64
    if shared_symbols < 128:
        raise ValueError("decoder window is too short for the timing guard")
    if config.candidate_window_limit > _MAX_CANDIDATE_WINDOWS:
        raise ValueError("candidate_window_limit exceeds the production bound")
    bank_size = len(set(config.rate_errors_ppm)) * config.phase_bins
    if bank_size > _MAX_TIMING_BANK_SIZE:
        raise ValueError("timing bank exceeds the production bound")
    if max(config.short_maximum_attempts, config.deep_maximum_attempts) > (
        _MAX_ATTEMPTS_PER_START
    ):
        raise ValueError("per-start candidate attempts exceed the production bound")
    for name, maximum in _MAX_SEARCH_LIMITS.items():
        if getattr(config, name) > maximum:
            raise ValueError(f"{name} exceeds the production bound")
    if config.maximum_receiver_paths_per_window > _MAX_RECEIVER_PATHS_PER_WINDOW:
        raise ValueError(
            "maximum_receiver_paths_per_window exceeds the production bound"
        )
    if config.maximum_frame_detections > _MAX_FRAME_DETECTIONS:
        raise ValueError("maximum_frame_detections exceeds the production bound")
    for name, maximum in _MAX_REPAIR_LIMITS.items():
        if getattr(config, name) > maximum:
            raise ValueError(f"{name} exceeds the production bound")
    work_bound = (
        config.candidate_window_limit
        * len(config.constant_radius_factors)
        * len(config.phase_difference_lags)
        * len(config.descramble_modes)
        * bank_size
    )
    if work_bound > _MAX_FRONTEND_TIMING_WINDOW_WORK:
        raise ValueError("frontend timing/window work exceeds the production bound")
    total_repair_attempt_bound = (
        config.candidate_window_limit
        * config.repair_window_maximum_attempts
    )
    if total_repair_attempt_bound > _MAX_TOTAL_REPAIR_ATTEMPTS:
        raise ValueError("total repair attempt work exceeds the production bound")
    total_repair_output_bound = (
        config.candidate_window_limit
        * config.repair_window_maximum_unique_frames
    )
    if total_repair_output_bound > _MAX_TOTAL_REPAIR_OUTPUTS:
        raise ValueError("total repair output count exceeds the production bound")


def _decoder_working_set_model_bytes(
    config: BlindPhaseFskConfig,
) -> int:
    """Conservative preflight model calibrated above the v4.2 measured RSS."""

    decoder_samples = int(
        round(config.decoder_window_seconds * config.sample_rate_hz)
    )
    source_samples_per_symbol = config.sample_rate_hz / config.baudrate
    decimation = max(1, math.floor(source_samples_per_symbol / 2.0))
    decimated_symbols = int(math.ceil(decoder_samples / decimation))
    retained_timing_streams = (
        len(config.constant_radius_factors)
        * len(config.phase_difference_lags)
        * max(
            config.short_search_timing_hypotheses,
            config.deep_search_timing_hypotheses,
        )
    )
    candidate_error_units = config.deep_least_reliable_symbols * (
        1 + 2 * config.candidate_neighbor_radius
    )
    seed_states = (
        config.maximum_map_seed_states
        + config.repair_path_maximum_unique_frames
    )
    seed_weight_streams = seed_states * (config.deep_maximum_flips + 1)
    path_attempts = min(
        config.deep_maximum_attempts,
        config.repair_path_maximum_attempts,
    )
    return (
        _FIXED_RUNTIME_WORKING_SET_RESERVE_BYTES
        + decoder_samples * _DECODER_BYTES_PER_SOURCE_SAMPLE
        + decimated_symbols
        * (
            _TRANSIENT_BYTES_PER_DECIMATED_SYMBOL
            + _RETAINED_BYTES_PER_SOFT_SYMBOL * retained_timing_streams
        )
        + config.maximum_receiver_paths_per_window * _BYTES_PER_RECEIVER_PATH
        + candidate_error_units * _BYTES_PER_CANDIDATE_ERROR_UNIT
        + seed_states * candidate_error_units * _BYTES_PER_SEED_ERROR_UNIT
        + seed_weight_streams * _BYTES_PER_SEED_WEIGHT_STREAM
        + path_attempts * _BYTES_PER_COMBINATION_ATTEMPT
    )


def _sqlite_lower_median(
    connection: sqlite3.Connection,
    *,
    column: str,
    row_count: int,
) -> float:
    lower_count = max(1, int(math.ceil(row_count * 0.25)))
    offsets = (
        (lower_count // 2,)
        if lower_count % 2
        else (lower_count // 2 - 1, lower_count // 2)
    )
    values = []
    for offset in offsets:
        row = connection.execute(
            f"SELECT {column} FROM window_stats ORDER BY {column} LIMIT 1 OFFSET ?",
            (offset,),
        ).fetchone()
        if row is None:
            raise ValueError("candidate-statistics store is incomplete")
        values.append(float(row[0]))
    result = sum(values) / len(values)
    if not math.isfinite(result) or result <= 0:
        raise ValueError("signal statistics have no finite positive baseline")
    return result


def _scratch_usage_bytes(database_path: Path) -> int:
    return sum(
        candidate.stat().st_size
        for candidate in (
            database_path,
            database_path.with_name(database_path.name + "-wal"),
            database_path.with_name(database_path.name + "-shm"),
            database_path.with_name(database_path.name + "-journal"),
        )
        if candidate.exists()
    )


def select_ci16_phase_windows_streaming(
    path: str | Path,
    *,
    config: BlindPhaseFskConfig,
    scratch_directory: str | Path | None = None,
    maximum_scratch_bytes: int = 512 * 1024 * 1024,
) -> tuple[tuple[PhaseWindowCandidate, ...], dict[str, Any]]:
    """Select candidates with memory independent of capture duration.

    Per-window statistics are streamed into a temporary SQLite store.  Exact
    lower-quartile baselines and score ordering therefore match the in-memory
    selector while SQL sorting spills to bounded scratch storage when needed.
    """

    import numpy as np

    _validate_receiver_bounds(config)
    if (
        isinstance(maximum_scratch_bytes, bool)
        or not isinstance(maximum_scratch_bytes, int)
        or maximum_scratch_bytes < 1
    ):
        raise ValueError("maximum_scratch_bytes must be a positive integer")
    source = Path(path)
    source_status = _validated_regular_ci16(source)
    analysis_samples = int(round(config.sample_rate_hz * config.analysis_window_seconds))
    if analysis_samples < 2:
        raise ValueError("analysis window must contain at least two complex samples")
    window_bytes = analysis_samples * 4
    window_count = source_status.st_size // window_bytes
    if window_count < 2:
        raise ValueError("capture must contain at least two complete analysis windows")
    estimated_scratch = window_count * _SQLITE_ROW_BUDGET_BYTES
    if estimated_scratch > maximum_scratch_bytes:
        raise ValueError("candidate selector scratch estimate exceeds maximum_scratch_bytes")

    scratch_root = None if scratch_directory is None else Path(scratch_directory)
    if scratch_root is not None:
        if not scratch_root.is_dir() or scratch_root.is_symlink():
            raise ValueError("scratch_directory must be a real directory")
    epsilon = float(np.finfo(np.float64).eps)
    peak_scratch_bytes = 0
    with tempfile.TemporaryDirectory(
        prefix="telemetry-yield-selector-", dir=scratch_root
    ) as temporary:
        database_path = Path(temporary) / "statistics.sqlite3"
        connection = sqlite3.connect(database_path)
        try:
            connection.execute("PRAGMA journal_mode=DELETE")
            connection.execute("PRAGMA temp_store=FILE")
            connection.execute(f"PRAGMA cache_size=-{_SQLITE_CACHE_KIB}")
            connection.execute(
                "CREATE TABLE window_stats ("
                "idx INTEGER PRIMARY KEY, power REAL NOT NULL, clip REAL NOT NULL, "
                "coherence REAL NOT NULL, clip_floor REAL NOT NULL, "
                "coherence_floor REAL NOT NULL, score REAL)"
            )
            with source.open("rb", buffering=0) as stream:
                for index in range(window_count):
                    payload = stream.read(window_bytes)
                    if len(payload) != window_bytes:
                        raise ValueError("IQ input changed while selecting windows")
                    raw = np.frombuffer(payload, dtype="<i2").reshape(-1, 2)
                    values = raw.astype(np.float64)
                    i_values = values[:, 0]
                    q_values = values[:, 1]
                    power = float(np.mean(i_values * i_values + q_values * q_values))
                    clip = float(
                        np.mean((raw == np.int16(-32768)) | (raw == np.int16(32767)))
                    )
                    real = i_values[1:] * i_values[:-1] + q_values[1:] * q_values[:-1]
                    imag = q_values[1:] * i_values[:-1] - i_values[1:] * q_values[:-1]
                    magnitude_sum = float(np.sum(np.hypot(real, imag), dtype=np.float64))
                    coherence = (
                        float(math.hypot(float(np.sum(real)), float(np.sum(imag))))
                        / magnitude_sum
                        if magnitude_sum > 0
                        else 0.0
                    )
                    if not all(math.isfinite(item) for item in (power, clip, coherence)):
                        raise ValueError("IQ input produced non-finite signal statistics")
                    connection.execute(
                        "INSERT INTO window_stats VALUES (?, ?, ?, ?, ?, ?, NULL)",
                        (
                            index,
                            power,
                            clip,
                            coherence,
                            max(clip, epsilon),
                            max(coherence, epsilon),
                        ),
                    )
                    if index % 512 == 511:
                        connection.commit()
                        peak_scratch_bytes = max(
                            peak_scratch_bytes, _scratch_usage_bytes(database_path)
                        )
                        if peak_scratch_bytes > maximum_scratch_bytes:
                            raise ValueError("candidate selector exceeded maximum_scratch_bytes")
            connection.commit()
            maximum_power_row = connection.execute(
                "SELECT MAX(power) FROM window_stats"
            ).fetchone()
            if maximum_power_row is None or maximum_power_row[0] is None:
                raise ValueError("candidate-statistics store is incomplete")
            if float(maximum_power_row[0]) == 0.0:
                peak_scratch_bytes = max(
                    peak_scratch_bytes, _scratch_usage_bytes(database_path)
                )
                if peak_scratch_bytes > maximum_scratch_bytes:
                    raise ValueError(
                        "candidate selector exceeded maximum_scratch_bytes"
                    )
                return (), {
                    "degenerate_input": True,
                    "degenerate_reason": "all_zero_ci16",
                    "analysis_windows_scanned": int(window_count),
                    "analysis_window_bytes": int(window_bytes),
                    "maximum_analysis_array_bytes": int(
                        analysis_samples * _ANALYSIS_ARRAY_BUDGET_BYTES_PER_SAMPLE
                    ),
                    "selector_sqlite_cache_bytes": _SQLITE_CACHE_KIB * 1024,
                    "selector_peak_scratch_bytes": int(peak_scratch_bytes),
                    "selector_estimated_scratch_bytes": int(estimated_scratch),
                }
            power_baseline = _sqlite_lower_median(
                connection, column="power", row_count=window_count
            )
            clip_baseline = _sqlite_lower_median(
                connection, column="clip_floor", row_count=window_count
            )
            coherence_baseline = _sqlite_lower_median(
                connection, column="coherence_floor", row_count=window_count
            )
            cursor = connection.execute(
                "SELECT idx, power, clip, coherence FROM window_stats ORDER BY idx"
            )
            for index, power, clip, coherence in cursor:
                score = max(
                    math.log1p(max(power / power_baseline - 1.0, 0.0)),
                    math.log1p(max(coherence / coherence_baseline - 1.0, 0.0)),
                    math.log1p(max(clip / clip_baseline - 1.0, 0.0)),
                )
                connection.execute(
                    "UPDATE window_stats SET score = ? WHERE idx = ?", (score, index)
                )
            connection.commit()
            connection.execute(
                "CREATE INDEX window_stats_score ON window_stats (score DESC, idx ASC)"
            )
            connection.commit()
            peak_scratch_bytes = max(peak_scratch_bytes, _scratch_usage_bytes(database_path))
            if peak_scratch_bytes > maximum_scratch_bytes:
                raise ValueError("candidate selector exceeded maximum_scratch_bytes")

            duration = (source_status.st_size // 4) / config.sample_rate_hz
            maximum_start = max(0.0, duration - config.decoder_window_seconds)
            candidates: list[PhaseWindowCandidate] = []
            for index, power, clip, coherence, score in connection.execute(
                "SELECT idx, power, clip, coherence, score "
                "FROM window_stats ORDER BY score DESC, idx ASC"
            ):
                analysis_start = index * config.analysis_window_seconds
                center = analysis_start + 0.5 * config.analysis_window_seconds
                if any(
                    abs(
                        center
                        - (
                            candidate.analysis_start_seconds
                            + 0.5 * config.analysis_window_seconds
                        )
                    )
                    < config.window_nms_seconds
                    for candidate in candidates
                ):
                    continue
                decoder_start = min(
                    maximum_start,
                    max(0.0, analysis_start - config.decoder_window_lead_seconds),
                )
                candidates.append(
                    PhaseWindowCandidate(
                        analysis_index=int(index),
                        analysis_start_seconds=float(analysis_start),
                        decoder_start_seconds=float(decoder_start),
                        mean_power=float(power),
                        endpoint_clip_fraction=float(clip),
                        lag1_phase_coherence=float(coherence),
                        score=float(score),
                    )
                )
                if len(candidates) >= config.candidate_window_limit:
                    break
        finally:
            connection.close()
    return tuple(candidates), {
        "degenerate_input": False,
        "degenerate_reason": None,
        "analysis_windows_scanned": int(window_count),
        "analysis_window_bytes": int(window_bytes),
        "maximum_analysis_array_bytes": int(
            analysis_samples * _ANALYSIS_ARRAY_BUDGET_BYTES_PER_SAMPLE
        ),
        "selector_sqlite_cache_bytes": _SQLITE_CACHE_KIB * 1024,
        "selector_peak_scratch_bytes": int(peak_scratch_bytes),
        "selector_estimated_scratch_bytes": int(estimated_scratch),
    }


def _implementation_manifest() -> dict[str, Any]:
    files = {}
    for name in (
        "ax25_validation.py",
        "blind_phase_fsk_file.py",
        "camras_replay.py",
        "canonical.py",
        "clipping_robust_fsk.py",
        "clock_recovery.py",
        "crc.py",
        "soft_sync.py",
        "symbol_boundary.py",
    ):
        path = Path(__file__).with_name(name)
        files[name] = {"sha256": _sha256_file(path), "size_bytes": path.stat().st_size}
    import numpy as np
    import scipy

    return {
        "demodulator_id": DEMODULATOR_ID,
        "demodulator_version": DEMODULATOR_VERSION,
        "selector_version": SELECTOR_VERSION,
        "source_files": files,
        "runtime": {
            "python": sys.version.split()[0],
            "numpy": np.__version__,
            "scipy": scipy.__version__,
        },
    }


def _frame_document(frame: Any) -> dict[str, Any]:
    frame_with_fcs = frame.payload + frame.fcs
    repaired = bool(frame.flipped_symbol_indices)
    return {
        "classification": (
            "crc_valid_list_repaired_candidate_not_authenticated"
            if repaired
            else "native_trusted_uncorrected"
        ),
        "native_trusted": not repaired,
        "payload_hex": frame.payload.hex(),
        "payload_sha256": _sha256_bytes(frame.payload),
        "fcs_hex": frame.fcs.hex(),
        "frame_sha256": _sha256_bytes(frame_with_fcs),
        "validation_layers": [
            "repeated_hdlc_flag_preamble",
            "complete_hdlc_flags_and_unstuffing",
            "crc16_x25_valid_complete_frame",
            "strict_parse_ax25_ui_after_verified_fcs_removal",
        ],
        "provenance": {
            "decoder_start_seconds": frame.decoder_start_seconds,
            "estimated_frame_start_seconds": frame.estimated_frame_start_seconds,
            "constant_radius_factor": frame.constant_radius_factor,
            "phase_difference_lag": frame.phase_difference_lag,
            "g3ruh_descramble": frame.g3ruh_descramble,
            "timing": asdict(frame.timing),
            "frame_start_symbol": frame.frame_start_symbol,
            "frame_stop_symbol": frame.frame_stop_symbol,
            "flipped_symbol_indices": list(frame.flipped_symbol_indices),
            "flipped_symbol_reliabilities": list(frame.flipped_symbol_reliabilities),
            "search_stage": frame.search_stage,
            "attempted_candidates": frame.attempted_candidates,
        },
    }


def _result_document(
    *,
    source: Path,
    source_size: int,
    source_sha256: str,
    config: BlindPhaseFskRunConfig,
    decoded: BlindPhaseFskResult,
    selector_counters: Mapping[str, Any],
    implementation: Mapping[str, Any],
) -> dict[str, Any]:
    detections = [_frame_document(frame) for frame in decoded.frames]
    consensus = group_ax25_frame_consensus(decoded.frames)
    groups = []
    for group in consensus:
        groups.append(
            {
                "payload_hex": group.payload.hex(),
                "payload_sha256": _sha256_bytes(group.payload),
                "fcs_hex": group.fcs.hex(),
                "estimated_frame_start_seconds": group.estimated_frame_start_seconds,
                "detection_count": len(group.detections),
                "frontend_variants": [list(item) for item in group.independent_frontends],
                "g3ruh_descramble_modes": list(group.g3ruh_descramble_modes),
                "has_uncorrected_detection": group.has_uncorrected_detection,
                "has_correction_consensus": group.has_correction_consensus,
                "native_trusted": group.native_trusted,
            }
        )
    effective_config = config.to_dict()
    decoder_samples = int(
        round(config.receiver.sample_rate_hz * config.receiver.decoder_window_seconds)
    )
    counters = {
        key: value
        for key, value in selector_counters.items()
        if key not in ("degenerate_input", "degenerate_reason")
    }
    document = {
        "api_version": API_VERSION,
        "schema_version": SCHEMA_VERSION,
        "status": "completed",
        "degenerate_input": bool(selector_counters["degenerate_input"]),
        "degenerate_reason": selector_counters["degenerate_reason"],
        "attempt_fingerprint": _attempt_fingerprint(
            source_size=source_size,
            source_sha256=source_sha256,
            config=config,
            implementation=implementation,
        ),
        "source": {
            "path": str(source),
            "sample_format": "ci16_le",
            "sample_rate_hz": config.receiver.sample_rate_hz,
            "size_bytes": source_size,
            "sha256": source_sha256,
            "complex_samples": source_size // 4,
            "duration_seconds": decoded.input_duration_seconds,
        },
        "route": {
            "modulation_family": "binary_phase_fsk_gfsk_gmsk",
            "compatible_selection_modulations": list(
                SUPPORTED_SELECTION_MODULATION_LABELS
            ),
            "demodulator_id": DEMODULATOR_ID,
            "demodulator_version": DEMODULATOR_VERSION,
            "protocol_adapter_id": PROTOCOL_ADAPTER_ID,
            "protocol_id": "ax25",
            "qualification": "blind_receiver_candidate_holdout_validation_pending",
        },
        "effective_config": effective_config,
        "config_sha256": config.fingerprint,
        "implementation": implementation,
        "selection": {
            "reference_free": True,
            "selector_version": SELECTOR_VERSION,
            "selected_windows": [asdict(item) for item in decoded.selected_windows],
        },
        "resource_counters": {
            **counters,
            "selected_windows": len(decoded.selected_windows),
            "decoded_windows": decoded.decoded_windows,
            "decoder_window_complex_samples": decoder_samples,
            "maximum_decoder_iq_array_bytes": decoder_samples * 16,
            "decoder_working_set_model_bytes": (
                _decoder_working_set_model_bytes(config.receiver)
            ),
            "timing_hypotheses_examined": decoded.timing_hypotheses_examined,
            "protocol_candidates_attempted": decoded.protocol_candidates_attempted,
            "native_protocol_candidates_attempted": (
                decoded.native_protocol_candidates_attempted
            ),
            "repair_protocol_candidates_attempted": (
                decoded.repair_protocol_candidates_attempted
            ),
            "frame_detections": len(decoded.frames),
            "consensus_groups": len(groups),
            "native_trusted_groups": sum(bool(item["native_trusted"]) for item in groups),
        },
        "candidates": {
            "detections": detections,
            "consensus_groups": groups,
            "trusted": [item for item in groups if item["native_trusted"]],
            "repaired_untrusted": [
                item for item in groups if not item["native_trusted"]
            ],
        },
        "claim_guard": {
            "publication_superiority_established": False,
            "deployment_qualification_established": False,
            "file_api_contract_versioned": True,
            "repaired_frame_authentication": (
                "CRC-valid list repairs remain candidates; correlated frontend "
                "agreement is not independent authentication"
            ),
        },
    }
    document["result_payload_sha256"] = _sha256_document(document)
    return document


def run_blind_phase_fsk_file(
    source: str | Path,
    output: str | Path,
    *,
    config: BlindPhaseFskRunConfig,
    scratch_directory: str | Path | None = None,
    api_version: str = API_VERSION,
) -> dict[str, Any]:
    """Run one content-bound blind decode and atomically publish its result."""

    if api_version != API_VERSION:
        raise ValueError(f"unsupported blind phase FSK API version: {api_version!r}")
    source_path = Path(source).absolute()
    output_path = Path(output).absolute()
    if source_path == output_path:
        raise ValueError("output path must differ from IQ input")
    output_exists = output_path.exists() or output_path.is_symlink()
    initial_status = _validated_regular_ci16(source_path)
    if initial_status.st_size != config.expected_input_size_bytes:
        raise ValueError("IQ size does not match expected_input_size_bytes")
    initial_identity = _identity(initial_status)
    source_sha256 = _sha256_file(source_path)
    if source_sha256 != config.expected_input_sha256:
        raise ValueError("IQ SHA-256 does not match expected_input_sha256")
    if _identity(_validated_regular_ci16(source_path)) != initial_identity:
        raise ValueError("IQ input changed while hashing")
    implementation = _implementation_manifest()
    completed_existing: dict[str, Any] | None = None
    if output_exists:
        completed_existing = _load_matching_completed_result(
            output_path,
            source_size=initial_status.st_size,
            source_sha256=source_sha256,
            config=config,
            implementation=implementation,
        )
        if _identity(_validated_regular_ci16(source_path)) != initial_identity:
            raise ValueError("IQ input changed while verifying completed result")

    windows, selector_counters = select_ci16_phase_windows_streaming(
        source_path,
        config=config.receiver,
        scratch_directory=scratch_directory,
        maximum_scratch_bytes=config.maximum_scratch_bytes,
    )
    if _identity(_validated_regular_ci16(source_path)) != initial_identity:
        raise ValueError("IQ input changed while selecting candidate windows")
    if selector_counters["degenerate_input"]:
        decoded = BlindPhaseFskResult(
            input_path=str(source_path),
            input_sha256=source_sha256,
            input_complex_samples=initial_status.st_size // 4,
            input_duration_seconds=(initial_status.st_size // 4)
            / config.receiver.sample_rate_hz,
            selected_windows=(),
            decoded_windows=0,
            timing_hypotheses_examined=0,
            protocol_candidates_attempted=0,
            native_protocol_candidates_attempted=0,
            repair_protocol_candidates_attempted=0,
            frames=(),
        )
    else:
        decoded = decode_clipping_robust_ax25_ci16(
            source_path, config=config.receiver, selected_windows=windows
        )
    if _identity(_validated_regular_ci16(source_path)) != initial_identity:
        raise ValueError("IQ input changed while decoding")
    if decoded.input_sha256 != source_sha256 or _sha256_file(source_path) != source_sha256:
        raise ValueError("IQ input content changed during decoding")
    if decoded.input_complex_samples != initial_status.st_size // 4:
        raise ValueError("decoder returned an inconsistent source size")
    if len(decoded.frames) > _MAX_FRAME_DETECTIONS:
        raise ValueError("decoder frame detections exceed the result bound")
    if _implementation_manifest() != implementation:
        raise ValueError("receiver implementation changed during decoding")

    document = _result_document(
        source=source_path,
        source_size=initial_status.st_size,
        source_sha256=source_sha256,
        config=config,
        decoded=decoded,
        selector_counters=selector_counters,
        implementation=implementation,
    )
    if completed_existing is not None:
        if completed_existing != document:
            raise ValueError(
                "existing blind receiver output failed deterministic revalidation"
            )
        return completed_existing
    try:
        _atomic_json(output_path, document)
    except _OutputExistsError:
        completed = _load_matching_completed_result(
            output_path,
            source_size=initial_status.st_size,
            source_sha256=source_sha256,
            config=config,
            implementation=implementation,
        )
        if _identity(_validated_regular_ci16(source_path)) != initial_identity:
            raise ValueError("IQ input changed while verifying concurrent result")
        if completed != document:
            raise ValueError(
                "concurrent blind receiver output failed deterministic revalidation"
            )
        return completed
    return document
