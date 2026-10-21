"""Trading layer (A13 step 3): buffer-band / holding-period grids, multi-horizon training labels, member selection
by after-cost long excess, training-period long statistics, and a frozen-version replay.

python -m quanta_agents.factor_lab_a.trading grid --root ... --prediction <pred.parquet> [--years 2019 ... ] [--cost 0.002]
python -m quanta_agents.factor_lab_a.trading train-long --root ...          (training-period long stats for every rank array)
python -m quanta_agents.factor_lab_a.trading freeze --root ... --prediction ... --rule <json> --out <dir>
python -m quanta_agents.factor_lab_a.trading replay --panel-dir ... --scores <dir>/scores.parquet --rule <dir>/rule.json
"""
from __future__ import annotations
import argparse
import json
import os
import time
import numpy as np
import pandas as pd

from .panel import Panel
from .evaluate import Evaluator, rank_ic
from .ledger import Store
from . import portfolio as pf


def _log(msg):
    print(time.strftime("%H:%M:%S"), msg, flush=True)


def trading_mask(panel) -> pd.DataFrame:
    """Tradable universe for portfolio statistics: eligibility only. The evaluator's mask additionally blanks the
    last six signal dates of each calendar year (label purge for IC evaluation); a portfolio must not go flat there."""
    return panel.mask("all_a")


# ----------------------------------------------------------------------------- buffer-band portfolio
def buffer_band_series(pct: np.ndarray, label1: np.ndarray, mask: np.ndarray, dates: pd.DatetimeIndex, top_frac=0.1, band=0.05,
                       min_hold=1) -> dict:
    """Daily-rebalanced long portfolio with hysteresis: a name enters when its percentile >= 1-top_frac and stays while
    its percentile >= 1-top_frac-band (and at least min_hold sessions have passed). Equal weight over held names.
    Position formed at t earns label1[t] (next open to the open after). Returns the same series dict as portfolio_series."""
    n_dates, n_codes = pct.shape
    valid = np.isfinite(pct) & mask
    enter_lvl = 1.0 - top_frac; keep_lvl = 1.0 - top_frac - band
    held = np.zeros(n_codes, bool); age = np.zeros(n_codes, np.int32)
    port = np.zeros(n_dates); bench = np.zeros(n_dates); turn = np.zeros(n_dates); cnt = np.zeros(n_dates)
    prev_w = np.zeros(n_codes)
    lab = np.where(np.isfinite(label1), label1, 0.0)
    uni = mask.astype(np.float32); uni = uni / np.maximum(uni.sum(axis=1), 1)[:, None]
    for t in range(n_dates):
        # a held name whose score is missing or which is unbuyable today (limit-up open) is kept at the keep level:
        # the buyable mask restricts purchases, not holdings (same convention as the tranche scheme)
        p = np.where(valid[t], pct[t], np.where(held, keep_lvl, -1.0))
        stay = held & ((p >= keep_lvl) | (age < min_hold))
        new = (~held) & (p >= enter_lvl)
        held = stay | new
        age = np.where(stay, age + 1, np.where(new, 1, 0))
        w = held.astype(np.float64); s = w.sum()
        w = w / s if s > 0 else w
        port[t] = (w * lab[t]).sum(); bench[t] = (uni[t] * lab[t]).sum()
        turn[t] = 0.5 * np.abs(w - prev_w).sum(); cnt[t] = s
        prev_w = w
    return {"portfolio": pd.Series(port, index=dates), "benchmark": pd.Series(bench, index=dates), "excess": pd.Series(port - bench, index=dates),
            "turnover": pd.Series(turn, index=dates), "coverage": pd.Series(cnt, index=dates),
            "live": pd.Series((cnt > 0) & (mask.sum(axis=1) > 0), index=dates)}


def cap_neutral_pct(pred: pd.DataFrame, panel, m: pd.DataFrame, n_buckets=5) -> pd.DataFrame:
    """Percentile of the score inside its market-cap bucket (buckets by float-cap rank within the buyable universe),
    so that the top decile holds the same number of names from every cap segment."""
    cap = panel["log_cap"].where(m).rank(axis=1, pct=True).reindex(index=pred.index, columns=pred.columns)
    cap_np = cap.to_numpy(np.float32)
    bucket = np.where(np.isfinite(cap_np), np.clip(np.ceil(np.nan_to_num(cap_np) * n_buckets), 1, n_buckets), 0).astype(int)
    m = m.reindex(index=pred.index, columns=pred.columns).fillna(False).astype(bool)
    out = pd.DataFrame(np.nan, index=pred.index, columns=pred.columns, dtype=np.float32)
    for q in range(1, n_buckets + 1):
        mq = m & pd.DataFrame(bucket == q, index=pred.index, columns=pred.columns)
        out = out.where(~mq, pred.where(mq).rank(axis=1, pct=True).astype(np.float32))
    return out


