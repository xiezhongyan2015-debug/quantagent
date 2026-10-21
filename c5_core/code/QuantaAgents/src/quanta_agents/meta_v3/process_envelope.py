"""Frozen experiment jobs and original whole-process-tree cost receipts.

The common supervisor is outside the measured experiment. Tool timings are
overlapping diagnostics and must not be added to these cumulative job costs.
"""
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import time
from uuid import uuid4

from .ledger import AdmissionBlocked, digest, need, serial
from . import study_registry as sr, tool_resources as tr

VERSION='windows_job_experiment_envelope_v1'
SESSION_ENV='QUANTA_V3_PROCESS_SESSION'
LIMITS={'launches':8,'cpu_ms':86400000,'io_transfer_bytes':1099511627776}
GRANT_FIELDS=tuple(LIMITS)


def validate(config):
    need(type(config) is dict and set(config)=={'version','entrypoint','entrypoint_sha256','arguments','budget'},'exact process envelope')
    need(config['version']==VERSION,'process envelope version')
    path=Path(config['entrypoint'])
    need(path.is_absolute() and path.is_file() and path.suffix=='.py','frozen Python controller entrypoint')
    need(hashlib.sha256(path.read_bytes()).hexdigest()==config['entrypoint_sha256'],'process entrypoint source drift')
    need(type(config['arguments']) is list and len(config['arguments'])<=32 and
         all(type(x) is str and len(x)<=8192 and '\0' not in x for x in config['arguments']),'bounded process arguments')
    b=config['budget']
    need(type(b) is dict and set(b)==set(LIMITS)|{'closing_cpu_ms','closing_io_transfer_bytes'},'exact process budget')
    need(all(type(b[k]) is int and 0<b[k]<=v for k,v in LIMITS.items()),'bounded process grant')
    need(all(type(b['closing_'+k]) is int and 0<=b['closing_'+k]<b[k] for k in ('cpu_ms','io_transfer_bytes')),'process closing reserve')


def _binding(root,db):return tr._binding(db,root)


def _recorded_binding(root,db):
    """Original cost storage does not depend on mutable experiment outputs."""
    row=db.execute('SELECT study_id,id FROM trials WHERE root=?',(sr._root(root),)).fetchone()
    if row is None:
        from .study_descendants import recorded_trial
        return recorded_trial(db,root) or _binding(root,db)
    _,trial,spec=sr._trial(db,row['study_id'],row['id'])
    return trial,spec,{}


