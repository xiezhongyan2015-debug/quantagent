#!/usr/bin/env bash
# A17: excluded weight redistributed inside the name's own style group (research seeds 0/1 of the turnover-neutral-label scores)
set -uo pipefail; cd "$(dirname "$0")/.."; export PYTHONPATH=src
PY=.venv/Scripts/python.exe; R=/Users/leslie/Downloads/中证500量化60min/c5_core/data/A_Layer_Research; PV=$R/competition/pv4; P2=$R/competition/pv2/combos
$PY scripts/competition_compare.py --panel-dir $R/panel --pred "turn_s0=$P2/pred_pv2_turnlabel_s0.parquet" --pred "turn_s1=$P2/pred_pv2_turnlabel_s1.parquet" --rules --rule-json "$(cat $PV/eval/regroup_rules.json)" --out $PV/eval/compare_regroup_research.json
echo REGROUP_DONE
