from __future__ import annotations

import copy
from datetime import datetime, timezone
import hashlib
import importlib.util
import inspect
import json
import math
import os
from pathlib import Path
import sys
import time
from types import SimpleNamespace

import numpy as np
import pytest


ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / "work/positive-real-iq"
PROTOCOL = ROOT / "configs/real-iq-legal-transfer-r5-v5-preregistration-draft.json"
TEMPLATE = ROOT / "configs/real-iq-legal-transfer-r5-v5-review-template.json"


def load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


GENERATOR = load(WORK / "make_legal_transfer_r5_v5_null.py", "test_r5_v5_generator")
AUDITOR = load(WORK / "audit_legal_transfer_r5_v5.py", "test_r5_v5_auditor")
REVIEW = load(WORK / "r5_v5_review.py", "test_r5_v5_review")
RUNNER = load(WORK / "run_legal_transfer_r5_v5.py", "test_r5_v5_runner")
INTEGRITY = load(WORK / "r5_v5_runtime_integrity.py", "test_r5_v5_integrity")
BUILDER = load(WORK / "build_legal_transfer_r5_v5_runtime.py", "test_r5_v5_builder")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def synthetic_ci16(path: Path, samples: int = 521) -> np.ndarray:
    index = np.arange(samples, dtype=np.float64)
    amplitude = 3200.0 + 700.0 * np.sin(2 * np.pi * index / 71.0)
    phase = 0.021 * index + 0.7 * np.sin(2 * np.pi * index / 43.0)
    signal = amplitude * np.exp(1j * phase)
    raw = np.empty((samples, 2), dtype="<i2")
    raw[:, 0] = np.rint(signal.real).astype(np.int16)
    raw[:, 1] = np.rint(signal.imag).astype(np.int16)
    path.write_bytes(raw.tobytes())
    return raw


def generate(tmp_path: Path, transform: str, suffix: str = "") -> tuple[np.ndarray, np.ndarray, dict[str, object], Path]:
    source = tmp_path / "synthetic.raw"
    original = synthetic_ci16(source)
    output = tmp_path / f"{transform}{suffix}.raw"
    record = GENERATOR.transform_ci16(
        source,
        output,
        observation_id=1245,
        transform=transform,
        expected_source_size_bytes=source.stat().st_size,
        expected_source_sha256=sha256_file(source),
        block_complex_samples=64,
    )
    transformed = np.frombuffer(output.read_bytes(), dtype="<i2").reshape(-1, 2)
    return original, transformed, record, source


def test_protocol_is_exactly_three_outcome_blind_informational_followup() -> None:
    protocol = json.loads(PROTOCOL.read_text(encoding="utf-8"))
    assert protocol["status"] == "draft_pending_independent_review_not_frozen"
    assert protocol["transforms"] == list(GENERATOR.TRANSFORMS)
    assert len(protocol["transforms"]) == 3
    assert "marginal R5-v4 sensitivity failure" in protocol["motivation"]["trigger"]
    assert protocol["execution_policy"]["all_transforms_run_regardless_of_results"] is True
    assert protocol["execution_policy"]["decoder_outputs_not_parsed_by_runner"] is True
    assert protocol["execution_policy"]["core_decoder_changes_permitted"] is False
    assert protocol["execution_policy"]["prospective_iq_access_before_review_and_freeze_permitted"] is False
    assert protocol["reporting"]["natural_negative_signal_hours_claimed"] is False
    assert protocol["transform_contract"]["seed_fields_exact"] == ["observation_id", "transform"]
    post81 = protocol["post_81_null_validity"]
    assert post81["timing"] == "evaluated independently only after all 81 frozen pairs complete"
    assert post81["positive_classification"] == "LEAKAGE_OR_FALSE_ACCEPT"
    assert post81["circular-q-delay"]["far_eligible_in_principle"] is False
    assert post81["failed_transform_excluded_from_qualified_far"] is True


