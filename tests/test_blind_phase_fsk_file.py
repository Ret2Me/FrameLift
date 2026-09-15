from __future__ import annotations

from dataclasses import asdict, replace
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import numpy as np
import pytest
from jsonschema import Draft202012Validator

import telemetry_yield.blind_phase_fsk_file as file_api
from telemetry_yield.blind_phase_fsk_file import (
    API_VERSION,
    SCHEMA_VERSION,
    BlindPhaseFskRunConfig,
    SUPPORTED_SELECTION_MODULATION_LABELS,
    run_blind_phase_fsk_file,
    select_ci16_phase_windows_streaming,
)
from telemetry_yield.cli import main
from telemetry_yield.clipping_robust_fsk import (
    BlindPhaseFskConfig,
    BlindPhaseFskResult,
    select_ci16_phase_windows,
)


def _write_ci16(path: Path, *, windows: int = 8, samples_per_window: int = 100) -> None:
    random = np.random.default_rng(8172)
    raw = random.integers(
        -30_000,
        30_001,
        size=(windows * samples_per_window, 2),
        dtype=np.int16,
    )
    raw[2 * samples_per_window : 3 * samples_per_window, 0] = np.int16(32767)
    path.write_bytes(raw.astype("<i2", copy=False).tobytes())


def _receiver() -> BlindPhaseFskConfig:
    return BlindPhaseFskConfig(
        sample_rate_hz=100,
        baudrate=10,
        analysis_window_seconds=1.0,
        decoder_window_seconds=80.0,
        decoder_window_lead_seconds=0.5,
        candidate_window_limit=3,
        window_nms_seconds=1.5,
        phase_bins=4,
        short_search_timing_hypotheses=4,
        deep_search_timing_hypotheses=2,
    )


def _run_config(path: Path) -> BlindPhaseFskRunConfig:
    payload = path.read_bytes()
    return BlindPhaseFskRunConfig(
        receiver=_receiver(),
        expected_input_size_bytes=len(payload),
        expected_input_sha256=hashlib.sha256(payload).hexdigest(),
        maximum_scratch_bytes=1024 * 1024,
    )


def test_streaming_selector_matches_reference_selector_without_capture_sized_arrays(
    tmp_path: Path,
) -> None:
    source = tmp_path / "capture.raw"
    _write_ci16(source)
    expected = select_ci16_phase_windows(source, config=_receiver())
    actual, counters = select_ci16_phase_windows_streaming(
        source,
        config=_receiver(),
        scratch_directory=tmp_path,
        maximum_scratch_bytes=1024 * 1024,
    )
    assert [asdict(item) for item in actual] == [asdict(item) for item in expected]
    assert counters["analysis_windows_scanned"] == 8
    assert counters["analysis_window_bytes"] == 400
    assert counters["maximum_analysis_array_bytes"] == 6400
    assert counters["selector_sqlite_cache_bytes"] == 4 * 1024 * 1024
    assert counters["selector_peak_scratch_bytes"] <= 1024 * 1024
    assert not list(tmp_path.glob("telemetry-yield-selector-*"))


def test_streaming_selector_fails_before_exceeding_declared_scratch_budget(
    tmp_path: Path,
) -> None:
    source = tmp_path / "capture.raw"
    _write_ci16(source)
    with pytest.raises(ValueError, match="scratch estimate"):
        select_ci16_phase_windows_streaming(
            source,
            config=_receiver(),
            scratch_directory=tmp_path,
            maximum_scratch_bytes=100,
        )


def test_selector_rejects_oversized_analysis_window_before_reading_input() -> None:
    oversized = BlindPhaseFskConfig(
        sample_rate_hz=57_600,
        baudrate=9_600,
        analysis_window_seconds=100.0,
        decoder_window_seconds=2.75,
        decoder_window_lead_seconds=1.25,
    )
    with pytest.raises(ValueError, match="analysis window"):
        select_ci16_phase_windows_streaming(
            Path("definitely-does-not-exist.raw"),
            config=oversized,
        )


