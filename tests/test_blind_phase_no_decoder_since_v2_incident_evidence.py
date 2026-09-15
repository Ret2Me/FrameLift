from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (
    ROOT
    / "work/blind-phase-confirmatory-v2/build_no_decoder_since_v2_incident_evidence.py"
)


def _module():
    name = "blind_phase_no_decoder_since_v2_incident_evidence_test_module"
    spec = importlib.util.spec_from_file_location(name, SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


EVIDENCE = _module()


def _self_hashed(schema: str, field: str, **values: object) -> dict[str, object]:
    document = {"schema_version": schema, **values}
    document[field] = EVIDENCE.sha256_document(document)
    return document


def _write(path: Path, value: object) -> dict[str, object]:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(value, bytes):
        path.write_bytes(value)
    else:
        path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")
    payload = path.read_bytes()
    return {
        "path": str(path.absolute()),
        "size_bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }


def _recovery_inventory() -> dict[str, object]:
    project = str(EVIDENCE.ROOT)

    def snapshot(target_count: int) -> list[dict[str, object]]:
        result = []
        for index in range(11):
            lines = [
                f"{900 + index} 1 0:5 / /proc rw - proc proc rw"
            ]
            for offset in range(target_count if index < 10 else 0):
                mount_id = 1000 + index * 10 + offset
                point = project if offset == 0 else f"{project}/runtime-{offset}"
                lines.append(
                    f"{mount_id} 1 252:0 / {point} rw - ext4 /dev/root rw"
                )
            result.append({"namespace_inode": 5000 + index, "mountinfo": lines})
        return result

    before = snapshot(3)
    candidate = snapshot(1)
    after = snapshot(0)
    return {
        "before": before,
        "after_candidate_cleanup": candidate,
        "after": after,
        "before_sha256": EVIDENCE.sha256_document(before),
        "after_sha256": EVIDENCE.sha256_document(after),
        "all_live_mount_namespaces_clear": True,
    }


def _bundle(tmp_path: Path) -> dict[str, object]:
    campaign = tmp_path / "campaign"
    relative_paths = [
        f"units/{unit}/{name}"
        for unit in ("a" * 64, "b" * 64)
        for name in (
            "output/raw-result.json",
            "normalized-result.json",
            "process-receipt.json",
        )
    ]
    files: dict[str, object] = {}
    cutoff = datetime(2026, 9, 4, 18, 8, 2, tzinfo=timezone.utc).timestamp()
    for index, relative in enumerate(relative_paths):
        path = campaign / relative
        identity = _write(path, {"index": index})
        os.utime(path, (cutoff - 60, cutoff - 60))
        files[relative] = identity
    expected_inventory = {
        "root": str(campaign.absolute()),
        "directory_count": 6,
        "file_count": 6,
        "exposed_decoder_unit_count": 2,
        "new_decoder_unit_count": 0,
        "files": files,
    }
    expected_inventory["inventory_sha256"] = EVIDENCE.sha256_document(
        expected_inventory
    )
    amendment = _self_hashed(
        "blind-phase-confirmatory-operational-amendment-v2",
        "amendment_payload_sha256",
        status="approved_after_acquisition_before_remaining_decoder_execution",
        schedule={
            "unit_count": 6168,
            "units_to_execute": 6166,
            "units_re_normalized_without_decoder_rerun": 2,
        },
        prior_decoder_exposures=[
            {
                "unit_id": character * 64,
                "observation_id": 4491,
                "decoder_rerun_permitted": False,
            }
            for character in "ab"
        ],
        sensitivity_exclusion_observation_ids=[4491],
        no_decoder_execution_since_v1={
            "unchanged_output_inventory": expected_inventory
        },
    )
    amendment_path = tmp_path / "amendment-v2.json"
    amendment_identity = _write(amendment_path, amendment)
    query_path = tmp_path / "amendment-v2.tsq"
    reply_path = tmp_path / "amendment-v2.tsr"
    query_identity = _write(query_path, b"query")
    reply_identity = _write(reply_path, b"reply")
    incident = _self_hashed(
        "blind-phase-confirmatory-activation-incident-corrected-v1",
        "incident_payload_sha256",
        status="fail_closed_before_seal_or_decoder",
        scientific_exposure={
            "iq_opened": False,
            "decoder_started": False,
            "seal_started": False,
            "runtime_root_swapped": False,
            "runtime_guard_frozen": False,
        },
        recovery_policy={"v2_campaign_resume_forbidden": True},
    )
    incident_path = tmp_path / "incident.json"
    incident_identity = _write(incident_path, incident)
    runner_path = tmp_path / "recover_activation_mounts_v11.py"
    runner_test_path = tmp_path / "test_recover_activation_mounts_v11.py"
    runner_identity = _write(runner_path, b"# runner v11\n")
    runner_test_identity = _write(runner_test_path, b"# runner v11 tests\n")
    intent = _self_hashed(
        EVIDENCE.RECOVERY_INTENT_SCHEMA,
        "recovery_payload_sha256",
        status="committed_before_first_unmount",
    )
    intent_path = tmp_path / "intent.json"
    intent_identity = _write(intent_path, intent)
    candidate = _self_hashed(
        EVIDENCE.RECOVERY_CANDIDATE_SCHEMA,
        "recovery_payload_sha256",
        status="PASS",
        transition="executed",
        intent=intent_identity,
        scientific_exposure=dict(EVIDENCE.NO_SCIENTIFIC_EXPOSURE),
        unrelated_mountinfo_records_byte_stable=True,
    )
    candidate_path = tmp_path / "candidate.json"
    candidate_identity = _write(candidate_path, candidate)
    refusal_exposure = dict(EVIDENCE.NO_SCIENTIFIC_EXPOSURE)
    refusal_exposure.update(
        {"candidate_unmount_succeeded": True, "project_unmount_attempted": False}
    )
    refusal = _self_hashed(
        EVIDENCE.RECOVERY_REFUSAL_SCHEMA,
        "incident_payload_sha256",
        status="fail_closed_after_candidate_cleanup_before_project_unmount",
        go_review_revoked_for_retry=True,
        scientific_exposure=refusal_exposure,
    )
    refusal_path = tmp_path / "refusal.json"
    refusal_identity = _write(refusal_path, refusal)
    resume = _self_hashed(
        EVIDENCE.RECOVERY_RESUME_SCHEMA,
        "recovery_payload_sha256",
        status="authorized_post_command_complete_project_only_after_v3_candidate_cleanup",
        resume_scope="project_reference_poll_and_project_unmount_only_no_candidate_transition",
        v3_intent=intent_identity,
        v3_candidate_phase=candidate_identity,
        v3_refusal_incident=refusal_identity,
        runtime_recovery_runner_v11=runner_identity,
        scientific_exposure=dict(EVIDENCE.NO_SCIENTIFIC_EXPOSURE),
    )
    resume_path = tmp_path / "resume.json"
    resume_identity = _write(resume_path, resume)
    recovery_plan = _self_hashed(
        EVIDENCE.RECOVERY_PLAN_V11_SCHEMA,
        "recovery_plan_payload_sha256",
        status="frozen_pending_independent_go",
        working_directory="/",
        runtime_recovery_runner_v11={
            "sha256": runner_identity["sha256"],
            "test_sha256": runner_test_identity["sha256"],
        },
        direct_immutable_dependencies=[
            intent_identity, candidate_identity, refusal_identity
        ],
    )
    recovery_plan_path = tmp_path / "recovery-plan-v11.json"
    recovery_plan_identity = _write(recovery_plan_path, recovery_plan)
    pre_review = _self_hashed(
        EVIDENCE.RECOVERY_PRE_REVIEW_V11_SCHEMA,
        "review_payload_sha256",
        status="GO",
        execution_authority=True,
        reviewed_plan=recovery_plan_identity,
        reviewed_runner={"sha256": runner_identity["sha256"]},
        reviewed_tests={"sha256": runner_test_identity["sha256"]},
        severity_counts={"P0": 0, "P1": 0, "P2": 0},
        verdict={"go": True},
    )
    pre_review_path = tmp_path / "pre-review-v11.json"
    pre_review_identity = _write(pre_review_path, pre_review)
    ready = _self_hashed(
        EVIDENCE.RECOVERY_PROJECT_READY_SCHEMA,
        "recovery_payload_sha256",
        status="PASS",
        intent=intent_identity,
        candidate_phase=candidate_identity,
        project_reference_count=0,
        scientific_exposure=dict(EVIDENCE.NO_SCIENTIFIC_EXPOSURE),
    )
    ready_path = tmp_path / "project-ready.json"
    ready_identity = _write(ready_path, ready)
    recovery = _self_hashed(
        EVIDENCE.RECOVERY_SCHEMA,
        "recovery_payload_sha256",
        status="PASS",
        intent=intent_identity,
        candidate_phase=candidate_identity,
        project_ready=ready_identity,
        transition="executed",
        command={
            "argv": ["/usr/bin/umount", "--no-canonicalize", str(EVIDENCE.ROOT)],
            "exit_code": 0,
            "stdout_size_bytes": 0,
            "stderr_size_bytes": 0,
        },
        namespace_inventory=_recovery_inventory(),
        recovery_policy={
            "force_or_lazy": False,
            "recursive": False,
            "nsenter_mutation": False,
            "v2_artifacts_preserved": True,
        },
        scientific_exposure={
            "iq_contents_opened": False,
            "decoder_started": False,
            "seal_started": False,
            "runtime_root_swapped": False,
            "runtime_guard_frozen": False,
        },
        metadata_only_preservation_gate={
            "iq_contents_read": False,
            "target_identities_before": {"project": "same"},
            "target_identities_after": {"project": "same"},
            "target_identities_unchanged": True,
        },
        unrelated_mountinfo_records_byte_stable=True,
    )
    recovery_path = tmp_path / "recovery.json"
    recovery_identity = _write(recovery_path, recovery)
    post_review = _self_hashed(
        EVIDENCE.RECOVERY_POST_REVIEW_SCHEMA,
        "review_payload_sha256",
        status="GO",
        reviewed_execution_artifacts={
            "resume": resume_identity,
            "project_ready": ready_identity,
            "project_complete": recovery_identity,
        },
        live_namespace_validation={
            "double_capture_equal": True,
            "live_and_pinned_equal": True,
            "exactly_matches_project_complete_journal": True,
            "live_mount_namespace_count": 11,
            "exact_project_mount_count": 0,
            "exact_candidate_runtime_r4_mount_count": 0,
            "project_descendant_mount_count": 0,
            "candidate_descendant_mount_count": 0,
            "all_unrelated_mountinfo_records_byte_identical_to_journal": True,
        },
        target_identity_validation={"unchanged": True},
        command_receipt={
            "argv": ["/usr/bin/umount", "--no-canonicalize", str(EVIDENCE.ROOT)],
            "exit_code": 0,
            "stdout_size_bytes": 0,
            "stderr_size_bytes": 0,
            "exact_receipt_validated": True,
        },
        scientific_exposure=dict(EVIDENCE.NO_SCIENTIFIC_EXPOSURE),
        severity_counts={"P0": 0, "P1": 0, "P2": 0},
        verdict={
            "go": True,
            "recovery_v11_execution_valid": True,
            "post_execution_state_valid": True,
            "further_recovery_mount_mutation_required": False,
        },
    )
    post_review_path = tmp_path / "post-review-v11.json"
    post_review_identity = _write(post_review_path, post_review)
    return {
        "campaign": campaign,
        "amendment_v2_path": amendment_path,
        "expected_amendment_v2_sha256": amendment_identity["sha256"],
        "timestamp_query_path": query_path,
        "expected_timestamp_query_sha256": query_identity["sha256"],
        "timestamp_reply_path": reply_path,
        "expected_timestamp_reply_sha256": reply_identity["sha256"],
        "incident_path": incident_path,
        "expected_incident_sha256": incident_identity["sha256"],
        "recovery_intent_path": intent_path,
        "expected_recovery_intent_sha256": intent_identity["sha256"],
        "recovery_candidate_path": candidate_path,
        "expected_recovery_candidate_sha256": candidate_identity["sha256"],
        "recovery_refusal_path": refusal_path,
        "expected_recovery_refusal_sha256": refusal_identity["sha256"],
        "recovery_resume_path": resume_path,
        "expected_recovery_resume_sha256": resume_identity["sha256"],
        "recovery_plan_v11_path": recovery_plan_path,
        "expected_recovery_plan_v11_sha256": recovery_plan_identity["sha256"],
        "recovery_pre_review_v11_path": pre_review_path,
        "expected_recovery_pre_review_v11_sha256": pre_review_identity["sha256"],
        "recovery_project_ready_path": ready_path,
        "expected_recovery_project_ready_sha256": ready_identity["sha256"],
        "recovery_path": recovery_path,
        "expected_recovery_sha256": recovery_identity["sha256"],
        "recovery_post_review_path": post_review_path,
        "expected_recovery_post_review_sha256": post_review_identity["sha256"],
        "recovery_tool_v11_path": runner_path,
        "expected_recovery_tool_v11_sha256": runner_identity["sha256"],
        "recovery_tool_v11_test_path": runner_test_path,
        "expected_recovery_tool_v11_test_sha256": runner_test_identity["sha256"],
        "compiled": {
            "RECOVERY_INTENT": (intent_identity, intent["recovery_payload_sha256"]),
            "RECOVERY_CANDIDATE": (candidate_identity, candidate["recovery_payload_sha256"]),
            "RECOVERY_REFUSAL": (refusal_identity, refusal["incident_payload_sha256"]),
            "RECOVERY_RESUME": (resume_identity, resume["recovery_payload_sha256"]),
            "RECOVERY_PLAN_V11": (recovery_plan_identity, recovery_plan["recovery_plan_payload_sha256"]),
            "RECOVERY_PRE_REVIEW_V11": (pre_review_identity, pre_review["review_payload_sha256"]),
            "RECOVERY_PROJECT_READY": (ready_identity, ready["recovery_payload_sha256"]),
            "RECOVERY": (recovery_identity, recovery["recovery_payload_sha256"]),
            "RECOVERY_POST_REVIEW": (post_review_identity, post_review["review_payload_sha256"]),
            "RECOVERY_RUNNER_V11": (runner_identity, None),
            "RECOVERY_RUNNER_V11_TEST": (runner_test_identity, None),
        },
    }


def _patch_compiled(bundle: dict[str, object], monkeypatch: pytest.MonkeyPatch) -> None:
    for stem, (identity, payload) in bundle["compiled"].items():
        monkeypatch.setattr(EVIDENCE, f"EXPECTED_{stem}_SHA256", identity["sha256"])
        if payload is not None:
            monkeypatch.setattr(
                EVIDENCE, f"EXPECTED_{stem}_PAYLOAD_SHA256", payload
            )


def test_build_binds_exact_failed_v2_recovery_and_unchanged_outputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle = _bundle(tmp_path)
    monkeypatch.setattr(
        EVIDENCE, "EXPECTED_AMENDMENT_V2_SHA256",
        bundle["expected_amendment_v2_sha256"],
    )
    monkeypatch.setattr(
        EVIDENCE, "EXPECTED_TIMESTAMP_QUERY_SHA256",
        bundle["expected_timestamp_query_sha256"],
    )
    monkeypatch.setattr(
        EVIDENCE, "EXPECTED_TIMESTAMP_REPLY_SHA256",
        bundle["expected_timestamp_reply_sha256"],
    )
    monkeypatch.setattr(
        EVIDENCE, "EXPECTED_INCIDENT_SHA256", bundle["expected_incident_sha256"]
    )
    _patch_compiled(bundle, monkeypatch)
    monkeypatch.setattr(
        EVIDENCE,
        "verify_timestamp",
        lambda *args: {
            "status": "PASS",
            "message_imprint_sha256": bundle["expected_amendment_v2_sha256"],
            "generation_time_utc": EVIDENCE.EXPECTED_TIMESTAMP_UTC,
            "query_verification": "OK",
            "data_verification": "OK",
        },
    )
    monkeypatch.setattr(EVIDENCE, "_active_decoder_processes", lambda: [])
    document = EVIDENCE.build_evidence(
        **{
            key: value
            for key, value in bundle.items()
            if key not in {"campaign", "compiled"}
        },
        campaign_root=bundle["campaign"],
        generated_at="2026-09-04T19:00:00Z",
    )
    assert document["status"] == "PASS"
    assert document["checks"]["new_decoder_output_count_since_v2"] == 0
    assert document["checks"]["immutable_post_v11_review_go_p0_p1_p2_zero"] is True
    assert len(document["recovery_v11_chain"]) == 11
    assert document["claim_scope"]["v2_campaign_resume_permitted"] is False
    unhashed = dict(document)
    assert unhashed.pop("evidence_payload_sha256") == EVIDENCE.sha256_document(unhashed)


def test_build_rejects_recovery_without_namespace_complete_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle = _bundle(tmp_path)
    _patch_compiled(bundle, monkeypatch)
    recovery_path = bundle["recovery_path"]
    recovery = json.loads(recovery_path.read_text())
    recovery["namespace_inventory"]["after"][0]["mountinfo"].append(
        "2000 1 252:0 / /home/ubuntu/telemetry-yield rw - ext4 /dev/root rw"
    )
    recovery["namespace_inventory"]["after_sha256"] = EVIDENCE.sha256_document(
        recovery["namespace_inventory"]["after"]
    )
    recovery["recovery_payload_sha256"] = EVIDENCE.sha256_document(
        {key: value for key, value in recovery.items() if key != "recovery_payload_sha256"}
    )
    recovery_identity = _write(recovery_path, recovery)
    bundle["expected_recovery_sha256"] = recovery_identity["sha256"]
    monkeypatch.setattr(EVIDENCE, "EXPECTED_RECOVERY_SHA256", recovery_identity["sha256"])
    monkeypatch.setattr(
        EVIDENCE, "EXPECTED_RECOVERY_PAYLOAD_SHA256",
        recovery["recovery_payload_sha256"],
    )
    monkeypatch.setattr(EVIDENCE, "EXPECTED_AMENDMENT_V2_SHA256", bundle["expected_amendment_v2_sha256"])
    monkeypatch.setattr(EVIDENCE, "EXPECTED_TIMESTAMP_QUERY_SHA256", bundle["expected_timestamp_query_sha256"])
    monkeypatch.setattr(EVIDENCE, "EXPECTED_TIMESTAMP_REPLY_SHA256", bundle["expected_timestamp_reply_sha256"])
    monkeypatch.setattr(EVIDENCE, "EXPECTED_INCIDENT_SHA256", bundle["expected_incident_sha256"])
    monkeypatch.setattr(EVIDENCE, "verify_timestamp", lambda *args: {"generation_time_utc": EVIDENCE.EXPECTED_TIMESTAMP_UTC})
    with pytest.raises(ValueError, match="recovery inventory is not exact"):
        EVIDENCE.build_evidence(
            **{
                key: value
                for key, value in bundle.items()
                if key not in {"campaign", "compiled"}
            },
            campaign_root=bundle["campaign"],
            generated_at="2026-09-04T19:00:00Z",
        )


def test_build_rejects_post_v11_review_with_findings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle = _bundle(tmp_path)
    _patch_compiled(bundle, monkeypatch)
    review_path = bundle["recovery_post_review_path"]
    review = json.loads(review_path.read_text())
    review["severity_counts"]["P1"] = 1
    review["review_payload_sha256"] = EVIDENCE.sha256_document(
        {key: value for key, value in review.items() if key != "review_payload_sha256"}
    )
    identity = _write(review_path, review)
    bundle["expected_recovery_post_review_sha256"] = identity["sha256"]
    monkeypatch.setattr(
        EVIDENCE, "EXPECTED_RECOVERY_POST_REVIEW_SHA256", identity["sha256"]
    )
    monkeypatch.setattr(
        EVIDENCE, "EXPECTED_RECOVERY_POST_REVIEW_PAYLOAD_SHA256",
        review["review_payload_sha256"],
    )
    monkeypatch.setattr(EVIDENCE, "EXPECTED_AMENDMENT_V2_SHA256", bundle["expected_amendment_v2_sha256"])
    monkeypatch.setattr(EVIDENCE, "EXPECTED_TIMESTAMP_QUERY_SHA256", bundle["expected_timestamp_query_sha256"])
    monkeypatch.setattr(EVIDENCE, "EXPECTED_TIMESTAMP_REPLY_SHA256", bundle["expected_timestamp_reply_sha256"])
    monkeypatch.setattr(EVIDENCE, "EXPECTED_INCIDENT_SHA256", bundle["expected_incident_sha256"])
    monkeypatch.setattr(EVIDENCE, "verify_timestamp", lambda *args: {"generation_time_utc": EVIDENCE.EXPECTED_TIMESTAMP_UTC})
    with pytest.raises(ValueError, match="independently reviewed GO"):
        EVIDENCE.build_evidence(
            **{
                key: value
                for key, value in bundle.items()
                if key not in {"campaign", "compiled"}
            },
            campaign_root=bundle["campaign"],
            generated_at="2026-09-04T19:00:00Z",
        )


def test_inventory_rejects_new_or_changed_decoder_output(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    amendment = json.loads(bundle["amendment_v2_path"].read_text())
    expected = amendment["no_decoder_execution_since_v1"]["unchanged_output_inventory"]
    cutoff = datetime(2026, 9, 4, 18, 8, 2, tzinfo=timezone.utc)
    extra = bundle["campaign"] / "units" / ("c" * 64) / "normalized-result.json"
    _write(extra, {})
    with pytest.raises(ValueError, match="new, missing, or renamed"):
        EVIDENCE._inventory_outputs(bundle["campaign"], expected, cutoff)


def test_timestamp_parser_enforces_exact_offsets_and_ignores_ascii_hex() -> None:
    digest = "00 11 22 33 44 55 66 77 88 99 aa bb cc dd ee ff"
    reply = (
        "Message data:\n"
        f"    0000 - {digest[:23]}-{digest[24:]}   ascii dead beef\n"
        f"    0010 - {digest[:23]}-{digest[24:]}   0123456789abcdef\n"
        "Serial number: 0x01\n"
    )
    assert EVIDENCE._timestamp_imprint(reply) == (
        "00112233445566778899aabbccddeeff" * 2
    )
    with pytest.raises(ValueError, match="row is malformed"):
        EVIDENCE._timestamp_imprint(reply.replace("0010 -", "0020 -"))


def test_timestamp_parser_rejects_extra_hexdump_row() -> None:
    row = "00 00 00 00 00 00 00 00-00 00 00 00 00 00 00 00"
    reply = (
        "Message data:\n"
        f"    0000 - {row}\n"
        f"    0010 - {row}\n"
        f"    0020 - {row}\n"
        "Serial number: 0x01\n"
    )
    with pytest.raises(ValueError, match="two 16-byte rows"):
        EVIDENCE._timestamp_imprint(reply)


def test_publish_is_mode_0444_no_clobber(tmp_path: Path) -> None:
    output = tmp_path / "evidence.json"
    EVIDENCE.publish_no_clobber(output, {"status": "PASS"})
    assert output.stat().st_mode & 0o777 == 0o444
    assert output.with_name(output.name + ".sha256").stat().st_mode & 0o777 == 0o444
    with pytest.raises(ValueError, match="already exists"):
        EVIDENCE.publish_no_clobber(output, {"status": "changed"})


def test_cli_requires_exact_recovery_and_review_hashes(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit, match="0"):
        EVIDENCE.main(["--help"])
    help_text = capsys.readouterr().out
    assert "--expected-recovery-sha256" in help_text
    assert "--expected-recovery-post-review-sha256" in help_text
    assert "--expected-recovery-plan-v11-sha256" in help_text
    assert "--expected-recovery-resume-sha256" in help_text
    assert "--expected-recovery-tool-v11-sha256" in help_text
    assert "--expected-recovery-smoke-sha256" not in help_text
    assert "--iq" not in help_text
