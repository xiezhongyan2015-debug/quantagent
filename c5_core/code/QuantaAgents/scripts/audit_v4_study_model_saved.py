"""Audit completed model study slots from saved evidence, without execution.

All candidate opportunities, including duplicates and failures, remain visible.
This reads only the registered v4s1 exposed cases and their saved artifacts.
It never invokes a gateway, a strategy, a worker, or a mutable recovery route.
"""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from quanta_agents.meta_v3.ledger import digest, need
from quanta_agents.meta_v3.saved_execution import SavedRawResearch
from quanta_agents.meta_v3.research_iteration import executable_program_hash
from quanta_agents.meta_v3.report_arguments import decode
from audit_v4_structural_cycle_results import (
    read, save, snapshot, candidate, account_checks, nav_metrics, number)


def identifier(value):
    need(type(value) is str and re.fullmatch(r'[A-Za-z0-9_-]{1,120}', value), 'bounded local evidence identity')
    return value


def canonical(study, trial, root, process):
    """Bind only through SQLite read-only connections; never reserve/recover."""
    from quanta_agents.meta_v3 import tool_measurements as tm
    registration = read(study / 'registration_receipt.json')
    need(registration['spec_sha256'] == hashlib.sha256((study / 'spec.json').read_bytes()).hexdigest(), 'registered spec bytes drift')
    registry = ROOT / 'experiment_traces/meta_framework_v3/study_registry.sqlite3'
    with sqlite3.connect(registry.as_uri() + '?mode=ro', uri=True, timeout=15) as db:
        db.row_factory = sqlite3.Row
        db.execute('BEGIN')
        saved_study = db.execute('SELECT * FROM studies WHERE id=?', ('v4s1',)).fetchone()
        saved_trial = db.execute('SELECT * FROM trials WHERE study_id=? AND id=?', ('v4s1', trial['id'])).fetchone()
        need(saved_study is not None and saved_trial is not None, 'canonical study/trial missing')
        study_plan = json.loads(saved_study['plan']); frozen = json.loads(saved_trial['spec'])
        need(digest(study_plan) == saved_study['plan_hash'] == registration['plan_hash'] and frozen in study_plan['trials'], 'canonical frozen cohort drift')
        expected = dict(trial, root=os.path.normcase(str(root.resolve())))
        expected.setdefault('measurement_protocol', tm.VERSION)
        need(expected == frozen and saved_trial['root'] == expected['root'], 'canonical trial differs from declared slot')
        slot = db.execute('SELECT * FROM process_runs WHERE study_id=? AND trial_id=? AND slot=1', ('v4s1', trial['id'])).fetchone()
        need(slot is not None and slot['receipt'] is not None, 'canonical native completion missing')
        for name in ('intent', 'dispatch', 'receipt'):
            value = read(root / 'process_runs/1' / (name + '.json'))
            need(json.loads(slot[name]) == value and digest(value) == slot[name + '_hash'], 'native ' + name + ' binding drift')
        intent = json.loads(slot['intent'])
        need(intent['study_id'] == 'v4s1' and intent['trial_id'] == trial['id']
            and Path(intent['root']).resolve() == root.resolve()
            and intent['stage_plan_hash'] == saved_trial['stage_plan_hash']
            and process['intent_hash'] == slot['intent_hash'] and process['dispatch_hash'] == slot['dispatch_hash'], 'native process belongs to another trial')
        calls = {r['id']: dict(r) for r in db.execute('SELECT * FROM calls WHERE study_id=? AND trial_id=?', ('v4s1', trial['id']))}
        stages = {r['id']: dict(r) for r in db.execute('SELECT * FROM model_call_stages WHERE study_id=? AND trial_id=?', ('v4s1', trial['id']))}
        measurements = {}
        for row in db.execute('SELECT * FROM tool_actions WHERE study_id=? AND trial_id=?', ('v4s1', trial['id'])):
            measurements[row['id']] = tm.saved(db, row)
        descendants = [dict(r) for r in db.execute('SELECT * FROM trial_descendants WHERE study_id=? AND trial_id=?', ('v4s1', trial['id']))]
    return {'stage_plan_hash': saved_trial['stage_plan_hash'], 'model_calls': calls,
            'call_stages': stages, 'measurements': measurements, 'descendants': descendants}


