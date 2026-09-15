from __future__ import annotations

import argparse
import ast
import copy
import hashlib
import json
import os
from pathlib import Path
import stat
from types import ModuleType, SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / (
    "work/blind-phase-confirmatory-v2/"
    "build_operational_amendment_v3_retry4_review.py"
)
EXPECTED_GENERATOR_SHA256 = (
    "b86e44f9d5d018f2fd1935947ec43712fc62b32050bfb74d60f2601b1c715048"
)
SOURCE_BYTES = SOURCE.read_bytes()
assert hashlib.sha256(SOURCE_BYTES).hexdigest() == EXPECTED_GENERATOR_SHA256
ast.parse(SOURCE_BYTES)
SOURCE_CODE = compile(SOURCE_BYTES, str(SOURCE), "exec")
MODULE = ModuleType("retry4_review_generator")
MODULE.__file__ = str(SOURCE)
MODULE.__package__ = None
MODULE.__spec__ = None
exec(SOURCE_CODE, MODULE.__dict__)


def _status(
    *,
    inode: int = 10,
    device: int = 20,
    mode: int = stat.S_IFREG | 0o444,
    uid: int = 0,
    gid: int = 0,
    nlink: int = 1,
    size: int = 0,
    mtime_ns: int = 100,
    ctime_ns: int = 200,
) -> SimpleNamespace:
    return SimpleNamespace(
        st_dev=device,
        st_ino=inode,
        st_mode=mode,
        st_uid=uid,
        st_gid=gid,
        st_nlink=nlink,
        st_size=size,
        st_mtime_ns=mtime_ns,
        st_ctime_ns=ctime_ns,
    )


def _identity(path: Path, digest: str, size: int = 10) -> dict[str, object]:
    return {
        "path": str(path.absolute()),
        "st_dev": 20,
        "st_ino": abs(hash(str(path))) % 100000 + 100,
        "uid": 1000,
        "gid": 1000,
        "mode": 0o664,
        "nlink": 1,
        "size_bytes": size,
        "sha256": digest,
    }


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _checks() -> dict[str, bool]:
    return {f"review_check_{index:02d}": True for index in range(12)}


def _template(checks: dict[str, bool]) -> dict[str, object]:
    return {
        "schema_version": MODULE.EXPECTED_REVIEW_SCHEMA,
        "status": "REVIEW_REQUIRED",
        "reviewed_bindings": None,
        "checks": {key: None for key in checks},
        "publication_scope": {
            key: None for key in MODULE.EXPECTED_PUBLICATION_SCOPE
        },
        "severity_counts": {"P0": None, "P1": None, "P2": None},
        "review_payload_sha256": None,
    }


