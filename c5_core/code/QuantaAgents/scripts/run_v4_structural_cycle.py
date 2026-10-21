"""Run one frozen real research cycle and its sole admitted data extension.

No action responses are authored here. No model retry, hidden scope renewal,
download or old-study resumption is performed by this controller.
"""
from datetime import datetime, timezone
from pathlib import Path
import json
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from quanta_agents.meta_v3.closing import ClosingPolicy
from quanta_agents.meta_v3.extension_handoff import inspect_handoff
from quanta_agents.meta_v3.ledger import Ledger, digest, need, serial
from quanta_agents.meta_v3.research_extension import decide_extension, _parent_request
from quanta_agents.meta_v3.research_tools import save_once
from quanta_agents.meta_v3.runtime import ResearchRuntime
from quanta_agents.meta_v3.saved_ohlc_extension import prepare
from quanta_agents.meta_v3.source_admission import source_paths

SCOPE = ROOT / 'experiment_traces/v4c1'
TASK = 'ap'
GUI = ROOT / 'docs/research/meta_framework_v4_handoff/gui_status_001.json'


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def event(kind, **details):
    row = {'time': datetime.now(timezone.utc).isoformat(), 'kind': kind, **details}
    with (SCOPE / 'cycle_events.jsonl').open('a', encoding='utf-8') as stream:
        stream.write(serial(row) + '\n')
    print(serial(row), flush=True)


def update_gui(active, status, summary):
    value = read(GUI)
    value.update(observed_at=datetime.now(timezone.utc).isoformat(),
        research_root=str(active), worker_identity_path=str(active/'worker_identity.json'),
        verification_path=str(SCOPE/'cycle_verification.json'),
        next_action=summary,
        prior_v4_research_calls=3, prior_v4_research_known_tokens=52182,
        cycle_root=str(SCOPE), formal_target_success=False)
    value['stages'][0].update(status='研究主链已接通',
        summary='实验设计检查、扩展交接、资料追加和独立子研究已接入；真实运行按冻结额度选择动作。',
        details=['设计审查给出可检验性警告', '资料扩展只接受真实模型请求并保留独立账本',
                 '旧范围和旧费用记录保持独立'],
        gap='全套正式样本外与公平架构比较尚未通过。')
    value['stages'][1].update(status=status, summary=summary,
        details=['新问题：交易活跃与价格推进不一致是否存在可交易信息',
                 '父研究最多14次调用；一次追加字段子研究最多10次；Astra / xhigh',
                 '100万元完整本金、2019年244日单股，费用与容量采用原冻结机械口径'],
        gap='已暴露开发资料；真实到达、真实费用/容量及独立样本外仍未认证。')
    value['stages'][2].update(status='准入缺口已核对',
        summary='完整真实研究循环正在验证；正式金融与架构验收需要更多合格资料。',
        gap='244日低于252日OOS最低门槛；6个合格真实留出case尚未具备。')
    temporary = GUI.with_suffix('.tmp')
    temporary.write_text(serial(value), encoding='utf-8')
    temporary.replace(GUI)


def admissions(parent_case, prepared):
    """Explicit metadata for preserved paths; never infer 2019 from raw files."""
    result = list(prepared['source_admissions'])
    for name, kind in source_paths(parent_case).items():
        path = Path(name)
        disclosure = ('company_action_evidence_' in name)
        raw_inventory = kind == 'market' or 'meta_development_raw_coverage_v13' in name
        result.append({'path': name,
            'role': 'historical_notice' if disclosure else 'development_market_data',
            'partition': 'public_disclosure' if disclosure else 'exposed_2017_2021',
            'content_date_range': (['2019-01-01', datetime.now().date().isoformat()] if disclosure
                else ['2017-01-01','2021-12-31'] if raw_inventory else ['2019-01-01','2019-12-31'])})
    return result


