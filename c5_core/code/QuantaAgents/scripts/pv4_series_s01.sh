#!/usr/bin/env bash
# A17 items 1/5: daily net series of the core rules for seeds 0/1 (research) and seed 0 (forward panel), queued behind the veto test
set -uo pipefail; cd "$(dirname "$0")/.."; export PYTHONPATH=src
PY=.venv/Scripts/python.exe; R=/Users/leslie/Downloads/中证500量化60min/c5_core/data/A_Layer_Research; PV=$R/competition/pv4; P2=$R/competition/pv2/combos; P3=$R/competition/pv3/combos; FR=$R/competition/frozen/c2_396_enhanced_turnneutral
until grep -q ITEM3_VETO_DONE $PV/logs/item3_veto.log 2>/dev/null; do sleep 30; done
mkdir -p $PV/series
$PY scripts/pv4_rule_series.py --panel-dir $R/panel --pred "base_s0=$FR/scores_research.parquet" --pred "base_s1=$P2/pred_pv2_base396_s1.parquet" --pred "turn_s0=$P2/pred_pv2_turnlabel_s0.parquet" --pred "turn_s1=$P2/pred_pv2_turnlabel_s1.parquet" --rule-json $PV/eval/core_rules.json --out $PV/series/core_research_s01.parquet
$PY scripts/pv4_rule_series.py --panel-dir /Users/leslie/Downloads/中证500量化60min/c5_core/data/A_Layer_OOS/p2026/panel --pred "base_s0=$P3/pred_pv3_base396_2026_s0.parquet" --pred "turn_s0=$P3/pred_pv3_turnlabel_2026_s0.parquet" --rule-json $PV/eval/core_rules.json --out $PV/series/core_p2026_s0.parquet
echo SERIES_S01_DONE
