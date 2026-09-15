"""Fail-closed licence checks for publication and data export."""

from __future__ import annotations

from collections.abc import Iterable

from telemetry_yield.models import LicenseMetadata


class LicenseGateError(PermissionError):
    """Raised when an export includes a source with unverified licensing."""


def unverified_source_ids(licenses: Iterable[LicenseMetadata]) -> tuple[str, ...]:
    """Return sorted, deduplicated IDs of sources that are not exportable."""

    return tuple(
        sorted({item.source_id for item in licenses if not item.license_verified})
    )


def unpublishable_source_ids(licenses: Iterable[LicenseMetadata]) -> tuple[str, ...]:
    """Return sources lacking either licence verification or publication review."""
    return tuple(
        sorted(
            {
                item.source_id
                for item in licenses
                if not item.license_verified or not item.publication_reviewed
            }
        )
    )


def is_export_allowed(licenses: Iterable[LicenseMetadata]) -> bool:
    """Return whether all source licences have been explicitly verified."""
    materialized = tuple(licenses)
    return bool(materialized) and not unpublishable_source_ids(materialized)


def assert_export_allowed(licenses: Iterable[LicenseMetadata]) -> None:
    """Block export if any source has ``license_verified=False``."""
    materialized = tuple(licenses)
    if not materialized:
        raise LicenseGateError("export blocked: provenance set is empty")
    blocked = unpublishable_source_ids(materialized)
    if blocked:
        joined = ", ".join(blocked)
        raise LicenseGateError(
            f"export blocked by unverified or unreviewed licences: {joined}"
        )
