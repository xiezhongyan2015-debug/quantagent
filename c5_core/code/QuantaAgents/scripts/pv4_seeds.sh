#!/usr/bin/env bash
# A17 item 1: more seeds for the two label variants (research panel seeds 2-4; full panel 2025+2026 seeds 1-4), one
# retrain at a time, every step skipped when its prediction exists. Pauses between 15:00 and 16:05 for the daily chain.
set -uo pipefail; cd "$(dirname "$0")/.."; export PYTHONPATH=src
PY=.venv/Scripts/python.exe; R=/Users/leslie/Downloads/中证500量化60min/c5_core/data/A_Layer_Research; PV=$R/competition/pv4; M=$R/batches/combinations_v11_396_blend51020.json; O=/Users/leslie/Downloads/中证500量化60min/c5_core/data/A_Layer_OOS/p2026
log() { echo "$(date +%H:%M:%S) $*"; }
quiet() { while true; do H=$(date +%H%M); if [ "$H" -ge 1500 ] && [ "$H" -le 1605 ]; then sleep 120; else break; fi; done; }
run() { tag=$1; shift; if [ -f $PV/combos/pred_$tag.parquet ]; then log "skip $tag"; return 0; fi; quiet; rm -rf $R/batches/tmp_lowmem_$tag; log "start $tag"
  $PY scripts/competition_union_combine.py --members-file $M --universe all_a --rerank none --label-blend 5 10 20 --out-dir $PV/combos --tag $tag "$@" > $PV/logs/$tag.log 2>&1; log "end $tag rc=$?"; }
for S in 2 3 4; do
  run base396_s$S --seed $S
  run turnlabel_s$S --label-neutral-field turnover20 --label-neutral-q 5 --seed $S
done
for S in 1 2 3 4; do
  run base396_2026_s$S --panel-dir $O/panel --ranks-dir $O/ranks_396 --target-years 2025 2026 --seed $S
  run turnlabel_2026_s$S --panel-dir $O/panel --ranks-dir $O/ranks_396 --target-years 2025 2026 --label-neutral-field turnover20 --label-neutral-q 5 --seed $S
done
log "PV4_SEEDS_DONE"
