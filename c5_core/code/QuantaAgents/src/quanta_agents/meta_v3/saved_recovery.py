"""Single claimed, read-only contract repair within a settled parent's balance.

No acquisition, strategy execution, paid invocation or automatic retry here.
Original model responses and interrupted delivery remain unchanged.
"""
from dataclasses import asdict
from copy import deepcopy
import hashlib
from pathlib import Path
import time

from .ledger import Ledger,need,digest,worker_lease
from .closing import ClosingPolicy
from .research_tools import save_once
from .research_batch import read

READ_ACTIONS=['inspect_batch','inspect_execution','diagnose_execution','read_evidence','submit_research_report']


def proofs(root):
    files=[root/'plan.json',root/'public_contract_at_admission.json']
    for name in ('calls','tools','batches'):
        files.extend(p for p in (root/name).rglob('*') if p.is_file() and p.name!='worker.lock')
    need(len(files)<=4096 and sum(p.stat().st_size for p in files)<=512*1024**2,'saved recovery evidence bound')
    return [{'path':str(p.resolve()),'sha256':hashlib.sha256(p.read_bytes()).hexdigest()} for p in sorted(set(files))]


def prepare(parent_root,root,task_id,*,reason,max_calls=8,max_tokens=400000):
    from .runtime import source_pins,ResearchRuntime
    parent=Ledger(parent_root);root=Path(root).resolve()
    need(type(reason) is str and 0<len(reason)<=4000,'explicit contract defect required')
    need(type(max_calls) is int and 1<=max_calls<=8 and type(max_tokens) is int and 80000<=max_tokens<=400000,'bounded saved recovery grant')
    with worker_lease(parent.root):
        with parent.transaction() as db:
            stage,plan,policy=parent._plan(db)
            need(stage['paused'],'parent must be paused')
            need(task_id in plan['tasks'] and len(plan['tasks'])==1,'single-task saved recovery only')
            need(not plan['tasks'][task_id].get('imported_history'),'recovery chains are not admitted')
            task_row=db.execute('SELECT * FROM tasks WHERE id=?',(task_id,)).fetchone()
            need(task_row['final_call'] is None,'final opportunity already consumed')
        history=parent.history(task_id);state=parent.status(task_id)
        need(history and all(x['status'] in ('applied','failed') and x['known_tokens'] is not None for x in state['calls']),
             'all parent calls must be settled with known usage')
        need(not any(x['response']['action']=='submit_research_report' for x in history),'parent final already attempted')
        remaining_calls=min(policy.task_calls,policy.stage_calls)-len(state['calls'])
        remaining_tokens=min(policy.task_tokens,policy.stage_tokens)-state['known_tokens']
        need(remaining_calls>=1 and remaining_tokens>=policy.closing_reserve and time.time()<plan['deadline_epoch'],'original allowance exhausted')
        claim_path=parent.root/(task_id+'_saved_recovery_claim.json')
        need(not (parent.root/(task_id+'_final_recovery_claim.json')).exists(),'another recovery already claimed')
        need(not claim_path.exists() and not root.exists(),'saved recovery already claimed; no second admission')
        pins=source_pins()
        from .kernel import FROZEN,ROOT
        prefix=str(FROZEN.relative_to(ROOT)).replace('\\','/')+'/'
        need(all(pins.get(k)==v for k,v in plan['provenance']['source_pins'].items() if k.startswith(prefix)),
             'frozen execution kernel changed')
        policy_new=ClosingPolicy(task_calls=min(max_calls,remaining_calls),stage_calls=min(max_calls,remaining_calls),
            task_tokens=min(max_tokens,remaining_tokens),stage_tokens=min(max_tokens,remaining_tokens),
            call_reserve=policy.call_reserve,closing_reserve=policy.closing_reserve,closing_seconds=policy.closing_seconds)
        evidence=proofs(parent.root)
        task=deepcopy(plan['tasks'][task_id])
        task['imported_history']={'root':str(parent.root),'task_id':task_id,'rows':history,'hash':digest(history)}
        task['permitted_actions']=READ_ACTIONS
        task['documents'].append({'name':'Public contract repair and saved-only continuation',
            'text':reason+' The original stage was stopped by the controller before its final opportunity because of this engineering defect. Its calls, all expanded candidates and failures remain counted. Use the corrected public contract below to finish reading saved evidence, diagnose saved accounts and provide your own report or abstention. No new strategy, registered revision, batch execution or acquisition is admitted. This recovery does not turn the interrupted original delivery into a successful original start.'})
        claim={'kind':'saved_contract_recovery_v1','root':str(root),'parent_root':str(parent.root),'task_id':task_id,
            'parent_plan_hash':digest(plan),'parent_history_hash':digest(history),'parent_known_tokens':state['known_tokens'],
            'parent_calls':len(state['calls']),'remaining_original_calls':remaining_calls,'remaining_original_tokens':remaining_tokens,
            'original_deadline':plan['deadline_epoch'],'policy':asdict(policy_new),'source_pins':pins,'evidence_sources':evidence,
            'task_hash':digest(task),'reason':reason,'new_candidate_trials':0,'independent_market_samples':0,
            'original_interrupted_delivery_retained':True,'formal_target_success':False,'registered_at':time.time()}
        # The exclusive claim spends the remaining opportunity once even if
        # stage creation is interrupted; it never silently grants a replacement.
        save_once(claim_path,claim)
        decision_path=root/'admission.json'
        ledger=Ledger.create(root,policy=policy_new,tasks={task_id:task},deadline_epoch=plan['deadline_epoch'],
            provenance={'source_pins':pins,'saved_contract_recovery':{'claim_path':str(claim_path),'claim_hash':digest(claim),'decision_path':str(decision_path)},
                'old_v2_known_tokens':491954,'old_v2_unknown_reserve':80000,'parent_known_tokens':state['known_tokens'],
                'exposure':'Same previously exposed generated batch evidence; read-only contract recovery, not a new start or market sample.',
                'fixture_only':plan['provenance'].get('fixture_only') is True,'formal_target_success':False})
        child_plan=read(root/'plan.json')
        save_once(decision_path,{'kind':'saved_contract_recovery_admission_v1','claim_hash':digest(claim),'plan_hash':digest(child_plan),
            'maximum_new_model_calls':policy_new.stage_calls,'nominal_token_limit':policy_new.stage_tokens,'new_candidate_trials':0,
            'parent_budget_changed':False,'parent_source_changed':False,'formal_target_success':False})
        rt=ResearchRuntime(root);rt.verify_inputs()
        save_once(root/'public_contract_at_admission.json',rt._tools(task_id).contract())
        return ledger


