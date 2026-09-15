from contextlib import ExitStack
import copy
import importlib.util
import os
from pathlib import Path
import types

import pytest


PROJECT = Path("/home/ubuntu/telemetry-yield")
SOURCE = PROJECT / "work/blind-phase-confirmatory-v2/build_component_hardening_resumption_v1.py"
SPEC = importlib.util.spec_from_file_location("component_hardening_resumption", SOURCE)
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)


def refresh(value):
    value["raw_manifest_sha256"] = M.raw_hash(value)
    value["content_manifest_sha256"] = M.document_hash(M.semantic_entries(value))


@pytest.fixture
def closure_pair(monkeypatch):
    group = M.document_hash([".file"])
    old = {
        "runtime_root": {"path": str(M.ROOT), "st_dev": 1, "st_ino": 10,
            "uid": 1000, "gid": 1000, "mode": 0o775, "mtime_ns": 100, "ctime_ns": 100},
        "entries": [
            {"path": ".file", "kind": "file", "uid": 1000, "gid": 1000,
                "mode": 0o664, "execute_bits": 0, "size_bytes": 7, "sha256": M.sha(b"runtime"),
                "mtime_ns": 100, "ctime_ns": 100, "hardlink_group_sha256": group},
            {"path": "bin", "kind": "directory", "uid": 1000, "gid": 1000,
                "mode": 0o775, "execute_bits": 0o111, "mtime_ns": 100, "ctime_ns": 100},
            {"path": "link", "kind": "symlink", "uid": 1000, "gid": 1000,
                "mode": 0o777, "mtime_ns": 100, "ctime_ns": 100, "link_target": ".file",
                "resolved_relative_path": ".file", "resolved_kind": "file"},
        ],
        "entry_count": 3, "regular_file_count": 1, "directory_count": 1, "symlink_count": 1,
        "hardlink_topology": {"groups": [{"group_sha256": group, "paths": [".file"],
            "in_tree_link_count": 1, "st_nlink": 2, "external_link_count": 1}],
            "group_count": 1, "internal_multi_path_group_count": 0, "external_hardlink_path_count": 1},
    }
    new = copy.deepcopy(old)
    new["runtime_root"].update(st_ino=20, uid=0, gid=0, mode=0o555, ctime_ns=200)
    for entry in new["entries"]:
        entry.update(uid=0, gid=0, ctime_ns=200)
        if entry["kind"] != "symlink":
            entry["mode"] &= ~0o222
    new["hardlink_topology"]["groups"][0].update(st_nlink=1, external_link_count=0)
    new["hardlink_topology"]["external_hardlink_path_count"] = 0
    refresh(old)
    refresh(new)
    monkeypatch.setattr(M, "OLD_RAW", old["raw_manifest_sha256"])
    monkeypatch.setattr(M, "CURRENT_RAW", new["raw_manifest_sha256"])
    monkeypatch.setattr(M, "CONTENT", old["content_manifest_sha256"])
    monkeypatch.setattr(M, "EXPECTED_ROOT", dict(new["runtime_root"]))
    monkeypatch.setattr(M, "OLD_ROOT_INODE", 10)
    monkeypatch.setattr(M, "BROKEN_EXTERNAL_LINKS", 1)
    monkeypatch.setattr(M, "EXPECTED_COUNTS", {"entry_count": 3, "regular_file_count": 1,
        "directory_count": 1, "symlink_count": 1})
    return old, new


def test_complete_sealing_transform_preserves_original(closure_pair):
    old, new = closure_pair
    before = copy.deepcopy(old)
    proof = M.validate_transition(old, new)
    assert proof["semantic_content_unchanged"] is True
    assert proof["original_raw_manifest_exact"] is False
    assert proof["current_preseal_raw_manifest_exact"] is True
    assert proof["root_owned_readonly_unique_files"] is True
    assert proof["changed_entry_count"] == 3
    assert proof["changed_mode_entry_count"] == 2
    assert proof["broken_external_hardlink_count"] == 1
    assert old == before


