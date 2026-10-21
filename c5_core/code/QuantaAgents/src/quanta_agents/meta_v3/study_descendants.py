"""Admitted extension stages spend their original trial, never another slot.

The canonical registry commits lineage before a child ledger is constructed.
Stage and call identities distinguish data scopes while every resource sum uses
the original study/trial keys. Old measurement identities remain unchanged.
"""
import json
from pathlib import Path
import time

from .ledger import digest, need, serial
from . import study_registry as sr

VERSION = 'same_trial_descendant_v1'


def _exists(db, table):
    return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is not None


def _tables(db):
    # execute, not executescript: retain the caller's BEGIN IMMEDIATE transaction.
    db.execute('''CREATE TABLE IF NOT EXISTS trial_descendants(
        root TEXT PRIMARY KEY, study_id TEXT NOT NULL, trial_id TEXT NOT NULL,
        parent_root TEXT NOT NULL, request_id TEXT NOT NULL, binding TEXT NOT NULL,
        expected_plan TEXT NOT NULL, stage_plan_hash TEXT,
        UNIQUE(parent_root,request_id),
        FOREIGN KEY(study_id,trial_id) REFERENCES trials(study_id,id))''')
    db.execute('''CREATE TABLE IF NOT EXISTS model_call_stages(
        study_id TEXT NOT NULL, trial_id TEXT NOT NULL, id TEXT NOT NULL,
        root TEXT NOT NULL, stage_plan_hash TEXT NOT NULL, task_id TEXT NOT NULL,
        final_attempted INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY(study_id,trial_id,id),
        FOREIGN KEY(study_id,trial_id,id) REFERENCES calls(study_id,trial_id,id))''')


def _read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def _shape(plan):
    return {k:plan[k] for k in ('policy','tasks','deadline_epoch','provenance','model','effort')}


def resolve(db, root, plan, *, bind=False):
    """Return the original trial plus this child's hash, or None for nonchildren."""
    record = (db.execute('SELECT * FROM trial_descendants WHERE root=?', (sr._root(root),)).fetchone()
              if _exists(db, 'trial_descendants') else None)
    supplied = plan['provenance'].get('study_descendant') if plan else None
    if record is None:
        need(supplied is None, 'unregistered descendant or copied stage root')
        return None
    study, trial, spec = sr._trial(db, record['study_id'], record['trial_id'])
    binding = json.loads(record['binding'])
    expected = json.loads(record['expected_plan'])
    need(plan is not None and supplied == binding and
         plan['provenance'].get('study_trial') == sr._binding(study, trial, spec),
         'required descendant study binding missing or changed')
    need(_shape(plan) == expected, 'registered descendant plan changed')
    need(binding['root'] == sr._root(root) and binding['parent_root'] == record['parent_root'] and
         binding['request_id'] == record['request_id'], 'descendant canonical lineage drift')
    intent = _read(binding['admission_intent_path'])
    need(digest(intent) == binding['admission_intent_hash'] and
         intent['request_id'] == binding['request_id'] and
         sr._root(intent['parent_root']) == record['parent_root'] and
         intent['parent_plan_hash'] == binding['parent_plan_hash'], 'descendant admission intent drift')
    from .architecture_contract import verify_binding
    verify_binding(spec, plan['provenance'])
    need(digest(plan['provenance'].get('source_pins')) == spec['source_pins_hash'], 'descendant study source identity changed')
    need(plan['deadline_epoch'] <= trial['claimed_at'] + spec['duration_seconds'], 'descendant trial clock renewed')
    if bind and record['stage_plan_hash'] is None:
        db.execute('UPDATE trial_descendants SET stage_plan_hash=? WHERE root=?', (digest(plan), sr._root(root)))
    else:
        need(record['stage_plan_hash'] == digest(plan), 'descendant stage plan missing or changed')
    return trial, spec, digest(plan)


def recorded_trial(db, root):
    """Cost recording survives local output damage, as for the original root."""
    if not _exists(db, 'trial_descendants'):return None
    row = db.execute('SELECT study_id,trial_id FROM trial_descendants WHERE root=?', (sr._root(root),)).fetchone()
    if row is None:return None
    _, trial, spec = sr._trial(db, row['study_id'], row['trial_id'])
    return trial, spec, {}


