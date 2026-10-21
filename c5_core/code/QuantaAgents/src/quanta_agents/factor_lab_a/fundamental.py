"""A18: point-in-time fundamental fields for the A-layer panels.

Raw tables (one parquet per Tushare-format api, produced by scripts/fd_consolidate.py) are turned into daily
``date x code`` float32 matrices on the *master grid* (union of the four panels' dates/codes) and stored under
``/Users/leslie/Downloads/中证500量化60min/c5_core/data/A_Layer_Research/fundamental/grid/<field>.parquet``; ``assemble`` then reindexes them onto a panel and adds the
panel-specific valuation ratios (numerator / the panel's own market cap).

Point-in-time rule (pre-registered 2026-09-21): a record is usable on signal date t only if its availability date
(``f_ann_date`` when present, else ``ann_date``) is strictly earlier than t — the live chain runs at 15:10 and picks up
evening announcements the next day, so the backtest uses the same lag.  Same-day revisions: the later ``update_flag``
wins; earlier revisions only take effect from their own announcement date.
"""
from __future__ import annotations

import json
import os
import time

import numpy as np
import pandas as pd

RAW = "/Users/leslie/Downloads/中证500量化60min/c5_core/data/A_Layer_Research/fundamental/raw"
GRID = "/Users/leslie/Downloads/中证500量化60min/c5_core/data/A_Layer_Research/fundamental/grid"
PANELS = {
    "research": "/Users/leslie/Downloads/中证500量化60min/c5_core/data/A_Layer_Research/panel",
    "oos": "/Users/leslie/Downloads/中证500量化60min/c5_core/data/A_Layer_OOS/panel",
    "p2026": "/Users/leslie/Downloads/中证500量化60min/c5_core/data/A_Layer_OOS/p2026/panel",
    "live": "/Users/leslie/Downloads/中证500量化60min/c5_core/data/A_Layer_Live/panel",
}
DAY = np.timedelta64(1, "D")

# --- field registry (master grid) -------------------------------------------------------------------------------
STATEMENT_FIELDS = {
    "fd_np_ttm": "net profit attributable to parent, trailing 12m (CNY), as of latest report",
    "fd_dedt_ttm": "net profit excl. non-recurring items, trailing 12m (CNY)",
    "fd_rev_ttm": "total revenue, trailing 12m (CNY)",
    "fd_ocf_ttm": "operating cash flow, trailing 12m (CNY)",
    "fd_equity": "shareholders' equity excl. minority (CNY), latest balance sheet",
    "fd_assets": "total assets (CNY), latest balance sheet",
    "fd_gm_ttm": "gross margin, trailing 12m (revenue - operating cost) / revenue",
    "fd_np_q_yoy": "latest single-quarter net profit yoy (vs |same quarter last year|)",
    "fd_rev_q_yoy": "latest single-quarter revenue yoy",
    "fd_np_yoy_accel": "change of single-quarter net profit yoy vs the previous quarter",
    "fd_sue": "standardised unexpected earnings: (Q - Q[-4]) / std of the last 8 seasonal differences",
    "fd_roe_ttm": "net profit ttm / average equity (latest and one year earlier)",
    "fd_d_roe_ttm": "roe_ttm minus roe_ttm one year earlier",
    "fd_accrual_cf": "(net profit ttm - operating cash flow ttm) / total assets",
    "fd_d_noa": "change in net operating assets over one year / average total assets",
    "fd_debt_ib": "interest-bearing debt / total assets",
    "fd_goodwill_eq": "goodwill / equity",
    "fd_cur_ratio": "current assets / current liabilities",
    "fd_d_cur_ratio": "current ratio minus one year earlier",
    "fd_extra_ratio": "non-recurring items ttm / |net profit ttm|",
    "fd_recv_minus_rev": "yoy growth of (accounts + notes receivable) minus revenue ttm growth",
    "fd_rpt_age": "calendar days since the latest income statement became available",
}
EVENT_FIELDS = {
    "fd_fc_pchg": "latest earnings pre-announcement: midpoint of net profit change range (%)",
    "fd_fc_neg": "latest pre-announcement is a loss / decline type (1/0)",
    "fd_fc_days": "calendar days since the latest pre-announcement",
    "fd_ex_yoy_np": "latest earnings express report: net profit yoy (%)",
    "fd_ex_days": "calendar days since the latest express report",
    "fd_div_ps_ttm": "cash dividend per share (pre-tax) with ex-date in the past 365 days",
    "fd_div_pre30": "dividend proposal announced in the past 30 days (1/0)",
    "fd_hsell_60": "net insider / major-holder reduction in the past 60 days (% of total shares, DE minus IN)",
    "fd_unlock_30": "announced share unlocks with float date in the next 30 days (% of total shares)",
    "fd_unlock_90": "announced share unlocks with float date in the next 90 days (% of total shares)",
    "fd_pledge": "pledged shares / total shares (%), latest weekly statistic (3-day lag)",
    "fd_hnum_chg": "shareholder count change vs the previous disclosed count",
    "fd_rep_90": "buyback announcement in the past 90 days (1/0)",
    "fd_top10f_conc": "top-10 float holders' combined float ratio (%), latest",
    "fd_d_top10f": "change of top-10 float concentration vs the previous period",
    "fd_audit_nonstd": "latest annual audit opinion is non-standard (1/0)",
}
ANALYST_FIELDS = {
    "fd_rc_np_fy1": "median analyst net-profit forecast for the current fiscal year, reports of the past 90 days (CNY)",
    "fd_rc_eps_fy1": "median analyst EPS forecast, current fiscal year, past 90 days",
    "fd_rc_n90": "number of analyst reports for the current fiscal year in the past 90 days",
    "fd_rc_disp": "dispersion of net-profit forecasts: std / |median|",
    "fd_rc_tp": "median target price (max_price, else min_price), past 90 days",
    "fd_rc_rating": "mean numeric rating (5 buy .. 1 sell), past 90 days",
    "fd_rc_up90": "rating upgrades by the same broker in the past 90 days",
    "fd_rc_down90": "rating downgrades by the same broker in the past 90 days",
    "fd_rc_epsup90": "EPS forecast raised by the same broker for the same fiscal year, past 90 days",
    "fd_rc_epsdown90": "EPS forecast cut by the same broker for the same fiscal year, past 90 days",
}
DAILY_FIELDS = {
    "fd_free_share": "free-float shares (10k shares, vendor daily_basic)",
    "fd_turnover_f": "turnover rate on free-float shares (%)",
    "fd_v_pe_ttm": "vendor pe_ttm (validation only)",
    "fd_v_pb": "vendor pb (validation only)",
    "fd_v_dv_ttm": "vendor dividend yield ttm (%) (validation only)",
}
PANEL_RATIO_FIELDS = {
    "fd_ep_ttm": "net profit ttm / total market cap",
    "fd_ep_dedt": "net profit excl. non-recurring ttm / total market cap",
    "fd_bp": "equity / total market cap",
    "fd_sp_ttm": "revenue ttm / total market cap",
    "fd_dp_ttm": "cash dividend ttm / price",
    "fd_ocf_ep": "operating cash flow ttm / total market cap",
    "fd_est_ep_fy1": "analyst net-profit forecast / total market cap",
    "fd_est_g": "analyst net-profit forecast / net profit ttm - 1",
    "fd_tp_ret": "median target price / price - 1",
    "fd_free_cap": "free-float market cap (CNY)",
    "fd_free_ratio": "free-float shares / total shares",
}
GRID_FIELDS = {**STATEMENT_FIELDS, **EVENT_FIELDS, **ANALYST_FIELDS, **DAILY_FIELDS}
FUND_FIELDS = tuple(GRID_FIELDS) + tuple(PANEL_RATIO_FIELDS)
FUND_FIELD_DOC = {**GRID_FIELDS, **PANEL_RATIO_FIELDS}

