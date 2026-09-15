from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import ssl
import stat
import sys
from datetime import datetime, timezone

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "work/blind-phase-confirmatory-v2/acquire_holdout.py"


def _module():
    name = "blind_phase_holdout_acquisition_test_module"
    spec = importlib.util.spec_from_file_location(name, SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


ACQUIRE = _module()


class FakeResponse:
    def __init__(self, *, url: str, status: int, headers: dict[str, str], payload: bytes):
        self.url = url
        self.status = status
        self.headers = headers
        self.payload = payload
        self.offset = 0
        self.timeouts: list[float] = []

    def geturl(self) -> str:
        return self.url

    def read(self, amount: int = -1) -> bytes:
        if amount < 0:
            amount = len(self.payload) - self.offset
        value = self.payload[self.offset : self.offset + amount]
        self.offset += len(value)
        return value

    def settimeout(self, timeout: float) -> None:
        self.timeouts.append(timeout)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class DetachingResponse(FakeResponse):
    """Model http.client detaching its socket after the framed body is read."""

    def settimeout(self, timeout: float) -> None:
        if self.offset == len(self.payload):
            raise ValueError("I/O operation on closed file")
        super().settimeout(timeout)

    def isclosed(self) -> bool:
        return self.offset == len(self.payload)


class FakeOpener:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def open(self, request, *, timeout):
        self.requests.append((request, timeout))
        return self.responses.pop(0)


class MultiHeaders(dict[str, str]):
    def __init__(self, values: dict[str, list[str]]):
        super().__init__({name: items[-1] for name, items in values.items()})
        self.values = values

    def get_all(self, name: str, default=None):
        return self.values.get(name, default)


def _spec(size: int = 12):
    return ACQUIRE.DownloadSpec(
        observation_id=999,
        satellite_id="SAT",
        object_key="synthetic-999.iq",
        source_url="https://example.invalid/iq-data/synthetic-999.iq",
        expected_size_bytes=size,
    )


TIMESTAMP_SHA256 = "3" * 64


def _validation_record(spec, path: Path, payload: bytes) -> dict[str, object]:
    return {
        "schema_version": ACQUIRE.RECEIPT_SCHEMA,
        "selection_sha256": "1" * 64,
        "execution_plan_sha256": "2" * 64,
        "execution_plan_timestamp_response_sha256": TIMESTAMP_SHA256,
        "observation_id": spec.observation_id,
        "satellite_id": spec.satellite_id,
        "row_sha256": spec.row_sha256,
        "object_key": spec.object_key,
        "source_url": spec.source_url,
        "expected_size_bytes": len(payload),
        "actual_size_bytes": len(payload),
        "local_path": str(path.absolute()),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }


def _install_timestamp_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, plan_sha256: str
) -> tuple[Path, Path]:
    request = tmp_path / "execution-plan.tsq"
    response = tmp_path / "execution-plan.tsr"
    request.write_bytes(b"synthetic timestamp query")
    response.write_bytes(b"synthetic timestamp response")
    request_identity = ACQUIRE._identity(request)
    response_identity = ACQUIRE._identity(response)

    def verify(**kwargs):
        assert kwargs["execution_plan_sha256"] == plan_sha256
        assert kwargs["request_path"] == request
        assert kwargs["response_path"] == response
        return {
            "schema_version": "blind-phase-execution-plan-rfc3161-verification-v1",
            "status": "PASS",
            "execution_plan_sha256": plan_sha256,
            "timestamp_request": request_identity,
            "timestamp_response": response_identity,
            "message_imprint_sha256": plan_sha256,
            "generation_time_utc": "2026-09-04T00:00:00Z",
            "verified_before_acquisition_start": True,
            "certificate_chain": "test",
            "openssl": "test",
        }

    monkeypatch.setattr(ACQUIRE, "_verify_execution_plan_timestamp", verify)
    return request, response


def test_download_is_bounded_content_bound_read_only_and_idempotent(tmp_path: Path) -> None:
    payload = b"abcdefghijkl"
    spec = _spec(len(payload))
    opener = FakeOpener(
        [
            FakeResponse(
                url=spec.source_url,
                status=200,
                headers={"Content-Length": str(len(payload)), "ETag": '"object-v1"'},
                payload=payload,
            )
        ]
    )
    receipt = ACQUIRE.download_one(
        spec,
        destination_root=tmp_path,
        selection_sha256="1" * 64,
        execution_plan_sha256="2" * 64,
        execution_plan_timestamp_response_sha256=TIMESTAMP_SHA256,
        opener=opener,
        chunk_bytes=3,
    )
    final_path = Path(receipt["local_path"])
    assert final_path.read_bytes() == payload
    assert final_path.stat().st_mode & 0o777 == 0o444
    assert receipt["sha256"] == hashlib.sha256(payload).hexdigest()
    assert receipt["http_validator_kind"] == "strong_etag"
    resumed = ACQUIRE.download_one(
        spec,
        destination_root=tmp_path,
        selection_sha256="1" * 64,
        execution_plan_sha256="2" * 64,
        execution_plan_timestamp_response_sha256=TIMESTAMP_SHA256,
        opener=FakeOpener([]),
    )
    assert resumed == receipt
    receipt_path = final_path.with_name(final_path.name + ".receipt.json")
    corrupted = json.loads(receipt_path.read_text(encoding="utf-8"))
    corrupted["satellite_id"] = "MUTATED"
    receipt_path.chmod(0o600)
    receipt_path.write_text(json.dumps(corrupted), encoding="utf-8")
    receipt_path.chmod(0o444)
    with pytest.raises(ValueError, match="receipt self-hash"):
        ACQUIRE.download_one(
            spec,
            destination_root=tmp_path,
            selection_sha256="1" * 64,
            execution_plan_sha256="2" * 64,
            execution_plan_timestamp_response_sha256=TIMESTAMP_SHA256,
            opener=FakeOpener([]),
        )