def _fake_freezer(
    generator_sha: str,
    generator_test_sha: str,
    freezer_sha: str,
    freezer_test_sha: str,
    template_sha: str,
) -> tuple[SimpleNamespace, dict[str, object], dict[str, int]]:
    generator = _identity(SOURCE, generator_sha, SOURCE.stat().st_size)
    generator_test = _identity(
        Path(__file__), generator_test_sha, Path(__file__).stat().st_size
    )
    freezer = _identity(MODULE.FREEZER, freezer_sha)
    freezer_test = _identity(MODULE.FREEZER_TEST, freezer_test_sha)
    template_payload = MODULE._canonical_bytes(_template(_checks()))
    template = _identity(MODULE.TEMPLATE, template_sha, len(template_payload))
    bindings: dict[str, object] = {
        "retry4_review_generator": MODULE._simple(generator),
        "retry4_review_generator_test": MODULE._simple(generator_test),
        "retry4_freezer": MODULE._simple(freezer),
        "retry4_freezer_test": MODULE._simple(freezer_test),
        "retry4_review_template": MODULE._simple(template),
    }
    for index in range(MODULE.EXPECTED_BINDING_COUNT - len(bindings)):
        bindings[f"input_{index:02d}"] = {
            "path": f"/tmp/input-{index:02d}",
            "size_bytes": index + 1,
            "sha256": f"{index + 1:064x}",
        }
    prepared = {
        "identities": bindings,
        "previous": {"science": {"schedule": [1, 2, 3]}},
    }
    calls = {"prepare": 0, "build": 0}
    fake = SimpleNamespace()
    fake.REVIEW_OUTPUT = MODULE.OUTPUT
    fake.REVIEW_SCHEMA = MODULE.EXPECTED_REVIEW_SCHEMA
    fake.FREEZER_TEST = MODULE.FREEZER_TEST
    fake.TEMPLATE = MODULE.TEMPLATE
    fake.REVIEW_GENERATOR = SOURCE
    fake.REVIEW_GENERATOR_TEST = Path(__file__)
    fake._json = lambda *_args, **_kwargs: pytest.fail("real review loader called")
    fake._sidecar = lambda *_args, **_kwargs: pytest.fail("real sidecar loader called")
    fake._science_snapshot = lambda document: copy.deepcopy(document["science"])
    fake.sha256_document = MODULE._document_sha256
    fake.expected_review_checks = _checks
    fake.expected_review_publication_scope = (
        lambda: dict(MODULE.EXPECTED_PUBLICATION_SCOPE)
    )

    def prepare(_args: argparse.Namespace) -> dict[str, object]:
        calls["prepare"] += 1
        return copy.deepcopy(prepared)

    fake._prepare = prepare

    def build_amendment(args: argparse.Namespace) -> dict[str, object]:
        calls["build"] += 1
        review, review_identity = fake._json(
            args.review,
            args.expected_review_sha256,
            schema=fake.REVIEW_SCHEMA,
            status_value="GO",
            hash_field="review_payload_sha256",
        )
        sidecar_identity = fake._sidecar(
            args.review_sidecar,
            args.expected_review_sidecar_sha256,
            review_identity,
        )
        return {
            "science": {"schedule": [1, 2, 3]},
            "amended_implementation": {
                "independent_review": MODULE._simple(review_identity),
                "independent_review_sidecar": MODULE._simple(sidecar_identity),
                "independent_review_payload_sha256": review[
                    "review_payload_sha256"
                ],
            },
            "runtime_consumed_identity_uniqueness": {
                "conflict_count": 0,
                "builder_test_occurrence_count": 3,
            },
            "amendment_payload_sha256": "a" * 64,
        }

    fake.build_amendment = build_amendment
    return fake, prepared, calls


def _enable_fake_build(monkeypatch: pytest.MonkeyPatch):
    generator_sha = "1" * 64
    generator_test_sha = "2" * 64
    freezer_sha = "3" * 64
    freezer_test_sha = "4" * 64
    template_sha = "5" * 64
    fake, prepared, calls = _fake_freezer(
        generator_sha,
        generator_test_sha,
        freezer_sha,
        freezer_test_sha,
        template_sha,
    )
    checks = _checks()
    template_document = _template(checks)

    monkeypatch.setattr(MODULE, "GENERATOR_BLOCKED_PENDING_RETRY4_FREEZE", False)
    monkeypatch.setattr(MODULE, "EXPECTED_FREEZER_SHA256", freezer_sha)
    monkeypatch.setattr(MODULE, "EXPECTED_FREEZER_TEST_SHA256", freezer_test_sha)
    monkeypatch.setattr(MODULE, "EXPECTED_TEMPLATE_SHA256", template_sha)
    monkeypatch.setattr(MODULE, "_load_freezer", lambda: fake)
    monkeypatch.setattr(
        MODULE,
        "_args",
        lambda _freezer, **values: argparse.Namespace(
            review=MODULE.OUTPUT,
            expected_review_sha256=values["review_sha256"],
            review_sidecar=MODULE.SIDECAR,
            expected_review_sidecar_sha256=values["sidecar_sha256"],
            created_at=values["amendment_created_at"],
            publish=False,
        ),
    )

    def read_regular(path: Path, expected: str, _maximum: int):
        path = Path(path).absolute()
        if path == SOURCE:
            payload = SOURCE.read_bytes()
            return payload, _identity(path, expected, len(payload))
        if path == Path(__file__).absolute():
            payload = Path(__file__).read_bytes()
            return payload, _identity(path, expected, len(payload))
        if path == MODULE.FREEZER_TEST:
            return b"freezer-test", _identity(path, expected)
        if path == MODULE.TEMPLATE:
            payload = MODULE._canonical_bytes(template_document)
            return payload, _identity(path, expected, len(payload))
        raise AssertionError(f"unexpected read: {path}")

    monkeypatch.setattr(MODULE, "_read_regular", read_regular)
    return {
        "generator_sha": generator_sha,
        "generator_test_sha": generator_test_sha,
        "freezer": fake,
        "prepared": prepared,
        "calls": calls,
    }


