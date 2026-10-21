#!/usr/bin/env bash
# A17 item 5: series of the exclusion-intensity variants of c3-A (none / 10% / 30%) for the environment-switch test
set -uo pipefail; cd "$(dirname "$0")/.."; export PYTHONPATH=src
PY=.venv/Scripts/python.exe; R=/Users/leslie/Downloads/中证500量化60min/c5_core/data/A_Layer_Research; PV=$R/competition/pv4; P2=$R/competition/pv2/combos; P3=$R/competition/pv3/combos; FR=$R/competition/frozen/c2_396_enhanced_turnneutral
until grep -q SERIES_S01_DONE $PV/logs/series_s01.log 2>/dev/null; do sleep 30; done
$PY scripts/pv4_rule_series.py --panel-dir $R/panel --pred "base_s0=$FR/scores_research.parquet" --pred "base_s1=$P2/pred_pv2_base396_s1.parquet" --rule-json $PV/eval/regime_rules.json --out $PV/series/regime_research_s01.parquet
$PY scripts/pv4_rule_series.py --panel-dir /Users/leslie/Downloads/中证500量化60min/c5_core/data/A_Layer_OOS/p2026/panel --pred "base_s0=$P3/pred_pv3_base396_2026_s0.parquet" --rule-json $PV/eval/regime_rules.json --out $PV/series/regime_p2026_s0.parquet
echo SERIES_REGIME_DONE
