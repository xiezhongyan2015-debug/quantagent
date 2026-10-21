"""Evaluate one or more prediction files under a set of named book rules and print one comparison table.
python scripts/competition_compare.py --panel-dir /Users/leslie/Downloads/中证500量化60min/c5_core/data/A_Layer_Research/panel --years 2019 2020 2021 2022 2023 2024 \
    --pred "396 all-A=/Users/leslie/Downloads/中证500量化60min/c5_core/data/A_Layer_Research/batches/pred_v11_396_blend51020.parquet" --pred "396 union=..." --out <json>
Rules: --rules baseline enhanced hybrid (names defined in RULES) or --rule-json '{"name": {...cfg...}}'.
"""
import argparse, json, os, sys, time
import numpy as np, pandas as pd
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
from quanta_agents.factor_lab_a.panel import Panel
from quanta_agents.factor_lab_a import competition as cp

RULES = {
    "baseline": {},                                                                        # top-55 / keep 3x / smooth 5 / drift
    "wide80": {"n500": 80, "keep_mult": 2.0, "smooth": 5},
    "enhanced": {"book": "enhanced", "gamma": 1.0, "x_out": 0.2, "x_in": 0.35, "y_in": 0.9, "y_out": 0.75, "tau": 0.0},
    "enhanced_tilt": {"book": "enhanced", "gamma": 0.5, "x_out": 0.3, "x_in": 0.45, "y_in": 0.9, "y_out": 0.75, "tau": 2.0},
    "hybrid": {"book": "enhanced", "gamma": 1.0, "x_out": 0.2, "x_in": 0.35, "y_in": 0.9, "y_out": 0.75, "tau": 9.0},
}


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--panel-dir", required=True); ap.add_argument("--years", nargs="*", type=int, default=[2019, 2020, 2021, 2022, 2023, 2024])
    ap.add_argument("--pred", action="append", required=True, help="label=path"); ap.add_argument("--rules", nargs="*", default=list(RULES)); ap.add_argument("--rule-json", default=None)
    ap.add_argument("--out", default=None); ap.add_argument("--index-source", default=None)
    a = ap.parse_args()
    rules = {k: RULES[k] for k in a.rules}
    if a.rule_json:
        rules.update(json.loads(a.rule_json))
    panel = Panel(a.panel_dir); panel._dtype = np.float32
    mem, mnote = cp.membership(panel); bench1, bnote = cp.benchmark_label1(panel.dates, "csi500", a.index_source)
    print(f"membership: {mnote} | benchmark: {bnote}")
    rows = []; full = {}
    for spec in a.pred:
        label, path = spec.split("=", 1)
        pred = pd.read_parquet(path); pred.index = pd.DatetimeIndex(pred.index)
        ic = cp.rank_ic_by_universe(pred, panel, mem, tuple(a.years))
        for rname, over in rules.items():
            cfg = dict(cp.DEFAULT_CFG, **over)
            t0 = time.time(); book = cp.two_bucket_book(pred, panel, mem, bench1, cfg); s = cp.summarize_book(book, tuple(a.years), cfg["cost"])
            full[f"{label}|{rname}"] = {"cfg": cfg, "book": s, "rank_ic": ic}
            rows.append({"model": label, "rule": rname, "IC_union": ic["union"]["mean"], "IC_union_worst": ic["union"]["worst"], "IC_500": ic["csi500"]["mean"], "gross": s["gross_excess_ann"], "net": s["net_excess_ann"],
                         "sharpe": s["net_sharpe"], "turn": s["daily_turnover"], "mdd": s["max_drawdown_net"], "worst_yr": s["worst_year_net"], "yrs_pos": s["years_net_positive"],
                         "w10_mean": s["w10"]["mean"], "w10_p10": s["w10"]["p10"], "w10_p90": s["w10"]["p90"], "w10_win": s["w10"]["share_pos"], "w10_win_nonov": s["w10"]["nonoverlap_share_pos"],
                         "names500": s["avg_names_500"], **{f"y{y}": s["annual"][str(y)]["net_excess"] for y in a.years if str(y) in s["annual"]}})
            print(f"  {label} / {rname}: net {s['net_excess_ann'] * 100:+.1f}% sh {s['net_sharpe']:.2f} turn {s['daily_turnover'] * 100:.1f}% w10>0 {s['w10']['share_pos'] * 100:.0f}% ({time.time() - t0:.0f}s)", flush=True)
    df = pd.DataFrame(rows)
    pd.set_option("display.width", 260)
    print(df.round(4).to_string(index=False))
    if a.out:
        with open(a.out, "w", encoding="utf-8") as fh:
            json.dump({"years": a.years, "panel_dir": a.panel_dir, "membership": mnote, "benchmark": bnote, "table": df.to_dict("records"), "full": full}, fh, ensure_ascii=False, indent=1, default=float)
        df.to_csv(a.out.replace(".json", ".csv"), index=False, encoding="utf-8-sig")


if __name__ == "__main__":
    main()
