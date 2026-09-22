"""A18 diagnostic: do pairwise interactions of the 396 members carry signal that the deployed model missed?
Residual e = pct(label) - pct(out-of-fold prediction) per date (both cross-sectional percentiles inside all-A). For every
pair (a, b) the interaction term is (r_a - 0.5)(r_b - 0.5) - the XOR-type structure a single split cannot see - and its
daily rank-free correlation with e is averaged over sampled dates (t across dates). The same statistics on the raw label
show what the interactions predict in total; a within-date permutation of e gives the null distribution of the t counts.
python scripts/pv4_interaction_scan.py --panel-dir P --pred pred.parquet --members-file M --ranks-dir D1 D2 --dates 60 --out out.json"""
import argparse, json, os, sys, time
import numpy as np, pandas as pd
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
from quanta_agents.factor_lab_a.panel import Panel
ap = argparse.ArgumentParser(); ap.add_argument("--panel-dir", required=True); ap.add_argument("--pred", required=True); ap.add_argument("--members-file", required=True); ap.add_argument("--ranks-dir", nargs="+", required=True)
ap.add_argument("--dates", type=int, default=60); ap.add_argument("--years", nargs=2, type=int, default=[2019, 2024]); ap.add_argument("--out", required=True); ap.add_argument("--label", default="label_5")
a = ap.parse_args()
m = json.load(open(a.members_file, encoding="utf-8")); m = m[0]["features"] if isinstance(m, list) and isinstance(m[0], dict) else (m["members"] if isinstance(m, dict) else m)
panel = Panel(a.panel_dir); panel._dtype = np.float32; dates = panel.dates
rng = np.random.default_rng(1); cand = np.where((dates.year >= a.years[0]) & (dates.year <= a.years[1]))[0]
rows = np.sort(rng.choice(cand[:-10], a.dates, replace=False))
mask = panel.mask("all_a").to_numpy(bool); lab = panel[a.label].to_numpy(np.float32)
pred = pd.read_parquet(a.pred); pred.index = pd.DatetimeIndex(pred.index); pred = pred.reindex(index=dates, columns=panel.codes).to_numpy(np.float32)
def path(cid):
    for d in a.ranks_dir:
        p = os.path.join(d, cid + ".npy")
        if os.path.exists(p): return p
    raise FileNotFoundError(cid)
F_all = np.stack([np.asarray(np.load(path(c), mmap_mode="r")[rows], dtype=np.float32) for c in m], axis=-1)   # dates x codes x K
K = len(m); acc = {k: np.zeros((K, K)) for k in ("res", "res_perm", "lab", "lab_perm")}; acc2 = {k: np.zeros((K, K)) for k in acc}
single = {k: np.zeros(K) for k in ("res", "lab")}; single2 = {k: np.zeros(K) for k in single}; n_used = 0; t0 = time.time()
def pct(v):
    r = np.full(v.shape, np.nan, np.float32); ok = np.isfinite(v); r[ok] = (pd.Series(v[ok]).rank(pct=True).to_numpy() - 0.5 / ok.sum()); return r
for i, t in enumerate(rows):
    ok = mask[t] & np.isfinite(lab[t]) & np.isfinite(pred[t]) & (np.isfinite(F_all[i]).sum(axis=1) >= 0.8 * K)
    if ok.sum() < 1000: continue
    F = F_all[i][ok]; F = np.where(np.isfinite(F), F, 0.5) - 0.5
    y = pct(lab[t][ok]); p = pct(pred[t][ok])
    # residual = label percentile minus its conditional mean given the prediction (20 prediction-quantile bins); y - p would
    # be dominated by -p itself, since the prediction explains only ~IC of the label
    b = np.minimum((p * 20).astype(int), 19); cm = np.zeros(20)
    for k in range(20): cm[k] = y[b == k].mean() if (b == k).any() else y.mean()
    e = y - cm[b]
    for key, target in (("res", e), ("lab", y)):
        for perm in (False, True):
            z = target.copy()
            if perm: z = rng.permutation(z)
            z = (z - z.mean()) / (z.std() + 1e-12); n = len(z)
            cov = (F * z[:, None]).T @ F / n                     # E[f_a f_b z]
            m1 = F.T @ F / n; m2 = (F ** 2).T @ (F ** 2) / n
            sd = np.sqrt(np.maximum(m2 - m1 ** 2, 1e-12)); c = cov / sd
            kk = key + ("_perm" if perm else ""); acc[kk] += c; acc2[kk] += c ** 2
            if not perm:
                s = (F * z[:, None]).mean(axis=0) / (F.std(axis=0) + 1e-12); single[key] += s; single2[key] += s ** 2
    n_used += 1
    if (i + 1) % 10 == 0: print(f"  {i + 1}/{len(rows)} dates ({time.time() - t0:.0f}s)", flush=True)
rep = {"dates_used": n_used, "members": K}
iu = np.triu_indices(K, 1)
for kk in acc:
    mean = acc[kk] / n_used; sd = np.sqrt(np.maximum(acc2[kk] / n_used - mean ** 2, 1e-12)); tt = mean / sd * np.sqrt(n_used)
    tv = tt[iu]; mv = mean[iu]
    rep[kk] = {"pairs": int(len(tv)), "share_abs_t_gt3": float((np.abs(tv) > 3).mean()), "share_abs_t_gt4": float((np.abs(tv) > 4).mean()), "n_abs_t_gt4": int((np.abs(tv) > 4).sum()),
               "n_abs_t_gt5": int((np.abs(tv) > 5).sum()), "max_abs_t": float(np.abs(tv).max()), "mean_abs_corr_top100": float(np.sort(np.abs(mv))[-100:].mean()), "p99_abs_corr": float(np.percentile(np.abs(mv), 99))}
    if kk in ("res", "lab"):
        idx = np.argsort(-np.abs(tv))[:15]
        rep[kk]["top_pairs"] = [{"a": m[iu[0][j]][:16], "b": m[iu[1][j]][:16], "corr": float(mv[j]), "t": float(tv[j])} for j in idx]
for key in single:
    mean = single[key] / n_used; sd = np.sqrt(np.maximum(single2[key] / n_used - mean ** 2, 1e-12)); tt = mean / sd * np.sqrt(n_used)
    rep["single_" + key] = {"n_abs_t_gt4": int((np.abs(tt) > 4).sum()), "max_abs_t": float(np.abs(tt).max()), "mean_abs_corr": float(np.abs(mean).mean()), "p99_abs_corr": float(np.percentile(np.abs(mean), 99))}
json.dump(rep, open(a.out, "w", encoding="utf-8"), indent=1)
for kk in ("lab", "lab_perm", "res", "res_perm"):
    r = rep[kk]; print(f"{kk:9s}: pairs {r['pairs']}  |t|>3 {r['share_abs_t_gt3']:.4f}  |t|>4 {r['n_abs_t_gt4']}  |t|>5 {r['n_abs_t_gt5']}  max|t| {r['max_abs_t']:.1f}  p99|corr| {r['p99_abs_corr']:.4f}  top100 mean|corr| {r['mean_abs_corr_top100']:.4f}")
for key in ("single_lab", "single_res"):
    r = rep[key]; print(f"{key}: members |t|>4 {r['n_abs_t_gt4']}  max|t| {r['max_abs_t']:.1f}  mean|corr| {r['mean_abs_corr']:.4f}  p99 {r['p99_abs_corr']:.4f}")