def test_download_accepts_socket_detached_after_exact_content_length(
    tmp_path: Path,
) -> None:
    payload = b"abcdefghijkl"
    spec = _spec(len(payload))
    response = DetachingResponse(
        url=spec.source_url,
        status=200,
        headers={"Content-Length": str(len(payload)), "ETag": '"object-v1"'},
        payload=payload,
    )
    receipt = ACQUIRE.download_one(
        spec,
        destination_root=tmp_path,
        selection_sha256="1" * 64,
        execution_plan_sha256="2" * 64,
        execution_plan_timestamp_response_sha256=TIMESTAMP_SHA256,
        opener=FakeOpener([response]),
        chunk_bytes=3,
    )
    assert receipt["actual_size_bytes"] == len(payload)
    assert receipt["sha256"] == hashlib.sha256(payload).hexdigest()
    assert len(response.timeouts) == 4


def test_completion_probe_still_fails_when_response_is_not_confirmed_closed() -> None:
    class TimeoutFailure(FakeResponse):
        def settimeout(self, timeout: float) -> None:
            raise ValueError("cannot set timeout")

        def isclosed(self) -> bool:
            return False

    response = TimeoutFailure(
        url="https://example.invalid/object",
        status=200,
        headers={},
        payload=b"",
    )
    with pytest.raises(ValueError, match="cannot set timeout"):
        ACQUIRE._assert_response_body_complete(response, 1.0)


def test_completion_probe_still_rejects_bytes_beyond_content_length() -> None:
    response = FakeResponse(
        url="https://example.invalid/object",
        status=200,
        headers={},
        payload=b"unexpected",
    )
    with pytest.raises(ValueError, match="bytes beyond Content-Length"):
        ACQUIRE._assert_response_body_complete(response, 1.0)
    assert response.timeouts == [1.0]


def test_missing_socket_before_first_read_fails_even_if_response_claims_closed(
    tmp_path: Path,
) -> None:
    class PrematurelyClosedResponse(FakeResponse):
        def settimeout(self, timeout: float) -> None:
            raise ValueError("cannot enforce pre-read timeout")

        def isclosed(self) -> bool:
            return True

    payload = b"abcdefghijkl"
    spec = _spec(len(payload))
    with pytest.raises(ValueError, match="cannot enforce pre-read timeout"):
        ACQUIRE.download_one(
            spec,
            destination_root=tmp_path,
            selection_sha256="1" * 64,
            execution_plan_sha256="2" * 64,
            execution_plan_timestamp_response_sha256=TIMESTAMP_SHA256,
            opener=FakeOpener(
                [
                    PrematurelyClosedResponse(
                        url=spec.source_url,
                        status=200,
                        headers={
                            "Content-Length": str(len(payload)),
                            "ETag": '"object-v1"',
                        },
                        payload=payload,
                    )
                ]
            ),
            chunk_bytes=3,
        )
    directory = tmp_path / f"observation-{spec.observation_id}"
    assert not (directory / spec.object_key).exists()


def test_persisted_receipt_requires_immutable_single_link_identity(tmp_path: Path) -> None:
    payload = b"abcdefghijkl"
    spec = _spec(len(payload))
    receipt = ACQUIRE.download_one(
        spec,
        destination_root=tmp_path,
        selection_sha256="1" * 64,
        execution_plan_sha256="2" * 64,
        execution_plan_timestamp_response_sha256=TIMESTAMP_SHA256,
        opener=FakeOpener(
            [
                FakeResponse(
                    url=spec.source_url,
                    status=200,
                    headers={"Content-Length": "12", "ETag": '"object-v1"'},
                    payload=payload,
                )
            ]
        ),
    )
    receipt_path = Path(receipt["local_path"]).with_name(
        spec.object_key + ".receipt.json"
    )
    receipt_path.chmod(0o600)
    with pytest.raises(ValueError, match="receipt must be owned.*mode 0444"):
        ACQUIRE.download_one(
            spec,
            destination_root=tmp_path,
            selection_sha256="1" * 64,
            execution_plan_sha256="2" * 64,
            execution_plan_timestamp_response_sha256=TIMESTAMP_SHA256,
            opener=FakeOpener([]),
        )
    receipt_path.chmod(0o444)
    alias = receipt_path.with_name("receipt-hardlink-alias")
    os.link(receipt_path, alias)
    with pytest.raises(ValueError, match="receipt must be owned.*singly linked"):
        ACQUIRE.download_one(
            spec,
            destination_root=tmp_path,
            selection_sha256="1" * 64,
            execution_plan_sha256="2" * 64,
            execution_plan_timestamp_response_sha256=TIMESTAMP_SHA256,
            opener=FakeOpener([]),
        )


def test_publish_persists_final_mode_and_temporary_unlink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory_fd = os.open(tmp_path, ACQUIRE.DIRECTORY_FLAGS)
    original_fsync = ACQUIRE.os.fsync
    events: list[tuple[str, int]] = []

    def recording_fsync(descriptor: int) -> None:
        status = os.fstat(descriptor)
        events.append(
            (
                "directory" if stat.S_ISDIR(status.st_mode) else "file",
                stat.S_IMODE(status.st_mode),
            )
        )
        original_fsync(descriptor)

    monkeypatch.setattr(ACQUIRE.os, "fsync", recording_fsync)
    try:
        ACQUIRE._publish_bytes_at(directory_fd, "artifact.json", b"{}\n")
    finally:
        os.close(directory_fd)
    assert ("file", 0o444) in events
    assert sum(kind == "directory" for kind, _ in events) >= 2
    assert sorted(path.name for path in tmp_path.iterdir()) == ["artifact.json"]


