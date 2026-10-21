#!/usr/bin/env bash
# A15 item 9: evaluate the GRU score alone and blended 50/50 with the frozen 396 score under the three rules
set -uo pipefail; cd "$(dirname "$0")/.."; export PYTHONPATH=src; PY=.venv/Scripts/python.exe; PV=/Users/leslie/Downloads/中证500量化60min/c5_core/data/A_Layer_Research/competition/pv2; FR=/Users/leslie/Downloads/中证500量化60min/c5_core/data/A_Layer_Research/competition/frozen/c2_396_enhanced_turnneutral
until grep -q SEQ_GRU_S0_DONE $PV/logs/seq_gru_s0.log 2>/dev/null; do sleep 60; done
$PY scripts/pv2_blend.py --panel-dir /Users/leslie/Downloads/中证500量化60min/c5_core/data/A_Layer_Research/panel --a $FR/scores_research.parquet --b $PV/seq/pred_pv2_gru_s0.parquet --out $PV/seq/pred_blend_396_gru_s0.parquet > $PV/logs/item9_blend.log 2>&1
bash scripts/pv2_compare.sh $PV/eval/compare_research_item9_gru.json --pred "gru_s0=$PV/seq/pred_pv2_gru_s0.parquet" --pred "blend_396_gru=$PV/seq/pred_blend_396_gru_s0.parquet" > $PV/logs/item9_compare.log 2>&1
echo ITEM9_DONE
