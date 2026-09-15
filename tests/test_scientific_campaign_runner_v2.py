"""Independent post-outcome retest checks, including real synthetic drivers.

The unchanged v1 regression cases are deliberately rerun against v2, while
additional assertions distinguish a fresh operational retest from a new blind
confirmatory study. Set TY_SCIENTIFIC_INTEGRATION=1 for actual driver execution.
No test opens cohort IQ.
"""
import ast
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "work/blind-phase-confirmatory-v2/scientific_campaign_runner_v2.py"


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


runner = load_module("scientific_runner_v2_independent", PATH)
v1 = load_module("scientific_runner_v1_reference", PATH.with_name("scientific_campaign_runner_v1.py"))
regression = load_module("scientific_v1_regression_against_v2", ROOT / "tests/test_scientific_campaign_runner_v1.py")
regression.runner = runner
regression.PATH = PATH
regression.INTEGRATION = regression.INTEGRATION.replace(
    "scientific_campaign_runner_v1.py", "scientific_campaign_runner_v2.py")
metadata = regression.metadata

# This imports test functions, not historical outcomes or historical receipts.
# Their global `runner` now explicitly resolves to the v2 module above.
for name, value in vars(regression).items():
    if name.startswith("test_"):
        globals()[name] = value


def test_frozen_science_and_complete_unit_descriptors_match_v1(metadata):
    old = v1.metadata_context()
    for name in ("PLAN", "NORMALIZER", "EXPOSED"):
        assert getattr(runner, name) == getattr(v1, name)
    for name in ("plan", "acquisition", "acquisition_id", "sources", "source_id", "config", "config_id"):
        assert getattr(metadata, name) == getattr(old, name)
    for source in metadata.sources["signals"]:
        assert metadata.frozen._units_for_source(plan=metadata.plan, source=source) == \
            old.frozen._units_for_source(plan=old.plan, source=source)
    assert metadata.sources["controls_per_observation"] == old.sources["controls_per_observation"]


def test_fresh_uniform_retest_does_not_import_prior_decoder_exposures(metadata):
    assert metadata.exposures == {}
    assert len(v1.metadata_context().exposures) == 2
    assert runner.SCHEDULE == {
        "terminal_unit_count": 6168,
        "new_execution_count": 6168,
        "prior_exposure_count": 0,
        "new_signal_execution_count": 514,
        "new_null_execution_count": 5654,
        "maximum_workers": 4,
        "sensitivity_exclusion_observation_ids": [4491],
    }


def function_source(module, name):
    tree = ast.parse(Path(module.__file__).read_text())
    node = next(node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name)
    return ast.dump(node, include_attributes=False)


@pytest.mark.parametrize("name", [
    "identity", "check_identity", "read_json", "publish", "secure_directory",
    "validate_tree", "receiver_argv", "normalize", "normalize_terminal", "reproduce_terminal",
    "run_unit", "null_request", "validate_null_source", "clear_active_null",
])
def test_scientific_and_evidence_functions_unchanged(name):
    assert function_source(runner, name) == function_source(v1, name)


