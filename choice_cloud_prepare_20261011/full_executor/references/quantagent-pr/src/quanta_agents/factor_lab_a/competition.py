"""Competition-rule evaluation layer (兴证全球杯 量化赛道).

Rules that shape the book (see docs/research/framework_review_20260913/competition_discipline_manual_v1.md):
  universe   = CSI300 U CSI500 U CSI1000 constituents, point-in-time by effective date
  CSI500 names must be > 80% of NAV (internal buffer 82%), stock position >= 90% (buffer 92%), single name <= 10% (9.5%)
  long only, no leverage, no ETF, T+1; benchmark = CSI500 price index; score = cumulative net excess after fees
Book model used here ("two buckets"): a CSI500 bucket (weight W500 of NAV) and an other-union bucket (WOTH), cash = rest.
Each bucket is a long book selected by score rank inside the bucket with hysteresis (enter top n_enter, keep while
inside top n_keep), min-hold sessions, weights either re-equalised daily ("equal") or drifting with entries funded by
exits ("drift", closer to live execution). Scores are computed after the close of t, trades at the open of t+1, the
first holding day earns label_1 = open[t+2]/open[t+1]-1; the benchmark uses the same open-to-open timing.
Cost: `cost` x turnover, where turnover = 0.5 * sum|dw| (fraction of NAV replaced). With cost = 0.002 this charges 0.2% per
replaced unit, i.e. 0.1% per side; the competition brief ("0.2% per side") corresponds to cost = 0.004 (review fix H2).
10-trading-day window statistics feed the registration card.
"""
from __future__ import annotations
import argparse
import json
import os
import time
import numpy as np
import pandas as pd

from .panel import Panel
from .evaluate import rank_ic, annual_table
from .oos import buyable_mask
from .trading import trading_mask

QLIB = r"D:/qlib_data/qlib_bin"
LIVE_INDEX = r"F:/A_Layer_Live/index_daily.parquet"
LIVE_INSTRUMENTS = r"F:/A_Layer_Live/instruments"
INDEX_CODES = {"csi300": "000300", "csi500": "000905", "csi1000": "000852"}
DEFAULT_CFG = {"w500": 0.88, "woth": 0.08, "n500": 55, "noth": 25, "keep_mult": 3.0, "min_hold": 1, "smooth": 5, "weighting": "drift", "cost": 0.002,
               # book: "concentrated" = top-n hysteresis in both buckets; "enhanced" = CSI500 bucket holds the whole bucket at cap^gamma
               # weights, excludes names whose in-bucket score percentile falls below x_out (re-admitted above x_in) and
               # overweights (x(1+tau)) names above y_in (until below y_out); the other bucket stays concentrated
               "book": "concentrated", "gamma": 1.0, "x_out": 0.2, "x_in": 0.35, "y_in": 0.9, "y_out": 0.7, "tau": 1.0,
               "cap_field": "float_market_cap",   # base weight field of the enhanced book (total_market_cap tracks the index more closely in 2026)
               "neutral_q": 0,                    # >0: score percentiles are taken inside `neutral_q` groups of `neutral_field` per bucket (kills that style tilt)
               "neutral_field": "float_market_cap",   # float_market_cap | total_market_cap | turnover20 | vol20 | mom20
               # A15 item 10: lot rounding in the backtest. lot_nav > 0 = account NAV (CNY); every day's target weights are
               # converted to whole lots at the next open's raw price (panel field raw_open), targets below half a lot are
               # dropped, trades smaller than lot_min_trade x NAV are suppressed (hold), the remainder stays in cash
               "lot_nav": 0.0, "lots": 100, "lot_min_trade": 0.001}


def _log(msg):
    print(time.strftime("%H:%M:%S"), msg, flush=True)


# ----------------------------------------------------------------------------- membership
def read_instruments(path: str) -> pd.DataFrame:
    m = pd.read_csv(path, sep="\t", header=None, names=["code", "start", "end"])
    m["code"] = m["code"].str.upper(); m["start"] = pd.to_datetime(m["start"]); m["end"] = pd.to_datetime(m["end"])
    return m


def membership_from_instruments(dates: pd.DatetimeIndex, codes: list, path: str, carry_forward=True) -> tuple[pd.DataFrame, pd.Timestamp]:
    """Point-in-time membership on a date x code grid. Segments ending on the file's last date are carried forward
    (the list stays in force until the next rebalance) when carry_forward is set."""
    m = read_instruments(path)
    last = m["end"].max()
    out = np.zeros((len(dates), len(codes)), bool); cidx = {c: i for i, c in enumerate(codes)}
    d = dates.to_numpy()
    for code, g in m.groupby("code"):
        j = cidx.get(code)
        if j is None:
            continue
        col = np.zeros(len(d), bool)
        for a, b in zip(g["start"].to_numpy(), g["end"].to_numpy()):
            bb = np.datetime64("2100-01-01") if (carry_forward and b == np.datetime64(last)) else b
            col |= (d >= a) & (d <= bb)
        out[:, j] = col
    return pd.DataFrame(out, index=dates, columns=codes), last


def membership(panel, instruments_dir: str | None = None, prefer_panel=True) -> tuple[dict, str]:
    """{'csi300','csi500','csi1000','union'} boolean frames on the panel grid + a note on the source."""
    mem = {}; note = "panel fields"
    if prefer_panel and all(panel.has(f"member_{u}") for u in INDEX_CODES):
        for u in INDEX_CODES:
            mem[u] = panel[f"member_{u}"] > 0
    else:
        inst = instruments_dir or (LIVE_INSTRUMENTS if os.path.exists(os.path.join(LIVE_INSTRUMENTS, "csi500.txt")) else os.path.join(QLIB, "instruments"))
        lasts = []
        for u in INDEX_CODES:
            mem[u], last = membership_from_instruments(panel.dates, panel.codes, os.path.join(inst, f"{u}.txt"))
            lasts.append(last)
        note = f"instruments {inst} (lists carried forward after {max(lasts).date()})"
    mem["union"] = mem["csi300"] | mem["csi500"] | mem["csi1000"]
    return mem, note


def write_membership_fields(panel_dir: str, instruments_dir: str | None = None, overwrite=False) -> dict:
    """Materialise member_csi300/500/1000 parquet fields into a panel directory (the OOS panel lacks them)."""
    panel = Panel(panel_dir)
    mem, note = membership(panel, instruments_dir, prefer_panel=False)
    written = {}
    for u in INDEX_CODES:
        p = os.path.join(panel_dir, f"member_{u}.parquet")
        if os.path.exists(p) and not overwrite:
            continue
        mem[u].astype(np.float32).to_parquet(p)
        written[u] = int(mem[u].to_numpy().sum(axis=1).mean())
    meta_p = os.path.join(panel_dir, "meta.json")
    with open(meta_p, encoding="utf-8") as fh:
        meta = json.load(fh)
    meta["membership"] = {"source": note, "written": time.strftime("%Y-%m-%dT%H:%M:%S")}
    meta["fields"] = sorted(set(meta.get("fields", [])) | {f"member_{u}" for u in INDEX_CODES})
    with open(meta_p, "w", encoding="utf-8") as fh:
        json.dump(meta, fh, ensure_ascii=False)
    return {"note": note, "written_mean_names": written}


# ----------------------------------------------------------------------------- benchmark
def _qlib_series(code: str, field: str) -> pd.Series:
    cal = pd.DatetimeIndex(pd.read_csv(os.path.join(QLIB, "calendars", "day.txt"), header=None)[0])
    arr = np.fromfile(os.path.join(QLIB, "features", code, f"{field}.day.bin"), dtype=np.float32)
    start = int(arr[0]); vals = arr[1:]
    return pd.Series(vals.astype(np.float64), index=cal[start:start + len(vals)])


def index_open(index="csi500", source: str | None = None) -> tuple[pd.Series, str]:
    """Daily open of the price index. Prefers the live parquet (extends past the qlib binary), else qlib."""
    code = INDEX_CODES[index]
    src = source or (LIVE_INDEX if os.path.exists(LIVE_INDEX) else "qlib")
    if src != "qlib" and os.path.exists(src):
        df = pd.read_parquet(src)
        df = df[df["index_code"].astype(str).str.zfill(6) == code]
        s = pd.Series(df["open"].to_numpy(np.float64), index=pd.DatetimeIndex(pd.to_datetime(df["date"]))).sort_index()
        s = s[~s.index.duplicated(keep="last")]
        return s, f"{src} ({s.index[0].date()}..{s.index[-1].date()})"
    s = _qlib_series(f"sh{code}", "open")
    return s, f"qlib {code} ({s.index[0].date()}..{s.index[-1].date()})"


