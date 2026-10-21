"""WIDE-POOL OPTIMIZER v2 (2026-10-15, registered before simulation; v1 outputs untouched).

Two changes versus v1, both closing caveats the mentor raised:
  1. Benchmark = OFFICIAL CSI500 month-end weights (tushare index_weight via the reseller endpoint, 2018-01..2026-09),
     drifted by adjusted close from the snapshot date to the signal date and renormalised; universe = names in the
     latest snapshot on/before the signal date.  Replaces v1's free-float x implied-multiplier proxy (TE 2.0%).
  2. Risk model = self-built Barra CNE5-style model (barra_cne5_local: 10 styles, 30 证监会 industry groups, country;
     EWMA/Newey-West factor covariance at the latest month-end on/before the signal date; EWMA specific risk).
     Replaces v1's 5-factor proxy.  Style limits now apply to all 10 CNE5 styles (±0.30σ; WS arm ±0.10σ).
Everything else identical to v1: signals, alpha scale, mentor limits (stock ±0.5%, industry ±1%, TE 3%, overlay
turnover 15%/day, λ=20, cost .001), execution, fee, calendar (v4 events 2024-06-04..2026-09-17), arms, relaxation
ladder, statistics.  Names without CNE5 exposures: styles 0, industry UNKNOWN, specific var = cross-sectional median.
"""
from pathlib import Path
import json, time, os
import numpy as np
import pandas as pd
import cvxpy as cp
from scipy.special import ndtri
from scipy.stats import rankdata
from .data import ROOT, OUT, END, write, sha
from . import replay as R
from .optimizer_overlay_v4 import VAL_IC, C5_PATH
from . import wide_pool_optimizer_v1 as V1

DEST = OUT/'wide_pool_optimizer_v2'; V4 = OUT/'optimizer_overlay_v4'
OFFICIAL = ROOT/'outputs/csi500_index_weight_tushare_official_monthly.csv'
CNE5 = ROOT/'outputs/barra_cne5_local'
STYLES = ['SIZE', 'BETA', 'MOMENTUM', 'RESVOL', 'NLSIZE', 'BTOP', 'LIQUIDITY', 'EARNYLD', 'GROWTH', 'LEVERAGE']
CAPITAL = V1.CAPITAL; FEE = V1.FEE; INVESTED = V1.INVESTED; BASE = V1.BASE; ARMS = V1.ARMS; RELAX = V1.RELAX
PROTOCOL = dict(version='wide_pool_optimizer_v2', registered='2026-10-15', base='wide_pool_optimizer_v1 (frozen)',
                benchmark='official CSI500 month-end weights (tushare index_weight, reseller), drifted by adjusted close, renormalised; universe = latest snapshot names',
                risk_model='barra_cne5_local self-built CNE5 (10 styles + 30 industry groups + country; EWMA 90/480 NW2 cov at latest month-end; EWMA 90 specific risk)',
                style_limit='all 10 CNE5 styles ±0.30σ (WS_blend ±0.10σ)', unchanged=['signals', 'alpha scale', 'stock ±0.5%', 'industry ±1%', 'TE 3%', 'turnover 15%', 'λ=20', 'cost .001', 'execution', 'fee', 'calendar', 'arms', 'relaxation', 'statistics'],
                arms=ARMS, skeleton_arms=['I0', 'I1_blend', 'WV0'], no_grid_search=True, parameters_chosen_on_results=False)


def preregister():
    DEST.mkdir(exist_ok=True); p = DEST/'protocol.json'
    if p.exists(): assert json.loads(p.read_text()) == json.loads(json.dumps(PROTOCOL)), 'Frozen v2 protocol changed'
    else: write(p, PROTOCOL)