def _build(monkeypatch: pytest.MonkeyPatch):
    fixture = _enable_fake_build(monkeypatch)
    document, context = MODULE.build_review(
        expected_generator_sha256=fixture["generator_sha"],
        expected_generator_test_sha256=fixture["generator_test_sha"],
        amendment_created_at="2026-09-06T15:00:00Z",
        reports_st_dev=20,
    )
    return fixture, document, context


def test_executable_generator_binds_exact_phase1_freeze() -> None:
    assert MODULE.GENERATOR_BLOCKED_PENDING_RETRY4_FREEZE is False
    assert MODULE.EXPECTED_FREEZER_SHA256 == (
        "77769c405551361300dba07767bb0949eeb84fc78c4585e33ead4f69bf27f5a7"
    )
    assert MODULE.EXPECTED_FREEZER_TEST_SHA256 == (
        "fed204da45899ebf06f19647a6ca80b96e862e4db7a9ee393277bd467295c62a"
    )
    assert MODULE.EXPECTED_TEMPLATE_SHA256 == (
        "b3d00f7c38753427b45e807a93983ab1f0ed43f8f0a9450cee0b80299ef2e300"
    )


def test_main_first_statement_is_exact_interpreter_gate() -> None:
    tree = ast.parse(SOURCE.read_text())
    main = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "main"
    )
    first = main.body[0]
    assert isinstance(first, ast.Expr)
    assert isinstance(first.value, ast.Call)
    assert isinstance(first.value.func, ast.Name)
    assert first.value.func.id == "_validate_invocation"


def test_cli_defaults_to_dry_run_and_has_no_iq_or_lifecycle_surface() -> None:
    actions = {action.dest: action for action in MODULE.parser()._actions}
    assert actions["publish"].default is False
    forbidden = ("iq", "lifecycle", "decoder", "campaign", "evaluator", "outcome")
    assert not any(
        any(word in action.lower() for word in forbidden) for action in actions
    )


def test_review_has_exact_26_bindings_12_checks_severity_and_selfhash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture, document, context = _build(monkeypatch)
    assert len(document["reviewed_bindings"]) == 26
    assert len(document["checks"]) == 12
    assert document["severity_counts"] == {"P0": 0, "P1": 0, "P2": 0}
    assert document["publication_scope"] == MODULE.EXPECTED_PUBLICATION_SCOPE
    unhashed = {
        key: value for key, value in document.items()
        if key != "review_payload_sha256"
    }
    assert document["review_payload_sha256"] == MODULE._document_sha256(unhashed)
    assert fixture["calls"] == {"prepare": 2, "build": 1}
    assert context["predicted_amendment"]["published"] is False
    assert context["predicted_amendment"]["runtime_identity_conflict_count"] == 0
    assert context["mutation_surface"] == {
        "os_open_call_count": 4,
        "filesystem_mutator_call_count": 6,
        "publisher_link_call_count": 2,
        "publish_and_verify_call_count": 1,
        "sole_mutation_function": "_publish_one_at",
        "publication_requires_explicit_publish_branch": True,
        "forbidden_mutation_process_lifecycle_iq_call_count": 0,
        "import_allowlist_exact": True,
        "static_and_dynamic_call_allowlists_exact": True,
        "callable_alias_allowlist_exact": True,
        "held_freezer_exec_shape_exact": True,
        "scoped_static_call_inventory_exact": True,
    }


def test_review_file_and_sidecar_predictions_are_exact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fixture, document, context = _build(monkeypatch)
    payload = MODULE._canonical_bytes(document)
    digest = hashlib.sha256(payload).hexdigest()
    sidecar = f"{digest}  {MODULE.OUTPUT.name}\n".encode()
    assert context["review_payload"] == payload
    assert context["review_file_sha256"] == digest
    assert context["sidecar_payload"] == sidecar
    assert context["sidecar_file_sha256"] == hashlib.sha256(sidecar).hexdigest()