def benchmark_label1(dates: pd.DatetimeIndex, index="csi500", source: str | None = None) -> tuple[np.ndarray, str]:
    """open[t+2]/open[t+1]-1 on the panel grid: the return earned on the first holding day of a position formed at t."""
    s, note = index_open(index, source)
    s = s.reindex(dates)
    return (s.shift(-2) / s.shift(-1) - 1).to_numpy(np.float64), note


# ----------------------------------------------------------------------------- lot rounding (A15 item 10)
def round_lots(w: np.ndarray, w_pre: np.ndarray, px: np.ndarray, nav_b: float, lots: int, min_trade_mv: float, spread: bool = True,
               pref: np.ndarray | None = None, pref_k: float = 0.0, group_below: bool = False, hyst: float = 0.0) -> np.ndarray:
    """Turn target weights (of a bucket with NAV nav_b) into whole-lot weights at price px (raw next-open). A change
    smaller than min_trade_mv keeps the held value (no-trade band, like the live target builder); targets below half a
    lot are dropped (cash). Unpriced names (no bar) keep their target unchanged.
    pref (score percentile in [0, 1]) with pref_k > 0 makes the rounding score-aware (A17 item 4): the lot count is
    round(target_lots + pref_k * (pref - 0.5)), so with pref_k = 1 the best names round up and the worst round down;
    the extra lots and the budget peel follow the same preference.
    group_below (A17 item 4): the names whose target is below half a lot (high-priced stocks at a small account) are not
    all dropped; single lots of them are bought - held ones first, then the most affordable - until the value of the
    group is spent, so the bucket keeps its exposure to the high-priced group instead of betting against it."""
    ok = np.isfinite(px) & (px > 0)
    adj = pref_k * (np.where(np.isfinite(pref), pref, 0.5) - 0.5) if (pref is not None and pref_k > 0) else np.zeros(len(w))
    tgt = w * nav_b; held = w_pre * nav_b
    lot_mv = np.where(ok, lots * px, np.nan)
    # hyst (A17 item 4): the no-trade band is at least `hyst` lots of the name, so an expensive stock whose target hovers
    # around x.5 lots does not flip a whole lot back and forth (the CNY band alone is only a fraction of such a lot)
    band = np.maximum(min_trade_mv, hyst * np.where(ok, lot_mv, 0.0)) if hyst > 0 else min_trade_mv
    keep = (np.abs(tgt - held) < band) & (tgt > 0) & (held > 0)
    tgt = np.where(keep, held, tgt)
    # targets below half a lot are dropped and their value spread over the kept names of the bucket (as the live
    # target builder does), so the bucket weight and the 82% / 92% checks hold after rounding
    units = np.where(ok, tgt / np.where(ok, lot_mv, 1.0), 0.0) + np.where(tgt > 0, adj, 0.0)
    below = ok & (tgt > 0) & (units < 0.5)
    kept = ok & (tgt > 0) & (units >= 0.5)
    traded = kept & ~keep                       # names inside the no-trade band are left exactly as held
    tgt = np.where(below, 0.0, tgt)
    sh = np.where(kept, np.maximum(np.round(units), 1.0) * lots, 0.0)
    sh = np.where(kept & keep, np.round(tgt / np.where(ok, lot_mv, 1.0)) * lots, sh)      # held-as-is names: exactly the held lots
    grp = np.zeros(len(w), bool)
    if group_below and below.any():
        # price bands keep the exposure band by band (a single pool would spend everything on the cheapest sub-lot names
        # and still leave the most expensive stocks at zero); inside a band the largest index weights come first, held
        # names are favoured (x1.5) to limit churn; what a band cannot spend is carried to the next band up
        v_grp = 0.0; tv = w * nav_b
        for lo_, hi_ in ((0.0, 80.0), (80.0, 160.0), (160.0, 320.0), (320.0, np.inf)):
            gb = below & (px >= lo_) & (px < hi_)
            if not gb.any():
                continue
            v_grp += float(tv[gb].sum())
            key = np.where(gb, tv + np.where(held > 0, 1e15, 0.0), -np.inf)     # held names first, then the largest index weights
            for j in np.argsort(-key):
                if not gb[j]:
                    break
                if v_grp >= (0.2 if held[j] > 0 else 0.6) * lot_mv[j]:            # hysteresis: a held lot is kept longer than a new one is bought
                    sh[j] = lots; grp[j] = True; v_grp -= lot_mv[j]
        tgt = np.where(grp, lot_mv, tgt)                       # bought on purpose: no rounding excess, peeled last
    pxz = np.where(ok, px, 0.0)
    # the value of the dropped sub-lot names is spread as at most ONE extra lot per traded name (largest targets first);
    # a proportional re-scaling would compound day after day into a few names, one lot per name cannot
    remaining = (max(v_grp, 0.0) if group_below and below.any() else float(np.where(below, w * nav_b, 0.0).sum())) if spread else 0.0
    if remaining > 0 and traded.any():
        for j in np.argsort(-np.where(traded, tgt if not adj.any() else adj, -np.inf)):
            if not traded[j] or remaining < lot_mv[j]:
                if not traded[j]:
                    break
                continue
            sh[j] += lots; remaining -= lot_mv[j]
    # never spend more than the bucket budget: peel single lots off the names with the largest rounding excess
    # (same rule as the live target builder), so rounding up never creates leverage; the budget is the bucket NAV
    # less what unpriced names keep, never more than the sum of the priced targets
    budget = min(float(np.where(ok, w * nav_b, 0.0).sum()) + float(np.where(ok & keep, held - w * nav_b, 0.0).sum()), float(nav_b - np.where(ok, 0.0, tgt).sum()))
    for _ in range(len(sh)):
        if (sh * pxz).sum() <= budget + 1e-6:
            break
        excess = np.where(sh > 0, sh * pxz - tgt - adj * np.where(ok, lot_mv, 0.0), -np.inf)
        if group_below:                          # price-neutral peel: the largest rounding excess in lots, not in CNY (which always hits the expensive names)
            excess = np.where(sh > 0, excess / np.where(ok, lot_mv, 1.0), -np.inf)
        j = int(np.argmax(excess))
        if not np.isfinite(excess[j]):
            break
        sh[j] -= lots
    mv = np.where(ok, sh * pxz, tgt)
    return mv / nav_b


