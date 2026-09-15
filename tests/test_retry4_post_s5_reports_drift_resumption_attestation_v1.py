from __future__ import annotations

import ast
import base64
import errno
import fcntl
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import types

import pytest


PROJECT = Path("/home/ubuntu/telemetry-yield")
SOURCE = PROJECT / "work/blind-phase-confirmatory-v2/build_retry4_post_s5_reports_drift_resumption_attestation_v1.py"
TEST = PROJECT / "tests/test_retry4_post_s5_reports_drift_resumption_attestation_v1.py"
OUTPUT = PROJECT / "reports/blind-phase-confirmatory-retry4-post-s5-reports-drift-resumption-attestation-v1.json"
EXPECTED_SOURCE_SHA256 = "b1f6b2486b2319e7234ecdfce686bdff7edec391dd988329de767a64520f3bea"
EXPECTED_SOURCE_SIZE = 60972
EXPECTED_GENERATOR_BLOCKED = False
BLOCKED_SOURCE_SHA256 = "135dde14955b9c1540eea3cd952d0027671a7621a44952c9eb42c38e7192acca"
BLOCKED_SOURCE_SIZE = 60908
PREDICTED_EXECUTABLE_SOURCE_SHA256 = "b1f6b2486b2319e7234ecdfce686bdff7edec391dd988329de767a64520f3bea"
PREDICTED_EXECUTABLE_SOURCE_SIZE = 60972
EXPECTED_BLOCKED_TEST_SHA256: str | None = "08bba1b4aba90946d7856f7f620a8a24963999204b3902e45532dcdc7073ed17"
EXPECTED_BLOCKED_TEST_SIZE: int | None = 41758

# No generator byte is compiled or executed before this exact held-byte gate.
SOURCE_BYTES = SOURCE.read_bytes()
assert len(SOURCE_BYTES) == EXPECTED_SOURCE_SIZE
assert hashlib.sha256(SOURCE_BYTES).hexdigest() == EXPECTED_SOURCE_SHA256
SOURCE_TREE = ast.parse(SOURCE_BYTES, filename=str(SOURCE))
MODULE = types.ModuleType("tested_post_s5_reports_drift_attestation")
MODULE.__file__ = str(SOURCE)
MODULE.__package__ = None
exec(compile(SOURCE_TREE, str(SOURCE), "exec"), MODULE.__dict__)
TEST_BYTES = TEST.read_bytes()


def _sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _executable_source() -> bytes:
    payload = SOURCE_BYTES
    replacements = (
        (
            b"RESUMPTION_ATTESTATION_BLOCKED_PENDING_INDEPENDENT_AUDIT = True\n",
            b"RESUMPTION_ATTESTATION_BLOCKED_PENDING_INDEPENDENT_AUDIT = False\n",
        ),
        (
            b"EXPECTED_BLOCKED_GENERATOR_SHA256: str | None = None\n",
            f'EXPECTED_BLOCKED_GENERATOR_SHA256: str | None = "{BLOCKED_SOURCE_SHA256}"\n'.encode(),
        ),
        (
            b"EXPECTED_BLOCKED_GENERATOR_SIZE: int | None = None\n",
            f"EXPECTED_BLOCKED_GENERATOR_SIZE: int | None = {BLOCKED_SOURCE_SIZE}\n".encode(),
        ),
    )
    for before, after in replacements:
        assert payload.count(before) == 1 and payload.count(after) == 0
        payload = payload.replace(before, after, 1)
    return payload


def _predicted_executable_test() -> bytes:
    assert EXPECTED_GENERATOR_BLOCKED
    payload = TEST_BYTES
    replacements = (
        (
            f'EXPECTED_SOURCE_SHA256 = "{BLOCKED_SOURCE_SHA256}"\n'.encode(),
            f'EXPECTED_SOURCE_SHA256 = "{PREDICTED_EXECUTABLE_SOURCE_SHA256}"\n'.encode(),
        ),
        (
            f"EXPECTED_SOURCE_SIZE = {BLOCKED_SOURCE_SIZE}\n".encode(),
            f"EXPECTED_SOURCE_SIZE = {PREDICTED_EXECUTABLE_SOURCE_SIZE}\n".encode(),
        ),
        (b"EXPECTED_GENERATOR_BLOCKED = True\n", b"EXPECTED_GENERATOR_BLOCKED = False\n"),
        (
            b"EXPECTED_BLOCKED_TEST_SHA256: str | None = None\n",
            f'EXPECTED_BLOCKED_TEST_SHA256: str | None = "{_sha(TEST_BYTES)}"\n'.encode(),
        ),
        (
            b"EXPECTED_BLOCKED_TEST_SIZE: int | None = None\n",
            f"EXPECTED_BLOCKED_TEST_SIZE: int | None = {len(TEST_BYTES)}\n".encode(),
        ),
    )
    for before, after in replacements:
        assert payload.count(before) == 1 and payload.count(after) == 0
        payload = payload.replace(before, after, 1)
    return payload


