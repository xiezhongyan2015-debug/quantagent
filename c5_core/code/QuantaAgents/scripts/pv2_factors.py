"""A15 price-volume families for the CSI500 bucket (items 2-5): register DSL candidates, evaluate them under the
all-A pool rules, write direction-applied rank arrays on any panel, build member lists, and turn one expression into
a standalone "second score" (direction fixed on the training years inside a chosen universe).

python scripts/pv2_factors.py register --item 2            # add the item's expressions to the research ledger
python scripts/pv2_factors.py evaluate --item 2            # single-factor evaluation + pool admission (FLA_WORKERS env)
python scripts/pv2_factors.py ranks --item 2 --panel-dir /Users/leslie/Downloads/中证500量化60min/c5_core/data/A_Layer_Research/panel --out /Users/leslie/Downloads/中证500量化60min/c5_core/data/A_Layer_Research/competition/pv2/ranks_research
python scripts/pv2_factors.py members --item 2 --admitted-only --out /Users/leslie/Downloads/中证500量化60min/c5_core/data/A_Layer_Research/competition/pv2/members/m396_plus_item2.json
python scripts/pv2_factors.py score --expr "..." --universe csi500 --panel-dir ... --out <parquet> [--direction-from <json>]
python scripts/pv2_factors.py table --item 2               # summary table of the item's candidates (all-A + CSI500 stats)
"""
from __future__ import annotations
import argparse
import json
import os
import sys
import time
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
from quanta_agents.factor_lab_a.ledger import Store  # noqa: E402
from quanta_agents.factor_lab_a.dsl import Compiler  # noqa: E402

ROOT = r"/Users/leslie/Downloads/中证500量化60min/c5_core/data/A_Layer_Research"
PV2 = os.path.join(ROOT, "competition", "pv2")
MEMBERS_396 = os.path.join(ROOT, "batches", "combinations_v11_396_blend51020.json")
XLIM = "where(ret > 0.095, 0, log(1 + ret))"   # daily log return with limit-up-sized days (> 9.5%) zeroed