def test_json_readers_recheck_post_read_inode_mode_and_link_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    persisted = tmp_path / "persisted.json"
    directory_fd = os.open(tmp_path, ACQUIRE.DIRECTORY_FLAGS)
    ACQUIRE._publish_bytes_at(directory_fd, persisted.name, b'{"ok":true}\n')
    original_read_fd = ACQUIRE._read_fd

    def read_then_chmod(descriptor: int, *, maximum_bytes: int) -> bytes:
        payload = original_read_fd(descriptor, maximum_bytes=maximum_bytes)
        persisted.chmod(0o400)
        return payload

    monkeypatch.setattr(ACQUIRE, "_read_fd", read_then_chmod)
    try:
        with pytest.raises(ValueError, match="owner, mode, link count, or size changed"):
            ACQUIRE._load_json_at(directory_fd, persisted.name, label="persisted test")
    finally:
        os.close(directory_fd)

    monkeypatch.setattr(ACQUIRE, "_read_fd", original_read_fd)
    ordinary = tmp_path / "ordinary.json"
    ordinary.write_text('{"ok":true}\n', encoding="utf-8")
    alias = tmp_path / "ordinary-alias.json"

    def read_then_link(descriptor: int, *, maximum_bytes: int) -> bytes:
        payload = original_read_fd(descriptor, maximum_bytes=maximum_bytes)
        os.link(ordinary, alias)
        return payload

    monkeypatch.setattr(ACQUIRE, "_read_fd", read_then_link)
    with pytest.raises(ValueError, match="owner, mode, link count, or size changed"):
        ACQUIRE._load_json_with_identity(ordinary, label="ordinary test")


def test_hash_regular_rechecks_path_and_metadata_after_hash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "object.bin"
    path.write_bytes(b"abcdefghijkl")
    path.chmod(0o444)
    directory_fd = os.open(tmp_path, ACQUIRE.DIRECTORY_FLAGS)
    original_hash_fd = ACQUIRE._hash_fd

    def hash_then_drift(descriptor: int, **kwargs):
        result = original_hash_fd(descriptor, **kwargs)
        path.chmod(0o400)
        return result

    monkeypatch.setattr(ACQUIRE, "_hash_fd", hash_then_drift)
    try:
        with pytest.raises(ValueError, match="owner, mode, link count, or size changed"):
            ACQUIRE._hash_regular_at(directory_fd, path.name, label="rehash test")
    finally:
        os.close(directory_fd)


def test_final_before_receipt_crash_recovers_offline_with_bound_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = b"abcdefghijkl"
    spec = _spec(len(payload))
    original_publish_receipt = ACQUIRE._publish_receipt_at

    def crash_before_receipt(*args, **kwargs):
        raise RuntimeError("injected final-before-receipt crash")

    monkeypatch.setattr(ACQUIRE, "_publish_receipt_at", crash_before_receipt)
    with pytest.raises(RuntimeError, match="final-before-receipt"):
        ACQUIRE.download_one(
            spec,
            destination_root=tmp_path,
            selection_sha256="1" * 64,
            execution_plan_sha256="2" * 64,
            execution_plan_timestamp_response_sha256=TIMESTAMP_SHA256,
            opener=FakeOpener(
                [
                    FakeResponse(
                        url=spec.source_url,
                        status=200,
                        headers={"Content-Length": "12", "ETag": '"object-v1"'},
                        payload=payload,
                    )
                ]
            ),
        )
    directory = tmp_path / f"observation-{spec.observation_id}"
    final_path = directory / spec.object_key
    partial_path = directory / f".{spec.object_key}.part"
    state_path = directory / f".{spec.object_key}.state.json"
    assert final_path.exists() and partial_path.exists() and state_path.exists()
    assert final_path.stat().st_ino == partial_path.stat().st_ino
    assert final_path.stat().st_nlink == 2

    monkeypatch.setattr(ACQUIRE, "_publish_receipt_at", original_publish_receipt)
    offline = FakeOpener([])
    receipt = ACQUIRE.download_one(
        spec,
        destination_root=tmp_path,
        selection_sha256="1" * 64,
        execution_plan_sha256="2" * 64,
        execution_plan_timestamp_response_sha256=TIMESTAMP_SHA256,
        opener=offline,
    )
    assert offline.requests == []
    assert receipt["http_validator_value"] == '"object-v1"'
    assert final_path.read_bytes() == payload
    assert final_path.stat().st_nlink == 1
    assert not partial_path.exists()
    assert not state_path.exists()


def test_interrupted_download_restarts_from_zero_with_same_strong_validator(tmp_path: Path) -> None:
    payload = b"abcdefghijkl"
    first = payload[:5]
    spec = _spec(len(payload))
    with pytest.raises(IOError, match="ended before"):
        ACQUIRE.download_one(
            spec,
            destination_root=tmp_path,
            selection_sha256="1" * 64,
            execution_plan_sha256="2" * 64,
            execution_plan_timestamp_response_sha256=TIMESTAMP_SHA256,
            opener=FakeOpener(
                [
                    FakeResponse(
                        url=spec.source_url,
                        status=200,
                        headers={"Content-Length": str(len(payload)), "ETag": '"object-v1"'},
                        payload=first,
                    )
                ]
            ),
            chunk_bytes=3,
        )
    resume_opener = FakeOpener(
        [
            FakeResponse(
                url=spec.source_url,
                status=200,
                headers={
                    "Content-Length": str(len(payload)),
                    "ETag": '"object-v1"',
                },
                payload=payload,
            )
        ]
    )
    receipt = ACQUIRE.download_one(
        spec,
        destination_root=tmp_path,
        selection_sha256="1" * 64,
        execution_plan_sha256="2" * 64,
        execution_plan_timestamp_response_sha256=TIMESTAMP_SHA256,
        opener=resume_opener,
        chunk_bytes=2,
    )
    request = resume_opener.requests[0][0]
    assert request.get_header("Range") is None
    assert request.get_header("If-match") == '"object-v1"'
    assert Path(receipt["local_path"]).read_bytes() == payload


