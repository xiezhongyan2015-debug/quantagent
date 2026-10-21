#!/usr/bin/env bash
# A17 item 1 quick look: 2-seed ensembles (seeds 0+1) for both label variants under the c2 / c3-A / c3-B rules
set -uo pipefail; cd "$(dirname "$0")/.."; export PYTHONPATH=src
PY=.venv/Scripts/python.exe; R=/Users/leslie/Downloads/中证500量化60min/c5_core/data/A_Layer_Research; PV=$R/competition/pv4; P2=$R/competition/pv2/combos; FR=$R/competition/frozen/c2_396_enhanced_turnneutral
$PY scripts/pv4_ensemble.py --panel-dir $R/panel --preds $FR/scores_research.parquet $P2/pred_pv2_base396_s1.parquet --out $PV/combos/ens_base396_s01.parquet
$PY scripts/pv4_ensemble.py --panel-dir $R/panel --preds $P2/pred_pv2_turnlabel_s0.parquet $P2/pred_pv2_turnlabel_s1.parquet --out $PV/combos/ens_turnlabel_s01.parquet
$PY scripts/competition_compare.py --panel-dir $R/panel --pred "base_s0=$FR/scores_research.parquet" --pred "base_s1=$P2/pred_pv2_base396_s1.parquet" --pred "base_ens01=$PV/combos/ens_base396_s01.parquet" --pred "turn_s0=$P2/pred_pv2_turnlabel_s0.parquet" --pred "turn_s1=$P2/pred_pv2_turnlabel_s1.parquet" --pred "turn_ens01=$PV/combos/ens_turnlabel_s01.parquet" --rules --rule-json "$(cat $PV/eval/core_rules.json)" --out $PV/eval/compare_item1_quick.json
echo ITEM1_QUICK_DONE
