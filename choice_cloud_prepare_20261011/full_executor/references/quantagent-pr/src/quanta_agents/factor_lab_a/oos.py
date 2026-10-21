"""Out-of-sample evaluation of a frozen combination on a fresh panel (2025 and 2026 never used in selection).

Steps:
  build-panel : wide panel from per-stock daily bars (minute aggregates), ST/delisting flags carried
                forward from the frozen daily source (last known status), same derived fields as panel.py
  run         : recompute every member of a saved combination on the new panel (definition + frozen
                direction from the research root), rank per date, train LightGBM on years <= Y-1 and
                predict year Y for Y in the out-of-sample years, then report RankIC and pure-factor
                portfolio statistics per year.
"""
from __future__ import annotations
import argparse
import glob
import json
import os
import time
import numpy as np
import pandas as pd
import pyarrow.parquet as pq

from .panel import Panel, LABELS
from .dsl import Compiler
from .evaluate import Evaluator, rank_ic, annual_table
from .ledger import Store
from .cli import compute_candidate, pct_ranks, _log
from .combine import rolling_combination, LazyRanks
from . import portfolio as pf


def build_panel_from_daily(daily_dir: str, out_dir: str, flags_source: str, start="2015-01-01", end="2026-12-31", min_listed_days=120):
    os.makedirs(out_dir, exist_ok=True)
    frames = []
    for f in sorted(glob.glob(os.path.join(daily_dir, "*.parquet"))):
        frames.append(pd.read_parquet(f))
    df = pd.concat(frames, ignore_index=True); del frames
    df["date"] = pd.to_datetime(df["date"])
    df = df[(df["date"] >= start) & (df["date"] <= end)].sort_values(["code", "date"])
    # flags from the frozen daily source (is_st / is_delisting), carried forward per stock
    fl = []
    for f in sorted(glob.glob(os.path.join(flags_source, "*.parquet"))):
        t = pq.read_table(f, columns=["date", "code", "is_st", "is_delisting"]).to_pandas()
        fl.append(t)
    flags = pd.concat(fl, ignore_index=True); del fl
    flags["code"] = flags["code"].str.upper(); flags["date"] = pd.to_datetime(flags["date"])
    df = df.merge(flags, on=["date", "code"], how="left")
    df[["is_st", "is_delisting"]] = df.groupby("code")[["is_st", "is_delisting"]].ffill().fillna(False)
    df["listed_days"] = df.groupby("code").cumcount() + 1
    fields = {}
    for name in ("open", "high", "low", "close", "volume", "amount", "float_shares", "total_shares", "float_market_cap", "total_market_cap", "listed_days"):
        fields[name] = df.pivot(index="date", columns="code", values=name)
    for name in ("is_st", "is_delisting"):
        fields[name] = df.pivot(index="date", columns="code", values=name).astype(float)
    dates = fields["close"].index; codes = list(fields["close"].columns)
    # previous close per stock (last available close before a suspension), not a calendar-grid shift
    fields["prev_close"] = fields["close"].ffill().shift(1).where(fields["close"].notna())
    if flags["date"].max() < df["date"].max():
        raise ValueError(f"ST/delisting flags end {flags['date'].max().date()} before the bars end {df['date'].max().date()}: extend the flags source or cut --end")
    # vwap on the same qfq basis as the prices (amount/volume is a raw price; qfq_ratio brings it to the panel basis)
    if "qfq_ratio" in df.columns:
        fields["qfq_ratio"] = df.pivot(index="date", columns="code", values="qfq_ratio")
        fields["vwap"] = (fields["amount"] / fields["volume"].replace(0, np.nan)) * fields.pop("qfq_ratio")
    else:
        raise ValueError("daily bars need qfq_ratio to build a qfq vwap")
    fields["turnover"] = fields["volume"] / fields["float_shares"]
    fields["ret"] = fields["close"] / fields["prev_close"] - 1
    fields["log_cap"] = np.log(fields["float_market_cap"])
    fields["log_total_cap"] = np.log(fields["total_market_cap"])
    eligible = ((fields["is_st"] != 1) & (fields["is_delisting"] != 1) & (fields["amount"] > 0)
                & (fields["listed_days"] >= min_listed_days) & fields["close"].notna()).astype(float)
    fields["eligible"] = eligible
    mkt = fields["ret"].where(eligible > 0).mean(axis=1)
    fields["mkt_ret"] = pd.DataFrame(np.repeat(mkt.values[:, None], len(codes), axis=1), index=dates, columns=codes)
    for name, (exit_shift, enter_shift) in LABELS.items():
        fields[name] = fields["open"].shift(-exit_shift) / fields["open"].shift(-enter_shift) - 1
    year = pd.Series(dates.year, index=dates)
    purge = pd.Series(False, index=dates)
    for y, idx in year.groupby(year).groups.items():
        if y < dates[-1].year:  # only complete calendar years get the year-end purge
            purge.loc[idx[-6:]] = True
    fields["eval_ok"] = pd.DataFrame(np.repeat((~purge).values[:, None].astype(float), len(codes), axis=1), index=dates, columns=codes)
    for name, frame in fields.items():
        frame.astype(np.float32).to_parquet(os.path.join(out_dir, f"{name}.parquet"))
    meta = {"source": daily_dir, "flags_source": flags_source, "start": start, "end": str(dates[-1].date()),
            "dates": [d.strftime("%Y-%m-%d") for d in dates], "codes": codes, "fields": sorted(fields),
            "min_listed_days": min_listed_days, "label_definition": "label_k = open[t+1+k]/open[t+1]-1",
            "purge": "last 6 signal dates of each complete calendar year", "vwap": "amount/volume * qfq_ratio (qfq basis)"}
    with open(os.path.join(out_dir, "meta.json"), "w", encoding="utf-8") as fh:
        json.dump(meta, fh, ensure_ascii=False)
    return {"dates": len(dates), "codes": len(codes), "end": meta["end"]}


