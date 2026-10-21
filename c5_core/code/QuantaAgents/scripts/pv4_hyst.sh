#!/usr/bin/env bash
# A17 item 4: lot-based hysteresis of the no-trade band (research seeds 0/1 only; forward is looked at after the choice)
set -uo pipefail; cd "$(dirname "$0")/.."; export PYTHONPATH=src
PY=.venv/Scripts/python.exe; R=/Users/leslie/Downloads/中证500量化60min/c5_core/data/A_Layer_Research; PV=$R/competition/pv4; P2=$R/competition/pv2/combos; FR=$R/competition/frozen/c2_396_enhanced_turnneutral
until grep -q "ITEM1_ENS5_DONE\|Traceback" $PV/logs/item1_ens5.log 2>/dev/null; do sleep 30; done
$PY scripts/competition_compare.py --panel-dir $R/panel --pred "base_s0=$FR/scores_research.parquet" --pred "base_s1=$P2/pred_pv2_base396_s1.parquet" --rules --rule-json "$(cat $PV/eval/hyst_rules.json)" --out $PV/eval/compare_hyst_research.json
echo HYST_DONE
