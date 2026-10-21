"""Pre-funded, nontransferable trial grants inside one frozen study.

Overrun headroom is explicit; nominal protection cannot cap supplier billing.
Full-stack measurement and scientific acceptance remain separate gates.
"""
import json

from .closing import ClosingDecision, FINAL_ACTION
from .ledger import digest, need
from . import study_registry as sr
from . import tool_resources as tr

VERSION = 'fixed_trial_grants_with_explicit_headroom_v1'


def validate(plan):
    allocation = plan.get('allocation')
    if 'allocation' not in plan: return
    has_process=any('process_envelope' in t for t in plan['trials'])
    keys={'version','model_tokens','model_calls','tool_resources'}|({'process_resources'} if has_process else set())
    need(type(allocation) is dict and set(allocation)==keys, 'exact study allocation')
    need(allocation['version']==VERSION, 'study allocation version')
    need(all(type(allocation[k]) is int and 0<allocation[k]<=1000000000 for k in ('model_tokens','model_calls')), 'bounded study model allocation')
    resources = allocation['tool_resources']
    need(type(resources) is dict and set(resources)==set(tr.LIMITS) and
         all(type(v) is int and 0<=v<=96*tr.LIMITS[k] for k,v in resources.items()), 'bounded study tool allocation')
    trials = plan['trials']
    need(all('tool_budget' in t for t in trials), 'allocation requires every trial tool grant')
    need(allocation['model_tokens']>=sum(t['policy']['stage_tokens'] for t in trials), 'study cannot fund all model grants')
    need(allocation['model_calls']==sum(t['policy']['stage_calls'] for t in trials), 'study calls must equal fixed trial grants')
    need(all(resources[k]>=sum(t['tool_budget'][k] for t in trials) for k in tr.LIMITS), 'study cannot fund all tool grants')
    if has_process:
        from .process_envelope import LIMITS
        need(all('process_envelope' in t for t in trials),'every trial requires its process envelope')
        total=allocation['process_resources']
        need(type(total) is dict and set(total)==set(LIMITS) and
             all(type(total[k]) is int and 0<total[k]<=96*v for k,v in LIMITS.items()),'bounded study process allocation')
        need(all(total[k]>=sum(t['process_envelope']['budget'][k] for t in trials) for k in LIMITS),'study cannot fund all process grants')
    comparisons = {}
    for t in trials:
        key = t['case_hash'],t['repeat']
        # Compare whole-trial ceilings, not architecture-specific task layout.
        grant = {'model_tokens':t['policy']['stage_tokens'],'model_calls':t['policy']['stage_calls'],
                 'duration_seconds':t['duration_seconds'],'tool_budget':t['tool_budget']}
        if has_process:grant['process_budget']=t['process_envelope']['budget']
        need(key not in comparisons or comparisons[key]==grant, 'same case-repeat architectures require equal resource ceilings')
        comparisons[key] = grant


