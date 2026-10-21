"""Mean-variance overlay on the 563030 PCF basket, VERSION 2 (10M account, fee 0.0002/side).

Registered as a separate PCF optimizer version; NOT an equivalent call of the teammate's B15 optimizer.
Changes from v1 after the 2026-10-11 audit (v1 outputs kept untouched in optimizer_overlay/):
  F01 alpha scale is causal: each walk-forward window uses its OWN validation-year RankIC
      (T2023<-2022, T2024<-2023, T2025<-2024, T2026<-2025) x median specific daily vol at the signal date.
  F02 protocol is written and frozen BEFORE any simulation; an existing file must match byte-for-byte.
  F04 infeasible solve falls back to holding the replication basket (never to a tilt), flagged.
  F05 execution-day tradability uses only prior-session information (volume>0, not ST, valid on T).
  F06 the actual turnover budget passed to the solver is logged; the rule is
      replication_need + overlay_turnover (overlay <= 15%/day on top of mandatory replication trades).
  F07 paired statistics: compounded relative-wealth CAGR with block bootstrap on compounded paths,
      arithmetic IR, tracking error measured against P0, identical window endpoints everywhere.
  F08 industry vocabulary fixed from the upstream frozen context (not from test-period data).
  Post-fill realised active exposures (industry/style/active share) are recorded every day.


Arms (all share the P0 PCF reference weights, T 15:00 walk-forward signal, T+1 open execution):
  P0  pure T+1 replication
  P1  heuristic tilt  w = b (1 + 0.5 z)                         (existing method, rerun at 10M)
  O1  B15-style optimizer: max  s·μᵀw − λ aᵀΣa − c‖w−w_prev‖₁
        s.t. w ≥ 0, w ≤ b + 1%, Σw = Σb, |industry active| ≤ 2%, |style active| ≤ 0.15,
             TE ≤ 5%/yr, two-way turnover ≤ 15%/day;  λ=20, s=0.0024, c=0.001
  O2  same as O1 with TE ≤ 3%/yr and per-name cap b + 0.5%
Risk model: Barra-style proxy estimated causally from daily prices (industry one-hot incl. UNKNOWN,
log cap, momentum63, volatility60, log amount20, beta120); EWMA factor covariance and specific
variance, half-life 60 sessions, minimum 120 sessions of history before the optimizer is armed.
Before arming, O arms hold the P0 basket. No parameter is chosen on results.
"""
from pathlib import Path
import json, time
import numpy as np
import pandas as pd
import cvxpy as cp
from .data import ROOT, OUT, END, symbol, write, sha
from . import replay as R

DEST = OUT/'optimizer_overlay_v2'
CAPITAL = 10_000_000.
FEE = .0002
ARMS = {
    'O1': dict(risk_aversion=20., alpha_scale='ic_implied', cost=.001, te=.05, cap=.01, industry=.02, style=.15, turnover=.15, active_share=.15),
    'O2': dict(risk_aversion=20., alpha_scale='ic_implied', cost=.001, te=.03, cap=.005, industry=.02, style=.15, turnover=.15, active_share=.10),
}
def validation_ic_by_test_year():
    out = {}
    for T in range(2023, 2027):
        v = [json.load(open(ROOT/f'outputs/gru_walkforward_20261010/T{T}/control_seed{s}/wf.json'))['best_validation14_rank_ic'] for s in [11, 22, 33, 44, 55]]
        out[T] = float(np.mean(v))
    return out
VAL_IC = validation_ic_by_test_year()   # causal: known before each test year starts
HALF_LIFE = 60; MIN_HISTORY = 120; WARMUP_START = None   # set after loading


def ewma_weights(n, half_life=HALF_LIFE):
    w = 0.5 ** (np.arange(n)[::-1] / half_life); return w / w.sum()


def standardize(x):
    x = np.asarray(x, float); out = np.full_like(x, np.nan); ok = np.isfinite(x)
    if ok.sum() < 10: return out
    v = x[ok]; lo, hi = np.quantile(v, [.01, .99]); v = np.clip(v, lo, hi)
    s = v.std(); out[ok] = (v - v.mean()) / (s if s > 1e-12 else 1.)
    return out


