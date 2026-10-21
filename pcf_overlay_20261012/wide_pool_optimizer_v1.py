"""WIDE-POOL OPTIMIZER v1 (2026-10-15) — mentor's 2026-10-14 construction, registered before simulation.

Question: does a mean-variance optimizer on the FULL CSI500 constituent pool, benchmarked to the CSI500 index
itself (not to the 563030 PCF basket), with the mentor's tight limits (stock ±0.5%, industry ±1%, style 0.1–0.3σ),
beat (a) pure index replication and (b) the live PCF construction (V5 排雷 + 混合倾斜)?

Universe   : current CSI500 members at the signal date (frozen panel membership, same as the GRU signal universe).
Benchmark  : proxy of official index weights = vendor free-float cap (c5 panel `fd_free_cap`) × teammate's implied
             inclusion multiplier (`index_weight_mult_a1`, walk-forward ridge, A17), renormalised over members,
             98% invested.  Official weights are NOT available locally; the proxy's tracking error to H00905 is
             reported for the pure-replication arm as the measure of its quality.  Members without free-float cap
             fall back to total cap (risk_context log_cap) scaled by the median proxy/total-cap ratio, counted.
Signals    : identical to v4 — gru (T 15:00 walk-forward 5-seed y4, neutralised, EMA .2), c5 (union percentile
             -> normal quantile), blend (mean of available z).  Alpha scale per signal = v4 A3 (trailing 250-session
             causal RankIC, lag 2, × median specific vol).
Risk model : v4 self-built Barra-style proxy (industry one-hot + logcap/mom63/vol60/logamt20/beta120), EWMA 60,
             NOT rqdata/jqdata Barra (no licence).  This is the main caveat of the whole exercise.
Execution  : T+1 open, prior-session tradability, fee 2bp/side, 10M, same ledger as v4.
Calendar   : exactly the v4 event calendar (2024-06-04..2026-09-17 execution dates) so NAVs pair with v4 arms.

Arms (frozen):
  I0        pure replication of the benchmark proxy (wide-pool skeleton)
  I1_blend  heuristic tilt w = b(1+0.5z) on the wide-pool skeleton (the live construction's tilt, moved to the index)
  W_gru / W_c5 / W_blend   optimizer, mentor limits: |w-b| <= 0.5%, w>=0, |industry active| <= 1%,
            |style active| <= 0.30σ, TE <= 3%/yr, overlay turnover <= 15%/day, λ=20, cost .001
  WS_blend  as W_blend with |style active| <= 0.10σ (mentor's tight end)
  WV0       wide-pool skeleton with c5 excluded/vetoed members set to zero and renormalised (排雷 on the index)
  WV_blend  W_blend plus hard zero on c5 excluded/vetoed members (lower bound 0 overrides the -0.5% limit)
Infeasible solve: relax industry limit 1% -> 2% -> 3% (flagged); still infeasible -> hold the arm's own skeleton
(I0 basket, or WV0 basket for WV_blend), flagged.  No parameter is chosen on results.

Comparisons (block bootstrap, 2000 × 20-day blocks, seed 20261011): every arm vs I0; W/WV arms vs I1_blend;
I0 and W_blend / WV_blend vs v4 P0 (PCF replication) and vs v4 V5_P1blend (live construction); all vs H00905.
"""
from pathlib import Path
import json, time, os
import numpy as np
import pandas as pd
import cvxpy as cp
from scipy.special import ndtri
from scipy.stats import rankdata
from .data import ROOT, OUT, END, symbol, write, sha
from . import replay as R
from .optimizer_overlay_v4 import RiskModel, VAL_IC, HALF_LIFE, MIN_HISTORY, C5_PATH, C5_START