# ----------------------------------------------------------------------------- bucket book
def bucket_book(score: np.ndarray, label1: np.ndarray, member: np.ndarray, buyable: np.ndarray, dates: pd.DatetimeIndex,
                n_enter: int, n_keep: int, min_hold=1, weighting="drift", lot: dict | None = None, keep_w: bool = False) -> dict:
    """Long book inside one bucket with count-based hysteresis. lot: {'px': dates x codes raw next-open, 'nav': bucket NAV,
    'lots': lot size, 'min_trade_mv': no-trade band in CNY} switches on whole-lot execution (A15 item 10).
    score: (dates x codes) float, higher = better, NaN where unknown. member: bucket membership (force-sell outside).
    buyable: purchases only (limit-up open etc.); a held, unbuyable or score-less name is kept while it is a member.
    Returns daily series (return earned, benchmark = equal-weight bucket, turnover as fraction of bucket, count)."""
    n_dates, n_codes = score.shape
    lab = np.where(np.isfinite(label1), label1, 0.0)
    held = np.zeros(n_codes, bool); age = np.zeros(n_codes, np.int32)
    w = np.zeros(n_codes)
    port = np.zeros(n_dates); bench = np.zeros(n_dates); turn = np.zeros(n_dates); cnt = np.zeros(n_dates); n_mem = np.zeros(n_dates)
    maxw = np.zeros(n_dates); inv = np.zeros(n_dates)
    W = np.zeros((n_dates, n_codes), np.float32) if keep_w else None; WP = np.zeros((n_dates, n_codes), np.float32) if keep_w else None
    for t in range(n_dates):
        mem = member[t]
        scored = np.isfinite(score[t]) & mem
        # descending rank position among scored bucket members; unscored -> +inf
        pos = np.full(n_codes, np.inf)
        if scored.any():
            idx = np.where(scored)[0]
            order = idx[np.argsort(-score[t, idx], kind="stable")]
            pos[order] = np.arange(len(order))
        # a held name is kept while it is a member and inside the keep band (a missing score keeps it; leaving the
        # bucket forces a sale); entries need a buyable open (no limit-up)
        stay = held & mem & ((pos < n_keep) | ~scored | (age < min_hold))
        new = (~held) & (pos < n_enter) & buyable[t]
        blocked = int(((~held) & (pos < n_enter) & ~buyable[t]).sum())   # entries that cannot fill today: their slot stays in cash
        if lot is not None:
            # whole-lot execution (A15 item 10): an entry whose equal-weight slot cannot buy half a lot is treated like an
            # unfillable entry (its slot stays in cash), a stay whose drifted value fell below half a lot is sold; without
            # this, names rounded to zero would stay "held" forever while the survivors absorb their money
            half_lot = 0.5 * lot["lots"] * lot["px"][t]
            n_slots0 = int((stay | new).sum()) + blocked
            slot_mv = lot["nav"] / max(n_slots0, 1)
            unaff = new & np.isfinite(half_lot) & (slot_mv < half_lot)
            blocked += int(unaff.sum()); new = new & ~unaff
            stay = stay & ~(np.isfinite(half_lot) & (w * lot["nav"] < half_lot) & (w > 0))
        held_new = stay | new
        age = np.where(stay, age + 1, np.where(new, 1, 0))
        n_mem[t] = mem.sum()
        # weights: drift the previous book by yesterday's return, then apply exits/entries
        n_slots = int(held_new.sum()) + blocked                 # invested fraction today = held / (held + blocked)
        invest = (n_slots - blocked) / n_slots if n_slots > 0 else 0.0
        if weighting == "equal" or not held.any():
            w_new = held_new.astype(np.float64); s = w_new.sum(); w_new = w_new / s * invest if s > 0 else w_new
            w_pre = w.copy()
        else:
            grow = w * (1 + (lab[t - 1] if t > 0 else 0.0))
            w_pre = grow / grow.sum() * (w.sum() if w.sum() > 0 else 1.0) if grow.sum() > 0 else grow
            w_new = np.where(stay, w_pre, 0.0)
            k = int(new.sum()); n_after = int(held_new.sum())
            if n_after > 0:
                if k:
                    target_new = invest / n_after
                    cash = invest - w_new.sum()          # proceeds of exits (plus yesterday's blocked cash)
                    need = k * target_new
                    if cash < need - 1e-12:              # trim existing pro-rata to fund entries
                        ex = w_new.sum()
                        w_new = w_new * ((invest - need) / ex) if ex > 0 else w_new
                    elif lot is not None:                # whole-lot mode: entries absorb the available cash (capped at 2x a slot)
                        target_new = min(cash / k, 2.0 * target_new)
                    w_new = np.where(new, target_new, w_new)
                tot = w_new.sum()
                if tot > 0 and lot is None:              # surplus cash from exits spread pro-rata (buys); not in whole-lot mode,
                    w_new = w_new / tot * invest         # where refilling structural sub-lot cash would inflate the big names daily
        held = held_new; w = w_new
        if lot is not None:
            w = round_lots(w, w_pre, lot["px"][t], lot["nav"], lot["lots"], lot["min_trade_mv"], spread=False)
            held = held & (w > 0)                      # a name rounded to zero is not held
        port[t] = (w * lab[t]).sum()
        u = mem.astype(np.float64); bench[t] = (u * lab[t]).sum() / max(u.sum(), 1)
        turn[t] = 0.5 * np.abs(w - w_pre).sum(); cnt[t] = held.sum(); maxw[t] = w.max() if w.size else 0.0; inv[t] = w.sum()
        if keep_w:
            W[t] = w; WP[t] = w_pre
    out = {"portfolio": pd.Series(port, index=dates), "bucket_ew": pd.Series(bench, index=dates), "turnover": pd.Series(turn, index=dates),
           "count": pd.Series(cnt, index=dates), "members": pd.Series(n_mem, index=dates), "live": pd.Series((cnt > 0) & (n_mem > 0), index=dates),
           "max_weight": pd.Series(maxw, index=dates), "invested": pd.Series(inv, index=dates)}
    if keep_w:
        out["weights"] = W; out["weights_pre"] = WP           # bucket-level weights after / before today's trades (A17 item 4)
    return out


def tilted_bucket_book(score: np.ndarray, label1: np.ndarray, member: np.ndarray, buyable: np.ndarray, cap: np.ndarray, dates: pd.DatetimeIndex,
                       gamma=1.0, x_out=0.2, x_in=0.35, y_in=0.9, y_out=0.7, tau=1.0, return_final=False, excl_score: np.ndarray | None = None,
                       over_score: np.ndarray | None = None, lot: dict | None = None, keep_w: bool = False, veto: np.ndarray | None = None,
                       ovn: np.ndarray | None = None, regroup: np.ndarray | None = None) -> dict:
    """Enhanced-index bucket: every member is held at cap^gamma weight (gamma=1 cap-weighted, 0 equal) x multiplier;
    multiplier 0 for names in the exclusion state (entered when the in-bucket score percentile < x_out, left when
    > x_in), 1+tau in the overweight state (entered at >= y_in, left below y_out), 1 otherwise. States carry
    hysteresis so that turnover comes from state changes, not daily noise. Names leaving the bucket are sold; a
    missing score keeps the previous state. Drift turnover: 0.5 * sum|target - drifted previous weights|."""
    n_dates, n_codes = score.shape
    lab = np.where(np.isfinite(label1), label1, 0.0)
    excl = np.zeros(n_codes, bool); over = np.zeros(n_codes, bool)
    w = np.zeros(n_codes)
    port = np.zeros(n_dates); bench = np.zeros(n_dates); turn = np.zeros(n_dates); cnt = np.zeros(n_dates); n_mem = np.zeros(n_dates)
    maxw = np.zeros(n_dates); inv = np.zeros(n_dates)
    W = np.zeros((n_dates, n_codes), np.float32) if keep_w else None; WP = np.zeros((n_dates, n_codes), np.float32) if keep_w else None
    def _pct(row, mem):
        scored = np.isfinite(row) & mem
        out_ = np.full(n_codes, np.nan)
        if scored.any():
            idx = np.where(scored)[0]
            order = np.argsort(row[idx], kind="stable")
            r = np.empty(len(idx)); r[order] = (np.arange(len(idx)) + 1) / len(idx)
            out_[idx] = r
        return out_

    for t in range(n_dates):
        mem = member[t]
        pct = _pct(score[t], mem)
        has = np.isfinite(pct)
        # exclusion may run on a separate score (e.g. a crash-probability model); default = the same score
        pe = _pct(excl_score[t], mem) if excl_score is not None else pct
        has_e = np.isfinite(pe)
        excl = np.where(has_e, np.where(excl, pe <= x_in, pe < x_out), excl) & mem
        # overweight may run on a separate score too (e.g. a momentum score; A15 item 2); default = the same score
        po = _pct(over_score[t], mem) if over_score is not None else pct
        has_o = np.isfinite(po)
        over = np.where(has_o, np.where(over, po >= y_out, po >= y_in), over) & mem
        base = np.where(mem & np.isfinite(cap[t]) & (cap[t] > 0), np.power(np.where(np.isfinite(cap[t]) & (cap[t] > 0), cap[t], 1.0), gamma), 0.0)
        mult = np.where(excl, 0.0, np.where(over, 1.0 + tau, 1.0))
        if veto is not None:                     # additive veto (A17 item 3): zero weight on top of the model's own exclusions
            mult = np.where(veto[t], 0.0, mult)
        # an unbuyable name (limit-up open) cannot be bought: keep its drifted weight instead of the target
        target = base * mult
        if regroup is not None:
            # A17: the weight freed by exclusions stays inside the name's own style group (e.g. turnover quintile), so the
            # bucket keeps the index's group weights instead of drifting toward the groups with fewer exclusions
            gt = regroup[t]
            for gidv in np.unique(gt[(gt >= 0) & (base > 0)]):
                gm = gt == gidv; tb = base[gm].sum(); tt = target[gm].sum()
                if tt > 0 and tb > 0:
                    target[gm] *= tb / tt
        s = target.sum(); target = target / s if s > 0 else target
        if ovn is not None:
            # A17: the live process fixes SHARES on the close of t and trades them at the next open, so the weights right
            # after the trade are the close-based target drifted by the overnight return. Without this the backtest
            # "rebalances back" to close weights at every open - a mechanical fade of the overnight gap that the live
            # process never does (about 1% a day of turnover in a pure cap-weighted bucket)
            g_ = target * (1.0 + np.where(np.isfinite(ovn[t]), ovn[t], 0.0)); s_ = g_.sum(); target = g_ / s_ if s_ > 0 else target
        grow = w * (1 + (lab[t - 1] if t > 0 else 0.0)); w_pre = grow / grow.sum() if grow.sum() > 0 else grow
        # a name that cannot be bought today (limit-up open) is pinned at its drifted weight and left out of the
        # renormalisation, so the pinned weight is not scaled back up (review fix H3)
        pin = (~buyable[t]) & (target > w_pre)
        if pin.any():
            free = 1.0 - w_pre[pin].sum(); rest = target[~pin].sum()
            target = np.where(pin, w_pre, target * (free / rest) if rest > 0 else 0.0)
        w = target
        if lot is not None:
            w = round_lots(w, w_pre, lot["px"][t], lot["nav"], lot["lots"], lot["min_trade_mv"], pref=pct, pref_k=float(lot.get("pref_k", 0.0)), group_below=bool(lot.get("group_below", False)), hyst=float(lot.get("hyst", 0.0)))
        port[t] = (w * lab[t]).sum()
        u = mem.astype(np.float64); bench[t] = (u * lab[t]).sum() / max(u.sum(), 1)
        turn[t] = 0.5 * np.abs(w - w_pre).sum(); cnt[t] = (w > 0).sum(); n_mem[t] = mem.sum(); maxw[t] = w.max() if w.size else 0.0; inv[t] = w.sum()
        if keep_w:
            W[t] = w; WP[t] = w_pre
    out = {"portfolio": pd.Series(port, index=dates), "bucket_ew": pd.Series(bench, index=dates), "turnover": pd.Series(turn, index=dates),
           "count": pd.Series(cnt, index=dates), "members": pd.Series(n_mem, index=dates), "live": pd.Series((cnt > 0) & (n_mem > 0), index=dates),
           "max_weight": pd.Series(maxw, index=dates), "invested": pd.Series(inv, index=dates)}
    if keep_w:
        out["weights"] = W; out["weights_pre"] = WP
    if return_final:
        out["final_weights"] = w; out["final_excluded"] = excl; out["final_overweight"] = over; out["final_pct"] = pct
    return out



