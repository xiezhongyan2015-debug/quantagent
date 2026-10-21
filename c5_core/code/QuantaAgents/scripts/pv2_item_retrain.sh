#!/usr/bin/env bash
# A15: retrain 396 + the admitted candidates of one or more items (seeds 0 and 1), compare under the three rules, and
# write the OOS rank arrays of the new members (for a later forward retrain). Waits for the resume chain to finish.
# usage: bash scripts/pv2_item_retrain.sh <tag> <item> [<item> ...]
set -uo pipefail; cd "$(dirname "$0")/.."; export PYTHONPATH=src
PY=.venv/Scripts/python.exe; R=/Users/leslie/Downloads/中证500量化60min/c5_core/data/A_Layer_Research; PV=$R/competition/pv2; TAG=$1; shift; ITEMS="$*"
log() { echo "$(date +%H:%M:%S) $*"; }
until grep -q RESUME_DONE $PV/logs/pv2_resume.log 2>/dev/null; do sleep 30; done
MF=$PV/members/m396_plus_$TAG.json
$PY scripts/pv2_factors.py members --item $ITEMS --admitted-only --out $MF
N=$($PY -c "import json;d=json.load(open('$MF',encoding='utf-8'));print(len(d['new_members']))")
log "$TAG: $N new members"
[ "$N" -gt 0 ] || { log "no new members; nothing to retrain"; echo RETRAIN_${TAG}_DONE; exit 0; }
for S in 0 1; do
  T=pv2_${TAG}_s$S
  if [ ! -f $PV/combos/pred_$T.parquet ]; then
    rm -rf $R/batches/tmp_lowmem_$T; log "start $T"
    $PY scripts/competition_union_combine.py --members-file $MF --universe all_a --rerank none --label-blend 5 10 20 --ranks-dir $R/pool_ranks $PV/ranks_research --seed $S --out-dir $PV/combos --tag $T > $PV/logs/$T.log 2>&1; log "end $T rc=$?"
  fi
  [ -f $PV/eval/compare_research_${T}.json ] || bash scripts/pv2_compare.sh $PV/eval/compare_research_${T}.json --pred "$T=$PV/combos/pred_$T.parquet" > $PV/logs/compare_$T.log 2>&1
done
for I in $ITEMS; do $PY scripts/pv2_factors.py ranks --item $I --panel-dir /Users/leslie/Downloads/中证500量化60min/c5_core/data/A_Layer_OOS/panel --out /Users/leslie/Downloads/中证500量化60min/c5_core/data/A_Layer_OOS/pv2_ranks > $PV/logs/item${I}_ranks_oos.log 2>&1; done
log "$TAG done"; echo RETRAIN_${TAG}_DONE
