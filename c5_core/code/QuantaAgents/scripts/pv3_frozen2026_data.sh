#!/usr/bin/env bash
# Frozen-test data chain (user instruction 2026-09-16): a full-history panel 2015..2026-09-16 from the live daily source,
# raw_open field, 396 member ranks (3 shards), expanding-window retrain for 2025 and 2026 (through 09-16), then the
# three-rule comparison on 2026. Products under /Users/leslie/Downloads/中证500量化60min/c5_core/data/A_Layer_OOS/p2026 and /Users/leslie/Downloads/中证500量化60min/c5_core/data/A_Layer_Research/competition/pv3.
set -uo pipefail; cd "$(dirname "$0")/.."; export PYTHONPATH=src
PY=.venv/Scripts/python.exe; LIVE=/Users/leslie/Downloads/中证500量化60min/c5_core/data/A_Layer_Live; O=/Users/leslie/Downloads/中证500量化60min/c5_core/data/A_Layer_OOS/p2026; PV=/Users/leslie/Downloads/中证500量化60min/c5_core/data/A_Layer_Research/competition/pv3; M=/Users/leslie/Downloads/中证500量化60min/c5_core/data/A_Layer_Research/batches/combinations_v11_396_blend51020.json
mkdir -p $O $PV/combos $PV/eval $PV/logs
log() { echo "$(date +%H:%M:%S) $*"; }
if [ ! -f $O/panel/meta.json ]; then
  log "build full panel 2015..latest from the live daily source"
  $PY -m quanta_agents.factor_lab_a.live build-panel --source $LIVE/daily_source --out $O/panel --instruments $LIVE/instruments --start 2015-01-01 > $PV/logs/build_panel.log 2>&1; log "panel rc=$?"
fi
[ -f $O/panel/raw_open.parquet ] || $PY scripts/pv2_raw_open.py --panel-dir $O/panel --source $LIVE/daily_source > $PV/logs/raw_open.log 2>&1
N=$(ls $O/ranks_396/*.npy 2>/dev/null | wc -l)
if [ "$N" -lt 390 ]; then
  log "member ranks (396, 3 shards)"; mkdir -p $O/ranks_396
  for i in 0 1 2; do $PY -m quanta_agents.factor_lab_a.live ranks --panel-dir $O/panel --members-file $M --out $O/ranks_396 --universe all_a --shard $i/3 > $PV/logs/ranks_shard$i.log 2>&1 & done; wait
  log "ranks written: $(ls $O/ranks_396/*.npy | wc -l)"
fi
if [ ! -f $PV/combos/pred_pv3_base396_2026_s0.parquet ]; then
  log "retrain 2025+2026 (expanding window, seed 0)"
  rm -rf /Users/leslie/Downloads/中证500量化60min/c5_core/data/A_Layer_Research/batches/tmp_lowmem_pv3_base396_2026_s0
  $PY scripts/competition_union_combine.py --members-file $M --universe all_a --rerank none --label-blend 5 10 20 --panel-dir $O/panel --ranks-dir $O/ranks_396 --target-years 2025 2026 --seed 0 --out-dir $PV/combos --tag pv3_base396_2026_s0 > $PV/logs/pv3_base396_2026_s0.log 2>&1; log "retrain rc=$?"
fi
for Y in 2025 2026; do
  bash scripts/pv2_compare.sh $PV/eval/compare_p2026_${Y}_base396.json --panel-dir $O/panel --years $Y --pred "base396=$PV/combos/pred_pv3_base396_2026_s0.parquet" > $PV/logs/compare_${Y}_base396.log 2>&1
done
log "PV3_DATA_DONE"
