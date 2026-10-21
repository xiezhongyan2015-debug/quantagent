"""Freeze and execute an explicitly declared architecture cohort, one slot once.

The supervisor and prior preparation are outside the measured trial and are
disclosed separately. Each worker, tool and admitted extension stays in its
original native process job and canonical study grant. No old scope is renewed.
"""
import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from quanta_agents.meta_v3 import study_registry as sr, process_envelope as pe
from quanta_agents.meta_v3.closing import ClosingPolicy
from quanta_agents.meta_v3.ledger import Ledger, digest, need, serial
from quanta_agents.meta_v3.research_tools import save_once
from quanta_agents.meta_v3.runtime import ResearchRuntime, source_pins, verify_case_sources


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def validate_spec(spec):
    # The declared experiment is a specific exposed case, not a generic route
    # into arbitrary files marked "development". Check its in-memory source
    # contract BEFORE verify_case_sources can read any referenced source bytes.
    original = read(ROOT / 'experiment_traces/meta_framework_v3/year_inputs_001/case.json')
    need(digest(original) == 'dee62e885fca97588334cf7f1d8ef97a37c90a425a523e6537b36442ef1ef776',
         'original admitted development case changed')
    need(set(spec) == {'kind', 'study_plan', 'stages', 'protocol'}
         and spec['kind'] == 'v4_local_architecture_study_v1', 'exact study specification required')
    trials = spec['study_plan']['trials']
    need(len(trials) == 9 and set(spec['stages']) == {t['id'] for t in trials}, 'complete nine-slot cohort required')
    from quanta_agents.meta_v3.architecture_contract import NAMES, validate_trial, verify_binding
    need({t['architecture_contract']['architecture'] for t in trials} == set(NAMES), 'three implemented architecture identities required')
    need(len({t['case_hash'] for t in trials}) == 1, 'this runner declares exactly one common development case')
    common_input = None
    for trial in trials:
        validate_trial(trial)
        stage = spec['stages'][trial['id']]
        need(set(stage) == {'tasks', 'provenance'}, 'exact stage input required')
        tasks = stage['tasks']; provenance = stage['provenance']
        need(len(tasks) == 1 and digest(tasks) == trial['tasks_hash'], 'frozen stage task binding differs')
        task = next(iter(tasks.values()))
        need(task['case']['research_class'] == 'real_saved_development'
             and task['case_hash'] == trial['case_hash'], 'exposed real development scope required')
        added_policies = {'report_policy', 'experiment_design_policy', 'research_policy', 'batch_policy'}
        need({k: v for k, v in task['case'].items() if k not in added_policies} == original,
             'local study must preserve the exact original case axes, capital, values and source paths')
        verify_case_sources(task)
        signature = digest({k: task[k] for k in ('idea', 'documents', 'case_hash')})
        common_input = signature if common_input is None else common_input
        need(signature == common_input, 'architectures must receive the same ordinary input, documents and data')
        need(provenance['source_pins'] == source_pins()
             and digest(provenance['source_pins']) == trial['source_pins_hash'], 'study source version changed')
        verify_binding(trial, provenance)
        need(provenance['comparison_protocol_hash'] == digest(spec['protocol']), 'comparison protocol binding differs')
        pe.validate(trial['process_envelope'])
        need(trial['process_envelope']['entrypoint'] == str(Path(__file__).resolve())
             and trial['process_envelope']['arguments'] == ['worker', '--root', trial['root']], 'worker entry differs')
    for name in NAMES:
        need(sorted(t['repeat'] for t in trials if t['architecture_contract']['architecture'] == name) == [1, 2, 3],
             'all three independent starts must be declared together')
    from quanta_agents.meta_v3.study_allocation import validate
    validate(spec['study_plan'])
    need(spec['protocol']['formal_target_success'] is False
         and spec['protocol']['formal_success_denominator_contribution'] == 0, 'local comparison cannot claim formal admission')


def freeze(spec_path):
    spec = read(spec_path); validate_spec(spec)
    result = sr.freeze(spec['study_plan'])
    save_once(Path(spec_path).with_name('registration_receipt.json'), {
        **result, 'spec_path': str(Path(spec_path).resolve()),
        'spec_sha256': hashlib.sha256(Path(spec_path).read_bytes()).hexdigest(),
        'preparation_model_calls': 0, 'preparation_not_in_trial_process_cost': True})
    return result


def dispatch(spec_path, trial_id):
    spec = read(spec_path); validate_spec(spec)
    registered = read(Path(spec_path).with_name('registration_receipt.json'))
    need(registered['spec_sha256'] == hashlib.sha256(Path(spec_path).read_bytes()).hexdigest(), 'registered specification changed')
    matches = [t for t in spec['study_plan']['trials'] if t['id'] == trial_id]
    need(len(matches) == 1, 'undeclared trial')
    trial = matches[0]; stage = spec['stages'][trial_id]
    need(not Path(trial['root']).exists(), 'trial directory already exists; inspect its authoritative state, do not replace or rerun')
    job = sr.create_stage(trial['root'], spec['study_plan']['study_id'], trial_id,
        policy=ClosingPolicy(**trial['policy']), tasks=stage['tasks'],
        duration_seconds=trial['duration_seconds'], provenance=stage['provenance'])
    return pe.supervise(job.root)


def _source_admissions(parent_case, prepared):
    from quanta_agents.meta_v3.source_admission import source_paths
    admissions = list(prepared['source_admissions'])
    for name, kind in source_paths(parent_case).items():
        notice = 'company_action_evidence_' in name
        archive = kind == 'market' or 'meta_development_raw_coverage_v13' in name
        admissions.append({'path': name, 'role': 'historical_notice' if notice else 'development_market_data',
            'partition': 'public_disclosure' if notice else 'exposed_2017_2021',
            'content_date_range': ['2019-01-01', datetime.now().date().isoformat()] if notice else
                ['2017-01-01', '2021-12-31'] if archive else ['2019-01-01', '2019-12-31']})
    return admissions


