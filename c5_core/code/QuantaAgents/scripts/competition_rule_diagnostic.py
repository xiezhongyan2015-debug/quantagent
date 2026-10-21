"""Read-only diagnostic: how the frozen model's scores behave under the competition rules
(universe = CSI300 ∪ CSI500 ∪ CSI1000 constituents, >=82% of NAV in CSI500 names, >=92% invested, single name <=9.5%,
no shorting, benchmark = CSI500 price index). Prints only."""
import os, sys, time
import numpy as np, pandas as pd
sys.path.insert(0, r"/Users/leslie/Downloads/中证500量化60min/c5_core/code/QuantaAgents/src")
from quanta_agents.factor_lab_a.panel import Panel
from quanta_agents.factor_lab_a import portfolio as pf
from quanta_agents.factor_lab_a.oos import buyable_mask
from quanta_agents.factor_lab_a.trading import score_pct, trading_mask, buffer_band_series
from quanta_agents.factor_lab_a.evaluate import rank_ic

QLIB = r"/Users/leslie/Downloads/中证500量化60min/c5_core/data/qlib_data/qlib_bin"
COST = 0.002
W500, WOTH, N500, NOTH = 0.88, 0.08, 55, 25   # bucket weights and target name counts (cash 4%)


def qlib_series(code, field):
    cal = pd.DatetimeIndex(pd.read_csv(os.path.join(QLIB, "calendars", "day.txt"), header=None)[0])
    arr = np.fromfile(os.path.join(QLIB, "features", code, f"{field}.day.bin"), dtype=np.float32)
    start = int(arr[0]); vals = arr[1:]
    return pd.Series(vals, index=cal[start:start + len(vals)])


def membership(panel, uni):
    """Point-in-time membership on the panel grid; the OOS panel lacks the fields, so rebuild from the instruments list
    (coverage to 2026-01-29; later dates carry the last list forward)."""
    if panel.has(f"member_{uni}"):
        return panel[f"member_{uni}"] > 0
    m = pd.read_csv(os.path.join(QLIB, "instruments", f"{uni}.txt"), sep="\t", header=None, names=["code", "start", "end"])
    m["code"] = m["code"].str.upper(); m["start"] = pd.to_datetime(m["start"]); m["end"] = pd.to_datetime(m["end"])
    last = m["end"].max()
    out = np.zeros((len(panel.dates), len(panel.codes)), bool); cidx = {c: i for i, c in enumerate(panel.codes)}
    d = panel.dates.to_numpy()
    for code, g in m.groupby("code"):
        j = cidx.get(code)
        if j is None:
            continue
        col = np.zeros(len(d), bool)
        for a, b in zip(g["start"].to_numpy(), g["end"].to_numpy()):
            bb = np.datetime64("2100-01-01") if b == np.datetime64(last) else b   # carry the last known list forward
            col |= (d >= a) & (d <= bb)
        out[:, j] = col
    return pd.DataFrame(out, index=panel.dates, columns=panel.codes), last


