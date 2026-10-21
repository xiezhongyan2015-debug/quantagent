"""One explicitly reviewed local application. Never dispatches a model."""
from pathlib import Path
import hashlib
import json
import sqlite3
import sys
import traceback
from datetime import datetime, timezone

project = Path.cwd()
stage = project / 'experiment_traces/meta_ashare_revision17'
sys.path.insert(0, str(stage / 'src'))
from quanta_agents.meta import casebank_continuation as cc
from quanta_agents.meta import codex_gateway, diagnostic_monitor

campaign = project / 'experiment_traces/meta_casebank_references/campaigns/reference_v15_original4_001'
preparation = project / 'experiment_traces/meta_casebank_continuation_preparations/reference_v15_original4_001_v17c1_scope_001'
out = campaign / 'v17_saved_application_001'
scope_bytes = (preparation / 'scope.json').read_bytes()
assert cc.sha(scope_bytes) == '63f93f90b7842fa39b3c0d037c661255ab496ce73c11c84099b4545d52552edb'
scope = cc.decode(scope_bytes)
admission_path = project / 'docs/research/meta_framework_v17_handoff/saved_local_application_admission_001.json'
admission_bytes = admission_path.read_bytes()
assert len(sys.argv) == 2 and cc.sha(admission_bytes) == sys.argv[1]
admission = cc.decode(admission_bytes)
assert admission['real_dispatch_authorized'] is False
assert admission['local_application_authorized'] is True
assert scope['allow_saved_application_after_deadline'] is True
assert admission['scope_hash'] == cc.content_hash(scope)
for pin in admission['independent_review_pins']:
    assert cc.sha(Path(pin['path']).read_bytes()) == pin['sha256']

def dump(name, value):
    cc.write_new(out / name, json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False).encode('utf-8'))

def old_rows():
    with sqlite3.connect((campaign / 'ledger.sqlite3').as_uri() + '?mode=ro', uri=True) as db:
        return {t: db.execute('SELECT body FROM ' + t + ' ORDER BY rowid').fetchall()
                for t in ('runs', 'calls', 'transport_corrections_v16')}

def wb_states(plan):
    result = {}
    for task_id, loc in plan['controller_paths'].items():
        db_path = Path(loc['root']) / 'casebank_workbench.sqlite3'
        with sqlite3.connect(db_path.as_uri() + '?mode=ro', uri=True) as db:
            result[task_id] = json.loads(db.execute("SELECT body FROM runs WHERE id='workbench'").fetchone()[0])
    return result

gateway_attempts = []
def forbidden_gateway(*args, **kwargs):
    gateway_attempts.append('blocked')
    raise RuntimeError('This saved-only operation forbids any model Gateway run')
codex_gateway.CodexGateway.run = forbidden_gateway

# Constructor is read-only; repeated execution of this administration script
# stops before any write because its output directory must be new.
controller = cc.CasebankContinuation(campaign, scope=scope, admission=admission_bytes,
    expected_admission_sha256=cc.sha(admission_bytes))
out.mkdir(exist_ok=False)
old = old_rows()
before = wb_states(controller.plan)
assert all(cc.content_hash(before[k]) == cc.content_hash(v)
           for k, v in scope['historical_workbench_snapshots'].items())
with sqlite3.connect((campaign / 'ledger.sqlite3').as_uri() + '?mode=ro', uri=True) as db:
    assert not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='casebank_continuation_v17'").fetchone()
    with sqlite3.connect(out / 'original_campaign_ledger.sqlite3') as backup:
        db.backup(backup)
dump('intent.json', {'at': datetime.now(timezone.utc).isoformat(), 'scope_hash': cc.content_hash(scope),
    'admission_sha256': cc.sha(admission_bytes), 'original_rows_hash': cc.content_hash(old),
    'workbench_before': before, 'allowed': 'prepare, apply original saved correction once, inspect, pause',
    'paid_dispatch_authorized': False, 'deadline_for_models_utc': '2026-09-07T03:01:23.682649Z',
    'deadline_extended': False, 'original_unknown_reserve': 80000})
phase = 'intent_saved'
try:
    dump('prepared.json', controller.prepare())
    phase = 'prepared'
    observation = controller.apply_saved_action(scope['correction_id'])
    dump('public_observation.json', observation)
    phase = 'saved_action_applied'
    dump('inspection.json', controller.inspect_saved())
    phase = 'saved_result_inspected'
    dump('paused.json', controller.pause('Original six-hour model admission expired incomplete; saved-only application finished; new model dispatch remains closed.'))
    phase = 'paused'
    current = diagnostic_monitor.snapshot(project)
    shown = next(x for x in current['campaigns'] if x['id'] == 'casebank:' + campaign.name)
    dump('monitor_state.json', shown)
    after = wb_states(controller.plan)
    first = controller.plan['tasks'][0]['task_run_id']
    assert after[first]['queries'] == 1 and len(after[first]['actions']) == 1 and after[first]['final_attempts'] == 0
    assert all(after[k] == before[k] for k in before if k != first)
    assert old_rows() == old and not gateway_attempts
    assert shown['calls_reserved'] == 2 and shown['completed_calls'] == 1
    assert shown['usage']['reported_tokens'] == 11235 and shown['usage']['reserved_tokens'] == 80000
    assert shown['cases'][0]['queries_completed'] == 1 and shown['status'] == 'paused'
    with sqlite3.connect((campaign / 'ledger.sqlite3').as_uri() + '?mode=ro', uri=True) as db:
        assert db.execute('SELECT count(*) FROM casebank_continuation_calls_v17').fetchone()[0] == 0
        assert db.execute('SELECT count(*) FROM casebank_continuation_actions_v17').fetchone()[0] == 1
    dump('receipt.json', {'at': datetime.now(timezone.utc).isoformat(), 'status': 'saved_original_action_applied_once_and_paused',
        'scope_hash': cc.content_hash(scope), 'admission_sha256': cc.sha(admission_bytes),
        'original_rows_unchanged': True, 'later_three_workbenches_unchanged': True,
        'after_workbench_state_hashes': {k: cc.content_hash(v) for k, v in after.items()},
        'known_tokens': 11235, 'unknown_reserve': 80000, 'transport_count': 2,
        'new_gateway_calls': 0, 'gateway_attempts': gateway_attempts, 'new_public_queries': 1,
        'public_observation_hash': cc.content_hash(observation), 'formal_finals': 0,
        'original_deadline_extended': False, 'costs_applied': False, 'execution_valid': False,
        'formal_target_success': False, 'artifacts_sha256': {x.name: cc.sha(x.read_bytes()) for x in out.iterdir() if x.is_file()}})
    print(json.dumps({'status': 'saved_original_action_applied_once_and_paused', 'receipt_sha256': cc.sha((out/'receipt.json').read_bytes()),
        'observation_sha256': cc.content_hash(observation), 'transports': 2, 'new_gateway_calls': 0, 'new_public_queries': 1}))
except BaseException as error:
    dump('failure.json', {'phase': phase, 'error': repr(error), 'traceback': traceback.format_exc(),
        'gateway_attempts': gateway_attempts, 'automatic_retry_authorized': False})
    raise