def _reconstruct_blocked_test(payload: bytes) -> bytes:
    if EXPECTED_GENERATOR_BLOCKED:
        blocked_hash = _sha(TEST_BYTES)
        blocked_size = len(TEST_BYTES)
    else:
        assert EXPECTED_BLOCKED_TEST_SHA256 is not None and EXPECTED_BLOCKED_TEST_SIZE is not None
        blocked_hash = EXPECTED_BLOCKED_TEST_SHA256
        blocked_size = EXPECTED_BLOCKED_TEST_SIZE
    replacements = (
        (
            f'EXPECTED_SOURCE_SHA256 = "{PREDICTED_EXECUTABLE_SOURCE_SHA256}"\n'.encode(),
            f'EXPECTED_SOURCE_SHA256 = "{BLOCKED_SOURCE_SHA256}"\n'.encode(),
        ),
        (
            f"EXPECTED_SOURCE_SIZE = {PREDICTED_EXECUTABLE_SOURCE_SIZE}\n".encode(),
            f"EXPECTED_SOURCE_SIZE = {BLOCKED_SOURCE_SIZE}\n".encode(),
        ),
        (b"EXPECTED_GENERATOR_BLOCKED = False\n", b"EXPECTED_GENERATOR_BLOCKED = True\n"),
        (
            f'EXPECTED_BLOCKED_TEST_SHA256: str | None = "{blocked_hash}"\n'.encode(),
            b"EXPECTED_BLOCKED_TEST_SHA256: str | None = None\n",
        ),
        (
            f"EXPECTED_BLOCKED_TEST_SIZE: int | None = {blocked_size}\n".encode(),
            b"EXPECTED_BLOCKED_TEST_SIZE: int | None = None\n",
        ),
    )
    for before, after in replacements:
        assert payload.count(before) == 1 and payload.count(after) == 0
        payload = payload.replace(before, after, 1)
    assert len(payload) == blocked_size and _sha(payload) == blocked_hash
    return payload