# ----------------------------------------------------------------------------- tracking-error-budget bucket (A15 item 7)
def _project_capped_simplex(v: np.ndarray, upper: np.ndarray) -> np.ndarray:
    """Euclidean projection of v onto {0 <= w <= upper, sum w = 1}: w = clip(v - tau, 0, upper) with the shift tau found
    exactly. f(tau) = sum clip(v - tau, 0, upper) is piecewise linear and non-increasing with breakpoints at v_i and
    v_i - u_i; it is evaluated at every breakpoint with sorted cumulative sums (O(n log n), a few numpy calls) and tau is
    interpolated on the segment where f crosses 1. Names with upper = 0 are fixed at 0."""
    out = np.zeros_like(v, dtype=np.float64)
    m = upper > 0
    if not m.any():
        return out
    vv = v[m].astype(np.float64); uu = upper[m].astype(np.float64)
    a = vv - uu
    ia = np.argsort(a); a_s = a[ia]
    cu_a = np.concatenate([[0.0], np.cumsum(uu[ia])]); cv_a = np.concatenate([[0.0], np.cumsum(vv[ia])])
    v_s = np.sort(vv); cv_v = np.concatenate([[0.0], np.cumsum(v_s)])
    U = float(uu.sum())
    bp = np.sort(np.concatenate([a_s, v_s]))
    ka = np.searchsorted(a_s, bp, side="left")            # number of names with a_i < tau  (at u_i)... see below
    kv = np.searchsorted(v_s, bp, side="right")           # number of names with v_i <= tau (at 0)
    # f(tau) = sum_{a_i >= tau} u_i + sum_{a_i < tau < v_i} (v_i - tau)
    f = (U - cu_a[ka]) + (cv_a[ka] - cv_v[kv]) - bp * (ka - kv)
    if U <= 1.0:                                          # sum = 1 unreachable: everything at its cap
        out[m] = uu; return out
    k = int(np.searchsorted(-f, -1.0))                    # first breakpoint with f <= 1 (f non-increasing)
    if k == 0:
        tau = bp[0]
    else:
        t0, t1 = bp[k - 1], bp[k]; f0, f1 = f[k - 1], f[k]
        tau = t0 + (f0 - 1.0) * (t1 - t0) / (f0 - f1) if f0 != f1 else t1
    out[m] = np.clip(vv - tau, 0.0, uu)
    return out


def _solve_te(alpha: np.ndarray, b: np.ndarray, S: np.ndarray, te_var: float, upper: np.ndarray, w0: np.ndarray | None = None, iters=200, kappa=0.0) -> tuple[np.ndarray, float, float]:
    """max alpha'(w-b) - lam (w-b)'S(w-b) - kappa ||w-w0||^2 over the capped simplex, with lam found by bisection so that
    the tracking variance (w-b)'S(w-b) <= te_var (binding when the unconstrained optimum exceeds it). kappa > 0 is a
    quadratic turnover penalty against the current (drifted) weights w0. Accelerated projected gradient."""
    n = len(alpha)
    L0 = float(np.linalg.eigvalsh(S)[-1]) if n else 1.0
    wprev = b.copy() if w0 is None else w0.copy()
    warm = [wprev.copy()]
    def solve(lam):
        w = warm[0].copy(); y = w.copy(); t = 1.0          # warm start from the previous lambda's solution
        step = 1.0 / (2.0 * lam * L0 + 2.0 * kappa + 1e-12)
        for _ in range(iters):
            g = -alpha + 2.0 * lam * (S @ (y - b)) + 2.0 * kappa * (y - wprev)
            w_new = _project_capped_simplex(y - step * g, upper)
            t_new = 0.5 * (1 + np.sqrt(1 + 4 * t * t))
            y = w_new + (t - 1) / t_new * (w_new - w)
            w, t = w_new, t_new
        warm[0] = w
        return w
    def tv(w):
        d = w - b; return float(d @ S @ d)
    lam_lo, lam_hi = 1e-3, 1e5
    w = solve(lam_lo)
    if tv(w) <= te_var:
        return w, tv(w), lam_lo
    for _ in range(14):
        lam = np.sqrt(lam_lo * lam_hi)
        w = solve(lam)
        if tv(w) > te_var:
            lam_lo = lam
        else:
            lam_hi = lam
    w = solve(lam_hi)
    return w, tv(w), lam_hi


