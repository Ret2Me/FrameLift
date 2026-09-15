from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "work/blind-phase-confirmatory-v2/freeze_operational_amendment_v1.py"
AMENDED_LAUNCHER_SCRIPT = (
    ROOT / "work/blind-phase-confirmatory-v2/campaign_launcher_amended.py"
)


def _module():
    name = "blind_phase_operational_amendment_test_module"
    spec = importlib.util.spec_from_file_location(name, SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


FREEZER = _module()


def _amended_launcher_module():
    name = "blind_phase_operational_amendment_launcher_contract_test_module"
    spec = importlib.util.spec_from_file_location(name, AMENDED_LAUNCHER_SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


AMENDED_LAUNCHER = _amended_launcher_module()


def _identity(path: Path) -> dict[str, object]:
    return FREEZER.regular_file_identity(path)


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _implementation_bundle(tmp_path: Path) -> dict[str, object]:
    launcher = tmp_path / "campaign_launcher_amended.py"
    normalizer = tmp_path / "normalize_result_amended.py"
    launcher_test = tmp_path / "test_campaign_launcher_amended.py"
    normalizer_test = tmp_path / "test_normalizer_amended.py"
    freezer_test = tmp_path / "test_operational_amendment.py"
    for index, path in enumerate(
        (launcher, normalizer, launcher_test, normalizer_test, freezer_test), start=1
    ):
        path.write_text(f"# bound test artifact {index}\n", encoding="utf-8")
    tests = [_identity(launcher_test), _identity(normalizer_test), _identity(freezer_test)]
    review: dict[str, object] = {
        "schema_version": FREEZER.REVIEW_SCHEMA_VERSION,
        "status": "PASS",
        "reviewed_amended_launcher": _identity(launcher),
        "reviewed_amended_normalizer": _identity(normalizer),
        "reviewed_tests": tests,
        "checks": {
            "original_frozen_files_unchanged": True,
            "scientific_unit_set_changed": False,
            "scientific_schedule_count": 6168,
            "execution_order_changed_for_operational_efficiency": True,
            "signal_first_then_nulls": True,
            "exposed_units_will_not_be_rerun": True,
            "exposed_units_renormalized_in_separate_namespace": True,
            "p0_findings": 0,
            "p1_findings": 0,
            "p2_findings": 0,
        },
    }
    review["review_payload_sha256"] = FREEZER.sha256_document(review)
    review_path = tmp_path / "review.json"
    _write_json(review_path, review)
    return {
        "amended_launcher": launcher,
        "expected_amended_launcher_sha256": _identity(launcher)["sha256"],
        "amended_normalizer": normalizer,
        "expected_amended_normalizer_sha256": _identity(normalizer)["sha256"],
        "launcher_test": launcher_test,
        "expected_launcher_test_sha256": _identity(launcher_test)["sha256"],
        "normalizer_test": normalizer_test,
        "expected_normalizer_test_sha256": _identity(normalizer_test)["sha256"],
        "freezer_test": freezer_test,
        "expected_freezer_test_sha256": _identity(freezer_test)["sha256"],
        "review_path": review_path,
        "expected_review_sha256": _identity(review_path)["sha256"],
    }


def _fake_timestamp(*_args: object, **_kwargs: object) -> dict[str, object]:
    return {
        "openssl": {"path": "/usr/bin/openssl", "size_bytes": 1, "sha256": "0" * 64},
        "certificate_chain": "system_CApath:/etc/ssl/certs",
        "checks": [
            {"mode": "queryfile", "returncode": 0, "verdict": "Verification: OK"},
            {"mode": "plan_digest", "returncode": 0, "verdict": "Verification: OK"},
        ],
        "status": "PASS",
    }


def test_exact_pre_amendment_inventory_and_disclosures() -> None:
    inventory = FREEZER._assert_output_inventory(FREEZER.PRE_AMENDMENT_OUTPUT_ROOT)
    assert inventory["exact_unit_directory_count"] == 2
    assert inventory["exact_file_count"] == 6
    assert inventory["no_candidate_or_mission_units_materialized"] is True
    assert inventory["results_disclosed_before_amendment"] == {
        "component_a_1200": {"candidate_count": 0, "trusted_count": 0},
        "component_a_4800": {
            "raw_pdu_count": 1,
            "raw_pdu_hex": "046c",
            "classification": "untrusted_non_ax25_diagnostic_not_admitted",
            "trusted_count": 0,
        },
        "pre_amendment_normalized_status": "normalization_failure",
    }
    for unit in inventory["units"]:
        root_check = unit["component_runtime_root_only_check"]
        assert root_check["content_identity_and_relative_path_matches"] == 19
        assert root_check["absolute_paths_differ_by_root_only"] == 19


def test_build_binds_implementation_review_schedule_and_claim_guards(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(FREEZER, "_timestamp_verification", _fake_timestamp)
    document = FREEZER.build_amendment(
        **_implementation_bundle(tmp_path), created_at="2026-09-04T18:00:00Z"
    )
    assert document["status"] == "approved_after_acquisition_before_remaining_decoder_execution"
    assert document["campaign_pristine"] is False
    assert document["schedule"] == {
        "unit_count": 6168,
        "scientific_schedule_changed": False,
        "execution_order_changed_for_operational_efficiency": True,
        "execution_phases": ["all_signals", "all_nulls"],
        "units_to_execute": 6166,
        "units_re_normalized_without_decoder_rerun": 2,
    }
    assert len(document["prior_decoder_exposures"]) == 2
    assert document["amendment_scope"]["scientific_schedule_count"] == 6168
    assert document["amendment_scope"]["scientific_unit_set_changed"] is False
    assert document["amendment_scope"]["execution_order_changed_for_operational_efficiency"] is True
    assert document["sensitivity_analysis"]["exclude_observation_ids"] == [4491]
    assert document["post_acquisition_inputs"]["selected_iq_already_acquired"] is True
    assert document["post_acquisition_inputs"]["selected_iq_opened_by_amendment_freezer"] is False
    assert document["claim_guard"]["prospective_blindness_restored_by_amendment"] is False
    assert document["claim_guard"]["raw_pdu_046c_is_trusted_telemetry"] is False
    exposures = AMENDED_LAUNCHER.validate_amendment(
        document,
        plan_identity=document["execution_plan"],
        acquisition_identity=document["acquisition_manifest"],
        source_identity=document["source_manifest"],
        normalizer_identity=document["amended_normalizer"],
    )
    assert set(exposures) == set(FREEZER.EXPOSED_UNIT_IDS)
    self_hash = document.pop("amendment_payload_sha256")
    assert self_hash == FREEZER.sha256_document(document)


def test_live_rfc3161_token_verifies_against_request_and_plan_digest() -> None:
    verification = FREEZER._timestamp_verification(
        Path("/usr/bin/openssl"), FREEZER.EXPECTED_IDENTITIES[FREEZER.PLAN]
    )
    assert verification["status"] == "PASS"
    assert [row["mode"] for row in verification["checks"]] == ["queryfile", "plan_digest"]


def test_review_must_be_self_hashed_exact_pass_and_bind_all_tests(tmp_path: Path) -> None:
    bundle = _implementation_bundle(tmp_path)
    review = json.loads(Path(bundle["review_path"]).read_text(encoding="utf-8"))
    review["checks"]["scientific_unit_set_changed"] = True
    review["review_payload_sha256"] = FREEZER.sha256_document(
        {key: value for key, value in review.items() if key != "review_payload_sha256"}
    )
    _write_json(Path(bundle["review_path"]), review)
    bundle["expected_review_sha256"] = _identity(Path(bundle["review_path"]))["sha256"]
    with pytest.raises(ValueError, match="review is not exact PASS"):
        FREEZER._validate_review(
            review,
            launcher=_identity(Path(bundle["amended_launcher"])),
            normalizer=_identity(Path(bundle["amended_normalizer"])),
            tests=[
                _identity(Path(bundle["launcher_test"])),
                _identity(Path(bundle["normalizer_test"])),
                _identity(Path(bundle["freezer_test"])),
            ],
        )


def test_inventory_rejects_any_extra_file_or_unit_directory(tmp_path: Path) -> None:
    copied = tmp_path / "campaign-run"
    shutil.copytree(FREEZER.PRE_AMENDMENT_OUTPUT_ROOT, copied)
    (copied / "unexpected.json").write_text("{}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="file inventory is not exact"):
        FREEZER._assert_output_inventory(copied)
    (copied / "unexpected.json").unlink()
    (copied / "units/candidate-forbidden").mkdir()
    with pytest.raises(ValueError, match="directory inventory is not exact"):
        FREEZER._assert_output_inventory(copied)


def test_publish_is_self_hashed_no_clobber_pair(tmp_path: Path) -> None:
    document = {"schema_version": FREEZER.SCHEMA_VERSION, "value": 1}
    document["amendment_payload_sha256"] = FREEZER.sha256_document(document)
    output = tmp_path / "amendment.json"
    FREEZER.publish_no_clobber(output, document)
    payload = output.read_bytes()
    expected = hashlib.sha256(payload).hexdigest()
    assert output.with_name("amendment.json.sha256").read_text(encoding="ascii") == (
        f"{expected}  amendment.json\n"
    )
    with pytest.raises(ValueError, match="already exists"):
        FREEZER.publish_no_clobber(output, document)


def test_existing_sidecar_prevents_partial_report_creation(tmp_path: Path) -> None:
    output = tmp_path / "amendment.json"
    output.with_name("amendment.json.sha256").write_text("reserved\n", encoding="ascii")
    with pytest.raises(ValueError, match="already exists"):
        FREEZER.publish_no_clobber(output, {"schema_version": FREEZER.SCHEMA_VERSION})
    assert not output.exists()


def test_build_never_dereferences_iq_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(FREEZER, "_timestamp_verification", _fake_timestamp)
    original_open = FREEZER.os.open
    opened: list[str] = []

    def recording_open(path: object, *args: object, **kwargs: object) -> int:
        opened.append(os.fspath(path))
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(FREEZER.os, "open", recording_open)
    FREEZER.build_amendment(
        **_implementation_bundle(tmp_path), created_at="2026-09-04T18:00:00Z"
    )
    assert not any(path.endswith(".iq") or "holdout-iq-v4" in path for path in opened)
