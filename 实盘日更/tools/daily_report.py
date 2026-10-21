"""Daily report for the team: today's fills, fees, NAV change vs the CSI500 index, holdings summary, tomorrow's plan.
Reads only files on the cloud machine (executor ledger, read-only snapshots, release receipts) and public index closes.
Usage: daily_report.py <T> [--next T1] [--out DIR] [--notify C:/ChoiceBridge/live/notify.json]
notify.json: {"feishu_webhook": "https://open.feishu.cn/open-apis/bot/v2/hook/...", "feishu_secret": "optional"} and/or dingtalk_webhook / wecom_webhook
"""
import sys, os, json, glob, datetime as dt, hashlib
from pathlib import Path
import requests
sys.path.insert(0, str(Path(__file__).resolve().parent))
from execution_status import summarize_day, lines as exec_lines, cn as status_cn
LIVE = Path(os.environ.get('QD_LIVE', 'C:/ChoiceBridge/live')); RUNS = Path(os.environ.get('QD_RUNS', 'C:/ChoiceBridge/executor/research/choice_pcf_automation/console/runs')); QUANT = Path(os.environ.get('QD_QUANT', 'C:/ChoiceBridge/quant/current'))
T = sys.argv[1]; args = sys.argv[2:]
def opt(name, default=None): return args[args.index(name) + 1] if name in args else default
NEXT = opt('--next'); OUT = Path(opt('--out', str(LIVE / 'reports'))); OUT.mkdir(parents=True, exist_ok=True); NOTIFY = Path(opt('--notify', str(LIVE / 'notify.json')))
def load(p): return json.loads(Path(p).read_text(encoding='utf-8'))
cal = load(QUANT / '实盘日更/config/calendar_2026.json'); prev_day = max([d for d in cal if d < T], default=None)
# ---- account today / yesterday (read-only snapshots written by the pipeline)
def snap(day):
    p = LIVE / f'captain_snapshot_{day}.json'
    if not p.exists(): return None
    s = load(p)
    if 'balance' in s: b = s['balance']; return dict(nav=float(b['totalAssets']), cash=float(b['availBalance']), stock=float(b['totalPosValue']), names=sum(1 for x in s['positions'] if int(x['shares']) > 0), at=s['checked_at'])
    return dict(nav=float(s['nav']), cash=float(s.get('available_cash', 0)), stock=float(s.get('position_market_value', float(s['nav']) - float(s.get('available_cash', 0)))), names=sum(1 for x in s['positions'] if int(x['shares']) > 0), at=s.get('captured_at'))
today, yest = snap(T), snap(prev_day) if prev_day else None
# ---- index closes (Tencent public)
def index_close(day):
    for host in ('https://proxy.finance.qq.com/ifzqgtimg/appstock/app/fqkline/get', 'https://web.ifzq.gtimg.cn/appstock/app/fqkline/get'):   # 2026-10-20: second host when the first is WAF-blocked
        try:
            j = requests.get(host, params={'param': f'sh000905,day,{day},{day},5,'}, timeout=15).json()
            rows = {r[0]: float(r[2]) for r in j['data']['sh000905'].get('day', [])}; return rows.get(day)
        except Exception: continue
    return None
ic_t, ic_y = index_close(T), index_close(prev_day) if prev_day else None
# ---- executor ledger for T
from daily_ledger_summary import summarize
runs = sorted(glob.glob(str(RUNS / T / '*' / 'private_state.json')))
summary = summarize(runs, T)
fills, cancelled, fees, pilot = (summary[k] for k in ('fills','cancelled','fees','pilot'))
buy = sum(f['cny'] for f in fills if f['side'] == '买'); sell = sum(f['cny'] for f in fills if f['side'] == '卖')
# ---- next-day plan
plan = None
if NEXT and (LIVE / f'release_receipt_{NEXT}.json').exists(): plan = load(LIVE / f'release_receipt_{NEXT}.json')
statuses = [load(p).get('status') for p in sorted(glob.glob(str(RUNS / (NEXT or 'none') / '*' / 'status_redacted.json')))]
status = 'scheduled_waiting' if 'scheduled_waiting' in statuses else (statuses[-1] if statuses else None)
# ---- compose
PRIVATE = '--private' in args   # pipeline not finished yet: goes to the private alert channel, not the team group
# 2026-10-16: live/report_route.json {"private_days": ["2026-10-16"]} sends a normal, complete report to the private channel only
# (header without the team-report prefix, so notify.py routes it to alert_feishu_webhook even when re-sent from the outbox).
ROUTE = LIVE / 'report_route.json'; PRIVATE_DAY = (not PRIVATE) and ROUTE.exists() and T in (load(ROUTE).get('private_days') or [])
lines = [f'【日更未完成 {T}，仅发本人】' if PRIVATE else (f'【日报·仅发本人 {T}】（按要求今天不发群）' if PRIVATE_DAY else f'【北国银行 日报 {T}】')]
if today:
    nav_ret = (today['nav'] / yest['nav'] - 1) if yest else None; idx_ret = (ic_t / ic_y - 1) if (ic_t and ic_y) else None
    lines.append(f"净资产 {today['nav']/1e4:,.1f} 万（现金 {today['cash']/1e4:,.1f} 万，持仓 {today['names']} 只，股票 {today['stock']/today['nav']:.1%}）")
    if nav_ret is not None: lines.append(f"今日净值 {nav_ret:+.2%}" + (f"，中证500 {idx_ret:+.2%}，超额 {nav_ret-idx_ret:+.2%}" if idx_ret is not None else ''))
