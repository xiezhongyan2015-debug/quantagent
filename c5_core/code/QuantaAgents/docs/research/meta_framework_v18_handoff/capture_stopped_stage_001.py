"""Append a read-only live observation and current handoff; never run research."""
from pathlib import Path
from datetime import datetime, timezone
import hashlib, json, subprocess, sys, urllib.request, urllib.parse

P = Path.cwd()
OUT = P / 'experiment_traces/meta_diagnostic_monitor_v18/after_budget_stop_002'
OUT.mkdir(exist_ok=False)
def sha(data): return hashlib.sha256(data).hexdigest()
def save(name, value):
    data = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False).encode('utf-8')
    with (OUT/name).open('xb') as f: f.write(data)
    return sha(data)
def get(route, name):
    data = urllib.request.urlopen('http://192.0.2.10:8776'+route, timeout=20).read()
    with (OUT/name).open('xb') as f: f.write(data)
    return data

page = get('/', 'page.html')
status = json.loads(get('/api/status', 'status.json'))
calls = json.loads(get('/api/calls?' + urllib.parse.urlencode({'campaign':'casebank:reference_v15_original4_001','offset':0,'limit':20}), 'calls.json'))
case = next(x for x in status['campaigns'] if x['id']=='casebank:reference_v15_original4_001')
if not (case['status']=='paused' and case['calls_reserved']==15 and case['completed_calls']==14
        and case['usage']=={'reported_tokens':491954,'reserved_tokens':80000,'unsettled_calls':1,'exposure_tokens':571954}
        and calls['total']==15 and len(calls['rows'])==15):
    raise ValueError('Observed stop/accounting no longer matches frozen review')
phase = case['supplemental_phase']
metadata = P/'experiment_traces/meta_casebank_content_reviews/reference_v15_original4_001_case01_supplement1_001/stopped_case01_metadata.json'
saved_metadata = json.loads(metadata.read_bytes())
pause_reason = saved_metadata['stage_pause_reason']
if pause_reason != 'admission stopped: original nominal exposure cap exhausted':
    raise ValueError('different stop reason')
review = P/'docs/research/meta_framework_v18_handoff/actual_case01_final_review_001.md'
if sha(review.read_bytes()) != '624aab03b83a3d10be6ba9f83bf2a351e520c656be73308ecc036c1608eca7fe':
    raise ValueError('independent review changed')
process = subprocess.run(['powershell','-NoProfile','-Command',
    "@(Get-CimInstance Win32_Process -Filter 'ProcessId=59904 OR ProcessId=91624 OR ProcessId=99764' | Select-Object ProcessId,ParentProcessId,CreationDate,CommandLine) | ConvertTo-Json -Depth 3 -Compress"],
    capture_output=True, text=True, check=True)
processes = json.loads(process.stdout)
if isinstance(processes, dict): processes=[processes]
if {r['ProcessId'] for r in processes}!={99764}:
    raise ValueError('old research process still present or monitor changed')
save('process_observation.json', processes)
sys.path.insert(0,str(P/'experiment_traces/meta_ashare_revision18/src'))
from quanta_agents.meta import diagnostic_monitor as monitor
if page.replace(b'\r\n', b'\n') != monitor.PAGE.encode().replace(b'\r\n', b'\n'):
    raise ValueError('live page differs from accepted source')
