"""Root's one concrete admission under the user's existing research authorization."""
from pathlib import Path
import json,sys,time
from datetime import datetime,timezone
P=Path.cwd();V=P/'experiment_traces/meta_ashare_revision18';sys.path.insert(0,str(V/'src'))
from quanta_agents.meta import casebank_continuation as c, casebank_supplemental as phase
scope_path=P/'experiment_traces/meta_casebank_supplemental_preparations/reference_v15_original4_001_v18s1_scope_001/scope.json'
scope_raw=c.read(scope_path);c.need(c.sha(scope_raw)=='8d60a4fdef3d94c4f952edb721f9d620c62a17f51c760e2b67a4e9ed9beef66b','scope changed')
scope=c.decode(scope_raw)
c.need(scope['start_epoch']<=time.time()<scope['end_epoch'],'fixed window has closed')
pins=[
 ('experiment_traces/meta_casebank_supplemental_scope_reviews/reference_v15_original4_001_v18s1_001/receipt.json','059620e3e117dd09c6c961704c34d752f754bd7ce6d6d9cdf25c4ad1d3f31c7d'),
 ('docs/research/meta_framework_v18_handoff/supplemental_registration_review_002/receipt.json','8554e1c5a97a2d03cd56a087c88272a77380c0e574036e98548b46fdb3a993c3'),
 ('experiment_traces/meta_diagnostic_monitor_v18/before_registration_001/receipt.json','ce0a99bfcc53a7a9311398b4ade2e3fe33e892463d17be24ca2735889fad32c0'),
 ('experiment_traces/meta_ashare_revision18/validation_artifacts/bounded_engineering_acceptance_001/receipt.json','58ed0c0c061de7de8781004af6a32c9062ec0c0f9e889d67e23189a429195b75')]
independent=[]
for path,digest in pins:
 p=P/path;c.need(c.sha(c.read(p))==digest,'readiness evidence changed');independent.append({'path':str(p),'sha256':digest})
registration=P/'docs/research/meta_framework_v18_handoff/register_supplemental_001.py'
c.need(c.sha(c.read(registration))=='d76f0a9ebd6a448bcb3f4c867899748fc59b401fb1fc63ea6ee5bd7489469a9a','registration source changed')
c.need(scope['new_source_pins']==phase.source_pins(),'runtime source changed')
admission={'kind':'casebank_supplemental_time_admission','id':'v18s1_root_admission_001','ready':True,
 'approved_at':datetime.now(timezone.utc).isoformat(),'controller_thread_id':'01a0774d-4327-7701-948f-2796de6fccd3',
 'scope_hash':c.content_hash(scope),'base_plan_hash':scope['base_plan_hash'],'original_v17_state_hash':scope['original_v17_state_hash'],
 'source_hash':c.content_hash(scope['new_source_pins']),'entrypoint_sha256':scope['entrypoint_sha256'],
 'local_application_authorized':True,'real_dispatch_authorized':True,'registration_script_sha256':c.sha(c.read(registration)),
 'runtime_observation_sha256':'11cbf7a5ee2eeffb6d74adce3ea5c82514ab6cf1c10b4b1e535a5485084aefb0',
 'pre_stage_preservation_receipt_sha256':'9dd35d09e7ba7202b96e6b2db9fc6d3b446ce87482bc4be47e8e790e4a8cabec',
 'independent_review_pins':independent,'start_epoch':scope['start_epoch'],'end_epoch':scope['end_epoch'],
 'model':'gpt-6-astra','reasoning_effort':'xhigh','provider_model_identity_attested':False,
 'authorization_basis':'User explicitly authorizes sustained isolated implementation, reasonable research and necessary large token use; one concrete supplementary development window was frozen after bounded engineering and independent actual-state readiness. This changes time resources only.',
 'permitted':'Register v18s1 once, verify saved state, start one bounded same-domain worker, inspect and pause/close as needed. All original tasks, complete own history, fees, debt, counts and final opportunities remain. No new independent sample.',
 'forbidden':'No original v15 retry, no v16 correction reuse, no old v17 paid admission change, no duplicate saved action, no automatic new-unknown retry/reserve release, no third window, no live trading/funds.',
 'original_timed_stage_outcome':'expired_incomplete','independent_task_increment':0,'formal_target_success':False}
target=P/'docs/research/meta_framework_v18_handoff/supplemental_dispatch_admission_001.json'
c.write_new(target,c.raw(admission))
print(json.dumps({'admission_sha256':c.sha(c.read(target)),'scope_sha256':c.sha(scope_raw),'new_phase_only':True}))
