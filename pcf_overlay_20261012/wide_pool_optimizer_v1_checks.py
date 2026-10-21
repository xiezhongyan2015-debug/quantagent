"""WIDE-POOL v1 CHECKS (2026-10-15, registered after v1 ran; v1 outputs untouched).
Hardest robustness check from the v4 audit, repeated on the wide pool: the c5 exclusion rule's turnover-bucketed
percentile was added after 2026-01..05 had been seen.  Here the exclusion is recomputed WITHOUT turnover buckets
(percentile of score_pct within the CSI500 bucket, hysteresis 0.20 in / 0.35 out, cold start at the first date),
plus the file's `vetoed` names, and applied as (a) zero weights on the index skeleton (WVnb0) and (b) hard zeros
inside the mentor-limit optimizer with the blend signal (WVnb_blend).  Everything else identical to v1.
"""
import json, time
import numpy as np
import pandas as pd
from scipy.special import ndtri
from scipy.stats import rankdata
from .data import ROOT, OUT, END, write, sha
from . import replay as R
from .optimizer_overlay_v4 import RiskModel, VAL_IC, C5_PATH
from . import wide_pool_optimizer_v1 as V1

DEST = OUT/'wide_pool_optimizer_v1_checks'
PROTOCOL = dict(version='wide_pool_optimizer_v1_checks', registered='2026-10-15', base='wide_pool_optimizer_v1 (frozen)',
                check='exclusion recomputed without turnover buckets (score_pct percentile within CSI500 bucket, 0.20/0.35 hysteresis, cold start) + file vetoed',
                arms=['WVnb0 (skeleton with no-bucket zeros)', 'WVnb_blend (W_blend limits + no-bucket hard zeros)'], comparisons='vs I0, vs WV0, vs WV_blend (v1 NAVs)')