def score_pct(pred: pd.DataFrame, panel, m: pd.DataFrame, smooth=0, cap_neutral=False) -> np.ndarray:
    p2 = pred.where(m)
    if smooth:
        p2 = p2.rank(axis=1, pct=True).ewm(span=smooth, adjust=False, min_periods=1).mean()
    if cap_neutral:
        return cap_neutral_pct(p2, panel, m).to_numpy(np.float32)
    return p2.where(m).rank(axis=1, pct=True).to_numpy(np.float32)


def grid_search(panel, ev, pred: pd.DataFrame, years, cost=0.002, top_fracs=(0.05, 0.1), bands=(0.0, 0.05, 0.1, 0.2), holds=(1, 5, 10, 20),
                smooths=(0, 5), cap_neutral=False, log=_log) -> pd.DataFrame:
    from .oos import buyable_mask
    label1 = panel["label_1"].to_numpy(np.float64)
    m = buyable_mask(panel, trading_mask(panel)); mnp = m.to_numpy(bool)
    rows = []
    for smooth in smooths:
        pct = score_pct(pred, panel, m, smooth=smooth, cap_neutral=cap_neutral)
        for tf in top_fracs:
            for hold in holds:
                s = pf.portfolio_series(pct, label1, mnp, panel.dates, top_frac=tf, hold=hold)
                sm = pf.summarize(s, tuple(years), cost_rate=cost)
                rows.append({"scheme": "tranche", "cap_neutral": cap_neutral, "smooth": smooth, "top_frac": tf, "hold": hold, "band": None, **{k: sm[k] for k in ("gross_excess_ann", "gross_sharpe", "net_excess_ann", "net_sharpe", "daily_turnover", "max_drawdown_net", "years_net_positive")},
                             "worst_year_net": float(np.nanmin([v["net_excess"] for v in sm["annual"].values()]))})
            for band in bands:
                for mh in (1, 5):
                    s = buffer_band_series(pct, label1, mnp, panel.dates, top_frac=tf, band=band, min_hold=mh)
                    sm = pf.summarize(s, tuple(years), cost_rate=cost)
                    rows.append({"scheme": "buffer", "cap_neutral": cap_neutral, "smooth": smooth, "top_frac": tf, "hold": mh, "band": band, **{k: sm[k] for k in ("gross_excess_ann", "gross_sharpe", "net_excess_ann", "net_sharpe", "daily_turnover", "max_drawdown_net", "years_net_positive")},
                                 "worst_year_net": float(np.nanmin([v["net_excess"] for v in sm["annual"].values()]))})
            log(f"  smooth {smooth} top {tf}: done")
    return pd.DataFrame(rows)


def cmd_grid(a):
    store = Store(a.root); proto = store.protocol()
    panel = Panel(a.panel_dir or proto["panel_dir"]); panel._dtype = np.float32
    years = tuple(a.years) if a.years else tuple(proto["target_years"])
    ev = Evaluator(panel, universe="all_a", label="label_5", min_stocks=proto["min_stocks"], target_years=years)
    pred = pd.read_parquet(a.prediction); pred.index = pd.DatetimeIndex(pred.index)
    frames = [grid_search(panel, ev, pred, years, cost=a.cost, cap_neutral=False)]
    if a.cap_neutral:
        frames.append(grid_search(panel, ev, pred, years, cost=a.cost, cap_neutral=True))
    df = pd.concat(frames, ignore_index=True).sort_values("net_sharpe", ascending=False)
    out = a.out or (os.path.splitext(a.prediction)[0] + f"_grid_cost{a.cost}.csv")
    df.to_csv(out, index=False, encoding="utf-8-sig")
    print(df.head(20).round(4).to_string(index=False)); print(out)


