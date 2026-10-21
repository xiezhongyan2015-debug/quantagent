"""风格杠杆检验（2026-10-18，用户问：除了 beta，换手、波动率等还能不能做倾斜/择时）。
与 Y2 同口径：每月末按特征把中证 500 成分分五组，下月每日 高组等权 − 低组等权（Q5 − Q1）。
特征（组建日 t 及以前的数据）：
  beta   60 日对中证500 的 beta            turn  20 日平均换手率（iFinD turnoverRatio）
  vol    60 日日收益标准差                  ivol  60 日对指数回归残差标准差（特质波动）
  size   20 日平均流通市值代理 = 成交额/换手率   rev   过去 20 日收益（高减低 > 0 表示短期动量，< 0 表示反转）
  mom    过去 240 日收益跳过最近 20 日（12-1 动量）
检验：
  A 描述：年化收益、波动、相关矩阵、与市场的 beta；对 beta 价差和市场回归后剩下多少（"beta 之外"的部分）
  B 择时：S7/S3/S5（现有），因子自身动量（过去 20/60 日价差收益的符号），市场放量/缩量（成分成交额 20 日 vs 120 日）
     对每个价差及其"beta 之外"部分，用 p2_eval.test（方向统计量、NW t、1000 次循环平移 p、三段）
  C 实盘映射：信号符号 × 权重 0.1，双边万二×2 成本，年化 bp 与分段
输出 outputs/timing_p2_20261017/style/
"""
import sys, glob, json, gzip, warnings
from pathlib import Path
import numpy as np
import pandas as pd

warnings.filterwarnings('ignore')
HERE = Path(__file__).resolve().parent; sys.path.insert(0, str(HERE))
import p2_eval as E  # noqa: E402
import p2_signals as P  # noqa: E402
OUT = E.OUT / 'style'; OUT.mkdir(parents=True, exist_ok=True)
CHARS = ['beta', 'turn', 'vol', 'ivol', 'size', 'rev', 'mom']


def load():
    f = OUT / 'panel.pkl'
    if f.exists(): return pd.read_pickle(f)
    fs = sorted(glob.glob(str(P.IFIND / 'bars/*/*/*.parquet')))
    df = pd.concat([pd.read_parquet(x, columns=['thscode', 'date', 'close', 'amount', 'turnoverRatio']) for x in fs]); df['date'] = pd.to_datetime(df['date'])
    close = df.pivot_table(index='date', columns='thscode', values='close').sort_index()
    amt = df.pivot_table(index='date', columns='thscode', values='amount').reindex(close.index)
    tr = df.pivot_table(index='date', columns='thscode', values='turnoverRatio').reindex(close.index)
    mem = {}
    for g in sorted(glob.glob(str(P.IFIND / 'memberships/*.json.gz'))):
        try: mem[pd.Timestamp(Path(g).name[:10])] = set(json.loads(gzip.open(g).read())['data'][0]['table']['THSCODE'])
        except Exception: pass
    md = sorted(mem); M = pd.DataFrame(False, index=close.index, columns=close.columns)
    for d in close.index:
        k = np.searchsorted(md, d, side='right') - 1
        if k >= 0: M.loc[d, [c for c in close.columns if c in mem[md[k]]]] = True
    out = dict(close=close, amt=amt, tr=tr, M=M); pd.to_pickle(out, f); return out


def spreads(D, idx):
    close, amt, tr, M = D['close'], D['amt'], D['tr'], D['M']
    ret = close.pct_change(); ret[(ret > 0.5) | (ret < -0.5)] = np.nan
    ir = idx.close.pct_change().reindex(ret.index)
    cap = amt / (tr / 100).where(tr > 0)
    me = ret.index.to_series().groupby([ret.index.year, ret.index.month]).max().values
    rows, legs = [], []
    for i in range(12, len(me) - 1):
        t, t1 = pd.Timestamp(me[i]), pd.Timestamp(me[i + 1])
        cols = M.columns[M.loc[t]]
        w = ret.loc[:t].iloc[-60:][cols]; m = ir.loc[:t].iloc[-60:]
        ok = (w.notna().sum() >= 40); w = w.loc[:, ok]; cols = w.columns
        mv = m.var(); cov = w.apply(lambda s: s.cov(m))
        beta = cov / mv; vol = w.std(); ivol = np.sqrt((w.var() - cov ** 2 / mv).clip(lower=0))
        ch = pd.DataFrame(dict(beta=beta, vol=vol, ivol=ivol,
                               turn=tr.loc[:t].iloc[-20:][cols].mean(), size=np.log(cap.loc[:t].iloc[-20:][cols].mean()),
                               rev=close.loc[:t].iloc[-1][cols] / close.loc[:t].iloc[-21][cols] - 1,
                               mom=close.loc[:t].iloc[-21][cols] / close.loc[:t].iloc[-241][cols] - 1))
        nxt = ret.loc[(ret.index > t) & (ret.index <= t1)]
        row = {}
        for c in CHARS:
            x = ch[c].dropna()
            if len(x) < 100: continue
            q = pd.qcut(x.rank(method='first'), 5, labels=False); hi, lo = q[q == 4].index, q[q == 0].index
            row[c] = nxt[hi].mean(axis=1) - nxt[lo].mean(axis=1)
            legs.append(dict(month=str(t1.date())[:7], char=c, d_beta=float(beta.reindex(hi).mean() - beta.reindex(lo).mean())))
        rows.append(pd.DataFrame(row))
    S = pd.concat(rows); S['rm'] = ir.reindex(S.index)
    return S, pd.DataFrame(legs)