DEST = OUT/'wide_pool_optimizer_v1'
V4 = OUT/'optimizer_overlay_v4'
CAPITAL = 10_000_000.; FEE = .0002; INVESTED = .98
FREECAP = ROOT/'c5_core/data/A_Layer_Live/panel/fd_free_cap.parquet'
MULT = ROOT/'c5_core/data/A_Layer_Live/indexw/index_weight_mult_a1.parquet'
BASE = dict(risk_aversion=20., cost=.001, te=.03, cap=.005, industry=.01, style=.30, turnover=.15)
ARMS = {
    'W_gru': dict(BASE, signal='gru'), 'W_c5': dict(BASE, signal='c5'), 'W_blend': dict(BASE, signal='blend'),
    'WS_blend': dict(BASE, signal='blend', style=.10),
    'WV_blend': dict(BASE, signal='blend', veto=True),
}
RELAX = [1., 2., 3.]      # industry-limit multipliers tried in order when infeasible
PROTOCOL = dict(version='wide_pool_optimizer_v1', registered='2026-10-15', question='mentor 2026-10-14: full-CSI500 pool, index benchmark, stock 0.5% / industry 1% / style 0.1-0.3',
                universe='frozen-panel CSI500 members at signal date', benchmark_proxy='fd_free_cap x index_weight_mult_a1 (teammate A17), renormalised over members, 98% invested; fallback total cap x median ratio',
                official_weights_available=False, barra_available=False, risk_model='v4 self-built proxy (industry + logcap/mom63/vol60/logamt20/beta120), EWMA 60, min history 120',
                signals=['gru', 'c5', 'blend'], alpha_scale='v4 A3 trailing causal RankIC x median specific vol', calendar='v4 event calendar 2024-06-04..2026-09-17',
                capital=CAPITAL, fee=FEE, invested=INVESTED, arms=ARMS, skeleton_arms=['I0', 'I1_blend(k=.5)', 'WV0'], infeasible='relax industry x2, x3 (flagged), then hold own skeleton (flagged)',
                comparisons='block bootstrap 2000x20d seed 20261011; vs I0, vs I1_blend, vs v4 P0, vs v4 V5_P1blend; Bonferroni family = 8 candidate arms', no_grid_search=True, parameters_chosen_on_results=False)


def preregister():
    DEST.mkdir(exist_ok=True); path = DEST/'protocol.json'
    if path.exists():
        assert json.loads(path.read_text()) == json.loads(json.dumps(PROTOCOL)), 'Frozen wide-pool v1 protocol changed'
    else:
        write(path, PROTOCOL)


def solve(alpha, b, current, X, F, d, sectors, p, budget, invested, ic, zero):
    n = len(b); w = cp.Variable(n); a = w - b
    root = np.linalg.cholesky(F + 1e-10 * np.eye(len(F)))
    risk = cp.hstack([root.T @ (X.T @ a), cp.multiply(np.sqrt(d), a)]); var = cp.sum_squares(risk)
    scale = ic * float(np.median(np.sqrt(d)))
    obj = cp.Maximize(scale * (alpha @ w) - p['risk_aversion'] * var - p['cost'] * cp.norm1(w - current))
    lb = np.maximum(b - p['cap'], 0.); ub = b + p['cap']
    lb[zero] = 0.; ub[zero] = 0.
    styles = X[:, -5:]; S = sectors
    last = ''
    for mult in RELAX:
        cons = [w >= lb, w <= ub, cp.sum(w) == invested, cp.norm1(w - current) <= budget,
                S.T @ a <= p['industry'] * mult, S.T @ a >= -p['industry'] * mult,
                styles.T @ a <= p['style'], styles.T @ a >= -p['style'],
                cp.norm(risk, 2) <= p['te'] / np.sqrt(252)]
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


