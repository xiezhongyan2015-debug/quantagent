"""Register the independently reviewed phase once, without any model execution."""
from pathlib import Path
import json, sqlite3, sys, traceback
from datetime import datetime, timezone

P=Path.cwd(); V=P/'experiment_traces/meta_ashare_revision18'
sys.path.insert(0,str(V/'src'))
from quanta_agents.meta import casebank_continuation as c, casebank_supplemental as s, codex_gateway, diagnostic_monitor
assert len(sys.argv)==3, 'exact scope and admission SHA256 required'
root=P/'experiment_traces/meta_casebank_references/campaigns/reference_v15_original4_001'
scope_path=P/'experiment_traces/meta_casebank_supplemental_preparations/reference_v15_original4_001_v18s1_scope_001/scope.json'
admission_path=P/'docs/research/meta_framework_v18_handoff/supplemental_dispatch_admission_001.json'
scope_raw=c.read(scope_path); admission_raw=c.read(admission_path,65536)
assert c.sha(scope_raw)==sys.argv[1] and c.sha(admission_raw)==sys.argv[2]
scope=c.decode(scope_raw); admission=c.decode(admission_raw)
assert admission['registration_script_sha256']==c.sha(c.read(__file__))
assert admission['real_dispatch_authorized'] is True and admission['local_application_authorized'] is True
for pin in admission['independent_review_pins']: assert c.sha(c.read(pin['path']))==pin['sha256']
runtime=c.decode(c.read(P/'docs/research/meta_framework_v18_handoff/runtime_observation_001.json'))
for item in runtime['files']: assert c.sha(Path(item['path']).read_bytes())==item['sha256']
attempts=[]
def no_gateway(*args,**kwargs):
    attempts.append('blocked'); raise RuntimeError('registration cannot execute a model')
codex_gateway.CodexGateway.run=no_gateway
controller=s.SupplementalStage(root,scope=scope,admission=admission_raw,expected_admission_sha256=c.sha(admission_raw))
baseline=P/'experiment_traces/meta_casebank_supplemental_preparations/reference_v15_original4_001_preservation_001'
saved=c.decode(c.read(baseline/'receipt.json'))
for item in saved['backups']:
    assert c.sha(Path(item['source']).read_bytes())==item['source_sha256_before_after']
    assert c.sha((baseline/item['backup']).read_bytes())==item['backup_sha256']
out=root/'v18_supplemental_registration_001';out.mkdir(exist_ok=False)
def dump(n,x): c.write_new(out/n,json.dumps(x,ensure_ascii=False,indent=2,allow_nan=False).encode('utf-8'))
dump('intent.json',{'at':datetime.now(timezone.utc).isoformat(),'scope_sha256':c.sha(scope_raw),'admission_sha256':c.sha(admission_raw),'operation':'prepare once, inspect saved, verify read-only monitor; model execution is forbidden in this registration process','pre_stage_full_backup_receipt_sha256':c.sha(c.read(baseline/'receipt.json'))})
try:
    before=controller.summary();dump('summary_before.json',before)
    assert before['status']=='paused' and before['total_calls']==2
    result=controller.prepare();dump('prepared.json',result)
    inspection=controller.inspect_saved();dump('inspection.json',inspection)
    assert inspection['status']=='saved_evidence_verified'
    assert result['status']=='ready' and result['supplemental_phase']['id']=='v18s1'
    assert result['total_calls']==2 and result['accounting']['reported_tokens']==11235
    assert result['accounting']['reserved_tokens']==80000
    assert sum(t['queries'] for t in result['tasks'].values())==1
    assert all(t['candidates']==t['finals']==0 for t in result['tasks'].values())
    assert not result['supplemental_phase']['timing_violation']
    for item in saved['backups'][1:]: assert c.sha(Path(item['source']).read_bytes())==item['source_sha256_before_after']
    with sqlite3.connect((root/'ledger.sqlite3').as_uri()+'?mode=ro',uri=True) as db:
        state=c._saved(db,c.TABLE)[0]
        for key in ('id','scope','scope_hash','admission_json','admission_sha256'): assert c.same(state[key],scope['original_v17_state'][key])
        original_rows=c.decode(c.read(baseline/'original_rows.json'))
        for table in ('runs','calls','transport_corrections_v16',c.CALLS,c.ACTIONS):
            rows=[list(row) for row in db.execute('SELECT body FROM '+table+' ORDER BY rowid').fetchall()]
            assert rows==original_rows[table]
    snapshot=diagnostic_monitor.snapshot(P);display=next(x for x in snapshot['campaigns'] if x['id']=='casebank:'+root.name)
    dump('monitor_state.json',display)
    assert display['calls_reserved']==2 and display['usage']['reported_tokens']==11235 and display['usage']['reserved_tokens']==80000
    assert display['supplemental_phase']['id']=='v18s1' and not attempts
    dump('receipt.json',{'status':'supplemental_phase_registered_saved_state_verified_no_model','at':datetime.now(timezone.utc).isoformat(),'scope_sha256':c.sha(scope_raw),'admission_sha256':c.sha(admission_raw),'original_scope_admission_and_old_rows_unchanged':True,'four_workbenches_unchanged':True,'new_model_calls':0,'new_workbench_actions':0,'known_tokens':11235,'unknown_reserve':80000,'transports':2,'original_stage_status':'expired_incomplete','independent_task_increment':0,'worker_started':False,'formal_target_success':False,'artifacts':{p.name:c.sha(p.read_bytes()) for p in out.iterdir() if p.is_file()}})
    print(json.dumps({'status':'registered_no_model','receipt_sha256':c.sha(c.read(out/'receipt.json'))}))
except BaseException as error:
    dump('failure.json',{'error':repr(error),'traceback':traceback.format_exc(),'gateway_attempts':attempts,'automatic_retry_authorized':False})
    raise
