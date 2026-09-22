"""A18 step 3 helpers for arms B / C / D (research panel unless --panel-dir given).

blend   : arm B  — rank-average of the price-volume ensemble and a fundamental-only model score (weights w_pv, w_fd)
mix     : arm D  — CSI500 members keep the price-volume score; other-bucket names get the rank-average blend
veto    : arm C  — veto mask (date x code, 1 = vetoed) for CSI500 names whose distress score is in the worst q inside the bucket

python scripts/fd_arm_inputs.py blend --pv <parquet> --fd <parquet> --w 0.7 0.3 --out <parquet>
python scripts/fd_arm_inputs.py mix   --pv <parquet> --fd <parquet> --w 0.5 0.5 --out <parquet>
python scripts/fd_arm_inputs.py veto  --expr "<DSL distress expression, higher = safer>" --q 0.10 --out <parquet> [--train-years 2016 2017 2018]
"""
from __future__ import annotations
import argparse
import json
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
from quanta_agents.factor_lab_a.panel import Panel  # noqa: E402
from quanta_agents.factor_lab_a.dsl import Compiler  # noqa: E402
from quanta_agents.factor_lab_a import competition as cp  # noqa: E402


def _pct(df: pd.DataFrame, panel, mask):
    df = df.reindex(index=panel.dates, columns=panel.codes)
    return df.where(mask).rank(axis=1, pct=True)


def cmd_blend(a, mix=False):
    panel = Panel(a.panel_dir); panel._dtype = np.float32
    m = panel.mask("all_a")
    pv = pd.read_parquet(a.pv); pv.index = pd.DatetimeIndex(pv.index)
    fd = pd.read_parquet(a.fd); fd.index = pd.DatetimeIndex(fd.index)
    rpv, rfd = _pct(pv, panel, m), _pct(fd, panel, m)
    w_pv, w_fd = a.w
    blend = (rpv * w_pv + rfd * w_fd) / (w_pv + w_fd)
    blend = blend.where(rpv.notna() & rfd.notna(), rpv)          # no fundamental score -> price-volume score alone
    if mix:
        mem, _ = cp.membership(panel)
        blend = rpv.where(mem["csi500"], blend)
    out = blend.dropna(how="all").astype(np.float32)
    out.to_parquet(a.out)
    print(a.out, out.shape, "blend cells", int((rpv.notna() & rfd.notna()).sum().sum()))


def cmd_veto(a):
    panel = Panel(a.panel_dir); panel._dtype = np.float32
    comp = Compiler(resolver=lambda n: panel[n])
    f = comp.evaluate(a.expr)
    mem, _ = cp.membership(panel)
    mask = (panel["eligible"] > 0) & mem["csi500"]
    if a.direction:
        f = f * a.direction; print(json.dumps({"direction": a.direction, "train_t": None}))
    elif a.train_years:
        from quanta_agents.factor_lab_a.evaluate import rank_ic
        r, n = rank_ic(f, panel["label_5"], mask & (panel["eval_ok"] > 0), min_stocks=50)
        tr = r[np.isin(r.index.year, a.train_years)].dropna()
        d = int(np.sign(tr.mean())); t = float(tr.mean() / tr.std(ddof=1) * np.sqrt(len(tr)))
        print(json.dumps({"direction": d, "train_t": t}))
        f = f * d
    pct = f.where(mask).rank(axis=1, pct=True)
    veto = (pct <= a.q).astype(np.float32)
    veto.to_parquet(a.out)
    per_year = veto.where(mask).sum(axis=1).groupby(veto.index.year).mean()
    print(a.out, "vetoed names per day by year:", per_year.round(1).to_dict())


def main():
    ap = argparse.ArgumentParser(); sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("blend", "mix"):
        s = sub.add_parser(name); s.add_argument("--panel-dir", default="F:/A_Layer_Research/panel"); s.add_argument("--pv", required=True); s.add_argument("--fd", required=True)
        s.add_argument("--w", nargs=2, type=float, default=[0.7, 0.3]); s.add_argument("--out", required=True)
    s = sub.add_parser("veto"); s.add_argument("--panel-dir", default="F:/A_Layer_Research/panel"); s.add_argument("--expr", required=True); s.add_argument("--q", type=float, default=0.10)
    s.add_argument("--train-years", nargs="*", type=int, default=[2016, 2017, 2018]); s.add_argument("--direction", type=int, default=0, help="fixed sign (+1/-1) instead of fitting on train years (live panel)"); s.add_argument("--out", required=True)
    a = ap.parse_args()
    {"blend": lambda x: cmd_blend(x, False), "mix": lambda x: cmd_blend(x, True), "veto": cmd_veto}[a.cmd](a)


if __name__ == "__main__":
    main()