ITEMS = {
    2: {  # momentum family
        "pv2_mom60": ("close / lag(close, 60) - 1", "medium_momentum", "60-day momentum"),
        "pv2_mom120": ("close / lag(close, 120) - 1", "medium_momentum", "120-day momentum"),
        "pv2_mom120_20": ("lag(close, 20) / lag(close, 120) - 1", "medium_momentum", "6-1 month momentum (classic mom_120_20)"),
        "pv2_mom250_20": ("lag(close, 20) / lag(close, 250) - 1", "medium_momentum", "12-1 month momentum"),
        "pv2_mom20_xlim": (f"rolling_sum({XLIM}, 20)", "medium_momentum", "20-day momentum with limit-up-sized days zeroed"),
        "pv2_mom60_xlim": (f"rolling_sum({XLIM}, 60)", "medium_momentum", "60-day momentum with limit-up-sized days zeroed"),
        "pv2_mom120_xlim": (f"rolling_sum({XLIM}, 120)", "medium_momentum", "120-day momentum with limit-up-sized days zeroed"),
        "pv2_mom20_tn": ("cs_neutralize(close / lag(close, 20) - 1, cs_rank(rolling_mean(turnover, 20)))", "medium_momentum", "20-day momentum, turnover-neutralised"),
        "pv2_mom60_tn": ("cs_neutralize(close / lag(close, 60) - 1, cs_rank(rolling_mean(turnover, 60)))", "medium_momentum", "60-day momentum, turnover-neutralised"),
        "pv2_mom120_tn": ("cs_neutralize(close / lag(close, 120) - 1, cs_rank(rolling_mean(turnover, 120)))", "medium_momentum", "120-day momentum, turnover-neutralised"),
        "pv2_mom60_xlim_tn": (f"cs_neutralize(rolling_sum({XLIM}, 60), cs_rank(rolling_mean(turnover, 60)))", "medium_momentum", "60-day ex-limit momentum, turnover-neutralised"),
        "pv2_mom120_q5turn": ("cs_rank_within(close / lag(close, 120) - 1, rolling_mean(turnover, 20), 5)", "medium_momentum", "120-day momentum ranked inside turnover quintiles"),
    },
    3: {  # one-month reversal inside turnover quintiles (+ blends with momentum)
        "pv2_rev20_q5turn": ("cs_rank_within(-(close / lag(close, 20) - 1), rolling_mean(turnover, 20), 5)", "short_reversal", "20-day reversal ranked inside turnover quintiles"),
        "pv2_rev20_xlim_q5turn": (f"cs_rank_within(-rolling_sum({XLIM}, 20), rolling_mean(turnover, 20), 5)", "short_reversal", "20-day ex-limit reversal ranked inside turnover quintiles"),
        "pv2_rev20_tn": ("cs_neutralize(-(close / lag(close, 20) - 1), cs_rank(rolling_mean(turnover, 20)))", "short_reversal", "20-day reversal, turnover-neutralised (linear)"),
        "pv2_rev20q5_mom120_blend": ("0.5 * cs_rank_within(-(close / lag(close, 20) - 1), rolling_mean(turnover, 20), 5) + 0.5 * cs_rank(lag(close, 20) / lag(close, 120) - 1)", "short_reversal", "equal-weight blend: turnover-quintile reversal + 6-1 momentum"),
        "pv2_rev20q5_mom250_blend": ("0.5 * cs_rank_within(-(close / lag(close, 20) - 1), rolling_mean(turnover, 20), 5) + 0.5 * cs_rank(lag(close, 20) / lag(close, 250) - 1)", "short_reversal", "equal-weight blend: turnover-quintile reversal + 12-1 momentum"),
    },
    4: {  # overnight vs intraday decomposition
        **{f"pv2_overnight{n}": (f"rolling_sum(log(open / prev_close), {n})", "intraday_overnight", f"{n}-day cumulative overnight return") for n in (5, 20, 60)},
        **{f"pv2_intraday{n}": (f"rolling_sum(log(close / open), {n})", "intraday_overnight", f"{n}-day cumulative intraday return") for n in (5, 20, 60)},
        **{f"pv2_id_minus_on{n}": (f"rolling_sum(log(close / open) - log(open / prev_close), {n})", "intraday_overnight", f"{n}-day intraday minus overnight") for n in (5, 20, 60)},
        **{f"pv2_on_share{n}": (f"rolling_sum(log(open / prev_close), {n}) / rolling_sum(abs(log(open / prev_close)) + abs(log(close / open)), {n})", "intraday_overnight", f"{n}-day overnight share of absolute moves") for n in (5, 20, 60)},
    },
    5: {  # volume structure
        "pv2_turn_10_120": ("rolling_mean(turnover, 10) / rolling_mean(turnover, 120)", "abnormal_turnover", "10/120-day turnover ratio"),
        "pv2_pv_div_prod20": ("(close / lag(close, 20) - 1) * (rolling_mean(turnover, 20) / lag(rolling_mean(turnover, 20), 20) - 1)", "price_volume_corr", "price change x turnover change (20d)"),
        "pv2_pv_div_rank20": ("cs_rank(close / lag(close, 20) - 1) - cs_rank(rolling_mean(turnover, 20) / lag(rolling_mean(turnover, 20), 20) - 1)", "price_volume_corr", "rank(price change) - rank(turnover change), 20d"),
        "pv2_vol_spike20": ("rolling_sum(turnover > 2 * lag(rolling_mean(turnover, 60), 1), 20)", "abnormal_turnover", "days in 20 with turnover > 2x trailing 60-day mean"),
        "pv2_vol_spike60": ("rolling_sum(turnover > 2 * lag(rolling_mean(turnover, 60), 1), 60)", "abnormal_turnover", "days in 60 with turnover > 2x trailing 60-day mean"),
        "pv2_amp20": ("rolling_mean((high - low) / prev_close, 20)", "low_volatility", "20-day mean amplitude"),
        "pv2_upshadow20": ("rolling_mean((high - max(open, close)) / prev_close, 20)", "close_location", "20-day mean upper shadow"),
        "pv2_lowshadow20": ("rolling_mean((min(open, close) - low) / prev_close, 20)", "close_location", "20-day mean lower shadow"),
        "pv2_shadow_diff20": ("rolling_mean(((high - max(open, close)) - (min(open, close) - low)) / prev_close, 20)", "close_location", "20-day mean upper minus lower shadow"),
        "pv2_limup_touch20": ("rolling_sum(high / prev_close - 1 > 0.095, 20)", "lottery_maxret", "days in 20 touching a 10% limit-up"),
        "pv2_limdn_touch20": ("rolling_sum(low / prev_close - 1 < -0.095, 20)", "lottery_maxret", "days in 20 touching a 10% limit-down"),
        "pv2_limup_close20": ("rolling_sum(ret > 0.095, 20)", "lottery_maxret", "days in 20 closing at a 10% limit-up"),
        "pv2_limup_touch60": ("rolling_sum(high / prev_close - 1 > 0.095, 60)", "lottery_maxret", "days in 60 touching a 10% limit-up"),
    },
}


