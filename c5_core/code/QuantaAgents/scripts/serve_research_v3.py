"""Local V4 controller status and V3 evidence viewer. No dispatch endpoint."""
import argparse
import hashlib
from contextlib import closing
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sqlite3
import time

import psutil

PROJECT_ROOT = Path(__file__).resolve().parents[1]

PAGE = r'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>QuantaAgents V4 研究监管</title>
<style>body{background:#0e1726;color:#edf3fa;font:16px/1.6 system-ui;margin:0}main{max-width:1160px;margin:auto;padding:32px}h1{font-size:30px;margin-bottom:0}p{color:#acbfd3}section{border:1px solid #344359;background:#152235;padding:20px;border-radius:12px;margin:18px 0}.grid{display:grid;grid-template-columns:repeat(4,1fr);gap:16px}.metric{font-size:26px;color:#9bdbbd}.label{font-size:13px;color:#adbdce}.pill{border-radius:20px;padding:4px 12px;background:#233d4b}details{margin:12px 0;border-top:1px solid #33445a;padding:10px 0}summary{cursor:pointer}pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:13px;background:#101c2c;padding:12px;max-height:500px;overflow:auto}button{background:#435369;color:white;border:0;border-radius:5px;padding:10px 15px;cursor:pointer}.warn{color:#f4c38d}small{color:#adbdce}@media(max-width:700px){.grid{grid-template-columns:1fr 1fr}main{padding:16px}}</style>
<main><h1>QuantaAgents <span class="metric">V4 研究监管</span></h1><p>工程进度、真实研究、正式验证分别记录 · 只读监管</p><div id="connection">正在连接监管服务…</div><div id="study"></div><div id="controller"></div><div id="cycle-results"></div><h2>V3 历史研究账</h2><div id="live"></div><section class="grid" id="metrics"></section><p class="warn">以下包含合成校准与真实数据开发，成交和成本仍为声明模拟。真实成本后冻结样本外净 Sharpe &gt; 1 尚未达标。旧 V2 未知费用预留 80,000，独立保留。</p><div id="tasks"></div><small>显示模型公开返回的摘要和研究动作。进程活性通过当前进程身份核验；保存状态本身不代表正在运行。每 10 秒刷新。</small></main>
<style>.table-wrap{overflow:auto}table{border-collapse:collapse;width:100%;font-size:14px}th,td{padding:9px 12px;border-bottom:1px solid #344359;text-align:right;white-space:nowrap}th:first-child,td:first-child{text-align:left}</style>
<style>.stages{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px}.stages section{margin:12px 0;border-top:3px solid #70b1db}.stages h2{font-size:20px;margin-top:0}.stages p{margin:10px 0}.stages ul{padding-left:20px;color:#cad7e4}.stages .metric{font-size:23px}.stamp{font-size:13px;color:#adbdce}.next{border-left:4px solid #e3bb81}.source{overflow-wrap:anywhere}.history-stage{font-size:13px}.history-stage td{white-space:normal}@media(max-width:850px){.stages{grid-template-columns:1fr}}</style>
<script>
const el=(tag,text)=>{const e=document.createElement(tag);if(text!==undefined)e.textContent=text;return e};
const localTime=value=>value?new Date(value).toLocaleString('zh-CN',{timeZone:'Asia/Hong_Kong',hour12:false})+'（香港时间）':'未提供';
function renderStudy(s){
 const target=document.getElementById('study');target.replaceChildren();if(!s)return;
 const box=el('section');box.append(el('h2','三架构局部开发比较 · '+s.study_id),el('p',s.state));
 if(s.error){const warning=el('p',s.error);warning.className='warn';box.append(warning)}
 if(s.totals)box.append(el('p',`完整槽位 ${s.slots.length} · 未启动 ${s.totals.not_started} · 已启动未终结 ${s.totals.started} · 终态 ${s.totals.terminal}`),el('p',`父任务与全部已登记扩展累计：${s.totals.call_count} 次模型调用 · 已知 ${s.totals.known_tokens.toLocaleString()} token · 未知用量预留 ${s.totals.unknown_reserve.toLocaleString()}`));
 for(const group of s.groups||[])box.append(el('p',`${group.label}：${group.terminal}/${group.slot_count} 个终态 · ${group.call_count} 次调用 · ${group.known_tokens.toLocaleString()} 已知 token · ${group.unknown_reserve.toLocaleString()} 未知预留`));
 const wrap=el('div');wrap.className='table-wrap';const table=el('table'),head=el('tr');for(const h of ['组别 / 重复','状态','模型调用（父 / 子）','已知 token','未知预留','交付 / 固定基线选择'])head.append(el('th',h));table.append(head);
 const body=el('tbody');for(const slot of s.slots||[]){const tr=el('tr');tr.dataset.trialId=slot.id;for(const value of [`${slot.label} / ${slot.repeat}`,slot.state,`${slot.call_count}（${slot.parent_call_count} / ${slot.child_call_count}）`,slot.known_tokens.toLocaleString(),slot.unknown_reserve.toLocaleString(),slot.result_summary||'尚无结果'])tr.append(el('td',value));body.append(tr);if(slot.errors?.length){const error=el('tr'),cell=el('td',slot.errors.join('；'));cell.colSpan=6;cell.className='warn';error.append(cell);body.append(error)}}table.append(body);wrap.append(table);box.append(wrap);
 const caution=el('p','同一已暴露案例的 3 组 × 3 次开发启动；固定组仅搜索预冻结的窄模板。费用按中央账归属原槽位，包含父任务和扩展；启动记录不证明进程存活。9 次开发不等于正式 54 次验证，正式成功分母贡献为 0。');caution.className='warn';box.append(caution,el('small','只读登记来源：'+s.registry_path));target.append(box)
}
function renderResearch(box,c){
 const r=c.research_status,cycle=c.cycle_status,prior=c.prior_v4_research_status;
 if(cycle){
  box.append(el('h3','本轮父任务与扩展任务累计'));
  if(cycle.error)box.append(el('p','本轮账本尚未就绪：'+cycle.error));
  else{
   box.append(el('p',`本轮已登记 ${cycle.call_count} 次 · 已返回 ${cycle.returned_calls} · 待返回 ${cycle.pending_calls}`),el('p',`本轮已知 token：${cycle.known_tokens.toLocaleString()} · 待核对预留：${cycle.reserved.toLocaleString()}`));
   for(const stage of cycle.stages)box.append(el('p',`${stage.role==='parent'?'父任务':'扩展任务'}：${stage.call_count} 次 / ${stage.known_tokens.toLocaleString()} token · ${stage.state}`));
   if(!cycle.aggregation_complete){const warning=el('p','本轮累计核验不完整：'+cycle.errors.map(x=>x.error).join('；'));warning.className='warn';box.append(warning)}
  }
  if(prior&&!prior.error)box.append(el('p',`前轮 V4 资料审阅（另列）：${prior.call_count} 次 / ${prior.known_tokens.toLocaleString()} token`));
  else if(prior?.error)box.append(el('p','前轮 V4 账本读取失败：'+prior.error));
 }
 if(c.new_v4_diagnostic_calls!==undefined)box.append(el('p',`前轮账户诊断（另列）：${c.new_v4_diagnostic_calls} 次 / ${(c.new_v4_diagnostic_known_tokens||0).toLocaleString()} token`));
 if(!r)return;
 if(r.error){box.append(el('p','当前 V4 研究账本读取失败：'+r.error));return}
 box.append(el('h3',cycle?'当前阶段：'+(r.role==='child'?'扩展任务':'父任务'):'当前 V4 研究范围'));
 box.append(el('p',`本阶段已登记 ${r.call_count} 次 · 已返回 ${r.returned_calls} · 待返回 ${r.pending_calls}`),el('p',`本阶段已知 token：${r.known_tokens.toLocaleString()} · 待核对预留：${r.reserved.toLocaleString()}`));
 const latest=r.latest_call;
 box.append(el('p','当前动作：'+(latest?(latest.action?(names[latest.action]||latest.action):'等待模型返回')+' · '+(labels[latest.status]||latest.status):'尚无调用记录')),el('p','任务终态：'+r.tasks.map(t=>`${t.id}：${labels[t.terminal]||t.terminal||(t.final_call?'终稿已保存':'未终结')}`).join('；')));
 if(r.latest_public_summary)box.append(el('p','最近公开摘要：'+r.latest_public_summary));
 if(r.report_summary)box.append(el('h3','当前阶段研究报告摘要'),el('p',r.report_summary));
 box.append(el('small','研究账本读取：'+localTime(r.observed_at)));
 const path=el('small','当前研究账本：'+r.ledger_path);path.className='source';box.append(path)
}
function renderController(c){const container=document.getElementById('controller');container.replaceChildren();if(!c||c.error){const box=el('section');box.append(el('h2','V4 阶段快照未载入'),el('p',c?.error||'未配置阶段状态文件；不能从 V3 历史账推断 V4 进度。'));container.append(box);return}const meta=el('p',`阶段快照：${localTime(c.observed_at)} · 快照年龄 ${c.snapshot_age_seconds===null?'未知':Math.floor(c.snapshot_age_seconds/60)+' 分钟'}`);meta.className='stamp';container.append(meta);const grid=el('div');grid.className='stages';for(const stage of c.stages||[]){const box=el('section');box.append(el('h2',stage.title));const state=el('div',stage.status);state.className='metric';box.append(state,el('p',stage.summary));if(stage.id==='real_research')renderResearch(box,c);const list=el('ul');for(const item of stage.details||[])list.append(el('li',item));box.append(list);const gaps=el('p','当前缺口：'+(stage.gap||'未报告'));gaps.className='warn';box.append(gaps);grid.append(box)}container.append(grid);const now=el('section');now.className='next';now.append(el('h2','当前推进与进程核验'),el('p',c.next_action||'未报告下一步'),el('p','V4 研究进程：'+(c.worker_observation.live?'正在运行（当前 PID、创建时间及命令已核验）':c.worker_observation.status==='not_configured'?'未提供研究进程身份，当前活性未核验':'保存的研究进程身份未匹配到存活进程')),el('p',c.authorization_note||''));const caution=el('p','网页在线表示监管服务可访问；阶段状态是带时间的保存快照，不构成研究 worker 正在运行或正式验证通过的证据。');caution.className='warn';now.append(caution);const source=el('small','状态来源：'+c.source_path);source.className='source';now.append(source);container.append(now)}
const evidence=document.createElement('div');document.getElementById('tasks').before(evidence);
let lastSnapshot;
function renderEvidence(reports,target=evidence){target.replaceChildren();for(const report of reports){const box=el('section');box.append(el('h2',report.title),el('p',report.summary));for(const fact of report.facts)box.append(el('p',fact));const wrap=el('div');wrap.className='table-wrap';const table=el('table'),head=el('thead'),headRow=el('tr');for(const column of report.columns)headRow.append(el('th',column));head.append(headRow);table.append(head);const body=el('tbody');for(const row of report.rows){const tr=el('tr');for(const value of row)tr.append(el('td',typeof value==='number'?value.toLocaleString():value));body.append(tr)}table.append(body);wrap.append(table);box.append(wrap);const limit=el('p',report.limitations);limit.className='warn';box.append(limit,el('p','下一步：'+report.next_step));target.append(box)}}
const names={inspect_inputs:'检查输入',diagnose_horizons:'期限、条件与匹配诊断',develop_strategy:'开发并执行策略',inspect_execution:'核对持仓与费用',read_evidence:'读取原始证据',submit_research_report:'提交最终研究报告',register_batch:'登记批量候选',execute_batch:'执行已登记批次',inspect_batch:'读取批次证据',diagnose_execution:'诊断账户与成本',register_experiment:'登记区分性修订',request_research_extension:'申请有界研究扩展'};
const labels={submitted:'已提交',applied:'已记录',failed:'失败',pending:'等待返回',close_only:'仅允许终稿',explore:'研究阶段',invalid_final:'终稿无效'};
async function update(){try{const r=await fetch('/api/status');if(!r.ok)throw new Error('HTTP '+r.status);const s=await r.json();document.getElementById('connection').textContent='监管服务在线 · 最近读取 '+localTime(s.observed_at)+' · 每 10 秒刷新';document.getElementById('live').textContent=s.live?'V3 账本关联进程正在运行（身份已核验）':'V3 账本关联研究进程当前未运行';renderStudy(s.controller_status?.study_status);renderController(s.controller_status);renderEvidence(s.controller_status?.cycle_results_summary?[s.controller_status.cycle_results_summary]:[],document.getElementById('cycle-results'));const fingerprint=JSON.stringify(s);if(fingerprint===lastSnapshot)return;lastSnapshot=fingerprint;renderEvidence(s.evidence_reports||[]);
const m=document.getElementById('metrics');m.replaceChildren();for(const [k,v] of [['V3 已知研究 token',s.known_tokens.toLocaleString()],['V3 待核对预留',s.reserved.toLocaleString()],['V3 历史实际调用',s.calls.length],['V3 已保存终稿',s.tasks.filter(t=>t.final_call).length]]){const d=el('div');d.append(el('div',k),el('div',String(v)));d.lastChild.className='metric';m.append(d)}
const container=document.getElementById('tasks');const opened=new Set([...container.querySelectorAll('details[open]')].map(x=>x.id));container.replaceChildren();container.append(el('p','原始校准和恢复范围分别显示；恢复终稿保留原始交付失败，不能提高原始交付率。模型请求锁定 Astra / xhigh；供应方模型身份尚未独立认证。'));for(const t of s.tasks){const box=el('section');box.append(el('h2',t.title),el('small',t.id),el('div',labels[t.terminal]||t.terminal||((s.live&&s.calls.some(c=>c.task_id===t.id&&c.status==='pending'))?'正在研究':t.paused?'已停止，保留未交付记录':labels[t.mode]||t.mode)));box.append(el('p',`冻结上限：每题 ${t.policy.task_calls} 次调用、${t.policy.task_tokens.toLocaleString()} 名义 token；截止 ${new Date(t.deadline_epoch*1000).toLocaleString('zh-CN',{timeZone:'Asia/Hong_Kong'})}（香港时间）`));if(t.pause_reason)box.append(el('p','停止原因：'+t.pause_reason));for(const c of s.calls.filter(c=>c.task_id===t.id)){const detail=el('details');detail.id=c.id;detail.open=opened.has(c.id);detail.append(el('summary',`${c.ordinal}. ${c.response?(names[c.response.action]||c.response.action):'等待模型返回'} · ${labels[c.status]||c.status} · ${c.known_tokens===null?'用量待核':c.known_tokens+' token'}`));if(c.response){detail.append(el('p',c.response.public_summary),el('pre',JSON.stringify(c.response.self_review,null,2)));let request;try{request=JSON.parse(c.response.arguments_json)}catch{request=c.response.arguments_json}detail.append(el('h3','公开工具请求'),el('pre',typeof request==='string'?request:JSON.stringify(request,null,2)))}if(c.result){if(c.result.model_report){const report=c.result.model_report;detail.append(el('p',report.conclusion));for(const [title,key] of [['局限','limitations'],['证伪条件','falsifiers']]){detail.append(el('h3',title));for(const x of report[key])detail.append(el('p',x))}detail.append(el('h3','下一步'),el('p',report.next_step))}else{detail.append(el('pre',JSON.stringify(c.result,null,2)))}}box.append(detail)}container.append(box)}}catch(e){document.getElementById('connection').textContent='监管服务读取失败：'+e.message+'；以下内容为上次快照'}}
update();setInterval(update,10000);
</script></html>'''


def snapshot(root):
    db = sqlite3.connect((root / "ledger.sqlite3").as_uri() + "?mode=ro", uri=True, timeout=5)
    db.row_factory = sqlite3.Row
    try:
        tasks = [dict(x) for x in db.execute("SELECT id,mode,terminal,final_call FROM tasks ORDER BY id")]
        stage = dict(db.execute("SELECT paused,reason,plan FROM stage").fetchone())
        plan = json.loads(stage["plan"])
        calls = [dict(x) for x in db.execute("SELECT id,task_id,ordinal,status,known_tokens,reserve,receipt,result,created FROM calls ORDER BY created,id")]
    finally:
        db.close()
    for c in calls:
        c["task_id"] = root.name + "/" + c["task_id"]
        c["response"] = json.loads(c.pop("receipt"))["response"] if c["receipt"] else None
        c["result"] = json.loads(c["result"]) if c["result"] else None
    for task in tasks:
        if plan['provenance'].get('generator_proposal_binding'):
            task['title'] = '模型参数提案 · 生成数据'
        elif "batch_recovery" in root.name:
            task['title'] = '批量工具只读恢复 · 原候选证据与终稿'
        elif "batch_calibration" in root.name:
            task['title'] = '批量工具校准 · 已暴露的生成数据'
        elif "year_research" in root.name:
            task["title"] = "全年真实数据开发 · 下跌后企稳"
        else:
            task["title"] = ("真实数据开发 · " if "real_research" in root.name else "终稿恢复 · " if "recovery" in root.name else "原始校准 · ") + ("成本负对照" if "cost" in task["id"] else "价格与活跃度")
        task["id"] = root.name + "/" + task["id"]
        task["paused"] = bool(stage["paused"])
        task["pause_reason"] = stage["reason"]
        task["policy"] = plan["policy"]
        task["deadline_epoch"] = plan["deadline_epoch"]
        task["scope_exposure"] = plan["provenance"].get("exposure", "")
    live = False
    try:
        ident = json.loads((root / "worker_identity.json").read_text(encoding="utf-8"))
        p = psutil.Process(ident["pid"])
        live = p.is_running() and p.create_time() == ident["create_time"] and p.cmdline() == ident["cmdline"]
    except (OSError, ValueError, psutil.Error):
        pass
    return {"live": live, "observed_at": time.time(), "tasks": tasks, "calls": calls,
            "known_tokens": sum(c["known_tokens"] or 0 for c in calls),
            "reserved": sum(c["reserve"] for c in calls if c["known_tokens"] is None),
            "old_v2_unknown_reserve": 80000, "formal_target_success": False}


def research_snapshot(root):
    """Read only the explicitly configured V4 ledger, separately from V3 totals."""
    ledger = root / "ledger.sqlite3"
    try:
        with closing(sqlite3.connect(ledger.as_uri() + "?mode=ro", uri=True, timeout=5)) as db:
            db.row_factory = sqlite3.Row
            db.execute("BEGIN")
            stage = dict(db.execute("SELECT paused,reason FROM stage").fetchone())
            tasks = [dict(row) for row in db.execute("SELECT id,mode,terminal,final_call FROM tasks ORDER BY id")]
            calls = [dict(row) for row in db.execute(
                "SELECT id,task_id,ordinal,status,known_tokens,reserve,receipt,result,created FROM calls ORDER BY created,id")]
        latest_summary = None
        report_summary = None
        for call in calls:
            receipt = json.loads(call.pop("receipt")) if call["receipt"] else {}
            response = receipt.get("response") or {}
            result = json.loads(call.pop("result")) if call["result"] else {}
            call["action"] = response.get("action")
            call["has_response"] = bool(response)
            if response.get("public_summary"):
                latest_summary = response["public_summary"]
            report = result.get("model_report") or {}
            if report.get("conclusion"):
                report_summary = report["conclusion"]
        pending = sum(call["status"] == "pending" for call in calls)
        all_terminal = bool(tasks) and all(task["terminal"] for task in tasks)
        if all_terminal:
            state = ("研究终稿已保存" if all(task["terminal"] == "submitted" and task["final_call"] for task in tasks)
                     else "终稿交付失败" if any(task["terminal"] == "invalid_final" for task in tasks)
                     else "研究已结束，未提交有效终稿")
        else:
            state = ("研究已暂停" if stage["paused"] else "模型调用等待返回" if pending
                     else "研究任务未终结" if calls else "等待研究调用")
        return {"observed_at": datetime.now(timezone.utc).isoformat(), "ledger_path": str(ledger),
                "call_count": len(calls), "returned_calls": sum(call["has_response"] for call in calls),
                "pending_calls": pending, "known_tokens": sum(call["known_tokens"] or 0 for call in calls),
                "reserved": sum(call["reserve"] for call in calls if call["known_tokens"] is None),
                "tasks": tasks, "latest_call": calls[-1] if calls else None,
                "latest_public_summary": latest_summary, "report_summary": report_summary,
                "paused": bool(stage["paused"]), "pause_reason": stage["reason"], "state": state}
    except (OSError, ValueError, TypeError, KeyError, sqlite3.Error) as exc:
        return {"error": str(exc), "ledger_path": str(ledger)}


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def _read_json(path, limit=16 * 1024 * 1024):
    if path.stat().st_size > limit:
        raise ValueError("Viewer evidence exceeds its read bound: " + str(path))
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _saved_plan(root):
    """Validate stored plan identity without entering a writer transaction."""
    with closing(sqlite3.connect((root / "ledger.sqlite3").as_uri() + "?mode=ro",
                                 uri=True, timeout=5)) as db:
        row = db.execute("SELECT plan,plan_hash FROM stage").fetchone()
    plan = json.loads(row[0])
    if _digest(plan) != row[1] or _read_json(root / "plan.json") != plan:
        raise ValueError("Viewer frozen plan identity mismatch")
    return plan


def _bound_child(parent, parent_plan, folder):
    """A directory name alone cannot add a child ledger to cycle accounting."""
    if not folder.resolve().is_relative_to(parent):
        raise ValueError("Extension folder resolves outside the cycle")
    decision = _read_json(folder / "decision.json", 65536)
    if decision.get("status") != "ready":
        return None
    intent = _read_json(folder / "intent.json")
    child = (folder / "stage").resolve()
    request_id, task_id = folder.name, intent["parent_task"]
    if not (decision.get("kind") == "controller_extension_decision_v1"
            and intent.get("kind") == "controller_extension_admission_v1"
            and decision["intent_hash"] == _digest(intent)
            and intent["decision"]["status"] == "ready"
            and Path(intent["parent_root"]).resolve() == parent
            and intent["parent_plan_hash"] == _digest(parent_plan)
            and intent["parent_case_hash"] == parent_plan["tasks"][task_id]["case_hash"]
            and intent["request_id"] == request_id
            and Path(decision["child_root"]).resolve() == child
            and decision["child_budget_registered"] is True):
        raise ValueError("Child controller decision is not bound to this cycle")
    with closing(sqlite3.connect((parent / "ledger.sqlite3").as_uri() + "?mode=ro",
                                 uri=True, timeout=5)) as db:
        row = db.execute("SELECT task_id,status,receipt,result FROM calls WHERE id=?",
                         (request_id,)).fetchone()
    if row is None or row[0] != task_id or row[1] != "applied":
        raise ValueError("Child admission has no applied parent request")
    receipt, result = json.loads(row[2]), json.loads(row[3])
    request = _read_json(parent / "tools" / task_id / request_id / "artifact.json")
    if not (receipt["response"]["action"] == "request_research_extension"
            and request["kind"] == "research_extension_request_v1"
            and request["current_case_hash"] == intent["parent_case_hash"]
            and request["request"] == json.loads(receipt["response"]["arguments_json"])
            and _digest(request) == intent["request_hash"] == result["artifact_hash"]):
        raise ValueError("Child admission parent request evidence mismatch")
    plan = _saved_plan(child)
    lineage = plan["provenance"]["research_extension"]
    if not (decision["child_plan_hash"] == _digest(plan)
            and lineage["intent_hash"] == _digest(intent)
            and Path(lineage["intent_path"]).resolve() == (folder / "intent.json").resolve()
            and Path(lineage["decision_path"]).resolve() == (folder / "decision.json").resolve()
            and plan["policy"] == intent["policy"]
            and plan["deadline_epoch"] == intent["deadline_epoch"]
            and set(plan["tasks"]) == {"extension"}
            and plan["tasks"]["extension"]["case_hash"] == intent["child_case_hash"]):
        raise ValueError("Child stage plan does not match its admission")
    return child


def cycle_snapshot(root, active_root):
    """Read this parent and at most one bound child; never traverse descendants."""
    root, active_root = root.resolve(), active_root.resolve()
    stages, errors, roots = [], [], [root]
    try:
        plan = _saved_plan(root)
        candidates = sorted((root / "controller_extensions").glob("*/decision.json"))
        if len(candidates) > 32:
            raise ValueError("Cycle extension decision count exceeds viewer bound")
        children = []
        for path in candidates:
            try:
                child = _bound_child(root, plan, path.parent)
                if child is not None:
                    children.append(child)
            except (OSError, ValueError, TypeError, KeyError, sqlite3.Error) as exc:
                errors.append({"path": str(path), "error": str(exc)})
        if len(children) > 1:
            errors.append({"error": "More than one bound child; cycle scope is ambiguous"})
        else:
            roots.extend(children)
        for stage_root in roots:
            item = research_snapshot(stage_root)
            item.update(root=str(stage_root), role="parent" if stage_root == root else "child")
            if item.get("error"):
                errors.append({"path": str(stage_root), "error": item["error"]})
            else:
                stages.append(item)
        current = next((item for item in stages if Path(item["root"]) == active_root), None)
        if current is None:
            errors.append({"error": "Configured current stage is not a verified cycle member"})
        return {"root": str(root), "current_stage_root": str(active_root),
            "current_stage": current, "stages": stages, "errors": errors,
            "aggregation_complete": not errors,
            **{key: sum(item[key] for item in stages) for key in (
                "call_count", "returned_calls", "pending_calls", "known_tokens", "reserved")},
            "semantics": "Disjoint parent/child ledger totals for this cycle only. Earlier V4, account diagnostics and V3 are separate. No dispatch or scientific acceptance is implied."}
    except (OSError, ValueError, TypeError, KeyError, sqlite3.Error) as exc:
        return {"error": str(exc), "root": str(root), "aggregation_complete": False}


STUDY_ARCHITECTURES = {
    "v4_evidence_workflow": ("v", "A · V4 证据工作流"),
    "strong_single": ("s", "B · 强单模型"),
    "fixed_template_search": ("f", "C · 固定模板搜索"),
}


def _study_slot(spec, registered=True):
    architecture = spec.get("architecture_contract", {}).get("architecture", "unknown")
    return {"id": spec["id"], "architecture": architecture,
        "label": STUDY_ARCHITECTURES.get(architecture, ("?", architecture))[1],
        "repeat": spec["repeat"], "registered": registered,
        "state": "未启动" if registered else "未登记", "status": "not_started",
        "call_count": 0, "parent_call_count": 0, "child_call_count": 0,
        "known_tokens": 0, "unknown_reserve": 0, "stages": [], "errors": [],
        "result_summary": None, "formal_target_success": False}


def _study_totals(slots):
    return {**{key: sum(slot[key] for slot in slots) for key in (
        "call_count", "parent_call_count", "child_call_count", "known_tokens", "unknown_reserve")},
        **{state: sum(slot["status"] == state for slot in slots)
           for state in ("not_started", "started", "terminal")}}


def _display_decimal(value, specification):
    """Format saved Decimal strings without changing their evidence value."""
    try:
        number = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError("固定搜索指标不是有效数字") from exc
    if not number.is_finite():
        raise ValueError("固定搜索指标必须为有限数字")
    return format(number, specification)


def _study_producer(row):
    """Interpret the saved producer receipt, never a model report or live worker."""
    if row is None or row["intent"] is None:
        return {"state": "已启动 · 固定搜索尚未登记", "completed": False}
    intent = json.loads(row["intent"])
    if _digest(intent) != row["intent_hash"]:
        raise ValueError("固定搜索原始登记 hash 不匹配")
    if row["receipt"] is None:
        return {"state": "已启动 · 固定搜索待保存结果", "completed": False}
    receipt = json.loads(row["receipt"])
    if not (_digest(receipt) == row["receipt_hash"]
            and receipt.get("kind") == "registered_template_control_producer_v1"
            and receipt.get("intent_hash") == row["intent_hash"]
            and receipt.get("actual_model_calls") == 0
            and receipt.get("model_final") is False
            and receipt.get("formal_target_success") is False):
        raise ValueError("固定搜索结果回执绑定或非模型语义不匹配")
    result = receipt["result"]
    selection = result.get("development_selection")
    summary = "固定搜索已结束；未保存开发选择"
    if selection is not None:
        if selection.get("outcome") not in ("development_candidate", "abstain"):
            raise ValueError("固定搜索开发选择状态未知")
        summary = ("开发候选：" + str(selection["selected_candidate_id"])
                   if selection["outcome"] == "development_candidate" else "弃权")
        metrics = selection.get("selected_metrics") or {}
        if metrics.get("return_on_full_initial_cash") is not None:
            summary += " · 完整本金收益 " + _display_decimal(metrics["return_on_full_initial_cash"], ".2%")
        if metrics.get("sharpe_rf2") is not None:
            summary += " · RF2 Sharpe " + _display_decimal(metrics["sharpe_rf2"], ".3f")
    return {"state": "固定搜索已完成", "completed": True,
        "result_summary": summary, "development_selection": selection,
        "candidate_count": len(result.get("candidates", [])),
        "actual_model_calls": 0, "model_report": False, "formal_target_success": False}


def study_snapshot(study_id):
    """Read only one explicitly named study from the canonical registry.

    No core imports, registry initializer, source verification or dispatch are used.
    Canonical calls already charge descendants to their original trial; stage
    ledgers supply delivery state only and are never added to token totals.
    """
    registry = PROJECT_ROOT / "experiment_traces/meta_framework_v3/study_registry.sqlite3"
    result = {"study_id": study_id, "registry_path": str(registry), "registered": False,
        "state": "未登记", "slots": [], "groups": [], "formal_target_success": False,
        "formal_success_denominator_contribution": 0,
        "observed_at": datetime.now(timezone.utc).isoformat()}
    # Display-only planned slots. They never register or claim a trial.
    if study_id == "v4s1":
        result["slots"] = [_study_slot({"id": code + str(repeat), "repeat": repeat,
            "architecture_contract": {"architecture": name}}, False)
            for name, (code, _) in STUDY_ARCHITECTURES.items() for repeat in (1, 2, 3)]
    try:
        if not isinstance(study_id, str) or not study_id or len(study_id) > 128:
            raise ValueError("study_id 必须为明确的非空名称")
        if not registry.is_file():
            result["error"] = "中央研究登记账尚不存在；界面不会创建。"
            return result
        with closing(sqlite3.connect(registry.resolve().as_uri() + "?mode=ro", uri=True, timeout=5)) as db:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA query_only=ON")
            db.execute("BEGIN")
            tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            study = db.execute("SELECT plan,plan_hash FROM studies WHERE id=?", (study_id,)).fetchone()
            if study is None:
                result["error"] = "本次 study_id 尚未在中央账登记；预备槽位不代表已启动。"
                return result
            plan = json.loads(study["plan"])
            if _digest(plan) != study["plan_hash"] or plan["study_id"] != study_id:
                raise ValueError("中央研究计划 hash 或身份不匹配")
            specs = plan["trials"]
            if not 1 <= len(specs) <= 96 or len({s["id"] for s in specs}) != len(specs):
                raise ValueError("中央研究槽位列表无效")
            trials = {row["id"]: dict(row) for row in db.execute("SELECT * FROM trials WHERE study_id=?", (study_id,))}
            if set(trials) != {s["id"] for s in specs}:
                raise ValueError("中央研究计划与已登记槽位不一致")
            calls = [dict(row) for row in db.execute("SELECT trial_id,id,reserve,known_tokens FROM calls WHERE study_id=? ORDER BY trial_id,id", (study_id,))]
            descendants = ([dict(row) for row in db.execute("SELECT * FROM trial_descendants WHERE study_id=? ORDER BY trial_id,root", (study_id,))]
                           if "trial_descendants" in tables else [])
            mappings = {(row["trial_id"], row["id"]): dict(row) for row in db.execute(
                "SELECT * FROM model_call_stages WHERE study_id=?", (study_id,))} if "model_call_stages" in tables else {}
            producers = {row["trial_id"]: dict(row) for row in db.execute(
                "SELECT * FROM producer_runs WHERE study_id=?", (study_id,))} if "producer_runs" in tables else {}
        slots = []
        for spec in specs:
            slot = _study_slot(spec)
            slots.append(slot)
            row = trials[spec["id"]]
            own_calls = [call for call in calls if call["trial_id"] == spec["id"]]
            children = [child for child in descendants if child["trial_id"] == spec["id"]]
            child_roots = {child["root"] for child in children}
            slot.update(root=row["root"], claimed_at=row["claimed_at"], child_count=len(children),
                call_count=len(own_calls), known_tokens=sum(c["known_tokens"] or 0 for c in own_calls),
                unknown_reserve=sum(c["reserve"] for c in own_calls if c["known_tokens"] is None))
            for call in own_calls:
                mapping = mappings.get((spec["id"], call["id"]))
                if mapping is None and "model_call_stages" in tables:
                    slot["errors"].append("调用缺少中央父子阶段归属：" + call["id"])
                    continue
                stage_root = mapping["root"] if mapping else row["root"]
                if stage_root == row["root"]:
                    slot["parent_call_count"] += 1
                elif stage_root in child_roots:
                    slot["child_call_count"] += 1
                else:
                    slot["errors"].append("调用阶段不在该槽位的已登记父子域：" + call["id"])
            try:
                if json.loads(row["spec"]) != spec or row["root"] != spec["root"]:
                    raise ValueError("中央槽位冻结内容不匹配")
                if slot["errors"]:
                    raise ValueError("调用费用已保留；阶段归属不完整，暂不确认终态")
                if row["claimed_at"] is None:
                    if own_calls or children or producers.get(spec["id"], {}).get("intent"):
                        raise ValueError("未启动槽位已有调用或 producer 记录")
                    continue
                slot.update(state="已启动", status="started")
                if slot["architecture"] == "fixed_template_search":
                    producer = _study_producer(producers.get(spec["id"]))
                    slot.update(producer=producer, state=producer["state"],
                        result_summary=producer.get("result_summary"))
                    if producer["completed"]:
                        slot["status"] = "terminal"
                    if own_calls:
                        raise ValueError("固定模板槽位出现模型调用；费用保留并标记异常")
                    continue
                roots = [(row["root"], row["stage_plan_hash"], "parent")]
                roots += [(child["root"], child["stage_plan_hash"], "child") for child in children]
                for stage_root, plan_hash, role in roots:
                    if not plan_hash:
                        slot["stages"].append({"root": stage_root, "role": role,
                            "state": "已登记，阶段账待绑定", "terminal": False})
                        continue
                    stage_path = Path(stage_root).resolve()
                    if _digest(_saved_plan(stage_path)) != plan_hash:
                        raise ValueError("阶段计划与中央绑定 hash 不匹配：" + stage_root)
                    stage = research_snapshot(stage_path)
                    if stage.get("error"):
                        raise ValueError(stage["error"])
                    stage.update(root=stage_root, role=role,
                        terminal=bool(stage["tasks"]) and all(t["terminal"] for t in stage["tasks"])
                            and stage["pending_calls"] == 0)
                    slot["stages"].append(stage)
                if all(stage["terminal"] for stage in slot["stages"]):
                    slot.update(status="terminal", state="已终结")
                slot["result_summary"] = "；".join(("父：" if stage["role"] == "parent" else "子：")
                    + stage["state"] for stage in slot["stages"])
            except (OSError, ValueError, TypeError, KeyError, sqlite3.Error) as exc:
                slot["errors"].append(str(exc))
                slot.update(state="读取异常，终态未确认", status="started" if row["claimed_at"] is not None else "not_started")
        result.update(registered=True, state="已登记 · 局部开发比较", slots=slots,
            plan_hash=study["plan_hash"], totals=_study_totals(slots),
            groups=[{"architecture": name, "label": label, "slot_count": len(group), **_study_totals(group)}
                for name, (_, label) in STUDY_ARCHITECTURES.items()
                if (group := [slot for slot in slots if slot["architecture"] == name])])
    except (OSError, ValueError, TypeError, KeyError, sqlite3.Error) as exc:
        result.update(state="登记账读取失败", error=str(exc))
    return result


def controller_snapshot(path):
    """Refresh saved progress, while separately observing an optional worker identity."""
    if path is None:
        return None
    try:
        if path.stat().st_size > 65536:
            raise ValueError("Controller status exceeds 64 KiB")
        status = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(status, dict) or not isinstance(status.get("stages"), list):
            raise ValueError("Controller status must contain a stages list")
        # Liveness is never copied from a saved progress claim.
        worker = {"live": False, "status": "not_configured"}
        identity_path = status.get("worker_identity_path")
        if identity_path:
            worker["status"] = "not_verified_live"
            try:
                identity_path = Path(identity_path)
                if not identity_path.is_absolute():
                    identity_path = path.parent / identity_path
                ident = json.loads(identity_path.read_text(encoding="utf-8-sig"))
                process = psutil.Process(ident["pid"])
                command = process.cmdline()
                is_viewer = any("serve_research_v3.py" in str(part) for part in command)
                worker["live"] = (not is_viewer and process.is_running()
                                  and process.create_time() == ident["create_time"]
                                  and command == ident["cmdline"])
                if worker["live"]:
                    worker["status"] = "verified_live"
            except (OSError, ValueError, TypeError, KeyError, psutil.Error):
                pass
        try:
            observed = datetime.fromisoformat(status["observed_at"].replace("Z", "+00:00"))
            if observed.tzinfo is None:
                raise ValueError("Snapshot timestamp needs timezone")
            status["snapshot_age_seconds"] = max(0, time.time() - observed.timestamp())
        except (KeyError, TypeError, ValueError, AttributeError):
            status["snapshot_age_seconds"] = None
        status["source_path"] = str(path)
        status["worker_observation"] = worker
        # Never reuse an inline saved claim or discover unrelated studies.
        status.pop("study_status", None)
        if status.get("study_id") is not None:
            status["study_status"] = study_snapshot(status["study_id"])
        status.pop("cycle_results_summary", None)
        summary_path = status.get("cycle_results_summary_path")
        if summary_path:
            summary_path = Path(summary_path)
            if not summary_path.is_absolute():
                summary_path = PROJECT_ROOT / summary_path
            try:
                report = _read_json(summary_path, 65536)
                required = {"title", "summary", "facts", "columns", "rows", "limitations", "next_step"}
                if not (isinstance(report, dict) and required <= set(report)
                        and all(isinstance(report[key], str) for key in ("title", "summary", "limitations", "next_step"))
                        and all(isinstance(report[key], list) for key in ("facts", "columns", "rows"))
                        and all(isinstance(value, str) for value in report["facts"] + report["columns"])
                        and all(isinstance(row, list) and len(row) == len(report["columns"]) for row in report["rows"])):
                    raise ValueError("Cycle result summary does not match the evidence report format")
                status["cycle_results_summary"] = report
                status["cycle_results_summary_status"] = {"status": "available", "path": str(summary_path)}
            except (OSError, ValueError, TypeError) as exc:
                status["cycle_results_summary_status"] = {"status": "not_available",
                    "path": str(summary_path), "reason": str(exc)}
        else:
            status["cycle_results_summary_status"] = {"status": "not_configured"}
        if status.get("research_root"):
            research_root = Path(status["research_root"])
            if not research_root.is_absolute():
                research_root = PROJECT_ROOT / research_root if status.get("cycle_root") else path.parent / research_root
            if status.get("cycle_root"):
                cycle_root = Path(status["cycle_root"])
                if not cycle_root.is_absolute():
                    cycle_root = PROJECT_ROOT / cycle_root
                cycle = cycle_snapshot(cycle_root, research_root)
                status["cycle_status"] = cycle
                research = cycle.get("current_stage") or {"error": "当前阶段尚未绑定或账本尚未创建。"}
                prior_root = Path(status.get("prior_v4_research_root") or
                    PROJECT_ROOT / "experiment_traces/meta_framework_v4/account_research_trial_001")
                status["prior_v4_research_status"] = research_snapshot(prior_root.resolve())
            else:
                research = research_snapshot(research_root.resolve())
            status["research_status"] = research
            if not research.get("error"):
                # The API's research totals are current ledger values, never stale saved counts.
                totals = status.get("cycle_status", research)
                status["new_v4_research_calls"] = totals["call_count"]
                status["new_v4_known_tokens"] = totals["known_tokens"]
                for item in status["stages"]:
                    if item.get("id") == "real_research":
                        item["status"] = research["state"]
                        item["gap"] = (research["pause_reason"] if research["paused"] and research["pause_reason"]
                                       else "已保存研究终稿；仍需独立审阅，不代表正式验证通过。" if research["report_summary"]
                                       else "存在待返回调用；用量与内容以实际回执为准。" if research["pending_calls"]
                                       else "以账本记录显示当前动作；完整研究报告尚未保存。")
        return status
    except (OSError, ValueError, TypeError) as exc:
        return {"error": str(exc), "source_path": str(path)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True)
    parser.add_argument("--port", type=int, default=8777)
    parser.add_argument("--related", action="append", default=[])
    parser.add_argument("--evidence", action="append", default=[], help="Read-only saved evidence summaries")
    parser.add_argument("--controller-status", help="V4 stage snapshot JSON, reloaded on every request")
    args = parser.parse_args()
    root = Path(args.root).resolve()
    roots = [root] + [Path(x).resolve() for x in args.related]
    evidence_paths = [Path(x).resolve() for x in args.evidence]
    controller_path = Path(args.controller_status).resolve() if args.controller_status else None
    def combined():
        parts = [snapshot(x) for x in roots]
        reports = []
        for path in evidence_paths:
            if path.stat().st_size > 65536:
                raise ValueError("Evidence summary exceeds 64 KiB")
            reports.append(json.loads(path.read_text(encoding="utf-8")))
        return {"observed_at": datetime.now(timezone.utc).isoformat(),
                "controller_status": controller_snapshot(controller_path),
                "historical_ledger_paths": [str(x / "ledger.sqlite3") for x in roots],
                "live": any(x["live"] for x in parts), "tasks": [t for x in parts for t in x["tasks"]],
                "calls": [c for x in parts for c in x["calls"]],
                "known_tokens": sum(x["known_tokens"] for x in parts), "reserved": sum(x["reserved"] for x in parts),
                "formal_target_success": False, "old_v2_unknown_reserve": 80000, "evidence_reports": reports}
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            try:
                if self.path == "/":
                    body, content = PAGE.encode(), "text/html; charset=utf-8"
                elif self.path == "/api/status":
                    body, content = json.dumps(combined(), ensure_ascii=False).encode(), "application/json; charset=utf-8"
                else:
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header("Content-Type", content)
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except Exception as exc:
                self.send_error(500, str(exc))
        def log_message(self, *_):
            pass
    ThreadingHTTPServer(("192.0.2.10", args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
