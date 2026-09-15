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
SOURCE = ROOT / "work/blind-phase-confirmatory-v2/build_retry3_prestart_seed_quarantine_terminal_independent_review_v1.py"
SPEC = importlib.util.spec_from_file_location("terminal_quarantine_review", SOURCE)
assert SPEC and SPEC.loader
REVIEW = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(REVIEW)


def _selfhashed(document: dict[str, object], field: str) -> bytes:
    value = copy.deepcopy(document)
    value[field] = REVIEW._document_sha(value)
    return (json.dumps(value, sort_keys=True) + "\n").encode()


def _identity(path: Path, payload: bytes, mode: int) -> dict[str, object]:
    return {
        "path": str(path), "size_bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
        "st_dev": 1, "st_ino": abs(hash(path)) + 1,
        "uid": 0, "gid": 0, "mode": mode, "nlink": 1,
    }


def _records(
    *, drifting: bool = False, blocked_runner: bool = False,
    bad_invocation: bool = False,
) -> dict[str, tuple[bytes, dict[str, object]]]:
    terminal = {
        "schema_version": "blind-phase-confirmatory-retry3-prestart-seed-quarantine-preflight-v1",
        "status": "PASS_READ_ONLY_TERMINAL_PROVENANCE_VALIDATED_NO_PUBLICATION_NO_RENAME",
        "attempt_id": REVIEW.ATTEMPT_ID,
        "durable_prefix_length": 2,
        "source_state": "DESTINATION_QUARANTINED",
    }
    snapshot = {
        "root": {"st_dev": 64512, "st_ino": 1594224},
        "entry_count": 4,
    }
    zero = {
        "keeper_absent": True, "relevant_mount_count": 0,
        "iq_content_opened": False, "scientific_outcome_generated": False,
    }
    def runner_bytes() -> bytes:
        payload = (
            f"QUARANTINE_TEMPLATE_BLOCKED_PENDING_EVIDENCE_AND_REVIEW = {blocked_runner!r}\n"
            "_calls = 0\n"
            f"_terminal = {terminal!r}\n_snapshot = {snapshot!r}\n_zero = {zero!r}\n"
            "def quarantine(**kwargs):\n"
            "    global _calls\n"
            "    _calls += 1\n"
            + ("    return {**_terminal, 'drift': _calls}\n" if drifting else "    return dict(_terminal)\n")
            + "def _snapshot_seed(*args, **kwargs): return dict(_snapshot)\n"
            "def _stage2(): return (object(), {}, {})\n"
            "def _zero_state(module): return dict(_zero)\n"
            + (
                "def _validate_invocation(): raise RuntimeError('invocation mismatch')\n"
                if bad_invocation else
                "def _validate_invocation(): return None\n"
            )
            + f"DESTINATION = Path({str(REVIEW.DESTINATION)!r})\n"
        ).encode()
        return b"from pathlib import Path\n" + payload

    runner_payload = runner_bytes()
    design_payload = _selfhashed(
        {
            "schema_version": "blind-phase-confirmatory-retry3-prestart-seed-quarantine-independent-review-v1",
            "status": "GO", "severity_counts": {"P0": 0, "P1": 0, "P2": 0},
        },
        "review_payload_sha256",
    )
    intent_payload = _selfhashed(
        {
            "schema_version": "blind-phase-confirmatory-retry3-prestart-seed-quarantine-intent-v1",
            "status": "COMMITTED_BEFORE_ATOMIC_NO_CLOBBER_CONTROL_ROOT_RENAME",
            "created_at_utc": "2026-09-06T17:00:00Z",
            "prestart_failure_evidence_chain": {"evidence": {"sha256": "e" * 64}},
        },
        "quarantine_intent_payload_sha256",
    )
    provenance_payload = _selfhashed(
        {
            "schema_version": "blind-phase-confirmatory-retry3-prestart-seed-quarantine-provenance-v1",
            "status": "PASS", "completed_at_utc": "2026-09-06T17:01:00Z",
        },
        "quarantine_payload_sha256",
    )
    values = {
        REVIEW.RUNNER.name: (runner_payload, 0o555),
        REVIEW.RUNNER_TEST.name: (b"tests", 0o555),
        REVIEW.DESIGN_REVIEW.name: (design_payload, 0o444),
        REVIEW.LOCK.name: (b"", 0o600),
        REVIEW.INTENT.name: (intent_payload, 0o444),
        REVIEW.PROVENANCE.name: (provenance_payload, 0o444),
    }
    records = {
        name: (payload, _identity(REVIEW.CONTROLLER / name, payload, mode))
        for name, (payload, mode) in values.items()
    }
    terminal["intent"] = REVIEW._simple(records[REVIEW.INTENT.name][1])
    terminal["provenance"] = REVIEW._simple(records[REVIEW.PROVENANCE.name][1])
    runner_payload = runner_bytes()
    records[REVIEW.RUNNER.name] = (
        runner_payload,
        _identity(REVIEW.RUNNER, runner_payload, 0o555),
    )
    design_payload = _selfhashed(
        {
            "schema_version": "blind-phase-confirmatory-retry3-prestart-seed-quarantine-independent-review-v1",
            "status": "GO",
            "reviewed_bindings": {
                "runner": REVIEW._simple(records[REVIEW.RUNNER.name][1]),
                "runner_tests": REVIEW._simple(records[REVIEW.RUNNER_TEST.name][1]),
            },
            "checks": {"safe": True},
            "severity_counts": {"P0": 0, "P1": 0, "P2": 0},
        },
        "review_payload_sha256",
    )
    records[REVIEW.DESIGN_REVIEW.name] = (
        design_payload,
        _identity(REVIEW.DESIGN_REVIEW, design_payload, 0o444),
    )
    for name in (REVIEW.DESIGN_REVIEW.name, REVIEW.INTENT.name, REVIEW.PROVENANCE.name):
        payload = f"{records[name][1]['sha256']}  {name}\n".encode()
        records[name + ".sha256"] = (
            payload, _identity(REVIEW.CONTROLLER / (name + ".sha256"), payload, 0o444)
        )
    return records


