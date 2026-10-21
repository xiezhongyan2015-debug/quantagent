"""A17 item 3a - shared helpers for the CSI500 periodic-review prediction overlay.

Replicates the CSI 300 / CSI 500 semi-annual sample review (中证指数 定期调整) from panel data only, using
information available at a given prediction date. Nothing here reads a label or a future membership state
except the explicitly named `actual_*` helpers, which are used for scoring accuracy only.

Review calendar (verified against csindex.com.cn methodology + the 2024/2025 announcements):
  data cut-off      : last trading day of April / October   (past-year averages end here)
  announcement      : last Friday of May / November
  effective         : first trading day AFTER the 2nd Friday of June / December
                      (the announcement says "第二个星期五收市后生效")
"""
from __future__ import annotations
import os
import sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
from quanta_agents.factor_lab_a.panel import Panel  # noqa: E402

# ----------------------------------------------------------------------------- calendar


def _second_friday(year: int, month: int) -> pd.Timestamp:
    d = pd.Timestamp(year=year, month=month, day=1)
    first_fri = d + pd.Timedelta(days=(4 - d.dayofweek) % 7)
    return first_fri + pd.Timedelta(days=7)


def _last_friday(year: int, month: int) -> pd.Timestamp:
    eom = pd.Timestamp(year=year, month=month, day=1) + pd.offsets.MonthEnd(0)
    return eom - pd.Timedelta(days=(eom.dayofweek - 4) % 7)


def _prev_td(dates: pd.DatetimeIndex, d: pd.Timestamp) -> pd.Timestamp | None:
    i = dates.searchsorted(d, side="right") - 1
    return dates[i] if i >= 0 else None


def _next_td(dates: pd.DatetimeIndex, d: pd.Timestamp) -> pd.Timestamp | None:
    i = dates.searchsorted(d, side="right")
    return dates[i] if i < len(dates) else None


def _shift_td(dates: pd.DatetimeIndex, d: pd.Timestamp, k: int) -> pd.Timestamp | None:
    i = int(dates.get_indexer([d])[0]) + k
    return dates[i] if 0 <= i < len(dates) else None


def event_calendar(dates: pd.DatetimeIndex, mem500: pd.DataFrame, events: list[tuple[int, int]]) -> list[dict]:
    """One dict per (year, review-month in {6, 12}) with every anchor date mapped onto the panel's trading grid.

    cutoff     : last trading day <= Apr 30 / Oct 31 (past-year window ends here)
    ann        : last trading day <= last Friday of May / November (announcement)
    pred_ann   : the trading day before `ann` -> the second, later prediction date
    eff_true   : first trading day after the 2nd Friday of June / December (real index effective date)
    eff_panel  : the date on which the panel's member_csi500 field actually flips for this review
                 (the instrument files this panel was built from use a month-end effective date until 2026)
    """
    out = []
    dchg = mem500.astype(np.int8).diff().abs().sum(axis=1)
    for year, month in events:
        cut_m, ann_m = (4, 5) if month == 6 else (10, 11)
        cutoff = _prev_td(dates, pd.Timestamp(year=year, month=cut_m, day=1) + pd.offsets.MonthEnd(0))
        ann = _prev_td(dates, _last_friday(year, ann_m))
        eff_true = _next_td(dates, _second_friday(year, month))
        if cutoff is None or ann is None or eff_true is None:
            continue
        win = dchg[(dchg.index >= eff_true - pd.Timedelta(days=5)) & (dchg.index <= eff_true + pd.Timedelta(days=35))]
        eff_panel = win.idxmax() if len(win) and win.max() >= 6 else eff_true
        out.append({"label": f"{year}-{month:02d}", "year": year, "month": month, "cutoff": cutoff, "ann": ann,
                    "pred_ann": _shift_td(dates, ann, -1), "eff_true": eff_true, "eff_panel": eff_panel,
                    "n_change_panel": int(dchg.get(eff_panel, 0))})
    return out


# ----------------------------------------------------------------------------- sample space / averages


