"""Read-only original-source verification and SQLite backups before a new phase."""
from pathlib import Path
import json, sqlite3, sys
from datetime import datetime, timezone

P=Path.cwd()
V=P/'experiment_traces/meta_ashare_revision17'
sys.path.insert(0,str(V/'src'))
from quanta_agents.meta import casebank_continuation as c, codex_gateway
OUT=P/'experiment_traces/meta_casebank_supplemental_preparations/reference_v15_original4_001_preservation_001'
ROOT=P/'experiment_traces/meta_casebank_references/campaigns/reference_v15_original4_001'
S=P/'experiment_traces/meta_casebank_continuation_preparations/reference_v15_original4_001_v17c1_scope_001/scope.json'
A=P/'docs/research/meta_framework_v17_handoff/saved_local_application_admission_001.json'
assert c.sha(S.read_bytes())=='63f93f90b7842fa39b3c0d037c661255ab496ce73c11c84099b4545d52552edb'
assert c.sha(A.read_bytes())=='f0477ce8d2fbc97e1944a4002ca42d2d6df9bdbb4739b90e44e994f9e565296f'
def fail(*args,**kwargs): raise AssertionError('No model allowed in preservation')
codex_gateway.CodexGateway.run=fail
controller=c.CasebankContinuation(ROOT,scope=c.decode(S.read_bytes()),admission=A.read_bytes(),expected_admission_sha256=c.sha(A.read_bytes()))
summary=controller.summary()
assert summary['status']=='paused' and summary['total_calls']==2
assert summary['accounting']['reported_tokens']==11235 and summary['accounting']['reserved_tokens']==80000
assert sum(t['queries'] for t in summary['tasks'].values())==1
assert all(t['candidates']==t['finals']==0 for t in summary['tasks'].values())
inspection=controller.inspect_saved()
assert inspection['status']=='saved_evidence_verified'
OUT.mkdir(parents=True,exist_ok=False)
def dump(n,v): c.write_new(OUT/n,json.dumps(v,ensure_ascii=False,indent=2,allow_nan=False).encode('utf-8'))
dump('intent.json',{'at':datetime.now(timezone.utc).isoformat(),'operation':'Readonly original v17 verification; complete SQLite backups of original campaign and four workbenches','new_window_created':False,'paid_authorized':False,'source_pins':c.runtime_source_pins(),'script_sha256':c.sha(Path(__file__).read_bytes())})
dump('summary_before.json',summary);dump('inspection_before.json',inspection)
def backup(path,name):
    before=c.sha(path.read_bytes())
    with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True) as db:
        with sqlite3.connect(OUT/name) as target: db.backup(target)
    assert c.sha(path.read_bytes())==before
    return {'source':str(path),'source_sha256_before_after':before,'backup':name,'backup_sha256':c.sha((OUT/name).read_bytes())}
backups=[backup(ROOT/'ledger.sqlite3','campaign_ledger.sqlite3')]
for i,(tid,location) in enumerate(controller.plan['controller_paths'].items(),1):
    record=backup(Path(location['root'])/'casebank_workbench.sqlite3',f'workbench_{i}.sqlite3');record['task_id']=tid;backups.append(record)
assert controller.summary()==summary
assert controller.inspect_saved()==inspection
with sqlite3.connect((ROOT/'ledger.sqlite3').as_uri()+'?mode=ro',uri=True) as db:
    assert not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='casebank_supplemental_time_v18'").fetchone()
    rows={t:db.execute('SELECT body FROM '+t+' ORDER BY rowid').fetchall() for t in ['runs','calls','transport_corrections_v16',c.TABLE,c.CALLS,c.ACTIONS]}
dump('original_rows.json',rows)
dump('receipt.json',{'status':'original_paused_single_query_state_fully_preserved','at':datetime.now(timezone.utc).isoformat(),'backups':backups,'original_rows_hash':c.content_hash(rows),'window_created':False,'actual_model_calls':0,'actual_workbench_actions':0,'inspection_saved_evidence_verified':True,'source_writes':0,'formal_target_success':False,'artifacts':{p.name:c.sha(p.read_bytes()) for p in OUT.iterdir() if p.is_file()}})
print(json.dumps({'status':'preserved','receipt_sha256':c.sha((OUT/'receipt.json').read_bytes()),'backup_count':len(backups)}))