NEG_FORECAST = {"预亏", "首亏", "续亏", "预减", "略减"}   # vocabulary seen: 预增 略增 预减 首亏 略减 续亏 扭亏 续盈 不确定
RATING_NUM = {"买入": 5, "买进": 5, "强烈推荐": 5, "强推": 5, "确信买入": 5, "Buy": 5, "BUY": 5,
              "推荐": 4, "增持": 4, "谨慎推荐": 4, "谨慎增持": 4, "审慎增持": 4, "审慎推荐": 4, "谨慎买入": 4, "优于大市": 4, "跑赢行业": 4,
              "强于大市": 4, "Overweight": 4, "OVERWEIGHT": 4,
              "中性": 3, "持有": 3, "观望": 3, "同步大市": 3, "区间操作": 3, "Neutral": 3, "Hold": 3,
              "减持": 2, "弱于大市": 2, "Underweight": 2, "回避": 1, "卖出": 1, "Sell": 1,
              "买入-A": 5, "买入-B": 5, "增持-A": 4, "增持-B": 4, "Outperform": 4, "OUTPERFORM": 4, "HOLD": 3, "Equal weight": 3, "Equal-weight": 3,
              "Reduce": 2, "Underperform": 2, "UNDERPERFORM": 2, "SELL": 1}


def log(msg: str) -> None:
    print(time.strftime("%H:%M:%S"), msg, flush=True)


# --- grid --------------------------------------------------------------------------------------------------------
def master_grid(panels: dict[str, str] = PANELS) -> tuple[pd.DatetimeIndex, list[str]]:
    dates, codes = set(), set()
    for p in panels.values():
        with open(os.path.join(p, "meta.json"), encoding="utf-8") as fh:
            m = json.load(fh)
        dates |= set(m["dates"]); codes |= set(m["codes"])
    return pd.DatetimeIndex(sorted(dates)), sorted(codes)


def save_grid_field(name: str, arr: np.ndarray, dates: pd.DatetimeIndex, codes: list[str]) -> float:
    os.makedirs(GRID, exist_ok=True)
    df = pd.DataFrame(arr.astype(np.float32), index=dates, columns=codes)
    df.to_parquet(os.path.join(GRID, f"{name}.parquet"))
    return float(np.isfinite(arr).mean())


def _dt(s: pd.Series) -> pd.Series:
    return pd.to_datetime(s, format="%Y%m%d", errors="coerce")


def load_raw(api: str, columns: list[str] | None = None) -> pd.DataFrame:
    return pd.read_parquet(os.path.join(RAW, f"{api}.parquet"), columns=columns)


# --- generic daily expansion --------------------------------------------------------------------------------------
def expand_events(dates: pd.DatetimeIndex, codes: list[str], groups, n_series: int, lag_days: int = 0):
    """groups: iterable of (code, event_dates[np.datetime64[D] sorted], values[n_events, n_series]).
    Value at date t = last event with event_date + lag_days < t.  Returns array [n_series, n_dates, n_codes]."""
    out = np.full((n_series, len(dates), len(codes)), np.nan, np.float32)
    cidx = {c: i for i, c in enumerate(codes)}
    d64 = dates.values.astype("datetime64[D]")
    for code, ev, vals in groups:
        j = cidx.get(code)
        if j is None or len(ev) == 0:
            continue
        pos = np.searchsorted(ev + lag_days * DAY, d64, side="left") - 1   # last event strictly before t
        ok = pos >= 0
        sel = vals[np.maximum(pos, 0)]                                         # [n_dates, n_series]
        sel[~ok] = np.nan
        out[:, :, j] = sel.T
    return out