# ----------------------------------------------------------------------------- multi-horizon training label
def blended_label(panel, horizons=(5, 10, 20), purge_last=21, mask=None) -> pd.DataFrame:
    """Per-date percentile ranks of several horizon labels (within the eligibility mask) averaged; the last
    `purge_last` signal dates of each calendar year are blanked so that no training label window reaches into the
    following (test) year."""
    dates = panel.dates
    if mask is None:
        mask = panel.mask("all_a")
    acc = None
    for h in horizons:
        r = panel[f"label_{h}"].where(mask).rank(axis=1, pct=True)
        acc = r if acc is None else acc + r
    out = acc / len(horizons)
    year = pd.Series(dates.year, index=dates)
    purge = pd.Series(False, index=dates)
    if purge_last > 0:
        for y, idx in year.groupby(year).groups.items():
            purge.loc[idx[-purge_last:]] = True
    out.loc[purge.values] = np.nan
    return out


# ----------------------------------------------------------------------------- training-period long statistics per member
def cmd_train_long(a):
    from .credibility import rank_path_any, eligible_candidates
    store = Store(a.root); proto = store.protocol()
    panel = Panel(proto["panel_dir"]); panel._dtype = np.float32
    ev = Evaluator(panel, universe="all_a", label="label_5", min_stocks=proto["min_stocks"], train_years=proto["train_years"], target_years=proto["target_years"])
    from .oos import buyable_mask
    label1 = panel["label_1"].to_numpy(np.float64); mnp = buyable_mask(panel, trading_mask(panel)).to_numpy(bool)
    cands = [c for c in eligible_candidates(store, a.min_t) if rank_path_any(store, c["id"]) is not None]
    rows = []
    for i, c in enumerate(cands):
        with open(rank_path_any(store, c["id"]), "rb") as fh:
            pct = np.load(fh).astype(np.float32)
        s = pf.portfolio_series(pct, label1, mnp, panel.dates, top_frac=0.1, hold=a.hold)
        tr = pf.summarize(s, tuple(proto["train_years"]), cost_rate=a.cost)
        rows.append({"id": c["id"], "name": c.get("name"), "source": c.get("source"), "train_t": c["_train_t"], "train_long_net_ann": tr["net_excess_ann"],
                     "train_long_net_sharpe": tr["net_sharpe"], "train_long_gross_ann": tr["gross_excess_ann"], "train_turnover": tr["daily_turnover"]})
        del pct
        if (i + 1) % 100 == 0:
            _log(f"train-long {i + 1}/{len(cands)}")
    df = pd.DataFrame(rows)
    out = os.path.join(store.root, "batches", f"train_long_hold{a.hold}_cost{a.cost}.csv")
    df.to_csv(out, index=False, encoding="utf-8-sig"); print(out); print(df.sort_values("train_long_net_sharpe", ascending=False).head(15).round(3).to_string(index=False))


# ----------------------------------------------------------------------------- freeze + replay
def cmd_freeze(a):
    """Write the frozen daily scores (per-date percentile within the buyable universe) and the trading rule."""
    store = Store(a.root); proto = store.protocol()
    panel = Panel(a.panel_dir or proto["panel_dir"]); panel._dtype = np.float32
    ev = Evaluator(panel, universe="all_a", label="label_5", min_stocks=proto["min_stocks"], target_years=tuple(a.years) if a.years else tuple(proto["target_years"]))
    pred = pd.read_parquet(a.prediction); pred.index = pd.DatetimeIndex(pred.index)
    rule = json.loads(a.rule)
    os.makedirs(a.out, exist_ok=True)
    from .oos import buyable_mask
    m = buyable_mask(panel, trading_mask(panel))
    pct = pd.DataFrame(score_pct(pred, panel, m, smooth=rule.get("smooth", 0), cap_neutral=bool(rule.get("cap_neutral", False))), index=pred.index, columns=pred.columns)
    keep = pct.notna().any(axis=1)
    pct = pct[keep]
    pct.astype(np.float32).to_parquet(os.path.join(a.out, "scores.parquet"))
    # the benchmark universe (buyable, eligible) is frozen next to the scores so that a replay needs no research code
    m.loc[keep].astype(np.uint8).to_parquet(os.path.join(a.out, "universe.parquet"))
    long_form = pct.stack().rename("score_pct").reset_index(); long_form.columns = ["date", "code", "score_pct"]
    long_form.to_csv(os.path.join(a.out, "scores_long.csv.gz"), index=False, compression="gzip")
    rule_doc = dict(rule, prediction=a.prediction, members_source=a.members_note, frozen=time.strftime("%Y-%m-%dT%H:%M:%S"),
                    universe="all A, non-ST, non-delisting, listed >= 120 sessions, positive amount; names whose next open is limit-up are unbuyable and excluded from ranking",
                    timing="scores computed after close of date t; enter at open of t+1; label_1 = open[t+2]/open[t+1]-1",
                    benchmark="equal-weight buyable universe, same timing", cost="one-way rate x daily turnover (fraction of portfolio replaced)")
    with open(os.path.join(a.out, "rule.json"), "w", encoding="utf-8") as fh:
        json.dump(rule_doc, fh, ensure_ascii=False, indent=1)
    print(os.path.join(a.out, "rule.json"), pct.shape)