def _log(msg):
    print(time.strftime("%H:%M:%S"), msg, flush=True)


def item_ids(store: Store, item: int) -> dict:
    """{id: candidate} of the item's registered candidates (by canonical form)."""
    comp = Compiler()
    canon = {comp.canonical(e): n for n, (e, _, _) in ITEMS[item].items()}
    return {c["id"]: c for c in store.candidates() if c.get("canonical") in canon}


def cmd_register(a):
    store = Store(a.root); comp = Compiler()
    batch = store.state().get("batch", 0)
    existing = store.by_canonical()
    added = 0
    for name, (expr, mech, note) in ITEMS[a.item].items():
        canon = comp.canonical(expr)
        if canon in existing:
            c = existing[canon]; r = store.load_result(c["id"]) or {}
            _log(f"exists  {name:24s} as {c.get('name')} ({c.get('source')}) id {c['id']} pool={r.get('in_pool')} ic={r.get('target_mean_rank_ic')}")
            continue
        rec = {"id": comp.identity(canon), "expression": expr, "canonical": canon, "name": name, "source": f"pv2_item{a.item}", "batch": batch,
               "mechanism_id": mech, "hypothesis": f"A15 item {a.item}: {note}", "status": "pending"}
        added += store.add_candidate(rec); _log(f"added   {name:24s} id {rec['id']}")
    _log(f"item {a.item}: {added} new candidates")


def cmd_evaluate(a):
    from quanta_agents.factor_lab_a.cli import get_ctx, evaluate_pending
    store, proto, panel, compiler, ev = get_ctx(a.root)
    ids = list(item_ids(store, a.item))
    pending = [i for i in ids if not store.has_result(i)]
    _log(f"item {a.item}: {len(ids)} candidates, {len(pending)} pending")
    if pending:
        evaluate_pending(store, proto, panel, compiler, ev, ids=set(pending), workers=a.workers)


def cmd_ranks(a):
    from quanta_agents.factor_lab_a.live import compute_ranks
    store = Store(a.root)
    ids = list(item_ids(store, a.item)) if a.item else json.load(open(a.ids_file, encoding="utf-8"))["members"]
    ids = [i for i in ids if (store.load_result(i) or {}).get("direction")]
    print(json.dumps(compute_ranks(a.root, a.panel_dir, ids, a.out, universe="all_a", overwrite=a.overwrite), ensure_ascii=False))


def cmd_members(a):
    store = Store(a.root)
    base = json.load(open(MEMBERS_396, encoding="utf-8"))[0]["features"]
    new = []
    for item in a.item:
        for cid, c in item_ids(store, item).items():
            r = store.load_result(cid) or {}
            if not r.get("direction"):
                continue
            if a.admitted_only and not r.get("in_pool"):
                continue
            if a.ids and cid not in a.ids:
                continue
            new.append(cid)
    new = [n for n in dict.fromkeys(new) if n not in set(base)]
    out = {"tag": os.path.splitext(os.path.basename(a.out))[0], "base": "combinations_v11_396_blend51020", "items": a.item, "admitted_only": a.admitted_only,
           "n": len(base) + len(new), "new_members": new, "members": list(base) + new}
    with open(a.out, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1)
    _log(f"{a.out}: {len(base)} + {len(new)} members")


