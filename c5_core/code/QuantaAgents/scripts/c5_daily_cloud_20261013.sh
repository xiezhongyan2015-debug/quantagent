#!/usr/bin/env bash
# c5-only daily chain (run after the close, data through today): the part of competition_daily.sh that c5 needs,
# plus the daily state export for the teammate.  About 40-60 minutes; the data update is most of it.
# Usage: bash scripts/c5_daily.sh [--skip-data] [--start YYYY-MM-DD] [--ffill-limit 10] [--sync <dir>]
#   --start        first date to export (default: the panel's last date)
#   --ffill-limit  trading days the fundamental grid may be carried past its last build (live chain default 10);
#                  the grid only moves when scripts/fd_update.py runs with FD_TUSHARE_TOKEN set
#   --sync         copy the day files and the merged file to this directory (shared drive / synced folder)
set -euo pipefail
cd "$(dirname "$0")/.."
export PYTHONPATH=src
PY=${PY:-.venv/Scripts/python.exe}
LIVE=C:/ChoiceBridge/c5_stage_20261012/payload/c5_core/data/A_Layer_Live
FZ5=C:/ChoiceBridge/c5_stage_20261012/payload/c5_core/data/A_Layer_Research/competition/frozen/c5_396_turnlabel_ens5_fdveto2_freecap
START=""; FFILL=10; SYNC=""; SKIP_DATA=0
while [ $# -gt 0 ]; do
  case "$1" in
    --skip-data) SKIP_DATA=1; shift;;
    --start) START="$2"; shift 2;;
    --ffill-limit) FFILL="$2"; shift 2;;
    --sync) SYNC="$2"; shift 2;;
    *) echo "unknown arg $1"; exit 2;;
  esac
done
TS=$(date +%Y%m%d_%H%M); mkdir -p $LIVE/logs
log() { echo "$(date +%H:%M:%S) $*"; }

if [ $SKIP_DATA -eq 0 ]; then
  log "data update"; $PY scripts/competition_data.py update > $LIVE/logs/update_$TS.log 2>&1 || { tail -n 20 $LIVE/logs/update_$TS.log; exit 3; }
fi
log "live panel"; $PY -m quanta_agents.factor_lab_a.live build-panel --source $LIVE/daily_source --out $LIVE/panel --instruments $LIVE/instruments --start 2022-06-01 2>&1 | tail -n 1
# 2026-10-13: fundamentals through the reseller endpoint (token file, never printed), then rebuild the fundamental grid on the
# live panel's own dates so event fields keep their latest disclosure instead of running out after the ffill limit.
# Any failure here falls back to the original research grid + ffill (the previous behaviour), the chain continues.
FD_SECRET=${FD_SECRET:-C:/ChoiceBridge/live/secrets/tushare.json}; FD_GRID=$LIVE/fundamental_grid
if [ -f "$FD_SECRET" ]; then
  log "fundamental update (reseller)"; $PY scripts/fd_update_reseller.py --since-last --max-minutes 20 --skip daily adj_factor --secret "$FD_SECRET" > $LIVE/logs/fd_update_$TS.log 2>&1 && tail -n 1 $LIVE/logs/fd_update_$TS.log || { log "fundamental update incomplete (see fd_update_$TS.log); grid rebuilt from the raw data on hand"; tail -n 3 $LIVE/logs/fd_update_$TS.log; }
fi
FD_GRID_OK=1
for part in income balancesheet cashflow fina_indicator ratios events analyst daily; do
  $PY scripts/fd_features.py build --parts $part --grid-dir $FD_GRID --grid-panels live > $LIVE/logs/fd_grid_${part}_$TS.log 2>&1 || { FD_GRID_OK=0; log "live grid build failed at $part"; break; }
done
if [ $FD_GRID_OK -eq 1 ]; then
  log "fundamental fields from the live grid (ffill <= $FFILL sessions)"; $PY scripts/fd_features.py assemble --panels live --grid-dir $FD_GRID --ffill-limit $FFILL 2>&1 | tail -n 1
else
  log "fundamental fields from the research grid (ffill <= $FFILL sessions)"; $PY scripts/fd_features.py assemble --panels live --ffill-limit $FFILL 2>&1 | tail -n 1
fi

log "member ranks (3 shards)"
mkdir -p $LIVE/ranks_396_allA
for i in 0 1 2; do
  $PY -m quanta_agents.factor_lab_a.live ranks --panel-dir $LIVE/panel --members-file C:/ChoiceBridge/c5_stage_20261012/payload/c5_core/data/A_Layer_Research/batches/combinations_v11_396_blend51020.json --out $LIVE/ranks_396_allA --universe all_a --shard $i/3 > $LIVE/logs/ranks_shard${i}_$TS.log 2>&1 &
