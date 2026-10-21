"""Build cached scores/prices and check them against the sealed cluster exports."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import numpy as np
import common as C

scores, axes = C.scores_cached()
check = {}
for (arm, head), d in scores.items():
    sealed = C.load_signal(arm, head)['scores']
    mine = d['ema0.2']
    both = np.isfinite(sealed) & np.isfinite(mine)
    check[f'{arm}/{head}'] = {
        'finite_pattern_equal': bool(np.array_equal(np.isfinite(sealed), np.isfinite(mine))),
        'max_abs_diff': float(np.nanmax(np.abs(sealed[both] - mine[both]))),
    }
px = C.price_panels()
info = {}
for field in ('open', 'close', 'vwap'):
    _, info[field] = C.merged_price(px, field)
# daily-return agreement between the two hfq sources on overlap
r_if = px['if_close'][1:] / px['if_close'][:-1] - 1
r_pq = px['pq_close'][1:] / px['pq_close'][:-1] - 1
ok = np.isfinite(r_if) & np.isfinite(r_pq)
info['close_return_abs_diff_p99'] = float(np.quantile(np.abs(r_if[ok] - r_pq[ok]), .99))
info['close_return_abs_diff_max'] = float(np.abs(r_if[ok] - r_pq[ok]).max())
info['calendar'] = [str(px['calendar'][0]), str(px['calendar'][-1]), int(len(px['calendar']))]
out = {'ema02_reproduces_sealed_ensemble': check, 'price_sources': info}
C.write_json(C.RESULTS / '01_build_check.json', out)
print(out)
