from __future__ import annotations

from dataclasses import FrozenInstanceError
import json

import pytest

from telemetry_yield.candidate_ledger import (
    DetectionRecord,
    build_candidate_ledger,
)


CAPTURE_A = "a" * 64
CAPTURE_B = "b" * 64


def _detection(**overrides: object) -> DetectionRecord:
    values: dict[str, object] = {
        "capture_sha256": CAPTURE_A,
        "segment_start_sample": 100,
        "segment_sample_count": 2_000,
        "branch_id": "phase-first",
        "source_role": "candidate",
        "plugin_id": "phase_fsk",
        "plugin_version": "1",
        "protocol_id": "ax25_ccsds_space_packet",
        "original_frame": b"outer-frame",
        "normalized_payload": b"inner-payload",
        "validation_layers": ("crc16_x25", "ccsds_primary_header"),
        "validated": True,
        "hypothesis_fingerprint": "hypothesis-1",
        "config_fingerprint": "config-1",
        "event_key": "event-10",
        "event_time_seconds": 10.25,
        "provenance": {"timing_rank": 2, "rates": [9_599.0, 9_600.0]},
    }
    values.update(overrides)
    return DetectionRecord(**values)  # type: ignore[arg-type]


def test_union_keeps_independent_origins_and_frame_digests() -> None:
    baseline = _detection(
        branch_id="reference",
        source_role="baseline",
        plugin_id="compatible_receiver",
        plugin_version="2.3.4",
        original_frame=b"baseline-envelope",
        hypothesis_fingerprint="baseline-hypothesis",
        config_fingerprint="baseline-config",
        provenance={"runtime": "source-compatible"},
    )
    candidate = _detection()

    result = build_candidate_ledger((candidate, baseline))

    assert result.baseline_count == 1
    assert result.candidate_count == 1
    assert result.branch_counts == {"phase-first": 1, "reference": 1}
    assert result.union_count == 1
    assert result.incremental_over_baseline == 0
    assert result.origin_count == 2
    frame = result.frames[0]
    assert len(frame.origins) == 2
    assert frame.branch_ids == ("phase-first", "reference")
    assert frame.original_frame_sha256s == tuple(
        sorted(
            {
                baseline.original_frame_sha256,
                candidate.original_frame_sha256,
            }
        )
    )


def test_candidate_only_frame_increases_union_over_baseline() -> None:
    baseline = _detection(
        branch_id="reference",
        source_role="baseline",
        plugin_id="compatible_receiver",
        event_key="baseline-event",
    )
    extra = _detection(event_key="new-event", event_time_seconds=11.0)

    result = build_candidate_ledger((baseline, extra))

    assert result.baseline_count == 1
    assert result.candidate_count == 1
    assert result.union_count == 2
    assert result.incremental_over_baseline == 1


def test_same_payload_in_different_events_is_not_merged() -> None:
    first = _detection(event_key="event-a", event_time_seconds=1.0)
    second = _detection(event_key="event-b", event_time_seconds=2.0)

    result = build_candidate_ledger((first, second))

    assert result.union_count == 2
    assert [frame.event_key for frame in result.frames] == ["event-a", "event-b"]


def test_capture_and_protocol_are_part_of_union_key() -> None:
    records = (
        _detection(),
        _detection(capture_sha256=CAPTURE_B),
        _detection(protocol_id="raw_ccsds"),
    )

    result = build_candidate_ledger(records)

    assert result.union_count == 3


def test_identical_origins_are_deduplicated_but_hypotheses_are_preserved() -> None:
    first = _detection()
    same = _detection()
    independent = _detection(
        hypothesis_fingerprint="hypothesis-2",
        provenance={"timing_rank": 3},
    )

    result = build_candidate_ledger((independent, same, first))

    assert result.union_count == 1
    assert result.origin_count == 2
    assert len(result.frames[0].origins) == 2


def test_unvalidated_detection_is_rejected() -> None:
    unvalidated = _detection(validated=False, validation_layers=())

    with pytest.raises(ValueError, match="explicitly validated"):
        build_candidate_ledger((unvalidated,))


def test_validated_detection_requires_named_validation_layer() -> None:
    with pytest.raises(ValueError, match="must name validation layers"):
        _detection(validated=True, validation_layers=())


def test_result_and_nested_provenance_are_immutable() -> None:
    detection = _detection(provenance={"nested": {"values": [1, 2]}})
    result = build_candidate_ledger((detection,))

    with pytest.raises(FrozenInstanceError):
        result.union_count = 99  # type: ignore[misc]
    with pytest.raises(TypeError):
        detection.provenance["new"] = 1  # type: ignore[index]
    nested = detection.provenance["nested"]
    assert isinstance(nested, dict) is False
    with pytest.raises(TypeError):
        nested["new"] = 1  # type: ignore[index,union-attr]


def test_result_is_deterministic_and_machine_readable() -> None:
    baseline = _detection(
        branch_id="z-baseline",
        source_role="baseline",
        plugin_id="reference",
        event_key="event-b",
        event_time_seconds=2.0,
    )
    candidate = _detection(branch_id="a-candidate", event_key="event-a")

    forward = build_candidate_ledger((baseline, candidate)).to_dict()
    reverse = build_candidate_ledger((candidate, baseline)).to_dict()

    assert forward == reverse
    assert list(forward["branch_counts"]) == ["a-candidate", "z-baseline"]
    serialized = json.dumps(forward, sort_keys=True, allow_nan=False)
    assert "original_frame_sha256" in serialized
    assert "original_frame_hex" in serialized


def test_empty_ledger_has_zero_counts() -> None:
    result = build_candidate_ledger(())

    assert result.baseline_count == 0
    assert result.candidate_count == 0
    assert result.branch_counts == {}
    assert result.union_count == 0
    assert result.incremental_over_baseline == 0
    assert result.origin_count == 0
