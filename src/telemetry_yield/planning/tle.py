"""Versioned TLE acquisition and change detection."""

from __future__ import annotations

import urllib.request
import math
import time
from calendar import isleap
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Callable, Mapping, Protocol, Sequence
from urllib.parse import urlencode
from urllib.error import HTTPError, URLError

from .models import TleSnapshot, as_utc


class TleError(RuntimeError):
    """Raised when an orbital element source returns unusable data."""


def tle_checksum_valid(line: str) -> bool:
    if len(line) != 69 or not line[-1].isdigit():
        return False
    checksum = sum(
        int(character)
        if character.isdigit()
        else 1
        if character == "-"
        else 0
        for character in line[:68]
    )
    return checksum % 10 == int(line[-1])


def parse_tle_epoch(line1: str) -> datetime:
    """Parse the fixed-width YYDDD.dddddddd epoch from TLE line 1."""

    if len(line1) < 32 or not line1.startswith("1 "):
        raise ValueError("invalid TLE line 1")
    raw = line1[18:32].strip()
    if len(raw) < 5:
        raise ValueError("TLE epoch is missing")
    try:
        short_year = int(raw[:2])
        day_of_year = float(raw[2:])
    except ValueError as exc:
        raise ValueError("invalid TLE epoch") from exc
    year = 1900 + short_year if short_year >= 57 else 2000 + short_year
    upper_bound = 367.0 if isleap(year) else 366.0
    if not 1.0 <= day_of_year < upper_bound:
        raise ValueError("TLE day-of-year is outside its valid range")
    return datetime(year, 1, 1, tzinfo=UTC) + timedelta(days=day_of_year - 1.0)


def parse_tle_text(
    text: str,
    *,
    norad_id: int,
    fetched_at: datetime,
    source: str,
    fallback_name: str | None = None,
) -> TleSnapshot:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) == 2 and lines[0].startswith("1 "):
        name = fallback_name or f"NORAD {norad_id}"
        line1, line2 = lines
    elif len(lines) == 3:
        name, line1, line2 = lines[0], lines[1], lines[2]
    else:
        raise TleError("TLE response must contain two element lines")
    if not line1.startswith("1 ") or not line2.startswith("2 "):
        raise TleError("TLE response has invalid line designators")
    if not tle_checksum_valid(line1) or not tle_checksum_valid(line2):
        raise TleError("TLE checksum mismatch")
    snapshot = TleSnapshot(
        norad_id=norad_id,
        name=name.removeprefix("0 "),
        line1=line1,
        line2=line2,
        epoch=parse_tle_epoch(line1),
        fetched_at=fetched_at,
        source=source,
    )
    try:
        catalog_numbers = (int(line1[2:7]), int(line2[2:7]))
    except ValueError as exc:
        raise TleError("TLE catalog number is malformed") from exc
    if catalog_numbers != (norad_id, norad_id):
        raise TleError(
            f"TLE catalog numbers {catalog_numbers} do not match requested {norad_id}"
        )
    return snapshot


FetchBytes = Callable[[str, Mapping[str, str], float, int], bytes]


def _bounded_get(
    url: str, headers: Mapping[str, str], timeout_seconds: float, limit: int
) -> bytes:
    request = urllib.request.Request(url, headers=dict(headers), method="GET")
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        body = response.read(limit + 1)
    if len(body) > limit:
        raise TleError(f"TLE response exceeded {limit} bytes")
    return body


class TleProvider(Protocol):
    def fetch(self, norad_id: int, *, now: datetime | None = None) -> TleSnapshot: ...