def test_real_retry4_contract_builds_metadata_only_when_runtime_unblocked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    freezer_sha = _digest(MODULE.FREEZER)
    freezer_test_sha = _digest(MODULE.FREEZER_TEST)
    template_sha = _digest(MODULE.TEMPLATE)
    generator_sha = _digest(SOURCE)
    generator_test_sha = _digest(Path(__file__))
    monkeypatch.setattr(MODULE, "GENERATOR_BLOCKED_PENDING_RETRY4_FREEZE", False)
    monkeypatch.setattr(MODULE, "EXPECTED_FREEZER_SHA256", freezer_sha)
    monkeypatch.setattr(MODULE, "EXPECTED_FREEZER_TEST_SHA256", freezer_test_sha)
    monkeypatch.setattr(MODULE, "EXPECTED_TEMPLATE_SHA256", template_sha)
    original_load = MODULE._load_freezer

    def load_unblocked():
        freezer = original_load()
        freezer.RETRY4_TEMPLATE_BLOCKED_PENDING_EVIDENCE_AND_QUARANTINE_REVIEW = False
        return freezer

    monkeypatch.setattr(MODULE, "_load_freezer", load_unblocked)
    document, context = MODULE.build_review(
        expected_generator_sha256=generator_sha,
        expected_generator_test_sha256=generator_test_sha,
        amendment_created_at="2026-09-06T16:00:00Z",
        reports_st_dev=MODULE.REPORTS.lstat().st_dev,
    )
    assert document["status"] == "GO"
    assert len(document["reviewed_bindings"]) == 26
    assert context["predicted_amendment"]["published"] is False
    assert context["predicted_amendment"]["scientific_snapshot_unchanged"] is True


def test_binding_count_or_generator_binding_drift_rejects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _enable_fake_build(monkeypatch)
    fixture["prepared"]["identities"].pop("input_00")
    with pytest.raises(MODULE.ReviewError, match="binding/check"):
        MODULE.build_review(
            expected_generator_sha256=fixture["generator_sha"],
            expected_generator_test_sha256=fixture["generator_test_sha"],
            amendment_created_at="2026-09-06T15:00:00Z",
            reports_st_dev=20,
        )


def test_false_or_extra_check_rejects(monkeypatch: pytest.MonkeyPatch) -> None:
    fixture = _enable_fake_build(monkeypatch)
    checks = fixture["freezer"].expected_review_checks()
    checks["review_check_00"] = False
    fixture["freezer"].expected_review_checks = lambda: checks
    with pytest.raises(MODULE.ReviewError, match="binding/check"):
        MODULE.build_review(
            expected_generator_sha256=fixture["generator_sha"],
            expected_generator_test_sha256=fixture["generator_test_sha"],
            amendment_created_at="2026-09-06T15:00:00Z",
            reports_st_dev=20,
        )


def test_template_shape_or_placeholder_drift_rejects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _enable_fake_build(monkeypatch)
    original = MODULE._template
    document, identity = original(str(MODULE.EXPECTED_TEMPLATE_SHA256))
    document["severity_counts"]["P0"] = 0
    monkeypatch.setattr(MODULE, "_template", lambda _digest: (document, identity))
    with pytest.raises(MODULE.ReviewError, match="template shape"):
        MODULE.build_review(
            expected_generator_sha256=fixture["generator_sha"],
            expected_generator_test_sha256=fixture["generator_test_sha"],
            amendment_created_at="2026-09-06T15:00:00Z",
            reports_st_dev=20,
        )


def test_virtual_review_and_sidecar_hooks_are_restored_on_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _enable_fake_build(monkeypatch)
    original_json = fixture["freezer"]._json
    original_sidecar = fixture["freezer"]._sidecar
    fixture["freezer"].build_amendment = lambda _args: (_ for _ in ()).throw(
        RuntimeError("synthetic build failure")
    )
    with pytest.raises(RuntimeError, match="synthetic"):
        MODULE.build_review(
            expected_generator_sha256=fixture["generator_sha"],
            expected_generator_test_sha256=fixture["generator_test_sha"],
            amendment_created_at="2026-09-06T15:00:00Z",
            reports_st_dev=20,
        )
    assert fixture["freezer"]._json is original_json
    assert fixture["freezer"]._sidecar is original_sidecar


def test_second_prepare_drift_rejects(monkeypatch: pytest.MonkeyPatch) -> None:
    fixture = _enable_fake_build(monkeypatch)
    prepared = fixture["prepared"]
    count = 0

    def drifting(_args):
        nonlocal count
        count += 1
        value = copy.deepcopy(prepared)
        if count == 2:
            value["identities"]["input_00"]["sha256"] = "f" * 64
        return value

    fixture["freezer"]._prepare = drifting
    with pytest.raises(MODULE.ReviewError, match="two prepare"):
        MODULE.build_review(
            expected_generator_sha256=fixture["generator_sha"],
            expected_generator_test_sha256=fixture["generator_test_sha"],
            amendment_created_at="2026-09-06T15:00:00Z",
            reports_st_dev=20,
        )


