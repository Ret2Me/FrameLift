"""Leakage-safe split helpers."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable


def assert_no_event_leakage(rows: Iterable[tuple[str, str]]) -> None:
    """Raise if one transmission event occurs in more than one split."""
    by_event: dict[str, set[str]] = defaultdict(set)
    for event_id, split in rows:
        by_event[event_id].add(split)
    leaked = {event: values for event, values in by_event.items() if len(values) > 1}
    if leaked:
        details = ", ".join(f"{event}:{sorted(values)}" for event, values in sorted(leaked.items()))
        raise ValueError(f"transmission_event_id leakage: {details}")
