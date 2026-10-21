"""Industry-aware refill of the c5 exclusion on the PCF skeleton (v4 ledger), registered 2026-10-15.

Current rule (V5): removed names' weight is spread pro-rata over all remaining names -> industries that lost names end up
underweight (CNE5 attribution: industry term of V5 minus P0 = -0.56 pp/yr).  Proposed rule: the removed weight is given
back to the remaining names of the SAME 申万一级 industry (pro-rata within the industry); if an industry has no remaining
name, that weight falls back to the pro-rata pool.  Industry = frozen research context (risk_context.npz, 1178 union names
at the signal date's 15:00 row); PCF names outside the union are 'UNKNOWN' and refill pro-rata.
Arms: V5 (as v4), V5_ind (industry refill), V5_P1blend (as v4), V5_ind_P1blend.  Paired stats on the full v4 calendar.
"""
from pathlib import Path
import json, time
import numpy as np
import pandas as pd
from scipy.special import ndtri
from .data import ROOT, OUT, END, symbol, write, sha
from . import replay as R
from .optimizer_overlay_v4 import C5_PATH, simulate as v4_simulate
from . import wide_pool_optimizer_v1 as V1

DEST = OUT/'industry_refill_v1'; V4 = OUT/'optimizer_overlay_v4'


def refill_within_industry(w, bad, ind):
    """w: Series weights (dot codes); bad: set of codes to zero; ind: dict code->industry id (-1 unknown)."""
    tot = w.sum(); removed = w[w.index.isin(bad)]; keep = w[~w.index.isin(bad)].copy()
    if keep.sum() <= 0: return w
    pool = 0.
    for code, wt in removed.items():
        g = ind.get(code, -1); same = keep.index[[ind.get(c, -1) == g for c in keep.index]] if g >= 0 else []
        if len(same) and keep[same].sum() > 0: keep[same] += wt * keep[same] / keep[same].sum()
        else: pool += wt
    if pool > 0: keep += pool * keep / keep.sum()
    return keep / keep.sum() * tot