def _enable_design(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(MODULE, "RESUMPTION_ATTESTATION_BLOCKED_PENDING_INDEPENDENT_AUDIT", False)
    monkeypatch.setattr(MODULE, "_validate_invocation", lambda: None)
    if EXPECTED_GENERATOR_BLOCKED:
        monkeypatch.setattr(MODULE, "_transition_contract", lambda _payload: {
            "operation": "virtual blocked-design test transition",
            "replacement_count": 3,
            "blocked_sha256": BLOCKED_SOURCE_SHA256,
            "blocked_size_bytes": BLOCKED_SOURCE_SIZE,
            "all_other_source_bytes_unchanged": True,
        })


def _patch_publication_authority(monkeypatch: pytest.MonkeyPatch) -> None:
    _enable_design(monkeypatch)
    monkeypatch.setattr(MODULE.os, "geteuid", lambda: 0)
    monkeypatch.setattr(MODULE.os, "getegid", lambda: 0)


def _candidate() -> tuple[dict[str, object], bytes, bytes]:
    unhashed = {
        "schema_version": MODULE.SCHEMA,
        "status": "GO",
        "reviewed_bindings": {"point_in_time": True},
        "checks": MODULE.expected_checks(),
        "severity_counts": {"P0": 0, "P1": 0, "P2": 0},
    }
    document = {**unhashed, MODULE.SELF_HASH_FIELD: MODULE._document_hash(unhashed)}
    payload = MODULE._canonical(document)
    sidecar = f"{_sha(payload)}  {MODULE.OUTPUT.name}\n".encode("ascii")
    return document, payload, sidecar


def test_exact_blocked_source_and_controlled_transition() -> None:
    assert MODULE.RESUMPTION_ATTESTATION_BLOCKED_PENDING_INDEPENDENT_AUDIT is EXPECTED_GENERATOR_BLOCKED
    if EXPECTED_GENERATOR_BLOCKED:
        assert MODULE.EXPECTED_BLOCKED_GENERATOR_SHA256 is None
        assert MODULE.EXPECTED_BLOCKED_GENERATOR_SIZE is None
        executable = _executable_source()
        assert len(executable) == PREDICTED_EXECUTABLE_SOURCE_SIZE
        assert _sha(executable) == PREDICTED_EXECUTABLE_SOURCE_SHA256
    else:
        assert MODULE.EXPECTED_BLOCKED_GENERATOR_SHA256 == BLOCKED_SOURCE_SHA256
        assert MODULE.EXPECTED_BLOCKED_GENERATOR_SIZE == BLOCKED_SOURCE_SIZE
        executable = SOURCE_BYTES
        assert len(executable) == PREDICTED_EXECUTABLE_SOURCE_SIZE
        assert _sha(executable) == PREDICTED_EXECUTABLE_SOURCE_SHA256
        assert MODULE._transition_contract(executable)["replacement_count"] == 3
    reconstructed = executable.replace(
        b"RESUMPTION_ATTESTATION_BLOCKED_PENDING_INDEPENDENT_AUDIT = False\n",
        b"RESUMPTION_ATTESTATION_BLOCKED_PENDING_INDEPENDENT_AUDIT = True\n", 1,
    ).replace(
        f'EXPECTED_BLOCKED_GENERATOR_SHA256: str | None = "{BLOCKED_SOURCE_SHA256}"\n'.encode(),
        b"EXPECTED_BLOCKED_GENERATOR_SHA256: str | None = None\n", 1,
    ).replace(
        f"EXPECTED_BLOCKED_GENERATOR_SIZE: int | None = {BLOCKED_SOURCE_SIZE}\n".encode(),
        b"EXPECTED_BLOCKED_GENERATOR_SIZE: int | None = None\n", 1,
    )
    assert len(reconstructed) == BLOCKED_SOURCE_SIZE
    assert _sha(reconstructed) == BLOCKED_SOURCE_SHA256
    if EXPECTED_GENERATOR_BLOCKED:
        assert reconstructed == SOURCE_BYTES


def test_test_harness_controlled_transition_is_exactly_five_fields_and_reversible() -> None:
    if EXPECTED_GENERATOR_BLOCKED:
        assert EXPECTED_BLOCKED_TEST_SHA256 is None and EXPECTED_BLOCKED_TEST_SIZE is None
        executable_test = _predicted_executable_test()
        assert _reconstruct_blocked_test(executable_test) == TEST_BYTES
    else:
        assert EXPECTED_BLOCKED_TEST_SHA256 is not None and EXPECTED_BLOCKED_TEST_SIZE is not None
        assert _reconstruct_blocked_test(TEST_BYTES) != TEST_BYTES


def test_exact_contract_constants_and_corrected_two_directory_delta() -> None:
    assert MODULE.OUTPUT == OUTPUT
    assert MODULE.EXPECTED_REPORTS_CORE == {
        "path": str(PROJECT / "reports"), "st_dev": 64512, "st_ino": 1060183,
        "uid": 1000, "gid": 1000, "mode": 0o775,
    }
    assert len(MODULE.EXPECTED_BASELINE_DIRECTORIES) == 16
    assert MODULE.EXPECTED_DELTA_DIRECTORIES == {
        "generalization-v3-20260907": (64512, 1594318, 1000, 1000, 0o775, 3),
        "network-scale-20260907": (64512, 1594315, 1000, 1000, 0o775, 2),
    }
    assert not hasattr(MODULE, "ACTIVE_JOB_PROTOCOL_AUTHORITIES")
    assert "/protocol.json" not in SOURCE_BYTES.decode("utf-8")
    assert MODULE.EXPECTED_PRIOR_REVIEW["sha256"] == "5d1d7625d2b5bff16e89dc93a679b1398e91ee5c4391bae5f180b40c2799a049"
    assert MODULE.EXPECTED_PRIOR_REVIEW_SIDECAR["sha256"] == "c4e71d6e07e5539fc18eaa0640f32ef4a5df9acc39794c11d372da6fd994d000"


@pytest.mark.skipif(not EXPECTED_GENERATOR_BLOCKED, reason="blocked-design boundary assertion")
@pytest.mark.parametrize("name", ["_build_context", "build_attestation", "publish_attestation"])
def test_direct_build_and_publish_boundaries_block_before_reads(
    monkeypatch: pytest.MonkeyPatch, name: str,
) -> None:
    monkeypatch.setattr(MODULE, "_load_prior_review", lambda: pytest.fail("read before block"))
    with pytest.raises(MODULE.AttestationError, match="blocked pending independent audit"):
        getattr(MODULE, name)(
            expected_generator_sha256="0" * 64,
            expected_generator_test_sha256="0" * 64,
        )


@pytest.mark.skipif(not EXPECTED_GENERATOR_BLOCKED, reason="blocked-design boundary assertion")
def test_main_blocks_before_parser_or_files(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(MODULE, "_validate_invocation", lambda: None)
    monkeypatch.setattr(MODULE, "parser", lambda: pytest.fail("parser reached"))
    with pytest.raises(MODULE.AttestationError, match="blocked pending independent audit"):
        MODULE.main([])


def test_default_exact_cli_is_blocked_and_creates_no_output() -> None:
    before = (OUTPUT.exists(), OUTPUT.with_name(OUTPUT.name + ".sha256").exists())
    result = subprocess.run(
        ["/usr/bin/python3.12", "-I", "-S", "-B", str(SOURCE),
         "--expected-generator-sha256", EXPECTED_SOURCE_SHA256,
         "--expected-generator-test-sha256", _sha(TEST_BYTES)],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        check=False, timeout=10,
    )
    if EXPECTED_GENERATOR_BLOCKED:
        assert result.returncode != 0 and result.stdout == b""
        assert b"blocked pending independent audit" in result.stderr
    elif os.geteuid() != 0:
        assert result.returncode != 0 and result.stdout == b""
        assert b"permission denied during complete namespace scan" in result.stderr
    else:
        assert result.returncode == 0 and result.stderr == b""
        receipt = json.loads(result.stdout)
        assert receipt["published"] is None
        assert receipt["document"]["status"] == "GO"
    assert (OUTPUT.exists(), OUTPUT.with_name(OUTPUT.name + ".sha256").exists()) == before == (False, False)


def test_held_bytes_are_checked_before_compile_and_exec() -> None:
    encoded = base64.b64encode(SOURCE_BYTES).decode("ascii")
    script = f"""
import ast,base64,hashlib,types
b=base64.b64decode({encoded!r})
assert len(b)=={EXPECTED_SOURCE_SIZE}
assert hashlib.sha256(b).hexdigest()=={EXPECTED_SOURCE_SHA256!r}
t=ast.parse(b,filename={str(SOURCE)!r})
m=types.ModuleType('held');m.__file__={str(SOURCE)!r};m.__package__=None
exec(compile(t,{str(SOURCE)!r},'exec'),m.__dict__)
assert m.RESUMPTION_ATTESTATION_BLOCKED_PENDING_INDEPENDENT_AUDIT is {EXPECTED_GENERATOR_BLOCKED!r}
print(m._static_surface(b)['call_inventory_sha256'])
"""
    result = subprocess.run(
        ["/usr/bin/python3.12", "-I", "-S", "-B", "-c", script],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        check=False, timeout=15,
    )
    assert result.returncode == 0, result.stderr.decode()
    assert result.stdout.strip().decode() == MODULE.EXPECTED_CALL_INVENTORY_SHA256


def test_static_surface_exact_and_rejects_effect_mutants() -> None:
    assert MODULE._static_surface(SOURCE_BYTES) == {
        "bounded_defense_in_depth": True,
        "top_level_function_count": 46,
        "call_count": 646,
        "call_inventory_sha256": "04866237c65d7c0e43a80216e3f6f227d6f851575d5a54f9ece7dbbea118a29e",
        "mutation_call_count": 18,
        "mutation_inventory_sha256": "ba4a2c0d8514013a9f341f6599f6e401cda665893e198a5b2443ef7440657572",
    }
    mutants = [
        b"\nf = os.system\nf('id')\n",
        b"\nfrom os import unlink as danger\n",
        b"\nfrom os import unlink\n",
        b"\nos = Path\n",
        b"\ngetattr(os, 'unlink')('/tmp/x')\n",
        b"\nexec('pass')\n",
        b"\nPath('/tmp/x').write_bytes(b'x')\n",
        b"\nbuild_attestation(expected_generator_sha256='0'*64, expected_generator_test_sha256='0'*64)\n",
        b"\ndef rogue(value=os.system('id')):\n    return value\n",
        b"\ndef _sha256(payload):\n    return '0'*64\n",
    ]
    for tail in mutants:
        with pytest.raises((MODULE.AttestationError, SyntaxError)):
            MODULE._static_surface(SOURCE_BYTES + tail)


def test_prior_terminal_review_and_all_recursive_file_bindings_are_exact() -> None:
    records, document = MODULE._load_prior_review()
    try:
        assert set(document) == {
            "schema_version", "status", "reviewed_bindings", "checks",
            "severity_counts", "review_payload_sha256",
        }
        assert document["status"] == "GO"
        assert document["severity_counts"] == {"P0": 0, "P1": 0, "P2": 0}
        assert len(records) >= 20
        paths = {record["identity"]["path"] for record in records.values()}
        assert str(MODULE.CANONICAL / "amendment-v3.tsq") in paths
        assert str(MODULE.CANONICAL / "amendment-v3.tsr") in paths
    finally:
        MODULE._close_records(records)


def test_live_reports_projection_is_complete_sorted_and_exact() -> None:
    records, prior = MODULE._load_prior_review()
    descriptor = -1
    try:
        descriptor, opened = MODULE._open_expected_directory(MODULE.REPORTS, MODULE.EXPECTED_REPORTS_CORE)
        scan = MODULE._reports_scan(descriptor, opened, prior)
        assert scan["frozen_baseline_nlink"] == 18
        assert scan["current_nlink"] == 20
        assert scan["complete_direct_directory_count"] == 18
        assert scan["modeled_delta_names"] == ["generalization-v3-20260907", "network-scale-20260907"]
        assert scan["descendants_remain_mutable_and_untrusted"] is True
        assert scan["descendant_names_bytes_progress_and_results_attested"] is False
        assert scan["unrelated_direct_regular_file_names_count_metadata_and_bytes_attested"] is False
        assert scan["regular_creation_rewrite_and_deletion_permitted_without_candidate_drift"] is True
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        MODULE._close_records(records)


def test_projection_rejects_expected_directory_inode_swap_during_open(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    expected = tmp_path / "expected"
    replacement = tmp_path / "replacement"
    displaced = tmp_path / "displaced"
    expected.mkdir()
    replacement.mkdir()
    monkeypatch.setattr(MODULE, "EXPECTED_BASELINE_DIRECTORIES", {"expected": (0, 0, 0, 0, 0, 0)})
    monkeypatch.setattr(MODULE, "EXPECTED_DELTA_DIRECTORIES", {})
    descriptor = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    real_open = MODULE.os.open
    swapped = False
    def changed(path: object, flags: int, *args: object, **kwargs: object) -> int:
        nonlocal swapped
        if path == "expected" and not swapped:
            swapped = True
            expected.rename(displaced)
            replacement.rename(expected)
        return real_open(path, flags, *args, **kwargs)
    monkeypatch.setattr(MODULE.os, "open", changed)
    try:
        with pytest.raises(MODULE.AttestationError, match="directory changed"):
            MODULE._directory_projection(descriptor, tmp_path)
    finally:
        os.close(descriptor)


def test_modeled_output_links_change_only_normalized_parent_fields_and_are_excluded(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    output = tmp_path / MODULE.OUTPUT.name
    sidecar = output.with_name(output.name + ".sha256")
    stable = tmp_path / "stable.json"
    stable.write_bytes(b"stable\n")
    monkeypatch.setattr(MODULE, "OUTPUT", output)
    monkeypatch.setattr(MODULE, "SIDECAR", sidecar)
    monkeypatch.setattr(MODULE, "EXPECTED_BASELINE_DIRECTORIES", {})
    monkeypatch.setattr(MODULE, "EXPECTED_DELTA_DIRECTORIES", {})
    directory = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        before = os.fstat(directory)
        baseline = MODULE._directory_projection(directory, tmp_path)
        sidecar.write_bytes(b"modeled\n")
        after_sidecar = os.fstat(directory)
        output.write_bytes(b"modeled main\n")
        after_pair = os.fstat(directory)
        assert MODULE._directory_security_tuple(before) == MODULE._directory_security_tuple(after_sidecar)
        assert MODULE._directory_security_tuple(before) == MODULE._directory_security_tuple(after_pair)
        assert MODULE._directory_projection(directory, tmp_path) == baseline
    finally:
        os.close(directory)


def test_unrelated_regular_create_rewrite_inode_swap_and_delete_do_not_change_directory_projection(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setattr(MODULE, "EXPECTED_BASELINE_DIRECTORIES", {})
    monkeypatch.setattr(MODULE, "EXPECTED_DELTA_DIRECTORIES", {})
    target = tmp_path / "stable.json"
    replacement = tmp_path / "replacement.json"
    directory = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        baseline = MODULE._directory_projection(directory, tmp_path)
        target.write_bytes(b"first\n")
        assert MODULE._directory_projection(directory, tmp_path) == baseline
        target.write_bytes(b"rewritten\n")
        assert MODULE._directory_projection(directory, tmp_path) == baseline
        replacement.write_bytes(target.read_bytes())
        target.unlink()
        replacement.rename(target)
        assert MODULE._directory_projection(directory, tmp_path) == baseline
        target.unlink()
        assert MODULE._directory_projection(directory, tmp_path) == baseline
    finally:
        os.close(directory)


def test_delta_descendants_are_never_consulted_and_arbitrary_file_drift_preserves_scan(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    delta = tmp_path / "delta"
    delta.mkdir()
    delta_status = delta.stat()
    root_status = tmp_path.stat()
    monkeypatch.setattr(MODULE, "REPORTS", tmp_path)
    monkeypatch.setattr(MODULE, "OUTPUT", tmp_path / MODULE.OUTPUT.name)
    monkeypatch.setattr(
        MODULE, "SIDECAR",
        (tmp_path / MODULE.OUTPUT.name).with_name(MODULE.OUTPUT.name + ".sha256"),
    )
    monkeypatch.setattr(MODULE, "EXPECTED_BASELINE_DIRECTORIES", {})
    monkeypatch.setattr(MODULE, "EXPECTED_DELTA_DIRECTORIES", {
        "delta": (
            delta_status.st_dev, delta_status.st_ino, delta_status.st_uid,
            delta_status.st_gid, stat.S_IMODE(delta_status.st_mode), delta_status.st_nlink,
        ),
    })
    monkeypatch.setattr(MODULE, "EXPECTED_REPORTS_CORE", {
        "path": str(tmp_path), "st_dev": root_status.st_dev,
        "st_ino": root_status.st_ino, "uid": root_status.st_uid,
        "gid": root_status.st_gid, "mode": stat.S_IMODE(root_status.st_mode),
    })
    directory, opened = MODULE._open_expected_directory(tmp_path, MODULE.EXPECTED_REPORTS_CORE)
    prior = {"reviewed_bindings": {}}
    protocol = delta / "protocol.json"
    replacement = delta / "replacement.json"
    try:
        baseline = MODULE._reports_scan(directory, opened, prior)
        protocol.write_bytes(b"first arbitrary descendant\n")
        assert MODULE._reports_scan(directory, opened, prior) == baseline
        protocol.write_bytes(b"replacement bytes with another length\n")
        assert MODULE._reports_scan(directory, opened, prior) == baseline
        replacement.write_bytes(b"replacement bytes with another length\n")
        old_inode = protocol.stat().st_ino
        protocol.unlink()
        replacement.rename(protocol)
        assert protocol.stat().st_ino != old_inode
        assert MODULE._reports_scan(directory, opened, prior) == baseline
        protocol.unlink()
        assert MODULE._reports_scan(directory, opened, prior) == baseline

        nested = delta / "unmodeled-directory"
        nested.mkdir()
        with pytest.raises(MODULE.AttestationError, match="differs"):
            MODULE._reports_scan(directory, opened, prior)
        nested.rmdir()

        displaced = tmp_path / "displaced-delta"
        delta.rename(displaced)
        delta.mkdir()
        with pytest.raises(MODULE.AttestationError, match="differs"):
            MODULE._reports_scan(directory, opened, prior)
    finally:
        os.close(directory)


def test_unrelated_regular_drift_between_sidecar_and_pair_final_scans_is_allowed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    output = tmp_path / MODULE.OUTPUT.name
    sidecar = output.with_name(output.name + ".sha256")
    monkeypatch.setattr(MODULE, "REPORTS", tmp_path)
    monkeypatch.setattr(MODULE, "OUTPUT", output)
    monkeypatch.setattr(MODULE, "SIDECAR", sidecar)
    monkeypatch.setattr(MODULE, "EXPECTED_BASELINE_DIRECTORIES", {})
    monkeypatch.setattr(MODULE, "EXPECTED_DELTA_DIRECTORIES", {})
    status = tmp_path.stat()
    monkeypatch.setattr(MODULE, "EXPECTED_REPORTS_CORE", {
        "path": str(tmp_path), "st_dev": status.st_dev, "st_ino": status.st_ino,
        "uid": status.st_uid, "gid": status.st_gid,
        "mode": stat.S_IMODE(status.st_mode),
    })
    directory, opened = MODULE._open_expected_directory(tmp_path, MODULE.EXPECTED_REPORTS_CORE)
    prior = {"reviewed_bindings": {}}
    unrelated = tmp_path / "operations-status.json"
    try:
        baseline = MODULE._reports_scan(directory, opened, prior)
        sidecar.write_bytes(b"modeled sidecar\n")
        unrelated.write_bytes(b"phase one\n")
        assert MODULE._reports_scan(directory, opened, prior) == baseline
        unrelated.write_bytes(b"phase two and a different size\n")
        output.write_bytes(b"modeled main\n")
        assert MODULE._reports_scan(directory, opened, prior) == baseline
        unrelated.unlink()
        assert MODULE._reports_scan(directory, opened, prior) == baseline
    finally:
        os.close(directory)


def test_unmodeled_direct_directory_changes_parent_nlink_and_is_rejected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setattr(MODULE, "REPORTS", tmp_path)
    monkeypatch.setattr(MODULE, "EXPECTED_BASELINE_DIRECTORIES", {})
    monkeypatch.setattr(MODULE, "EXPECTED_DELTA_DIRECTORIES", {})
    status = tmp_path.stat()
    monkeypatch.setattr(MODULE, "EXPECTED_REPORTS_CORE", {
        "path": str(tmp_path), "st_dev": status.st_dev, "st_ino": status.st_ino,
        "uid": status.st_uid, "gid": status.st_gid,
        "mode": stat.S_IMODE(status.st_mode),
    })
    directory, opened = MODULE._open_expected_directory(tmp_path, MODULE.EXPECTED_REPORTS_CORE)
    try:
        (tmp_path / "third-directory").mkdir()
        with pytest.raises(MODULE.AttestationError, match="reports parent differs"):
            MODULE._reports_scan(directory, opened, {"reviewed_bindings": {}})
    finally:
        os.close(directory)


def test_frozen_authority_same_bytes_new_inode_is_rejected(tmp_path: Path) -> None:
    target = tmp_path / "authority.json"
    replacement = tmp_path / "replacement.json"
    target.write_bytes(b"exact authority\n")
    status = target.stat()
    expected = MODULE._expected_file_record(
        target, _sha(target.read_bytes()), status.st_size, status.st_dev, status.st_ino,
        status.st_uid, status.st_gid, stat.S_IMODE(status.st_mode), status.st_nlink,
    )
    record = MODULE._open_exact(expected)
    try:
        replacement.write_bytes(target.read_bytes())
        target.unlink()
        replacement.rename(target)
        with pytest.raises(MODULE.AttestationError, match="held authority changed"):
            MODULE._revalidate_records({"authority": record})
    finally:
        os.close(record["fd"])


def test_intent_lock_contention_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    _enable_design(monkeypatch)
    records, prior = MODULE._load_prior_review()
    intent = Path(prior["reviewed_bindings"]["intent"]["main"]["path"])
    blocker = os.open(intent, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    try:
        fcntl.flock(blocker, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(MODULE.AttestationError, match="intent lock is held"):
            MODULE._acquire_intent_lock(records, prior)
    finally:
        fcntl.flock(blocker, fcntl.LOCK_UN)
        os.close(blocker)
        MODULE._close_records(records)


@pytest.mark.skipif(os.geteuid() != 0, reason="root-only complete cross-namespace /proc scan")
def test_virtual_unblocked_live_build_is_two_pass_deterministic_and_read_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable_design(monkeypatch)
    test_sha = _sha(TEST_BYTES)
    first = MODULE._build_context(EXPECTED_SOURCE_SHA256, test_sha)
    try:
        first_document = first["document"]
        first_payload = first["payload"]
        MODULE._validate_candidate(first_document, first_payload, first["sidecar"])
        assert set(first_document) == {
            "schema_version", "status", "reviewed_bindings", "checks",
            "severity_counts", MODULE.SELF_HASH_FIELD,
        }
        assert first_document["status"] == "GO"
        assert first_document["severity_counts"] == {"P0": 0, "P1": 0, "P2": 0}
        assert first["publication_state"][0] == "FRESH"
        fixed = first_document["reviewed_bindings"]["live_post_s5_fixed_point"]
        assert fixed["equal"] is True and fixed["scan_count_before_document"] == 2
        assert fixed["snapshot"]["reports"]["modeled_delta_names"] == [
            "generalization-v3-20260907", "network-scale-20260907",
        ]
        namespaces = fixed["snapshot"]["process_and_cross_namespace_exclusion"]
        assert namespaces["all_visible_process_mount_namespaces_scanned"] is True
        assert namespaces["unrelated_namespace_identities_attested"] is False
        assert namespaces["matching_mount_points_across_namespaces"] == []
        mutable = first_document["reviewed_bindings"]["mutable_namespace_contract"]
        assert mutable["active_job_descendants_attested"] is False
        assert mutable["active_job_descendants_opened_or_enumerated"] is False
        assert mutable["active_job_origin_content_and_descendants_trusted"] is False
        assert mutable["active_job_descendant_data_consumed_by_lifecycle"] is False
    finally:
        MODULE._close_context(first)
    second = MODULE._build_context(EXPECTED_SOURCE_SHA256, test_sha)
    try:
        assert second["payload"] == first_payload
        assert second["publication_state"][0] == "FRESH"
    finally:
        MODULE._close_context(second)
    assert not MODULE.OUTPUT.exists() and not MODULE.SIDECAR.exists()


def test_candidate_exact_six_key_selfhash_and_sidecar_formula() -> None:
    document, payload, sidecar = _candidate()
    MODULE._validate_candidate(document, payload, sidecar)
    assert set(document) == {
        "schema_version", "status", "reviewed_bindings", "checks",
        "severity_counts", MODULE.SELF_HASH_FIELD,
    }
    assert sidecar == f"{_sha(payload)}  {MODULE.OUTPUT.name}\n".encode("ascii")
    for changed in (
        {**document, "status": "NO_GO"},
        {**document, "severity_counts": {"P0": 0, "P1": 1, "P2": 0}},
        {**document, "extra": True},
    ):
        with pytest.raises(MODULE.AttestationError):
            MODULE._validate_candidate(changed, MODULE._canonical(changed), sidecar)


@pytest.mark.skipif(os.geteuid() != 0, reason="root-only root:root fragment state")
def test_output_state_fresh_sidecar_pair_and_rejects_main_only_or_foreign(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    output = tmp_path / MODULE.OUTPUT.name
    side_path = output.with_name(output.name + ".sha256")
    monkeypatch.setattr(MODULE, "OUTPUT", output)
    monkeypatch.setattr(MODULE, "SIDECAR", side_path)
    _, payload, sidecar = _candidate()
    directory = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        assert MODULE._output_state(directory, payload, sidecar)[0] == "FRESH"
        side_path.write_bytes(sidecar)
        os.chown(side_path, 0, 0)
        os.chmod(side_path, 0o444)
        assert MODULE._output_state(directory, payload, sidecar)[0] == "SIDECAR_COMMITTED"
        output.write_bytes(payload)
        os.chown(output, 0, 0)
        os.chmod(output, 0o444)
        assert MODULE._output_state(directory, payload, sidecar)[0] == "PAIR_COMMITTED"
        output.unlink()
        side_path.unlink()
        output.write_bytes(payload)
        os.chown(output, 0, 0)
        os.chmod(output, 0o444)
        with pytest.raises(MODULE.AttestationError, match="main-only"):
            MODULE._output_state(directory, payload, sidecar)
        output.unlink()
        side_path.write_bytes(b"0" * len(sidecar))
        os.chown(side_path, 0, 0)
        os.chmod(side_path, 0o444)
        with pytest.raises(MODULE.AttestationError, match="collision"):
            MODULE._output_state(directory, payload, sidecar)
    finally:
        os.close(directory)


@pytest.mark.parametrize("initial_state", ["FRESH", "SIDECAR_COMMITTED", "PAIR_COMMITTED"])
def test_publisher_orchestration_is_sidecar_first_durable_replay_and_revalidates(
    monkeypatch: pytest.MonkeyPatch, initial_state: str,
) -> None:
    _patch_publication_authority(monkeypatch)
    document, payload, sidecar = _candidate()
    context = {
        "document": document, "payload": payload, "sidecar": sidecar,
        "publication_state": (initial_state, None, None), "reports_fd": 9,
        "prior_records": {}, "dynamic_records": {}, "prior": {},
        "reports_opened": object(), "canonical_fd": 10, "canonical_opened": object(),
        "quarantine_fd": 11, "quarantine_opened": object(), "snapshot": {"fixed": True},
    }
    events: list[str] = []
    monkeypatch.setattr(MODULE, "_build_context", lambda *_args, **_kwargs: context)
    monkeypatch.setattr(MODULE, "_close_context", lambda _context: None)
    monkeypatch.setattr(MODULE, "_ensure_fragment", lambda _fd, path, _data, _max: events.append(path.name) or {"path": str(path)})
    monkeypatch.setattr(MODULE, "_output_state", lambda *_args: ("PAIR_COMMITTED", {"main": True}, {"side": True}))
    monkeypatch.setattr(MODULE, "_revalidate_records", lambda _records: events.append("records"))
    monkeypatch.setattr(MODULE, "_snapshot", lambda *_args: events.append("snapshot") or {"fixed": True})
    returned = MODULE.publish_attestation(
        expected_generator_sha256="0" * 64,
        expected_generator_test_sha256="0" * 64,
    )
    assert returned[:3] == (document, payload, sidecar)
    assert events == [MODULE.SIDECAR.name, MODULE.OUTPUT.name, "records", "snapshot"]
    assert returned[3]["reports_namespace_immutable"] is False
    assert returned[3]["mandatory_downstream_exact_revalidation"] is True


@pytest.mark.skipif(os.geteuid() != 0, reason="root-only O_TMPFILE publication integration")
def test_root_tmp_sidecar_first_exact_pair_resume_and_idempotence(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    _patch_publication_authority(monkeypatch)
    output = tmp_path / MODULE.OUTPUT.name
    monkeypatch.setattr(MODULE, "OUTPUT", output)
    monkeypatch.setattr(MODULE, "SIDECAR", output.with_name(output.name + ".sha256"))
    document, payload, _ = _candidate()
    sidecar = f"{_sha(payload)}  {output.name}\n".encode("ascii")
    directory = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        MODULE._ensure_fragment(directory, MODULE.SIDECAR, sidecar, MODULE.MAX_SIDECAR_BYTES)
        assert MODULE._output_state(directory, payload, sidecar)[0] == "SIDECAR_COMMITTED"
        MODULE._ensure_fragment(directory, MODULE.OUTPUT, payload, MODULE.MAX_OUTPUT_BYTES)
        assert MODULE._output_state(directory, payload, sidecar)[0] == "PAIR_COMMITTED"
        first = (MODULE.OUTPUT.stat().st_ino, MODULE.SIDECAR.stat().st_ino)
        real_fsync = MODULE.os.fsync
        replay_events: list[str] = []
        def observed_fsync(descriptor: int) -> None:
            replay_events.append("directory" if descriptor == directory else "file")
            real_fsync(descriptor)
        monkeypatch.setattr(MODULE.os, "fsync", observed_fsync)
        MODULE._ensure_fragment(directory, MODULE.SIDECAR, sidecar, MODULE.MAX_SIDECAR_BYTES)
        MODULE._ensure_fragment(directory, MODULE.OUTPUT, payload, MODULE.MAX_OUTPUT_BYTES)
        assert replay_events == ["file", "directory", "file", "directory"]
        assert (MODULE.OUTPUT.stat().st_ino, MODULE.SIDECAR.stat().st_ino) == first
        for path in (MODULE.SIDECAR, MODULE.OUTPUT):
            status = path.stat()
            assert (status.st_uid, status.st_gid, stat.S_IMODE(status.st_mode), status.st_nlink) == (0, 0, 0o444, 1)
    finally:
        os.close(directory)


@pytest.mark.skipif(os.geteuid() != 0, reason="root-only EEXIST durability replay")
def test_root_eexist_race_replays_exact_and_foreign_is_untouched(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    _patch_publication_authority(monkeypatch)
    target = tmp_path / "fragment"
    payload = b"exact\n"
    directory = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    real_fsync = MODULE.os.fsync
    fsyncs: list[int] = []
    def fsync(descriptor: int) -> None:
        fsyncs.append(descriptor)
        real_fsync(descriptor)
    def raced(_directory: int, path: Path, data: bytes) -> dict[str, object]:
        path.write_bytes(data)
        os.chown(path, 0, 0)
        os.chmod(path, 0o444)
        raise FileExistsError(path.name)
    monkeypatch.setattr(MODULE.os, "fsync", fsync)
    monkeypatch.setattr(MODULE, "_publish_one", raced)
    try:
        record = MODULE._ensure_fragment(directory, target, payload, 100)
        assert record["sha256"] == _sha(payload) and len(fsyncs) == 2
        target.chmod(0o644)
        target.write_bytes(b"foreign\n")
        target.chmod(0o444)
        with pytest.raises(MODULE.AttestationError):
            MODULE._ensure_fragment(directory, target, payload, 100)
        assert target.read_bytes() == b"foreign\n"
    finally:
        os.close(directory)


@pytest.mark.skipif(os.geteuid() != 0, reason="root-only publication fault matrix")
@pytest.mark.parametrize(
    ("boundary", "linked_after_failure"),
    [
        ("write", False), ("fchown", False), ("fchmod", False),
        ("inode_fsync", False), ("linkat", False),
        ("parent_fsync", True), ("readback", True),
    ],
)
def test_root_publication_faults_leave_only_absent_or_exact_resumable_fragment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    boundary: str, linked_after_failure: bool,
) -> None:
    directory_path = tmp_path / boundary
    directory_path.mkdir()
    directory = os.open(directory_path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    target = directory_path / "fragment"
    payload = b"durable exact fragment\n"
    real_write = MODULE.os.write
    real_fchown = MODULE.os.fchown
    real_fchmod = MODULE.os.fchmod
    real_fsync = MODULE.os.fsync
    real_fragment = MODULE._fragment_at
    with monkeypatch.context() as scoped:
        _patch_publication_authority(scoped)
        if boundary == "write":
            scoped.setattr(MODULE.os, "write", lambda *_args: (_ for _ in ()).throw(OSError("write fault")))
        elif boundary == "fchown":
            scoped.setattr(MODULE.os, "fchown", lambda *_args: (_ for _ in ()).throw(OSError("chown fault")))
        elif boundary == "fchmod":
            scoped.setattr(MODULE.os, "fchmod", lambda *_args: (_ for _ in ()).throw(OSError("chmod fault")))
        elif boundary == "inode_fsync":
            scoped.setattr(MODULE.os, "fsync", lambda fd: (_ for _ in ()).throw(OSError("inode fsync fault")) if fd != directory else real_fsync(fd))
        elif boundary == "linkat":
            class FailedLink:
                argtypes: object = None
                restype: object = None
                def __call__(self, *_args: object) -> int:
                    return -1
            class FailedLibrary:
                linkat = FailedLink()
            scoped.setattr(MODULE.ctypes, "CDLL", lambda *_args, **_kwargs: FailedLibrary())
            scoped.setattr(MODULE.ctypes, "get_errno", lambda: errno.EIO)
        elif boundary == "parent_fsync":
            scoped.setattr(MODULE.os, "fsync", lambda fd: (_ for _ in ()).throw(OSError("parent fsync fault")) if fd == directory else real_fsync(fd))
        elif boundary == "readback":
            scoped.setattr(MODULE, "_fragment_at", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("readback fault")))
        with pytest.raises(OSError):
            MODULE._publish_one(directory, target, payload)
    assert target.exists() is linked_after_failure
    if target.exists():
        assert target.read_bytes() == payload
        status = target.stat()
        assert (status.st_uid, status.st_gid, stat.S_IMODE(status.st_mode), status.st_nlink) == (0, 0, 0o444, 1)
    with monkeypatch.context() as scoped:
        _patch_publication_authority(scoped)
        record = MODULE._ensure_fragment(directory, target, payload, 1024)
    assert record["sha256"] == _sha(payload)
    assert target.read_bytes() == payload
    os.close(directory)


def test_future_rfc3161_plan_is_nonexecuting_and_exact() -> None:
    text = SOURCE_BYTES.decode("utf-8")
    assert "http://timestamp.digicert.com" in text
    assert '"performed_by_this_generator": False' in text
    assert '"network_performed": False' in text
    assert '"digest_algorithm": "sha256"' in text
    assert "subprocess" not in {alias.name for node in SOURCE_TREE.body if isinstance(node, ast.Import) for alias in node.names}
    assert not OUTPUT.exists() and not OUTPUT.with_name(OUTPUT.name + ".sha256").exists()


def test_complete_lifecycle_command_inventory_and_permission_failure_policy() -> None:
    function = next(
        node for node in SOURCE_TREE.body
        if isinstance(node, ast.FunctionDef) and node.name == "_process_scan"
    )
    literals = {
        node.value.decode("ascii") for node in ast.walk(function)
        if isinstance(node, ast.Constant) and isinstance(node.value, bytes)
        and node.value.decode("ascii", "ignore") in {
            "start-persistent-namespace", "activate-readonly-project", "seal",
            "close-seal-window", "freeze-guard", "freeze-guard-candidate",
            "freeze-evaluator-lock", "freeze-rfc3161-attestation",
            "rollback-sealed-runtimes", "stop-persistent-namespace",
            "exec-campaign", "exec-evaluator", "export-results",
            "deactivate-readonly-project",
        }
    }
    assert literals == {
        "start-persistent-namespace", "activate-readonly-project", "seal",
        "close-seal-window", "freeze-guard", "freeze-guard-candidate",
        "freeze-evaluator-lock", "freeze-rfc3161-attestation",
        "rollback-sealed-runtimes", "stop-persistent-namespace",
        "exec-campaign", "exec-evaluator", "export-results",
        "deactivate-readonly-project",
    }


def test_cross_namespace_scan_fails_closed_on_permission_denial(monkeypatch: pytest.MonkeyPatch) -> None:
    real_readlink = MODULE.os.readlink
    def denied(path: object) -> str:
        if str(path).endswith("/ns/mnt"):
            raise PermissionError("denied")
        return real_readlink(path)
    monkeypatch.setattr(MODULE.os, "readlink", denied)
    prior = {"reviewed_bindings": {"target_mount_exclusion_contract": {"checked_paths": []}}}
    with pytest.raises(MODULE.AttestationError, match="permission denied"):
        MODULE._process_scan(prior)


def test_unrelated_namespace_identity_churn_is_normalized_out_of_candidate() -> None:
    checked = {"/var/lib/telemetry-yield-confirmatory-v3"}
    first = MODULE._normalized_namespace_attestation({"mnt:[1]"}, checked)
    second = MODULE._normalized_namespace_attestation({"mnt:[2]", "mnt:[3]"}, checked)
    assert first == second
    assert first["unrelated_namespace_identities_attested"] is False
    assert "unique_mount_namespace_ids" not in first
    assert "unique_mount_namespace_count" not in first
