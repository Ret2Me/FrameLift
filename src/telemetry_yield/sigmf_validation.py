"""Validation helpers for public SigMF datasets and GPS L1 C/A recordings."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True)
class SigMFSummary:
    metadata_path: Path
    data_path: Path
    datatype: str
    sample_rate_hz: float
    sample_count: int
    sha512: str


@dataclass(frozen=True)
class GPSAcquisition:
    prn: int
    doppler_hz: float
    code_phase_samples: int
    peak_to_median: float


_GPS_G2_TAPS: dict[int, tuple[int, int]] = {
    1: (2, 6),
    2: (3, 7),
    3: (4, 8),
    4: (5, 9),
    5: (1, 9),
    6: (2, 10),
    7: (1, 8),
    8: (2, 9),
    9: (3, 10),
    10: (2, 3),
    11: (3, 4),
    12: (5, 6),
    13: (6, 7),
    14: (7, 8),
    15: (8, 9),
    16: (9, 10),
    17: (1, 4),
    18: (2, 5),
    19: (3, 6),
    20: (4, 7),
    21: (5, 8),
    22: (6, 9),
    23: (1, 3),
    24: (4, 6),
    25: (5, 7),
    26: (6, 8),
    27: (7, 9),
    28: (8, 10),
    29: (1, 6),
    30: (2, 7),
    31: (3, 8),
    32: (4, 9),
}


def _sha512_file(path: Path, *, chunk_bytes: int = 1024 * 1024) -> str:
    digest = hashlib.sha512()
    with path.open("rb") as source:
        while chunk := source.read(chunk_bytes):
            digest.update(chunk)
    return digest.hexdigest()


def validate_sigmf_dataset(metadata_path: Path) -> SigMFSummary:
    """Open a SigMF pair, validate its schema, and verify ``core:sha512``."""
    from sigmf import sigmffile

    metadata_path = metadata_path.resolve(strict=True)
    document = json.loads(metadata_path.read_text(encoding="utf-8"))
    global_metadata = document.get("global")
    if not isinstance(global_metadata, dict):
        raise ValueError("SigMF metadata has no global object")
    expected_sha512 = global_metadata.get("core:sha512")
    if not isinstance(expected_sha512, str) or len(expected_sha512) != 128:
        raise ValueError("SigMF metadata has no valid core:sha512")

    sigmf_recording = sigmffile.fromfile(metadata_path, skip_checksum=True)
    sigmf_recording.validate()
    data_path = Path(sigmf_recording.data_file).resolve(strict=True)
    actual_sha512 = _sha512_file(data_path)
    if actual_sha512.lower() != expected_sha512.lower():
        raise ValueError("SigMF data SHA-512 does not match metadata")

    datatype = sigmf_recording.get_global_field("core:datatype")
    sample_rate = sigmf_recording.get_global_field("core:sample_rate")
    if not isinstance(datatype, str) or not datatype:
        raise ValueError("SigMF datatype is missing")
    if not isinstance(sample_rate, (int, float)) or not math.isfinite(sample_rate):
        raise ValueError("SigMF sample rate is invalid")
    if sample_rate <= 0:
        raise ValueError("SigMF sample rate must be positive")
    return SigMFSummary(
        metadata_path=metadata_path,
        data_path=data_path,
        datatype=datatype,
        sample_rate_hz=float(sample_rate),
        sample_count=int(sigmf_recording.sample_count),
        sha512=actual_sha512,
    )


def gps_ca_code(prn: int):
    """Generate one 1023-chip GPS L1 C/A Gold code as values in {-1, +1}."""
    import numpy as np

    try:
        tap_a, tap_b = _GPS_G2_TAPS[prn]
    except KeyError as exc:
        raise ValueError("GPS C/A PRN must be in the range 1..32") from exc
    g1 = np.ones(10, dtype=np.uint8)
    g2 = np.ones(10, dtype=np.uint8)
    output = np.empty(1023, dtype=np.int8)
    for index in range(1023):
        chip = int(g1[9]) ^ int(g2[tap_a - 1]) ^ int(g2[tap_b - 1])
        output[index] = 1 if chip == 0 else -1
        feedback_1 = int(g1[2]) ^ int(g1[9])
        feedback_2 = (
            int(g2[1])
            ^ int(g2[2])
            ^ int(g2[5])
            ^ int(g2[7])
            ^ int(g2[8])
            ^ int(g2[9])
        )
        g1[1:] = g1[:-1]
        g2[1:] = g2[:-1]
        g1[0] = feedback_1
        g2[0] = feedback_2
    return output


def sampled_gps_ca_code(prn: int, sample_rate_hz: float):
    """Sample one millisecond of a GPS C/A code at ``sample_rate_hz``."""
    import numpy as np

    if not math.isfinite(sample_rate_hz) or sample_rate_hz <= 0:
        raise ValueError("sample_rate_hz must be finite and positive")
    samples_per_ms = int(round(sample_rate_hz / 1000.0))
    if samples_per_ms < 1023:
        raise ValueError("sample_rate_hz is too low for GPS C/A acquisition")
    chip_indices = np.floor(
        np.arange(samples_per_ms, dtype=np.float64) * 1_023_000.0 / sample_rate_hz
    ).astype(np.int64)
    return gps_ca_code(prn)[chip_indices % 1023].astype(np.float32)


def acquire_gps_l1(
    samples,
    *,
    sample_rate_hz: float,
    prns: Iterable[int] = range(1, 33),
    doppler_min_hz: int = -6000,
    doppler_max_hz: int = 6000,
    doppler_step_hz: int = 500,
    noncoherent_ms: int = 5,
    threshold: float = 10.0,
) -> tuple[GPSAcquisition, ...]:
    """Acquire GPS L1 C/A PRNs with FFT correlation over short 1 ms blocks."""
    import numpy as np

    if doppler_step_hz <= 0 or doppler_min_hz > doppler_max_hz:
        raise ValueError("invalid Doppler grid")
    if noncoherent_ms <= 0:
        raise ValueError("noncoherent_ms must be positive")
    if not math.isfinite(threshold) or threshold <= 1:
        raise ValueError("threshold must be finite and greater than one")
    samples_per_ms = int(round(sample_rate_hz / 1000.0))
    values = np.asarray(samples, dtype=np.complex64).reshape(-1)
    required = samples_per_ms * noncoherent_ms
    if values.size < required:
        raise ValueError(f"GPS acquisition requires at least {required} samples")
    blocks = values[:required].reshape(noncoherent_ms, samples_per_ms).copy()
    blocks -= blocks.mean(axis=1, keepdims=True)
    times = np.arange(samples_per_ms, dtype=np.float64) / sample_rate_hz

    detections: list[GPSAcquisition] = []
    for prn in tuple(prns):
        code_fft = np.conj(np.fft.fft(sampled_gps_ca_code(prn, sample_rate_hz)))
        best: GPSAcquisition | None = None
        for doppler_hz in range(
            doppler_min_hz, doppler_max_hz + 1, doppler_step_hz
        ):
            carrier = np.exp(-2j * np.pi * doppler_hz * times)
            correlation_power = np.zeros(samples_per_ms, dtype=np.float64)
            for block in blocks:
                correlation = np.fft.ifft(np.fft.fft(block * carrier) * code_fft)
                correlation_power += np.abs(correlation) ** 2
            median = float(np.median(correlation_power))
            if median <= 0:
                continue
            peak_index = int(np.argmax(correlation_power))
            candidate = GPSAcquisition(
                prn=prn,
                doppler_hz=float(doppler_hz),
                code_phase_samples=peak_index,
                peak_to_median=float(correlation_power[peak_index] / median),
            )
            if best is None or candidate.peak_to_median > best.peak_to_median:
                best = candidate
        if best is not None and best.peak_to_median >= threshold:
            detections.append(best)
    return tuple(sorted(detections, key=lambda item: item.prn))
