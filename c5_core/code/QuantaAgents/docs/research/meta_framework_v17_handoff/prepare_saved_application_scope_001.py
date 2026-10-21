from pathlib import Path
import hashlib,json,sqlite3,sys
from datetime import datetime,timezone
p=Path.cwd(); stage=p/'experiment_traces/meta_ashare_revision17';sys.path.insert(0,str(stage/'src'))
from quanta_agents.meta import casebank_continuation as cc
root=p/'experiment_traces/meta_casebank_references/campaigns/reference_v15_original4_001'
out=p/'experiment_traces/meta_casebank_continuation_preparations/reference_v15_original4_001_v17c1_scope_001'
provenance=[
'docs/research/meta_framework_v15_handoff/reference_budget_001.json',
'docs/research/meta_framework_v15_handoff/reference_scope_001.json',
'docs/research/meta_framework_v15_handoff/reference_evaluation_prereg_001.json',
'docs/research/meta_framework_v15_handoff/reference_evaluation_prereg_001.md',
'docs/research/meta_framework_v8_handoff/checkpoint_20260907_023.json',
'docs/research/meta_framework_v17_handoff/saved_action_continuation_design_001.md',
'experiment_traces/meta_casebank_continuation_preparations/reference_v15_original4_001_preservation_001/receipt.json',
'experiment_traces/meta_ashare_revision17/validation_artifacts/bounded_engineering_acceptance_001/receipt.json',
'experiment_traces/meta_ashare_revision17/validation_artifacts/source_inventory_002/source_files_sha256.json',
'experiment_traces/meta_ashare_revision17/validation_artifacts/source_inventory_002/receipt.json',
'experiment_traces/meta_ashare_revision17/continuation_independent_validation/002_core_saved_review/receipt.json',
'docs/research/meta_framework_v16_handoff/transport_correction_outcome_independent_001.md'
]
files=[p/x for x in provenance];assert all(x.is_file() for x in files)
def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()
accept=json.loads(files[7].read_bytes());assert accept['engineering_accepted'] is True and accept['paid_dispatch_authorized'] is False
# These are deliberate read-only snapshots; no controller constructor, Store, Gateway, Workbench or registry writes.
def old_rows():
 with sqlite3.connect((root/'ledger.sqlite3').as_uri()+'?mode=ro',uri=True) as db:
  table_names=[x[0] for x in db.execute("SELECT name FROM sqlite_master WHERE type='table'")]
  assert 'casebank_continuation_v17' not in table_names
  return {t:db.execute('SELECT body FROM '+t+' ORDER BY rowid').fetchall() for t in ['runs','calls','transport_corrections_v16']}
before=old_rows();pins=[{'path':str(f),'sha256':digest(f)} for f in files]
scope=cc.build_scope(root,continuation_id='v17c1',provenance_pins=pins,allow_saved_application_after_deadline=True)
assert before==old_rows();assert scope['created_epoch']==1788728483.6826499
assert scope['budget']['max_runtime_seconds']==21600
out.mkdir(parents=True,exist_ok=False);body=cc.raw(scope);cc.write_new(out/'scope.json',body)
intent={'kind':'readonly_saved_action_continuation_scope_preparation','created_at':datetime.now(timezone.utc).isoformat(),'base_campaign':str(root),'continuation_id':'v17c1','scope_file_sha256':cc.sha(body),'scope_hash':cc.content_hash(scope),'provenance_pins':pins,'only_next_authorized_activity_under_review':'Append same-ledger continuation, apply existing original correction inspect_inputs exactly once, inspect saved result, and pause. No model transport.','allow_saved_application_after_deadline':True,'deadline_for_new_models_utc':'2026-09-07T03:01:23.682649Z','original_deadline_unchanged':True,'planned_real_dispatch_authorized':False,'old_rows_unchanged':True,'workbench_actions_added':0,'new_model_calls':0,'ready_admission_created':False,'formal_target_success':False}
cc.write_new(out/'receipt.json',json.dumps(intent,ensure_ascii=False,indent=2).encode('utf-8'));print(json.dumps({'out':str(out),'scope_file_sha256':cc.sha(body),'scope_hash':cc.content_hash(scope),'receipt_sha256':digest(out/'receipt.json'),'budget':scope['budget'],'snapshots':{k:{'status':v['status'],'queries':v['queries'],'actions':len(v['actions'])} for k,v in scope['historical_workbench_snapshots'].items()}}))