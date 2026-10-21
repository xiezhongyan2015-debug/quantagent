#!/usr/bin/env bash
# A17 item 6: retrain 396 + the 19 CSI500-long-side composites (turnover-neutral label, seeds 0/1), after the item-2 variants
set -uo pipefail; cd "$(dirname "$0")/.."; export PYTHONPATH=src
PY=.venv/Scripts/python.exe; R=/Users/leslie/Downloads/中证500量化60min/c5_core/data/A_Layer_Research; PV=$R/competition/pv4; MF=$PV/members/m396_plus_cross500.json
log() { echo "$(date +%H:%M:%S) $*"; }
quiet() { while true; do H=$(date +%H%M); if [ "$H" -ge 1500 ] && [ "$H" -le 1605 ]; then sleep 120; else break; fi; done; }
until grep -q PV4_ITEM2_S0_DONE $PV/logs/item2_s0_driver.log 2>/dev/null; do sleep 60; done
for S in 0 1; do
  T=x500_turnlabel_s$S
  if [ ! -f $PV/combos/pred_$T.parquet ]; then
    quiet; rm -rf $R/batches/tmp_lowmem_$T; log "start $T"
    $PY scripts/competition_union_combine.py --members-file $MF --universe all_a --rerank none --label-blend 5 10 20 --label-neutral-field turnover20 --ranks-dir $R/pool_ranks $R/competition/pv2/ranks_research $PV/ranks_cross500 --seed $S --out-dir $PV/combos --tag $T > $PV/logs/$T.log 2>&1; log "end $T rc=$?"
  fi
done
log "PV4_ITEM6_DONE"
