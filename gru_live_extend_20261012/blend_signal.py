"""Blend the GRU 15:00 z with the c5 z (ndtri of the union percentile), as in optimizer_overlay_v4 ('mean of available z').
Usage: blend_signal.py <date> <c5 parquet/csv> -> outputs/gru_live_extend_20261012/signals/blend_<date>.csv (date,code,z) plus gru-only copy."""
import sys
from pathlib import Path
import numpy as np, pandas as pd
from scipy.special import ndtri
ROOT = Path(__file__).resolve().parents[1]; SIG = ROOT / 'outputs/gru_live_extend_20261012/signals'
day, c5_path = sys.argv[1], Path(sys.argv[2]); suffix = sys.argv[3] if len(sys.argv) > 3 else 'slot15'   # e.g. slot15 (live) | slot15_all15 | slot14_all15 (shadow)
c5_day = sys.argv[4] if len(sys.argv) > 4 else day   # intraday shadow (2026-10-16): yesterday's c5 rows
g = pd.read_csv(SIG / f'z_{day}_{suffix}.csv', dtype={'code': str}); g['date'] = g.date.astype(str)
c5 = pd.read_parquet(c5_path) if c5_path.suffix == '.parquet' else pd.read_csv(c5_path, dtype={'code': str}); c5['date'] = c5.date.astype(str).str[:10]; c5 = c5[c5.date == c5_day]
c5z = pd.Series(ndtri(np.clip(c5.score_pct.astype(float).to_numpy(), 1e-4, 1 - 1e-4)), index=c5.code.to_numpy())
m = pd.DataFrame({'gru': g.set_index('code').z}); m['c5'] = c5z.reindex(m.index); m = m.join(pd.DataFrame({'c5_only': c5z[~c5z.index.isin(m.index)]}), how='outer')
m['c5'] = m.c5.fillna(m.c5_only); z = m[['gru', 'c5']].mean(axis=1, skipna=True)
out = pd.DataFrame(dict(date=day, code=z.index, z=z.values)); out = out[np.isfinite(out.z)]
out.to_csv(SIG / (f'blend_{day}.csv' if suffix == 'slot15' else f'blend_{day}_{suffix}.csv'), index=False)
print(dict(date=day, c5_date=c5_day, gru_names=int(m.gru.notna().sum()), c5_names=int(m.c5.notna().sum()), both=int((m.gru.notna() & m.c5.notna()).sum()), blend_names=len(out), corr_gru_c5=float(m[['gru', 'c5']].dropna().corr(method='spearman').iloc[0, 1]) if (m.gru.notna() & m.c5.notna()).sum() > 10 else None))
