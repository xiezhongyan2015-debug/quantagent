"""Self-built Barra CNE5-style risk model for A shares from local data only (no paid licence).  2026-10-15.

Why: no Barra licence; the mentor's question "is the 排雷/倾斜 increment a known style exposure" needs a proper
style model.  This follows the CNE5 China model definitions (10 style factors, industry factors, country factor,
cap-weighted constrained WLS factor returns, EWMA covariance with Newey-West, specific risk) with the approximations
listed under DEVIATIONS.  Everything is point-in-time: exposures at close t use data through t; factor returns of
session t+1 are regressed on exposures at t.

Data (all local):
  c5_core/data/A_Layer_Live/panel/*.parquet      all-A daily panel 2022-06-01..2026-10-09 (returns, caps, turnover,
                                                 PIT fundamentals fd_*, analyst consensus)
  outputs/barra_cne5_local/vendor_returns_2020_2022H1.parquet   vendor daily returns 2020-01..2022-05 (warm-up)
  outputs/barra_cne5_local_sw_industry_baostock_20261015.csv    证监会行业 (baostock, current snapshot)

DEVIATIONS from CNE5 (documented, not hidden):
  * industry = 证监会 2-digit classes grouped into 24 groups (static snapshot of 2026-10); CNE5 uses 32 CITIC industries
    with history.
  * risk-free rate taken as 0 (China 1y deposit ~1.5%: negligible cross-sectionally).
  * EGRLF/EGRSF (analyst long/short-term growth) both proxied by fd_est_g (analyst FY1 net profit / TTM net profit - 1);
    EGRO/SGRO (5-year regression growth) replaced by trailing 3-year slope of TTM net profit / revenue scaled by mean
    absolute level (panel history is 4.3 years).
  * EPIBS proxied by fd_est_ep_fy1 (analyst FY1 net profit / market cap).
  * market return = panel mkt_ret (cap-weighted all-A) after 2022-06, cap-weighted vendor return before.
  * covariance: EWMA (half-life 90 variance / 480 correlation), Newey-West 2 lags; no VRA / eigenfactor adjustment.
  * specific risk: EWMA (half-life 90) of squared specific returns, shrunk 50/50 toward the cap-decile mean; no
    structural model for names with short history.
Outputs (outputs/barra_cne5_local/):
  exposures.parquet      long (date, symbol, 10 styles, industry group, in_estimation_universe, cap)
  factor_returns.parquet daily factor returns (country, 24 industries, 10 styles) with R2 and N
  specific_returns.parquet (date x symbol wide, float32)
  specific_risk.parquet  daily annualised specific vol (date x symbol wide)
  factor_cov_<date>.parquet month-end factor covariance (daily horizon)
  build_receipt.json
"""
from pathlib import Path
import json, time
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
PANEL = ROOT/'c5_core/data/A_Layer_Live/panel'
OUT = ROOT/'outputs/barra_cne5_local'; OUT.mkdir(exist_ok=True)
IND_CSV = OUT.parent/'barra_cne5_local_sw_industry_baostock_20261015.csv'
STYLES = ['SIZE', 'BETA', 'MOMENTUM', 'RESVOL', 'NLSIZE', 'BTOP', 'LIQUIDITY', 'EARNYLD', 'GROWTH', 'LEVERAGE']

# 证监会 2-digit codes -> 24 groups (manufacturing split by chain; services grouped by 门类)
GROUPS = {
    'A': 'AGRI', 'B': 'MINING', 'D': 'UTILITY', 'E': 'CONSTR', 'F': 'TRADE', 'G': 'TRANSPORT', 'H': 'HOTEL', 'I': 'TMT_SERV',
    'J': 'FINANCE', 'K': 'REALEST', 'L': 'BIZ_SERV', 'M': 'TECH_SERV', 'N': 'ENVIRON', 'O': 'RESIDENT', 'P': 'EDU', 'Q': 'HEALTH_SERV', 'R': 'CULTURE', 'S': 'OTHER',
}
C_GROUPS = [(13, 16, 'C_FOOD'), (17, 19, 'C_TEXTILE'), (20, 24, 'C_LIGHT'), (25, 26, 'C_CHEM'), (27, 28, 'C_PHARMA'), (29, 31, 'C_MATERIAL'),
            (32, 33, 'C_METAL'), (34, 35, 'C_MACHINE'), (36, 37, 'C_TRANSEQ'), (38, 38, 'C_ELECTRIC'), (39, 39, 'C_ELECTRON'), (40, 43, 'C_INSTR_OTHER')]


