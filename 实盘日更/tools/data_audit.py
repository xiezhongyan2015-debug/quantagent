"""Daily data audit before release: PCF, valuation prices, c5 states, minute store, daily bars, GRU signal, target. Writes live/audit_<T>.json; exit 2 on hard failures.
Usage: data_audit.py <T> <T1>"""
import sys, os, json, glob, re
from pathlib import Path
import numpy as np, pandas as pd, xml.etree.ElementTree as ET
T, T1 = sys.argv[1], sys.argv[2]; T8 = T.replace('-', ''); Q = Path('C:/ChoiceBridge/quant/current'); L = Q / '实盘日更'; LIVE = Path(os.environ.get('QD_LIVE', 'C:/ChoiceBridge/live')); D = Q / 'gru_research/trading_data'
problems, warnings, info = [], [], {}
def hard(cond, msg):
    if not cond: problems.append(msg)
def soft(cond, msg):
    if not cond: warnings.append(msg)
# PCF
p = L / f'inputs/pcf/563030_{T8}.xml'
if p.exists():
    r = ET.fromstring(p.read_bytes()); n = len(r.findall('ComponentList/Component')); info['pcf'] = dict(trading_day=r.findtext('TradingDay'), valuation=r.findtext('PreTradingDay'), components=n)
    hard(r.findtext('TradingDay') == T8, 'PCF 不是当天的'); hard(n >= 100, 'PCF 成分数量异常'); v8 = r.findtext('PreTradingDay')
    px = L / f'inputs/prices/close_{v8}.csv'; rec = L / f'inputs/prices/close_{v8}.receipt.json'
    hard(px.exists(), '估值日收盘价缺失')
    if rec.exists(): rj = json.loads(rec.read_text(encoding='utf-8')); info['prices'] = dict(names=rj['names'], missing=len(rj['missing'])); hard(not rj['missing'], f"估值价缺 {len(rj['missing'])} 只")
else: problems.append('PCF 文件缺失')
# c5
c = L / f'inputs/c5/c5_state_{T8}.parquet'
if c.exists():
    d = pd.read_parquet(c); d['date'] = d.date.astype(str).str[:10]; d = d[d.date == T]; st = d.state.fillna('').value_counts().to_dict(); info['c5'] = dict(rows=len(d), states={k: int(v) for k, v in st.items()}, raw_finite=float(np.isfinite(pd.to_numeric(d.raw_score, errors='coerce')).mean()) if len(d) else 0)
    hard(len(d) >= 1700, f'c5 当天行数 {len(d)} 过少'); hard(st.get('excluded', 0) >= 60, 'c5 excluded 数量异常'); soft(st.get('vetoed', 0) >= 20, f"c5 vetoed 只有 {st.get('vetoed', 0)} 只（基本面可能过期）"); hard(info['c5']['raw_finite'] >= 0.9, 'c5 原始分缺失过多')
else: problems.append('c5 当天状态文件缺失')
# c5 fundamentals: since 2026-10-13 the chain rebuilds a live grid on the panel's own dates (event fields carry the latest disclosure);
# the research grid only applies if that rebuild failed, and then the veto inputs run out 15 trading days after its last date.
C5D = Path(os.environ.get('QD_C5_DATA', 'C:/ChoiceBridge/c5_stage_20261012/payload/c5_core/data'))
cal = json.loads((L / 'config/calendar_2026.json').read_text(encoding='utf-8'))
live_g, res_g = C5D / 'A_Layer_Live/fundamental_grid/grid.json', C5D / 'A_Layer_Research/fundamental/grid/grid.json'
gj = live_g if live_g.exists() and max(json.loads(live_g.read_text(encoding='utf-8'))['dates'])[:10] >= T else res_g
if gj.exists():
    last = max(json.loads(gj.read_text(encoding='utf-8'))['dates'])[:10]; after = [d for d in cal if d > last]; used = sum(1 for d in after if d <= T); left = 15 - used
    info['fundamentals'] = dict(grid='live' if gj == live_g else 'research', grid_last=last, ffill_left=left)
    soft(left > 5, f'c5 基本面网格截至 {last}（{info["fundamentals"]["grid"]}），前向填充还剩 {max(left, 0)} 个交易日，之后否决名单会清空' if left >= 0 else f'c5 基本面网格截至 {last}，已超出前向填充，否决名单已失效')