def ortho(S):
    """part of each spread not explained by the beta spread and the market (full-sample OLS, for description and as a target)"""
    X = np.column_stack([np.ones(len(S)), S.beta.values, S.rm.values]); out = {}
    for c in CHARS:
        if c == 'beta': continue
        y = S[c].values; ok = np.isfinite(y) & np.isfinite(X).all(1)
        b = np.linalg.lstsq(X[ok], y[ok], rcond=None)[0]; out[c] = pd.Series(y - X[:, 1:] @ b[1:], index=S.index)   # keep the intercept (mean) in the residual
    return pd.DataFrame(out)


def activity(D, index):
    amt = (D['amt'] * D['M']).sum(axis=1).replace(0, np.nan)
    z = np.log(amt.rolling(20).mean() / amt.rolling(120).mean())
    s = pd.Series(0.0, index=amt.index); s[z > 0.10] = 1; s[z < -0.10] = -1
    return s.reindex(index).fillna(0), z


def main():
    idx = pd.read_parquet(E.OUT / 'index_000905.parquet')
    f = OUT / 'spreads.parquet'
    if f.exists() and '--rebuild' not in sys.argv: S = pd.read_parquet(f); legs = pd.read_csv(OUT / 'legs.csv')
    else:
        D = load(); S, legs = spreads(D, idx); S.to_parquet(f); legs.to_csv(OUT / 'legs.csv', index=False)
        act, z = activity(D, idx.index); act.to_frame('act').assign(z=z.reindex(idx.index)).to_parquet(OUT / 'activity.parquet')
    act = pd.read_parquet(OUT / 'activity.parquet').act
    R = ortho(S)
    # ---- A description
    ann = lambda x: x.mean() * 252 * 1e4
    desc = pd.DataFrame({c: dict(ann_bp=ann(S[c]), vol_pct=S[c].std() * np.sqrt(252) * 100, corr_beta=S[c].corr(S.beta), corr_mkt=S[c].corr(S.rm),
                                 beta_to_mkt=S[c].cov(S.rm) / S.rm.var(), d_beta_legs=legs[legs.char == c].d_beta.mean(),
                                 resid_share_var=(R[c].var() / S[c].var()) if c in R else np.nan, resid_ann_bp=ann(R[c]) if c in R else np.nan,
                                 **{f'ann_{y}': ann(S[c][str(y)]) for y in (2018, 2019, 2022, 2024, 2025, 2026)}) for c in CHARS}).T
    desc.to_csv(OUT / 'describe.csv')
    C = S[CHARS].corr(); C.to_csv(OUT / 'corr_daily.csv')
    Cm = S[CHARS].resample('ME').sum().corr(); Cm.to_csv(OUT / 'corr_monthly.csv')
    # ---- B timing tests
    s = pd.read_parquet(E.OUT / 'signals_000905.parquet'); s['S4'] = E.s4_series('000905', s.index)
    s['S5'] = pd.read_parquet(E.OUT / 'breadth.parquet').S5.reindex(s.index)
    s['S7'] = np.sign(s[['S2', 'S3', 'S4', 'S5']].sum(axis=1, min_count=4)); s['ACT'] = act.reindex(s.index).fillna(0)
    rows, lev = [], []
    for c in CHARS:
        tg = {c: S[c]}
        if c in R: tg[c + '_beta之外'] = R[c]
        for tn, y in tg.items():
            yy = y.reindex(s.index)
            sig = dict(S7=s.S7, S3=s.S3, S5=s.S5, ACT=s.ACT,
                       FM20=np.sign(yy.rolling(20, min_periods=15).sum()).fillna(0), FM60=np.sign(yy.rolling(60, min_periods=45).sum()).fillna(0))
            for h in (20, 35):
                fy = E.fwd_daily(y, s.index, h)
                for k, v in sig.items(): rows.append(E.test(k, v, fy, h, tn))
            for k, v in sig.items():   # lever: sign held as is (S7 also as 35-day hold), weight 0.1, cost 2bp x2 per unit turnover x2 legs
                for hold in ([None, 35] if k in ('S7', 'S3', 'S5') else [None]):
                    p = v.where(v != 0).ffill(limit=hold - 1).fillna(0) if hold else v
                    pos = 0.1 * np.sign(p); inc = pos * y.reindex(s.index).shift(-2); turn = pos.diff().abs() * 2
                    inc = (inc - turn.fillna(0) * 0.0004).dropna()
                    if len(inc) < 250: continue
                    lev.append(dict(target=tn, signal=k + (f'_hold{hold}' if hold else ''), ann_bp=ann(inc), active=float((pos != 0).mean()),
                                    **{f'{a}_{b}': ann(inc[a:b]) for a, b in E.PERIODS}, switches_per_year=float((pos.diff().abs() > 0).sum() / (len(pos) / 252))))
    T = pd.DataFrame(rows); T.to_csv(OUT / 'tests.csv', index=False); L = pd.DataFrame(lev); L.to_csv(OUT / 'levers.csv', index=False)
    pd.set_option('display.width', 260); pd.set_option('display.max_rows', 500)
    print(desc.round(2).to_string()); print(C.round(2).to_string()); print(Cm.round(2).to_string())
    k = T[T.h == 35][['target', 'signal', 'n_nonzero', 'stat', 'nw_t', 'p_shuffle', 'periods_positive', 'periods_tested']]
    print(k.round(4).to_string(index=False))
    print(L.round(1).to_string(index=False))
    m = T.dropna(subset=['p_shuffle']); print('tests', len(m), 'p<0.05', int((m.p_shuffle < 0.05).sum()), 'expected', round(0.05 * len(m), 1), 'p<0.01', int((m.p_shuffle < 0.01).sum()))


if __name__ == '__main__':
    main()