def test_resume_rejects_validator_drift_and_fresh_redirect(tmp_path: Path) -> None:
    payload = b"abcdefghijkl"
    spec = _spec(len(payload))
    with pytest.raises(IOError):
        ACQUIRE.download_one(
            spec,
            destination_root=tmp_path,
            selection_sha256="1" * 64,
            execution_plan_sha256="2" * 64,
            execution_plan_timestamp_response_sha256=TIMESTAMP_SHA256,
            opener=FakeOpener(
                [FakeResponse(url=spec.source_url, status=200, headers={"Content-Length": "12", "ETag": '"v1"'}, payload=b"a")]
            ),
        )
    with pytest.raises(ValueError, match="validator changed"):
        ACQUIRE.download_one(
            spec,
            destination_root=tmp_path,
            selection_sha256="1" * 64,
            execution_plan_sha256="2" * 64,
            execution_plan_timestamp_response_sha256=TIMESTAMP_SHA256,
            opener=FakeOpener(
                [FakeResponse(url=spec.source_url, status=200, headers={"Content-Length": "12", "ETag": '"v2"'}, payload=payload)]
            ),
        )
    other = tmp_path / "redirect"
    with pytest.raises(ValueError, match="redirected"):
        ACQUIRE.download_one(
            spec,
            destination_root=other,
            selection_sha256="1" * 64,
            execution_plan_sha256="2" * 64,
            execution_plan_timestamp_response_sha256=TIMESTAMP_SHA256,
            opener=FakeOpener(
                [FakeResponse(url="https://other.invalid/object", status=200, headers={"Content-Length": "12", "ETag": '"v1"'}, payload=payload)]
            ),
        )


