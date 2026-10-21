"""Read-only summary of one execution day from the executor run folders (no broker calls).
Used by daily_report.py and monitor.py: which runs traded, abnormal stops, planned vs. unfinished names and why."""
import json, glob
from pathlib import Path

STATUS_CN = {'scheduled_waiting': '已预约，等待09:30', 'waiting_open': '等待开盘', 'starting': '启动中', 'preparing': '准备计划',
             'prepared_readonly': '只读核验完成未交易', 'preflight_passed': '开盘核验通过', 'watching': '交易中', 'trading': '交易中',
             'formal_rebalancing': '正式调仓中', 'pilot_accepted_preparing_targets': '试单通过，准备正式调仓', 'session_paused': '午间暂停',
             'reconciling': '核对账户中', 'reconciling_corporate_action': '核验分红送转中', 'recovering_readonly': '短暂故障自动恢复中',
             'recovering_submission': '追查委托回执中', 'waiting_login': '等待恢复登录', 'login_required': '需要登录Choice',
             'stopped_review_required': '停止，需人工审核', 'stopped': '已停止', 'closed': '收盘结束', 'completed_targets': '目标全部完成',
             'completed_pilot': '试单结束', 'position_floor_unmet': '仓位底线未满足', 'handed_off': '已移交'}

def cn(status): return STATUS_CN.get(status, status or '无记录')

def _load(p):
    try: return json.loads(Path(p).read_text(encoding='utf-8'))
    except (OSError, ValueError): return None

def _reason(code, qty, deferred):
    if code in deferred: return deferred[code].replace('不利方向超过3%价格保护或价格精度', '价格保护3%')
    if abs(qty) < 100: return '零股暂不可卖' if qty < 0 else '不足一手'
    return '未记录原因'

def day_runs(runs_dir, day):
    out = []
    for d in sorted(glob.glob(str(Path(runs_dir) / day / '*' / ''))):
        d = Path(d); st = _load(d / 'status_redacted.json') or {}; ps = _load(d / 'private_state.json'); pr = _load(d / 'process.json') or {}
        out.append(dict(dir=d, name=d.name, status=st.get('status'), reason=st.get('reason'), at=st.get('at'), started_at=pr.get('started_at'),
                        pid=pr.get('pid'), ledger=ps is not None, attempts=len((ps or {}).get('attempts', [])), state=ps, raw_status=st))
    return out

def main_run(runs):
    """The run with the day's ledger and most order attempts; ties go to the latest status time."""
    cands = [r for r in runs if r['ledger']]
    return max(cands, key=lambda r: (r['attempts'], r['at'] or '')) if cands else None

def summarize_day(runs_dir, day):
    runs = day_runs(runs_dir, day); m = main_run(runs)
    stops = [r for r in runs if r['ledger'] and r['status'] == 'stopped_review_required']
    res = dict(runs=len([r for r in runs if r['ledger']]), stops=[dict(at=(r['at'] or '')[11:16], reason=(r['reason'] or '')[:80]) for r in stops],
               final_status=m['status'] if m else None, final_reason=m['reason'] if m else None, planned=None, buys=None, sells=None, unfinished=[])
    if not m: return res
    st = m['raw_status']; state = m['state']
    plan = _load(m['dir'] / 'formal_plan.json')
    if plan and state:
        base = state.get('baseline', {}).get('positions', {}); tg = {c: int(v['quantity']) for c, v in plan.get('targets', {}).items()}
        diff = {c: tg.get(c, 0) - int(base.get(c, 0)) for c in set(tg) | set(base) if tg.get(c, 0) != int(base.get(c, 0))}
        res.update(planned=len(diff), buys=sum(1 for v in diff.values() if v > 0), sells=sum(1 for v in diff.values() if v < 0))
    remaining = st.get('remaining') or {}; deferred = st.get('deferred') or {}
    res['unfinished'] = [dict(code=c, side='买' if q > 0 else '卖', qty=abs(int(q)), reason=_reason(c, int(q), deferred)) for c, q in sorted(remaining.items())]
    return res

def lines(summary):
    out = []
    if summary['planned'] is not None:
        done = summary['planned'] - len(summary['unfinished'])
        s = f"调仓：计划 {summary['planned']} 只（买 {summary['buys']} / 卖 {summary['sells']}），到位 {done} 只"
        if summary['unfinished']: s += f"；未完成 {len(summary['unfinished'])} 只：" + '、'.join(f"{u['code']} {u['side']}{u['qty']}股（{u['reason']}）" for u in summary['unfinished'][:6])
        out.append(s)
    if summary['runs']:
        s = f"执行器：最终{cn(summary['final_status'])}"
        if summary['stops']: s += f"；当天异常停止 {len(summary['stops'])} 次（" + '；'.join(f"{x['at']} {x['reason']}" for x in summary['stops'][:2]) + '）'
        out.append(s)
    return out