def group_of(code):
    if not isinstance(code, str) or not code: return 'UNKNOWN'
    letter = code[0]
    if letter == 'C':
        try: n = int(code[1:3])
        except ValueError: return 'C_INSTR_OTHER'
        for lo, hi, g in C_GROUPS:
            if lo <= n <= hi: return g
        return 'C_INSTR_OTHER'
    return GROUPS.get(letter, 'OTHER')


def load_panel(name, codes=None):
    x = pd.read_parquet(PANEL/f'{name}.parquet'); x.index = pd.to_datetime(x.index)
    x.columns = [c[2:] + '.' + c[:2] for c in x.columns]
    return x if codes is None else x.reindex(columns=codes)


def ewm_weights(n, half_life):
    w = 0.5 ** (np.arange(n)[::-1] / half_life); return w / w.sum()


def rolling_weighted(fn, R, M, window, half_life, min_obs, t_index):
    """Apply fn(window_returns, window_market, weights) at each t in t_index over trailing `window` rows."""
    out = np.full((len(t_index), R.shape[1]), np.nan); w_full = ewm_weights(window, half_life)
    for k, t in enumerate(t_index):
        lo = t - window + 1
        if lo < 0: continue
        r = R[lo:t + 1]; m = M[lo:t + 1]; ok = np.isfinite(r) & np.isfinite(m)[:, None]
        cnt = ok.sum(axis=0); enough = cnt >= min_obs
        if not enough.any(): continue
        out[k] = fn(np.where(ok, r, 0.), m, ok, w_full, enough)
    return out


def beta_fn(r, m, ok, w, enough):
    W = ok * w[:, None]; W = W / np.maximum(W.sum(axis=0), 1e-12)
    mm = np.where(np.isfinite(m), m, 0.)[:, None]
    mx = (W * mm).sum(axis=0); mr = (W * r).sum(axis=0)
    cov = (W * (mm - mx) * (r - mr)).sum(axis=0); var = (W * (mm - mx) ** 2).sum(axis=0)
    beta = cov / np.maximum(var, 1e-12); alpha = mr - beta * mx
    resid = (r - alpha - beta * mm) * ok; hs = np.sqrt((W * resid ** 2).sum(axis=0))
    beta[~enough] = np.nan; hs[~enough] = np.nan
    return np.stack([beta, hs])


def dastd_fn(r, m, ok, w, enough):
    W = ok * w[:, None]; W = W / np.maximum(W.sum(axis=0), 1e-12); mr = (W * r).sum(axis=0)
    s = np.sqrt((W * (r - mr) ** 2).sum(axis=0)); s[~enough] = np.nan; return s


def rstr_fn(r, m, ok, w, enough):
    # r here is log excess return (already lagged); weighted sum
    W = ok * w[:, None]; s = (W * r).sum(axis=0) / np.maximum(W.sum(axis=0), 1e-12) * ok.sum(axis=0)  # sum with weights normalised to the available count
    s[~enough] = np.nan; return s


def standardize(x, cap, universe):
    """CNE5: cap-weighted mean zero, equal-weighted unit std over the estimation universe; winsorize at ±3 std."""
    out = np.full_like(x, np.nan); ok = universe & np.isfinite(x) & np.isfinite(cap) & (cap > 0)
    if ok.sum() < 30: return out
    v = x[ok]; c = cap[ok]
    mu = (v * c).sum() / c.sum(); sd = v.std()
    z = (v - mu) / (sd if sd > 1e-12 else 1.); z = np.clip(z, -3, 3)
    mu2 = (z * c).sum() / c.sum(); sd2 = z.std(); z = (z - mu2) / (sd2 if sd2 > 1e-12 else 1.)
    out[ok] = z
    # names outside the estimation universe still get a value on the same scale
    rest = ~universe & np.isfinite(x)
    if rest.any(): out[rest] = np.clip((x[rest] - mu) / (sd if sd > 1e-12 else 1.), -3, 3)
    return out


