from contextlib import ExitStack
import hashlib
import importlib.util
import os
from pathlib import Path
import types

import pytest


ROOT = Path("/home/ubuntu/telemetry-yield")
SOURCE = ROOT / "work/blind-phase-confirmatory-v2/request_attested_timestamp_v1.py"
SPEC = importlib.util.spec_from_file_location("request_attested_timestamp_v1", SOURCE)
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)


def tlv(tag, payload):
    assert len(payload) < 128
    return bytes((tag, len(payload))) + payload


def query_for(data, nonce=b"\x01"):
    algorithm = tlv(48, tlv(6, bytes.fromhex("608648016503040201")) + tlv(5, b""))
    imprint = tlv(48, algorithm + tlv(4, hashlib.sha256(data).digest()))
    return tlv(48, tlv(2, b"\x01") + imprint + tlv(2, nonce) + tlv(1, b"\xff"))


def secure_file(path, payload):
    path.write_bytes(payload)
    path.chmod(0o444)
    return path


@pytest.fixture
def case(tmp_path, monkeypatch):
    if os.geteuid() != 0 or os.getegid() != 0:
        pytest.skip("root ownership/publication regression requires sudo -n")
    data = secure_file(tmp_path / "artifact.json", b"exact frozen artifact\n")
    ca = secure_file(tmp_path / "ca.pem", b"fake CA for mocked protocol tests\n")
    args = types.SimpleNamespace(data=str(data), data_sha256=M.digest(data.read_bytes()),
        data_size=data.stat().st_size, ca=str(ca), ca_sha256=M.digest(ca.read_bytes()),
        query=str(tmp_path / "artifact.tsq"), response=str(tmp_path / "artifact.tsr"),
        request=False, publish=False)
    query = query_for(data.read_bytes())
    response = b"mocked valid signed response"
    calls = []

    def generate(records, arguments, fds):
        assert "-query" in arguments
        return query

    def network(stack, records, supplied):
        assert supplied == query
        calls.append("network")
        return response

    def verify(stack, records, supplied, reply):
        assert supplied == query
        if reply != response:
            raise M.TimestampError("foreign response")
        calls.append("verify")
        return {"status": "Granted"}

    monkeypatch.setattr(M, "openssl", generate)
    monkeypatch.setattr(M, "request_response", network)
    monkeypatch.setattr(M, "verify_response", verify)
    return args, query, response, calls


@pytest.mark.parametrize("transform", [
    lambda q: q + b"\x00", lambda q: q[:-1], lambda q: b"\x30\x80" + q,
    lambda q: q.replace(bytes.fromhex("608648016503040201"), bytes.fromhex("608648016503040202")),
    lambda q: q[:-1] + b"\x00",
])
def test_malformed_query_rejected(transform):
    with pytest.raises(M.TimestampError):
        M.validate_query(transform(query_for(b"data")), M.digest(b"data"))


@pytest.mark.parametrize("nonce", [b"", b"\x00", b"\x80", b"\x00\x01", b"\x01" * 21])
def test_invalid_nonce_rejected(nonce):
    with pytest.raises(M.TimestampError):
        M.validate_query(query_for(b"data", nonce), M.digest(b"data"))


def test_foreign_digest_rejected():
    with pytest.raises(M.TimestampError, match="digest"):
        M.validate_query(query_for(b"foreign"), M.digest(b"data"))


def test_preflight_performs_no_network_or_publication(case, tmp_path):
    args, _, _, calls = case
    before = {p.name: p.stat() for p in tmp_path.iterdir()}
    result = M.execute(args)
    assert result["status"] == "PASS_PREFLIGHT"
    assert not calls and not result["network_performed"]
    assert {p.name: p.stat() for p in tmp_path.iterdir()} == before


def test_publish_requires_request(case):
    args, *_ = case
    args.publish = True
    with pytest.raises(M.TimestampError, match="requires --request"):
        M.execute(args)


