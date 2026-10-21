"""SIZE_RANK in the his5 slot of the live construction (c5 veto PCF + 50/50 GRU/c5 z blend + k=.5 tilt), PROTOCOL.json secondary.

Run from the project root: .pcf-research-venv/bin/python -I pcf_overlay_20261012/size_rank_replay/live_replay.py --snapshot <snapshot dir> --out <dir>
Ledger construction, simulate and paired are ensemble_replay.py's, unchanged; only the GRU signal per arm differs.
The two cluster six-field signals are read as data (np.load, allow_pickle=False); nothing from the snapshot is imported.
"""
from pathlib import Path
import argparse, hashlib, json, sys, time
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))   # project packages only (python -I drops cwd)
from scipy.special import ndtri
from scipy.stats import rankdata
from pcf_overlay_20261012.data import OUT, END, symbol
from pcf_overlay_20261012 import replay as R
from pcf_overlay_20261012.optimizer_overlay_v4 import C5_PATH, simulate as v4_simulate
from pcf_overlay_20261012 import wide_pool_optimizer_v1 as V1
from pcf_overlay_20261012.ensemble_replay import build_signals, V4, START, TZ

SIGNAL_SHA = {'OLD_RANK': 'e402d5a3c48ad45bd3d4c5e8c4fde068f5a9d37933a12295519f035e5ee63b1f',
              'SIZE_RANK': '91e4d25aaaf3732c436142701de6de0aa638b02733d5661525338794d8ef2c93'}
PAIRS = [('SIZENPZ', 'OLDNPZ'), ('tenA_SIZE', 'tenA_OLD'), ('SIZENPZ', 'V5'), ('SIZENPZ', 'ctrl5'), ('OLDNPZ', 'his5'), ('tenA_SIZE', 'ctrl5'),
         ('OLDNPZ', 'V5'), ('his5', 'V5'), ('ctrl5', 'V5'), ('tenA_OLD', 'ctrl5')]


def sha(p):
    h = hashlib.sha256(); h.update(Path(p).read_bytes()); return h.hexdigest()


def cluster_signal(path, expected, symbols):
    if sha(path) != expected: raise SystemExit('signal SHA mismatch ' + str(path))
    with np.load(path, allow_pickle=False) as z: dates, syms, scores = z['dates'], z['symbols'], z['scores']
    cols = pd.Index(syms.astype(str)).get_indexer(symbols); ok = cols >= 0; out = {}
    for i, d in enumerate(dates.astype(str)):
        row = np.full(len(symbols), np.nan); row[ok] = scores[i, cols[ok]]; out[d] = row
    return out


