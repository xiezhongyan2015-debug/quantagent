"""择时仪表盘（只读，不交易）——兴证式 PM 仪表盘在中证500上的复刻（2026-10-17）。
输出 <out>/dashboard_<T>.json（含 lines：日报里的几行中文）与 dashboard_<T>.md。任何一块失败只记在 errors 里，不中断其余模块。

模块（口径见 reports/择时研报深度解读_2026-10-17.md）：
  1 波段/点位效率：中证500、沪深300、中证1000、创业板指（xy_swing.py，兴证系列二公式）
  2 LPPLS 正/负向泡沫置信度 + 兴证消散规则：中证500、创业板指（lppls.py，结果按日缓存）
  3 宽度：中证500 成分上涨占比、站上 60 日线占比、60 日新低占比；指数 vs 宽度背离（兴证系列六，周频口径为本实现的解读）
  4 3.0 战术信号（兴证《中低波组合解决方案》）：成交额活跃度、行业集中度 CR10、PB 相对动量，各 ±1，合计 −3..+3
     偏离：CR10 用并集池（沪深300/中证500/中证1000）申万一级映射而非全 A；EMA 跨度与 Min-Max 窗口原文未给，取 20/20
  5 外部读数：xy_external_log.csv 中兴证周报/月报的最新登记
用法：python dashboard.py <T> [--panel DIR] [--industry JSON] [--external CSV] [--cache DIR] [--out DIR] [--no-fetch]
"""
import sys, os, json, time, traceback, datetime as dt
from pathlib import Path
import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import xy_swing  # noqa: E402
import lppls  # noqa: E402

INDICES = {'000905': ('sh000905', '中证500'), '000300': ('sh000300', '沪深300'), '000852': ('sh000852', '中证1000'), '399006': ('sz399006', '创业板指'), '000985': ('sh000985', '中证全指')}
LPPLS_CODES = ['000905', '399006', '000985']


def opt(args, name, default=None):
    return args[args.index(name) + 1] if name in args else default


def default_panel():
    for p in ['C:/ChoiceBridge/c5_stage_20261012/payload/c5_core/data/A_Layer_Live', str(HERE.parent / 'c5_core/data/A_Layer_Live')]:
        if Path(p, 'panel').exists(): return p
    return None


# ---------------------------------------------------------------- index bars
def fetch_tencent(sym, start, end):
    import requests
    out = []; s = pd.Timestamp(start)
    while s <= pd.Timestamp(end):
        e = min(s + pd.Timedelta(days=1100), pd.Timestamp(end))
        for host in ['https://web.ifzq.gtimg.cn/appstock/app/fqkline/get', 'https://proxy.finance.qq.com/ifzqgtimg/appstock/app/fqkline/get']:
            try:
                j = requests.get(host, params={'param': f'{sym},day,{s.date()},{e.date()},800,qfq'}, timeout=20).json()
                d = j['data'][sym]; k = d.get('day') or d.get('qfqday') or []
                out += [r[:6] for r in k]; break
            except Exception:
                continue
        s = e + pd.Timedelta(days=1)
    if not out: return None
    df = pd.DataFrame(out, columns=['date', 'open', 'close', 'high', 'low', 'volume'])
    df['date'] = pd.to_datetime(df.date)
    for c in ['open', 'close', 'high', 'low', 'volume']: df[c] = df[c].astype(float)
    return df.drop_duplicates('date').set_index('date').sort_index()


def load_index(code, T, cache, panel_root, fetch=True):
    """cached Tencent bars from 2010; refresh the tail; fall back to the c5 index_daily.parquet for 300/500/1000"""
    sym, _ = INDICES[code]; f = cache / f'index_{code}.csv'
    old = pd.read_csv(f, parse_dates=['date']).set_index('date') if f.exists() else None
    new = None
    if fetch:
        start = '2010-01-01' if old is None or len(old) < 1000 else str((old.index[-1] - pd.Timedelta(days=40)).date())
        new = fetch_tencent(sym, start, T)
    df = old if new is None else (new if old is None else pd.concat([old[old.index < new.index[0]], new]))
    if (df is None or df.index[-1] < pd.Timestamp(T)) and panel_root and code in ('000905', '000300', '000852'):
        p = Path(panel_root, 'index_daily.parquet')
        if p.exists():
            x = pd.read_parquet(p); x = x[x.index_code == code].copy(); x['date'] = pd.to_datetime(x.date)
            x = x.set_index('date')[['open', 'close', 'high', 'low', 'volume']]
            df = x if df is None else pd.concat([df[df.index < x.index[0]], x]) if x.index[0] > df.index[0] else pd.concat([df[df.index < x.index[0]], x])
    if df is None: return None
    df = df[~df.index.duplicated(keep='last')].sort_index()
    df.reset_index().to_csv(f, index=False)
    return df[df.index <= pd.Timestamp(T)]


