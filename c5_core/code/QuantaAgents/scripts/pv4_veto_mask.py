"""Veto mask (date x code, 1 = vetoed) from a veto-score file: the cells where the overlay pushed the base score down.
python scripts/pv4_veto_mask.py --base base.parquet --veto score_veto.parquet --out mask.parquet"""
import argparse
import numpy as np
import pandas as pd
ap = argparse.ArgumentParser(); ap.add_argument("--base", required=True); ap.add_argument("--veto", required=True); ap.add_argument("--out", required=True)
a = ap.parse_args()
b = pd.read_parquet(a.base); v = pd.read_parquet(a.veto); b.index = pd.DatetimeIndex(b.index); v.index = pd.DatetimeIndex(v.index)
b = b.reindex(index=v.index, columns=v.columns)
m = (v.notna() & (b.isna() | (v < b - 1e-9)))
m = m.loc[m.any(axis=1), m.any(axis=0)]
m.astype(np.int8).to_parquet(a.out)
print(a.out, m.shape, "vetoed cells", int(m.sum().sum()), "days", len(m), "mean names/day", float(m.sum(axis=1).mean()))