class CelesTrakTleProvider:
    """Small bounded client for CelesTrak's current GP/TLE endpoint."""

    BASE_URL = "https://celestrak.org/NORAD/elements/gp.php"

    def __init__(
        self,
        *,
        user_agent: str,
        fetch_bytes: FetchBytes = _bounded_get,
        timeout_seconds: float = 20.0,
        max_response_bytes: int = 8_192,
        max_retries: int = 3,
        backoff_seconds: float = 0.5,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not user_agent.strip() or "\n" in user_agent or "\r" in user_agent:
            raise ValueError("an explicit single-line User-Agent is required")
        if timeout_seconds <= 0 or max_response_bytes <= 0:
            raise ValueError("timeout and response limit must be positive")
        if max_retries < 0:
            raise ValueError("max_retries must be non-negative")
        if not math.isfinite(backoff_seconds) or backoff_seconds < 0:
            raise ValueError("backoff_seconds must be finite and non-negative")
        self.user_agent = user_agent.strip()
        self.fetch_bytes = fetch_bytes
        self.timeout_seconds = timeout_seconds
        self.max_response_bytes = max_response_bytes
        self.max_retries = max_retries
        self.backoff_seconds = backoff_seconds
        self.sleep = sleep

    def fetch(self, norad_id: int, *, now: datetime | None = None) -> TleSnapshot:
        if isinstance(norad_id, bool) or norad_id <= 0:
            raise ValueError("norad_id must be positive")
        fetched_at = as_utc(now or datetime.now(UTC), name="now")
        url = f"{self.BASE_URL}?{urlencode({'CATNR': norad_id, 'FORMAT': 'TLE'})}"
        body: bytes | None = None
        for attempt in range(self.max_retries + 1):
            try:
                body = self.fetch_bytes(
                    url,
                    {"User-Agent": self.user_agent, "Accept": "text/plain"},
                    self.timeout_seconds,
                    self.max_response_bytes,
                )
                break
            except HTTPError as exc:
                retryable = exc.code == 429 or 500 <= exc.code <= 599
                if retryable and attempt < self.max_retries:
                    self.sleep(self.backoff_seconds * (2**attempt))
                    continue
                raise TleError(
                    f"CelesTrak TLE request failed with HTTP {exc.code}"
                ) from exc
            except (TimeoutError, URLError, OSError) as exc:
                if attempt < self.max_retries:
                    self.sleep(self.backoff_seconds * (2**attempt))
                    continue
                raise TleError("CelesTrak TLE request failed after retries") from exc
        if body is None:
            raise AssertionError("TLE retry loop exited without a response")
        try:
            text = body.decode("ascii")
        except UnicodeDecodeError as exc:
            raise TleError("CelesTrak returned a non-ASCII TLE") from exc
        return parse_tle_text(
            text,
            norad_id=norad_id,
            fetched_at=fetched_at,
            source=url,
        )


@dataclass(frozen=True, slots=True)
class TleChange:
    norad_id: int
    old_fingerprint: str | None
    new_fingerprint: str
    epoch_shift_seconds: float | None

    @property
    def changed(self) -> bool:
        return self.old_fingerprint != self.new_fingerprint


def diff_tle_snapshots(
    previous: Mapping[int, TleSnapshot], current: Mapping[int, TleSnapshot]
) -> tuple[TleChange, ...]:
    changes: list[TleChange] = []
    for norad_id, fresh in sorted(current.items()):
        old = previous.get(norad_id)
        changes.append(
            TleChange(
                norad_id=norad_id,
                old_fingerprint=old.fingerprint if old else None,
                new_fingerprint=fresh.fingerprint,
                epoch_shift_seconds=(
                    (fresh.epoch - old.epoch).total_seconds() if old else None
                ),
            )
        )
    return tuple(changes)


def refresh_tles(
    provider: TleProvider,
    norad_ids: Sequence[int],
    *,
    now: datetime | None = None,
) -> dict[int, TleSnapshot]:
    """Fetch every unique catalog ID, failing closed on a partial refresh."""

    refreshed: dict[int, TleSnapshot] = {}
    for norad_id in sorted(set(norad_ids)):
        refreshed[norad_id] = provider.fetch(norad_id, now=now)
    return refreshed