# ---------------------------------------------------------------- 1 swing
def swing_block(df):
    out, piv, ext_i = xy_swing.swing(df)
    t = len(df) - 1; e = xy_swing.efficiency(df, piv, t)
    rows = []
    for k in range(2, len(piv)):
        a, b, c = piv.iloc[k - 2], piv.iloc[k - 1], piv.iloc[k]
        rows.append(dict(kind=c.kind, eff=abs(c.price - b.price) / abs(b.price - a.price), amp=c.price / b.price - 1))
    R = pd.DataFrame(rows)
    falling = e['order'].startswith('L-H')  # last pivot is H -> current leg falls toward an L
    same = R[R.kind == ('L' if falling else 'H')] if len(R) else R
    leg_ret = df.close.iloc[-1] / e['last_pivot_px'] - 1
    st = int(out.status.iloc[-1]); dr = int(out.dir.iloc[-1]); ex = int(out['except'].iloc[-1])
    # 兴证"异常状态"：下行段里价格已破前低而 MACD 方向仍向上（或上行段里破前高而方向仍向下）。P2 检验显示这多半是方向滞后，不等于可交易的底/顶背离
    state = ('下行段' if falling else '上行段') + ('·异常态(破前低,MACD未转)' if (falling and dr == 1 and ex == -1) else ('·异常态(破前高,MACD未转)' if ((not falling) and dr == -1 and ex == -1) else ''))
    return dict(asof=str(df.index[-1].date()), state=state, falling=falling, dir=dr, exception=ex, status=st,
                last_pivot=e['last_pivot'], last_pivot_px=round(float(e['last_pivot_px']), 2), leg_return=round(float(leg_ret), 4),
                price_eff=round(float(e['price_eff']), 3), time_eff=round(float(e['time_eff']), 3),
                n_completed_same=int(len(same)), p_leg_extends=(round(float((same.eff > e['price_eff']).mean()), 3) if len(same) else None),
                median_leg_amp=(round(float(same.amp.median()), 4) if len(same) else None), n_pivots=int(len(piv)))


# ---------------------------------------------------------------- 2 LPPLS with cache
def lppls_block(code, df, cache, max_new=60):
    f = cache / f'lppls_{code}.csv'
    hist = pd.read_csv(f, parse_dates=['date']).set_index('date') if f.exists() else pd.DataFrame(columns=['pos', 'neg', 'n'])
    dates = df.index[-max_new:]; todo = [d for d in dates if d not in hist.index]
    t0 = time.time(); close = df.close
    for d in todo:
        s = close[:d].values
        if len(s) < 760: continue
        pos, neg, n, _ = lppls.confidence(s)
        hist.loc[d] = [pos, neg, n]
    hist = hist.sort_index(); hist.index.name = 'date'; hist.reset_index().to_csv(f, index=False)
    h = hist[hist.index <= df.index[-1]]
    dpos = lppls.dissipation(h.pos.astype(float)); dneg = lppls.dissipation(h.neg.astype(float))
    return dict(asof=str(h.index[-1].date()) if len(h) else None, pos=float(h.pos.iloc[-1]) if len(h) else None, neg=float(h.neg.iloc[-1]) if len(h) else None,
                pos_rule=dpos, neg_rule=dneg, computed_days=len(todo), seconds=round(time.time() - t0, 1),
                signal=(1 if dneg['fired'] else 0) - (1 if dpos['fired'] else 0))