def window_sum(dates: pd.DatetimeIndex, codes: list[str], groups, window_days: int, n_series: int):
    """Sum of event values over (t - window_days, t) with event_date < t.  groups: (code, ev_dates, values)."""
    out = np.zeros((n_series, len(dates), len(codes)), np.float32)
    cidx = {c: i for i, c in enumerate(codes)}
    d64 = dates.values.astype("datetime64[D]")
    for code, ev, vals in groups:
        j = cidx.get(code)
        if j is None or len(ev) == 0:
            continue
        cs = np.vstack([np.zeros((1, n_series)), np.cumsum(vals, axis=0)])
        hi = np.searchsorted(ev, d64, side="left")
        lo = np.searchsorted(ev, d64 - window_days * DAY, side="right")
        out[:, :, j] = (cs[hi] - cs[lo]).T
    return out


def window_future_sum(dates: pd.DatetimeIndex, codes: list[str], groups, horizon_days: int):
    """Sum over records announced before t whose (future) event date lies in [t, t + horizon).  groups: (code, ann, when, value)."""
    out = np.zeros((len(dates), len(codes)), np.float32)
    cidx = {c: i for i, c in enumerate(codes)}
    d64 = dates.values.astype("datetime64[D]")
    for code, ann, when, val in groups:
        j = cidx.get(code)
        if j is None or len(ann) == 0:
            continue
        col = np.zeros(len(d64), np.float64)
        for a, w, v in zip(ann, when, val):
            if not np.isfinite(v):
                continue
            start = max(np.searchsorted(d64, a, side="right"), np.searchsorted(d64, w - horizon_days * DAY, side="right"))
            end = np.searchsorted(d64, w, side="right")   # dates t <= w
            if end > start:
                col[start:end] += v
        out[:, j] = col
    return out


# --- statements --------------------------------------------------------------------------------------------------
def _asof_matrix(g: pd.DataFrame, item: str, events: np.ndarray, qcols: pd.DatetimeIndex) -> np.ndarray:
    """events x quarter-columns matrix of `item`, as of each event date (ffill along events)."""
    piv = g.pivot_table(index="avail", columns="end_date", values=item, aggfunc="last")
    piv = piv.reindex(index=pd.DatetimeIndex(events), columns=qcols)
    return piv.ffill(axis=0).to_numpy(np.float64)


def _take(A: np.ndarray, k: np.ndarray) -> np.ndarray:
    n = A.shape[0]
    kk = np.clip(k, 0, A.shape[1] - 1)
    v = A[np.arange(n), kk]
    return np.where((k >= 0) & (k < A.shape[1]), v, np.nan)


