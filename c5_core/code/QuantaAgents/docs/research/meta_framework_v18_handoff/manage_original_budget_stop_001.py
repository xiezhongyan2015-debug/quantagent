"""Root's explicit prepare/apply wrapper. No model dispatch or resume exists here."""
from pathlib import Path
from datetime import datetime, timezone
import importlib.util, json, sys, traceback
if sys.flags.optimize: raise RuntimeError('No optimized administrative execution')
P=Path.cwd(); V=P/'experiment_traces/meta_ashare_revision18'
sys.path.insert(0,str(V/'src'))
from quanta_agents.meta import casebank_continuation as c, casebank_supplemental as s, diagnostic_monitor
ROOT=P/'experiment_traces/meta_casebank_references/campaigns/reference_v15_original4_001'
AREA=P/'experiment_traces/meta_casebank_budget_management_v18'
PREP=P/'experiment_traces/meta_casebank_budget_preparations/reference_v15_original4_001_case01_management_001'
ACTUAL=ROOT/'v18_case01_budget_management_001'
def need(ok, why):
    if not ok: raise RuntimeError(why)
def write(directory,name,value):
    with (directory/name).open('x',encoding='utf-8') as f:json.dump(value,f,ensure_ascii=False,indent=2,allow_nan=False)
need(len(sys.argv)==5,'command config_path config_sha256 expected_manager_sha256 required')
command,config_path,config_sha,manager_sha=sys.argv[1:]
need(command in ('prepare','apply'),'no resume or dispatch command')
cfg_raw=c.read(config_path,65536);need(c.sha(cfg_raw)==config_sha,'configuration byte hash changed')
cfg=c.decode(cfg_raw)
need(cfg['wrapper_sha256']==c.sha(c.read(__file__)),'root administrative wrapper changed')
need(c.sha(c.read(AREA/'budget_management.py'))==manager_sha==cfg['manager_sha256'],'reviewed manager changed')
need(cfg['campaign_root']==str(ROOT),'only the named original campaign')
for pin in cfg['provenance_pins']:need(c.sha(c.read(pin['path']))==pin['sha256'],'review/provenance changed')
spec=importlib.util.spec_from_file_location('reviewed_budget_management',AREA/'budget_management.py')
manager=importlib.util.module_from_spec(spec);spec.loader.exec_module(manager)
directory=PREP if command=='prepare' else ACTUAL
directory.mkdir(parents=True,exist_ok=False)
write(directory,'intent.json',{'command':command,'at':datetime.now(timezone.utc).isoformat(),
    'configuration_sha256':config_sha,'manager_sha256':manager_sha,'root_thread_id':'01a0774d-4327-7701-948f-2796de6fccd3',
    'model_dispatch_authorized':False,'resume_authorized':False,'first_task_may_receive_more_calls':False})