def test_acquisition_manifest_is_exact_all_30_and_resume_verified(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rows = [
        {
            "observation_id": 10_000 + index,
            "satellite_id": f"SAT-{index % 3}",
            "object_key": f"synthetic-{index}.iq",
            "url": f"https://example.invalid/iq-data/synthetic-{index}.iq",
            "size_bytes": 4,
        }
        for index in range(30)
    ]
    selection_path = tmp_path / "selection.json"
    plan_path = tmp_path / "plan.json"
    selection_path.write_text(
        json.dumps(
            {"schema_version": "blind-phase-prospective-holdout-selection-v2", "selected": rows}
        ),
        encoding="utf-8",
    )
    selection_sha = hashlib.sha256(selection_path.read_bytes()).hexdigest()
    selection_identity = ACQUIRE._identity(selection_path)
    plan_path.write_text(
        json.dumps(
            {
                "schema_version": "blind-phase-confirmatory-execution-plan-v2",
                "status": "frozen_before_selected_iq_download_and_decoder_execution",
                "provenance": {
                    "fixed_metadata_inputs": [selection_identity],
                    "campaign_execution_tools": {
                        "acquisition": ACQUIRE._identity(SCRIPT)
                    },
                },
                "release_gates": {
                    "selected_iq_absent_during_plan_freeze": True,
                    "candidate_decoder_config_and_runtime_locked_before_download": True,
                    "component_baseline_a_runtime_locked_before_download": True,
                    "bwrap_no_network_read_only_canary_passed_before_download": True,
                },
            }
        ),
        encoding="utf-8",
    )
    plan_sha = hashlib.sha256(plan_path.read_bytes()).hexdigest()
    timestamp_request, timestamp_response = _install_timestamp_gate(
        tmp_path, monkeypatch, plan_sha
    )
    timestamp_response_sha = ACQUIRE._identity(timestamp_response)["sha256"]
    destination = tmp_path / "objects"

    def fake_download(
        spec,
        *,
        destination_root,
        selection_sha256,
        execution_plan_sha256,
        execution_plan_timestamp_response_sha256,
        opener,
        absolute_deadline,
        clock,
    ):
        assert execution_plan_timestamp_response_sha256 == timestamp_response_sha
        assert absolute_deadline > clock() - 1
        path = destination_root / f"observation-{spec.observation_id}" / spec.object_key
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        path.parent.chmod(0o700)
        path.write_bytes(b"ci16")
        path.chmod(0o444)
        return {
            "schema_version": ACQUIRE.RECEIPT_SCHEMA,
            "selection_sha256": selection_sha256,
            "execution_plan_sha256": execution_plan_sha256,
            "execution_plan_timestamp_response_sha256": (
                execution_plan_timestamp_response_sha256
            ),
            "observation_id": spec.observation_id,
            "satellite_id": spec.satellite_id,
            "row_sha256": spec.row_sha256,
            "object_key": spec.object_key,
            "source_url": spec.source_url,
            "expected_size_bytes": 4,
            "local_path": str(path.absolute()),
            "actual_size_bytes": 4,
            "sha256": hashlib.sha256(b"ci16").hexdigest(),
        }

    monkeypatch.setattr(ACQUIRE, "download_one", fake_download)
    manifest_path = tmp_path / "acquisition.json"
    manifest = ACQUIRE.acquire_all(
        selection_path=selection_path,
        expected_selection_sha256=selection_sha,
        execution_plan_path=plan_path,
        expected_execution_plan_sha256=plan_sha,
        execution_plan_timestamp_request_path=timestamp_request,
        execution_plan_timestamp_response_path=timestamp_response,
        destination_root=destination,
        manifest_path=manifest_path,
        opener=FakeOpener([]),
    )
    assert manifest["counts"] == {"observations": 30, "total_size_bytes": 120}
    assert manifest["claim_guard"]["iq_content_interpreted"] is False
    assert len(manifest["objects"]) == 30
    sidecar = Path(str(manifest_path) + ".sha256")
    assert sidecar.read_text(encoding="ascii") == (
        f"{hashlib.sha256(manifest_path.read_bytes()).hexdigest()}  {manifest_path.name}\n"
    )
    sidecar.chmod(0o600)
    with pytest.raises(ValueError, match="sidecar must be owned.*mode 0444"):
        ACQUIRE.acquire_all(
            selection_path=selection_path,
            expected_selection_sha256=selection_sha,
            execution_plan_path=plan_path,
            expected_execution_plan_sha256=plan_sha,
            execution_plan_timestamp_request_path=timestamp_request,
            execution_plan_timestamp_response_path=timestamp_response,
            destination_root=destination,
            manifest_path=manifest_path,
            opener=FakeOpener([]),
        )
    sidecar.chmod(0o444)
    manifest_path.chmod(0o600)
    with pytest.raises(ValueError, match="manifest must be owned.*mode 0444"):
        ACQUIRE.acquire_all(
            selection_path=selection_path,
            expected_selection_sha256=selection_sha,
            execution_plan_path=plan_path,
            expected_execution_plan_sha256=plan_sha,
            execution_plan_timestamp_request_path=timestamp_request,
            execution_plan_timestamp_response_path=timestamp_response,
            destination_root=destination,
            manifest_path=manifest_path,
            opener=FakeOpener([]),
        )
    manifest_path.chmod(0o444)
    resumed = ACQUIRE.acquire_all(
        selection_path=selection_path,
        expected_selection_sha256=selection_sha,
        execution_plan_path=plan_path,
        expected_execution_plan_sha256=plan_sha,
        execution_plan_timestamp_request_path=timestamp_request,
        execution_plan_timestamp_response_path=timestamp_response,
        destination_root=destination,
        manifest_path=manifest_path,
        opener=FakeOpener([]),
    )
    assert resumed == manifest
    sidecar.unlink()
    recovered = ACQUIRE.acquire_all(
        selection_path=selection_path,
        expected_selection_sha256=selection_sha,
        execution_plan_path=plan_path,
        expected_execution_plan_sha256=plan_sha,
        execution_plan_timestamp_request_path=timestamp_request,
        execution_plan_timestamp_response_path=timestamp_response,
        destination_root=destination,
        manifest_path=manifest_path,
        opener=FakeOpener([]),
    )
    assert recovered == manifest
    assert sidecar.read_text(encoding="ascii") == (
        f"{hashlib.sha256(manifest_path.read_bytes()).hexdigest()}  {manifest_path.name}\n"
    )


def test_acquisition_preflights_disk_before_any_download(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rows = [
        {
            "observation_id": 20_000 + index,
            "satellite_id": "SAT",
            "object_key": f"disk-{index}.iq",
            "url": f"https://example.invalid/iq-data/disk-{index}.iq",
            "size_bytes": 4,
        }
        for index in range(30)
    ]
    selection = tmp_path / "selection.json"
    selection.write_text(
        json.dumps(
            {"schema_version": "blind-phase-prospective-holdout-selection-v2", "selected": rows}
        ),
        encoding="utf-8",
    )
    selection_identity = ACQUIRE._identity(selection)
    plan = tmp_path / "plan.json"
    plan.write_text(
        json.dumps(
            {
                "schema_version": "blind-phase-confirmatory-execution-plan-v2",
                "status": "frozen_before_selected_iq_download_and_decoder_execution",
                "provenance": {
                    "fixed_metadata_inputs": [selection_identity],
                    "campaign_execution_tools": {
                        "acquisition": ACQUIRE._identity(SCRIPT)
                    },
                },
                "release_gates": {
                    "selected_iq_absent_during_plan_freeze": True,
                    "candidate_decoder_config_and_runtime_locked_before_download": True,
                    "component_baseline_a_runtime_locked_before_download": True,
                    "bwrap_no_network_read_only_canary_passed_before_download": True,
                },
            }
        ),
        encoding="utf-8",
    )
    plan_sha = hashlib.sha256(plan.read_bytes()).hexdigest()
    timestamp_request, timestamp_response = _install_timestamp_gate(
        tmp_path, monkeypatch, plan_sha
    )
    calls = 0

    def forbidden_download(*args, **kwargs):
        nonlocal calls
        calls += 1
        raise AssertionError("download must not start")

    monkeypatch.setattr(ACQUIRE, "download_one", forbidden_download)
    monkeypatch.setattr(ACQUIRE, "_available_free_bytes", lambda descriptor: 1)
    with pytest.raises(ValueError, match="insufficient free disk"):
        ACQUIRE.acquire_all(
            selection_path=selection,
            expected_selection_sha256=hashlib.sha256(selection.read_bytes()).hexdigest(),
            execution_plan_path=plan,
            expected_execution_plan_sha256=plan_sha,
            execution_plan_timestamp_request_path=timestamp_request,
            execution_plan_timestamp_response_path=timestamp_response,
            destination_root=tmp_path / "objects",
            manifest_path=tmp_path / "manifest.json",
        )
    assert calls == 0


@pytest.mark.parametrize(
    ("headers", "status", "error"),
    [
        ({"Content-Length": "+12", "ETag": '"v1"'}, 200, "Content-Length"),
        (
            MultiHeaders(
                {"Content-Length": ["12", "12"], "ETag": ['"v1"']}
            ),
            200,
            "ambiguous Content-Length",
        ),
        (
            {"Content-Length": "12", "Content-Range": "bytes 0-11/12", "ETag": '"v1"'},
            200,
            "must not carry Content-Range",
        ),
        (
            {"Content-Length": "12", "Transfer-Encoding": "chunked", "ETag": '"v1"'},
            200,
            "Transfer-Encoding",
        ),
        (
            {"Content-Length": "12", "Content-Encoding": "gzip", "ETag": '"v1"'},
            200,
            "Content-Encoding",
        ),
        ({"Content-Length": "12", "ETag": 'W/"v1"'}, 200, "strong ETag"),
        ({"Content-Length": "12", "ETag": '"v1"'}, 206, "requires HTTP 200"),
        ({"Content-Length": "12", "ETag": '"v1"'}, 302, "redirects are forbidden"),
    ],
)
def test_http_framing_and_validator_are_fail_closed(
    tmp_path: Path, headers, status: int, error: str
) -> None:
    spec = _spec()
    with pytest.raises(ValueError, match=error):
        ACQUIRE.download_one(
            spec,
            destination_root=tmp_path / str(status) / hashlib.sha256(
                repr(headers).encode()
            ).hexdigest(),
            selection_sha256="1" * 64,
            execution_plan_sha256="2" * 64,
            execution_plan_timestamp_response_sha256=TIMESTAMP_SHA256,
            opener=FakeOpener(
                [
                    FakeResponse(
                        url=spec.source_url,
                        status=status,
                        headers=headers,
                        payload=b"abcdefghijkl",
                    )
                ]
            ),
        )


def test_range_response_must_exactly_match_requested_offset() -> None:
    spec = _spec()
    response = FakeResponse(
        url=spec.source_url,
        status=206,
        headers={
            "Content-Length": "8",
            "Content-Range": "bytes 4-11/12",
            "ETag": '"v1"',
        },
        payload=b"efghijkl",
    )
    assert ACQUIRE._validate_response(
        response, spec, offset=4, expected_validator=("strong_etag", '"v1"')
    ) == ("strong_etag", '"v1"')
    response.headers["Content-Range"] = "bytes 5-11/12"
    with pytest.raises(ValueError, match="Content-Range"):
        ACQUIRE._validate_response(
            response, spec, offset=4, expected_validator=("strong_etag", '"v1"')
        )


@pytest.mark.parametrize(
    ("times", "maximum_wall", "minimum_rate", "grace", "error"),
    [
        ([0.0, 0.0, 0.0, 11.0], 10.0, 1.0, 100.0, "wall-clock"),
        ([0.0, 0.0, 0.0, 2.0], 10.0, 100.0, 1.0, "throughput"),
    ],
)
def test_wall_clock_and_minimum_throughput_bounds_are_independent_of_socket_timeout(
    tmp_path: Path,
    times: list[float],
    maximum_wall: float,
    minimum_rate: float,
    grace: float,
    error: str,
) -> None:
    iterator = iter(times)
    spec = _spec()
    with pytest.raises(TimeoutError, match=error):
        ACQUIRE.download_one(
            spec,
            destination_root=tmp_path / error,
            selection_sha256="1" * 64,
            execution_plan_sha256="2" * 64,
            execution_plan_timestamp_response_sha256=TIMESTAMP_SHA256,
            opener=FakeOpener(
                [
                    FakeResponse(
                        url=spec.source_url,
                        status=200,
                        headers={"Content-Length": "12", "ETag": '"v1"'},
                        payload=b"abcdefghijkl",
                    )
                ]
            ),
            timeout_seconds=60.0,
            maximum_wall_seconds=maximum_wall,
            minimum_throughput_bytes_per_second=minimum_rate,
            throughput_grace_seconds=grace,
            clock=lambda: next(iterator),
        )


def test_cohort_deadline_expires_during_object_and_caps_open_and_read(
    tmp_path: Path,
) -> None:
    times = iter([0.0, 0.0, 0.0, 0.0, 6.0])
    spec = _spec()
    response = FakeResponse(
        url=spec.source_url,
        status=200,
        headers={"Content-Length": "12", "ETag": '"v1"'},
        payload=b"abcdefghijkl",
    )
    opener = FakeOpener([response])
    with pytest.raises(TimeoutError, match="cohort wall-clock"):
        ACQUIRE.download_one(
            spec,
            destination_root=tmp_path,
            selection_sha256="1" * 64,
            execution_plan_sha256="2" * 64,
            execution_plan_timestamp_response_sha256=TIMESTAMP_SHA256,
            opener=opener,
            timeout_seconds=60.0,
            chunk_bytes=4,
            maximum_wall_seconds=10.0,
            minimum_throughput_bytes_per_second=1.0,
            throughput_grace_seconds=100.0,
            absolute_deadline=5.0,
            clock=lambda: next(times),
        )
    assert opener.requests[0][1] == 5.0
    assert response.timeouts == [5.0]
    directory = tmp_path / f"observation-{spec.observation_id}"
    assert (directory / f".{spec.object_key}.state.json").exists()
    assert not (directory / spec.object_key).exists()


def test_deadline_expires_during_existing_final_rehash(tmp_path: Path) -> None:
    payload = b"abcdefghijkl"
    spec = _spec()
    ACQUIRE.download_one(
        spec,
        destination_root=tmp_path,
        selection_sha256="1" * 64,
        execution_plan_sha256="2" * 64,
        execution_plan_timestamp_response_sha256=TIMESTAMP_SHA256,
        opener=FakeOpener(
            [
                FakeResponse(
                    url=spec.source_url,
                    status=200,
                    headers={"Content-Length": "12", "ETag": '"v1"'},
                    payload=payload,
                )
            ]
        ),
    )
    times = iter([0.0, 0.0, 10.0])
    offline = FakeOpener([])
    with pytest.raises(TimeoutError, match="hashing exceeded"):
        ACQUIRE.download_one(
            spec,
            destination_root=tmp_path,
            selection_sha256="1" * 64,
            execution_plan_sha256="2" * 64,
            execution_plan_timestamp_response_sha256=TIMESTAMP_SHA256,
            opener=offline,
            maximum_wall_seconds=100.0,
            absolute_deadline=5.0,
            clock=lambda: next(times),
        )
    assert offline.requests == []


def test_manifest_validation_rehash_honors_cohort_deadline_and_exact_mode(
    tmp_path: Path,
) -> None:
    payload = b"abcdefghijkl"
    spec = _spec()
    destination = tmp_path / "objects"
    observation = destination / f"observation-{spec.observation_id}"
    observation.mkdir(parents=True, mode=0o700)
    destination.chmod(0o700)
    final_path = observation / spec.object_key
    final_path.write_bytes(payload)
    final_path.chmod(0o444)
    record = _validation_record(spec, final_path, payload)
    times = iter([0.0, 10.0])
    with pytest.raises(TimeoutError, match="hashing exceeded"):
        ACQUIRE._validate_manifest_records(
            [record],
            specs=[spec],
            destination_root=destination,
            selection_sha256="1" * 64,
            execution_plan_sha256="2" * 64,
            execution_plan_timestamp_response_sha256=TIMESTAMP_SHA256,
            absolute_deadline=5.0,
            clock=lambda: next(times),
        )

    final_path.chmod(0o400)
    with pytest.raises(ValueError, match="identity or read-only mode drift"):
        ACQUIRE._validate_manifest_records(
            [record],
            specs=[spec],
            destination_root=destination,
            selection_sha256="1" * 64,
            execution_plan_sha256="2" * 64,
            execution_plan_timestamp_response_sha256=TIMESTAMP_SHA256,
            absolute_deadline=5.0,
            clock=lambda: 0.0,
        )


def test_symlink_in_destination_path_is_rejected_before_network(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    linked = tmp_path / "linked"
    linked.symlink_to(real, target_is_directory=True)
    opener = FakeOpener([])
    with pytest.raises(ValueError, match="symlink or non-directory"):
        ACQUIRE.download_one(
            _spec(),
            destination_root=linked / "objects",
            selection_sha256="1" * 64,
            execution_plan_sha256="2" * 64,
            execution_plan_timestamp_response_sha256=TIMESTAMP_SHA256,
            opener=opener,
        )
    assert opener.requests == []


def test_symlink_state_and_existing_final_are_never_followed(tmp_path: Path) -> None:
    spec = _spec()
    directory = tmp_path / f"observation-{spec.observation_id}"
    directory.mkdir(mode=0o700)
    outside = tmp_path / "outside"
    outside.write_bytes(b"do-not-touch")
    state = directory / f".{spec.object_key}.state.json"
    state.symlink_to(outside)
    with pytest.raises(ValueError, match="symlink"):
        ACQUIRE.download_one(
            spec,
            destination_root=tmp_path,
            selection_sha256="1" * 64,
            execution_plan_sha256="2" * 64,
            execution_plan_timestamp_response_sha256=TIMESTAMP_SHA256,
            opener=FakeOpener([]),
        )
    assert outside.read_bytes() == b"do-not-touch"


def test_existing_final_is_no_clobber_and_final_name_is_rehashed(tmp_path: Path) -> None:
    spec = _spec()
    directory = tmp_path / f"observation-{spec.observation_id}"
    directory.mkdir(mode=0o700)
    final = directory / spec.object_key
    final.write_bytes(b"XXXXXXXXXXXX")
    final.chmod(0o444)
    opener = FakeOpener(
        [
            FakeResponse(
                url=spec.source_url,
                status=200,
                headers={"Content-Length": "12", "ETag": '"v1"'},
                payload=b"abcdefghijkl",
            )
        ]
    )
    with pytest.raises(ValueError, match="without a receipt and content-bound state"):
        ACQUIRE.download_one(
            spec,
            destination_root=tmp_path,
            selection_sha256="1" * 64,
            execution_plan_sha256="2" * 64,
            execution_plan_timestamp_response_sha256=TIMESTAMP_SHA256,
            opener=opener,
        )
    assert final.read_bytes() == b"XXXXXXXXXXXX"
    assert opener.requests == []


def test_default_transport_requires_verified_tls_and_disables_environment_proxy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        ACQUIRE.urllib.request,
        "getproxies",
        lambda: {"https": "http://attacker.invalid:8080"},
    )
    opener = ACQUIRE._default_opener()
    https = next(
        handler
        for handler in opener.handlers
        if isinstance(handler, ACQUIRE.urllib.request.HTTPSHandler)
    )
    assert https._context.check_hostname is True
    assert https._context.verify_mode == ssl.CERT_REQUIRED
    assert https._context.minimum_version >= ssl.TLSVersion.TLSv1_2
    assert all(
        not isinstance(handler, ACQUIRE.urllib.request.ProxyHandler)
        or handler.proxies == {}
        for handler in opener.handlers
    )
    assert any(isinstance(handler, ACQUIRE._NoRedirect) for handler in opener.handlers)


def test_rfc3161_verifier_binds_query_response_plan_and_pre_acquisition_time(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan_sha = "6575161822d8a8fb65a82d3c1b9967d36532b9ff32c889bba9c9e5503ae9bc52"
    request = tmp_path / "plan.tsq"
    response = tmp_path / "plan.tsr"
    request.write_bytes(b"query")
    response.write_bytes(b"response")
    calls: list[tuple[str, ...]] = []

    def openssl(arguments, *, pass_fds=()):
        calls.append(tuple(arguments))
        if arguments == ("version",):
            return "OpenSSL test\n"
        if "-reply" in arguments:
            return (
                "Status: Granted.\nHash Algorithm: sha256\nMessage data:\n"
                "    0000 - 65 75 16 18 22 d8 a8 fb-65 a8 2d 3c 1b 99 67 d3   eu..\"...e.-<..g.\n"
                "    0010 - 65 32 b9 ff 32 c8 89 bb-a9 c9 e5 50 3a e9 bc 52   e2..2......P:..R\n"
                "Serial number: 0x01\n"
                "Time stamp: Sep  3 12:00:00 2026 GMT\n"
            )
        return "Verification: OK\n"

    monkeypatch.setattr(ACQUIRE, "_run_openssl", openssl)
    result = ACQUIRE._verify_execution_plan_timestamp(
        execution_plan_sha256=plan_sha,
        request_path=request,
        response_path=response,
        acquisition_started_at=datetime(2026, 9, 4, tzinfo=timezone.utc),
    )
    assert result["status"] == "PASS"
    assert result["message_imprint_sha256"] == plan_sha
    assert any("-queryfile" in call for call in calls)
    assert any("-digest" in call and plan_sha in call for call in calls)


@pytest.mark.parametrize(
    "message_rows",
    [
        # One non-OpenSSL row containing all 32 bytes.
        "    0000 - " + " ".join(["00"] * 32),
        # A short first row.
        "    0000 - 00 00 00 00 00 00 00 00-00 00 00 00 00 00 00\n"
        "    0010 - 00 00 00 00 00 00 00 00-00 00 00 00 00 00 00 00",
        # Two full rows with a non-contiguous second offset.
        "    0000 - 00 00 00 00 00 00 00 00-00 00 00 00 00 00 00 00\n"
        "    0020 - 00 00 00 00 00 00 00 00-00 00 00 00 00 00 00 00",
        # An additional hexdump row must not be silently ignored.
        "    0000 - 00 00 00 00 00 00 00 00-00 00 00 00 00 00 00 00\n"
        "    0010 - 00 00 00 00 00 00 00 00-00 00 00 00 00 00 00 00\n"
        "    0020 - 00 00 00 00 00 00 00 00-00 00 00 00 00 00 00 00",
    ],
)
def test_rfc3161_message_data_parser_rejects_malformed_rows(
    message_rows: str,
) -> None:
    details = f"Message data:\n{message_rows}\nSerial number: 0x01\n"
    with pytest.raises(ValueError, match="message imprint"):
        ACQUIRE._parse_openssl_sha256_message_data(details)


def test_rfc3161_gate_fails_before_destination_or_download(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rows = [
        {
            "observation_id": 30_000 + index,
            "satellite_id": "SAT",
            "object_key": f"timestamp-{index}.iq",
            "url": f"https://example.invalid/iq-data/timestamp-{index}.iq",
            "size_bytes": 4,
        }
        for index in range(30)
    ]
    selection = tmp_path / "selection.json"
    selection.write_text(
        json.dumps(
            {"schema_version": "blind-phase-prospective-holdout-selection-v2", "selected": rows}
        ),
        encoding="utf-8",
    )
    selection_identity = ACQUIRE._identity(selection)
    plan = tmp_path / "plan.json"
    plan.write_text(
        json.dumps(
            {
                "schema_version": "blind-phase-confirmatory-execution-plan-v2",
                "status": "frozen_before_selected_iq_download_and_decoder_execution",
                "provenance": {
                    "fixed_metadata_inputs": [selection_identity],
                    "campaign_execution_tools": {
                        "acquisition": ACQUIRE._identity(SCRIPT)
                    },
                },
                "release_gates": {
                    "selected_iq_absent_during_plan_freeze": True,
                    "candidate_decoder_config_and_runtime_locked_before_download": True,
                    "component_baseline_a_runtime_locked_before_download": True,
                    "bwrap_no_network_read_only_canary_passed_before_download": True,
                },
            }
        ),
        encoding="utf-8",
    )
    request = tmp_path / "plan.tsq"
    response = tmp_path / "plan.tsr"
    request.write_bytes(b"query")
    response.write_bytes(b"response")
    monkeypatch.setattr(
        ACQUIRE,
        "_verify_execution_plan_timestamp",
        lambda **kwargs: (_ for _ in ()).throw(ValueError("timestamp rejected")),
    )
    monkeypatch.setattr(
        ACQUIRE,
        "download_one",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("download must not start")
        ),
    )
    destination = tmp_path / "objects"
    with pytest.raises(ValueError, match="timestamp rejected"):
        ACQUIRE.acquire_all(
            selection_path=selection,
            expected_selection_sha256=hashlib.sha256(selection.read_bytes()).hexdigest(),
            execution_plan_path=plan,
            expected_execution_plan_sha256=hashlib.sha256(plan.read_bytes()).hexdigest(),
            execution_plan_timestamp_request_path=request,
            execution_plan_timestamp_response_path=response,
            destination_root=destination,
            manifest_path=tmp_path / "manifest.json",
        )
    assert not destination.exists()
