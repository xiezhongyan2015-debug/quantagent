#!/usr/bin/env bash
set -uo pipefail; cd "$(dirname "$0")/.."; export PYTHONPATH=src; PV=/Users/leslie/Downloads/中证500量化60min/c5_core/data/A_Layer_Research/competition/pv2; FR=/Users/leslie/Downloads/中证500量化60min/c5_core/data/A_Layer_Research/competition/frozen/c2_396_enhanced_turnneutral
for Y in 2025 2026; do
  .venv/Scripts/python.exe scripts/competition_compare.py --panel-dir /Users/leslie/Downloads/中证500量化60min/c5_core/data/A_Layer_OOS/panel --years $Y --pred "c2_frozen_s0=$FR/scores_forward.parquet" --rules --rule-json "$(cat $PV/eval/item7e_rules.json)" --out $PV/eval/compare_oos${Y}_item7e_tilt.json > $PV/logs/compare_oos${Y}_item7e.log 2>&1
done
echo COMPARE7EF_DONE >> $PV/logs/compare_oos2026_item7e.log
