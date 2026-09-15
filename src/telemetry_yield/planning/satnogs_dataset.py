"""Bounded, auditable acquisition of a frozen SatNOGS publication cohort."""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Callable, Mapping

from telemetry_yield.satnogs import (
    SATNOGS_NETWORK_API,
    SatNOGSClient,
    SatNOGSError,
    SatNOGSHTTPError,
    SatNOGSResponseTooLarge,
)

from .dataset import DatasetIntegrityError, parse_api_datetime
from .models import as_utc


SATNOGS_DATA_LICENSE = "CC-BY-SA-4.0"
SATNOGS_DATA_LICENSE_URL = "https://creativecommons.org/licenses/by-sa/4.0/"


def _utc_text(value: datetime) -> str:
    return as_utc(value).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True, slots=True)
class PublicationTarget:
    norad_id: int
    selection_transmitter_uuid: str
    selection_mode: str
    selection_total_count: int
    selection_good_count: int
    selection_bad_count: int

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "PublicationTarget":
        return cls(
            norad_id=int(value["norad_id"]),
            selection_transmitter_uuid=str(value["selection_transmitter_uuid"]),
            selection_mode=str(value["selection_mode"]),
            selection_total_count=int(value["selection_total_count"]),
            selection_good_count=int(value["selection_good_count"]),
            selection_bad_count=int(value["selection_bad_count"]),
        )


@dataclass(frozen=True, slots=True)
class PublicationAcquisitionWindow:
    """A predeclared target-specific override of the default cohort interval."""

    norad_id: int
    start: datetime
    end: datetime
    transmitter_uuid: str | None = None
    waterfall_status: int = -1

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "PublicationAcquisitionWindow":
        return cls(
            norad_id=int(value["norad_id"]),
            start=parse_api_datetime(value["start"], name="acquisition_window.start"),
            end=parse_api_datetime(value["end"], name="acquisition_window.end"),
            transmitter_uuid=(
                str(value["transmitter_uuid"])
                if value.get("transmitter_uuid") is not None
                else None
            ),
            waterfall_status=int(value.get("waterfall_status", -1)),
        )


@dataclass(frozen=True, slots=True)
class PublicationSamplingInterval:
    """An outcome-blind UTC interval applied identically to every target."""

    start: datetime
    end: datetime

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "PublicationSamplingInterval":
        return cls(
            start=parse_api_datetime(value["start"], name="sampling_interval.start"),
            end=parse_api_datetime(value["end"], name="sampling_interval.end"),
        )