@pytest.mark.parametrize("mutation", [
    lambda v: v["entries"][0].update(sha256=M.sha(b"changed")),
    lambda v: v["entries"][0].update(size_bytes=8),
    lambda v: v["entries"][0].update(execute_bits=0o111),
    lambda v: v["entries"][0].update(mtime_ns=101),
    lambda v: v["entries"][0].update(uid=1),
    lambda v: v["entries"][0].update(mode=0o644),
    lambda v: v["entries"][0].update(ctime_ns=100),
    lambda v: v["entries"][2].update(link_target="bin", resolved_relative_path="bin", resolved_kind="directory"),
    lambda v: v["runtime_root"].update(st_ino=21),
    lambda v: v["hardlink_topology"]["groups"][0].update(st_nlink=2, external_link_count=1),
    lambda v: v["hardlink_topology"].update(external_hardlink_path_count=1),
    lambda v: v["entries"][0].update(unexpected_field="not authorized"),
])
def test_actual_transition_invariants_reject_changes_even_with_new_raw_pin(closure_pair, monkeypatch, mutation):
    old, new = closure_pair
    mutation(new)
    refresh(new)
    # Exercise the transformation checks, not merely a stale outer digest.
    monkeypatch.setattr(M, "CURRENT_RAW", new["raw_manifest_sha256"])
    with pytest.raises(M.ResumptionError):
        M.validate_transition(old, new)


def test_complete_inventory_hash_must_match_recorded_raw(closure_pair):
    old, new = closure_pair
    new["entries"][0]["ctime_ns"] += 1
    with pytest.raises(M.ResumptionError, match="raw or semantic"):
        M.validate_transition(old, new)


def test_duplicate_or_missing_paths_cannot_be_hidden_by_claimed_counts(closure_pair, monkeypatch):
    old, new = closure_pair
    new["entries"] = [new["entries"][0], new["entries"][0], new["entries"][2]]
    refresh(new)
    monkeypatch.setattr(M, "CURRENT_RAW", new["raw_manifest_sha256"])
    with pytest.raises(M.ResumptionError):
        M.validate_transition(old, new)


def test_fixed_point_requires_two_equal_full_raw_scans(closure_pair):
    _, new = closure_pair
    calls = []
    builder = types.SimpleNamespace(_component_closure_live=lambda root: (calls.append(root), copy.deepcopy(new))[1])
    assert M.fixed_point(builder) == new
    assert calls == [M.ROOT, M.ROOT]
    changed = copy.deepcopy(new)
    changed["runtime_root"]["ctime_ns"] += 1
    sequence = iter([new, changed])
    builder._component_closure_live = lambda root: next(sequence)
    with pytest.raises(M.ResumptionError, match="scans disagree"):
        M.fixed_point(builder)


def test_held_input_digest_path_and_owner_mode_are_enforced(tmp_path):
    path = tmp_path / "authority"
    path.write_bytes(b"exact")
    path.chmod(0o444)
    uid, gid = os.geteuid(), os.getegid()
    with ExitStack() as stack:
        pin = M.HeldInput(stack, path, M.sha(b"exact"), 5, uid=uid, gid=gid)
        assert pin.compact()["sha256"] == M.sha(b"exact")
        path.chmod(0o644)
        with pytest.raises(M.ResumptionError, match="changed"):
            pin.validate()
    path.chmod(0o444)
    with ExitStack() as stack, pytest.raises(M.ResumptionError, match="SHA256"):
        M.HeldInput(stack, path, M.sha(b"wrong"), 5, uid=uid, gid=gid)