def main():
    DEST.mkdir(exist_ok=True); p = DEST/'protocol.json'
    if p.exists(): assert json.loads(p.read_text()) == json.loads(json.dumps(PROTOCOL))
    else: write(p, PROTOCOL)
    t0 = time.time()
    prices = pd.read_parquet(OUT/'daily_prices.parquet'); symbols = json.load(open(OUT/'price_symbols.json')); U = len(symbols)
    historic = pd.read_parquet(ROOT/'outputs/cluster_sync_20261011/teammate_latest_uploads/unpacked/pcf_alpha_materials_20261011/benchmarks/H00905_daily.parquet')[['date', 'close']]
    recent = pd.read_csv(ROOT/'outputs/gru_beta_neutral_20261008/benchmark_sources/H00905_daily.csv')[['date', 'close']]
    for f in (historic, recent): f['date'] = pd.to_datetime(f.date).dt.strftime('%Y-%m-%d')
    benchmark = pd.concat([historic, recent]).drop_duplicates('date', keep='last').set_index('date').close.astype(float)
    pricebench = pd.read_csv(ROOT/'outputs/gru_beta_neutral_20261008/benchmark_sources/000905_daily.csv').set_index('date').close
    calendar = sorted(set(benchmark.index) & set(pricebench.index) & set(prices.date)); all_days = [d for d in calendar if '2023-03-01' <= d <= END]
    wide = lambda field: prices.pivot(index='date', columns='symbol', values=field).reindex(index=all_days, columns=symbols).apply(pd.to_numeric, errors='coerce').to_numpy(dtype=float)
    op = wide('open_adjusted'); close = wide('close_adjusted'); amount = wide('amount'); volume = wide('volume'); valid = wide('valid') == 1; isST = wide('isST')
    can_same = valid & (volume > 0) & (isST != 1); can = np.vstack([can_same[:1], can_same[:-1]])
    ret = np.full_like(close, np.nan); ret[1:] = close[1:] / close[:-1] - 1
    bench_ret = benchmark.loc[all_days].to_numpy(); bench_ret = np.r_[np.nan, bench_ret[1:] / bench_ret[:-1] - 1]
    lookup = {s: i for i, s in enumerate(symbols)}; daypos = {d: i for i, d in enumerate(all_days)}
    DATA = ROOT/'gru_research/artifacts/dataset'; times = np.load(DATA/'times.npy'); clock = pd.to_datetime(times, utc=True).tz_convert('Asia/Shanghai')
    q = np.load(ROOT/'outputs/gru_beta_neutral_20261008/risk_context.npz'); model_symbols = json.load(open(DATA/'symbols.json'))
    mapping = np.array([lookup[s] for s in model_symbols]); by15 = {str(clock[i].date()): i for i in np.flatnonzero(clock.hour == 15)}
    industry = np.full((len(all_days), U), -1, int); logcap = np.full((len(all_days), U), np.nan)
    for j, d in enumerate(all_days):
        if d in by15: industry[j, mapping] = q['industry'][by15[d]]; logcap[j, mapping] = q['log_cap'][by15[d]]
    frozen_member = np.load(DATA/'members.npy', mmap_mode='r'); labels_h = np.load(DATA/'labels.npy', mmap_mode='r')
    s15, h15 = R.signals(15, all_days, symbols)
    c5 = pd.read_parquet(C5_PATH); c5 = c5[c5.segment != 'live']; s_c5 = {}; veto_nb = {}; state_nb = {}
    for day, g in c5.groupby('date'):
        z = np.full(U, np.nan); gg = g[g.code.isin(lookup.keys())]
        z[[lookup[c] for c in gg.code]] = ndtri(np.clip(gg.score_pct.to_numpy(), 1e-4, 1 - 1e-4)); s_c5[day] = z
        b5 = gg[gg.bucket == 'csi500']; pct = b5.score_pct.rank(pct=True); vn = np.zeros(U, bool)
        for code, qq in zip(b5.code, pct):
            prev = state_nb.get(code, None); ex = (qq < .20) if prev is None else (True if (prev and qq <= .35) else (qq < .20))
            state_nb[code] = ex
            if ex: vn[lookup[code]] = True
        vn[[lookup[c] for c in gg[gg.state == 'vetoed'].code]] = True; veto_nb[day] = vn
    def blend(day):
        a = s15.get(day); b = s_c5.get(day)
        if a is None: return b
        if b is None: return a
        return np.where(np.isfinite(a) & np.isfinite(b), (a + b) / 2, np.where(np.isfinite(a), a, b))
    fc = pd.read_parquet(V1.FREECAP); fc.index = pd.to_datetime(fc.index).strftime('%Y-%m-%d'); fc.columns = [c[2:] + '.' + c[:2] for c in fc.columns]
    mult = pd.read_parquet(V1.MULT); mult.index = pd.to_datetime(mult.index).strftime('%Y-%m-%d'); mult.columns = [c[2:] + '.' + c[:2] for c in mult.columns]
    fc = fc.reindex(columns=symbols); mult = mult.reindex(columns=symbols)
    risk = RiskModel(all_days, symbols, ret, amount, logcap, industry, bench_ret); X_by_day = {}; cov_by_day = {}; sectors_by_day = {}; X_prev = None
    for j in range(len(all_days)):
        if X_prev is not None: risk.update(j, X_prev)
        X = risk.exposures(j); X_prev = X; X_by_day[j] = X; cov_by_day[j] = risk.covariance(); sectors_by_day[j] = X[:, 1:1 + risk.K_ind]
    v1_dates = pd.read_csv(V1.DEST/'I0_daily.csv').date.tolist()
    dates = []; refs = []; vetos = []; zs = []; signal_marks = []; fills = []; execution_prices = []; closes = []; uni_ids = []; members = []; Xd = {}; Cd = {}; Sd = {}; last_c5 = None
    for day in v1_dates:
        j = daypos[day]; prior = all_days[j - 1]
        if prior in s_c5: last_c5 = prior
        else: s_c5[prior] = s_c5[last_c5]; veto_nb[prior] = veto_nb[last_c5]
        i15 = by15[prior]; mem = np.zeros(U, bool); mem[mapping] = frozen_member[i15]; ids = np.flatnonzero(mem)
        f = fc.loc[prior].to_numpy(dtype=float) if prior in fc.index else np.full(U, np.nan)
        g = mult.loc[prior].to_numpy(dtype=float) if prior in mult.index else np.full(U, np.nan); g = np.where(np.isfinite(g), g, 1.)
        w = np.where(mem & np.isfinite(f) & (f > 0), f * g, np.nan); have = mem & np.isfinite(w); miss = mem & ~np.isfinite(w)
        cap = np.exp(np.nan_to_num(logcap[j - 1], nan=-np.inf))
        if miss.any():
            ok = have & np.isfinite(cap) & (cap > 0); ratio = np.median(w[ok] / cap[ok]) if ok.sum() > 10 else 1.
            fillv = cap[miss] * ratio; w[miss] = np.where(np.isfinite(fillv) & (fillv > 0), fillv, np.nan)
        w = np.where(mem & np.isfinite(w), w, 0.); reference = w / w.sum() * V1.INVESTED
        k = len(dates); dates.append(day); refs.append(reference); vetos.append(veto_nb[prior] & mem); zs.append(blend(prior))
        signal_marks.append(close[j - 1]); fills.append(can[j]); execution_prices.append(op[j]); closes.append(close[j]); uni_ids.append(ids); members.append(mem)
        Xd[k] = X_by_day[j - 1]; Cd[k] = cov_by_day[j - 1]; Sd[k] = sectors_by_day[j - 1]
    refs = np.asarray(refs); closes = np.asarray(closes); vetos = np.asarray(vetos); zs = np.asarray(zs); members = np.asarray(members)
    # same reference weights as v1 (check against v1 audit max weights)
    aud = pd.read_csv(V1.DEST/'benchmark_proxy_audit.csv'); assert np.allclose(aud.max_weight.to_numpy(), refs.max(axis=1)), 'reference weights differ from v1'
    daily_ic = np.full(len(dates), np.nan); ic_prior = []
    for e, day in enumerate(dates):
        prior = all_days[daypos[day] - 1]; i = by15[prior]; y = np.full(U, np.nan); y[mapping] = labels_h[i, :, 3]; m = members[e]; ic_prior.append(VAL_IC[int(prior[:4])])
        s = zs[e]; ok = m & np.isfinite(s) & np.isfinite(y)
        if ok.sum() >= 100: daily_ic[e] = np.corrcoef(rankdata(s[ok]), rankdata(y[ok]))[0, 1]
    ser = pd.Series(daily_ic).shift(2).rolling(250, min_periods=60).mean().to_numpy(); ic_trail = list(np.where(np.isfinite(ser), ser, np.asarray(ic_prior)))
    b = benchmark.loc[dates].to_numpy(); pb = pricebench.loc[dates].to_numpy()
    V1.ARMS['WVnb_blend'] = dict(V1.BASE, signal='blend', veto=True)
    results = {}; scores = []
    for arm, kind in [('WVnb0', 'WV0'), ('WVnb_blend', 'WVnb_blend')]:
        t1 = time.time()
        d, diag = V1.simulate(kind, dates, refs, zs, vetos, np.asarray(signal_marks), np.asarray(execution_prices), np.asarray(fills), closes, symbols, Xd, Cd, Sd, uni_ids, members, ic_trail)
        d['H00905_nav'] = b / b[0]; d['price_index_nav'] = pb / pb[0]; d.to_csv(DEST/f'{arm}_daily.csv', index=False)
        if len(diag): diag.to_csv(DEST/f'{arm}_optimizer.csv', index=False)
        rec = R.summary(d, b, pb); rec['arm'] = arm; rec['seconds'] = round(time.time() - t1); rec['zeros_mean'] = float(vetos.sum(axis=1).mean()); rec['zero_weight_mean'] = float((refs * vetos).sum(axis=1).mean())
        if len(diag) and 'status' in diag: rec['optimizer_failed_days'] = int(diag['fallback_hold_skeleton'].fillna(False).astype(bool).sum()) if 'fallback_hold_skeleton' in diag else 0
        scores.append(rec); results[arm] = d; print('ARM_COMPLETE', arm, round(rec['annualized_relative_excess'], 4), flush=True)
    for a in ['I0', 'WV0', 'WV_blend', 'I1_blend']:
        dv = pd.read_csv(V1.DEST/f'{a}_daily.csv'); assert dv.date.tolist() == dates; results[a] = dv
    comp = []
    for x, y in [('WVnb0', 'I0'), ('WVnb0', 'WV0'), ('WVnb_blend', 'I0'), ('WVnb_blend', 'WVnb0'), ('WVnb_blend', 'WV_blend'), ('WVnb0', 'I1_blend')]:
        st, hv = V1.paired(results[x].nav.to_numpy(), results[y].nav.to_numpy(), np.asarray(dates), 8); comp.append(dict(arm=x, versus=y, **st, **{k: round(v, 2) for k, v in hv.items()}))
    pd.DataFrame(scores).to_csv(DEST/'scorecard.csv', index=False); pd.DataFrame(comp).to_csv(DEST/'paired_comparisons.csv', index=False)
    write(DEST/'run_receipt.json', dict(code_sha256=sha(__file__), v1_code_sha256=sha(V1.__file__), seconds=round(time.time() - t0)))
    pd.set_option('display.width', 250); print(pd.DataFrame(scores)[['arm', 'annualized_relative_excess', 'tracking_error', 'information_ratio', 'average_turnover', 'zeros_mean', 'zero_weight_mean']].round(4).to_string(index=False))
    print(pd.DataFrame(comp).round(4).to_string(index=False))


if __name__ == '__main__':
    main()