def _ytd_ops(qcols: pd.DatetimeIndex):
    quarter = np.asarray(qcols.month // 3)          # 1..4

    def q_of(k):
        return np.where(k >= 0, quarter[np.clip(k, 0, len(quarter) - 1)], 0)

    def single_q(A, k):
        return np.where(q_of(k) > 1, _take(A, k) - _take(A, k - 1), _take(A, k))

    def ttm(A, k):
        q = q_of(k)
        prev_q4 = k - q
        return np.where(q == 4, _take(A, k), _take(A, k) + _take(A, prev_q4) - _take(A, k - 4))

    return single_q, ttm


def _statement_groups(table: pd.DataFrame, items: list[str], derive, min_end="2010-03-31"):
    """Yield (code, event_dates, values[n_events, n_out]) per code.  `derive(mats: dict item->asof matrix, k_latest, ops, qcols, events) -> list of arrays."""
    table = table.dropna(subset=["avail", "end_date"])
    table = table[table["end_date"] >= pd.Timestamp(min_end)]
    table = table.sort_values(["code", "avail", "update_flag"])
    qcols = pd.date_range(min_end, table["end_date"].max(), freq="QE-DEC")
    ops = _ytd_ops(qcols)
    for code, g in table.groupby("code", sort=False):
        events = np.sort(g["avail"].unique())
        mats = {it: _asof_matrix(g, it, events, qcols) for it in items}
        first = next(iter(mats.values()))
        k_latest = np.where(np.isfinite(first), np.arange(first.shape[1]), -1).max(axis=1)
        vals = np.column_stack(derive(mats, k_latest, ops, qcols, events))
        yield code, events.astype("datetime64[D]"), vals.astype(np.float32)


AUX_FIELDS = ("fdx_np_ttm_4", "fdx_rev_ttm_g", "fdx_equity_4", "fdx_recv_g", "fdx_extra_ttm")   # helpers for the ratio step, not model inputs


def _load_stmt(api: str, cols: list[str], avail_cols=("f_ann_date", "ann_date")):
    df = load_raw(api, ["code", "end_date", "update_flag"] + list(avail_cols) + cols)
    av = None
    for c in avail_cols:
        d = _dt(df[c]); av = d if av is None else av.fillna(d)
    df["avail"] = av; df["end_date"] = _dt(df["end_date"])
    df["update_flag"] = pd.to_numeric(df["update_flag"], errors="coerce").fillna(0)
    return df


def _age_field(dates, codes, groups):
    age = np.full((len(dates), len(codes)), np.nan, np.float32); cidx = {c: i for i, c in enumerate(codes)}
    d64 = dates.values.astype("datetime64[D]")
    for code, ev, _ in groups:
        j = cidx.get(code)
        if j is None or len(ev) == 0:
            continue
        pos = np.searchsorted(ev, d64, side="left") - 1
        a = (d64 - ev[np.maximum(pos, 0)]).astype("timedelta64[D]").astype(np.float32); a[pos < 0] = np.nan; age[:, j] = a
    return age


def build_income(dates, codes):
    inc = _load_stmt("income", ["n_income_attr_p", "total_revenue", "revenue", "oper_cost"])
    inc["rev"] = inc["total_revenue"].fillna(inc["revenue"])

    def d_income(m, k, ops, qcols, events):
        sq, ttm = ops
        np_ttm = ttm(m["n_income_attr_p"], k); rev_ttm = ttm(m["rev"], k); cogs_ttm = ttm(m["oper_cost"], k)
        npq = sq(m["n_income_attr_p"], k); npq_1 = sq(m["n_income_attr_p"], k - 1); npq_4 = sq(m["n_income_attr_p"], k - 4); npq_5 = sq(m["n_income_attr_p"], k - 5)
        revq = sq(m["rev"], k); revq_4 = sq(m["rev"], k - 4)
        np_q_yoy = npq / np.abs(npq_4) - 1
        np_q_yoy_prev = npq_1 / np.abs(npq_5) - 1
        diffs = np.column_stack([sq(m["n_income_attr_p"], k - i) - sq(m["n_income_attr_p"], k - i - 4) for i in range(8)])
        with np.errstate(invalid="ignore"):
            sd = np.nanstd(diffs[:, 1:], axis=1, ddof=1)
            cnt = np.isfinite(diffs[:, 1:]).sum(axis=1)
        sue = np.where(cnt >= 6, diffs[:, 0] / sd, np.nan)
        rev_ttm_4 = ttm(m["rev"], k - 4); np_ttm_4 = ttm(m["n_income_attr_p"], k - 4)
        return [np_ttm, rev_ttm, np.where(rev_ttm > 0, (rev_ttm - cogs_ttm) / rev_ttm, np.nan), np_q_yoy, revq / np.abs(revq_4) - 1,
                np_q_yoy - np_q_yoy_prev, sue, np_ttm_4, rev_ttm / np.abs(rev_ttm_4) - 1]

    names = ["fd_np_ttm", "fd_rev_ttm", "fd_gm_ttm", "fd_np_q_yoy", "fd_rev_q_yoy", "fd_np_yoy_accel", "fd_sue", "fdx_np_ttm_4", "fdx_rev_ttm_g"]
    log("income: deriving per code")
    groups = list(_statement_groups(inc, ["n_income_attr_p", "rev", "oper_cost"], d_income))
    out = dict(zip(names, expand_events(dates, codes, groups, len(names))))
    out["fd_rpt_age"] = _age_field(dates, codes, groups)
    return out


def build_balancesheet(dates, codes):
    items = ["total_assets", "total_liab", "total_hldr_eqy_exc_min_int", "goodwill", "money_cap", "st_borr", "lt_borr", "bond_payable",
             "non_cur_liab_due_1y", "accounts_receiv", "notes_receiv", "total_cur_assets", "total_cur_liab"]
    bs = _load_stmt("balancesheet", items)
    for c in ["goodwill", "money_cap", "st_borr", "lt_borr", "bond_payable", "non_cur_liab_due_1y", "accounts_receiv", "notes_receiv"]:
        bs[c] = bs[c].fillna(0.0)

    def d_bs(m, k, ops, qcols, events):
        A = lambda it, kk: _take(m[it], kk)
        assets, assets4 = A("total_assets", k), A("total_assets", k - 4)
        eq, eq4 = A("total_hldr_eqy_exc_min_int", k), A("total_hldr_eqy_exc_min_int", k - 4)
        ib = A("st_borr", k) + A("lt_borr", k) + A("bond_payable", k) + A("non_cur_liab_due_1y", k)
        ib4 = A("st_borr", k - 4) + A("lt_borr", k - 4) + A("bond_payable", k - 4) + A("non_cur_liab_due_1y", k - 4)
        noa = (assets - A("money_cap", k)) - (A("total_liab", k) - ib)
        noa4 = (assets4 - A("money_cap", k - 4)) - (A("total_liab", k - 4) - ib4)
        recv, recv4 = A("accounts_receiv", k) + A("notes_receiv", k), A("accounts_receiv", k - 4) + A("notes_receiv", k - 4)
        cr, cr4 = A("total_cur_assets", k) / A("total_cur_liab", k), A("total_cur_assets", k - 4) / A("total_cur_liab", k - 4)
        return [eq, assets, (noa - noa4) / (0.5 * (assets + assets4)), ib / assets, A("goodwill", k) / eq, cr, cr - cr4,
                np.where(recv4 > 0, recv / recv4 - 1, np.nan), eq4]

    names = ["fd_equity", "fd_assets", "fd_d_noa", "fd_debt_ib", "fd_goodwill_eq", "fd_cur_ratio", "fd_d_cur_ratio", "fdx_recv_g", "fdx_equity_4"]
    log("balancesheet: deriving per code")
    return dict(zip(names, expand_events(dates, codes, list(_statement_groups(bs, items, d_bs)), len(names))))


def build_cashflow(dates, codes):
    cf = _load_stmt("cashflow", ["n_cashflow_act"])
    log("cashflow: deriving per code")
    arr = expand_events(dates, codes, list(_statement_groups(cf, ["n_cashflow_act"], lambda m, k, ops, q, e: [ops[1](m["n_cashflow_act"], k)])), 1)
    return {"fd_ocf_ttm": arr[0]}


def build_fina_indicator(dates, codes):
    fi = _load_stmt("fina_indicator", ["profit_dedt", "extra_item"], avail_cols=("ann_date",))
    log("fina_indicator: deriving per code")
    arr = expand_events(dates, codes, list(_statement_groups(fi, ["profit_dedt", "extra_item"], lambda m, k, ops, q, e: [ops[1](m["profit_dedt"], k), ops[1](m["extra_item"], k)])), 2)
    return {"fd_dedt_ttm": arr[0], "fdx_extra_ttm": arr[1]}


def load_grid_field(name: str) -> np.ndarray:
    return pd.read_parquet(os.path.join(GRID, f"{name}.parquet")).to_numpy(np.float32)


def build_ratios(dates, codes):
    """Cross-table statement ratios from the saved grid fields (run after income / balancesheet / cashflow / fina_indicator)."""
    g = {k: load_grid_field(k) for k in ["fd_np_ttm", "fdx_np_ttm_4", "fd_equity", "fdx_equity_4", "fd_ocf_ttm", "fd_assets", "fdx_extra_ttm", "fdx_recv_g", "fdx_rev_ttm_g"]}
    out = {}
    with np.errstate(invalid="ignore", divide="ignore"):
        avg_eq = 0.5 * (g["fd_equity"] + g["fdx_equity_4"])
        out["fd_roe_ttm"] = np.where(avg_eq > 0, g["fd_np_ttm"] / avg_eq, np.nan)
        out["fd_d_roe_ttm"] = out["fd_roe_ttm"] - np.where(g["fdx_equity_4"] > 0, g["fdx_np_ttm_4"] / g["fdx_equity_4"], np.nan)
        out["fd_accrual_cf"] = (g["fd_np_ttm"] - g["fd_ocf_ttm"]) / g["fd_assets"]
        out["fd_extra_ratio"] = g["fdx_extra_ttm"] / np.abs(g["fd_np_ttm"])
        out["fd_recv_minus_rev"] = g["fdx_recv_g"] - g["fdx_rev_ttm_g"]
    return out


def build_statements(dates, codes):
    out = {}
    for fn in (build_income, build_balancesheet, build_cashflow, build_fina_indicator):
        part = fn(dates, codes); out.update(part)
        for k, v in part.items():
            save_grid_field(k, v, dates, codes)
    out.update(build_ratios(dates, codes))
    return out


# --- events ------------------------------------------------------------------------------------------------------
def build_events(dates, codes):
    out = {}
    cidx = {c: i for i, c in enumerate(codes)}

    def latest_groups(df, avail_col, value_cols):
        df = df.dropna(subset=[avail_col]).sort_values(["code", avail_col])
        for code, g in df.groupby("code", sort=False):
            g = g.drop_duplicates(subset=[avail_col], keep="last")
            yield code, g[avail_col].values.astype("datetime64[D]"), g[value_cols].to_numpy(np.float32)

    def age_field(df, avail_col):
        arr = np.full((len(dates), len(codes)), np.nan, np.float32); d64 = dates.values.astype("datetime64[D]")
        for code, g in df.dropna(subset=[avail_col]).groupby("code", sort=False):
            j = cidx.get(code)
            if j is None:
                continue
            ev = np.sort(g[avail_col].unique()).astype("datetime64[D]")
            pos = np.searchsorted(ev, d64, side="left") - 1
            a = (d64 - ev[np.maximum(pos, 0)]).astype("timedelta64[D]").astype(np.float32); a[pos < 0] = np.nan; arr[:, j] = a
        return arr

    # forecast
    fc = load_raw("forecast", ["code", "ann_date", "end_date", "type", "p_change_min", "p_change_max"])
    fc["avail"] = _dt(fc["ann_date"]); fc["mid"] = fc[["p_change_min", "p_change_max"]].mean(axis=1)
    fc["neg"] = fc["type"].isin(NEG_FORECAST).astype(np.float32)
    a = expand_events(dates, codes, latest_groups(fc, "avail", ["mid", "neg"]), 2)
    out["fd_fc_pchg"], out["fd_fc_neg"] = a[0], a[1]; out["fd_fc_days"] = age_field(fc, "avail"); del fc
    log("forecast done")

    # express
    ex = load_raw("express", ["code", "ann_date", "yoy_net_profit"]); ex["avail"] = _dt(ex["ann_date"])
    out["fd_ex_yoy_np"] = expand_events(dates, codes, latest_groups(ex, "avail", ["yoy_net_profit"]), 1)[0]; out["fd_ex_days"] = age_field(ex, "avail"); del ex

    # dividend: implemented cash dividends, ex-date in the past 365 days; proposals in the past 30 days
    dv = load_raw("dividend", ["code", "ann_date", "div_proc", "cash_div_tax", "ex_date", "imp_ann_date"])
    imp = dv[(dv["div_proc"] == "实施") & dv["ex_date"].notna() & (dv["cash_div_tax"] > 0)].copy()
    imp["ex"] = _dt(imp["ex_date"]); imp["imp"] = _dt(imp["imp_ann_date"]).fillna(imp["ex"])
    imp = imp.dropna(subset=["ex"]); imp["ev"] = imp[["ex", "imp"]].max(axis=1)      # usable once both announced and ex'd
    imp = imp.drop_duplicates(subset=["code", "ex", "cash_div_tax"]).sort_values(["code", "ev"])
    out["fd_div_ps_ttm"] = window_sum(dates, codes, ((c, g["ev"].values.astype("datetime64[D]"), g[["cash_div_tax"]].to_numpy(np.float32)) for c, g in imp.groupby("code", sort=False)), 365, 1)[0]
    pre = dv[dv["div_proc"] == "预案"].copy(); pre["avail"] = _dt(pre["ann_date"]); pre = pre.dropna(subset=["avail"]).sort_values(["code", "avail"]); pre["one"] = 1.0
    out["fd_div_pre30"] = np.minimum(window_sum(dates, codes, ((c, g["avail"].values.astype("datetime64[D]"), g[["one"]].to_numpy(np.float32)) for c, g in pre.groupby("code", sort=False)), 30, 1)[0], 1)
    del dv, imp, pre; log("dividend done")

    # holder trades: net reduction ratio (% of total shares) in 60 days
    ht = load_raw("stk_holdertrade", ["code", "ann_date", "in_de", "change_ratio"]); ht["avail"] = _dt(ht["ann_date"])
    ht = ht.dropna(subset=["avail", "change_ratio"]); ht["signed"] = np.where(ht["in_de"].astype(str).str.upper().str.startswith("DE"), 1.0, -1.0) * ht["change_ratio"].abs()
    ht = ht.sort_values(["code", "avail"])
    out["fd_hsell_60"] = window_sum(dates, codes, ((c, g["avail"].values.astype("datetime64[D]"), g[["signed"]].to_numpy(np.float32)) for c, g in ht.groupby("code", sort=False)), 60, 1)[0]; del ht

    # share unlocks
    sf = load_raw("share_float", ["code", "ann_date", "float_date", "float_ratio"]); sf["ann"] = _dt(sf["ann_date"]); sf["when"] = _dt(sf["float_date"])
    sf = sf.dropna(subset=["ann", "when", "float_ratio"]); sf = sf[sf["when"] >= sf["ann"]]
    sf = sf.groupby(["code", "ann", "when"], as_index=False)["float_ratio"].sum().sort_values(["code", "when"])
    for h in (30, 90):
        out[f"fd_unlock_{h}"] = window_future_sum(dates, codes, ((c, g["ann"].values.astype("datetime64[D]"), g["when"].values.astype("datetime64[D]"), g["float_ratio"].to_numpy(np.float64)) for c, g in sf.groupby("code", sort=False)), h)
    del sf; log("unlock done")

    # pledge (weekly statistic dated end_date; usable 3 days later)
    pl = load_raw("pledge_stat", ["code", "end_date", "pledge_ratio"]); pl["avail"] = _dt(pl["end_date"])
    out["fd_pledge"] = expand_events(dates, codes, latest_groups(pl, "avail", ["pledge_ratio"]), 1, lag_days=3)[0]; del pl

    # shareholder count change vs previous disclosed period
    hn = load_raw("stk_holdernumber", ["code", "ann_date", "end_date", "holder_num"]); hn["avail"] = _dt(hn["ann_date"]); hn["end"] = _dt(hn["end_date"])
    hn = hn.dropna(subset=["avail", "end", "holder_num"]).sort_values(["code", "end", "avail"]).drop_duplicates(subset=["code", "end"], keep="last")
    hn["chg"] = hn.groupby("code")["holder_num"].pct_change()
    out["fd_hnum_chg"] = expand_events(dates, codes, latest_groups(hn, "avail", ["chg"]), 1)[0]; del hn

    # buyback announcements in 90 days
    rp = load_raw("repurchase", ["code", "ann_date"]); rp["avail"] = _dt(rp["ann_date"]); rp = rp.dropna(subset=["avail"]).drop_duplicates(subset=["code", "avail"]).sort_values(["code", "avail"]); rp["one"] = 1.0
    out["fd_rep_90"] = np.minimum(window_sum(dates, codes, ((c, g["avail"].values.astype("datetime64[D]"), g[["one"]].to_numpy(np.float32)) for c, g in rp.groupby("code", sort=False)), 90, 1)[0], 1); del rp

    # top-10 float-holder concentration
    tf = load_raw("top10_floatholders", ["code", "ann_date", "end_date", "hold_float_ratio"]); tf["avail"] = _dt(tf["ann_date"]); tf["end"] = _dt(tf["end_date"])
    tf = tf.dropna(subset=["avail", "end"]).groupby(["code", "end"], as_index=False).agg(avail=("avail", "max"), conc=("hold_float_ratio", "sum"))
    tf = tf.sort_values(["code", "end"]); tf["d"] = tf.groupby("code")["conc"].diff()
    a = expand_events(dates, codes, latest_groups(tf, "avail", ["conc", "d"]), 2); out["fd_top10f_conc"], out["fd_d_top10f"] = a[0], a[1]; del tf

    # audit opinion
    au = load_raw("fina_audit", ["code", "ann_date", "audit_result"]); au["avail"] = _dt(au["ann_date"])
    au["nonstd"] = (~au["audit_result"].fillna("").str.contains("标准无保留")).astype(np.float32)
    out["fd_audit_nonstd"] = expand_events(dates, codes, latest_groups(au, "avail", ["nonstd"]), 1)[0]; del au
    # no record = no event: fill neutral values so expressions keep >= 80% coverage; ages without any record are set large
    for k in ("fd_fc_pchg", "fd_fc_neg", "fd_ex_yoy_np", "fd_pledge"):
        out[k] = np.nan_to_num(out[k], nan=0.0)
    for k in ("fd_fc_days", "fd_ex_days"):
        out[k] = np.nan_to_num(out[k], nan=9999.0)
    log("events done")
    return out


# --- analysts ----------------------------------------------------------------------------------------------------
def build_analyst(dates, codes, window_days: int = 90):
    rc = load_raw("report_rc", ["code", "report_date", "org_name", "quarter", "np", "eps", "max_price", "min_price", "rating"])
    rc["tp"] = rc["max_price"].fillna(rc["min_price"])   # report_rc.tp is forecast total profit; target price lives in max_price/min_price
    rc["avail"] = _dt(rc["report_date"]); rc = rc.dropna(subset=["avail"])
    rc["fy"] = pd.to_numeric(rc["quarter"].astype(str).str[:4], errors="coerce")
    rc = rc[rc["quarter"].astype(str).str.endswith("Q4")].copy()
    rc["rating_num"] = rc["rating"].map(RATING_NUM)
    unknown = rc.loc[rc["rating_num"].isna() & rc["rating"].notna(), "rating"].value_counts().head(20)
    if len(unknown):
        log("unmapped ratings (ignored): " + json.dumps(unknown.to_dict(), ensure_ascii=False))
    rc = rc.sort_values(["code", "org_name", "fy", "avail"])
    g = rc.groupby(["code", "org_name", "fy"], sort=False)
    rc["d_rating"] = g["rating_num"].diff(); rc["d_eps"] = g["eps"].diff()
    rc["r_up"] = (rc["d_rating"] > 0).astype(np.float32); rc["r_dn"] = (rc["d_rating"] < 0).astype(np.float32)
    rc["e_up"] = (rc["d_eps"] > 0).astype(np.float32); rc["e_dn"] = (rc["d_eps"] < 0).astype(np.float32)
    rc = rc.sort_values(["code", "avail"])
    d64 = dates.values.astype("datetime64[D]"); years = np.asarray(dates.year)
    cidx = {c: i for i, c in enumerate(codes)}
    names = ["fd_rc_np_fy1", "fd_rc_eps_fy1", "fd_rc_n90", "fd_rc_disp", "fd_rc_tp", "fd_rc_rating", "fd_rc_up90", "fd_rc_down90", "fd_rc_epsup90", "fd_rc_epsdown90"]
    out = {n: np.full((len(dates), len(codes)), np.nan, np.float32) for n in names}
    for n in names[6:]:
        out[n][:] = 0
    out["fd_rc_n90"][:] = 0          # no report in the window = 0, not missing
    t0 = time.time(); done = 0
    for code, gc in rc.groupby("code", sort=False):
        j = cidx.get(code)
        if j is None:
            continue
        for fy, gf in gc.groupby("fy", sort=False):
            rows = np.where(years == fy)[0]
            if len(rows) == 0:
                continue
            ev = gf["avail"].values.astype("datetime64[D]")
            npv = gf["np"].to_numpy(np.float64) * 1e4; eps = gf["eps"].to_numpy(np.float64); tp = gf["tp"].to_numpy(np.float64); rt = gf["rating_num"].to_numpy(np.float64)
            flags = gf[["r_up", "r_dn", "e_up", "e_dn"]].to_numpy(np.float64)
            cs = np.vstack([np.zeros((1, 4)), np.cumsum(flags, axis=0)])
            dd = d64[rows]
            hi = np.searchsorted(ev, dd, side="left"); lo = np.searchsorted(ev, dd - window_days * DAY, side="right")
            wsum = cs[hi] - cs[lo]
            for k, n in enumerate(names[6:]):
                out[n][rows, j] = wsum[:, k]
            # medians etc. only where the window changes, then broadcast to the rows in between
            change = np.r_[True, (hi[1:] != hi[:-1]) | (lo[1:] != lo[:-1])]
            cp = np.where(change)[0]
            vals_cp = np.full((len(cp), 6), np.nan)
            for ci, i in enumerate(cp):
                l, h = lo[i], hi[i]
                if h <= l:
                    continue
                s_np, s_eps, s_tp, s_rt = npv[l:h], eps[l:h], tp[l:h], rt[l:h]
                with np.errstate(invalid="ignore", divide="ignore"):
                    f_np = np.isfinite(s_np)
                    med = np.nanmedian(s_np) if f_np.any() else np.nan
                    disp = np.nanstd(s_np) / abs(med) if f_np.sum() >= 3 and med else np.nan
                    vals_cp[ci] = (med, np.nanmedian(s_eps) if np.isfinite(s_eps).any() else np.nan, float(h - l), disp,
                                   np.nanmedian(s_tp) if np.isfinite(s_tp).any() else np.nan, np.nanmean(s_rt) if np.isfinite(s_rt).any() else np.nan)
            vals = vals_cp[np.searchsorted(cp, np.arange(len(rows)), side="right") - 1]
            vals[:, 2] = np.nan_to_num(vals[:, 2])          # report count: an empty window is 0, not missing
            for k, n in enumerate(names[:6]):
                out[n][rows, j] = vals[:, k]
        done += 1
        if done % 500 == 0:
            log(f"analyst {done} codes ({time.time() - t0:.0f}s)")
    return out


# --- vendor daily basics ------------------------------------------------------------------------------------------
def build_daily_basic(dates, codes):
    db = load_raw("daily_basic", ["code", "trade_date", "free_share", "turnover_rate_f", "pe_ttm", "pb", "dv_ttm"])
    db["date"] = _dt(db["trade_date"]); db = db[db["date"].isin(dates) & db["code"].isin(set(codes))]
    out = {}
    for src, name in [("free_share", "fd_free_share"), ("turnover_rate_f", "fd_turnover_f"), ("pe_ttm", "fd_v_pe_ttm"), ("pb", "fd_v_pb"), ("dv_ttm", "fd_v_dv_ttm")]:
        w = db.pivot(index="date", columns="code", values=src).reindex(index=dates, columns=codes)
        out[name] = w.to_numpy(np.float32)
    return out


# --- assemble onto panels -----------------------------------------------------------------------------------------
def _panel_meta(panel_dir):
    with open(os.path.join(panel_dir, "meta.json"), encoding="utf-8") as fh:
        meta = json.load(fh)
    return meta, pd.DatetimeIndex(meta["dates"]), list(meta["codes"])


def _read_panel(panel_dir, name, dates, codes):
    w = pd.read_parquet(os.path.join(panel_dir, f"{name}.parquet")); w.index = pd.DatetimeIndex(w.index)
    return w.reindex(index=dates, columns=codes).astype(np.float64)


def _ratios(got: dict, cap: pd.DataFrame, shares: pd.DataFrame) -> dict:
    cap = cap.where(cap > 0); shares = shares.where(shares > 0); price = cap / shares
    r = {}
    if "fd_np_ttm" in got: r["fd_ep_ttm"] = got["fd_np_ttm"] / cap
    if "fd_dedt_ttm" in got: r["fd_ep_dedt"] = got["fd_dedt_ttm"] / cap
    if "fd_equity" in got: r["fd_bp"] = got["fd_equity"] / cap
    if "fd_rev_ttm" in got: r["fd_sp_ttm"] = got["fd_rev_ttm"] / cap
    if "fd_ocf_ttm" in got: r["fd_ocf_ep"] = got["fd_ocf_ttm"] / cap
    if "fd_div_ps_ttm" in got: r["fd_dp_ttm"] = got["fd_div_ps_ttm"] / price
    if "fd_rc_np_fy1" in got:
        r["fd_est_ep_fy1"] = got["fd_rc_np_fy1"] / cap
        if "fd_np_ttm" in got:
            r["fd_est_g"] = got["fd_rc_np_fy1"] / got["fd_np_ttm"].where(got["fd_np_ttm"] > 0) - 1
    if "fd_rc_tp" in got: r["fd_tp_ret"] = got["fd_rc_tp"] / price - 1
    if "fd_free_share" in got:
        r["fd_free_cap"] = got["fd_free_share"] * 1e4 * price
        r["fd_free_ratio"] = got["fd_free_share"] * 1e4 / shares
    return {k: v.replace([np.inf, -np.inf], np.nan).astype(np.float32) for k, v in r.items()}


NUMERATORS = ("fd_np_ttm", "fd_dedt_ttm", "fd_equity", "fd_rev_ttm", "fd_ocf_ttm", "fd_div_ps_ttm", "fd_rc_np_fy1", "fd_rc_tp", "fd_free_share")


def _grid_last_date():
    p = os.path.join(GRID, "grid.json")
    try:
        return json.load(open(p))["dates"][-1]
    except Exception:
        return None


def _write_meta(panel_dir, meta, cov):
    meta["fields"] = sorted(set(meta.get("fields", [])) | set(cov))
    meta["fundamental_fields"] = {"source": GRID, "fields": sorted(cov), "assembled": time.strftime("%Y-%m-%dT%H:%M:%S"), "cell_coverage": cov, "grid_last_date": _grid_last_date(),
                                  "pit_rule": "availability date < signal date; same-day revisions take the later update_flag"}
    tmp = os.path.join(panel_dir, "meta.json.tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(meta, fh, ensure_ascii=False)
    os.replace(tmp, os.path.join(panel_dir, "meta.json"))


def assemble_all(panel_dirs: list[str], fields: list[str] | None = None, log=log, ffill_limit: int | None = None) -> dict:
    """One pass over the grid: every field is read once and written to every panel; ratios use each panel's own cap."""
    fields = fields or list(GRID_FIELDS)
    metas = {p: _panel_meta(p) for p in panel_dirs}
    cov = {p: {} for p in panel_dirs}; kept = {p: {} for p in panel_dirs}
    for f in fields:
        path = os.path.join(GRID, f"{f}.parquet")
        if not os.path.exists(path):
            continue
        w = pd.read_parquet(path); w.index = pd.DatetimeIndex(w.index)
        for p, (meta, dates, codes) in metas.items():
            x = w.reindex(index=dates, columns=codes).astype(np.float32)
            if ffill_limit:                       # live chain: dates after the grid's last build carry the last known value (bounded staleness)
                x = x.ffill(limit=int(ffill_limit))
            x.to_parquet(os.path.join(p, f"{f}.parquet"))
            cov[p][f] = float(np.isfinite(x.to_numpy()).mean())
            if f in NUMERATORS:
                kept[p][f] = x.astype(np.float64)
        log(f"assembled {f}")
    for p, (meta, dates, codes) in metas.items():
        cap = _read_panel(p, "total_market_cap", dates, codes); shares = _read_panel(p, "total_shares", dates, codes)
        for k, v in _ratios(kept[p], cap, shares).items():
            v.to_parquet(os.path.join(p, f"{k}.parquet")); cov[p][k] = float(np.isfinite(v.to_numpy()).mean())
        _write_meta(p, meta, cov[p])
        log(f"{p}: {len(cov[p])} fields")
    return cov


def assemble(panel_dir: str, fields: list[str] | None = None) -> dict:
    return assemble_all([panel_dir], fields)[panel_dir]