def run_oos(research_root: str, panel_dir: str, combo_file: str, index: int, out_root: str, oos_years=(2025, 2026), train_years=(2016, 2017, 2018),
            day_step=4, cost=0.002, seed=0, workers=6):
    os.makedirs(out_root, exist_ok=True)
    store = Store(research_root)
    proto = store.protocol()
    panel = Panel(panel_dir)
    panel._dtype = np.float32
    compiler = Compiler(resolver=lambda name: panel[name])
    ev = Evaluator(panel, universe="all_a", label="label_5", min_stocks=proto["min_stocks"], train_years=train_years, target_years=tuple(oos_years))
    ev._store = store
    with open(combo_file, encoding="utf-8") as fh:
        saved = json.load(fh)[index]
    members = saved["features"]
    cands = {c["id"]: c for c in store.candidates()}
    rank_dir = os.path.join(out_root, "ranks"); os.makedirs(rank_dir, exist_ok=True)
    ranks = {}
    t0 = time.time(); failed = []
    todo = []
    for cid in members:
        rp = os.path.join(rank_dir, f"{cid}.npy")
        if os.path.exists(rp):
            ranks[cid] = LazyRanks(rp)
        else:
            todo.append(cands[cid])
    if todo and workers <= 1:
        for i, c in enumerate(todo):
            try:
                f, _ = compute_candidate(c, panel, compiler, ev)
                d = (store.load_result(c["id"]) or {}).get("direction")
                if not d:
                    raise ValueError(f"no frozen direction for member {c['id']}")
                rp = os.path.join(rank_dir, f"{c['id']}.npy"); np.save(rp, pct_ranks(f * d, ev).astype(np.float16)); ranks[c["id"]] = LazyRanks(rp)
                del f
            except Exception as exc:  # noqa: BLE001
                failed.append({"id": c["id"], "name": c.get("name"), "error": str(exc)[:160]}); _log(f"  failed {c.get('name')}: {str(exc)[:100]}")
            if (i + 1) % 20 == 0:
                _log(f"recomputed {i + 1}/{len(todo)} members ({time.time() - t0:.0f}s), failed {len(failed)}")
            panel.release(keep=tuple(k for k in panel._cache if not k.startswith("__")))
        todo = []
    if todo:
        from .parallel import run_parallel
        import shutil
        directions = {c["id"]: (store.load_result(c["id"]) or {}).get("direction") for c in todo}
        missing_dir = [cid for cid, d in directions.items() if not d]
        if missing_dir:
            raise ValueError(f"no frozen direction for members {missing_dir[:5]}")
        # the OOS panel is a different directory: point worker panels at it
        n = 0
        for w in run_parallel(todo, research_root, panel_dir, tuple(oos_years), train_years, workers=workers, full=False, directions=directions, log=_log):
            n += 1
            if "error" in w or not w.get("rank_path"):
                failed.append({"id": w["id"], "name": w.get("name"), "error": w.get("error", "no ranks")[:160]})
            else:
                rp = os.path.join(rank_dir, f"{w['id']}.npy"); shutil.move(w["rank_path"], rp); ranks[w["id"]] = LazyRanks(rp)
                if w.get("sig_path") and os.path.exists(w["sig_path"]):
                    os.remove(w["sig_path"])
            if n % 20 == 0:
                _log(f"recomputed {n}/{len(todo)} members ({time.time() - t0:.0f}s), failed {len(failed)}")
    # members that failed in the workers (usually a transient memory error under the commit cap) get one serial retry
    retry = [cands[f["id"]] for f in failed if f["id"] in cands]
    failed = []
    for c in retry:
        try:
            f, _ = compute_candidate(c, panel, compiler, ev)
            d = (store.load_result(c["id"]) or {}).get("direction")
            if not d:
                raise ValueError("no frozen direction")
            rp = os.path.join(rank_dir, f"{c['id']}.npy"); np.save(rp, pct_ranks(f * d, ev).astype(np.float16)); ranks[c["id"]] = LazyRanks(rp)
            del f
            _log(f"  retried {c.get('name')}: ok")
        except Exception as exc:  # noqa: BLE001
            failed.append({"id": c["id"], "name": c.get("name"), "error": str(exc)[:160]}); _log(f"  retry failed {c.get('name')}: {str(exc)[:100]}")
        panel.release(keep=tuple(k for k in panel._cache if not k.startswith("im_")))
    _log(f"members available {len(ranks)}/{len(members)}; failed {len(failed)}")
    feature_ids = [m for m in members if m in ranks]
    if len(feature_ids) != len(members):
        raise RuntimeError(f"{len(members) - len(feature_ids)} members could not be recomputed on the out-of-sample panel: {failed[:3]}")
    if saved["model"] == "lgbm":
        from .combine import rolling_combination_lowmem
        label_override = None
        if saved.get("label_blend"):
            from .trading import blended_label
            label_override = blended_label(panel, horizons=tuple(saved["label_blend"]), mask=panel.mask(ev.universe))
            _log(f"training label: blend of horizons {saved['label_blend']}")
        res, pred = rolling_combination_lowmem(panel, ev, ranks, feature_ids, target=saved["target"], train_years=train_years, target_years=tuple(oos_years),
                                               day_step=day_step, params=saved.get("params") or None, seed=seed, workdir=os.path.join(out_root, "tmp_lowmem"), log=_log,
                                               smooth_lambda=float(saved.get("smooth_lambda") or 0.0), label_override=label_override)
        res["label_blend"] = saved.get("label_blend")
    else:
        res, pred = rolling_combination(panel, ev, ranks, feature_ids, model=saved["model"], target=saved["target"], update="expanding",
                                        train_years=train_years, target_years=tuple(oos_years), day_step=day_step, params=saved.get("params") or None, seed=seed)
    pred.astype(np.float32).to_parquet(os.path.join(out_root, "prediction_oos.parquet"))
    out = {"combo_file": combo_file, "index": index, "n_members": len(feature_ids), "failed_members": failed,
           "oos_years": list(oos_years), "annual_rank_ic": {y: v for y, v in res["annual_rank_ic"].items()},
           "target_mean_rank_ic": res["target_mean_rank_ic"], "target_worst_rank_ic": res["target_worst_rank_ic"], "fits": res["fits"], "portfolio": {}}
    out["portfolio"] = portfolio_report(panel, ev, pred, tuple(oos_years), cost)
    with open(os.path.join(out_root, "oos_result.json"), "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1, default=float)
    return out


def buyable_mask(panel, mask: pd.DataFrame) -> pd.DataFrame:
    gap = (panel["open"].shift(-1) / panel["close"] - 1).to_numpy(np.float64)
    codes = pd.Index(panel.codes)
    wide = np.asarray(codes.str.startswith(("SZ300", "SZ301", "SZ302", "SH688", "SH689")))
    # a name is unbuyable when its next open already sits at the limit (10% main board, 20% ChiNext/STAR; ChiNext
    # moved to 20% on 2020-08-24); the threshold sits just under the band so rounding never lets a limit open through
    lim = np.broadcast_to(np.where(wide[None, :], 0.1985, 0.0985), gap.shape).copy()
    early = np.asarray(panel.dates < pd.Timestamp("2020-08-24"))
    chinext = np.asarray(codes.str.startswith(("SZ300", "SZ301", "SZ302")))
    lim[np.ix_(early, chinext)] = 0.0985
    if panel.has("is_st"):  # ST names trade in a 5% band
        lim = np.where(panel["is_st"].to_numpy(np.float32) > 0, 0.0485, lim)
    with np.errstate(invalid="ignore"):
        limit_up = gap > lim
    return mask & ~pd.DataFrame(limit_up, index=mask.index, columns=mask.columns)


def portfolio_report(panel, ev, pred: pd.DataFrame, years, cost=0.002) -> dict:
    label1 = panel["label_1"].to_numpy(np.float64)
    m = buyable_mask(panel, panel.mask(ev.universe))  # trading universe: eligibility only, no label purge
    out = {}
    for smooth in (0, 5):
        p2 = pred.where(m)
        if smooth:
            p2 = p2.rank(axis=1, pct=True).ewm(span=smooth, adjust=False, min_periods=1).mean()
        pct = p2.where(m).rank(axis=1, pct=True).to_numpy(np.float32)
        for hold in (1, 5, 10):
            s = pf.portfolio_series(pct, label1, m.to_numpy(bool), panel.dates, top_frac=0.1, hold=hold)
            for c in sorted({0.001, cost}):
                out[f"smooth{smooth}_hold{hold}_cost{c}"] = pf.summarize(s, tuple(years), cost_rate=c)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(); sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("build-panel"); s.add_argument("--daily-dir", required=True); s.add_argument("--out", required=True)
    s.add_argument("--flags-source", default=r"D:/qlib_data/parquet_cn_a_qfq_tradeable_v2_2015_2025"); s.add_argument("--start", default="2015-01-01")
    s = sub.add_parser("run"); s.add_argument("--research-root", required=True); s.add_argument("--panel-dir", required=True); s.add_argument("--combo-file", required=True)
    s.add_argument("--index", type=int, default=0); s.add_argument("--out", required=True); s.add_argument("--years", nargs="*", type=int, default=[2025, 2026])
    s.add_argument("--day-step", type=int, default=4); s.add_argument("--cost", type=float, default=0.002); s.add_argument("--seed", type=int, default=0); s.add_argument("--workers", type=int, default=6)
    s = sub.add_parser("report"); s.add_argument("--research-root", required=True); s.add_argument("--panel-dir", required=True); s.add_argument("--out", required=True)
    s.add_argument("--years", nargs="*", type=int, default=[2025, 2026]); s.add_argument("--cost", type=float, default=0.002)
    a = ap.parse_args(argv)
    if a.cmd == "report":
        store = Store(a.research_root); proto = store.protocol(); panel = Panel(a.panel_dir); panel._dtype = np.float32
        ev = Evaluator(panel, universe="all_a", label="label_5", min_stocks=proto["min_stocks"], train_years=(2016, 2017, 2018), target_years=tuple(a.years))
        pred = pd.read_parquet(os.path.join(a.out, "prediction_oos.parquet")); pred.index = pd.DatetimeIndex(pred.index)
        ric, n = rank_ic(pred, ev.label, ev.mask, proto["min_stocks"])
        ar = annual_table(ric, tuple(a.years))
        res = {"annual_rank_ic": {str(y): v for y, v in ar.items()}, "portfolio": portfolio_report(panel, ev, pred, tuple(a.years), a.cost)}
        with open(os.path.join(a.out, "oos_report.json"), "w", encoding="utf-8") as fh:
            json.dump(res, fh, ensure_ascii=False, indent=1, default=float)
        print("RankIC:", {y: (round(v["mean"], 4), v["days"]) for y, v in res["annual_rank_ic"].items()})
        for k, v in res["portfolio"].items():
            print("%-26s gross %+.3f sh %.2f | net %+.3f sh %.2f | turn %.3f | annual net %s" % (k, v["gross_excess_ann"], v["gross_sharpe"], v["net_excess_ann"], v["net_sharpe"], v["daily_turnover"],
                  {y: round(a_["net_excess"], 3) for y, a_ in v["annual"].items()}))
        return
    if a.cmd == "build-panel":
        print(json.dumps(build_panel_from_daily(a.daily_dir, a.out, a.flags_source, start=a.start), ensure_ascii=False))
    else:
        out = run_oos(a.research_root, a.panel_dir, a.combo_file, a.index, a.out, oos_years=tuple(a.years), day_step=a.day_step, cost=a.cost, seed=a.seed, workers=a.workers)
        print(json.dumps({k: out[k] for k in ("n_members", "target_mean_rank_ic", "target_worst_rank_ic", "annual_rank_ic")}, ensure_ascii=False, default=float))
        for k, v in out["portfolio"].items():
            print("%-26s gross %+.3f sh %.2f | net %+.3f sh %.2f | turn %.3f | annual net %s" % (k, v["gross_excess_ann"], v["gross_sharpe"], v["net_excess_ann"], v["net_sharpe"], v["daily_turnover"],
                  {y: round(a_["net_excess"], 3) for y, a_ in v["annual"].items()}))


if __name__ == "__main__":
    main()