class RiskModel:
    """Causal cross-sectional factor model on the daily price panel."""
    def __init__(self, dates, symbols, ret, amount, logcap, industry, bench_ret):
        self.dates, self.symbols, self.ret, self.amount = dates, symbols, ret, amount
        self.logcap, self.industry, self.bench = logcap, industry, bench_ret
        self.inds = sorted(set(int(i) for i in np.unique(industry) if i >= 0))
        self.K_ind = len(self.inds) + 1                      # + UNKNOWN
        self.style_names = ['logcap', 'mom63', 'vol60', 'logamt20', 'beta120']
        self.K = 1 + self.K_ind + len(self.style_names)       # intercept + industries + styles
        self.factor_returns = []                               # list of (date, f vector)
        self.spec = {}                                         # symbol index -> EWMA residual var
        self.resid_hist = []                                   # list of (date, residual vector with NaN)
        self.ind_pos = {k: i for i, k in enumerate(self.inds)}

    def exposures(self, t):
        """Exposures known at close of session t (uses returns up to t)."""
        N = len(self.symbols); X = np.full((N, self.K), np.nan)
        r = self.ret[max(0, t - 119):t + 1]                   # up to 120 sessions ending t
        n = r.shape[0]
        mom = np.nansum(np.log1p(r[-63:]), axis=0) if n >= 63 else np.full(N, np.nan)
        with np.errstate(all='ignore'):
            vol = np.nanstd(r[-60:], axis=0) if n >= 60 else np.full(N, np.nan)
            amt = np.log(np.nanmean(self.amount[max(0, t - 19):t + 1], axis=0))
            b = self.bench[max(0, t - 119):t + 1]
            okb = np.isfinite(b)
            rr = r[okb]; bb = b[okb]
            if len(bb) >= 60:
                bc = bb - bb.mean(); rc = rr - np.nanmean(rr, axis=0)
                beta = np.nansum(rc * bc[:, None], axis=0) / (bc @ bc)
                cnt = np.isfinite(rr).sum(axis=0); beta[cnt < 60] = np.nan
            else: beta = np.full(N, np.nan)
        styles = np.column_stack([self.logcap[t], mom, vol, amt, beta])
        enough = np.isfinite(r).sum(axis=0) >= min(60, n)
        styles[~enough] = np.nan
        styles = np.column_stack([standardize(c) for c in styles.T])
        styles = np.where(np.isfinite(styles), styles, 0.)       # missing style -> neutral
        X[:, 0] = 1.
        ind = np.zeros((N, self.K_ind)); ind[:, -1] = 1.
        for i, k in enumerate(self.industry[t]):
            if int(k) in self.ind_pos: ind[i, self.ind_pos[int(k)]] = 1.; ind[i, -1] = 0.
        X[:, 1:1 + self.K_ind] = ind; X[:, 1 + self.K_ind:] = styles
        return X

    def update(self, t, X_prev):
        """Regress session-t returns on exposures from session t-1; update EWMA state."""
        y = self.ret[t]; ok = np.isfinite(y) & np.isfinite(X_prev).all(axis=1) & np.isfinite(self.amount[t])
        if ok.sum() < 200: return
        Xo, yo = X_prev[ok], np.clip(y[ok], -.2, .2)
        # ridge on industries+styles to keep regression well posed when a sector has few names
        lam = 1e-4 * ok.sum()
        A = Xo.T @ Xo + lam * np.eye(self.K); A[0, 0] -= lam
        f = np.linalg.solve(A, Xo.T @ yo); e = yo - Xo @ f
        self.factor_returns.append(f)
        resid = np.full(len(y), np.nan); resid[ok] = e; self.resid_hist.append(resid)
        if len(self.resid_hist) > 250: self.resid_hist.pop(0)

    def covariance(self):
        if len(self.factor_returns) < MIN_HISTORY: return None, None
        F_hist = np.array(self.factor_returns[-250:]); w = ewma_weights(len(F_hist))
        mu = w @ F_hist; C = (F_hist - mu).T @ ((F_hist - mu) * w[:, None])
        # shrink toward diagonal (PSD)
        C = .9 * C + .1 * np.diag(np.diag(C)); C = (C + C.T) / 2
        E = np.array(self.resid_hist); w2 = ewma_weights(len(E))
        with np.errstate(all='ignore'):
            d = np.nansum((E ** 2) * w2[:, None], axis=0) / np.nansum(np.isfinite(E) * w2[:, None], axis=0)
        floor = np.nanquantile(d, .25) if np.isfinite(d).any() else 1e-4
        d = np.where(np.isfinite(d), np.maximum(d, floor), np.nanmedian(d))
        return C, d


