#!/usr/bin/env bash
# A17 item 1: 5-seed ensembles on the research panel vs every single seed, under the c2 / c3-A / c3-B rules
set -uo pipefail; cd "$(dirname "$0")/.."; export PYTHONPATH=src
PY=.venv/Scripts/python.exe; R=/Users/leslie/Downloads/中证500量化60min/c5_core/data/A_Layer_Research; PV=$R/competition/pv4; P2=$R/competition/pv2/combos; C=$PV/combos; FR=$R/competition/frozen/c2_396_enhanced_turnneutral
until grep -q "IW_DONE\|Traceback" $PV/logs/iw.log 2>/dev/null; do sleep 30; done
$PY scripts/pv4_ensemble.py --panel-dir $R/panel --preds $FR/scores_research.parquet $P2/pred_pv2_base396_s1.parquet $C/pred_base396_s2.parquet $C/pred_base396_s3.parquet $C/pred_base396_s4.parquet --out $C/ens_base396_s01234.parquet
$PY scripts/pv4_ensemble.py --panel-dir $R/panel --preds $P2/pred_pv2_turnlabel_s0.parquet $P2/pred_pv2_turnlabel_s1.parquet $C/pred_turnlabel_s2.parquet $C/pred_turnlabel_s3.parquet $C/pred_turnlabel_s4.parquet --out $C/ens_turnlabel_s01234.parquet
$PY scripts/competition_compare.py --panel-dir $R/panel --pred "base_s2=$C/pred_base396_s2.parquet" --pred "base_s3=$C/pred_base396_s3.parquet" --pred "base_s4=$C/pred_base396_s4.parquet" --pred "base_ens5=$C/ens_base396_s01234.parquet" --pred "turn_s2=$C/pred_turnlabel_s2.parquet" --pred "turn_s3=$C/pred_turnlabel_s3.parquet" --pred "turn_s4=$C/pred_turnlabel_s4.parquet" --pred "turn_ens5=$C/ens_turnlabel_s01234.parquet" --rules --rule-json "$(cat $PV/eval/core_rules.json)" --out $PV/eval/compare_item1_ens5.json
echo ITEM1_ENS5_DONE