def status(db, study_id):
    study = db.execute('SELECT * FROM studies WHERE id=?',(study_id,)).fetchone()
    need(study is not None, 'unregistered study allocation')
    plan = json.loads(study['plan'])
    need(digest(plan)==study['plan_hash'], 'study allocation plan drift')
    if 'allocation' not in plan:
        return {'integrated':False,'full_stack_comparison_admitted':False}
    allocation = plan['allocation']
    grants=[]; token_debt=0; tool_debt=dict.fromkeys(tr.LIMITS,0)
    from .process_envelope import trial_summary, LIMITS as process_limits
    process_debt=dict.fromkeys(process_limits,0);process_grants=dict.fromkeys(process_limits,0)
    for declared in plan['trials']:
        _, row, spec = sr._trial(db,study_id,declared['id'])
        from .tool_measurements import protocol
        protocol(db,study_id,row['id'])
        calls = list(db.execute('SELECT * FROM calls WHERE study_id=? AND trial_id=?',(study_id,row['id'])))
        known = sum(c['known_tokens'] or 0 for c in calls)
        unknown = sum(c['reserve'] for c in calls if c['known_tokens'] is None)
        exposure = known+unknown
        token_debt += max(0,exposure-spec['policy']['stage_tokens'])
        used = dict.fromkeys(tr.LIMITS,0); unresolved_tools=0
        from .registered_producer import trial_summary as producer_summary
        producer=producer_summary(db,study_id,row['id'])
        if producer['integrated']:
            used['actions']+=producer['reserved_actions']
            used['candidates']+=producer['reserved_candidates']
            used['scan_cells']+=producer['reserved_scan_cells']
        for tool in db.execute('SELECT * FROM tool_actions WHERE study_id=? AND trial_id=?',(study_id,row['id'])):
            quote = json.loads(tool['quote']);metrics=json.loads(tool['metrics']) if tool['metrics'] is not None else None
            from .tool_measurements import saved
            receipt=saved(db,tool)
            if tool['metrics'] is None and receipt is not None: metrics=receipt['metrics']
            for k in tr.LOGICAL: used[k]+=quote[k]
            if tool['metrics'] is None: unresolved_tools+=1
            if metrics is not None:
                for k in tr.MEASURED: used[k]+=metrics[k]
        for k in tr.LIMITS: tool_debt[k]+=max(0,used[k]-spec['tool_budget'][k])
        if 'process_envelope' in spec:
            process=trial_summary(db,study_id,row['id'])
            budget=spec['process_envelope']['budget']
            observed=dict.fromkeys(process_limits,0)
            if process['integrated']:
                observed['launches']=process['launches_used']
                for k in process['known_finished']:observed[k]=process['known_finished'][k]
                for run in process['runs']:
                    if run['receipt'] is None and run['observation'] is not None:
                        for k in process['known_finished']:observed[k]+=run['observation'][k]
            for k in process_limits:
                process_grants[k]+=budget[k];process_debt[k]+=max(0,observed[k]-budget[k])
        grants.append({'trial_id':row['id'],'claimed':row['claimed_at'] is not None,
            'model_token_grant':spec['policy']['stage_tokens'],'model_call_grant':spec['policy']['stage_calls'],
            'known_tokens':known,'unknown_model_reserve':unknown,'calls_used':len(calls),
            'tool_grant':spec['tool_budget'],'tool_used':used,'unresolved_tools':unresolved_tools})
    token_headroom = allocation['model_tokens']-sum(g['model_token_grant'] for g in grants)
    tool_headroom = {k:allocation['tool_resources'][k]-sum(g['tool_grant'][k] for g in grants) for k in tr.LIMITS}
    process_headroom={k:allocation.get('process_resources',{}).get(k,0)-process_grants[k] for k in process_limits}
    return {'integrated':True,'version':VERSION,'allocation':allocation,'grants':grants,
        'process_overrun':process_debt,'process_headroom':process_headroom,
        'process_allocation_exhausted':any(process_debt[k]>process_headroom[k] for k in process_limits),
        'model_overrun':token_debt,'model_headroom':token_headroom,'model_allocation_exhausted':token_debt>token_headroom,
        'tool_overrun':tool_debt,'tool_headroom':tool_headroom,
        'tool_allocation_exhausted':any(tool_debt[k]>tool_headroom[k] for k in tr.LIMITS),
        'grant_transfer_allowed':False,'unresolved_policy':'Unclaimed/unknown/finished grants stay assigned, never refunded or transferred.',
        'closing_protection':'Full stage grant remains assigned; existing Ledger protects its tasks final tokens/calls/time. Supplier overrun beyond explicit headroom blocks admission; no billing guarantee.',
        'full_stack_comparison_admitted':False}


def constrain(root, decision):
    if not sr.REGISTRY.is_file():
        tr._binding(None,root)  # A lost canonical binding must not downgrade.
        return decision
    with sr._db() as db:
        binding = tr._binding(db,root)
        if binding is None: return decision
        from .tool_measurements import validate_trial, VERSION as measurement_protocol
        version=validate_trial(db,binding[0]['study_id'],binding[0]['id'])
        if 'tool_budget' in binding[1] and version!=measurement_protocol:
            return ClosingDecision('blocked',('legacy_measurement_scope_read_only',))
        state = status(db,binding[0]['study_id'])
        if not state['integrated']: return decision
        if decision.mode in ('blocked','terminal_without_submission'): return decision
        if state['model_allocation_exhausted']:
            return ClosingDecision('blocked',('study_model_headroom_exhausted',))
        if state['process_allocation_exhausted']:
            return ClosingDecision('blocked',('study_process_headroom_exhausted',))
        if state['tool_allocation_exhausted']:
            return ClosingDecision('close_only',tuple(decision.reasons)+('study_tool_headroom_exhausted',))
        return decision


def admit_model(db, study_id, intent):
    state=status(db,study_id)
    if not state['integrated']: return
    need(not state['model_allocation_exhausted'], 'study model headroom exhausted; no grant transfer')
    need(not state['process_allocation_exhausted'],'study process headroom exhausted; no grant transfer')
    if state['tool_allocation_exhausted']:
        need(intent['mode']=='close_only' and intent['actions']==[FINAL_ACTION], 'study tools exhausted; final-only model admission')


def admit_tool(db, study_id):
    state=status(db,study_id)
    if state['integrated']:
        need(not state['tool_allocation_exhausted'] and not state['model_allocation_exhausted']
             and not state['process_allocation_exhausted'], 'study allocation exhausted; no new tool work')
