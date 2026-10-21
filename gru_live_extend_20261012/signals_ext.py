"""Neutralised, smoothed 15:00 (and 14:00) signals for the new sessions, continuing the frozen research chain exactly.

Replicates pcf_overlay_20261012.replay.signals: per seed z-score of head y4 -> seed mean -> residual on (volatility rank,
turnover rank, log cap, beta60) -> EMA 0.2 across sessions -> z-score over members. Frozen sessions (<= 2026-09-23) come from
the exported test_scores.npz and the frozen risk_context.npz; new sessions come from scores_new.npz with the risk inputs
rebuilt from the extended panel (slow rows: d_vol20, d_turnover, d_log_total_cap; beta60 from 60 prior sessions of adjusted
15:00 closes against the CSI500 price index).
Outputs: outputs/gru_live_extend_20261012/signals/z_<date>_slot<15|14>.csv (date,code,z) and chain_check.json.
"""
import json, sys
from pathlib import Path
import numpy as np, pandas as pd
ROOT = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(ROOT))
from gru_beta_neutral_20261008.replay import neutralize
from pcf_overlay_20261012.replay import zscore
DATASET = ROOT / 'gru_research/artifacts/dataset'; SC = ROOT / 'outputs/gru_live_extend_20261012/scores'; OUT = ROOT / 'outputs/gru_live_extend_20261012/signals'; OUT.mkdir(parents=True, exist_ok=True)
import os
TENCENT = Path(os.environ['EXT_TENCENT']) if os.environ.get('EXT_TENCENT') else ROOT / 'gru_research/trading_data/future_minute_probe/tencent_daily_20261012/raw'; TZ = 'Asia/Shanghai'


def index_closes():
    b = pd.read_csv(ROOT / 'outputs/gru_beta_neutral_20261008/benchmark_sources/000905_daily.csv', dtype={'date': str}).set_index('date').close.astype(float)
    j = json.load(open(TENCENT / '000905.SH_raw.json', encoding='utf-8')); k = list(j['data'])[0]; t = pd.Series({r[0]: float(r[2]) for r in j['data'][k]['day']})
    overlap = t.index.intersection(b.index); rel = float(np.max(np.abs(t[overlap] / b[overlap] - 1))) if len(overlap) else None
    return pd.concat([b, t[~t.index.isin(b.index)]]).sort_index(), rel


ENSEMBLES = {'ctrl5': ['wf2026'], 'his5': ['oldrank'], 'ten': ['wf2026', 'oldrank'], 'wf10': ['wf2025', 'wf2026'], 'all15': ['wf2025', 'wf2026', 'oldrank']}
FROZEN = ROOT / 'outputs/gru_live_extend_20261012/scores_frozen'; WARM = 60


