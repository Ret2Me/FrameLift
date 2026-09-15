"""Focused trusted-host runner checks. No cohort IQ or campaign outcome access.

Set TY_SCIENTIFIC_INTEGRATION=1 for the real three-driver positive synthetic
test (runs as uid1000 through the real systemd/bwrap/cgroup path).
"""
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / 'work/blind-phase-confirmatory-v2/scientific_campaign_runner_v1.py'
spec = importlib.util.spec_from_file_location('scientific_runner_test', PATH)
runner = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = runner
spec.loader.exec_module(runner)


@pytest.fixture(scope='module')
def metadata():
    return runner.metadata_context()


def test_default_is_metadata_only(monkeypatch, capsys):
    monkeypatch.setattr(runner, 'runtime_preflight', lambda *a: pytest.fail('runtime read in default mode'))
    monkeypatch.setattr(runner, 'activate', lambda *a: pytest.fail('scientific import in default mode'))
    monkeypatch.setattr(runner, 'execute_campaign', lambda *a: pytest.fail('execution in default mode'))
    assert runner.main([]) == 0
    assert json.loads(capsys.readouterr().out)['blind_iq_opened'] is False


def test_complete_independently_counted_schedule(metadata):
    descriptors, ids = set(), set()
    for signal in metadata.sources['signals']:
        sources = [signal]
        for control in metadata.sources['controls_per_observation']:
            null_id = metadata.frozen._null_id(control)
            sources.append({**signal, 'input_kind': 'null', 'null_id': null_id,
                            'sha256': runner.digest({'synthetic_null': null_id, 'signal': signal['sha256']})})
        for source in sources:
            for unit in metadata.frozen._units_for_source(plan=metadata.plan, source=source):
                unhashed = {k: v for k, v in unit.items() if k != 'unit_id'}
                assert unit['unit_id'] == hashlib.sha256(json.dumps(
                    unhashed, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()
                ids.add(unit['unit_id'])
                descriptors.add(tuple(unit[k] for k in
                                      ('observation_id', 'arm', 'baudrate', 'repeat_id', 'input_kind', 'null_id')))
    expected = set()
    for route in metadata.plan['routing']['routes']:
        for kind, null_id in [('signal', None), *[('null', metadata.frozen._null_id(c))
                                                for c in metadata.sources['controls_per_observation']]]:
            for repeat in ('repeat-a', 'repeat-b'):
                for arm in ('candidate', 'component_a'):
                    for rate in (1200, 4800, 9600, 19200):
                        expected.add((route['observation_id'], arm, rate, repeat, kind, null_id))
                if route['profile_routable']:
                    expected.add((route['observation_id'], 'mission_satyaml', None, repeat, kind, null_id))
    assert descriptors == expected
    assert len(descriptors) == len(ids) == 6168
    assert runner.EXPOSED <= ids
    assert len(ids - runner.EXPOSED) == 6166
    assert sum(d[4] == 'signal' for d in descriptors) == 514
    assert sum(d[4] == 'null' for d in descriptors) == 5654


def test_readonly_metadata_has_frozen_claim_boundaries(metadata):
    assert runner.ASSURANCES['historical_guard_succeeded'] is False
    assert runner.ASSURANCES['persistent_namespace_attestation_claimed'] is False
    assert metadata.plan['null_controls']['control_count_per_observation'] == 11
    assert metadata.plan['null_controls']['far_denominator_control_count_per_observation'] == 10
    assert metadata.plan['null_controls']['maximum_rate_per_hour'] == 0.1
    assert metadata.plan['null_controls']['minimum_exposure_hours'] == 30
    assert runner.SCHEDULE['sensitivity_exclusion_observation_ids'] == [4491]


def test_no_legacy_execution_or_source_gate_calls():
    import ast
    tree = ast.parse(PATH.read_text())
    forbidden = {'_activate_plan_bound_modules', '_validate_campaign_execution_tools',
                 'validate_runtime_lock_live', '_static_metadata_context', '_expanded_activation_context'}
    called = {n.func.attr for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
    called |= {n.func.id for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    assert not called & forbidden


def test_publication_no_clobber_and_idempotent(tmp_path):
    path = tmp_path / 'receipt.json'
    runner.publish(path, {'terminal': True})
    inode = path.stat().st_ino
    runner.publish(path, {'terminal': True})
    assert path.stat().st_ino == inode
    with pytest.raises(ValueError, match='conflicting'):
        runner.publish(path, {'terminal': False})
    assert runner.read_json(path) == {'terminal': True}


def test_json_symlink_and_identity_drift_rejected(tmp_path):
    source = tmp_path / 'data.json'
    source.write_text('{"v": 1}')
    pinned = runner.identity(source)
    alias = tmp_path / 'alias.json'
    alias.symlink_to(source)
    with pytest.raises(OSError):
        runner.read_json(alias)
    source.write_text('{"v": 2}')
    with pytest.raises(ValueError):
        runner.read_json(source, pinned)


@pytest.mark.parametrize('change', ['byte', 'extra', 'missing', 'writable', 'execute', 'symlink'])
def test_runtime_tree_drift_rejected(tmp_path, change):
    root = tmp_path / 'runtime'
    root.mkdir()
    path = root / 'module.py'
    path.write_bytes(b'pass\n')
    path.chmod(0o444)
    rows = [dict(path='module.py', type='file', size_bytes=5,
                 sha256=hashlib.sha256(b'pass\n').hexdigest(), executable=False)]
    root.chmod(0o555)
    runner.validate_tree(root, rows)
    root.chmod(0o755)
    if change == 'byte':
        path.chmod(0o644)
        path.write_bytes(b'fail\n')
        path.chmod(0o444)
    elif change == 'extra':
        (root / 'foreign.py').write_bytes(b'')
    elif change == 'missing':
        path.unlink()
    elif change == 'writable':
        path.chmod(0o644)
    elif change == 'execute':
        path.chmod(0o555)
    elif change == 'symlink':
        path.unlink()
        path.symlink_to('/etc/passwd')
    root.chmod(0o555)
    with pytest.raises((ValueError, OSError)):
        runner.validate_tree(root, rows)
    root.chmod(0o755)


def test_runtime_relocation_same_bytes_allowed(tmp_path):
    root = tmp_path / 'new-runtime'
    root.mkdir()
    path = root / 'python'
    path.write_bytes(b'original interpreter bytes')
    path.chmod(0o555)
    root.chmod(0o555)
    original_identity = {**runner.identity(path), 'path': '/historical/runtime/python'}
    assert runner.check_identity(original_identity, path)['path'] == str(path)
    root.chmod(0o755)


def fake_context(tmp_path, monkeypatch):
    source = tmp_path / 'synthetic.ci16'
    source.write_bytes(bytes(64))
    output_root = tmp_path / 'result'
    output_root.mkdir(mode=0o700)
    (output_root / 'units').mkdir(mode=0o700)
    unit = dict(unit_id='a' * 64, observation_id=1, arm='candidate',
                source={k: v for k, v in runner.identity(source).items() if k != 'path'})
    calls = []

    class Bounded:
        @staticmethod
        def process_attempt_fingerprint(argv, **kwargs):
            return runner.digest({'argv': argv, 'unit': kwargs['unit']})

        @staticmethod
        def run_bounded_subprocess(argv, *, unit, result_path, receipt_path, limits):
            if receipt_path.exists():
                return runner.read_json(receipt_path)
            calls.append(unit['unit_id'])
            runner.publish(result_path, {'synthetic': True})
            receipt = dict(unit=unit, attempt_fingerprint=Bounded.process_attempt_fingerprint(argv, unit=unit),
                           result=runner.identity(result_path), status='completed')
            runner.publish(receipt_path, receipt)
            return receipt

    ctx = SimpleNamespace(exposures={}, bounded=Bounded(), limits=object())
    monkeypatch.setattr(runner, 'receiver_argv', lambda *a, **kw: ('synthetic',))

    def normalized(ctx, unit, receipt, raw_path, fingerprint):
        assert runner.identity(raw_path) == receipt['result']
        return dict(unit=unit, status=receipt['status'], fingerprint=fingerprint)

    monkeypatch.setattr(runner, 'normalize', normalized)
    return ctx, unit, source, output_root, calls


def test_resume_revalidates_terminal_evidence_without_execution(tmp_path, monkeypatch):
    ctx, unit, source, output, calls = fake_context(tmp_path, monkeypatch)
    first = runner.run_unit(ctx, unit, source, output, {'operation': 1})
    second = runner.run_unit(ctx, unit, source, output, {'operation': 1})
    assert first == second and calls == [unit['unit_id']]


def test_interrupted_exposure_never_reexecuted(tmp_path, monkeypatch):
    ctx, unit, source, output, calls = fake_context(tmp_path, monkeypatch)
    root = runner.unit_workspace(output, unit)
    binding = {'operation': 1}
    runner.publish(root / 'started.json', dict(unit=unit, binding=binding,
                                              attempt_fingerprint=ctx.bounded.process_attempt_fingerprint(('synthetic',), unit=unit)))
    with pytest.raises(RuntimeError, match='WILL NOT RERUN'):
        runner.run_unit(ctx, unit, source, output, binding)
    assert calls == []


def test_input_drift_blocks_even_resume(tmp_path, monkeypatch):
    ctx, unit, source, output, calls = fake_context(tmp_path, monkeypatch)
    runner.run_unit(ctx, unit, source, output, {})
    source.write_bytes(bytes(60) + b'bad!')
    with pytest.raises(ValueError, match='content identity'):
        runner.run_unit(ctx, unit, source, output, {})
    assert len(calls) == 1


def test_foreign_operation_cannot_resume(tmp_path, monkeypatch):
    ctx, unit, source, output, calls = fake_context(tmp_path, monkeypatch)
    runner.run_unit(ctx, unit, source, output, {'operation': 1})
    with pytest.raises(ValueError, match='different operation'):
        runner.run_unit(ctx, unit, source, output, {'operation': 2})
    assert len(calls) == 1


def test_normalized_tamper_not_trusted(tmp_path, monkeypatch):
    ctx, unit, source, output, calls = fake_context(tmp_path, monkeypatch)
    runner.run_unit(ctx, unit, source, output, {})
    path = output / 'units' / unit['unit_id'] / 'normalized-result.json'
    path.chmod(0o644)
    path.write_text('{"status":"completed","fake":true}')
    with pytest.raises(ValueError, match='conflicting'):
        runner.run_unit(ctx, unit, source, output, {})
    assert len(calls) == 1


def test_prior_exposure_import_has_no_launch(tmp_path, monkeypatch):
    ctx, unit, source, output, calls = fake_context(tmp_path, monkeypatch)
    historical_raw = tmp_path / 'historical-raw.json'
    runner.publish(historical_raw, {'synthetic': True})
    historical_receipt = tmp_path / 'historical-receipt.json'
    runner.publish(historical_receipt, dict(unit=unit, result=runner.identity(historical_raw),
                                            status='completed', attempt_fingerprint='f' * 64))
    ctx.exposures[unit['unit_id']] = dict(raw_result=runner.identity(historical_raw),
                                         process_receipt=runner.identity(historical_receipt))
    runner.run_unit(ctx, unit, source, output, {})
    runner.run_unit(ctx, unit, source, output, {})
    assert calls == []


def test_amendment_missing_or_foreign_rejected(metadata, tmp_path):
    args = SimpleNamespace(amendment=None, amendment_sha256=None, output_root=tmp_path)
    with pytest.raises(ValueError, match='amendment'):
        runner.validate_amendment(metadata, args)
    path = tmp_path / 'amendment.json'
    runner.publish(path, {'schema_version': runner.AMENDMENT_SCHEMA})
    args.amendment = path
    args.amendment_sha256 = runner.identity(path)['sha256']
    with pytest.raises(ValueError, match='field differs'):
        runner.validate_amendment(metadata, args)


def test_normalization_error_is_a_reproducible_terminal_failure(tmp_path, monkeypatch, metadata):
    ctx, unit, source, output, calls = fake_context(tmp_path, monkeypatch)
    ctx.frozen = metadata.frozen
    ctx.acquisition_id = metadata.acquisition_id
    monkeypatch.setattr(runner, 'normalize', lambda *a: (_ for _ in ()).throw(ValueError('malformed synthetic raw')))
    result = runner.run_unit(ctx, unit, source, output, {})
    assert result['status'] == 'normalization_failure'
    assert result['trusted_native'] == result['untrusted_diagnostic'] == []
    assert runner.reproduce_terminal(ctx, unit, output, {}) == result
    assert calls == [unit['unit_id']]


def test_supervisor_error_retained_never_relaunched(tmp_path, monkeypatch, metadata):
    ctx, unit, source, output, calls = fake_context(tmp_path, monkeypatch)
    ctx.frozen = metadata.frozen
    ctx.acquisition_id = metadata.acquisition_id
    def fail(*a, **kw):
        calls.append('launch_failure')
        raise RuntimeError('synthetic systemd error')
    ctx.bounded.run_bounded_subprocess = fail
    result = runner.run_unit(ctx, unit, source, output, {})
    assert result['status'] == 'supervisor_failure'
    assert result['process_tree_cleanup_passed'] is False
    assert runner.run_unit(ctx, unit, source, output, {}) == result
    assert calls == ['launch_failure']


def test_completed_receipt_without_raw_cannot_count_as_completed(tmp_path, monkeypatch, metadata):
    ctx, unit, source, output, calls = fake_context(tmp_path, monkeypatch)
    ctx.frozen = metadata.frozen
    ctx.acquisition_id = metadata.acquisition_id
    def empty(argv, *, unit, result_path, receipt_path, limits):
        receipt = dict(status='missing_result', unit=unit, result=None)
        runner.publish(receipt_path, receipt)
        return receipt
    ctx.bounded.run_bounded_subprocess = empty
    monkeypatch.setattr(runner, 'normalize', lambda ctx, unit, receipt, raw, fp:
                        dict(status=receipt['status'], unit=unit, trusted_native=[], untrusted_diagnostic=[]))
    result = runner.run_unit(ctx, unit, source, output, {})
    assert result['status'] == 'missing_result'
    assert result['trusted_native'] == []


def test_all_zero_scoring_contract_retained(metadata):
    ctx = copy.copy(metadata)
    ctx.scoring = SimpleNamespace(evaluate_campaign=lambda **kw: pytest.fail('scored invalid all-zero control'))
    zero = metadata.frozen._null_id({'kind': 'all_zero', 'seed': None})
    unit = dict(observation_id=4491, arm='candidate', input_kind='null', null_id=zero)
    record = dict(unit=unit, status='completed', trusted_native=[],
                  receiver_projection={'receiver': {'degenerate_input': False}})
    with pytest.raises(ValueError, match='all-zero'):
        runner.score(ctx, [unit], [record], [])


def test_failed_campaign_status_and_main_exit_are_not_success(monkeypatch, tmp_path):
    records = [dict(status='completed'), dict(status='timeout')]
    summary = runner.execution_summary(records, evaluation={'full_cohort': {'gates': {'all_required_units_completed': False}}})
    assert summary['status'] == 'campaign_scored_incomplete'
    assert summary['failed_count'] == 1 and summary['operational_execution_complete'] is False
    monkeypatch.setattr(runner, 'metadata_context', lambda: object())
    monkeypatch.setattr(runner, 'validate_amendment', lambda *a: {})
    monkeypatch.setattr(runner, 'runtime_preflight', lambda *a: {})
    monkeypatch.setattr(runner, 'activate', lambda *a: None)
    monkeypatch.setattr(runner, 'execute_campaign', lambda *a: summary)
    assert runner.main(['--execute', '--output-root', str(tmp_path)]) == 2


def test_completed_null_crash_before_unlink_reconciles_without_decode(tmp_path, monkeypatch):
    ctx, signal_unit, signal_path, output, calls = fake_context(tmp_path, monkeypatch)
    binding = {'synthetic_resume': True}
    ctx.plan = {'null_controls': {'chunk_complex_samples': 32, 'transform_module_version': 'test-v1'}}
    signal = dict(observation_id=1, satellite_id='test', input_kind='signal', null_id=None,
                  **runner.identity(signal_path))
    control = {'kind': 'all_zero', 'seed': None}
    ctx.sources = {'signals': [signal], 'controls_per_observation': [control]}
    for name in ('ephemeral', 'null-ledgers'):
        (output / name).mkdir(mode=0o700)
    path = output / 'ephemeral/current-null.ci16'
    path.write_bytes(bytes(64))
    null_unit = {**signal_unit, 'unit_id': 'b' * 64}
    ctx.frozen = SimpleNamespace(_null_id=lambda c: 'zero',
                                 _units_for_source=lambda *, plan, source: [signal_unit if source['input_kind']=='signal' else null_unit])
    source = dict(observation_id=1, satellite_id='test', input_kind='null', null_id='zero',
                  **runner.identity(path), duration_seconds=64/230400, far_denominator=False)
    source['transform'] = dict(source_path=str(signal_path), source_sha256=signal['sha256'], source_size_bytes=64,
                               output_path=str(path), output_sha256=source['sha256'], output_size_bytes=64,
                               kind='all_zero', seed=None, chunk_complex_samples=32, transform_version='test-v1')
    request = runner.null_request(ctx, signal, control, output, binding)
    runner.publish(output/'ephemeral/active-null.json', dict(request=request, source=source))
    runner.run_unit(ctx, signal_unit, signal_path, output, binding)
    runner.run_unit(ctx, null_unit, path, output, binding)
    runner.publish(output/'null-ledgers/1-zero.json', dict(source=source, unit_ids=[null_unit['unit_id']], binding=binding))
    ctx.null_transform = SimpleNamespace(transform_ci16_null=lambda *a, **kw: pytest.fail('completed null regenerated'))
    # This tiny synthetic fixture intentionally fails the final full-6168 check,
    # after traversing the actual completed-ledger resume/cleanup branch.
    with pytest.raises(ValueError, match='terminal schedule is not exact'):
        runner.execute_campaign(ctx, SimpleNamespace(output_root=output, phase='all'), binding)
    assert calls == [signal_unit['unit_id'], null_unit['unit_id']]
    assert not path.exists() and not (output/'ephemeral/active-null.json').exists()


def test_scoring_uses_exact_full_and_whole_observation_exclusion(metadata):
    ctx = copy.copy(metadata)
    invocations = []
    ctx.scoring = SimpleNamespace(evaluate_campaign=lambda **kw: invocations.append(kw) or {'called': True})
    units = [dict(observation_id=4491), dict(observation_id=4490)]
    records = [dict(unit=u) for u in units]
    result = runner.score(ctx, units, records, [])
    assert set(result) >= {'full_cohort', 'sensitivity_29'}
    assert len(invocations) == 2
    assert invocations[0]['expected_units'] == units
    assert invocations[1]['expected_units'] == [units[1]]
    assert len(invocations[0]['observation_ids']) == 30
    assert invocations[0]['observation_ids'] == [r['observation_id'] for r in ctx.plan['routing']['routes']]
    assert len(invocations[1]['observation_ids']) == 29
    assert 4491 not in invocations[1]['observation_ids']
    assert len(invocations[0]['null_ids']) == 11
    assert len(invocations[0]['far_null_ids']) == 10
    assert invocations[0]['minimum_null_exposure_hours'] == invocations[1]['minimum_null_exposure_hours'] == 30


INTEGRATION = r'''
import ast, importlib.util, json, pathlib, sys
from pathlib import Path
root = pathlib.Path(sys.argv[1]); output = pathlib.Path(sys.argv[2])
path = root / 'work/blind-phase-confirmatory-v2/scientific_campaign_runner_v1.py'
spec = importlib.util.spec_from_file_location('sci_integration', path)
m = importlib.util.module_from_spec(spec); sys.modules[spec.name] = m; spec.loader.exec_module(m)
c = m.metadata_context(); m.runtime_preflight(c); m.activate(c)
import numpy as np
from telemetry_yield.camras_replay import bytes_to_bits
from telemetry_yield.clock_recovery import HDLC_FLAG, g3ruh_scramble_bits
from telemetry_yield.crc import append_ax25_fcs
REFERENCE = bytes.fromhex('94a662b2a0826094a662b29eb2e103f00018ad8001020304')
tree = ast.parse((root / 'tests/test_gr_satellites_component_baseline.py').read_text())
selected = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in
            ('_stuff', '_nrzi_encode', '_write_component_golden')]
exec(compile(ast.Module(body=selected, type_ignores=[]), '<existing-synthetic-fixture>', 'exec'), globals())
m.secure_directory(output, create=True); m.secure_directory(output/'units', create=True)
source = output/'positive.ci16'; _write_component_golden(source, g3ruh=True)
# Candidate acquisition requires two complete0.5s windows. Repeat this known
# synthetic waveform only; no candidate parameter or real capture is changed.
source.write_bytes(source.read_bytes()*8)
route = next(r for r in c.plan['routing']['routes'] if r['profile_routable'] and r['profile_name']=='NETSAT 1')
s = dict(observation_id=route['observation_id'], satellite_id=route['satellite_id'],
         input_kind='signal', null_id=None, **m.identity(source))
units = [u for u in c.frozen._units_for_source(plan=c.plan, source=s)
         if u['baudrate'] in (9600,None) and u['repeat_id']=='repeat-a']
records = [m.run_unit(c,u,source,output,{'synthetic_positive':True}) for u in units]
for record in records:
    assert record['status']=='completed', record['status']
    arm=record['unit']['arm']
    rows=record['untrusted_diagnostic'] if arm=='mission_satyaml' else record['trusted_native']
    assert REFERENCE.hex() in {r['payload_hex'] for r in rows}, arm
    if arm=='mission_satyaml': assert record['trusted_native']==[]
old_prepare=c.bounded._prepare_cgroup
c.bounded._prepare_cgroup=lambda *a,**k: (_ for _ in ()).throw(AssertionError('resume relaunched'))
assert [m.run_unit(c,u,source,output,{'synthetic_positive':True}) for u in units]==records
c.bounded._prepare_cgroup=old_prepare
candidate=next(r for r in records if r['unit']['arm']=='candidate')
u=candidate['unit']; raw=m.read_json(output/'units'/u['unit_id']/'output/raw-result.json')
assert raw['candidates']['detections']
d=raw['candidates']['detections'][0]; d['fcs_hex']='0000'
d['frame_sha256']=__import__('hashlib').sha256(bytes.fromhex(d['payload_hex'])+b'\x00\x00').hexdigest()
raw.pop('result_payload_sha256');raw['result_payload_sha256']=m.digest(raw)
try:
    c.normalizer._candidate(raw,unit=u,candidate_config=c.config,candidate_config_identity=c.config_id,
                            runtime_lock=c.plan['provenance']['candidate_runtime_lock'])
except ValueError: pass
else: raise AssertionError('invalid CRC was accepted')
print(json.dumps({'status':'positive_three_driver_pass','blind_iq_opened':False,'units':len(units)}))
'''


@pytest.mark.skipif(os.environ.get('TY_SCIENTIFIC_INTEGRATION') != '1', reason='explicit synthetic integration opt-in')
def test_real_positive_all_three_drivers_resume_and_crc(tmp_path):
    completed = subprocess.run(['/usr/bin/python3', '-I', '-B', '-c', INTEGRATION, str(ROOT), str(tmp_path/'positive')],
                               cwd=ROOT, capture_output=True, text=True, timeout=240)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert 'positive_three_driver_pass' in completed.stdout
