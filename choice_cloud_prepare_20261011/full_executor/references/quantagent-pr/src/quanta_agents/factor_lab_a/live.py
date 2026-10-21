"""Daily production path for the competition book.

  build-panel   : window panel (same fields as the research panel) from a frozen-format daily source that extends to
                  the latest session; listed_days is counted over the full history, not the window
  ranks         : recompute a saved member set on that panel (frozen directions from the research store) and store
                  per-date percentile ranks inside the chosen universe (all_a or union), float16 <id>.npy
  check-ranks   : rank agreement between two rank directories on overlapping dates (live source vs research source)
  train-final   : one LightGBM model (same setup as the frozen combinations) on every sampled labelled row up to the
                  last date whose blended label has fully exited; booster + feature order saved
  score         : predict the model on every date of a panel window -> prediction parquet
  target        : next-session target book from the latest scores, the current holdings and the two-bucket rule,
                  with fill-rate pre-checks (CSI500 share, invested, single-name)
Timing: run after the close of t with data through t; orders go at the open of t+1.
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

from .panel import Panel, RAW_FIELDS, BOOL_FIELDS, LABELS
from .evaluate import Evaluator
from .ledger import Store
from .dsl import Compiler
from .combine import LazyRanks, MemmapSequence, _predict_chunks
from .trading import blended_label, trading_mask
from . import competition as cp

LGB_PARAMS = {"objective": "regression", "learning_rate": 0.03, "num_leaves": 31, "min_data_in_leaf": 500, "feature_fraction": 0.7,
              "bagging_fraction": 0.7, "bagging_freq": 1, "lambda_l2": 10.0, "verbose": -1, "num_threads": 24, "num_rounds": 400}


def _log(msg):
    print(time.strftime("%H:%M:%S"), msg, flush=True)


# ----------------------------------------------------------------------------- panel window
def build_panel_window(source_root: str, out_dir: str, instruments_dir: str, start: str, end: str | None = None, min_listed_days=120) -> dict:
    os.makedirs(out_dir, exist_ok=True)
    cols = ["date", "code"] + RAW_FIELDS + BOOL_FIELDS
    frames = []
    for f in sorted(glob.glob(os.path.join(source_root, "*.parquet"))):
        t = pq.read_table(f, columns=cols).to_pandas()
        if end:
            t = t[t["date"] <= end]
        t = t.sort_values("date")
        t["listed_days"] = np.arange(1, len(t) + 1)      # full history before the window cut
        t = t[t["date"] >= start]
        if len(t):
            frames.append(t)
    df = pd.concat(frames, ignore_index=True); del frames
    df["code"] = df["code"].str.upper(); df = df.sort_values(["code", "date"])
    fields = {}
    for name in RAW_FIELDS + ["listed_days"]:
        fields[name] = df.pivot(index="date", columns="code", values=name)
    for name in BOOL_FIELDS:
        fields[name] = df.pivot(index="date", columns="code", values=name).astype(float)
    dates = fields["close"].index; codes = list(fields["close"].columns)
    fields["turnover"] = fields["volume"] / fields["float_shares"]
    fields["ret"] = fields["close"] / fields["prev_close"] - 1
    fields["log_cap"] = np.log(fields["float_market_cap"]); fields["log_total_cap"] = np.log(fields["total_market_cap"])
    fields["vwap"] = fields.pop("vwap_qfq")
    eligible = ((fields["is_st"] != 1) & (fields["is_delisting"] != 1) & (fields["amount"] > 0) & (fields["listed_days"] >= min_listed_days) & fields["close"].notna()).astype(float)
    fields["eligible"] = eligible
    mkt = fields["ret"].where(eligible > 0).mean(axis=1)
    fields["mkt_ret"] = pd.DataFrame(np.repeat(mkt.values[:, None], len(codes), axis=1), index=dates, columns=codes)
    for name, (exit_shift, enter_shift) in LABELS.items():
        fields[name] = fields["open"].shift(-exit_shift) / fields["open"].shift(-enter_shift) - 1
    for uni in cp.INDEX_CODES:
        m, _ = cp.membership_from_instruments(dates, codes, os.path.join(instruments_dir, f"{uni}.txt"))
        fields[f"member_{uni}"] = m.astype(float)
    year = pd.Series(dates.year, index=dates); purge = pd.Series(False, index=dates)
    for y, idx in year.groupby(year).groups.items():
        if y < dates[-1].year:
            purge.loc[idx[-6:]] = True
    fields["eval_ok"] = pd.DataFrame(np.repeat((~purge).values[:, None].astype(float), len(codes), axis=1), index=dates, columns=codes)
    for name, frame in fields.items():
        frame.astype(np.float32).to_parquet(os.path.join(out_dir, f"{name}.parquet"))
    meta = {"source_root": source_root, "instruments_dir": instruments_dir, "start": start, "end": str(dates[-1].date()), "dates": [d.strftime("%Y-%m-%d") for d in dates],
            "codes": codes, "fields": sorted(fields), "min_listed_days": min_listed_days, "label_definition": "label_k = open[t+1+k]/open[t+1]-1",
            "purge": "last 6 signal dates of each complete calendar year", "built": time.strftime("%Y-%m-%dT%H:%M:%S"), "kind": "live window (listed_days over full history)"}
    with open(os.path.join(out_dir, "meta.json"), "w", encoding="utf-8") as fh:
        json.dump(meta, fh, ensure_ascii=False)
    return {"dates": len(dates), "codes": len(codes), "start": str(dates[0].date()), "end": meta["end"]}


# ----------------------------------------------------------------------------- member ranks
def load_members(members_file: str, index=0) -> list[str]:
    with open(members_file, encoding="utf-8") as fh:
        d = json.load(fh)
    if isinstance(d, dict):
        return list(d.get("features") or d["members"])
    if d and isinstance(d[0], dict) and "features" in d[0]:
        return list(d[index]["features"])
    return list(d)


def compute_ranks(research_root: str, panel_dir: str, members: list[str], out_dir: str, universe="union", overwrite=False) -> dict:
    from .cli import compute_candidate
    os.makedirs(out_dir, exist_ok=True)
    store = Store(research_root)
    panel = Panel(panel_dir); panel._dtype = np.float32
    compiler = Compiler(resolver=lambda name: panel[name])
    years = sorted(set(panel.dates.year))
    ev = Evaluator(panel, universe=universe, label="label_5", min_stocks=50, train_years=tuple(years[:1]), target_years=tuple(years))
    ev._store = store
    ev.mask = panel.mask(universe)            # no year-end purge on the production ranks
    ev._mask_np = ev.mask.to_numpy(bool)
    cands = {c["id"]: c for c in store.candidates()}
    failed = []; done = 0; t0 = time.time()
    for i, cid in enumerate(members):
        rp = os.path.join(out_dir, f"{cid}.npy")
        if os.path.exists(rp) and not overwrite:
            done += 1; continue
        try:
            f, _ = compute_candidate(cands[cid], panel, compiler, ev)
            d = (store.load_result(cid) or {}).get("direction")
            if not d:
                raise ValueError("no frozen direction")
            arr = (f * d).where(ev.mask).rank(axis=1, pct=True).to_numpy(np.float16)
            np.save(rp + ".tmp.npy", arr); os.replace(rp + ".tmp.npy", rp); done += 1
            del f, arr
        except Exception as exc:  # noqa: BLE001
            failed.append({"id": cid, "name": cands.get(cid, {}).get("name"), "error": str(exc)[:200]}); _log(f"  failed {cands.get(cid, {}).get('name')}: {str(exc)[:120]}")
        if (i + 1) % 25 == 0:
            _log(f"  ranks {i + 1}/{len(members)} ({time.time() - t0:.0f}s), failed {len(failed)}")
            panel.release(keep=("open", "high", "low", "close", "volume", "amount", "vwap", "turnover", "ret", "log_cap", "prev_close", "mkt_ret", "eligible", "float_market_cap"))
    with open(os.path.join(out_dir, f"_meta{'_' + str(os.getpid()) if len(members) < 100 else ''}.json"), "w", encoding="utf-8") as fh:
        json.dump({"research_root": research_root, "panel_dir": panel_dir, "universe": universe, "n_members": len(members), "done": done, "failed": failed,
                   "dates": [d.strftime("%Y-%m-%d") for d in panel.dates[[0, -1]]], "written": time.strftime("%Y-%m-%dT%H:%M:%S")}, fh, ensure_ascii=False, indent=1)
    return {"done": done, "failed": failed}


def check_ranks(dir_a: str, panel_a: str, dir_b: str, panel_b: str, members: list[str], max_members=60) -> dict:
    """Spearman agreement per overlapping date between two rank directories (different data sources)."""
    pa = Panel(panel_a); pb = Panel(panel_b)
    common_dates = pa.dates.intersection(pb.dates); common_codes = [c for c in pa.codes if c in set(pb.codes)]
    ia = pa.dates.get_indexer(common_dates); ib = pb.dates.get_indexer(common_dates)
    ca = pd.Index(pa.codes).get_indexer(common_codes); cb = pd.Index(pb.codes).get_indexer(common_codes)
    rows = []
    for cid in members[:max_members]:
        fa = os.path.join(dir_a, f"{cid}.npy"); fb = os.path.join(dir_b, f"{cid}.npy")
        if not (os.path.exists(fa) and os.path.exists(fb)):
            continue
        A = np.load(fa).astype(np.float32)[np.ix_(ia, ca)]; B = np.load(fb).astype(np.float32)[np.ix_(ib, cb)]
        corr = pd.DataFrame(A).T.corrwith(pd.DataFrame(B).T, method="spearman")
        cov = (np.isfinite(A) & np.isfinite(B)).sum(axis=1) / np.maximum(np.isfinite(B).sum(axis=1), 1)
        rows.append({"id": cid, "spearman_mean": float(np.nanmean(corr)), "spearman_min": float(np.nanmin(corr)), "coverage_ratio": float(np.nanmean(cov))})
    df = pd.DataFrame(rows)
    return {"dates": [str(common_dates[0].date()), str(common_dates[-1].date()), int(len(common_dates))], "codes": len(common_codes), "members": len(df),
            "spearman_median": float(df["spearman_mean"].median()) if len(df) else None, "spearman_p05": float(df["spearman_mean"].quantile(0.05)) if len(df) else None,
            "worst": df.sort_values("spearman_mean").head(8).to_dict("records") if len(df) else []}


# ----------------------------------------------------------------------------- final model
def _pack(panel, ranks_dir: str, members: list[str], pos: np.ndarray, mask_np: np.ndarray, path: str, log=_log) -> np.ndarray:
    n_rows = len(pos) * len(panel.codes); nf = len(members)
    X = np.lib.format.open_memmap(path, mode="w+", dtype=np.float16, shape=(n_rows, nf))
    fin = np.zeros(n_rows, np.int32); t0 = time.time(); BLOCK = 32
    for j0 in range(0, nf, BLOCK):
        ids = members[j0:j0 + BLOCK]; blk = np.empty((n_rows, len(ids)), np.float16)
        for jj, fid in enumerate(ids):
            with open(os.path.join(ranks_dir, f"{fid}.npy"), "rb") as fh:
                arr = np.load(fh)
            a = np.where(mask_np, arr[pos], np.nan).astype(np.float16).ravel(); blk[:, jj] = a; fin += np.isfinite(a); del arr, a
        X[:, j0:j0 + len(ids)] = blk; del blk
        if j0 + BLOCK >= nf or (j0 // BLOCK) % 4 == 3:
            log(f"  packed {min(j0 + BLOCK, nf)}/{nf} ({time.time() - t0:.0f}s)")
    X.flush(); del X
    return fin


def train_final(panel_dir: str, ranks_dir: str, members: list[str], out_dir: str, universe="union", label_blend=(5, 10, 20), train_from=2016, day_step=4,
                seed=0, params=None, min_feature_share=0.8, workdir=None, log=_log, label_neutral_field: str | None = None, label_neutral_q=5) -> dict:
    """label_neutral_field (A15 item 1): re-rank the blended training label inside `label_neutral_q` groups of a style
    field (e.g. turnover20) per date before training, as scripts/competition_union_combine.py --label-neutral-field."""
    import lightgbm as lgb
    os.makedirs(out_dir, exist_ok=True)
    panel = Panel(panel_dir); panel._dtype = np.float32
    mask = panel.mask(universe)
    lab = blended_label(panel, horizons=tuple(label_blend), purge_last=0, mask=mask)
    if label_neutral_field:
        lab = cp.cap_neutral_within(lab, cp.style_field(panel, label_neutral_field), mask & lab.notna(), int(label_neutral_q))
        log(f"  training label re-ranked inside {label_neutral_q} groups of {label_neutral_field}")
    ok = (mask & lab.notna())
    years = np.array(panel.dates.year)
    pos = np.arange(len(panel.dates))[(years >= train_from) & ok.any(axis=1).to_numpy()][::day_step]
    last_label_date = panel.dates[pos[-1]]
    workdir = workdir or os.path.join(out_dir, "tmp"); os.makedirs(workdir, exist_ok=True)
    xpath = os.path.join(workdir, "xtr.npy")
    m_np = ok.iloc[pos].to_numpy(bool)
    fin = _pack(panel, ranks_dir, members, pos, m_np, xpath, log)
    y = (lab.iloc[pos].where(ok.iloc[pos]).rank(axis=1, pct=True) - 0.5).to_numpy(np.float32).ravel()
    keep = np.isfinite(y) & (fin >= min_feature_share * len(members)); y = np.where(keep, y, 0.0).astype(np.float32)
    p = dict(LGB_PARAMS); p.update(params or {}); rounds = p.pop("num_rounds"); p["seed"] = seed
    ds_params = {"max_bin": 255, "min_data_in_leaf": p["min_data_in_leaf"], "bin_construct_sample_cnt": 50000, "verbose": -1}
    mm = np.load(xpath, mmap_mode="r")
    ds = lgb.Dataset([MemmapSequence(mm)], label=y, weight=keep.astype(np.float32), params=ds_params, free_raw_data=True); ds.construct()
    log(f"  training on {int(keep.sum())} rows x {len(members)} features, sampled every {day_step} sessions {panel.dates[pos[0]].date()}..{last_label_date.date()}")
    booster = lgb.train(p, ds, num_boost_round=rounds)
    del ds, mm
    try:
        os.remove(xpath)
    except OSError:
        pass
    booster.save_model(os.path.join(out_dir, "model.txt"))
    meta = {"panel_dir": panel_dir, "ranks_dir": ranks_dir, "universe": universe, "label_blend": list(label_blend), "label_neutral_field": label_neutral_field, "label_neutral_q": int(label_neutral_q) if label_neutral_field else 0, "train_from": train_from, "day_step": day_step, "seed": seed,
            "params": dict(p, num_rounds=rounds), "n_rows": int(keep.sum()), "first_train_date": str(panel.dates[pos[0]].date()), "last_label_date": str(last_label_date.date()),
            "data_end": str(panel.dates[-1].date()), "features": members, "trained": time.strftime("%Y-%m-%dT%H:%M:%S")}
    with open(os.path.join(out_dir, "model_meta.json"), "w", encoding="utf-8") as fh:
        json.dump(meta, fh, ensure_ascii=False, indent=1)
    return meta


def score(panel_dir: str, ranks_dir: str, model_dir: str, out_path: str, start: str | None = None, min_feature_share=0.8, workdir=None, log=_log) -> dict:
    import lightgbm as lgb
    with open(os.path.join(model_dir, "model_meta.json"), encoding="utf-8") as fh:
        meta = json.load(fh)
    members = meta["features"]; universe = meta["universe"]
    panel = Panel(panel_dir); panel._dtype = np.float32
    mask = panel.mask(universe)
    pos = np.arange(len(panel.dates))
    if start:
        pos = pos[panel.dates >= pd.Timestamp(start)]
    workdir = workdir or os.path.join(os.path.dirname(out_path) or ".", "tmp_score"); os.makedirs(workdir, exist_ok=True)
    xpath = os.path.join(workdir, "xte.npy")
    m_np = mask.iloc[pos].to_numpy(bool)
    fin = _pack(panel, ranks_dir, members, pos, m_np, xpath, log)
    keep = m_np.ravel() & (fin >= min_feature_share * len(members))
    booster = lgb.Booster(model_file=os.path.join(model_dir, "model.txt"))
    mm = np.load(xpath, mmap_mode="r")
    pred = _predict_chunks(booster, mm)
    del mm
    try:
        os.remove(xpath)
    except OSError:
        pass
    full = np.where(keep, pred, np.nan).astype(np.float32).reshape(len(pos), len(panel.codes))
    df = pd.DataFrame(full, index=panel.dates[pos], columns=panel.codes)
    df.to_parquet(out_path)
    cov = df.notna().sum(axis=1)
    return {"out": out_path, "dates": [str(df.index[0].date()), str(df.index[-1].date()), int(len(df))], "scored_names_last": int(cov.iloc[-1]), "scored_names_mean": float(cov.mean())}


# ----------------------------------------------------------------------------- target book
def read_holdings(path: str | None) -> pd.DataFrame:
    """CSV with columns code, shares[, market_value][, avail_shares]; code accepted as SH600000 / 600000.SH / sh600000."""
    if not path or not os.path.exists(path):
        return pd.DataFrame(columns=["code", "shares", "market_value"])
    h = pd.read_csv(path, dtype={"code": str})
    h["code"] = h["code"].astype(str).str.strip().map(normalize_code)
    if "market_value" not in h:
        h["market_value"] = np.nan
    return h


def normalize_code(c: str) -> str:
    c = c.strip().upper()
    if "." in c:
        num, ex = c.split(".")[:2]
        return f"{ex[:2]}{num.zfill(6)}"
    if c[:2] in ("SH", "SZ", "BJ"):
        return c[:2] + c[2:].zfill(6)
    num = c.zfill(6)
    return ("SH" if num[0] in "5679" else "SZ") + num


def build_target(panel_dir: str, prediction_path: str, holdings_path: str | None, nav: float, cfg: dict, out_dir: str, asof: str | None = None, lots=100,
                 state_window=250, min_trade_frac=0.001, allow_history=False) -> dict:
    """Next-session target book. CSI500 bucket: 'concentrated' (top-n hysteresis against the current holdings) or
    'enhanced' (cap^gamma weights with exclusion / overweight states replayed over the last `state_window` sessions of
    scores, so no state file is needed). Other-union bucket: concentrated top-n. Trades smaller than
    min_trade_frac x NAV are suppressed (target set to the current value)."""
    cfg = dict(cp.DEFAULT_CFG, **(cfg or {}))
    panel = Panel(panel_dir); panel._dtype = np.float32
    pred = pd.read_parquet(prediction_path); pred.index = pd.DatetimeIndex(pred.index)
    pred = pred.reindex(columns=panel.codes)
    asof_ts = pd.Timestamp(asof) if asof else pred.index[-1]
    pred = pred[pred.index <= asof_ts]
    if pred.index[-1] != asof_ts:
        raise SystemExit(f"no scores on {asof_ts.date()} (last scored {pred.index[-1].date()})")
    if asof_ts != panel.dates[-1] and not allow_history:
        raise SystemExit(f"asof {asof_ts.date()} is not the panel's last date {panel.dates[-1].date()}: closes are qfq prices, lot sizes would be wrong (pass allow_history to override)")
    mem, mnote = cp.membership(panel)
    base = trading_mask(panel).reindex(index=pred.index)
    union = (base & mem["union"].reindex(index=pred.index))
    sc = cp.smooth_scores(pred, union, cfg["smooth"])
    q = int(cfg.get("neutral_q", 0) or 0)
    if q > 0:   # style-neutral percentiles inside groups of `neutral_field`, per bucket (same as competition.two_bucket_book)
        nf = cfg.get("neutral_field", "float_market_cap")
        if nf == "turnover20":
            grp = panel["turnover"].rolling(20, min_periods=10).mean()
        elif nf == "vol20":
            grp = panel["ret"].rolling(20, min_periods=10).std()
        elif nf == "mom20":
            grp = panel["close"].pct_change(20, fill_method=None)
        else:
            grp = panel[nf]
        grp = grp.reindex(index=pred.index, columns=panel.codes)
        m500_w = base & mem["csi500"].reindex(index=pred.index); moth_w = base & mem["union"].reindex(index=pred.index) & ~mem["csi500"].reindex(index=pred.index)
        if cfg.get("oth_universe"):              # A17 (opt-in): other bucket drawn from one index only, same as competition.two_bucket_book
            moth_w = moth_w & mem[cfg["oth_universe"]].reindex(index=pred.index)
        sc = cp.cap_neutral_within(sc, grp, m500_w, q).where(m500_w, cp.cap_neutral_within(sc, grp, moth_w, q))
    row = sc.iloc[-1]; t = pred.index[-1]
    m500 = (base & mem["csi500"]).loc[t]; moth = (base & mem["union"] & ~mem["csi500"]).loc[t]
    if cfg.get("oth_universe"):
        moth = moth & mem[cfg["oth_universe"]].reindex(index=base.index).loc[t]
    # A15 item 10: style exposure of the raw score on the as-of date (Spearman inside CSI500 / union) and the 20-session
    # mean of the score-vs-log-cap correlation inside CSI500 (the registered risk indicator: report when < -0.3)
    style = {}
    try:
        raw_t = pred.iloc[-1]
        fields = {"turnover20": cp.style_field(panel, "turnover20").loc[t], "log_cap": panel["log_cap"].loc[t], "vol20": cp.style_field(panel, "vol20").loc[t], "mom20": cp.style_field(panel, "mom20").loc[t]}
        for bname, bm in (("csi500", m500), ("union", (base & mem["union"]).loc[t])):
            style[bname] = {k: float(raw_t[bm].corr(v[bm], method="spearman")) for k, v in fields.items()}
        last20 = pred.index[-20:]; lc = panel["log_cap"].reindex(index=last20, columns=panel.codes); m5w = (base & mem["csi500"]).reindex(index=last20)
        cc = [float(pred.loc[d][m5w.loc[d]].corr(lc.loc[d][m5w.loc[d]], method="spearman")) for d in last20]
        style["csi500_cap_corr_20d_mean"] = float(np.nanmean(cc)); style["csi500_cap_corr_20d_alert"] = bool(np.nanmean(cc) < -0.3)
    except Exception as exc:  # diagnostics must never block the target
        style = {"error": str(exc)[:120]}
    close = panel["close"].loc[t]; is_st = panel["is_st"].loc[t] if panel.has("is_st") else pd.Series(0.0, index=panel.codes)
    hold = read_holdings(holdings_path)
    held_mv = {}
    for _, r in hold.iterrows():
        c = r["code"]; mv = r["market_value"]
        if not np.isfinite(mv):
            mv = float(r["shares"]) * float(close.get(c, np.nan))
        held_mv[c] = float(mv)
    held = set(held_mv)
    out_rows = []; plan = {}; min_trade = min_trade_frac * nav

    def emit(bucket, code, target, extra=None):
        h = held_mv.get(code, 0.0)
        if abs(target - h) < min_trade and target > 0 and h > 0:
            target = h; action = "hold"
        else:
            action = "sell" if target <= 0 and h > 0 else ("buy" if target > h else ("trim" if target < h else "hold"))
        out_rows.append({"bucket": bucket, "code": code, "score_pct": float(row.get(code, np.nan)), "held_mv": h, "target_mv": float(target), "action": action,
                         "close": float(close.get(code, np.nan)), **(extra or {})})

    def concentrated(bucket, msk, n_enter, n_keep, bucket_nav):
        s_ = row.where(msk).dropna().sort_values(ascending=False); pos = {c: i for i, c in enumerate(s_.index)}
        members_now = set(msk[msk].index)
        held_b = [c for c in held if c in members_now]
        stay = [c for c in held_b if (c not in pos) or pos[c] < n_keep]; sells = [c for c in held_b if c not in stay]
        entries = [c for c in s_.index[:n_enter] if c not in held] if bucket_nav > 0 else []
        # whole-lot execution (A15 item 10): an entry whose equal-weight slot cannot buy half a lot is skipped (its slot
        # stays in cash), a stay below half a lot is sold; the dropped value is NOT spread over the others (see below)
        n0 = max(len(stay) + len(entries), 1); slot_mv = bucket_nav / n0
        half = {c: 0.5 * lots * float(close.get(c, np.nan)) for c in stay + entries}
        skipped = [c for c in entries if np.isfinite(half[c]) and slot_mv < half[c]]
        entries = [c for c in entries if c not in set(skipped)]
        too_small = [c for c in stay if np.isfinite(half[c]) and held_mv[c] < half[c]]
        stay = [c for c in stay if c not in set(too_small)]; sells = sells + too_small
        book = stay + entries; n_after = len(book)
        cur = {c: held_mv[c] for c in stay}; cur_tot = sum(cur.values()); tgt = {}
        if n_after and bucket_nav > 0:
            slot = bucket_nav / n_after; need = len(entries) * slot
            scale = min(1.0, (bucket_nav - need) / cur_tot) if cur_tot > 0 else 0.0   # stays are trimmed to fund entries, never inflated (A15 item 10)
            for c in stay:
                tgt[c] = cur[c] * scale
            cash = bucket_nav - sum(tgt.values())
            entry_mv = min(cash / len(entries), 2.0 * slot) if entries else 0.0        # entries absorb the cash, capped at 2x a slot
            for c in entries:
                tgt[c] = entry_mv
        for c in set(book) | set(sells):
            emit(bucket, c, tgt.get(c, 0.0), {"state": None, "rank_in_bucket": pos.get(c)})
        plan[bucket] = {"book": "concentrated", "members": len(members_now), "scored": len(s_), "held_before": len(held_b), "stay": len(stay), "sells": len(sells),
                        "entries": len(entries), "after": n_after, "n_enter": n_enter, "n_keep": n_keep, "bucket_nav": bucket_nav, "skipped_unaffordable": len(skipped), "sold_below_half_lot": len(too_small)}

    # ---- CSI500 bucket
    w5 = float(cfg["w500"]); bucket_nav = w5 * nav
    if cfg.get("book", "concentrated") == "tebudget":
        # A15 item 7: one solve at the as-of date. alpha = centred in-bucket percentile of the (neutralised) score,
        # covariance = Ledoit-Wolf of the trailing te_lookback sessions of the members' returns, w0 = current bucket holdings
        from sklearn.covariance import LedoitWolf
        members_now = set(m500[m500].index)
        capr = panel[cfg.get("cap_field", "float_market_cap")].loc[t]
        idx = [c for c in panel.codes if m500.get(c, False) and np.isfinite(capr.get(c, np.nan)) and capr.get(c, 0) > 0]
        b = capr[idx].to_numpy(np.float64); b = b / b.sum()
        sc_b = row[idx].to_numpy(np.float64); has = np.isfinite(sc_b)
        r_ = np.full(len(idx), 0.5)
        if has.any():
            order = np.argsort(sc_b[has], kind="stable"); rr = np.empty(has.sum()); rr[order] = (np.arange(has.sum()) + 1) / has.sum(); r_[has] = rr
        ap_ = float(cfg.get("te_alpha_power", 1.0)); alpha = np.sign(r_ - 0.5) * np.abs(r_ - 0.5) ** ap_
        lb = int(cfg.get("te_lookback", 250))
        R = panel["ret"].loc[:t].iloc[-lb:][idx].to_numpy(np.float64); R = np.where(np.isfinite(R), R, 0.0); R = R - R.mean(axis=0, keepdims=True)
        S = LedoitWolf().fit(R).covariance_ if R.shape[0] >= 60 else np.diag(np.var(R, axis=0) + 1e-6)
        upper = np.full(len(idx), float(cfg.get("te_max_w", 0.10)))
        if cfg.get("te_max_tilt"):
            upper = np.minimum(upper, float(cfg["te_max_tilt"]) * b)
        held_b = np.array([held_mv.get(c, 0.0) for c in idx]); w0 = held_b / held_b.sum() if held_b.sum() > 0 else None
        te_var = (float(cfg.get("te_ann", 0.04)) ** 2) / 252.0
        sol, tvar, lam = cp._solve_te(alpha, b, S, te_var, upper, w0=w0, kappa=float(cfg.get("te_kappa", 0.0)))
        wfin = pd.Series(sol, index=idx)
        bmap = dict(zip(idx, b))
        for c in sorted(members_now | {h for h in held if m500.get(h, False)}):
            wv = float(wfin.get(c, 0.0)); bi = bmap.get(c, 0.0)
            state = "zero" if wv <= 1e-6 else ("over" if wv > bi * 1.001 else ("under" if wv < bi * 0.999 else "core"))
            emit("csi500", c, bucket_nav * wv, {"state": state, "rank_in_bucket": None})
        plan["csi500"] = {"book": "tebudget", "members": len(members_now), "solved": len(idx), "nonzero": int((sol > 1e-6).sum()), "te_exante_ann": float(np.sqrt(max(tvar, 0) * 252)), "lambda": lam,
                          "held_before": sum(1 for h in held if m500.get(h, False)), "bucket_nav": bucket_nav, "cap_field": cfg.get("cap_field", "float_market_cap"),
                          "te_ann": cfg.get("te_ann", 0.04), "te_kappa": cfg.get("te_kappa", 0.0), "te_max_tilt": cfg.get("te_max_tilt")}
    elif cfg.get("book", "concentrated") == "enhanced":
        win = sc.iloc[-state_window:]
        mwin = (base & mem["csi500"].reindex(index=pred.index)).loc[win.index].to_numpy(bool)
        capwin = panel[cfg.get("cap_field", "float_market_cap")].reindex(index=win.index, columns=panel.codes).to_numpy(np.float64)
        if cfg.get("cap_mult"):                  # A17 (opt-in): implied index-weight multipliers, date x code, missing = 1 (same as competition.two_bucket_book)
            cm = pd.read_parquet(cfg["cap_mult"]); cm.index = pd.DatetimeIndex(cm.index)
            capwin = capwin * cm.reindex(index=win.index, columns=panel.codes).ffill().fillna(1.0).to_numpy(np.float64)
        r = cp.tilted_bucket_book(win.to_numpy(np.float32), np.zeros(win.shape), mwin, np.ones(win.shape, bool), capwin, win.index, gamma=float(cfg["gamma"]),
                                  x_out=float(cfg["x_out"]), x_in=float(cfg["x_in"]), y_in=float(cfg["y_in"]), y_out=float(cfg["y_out"]), tau=float(cfg["tau"]), return_final=True)
        wfin = pd.Series(r["final_weights"], index=panel.codes); excl = pd.Series(r["final_excluded"], index=panel.codes); over = pd.Series(r["final_overweight"], index=panel.codes)
        members_now = set(m500[m500].index)
        for c in sorted(members_now | {h for h in held if m500.get(h, False)}):
            emit("csi500", c, bucket_nav * float(wfin.get(c, 0.0)), {"state": "excluded" if excl.get(c, False) else ("overweight" if over.get(c, False) else "core"), "rank_in_bucket": None})
        plan["csi500"] = {"book": "enhanced", "members": len(members_now), "excluded": int(excl.sum()), "overweight": int(over.sum()), "held_before": sum(1 for h in held if m500.get(h, False)),
                          "after": int((wfin > 0).sum()), "bucket_nav": bucket_nav, "state_window": state_window, "cap_field": cfg.get("cap_field", "float_market_cap")}
    else:
        n_enter = int(cfg["n500"]); concentrated("csi500", m500, n_enter, int(round(float(cfg["keep_mult"]) * n_enter)), bucket_nav)
    # ---- other-union bucket (concentrated)
    wo = float(cfg["woth"]); n_enter = int(cfg["noth"])
    concentrated("other", moth, n_enter, int(round(float(cfg["keep_mult"]) * n_enter)), wo * nav)
    # ---- names held outside the union (dropped constituents, ST) must be sold
    covered = {r["code"] for r in out_rows}
    for c in held:
        if c not in covered:
            emit("outside", c, 0.0, {"state": None, "rank_in_bucket": None})
    df = pd.DataFrame(out_rows)
    df["is_st"] = df["code"].map(lambda c: bool(is_st.get(c, 0) > 0))
    # lot-aware execution targets: shares in whole lots at the last close (reference price; the open decides the real
    # quantity), a target below half a lot is dropped, the freed value is spread over the other names of the same
    # bucket so that the bucket weights (and the 82% / 92% checks) hold after rounding
    held_sh = {r["code"]: float(r.get("shares", np.nan)) for _, r in hold.iterrows()} if len(hold) else {}
    df["exec_shares"] = 0.0
    for b in df["bucket"].unique():
        sel = df["bucket"] == b
        tgt = df.loc[sel, "target_mv"].to_numpy(np.float64); px = df.loc[sel, "close"].to_numpy(np.float64)
        lot_mv = lots * px
        keep = tgt >= 0.5 * lot_mv
        dropped = tgt[~keep].sum()
        budget = float(tgt.sum())                      # the bucket's full target value, before sub-lot names are dropped
        tgt0 = tgt.copy()
        tgt = np.where(keep, tgt, 0.0)
        sh = np.where(keep & np.isfinite(px) & (px > 0), np.round(tgt / np.maximum(lot_mv, 1e-9)) * lots, 0.0)
        # A17 item 4 (opt-in, cfg['lot_group_below']): the sub-lot names of the enhanced bucket (high-priced stocks at a small
        # account) are not all dropped; band by band single lots are bought - held names first, then the largest index
        # weights - until the band's value is spent, so the book does not bet against the high-priced group (same rule as
        # competition.round_lots(group_below=True))
        group_mode = bool(cfg.get("lot_group_below", False)) and b == "csi500" and cfg.get("book", "concentrated") != "concentrated"
        if group_mode:
            held_b = df.loc[sel, "held_mv"].to_numpy(np.float64)
            below = (~keep) & (tgt0 > 0) & np.isfinite(px) & (px > 0)
            v_grp = 0.0; grp = np.zeros(len(tgt), bool)
            for lo_, hi_ in ((0.0, 80.0), (80.0, 160.0), (160.0, 320.0), (320.0, np.inf)):
                gb = below & (px >= lo_) & (px < hi_)
                if not gb.any():
                    continue
                v_grp += float(tgt0[gb].sum())
                key = np.where(gb, tgt0 + np.where(held_b > 0, 1e15, 0.0), -np.inf)
                for j in np.argsort(-key):
                    if not gb[j]:
                        break
                    if v_grp >= (0.2 if held_b[j] > 0 else 0.6) * lot_mv[j]:
                        sh[j] = lots; grp[j] = True; v_grp -= lot_mv[j]
            tgt = np.where(grp, lot_mv, tgt); dropped = max(v_grp, 0.0)
        # A15 item 10: the dropped sub-lot value buys at most one extra lot per kept name (largest targets first) instead of
        # a proportional re-scaling, which compounds into a few names over consecutive days
        remaining = float(dropped) if (b == "csi500" and cfg.get("book", "concentrated") != "concentrated") else 0.0
        if remaining > 0 and keep.any():
            for j in np.argsort(-np.where(keep, tgt, -np.inf)):
                if not keep[j]:
                    break
                if np.isfinite(lot_mv[j]) and lot_mv[j] > 0 and remaining >= lot_mv[j]:
                    sh[j] += lots; remaining -= lot_mv[j]
        # never spend more than the bucket's budget: peel single lots off the names with the largest rounding excess
        while (np.nansum(sh * px) if group_mode else (sh * px).sum()) > budget + 1e-6:
            excess = np.where(sh > 0, sh * px - tgt, -np.inf)
            if group_mode:                             # price-neutral peel: largest rounding excess in lots, not in CNY
                excess = np.where(sh > 0, excess / np.maximum(lot_mv, 1e-9), -np.inf)
            j = int(np.argmax(excess))
            if not np.isfinite(excess[j]):
                break
            sh[j] -= lots
        df.loc[sel, "exec_shares"] = sh
    # a held name keeps its actual share count when the action is hold
    codes_held = df["code"].map(lambda c: held_sh.get(c, np.nan))
    hold_rows = (df["action"] == "hold") & codes_held.notna()
    df.loc[hold_rows, "exec_shares"] = codes_held[hold_rows]
    # the hold override can push a bucket over its budget: peel lots off tradable names until it fits (review fix M2)
    for b in df["bucket"].unique():
        sel = (df["bucket"] == b).to_numpy()
        budget = float(df.loc[sel, "target_mv"].sum())
        px = df.loc[sel, "close"].to_numpy(np.float64); sh = df.loc[sel, "exec_shares"].to_numpy(np.float64).copy(); tgt = df.loc[sel, "target_mv"].to_numpy(np.float64)
        adjustable = ~hold_rows.to_numpy()[sel]
        while np.nansum(sh * px) > budget * 1.002 + 1e-6:
            excess = np.where(adjustable & (sh > 0), sh * px - tgt, -np.inf)
            j = int(np.argmax(excess))
            if not np.isfinite(excess[j]):
                break
            sh[j] -= lots
        df.loc[sel, "exec_shares"] = sh
    df["exec_mv"] = df["exec_shares"] * df["close"]
    df["delta_mv"] = df["exec_mv"] - df["held_mv"]
    df["delta_shares"] = df["exec_shares"] - df["code"].map(lambda c: held_sh.get(c, 0.0) if np.isfinite(held_sh.get(c, np.nan)) else 0.0)
    df.loc[(df["action"] != "hold") & (df["exec_mv"] <= 0) & (df["held_mv"] > 0), "action"] = "sell"
    df.loc[(df["action"] == "buy") & (df["exec_shares"] <= 0) & (df["held_mv"] <= 0), "action"] = "skip_lot"
    df = df.sort_values(["bucket", "action", "exec_mv"], ascending=[True, True, False])
    is500 = {c: bool(m500.get(c, False)) for c in df["code"]}
    chk = cp.precheck({r["code"]: r["held_mv"] for _, r in df.iterrows() if r["held_mv"] > 0}, {r["code"]: r["exec_mv"] for _, r in df.iterrows() if r["exec_mv"] > 0}, nav, is500)
    tgt_tot = df["exec_mv"].sum(); tgt_500 = df.loc[df["code"].map(is500), "exec_mv"].sum()
    trades = df[df["action"].isin(["buy", "sell", "trim"])]
    summary = {"asof": str(t.date()), "nav": nav, "membership": mnote, "cfg": cfg, "plan": plan, "target_invested": tgt_tot / nav, "target_share500": tgt_500 / nav,
               "target_max_single": float(df["exec_mv"].max() / nav) if len(df) else 0.0, "gross_traded": float(trades["delta_mv"].abs().sum() / nav), "n_orders": int(len(trades)),
               "n_skipped_below_half_lot": int((df["action"] == "skip_lot").sum()), "lot_rounding_abs_error": float((df["exec_mv"] - df["target_mv"]).abs().sum() / max(df["target_mv"].sum(), 1)),
               "n_buy": int((trades["action"] == "buy").sum()), "n_sell": int((trades["action"] == "sell").sum()), "n_trim": int((trades["action"] == "trim").sum()),
               "precheck": chk, "style_exposure": style, "prediction": prediction_path, "panel_dir": panel_dir, "generated": time.strftime("%Y-%m-%dT%H:%M:%S")}
    os.makedirs(out_dir, exist_ok=True)
    tag = t.strftime("%Y%m%d")
    df.to_csv(os.path.join(out_dir, f"target_{tag}.csv"), index=False, encoding="utf-8-sig")
    with open(os.path.join(out_dir, f"target_{tag}.json"), "w", encoding="utf-8") as fh:
        json.dump(summary, fh, ensure_ascii=False, indent=1, default=float)
    return summary


# ----------------------------------------------------------------------------- CLI
def main(argv=None):
    ap = argparse.ArgumentParser(); sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("build-panel"); s.add_argument("--source", required=True); s.add_argument("--out", required=True); s.add_argument("--instruments", required=True)
    s.add_argument("--start", required=True); s.add_argument("--end", default=None)
    s = sub.add_parser("ranks"); s.add_argument("--research-root", default=r"F:/A_Layer_Research"); s.add_argument("--panel-dir", required=True); s.add_argument("--members-file", required=True)
    s.add_argument("--index", type=int, default=0); s.add_argument("--out", required=True); s.add_argument("--universe", default="union"); s.add_argument("--overwrite", action="store_true")
    s.add_argument("--shard", default=None, help="i/n: compute only members[i::n] (run n processes in parallel on the same out dir)")
    s = sub.add_parser("check-ranks"); s.add_argument("--dir-a", required=True); s.add_argument("--panel-a", required=True); s.add_argument("--dir-b", required=True); s.add_argument("--panel-b", required=True)
    s.add_argument("--members-file", required=True); s.add_argument("--index", type=int, default=0); s.add_argument("--max-members", type=int, default=60); s.add_argument("--out", default=None)
    s = sub.add_parser("train-final"); s.add_argument("--panel-dir", required=True); s.add_argument("--ranks-dir", required=True); s.add_argument("--members-file", required=True); s.add_argument("--index", type=int, default=0)
    s.add_argument("--out", required=True); s.add_argument("--universe", default="union"); s.add_argument("--label-blend", nargs="*", type=int, default=[5, 10, 20]); s.add_argument("--train-from", type=int, default=2016)
    s.add_argument("--day-step", type=int, default=4); s.add_argument("--seed", type=int, default=0); s.add_argument("--params", default=None)
    s.add_argument("--label-neutral-field", default=None, help="re-rank the training label inside groups of this style field (turnover20 / log_cap / vol20 / mom20)"); s.add_argument("--label-neutral-q", type=int, default=5)
    s = sub.add_parser("score"); s.add_argument("--panel-dir", required=True); s.add_argument("--ranks-dir", required=True); s.add_argument("--model-dir", required=True); s.add_argument("--out", required=True); s.add_argument("--start", default=None)
    s = sub.add_parser("target"); s.add_argument("--panel-dir", required=True); s.add_argument("--prediction", required=True); s.add_argument("--holdings", default=None); s.add_argument("--nav", type=float, required=True)
    s.add_argument("--out", required=True); s.add_argument("--asof", default=None); s.add_argument("--cfg", default=None, help="json overrides of competition.DEFAULT_CFG")
    s.add_argument("--allow-history", action="store_true", help="allow an asof before the panel's last date (dry runs only)")
    a = ap.parse_args(argv)
    if a.cmd == "build-panel":
        print(json.dumps(build_panel_window(a.source, a.out, a.instruments, a.start, a.end), ensure_ascii=False))
    elif a.cmd == "ranks":
        members = load_members(a.members_file, a.index)
        if a.shard:
            i, n = (int(x) for x in a.shard.split("/")); members = members[i::n]
        print(json.dumps(compute_ranks(a.research_root, a.panel_dir, members, a.out, a.universe, a.overwrite), ensure_ascii=False))
    elif a.cmd == "check-ranks":
        r = check_ranks(a.dir_a, a.panel_a, a.dir_b, a.panel_b, load_members(a.members_file, a.index), a.max_members)
        print(json.dumps(r, ensure_ascii=False, indent=1))
        if a.out:
            with open(a.out, "w", encoding="utf-8") as fh:
                json.dump(r, fh, ensure_ascii=False, indent=1)
    elif a.cmd == "train-final":
        print(json.dumps({k: v for k, v in train_final(a.panel_dir, a.ranks_dir, load_members(a.members_file, a.index), a.out, a.universe, tuple(a.label_blend), a.train_from, a.day_step, a.seed,
                                                       json.loads(a.params) if a.params else None, label_neutral_field=a.label_neutral_field, label_neutral_q=a.label_neutral_q).items() if k != "features"}, ensure_ascii=False, indent=1))
    elif a.cmd == "score":
        print(json.dumps(score(a.panel_dir, a.ranks_dir, a.model_dir, a.out, a.start), ensure_ascii=False))
    elif a.cmd == "target":
        r = build_target(a.panel_dir, a.prediction, a.holdings, a.nav, json.loads(a.cfg) if a.cfg else None, a.out, a.asof, allow_history=a.allow_history)
        print(json.dumps({k: v for k, v in r.items() if k not in ("cfg",)}, ensure_ascii=False, indent=1, default=float))


if __name__ == "__main__":
    main()
