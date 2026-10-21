"""Capture the actual read-only service after root's real browser inspection."""
from pathlib import Path
import json,hashlib,sys,urllib.request,urllib.parse
from datetime import datetime,timezone
P=Path.cwd();V=P/'experiment_traces/meta_ashare_revision18';sys.path.insert(0,str(V/'src'))
from quanta_agents.meta import diagnostic_monitor as m
out=P/'experiment_traces/meta_diagnostic_monitor_v18/before_registration_001';out.mkdir(exist_ok=False)
def sha(b):return hashlib.sha256(b).hexdigest()
def get(route,name):
 data=urllib.request.urlopen('http://192.0.2.10:8776'+route,timeout=20).read()
 with (out/name).open('xb') as f:f.write(data)
 return data
body=get('/','page.html');status=json.loads(get('/api/status','status.json'))
campaign='casebank:reference_v15_original4_001'
calls=json.loads(get('/api/calls?'+urllib.parse.urlencode({'campaign':campaign,'offset':0,'limit':20}),'calls.json'))
if body.replace(b'\r\n',b'\n')!=m.PAGE.encode('utf-8').replace(b'\r\n',b'\n'):raise ValueError('served page differs from accepted source')
shown=next(x for x in status['campaigns'] if x['id']==campaign)
if not(shown['status']=='paused' and shown['calls_reserved']==2 and shown['usage']['reported_tokens']==11235 and shown['usage']['reserved_tokens']==80000 and shown['supplemental_phase'] is None):raise ValueError('pre-registration campaign state differs')
if calls['total']!=2:raise ValueError('duplicate/missing call')
first,second=calls['rows']
if not(first['duration_seconds'] is None and first['accounting']['reserved_tokens']==80000 and second['duration_seconds']==12.921999999998661 and second['accounting']['reported_tokens']==11235 and second['public_action_applied']):raise ValueError('public runtime/accounting differs')
if not status['controller_self_check']['not_model_hidden_reasoning']:raise ValueError('self-check provenance')
receipt={'status':'live_v18_before_registration_verified','at':datetime.now(timezone.utc).isoformat(),'url':'http://192.0.2.10:8776/','monitor_source_sha256':sha(Path(m.__file__).read_bytes()),
 'root_browser_verification':{'browser_id':'1','tab_id':'3','reload_after_server_transition':True,'new_self_check_heading_and_four_fields_visible':True,'selected_original_casebank_and_loaded_two_calls':True,'failed_original_unknown_duration_and_80000_reserve_visible':True,'saved_correction_duration_and_11235_known_visible':True,'saved_action_applied_visible':True,'formal_target_not_met_visible':True,'observation':'Actual CUA DOM snapshots in this task at 04:18-04:19 UTC; no model/tool action was triggered by the browser.'},
 'supplemental_banner_verified':False,'reason':'Actual phase not yet registered; its API/JS path was separately covered by four bounded integration checks.',
 'raw_page_sha256':sha(body),'page_comparison':'CRLF-normalized bytes equal accepted final PAGE; raw hash retained separately','monitor_parent_pid':80840,'listener_pid':99764,
 'actual_model_calls':0,'actual_workbench_actions':0,'formal_target_success':False,'artifacts':{p.name:sha(p.read_bytes()) for p in out.iterdir() if p.is_file()}}
with (out/'receipt.json').open('x',encoding='utf-8') as f:json.dump(receipt,f,ensure_ascii=False,indent=2)
print(json.dumps({'verified':True,'receipt_sha256':sha((out/'receipt.json').read_bytes())}))