def test_virtual_amendment_science_or_alias_drift_rejects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _enable_fake_build(monkeypatch)
    original = fixture["freezer"].build_amendment

    def changed(args):
        result = original(args)
        result["runtime_consumed_identity_uniqueness"][
            "builder_test_occurrence_count"
        ] = 2
        return result

    fixture["freezer"].build_amendment = changed
    with pytest.raises(MODULE.ReviewError, match="virtual retry4"):
        MODULE.build_review(
            expected_generator_sha256=fixture["generator_sha"],
            expected_generator_test_sha256=fixture["generator_test_sha"],
            amendment_created_at="2026-09-06T15:00:00Z",
            reports_st_dev=20,
        )


def test_read_regular_rejects_symlink_and_wrong_hash(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.write_bytes(b"bounded")
    link = tmp_path / "link"
    link.symlink_to(target)
    with pytest.raises(MODULE.ReviewError, match="noncanonical"):
        MODULE._read_regular(link, hashlib.sha256(b"bounded").hexdigest(), 32)
    with pytest.raises(MODULE.ReviewError, match="differs"):
        MODULE._read_regular(target, "0" * 64, 32)


def test_exact_source_mutation_surface_is_accepted() -> None:
    assert _digest(SOURCE) == EXPECTED_GENERATOR_SHA256
    assert MODULE._mutation_surface(SOURCE.read_bytes())[
        "sole_mutation_function"
    ] == "_publish_one_at"


def test_test_loader_executes_only_prehashed_in_memory_generator_bytes() -> None:
    test_source = Path(__file__).read_text()
    hash_offset = test_source.index(
        "assert hashlib.sha256(SOURCE_BYTES).hexdigest() == EXPECTED_GENERATOR_SHA256"
    )
    compile_offset = test_source.index(
        'SOURCE_CODE = compile(SOURCE_BYTES, str(SOURCE), "exec")'
    )
    exec_offset = test_source.index("exec(SOURCE_CODE, MODULE.__dict__)")
    assert hash_offset < compile_offset < exec_offset
    tree = ast.parse(test_source)
    assert not any(
        isinstance(node, ast.Import)
        and any(alias.name.startswith("importlib") for alias in node.names)
        for node in ast.walk(tree)
    )
    assert not any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in {"spec_from_file_location", "exec_module"}
        for node in ast.walk(tree)
    )


@pytest.mark.parametrize(
    ("old", "new", "message"),
    [
        (
            'os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)',
            'os.O_WRONLY | getattr(os, "O_CLOEXEC", 0)',
            "os.open call/flag",
        ),
        (
            "    absolute = path.absolute()\n",
            '    os.unlink("/tmp/forbidden")\n    absolute = path.absolute()\n',
            "forbidden",
        ),
        (
            "    try:\n        before = os.stat(name, dir_fd=directory, follow_symlinks=False)\n",
            "    os.write(directory, b'x')\n    try:\n        before = os.stat(name, dir_fd=directory, follow_symlinks=False)\n",
            "mutator allowlist",
        ),
        (
            "                _publish_one_at(directory, SIDECAR.name, sidecar_payload)\n",
            "                _publish_one_at(directory, OUTPUT.name, sidecar_payload)\n",
            "publisher call/argument",
        ),
        (
            "    args = parser().parse_args(argv)\n",
            "    subprocess.run(['/bin/true'])\n    args = parser().parse_args(argv)\n",
            "forbidden",
        ),
        (
            "    args = parser().parse_args(argv)\n",
            "    read_iq_payload()\n    args = parser().parse_args(argv)\n",
            "forbidden",
        ),
    ],
)
def test_mutation_surface_rejects_open_write_publish_process_and_iq_mutants(
    old: str, new: str, message: str,
) -> None:
    source = SOURCE.read_text()
    assert old in source
    mutated = source.replace(old, new, 1).encode()
    with pytest.raises(MODULE.ReviewError, match=message):
        MODULE._mutation_surface(mutated)


def test_mutation_surface_rejects_publish_call_outside_explicit_branch() -> None:
    source = SOURCE.read_text()
    marker = "    if args.publish:\n"
    injected = (
        "    publish_and_verify(\n"
        "        review, context,\n"
        "        expected_reports_st_dev=args.expected_reports_st_dev,\n"
        "        expected_reports_st_ino=args.expected_reports_st_ino,\n"
        "        expected_reports_nlink=args.expected_reports_nlink,\n"
        "    )\n"
    )
    assert marker in source
    with pytest.raises(MODULE.ReviewError, match="call count"):
        MODULE._mutation_surface(source.replace(marker, injected + marker, 1).encode())