receipt = {
    'status':'actual_supplemental_stage_budget_pause_verified', 'at':datetime.now(timezone.utc).isoformat(),
    'campaign':case['id'],'usage':case['usage'],'calls':15,'completed_calls':14,
    'case01_queries':14,'case01_candidates':12,'case01_final_submissions':0,
    'other_three_cases':'not_started; all retained in original descriptive denominator',
    'pause_reason':pause_reason,'pause_reason_source':'independently captured saved ledger metadata; omitted by public monitor projection',
    'pause_reason_source_sha256':sha(metadata.read_bytes()),'original_unknown_debt_preserved':80000,
    'formal_strategy_denominator_increment':0,'formal_target_success':False,
    'root_browser_witness':{
        'browser_id':'1','tab_id':'3','actual_actions':'Refreshed status then loaded call list; actual CUA DOM inspected in this root task.',
        'supplemental_paused_original_expired_incomplete_banner_visible':True,
        'same_fixed_window_original_and_new_permission_visible':True,
        'call_list_display':'显示 15 / 15 条调用；原失败保留',
        'known_defects':'Per-case governance ready displays 待执行; the exact budget pause reason is omitted by public API and banner. Checkpoint 028 was historical and stale at this capture.'},
    'root_process_observation':'PIDs59904 and91624 absent; monitor99764 exact command retained. Does not by itself prove every possible process or registry lease absence.',
    'independent_review_sha256':sha(review.read_bytes()),
    'independent_capture_sha256':'c152b7cbd7f0181ec1326c75cb5e9a089345fd504dc4907fd8c49b531bceab8f',
    'model_calls_started':0,'workbench_actions_started':0,'actual_database_writes':0,
    'exposure_note':'Root inadvertently received a large stdout tail containing public intermediate tool observations while checking stopped worker. This is development exposure only; not fed to learner, not used to replace or score final. Independent reviewer inspected metadata/hashes only.',
    'artifacts':{p.name:sha(p.read_bytes()) for p in OUT.iterdir() if p.is_file()}}
receipt_sha=save('receipt.json',receipt)
cp_dir=P/'docs/research/meta_framework_v8_handoff'
previous=cp_dir/'checkpoint_20260907_028.json'
cp=json.loads(previous.read_bytes())
cp.update(checkpoint_number=29,recorded_at=receipt['at'],previous_checkpoint_sha256=sha(previous.read_bytes()),
    phase='v18_supplemental_paused_case01_nominal_budget_exhausted_no_final',
    stage_state='Unique worker exited after known per-task admission stop. Four original cases preserved: case01 unfinished, cases02-04 not started. No automatic restart or top-up.',
    paid_dispatch_permitted=False,paid_dispatch_scope=None,
    current_casebank_result='15 transports,14 complete;491954 known+80000 original unknown. Case01:14queries/12candidates/0final; whole stage paused. Other three untouched.',
    current_casebank_worker_status='Original worker PIDs59904/91624 absent at capture; no restart. Full lease/process audit delegated.',
    next_paid_scope_under_review='None admitted. Read-only audit of separately frozen known-budget terminal management permitted by original scope; no new window or budget.',
    self_check={
      'assessment':'13次补充模型调用均完整结算并应用；预算门按规则实际阻止下一笔。首题14查询、12候选、无最终提交，独立评审记未完成。研究worker已退出，原失败和80k未知预留保留。',
      'defects_uncertainty':'无最终产物可评；单题预算耗尽暂停全阶段，后三题尚未开始。GUI首题治理ready仍显示待执行且未直接展示暂停原因。真实执行和正式策略目标仍未达标。',
      'next_step':'按原范围审计已知单题预算停止的独立管理步骤与研究收尾机制。先离线实现、核验和独立审计；未有新准入前保持暂停，不给首题追加调用或代写final。',
      'falsifiers':'任何新unknown、未应用回答、工具pending、原记录漂移、债务释放、重置预算/时间或首题重复调用均阻止恢复。未开始题只能在原规则许可、独立管理核验和原补充窗口内推进。'})
cp['v18_budget_stop']={'live_receipt':str((OUT/'receipt.json').relative_to(P)).replace('\\','/'),
    'live_receipt_sha256':receipt_sha,'independent_review':str(review.relative_to(P)).replace('\\','/'),
    'independent_review_sha256':sha(review.read_bytes()),'case01_not_a_legal_abstention':True,'original_case_order_preserved':True}
target=cp_dir/'checkpoint_20260907_029.json'
with target.open('x',encoding='utf-8') as f:json.dump(cp,f,ensure_ascii=False,indent=2,allow_nan=False)
print(json.dumps({'receipt_sha256':receipt_sha,'checkpoint_sha256':sha(target.read_bytes())}))
