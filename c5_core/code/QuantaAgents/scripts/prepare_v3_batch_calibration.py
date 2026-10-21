"""One separately budgeted authentic model test of the new public batch tools.

Reuses previously exposed generated inputs; never a new financial sample or
permission to resume an old stage. No model call occurs in this preparation.
"""
from copy import deepcopy
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
from quanta_agents.meta_v3.closing import ClosingPolicy
from quanta_agents.meta_v3.ledger import Ledger,digest
from quanta_agents.meta_v3.research_tools import ResearchTools,save_once
from quanta_agents.meta_v3.runtime import source_pins,ResearchRuntime


def main():
    destination=ROOT/'experiment_traces/meta_framework_v3/batch_calibration_001'
    if destination.exists():raise SystemExit('Scope already exists. Inspect it; do not re-create or extend its budget.')
    parent=ROOT/'experiment_traces/meta_framework_v3/calibration_001/plan.json'
    parent_plan=json.loads(parent.read_text(encoding='utf-8'))
    original=parent_plan['tasks']['calibration_signal_001']['case'];case=deepcopy(original)
    case['description']='Public batch-tool calibration on previously exposed generated12-session inputs. Activity is a synthetic index, not observed exchange volume. Full original cash, raw source archives, chronology and costs are unchanged. No new independent financial sample.'
    case['research_policy']={'version':'structural_research_v1','action_limits':{
        'inspect_inputs':3,'diagnose_horizons':2,'develop_strategy':3,'inspect_execution':6,'read_evidence':8,
        'diagnose_execution':3,'register_experiment':2,'request_research_extension':1,
        'register_batch':2,'execute_batch':2,'inspect_batch':8}}
    case['batch_policy']={'version':'bounded_batch_v1','max_candidates_total':12,'max_scan_cells_total':20000,
        'max_wall_seconds':600,'output_stop_threshold_bytes':134217728,
        'field_semantics':{f['name']:{'unit':f['unit'],'raw_precision':'generated decimal values; no market measurement certification','digit_derivation':None} for f in case['decision_fixture']['fields']}}
    case['evidence_sources']=[{'path':str(p),'sha256':hashlib.sha256(p.read_bytes()).hexdigest()} for p in (parent,Path(__file__))]
    assert case['decision_fixture']==original['decision_fixture'] and case['raw_source_bindings']==original['raw_source_bindings'] and case['initial_cash']==original['initial_cash']
    task={'case':case,'case_hash':digest(case),
        'idea':'识别这组资料中活动度指标与价格位置是否有值得保留的简单组合。按你的直觉排序少量候选家族，用受控批量工具事前冻结并枚举。请读完整批结果、解释成本与持仓影响，若证据支持，再登记有区分力的修订；最后给出基于执行证据的结论或弃权。',
        'documents':[{'name':'Input provenance and calibration boundary','text':'All inputs are generated and were already exposed in earlier V3 development. This validates tool use, accounting feedback and evidence-based reporting, not market alpha. Candidate programs and judgments must come from you; no controller-supplied target answer. Preserve unknown mechanisms and all failures. The full initial cash remains10000CNY. No real book, volume-in-shares, publication-time or future OOS evidence is supplied. New tools provide batches; a batch candidate reference such as execute_batch_ID/c001 can be diagnosed or used as a registered revision baseline without re-executing it.'}]}
    admission={'kind':'new_public_batch_tool_authentic_calibration','registered_at':datetime.now(timezone.utc).isoformat(),
        'source_scope':'previously_exposed_generated_fixture','parent_case_hash':digest(original),'parent_plan_file_sha256':hashlib.sha256(parent.read_bytes()).hexdigest(),
        'substantive_change':'Implemented register_batch/execute_batch/inspect_batch and candidate references with durable per-candidate/scan accounting; not a data or metadata-only extension.',
        'maximum_new_model_calls':14,'task_nominal_token_limit':1000000,'stage_nominal_token_limit':1000000,
        'call_reserve':80000,'protected_final_tokens':80000,'protected_final_seconds':3600,'maximum_wall_seconds':14400,
        'maximum_reserved_program_candidates':12,'maximum_reserved_scan_cells':20000,'real_market_strategy_runs':0,
        'external_source_acquisition_requests':0,'sealed_market_reads':0,'new_independent_financial_samples':0,
        'no_old_stage_resume_or_budget_change':True,'no_automatic_model_retry':True,'formal_target_success':False}
    ledger=Ledger.create(destination,policy=ClosingPolicy(task_calls=14,stage_calls=14,task_tokens=1000000,stage_tokens=1000000),
        tasks={'batch_tool_calibration_001':task},deadline_epoch=time.time()+14400,
        provenance={'source_pins':source_pins(),'old_v3_known_tokens':1020074,'old_v3_known_calls':40,
            'old_v2_known_tokens':491954,'old_v2_unknown_reserve':80000,'old_campaign_resume_authorized':False,
            'exposure':'Previously exposed generated12-session fixture; not new OOS or a real strategy discovery. New batch tools only.',
            'fixture_only':True,'controller_admission':admission})
    save_once(destination/'admission.json',admission)
    runtime=ResearchRuntime(destination);runtime.verify_inputs()
    tools=runtime._tools('batch_tool_calibration_001')
    assert {'register_batch','execute_batch','inspect_batch'}<=set(tools.menu())
    save_once(destination/'public_contract_at_admission.json',tools.contract())
    print(json.dumps({'root':str(destination),'case_hash':task['case_hash'],'new_model_calls_so_far':0,'admitted_maximum_model_calls':14,'model':'gpt-6-astra','effort':'xhigh','formal_target_success':False},ensure_ascii=True))


if __name__=='__main__':main()