@pytest.mark.parametrize(
    ("marker", "injected"),
    [
        (
            'if __name__ == "__main__":\n',
            'main(sys.argv[1:] + ["--publish"])\n\n',
        ),
        (
            "    args = parser().parse_args(argv)\n",
            "    build_review(\n"
            '        expected_generator_sha256="0" * 64,\n'
            '        expected_generator_test_sha256="0" * 64,\n'
            '        amendment_created_at="2026-09-06T00:00:00Z",\n'
            "        reports_st_dev=0,\n"
            "    )\n",
        ),
    ],
)
def test_mutation_surface_rejects_extra_allowed_static_calls(
    marker: str, injected: str,
) -> None:
    source = SOURCE.read_text()
    assert marker in source
    with pytest.raises(MODULE.ReviewError, match="scoped static-call inventory"):
        MODULE._mutation_surface(source.replace(marker, injected + marker, 1).encode())


@pytest.mark.parametrize(
    ("location", "injected", "message"),
    [
        (
            "main",
            '    danger = os.unlink\n    danger("/tmp/forbidden")\n',
            "unknown review generator call target|callable-alias",
        ),
        (
            "module",
            "from os import unlink as danger\n",
            "import allowlist",
        ),
        (
            "main",
            '    getattr(os, "unlink")("/tmp/forbidden")\n',
            "dynamic-call allowlist",
        ),
        (
            "main",
            '    danger = lambda: os.unlink("/tmp/forbidden")\n    danger()\n',
            "unknown review generator call target|callable-alias",
        ),
        (
            "main",
            '    len = getattr(os, "unlink")\n    len("/tmp/forbidden")\n',
            "Store/rebinding",
        ),
        (
            "main",
            '    exec("pass")\n',
            "held-freezer exec shape",
        ),
    ],
)
def test_mutation_surface_rejects_alias_dynamic_and_extra_exec_mutants(
    location: str, injected: str, message: str,
) -> None:
    source = SOURCE.read_text()
    if location == "module":
        marker = "from __future__ import annotations\n"
        replacement = marker + injected
    else:
        marker = "    args = parser().parse_args(argv)\n"
        replacement = injected + marker
    assert marker in source
    with pytest.raises(MODULE.ReviewError, match=message):
        MODULE._mutation_surface(source.replace(marker, replacement, 1).encode())


@pytest.mark.parametrize(
    "injected",
    [
        '    _simple = open\n    _simple("/tmp/forbidden", "w")\n',
        '    Path = open\n    Path("/tmp/forbidden", "w")\n',
    ],
)
def test_mutation_surface_rejects_allowed_target_name_rebinding(
    injected: str,
) -> None:
    source = SOURCE.read_text()
    marker = "    args = parser().parse_args(argv)\n"
    assert marker in source
    with pytest.raises(MODULE.ReviewError, match="Store/rebinding"):
        MODULE._mutation_surface(source.replace(marker, injected + marker, 1).encode())


def test_mutation_surface_rejects_linkat_name_alias_replacement() -> None:
    source = SOURCE.read_text()
    original = "    linkat = ctypes.CDLL(None, use_errno=True).linkat\n"
    assert original in source
    with pytest.raises(
        MODULE.ReviewError,
        match="Name-valued assignment|callable-alias|callable reference",
    ):
        MODULE._mutation_surface(
            source.replace(original, "    linkat = open\n", 1).encode()
        )


def test_mutation_surface_rejects_duplicate_function_and_callable_default() -> None:
    source = SOURCE.read_text()
    marker = "class ReviewError(RuntimeError):\n"
    duplicate = (
        "def _simple(print=open):\n"
        "    return print('/tmp/forbidden', 'w')\n\n\n"
    )
    assert marker in source
    with pytest.raises(MODULE.ReviewError, match="function-name multiset"):
        MODULE._mutation_surface(source.replace(marker, duplicate + marker, 1).encode())


@pytest.mark.parametrize(
    "injected",
    [
        'os.open("/tmp/forbidden", os.O_WRONLY)\n',
        'os.unlink("/tmp/forbidden")\n',
        'os.write(1, b"forbidden")\n',
        '_publish_one_at(1, "forbidden", b"x")\n',
    ],
)
def test_mutation_surface_rejects_module_level_mutants(injected: str) -> None:
    source = SOURCE.read_text()
    marker = "from __future__ import annotations\n"
    assert marker in source
    mutated = source.replace(marker, marker + injected, 1).encode()
    with pytest.raises(MODULE.ReviewError):
        MODULE._mutation_surface(mutated)


