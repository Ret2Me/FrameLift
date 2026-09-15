"""Explicit, confirmation-bound SatNOGS scheduling submission boundary."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any, Callable, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

from telemetry_yield.satnogs import SATNOGS_NETWORK_API

from .models import as_utc
from .satnogs_adapter import SatnogsScheduleExport


class SatnogsSubmissionError(RuntimeError):
    pass


class SatnogsSubmissionAmbiguous(SatnogsSubmissionError):
    """The caller must reconcile jobs before considering a retry."""


class _RejectRedirects(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        return None


def satnogs_export_sha256(export: SatnogsScheduleExport) -> str:
    body = json.dumps(
        export.as_dict(), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(body).hexdigest()


def satnogs_export_from_dict(payload: Mapping[str, object]) -> SatnogsScheduleExport:
    if payload.get("schema_version") != "satnogs-network-schedule-export-v1":
        raise SatnogsSubmissionError("unsupported SatNOGS schedule export schema")
    if payload.get("mode") != "dry_run" or payload.get("api_path") != "observations/":
        raise SatnogsSubmissionError("submission requires an unchanged dry-run export")
    api_payload = payload.get("api_payload")
    audit = payload.get("audit")
    if (
        not isinstance(api_payload, list)
        or not isinstance(audit, list)
        or len(api_payload) != len(audit)
        or not api_payload
        or any(not isinstance(item, Mapping) for item in api_payload + audit)
    ):
        raise SatnogsSubmissionError("malformed or empty SatNOGS schedule export")
    required_request = {"start", "end", "ground_station", "transmitter_uuid"}
    allowed_request = required_request | {"center_frequency"}
    required_audit = {
        "opportunity_id",
        "norad_id",
        "tle_fingerprint",
        "p_success",
        "expected_unique_samples",
        "plan_id",
        "plan_revision",
        "plan_canonical_fingerprint",
    }
    if any(not required_request <= set(item) for item in api_payload):
        raise SatnogsSubmissionError("schedule request is missing a required field")
    if any(not set(item) <= allowed_request for item in api_payload):
        raise SatnogsSubmissionError("schedule request contains an unsupported field")
    if any(not required_audit <= set(item) for item in audit):
        raise SatnogsSubmissionError("schedule audit is missing provenance")
    if any(set(item) != required_audit for item in audit):
        raise SatnogsSubmissionError("schedule audit contains an unsupported field")
    station_intervals: dict[int, list[tuple[datetime, datetime]]] = {}
    for item in api_payload:
        try:
            if not isinstance(item["start"], str) or not isinstance(item["end"], str):
                raise TypeError
            start = datetime.strptime(item["start"], "%Y-%m-%d %H:%M:%S")
            end = datetime.strptime(item["end"], "%Y-%m-%d %H:%M:%S")
        except (KeyError, TypeError, ValueError) as exc:
            raise SatnogsSubmissionError("schedule request field has an invalid type") from exc
        station = item["ground_station"]
        transmitter = item["transmitter_uuid"]
        frequency = item.get("center_frequency")
        if (
            isinstance(station, bool)
            or not isinstance(station, int)
            or isinstance(frequency, bool)
            or (frequency is not None and not isinstance(frequency, int))
            or not isinstance(transmitter, str)
        ):
            raise SatnogsSubmissionError("schedule request field has an invalid type")
        if (
            end <= start
            or station <= 0
            or len(transmitter) != 22
            or not transmitter.isascii()
            or not transmitter.isalnum()
        ):
            raise SatnogsSubmissionError("schedule request field is outside its valid range")
        if frequency is not None and frequency <= 0:
            raise SatnogsSubmissionError("center_frequency must be positive")
        station_intervals.setdefault(station, []).append((start, end))
    for station, intervals in station_intervals.items():
        ordered = sorted(intervals)
        if any(
            current_start < previous_end
            for (_, previous_end), (current_start, _) in zip(
                ordered, ordered[1:]
            )
        ):
            raise SatnogsSubmissionError(
                f"schedule contains overlapping SatNOGS station {station} jobs"
            )
    opportunity_ids: set[str] = set()
    plan_identities: set[tuple[str, int]] = set()
    plan_fingerprints: set[str] = set()
    for item in audit:
        fingerprint = item["tle_fingerprint"]
        p_success = item["p_success"]
        expected_samples = item["expected_unique_samples"]
        norad_id = item["norad_id"]
        plan_fingerprint = item["plan_canonical_fingerprint"]
        if (
            not isinstance(fingerprint, str)
            or len(fingerprint) != 64
            or any(character not in "0123456789abcdef" for character in fingerprint)
        ):
            raise SatnogsSubmissionError("schedule audit TLE fingerprint is invalid")
        if (
            not isinstance(item["opportunity_id"], str)
            or not item["opportunity_id"].strip()
            or not isinstance(item["plan_id"], str)
            or not item["plan_id"].strip()
            or isinstance(item["plan_revision"], bool)
            or not isinstance(item["plan_revision"], int)
            or item["plan_revision"] <= 0
        ):
            raise SatnogsSubmissionError("schedule audit provenance is invalid")
        if (
            not isinstance(plan_fingerprint, str)
            or len(plan_fingerprint) != 64
            or any(
                character not in "0123456789abcdef"
                for character in plan_fingerprint
            )
        ):
            raise SatnogsSubmissionError("schedule audit plan fingerprint is invalid")
        if (
            isinstance(norad_id, bool)
            or not isinstance(norad_id, int)
            or norad_id <= 0
            or isinstance(p_success, bool)
            or not isinstance(p_success, (int, float))
            or not math.isfinite(float(p_success))
            or not 0 <= float(p_success) <= 1
            or isinstance(expected_samples, bool)
            or not isinstance(expected_samples, (int, float))
            or not math.isfinite(float(expected_samples))
            or float(expected_samples) < 0
        ):
            raise SatnogsSubmissionError("schedule audit values are invalid")
        opportunity_id = item["opportunity_id"].strip()
        if opportunity_id in opportunity_ids:
            raise SatnogsSubmissionError("schedule audit opportunity IDs must be unique")
        opportunity_ids.add(opportunity_id)
        plan_identities.add((item["plan_id"].strip(), item["plan_revision"]))
        plan_fingerprints.add(plan_fingerprint)
    if len(plan_identities) != 1:
        raise SatnogsSubmissionError("schedule audit must reference one plan revision")
    if len(plan_fingerprints) != 1:
        raise SatnogsSubmissionError("schedule audit must reference one plan fingerprint")
    return SatnogsScheduleExport(
        schema_version="satnogs-network-schedule-export-v1",
        api_path="observations/",
        api_payload=tuple(dict(item) for item in api_payload),
        audit=tuple(dict(item) for item in audit),
    )


@dataclass(frozen=True, slots=True)
class SatnogsSubmissionReceipt:
    schema_version: str
    export_sha256: str
    submitted_at: str
    api_url: str
    http_status: int
    response_sha256: str
    response_observation_ids: tuple[int, ...]

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


class SatnogsSubmissionClient:
    """One-shot POST client with no automatic retry after an ambiguous failure."""

    def __init__(
        self,
        *,
        api_token: str,
        user_agent: str,
        base_url: str = SATNOGS_NETWORK_API,
        timeout_seconds: float = 60.0,
        max_response_bytes: int = 1_000_000,
        transport: Callable[..., Any] | None = None,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        api_token = api_token.strip()
        user_agent = user_agent.strip()
        if not api_token or "\r" in api_token or "\n" in api_token:
            raise ValueError("a valid explicit SatNOGS API token is required")
        if not user_agent or "\r" in user_agent or "\n" in user_agent:
            raise ValueError("a valid explicit User-Agent is required")
        if timeout_seconds <= 0 or max_response_bytes <= 0:
            raise ValueError("timeout and response limit must be positive")
        parsed = urlparse(base_url)
        official = urlparse(SATNOGS_NETWORK_API)
        if (
            parsed.scheme,
            parsed.netloc,
            parsed.path.rstrip("/") + "/",
        ) != (
            official.scheme,
            official.netloc,
            official.path.rstrip("/") + "/",
        ) or parsed.query or parsed.fragment:
            raise ValueError("SatNOGS submission requires the official API origin")
        self._base_url = parsed._replace(path=parsed.path.rstrip("/") + "/").geturl()
        self._api_token = api_token
        self._user_agent = user_agent
        self._timeout_seconds = timeout_seconds
        self._max_response_bytes = max_response_bytes
        self._transport = transport or build_opener(_RejectRedirects()).open
        self._now = now

    def submit(
        self, export: SatnogsScheduleExport, *, confirmation_sha256: str
    ) -> SatnogsSubmissionReceipt:
        export = satnogs_export_from_dict(export.as_dict())
        digest = satnogs_export_sha256(export)
        if confirmation_sha256 != digest:
            raise SatnogsSubmissionError(
                f"confirmation digest mismatch; review and confirm exactly {digest}"
            )
        if export.api_path != "observations/" or not export.api_payload:
            raise SatnogsSubmissionError("invalid or empty SatNOGS submission batch")
        submitted_at = as_utc(self._now(), name="now")
        if any(
            datetime.strptime(str(item["start"]), "%Y-%m-%d %H:%M:%S").replace(
                tzinfo=UTC
            )
            <= submitted_at
            for item in export.api_payload
        ):
            raise SatnogsSubmissionError("SatNOGS jobs must start in the future")
        url = urljoin(self._base_url, export.api_path)
        body = json.dumps(
            list(export.api_payload), sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        request = Request(
            url,
            data=body,
            headers={
                "Authorization": f"Token {self._api_token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": self._user_agent,
            },
            method="POST",
        )
        response: Any | None = None
        try:
            response = self._transport(request, timeout=self._timeout_seconds)
            response_url = str(response.geturl()) if hasattr(response, "geturl") else url
            expected = urlparse(url)
            actual = urlparse(response_url)
            if (actual.scheme, actual.netloc, actual.path) != (
                expected.scheme,
                expected.netloc,
                expected.path,
            ):
                raise SatnogsSubmissionError("SatNOGS POST redirected outside the exact endpoint")
            status_value = getattr(response, "status", None)
            if status_value is None:
                status_value = response.getcode()
            status = int(status_value)
            response_body = response.read(self._max_response_bytes + 1)
            if len(response_body) > self._max_response_bytes:
                raise SatnogsSubmissionAmbiguous(
                    "submission response exceeded the cap; reconcile jobs before retry"
                )
            if status >= 500:
                raise SatnogsSubmissionAmbiguous(
                    f"SatNOGS POST returned HTTP {status}; reconcile jobs before retry"
                )
            if not 200 <= status <= 299:
                raise SatnogsSubmissionError(f"SatNOGS POST failed with HTTP {status}")
        except HTTPError as exc:
            status = exc.code
            exc.close()
            if status >= 500:
                raise SatnogsSubmissionAmbiguous(
                    f"SatNOGS POST returned HTTP {status}; reconcile jobs before retry"
                ) from exc
            raise SatnogsSubmissionError(f"SatNOGS POST failed with HTTP {status}") from exc
        except (URLError, TimeoutError) as exc:
            raise SatnogsSubmissionAmbiguous(
                "SatNOGS POST outcome is unknown; reconcile jobs before any retry"
            ) from exc
        finally:
            if response is not None and hasattr(response, "close"):
                response.close()
        try:
            payload = json.loads(response_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SatnogsSubmissionAmbiguous(
                "SatNOGS accepted the request but returned invalid JSON; reconcile jobs"
            ) from exc
        if not isinstance(payload, list) or any(not isinstance(item, Mapping) for item in payload):
            raise SatnogsSubmissionAmbiguous(
                "SatNOGS accepted the request but returned an unexpected body; reconcile jobs"
            )
        identifiers = tuple(item.get("id") for item in payload)
        if (
            len(identifiers) != len(export.api_payload)
            or any(
                isinstance(identifier, bool)
                or not isinstance(identifier, int)
                or identifier <= 0
                for identifier in identifiers
            )
            or len(set(identifiers)) != len(identifiers)
        ):
            raise SatnogsSubmissionAmbiguous(
                "SatNOGS response did not identify every submitted job; reconcile jobs"
            )
        return SatnogsSubmissionReceipt(
            schema_version="satnogs-network-submission-receipt-v1",
            export_sha256=digest,
            submitted_at=submitted_at.isoformat().replace("+00:00", "Z"),
            api_url=url,
            http_status=status,
            response_sha256=hashlib.sha256(response_body).hexdigest(),
            response_observation_ids=identifiers,  # type: ignore[arg-type]
        )