def tebudget_bucket_book(score: np.ndarray, label1: np.ndarray, member: np.ndarray, buyable: np.ndarray, cap: np.ndarray, ret: np.ndarray, dates: pd.DatetimeIndex,
                         te_ann=0.04, rebalance=5, lookback=250, max_w=0.10, alpha_power=1.0, excl_score: np.ndarray | None = None, x_out=0.0, kappa=0.0,
                         max_tilt: float | None = None, lot: dict | None = None) -> dict:
    """Enhanced-index bucket by optimisation: every `rebalance` sessions the bucket weights maximise the score-weighted
    expected excess against the cap-weighted bucket subject to annualised tracking error <= te_ann (Ledoit-Wolf
    covariance of the trailing `lookback` sessions of the members' returns), long-only, sum = 1, single name <= max_w.
    alpha_i = centred in-bucket score percentile (^alpha_power keeps the sign). Optional hard exclusion (names whose
    exclusion-score percentile < x_out get an upper bound of 0). Between solves the book drifts. Same outputs as
    tilted_bucket_book."""
    from sklearn.covariance import LedoitWolf
    n_dates, n_codes = score.shape
    lab = np.where(np.isfinite(label1), label1, 0.0)
    w = np.zeros(n_codes); held_idx = None
    port = np.zeros(n_dates); bench = np.zeros(n_dates); turn = np.zeros(n_dates); cnt = np.zeros(n_dates); n_mem = np.zeros(n_dates)
    maxw = np.zeros(n_dates); inv = np.zeros(n_dates); te_real = np.full(n_dates, np.nan); lam_used = np.full(n_dates, np.nan)
    te_var = (te_ann ** 2) / 252.0
    last_solve = -10 ** 9
    for t in range(n_dates):
        mem = member[t]
        grow = w * (1 + (lab[t - 1] if t > 0 else 0.0)); w_pre = grow / grow.sum() if grow.sum() > 0 else grow
        n_mem[t] = mem.sum()
        need = (t - last_solve >= rebalance) or (not (w_pre[mem].sum() > 0.5))
        if need and mem.any() and t >= 20:
            idx = np.where(mem & np.isfinite(cap[t]) & (cap[t] > 0))[0]
            b = cap[t, idx]; b = b / b.sum()
            sc = score[t, idx]
            has = np.isfinite(sc)
            r = np.empty(len(idx)); r[:] = 0.5
            if has.any():
                order = np.argsort(sc[has], kind="stable"); rr = np.empty(has.sum()); rr[order] = (np.arange(has.sum()) + 1) / has.sum(); r[has] = rr
            alpha = np.sign(r - 0.5) * np.abs(r - 0.5) ** alpha_power
            lo = max(0, t - lookback)
            R = ret[lo:t, idx]                              # returns through the previous session (no look-ahead)
            R = np.where(np.isfinite(R), R, 0.0); R = R - R.mean(axis=0, keepdims=True)
            if R.shape[0] >= 60:
                S = LedoitWolf().fit(R).covariance_
            else:
                S = np.diag(np.var(R, axis=0) + 1e-6)
            upper = np.full(len(idx), max_w)
            if max_tilt is not None:                          # bounded tilt: no name above max_tilt x its cap weight (removal still allowed)
                upper = np.minimum(upper, max_tilt * b)
            if excl_score is not None and x_out > 0:
                es = excl_score[t, idx]; he = np.isfinite(es)
                if he.any():
                    o2 = np.argsort(es[he], kind="stable"); pe = np.empty(he.sum()); pe[o2] = (np.arange(he.sum()) + 1) / he.sum()
                    ex = np.zeros(len(idx), bool); ex[np.where(he)[0][pe < x_out]] = True
                    upper[ex] = 0.0
            upper[~buyable[t, idx] & (w_pre[idx] <= 0)] = 0.0         # cannot open a new position at a limit-up open
            w0 = w_pre[idx] / w_pre[idx].sum() if w_pre[idx].sum() > 0 else None
            sol, tvar, lam = _solve_te(alpha, b, S, te_var, upper, w0=w0, kappa=kappa)
            w_new = np.zeros(n_codes); w_new[idx] = sol
            te_real[t] = np.sqrt(max(tvar, 0.0) * 252); lam_used[t] = lam
            last_solve = t
        else:
            w_new = np.where(mem, w_pre, 0.0)
            s = w_new.sum(); w_new = w_new / s if s > 0 else w_new
        w = w_new
        if lot is not None:
            w = round_lots(w, w_pre, lot["px"][t], lot["nav"], lot["lots"], lot["min_trade_mv"])
        port[t] = (w * lab[t]).sum()
        u = mem.astype(np.float64); bench[t] = (u * lab[t]).sum() / max(u.sum(), 1)
        turn[t] = 0.5 * np.abs(w - w_pre).sum(); cnt[t] = (w > 1e-6).sum(); maxw[t] = w.max() if w.size else 0.0; inv[t] = w.sum()
    return {"portfolio": pd.Series(port, index=dates), "bucket_ew": pd.Series(bench, index=dates), "turnover": pd.Series(turn, index=dates),
            "count": pd.Series(cnt, index=dates), "members": pd.Series(n_mem, index=dates), "live": pd.Series((cnt > 0) & (n_mem > 0), index=dates),
            "max_weight": pd.Series(maxw, index=dates), "invested": pd.Series(inv, index=dates), "te_exante": pd.Series(te_real, index=dates), "lambda": pd.Series(lam_used, index=dates)}

def smooth_scores(pred: pd.DataFrame, mask: pd.DataFrame, smooth: int) -> pd.DataFrame:
    """Score percentile within the mask, optionally EWM-smoothed over `smooth` sessions (rank first, then smooth)."""
    p = pred.where(mask)
    if smooth:
        p = p.rank(axis=1, pct=True).ewm(span=smooth, adjust=False, min_periods=1).mean()
    return p.where(mask)


def style_field(panel, name: str) -> pd.DataFrame:
    """Style grouping field by name: turnover20 (20-session mean turnover), vol20 (20-session return std), mom20
    (20-session price change) or any raw panel field (float_market_cap, total_market_cap, log_cap, ...)."""
    if name == "turnover20":
        return panel["turnover"].rolling(20, min_periods=10).mean()
    if name == "vol20":
        return panel["ret"].rolling(20, min_periods=10).std()
    if name == "mom20":
        return panel["close"].pct_change(20, fill_method=None)
    return panel[name]


def cap_neutral_within(sc: pd.DataFrame, cap: pd.DataFrame, mask: pd.DataFrame, q: int) -> pd.DataFrame:
    """Percentile of the score inside cap groups (q quantiles of `cap` among `mask` names, per date)."""
    cpct = cap.where(mask).rank(axis=1, pct=True)
    grp = np.where(np.isfinite(cpct.to_numpy()), np.clip(np.ceil(np.nan_to_num(cpct.to_numpy()) * q), 1, q), 0).astype(int)
    out = pd.DataFrame(np.nan, index=sc.index, columns=sc.columns, dtype=np.float32)
    for g in range(1, q + 1):
        mg = mask & pd.DataFrame(grp == g, index=sc.index, columns=sc.columns)
        out = out.where(~mg, sc.where(mg).rank(axis=1, pct=True).astype(np.float32))
    return out


def group_neutral_within(sc: pd.DataFrame, gid: pd.DataFrame, mask: pd.DataFrame) -> pd.DataFrame:
    """Percentile of the score inside categorical groups (gid: date x code ids, NaN = no group) among `mask` names."""
    g = gid.where(mask)
    ids = np.unique(g.to_numpy()[np.isfinite(g.to_numpy())])
    out = pd.DataFrame(np.nan, index=sc.index, columns=sc.columns, dtype=np.float32)
    for v in ids:
        mg = mask & (g == v)
        out = out.where(~mg, sc.where(mg).rank(axis=1, pct=True).astype(np.float32))
    return out


def neutral_scores(panel, sc_df: pd.DataFrame, m500: pd.DataFrame, moth: pd.DataFrame, neutral_field: str, q: int) -> pd.DataFrame:
    """Style-neutral percentiles per bucket. neutral_field is one grouping or several joined by '|' applied in
    sequence (each stage ranks the previous stage's percentile inside its own groups): a style name (percentile inside
    `q` quantile groups of style_field) or 'cluster:<parquet>' (percentile inside categorical cluster ids; A15 item 6)."""
    out = sc_df
    for part in str(neutral_field).split("|"):
        part = part.strip()
        if part.startswith("cluster:"):
            gid = pd.read_parquet(part[len("cluster:"):]); gid.index = pd.DatetimeIndex(gid.index)
            gid = gid.reindex(index=panel.dates, columns=panel.codes)
            out = group_neutral_within(out, gid, m500).where(m500, group_neutral_within(out, gid, moth))
        else:
            grp = style_field(panel, part)
            out = cap_neutral_within(out, grp, m500, q).where(m500, cap_neutral_within(out, grp, moth, q))
    return out