def test_read_at_rejects_symlink_and_hardlink_metadata_is_visible(
    tmp_path: Path,
) -> None:
    target = tmp_path / "target"
    target.write_bytes(b"bounded")
    link = tmp_path / "link"
    link.symlink_to(target)
    hard = tmp_path / "hard"
    os.link(target, hard)
    descriptor = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        with pytest.raises(MODULE.ReviewError, match="regular"):
            MODULE._read_at(descriptor, link.name, 32)
        observed = MODULE._read_at(descriptor, hard.name, 32)
    finally:
        os.close(descriptor)
    assert observed is not None
    assert observed[1]["nlink"] == 2


def test_validate_immutable_rejects_wrong_mode_owner_link_or_bytes() -> None:
    payload = b"review"
    valid = {
        "uid": 0,
        "gid": 0,
        "mode": 0o444,
        "nlink": 1,
    }
    for key, wrong in (("uid", 1000), ("gid", 1000), ("mode", 0o644), ("nlink", 2)):
        identity = dict(valid)
        identity[key] = wrong
        with pytest.raises(MODULE.ReviewError, match="immutable"):
            MODULE._validate_immutable((payload, identity), payload, "fixture")
    with pytest.raises(MODULE.ReviewError, match="immutable"):
        MODULE._validate_immutable((b"other", valid), payload, "fixture")


def _publication_fixture(
    monkeypatch: pytest.MonkeyPatch,
    *,
    initial: dict[str, bytes],
) -> tuple[dict[str, object], dict[str, object], list[str], dict[str, bytes]]:
    review = {
        "review_payload_sha256": "a" * 64,
    }
    review_payload = MODULE._canonical_bytes(review)
    review_sha = hashlib.sha256(review_payload).hexdigest()
    side_payload = MODULE._sidecar_bytes(review_sha)
    context = {
        "generator": {"sha256": "1" * 64},
        "generator_test": {"sha256": "2" * 64},
        "amendment_created_at": "2026-09-06T15:00:00Z",
        "review_payload": review_payload,
        "sidecar_payload": side_payload,
        "predicted_amendment": {"published": False},
    }
    state = dict(initial)
    order: list[str] = []
    parent = _status(
        inode=30,
        device=20,
        mode=stat.S_IFDIR | 0o775,
        uid=1000,
        gid=1000,
        nlink=18,
    )
    monkeypatch.setattr(MODULE.os, "geteuid", lambda: 0)
    monkeypatch.setattr(MODULE.os, "getegid", lambda: 0)
    monkeypatch.setattr(MODULE, "build_review", lambda **_kwargs: (review, context))
    monkeypatch.setattr(MODULE, "_open_reports", lambda **_kwargs: (99, parent))
    monkeypatch.setattr(MODULE.os, "fstat", lambda _fd: parent)
    monkeypatch.setattr(MODULE.os, "close", lambda _fd: None)
    monkeypatch.setattr(MODULE.REPORTS.__class__, "lstat", lambda _self: parent)

    def read_at(_directory: int, name: str, _maximum: int):
        payload = state.get(name)
        if payload is None:
            return None
        inode = 40 if name == MODULE.OUTPUT.name else 41
        identity = {
            "path": str(MODULE.REPORTS / name),
            "st_dev": 20,
            "st_ino": inode,
            "uid": 0,
            "gid": 0,
            "mode": 0o444,
            "nlink": 1,
            "size_bytes": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
        }
        return payload, identity

    def publish(_directory: int, name: str, payload: bytes):
        if name in state:
            raise MODULE.ReviewError("synthetic collision")
        order.append(name)
        state[name] = payload

    monkeypatch.setattr(MODULE, "_read_at", read_at)
    monkeypatch.setattr(MODULE, "_publish_one_at", publish)
    return review, context, order, state


def _publish(monkeypatch: pytest.MonkeyPatch, initial: dict[str, bytes]):
    review, context, order, state = _publication_fixture(
        monkeypatch, initial=initial
    )
    result = MODULE.publish_and_verify(
        review,
        context,
        expected_reports_st_dev=20,
        expected_reports_st_ino=30,
        expected_reports_nlink=18,
    )
    return result, order, state, context