def test_seed_is_stable_and_uses_only_observation_id_plus_transform() -> None:
    seeds = {transform: GENERATOR.seed_for(77, transform) for transform in GENERATOR.TRANSFORMS}
    assert len(set(seeds.values())) == 3
    assert all(0 <= value < 2**64 for value in seeds.values())
    assert seeds == {transform: AUDITOR.independent_seed(77, transform) for transform in GENERATOR.TRANSFORMS}
    assert GENERATOR.seed_for(78, GENERATOR.TRANSFORMS[0]) != seeds[GENERATOR.TRANSFORMS[0]]
    assert set(inspect.signature(GENERATOR.seed_for).parameters) == {"observation_id", "transform"}


@pytest.mark.parametrize("transform", GENERATOR.TRANSFORMS)
def test_all_transforms_are_deterministic_nonzero_distinct_and_independently_regenerated(
    tmp_path: Path, transform: str
) -> None:
    original, transformed, first, source = generate(tmp_path, transform, "-a")
    second_output = tmp_path / f"{transform}-b.raw"
    second = GENERATOR.transform_ci16(
        source,
        second_output,
        observation_id=1245,
        transform=transform,
        expected_source_size_bytes=source.stat().st_size,
        expected_source_sha256=sha256_file(source),
        block_complex_samples=64,
    )
    regenerated, invariants = AUDITOR.independent_regenerated_hash(
        source, 1245, transform, block_complex_samples=64
    )
    assert first["output_sha256"] == second["output_sha256"] == regenerated
    assert first["output_sha256"] != first["source_sha256"]
    assert first["seed_contract"]["fields"] == ["observation_id", "transform"]
    assert first["output_size_bytes"] == first["source_size_bytes"] == original.nbytes
    assert np.count_nonzero(transformed) > 0
    assert first["block_count"] == math.ceil(len(original) / 64)
    assert first["peak_explicit_array_bytes"] < 64 * 1024
    assert first["peak_working_bytes_upper_bound"] <= 2 * 1024 * 1024
    assert invariants["input_nonzero"] and invariants["output_nonzero"]


def test_fft_random_phase_preserves_local_spectral_power_before_deterministic_gain(tmp_path: Path) -> None:
    original, transformed, record, source = generate(tmp_path, "block-fft-random-phase")
    _, invariants = AUDITOR.independent_regenerated_hash(
        source, 1245, "block-fft-random-phase", block_complex_samples=64
    )
    assert invariants["fft_or_envelope_power_relative_error_before_gain"] <= 1e-12
    assert invariants["fft_magnitude_relative_error_max_before_gain"] <= 1e-12
    assert record["fft_or_envelope_power_relative_error_before_gain"] <= 1e-12
    assert record["fft_magnitude_relative_error_max_before_gain"] <= 1e-12
    assert 0 < record["anti_clipping"]["minimum_block_gain"] <= 1.0
    assert np.max(np.abs(transformed.astype(np.int32))) <= 32766
    original_complex = original[:64, 0].astype(float) + 1j * original[:64, 1].astype(float)
    transformed_complex = transformed[:64, 0].astype(float) + 1j * transformed[:64, 1].astype(float)
    gain = record["anti_clipping"]["minimum_block_gain"]
    assert np.allclose(np.abs(np.fft.fft(original_complex)), np.abs(np.fft.fft(transformed_complex / gain)), rtol=0.01, atol=20)


def test_circular_q_delay_preserves_exact_i_sequence_and_q_marginal(tmp_path: Path) -> None:
    original, transformed, record, source = generate(tmp_path, "circular-q-delay")
    assert 1 <= record["circular_q_delay_complex_samples"] < len(original)
    assert np.array_equal(transformed[:, 0], original[:, 0])
    assert np.array_equal(np.sort(transformed[:, 1]), np.sort(original[:, 1]))
    _, invariants = AUDITOR.independent_regenerated_hash(
        source, 1245, "circular-q-delay", block_complex_samples=64
    )
    assert invariants["circular_i_sequence_exact"] is True
    assert invariants["circular_q_marginal_exact"] is True
    assert invariants["circular_distance_fraction"] >= 0.125


