#!/usr/bin/env bash
set -uo pipefail; cd "$(dirname "$0")/.."; export PYTHONPATH=src; PY=.venv/Scripts/python.exe; PV=/Users/leslie/Downloads/中证500量化60min/c5_core/data/A_Layer_Research/competition/pv3; P2=/Users/leslie/Downloads/中证500量化60min/c5_core/data/A_Layer_Research/competition/pv2/combos
until grep -q "ranks written" $PV/logs/driver.log 2>/dev/null; do sleep 60; done
$PY scripts/competition_compare.py --panel-dir /Users/leslie/Downloads/中证500量化60min/c5_core/data/A_Layer_Research/panel --pred "turnlabel_s0=$P2/pred_pv2_turnlabel_s0.parquet" --pred "turnlabel_s1=$P2/pred_pv2_turnlabel_s1.parquet" --rules --rule-json "$(cat $PV/eval/wave2_rules.json)" --out $PV/eval/compare_research_wave2_conc.json > $PV/logs/wave2_compare.log 2>&1
echo WAVE2_DONE >> $PV/logs/wave2_compare.log
$PY scripts/competition_compare.py --panel-dir /Users/leslie/Downloads/中证500量化60min/c5_core/data/A_Layer_Research/panel --pred "turnlabel_s0=$P2/pred_pv2_turnlabel_s0.parquet" --pred "turnlabel_s1=$P2/pred_pv2_turnlabel_s1.parquet" --rules --rule-json "$(cat $PV/eval/wave2_te_rules.json)" --out $PV/eval/compare_research_wave2_te.json > $PV/logs/wave2_te_compare.log 2>&1
echo WAVE2TE_DONE >> $PV/logs/wave2_te_compare.log
