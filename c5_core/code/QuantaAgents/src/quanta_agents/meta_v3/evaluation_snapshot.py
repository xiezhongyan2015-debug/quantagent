"""Read-only custody snapshot of a closed canonical study, never an evaluator.

Only canonical roots, stage plans/ledgers and final application records are read.
Plans are verified before projection: large case values and tool/account artifacts
are not copied. Original plan hashes bind the projection. No current source pins,
clock-based closure, recovery, market adapter or scientific acceptance is used.
"""
from contextlib import contextmanager
import json
from pathlib import Path
import re
import sqlite3

from . import study_registry as sr
from .ledger import digest, need

VERSION = 'closed_study_custody_snapshot_v1'
TABLES = ('trials', 'calls', 'process_runs', 'producer_runs', 'tool_actions',
          'tool_measurements', 'trial_descendants', 'model_call_stages')


@contextmanager
def _ro(path):
    path = Path(path).resolve()
    need(path.is_file(), 'snapshot database missing')
    db = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=15)
    db.row_factory = sqlite3.Row
    try:
        db.execute('PRAGMA query_only=ON')
        db.execute('BEGIN')
        yield db
    finally:
        db.close()


def _identifier(value):
    need(type(value) is str and re.fullmatch(r'[A-Za-z0-9_-]{1,120}', value),
         'snapshot record identity')
    return value


def _json(value):
    return json.loads(value) if value is not None else None


def _file(root, relative, observed):
    root = Path(root).resolve()
    path = (root / relative).resolve()
    need(path.is_relative_to(root), 'snapshot metadata path escaped canonical root')
    need(path.is_file() and path.stat().st_size <= 32 * 1024**2,
         'bounded snapshot metadata missing')
    value = json.loads(path.read_text(encoding='utf-8'))
    observed[path] = digest(value)
    return value


def _catalog(db, study_id):
    row = db.execute('SELECT * FROM studies WHERE id=?', (study_id,)).fetchone()
    need(row is not None, 'unregistered study snapshot')
    tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    result = {'study': dict(row)}
    for table in TABLES:
        result[table] = sorted((dict(r) for r in db.execute(
            'SELECT * FROM ' + table + ' WHERE study_id=?', (study_id,))), key=digest) if table in tables else []
    return result


def _hashed_record(row, key):
    value = _json(row.get(key))
    need((value is None and row.get(key + '_hash') is None) or
         (value is not None and digest(value) == row.get(key + '_hash')),
         'canonical ' + key + ' hash drift')
    return value


def _processes(rows, stage_hash):
    result = []
    for row in sorted(rows, key=lambda r: r['slot']):
        intent = _hashed_record(row, 'intent')
        dispatch = _hashed_record(row, 'dispatch')
        receipt = _hashed_record(row, 'receipt')
        _hashed_record(row, 'observation')
        if intent is None:
            need(dispatch is None and receipt is None, 'process receipt without intent')
        else:
            need(intent['stage_plan_hash'] == stage_hash and
                 intent['study_id'] == row['study_id'] and intent['trial_id'] == row['trial_id']
                 and intent['slot'] == row['slot'], 'process intent stage binding drift')
            need(receipt is not None, 'study not closed: unresolved process intent')
            need(receipt.get('intent_hash') == row['intent_hash'] and
                 receipt.get('dispatch_hash') == row.get('dispatch_hash') and
                 type(receipt.get('exit_code')) is int and
                 type(receipt.get('metrics', {}).get('active_processes')) is int and
                 receipt['metrics']['active_processes'] == 0,
                 'study not closed: process terminal receipt invalid')
        result.append({'slot': row['slot'], 'intent_hash': row['intent_hash'],
            'dispatch_hash': row.get('dispatch_hash'), 'receipt_hash': row['receipt_hash'],
            'receipt': receipt})
    return result


def _producer(rows, stage_hash):
    need(len(rows) <= 1, 'duplicate producer record')
    if not rows:
        return None
    row = rows[0]
    intent = _hashed_record(row, 'intent')
    receipt = _hashed_record(row, 'receipt')
    if intent is None:
        need(receipt is None, 'producer receipt without intent')
        return {'status': 'not_started', 'selection': None, 'intent_hash': None, 'receipt_hash': None}
    need(intent['stage_plan_hash'] == stage_hash and intent['study_id'] == row['study_id']
         and intent['trial_id'] == row['trial_id'], 'producer intent stage binding drift')
    need(receipt is not None, 'study not closed: unresolved producer intent')
    need(receipt.get('intent_hash') == row['intent_hash'], 'producer receipt intent drift')
    selection = receipt.get('result', {}).get('development_selection')
    if selection is not None:
        need(type(selection) is dict, 'producer selection shape')
        if selection.get('outcome') == 'abstain':
            need(selection.get('selected_candidate_id') is None and
                 selection.get('selected_program_hash') is None, 'abstention cannot select a program')
    return {'status': 'completed', 'intent_hash': row['intent_hash'],
        'receipt_hash': row['receipt_hash'], 'result_hash': digest(receipt.get('result')),
        'selection': selection, 'selection_hash': digest(selection)}