def test_phase_increment_permutation_preserves_envelope_and_increment_histogram_before_rounding(
    tmp_path: Path,
) -> None:
    original, transformed, record, source = generate(tmp_path, "block-phase-increment-permutation")
    _, invariants = AUDITOR.independent_regenerated_hash(
        source, 1245, "block-phase-increment-permutation", block_complex_samples=64
    )
    assert invariants["phase_increment_permutations_exact"] is True
    assert invariants["phase_increment_count"] == record["permuted_within_block_phase_increment_count"]
    assert invariants["amplitude_envelope_max_abs_error_before_gain"] <= 1e-9
    original_amplitude = np.hypot(original[:, 0].astype(float), original[:, 1].astype(float))
    output_amplitude = np.hypot(transformed[:, 0].astype(float), transformed[:, 1].astype(float))
    assert np.max(np.abs(original_amplitude - output_amplitude)) < 1.0


def test_generator_is_bounded_no_clobber_and_content_bound(tmp_path: Path) -> None:
    _, _, record, source = generate(tmp_path, "block-fft-random-phase")
    assert record["block_complex_samples"] == 64
    with pytest.raises(RuntimeError, match="new path"):
        GENERATOR.transform_ci16(
            source,
            Path(record["output_path"]),
            observation_id=1245,
            transform="block-fft-random-phase",
            expected_source_size_bytes=source.stat().st_size,
            expected_source_sha256=sha256_file(source),
            block_complex_samples=64,
        )
    with pytest.raises(RuntimeError, match="SHA-256 mismatch"):
        GENERATOR.transform_ci16(
            source,
            tmp_path / "wrong.raw",
            observation_id=1245,
            transform="block-fft-random-phase",
            expected_source_size_bytes=source.stat().st_size,
            expected_source_sha256="0" * 64,
            block_complex_samples=64,
        )


def test_review_template_cannot_unlock_and_exact_approval_is_byte_bound(tmp_path: Path) -> None:
    pending = REVIEW.pending_review_document(ROOT)
    assert pending["decision"] == "pending"
    assert set(pending["implementation_sha256"]) == set(REVIEW.REVIEWED_IMPLEMENTATION_RELATIVE_PATHS)
    assert all(value is False for value in pending["attestations"].values())
    with pytest.raises(RuntimeError, match="not been approved"):
        REVIEW.validate_review_attestation(
            root=ROOT,
            attestation_path=TEMPLATE,
            expected_attestation_sha256=sha256_file(TEMPLATE),
            protocol_path=PROTOCOL,
            implementation_paths=(PROTOCOL,),
        )
    reviewed_paths = (PROTOCOL, WORK / "make_legal_transfer_r5_v5_null.py")
    document = {
        "schema_version": REVIEW.REVIEW_SCHEMA,
        "decision": "approve_freeze",
        "reviewed_at_utc": datetime.now(timezone.utc).isoformat(),
        "reviewer": {
            "name": "Independent Test Reviewer",
            "affiliation": "test-only",
            "independence_statement": "I did not author the implementation under this test review.",
        },
        "protocol": {"path": str(PROTOCOL.relative_to(ROOT)), "sha256": sha256_file(PROTOCOL)},
        "review_scope": sorted(REVIEW.REQUIRED_SCOPE),
        "attestations": {name: True for name in REVIEW.REQUIRED_ATTESTATIONS},
        "implementation_sha256": {
            str(path.relative_to(ROOT)): sha256_file(path) for path in reviewed_paths
        },
    }
    approval = tmp_path / "approval.json"
    approval.write_text(json.dumps(document, sort_keys=True), encoding="utf-8")
    loaded = REVIEW.validate_review_attestation(
        root=ROOT,
        attestation_path=approval,
        expected_attestation_sha256=sha256_file(approval),
        protocol_path=PROTOCOL,
        implementation_paths=reviewed_paths,
    )
    assert loaded["decision"] == "approve_freeze"
    document["implementation_sha256"][str(PROTOCOL.relative_to(ROOT))] = "0" * 64
    approval.write_text(json.dumps(document, sort_keys=True), encoding="utf-8")
    with pytest.raises(RuntimeError, match="reviewed protocol bytes changed|reviewed implementation changed"):
        REVIEW.validate_review_attestation(
            root=ROOT,
            attestation_path=approval,
            expected_attestation_sha256=sha256_file(approval),
            protocol_path=PROTOCOL,
            implementation_paths=reviewed_paths,
        )