def register(parent_root, parent_plan, request_id, intent_path, child_root, *, tasks, policy, deadline_epoch, provenance):
    """Register only an already saved controller admission, without allocating."""
    if parent_plan['provenance'].get('study_trial') is None:return provenance
    from .tool_resources import _binding
    from .architecture_contract import verify_binding
    with sr._db() as db:
        parent = _binding(db, parent_root)
        need(parent is not None and digest(parent[2]) == digest(parent_plan), 'extension parent trial changed')
        trial, spec, _ = parent
        need('producer_contract' not in spec, 'standalone producer cannot acquire research descendants')
        intent_path = Path(intent_path).resolve()
        intent = _read(intent_path)
        need(intent['decision']['status'] == 'ready' and intent['request_id'] == request_id and
             sr._root(intent['parent_root']) == sr._root(parent_root) and
             intent['parent_plan_hash'] == digest(parent_plan), 'descendant requires its saved ready admission')
        need(sr._root(child_root) == sr._root(intent_path.parent/'stage'), 'descendant destination differs from admission')
        need(deadline_epoch <= min(parent_plan['deadline_epoch'], trial['claimed_at'] + spec['duration_seconds']),
             'descendant trial clock cannot be renewed')
        need(all(policy[k] <= spec['policy'][k] for k in ('stage_calls','task_calls','stage_tokens','task_tokens')) and
             all(policy[k] == spec['policy'][k] for k in ('call_reserve','closing_reserve','closing_seconds')),
             'descendant budget cannot exceed or weaken original trial policy')
        binding = {'kind':VERSION, 'root':sr._root(child_root), 'parent_root':sr._root(parent_root),
                   'parent_plan_hash':digest(parent_plan), 'request_id':request_id,
                   'admission_intent_path':str(intent_path), 'admission_intent_hash':digest(intent),
                   'canonical_trial_root':trial['root']}
        supplied = {**provenance, 'study_trial':parent_plan['provenance']['study_trial'], 'study_descendant':binding}
        verify_binding(spec, supplied)
        need(digest(supplied.get('source_pins')) == spec['source_pins_hash'], 'descendant study source identity changed')
        expected = {'policy':policy, 'tasks':tasks, 'deadline_epoch':deadline_epoch,
                    'provenance':supplied, 'model':parent_plan['model'], 'effort':parent_plan['effort']}
        _tables(db)
        old = db.execute('SELECT * FROM trial_descendants WHERE root=?', (sr._root(child_root),)).fetchone()
        if old is not None:
            need(old['binding'] == serial(binding) and old['expected_plan'] == serial(expected), 'descendant admission cannot be replaced')
            return supplied
        need(db.execute('SELECT 1 FROM trials WHERE root=?', (sr._root(child_root),)).fetchone() is None,
             'descendant cannot consume another trial slot')
        from .process_envelope import admit
        admit(db, parent_root)
        db.execute('INSERT INTO trial_descendants(root,study_id,trial_id,parent_root,request_id,binding,expected_plan) VALUES(?,?,?,?,?,?,?)',
                   (sr._root(child_root),trial['study_id'],trial['id'],sr._root(parent_root),request_id,serial(binding),serial(expected)))
        state = model_state(db, trial, spec)
        need(not state['unresolved'] and state['call_room'] >= state['unfinished_tasks'] and
             state['token_room'] >= state['unfinished_tasks'] * spec['policy']['closing_reserve'],
             'descendant cannot consume protected trial final reserves')
        return supplied


def record_call(db, trial, root, intent):
    _tables(db)
    db.execute('INSERT INTO model_call_stages(study_id,trial_id,id,root,stage_plan_hash,task_id) VALUES(?,?,?,?,?,?)',
               (trial['study_id'],trial['id'],intent['intent_id'],sr._root(root),intent['plan_hash'],intent['task_id']))


def settle_call(db, trial, intent, receipt):
    if not _exists(db, 'model_call_stages'):return
    row = db.execute('SELECT * FROM model_call_stages WHERE study_id=? AND trial_id=? AND id=?',
                     (trial['study_id'],trial['id'],intent['intent_id'])).fetchone()
    if row is None:return  # Preserve already frozen legacy measurement identity.
    need(row['stage_plan_hash'] == intent['plan_hash'] and row['task_id'] == intent['task_id'], 'call stage identity drift')
    final = int(receipt['response']['action'] == 'submit_research_report')
    db.execute('UPDATE model_call_stages SET final_attempted=? WHERE study_id=? AND trial_id=? AND id=?',
               (final,trial['study_id'],trial['id'],intent['intent_id']))


def call_stage_hash(db, row, original_hash):
    if not _exists(db, 'model_call_stages'):return original_hash
    value = db.execute('SELECT stage_plan_hash FROM model_call_stages WHERE study_id=? AND trial_id=? AND id=?',
                       (row['study_id'],row['trial_id'],row['id'])).fetchone()
    return value['stage_plan_hash'] if value else original_hash


