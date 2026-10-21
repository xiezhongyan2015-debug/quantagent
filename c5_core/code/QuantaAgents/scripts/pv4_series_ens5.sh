#!/usr/bin/env bash
# A17 item 1: daily net series of the rule grid for the 5-seed ensembles (research now; forward once the 2026-panel seeds exist)
set -uo pipefail; cd "$(dirname "$0")/.."; export PYTHONPATH=src
PY=.venv/Scripts/python.exe; R=/Users/leslie/Downloads/中证500量化60min/c5_core/data/A_Layer_Research; PV=$R/competition/pv4; C=$PV/combos; P3=$R/competition/pv3/combos
until grep -q "HYST_DONE\|Traceback" $PV/logs/hyst.log 2>/dev/null; do sleep 30; done
$PY scripts/pv4_rule_series.py --panel-dir $R/panel --pred "base=$C/ens_base396_s01234.parquet" --pred "turn=$C/ens_turnlabel_s01234.parquet" --rule-json $PV/eval/grid_rules_research.json --out $PV/series/grid_research_ens5.parquet
echo SERIES_ENS5_RESEARCH_DONE
until grep -q PV4_SEEDS_DONE $PV/logs/seeds_driver.log 2>/dev/null; do sleep 60; done
$PY scripts/pv4_ensemble.py --panel-dir /Users/leslie/Downloads/中证500量化60min/c5_core/data/A_Layer_OOS/p2026/panel --preds $P3/pred_pv3_base396_2026_s0.parquet $C/pred_base396_2026_s1.parquet $C/pred_base396_2026_s2.parquet $C/pred_base396_2026_s3.parquet $C/pred_base396_2026_s4.parquet --out $C/ens_base396_2026_s01234.parquet
$PY scripts/pv4_ensemble.py --panel-dir /Users/leslie/Downloads/中证500量化60min/c5_core/data/A_Layer_OOS/p2026/panel --preds $P3/pred_pv3_turnlabel_2026_s0.parquet $C/pred_turnlabel_2026_s1.parquet $C/pred_turnlabel_2026_s2.parquet $C/pred_turnlabel_2026_s3.parquet $C/pred_turnlabel_2026_s4.parquet --out $C/ens_turnlabel_2026_s01234.parquet
$PY scripts/pv4_rule_series.py --panel-dir /Users/leslie/Downloads/中证500量化60min/c5_core/data/A_Layer_OOS/p2026/panel --pred "base=$C/ens_base396_2026_s01234.parquet" --pred "turn=$C/ens_turnlabel_2026_s01234.parquet" --rule-json $PV/eval/grid_rules_forward.json --out $PV/series/grid_p2026_ens5.parquet
echo SERIES_ENS5_FORWARD_DONE