try:
    if command=='prepare':
        need(cfg['operation']=='prepare_saved_budget_management_scope','wrong prepare configuration')
        scope=manager.prepare(ROOT,scope_id='reference_v15_original4_001_case01_budget_001',provenance_pins=cfg['provenance_pins'])
        need(scope['pre_state_hash']=='551ee2ab4442003db718fe6966499d17d34b9a2a3708bc8fcc456d7df45f032a','stopped actual state changed')
        need(scope['from_task']=='reference_v15_original4_001_case_01_reference' and scope['next_task']=='reference_v15_original4_001_case_02_reference','original order changed')
        c.write_new(PREP/'scope.json',c.raw(scope))
        write(PREP,'receipt.json',{'status':'concrete_scope_prepared_read_only','scope_sha256':c.sha(c.read(PREP/'scope.json')),
            'manager_sha256':manager_sha,'actual_database_writes':0,'new_model_calls':0,'new_workbench_actions':0,
            'dispatch_authorized':False,'management_applied':False,'formal_target_success':False,
            'artifacts':{p.name:c.sha(p.read_bytes()) for p in PREP.iterdir() if p.is_file()}})
    else:
        need(cfg['operation']=='apply_one_saved_budget_management','wrong apply configuration')
        scope_raw=c.read(PREP/'scope.json');need(c.sha(scope_raw)==cfg['scope_sha256'],'concrete scope changed')
        admission_raw=c.read(cfg['admission_path'],65536);need(c.sha(admission_raw)==cfg['admission_sha256'],'concrete local admission changed')
        scope=c.decode(scope_raw)
        record=manager.execute(scope,admission=admission_raw,expected_admission_sha256=cfg['admission_sha256'])
        write(ACTUAL,'committed_record.json',record)
        need(record==manager.read_committed(ROOT,scope_id=scope['scope_id']),'committed management readback differs')
        scope_path=P/'experiment_traces/meta_casebank_supplemental_preparations/reference_v15_original4_001_v18s1_scope_001/scope.json'
        admission_path=P/'docs/research/meta_framework_v18_handoff/supplemental_dispatch_admission_001.json'
        phase=s.SupplementalStage(ROOT,scope=c.decode(scope_path.read_bytes()),admission=admission_path.read_bytes(),
            expected_admission_sha256='e57a5ed310ea7d8abd3fa43177fe0e07e1797d7665f00387c52cb8b5f230a163')
        inspection=phase.inspect_saved();need(inspection['status']=='saved_evidence_verified','post-management saved evidence unresolved')
        summary=phase.summary()
        need(summary['status']=='paused' and summary['current_task']==scope['next_task'],'management must not resume')
        need(summary['total_calls']==15 and summary['accounting']=={'reported_tokens':491954,'reserved_tokens':80000,'unsettled_calls':1,'exposure_tokens':571954},'old accounting changed')
        need(summary['tasks'][scope['from_task']]['status']=='budget_stopped' and all(t['finals']==0 for t in summary['tasks'].values()),'wrong administrative terminal/final')
        write(ACTUAL,'summary_after.json',summary);write(ACTUAL,'inspection_after.json',inspection)
        display=next(row for row in diagnostic_monitor.snapshot(P)['campaigns'] if row['id']=='casebank:'+ROOT.name)
        write(ACTUAL,'monitor_after.json',display)
        need(display['cases'][0]['status']=='budget_stopped' and display['status']=='paused','monitor does not retain budget terminal')
        write(ACTUAL,'receipt.json',{'status':'first_task_budget_terminal_recorded_stage_still_paused',
            'at':datetime.now(timezone.utc).isoformat(),'scope_sha256':cfg['scope_sha256'],'admission_sha256':cfg['admission_sha256'],
            'committed_record_sha256':c.sha(c.read(ACTUAL/'committed_record.json')),'manager_sha256':manager_sha,
            'new_model_calls':0,'new_workbench_actions':0,'new_final_or_abstain':0,'resume_performed':False,
            'first_task_old_review_unchanged':True,'formal_target_success':False,
            'artifacts':{p.name:c.sha(p.read_bytes()) for p in ACTUAL.iterdir() if p.is_file()}})
except Exception as exc:
    original_traceback=traceback.format_exc()
    recovery_observation={'attempted':False}
    if command=='apply':
        try:
            saved=manager.read_committed(ROOT,scope_id='reference_v15_original4_001_case01_budget_001')
            write(directory,'failure_committed_readback.json',saved)
            recovery_observation={'attempted':True,'committed_record_present':saved is not None,
                'saved_readback_sha256':c.sha(c.read(directory/'failure_committed_readback.json'))}
        except Exception as read_error:
            recovery_observation={'attempted':True,'read_error':str(read_error),'commit_status':'unknown'}
    write(directory,'failure.json',{'error':str(exc),'traceback':original_traceback,'read_only_recovery_observation':recovery_observation,
        'recovery':'Do not blindly rerun. An apply may have committed before external verification failed; read committed record and original phase first.'})
    raise
print(json.dumps({'command':command,'receipt_sha256':c.sha(c.read(directory/'receipt.json'))}))
