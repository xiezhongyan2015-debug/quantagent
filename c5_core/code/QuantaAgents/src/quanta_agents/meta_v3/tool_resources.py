"""Canonical, opt-in tool application quotas and measured retained work.

Logical scan reservations are not physical IO. Timings exclude initialization,
model calls and child CPU. These producers alone do not admit full-stack studies.
"""
import json
import math
from pathlib import Path
import time

from .ledger import Ledger, digest, need, serial
from . import study_registry as sr

LIMITS = {'actions':512, 'candidates':256, 'scan_cells':134217728,
          'comparisons':4096, 'wall_ms':86400000, 'controller_cpu_ms':86400000,
          'retained_output_bytes':1073741824}
LOGICAL = ('actions', 'candidates', 'scan_cells', 'comparisons')
MEASURED = ('wall_ms', 'controller_cpu_ms', 'retained_output_bytes')


def validate_budget(budget):
    need(type(budget) is dict and set(budget)==set(LIMITS), 'exact tool resource budget')
    need(all(type(budget[k]) is int and 0<=budget[k]<=v for k,v in LIMITS.items()), 'bounded tool resource budget')
    need(all(budget[k]>0 for k in ('actions',)+MEASURED), 'positive tool application and measurement budget')


def _binding(db, root):
    path = Path(root)/'plan.json'
    plan = json.loads(path.read_text(encoding='utf-8')) if path.is_file() else None
    supplied = plan['provenance'].get('study_trial') if plan else None
    if db is None:
        need(supplied is None, 'canonical study registry missing')
        return None
    row = db.execute('SELECT * FROM trials WHERE root=?', (sr._root(root),)).fetchone()
    if row is None:
        from .study_descendants import resolve
        child=resolve(db,root,plan)
        if child is not None:return child[0],child[1],plan
        need(supplied is None, 'tool stage copied outside registered root')
        return None
    study, row, spec = sr._trial(db, row['study_id'], row['id'])
    need(plan is not None and digest(supplied)==digest(sr._binding(study,row,spec)), 'tool study binding missing or changed')
    need(row['stage_plan_hash']==digest(plan), 'tool stage plan changed')
    return row, spec, plan


def _status(db, binding):
    if binding is None or 'tool_budget' not in binding[1]:
        return {'integrated':False, 'full_stack_comparison_admitted':False}
    row, spec, plan = binding
    from .tool_measurements import protocol, VERSION
    measurement_protocol=protocol(db,row['study_id'],row['id'])
    entries = [dict(x) for x in db.execute('SELECT * FROM tool_actions WHERE study_id=? AND trial_id=? ORDER BY id',
               (row['study_id'], row['id']))]
    used = {k:0 for k in LIMITS}
    for entry in entries:
        from .tool_measurements import saved
        receipt=saved(db,entry)
        entry['quote'] = json.loads(entry['quote'])
        entry['metrics'] = json.loads(entry['metrics']) if entry['metrics'] is not None else None
        entry['anchored_metrics']=receipt['metrics'] if receipt else None
        for k in LOGICAL: used[k] += entry['quote'][k]
        measured=entry['metrics'] if entry['metrics'] is not None else entry['anchored_metrics']
        if measured is not None:
            for k in MEASURED: used[k] += measured[k]
    from .study_descendants import model_state
    shared_model=model_state(db,row,spec)
    return {'integrated':True, 'scope':'this registered trial across all its tasks and admitted descendant scopes',
        'measurement_protocol':measurement_protocol,'measurement_protocol_current':measurement_protocol==VERSION,
        'budget':spec['tool_budget'], 'used':used, 'entries':entries,
        'unresolved':sum(x['metrics'] is None for x in entries),
        'unknown_measurements':sum(x['metrics'] is None and x['anchored_metrics'] is None for x in entries),
        'closing_time_reached':time.time()>=min(plan['deadline_epoch'],row['claimed_at']+spec['duration_seconds'])-spec['policy']['closing_seconds'],
        'full_stack_comparison_admitted':False,
        **({'shared_model_resources':{**shared_model,'frozen_policy':spec['policy']}} if shared_model['integrated'] else {})}


def status(root):
    if not sr.REGISTRY.is_file():
        return _status(None, _binding(None, root))
    with sr._db() as db:
        return _status(db, _binding(db, root))


