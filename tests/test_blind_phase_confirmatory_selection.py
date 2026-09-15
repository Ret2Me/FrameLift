from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "work/blind-phase-confirmatory-v1/freeze_selection.py"


def load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("freeze_selection", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def fixtures(module: ModuleType, count: int = 35) -> tuple[dict, dict]:
    observations = []
    candidates = []
    modes = sorted(module.ELIGIBLE_MODES)
    for index in range(1, count + 1):
        observation_id = 10_000 + index
        observations.append(
            {
                "observation_id": observation_id,
                "satellite_id": f"SAT-{index % 9}",
                "mode": modes[index % len(modes)],
                "frequency_hz": 400_000_000 + index,
                "start": f"2026-09-03T00:{index % 60:02d}:00Z",
                "end": f"2026-09-03T00:{index % 60:02d}:30Z",
                # Deliberately conflicting truth must not affect selection.
                "frame_count": index % 5,
                "frames_recovered": bool(index % 2),
                "frames": [{"payload": "forbidden"}],
            }
        )
        candidates.append(
            {
                "observation_id": observation_id,
                "key": f"observation_{observation_id}.iq",
                "size_bytes": 4_000 + 4 * index,
                "url": f"https://bucket.invalid/observation_{observation_id}.iq",
            }
        )
    return {"observations": observations}, {"candidates": candidates}


def test_selection_is_deterministic_diverse_and_outcome_blind() -> None:
    module = load_script()
    catalogue, plan = fixtures(module)
    first = module.build_selection(catalogue, plan)
    reversed_catalogue = {"observations": list(reversed(catalogue["observations"]))}
    reversed_plan = {"candidates": list(reversed(plan["candidates"]))}
    second = module.build_selection(reversed_catalogue, reversed_plan)

    assert first == second
    assert first["selected_summary"]["observations"] == 30
    assert first["selected_summary"]["satellite_identities"] >= 3
    assert max(first["selected_summary"]["satellite_counts"].values()) <= 6
    def keys(value: object) -> set[str]:
        if isinstance(value, dict):
            return set(value).union(*(keys(item) for item in value.values()))
        if isinstance(value, list):
            return set().union(*(keys(item) for item in value))
        return set()

    assert module.FORBIDDEN_OUTCOME_KEYS.isdisjoint(keys(first))


def test_outcome_values_cannot_change_selection() -> None:
    module = load_script()
    catalogue, plan = fixtures(module)
    first = module.build_selection(catalogue, plan)
    for row in catalogue["observations"]:
        row["frame_count"] = 999
        row["frames_recovered"] = not row["frames_recovered"]
        row["frames"] = [{"payload": "different"}]
    second = module.build_selection(catalogue, plan)
    assert first == second


def test_invalid_object_key_fails_and_zero_size_is_ineligible() -> None:
    module = load_script()
    catalogue, plan = fixtures(module)
    plan["candidates"][0]["key"] = "wrong.iq"
    with pytest.raises(ValueError, match="key/ID mismatch"):
        module.build_selection(catalogue, plan)

    catalogue, plan = fixtures(module)
    plan["candidates"][0]["size_bytes"] = 0
    result = module.build_selection(catalogue, plan)
    assert 10_001 not in {
        row["observation_id"] for row in result["selected"]
    }