else: lines.append('今日账户快照缺失')
lines.append(f"成交 {len(fills)} 笔（买 {buy/1e4:,.1f} 万 / 卖 {sell/1e4:,.1f} 万），撤单 {cancelled} 笔" + (f"，费用 {fees:,.0f} 元" + (f"（成交额的 {fees/(buy+sell)*1e4:.1f}bp）" if buy + sell > 0 else '') if fees is not None else '') + (f"，试单 {pilot}" if pilot else ''))
try: execution = summarize_day(RUNS, T); lines.extend(exec_lines(execution))
except Exception as e: execution = None; lines.append(f'执行汇总读取失败：{type(e).__name__}')
if summary['warnings']: lines.append('成交记录核验提醒：'+'；'.join(summary['warnings'][:3]))
audit_p = LIVE / f'audit_{T}.json'
if audit_p.exists():
    au = load(audit_p); lines.append('数据审核：' + ('通过' if au.get('ok') else '未通过') + (('；' + '；'.join(au.get('problems', [])[:4])) if au.get('problems') else ''))
    if au.get('warnings'): lines.append('⚠️ 提醒：' + '；'.join(au.get('warnings', [])[:4]))
if plan: lines.append(f"明日 {plan['execution_date']} 计划：{plan['strategy_layer']}，{plan['target_names']} 只，股票 {plan['stock_value_fraction']:.1%}，中证500 {plan['csi500_nav_fraction']:.1%}" + ('（⚠️ 低于导师要求的 85%，执行器下限 80%）' if plan['csi500_nav_fraction'] < 0.85 else '') + f"，试单 {plan['pilot_symbol']}" + (f"，执行器{status_cn(status)}" if status else '，执行器未预约'))
elif NEXT: lines.append(f"明日 {NEXT}：没有发布目标（流水线未完成），执行器不会下单")
try:  # 2026-10-19: live beta tilt (timing_research_20261017/beta_tilt_live.py writes applied_<NEXT>.json before the release)
    bt = LIVE / 'timing' / 'beta_shadow' / (NEXT or 'none') / f'applied_{NEXT}.json'
    if plan and bt.exists():
        b = load(bt); side = {1: '偏高beta', -1: '偏低beta'}.get(b.get('pos'), '不倾斜')
        if b.get('applied') and b.get('untilted_sha256') and b.get('tilted_sha256'):
            lines.append(f"beta 倾斜：已执行，{side}（{b.get('driver') or 'S7'} {b.get('episode_start')} 起，剩 {b.get('remaining')} 天），搬动 {b.get('realized_l1_half', 0):.1%}，组合 beta {b.get('beta_live')}→{b.get('beta_shadow')}")
        else: lines.append(f"beta 倾斜：未执行（{b.get('reason', '')[:60]}），按未倾斜目标交易")
except Exception as e: lines.append(f'beta 倾斜记录读取失败：{type(e).__name__}')
# 2026-10-17: read-only timing dashboard (timing_research_20261017/dashboard.py, run by daily_cloud.ps1 just before this step).
# Display only, never used for trading; a missing or broken file must not stop the report.
try:  # 2026-10-17: cloud resources of the day (resource_watch.py samples every 2 min)
    from resource_watch import day_summary
    rs = day_summary(T)
    if rs: lines.append(f"云机资源：今日最低可用内存 {rs['min_avail_gb']:.1f} GB（{rs['at']}，占用峰值 {rs['max_load_pct']}%），C 盘剩 {rs['disk_free_gb']:.0f} GB")
except Exception: pass
timing = None
try:
    tp = LIVE / 'timing' / f'dashboard_{T}.json'
    if tp.exists(): timing = load(tp); lines.extend(['', *timing.get('lines', [])[:8]])
except Exception as e: lines.append(f'择时仪表盘读取失败：{type(e).__name__}')
text = '\n'.join(lines)
md = text + '\n\n成交明细：\n' + '\n'.join(f"- {f['at']} {f['side']} {f['code']} {f['qty']} 股 @ {f['price']} = {f['cny']:,.0f}" for f in fills[:400])
(OUT / f'report_{T}.md').write_text(md, encoding='utf-8'); (OUT / f'report_{T}.json').write_text(json.dumps(dict(day=T, account=today, prev=yest, index_close=ic_t, index_prev=ic_y, fills=fills, cancelled=cancelled, fees=fees, plan=plan, status=status, execution=execution, timing_lines=(timing or {}).get('lines')), ensure_ascii=False, indent=1), encoding='utf-8')
print(text)
if os.environ.get('QD_NO_PUSH'): sys.exit(0)
# Push through notify.py; a failed push stays in the durable outbox that QuantAlertDispatch retries every minute.
os.environ.setdefault('QD_NOTIFY', str(NOTIFY))
from notify import send_or_queue
print('push', send_or_queue(text, 'daily_report_private' if (PRIVATE or PRIVATE_DAY) else 'daily_report', f'daily-report:{T}:' + hashlib.sha256(text.encode()).hexdigest()[:16]))