def _args(records: dict[str, tuple[bytes, dict[str, object]]]) -> argparse.Namespace:
    values = {
        "expected_runner_sha256": records[REVIEW.RUNNER.name][1]["sha256"],
        "expected_runner_test_sha256": records[REVIEW.RUNNER_TEST.name][1]["sha256"],
        "expected_design_review_sha256": records[REVIEW.DESIGN_REVIEW.name][1]["sha256"],
        "expected_design_review_sidecar_sha256": records[REVIEW.DESIGN_REVIEW.name + ".sha256"][1]["sha256"],
        "expected_intent_sha256": records[REVIEW.INTENT.name][1]["sha256"],
        "expected_intent_sidecar_sha256": records[REVIEW.INTENT.name + ".sha256"][1]["sha256"],
        "expected_provenance_sha256": records[REVIEW.PROVENANCE.name][1]["sha256"],
        "expected_provenance_sidecar_sha256": records[REVIEW.PROVENANCE.name + ".sha256"][1]["sha256"],
        "expected_generator_sha256": "1" * 64,
        "expected_generator_test_sha256": "2" * 64,
        "expected_template_sha256": "3" * 64,
        "expected_controller_st_dev": 64512,
        "expected_controller_st_ino": 123,
        "expected_var_lib_st_dev": 64512,
        "expected_var_lib_st_ino": 1179656,
    }
    return argparse.Namespace(**values)


