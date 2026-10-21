"""Style attribution of the competition constructions with the self-built CNE5 model (2026-10-15).

For each arm the daily TARGET weights are rebuilt from the frozen inputs exactly as in v4 / wide-pool v1
(skeleton arms are deterministic: no optimizer), then the active return of arm X versus its skeleton Y on
session t+1 is decomposed with exposures at t:
    r_X - r_Y  =  sum_k (a_t' X_k) f_k,t+1  +  a_t' e_t+1          a_t = w_X - w_Y
Reported per pair: annualised contribution of each style, of industries (sum), country, specific, plus the
ex-ante total vs the realised NAV difference (gap = execution, fees, weights drift, missing exposures).
Pairs: V5-P0, P1_blend-P0, V5_P1blend-P0 (PCF skeleton); WV0-I0, I1_blend-I0 (wide pool); I0-P0 (skeleton vs skeleton).
Average active exposures (and their t-stats over days) are reported too: that is the direct answer to
"is the increment a known style bet".
"""
from pathlib import Path
import json, time
import numpy as np
import pandas as pd
from scipy.special import ndtri
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pcf_overlay_20261012.data import ROOT, OUT, END, symbol
from pcf_overlay_20261012 import replay as R
from pcf_overlay_20261012.optimizer_overlay_v4 import C5_PATH
from pcf_overlay_20261012 import wide_pool_optimizer_v1 as V1

B = ROOT/'outputs/barra_cne5_local'; DEST = B/'attribution'; DEST.mkdir(exist_ok=True)
STYLES = ['SIZE', 'BETA', 'MOMENTUM', 'RESVOL', 'NLSIZE', 'BTOP', 'LIQUIDITY', 'EARNYLD', 'GROWTH', 'LEVERAGE']
V4 = OUT/'optimizer_overlay_v4'