def verify_call(stage, call, bound):
    """Authenticate saved model/application plus original returned tool files."""
    root, task_id = stage['root'], stage['task_id']
    folder = root / 'calls' / identifier(call['id'])
    intent, receipt = call['intent'], call['receipt']
    need(read(folder / 'intent.json') == intent and intent['intent_id'] == call['id'], 'original call intent drift')
    saved = bound['model_calls'][call['id']]
    location = bound['call_stages'][call['id']]
    need(saved['intent_hash'] == digest(intent) and Path(location['root']).resolve() == root
        and location['stage_plan_hash'] == digest(stage['plan']) and location['task_id'] == task_id, 'canonical call-stage binding drift')
    if receipt is None:
        need(call['known_tokens'] is None and saved['known_tokens'] is None, 'unresolved call has fabricated usage')
        return None, None
    from quanta_agents.meta_v3.codex_session_gateway import verify_saved_completion
    verified = verify_saved_completion(folder, expected_prompt_hash=intent['prompt_hash'],
        expected_schema=intent['schema'], expected_artifact_sha256=receipt['artifact_sha256'])
    for key in ('response', 'usage', 'artifact_sha256', 'request_identity'):
        need(verified[key] == receipt[key], 'saved model receipt drift: ' + key)
    proof = digest({k: receipt[k] for k in ('request_identity', 'usage', 'artifact_sha256', 'response')})
    need(saved['proof_hash'] == proof and saved['known_tokens'] == call['known_tokens']
        == receipt['usage']['input_tokens'] + receipt['usage']['output_tokens'], 'canonical paid completion drift')
    response = receipt['response']
    if call['status'] in ('applied', 'failed'):
        application = read(folder / 'application.json')
        need(application['model_response_hash'] == digest(response) and application['result'] == call['result']
            and application['failed'] == (call['status'] == 'failed'), 'original application drift')
    if response['action'] == 'submit_research_report':
        if call['status'] == 'applied':
            arguments, proof = decode(response, stage['plan']['provenance'].get('report_argument_policy'))
            need(application.get('argument_decoding') == proof and call['result']['model_report'] == arguments
                and call['result']['legal_submission'] is True, 'model final argument/result binding drift')
        return None, None
    body = bound['measurements'].get(call['id'])
    if body is None or body['outcome'] != 'returned':
        return None, None
    originals = {}
    for item in body['outputs']:
        path = (root / item['path']).resolve()
        need(path.is_relative_to(root), 'original tool output escaped parent scope')
        content = path.read_bytes()
        need(len(content) == item['bytes'] and hashlib.sha256(content).hexdigest() == item['sha256'], 'original measured tool output drift')
        if path.parent == root / 'tools' / task_id / call['id']:
            originals[path.name] = json.loads(content) if path.suffix == '.json' else None
    need({'request.json', 'result.json', 'artifact.json'} <= set(originals), 'anchored tool output set incomplete')
    arguments, _ = decode(response, stage['plan']['provenance'].get('report_argument_policy'))
    result, artifact = originals['result.json'], originals['artifact.json']
    need(originals['request.json'] == {'action': response['action'], 'arguments': arguments}
        and result['evidence_id'] == call['id'] and result['action'] == response['action']
        and digest(result) == body['result_hash'] and result['artifact_hash'] == digest(artifact), 'returned tool request/result binding drift')
    if call['status'] == 'applied':
        need(result == call['result'], 'successful application differs from original returned result')
    return result, artifact