def test_held_input_rejects_leaf_and_ancestor_symlinks(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    path = real / "authority"
    path.write_bytes(b"exact")
    path.chmod(0o444)
    (tmp_path / "link").symlink_to(real, target_is_directory=True)
    (tmp_path / "leaf").symlink_to(path)
    for candidate in (tmp_path / "link/authority", tmp_path / "leaf"):
        with ExitStack() as stack, pytest.raises(OSError):
            M.HeldInput(stack, candidate, M.sha(b"exact"), 5, uid=os.geteuid(), gid=os.getegid())


@pytest.fixture
def publisher(tmp_path, monkeypatch):
    if os.geteuid() != 0 or os.getegid() != 0:
        pytest.skip("root-owned publication regression")
    output = tmp_path / "supplement.json"
    monkeypatch.setattr(M, "OUTPUT", output)
    monkeypatch.setattr(M, "SIDECAR", output.with_name(output.name + ".sha256"))
    with ExitStack() as stack:
        pin = M.HeldInput(stack, *M.PUBLICATION, mode=0o664)
        publication = M.load_held_module(pin, "test_pinned_component_publication")
        parent = publication.Directory(tmp_path)
        stack.callback(parent.close)
        payload = M.canonical({"supplement": "exact"})
        sidecar = f"{M.sha(payload)}  {output.name}\n".encode()
        yield stack, publication, parent, payload, sidecar


def test_read_only_output_check_creates_nothing(publisher, tmp_path):
    stack, publication, parent, payload, sidecar = publisher
    assert M.output_pair(stack, publication, parent, payload, sidecar) == [None, None]
    assert list(tmp_path.iterdir()) == []


def test_checksum_first_publication_and_durable_exact_pair_resume(publisher, monkeypatch):
    stack, publication, parent, payload, sidecar = publisher
    published = []
    original = publication.publish_one
    monkeypatch.setattr(publication, "publish_one", lambda p, n, b: (published.append(n), original(p, n, b))[1])
    main, checksum = M.publish_pair(stack, publication, parent, payload, sidecar)
    assert published == [M.SIDECAR.name, M.OUTPUT.name]
    assert main.payload == payload and checksum.payload == sidecar
    for path in (M.OUTPUT, M.SIDECAR):
        assert path.stat().st_uid == path.stat().st_gid == 0
        assert path.stat().st_mode & 0o777 == 0o444
        assert path.stat().st_nlink == 1
    published.clear()
    syncs = []
    original_sync = M.os.fdatasync
    monkeypatch.setattr(M.os, "fdatasync", lambda fd: (syncs.append(os.fstat(fd).st_ino), original_sync(fd))[1])
    M.publish_pair(stack, publication, parent, payload, sidecar)
    assert not published
    assert syncs == [M.SIDECAR.stat().st_ino, M.OUTPUT.stat().st_ino]


def test_crash_after_checksum_does_not_overwrite_and_resumes(publisher, monkeypatch):
    stack, publication, parent, payload, sidecar = publisher
    original = publication.publish_one
    def fail_main(p, name, body):
        if name == M.OUTPUT.name:
            raise OSError("injected crash before main link")
        return original(p, name, body)
    monkeypatch.setattr(publication, "publish_one", fail_main)
    with pytest.raises(OSError, match="injected crash"):
        M.publish_pair(stack, publication, parent, payload, sidecar)
    assert M.SIDECAR.read_bytes() == sidecar and not M.OUTPUT.exists()
    inode = M.SIDECAR.stat().st_ino
    monkeypatch.setattr(publication, "publish_one", original)
    M.publish_pair(stack, publication, parent, payload, sidecar)
    assert M.SIDECAR.stat().st_ino == inode


@pytest.mark.parametrize("state", ["main_only", "foreign_sidecar", "foreign_main"])
def test_foreign_or_impossible_pairs_are_preserved_and_rejected(publisher, state):
    stack, publication, parent, payload, sidecar = publisher
    values = ({M.OUTPUT: payload} if state == "main_only" else
              {M.SIDECAR: b"foreign"} if state == "foreign_sidecar" else
              {M.SIDECAR: sidecar, M.OUTPUT: b"foreign"})
    for path, data in values.items():
        publication.publish_one(parent, path.name, data)
    before = {path: (path.read_bytes(), path.stat().st_ino) for path in values}
    with pytest.raises(M.ResumptionError):
        M.publish_pair(stack, publication, parent, payload, sidecar)
    assert {path: (path.read_bytes(), path.stat().st_ino) for path in values} == before


def test_unrelated_reports_files_may_change_without_touching_protected_pair(publisher, tmp_path):
    stack, publication, parent, payload, sidecar = publisher
    M.publish_pair(stack, publication, parent, payload, sidecar)
    unrelated = tmp_path / "other-job.json"
    unrelated.write_bytes(b"first")
    unrelated.write_bytes(b"second")
    unrelated.unlink()
    main, checksum = M.publish_pair(stack, publication, parent, payload, sidecar)
    assert main.payload == payload and checksum.payload == sidecar


def test_default_full_workflow_is_read_only_with_explicit_disclosures(closure_pair, publisher, monkeypatch):
    old, new = closure_pair
    stack, publication, _, _, _ = publisher
    fd = os.open(M.PUBLICATION[0], os.O_RDONLY)
    stack.callback(os.close, fd)
    pin = types.SimpleNamespace(fd=fd, validate=lambda: None,
        compact=lambda: {"path": "held-authority", "sha256": "1" * 64, "size_bytes": 1})
    old["manifest_payload_sha256"] = "historical-selfhash"
    pins = {name: pin for name in ("generator", "inventory_builder", "publication_primitives",
        "original_closure", *M.SCIENCE)}
    builder = types.SimpleNamespace(_component_closure_live=lambda root: copy.deepcopy(new))
    monkeypatch.setattr(M, "prepare", lambda *args: (pins, old, builder, publication))
    result = M.execute("external-source-digest")
    assert result["status"] == "PASS_READ_ONLY_CANDIDATE"
    assert result["published"] is False and result["initial_state"] == "FRESH"
    assert not M.OUTPUT.exists() and not M.SIDECAR.exists()

