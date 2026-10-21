"""Credibility checks for the A layer (A13 step 1).

1. Training-only member reselection (selection-inflation estimate). Members are chosen using nothing after
   2018: direction and |t| on 2016-2018 daily RankIC, stock coverage on training dates, redundancy on
   training-date rank signatures (greedy in |t| order). The same LightGBM combination (rank target,
   expanding annual retrain) is then retrained and its 2019-2024 numbers compared with the exposed
   selection (pool admitted on 2019-2024 mean RankIC / long net Sharpe).
2. 2026 attribution: decompose the Jan-May 2026 long-side failure by market-cap segment, style tilt of the
   top decile, benchmark and style-return paths, deciles and months, against 2025 and 2019-2024.

python -m quanta_agents.factor_lab_a.credibility recompute|select|combine|attribute ...
"""
from __future__ import annotations
import argparse
import json
import os
import time
import numpy as np
import pandas as pd

from .panel import Panel
from .evaluate import Evaluator, rank_ic, annual_table, _quantiles_from_pct, _row_corr_np
from .ledger import Store
from .combine import rolling_combination, LazyRanks
from . import portfolio as pf


def _log(msg):
    print(time.strftime("%H:%M:%S"), msg, flush=True)


# ----------------------------------------------------------------------------- rank arrays for non-pool candidates
def rank_path_any(store: Store, cid: str) -> str | None:
    p = store.rank_path(cid)
    if os.path.exists(p):
        return p
    q = os.path.join(store.root, "cand_ranks", f"{cid}.npy")
    return q if os.path.exists(q) else None


def eligible_candidates(store: Store, min_abs_t=2.0) -> list[dict]:
    """Evaluated candidates with a training direction and |train t| >= min_abs_t (training-period facts only)."""
    out = []
    for c in store.candidates():
        r = store.load_result(c["id"])
        if not r or r.get("status") != "ok" or not r.get("direction") or r.get("train_t") is None:
            continue
        if abs(r["train_t"]) < min_abs_t:
            continue
        out.append(dict(c, _train_t=float(r["train_t"]), _direction=int(r["direction"])))
    return out


def cmd_recompute(a):
    store = Store(a.root); proto = store.protocol()
    os.makedirs(os.path.join(a.root, "cand_ranks"), exist_ok=True)
    cands = eligible_candidates(store, a.min_t)
    todo = [c for c in cands if rank_path_any(store, c["id"]) is None]
    _log(f"{len(cands)} candidates with |train t|>={a.min_t}; {len(todo)} need rank arrays")
    if not todo:
        return
    from .parallel import run_parallel
    import shutil
    directions = {c["id"]: c["_direction"] for c in todo}
    failed = []; n = 0; t0 = time.time()
    for w in run_parallel(todo, a.root, proto["panel_dir"], tuple(proto["target_years"]), tuple(proto["train_years"]), workers=a.workers, full=False, directions=directions, log=_log):
        n += 1
        if "error" in w or not w.get("rank_path"):
            failed.append({"id": w["id"], "name": w.get("name"), "error": str(w.get("error", "no ranks"))[:160]})
        else:
            shutil.move(w["rank_path"], os.path.join(a.root, "cand_ranks", f"{w['id']}.npy"))
            if w.get("sig_path") and os.path.exists(w["sig_path"]):
                os.remove(w["sig_path"])
        if n % 25 == 0:
            _log(f"recomputed {n}/{len(todo)} ({time.time() - t0:.0f}s), failed {len(failed)}")
    _log(f"done {n}, failed {len(failed)}")
    with open(os.path.join(a.root, "batches", "cand_ranks_failed.json"), "w", encoding="utf-8") as fh:
        json.dump(failed, fh, ensure_ascii=False, indent=1)


# ----------------------------------------------------------------------------- training-only selection
# Step-1 "clean" baseline: the seven daily sources whose generation used no 2019-2024 information
CLEAN_SOURCES_STEP1 = ("classic", "alpha158", "legacy", "alpha101", "alpha191", "calendar", "gp")
# Step-2 "clean" set: the same plus the intraday base set and GP over intraday fields
CLEAN_SOURCES = CLEAN_SOURCES_STEP1 + ("minute", "gp_minute")
# sources whose *generation* saw exposed numbers (LLM reviews, expand/crossover parents chosen from the exposed pool)
EXPOSED_GENERATION = ("llm", "expand", "crossover")


def _train_rows(ev: Evaluator, step=10):
    yrs = ev.years
    pos = np.arange(len(ev.dates))
    return pos[np.isin(yrs, ev.train_years)][::step]