def test_complete_publication_and_existing_pair_resume_without_network(case, monkeypatch):
    args, query, response, calls = case
    args.request = args.publish = True
    result = M.execute(args)
    assert result["status"] == "PASS_VERIFIED_PAIR"
    for name, expected in ((args.query, query), (args.response, response)):
        path = Path(name)
        assert path.read_bytes() == expected
        assert path.stat().st_mode & 0o777 == 0o444
        assert path.stat().st_uid == path.stat().st_gid == 0
        assert path.stat().st_nlink == 1
    assert calls == ["network", "verify"]
    syncs = []
    original_sync = os.fdatasync
    monkeypatch.setattr(M.os, "fdatasync", lambda fd: (syncs.append(os.fstat(fd).st_ino), original_sync(fd))[-1])
    calls.clear()
    again = M.execute(args)
    assert again["initial_state"] == "PAIR_COMMITTED"
    assert not again["network_performed"] and calls == ["verify"]
    assert syncs == [Path(args.query).stat().st_ino, Path(args.response).stat().st_ino]


def test_query_durable_before_network_and_resume_after_failure(case, monkeypatch):
    args, query, _, _ = case
    args.request = args.publish = True
    original = M.request_response

    def fail_network(*unused):
        assert Path(args.query).read_bytes() == query
        assert not Path(args.response).exists()
        raise M.TimestampError("network interruption")

    monkeypatch.setattr(M, "request_response", fail_network)
    with pytest.raises(M.TimestampError, match="network interruption"):
        M.execute(args)
    query_inode = Path(args.query).stat().st_ino
    monkeypatch.setattr(M, "request_response", original)
    result = M.execute(args)
    assert result["initial_state"] == "QUERY_COMMITTED"
    assert Path(args.query).stat().st_ino == query_inode


def test_response_link_fsync_crash_resumes_exact_pair(case, monkeypatch):
    args, _, _, _ = case
    args.request = args.publish = True
    real = M.os.fsync

    def crash_after_link(fd):
        if Path(args.response).exists():
            raise OSError("injected response directory fsync crash")
        return real(fd)

    monkeypatch.setattr(M.os, "fsync", crash_after_link)
    with pytest.raises(OSError, match="injected"):
        M.execute(args)
    assert Path(args.query).exists() and Path(args.response).exists()
    monkeypatch.setattr(M.os, "fsync", real)
    result = M.execute(args)
    assert result["status"] == "PASS_VERIFIED_PAIR"
    assert result["network_performed"] is False


def test_query_link_fsync_crash_resumes_exact_query(case, monkeypatch):
    args, _, _, _ = case
    args.request = args.publish = True
    real = M.os.fsync
    monkeypatch.setattr(M.os, "fsync", lambda fd: (_ for _ in ()).throw(OSError("crash")))
    with pytest.raises(OSError, match="crash"):
        M.execute(args)
    assert Path(args.query).exists() and not Path(args.response).exists()
    monkeypatch.setattr(M.os, "fsync", real)
    assert M.execute(args)["initial_state"] == "QUERY_COMMITTED"


@pytest.mark.parametrize("kind", ["response_only", "foreign_query", "foreign_response", "symlink", "hardlink", "writable"])
def test_foreign_or_unsafe_existing_fragments_rejected(case, kind, tmp_path):
    args, query, response, calls = case
    if kind == "response_only":
        secure_file(Path(args.response), response)
    elif kind == "foreign_query":
        secure_file(Path(args.query), query_for(b"foreign"))
    elif kind == "foreign_response":
        secure_file(Path(args.query), query)
        secure_file(Path(args.response), b"foreign response")
    elif kind == "symlink":
        Path(args.query).symlink_to(args.data)
    elif kind == "hardlink":
        source = secure_file(tmp_path / "extra", query)
        os.link(source, args.query)
    else:
        secure_file(Path(args.query), query).chmod(0o644)
    with pytest.raises((M.TimestampError, OSError)):
        M.execute(args)
    assert "network" not in calls


def test_no_clobber_eexist_retains_foreign_file(case, tmp_path):
    args, *_ = case
    path = secure_file(Path(args.query), b"foreign exact target")
    inode = path.stat().st_ino
    parent = M.Directory(tmp_path)
    try:
        with pytest.raises(M.TimestampError, match="refusing to overwrite"):
            M.publish_one(parent, path.name, b"candidate")
    finally:
        parent.close()
    assert path.read_bytes() == b"foreign exact target" and path.stat().st_ino == inode