def _slots(db,binding):
    trial,spec,plan=binding
    if 'process_envelope' not in spec:return []
    need(db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='process_runs'").fetchone() is not None,'canonical process journal missing')
    rows=[dict(r) for r in db.execute('SELECT * FROM process_runs WHERE study_id=? AND trial_id=? ORDER BY slot',
        (trial['study_id'],trial['id']))]
    need([r['slot'] for r in rows]==list(range(1,spec['process_envelope']['budget']['launches']+1)),
         'predeclared process slots missing or changed')
    for r in rows:
        for name in ('intent','dispatch','observation','receipt'):
            value=json.loads(r[name]) if r[name] is not None else None
            need((value is None and r[name+'_hash'] is None) or (value is not None and digest(value)==r[name+'_hash']),
                 'canonical process '+name+' drift')
            r[name]=value
        intent=r['intent']
        if intent is None:
            need(all(r[k] is None for k in ('dispatch','observation','receipt')),'unclaimed process slot has work')
            continue
        need(intent['kind']==VERSION and intent['study_id']==trial['study_id'] and intent['trial_id']==trial['id']
             and intent['slot']==r['slot'] and intent['stage_plan_hash']==trial['stage_plan_hash']
             and intent['envelope']==spec['process_envelope'] and intent['root']==str(Path(trial['root']).resolve()),'process intent binding drift')
        need(intent['deadline_epoch']==trial['claimed_at']+spec['duration_seconds'],'process deadline changed')
        if r['dispatch'] is not None:
            need(r['dispatch']['job_name']==intent['job_name'] and r['dispatch']['assigned_before_resume'] is True,'process dispatch binding drift')
        if r['observation'] is not None:_metrics(r['observation'])
        if r['receipt'] is not None:
            receipt=r['receipt']
            need(r['dispatch'] is not None and receipt['intent_hash']==r['intent_hash'] and receipt['dispatch_hash']==r['dispatch_hash'],
                 'original process receipt binding drift')
            _receipt(receipt)
            _monotonic(r['observation'],receipt['metrics'])
    return rows


def _metrics(value,*,complete=False):
    keys={'user_cpu_100ns','kernel_cpu_100ns','cpu_ms','read_ops','write_ops','other_ops','read_bytes','write_bytes','other_bytes',
          'io_transfer_bytes','total_processes','active_processes','processes_terminated_by_limit'}
    need(type(value) is dict and set(value)==keys and all(type(v) is int and v>=0 for v in value.values()),'exact process counters')
    need(value['cpu_ms']==math.ceil((value['user_cpu_100ns']+value['kernel_cpu_100ns'])/10000)
         and value['io_transfer_bytes']==sum(value[k] for k in ('read_bytes','write_bytes','other_bytes')),'process counter totals changed')
    if complete:need(value['active_processes']==0 and value['total_processes']>=1,'process tree not observed complete')


def _receipt(value):
    keys={'kind','intent_hash','dispatch_hash','metrics','exit_code','stop_reason','ended_at','wall_ms',
          'supervisor_outside_measured_experiment','io_is_physical_disk_bytes','formal_target_success'}
    need(type(value) is dict and set(value)==keys and value['kind']==VERSION,'exact original process receipt')
    need(type(value['exit_code']) is int and 0<=value['exit_code']<2**32 and value['stop_reason'] in (None,'frozen_deadline','process_resource_ceiling'),
         'process exit observation missing')
    need(type(value['wall_ms']) is int and value['wall_ms']>=0 and type(value['ended_at']) in (int,float)
         and math.isfinite(value['ended_at']),'process completion time missing')
    need(value['supervisor_outside_measured_experiment'] is True and value['io_is_physical_disk_bytes'] is False
         and value['formal_target_success'] is False,'process receipt scope changed')
    _metrics(value['metrics'],complete=True)


def _monotonic(before,after):
    if before is not None:
        need(all(after[k]>=before[k] for k in before if k!='active_processes'),'original process counters went backwards')


def trial_summary(db,study_id,trial_id):
    _,row,spec=sr._trial(db,study_id,trial_id)
    if 'process_envelope' not in spec:return {'integrated':False,'full_stack_comparison_admitted':False}
    return _summary(db,(row,spec,{}))


def _summary(db,binding):
    if binding is None or 'process_envelope' not in binding[1]:return {'integrated':False,'full_stack_comparison_admitted':False}
    rows=_slots(db,binding);known=dict.fromkeys(('cpu_ms','io_transfer_bytes'),0)
    for r in rows:
        if r['receipt'] is not None:
            for k in known:known[k]+=r['receipt']['metrics'][k]
    return {'integrated':True,'version':VERSION,'budget':binding[1]['process_envelope']['budget'],
        'launches_used':sum(r['intent'] is not None for r in rows),'known_finished':known,
        'unfinished_slots':[r['slot'] for r in rows if r['intent'] is not None and r['receipt'] is None],
        'runs':rows,'cpu_io_are_not_additive_with_tool_measurements':True,
        'io_semantics':'OS IO transfer counters, including exited job processes; not physical disk bytes or remote compute',
        'supervisor_outside_measured_experiment':True,'full_stack_comparison_admitted':False}


def status(root):
    if not sr.REGISTRY.is_file():return _summary(None,_binding(root,None))
    with sr._db() as db:return _summary(db,_recorded_binding(root,db))


def public_status(root):
    if not sr.REGISTRY.is_file():return status(root)
    with sr._db() as db:
        state=_current(db,_binding(root,db))
        return {k:v for k,v in state.items() if k not in ('runs','live')}


def filter_actions(root,actions):
    if not sr.REGISTRY.is_file():_binding(root,None);return tuple(actions)
    with sr._db() as db:
        binding=_binding(root,db)
        if binding is None or 'process_envelope' not in binding[1]:return tuple(actions)
        try:mode=_mode(_current(db,binding))
        except AdmissionBlocked:return ()
    return tuple(actions) if mode=='explore' else tuple(a for a in actions if mode=='close_only' and a=='submit_research_report')


def _current(db,binding):
    state=_summary(db,binding)
    if not state['integrated']:return state
    candidates=[r for r in state['runs'] if r['intent'] is not None and r['receipt'] is None]
    need(len(candidates)==1,'a single registered live process run is required')
    row=candidates[0]
    need(row['dispatch'] is not None and os.environ.get(SESSION_ENV)==row['intent']['session_id'],
         'model or tool requires its supervised process session')
    from .windows_job import own_measurement
    try:live=own_measurement(row['intent']['job_name'])
    except OSError as exc:raise AdmissionBlocked('registered process job unavailable; original cost remains unknown') from exc
    _metrics(live)
    state['live']=live
    state['used']={k:state['known_finished'][k]+live[k] for k in state['known_finished']}
    return state


def _mode(state):
    if not state['integrated']:return 'explore'
    if any(state['used'][k]>=state['budget'][k] for k in state['used']):return 'blocked'
    if any(state['used'][k]>=state['budget'][k]-state['budget']['closing_'+k] for k in state['used']):return 'close_only'
    return 'explore'


def constrain(root,decision):
    from .closing import ClosingDecision
    from .study_descendants import constrain as constrain_descendants
    decision=constrain_descendants(root,decision)
    if not sr.REGISTRY.is_file():_binding(root,None);return decision
    with sr._db() as db:
        binding=_binding(root,db)
        if binding is None or 'process_envelope' not in binding[1]:return decision
        try:state=_current(db,binding)
        except AdmissionBlocked as exc:return ClosingDecision('blocked',(str(exc),))
        mode=_mode(state)
        if decision.mode in ('blocked','terminal_without_submission'):return decision
        return decision if mode=='explore' else ClosingDecision(mode,tuple(decision.reasons)+('process_resource_'+mode,))


def admit(db,root,*,intent=None):
    state=_current(db,_binding(root,db))
    mode=_mode(state)
    need(mode!='blocked','process resource budget exhausted')
    if mode=='close_only':
        need(intent is not None and intent['mode']=='close_only' and intent['actions']==['submit_research_report'],
             'process closing reserve allows only a final model call')


def inside(root):
    if not os.environ.get(SESSION_ENV):return False
    with sr._db() as db:
        need(_current(db,_binding(root,db))['integrated'],'process session is not registered for this stage')
    return True


def reserve(root):
    root=Path(root).resolve()
    with sr._db() as db:
        binding=_binding(root,db);state=_summary(db,binding)
        need(state['integrated'],'supervision requires a frozen process envelope')
        trial,spec,plan=binding;validate(spec['process_envelope'])
        if sr._root(root)!=trial['root']:
            root=Path(trial['root']).resolve()
            plan=json.loads((root/'plan.json').read_text(encoding='utf-8'))
            need(digest(plan)==trial['stage_plan_hash'],'canonical process controller plan changed')
        need(not state['unfinished_slots'],'unresolved process run; no replacement launch')
        need(state['launches_used']<state['budget']['launches'],'frozen process launch slots exhausted')
        need(all(state['known_finished'][k]<state['budget'][k] for k in state['known_finished']),'whole-trial process resources exhausted')
        need(time.time()<plan['deadline_epoch'],'process trial deadline exhausted')
        from .study_allocation import admit_tool, status as allocation_status
        if 'producer_contract' in spec:
            # This BEGIN IMMEDIATE transaction also writes the process intent.
            # A healthy outer dispatch check cannot authorize a later start
            # after a peer's already-committed model/tool overrun.
            admit_tool(db,trial['study_id'])
        else:
            # Preserve ordinary research's separate final-only closing policy.
            allocation=allocation_status(db,trial['study_id'])
            need(not allocation.get('process_allocation_exhausted',False),'study process headroom exhausted')
        slot=next(r['slot'] for r in state['runs'] if r['intent'] is None)
        session=uuid4().hex
        value={'kind':VERSION,'study_id':trial['study_id'],'trial_id':trial['id'],'slot':slot,
            'session_id':session,'job_name':'Local\\quanta_v3_'+session,'root':str(root),
            'stage_plan_hash':trial['stage_plan_hash'],'envelope':spec['process_envelope'],
            'deadline_epoch':plan['deadline_epoch'],'reserved_at':time.time()}
        db.execute('UPDATE process_runs SET intent=?,intent_hash=? WHERE study_id=? AND trial_id=? AND slot=?',
            (serial(value),digest(value),trial['study_id'],trial['id'],slot))
    return value,state['known_finished']


def _write(root,intent,kind,value):
    need(kind in ('dispatch','observation','receipt'),'process record kind')
    with sr._db() as db:
        rows=_slots(db,_recorded_binding(root,db));row=next(r for r in rows if r['slot']==intent['slot'])
        need(row['intent']==intent,'process record belongs to another launch')
        if row[kind] is not None and kind!='observation':
            need(row[kind]==value,'original process record cannot be replaced');return False
        need(row['receipt'] is None,'closed process run cannot change')
        if kind=='observation':
            _metrics(value);_monotonic(row['observation'],value)
        if kind=='receipt':
            need(value['intent_hash']==row['intent_hash'] and value['dispatch_hash']==row['dispatch_hash'],'process completion binding changed')
            _receipt(value);_monotonic(row['observation'],value['metrics'])
        db.execute(f'UPDATE process_runs SET {kind}=?,{kind}_hash=? WHERE study_id=? AND trial_id=? AND slot=?',
            (serial(value),digest(value),intent['study_id'],intent['trial_id'],intent['slot']))
    return True


def supervise(root):
    """One bounded predeclared launch; never retry an uncertain start."""
    from .windows_job import job
    from .research_tools import save_once
    need(os.name=='nt','Windows process supervision is unavailable on this platform')
    root=Path(root).resolve()
    # A descendant uses the same frozen controller entrypoint and launch pool;
    # a controller already inside that job calls the child runtime directly.
    with sr._db() as db:
        binding=_binding(root,db)
        if binding is not None:root=Path(binding[0]['root']).resolve()
    intent,known=reserve(root)
    folder=root/'process_runs'/str(intent['slot']);folder.mkdir(parents=True,exist_ok=False)
    save_once(folder/'intent.json',intent)
    config=intent['envelope'];budget=config['budget'];reason=None
    env=dict(os.environ);env[SESSION_ENV]=intent['session_id']
    env['PYTHONPATH']=str(Path(__file__).resolve().parents[2])
    argv=[sys.executable,config['entrypoint'],*config['arguments']]
    with job(intent['job_name']) as owned:
        def dispatched(value):
            _write(root,intent,'dispatch',value);save_once(folder/'dispatch.json',value)
        owned.start(argv,cwd=sr.ROOT,env=env,log_path=folder/'worker.log',on_suspended=dispatched)
        last_saved=0
        while True:
            metrics=owned.measurement();exit_code=owned.poll()
            if exit_code is not None and metrics['active_processes']==0:break
            if reason is None:
                if time.time()>=intent['deadline_epoch']:reason='frozen_deadline'
                elif any(known[k]+metrics[k]>=budget[k] for k in known):reason='process_resource_ceiling'
                if reason is not None:owned.terminate()
            if time.monotonic()-last_saved>=1:
                _write(root,intent,'observation',metrics);last_saved=time.monotonic()
            time.sleep(.1)
        ended=time.time();metrics=owned.measurement()
        rows=status(root)['runs'];row=next(r for r in rows if r['slot']==intent['slot'])
        receipt={'kind':VERSION,'intent_hash':digest(intent),'dispatch_hash':row['dispatch_hash'],
            'metrics':metrics,'exit_code':exit_code,'stop_reason':reason,'ended_at':ended,
            'wall_ms':math.ceil((time.perf_counter()-owned.clock)*1000),
            'supervisor_outside_measured_experiment':True,'io_is_physical_disk_bytes':False,
            'formal_target_success':False}
        _write(root,intent,'receipt',receipt)
        save_once(folder/'receipt.json',receipt)
    return receipt