def _case_projection(task):
    case = task.get('case')
    result = {'task_input_hash': digest(task)}
    if case is None:
        return result
    need(type(case) is dict and task.get('case_hash') == digest(case), 'task case hash drift')
    fixture = case.get('decision_fixture', {})
    result.update(case_hash=digest(case), research_class=case.get('research_class'),
        execution_backend=case.get('execution_backend'), initial_cash=case.get('initial_cash'),
        calendar=fixture.get('calendar'), codes=fixture.get('codes'))
    return result


def _stage(root, expected_hash, canonical_calls, locations, *, producer=False, observed):
    root = Path(root).resolve()
    with _ro(root / 'ledger.sqlite3') as db:
        header = db.execute('SELECT * FROM stage WHERE id=1').fetchone()
        need(header is not None, 'claimed stage header missing')
        plan = _json(header['plan'])
        need(digest(plan) == header['plan_hash'] == expected_hash and
             _file(root, 'plan.json', observed) == plan, 'original stage plan drift')
        tasks = [dict(r) for r in db.execute('SELECT * FROM tasks ORDER BY id')]
        calls = [dict(r) for r in db.execute('SELECT * FROM calls ORDER BY task_id,ordinal,id')]
    need({r['id'] for r in tasks} == set(plan['tasks']), 'stage original task set drift')
    bound = {c['id']: c for c in canonical_calls}
    finals, call_rows = {}, []
    for call in calls:
        cid = _identifier(call['id'])
        intent, receipt, result = (_json(call[k]) for k in ('intent', 'receipt', 'result'))
        registered = bound.get(cid)
        need(registered is not None and registered['intent_hash'] == digest(intent) and
             intent['intent_id'] == cid and intent['task_id'] == call['task_id'] and
             intent['plan_hash'] == expected_hash, 'canonical model call binding drift')
        if locations:
            location = locations.get(cid)
            need(location is not None and sr._root(location['root']) == sr._root(root) and
                 location['stage_plan_hash'] == expected_hash and location['task_id'] == call['task_id'],
                 'canonical model stage location drift')
        need(call['status'] in ('applied', 'failed') and receipt is not None and result is not None,
             'study not closed: unresolved model/application intent')
        proof = digest({k: receipt[k] for k in ('request_identity', 'usage', 'artifact_sha256', 'response')})
        tokens = receipt['usage']['input_tokens'] + receipt['usage']['output_tokens']
        need(all(type(receipt['usage'].get(k)) is int and receipt['usage'][k] >= 0
                 for k in ('input_tokens', 'output_tokens')) and
             receipt['request_identity']['intent_id'] == cid and
             registered['proof_hash'] == proof and registered['known_tokens'] == call['known_tokens'] == tokens,
             'canonical completion proof or cost drift')
        response = receipt['response']
        summary = {'id': cid, 'task_id': call['task_id'], 'ordinal': call['ordinal'],
            'status': call['status'], 'intent_hash': digest(intent), 'receipt_hash': digest(receipt),
            'result_hash': digest(result), 'known_tokens': tokens, 'action': response['action']}
        if response['action'] == 'submit_research_report':
            application = _file(root, Path('calls') / cid / 'application.json', observed)
            need(application['model_response_hash'] == digest(response) and
                 application['result'] == result and
                 type(application['failed']) is bool and application['failed'] == (call['status'] == 'failed'),
                 'original final application/result drift')
            final = {**summary, 'response': response, 'application_hash': digest(application),
                     'result': result, 'selection': None}
            if call['status'] == 'applied':
                from .report_arguments import decode
                args, decoding = decode(response, plan['provenance'].get('report_argument_policy'))
                need(application.get('argument_decoding') == decoding and
                     result.get('model_report') == args and result.get('legal_submission') is True,
                     'original final payload binding drift')
                need(args['outcome'] in ('abstain', 'strategy_for_development'), 'unknown original final outcome')
                if args['outcome'] == 'abstain':
                    need(args.get('program_evidence_id') is None and 'batch_candidate_id' not in args,
                         'abstention cannot select a program')
                else:
                    eid = _identifier(args['program_evidence_id'])
                    need(eid in args['evidence_ids'], 'selected evidence not cited by original final')
                    source = next((c for c in calls if c['id'] == eid and c['task_id'] == call['task_id']), None)
                    need(source is not None and source['ordinal'] < call['ordinal'], 'selected program evidence missing')
                    original_response = _json(source['receipt'])['response']
                    expected_action = 'execute_batch' if 'batch_candidate_id' in args else 'develop_strategy'
                    need(original_response['action'] == expected_action, 'selected evidence action mismatch')
                    selection = {'program_evidence_id': eid, 'evidence_receipt_hash': digest(_json(source['receipt'])),
                        'evidence_result_hash': digest(_json(source['result']))}
                    if 'batch_candidate_id' in args:
                        selection['batch_candidate_id'] = _identifier(args['batch_candidate_id'])
                    else:
                        program = json.loads(original_response['arguments_json'])['program']
                        selection.update(program=program, program_hash=digest(program))
                    final['selection'] = selection
            finals[cid] = final
        call_rows.append(summary)
    task_rows = []
    for task in tasks:
        tid = task['id']
        need(_json(task['input']) == plan['tasks'][tid], 'original task input drift')
        need(producer or task['terminal'] is not None, 'study not closed: research task nonterminal')
        own_finals = [f for f in finals.values() if f['task_id'] == tid]
        if task['terminal'] == 'submitted':
            final = finals.get(task['final_call'])
            need(final is not None and final['task_id'] == tid and final['status'] == 'applied',
                 'submitted task lacks original applied final')
        else:
            need(task['final_call'] is None, 'non-submitted task has a final selection')
            final = None
        if task['terminal'] == 'invalid_final':
            need(any(f['status'] == 'failed' for f in own_finals), 'invalid_final lacks original failed final')
        task_rows.append({'id': tid, **_case_projection(plan['tasks'][tid]), 'mode': task['mode'],
            'terminal': task['terminal'], 'final_call': task['final_call'], 'final': final,
            'final_attempts': own_finals, 'selection': final['selection'] if final else None,
            'failure_reason': None if final else ('producer_has_no_model_final' if producer else task['terminal'])})
    projection = {'root': str(root), 'stage_plan_hash': expected_hash, 'tasks_hash': digest(plan['tasks']),
        'policy': plan['policy'], 'provenance': plan['provenance'], 'deadline_epoch': plan['deadline_epoch'],
        'model': plan['model'], 'effort': plan['effort'], 'paused': header['paused'],
        'pause_reason': header['reason'], 'tasks': task_rows, 'calls': call_rows}
    return projection, plan, {c['id'] for c in calls}


