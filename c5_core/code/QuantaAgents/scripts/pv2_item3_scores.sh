#!/usr/bin/env bash
# A15 item 3 second scores: turnover-quintile reversal (built earlier), its equal-weight blend with 12-1 momentum,
# and combined-veto exclusion scores = per-date min of the union percentiles of the c2 score and a second score.
set -uo pipefail; cd "$(dirname "$0")/.."; export PYTHONPATH=src; PY=.venv/Scripts/python.exe; PV=/Users/leslie/Downloads/中证500量化60min/c5_core/data/A_Layer_Research/competition/pv2; S=$PV/scores
$PY scripts/pv2_factors.py score --panel-dir /Users/leslie/Downloads/中证500量化60min/c5_core/data/A_Layer_Research/panel --universe csi500 --expr "0.5 * cs_rank_within(-(close / lag(close, 20) - 1), rolling_mean(turnover, 20), 5) + 0.5 * cs_rank(lag(close, 20) / lag(close, 250) - 1)" --out $S/rev20q5_mom250_blend_research.parquet
$PY - <<'PYEOF'
import pandas as pd, numpy as np, json
from quanta_agents.factor_lab_a.panel import Panel
panel = Panel("/Users/leslie/Downloads/中证500量化60min/c5_core/data/A_Layer_Research/panel"); panel._dtype = np.float32
union = panel.mask("union")
c2 = pd.read_parquet("/Users/leslie/Downloads/中证500量化60min/c5_core/data/A_Layer_Research/competition/frozen/c2_396_enhanced_turnneutral/scores_research.parquet"); c2.index = pd.DatetimeIndex(c2.index)
c2 = c2.reindex(index=panel.dates, columns=panel.codes)
pc2 = c2.where(union).rank(axis=1, pct=True)
for name in ("mom250_20", "rev20_q5turn"):
    m = pd.read_parquet(f"/Users/leslie/Downloads/中证500量化60min/c5_core/data/A_Layer_Research/competition/pv2/scores/{name}_research.parquet"); m.index = pd.DatetimeIndex(m.index)
    pm = m.reindex(index=panel.dates, columns=panel.codes).where(union).rank(axis=1, pct=True)
    out = pd.concat([pc2, pm]).groupby(level=0).min()          # elementwise min of the two percentiles (NaN ignored)
    out = out.where(pc2.notna())
    out.astype(np.float32).to_parquet(f"/Users/leslie/Downloads/中证500量化60min/c5_core/data/A_Layer_Research/competition/pv2/scores/veto_min_c2_{name}_research.parquet")
    print(name, "veto file written", float(out.notna().sum(axis=1).mean()))
PYEOF
echo SCORES3_DONE