def allowed(root, actions):
    from .process_envelope import filter_actions
    actions=filter_actions(root,actions)
    state = status(root)
    if not state['integrated']: return tuple(actions)
    if not state['measurement_protocol_current']: return ()
    used, budget = state['used'], state['budget']
    blocked = state['unresolved'] or state['closing_time_reached'] or any(used[k]>=budget[k] for k in ('actions',)+MEASURED)
    def permitted(action):
        if action=='submit_research_report': return True
        if blocked: return False
        if action in ('develop_strategy','register_batch') and used['candidates']>=budget['candidates']: return False
        if action in ('develop_strategy','register_batch','diagnose_horizons') and used['scan_cells']>=budget['scan_cells']: return False
        if action in ('diagnose_horizons','diagnose_execution','register_experiment') and used['comparisons']>=budget['comparisons']: return False
        return True
    return tuple(a for a in actions if permitted(a))


def quote(case, action, args):
    """Bounded logical requests, including invalid and duplicate programs."""
    q = dict.fromkeys(LOGICAL, 0); q['actions'] = 1
    symbols = len(case['decision_fixture']['codes'])
    cells = symbols*len(case['decision_fixture']['calendar'])
    if action=='develop_strategy':
        q.update(candidates=1, scan_cells=cells*6)
    elif action=='diagnose_horizons':
        hs = args.get('horizons')
        count = min(3,len(hs)) if type(hs) is list and hs else 3
        q.update(comparisons=count*4, scan_cells=cells*(2+count*(4+symbols)))
    elif action=='register_batch':
        families = args.get('families')
        need(type(families) is list and 1<=len(families)<=4, 'bounded batch quote families')
        for family in families:
            need(type(family) is dict and type(family.get('parameters')) is dict and len(family['parameters'])<=4, 'bounded batch quote axes')
            axes = family['parameters'].values()
            need(all(type(v) is list and 1<=len(v)<=8 for v in axes), 'bounded batch quote values')
            count = math.prod(len(v) for v in axes)
            program = family.get('program_template')
            factors = program.get('factors') if type(program) is dict else None
            q['candidates'] += count
            q['scan_cells'] += count*cells*((len(factors) if type(factors) is list else 0)+2)
        need(q['candidates']<=32, 'expanded batch quote exceeds32 before computation')
        comparisons = args.get('comparisons')
        q['comparisons'] = len(comparisons) if type(comparisons) is list else 0
    elif action=='diagnose_execution':
        ids = args.get('evidence_ids')
        q['comparisons'] = min(4,len(ids)) if type(ids) is list else 4
    elif action=='register_experiment':
        q['comparisons'] = 1
    return q


def _reserve(tools, call_id, action, args):
    # Local read/lock finishes before the shared transaction starts.
    call = Ledger(tools.root).call(call_id)
    need(call['status']=='applying' and call['task_id']==tools.task_id, 'tool requires its applying model call')
    response = call['receipt']['response']
    need(response['action']==action and digest(json.loads(response['arguments_json']))==digest(args), 'tool request differs from saved model response')
    with sr._db() as db:
        binding = _binding(db, tools.root)
        need(binding is not None and 'tool_budget' in binding[1], 'tool budget binding disappeared')
        row, spec, plan = binding
        from .tool_measurements import protocol
        protocol(db,row['study_id'],row['id'],admission=True)
        task = plan['tasks'][tools.task_id]
        need(call['intent']['plan_hash']==digest(plan),'tool model call belongs to another stage')
        need(task['case_hash']==digest(tools.case)==digest(task['case']), 'tool case differs from frozen input')
        state = _status(db, binding)
        need(not state['unresolved'], 'unresolved tool application; no replacement')
        need(action in allowed_from_state(state, (action,)), 'tool resource admission exhausted')
        q = quote(tools.case, action, args)
        need(all(state['used'][k]+q[k]<=spec['tool_budget'][k] for k in LOGICAL), 'tool logical resource budget exhausted')
        model = db.execute('SELECT * FROM calls WHERE study_id=? AND trial_id=? AND id=?',
            (row['study_id'],row['id'],call_id)).fetchone()
        need(model is not None and model['known_tokens'] is not None, 'tool lacks settled canonical model call')
        proof = digest({k:call['receipt'][k] for k in ('request_identity','usage','artifact_sha256','response')})
        need(model['intent_hash']==digest(call['intent']) and model['proof_hash']==proof, 'tool canonical receipt binding changed')
        from .study_allocation import admit_tool
        admit_tool(db,row['study_id'])
        from .process_envelope import admit as admit_process
        admit_process(db,tools.root)
        if action=='execute_batch':
            prior = db.execute('SELECT * FROM tool_actions WHERE study_id=? AND trial_id=? AND id=?',
                (row['study_id'],row['id'],args.get('registration_evidence_id'))).fetchone()
            need(prior is not None and prior['task_id']==tools.task_id and prior['action']=='register_batch'
                 and prior['outcome']=='returned' and prior['metrics'] is not None, 'batch lacks metered registration')
        db.execute('INSERT INTO tool_actions(study_id,trial_id,id,task_id,action,request_hash,quote,started_at) VALUES(?,?,?,?,?,?,?,?)',
            (row['study_id'],row['id'],call_id,tools.task_id,action,digest(response),serial(q),time.time()))
        from .tool_measurements import identity
        return identity(db,db.execute('SELECT * FROM tool_actions WHERE study_id=? AND trial_id=? AND id=?',
            (row['study_id'],row['id'],call_id)).fetchone())