def simulate(arm, dates, refs, zsig, vetos, signal_marks, exec_prices, fill, closes, symbols, Xd, Cd, Sd, uni_ids, members, ic_trail):
    U = len(symbols); units = np.zeros(U); marks = np.zeros(U); cash = CAPITAL
    rows = []; diags = []; p = ARMS.get(arm)
    for t, day in enumerate(dates):
        sig = signal_marks[t]; valid = np.isfinite(sig) & (sig > 0); marks[valid] = sig[valid]
        nav = cash + units @ marks
        ref = refs[t]; ids = uni_ids[t]; b = ref[ids]; invested = float(b.sum())
        current_full = np.where(marks > 0, units * marks / nav, 0.)
        if arm == 'I0': wfull = ref.copy()
        elif arm == 'I1_blend': wfull = R.target_weights(ref, zsig[t], .5)
        elif arm == 'WV0':
            w = ref.copy(); w[vetos[t]] = 0.; wfull = w / w.sum() * invested if w.sum() > 0 else ref.copy()
        else:
            F, d = Cd[t]
            skeleton = ref.copy()
            if p.get('veto'):
                w = ref.copy(); w[vetos[t]] = 0.; skeleton = w / w.sum() * invested if w.sum() > 0 else ref.copy()
            wfull = skeleton.copy()
            if F is None: diags.append(dict(day=day, status='warmup_hold_skeleton'))
            else:
                X = Xd[t][ids]; dd = d[ids]; S = Sd[t][ids]
                z = zsig[t][ids]; z = np.where(np.isfinite(z), z, 0.)
                alpha = z - (b @ z) / max(b.sum(), 1e-12)
                cur = current_full[ids]; outside = current_full.sum() - cur.sum()
                prev_ref = np.nan_to_num(refs[t - 1]) if t > 0 else np.zeros(U)
                replication_need = float(np.abs(np.nan_to_num(ref) - prev_ref).sum()) + float(outside)
                budget = replication_need + p['turnover']
                zero = vetos[t][ids] if p.get('veto') else np.zeros(len(ids), bool)
                v, info = solve(alpha, b, cur, X, F, dd, S, p, budget, invested, ic_trail[t], zero)
                if v is None:
                    info['fallback_hold_skeleton'] = True
                else:
                    wfull = np.zeros(U); wfull[ids] = v
                info.update(day=day, active_share=float(np.abs(wfull[ids] - b).sum() / 2), turnover_planned=float(np.abs(wfull - current_full).sum()),
                            replication_need=replication_need, budget_passed=float(budget), n_ids=int(len(ids)), n_zero=int(zero.sum()), ic=float(ic_trail[t]),
                            max_abs_active=float(np.max(np.abs(wfull[ids] - b))))
                diags.append(info)
        desired = np.divide(wfull * nav, marks, out=np.zeros(U), where=marks > 0)
        desired[~valid] = units[~valid]
        delta = desired - units; delta[np.abs(delta) * marks < 1000] = 0
        px = exec_prices[t]; can = fill[t] & np.isfinite(px) & (px > 0)
        px = np.where(can, px, marks); sell = np.where(can, np.maximum(-delta, 0), 0)
        proceeds = sell * px; cash += float((proceeds * (1 - FEE)).sum()); units -= sell
        buy = np.where(can, np.maximum(delta, 0), 0); cost = float((buy * px).sum() * (1 + FEE))
        if cost > cash: buy *= max(cash, 0) / cost
        paid = buy * px; cash -= float(paid.sum() * (1 + FEE)); units += buy
        observed = np.isfinite(closes[t]) & (closes[t] > 0); marks[observed] = closes[t, observed]
        equity = units * marks; nav = cash + equity.sum()
        if arm in ARMS and Cd[t][0] is not None and diags and diags[-1].get('day') == day:
            realised = equity / nav; a_full = realised - np.nan_to_num(refs[t]); S = Sd[t]; styles = Xd[t][:, -5:]
            diags[-1].update(realised_active_share=float(np.abs(a_full).sum() / 2), realised_max_industry_active=float(np.max(np.abs(S.T @ a_full))),
                             realised_max_style_active=float(np.max(np.abs(styles.T @ a_full))), realised_vs_target_L1=float(np.abs(realised - wfull).sum()))
        rows.append(dict(date=day, nav=nav / CAPITAL, cash_weight=cash / nav, fee_cny=float((paid.sum() + proceeds.sum()) * FEE),
                         turnover=float((paid.sum() + proceeds.sum()) / nav), holdings=int((units > 1e-10).sum()),
                         max_stock_nav_weight=float(equity.max() / nav), stale_weight=float(equity[~observed].sum() / nav),
                         member_nav_weight=float(equity[members[t]].sum() / nav),
                         fill_blocked_weight=float((np.abs(delta) * signal_marks[t] * ~can)[np.isfinite(signal_marks[t])].sum() / nav),
                         active_share_vs_index=float(np.abs(np.where(marks > 0, equity / nav, 0) - np.nan_to_num(refs[t])).sum() / 2)))
    return pd.DataFrame(rows), pd.DataFrame(diags)