def main():
    DEST.mkdir(exist_ok=True); t0 = time.time()
    pc = pd.read_parquet(OUT/'pcf_components.parquet'); meta = pd.read_csv(OUT/'pcf_metadata.csv', dtype={'date': str, 'valuation_date': str}).set_index('date')
    prices = pd.read_parquet(OUT/'daily_prices.parquet'); symbols = json.load(open(OUT/'price_symbols.json')); U = len(symbols); lookup = {s: i for i, s in enumerate(symbols)}
    all_days = sorted(set(prices.date)); all_days = [d for d in all_days if '2023-03-01' <= d <= END]; daypos = {d: i for i, d in enumerate(all_days)}
    wide = lambda f: prices.pivot(index='date', columns='symbol', values=f).reindex(index=all_days, columns=symbols).apply(pd.to_numeric, errors='coerce').to_numpy(float)
    rawclose = wide('close'); op = wide('open_adjusted'); close = wide('close_adjusted'); volume = wide('volume'); valid = wide('valid') == 1; isST = wide('isST')
    can_same = valid & (volume > 0) & (isST != 1); can = np.vstack([can_same[:1], can_same[:-1]])
    DATA = ROOT/'gru_research/artifacts/dataset'; times = np.load(DATA/'times.npy'); clock = pd.to_datetime(times, utc=True).tz_convert('Asia/Shanghai')
    q = np.load(ROOT/'outputs/gru_beta_neutral_20261008/risk_context.npz'); model_symbols = json.load(open(DATA/'symbols.json')); mapping = np.array([lookup[s] for s in model_symbols]); by15 = {str(clock[i].date()): i for i in np.flatnonzero(clock.hour == 15)}
    frozen_member = np.load(DATA/'members.npy', mmap_mode='r')
    s15, _ = R.signals(15, all_days, symbols)
    c5 = pd.read_parquet(C5_PATH); c5 = c5[c5.segment != 'live']; s_c5 = {}; bad_c5 = {}
    for day, g in c5.groupby('date'):
        z = np.full(U, np.nan); gg = g[g.code.isin(lookup.keys())]; z[[lookup[c] for c in gg.code]] = ndtri(np.clip(gg.score_pct.to_numpy(), 1e-4, 1 - 1e-4))
        bad = gg[(gg.state == 'vetoed') | ((gg.state == 'excluded') & np.isfinite(gg.raw_score.to_numpy(dtype=float)))]; s_c5[day] = z; bad_c5[day] = set(bad.code)
    def blend(a, b): return np.where(np.isfinite(a) & np.isfinite(b), (a + b) / 2, np.where(np.isfinite(a), a, b))
    v4_dates = pd.read_csv(V4/'P0_daily.csv').date.tolist(); pcgroups = {d: g[g.stock_replicable] for d, g in pc.groupby('date')}
    dates = []; refs_v5 = []; refs_ind = []; sig = []; signal_marks = []; fills = []; execution_prices = []; closes = []; pcf_ids = []; members = []; last_c5 = None; stats = []
    for day in v4_dates:
        j = daypos[day]; prior = all_days[j - 1]
        if prior in s_c5: last_c5 = prior
        else: s_c5[prior] = s_c5[last_c5]; bad_c5[prior] = bad_c5[last_c5]
        g = pcgroups[prior]; m = meta.loc[prior]; codes = [symbol(c) for c in g.code]; ids = np.array([lookup[c] for c in codes]); prev = rawclose[daypos[m.valuation_date], ids]
        w = pd.Series(g.quantity.to_numpy() * prev / float(m.nav_per_cu), index=codes)
        if w.sum() > .98: w *= .98 / w.sum()
        i15 = by15[prior]; ind_row = q['industry'][i15]; ind = {model_symbols[k]: int(ind_row[k]) for k in range(len(model_symbols))}
        bad = bad_c5[prior]
        v5 = w.copy(); tot = v5.sum(); v5[v5.index.isin(bad)] = 0.; v5 = v5 / v5.sum() * tot
        vi = refill_within_industry(w, bad, ind)
        r5 = np.zeros(U); r5[[lookup[c] for c in v5.index]] = v5.to_numpy(); ri = np.zeros(U); ri[[lookup[c] for c in vi.index]] = vi.to_numpy()
        dates.append(day); refs_v5.append(r5); refs_ind.append(ri); pcf_ids.append(ids); sig.append(blend(s15[prior], s_c5[prior]))
        signal_marks.append(close[j - 1]); fills.append(can[j]); execution_prices.append(op[j]); closes.append(close[j])
        mem = np.zeros(U, bool); mem[mapping] = frozen_member[i15]; members.append(mem)
        removed = w[w.index.isin(bad)]; stats.append(dict(date=day, removed_weight=float(removed.sum()), removed_unknown_industry=float(removed[[ind.get(c, -1) < 0 for c in removed.index]].sum()), l1_diff_v5_vs_ind=float(np.abs(r5 - ri).sum() / 2)))
    refs_v5 = np.asarray(refs_v5); refs_ind = np.asarray(refs_ind); closes = np.asarray(closes); sig = np.asarray(sig); members = np.asarray(members)
    pd.DataFrame(stats).to_csv(DEST/'refill_stats.csv', index=False); print('events', len(dates), pd.DataFrame(stats)[['removed_weight', 'removed_unknown_industry', 'l1_diff_v5_vs_ind']].mean().round(4).to_dict(), round(time.time() - t0), 's', flush=True)
    dummy = {k: (None, None) for k in range(len(dates))}; dummyX = {k: None for k in range(len(dates))}; results = {}
    for arm, (kind, rf) in {'V5': ('P0', refs_v5), 'V5_ind': ('P0', refs_ind), 'V5_P1blend': ('P1', refs_v5), 'V5_ind_P1blend': ('P1', refs_ind)}.items():
        d, _ = v4_simulate(kind, dates, rf, sig, np.asarray(signal_marks), np.asarray(execution_prices), np.asarray(fills), closes, symbols, None, dummyX, dummy, dummyX, pcf_ids, members, [0.] * len(dates))
        d.to_csv(DEST/f'{arm}_daily.csv', index=False); results[arm] = d; print('ARM', arm, round(float(d.nav.iloc[-1]), 4), flush=True)
    ref = pd.read_csv(V4/'V5_daily.csv'); assert np.allclose(ref.nav.to_numpy(), results['V5'].nav.to_numpy(), atol=1e-9), 'V5 rebuild differs from v4'
    comp = []
    for x, y in [('V5_ind', 'V5'), ('V5_ind_P1blend', 'V5_P1blend')]:
        st, hv = V1.paired(results[x].nav.to_numpy(), results[y].nav.to_numpy(), np.asarray(dates), 4)
        lg = np.log(results[x].nav.to_numpy()[1:] / results[x].nav.to_numpy()[:-1]) - np.log(results[y].nav.to_numpy()[1:] / results[y].nav.to_numpy()[:-1]); ser = pd.Series(lg, index=pd.to_datetime(dates[1:]))
        comp.append(dict(arm=x, versus=y, **{k: v for k, v in st.items() if k in ('days', 'annualized_relative_cagr', 'boot95_low', 'boot95_high', 'tracking_error', 'information_ratio', 'w50_p10', 'w50_worst', 'w50_win')}, **{f'y{yy}': round(float(g.sum() * 252 / len(g) * 100), 2) for yy, g in ser.groupby(ser.index.year)}))
    C = pd.DataFrame(comp); C.to_csv(DEST/'paired_comparisons.csv', index=False); write(DEST/'run_receipt.json', dict(code_sha256=sha(__file__), seconds=round(time.time() - t0)))
    pd.set_option('display.width', 250); print(C.round(4).to_string(index=False))


if __name__ == '__main__':
    main()
