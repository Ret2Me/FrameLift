"""Versioned reception history, replayed by availability time, not event time.

No network access, model fitting, or implicit clock. A caller supplies the
prediction issue time separately from the future pass time. Unknown/retracted
labels never count as failures. Captured and assumed availability cannot mix.
"""
from __future__ import annotations

import bisect
import hashlib
import heapq
import json
import math
import sqlite3
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

WINDOWS = (1, 7, 30, 90, 180)
SCOPES = ("global", "sat", "station", "transmitter", "pair", "link", "similar", "station_band")
TASKS = ("signal_present", "decode_success_given_signal")


def utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("an explicit timezone-aware time is required")
    return value.astimezone(UTC)


def keys(row):
    sat, station = row.norad_id, row.station_id
    tx = row.transmitter_uuid or None
    elevation = getattr(row, "max_elevation_deg", None)
    frequency = getattr(row, "frequency_hz", None)
    elev_bin = int(min(elevation, 89.999) // 15) if elevation is not None else None
    band = int(math.log2(frequency / 1e6)) if frequency is not None and frequency > 0 else None
    return {"global": "all", "sat": sat, "station": station, "transmitter": tx,
            "pair": (sat, station), "link": (sat, station, tx) if tx else None,
            "similar": (sat, station, tx, elev_bin) if tx and elev_bin is not None else None,
            "station_band": (station, band) if band is not None else None}


@dataclass(frozen=True)
class HistoryEvent:
    observation_key: str
    task: str
    ended_at: datetime
    available_at: datetime
    norad_id: int
    station_id: int
    transmitter_uuid: str | None
    max_elevation_deg: float | None
    frequency_hz: float | None
    outcome: int | None
    availability_kind: str = "captured"

    def __post_init__(self):
        object.__setattr__(self, "ended_at", utc(self.ended_at))
        object.__setattr__(self, "available_at", utc(self.available_at))
        if self.available_at < self.ended_at:
            raise ValueError("a result cannot be available before reception ended")
        if not self.observation_key or self.task not in TASKS:
            raise ValueError("invalid observation identity or task")
        if self.outcome is not None and (type(self.outcome) is not int or self.outcome not in (0, 1)):
            raise ValueError("outcome must be 0, 1, or unknown")
        if any(type(v) is not int or v <= 0 for v in (self.norad_id, self.station_id)):
            raise ValueError("positive integer satellite/station IDs required")
        if self.max_elevation_deg is not None and not 0 <= self.max_elevation_deg <= 90:
            raise ValueError("invalid elevation")
        if self.frequency_hz is not None and (not math.isfinite(self.frequency_hz) or self.frequency_hz <= 0):
            raise ValueError("invalid frequency")
        if self.availability_kind not in {"captured", "assumed_delay"}:
            raise ValueError("explicit availability provenance required")

    @classmethod
    def from_row(cls, row, task, available_at, *, availability_kind="captured"):
        return cls(f"satnogs:{row.observation_id}", task, row.end, available_at,
                   row.norad_id, row.station_id, row.transmitter_uuid,
                   row.max_elevation_deg, row.frequency_hz, getattr(row, task), availability_kind)

    def payload(self):
        result = asdict(self)
        result["ended_at"] = self.ended_at.isoformat()
        result["available_at"] = self.available_at.isoformat()
        return result

    @classmethod
    def parse(cls, payload):
        value = dict(payload)
        for name in ("ended_at", "available_at"):
            value[name] = datetime.fromisoformat(value[name])
        return cls(**value)


class HistoryCursor:
    """Efficient forward-only replay with corrections and rolling expiry.

    Future records may be preloaded but cannot influence a snapshot until
    available_at. All predictions in a batch share a monotonically advancing
    cursor; construct a fresh cursor to replay an earlier time.
    """
    def __init__(self, events, *, task, availability_kind):
        unique = {}
        for event in events:
            if event.task != task:
                continue
            if event.availability_kind != availability_kind:
                raise ValueError("cannot mix captured and assumed availability")
            key = (event.observation_key, event.available_at)
            if key in unique and unique[key] != event:
                raise ValueError("conflicting versions at the same availability time")
            unique[key] = event
        self.events = sorted(unique.values(), key=lambda e: (e.available_at, e.observation_key))
        self.index, self.as_of = 0, None
        self.latest = {}
        self.totals = defaultdict(lambda: [0, 0])
        self.rolling = defaultdict(lambda: [0, 0])
        self.ordered = defaultdict(list)
        self.expiry = []

    def _change(self, event, sign):
        if event.outcome is None:
            return
        for scope, key in keys(event).items():
            if key is None:
                continue
            group = (scope, key)
            counts = self.totals[group]
            counts[0] += sign
            counts[1] += sign * event.outcome
            ordered = self.ordered[group]
            marker = (event.ended_at, event.observation_key)
            if sign > 0:
                bisect.insort(ordered, marker)
            else:
                index = bisect.bisect_left(ordered, marker)
                if index == len(ordered) or ordered[index] != marker:
                    raise RuntimeError("history index corruption")
                ordered.pop(index)
            for days in WINDOWS:
                if event.ended_at + timedelta(days=days) >= self.as_of:
                    counts = self.rolling[(scope, key, days)]
                    counts[0] += sign
                    counts[1] += sign * event.outcome
        if sign > 0:
            for days in WINDOWS:
                expiry = event.ended_at + timedelta(days=days)
                if expiry >= self.as_of:
                    heapq.heappush(self.expiry, (expiry, days, event.observation_key, event.available_at))

    def advance(self, as_of):
        as_of = utc(as_of)
        if self.as_of is not None and as_of < self.as_of:
            raise ValueError("history cursor cannot go backwards")
        self.as_of = as_of
        while self.expiry and self.expiry[0][0] < as_of:
            _, days, oid, version = heapq.heappop(self.expiry)
            event = self.latest.get(oid)
            if event is None or event.available_at != version or event.outcome is None:
                continue
            for scope, key in keys(event).items():
                if key is not None:
                    counts = self.rolling[(scope, key, days)]
                    counts[0] -= 1
                    counts[1] -= event.outcome
        while self.index < len(self.events) and self.events[self.index].available_at <= as_of:
            event = self.events[self.index]
            previous = self.latest.get(event.observation_key)
            if previous is not None:
                self._change(previous, -1)
            self.latest[event.observation_key] = event
            self._change(event, 1)
            self.index += 1

    def snapshot(self, row, *, as_of, pass_start):
        as_of, pass_start = utc(as_of), utc(pass_start)
        if pass_start <= as_of:
            raise ValueError("prediction must precede the target pass")
        self.advance(as_of)
        result = {"prediction_lead_days": (pass_start - as_of).total_seconds() / 86400}
        query_keys = keys(row)
        for scope, key in query_keys.items():
            count, success = self.totals.get((scope, key), (0, 0))
            result.update({f"{scope}_count": float(count), f"{scope}_success": float(success),
                           f"{scope}_rate": (success + 1) / (count + 2)})
            ordered = self.ordered.get((scope, key), ())
            if ordered:
                result[f"{scope}_days_since_last"] = (as_of - ordered[-1][0]).total_seconds() / 86400
            for days in WINDOWS:
                n, s = self.rolling.get((scope, key, days), (0, 0))
                result[f"{scope}_{days}d_count"] = float(n)
                result[f"{scope}_{days}d_rate"] = (s + 1) / (n + 2)
            for size in (10, 50):
                selected = ordered[-size:]
                s = sum(self.latest[oid].outcome for _, oid in selected)
                result[f"{scope}_last{size}_count"] = float(len(selected))
                result[f"{scope}_last{size}_rate"] = (s + 1) / (len(selected) + 2)
            result[f"{scope}_trend_7d_30d"] = result[f"{scope}_7d_rate"] - result[f"{scope}_30d_rate"]
        for name, scope in (("station_other_sat", "station"), ("sat_other_station", "sat")):
            for days in (7, 30):
                n, s = self.rolling.get((scope, query_keys[scope], days), (0, 0))
                pn, ps = self.rolling.get(("pair", query_keys["pair"], days), (0, 0))
                result[f"{name}_{days}d_count"] = float(n - pn)
                result[f"{name}_{days}d_rate"] = (s - ps + 1) / (n - pn + 2)
        return result


class LiveHistoryStore:
    """Append-only local provenance store; only captured availability accepted."""
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, timeout=30)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("""CREATE TABLE IF NOT EXISTS events(
            observation_key TEXT, task TEXT, available_at TEXT, payload TEXT, sha256 TEXT,
            PRIMARY KEY(observation_key,task,available_at))""")
        self.db.execute("CREATE INDEX IF NOT EXISTS events_asof ON events(task,available_at)")

    def append(self, events):
        count = 0
        with self.db:
            for event in events:
                if event.availability_kind != "captured":
                    raise ValueError("assumed availability cannot enter the live store")
                payload = json.dumps(event.payload(), sort_keys=True, separators=(",", ":"))
                identity = hashlib.sha256(payload.encode()).hexdigest()
                key = (event.observation_key, event.task, event.available_at.isoformat())
                old = self.db.execute("SELECT sha256 FROM events WHERE observation_key=? AND task=? AND available_at=?", key).fetchone()
                if old and old[0] != identity:
                    raise ValueError("conflicting captured event revision")
                if not old:
                    self.db.execute("INSERT INTO events VALUES (?,?,?,?,?)", (*key, payload, identity))
                    count += 1
        return count

    def events(self, task, as_of):
        result = []
        for payload, identity in self.db.execute("SELECT payload,sha256 FROM events WHERE task=? AND available_at<=? ORDER BY available_at,observation_key", (task, utc(as_of).isoformat())):
            if hashlib.sha256(payload.encode()).hexdigest() != identity:
                raise ValueError("live history payload integrity failure")
            result.append(HistoryEvent.parse(json.loads(payload)))
        return tuple(result)

    def close(self):
        self.db.close()
