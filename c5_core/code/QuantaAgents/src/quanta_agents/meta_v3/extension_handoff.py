"""Read-only V4 extension handoff; never grant a budget or execute a worker.

The parent keeps its original case and ledger. A completed child report is
explicitly cross-scope context, never a parent-local tool result or acceptance.
"""
import json
from pathlib import Path

from .context import bounded
from .ledger import Ledger, digest, need, serial
from .research_extension import (_read, _parent_request, _verify_saved_call,
                                 verify_extension_stage)

VERSION='yield_to_controller_v1'


def validate_policy(policy):
    need(policy is None or policy=={'version':VERSION},'unknown extension handoff policy')
    return policy


def _intent(parent,plan,task_id,request_id,request,folder):
    path=folder/'intent.json'
    if not path.exists():
        need(not (folder/'decision.json').exists(),'extension decision without intent')
        return None
    intent=_read(path)
    need(intent.get('kind')=='controller_extension_admission_v1' and
         Path(intent['parent_root']).resolve()==parent.root and
         intent['parent_task']==task_id and intent['request_id']==request_id and
         intent['parent_plan_hash']==digest(plan) and
         intent['parent_case_hash']==plan['tasks'][task_id]['case_hash'] and
         intent['request_hash']==digest(request),'extension handoff intent binding drift')
    return intent


def _child_report(child,plan,state):
    if state['terminal']!='submitted':
        return None
    final_id=state['final_call']
    need(type(final_id) is str and final_id,'submitted child has no final call')
    call=child.call(final_id)
    need(call['task_id']=='extension' and call['status']=='applied' and
         call['receipt']['response']['action']=='submit_research_report',
         'child report call binding drift')
    receipt=_verify_saved_call(child,plan,call)
    application=_read(child.root/'calls'/final_id/'application.json')
    need(application.get('failed') is False and
         application['model_response_hash']==digest(receipt['response']) and
         application['result']==call['result'],'child report application drift')
    report=call['result']
    from .report_arguments import decode
    arguments, proof = decode(receipt['response'], plan['provenance'].get('report_argument_policy'))
    need(application.get('argument_decoding') == proof, 'child report argument decoding proof drift')
    need(report.get('legal_submission') is True and
         report['model_report']==arguments,
         'child report does not bind to original model response')
    return {'child_call_id':final_id,'child_result_hash':digest(report),
        'public_result':report,
        'interpretation':'Child-scope model report, not a parent-local result, accepted scientific claim, independent observation or formal success.'}


def inspect_handoff(parent_root,task_id,*,max_bytes=12000):
    """Return a bounded status projection for one parent task, without writes.

    ``None`` means no policy. Pending decisions and unsettled child work block
    parent dispatch; a terminal child, including a failed final, can be reported
    back. ``child_running`` describes unfinished work, not verified OS liveness.
    """
    need(type(max_bytes) is int and 4000<=max_bytes<=262144,'bounded handoff projection size required')
    parent=Ledger(parent_root)
    with parent.transaction() as db:
        _,plan,_=parent._plan(db)
    policy=validate_policy(plan['provenance'].get('extension_handoff_policy'))
    if policy is None:
        return None
    need(task_id in plan['tasks'],'unknown extension handoff parent task')
    rows=[];details=[]
    for entry in parent.history(task_id):
        if entry['status']!='applied' or (entry.get('response') or {}).get('action')!='request_research_extension':
            continue
        request_id=entry['id']
        _,_,request,_,_=_parent_request(parent,task_id,request_id,require_settled=False)
        folder=parent.root/'controller_extensions'/request_id
        intent=_intent(parent,plan,task_id,request_id,request,folder)
        row={'request_id':request_id,'status':'pending_controller'}
        detail={'request_hash':digest(request),'request':request['request']}
        decision_path=folder/'decision.json'
        if intent is not None and decision_path.exists():
            decision=_read(decision_path)
            need(decision.get('kind')=='controller_extension_decision_v1' and
                 decision['intent_hash']==digest(intent) and
                 decision['status']==intent['decision']['status'] and
                 decision['reason']==intent['decision']['reason'],
                 'extension handoff decision binding drift')
            detail['controller_reason']=decision['reason']
            if decision['status']=='needs_implementation':
                need(decision['child_root'] is None and decision['child_plan_hash'] is None and
                     decision['child_budget_registered'] is False,
                     'unimplemented extension cannot contain child authority')
                row['status']='needs_implementation'
            else:
                need(decision['status']=='ready','unknown saved extension decision')
                root=folder/'stage'
                need(Path(decision['child_root']).resolve()==root.resolve() and
                     decision['child_budget_registered'] is True,
                     'extension handoff child root binding drift')
                child=Ledger(root)
                with child.transaction() as db:
                    _,child_plan,_=child._plan(db)
                need(set(child_plan['tasks'])=={'extension'} and
                     decision['child_plan_hash']==digest(child_plan),
                     'extension handoff child plan binding drift')
                lineage=child_plan['provenance'].get('research_extension',{})
                need(Path(lineage.get('intent_path','')).resolve()==(folder/'intent.json').resolve() and
                     Path(lineage.get('decision_path','')).resolve()==decision_path.resolve() and
                     lineage.get('intent_hash')==digest(intent),
                     'extension handoff child lineage drift')
                verify_extension_stage(child_plan)
                state=child.status('extension')
                unresolved=any(c['status'] in ('pending','unknown','received','applying') for c in state['calls'])
                # Budget/deadline exhaustion may be derived before a worker has
                # persisted terminal_without_submission. Never renew that work.
                ended=bool(state['terminal']) or (state['mode']=='terminal_without_submission' and not unresolved)
                row.update(status='ready_followup' if ended else 'child_running',
                    child_plan_hash=digest(child_plan),child_terminal=state['terminal'],
                    child_mode=state['mode'])
                detail.update(child_root=str(root),child_case_hash=child_plan['tasks']['extension']['case_hash'],
                    child_known_tokens=state['known_tokens'],
                    child_unknown_or_pending_reserve=state['unknown_or_pending_reserve'],
                    child_reasons=state['reasons'],child_report=_child_report(child,child_plan,state),
                    liveness='Unfinished child ledger is not evidence of a live process.')
        elif intent is not None:
            detail['controller_admission']='Intent exists but decision is incomplete; reconcile without a new model call.'
        rows.append(row);details.append(detail)
    statuses={r['status'] for r in rows}
    status=next((s for s in ('pending_controller','child_running','ready_followup','needs_implementation') if s in statuses),'none')
    result={'version':VERSION,'status':status,
        'blocks_parent_dispatch':bool(statuses & {'pending_controller','child_running'}),
        'requests':rows,
        'cross_scope_semantics':'Child reports are previously exposed context from a distinct frozen case. They do not become parent-local evidence IDs, matched returns, independent OOS or accepted research conclusions.'}
    room=max_bytes-len(serial(result).encode('utf-8'))-64*len(rows)
    need(not rows or room>=1600,'handoff metadata exceeds requested projection bound')
    if rows:
        each=room//len(rows)
        # Preserve every request's scheduling identity even if many requests
        # leave too little space for a useful evidence excerpt.
        if each>=1600:
            for row,detail in zip(rows,details):row['detail']=bounded(detail,each)
        else:
            for row,detail in zip(rows,details):row['detail_hash']=digest(detail)
    need(len(serial(result).encode('utf-8'))<=max_bytes,'handoff projection exceeds byte bound')
    return result