def main():
    t0 = time.time()
    pc = pd.read_parquet(OUT/'pcf_components.parquet'); meta = pd.read_csv(OUT/'pcf_metadata.csv', dtype={'date': str, 'valuation_date': str}).set_index('date')
    prices = pd.read_parquet(OUT/'daily_prices.parquet'); symbols = json.load(open(OUT/'price_symbols.json')); U = len(symbols); lookup = {s: i for i, s in enumerate(symbols)}
    all_days = sorted(set(prices.date)); all_days = [d for d in all_days if '2023-03-01' <= d <= END]; daypos = {d: i for i, d in enumerate(all_days)}
    rawclose = prices.pivot(index='date', columns='symbol', values='close').reindex(index=all_days, columns=symbols).to_numpy(float)
    DATA = ROOT/'gru_research/artifacts/dataset'; times = np.load(DATA/'times.npy'); clock = pd.to_datetime(times, utc=True).tz_convert('Asia/Shanghai')
    q = np.load(ROOT/'outputs/gru_beta_neutral_20261008/risk_context.npz'); model_symbols = json.load(open(DATA/'symbols.json'))
    mapping = np.array([lookup[s] for s in model_symbols]); by15 = {str(clock[i].date()): i for i in np.flatnonzero(clock.hour == 15)}
    frozen_member = np.load(DATA/'members.npy', mmap_mode='r')
    s15, _ = R.signals(15, all_days, symbols)
    c5 = pd.read_parquet(C5_PATH); c5 = c5[c5.segment != 'live']; s_c5 = {}; veto_c5 = {}
    for day, g in c5.groupby('date'):
        z = np.full(U, np.nan); v = np.zeros(U, bool); gg = g[g.code.isin(lookup.keys())]
        z[[lookup[c] for c in gg.code]] = ndtri(np.clip(gg.score_pct.to_numpy(), 1e-4, 1 - 1e-4))
        bad = gg[(gg.state == 'vetoed') | ((gg.state == 'excluded') & np.isfinite(gg.raw_score.to_numpy(dtype=float)))]; v[[lookup[c] for c in bad.code]] = True
        s_c5[day] = z; veto_c5[day] = v
    def blend(day):
        a = s15.get(day); b = s_c5.get(day)
        if a is None: return b
        if b is None: return a
        return np.where(np.isfinite(a) & np.isfinite(b), (a + b) / 2, np.where(np.isfinite(a), a, b))
    fc = pd.read_parquet(V1.FREECAP); fc.index = pd.to_datetime(fc.index).strftime('%Y-%m-%d'); fc.columns = [c[2:] + '.' + c[:2] for c in fc.columns]; fc = fc.reindex(columns=symbols)
    mult = pd.read_parquet(V1.MULT); mult.index = pd.to_datetime(mult.index).strftime('%Y-%m-%d'); mult.columns = [c[2:] + '.' + c[:2] for c in mult.columns]; mult = mult.reindex(columns=symbols)
    logcap_by_day = {}
    dates = pd.read_csv(V4/'P0_daily.csv').date.tolist()
    pcgroups = {d: g[g.stock_replicable] for d, g in pc.groupby('date')}
    W = {k: [] for k in ['P0', 'V5', 'P1_blend', 'V5_P1blend', 'I0', 'WV0', 'I1_blend']}; last_c5 = None; used = []
    for day in dates:
        j = daypos[day]; prior = all_days[j - 1]
        if prior in s_c5: last_c5 = prior
        else: s_c5[prior] = s_c5[last_c5]; veto_c5[prior] = veto_c5[last_c5]
        z = blend(prior); veto = veto_c5[prior]
        # PCF reference (v4)
        g = pcgroups[prior]; m = meta.loc[prior]; ids = np.array([lookup[symbol(c)] for c in g.code]); prev = rawclose[daypos[m.valuation_date], ids]
        ref = np.zeros(U); ref[ids] = g.quantity.to_numpy() * prev / float(m.nav_per_cu)
        if ref.sum() > .98: ref *= .98 / ref.sum()
        v5 = ref.copy(); v5[veto] = 0.; v5 = v5 / v5.sum() * ref.sum()
        W['P0'].append(ref); W['V5'].append(v5); W['P1_blend'].append(R.target_weights(ref, z, .5)); W['V5_P1blend'].append(R.target_weights(v5, z, .5))
        # wide-pool reference (v1)
        i15 = by15[prior]; mem = np.zeros(U, bool); mem[mapping] = frozen_member[i15]
        f = fc.loc[prior].to_numpy(float) if prior in fc.index else np.full(U, np.nan); gm = mult.loc[prior].to_numpy(float) if prior in mult.index else np.full(U, np.nan); gm = np.where(np.isfinite(gm), gm, 1.)
        w = np.where(mem & np.isfinite(f) & (f > 0), f * gm, np.nan); have = mem & np.isfinite(w); miss = mem & ~np.isfinite(w)
        lc = np.full(U, np.nan); lc[mapping] = q['log_cap'][i15]; cap = np.exp(np.nan_to_num(lc, nan=-np.inf))
        if miss.any():
            ok = have & np.isfinite(cap) & (cap > 0); ratio = np.median(w[ok] / cap[ok]) if ok.sum() > 10 else 1.
            fillv = cap[miss] * ratio; w[miss] = np.where(np.isfinite(fillv) & (fillv > 0), fillv, np.nan)
        w = np.where(mem & np.isfinite(w), w, 0.); i0 = w / w.sum() * V1.INVESTED
        wv0 = i0.copy(); wv0[veto & mem] = 0.; wv0 = wv0 / wv0.sum() * i0.sum()
        W['I0'].append(i0); W['WV0'].append(wv0); W['I1_blend'].append(R.target_weights(i0, z, .5)); used.append((day, prior))
    W = {k: np.asarray(v) for k, v in W.items()}
    aud = pd.read_csv(V1.DEST/'benchmark_proxy_audit.csv'); assert np.allclose(aud.max_weight.to_numpy(), W['I0'].max(axis=1)), 'I0 weights differ from v1'
    print('weights rebuilt', round(time.time() - t0), 's', flush=True)
    # ---- CNE5 model
    E = pd.read_parquet(B/'exposures.parquet'); E['date'] = E.date.dt.strftime('%Y-%m-%d'); E = E[E.symbol.isin(lookup)]
    FR = pd.read_parquet(B/'factor_returns.parquet'); FR.index = FR.index.strftime('%Y-%m-%d')
    SR = pd.read_parquet(B/'specific_returns.parquet'); SR.index = SR.index.strftime('%Y-%m-%d'); SR = SR.reindex(columns=symbols)
    inds = [c for c in FR.columns if c not in STYLES + ['COUNTRY', 'N', 'R2']]
    Eg = {d: g for d, g in E.groupby('date')}
    nav = {a: pd.read_csv(V4/f'{a}_daily.csv').nav.to_numpy() for a in ['P0', 'V5', 'P1_blend', 'V5_P1blend']}
    nav.update({a: pd.read_csv(V1.DEST/f'{a}_daily.csv').nav.to_numpy() for a in ['I0', 'WV0', 'I1_blend']})
    pairs = [('V5', 'P0'), ('P1_blend', 'P0'), ('V5_P1blend', 'P0'), ('WV0', 'I0'), ('I1_blend', 'I0'), ('I0', 'P0'), ('V5_P1blend', 'V5'), ('WV0', 'V5')]
    rows = []; expo_rows = []
    for k, (day, prior) in enumerate(used):
        if prior not in Eg or day not in FR.index: continue
        g = Eg[prior]; idx = np.array([lookup[s] for s in g.symbol]); X = np.full((U, len(STYLES)), np.nan); X[idx] = g[STYLES].to_numpy(float)
        D = np.zeros((U, len(inds))); gi = {n: i for i, n in enumerate(inds)}; ii = np.array([gi.get(s, -1) for s in g.industry])
        D[idx[ii >= 0], ii[ii >= 0]] = 1.
        f = FR.loc[day]; e = SR.loc[day].to_numpy(float)
        for x, y in pairs:
            a = W[x][k] - W[y][k]; have = np.isfinite(X).all(axis=1)
            cov_w = float(np.abs(a[~have]).sum() / max(np.abs(a).sum(), 1e-12))
            ax = a[have] @ X[have]; ad = a @ D; contrib = {s: float(ax[i] * f[s]) for i, s in enumerate(STYLES)}
            contrib['INDUSTRY'] = float(sum(ad[i] * f[n] for i, n in enumerate(inds))); contrib['COUNTRY'] = float(a.sum() * f['COUNTRY'])
            es = np.where(np.isfinite(e), e, 0.); contrib['SPECIFIC'] = float(a @ es)
            contrib['EXANTE_TOTAL'] = float(sum(contrib.values()))
            contrib['REALISED'] = float(nav[x][k] / nav[x][k - 1] - nav[y][k] / nav[y][k - 1]) if k > 0 else np.nan
            rows.append(dict(date=day, arm=x, versus=y, uncovered_active=cov_w, **contrib))
            expo_rows.append(dict(date=day, arm=x, versus=y, **{s: float(ax[i]) for i, s in enumerate(STYLES)}, max_abs_industry=float(np.abs(ad).max())))
    A = pd.DataFrame(rows); XE = pd.DataFrame(expo_rows)
    A.to_csv(DEST/'daily_attribution.csv', index=False); XE.to_csv(DEST/'daily_active_exposures.csv', index=False)
    cols = STYLES + ['INDUSTRY', 'COUNTRY', 'SPECIFIC', 'EXANTE_TOTAL', 'REALISED']
    A['year'] = A.date.str[:4]
    summ = A.groupby(['arm', 'versus'])[cols].mean() * 252 * 100; summ['days'] = A.groupby(['arm', 'versus']).size(); summ['uncovered_active_mean'] = A.groupby(['arm', 'versus']).uncovered_active.mean()
    yearly = A.groupby(['arm', 'versus', 'year'])[cols].mean() * 252 * 100
    ex = XE.groupby(['arm', 'versus'])[STYLES].agg(['mean', lambda s: s.mean() / s.std() * np.sqrt(len(s))]); ex.columns = [f'{a}_{"mean" if b == "mean" else "t"}' for a, b in ex.columns]
    summ.round(3).to_csv(DEST/'attribution_summary.csv'); yearly.round(3).to_csv(DEST/'attribution_yearly.csv'); ex.round(3).to_csv(DEST/'active_exposure_summary.csv')
    pd.set_option('display.width', 250); pd.set_option('display.max_columns', 30)
    print('\nAnnualised contributions (pp/yr), ex-ante decomposition of X minus Y target weights:'); print(summ.round(2).to_string())
    print('\nMean active style exposures (σ units) and t-stats:'); print(ex.round(2).to_string())
    print('\nBy year:'); print(yearly.round(2).to_string())


if __name__ == '__main__':
    main()