def test_all_zero_input_completes_as_degenerate_without_dsp_or_accepts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "all-zero.raw"
    output = tmp_path / "result.json"
    source.write_bytes(b"\x00" * (8 * 100 * 4))
    digest = hashlib.sha256(source.read_bytes()).hexdigest()

    def forbidden_decode(*args, **kwargs):
        raise AssertionError("all-zero input must bypass DSP")

    monkeypatch.setattr(
        file_api, "decode_clipping_robust_ax25_ci16", forbidden_decode
    )
    result = run_blind_phase_fsk_file(
        source,
        output,
        config=BlindPhaseFskRunConfig(
            receiver=_receiver(),
            expected_input_size_bytes=source.stat().st_size,
            expected_input_sha256=digest,
            maximum_scratch_bytes=1024 * 1024,
        ),
        scratch_directory=tmp_path,
    )
    assert result["status"] == "completed"
    assert result["degenerate_input"] is True
    assert result["degenerate_reason"] == "all_zero_ci16"
    counters = result["resource_counters"]
    assert counters["selected_windows"] == 0
    assert counters["decoded_windows"] == 0
    assert counters["frame_detections"] == 0
    assert counters["decoder_working_set_model_bytes"] == (
        file_api._decoder_working_set_model_bytes(_receiver())
    )
    assert result["selection"]["selected_windows"] == []
    assert result["candidates"] == {
        "detections": [],
        "consensus_groups": [],
        "trusted": [],
        "repaired_untrusted": [],
    }
    schema = json.loads(
        (
            Path(__file__).resolve().parents[1]
            / f"schemas/{SCHEMA_VERSION}.schema.json"
        ).read_text(encoding="utf-8")
    )
    Draft202012Validator(schema).validate(result)
    inconsistent = deepcopy(result)
    inconsistent["degenerate_input"] = False
    assert not Draft202012Validator(schema).is_valid(inconsistent)
    inconsistent = deepcopy(result)
    inconsistent["degenerate_reason"] = None
    assert not Draft202012Validator(schema).is_valid(inconsistent)


