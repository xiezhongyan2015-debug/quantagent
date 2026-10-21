"""Controller-owned study slots and cross-directory model-cost reservations.

This registers bounded study opportunities, not formal strategy acceptance.
Tool-search/compute accounting must be integrated before full-stack comparison.
"""
from contextlib import contextmanager
from copy import deepcopy
import json
import os
from pathlib import Path
import re
import sqlite3
import time

from .kernel import ROOT
from .ledger import digest, need, serial

REGISTRY = ROOT / 'experiment_traces/meta_framework_v3/study_registry.sqlite3'
VERSION = 'study_slots_and_model_cost_v1'
OPPORTUNITY_RULE = 'one_frozen_repeat_cohort_per_declared_case_architecture_v1'


def _root(path):
    return os.path.normcase(str(Path(path).resolve()))


def _cohort(spec):
    # Budgets, input/source revisions, directory and repeat labels cannot mint
    # new attempts at the same declared case/architecture. Declare all repeats
    # together; a failed or unclaimed member never becomes a replacement slot.
    return spec['case_hash'], spec['architecture_hash']


@contextmanager
def _db(*, create=False):
    if create:
        REGISTRY.parent.mkdir(parents=True, exist_ok=True)
    else:
        need(REGISTRY.is_file(), 'canonical study registry missing')
    db = sqlite3.connect(REGISTRY, timeout=15, isolation_level=None)
    db.row_factory = sqlite3.Row
    try:
        db.execute('PRAGMA foreign_keys=ON')
        db.execute('PRAGMA synchronous=FULL')
        if create:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS studies(
                    id TEXT PRIMARY KEY, semantic_hash TEXT NOT NULL UNIQUE,
                    plan TEXT NOT NULL, plan_hash TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS trials(
                    study_id TEXT NOT NULL REFERENCES studies(id), id TEXT NOT NULL,
                    root TEXT NOT NULL UNIQUE, spec TEXT NOT NULL,
                    claimed_at REAL, stage_plan_hash TEXT,
                    PRIMARY KEY(study_id,id));
                CREATE TABLE IF NOT EXISTS calls(
                    study_id TEXT NOT NULL, trial_id TEXT NOT NULL, id TEXT NOT NULL,
                    intent_hash TEXT NOT NULL, reserve INTEGER NOT NULL,
                    known_tokens INTEGER, proof_hash TEXT,
                    PRIMARY KEY(study_id,trial_id,id),
                    FOREIGN KEY(study_id,trial_id) REFERENCES trials(study_id,id));
                CREATE TABLE IF NOT EXISTS tool_actions(
                    study_id TEXT NOT NULL, trial_id TEXT NOT NULL, id TEXT NOT NULL,
                    task_id TEXT NOT NULL, action TEXT NOT NULL, request_hash TEXT NOT NULL,
                    quote TEXT NOT NULL, started_at REAL NOT NULL, metrics TEXT,
                    outcome TEXT, result_hash TEXT,
                    PRIMARY KEY(study_id,trial_id,id),
                    FOREIGN KEY(study_id,trial_id,id) REFERENCES calls(study_id,trial_id,id));
                CREATE TABLE IF NOT EXISTS tool_measurements(
                    study_id TEXT NOT NULL, trial_id TEXT NOT NULL, id TEXT NOT NULL,
                    receipt_hash TEXT NOT NULL, body TEXT NOT NULL,
                    PRIMARY KEY(study_id,trial_id,id),
                    FOREIGN KEY(study_id,trial_id,id) REFERENCES tool_actions(study_id,trial_id,id));
                CREATE TABLE IF NOT EXISTS process_runs(
                    study_id TEXT NOT NULL, trial_id TEXT NOT NULL, slot INTEGER NOT NULL,
                    intent TEXT, intent_hash TEXT, dispatch TEXT, dispatch_hash TEXT,
                    observation TEXT, observation_hash TEXT, receipt TEXT, receipt_hash TEXT,
                    PRIMARY KEY(study_id,trial_id,slot),
                    FOREIGN KEY(study_id,trial_id) REFERENCES trials(study_id,id));
                CREATE TABLE IF NOT EXISTS producer_runs(
                    study_id TEXT NOT NULL, trial_id TEXT NOT NULL,
                    intent TEXT, intent_hash TEXT, receipt TEXT, receipt_hash TEXT,
                    PRIMARY KEY(study_id,trial_id),
                    FOREIGN KEY(study_id,trial_id) REFERENCES trials(study_id,id));
            ''')
        db.execute('BEGIN IMMEDIATE')
        yield db
        db.commit()
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()


def freeze(plan):
    """Register every slot and its destination before any stage is created."""
    plan = deepcopy(plan)
    need(type(plan) is dict and set(plan) in ({'study_id','trials'}, {'study_id','trials','allocation'}), 'exact study plan required')
    need(type(plan['study_id']) is str and re.fullmatch('[A-Za-z0-9_-]{1,80}', plan['study_id']), 'study identity')
    trials = plan['trials']
    need(type(trials) is list and 1 <= len(trials) <= 96, 'bounded study slots')
    keys = {'id','root','case_hash','architecture_hash','repeat','tasks_hash','source_pins_hash','policy','duration_seconds'}
    identities = set(); roots = set(); slots = set()
    from .closing import ClosingPolicy
    for trial in trials:
        need(type(trial) is dict and keys<=set(trial) and set(trial)-keys<={'tool_budget','measurement_protocol','process_envelope','producer_contract','architecture_contract'}, 'exact trial specification')
        from .architecture_contract import validate_trial
        validate_trial(trial)
        if 'producer_contract' in trial:
            from .registered_producer import validate_contract
            need('process_envelope' in trial,'standalone producer requires a process envelope')
            validate_contract(trial['producer_contract'])
        if 'process_envelope' in trial:
            from .process_envelope import validate as validate_process
            need('tool_budget' in trial,'process envelope requires the registered tool budget')
            validate_process(trial['process_envelope'])
        if 'tool_budget' in trial:
            from .tool_resources import validate_budget
            from .tool_measurements import VERSION as measurement_protocol
            validate_budget(trial['tool_budget'])
            need(trial.get('measurement_protocol',measurement_protocol)==measurement_protocol, 'new trial requires original measurement protocol')
            trial['measurement_protocol']=measurement_protocol
        else:
            need('measurement_protocol' not in trial, 'measurement protocol requires a tool budget')
        need(type(trial['id']) is str and re.fullmatch('[A-Za-z0-9_-]{1,80}', trial['id']), 'trial identity')
        need(type(trial['repeat']) is int and 1 <= trial['repeat'] <= 32, 'bounded predeclared repeat')
        need(type(trial['duration_seconds']) is int and 1 <= trial['duration_seconds'] <= 86400, 'bounded trial duration')
        for key in ('case_hash','architecture_hash','tasks_hash','source_pins_hash'):
            need(type(trial[key]) is str and re.fullmatch('[a-f0-9]{64}', trial[key]), 'frozen trial hash: '+key)
        ClosingPolicy(**trial['policy'])
        need(type(trial['root']) is str and Path(trial['root']).is_absolute(), 'absolute predeclared stage root')
        trial['root'] = _root(trial['root'])
        need(not Path(trial['root']).exists(), 'trial destination already exposed')
        slot = (trial['case_hash'],trial['architecture_hash'],trial['repeat'])
        need(trial['id'] not in identities and trial['root'] not in roots and slot not in slots, 'duplicate trial slot')
        identities.add(trial['id']); roots.add(trial['root']); slots.add(slot)
    from .study_allocation import validate
    validate(plan)
    # Directory, label and presentation order changes do not create a new study.
    semantic = sorted([{k:v for k,v in t.items() if k not in ('id','root')} for t in trials], key=serial)
    with _db(create=True) as db:
        need(db.execute('SELECT 1 FROM studies WHERE id=? OR semantic_hash=?',
             (plan['study_id'],digest(semantic))).fetchone() is None, 'study or equivalent opportunities already registered')
        need(not any(db.execute('SELECT 1 FROM trials WHERE root=?',(t['root'],)).fetchone() for t in trials), 'stage root already registered')
        cohorts = {_cohort(t) for t in trials}
        for old in db.execute('SELECT study_id,id FROM trials'):
            _, _, spec = _trial(db, old['study_id'], old['id'])
            need(_cohort(spec) not in cohorts, 'case-architecture repeat cohort already registered; no split, extension or replacement')
        db.execute('INSERT INTO studies VALUES(?,?,?,?)',(plan['study_id'],digest(semantic),serial(plan),digest(plan)))
        for t in trials:
            db.execute('INSERT INTO trials(study_id,id,root,spec) VALUES(?,?,?,?)',
                (plan['study_id'],t['id'],t['root'],serial(t)))
            if 'process_envelope' in t:
                db.executemany('INSERT INTO process_runs(study_id,trial_id,slot) VALUES(?,?,?)',
                    [(plan['study_id'],t['id'],i) for i in range(1,t['process_envelope']['budget']['launches']+1)])
            if 'producer_contract' in t:
                db.execute('INSERT INTO producer_runs(study_id,trial_id) VALUES(?,?)',(plan['study_id'],t['id']))
    return {'kind':VERSION,'study_id':plan['study_id'],'plan_hash':digest(plan),'slots':len(trials),
            'opportunity_rule':OPPORTUNITY_RULE,'full_stack_comparison_admitted':False}


def _trial(db, study_id, trial_id):
    study = db.execute('SELECT * FROM studies WHERE id=?',(study_id,)).fetchone()
    row = db.execute('SELECT * FROM trials WHERE study_id=? AND id=?',(study_id,trial_id)).fetchone()
    need(study is not None and row is not None, 'unregistered study trial')
    plan = json.loads(study['plan']); need(digest(plan)==study['plan_hash'], 'study plan drift')
    spec = json.loads(row['spec'])
    need(spec in plan['trials'] and row['root']==spec['root'], 'trial specification drift')
    return study, row, spec


def claim(study_id, trial_id):
    """A claimed slot never gets a new root or clock, including after failure."""
    with _db() as db:
        study, row, spec = _trial(db,study_id,trial_id)
        return _claim(db,study,row,spec)


def _claim(db, study, row, spec):
    # Check the whole allocation under the same write transaction as the claim.
    # Untouched slots retain their fixed grants; debt does not buy an empty run.
    need(row['claimed_at'] is None, 'trial slot already claimed; no restart or replacement')
    from .study_allocation import status as allocation_status
    allocation = allocation_status(db,row['study_id'])
    if allocation['integrated']:
        for resource in ('model','tool','process'):
            need(not allocation[resource+'_allocation_exhausted'],
                 'study '+resource+' headroom exhausted; no new stage claim')
    now = time.time()
    db.execute('UPDATE trials SET claimed_at=? WHERE study_id=? AND id=?',
               (now,row['study_id'],row['id']))
    return {'kind':VERSION,'study_id':row['study_id'],'trial_id':row['id'],'study_plan_hash':study['plan_hash'],
        'trial_hash':digest(spec),'root':row['root'],'claimed_at':now,
        'deadline_epoch':now+spec['duration_seconds']}


def _binding(study, row, spec):
    need(row['claimed_at'] is not None, 'study slot must be claimed before stage creation')
    return {'kind':VERSION,'study_id':row['study_id'],'trial_id':row['id'],'study_plan_hash':study['plan_hash'],
        'trial_hash':digest(spec),'root':row['root'],'claimed_at':row['claimed_at'],
        'deadline_epoch':row['claimed_at']+spec['duration_seconds']}


def create_stage(root, study_id, trial_id, *, policy, tasks, duration_seconds, provenance):
    """Ordinary-input CLI entry; validate mismatches before consuming a slot."""
    from dataclasses import asdict
    from .ledger import Ledger
    with _db() as db:
        study,row,spec = _trial(db,study_id,trial_id)
        need(_root(root)==row['root'], 'registered destination cannot change')
        need(digest(tasks)==spec['tasks_hash'] and asdict(policy)==spec['policy'], 'study inputs or policy changed')
        need(digest(provenance.get('source_pins'))==spec['source_pins_hash'], 'study source identity changed')
        need(duration_seconds==spec['duration_seconds'], 'registered duration cannot change')
        from .architecture_contract import verify_binding, validate_stage_entry
        verify_binding(spec, provenance)
        validate_stage_entry(provenance,producer_contract=spec.get('producer_contract'))
        binding = _claim(db,study,row,spec)
    return Ledger.create(root,policy=policy,tasks=tasks,deadline_epoch=binding['deadline_epoch'],
        provenance={**provenance,'study_trial':binding})


def verify_stage(root, plan, *, bind=False):
    """Called even without a binding, so deleting one cannot downgrade a claim."""
    supplied = plan['provenance'].get('study_trial')
    if not REGISTRY.is_file():
        need(supplied is None, 'canonical study registry missing')
        return
    with _db() as db:
        row = db.execute('SELECT * FROM trials WHERE root=?',(_root(root),)).fetchone()
        if row is None:
            from .study_descendants import resolve
            if resolve(db,root,plan,bind=bind) is not None:return
            need(supplied is None, 'stage copied outside its registered root')
            return
        study,row,spec = _trial(db,row['study_id'],row['id'])
        need(type(supplied) is dict and digest(supplied)==digest(_binding(study,row,spec)), 'required study binding missing or changed')
        need(digest(plan['tasks'])==spec['tasks_hash'] and plan['policy']==spec['policy'], 'study inputs or policy changed')
        need(digest(plan['provenance'].get('source_pins'))==spec['source_pins_hash'], 'study source identity changed')
        from .architecture_contract import verify_binding
        verify_binding(spec, plan['provenance'])
        need(plan['model']=='gpt-6-astra' and plan['effort']=='xhigh', 'study model request changed')
        need(plan['deadline_epoch']<=supplied['deadline_epoch'], 'study clock cannot be renewed')
        if bind and row['stage_plan_hash'] is None:
            db.execute('UPDATE trials SET stage_plan_hash=? WHERE study_id=? AND id=?',
                       (digest(plan),row['study_id'],row['id']))
        else:
            need(row['stage_plan_hash']==digest(plan), 'registered stage plan missing or changed')


def _call_binding(root, intent):
    plan = json.loads((Path(root)/'plan.json').read_text(encoding='utf-8'))
    need(digest(plan)==intent['plan_hash'], 'call plan identity changed')
    return plan['provenance'].get('study_trial')


def reserve_model(root, intent, reserve):
    """Commit shared exposure BEFORE the local call reservation may commit."""
    supplied = _call_binding(root,intent)
    if not REGISTRY.is_file():
        need(supplied is None, 'canonical study registry missing')
        return
    with _db() as db:
        row = db.execute('SELECT * FROM trials WHERE root=?',(_root(root),)).fetchone()
        stage_hash = row['stage_plan_hash'] if row is not None else None
        if row is None:
            from .study_descendants import resolve
            child=resolve(db,root,json.loads((Path(root)/'plan.json').read_text(encoding='utf-8')))
            if child is None:
                need(supplied is None, 'stage copied outside its registered root')
                return
            row,_,stage_hash=child
        study,row,spec = _trial(db,row['study_id'],row['id'])
        need('producer_contract' not in spec,'standalone producer has no model-call authority')
        need(digest(supplied)==digest(_binding(study,row,spec)), 'required study binding missing or changed')
        need(stage_hash==intent['plan_hash'], 'call study plan mismatch')
        calls = list(db.execute('SELECT * FROM calls WHERE study_id=? AND trial_id=?',(row['study_id'],row['id'])))
        need(not any(c['known_tokens'] is None for c in calls), 'unresolved shared model reservation; no replacement')
        need(time.time()<row['claimed_at']+spec['duration_seconds'], 'study trial deadline exhausted')
        exposure = sum(c['known_tokens'] for c in calls)
        need(type(reserve) is int and reserve>0 and len(calls)<spec['policy']['stage_calls']
             and exposure+reserve<=spec['policy']['stage_tokens'], 'shared study model budget exhausted')
        from .tool_measurements import validate_trial
        validate_trial(db,row['study_id'],row['id'],admission=True)
        from .study_allocation import admit_model
        admit_model(db,row['study_id'],intent)
        from .process_envelope import admit as admit_process
        admit_process(db,root,intent=intent)
        from .study_descendants import admit_model as admit_descendant_model, record_call
        admit_descendant_model(db,row,spec,intent)
        db.execute('INSERT INTO calls(study_id,trial_id,id,intent_hash,reserve) VALUES(?,?,?,?,?)',
            (row['study_id'],row['id'],intent['intent_id'],digest(intent),reserve))
        record_call(db,row,root,intent)


def settle_model(root, intent, receipt):
    """Record every verified paid completion, including overrun and failed work."""
    supplied = _call_binding(root,intent)
    if not REGISTRY.is_file():
        need(supplied is None, 'canonical study registry missing')
        return
    with _db() as db:
        row = db.execute('SELECT * FROM trials WHERE root=?',(_root(root),)).fetchone()
        if row is None:
            from .study_descendants import resolve
            child=resolve(db,root,json.loads((Path(root)/'plan.json').read_text(encoding='utf-8')))
            if child is None:
                need(supplied is None, 'stage copied outside its registered root')
                return
            row=child[0]
        study,row,spec = _trial(db,row['study_id'],row['id'])
        need(digest(supplied)==digest(_binding(study,row,spec)), 'required study binding missing or changed')
        c = db.execute('SELECT * FROM calls WHERE study_id=? AND trial_id=? AND id=?',
            (row['study_id'],row['id'],intent['intent_id'])).fetchone()
        need(c is not None and c['intent_hash']==digest(intent), 'completion lacks shared reservation')
        need(receipt['request_identity']['intent_id']==intent['intent_id'], 'shared receipt identity mismatch')
        need(all(type(receipt['usage'].get(k)) is int and receipt['usage'][k]>=0
                 for k in ('input_tokens','output_tokens')), 'shared completion usage missing')
        cost = receipt['usage']['input_tokens']+receipt['usage']['output_tokens']
        proof = digest({k:receipt[k] for k in ('request_identity','usage','artifact_sha256','response')})
        if c['known_tokens'] is not None:
            need(c['known_tokens']==cost and c['proof_hash']==proof, 'shared completion drift')
        else:
            db.execute('UPDATE calls SET known_tokens=?,proof_hash=? WHERE study_id=? AND trial_id=? AND id=?',
                (cost,proof,row['study_id'],row['id'],intent['intent_id']))
        from .study_descendants import settle_call
        settle_call(db,row,intent,receipt)


def snapshot(study_id):
    with _db() as db:
        study = db.execute('SELECT * FROM studies WHERE id=?',(study_id,)).fetchone()
        need(study is not None, 'unregistered study')
        trials = [dict(r) for r in db.execute('SELECT * FROM trials WHERE study_id=? ORDER BY id',(study_id,))]
        calls = [dict(r) for r in db.execute('SELECT * FROM calls WHERE study_id=? ORDER BY trial_id,id',(study_id,))]
        from .tool_measurements import validate_trial
        for trial in trials: validate_trial(db,study_id,trial['id'])
        from .process_envelope import trial_summary
        process_resources={trial['id']:trial_summary(db,study_id,trial['id']) for trial in trials}
        from .registered_producer import trial_summary as producer_summary
        producer_resources={trial['id']:producer_summary(db,study_id,trial['id']) for trial in trials}
        cases = {json.loads(t['spec'])['case_hash'] for t in trials}
        case_history = []
        # A new declared architecture is allowed, but it sees the same case's
        # canonical past attempts/costs. This is a reference view, not copied
        # charges; never add these all-case totals to the study's own totals.
        related = []
        for item in db.execute('SELECT study_id,id FROM trials ORDER BY study_id,id'):
            _, row, spec = _trial(db, item['study_id'], item['id'])
            if spec['case_hash'] in cases:
                related.append((row, spec))
        for case in sorted(cases):
            rows = [(row, spec) for row, spec in related if spec['case_hash']==case]
            case_calls = [dict(c) for row, _ in rows for c in db.execute(
                'SELECT * FROM calls WHERE study_id=? AND trial_id=? ORDER BY id', (row['study_id'], row['id']))]
            external = [c for c in case_calls if c['study_id']!=study_id]
            case_history.append({'case_hash':case,
                'trials':[{'study_id':row['study_id'],'trial_id':row['id'],
                    'architecture_hash':spec['architecture_hash'],'repeat':spec['repeat'],
                    'claimed_at':row['claimed_at']} for row, spec in rows],
                'calls':case_calls,
                'external_claimed_trials':sum(row['study_id']!=study_id and row['claimed_at'] is not None for row, _ in rows),
                'external_known_tokens':sum(c['known_tokens'] or 0 for c in external),
                'external_unresolved_reserve':sum(c['reserve'] for c in external if c['known_tokens'] is None),
                'all_known_tokens':sum(c['known_tokens'] or 0 for c in case_calls),
                'all_unresolved_reserve':sum(c['reserve'] for c in case_calls if c['known_tokens'] is None),
                'formal_freshness_verified':False})
        from .study_allocation import status as allocation_status
        from .study_descendants import snapshot as descendant_snapshot
        return {'study_id':study_id,'plan_hash':study['plan_hash'],'trials':trials,'calls':calls,
            **descendant_snapshot(db,study_id),
            'process_resources':process_resources,
            'producer_resources':producer_resources,
            'study_allocation':allocation_status(db,study_id),
            'known_tokens':sum(c['known_tokens'] or 0 for c in calls),
            'unresolved_reserve':sum(c['reserve'] for c in calls if c['known_tokens'] is None),
            'opportunity_rule':OPPORTUNITY_RULE,'case_history':case_history,
            'case_history_semantics':'Canonical referenced totals; overlap across study snapshots; do not sum. Declared case hashes do not verify scientific identity or fresh OOS data.',
            'full_stack_comparison_admitted':False,'tool_resource_accounting_integrated':False}
