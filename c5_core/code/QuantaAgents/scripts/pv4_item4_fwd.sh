#!/usr/bin/env bash
# A17 item 4: forward confirmation (2025 / 2026 to 09-16) of group rounding for high-priced names; nothing is tuned here
set -uo pipefail; cd "$(dirname "$0")/.."; export PYTHONPATH=src
PY=.venv/Scripts/python.exe; R=/Users/leslie/Downloads/中证500量化60min/c5_core/data/A_Layer_Research; PV=$R/competition/pv4
$PY scripts/competition_compare.py --panel-dir /Users/leslie/Downloads/中证500量化60min/c5_core/data/A_Layer_OOS/p2026/panel --years 2025 2026 --pred "base_s0=$R/competition/pv3/combos/pred_pv3_base396_2026_s0.parquet" --rules --rule-json "$(cat $PV/eval/item4_lot_rules_fwd.json)" --out $PV/eval/compare_item4_fwd.json
echo ITEM4_FWD_DONE