def test_file_api_is_content_bound_atomic_and_claim_guarded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "capture.raw"
    output = tmp_path / "result.json"
    _write_ci16(source)

    decode_calls = 0

    def fake_decode(path: Path, *, config: BlindPhaseFskConfig, selected_windows):
        nonlocal decode_calls
        decode_calls += 1
        payload = path.read_bytes()
        return BlindPhaseFskResult(
            input_path=str(path),
            input_sha256=hashlib.sha256(payload).hexdigest(),
            input_complex_samples=len(payload) // 4,
            input_duration_seconds=(len(payload) // 4) / config.sample_rate_hz,
            selected_windows=tuple(selected_windows),
            decoded_windows=len(selected_windows),
            timing_hypotheses_examined=12,
            protocol_candidates_attempted=34,
            native_protocol_candidates_attempted=4,
            repair_protocol_candidates_attempted=30,
            frames=(),
        )

    monkeypatch.setattr(file_api, "decode_clipping_robust_ax25_ci16", fake_decode)
    document = run_blind_phase_fsk_file(
        source,
        output,
        config=_run_config(source),
        scratch_directory=tmp_path,
    )
    assert document["api_version"] == API_VERSION
    assert document["schema_version"] == SCHEMA_VERSION
    assert document["status"] == "completed"
    assert document["route"]["protocol_adapter_id"] == (
        "hdlc-crc16-x25-ax25-ui-plain-and-g3ruh"
    )
    assert document["route"]["compatible_selection_modulations"] == [
        "FSK",
        "FSK AX25 G3RUH",
        "GFSK",
        "GMSK",
    ]
    assert document["selection"]["reference_free"] is True
    assert document["claim_guard"] == {
        "publication_superiority_established": False,
        "deployment_qualification_established": False,
        "file_api_contract_versioned": True,
        "repaired_frame_authentication": (
            "CRC-valid list repairs remain candidates; correlated frontend "
            "agreement is not independent authentication"
        ),
    }
    assert document["resource_counters"]["protocol_candidates_attempted"] == 34
    assert json.loads(output.read_text(encoding="utf-8")) == document
    schema = json.loads(
        (
            Path(__file__).resolve().parents[1]
            / f"schemas/{SCHEMA_VERSION}.schema.json"
        ).read_text(encoding="utf-8")
    )
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(document)
    assert not list(tmp_path.glob(".result.json.*.tmp"))
    resumed = run_blind_phase_fsk_file(
        source,
        output,
        config=_run_config(source),
        scratch_directory=tmp_path,
    )
    assert resumed == document
    assert decode_calls == 2

    semantic_forgeries = []
    forged_empty = deepcopy(document)
    forged_empty["resource_counters"]["protocol_candidates_attempted"] = 0
    semantic_forgeries.append(forged_empty)
    forged_relabel = deepcopy(document)
    forged_relabel["route"]["protocol_id"] = "ccsds"
    semantic_forgeries.append(forged_relabel)
    forged_crc = deepcopy(document)
    forged_crc["candidates"]["detections"].append(
        {
            "classification": "native_trusted_uncorrected",
            "native_trusted": True,
            "payload_hex": "00",
            "payload_sha256": "0" * 64,
            "fcs_hex": "0000",
            "frame_sha256": "0" * 64,
            "validation_layers": [],
            "provenance": {},
        }
    )
    semantic_forgeries.append(forged_crc)
    forged_consensus = deepcopy(document)
    forged_consensus["candidates"]["consensus_groups"].append({"payload_hex": "00"})
    semantic_forgeries.append(forged_consensus)
    for forged in semantic_forgeries:
        unhashed = {
            key: value
            for key, value in forged.items()
            if key != "result_payload_sha256"
        }
        forged["result_payload_sha256"] = file_api._sha256_document(unhashed)
        output.write_text(json.dumps(forged), encoding="utf-8")
        with pytest.raises(ValueError, match="deterministic revalidation"):
            run_blind_phase_fsk_file(
                source,
                output,
                config=_run_config(source),
                scratch_directory=tmp_path,
            )

    forged_schema = deepcopy(document)
    forged_schema["schema_version"] = "blind-phase-fsk-file-result-v999"
    unhashed = {
        key: value
        for key, value in forged_schema.items()
        if key != "result_payload_sha256"
    }
    forged_schema["result_payload_sha256"] = file_api._sha256_document(unhashed)
    output.write_text(json.dumps(forged_schema), encoding="utf-8")
    with pytest.raises(ValueError, match="another attempt"):
        run_blind_phase_fsk_file(
            source,
            output,
            config=_run_config(source),
            scratch_directory=tmp_path,
        )

    corrupted = deepcopy(document)
    corrupted["claim_guard"]["publication_superiority_established"] = True
    output.write_text(json.dumps(corrupted), encoding="utf-8")
    with pytest.raises(ValueError, match="result hash"):
        run_blind_phase_fsk_file(
            source,
            output,
            config=_run_config(source),
            scratch_directory=tmp_path,
        )


def test_file_api_fails_closed_on_hash_symlink_and_version(tmp_path: Path) -> None:
    source = tmp_path / "capture.raw"
    output = tmp_path / "result.json"
    _write_ci16(source)
    invalid = BlindPhaseFskRunConfig(
        receiver=_receiver(),
        expected_input_size_bytes=source.stat().st_size,
        expected_input_sha256="0" * 64,
        maximum_scratch_bytes=1024 * 1024,
    )
    with pytest.raises(ValueError, match="SHA-256"):
        run_blind_phase_fsk_file(source, output, config=invalid)
    assert not output.exists()


def test_file_api_does_not_publish_if_source_changes_during_decode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "capture.raw"
    output = tmp_path / "result.json"
    _write_ci16(source)

    def mutating_decode(path: Path, *, config: BlindPhaseFskConfig, selected_windows):
        with path.open("ab") as stream:
            stream.write(b"\x00\x00\x00\x00")
        payload = path.read_bytes()
        return BlindPhaseFskResult(
            input_path=str(path),
            input_sha256=hashlib.sha256(payload).hexdigest(),
            input_complex_samples=len(payload) // 4,
            input_duration_seconds=(len(payload) // 4) / config.sample_rate_hz,
            selected_windows=tuple(selected_windows),
            decoded_windows=len(selected_windows),
            timing_hypotheses_examined=0,
            protocol_candidates_attempted=0,
            native_protocol_candidates_attempted=0,
            repair_protocol_candidates_attempted=0,
            frames=(),
        )

    monkeypatch.setattr(
        file_api, "decode_clipping_robust_ax25_ci16", mutating_decode
    )
    with pytest.raises(ValueError, match="changed while decoding"):
        run_blind_phase_fsk_file(
            source,
            output,
            config=_run_config(source),
            scratch_directory=tmp_path,
        )
    assert not output.exists()
    link = tmp_path / "capture-link.raw"
    link.symlink_to(source)
    with pytest.raises(ValueError, match="symbolic link"):
        run_blind_phase_fsk_file(link, output, config=_run_config(source))
    with pytest.raises(ValueError, match="API version"):
        run_blind_phase_fsk_file(
            source,
            output,
            config=_run_config(source),
            api_version="telemetry-yield-blind-phase-fsk-file-api-v999",
        )
    assert not output.exists()


def test_run_config_rejects_unbounded_or_ambiguous_inputs() -> None:
    with pytest.raises(ValueError, match="multiple of four"):
        BlindPhaseFskRunConfig(
            receiver=_receiver(),
            expected_input_size_bytes=3,
            expected_input_sha256="0" * 64,
        )
    with pytest.raises(ValueError, match="finite"):
        BlindPhaseFskRunConfig(
            receiver=BlindPhaseFskConfig(
                sample_rate_hz=100,
                baudrate=10,
                rate_errors_ppm=(float("nan"),),
                phase_bins=4,
                short_search_timing_hypotheses=4,
                deep_search_timing_hypotheses=2,
            ),
            expected_input_size_bytes=4,
            expected_input_sha256="0" * 64,
        )


def test_run_config_accepts_19200_baud_at_57600_sample_rate() -> None:
    config = BlindPhaseFskRunConfig(
        receiver=BlindPhaseFskConfig(
            sample_rate_hz=57_600,
            baudrate=19_200,
            rate_errors_ppm=(0.0,),
        ),
        expected_input_size_bytes=4,
        expected_input_sha256="0" * 64,
        maximum_scratch_bytes=1024 * 1024,
    )
    assert config.receiver.sample_rate_hz / config.receiver.baudrate == 3.0


@pytest.mark.parametrize(
    ("field", "excessive"),
    (
        ("repair_path_maximum_attempts", 100_001),
        ("repair_path_maximum_unique_frames", 9),
        ("repair_region_maximum_attempts", 50_001),
        ("repair_event_maximum_attempts", 400_001),
        ("repair_event_maximum_unique_frames", 9),
        ("repair_window_maximum_events", 17),
        ("repair_window_maximum_attempts", 1_600_001),
        ("repair_window_maximum_unique_frames", 17),
        ("event_cluster_tolerance_symbols", 17),
        ("maximum_regions_per_start", 65),
        ("short_least_reliable_symbols", 513),
        ("deep_least_reliable_symbols", 513),
        ("short_maximum_flips", 9),
        ("deep_maximum_flips", 17),
        ("maximum_map_seed_states", 4097),
        ("maximum_receiver_paths_per_window", 20_001),
        ("maximum_frame_detections", 20_001),
        ("candidate_neighbor_radius", 3),
    ),
)
def test_run_config_rejects_each_excessive_repair_scheduler_bound(
    field: str,
    excessive: int,
) -> None:
    receiver = replace(_receiver(), **{field: excessive})
    with pytest.raises(ValueError, match=field):
        BlindPhaseFskRunConfig(
            receiver=receiver,
            expected_input_size_bytes=4,
            expected_input_sha256="0" * 64,
        )


def test_run_config_rejects_multiplicative_repair_work_and_output_bounds() -> None:
    with pytest.raises(ValueError, match="total repair attempt work"):
        BlindPhaseFskRunConfig(
            receiver=replace(_receiver(), candidate_window_limit=33),
            expected_input_size_bytes=4,
            expected_input_sha256="0" * 64,
        )


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("short_least_reliable_symbols", 65),
        ("short_maximum_flips", 3),
        ("short_maximum_attempts", 20_001),
    ),
)
def test_run_config_rejects_reserved_ineffective_short_list_knobs(
    field: str,
    value: int,
) -> None:
    with pytest.raises(ValueError, match=f"{field} is reserved"):
        BlindPhaseFskRunConfig(
            receiver=replace(_receiver(), **{field: value}),
            expected_input_size_bytes=4,
            expected_input_sha256="0" * 64,
        )