def run(panel_dir, pred_path, periods, tag):
    t0 = time.time()
    panel = Panel(panel_dir); panel._dtype = np.float32
    pred = pd.read_parquet(pred_path); pred.index = pd.DatetimeIndex(pred.index)
    pred = pred.reindex(index=panel.dates, columns=panel.codes)
    dates = panel.dates; codes = panel.codes; yrs = np.array(dates.year)
    mem = {}
    note = ""
    for u in ("csi300", "csi500", "csi1000"):
        r = membership(panel, u)
        if isinstance(r, tuple):
            mem[u], last = r; note = f" (constituent lists carried forward after {last.date()})"
        else:
            mem[u] = r
    union = mem["csi300"] | mem["csi500"] | mem["csi1000"]
    base = trading_mask(panel); mb = buyable_mask(panel, base)
    label1 = panel["label_1"].to_numpy(np.float64); label5 = panel["label_5"]
    idx_open = qlib_series("sh000905", "open").reindex(dates)
    bench = (idx_open.shift(-2) / idx_open.shift(-1) - 1).to_numpy(np.float64)   # same open-to-open timing as label_1
    # all-A book (frozen rule one) for reference, and where its names sit
    pct_all = score_pct(pred, panel, mb, smooth=5)
    top_all = np.isfinite(pct_all) & (pct_all >= 0.9)
    # bucket books: CSI500 names and the other union names, each equal-weight with hysteresis (enter top, hold to 3x)
    m500 = mb & mem["csi500"]; moth = mb & union & ~mem["csi500"]
    n500 = m500.sum(axis=1).to_numpy(); noth = moth.sum(axis=1).to_numpy()
    f500 = float(np.median(N500 / np.maximum(n500[n500 > 0], 1))); foth = float(np.median(NOTH / np.maximum(noth[noth > 0], 1)))
    p500 = score_pct(pred, panel, m500, smooth=5); poth = score_pct(pred, panel, moth, smooth=5)
    s500 = buffer_band_series(p500, label1, m500.to_numpy(bool), dates, top_frac=f500, band=2 * f500, min_hold=1)
    soth = buffer_band_series(poth, label1, moth.to_numpy(bool), dates, top_frac=foth, band=2 * foth, min_hold=1)
    port = W500 * s500["portfolio"] + WOTH * soth["portfolio"]
    turn = W500 * s500["turnover"] + WOTH * soth["turnover"]
    live = s500["live"] & soth["live"] & pd.Series(np.isfinite(bench), index=dates)
    book = {"portfolio": port, "benchmark": pd.Series(bench, index=dates), "excess": port - bench, "turnover": turn, "live": live}
    # pure CSI500 book (100% in 500 names, 96% invested) for comparison; and the equal-weight CSI500 basket vs the index
    port2 = 0.96 * s500["portfolio"]
    book2 = {"portfolio": port2, "benchmark": pd.Series(bench, index=dates), "excess": port2 - bench, "turnover": 0.96 * s500["turnover"], "live": live}
    ew500 = pf.portfolio_series(np.where(m500.to_numpy(bool), 0.5, np.nan).astype(np.float32), label1, m500.to_numpy(bool), dates, top_frac=1.0, hold=1)
    ewb = {"portfolio": ew500["portfolio"], "benchmark": pd.Series(bench, index=dates), "excess": ew500["portfolio"] - bench, "turnover": ew500["turnover"] * 0, "live": live}
    # all-A frozen book measured against the CSI500 index (what the current model would score under the new benchmark, ignoring the universe rule)
    sA = buffer_band_series(pct_all, label1, mb.to_numpy(bool), dates, top_frac=0.1, band=0.2, min_hold=1)
    bookA = {"portfolio": sA["portfolio"], "benchmark": pd.Series(bench, index=dates), "excess": sA["portfolio"] - bench, "turnover": sA["turnover"], "live": sA["live"] & live}
    for years in periods:
        rows = np.isin(yrs, years)
        print(f"== {tag} {years[0]}{'-' + str(years[-1]) if len(years) > 1 else ''}  ({int(rows.sum())} dates){note}")
        u = union.to_numpy(bool)[rows]; c5 = mem['csi500'].to_numpy(bool)[rows]; ta = top_all[rows]
        print("   universe sizes: union %.0f, csi500 %.0f, other-union %.0f | all-A top decile: %.0f%% in union, %.0f%% in csi500" % (
            np.nanmean(u.sum(axis=1)), np.nanmean(c5.sum(axis=1)), np.nanmean((u & ~c5).sum(axis=1)), 100 * ta[u].sum() / max(ta.sum(), 1), 100 * ta[c5].sum() / max(ta.sum(), 1)))
        ics = {}
        for name, msk in (("all-A", base), ("union", base & union), ("csi300", base & mem["csi300"]), ("csi500", base & mem["csi500"]), ("csi1000", base & mem["csi1000"])):
            r, n = rank_ic(pred, label5, msk, min_stocks=50)
            ics[name] = float(r[rows].mean())
        print("   RankIC by universe: " + "  ".join(f"{k} {v:.3f}" for k, v in ics.items()))
        for name, b in (("all-A book vs CSI500 index (ignores universe rule)", bookA), ("equal-weight CSI500 basket vs index (no selection)", ewb),
                        ("rule book: 88% top-55 of CSI500 + 8% top-25 other union + 4% cash", book), ("pure CSI500 book: 96% top-55 of CSI500", book2)):
            r = pf.summarize(b, tuple(years), cost_rate=COST)
            ann = " ".join("%s %+.0f%%" % (y, v["net_excess"] * 100) for y, v in r["annual"].items()) if len(years) > 1 else ""
            print("   %-58s gross %+6.1f%%  net %+6.1f%%  sh %5.2f  turn %4.1f%%  mdd %6.1f%%  %s" % (name, r["gross_excess_ann"] * 100, r["net_excess_ann"] * 100, r["net_sharpe"], r["daily_turnover"] * 100, r["max_drawdown_net"] * 100, ann))
        # 10-trading-day cumulative net excess distribution of the rule book (registration-card forecast material)
        ex = (book["excess"] - COST * book["turnover"])[rows & live.to_numpy()]
        w10 = ex.rolling(10).sum().dropna(); w20 = ex.rolling(20).sum().dropna()
        print("   rule book net excess over 10 trading days: mean %+.2f%%  p10 %+.2f%%  p90 %+.2f%%  share>0 %.0f%% | 20 days: mean %+.2f%%  p10 %+.2f%%  p90 %+.2f%%" % (
            w10.mean() * 100, w10.quantile(0.1) * 100, w10.quantile(0.9) * 100, (w10 > 0).mean() * 100, w20.mean() * 100, w20.quantile(0.1) * 100, w20.quantile(0.9) * 100))
        hold = s500["coverage"][rows]; print("   names held in the CSI500 book: mean %.0f  min %.0f | other bucket: mean %.0f" % (hold.mean(), hold.min(), soth["coverage"][rows].mean()))
    print(f"   ({time.time() - t0:.0f}s)", flush=True)


if __name__ == "__main__":
    run("/Users/leslie/Downloads/中证500量化60min/c5_core/data/A_Layer_Research/panel", "/Users/leslie/Downloads/中证500量化60min/c5_core/data/A_Layer_Research/batches/pred_pool_all_597_blend51020.parquet", [tuple(range(2019, 2025))], "research")
    run("/Users/leslie/Downloads/中证500量化60min/c5_core/data/A_Layer_OOS/panel", "/Users/leslie/Downloads/中证500量化60min/c5_core/data/A_Layer_OOS/pool_all_597_blend/prediction_oos.parquet", [(2025,), (2026,)], "forward")