def main():
    ap = argparse.ArgumentParser(); ap.add_argument('--snapshot', type=Path, required=True); ap.add_argument('--out', type=Path, required=True)
    a = ap.parse_args(); dest = a.out.resolve(); dest.mkdir(parents=True, exist_ok=False); t0 = time.time()
    inf = a.snapshot.resolve() / 'HOME/gru_teammate_features_20261014_scratch_v2/inference'
    pc = pd.read_parquet(OUT/'pcf_components.parquet'); meta = pd.read_csv(OUT/'pcf_metadata.csv', dtype={'date': str, 'valuation_date': str}).set_index('date')
    prices = pd.read_parquet(OUT/'daily_prices.parquet'); symbols = json.load(open(OUT/'price_symbols.json')); U = len(symbols); lookup = {s: i for i, s in enumerate(symbols)}
    all_days = sorted(set(prices.date)); all_days = [d for d in all_days if '2023-03-01' <= d <= END]; daypos = {d: i for i, d in enumerate(all_days)}
    wide = lambda f: prices.pivot(index='date', columns='symbol', values=f).reindex(index=all_days, columns=symbols).apply(pd.to_numeric, errors='coerce').to_numpy(float)
    rawclose = wide('close'); op = wide('open_adjusted'); close = wide('close_adjusted'); volume = wide('volume'); valid = wide('valid') == 1; isST = wide('isST')
    can_same = valid & (volume > 0) & (isST != 1); can = np.vstack([can_same[:1], can_same[:-1]])
    DATA = ROOT/'gru_research/artifacts/dataset'; times = np.load(DATA/'times.npy'); clock = pd.to_datetime(times, utc=True).tz_convert(TZ); model_symbols = json.load(open(DATA/'symbols.json')); mapping = np.array([lookup[s] for s in model_symbols])
    by15 = {str(clock[i].date()): i for i in np.flatnonzero(clock.hour == 15)}; frozen_member = np.load(DATA/'members.npy', mmap_mode='r'); labels_h = np.load(DATA/'labels.npy', mmap_mode='r')
    sigs = {'ctrl5': build_signals(['wf2026'], symbols), 'his5': build_signals(['oldrank'], symbols)}
    sigs['OLDNPZ'] = cluster_signal(inf/'OLD_RANK_ensemble_signals.npz', SIGNAL_SHA['OLD_RANK'], symbols)
    sigs['SIZENPZ'] = cluster_signal(inf/'SIZE_RANK_ensemble_signals.npz', SIGNAL_SHA['SIZE_RANK'], symbols)
    def blend(a, b): return np.where(np.isfinite(a) & np.isfinite(b), (a + b) / 2, np.where(np.isfinite(a), a, b))
    for tag, src in (('tenA_OLD', 'OLDNPZ'), ('tenA_SIZE', 'SIZENPZ')):
        sigs[tag] = {d: R.zscore(blend(sigs['ctrl5'][d], sigs[src][d])) for d in sigs['ctrl5'] if d in sigs[src]}
    c5 = pd.read_parquet(C5_PATH); c5 = c5[c5.segment != 'live']; s_c5 = {}; veto_c5 = {}
    for day, g in c5.groupby('date'):
        z = np.full(U, np.nan); v = np.zeros(U, bool); gg = g[g.code.isin(lookup.keys())]; z[[lookup[c] for c in gg.code]] = ndtri(np.clip(gg.score_pct.to_numpy(), 1e-4, 1 - 1e-4))
        bad = gg[(gg.state == 'vetoed') | ((gg.state == 'excluded') & np.isfinite(gg.raw_score.to_numpy(dtype=float)))]; v[[lookup[c] for c in bad.code]] = True; s_c5[day] = z; veto_c5[day] = v
    names = ['ctrl5', 'his5', 'OLDNPZ', 'SIZENPZ', 'tenA_OLD', 'tenA_SIZE']
    rows = []
    for d in sorted(sigs['ctrl5']):
        if d not in by15 or d < START: continue
        i = by15[d]; y = np.full(U, np.nan); y[mapping] = labels_h[i, :, 3]; m = np.zeros(U, bool); m[mapping] = frozen_member[i]; rec = dict(date=d)
        for n in names:
            s = sigs[n].get(d, np.full(U, np.nan)); ok = m & np.isfinite(s) & np.isfinite(y); rec[n] = np.corrcoef(rankdata(s[ok]), rankdata(y[ok]))[0, 1] if ok.sum() >= 100 else np.nan
        for x, yy in (('OLDNPZ', 'his5'), ('SIZENPZ', 'OLDNPZ'), ('SIZENPZ', 'ctrl5')):
            p, q = sigs[x].get(d, np.full(U, np.nan)), sigs[yy].get(d, np.full(U, np.nan)); ok = m & np.isfinite(p) & np.isfinite(q)
            rec[f'corr_{x}_{yy}'] = np.corrcoef(rankdata(p[ok]), rankdata(q[ok]))[0, 1] if ok.sum() >= 100 else np.nan
        rows.append(rec)
    IC = pd.DataFrame(rows); IC['year'] = IC.date.str[:4]; IC.to_csv(dest/'daily_rankic_y4.csv', index=False)
    print(IC.groupby('year')[[c for c in IC.columns if c not in ('date', 'year')]].mean().round(4).to_string(), flush=True)
    v4_dates = pd.read_csv(V4/'P0_daily.csv').date.tolist(); pcgroups = {d: g[g.stock_replicable] for d, g in pc.groupby('date')}; last_c5 = None
    dates = []; refs_v5 = []; sig = {k: [] for k in names}; signal_marks = []; fills = []; execution_prices = []; closes = []; pcf_ids = []; members = []
    for day in v4_dates:
        j = daypos[day]; prior = all_days[j - 1]
        if prior in s_c5: last_c5 = prior
        else: s_c5[prior] = s_c5[last_c5]; veto_c5[prior] = veto_c5[last_c5]
        if prior not in sigs['ctrl5']: continue
        g = pcgroups[prior]; mm = meta.loc[prior]; ids = np.array([lookup[symbol(c)] for c in g.code]); prev = rawclose[daypos[mm.valuation_date], ids]
        ref = np.zeros(U); ref[ids] = g.quantity.to_numpy() * prev / float(mm.nav_per_cu)
        if ref.sum() > .98: ref *= .98 / ref.sum()
        v5 = ref.copy(); v5[veto_c5[prior]] = 0.; v5 = v5 / v5.sum() * ref.sum()
        dates.append(day); refs_v5.append(v5); pcf_ids.append(ids)
        for k in names: sig[k].append(blend(sigs[k].get(prior, np.full(U, np.nan)), s_c5[prior]))
        signal_marks.append(close[j - 1]); fills.append(can[j]); execution_prices.append(op[j]); closes.append(close[j]); mem = np.zeros(U, bool); mem[mapping] = frozen_member[by15[prior]]; members.append(mem)
    refs_v5 = np.asarray(refs_v5); closes = np.asarray(closes); sig = {k: np.asarray(v) for k, v in sig.items()}; members = np.asarray(members)
    dummy = {k: (None, None) for k in range(len(dates))}; dummyX = {k: None for k in range(len(dates))}; results = {}
    run = lambda arm, s: v4_simulate(arm, dates, refs_v5, s, np.asarray(signal_marks), np.asarray(execution_prices), np.asarray(fills), closes, symbols, None, dummyX, dummy, dummyX, pcf_ids, members, [0.] * len(dates))[0]
    results['V5'] = run('P0', sig['ctrl5'])
    for k in names: results[k] = run('P1', sig[k]); print('ARM', k, round(float(results[k].nav.iloc[-1]), 4), flush=True)
    for k, d in results.items(): d.to_csv(dest/f'{k}_daily.csv', index=False)
    start = next(i for i, d in enumerate(dates) if d >= START); sub = np.asarray(dates[start:]); comp = []
    for x, y in PAIRS:
        st, _ = V1.paired(results[x].nav.to_numpy()[start:], results[y].nav.to_numpy()[start:], sub, 5)
        lg = np.log(results[x].nav.to_numpy()[start + 1:] / results[x].nav.to_numpy()[start:-1]) - np.log(results[y].nav.to_numpy()[start + 1:] / results[y].nav.to_numpy()[start:-1]); ser = pd.Series(lg, index=pd.to_datetime(sub[1:]))
        to = lambda r: float(r.iloc[start + 1:].filter(like='turnover').iloc[:, 0].mean()) if r.filter(like='turnover').shape[1] else np.nan
        comp.append(dict(arm=x, versus=y, **{k: v for k, v in st.items() if k in ('days', 'annualized_relative_cagr', 'boot95_low', 'boot95_high', 'tracking_error', 'information_ratio', 'w50_p10', 'w50_worst', 'w50_win')},
                         **{f'y{yy}_ann_log_pct': round(float(gg.sum() * 252 / len(gg) * 100), 2) for yy, gg in ser.groupby(ser.index.year)}, arm_turnover=to(results[x]), versus_turnover=to(results[y])))
    C = pd.DataFrame(comp); C.to_csv(dest/'paired_comparisons_2025on.csv', index=False)
    (dest/'run_receipt.json').write_text(json.dumps(dict(code_sha256=sha(__file__), seconds=round(time.time() - t0), signals_sha256=SIGNAL_SHA, start=START, rows=len(dates)), indent=1))
    pd.set_option('display.width', 250); print(C.round(4).to_string(index=False))


if __name__ == '__main__':
    main()