def replay(panel_dir: str, scores_path: str, rule_path: str, years, cost=None) -> dict:
    """Independent replay: needs only the frozen scores, the rule and the price panel (open prices + eligibility)."""
    panel = Panel(panel_dir); panel._dtype = np.float32
    with open(rule_path, encoding="utf-8") as fh:
        rule = json.load(fh)
    sc = pd.read_parquet(scores_path); sc.index = pd.DatetimeIndex(sc.index)
    sc = sc.reindex(index=panel.dates, columns=panel.codes)
    opn = panel["open"]
    label1 = (opn.shift(-2) / opn.shift(-1) - 1).to_numpy(np.float64)
    upath = os.path.join(os.path.dirname(scores_path), "universe.parquet")
    if os.path.exists(upath):
        uni = pd.read_parquet(upath); uni.index = pd.DatetimeIndex(uni.index)
        mask = uni.reindex(index=panel.dates, columns=panel.codes).fillna(0).to_numpy() > 0
    else:
        mask = sc.notna().to_numpy(bool)
    pct = sc.to_numpy(np.float32)
    cost = rule.get("cost_one_way", 0.002) if cost is None else cost
    if rule.get("scheme", "tranche") == "buffer":
        s = buffer_band_series(pct, label1, mask, panel.dates, top_frac=rule["top_frac"], band=rule.get("band", 0.0), min_hold=rule.get("hold", 1))
    else:
        s = pf.portfolio_series(pct, label1, mask, panel.dates, top_frac=rule["top_frac"], hold=rule.get("hold", 10))
    return pf.summarize(s, tuple(years), cost_rate=cost)


def cmd_replay(a):
    r = replay(a.panel_dir, a.scores, a.rule, tuple(a.years), cost=a.cost)
    print(json.dumps({k: v for k, v in r.items() if k != "annual"}, ensure_ascii=False, indent=1))
    print({y: round(v["net_excess"], 4) for y, v in r["annual"].items()})
    if a.out:
        with open(a.out, "w", encoding="utf-8") as fh:
            json.dump(r, fh, ensure_ascii=False, indent=1)


def main(argv=None):
    ap = argparse.ArgumentParser(); sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("grid"); s.add_argument("--root", required=True); s.add_argument("--prediction", required=True); s.add_argument("--panel-dir", default=None)
    s.add_argument("--years", nargs="*", type=int, default=None); s.add_argument("--cost", type=float, default=0.002); s.add_argument("--out", default=None); s.add_argument("--cap-neutral", action="store_true", help="also evaluate scores ranked within market-cap quintiles")
    s = sub.add_parser("train-long"); s.add_argument("--root", required=True); s.add_argument("--min-t", type=float, default=2.0); s.add_argument("--hold", type=int, default=10); s.add_argument("--cost", type=float, default=0.002)
    s = sub.add_parser("freeze"); s.add_argument("--root", required=True); s.add_argument("--prediction", required=True); s.add_argument("--panel-dir", default=None)
    s.add_argument("--years", nargs="*", type=int, default=None); s.add_argument("--rule", required=True); s.add_argument("--out", required=True); s.add_argument("--members-note", default="")
    s = sub.add_parser("replay"); s.add_argument("--panel-dir", required=True); s.add_argument("--scores", required=True); s.add_argument("--rule", required=True)
    s.add_argument("--years", nargs="*", type=int, default=[2019, 2020, 2021, 2022, 2023, 2024]); s.add_argument("--cost", type=float, default=None); s.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    {"grid": cmd_grid, "train-long": cmd_train_long, "freeze": cmd_freeze, "replay": cmd_replay}[a.cmd](a)


if __name__ == "__main__":
    main()