def main(slot=15, ens='ctrl5'):
    if ens != 'ctrl5': return main_ensemble(slot, ens)
    symbols = json.loads((DATASET / 'symbols.json').read_text(encoding='utf-8')); U = len(symbols)
    p = np.load(SC / 'panel_ext.npz', allow_pickle=True); s = np.load(SC / 'scores_new.npz'); q = np.load(ROOT / 'outputs/gru_beta_neutral_20261008/risk_context.npz')
    times_ext = p['times']; clock_ext = pd.to_datetime(times_ext, utc=True).tz_convert(TZ); new_dates = list(p['new_dates']); slow_names = list(p['slow_names'])
    # frozen chain through the last frozen session, keyed by timestamp (works on the full dataset and on the lite tail):
    # the EMA state at the last frozen session is the *neutralised* (pre-zscore) row, recomputed exactly as replay.signals()
    times = np.load(DATASET / 'times.npy'); clock = pd.to_datetime(times, utc=True).tz_convert(TZ); members = np.load(DATASET / 'members.npy', mmap_mode='r')
    tpos = {int(t): i for i, t in enumerate(times)}; qpos = {int(t): i for i, t in enumerate(q['times'])}
    last = None; seeds = [11, 22, 33, 44, 55]; seed_scores = []; ref = None
    for seed in seeds:
        a = np.load(ROOT / f'outputs/gru_walkforward_20261010/T2026/control_seed{seed}/test_scores.npz'); at = a['times']
        keep = np.array([(int(t) in tpos) and (pd.Timestamp(int(t), tz='UTC').tz_convert(TZ).hour == slot) for t in at]); sel = at[keep]
        ref = sel if ref is None else ref; assert np.array_equal(ref, sel)
        seed_scores.append(np.asarray([zscore(r) for r in a['scores'][keep, :, 3].astype(float)]))
    raw = np.mean(seed_scores, axis=0)
    for row, t in zip(raw, ref):
        i, j = tpos[int(t)], qpos[int(t)]
        expo = np.stack([q['volatility'][j], q['turnover'][j], q['log_cap'][j], q['beta60'][j]], axis=1); row = neutralize(np.where(members[i], row, np.nan), expo)
        if last is not None: row = np.where(np.isfinite(row) & np.isfinite(last), .2 * row + .8 * last, row)
        last = row.copy()
    check0 = dict(frozen_chain_sessions=int(len(ref)), frozen_chain_first=str(pd.Timestamp(int(ref[0]), tz='UTC').tz_convert(TZ).date()), frozen_chain_last=str(pd.Timestamp(int(ref[-1]), tz='UTC').tz_convert(TZ).date()))
    # risk inputs for the new sessions
    idx, idx_rel = index_closes(); close15 = np.asarray(p['close_adj'])[3::4]; dates_ext = [str(d.date()) for d in clock_ext[3::4]]
    stockret = np.full_like(close15, np.nan); ok = np.isfinite(close15[1:]) & np.isfinite(close15[:-1]) & (close15[1:] > 0) & (close15[:-1] > 0); np.divide(close15[1:], close15[:-1], out=stockret[1:], where=ok); stockret[1:] -= 1
    ic = idx.reindex(dates_ext).to_numpy(float); mret = np.full(len(ic), np.nan); mret[1:] = ic[1:] / ic[:-1] - 1
    def beta_at(d):
        y, m = stockret[d - 60:d], mret[d - 60:d, None]; okk = np.isfinite(y) & np.isfinite(m); n = okk.sum(axis=0)
        ym = np.where(okk, y, 0).sum(axis=0) / np.maximum(n, 1); mm = np.where(okk, m, 0).sum(axis=0) / np.maximum(n, 1)
        cov = np.where(okk, (y - ym) * (m - mm), 0).sum(axis=0); var = np.where(okk, (m - mm) ** 2, 0).sum(axis=0); out = np.full(U, np.nan); np.divide(cov, var, out=out, where=(n >= 40) & (var > 1e-12)); return out
    # frozen-vs-rebuilt beta check on the last frozen session
    dlast = dates_ext.index('2026-09-23'); tlast = qpos[int(times[np.flatnonzero(clock.strftime('%Y-%m-%d').to_numpy() == '2026-09-23')[-1]])]
    b_new, b_old = beta_at(dlast), q['beta60'][tlast]; okb = np.isfinite(b_new) & np.isfinite(b_old)
    check = dict(**check0, index_overlap_maxrel=idx_rel, beta60_0923_maxabs_diff=float(np.max(np.abs(b_new[okb] - b_old[okb]))), beta60_0923_n=int(okb.sum()))
    vol_i, to_i, cap_i = [slow_names.index(n) for n in ['d_vol20', 'd_turnover', 'd_log_total_cap']]
    # new sessions
    sc = s['scores']; stimes = s['times']; sclock = pd.to_datetime(stimes, utc=True).tz_convert(TZ); mem_ext = p['members']; tix = {int(t): i for i, t in enumerate(times_ext)}
    outputs = {}
    for j, t in enumerate(stimes):
        day = str(sclock[j].date())
        if sclock[j].hour != slot or day not in new_dates: continue
        raw = np.mean([zscore(sc[j, k, :, 3].astype(float)) for k in range(sc.shape[1])], axis=0)
        di = new_dates.index(day); slow = p['slow_new'][di]; d_ext = dates_ext.index(day); t_ext = tix[int(t)]
        expo = np.stack([slow[:, vol_i], slow[:, to_i], slow[:, cap_i], beta_at(d_ext)], axis=1).astype(float)
        row = neutralize(np.where(mem_ext[t_ext], raw, np.nan), expo)
        if last is not None: row = np.where(np.isfinite(row) & np.isfinite(last), .2 * row + .8 * last, row)
        last = row.copy(); z = zscore(np.where(mem_ext[t_ext], row, np.nan))
        df = pd.DataFrame(dict(date=day, code=symbols, z=z)); df = df[np.isfinite(df.z)]; df.to_csv(OUT / f'z_{day}_slot{slot}.csv', index=False)
        outputs[day] = dict(names=int(len(df)), z_abs_mean=float(df.z.abs().mean()), scored=int(np.isfinite(raw).sum()), neutralised=int(np.isfinite(row).sum()))
    check['new_sessions'] = outputs
    (OUT / f'chain_check_slot{slot}.json').write_text(json.dumps(check, ensure_ascii=False, indent=1, default=str), encoding='utf-8'); print(json.dumps(check, ensure_ascii=False, default=str))


