#!/usr/bin/env bash
# forward confirmation tables for item 1 (2025 and 2026 Jan-May separately, OOS panel), c2 baseline scores alongside
set -uo pipefail; cd "$(dirname "$0")/.."; PV=/Users/leslie/Downloads/中证500量化60min/c5_core/data/A_Layer_Research/competition/pv2; FR=/Users/leslie/Downloads/中证500量化60min/c5_core/data/A_Layer_Research/competition/frozen/c2_396_enhanced_turnneutral
until grep -q "forward done" $PV/logs/pv2_item1_forward_driver.log 2>/dev/null; do sleep 30; done
for Y in 2025 2026; do
  bash scripts/pv2_compare.sh $PV/eval/compare_oos${Y}_item1.json --panel-dir /Users/leslie/Downloads/中证500量化60min/c5_core/data/A_Layer_OOS/panel --years $Y --pred "c2_frozen_s0=$FR/scores_forward.parquet" --pred "pv2_turnlabel_oos_s0=$PV/combos/pred_pv2_turnlabel_oos_s0.parquet" > $PV/logs/compare_oos${Y}_item1.log 2>&1
  echo "$(date +%H:%M:%S) compared $Y"
done
echo ITEM1_FORWARD_COMPARES_DONE
