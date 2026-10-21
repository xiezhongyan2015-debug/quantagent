#!/usr/bin/env bash
set -uo pipefail; cd "$(dirname "$0")/.."; export PYTHONPATH=src; PY=.venv/Scripts/python.exe; PV=/Users/leslie/Downloads/中证500量化60min/c5_core/data/A_Layer_Research/competition/pv2
until [ -f $PV/cluster_k25_research.json ]; do sleep 20; done
$PY scripts/competition_compare.py --panel-dir /Users/leslie/Downloads/中证500量化60min/c5_core/data/A_Layer_Research/panel --pred "c2_frozen_s0=/Users/leslie/Downloads/中证500量化60min/c5_core/data/A_Layer_Research/competition/frozen/c2_396_enhanced_turnneutral/scores_research.parquet" --pred "base396_s1=$PV/combos/pred_pv2_base396_s1.parquet" --rules --rule-json "$(cat $PV/eval/item6_rules.json)" --out $PV/eval/compare_research_item6_cluster.json
echo COMPARE6_DONE
