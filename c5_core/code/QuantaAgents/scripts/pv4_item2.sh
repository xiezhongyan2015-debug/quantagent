#!/usr/bin/env bash
# A17 item 2 (objective alignment): one seed of six training variants on the research panel, run after the seed chain.
# usage: bash scripts/pv4_item2.sh <seed>
set -uo pipefail
cd "$(dirname "$0")/.."
export PYTHONPATH=src
PY=.venv/Scripts/python.exe; R=/Users/leslie/Downloads/中证500量化60min/c5_core/data/A_Layer_Research; PV=$R/competition/pv4; M=$R/batches/combinations_v11_396_blend51020.json
S=${1:-0}
log() { echo "$(date +%H:%M:%S) $*"; }
quiet() { while true; do H=$(date +%H%M); if [ "$H" -ge 1500 ] && [ "$H" -le 1605 ]; then sleep 120; else break; fi; done; }
until grep -q PV4_SEEDS_DONE $PV/logs/seeds_driver.log 2>/dev/null; do sleep 60; done
run() {
  tag=$1; shift
  if [ -f $PV/combos/pred_$tag.parquet ]; then log "skip $tag"; return 0; fi
  quiet; rm -rf $R/batches/tmp_lowmem_$tag; log "start $tag"
  $PY scripts/pv4_train.py --members-file $M --universe all_a --rerank none --label-blend 5 10 20 --out-dir $PV/combos --tag $tag "$@" > $PV/logs/$tag.log 2>&1
  log "end $tag rc=$?"
}
run al_wunion3_s$S --weight-universe union=3 --seed $S
run al_wunion3_csi5_s$S --weight-universe union=3 csi500=5 --seed $S
run al_topw3_s$S --top-weight 3 --seed $S
run al_long20_s$S --binary-label top 0.2 --seed $S
run al_long20_union_s$S --binary-label top 0.2 --label-universe union --weight-universe union=3 --seed $S
run al_short20_s$S --binary-label bottom 0.2 --seed $S
log "PV4_ITEM2_S${S}_DONE"
