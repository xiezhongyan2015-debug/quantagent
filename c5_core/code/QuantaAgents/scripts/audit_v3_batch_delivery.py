"""Export immutable batch delivery receipts and independent saved cash arithmetic."""
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
from quanta_agents.meta_v3.kernel import module
from quanta_agents.meta_v3.ledger import digest
from quanta_agents.meta_v3.research_tools import save_once
from audit_research_v3_saved import daily_audit


def audit(destination):
    task='batch_tool_calibration_001';base=ROOT/'experiment_traces/meta_framework_v3'
    scopes=[];all_history=[];reports=[];candidates=[]
    gateway=module('meta.codex_gateway')
    for scope in ('batch_calibration_001','batch_recovery_001'):
        root=base/scope;plan=json.loads((root/'plan.json').read_text(encoding='utf-8'))
        db=sqlite3.connect((root/'ledger.sqlite3').as_uri()+'?mode=ro',uri=True);db.row_factory=sqlite3.Row
        try:
            stage=dict(db.execute('SELECT * FROM stage').fetchone());calls=[dict(x) for x in db.execute('SELECT * FROM calls ORDER BY ordinal')]
            state=dict(db.execute('SELECT * FROM tasks WHERE id=?',(task,)).fetchone())
        finally:db.close()
        assert digest(plan)==stage['plan_hash'] and json.loads(stage['plan'])==plan
        rows=[]
        for call in calls:
            assert call['status'] in ('applied','failed') and call['known_tokens'] is not None
            intent=json.loads(call['intent']);receipt=json.loads(call['receipt']);result=json.loads(call['result'])
            checked=gateway.verify_saved_completion(root/'calls'/call['id'],expected_prompt_hash=intent['prompt_hash'],
                expected_schema=intent['schema'],expected_artifact_sha256=receipt['artifact_sha256'])
            assert checked['response']==receipt['response'] and checked['usage']==receipt['usage']
            assert checked['model']=='gpt-6-astra' and checked['effort']=='xhigh'
            assert checked['request_identity']['intent_id']==call['id']
            assert call['known_tokens']==checked['usage']['input_tokens']+checked['usage']['output_tokens']
            response=checked['response'];args=json.loads(response['arguments_json'])
            application=json.loads((root/'calls'/call['id']/'application.json').read_text(encoding='utf-8'))
            assert application['model_response_hash']==digest(response) and application['result']==result
            entry={'scope':scope,'id':call['id'],'status':call['status'],'action':response['action'],'known_tokens':call['known_tokens'],
                'request_model':checked['model'],'request_effort':checked['effort'],'supplier_model_verified':checked['model_verified'],
                'saved_completion_verified':True,'public_summary':response['public_summary'],'arguments':args}
            if result.get('artifact_hash'):
                artifact=json.loads((root/'tools'/task/call['id']/'artifact.json').read_text(encoding='utf-8'));assert digest(artifact)==result['artifact_hash']
                entry['artifact_hash']=digest(artifact)
                if response['action']=='execute_batch':
                    for r in artifact['candidates']:
                        work=root/'batches'/task/artifact['batch_id']/'candidates'/r['candidate_id']
                        original=json.loads((work/'worker_result.json').read_text(encoding='utf-8'))
                        assert digest(original)==r['worker_result_hash'] and digest(original['artifact'])==r['artifact_hash']
                        raw=original['artifact']['raw'];assert raw['status']=='completed_mechanical'
                        accounting=daily_audit(raw['result'],plan['tasks'][task]['case'])
                        candidates.append({'candidate_id':r['candidate_id'],'family_id':r['family_id'],'parameters':r['parameters'],
                            'status':r['status'],'account':r['account'],'independent_saved_arithmetic':accounting,
                            'worker_result_hash':digest(original),'artifact_hash':r['artifact_hash']})
                elif response['action']=='inspect_batch' and args['table']=='results':
                    entry['delivered_result_page']={k:result['public'].get(k) for k in ('offset','returned_rows','next_offset','batch_results_hash','registration_evidence_id','delivery_blocked')}
            if response['action']=='submit_research_report':
                assert result.get('model_report')==args and result.get('legal_submission') is True
                reports.append({'scope':scope,'id':call['id'],'model_report':args,'authentic_saved_response_verified':True})
            rows.append(entry);all_history.append(entry)
        scopes.append({'scope':scope,'plan_hash':digest(plan),'paused':bool(stage['paused']),'pause_reason':stage['reason'],
            'terminal':state['terminal'],'final_call':state['final_call'],'calls':rows,'known_tokens':sum(x['known_tokens'] for x in rows),
            'unknown_or_pending_reserve':0,'source_pins_recorded':plan['provenance']['source_pins']})
    assert len(candidates)==9 and len(reports)==1 and reports[0]['scope']=='batch_recovery_001'
    covered=set();hashes=set()
    for h in all_history:
        p=h.get('delivered_result_page')
        if p and not p['delivery_blocked']:
            covered.update(range(p['offset'],p['offset']+p['returned_rows']));hashes.add(p['batch_results_hash'])
    assert covered==set(range(9)) and len(hashes)==1
    assert all(x['action'] in ('inspect_batch','inspect_execution','diagnose_execution','read_evidence','submit_research_report') for x in scopes[1]['calls'])
    claim=json.loads((base/'batch_calibration_001'/f'{task}_saved_recovery_claim.json').read_text(encoding='utf-8'))
    assert all(hashlib.sha256(Path(p['path']).read_bytes()).hexdigest()==p['sha256'] for p in claim['evidence_sources'])
    total=sum(s['known_tokens'] for s in scopes)
    value={'kind':'authentic_batch_saved_recovery_audit_v1','observed_at':datetime.now(timezone.utc).isoformat(),'scopes':scopes,
        'candidates':candidates,'reports':reports,'all_nine_result_rows_delivered':True,'independent_saved_account_arithmetic_passed':9,
        'parent_manifest_files_unchanged':len(claim['evidence_sources']),'new_candidate_runs_in_recovery':0,
        'new_model_calls_in_this_audit':0,'total_batch_and_recovery_known_tokens':total,
        'v3_known_tokens_including_previous_1020074':1020074+total,'v3_known_calls_including_previous40':40+len(all_history),
        'old_v2_known_tokens':491954,'old_v2_unknown_reserve':80000,'original_interruption_cause':'controller public-contract defect; not model reasoning failure',
        'uninterrupted_original_delivery_success':False,'new_independent_financial_samples':0,'formal_success_denominator':0,
        'formal_target_success':False,'limitations':['Generated previously exposed12-session data, not real market or OOS alpha.',
            'Read-only arithmetic certifies saved computation only; no actual provider identity, broker fills or historical availability certification.',
            'No new registered revision or strategy was admitted during recovery. Fixed/random generator fairness and intraday book-digit search remain untested.']}
    save_once(destination,value)
    return value


if __name__=='__main__':
    output=ROOT/'validation/research_batch_contract_repair_001/authentic_delivery_audit.json'
    result=audit(output)
    print(json.dumps({'path':str(output),'sha256':hashlib.sha256(output.read_bytes()).hexdigest(),'v3_known_calls':result['v3_known_calls_including_previous40'],
        'v3_known_tokens':result['v3_known_tokens_including_previous_1020074'],'formal_target_success':False},ensure_ascii=True))
