#!/usr/bin/env bash
# A15: evaluate one item's registered candidates under the all-A pool rules, write direction-applied rank arrays on the
# research panel, and re-evaluate them inside CSI500 / union (2019-2024 and the 2016-2018 training years).
# usage: bash scripts/pv2_item_prep.sh <item> [workers]
set -uo pipefail
cd "$(dirname "$0")/.."
export PYTHONPATH=src; export FLA_WORKERS=${2:-2}
PY=.venv/Scripts/python.exe; PV=/Users/leslie/Downloads/中证500量化60min/c5_core/data/A_Layer_Research/competition/pv2; mkdir -p $PV/logs $PV/eval $PV/ranks_research
ITEM=$1
echo "$(date +%H:%M:%S) evaluate item $ITEM (workers $FLA_WORKERS)"
$PY scripts/pv2_factors.py evaluate --item $ITEM --workers $FLA_WORKERS > $PV/logs/item${ITEM}_evaluate.log 2>&1; echo "rc=$?"
echo "$(date +%H:%M:%S) ranks item $ITEM"
$PY scripts/pv2_factors.py ranks --item $ITEM --panel-dir /Users/leslie/Downloads/中证500量化60min/c5_core/data/A_Layer_Research/panel --out $PV/ranks_research > $PV/logs/item${ITEM}_ranks.log 2>&1; echo "rc=$?"
echo "$(date +%H:%M:%S) member eval item $ITEM"
$PY scripts/competition_member_eval.py --ranks-dir $PV/ranks_research --out $PV/eval/member_eval_500_pv2.csv > $PV/logs/item${ITEM}_member_eval.log 2>&1; echo "rc=$?"
$PY scripts/competition_member_eval.py --ranks-dir $PV/ranks_research --years 2016 2017 2018 --out $PV/eval/member_eval_500_pv2_train.csv > $PV/logs/item${ITEM}_member_eval_train.log 2>&1; echo "rc=$?"
echo "$(date +%H:%M:%S) item $ITEM prep done"