def batch_candidate(stage, batch, declaration, published, seen=None):
    """Follow explicit same-case reuse; never create a missing receipt."""
    seen = set() if seen is None else seen
    cid = identifier(declaration['candidate_id'])
    need(batch.resolve().parent == stage['root'] / 'batches' / stage['task_id'], 'batch escaped its parent task')
    key = (str(batch), cid)
    need(key not in seen, 'cyclic saved reuse')
    seen.add(key)
    folder = batch / 'candidates' / cid
    case = stage['plan']['tasks'][stage['task_id']]['case']
    row = {'candidate_id': cid, 'batch_id': batch.name,
        'status': published['status'], 'program_hash': declaration['program_hash'],
        'parameters': declaration['parameters'], 'metrics': None,
        'formal_target_success': False}
    need(digest(declaration['program']) == declaration['program_hash'], 'declared program drift')
    rule_hash = executable_program_hash(declaration['program']) if declaration['validation_error'] is None else None
    need(rule_hash == declaration['executable_program_hash'], 'declared executable rule drift')
    need(published['candidate_id'] == cid and published['status'] in {'reused', 'completed', 'failed', 'unknown', 'invalid', 'not_started'}, 'candidate identity/status drift')
    need(rule_hash is not None or published['status'] in {'invalid', 'not_started'}, 'invalid candidate cannot have executable results')
    if published['status'] == 'reused':
        disposition = read(folder / 'disposition.json')
        need(disposition['status'] == 'reused' and disposition['candidate_id'] == cid
            and disposition['reference'] == published['reference'], 'reuse reference drift')
        reference = disposition['reference']
        if 'ordinary_evidence_id' in reference:
            original = next(c for c in stage['calls'] if c['id'] == identifier(reference['ordinary_evidence_id']))
            need((original['receipt'] or {}).get('response', {}).get('action') == 'develop_strategy', 'ordinary reuse is not a strategy candidate')
            original_artifact = stage['returned_artifacts'].get(original['id'])
            need(original_artifact is not None and executable_program_hash(original_artifact['program']) == rule_hash, 'reused ordinary executable differs from declared candidate')
            original = {**original, 'result': stage['returned_results'][original['id']]}
            resolved = candidate(stage, original)
            need(resolved['artifact_hash'] == reference['artifact_hash'], 'ordinary reuse artifact drift')
        else:
            other = batch.parent / identifier(reference['batch_id'])
            registration = read(other / 'registration.json')
            need(registration['case_hash'] == digest(case), 'cross-case reuse')
            saved = read(other / 'candidates' / identifier(reference['candidate_id']) / 'receipt.json')
            need(digest(saved) == reference['receipt_hash'], 'reused receipt drift')
            declared = next(r for r in registration['candidates'] if r['candidate_id'] == reference['candidate_id'])
            need(executable_program_hash(declared['program']) == rule_hash, 'reused batch executable differs from declared candidate')
            resolved = batch_candidate(stage, other, declared, saved, seen)
        return {**row, 'reference': reference, 'resolved': resolved,
            'metrics': resolved.get('metrics'), 'new_execution': False}
    if published['status'] in ('invalid', 'not_started'):
        return {**row, 'saved_disposition': published, 'new_execution': False}
    raw_root = folder / 'workbench/raw_children/program'
    if not (raw_root / 'plan.json').exists():
        return {**row, 'saved_disposition': published, 'account_status': 'no_saved_raw_plan'}
    plan = read(raw_root / 'plan.json')
    need(plan['identity']['data_hash'] == digest(case)
        and plan['identity']['strategy_hash'] == declaration['program_hash']
        and plan['calendar'] == case['decision_fixture']['calendar']
        and plan['codes'] == case['decision_fixture']['codes']
        and number(plan['initial_cash']) == number(case['initial_cash']), 'saved batch account scope drift')
    raw = SavedRawResearch(raw_root).reconcile_saved_only(expected_plan_sha256=plan['plan_sha256'])
    row.update(raw_status=raw['status'], raw_record_head=raw['record_head_sha256'])
    if published['status'] == 'unknown':
        return {**row, 'account_status': 'original_unknown_preserved',
            'last_valued_date': raw.get('valuation_complete_through'), 'full_metrics_withheld': True}
    receipt = read(folder / 'receipt.json')
    worker = read(folder / 'worker_result.json')
    intent = read(folder / 'intent.json')
    need(all(published.get(k) == value for k, value in receipt.items())
        and intent['case_hash'] == digest(case) and intent['case'] == case
        and intent['program_hash'] == declaration['program_hash'] and intent['program'] == declaration['program']
        and receipt['candidate_id'] == cid and receipt['program_hash'] == declaration['program_hash']
        and receipt['worker_result_hash'] == digest(worker)
        and worker['request_hash'] == digest(intent)
        and worker['artifact_hash'] == digest(worker['artifact']) == receipt['artifact_hash']
        and worker['artifact']['program'] == declaration['program']
        and worker['artifact'].get('raw') == raw, 'saved batch result binding drift')
    need(receipt['status'] == ('completed' if raw['status'] == 'completed_mechanical' else 'failed'), 'candidate status differs from raw outcome')
    if raw['status'] != 'completed_mechanical' or raw.get('result') is None:
        return {**row, 'account_status': 'failed_or_interrupted_account'}
    body = raw['result']
    need([r['date'] for r in body['daily']] == plan['calendar'], 'full calendar required')
    checks = account_checks(body)
    metrics = nav_metrics(body['initial_cash'], [r['simulated_net_asset_value'] for r in body['daily']],
        trade_count=checks['trade_count'], flat_share_exits=checks['flat_share_exits'],
        exit_batches=checks['sell_fill_batches'])
    need(number(receipt['account']['final_nav']) == number(metrics['final_nav']), 'public NAV differs')
    return {**row, 'account_status': 'complete_saved_account_verified',
        'metrics': metrics, 'accounting': checks, 'new_execution': True}


