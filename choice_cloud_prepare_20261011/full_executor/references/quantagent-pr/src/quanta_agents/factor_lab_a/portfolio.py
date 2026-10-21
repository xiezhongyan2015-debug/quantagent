"""Pure-factor portfolio statistics: turns a daily score panel into return series and Sharpe.

Entry at next open (t+1), so a position formed from date-t scores earns label_1 = open[t+2]/open[t+1]-1
on its first day. Two holding schemes:
  - daily: rebalance every day to the current top decile (high turnover)
  - tranche_k: k overlapping tranches; each day's top decile is held for k sessions (turnover ~1/k)
Universe benchmark = equal-weight eligible universe (same entry timing). Excess = portfolio - universe.
Cost model: one-way cost rate x daily turnover (fraction of portfolio replaced).
"""
from __future__ import annotations
import numpy as np
import pandas as pd

TARGET_YEARS = (2019, 2020, 2021, 2022, 2023, 2024)


def _top_mask(pct: np.ndarray, top_frac: float) -> np.ndarray:
    return pct >= (1.0 - top_frac)


def portfolio_series(pct: np.ndarray, label1: np.ndarray, mask: np.ndarray, dates: pd.DatetimeIndex, top_frac=0.1, hold=1,
                     bottom=False) -> dict[str, pd.Series]:
    """pct: per-date percentile ranks (NaN outside mask). label1: next-day open-to-open return (date t row =
    return earned on the first holding day of a position formed at t). Returns daily series aligned to the
    date the return is EARNED (t+1 ... t+hold)."""
    n_dates, n_codes = pct.shape
    valid = np.isfinite(pct) & mask
    sel = _top_mask(np.where(valid, pct, -1.0), top_frac) if not bottom else (np.where(valid, pct, 2.0) <= top_frac)
    # weights formed at t, earning label1 at row t (first day), row t+1 (second day) ... need forward returns of
    # subsequent days: return earned on day t+j (j=0..hold-1) by a position formed at t is label1 shifted by j.
    w = sel.astype(np.float32)
    w_sum = w.sum(axis=1)
    w = np.where(w_sum[:, None] > 0, w / np.maximum(w_sum, 1)[:, None], 0.0)
    uni = mask.astype(np.float32)
    uni = uni / np.maximum(uni.sum(axis=1), 1)[:, None]
    lab = np.where(np.isfinite(label1), label1, 0.0)
    port = np.zeros(n_dates); bench = np.zeros(n_dates); turn = np.zeros(n_dates)
    funded = np.zeros(n_dates)          # number of tranches that actually hold names on each day
    eff = np.zeros_like(w)              # effective weights (average over funded tranches) for turnover
    for j in range(hold):
        # position formed at t contributes on row t+j with the return of row t+j (label1 at t+j is the
        # open[t+j+2]/open[t+j+1] return, i.e. the (j+1)-th holding day)
        wj = np.zeros_like(w)
        wj[j:] = w[: n_dates - j] if j else w
        port += (wj * lab).sum(axis=1)
        funded += (wj.sum(axis=1) > 0)
        eff += wj
    # capital is spread over the tranches that exist: on the first hold-1 days of a score history (and on days
    # whose scores are missing) the book is not left partly in cash against a fully invested benchmark
    port = port / np.maximum(funded, 1)
    eff = eff / np.maximum(funded, 1)[:, None]
    bench = (uni * lab).sum(axis=1)
    turn[1:] = 0.5 * np.abs(eff[1:] - eff[:-1]).sum(axis=1)
    live = (funded > 0) & (mask.sum(axis=1) > 0)
    idx = dates
    return {"portfolio": pd.Series(port, index=idx), "benchmark": pd.Series(bench, index=idx),
            "excess": pd.Series(port - bench, index=idx), "turnover": pd.Series(turn, index=idx),
            "coverage": pd.Series(valid.sum(axis=1), index=idx), "live": pd.Series(live, index=idx)}


def summarize(series: dict[str, pd.Series], years=TARGET_YEARS, cost_rate=0.001) -> dict:
    """Annualised statistics over LIVE days only (days on which the book holds names and the universe exists);
    days without a book (before the first score, purged dates of an old score file) do not dilute the mean."""
    ex = series["excess"]; tv = series["turnover"]
    live = series.get("live")
    live = live.to_numpy(bool) if live is not None else np.ones(len(ex), bool)
    yrs = ex.index.year
    sel = np.isin(yrs, years) & live
    e = ex[sel]; t = tv[sel]
    net = e - cost_rate * t
    def ann(s):
        return float(s.mean() * 252)
    def sharpe(s):
        sd = s.std(ddof=1)
        return float(s.mean() / sd * np.sqrt(252)) if sd > 0 else float("nan")
    def mdd(s):
        c = (1 + s).cumprod(); peak = c.cummax(); return float(((c / peak) - 1).min())
    out = {"gross_excess_ann": ann(e), "gross_sharpe": sharpe(e), "net_excess_ann": ann(net), "net_sharpe": sharpe(net),
           "daily_turnover": float(t.mean()), "cost_rate_one_way": cost_rate, "max_drawdown_gross": mdd(e), "max_drawdown_net": mdd(net),
           "portfolio_ann": ann(series["portfolio"][sel]), "benchmark_ann": ann(series["benchmark"][sel]),
           "annual": {}}
    for y in years:
        m = (yrs == y) & live
        ey = ex[m]; ny = (ex - cost_rate * tv)[m]
        out["annual"][str(y)] = {"gross_excess": ann(ey), "gross_sharpe": sharpe(ey), "net_excess": ann(ny), "net_sharpe": sharpe(ny),
                                 "turnover": float(tv[m].mean()) if m.any() else float("nan"), "live_days": int(m.sum())}
    out["live_days"] = int(sel.sum())
    out["years_net_positive"] = int(sum(1 for y in years if out["annual"][str(y)]["net_excess"] > 0))
    return out


def evaluate_scores(pct: np.ndarray, label1: np.ndarray, mask: np.ndarray, dates, top_frac=0.1, holds=(1, 5), cost_rate=0.001,
                    years=TARGET_YEARS) -> dict:
    res = {}
    for h in holds:
        s = portfolio_series(pct, label1, mask, dates, top_frac=top_frac, hold=h)
        res[f"long_top{int(top_frac*100)}_hold{h}"] = summarize(s, years, cost_rate)
    # long-short (top - bottom decile), hold 5
    s_top = portfolio_series(pct, label1, mask, dates, top_frac=top_frac, hold=5)
    s_bot = portfolio_series(pct, label1, mask, dates, top_frac=top_frac, hold=5, bottom=True)
    ls = {"excess": s_top["portfolio"] - s_bot["portfolio"], "turnover": s_top["turnover"] + s_bot["turnover"],
          "portfolio": s_top["portfolio"], "benchmark": s_bot["portfolio"], "live": s_top["live"] & s_bot["live"]}
    res["long_short_hold5"] = summarize(ls, years, cost_rate)
    return res
