"""v3 择时信号库：按兴证原文口径复刻（能复刻的照抄，做不到的写明偏离），分"趋势型 / 拐点型"两族（2026-10-17）。
输出 outputs/timing_v3_20261017/signals_v3.parquet（日度，按中证全指 000985 交易日）与 erp.parquet。

趋势型（持续给方向）
  T1 非对称信用扩张（择时的艺术）：月度信用同比较上月上行 +1；下行且连续下行 ≤12 个月 −1；连续下行 >12 个月 0；每月 15 日起可用上月数据
     偏离：原文用中长期贷款余额同比，此处用人民币贷款余额同比（东方财富月度新增倒推，锚定 2026-09 余额 283.44 万亿）
  T2 汇率趋势（宏观预期数据解析，2023-12；2021 周报"与 20 个交易日前比较"）：美元兑人民币低于 20 个交易日前（人民币升值）+1，否则 −1
     偏离：原文离岸 CNH，此处 FRED 在岸 DEXCHUS（可得性），近几日由东方财富/Tushare 补
  T3 通胀预期变动：彭博 ECFC 下季 CPI 预期，无数据 → 不计（0）
  T4 成交额活跃度（3.0）：全 A 成交额 MA20 → 20 日 Min-Max → >0.5 +1，<0.5 −1（数据：c5 daily_source 全 A，2015 起）
  T5 行业集中度 CR10（3.0）：申万一级前 10 成交额占比 → EMA → Min-Max → ±1；偏离：并集池申万一级映射；EMA 跨度/窗口原文未给（取 20/20）
  T6 PB 相对动量（3.0）：剔 ST 与上市不满一年，PB 前 20% 与后 20% 的 20 日收益中位数之差，取 20 日均线（原文图示口径）符号 ±1；PB 来自 daily_basic（2016-09 起）
  T7 波段方向（点位效率系列二）：Status ±1（不属于兴证合成模型，单列）
拐点型（只在极端时给信号）
  E1 改进性价比涨跌周期（择时的艺术）：性价比 2.0 分位 ≥90% 进入上行周期，此后一年内分位 ≥50% 维持 +1；≤10% 进入下行周期，一年内 ≤50% 维持 −1
  E2 LPPLS 负向泡沫消散（择时的艺术 / 3.0 尾部）：近 40 日负泡沫置信度峰值 >0.3 且回落 >0.2 → +1（A 股只用负向）
  E3 地缘政治极端（择时的艺术）：GPR 日度指数 10 年分位 >99% 且绝对值 >200 → −1 并延续，分位 <80% 或绝对值 <100 时解除；按 1 日滞后可得
  E4 拓扑 2 维孔洞（捕捉市场的形状）：申万二级 30 日收益相关网络 H2 ≤1 → +1 持续 4 个交易日（另文件，待申万二级数据）
  X2/X3/X5 异常态、段延伸、宽度背离：我自己加的（非兴证合成模型成员），单列
战略中枢
  Q2 性价比 2.0：DIV_adj = DIV×(1+Δ信用同比(2 个月)×10)，Y_adj = 0.5·中债10Y + 0.5·美债10Y，(DIV_adj − Y_adj) 的滚动 1000 日分位
  Q3 性价比 3.0：美债权重 w = 0.3 + 0.4 × 美元兑人民币滚动 252 日分位，Y_adj = (1−w)·中债 + w·美债
     DIV：全 A 市值加权股息率（daily_basic dv_ttm，2016-09 起日度；2009–2016 月末由云机 Tushare 补，月内按中证全指/沪深300 价格缩放）
"""
import sys, glob, json, warnings
from pathlib import Path
import numpy as np, pandas as pd

warnings.filterwarnings('ignore')
HERE = Path(__file__).resolve().parent; ROOT = HERE.parent; sys.path.insert(0, str(HERE))
import xy_swing  # noqa: E402
OUT = ROOT / 'outputs/timing_v3_20261017'; PUB = OUT / 'public'; P2 = ROOT / 'outputs/timing_p2_20261017'


