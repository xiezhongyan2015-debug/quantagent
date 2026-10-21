"""P2：按 reports/P2_择时信号检验协议_2026-10-17.md 构造信号与目标的历史序列（S1–S3、S5、S6，Y1–Y3；S4 见 p2_lppls_backfill.py）。
全部因果：t 日信号只用 t 日收盘及以前的数据；波段端点按"确认日"（Status 翻转日）而非极值日进入信息集。
输出 outputs/timing_p2_20261017/：signals_<code>.parquet、index_<code>.parquet、y2_beta_spread.parquet、y3_overlay_excess.parquet、breadth.parquet
"""
import sys, json, gzip, glob, time
from pathlib import Path
import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent; ROOT = HERE.parent; sys.path.insert(0, str(HERE))
import xy_swing  # noqa: E402
OUT = ROOT / 'outputs/timing_p2_20261017'
CODES = ['000905', '000300', '000852', '399006']
IFIND = ROOT / '中证500_历年成分股_日频_2010-2026_iFinD_后复权'
PANEL = ROOT / 'c5_core/data/A_Layer_Live/panel'


def load_index(code):
    return pd.read_csv(HERE / f'cache/index_{code}.csv', parse_dates=['date']).set_index('date').sort_index()


# ------------------------------------------------------------------ S1–S3
def swing_history(df, min_legs=20):
    out, piv, _ = xy_swing.swing(df)
    n = len(df); c = df.close.values
    S1 = out.status.values.astype(float); S1[S1 == 0] = np.nan
    S2 = np.zeros(n)
    S2[(out.status.values == -1) & (out.dir.values == 1) & (out['except'].values == -1)] = 1
    S2[(out.status.values == 1) & (out.dir.values == -1) & (out['except'].values == -1)] = -1
    S3 = np.zeros(n); p_ext = np.full(n, np.nan); eff_now = np.full(n, np.nan)
    pv = piv.sort_values('confirm_i').reset_index(drop=True)
    # completed-leg efficiencies, indexed by the confirmation bar of the leg's end pivot
    legs = []
    for k in range(2, len(pv)):
        a, b, e = pv.iloc[k - 2], pv.iloc[k - 1], pv.iloc[k]
        legs.append((int(e.confirm_i), e.kind, abs(e.price - b.price) / abs(b.price - a.price)))
    legs = pd.DataFrame(legs, columns=['confirm_i', 'kind', 'eff'])
    ci = pv.confirm_i.values
    for t in range(n):
        m = np.searchsorted(ci, t, side='right')  # pivots confirmed on or before t
        if m < 2: continue
        a, b = pv.iloc[m - 2], pv.iloc[m - 1]
        eff = abs(c[t] - b.price) / abs(b.price - a.price); eff_now[t] = eff
        falling = b.kind == 'H'
        L = legs[(legs.confirm_i <= t) & (legs.kind == ('L' if falling else 'H'))]
        if len(L) < min_legs: continue
        p = float((L.eff > eff).mean()); p_ext[t] = p
        if p < 0.30: S3[t] = 1 if falling else -1
    return pd.DataFrame(dict(S1=S1, S2=S2, S3=S3, p_extend=p_ext, eff=eff_now, status=out.status.values), index=df.index), piv


# ------------------------------------------------------------------ iFinD constituents (breadth, Y2)
def load_ifind():
    fs = sorted(glob.glob(str(IFIND / 'bars/*/*/*.parquet')))
    df = pd.concat([pd.read_parquet(f, columns=['thscode', 'date', 'close', 'amount']) for f in fs])
    df['date'] = pd.to_datetime(df['date'])
    close = df.pivot_table(index='date', columns='thscode', values='close').sort_index()
    mem_files = sorted(glob.glob(str(IFIND / 'memberships/*.json.gz')))
    mem = {}
    for f in mem_files:
        d = pd.Timestamp(Path(f).name[:10])
        try:
            m = json.loads(gzip.open(f).read()); mem[d] = set(m['data'][0]['table']['THSCODE'])
        except Exception:
            continue
    dates = close.index
    M = pd.DataFrame(False, index=dates, columns=close.columns)
    md = sorted(mem)
    for d in dates:
        k = np.searchsorted(md, d, side='right') - 1
        if k < 0: continue
        s = mem[md[k]]; cols = [c for c in close.columns if c in s]
        M.loc[d, cols] = True
    return close, M


def breadth_history(close, M, idx):
    ret = close.pct_change(); ret[(ret > 0.5) | (ret < -0.5)] = np.nan
    up = ((ret > 0) & M).sum(axis=1) / M.sum(axis=1).replace(0, np.nan)
    wk_up = up.resample('W-FRI').mean().dropna(); wk_up_ma = wk_up.rolling(8, min_periods=6).mean()
    wk_idx = idx.close.resample('W-FRI').last().reindex(wk_up_ma.index)
    corr = wk_idx.rolling(8, min_periods=6).corr(wk_up_ma); idx8 = wk_idx / wk_idx.shift(8) - 1
    div = (corr < 0) & (corr < corr.shift(1))
    s_w = pd.Series(0.0, index=corr.index); s_w[div & (idx8 < 0)] = 1; s_w[div & (idx8 >= 0)] = -1
    # weekly value known at Friday close; hold through the following week (forward-fill to daily)
    S5 = s_w.reindex(up.index, method='ffill').fillna(0)
    return pd.DataFrame(dict(up_ratio=up, S5=S5))


