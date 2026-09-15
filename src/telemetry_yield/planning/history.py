"""Convert local/SatNOGS observation outcomes into qualified model evidence."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Mapping, Sequence

from telemetry_yield.satnogs import SatNOGSClient

from .probability import ReceptionEvidence


def evidence_from_satnogs_observations(
    observations: Sequence[Mapping[str, object]],
) -> tuple[ReceptionEvidence, ...]:
    result: list[ReceptionEvidence] = []
    for observation in observations:
        raw_norad = observation.get("norad_cat_id")
        if (
            isinstance(raw_norad, bool)
            or not isinstance(raw_norad, int)
            or raw_norad <= 0
        ):
            continue
        station = observation.get("ground_station")
        if station is not None and (
            isinstance(station, bool)
            or not isinstance(station, int)
            or station <= 0
        ):
            raise ValueError("SatNOGS ground_station must be a positive integer or null")
        demoddata = observation.get("demoddata")
        decoded = isinstance(demoddata, list) and bool(demoddata)
        waterfall_status = observation.get("waterfall_status")
        # A positive packet artifact and an explicit no-signal review disagree.
        # Do not let that internally contradictory row update either posterior.
        if waterfall_status == "without-signal" and decoded:
            continue
        signal_confirmed = waterfall_status == "with-signal" or decoded
        signal_present = (
            True
            if signal_confirmed
            else False
            if waterfall_status == "without-signal"
            else None
        )
        transmitter_state = "confirmed" if signal_confirmed else "unknown"
        # Empty demoddata becomes a qualified failure only with independent
        # signal confirmation.  Otherwise upload gaps and no-TX passes stay unknown.
        decoded_outcome: bool | None = (
            decoded if signal_confirmed and isinstance(demoddata, list) else None
        )
        result.append(
            ReceptionEvidence(
                norad_id=raw_norad,
                station_id=str(station) if station is not None else None,
                resource_id=None,
                listened=True,
                transmitter_state=transmitter_state,
                decoded=decoded_outcome,
                source="satnogs",
                signal_present=signal_present,
            )
        )
    return tuple(result)


class SatnogsHistorySource:
    """Read-only history adapter using the project's bounded SatNOGS client."""

    def __init__(self, client: SatNOGSClient, *, maximum_pages: int = 100) -> None:
        if isinstance(maximum_pages, bool) or maximum_pages <= 0:
            raise ValueError("maximum_pages must be a positive integer")
        self.client = client
        self.maximum_pages = maximum_pages

    @staticmethod
    def _next_link(headers: Mapping[str, str]) -> str | None:
        link = next(
            (value for key, value in headers.items() if key.casefold() == "link"),
            None,
        )
        if not link:
            return None
        for part in link.split(","):
            section = part.strip()
            if 'rel="next"' in section or "rel=next" in section:
                if not section.startswith("<") or ">" not in section:
                    raise ValueError("malformed SatNOGS history pagination link")
                return section[1 : section.index(">")]
        return None

    def load(
        self,
        norad_id: int,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
        ground_station: int | None = None,
    ) -> tuple[ReceptionEvidence, ...]:
        params: dict[str, object] = {"norad_cat_id": norad_id}
        if start is not None:
            params["start"] = start.isoformat()
        if end is not None:
            params["end"] = end.isoformat()
        if ground_station is not None:
            params["ground_station"] = ground_station
        response = self.client.get("observations/", params=params)
        rows_by_id: dict[int, Mapping[str, object]] = {}
        for page in range(1, self.maximum_pages + 1):
            try:
                payload = json.loads(response.body.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValueError("SatNOGS observations response must be JSON") from exc
            if not isinstance(payload, list) or any(
                not isinstance(item, Mapping) for item in payload
            ):
                raise ValueError("SatNOGS observations response must be a list of objects")
            for item in payload:
                identifier = item.get("id")
                if isinstance(identifier, bool) or not isinstance(identifier, int):
                    raise ValueError("SatNOGS history observation requires an integer id")
                previous = rows_by_id.get(identifier)
                if previous is not None and json.dumps(
                    previous, sort_keys=True, separators=(",", ":"), default=str
                ) != json.dumps(item, sort_keys=True, separators=(",", ":"), default=str):
                    raise ValueError("conflicting duplicate in SatNOGS history")
                rows_by_id[identifier] = item
            next_url = self._next_link(response.headers)
            if next_url is None:
                return evidence_from_satnogs_observations(
                    [rows_by_id[key] for key in sorted(rows_by_id)]
                )
            if page == self.maximum_pages:
                break
            response = self.client.get(next_url)
        raise ValueError(
            f"SatNOGS history exceeded the explicit {self.maximum_pages}-page bound"
        )


def evidence_from_local_rows(
    rows: Sequence[Mapping[str, object]],
) -> tuple[ReceptionEvidence, ...]:
    """Map application DB rows with explicit observability fields.

    Required keys are ``norad_id``, ``listened`` and ``transmitter_state``;
    ``decoded`` may be null.  No implicit negative is inferred.
    """

    result: list[ReceptionEvidence] = []
    for row in rows:
        norad_id = row.get("norad_id")
        listened = row.get("listened")
        decoded = row.get("decoded")
        signal_present = row.get("signal_present")
        if not isinstance(listened, bool):
            raise ValueError("local listened must be boolean")
        if (
            isinstance(norad_id, bool)
            or not isinstance(norad_id, int)
            or norad_id <= 0
        ):
            raise ValueError("local norad_id must be a positive integer")
        for name, value in (
            ("decoded", decoded),
            ("signal_present", signal_present),
        ):
            if value is not None and not isinstance(value, bool):
                raise ValueError(f"local {name} must be boolean or null")
        result.append(
            ReceptionEvidence(
                norad_id=norad_id,
                station_id=(
                    str(row["station_id"]) if row.get("station_id") is not None else None
                ),
                resource_id=(
                    str(row["resource_id"]) if row.get("resource_id") is not None else None
                ),
                listened=listened,
                transmitter_state=str(row["transmitter_state"]),  # type: ignore[arg-type]
                decoded=decoded,  # type: ignore[arg-type]
                source="local",
                weight=float(row.get("weight", 1.0)),
                signal_present=signal_present,  # type: ignore[arg-type]
            )
        )
    return tuple(result)
