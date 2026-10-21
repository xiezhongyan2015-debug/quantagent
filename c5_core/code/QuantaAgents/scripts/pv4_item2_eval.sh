#!/usr/bin/env bash
# A17 item 2: evaluate the remaining objective-alignment variants (seed 0): union-universe long classifier, short classifier as
# the exclusion score of the enhanced book (main score = ordinary seed 0), and the long/short split in the concentrated book
set -uo pipefail; cd "$(dirname "$0")/.."; export PYTHONPATH=src
PY=.venv/Scripts/python.exe; R=/Users/leslie/Downloads/中证500量化60min/c5_core/data/A_Layer_Research; PV=$R/competition/pv4; C=$PV/combos; FR=$R/competition/frozen/c2_396_enhanced_turnneutral
until grep -q PV4_ITEM2_S0_DONE $PV/logs/item2_s0_driver.log 2>/dev/null; do sleep 60; done
$PY - <<PYEOF
import json
PV = "/Users/leslie/Downloads/中证500量化60min/c5_core/data/A_Layer_Research/competition/pv4/"
core = json.load(open(PV + "eval/core_rules.json", encoding="utf-8"))
rules = dict(core); rules["c3A_exclshort"] = dict(core["c3A"], exclusion_pred=PV + "combos/pred_al_short20_s0.parquet"); rules["c2_exclshort"] = dict(core["c2"], exclusion_pred=PV + "combos/pred_al_short20_s0.parquet")
json.dump(rules, open(PV + "eval/item2_split_rules.json", "w", encoding="utf-8"))
PYEOF
$PY scripts/competition_compare.py --panel-dir $R/panel --pred "base_s0=$FR/scores_research.parquet" --pred "al_long20_union_s0=$C/pred_al_long20_union_s0.parquet" --pred "al_short20_s0=$C/pred_al_short20_s0.parquet" --rules --rule-json "$(cat $PV/eval/item2_split_rules.json)" --out $PV/eval/compare_item2_split_s0.json
echo ITEM2_EVAL_DONE