def main_ensemble(slot, ens):
    """Same chain as main() for a different model set: EMA warmed up over the last WARM frozen sessions at this slot from
    scores_frozen/<set>.npz (risk exposures from the frozen risk_context), then the new sessions from scores_new['scores_all'].
    Output z_<date>_slot<slot>_<ens>.csv (shadow files; the live file stays z_<date>_slot15.csv)."""
    sets = ENSEMBLES[ens]; symbols = json.loads((DATASET / 'symbols.json').read_text(encoding='utf-8')); U = len(symbols)
    p = np.load(SC / 'panel_ext.npz', allow_pickle=True); s = np.load(SC / 'scores_new.npz'); q = np.load(ROOT / 'outputs/gru_beta_neutral_20261008/risk_context.npz')
    times_ext = p['times']; clock_ext = pd.to_datetime(times_ext, utc=True).tz_convert(TZ); new_dates = list(p['new_dates']); slow_names = list(p['slow_names'])
    times = np.load(DATASET / 'times.npy'); members = np.load(DATASET / 'members.npy', mmap_mode='r'); tpos = {int(t): i for i, t in enumerate(times)}; qpos = {int(t): i for i, t in enumerate(q['times'])}
    # frozen warm-up rows at this slot
    fro = {k: {'times': np.load(FROZEN / f'{k}.npz')['times'], 'scores': np.load(FROZEN / f'{k}.npz')['scores'][:, :, :, 3].astype(np.float32)} for k in sets}; ft = fro[sets[0]]['times']; assert all(np.array_equal(fro[k]['times'], ft) for k in sets)   # materialised once
    fclock = pd.to_datetime(ft, utc=True).tz_convert(TZ); rows = [i for i in range(len(ft)) if fclock[i].hour == slot][-WARM:]
    last = None
    for i in rows:
        raw = np.mean([zscore(fro[k]['scores'][i, m].astype(float)) for k in sets for m in range(5)], axis=0); t = int(ft[i]); j = qpos[t]; ti = tpos[t]
        expo = np.stack([q['volatility'][j], q['turnover'][j], q['log_cap'][j], q['beta60'][j]], axis=1); row = neutralize(np.where(members[ti], raw, np.nan), expo)
        if last is not None: row = np.where(np.isfinite(row) & np.isfinite(last), .2 * row + .8 * last, row)
        last = row.copy()
    check = dict(ensemble=ens, sets=sets, warmup_sessions=len(rows), warmup_first=str(fclock[rows[0]].date()), warmup_last=str(fclock[rows[-1]].date()))
    idx, idx_rel = index_closes(); close15 = np.asarray(p['close_adj'])[3::4]; dates_ext = [str(d.date()) for d in clock_ext[3::4]]
    stockret = np.full_like(close15, np.nan); ok = np.isfinite(close15[1:]) & np.isfinite(close15[:-1]) & (close15[1:] > 0) & (close15[:-1] > 0); np.divide(close15[1:], close15[:-1], out=stockret[1:], where=ok); stockret[1:] -= 1
    ic = idx.reindex(dates_ext).to_numpy(float); mret = np.full(len(ic), np.nan); mret[1:] = ic[1:] / ic[:-1] - 1
    def beta_at(d):
        y, m = stockret[d - 60:d], mret[d - 60:d, None]; okk = np.isfinite(y) & np.isfinite(m); n = okk.sum(axis=0)
        ym = np.where(okk, y, 0).sum(axis=0) / np.maximum(n, 1); mm = np.where(okk, m, 0).sum(axis=0) / np.maximum(n, 1)
        cov = np.where(okk, (y - ym) * (m - mm), 0).sum(axis=0); var = np.where(okk, (m - mm) ** 2, 0).sum(axis=0); out = np.full(U, np.nan); np.divide(cov, var, out=out, where=(n >= 40) & (var > 1e-12)); return out
    vol_i, to_i, cap_i = [slow_names.index(n) for n in ['d_vol20', 'd_turnover', 'd_log_total_cap']]
    sc = s['scores_all']; labels = [str(x) for x in s['model_set']]; cols = [k for k, l in enumerate(labels) if l in sets]; assert len(cols) == 5 * len(sets), 'scores_new lacks the requested model sets'
    stimes = s['times']; sclock = pd.to_datetime(stimes, utc=True).tz_convert(TZ); mem_ext = p['members']; tix = {int(t): i for i, t in enumerate(times_ext)}; outputs = {}
    for j, t in enumerate(stimes):
        day = str(sclock[j].date())
        if sclock[j].hour != slot or day not in new_dates: continue
        raw = np.mean([zscore(sc[j, k, :, 3].astype(float)) for k in cols], axis=0)
        di = new_dates.index(day); slow = p['slow_new'][di]; d_ext = dates_ext.index(day); t_ext = tix[int(t)]
        expo = np.stack([slow[:, vol_i], slow[:, to_i], slow[:, cap_i], beta_at(d_ext)], axis=1).astype(float); row = neutralize(np.where(mem_ext[t_ext], raw, np.nan), expo)
        if last is not None: row = np.where(np.isfinite(row) & np.isfinite(last), .2 * row + .8 * last, row)
        last = row.copy(); z = zscore(np.where(mem_ext[t_ext], row, np.nan))
        df = pd.DataFrame(dict(date=day, code=symbols, z=z)); df = df[np.isfinite(df.z)]; df.to_csv(OUT / f'z_{day}_slot{slot}_{ens}.csv', index=False)
        outputs[day] = dict(names=int(len(df)), z_abs_mean=float(df.z.abs().mean()), scored=int(np.isfinite(raw).sum()))
    check['new_sessions'] = outputs; (OUT / f'chain_check_slot{slot}_{ens}.json').write_text(json.dumps(check, ensure_ascii=False, indent=1, default=str), encoding='utf-8'); print(json.dumps(check, ensure_ascii=False, default=str))


if __name__ == '__main__':
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 15, sys.argv[2] if len(sys.argv) > 2 else 'ctrl5')