def allowed_from_state(state, actions):
    used, budget = state['used'], state['budget']
    if state['unresolved'] or state['closing_time_reached'] or any(used[k]>=budget[k] for k in ('actions',)+MEASURED):
        return tuple(a for a in actions if a=='submit_research_report')
    return tuple(actions)


def _settle(root, call_id, metrics, outcome, result_hash):
    need(all(type(metrics[k]) is int and metrics[k]>=0 for k in MEASURED), 'invalid measured tool resources')
    with sr._db() as db:
        binding = _binding(db,root)
        need(binding is not None and 'tool_budget' in binding[1], 'tool budget binding disappeared')
        row = binding[0]
        from .tool_measurements import protocol
        protocol(db,row['study_id'],row['id'],admission=True)
        current = db.execute('SELECT * FROM tool_actions WHERE study_id=? AND trial_id=? AND id=?',
            (row['study_id'],row['id'],call_id)).fetchone()
        need(current is not None, 'tool completion missing')
        from .tool_measurements import saved
        receipt=saved(db,current,required=True)
        need(metrics==receipt['metrics'] and outcome==receipt['outcome'] and result_hash==receipt['result_hash'], 'settlement differs from original measurement')
        if current['metrics'] is not None: return False
        # All measured overrun is retained; budget is not a process-kill promise.
        db.execute('UPDATE tool_actions SET metrics=?,outcome=?,result_hash=? WHERE study_id=? AND trial_id=? AND id=?',
            (serial(metrics),outcome,result_hash,row['study_id'],row['id'],call_id))
        return True


def _bytes(paths):
    return sum(p.stat().st_size for base in paths if base.exists() for p in base.rglob('*') if p.is_file())


def execute(tools, call_id, action, args, producer):
    if not status(tools.root)['integrated']:
        return producer(call_id,action,args)
    intent=_reserve(tools,call_id,action,args)
    paths = [tools.folder/call_id]
    if action=='register_batch': paths.append(tools.batch_folder/call_id)
    elif action=='execute_batch': paths.append(tools.batch_folder/args['registration_evidence_id'])
    before = _bytes(paths)
    started, cpu = time.perf_counter(), time.process_time()
    outcome, result_hash = 'returned', None
    try:
        result = producer(call_id,action,args)
        result_hash = digest(result)
        return result
    except Exception as exc:
        outcome, result_hash = 'raised', digest({'type':type(exc).__name__,'message':str(exc)})
        raise
    finally:
        # BaseException/process death before a trustworthy end remains unknown.
        import sys
        exception = sys.exc_info()[1]
        if exception is None or isinstance(exception, Exception):
            from . import tool_measurements
            outputs=tool_measurements.inventory(tools.root,paths)
            metrics = {'wall_ms':math.ceil((time.perf_counter()-started)*1000),
                'controller_cpu_ms':math.ceil((time.process_time()-cpu)*1000),
                'retained_output_bytes':max(0,sum(x['bytes'] for x in outputs)-before)}
            tool_measurements.capture(tools.root,intent,metrics,outcome,result_hash,before,outputs)
            _settle(tools.root,call_id,metrics,outcome,result_hash)
