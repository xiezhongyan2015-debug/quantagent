#!/usr/bin/env bash
# 2025 / 2026 frozen-test comparison of the allocation candidates (runs after the queued candidates comparison)
set -uo pipefail; cd "$(dirname "$0")/.."; export PYTHONPATH=src; PY=.venv/Scripts/python.exe; O=/Users/leslie/Downloads/中证500量化60min/c5_core/data/A_Layer_OOS/p2026; PV=/Users/leslie/Downloads/中证500量化60min/c5_core/data/A_Layer_Research/competition/pv3
until grep -q PV3_CANDIDATES_DONE $PV/logs/turnlabel_2026_driver.log 2>/dev/null; do sleep 60; done
for Y in 2025 2026; do
  $PY scripts/competition_compare.py --panel-dir $O/panel --years $Y --pred "base396=$PV/combos/pred_pv3_base396_2026_s0.parquet" --pred "turnlabel=$PV/combos/pred_pv3_turnlabel_2026_s0.parquet" --rules --rule-json "$(cat $PV/eval/alloc3_rules.json)" --out $PV/eval/compare_p2026_${Y}_alloc3.json > $PV/logs/compare_${Y}_alloc3.log 2>&1
done
echo PV3_ALLOC_DONE
