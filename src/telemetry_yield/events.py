"""Conservative cross-station transmission-event grouping."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta


@dataclass(frozen=True)
class EventCandidate:
    observation_id: str
    norad_id: int
    transmitter_uuid: str
    start_utc: datetime
    end_utc: datetime
    station_id: str
    payload_hashes: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        for name, value in (("start_utc", self.start_utc), ("end_utc", self.end_utc)):
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError(f"{name} must be timezone-aware UTC")
            if value.utcoffset() != timedelta(0):
                raise ValueError(f"{name} must use UTC offset +00:00")
        if self.end_utc <= self.start_utc:
            raise ValueError("end_utc must be after start_utc")


def same_transmission_event(
    left: EventCandidate,
    right: EventCandidate,
    *,
    time_tolerance: timedelta = timedelta(seconds=30),
    doppler_tracks_consistent: bool = False,
) -> bool:
    """Require identity, temporal overlap, and independent physical evidence.

    Time overlap alone deliberately cannot produce an event match.
    """
    if left.norad_id != right.norad_id or left.transmitter_uuid != right.transmitter_uuid:
        return False
    overlaps = (
        left.start_utc <= right.end_utc + time_tolerance
        and right.start_utc <= left.end_utc + time_tolerance
    )
    if not overlaps:
        return False
    payload_agrees = bool(left.payload_hashes & right.payload_hashes)
    return payload_agrees or doppler_tracks_consistent