def test_run_config_rejects_derived_decoder_memory_product() -> None:
    with pytest.raises(ValueError, match="working-set model"):
        BlindPhaseFskRunConfig(
            receiver=replace(_receiver(), decoder_window_seconds=50_000.0),
            expected_input_size_bytes=4,
            expected_input_sha256="0" * 64,
        )


def test_run_config_rejects_zero_deep_reliability_budget() -> None:
    with pytest.raises(ValueError, match="deep_least_reliable_symbols"):
        BlindPhaseFskRunConfig(
            receiver=replace(_receiver(), deep_least_reliable_symbols=0),
            expected_input_size_bytes=4,
            expected_input_sha256="0" * 64,
        )


def test_run_config_rejects_invalid_rate_error_timing_step() -> None:
    with pytest.raises(ValueError, match="invalid symbol step"):
        BlindPhaseFskRunConfig(
            receiver=replace(
                _receiver(),
                baudrate=50.0,
                rate_errors_ppm=(-500_000.0,),
            ),
            expected_input_size_bytes=4,
            expected_input_sha256="0" * 64,
        )


def test_run_config_rejects_decoder_window_too_short_for_phase_frontend() -> None:
    with pytest.raises(ValueError, match="too short after phase discrimination"):
        BlindPhaseFskRunConfig(
            receiver=replace(_receiver(), decoder_window_seconds=2.0),
            expected_input_size_bytes=4,
            expected_input_sha256="0" * 64,
        )