def verify_saved_recovery(plan,root):
    link=plan['provenance'].get('saved_contract_recovery')
    if not link:return
    root=Path(root).resolve()
    claim=read(link['claim_path'])
    need(root==Path(claim['root']).resolve(),'saved recovery runtime root differs from claimed root')
    need(Path(link['decision_path']).resolve()==root/'admission.json','saved recovery admission must be inside claimed root')
    need(Path(link['claim_path']).resolve()==Path(claim['parent_root']).resolve()/(claim['task_id']+'_saved_recovery_claim.json'),
         'saved recovery claim must use exclusive parent marker')
    decision=read(link['decision_path'])
    need(digest(claim)==link['claim_hash']==decision['claim_hash'] and digest(plan)==decision['plan_hash'],'saved recovery admission drift')
    need(plan['policy']==claim['policy'] and plan['deadline_epoch']==claim['original_deadline'] and
         plan['provenance']['source_pins']==claim['source_pins'],'saved recovery grant changed')
    task=plan['tasks'][claim['task_id']]
    need(digest(task)==claim['task_hash'] and task['permitted_actions']==READ_ACTIONS,'saved-only task drift')
    parent=Ledger(claim['parent_root']);state=parent.status(claim['task_id'])
    with parent.transaction() as db:
        stage,parent_plan,_=parent._plan(db)
        need(stage['paused'] and digest(parent_plan)==claim['parent_plan_hash'],'parent resumed or changed')
    need(state['known_tokens']==claim['parent_known_tokens'] and len(state['calls'])==claim['parent_calls'] and
         state['unknown_or_pending_reserve']==0 and state['final_call'] is None and
         digest(parent.history(claim['task_id']))==claim['parent_history_hash'],'parent recovery balance/history drift')
    for proof in claim['evidence_sources']:
        need(hashlib.sha256(Path(proof['path']).read_bytes()).hexdigest()==proof['sha256'],'saved parent evidence drift')