def solve(alpha, b, current, X, F, d, sectors, p, budget, invested, ic):
    n = len(b); w = cp.Variable(n); a = w - b
    root = np.linalg.cholesky(F + 1e-10 * np.eye(len(F)))
    risk = cp.hstack([root.T @ (X.T @ a), cp.multiply(np.sqrt(d), a)])
    var = cp.sum_squares(risk)
    scale = p['alpha_scale'] if isinstance(p['alpha_scale'], float) else ic * float(np.median(np.sqrt(d)))
    obj = cp.Maximize(scale * (alpha @ w) - p['risk_aversion'] * var - p['cost'] * cp.norm1(w - current))
    S = sectors; styles = X[:, -5:]
    cons = [w >= 0, w <= b + p['cap'], cp.sum(w) == invested, cp.norm1(w - current) <= budget, cp.norm1(w - b) <= 2 * p['active_share'],
            S.T @ a <= p['industry'], S.T @ a >= -p['industry'],
            styles.T @ a <= p['style'], styles.T @ a >= -p['style'],
            cp.norm(risk, 2) <= p['te'] / np.sqrt(252)]
    prob = cp.Problem(obj, cons)
    last = ''
    for solver in ['CLARABEL', 'SCS']:
        try:
            prob.solve(solver=solver, verbose=False)
            if prob.status in ('optimal', 'optimal_inaccurate') and w.value is not None:
                v = np.asarray(w.value).reshape(-1); v[v < 1e-8] = 0.
                return v, dict(status=prob.status, solver=solver, var=float(var.value) if var.value is not None else np.nan, alpha_scale=float(scale))
            last = f'{solver}:{prob.status}'
        except Exception as e:
            last = f'{solver}:{type(e).__name__}:{str(e)[:200]}'
    return None, dict(status='infeasible_or_failed', error=last)


