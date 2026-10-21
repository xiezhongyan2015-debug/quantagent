#!/usr/bin/env bash
# A17 item 6: 396 + 19 CSI500-long-side composites vs plain 396 (turnover-neutral label, seeds 0/1) under c3-A / c3-B / c4
set -uo pipefail; cd "$(dirname "$0")/.."; export PYTHONPATH=src
PY=.venv/Scripts/python.exe; R=/Users/leslie/Downloads/中证500量化60min/c5_core/data/A_Layer_Research; PV=$R/competition/pv4; C=$PV/combos
until grep -q PV4_ITEM6_DONE $PV/logs/item6_driver.log 2>/dev/null; do sleep 60; done
$PY - <<PYEOF
import json
PV = "/Users/leslie/Downloads/中证500量化60min/c5_core/data/A_Layer_Research/competition/pv4/eval/"
core = json.load(open(PV + "core_rules.json", encoding="utf-8")); c4 = json.load(open(PV + "c4_rule.json", encoding="utf-8"))["c4"]
json.dump({"c3A": core["c3A"], "c3B": core["c3B"], "c4": c4}, open(PV + "item6_rules.json", "w", encoding="utf-8"))
PYEOF
$PY scripts/competition_compare.py --panel-dir $R/panel --pred "x500_s0=$C/pred_x500_turnlabel_s0.parquet" --pred "x500_s1=$C/pred_x500_turnlabel_s1.parquet" --rules --rule-json "$(cat $PV/eval/item6_rules.json)" --out $PV/eval/compare_item6.json
echo ITEM6_EVAL_DONE