def test_run_config_rejects_subsample_analysis_window() -> None:
    with pytest.raises(ValueError, match="at least two samples"):
        BlindPhaseFskRunConfig(
            receiver=replace(_receiver(), analysis_window_seconds=0.004),
            expected_input_size_bytes=4,
            expected_input_sha256="0" * 64,
        )
    with pytest.raises(ValueError, match="total repair output count"):
        BlindPhaseFskRunConfig(
            receiver=replace(
                _receiver(),
                candidate_window_limit=33,
                repair_window_maximum_attempts=400_000,
            ),
            expected_input_size_bytes=4,
            expected_input_sha256="0" * 64,
        )


def test_route_contract_golden_covers_phase_fsk_selection_labels() -> None:
    assert SUPPORTED_SELECTION_MODULATION_LABELS == (
        "FSK",
        "FSK AX25 G3RUH",
        "GFSK",
        "GMSK",
    )
    schema = json.loads(
        (
            Path(__file__).resolve().parents[1]
            / f"schemas/{SCHEMA_VERSION}.schema.json"
        ).read_text(encoding="utf-8")
    )
    assert schema["properties"]["route"]["properties"][
        "compatible_selection_modulations"
    ]["const"] == list(SUPPORTED_SELECTION_MODULATION_LABELS)