def test_unrelated_regular_creation_rewrite_delete_does_not_drift_parent(case, tmp_path, monkeypatch):
    args, _, _, _ = case
    args.request = args.publish = True
    original = M.request_response

    def concurrent(*values):
        extra = tmp_path / "unrelated.json"
        extra.write_bytes(b"first")
        extra.write_bytes(b"second")
        extra.unlink()
        return original(*values)

    monkeypatch.setattr(M, "request_response", concurrent)
    assert M.execute(args)["status"] == "PASS_VERIFIED_PAIR"


def test_data_drift_during_request_rejects_before_response_publication(case, monkeypatch):
    args, _, _, _ = case
    args.request = args.publish = True
    original = M.request_response

    def concurrent(*values):
        Path(args.data).write_bytes(b"changed input")
        return original(*values)

    monkeypatch.setattr(M, "request_response", concurrent)
    with pytest.raises(M.TimestampError, match="drift"):
        M.execute(args)
    assert Path(args.query).exists() and not Path(args.response).exists()


def test_parent_mode_drift_rejected(case, tmp_path, monkeypatch):
    args, _, _, _ = case
    args.request = args.publish = True
    original = M.request_response

    def concurrent(*values):
        tmp_path.chmod(0o755)
        return original(*values)

    monkeypatch.setattr(M, "request_response", concurrent)
    with pytest.raises(M.TimestampError, match="directory identity"):
        M.execute(args)


@pytest.mark.parametrize("wire", [
    b"HTTP/1.1 500 Error\r\nContent-Type: application/timestamp-reply\r\n\r\nbody",
    b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\n\r\nbody",
    b"HTTP/1.1 200 OK\r\nContent-Type: application/timestamp-reply\r\n\r\n" + b"x" * (M.MAX_RESPONSE + 1),
])
def test_network_http_and_response_size_limits(wire, monkeypatch):
    monkeypatch.setattr(M, "bounded_command", lambda *a: (wire, b""))
    records = {"curl": types.SimpleNamespace(fd=0)}
    with ExitStack() as stack, pytest.raises(M.TimestampError):
        # Use original function even when other tests install mock fixtures.
        M.request_response(stack, records, b"query")


def test_bounded_subprocess_output_limit():
    fd = os.open("/usr/bin/printf", os.O_RDONLY)
    try:
        with pytest.raises(M.TimestampError, match="byte limit"):
            M.bounded_command(fd, ["x" * 2048], [], maximum=100)
    finally:
        os.close(fd)


def test_memfd_is_sealed():
    with ExitStack() as stack:
        fd = M.memory_file(stack, b"exact")
        with pytest.raises(OSError):
            os.write(fd, b"mutation")


def test_real_existing_rfc3161_fixture_verifies_and_nonce_mismatch_fails():
    if os.geteuid() != 0:
        pytest.skip("root secure file reader")
    base = ROOT / "reports/blind-phase-confirmatory-operational-amendment-v3-retry4-v1"
    data = Path(str(base) + ".json")
    query = Path(str(base) + ".tsq").read_bytes()
    response = Path(str(base) + ".tsr").read_bytes()
    ca = Path("/var/lib/telemetry-yield-confirmatory-v3/tsa-ca-certificates.pem")
    with ExitStack() as stack:
        records = {name: M.Pin(stack, path, size, sha, size, mode)
                   for name, (path, sha, size, mode) in M.TOOLS.items()}
        records["data"] = M.Pin(stack, data, M.MAX_DATA,
            "9ef67a21b69cc4fc52fdbe7402ffa9411d9501d75a203e1d9d113c7e9d318d71", 135095)
        records["ca"] = M.Pin(stack, ca, M.MAX_CA,
            "ecd9dc38bc3efb7dbd6431f57e29d2f8d6a0f0d211e1464b3fef2cbfe266fcd2")
        result = M.verify_response(stack, records, query, response)
        assert result["verified_against"] == ["query_and_nonce", "exact_data"]
        with pytest.raises(M.TimestampError):
            M.verify_response(stack, records, query_for(data.read_bytes(), b"\x01"), response)
        with pytest.raises(M.TimestampError):
            M.verify_response(stack, records, query, response + b"trailing junk")
