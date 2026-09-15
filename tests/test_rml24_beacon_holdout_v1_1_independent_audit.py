from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "work/rml24/independent_audit_beacon_holdout_v1_1.py"
SPEC = importlib.util.spec_from_file_location("independent_rml24_beacon_audit", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
AUDIT = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = AUDIT
SPEC.loader.exec_module(AUDIT)


def test_independent_rank_is_deterministic_and_domain_separated() -> None:
    salt = bytes(range(32))
    assert AUDIT.rank_integer(salt, 7) == AUDIT.rank_integer(salt, 7)
    assert AUDIT.rank_integer(salt, 7) != AUDIT.rank_integer(salt, 8)


def test_selection_reconstruction_matches_frozen_selection() -> None:
    plan = AUDIT.load_object(AUDIT.PLAN)
    lock = AUDIT.load_object(AUDIT.COHORT_LOCK)
    manifest = AUDIT.load_object(AUDIT.MANIFEST)
    selection = AUDIT.load_object(AUDIT.SELECTION)
    result = AUDIT.reconstruct_selection(plan, lock, manifest, selection)
    assert result["selection_exact"] is True
    assert result["lock_universe_exact"] is True
    assert result["groups"] == 252
    assert result["transfer_records"] == 504
    assert result["holdout_records"] == 1008


def test_score_rows_recompute_exact_primary_without_dsp() -> None:
    selection = AUDIT.load_object(AUDIT.SELECTION)
    lock = AUDIT.load_object(AUDIT.AMENDED_EVALUATOR_LOCK)
    result = AUDIT.audit_scores(selection, lock)
    primary = result["holdout_primary_recomputed"]
    assert result["run1_run2_byte_identical"] is True
    assert result["stored_primary_exact"] is True
    assert primary["baseline_errors"] == 420529
    assert primary["candidate_errors"] == 384563
    assert primary["truth_bits"] == 877464
    assert primary["preregistered_success"] is True


def test_selector_static_audit_has_no_outcome_array_loader() -> None:
    result = AUDIT.selector_leakage_audit((AUDIT.SELECTOR, AUDIT.PUBLIC_RANDOM, AUDIT.NIST_HELPER))
    assert result["forbidden_outcome_array_imports_absent"] is True
    assert result["violations"] == []


def test_written_report_is_pass_and_claim_is_bounded() -> None:
    report_path = ROOT / "reports/rml24-beacon-holdout-v1.1-independent-audit.json"
    report = json.loads(report_path.read_text())
    assert report["status"] == "PASS"
    assert all(report["checks"].values())
    boundary = report["claim_boundary"]
    assert boundary["fully_blind_ber"] is False
    assert boundary["packet_yield"] is False
    assert boundary["over_the_air_satellite_telemetry"] is False
    assert boundary["satnogs_comparable"] is False
