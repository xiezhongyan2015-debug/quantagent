#!/usr/bin/env bash
# A15 item 1 forward confirmation (2025, 2026 Jan-May): turnover-neutral training label retrained on the OOS panel
# with the 396 members' OOS rank arrays; seed 0. Waits for the research chain to finish first (one retrain at a time).
set -uo pipefail
cd "$(dirname "$0")/.."
export PYTHONPATH=src
PY=.venv/Scripts/python.exe; R=/Users/leslie/Downloads/中证500量化60min/c5_core/data/A_Layer_Research; PV=$R/competition/pv2; M=$R/batches/combinations_v11_396_blend51020.json
until grep -q "all done" $PV/logs/pv2_item1_driver.log 2>/dev/null; do sleep 30; done
echo "$(date +%H:%M:%S) start pv2_turnlabel_oos_s0"
$PY scripts/competition_union_combine.py --members-file $M --universe all_a --rerank none --label-blend 5 10 20 --panel-dir /Users/leslie/Downloads/中证500量化60min/c5_core/data/A_Layer_OOS/panel --ranks-dir /Users/leslie/Downloads/中证500量化60min/c5_core/data/A_Layer_OOS/v11/ranks --target-years 2025 2026 --label-neutral-field turnover20 --label-neutral-q 5 --seed 0 --out-dir $PV/combos --tag pv2_turnlabel_oos_s0 > $PV/logs/pv2_turnlabel_oos_s0.log 2>&1
echo "$(date +%H:%M:%S) end pv2_turnlabel_oos_s0 rc=$?"
echo "forward done"