st_p = C5D / 'A_Layer_Research/fundamental/raw/_reseller_update_state.json'
if st_p.exists():
    rs = json.loads(st_p.read_text(encoding='utf-8')); info['fundamentals_update'] = dict(last_ok_end=rs.get('last_ok_end'), failed=sorted(rs.get('failed', {})))
    lag = sum(1 for d in cal if (rs.get('last_ok_end') or '0')[:4] + '-' + (rs.get('last_ok_end') or '0000000')[4:6] + '-' + (rs.get('last_ok_end') or '00000000')[6:8] < d <= T)
    soft(lag <= 3, f'基本面增量（Tushare 转售接口）已 {lag} 个交易日没有成功更新，最近失败：{"、".join(sorted(rs.get("failed", {}))) or "无"}')
    # 2026-10-19 (audit P2-5): a partial failure used to pass silently because the grid is rebuilt to today anyway
    soft(lag > 3 or not rs.get('failed'), f'基本面增量部分失败 {len(rs.get("failed", {}))} 类（{"、".join(sorted(rs.get("failed", {}))[:6])}…），完整成功截至 {rs.get("last_ok_end")}；网格日期是重建的，不代表这些字段已更新')
# minute store / daily bars / quotes
store = sorted(glob.glob(str(D / 'minute_store/*.parquet'))); good = 0
for f in store[::7]:
    m = pd.read_parquet(f, columns=['start']); s = pd.to_datetime(m.start, utc=True).dt.tz_convert('Asia/Shanghai'); good += int((s.dt.strftime('%Y-%m-%d') == T).sum() >= 230)
info['minute_store'] = dict(symbols=len(store), sampled=len(store[::7]), with_today=good); hard(len(store) >= 1100, f'分钟库只有 {len(store)} 只'); hard(good >= 0.9 * max(1, len(store[::7])), f'分钟库当天完整的样本 {good}/{len(store[::7])}')
td = sorted(glob.glob(str(D / f'future_minute_probe/tencent_daily_{T}/raw/*_raw.json'))); has_today = 0
for f in td[::10]:
    j = json.loads(Path(f).read_text(encoding='utf-8')); k = list(j['data'])[0]; has_today += int(any(r[0] == T for r in j['data'][k].get('day', [])))
info['tencent_daily'] = dict(files=len(td), sampled=len(td[::10]), with_today=has_today); hard(len(td) >= 1100, f'腾讯日线只有 {len(td)} 只'); soft(has_today >= 0.8 * max(1, len(td[::10])), f'腾讯当天日 K 仅 {has_today}/{len(td[::10])} 只（接口未更新，复权系数沿用前一日）')
qt = D / f'future_minute_probe/tencent_qt_{T}/qt_snapshot.json'; hard(qt.exists() and len(json.loads(qt.read_text(encoding='utf-8'))['rows']) >= 1100, '腾讯批量行情缺失')
# GRU signal
z = Q / f'outputs/gru_live_extend_20261012/signals/z_{T}_slot15.csv'
if z.exists():
    zz = pd.read_csv(z); n = int(np.isfinite(zz.z).sum()); info['gru'] = dict(names=n, z_abs_mean=round(float(zz.z.abs().mean()), 3)); hard(n >= 400, f'GRU 信号只有 {n} 只'); hard(0.4 <= info['gru']['z_abs_mean'] <= 1.2, 'GRU z 尺度异常')
    prevs = sorted(glob.glob(str(z.parent / 'z_*_slot15.csv'))); prevs = [x for x in prevs if x < str(z)]
    if prevs:
        pz = pd.read_csv(prevs[-1]).set_index('code').z; rc = float(zz.set_index('code').z.corr(pz, method='spearman')); info['gru']['rank_corr_prev'] = round(rc, 3); hard(rc >= 0.8, f'GRU 信号与前一日秩相关 {rc:.2f} 过低'); soft(rc <= 0.995, 'GRU 信号与前一日几乎相同（疑似未更新）')
    hard((z.parent / f'blend_{T}.csv').exists(), '混合信号缺失')
    a15 = z.parent / f'blend_{T}_slot15_all15.csv'
    soft(a15.exists(), '十五模型混合信号缺失（2026-10-19 起实盘用它；缺失时 daily.sh 退回五种子）')
    if a15.exists():   # 2026-10-19 (audit P2-4): check the file the live target actually uses, not only the five-seed one
        bb = pd.read_csv(a15); bz = pd.to_numeric(bb.z, errors='coerce'); bb['date'] = bb.date.astype(str).str[:10]
        info['all15'] = dict(rows=int(len(bb)), finite=int(np.isfinite(bz).sum()), z_abs_mean=round(float(bz.abs().mean()), 3), dates=sorted(bb.date.unique())[-2:])
        soft(set(bb.date) == {T} and not bb.code.duplicated().any(), '十五模型混合信号日期或代码异常'); soft(int(np.isfinite(bz).sum()) >= 1500, f"十五模型混合信号只有 {info['all15']['finite']} 个有限值"); soft(0.3 <= info['all15']['z_abs_mean'] <= 1.5, '十五模型混合信号尺度异常')
        z15 = z.parent / f'z_{T}_slot15_all15.csv'
        if z15.exists():
            gz = pd.to_numeric(pd.read_csv(z15).z, errors='coerce'); info['all15']['gru_names'] = int(np.isfinite(gz).sum()); soft(info['all15']['gru_names'] >= 400, f"十五模型 GRU 信号只有 {info['all15']['gru_names']} 只")