def test_zero_preflight_all_three_drivers_is_opt_in(monkeypatch, capsys):
    monkeypatch.setattr(runner, "synthetic_preflight", lambda *a: pytest.fail("implicit decoder execution"))
    assert runner.main([]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["blind_iq_opened"] is False


def test_limits_only_increase_execution_resources_and_bound_aggregate_ram(metadata):
    original = metadata.plan["candidate_execution"]["process_supervision"]["limits"]
    assert runner.PROCESS_LIMIT_OVERRIDES == {
        "maximum_process_count": 512,
        "maximum_process_tree_rss_bytes": 2 * 1024**3,
        "rlimit_address_space_bytes_per_process": 32 * 1024**3,
        "termination_grace_seconds": 15,
        "natural_exit_grace_seconds": 3,
    }
    revised = {**original, **runner.PROCESS_LIMIT_OVERRIDES}
    assert set(revised) == set(original) | {"natural_exit_grace_seconds"}
    assert all(revised[key] >= value for key, value in original.items())
    assert runner.MAXIMUM_WORKERS == 4
    assert revised["maximum_process_tree_rss_bytes"] * runner.MAXIMUM_WORKERS == 8 * 1024**3
    assert metadata.tools["bounded_process"] == runner.REVISED_SUPERVISOR
    assert metadata.plan["provenance"]["campaign_execution_tools"]["bounded_process"] != runner.REVISED_SUPERVISOR


def test_score_numerical_calls_are_unchanged():
    def numerical_body(module):
        tree = ast.parse(Path(module.__file__).read_text())
        score = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "score")
        # In v2 only the tail after the frozen full/sensitivity calls changes:
        # publication/production claims are explicitly lowered, not improved.
        end = next(i for i, node in enumerate(score.body)
                   if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name)
                   and node.targets[0].id == "sensitivity")
        return ast.dump(ast.Module(body=score.body[:end + 1], type_ignores=[]), include_attributes=False)
    assert numerical_body(runner) == numerical_body(v1)


def test_scoring_uses_exact_full_and_whole_observation_exclusion(metadata):
    invocations = []
    def evaluate(**kwargs):
        invocations.append(kwargs)
        return {"claim_guard": {"publication_ready": True, "deployment_ready": True}, "sentinel_numeric_score": 12.5}
    context = SimpleNamespace(**vars(metadata))
    context.scoring = SimpleNamespace(evaluate_campaign=evaluate)
    units = [dict(observation_id=4491), dict(observation_id=4490)]
    records = [dict(unit=unit) for unit in units]
    result = runner.score(context, units, records, [])
    assert len(invocations) == 2
    assert invocations[0]["expected_units"] == units
    assert invocations[1]["expected_units"] == [units[1]]
    assert invocations[0]["observation_ids"] == [route["observation_id"] for route in metadata.plan["routing"]["routes"]]
    assert len(invocations[0]["observation_ids"]) == 30
    assert len(invocations[1]["observation_ids"]) == 29
    assert 4491 not in invocations[1]["observation_ids"]
    assert len(invocations[0]["null_ids"]) == 11
    assert len(invocations[0]["far_null_ids"]) == 10
    assert invocations[0]["minimum_null_exposure_hours"] == invocations[1]["minimum_null_exposure_hours"] == 30
    for key in ("full_cohort", "sensitivity_29"):
        evaluation = result[key]
        assert evaluation["sentinel_numeric_score"] == 12.5
        assert evaluation["claim_guard"] == {
            "publication_ready": False,
            "deployment_ready": False,
            "pristine_holdout_claimed": False,
            "study_classification": "post_outcome_operational_retest_of_exposed_cohort",
        }
        assert evaluation["evaluation_payload_sha256"] == runner.digest(
            {name: value for name, value in evaluation.items() if name != "evaluation_payload_sha256"})
    assert result["original_process_limits_unchanged"] is False


def test_previous_run_is_pinned_and_preserved():
    runner.check_identity(runner.PREVIOUS_RUN_AMENDMENT)
    runner.check_identity(runner.PREVIOUS_RUN_EVALUATION)
    assert runner.PREVIOUS_RUN_EVALUATION["sha256"] == "c7adc5169a11fa4cea4c17f807ff1782ab0e42f507800d0d578ea4b4c5e450f4"
    assert runner.ASSURANCES["pristine_holdout_claimed"] is False
    assert runner.ASSURANCES["original_results_preserved"] is True


def test_amendment_rejects_old_output_directory(metadata, tmp_path):
    path = tmp_path / "amendment.json"
    runner.publish(path, {})
    args = SimpleNamespace(amendment=path, amendment_sha256=runner.identity(path)["sha256"],
                           output_root=runner.HERE / "campaign-v4-scientific-run-v1")
    with pytest.raises(ValueError, match="previous execution directory"):
        runner.validate_amendment(metadata, args)