def solve(alpha, b, current, X, F, d, sectors, styles, p, budget, invested, ic, zero):
    n = len(b); w = cp.Variable(n); a = w - b
    root = np.linalg.cholesky(F + 1e-10 * np.eye(len(F)))
    risk = cp.hstack([root.T @ (X.T @ a), cp.multiply(np.sqrt(d), a)]); var = cp.sum_squares(risk)
    scale = ic * float(np.median(np.sqrt(d)))
    obj = cp.Maximize(scale * (alpha @ w) - p['risk_aversion'] * var - p['cost'] * cp.norm1(w - current))
    lb = np.maximum(b - p['cap'], 0.); ub = b + p['cap']; lb[zero] = 0.; ub[zero] = 0.
    last = ''
    for mult in RELAX:
        cons = [w >= lb, w <= ub, cp.sum(w) == invested, cp.norm1(w - current) <= budget,
                sectors.T @ a <= p['industry'] * mult, sectors.T @ a >= -p['industry'] * mult,
                styles.T @ a <= p['style'], styles.T @ a >= -p['style'], cp.norm(risk, 2) <= p['te'] / np.sqrt(252)]
        prob = cp.Problem(obj, cons)
        for solver in ['CLARABEL', 'SCS']:
            try:
                prob.solve(solver=solver, verbose=False)
                if prob.status in ('optimal', 'optimal_inaccurate') and w.value is not None:
                    v = np.asarray(w.value).reshape(-1); v[v < 1e-8] = 0.; v[zero] = 0.
                    return v, dict(status=prob.status, solver=solver, industry_relax=mult, var=float(var.value), alpha_scale=float(scale))
                last = f'{solver}:{prob.status}'
            except Exception as e:
                last = f'{solver}:{type(e).__name__}:{str(e)[:120]}'
    return None, dict(status='infeasible_or_failed', error=last, industry_relax=RELAX[-1])


def simulate(arm, dates, refs, zsig, vetos, signal_marks, exec_prices, fill, closes, symbols, risk_by_day, uni_ids, members, ic_trail):
    U = len(symbols); units = np.zeros(U); marks = np.zeros(U); cash = CAPITAL; rows = []; diags = []; p = ARMS.get(arm)
    for t, day in enumerate(dates):
        sig = signal_marks[t]; valid = np.isfinite(sig) & (sig > 0); marks[valid] = sig[valid]
        nav = cash + units @ marks; ref = refs[t]; ids = uni_ids[t]; b = ref[ids]; invested = float(b.sum())
        current_full = np.where(marks > 0, units * marks / nav, 0.)
        if arm == 'I0': wfull = ref.copy()
        elif arm == 'I1_blend': wfull = R.target_weights(ref, zsig[t], .5)
        elif arm == 'WV0':
            w = ref.copy(); w[vetos[t]] = 0.; wfull = w / w.sum() * invested if w.sum() > 0 else ref.copy()
        else:
            X, F, d, S, ST = risk_by_day[t]; skeleton = ref.copy()
            if p.get('veto'):
                w = ref.copy(); w[vetos[t]] = 0.; skeleton = w / w.sum() * invested if w.sum() > 0 else ref.copy()
            wfull = skeleton.copy()
            if F is None: diags.append(dict(day=day, status='warmup_hold_skeleton'))
            else:
                z = zsig[t][ids]; z = np.where(np.isfinite(z), z, 0.); alpha = z - (b @ z) / max(b.sum(), 1e-12)
                cur = current_full[ids]; outside = current_full.sum() - cur.sum()
                prev_ref = np.nan_to_num(refs[t - 1]) if t > 0 else np.zeros(U)
                replication_need = float(np.abs(np.nan_to_num(ref) - prev_ref).sum()) + float(outside); budget = replication_need + p['turnover']
                zero = vetos[t][ids] if p.get('veto') else np.zeros(len(ids), bool)
                v, info = solve(alpha, b, cur, X[ids], F, d[ids], S[ids], ST[ids], p, budget, invested, ic_trail[t], zero)
                if v is None: info['fallback_hold_skeleton'] = True
                else: wfull = np.zeros(U); wfull[ids] = v
                info.update(day=day, active_share=float(np.abs(wfull[ids] - b).sum() / 2), turnover_planned=float(np.abs(wfull - current_full).sum()), replication_need=replication_need,
                            budget_passed=float(budget), n_ids=int(len(ids)), n_zero=int(zero.sum()), ic=float(ic_trail[t]), max_abs_active=float(np.max(np.abs(wfull[ids] - b))))
                diags.append(info)
        desired = np.divide(wfull * nav, marks, out=np.zeros(U), where=marks > 0); desired[~valid] = units[~valid]
        delta = desired - units; delta[np.abs(delta) * marks < 1000] = 0
        px = exec_prices[t]; can = fill[t] & np.isfinite(px) & (px > 0); px = np.where(can, px, marks)
        sell = np.where(can, np.maximum(-delta, 0), 0); proceeds = sell * px; cash += float((proceeds * (1 - FEE)).sum()); units -= sell
        buy = np.where(can, np.maximum(delta, 0), 0); cost = float((buy * px).sum() * (1 + FEE))
        if cost > cash: buy *= max(cash, 0) / cost
        paid = buy * px; cash -= float(paid.sum() * (1 + FEE)); units += buy
        observed = np.isfinite(closes[t]) & (closes[t] > 0); marks[observed] = closes[t, observed]; equity = units * marks; nav = cash + equity.sum()
        if arm in ARMS and risk_by_day[t][1] is not None and diags and diags[-1].get('day') == day:
            X, F, d, S, ST = risk_by_day[t]; realised = equity / nav; a_full = realised - np.nan_to_num(refs[t])
            diags[-1].update(realised_active_share=float(np.abs(a_full).sum() / 2), realised_max_industry_active=float(np.max(np.abs(S.T @ a_full))),
                             realised_max_style_active=float(np.max(np.abs(ST.T @ a_full))), realised_vs_target_L1=float(np.abs(realised - wfull).sum()))
        rows.append(dict(date=day, nav=nav / CAPITAL, cash_weight=cash / nav, fee_cny=float((paid.sum() + proceeds.sum()) * FEE), turnover=float((paid.sum() + proceeds.sum()) / nav),
                         holdings=int((units > 1e-10).sum()), max_stock_nav_weight=float(equity.max() / nav), stale_weight=float(equity[~observed].sum() / nav),
                         member_nav_weight=float(equity[members[t]].sum() / nav), fill_blocked_weight=float((np.abs(delta) * signal_marks[t] * ~can)[np.isfinite(signal_marks[t])].sum() / nav),
                         active_share_vs_index=float(np.abs(np.where(marks > 0, equity / nav, 0) - np.nan_to_num(refs[t])).sum() / 2)))
    return pd.DataFrame(rows), pd.DataFrame(diags)