def _install_build_mocks(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    records: dict[str, tuple[bytes, dict[str, object]]],
) -> None:
    monkeypatch.setattr(REVIEW, "BLOCKED_PENDING_EXECUTABLE_AND_TERMINAL_RECEIPTS", False)
    monkeypatch.setattr(
        REVIEW, "EXPECTED_RUNNER_SHA256", records[REVIEW.RUNNER.name][1]["sha256"]
    )
    monkeypatch.setattr(
        REVIEW, "EXPECTED_RUNNER_TEST_SHA256",
        records[REVIEW.RUNNER_TEST.name][1]["sha256"],
    )
    monkeypatch.setattr(
        REVIEW, "EXPECTED_DESIGN_REVIEW_SHA256",
        records[REVIEW.DESIGN_REVIEW.name][1]["sha256"],
    )
    expected = _args(records)
    for name in (
        "expected_design_review_sidecar_sha256",
        "expected_intent_sha256", "expected_intent_sidecar_sha256",
        "expected_provenance_sha256", "expected_provenance_sidecar_sha256",
        "expected_controller_st_dev", "expected_controller_st_ino",
        "expected_var_lib_st_dev", "expected_var_lib_st_ino",
    ):
        monkeypatch.setattr(REVIEW, name.upper(), getattr(expected, name))
    monkeypatch.setattr(
        REVIEW, "EXPECTED_INTENT_PAYLOAD_SHA256",
        json.loads(records[REVIEW.INTENT.name][0])["quarantine_intent_payload_sha256"],
    )
    monkeypatch.setattr(
        REVIEW, "EXPECTED_PROVENANCE_PAYLOAD_SHA256",
        json.loads(records[REVIEW.PROVENANCE.name][0])["quarantine_payload_sha256"],
    )
    monkeypatch.setattr(REVIEW, "_controller_records", lambda **_kwargs: records)
    monkeypatch.setattr(REVIEW, "SOURCE", tmp_path / "absent-canonical")

    def read_regular(path: Path, expected: str) -> tuple[bytes, dict[str, object]]:
        if path == REVIEW.GENERATOR:
            payload = b"generator"
        elif path == REVIEW.GENERATOR_TEST:
            payload = b"generator-test"
        else:
            assert path == REVIEW.TEMPLATE
            payload = REVIEW.TEMPLATE.read_bytes()
        identity = {
            "path": str(path), "size_bytes": len(payload), "sha256": expected,
            "st_dev": 1, "st_ino": 2, "uid": 1000, "gid": 1000,
            "mode": 0o664, "nlink": 1,
        }
        return payload, identity

    monkeypatch.setattr(REVIEW, "_read_regular", read_regular)


def test_source_compiles_and_executable_refreeze_binds_terminal_receipts() -> None:
    compile(SOURCE.read_bytes(), str(SOURCE), "exec")
    assert REVIEW.BLOCKED_PENDING_EXECUTABLE_AND_TERMINAL_RECEIPTS is False
    assert REVIEW.EXPECTED_RUNNER_SHA256 == "57193465820276e1e1d0f4c7cbc672a75df401a5e8e903fd6e26fcddd216b12d"
    assert REVIEW.EXPECTED_RUNNER_TEST_SHA256 == "44c5dd654a8491b557819adde1ed4ba015963eb65e98ee37d0ced9e7226fe48d"
    assert REVIEW.EXPECTED_DESIGN_REVIEW_SHA256 == "4c106c82c16e01e491431e44576024289901b34ef04feec1940a88adeedd335d"
    assert REVIEW.EXPECTED_DESIGN_REVIEW_SIDECAR_SHA256 == "5f217b0940c7da14a1c35743af9e84014773bbaf504ff7ba93eb9c46fbe823ca"
    assert REVIEW.EXPECTED_INTENT_SHA256 == "df5e2e1db52f30c1dc489dade91bc8de8ff7411a05f43969f9301a843e21ca74"
    assert REVIEW.EXPECTED_INTENT_PAYLOAD_SHA256 == "32dc95d9a376a72b44b968c9da319af8541f2abb1bf4dd19e66e156a5606811f"
    assert REVIEW.EXPECTED_INTENT_SIDECAR_SHA256 == "f5defc25cf96235aef4f74fc705e7f3d2b89e26161c547111a4fd1b30b6328b4"
    assert REVIEW.EXPECTED_PROVENANCE_SHA256 == "c9bf65fe570857b2361ba2e87d187aa2d60eb08953c534cda935531d9a8f9b98"
    assert REVIEW.EXPECTED_PROVENANCE_PAYLOAD_SHA256 == "a4416526109549daec09217984cb0991132629f6bd72eae3b6c8aa5816feac29"
    assert REVIEW.EXPECTED_PROVENANCE_SIDECAR_SHA256 == "19577bad49ac9d56ceb756c66c6b841e7a2b44417a2a52ba41904cb51f9f5177"
    assert REVIEW.EXPECTED_CONTROLLER_ST_DEV == 64512
    assert REVIEW.EXPECTED_CONTROLLER_ST_INO == 1594231
    assert REVIEW.EXPECTED_VAR_LIB_ST_DEV == 64512
    assert REVIEW.EXPECTED_VAR_LIB_ST_INO == 1179656