def grant_request(plan, request_id):
    folder = SCOPE / 'controller_extensions' / request_id
    if (folder/'decision.json').exists():
        return read(folder/'decision.json')
    # An interrupted grant must be explicitly reconciled using its exact saved
    # deadline and case, never rebuilt with a fresh clock or budget.
    need(not (folder/'intent.json').exists(), 'unfinished controller admission requires exact saved reconciliation')
    _, task, artifact, _, _ = _parent_request(Ledger(SCOPE), TASK, request_id)
    body = artifact['request']
    service = plan['provenance']['extension_service_policy']
    fields = body.get('requested_fields')
    requested = body['requested_resource_bounds']
    wall = min(service['maximum_child_wall_seconds'], requested['wall_seconds'],
               int(plan['deadline_epoch'] - time.time() - plan['policy']['closing_seconds']))
    calls = min(service['maximum_child_model_calls'], requested['model_calls'])
    eligible = (body['request_kind']=='data' and isinstance(fields,list) and bool(fields)
        and set(fields)<=set(service['fields']) and requested['symbols']>=1
        and requested['sessions']>=244 and calls>=1 and wall>=120)
    decision = {'status':'ready' if eligible else 'needs_implementation',
        'reason': ('Deliver the actually requested additive OHLC cells from the already pinned selected rows. '
                   'Same axes and capital; original rows and execution semantics preserved; independent child budget.' if eligible else
                   'This frozen service can only add open/high/low for the same 244-session sh600004 grid. '
                   'Requested deliverables or remaining resources do not fit; no new data, budget or model call was granted.'),
        'asset_contract':'a_share_cash_equity', 'resource_bounds':None,
        'exposure_statement':'Previously exposed 2019 development; no new market samples, no OOS or matched-scope performance claim. All parent and child costs retained.'}
    if not eligible:
        return decide_extension(SCOPE,TASK,request_id,decision=decision)
    need(len(list((SCOPE/'controller_extensions').glob('*/decision.json'))) == 0,
         'single extension maximum reached')
    prepared = prepare(SCOPE,TASK,request_id,folder/'inputs')
    child = prepared['child_case']
    # The sole permitted extension is consumed; no recursive scope renewal.
    child['research_policy']['action_limits']['request_research_extension'] = 0
    bounds = {'symbols':1, 'sessions':244, 'model_calls':calls, 'download_bytes':0, 'wall_seconds':wall}
    need(set(bounds)==set(requested),'unexpected requested resource dimensions')
    decision.update(resource_bounds=bounds, source_admissions=admissions(task['case'],prepared))
    policy = ClosingPolicy(task_calls=calls,stage_calls=calls,
        task_tokens=service['maximum_child_tokens'],stage_tokens=service['maximum_child_tokens'],
        closing_seconds=min(600,max(30,wall//5)))
    return decide_extension(SCOPE,TASK,request_id,decision=decision,child_case=child,
        policy=policy,deadline_epoch=time.time()+wall-2)


def audit(stages):
    summaries=[]
    for root, task_id in stages:
        runtime=ResearchRuntime(root)
        runtime.verify_inputs()
        state=runtime.ledger.status(task_id)
        rows=[]
        for item in state['calls']:
            call=runtime.ledger.call(item['id'])
            need(call['status'] in ('applied','failed'),'unsettled call cannot be audited as complete')
            receipt=runtime.gateway_module.verify_saved_completion(root/'calls'/call['id'],
                expected_prompt_hash=call['intent']['prompt_hash'],expected_schema=call['intent']['schema'],
                expected_artifact_sha256=call['receipt']['artifact_sha256'])
            need(receipt['response']==call['receipt']['response'] and receipt['usage']==call['receipt']['usage'],
                 'saved model response or usage drift')
            need(receipt['runtime_identity']['verified'] is True,'runtime identity unverified')
            result=call.get('result') or {}
            if result.get('artifact_hash'):
                need(digest(read(root/'tools'/task_id/call['id']/'artifact.json'))==result['artifact_hash'],
                     'tool artifact drift')
            rows.append({'call_id':call['id'],'action':receipt['response']['action'],
                'status':call['status'],'known_tokens':call['known_tokens'],
                'runtime_identity':receipt['runtime_identity'],'result':result})
        summaries.append({'root':str(root),'task_id':task_id,'terminal':state['terminal'],
            'calls':rows,'known_tokens':state['known_tokens'],
            'unknown_or_pending_reserve':state['unknown_or_pending_reserve']})
    result={'kind':'v4_structural_cycle_saved_audit_v1','stages':summaries,
        'research_model_calls':sum(len(s['calls']) for s in summaries),
        'research_known_tokens':sum(s['known_tokens'] for s in summaries),
        'runtime_configuration_verified_every_call':True,'provider_identity_independently_verified':False,
        'source_and_tool_hashes_verified':True,'prior_v4_research_calls':3,'prior_v4_research_tokens':52182,
        'diagnostic_calls_separate':1,'diagnostic_tokens_separate':8691,
        'financial_execution':'Declared development simulation on full cash and calendar, not certified actual costs/capacity.',
        'formal_architecture_comparison':False,'formal_target_success':False,
        'provider_currency_cost':None,'controller_and_subagent_currency_cost':None}
    need(result['research_model_calls']<=24,'cycle exceeded frozen call maximum')
    save_once(SCOPE/'cycle_verification.json',result)
    return result


def saved_stages():
    """Recover every already granted child before resumption or cost audit."""
    stages=[(SCOPE,TASK)]
    handoff=inspect_handoff(SCOPE,TASK)
    for row in handoff['requests']:
        path=SCOPE/'controller_extensions'/row['request_id']/'decision.json'
        if path.exists():
            decision=read(path)
            if decision.get('child_root'):
                stages.append((Path(decision['child_root']),'extension'))
    need(len(stages)<=2,'cycle contains more than the single admitted child')
    return stages


def main():
    plan=read(SCOPE/'plan.json')
    for name, sha in plan['provenance']['controller_scripts'].items():
        need(digest((ROOT/'scripts'/name).read_text(encoding='utf-8'))==sha,'frozen controller script drift')
    service=plan['provenance']['extension_service_policy']
    need(service['version']=='saved_ohlc_service_v1' and service['maximum_extensions']==1
         and service['maximum_total_model_calls']==24,'unexpected cycle grant')
    stages=saved_stages()
    try:
        for round_index in range(4):
            update_gui(SCOPE,'完整研究循环运行中','正在运行冻结研究，模型自行选择资料检查、策略执行与诊断。')
            event('parent_run',round=round_index)
            ResearchRuntime(SCOPE).run()
            state=Ledger(SCOPE).status(TASK)
            if state['terminal']:
                break
            handoff=inspect_handoff(SCOPE,TASK)
            need(handoff['requests'],'parent stopped without terminal or an actionable extension request')
            for row in handoff['requests']:
                if row['status']=='pending_controller':
                    decision=grant_request(plan,row['request_id'])
                    event('extension_decided',request_id=row['request_id'],decision=decision)
                else:
                    decision=read(SCOPE/'controller_extensions'/row['request_id']/'decision.json')
                if decision['child_root']:
                    child=Path(decision['child_root'])
                    if (child,'extension') not in stages:
                        stages.append((child,'extension'))
                    child_state=Ledger(child).status('extension')
                    if not child_state['terminal']:
                        update_gui(child,'资料扩展子研究运行中','模型请求的新字段已准入，独立子研究运行后将报告返回父研究。')
                        event('child_run',root=str(child))
                        ResearchRuntime(child).run()
                        need(Ledger(child).status('extension')['terminal'],'child stopped without terminal; no automatic retry')
        else:
            raise ValueError('bounded controller handoff rounds exhausted')
        result=audit(saved_stages())
        update_gui(SCOPE,'完整研究循环已结束',
            f"本轮父/子研究共{result['research_model_calls']}次调用、{result['research_known_tokens']}个已知token；原始报告与证据已保存。")
        event('cycle_complete',research_calls=result['research_model_calls'],known_tokens=result['research_known_tokens'])
    except Exception as exc:
        event('cycle_stopped',error_type=type(exc).__name__,error=str(exc)[:3000])
        update_gui(SCOPE,'研究暂停待核对记录','流程出现错误；已保留原始调用和费用，控制器正在核对，未自动重试模型。')
        raise


if __name__=='__main__':
    main()
