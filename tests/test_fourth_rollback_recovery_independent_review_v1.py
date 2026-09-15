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
    / "work/blind-phase-confirmatory-v2/build_fourth_rollback_recovery_independent_review_v1.py"
)
REPO_REVIEW = (
    PROJECT_ROOT
    / "work/blind-phase-confirmatory-v2/fourth-rollback-recovery-independent-review-v1.json"
)
SPEC = importlib.util.spec_from_file_location("fourth_rollback_independent_review", SOURCE)
assert SPEC is not None and SPEC.loader is not None
review = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(review)


def _identity(name: str, size: int = 1) -> dict[str, object]:
    return {
        "path": f"/canonical/{name}",
        "size_bytes": size,
        "sha256": hashlib.sha256(name.encode()).hexdigest(),
    }


def test_review_document_is_deterministic_stage1_only_go() -> None:
    bindings = {
        name: _identity(name)
        for name in (
            "historical_builder",
            "patched_builder_before_recovery",
            "recovery_runner",
            "recovery_runner_test",
            "review_generator",
            "review_generator_test",
        )
    }
    first = review._review_document(bindings)
    second = review._review_document(bindings)
    assert first == second
    assert first["status"] == "GO"
    assert first["scope"] == {
        "stage1_exact_rollback_runner": True,
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


def test_reviewed_contract_binds_constants_and_exact_historical_callsite() -> None:
    runner = SOURCE.parent.joinpath("recover_fourth_rollback_v3.py").read_bytes()
    historical = Path(
        "/var/lib/telemetry-yield-confirmatory-v3/build_runtime_guard_v3.py"
    )
    if not historical.exists():
        pytest.skip("historical root-control builder is unavailable")
    review._validate_reviewed_contract(runner, historical.read_bytes())
    changed = historical.read_bytes().replace(
        review.HISTORICAL_OPEN_SOURCE.encode(), b"parent_fds[label] = os.open(path)"
    )
    with pytest.raises(review.ReviewError, match="callsite differs"):
        review._validate_reviewed_contract(runner, changed)


def test_read_regular_rejects_digest_size_and_hardlink_drift(tmp_path: Path) -> None:
    path = tmp_path / "input"
    path.write_bytes(b"reviewed")
    digest = hashlib.sha256(b"reviewed").hexdigest()
    _, identity = review._read_regular(path, digest, len(b"reviewed"))
    assert identity == {
        "path": str(path),
        "size_bytes": 8,
        "sha256": digest,
    }
    with pytest.raises(review.ReviewError, match="identity or metadata differs"):
        review._read_regular(path, "0" * 64, len(b"reviewed"))
    with pytest.raises(review.ReviewError, match="identity or metadata differs"):
        review._read_regular(path, digest, 7)
    hardlink = tmp_path / "hardlink"
    os.link(path, hardlink)
    with pytest.raises(review.ReviewError, match="identity or metadata differs"):
        review._read_regular(path, digest, len(b"reviewed"))


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


def test_review_generator_has_no_recovery_or_lifecycle_execution_surface() -> None:
    source = SOURCE.read_text(encoding="utf-8")
    assert "subprocess" not in source
    assert "os.setns" not in source
    assert "os.unshare" not in source
    assert "os.kill" not in source
    parser = review._parser()
    assert {action.dest for action in parser._actions} == {
        "help",
        "expected_generator_sha256",
        "expected_generator_test_sha256",
        "publish",
    }


def test_canonical_payload_is_strict_json() -> None:
    document = review._review_document({"runner": _identity("runner")})
    payload = review._payload(document)
    assert payload.endswith(b"\n")
    assert json.loads(payload) == document


def test_repo_review_is_exact_deterministic_canonical_payload() -> None:
    generator_payload = SOURCE.read_bytes()
    test_payload = Path(__file__).read_bytes()
    bindings = {
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
        "recovery_runner": {
            "path": str(review.RUNNER),
            "size_bytes": review.EXPECTED_RUNNER_SIZE,
            "sha256": review.EXPECTED_RUNNER_SHA256,
        },
        "recovery_runner_test": {
            "path": str(review.RUNNER_TEST),
            "size_bytes": review.EXPECTED_RUNNER_TEST_SIZE,
            "sha256": review.EXPECTED_RUNNER_TEST_SHA256,
        },
        "review_generator": {
            "path": str(review.GENERATOR),
            "size_bytes": len(generator_payload),
            "sha256": hashlib.sha256(generator_payload).hexdigest(),
        },
        "review_generator_test": {
            "path": str(review.GENERATOR_TEST),
            "size_bytes": len(test_payload),
            "sha256": hashlib.sha256(test_payload).hexdigest(),
        },
    }
    expected = review._review_document(bindings)
    payload = REPO_REVIEW.read_bytes()
    assert payload == review._payload(expected)
    observed = json.loads(payload)
    assert observed["schema_version"] == review.SCHEMA
    assert observed["status"] == "GO"
    assert observed["review_payload_sha256"] == review._sha256_document(
        {
            key: value
            for key, value in observed.items()
            if key != "review_payload_sha256"
        }
    )
