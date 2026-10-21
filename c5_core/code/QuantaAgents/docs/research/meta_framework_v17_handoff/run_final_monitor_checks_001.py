from pathlib import Path
import hashlib,json,subprocess,os,uuid,xml.etree.ElementTree as ET
from datetime import datetime,timezone
p=Path.cwd(); stage=p/'experiment_traces/meta_ashare_revision17'; out=stage/'continuation_joint_validation/004_final_monitor'; out.mkdir(parents=True,exist_ok=False)
tests=['test_meta_continuation_monitor_v17.py','test_meta_continuation_monitor_integration_v17.py','test_meta_diagnostic_monitor_v10.py','test_meta_diagnostic_monitor_v11.py','test_meta_casebank_monitor_v15.py','test_meta_transport_monitor_v16.py']
tracked=sorted(x for x in (stage/'src').rglob('*') if x.is_file() and '__pycache__' not in x.parts)
tracked += [stage/'tests'/x for x in tests] + [stage/'tests/test_meta_casebank_continuation_v17.py', stage/'scripts/run_casebank_continuation.py']
def dig(x):return hashlib.sha256(x.read_bytes()).hexdigest()
def pins():return {str(x.relative_to(stage)):dig(x) for x in tracked}
before=pins(); tmp=Path(os.environ['TEMP'])/('qa17_final_'+uuid.uuid4().hex[:6]);env=os.environ.copy();env['PYTHONPATH']=str(stage/'src');env['PYTHONIOENCODING']='utf-8';env.pop('PYTHONUTF8',None)
cmd=[str(p/'.venv/Scripts/python.exe'),'-m','pytest',*[str(stage/'tests'/x) for x in tests],'-q','--junitxml='+str(out/'junit.xml'),'--basetemp='+str(tmp)]
(out/'intent.json').write_text(json.dumps({'started_at':datetime.now(timezone.utc).isoformat(),'command':cmd,'source_before':before,'reason':'Actual GUI duration failure and earlier 52 monitor source backup absent; affected current-source monitor only, no core29 repeat','actual_scope_changed':False},ensure_ascii=True,indent=2),encoding='utf-8')
with (out/'stdout.log').open('wb') as a,(out/'stderr.log').open('wb') as b:rc=subprocess.run(cmd,cwd=p,env=env,stdout=a,stderr=b).returncode
after=pins(); suites=[x.attrib for x in ET.parse(out/'junit.xml').getroot().iter('testsuite')]
r={'exit_code':rc,'counts':suites,'source_after':after,'inputs_unchanged':before==after,'artifacts_sha256':{x.name:dig(x) for x in out.iterdir() if x.is_file()},'new_actual_gateway_calls':0,'actual_scope_changed':False}
(out/'receipt.json').write_text(json.dumps(r,ensure_ascii=True,indent=2),encoding='utf-8');print(json.dumps({'out':str(out),'receipt_sha256':dig(out/'receipt.json'),'exit_code':rc,'counts':suites,'inputs_unchanged':before==after}));print((out/'stdout.log').read_text(encoding='utf-8')[-8500:])