def test_main_invocation_gate_precedes_argument_parsing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def reject_invocation() -> None:
        raise REVIEW.ReviewError("invocation rejected before parser")
    monkeypatch.setattr(REVIEW, "_validate_invocation", reject_invocation)
    monkeypatch.setattr(REVIEW, "parser", lambda: pytest.fail("parser"))
    with pytest.raises(REVIEW.ReviewError, match="before parser"):
        REVIEW.main([])


def test_generator_ast_has_no_write_rename_delete_or_subprocess_primitive() -> None:
    tree = ast.parse(SOURCE.read_bytes())
    forbidden = {
        "write", "write_bytes", "write_text", "pwrite", "rename", "replace",
        "unlink", "remove", "rmdir", "mkdir", "makedirs", "chmod", "chown",
        "fchmod", "fchown", "link", "symlink", "truncate", "ftruncate",
        "Popen", "run", "system", "kill", "mount", "unshare", "setns",
    }
    observed = {
        node.attr for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and node.attr in forbidden
    }
    assert observed == set()
    bare_or_path_open = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and (
            (isinstance(node.func, ast.Name) and node.func.id == "open")
            or (
                isinstance(node.func, ast.Attribute)
                and node.func.attr == "open"
                and not (
                    isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "os"
                )
            )
        )
    ]
    assert bare_or_path_open == []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "os"
            and node.func.attr == "open"
        ):
            flags = node.args[1]
            names = {
                value.attr for value in ast.walk(flags)
                if isinstance(value, ast.Attribute)
                and isinstance(value.value, ast.Name)
                and value.value.id == "os"
            }
            assert not names & {
                "O_CREAT", "O_TRUNC", "O_WRONLY", "O_RDWR", "O_APPEND", "O_EXCL"
            }
    assert sum(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "exec"
        for node in ast.walk(tree)
    ) == 1
    terminal_calls = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "runner_module"
        and node.func.attr == "quarantine"
    ]
    assert len(terminal_calls) == 2
    for call in terminal_calls:
        preflight = next(
            keyword.value for keyword in call.keywords
            if keyword.arg == "preflight_only"
        )
        assert isinstance(preflight, ast.Constant) and preflight.value is True


def test_expected_checks_are_all_true() -> None:
    assert REVIEW.expected_checks()
    assert all(REVIEW.expected_checks().values())