@pytest.mark.skipif(os.environ.get("TY_SCIENTIFIC_INTEGRATION") != "1", reason="explicit synthetic integration opt-in")
def test_real_zero_all_three_drivers_two_repeats(tmp_path):
    completed = subprocess.run(
        ["/usr/bin/python3", "-I", "-B", str(PATH), "--synthetic-preflight",
         "--output-root", str(tmp_path / "zero")],
        cwd=ROOT, capture_output=True, text=True, timeout=240,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    summary = json.loads(completed.stdout.strip().splitlines()[-1])
    assert summary == {"status": "synthetic_drivers_passed", "driver_executions": 6, "blind_iq_opened": False}
    normalized = [json.loads(path.read_text()) for path in (tmp_path / "zero/units").glob("*/normalized-result.json")]
    receipts = [json.loads(path.read_text()) for path in (tmp_path / "zero/units").glob("*/process-receipt.json")]
    assert len(normalized) == len(receipts) == 6
    assert all(record["status"] == "completed" for record in normalized)
    assert all(record["trusted_native"] == record["untrusted_diagnostic"] == [] for record in normalized)
    assert all(record["process_tree_cleanup_passed"] is True for record in receipts)
    assert {record["unit"]["arm"] for record in normalized} == {"candidate", "component_a", "mission_satyaml"}


ASTROCAST_INTEGRATION = r'''
import importlib.util, json, sys
from pathlib import Path
root, output = Path(sys.argv[1]), Path(sys.argv[2])
path = root / 'work/blind-phase-confirmatory-v2/scientific_campaign_runner_v2.py'
spec = importlib.util.spec_from_file_location('astrocast_v2_preflight', path)
m = importlib.util.module_from_spec(spec); sys.modules[spec.name] = m; spec.loader.exec_module(m)
c = m.metadata_context(); m.runtime_preflight(c); m.activate(c)
m.secure_directory(output, create=True); m.secure_directory(output / 'units', create=True)
source = output / 'zero.ci16'; source.write_bytes(bytes(4 * 57600))
route = next(r for r in c.plan['routing']['routes'] if r['observation_id'] == 4512)
assert route['profile_name'] == 'Astrocast 0.1'
s = dict(observation_id=route['observation_id'], satellite_id=route['satellite_id'],
         input_kind='signal', null_id=None, **m.identity(source))
units = [u for u in c.frozen._units_for_source(plan=c.plan, source=s) if u['arm'] == 'mission_satyaml']
records = [m.run_unit(c, u, source, output, {'synthetic_astrocast_zero': True}) for u in units]
assert len(records) == 2
for record in records:
    assert record['status'] == 'completed', record
    assert record['trusted_native'] == record['untrusted_diagnostic'] == []
    receipt = m.read_json(output / 'units' / record['unit']['unit_id'] / 'process-receipt.json')
    assert receipt['process_tree_cleanup_passed'] is True, receipt
    assert receipt['limits']['maximum_process_count'] == 512, receipt
    assert receipt['cgroup']['empty_after_run'] is True, receipt
    assert receipt['cgroup']['scope_removed_before_receipt'] is True, receipt
    assert receipt['cgroup']['pids_events_delta'].get('max', 0) == 0, receipt
assert records[0]['determinism_projection_sha256'] == records[1]['determinism_projection_sha256']
m.runtime_preflight(c)
print(json.dumps({'status': 'astrocast_512_tasks_passed', 'driver_executions': 2, 'blind_iq_opened': False}))
'''


@pytest.mark.skipif(os.environ.get("TY_SCIENTIFIC_INTEGRATION") != "1", reason="explicit synthetic integration opt-in")
def test_real_astrocast_profile_previously_hit_64_tasks(tmp_path):
    completed = subprocess.run(
        ["/usr/bin/python3", "-I", "-B", "-c", ASTROCAST_INTEGRATION, str(ROOT), str(tmp_path / "astrocast")],
        cwd=ROOT, capture_output=True, text=True, timeout=240,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "astrocast_512_tasks_passed" in completed.stdout