def simulate(arm, dates, refs, alpha15, signal_marks, exec_prices, fill, closes, symbols, risk, X_by_day, cov_by_day, sectors_by_day, pcf_ids, members, ic_by_event):
    U = len(symbols); units = np.zeros(U); marks = np.zeros(U); cash = CAPITAL
    rows = []; diags = []; p = ARMS.get(arm)
    prev_w = None
    for t, day in enumerate(dates):
        sig = signal_marks[t]; valid = np.isfinite(sig) & (sig > 0); marks[valid] = sig[valid]
        nav = cash + units @ marks
        ref = refs[t]
        if not np.isfinite(ref).all():
            desired = units.copy()
        else:
            ids = pcf_ids[t]; b = ref[ids]; invested = float(b.sum())
            current_full = np.where(marks > 0, units * marks / nav, 0.)
            if arm == 'P0': wfull = ref.copy()
            elif arm == 'P1': wfull = R.target_weights(ref, alpha15[t], .5)
            else:
                F, d = cov_by_day[t]
                wfull = ref.copy()
                if F is not None:
                    X = X_by_day[t][ids]; dd = d[ids]; S = sectors_by_day[t][ids]
                    z = alpha15[t][ids]; z = np.where(np.isfinite(z), z, 0.)
                    alpha = z - (b @ z) / max(b.sum(), 1e-12)     # centre under reference weights
                    cur = current_full[ids]
                    # replication trades (reaching today's basket, selling names that left it) are mandatory;
                    # the overlay may add at most p['turnover'] two-way turnover on top of them.
                    outside = current_full.sum() - cur.sum()
                    # mandatory replication = change of the PCF basket itself since the previous event (known at signal time)
                    # plus selling holdings that left the basket; the overlay may add at most p['turnover'] on top.
                    prev_ref = np.nan_to_num(refs[t - 1]) if t > 0 else np.zeros(U)
                    replication_need = float(np.abs(np.nan_to_num(ref) - prev_ref).sum()) + float(outside)
                    budget = max(replication_need + p['turnover'], float(np.abs(b - cur).sum()) * 0 + .02)
                    v, info = solve(alpha, b, cur, X, F, dd, S, p, budget, invested, ic_by_event[t])
                    if v is None:
                        wfull = ref.copy(); info['fallback_hold_basket'] = True
                    else:
                        wfull = np.zeros(U); wfull[ids] = v
                    info.update(day=day, active_share=float(np.abs(wfull[ids] - b).sum() / 2),
                                turnover_planned=float(np.abs(wfull - current_full).sum()),
                                replication_need=replication_need, outside=float(outside), budget_passed=float(budget), overlay_turnover_cap=p['turnover'], validation_ic=float(ic_by_event[t]),
                                b_sum=float(invested), cur_sum=float(cur.sum()), n_ids=int(len(ids)), d_min=float(np.nanmin(dd)), d_max=float(np.nanmax(dd)), F_trace=float(np.trace(F)))
                    diags.append(info)
                else:
                    diags.append(dict(day=day, status='warmup_hold_P0'))
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
        if arm in ARMS and np.isfinite(refs[t]).all() and cov_by_day[t][0] is not None and diags and diags[-1].get('day') == day:
            ids = pcf_ids[t]; realised = equity / nav; a_full = realised - np.nan_to_num(refs[t])
            S = sectors_by_day[t]; styles = X_by_day[t][:, -5:]
            diags[-1].update(realised_active_share=float(np.abs(a_full).sum() / 2),
                             realised_max_industry_active=float(np.max(np.abs(S.T @ a_full))),
                             realised_max_style_active=float(np.max(np.abs(styles.T @ a_full))),
                             realised_vs_target_L1=float(np.abs(realised - wfull).sum()))
        rows.append(dict(date=day, nav=nav / CAPITAL, cash_weight=cash / nav, fee_cny=float((paid.sum() + proceeds.sum()) * FEE),
                         turnover=float((paid.sum() + proceeds.sum()) / nav), holdings=int((units > 1e-10).sum()),
                         max_stock_nav_weight=float(equity.max() / nav), stale_weight=float(equity[~observed].sum() / nav),
                         member_nav_weight=float(equity[members[t]].sum() / nav),
                         fill_blocked_weight=float((np.abs(delta) * signal_marks[t] * ~can)[np.isfinite(signal_marks[t])].sum() / nav),
                         active_share_vs_pcf=float(np.abs(np.where(marks > 0, equity / nav, 0) - np.nan_to_num(refs[t])).sum() / 2)))
    return pd.DataFrame(rows), pd.DataFrame(diags)


PROTOCOL = dict(version='pcf_optimizer_overlay_v2', capital=CAPITAL, fee=FEE, arms=ARMS,
                risk_model=dict(exposures=['intercept', 'industry_onehot+UNKNOWN(frozen upstream vocabulary)', 'logcap', 'mom63', 'vol60', 'logamt20', 'beta120'], half_life=HALF_LIFE, min_history=MIN_HISTORY, shrink_to_diag=.1, ridge=1e-4),
                alpha_scale='validation-year RankIC of the test-year window x median specific daily vol at signal date', validation_ic_by_test_year=VAL_IC,
                signal='T 15:00 walk-forward 5-seed y4, neutralised, EMA .2 (replay.signals)', execution='T+1 open, adjusted; tradable = prior-session valid & volume>0 & not ST',
                turnover_rule='budget passed = ||b_t - b_{t-1}||_1 (basket change) + weight of holdings outside the basket + overlay cap 15%; replication trades are mandatory, overlay limited', infeasible='hold replication basket, flagged',
                member_constraint='none (user instruction 2026-10-11)', statistics='compounded relative-wealth CAGR; block bootstrap on compounded paths; arithmetic IR; TE vs P0; windows start at armed-day close',
                superseded_v1='outputs/pcf_overlay_20261012/optimizer_overlay (constant 2024 IC scale, same-day tradability, stale budget log); kept, not rerun',
                no_grid_search=True, not_equivalent_to_B15=True)