@dataclass(frozen=True, slots=True)
class PublicationDatasetConfig:
    schema_version: str
    study_id: str
    frozen_at: datetime
    start: datetime
    end: datetime
    targets: tuple[PublicationTarget, ...]
    geometry_step_seconds: int
    minimum_chunk_hours: int
    max_response_bytes: int
    random_seed: int
    bootstrap_replicates: int
    temporal_test_fraction: float
    regularization_grid: tuple[float, ...]
    user_agent: str
    sampling_intervals: tuple[PublicationSamplingInterval, ...] = ()
    acquisition_windows: tuple[PublicationAcquisitionWindow, ...] = ()
    supplemental_acquisition_windows: tuple[PublicationAcquisitionWindow, ...] = ()
    external_validation_norad_ids: tuple[int, ...] = ()
    external_station_holdout_fraction: float = 0.0
    minimum_external_test_rows: int = 10

    @classmethod
    def load(cls, path: Path) -> "PublicationDatasetConfig":
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, Mapping):
            raise DatasetIntegrityError("publication config must be a JSON object")
        config = cls(
            schema_version=str(payload["schema_version"]),
            study_id=str(payload["study_id"]),
            frozen_at=parse_api_datetime(payload["frozen_at"], name="frozen_at"),
            start=parse_api_datetime(payload["start"], name="start"),
            end=parse_api_datetime(payload["end"], name="end"),
            targets=tuple(
                PublicationTarget.from_dict(item)
                for item in payload["targets"]  # type: ignore[union-attr]
            ),
            geometry_step_seconds=int(payload["geometry_step_seconds"]),
            minimum_chunk_hours=int(payload["minimum_chunk_hours"]),
            max_response_bytes=int(payload["max_response_bytes"]),
            random_seed=int(payload["random_seed"]),
            bootstrap_replicates=int(payload["bootstrap_replicates"]),
            temporal_test_fraction=float(payload["temporal_test_fraction"]),
            regularization_grid=tuple(float(x) for x in payload["regularization_grid"]),  # type: ignore[union-attr]
            user_agent=str(payload["user_agent"]),
            sampling_intervals=tuple(
                PublicationSamplingInterval.from_dict(item)
                for item in payload.get("sampling_intervals", [])  # type: ignore[union-attr]
            ),
            acquisition_windows=tuple(
                PublicationAcquisitionWindow.from_dict(item)
                for item in payload.get("acquisition_windows", [])  # type: ignore[union-attr]
            ),
            supplemental_acquisition_windows=tuple(
                PublicationAcquisitionWindow.from_dict(item)
                for item in payload.get("supplemental_acquisition_windows", [])  # type: ignore[union-attr]
            ),
            external_validation_norad_ids=tuple(
                int(value)
                for value in payload.get("external_validation_norad_ids", [])  # type: ignore[union-attr]
            ),
            external_station_holdout_fraction=float(
                payload.get("external_station_holdout_fraction", 0.0)
            ),
            minimum_external_test_rows=int(
                payload.get("minimum_external_test_rows", 10)
            ),
        )
        config.validate()
        return config

    def validate(self) -> None:
        if self.schema_version != "observation-planning-publication-config-v1":
            raise DatasetIntegrityError("unsupported publication config schema")
        if not self.study_id or not self.user_agent.strip():
            raise DatasetIntegrityError("study_id and user_agent are required")
        if self.end <= self.start:
            raise DatasetIntegrityError("invalid frozen/evaluation time boundary")
        if self.end > self.frozen_at:
            raise DatasetIntegrityError("evaluation interval must end no later than frozen_at")
        if len(self.targets) < 5:
            raise DatasetIntegrityError("publication cohort requires at least five targets")
        ids = [target.norad_id for target in self.targets]
        if any(value <= 0 for value in ids) or len(ids) != len(set(ids)):
            raise DatasetIntegrityError("target NORAD IDs must be unique and positive")
        if self.geometry_step_seconds <= 0 or self.minimum_chunk_hours <= 0:
            raise DatasetIntegrityError("chunk and geometry steps must be positive")
        if self.max_response_bytes <= 0 or self.bootstrap_replicates <= 0:
            raise DatasetIntegrityError("response bound and bootstrap count must be positive")
        if not 0 < self.temporal_test_fraction < 0.5:
            raise DatasetIntegrityError("temporal_test_fraction must be in (0, 0.5)")
        if not self.regularization_grid or any(value <= 0 for value in self.regularization_grid):
            raise DatasetIntegrityError("regularization grid must be positive")
        target_ids = set(ids)
        intervals = self.sampling_intervals
        if any(
            interval.end <= interval.start
            or interval.start < self.start
            or interval.end > self.end
            for interval in intervals
        ):
            raise DatasetIntegrityError(
                "sampling intervals must be positive and inside the cohort boundary"
            )
        if tuple(sorted(intervals, key=lambda item: (item.start, item.end))) != intervals:
            raise DatasetIntegrityError("sampling intervals must be chronological")
        if any(
            current.start < previous.end
            for previous, current in zip(intervals, intervals[1:])
        ):
            raise DatasetIntegrityError("sampling intervals must not overlap")
        window_ids = [window.norad_id for window in self.acquisition_windows]
        if len(window_ids) != len(set(window_ids)):
            raise DatasetIntegrityError("acquisition-window NORAD IDs must be unique")
        all_windows = self.acquisition_windows + self.supplemental_acquisition_windows
        if any(window.norad_id not in target_ids for window in all_windows):
            raise DatasetIntegrityError("acquisition window references a non-target NORAD ID")
        if any(
            window.end <= window.start or window.end > self.frozen_at
            for window in all_windows
        ):
            raise DatasetIntegrityError("invalid target-specific acquisition window")
        target_by_id = {target.norad_id: target for target in self.targets}
        if any(
            window.waterfall_status not in {-1, 0, 1}
            or (
                window.transmitter_uuid is not None
                and window.transmitter_uuid
                != target_by_id[window.norad_id].selection_transmitter_uuid
            )
            for window in all_windows
        ):
            raise DatasetIntegrityError(
                "acquisition filters must use a valid waterfall stratum and the frozen transmitter"
            )
        supplemental_keys = [
            (
                window.norad_id,
                window.start,
                window.end,
                window.transmitter_uuid,
                window.waterfall_status,
            )
            for window in self.supplemental_acquisition_windows
        ]
        if len(supplemental_keys) != len(set(supplemental_keys)):
            raise DatasetIntegrityError("supplemental acquisition windows must be unique")
        external_ids = self.external_validation_norad_ids
        if len(external_ids) != len(set(external_ids)) or any(
            value not in target_ids for value in external_ids
        ):
            raise DatasetIntegrityError(
                "external validation NORAD IDs must be unique members of the target cohort"
            )
        if self.external_station_holdout_fraction != 0.0 and not (
            0 < self.external_station_holdout_fraction < 0.5
        ):
            raise DatasetIntegrityError(
                "external station holdout fraction must be zero or in (0, 0.5)"
            )
        if self.minimum_external_test_rows <= 0:
            raise DatasetIntegrityError("minimum external test rows must be positive")

    def acquisition_windows_for(
        self, norad_id: int
    ) -> tuple[PublicationAcquisitionWindow, ...]:
        override = next(
            (window for window in self.acquisition_windows if window.norad_id == norad_id),
            None,
        )
        if override is not None:
            primary = (override,)
        elif self.sampling_intervals:
            primary = tuple(
                PublicationAcquisitionWindow(
                    norad_id=norad_id,
                    start=interval.start,
                    end=interval.end,
                )
                for interval in self.sampling_intervals
            )
        else:
            primary = (PublicationAcquisitionWindow(norad_id, self.start, self.end),)
        supplements = tuple(
            window
            for window in self.supplemental_acquisition_windows
            if window.norad_id == norad_id
        )
        return (*primary, *supplements)

    def acquisition_window_for(self, norad_id: int) -> tuple[datetime, datetime]:
        """Backward-compatible access to the target's primary acquisition interval."""

        primary = self.acquisition_windows_for(norad_id)[0]
        return primary.start, primary.end


