import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / 'work/blind-phase-confirmatory-v2/report_scientific_campaign_v2.py'
spec = importlib.util.spec_from_file_location('campaign_v2_terminal_report_test', SCRIPT)
report = importlib.util.module_from_spec(spec)
spec.loader.exec_module(report)


def write_json(path, value, field=None):
    if field:
        value[field] = report.digest(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


def unit(root, name, observation=4493, arm='candidate', repeat='repeat-a', status='completed', payload='aa', trusted=True):
    value = {'unit': {'unit_id': name, 'observation_id': observation, 'arm': arm, 'repeat_id': repeat, 'input_kind': 'signal'},
             'status': status, 'trusted_native': [], 'untrusted_diagnostic': []}
    if payload is not None:
        value['trusted_native' if trusted else 'untrusted_diagnostic'].append(
            {'payload_hex': payload, 'payload_sha256': report.hashlib.sha256(bytes.fromhex(payload)).hexdigest(),
             'admission': 'trusted_native' if trusted else 'mission_protocol_unvalidated'})
    directory = root / 'units' / name
    write_json(directory / 'normalized-result.json', value, 'normalized_result_payload_sha256')
    write_json(directory / 'process-receipt.json', {'elapsed_seconds': 1.25}, 'receipt_payload_sha256')
    return value


def evaluation(records, observations=30, expected=6168):
    obs = [4491, 4493] + list(range(100, 100 + observations - 2))
    sets = report.inventory(records, 'trusted_native', 'signal', 'repeat-a')
    value = {'status': 'incomplete', 'gates': {}, 'null_controls': {}, 'primary_component_candidate_union_increment': {},
             'coverage': {'terminal_result_count': len(records), 'expected_unit_count': expected, 'observation_count': observations,
                          'terminal_failures': [{'unit_id': r['unit']['unit_id'], 'status': r['status']} for r in records if r['status'] != 'completed'],
                          'repeat_mismatches': []},
             'per_observation': [{'observation_id': o, **{a: sum(k[0] == o for k in s) for a, s in sets.items()}} for o in obs]}
    value['evaluation_payload_sha256'] = report.digest(value)
    return value


def test_empty_and_missing_evaluation_stays_incomplete(tmp_path):
    result = report.summarize(tmp_path)
    assert result['execution_status'] == 'incomplete_or_invalid'
    assert result['full30']['missing_or_invalid_unit_count'] == 6168
    assert not result['claim_guard']['publication_ready']
    assert not result['claim_guard']['independent_crc_rerun']


def test_pdu_dedup_repeat_union_and_sensitivity(tmp_path):
    unit(tmp_path, 'a', observation=4491)
    unit(tmp_path, 'b')
    unit(tmp_path, 'c', repeat='repeat-b')
    unit(tmp_path, 'd', repeat='repeat-b', payload='bbcc')
    unit(tmp_path, 'e', arm='component_a')
    unit(tmp_path, 'f', arm='mission_satyaml', trusted=False, payload='00')
    unit(tmp_path, 'g', status='pids_limit', payload=None)
    result = report.summarize(tmp_path, {'SERVICE_RESULT': 'exit-code'})
    full = result['full30']
    assert full['trusted_signal_unique_any_repeat']['three_way_operational_union'] == {'unique_pdus': 3, 'bytes': 4}
    assert full['trusted_signal_repeat_a']['three_way_operational_union'] == {'unique_pdus': 2, 'bytes': 2}
    assert result['sensitivity29']['trusted_signal_unique_any_repeat']['three_way_operational_union']['unique_pdus'] == 2
    assert full['untrusted_signal_diagnostic_unique_any_repeat']['mission_satyaml']['unique_pdus'] == 1
    assert full['failed_count'] == 1
    assert result['timing']['receipt_elapsed_sum_seconds_not_wall_time'] == 8.75


def test_corrupt_normalized_hash_not_counted(tmp_path):
    unit(tmp_path, 'a')
    path = tmp_path / 'units/a/normalized-result.json'
    value = json.loads(path.read_text()); value['status'] = 'faked'
    path.write_text(json.dumps(value))
    assert report.summarize(tmp_path)['full30']['valid_normalized_count'] == 0


def test_trusted_on_failed_record_rejected(tmp_path):
    unit(tmp_path, 'a', status='pids_limit')
    assert report.summarize(tmp_path)['full30']['valid_normalized_count'] == 0


def test_evaluation_hash_count_and_first_repeat_yield(tmp_path):
    records = [unit(tmp_path, 'a'), unit(tmp_path, 'b', repeat='repeat-b', payload='bb')]
    value = evaluation(records)
    assert report.check_evaluation(value, records, 6168, 30)['self_hash_verified']
    value['status'] = 'faked'
    with pytest.raises(ValueError, match='self-hash'):
        report.check_evaluation(value, records, 6168, 30)
    value = evaluation(records); value['coverage']['terminal_result_count'] = 10
    value['evaluation_payload_sha256'] = report.digest({k: v for k, v in value.items() if k != 'evaluation_payload_sha256'})
    with pytest.raises(ValueError, match='coverage'):
        report.check_evaluation(value, records, 6168, 30)
    value = evaluation(records); value['per_observation'][1]['candidate'] = 2
    value['evaluation_payload_sha256'] = report.digest({k: v for k, v in value.items() if k != 'evaluation_payload_sha256'})
    with pytest.raises(ValueError, match='yield'):
        report.check_evaluation(value, records, 6168, 30)


def test_publish_atomic_no_clobber_and_no_staging_file(tmp_path):
    path = tmp_path / 'terminal-summary.json'
    report.publish(path, b'first')
    assert path.read_bytes() == b'first'
    assert path.stat().st_mode & 0o777 == 0o444
    with pytest.raises(FileExistsError):
        report.publish(path, b'second')
    assert path.read_bytes() == b'first'
    assert list(tmp_path.iterdir()) == [path]


def test_main_idempotent_and_never_overwrites_markdown(tmp_path, monkeypatch):
    monkeypatch.setattr(report, 'OUTPUT_ROOT', tmp_path)
    assert report.main() == 0
    before = (tmp_path / 'terminal-summary.json').read_bytes()
    assert report.main() == 0
    assert (tmp_path / 'terminal-summary.json').read_bytes() == before
    path = tmp_path / 'terminal-summary.md'; path.chmod(0o644); path.write_bytes(b'user content')
    with pytest.raises(ValueError, match='refusing overwrite'):
        report.main()
    assert path.read_bytes() == b'user content'


def test_symlinked_unit_is_not_followed(tmp_path):
    external = tmp_path / 'external'; unit(external, 'a')
    (tmp_path / 'units').mkdir()
    (tmp_path / 'units/a').symlink_to(external / 'units/a', target_is_directory=True)
    assert report.summarize(tmp_path)['full30']['valid_normalized_count'] == 0


def test_main_rejects_stale_summary_after_added_results(tmp_path, monkeypatch):
    monkeypatch.setattr(report, 'OUTPUT_ROOT', tmp_path)
    assert report.main() == 0
    path = tmp_path / 'terminal-summary.json'
    before = path.read_bytes()
    unit(tmp_path, 'new-result')
    with pytest.raises(ValueError, match='stale terminal summary'):
        report.main()
    assert path.read_bytes() == before