def two_bucket_book(pred: pd.DataFrame, panel, mem: dict, bench1: np.ndarray, cfg: dict, excl_pred: pd.DataFrame | None = None,
                    over_pred: pd.DataFrame | None = None) -> dict:
    """The competition book: W500 in a CSI500 bucket, WOTH in the other-union bucket, remainder cash.
    excl_pred (or cfg['exclusion_pred'] = parquet path): separate score driving the enhanced book's exclusion state;
    over_pred (or cfg['overweight_pred']): separate score driving its overweight state (the main score still selects the
    other bucket and, unless excl_pred is given, the exclusions)."""
    dates = panel.dates
    pred = pred.reindex(index=dates, columns=panel.codes)
    if excl_pred is None and cfg.get("exclusion_pred"):
        excl_pred = pd.read_parquet(cfg["exclusion_pred"]); excl_pred.index = pd.DatetimeIndex(excl_pred.index)
    if excl_pred is not None:
        excl_pred = excl_pred.reindex(index=dates, columns=panel.codes)
    if over_pred is None and cfg.get("overweight_pred"):
        over_pred = pd.read_parquet(cfg["overweight_pred"]); over_pred.index = pd.DatetimeIndex(over_pred.index)
    if over_pred is not None:
        over_pred = over_pred.reindex(index=dates, columns=panel.codes)
    label1 = panel["label_1"].to_numpy(np.float64)
    base = trading_mask(panel); mb = buyable_mask(panel, base)
    m500 = base & mem["csi500"]; moth = base & mem["union"] & ~mem["csi500"]
    if cfg.get("oth_universe"):                  # A17: other bucket drawn from one index only (e.g. "csi1000": no CSI300 large caps)
        moth = moth & mem[cfg["oth_universe"]]
    if cfg.get("oth_cap_band"):                  # A17: keep only the other-bucket names whose size sits inside the CSI500 size range (quantiles of the members' caps)
        capx = panel[cfg.get("cap_field", "float_market_cap")]; qlo, qhi = cfg["oth_cap_band"]
        lo_ = capx.where(m500).quantile(float(qlo), axis=1); hi_ = capx.where(m500).quantile(float(qhi), axis=1)
        moth = moth & capx.ge(lo_, axis=0) & capx.le(hi_, axis=0)
    # score percentile is computed inside the whole union so that the two buckets share one smoothing history
    sc_df = smooth_scores(pred, base & mem["union"], cfg["smooth"])
    q = int(cfg.get("neutral_q", 0) or 0)
    if q > 0:  # style-neutral ranks: percentile inside groups of a style field (or cluster ids, or a sequence), each bucket separately
        sc_df = neutral_scores(panel, sc_df, m500, moth, cfg.get("neutral_field", "float_market_cap"), q)
    sc = sc_df.to_numpy(np.float32)
    def _second(pred2):   # a second score gets the same union percentile / smoothing / bucket-level style neutralisation as the main score
        if pred2 is None:
            return None
        s2 = smooth_scores(pred2, base & mem["union"], cfg["smooth"])
        if q > 0:
            s2 = neutral_scores(panel, s2, m500, moth, cfg.get("neutral_field", "float_market_cap"), q)
        return s2.to_numpy(np.float32)
    n500 = int(cfg["n500"]); noth = int(cfg["noth"]); km = float(cfg["keep_mult"])
    kw = bool(cfg.get("keep_weights", False))             # keep daily bucket weights (execution study, A17 item 4)
    lot500 = lototh = None
    lot_nav = float(cfg.get("lot_nav", 0) or 0)
    if lot_nav > 0:
        if not panel.has("raw_open"):
            raise KeyError("lot rounding needs the panel field raw_open (scripts/pv2_raw_open.py)")
        px = panel["raw_open"].shift(-1).to_numpy(np.float64)          # executed at the next open, raw price
        lots = int(cfg.get("lots", 100)); mt = float(cfg.get("lot_min_trade", 0.001)) * lot_nav
        lot500 = {"px": px, "nav": float(cfg["w500"]) * lot_nav, "lots": lots, "min_trade_mv": mt, "pref_k": float(cfg.get("lot_pref_k", 0.0) or 0.0), "group_below": bool(cfg.get("lot_group_below", False)), "hyst": float(cfg.get("lot_hyst", 0.0) or 0.0)}
        lototh = {"px": px, "nav": float(cfg["woth"]) * lot_nav, "lots": lots, "min_trade_mv": mt}
    if cfg.get("book", "concentrated") == "tebudget":   # A15 item 7: optimised enhanced bucket under a tracking-error budget
        cap = panel[cfg.get("cap_field", "float_market_cap")].to_numpy(np.float64)
        ex_np = _second(excl_pred) if excl_pred is not None else (sc if float(cfg.get("x_out", 0)) > 0 and cfg.get("te_excl", False) else None)
        b500 = tebudget_bucket_book(sc, label1, m500.to_numpy(bool), mb.to_numpy(bool), cap, panel["ret"].to_numpy(np.float64), dates, te_ann=float(cfg.get("te_ann", 0.04)),
                                    rebalance=int(cfg.get("te_rebalance", 5)), lookback=int(cfg.get("te_lookback", 250)), max_w=float(cfg.get("te_max_w", 0.10)),
                                    alpha_power=float(cfg.get("te_alpha_power", 1.0)), excl_score=ex_np, x_out=float(cfg.get("x_out", 0.0)) if cfg.get("te_excl", False) else 0.0,
                                    kappa=float(cfg.get("te_kappa", 0.0)), max_tilt=(float(cfg["te_max_tilt"]) if cfg.get("te_max_tilt") else None), lot=lot500)
    elif cfg.get("book", "concentrated") == "enhanced":
        cap = panel[cfg.get("cap_field", "float_market_cap")].to_numpy(np.float64)
        if cfg.get("cap_mult"):                  # A17: implied index-weight multipliers (scripts/pv4_index_weights.py), date x code, missing = 1
            cm = pd.read_parquet(cfg["cap_mult"]); cm.index = pd.DatetimeIndex(cm.index)
            cap = cap * cm.reindex(index=dates, columns=panel.codes).ffill().fillna(1.0).to_numpy(np.float64)
        ex_np = _second(excl_pred); ov_np = _second(over_pred)
        rg_np = None
        if cfg.get("regroup_field"):             # quantile groups of a style field inside the CSI500 bucket, per date
            sf = style_field(panel, cfg["regroup_field"]).where(m500)
            rg_np = np.ceil(sf.rank(axis=1, pct=True) * int(cfg.get("regroup_q", 5))).fillna(-1).to_numpy(np.int16)
        veto_np = None
        if cfg.get("veto_mask"):                 # parquet date x code, non-zero = vetoed that day (e.g. predicted index deletions)
            vm = pd.read_parquet(cfg["veto_mask"]); vm.index = pd.DatetimeIndex(vm.index)
            veto_np = (vm.reindex(index=dates, columns=panel.codes).fillna(0).to_numpy() != 0)
        b500 = tilted_bucket_book(sc, label1, m500.to_numpy(bool), mb.to_numpy(bool), cap, dates, gamma=float(cfg["gamma"]), x_out=float(cfg["x_out"]), x_in=float(cfg["x_in"]),
                                  y_in=float(cfg["y_in"]), y_out=float(cfg["y_out"]), tau=float(cfg["tau"]), excl_score=ex_np, over_score=ov_np, lot=lot500, keep_w=kw, veto=veto_np, regroup=rg_np,
                                  ovn=((panel["open"].shift(-1) / panel["close"] - 1).to_numpy(np.float64) if cfg.get("shares_at_close", False) else None))
    else:
        b500 = bucket_book(sc, label1, m500.to_numpy(bool), mb.to_numpy(bool), dates, n500, int(round(km * n500)), cfg["min_hold"], cfg["weighting"], lot=lot500, keep_w=kw)
    both = bucket_book(sc, label1, moth.to_numpy(bool), mb.to_numpy(bool), dates, noth, int(round(km * noth)), cfg["min_hold"], cfg["weighting"], lot=lototh, keep_w=kw)
    w5, wo = float(cfg["w500"]), float(cfg["woth"])
    port = w5 * b500["portfolio"] + wo * both["portfolio"]
    turn = w5 * b500["turnover"] + wo * both["turnover"]
    bench = pd.Series(bench1, index=dates)
    live = b500["live"] & both["live"] & bench.notna()
    return {"portfolio": port, "benchmark": bench, "excess": port - bench, "turnover": turn, "live": live,
            "count500": b500["count"], "countoth": both["count"], "bucket500": b500, "bucketoth": both,
            "ew500_vs_index": b500["bucket_ew"] - bench,
            "max_single": pd.concat([w5 * b500["max_weight"], wo * both["max_weight"]], axis=1).max(axis=1),
            "share500": w5 * b500["invested"], "invested": w5 * b500["invested"] + wo * both["invested"]}