def publication_config_identity(config: PublicationDatasetConfig) -> str:
    """Hash every acquisition/evaluation field that defines the frozen cohort."""

    payload = {
        "schema_version": config.schema_version,
        "study_id": config.study_id,
        "frozen_at": _utc_text(config.frozen_at),
        "start": _utc_text(config.start),
        "end": _utc_text(config.end),
        "targets": [
            {
                "norad_id": target.norad_id,
                "selection_transmitter_uuid": target.selection_transmitter_uuid,
                "selection_mode": target.selection_mode,
                "selection_total_count": target.selection_total_count,
                "selection_good_count": target.selection_good_count,
                "selection_bad_count": target.selection_bad_count,
            }
            for target in config.targets
        ],
        "geometry_step_seconds": config.geometry_step_seconds,
        "minimum_chunk_hours": config.minimum_chunk_hours,
        "max_response_bytes": config.max_response_bytes,
        "random_seed": config.random_seed,
        "bootstrap_replicates": config.bootstrap_replicates,
        "temporal_test_fraction": config.temporal_test_fraction,
        "regularization_grid": config.regularization_grid,
        "user_agent": config.user_agent,
        "query_interval": "observation_start_half_open_all_labels_v1",
    }
    def serialized_windows(
        windows: tuple[PublicationAcquisitionWindow, ...],
    ) -> list[dict[str, object]]:
        values: list[dict[str, object]] = []
        for window in windows:
            value: dict[str, object] = {
                "norad_id": window.norad_id,
                "start": _utc_text(window.start),
                "end": _utc_text(window.end),
            }
            if window.transmitter_uuid is not None:
                value["transmitter_uuid"] = window.transmitter_uuid
            if window.waterfall_status >= 0:
                value["waterfall_status"] = window.waterfall_status
            values.append(value)
        return values

    if config.acquisition_windows:
        payload["acquisition_windows"] = serialized_windows(config.acquisition_windows)
    if config.sampling_intervals:
        payload["sampling_intervals"] = [
            {
                "start": _utc_text(interval.start),
                "end": _utc_text(interval.end),
            }
            for interval in config.sampling_intervals
        ]
    if config.supplemental_acquisition_windows:
        payload["supplemental_acquisition_windows"] = serialized_windows(
            config.supplemental_acquisition_windows
        )
    if config.external_validation_norad_ids:
        payload["external_validation_norad_ids"] = list(
            config.external_validation_norad_ids
        )
    if config.external_station_holdout_fraction:
        payload["external_station_holdout_fraction"] = (
            config.external_station_holdout_fraction
        )
    if config.external_validation_norad_ids or config.external_station_holdout_fraction:
        payload["minimum_external_test_rows"] = config.minimum_external_test_rows
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(canonical).hexdigest()


