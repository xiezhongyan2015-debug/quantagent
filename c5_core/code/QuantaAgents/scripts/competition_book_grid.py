"""Enhanced-index book grid for the CSI500 bucket (2019-2024 only; the other bucket stays top-25 concentrated).
python scripts/competition_book_grid.py --panel-dir /Users/leslie/Downloads/中证500量化60min/c5_core/data/A_Layer_Research/panel --prediction <pred> --out <csv>
"""
import argparse, itertools, os, sys, time
import numpy as np, pandas as pd
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
from quanta_agents.factor_lab_a.panel import Panel
from quanta_agents.factor_lab_a import competition as cp


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--panel-dir", required=True); ap.add_argument("--prediction", required=True)
    ap.add_argument("--years", nargs="*", type=int, default=[2019, 2020, 2021, 2022, 2023, 2024]); ap.add_argument("--out", required=True)
    ap.add_argument("--gamma", nargs="*", type=float, default=[1.0, 0.5, 0.0]); ap.add_argument("--x-out", nargs="*", type=float, default=[0.0, 0.2, 0.3, 0.4])
    ap.add_argument("--tau", nargs="*", type=float, default=[0.0, 1.0, 2.0]); ap.add_argument("--smooth", nargs="*", type=int, default=[5]); ap.add_argument("--cost", type=float, default=0.002)
    ap.add_argument("--band", type=float, default=0.15, help="hysteresis width: x_in = x_out + band, y_out = 0.9 - band")
    a = ap.parse_args()
    panel = Panel(a.panel_dir); panel._dtype = np.float32
    pred = pd.read_parquet(a.prediction); pred.index = pd.DatetimeIndex(pred.index)
    mem, mnote = cp.membership(panel); bench1, bnote = cp.benchmark_label1(panel.dates, "csi500")
    rows = []; t0 = time.time()
    combos = list(itertools.product(a.gamma, a.x_out, a.tau, a.smooth))
    for i, (g, xo, tau, sm) in enumerate(combos):
        cfg = dict(cp.DEFAULT_CFG, book="enhanced", gamma=g, x_out=xo, x_in=min(xo + a.band, 0.95) if xo > 0 else 0.0, y_in=0.9, y_out=0.9 - a.band, tau=tau, smooth=sm, cost=a.cost)
        book = cp.two_bucket_book(pred, panel, mem, bench1, cfg)
        s = cp.summarize_book(book, tuple(a.years), a.cost)
        rows.append({"gamma": g, "x_out": xo, "tau": tau, "smooth": sm, "gross": s["gross_excess_ann"], "net": s["net_excess_ann"], "sharpe": s["net_sharpe"], "turnover": s["daily_turnover"],
                     "mdd": s["max_drawdown_net"], "worst_year": s["worst_year_net"], "years_pos": s["years_net_positive"], "names500": s["avg_names_500"],
                     "w10_mean": s["w10"]["mean"], "w10_p10": s["w10"]["p10"], "w10_share_pos": s["w10"]["share_pos"], "w10_nonov_share_pos": s["w10"]["nonoverlap_share_pos"],
                     **{f"y{y}": s["annual"][str(y)]["net_excess"] for y in a.years if str(y) in s["annual"]}})
        print(f"{i + 1}/{len(combos)} gamma={g} x_out={xo} tau={tau} smooth={sm}: net {s['net_excess_ann'] * 100:+.1f}% sh {s['net_sharpe']:.2f} turn {s['daily_turnover'] * 100:.2f}% mdd {s['max_drawdown_net'] * 100:.1f}% w10>0 {s['w10']['share_pos'] * 100:.0f}% names {s['avg_names_500']:.0f} ({time.time() - t0:.0f}s)", flush=True)
    df = pd.DataFrame(rows).sort_values("sharpe", ascending=False)
    df.to_csv(a.out, index=False, encoding="utf-8-sig")
    print(df.head(20).round(4).to_string(index=False)); print(a.out)


if __name__ == "__main__":
    main()
