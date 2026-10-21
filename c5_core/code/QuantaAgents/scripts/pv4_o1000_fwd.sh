#!/usr/bin/env bash
# A17: forward confirmation (2025 / 2026) of the CSI1000-only other bucket, alone and stacked with implied weights and the additive veto
set -uo pipefail; cd "$(dirname "$0")/.."; export PYTHONPATH=src
PY=.venv/Scripts/python.exe; R=/Users/leslie/Downloads/中证500量化60min/c5_core/data/A_Layer_Research; PV=$R/competition/pv4
until grep -q "SERIES_ENS5_FORWARD_DONE\|Traceback" $PV/logs/series_ens5.log 2>/dev/null; do sleep 30; done
$PY scripts/competition_compare.py --panel-dir /Users/leslie/Downloads/中证500量化60min/c5_core/data/A_Layer_OOS/p2026/panel --years 2025 2026 --pred "base_s0=$R/competition/pv3/combos/pred_pv3_base396_2026_s0.parquet" --pred "base_ens5=$PV/combos/ens_base396_2026_s01234.parquet" --pred "turn_ens5=$PV/combos/ens_turnlabel_2026_s01234.parquet" --rules --rule-json "$(cat $PV/eval/o1000_rules_forward.json)" --out $PV/eval/compare_o1000_forward.json
echo O1000_FWD_DONE