def preregister():
    DEST.mkdir(exist_ok=True); path = DEST/'protocol.json'
    if path.exists():
        assert json.loads(path.read_text()) == json.loads(json.dumps(PROTOCOL)), 'Frozen v2 protocol changed'
    else:
        write(path, PROTOCOL)


def main():
    preregister()
    pc = pd.read_parquet(OUT/'pcf_components.parquet'); meta = pd.read_csv(OUT/'pcf_metadata.csv', dtype={'date': str, 'valuation_date': str}).set_index('date')
    prices = pd.read_parquet(OUT/'daily_prices.parquet'); symbols = json.load(open(OUT/'price_symbols.json')); U = len(symbols)
    historic = pd.read_parquet(ROOT/'outputs/cluster_sync_20261011/teammate_latest_uploads/unpacked/pcf_alpha_materials_20261011/benchmarks/H00905_daily.parquet')[['date', 'close']]
    recent = pd.read_csv(ROOT/'outputs/gru_beta_neutral_20261008/benchmark_sources/H00905_daily.csv')[['date', 'close']]
    for f in (historic, recent): f['date'] = pd.to_datetime(f.date).dt.strftime('%Y-%m-%d')
    benchmark = pd.concat([historic, recent]).drop_duplicates('date', keep='last').set_index('date').close.astype(float)
    pricebench = pd.read_csv(ROOT/'outputs/gru_beta_neutral_20261008/benchmark_sources/000905_daily.csv').set_index('date').close
    calendar = sorted(set(benchmark.index) & set(pricebench.index) & set(prices.date)); all_days = [d for d in calendar if '2023-03-01' <= d <= END]
    wide = lambda field: prices.pivot(index='date', columns='symbol', values=field).reindex(index=all_days, columns=symbols).apply(pd.to_numeric, errors='coerce').to_numpy(dtype=float)
    rawclose = wide('close'); op = wide('open_adjusted'); close = wide('close_adjusted'); amount = wide('amount')
    volume = wide('volume'); valid = wide('valid') == 1; isST = wide('isST')
    can_same = valid & (volume > 0) & (isST != 1)
    can = np.vstack([can_same[:1], can_same[:-1]])   # prior-session information only (F05)
    ret = np.full_like(close, np.nan); ret[1:] = close[1:] / close[:-1] - 1
    bench_ret = benchmark.loc[all_days].to_numpy(); bench_ret = np.r_[np.nan, bench_ret[1:] / bench_ret[:-1] - 1]
    lookup = {s: i for i, s in enumerate(symbols)}; daypos = {d: i for i, d in enumerate(all_days)}
    # industry & log cap for union names from risk_context (prior-close information); others UNKNOWN / NaN
    DATA = ROOT/'gru_research/artifacts/dataset'; times = np.load(DATA/'times.npy'); clock = pd.to_datetime(times, utc=True).tz_convert('Asia/Shanghai')
    q = np.load(ROOT/'outputs/gru_beta_neutral_20261008/risk_context.npz'); model_symbols = json.load(open(DATA/'symbols.json'))
    mapping = np.array([lookup[s] for s in model_symbols]); by15 = {str(clock[i].date()): i for i in np.flatnonzero(clock.hour == 15)}
    industry = np.full((len(all_days), U), -1, int); logcap = np.full((len(all_days), U), np.nan)
    for j, d in enumerate(all_days):
        if d in by15:
            industry[j, mapping] = q['industry'][by15[d]]; logcap[j, mapping] = q['log_cap'][by15[d]]
    s15, h15 = R.signals(15, all_days, symbols)
    frozen_member = np.load(DATA/'members.npy', mmap_mode='r')
    # ---- risk model, estimated causally over the whole calendar
    risk = RiskModel(all_days, symbols, ret, amount, logcap, industry, bench_ret)
    X_by_day = {}; cov_by_day = {}; sectors_by_day = {}
    X_prev = None; t0 = time.time()
    for j in range(len(all_days)):
        if X_prev is not None: risk.update(j, X_prev)
        X = risk.exposures(j); X_prev = X
        X_by_day[j] = X; cov_by_day[j] = risk.covariance(); sectors_by_day[j] = X[:, 1:1 + risk.K_ind]
    print('risk model built', round(time.time() - t0), 's; factor history', len(risk.factor_returns), 'K', risk.K, flush=True)
    # ---- event list (same as replay.main)
    pcgroups = {d: g[g.stock_replicable] for d, g in pc.groupby('date')}
    dates = []; refs = []; a15 = []; signal_marks = []; fills = []; execution_prices = []; closes = []; pcf_ids = []; Xd = {}; Cd = {}; Sd = {}; members = []; ic_by_event = []
    for j, day in enumerate(all_days):
        if j == 0: continue
        prior = all_days[j - 1]
        if prior not in pcgroups or prior not in s15 or prior < '2023-03-13': continue
        g = pcgroups[prior]; m = meta.loc[prior]; vday = m.valuation_date
        ids = np.array([lookup[symbol(c)] for c in g.code]); prev = rawclose[daypos[vday], ids]
        missing = ~np.isfinite(prev) | (prev <= 0); reference = np.zeros(U)
        values = g.quantity.to_numpy() * prev / float(m.nav_per_cu)
        if missing.any(): reference[:] = np.nan
        else:
            reference[ids] = values
            if reference.sum() > .98: reference *= .98 / reference.sum()
        k = len(dates)
        dates.append(day); refs.append(reference); a15.append(s15[prior]); signal_marks.append(close[j - 1]); fills.append(can[j])
        execution_prices.append(op[j]); closes.append(close[j]); pcf_ids.append(ids)
        mem = np.zeros(U, bool); mem[mapping] = frozen_member[by15[day]] if day in by15 else frozen_member[by15[prior]]; members.append(mem)
        ic_by_event.append(VAL_IC[int(prior[:4])])
        Xd[k] = X_by_day[j - 1]; Cd[k] = cov_by_day[j - 1]; Sd[k] = sectors_by_day[j - 1]   # information through prior close
    refs = np.asarray(refs); a15 = np.asarray(a15); closes = np.asarray(closes)
    b = benchmark.loc[dates].to_numpy(); pb = pricebench.loc[dates].to_numpy()
    results = {}; scores = []; yearly = []
    armed_from = next((dates[k] for k in range(len(dates)) if Cd[k][0] is not None), None)
    for arm in ['P0', 'P1', 'O1', 'O2']:
        t0 = time.time()
        d, diag = simulate(arm, dates, refs, a15, np.asarray(signal_marks), np.asarray(execution_prices), np.asarray(fills), closes, symbols, risk, Xd, Cd, Sd, pcf_ids, np.asarray(members), ic_by_event)
        d['H00905_nav'] = b / b[0]; d['price_index_nav'] = pb / pb[0]
        d.to_csv(DEST/f'{arm}_daily.csv', index=False)
        if len(diag): diag.to_csv(DEST/f'{arm}_optimizer.csv', index=False)
        rec = R.summary(d, b, pb); rec['arm'] = arm; rec['seconds'] = round(time.time() - t0)
        if len(diag) and 'status' in diag:
            rec['optimizer_days'] = int((diag.status != 'warmup_hold_P0').sum()); rec['optimizer_failed_days'] = int(diag['fallback_hold_basket'].fillna(False).astype(bool).sum()) if 'fallback_hold_basket' in diag else 0
            for c in ['realised_active_share', 'realised_max_industry_active', 'realised_max_style_active', 'realised_vs_target_L1']:
                if c in diag: rec[c + '_mean'] = float(diag[c].mean()); rec[c + '_max'] = float(diag[c].max())
            rec['mean_active_share'] = float(diag.get('active_share', pd.Series(np.nan)).mean())
        scores.append(rec); results[arm] = d
        for year in range(2023, 2027):
            ids = np.flatnonzero(d.date.str.startswith(str(year))); start = max(int(ids[0]) - 1, 0); stop = int(ids[-1]) + 1
            r = R.summary(d.iloc[start:stop], b[start:stop], pb[start:stop]); r.update(arm=arm, year=year); yearly.append(r)
        # post-arming window (fair comparison for O arms)
        if armed_from:
            start = int(np.flatnonzero(d.date >= armed_from)[0])     # rebase at the armed day's close (same endpoint as paired stats)
            r = R.summary(d.iloc[start:], b[start:], pb[start:]); r.update(arm=arm, year='armed_' + armed_from); yearly.append(r)
        print('ARM_COMPLETE', arm, round(rec['net_return'], 4), round(rec['annualized_relative_excess'], 4), flush=True)
    pd.DataFrame(scores).to_csv(DEST/'scorecard.csv', index=False); pd.DataFrame(yearly).to_csv(DEST/'yearly_scorecard.csv', index=False)
    # paired comparisons on the armed window
    comp = []; halves = []
    base_ids = np.flatnonzero(results['P0'].date >= armed_from)
    for x, y in [('P1', 'P0'), ('O1', 'P0'), ('O2', 'P0'), ('O1', 'P1'), ('O2', 'P1')]:
        nx = results[x].nav.to_numpy()[base_ids]; ny = results[y].nav.to_numpy()[base_ids]; n = len(nx) - 1
        rx = nx[1:] / nx[:-1]; ry = ny[1:] / ny[:-1]; rel_path = rx / ry
        annual = float(np.prod(rel_path) ** (252 / n) - 1)
        rng = np.random.default_rng(20261011); ix = (rng.integers(0, n, size=(2000, int(np.ceil(n / 20)), 1)) + np.arange(20)) % n; ix = ix.reshape(2000, -1)[:, :n]
        boot_cagr = np.quantile(np.prod(rel_path[ix], axis=1) ** (252 / n) - 1, [.025, .975])          # compounded, same statistic as the headline
        diff = (rx - 1) - (ry - 1); boot_arith = np.quantile(diff[ix].mean(axis=1) * 252, [.025, .975])
        te_vs_y = float(np.std(rel_path - 1, ddof=1) * np.sqrt(252)); ir = float(np.mean(diff) / np.std(diff, ddof=1) * np.sqrt(252))
        wx = R.relative_windows(nx, ny)     # 50-day windows of x relative to y
        comp.append(dict(arm=x, versus=y, window_start=armed_from, days=n, annualized_relative_cagr=annual, cagr_boot95_low=float(boot_cagr[0]), cagr_boot95_high=float(boot_cagr[1]),
                         arithmetic_mean_diff_annualized=float(np.mean(diff) * 252), arith_boot95_low=float(boot_arith[0]), arith_boot95_high=float(boot_arith[1]),
                         tracking_error_vs_versus=te_vs_y, information_ratio_vs_versus=ir,
                         w50_vs_versus_mean=float(wx.mean()), w50_vs_versus_p10=float(np.quantile(wx, .1)), w50_vs_versus_worst=float(wx.min()), w50_vs_versus_win=float((wx > 0).mean())))
        # calendar half-years of x relative to y, generated from the saved NAVs
        s = pd.Series(np.cumprod(rel_path), index=pd.to_datetime(results[x].date.to_numpy()[base_ids][1:]))
        last = s.groupby([s.index.year, (s.index.month > 6).astype(int) + 1]).last(); starts = [1.0] + last.tolist()[:-1]
        for (yy, hh), l, st in zip(last.index, last, starts): halves.append(dict(arm=x, versus=y, period=f'{yy}H{hh}', relative_growth_pp=float((l / st - 1) * 100)))
    pd.DataFrame(comp).to_csv(DEST/'paired_comparisons.csv', index=False); pd.DataFrame(halves).to_csv(DEST/'calendar_half_years.csv', index=False)
    write(DEST/'run_receipt.json', dict(armed_from=armed_from, signal_hashes=h15, code_sha256=sha(__file__)))
    print(pd.DataFrame(scores)[['arm', 'net_return', 'annualized_relative_excess', 'tracking_error', 'information_ratio', 'average_turnover', 'w50_p10', 'w50_worst', 'w50_win_rate']].round(4).to_string(index=False))
    pd.set_option('display.width', 250); print(pd.DataFrame(comp).round(4).to_string(index=False)); print(pd.DataFrame(halves).pivot(index='period', columns=['arm', 'versus'], values='relative_growth_pp').round(2).to_string())


if __name__ == '__main__':
    main()