def idx(code):
    return pd.read_csv(HERE / f'cache/index_{code}.csv', parse_dates=['date']).set_index('date').sort_index()


def rolling_pct(s, n, minp=None):
    return s.rolling(n, min_periods=minp or n // 2).apply(lambda w: (w[:-1] < w[-1]).mean() + 0.5 * (w[:-1] == w[-1]).mean() if len(w) > 1 else np.nan, raw=True)


def minmax(s, n=20):
    lo, hi = s.rolling(n).min(), s.rolling(n).max(); return ((s - lo) / (hi - lo)).where(hi > lo, 0.5)


def credit(days):
    L = pd.read_csv(PUB / 'loans.csv'); L['month'] = pd.PeriodIndex(L.month, freq='M'); L = L.set_index('month').yoy
    out = pd.DataFrame(index=days, columns=['T1', 'credit_yoy', 'credit_d2'], dtype=float)
    pub = {m: (m + 1).to_timestamp() + pd.Timedelta(days=14) for m in L.index}  # 15th of next month
    ms = sorted(L.index)
    for d in days:
        avail = [m for m in ms if pub[m] <= d]
        if len(avail) < 3: continue
        m = avail[-1]; y = L.loc[:m]
        dn = 0
        for k in range(len(y) - 1, 0, -1):
            if y.iloc[k] < y.iloc[k - 1]: dn += 1
            else: break
        s = 1 if y.iloc[-1] > y.iloc[-2] else (-1 if dn <= 12 else 0)
        out.loc[d] = [s, y.iloc[-1], y.iloc[-1] - y.iloc[-3]]
    return out


def fx(days):
    f = pd.read_csv(PUB / 'fred_dexchus.csv', na_values='.', parse_dates=['observation_date']).set_index('observation_date').DEXCHUS.dropna()
    extra = PUB / 'usdcny_recent.csv'
    if extra.exists():
        e = pd.read_csv(extra, parse_dates=['date']).set_index('date').close; f = pd.concat([f[f.index < e.index[0]], e])
    s = f.reindex(days.union(f.index)).ffill().reindex(days)
    T2 = np.where(s < s.shift(20), 1.0, -1.0); T2 = pd.Series(T2, index=days).where(s.shift(20).notna())
    q = rolling_pct(s, 252); return pd.DataFrame(dict(T2=T2, usdcny=s, fx_q252=q))


def dividend_yield(days):
    db = pd.read_parquet(ROOT / 'c5_core/data/A_Layer_Research/fundamental/raw/daily_basic.parquet', columns=['trade_date', 'dv_ttm', 'pe_ttm', 'total_mv'])
    db = db.dropna(subset=['total_mv']); db['dv'] = db.dv_ttm.fillna(0) / 100 * db.total_mv
    g = db.groupby('trade_date'); dy = (g.dv.sum() / g.total_mv.sum()); dy.index = pd.to_datetime(dy.index)
    cloud = OUT / 'dy_agg_cloud.csv'
    if cloud.exists():
        c = pd.read_csv(cloud, dtype={'trade_date': str}); c['d'] = pd.to_datetime(c.trade_date); c = c.set_index('d').dy.sort_index()
        dy = pd.concat([c[c.index < dy.index[0]], dy, c[c.index > dy.index[-1]]]).sort_index()
    dy = dy[~dy.index.duplicated()]
    # fill between sparse (month-end) points by scaling with the all-A proxy price (dividend yield ~ D/P)
    px = pd.concat([idx('000300').close.rename('p')], axis=1).p
    allA = idx('000985').close; px = px.where(px.index < allA.index[0], np.nan).combine_first(allA)
    px = px.reindex(days.union(dy.index)).ffill()
    anchor = dy.reindex(px.index); last_dy = anchor.ffill(); last_px = px.where(anchor.notna()).ffill()
    full = (last_dy * last_px / px).reindex(days)
    return full * 100  # percent


def erp(days, cr):
    y = pd.read_csv(PUB / 'yields.csv', parse_dates=['date']).set_index('date').sort_index()
    y = y.reindex(days.union(y.index)).ffill().reindex(days)
    DIV = dividend_yield(days); fxd = fx(days)
    adj = 1 + cr.credit_d2.astype(float) * 10
    DIVadj = DIV * adj
    E2 = DIVadj - (0.5 * y.cn10y + 0.5 * y.us10y)
    w = 0.3 + 0.4 * fxd.fx_q252
    E3 = DIVadj - ((1 - w) * y.cn10y + w * y.us10y)
    Q2, Q3 = rolling_pct(E2.dropna(), 1000, 500).reindex(days), rolling_pct(E3.dropna(), 1000, 500).reindex(days)
    return pd.DataFrame(dict(DIV=DIV, DIVadj=DIVadj, cn10y=y.cn10y, us10y=y.us10y, w_us=w, ERP2=E2, ERP3=E3, Q2=Q2, Q3=Q3))


def erp_cycle(Q):
    s = pd.Series(0.0, index=Q.index); state, t0 = 0, None
    for i, (d, q) in enumerate(Q.items()):
        if np.isnan(q): continue
        if q >= 0.9: state, t0 = 1, i
        elif q <= 0.1: state, t0 = -1, i
        elif state == 1 and (i - t0 > 252 or q < 0.5): state = 0
        elif state == -1 and (i - t0 > 252 or q > 0.5): state = 0
        s[d] = state
    return s


def lppls_neg(code, days):
    f = P2 / f'lppls_hist_{code}.csv'
    if not f.exists(): return pd.Series(np.nan, index=days)
    h = pd.read_csv(f, parse_dates=['date']).drop_duplicates('date').set_index('date').sort_index()
    h = h.reindex(days.union(h.index)).ffill(limit=5).reindex(days)
    pk = h.neg.rolling(40, min_periods=1).max()
    return ((pk > 0.3) & (pk - h.neg > 0.2)).astype(float).where(h.neg.notna()), h.neg, h.pos


def gpr(days):
    g = pd.read_csv(PUB / 'gpr_daily.csv', parse_dates=['date']).set_index('date').GPRD.dropna().sort_index()
    q = g.rolling(3650, min_periods=1500).apply(lambda w: (w[:-1] < w[-1]).mean(), raw=True)
    s = pd.Series(0.0, index=g.index); on = False
    for d in g.index:
        if not on and q[d] > 0.99 and g[d] > 200: on = True
        elif on and (q[d] < 0.8 or g[d] < 100): on = False
        s[d] = -1.0 if on else 0.0
    s.index = s.index + pd.Timedelta(days=1)  # value of day d is known on d+1
    df = pd.DataFrame(dict(E3=s)).join(pd.DataFrame(dict(gpr=g, gpr_q10y=q)).set_axis(g.index + pd.Timedelta(days=1)), how='outer')
    return df.reindex(days.union(df.index)).ffill().reindex(days)


def tactical(days):
    fs = glob.glob(str(ROOT / 'c5_core/data/A_Layer_Live/daily_source/*.parquet'))
    amt, close, st = {}, {}, {}
    for f in fs:
        x = pd.read_parquet(f, columns=['date', 'amount', 'close', 'prev_close', 'is_st']); x['date'] = pd.to_datetime(x.date); x = x.set_index('date')
        s = Path(f).stem.upper(); amt[s] = x.amount; close[s] = (x.close / x.prev_close).where(x.prev_close > 0).cumprod(); st[s] = x.is_st
    A = pd.DataFrame(amt); C = pd.DataFrame(close); ST = pd.DataFrame(st).fillna(False).astype(bool)
    B = A.sum(axis=1, min_count=1000).rolling(20).mean(); T4 = np.sign(minmax(B, 20) - 0.5)
    ind = json.loads((ROOT / '实盘日更/config/industry_sw_l1_20260924.json').read_text(encoding='utf-8'))
    g = pd.Series({c: ind.get(c[2:] + '.' + c[:2]) for c in A.columns}).dropna()
    a = A[g.index].T.groupby(g).sum().T
    cr10 = a.apply(lambda r: r.nlargest(10).sum(), axis=1) / a.sum(axis=1).replace(0, np.nan)
    T5 = np.sign(minmax(cr10.ewm(span=20, adjust=False).mean(), 20) - 0.5)
    db = pd.read_parquet(ROOT / 'c5_core/data/A_Layer_Research/fundamental/raw/daily_basic.parquet', columns=['code', 'trade_date', 'pb'])
    db['code'] = db.code.astype(str).str.upper().str.replace('.', '', regex=False)
    PB = db.pivot_table(index='trade_date', columns='code', values='pb'); PB.index = pd.to_datetime(PB.index)
    PB.columns = [c if c[:2] in ('SH', 'SZ', 'BJ') else (('SH' if c.startswith('6') else 'SZ') + c[:6]) for c in PB.columns]
    first = C.apply(lambda s: s.first_valid_index())
    r20 = C / C.shift(20) - 1; sp = {}
    for d in PB.index:
        if d not in r20.index: continue
        p = PB.loc[d].dropna(); p = p[p > 0]; cols = [c for c in p.index if c in r20.columns]
        p = p[cols]; r = r20.loc[d, cols]; ok = r.notna() & (~ST.reindex(columns=cols).loc[d].fillna(False)) & ((d - first.reindex(cols)).dt.days >= 365)
        p, r = p[ok], r[ok]
        if len(p) >= 500: sp[d] = r[p >= p.quantile(0.8)].median() - r[p <= p.quantile(0.2)].median()
    sp = pd.Series(sp).sort_index(); T6 = np.sign(sp.rolling(20).mean())
    return pd.DataFrame(dict(T4=T4, T5=T5, T6=T6, act_B=B, cr10=cr10, pb_spread=sp)).reindex(days)


def swing_dir(code, days):
    sh, _ = __import__('p2_signals').swing_history(idx(code)); return sh.reindex(days)


if __name__ == '__main__':
    OUT.mkdir(parents=True, exist_ok=True)
    days = idx('000985').index
    cr = credit(days); fxd = fx(days); er = erp(days, cr); er.to_parquet(OUT / 'erp.parquet')
    E1 = erp_cycle(er.Q2)
    E2, neg, pos = lppls_neg('000985', days); E2_500, _, _ = lppls_neg('000905', days)
    gp = gpr(days); tac = tactical(days)
    sw_all = swing_dir('000985', days); sw500 = pd.read_parquet(P2 / 'signals_000905.parquet').reindex(days)
    br = pd.read_parquet(P2 / 'breadth.parquet').reindex(days)
    S = pd.DataFrame(dict(T1=cr.T1, T2=fxd.T2, T4=tac.T4, T5=tac.T5, T6=tac.T6, T7_allA=sw_all.S1, T7_500=sw500.S1,
                          E1=E1, E2=E2, E2_500=E2_500, E3=gp.E3, X2=sw500.S2, X3=sw500.S3, X5=br.S5,
                          Q2=er.Q2, Q3=er.Q3, lppls_neg=neg, lppls_pos=pos, gpr=gp.gpr, gpr_q=gp.gpr_q10y, credit_yoy=cr.credit_yoy, usdcny=fxd.usdcny))
    tda = OUT / 'tda_sw2_signal.csv'
    if tda.exists(): S['E4'] = pd.read_csv(tda, parse_dates=['date']).set_index('date').E4.reindex(days)
    S.to_parquet(OUT / 'signals_v3.parquet')
    print(S.describe().T[['count', 'mean', 'min', 'max']].round(3).to_string())
    print(S.dropna(how='all').tail(6).T.round(3).to_string())