def window_mean(df: pd.DataFrame, end: pd.Timestamp, n: int) -> tuple[pd.Series, pd.Series]:
    """Mean and observation count of `df` over the last `n` trading rows ending at `end` (inclusive)."""
    i = int(df.index.get_indexer([end])[0])
    sl = df.iloc[max(0, i - n + 1): i + 1]
    return sl.mean(axis=0, skipna=True), sl.notna().sum(axis=0)


def sample_space(panel: Panel, asof: pd.Timestamp, star_ok: bool, min_listed=63, star_min_listed=252) -> pd.Series:
    """Boolean series over codes: in the CSI sample space at `asof`.
    非ST/非*ST, 非退市整理, 上市时间超过一个季度 (科创板: 超过一年, and only from the Dec-2020 review on)."""
    st = panel["is_st"].loc[asof]
    de = panel["is_delisting"].loc[asof]
    ld = panel["listed_days"].loc[asof]
    close = panel["close"].loc[asof]
    codes = pd.Index(panel.codes)
    is_star = pd.Series([c.startswith("SH68") for c in codes], index=codes)
    need = pd.Series(np.where(is_star.to_numpy(), star_min_listed, min_listed), index=codes)
    ok = (st != 1) & (de != 1) & (ld >= need) & close.notna()
    if not star_ok:
        ok = ok & ~is_star
    return ok.fillna(False)


# ----------------------------------------------------------------------------- the review itself


def _rank_desc(s: pd.Series) -> pd.Series:
    """1-based rank, largest first; NaN stays NaN."""
    return s.rank(ascending=False, method="first")


def review_index(avg_cap: pd.Series, avg_amt: pd.Series, space: pd.Series, old: pd.Series, *,
                 n_target: int, amt_keep: float, amt_keep_old: float, buf_in: int, buf_keep: int,
                 max_replace: int, pre_drop: pd.Series | None = None) -> dict:
    """Generic CSI periodic review.

    space      : securities in the sample space at the cut-off
    old        : current constituents (pre-review)
    pre_drop   : securities removed before the liquidity screen (CSI500: CSI300 samples U top-300 by avg cap)
    amt_keep   : fraction of the remaining securities kept by the traded-amount screen (0.8 -> drop the bottom 20%)
    amt_keep_old : same screen for existing constituents (CSI500 buffer: 0.9)
    buf_in/buf_keep : cap-rank buffer (a new name enters at <= buf_in, an old name is retained at <= buf_keep)
    max_replace: hard cap on the number of replacements (10% of n_target)
    """
    cand = space.copy()
    if pre_drop is not None:
        cand = cand & ~pre_drop.fillna(False)
    cand = cand & avg_cap.notna() & avg_amt.notna()
    # liquidity screen on the remaining securities (buffered for existing constituents)
    amt_pct = avg_amt.where(cand).rank(ascending=False, pct=True)
    liq = (amt_pct <= amt_keep) | (old.fillna(False) & (amt_pct <= amt_keep_old))
    surv = cand & liq.fillna(False)
    rank = _rank_desc(avg_cap.where(surv))
    # buffered selection: walk down the cap ranking, a new name needs rank <= buf_in, an old name rank <= buf_keep
    order = rank.dropna().sort_values().index
    is_old = old.reindex(order).fillna(False).to_numpy()
    r = rank.reindex(order).to_numpy()
    take = np.where(is_old, r <= buf_keep, r <= buf_in)
    sel = list(np.array(order)[take][:n_target])
    if len(sel) < n_target:                       # buffer left the list short -> fill with the next best names
        extra = [c for c in order if c not in set(sel)]
        sel = sel + extra[: n_target - len(sel)]
    sel_set = set(sel)
    old_list = set(old[old.fillna(False)].index)
    adds = [c for c in sel if c not in old_list]
    drops = [c for c in old_list if c not in sel_set]
    # mandatory drops (left the sample space / pre-screened out) always go first
    def _rk(c):        # cap rank of a name; screened-out names rank worst
        v = rank.get(c, np.nan)
        return float(v) if np.isfinite(v) else 1e9
    forced = [c for c in drops if not bool(cand.get(c, False))]
    optional = sorted([c for c in drops if c not in set(forced)], key=_rk, reverse=True)
    forced = sorted(forced, key=_rk, reverse=True)
    adds.sort(key=_rk)
    k = min(max_replace, len(adds), len(forced) + len(optional))
    drops_final = (forced + optional)[:k]
    adds_final = adds[:k]
    new_list = (old_list - set(drops_final)) | set(adds_final)
    return {"adds": adds_final, "drops": drops_final, "new_list": sorted(new_list), "rank": rank,
            "n_ideal_adds": len(adds), "n_forced": len(forced), "capped": len(adds) > max_replace}