def cmd_score(a):
    """Standalone score from one expression: direction fixed on the training years (daily RankIC vs label_5 inside
    `universe`), value = direction x expression, NaN outside the eligibility mask."""
    from quanta_agents.factor_lab_a.panel import Panel
    from quanta_agents.factor_lab_a.evaluate import rank_ic
    panel = Panel(a.panel_dir); panel._dtype = np.float32
    comp = Compiler(resolver=lambda n: panel[n])
    f = comp.evaluate(a.expr)
    mask = panel.mask(a.universe) & (panel["eval_ok"] > 0)
    if a.direction_from and os.path.exists(a.direction_from):
        d = json.load(open(a.direction_from, encoding="utf-8"))["direction"]; t = None
    else:
        r, n = rank_ic(f, panel["label_5"], mask, min_stocks=50)
        tr = r[np.isin(r.index.year, a.train_years)].dropna()
        d = int(np.sign(tr.mean())); t = float(tr.mean() / tr.std(ddof=1) * np.sqrt(len(tr)))
    out = (f * d).where(panel.mask("all_a")).astype(np.float32)
    out.to_parquet(a.out)
    meta = {"expr": a.expr, "universe": a.universe, "train_years": a.train_years, "direction": d, "train_t": t, "panel_dir": a.panel_dir, "out": a.out}
    with open(os.path.splitext(a.out)[0] + ".json", "w", encoding="utf-8") as fh:
        json.dump(meta, fh, ensure_ascii=False, indent=1)
    print(json.dumps(meta, ensure_ascii=False))


def cmd_table(a):
    store = Store(a.root)
    me = None
    if a.member_eval and os.path.exists(a.member_eval):
        me = pd.read_csv(a.member_eval).set_index("id")
    rows = []
    for cid, c in item_ids(store, a.item).items():
        r = store.load_result(cid) or {}
        row = {"id": cid, "name": c.get("name"), "source": c.get("source"), "dir": r.get("direction"), "train_t": r.get("train_t"), "ic": r.get("target_mean_rank_ic"),
               "worst": r.get("target_worst_rank_ic"), "cov": r.get("stock_coverage"), "corr": r.get("max_pool_corr"), "class": r.get("failure_class"), "pool": r.get("in_pool"),
               "top10": r.get("top_excess_target"), "bot10": r.get("bottom_excess_target"), "exp_turn": (r.get("style_exposure") or {}).get("turnover20"), "exp_size": (r.get("style_exposure") or {}).get("size")}
        if me is not None and cid in me.index:
            for k in ("csi500_ic_mean", "csi500_ic_worst", "csi500_ic_t", "csi500_top_mean", "csi500_bot_mean", "csi500_cap_corr", "union_ic_mean", "csi500_coverage"):
                row[k] = me.loc[cid, k] if k in me.columns else None
        rows.append(row)
    df = pd.DataFrame(rows).sort_values("ic", ascending=False)
    pd.set_option("display.width", 320)
    print(df.round(4).to_string(index=False))
    if a.out:
        df.to_csv(a.out, index=False, encoding="utf-8-sig")


def main(argv=None):
    ap = argparse.ArgumentParser(); sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("register", "evaluate", "ranks", "table"):
        s = sub.add_parser(name); s.add_argument("--root", default=ROOT); s.add_argument("--item", type=int, default=None)
        if name == "evaluate":
            s.add_argument("--workers", type=int, default=None)
        if name == "ranks":
            s.add_argument("--panel-dir", required=True); s.add_argument("--out", required=True); s.add_argument("--ids-file", default=None); s.add_argument("--overwrite", action="store_true")
        if name == "table":
            s.add_argument("--member-eval", default=None); s.add_argument("--out", default=None)
    s = sub.add_parser("members"); s.add_argument("--root", default=ROOT); s.add_argument("--item", type=int, nargs="+", required=True); s.add_argument("--admitted-only", action="store_true")
    s.add_argument("--ids", nargs="*", default=None); s.add_argument("--out", required=True)
    s = sub.add_parser("score"); s.add_argument("--panel-dir", required=True); s.add_argument("--expr", required=True); s.add_argument("--universe", default="csi500")
    s.add_argument("--train-years", nargs="*", type=int, default=[2016, 2017, 2018]); s.add_argument("--direction-from", default=None); s.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    {"register": cmd_register, "evaluate": cmd_evaluate, "ranks": cmd_ranks, "members": cmd_members, "score": cmd_score, "table": cmd_table}[a.cmd](a)


if __name__ == "__main__":
    main()
