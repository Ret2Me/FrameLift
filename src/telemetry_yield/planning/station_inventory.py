"""Current SatNOGS station/antenna inventory for production opportunity building."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Callable, Mapping, Sequence

from telemetry_yield.satnogs import SATNOGS_NETWORK_API, SatNOGSClient

from .models import AntennaSpec, GroundStation, ReceiverResource, as_utc


class StationInventoryError(ValueError):
    pass


def _required_number(payload: Mapping[str, object], name: str) -> float:
    value = payload.get(name)
    if isinstance(value, bool):
        raise StationInventoryError(f"station {name} must be numeric")
    try:
        result = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise StationInventoryError(f"station {name} must be numeric") from exc
    if not math.isfinite(result):
        raise StationInventoryError(f"station {name} must be finite")
    return result


def station_from_satnogs(
    payload: Mapping[str, object], *, allow_testing: bool = False
) -> GroundStation:
    """Parse one current station snapshot without inventing radio/antenna gain."""

    raw_station_id = payload.get("id")
    if (
        isinstance(raw_station_id, bool)
        or not isinstance(raw_station_id, int)
        or raw_station_id <= 0
    ):
        raise StationInventoryError("station id must be a positive integer")
    station_id = raw_station_id
    if payload.get("testing") is True and not allow_testing:
        raise StationInventoryError(f"SatNOGS station {station_id} is in testing mode")
    if payload.get("is_connected") is not True:
        raise StationInventoryError(f"SatNOGS station {station_id} is not connected")
    if payload.get("is_available") is not True:
        raise StationInventoryError(f"SatNOGS station {station_id} is unavailable")

    antennas = payload.get("antenna")
    ranges: set[tuple[float, float]] = set()
    types: set[str] = set()
    if antennas is not None:
        if not isinstance(antennas, list):
            raise StationInventoryError("station antenna must be a list")
        for item in antennas:
            if not isinstance(item, Mapping):
                raise StationInventoryError("station antenna entry must be an object")
            low = _required_number(item, "frequency")
            high = _required_number(item, "frequency_max")
            if low <= 0 or high < low:
                raise StationInventoryError("station antenna frequency range is invalid")
            ranges.add((low, high))
            name = item.get("antenna_type_name") or item.get("antenna_type")
            if isinstance(name, str) and name.strip():
                types.add(name.strip())
    antenna = (
        AntennaSpec(
            antenna_id=(
                f"satnogs-{station_id}:" + "+".join(sorted(types))
                if types
                else f"satnogs-{station_id}:published-ranges"
            ),
            frequency_ranges_hz=tuple(sorted(ranges)),
            gain_dbi=None,
            system_noise_temperature_k=None,
        )
        if ranges
        else None
    )
    horizon = payload.get("min_horizon")
    minimum_elevation = (
        5.0 if horizon is None else _required_number(payload, "min_horizon")
    )
    name = payload.get("name")
    station_name = str(name).strip() if name is not None else ""
    return GroundStation(
        station_id=f"satnogs-{station_id}:{station_name or 'unnamed'}",
        latitude_deg=_required_number(payload, "lat"),
        longitude_deg=_required_number(payload, "lng"),
        altitude_m=_required_number(payload, "altitude"),
        resources=(
            ReceiverResource(
                resource_id="satnogs-network-receiver",
                antenna=antenna,
                capacity=1,
            ),
        ),
        minimum_elevation_deg=minimum_elevation,
        satnogs_station_id=station_id,
    )


@dataclass(frozen=True, slots=True)
class SatnogsStationInventorySnapshot:
    retrieved_at: datetime
    source: str
    current_configuration_only: bool
    stations: tuple[GroundStation, ...]
    evidence_sha256_by_station: tuple[tuple[int, str], ...]
    excluded_station_ids: tuple[int, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "retrieved_at", as_utc(self.retrieved_at, name="retrieved_at")
        )
        if not isinstance(self.source, str) or not self.source.strip():
            raise ValueError("inventory source is required")
        if not isinstance(self.current_configuration_only, bool):
            raise ValueError("current_configuration_only must be boolean")
        identifiers = [station.station_id for station in self.stations]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("inventory station IDs must be unique")
        evidence_ids = [item[0] for item in self.evidence_sha256_by_station]
        expected_ids = sorted(
            station.satnogs_station_id
            for station in self.stations
            if station.satnogs_station_id is not None
        )
        if sorted(evidence_ids) != expected_ids or any(
            isinstance(station_id, bool)
            or not isinstance(station_id, int)
            or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
            for station_id, digest in self.evidence_sha256_by_station
        ):
            raise ValueError("inventory evidence must bind every station response")
        if (
            len(set(self.excluded_station_ids)) != len(self.excluded_station_ids)
            or any(station_id <= 0 for station_id in self.excluded_station_ids)
            or set(self.excluded_station_ids) & set(expected_ids)
        ):
            raise ValueError("excluded station IDs must be unique and disjoint")


class SatnogsStationInventoryProvider:
    def __init__(
        self,
        client: SatNOGSClient,
        *,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        if client.base_url != SATNOGS_NETWORK_API:
            raise StationInventoryError("station inventory requires the official Network API")
        self._client = client
        self._now = now

    def fetch(
        self,
        station_ids: Sequence[int],
        *,
        allow_testing: bool = False,
        skip_unavailable: bool = False,
    ) -> SatnogsStationInventorySnapshot:
        if not station_ids or any(
            isinstance(value, bool) or not isinstance(value, int) or value <= 0
            for value in station_ids
        ):
            raise StationInventoryError("station IDs must be non-empty positive integers")
        if len(set(station_ids)) != len(station_ids):
            raise StationInventoryError("station IDs must be unique")
        stations: list[GroundStation] = []
        evidence: list[tuple[int, str]] = []
        excluded: list[int] = []
        for station_id in station_ids:
            response = self._client.get(f"stations/{station_id}/")
            try:
                payload = json.loads(response.body.decode("utf-8"))
            except (AttributeError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise StationInventoryError(
                    f"SatNOGS returned invalid JSON for station {station_id}"
                ) from exc
            if not isinstance(payload, Mapping) or payload.get("id") != station_id:
                raise StationInventoryError(
                    f"SatNOGS returned the wrong station for {station_id}"
                )
            if skip_unavailable and (
                payload.get("is_connected") is not True
                or payload.get("is_available") is not True
            ):
                excluded.append(station_id)
                continue
            stations.append(station_from_satnogs(payload, allow_testing=allow_testing))
            evidence.append((station_id, hashlib.sha256(response.body).hexdigest()))
        if not stations:
            raise StationInventoryError("no selected SatNOGS station is currently available")
        return SatnogsStationInventorySnapshot(
            retrieved_at=as_utc(self._now(), name="now"),
            source=SATNOGS_NETWORK_API,
            current_configuration_only=True,
            stations=tuple(stations),
            evidence_sha256_by_station=tuple(sorted(evidence)),
            excluded_station_ids=tuple(sorted(excluded)),
        )