def beta_spread(close, M, idx):
    ret = close.pct_change(); ret[(ret > 0.5) | (ret < -0.5)] = np.nan
    ir = idx.close.pct_change().reindex(ret.index)
    me = ret.index.to_series().groupby([ret.index.year, ret.index.month]).max().values
    out = []
    for i in range(3, len(me) - 1):
        t, t1 = pd.Timestamp(me[i]), pd.Timestamp(me[i + 1])
        cols = M.columns[M.loc[t]]
        w = ret.loc[:t].iloc[-60:][cols]; m = ir.loc[:t].iloc[-60:]
        ok = w.notna().sum() >= 40; w = w.loc[:, ok]
        beta = w.apply(lambda s: s.cov(m) / m.var())
        q = pd.qcut(beta.rank(method='first'), 5, labels=False)
        hi, lo = q[q == 4].index, q[q == 0].index
        nxt = ret.loc[(ret.index > t) & (ret.index <= t1)]
        out.append(nxt[hi].mean(axis=1) - nxt[lo].mean(axis=1))
    return pd.concat(out).rename('y2')


# ------------------------------------------------------------------ S6 (c5 panel, 2022-06+) and long activity proxy
def rp(name):
    x = pd.read_parquet(PANEL / f'{name}.parquet'); x.index = pd.to_datetime(x.index); return x


def rolling_minmax(s, n=20):
    lo, hi = s.rolling(n).min(), s.rolling(n).max()
    return ((s - lo) / (hi - lo)).where(hi > lo, 0.5)


def tactical_history():
    amt = rp('amount')
    B = amt.sum(axis=1, min_count=100).rolling(20).mean(); z = rolling_minmax(B, 20)
    S6a = np.sign(z - 0.5)
    ind = json.loads((ROOT / '实盘日更/config/industry_sw_l1_20260924.json').read_text(encoding='utf-8'))
    g = pd.Series({c: ind.get(c[2:] + '.' + c[:2]) for c in amt.columns}).dropna()
    a = amt[g.index].T.groupby(g).sum().T
    cr10 = a.apply(lambda r: r.nlargest(10).sum(), axis=1) / a.sum(axis=1).replace(0, np.nan)
    e = cr10.ewm(span=20, adjust=False).mean(); zc = rolling_minmax(e, 20); S6b = np.sign(zc - 0.5)
    close = rp('close'); pb = rp('fd_v_pb'); st = rp('is_st').fillna(0).astype(bool); ld = rp('listed_days')
    r20 = close / close.shift(20) - 1; sp = []
    for d in close.index:
        ok = (~st.loc[d]) & (ld.loc[d] >= 250) & (pb.loc[d] > 0) & r20.loc[d].notna()
        p = pb.loc[d][ok]; r = r20.loc[d][ok]
        sp.append(r[p >= p.quantile(0.8)].median() - r[p <= p.quantile(0.2)].median() if len(p) >= 500 else np.nan)
    sp = pd.Series(sp, index=close.index); S6c = np.sign(sp.rolling(20).mean())
    df = pd.DataFrame(dict(S6a=S6a, S6b=S6b, S6c=S6c, act_z=z, cr10=cr10, pb_spread=sp))
    df['S6'] = df[['S6a', 'S6b', 'S6c']].sum(axis=1, min_count=3)
    return df


def activity_long():
    x = pd.read_parquet(ROOT / 'c5_core/data/A_Layer_Live/index_daily.parquet'); x['date'] = pd.to_datetime(x.date)
    a = x.pivot_table(index='date', columns='index_code', values='amount')[['000300', '000905', '000852']].sum(axis=1, min_count=3)
    B = a.rolling(20).mean(); z = rolling_minmax(B, 20)
    return pd.DataFrame(dict(S6a_long=np.sign(z - 0.5), act_z_long=z))


def overlay_excess():
    B = ROOT / 'outputs/pcf_overlay_20261012/optimizer_overlay_v4'
    P0 = pd.read_csv(B / 'P0_daily.csv', parse_dates=['date']).set_index('date'); L = pd.read_csv(B / 'V5_P1blend_daily.csv', parse_dates=['date']).set_index('date')
    return (L.nav.pct_change() - P0.nav.pct_change()).dropna().rename('y3')


if __name__ == '__main__':
    OUT.mkdir(parents=True, exist_ok=True); t0 = time.time()
    idx = {c: load_index(c) for c in CODES}
    for c in CODES:
        sh, piv = swing_history(idx[c]); sh.to_parquet(OUT / f'signals_{c}.parquet'); idx[c].to_parquet(OUT / f'index_{c}.parquet')
        piv.to_csv(OUT / f'pivots_{c}.csv', index=False)
        print(c, 'swing', len(sh), 'pivots', len(piv), 'S2 nonzero', int((sh.S2 != 0).sum()), 'S3 nonzero', int((sh.S3 != 0).sum()), round(time.time() - t0), 's', flush=True)
    overlay_excess().to_frame().to_parquet(OUT / 'y3_overlay_excess.parquet')
    tac = tactical_history().join(activity_long(), how='outer'); tac.to_parquet(OUT / 'tactical.parquet'); print('tactical', tac.dropna(subset=['S6']).shape, round(time.time() - t0), 's', flush=True)
    close, M = load_ifind(); print('ifind', close.shape, int(M.sum(axis=1).median()), round(time.time() - t0), 's', flush=True)
    br = breadth_history(close, M, idx['000905']); br.to_parquet(OUT / 'breadth.parquet'); print('breadth S5 nonzero days', int((br.S5 != 0).sum()), flush=True)
    y2 = beta_spread(close, M, idx['000905']); y2.to_frame().to_parquet(OUT / 'y2_beta_spread.parquet'); print('y2', len(y2), round(time.time() - t0), 's', flush=True)
