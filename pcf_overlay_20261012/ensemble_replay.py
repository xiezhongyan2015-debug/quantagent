"""Ensemble replay on the PCF ledger (v4), registered 2026-10-15: which model set should the live 15:00 signal use?

Signals are rebuilt from scores_frozen/<set>.npz exactly as the live chain does (per-seed z of head y4 -> mean over the
set's models -> OLS neutralisation on volatility/turnover/log cap/beta60 from risk_context -> EMA .2 -> member z), at the
15:00 slot, for: ctrl5 = wf2026 (live), his5 = oldrank, ten = wf2026+oldrank, wf10 = wf2025+wf2026, all15 = all three.
Each is blended with the c5 z and run as V5 + k=.5 tilt (the live construction) on the v4 calendar; paired on
2025-01-02..2026-09-17 against V5 and against ctrl5.  Note: wf2025 and oldrank were validated on 2024 and are proper
out-of-sample in 2025-2026; wf2026 was validated on 2025 (its 2025 is in-sample for selection), as in all earlier replays.
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
from gru_beta_neutral_20261008.replay import neutralize

DEST = OUT/'ensemble_replay_v1'; V4 = OUT/'optimizer_overlay_v4'; FROZEN = ROOT/'outputs/gru_live_extend_20261012/scores_frozen'; START = '2025-01-02'; TZ = 'Asia/Shanghai'
ENSEMBLES = {'ctrl5': ['wf2026'], 'his5': ['oldrank'], 'ten': ['wf2026', 'oldrank'], 'wf10': ['wf2025', 'wf2026'], 'all15': ['wf2025', 'wf2026', 'oldrank']}


def build_signals(sets, symbols, slot=15):
    DATA = ROOT/'gru_research/artifacts/dataset'; model_symbols = json.load(open(DATA/'symbols.json')); lookup = {s: i for i, s in enumerate(symbols)}; mapping = np.array([lookup[s] for s in model_symbols])
    times = np.load(DATA/'times.npy'); members = np.load(DATA/'members.npy', mmap_mode='r'); tpos = {int(t): i for i, t in enumerate(times)}
    q = np.load(ROOT/'outputs/gru_beta_neutral_20261008/risk_context.npz'); qpos = {int(t): i for i, t in enumerate(q['times'])}
    fro = {k: {'times': np.load(FROZEN/f'{k}.npz')['times'], 'scores': np.load(FROZEN/f'{k}.npz')['scores'][:, :, :, 3].astype(np.float32)} for k in sets}; ft = fro[sets[0]]['times']   # materialise once: NpzFile re-decompresses on every access; assert all(np.array_equal(fro[k]['times'], ft) for k in sets)
    clock = pd.to_datetime(ft, utc=True).tz_convert(TZ); out = {}; last = None
    for i in range(len(ft)):
        if clock[i].hour != slot: continue
        raw = np.mean([R.zscore(fro[k]['scores'][i, m].astype(float)) for k in sets for m in range(5)], axis=0); t = int(ft[i]); j = qpos[t]; ti = tpos[t]
        expo = np.stack([q['volatility'][j], q['turnover'][j], q['log_cap'][j], q['beta60'][j]], axis=1); row = neutralize(np.where(members[ti], raw, np.nan), expo)
        if last is not None: row = np.where(np.isfinite(row) & np.isfinite(last), .2 * row + .8 * last, row)
        last = row.copy(); z = R.zscore(np.where(members[ti], row, np.nan)); full = np.full(len(symbols), np.nan); full[mapping] = z; out[str(clock[i].date())] = full
    return out


def main():
    DEST.mkdir(exist_ok=True); t0 = time.time()
    pc = pd.read_parquet(OUT/'pcf_components.parquet'); meta = pd.read_csv(OUT/'pcf_metadata.csv', dtype={'date': str, 'valuation_date': str}).set_index('date')
    prices = pd.read_parquet(OUT/'daily_prices.parquet'); symbols = json.load(open(OUT/'price_symbols.json')); U = len(symbols); lookup = {s: i for i, s in enumerate(symbols)}
    all_days = sorted(set(prices.date)); all_days = [d for d in all_days if '2023-03-01' <= d <= END]; daypos = {d: i for i, d in enumerate(all_days)}
    wide = lambda f: prices.pivot(index='date', columns='symbol', values=f).reindex(index=all_days, columns=symbols).apply(pd.to_numeric, errors='coerce').to_numpy(float)
    rawclose = wide('close'); op = wide('open_adjusted'); close = wide('close_adjusted'); volume = wide('volume'); valid = wide('valid') == 1; isST = wide('isST')
    can_same = valid & (volume > 0) & (isST != 1); can = np.vstack([can_same[:1], can_same[:-1]])
    DATA = ROOT/'gru_research/artifacts/dataset'; times = np.load(DATA/'times.npy'); clock = pd.to_datetime(times, utc=True).tz_convert(TZ); model_symbols = json.load(open(DATA/'symbols.json')); mapping = np.array([lookup[s] for s in model_symbols])
    by15 = {str(clock[i].date()): i for i in np.flatnonzero(clock.hour == 15)}; frozen_member = np.load(DATA/'members.npy', mmap_mode='r'); labels_h = np.load(DATA/'labels.npy', mmap_mode='r')
    sigs = {name: build_signals(sets, symbols) for name, sets in ENSEMBLES.items()}
    s_live, _ = R.signals(15, all_days, symbols)   # the exact live chain (walk-forward exports) as a cross-check of ctrl5
    c5 = pd.read_parquet(C5_PATH); c5 = c5[c5.segment != 'live']; s_c5 = {}; veto_c5 = {}
    for day, g in c5.groupby('date'):
        z = np.full(U, np.nan); v = np.zeros(U, bool); gg = g[g.code.isin(lookup.keys())]; z[[lookup[c] for c in gg.code]] = ndtri(np.clip(gg.score_pct.to_numpy(), 1e-4, 1 - 1e-4))
        bad = gg[(gg.state == 'vetoed') | ((gg.state == 'excluded') & np.isfinite(gg.raw_score.to_numpy(dtype=float)))]; v[[lookup[c] for c in bad.code]] = True; s_c5[day] = z; veto_c5[day] = v
    def blend(a, b): return np.where(np.isfinite(a) & np.isfinite(b), (a + b) / 2, np.where(np.isfinite(a), a, b))
    # RankIC and agreement
    from scipy.stats import rankdata
    rows = []
    for d in sorted(sigs['ctrl5']):
        if d not in by15 or d < START: continue
        i = by15[d]; y = np.full(U, np.nan); y[mapping] = labels_h[i, :, 3]; m = np.zeros(U, bool); m[mapping] = frozen_member[i]; rec = dict(date=d)
        for name in ENSEMBLES:
            s = sigs[name][d]; ok = m & np.isfinite(s) & np.isfinite(y); rec[name] = np.corrcoef(rankdata(s[ok]), rankdata(y[ok]))[0, 1] if ok.sum() >= 100 else np.nan
        ok = m & np.isfinite(sigs['ctrl5'][d]) & np.isfinite(s_live.get(d, np.full(U, np.nan))); rec['ctrl5_vs_live_export'] = np.corrcoef(rankdata(sigs['ctrl5'][d][ok]), rankdata(s_live[d][ok]))[0, 1] if ok.sum() >= 100 else np.nan
        rows.append(rec)
    IC = pd.DataFrame(rows); IC['year'] = IC.date.str[:4]; IC.to_csv(DEST/'daily_rankic.csv', index=False)
    print(IC.groupby('year')[list(ENSEMBLES) + ['ctrl5_vs_live_export']].mean().round(4).to_string(), flush=True)
    # ledger
    v4_dates = pd.read_csv(V4/'P0_daily.csv').date.tolist(); pcgroups = {d: g[g.stock_replicable] for d, g in pc.groupby('date')}; last_c5 = None
    dates = []; refs_v5 = []; sig = {k: [] for k in ENSEMBLES}; signal_marks = []; fills = []; execution_prices = []; closes = []; pcf_ids = []; members = []
    for day in v4_dates:
        j = daypos[day]; prior = all_days[j - 1]
        if prior in s_c5: last_c5 = prior
        else: s_c5[prior] = s_c5[last_c5]; veto_c5[prior] = veto_c5[last_c5]
        if prior not in sigs['ctrl5']: continue
        g = pcgroups[prior]; m = meta.loc[prior]; ids = np.array([lookup[symbol(c)] for c in g.code]); prev = rawclose[daypos[m.valuation_date], ids]
        ref = np.zeros(U); ref[ids] = g.quantity.to_numpy() * prev / float(m.nav_per_cu)
        if ref.sum() > .98: ref *= .98 / ref.sum()
        v5 = ref.copy(); v5[veto_c5[prior]] = 0.; v5 = v5 / v5.sum() * ref.sum()
        dates.append(day); refs_v5.append(v5); pcf_ids.append(ids)
        for k in ENSEMBLES: sig[k].append(blend(sigs[k][prior], s_c5[prior]))
        signal_marks.append(close[j - 1]); fills.append(can[j]); execution_prices.append(op[j]); closes.append(close[j]); mem = np.zeros(U, bool); mem[mapping] = frozen_member[by15[prior]]; members.append(mem)
    refs_v5 = np.asarray(refs_v5); closes = np.asarray(closes); sig = {k: np.asarray(v) for k, v in sig.items()}; members = np.asarray(members)
    dummy = {k: (None, None) for k in range(len(dates))}; dummyX = {k: None for k in range(len(dates))}; results = {}
    d, _ = v4_simulate('P0', dates, refs_v5, sig['ctrl5'], np.asarray(signal_marks), np.asarray(execution_prices), np.asarray(fills), closes, symbols, None, dummyX, dummy, dummyX, pcf_ids, members, [0.] * len(dates)); results['V5'] = d; d.to_csv(DEST/'V5_daily.csv', index=False)
    for k in ENSEMBLES:
        d, _ = v4_simulate('P1', dates, refs_v5, sig[k], np.asarray(signal_marks), np.asarray(execution_prices), np.asarray(fills), closes, symbols, None, dummyX, dummy, dummyX, pcf_ids, members, [0.] * len(dates)); results[k] = d; d.to_csv(DEST/f'V5_P1_{k}_daily.csv', index=False)
        print('ARM', k, round(float(d.nav.iloc[-1]), 4), flush=True)
    start = next(i for i, d in enumerate(dates) if d >= START); sub = np.asarray(dates[start:]); comp = []
    for x, y in [(k, 'V5') for k in ENSEMBLES] + [(k, 'ctrl5') for k in ENSEMBLES if k != 'ctrl5']:
        st, hv = V1.paired(results[x].nav.to_numpy()[start:], results[y].nav.to_numpy()[start:], sub, 5)
        lg = np.log(results[x].nav.to_numpy()[start + 1:] / results[x].nav.to_numpy()[start:-1]) - np.log(results[y].nav.to_numpy()[start + 1:] / results[y].nav.to_numpy()[start:-1]); ser = pd.Series(lg, index=pd.to_datetime(sub[1:]))
        comp.append(dict(arm=x, versus=y, **{k: v for k, v in st.items() if k in ('days', 'annualized_relative_cagr', 'boot95_low', 'boot95_high', 'tracking_error', 'information_ratio', 'w50_p10', 'w50_worst', 'w50_win')}, **{f'y{yy}': round(float(g.sum() * 252 / len(g) * 100), 2) for yy, g in ser.groupby(ser.index.year)}))
    C = pd.DataFrame(comp); C.to_csv(DEST/'paired_comparisons_2025on.csv', index=False); write(DEST/'run_receipt.json', dict(code_sha256=sha(__file__), seconds=round(time.time() - t0)))
    pd.set_option('display.width', 250); print(C.round(4).to_string(index=False))


if __name__ == '__main__':
    main()