def predict_event(panel: Panel, ev: dict, asof: pd.Timestamp, *, window=252, min_obs=40,
                  csi300_mode="predicted", star_from="2020-12", cap300=30, cap500=50) -> dict:
    """One review prediction, computable from data through `asof` only.

    csi300_mode: 'predicted' (replicate the simultaneous CSI300 review), 'pre' (pre-review CSI300 list -> lower
    bound on the removal set) or 'actual' (post-review CSI300 list; look-ahead, upper bound / diagnostic only).
    """
    cut = ev["cutoff"]
    star_ok = ev["label"] >= star_from
    # eligibility is judged at the CUT-OFF (that is the review's reference date). A later prediction date only adds
    # information: names that became ST / entered delisting between the cut-off and `asof` are removed as well.
    space = sample_space(panel, cut, star_ok)
    if asof > cut:
        space = space & (panel["is_st"].loc[asof] != 1) & (panel["is_delisting"].loc[asof] != 1)
    cap_df = panel["total_market_cap"]
    amt_df = panel["amount"]
    avg_cap, n_cap = window_mean(cap_df, cut, window)
    avg_amt, _ = window_mean(amt_df, cut, window)
    space = space & (n_cap >= min_obs)
    m300 = panel["member_csi300"] > 0
    m500 = panel["member_csi500"] > 0
    old300 = m300.loc[asof]
    old500 = m500.loc[asof]
    # --- CSI300 review (needed because its drops are the main source of CSI500 adds)
    r300 = review_index(avg_cap, avg_amt, space, old300, n_target=300, amt_keep=0.5, amt_keep_old=0.5,
                        buf_in=240, buf_keep=360, max_replace=cap300)
    if csi300_mode == "predicted":
        post300 = pd.Series(False, index=pd.Index(panel.codes)); post300[r300["new_list"]] = True
    elif csi300_mode == "pre":
        post300 = old300.copy()
    else:
        post300 = m300.loc[ev["eff_panel"]].copy()          # look-ahead: diagnostic upper bound only
    # --- CSI500 review
    top300_cap = _rank_desc(avg_cap.where(space)) <= 300
    pre_drop = post300.fillna(False) | top300_cap.fillna(False)
    r500 = review_index(avg_cap, avg_amt, space, old500, n_target=500, amt_keep=0.8, amt_keep_old=0.9,
                        buf_in=400, buf_keep=600, max_replace=cap500, pre_drop=pre_drop)
    return {"event": ev["label"], "asof": asof, "csi300_mode": csi300_mode, "csi500": r500, "csi300": r300,
            "avg_cap": avg_cap, "space": space}


# ----------------------------------------------------------------------------- actual outcome / scoring


def actual_changes(mem: pd.DataFrame, dates: pd.DatetimeIndex, eff: pd.Timestamp) -> dict:
    """Constituents added / dropped across the effective date (the panel's own membership flip)."""
    prev = _shift_td(dates, eff, -1)
    a = mem.loc[prev].fillna(False).astype(bool)
    b = mem.loc[eff].fillna(False).astype(bool)
    return {"adds": sorted(b[b & ~a].index), "drops": sorted(a[a & ~b].index)}


def prf(pred: list, actual: list) -> dict:
    p, a = set(pred), set(actual)
    hit = len(p & a)
    return {"n_pred": len(p), "n_actual": len(a), "hit": hit,
            "precision": hit / len(p) if p else float("nan"),
            "recall": hit / len(a) if a else float("nan")}