def calendar_month_chunks(start: datetime, end: datetime) -> tuple[tuple[datetime, datetime], ...]:
    start, end = as_utc(start), as_utc(end)
    chunks: list[tuple[datetime, datetime]] = []
    cursor = start
    while cursor < end:
        if cursor.month == 12:
            boundary = datetime(cursor.year + 1, 1, 1, tzinfo=UTC)
        else:
            boundary = datetime(cursor.year, cursor.month + 1, 1, tzinfo=UTC)
        chunk_end = min(boundary, end)
        chunks.append((cursor, chunk_end))
        cursor = chunk_end
    return tuple(chunks)


def _split_interval(start: datetime, end: datetime) -> tuple[tuple[datetime, datetime], ...]:
    midpoint = start + (end - start) / 2
    return ((start, midpoint), (midpoint, end))


def _safe_filename(
    norad_id: int,
    start: datetime,
    end: datetime,
    waterfall_status: int,
    transmitter_uuid: str | None,
    page: int,
) -> str:
    def stamp(value: datetime) -> str:
        return value.astimezone(UTC).strftime("%Y%m%dT%H%M%S%fZ")

    prefix = f"norad-{norad_id}_{stamp(start)}_{stamp(end)}"
    if transmitter_uuid is not None:
        transmitter_token = hashlib.sha256(transmitter_uuid.encode()).hexdigest()[:12]
        prefix = f"{prefix}_transmitter-{transmitter_token}"
    if waterfall_status < 0:
        return f"{prefix}_page-{page:06d}.json"
    return f"{prefix}_waterfall-{waterfall_status}_page-{page:06d}.json"


def _next_link(headers: Mapping[str, str]) -> str | None:
    link = next(
        (value for key, value in headers.items() if key.casefold() == "link"), None
    )
    if not link:
        return None
    for part in link.split(","):
        section = part.strip()
        if 'rel="next"' not in section and "rel=next" not in section:
            continue
        if not section.startswith("<") or ">" not in section:
            raise DatasetIntegrityError("malformed SatNOGS pagination Link header")
        return section[1 : section.index(">")]
    return None


