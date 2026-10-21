"""协议外机制诊断：H 的水平与 H 的日变化（驱动调仓的部分）对隔夜跳空 r_gap 的 RankIC。只写本目录。
运行：.pcf-research-venv/bin/python -I outputs/overnight_capture_20261019/posthoc_delta_ic.py"""
from pathlib import Path
import json, sys
import numpy as np, pandas as pd
from scipy.stats import rankdata
HERE = Path(__file__).resolve().parent; ROOT = HERE.parents[1]; sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(HERE))
import overnight_capture as OC
from pcf_overlay_20261012.data import OUT, END
from pcf_overlay_20261012.ensemble_replay import build_signals

prices = pd.read_parquet(OUT / 'daily_prices.parquet'); symbols = json.load(open(OUT / 'price_symbols.json'))
days = [d for d in sorted(set(prices.date)) if '2023-03-01' <= d <= END]; dpos = {d: i for i, d in enumerate(days)}
wide = lambda f: prices.pivot(index='date', columns='symbol', values=f).reindex(index=days, columns=symbols).apply(pd.to_numeric, errors='coerce').to_numpy(float)
o, c = OC.hybrid_prices(days, symbols, wide('open_adjusted'), wide('close_adjusted'))[:2]
H15 = build_signals(OC.SETS, symbols, 15); H14 = build_signals(OC.SETS, symbols, 14); keys = sorted(H15)
def ic(a, b):
    ok = np.isfinite(a) & np.isfinite(b); return np.corrcoef(rankdata(a[ok]), rankdata(b[ok]))[0, 1] if ok.sum() >= 100 else np.nan
rows = []
for k0, k1 in zip(keys[:-1], keys[1:]):
    i = dpos[k1]
    if i + 1 >= len(days): break
    gap = o[i + 1] / c[i] - 1; oo = o[i + 2] / o[i + 1] - 1 if i + 2 < len(days) else np.full(len(symbols), np.nan)
    rows.append(dict(date=k1, H15_level_gap=ic(H15[k1], gap), H15_delta_gap=ic(H15[k1] - H15[k0], gap), H14_level_gap=ic(H14[k1], gap),
                     H14_minus_H15prev_gap=ic(H14[k1] - H15[k0], gap), H15_level_oo=ic(H15[k1], oo), H15_delta_oo=ic(H15[k1] - H15[k0], oo)))
d = pd.DataFrame(rows); d['year'] = d.date.str[:4]
s = d.groupby('year').mean(numeric_only=True); s.loc['all'] = d.mean(numeric_only=True)
t = d.drop(columns=['date', 'year']).apply(lambda x: x.mean() / x.std() * np.sqrt(x.notna().sum())); s.loc['t_all'] = t
s.to_csv(HERE / 'posthoc_delta_ic.csv'); print(s.round(4).T.to_string())

# ---- theoretical ceiling: a pure GRU-driven trade Δw_i = k·w̄·ΔH_i (k=.5, w̄ = .949/holdings≈108, no clip/deadband/cost),
# executed at close T instead of open T+1 earns Σ Δw_i·(gap_i − mean gap). Scaled from the member cross-section.
import json as _j
hold = 108; wbar = .949 / hold; out = []
for k0, k1 in zip(keys[:-1], keys[1:]):
    i = dpos[k1]
    if i + 1 >= len(days): break
    gap = o[i + 1] / c[i] - 1
    for nm, dh in (('H15_delta', H15[k1] - H15[k0]), ('H14_minus_H15prev', H14[k1] - H15[k0])):
        ok = np.isfinite(dh) & np.isfinite(gap)
        if ok.sum() < 100: continue
        g = gap[ok] - gap[ok].mean(); dd = dh[ok] - dh[ok].mean()
        gain = .5 * wbar * hold * float(np.mean(dd * g)); one_side = .5 * wbar * hold * float(np.mean(np.abs(dd))) / 2
        frac_small = float(np.mean(.5 * wbar * np.abs(dd) * 1e7 < 1e4))   # share of names whose Δw is below the 1万 deadband on 1000万
        out.append(dict(date=k1, signal=nm, gain=gain, one_side_turnover=one_side, frac_below_deadband=frac_small))
q = pd.DataFrame(out); r = q.groupby('signal').agg(gain_ann=('gain', lambda x: x.mean() * 252), one_side_turnover_daily=('one_side_turnover', 'mean'), frac_below_deadband=('frac_below_deadband', 'mean'))
r['breakeven_extra_cost_bp_per_side'] = r.gain_ann / 252 / (2 * r.one_side_turnover_daily) * 1e4
r.to_csv(HERE / 'posthoc_pure_delta_ceiling.csv'); print(r.round(5).to_string())
