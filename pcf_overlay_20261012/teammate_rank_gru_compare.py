"""Teammate's OLD_RANK five-seed GRU (branch share/rank-gru-backtest-20261015) versus our control on the same ledger.

His package ships a processed T15 score matrix (662 x 1178, 2024-01-02..2026-09-23; neutralised, EMA .2, member z) from
five seeds 66/77/88/99/111 of the same control architecture, trained 2019-2023, validated 2024 (factor inputs as
uniform ranks instead of our clipped z).  No returns were computed on his side.  Here:
  1. RankIC (T15, members, y4 label) by year: his / ours (walk-forward control) / ours T2025-only / 10-seed average;
     rank correlation between his and ours.
  2. PCF-skeleton replay with the v4 ledger (10M, 2bp, T+1 open): P1 tilt k=.5 and V5 排雷 + tilt, signal = his / ours /
     average-of-both z (and each blended with c5), paired on 2025-01-02..2026-09-17 (his primary2025 + carry2026;
     2024 is his validation year and is excluded from the comparison).
"""
from pathlib import Path
import json, sys, time
import numpy as np
import pandas as pd
from scipy.special import ndtri
from scipy.stats import rankdata
from .data import ROOT, OUT, END, symbol, write, sha
from . import replay as R
from .optimizer_overlay_v4 import C5_PATH, simulate as v4_simulate
from . import wide_pool_optimizer_v1 as V1

PKG = Path(sys.argv[1]) if len(sys.argv) > 1 else Path('/private/tmp/claude-501/-Users-leslie-Downloads---500--60min/3881df40-0168-4cda-bd66-a5478b75c001/scratchpad/teammate_gru/research/gru_rank_backtest_20261015')
DEST = OUT/'teammate_rank_gru_20261015'; V4 = OUT/'optimizer_overlay_v4'; START = '2025-01-02'


def zrow(x):
    out = np.full_like(x, np.nan); ok = np.isfinite(x)
    if ok.sum() > 10 and x[ok].std() > 1e-12: out[ok] = (x[ok] - x[ok].mean()) / x[ok].std()
    return out


