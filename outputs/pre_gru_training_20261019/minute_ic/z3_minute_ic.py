"""Z3 diagnostic (CPU only, read-only on every input outside this folder).

Question: is the minute-statistics increment (RANK_STATS - RANK_NULL) and the hourly production GRU signal paid in the
overnight gap or in the next-day session, how fast does it decay, and does the minute increment survive a 2024 residual
IC gate after controlling the production signal and the four risk exposures?

Run:  .pcf-research-venv/bin/python outputs/pre_gru_training_20261019/minute_ic/z3_minute_ic.py
Needs scores_2024/{wf2025,wf2026,oldrank}.npz produced by score_2024_models.py in this folder (bit-exact copy of the
live scoring code; parity with scores_frozen checked on 2025-01-02..03, max |diff| = 0).
"""
from pathlib import Path
import glob, hashlib, json, sys, time
import numpy as np
import pandas as pd
from scipy.stats import rankdata

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from gru_beta_neutral_20261008.replay import neutralize  # noqa: E402

DATA = ROOT / 'gru_research/artifacts/dataset'
RANK = ROOT / 'outputs/cluster_results_20261012_16_sync_20261017/snapshot/DATA/gru_rank_minute_20261015'
FROZEN = ROOT / 'outputs/gru_live_extend_20261012/scores_frozen'
S2024 = OUT / 'scores_2024'
IFIND = ROOT / '中证500_历年成分股_日频_2010-2026_iFinD_后复权/bars'
BAOSTOCK = ROOT / 'outputs/pcf_overlay_20261012/daily_prices.parquet'
RISK = ROOT / 'outputs/gru_beta_neutral_20261008/risk_context.npz'
TZ = 'Asia/Shanghai'
SEEDS_RANK = [66, 77, 88, 99, 111]
YEARS = ['2024', '2025', '2026']
NW_LAG = 5
MIN_N = 100
SOURCES = []


