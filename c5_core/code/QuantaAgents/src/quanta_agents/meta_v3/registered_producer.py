"""One-shot standalone controls under canonical study and native process costs.

Planning/setup outside the worker remains separately disclosed. The opted-in
real fixed arm may select a development candidate, never a model final or
financial success. The old synthetic producer protocol keeps its semantics.
"""
import json
import math
from copy import deepcopy
from pathlib import Path
import re
import time

from . import process_envelope as pe, study_registry as sr
from .ledger import digest, need, serial

VERSION='registered_template_control_producer_v1'
REAL_VERSION='registered_real_fixed_template_producer_v1'


def make_real_contract(space, *, count, seed):
    from .generator_controls import selection_policy
    value={'kind':REAL_VERSION,'arm':'fixed','space':deepcopy(space),
        'count':count,'seed':seed,'selection':selection_policy()}
    validate_contract(value)
    return value


def validate_contract(c):
    from .generator_controls import _space, validate_selection
    real=type(c) is dict and c.get('kind')==REAL_VERSION
    keys={'kind','arm','space','count','seed'}|({'selection'} if real else set())
    need(type(c) is dict and set(c)==keys,'exact standalone producer contract')
    need((real and c['arm']=='fixed') or (c['kind']==VERSION and c['arm'] in ('fixed','random')),
         'supported standalone producer kind/arm')
    if real:validate_selection(c['selection'])
    # Registration/status can run outside the worker. Validate only metadata;
    # candidate ordering and materialization belong to the supervised job.
    total=_space(c['space'])
    need(type(c['count']) is int and 1<=c['count']<=min(32,total),'candidate count exceeds domain')
    need(type(c['seed']) is str and re.fullmatch('[a-f0-9]{64}',c['seed']),'explicit 256bit seed required')


def _binding(db,root):
    b=pe._binding(root,db)
    need(b is not None and 'producer_contract' in b[1],'registered standalone producer required for new control execution')
    row,spec,plan=b;validate_contract(spec['producer_contract'])
    need(set(plan['tasks'])=={'producer'},'standalone producer task identity')
    task=plan['tasks']['producer']
    need(task['case_hash']==spec['case_hash']==digest(task['case']),'standalone producer case changed')
    if spec['producer_contract']['kind']==REAL_VERSION:
        from .architecture_contract import make, verify_binding
        from .generator_controls import validate_development_case
        need(plan['provenance'].get('architecture_contract')==make('fixed_template_search'),
             'real fixed producer requires its frozen architecture contract')
        verify_binding(spec,plan['provenance'])
        validate_development_case(task['case'])
    return b


