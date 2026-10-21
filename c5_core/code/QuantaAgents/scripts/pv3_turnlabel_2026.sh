#!/usr/bin/env bash
# turnover-neutral-label retrain for 2025+2026 on the full panel (after the base retrain), then the 2026 frozen-test
# comparison of the candidate books (c2, conc80k3, enhanced tilt, tebudget) on both score variants
set -uo pipefail; cd "$(dirname "$0")/.."; export PYTHONPATH=src
PY=.venv/Scripts/python.exe; O=/Users/leslie/Downloads/中证500量化60min/c5_core/data/A_Layer_OOS/p2026; PV=/Users/leslie/Downloads/中证500量化60min/c5_core/data/A_Layer_Research/competition/pv3; M=/Users/leslie/Downloads/中证500量化60min/c5_core/data/A_Layer_Research/batches/combinations_v11_396_blend51020.json
until grep -q PV3_DATA_DONE $PV/logs/driver.log 2>/dev/null; do sleep 60; done
if [ ! -f $PV/combos/pred_pv3_turnlabel_2026_s0.parquet ]; then
  rm -rf /Users/leslie/Downloads/中证500量化60min/c5_core/data/A_Layer_Research/batches/tmp_lowmem_pv3_turnlabel_2026_s0
  $PY scripts/competition_union_combine.py --members-file $M --universe all_a --rerank none --label-blend 5 10 20 --panel-dir $O/panel --ranks-dir $O/ranks_396 --target-years 2025 2026 --label-neutral-field turnover20 --label-neutral-q 5 --seed 0 --out-dir $PV/combos --tag pv3_turnlabel_2026_s0 > $PV/logs/pv3_turnlabel_2026_s0.log 2>&1
fi
RULES='{"c2":{"book":"enhanced","gamma":1.0,"x_out":0.2,"x_in":0.35,"y_in":0.9,"y_out":0.75,"tau":0.0,"cap_field":"float_market_cap","cost":0.004,"neutral_q":5,"neutral_field":"turnover20"},"conc55k3":{"cost":0.004},"conc80k3":{"cost":0.004,"n500":80,"keep_mult":3.0},"conc80k3_82_14":{"cost":0.004,"n500":80,"keep_mult":3.0,"w500":0.82,"woth":0.14},"enh_tilt":{"book":"enhanced","gamma":0.5,"x_out":0.3,"x_in":0.45,"y_in":0.9,"y_out":0.75,"tau":2.0,"cost":0.004,"neutral_q":5,"neutral_field":"turnover20"},"te_k20":{"book":"tebudget","cap_field":"float_market_cap","cost":0.004,"neutral_q":5,"neutral_field":"turnover20","smooth":5,"te_rebalance":5,"te_lookback":250,"te_max_w":0.10,"te_ann":0.04,"te_kappa":20.0}}'
for Y in 2025 2026; do
  $PY scripts/competition_compare.py --panel-dir $O/panel --years $Y --pred "base396=$PV/combos/pred_pv3_base396_2026_s0.parquet" --pred "turnlabel=$PV/combos/pred_pv3_turnlabel_2026_s0.parquet" --rules --rule-json "$RULES" --out $PV/eval/compare_p2026_${Y}_candidates.json > $PV/logs/compare_${Y}_candidates.log 2>&1
done
echo PV3_CANDIDATES_DONE
