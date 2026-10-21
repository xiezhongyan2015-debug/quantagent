"""LPPLS 泡沫置信度（2026-10-17）。兴证 2024-08 前作未公开细节，这里按学界口径实现：
  模型 ln p(t) = A + B f + C1 f cos(w ln(tc-t)) + C2 f sin(w ln(tc-t))，f = (tc-t)^m；给定 (tc, m, w) 时 A,B,C1,C2 线性求解（Filimonov-Sornette）。
  窗口：对每个 t2，dt = 750, 745, ..., 125 个交易日，共 126 个（Demos-Sornette 2016 / 兴证"多窗口"）。
  搜索空间与过滤（Shu & Zhu 2019，沪深300 实证）：原文搜索 m∈[0,1]、w∈[1,50]、tc∈[t2, t2+dt/3]；本实现把 m 收到 [0.1,0.9]、w 收到 [2,25]
  （原范围下优化器在中证500上系统性退化到 m→0、|B|→100 的共线解，开源 lppls 包同样限定），并从网格前 3 名分别细化；
  过滤 m∈[0.01,0.99]、w∈[2,25]、tc∈[t2, t2+dt/5]、振荡次数 (w/pi)·ln((tc-t1)/(tc-t2)) >= 2.5、
  阻尼 m|B|/(w·sqrt(C1²+C2²)) >= 1、最大相对误差 max|p̂-p|/p <= 0.15。未做 Lomb 与单位根检验（登记为偏离）。
  置信度：通过过滤且 B<0 的窗口占比 = 正向泡沫（加速上涨）；B>0 = 负向泡沫（加速下跌）。
兴证消散规则（《择时的艺术》）：近 40 个交易日置信度峰值 > 0.3 且 峰值 − 当前 > 0.2 → 负向泡沫消散看多 / 正向泡沫消散看空。
"""
import numpy as np
from scipy.optimize import minimize

WINDOWS = list(range(750, 124, -5))  # 126 windows


def _linear(t, y, tc, m, w):
    dtc = tc - t
    f = dtc ** m; lg = np.log(dtc)
    X = np.column_stack([np.ones_like(t), f, f * np.cos(w * lg), f * np.sin(w * lg)])
    coef, *_ = np.linalg.lstsq(X, y, rcond=None)
    res = y - X @ coef
    return coef, res


def _grid(t, y, t2, dt, n_tc=5, n_m=5, n_w=10):
    """vectorised coarse grid over (tc, m, w); returns best params by SSE"""
    tcs = t2 + dt / 3 * (np.arange(1, n_tc + 1) / n_tc) * 0.999 + 1e-3
    ms = np.linspace(0.1, 0.9, n_m); ws = np.exp(np.linspace(np.log(2), np.log(25), n_w))
    TC, M, W = np.meshgrid(tcs, ms, ws, indexing='ij'); TC, M, W = TC.ravel(), M.ravel(), W.ravel()
    dtc = TC[:, None] - t[None, :]
    f = dtc ** M[:, None]; lg = np.log(dtc)
    X = np.stack([np.ones_like(f), f, f * np.cos(W[:, None] * lg), f * np.sin(W[:, None] * lg)], axis=2)  # K,n,4
    XtX = np.einsum('kni,knj->kij', X, X); Xty = np.einsum('kni,n->ki', X, y)
    try:
        coef = np.linalg.solve(XtX + 1e-10 * np.eye(4)[None], Xty[..., None])[..., 0]
    except np.linalg.LinAlgError:
        return None
    sse = ((y[None, :] - np.einsum('kni,ki->kn', X, coef)) ** 2).sum(axis=1)
    ks = np.argsort(np.where(np.isfinite(sse), sse, np.inf))[:3]
    return [(TC[k], M[k], W[k]) for k in ks]


def fit_window(logp, dt):
    """fit the last `dt` points of logp (array); t in trading-day units with t2 = dt-1"""
    y = logp[-dt:]; t = np.arange(dt, dtype=float); t1, t2 = 0.0, float(dt - 1)
    g = _grid(t, y, t2, dt)
    if g is None: return None
    lo = np.array([t2 + 1e-3, 0.1, 2.0]); hi = np.array([t2 + dt / 3, 0.9, 25.0])

    def sse(p):
        p = np.clip(p, lo, hi); _, r = _linear(t, y, *p); return float(r @ r)

    best = None
    for start in g:
        r = minimize(sse, np.array(start), method='L-BFGS-B', bounds=list(zip(lo, hi)), options=dict(maxiter=200))
        if best is None or r.fun < best.fun: best = r
    tc, m, w = np.clip(best.x, lo, hi)
    coef, res = _linear(t, y, tc, m, w); A, B, C1, C2 = coef
    C = float(np.hypot(C1, C2))
    p_hat = np.exp(y - res); p = np.exp(y)
    max_rel = float(np.max(np.abs(p_hat - p) / p))
    osc = (w / np.pi) * np.log((tc - t1) / (tc - t2)) if tc > t2 else 0.0
    damping = (m * abs(B)) / (w * C) if C > 0 else np.inf
    ok = (0.01 <= m <= 0.99) and (2 <= w <= 25) and (t2 <= tc <= t2 + dt / 5) and osc >= 2.5 and damping >= 1 and max_rel <= 0.15
    return dict(dt=dt, tc=float(tc - t2), m=float(m), w=float(w), B=float(B), ok=bool(ok), max_rel=max_rel, osc=float(osc), damping=float(damping))


def confidence(close, windows=WINDOWS):
    """close: 1-D array of prices ending at t2 (needs >= max(windows) points). Returns (conf_pos, conf_neg, n_windows, fits)."""
    logp = np.log(np.asarray(close, float)); fits = []
    for dt in windows:
        if len(logp) < dt: continue
        f = fit_window(logp, dt)
        if f is not None: fits.append(f)
    n = len(fits)
    if n == 0: return np.nan, np.nan, 0, fits
    pos = sum(1 for f in fits if f['ok'] and f['B'] < 0) / n
    neg = sum(1 for f in fits if f['ok'] and f['B'] > 0) / n
    return pos, neg, n, fits


def dissipation(series, lookback=40, peak_min=0.3, drop=0.2):
    """兴证规则：近 lookback 日峰值 > peak_min 且 峰值 − 当前 > drop。series: pandas Series of daily confidence."""
    s = series.dropna()
    if len(s) == 0: return dict(peak=np.nan, now=np.nan, fired=False)
    win = s.iloc[-lookback:]; peak = float(win.max()); now = float(s.iloc[-1])
    return dict(peak=peak, peak_date=str(win.idxmax().date()) if hasattr(win.idxmax(), 'date') else str(win.idxmax()), now=now, fired=bool(peak > peak_min and peak - now > drop))