done
wait
# A shard that hits a memory error leaves the old array (one row per old panel date) in place and does not write the
# cache key, so counting files is not enough: count arrays whose row count matches the panel, and if some are stale,
# write the key for the current panel and fill the rest in one sequential pass (it skips arrays that already match).
RANKS_OK='import glob,sys,numpy as np,pandas as pd
n=len(pd.read_parquet("C:/ChoiceBridge/c5_stage_20261012/payload/c5_core/data/A_Layer_Live/panel/close.parquet").index)
ok=sum(np.load(f,mmap_mode="r").shape[0]==n for f in glob.glob("C:/ChoiceBridge/c5_stage_20261012/payload/c5_core/data/A_Layer_Live/ranks_396_allA/*.npy")); print(ok)'
N_OK=$($PY -c "$RANKS_OK"); log "ranks matching the panel: $N_OK / 396"
if [ "$N_OK" -lt 396 ]; then
  $PY -c 'import json,hashlib
from quanta_agents.factor_lab_a.panel import Panel
p=Panel("C:/ChoiceBridge/c5_stage_20261012/payload/c5_core/data/A_Layer_Live/panel")
json.dump({"n_dates":len(p.dates),"n_codes":len(p.codes),"first_date":p.dates[0].strftime("%Y-%m-%d"),"last_date":p.dates[-1].strftime("%Y-%m-%d"),
           "codes_md5":hashlib.md5("|".join(p.codes).encode()).hexdigest()},open("C:/ChoiceBridge/c5_stage_20261012/payload/c5_core/data/A_Layer_Live/ranks_396_allA/_cache_key.json","w",encoding="utf-8"))'
  log "filling stale ranks sequentially"
  $PY -m quanta_agents.factor_lab_a.live ranks --panel-dir $LIVE/panel --members-file C:/ChoiceBridge/c5_stage_20261012/payload/c5_core/data/A_Layer_Research/batches/combinations_v11_396_blend51020.json --out $LIVE/ranks_396_allA --universe all_a > $LIVE/logs/ranks_fill_$TS.log 2>&1
  N_OK=$($PY -c "$RANKS_OK"); log "ranks matching the panel: $N_OK / 396"
fi
[ "$N_OK" -ge 396 ] || { echo "member ranks incomplete ($N_OK / 396); see $LIVE/logs/ranks_*_$TS.log"; exit 3; }

log "score (turnover-neutral-label final model)"
$PY -m quanta_agents.factor_lab_a.live score --panel-dir $LIVE/panel --ranks-dir $LIVE/ranks_396_allA --model-dir $LIVE/models/m396_allA_turnlabel_through202605 --out $LIVE/pred_live_turnlabel.parquet 2>&1 | tail -n 1

VEXPR="cs_rank(-fd_accrual_cf) + cs_rank(-fd_audit_nonstd) + cs_rank(-clip(fd_hnum_chg, -1, 3)) + cs_rank(-fd_hsell_60) + cs_rank(-fd_pledge) + cs_rank(clip(fd_rev_q_yoy, -3, 3)) + cs_rank(cs_rank(clip(fd_sue, -5, 5)) * cs_rank(-rolling_mean(turnover, 20)))"
log "fundamental veto mask"; $PY scripts/fd_arm_inputs.py veto --panel-dir $LIVE/panel --direction 1 --q 0.10 --out $LIVE/fd_veto_distress10_v2.parquet --expr "$VEXPR" 2>&1 | tail -n 1

CFG_C5='{"book":"enhanced","gamma":1.0,"x_out":0.2,"x_in":0.35,"y_in":0.9,"y_out":0.75,"tau":0.0,"cap_field":"fd_free_cap","smooth":5,"w500":0.85,"woth":0.13,"noth":110,"keep_mult":4.0,"neutral_q":5,"neutral_field":"turnover20","lot_group_below":true,"veto_mask":"C:/ChoiceBridge/c5_stage_20261012/payload/c5_core/data/A_Layer_Live/fd_veto_distress10_v2.parquet","oth_universe":"csi1000"}'
log "c5 target"; $PY -m quanta_agents.factor_lab_a.live target --panel-dir $LIVE/panel --prediction $LIVE/pred_live_turnlabel.parquet --nav 2000000 --out $LIVE/targets/shadow_c5_fdveto2_freecap --cfg "$CFG_C5" | grep -E '"asof"|vetoed'

[ -n "$START" ] || START=$($PY -c "import pandas as pd;print(pd.read_parquet('$LIVE/panel/close.parquet').index[-1].strftime('%Y-%m-%d'))")
log "c5 states from $START"; $PY scripts/c5_states_export.py --start $START --out $LIVE/c5_states
if [ -n "$SYNC" ]; then
  mkdir -p "$SYNC"; cp $LIVE/c5_states/c5_state_*.parquet $LIVE/c5_states/c5_state_*.csv $LIVE/c5_states/c5_states_all.parquet "$SYNC"/; log "synced to $SYNC"
fi
log "done"
