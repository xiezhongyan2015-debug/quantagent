#!/usr/bin/env bash
# A18 diagnostic: how much of the 396-member model's skill comes from interactions? Same members / label / seed 0, only the
# tree capacity changes: depth-1 stumps (purely additive), 7 leaves, 31 leaves (reference), 127 leaves.
set -uo pipefail; cd "$(dirname "$0")/.."; export PYTHONPATH=src
PY=.venv/Scripts/python.exe; R=F:/A_Layer_Research; PV=$R/competition/pv4; M=$R/batches/combinations_v11_396_blend51020.json
log() { echo "$(date +%H:%M:%S) $*"; }
run() { tag=$1; shift; [ -f $PV/combos/pred_$tag.parquet ] && { log "skip $tag"; return; }; rm -rf $R/batches/tmp_lowmem_$tag; log "start $tag"
  $PY scripts/competition_union_combine.py --members-file $M --universe all_a --rerank none --label-blend 5 10 20 --seed 0 --out-dir $PV/combos --tag $tag --params "$1" > $PV/logs/$tag.log 2>&1; log "end $tag rc=$?"; }
run cap_add1_s0 '{"max_depth":1,"num_leaves":2,"num_rounds":1200}'
run cap_l7_s0 '{"num_leaves":7,"num_rounds":800}'
run cap_l127_s0 '{"num_leaves":127,"min_data_in_leaf":300,"num_rounds":300}'
$PY scripts/competition_compare.py --panel-dir $R/panel --pred "base_s0=$R/competition/frozen/c2_396_enhanced_turnneutral/scores_research.parquet" --pred "add1=$PV/combos/pred_cap_add1_s0.parquet" --pred "l7=$PV/combos/pred_cap_l7_s0.parquet" --pred "l127=$PV/combos/pred_cap_l127_s0.parquet" --rules --rule-json "$(cat $PV/eval/core_rules.json)" --out $PV/eval/compare_capacity.json > $PV/logs/compare_capacity.log 2>&1
log "CAPACITY_DONE"
