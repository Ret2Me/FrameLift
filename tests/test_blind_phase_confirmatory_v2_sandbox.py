from __future__ import annotations

import copy
import importlib.util
import os
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "work/blind-phase-confirmatory-v2/sandbox_contract.py"


def _module():
    name = "blind_phase_confirmatory_v2_sandbox_test_module"
    spec = importlib.util.spec_from_file_location(name, SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _file(path: Path, payload: bytes = b"x") -> Path:
    path.write_bytes(payload)
    return path


def test_bwrap_argv_has_no_network_and_only_explicit_scientific_inputs(
    tmp_path: Path,
) -> None:
    sandbox = _module()
    scratch = tmp_path / "scratch"
    output = tmp_path / "output"
    runtime = tmp_path / "runtime"
    scratch.mkdir()
    output.mkdir()
    scratch.chmod(0o700)
    output.chmod(0o700)
    runtime.mkdir()
    argv = sandbox.build_bwrap_argv(
        iq_path=_file(tmp_path / "capture.ci16", b"\x00" * 4),
        plan_path=_file(tmp_path / "plan.json"),
        acquisition_manifest_path=_file(tmp_path / "acquisition.json"),
        readonly_runtime_binds={"/runtime/candidate": runtime},
        scratch_directory=scratch,
        output_directory=output,
        command=("/runtime/candidate/bin/python", "-I", "/runtime/runner.py"),
    )
    assert "--unshare-all" in argv
    assert "--share-net" not in argv
    assert ("--ro-bind", "/", "/") not in tuple(zip(argv, argv[1:], strict=False))
    assert "/home/ubuntu" not in argv
    assert argv.count("--bind") == 2
    assert argv.count("--ro-bind") == 5
    assert argv[:14] == (
        "/usr/bin/sudo",
        "-n",
        "/usr/bin/setpriv",
        "--ruid",
        "1000",
        "--euid",
        "0",
        "--rgid",
        "1000",
        "--egid",
        "1000",
        "--clear-groups",
        "--",
        "/usr/bin/bwrap",
    )
    assert argv[-4:] == (
        "--",
        "/runtime/candidate/bin/python",
        "-I",
        "/runtime/runner.py",
    )


def test_bwrap_argv_rejects_broad_runtime_bind_and_shared_writable_state(
    tmp_path: Path,
) -> None:
    sandbox = _module()
    capture = _file(tmp_path / "capture.ci16", b"\x00" * 4)
    plan = _file(tmp_path / "plan.json")
    manifest = _file(tmp_path / "acquisition.json")
    writable = tmp_path / "writable"
    output = tmp_path / "output"
    writable.mkdir()
    output.mkdir()
    writable.chmod(0o700)
    output.chmod(0o700)
    with pytest.raises(ValueError, match="broad host bind"):
        sandbox.build_bwrap_argv(
            iq_path=capture,
            plan_path=plan,
            acquisition_manifest_path=manifest,
            readonly_runtime_binds={"/runtime/host": Path("/home/ubuntu")},
            scratch_directory=writable,
            output_directory=output,
            command=("/runtime/host/python",),
        )
    with pytest.raises(ValueError, match="must be disjoint"):
        sandbox.build_bwrap_argv(
            iq_path=capture,
            plan_path=plan,
            acquisition_manifest_path=manifest,
            readonly_runtime_binds={"/runtime/candidate": tmp_path},
            scratch_directory=writable,
            output_directory=writable,
            command=("/runtime/candidate/python",),
        )


def test_bwrap_rejects_symlinked_bind_ancestry_and_runtime_path_escape(
    tmp_path: Path,
) -> None:
    sandbox = _module()
    real = tmp_path / "real"
    real.mkdir()
    linked = tmp_path / "linked"
    linked.symlink_to(real, target_is_directory=True)
    plan = _file(tmp_path / "plan.json")
    manifest = _file(tmp_path / "acquisition.json")
    scratch = tmp_path / "scratch"
    output = tmp_path / "output"
    scratch.mkdir()
    output.mkdir()
    scratch.chmod(0o700)
    output.chmod(0o700)
    with pytest.raises(ValueError, match="symlink component"):
        sandbox.build_bwrap_argv(
            iq_path=_file(real / "capture.ci16", b"\x00" * 4),
            plan_path=plan,
            acquisition_manifest_path=manifest,
            readonly_runtime_binds={"/runtime/candidate": linked},
            scratch_directory=scratch,
            output_directory=output,
            command=("/runtime/candidate/bin/python",),
        )
    with pytest.raises(ValueError, match="exact bound runtime"):
        sandbox.build_bwrap_argv(
            iq_path=real / "capture.ci16",
            plan_path=plan,
            acquisition_manifest_path=manifest,
            readonly_runtime_binds={"/runtime/candidate": real},
            scratch_directory=scratch,
            output_directory=output,
            command=("/runtime/candidate/../usr/bin/python3",),
        )


def test_bwrap_rejects_writable_ancestor_alias_nonempty_and_runtime_overlap(
    tmp_path: Path,
) -> None:
    sandbox = _module()
    readonly_root = tmp_path / "readonly"
    readonly_root.mkdir()
    capture = _file(readonly_root / "capture.ci16", b"\x00" * 4)
    plan = _file(readonly_root / "plan.json")
    manifest = _file(readonly_root / "acquisition.json")
    runtime = readonly_root / "runtime"
    runtime.mkdir()
    runtime.chmod(0o700)
    scratch = tmp_path / "scratch"
    scratch.mkdir(mode=0o700)
    scratch.chmod(0o700)
    readonly_root.chmod(0o700)
    with pytest.raises(ValueError, match="overlaps or aliases"):
        sandbox.build_bwrap_argv(
            iq_path=capture,
            plan_path=plan,
            acquisition_manifest_path=manifest,
            readonly_runtime_binds={"/runtime/candidate": runtime},
            scratch_directory=scratch,
            output_directory=runtime,
            command=("/runtime/candidate/bin/python",),
        )

    output = tmp_path / "output"
    output.mkdir(mode=0o700)
    output.chmod(0o700)
    (output / "stale").write_bytes(b"x")
    with pytest.raises(ValueError, match="fresh and empty"):
        sandbox.build_bwrap_argv(
            iq_path=capture,
            plan_path=plan,
            acquisition_manifest_path=manifest,
            readonly_runtime_binds={"/runtime/candidate": runtime},
            scratch_directory=scratch,
            output_directory=output,
            command=("/runtime/candidate/bin/python",),
        )

    output.joinpath("stale").unlink()
    with pytest.raises(ValueError, match="destinations must not overlap"):
        sandbox.build_bwrap_argv(
            iq_path=capture,
            plan_path=plan,
            acquisition_manifest_path=manifest,
            readonly_runtime_binds={
                "/runtime/candidate": runtime,
                "/runtime/candidate/sub": readonly_root,
            },
            scratch_directory=scratch,
            output_directory=output,
            command=("/runtime/candidate/bin/python",),
        )


def test_real_bwrap_preflight_maps_host_owner_and_writes_output() -> None:
    sandbox = _module()
    contract = sandbox.run_synthetic_preflight()
    assert sandbox.SANDBOX_CONTRACT_VERSION == "blind-phase-bwrap-firewall-v2"
    assert contract["status"] == "PASS"
    assert contract["outer_bwrap_real_uid"] == os.getuid() == 1000
    assert contract["host_bind_uid"] == contract["inner_uid"] == 1000
    assert contract["host_bind_gid"] == contract["inner_gid"] == 1000
    assert contract["host_bind_identity_mapped_to_inner_identity"] is True
    assert contract["host_root_uid_mapped_into_receiver_namespace"] is False
    assert contract["inner_capability_sets"] == {
        "CapInh": "0000000000000000",
        "CapPrm": "0000000000000000",
        "CapEff": "0000000000000000",
        "CapBnd": "0000000000000000",
        "CapAmb": "0000000000000000",
    }
    sandbox.validate_live_contract(contract)


def test_live_contract_and_writable_owner_drift_fail_closed(tmp_path: Path) -> None:
    sandbox = _module()
    contract = sandbox.run_synthetic_preflight()
    tampered = copy.deepcopy(contract)
    tampered["host_root_uid_mapped_into_receiver_namespace"] = True
    with pytest.raises(ValueError, match="payload hash mismatch"):
        sandbox.validate_live_contract(tampered)

    tampered["contract_sha256"] = sandbox.hashlib.sha256(
        sandbox.json.dumps(
            {
                key: value
                for key, value in tampered.items()
                if key != "contract_sha256"
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    with pytest.raises(ValueError, match="identity contract drift"):
        sandbox.validate_live_contract(tampered)

    tampered = copy.deepcopy(contract)
    tampered["inner_capability_sets"]["CapBnd"] = "00000000a80425fb"
    tampered["contract_sha256"] = sandbox.hashlib.sha256(
        sandbox.json.dumps(
            {
                key: value
                for key, value in tampered.items()
                if key != "contract_sha256"
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    with pytest.raises(ValueError, match="identity contract drift"):
        sandbox.validate_live_contract(tampered)

    semantic_drifts = (
        ("bubblewrap-version", lambda value: value["bubblewrap"].__setitem__("version", "bwrap forged"), "version drift"),
        ("canary-stdout", lambda value: value.__setitem__("canary_stdout_sha256", "0" * 64), "stdout attestation drift"),
        ("forbidden-probe", lambda value: value.__setitem__("forbidden_probe_absent", "/tmp/not-the-frozen-probe"), "forbidden-probe attestation drift"),
        ("additive-field", lambda value: value.__setitem__("unreviewed", True), "field set drift"),
    )
    for _, mutate, message in semantic_drifts:
        tampered = copy.deepcopy(contract)
        mutate(tampered)
        tampered["contract_sha256"] = sandbox.hashlib.sha256(
            sandbox.json.dumps(
                {
                    key: value
                    for key, value in tampered.items()
                    if key != "contract_sha256"
                },
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
        with pytest.raises(ValueError, match=message):
            sandbox.validate_live_contract(tampered)

    scratch = tmp_path / "scratch"
    output = tmp_path / "output"
    runtime = tmp_path / "runtime"
    scratch.mkdir(mode=0o700)
    output.mkdir(mode=0o700)
    runtime.mkdir()
    output.chmod(0o750)
    with pytest.raises(ValueError, match="owner-1000:1000 mode-0700"):
        sandbox.build_bwrap_argv(
            iq_path=_file(tmp_path / "capture.ci16", b"\x00" * 4),
            plan_path=_file(tmp_path / "plan.json"),
            acquisition_manifest_path=_file(tmp_path / "acquisition.json"),
            readonly_runtime_binds={"/runtime/candidate": runtime},
            scratch_directory=scratch,
            output_directory=output,
            command=("/runtime/candidate/bin/python",),
        )


def test_real_bwrap_launcher_path_writes_both_private_binds(tmp_path: Path) -> None:
    sandbox = _module()
    contract = sandbox.run_synthetic_preflight()
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    entrypoint = runtime / "entrypoint"
    entrypoint.write_text(
        "#!/bin/sh\nset -eu\nprintf scratch >/scratch/probe\nprintf output >/output/result\n",
        encoding="utf-8",
    )
    entrypoint.chmod(0o555)
    scratch = tmp_path / "scratch"
    output = tmp_path / "output"
    scratch.mkdir(mode=0o700)
    output.mkdir(mode=0o700)
    argv = sandbox.build_bwrap_argv(
        iq_path=_file(tmp_path / "capture.ci16", b"\x00" * 4),
        plan_path=_file(tmp_path / "plan.json"),
        acquisition_manifest_path=_file(tmp_path / "acquisition.json"),
        readonly_runtime_binds={"/runtime/test": runtime},
        scratch_directory=scratch,
        output_directory=output,
        command=("/runtime/test/entrypoint",),
        expected_contract=contract,
    )
    completed = subprocess.run(
        argv,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=30,
        check=False,
        env={"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C"},
    )
    assert completed.returncode == 0, completed.stderr.decode(errors="replace")
    assert (scratch / "probe").read_bytes() == b"scratch"
    assert (output / "result").read_bytes() == b"output"
