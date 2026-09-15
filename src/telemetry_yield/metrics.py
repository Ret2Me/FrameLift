"""Small, auditable metrics used throughout telemetry-yield experiments."""

from __future__ import annotations

import math
from collections.abc import Iterable

from telemetry_yield.models import FrameRecord

UniqueFrameKey = tuple[str, str]


def unique_crc_frame_keys(frames: Iterable[FrameRecord]) -> set[UniqueFrameKey]:
    """Return unique CRC-valid frames keyed by event and payload digest."""

    return {
        (frame.transmission_event_id, frame.payload_sha256)
        for frame in frames
        if frame.crc_valid
    }


def unique_crc_frames(frames: Iterable[FrameRecord]) -> int:
    """Count CRC-valid frames without inflating repeated decodes."""

    return len(unique_crc_frame_keys(frames))


def false_accepts_per_hour(
    n_frames_on_negative: int, total_hours_negative: float
) -> float:
    """Return CRC-valid frames accepted on negative material per hour."""

    if isinstance(n_frames_on_negative, bool) or n_frames_on_negative < 0:
        raise ValueError("n_frames_on_negative must be a non-negative integer")
    if not isinstance(n_frames_on_negative, int):
        raise TypeError("n_frames_on_negative must be an integer")
    if not math.isfinite(total_hours_negative) or total_hours_negative <= 0:
        raise ValueError("total_hours_negative must be finite and greater than zero")
    return n_frames_on_negative / total_hours_negative


def frames_per_cpu_second(n_unique_crc_frames: int, cpu_seconds: float) -> float:
    """Return unique CRC-valid frame yield normalized by CPU time."""

    if isinstance(n_unique_crc_frames, bool) or n_unique_crc_frames < 0:
        raise ValueError("n_unique_crc_frames must be a non-negative integer")
    if not isinstance(n_unique_crc_frames, int):
        raise TypeError("n_unique_crc_frames must be an integer")
    if not math.isfinite(cpu_seconds) or cpu_seconds <= 0:
        raise ValueError("cpu_seconds must be finite and greater than zero")
    return n_unique_crc_frames / cpu_seconds