def snapshot(db, study_id):
    children=[]
    if _exists(db,'trial_descendants'):
        for row in db.execute('SELECT * FROM trial_descendants WHERE study_id=? ORDER BY root',(study_id,)):
            expected=json.loads(row['expected_plan'])
            _,_,spec=sr._trial(db,study_id,row['trial_id'])
            children.append({'root':row['root'],'trial_id':row['trial_id'],'parent_root':row['parent_root'],
                             'request_id':row['request_id'],'stage_plan_hash':row['stage_plan_hash'],
                             'trial_case_hash':spec['case_hash'],
                             'child_case_hashes':{k:v['case_hash'] for k,v in expected['tasks'].items()},
                             'tasks_hash':digest(expected['tasks']),'deadline_epoch':expected['deadline_epoch'],
                             'new_trial_or_repeat':False})
    calls=([dict(row) for row in db.execute('SELECT * FROM model_call_stages WHERE study_id=? ORDER BY trial_id,id',(study_id,))]
           if _exists(db,'model_call_stages') else [])
    return {'descendant_stages':children,'call_stage_bindings':calls}


def model_state(db, trial, spec):
    children = (list(db.execute('SELECT * FROM trial_descendants WHERE study_id=? AND trial_id=?',
                                (trial['study_id'],trial['id']))) if _exists(db,'trial_descendants') else [])
    if not children:return {'integrated':False}
    parent = _read(Path(trial['root'])/'plan.json')
    need(digest(parent) == trial['stage_plan_hash'], 'canonical parent plan changed')
    stages = [(trial['root'], _shape(parent))] + [(c['root'],json.loads(c['expected_plan'])) for c in children]
    finished = {(r['root'],r['task_id']) for r in db.execute(
        'SELECT root,task_id FROM model_call_stages WHERE study_id=? AND trial_id=? AND final_attempted=1',
        (trial['study_id'],trial['id']))}
    unfinished = sum((root, task) not in finished for root, p in stages if time.time() < p['deadline_epoch'] for task in p['tasks'])
    calls = list(db.execute('SELECT known_tokens,reserve FROM calls WHERE study_id=? AND trial_id=?', (trial['study_id'],trial['id'])))
    return {'integrated':True, 'unfinished_tasks':unfinished, 'unresolved':any(c['known_tokens'] is None for c in calls),
            'call_room':spec['policy']['stage_calls']-len(calls),
            'token_room':spec['policy']['stage_tokens']-sum(c['known_tokens'] if c['known_tokens'] is not None else c['reserve'] for c in calls),
            'deadline_epoch':trial['claimed_at']+spec['duration_seconds']}


def _mode(state, spec):
    if not state['integrated']:return 'explore'
    if state['unresolved']:return 'blocked'
    finals=state['unfinished_tasks'];policy=spec['policy']
    if time.time() >= state['deadline_epoch'] or state['call_room'] < finals or state['token_room'] < finals*policy['closing_reserve']:
        return 'blocked'
    if (state['call_room'] <= finals or state['token_room'] < finals*policy['closing_reserve']+policy['call_reserve'] or
            time.time() >= state['deadline_epoch']-policy['closing_seconds']):return 'close_only'
    return 'explore'


def admit_model(db, trial, spec, intent):
    if _exists(db,'trial_descendants'):
        child=db.execute('SELECT binding,expected_plan FROM trial_descendants WHERE study_id=? AND trial_id=? AND stage_plan_hash=?',
                         (trial['study_id'],trial['id'],intent['plan_hash'])).fetchone()
        if child is not None:
            binding=json.loads(child['binding'])
            need(time.time()<json.loads(child['expected_plan'])['deadline_epoch'],'descendant local deadline exhausted')
            path=Path(binding['admission_intent_path']).with_name('decision.json')
            need(path.is_file(),'descendant admission decision incomplete')
            decision=_read(path)
            need(decision['status']=='ready' and decision['intent_hash']==binding['admission_intent_hash'] and
                 decision['child_plan_hash']==intent['plan_hash'],'descendant committed decision binding drift')
    mode = _mode(model_state(db,trial,spec),spec)
    need(mode != 'blocked', 'shared descendant model budget or unresolved reservation blocks dispatch')
    if mode == 'close_only':
        need(intent['mode'] == 'close_only' and intent['actions'] == ['submit_research_report'],
             'shared descendant final reserves allow only final calls')


def constrain(root, decision):
    from .closing import ClosingDecision
    from .tool_resources import _binding
    if not sr.REGISTRY.is_file():return decision
    with sr._db() as db:
        binding=_binding(db,root)
        if binding is None:return decision
        mode=_mode(model_state(db,binding[0],binding[1]),binding[1])
    if decision.mode in ('blocked','terminal_without_submission') or mode=='explore':return decision
    return ClosingDecision(mode,tuple(decision.reasons)+('shared_trial_descendant_'+mode,))
