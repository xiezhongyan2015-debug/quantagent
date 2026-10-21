"""Choose the one explicit clock only after bounded engineering acceptance."""
from pathlib import Path
import json,hashlib,sys,time,subprocess
P=Path.cwd();V=P/'experiment_traces/meta_ashare_revision18'
sys.path.insert(0,str(V/'src'))
from quanta_agents.meta import casebank_supplemental as phase
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
engineering=V/'validation_artifacts/bounded_engineering_acceptance_001/receipt.json'
if sha(engineering)!='58ed0c0c061de7de8781004af6a32c9062ec0c0f9e889d67e23189a429195b75':raise ValueError('engineering changed')
paths=[engineering,V/'validation_artifacts/source_inventory_001/receipt.json',V/'validation_artifacts/source_inventory_001/source_files_sha256.json',
 V/'supplemental_independent_validation/005_final_saved_review/receipt.json',V/'supplemental_monitor_independent_review/001_saved_only/receipt.json',
 P/'docs/research/meta_framework_v18_handoff/supplementary_research_policy_001.json',P/'docs/research/meta_framework_v18_handoff/post_deadline_research_design_001.md',
 P/'docs/research/meta_framework_v18_handoff/supplemental_registration_review_002/receipt.json',P/'docs/research/meta_framework_v18_handoff/runtime_observation_001.json',
 P/'docs/research/meta_framework_v18_handoff/engineering_usage_snapshot_001.json',P/'docs/research/meta_framework_v8_handoff/checkpoint_20260907_026.json',
 P/'experiment_traces/meta_casebank_supplemental_preparations/reference_v15_original4_001_preservation_001/receipt.json',
 P/'docs/research/meta_framework_v17_handoff/actual_saved_action_outcome_independent_review_001.md',
 P/'docs/research/meta_framework_v15_handoff/reference_budget_001.json',P/'docs/research/meta_framework_v15_handoff/reference_evaluation_prereg_001.json']
pins=[{'path':str(p),'sha256':sha(p)} for p in paths]
start=time.time()
cfg={'kind':'one_concrete_supplemental_scope_preparation_config','engineering_accepted':True,'real_dispatch_authorized':False,
 'expected_source_pins':phase.source_pins(),'expected_entrypoint_sha256':phase.entrypoint_sha256(),'provenance_pins':pins,'start_epoch':start,'end_epoch':start+21600,
 'window_waiting_and_pauses_consume_time':True,'independent_task_increment':0,'original_stage_status':'expired_incomplete'}
target=P/'docs/research/meta_framework_v18_handoff/supplemental_scope_preparation_config_001.json'
with target.open('x',encoding='utf-8') as f:json.dump(cfg,f,ensure_ascii=False,indent=2,allow_nan=False)
cmd=[sys.executable,'-B',str(P/'docs/research/meta_framework_v18_handoff/prepare_supplemental_scope_001.py'),str(target),sha(target)]
proc=subprocess.run(cmd,cwd=P,check=False)
raise SystemExit(proc.returncode)