def main():
    preregister(); t0 = time.time()
    prices = pd.read_parquet(OUT/'daily_prices.parquet'); symbols = json.load(open(OUT/'price_symbols.json')); U = len(symbols)
    historic = pd.read_parquet(ROOT/'outputs/cluster_sync_20261011/teammate_latest_uploads/unpacked/pcf_alpha_materials_20261011/benchmarks/H00905_daily.parquet')[['date', 'close']]
    recent = pd.read_csv(ROOT/'outputs/gru_beta_neutral_20261008/benchmark_sources/H00905_daily.csv')[['date', 'close']]
    for f in (historic, recent): f['date'] = pd.to_datetime(f.date).dt.strftime('%Y-%m-%d')
    benchmark = pd.concat([historic, recent]).drop_duplicates('date', keep='last').set_index('date').close.astype(float)
    pricebench = pd.read_csv(ROOT/'outputs/gru_beta_neutral_20261008/benchmark_sources/000905_daily.csv').set_index('date').close
    calendar = sorted(set(benchmark.index) & set(pricebench.index) & set(prices.date)); all_days = [d for d in calendar if '2023-03-01' <= d <= END]
    wide = lambda field: prices.pivot(index='date', columns='symbol', values=field).reindex(index=all_days, columns=symbols).apply(pd.to_numeric, errors='coerce').to_numpy(dtype=float)
    op = wide('open_adjusted'); close = wide('close_adjusted'); volume = wide('volume'); valid = wide('valid') == 1; isST = wide('isST')
    can_same = valid & (volume > 0) & (isST != 1); can = np.vstack([can_same[:1], can_same[:-1]])
    lookup = {s: i for i, s in enumerate(symbols)}; daypos = {d: i for i, d in enumerate(all_days)}
    DATA = ROOT/'gru_research/artifacts/dataset'; times = np.load(DATA/'times.npy'); clock = pd.to_datetime(times, utc=True).tz_convert('Asia/Shanghai')
    model_symbols = json.load(open(DATA/'symbols.json')); mapping = np.array([lookup[s] for s in model_symbols]); by15 = {str(clock[i].date()): i for i in np.flatnonzero(clock.hour == 15)}
    frozen_member = np.load(DATA/'members.npy', mmap_mode='r'); labels_h = np.load(DATA/'labels.npy', mmap_mode='r')
    s15, h15 = R.signals(15, all_days, symbols)
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
    sig_fn = {'gru': lambda d: s15.get(d), 'c5': lambda d: s_c5.get(d), 'blend': blend}
    # ---- official weights
    ow = pd.read_csv(OFFICIAL, dtype={'trade_date': str}); ow['date'] = pd.to_datetime(ow.trade_date, format='mixed').dt.strftime('%Y-%m-%d')
    snaps = {}; missing_names = {}
    for d, g in ow.groupby('date'):
        vec = np.zeros(U); miss = 0
        for c, w in zip(g.con_code, g.weight):
            if c in lookup: vec[lookup[c]] = float(w)
            else: miss += 1
        snaps[d] = vec; missing_names[d] = miss
    snap_dates = sorted(snaps)
    # ---- CNE5 model
    E = pd.read_parquet(CNE5/'exposures.parquet'); E['date'] = E.date.dt.strftime('%Y-%m-%d'); E = E[E.symbol.isin(lookup)]; Eg = {d: g for d, g in E.groupby('date')}
    inds = sorted(E.industry.unique()); inds = [i for i in inds if i != 'UNKNOWN'] + ['UNKNOWN']; gi = {n: i for i, n in enumerate(inds)}
    covs = {f.name[11:21]: pd.read_parquet(f) for f in sorted(CNE5.glob('factor_cov_*.parquet'))}; cov_dates = sorted(covs)
    SRk = pd.read_parquet(CNE5/'specific_risk.parquet'); SRk.index = SRk.index.strftime('%Y-%m-%d'); SRk = SRk.reindex(columns=symbols)
    fac_names = ['COUNTRY'] + inds[:-1] + STYLES     # UNKNOWN industry has no factor return; its names load only on country + styles
    def risk_at(prior):
        cd = [d for d in cov_dates if d <= prior]
        if not cd or prior not in Eg or prior not in SRk.index: return None
        C = covs[cd[-1]]; names = [n for n in fac_names if n in C.index]
        F = C.loc[names, names].to_numpy(float)
        g = Eg[prior]; idx = np.array([lookup[s] for s in g.symbol])
        X = np.zeros((U, len(names))); X[:, 0] = 1.
        st_cols = [names.index(s) for s in STYLES]; Xs = g[STYLES].to_numpy(float); Xs = np.where(np.isfinite(Xs), Xs, 0.); X[np.ix_(idx, st_cols)] = Xs
        ind_cols = {n: names.index(n) for n in inds[:-1] if n in names}
        S = np.zeros((U, len(inds))); S[:, -1] = 1.
        for i, n in zip(idx, g.industry):
            if n in ind_cols: S[i, -1] = 0.; S[i, gi[n]] = 1.; X[i, ind_cols[n]] = 1.
        sr = SRk.loc[prior].to_numpy(float); dvar = np.where(np.isfinite(sr), sr ** 2 / 252, np.nan); dvar = np.where(np.isfinite(dvar), dvar, np.nanmedian(dvar))
        ST = X[:, st_cols]
        return X, F, dvar, S, ST
    # ---- events
    v4_dates = pd.read_csv(V4/'P0_daily.csv').date.tolist(); last_c5 = None
    dates = []; refs = []; vetos = []; sig = {k: [] for k in sig_fn}; signal_marks = []; fills = []; execution_prices = []; closes = []; uni_ids = []; members = []; risk_by_day = {}; audit = []
    for day in v4_dates:
        j = daypos[day]; prior = all_days[j - 1]
        if prior in s_c5: last_c5 = prior
        else: s_c5[prior] = s_c5[last_c5]; veto_c5[prior] = veto_c5[last_c5]
        sd = [d for d in snap_dates if d <= prior][-1]; base = snaps[sd]
        if sd in daypos:
            p0 = close[daypos[sd]]; ok_d = np.isfinite(p0) & (p0 > 0) & np.isfinite(close[j - 1]); drift = np.where(ok_d, close[j - 1] / np.where(ok_d, p0, 1.), 1.)
        else: drift = np.ones(U)
        w = base * drift; w = np.where(np.isfinite(w) & (w > 0), w, 0.); ids = np.flatnonzero(w > 0); reference = w / w.sum() * INVESTED
        mem = np.zeros(U, bool); mem[ids] = True
        k = len(dates); dates.append(day); refs.append(reference); vetos.append(veto_c5[prior] & mem)
        for name_, fn in sig_fn.items(): sig[name_].append(fn(prior))
        signal_marks.append(close[j - 1]); fills.append(can[j]); execution_prices.append(op[j]); closes.append(close[j]); uni_ids.append(ids); members.append(mem)
        risk_by_day[k] = risk_at(prior) or (None, None, None, None, None)
        audit.append(dict(date=day, signal_date=prior, snapshot=sd, names=int(len(ids)), snapshot_names_missing_price=missing_names[sd], max_weight=float(reference.max()), vetoed=int((veto_c5[prior] & mem).sum()),
                          veto_weight=float(reference[veto_c5[prior] & mem].sum()), cov_date=([d for d in cov_dates if d <= prior] or [None])[-1], exposure_cov=float(np.isin(ids, [lookup[s] for s in Eg[prior].symbol] if prior in Eg else []).mean())))
    refs = np.asarray(refs); closes = np.asarray(closes); vetos = np.asarray(vetos); sig = {k: np.asarray(v) for k, v in sig.items()}; members = np.asarray(members)
    pd.DataFrame(audit).to_csv(DEST/'benchmark_audit.csv', index=False); print('events', len(dates), 'risk model days', sum(1 for k in risk_by_day if risk_by_day[k][1] is not None), round(time.time() - t0), 's', flush=True)
    daily_ic = {k: np.full(len(dates), np.nan) for k in sig}; ic_prior = []
    for e, day in enumerate(dates):
        prior = all_days[daypos[day] - 1]; i = by15.get(prior); ic_prior.append(VAL_IC[int(prior[:4])])
        if i is None: continue
        y = np.full(U, np.nan); y[mapping] = labels_h[i, :, 3]; m = members[e]
        for k in sig:
            s = sig[k][e]; ok = m & np.isfinite(s) & np.isfinite(y)
            if ok.sum() >= 100: daily_ic[k][e] = np.corrcoef(rankdata(s[ok]), rankdata(y[ok]))[0, 1]
    ic_trail = {}
    for k in sig:
        ser = pd.Series(daily_ic[k]).shift(2).rolling(250, min_periods=60).mean().to_numpy(); ic_trail[k] = np.where(np.isfinite(ser), ser, np.asarray(ic_prior))
    b = benchmark.loc[dates].to_numpy(); pb = pricebench.loc[dates].to_numpy()
    SPECS = {'I0': 'blend', 'I1_blend': 'blend', 'WV0': 'blend', **{a: p['signal'] for a, p in ARMS.items()}}
    results = {}; scores = []; yearly = []
    for arm, signame in SPECS.items():
        t1 = time.time()
        d, diag = simulate(arm, dates, refs, sig[signame], vetos, np.asarray(signal_marks), np.asarray(execution_prices), np.asarray(fills), closes, symbols, risk_by_day, uni_ids, members, list(ic_trail[signame]))
        d['H00905_nav'] = b / b[0]; d['price_index_nav'] = pb / pb[0]; d.to_csv(DEST/f'{arm}_daily.csv', index=False)
        if len(diag): diag.to_csv(DEST/f'{arm}_optimizer.csv', index=False)
        rec = R.summary(d, b, pb); rec['arm'] = arm; rec['seconds'] = round(time.time() - t1)
        if len(diag) and 'status' in diag:
            rec['optimizer_failed_days'] = int(diag['fallback_hold_skeleton'].fillna(False).astype(bool).sum()) if 'fallback_hold_skeleton' in diag else 0
            rec['industry_relaxed_days'] = int((diag.get('industry_relax', pd.Series(1.)) > 1).sum())
            for c in ['active_share', 'max_abs_active', 'realised_active_share', 'realised_max_industry_active', 'realised_max_style_active']:
                if c in diag: rec[c + '_mean'] = float(diag[c].mean()); rec[c + '_max'] = float(diag[c].max())
        scores.append(rec); results[arm] = d
        for year in range(2024, 2027):
            ids_ = np.flatnonzero(d.date.str.startswith(str(year)))
            if len(ids_) < 30: continue
            start = max(int(ids_[0]) - 1, 0); stop = int(ids_[-1]) + 1
            r = R.summary(d.iloc[start:stop], b[start:stop], pb[start:stop]); r.update(arm=arm, year=year); yearly.append(r)
        print('ARM_COMPLETE', arm, round(rec['net_return'], 4), round(rec['annualized_relative_excess'], 4), round(rec['tracking_error'], 4), flush=True)
    pd.DataFrame(scores).to_csv(DEST/'scorecard.csv', index=False); pd.DataFrame(yearly).to_csv(DEST/'yearly_scorecard.csv', index=False)
    allnav = dict(results)
    for a in ['P0', 'V5', 'V5_P1blend', 'O2_blend', 'V5_O2blend']:
        dv = pd.read_csv(V4/f'{a}_daily.csv'); assert dv.date.tolist() == dates; allnav['v4_' + a] = dv
    for a in ['I0', 'I1_blend', 'WV0', 'W_blend', 'WV_blend']:
        dv = pd.read_csv(V1.DEST/f'{a}_daily.csv'); assert dv.date.tolist() == dates; allnav['v1_' + a] = dv
    pairs = [(a, 'I0') for a in SPECS if a != 'I0'] + [(a, 'I1_blend') for a in ARMS] + [('WV_blend', 'WV0')]
    pairs += [(a, 'v1_' + a) for a in ['I0', 'I1_blend', 'WV0', 'W_blend', 'WV_blend']] + [(a, 'v4_P0') for a in ['I0', 'WV0', 'WV_blend']] + [(a, 'v4_V5_P1blend') for a in ['I0', 'WV_blend']] + [('W_blend', 'v4_O2_blend'), ('WV_blend', 'v4_V5_O2blend')]
    comp = []; halves = []
    for x, y in pairs:
        st, hv = V1.paired(allnav[x].nav.to_numpy(), allnav[y].nav.to_numpy(), np.asarray(dates), 8); comp.append(dict(arm=x, versus=y, **st))
        for k_, v_ in hv.items(): halves.append(dict(arm=x, versus=y, period=k_, relative_growth_pp=v_))
    pd.DataFrame(comp).to_csv(DEST/'paired_comparisons.csv', index=False); pd.DataFrame(halves).to_csv(DEST/'calendar_half_years.csv', index=False)
    write(DEST/'run_receipt.json', dict(signal_hashes=h15, code_sha256=sha(__file__), official_sha256=sha(OFFICIAL), cne5_receipt=json.load(open(CNE5/'build_receipt.json')), seconds=round(time.time() - t0)))
    pd.set_option('display.width', 250)
    print(pd.DataFrame(scores)[['arm', 'net_return', 'annualized_relative_excess', 'tracking_error', 'information_ratio', 'average_turnover', 'w50_p10', 'w50_worst', 'w50_win_rate']].round(4).to_string(index=False))
    print(pd.DataFrame(comp).round(4).to_string(index=False))


if __name__ == '__main__':
    main()