def orthogonalize(y, X, cap, universe):
    """Residual of y on X (with intercept), fitted by cap-weighted LS on the estimation universe; applied to all."""
    ok = universe & np.isfinite(y) & np.isfinite(X).all(axis=1) & np.isfinite(cap) & (cap > 0)
    if ok.sum() < 30: return y
    A = np.c_[np.ones(ok.sum()), X[ok]]; w = np.sqrt(cap[ok])
    b = np.linalg.lstsq(A * w[:, None], y[ok] * w, rcond=None)[0]
    allok = np.isfinite(y) & np.isfinite(X).all(axis=1); out = np.full_like(y, np.nan)
    out[allok] = y[allok] - np.c_[np.ones(allok.sum()), X[allok]] @ b
    return out


def combine(descs, weights):
    """Weighted combination of standardized descriptors with reweighting over the available ones."""
    D = np.stack(descs); Wt = np.asarray(weights)[:, None] * np.isfinite(D)
    num = np.nansum(np.where(np.isfinite(D), D, 0.) * Wt, axis=0); den = Wt.sum(axis=0)
    out = np.where(den > 0, num / np.maximum(den, 1e-12), np.nan); return out


def main():
    t0 = time.time()
    ret = load_panel('ret'); dates = ret.index; codes = list(ret.columns); N = len(codes); T = len(dates)
    mkt = load_panel('mkt_ret', codes).iloc[:, 0].to_numpy(float)
    cap = load_panel('total_market_cap', codes); turn = load_panel('turnover', codes)
    F = {k: load_panel(k, codes) for k in ['fd_bp', 'fd_ep_ttm', 'fd_ocf_ep', 'fd_est_ep_fy1', 'fd_est_g', 'fd_debt_ib', 'fd_assets', 'fd_equity', 'fd_np_ttm', 'fd_rev_ttm']}
    elig = load_panel('eligible', codes).fillna(0).to_numpy(bool); st = load_panel('is_st', codes).fillna(0).to_numpy(bool); ld = load_panel('listed_days', codes).to_numpy(float)
    print('panel', T, N, round(time.time() - t0), 's', flush=True)
    # ---- warm-up returns from the vendor (2020-01 .. 2022-05)
    v = pd.read_parquet(OUT/'vendor_returns_2020_2022H1.parquet'); v['date'] = pd.to_datetime(v.date)
    vr = v.pivot(index='date', columns='symbol', values='ret').reindex(columns=codes); vc = v.pivot(index='date', columns='symbol', values='total_cap').reindex(columns=codes)
    vr = vr[vr.index < dates[0]]; vc = vc.loc[vr.index]
    capl = vc.shift(1); mk_pre = (vr * capl).sum(axis=1) / capl.where(vr.notna()).sum(axis=1)
    R = np.vstack([vr.to_numpy(float), ret.to_numpy(float)]); M = np.r_[mk_pre.to_numpy(float), mkt]
    all_dates = vr.index.append(dates); P = len(vr); t_index = np.arange(P, P + T)
    print('returns matrix', R.shape, 'warm-up rows', P, flush=True)
    # ---- market-data descriptors
    BETA = np.full((T, N), np.nan); HSIGMA = np.full((T, N), np.nan)
    for k, t in enumerate(t_index):
        lo = t - 251
        if lo < 0: continue
        r = R[lo:t + 1]; m = M[lo:t + 1]; ok = np.isfinite(r) & np.isfinite(m)[:, None]; enough = ok.sum(axis=0) >= 126
        res = beta_fn(np.where(ok, r, 0.), m, ok, ewm_weights(252, 63), enough); BETA[k], HSIGMA[k] = res[0], res[1]
    print('beta done', round(time.time() - t0), 's', flush=True)
    logex = np.log1p(np.where(np.isfinite(R), R, np.nan)) - np.log1p(np.where(np.isfinite(M), M, 0.))[:, None]
    RSTR = np.full((T, N), np.nan)
    for k, t in enumerate(t_index):
        hi = t - 21; lo = hi - 503
        if lo < 0: continue
        r = logex[lo:hi + 1]; ok = np.isfinite(r); enough = ok.sum(axis=0) >= 252
        RSTR[k] = rstr_fn(np.where(ok, r, 0.), None, ok, ewm_weights(504, 126), enough)
    DASTD = np.full((T, N), np.nan)
    for k, t in enumerate(t_index):
        lo = t - 251
        if lo < 0: continue
        r = logex[lo:t + 1]; ok = np.isfinite(r); enough = ok.sum(axis=0) >= 126
        DASTD[k] = dastd_fn(np.where(ok, r, 0.), None, ok, ewm_weights(252, 42), enough)
    # CMRA: range of cumulative log returns over the last 12 months (21-day blocks)
    CMRA = np.full((T, N), np.nan); lr = np.log1p(np.where(np.isfinite(R), R, 0.)); cs = np.cumsum(lr, axis=0); okr = np.isfinite(R)
    for k, t in enumerate(t_index):
        if t - 252 < 0: continue
        pts = np.stack([cs[t - 21 * j] for j in range(0, 13)])            # cumulative at month boundaries
        z = pts[0] - pts[1:]                                               # Z(T) for T=1..12
        cnt = okr[t - 251:t + 1].sum(axis=0); CMRA[k] = np.where(cnt >= 126, z.max(axis=0) - z.min(axis=0), np.nan)
    print('momentum/vol done', round(time.time() - t0), 's', flush=True)
    tr = turn.to_numpy(float); tr = np.where(np.isfinite(tr) & (tr >= 0), tr, np.nan)
    STOM = np.full((T, N), np.nan); STOQ = np.full((T, N), np.nan); STOA = np.full((T, N), np.nan)
    tsum = pd.DataFrame(tr).rolling(21, min_periods=15).sum().to_numpy()
    STOM = np.log(np.where(tsum > 0, tsum, np.nan))
    for k in range(T):
        q = [tsum[k - 21 * j] for j in range(0, 3) if k - 21 * j >= 0]; a = [tsum[k - 21 * j] for j in range(0, 12) if k - 21 * j >= 0]
        if len(q) == 3: STOQ[k] = np.log(np.maximum(np.nanmean(np.stack(q), axis=0), 1e-12))
        if len(a) == 12: STOA[k] = np.log(np.maximum(np.nanmean(np.stack(a), axis=0), 1e-12))
    capv = cap.to_numpy(float); LNCAP = np.log(np.where(capv > 0, capv, np.nan))
    # ---- fundamental descriptors
    bp = F['fd_bp'].to_numpy(float); etop = F['fd_ep_ttm'].to_numpy(float); cetop = F['fd_ocf_ep'].to_numpy(float); epibs = F['fd_est_ep_fy1'].to_numpy(float)
    estg = F['fd_est_g'].to_numpy(float); debt = F['fd_debt_ib'].to_numpy(float); assets = F['fd_assets'].to_numpy(float); eq = F['fd_equity'].to_numpy(float)
    np_ttm = F['fd_np_ttm']; rev_ttm = F['fd_rev_ttm']
    def trailing_growth(x, window=756, min_obs=252):
        """slope of a linear trend over the trailing window divided by mean absolute level (CNE5 EGRO/SGRO analogue)."""
        g = np.full((T, N), np.nan); X = x.to_numpy(float)
        step = 21
        for k in range(0, T):
            lo = max(0, k - window + 1); seg = X[lo:k + 1:step]; n = seg.shape[0]
            if n < max(4, min_obs // step): continue
            tt = np.arange(n, dtype=float); tt -= tt.mean(); ok = np.isfinite(seg); cnt = ok.sum(axis=0)
            segz = np.where(ok, seg, 0.); mean = segz.sum(axis=0) / np.maximum(cnt, 1)
            slope = ((segz - mean) * tt[:, None] * ok).sum(axis=0) / np.maximum((tt[:, None] ** 2 * ok).sum(axis=0), 1e-12) * (252 / step)
            level = np.abs(segz).sum(axis=0) / np.maximum(cnt, 1)
            g[k] = np.where((cnt >= max(4, min_obs // step)) & (level > 0), slope / level, np.nan)
        return g
    EGRO = trailing_growth(np_ttm); SGRO = trailing_growth(rev_ttm)
    with np.errstate(all='ignore'):
        MLEV = np.where(capv > 0, (capv + np.where(np.isfinite(debt), debt, 0.)) / capv, np.nan)
        DTOA = np.where(assets > 0, debt / assets, np.nan)
        BLEV = np.where(eq > 0, (eq + np.where(np.isfinite(debt), debt, 0.)) / eq, np.nan)
    print('descriptors done', round(time.time() - t0), 's', flush=True)
    # ---- industry
    ind = pd.read_csv(IND_CSV, dtype=str).fillna('')
    ind['symbol'] = ind.code.str[3:] + '.' + ind.code.str[:2].str.upper(); gmap = dict(zip(ind.symbol, ind.industry.map(group_of)))
    groups = np.array([gmap.get(c, 'UNKNOWN') for c in codes]); glist = sorted(set(groups) - {'UNKNOWN'})
    print('industry groups', len(glist), 'unknown names', int((groups == 'UNKNOWN').sum()), flush=True)
    # ---- standardize & build factors day by day
    universe = elig & ~st & (ld >= 120) & np.isfinite(capv) & (capv > 0) & np.isfinite(ret.to_numpy(float)) & (groups != 'UNKNOWN')[None, :]
    EXPO = np.full((T, N, len(STYLES)), np.nan, np.float32)
    for k in range(T):
        u = universe[k]; c = capv[k]
        if u.sum() < 500: continue
        S = lambda x: standardize(x, c, u)
        size = S(LNCAP[k]); beta = S(BETA[k])
        mom = S(RSTR[k])
        resvol_raw = combine([S(DASTD[k]), S(CMRA[k]), S(HSIGMA[k])], [.74, .16, .10]); resvol = S(orthogonalize(S(resvol_raw), np.c_[beta, size], c, u))
        nl = S(orthogonalize(size ** 3, size[:, None], c, u))
        btop = S(bp[k])
        liq_raw = combine([S(STOM[k]), S(STOQ[k]), S(STOA[k])], [.35, .35, .30]); liq = S(orthogonalize(S(liq_raw), size[:, None], c, u))
        ey = S(combine([S(epibs[k]), S(etop[k]), S(cetop[k])], [.68, .21, .11]))
        gr = S(combine([S(estg[k]), S(estg[k]), S(EGRO[k]), S(SGRO[k])], [.18, .11, .24, .47]))
        lev = S(combine([S(MLEV[k]), S(DTOA[k]), S(BLEV[k])], [.38, .35, .27]))
        EXPO[k] = np.stack([size, beta, mom, resvol, nl, btop, liq, ey, gr, lev], axis=1)
    print('exposures done', round(time.time() - t0), 's', flush=True)
    # ---- factor returns: r_{t+1} on X_t, cap-weighted constrained WLS (industry returns cap-weighted sum to zero)
    K_ind = len(glist); gi = {g: i for i, g in enumerate(glist)}; gidx = np.array([gi.get(g, -1) for g in groups])
    Rp = ret.to_numpy(float); fr_rows = []; spec = np.full((T, N), np.nan, np.float32); names = ['COUNTRY'] + glist + STYLES
    for k in range(T - 1):
        X = EXPO[k].astype(float); u = universe[k] & np.isfinite(X).all(axis=1) & np.isfinite(Rp[k + 1]) & (gidx >= 0)
        if u.sum() < 500: continue
        n = u.sum(); c = capv[k][u]; w = np.sqrt(c); w = np.minimum(w, np.quantile(w, .95)); w /= w.sum()
        D = np.zeros((n, K_ind)); D[np.arange(n), gidx[u]] = 1.
        A = np.c_[np.ones(n), D, X[u]]                                     # 1 + K_ind + 10
        # constraint: sum_j capw_j f_ind_j = 0 -> express f_last in terms of the others
        cw = np.array([c[gidx[u] == j].sum() for j in range(K_ind)]); cw /= cw.sum()
        Rm = np.eye(1 + K_ind + len(STYLES)); last = K_ind                 # column index of the last industry in A
        Rm = np.delete(Rm, last, axis=1); Rm[last, 1:K_ind] = -cw[:-1] / max(cw[-1], 1e-12)
        Ar = A @ Rm; y = np.clip(Rp[k + 1][u], -.2, .2)
        sw = np.sqrt(w); b_r = np.linalg.lstsq(Ar * sw[:, None], y * sw, rcond=None)[0]; f = Rm @ b_r
        e = y - A @ f; r2 = 1 - (w * e ** 2).sum() / max((w * (y - (w * y).sum()) ** 2).sum(), 1e-12)
        fr_rows.append(dict(date=dates[k + 1], N=int(n), R2=float(r2), **{nm: float(v) for nm, v in zip(names, f)}))
        full = np.full(N, np.nan); full[u] = e; spec[k + 1] = full
    FR = pd.DataFrame(fr_rows).set_index('date'); FR.to_parquet(OUT/'factor_returns.parquet'); print('factor returns', FR.shape, 'mean R2', round(FR.R2.mean(), 3), round(time.time() - t0), 's', flush=True)
    pd.DataFrame(spec, index=dates, columns=codes).to_parquet(OUT/'specific_returns.parquet')
    # ---- covariance (EWMA, Newey-West 2 lags) at month-ends; specific risk EWMA with shrinkage
    Fm = FR[names].to_numpy(float); fd = FR.index
    def ewma_cov(X, hl_var, hl_corr, lags=2):
        n = len(X); wv = ewm_weights(n, hl_var); wc = ewm_weights(n, hl_corr)
        mu = wv @ X; Xc = X - mu
        var = (wv[:, None] * Xc ** 2).sum(axis=0)
        C = (Xc * wc[:, None]).T @ Xc
        for L in range(1, lags + 1):
            G = (Xc[L:] * wc[L:, None]).T @ Xc[:-L]; C += (1 - L / (lags + 1)) * (G + G.T)
        d = np.sqrt(np.maximum(np.diag(C), 1e-16)); corr = C / np.outer(d, d); corr = np.clip(corr, -1, 1); np.fill_diagonal(corr, 1.)
        cov = corr * np.outer(np.sqrt(var), np.sqrt(var))
        ev, V = np.linalg.eigh((cov + cov.T) / 2); cov = (V * np.maximum(ev, 1e-12)) @ V.T
        return cov
    month_ends = [d for i, d in enumerate(fd) if i == len(fd) - 1 or fd[i + 1].month != d.month]
    for d in month_ends:
        i = fd.get_loc(d)
        if i < 120: continue
        cov = ewma_cov(Fm[:i + 1], 90, 480); pd.DataFrame(cov, index=names, columns=names).to_parquet(OUT/f'factor_cov_{d.strftime("%Y-%m-%d")}.parquet')
    e2 = pd.DataFrame(spec ** 2, index=dates, columns=codes)
    sr = e2.ewm(halflife=90, min_periods=60).mean()
    capdf = pd.DataFrame(capv, index=dates, columns=codes)
    dec = capdf.rank(axis=1, pct=True).mul(10).fillna(0).astype(int).clip(0, 9)
    prior = sr.copy()
    for k in range(T):
        row = sr.iloc[k]; g = dec.iloc[k]; m = row.groupby(g).transform('mean'); prior.iloc[k] = m
    srisk = np.sqrt((0.5 * sr + 0.5 * prior.where(sr.notna())) * 252)
    srisk.astype(np.float32).to_parquet(OUT/'specific_risk.parquet')
    # ---- long exposures
    rows = []
    for k in range(T):
        u = universe[k]; keep = np.isfinite(EXPO[k]).any(axis=1)
        df = pd.DataFrame(EXPO[k][keep], columns=STYLES); df.insert(0, 'symbol', np.array(codes)[keep]); df.insert(0, 'date', dates[k])
        df['industry'] = groups[keep]; df['in_universe'] = u[keep]; df['cap'] = capv[k][keep].astype(np.float32); rows.append(df)
    E = pd.concat(rows, ignore_index=True); E.to_parquet(OUT/'exposures.parquet', index=False)
    rec = dict(built='2026-10-15', dates=[str(dates[0].date()), str(dates[-1].date())], names=N, styles=STYLES, industries=glist, mean_R2=float(FR.R2.mean()),
               factor_return_tstats={nm: float(FR[nm].mean() / FR[nm].std() * np.sqrt(len(FR))) for nm in STYLES}, seconds=round(time.time() - t0),
               deviations=__doc__.split('DEVIATIONS from CNE5')[1].split('Outputs')[0].strip())
    json.dump(rec, open(OUT/'build_receipt.json', 'w'), ensure_ascii=False, indent=1)
    print(json.dumps({k: v for k, v in rec.items() if k != 'deviations'}, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