def main():
    DEST.mkdir(exist_ok=True); t0 = time.time()
    npz = PKG/'signals/OLD_RANK_ensemble_signals.npz'; assert sha(npz) == 'e402d5a3c48ad45bd3d4c5e8c4fde068f5a9d37933a12295519f035e5ee63b1f'
    z = np.load(npz, allow_pickle=False); his_dates = [str(d) for d in z['dates']]; his_syms = [str(s) for s in z['symbols']]; HS = z['scores']
    pc = pd.read_parquet(OUT/'pcf_components.parquet'); meta = pd.read_csv(OUT/'pcf_metadata.csv', dtype={'date': str, 'valuation_date': str}).set_index('date')
    prices = pd.read_parquet(OUT/'daily_prices.parquet'); symbols = json.load(open(OUT/'price_symbols.json')); U = len(symbols); lookup = {s: i for i, s in enumerate(symbols)}
    all_days = sorted(set(prices.date)); all_days = [d for d in all_days if '2023-03-01' <= d <= END]; daypos = {d: i for i, d in enumerate(all_days)}
    wide = lambda f: prices.pivot(index='date', columns='symbol', values=f).reindex(index=all_days, columns=symbols).apply(pd.to_numeric, errors='coerce').to_numpy(float)
    rawclose = wide('close'); op = wide('open_adjusted'); close = wide('close_adjusted'); volume = wide('volume'); valid = wide('valid') == 1; isST = wide('isST')
    can_same = valid & (volume > 0) & (isST != 1); can = np.vstack([can_same[:1], can_same[:-1]])
    DATA = ROOT/'gru_research/artifacts/dataset'; times = np.load(DATA/'times.npy'); clock = pd.to_datetime(times, utc=True).tz_convert('Asia/Shanghai')
    model_symbols = json.load(open(DATA/'symbols.json')); assert model_symbols == his_syms; mapping = np.array([lookup[s] for s in model_symbols]); by15 = {str(clock[i].date()): i for i in np.flatnonzero(clock.hour == 15)}
    frozen_member = np.load(DATA/'members.npy', mmap_mode='r'); labels_h = np.load(DATA/'labels.npy', mmap_mode='r')
    # ---- signals: ours (walk-forward control), his, 10-seed average (z of mean of the two member-z scores)
    s_ours, _ = R.signals(15, all_days, symbols)
    s_his = {}
    for k, d in enumerate(his_dates):
        full = np.full(U, np.nan); full[mapping] = HS[k]; s_his[d] = full
    s_avg = {d: zrow(np.where(np.isfinite(s_ours[d]) & np.isfinite(s_his[d]), (s_ours[d] + s_his[d]) / 2, np.where(np.isfinite(s_ours[d]), s_ours[d], s_his[d]))) for d in s_his if d in s_ours}
    c5 = pd.read_parquet(C5_PATH); c5 = c5[c5.segment != 'live']; s_c5 = {}; veto_c5 = {}
    for day, g in c5.groupby('date'):
        zz = np.full(U, np.nan); v = np.zeros(U, bool); gg = g[g.code.isin(lookup.keys())]
        zz[[lookup[c] for c in gg.code]] = ndtri(np.clip(gg.score_pct.to_numpy(), 1e-4, 1 - 1e-4))
        bad = gg[(gg.state == 'vetoed') | ((gg.state == 'excluded') & np.isfinite(gg.raw_score.to_numpy(dtype=float)))]; v[[lookup[c] for c in bad.code]] = True
        s_c5[day] = zz; veto_c5[day] = v
    def blend(a, b):
        if a is None: return b
        if b is None: return a
        return np.where(np.isfinite(a) & np.isfinite(b), (a + b) / 2, np.where(np.isfinite(a), a, b))
    # ---- 1. RankIC by year
    rows = []
    for d in sorted(s_his):
        if d not in by15 or d not in s_ours: continue
        i = by15[d]; y = np.full(U, np.nan); y[mapping] = labels_h[i, :, 3]; m = np.zeros(U, bool); m[mapping] = frozen_member[i]
        rec = dict(date=d)
        for nm, s in [('his', s_his[d]), ('ours_wf', s_ours[d]), ('avg10', s_avg[d])]:
            ok = m & np.isfinite(s) & np.isfinite(y)
            rec[nm] = np.corrcoef(rankdata(s[ok]), rankdata(y[ok]))[0, 1] if ok.sum() >= 100 else np.nan
        ok = m & np.isfinite(s_his[d]) & np.isfinite(s_ours[d]); rec['corr_his_ours'] = np.corrcoef(rankdata(s_his[d][ok]), rankdata(s_ours[d][ok]))[0, 1] if ok.sum() >= 100 else np.nan
        rows.append(rec)
    IC = pd.DataFrame(rows); IC['year'] = IC.date.str[:4]; IC.to_csv(DEST/'daily_rankic.csv', index=False)
    ic_tab = IC.groupby('year')[['his', 'ours_wf', 'avg10', 'corr_his_ours']].agg(['mean', lambda s: s.mean() / s.std() * np.sqrt(s.notna().sum())]); ic_tab.columns = [f'{a}_{"mean" if b == "mean" else "t"}' for a, b in ic_tab.columns]
    print('RankIC (T15, members, y4):'); print(ic_tab.round(4).to_string(), flush=True)
    # ---- 2. PCF replay (v4 ledger)
    v4_dates = pd.read_csv(V4/'P0_daily.csv').date.tolist(); pcgroups = {d: g[g.stock_replicable] for d, g in pc.groupby('date')}
    dates = []; refs = []; refs_v5 = []; sig = {k: [] for k in ['ours', 'his', 'avg', 'ours_c5', 'his_c5', 'avg_c5']}; signal_marks = []; fills = []; execution_prices = []; closes = []; pcf_ids = []; members = []; last_c5 = None
    for day in v4_dates:
        j = daypos[day]; prior = all_days[j - 1]
        if prior in s_c5: last_c5 = prior
        else: s_c5[prior] = s_c5[last_c5]; veto_c5[prior] = veto_c5[last_c5]
        if prior not in s_his: continue
        g = pcgroups[prior]; m = meta.loc[prior]; ids = np.array([lookup[symbol(c)] for c in g.code]); prev = rawclose[daypos[m.valuation_date], ids]
        ref = np.zeros(U); ref[ids] = g.quantity.to_numpy() * prev / float(m.nav_per_cu)
        if ref.sum() > .98: ref *= .98 / ref.sum()
        v5 = ref.copy(); v5[veto_c5[prior]] = 0.; v5 = v5 / v5.sum() * ref.sum()
        dates.append(day); refs.append(ref); refs_v5.append(v5); pcf_ids.append(ids)
        sig['ours'].append(s_ours[prior]); sig['his'].append(s_his[prior]); sig['avg'].append(s_avg[prior])
        sig['ours_c5'].append(blend(s_ours[prior], s_c5[prior])); sig['his_c5'].append(blend(s_his[prior], s_c5[prior])); sig['avg_c5'].append(blend(s_avg[prior], s_c5[prior]))
        signal_marks.append(close[j - 1]); fills.append(can[j]); execution_prices.append(op[j]); closes.append(close[j])
        mem = np.zeros(U, bool); mem[mapping] = frozen_member[by15[prior]]; members.append(mem)
    refs = np.asarray(refs); refs_v5 = np.asarray(refs_v5); closes = np.asarray(closes); sig = {k: np.asarray(v) for k, v in sig.items()}; members = np.asarray(members)
    dummy = {k: (None, None) for k in range(len(dates))}; dummyX = {k: None for k in range(len(dates))}
    results = {}
    for arm, (kind, rf, sname) in {'P0': ('P0', refs, 'ours'), 'P1_ours': ('P1', refs, 'ours'), 'P1_his': ('P1', refs, 'his'), 'P1_avg': ('P1', refs, 'avg'),
                                   'V5': ('P0', refs_v5, 'ours'), 'V5_P1_ours_c5': ('P1', refs_v5, 'ours_c5'), 'V5_P1_his_c5': ('P1', refs_v5, 'his_c5'), 'V5_P1_avg_c5': ('P1', refs_v5, 'avg_c5'), 'V5_P1_his': ('P1', refs_v5, 'his'), 'V5_P1_ours': ('P1', refs_v5, 'ours')}.items():
        d, _ = v4_simulate(kind, dates, rf, sig[sname], np.asarray(signal_marks), np.asarray(execution_prices), np.asarray(fills), closes, symbols, None, dummyX, dummy, dummyX, pcf_ids, members, [0.] * len(dates))
        d.to_csv(DEST/f'{arm}_daily.csv', index=False); results[arm] = d; print('ARM', arm, round(float(d.nav.iloc[-1]), 4), flush=True)
    start = next(k for k, d in enumerate(dates) if d >= START); sub = np.asarray(dates[start:])
    comp = []
    for x, y in [('P1_ours', 'P0'), ('P1_his', 'P0'), ('P1_avg', 'P0'), ('P1_his', 'P1_ours'), ('P1_avg', 'P1_ours'), ('V5_P1_ours', 'V5'), ('V5_P1_his', 'V5'), ('V5_P1_ours_c5', 'V5'), ('V5_P1_his_c5', 'V5'), ('V5_P1_avg_c5', 'V5'), ('V5_P1_his_c5', 'V5_P1_ours_c5'), ('V5_P1_avg_c5', 'V5_P1_ours_c5')]:
        st, hv = V1.paired(results[x].nav.to_numpy()[start:], results[y].nav.to_numpy()[start:], sub, 6)
        lg = np.log(results[x].nav.to_numpy()[start + 1:] / results[x].nav.to_numpy()[start:-1]) - np.log(results[y].nav.to_numpy()[start + 1:] / results[y].nav.to_numpy()[start:-1]); ser = pd.Series(lg, index=pd.to_datetime(sub[1:]))
        yr = {f'y{yy}': round(float(g.sum() * 252 / len(g) * 100), 2) for yy, g in ser.groupby(ser.index.year)}
        comp.append(dict(arm=x, versus=y, window=f'{sub[0]}..{sub[-1]}', **{k: v for k, v in st.items() if k in ('days', 'annualized_relative_cagr', 'boot95_low', 'boot95_high', 'tracking_error', 'information_ratio', 'w50_p10', 'w50_worst', 'w50_win')}, **yr))
    C = pd.DataFrame(comp); C.to_csv(DEST/'paired_comparisons_2025on.csv', index=False)
    write(DEST/'run_receipt.json', dict(package=str(PKG), signal_sha256=sha(npz), code_sha256=sha(__file__), seconds=round(time.time() - t0)))
    pd.set_option('display.width', 250); print(C.round(4).to_string(index=False))


if __name__ == '__main__':
    main()