# ---------------------------------------------------------------- panel helpers
def read_panel(root, name):
    x = pd.read_parquet(Path(root, 'panel', f'{name}.parquet')); x.index = pd.to_datetime(x.index); return x


def breadth_block(root, idx500, T):
    ret = read_panel(root, 'ret'); close = read_panel(root, 'close'); mem = read_panel(root, 'member_csi500').fillna(0).astype(bool)
    ret, close, mem = ret.loc[:T], close.loc[:T], mem.loc[:T]
    m = mem.reindex_like(ret).fillna(False)
    up = ((ret > 0) & m).sum(axis=1) / m.sum(axis=1).replace(0, np.nan)
    ma60 = close.rolling(60, min_periods=40).mean(); low60 = close.rolling(60, min_periods=40).min()
    above60 = ((close > ma60) & m).sum(axis=1) / m.sum(axis=1).replace(0, np.nan)
    atlow = ((close <= low60 * 1.0001) & m).sum(axis=1) / m.sum(axis=1).replace(0, np.nan)
    # weekly divergence (系列六: index vs 2-month MA of the member up-ratio, rolling 2-month correlation negative and falling)
    wk_up = up.resample('W-FRI').mean().dropna(); wk_up_ma = wk_up.rolling(8, min_periods=6).mean()
    wk_idx = idx500.close.resample('W-FRI').last().reindex(wk_up_ma.index)
    corr = wk_idx.rolling(8, min_periods=6).corr(wk_up_ma)
    idx8 = wk_idx / wk_idx.shift(8) - 1
    div = bool(corr.iloc[-1] < 0 and corr.iloc[-1] < corr.iloc[-2]) if len(corr.dropna()) >= 2 else False
    kind = ('指数跌·宽度不跌（偏多）' if idx8.iloc[-1] < 0 else '指数涨·宽度不跟（偏空）') if div else '无'
    return dict(asof=str(up.index[-1].date()), up_ratio=round(float(up.iloc[-1]), 3), up_ratio_ma20=round(float(up.iloc[-20:].mean()), 3),
                above_ma60=round(float(above60.iloc[-1]), 3), at_60d_low=round(float(atlow.iloc[-1]), 3),
                weekly_corr=round(float(corr.iloc[-1]), 3) if pd.notna(corr.iloc[-1]) else None, divergence=div, divergence_kind=kind,
                signal=(1 if div and idx8.iloc[-1] < 0 else (-1 if div else 0)))


def minmax_last(s, n=20):
    w = s.iloc[-n:]; lo, hi = w.min(), w.max()
    return float((s.iloc[-1] - lo) / (hi - lo)) if hi > lo else 0.5


def tactical_block(root, T, industry_json):
    amt = read_panel(root, 'amount').loc[:T]
    out = {}
    # (1) 成交额活跃度：全 A 成交额 MA20，再做 20 日 Min-Max
    B = amt.sum(axis=1, min_count=100).rolling(20).mean().dropna()
    z = minmax_last(B, 20); out['activity'] = dict(z=round(z, 3), ma20_amount_yi=round(float(B.iloc[-1]) / 1e8, 0), signal=int(np.sign(z - 0.5)))
    # (2) 行业集中度：并集池申万一级成交额前 10 占比，EMA20 后 20 日 Min-Max
    try:
        ind = json.loads(Path(industry_json).read_text(encoding='utf-8'))
        cols = {c: ind.get(c[2:] + '.' + c[:2]) for c in amt.columns}
        g = pd.Series(cols).dropna()
        a = amt[g.index].T.groupby(g).sum().T
        cr10 = a.apply(lambda r: r.nlargest(10).sum(), axis=1) / a.sum(axis=1).replace(0, np.nan)
        e = cr10.ewm(span=20, adjust=False).mean().dropna(); zc = minmax_last(e, 20)
        out['cr10'] = dict(cr10=round(float(cr10.iloc[-1]), 3), z=round(zc, 3), n_industries=int(a.shape[1]), signal=int(np.sign(zc - 0.5)))
    except Exception as ex:
        out['cr10'] = dict(error=f'{type(ex).__name__}: {ex}', signal=0)
    # (3) PB 相对动量：剔 ST、上市不满 250 日；PB 高 20% 组与低 20% 组 20 日收益中位数之差，MA20
    close = read_panel(root, 'close').loc[:T]; pb = read_panel(root, 'fd_v_pb').loc[:T]
    st = read_panel(root, 'is_st').loc[:T].fillna(0).astype(bool); ld = read_panel(root, 'listed_days').loc[:T]
    r20 = close / close.shift(20) - 1
    days = close.index[-25:]; vals = []
    for d in days:
        ok = (~st.loc[d]) & (ld.loc[d] >= 250) & (pb.loc[d] > 0) & r20.loc[d].notna()
        p = pb.loc[d][ok]; r = r20.loc[d][ok]
        if len(p) < 500: vals.append(np.nan); continue
        hi = r[p >= p.quantile(0.8)].median(); lo = r[p <= p.quantile(0.2)].median(); vals.append(hi - lo)
    s = pd.Series(vals, index=days); m20 = s.iloc[-20:].mean()
    out['pb_momentum'] = dict(spread_today=round(float(s.iloc[-1]), 4), ma20=round(float(m20), 4), signal=int(np.sign(m20)))
    out['total'] = int(sum(out[k].get('signal', 0) for k in ('activity', 'cr10', 'pb_momentum')))
    out['asof'] = str(amt.index[-1].date())
    return out


