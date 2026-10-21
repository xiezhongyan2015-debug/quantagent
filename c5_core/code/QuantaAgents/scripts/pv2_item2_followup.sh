#!/usr/bin/env bash
set -uo pipefail; cd "$(dirname "$0")/.."; export PYTHONPATH=src; PY=.venv/Scripts/python.exe; PV=/Users/leslie/Downloads/中证500量化60min/c5_core/data/A_Layer_Research/competition/pv2
$PY scripts/competition_compare.py --panel-dir /Users/leslie/Downloads/中证500量化60min/c5_core/data/A_Layer_Research/panel --pred "base396_s1=$PV/combos/pred_pv2_base396_s1.parquet" --rules --rule-json "$(cat $PV/eval/item2_overweight_rules_s1.json)" --out $PV/eval/compare_research_item2_overweight_s1.json
$PY scripts/competition_compare.py --panel-dir /Users/leslie/Downloads/中证500量化60min/c5_core/data/A_Layer_Research/panel --pred "mom250_20_score=$PV/scores/mom250_20_research.parquet" --rules --rule-json "$(cat $PV/eval/item2_exclpred_rules.json)" --out $PV/eval/compare_research_item2_exclpred.json
echo FOLLOWUP_DONE