def _record(db,b):
    row,spec,plan=b
    need(db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='producer_runs'").fetchone() is not None,'canonical producer journal missing')
    r=db.execute('SELECT * FROM producer_runs WHERE study_id=? AND trial_id=?',(row['study_id'],row['id'])).fetchone()
    need(r is not None,'predeclared producer slot missing')
    result=dict(r)
    for key in ('intent','receipt'):
        value=json.loads(result[key]) if result[key] is not None else None
        need((value is None and result[key+'_hash'] is None) or (value is not None and digest(value)==result[key+'_hash']),'canonical producer '+key+' changed')
        result[key]=value
    if result['intent'] is not None:
        intent=result['intent']
        need(set(intent)=={'kind','study_id','trial_id','stage_plan_hash','contract_hash','process_intent_hash',
            'started_at','mode','reserved_candidates','reserved_scan_cells','model_calls_authorized'},'producer intent schema changed')
        need(intent['kind']==VERSION and intent['study_id']==row['study_id'] and intent['trial_id']==row['id']
             and intent['stage_plan_hash']==row['stage_plan_hash'] and intent['contract_hash']==digest(spec['producer_contract']),
             'producer original intent binding changed')
        need(intent['mode'] in ('explore','close_only') and intent['model_calls_authorized']==0 and
             type(intent['started_at']) in (int,float) and math.isfinite(intent['started_at']) and
             row['claimed_at']<=intent['started_at']<=row['claimed_at']+spec['duration_seconds'],
             'producer original mode or time changed')
        need(intent['reserved_candidates']==spec['producer_contract']['count'] and
             type(intent['reserved_scan_cells']) is int and 0<intent['reserved_scan_cells']<=spec['tool_budget']['scan_cells'],
             'producer original candidate/scan grant changed')
        runs=pe._slots(db,b)
        need(any(r['intent_hash']==intent['process_intent_hash'] and r['dispatch'] is not None for r in runs),
             'producer original process anchor missing')
    need(result['receipt'] is None or result['intent'] is not None,'producer receipt without original intent')
    if result['receipt'] is not None:
        need(result['receipt']['intent_hash']==result['intent_hash'],'producer receipt original intent changed')
        receipt=result['receipt']
        need(set(receipt)=={'kind','intent_hash','result','actual_model_calls','model_final','formal_target_success','ended_at'} and
             receipt['kind']==VERSION and receipt['actual_model_calls']==0 and receipt['model_final'] is False and
             receipt['formal_target_success'] is False and type(receipt['ended_at']) in (int,float) and
             math.isfinite(receipt['ended_at']) and receipt['ended_at']>=result['intent']['started_at'],
             'producer receipt scope changed')
    return result


def trial_summary(db,study_id,trial_id):
    _,row,spec=sr._trial(db,study_id,trial_id)
    if 'producer_contract' not in spec:return {'integrated':False}
    record=_record(db,(row,spec,{}));intent=record['intent']
    return {'integrated':True,'arm':spec['producer_contract']['arm'],
        'reserved_actions':1 if intent else 0,
        'reserved_candidates':intent['reserved_candidates'] if intent else 0,
        'reserved_scan_cells':intent['reserved_scan_cells'] if intent else 0,
        'unknown_work':intent is not None and record['receipt'] is None,
        'completed':record['receipt'] is not None,'record':record,
        'compute_cost_source':'overlapping native process envelope, not zero or extra tool CPU',
        'model_final':False,'formal_target_success':False}


def status(root):
    with sr._db() as db:
        b=_binding(db,root);r=_record(db,b)
    return {'kind':VERSION,'contract':b[1]['producer_contract'],'record':r,
        'unknown_work':r['intent'] is not None and r['receipt'] is None,
        'completed':r['receipt'] is not None,'actual_model_calls':0,'formal_target_success':False}


def _active(db,root,b):
    state=pe._current(db,b)
    need(state['integrated'],'producer process accounting required')
    from .study_allocation import admit_tool
    admit_tool(db,b[0]['study_id'])
    return state


def _reserve(root):
    with sr._db() as db:
        b=_binding(db,root);record=_record(db,b)
        need(record['intent'] is None,'producer slot already started; no replacement generation')
        state=_active(db,root,b);mode=pe._mode(state)
        need(mode!='blocked','producer process resource budget exhausted')
        row,spec,plan=b;contract=spec['producer_contract'];case=plan['tasks']['producer']['case']
        from .source_admission import preflight_case
        from .research_batch import policy
        preflight_case(case);limits=policy(case)
        if contract['kind']==REAL_VERSION:
            from .generator_controls import validate_development_case
            validate_development_case(case)
        else:
            need(case['research_class']=='synthetic_calibration','real producer comparison is not admitted')
        cells=contract['count']*len(case['decision_fixture']['codes'])*len(case['decision_fixture']['calendar'])*(len(contract['space']['program_template']['factors'])+2)
        need(contract['count']<=limits['max_candidates_total'] and cells<=limits['max_scan_cells_total'],'producer case opportunity grant exceeded')
        need(contract['count']<=spec['tool_budget']['candidates'] and cells<=spec['tool_budget']['scan_cells'],'producer registered opportunity grant exceeded')
        need(limits['max_wall_seconds']*1000<=spec['tool_budget']['wall_ms'] and
             limits['output_stop_threshold_bytes']<=spec['tool_budget']['retained_output_bytes'],
             'producer batch ceiling exceeds registered wall/output grant')
        active=[r for r in state['runs'] if r['intent'] is not None and r['receipt'] is None][0]
        intent={'kind':VERSION,'study_id':row['study_id'],'trial_id':row['id'],
            'stage_plan_hash':row['stage_plan_hash'],'contract_hash':digest(contract),
            'process_intent_hash':active['intent_hash'],'started_at':time.time(),'mode':mode,
            'reserved_candidates':contract['count'],'reserved_scan_cells':cells,
            'model_calls_authorized':0}
        db.execute('UPDATE producer_runs SET intent=?,intent_hash=? WHERE study_id=? AND trial_id=?',
            (serial(intent),digest(intent),row['study_id'],row['id']))
        return intent,plan,contract


def authorize_control(control_root,arm):
    """Gate the actual direct generator path before generation validation/scans."""
    root=Path(control_root).resolve().parent
    with sr._db() as db:
        b=_binding(db,root);record=_record(db,b);state=_active(db,root,b)
        need(Path(control_root).resolve()==root/'control','producer output directory changed')
        need(record['intent'] is not None and record['receipt'] is None,'active original producer intent required')
        active=[r for r in state['runs'] if r['intent'] is not None and r['receipt'] is None][0]
        need(active['intent_hash']==record['intent']['process_intent_hash'],'producer cannot move to another process run')
        need(pe._mode(state)=='explore','producer closing reserve forbids new control work')
        c=b[1]['producer_contract'];plan=b[2]
        need(arm==c['arm'],'producer cannot change its frozen arm')
        saved=json.loads((Path(control_root)/'plan.json').read_text(encoding='utf-8'))
        created=saved.get('created_at')
        need(type(created) in (int,float) and math.isfinite(created) and
             record['intent']['started_at']<=created<=min(time.time(),plan['deadline_epoch']),
             'control creation time outside original producer work')
        from .generator_controls import _frozen_plan
        options={'development_selection':c['selection']} if c['kind']==REAL_VERSION else {}
        expected=_frozen_plan(plan['tasks']['producer']['case'],c['space'],count=c['count'],seed=c['seed'],
            deadline_epoch=plan['deadline_epoch'],created_at=created,source_pins=plan['provenance']['source_pins'],only_arm=c['arm'],**options)
        need(saved==expected,'control plan differs from frozen producer contract')
        need(expected['budget']['scan_cells_per_arm']==record['intent']['reserved_scan_cells'],
             'control scans differ from original producer reservation')


def _finish(root,intent,result):
    receipt={'kind':VERSION,'intent_hash':digest(intent),'result':result,
        'actual_model_calls':0,'model_final':False,'formal_target_success':False,'ended_at':time.time()}
    with sr._db() as db:
        b=_binding(db,root);record=_record(db,b)
        need(record['intent']==intent and record['receipt'] is None,'original producer commit is unavailable')
        row=b[0]
        db.execute('UPDATE producer_runs SET receipt=?,receipt_hash=? WHERE study_id=? AND trial_id=?',
            (serial(receipt),digest(receipt),row['study_id'],row['id']))
    from .research_tools import save_once
    save_once(Path(root)/'producer_receipt.json',receipt)
    return receipt


def run_inside(root):
    """No recovery replay: unknown generation/result must remain unresolved."""
    from .generator_controls import freeze,run_control
    intent,plan,c=_reserve(root)
    if intent['mode']=='close_only':
        return _finish(root,intent,{'status':'closed_without_generation','reason':'process closing reserve',
            'reserved_candidates':intent['reserved_candidates'],'reserved_scan_cells':intent['reserved_scan_cells'],
            'started_candidates':0,'formal_target_success':False})
    path=Path(root)/'control'
    options={'development_selection':c['selection']} if c['kind']==REAL_VERSION else {}
    freeze(path,plan['tasks']['producer']['case'],c['space'],count=c['count'],seed=c['seed'],deadline_epoch=plan['deadline_epoch'],only_arm=c['arm'],**options)
    result=run_control(path,c['arm'])
    return _finish(root,intent,result)


def dispatch(root):
    # Validate the one-shot slot before spending even a process-launch slot.
    state=status(root)
    need(state['record']['intent'] is None,'producer slot already started; read saved status, never regenerate')
    from .study_allocation import admit_tool
    with sr._db() as db:
        binding=_binding(db,root)
        admit_tool(db,binding[0]['study_id'])
    if pe.inside(root):return run_inside(root)
    need(pe.status(root)['launches_used']==0,'producer original process already started; no replacement launch')
    return pe.supervise(root)
