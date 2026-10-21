"""Re-evaluate pool members inside the competition buckets (step 2 input).
For every rank array in the pool (direction-applied all-A percentiles), per year 2019-2024 and inside CSI500 / union:
RankIC vs label_5, top-decile and bottom-decile label_5 excess vs bucket equal weight (annualised), Spearman of the
member with log float cap inside CSI500 (size loading). Writes one CSV.
python scripts/competition_member_eval.py --out /Users/leslie/Downloads/中证500量化60min/c5_core/data/A_Layer_Research/competition/eval/member_eval_500.csv [--ids-file ...]
"""
import argparse, json, os, sys, time
import numpy as np, pandas as pd
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
from quanta_agents.factor_lab_a.panel import Panel
from quanta_agents.factor_lab_a.ledger import Store
from quanta_agents.factor_lab_a.evaluate import _row_corr_np
from quanta_agents.factor_lab_a import competition as cp


def rank_rows(a):
    return pd.DataFrame(a).rank(axis=1).to_numpy(np.float32)


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--root", default=r"/Users/leslie/Downloads/中证500量化60min/c5_core/data/A_Layer_Research"); ap.add_argument("--panel-dir", default=r"/Users/leslie/Downloads/中证500量化60min/c5_core/data/A_Layer_Research/panel")
    ap.add_argument("--years", nargs="*", type=int, default=[2019, 2020, 2021, 2022, 2023, 2024]); ap.add_argument("--ids-file", default=None); ap.add_argument("--out", required=True)
    ap.add_argument("--step", type=int, default=2, help="evaluate every k-th date (2 = half the dates, halves the run time)")
    ap.add_argument("--ranks-dir", default=None, help="directory of <id>.npy rank arrays (default <root>/pool_ranks)")
    ap.add_argument("--extra-universe", nargs="*", default=[], help="additional bucket masks to evaluate, e.g. csi1000 csi300")
    a = ap.parse_args()
    store = Store(a.root); cands = {c["id"]: c for c in store.candidates()}
    panel = Panel(a.panel_dir); panel._dtype = np.float32
    mem, _ = cp.membership(panel)
    base = (panel["eligible"] > 0) & (panel["eval_ok"] > 0)
    yrs = np.array(panel.dates.year); rows_sel = np.isin(yrs, a.years)
    pos = np.arange(len(panel.dates))[rows_sel][::a.step]; yr = yrs[pos]
    lab = panel["label_5"].to_numpy(np.float64)[pos]
    logcap = panel["log_cap"].to_numpy(np.float64)[pos]
    masks = {"csi500": (base & mem["csi500"]).to_numpy(bool)[pos], "union": (base & mem["union"]).to_numpy(bool)[pos]}
    for u in a.extra_universe:
        masks[u] = (base & mem[u]).to_numpy(bool)[pos]
    lab_rank = {k: rank_rows(np.where(m & np.isfinite(lab), lab, np.nan)) for k, m in masks.items()}
    ew = {k: np.nanmean(np.where(m, lab, np.nan), axis=1) for k, m in masks.items()}
    cap_rank = rank_rows(np.where(masks["csi500"], logcap, np.nan))
    if a.ids_file:
        with open(a.ids_file, encoding="utf-8") as fh:
            d = json.load(fh)
        ids = d[0]["features"] if isinstance(d, list) and d and isinstance(d[0], dict) else (d.get("members") if isinstance(d, dict) else d)
    else:
        ids = [f[:-4] for f in os.listdir(a.ranks_dir or os.path.join(a.root, "pool_ranks")) if f.endswith(".npy")]
    out = []; t0 = time.time()
    for i, cid in enumerate(ids):
        p = os.path.join(a.ranks_dir or os.path.join(a.root, "pool_ranks"), f"{cid}.npy")
        if not os.path.exists(p):
            continue
        with open(p, "rb") as fh:
            arr = np.load(fh)[pos].astype(np.float32)
        rec = {"id": cid, "name": cands.get(cid, {}).get("name"), "source": cands.get(cid, {}).get("source")}
        for k, m in masks.items():
            f = np.where(m & np.isfinite(arr), arr, np.nan)
            fr = rank_rows(f)
            r, n = _row_corr_np(fr, lab_rank[k]); r = np.where(n >= 50, r, np.nan)
            cnt = np.sum(np.isfinite(f), axis=1); pct = fr / np.where(cnt > 0, cnt, np.nan)[:, None]
            top = np.nanmean(np.where(pct >= 0.9, lab, np.nan), axis=1) - ew[k]; bot = np.nanmean(np.where(pct <= 0.1, lab, np.nan), axis=1) - ew[k]
            ics = []
            for y in a.years:
                s = yr == y
                v = np.nanmean(r[s]); ics.append(v)
                rec[f"{k}_ic_{y}"] = float(v); rec[f"{k}_top_{y}"] = float(np.nanmean(top[s]) * 252 / 5); rec[f"{k}_bot_{y}"] = float(np.nanmean(bot[s]) * 252 / 5)
            rec[f"{k}_ic_mean"] = float(np.mean(ics)); rec[f"{k}_ic_worst"] = float(np.min(ics)); rec[f"{k}_ic_t"] = float(np.nanmean(r) / np.nanstd(r) * np.sqrt(np.sum(np.isfinite(r))))
            rec[f"{k}_top_mean"] = float(np.nanmean(top) * 252 / 5); rec[f"{k}_bot_mean"] = float(np.nanmean(bot) * 252 / 5)
            rec[f"{k}_coverage"] = float(np.mean(cnt / np.maximum(m.sum(axis=1), 1)))
            if k == "csi500":
                rc, nc = _row_corr_np(fr, cap_rank); rec["csi500_cap_corr"] = float(np.nanmean(np.where(nc >= 50, rc, np.nan)))
        out.append(rec)
        if (i + 1) % 25 == 0:
            print(f"{i + 1}/{len(ids)} ({time.time() - t0:.0f}s)", flush=True)
            pd.DataFrame(out).to_csv(a.out, index=False, encoding="utf-8-sig")
    df = pd.DataFrame(out); df.to_csv(a.out, index=False, encoding="utf-8-sig")
    print(df.sort_values("csi500_ic_mean", ascending=False)[["name", "source", "csi500_ic_mean", "csi500_ic_worst", "csi500_top_mean", "csi500_bot_mean", "csi500_cap_corr", "union_ic_mean"]].head(25).round(4).to_string(index=False))
    print(a.out)


if __name__ == "__main__":
    main()