def test_file_api_refuses_existing_output_and_atomic_publish_race(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "capture.raw"
    output = tmp_path / "result.json"
    _write_ci16(source)
    output.write_bytes(b"existing")
    with pytest.raises(ValueError, match="not valid JSON"):
        run_blind_phase_fsk_file(source, output, config=_run_config(source))
    assert output.read_bytes() == b"existing"

    output.unlink()
    original_publish = file_api._publish_no_clobber

    def racing_publish(temporary_path: Path, output_path: Path) -> None:
        output_path.write_bytes(b"concurrent")
        original_publish(temporary_path, output_path)

    monkeypatch.setattr(file_api, "_publish_no_clobber", racing_publish)
    with pytest.raises(ValueError, match="concurrently"):
        file_api._atomic_json(output, {"status": "completed"})
    assert output.read_bytes() == b"concurrent"
    assert not list(tmp_path.glob(".result.json.*.tmp"))


def test_cli_builds_versioned_blind_receiver_contract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "capture.raw"
    output = tmp_path / "result.json"
    source.write_bytes(b"\x00" * 8)
    seen: dict[str, object] = {}

    def fake_run(source_arg, output_arg, *, config, scratch_directory, api_version):
        seen.update(
            source=source_arg,
            output=output_arg,
            config=config,
            scratch=scratch_directory,
            api_version=api_version,
        )
        result = {
            "status": "completed",
            "resource_counters": {
                "decoded_windows": 0,
                "native_trusted_groups": 0,
            },
            "candidates": {"repaired_untrusted": []},
        }
        output_arg.write_text(json.dumps(result), encoding="utf-8")
        return result

    monkeypatch.setattr(file_api, "run_blind_phase_fsk_file", fake_run)
    status = main(
        [
            "decode-blind-phase-fsk",
            str(source),
            "--output",
            str(output),
            "--sample-rate",
            "57600",
            "--baudrate",
            "9600",
            "--expected-size-bytes",
            "8",
            "--expected-sha256",
            "0" * 64,
            "--constant-radius-factor",
            "2.5",
            "--constant-radius-factor",
            "3.0",
            "--phase-difference-lag",
            "1",
            "--phase-difference-lag",
            "3",
            "--short-timing-hypotheses",
            "4",
            "--deep-timing-hypotheses",
            "4",
        ]
    )
    config = seen["config"]
    assert status == 0
    assert isinstance(config, BlindPhaseFskRunConfig)
    assert config.receiver.constant_radius_factors == (2.5, 3.0)
    assert config.receiver.phase_difference_lags == (1, 3)
    assert config.receiver.descramble_modes == (False, True)
    assert config.receiver.baudrate == 9600
    assert config.receiver.rate_errors_ppm == (0.0,)
    assert seen["api_version"] == API_VERSION


def test_cli_accepts_symbol_rate_alias_and_bounded_rate_bank(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "capture.raw"
    output = tmp_path / "result.json"
    source.write_bytes(b"\x00" * 8)
    seen: dict[str, object] = {}

    def fake_run(source_arg, output_arg, *, config, scratch_directory, api_version):
        seen["config"] = config
        return {
            "status": "completed",
            "resource_counters": {
                "decoded_windows": 0,
                "native_trusted_groups": 0,
            },
            "candidates": {"repaired_untrusted": []},
        }

    monkeypatch.setattr(file_api, "run_blind_phase_fsk_file", fake_run)
    assert (
        main(
            [
                "decode-blind-phase-fsk",
                str(source),
                "--output",
                str(output),
                "--sample-rate",
                "57600",
                "--symbol-rate",
                "9600",
                "--expected-size-bytes",
                "8",
                "--expected-sha256",
                "0" * 64,
                "--rate-error-ppm",
                "-5000",
                "--rate-error-ppm",
                "0",
                "--rate-error-ppm",
                "5000",
                "--descramble-mode",
                "g3ruh",
                "--descramble-mode",
                "plain",
            ]
        )
        == 0
    )
    config = seen["config"]
    assert isinstance(config, BlindPhaseFskRunConfig)
    assert config.receiver.baudrate == 9600
    assert config.receiver.rate_errors_ppm == (-5000.0, 0.0, 5000.0)
    assert config.receiver.descramble_modes == (True, False)
