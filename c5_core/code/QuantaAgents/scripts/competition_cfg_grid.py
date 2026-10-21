"""Book-rule grid under the competition rules for one prediction file (2019-2024 only; used to pick the rule).
python scripts/competition_cfg_grid.py --panel-dir /Users/leslie/Downloads/中证500量化60min/c5_core/data/A_Layer_Research/panel --prediction <pred.parquet> --out <csv>
"""
import argparse, itertools, json, os, sys, time
import numpy as np, pandas as pd
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
from quanta_agents.factor_lab_a.panel import Panel
from quanta_agents.factor_lab_a import competition as cp


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--panel-dir", required=True); ap.add_argument("--prediction", required=True)
    ap.add_argument("--years", nargs="*", type=int, default=[2019, 2020, 2021, 2022, 2023, 2024]); ap.add_argument("--out", required=True)
    ap.add_argument("--n500", nargs="*", type=int, default=[40, 55, 80, 110]); ap.add_argument("--noth", nargs="*", type=int, default=[25])
    ap.add_argument("--keep-mult", nargs="*", type=float, default=[1.0, 2.0, 3.0, 4.0]); ap.add_argument("--smooth", nargs="*", type=int, default=[0, 5, 10])
    ap.add_argument("--weighting", nargs="*", default=["drift"]); ap.add_argument("--min-hold", nargs="*", type=int, default=[1])
    ap.add_argument("--cost", type=float, default=0.002)
    a = ap.parse_args()
    panel = Panel(a.panel_dir); panel._dtype = np.float32
    pred = pd.read_parquet(a.prediction); pred.index = pd.DatetimeIndex(pred.index)
    mem, mnote = cp.membership(panel)
    bench1, bnote = cp.benchmark_label1(panel.dates, "csi500")
    rows = []; t0 = time.time()
    combos = list(itertools.product(a.n500, a.noth, a.keep_mult, a.smooth, a.weighting, a.min_hold))
    for i, (n500, noth, km, sm, wt, mh) in enumerate(combos):
        cfg = dict(cp.DEFAULT_CFG, n500=n500, noth=noth, keep_mult=km, smooth=sm, weighting=wt, min_hold=mh, cost=a.cost)
        book = cp.two_bucket_book(pred, panel, mem, bench1, cfg)
        s = cp.summarize_book(book, tuple(a.years), a.cost)
        rows.append({"n500": n500, "noth": noth, "keep_mult": km, "smooth": sm, "weighting": wt, "min_hold": mh, "gross": s["gross_excess_ann"], "net": s["net_excess_ann"],
                     "sharpe": s["net_sharpe"], "turnover": s["daily_turnover"], "mdd": s["max_drawdown_net"], "worst_year": s["worst_year_net"], "years_pos": s["years_net_positive"],
                     "names500": s["avg_names_500"], "w10_mean": s["w10"]["mean"], "w10_p10": s["w10"]["p10"], "w10_share_pos": s["w10"]["share_pos"], "w10_nonov_share_pos": s["w10"]["nonoverlap_share_pos"],
                     **{f"y{y}": s["annual"][str(y)]["net_excess"] for y in a.years if str(y) in s["annual"]}})
        print(f"{i + 1}/{len(combos)} n500={n500} keep={km} smooth={sm} {wt}: net {s['net_excess_ann'] * 100:+.1f}% sh {s['net_sharpe']:.2f} turn {s['daily_turnover'] * 100:.1f}% w10>0 {s['w10']['share_pos'] * 100:.0f}% ({time.time() - t0:.0f}s)", flush=True)
    df = pd.DataFrame(rows).sort_values("sharpe", ascending=False)
    df.to_csv(a.out, index=False, encoding="utf-8-sig")
    print(df.head(15).round(4).to_string(index=False)); print(a.out)


if __name__ == "__main__":
    main()