@pytest.mark.skipif(os.geteuid() != 0, reason="root metadata controller integration")
def test_controller_records_is_parent_pinned_exact9_and_rejects_foreign(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    controller = tmp_path / "controller"
    controller.mkdir()
    controller.chmod(0o755)
    paths = {
        "runner": controller / "runner.py",
        "test": controller / "test.py",
        "review": controller / "review.json",
        "lock": controller / "lock",
        "intent": controller / "intent.json",
        "provenance": controller / "provenance.json",
    }
    monkeypatch.setattr(REVIEW, "CONTROLLER", controller)
    monkeypatch.setattr(REVIEW, "RUNNER", paths["runner"])
    monkeypatch.setattr(REVIEW, "RUNNER_TEST", paths["test"])
    monkeypatch.setattr(REVIEW, "DESIGN_REVIEW", paths["review"])
    monkeypatch.setattr(REVIEW, "LOCK", paths["lock"])
    monkeypatch.setattr(REVIEW, "INTENT", paths["intent"])
    monkeypatch.setattr(REVIEW, "PROVENANCE", paths["provenance"])
    expected_hashes: dict[str, str] = {}
    for path in paths.values():
        payload = b"" if path == paths["lock"] else path.name.encode()
        path.write_bytes(payload)
        path.chmod(0o600 if path == paths["lock"] else 0o444)
        expected_hashes[path.name] = hashlib.sha256(payload).hexdigest()
    for path in (paths["review"], paths["intent"], paths["provenance"]):
        sidecar = path.with_name(path.name + ".sha256")
        payload = f"{expected_hashes[path.name]}  {path.name}\n".encode()
        sidecar.write_bytes(payload)
        sidecar.chmod(0o444)
        expected_hashes[sidecar.name] = hashlib.sha256(payload).hexdigest()
    status = controller.lstat()
    records = REVIEW._controller_records(
        controller_dev=status.st_dev, controller_ino=status.st_ino,
        expected_hashes=expected_hashes,
    )
    assert set(records) == set(expected_hashes)
    (controller / "foreign").write_bytes(b"x")
    with pytest.raises(REVIEW.ReviewError, match="not exact9"):
        REVIEW._controller_records(
            controller_dev=status.st_dev, controller_ino=status.st_ino,
            expected_hashes=expected_hashes,
        )


def test_template_is_strict_pending_all_null_contract() -> None:
    REVIEW._validate_template(REVIEW.TEMPLATE.read_bytes())
    document = json.loads(REVIEW.TEMPLATE.read_bytes())
    document["checks"][next(iter(document["checks"]))] = True
    with pytest.raises(REVIEW.ReviewError, match="template contract differs"):
        REVIEW._validate_template(json.dumps(document).encode())


def test_build_review_binds_two_identical_terminal_passes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    records = _records()
    _install_build_mocks(monkeypatch, tmp_path, records)
    document = REVIEW.build_review(_args(records))
    assert document["status"] == "GO"
    assert document["severity_counts"] == {"P0": 0, "P1": 0, "P2": 0}
    reviewed = document["reviewed_bindings"]
    assert reviewed["pre_execution_design_review_sidecar"] == REVIEW._simple(
        records[REVIEW.DESIGN_REVIEW.name + ".sha256"][1]
    )
    assert reviewed["intent_sidecar"] == REVIEW._simple(
        records[REVIEW.INTENT.name + ".sha256"][1]
    )
    assert reviewed["provenance_sidecar"] == REVIEW._simple(
        records[REVIEW.PROVENANCE.name + ".sha256"][1]
    )
    assert reviewed["intent_payload_sha256"] == json.loads(
        records[REVIEW.INTENT.name][0]
    )["quarantine_intent_payload_sha256"]
    assert reviewed["provenance_payload_sha256"] == json.loads(
        records[REVIEW.PROVENANCE.name][0]
    )["quarantine_payload_sha256"]
    assert reviewed["controller_parent"] == {
        "path": str(REVIEW.CONTROLLER), "st_dev": 64512, "st_ino": 123,
        "uid": 0, "gid": 0, "mode": 0o755, "nlink": 2,
    }
    assert reviewed["rename_parent"] == {
        "path": str(REVIEW.VAR_LIB), "st_dev": 64512, "st_ino": 1179656,
        "uid": 0, "gid": 0, "mode": 0o755,
    }
    assert document["terminal_validation"]["fixed_point"] is True
    assert document["terminal_validation"]["canonical_source_absent"] is True
    unhashed = dict(document)
    assert unhashed.pop(REVIEW.HASH_FIELD) == REVIEW._document_sha(unhashed)


def test_build_review_rejects_runner_identity_not_frozen_in_source(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    records = _records()
    _install_build_mocks(monkeypatch, tmp_path, records)
    args = _args(records)
    args.expected_runner_sha256 = "0" * 64
    with pytest.raises(REVIEW.ReviewError, match="identities are not frozen"):
        REVIEW.build_review(args)


def test_repo_inputs_are_read_before_terminal_passes_and_second_pass_is_final() -> None:
    source = SOURCE.read_text(encoding="utf-8")
    generator_read = source.index("generator_payload, generator = _read_regular")
    first_pass = source.index("first = runner_module.quarantine")
    second_pass = source.index("second = runner_module.quarantine")
    document_build = source.index("document: dict[str, object]")
    assert generator_read < first_pass < second_pass < document_build


def test_build_review_rejects_nonfixed_terminal_preflight(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    records = _records(drifting=True)
    _install_build_mocks(monkeypatch, tmp_path, records)
    with pytest.raises(REVIEW.ReviewError, match="fixed point differs"):
        REVIEW.build_review(_args(records))


def test_build_review_rejects_blocked_installed_runner(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    records = _records(blocked_runner=True)
    _install_build_mocks(monkeypatch, tmp_path, records)
    with pytest.raises(REVIEW.ReviewError, match="runner remains blocked"):
        REVIEW.build_review(_args(records))


def test_build_review_requires_runner_invocation_gate(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    records = _records(bad_invocation=True)
    _install_build_mocks(monkeypatch, tmp_path, records)
    with pytest.raises(RuntimeError, match="invocation mismatch"):
        REVIEW.build_review(_args(records))


@pytest.mark.parametrize("target", ["design", "intent", "provenance"])
def test_build_review_rejects_wrong_sidecar(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, target: str,
) -> None:
    records = _records()
    name = {
        "design": REVIEW.DESIGN_REVIEW.name,
        "intent": REVIEW.INTENT.name,
        "provenance": REVIEW.PROVENANCE.name,
    }[target]
    records[name + ".sha256"] = (
        b"wrong\n", _identity(REVIEW.CONTROLLER / (name + ".sha256"), b"wrong\n", 0o444)
    )
    _install_build_mocks(monkeypatch, tmp_path, records)
    with pytest.raises(REVIEW.ReviewError, match="sidecar differs"):
        REVIEW.build_review(_args(records))


def test_build_review_rejects_nonzero_design_review(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    records = _records()
    payload = _selfhashed(
        {
            "schema_version": "blind-phase-confirmatory-retry3-prestart-seed-quarantine-independent-review-v1",
            "status": "GO", "severity_counts": {"P0": 0, "P1": 1, "P2": 0},
        },
        "review_payload_sha256",
    )
    records[REVIEW.DESIGN_REVIEW.name] = (
        payload, _identity(REVIEW.DESIGN_REVIEW, payload, 0o444)
    )
    sidecar = f"{records[REVIEW.DESIGN_REVIEW.name][1]['sha256']}  {REVIEW.DESIGN_REVIEW.name}\n".encode()
    records[REVIEW.DESIGN_REVIEW.name + ".sha256"] = (
        sidecar, _identity(REVIEW.DESIGN_REVIEW.with_name(REVIEW.DESIGN_REVIEW.name + ".sha256"), sidecar, 0o444)
    )
    _install_build_mocks(monkeypatch, tmp_path, records)
    with pytest.raises(REVIEW.ReviewError, match="zero-severity"):
        REVIEW.build_review(_args(records))


@pytest.mark.parametrize(
    "constant",
    ["EXPECTED_INTENT_PAYLOAD_SHA256", "EXPECTED_PROVENANCE_PAYLOAD_SHA256"],
)
def test_build_review_rejects_receipt_payload_identity_drift(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, constant: str,
) -> None:
    records = _records()
    _install_build_mocks(monkeypatch, tmp_path, records)
    monkeypatch.setattr(REVIEW, constant, "0" * 64)
    with pytest.raises(REVIEW.ReviewError, match="payload identity differs"):
        REVIEW.build_review(_args(records))
