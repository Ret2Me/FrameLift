"""Conservative routing from an observation catalogue to SatYAML profiles.

Names and free-text modes are never used to establish identity.  The routing
chain is satellite UUID -> explicitly bounded frequency -> unique NORAD ->
unambiguous SatYAML registry profile -> profile transmitter frequency.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
import json
import math
from pathlib import Path
from typing import Iterable, Literal, Mapping, Sequence, TypeAlias

from .gr_satellites_backend import GrSatellitesProfile, GrSatellitesTransmitter
from .models import JsonValue
from .satyaml_registry import SatYamlRegistry


RoutingStatus: TypeAlias = Literal[
    "routed",
    "unmatched_satellite_uuid",
    "unmatched_catalog_frequency",
    "ambiguous_catalog_norad",
    "unmatched_satyaml_profile",
    "ambiguous_satyaml_profile",
    "unmatched_profile_frequency",
]


@dataclass(frozen=True, slots=True)
class CatalogObservation:
    observation_id: int
    satellite_id: str
    frequency_hz: float
    mode: str | None
    iq_url: str

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
        if self.mode is not None and (
            not isinstance(self.mode, str) or not self.mode.strip()
        ):
            raise ValueError("mode must be a non-empty string or None")
        if (
            isinstance(self.frequency_hz, bool)
            or not isinstance(self.frequency_hz, (int, float))
            or not math.isfinite(float(self.frequency_hz))
            or self.frequency_hz <= 0
        ):
            raise ValueError("frequency_hz must be finite and positive")
        object.__setattr__(self, "frequency_hz", float(self.frequency_hz))


@dataclass(frozen=True, slots=True)
class CatalogTransmitter:
    transmitter_uuid: str
    satellite_id: str
    norad_id: int
    frequency_hz: float
    mode: str | None
    baud: float | None
    status: str | None

    def __post_init__(self) -> None:
        for name in ("transmitter_uuid", "satellite_id"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be a non-empty string")
        if (
            isinstance(self.norad_id, bool)
            or not isinstance(self.norad_id, int)
            or self.norad_id <= 0
        ):
            raise ValueError("norad_id must be a positive integer")
        for name in ("frequency_hz", "baud"):
            value = getattr(self, name)
            if value is None and name == "baud":
                continue
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(float(value))
                or value <= 0
            ):
                raise ValueError(f"{name} must be finite and positive")
            object.__setattr__(self, name, float(value))
        for name in ("mode", "status"):
            value = getattr(self, name)
            if value is not None and (
                not isinstance(value, str) or not value.strip()
            ):
                raise ValueError(f"{name} must be a non-empty string or None")

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "transmitter_uuid": self.transmitter_uuid,
            "satellite_id": self.satellite_id,
            "norad_id": self.norad_id,
            "frequency_hz": self.frequency_hz,
            "mode": self.mode,
            "baud": self.baud,
            "status": self.status,
        }


@dataclass(frozen=True, slots=True)
class ParameterDisagreement:
    field: Literal["mode", "baud"]
    observed: tuple[str, ...]
    profile: tuple[str, ...]
    comparison: str = "exact_no_aliases"

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "field": self.field,
            "observed": list(self.observed),
            "profile": list(self.profile),
            "comparison": self.comparison,
        }


@dataclass(frozen=True, slots=True)
class ObservationRoute:
    observation: CatalogObservation
    status: RoutingStatus
    detail: str
    frequency_tolerance_hz: float
    catalog_transmitters: tuple[CatalogTransmitter, ...] = ()
    norad_id: int | None = None
    profile: GrSatellitesProfile | None = None
    profile_transmitter_ids: tuple[str, ...] = ()
    disagreements: tuple[ParameterDisagreement, ...] = ()

    @property
    def routable(self) -> bool:
        return self.status == "routed"

    @property
    def matched_profile_transmitters(self) -> tuple[GrSatellitesTransmitter, ...]:
        if self.profile is None:
            return ()
        identifiers = set(self.profile_transmitter_ids)
        return tuple(
            item for item in self.profile.transmitters if item.transmitter_id in identifiers
        )

    def to_dict(self) -> dict[str, JsonValue]:
        capability: dict[str, JsonValue] | None = None
        if self.profile is not None:
            matched = self.matched_profile_transmitters
            capability = {
                "name": self.profile.name,
                "norad_id": self.profile.norad_id,
                "selector": self.profile.selector,
                "profile_modulations": list(self.profile.modulations),
                "profile_framing": list(self.profile.framing),
                "profile_fec": list(self.profile.fec),
                "frequency_matched_transmitter_ids": list(
                    self.profile_transmitter_ids
                ),
                "frequency_matched_modulations": sorted(
                    {item.modulation for item in matched}
                ),
                "frequency_matched_framing": sorted(
                    {item.framing for item in matched}
                ),
                "frequency_matched_fec": sorted(
                    {fec for item in matched for fec in item.fec}
                ),
            }
        return {
            "observation_id": self.observation.observation_id,
            "satellite_id": self.observation.satellite_id,
            "frequency_hz": self.observation.frequency_hz,
            "mode": self.observation.mode,
            "iq_url": self.observation.iq_url,
            "status": self.status,
            "routable": self.routable,
            "detail": self.detail,
            "frequency_tolerance_hz": self.frequency_tolerance_hz,
            "catalog_transmitters": [
                item.to_dict() for item in self.catalog_transmitters
            ],
            "norad_id": self.norad_id,
            "capability_profile": capability,
            "disagreements": [item.to_dict() for item in self.disagreements],
        }


@dataclass(frozen=True, slots=True)
class CatalogRoutingResult:
    routes: tuple[ObservationRoute, ...]
    frequency_tolerance_hz: float

    def __post_init__(self) -> None:
        routes = tuple(sorted(self.routes, key=lambda route: route.observation.observation_id))
        if len({route.observation.observation_id for route in routes}) != len(routes):
            raise ValueError("observation IDs must be unique")
        object.__setattr__(self, "routes", routes)

    def summary(self) -> dict[str, JsonValue]:
        status_counts = Counter(route.status for route in self.routes)
        routable = [route for route in self.routes if route.routable]
        modulation_counts: Counter[str] = Counter()
        framing_counts: Counter[str] = Counter()
        profile_counts: Counter[str] = Counter()
        for route in routable:
            assert route.profile is not None
            profile_counts[route.profile.name or route.profile.selector] += 1
            matched = route.matched_profile_transmitters
            for modulation in {item.modulation for item in matched}:
                modulation_counts[modulation] += 1
            for framing in {item.framing for item in matched}:
                framing_counts[framing] += 1
        definite_profile_work = {
            "unmatched_satyaml_profile",
            "unmatched_profile_frequency",
        }
        metadata_blocked = {
            "unmatched_satellite_uuid",
            "unmatched_catalog_frequency",
            "ambiguous_catalog_norad",
            "ambiguous_satyaml_profile",
        }
        profile_work_routes = [
            route for route in self.routes if route.status in definite_profile_work
        ]
        metadata_blocked_routes = [
            route for route in self.routes if route.status in metadata_blocked
        ]
        return {
            "observation_count": len(self.routes),
            "existing_profile_routable_count": len(routable),
            "not_routable_to_existing_profile_count": len(self.routes) - len(routable),
            "definite_new_or_updated_profile_count": sum(
                route.status in definite_profile_work for route in self.routes
            ),
            "definite_new_or_updated_profile_unique_norad_count": len(
                {route.norad_id for route in profile_work_routes if route.norad_id}
            ),
            "metadata_or_ambiguity_blocked_count": sum(
                route.status in metadata_blocked for route in self.routes
            ),
            "metadata_or_ambiguity_blocked_unique_satellite_count": len(
                {route.observation.satellite_id for route in metadata_blocked_routes}
            ),
            "mode_disagreement_count": sum(
                any(item.field == "mode" for item in route.disagreements)
                for route in self.routes
            ),
            "baud_disagreement_count": sum(
                any(item.field == "baud" for item in route.disagreements)
                for route in self.routes
            ),
            "status_counts": dict(sorted(status_counts.items())),
            "modulation_distribution": dict(sorted(modulation_counts.items())),
            "framing_distribution": dict(sorted(framing_counts.items())),
            "profile_distribution": dict(sorted(profile_counts.items())),
            "routable_unique_profile_count": len(profile_counts),
            "frequency_tolerance_hz": self.frequency_tolerance_hz,
            "coverage_caveat": (
                "routing proves metadata compatibility with an installed profile; "
                "it does not prove that the recording contains a signal or that all "
                "runtime blocks decode it successfully"
            ),
        }

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "schema_version": 1,
            "match_policy": {
                "satellite_identity": "exact_satellite_uuid",
                "catalog_frequency_field": "downlink_low",
                "profile_identity": "exact_unique_norad",
                "profile_frequency_field": "transmitters.*.frequency",
                "frequency_tolerance_hz": self.frequency_tolerance_hz,
                "name_matching": False,
                "mode_aliases": False,
            },
            "summary": self.summary(),
            "routes": [route.to_dict() for route in self.routes],
        }


def _within(left: float, right: float, tolerance_hz: float) -> bool:
    return abs(left - right) <= tolerance_hz


def _profile_frequency(
    transmitter: GrSatellitesTransmitter,
) -> float | None:
    value = transmitter.metadata.get("frequency")
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        or value <= 0
    ):
        return None
    return float(value)


def _baud_values(
    transmitters: Sequence[GrSatellitesTransmitter],
) -> tuple[float, ...]:
    values: set[float] = set()
    for transmitter in transmitters:
        value = transmitter.metadata.get("baudrate")
        if (
            not isinstance(value, bool)
            and isinstance(value, (int, float))
            and math.isfinite(float(value))
            and value > 0
        ):
            values.add(float(value))
    return tuple(sorted(values))


def _disagreements(
    observation: CatalogObservation,
    catalog: Sequence[CatalogTransmitter],
    profile: Sequence[GrSatellitesTransmitter],
) -> tuple[ParameterDisagreement, ...]:
    result: list[ParameterDisagreement] = []
    observed_modes = {
        value.casefold(): value
        for value in [observation.mode, *(item.mode for item in catalog)]
        if value is not None
    }
    profile_modes = {
        item.modulation.casefold(): item.modulation for item in profile
    }
    if observed_modes and profile_modes and not set(observed_modes).issubset(
        profile_modes
    ):
        result.append(
            ParameterDisagreement(
                field="mode",
                observed=tuple(sorted(observed_modes.values(), key=str.casefold)),
                profile=tuple(sorted(profile_modes.values(), key=str.casefold)),
            )
        )
    catalog_bauds = tuple(sorted({item.baud for item in catalog if item.baud is not None}))
    profile_bauds = _baud_values(profile)
    if catalog_bauds and profile_bauds and any(
        not any(
            math.isclose(left, right, rel_tol=1e-9, abs_tol=0.0)
            for right in profile_bauds
        )
        for left in catalog_bauds
    ):
        result.append(
            ParameterDisagreement(
                field="baud",
                observed=tuple(format(value, ".17g") for value in catalog_bauds),
                profile=tuple(format(value, ".17g") for value in profile_bauds),
            )
        )
    return tuple(result)


def route_catalog_observations(
    observations: Sequence[CatalogObservation],
    transmitters: Sequence[CatalogTransmitter],
    registry: SatYamlRegistry,
    *,
    frequency_tolerance_hz: float = 0.0,
) -> CatalogRoutingResult:
    """Build a fail-closed plan without executing any demodulator."""

    if (
        isinstance(frequency_tolerance_hz, bool)
        or not isinstance(frequency_tolerance_hz, (int, float))
        or not math.isfinite(float(frequency_tolerance_hz))
        or frequency_tolerance_hz < 0
    ):
        raise ValueError("frequency_tolerance_hz must be finite and non-negative")
    tolerance = float(frequency_tolerance_hz)
    by_satellite: dict[str, list[CatalogTransmitter]] = defaultdict(list)
    for transmitter in transmitters:
        if not isinstance(transmitter, CatalogTransmitter):
            raise TypeError("transmitters must contain CatalogTransmitter objects")
        by_satellite[transmitter.satellite_id].append(transmitter)
    for values in by_satellite.values():
        values.sort(key=lambda item: item.transmitter_uuid)

    routes: list[ObservationRoute] = []
    for observation in observations:
        if not isinstance(observation, CatalogObservation):
            raise TypeError("observations must contain CatalogObservation objects")
        satellite_matches = by_satellite.get(observation.satellite_id, [])
        if not satellite_matches:
            routes.append(
                ObservationRoute(
                    observation,
                    "unmatched_satellite_uuid",
                    "no transmitter has the exact satellite UUID",
                    tolerance,
                )
            )
            continue
        frequency_matches = tuple(
            item
            for item in satellite_matches
            if _within(item.frequency_hz, observation.frequency_hz, tolerance)
        )
        if not frequency_matches:
            available = ", ".join(
                format(value, ".17g")
                for value in sorted({item.frequency_hz for item in satellite_matches})
            )
            routes.append(
                ObservationRoute(
                    observation,
                    "unmatched_catalog_frequency",
                    f"no exact/bounded frequency match; available: {available}",
                    tolerance,
                )
            )
            continue
        norads = sorted({item.norad_id for item in frequency_matches})
        if len(norads) != 1:
            routes.append(
                ObservationRoute(
                    observation,
                    "ambiguous_catalog_norad",
                    f"frequency-matched transmitters disagree on NORAD: {norads}",
                    tolerance,
                    catalog_transmitters=frequency_matches,
                )
            )
            continue
        norad = norads[0]
        if norad in registry.conflicted_norads:
            routes.append(
                ObservationRoute(
                    observation,
                    "ambiguous_satyaml_profile",
                    "SatYAML registry has more than one profile for this NORAD",
                    tolerance,
                    catalog_transmitters=frequency_matches,
                    norad_id=norad,
                )
            )
            continue
        profile = registry.get_by_norad(norad)
        if profile is None:
            routes.append(
                ObservationRoute(
                    observation,
                    "unmatched_satyaml_profile",
                    "SatYAML registry has no profile for the exact NORAD",
                    tolerance,
                    catalog_transmitters=frequency_matches,
                    norad_id=norad,
                )
            )
            continue
        profile_matches = tuple(
            item
            for item in profile.transmitters
            if (frequency := _profile_frequency(item)) is not None
            and _within(frequency, observation.frequency_hz, tolerance)
        )
        disagreements = _disagreements(
            observation, frequency_matches, profile_matches or profile.transmitters
        )
        if not profile_matches:
            routes.append(
                ObservationRoute(
                    observation,
                    "unmatched_profile_frequency",
                    "the NORAD profile has no transmitter at the exact/bounded frequency",
                    tolerance,
                    catalog_transmitters=frequency_matches,
                    norad_id=norad,
                    profile=profile,
                    disagreements=disagreements,
                )
            )
            continue
        routes.append(
            ObservationRoute(
                observation,
                "routed",
                "exact UUID, bounded frequency, unique NORAD, and profile frequency",
                tolerance,
                catalog_transmitters=frequency_matches,
                norad_id=norad,
                profile=profile,
                profile_transmitter_ids=tuple(
                    sorted(item.transmitter_id for item in profile_matches)
                ),
                disagreements=disagreements,
            )
        )
    return CatalogRoutingResult(tuple(routes), tolerance)


def load_catalog_iq_observations(
    path: str | Path,
    *,
    frames_recovered: bool | None = None,
    mode: str | None = None,
    frame_count: int | None = None,
) -> tuple[CatalogObservation, ...]:
    """Load IQ observations using explicit catalogue outcome filters.

    Filters are exact and independent: no mode aliases, names, or inferred
    frame outcome are used.  ``None`` leaves that field unrestricted.
    """

    if frames_recovered is not None and not isinstance(frames_recovered, bool):
        raise TypeError("frames_recovered must be a boolean or None")
    if mode is not None and (not isinstance(mode, str) or not mode.strip()):
        raise ValueError("mode must be a non-empty string or None")
    if frame_count is not None and (
        not isinstance(frame_count, int)
        or isinstance(frame_count, bool)
        or frame_count < 0
    ):
        raise ValueError("frame_count must be a non-negative integer or None")
    document = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(document, Mapping) or not isinstance(
        document.get("observations"), list
    ):
        raise ValueError("catalogue must contain an observations list")
    output: list[CatalogObservation] = []
    seen: set[int] = set()
    for index, raw in enumerate(document["observations"]):
        if not isinstance(raw, Mapping):
            raise ValueError(f"observation {index} must be an object")
        if (
            frames_recovered is not None
            and raw.get("frames_recovered") is not frames_recovered
        ):
            continue
        if mode is not None and raw.get("mode") != mode:
            continue
        if frame_count is not None and raw.get("frame_count") != frame_count:
            continue
        links = raw.get("links")
        iq_url = links.get("iq") if isinstance(links, Mapping) else None
        if not isinstance(iq_url, str) or not iq_url:
            continue
        observation = CatalogObservation(
            observation_id=raw.get("observation_id"),
            satellite_id=raw.get("satellite_id"),
            frequency_hz=raw.get("frequency_hz"),
            mode=raw.get("mode"),
            iq_url=iq_url,
        )
        if observation.observation_id in seen:
            raise ValueError(f"duplicate observation ID {observation.observation_id}")
        seen.add(observation.observation_id)
        output.append(observation)
    return tuple(sorted(output, key=lambda item: item.observation_id))


def load_zero_frame_iq_observations(path: str | Path) -> tuple[CatalogObservation, ...]:
    """Load only catalogue observations with zero frames and an IQ link."""

    return load_catalog_iq_observations(path, frame_count=0)


def load_positive_g3ruh_iq_observations(
    path: str | Path,
) -> tuple[CatalogObservation, ...]:
    """Load exact positive-reference FSK AX.25 G3RUH IQ observations."""

    return load_catalog_iq_observations(
        path,
        frames_recovered=True,
        mode="FSK AX.25 G3RUH",
    )


def load_catalog_transmitters(
    paths: Iterable[str | Path],
) -> tuple[CatalogTransmitter, ...]:
    """Load nominal downlink frequencies from SatNOGS transmitter snapshots."""

    output: list[CatalogTransmitter] = []
    seen: set[str] = set()
    for path in sorted((Path(path) for path in paths), key=str):
        records = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(records, list):
            raise ValueError(f"transmitter snapshot is not a list: {path}")
        for index, raw in enumerate(records):
            if not isinstance(raw, Mapping):
                raise ValueError(f"transmitter {index} in {path} is not an object")
            frequency = raw.get("downlink_low")
            if frequency is None:
                continue
            uuid = raw.get("uuid")
            if not isinstance(uuid, str) or not uuid:
                raise ValueError(f"transmitter {index} in {path} has no UUID")
            if uuid in seen:
                raise ValueError(f"duplicate transmitter UUID {uuid}")
            seen.add(uuid)
            baud = raw.get("baud")
            # SatNOGS snapshots use both null and 0.0 for an unspecified baud.
            if isinstance(baud, (int, float)) and not isinstance(baud, bool) and baud <= 0:
                baud = None
            output.append(
                CatalogTransmitter(
                    transmitter_uuid=uuid,
                    satellite_id=raw.get("sat_id"),
                    norad_id=raw.get("norad_cat_id"),
                    frequency_hz=frequency,
                    mode=raw.get("mode"),
                    baud=baud,
                    status=raw.get("status"),
                )
            )
    return tuple(sorted(output, key=lambda item: item.transmitter_uuid))
