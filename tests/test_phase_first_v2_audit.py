from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


SCRIPT = Path(__file__).parents[1] / "work/polyitan/audit_phase_first_v2.py"
SPEC = importlib.util.spec_from_file_location("audit_phase_first_v2", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
AUDIT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(AUDIT)


def test_observation_provenance_accepts_match_and_documents_baseline_gap() -> None:
    matched = AUDIT._validate_observation_provenance(
        {"observation_id": 14366383},
        arm="conditioned_phase",
        expected_observation_id=14366383,
    )
    absent = AUDIT._validate_observation_provenance(
        {},
        arm="satnogs_compatible",
        expected_observation_id=14366383,
        allow_missing=True,
    )

    assert matched["status"] == "matches_requested_observation"
    assert matched["field_present"] is True
    assert absent == {
        "expected_observation_id": 14366383,
        "artifact_observation_id": None,
        "field_present": False,
        "status": "absent_in_source_artifact",
    }


@pytest.mark.parametrize("document", [{}, {"observation_id": 14115025}])
def test_observation_provenance_fails_closed_for_native_artifacts(
    document: dict[str, int],
) -> None:
    with pytest.raises(RuntimeError):
        AUDIT._validate_observation_provenance(
            document,
            arm="legacy_direct_phase",
            expected_observation_id=14366383,
        )


def test_observation_provenance_checks_baseline_id_when_present() -> None:
    with pytest.raises(RuntimeError):
        AUDIT._validate_observation_provenance(
            {"observation_id": 1},
            arm="satnogs_compatible",
            expected_observation_id=14366383,
            allow_missing=True,
        )