def test_publication_absent_pair_is_sidecar_first(monkeypatch: pytest.MonkeyPatch) -> None:
    result, order, _state, _context = _publish(monkeypatch, {})
    assert order == [MODULE.SIDECAR.name, MODULE.OUTPUT.name]
    assert result["publication"] == "PUBLISHED_EXACT_SIDECAR_THEN_JSON"
    assert result["status"] == (
        "PASS_ROOT_OWNED_IMMUTABLE_FILES_IN_MUTABLE_REPORTS_NAMESPACE"
    )
    assert result["inode_metadata_immutable"] is True
    assert result["namespace_immutable"] is False
    assert result["point_in_time_attestation"] is True
    assert result["mandatory_downstream_exact_revalidation"] is True
    assert result["publication_scope"] == MODULE.EXPECTED_PUBLICATION_SCOPE


def test_publication_exact_sidecar_only_resumes_main(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    review = {"review_payload_sha256": "a" * 64}
    main = MODULE._canonical_bytes(review)
    side = MODULE._sidecar_bytes(hashlib.sha256(main).hexdigest())
    result, order, _state, _context = _publish(
        monkeypatch, {MODULE.SIDECAR.name: side}
    )
    assert order == [MODULE.OUTPUT.name]
    assert result["publication"] == "RESUMED_EXACT_SIDECAR_ONLY"


def test_publication_main_only_rejects_without_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    review = {"review_payload_sha256": "a" * 64}
    main = MODULE._canonical_bytes(review)
    _review, context, order, state = _publication_fixture(
        monkeypatch, initial={MODULE.OUTPUT.name: main}
    )
    with pytest.raises(MODULE.ReviewError, match="without its sidecar"):
        MODULE.publish_and_verify(
            _review,
            context,
            expected_reports_st_dev=20,
            expected_reports_st_ino=30,
            expected_reports_nlink=18,
        )
    assert order == []
    assert state == {MODULE.OUTPUT.name: main}


def test_publication_exact_pair_is_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    review = {"review_payload_sha256": "a" * 64}
    main = MODULE._canonical_bytes(review)
    side = MODULE._sidecar_bytes(hashlib.sha256(main).hexdigest())
    result, order, _state, _context = _publish(
        monkeypatch,
        {MODULE.OUTPUT.name: main, MODULE.SIDECAR.name: side},
    )
    assert order == []
    assert result["publication"] == "IDEMPOTENT_EXACT_PAIR"


def test_publication_foreign_sidecar_rejects_before_link(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    review, context, order, state = _publication_fixture(
        monkeypatch, initial={MODULE.SIDECAR.name: b"foreign"}
    )
    with pytest.raises(MODULE.ReviewError, match="sidecar differs"):
        MODULE.publish_and_verify(
            review,
            context,
            expected_reports_st_dev=20,
            expected_reports_st_ino=30,
            expected_reports_nlink=18,
        )
    assert order == []
    assert state == {MODULE.SIDECAR.name: b"foreign"}


def test_publication_final_parent_swap_rejects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    review, context, _order, _state = _publication_fixture(monkeypatch, initial={})
    swapped = _status(
        inode=31,
        device=20,
        mode=stat.S_IFDIR | 0o775,
        uid=1000,
        gid=1000,
        nlink=18,
    )
    monkeypatch.setattr(MODULE.REPORTS.__class__, "lstat", lambda _self: swapped)
    with pytest.raises(MODULE.ReviewError, match="parent"):
        MODULE.publish_and_verify(
            review,
            context,
            expected_reports_st_dev=20,
            expected_reports_st_ino=30,
            expected_reports_nlink=18,
        )


def test_publish_requires_effective_root(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(MODULE.os, "geteuid", lambda: 1000)
    monkeypatch.setattr(MODULE.os, "getegid", lambda: 1000)
    with pytest.raises(MODULE.ReviewError, match="effective root"):
        MODULE.publish_and_verify(
            {},
            {},
            expected_reports_st_dev=20,
            expected_reports_st_ino=30,
            expected_reports_nlink=18,
        )


@pytest.mark.skipif(os.geteuid() != 0, reason="root-only O_TMPFILE publication")
def test_publish_one_at_creates_root_immutable_nlink1(tmp_path: Path) -> None:
    descriptor = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        MODULE._publish_one_at(descriptor, "fixture.json", b"{}\n")
    finally:
        os.close(descriptor)
    status = (tmp_path / "fixture.json").lstat()
    assert status.st_uid == 0 and status.st_gid == 0
    assert stat.S_IMODE(status.st_mode) == 0o444 and status.st_nlink == 1
