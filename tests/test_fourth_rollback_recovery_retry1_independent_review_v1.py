from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE = (
    PROJECT_ROOT
    / "work/blind-phase-confirmatory-v2/"
    "build_fourth_rollback_recovery_retry1_independent_review_v1.py"
)
REPO_REVIEW = (
    PROJECT_ROOT
    / "work/blind-phase-confirmatory-v2/"
    "fourth-rollback-recovery-retry1-independent-review-v1.json"
)
SPEC = importlib.util.spec_from_file_location(
    "fourth_rollback_retry1_independent_review", SOURCE
)
assert SPEC is not None and SPEC.loader is not None
review = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(review)


def _identity(name: str, size: int = 1) -> dict[str, object]:
    return {
        "path": f"/canonical/{name}",
        "size_bytes": size,
        "sha256": hashlib.sha256(name.encode()).hexdigest(),
    }


def _repo_bindings() -> dict[str, dict[str, object]]:
    generator = SOURCE.read_bytes()
    generator_test = Path(__file__).read_bytes()
    return {
        "historical_builder": {
            "path": str(review.HISTORICAL_BUILDER),
            "size_bytes": review.EXPECTED_HISTORICAL_SIZE,
            "sha256": review.EXPECTED_HISTORICAL_SHA256,
        },
        "patched_builder_before_recovery": {
            "path": str(review.PATCHED_BUILDER),
            "size_bytes": review.EXPECTED_PATCHED_SIZE,
            "sha256": review.EXPECTED_PATCHED_SHA256,
        },
        "recovery_runner_retry1": {
            "path": str(review.RUNNER),
            "size_bytes": review.EXPECTED_RUNNER_SIZE,
            "sha256": review.EXPECTED_RUNNER_SHA256,
        },
        "recovery_runner_retry1_test": {
            "path": str(review.RUNNER_TEST),
            "size_bytes": review.EXPECTED_RUNNER_TEST_SIZE,
            "sha256": review.EXPECTED_RUNNER_TEST_SHA256,
        },
        "review_generator_retry1": {
            "path": str(review.GENERATOR),
            "size_bytes": len(generator),
            "sha256": hashlib.sha256(generator).hexdigest(),
        },
        "review_generator_retry1_test": {
            "path": str(review.GENERATOR_TEST),
            "size_bytes": len(generator_test),
            "sha256": hashlib.sha256(generator_test).hexdigest(),
        },
        "superseded_prior_review": {
            "path": str(review.PRIOR_REVIEW),
            "size_bytes": review.EXPECTED_PRIOR_REVIEW_SIZE,
            "sha256": review.EXPECTED_PRIOR_REVIEW_SHA256,
        },
    }


def test_review_document_is_deterministic_retry1_stage1_only_go() -> None:
    bindings = {name: _identity(name) for name in _repo_bindings()}
    first = review._review_document(bindings)
    second = review._review_document(bindings)
    assert first == second
    assert first["status"] == "GO"
    assert first["scope"] == {
        "stage1_exact_retry1_rollback_runner": True,
        "stage2_abort_and_transaction_cleanup": False,
        "stage2_status": "DEFERRED_UNTIL_ACTUAL_ROLLBACK_READY_AND_COMPLETE_IDENTITIES_EXIST",
        "lifecycle_or_recovery_executed_by_independent_review": False,
    }
    assert first["findings"]["p0_open"] == 0
    assert first["findings"]["p1_open"] == 0
    assert first["findings"]["p2_open"] == 0
    assert all(first["verification"].values())
    unhashed = {key: value for key, value in first.items() if key != "review_payload_sha256"}
    assert first["review_payload_sha256"] == review._sha256_document(unhashed)


def test_reviewed_contract_binds_retry1_constants_callsite_and_prior_review() -> None:
    runner = SOURCE.parent.joinpath("recover_fourth_rollback_v3.py").read_bytes()
    historical = review.HISTORICAL_BUILDER
    prior = review.PRIOR_REVIEW
    if not historical.exists() or not prior.exists():
        pytest.skip("live historical/prior review evidence is unavailable")
    review._validate_reviewed_contract(
        runner, historical.read_bytes(), prior.read_bytes()
    )
    changed = historical.read_bytes().replace(
        review.HISTORICAL_OPEN_SOURCE.encode(), b"parent_fds[label] = os.open(path)"
    )
    with pytest.raises(review.ReviewError, match="callsite differs"):
        review._validate_reviewed_contract(runner, changed, prior.read_bytes())


def test_read_regular_rejects_digest_size_and_hardlink_drift(tmp_path: Path) -> None:
    path = tmp_path / "input"
    path.write_bytes(b"reviewed")
    digest = hashlib.sha256(b"reviewed").hexdigest()
    _, identity = review._read_regular(path, digest, 8)
    assert identity == {"path": str(path), "size_bytes": 8, "sha256": digest}
    with pytest.raises(review.ReviewError, match="identity or metadata differs"):
        review._read_regular(path, "0" * 64, 8)
    with pytest.raises(review.ReviewError, match="identity or metadata differs"):
        review._read_regular(path, digest, 7)
    hardlink = tmp_path / "hardlink"
    os.link(path, hardlink)
    with pytest.raises(review.ReviewError, match="identity or metadata differs"):
        review._read_regular(path, digest, 8)


def test_write_all_handles_partial_writes_and_rejects_zero_progress(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    written = bytearray()

    def partial(_descriptor: int, payload: bytes) -> int:
        count = min(2, len(payload))
        written.extend(payload[:count])
        return count

    monkeypatch.setattr(review.os, "write", partial)
    review._write_all(7, b"abcdef")
    assert written == b"abcdef"
    monkeypatch.setattr(review.os, "write", lambda _descriptor, _payload: 0)
    with pytest.raises(review.ReviewError, match="no forward progress"):
        review._write_all(7, b"x")


def test_publish_requires_root_before_any_output_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(review.os, "geteuid", lambda: 1000)
    with pytest.raises(review.ReviewError, match="requires effective root"):
        review.publish_review({"review_payload_sha256": "0" * 64})


def test_generator_has_no_recovery_or_lifecycle_execution_surface() -> None:
    source = SOURCE.read_text(encoding="utf-8")
    assert "subprocess" not in source
    assert "os.setns" not in source
    assert "os.unshare" not in source
    assert "os.kill" not in source
    assert {action.dest for action in review._parser()._actions} == {
        "help", "expected_generator_sha256", "expected_generator_test_sha256", "publish",
    }


def test_canonical_payload_is_strict_json() -> None:
    document = review._review_document({"runner": _identity("runner")})
    payload = review._payload(document)
    assert payload.endswith(b"\n")
    assert json.loads(payload) == document


def test_repo_review_is_exact_deterministic_canonical_payload() -> None:
    expected = review._review_document(_repo_bindings())
    payload = REPO_REVIEW.read_bytes()
    assert payload == review._payload(expected)
    observed = json.loads(payload)
    assert observed["schema_version"] == review.SCHEMA
    assert observed["status"] == "GO"
    assert observed["review_payload_sha256"] == review._sha256_document(
        {key: value for key, value in observed.items() if key != "review_payload_sha256"}
    )
