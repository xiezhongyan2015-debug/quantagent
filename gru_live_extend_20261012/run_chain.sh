#!/usr/bin/env bash
# Daily GRU signal chain on public data: fetch (Sina 1-min, Tencent daily, Tencent quotes) -> hourly -> daily -> assemble/infer -> signals -> blend.
set -eo pipefail
cd "$(cd "$(dirname "$0")/.." && pwd)"
export PYTHONUTF8=1 PYTHONIOENCODING=utf-8
PY=${PY:-.pcf-research-venv/bin/python}; SP=gru_live_extend_20261012/fetch
mkdir -p $SP
DAY=${1:?signal date YYYY-MM-DD}; C5=${2:-}
D=gru_research/trading_data/future_minute_probe; export EXT_SINA=$D/sina_$DAY/raw EXT_TENCENT=$D/tencent_daily_$DAY/raw EXT_QT=$D/tencent_qt_$DAY
echo "== fetch $(date)"; $PY -I $SP/fetch_sina_1min.py $EXT_SINA | tail -1; $PY -I $SP/fetch_tencent_daily.py $EXT_TENCENT $DAY | tail -1; $PY -I $SP/fetch_qt.py $EXT_QT
echo "== hourly $(date)"; $PY gru_live_extend_20261012/hourly_extend.py | tail -1
echo "== daily $(date)"; $PY gru_live_extend_20261012/daily_extend.py | tail -1
echo "== assemble/infer $(date)"; $PY gru_live_extend_20261012/assemble_infer.py | tail -1
echo "== signals $(date)"; $PY gru_live_extend_20261012/signals_ext.py 15 | tail -1
# shadow ensembles / slots (2026-10-15): never touch the live file z_<day>_slot15.csv; failures here do not stop the chain
for ENS in all15 ten; do for SLOT in 15 14; do $PY gru_live_extend_20261012/signals_ext.py $SLOT $ENS | tail -1 || echo "shadow $SLOT $ENS failed"; done; done
if [ -n "$C5" ]; then echo "== blend"; $PY gru_live_extend_20261012/blend_signal.py $DAY "$C5"; for SUF in slot15_all15 slot14_all15 slot15_ten slot14_ten; do [ -f outputs/gru_live_extend_20261012/signals/z_${DAY}_$SUF.csv ] && $PY gru_live_extend_20261012/blend_signal.py $DAY "$C5" $SUF | tail -1; done; fi
echo "== verify $(date)"; $PY - "$DAY" <<'PYEOF'
import sys, pandas as pd, numpy as np, json
day=sys.argv[1]; z=pd.read_csv(f'outputs/gru_live_extend_20261012/signals/z_{day}_slot15.csv'); n=int(np.isfinite(z.z).sum())
ok = n >= 400 and 0.4 <= float(z.z.abs().mean()) <= 1.2
print(json.dumps(dict(day=day, names=n, z_abs_mean=round(float(z.z.abs().mean()),3), ok=ok)))
sys.exit(0 if ok else 3)
PYEOF
echo "== done $(date)"