def grant(root, task_id, request_id):
    from quanta_agents.meta_v3.research_extension import _parent_request, decide_extension
    from quanta_agents.meta_v3.saved_ohlc_extension import prepare
    folder = root / 'controller_extensions' / request_id
    need(not (folder / 'intent.json').exists() and not (folder / 'inputs').exists(),
         'unfinished admission must be reconciled from its original evidence, never rebuilt')
    plan, task, artifact, _, _ = _parent_request(Ledger(root), task_id, request_id)
    service = plan['provenance']['extension_service_policy']; request = artifact['request']
    supplied = plan['provenance']['study_trial']
    snapshot = sr.snapshot(supplied['study_id'])
    own_calls = [c for c in snapshot['calls'] if c['trial_id'] == supplied['trial_id']]
    need(all(c['known_tokens'] is not None for c in own_calls), 'unknown trial cost blocks a new extension')
    requested = request['requested_resource_bounds']
    wall = min(service['maximum_child_wall_seconds'], requested['wall_seconds'],
               int(plan['deadline_epoch'] - time.time() - plan['policy']['closing_seconds']))
    calls = min(service['maximum_child_model_calls'], requested['model_calls'],
                plan['policy']['stage_calls'] - len(own_calls) - 1)
    tokens = min(service['maximum_child_tokens'], plan['policy']['stage_tokens']
                 - sum(c['known_tokens'] for c in own_calls) - plan['policy']['closing_reserve'])
    fields = request.get('requested_fields')
    ready = (request['request_kind'] == 'data' and type(fields) is list and bool(fields)
        and set(fields) <= set(service['fields']) and requested['symbols'] >= 1
        and requested['sessions'] >= 244 and calls >= 2
        and tokens >= 2 * plan['policy']['closing_reserve']
        and wall >= plan['policy']['closing_seconds'] + 120
        and not list((root / 'controller_extensions').glob('*/decision.json')))
    decision = {'status': 'ready' if ready else 'needs_implementation',
        'reason': ('Add the actually requested OHLC fields from the original saved slice under the same trial grant.' if ready else
                   'The frozen saved-field service or remaining common trial grant does not cover this request.'),
        'asset_contract': 'a_share_cash_equity', 'resource_bounds': None,
        'exposure_statement': 'Same exposed 2019 stock/calendar. Parent and child count against one original trial; no OOS or free additional search.'}
    if not ready:
        return decide_extension(root, task_id, request_id, decision=decision)
    prepared = prepare(root, task_id, request_id, folder / 'inputs'); child = prepared['child_case']
    child['research_policy']['action_limits']['request_research_extension'] = 0
    decision.update(resource_bounds={'symbols': 1, 'sessions': 244, 'model_calls': calls,
        'download_bytes': 0, 'wall_seconds': wall}, source_admissions=_source_admissions(task['case'], prepared))
    return decide_extension(root, task_id, request_id, decision=decision, child_case=child,
        policy=ClosingPolicy(task_calls=calls, stage_calls=calls, task_tokens=tokens, stage_tokens=tokens,
            call_reserve=plan['policy']['call_reserve'], closing_reserve=plan['policy']['closing_reserve'],
            closing_seconds=plan['policy']['closing_seconds']), deadline_epoch=time.time() + wall - 2)


def worker(root):
    root = Path(root).resolve(); need(pe.inside(root), 'study worker must belong to its frozen native process job')
    plan = read(root / 'plan.json')
    if plan['provenance']['architecture_contract']['architecture'] == 'fixed_template_search':
        from quanta_agents.meta_v3.registered_producer import dispatch as produce
        return produce(root)
    from quanta_agents.meta_v3.extension_handoff import inspect_handoff
    task_id = next(iter(plan['tasks']))
    while True:
        runtime = ResearchRuntime(root); state = runtime.run()
        if runtime.ledger.status(task_id)['terminal']:
            return state
        handoff = inspect_handoff(root, task_id)
        pending = [r for r in handoff['requests'] if r['status'] == 'pending_controller']
        if pending:
            grant(root, task_id, pending[0]['request_id'])
            continue
        children = [r for r in handoff['requests'] if r['status'] == 'child_running']
        if children:
            # The admitted child uses this same OS process/job. Its ledger and
            # actual source scope remain independent; resources remain common.
            child_root = root / 'controller_extensions' / children[0]['request_id'] / 'stage'
            child = ResearchRuntime(child_root); before = digest(child.ledger.status('extension'))
            child.run()
            after = child.ledger.status('extension')
            if not after['terminal']:
                need(digest(after) != before, 'child returned without actionable progress')
                return state
            continue
        return state


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    for name in ('freeze', 'dispatch'):
        sub = commands.add_parser(name); sub.add_argument('--spec', required=True)
        if name == 'dispatch': sub.add_argument('--trial', required=True)
    sub = commands.add_parser('worker'); sub.add_argument('--root', required=True)
    sub = commands.add_parser('status'); sub.add_argument('--study-id', required=True)
    args = parser.parse_args()
    result = (freeze(args.spec) if args.command == 'freeze' else
        dispatch(args.spec, args.trial) if args.command == 'dispatch' else
        worker(args.root) if args.command == 'worker' else sr.snapshot(args.study_id))
    print(serial(result), flush=True)
    if type(result) is dict and result.get('exit_code', 0) != 0:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