def paired(nx, ny, dates_, n_family):
    n = len(nx) - 1; rx = nx[1:] / nx[:-1]; ry = ny[1:] / ny[:-1]; rel = rx / ry
    annual = float(np.prod(rel) ** (252 / n) - 1)
    rng = np.random.default_rng(20261011); ix = (rng.integers(0, n, size=(2000, int(np.ceil(n / 20)), 1)) + np.arange(20)) % n; ix = ix.reshape(2000, -1)[:, :n]
    samples = np.prod(rel[ix], axis=1) ** (252 / n) - 1
    lo, hi = np.quantile(samples, [.025, .975]); blo, bhi = np.quantile(samples, [.025 / n_family, 1 - .025 / n_family])
    diff = (rx - 1) - (ry - 1); te = float(np.std(rel - 1, ddof=1) * np.sqrt(252)); ir = float(np.mean(diff) / np.std(diff, ddof=1) * np.sqrt(252))
    wx = R.relative_windows(nx, ny); lg = np.log(rel)
    yrs = pd.Series(lg, index=pd.to_datetime(dates_[1:])).groupby(lambda i: i.year).sum(); share26 = float(yrs.get(2026, 0) / lg.sum()) if lg.sum() != 0 else np.nan
    s = pd.Series(np.cumprod(rel), index=pd.to_datetime(dates_[1:])); last = s.groupby([s.index.year, (s.index.month > 6).astype(int) + 1]).last(); starts = [1.0] + last.tolist()[:-1]
    halves = {f'{yy}H{hh}': float((l / st - 1) * 100) for (yy, hh), l, st in zip(last.index, last, starts)}
    return dict(days=n, annualized_relative_cagr=annual, boot95_low=float(lo), boot95_high=float(hi), bonferroni_low=float(blo), bonferroni_high=float(bhi), share_of_log_gain_2026=share26,
                tracking_error=te, information_ratio=ir, w50_mean=float(wx.mean()), w50_p10=float(np.quantile(wx, .1)), w50_worst=float(wx.min()), w50_win=float((wx > 0).mean())), halves


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
    # ---- signals
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
    # ---- benchmark proxy weights
    fc = pd.read_parquet(FREECAP); fc.index = pd.to_datetime(fc.index).strftime('%Y-%m-%d'); fc.columns = [c[2:] + '.' + c[:2] for c in fc.columns]
    mult = pd.read_parquet(MULT); mult.index = pd.to_datetime(mult.index).strftime('%Y-%m-%d'); mult.columns = [c[2:] + '.' + c[:2] for c in mult.columns]
    fc = fc.reindex(columns=symbols); mult = mult.reindex(columns=symbols)
    # ---- risk model (identical to v4)
    risk = RiskModel(all_days, symbols, ret, amount, logcap, industry, bench_ret); X_by_day = {}; cov_by_day = {}; sectors_by_day = {}; X_prev = None
    for j in range(len(all_days)):
        if X_prev is not None: risk.update(j, X_prev)
        X = risk.exposures(j); X_prev = X; X_by_day[j] = X; cov_by_day[j] = risk.covariance(); sectors_by_day[j] = X[:, 1:1 + risk.K_ind]
    print('risk model built', round(time.time() - t0), 's', flush=True)
    # ---- event calendar = v4 P0 execution dates
    v4_dates = pd.read_csv(V4/'P0_daily.csv').date.tolist()
    dates = []; refs = []; vetos = []; sig = {k: [] for k in sig_fn}; signal_marks = []; fills = []; execution_prices = []; closes = []; uni_ids = []; members = []; Xd = {}; Cd = {}; Sd = {}; audit = []
    last_c5 = None
    for day in v4_dates:
        j = daypos[day]; prior = all_days[j - 1]
        if prior not in by15 or prior not in s15: raise RuntimeError(f'signal missing for {prior}')
        if prior in s_c5: last_c5 = prior
        else: s_c5[prior] = s_c5[last_c5]; veto_c5[prior] = veto_c5[last_c5]
        i15 = by15[prior]; mem = np.zeros(U, bool); mem[mapping] = frozen_member[i15]
        ids = np.flatnonzero(mem)
        f = fc.loc[prior].to_numpy(dtype=float) if prior in fc.index else np.full(U, np.nan)
        g = mult.loc[prior].to_numpy(dtype=float) if prior in mult.index else np.full(U, np.nan)
        g = np.where(np.isfinite(g), g, 1.)
        w = np.where(mem & np.isfinite(f) & (f > 0), f * g, np.nan)
        have = mem & np.isfinite(w); miss = mem & ~np.isfinite(w)
        cap = np.exp(np.nan_to_num(logcap[j - 1], nan=-np.inf)); fallback = 0
        if miss.any():
            ok = have & np.isfinite(cap) & (cap > 0)
            ratio = np.median(w[ok] / cap[ok]) if ok.sum() > 10 else 1.
            fillv = cap[miss] * ratio; w[miss] = np.where(np.isfinite(fillv) & (fillv > 0), fillv, np.nan); fallback = int(miss.sum())
        w = np.where(mem & np.isfinite(w), w, 0.)
        reference = w / w.sum() * INVESTED
        k = len(dates); dates.append(day); refs.append(reference); vetos.append(veto_c5[prior] & mem)
        for name_, fn in sig_fn.items(): sig[name_].append(fn(prior))
        signal_marks.append(close[j - 1]); fills.append(can[j]); execution_prices.append(op[j]); closes.append(close[j]); uni_ids.append(ids); members.append(mem)
        Xd[k] = X_by_day[j - 1]; Cd[k] = cov_by_day[j - 1]; Sd[k] = sectors_by_day[j - 1]
        audit.append(dict(date=day, signal_date=prior, members=int(mem.sum()), freecap_fallback=fallback, mult_available=bool(prior in mult.index), max_weight=float(reference.max()), vetoed=int((veto_c5[prior] & mem).sum()), veto_weight=float(reference[veto_c5[prior] & mem].sum())))
    refs = np.asarray(refs); closes = np.asarray(closes); vetos = np.asarray(vetos); sig = {k: np.asarray(v) for k, v in sig.items()}; members = np.asarray(members)
    pd.DataFrame(audit).to_csv(DEST/'benchmark_proxy_audit.csv', index=False)
    # ---- trailing causal IC per signal (v4 A3)
    daily_ic = {k: np.full(len(dates), np.nan) for k in sig}; ic_prior = []
    for e, day in enumerate(dates):
        prior = all_days[daypos[day] - 1]; i = by15[prior]; y = np.full(U, np.nan); y[mapping] = labels_h[i, :, 3]; m = members[e]
        ic_prior.append(VAL_IC[int(prior[:4])])
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
        d, diag = simulate(arm, dates, refs, sig[signame], vetos, np.asarray(signal_marks), np.asarray(execution_prices), np.asarray(fills), closes, symbols, Xd, Cd, Sd, uni_ids, members, list(ic_trail[signame]))
        d['H00905_nav'] = b / b[0]; d['price_index_nav'] = pb / pb[0]; d.to_csv(DEST/f'{arm}_daily.csv', index=False)
        if len(diag): diag.to_csv(DEST/f'{arm}_optimizer.csv', index=False)
        rec = R.summary(d, b, pb); rec['arm'] = arm; rec['seconds'] = round(time.time() - t1)
        ms = d.member_nav_weight.iloc[3:]; rec.update(member_share_mean=float(ms.mean()), member_share_min=float(ms.min()))
        if len(diag) and 'status' in diag:
            rec['optimizer_days'] = int((diag.status != 'warmup_hold_skeleton').sum()); rec['optimizer_failed_days'] = int(diag['fallback_hold_skeleton'].fillna(False).astype(bool).sum()) if 'fallback_hold_skeleton' in diag else 0
            rec['industry_relaxed_days'] = int((diag.get('industry_relax', pd.Series(1.)) > 1).sum())
            for c in ['active_share', 'max_abs_active', 'realised_active_share', 'realised_max_industry_active', 'realised_max_style_active', 'realised_vs_target_L1']:
                if c in diag: rec[c + '_mean'] = float(diag[c].mean()); rec[c + '_max'] = float(diag[c].max())
        scores.append(rec); results[arm] = d
        for year in range(2024, 2027):
            ids_ = np.flatnonzero(d.date.str.startswith(str(year)))
            if len(ids_) < 30: continue
            start = max(int(ids_[0]) - 1, 0); stop = int(ids_[-1]) + 1
            r = R.summary(d.iloc[start:stop], b[start:stop], pb[start:stop]); r.update(arm=arm, year=year); yearly.append(r)
        print('ARM_COMPLETE', arm, round(rec['net_return'], 4), round(rec['annualized_relative_excess'], 4), round(rec['tracking_error'], 4), flush=True)
    pd.DataFrame(scores).to_csv(DEST/'scorecard.csv', index=False); pd.DataFrame(yearly).to_csv(DEST/'yearly_scorecard.csv', index=False)
    # ---- v4 arms on the same calendar
    v4arms = {}
    for a in ['P0', 'P1_blend', 'V5', 'V5_P1blend', 'V5_O2blend', 'O2_blend']:
        dv = pd.read_csv(V4/f'{a}_daily.csv'); assert dv.date.tolist() == dates, a; v4arms['v4_' + a] = dv
    allnav = {**results, **v4arms}
    pairs = [(a, 'I0') for a in SPECS if a != 'I0'] + [(a, 'I1_blend') for a in ARMS] + [('WV_blend', 'WV0'), ('WV0', 'I0')]
    pairs += [(a, 'v4_P0') for a in ['I0', 'I1_blend', 'W_blend', 'WV0', 'WV_blend']] + [(a, 'v4_V5_P1blend') for a in ['I0', 'I1_blend', 'W_blend', 'WV_blend']] + [('W_blend', 'v4_O2_blend'), ('WV_blend', 'v4_V5_O2blend'), ('WV0', 'v4_V5')]
    comp = []; halves = []
    for x, y in pairs:
        st, hv = paired(allnav[x].nav.to_numpy(), allnav[y].nav.to_numpy(), np.asarray(dates), 8); comp.append(dict(arm=x, versus=y, **st))
        for k_, v_ in hv.items(): halves.append(dict(arm=x, versus=y, period=k_, relative_growth_pp=v_))
    pd.DataFrame(comp).to_csv(DEST/'paired_comparisons.csv', index=False); pd.DataFrame(halves).to_csv(DEST/'calendar_half_years.csv', index=False)
    write(DEST/'run_receipt.json', dict(signal_hashes=h15, code_sha256=sha(__file__), freecap_sha256=sha(FREECAP), mult_sha256=sha(MULT), c5_sha256=sha(C5_PATH), seconds=round(time.time() - t0), arms=list(SPECS)))
    pd.set_option('display.width', 250)
    print(pd.DataFrame(scores)[['arm', 'net_return', 'annualized_relative_excess', 'tracking_error', 'information_ratio', 'average_turnover', 'w50_p10', 'w50_worst', 'w50_win_rate']].round(4).to_string(index=False))
    print(pd.DataFrame(comp).round(4).to_string(index=False))


if __name__ == '__main__':
    main()
