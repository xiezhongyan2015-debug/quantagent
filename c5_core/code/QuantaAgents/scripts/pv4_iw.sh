#!/usr/bin/env bash
# A17 fidelity check: shares fixed at the close (as the live process does) vs the old backtest that re-targets close weights at the open
set -uo pipefail; cd "$(dirname "$0")/.."; export PYTHONPATH=src
PY=.venv/Scripts/python.exe; R=/Users/leslie/Downloads/中证500量化60min/c5_core/data/A_Layer_Research; PV=$R/competition/pv4; P2=$R/competition/pv2/combos; FR=$R/competition/frozen/c2_396_enhanced_turnneutral
$PY scripts/competition_compare.py --panel-dir $R/panel --pred "base_s0=$FR/scores_research.parquet" --pred "base_s1=$P2/pred_pv2_base396_s1.parquet" --rules --rule-json "$(cat $PV/eval/iw_rules.json)" --out $PV/eval/compare_iw_research.json
$PY scripts/competition_compare.py --panel-dir /Users/leslie/Downloads/中证500量化60min/c5_core/data/A_Layer_OOS/p2026/panel --years 2025 2026 --pred "base_s0=$R/competition/pv3/combos/pred_pv3_base396_2026_s0.parquet" --rules --rule-json "$(cat $PV/eval/iw_rules.json)" --out $PV/eval/compare_iw_forward.json
echo IW_DONE
