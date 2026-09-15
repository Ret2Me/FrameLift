"""Independent post-outcome v3 retest checks, including real synthetic drivers.

The unchanged v1 regression cases are deliberately rerun against v3, while
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
PATH = ROOT / "work/blind-phase-confirmatory-v2/scientific_campaign_runner_v3.py"


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


runner = load_module("scientific_runner_v3_independent", PATH)
v1 = load_module("scientific_runner_v1_reference", PATH.with_name("scientific_campaign_runner_v1.py"))
regression = load_module("scientific_v1_regression_against_v3", ROOT / "tests/test_scientific_campaign_runner_v1.py")
regression.runner = runner
regression.PATH = PATH
regression.INTEGRATION = regression.INTEGRATION.replace(
    "scientific_campaign_runner_v1.py", "scientific_campaign_runner_v3.py")
metadata = regression.metadata

# This imports test functions, not historical outcomes or historical receipts.
# Their global `runner` now explicitly resolves to the v3 module above.
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
    runner.check_identity(runner.PREVIOUS_INTERRUPTED_ATTEMPT)
    assert runner.PREVIOUS_INTERRUPTED_ATTEMPT["sha256"] == "85f675a2ddd0ba6507f2cfc8983e215d23f4f6b4f28c12f1435262f059c23043"
    assert runner.PREVIOUS_RUN_EVALUATION["sha256"] == "c7adc5169a11fa4cea4c17f807ff1782ab0e42f507800d0d578ea4b4c5e450f4"
    assert runner.ASSURANCES["pristine_holdout_claimed"] is False
    assert runner.ASSURANCES["original_results_preserved"] is True


@pytest.mark.parametrize("old_run", ["v1", "v2"])
def test_amendment_rejects_old_output_directory(metadata, tmp_path, old_run):
    path = tmp_path / "amendment.json"
    runner.publish(path, {})
    args = SimpleNamespace(amendment=path, amendment_sha256=runner.identity(path)["sha256"],
                           output_root=runner.HERE / f"campaign-v4-scientific-run-{old_run}")
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
path = root / 'work/blind-phase-confirmatory-v2/scientific_campaign_runner_v3.py'
spec = importlib.util.spec_from_file_location('astrocast_v3_preflight', path)
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
    scope = Path(receipt['cgroup']['parent']) / 'app.slice' / receipt['cgroup']['systemd_scope_unit']
    assert not c.bounded._cgroup_populated(scope), receipt
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


STARTUP_SERVICE_INTEGRATION = r'''
import importlib.util, json, sys, threading, time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
root, output = Path(sys.argv[1]), Path(sys.argv[2])
own_cgroup = Path('/proc/self/cgroup').read_text()
assert 'telemetry-yield-v3-independent-' in own_cgroup and '.service' in own_cgroup, own_cgroup
path = root / 'work/blind-phase-confirmatory-v2/scientific_campaign_runner_v3.py'
spec = importlib.util.spec_from_file_location('startup_service_v3', path)
m = importlib.util.module_from_spec(spec); sys.modules[spec.name] = m; spec.loader.exec_module(m)
c = m.metadata_context(); m.runtime_preflight(c); m.activate(c)
m.secure_directory(output, create=True)
bounded = c.bounded
original_wait = bounded._wait_for_scope_limits
local = threading.local()
observed = []
def delayed_verification(cgroup, process, limits):
    assert not local.result.exists(), 'target executed before cgroup verification'
    time.sleep(.03)
    assert not local.result.exists(), 'target bypassed startup barrier during delay'
    original_wait(cgroup, process, limits)
    assert not local.result.exists(), 'target executed before barrier release'
    observed.append(str(cgroup))
bounded._wait_for_scope_limits = delayed_verification
def argv_for(directory, result):
    return ['/usr/bin/sudo', '-n', '/usr/bin/setpriv', '--ruid', '1000', '--euid', '0',
            '--rgid', '1000', '--egid', '1000', '--clear-groups', '--',
            '/usr/bin/bwrap', '--die-with-parent', '--unshare-all', '--new-session',
            '--cap-drop', 'ALL', '--uid', '1000', '--gid', '1000', '--ro-bind', '/', '/',
            '--dev', '/dev', '--proc', '/proc', '--bind', str(directory), str(directory),
            '/usr/bin/python3.12', '-I', '-S', '-B', '-c',
            'import pathlib,sys; pathlib.Path(sys.argv[1]).write_text("{}")', str(result)]
def run_one(number):
    directory = output / str(number); m.secure_directory(directory, create=True)
    local.result = directory / 'result.json'
    argv = argv_for(directory, local.result)
    document = bounded.run_bounded_subprocess(
        argv, unit={'unit_id': f'independent-service-fast-{number}'}, result_path=local.result,
        receipt_path=directory / 'receipt.json', limits=c.limits)
    assert document['status'] == 'completed', document
    assert document['argv'] == argv, document
    assert document['process_tree_cleanup_passed'] is True, document
    assert document['cgroup']['empty_after_run'] is True, document
    scope = Path(document['cgroup']['parent']) / 'app.slice' / document['cgroup']['systemd_scope_unit']
    assert not bounded._cgroup_populated(scope), document
    assert document['cgroup']['pids_events_delta'].get('max', 0) == 0, document
    assert document['cgroup']['accounting_snapshot_observed'] is True, document
    assert document['startup_gate'] == bounded._startup_gate_contract(), document
    assert document['startup_gate']['scope_and_limits_and_initial_accounting_verified_before_release'] is True
    return document
with ThreadPoolExecutor(max_workers=4) as pool:
    records = list(pool.map(run_one, range(64)))
assert len(records) == len(observed) == 64
assert len(set(observed)) == 64
# Empty/no-live-task is the completion contract. systemd may remove an already
# empty scope directory asynchronously after the receipt has been published.
collection_deadline = time.monotonic() + 2
while any(Path(path).exists() for path in observed) and time.monotonic() < collection_deadline:
    assert all(not bounded._cgroup_populated(Path(path)) for path in observed)
    time.sleep(.01)
assert all(not Path(path).exists() for path in observed)
# On a pre-release validation error, a waiting gate must be cleaned up and
# the target marker must never exist. This is not a decoder retry.
directory = output / 'injected-pre-release-failure'; m.secure_directory(directory, create=True)
result = directory / 'result.json'
failed_scope = []
def rejected_limits(cgroup, process, limits):
    failed_scope.append(cgroup)
    assert not result.exists()
    time.sleep(.03)
    assert not result.exists()
    raise RuntimeError('independent pre-release validation failure')
bounded._wait_for_scope_limits = rejected_limits
try:
    bounded.run_bounded_subprocess(argv_for(directory, result),
        unit={'unit_id': 'independent-pre-release-failure'}, result_path=result,
        receipt_path=directory / 'receipt.json', limits=c.limits)
except RuntimeError as error:
    assert 'independent pre-release validation failure' in str(error), error
else:
    raise AssertionError('failed startup validation became success')
assert failed_scope and not result.exists()
assert all(not bounded._cgroup_populated(path) for path in failed_scope)
bounded._wait_for_scope_limits = original_wait
m.publish(output / 'independent-startup-summary.json', {
    'status': 'actual_service_startup_barrier_passed', 'synthetic_fast_executions': 64,
    'failed_pre_release_target_executions': 0, 'blind_iq_opened': False,
    'runner': m.identity(path), 'supervisor': m.REVISED_SUPERVISOR,
    'service_cgroup': own_cgroup.strip()})
print(json.dumps({'status': 'actual_service_startup_barrier_passed', 'synthetic_fast_executions': 64,
                  'failed_pre_release_target_executions': 0, 'blind_iq_opened': False}))
'''


@pytest.mark.skipif(os.environ.get("TY_SCIENTIFIC_INTEGRATION") != "1", reason="explicit synthetic integration opt-in")
def test_real_startup_barrier_parallel_fast_jobs_inside_campaign_service(tmp_path):
    completed = subprocess.run(
        ["/usr/bin/python3", "-I", "-B", "-c", STARTUP_SERVICE_INTEGRATION,
         str(ROOT), str(tmp_path / "service-startup")],
        cwd=ROOT, capture_output=True, text=True, timeout=240,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "actual_service_startup_barrier_passed" in completed.stdout
