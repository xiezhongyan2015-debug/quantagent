#!/usr/bin/env bash
# A18 step 3, arm A: retrain 396 + admitted fundamental members (turnover-neutral 5/10/20 label) on the research panel,
# seeds 0 and 1, and compare with the same-seed 396 baselines under the c4 rule set.  One retrain at a time; every
# step is skipped when its output exists; pauses 15:00-16:05 machine time for the daily chain.
#   bash scripts/fd_arm_a.sh [members.json]
set -uo pipefail; cd "$(dirname "$0")/.."; export PYTHONPATH=src
PY=.venv/Scripts/python.exe; R=F:/A_Layer_Research; PV=$R/competition/pv4; FDD=$R/competition/fd; P2=$R/competition/pv2/combos
M=${1:-$FDD/members/m396_plus_fd.json}
SET=$(basename "$M" .json); SET=${SET#m396_plus_}   # fd | fd_relaxed
mkdir -p $FDD/combos $FDD/logs $FDD/eval
log() { echo "$(date +%H:%M:%S) $*"; }
quiet() { while true; do H=$(date +%H%M); if [ "$H" -ge 1500 ] && [ "$H" -le 1605 ]; then sleep 120; else break; fi; done; }
run() { tag=$1; shift; if [ -f $FDD/combos/pred_$tag.parquet ]; then log "skip $tag"; return 0; fi; quiet; rm -rf $R/batches/tmp_lowmem_$tag; log "start $tag"
  $PY scripts/competition_union_combine.py --members-file $M --universe all_a --rerank none --label-blend 5 10 20 \
     --ranks-dir $FDD/ranks_research $R/pool_ranks $R/cand_ranks --out-dir $FDD/combos --tag $tag "$@" > $FDD/logs/$tag.log 2>&1; log "end $tag rc=$?"; }
for S in 0 1; do
  run fdA_${SET}_turnlabel_s$S --label-neutral-field turnover20 --label-neutral-q 5 --seed $S
done
# same-seed baselines: seed 0/1 turnover-neutral 396 models from A15 (pv2) — compare under the c4 rule set
$PY scripts/competition_compare.py --panel-dir $R/panel \
  --pred "base_turn_s0=$P2/pred_pv2_turnlabel_s0.parquet" --pred "base_turn_s1=$P2/pred_pv2_turnlabel_s1.parquet" \
  --pred "fdA_${SET}_s0=$FDD/combos/pred_fdA_${SET}_turnlabel_s0.parquet" --pred "fdA_${SET}_s1=$FDD/combos/pred_fdA_${SET}_turnlabel_s1.parquet" \
  --rules --rule-json "$(cat $PV/eval/c4_rules_research.json)" --out $FDD/eval/compare_armA_${SET}_research.json > $FDD/logs/compare_armA_${SET}.log 2>&1
log "FD_ARM_A_DONE rc=$?"
