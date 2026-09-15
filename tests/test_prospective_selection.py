from __future__ import annotations

from copy import deepcopy

import pytest

from telemetry_yield.prospective_selection import (
    build_eligible_pool,
    select_public_random_cohort,
)


def _inputs(count: int = 38):
    catalogue = {"observations": []}
    plan = {"candidates": []}
    for index in range(1, count + 1):
        observation_id = 20_000 + index
        catalogue["observations"].append(
            {
                "observation_id": observation_id,
                "satellite_id": f"sat-{index % 8}",
                "mode": "FSK",
                "frequency_hz": 400_000_000 + index,
                "start": "2026-09-03T00:00:00Z",
                "end": "2026-09-03T00:00:30Z",
                "frame_count": index,
                "frames_recovered": bool(index % 2),
                "frames": [{"secret": index}],
            }
        )
        plan["candidates"].append(
            {
                "observation_id": observation_id,
                "key": f"observation_{observation_id}.iq",
                "size_bytes": 4_000 + index * 4,
                "url": f"https://invalid/observation_{observation_id}.iq",
            }
        )
    return catalogue, plan


def test_projection_excludes_outcomes_and_selection_is_order_invariant() -> None:
    catalogue, plan = _inputs()
    pool = build_eligible_pool(
        catalogue, plan, eligible_modes=("FSK",), development_exclusions=(20_001,)
    )
    assert len(pool) == 37
    assert all("frames" not in row and "frame_count" not in row for row in pool)
    selected = select_public_random_cohort(
        pool,
        selection_salt=b"a" * 32,
        target_observations=30,
        maximum_per_satellite=6,
        minimum_satellites=3,
    )
    reversed_selected = select_public_random_cohort(
        tuple(reversed(pool)),
        selection_salt=b"a" * 32,
        target_observations=30,
        maximum_per_satellite=6,
        minimum_satellites=3,
    )
    assert selected == reversed_selected
    assert len({row["satellite_id"] for row in selected}) >= 3


def test_outcome_mutations_cannot_change_projected_pool() -> None:
    catalogue, plan = _inputs()
    first = build_eligible_pool(
        catalogue, plan, eligible_modes=("FSK",), development_exclusions=()
    )
    changed = deepcopy(catalogue)
    for row in changed["observations"]:
        row["frame_count"] = 999999
        row["frames_recovered"] = not row["frames_recovered"]
        row["frames"] = [{"secret": "different"}]
    second = build_eligible_pool(
        changed, plan, eligible_modes=("FSK",), development_exclusions=()
    )
    assert first == second


def test_malformed_ci16_and_insufficient_diversity_fail_closed() -> None:
    catalogue, plan = _inputs(3)
    plan["candidates"][0]["size_bytes"] = 3
    with pytest.raises(ValueError, match="multiple of four"):
        build_eligible_pool(
            catalogue, plan, eligible_modes=("FSK",), development_exclusions=()
        )
    catalogue, plan = _inputs(3)
    for row in catalogue["observations"]:
        row["satellite_id"] = "one"
    pool = build_eligible_pool(
        catalogue, plan, eligible_modes=("FSK",), development_exclusions=()
    )
    with pytest.raises(ValueError, match="too few satellite"):
        select_public_random_cohort(
            pool,
            selection_salt=b"b" * 32,
            target_observations=3,
            maximum_per_satellite=3,
            minimum_satellites=2,
        )