# ----------------------------------------------------------------------------- statistics
def _ann(s):
    return float(s.mean() * 252)


def _sharpe(s):
    sd = s.std(ddof=1)
    return float(s.mean() / sd * np.sqrt(252)) if sd > 0 else float("nan")


def _mdd(s):
    c = (1 + s).cumprod(); return float((c / c.cummax() - 1).min())


def window_stats(net: pd.Series, n: int) -> dict:
    """Cumulative net excess over n-session windows (compounded difference of the two legs is approximated by the
    sum of daily net excess; for 10-20 sessions the difference is < 0.05 pt)."""
    if len(net) < n:
        return {"n": n, "windows": 0}
    roll = net.rolling(n).sum().dropna()
    step = net.iloc[::n].index
    nonov = pd.Series([net.loc[a:b].iloc[:n].sum() for a, b in zip(step[:-1], step[1:])], index=step[:-1]) if len(step) > 1 else roll
    return {"n": n, "windows": int(len(roll)), "mean": float(roll.mean()), "p10": float(roll.quantile(0.1)), "p50": float(roll.median()),
            "p90": float(roll.quantile(0.9)), "share_pos": float((roll > 0).mean()), "nonoverlap_windows": int(len(nonov)),
            "nonoverlap_share_pos": float((nonov > 0).mean()), "nonoverlap_mean": float(nonov.mean()), "nonoverlap_p10": float(nonov.quantile(0.1)),
            "nonoverlap_p90": float(nonov.quantile(0.9))}


def summarize_book(book: dict, years, cost: float) -> dict:
    ex = book["excess"]; tv = book["turnover"]; live = book["live"].to_numpy(bool)
    yrs = ex.index.year
    sel = np.isin(yrs, list(years)) & live
    e = ex[sel]; t = tv[sel]; net = e - cost * t
    p = book["portfolio"][sel]; b = book["benchmark"][sel]
    pn = p - cost * t
    out = {"gross_excess_ann": _ann(e), "gross_sharpe": _sharpe(e), "net_excess_ann": _ann(net), "net_sharpe": _sharpe(net), "daily_turnover": float(t.mean()),
           "cost_one_way": cost, "max_drawdown_net": _mdd(net), "max_drawdown_gross": _mdd(e), "portfolio_ann": _ann(p), "benchmark_ann": _ann(b),
           "cum_portfolio_net": float((1 + pn).prod() - 1), "cum_benchmark": float((1 + b).prod() - 1), "live_days": int(sel.sum()),
           "avg_names_500": float(book["count500"][sel].mean()), "min_names_500": float(book["count500"][sel].min()) if sel.any() else None,
           "avg_names_oth": float(book["countoth"][sel].mean()), "ew500_vs_index_ann": _ann(book["ew500_vs_index"][sel]), "annual": {}}
    out["cum_excess_net"] = out["cum_portfolio_net"] - out["cum_benchmark"]
    if "max_single" in book:   # competition hard limits (single name <= 10% / 9.5% buffer, CSI500 share > 80% / 82%, invested >= 90% / 92%)
        ms = book["max_single"][sel]; sh = book["share500"][sel]; iv = book["invested"][sel]
        out["constraints"] = {"max_single_weight": float(ms.max()), "days_single_gt_9.5pct": int((ms > 0.095).sum()), "min_share500": float(sh.min()),
                              "days_share500_le_82pct": int((sh <= 0.82).sum()), "min_invested": float(iv.min()), "days_invested_lt_92pct": int((iv < 0.92).sum())}
    for y in years:
        m = (yrs == y) & live
        if not m.any():
            continue
        ey = ex[m]; ny = (ex - cost * tv)[m]; py = (book["portfolio"] - cost * tv)[m]; by = book["benchmark"][m]
        out["annual"][str(y)] = {"gross_excess": _ann(ey), "net_excess": _ann(ny), "net_sharpe": _sharpe(ny), "turnover": float(tv[m].mean()), "live_days": int(m.sum()),
                                 "cum_excess_net": float((1 + py).prod() - (1 + by).prod()), "max_drawdown_net": _mdd(ny), "w10": window_stats(ny, 10), "w20": window_stats(ny, 20)}
    out["years_net_positive"] = int(sum(1 for v in out["annual"].values() if v["net_excess"] > 0))
    out["worst_year_net"] = float(min(v["net_excess"] for v in out["annual"].values())) if out["annual"] else None
    out["w10"] = window_stats(net, 10); out["w20"] = window_stats(net, 20)
    return out


def rank_ic_by_universe(pred: pd.DataFrame, panel, mem: dict, years, label="label_5", min_stocks=50) -> dict:
    base = trading_mask(panel) & (panel["eval_ok"] > 0)
    lab = panel[label]
    pred = pred.reindex(index=panel.dates, columns=panel.codes)
    out = {}
    for name, msk in (("all_a", base), ("union", base & mem["union"]), ("csi300", base & mem["csi300"]), ("csi500", base & mem["csi500"]), ("csi1000", base & mem["csi1000"])):
        r, n = rank_ic(pred, lab, msk, min_stocks=min_stocks)
        ann = annual_table(r, tuple(years))
        vals = {str(y): v["mean"] for y, v in ann.items()}
        ok = [v for v in vals.values() if v is not None]
        out[name] = {"annual": vals, "mean": float(np.mean(ok)) if ok else None, "worst": float(np.min(ok)) if ok else None}
    return out


def evaluate_prediction(panel, pred: pd.DataFrame, years, cfg: dict | None = None, instruments_dir: str | None = None, index_source: str | None = None,
                        with_ic=True) -> dict:
    cfg = dict(DEFAULT_CFG, **(cfg or {}))
    mem, mnote = membership(panel, instruments_dir)
    bench1, bnote = benchmark_label1(panel.dates, "csi500", index_source)
    book = two_bucket_book(pred, panel, mem, bench1, cfg)
    res = {"cfg": cfg, "membership": mnote, "benchmark": bnote, "years": list(years), "book": summarize_book(book, years, cfg["cost"])}
    if with_ic:
        res["rank_ic"] = rank_ic_by_universe(pred, panel, mem, years)
    return res


def print_result(res: dict, label=""):
    b = res["book"]; y = list(res["years"])
    print(f"== {label} years {y[0]}..{y[-1]} | membership: {res['membership']} | benchmark: {res['benchmark']}")
    print("   cfg: " + " ".join(f"{k}={v}" for k, v in res["cfg"].items()))
    if "rank_ic" in res:
        print("   RankIC: " + "  ".join(f"{k} {v['mean']:.3f}/{v['worst']:.3f}" for k, v in res["rank_ic"].items() if v["mean"] is not None))
    print("   book: gross %+.1f%%  net %+.1f%%  sharpe %.2f  turn %.1f%%/day  mdd %.1f%%  names500 %.0f (min %s)  ew500-index %+.1f%%  cum net excess %+.1f%% over %d days" % (
        b["gross_excess_ann"] * 100, b["net_excess_ann"] * 100, b["net_sharpe"], b["daily_turnover"] * 100, b["max_drawdown_net"] * 100, b["avg_names_500"],
        f"{b['min_names_500']:.0f}" if b["min_names_500"] is not None else "-", b["ew500_vs_index_ann"] * 100, b["cum_excess_net"] * 100, b["live_days"]))
    print("   annual net: " + "  ".join(f"{yy} {v['net_excess'] * 100:+.1f}% (cum {v['cum_excess_net'] * 100:+.1f}%)" for yy, v in b["annual"].items()))
    w = b["w10"]
    if w.get("windows"):
        print("   10-day net excess: mean %+.2f%% p10 %+.2f%% p90 %+.2f%% share>0 %.0f%% (overlapping %d) | non-overlapping share>0 %.0f%% (%d windows)" % (
            w["mean"] * 100, w["p10"] * 100, w["p90"] * 100, w["share_pos"] * 100, w["windows"], w["nonoverlap_share_pos"] * 100, w["nonoverlap_windows"]))
    w = b["w20"]
    if w.get("windows"):
        print("   20-day net excess: mean %+.2f%% p10 %+.2f%% p90 %+.2f%% share>0 %.0f%%" % (w["mean"] * 100, w["p10"] * 100, w["p90"] * 100, w["share_pos"] * 100))