def audit(trial_id):
    need(trial_id in {'v1', 'v2', 'v3', 's1', 's2', 's3'}, 'declared model slot required')
    study = ROOT / 'experiment_traces/v4s1'
    spec = read(study / 'spec.json')
    trial = next(t for t in spec['study_plan']['trials'] if t['id'] == trial_id)
    root = Path(trial['root'])
    need(root == study / trial_id, 'unexpected registered root')
    process = read(root / 'process_runs/1/receipt.json')
    need(process['metrics']['active_processes'] == 0, 'completed native job required')
    bound = canonical(study, trial, root, process)
    before = {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in root.rglob('*') if p.is_file()}
    stage = snapshot(root, 'research')
    need(digest(stage['plan']) == bound['stage_plan_hash'], 'canonical parent stage plan drift')
    need(digest(stage['plan']['tasks']) == trial['tasks_hash'], 'frozen tasks drift')
    need(stage['plan']['provenance']['architecture_contract'] == trial['architecture_contract'], 'architecture drift')
    parent_call_ids = {key for key, value in bound['call_stages'].items() if Path(value['root']).resolve() == root}
    need({c['id'] for c in stage['calls']} == parent_call_ids, 'parent call set differs from canonical trial-stage records')
    calls, accounts, registrations, reports = [], [], [], []
    stage['returned_results'], stage['returned_artifacts'] = {}, {}
    batches, executions = {}, {}
    # Verify original measured outputs before any report/candidate consumes
    # them; failed application state remains in calls, even when its producer
    # returned a complete saved result. This performs no mutable recovery.
    for call in stage['calls']:
        returned, artifact = verify_call(stage, call, bound)
        if returned is not None:
            stage['returned_results'][call['id']] = returned
            stage['returned_artifacts'][call['id']] = artifact
    for call in stage['calls']:
        response = (call['receipt'] or {}).get('response', {})
        action = response.get('action')
        original_result = call['result'] or {}
        result = stage['returned_results'].get(call['id'], original_result)
        calls.append({'call_id': call['id'], 'status': call['status'], 'action': action,
            'known_tokens': call['known_tokens'], 'unresolved_reserve': call['reserve'] if call['known_tokens'] is None else 0,
            'public_summary': response.get('public_summary'), 'result': original_result,
            'verified_returned_result': result if result != original_result else None,
            'original_application_failure_preserved': call['status'] == 'failed'})
        artifact = stage['returned_artifacts'].get(call['id'])
        need(not result.get('artifact_hash') or artifact is not None, 'result lacks anchored original tool output')
        if action == 'develop_strategy':
            accounts.append({'origin_call_id': call['id'], 'opportunity_id': 'ordinary/' + call['id'],
                'original_call_status': call['status'], **candidate(stage, {**call, 'result': result})})
        if action == 'register_experiment':
            registrations.append({'call_id': call['id'], 'result': result, 'artifact': artifact})
        if action == 'register_batch' and artifact:
            batch = root / 'batches/research' / identifier(call['id'])
            registration = read(batch / 'registration.json')
            need(registration == artifact and registration['case_hash'] == trial['case_hash'], 'batch registration binding drift')
            batches[call['id']] = {'folder': batch, 'registration': registration, 'registration_call_id': call['id']}
        if action == 'execute_batch' and artifact:
            batch_id = identifier(artifact['batch_id'])
            need(batch_id in batches, 'batch execution has no preceding anchored registration')
            registration = batches[batch_id]['registration']
            arguments, _ = decode(response, stage['plan']['provenance'].get('report_argument_policy'))
            need(arguments == {'registration_evidence_id': batch_id}
                and digest(registration) == artifact['registration_hash'], 'batch execution registration drift')
            need([r['candidate_id'] for r in artifact['candidates']] ==
                [r['candidate_id'] for r in registration['candidates']], 'all frozen candidates required')
            executions.setdefault(batch_id, []).append({'call_id': call['id'], 'artifact': artifact})
        if action == 'submit_research_report':
            reports.append({'call_id': call['id'], 'status': call['status'], 'result': result})
    if stage['state']['terminal'] == 'submitted':
        need(any(r['call_id'] == stage['state']['final_call'] and r['status'] == 'applied'
            and r['result']['legal_submission'] is True for r in reports), 'submitted parent final missing from audit')
    elif stage['state']['terminal'] == 'invalid_final':
        need(stage['state']['final_call'] is None and any(r['status'] == 'failed' for r in reports), 'failed final state missing from audit')
    batch_reviews = []
    for batch_id, item in batches.items():
        observed = executions.get(batch_id, [])
        published = observed[-1]['artifact']['candidates'] if observed else [
            {'candidate_id': d['candidate_id'], 'status': 'not_started', 'reason': 'registered_without_completed_execute_call'}
            for d in item['registration']['candidates']]
        batch_reviews.append({'batch_id': batch_id, 'registered_candidates': len(published),
            'execution_observation_call_ids': [e['call_id'] for e in observed],
            'latest_original_observation': observed[-1]['call_id'] if observed else None,
            'opportunities_counted_once': True})
        for declared, public in zip(item['registration']['candidates'], published):
            if not observed:
                # A killed execute may have left dispatch evidence but no
                # returned batch observation. Preserve this as unobserved;
                # never infer it was safely unstarted or renew the slot.
                folder = item['folder'] / 'candidates' / identifier(declared['candidate_id'])
                if (folder / 'intent.json').exists():
                    public = {'candidate_id': declared['candidate_id'], 'status': 'unknown',
                              'reason': 'dispatch_exists_without_returned_execute_observation'}
            accounts.append({'origin_call_id': observed[-1]['call_id'] if observed else None,
                'registration_call_id': batch_id, 'opportunity_id': 'batch/' + batch_id + '/' + declared['candidate_id'],
                **batch_candidate(stage, item['folder'], declared, public)})
    unanchored = []
    for path in (root / 'batches/research').glob('*/registration.json'):
        identifier(path.parent.name)
        if path.parent.name in batches:
            continue
        value = read(path)
        need(value['case_hash'] == trial['case_hash'], 'unanchored registration has foreign case')
        unanchored.append({'batch_id': path.parent.name, 'registration_hash': digest(value),
            'reserved_candidates': value['reserved_candidates'], 'accounting_authority': 'no_anchored_returned_registration'})
        for declared in value['candidates']:
            accounts.append({'opportunity_id': 'batch/' + path.parent.name + '/' + identifier(declared['candidate_id']),
                'batch_id': path.parent.name, 'candidate_id': declared['candidate_id'],
                'program_hash': declared['program_hash'], 'parameters': declared['parameters'],
                'status': 'unanchored_registration_preserved', 'metrics': None, 'formal_target_success': False})
    need(len({a['opportunity_id'] for a in accounts}) == len(accounts), 'candidate opportunity counted twice')
    after = {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in root.rglob('*') if p.is_file()}
    need(before == after, 'original evidence changed during read-only audit')
    return {'kind': 'v4s1_saved_model_slot_audit_v1', 'trial_id': trial_id,
        'observed_at': datetime.now(timezone.utc).isoformat(), 'root': str(root),
        'architecture': trial['architecture_contract']['architecture'],
        'terminal': stage['state']['terminal'], 'process_receipt': process,
        'calls': calls, 'action_counts': dict(Counter(c['action'] for c in calls if c['action'])),
        'accounts': accounts, 'candidate_opportunity_count': len(accounts),
        'batch_reviews': batch_reviews, 'unanchored_batch_registrations': unanchored,
        'registered_experiments': registrations, 'reports': reports,
        'account_scope': {'parent_only': True, 'child_accounts_included': False,
            'canonical_descendant_roots_not_counted_as_parent': [r['root'] for r in bound['descendants']],
            'parent_known_tokens': sum(c['known_tokens'] or 0 for c in calls),
            'whole_trial_known_tokens_reference': sum(c['known_tokens'] or 0 for c in bound['model_calls'].values()),
            'whole_trial_unknown_reserve_reference': sum(c['reserve'] for c in bound['model_calls'].values() if c['known_tokens'] is None)},
        'canonical_model_and_process_binding_verified': True,
        'audit_script_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'original_artifacts_unchanged': True, 'original_file_hashes': before,
        'new_model_calls': 0, 'new_strategy_executions': 0,
        'formal_success_denominator_contribution': 0, 'formal_target_success': False,
        'limitations': ['Saved arithmetic and recorded event consistency only; market sources are not reopened.',
            '2019 is exposed development. Historical arrival, actual execution costs and capacity remain uncertified.',
            'Report legality and verified scalar claims do not establish causal mechanisms or strategy generalization.',
            'This audit covers this parent ledger. Explicit descendant ledgers and trial-wide costs require the separate canonical audit.']}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--trial', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    need(not Path(args.output).exists(), 'new immutable audit output required')
    result = audit(args.trial)
    save(args.output, result)
    print({'output': args.output, 'terminal': result['terminal'], 'accounts': len(result['accounts']),
        'action_counts': result['action_counts'], 'original_artifacts_unchanged': True})