else: problems.append('GRU 当天信号缺失')
# target
tg = L / f'outputs/targets/{T1}/precheck_{T1}.json'
if tg.exists():
    pre = json.loads(tg.read_text(encoding='utf-8')); info['target'] = {k: pre.get(k) for k in ('released', 'mode', 'names', 'stock_weight', 'planned_two_way_turnover', 'csi500_share_of_target', 'fallback_from')}
    hard(pre.get('released') is True, '目标未通过闸门'); soft(not pre.get('fallback_from'), '目标发生了降层'); soft((pre.get('planned_two_way_turnover') or 0) <= 0.45, '换手超过 45%')
    # 2026-10-19 (audit P2-3/P2-4/P1-2): degradations that release normally but must be seen
    tj = tg.with_name(f'target_{T1}.json'); gen = json.loads(tj.read_text(encoding='utf-8')).get('generated_from', {}) if tj.exists() else {}
    soft(str(gen.get('signal') or '').endswith('_slot15_all15.csv'), f"实盘信号不是十五模型混合（{Path(str(gen.get('signal'))).name}），已退回五种子")
    if (L / 'config/skeleton_v5.json').exists() and json.loads((L / 'config/skeleton_v5.json').read_text(encoding='utf-8')).get('enabled'):
        soft(pre.get('tilt_form') == 'sqrtb' and (pre.get('backfill_names') or 0) > 0, '骨架 v5 开着，但目标没有用 √b 倾斜或没有回填')
        soft(not pre.get('backfill_dropped_no_price'), f"回填股缺价被丢弃：{'、'.join(pre.get('backfill_dropped_no_price') or [])}")
    bj = LIVE / f'timing/beta_shadow/{T1}/applied_{T1}.json'
    if bj.exists():
        ba = json.loads(bj.read_text(encoding='utf-8')); info['beta_tilt'] = {k: ba.get(k) for k in ('applied', 'pos', 'a', 'reason', 'turnover')}
        soft(ba.get('applied') or str(ba.get('reason', '')).startswith('pos=0'), f"beta 倾斜没有应用：{ba.get('reason')}（已用未倾斜目标）")
        sw = json.loads((LIVE / 'switch_days.json').read_text(encoding='utf-8-sig')) if (LIVE / 'switch_days.json').exists() else []
        soft(ba.get('applied') or T in sw or (pre.get('planned_two_way_turnover') or 0) <= 0.40, f"未倾斜备用目标换手 {(pre.get('planned_two_way_turnover') or 0):.0%} 超过最终闸门 40%（beta 未倾斜时按 60% 放行）")
else: problems.append('目标文件预检缺失')
out = dict(day=T, next=T1, ok=not problems, problems=problems, warnings=warnings, info=info); LIVE.mkdir(parents=True, exist_ok=True); (LIVE / f'audit_{T}.json').write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding='utf-8')
print(json.dumps(out, ensure_ascii=False)); sys.exit(0 if not problems else 2)
