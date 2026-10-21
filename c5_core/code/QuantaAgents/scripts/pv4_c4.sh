#!/usr/bin/env bash
# A17: candidate c4 = c3-A rule + implied index weights + CSI1000-only other bucket, on the 5-seed ensembles (research, then forward)
set -uo pipefail; cd "$(dirname "$0")/.."; export PYTHONPATH=src
PY=.venv/Scripts/python.exe; R=/Users/leslie/Downloads/中证500量化60min/c5_core/data/A_Layer_Research; PV=$R/competition/pv4; C=$PV/combos
$PY scripts/competition_compare.py --panel-dir $R/panel --pred "base_ens5=$C/ens_base396_s01234.parquet" --pred "turn_ens5=$C/ens_turnlabel_s01234.parquet" --rules --rule-json "$(cat $PV/eval/c4_rules_research.json)" --out $PV/eval/compare_c4_research.json
$PY scripts/competition_compare.py --panel-dir /Users/leslie/Downloads/中证500量化60min/c5_core/data/A_Layer_OOS/p2026/panel --years 2025 2026 --pred "base_ens5=$C/ens_base396_2026_s01234.parquet" --pred "turn_ens5=$C/ens_turnlabel_2026_s01234.parquet" --rules --rule-json "$(cat $PV/eval/c4_rules_forward.json)" --out $PV/eval/compare_c4_forward.json
echo C4_DONE
