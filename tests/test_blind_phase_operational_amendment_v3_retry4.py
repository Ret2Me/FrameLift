from __future__ import annotations

import argparse
import ast
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "work/blind-phase-confirmatory-v2/freeze_operational_amendment_v3_retry4.py"
TEMPLATE = ROOT / "work/blind-phase-confirmatory-v2/operational-amendment-review-template-v3-retry4.json"
SPEC = importlib.util.spec_from_file_location(
    "freeze_operational_amendment_v3_retry4", SOURCE
)
assert SPEC and SPEC.loader
FREEZER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(FREEZER)


def _identity(path: str | Path, marker: str = "a", size: int = 1) -> dict[str, object]:
    return {
        "path": str(Path(path).absolute()),
        "size_bytes": size,
        "sha256": marker * 64,
    }


def _builder_test_identity() -> dict[str, object]:
    return {
        "path": str(FREEZER.RUNTIME_GUARD_BUILDER_TEST),
        "size_bytes": 155805,
        "sha256": FREEZER.RUNTIME_GUARD_BUILDER_TEST_SHA256,
    }


def _retry3() -> dict[str, object]:
    return json.loads(FREEZER.PREVIOUS_AMENDMENT.read_bytes())


def _repair_active_builder_test_aliases(
    document: dict[str, object],
) -> dict[str, object]:
    result = copy.deepcopy(document)
    implementation = result["amended_implementation"]
    assert isinstance(implementation, dict)
    identity = _builder_test_identity()
    implementation["runtime_guard_builder_test"] = identity
    implementation["runtime_guard_builder_v3_tests"] = identity
    implementation["tests"] = FREEZER._replace_test_inventory(
        implementation["tests"], identity
    )
    return result


def _prepared() -> dict[str, object]:
    previous = _retry3()
    runtime_metadata_documents = {
        role: json.loads(Path(previous[key]["path"]).read_bytes())
        for role, key in (
            ("execution_plan", "execution_plan"),
            ("acquisition_manifest", "acquisition_manifest"),
            ("source_manifest", "source_manifest"),
        )
    }
    identities = {
        "previous_amendment": _identity(FREEZER.PREVIOUS_AMENDMENT, "1", 115742),
        "previous_review": _identity(FREEZER.PREVIOUS_REVIEW, "2", 21681),
        "previous_timestamp_query": _identity(FREEZER.PREVIOUS_TSQ, "3", 70),
        "previous_timestamp_response": _identity(FREEZER.PREVIOUS_TSR, "4", 6008),
        "runtime_guard_builder": _identity(FREEZER.RUNTIME_GUARD_BUILDER, "5", 369598),
        "runtime_guard_builder_test": _builder_test_identity(),
        "prestart_evidence": _identity(FREEZER.PRESTART_EVIDENCE, "6", 100),
        "prestart_evidence_sidecar": _identity(
            FREEZER.PRESTART_EVIDENCE_SIDECAR, "0", 100
        ),
        "prestart_evidence_review": _identity(
            FREEZER.PRESTART_EVIDENCE_REVIEW, "7", 100
        ),
        "prestart_evidence_review_sidecar": _identity(
            FREEZER.PRESTART_EVIDENCE_REVIEW_SIDECAR, "1", 100
        ),
        "quarantine_intent": _identity(FREEZER.QUARANTINE_INTENT, "8", 100),
        "quarantine_intent_sidecar": _identity(
            FREEZER.QUARANTINE_INTENT_SIDECAR, "2", 100
        ),
        "quarantine_provenance": _identity(FREEZER.QUARANTINE_PROVENANCE, "9", 100),
        "quarantine_provenance_sidecar": _identity(
            FREEZER.QUARANTINE_PROVENANCE_SIDECAR, "3", 100
        ),
        "quarantine_review": _identity(FREEZER.QUARANTINE_REVIEW, "a", 100),
        "quarantine_review_sidecar": _identity(
            FREEZER.QUARANTINE_REVIEW_SIDECAR, "4", 100
        ),
        "quarantine_terminal_review": _identity(
            FREEZER.QUARANTINE_TERMINAL_REVIEW, "f", 100
        ),
        "quarantine_terminal_review_sidecar": _identity(
            FREEZER.QUARANTINE_TERMINAL_REVIEW_SIDECAR, "5", 100
        ),
        "retry4_freezer": _identity(FREEZER.__file__, "b", 100),
        "retry4_freezer_test": _identity(FREEZER.FREEZER_TEST, "c", 100),
        "retry4_review_template": _identity(FREEZER.TEMPLATE, "d", 100),
        "retry4_review_generator": _identity(FREEZER.REVIEW_GENERATOR, "e", 100),
        "retry4_review_generator_test": _identity(
            FREEZER.REVIEW_GENERATOR_TEST, "f", 100
        ),
        "execution_plan": copy.deepcopy(previous["execution_plan"]),
        "acquisition_manifest": copy.deepcopy(previous["acquisition_manifest"]),
        "source_manifest": copy.deepcopy(previous["source_manifest"]),
    }
    return {
        "previous": previous,
        "previous_review": {"review_payload_sha256": "e" * 64},
        "evidence": {"evidence_payload_sha256": "f" * 64},
        "intent": {"intent_payload_sha256": "0" * 64},
        "provenance": {"provenance_payload_sha256": "1" * 64},
        "runtime_metadata_documents": runtime_metadata_documents,
        "identities": identities,
    }


def _args() -> argparse.Namespace:
    return argparse.Namespace(
        review=FREEZER.REVIEW_OUTPUT,
        expected_review_sha256="e" * 64,
        review_sidecar=FREEZER.REVIEW_SIDECAR,
        expected_review_sidecar_sha256="d" * 64,
        created_at="2026-09-06T16:00:00Z",
    )


