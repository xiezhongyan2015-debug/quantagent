"""One separately gated restart after first-task management, with observed exit code.

This wrapper resumes the existing phase and runs its frozen CLI. It cannot add
a window, change a budget, retry case01, or create another campaign.
"""
from pathlib import Path
from datetime import datetime, timezone
import hashlib, json, os, subprocess, sys, time, traceback
if sys.flags.optimize: raise RuntimeError('No optimized administrative execution')
P=Path.cwd(); V=P/'experiment_traces/meta_ashare_revision18';sys.path.insert(0,str(V/'src'))
from quanta_agents.meta import casebank_continuation as core, casebank_supplemental as phase
ROOT=P/'experiment_traces/meta_casebank_references/campaigns/reference_v15_original4_001'
OUT=ROOT/'v18_worker_002'
S=P/'experiment_traces/meta_casebank_supplemental_preparations/reference_v15_original4_001_v18s1_scope_001/scope.json'
A=P/'docs/research/meta_framework_v18_handoff/supplemental_dispatch_admission_001.json'
R=ROOT/'v18_case01_budget_management_001/receipt.json'
def sha(path):
    digest=hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''):digest.update(block)
    return digest.hexdigest()
def need(ok,why):
    if not ok:raise RuntimeError(why)
def save(name,value):
    with (OUT/name).open('x',encoding='utf-8') as f:json.dump(value,f,ensure_ascii=False,indent=2,allow_nan=False)
need(len(sys.argv)==3,'exact root gate path and SHA256 required')
gate_path=Path(sys.argv[1]);need(sha(gate_path)==sys.argv[2],'root gate changed')
gate=json.loads(gate_path.read_bytes())
need(gate['kind']=='same_v18s1_remaining_original_cases_dispatch_gate' and gate['authorized'] is True,'specific continuation gate required')
need(gate['wrapper_sha256']==sha(__file__),'reviewed worker wrapper changed')
need(gate['phase_id']=='v18s1' and gate['case01_further_calls_authorized'] is False
    and gate['third_window_authorized'] is False and gate['budget_increase_authorized'] is False,'scope must remain original')
need(sha(S)=='8d60a4fdef3d94c4f952edb721f9d620c62a17f51c760e2b67a4e9ed9beef66b'
    and sha(A)=='e57a5ed310ea7d8abd3fa43177fe0e07e1797d7665f00387c52cb8b5f230a163','original phase permission changed')
need(sha(R)==gate['management_outcome_receipt_sha256'],'management outcome changed')
for pin in gate['provenance_pins']:need(sha(pin['path'])==pin['sha256'],'independent evidence changed')
python=P/'.venv/Scripts/python.exe';cli=V/'scripts/run_casebank_supplemental.py';native=Path(gate['codex_executable'])
need(sha(python)==gate['python_sha256'] and sha(native)==gate['codex_executable_sha256']
    and sha(cli)=='429e0d7d3b191028f32545aaeabc6007e6972583d3a3049b13fcf949d5c44da9','runtime/entrypoint changed')
scope=json.loads(S.read_bytes())
need(scope['start_epoch']<=time.time()<scope['end_epoch'],'fixed supplemental window closed')
controller=phase.SupplementalStage(ROOT,scope=scope,admission=A.read_bytes(),expected_admission_sha256=sha(A))
summary=controller.summary()
first='reference_v15_original4_001_case_01_reference';second='reference_v15_original4_001_case_02_reference'
need(summary['status']=='paused' and summary['current_task']==second
    and summary['tasks'][first]['status']=='budget_stopped','first case not administratively closed or stage already resumed')
need(summary['supplemental_phase']['current_state_hash']==gate['expected_management_post_state_hash'],'fresh post-management state required')
need(summary['total_calls']==15 and summary['accounting']=={'reported_tokens':491954,'reserved_tokens':80000,'unsettled_calls':1,'exposure_tokens':571954},'original charges changed')
need(controller.inspect_saved()['status']=='saved_evidence_verified','unresolved saved evidence prevents restart')
OUT.mkdir(exist_ok=False)
save('intent.json',{'at':datetime.now(timezone.utc).isoformat(),'gate_sha256':sys.argv[2],'wrapper_sha256':sha(__file__),
    'supervisor_pid':os.getpid(),'operation':'Resume existing v18s1 after reviewed case01 budget terminal, then one frozen run command',
    'fixed_end_epoch':scope['end_epoch'],'new_independent_start':False,'new_budget_or_window':False})
child=None
start_record_error=None
try:
    resumed=controller.resume()
    need(resumed['status']=='ready' and resumed['current_task']==second,'resume did not retain original successor')
    save('resumed.json',resumed)
    args=[str(python),str(cli),'run','--campaign-root',str(ROOT),'--scope',str(S),'--scope-sha256',sha(S),
          '--admission',str(A),'--admission-sha256',sha(A),'--executable',str(native)]
    env=dict(os.environ);env['PYTHONIOENCODING']='utf-8'
    with (OUT/'stdout.log').open('xb') as stdout, (OUT/'stderr.log').open('xb') as stderr:
        child=subprocess.Popen(args,cwd=P,env=env,stdout=stdout,stderr=stderr,
            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        try:
            save('start.json',{'at':datetime.now(timezone.utc).isoformat(),'supervisor_pid':os.getpid(),
                'worker_pid':child.pid,'command':args,'gate_sha256':sys.argv[2],'completion_verified':False})
        except Exception as start_error:
            # Keep supervising this same authorized child even if the external
            # start record could not be written. Do not launch a replacement.
            start_record_error={'error':str(start_error),'traceback':traceback.format_exc()}
        code=child.wait()
    save('process_exit.json',{'at':datetime.now(timezone.utc).isoformat(),'supervisor_pid':os.getpid(),
        'worker_pid':child.pid,'exit_observed':True,'return_code':code,'stdout_sha256':sha(OUT/'stdout.log'),
        'stderr_sha256':sha(OUT/'stderr.log'),'research_result_qualified':False,
        'meaning':'Observed CLI exit only; use saved ledger and independent final review for actual research outcome.'})
    if start_record_error is not None:
        save('start_record_failure.json',start_record_error)
        raise RuntimeError('Start record failed; the same child was nevertheless waited for and its actual exit was observed')
except Exception as exc:
    save('supervisor_failure.json',{'at':datetime.now(timezone.utc).isoformat(),'error':str(exc),'traceback':traceback.format_exc(),
        'worker_pid':None if child is None else child.pid,'worker_return_code_if_observed':None if child is None else child.poll(),
        'recovery':'Do not rerun or terminate a possibly live worker blindly. Inspect current process, lease, ledger, saved calls and actions first.'})
    raise
raise SystemExit(code)