def test_review_hash_closure_includes_dynamic_and_transitive_scripts() -> None:
    required = {
        "reports/real-iq-legal-transfer-r5-runtime-manifest-v4.json",
        "work/positive-real-iq/build_legal_transfer_r5_v3_runtime.py",
        "work/positive-real-iq/run_legal_transfer_r5_v3.py",
        "work/positive-real-iq/audit_legal_transfer_r5_v2.py",
        "work/positive-real-iq/decode_legal_fsk_transfer_rate_v2.py",
        "work/positive-real-iq/decode_legal_fsk_transfer_rate_v3.py",
        "work/positive-real-iq/decode_legal_fsk_transfer.py",
        "work/positive-real-iq/make_legal_transfer_r5_v3_null.py",
        "work/positive-real-iq/make_legal_transfer_r5_v2_null.py",
    }
    assert required <= set(REVIEW.REVIEWED_IMPLEMENTATION_RELATIVE_PATHS)
    pending = REVIEW.pending_review_document(ROOT)
    assert set(pending["implementation_sha256"]) == set(REVIEW.REVIEWED_IMPLEMENTATION_RELATIVE_PATHS)
    assert required <= set(pending["implementation_sha256"])


def test_auditor_rejects_replaced_v4_runtime_manifest_even_if_one_pin_is_updated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reports = tmp_path / "reports"
    reports.mkdir()
    predecessor = reports / "runtime-v4.json"
    predecessor.write_text('{"generation":"original"}\n', encoding="utf-8")
    original_sha = sha256_file(predecessor)
    relative = str(predecessor.relative_to(tmp_path))
    plan = {
        "locked_live_artifacts": {relative: original_sha},
        "predecessor": {"r5_v4_runtime_manifest_sha256": original_sha},
    }
    successor = {"construction": {"predecessor_runtime_manifest_sha256": original_sha}}
    monkeypatch.setattr(AUDITOR, "ROOT", tmp_path)
    monkeypatch.setattr(AUDITOR, "V4_RUNTIME_MANIFEST", predecessor)
    assert AUDITOR.load_bound_predecessor_runtime_manifest(plan, successor)["generation"] == "original"

    predecessor.write_text('{"generation":"replacement"}\n', encoding="utf-8")
    replacement_sha = sha256_file(predecessor)
    with pytest.raises(RuntimeError, match="frozen plan predecessor-runtime manifest binding mismatch"):
        AUDITOR.load_bound_predecessor_runtime_manifest(plan, successor)

    plan["locked_live_artifacts"][relative] = replacement_sha
    plan["predecessor"]["r5_v4_runtime_manifest_sha256"] = replacement_sha
    with pytest.raises(RuntimeError, match="successor construction predecessor-runtime manifest binding mismatch"):
        AUDITOR.load_bound_predecessor_runtime_manifest(plan, successor)