def external_block(path):
    if not path or not Path(path).exists(): return None
    x = pd.read_csv(path, dtype=str).fillna('')
    x = x.sort_values(['as_of', 'published'])
    last = x.groupby('item').tail(1)
    return {r['item']: dict(value=r['value'], as_of=r['as_of'], published=r['published'], source=r['source']) for _, r in last.iterrows()}


# ---------------------------------------------------------------- compose
def compose_lines(D):
    L = ['【择时仪表盘·只读，不参与交易】']
    sw = D.get('swing', {})
    def sdesc(code):
        s = sw.get(code); name = INDICES[code][1]
        if not s or 'error' in s: return f'{name} —'
        p = f"，历史{s['p_leg_extends']:.0%}的同向段走得更远" if s.get('p_leg_extends') is not None else ''
        return f"{name}{s['state']}{s['leg_return']:+.1%}（效率{s['price_eff']:.2f}{p}）"
    if sw: L.append('波段：' + sdesc('000905') + '；' + sdesc('399006') + '；' + '、'.join(f"{INDICES[c][1]}{sw[c]['state']}" for c in ('000300', '000852') if c in sw and 'error' not in sw[c]))
    v3 = (D.get('v3') or {}).get('lines')
    if v3: L.extend(v3)
    lp = D.get('lppls', {}) if not v3 else {}
    if lp:
        def ld(code):
            x = lp.get(code); name = INDICES[code][1]
            if not x or x.get('pos') is None: return f'{name} —'
            tag = '负泡沫消散→看多' if x['neg_rule']['fired'] else ('正泡沫消散→看空' if x['pos_rule']['fired'] else '无信号')
            return f"{name} 正{x['pos']:.2f}/负{x['neg']:.2f}（40日峰 {x['pos_rule']['peak']:.2f}/{x['neg_rule']['peak']:.2f}，{tag}）"
        L.append('LPPLS 泡沫置信度：' + '；'.join(ld(c) for c in LPPLS_CODES if c in lp))
    tb = D.get('tactical') if not v3 else None
    if tb and 'error' not in tb:
        sg = lambda v: {1: '+1', -1: '−1', 0: '0'}[v]
        L.append(f"3.0 战术：成交额活跃度 {sg(tb['activity']['signal'])}（Z={tb['activity']['z']:.2f}）、行业集中度 {sg(tb['cr10'].get('signal', 0))}"
                 + (f"（CR10={tb['cr10']['cr10']:.0%}）" if 'cr10' in tb['cr10'] else '') + f"、PB 动量 {sg(tb['pb_momentum']['signal'])} → 合计 {tb['total']:+d}/3")
    br = D.get('breadth')
    if br and 'error' not in br:
        L.append(f"宽度（中证500）：今日上涨 {br['up_ratio']:.0%}（20日均 {br['up_ratio_ma20']:.0%}），站上60日线 {br['above_ma60']:.0%}，60日新低 {br['at_60d_low']:.0%}；背离：{br['divergence_kind']}")
    ex = D.get('external')
    if ex:
        pick = [k for k in ('兴证不定期择时·中证500', '兴证股债性价比3.0分位', '兴证大小盘', '兴证成长价值') if k in ex]
        if pick: L.append('兴证外部：' + '；'.join(f"{k.replace('兴证', '')} {ex[k]['value']}（{ex[k]['as_of'][5:]}）" for k in pick))
    if D.get('beta_shadow'): L.append(D['beta_shadow'])
    if D.get('errors'): L.append('仪表盘部分模块失败：' + '、'.join(D['errors'].keys()))
    return L


