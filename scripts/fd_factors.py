"""A18 step 2: fundamental candidate families — register DSL candidates on the fd_* panel fields, evaluate them under the
all-A pool rules (direction + admission on 2016-2018), write rank arrays on any panel, and build member lists.
Mirrors scripts/pv2_factors.py (A15) so the same downstream tools apply.

python scripts/fd_factors.py register --item 1            # families 1..7 (see ITEMS); --item 0 = all
python scripts/fd_factors.py evaluate --item 0 --workers 6
python scripts/fd_factors.py ranks --item 0 --panel-dir F:/A_Layer_Research/panel --out F:/A_Layer_Research/competition/fd/ranks_research
python scripts/fd_factors.py members --item 1 2 3 4 5 6 7 --admitted-only --out F:/A_Layer_Research/competition/fd/members/m396_plus_fd.json
python scripts/fd_factors.py table --item 0 [--member-eval <csv>]
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

ROOT = r"F:/A_Layer_Research"
FD = os.path.join(ROOT, "competition", "fd")
MEMBERS_396 = os.path.join(ROOT, "batches", "combinations_v11_396_blend51020.json")
T20 = "rolling_mean(turnover, 20)"
COV = "where(fd_rc_n90 > 0, {x}, 0)"   # analyst fields: no coverage -> cross-sectional neutral (0 after cs_zscore/cs_demean)

ITEMS = {
    1: {  # valuation
        "fd1_ep_ttm": ("fd_ep_ttm", "value", "earnings yield ttm"),
        "fd1_ep_dedt": ("fd_ep_dedt", "value", "earnings yield excl. non-recurring"),
        "fd1_bp": ("fd_bp", "value", "book to price"),
        "fd1_sp_ttm": ("fd_sp_ttm", "value", "sales to price"),
        "fd1_dp_ttm": ("fd_dp_ttm", "value", "dividend yield ttm (implemented cash dividends)"),
        "fd1_ocf_ep": ("fd_ocf_ep", "value", "operating cash flow yield"),
        "fd1_ep_q5cap": ("cs_rank_within(fd_ep_ttm, log_cap, 5)", "value", "earnings yield ranked inside size quintiles"),
        "fd1_bp_q5cap": ("cs_rank_within(fd_bp, log_cap, 5)", "value", "book to price ranked inside size quintiles"),
        "fd1_ep_q5turn": (f"cs_rank_within(fd_ep_ttm, {T20}, 5)", "value", "earnings yield ranked inside turnover quintiles"),
    },
    2: {  # profitability & growth
        "fd2_roe_ttm": ("fd_roe_ttm", "profitability", "roe ttm"),
        "fd2_d_roe": ("fd_d_roe_ttm", "profitability", "roe ttm change yoy"),
        "fd2_gm_ttm": ("fd_gm_ttm", "profitability", "gross margin ttm"),
        "fd2_d_gm250": ("delta(fd_gm_ttm, 250)", "profitability", "gross margin change over ~1 year"),
        "fd2_np_q_yoy": ("clip(fd_np_q_yoy, -3, 3)", "growth", "single-quarter net profit yoy (clipped)"),
        "fd2_rev_q_yoy": ("clip(fd_rev_q_yoy, -3, 3)", "growth", "single-quarter revenue yoy (clipped)"),
        "fd2_np_accel": ("clip(fd_np_yoy_accel, -3, 3)", "growth", "net profit yoy acceleration (clipped)"),
        "fd2_sue": ("clip(fd_sue, -5, 5)", "earnings_surprise", "standardised unexpected earnings"),
        "fd2_sue_q5turn": (f"cs_rank_within(clip(fd_sue, -5, 5), {T20}, 5)", "earnings_surprise", "SUE ranked inside turnover quintiles"),
        "fd2_cash_minus_earn": ("fd_ocf_ep - fd_ep_ttm", "earnings_quality", "cash flow yield minus earnings yield"),
    },
    3: {  # quality / financial risk (short-end candidates for the CSI500 exclusion line)
        "fd3_accrual_cf": ("-fd_accrual_cf", "earnings_quality", "negative cash-flow accruals"),
        "fd3_d_noa": ("-fd_d_noa", "earnings_quality", "negative change in net operating assets"),
        "fd3_debt_ib": ("-fd_debt_ib", "leverage", "negative interest-bearing debt ratio"),
        "fd3_goodwill": ("-clip(fd_goodwill_eq, 0, 5)", "leverage", "negative goodwill / equity"),
        "fd3_cur_ratio": ("clip(fd_cur_ratio, 0, 10)", "leverage", "current ratio"),
        "fd3_d_cur_ratio": ("clip(fd_d_cur_ratio, -5, 5)", "leverage", "current ratio change yoy"),
        "fd3_extra_ratio": ("-clip(abs(fd_extra_ratio), 0, 5)", "earnings_quality", "negative share of non-recurring items"),
        "fd3_recv_minus_rev": ("-clip(fd_recv_minus_rev, -3, 3)", "earnings_quality", "negative receivables growth minus revenue growth"),
        "fd3_audit_nonstd": ("-fd_audit_nonstd", "governance", "non-standard audit opinion"),
        "fd3_rpt_age": ("-fd_rpt_age", "governance", "days since the latest report (late reporters)"),
        "fd3_fc_neg": ("-fd_fc_neg", "earnings_surprise", "loss / decline pre-announcement"),
        "fd3_distress_mix": ("cs_rank(-fd_accrual_cf) + cs_rank(-fd_debt_ib) + cs_rank(-clip(fd_goodwill_eq, 0, 5)) + cs_rank(-fd_audit_nonstd) + cs_rank(-fd_fc_neg)",
                             "earnings_quality", "equal-rank mix of five distress signals"),
    },
    4: {  # shareholders & corporate events
        "fd4_hsell_60": ("-fd_hsell_60", "insider", "negative net major-holder reduction, 60 days"),
        "fd4_unlock_30": ("-fd_unlock_30", "supply", "negative share unlocks in the next 30 days"),
        "fd4_unlock_90": ("-fd_unlock_90", "supply", "negative share unlocks in the next 90 days"),
        "fd4_pledge": ("-fd_pledge", "governance", "negative pledge ratio"),
        "fd4_d_pledge60": ("-delta(fd_pledge, 60)", "governance", "negative pledge ratio change, 60 days"),
        "fd4_hnum_chg": ("-clip(fd_hnum_chg, -1, 3)", "holder_structure", "negative shareholder-count growth (retail dispersion)"),
        "fd4_rep_90": ("fd_rep_90", "insider", "buyback announced in 90 days"),
        "fd4_top10f": ("fd_top10f_conc", "holder_structure", "top-10 float holder concentration"),
        "fd4_d_top10f": ("clip(fd_d_top10f, -50, 50)", "holder_structure", "change in top-10 float concentration"),
        "fd4_div_pre30": ("fd_div_pre30", "dividend_event", "dividend proposal in 30 days"),
    },
    5: {  # pre-announcements and express reports (recent only)
        "fd5_fc_pchg": ("clip(fd_fc_pchg, -300, 300)", "earnings_surprise", "latest pre-announcement change midpoint"),
        "fd5_fc_pchg60": ("where(fd_fc_days < 60, clip(fd_fc_pchg, -300, 300), 0)", "earnings_surprise", "pre-announcement midpoint if within 60 days"),
        "fd5_ex_yoy": ("clip(fd_ex_yoy_np, -300, 300)", "earnings_surprise", "latest express-report net profit yoy"),
        "fd5_ex_yoy60": ("where(fd_ex_days < 60, clip(fd_ex_yoy_np, -300, 300), 0)", "earnings_surprise", "express-report yoy if within 60 days"),
    },
    6: {  # analyst consensus (in-house from report_rc)
        "fd6_est_ep": (COV.format(x="cs_zscore(clip(fd_est_ep_fy1, -0.5, 0.5))"), "analyst", "forecast earnings yield, neutral when uncovered"),
        "fd6_est_g": (COV.format(x="cs_zscore(clip(fd_est_g, -3, 5))"), "analyst", "forecast growth vs ttm, neutral when uncovered"),
        "fd6_n90": ("fd_rc_n90", "analyst", "analyst report count, 90 days"),
        "fd6_d_n90": ("delta(fd_rc_n90, 60)", "analyst", "change in report count over 60 days"),
        "fd6_disp": (COV.format(x="-cs_zscore(clip(fd_rc_disp, 0, 3))"), "analyst", "negative forecast dispersion"),
        "fd6_tp_ret": (COV.format(x="cs_zscore(clip(fd_tp_ret, -1, 3))"), "analyst", "target price implied return"),
        "fd6_rating": (COV.format(x="cs_zscore(fd_rc_rating)"), "analyst", "mean rating"),
        "fd6_rating_chg": ("fd_rc_up90 - fd_rc_down90", "analyst", "rating upgrades minus downgrades, 90 days"),
        "fd6_eps_rev": ("fd_rc_epsup90 - fd_rc_epsdown90", "analyst", "EPS raises minus cuts, 90 days"),
        "fd6_eps_rev_ratio": ("(fd_rc_epsup90 - fd_rc_epsdown90) / (fd_rc_n90 + 1)", "analyst", "EPS revision balance per report"),
        "fd6_np_rev60": (COV.format(x="cs_zscore(clip(pct_change(fd_rc_np_fy1, 60), -1, 1))"), "analyst", "consensus net profit revision over 60 days"),
        "fd6_eps_rev20": (COV.format(x="cs_zscore(clip(pct_change(fd_rc_eps_fy1, 20), -1, 1))"), "analyst", "consensus EPS revision over 20 days"),
    },
    7: {  # structure & interactions
        "fd7_free_ratio": ("fd_free_ratio", "holder_structure", "free-float share ratio"),
        "fd7_sue_x_lowturn": (f"cs_rank(clip(fd_sue, -5, 5)) * cs_rank(-{T20})", "earnings_surprise", "SUE x low turnover"),
        "fd7_estg_q5cap": ("cs_rank_within(clip(fd_est_g, -3, 5), log_cap, 5)", "analyst", "forecast growth ranked inside size quintiles"),
        "fd7_value_quality": ("cs_rank(fd_ep_ttm) + cs_rank(fd_roe_ttm) - cs_rank(fd_accrual_cf)", "value", "value + profitability - accruals"),
        "fd7_rev_x_mom": ("cs_rank(fd_rc_epsup90 - fd_rc_epsdown90) * cs_rank(lag(close, 20) / lag(close, 120) - 1)", "analyst", "EPS revisions x 6-1 momentum"),
    },
}


def _log(msg):
    print(time.strftime("%H:%M:%S"), msg, flush=True)


def items_of(item: int):
    return list(ITEMS) if item in (0, None) else [item]


def item_ids(store: Store, item: int) -> dict:
    comp = Compiler()
    canon = {}
    for it in items_of(item):
        canon.update({comp.canonical(e): n for n, (e, _, _) in ITEMS[it].items()})
    return {c["id"]: c for c in store.candidates() if c.get("canonical") in canon}


def cmd_register(a):
    store = Store(a.root); comp = Compiler()
    batch = store.state().get("batch", 0)
    existing = store.by_canonical()
    added = 0
    for it in items_of(a.item):
        for name, (expr, mech, note) in ITEMS[it].items():
            canon = comp.canonical(expr)
            if canon in existing:
                c = existing[canon]; r = store.load_result(c["id"]) or {}
                _log(f"exists  {name:22s} as {c.get('name')} ({c.get('source')}) id {c['id']} pool={r.get('in_pool')} ic={r.get('target_mean_rank_ic')}")
                continue
            rec = {"id": comp.identity(canon), "expression": expr, "canonical": canon, "name": name, "source": f"fd_item{it}", "batch": batch,
                   "mechanism_id": mech, "hypothesis": f"A18 family {it}: {note}", "status": "pending"}
            added += store.add_candidate(rec); existing[canon] = rec; _log(f"added   {name:22s} id {rec['id']}")
    _log(f"{added} new candidates")


def cmd_evaluate(a):
    from quanta_agents.factor_lab_a.cli import get_ctx, evaluate_pending
    store, proto, panel, compiler, ev = get_ctx(a.root)
    ids = list(item_ids(store, a.item))
    pending = [i for i in ids if not store.has_result(i)]
    _log(f"{len(ids)} candidates, {len(pending)} pending")
    if pending:
        evaluate_pending(store, proto, panel, compiler, ev, ids=set(pending), workers=a.workers)


def cmd_ranks(a):
    from quanta_agents.factor_lab_a.live import compute_ranks
    store = Store(a.root)
    ids = list(item_ids(store, a.item)) if a.ids_file is None else json.load(open(a.ids_file, encoding="utf-8"))["members"]
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
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1)
    _log(f"{a.out}: {len(base)} + {len(new)} members")


def cmd_table(a):
    store = Store(a.root)
    me = pd.read_csv(a.member_eval).set_index("id") if a.member_eval and os.path.exists(a.member_eval) else None
    rows = []
    for cid, c in item_ids(store, a.item).items():
        r = store.load_result(cid) or {}
        row = {"id": cid, "name": c.get("name"), "dir": r.get("direction"), "train_t": r.get("train_t"), "ic": r.get("target_mean_rank_ic"),
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
        s = sub.add_parser(name); s.add_argument("--root", default=ROOT); s.add_argument("--item", type=int, default=0)
        if name == "evaluate":
            s.add_argument("--workers", type=int, default=None)
        if name == "ranks":
            s.add_argument("--panel-dir", required=True); s.add_argument("--out", required=True); s.add_argument("--ids-file", default=None); s.add_argument("--overwrite", action="store_true")
        if name == "table":
            s.add_argument("--member-eval", default=None); s.add_argument("--out", default=None)
    s = sub.add_parser("members"); s.add_argument("--root", default=ROOT); s.add_argument("--item", type=int, nargs="+", required=True); s.add_argument("--admitted-only", action="store_true")
    s.add_argument("--ids", nargs="*", default=None); s.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    {"register": cmd_register, "evaluate": cmd_evaluate, "ranks": cmd_ranks, "members": cmd_members, "table": cmd_table}[a.cmd](a)


if __name__ == "__main__":
    main()
