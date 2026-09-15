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
SOURCE = ROOT / "work/blind-phase-confirmatory-v2/freeze_operational_amendment_v3_retry2.py"
TEMPLATE = ROOT / "work/blind-phase-confirmatory-v2/operational-amendment-review-template-v3-retry2.json"
EVIDENCE_GENERATOR_SOURCE = ROOT / "work/blind-phase-confirmatory-v2/build_third_start_preflight_failure_evidence.py"
SPEC = importlib.util.spec_from_file_location("freeze_operational_amendment_v3_retry2", SOURCE)
assert SPEC and SPEC.loader
FREEZER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(FREEZER)


def _write_json(path: Path, value: object) -> dict[str, object]:
    payload = (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()
    path.write_bytes(payload)
    return {
        "path": str(path.absolute()), "size_bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }


def _bind_evidence(
    path: Path, value: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> dict[str, object]:
    identity = _write_json(path, value)
    os.chmod(path, 0o444)
    sidecar = path.with_name(path.name + ".sha256")
    sidecar_payload = f"{identity['sha256']}  {path.name}\n".encode("ascii")
    sidecar.write_bytes(sidecar_payload)
    os.chmod(sidecar, 0o444)
    monkeypatch.setattr(FREEZER, "EVIDENCE_PATH", path.absolute())
    monkeypatch.setattr(FREEZER, "EXPECTED_EVIDENCE_SHA256", identity["sha256"])
    monkeypatch.setattr(
        FREEZER, "EXPECTED_EVIDENCE_PAYLOAD_SHA256", value["evidence_payload_sha256"]
    )
    monkeypatch.setattr(
        FREEZER, "EXPECTED_EVIDENCE_SIDECAR_SHA256",
        hashlib.sha256(sidecar_payload).hexdigest(),
    )
    monkeypatch.setattr(FREEZER, "EXPECTED_EVIDENCE_UID", os.geteuid())
    monkeypatch.setattr(FREEZER, "EXPECTED_EVIDENCE_GID", os.getegid())
    return identity


def _identity(path: Path, *, maximum: int = FREEZER.MAX_JSON_BYTES) -> dict[str, object]:
    return FREEZER.read_regular_bytes(path, maximum_bytes=maximum)[1]


def _fake_seed() -> dict[str, object]:
    entries = []
    for index, (name, (mode, size, digest)) in enumerate(sorted(FREEZER.CONTROL_FILES.items())):
        entries.append({
            "path": str(FREEZER.CONTROL_ROOT / name), "size_bytes": size,
            "sha256": digest, "uid": 0, "gid": 0, "mode": mode, "nlink": 1,
            "st_dev": 64512, "st_ino": 1000 + index,
        })
    by_name = {Path(str(item["path"])).name: item for item in entries}
    return {
        "path": str(FREEZER.CONTROL_ROOT),
        "directory_identity": {
            "path": str(FREEZER.CONTROL_ROOT), "uid": 0, "gid": 0,
            "mode": 0o755, "nlink": 2, "st_dev": 64512, "st_ino": 999,
        },
        "expected_entry_count": 4,
        "exact_entries": entries,
        "rfc3161": {
            "timestamp_query": {
                key: by_name["amendment-v3.tsq"][key]
                for key in ("path", "size_bytes", "sha256")
            },
            "timestamp_response": {
                key: by_name["amendment-v3.tsr"][key]
                for key in ("path", "size_bytes", "sha256")
            },
            "tsa_ca_certificates": {
                key: by_name["tsa-ca-certificates.pem"][key]
                for key in ("path", "size_bytes", "sha256")
            },
            "query_and_response_match_retry_v1": True,
            "query_message_imprint_sha256": FREEZER.EXPECTED_PREVIOUS_AMENDMENT_SHA256,
            "response_token_verified": True,
        },
        "namespace_baseline_sha256": FREEZER.EXTERNAL_NAMESPACE_BASELINE_SHA256,
        "namespace_fixed_point": {
            "before_sha256": FREEZER.EXTERNAL_NAMESPACE_BASELINE_SHA256,
            "after_sha256": FREEZER.EXTERNAL_NAMESPACE_BASELINE_SHA256,
            "unchanged": True,
            "external_namespace_mount_delta": 0,
        },
    }


def _static_identities() -> dict[str, dict[str, object]]:
    result = {}
    for name, path in FREEZER.EXPECTED_FIXED_PATHS.items():
        result[name] = _identity(
            path,
            maximum=FREEZER.MAX_CODE_BYTES if name.endswith("builder") or name.endswith("test") else FREEZER.MAX_JSON_BYTES,
        )
    return result


def _evidence(
    identities: dict[str, dict[str, object]], seed: dict[str, object],
    previous: dict[str, object], previous_review: dict[str, object],
) -> dict[str, object]:
    failure = {
        "command": "start-persistent-namespace",
        "operator_journal": {
            "pid": 1813047,
            "argv": ["build_runtime_guard_v3.py", "start-persistent-namespace"],
        },
        "stage": "_expanded_activation_context",
        "exception_type": "ValueError",
        "reason": "runtime_guard_builder_v3_transitive_identity_conflict",
        "conflict_path": {
            "path": identities["runtime-guard-builder"]["path"],
            "selector_count": 4,
            "selectors": FREEZER.CONFLICTING_FIELDS,
        },
        "conflicting_fields": FREEZER.CONFLICTING_FIELDS,
        "current_builder_identity": identities["runtime-guard-builder"],
        "stale_builder_identity": FREEZER.STALE_RUNTIME_GUARD_BUILDER,
        "required_builder_sha256": FREEZER.EXPECTED_RUNTIME_GUARD_BUILDER_SHA256,
        "deterministic_replay": {
            "status": "PASS",
            "method": "pure JSON identity traversal matching production collector; builder was not imported or executed",
            "exception_message": "embedded artifact identity conflicts",
            "same_failure_reproduced_from_frozen_metadata": True,
        },
        "control_flow_proof": {
            "status": "PASS",
            "builder_source_sha256": FREEZER.EXPECTED_RUNTIME_GUARD_BUILDER_SHA256,
            "failure_precedes_activation_intent": True,
            "failure_precedes_unshare": True, "failure_precedes_iq_access": True,
            "frozen_iq_identity_collector_metadata_only": True,
        },
        "activation_intent_created": False,
        "unshare_called": False,
        "keeper_started": False,
        "mount_mutation_performed": False,
    }
    value: dict[str, object] = {
        "schema_version": FREEZER.EVIDENCE_SCHEMA,
        "status": FREEZER.EVIDENCE_STATUS,
        "created_at_utc": "2026-09-06T05:28:00Z",
        "generator": identities["third-preflight-evidence-generator"],
        "generator_tests": identities["third-preflight-evidence-generator-test"],
        "previous_protocol": FREEZER._expected_evidence_protocol(
            identities, previous, previous_review, seed
        ),
        "preflight_failure": failure,
        "root_control_seed": copy.deepcopy(seed),
        "scientific_exposure": copy.deepcopy(FREEZER.SCIENTIFIC_EXPOSURE),
    }
    value["evidence_payload_sha256"] = FREEZER.sha256_document(value)
    return value


def _base_args(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[argparse.Namespace, dict[str, object]]:
    seed = _fake_seed()
    monkeypatch.setattr(FREEZER, "_root_seed_inventory", lambda path: copy.deepcopy(seed))
    monkeypatch.setattr(FREEZER, "OUTPUT", tmp_path / "amendment.json")
    monkeypatch.setattr(FREEZER, "REVIEW_OUTPUT", tmp_path / "review.json")
    identities = _static_identities()
    previous = json.loads(Path(str(identities["previous-amendment"]["path"])).read_bytes())
    previous_review = json.loads(Path(str(identities["previous-review"]["path"])).read_bytes())
    evidence_document = _evidence(identities, seed, previous, previous_review)
    evidence_identity = _bind_evidence(
        tmp_path / "third-evidence.json",
        evidence_document, monkeypatch,
    )
    values: dict[str, object] = {
        "third_preflight_evidence": Path(evidence_identity["path"]),
        "expected_third_preflight_evidence_sha256": evidence_identity["sha256"],
        "third_preflight_evidence_schema": FREEZER.EVIDENCE_SCHEMA,
        "third_preflight_evidence_status": FREEZER.EVIDENCE_STATUS,
        "third_preflight_evidence_self_hash_field": "evidence_payload_sha256",
        "control_root": FREEZER.CONTROL_ROOT,
        "review": FREEZER.REVIEW_OUTPUT,
        "expected_review_sha256": "f" * 64,
        "output": FREEZER.OUTPUT,
        "created_at": "2026-09-06T05:30:00Z",
    }
    for name, identity in identities.items():
        key = name.replace("-", "_")
        values[key] = Path(str(identity["path"]))
        values[f"expected_{key}_sha256"] = identity["sha256"]
    args = argparse.Namespace(**values)
    prepared = FREEZER._prepare(args)
    review: dict[str, object] = {
        "schema_version": FREEZER.REVIEW_SCHEMA,
        "status": "GO",
        "reviewed_bindings": prepared["review_bindings"],
        "checks": FREEZER.expected_review_checks(),
    }
    review["review_payload_sha256"] = FREEZER.sha256_document(review)
    review_identity = _write_json(FREEZER.REVIEW_OUTPUT, review)
    args.expected_review_sha256 = review_identity["sha256"]
    return args, seed


def test_build_preserves_science_and_repairs_both_transitive_identities(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    args, _seed = _base_args(tmp_path, monkeypatch)
    previous = json.loads(args.previous_amendment.read_bytes())
    document = FREEZER.build_amendment(args)
    builder = document["amended_implementation"]["runtime_guard_builder"]
    assert FREEZER._science_snapshot(document) == FREEZER._science_snapshot(previous)
    assert document["schedule"]["unit_count"] == 6168
    assert document["schedule"]["units_to_execute"] == 6166
    assert len(document["prior_decoder_exposures"]) == 2
    assert document["sensitivity_exclusion_observation_ids"] == [4491]
    assert document["amended_implementation"]["transitive_code_dependencies"]["runtime_guard_builder_v3"] == builder
    assert document["transitive_code_dependency_contract"]["dependencies"]["runtime_guard_builder_v3"] == builder
    for container in (
        "amended_implementation", "transitive_code_dependency_contract",
    ):
        old_dependencies = (
            previous[container]["transitive_code_dependencies"]
            if container == "amended_implementation"
            else previous[container]["dependencies"]
        )
        new_dependencies = (
            document[container]["transitive_code_dependencies"]
            if container == "amended_implementation"
            else document[container]["dependencies"]
        )
        assert {
            key: value for key, value in new_dependencies.items()
            if key != "runtime_guard_builder_v3"
        } == {
            key: value for key, value in old_dependencies.items()
            if key != "runtime_guard_builder_v3"
        }
    assert document["amendment_payload_sha256"] == FREEZER.sha256_document(
        {key: value for key, value in document.items() if key != "amendment_payload_sha256"}
    )


def test_third_attempt_is_metadata_only_and_requires_fresh_evaluator_review(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    args, seed = _base_args(tmp_path, monkeypatch)
    document = FREEZER.build_amendment(args)
    attempt = document["third_start_preflight_failure"]
    assert attempt["validated_root_control_seed"] == seed
    assert attempt["failure_stage"] == "_expanded_activation_context"
    assert attempt["failure_reason"] == "runtime_guard_builder_v3_transitive_identity_conflict"
    assert attempt["activation_intent_created"] is False
    assert attempt["unshare_called"] is False
    assert attempt["scientific_exposure"] == FREEZER.SCIENTIFIC_EXPOSURE
    assert document["failed_v3_activation_attempts"]["attempt_count"] == 3
    requirement = document["post_freeze_evaluator_v3_review_requirement"]
    assert requirement["required"] is True
    assert requirement["previous_evaluator_review_or_lock_reuse_permitted"] is False


def test_result_contains_one_current_identity_for_builder_logical_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    args, _seed = _base_args(tmp_path, monkeypatch)
    document = FREEZER.build_amendment(args)
    found: list[dict[str, object]] = []

    def collect(value: object) -> None:
        if isinstance(value, dict):
            if set(value) == {"path", "size_bytes", "sha256"}:
                if value["path"] == FREEZER.STALE_RUNTIME_GUARD_BUILDER["path"]:
                    found.append(value)
                return
            for child in value.values():
                collect(child)
        elif isinstance(value, list):
            for child in value:
                collect(child)

    projection = copy.deepcopy(document)
    projection.pop("planned_artifact_mirror_contract", None)
    collect(projection)
    assert found
    assert {json.dumps(item, sort_keys=True) for item in found} == {
        json.dumps(document["amended_implementation"]["runtime_guard_builder"], sort_keys=True)
    }


@pytest.mark.parametrize(
    ("section", "key", "replacement"),
    [
        ("scientific_exposure", "iq_content_opened", True),
        ("preflight_failure", "unshare_called", True),
        ("preflight_failure", "reason", "unrelated"),
    ],
)
def test_evidence_semantic_contradictions_refuse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    section: str, key: str, replacement: object,
) -> None:
    args, _seed = _base_args(tmp_path, monkeypatch)
    value = json.loads(args.third_preflight_evidence.read_bytes())
    value[section][key] = replacement
    value["evidence_payload_sha256"] = FREEZER.sha256_document(
        {name: item for name, item in value.items() if name != "evidence_payload_sha256"}
    )
    identity = _bind_evidence(tmp_path / f"altered-{key}.json", value, monkeypatch)
    args.third_preflight_evidence = Path(identity["path"])
    args.expected_third_preflight_evidence_sha256 = identity["sha256"]
    with pytest.raises(ValueError, match="(?:evidence|failure) semantics"):
        FREEZER.build_amendment(args)


def test_evidence_self_hash_refuses(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    args, _seed = _base_args(tmp_path, monkeypatch)
    value = json.loads(args.third_preflight_evidence.read_bytes())
    value["evidence_payload_sha256"] = "a" * 64
    identity = _bind_evidence(tmp_path / "bad-selfhash.json", value, monkeypatch)
    args.third_preflight_evidence = Path(identity["path"])
    args.expected_third_preflight_evidence_sha256 = identity["sha256"]
    with pytest.raises(ValueError, match="self-hash mismatch"):
        FREEZER.build_amendment(args)


def test_evidence_generator_reference_and_exact_topology_refuse_extra_or_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    args, _seed = _base_args(tmp_path, monkeypatch)
    for variant in ("extra", "generator"):
        value = json.loads(args.third_preflight_evidence.read_bytes())
        if variant == "extra":
            value["scientific_note"] = "not authorized"
        else:
            value["generator"]["sha256"] = "a" * 64
        value["evidence_payload_sha256"] = FREEZER.sha256_document(
            {key: item for key, item in value.items() if key != "evidence_payload_sha256"}
        )
        identity = _bind_evidence(tmp_path / f"bad-{variant}.json", value, monkeypatch)
        changed = copy.copy(args)
        changed.third_preflight_evidence = Path(identity["path"])
        changed.expected_third_preflight_evidence_sha256 = identity["sha256"]
        with pytest.raises(ValueError, match="evidence semantics"):
            FREEZER.build_amendment(changed)


def test_deterministic_replay_and_control_flow_proof_are_literal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    args, _seed = _base_args(tmp_path, monkeypatch)
    value = json.loads(args.third_preflight_evidence.read_bytes())
    value["preflight_failure"]["deterministic_replay"]["method"] = "unreviewed replay"
    value["evidence_payload_sha256"] = FREEZER.sha256_document(
        {key: item for key, item in value.items() if key != "evidence_payload_sha256"}
    )
    identity = _bind_evidence(tmp_path / "bad-replay.json", value, monkeypatch)
    args.third_preflight_evidence = Path(identity["path"])
    args.expected_third_preflight_evidence_sha256 = identity["sha256"]
    with pytest.raises(ValueError, match="failure semantics"):
        FREEZER.build_amendment(args)


def test_evidence_seed_must_match_live_exact_seed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    args, seed = _base_args(tmp_path, monkeypatch)
    changed = copy.deepcopy(seed)
    changed["exact_entries"][0]["sha256"] = "a" * 64
    monkeypatch.setattr(FREEZER, "_root_seed_inventory", lambda path: changed)
    with pytest.raises(ValueError, match="evidence semantics"):
        FREEZER.build_amendment(args)


def test_review_binding_mismatch_refuses(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    args, _seed = _base_args(tmp_path, monkeypatch)
    review = json.loads(args.review.read_bytes())
    review["checks"]["guard_validation_not_weakened"] = False
    review["review_payload_sha256"] = FREEZER.sha256_document(
        {key: value for key, value in review.items() if key != "review_payload_sha256"}
    )
    identity = _write_json(tmp_path / "wrong-review.json", review)
    monkeypatch.setattr(FREEZER, "REVIEW_OUTPUT", Path(identity["path"]))
    args.review = Path(identity["path"])
    args.expected_review_sha256 = identity["sha256"]
    with pytest.raises(ValueError, match="exact clean GO"):
        FREEZER.build_amendment(args)


def test_created_at_must_follow_retry_v1(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    args, _seed = _base_args(tmp_path, monkeypatch)
    args.created_at = "2026-09-06T05:21:28Z"
    with pytest.raises(ValueError, match="does not follow"):
        FREEZER.build_amendment(args)


def test_created_at_must_follow_third_preflight_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    args, _seed = _base_args(tmp_path, monkeypatch)
    args.created_at = "2026-09-06T05:27:59Z"
    with pytest.raises(ValueError, match="does not follow"):
        FREEZER.build_amendment(args)


@pytest.mark.skipif(os.geteuid() != 0, reason="O_TMPFILE link publication requires root here")
def test_no_clobber_publication_is_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    args, _seed = _base_args(tmp_path, monkeypatch)
    document = FREEZER.build_amendment(args)
    first = FREEZER.publish_no_clobber(args.output, document)
    second = FREEZER.publish_no_clobber(args.output, document)
    assert first == second
    assert args.output.stat().st_nlink == 1
    assert (args.output.stat().st_mode & 0o777) == 0o444
    assert args.output.with_name(args.output.name + ".sha256").read_text() == (
        f"{first}  {args.output.name}\n"
    )


@pytest.mark.skipif(os.geteuid() != 0, reason="O_TMPFILE link publication requires root here")
def test_no_clobber_rejects_different_existing_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    args, _seed = _base_args(tmp_path, monkeypatch)
    document = FREEZER.build_amendment(args)
    FREEZER.publish_no_clobber(args.output, document)
    changed = copy.deepcopy(document)
    changed["created_at"] = "2026-09-06T05:31:00Z"
    with pytest.raises(ValueError, match="published artifact"):
        FREEZER.publish_no_clobber(args.output, changed)


@pytest.mark.skipif(os.geteuid() != 0, reason="O_TMPFILE link publication requires root here")
def test_sidecar_only_crash_state_is_recoverable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    args, _seed = _base_args(tmp_path, monkeypatch)
    document = FREEZER.build_amendment(args)
    payload = (json.dumps(document, indent=2, sort_keys=True) + "\n").encode()
    digest = hashlib.sha256(payload).hexdigest()
    sidecar = args.output.with_name(args.output.name + ".sha256")
    FREEZER._publish_one(sidecar, f"{digest}  {args.output.name}\n".encode())
    assert FREEZER.publish_no_clobber(args.output, document) == digest


def test_cli_has_no_iq_or_lifecycle_arguments() -> None:
    destinations = {action.dest for action in FREEZER.parser()._actions}
    assert not any("iq" in value for value in destinations)
    assert not any(value in destinations for value in ("seal", "mount", "decoder", "launcher", "evaluator"))


def test_review_template_matches_exact_contract() -> None:
    template = json.loads(TEMPLATE.read_bytes())
    assert template["schema_version"] == FREEZER.REVIEW_SCHEMA
    assert template["status"] == "REVIEW_REQUIRED"
    assert set(template["checks"]) == set(FREEZER.expected_review_checks())
    assert all(value is None for value in template["checks"].values())
    expected_bindings = {
        "previous_amendment", "previous_review", "previous_timestamp_query",
        "previous_timestamp_reply", "runtime_guard_builder",
        "runtime_guard_builder_test", "retry2_freezer_test",
        "retry2_review_template", "third_preflight_evidence", "retry2_freezer",
        "root_control_seed", "third_preflight_evidence_contract",
        "corrected_runtime_guard_builder_identity", "stale_runtime_guard_builder_identity",
        "third_preflight_evidence_generator", "third_preflight_evidence_generator_test",
        "third_preflight_evidence_sidecar",
    }
    assert set(template["reviewed_bindings"]) == expected_bindings


def test_live_retry_v1_has_only_the_two_known_stale_builder_references() -> None:
    previous = json.loads(FREEZER.EXPECTED_FIXED_PATHS["previous-amendment"].read_bytes())
    builder = _identity(FREEZER.EXPECTED_FIXED_PATHS["runtime-guard-builder"], maximum=FREEZER.MAX_CODE_BYTES)
    implementation = previous["amended_implementation"]
    assert implementation["runtime_guard_builder"] == builder
    assert implementation["runtime_guard_builder_v3"] == builder
    assert implementation["transitive_code_dependencies"]["runtime_guard_builder_v3"] == FREEZER.STALE_RUNTIME_GUARD_BUILDER
    assert previous["transitive_code_dependency_contract"]["dependencies"]["runtime_guard_builder_v3"] == FREEZER.STALE_RUNTIME_GUARD_BUILDER
    assert previous["amendment_payload_sha256"] == FREEZER.EXPECTED_PREVIOUS_AMENDMENT_PAYLOAD_SHA256


def test_live_root_control_seed_is_exact_four_entries() -> None:
    seed = FREEZER._root_seed_inventory(FREEZER.CONTROL_ROOT)
    assert seed["expected_entry_count"] == 4
    assert {Path(entry["path"]).name for entry in seed["exact_entries"]} == set(FREEZER.CONTROL_FILES)
    assert seed["namespace_fixed_point"]["unchanged"] is True


def test_live_published_evidence_and_sidecar_cross_validate() -> None:
    identities = _static_identities()
    previous = json.loads(FREEZER.EXPECTED_FIXED_PATHS["previous-amendment"].read_bytes())
    previous_review = json.loads(FREEZER.EXPECTED_FIXED_PATHS["previous-review"].read_bytes())
    root_seed = FREEZER._root_seed_inventory(FREEZER.CONTROL_ROOT)
    evidence, evidence_identity = FREEZER._json(
        FREEZER.EVIDENCE_PATH, FREEZER.EXPECTED_EVIDENCE_SHA256
    )
    FREEZER._validate_evidence(
        evidence, identities=identities, previous=previous,
        previous_review=previous_review, root_seed=root_seed,
        schema=FREEZER.EVIDENCE_SCHEMA, status=FREEZER.EVIDENCE_STATUS,
        self_hash_field="evidence_payload_sha256",
    )
    sidecar = FREEZER._validate_evidence_publication(
        FREEZER.EVIDENCE_PATH, evidence_identity
    )
    assert evidence["evidence_payload_sha256"] == FREEZER.EXPECTED_EVIDENCE_PAYLOAD_SHA256
    assert sidecar["sha256"] == FREEZER.EXPECTED_EVIDENCE_SIDECAR_SHA256


def test_source_is_metadata_only_by_construction() -> None:
    source = SOURCE.read_text()
    imports = {
        alias.name
        for node in ast.walk(ast.parse(source))
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    assert "subprocess" not in imports
    assert "importlib" not in imports
    assert "os.exec" not in source
    assert "--iq" not in source


def test_evidence_generator_contract_constants_align() -> None:
    spec = importlib.util.spec_from_file_location(
        "third_start_preflight_evidence_contract_audit", EVIDENCE_GENERATOR_SOURCE
    )
    assert spec and spec.loader
    generator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(generator)
    assert generator.SCHEMA == FREEZER.EVIDENCE_SCHEMA
    assert generator.STATUS == FREEZER.EVIDENCE_STATUS
    assert generator.HASH_FIELD == "evidence_payload_sha256"
    assert list(generator.CURRENT_SELECTORS + generator.STALE_SELECTORS) == FREEZER.CONFLICTING_FIELDS
    assert generator.BUILDER_SHA256 == FREEZER.EXPECTED_RUNTIME_GUARD_BUILDER_SHA256
    assert generator.STALE_BUILDER_SHA256 == FREEZER.STALE_RUNTIME_GUARD_BUILDER["sha256"]
    assert generator.EXTERNAL_NAMESPACE_BASELINE_SHA256 == FREEZER.EXTERNAL_NAMESPACE_BASELINE_SHA256


def test_actual_generator_document_cross_validates_with_retry2_freezer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    args, seed = _base_args(tmp_path, monkeypatch)
    spec = importlib.util.spec_from_file_location(
        "third_start_preflight_evidence_cross_validation", EVIDENCE_GENERATOR_SOURCE
    )
    assert spec and spec.loader
    generator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(generator)
    identities = _static_identities()
    previous = json.loads(args.previous_amendment.read_bytes())
    previous_review = json.loads(args.previous_review.read_bytes())
    protocol = FREEZER._expected_evidence_protocol(
        identities, previous, previous_review, seed
    )
    protocol_without_ca = dict(protocol)
    protocol_without_ca.pop("tsa_ca_certificates")
    seed_base = {
        key: copy.deepcopy(seed[key])
        for key in ("path", "directory_identity", "expected_entry_count", "exact_entries")
    }
    live = {"inventory_sha256": FREEZER.EXTERNAL_NAMESPACE_BASELINE_SHA256}
    conflict = {
        "conflict_path": {
            "path": identities["runtime-guard-builder"]["path"],
            "selector_count": 4,
            "selectors": FREEZER.CONFLICTING_FIELDS,
        },
        "conflicting_fields": FREEZER.CONFLICTING_FIELDS,
        "current_builder_identity": identities["runtime-guard-builder"],
        "stale_builder_identity": FREEZER.STALE_RUNTIME_GUARD_BUILDER,
    }
    flow = {
        "status": "PASS",
        "builder_source_sha256": FREEZER.EXPECTED_RUNTIME_GUARD_BUILDER_SHA256,
        "failure_precedes_activation_intent": True,
        "failure_precedes_unshare": True,
        "failure_precedes_iq_access": True,
        "frozen_iq_identity_collector_metadata_only": True,
    }
    monkeypatch.setattr(generator, "_capture_live_state", lambda: copy.deepcopy(live))
    monkeypatch.setattr(generator, "_validate_seed", lambda: copy.deepcopy(seed_base))
    monkeypatch.setattr(
        generator, "_validate_protocol",
        lambda: (copy.deepcopy(protocol_without_ca), {"builder": {"source": b""}}),
    )
    monkeypatch.setattr(generator, "_validate_conflict", lambda documents: copy.deepcopy(conflict))
    monkeypatch.setattr(generator, "_validate_control_flow", lambda source: copy.deepcopy(flow))
    monkeypatch.setattr(
        generator, "_journal_evidence",
        lambda: {"pid": 1813047, "argv": ["build_runtime_guard_v3.py", "start-persistent-namespace"]},
    )
    monkeypatch.setattr(generator, "_verify_rfc3161", lambda amendment, value: copy.deepcopy(seed["rfc3161"]))
    generated = generator.build_evidence(
        FREEZER.EXPECTED_EVIDENCE_GENERATOR_SHA256,
        FREEZER.EXPECTED_EVIDENCE_GENERATOR_TEST_SHA256,
        "2026-09-06T05:28:00Z",
    )
    FREEZER._validate_evidence(
        generated, identities=identities, previous=previous,
        previous_review=previous_review, root_seed=seed,
        schema=FREEZER.EVIDENCE_SCHEMA, status=FREEZER.EVIDENCE_STATUS,
        self_hash_field="evidence_payload_sha256",
    )