def _zsig(arr16: np.ndarray, rows: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Per-row z-scored signature with NaN -> 0 (rows = training sample dates). Returns (z, finite count per row)."""
    a = np.asarray(arr16[rows], dtype=np.float32)
    m = np.isfinite(a)
    n = m.sum(axis=1)
    with np.errstate(all="ignore"):
        mu = np.where(m, a, 0).sum(axis=1) / np.maximum(n, 1)
        c = np.where(m, a - mu[:, None], 0.0)
        sd = np.sqrt((c ** 2).sum(axis=1) / np.maximum(n, 1))
        z = np.where(m, c / np.maximum(sd, 1e-9)[:, None], 0.0)
    return z.astype(np.float32), n


def training_stats(store: Store, ev: Evaluator, ids: list[str]) -> tuple[pd.DataFrame, np.ndarray]:
    """Training-date coverage and z-signatures for every id (loaded one array at a time)."""
    rows = _train_rows(ev)
    ucount = np.maximum(ev._universe_count[rows], 1)
    Z = np.empty((len(ids), len(rows) * len(ev.panel.codes)), np.float32)
    cov = np.empty(len(ids))
    nrow = np.empty((len(ids), len(rows)), np.float32)
    for i, cid in enumerate(ids):
        with open(rank_path_any(store, cid), "rb") as fh:
            arr = np.load(fh)
        z, n = _zsig(arr, rows)
        Z[i] = z.ravel(); nrow[i] = n
        cov[i] = float(np.median(n / ucount))
        del arr
        if (i + 1) % 100 == 0:
            _log(f"signatures {i + 1}/{len(ids)}")
    # average signed correlation over training dates: sum of products over common finite cells divided by the
    # exact common finite count (Z is overwritten in place by its finite indicator to get that count)
    G = Z @ Z.T
    np.not_equal(Z, 0.0, out=Z)
    N = Z @ Z.T
    with np.errstate(all="ignore"):
        C = G / np.maximum(N, 1)
    del Z, G, N
    return pd.DataFrame({"id": ids, "train_cov": cov}), C


def greedy_select(order_ids: list[str], C: np.ndarray, idx: dict[str, int], max_corr: float, cap: int | None = None) -> list[str]:
    chosen = []
    for cid in order_ids:
        if cap and len(chosen) >= cap:
            break
        i = idx[cid]
        if chosen and np.max(np.abs(C[i, [idx[c] for c in chosen]])) >= max_corr:
            continue
        chosen.append(cid)
    return chosen


def cmd_select(a):
    store = Store(a.root); proto = store.protocol()
    panel = Panel(proto["panel_dir"]); panel._dtype = np.float32
    ev = Evaluator(panel, universe=proto["universe"], label=proto["label"], min_stocks=proto["min_stocks"], train_years=proto["train_years"], target_years=proto["target_years"])
    cands = [c for c in eligible_candidates(store, a.min_t) if rank_path_any(store, c["id"]) is not None]
    if a.sources == "clean":
        cands = [c for c in cands if c.get("source") in CLEAN_SOURCES]
    elif a.sources == "clean_step1":
        cands = [c for c in cands if c.get("source") in CLEAN_SOURCES_STEP1]
    elif a.sources != "all":
        keep = set(a.sources.split(","))
        cands = [c for c in cands if c.get("source") in keep]
    _log(f"{len(cands)} candidates after |t|>={a.min_t} and source filter '{a.sources}'")
    ids = [c["id"] for c in cands]
    stats, C = training_stats(store, ev, ids)
    cov = dict(zip(stats["id"], stats["train_cov"]))
    ok = [c for c in cands if cov[c["id"]] >= a.min_cov]
    ok.sort(key=lambda c: (-abs(c["_train_t"]), c["id"]))  # deterministic tie-break
    idx = {cid: i for i, cid in enumerate(ids)}
    chosen = greedy_select([c["id"] for c in ok], C, idx, a.max_corr, cap=a.cap)
    pool = set(store.pool_ids())
    by_id = {c["id"]: c for c in cands}
    exposed_overlap = len([c for c in chosen if c in pool])
    _log(f"selected {len(chosen)} members (coverage>={a.min_cov}, corr<{a.max_corr}); {exposed_overlap} also in the exposed pool")
    tag = a.tag or f"trainonly_t{a.min_t:g}_c{a.max_corr:g}_{a.sources}"
    out = {"tag": tag, "rule": {"min_abs_train_t": a.min_t, "min_train_coverage": a.min_cov, "max_train_corr": a.max_corr, "sources": a.sources, "cap": a.cap,
                                "order": "descending |train t|", "signature": "per-date z-scored percentile ranks on every 10th training date, signed mean correlation"},
           "n_candidates": len(cands), "candidate_ids": ids, "n_coverage_ok": len(ok), "n_selected": len(chosen), "overlap_with_exposed_pool": exposed_overlap,
           "exposed_pool_size": len(pool), "members": chosen,
           "by_source": pd.Series([by_id[c]["source"] for c in chosen]).value_counts().to_dict()}
    path = os.path.join(a.root, "batches", f"members_{tag}.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1)
    print(json.dumps({k: v for k, v in out.items() if k != "members"}, ensure_ascii=False, indent=1)); print(path)


# ----------------------------------------------------------------------------- combination + long portfolio
def long_report(panel, ev, pred: pd.DataFrame, years, cost=0.002, holds=(1, 5, 10), smooths=(0,), top_frac=0.1) -> dict:
    from .oos import buyable_mask
    assert pred.index.equals(panel.dates) and list(pred.columns) == list(panel.codes), "prediction must be aligned to the panel"
    label1 = panel["label_1"].to_numpy(np.float64)
    m = buyable_mask(panel, panel.mask(ev.universe))  # trading universe: eligibility only, no label purge
    out = {}
    for smooth in smooths:
        p2 = pred.where(m)
        if smooth:
            p2 = p2.rank(axis=1, pct=True).ewm(span=smooth, adjust=False, min_periods=1).mean()
        pct = p2.where(m).rank(axis=1, pct=True).to_numpy(np.float32)
        for hold in holds:
            s = pf.portfolio_series(pct, label1, m.to_numpy(bool), panel.dates, top_frac=top_frac, hold=hold)
            for c in sorted({0.001, cost}):
                out[f"smooth{smooth}_hold{hold}_cost{c}"] = pf.summarize(s, tuple(years), cost_rate=c)
    return out


def combine_members(root: str, members: list[str], tag: str, day_step=4, seed=0, cost=0.002, model="lgbm", target="rank", params=None, n_seeds=1, lowmem=True,
                    label_blend=None, smooth_lambda=0.0) -> dict:
    store = Store(root); proto = store.protocol()
    panel = Panel(proto["panel_dir"]); panel._dtype = np.float32
    ev = Evaluator(panel, universe=proto["universe"], label=proto["label"], min_stocks=proto["min_stocks"], train_years=proto["train_years"], target_years=proto["target_years"])
    ranks = {}
    for m in members:
        p = rank_path_any(store, m)
        if p is None:
            raise SystemExit(f"missing ranks for {m}")
        ranks[m] = LazyRanks(p)
    _log(f"combination {model}/{target}/expanding n={len(members)} day_step={day_step} tag={tag} lowmem={lowmem}")
    if lowmem and model == "lgbm":
        from .combine import rolling_combination_lowmem
        label_override = None
        if label_blend:
            from .trading import blended_label
            label_override = blended_label(panel, horizons=tuple(label_blend))
        res, pred = rolling_combination_lowmem(panel, ev, ranks, members, target=target, train_years=tuple(proto["train_years"]), target_years=tuple(proto["target_years"]),
                                               day_step=day_step, params=params, seed=seed, n_seeds=n_seeds, workdir=os.path.join(root, "batches", f"tmp_lowmem_{tag}"), log=_log,
                                               label_override=label_override, smooth_lambda=smooth_lambda)
        res["label_blend"] = list(label_blend) if label_blend else None
    else:
        res, pred = rolling_combination(panel, ev, ranks, members, model=model, target=target, update="expanding", train_years=tuple(proto["train_years"]),
                                        target_years=tuple(proto["target_years"]), day_step=day_step, params=params, seed=seed, n_seeds=n_seeds)
    ppath = os.path.join(root, "batches", f"pred_{tag}.parquet")
    pred.astype(np.float32).to_parquet(ppath)
    res["prediction_path"] = ppath; res["tag"] = tag; res["members_file"] = tag
    res["long"] = long_report(panel, ev, pred, tuple(proto["target_years"]), cost=cost)
    ar = res["annual_rank_ic"]
    _log("  IC mean %.4f worst %.4f | %s" % (res["target_mean_rank_ic"], res["target_worst_rank_ic"], " ".join(f"{y}:{ar[str(y)]['mean']:+.3f}" for y in proto["target_years"])))
    for k, v in res["long"].items():
        _log("  %-26s gross %+.3f sh %.2f | net %+.3f sh %.2f | turn %.3f | %s" % (k, v["gross_excess_ann"], v["gross_sharpe"], v["net_excess_ann"], v["net_sharpe"], v["daily_turnover"],
                                                                             " ".join(f"{y}:{v['annual'][str(y)]['net_excess']:+.3f}" for y in proto["target_years"])))
    with open(os.path.join(root, "batches", f"combinations_{tag}.json"), "w", encoding="utf-8") as fh:
        json.dump([res], fh, ensure_ascii=False, indent=1, default=float)
    return res


def cmd_combine(a):
    with open(a.members_file, encoding="utf-8") as fh:
        d = json.load(fh)
    if isinstance(d, dict):
        members = d["members"]
    elif d and isinstance(d[0], dict) and "features" in d[0]:
        members = d[a.index]["features"]
    else:
        members = d
    if a.top:
        members = members[: a.top]
    tag = a.tag or (d.get("tag") if isinstance(d, dict) else os.path.basename(a.members_file)[:-5])
    if a.top:
        tag = f"{tag}_top{a.top}"
    combine_members(a.root, members, tag, day_step=a.day_step, seed=a.seed, cost=a.cost, params=json.loads(a.params) if a.params else None, n_seeds=a.seeds, lowmem=not a.classic,
                    label_blend=tuple(a.label_blend) if a.label_blend else None, smooth_lambda=a.smooth_lambda)


# ----------------------------------------------------------------------------- 2026 attribution
def _ew(ret: np.ndarray, mask: np.ndarray) -> np.ndarray:
    w = mask.astype(np.float32); w /= np.maximum(w.sum(axis=1), 1)[:, None]
    return (w * np.where(np.isfinite(ret), ret, 0.0)).sum(axis=1)


def _ann(x: pd.Series) -> float:
    return float(x.mean() * 252)


def _sharpe(x: pd.Series) -> float:
    sd = x.std(ddof=1)
    return float(x.mean() / sd * np.sqrt(252)) if sd > 0 else float("nan")


def _nw_ols(y: np.ndarray, X: np.ndarray, lag: int) -> tuple[np.ndarray, np.ndarray]:
    """OLS coefficients with Newey-West (Bartlett, `lag` lags) standard errors; X includes the intercept column."""
    n, k = X.shape
    XtX_inv = np.linalg.pinv(X.T @ X)
    beta = XtX_inv @ X.T @ y
    u = y - X @ beta
    S = (X * u[:, None]).T @ (X * u[:, None])
    for L in range(1, lag + 1):
        w = 1.0 - L / (lag + 1.0)
        G = (X[L:] * u[L:, None]).T @ (X[:-L] * u[:-L, None])
        S += w * (G + G.T)
    cov = XtX_inv @ S @ XtX_inv
    return beta, np.sqrt(np.maximum(np.diag(cov), 0))


def attribute_period(panel, pred: pd.DataFrame, years: tuple, min_stocks=100, cost=0.002, hold=10) -> dict:
    from .oos import buyable_mask
    assert pred.index.equals(panel.dates) and list(pred.columns) == list(panel.codes), "prediction must be aligned to the panel"
    ev = Evaluator(panel, universe="all_a", label="label_5", min_stocks=min_stocks, target_years=years)
    dates = panel.dates; yrs = np.array(dates.year)
    in_p = np.isin(yrs, years)
    mask = ev.mask                       # IC evaluation mask (eligibility + label purge)
    tmask = panel.mask("all_a")          # trading universe: eligibility only
    m = buyable_mask(panel, tmask)       # buyable: next open not limit-up
    mnp = m.to_numpy(bool); tnp = tmask.to_numpy(bool)
    label1 = panel["label_1"].to_numpy(np.float64)
    label5 = panel["label_5"]
    pct = pred.where(m).rank(axis=1, pct=True)
    pct_np = pct.to_numpy(np.float32)
    # styles (ranked within the eligible universe); factor legs are formed on the eligibility mask, not on tradability
    styles = {"size": panel["log_cap"], "turnover20": panel["turnover"].rolling(20).mean(), "vol20": panel["ret"].rolling(20).std(),
              "mom20": panel["close"].pct_change(20, fill_method=None)}
    srank = {k: v.where(tmask).rank(axis=1, pct=True) for k, v in styles.items()}
    has_pred = np.isfinite(pct_np).any(axis=1)
    out = {"years": list(years), "calendar_days": int(in_p.sum()), "signal_days": int((in_p & has_pred).sum())}
    # 1. benchmark & style factor returns (equal-weight, entry next open, label_1 timing)
    uni = pd.Series(_ew(label1, mnp), index=dates)
    out["universe_ew_ann"] = _ann(uni[in_p]); out["universe_ew_cum"] = float((1 + uni[in_p]).prod() - 1)
    style_ret = {}
    for k, r in srank.items():
        rn = r.to_numpy(np.float32)
        hi = tnp & (rn > 0.8); lo = tnp & (rn <= 0.2)
        s = pd.Series(_ew(label1, hi) - _ew(label1, lo), index=dates)
        style_ret[k] = s
        out[f"style_{k}_hml_ann"] = _ann(s[in_p]); out[f"style_{k}_hml_sharpe"] = _sharpe(s[in_p])
    # 2. top-decile tilt and cap composition
    top = np.isfinite(pct_np) & (pct_np >= 0.9)
    tilt = {}
    for k, r in srank.items():
        rn = r.to_numpy(np.float32)
        v = np.where(top & np.isfinite(rn), rn, np.nan)
        tilt[k] = float(np.nanmean(np.nanmean(v[in_p], axis=1)) - 0.5)
    out["top_decile_style_tilt"] = tilt
    cap = srank["size"].to_numpy(np.float32)
    bucket = np.where(np.isfinite(cap), np.clip(np.ceil(np.nan_to_num(cap) * 5), 1, 5), 0).astype(int)  # NaN cap -> no bucket
    comp = {}
    days_top = in_p & (top.sum(axis=1) > 0)
    for q in range(1, 6):
        sel = top & (bucket == q)
        comp[f"Q{q}"] = float(np.mean(sel[days_top].sum(axis=1) / np.maximum(top[days_top].sum(axis=1), 1)))
    out["top_decile_cap_composition"] = comp
    # 3. overall long excess (holding-period gradient) and within-cap-quintile long excess (top decile inside each quintile vs quintile EW)
    s_all = pf.portfolio_series(pct_np, label1, mnp, dates, top_frac=0.1, hold=hold)
    sm = pf.summarize(s_all, years, cost_rate=cost)
    out["long_all"] = {k: sm[k] for k in ("gross_excess_ann", "gross_sharpe", "net_excess_ann", "net_sharpe", "daily_turnover", "portfolio_ann", "benchmark_ann")}
    out["long_all"]["gross_excess_cum"] = float(s_all["excess"][in_p].sum())
    grad = {}
    for h in (1, 5, 10, 20):
        sh = pf.portfolio_series(pct_np, label1, mnp, dates, top_frac=0.1, hold=h)
        smh = pf.summarize(sh, years, cost_rate=cost)
        grad[str(h)] = {"gross_excess_ann": smh["gross_excess_ann"], "gross_sharpe": smh["gross_sharpe"], "net_excess_ann": smh["net_excess_ann"], "daily_turnover": smh["daily_turnover"]}
    out["hold_gradient"] = grad
    within = {}
    ic_q = {}
    for q in range(1, 6):
        mq = m & pd.DataFrame(bucket == q, index=dates, columns=panel.codes)
        pq_ = pred.where(mq).rank(axis=1, pct=True).to_numpy(np.float32)
        sq = pf.portfolio_series(pq_, label1, mq.to_numpy(bool), dates, top_frac=0.1, hold=hold)
        smq = pf.summarize(sq, years, cost_rate=cost)
        within[f"Q{q}"] = {"gross_excess_ann": smq["gross_excess_ann"], "gross_sharpe": smq["gross_sharpe"], "quintile_ew_ann": smq["benchmark_ann"]}
        ric, n = rank_ic(pred, label5, mq, min_stocks=50)
        ic_q[f"Q{q}"] = float(ric[in_p].mean())
    out["long_within_cap_quintile"] = within; out["ic_by_cap_quintile"] = ic_q
    # cap-neutral long: top decile within each quintile, all five quintiles pooled equally
    pq_all = np.full(pct_np.shape, np.nan, np.float32)
    for q in range(1, 6):
        mq = m & pd.DataFrame(bucket == q, index=dates, columns=panel.codes)
        pq_all = np.where(mq.to_numpy(bool), pred.where(mq).rank(axis=1, pct=True).to_numpy(np.float32), pq_all)
    s_cn = pf.portfolio_series(pq_all, label1, mnp, dates, top_frac=0.1, hold=hold)
    smc = pf.summarize(s_cn, years, cost_rate=cost)
    out["long_cap_neutral"] = {k: smc[k] for k in ("gross_excess_ann", "gross_sharpe", "net_excess_ann", "net_sharpe", "daily_turnover")}
    # 4. deciles (5-day label, annualised x 252/5), monotonicity and the long side's share of the spread
    lab5 = label5.where(m).to_numpy(np.float64)
    qr = _quantiles_from_pct(pct_np, lab5, dates)
    tq = qr[in_p]
    dec = {str(k): float((tq[k] - tq["universe"]).mean() * 252 / 5) for k in range(1, 11)}
    out["decile_excess_5d_ann"] = dec
    dv = np.array([dec[str(k)] for k in range(1, 11)])
    out["decile_spearman"] = float(pd.Series(dv).corr(pd.Series(np.arange(10)), method="spearman"))
    spread = dv[9] - dv[0]
    out["long_share_of_spread"] = float(dv[9] / spread) if spread > 0 else None
    # 5. IC overall (daily; the 5-day label overlaps, so the t-stat is deflated by sqrt(5))
    ric, n = rank_ic(pred, label5, m, min_stocks)
    rs = ric[in_p].dropna()
    out["rank_ic_mean"] = float(rs.mean()); out["rank_ic_days"] = int(len(rs))
    out["rank_ic_t_overlap_adjusted"] = float(rs.mean() / rs.std(ddof=1) * np.sqrt(len(rs) / 5.0)) if len(rs) > 5 else None
    # 6. regression of daily long excess on contemporaneous style returns (decomposition with in-sample betas;
    #    Newey-West standard errors with `hold` lags because the tranche book overlaps)
    sel_rows = in_p & has_pred
    ex = s_all["excess"][sel_rows]
    Xs = pd.DataFrame({k: v[sel_rows] for k, v in style_ret.items()})
    X = np.column_stack([np.ones(len(ex)), Xs.to_numpy()])
    beta, se = _nw_ols(ex.to_numpy(), X, lag=hold)
    ci = 1.96 * se[0] * 252
    out["excess_on_styles"] = {"alpha_ann": float(beta[0] * 252), "alpha_se_ann": float(se[0] * 252), "alpha_ci95_ann": [float(beta[0] * 252 - ci), float(beta[0] * 252 + ci)],
                               "alpha_t_nw": float(beta[0] / se[0]) if se[0] > 0 else None,
                               "betas": dict(zip(Xs.columns, [float(b) for b in beta[1:]])), "beta_t_nw": dict(zip(Xs.columns, [float(b / e) if e > 0 else None for b, e in zip(beta[1:], se[1:])])),
                               "style_contribution_ann": {k: float(b * Xs[k].mean() * 252) for k, b in zip(Xs.columns, beta[1:])},
                               "style_mean_ann": {k: float(Xs[k].mean() * 252) for k in Xs.columns}, "n_days": int(len(ex))}
    # 7. monthly table
    months = {}
    ex_net = s_all["excess"] - cost * s_all["turnover"]
    for per, g in ex.groupby(ex.index.to_period("M")):
        i = np.isin(dates, g.index)
        months[str(per)] = {"long_gross_excess": float(g.sum()), "long_net_excess": float(ex_net[i].sum()), "universe": float(uni[i].sum()),
                            "smb": float(-style_ret["size"][i].sum()), "hml_turnover": float(style_ret["turnover20"][i].sum()),
                            "hml_vol": float(style_ret["vol20"][i].sum()), "hml_mom": float(style_ret["mom20"][i].sum()),
                            "rank_ic": float(ric[i].mean()), "days": int(i.sum())}
    out["monthly"] = months
    return out


def cmd_attribute(a):
    res = {}
    if a.research_pred:
        panel = Panel(a.research_panel); panel._dtype = np.float32
        pred = pd.read_parquet(a.research_pred); pred.index = pd.DatetimeIndex(pred.index)
        res["2019-2024"] = attribute_period(panel, pred, (2019, 2020, 2021, 2022, 2023, 2024), cost=a.cost, hold=a.hold)
        _log("research period done")
        del panel, pred
    panel = Panel(a.oos_panel); panel._dtype = np.float32
    pred = pd.read_parquet(a.oos_pred); pred.index = pd.DatetimeIndex(pred.index)
    for y in a.years:
        res[str(y)] = attribute_period(panel, pred, (y,), cost=a.cost, hold=a.hold)
        _log(f"{y} done")
    # style-adjusted alpha with FIXED research-period betas (a test rather than an identity): alpha_train_beta =
    # gross excess - sum(beta_research x style mean of the period)
    ref = res.get("2019-2024")
    if ref:
        b_ref = ref["excess_on_styles"]["betas"]
        for k, v in res.items():
            sm_ = v["excess_on_styles"]["style_mean_ann"]
            styled = sum(b_ref[s_] * sm_[s_] for s_ in b_ref)
            v["excess_on_styles"]["alpha_with_research_betas_ann"] = float(v["long_all"]["gross_excess_ann"] - styled)
            v["excess_on_styles"]["style_driven_with_research_betas_ann"] = float(styled)
    with open(a.out, "w", encoding="utf-8") as fh:
        json.dump(res, fh, ensure_ascii=False, indent=1, default=float)
    for k, v in res.items():
        print(f"== {k}: signal days {v['signal_days']} (IC days {v['rank_ic_days']}) IC {v['rank_ic_mean']:.4f} t_adj {v['rank_ic_t_overlap_adjusted']:.2f} | universe EW ann {v['universe_ew_ann']:+.3f} | long gross {v['long_all']['gross_excess_ann']:+.3f} (cum {v['long_all']['gross_excess_cum']:+.3f}) net {v['long_all']['net_excess_ann']:+.3f} | cap-neutral gross {v['long_cap_neutral']['gross_excess_ann']:+.3f} net {v['long_cap_neutral']['net_excess_ann']:+.3f}")
        print("   hold gradient gross:", {h: round(x["gross_excess_ann"], 3) for h, x in v["hold_gradient"].items()}, "| decile spearman", round(v["decile_spearman"], 2), "long share of spread", None if v["long_share_of_spread"] is None else round(v["long_share_of_spread"], 2))
        eo = v["excess_on_styles"]; print("   alpha (NW) %+.3f se %.3f ci [%+.3f, %+.3f] t %.2f | alpha with research betas %+.3f (style-driven %+.3f)" % (eo["alpha_ann"], eo["alpha_se_ann"], eo["alpha_ci95_ann"][0], eo["alpha_ci95_ann"][1], eo["alpha_t_nw"] or 0, eo.get("alpha_with_research_betas_ann", float("nan")), eo.get("style_driven_with_research_betas_ann", float("nan"))))
        print("   style HML ann:", {s: round(v[f'style_{s}_hml_ann'], 3) for s in ('size', 'turnover20', 'vol20', 'mom20')}, "| top tilt:", {s: round(t, 3) for s, t in v["top_decile_style_tilt"].items()})
        print("   cap comp:", {q: round(x, 3) for q, x in v["top_decile_cap_composition"].items()})
        print("   within-quintile long gross:", {q: round(x["gross_excess_ann"], 3) for q, x in v["long_within_cap_quintile"].items()}, "| IC by quintile:", {q: round(x, 3) for q, x in v["ic_by_cap_quintile"].items()})
        print("   deciles:", {k: round(x, 3) for k, x in v["decile_excess_5d_ann"].items()})
        print("   betas", {k: round(b, 2) for k, b in v["excess_on_styles"]["betas"].items()}, "contrib", {k: round(b, 3) for k, b in v["excess_on_styles"]["style_contribution_ann"].items()})
        if len(v["monthly"]) <= 12:
            for mth, row in v["monthly"].items():
                print(f"   {mth}: long gross {row['long_gross_excess']:+.3f} net {row['long_net_excess']:+.3f} | universe {row['universe']:+.3f} smb {row['smb']:+.3f} turn {row['hml_turnover']:+.3f} vol {row['hml_vol']:+.3f} mom {row['hml_mom']:+.3f} | IC {row['rank_ic']:+.3f} ({row['days']}d)")


# ----------------------------------------------------------------------------- effective number of independent directions
def effective_directions(C: np.ndarray) -> dict:
    """Spectrum summaries of a correlation matrix: participation ratio, Kaiser count (eigenvalues > 1),
    components needed for 80/90/95% of variance."""
    C = np.nan_to_num(C, nan=0.0)
    C = 0.5 * (C + C.T); np.fill_diagonal(C, 1.0)
    w = np.sort(np.linalg.eigvalsh(C))[::-1]
    w = np.clip(w, 0, None)
    cum = np.cumsum(w) / max(w.sum(), 1e-12)
    return {"n": int(len(w)), "participation_ratio": float(w.sum() ** 2 / (w ** 2).sum()), "kaiser_gt1": int((w > 1).sum()),
            "n_for_80pct": int(np.searchsorted(cum, 0.80) + 1), "n_for_90pct": int(np.searchsorted(cum, 0.90) + 1), "n_for_95pct": int(np.searchsorted(cum, 0.95) + 1),
            "top_eigenvalues": [float(x) for x in w[:10]]}


def cmd_directions(a):
    store = Store(a.root); proto = store.protocol()
    panel = Panel(proto["panel_dir"]); panel._dtype = np.float32
    ev = Evaluator(panel, universe=proto["universe"], label=proto["label"], min_stocks=proto["min_stocks"], train_years=proto["train_years"], target_years=proto["target_years"])
    if a.members_file:
        with open(a.members_file, encoding="utf-8") as fh:
            d = json.load(fh)
        ids = d["members"] if isinstance(d, dict) else (d[0]["features"] if isinstance(d, list) and d and isinstance(d[0], dict) and "features" in d[0] else d)
    else:
        ids = store.pool_ids()
    ids = [i for i in ids if rank_path_any(store, i) is not None]
    cands = {c["id"]: c for c in store.candidates()}
    if a.exclude_sources:
        ex = set(a.exclude_sources.split(","))
        ids = [i for i in ids if cands.get(i, {}).get("source") not in ex]
    if a.only_sources:
        keep = set(a.only_sources.split(","))
        ids = [i for i in ids if cands.get(i, {}).get("source") in keep]
    if a.rows == "all":
        ev.train_years = tuple(proto["train_years"]) + tuple(proto["target_years"])  # _train_rows samples every 10th date of these years
    _, C = training_stats(store, ev, ids)
    out = effective_directions(C); out["rows"] = a.rows; out["sources"] = pd.Series([cands.get(i, {}).get("source") for i in ids]).value_counts().to_dict()
    print(json.dumps(out, ensure_ascii=False, indent=1))
    if a.out:
        with open(a.out, "w", encoding="utf-8") as fh:
            json.dump(out, fh, ensure_ascii=False, indent=1)


def main(argv=None):
    ap = argparse.ArgumentParser(); sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("recompute"); s.add_argument("--root", required=True); s.add_argument("--min-t", type=float, default=2.0); s.add_argument("--workers", type=int, default=6)
    s = sub.add_parser("select"); s.add_argument("--root", required=True); s.add_argument("--min-t", type=float, default=4.0); s.add_argument("--max-corr", type=float, default=0.9)
    s.add_argument("--min-cov", type=float, default=0.8); s.add_argument("--sources", default="all"); s.add_argument("--cap", type=int, default=None); s.add_argument("--tag", default=None)
    s = sub.add_parser("combine"); s.add_argument("--root", required=True); s.add_argument("--members-file", required=True); s.add_argument("--tag", default=None)
    s.add_argument("--day-step", type=int, default=4); s.add_argument("--seed", type=int, default=0); s.add_argument("--cost", type=float, default=0.002); s.add_argument("--top", type=int, default=None)
    s.add_argument("--params", default=None); s.add_argument("--seeds", type=int, default=1); s.add_argument("--classic", action="store_true", help="use the original in-memory path"); s.add_argument("--index", type=int, default=0)
    s.add_argument("--label-blend", nargs="*", type=int, default=None, help="horizons whose per-date label ranks are averaged as the training target, e.g. 5 10 20")
    s.add_argument("--smooth-lambda", type=float, default=0.0, help="turnover penalty weight on prediction changes between consecutive sampled dates")
    s = sub.add_parser("attribute"); s.add_argument("--research-panel", default=r"F:/A_Layer_Research/panel"); s.add_argument("--research-pred", default=None)
    s.add_argument("--oos-panel", default=r"F:/A_Layer_OOS/panel"); s.add_argument("--oos-pred", default=r"F:/A_Layer_OOS/v11/prediction_oos.parquet")
    s.add_argument("--years", nargs="*", type=int, default=[2025, 2026]); s.add_argument("--cost", type=float, default=0.002); s.add_argument("--hold", type=int, default=10); s.add_argument("--out", required=True)
    s = sub.add_parser("directions"); s.add_argument("--root", required=True); s.add_argument("--members-file", default=None); s.add_argument("--rows", default="train", choices=["train", "all"])
    s.add_argument("--exclude-sources", default=None); s.add_argument("--only-sources", default=None); s.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    {"recompute": cmd_recompute, "select": cmd_select, "combine": cmd_combine, "attribute": cmd_attribute, "directions": cmd_directions}[a.cmd](a)


if __name__ == "__main__":
    main()