def test_exact_runtime_closure_rejects_additive_mode_symlink_external_and_elf_drift(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime"
    tools = runtime / "tools"
    tools.mkdir(parents=True)
    first = tools / "first.py"
    second = tools / "second.py"
    first.write_text("first\n", encoding="utf-8")
    second.write_text("second\n", encoding="utf-8")
    link = runtime / "entry"
    link.symlink_to("tools/first.py")
    document = INTEGRITY.snapshot_runtime(runtime)
    document["runtime_root"] = str(runtime)
    assert INTEGRITY.verify_runtime_document(document, expected_root=runtime)["path_set_exact"] is True

    added = runtime / "added"
    added.write_text("drift\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="path-set drift"):
        INTEGRITY.verify_runtime_document(document, expected_root=runtime)
    added.unlink()

    first.chmod(0o700)
    with pytest.raises(RuntimeError, match="mode drift"):
        INTEGRITY.verify_runtime_document(document, expected_root=runtime)
    first.chmod(next(row["mode"] for row in document["files"] if row["path"] == "tools/first.py"))

    link.unlink()
    link.symlink_to("tools/second.py")
    with pytest.raises(RuntimeError, match="symlink target drift"):
        INTEGRITY.verify_runtime_document(document, expected_root=runtime)
    link.unlink()
    link.symlink_to("tools/first.py")

    external_drift = copy.deepcopy(document)
    external_drift["external_runtime_files"].append(
        {"path": "/not/declared/live", "size_bytes": 0, "mode": 0, "sha256": "0" * 64}
    )
    with pytest.raises(RuntimeError, match="external/ELF dependency path-set drift"):
        INTEGRITY.verify_runtime_document(external_drift, expected_root=runtime)
    elf_drift = copy.deepcopy(document)
    elf_drift["elf_paths"].append("tools/not-live-elf")
    with pytest.raises(RuntimeError, match="ELF path-set drift"):
        INTEGRITY.verify_runtime_document(elf_drift, expected_root=runtime)


def test_real_legacy_v4_relocation_closure_is_fail_closed_and_buildable() -> None:
    manifest = json.loads(BUILDER.V4_MANIFEST.read_text(encoding="utf-8"))
    BUILDER._validate_predecessor(manifest, INTEGRITY)
    mutated = copy.deepcopy(manifest)
    mutated["external_runtime_files"][0]["sha256"] = "0" * 64
    with pytest.raises(RuntimeError, match="external dependency drift"):
        BUILDER._validate_predecessor(mutated, INTEGRITY)


@pytest.mark.parametrize(
    ("program", "expected_failure"),
    [
        ("import sys;sys.stdout.write('x'*129)", "stdout_limit"),
        ("import sys;sys.stderr.write('x'*129)", "stderr_limit"),
        ("import time;time.sleep(2)", "timeout"),
    ],
)
def test_bounded_process_stops_noisy_or_hung_children(
    tmp_path: Path, program: str, expected_failure: str
) -> None:
    result = INTEGRITY.run_bounded_process(
        [sys.executable, "-c", program],
        cwd=tmp_path,
        environment=dict(os.environ),
        timeout_seconds=0.25,
        stdout_limit_bytes=64,
        stderr_limit_bytes=64,
    )
    assert result.failure_code == expected_failure
    assert len(result.stdout) <= 64 and len(result.stderr) <= 64


def test_bounded_process_stops_oversized_artifact(tmp_path: Path) -> None:
    artifact = tmp_path / "artifact.bin"
    program = f"from pathlib import Path;Path({str(artifact)!r}).write_bytes(b'x'*4096)"
    result = INTEGRITY.run_bounded_process(
        [sys.executable, "-c", program],
        cwd=tmp_path,
        environment=dict(os.environ),
        timeout_seconds=2.0,
        stdout_limit_bytes=64,
        stderr_limit_bytes=64,
        artifact_limits={artifact: 32},
    )
    assert result.failure_code is not None and result.failure_code.startswith("artifact_limit:")


def test_bounded_process_keeps_polling_artifact_after_both_pipe_eofs(tmp_path: Path) -> None:
    artifact = tmp_path / "after-eof.bin"
    program = (
        "import sys;"
        "sys.stdout.close();sys.stderr.close();"
        f"open({str(artifact)!r},'wb').write(b'x'*4096)"
    )
    result = INTEGRITY.run_bounded_process(
        [sys.executable, "-c", program],
        cwd=tmp_path,
        environment=dict(os.environ),
        timeout_seconds=2.0,
        stdout_limit_bytes=64,
        stderr_limit_bytes=64,
        artifact_limits={artifact: 32},
    )
    assert result.failure_code is not None and result.failure_code.startswith("artifact_limit:")
    assert result.process_group_cleanup_complete is True


def test_bounded_process_waits_for_and_kills_same_group_descendant_after_pipe_eofs(tmp_path: Path) -> None:
    marker = tmp_path / "descendant-survived"
    descendant = f"import time;time.sleep(0.6);open({str(marker)!r},'wb').write(b'alive')"
    leader = (
        "import subprocess,sys;"
        "sys.stdout.close();sys.stderr.close();"
        f"subprocess.Popen([sys.executable,'-c',{descendant!r}],"
        "stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,close_fds=True)"
    )
    result = INTEGRITY.run_bounded_process(
        [sys.executable, "-c", leader],
        cwd=tmp_path,
        environment=dict(os.environ),
        timeout_seconds=0.2,
        stdout_limit_bytes=64,
        stderr_limit_bytes=64,
    )
    assert result.failure_code == "timeout"
    assert result.process_group_cleanup_complete is True
    time.sleep(0.7)
    assert not marker.exists()


def test_failure_receipt_removes_partial_and_preserves_same_pair_resume(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(RUNNER, "ROOT", tmp_path)
    monkeypatch.setattr(RUNNER, "RUN_ROOT", tmp_path / "run")
    partial = RUNNER.RUN_ROOT / "obs-7" / "partial.bin"
    partial.parent.mkdir(parents=True)
    partial.write_bytes(b"partial")

    class Base:
        sha256_file = staticmethod(sha256_file)
        sha256_bytes = staticmethod(lambda value: hashlib.sha256(value).hexdigest())

        @staticmethod
        def durable_new(path: Path, value: object) -> None:
            path.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")

    result = SimpleNamespace(
        returncode=None, failure_code="timeout", elapsed_seconds=0.25,
        timeout_seconds=0.25, stdout_limit_bytes=64, stdout=b"",
        stderr_limit_bytes=64, stderr=b"", process_group_cleanup_complete=True,
    )
    receipt = RUNNER._record_operational_failure(
        Base(), row={"observation_id": 7}, transform=GENERATOR.TRANSFORMS[0],
        schedule_index=1, stage="receiver_process", processes={"native": (["decoder"], result)},
        partial_paths=(partial,),
    )
    record = json.loads(receipt.read_text(encoding="utf-8"))
    assert not partial.exists()
    assert record["scientific_result_interpreted"] is False
    assert record["retry_same_frozen_schedule_pair"] is True
    assert record["processes"]["native"]["failure_code"] == "timeout"


def test_strict_parsers_reject_record_and_native_frame_caps(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    kiss = tmp_path / "many.kiss"
    kiss.write_bytes(b"\xc0\x00A\xc0\x00B\xc0")
    monkeypatch.setitem(AUDITOR.PROCESS_BOUNDS, "strict_parser_max_records_per_artifact", 1)
    with pytest.raises(RuntimeError, match="record cap exceeded"):
        AUDITOR.baseline_strict_bounded(kiss)

    native = tmp_path / "native.json"
    native.write_text(json.dumps({
        "input_sha256": "a" * 64,
        "plan": {"sample_rate_hz": 1},
        "reference_paths_available_to_decode": False,
        "reference_hashes_available_to_decode": False,
        "event_timestamps_available_to_decode": False,
        "unique_crc_valid_frames": [{"frame_with_fcs_hex": "00" * 9}],
    }), encoding="utf-8")
    monkeypatch.setitem(AUDITOR.PROCESS_BOUNDS, "strict_parser_max_frame_bytes", 8)
    with pytest.raises(RuntimeError, match="native frame cap exceeded"):
        AUDITOR.native_strict_bounded(native, "a" * 64, 1)


def test_post_81_null_qualification_is_whole_transform_and_q_delay_never_far() -> None:
    protocol = json.loads(PROTOCOL.read_text(encoding="utf-8"))
    invariants = {
        "block-fft-random-phase": [{
            "normalized_source_output_complex_correlation": 0.01,
            "fft_magnitude_relative_error_max_before_gain": 0.0,
            "fft_or_envelope_power_relative_error_before_gain": 0.0,
        } for _ in range(27)],
        "circular-q-delay": [{
            "centered_source_delayed_q_correlation": 0.01,
            "circular_distance_fraction": 0.125,
            "circular_i_sequence_exact": True,
            "circular_q_marginal_exact": True,
        } for _ in range(27)],
        "block-phase-increment-permutation": [{
            "normalized_source_output_complex_correlation": 0.01,
            "phase_increment_count": 100,
            "phase_increment_fixed_index_fraction": 0.01,
            "phase_increment_mean_absolute_displacement_fraction": 0.3,
        } for _ in range(27)],
    }
    passed = AUDITOR.assess_post_81_null_validity(invariants, protocol["post_81_null_validity"])
    assert passed["block-fft-random-phase"]["qualified_for_far"] is True
    assert passed["block-phase-increment-permutation"]["qualified_for_far"] is True
    assert passed["circular-q-delay"]["diagnostic_pass"] is True
    assert passed["circular-q-delay"]["qualified_for_far"] is False

    failed = copy.deepcopy(invariants)
    failed["block-fft-random-phase"][3]["normalized_source_output_complex_correlation"] = 0.11
    assessed = AUDITOR.assess_post_81_null_validity(failed, protocol["post_81_null_validity"])
    assert assessed["block-fft-random-phase"]["qualified_for_far"] is False
    assert assessed["block-phase-increment-permutation"]["qualified_for_far"] is True


def test_builder_freezer_runner_all_have_review_gates_and_no_partial_campaign_cli() -> None:
    builder = (WORK / "build_legal_transfer_r5_v5_runtime.py").read_text(encoding="utf-8")
    freezer = (WORK / "freeze_legal_transfer_r5_v5_plan.py").read_text(encoding="utf-8")
    runner = (WORK / "run_legal_transfer_r5_v5.py").read_text(encoding="utf-8")
    for source in (builder, freezer, runner):
        assert "--review-attestation" in source
        assert "--expected-review-sha256" in source
        assert "validate_review_attestation" in source
    assert 'parser.add_argument("transform"' not in runner
    assert 'runner_read_decoder_outcomes": False' in runner
    assert "for transform in TRANSFORMS" in runner
    assert builder.index("validate_review_attestation") < builder.index("shutil.copytree")
    assert freezer.index("validate_review_attestation") < freezer.index("OUTPUT.open")
    assert "source.stat()" not in freezer and "sha256_file(source)" not in freezer
    assert '"freezer_reads_iq_files": False' in freezer
    assert "submit_tlm\", \"no\"" in builder
    auditor = (WORK / "audit_legal_transfer_r5_v5.py").read_text(encoding="utf-8")
    assert '"core_decoder_unchanged"] = True' in auditor
    assert 'allowed_changed = "home/.gr_satellites/config.ini"' in auditor


def test_runner_commands_bind_review_runtime_and_same_ephemeral_path() -> None:
    plan = {
        "runtime_snapshot": {
            "manifest_path": "reports/manifest-v5.json",
            "manifest_sha256": "a" * 64,
        }
    }
    row = {
        "observation_id": 17,
        "mission": "TEST",
        "sample_rate_hz": 57600,
        "baudrate": 9600,
        "native_decimation": 2,
        "native_g3ruh": True,
    }
    generator = RUNNER.generator_command(
        plan,
        ROOT / "plan-v5.json",
        "b" * 64,
        ROOT / "review-v5.json",
        "c" * 64,
        row,
        GENERATOR.TRANSFORMS[0],
    )
    native = RUNNER.native_command(plan, row, ROOT / "native.json")
    assert generator[0] == native[0] == str(RUNNER.RUNTIME / "native_env/bin/python")
    assert "--expected-review-sha256" in generator
    assert "--expected-runtime-manifest-sha256" in generator and "--expected-runtime-manifest-sha256" in native
    assert str(RUNNER.EPHEMERAL) in generator and str(RUNNER.EPHEMERAL) in native


def test_far_metrics_are_separate_combinable_and_exact() -> None:
    zero = AUDITOR.far_metric(0, 4.0)
    assert zero["events_per_decoder_hour"] == 0.0
    assert math.isclose(zero["one_sided_exact_poisson_upper95_per_decoder_hour"], -math.log(0.05) / 4.0)
    one = AUDITOR.far_metric(1, 4.0)
    assert one["events_per_decoder_hour"] == 0.25
    combined = AUDITOR.far_metric(1, 8.0)
    assert combined["events_per_decoder_hour"] == 0.125
    auditor_source = (WORK / "audit_legal_transfer_r5_v5.py").read_text(encoding="utf-8")
    assert '"per_transform"' in auditor_source
    assert '"all_transforms"' in auditor_source
    assert '"both_receivers_combined"' in auditor_source
    assert '"natural_negative_signal_hours_claimed": False' in auditor_source
