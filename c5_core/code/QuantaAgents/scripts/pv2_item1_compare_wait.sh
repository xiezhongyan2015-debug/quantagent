#!/usr/bin/env bash
# compare each item-1 retrain as soon as its prediction file appears
set -uo pipefail; cd "$(dirname "$0")/.."; PV=/Users/leslie/Downloads/中证500量化60min/c5_core/data/A_Layer_Research/competition/pv2
for tag in pv2_turnlabel_s0 pv2_turnlabel_s1; do
  until grep -q "saved F:" $PV/logs/$tag.log 2>/dev/null; do sleep 20; done
  bash scripts/pv2_compare.sh $PV/eval/compare_research_item1_$tag.json --pred "$tag=$PV/combos/pred_$tag.parquet" > $PV/logs/compare_item1_$tag.log 2>&1
  echo "$(date +%H:%M:%S) compared $tag"
done
echo ITEM1_COMPARES_DONE
