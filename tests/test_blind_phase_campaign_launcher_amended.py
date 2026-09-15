from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import time
import types

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "work/blind-phase-confirmatory-v2/campaign_launcher_amended.py"


def _module():
    name = "blind_phase_campaign_launcher_amended_test_module"
    spec = importlib.util.spec_from_file_location(name, SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


LAUNCHER = _module()


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")


def _identity(path: Path) -> dict[str, object]:
    return LAUNCHER.regular_file_identity(path)


def _synthetic_plan_and_sources():
    plan = {
        "schema_version": "blind-phase-confirmatory-execution-plan-v2",
        "status": "frozen_before_selected_iq_download_and_decoder_execution",
        "routing": {
            "routes": [
                {
                    "observation_id": observation_id,
                    "satellite_id": f"SAT-{observation_id}",
                    "profile_routable": observation_id <= 17,
                }
                for observation_id in range(1, 31)
            ]
        },
    }
    sources = []
    for observation_id in range(1, 31):
        for input_kind, null_id in [
            ("signal", None),
            *(("null", f"null-{index}") for index in range(11)),
        ]:
            sources.append(
                {
                    "observation_id": observation_id,
                    "satellite_id": f"SAT-{observation_id}",
                    "input_kind": input_kind,
                    "null_id": null_id,
                    "size_bytes": 4,
                    "sha256": hashlib.sha256(
                        f"{observation_id}:{input_kind}:{null_id}".encode()
                    ).hexdigest(),
                    "duration_seconds": 4 / (4 * 57_600),
                    "far_denominator": input_kind == "null",
                }
            )
    return plan, {
        "schema_version": LAUNCHER.FROZEN.SOURCE_MANIFEST_SCHEMA,
        "status": "complete",
        "sources": sources,
    }


def _amendment(
    *,
    plan_identity,
    acquisition_identity,
    source_identity,
    normalizer_identity,
    exposure_records,
):
    document = {
        "schema_version": LAUNCHER.AMENDMENT_SCHEMA,
        "status": "approved_after_acquisition_before_remaining_decoder_execution",
        "campaign_pristine": False,
        "execution_plan": plan_identity,
        "acquisition_manifest": acquisition_identity,
        "source_manifest": source_identity,
        "amended_normalizer": normalizer_identity,
        "sensitivity_exclusion_observation_ids": [4491],
        "schedule": {
            "unit_count": 6_168,
            "scientific_schedule_changed": False,
            "execution_order_changed_for_operational_efficiency": True,
            "execution_phases": ["all_signals", "all_nulls"],
            "units_to_execute": 6_166,
            "units_re_normalized_without_decoder_rerun": 2,
        },
        "prior_decoder_exposures": exposure_records,
    }
    document["amendment_payload_sha256"] = LAUNCHER.sha256_document(document)
    return document


def test_scientific_unit_set_remains_exactly_6168() -> None:
    plan, sources = _synthetic_plan_and_sources()
    units = LAUNCHER.FROZEN.build_units(plan=plan, source_manifest=sources)
    assert len(units) == LAUNCHER.EXPECTED_UNIT_COUNT == 6_168
    assert len({unit["unit_id"] for unit in units}) == 6_168


def test_amendment_self_hash_bindings_and_exact_exposure_inventory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan = {"path": "/plan", "size_bytes": 1, "sha256": "1" * 64}
    acquisition = {"path": "/acquisition", "size_bytes": 2, "sha256": "2" * 64}
    source = {"path": "/source", "size_bytes": 3, "sha256": "3" * 64}
    normalizer = {"path": "/normalizer", "size_bytes": 4, "sha256": "4" * 64}
    exposures = []
    expected = {}
    for index, (unit_id, baudrate) in enumerate((("unit-a", 1200), ("unit-b", 4800))):
        raw_path = tmp_path / f"raw-{index}.json"
        receipt_path = tmp_path / f"receipt-{index}.json"
        _write_json(raw_path, {"raw": index})
        _write_json(receipt_path, {"receipt": index})
        raw_identity = _identity(raw_path)
        receipt_identity = _identity(receipt_path)
        expected[unit_id] = (baudrate, raw_identity["sha256"])
        exposures.append(
            {
                "unit_id": unit_id,
                "observation_id": 4491,
                "arm": "component_a",
                "input_kind": "signal",
                "repeat_id": "repeat-a",
                "baudrate": baudrate,
                "decoder_rerun_permitted": False,
                "raw_result": raw_identity,
                "process_receipt": receipt_identity,
            }
        )
    monkeypatch.setattr(LAUNCHER, "EXPOSED_RAW_SHA256_BY_UNIT", expected)
    amendment = _amendment(
        plan_identity=plan,
        acquisition_identity=acquisition,
        source_identity=source,
        normalizer_identity=normalizer,
        exposure_records=exposures,
    )
    assert set(
        LAUNCHER.validate_amendment(
            amendment,
            plan_identity=plan,
            acquisition_identity=acquisition,
            source_identity=source,
            normalizer_identity=normalizer,
        )
    ) == {"unit-a", "unit-b"}

    changed = json.loads(json.dumps(amendment))
    changed["schedule"]["unit_count"] = 6_167
    with pytest.raises(ValueError, match="self-hash"):
        LAUNCHER.validate_amendment(
            changed,
            plan_identity=plan,
            acquisition_identity=acquisition,
            source_identity=source,
            normalizer_identity=normalizer,
        )

    changed["amendment_payload_sha256"] = LAUNCHER.sha256_document(
        {key: value for key, value in changed.items() if key != "amendment_payload_sha256"}
    )
    with pytest.raises(ValueError, match="6,168-unit"):
        LAUNCHER.validate_amendment(
            changed,
            plan_identity=plan,
            acquisition_identity=acquisition,
            source_identity=source,
            normalizer_identity=normalizer,
        )


def test_runtime_guard_contract_is_self_hashed_and_exact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identities = {
        key: {"path": f"/{key}", "size_bytes": index, "sha256": str(index) * 64}
        for index, key in enumerate(
            ("execution_plan", "acquisition_manifest", "source_manifest", "amendment"),
            start=1,
        )
    }
    protected = {
        "candidate": {
            "path": "/candidate",
            "st_dev": 1,
            "st_ino": 2,
            "uid": 0,
            "gid": 0,
            "mode": 0o555,
            "recursive_entry_count": 3,
            "recursive_manifest_sha256": "c" * 64,
        },
        "component": {
            "path": "/component",
            "st_dev": 4,
            "st_ino": 5,
            "uid": 0,
            "gid": 0,
            "mode": 0o555,
            "recursive_entry_count": 6,
            "recursive_manifest_sha256": "d" * 64,
        },
    }
    guard = {
        "schema_version": LAUNCHER.RUNTIME_GUARD_SCHEMA,
        "status": "active",
        **identities,
        "runtime_validation": {
            "full_live_validation_before_campaign": True,
            "full_live_validation_after_campaign": True,
            "full_live_validation_per_unit": False,
            "per_unit_guard": "light-plan-tool-source-config-iq",
        },
        "source_protection": {
            "launcher_write_access_to_source_tree": False,
            "mechanism": "external read-only bind",
        },
        "protected_roots": protected,
    }
    monkeypatch.setattr(LAUNCHER.FROZEN, "_runtime_root", lambda lock: Path("/candidate"))
    monkeypatch.setattr(LAUNCHER.FROZEN, "_component_root", lambda lock: Path("/component"))
    monkeypatch.setattr(
        LAUNCHER,
        "recursive_protected_root_identity",
        lambda path: protected[path.name],
    )
    plan = {
        "provenance": {
            "candidate_runtime_lock": {},
            "component_baseline_lock": {},
        }
    }
    guard["runtime_guard_payload_sha256"] = LAUNCHER.sha256_document(guard)
    LAUNCHER.validate_runtime_guard(
        guard,
        plan=plan,
        plan_identity=identities["execution_plan"],
        acquisition_identity=identities["acquisition_manifest"],
        source_identity=identities["source_manifest"],
        amendment_identity=identities["amendment"],
    )
    guard["source_protection"]["launcher_write_access_to_source_tree"] = True
    guard["runtime_guard_payload_sha256"] = LAUNCHER.sha256_document(
        {key: value for key, value in guard.items() if key != "runtime_guard_payload_sha256"}
    )
    with pytest.raises(ValueError, match="protection contract"):
        LAUNCHER.validate_runtime_guard(
            guard,
            plan=plan,
            plan_identity=identities["execution_plan"],
            acquisition_identity=identities["acquisition_manifest"],
            source_identity=identities["source_manifest"],
            amendment_identity=identities["amendment"],
        )


def test_parallel_execution_is_bounded_and_returns_canonical_input_order() -> None:
    values = list(range(16))

    def deliberately_out_of_order(value: int) -> int:
        time.sleep((15 - value) / 20_000)
        return value * 10

    assert LAUNCHER._parallel_map_ordered(
        values, maximum_workers=8, function=deliberately_out_of_order
    ) == [value * 10 for value in values]
    for invalid in (0, 9, True):
        with pytest.raises(ValueError, match="1..8"):
            LAUNCHER._parallel_map_ordered(
                values, maximum_workers=invalid, function=lambda value: value
            )


def test_light_runtime_guard_rejects_lstat_identity_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = {
        "provenance": {
            "candidate_runtime_lock": {},
            "component_baseline_lock": {},
        }
    }
    monkeypatch.setattr(LAUNCHER.FROZEN, "_runtime_root", lambda lock: Path("/candidate"))
    monkeypatch.setattr(LAUNCHER.FROZEN, "_component_root", lambda lock: Path("/component"))
    expected = {
        "candidate": {
            "path": "/candidate",
            "st_dev": 1,
            "st_ino": 10,
            "uid": 0,
            "gid": 0,
            "mode": 0o555,
            "recursive_entry_count": 1,
            "recursive_manifest_sha256": "a" * 64,
        },
        "component": {
            "path": "/component",
            "st_dev": 1,
            "st_ino": 20,
            "uid": 0,
            "gid": 0,
            "mode": 0o555,
            "recursive_entry_count": 1,
            "recursive_manifest_sha256": "b" * 64,
        },
    }

    def changed_inode(path: Path):
        record = {
            key: value
            for key, value in expected[path.name].items()
            if key not in {"recursive_entry_count", "recursive_manifest_sha256"}
        }
        if path.name == "candidate":
            record["st_ino"] = 11
        return record

    monkeypatch.setattr(LAUNCHER, "_protected_root_lstat", changed_inode)
    with pytest.raises(ValueError, match="metadata drift: candidate"):
        LAUNCHER._validate_protected_roots_light(
            {"protected_roots": expected}, plan
        )


def test_output_namespace_is_no_clobber_and_exactly_resumable(tmp_path: Path) -> None:
    output = tmp_path / "new-amended-output"
    binding = {
        "schema_version": LAUNCHER.OUTPUT_BINDING_SCHEMA,
        "output_binding_payload_sha256": "a" * 64,
    }
    LAUNCHER._prepare_output_root(output, binding)
    assert json.loads((output / "amended-output-binding.json").read_text()) == binding
    assert (output / "amended-output-binding.json").stat().st_mode & 0o777 == 0o444
    LAUNCHER._prepare_output_root(output, binding)
    with pytest.raises(ValueError, match="not this exact"):
        LAUNCHER._prepare_output_root(output, {**binding, "unexpected": True})

    unrelated = tmp_path / "unrelated"
    unrelated.mkdir()
    with pytest.raises(ValueError, match="not this exact"):
        LAUNCHER._prepare_output_root(unrelated, binding)


def test_full_runtime_validation_occurs_once_before_and_once_after(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    paths = {
        name: Path(f"/{name}.json")
        for name in ("plan", "acquisition", "source", "amendment", "guard", "config", "output")
    }
    identity_by_path = {
        str(path): {"path": str(path), "size_bytes": 1, "sha256": name[0] * 64}
        for name, path in paths.items()
    }
    monkeypatch.setattr(
        LAUNCHER,
        "regular_file_identity",
        lambda path, maximum_bytes=None: identity_by_path.get(
            str(path), {"path": str(path), "size_bytes": 1, "sha256": "n" * 64}
        ),
    )
    monkeypatch.setattr(LAUNCHER, "_validate_release_constants", lambda **kwargs: None)
    monkeypatch.setattr(LAUNCHER.FROZEN, "_verified_acquisition", lambda *args, **kwargs: [])
    monkeypatch.setattr(LAUNCHER, "_validate_source_manifest", lambda *args, **kwargs: None)
    monkeypatch.setattr(LAUNCHER, "validate_amendment", lambda *args, **kwargs: {})
    monkeypatch.setattr(LAUNCHER, "validate_runtime_guard", lambda *args, **kwargs: None)
    monkeypatch.setattr(LAUNCHER, "_output_binding_document", lambda **kwargs: {"binding": True})
    monkeypatch.setattr(LAUNCHER, "_prepare_output_root", lambda *args, **kwargs: None)
    plan = {
        "provenance": {
            "candidate_runtime_lock": {"lock": True},
            "component_baseline_lock": {"component": True},
        }
    }
    protected = {
        "candidate": {"path": "/candidate"},
        "component": {"path": "/component"},
    }
    documents = {
        str(paths["plan"]): plan,
        str(paths["acquisition"]): {},
        str(paths["source"]): {},
        str(paths["amendment"]): {},
        str(paths["guard"]): {"protected_roots": protected},
    }
    monkeypatch.setattr(LAUNCHER, "_load_json", lambda path, *args: documents[str(path)])
    release = types.SimpleNamespace(
        validate_runtime_lock_live=lambda lock: events.append("post")
    )

    def activate(_plan):
        events.append("pre")
        LAUNCHER.FROZEN.RELEASE = release

    monkeypatch.setattr(LAUNCHER.FROZEN, "_activate_plan_bound_modules", activate)
    monkeypatch.setattr(LAUNCHER.FROZEN, "_runtime_root", lambda lock: Path("/candidate"))
    monkeypatch.setattr(LAUNCHER.FROZEN, "_component_root", lambda lock: Path("/component"))
    monkeypatch.setattr(
        LAUNCHER,
        "recursive_protected_root_identity",
        lambda path: protected[path.name],
    )
    monkeypatch.setattr(LAUNCHER, "_load_module", lambda *args: types.SimpleNamespace())
    monkeypatch.setattr(
        LAUNCHER,
        "_campaign_body",
        lambda **kwargs: {"schema_version": "test", "status": "complete"},
    )
    result = LAUNCHER.execute_campaign(
        plan_path=paths["plan"],
        acquisition_path=paths["acquisition"],
        source_manifest_path=paths["source"],
        candidate_config_path=paths["config"],
        amendment_path=paths["amendment"],
        expected_amendment_sha256=identity_by_path[str(paths["amendment"])]["sha256"],
        runtime_guard_path=paths["guard"],
        expected_runtime_guard_sha256=identity_by_path[str(paths["guard"])]["sha256"],
        output_root=paths["output"],
    )
    assert result["status"] == "complete"
    assert events == ["pre", "post"]


def test_exposure_constants_are_exact_and_signal_only() -> None:
    assert LAUNCHER.EXPOSED_RAW_SHA256_BY_UNIT == {
        "fd15a0e2ea94de7625d2609ef16fc9557596ec0b76380748ff620d2493484d5d": (
            1200,
            "4121dd18e3325f2e5af45538afa16d97ca086b0ed7ce22367d4555b6b907a954",
        ),
        "dcf6cc02da72b43ce2edf8f91567c681c65092e3327165ad6716026ece8ef637": (
            4800,
            "14e5dd2af9216ed4ebc13d04543697cb6abd0030fbf965df4a913aee22a0982f",
        ),
    }


def test_prior_exposure_is_re_normalized_without_a_decoder_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    unit = {
        "schema_version": "blind-phase-confirmatory-campaign-unit-v1",
        "unit_id": "exposed-unit",
        "observation_id": 4491,
        "satellite_id": "SAT",
        "arm": "component_a",
        "repeat_id": "repeat-a",
        "input_kind": "signal",
        "null_id": None,
        "baudrate": 1200,
        "source": {"size_bytes": 4, "sha256": "s" * 64, "duration_seconds": 1.0},
    }
    raw_path = tmp_path / "old-evidence" / "raw-result.json"
    receipt_path = tmp_path / "old-evidence" / "process-receipt.json"
    _write_json(raw_path, {"raw": "already executed"})
    _write_json(
        receipt_path,
        {"unit": unit, "status": "completed", "attempt_fingerprint": "attempt"},
    )
    exposure = {
        "raw_result": _identity(raw_path),
        "process_receipt": _identity(receipt_path),
    }
    normalizer_calls = []

    def normalize_result(**kwargs):
        normalizer_calls.append(kwargs)
        return {
            "schema_version": LAUNCHER.FROZEN.NORMALIZED_RESULT_SCHEMA_VERSION,
            "status": "completed",
            "unit": unit,
        }

    monkeypatch.setattr(
        LAUNCHER.FROZEN,
        "NORMALIZER",
        types.SimpleNamespace(normalize_result=normalize_result),
    )
    output_root = tmp_path / "new-amended-output"
    result = LAUNCHER._normalize_prior_exposure(
        unit=unit,
        exposure=exposure,
        candidate_config={},
        candidate_identity={"path": "/config", "size_bytes": 1, "sha256": "c" * 64},
        plan={
            "routing": {"routes": [{"observation_id": 4491}]},
            "provenance": {
                "candidate_runtime_lock": {},
                "component_baseline_lock": {},
                "mission_runtime_lock": {},
                "mission_satyaml_profile_sources": [],
            },
        },
        plan_identity={"path": "/plan", "size_bytes": 1, "sha256": "p" * 64},
        acquisition_identity={"path": "/acq", "size_bytes": 1, "sha256": "a" * 64},
        output_root=output_root,
        normalizer_identity={"path": "/normalizer", "size_bytes": 1, "sha256": "n" * 64},
        amendment_identity={"path": "/amendment", "size_bytes": 1, "sha256": "m" * 64},
        runtime_guard_identity={"path": "/guard", "size_bytes": 1, "sha256": "g" * 64},
    )
    assert result["status"] == "completed"
    assert len(normalizer_calls) == 1
    unit_root = output_root / "units" / "exposed-unit"
    assert sorted(path.name for path in unit_root.iterdir()) == [
        "exposure-reuse-binding.json",
        "normalized-result.json",
    ]
    assert not (unit_root / "scratch").exists()
    assert not (unit_root / "output").exists()