def closed_study_snapshot(study_id):
    """Return a stable, digestible projection; incomplete custody raises.

    This is not a provider re-verification or source/execution certification.
    Selections describe original final custody, not independent account validity.
    """
    _identifier(study_id)
    observed = {}
    with _ro(sr.REGISTRY) as db:
        catalog = _catalog(db, study_id)
    study = catalog['study']; plan = _json(study['plan'])
    need(plan['study_id'] == study_id and digest(plan) == study['plan_hash'], 'canonical study plan drift')
    trials = {r['id']: r for r in catalog['trials']}
    need(len(trials) == len(plan['trials']) and set(trials) == {t['id'] for t in plan['trials']},
         'canonical original trial set drift')
    out = []
    for spec in plan['trials']:
        row = trials[spec['id']]; tid = row['id']
        need(_json(row['spec']) == spec and sr._root(row['root']) == sr._root(spec['root']),
             'canonical trial specification drift')
        own = {key: [r for r in catalog[key] if r.get('trial_id') == tid] for key in TABLES if key != 'trials'}
        item = {'id': tid, 'spec': spec, 'spec_hash': digest(spec), 'claimed_at': row['claimed_at'],
                'stage_plan_hash': row['stage_plan_hash'], 'stage': None, 'descendants': [], 'selection': None}
        if row['claimed_at'] is None:
            need(row['stage_plan_hash'] is None and not own['calls'] and not own['tool_actions'] and
                 not own['trial_descendants'] and all(r.get('intent') is None for r in own['process_runs'] + own['producer_runs']),
                 'unclaimed trial contains dispatched work')
            item.update(status='not_started', failure_reason='original_unclaimed_slot')
        else:
            need(row['stage_plan_hash'] is not None, 'claimed original stage binding missing')
            item['process_runs'] = _processes(own['process_runs'], row['stage_plan_hash'])
            if 'process_envelope' in spec:
                need(any(p['intent_hash'] is not None for p in item['process_runs']), 'claimed stage lacks terminal supervised process')
            producer = _producer(own['producer_runs'], row['stage_plan_hash'])
            need(('producer_contract' in spec) == (producer is not None), 'producer contract/record mismatch')
            locations = {r['id']: r for r in own['model_call_stages']}
            stage, original, seen = _stage(row['root'], row['stage_plan_hash'], own['calls'], locations,
                producer=producer is not None, observed=observed)
            need(digest(original['tasks']) == spec['tasks_hash'] and original['policy'] == spec['policy'] and
                 digest(original['provenance'].get('source_pins')) == spec['source_pins_hash'] and
                 original['deadline_epoch'] <= row['claimed_at'] + spec['duration_seconds'],
                 'stage differs from original trial contract')
            binding = original['provenance'].get('study_trial', {})
            need(binding.get('study_id') == study_id and binding.get('trial_id') == tid and
                 binding.get('study_plan_hash') == study['plan_hash'] and binding.get('trial_hash') == digest(spec)
                 and sr._root(binding.get('root', '')) == sr._root(row['root'])
                 and binding.get('claimed_at') == row['claimed_at'], 'stage study binding drift')
            need(original['provenance'].get('architecture_contract') == spec.get('architecture_contract'),
                 'original architecture contract drift')
            for child in sorted(own['trial_descendants'], key=lambda r: r['root']):
                need(child['stage_plan_hash'] is not None, 'descendant stage was not committed')
                projected, child_plan, child_calls = _stage(child['root'], child['stage_plan_hash'],
                    own['calls'], locations, observed=observed)
                expected = _json(child['expected_plan']); lineage = _json(child['binding'])
                need({k: child_plan[k] for k in expected} == expected and
                     child_plan['provenance'].get('study_descendant') == lineage and
                     child_plan['provenance'].get('study_trial') == binding and
                     sr._root(lineage['parent_root']) == sr._root(child['parent_root']) and
                     sr._root(lineage['root']) == sr._root(child['root']), 'descendant canonical binding drift')
                need(not seen.intersection(child_calls), 'duplicate stage call identity')
                seen.update(child_calls); item['descendants'].append(projected)
            need(seen == {c['id'] for c in own['calls']}, 'canonical call missing from original stage journals')
            if producer is not None:
                need(not seen and not item['descendants'], 'producer cannot acquire model or descendant final')
                item['selection'] = producer['selection']
            else:
                item['selection'] = {t['id']: t['selection'] for t in stage['tasks']}
            tool_rows = []
            measurements = {r['id']: r for r in own['tool_measurements']}
            for tool in own['tool_actions']:
                need(tool['id'] in seen and tool['metrics'] is not None and tool['outcome'] in ('returned', 'raised')
                     and tool['result_hash'] is not None, 'study not closed: unresolved tool intent')
                measurement = measurements.get(tool['id'])
                if 'measurement_protocol' in spec:
                    need(measurement is not None and digest(_json(measurement['body'])) == measurement['receipt_hash'],
                         'canonical tool measurement receipt missing or changed')
                tool_rows.append({'id': tool['id'], 'action': tool['action'], 'request_hash': tool['request_hash'],
                    'outcome': tool['outcome'], 'metrics': _json(tool['metrics']), 'quote': _json(tool['quote']),
                    'result_hash': tool['result_hash'], 'measurement_hash': measurement['receipt_hash'] if measurement else None})
            item.update(status='closed', stage=stage, producer=producer, calls=own['calls'],
                tool_actions=sorted(tool_rows, key=lambda r: r['id']), failure_reason=None)
        out.append(item)
    # Recheck known catalog rows, not DB-file hashes: an unrelated new custody
    # table/row must not change this original study's snapshot identity.
    with _ro(sr.REGISTRY) as db:
        need(_catalog(db, study_id) == catalog, 'canonical study changed during snapshot')
    for path, expected in observed.items():
        need(digest(json.loads(path.read_text(encoding='utf-8'))) == expected, 'original metadata changed during snapshot')
    return {'kind': VERSION, 'study_id': study_id, 'study_plan': plan,
        'study_plan_hash': study['plan_hash'], 'trials': out, 'closed': True,
        'formal_acceptance': False, 'market_files_read': False,
        'selection_semantics': 'Original applied final/producer selection only; abstentions and missing finals never receive replacement programs.'}
