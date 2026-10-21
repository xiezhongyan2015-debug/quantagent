#!/usr/bin/env bash
# A17 item 3a: additive veto of the predicted CSI500 deletions (on top of the model's own exclusions); research seeds 0/1 then forward
set -uo pipefail; cd "$(dirname "$0")/.."; export PYTHONPATH=src
PY=.venv/Scripts/python.exe; R=/Users/leslie/Downloads/中证500量化60min/c5_core/data/A_Layer_Research; PV=$R/competition/pv4; P2=$R/competition/pv2/combos; FR=$R/competition/frozen/c2_396_enhanced_turnneutral
until grep -q ITEM4_LOTS3_DONE $PV/logs/item4_lots3.log 2>/dev/null; do sleep 30; done
$PY scripts/competition_compare.py --panel-dir $R/panel --pred "base_s0=$FR/scores_research.parquet" --pred "base_s1=$P2/pred_pv2_base396_s1.parquet" --rules --rule-json "$(cat $PV/eval/item3_veto_rules_research.json)" --out $PV/eval/compare_item3_veto_research.json
$PY scripts/competition_compare.py --panel-dir /Users/leslie/Downloads/中证500量化60min/c5_core/data/A_Layer_OOS/p2026/panel --years 2025 2026 --pred "base_s0=$R/competition/pv3/combos/pred_pv3_base396_2026_s0.parquet" --rules --rule-json "$(cat $PV/eval/item3_veto_rules_forward.json)" --out $PV/eval/compare_item3_veto_forward.json
echo ITEM3_VETO_DONE
