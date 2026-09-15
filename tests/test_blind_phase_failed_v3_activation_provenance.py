from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys

import pytest


SOURCE = Path(__file__).resolve().parents[1] / "work/blind-phase-confirmatory-v2/build_failed_v3_activation_provenance.py"
SPEC = importlib.util.spec_from_file_location("build_failed_v3_activation_provenance", SOURCE)
assert SPEC and SPEC.loader
PROVENANCE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = PROVENANCE
SPEC.loader.exec_module(PROVENANCE)


def test_live_dossier_binds_exact_recovery_quarantine_and_current_builder() -> None:
    _, generator = PROVENANCE._read_regular(SOURCE)
    document = PROVENANCE.build_dossier(generator["sha256"])
    unhashed = dict(document)
    stored = unhashed.pop(PROVENANCE.HASH_FIELD)
    assert stored == PROVENANCE._sha256_document(unhashed)
    assert document["recovery"]["incident"]["sha256"] == "a81672dbd3ee100292f1a96a4dccc30bf76788c887b0b72272d4690602643a54"
    assert document["recovery"]["pass_result"]["sha256"] == "3b0779a786ac240a3e06619f4db34b5b3fb54851b928978d5cd5820d46f20222"
    assert document["recovery"]["runner"]["sha256"] == "9c1b9099c653bb1ab2e99a70330590627748491bea8cd52525b4e811efd1be83"
    assert document["current_implementation"]["runtime_guard_builder_v3"]["sha256"] == "2f13929fc235d3d4a428e3d71df8cc179a164f8459c135f503bb7f28411c1ef9"
    assert document["current_implementation"]["runtime_guard_builder_v3_tests"]["sha256"] == "fa6d0fe049cd7beabb266fd0fa3d9fcf166764d64f003caea807de274c784b64"


def test_disclosure_is_explicit_and_does_not_overclaim_pristine_iq() -> None:
    _, generator = PROVENANCE._read_regular(SOURCE)
    document = PROVENANCE.build_dossier(generator["sha256"])
    disclosure = document["exposure_disclosure"]
    assert disclosure == {
        "iq_was_mechanically_read_and_copied_into_private_sealed_input_tmpfs": True,
        "iq_was_used_as_decoder_input": False,
        "decoder_process_started": False,
        "campaign_or_evaluator_role_started": False,
        "scientific_outcome_generated": False,
        "private_results_entry_count_before_recovery": 0,
        "generator_opened_iq_contents": False,
        "generator_opened_candidate_or_component_runtime_contents": False,
    }
    assert document["reuse_policy"]["prior_attempt_artifacts_must_not_be_reused"] is True
    assert document["scientific_contract"]["execution_plan_changed"] is False


def test_generator_rejects_wrong_own_hash() -> None:
    with pytest.raises(PROVENANCE.ProvenanceError, match="generator identity drift"):
        PROVENANCE.build_dossier("0" * 64)


def test_quarantine_entry_set_is_exact(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(PROVENANCE, "QUARANTINE", tmp_path)
    (tmp_path / "foreign").write_text("x")
    monkeypatch.setattr(
        PROVENANCE, "_directory_identity",
        lambda path, mode: {"path": str(path), "mode": format(mode, "04o")},
    )
    with pytest.raises(PROVENANCE.ProvenanceError, match="entry set drift"):
        PROVENANCE._quarantine_inventory()


def test_publish_is_self_hashed_no_clobber(monkeypatch, tmp_path: Path) -> None:
    output = tmp_path / PROVENANCE.OUTPUT.name
    monkeypatch.setattr(PROVENANCE, "REPORTS", tmp_path)
    monkeypatch.setattr(PROVENANCE, "OUTPUT", output)
    document = {"schema_version": PROVENANCE.SCHEMA, "status": "PASS_METADATA_PROVENANCE_ONLY"}
    document[PROVENANCE.HASH_FIELD] = PROVENANCE._sha256_document(document)
    PROVENANCE.publish(document, output)
    parsed = json.loads(output.read_bytes())
    assert parsed == document
    assert output.with_name(output.name + ".sha256").read_text().endswith(f"  {output.name}\n")
    with pytest.raises(PROVENANCE.ProvenanceError, match="no-clobber"):
        PROVENANCE.publish(document, output)
