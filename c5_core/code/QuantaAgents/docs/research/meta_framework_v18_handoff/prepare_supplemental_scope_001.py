"""Freeze one read-only scope only after exact engineering/source evidence is supplied."""
from pathlib import Path
import json, sys
from datetime import datetime, timezone

if sys.flags.optimize:
    raise RuntimeError('Optimized Python cannot run this reviewed administrative script')
P=Path.cwd(); V=P/'experiment_traces/meta_ashare_revision18'
sys.path.insert(0,str(V/'src'))
from quanta_agents.meta import casebank_continuation as c, casebank_supplemental as s
assert len(sys.argv)==3, 'config path and exact raw SHA256 required'
config_raw=c.read(sys.argv[1]); assert c.sha(config_raw)==sys.argv[2]
cfg=c.decode(config_raw)
assert cfg['engineering_accepted'] is True and cfg['real_dispatch_authorized'] is False
assert cfg['expected_source_pins']==s.source_pins()
assert cfg['expected_entrypoint_sha256']==s.entrypoint_sha256()
for pin in cfg['provenance_pins']: assert c.sha(c.read(pin['path']))==pin['sha256']
root=P/'experiment_traces/meta_casebank_references/campaigns/reference_v15_original4_001'
scope=s.build_supplemental_scope(root,phase_id='v18s1',start_epoch=cfg['start_epoch'],end_epoch=cfg['end_epoch'],
    original_source_root=P/'experiment_traces/meta_ashare_revision17/src/quanta_agents/meta',
    provenance_pins=cfg['provenance_pins'],allow_saved_application_after_deadline=True)
assert scope['base_plan_hash']=='3cda519a83180b6b5b4fe96c447a406efee82b5df73b31cc450fd10555437173'
out=P/'experiment_traces/meta_casebank_supplemental_preparations/reference_v15_original4_001_v18s1_scope_001'
out.mkdir(parents=True,exist_ok=False)
c.write_new(out/'scope.json',c.raw(scope));c.write_new(out/'preparation_config.json',config_raw)
receipt={'status':'scope_frozen_only_no_admission_or_registration','at':datetime.now(timezone.utc).isoformat(),
    'scope_sha256':c.sha(c.read(out/'scope.json')),'config_sha256':c.sha(config_raw),'script_sha256':c.sha(c.read(__file__)),
    'source_pins':s.source_pins(),'entrypoint_sha256':s.entrypoint_sha256(),
    'original_state_hash':scope['original_v17_state_hash'],'start_epoch':scope['start_epoch'],'end_epoch':scope['end_epoch'],
    'window_seconds':scope['end_epoch']-scope['start_epoch'],'actual_model_calls':0,'actual_workbench_actions':0,
    'phase_registered':False,'admission_created':False,'paid_dispatch_authorized':False,'original_stage_status':'expired_incomplete',
    'independent_task_increment':0,'formal_target_success':False}
c.write_new(out/'receipt.json',c.raw(receipt))
print(json.dumps({'scope_sha256':receipt['scope_sha256'],'receipt_sha256':c.sha(c.read(out/'receipt.json')),'registered':False}))