def _write_bytes_atomic(path: Path, body: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(body)
    temporary.replace(path)


def fetch_publication_snapshot(
    client: SatNOGSClient,
    config: PublicationDatasetConfig,
    *,
    raw_dir: Path,
    manifest_path: Path,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
    minimum_request_interval_seconds: float = 0.0,
) -> Mapping[str, object]:
    """Fetch all frozen chunks, recursively bisecting only on the byte cap."""

    if client.base_url != SATNOGS_NETWORK_API:
        raise DatasetIntegrityError("publication acquisition requires the official Network API")
    if client.user_agent != config.user_agent:
        raise DatasetIntegrityError("client User-Agent differs from the frozen config")
    if client.max_response_bytes != config.max_response_bytes:
        raise DatasetIntegrityError("client response bound differs from the frozen config")
    if minimum_request_interval_seconds < 0:
        raise ValueError("minimum_request_interval_seconds must be non-negative")

    raw_dir.mkdir(parents=True, exist_ok=True)
    entries: list[dict[str, object]] = []
    acquisition_started_at = as_utc(now(), name="now")
    request_rate_phases: list[dict[str, object]] = []
    pending: list[tuple[int, datetime, datetime, int, str | None]] = [
        (
            target.norad_id,
            chunk_start,
            chunk_end,
            window.waterfall_status,
            window.transmitter_uuid,
        )
        for target in config.targets
        for window in config.acquisition_windows_for(target.norad_id)
        for chunk_start, chunk_end in calendar_month_chunks(
            window.start, window.end
        )
    ]
    active_request: tuple[int, datetime, datetime, int, str | None] | None = None
    last_network_request_at: float | None = None

    def wait_for_rate_slot() -> None:
        nonlocal last_network_request_at
        if last_network_request_at is not None:
            remaining = minimum_request_interval_seconds - (
                time.monotonic() - last_network_request_at
            )
            if remaining > 0:
                time.sleep(remaining)
        last_network_request_at = time.monotonic()

    config_identity = publication_config_identity(config)
    if manifest_path.exists():
        checkpoint = json.loads(manifest_path.read_text(encoding="utf-8"))
        if (
            checkpoint.get("schema_version") == "satnogs-publication-snapshot-manifest-v1"
            and checkpoint.get("config_identity_sha256") == config_identity
            and checkpoint.get("complete") is False
        ):
            entries = [dict(item) for item in checkpoint.get("responses", [])]
            acquisition_started_at = parse_api_datetime(
                checkpoint["created_at"], name="created_at"
            )
            pending = [
                (
                    int(item["norad_id"]),
                    parse_api_datetime(item["start"], name="pending.start"),
                    parse_api_datetime(item["end"], name="pending.end"),
                    int(item["waterfall_status"]),
                    (
                        str(item["transmitter_uuid"])
                        if item.get("transmitter_uuid") is not None
                        else None
                    ),
                )
                for item in checkpoint.get("pending_requests", [])
            ]
            active = checkpoint.get("active_request")
            if isinstance(active, Mapping):
                pending.insert(
                    0,
                    (
                        int(active["norad_id"]),
                        parse_api_datetime(active["start"], name="active.start"),
                        parse_api_datetime(active["end"], name="active.end"),
                        int(active["waterfall_status"]),
                        (
                            str(active["transmitter_uuid"])
                            if active.get("transmitter_uuid") is not None
                            else None
                        ),
                    ),
                )

            raw_phases = checkpoint.get("request_rate_phases")
            if isinstance(raw_phases, list) and all(
                isinstance(item, Mapping) for item in raw_phases
            ):
                request_rate_phases = [dict(item) for item in raw_phases]
            else:
                request_rate_phases = [
                    {
                        "authentication_mode": str(
                            checkpoint.get("authentication_mode", "anonymous")
                        ),
                        "minimum_request_interval_seconds": float(
                            checkpoint.get("minimum_request_interval_seconds", 0.0)
                        ),
                        "started_at": str(checkpoint["created_at"]),
                        "starting_response_count": 0,
                    }
                ]

    current_mode = client.authentication_mode
    current_phase = {
        "authentication_mode": current_mode,
        "minimum_request_interval_seconds": minimum_request_interval_seconds,
    }
    if not request_rate_phases or any(
        request_rate_phases[-1].get(key) != value
        for key, value in current_phase.items()
    ):
        request_rate_phases.append(
            {
                **current_phase,
                "started_at": _utc_text(as_utc(now(), name="now")),
                "starting_response_count": len(entries),
            }
        )

    def write_manifest(*, complete: bool) -> dict[str, object]:
        ordered = sorted(
            entries,
            key=lambda item: (
                int(item["norad_id"]),
                str(item["start"]),
                str(item["end"]),
                int(item.get("waterfall_status_filter", -1)),
                str(item.get("transmitter_uuid_filter") or ""),
                int(item.get("page_index", 0)),
            ),
        )
        manifest: dict[str, object] = {
            "schema_version": "satnogs-publication-snapshot-manifest-v1",
            "study_id": config.study_id,
            "source": SATNOGS_NETWORK_API,
            "license": SATNOGS_DATA_LICENSE,
            "license_url": SATNOGS_DATA_LICENSE_URL,
            "user_agent": config.user_agent,
            "pagination": "DRF cursor via RFC 8288 Link rel=next",
            "authentication_mode": current_mode,
            "minimum_request_interval_seconds": minimum_request_interval_seconds,
            "request_rate_phases": request_rate_phases,
            "config_identity_sha256": config_identity,
            "created_at": _utc_text(acquisition_started_at),
            "last_checkpoint_at": _utc_text(as_utc(now(), name="now")),
            "complete": complete,
            "remaining_request_count": len(pending),
            "active_request": (
                {
                    "norad_id": active_request[0],
                    "start": _utc_text(active_request[1]),
                    "end": _utc_text(active_request[2]),
                    "waterfall_status": active_request[3],
                    "transmitter_uuid": active_request[4],
                }
                if active_request is not None
                else None
            ),
            "pending_requests": [
                {
                    "norad_id": item[0],
                    "start": _utc_text(item[1]),
                    "end": _utc_text(item[2]),
                    "waterfall_status": item[3],
                    "transmitter_uuid": item[4],
                }
                for item in pending
            ],
            "raw_directory": str(raw_dir),
            "total_rows_before_deduplication": sum(
                int(item["row_count"]) for item in ordered
            ),
            "responses": ordered,
        }
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        _write_bytes_atomic(
            manifest_path,
            (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode("utf-8"),
        )
        return manifest

    while pending:
        norad_id, chunk_start, chunk_end, waterfall_status, transmitter_uuid = pending.pop(0)
        active_request = (
            norad_id,
            chunk_start,
            chunk_end,
            waterfall_status,
            transmitter_uuid,
        )
        write_manifest(complete=False)
        params = {
            "norad_cat_id": norad_id,
            "start": _utc_text(chunk_start),
            "start__lt": _utc_text(chunk_end),
        }
        if transmitter_uuid is not None:
            params["transmitter_uuid"] = transmitter_uuid
        if waterfall_status >= 0:
            params["waterfall_status"] = waterfall_status
        page_index = 1
        next_url: str | None = None
        split_chunk = False
        while True:
            filename = _safe_filename(
                norad_id,
                chunk_start,
                chunk_end,
                waterfall_status,
                transmitter_uuid,
                page_index,
            )
            raw_path = raw_dir / filename
            metadata_path = raw_path.with_suffix(".meta.json")
            if raw_path.exists() and metadata_path.exists():
                body = raw_path.read_bytes()
                metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
                next_url = metadata.get("next_url")
                entry = dict(metadata["manifest_entry"])
                if hashlib.sha256(body).hexdigest() != entry.get("sha256"):
                    raise DatasetIntegrityError(f"SHA-256 mismatch in resumable page {raw_path}")
                if not any(item.get("relative_path") == filename for item in entries):
                    entries.append(entry)
                    write_manifest(complete=False)
                if next_url is None:
                    break
                page_index += 1
                continue
            try:
                wait_for_rate_slot()
                response = (
                    client.get("observations/", params=params)
                    if page_index == 1
                    else client.get(str(next_url))
                )
            except SatNOGSHTTPError as exc:
                if exc.status == 429:
                    write_manifest(complete=False)
                    raise DatasetIntegrityError(
                        "SatNOGS anonymous rate limit reached; rerun resumes the checkpoint"
                    ) from exc
                if page_index > 1:
                    write_manifest(complete=False)
                    raise DatasetIntegrityError(
                        f"paginated response failed for NORAD {norad_id}; rerun resumes saved pages"
                    ) from exc
                if chunk_end - chunk_start <= timedelta(hours=config.minimum_chunk_hours):
                    raise DatasetIntegrityError(
                        f"response for NORAD {norad_id} failed at minimum chunk size"
                    ) from exc
                pending[0:0] = [
                    (norad_id, left, right, waterfall_status, transmitter_uuid)
                    for left, right in _split_interval(chunk_start, chunk_end)
                ]
                split_chunk = True
                break
            except (SatNOGSResponseTooLarge, SatNOGSError):
                if page_index > 1:
                    write_manifest(complete=False)
                    raise DatasetIntegrityError(
                        f"paginated response failed for NORAD {norad_id}; rerun resumes saved pages"
                    )
                if chunk_end - chunk_start <= timedelta(hours=config.minimum_chunk_hours):
                    raise DatasetIntegrityError(
                        f"response for NORAD {norad_id} failed at minimum chunk size"
                    )
                pending[0:0] = [
                    (norad_id, left, right, waterfall_status, transmitter_uuid)
                    for left, right in _split_interval(chunk_start, chunk_end)
                ]
                split_chunk = True
                break
            try:
                payload = json.loads(response.body.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise DatasetIntegrityError(f"non-JSON response for {response.url}") from exc
            if not isinstance(payload, list) or any(not isinstance(item, Mapping) for item in payload):
                raise DatasetIntegrityError(f"non-list observation response for {response.url}")
            unexpected = {
                item.get("norad_cat_id")
                for item in payload
                if item.get("norad_cat_id") != norad_id
            }
            if unexpected:
                raise DatasetIntegrityError(
                    f"response for NORAD {norad_id} contained other catalog IDs: {sorted(map(str, unexpected))}"
                )
            if transmitter_uuid is not None and any(
                item.get("transmitter") != transmitter_uuid
                and item.get("transmitter_uuid") != transmitter_uuid
                for item in payload
            ):
                raise DatasetIntegrityError(
                    "response for transmitter stratum contained a different transmitter"
                )
            expected_waterfall = "with-signal" if waterfall_status == 1 else "without-signal"
            if waterfall_status >= 0 and any(
                item.get("waterfall_status") != expected_waterfall for item in payload
            ):
                raise DatasetIntegrityError(
                    f"response for waterfall stratum {waterfall_status} contained a different label"
                )
            recorded_at = as_utc(now(), name="now")
            next_url = _next_link(response.headers)
            entry = {
                "norad_id": norad_id,
                "start": _utc_text(chunk_start),
                "end": _utc_text(chunk_end),
                "waterfall_status_filter": waterfall_status,
                "transmitter_uuid_filter": transmitter_uuid,
                "page_index": page_index,
                "request_url": response.url,
                "snapshot_recorded_at": _utc_text(recorded_at),
                "from_http_cache": response.from_cache,
                "http_date": next(
                    (value for key, value in response.headers.items() if key.casefold() == "date"),
                    None,
                ),
                "relative_path": filename,
                "sha256": hashlib.sha256(response.body).hexdigest(),
                "byte_length": len(response.body),
                "row_count": len(payload),
            }
            _write_bytes_atomic(raw_path, response.body)
            _write_bytes_atomic(
                metadata_path,
                (json.dumps({"next_url": next_url, "manifest_entry": entry}, indent=2, sort_keys=True) + "\n").encode(),
            )
            entries.append(entry)
            write_manifest(complete=False)
            if next_url is None:
                break
            page_index += 1
        active_request = None
        write_manifest(complete=False)
        if split_chunk:
            continue

    return write_manifest(complete=True)


def load_snapshot_observations(
    manifest_path: Path, *, raw_dir: Path | None = None
) -> tuple[Mapping[str, object], ...]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != "satnogs-publication-snapshot-manifest-v1":
        raise DatasetIntegrityError("unsupported snapshot manifest")
    if manifest.get("complete") is not True:
        raise DatasetIntegrityError("snapshot manifest is incomplete")
    root = raw_dir or Path(str(manifest["raw_directory"]))
    observations: list[Mapping[str, object]] = []
    for entry in manifest.get("responses", []):
        if not isinstance(entry, Mapping):
            raise DatasetIntegrityError("malformed response manifest entry")
        path = root / str(entry["relative_path"])
        body = path.read_bytes()
        if len(body) != int(entry["byte_length"]):
            raise DatasetIntegrityError(f"byte-length mismatch for {path}")
        if hashlib.sha256(body).hexdigest() != entry["sha256"]:
            raise DatasetIntegrityError(f"SHA-256 mismatch for {path}")
        payload = json.loads(body.decode("utf-8"))
        if not isinstance(payload, list) or len(payload) != int(entry["row_count"]):
            raise DatasetIntegrityError(f"row-count mismatch for {path}")
        if any(not isinstance(item, Mapping) for item in payload):
            raise DatasetIntegrityError(f"non-object observation in {path}")
        observations.extend(payload)
    return tuple(observations)