def _build(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    prepared = _prepared()
    review_identity = {
        **_identity(FREEZER.REVIEW_OUTPUT, "f", 100),
        "uid": 0, "gid": 0, "mode": 0o444, "nlink": 1,
    }
    review_sidecar_identity = {
        **_identity(FREEZER.REVIEW_SIDECAR, "d", 100),
        "uid": 0, "gid": 0, "mode": 0o444, "nlink": 1,
    }
    review = {
        "schema_version": FREEZER.REVIEW_SCHEMA,
        "status": "GO",
        "reviewed_bindings": copy.deepcopy(prepared["identities"]),
        "checks": FREEZER.expected_review_checks(),
        "publication_scope": FREEZER.expected_review_publication_scope(),
        "severity_counts": {"P0": 0, "P1": 0, "P2": 0},
    }
    review["review_payload_sha256"] = FREEZER.sha256_document(review)
    monkeypatch.setattr(FREEZER, "_prepare", lambda _args: prepared)
    monkeypatch.setattr(
        FREEZER,
        "_json",
        lambda *_args, **_kwargs: (review, review_identity),
    )
    monkeypatch.setattr(
        FREEZER, "_sidecar", lambda *_args, **_kwargs: review_sidecar_identity
    )
    return FREEZER.build_amendment(_args())


def test_source_and_frozen_retry3_import_compile() -> None:
    compile(SOURCE.read_bytes(), str(SOURCE), "exec")
    assert FREEZER.RETRY3_FREEZER_PATH.is_file()
    assert hashlib.sha256(FREEZER.RETRY3_FREEZER_PATH.read_bytes()).hexdigest() == (
        FREEZER.RETRY3_FREEZER_SHA256
    )
    assert FREEZER.RETRY4_TEMPLATE_BLOCKED_PENDING_EVIDENCE_AND_QUARANTINE_REVIEW is False
    assert FREEZER.PRESTART_EVIDENCE_SHA256 == "5c900c7ddd41137833b30fdc1085bc986cd01013ffb22b214a147f8d15c00a80"
    assert FREEZER.PRESTART_EVIDENCE_REVIEW_SHA256 == "60279f5a9f138769b8c6bcdc32c6736661e724899ac1984549ee6dd202862e75"
    assert FREEZER.QUARANTINE_INTENT_SHA256 == "df5e2e1db52f30c1dc489dade91bc8de8ff7411a05f43969f9301a843e21ca74"
    assert FREEZER.QUARANTINE_PROVENANCE_SHA256 == "c9bf65fe570857b2361ba2e87d187aa2d60eb08953c534cda935531d9a8f9b98"
    assert FREEZER.QUARANTINE_REVIEW_SHA256 == "4c106c82c16e01e491431e44576024289901b34ef04feec1940a88adeedd335d"
    assert FREEZER.QUARANTINE_TERMINAL_REVIEW_SHA256 == "a6862297586c528943a0b97a104a8f1b64fac3c595b71883b218cc1bcb1f885e"
    assert FREEZER.PRESTART_EVIDENCE_PAYLOAD_SHA256 == "fb04518187837a141b20b91952c49a54e2d29f4eb7b0eda139fdfb77675d4cbd"
    assert FREEZER.PRESTART_EVIDENCE_SIDECAR_SHA256 == "451de18353f44ddafd72746d39c494265bf08eed1e61a177b045ea8e45a400a8"
    assert FREEZER.PRESTART_EVIDENCE_REVIEW_PAYLOAD_SHA256 == "801b708c58a4010e50e104f103195080faa5f761c76f6d24002c7ce8c30dc2eb"
    assert FREEZER.PRESTART_EVIDENCE_REVIEW_SIDECAR_SHA256 == "eccf94596eeade846c1fdab73929b6dcc732ac15285d83d220dc6a7ce9f4271c"
    assert FREEZER.QUARANTINE_INTENT_PAYLOAD_SHA256 == "32dc95d9a376a72b44b968c9da319af8541f2abb1bf4dd19e66e156a5606811f"
    assert FREEZER.QUARANTINE_INTENT_SIDECAR_SHA256 == "f5defc25cf96235aef4f74fc705e7f3d2b89e26161c547111a4fd1b30b6328b4"
    assert FREEZER.QUARANTINE_PROVENANCE_PAYLOAD_SHA256 == "a4416526109549daec09217984cb0991132629f6bd72eae3b6c8aa5816feac29"
    assert FREEZER.QUARANTINE_PROVENANCE_SIDECAR_SHA256 == "19577bad49ac9d56ceb756c66c6b841e7a2b44417a2a52ba41904cb51f9f5177"
    assert FREEZER.QUARANTINE_REVIEW_PAYLOAD_SHA256 == "3cd85a97e97bbbaac1d20c07450a98e7078e3805c3ba9d2d2cb31aa410fdb8fe"
    assert FREEZER.QUARANTINE_REVIEW_SIDECAR_SHA256 == "5f217b0940c7da14a1c35743af9e84014773bbaf504ff7ba93eb9c46fbe823ca"
    assert FREEZER.QUARANTINE_TERMINAL_REVIEW_PAYLOAD_SHA256 == "5485dc3bcc42438879a3c3fe5828313709b94887e4f63a23c9b805e72933e19a"
    assert FREEZER.QUARANTINE_TERMINAL_REVIEW_SIDECAR_SHA256 == "9bf0f39c62953812630b51209c0334ca43bb5a81b5068079d825635bdf1107b6"


def test_sidecar_requires_exact_main_hash_and_basename(tmp_path: Path) -> None:
    main = tmp_path / "evidence.json"
    main_payload = b"{}\n"
    main.write_bytes(main_payload)
    main_identity = {
        "path": str(main), "size_bytes": len(main_payload),
        "sha256": hashlib.sha256(main_payload).hexdigest(),
    }
    sidecar = tmp_path / "evidence.json.sha256"
    payload = f"{main_identity['sha256']}  {main.name}\n".encode()
    sidecar.write_bytes(payload)
    identity = FREEZER._sidecar(
        sidecar, hashlib.sha256(payload).hexdigest(), main_identity
    )
    assert FREEZER._simple(identity) == {
        "path": str(sidecar), "size_bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }
    sidecar.write_bytes(b"0" * 64 + b"  evidence.json\n")
    with pytest.raises(ValueError, match="sidecar differs"):
        FREEZER._sidecar(
            sidecar, hashlib.sha256(sidecar.read_bytes()).hexdigest(), main_identity
        )


def test_read_returns_same_fd_live_metadata(tmp_path: Path) -> None:
    target = tmp_path / "artifact.json"
    payload = b"{}\n"
    target.write_bytes(payload)
    target.chmod(0o440)
    observed_payload, identity = FREEZER._read(
        target, hashlib.sha256(payload).hexdigest(), maximum=100
    )
    status = target.lstat()
    assert observed_payload == payload
    assert identity == {
        "path": str(target),
        "st_dev": status.st_dev,
        "st_ino": status.st_ino,
        "uid": status.st_uid,
        "gid": status.st_gid,
        "mode": 0o440,
        "nlink": 1,
        "size_bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }


def test_template_is_exact_review_required_contract() -> None:
    template = json.loads(TEMPLATE.read_bytes())
    assert template == {
        "schema_version": FREEZER.REVIEW_SCHEMA,
        "status": "REVIEW_REQUIRED",
        "reviewed_bindings": None,
        "checks": {key: None for key in FREEZER.expected_review_checks()},
        "publication_scope": {
            key: None for key in FREEZER.expected_review_publication_scope()
        },
        "severity_counts": {"P0": None, "P1": None, "P2": None},
        "review_payload_sha256": None,
    }


def test_prepare_is_unblocked_and_enters_read_only_artifact_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        FREEZER,
        "_json",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            ValueError("read-only artifact validation reached")
        ),
    )
    with pytest.raises(ValueError, match="read-only artifact validation reached"):
        FREEZER._prepare(
            argparse.Namespace(
                previous_amendment=FREEZER.PREVIOUS_AMENDMENT,
                expected_previous_amendment_sha256=FREEZER.PREVIOUS_AMENDMENT_SHA256,
            )
        )


def test_main_invocation_gate_precedes_block_and_parser(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def reject() -> None:
        raise ValueError("invocation first")
    monkeypatch.setattr(FREEZER, "_validate_invocation", reject)
    monkeypatch.setattr(FREEZER, "parser", lambda: pytest.fail("parser reached"))
    with pytest.raises(ValueError, match="invocation first"):
        FREEZER.main([])


def test_invocation_gate_rejects_noncanonical_test_interpreter() -> None:
    with pytest.raises(ValueError, match="/usr/bin/python3.12 -I -S -B"):
        FREEZER._validate_invocation()


def test_main_defaults_to_read_only_and_requires_explicit_publish(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    args = argparse.Namespace(output=FREEZER.OUTPUT, publish=False)
    monkeypatch.setattr(FREEZER, "_validate_invocation", lambda: None)
    monkeypatch.setattr(
        FREEZER, "parser", lambda: argparse.Namespace(parse_args=lambda _argv: args)
    )
    monkeypatch.setattr(
        FREEZER, "build_amendment",
        lambda _args: {"amendment_payload_sha256": "a" * 64},
    )
    monkeypatch.setattr(
        FREEZER, "publish_no_clobber", lambda *_args: pytest.fail("published")
    )
    assert FREEZER.main([]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "PASS_NO_PUBLICATION"


def test_main_publishes_only_with_explicit_switch(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    args = argparse.Namespace(output=FREEZER.OUTPUT, publish=True)
    document = {"amendment_payload_sha256": "a" * 64}
    observed: list[tuple[Path, dict[str, object]]] = []
    monkeypatch.setattr(FREEZER, "_validate_invocation", lambda: None)
    monkeypatch.setattr(
        FREEZER, "parser", lambda: argparse.Namespace(parse_args=lambda _argv: args)
    )
    monkeypatch.setattr(FREEZER, "build_amendment", lambda _args: document)
    monkeypatch.setattr(
        FREEZER,
        "publish_no_clobber",
        lambda path, value: (
            observed.append((path, value))
            or {
                "main": _identity(path),
                "sidecar": _identity(FREEZER.REVIEW_SIDECAR),
                "amendment_payload_sha256": value["amendment_payload_sha256"],
            }
        ),
    )
    assert FREEZER.main([]) == 0
    assert observed == [(FREEZER.OUTPUT, document)]
    assert json.loads(capsys.readouterr().out)["amendment_payload_sha256"] == "a" * 64


def test_main_order_is_invocation_then_block_then_parser() -> None:
    source = SOURCE.read_text(encoding="utf-8")
    body = source[source.index("def main("):]
    assert body.index("_validate_invocation()") < body.index(
        "RETRY4_TEMPLATE_BLOCKED_PENDING_EVIDENCE_AND_QUARANTINE_REVIEW"
    ) < body.index("args = parser().parse_args(argv)")


def test_retry3_contains_the_single_known_active_alias_conflict() -> None:
    with pytest.raises(ValueError, match="conflicting identities"):
        FREEZER._assert_runtime_identity_uniqueness(_retry3())


def test_repair_makes_all_three_active_aliases_one_current_identity() -> None:
    repaired = _repair_active_builder_test_aliases(_retry3())
    result = FREEZER._assert_runtime_identity_uniqueness(repaired)
    assert result["conflict_count"] == 0
    assert result["builder_test_occurrence_count"] == 3
    assert len(result["builder_test_selectors"]) == 3


def test_real_full_runtime_metadata_closure_has_524_unique_paths_after_repair() -> None:
    repaired = _repair_active_builder_test_aliases(_retry3())
    references = {}
    for role, key in (
        ("execution_plan", "execution_plan"),
        ("acquisition_manifest", "acquisition_manifest"),
        ("source_manifest", "source_manifest"),
    ):
        document, identity = FREEZER._referenced_json(repaired, key)
        assert FREEZER._simple(identity) == repaired[key]
        references[role] = document
    result = FREEZER._assert_runtime_identity_uniqueness(
        repaired, referenced_documents=references
    )
    assert result["unique_path_count"] == 524
    assert result["conflict_count"] == 0


def test_replace_inventory_removes_every_same_path_identity() -> None:
    current = _builder_test_identity()
    stale = dict(current, sha256="0" * 64, size_bytes=1)
    unrelated = _identity("/tmp/unrelated", "2")
    result = FREEZER._replace_test_inventory(
        [stale, current, unrelated, stale], current
    )
    assert result == [unrelated, current]


def test_generic_conflict_scan_rejects_new_conflict_anywhere() -> None:
    repaired = _repair_active_builder_test_aliases(_retry3())
    repaired["adversarial_nested_branch"] = {
        "original": _identity("/tmp/arbitrary-artifact", "1", 1),
        "replacement": _identity("/tmp/arbitrary-artifact", "2", 2),
    }
    with pytest.raises(ValueError, match="/tmp/arbitrary-artifact"):
        FREEZER._assert_runtime_identity_uniqueness(repaired)


def test_generic_conflict_scan_rejects_cross_document_conflict() -> None:
    repaired = _repair_active_builder_test_aliases(_retry3())
    references = {
        "execution_plan": {
            "first": _identity("/tmp/cross-document-artifact", "1", 1)
        },
        "source_manifest": {
            "second": _identity("/tmp/cross-document-artifact", "2", 2)
        },
    }
    with pytest.raises(ValueError, match="/tmp/cross-document-artifact"):
        FREEZER._assert_runtime_identity_uniqueness(
            repaired, referenced_documents=references
        )


def test_only_planned_mirror_top_level_branch_is_excluded() -> None:
    repaired = _repair_active_builder_test_aliases(_retry3())
    original = copy.deepcopy(repaired[FREEZER.PLANNED_MIRROR_KEY])
    repaired[FREEZER.PLANNED_MIRROR_KEY] = {
        "fixture_only_conflict": [
            _identity("/tmp/planned-mirror-fixture", "1"),
            _identity("/tmp/planned-mirror-fixture", "2"),
        ]
    }
    assert FREEZER._assert_runtime_identity_uniqueness(repaired)["conflict_count"] == 0
    repaired[FREEZER.PLANNED_MIRROR_KEY] = original
    repaired["not_the_planned_mirror_contract"] = {
        "fixture_only_conflict": [
            _identity("/tmp/planned-mirror-fixture", "1"),
            _identity("/tmp/planned-mirror-fixture", "2"),
        ]
    }
    with pytest.raises(ValueError, match="conflicting identities"):
        FREEZER._assert_runtime_identity_uniqueness(repaired)


def test_build_preserves_science_and_repairs_all_aliases(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    previous = _retry3()
    document = _build(monkeypatch)
    assert FREEZER._science_snapshot(document) == FREEZER._science_snapshot(previous)
    assert document[FREEZER.PLANNED_MIRROR_KEY] == previous[FREEZER.PLANNED_MIRROR_KEY]
    assert document["amendment_revision"] == FREEZER.REVISION
    assert document["failed_v3_activation_attempts"]["attempt_count"] == 5
    assert document["runtime_consumed_identity_uniqueness"]["conflict_count"] == 0
    assert document["amendment_payload_sha256"] == FREEZER.sha256_document(
        {key: value for key, value in document.items() if key != "amendment_payload_sha256"}
    )


def test_build_discloses_prestart_boundary_and_requires_fresh_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    document = _build(monkeypatch)
    failure = document["retry3_prestart_failure_and_quarantine"]
    assert failure["failure_before_namespace_intent"] is True
    assert failure["failure_before_fork_or_unshare"] is True
    assert failure["failure_before_mount_or_iq_access"] is True
    assert failure["keeper_started"] is False
    assert failure["decoder_or_role_started"] is False
    assert failure["scientific_outcome_generated"] is False
    fresh = document["fresh_retry4_execution_requirement"]
    assert fresh["fresh_control_parent_required"] is True
    assert fresh["fresh_persistent_namespace_required"] is True
    assert fresh["retry4_rfc3161_timestamp_required_before_lifecycle"] is True
    assert fresh["review_and_amendment_publication_namespace_contract"] == (
        FREEZER.expected_review_publication_scope()
    )


def test_build_rejects_review_binding_drift(monkeypatch: pytest.MonkeyPatch) -> None:
    prepared = _prepared()
    review = {
        "schema_version": FREEZER.REVIEW_SCHEMA,
        "status": "GO",
        "reviewed_bindings": copy.deepcopy(prepared["identities"]),
        "checks": FREEZER.expected_review_checks(),
        "publication_scope": FREEZER.expected_review_publication_scope(),
        "severity_counts": {"P0": 0, "P1": 0, "P2": 0},
    }
    review["reviewed_bindings"]["retry4_freezer_test"]["sha256"] = "0" * 64
    review["review_payload_sha256"] = FREEZER.sha256_document(review)
    monkeypatch.setattr(FREEZER, "_prepare", lambda _args: prepared)
    monkeypatch.setattr(
        FREEZER,
        "_json",
        lambda *_args, **_kwargs: (
            review,
            {
                **_identity(FREEZER.REVIEW_OUTPUT),
                "uid": 0, "gid": 0, "mode": 0o444, "nlink": 1,
            },
        ),
    )
    monkeypatch.setattr(
        FREEZER,
        "_sidecar",
        lambda *_args, **_kwargs: {
            **_identity(FREEZER.REVIEW_SIDECAR),
            "uid": 0, "gid": 0, "mode": 0o444, "nlink": 1,
        },
    )
    with pytest.raises(ValueError, match="exact clean GO"):
        FREEZER.build_amendment(_args())


def test_prestart_evidence_validator_requires_exact_receipt_and_zero_boundary() -> None:
    previous = _identity(FREEZER.PREVIOUS_AMENDMENT, "1", 115742)
    seed_entries = []
    for name, inode, size, mode, digest in (
        ("amendment-v3.tsq", 1594227, 70, 0o444, FREEZER.PREVIOUS_TSQ_SHA256),
        ("amendment-v3.tsr", 1594228, 6008, 0o444, FREEZER.PREVIOUS_TSR_SHA256),
        (
            "build_runtime_guard_v3.py",
            1594226,
            369598,
            0o555,
            FREEZER.RUNTIME_GUARD_BUILDER_SHA256,
        ),
        (
            "tsa-ca-certificates.pem",
            1594229,
            182140,
            0o444,
            "ecd9dc38bc3efb7dbd6431f57e29d2f8d6a0f0d211e1464b3fef2cbfe266fcd2",
        ),
    ):
        seed_entries.append(
            {
                "path": str(FREEZER.HISTORICAL_SEED_ROOT / name),
                "st_dev": 64512,
                "st_ino": inode,
                "uid": 0,
                "gid": 0,
                "mode": mode,
                "nlink": 1,
                "size_bytes": size,
                "sha256": digest,
            }
        )
    evidence = {
        "retry3_protocol": {
            "retry3_amendment": previous,
            "retry3_amendment_payload_sha256": FREEZER.PREVIOUS_AMENDMENT_PAYLOAD_SHA256,
            "runtime_guard_builder_test_live": {
                **_builder_test_identity(),
                "st_dev": 1,
                "st_ino": 2,
                "uid": 1000,
                "gid": 1000,
                "mode": 0o664,
                "nlink": 1,
            },
        },
        "negative_state": {
            "activation_intent_present": False,
            "namespace_registration_present": False,
            "namespace_active_receipt_present": False,
            "keeper_present": False,
            "project_or_control_mount_present": False,
            "unshare_reached": False,
            "iq_content_opened": False,
            "iq_content_copied_or_hashed": False,
            "decoder_or_evaluator_or_campaign_role_started": False,
            "scientific_outcome_generated": False,
        },
        "live_fixed_point": {
            "before": {
                "inventory_sha256": "c9189df4cd87dedf48a81aba866147c885b4ba75212f7e71680c9523ab5b013a",
                "relevant_mount_count": 0,
                "keeper_or_lifecycle_role_process_count": 0,
                "anonymous_namespace_fd_count": 0,
            },
            "after": {
                "inventory_sha256": "c9189df4cd87dedf48a81aba866147c885b4ba75212f7e71680c9523ab5b013a",
                "relevant_mount_count": 0,
                "keeper_or_lifecycle_role_process_count": 0,
                "anonymous_namespace_fd_count": 0,
            },
            "unchanged": True,
        },
        "prestart_failure": {
            "failure_is_before_activation_intent_unshare_keeper_and_mount_mutation": True,
            "conflict_reconstruction": {
                "collector_projection": {
                    "documents_in_order": ["plan", "acquisition", "source", "amendment"],
                    "amendment_excluded_subtree": FREEZER.PLANNED_MIRROR_KEY,
                    "unique_path_count": 524,
                    "unauthorized_conflict_path_count": 1,
                    "authorized_mirror_dual_identity_path_count": 2,
                },
                "conflict_path": FREEZER.BUILDER_TEST_PATH,
                "current_identity": _builder_test_identity(),
                "current_selectors": [
                    "amendment.amended_implementation.runtime_guard_builder_test",
                    "amendment.amended_implementation.tests[13]",
                ],
                "stale_identity": {
                    "path": FREEZER.BUILDER_TEST_PATH,
                    "size_bytes": 151634,
                    "sha256": "5b24fb3ee696e951a0a997a4ca0e078160d820879ec1bd2b07c99f37b6d55263",
                },
                "stale_selectors": [
                    "amendment.amended_implementation.runtime_guard_builder_v3_tests"
                ],
            },
            "control_flow_proof": {
                "expanded_context_precedes_start_persistent_namespace": True,
                "conflict_collector_is_inside_expanded_context": True,
                "failure_precedes_activation_intent": True,
                "failure_precedes_unshare": True,
                "failure_precedes_keeper": True,
                "failure_precedes_mount_mutation": True,
                "frozen_iq_collector_is_metadata_only": True,
            },
            "transcript_receipt": {
                "physical_line_number": 11060,
                "ordinal": 11059,
                "raw_record": {"sha256": FREEZER.ATTEMPT_ID, "size_bytes": 7122},
                "aggregated_output": {
                    "sha256": "0ae62fd5c3722d8452ca650d488cc6e6ddf3c2a99fc8b1cad3e1e191b67c4947",
                    "size_bytes": 1275,
                },
                "execution_status": "failed",
                "exit_code": 1,
                "stderr_empty": True,
                "duration": {"secs": 0, "nanos": 743766585},
                "exception_message": "embedded artifact identity conflicts",
            },
        },
        "exact_four_file_control_seed": {
            "directory_identity": {
                "path": str(FREEZER.HISTORICAL_SEED_ROOT),
                "st_dev": 64512,
                "st_ino": 1594224,
                "uid": 0,
                "gid": 0,
                "mode": 0o755,
                "nlink": 2,
            },
            "entry_count": 4,
            "entries": seed_entries,
            "lifecycle_artifact_count": 0,
            "results_directory_present": False,
        },
        "authority_limits": {
            "transcript_is_user_owned_append_only_not_root_immutable": True,
            "retry3_review_is_exact_self_hashed_repo_artifact_not_root_immutable": True,
            "generator_is_metadata_only": True,
            "generator_has_publication_mode": False,
            "evidence_authorizes_retry4_documentation_only": True,
            "evidence_authorizes_lifecycle_or_quarantine": False,
            "control_flow_proof_attests_one_exact_independently_reviewed_builder_identity": True,
            "control_flow_proof_is_not_a_general_python_effect_analyser": True,
            "coherent_builder_or_digest_authority_change_requires_new_independent_review": True,
            "verifier_cleanup_can_signal_only_its_owned_transient_openssl_child": True,
        },
    }
    FREEZER._validate_prestart_evidence(evidence, previous)
    evidence["negative_state"]["iq_content_opened"] = True
    with pytest.raises(ValueError, match="semantics differ"):
        FREEZER._validate_prestart_evidence(evidence, previous)


def test_quarantine_validator_binds_same_inode_no_delete() -> None:
    evidence = _identity(FREEZER.PRESTART_EVIDENCE, "1", 100)
    intent_identity = _identity(FREEZER.QUARANTINE_INTENT, "2", 100)
    evidence_chain = {
        "evidence": FREEZER._simple(evidence),
        "evidence_sidecar": _identity(FREEZER.PRESTART_EVIDENCE, "3", 100),
        "evidence_payload_sha256": "4" * 64,
        "independent_review": _identity(FREEZER.PRESTART_EVIDENCE_REVIEW, "5", 100),
        "independent_review_sidecar": _identity(FREEZER.PRESTART_EVIDENCE_REVIEW, "6", 100),
        "independent_review_payload_sha256": "7" * 64,
    }
    stage2 = {
        "durable_prefix_length": 9,
        "external_inventory_sha256": "c9189df4cd87dedf48a81aba866147c885b4ba75212f7e71680c9523ab5b013a",
        "result": {
            "path": "/stage2/result.json", "size_bytes": 90532,
            "sha256": "199f4ccb14c631569339e4ab13c6c023a24911f2595f73648d3d415093a0b786",
        },
    }
    authority = {
        "path": "/stage2/intent.json", "size_bytes": 44662,
        "sha256": "388eb5d9faad83c559fdba7b79a419bfd65a2591705b9d9dfb697302d9d18a35",
    }
    operation = {
        "operation": "renameat2(RENAME_NOREPLACE)",
        "source_parent": "/var/lib", "destination_parent": "/var/lib",
        "recursive_delete": False, "content_copy": False,
        "iq_content_open": False,
    }
    intent = {
        "schema_version": FREEZER.QUARANTINE_INTENT_SCHEMA,
        "status": FREEZER.QUARANTINE_INTENT_STATUS,
        "attempt_id": FREEZER.ATTEMPT_ID,
        "created_at_utc": "2026-09-06T17:00:00Z",
        "source_control_root": str(FREEZER.HISTORICAL_SEED_ROOT),
        "quarantine_root": str(FREEZER.QUARANTINED_SEED_ROOT),
        "controller_parent": {
            "path": str(FREEZER.QUARANTINE_CONTROLLER), "st_dev": 64512,
            "st_ino": 12345, "uid": 0, "gid": 0, "mode": 0o755,
            "nlink": 2,
        },
        "rename_parent": {
            "path": "/var/lib", "st_dev": 64512, "st_ino": 1179656,
            "uid": 0, "gid": 0, "mode": 0o755,
        },
        "tools": {"runner": _identity(Path("/runner"), "8", 100)},
        "prestart_failure_evidence_chain": evidence_chain,
        "historical_stage2_authority": authority,
        "stage2": stage2,
        "pre_rename_snapshot": FREEZER._expected_seed_snapshot(
            FREEZER.HISTORICAL_SEED_ROOT
        ),
        "zero_runtime_state": FREEZER._expected_quarantine_zero_state(),
        "authorized_operation": operation,
        FREEZER.QUARANTINE_INTENT_HASH_FIELD: "9" * 64,
    }
    provenance = {
        "schema_version": FREEZER.QUARANTINE_PROVENANCE_SCHEMA,
        "status": FREEZER.QUARANTINE_PROVENANCE_STATUS,
        "attempt_id": FREEZER.ATTEMPT_ID,
        "completed_at_utc": "2026-09-06T17:01:00Z",
        "intent": FREEZER._simple(intent_identity),
        "tools": intent["tools"],
        "prestart_failure_evidence_chain": evidence_chain,
        "historical_stage2_authority": authority,
        "stage2": stage2,
        "source_control_root": str(FREEZER.HISTORICAL_SEED_ROOT),
        "quarantine_root": str(FREEZER.QUARANTINED_SEED_ROOT),
        "pre_rename_snapshot": intent["pre_rename_snapshot"],
        "post_rename_snapshot": FREEZER._expected_seed_snapshot(
            FREEZER.QUARANTINED_SEED_ROOT
        ),
        "root_inode_preserved": True,
        "all_four_seed_entries_preserved": True,
        "canonical_control_root_absent": True,
        "post_quarantine_state": FREEZER._expected_quarantine_zero_state(),
        "authorized_operation": operation,
        FREEZER.QUARANTINE_PROVENANCE_HASH_FIELD: "a" * 64,
    }
    FREEZER._validate_quarantine(intent, intent_identity, provenance, evidence)
    provenance["post_rename_snapshot"]["root"]["st_ino"] = 1
    with pytest.raises(ValueError, match="semantics differ"):
        FREEZER._validate_quarantine(intent, intent_identity, provenance, evidence)


def _terminal_quarantine_review_fixture() -> tuple[
    dict[str, object], dict[str, object], dict[str, object],
    dict[str, object], dict[str, object], dict[str, object],
    dict[str, object], dict[str, object], dict[str, object],
]:
    runner = _identity(Path("/controller/runner.py"), "1", 100)
    runner_test = _identity(Path("/controller/test.py"), "2", 100)
    design = _identity(FREEZER.QUARANTINE_REVIEW, "3", 100)
    design_sidecar = _identity(FREEZER.QUARANTINE_REVIEW_SIDECAR, "b", 100)
    intent_identity = _identity(FREEZER.QUARANTINE_INTENT, "4", 100)
    intent_sidecar = _identity(FREEZER.QUARANTINE_INTENT_SIDECAR, "c", 100)
    provenance_identity = _identity(FREEZER.QUARANTINE_PROVENANCE, "5", 100)
    provenance_sidecar = _identity(
        FREEZER.QUARANTINE_PROVENANCE_SIDECAR, "d", 100
    )
    chain = {"evidence": _identity(FREEZER.PRESTART_EVIDENCE, "6", 100)}
    intent = {"prestart_failure_evidence_chain": chain}
    tools = {"runner": runner, "runner_tests": runner_test}
    preflight = {
        "schema_version": (
            "blind-phase-confirmatory-retry3-prestart-seed-quarantine-preflight-v1"
        ),
        "status": "PASS_READ_ONLY_TERMINAL_PROVENANCE_VALIDATED_NO_PUBLICATION_NO_RENAME",
        "attempt_id": FREEZER.ATTEMPT_ID,
        "durable_prefix_length": 2,
        "source_state": "DESTINATION_QUARANTINED",
        "intent": intent_identity,
        "provenance": provenance_identity,
    }
    review = {
        "schema_version": FREEZER.QUARANTINE_TERMINAL_REVIEW_SCHEMA,
        "status": "GO",
        "reviewed_bindings": {
            "quarantine_runner": runner,
            "quarantine_runner_tests": runner_test,
            "pre_execution_design_review": design,
            "pre_execution_design_review_sidecar": design_sidecar,
            "intent": intent_identity,
            "intent_sidecar": intent_sidecar,
            "intent_payload_sha256": FREEZER.QUARANTINE_INTENT_PAYLOAD_SHA256,
            "provenance": provenance_identity,
            "provenance_sidecar": provenance_sidecar,
            "provenance_payload_sha256": (
                FREEZER.QUARANTINE_PROVENANCE_PAYLOAD_SHA256
            ),
            "controller_parent": {
                "path": str(FREEZER.QUARANTINE_CONTROLLER), "st_dev": 64512,
                "st_ino": 1594231, "uid": 0, "gid": 0,
                "mode": 0o755, "nlink": 2,
            },
            "rename_parent": {
                "path": "/var/lib", "st_dev": 64512, "st_ino": 1179656,
                "uid": 0, "gid": 0, "mode": 0o755,
            },
            "prestart_failure_evidence_chain": chain,
            "terminal_review_generator": _identity(Path("/generator.py"), "7", 100),
            "terminal_review_generator_tests": _identity(Path("/test-generator.py"), "8", 100),
            "terminal_review_template": _identity(Path("/template.json"), "9", 100),
        },
        "terminal_validation": {
            "first_preflight": preflight,
            "second_preflight": copy.deepcopy(preflight),
            "destination_snapshot": FREEZER._expected_seed_snapshot(
                FREEZER.QUARANTINED_SEED_ROOT
            ),
            "zero_runtime_state": FREEZER._expected_quarantine_zero_state(),
            "canonical_source_absent": True,
            "destination_same_seed_inode": True,
            "parent_directory_durability_bound_by_committed_provenance": True,
            "fixed_point": True,
        },
        "checks": FREEZER._expected_terminal_quarantine_review_checks(),
        "authority_limits": {
            "review_is_read_only": True,
            "review_authorizes_quarantine_recovery_documentation_only": True,
            "review_authorizes_lifecycle_start": False,
            "review_authorizes_iq_decoder_evaluator_or_outcome_action": False,
        },
        "severity_counts": {"P0": 0, "P1": 0, "P2": 0},
        "review_payload_sha256": "a" * 64,
    }
    return (
        review, tools, design, design_sidecar, intent, intent_identity,
        intent_sidecar, provenance_identity, provenance_sidecar,
    )


def test_terminal_quarantine_review_validator_binds_two_passes_and_authority() -> None:
    (
        review, tools, design, design_sidecar, intent, intent_identity,
        intent_sidecar, provenance_identity, provenance_sidecar,
    ) = (
        _terminal_quarantine_review_fixture()
    )
    FREEZER._validate_quarantine_terminal_review(
        review,
        quarantine_tools=tools,
        quarantine_review_identity=design,
        quarantine_review_sidecar_identity=design_sidecar,
        intent=intent,
        intent_identity=intent_identity,
        intent_sidecar_identity=intent_sidecar,
        provenance_identity=provenance_identity,
        provenance_sidecar_identity=provenance_sidecar,
    )


@pytest.mark.parametrize(
    ("branch", "key", "value"),
    [
        ("terminal_validation", "fixed_point", False),
        ("terminal_validation", "canonical_source_absent", False),
        ("severity_counts", "P1", 1),
        ("authority_limits", "review_authorizes_lifecycle_start", True),
    ],
)
def test_terminal_quarantine_review_validator_rejects_semantic_drift(
    branch: str, key: str, value: object,
) -> None:
    (
        review, tools, design, design_sidecar, intent, intent_identity,
        intent_sidecar, provenance_identity, provenance_sidecar,
    ) = (
        _terminal_quarantine_review_fixture()
    )
    review[branch][key] = value
    with pytest.raises(ValueError, match="terminal independent review differs"):
        FREEZER._validate_quarantine_terminal_review(
            review,
            quarantine_tools=tools,
            quarantine_review_identity=design,
            quarantine_review_sidecar_identity=design_sidecar,
            intent=intent,
            intent_identity=intent_identity,
            intent_sidecar_identity=intent_sidecar,
            provenance_identity=provenance_identity,
            provenance_sidecar_identity=provenance_sidecar,
        )


def test_parser_exposes_only_metadata_inputs_and_preflight_publish_switch() -> None:
    destinations = {action.dest for action in FREEZER.parser()._actions}
    assert {
        "publish", "output", "created_at", "review_sidecar",
        "review_generator", "review_generator_test",
    }.issubset(destinations)
    assert "preflight_only" not in destinations
    for forbidden in (
        "iq",
        "decode",
        "decoder",
        "start",
        "activate",
        "seal",
        "mount",
        "namespace",
        "role",
        "campaign",
        "evaluator",
        "outcome",
    ):
        assert forbidden not in destinations


def _publication_fixture(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> tuple[Path, dict[str, object]]:
    reports = tmp_path / "reports"
    reports.mkdir(mode=0o775)
    reports.chmod(0o775)
    output = reports / FREEZER.OUTPUT.name
    status = reports.lstat()
    monkeypatch.setattr(FREEZER, "REPORTS", reports)
    monkeypatch.setattr(FREEZER, "OUTPUT", output)
    monkeypatch.setattr(
        FREEZER,
        "EXPECTED_REPORTS_PARENT",
        {
            "path": str(reports), "st_dev": status.st_dev,
            "st_ino": status.st_ino, "uid": status.st_uid,
            "gid": status.st_gid, "mode": 0o775, "nlink": status.st_nlink,
        },
    )
    document = {"schema_version": "fixture", "amendment_payload_sha256": "a" * 64}
    return output, document


@pytest.mark.skipif(os.geteuid() != 0, reason="root immutable publication integration")
def test_pinned_publisher_fresh_and_terminal_pair_are_idempotent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    output, document = _publication_fixture(monkeypatch, tmp_path)
    first = FREEZER.publish_no_clobber(output, document)
    second = FREEZER.publish_no_clobber(output, document)
    assert first == second
    assert first["status"] == (
        "PASS_ROOT_OWNED_IMMUTABLE_FILES_IN_MUTABLE_REPORTS_NAMESPACE"
    )
    assert first["reports_namespace_immutable"] is False
    assert first["published_file_inode_metadata_immutable"] is True
    assert first["point_in_time_attestation"] is True
    assert first["mandatory_downstream_exact_revalidation"] is True
    assert output.read_bytes() == (
        json.dumps(document, allow_nan=False, indent=2, sort_keys=True) + "\n"
    ).encode()
    assert output.with_name(output.name + ".sha256").read_bytes() == (
        f"{first['main']['sha256']}  {output.name}\n".encode()
    )
    for path in (output, output.with_name(output.name + ".sha256")):
        status = path.lstat()
        assert (status.st_uid, status.st_gid, status.st_mode & 0o777, status.st_nlink) == (
            0, 0, 0o444, 1,
        )


@pytest.mark.skipif(os.geteuid() != 0, reason="root immutable publication integration")
def test_pinned_publisher_resumes_sidecar_only_and_rejects_main_only(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    output, document = _publication_fixture(monkeypatch, tmp_path)
    payload = (
        json.dumps(document, allow_nan=False, indent=2, sort_keys=True) + "\n"
    ).encode()
    digest = hashlib.sha256(payload).hexdigest()
    sidecar = output.with_name(output.name + ".sha256")
    directory_fd = os.open(output.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        FREEZER._publish_one(
            directory_fd, sidecar.name, f"{digest}  {output.name}\n".encode()
        )
    finally:
        os.close(directory_fd)
    receipt = FREEZER.publish_no_clobber(output, document)
    assert receipt["main"]["sha256"] == digest

    second_root = tmp_path / "second"
    second_root.mkdir(mode=0o775)
    second_root.chmod(0o775)
    second_output = second_root / output.name
    status = second_root.lstat()
    monkeypatch.setattr(FREEZER, "REPORTS", second_root)
    monkeypatch.setattr(FREEZER, "OUTPUT", second_output)
    monkeypatch.setattr(
        FREEZER, "EXPECTED_REPORTS_PARENT",
        {
            "path": str(second_root), "st_dev": status.st_dev,
            "st_ino": status.st_ino, "uid": status.st_uid,
            "gid": status.st_gid, "mode": 0o775, "nlink": status.st_nlink,
        },
    )
    descriptor = os.open(second_root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        FREEZER._publish_one(descriptor, second_output.name, payload)
    finally:
        os.close(descriptor)
    with pytest.raises(ValueError, match="main-only"):
        FREEZER.publish_no_clobber(second_output, document)


@pytest.mark.skipif(os.geteuid() != 0, reason="root immutable publication integration")
def test_pinned_publisher_rejects_reports_parent_swap_after_sidecar(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    output, document = _publication_fixture(monkeypatch, tmp_path)
    original_publish_one = FREEZER._publish_one
    renamed = tmp_path / "renamed-reports"

    def swap_after_first_link(
        directory_fd: int, name: str, payload: bytes,
    ) -> dict[str, object]:
        identity = original_publish_one(directory_fd, name, payload)
        output.parent.rename(renamed)
        output.parent.mkdir(mode=0o775)
        output.parent.chmod(0o775)
        return identity

    monkeypatch.setattr(FREEZER, "_publish_one", swap_after_first_link)
    with pytest.raises(ValueError, match="parent identity/path differs"):
        FREEZER.publish_no_clobber(output, document)


def test_source_has_one_explicit_amendment_publisher_and_no_inherited_publish() -> None:
    source = SOURCE.read_text(encoding="utf-8")
    tree = ast.parse(source)
    calls = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "publish_no_clobber"
    ]
    assert len(calls) == 1
    assert "RETRY3.publish_no_clobber" not in source
    assert "os.O_RDWR | os.O_CLOEXEC | os.O_TMPFILE" in source
    assert "_publish_one(\n                    directory_fd, sidecar.name" in source
    assert "main_inode = _publish_one(directory_fd, path.name, payload)" in source


def test_source_has_no_lifecycle_or_data_plane_process_primitives() -> None:
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    forbidden_attributes = {
        "fork",
        "unshare",
        "setns",
        "mount",
        "umount",
        "execve",
        "kill",
        "pidfd_send_signal",
    }
    seen = {
        node.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and node.attr in forbidden_attributes
    }
    assert seen == set()
