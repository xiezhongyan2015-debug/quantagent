"""Freeze five databases while the research stage is paused; source connections are read-only."""
from pathlib import Path
from datetime import datetime, timezone
import hashlib, json, sqlite3, sys
if sys.flags.optimize: raise RuntimeError('No optimized administrative execution')
P=Path.cwd(); V=P/'experiment_traces/meta_ashare_revision18'; sys.path.insert(0,str(V/'src'))
from quanta_agents.meta import casebank_continuation as core, casebank_supplemental as supplemental
from quanta_agents.meta import codex_gateway
ROOT=P/'experiment_traces/meta_casebank_references/campaigns/reference_v15_original4_001'
OUT=P/'experiment_traces/meta_casebank_budget_preparations/reference_v15_original4_001_preservation_001'
S=P/'experiment_traces/meta_casebank_supplemental_preparations/reference_v15_original4_001_v18s1_scope_001/scope.json'
A=P/'docs/research/meta_framework_v18_handoff/supplemental_dispatch_admission_001.json'
def sha(data): return hashlib.sha256(data).hexdigest()
def need(value, reason):
    if not value: raise RuntimeError(reason)
need(sha(S.read_bytes())=='8d60a4fdef3d94c4f952edb721f9d620c62a17f51c760e2b67a4e9ed9beef66b','scope changed')
need(sha(A.read_bytes())=='e57a5ed310ea7d8abd3fa43177fe0e07e1797d7665f00387c52cb8b5f230a163','admission changed')
def forbid(*args,**kwargs): raise RuntimeError('No Gateway in saved-only preservation')
codex_gateway.CodexGateway.run=forbid
c=supplemental.SupplementalStage(ROOT,scope=json.loads(S.read_bytes()),admission=A.read_bytes(),expected_admission_sha256=sha(A.read_bytes()))
before=c.summary()
need(before['status']=='paused' and before['total_calls']==15 and before['accounting']['reported_tokens']==491954
    and before['accounting']['reserved_tokens']==80000,'stage changed')
need(before['pause_reason']=='admission stopped: original nominal exposure cap exhausted','different pause')
inspection=c.inspect_saved();need(inspection['status']=='saved_evidence_verified','unresolved saved evidence')
OUT.mkdir(parents=True,exist_ok=False)
def save(name,value):
    with (OUT/name).open('x',encoding='utf-8') as f:json.dump(value,f,ensure_ascii=False,indent=2,allow_nan=False)
save('intent.json',{'at':datetime.now(timezone.utc).isoformat(),'operation':'Five full SQLite backups before separately reviewed known-budget management',
    'actual_model_calls':0,'actual_workbench_actions':0,'actual_source_writes':0,'script_sha256':sha(Path(__file__).read_bytes())})
save('summary_before.json',before);save('inspection_before.json',inspection)
paths=[(None,ROOT/'ledger.sqlite3','campaign_ledger.sqlite3')]
for i,task in enumerate(c.plan['tasks'],1):
    tid=task['task_run_id']; paths.append((tid,Path(c.plan['controller_paths'][tid]['root'])/'casebank_workbench.sqlite3',f'workbench_{i}.sqlite3'))
backups=[]
for tid,path,name in paths:
    digest=sha(path.read_bytes())
    with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True) as src:
        src.execute('PRAGMA query_only=ON')
        with sqlite3.connect(OUT/name) as dst:src.backup(dst)
        tables=[r[0] for r in src.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        counts={t:src.execute('SELECT COUNT(*) FROM "'+t.replace('"','""')+'"').fetchone()[0] for t in tables}
    need(sha(path.read_bytes())==digest,'source changed during backup')
    backups.append({'task_id':tid,'source':str(path),'source_sha256_before_after':digest,'source_bytes':path.stat().st_size,
        'backup':name,'backup_sha256':sha((OUT/name).read_bytes()),'table_row_counts':counts})
for item in backups:need(sha(Path(item['source']).read_bytes())==item['source_sha256_before_after'],'source changed after backup')
need(c.summary()==before,'governance changed during preservation')
save('receipt.json',{'status':'paused_budget_state_and_five_databases_preserved','at':datetime.now(timezone.utc).isoformat(),
    'backups':backups,'current_governance_state_hash':before['supplemental_phase']['current_state_hash'],
    'known_tokens':491954,'original_unknown_reserve':80000,'total_calls':15,'actual_model_calls':0,
    'actual_workbench_actions':0,'source_writes':0,'management_applied':False,'dispatch_authorized':False,
    'formal_target_success':False,'artifacts':{p.name:sha(p.read_bytes()) for p in OUT.iterdir() if p.is_file()}})
print(json.dumps({'receipt_sha256':sha((OUT/'receipt.json').read_bytes()),'backups':len(backups)}))