# ----------------------------------------------------------------------------- pre-trade constraint check
def precheck(holdings: dict, targets: dict, nav: float, is500: dict, fill_rates=(1.0, 0.8, 0.5, 0.0), lim=None) -> dict:
    """Constraint ratios for a target book under partial fills. holdings/targets: {code: market value}; is500: {code: bool}.
    Scenario 'sells first, buys filled at rate f': every sell completes, every buy fills a fraction f."""
    lim = lim or {"share500": 0.82, "invested": 0.92, "single": 0.095}
    codes = set(holdings) | set(targets)
    out = {}
    for f in fill_rates:
        mv = {}
        for c in codes:
            h = holdings.get(c, 0.0); tgt = targets.get(c, 0.0)
            mv[c] = tgt if tgt <= h else h + f * (tgt - h)
        tot = sum(mv.values()); s500 = sum(v for c, v in mv.items() if is500.get(c, False))
        single = max(mv.values()) / nav if mv else 0.0
        ok = (s500 / nav > lim["share500"]) and (tot / nav >= lim["invested"]) and (single <= lim["single"])
        out[f"fill_{f:.2f}"] = {"invested": tot / nav, "share500": s500 / nav, "max_single": single, "ok": bool(ok)}
    # sells done, nothing bought yet (worst intraday point of a sells-first execution)
    return out


# ----------------------------------------------------------------------------- freeze
def freeze_version(out_dir: str, rule_cfg: dict, preds: dict, panels: dict, years: dict, note: str = "", shadow_cfgs: dict | None = None) -> dict:
    """Write a frozen competition version: rule.json (book cfg + timing/cost conventions), the score files it applies
    to (copied), and the replay results per period. preds/panels/years are keyed by period label ('research',
    'forward'). shadow_cfgs: additional named rules evaluated on the same scores (shadow arms)."""
    import shutil
    os.makedirs(out_dir, exist_ok=True)
    cfg = dict(DEFAULT_CFG, **(rule_cfg or {}))
    results = {}
    for period, pred_path in preds.items():
        dst = os.path.join(out_dir, f"scores_{period}.parquet")
        if not os.path.exists(dst):
            shutil.copyfile(pred_path, dst)
        panel = Panel(panels[period]); panel._dtype = np.float32
        pred = pd.read_parquet(dst); pred.index = pd.DatetimeIndex(pred.index)
        res = evaluate_prediction(panel, pred, tuple(years[period]), cfg)
        res["prediction_source"] = pred_path; res["panel_dir"] = panels[period]
        results[period] = res
        for name, over in (shadow_cfgs or {}).items():
            r2 = evaluate_prediction(panel, pred, tuple(years[period]), dict(DEFAULT_CFG, **over), with_ic=False)
            results[f"{period}|shadow:{name}"] = {"cfg": r2["cfg"], "book": r2["book"]}
        panel.release()
    rule_doc = {"version": os.path.basename(os.path.normpath(out_dir)), "frozen": time.strftime("%Y-%m-%dT%H:%M:%S"), "cfg": cfg, "shadow_cfgs": shadow_cfgs or {}, "note": note,
                "universe": "CSI300 U CSI500 U CSI1000 constituents by effective date; eligible = non-ST, non-delisting, listed >= 120 sessions, positive amount",
                "timing": "scores from data through the close of t; orders at the open of t+1; first holding day earns open[t+2]/open[t+1]-1; benchmark = CSI500 price index, same timing",
                "book": "two buckets: w500 of NAV in CSI500 names, woth in other union names, rest cash; CSI500 bucket = " + ("cap^gamma weights, exclusion below x_out (re-entry above x_in), overweight 1+tau above y_in (until y_out)" if cfg.get("book") == "enhanced" else "top-n500 hysteresis (keep while inside keep_mult*n500)") + "; other bucket = top-noth hysteresis; scores = union percentile, EWM span `smooth`",
                "cost": "one-way `cost` x fraction of NAV traded per day", "scores": {k: f"scores_{k}.parquet" for k in preds}, "replay": "python -m quanta_agents.factor_lab_a.competition eval --panel-dir <panel> --prediction scores_<period>.parquet --years ... (cfg flags from rule.json)"}
    with open(os.path.join(out_dir, "rule.json"), "w", encoding="utf-8") as fh:
        json.dump(rule_doc, fh, ensure_ascii=False, indent=1)
    with open(os.path.join(out_dir, "replay_results.json"), "w", encoding="utf-8") as fh:
        json.dump(results, fh, ensure_ascii=False, indent=1, default=float)
    return {"out_dir": out_dir, "periods": list(results)}


# ----------------------------------------------------------------------------- CLI
def _cli_eval(a):
    panel = Panel(a.panel_dir); panel._dtype = np.float32
    pred = pd.read_parquet(a.prediction); pred.index = pd.DatetimeIndex(pred.index)
    cfg = {k: getattr(a, k) for k in DEFAULT_CFG if getattr(a, k, None) is not None}
    res = evaluate_prediction(panel, pred, tuple(a.years), cfg, instruments_dir=a.instruments_dir, index_source=a.index_source, with_ic=not a.no_ic)
    res["prediction"] = a.prediction; res["panel_dir"] = a.panel_dir
    print_result(res, a.label or os.path.basename(a.prediction))
    if a.out:
        with open(a.out, "w", encoding="utf-8") as fh:
            json.dump(res, fh, ensure_ascii=False, indent=1, default=float)


def main(argv=None):
    ap = argparse.ArgumentParser(); sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("eval"); s.add_argument("--panel-dir", required=True); s.add_argument("--prediction", required=True); s.add_argument("--years", nargs="*", type=int, default=[2019, 2020, 2021, 2022, 2023, 2024])
    for k, v in DEFAULT_CFG.items():
        s.add_argument(f"--{k.replace('_', '-')}", dest=k, type=type(v) if not isinstance(v, str) else str, default=None)
    s.add_argument("--instruments-dir", default=None); s.add_argument("--index-source", default=None); s.add_argument("--out", default=None); s.add_argument("--label", default=None); s.add_argument("--no-ic", action="store_true")
    s = sub.add_parser("membership-fields"); s.add_argument("--panel-dir", required=True); s.add_argument("--instruments-dir", default=None); s.add_argument("--overwrite", action="store_true")
    s = sub.add_parser("freeze"); s.add_argument("--out", required=True); s.add_argument("--cfg", required=True, help="json overrides of DEFAULT_CFG"); s.add_argument("--shadow", default=None, help="json {name: cfg overrides}")
    s.add_argument("--research-pred", required=True); s.add_argument("--research-panel", default=r"F:/A_Layer_Research/panel"); s.add_argument("--research-years", nargs="*", type=int, default=[2019, 2020, 2021, 2022, 2023, 2024])
    s.add_argument("--forward-pred", default=None); s.add_argument("--forward-panel", default=r"F:/A_Layer_OOS/panel"); s.add_argument("--forward-years", nargs="*", type=int, default=[2025, 2026]); s.add_argument("--note", default="")
    a = ap.parse_args(argv)
    if a.cmd == "eval":
        _cli_eval(a)
    elif a.cmd == "freeze":
        preds = {"research": a.research_pred}; panels = {"research": a.research_panel}; years = {"research": a.research_years}
        if a.forward_pred:
            preds["forward"] = a.forward_pred; panels["forward"] = a.forward_panel; years["forward"] = a.forward_years
        print(json.dumps(freeze_version(a.out, json.loads(a.cfg), preds, panels, years, a.note, json.loads(a.shadow) if a.shadow else None), ensure_ascii=False))
    else:
        print(json.dumps(write_membership_fields(a.panel_dir, a.instruments_dir, a.overwrite), ensure_ascii=False))


if __name__ == "__main__":
    main()