def main():
    args = sys.argv[1:]; T = args[0]
    panel = opt(args, '--panel', default_panel()); cache = Path(opt(args, '--cache', str(HERE / 'cache'))); cache.mkdir(parents=True, exist_ok=True)
    out = Path(opt(args, '--out', str(HERE / 'out'))); out.mkdir(parents=True, exist_ok=True)
    industry = opt(args, '--industry', str(HERE.parent / '实盘日更/config/industry_sw_l1_20260924.json'))
    external = opt(args, '--external', str(HERE / 'xy_external_log.csv')); fetch = '--no-fetch' not in args
    D = dict(day=T, generated_at=dt.datetime.now().isoformat(timespec='seconds'), panel=panel, swing={}, lppls={}, errors={})
    bars = {}
    for code in INDICES:
        try:
            bars[code] = load_index(code, T, cache, panel, fetch)
            D['swing'][code] = swing_block(bars[code])
        except Exception as e:
            D['errors'][f'swing_{code}'] = f'{type(e).__name__}: {e}'
    for code in LPPLS_CODES:
        try:
            if code in bars and bars[code] is not None: D['lppls'][code] = lppls_block(code, bars[code], cache)
        except Exception as e:
            D['errors'][f'lppls_{code}'] = f'{type(e).__name__}: {e}'
    try: D['breadth'] = breadth_block(panel, bars['000905'], T)
    except Exception as e: D['errors']['breadth'] = f'{type(e).__name__}: {e}'; D['breadth'] = None
    try: D['tactical'] = tactical_block(panel, T, industry)
    except Exception as e: D['errors']['tactical'] = f'{type(e).__name__}: {e}'; D['tactical'] = None
    try: D['external'] = external_block(external)
    except Exception as e: D['errors']['external'] = f'{type(e).__name__}: {e}'
    try:  # v3: 兴证 trend / extreme families, ART composite, 3.0 three-dimension position
        import dashboard_v3
        D['v3'] = dashboard_v3.v3_block(T, D, panel, cache, D.get('external'))
        for k, v in D['v3'].get('errors', {}).items(): D['errors'][f'v3_{k}'] = v
    except Exception as e: D['errors']['v3'] = f'{type(e).__name__}: {e}'
    try:  # P3 beta-tilt shadow summary (written by beta_tilt_shadow.py earlier in the pipeline)
        import beta_tilt_shadow
        D['beta_shadow'] = beta_tilt_shadow.summary_line(opt(args, '--shadow-dir', str(HERE / 'out/beta_shadow')))
    except Exception as e: D['errors']['beta_shadow'] = f'{type(e).__name__}: {e}'
    stale = [k for k, v in D['swing'].items() if v.get('asof') != T]
    if D.get('breadth') and D['breadth'].get('asof') != T: stale.append('panel')
    D['stale'] = stale
    D['lines'] = compose_lines(D) + ([f"⚠️ 数据未到 {T}：{'、'.join(stale)}"] if stale else [])
    (out / f'dashboard_{T}.json').write_text(json.dumps(D, ensure_ascii=False, indent=1, default=str), encoding='utf-8')
    (out / f'dashboard_{T}.md').write_text('\n'.join(D['lines']) + '\n\n```json\n' + json.dumps({k: v for k, v in D.items() if k != 'lines'}, ensure_ascii=False, indent=1, default=str) + '\n```\n', encoding='utf-8')
    print('\n'.join(D['lines']))


if __name__ == '__main__':
    main()
