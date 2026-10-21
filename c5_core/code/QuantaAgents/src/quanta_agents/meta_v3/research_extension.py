"""Controller admission of saved extension requests into separate, bounded stages.

This interface performs no acquisition or model call. A ready decision requires
already implemented inputs; an unavailable capability is recorded explicitly.
The durable intent precedes stage creation. Reconciliation never runs a worker.
"""
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import re
import time

from .closing import ClosingPolicy
from .kernel import module, FROZEN, ROOT
from .ledger import Ledger, digest, need, serial, worker_lease
from .research_tools import ResearchTools, save_once
from .source_admission import preflight_case,admit_paths,substantive_change,source_paths


def validate_context_policy(policy):
    """Old scopes retain their original context unless explicitly opted in."""
    if policy is None:return
    need(type(policy) is dict and policy=={'version':'parent_programs_v1'},
         'unknown extension context policy')


def _read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def _file_proof(path):
    path=Path(path).resolve()
    need(path.is_file() and path.stat().st_size<=128*1024**2,'bounded existing admission evidence required')
    return {'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}


def _gateway_for_plan(plan):
    if plan['provenance'].get('runtime_identity_route') == {'version': 'codex_session_v1'}:
        from . import codex_session_gateway
        return codex_session_gateway
    return module('meta.codex_gateway')


def _verify_saved_call(ledger,plan,call):
    """Use the original route; a captured session is part of its artifact set."""
    receipt=call['receipt']
    need(receipt is not None,'saved model receipt required')
    verified=_gateway_for_plan(plan).verify_saved_completion(ledger.root/'calls'/call['id'],
        expected_prompt_hash=call['intent']['prompt_hash'],expected_schema=call['intent']['schema'],
        expected_artifact_sha256=receipt['artifact_sha256'])
    need(verified['response']==receipt['response'],'extension model receipt drift')
    need(verified.get('request_identity',{}).get('intent_id')==call['id'],
         'extension model request identity mismatch')
    if plan['provenance'].get('controller_policy') is not None:
        if plan['provenance'].get('runtime_identity_route') == {'version': 'codex_session_v1'}:
            need(verified.get('runtime_identity',{}).get('verified') is True,
                 'V4 extension runtime session identity unverified')
        else:
            need(verified.get('model_verified') is True,
                 'V4 extension runtime model/effort identity unverified')
    return verified


def _parent_request(parent,task_id,request_id,*,require_settled=True):
    with parent.transaction() as db:
        _,plan,_=parent._plan(db)
    context_policy=plan['provenance'].get('extension_context_policy')
    validate_context_policy(context_policy)
    if context_policy is not None:
        need(plan['provenance'].get('controller_policy') is not None,
             'extension context policy requires a V4 controller')
    need(task_id in plan['tasks'],'unknown parent task')
    state=parent.status(task_id)
    need(not require_settled or not any(c['status'] in ('pending','unknown','received','applying') for c in state['calls']),
         'unresolved parent exposure blocks extension admission')
    task=plan['tasks'][task_id]
    need(digest(task['case'])==task['case_hash'],'parent case drift')
    history=parent.history(task_id)
    rows={r['id']:r for r in history}
    need(request_id in rows and rows[request_id]['status']=='applied','extension request must be applied')
    call=parent.call(request_id)
    receipt=call['receipt']
    need(receipt['response']['action']=='request_research_extension','not an extension action')
    _verify_saved_call(parent,plan,call)
    path=parent.root/'tools'/task_id/request_id/'artifact.json'
    request=_read(path)
    need(digest(request)==rows[request_id]['result']['artifact_hash'],'extension artifact drift')
    need(request['kind']=='research_extension_request_v1' and request['current_case_hash']==task['case_hash'],
         'extension parent scope mismatch')
    need(request['request']==json.loads(receipt['response']['arguments_json']),'request response binding drift')
    proofs=[_file_proof(parent.root/'plan.json'),_file_proof(path)]
    context=[]
    for eid in request['request']['evidence_ids']:
        need(eid in rows and rows[eid]['status']=='applied' and rows[eid]['result'],'unsettled cited parent evidence')
        original=parent.root/'tools'/task_id/eid/'artifact.json'
        value=_read(original)
        need(digest(value)==rows[eid]['result'].get('artifact_hash'),'cited parent artifact drift')
        proofs.append(_file_proof(original))
        entry={'parent_evidence_id':eid,'action':rows[eid]['response']['action'],
            'artifact_hash':digest(value),'public_result':rows[eid]['result'].get('public')}
        if context_policy is not None and entry['action']=='develop_strategy':
            original_call=parent.call(eid)
            verified=_verify_saved_call(parent,plan,original_call)
            need(verified['response']['action']=='develop_strategy','parent program action binding drift')
            args=_gateway_for_plan(plan)._strict_json(verified['response']['arguments_json'])
            program=value.get('program')
            required={'version','factors','target_weight_expression','hypothesis',
                      'applicability','invalidation_conditions'}
            need(type(program) is dict and required<=set(program),'complete parent program required')
            need(type(args) is dict and args.get('program')==program,
                 'parent program differs from bound model request')
            from .research_iteration import executable_program_hash
            entry['frozen_parent_program']={
                'program':program,'program_hash':digest(program),
                'executable_program_hash':executable_program_hash(program),
                'source_parent_call_id':eid,'source_artifact_hash':digest(value),
                'source_prompt_hash':original_call['intent']['prompt_hash'],
                'source_response_hash':digest(verified['response']),
                'source_model_artifact_sha256':verified['artifact_sha256'],
                'interpretation':'Exact previously exposed parent program for reference only; not a child-local execution or a new independent observation.'}
        context.append(entry)
    need(len(serial(context).encode('utf-8'))<=64000,'extension context requires bounded explicit evidence pages')
    return plan,task,request,proofs,context


def _child_controller_policy(request):
    """Describe the saved research question without prescribing model actions."""
    from .controller_plan import VERSION
    body=request['request']
    return {'version':VERSION,'work':{'extension':{
        'question':('Resolve the saved extension question: '+body['problem'])[:2000],
        'necessary_evidence':[
            ('Requested deliverable: '+body['specification'])[:1000],
            ('Information needed to distinguish explanations: '+body['expected_information_gain'])[:1000],
            'Newly admitted case source bindings, field availability, complete capital and execution limitations.',
            'Previously exposed parent evidence remains identified as another scope; new actions receive child-local evidence IDs.'],
        'depends_on':[],
        'unlocks':'A bounded child report addressing the original request for the parent controller and researcher.',
        'stop_condition':'Stop at the frozen child call, token and deadline limits; report whether the requested evidence resolves the question, including failure or insufficient evidence. No scope renewal or formal success is implied.'}}}


def _protected_contract(case):
    # Scope changes may add observations, fields and symbols, but this path
    # cannot cheapen execution, shrink capital, or change the research class.
    result={k:case.get(k) for k in ('initial_cash','research_class','execution_backend')}
    if case.get('execution_backend')=='v3_l2_cash_001':
        plan=case['l2_execution']['account_plan']
        result['account_contract']={k:v for k,v in plan.items() if k not in
            ('calendar','codes','initial_time','obligations','corporate_announcements')}
    return result


def decide_extension(parent_root,task_id,request_id,*,decision,child_case=None,policy=None,deadline_epoch=None):
    """Only the trusted controller calls this with its explicit frozen decision.

    Idempotence is scoped to the actual parent request. A different decision
    cannot replace the first intent, including after interruption.
    """
    from .runtime import source_pins,verify_case_sources
    parent=Ledger(parent_root)
    need(re.fullmatch(r'[A-Za-z0-9_-]{1,120}',request_id or ''),'invalid request identity')
    with worker_lease(parent.root):
        plan,task,request,proofs,context=_parent_request(parent,task_id,request_id)
        required={'status','reason','asset_contract','resource_bounds','exposure_statement'}
        need(type(decision) is dict and set(decision) in (required,required|{'source_admissions'}),'exact controller decision fields')
        need(decision['status'] in ('ready','needs_implementation'),'unknown controller decision')
        for key in ('reason','exposure_statement'):
            need(type(decision[key]) is str and 0<len(decision[key])<=4000,'explicit bounded controller explanation')
        ready=decision['status']=='ready'
        if ready:
            need(decision['asset_contract']=='a_share_cash_equity','unsupported asset execution semantics')
            need(type(child_case) is dict and type(policy) is ClosingPolicy,'implemented case and explicit child budget required')
            # Validate in-memory structure, assets and dates before any new
            # source evidence hashing (including canaries outside the scope).
            bounds=decision['resource_bounds'];requested=request['request']['requested_resource_bounds']
            need(type(bounds) is dict and set(bounds)==set(requested),'exact granted resource dimensions')
            need(all(type(n) is int and 0<=n<=requested[k] for k,n in bounds.items()),'grant exceeds requested bounds')
            need(bounds['download_bytes']==0,'admission cannot execute acquisition; first obtain separately bounded saved sources')
            need(policy.stage_calls<=bounds['model_calls'],'child model calls exceed grant')
            need(type(deadline_epoch) in (int,float) and time.time()<deadline_epoch<=time.time()+bounds['wall_seconds'],
                 'child deadline exceeds grant')
            need(digest(child_case)!=task['case_hash'],'new scope must differ from original frozen case')
            need(_protected_contract(child_case)==_protected_contract(task['case']),'capital or execution contract changed')
            preflight_case(child_case)
            kind=request['request']['request_kind']
            if kind=='data':need(request['request'].get('requested_fields'),'text-only data request needs structured field deliverables before ready admission')
            material=substantive_change(task['case'],child_case,kind,request['request'].get('requested_fields'))
            path_admission=admit_paths(child_case,decision.get('source_admissions'),
                engineering_allowed=plan['provenance'].get('fixture_only') is True)
            frozen_prefix=str(FROZEN.relative_to(ROOT)).replace('\\','/')+'/'
            current_pins=source_pins()
            for name,pin in plan['provenance']['source_pins'].items():
                if name.startswith(frozen_prefix):need(current_pins.get(name)==pin,'frozen execution kernel changed')
            fixture=child_case['decision_fixture']
            need(len(fixture['codes'])<=bounds['symbols'] and len(fixture['calendar'])<=bounds['sessions'],'child input axes exceed grant')
            need(child_case.get('evidence_sources'),'implemented scope needs pinned source and admission evidence')
            verify_case_sources({'case':child_case,'case_hash':digest(child_case)})
        else:
            need(child_case is None and policy is None and deadline_epoch is None and decision['resource_bounds'] is None,
                 'unimplemented request cannot create a child or budget')
        folder=parent.root/'controller_extensions'/request_id
        child=folder/'stage'
        proposal={'kind':'controller_extension_admission_v1','parent_root':str(parent.root),
            'parent_task':task_id,'request_id':request_id,'parent_plan_hash':digest(plan),'parent_case_hash':task['case_hash'],
            'request_hash':digest(request),'decision':decision,'child_case':child_case,
            'child_case_hash':digest(child_case) if ready else None,'policy':asdict(policy) if ready else None,
            'deadline_epoch':deadline_epoch,'source_pins':source_pins(),'parent_evidence_sources':proofs,
            'substantive_extension':material if ready else None,'source_admission':path_admission if ready else None}
        intent=folder/'intent.json'
        if intent.exists():need(_read(intent)==proposal,'previous admission intent differs; no free replacement')
        else:save_once(intent,proposal)
        final=folder/'decision.json'
        if final.exists():
            result=_read(final)
            need(result['intent_hash']==digest(proposal),'saved admission decision drift')
            if ready:verify_extension_stage(_read(child/'plan.json'))
            return result
        if ready:
            # Constructor validates the causal grid and supported execution
            # backend. This creates no strategy, model or acquisition worker.
            ResearchTools(folder/'validation','extension',child_case,[])
            child_task={'case':child_case,'case_hash':digest(child_case),'idea':task['idea'],
                'documents':task['documents']+[{'type':'controller_admitted_extension',
                    'original_request':request,'controller_decision':decision,'parent_evidence':context,
                    'interpretation':'Previously exposed parent evidence, not new independent observations. Different scopes cannot be treated as matched returns. New task actions use new evidence IDs.'}]}
            provenance={'source_pins':proposal['source_pins'],'research_extension':{
                'intent_path':str(intent),'intent_hash':digest(proposal),'decision_path':str(final)},
                'old_v3_known_tokens':plan['provenance'].get('old_v3_known_tokens',0),
                'parent_stage_known_tokens':parent.status(task_id)['known_tokens'],
                'old_v2_known_tokens':plan['provenance'].get('old_v2_known_tokens',491954),
                'old_v2_unknown_reserve':plan['provenance'].get('old_v2_unknown_reserve',80000),
                'exposure':decision['exposure_statement'],'formal_target_success':False}
            if plan['provenance'].get('controller_policy') is not None:
                provenance['controller_policy']=_child_controller_policy(request)
                provenance['harness_version']=plan['provenance'].get('harness_version','4.0.0-dev')
                if 'runtime_identity_route' in plan['provenance']:
                    provenance['runtime_identity_route']=plan['provenance']['runtime_identity_route']
                for name in ('extension_context_policy','report_argument_policy','architecture_contract'):
                    if name in plan['provenance']:
                        provenance[name]=plan['provenance'][name]
            from .study_descendants import register as register_descendant
            provenance=register_descendant(parent.root,plan,request_id,intent,child,
                tasks={'extension':child_task},policy=asdict(policy),deadline_epoch=deadline_epoch,provenance=provenance)
            if child.exists():
                # Interrupted construction is reconciled only if the complete
                # untouched child ledger agrees. Incomplete DBs remain blocked.
                ledger=Ledger(child)
                with ledger.transaction() as db:_,saved,_=ledger._plan(db)
                need(saved['tasks']=={'extension':child_task} and saved['provenance']==provenance and
                    saved['policy']==asdict(policy) and saved['deadline_epoch']==deadline_epoch,'incomplete or changed child stage')
                need(not ledger.history('extension'),'uncommitted admission child has model activity')
            else:
                ledger=Ledger.create(child,policy=policy,tasks={'extension':child_task},deadline_epoch=deadline_epoch,provenance=provenance)
            child_plan=_read(child/'plan.json')
        result={'kind':'controller_extension_decision_v1','intent_hash':digest(proposal),'status':decision['status'],
            'reason':decision['reason'],'child_root':str(child) if ready else None,
            'child_plan_hash':digest(child_plan) if ready else None,'parent_scope_changed':False,
            'child_budget_registered':ready,'model_calls_executed_by_admission':0,'downloads_executed_by_admission':0,
            'genuine_model_extension_use_verified':False,'formal_target_success':False}
        save_once(final,result)
        if ready:verify_extension_stage(child_plan)
        return result


def verify_extension_stage(plan):
    """Runtime gate: an unfinished admission must never launch a paid call."""
    lineage=plan['provenance'].get('research_extension')
    if not lineage:return
    intent=_read(lineage['intent_path'])
    need(digest(intent)==lineage['intent_hash'],'extension admission intent drift')
    decision_path=Path(lineage['decision_path'])
    need(decision_path.is_file(),'extension admission incomplete; controller reconciliation required')
    result=_read(decision_path)
    need(result['intent_hash']==lineage['intent_hash'] and result['status']=='ready' and
         result['child_plan_hash']==digest(plan),'extension decision binding drift')
    need(plan['policy']==intent['policy'] and plan['deadline_epoch']==intent['deadline_epoch'] and
         plan['tasks']['extension']['case_hash']==intent['child_case_hash'],'extension stage exceeds frozen admission')
    preflight_case(plan['tasks']['extension']['case'])
    # All declared child paths must still match the original approved set;
    # runtime does not broaden a source admission based on file contents.
    need(intent['source_admission']['source_path_set_hash']==digest(sorted(source_paths(plan['tasks']['extension']['case']))),
        'extension source path admission changed')
    for proof in intent['parent_evidence_sources']:
        need(_file_proof(proof['path'])['sha256']==proof['sha256'],'extension parent evidence drift')
