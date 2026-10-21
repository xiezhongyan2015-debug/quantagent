"""Did high-beta or low-beta stocks fall more in the latest decline?  All-A panel (c5_core, to 2026-10-09) + self-built CNE5
exposures (BETA as of the window start, 252-day half-life-63 beta vs the market, standardised) and the CNE5 pure BETA factor
return.  Windows: 2026-06-30 peak -> 10-09 (full decline), 07-01 -> 07-31 (crash month), 08-03 -> 09-17 (rebound + drift), 09-18 -> 10-09 (competition).
Output: outputs/beta_drawdown_20261018/*.csv"""
import numpy as np, pandas as pd
P = 'c5_core/data/A_Layer_Live/panel'; OUT = 'outputs/beta_drawdown_20261018'
close = pd.read_parquet(f'{P}/close.parquet'); elig = pd.read_parquet(f'{P}/eligible.parquet'); st = pd.read_parquet(f'{P}/is_st.parquet'); ld = pd.read_parquet(f'{P}/listed_days.parquet')
m500 = pd.read_parquet(f'{P}/member_csi500.parquet'); m1000 = pd.read_parquet(f'{P}/member_csi1000.parquet'); m300 = pd.read_parquet(f'{P}/member_csi300.parquet'); cap = pd.read_parquet(f'{P}/float_market_cap.parquet')
ex = pd.read_parquet('outputs/barra_cne5_local/exposures.parquet', columns=['date', 'symbol', 'BETA', 'SIZE', 'RESVOL', 'MOMENTUM', 'LIQUIDITY', 'in_universe']); ex['symbol'] = ex.symbol.str[-2:] + ex.symbol.str[:6]
fr = pd.read_parquet('outputs/barra_cne5_local/factor_returns.parquet')
WINDOWS = {'06-30 高点 → 10-09': ('2026-06-30', '2026-10-09'), '07 月崩跌': ('2026-06-30', '2026-07-31'), '08-03 → 09-17 反弹与阴跌': ('2026-07-31', '2026-09-17'), '09-18 → 10-09 比赛期': ('2026-09-17', '2026-10-09')}
rows = []; quint = []
for name, (a, b) in WINDOWS.items():
    a, b = pd.Timestamp(a), pd.Timestamp(b)
    r = close.loc[b] / close.loc[a] - 1
    e = ex[ex.date == a].set_index('symbol'); ok = (elig.loc[a] == 1) & (st.loc[a] != 1) & (ld.loc[a] > 120) & r.notna()
    u = pd.DataFrame({'ret': r, 'beta': e.BETA.reindex(r.index), 'size': e.SIZE.reindex(r.index), 'resvol': e.RESVOL.reindex(r.index), 'mom': e.MOMENTUM.reindex(r.index), 'liq': e.LIQUIDITY.reindex(r.index), 'cap': cap.loc[a], 'm500': m500.loc[a] == 1, 'm1000': m1000.loc[a] == 1, 'm300': m300.loc[a] == 1})[ok].dropna(subset=['beta'])
    pools = {'全 A（可交易、非 ST、上市>120 日）': u, '沪深 300': u[u.m300], '中证 500': u[u.m500], '中证 1000': u[u.m1000]}
    for pool, d in pools.items():
        q = pd.qcut(d.beta.rank(method='first'), 5, labels=['Q1 最低 beta', 'Q2', 'Q3', 'Q4', 'Q5 最高 beta'])
        g = d.groupby(q, observed=True).ret.agg(['mean', 'median', 'size']); g['equal_weight_all'] = d.ret.mean(); g['cap_weight_all'] = np.average(d.ret, weights=d.cap)
        for k, v in g.iterrows(): quint.append(dict(window=name, pool=pool, quintile=k, mean_ret=v['mean'], median_ret=v['median'], n=int(v['size'])))
        sp = d[['ret', 'beta', 'size', 'resvol', 'mom', 'liq']].corr(method='spearman').ret
        # beta effect controlling for size, resvol, momentum, liquidity (cross-sectional OLS, standardised exposures)
        dd = d.dropna(subset=['size', 'resvol', 'mom', 'liq']); X = np.column_stack([np.ones(len(dd)), dd.beta, dd['size'], dd.resvol, dd.mom, dd.liq]); b_ = np.linalg.lstsq(X, dd.ret.to_numpy(), rcond=None)[0]
        rows.append(dict(window=name, pool=pool, n=len(d), ew_ret=d.ret.mean(), q5_minus_q1=g.loc['Q5 最高 beta', 'mean'] - g.loc['Q1 最低 beta', 'mean'], spearman_beta=sp['beta'], spearman_size=sp['size'], spearman_resvol=sp['resvol'], spearman_mom=sp['mom'], spearman_liq=sp['liq'], ols_beta_per_sigma=b_[1], ols_size_per_sigma=b_[2], ols_resvol_per_sigma=b_[3]))
    # pure factor return (CNE5 constrained WLS, industry- and other-style-controlled)
    f = fr[(fr.index > a) & (fr.index <= b)]
    rows.append(dict(window=name, pool='CNE5 纯因子（控制行业与其他风格）', n=len(f), ew_ret=np.nan, q5_minus_q1=np.nan, spearman_beta=np.nan, cne5_BETA_cum=float(np.expm1(np.log1p(f.BETA).sum())), cne5_SIZE_cum=float(np.expm1(np.log1p(f.SIZE).sum())), cne5_RESVOL_cum=float(np.expm1(np.log1p(f.RESVOL).sum())), cne5_LIQUIDITY_cum=float(np.expm1(np.log1p(f.LIQUIDITY).sum())), cne5_MOMENTUM_cum=float(np.expm1(np.log1p(f.MOMENTUM).sum()))))
Q = pd.DataFrame(quint); R = pd.DataFrame(rows); Q.to_csv(f'{OUT}/beta_quintiles.csv', index=False); R.to_csv(f'{OUT}/beta_summary.csv', index=False)
pd.set_option('display.width', 250); pd.set_option('display.max_rows', 300)
print(Q.pivot_table(index=['window', 'pool'], columns='quintile', values='mean_ret', observed=True).round(4).to_string())
print(); print(R.round(4).to_string())
# what is BETA correlated with right now (why high beta might fall more / less)
e = ex[ex.date == pd.Timestamp('2026-09-17')]; print('\n09-17 截面：BETA 与其他风格的相关', e[['BETA', 'SIZE', 'RESVOL', 'MOMENTUM', 'LIQUIDITY']].corr().BETA.round(2).to_dict())