def record(path, hash_it=True):
    path = Path(path)
    row = {'path': str(path.relative_to(ROOT)), 'bytes': path.stat().st_size}
    if hash_it:
        row['sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
    SOURCES.append(row)
    return path


def zrow(a, mask=None):
    a = np.asarray(a, float).copy()
    if mask is not None:
        a[~mask] = np.nan
    out = np.full_like(a, np.nan)
    ok = np.isfinite(a)
    if ok.sum() > 1 and a[ok].std() > 1e-12:
        out[ok] = (a[ok] - a[ok].mean()) / a[ok].std()
    return out


def rank_ic(s, r, mask):
    ok = mask & np.isfinite(s) & np.isfinite(r)
    if ok.sum() < MIN_N:
        return np.nan
    a, b = rankdata(s[ok]), rankdata(r[ok])
    if a.std() < 1e-12 or b.std() < 1e-12:
        return np.nan
    return float(np.corrcoef(a, b)[0, 1])


def nw_t(x, lag=NW_LAG):
    x = np.asarray(x, float)
    x = x[np.isfinite(x)]
    n = len(x)
    if n < 10:
        return np.nan
    e = x - x.mean()
    s = e @ e / n
    for l in range(1, lag + 1):
        s += 2 * (1 - l / (lag + 1)) * (e[l:] @ e[:-l]) / n
    return float(x.mean() / np.sqrt(s / n)) if s > 0 else np.nan


def summarize(x):
    x = np.asarray(x, float)
    x = x[np.isfinite(x)]
    n = len(x)
    if n < 10:
        return dict(n_days=n, mean=np.nan, sd=np.nan, t=np.nan, nw_t5=np.nan, pos_share=np.nan)
    return dict(n_days=n, mean=float(x.mean()), sd=float(x.std(ddof=1)), t=float(x.mean() / x.std(ddof=1) * np.sqrt(n)),
                nw_t5=nw_t(x), pos_share=float((x > 0).mean()))


def load_daily(days, symbols):
    """iFinD CPS:3 hfq open/close (primary) and baostock adjusted open/close (fallback for non-member days)."""
    U = len(symbols); dpos = {d: i for i, d in enumerate(days)}; spos = {s: i for i, s in enumerate(symbols)}
    O = np.full((len(days), U), np.nan); C = O.copy()
    files = sorted(f for f in glob.glob(str(IFIND / '*/*/*.parquet')) if Path(f).parent.name >= '2023-10')
    for f in files:
        record(f, hash_it=False)
        d = pd.read_parquet(f, columns=['thscode', 'date', 'open', 'close'])
        d = d[d.date.isin(dpos) & d.thscode.isin(spos)]
        i = d.date.map(dpos).to_numpy(); j = d.thscode.map(spos).to_numpy()
        O[i, j] = d.open.to_numpy(float); C[i, j] = d.close.to_numpy(float)
    b = pd.read_parquet(record(BAOSTOCK), columns=['date', 'symbol', 'open_adjusted', 'close_adjusted', 'valid', 'volume'])
    b = b[b.valid.astype(bool) & (b.volume > 0) & b.date.isin(dpos) & b.symbol.isin(spos)]
    OB = np.full_like(O, np.nan); CB = OB.copy()
    i = b.date.map(dpos).to_numpy(); j = b.symbol.map(spos).to_numpy()
    OB[i, j] = b.open_adjusted.to_numpy(float); CB[i, j] = b.close_adjusted.to_numpy(float)
    for a in (O, C, OB, CB):
        a[~(a > 0)] = np.nan
    return O, C, OB, CB


def ratio(num_a, den_a, num_b, den_b):
    """Return ratio-1 from source A; where A cannot form it, from source B (never mixing sources inside one return)."""
    ra = num_a / den_a - 1
    rb = num_b / den_b - 1
    out = np.where(np.isfinite(ra), ra, rb)
    return out, float(np.isfinite(ra).sum()), float((~np.isfinite(ra) & np.isfinite(rb)).sum())


def shift(a, k):
    out = np.full_like(a, np.nan)
    if k == 0:
        return a.copy()
    out[:-k] = a[k:]
    return out


def production_signal(times, members, q, slot_hour, sets):
    """Exact chain of pcf_overlay_20261012/ensemble_replay.build_signals: per-seed z of head y4 -> mean over models ->
    OLS neutralisation (vol rank, turnover rank, log cap, beta60) -> EMA .2 over this slot's sessions -> member z.
    2023-11..2024-12 from scores_2024 (local CPU rerun of the same models), 2025-01.. from scores_frozen."""
    qpos = {int(t): i for i, t in enumerate(q['times'])}; tpos = {int(t): i for i, t in enumerate(times)}
    parts = {}
    for k in sets:
        a = np.load(record(S2024 / f'{k}.npz', hash_it=False)); b = np.load(record(FROZEN / f'{k}.npz', hash_it=False))
        assert a['times'][-1] < b['times'][0]
        parts[k] = (np.concatenate([a['times'], b['times']]), np.concatenate([a['scores'][:, :, :, 3], b['scores'][:, :, :, 3]]).astype(np.float32))
    ft = parts[sets[0]][0]
    assert all(np.array_equal(parts[k][0], ft) for k in sets)
    clock = pd.to_datetime(ft, utc=True).tz_convert(TZ)
    out = {}; raw_out = {}; last = None
    for i in np.flatnonzero(clock.hour == slot_hour):
        t = int(ft[i]); ti = tpos[t]; j = qpos[t]; mem = np.asarray(members[ti])
        raw = np.mean([zrow(parts[k][1][i, m]) for k in sets for m in range(5)], axis=0)
        expo = np.stack([q['volatility'][j], q['turnover'][j], q['log_cap'][j], q['beta60'][j]], axis=1)
        row = neutralize(np.where(mem, raw, np.nan), expo)
        if last is not None:
            row = np.where(np.isfinite(row) & np.isfinite(last), .2 * row + .8 * last, row)
        last = row.copy()
        out[t] = zrow(row, mem); raw_out[t] = zrow(raw, mem)
    return out, raw_out


def design(expo_row, extra=None, industry=None):
    """neutralize()'s exposure design (rank vol/turnover, standardise all) plus optional extra columns / industry dummies."""
    x = expo_row.copy()
    x[:, 0] = pd.Series(x[:, 0]).rank(pct=True).to_numpy()
    x[:, 1] = pd.Series(x[:, 1]).rank(pct=True).to_numpy()
    cols = [x] + ([extra] if extra is not None else [])
    x = np.column_stack(cols)
    sc = x.std(axis=0)
    x = (x - x.mean(axis=0)) / np.where(sc > 1e-12, sc, 1)
    x = np.c_[np.ones(len(x)), x]
    if industry is not None:
        codes = pd.Series(industry).astype('category')
        dummies = pd.get_dummies(codes, drop_first=True).to_numpy(float)
        x = np.c_[x, dummies]
    return x


def residualize(y, expo, mask, extra=None, industry=None):
    ok = mask & np.isfinite(y) & np.isfinite(expo).all(axis=1)
    if extra is not None:
        ok &= np.isfinite(extra).all(axis=1) if extra.ndim == 2 else np.isfinite(extra)
    if industry is not None:
        ok &= np.isfinite(industry)
    out = np.full(len(y), np.nan)
    if ok.sum() < MIN_N:
        return out
    ex = None if extra is None else (extra[ok] if extra.ndim == 2 else extra[ok][:, None])
    x = design(expo[ok], ex, None if industry is None else industry[ok])
    out[ok] = y[ok] - x @ np.linalg.lstsq(x, y[ok], rcond=None)[0]
    return out


def main():
    t0 = time.time()
    times = np.load(record(DATA / 'times.npy')); symbols = json.loads(record(DATA / 'symbols.json').read_text())
    members = np.load(record(DATA / 'members.npy'), mmap_mode='r')
    labels = np.load(record(DATA / 'labels.npy'), mmap_mode='r')
    hour = np.load(record(DATA / 'hour_ohlcv.npy', hash_it=False), mmap_mode='r')
    exec_raw = np.load(record(DATA / 'execution_raw_prices.npy'), mmap_mode='r')
    q = dict(np.load(record(RISK), allow_pickle=True))
    assert np.array_equal(q['times'], times)
    clock = pd.to_datetime(times, utc=True).tz_convert(TZ)
    assert (clock.hour[3::4] == 15).all() and (clock.hour[2::4] == 14).all() and (clock.hour[0::4] == 10).all()
    days = [str(c.date()) for c in clock[3::4]]; ND = len(days); U = len(symbols)
    dpos = {d: i for i, d in enumerate(days)}

    # ---------- signals at 15:00 ----------
    raw = {}
    sig_times = None
    for arm in ['RANK_NULL', 'RANK_STATS']:
        zs = []
        for seed in SEEDS_RANK:
            z = np.load(record(RANK / f'inference/raw_predictions/{arm}_seed{seed}_head4.npz'))
            assert list(z['symbols']) == symbols
            if sig_times is None:
                sig_times = z['times']
            assert np.array_equal(z['times'], sig_times)
            zs.append(np.stack([zrow(z['head4_scores'][k], np.asarray(members[np.searchsorted(times, sig_times[k])]))
                                for k in range(len(sig_times))]))
        raw[arm] = np.mean(zs, axis=0)
    sig_ix = np.searchsorted(times, sig_times); assert np.array_equal(times[sig_ix], sig_times) and (sig_ix % 4 == 3).all()
    sig_days = sig_ix // 4
    H15, H15raw = production_signal(times, members, q, 15, ['wf2025', 'wf2026', 'oldrank'])
    H10, _ = production_signal(times, members, q, 15, ['wf2025', 'oldrank'])
    H14, _ = production_signal(times, members, q, 14, ['wf2025', 'wf2026', 'oldrank'])
    S = {'S_null': [], 'S_stats': [], 'D': [], 'H': [], 'H_raw_preneutral': [], 'H_his10': []}
    for k, t in enumerate(sig_ix):
        mem = np.asarray(members[t])
        sn, ss = zrow(raw['RANK_NULL'][k], mem), zrow(raw['RANK_STATS'][k], mem)
        S['S_null'].append(sn); S['S_stats'].append(ss); S['D'].append(ss - sn)
        S['H'].append(H15[int(times[t])]); S['H_raw_preneutral'].append(H15raw[int(times[t])]); S['H_his10'].append(H10[int(times[t])])
    S = {k: np.asarray(v) for k, v in S.items()}

    # ---------- returns ----------
    O, C, OB, CB = load_daily(days, symbols)
    cov = {}
    r_gap, a, b = ratio(shift(O, 1), C, shift(OB, 1), CB); cov['r_gap'] = (a, b)
    r_day, a, b = ratio(shift(C, 1), shift(O, 1), shift(CB, 1), shift(OB, 1)); cov['r_day'] = (a, b)
    r_cc, a, b = ratio(shift(C, 1), C, shift(CB, 1), CB); cov['r_cc'] = (a, b)
    oo = {}
    for k in range(1, 11):
        oo[k], a, b = ratio(shift(O, k + 1), shift(O, k), shift(OB, k + 1), shift(OB, k)); cov[f'r_oo_lag{k}'] = (a, b)
    hraw_open = np.asarray(hour[0::4, :, 5], float); hraw_close = np.asarray(hour[3::4, :, 8], float)
    p14 = np.asarray(exec_raw[2::4], float)  # 14:01-14:05 mean raw open of the same day (exec window after the 14:00 signal)
    r_tail = hraw_close / p14 - 1                     # day t 14:01 window -> 15:00 close (same-day raw ratio)
    r_morn = shift(p14, 1) / shift(hraw_open, 1) - 1  # day t+1 open -> 14:01 window (same-day raw ratio)
    y4_15 = np.asarray(labels[3::4, :, 3], float); y4_14 = np.asarray(labels[2::4, :, 3], float)
    y1_14 = np.asarray(labels[2::4, :, 0], float)

    # sanity: r_oo vs y4(15:00), segment sums vs y4(14:00)
    sanity = {}
    sel = sig_days[:-3]
    mm = np.asarray(members[sig_ix[:-3]])
    ok = mm & np.isfinite(oo[1][sel]) & np.isfinite(y4_15[sel])
    sanity['corr_r_oo_vs_y4_15'] = float(np.corrcoef(np.log1p(oo[1][sel][ok]), y4_15[sel][ok])[0, 1])
    seg = np.log1p(r_tail) + np.log1p(r_gap) + np.log1p(r_morn)
    ok = mm & np.isfinite(seg[sel]) & np.isfinite(y4_14[sel])
    sanity['corr_tail+gap+morn_vs_y4_14'] = float(np.corrcoef(seg[sel][ok], y4_14[sel][ok])[0, 1])
    sanity['mean_abs_diff_tail+gap+morn_minus_y4_14'] = float(np.mean(np.abs(seg[sel][ok] - y4_14[sel][ok])))
    hb = np.asarray(hour[3::4, :, 3], float) / np.asarray(hour[0::4, :, 0], float) - 1
    ok = mm & np.isfinite(hb[sel]) & np.isfinite((C / O - 1)[sel])
    sanity['corr_hour_based_vs_ifind_same_day_open_to_close'] = float(np.corrcoef(hb[sel][ok], (C / O - 1)[sel][ok])[0, 1])
    sanity['return_coverage_ifind_vs_baostock_fill'] = {k: {'ifind_cells': v[0], 'baostock_filled_cells': v[1]} for k, v in cov.items()}

    targets15 = {'r_gap': r_gap, 'r_day': r_day, 'r_oo': oo[1], 'r_cc': r_cc, 'y4': y4_15}
    year_of = np.array([days[d][:4] for d in sig_days])

    # ---------- 1. three-segment RankIC at 15:00 ----------
    daily_rows, seg_rows = [], []
    for k, (t, d) in enumerate(zip(sig_ix, sig_days)):
        mem = np.asarray(members[t]); row = {'date': days[d]}
        for s in S:
            for nm, r in targets15.items():
                row[f'{s}|{nm}'] = rank_ic(S[s][k], r[d], mem)
        row['corr_D_H'] = rank_ic(S['D'][k], S['H'][k], mem)
        row['corr_Snull_Sstats'] = rank_ic(S['S_null'][k], S['S_stats'][k], mem)
        daily_rows.append(row)
    daily = pd.DataFrame(daily_rows); daily.to_csv(OUT / 'daily_rankic_15.csv', index=False)
    for s in S:
        for nm in targets15:
            for y in YEARS + ['2024-2026']:
                v = daily[f'{s}|{nm}'] if y == '2024-2026' else daily.loc[year_of == y, f'{s}|{nm}']
                seg_rows.append(dict(signal=s, target=nm, year=y, **summarize(v)))
    for nm in ['corr_D_H', 'corr_Snull_Sstats']:
        for y in YEARS:
            seg_rows.append(dict(signal=nm, target='cross_section_rank_corr', year=y, **summarize(daily.loc[year_of == y, nm])))
    seg = pd.DataFrame(seg_rows); seg.to_csv(OUT / 'segment_rankic_15.csv', index=False)

    # ---------- 2. decay: RankIC vs open-to-open return k days ahead ----------
    dec_rows = []
    for s in ['H', 'D', 'S_null', 'S_stats']:
        ics = {k: [] for k in range(1, 11)}
        for kk, (t, d) in enumerate(zip(sig_ix, sig_days)):
            mem = np.asarray(members[t])
            for k in range(1, 11):
                ics[k].append(rank_ic(S[s][kk], oo[k][d], mem))
        for k in range(1, 11):
            v = np.asarray(ics[k])
            for y in YEARS + ['2024-2026']:
                vv = v if y == '2024-2026' else v[year_of == y]
                dec_rows.append(dict(signal=s, lag=k, window=f'open(t+{k})->open(t+{k + 1})', year=y, **summarize(vv)))
    pd.DataFrame(dec_rows).to_csv(OUT / 'decay_rankic_15.csv', index=False)

    # ---------- 3. residual IC gate ----------
    qpos = {int(t): i for i, t in enumerate(q['times'])}
    variants = {
        'D_raw': lambda k, e, m, ind: S['D'][k],
        'D_resid_4expo': lambda k, e, m, ind: residualize(S['D'][k], e, m),
        'D_resid_H+4expo [GATE]': lambda k, e, m, ind: residualize(S['D'][k], e, m, extra=S['H'][k]),
        'D_resid_H+4expo+industry': lambda k, e, m, ind: residualize(S['D'][k], e, m, extra=S['H'][k], industry=ind),
        'D_resid_Hhis10+4expo': lambda k, e, m, ind: residualize(S['D'][k], e, m, extra=S['H_his10'][k]),
        'D_resid_H+Snull+4expo': lambda k, e, m, ind: residualize(S['D'][k], e, m, extra=np.column_stack([S['H'][k], S['S_null'][k]])),
        'Sstats_resid_H+4expo': lambda k, e, m, ind: residualize(S['S_stats'][k], e, m, extra=S['H'][k]),
        'Snull_resid_H+4expo': lambda k, e, m, ind: residualize(S['S_null'][k], e, m, extra=S['H'][k]),
    }
    gate_daily = {v: {nm: [] for nm in ['r_oo', 'y4', 'r_gap', 'r_day']} for v in variants}
    # 3b. veto-style test: excess return of the bottom x% by the signal relative to the member mean (same day, same set)
    veto_vars = ['D_raw', 'D_resid_H+4expo [GATE]', 'Sstats_resid_H+4expo']
    veto_daily = {(v, x, nm): [] for v in veto_vars for x in (5, 10, 20) for nm in ('r_oo', 'y4')}
    for k, (t, d) in enumerate(zip(sig_ix, sig_days)):
        mem = np.asarray(members[t]); j = qpos[int(times[t])]
        expo = np.stack([q['volatility'][j], q['turnover'][j], q['log_cap'][j], q['beta60'][j]], axis=1).astype(float)
        ind = q['industry'][j].astype(float); ind[ind < 0] = np.nan
        for v, f in variants.items():
            res = f(k, expo, mem, ind)
            for nm in gate_daily[v]:
                gate_daily[v][nm].append(rank_ic(res, targets15[nm][d], mem))
            if v in veto_vars:
                for nm in ('r_oo', 'y4'):
                    r = targets15[nm][d]; ok = mem & np.isfinite(res) & np.isfinite(r)
                    for x in (5, 10, 20):
                        if ok.sum() < MIN_N:
                            veto_daily[(v, x, nm)].append(np.nan); continue
                        cut = np.quantile(res[ok], x / 100)
                        veto_daily[(v, x, nm)].append(float(r[ok][res[ok] <= cut].mean() - r[ok].mean()))
    veto_rows = []
    for (v, x, nm), arr in veto_daily.items():
        arr = np.asarray(arr)
        for y in YEARS:
            veto_rows.append(dict(variant=v, bottom_pct=x, target=nm, year=y, metric='bottom-minus-member-mean daily return',
                                  **summarize(arr[year_of == y])))
    pd.DataFrame(veto_rows).to_csv(OUT / 'veto_bottom_tail.csv', index=False)
    gate_rows = []
    gd = {'date': [days[d] for d in sig_days]}
    for v in variants:
        for nm in gate_daily[v]:
            arr = np.asarray(gate_daily[v][nm]); gd[f'{v}|{nm}'] = arr
            for y in YEARS:
                st = summarize(arr[year_of == y])
                gate_rows.append(dict(variant=v, target=nm, year=y,
                                      status='gate year (selection period of RANK arms)' if y == '2024' else 'already-seen history (exploratory only)',
                                      **st, gate_pass=(bool(np.isfinite(st['nw_t5']) and abs(st['nw_t5']) > 2 and st['mean'] > 0))))
    pd.DataFrame(gd).to_csv(OUT / 'daily_residual_gate.csv', index=False)
    gate = pd.DataFrame(gate_rows); gate.to_csv(OUT / 'residual_gate.csv', index=False)

    # ---------- 4. 14:00 version ----------
    # 4a. production H at 14:00 and 15:00 on the 14:00-horizon segments (all signal days 2024-01-02..)
    h14_rows, h14_daily = [], []
    t14 = {'r_tail(t 14:01->close)': r_tail, 'r_gap': r_gap, 'r_morn(t+1 open->14:01)': r_morn,
           'r_day(t+1)': r_day, 'y4_14 (14:01 t -> 14:01 t+1)': y4_14, 'y1_14 (14:01->next exec window)': y1_14}
    for d in sig_days:
        t = 4 * d + 2; mem = np.asarray(members[t]); row = {'date': days[d]}
        h = H14[int(times[t])]
        for nm, r in t14.items():
            row[f'H14|{nm}'] = rank_ic(h, r[d], mem)
        h14_daily.append(row)
    h14d = pd.DataFrame(h14_daily); h14d.to_csv(OUT / 'daily_rankic_14_H.csv', index=False)
    for nm in t14:
        for y in YEARS + ['2024-2026']:
            v = h14d[f'H14|{nm}'] if y == '2024-2026' else h14d.loc[year_of == y, f'H14|{nm}']
            h14_rows.append(dict(signal='H14 (all15 chain at 14:00)', target=nm, year=y, **summarize(v)))
    pd.DataFrame(h14_rows).to_csv(OUT / 'segment_rankic_14_H.csv', index=False)

    # 4b. RANK_NULL vs RANK_STATS by slot, 2024 validation sections (same best.pt checkpoints as the 15:00 exports)
    vs = []
    for arm in ['RANK_NULL', 'RANK_STATS']:
        for seed in SEEDS_RANK:
            f = record(RANK / f'train/{arm}/control_seed{seed}/validation_sections.csv')
            v = pd.read_csv(f); v['arm'] = arm; v['seed'] = seed; vs.append(v)
    vs = pd.concat(vs)
    piv = vs.groupby(['date', 'slot', 'arm']).rank_ic.mean().unstack('arm')
    piv['diff'] = piv['RANK_STATS'] - piv['RANK_NULL']
    slot_rows = []
    for slot, g in piv.groupby(level='slot'):
        for col in ['RANK_NULL', 'RANK_STATS', 'diff']:
            slot_rows.append(dict(slot=['10:30', '11:30', '14:00', '15:00'][slot], series=col, **summarize(g[col].to_numpy())))
    # check: 15:00 sections reproduce the exported raw predictions
    chk = []
    for arm in ['RANK_NULL']:
        z = np.load(RANK / f'inference/raw_predictions/{arm}_seed66_head4.npz')
        v3 = vs[(vs.arm == arm) & (vs.seed == 66) & (vs.slot == 3)].set_index('time')
        for k in range(0, 242, 11):
            t = int(z['times'][k]); ti = int(np.searchsorted(times, t))
            chk.append(rank_ic(z['head4_scores'][k].astype(float), np.asarray(labels[ti, :, 3], float), np.asarray(members[ti])) - v3.loc[t, 'rank_ic'])
    sanity['validation_sections_vs_exported_15h_max_abs_ic_diff'] = float(np.nanmax(np.abs(chk)))
    pd.DataFrame(slot_rows).to_csv(OUT / 'slot_ic_validation2024_null_vs_stats.csv', index=False)

    # 4c. mechanism proxy: the ten minute-path hour features already in X (both arms have them), 14:00 vs 15:00 rows
    X = np.load(DATA / 'X.npy', mmap_mode='r'); record(DATA / 'X.npy', hash_it=False)
    fnames = json.loads(record(DATA / 'feature_names.json').read_text())
    fidx = list(range(14, 24))
    prox = []
    for slot, label, tg in [(2, '14:00', {'r_tail': r_tail, 'r_gap': r_gap, 'r_morn': r_morn, 'y4_14': y4_14}),
                            (3, '15:00', {'r_gap': r_gap, 'r_day': r_day, 'r_oo': oo[1], 'y4_15': y4_15})]:
        acc = {(f, nm): [] for f in fidx for nm in tg}
        for d in sig_days:
            t = 4 * d + slot; mem = np.asarray(members[t]); x = np.asarray(X[t][:, fidx], float)
            for c, f in enumerate(fidx):
                for nm, r in tg.items():
                    acc[(f, nm)].append(rank_ic(x[:, c], r[d], mem))
        for (f, nm), v in acc.items():
            v = np.asarray(v)
            for y in YEARS + ['2024-2026']:
                vv = v if y == '2024-2026' else v[year_of == y]
                prox.append(dict(signal_slot=label, feature=fnames[f], target=nm, year=y, **summarize(vv)))
    pd.DataFrame(prox).to_csv(OUT / 'minute_feature_proxy_ic.csv', index=False)

    receipt = dict(status='exploratory_read_only_cpu', gpu_used=False, network_used=False, training_started=False,
                   seconds=round(time.time() - t0), signal_days=len(sig_ix), first=days[sig_days[0]], last=days[sig_days[-1]],
                   definitions={
                       'members': 'dataset members.npy at the signal timestamp',
                       'S_null/S_stats': 'per-seed member z of raw head4 (y4) -> mean of 5 seeds -> member z',
                       'D': 'z(S_stats) - z(S_null)',
                       'H': 'production all15 chain (wf2025+wf2026+oldrank): per-seed z of y4 head -> mean -> neutralize(vol rank, turnover rank, log cap, beta60) -> EMA .2 -> member z; 2024 rows rerun locally on CPU',
                       'H_his10': 'same chain with wf2025+oldrank only (no model trained on 2024)',
                       'r_gap': 'open(t+1)/close(t)-1, iFinD CPS3 hfq; baostock adjusted fallback when iFinD lacks the pair',
                       'r_day': 'close(t+1)/open(t+1)-1', 'r_oo': 'open(t+2)/open(t+1)-1', 'r_cc': 'close(t+1)/close(t)-1',
                       'y4': 'dataset label y4 (15:00: 09:31-35 t+1 -> 09:31-35 t+2 five-minute mean; 14:00: 14:01-05 t -> 14:01-05 t+1)',
                       'r_tail': 'raw 15:00 close(t) / raw 14:01-05 mean open(t) - 1', 'r_morn': 'raw 14:01-05 mean(t+1) / raw open(t+1) - 1',
                       'RankIC': f'daily Spearman over members with finite signal and return, n>={MIN_N}',
                       'NW': f'Newey-West Bartlett lag {NW_LAG} t of the daily RankIC mean',
                       'gate': 'residual of D on [1, H, vol rank, turnover rank, log cap, beta60] (standardised, like neutralize); pass if 2024 mean>0 and NW|t|>2 on r_oo'},
                   sanity=sanity, sources=SOURCES)
    (OUT / 'run_receipt.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2, allow_nan=True))
    pd.set_option('display.width', 250); pd.set_option('display.max_rows', 500)
    print(json.dumps(sanity, ensure_ascii=False, indent=1)[:3000])
    print(seg[seg.year != '2024-2026'].pivot_table(index=['signal', 'year'], columns='target', values='mean').round(4))
    print(seg[seg.year != '2024-2026'].pivot_table(index=['signal', 'year'], columns='target', values='nw_t5').round(2))
    print(gate.pivot_table(index=['variant', 'target'], columns='year', values=['mean', 'nw_t5']).round(4))
    print(pd.DataFrame(slot_rows).round(5))
    print('seconds', round(time.time() - t0))


if __name__ == '__main__':
    main()
