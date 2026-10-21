#!/usr/bin/env bash
# A15 item 1: turnover-neutral training label (5/10/20-day blend re-ranked inside 20-day-turnover quintiles), same 396
# members / same LightGBM; seeds 0 and 1; plus the seed-1 baseline of the frozen (plain blended) label for seed noise.
set -uo pipefail
cd "$(dirname "$0")/.."
export PYTHONPATH=src
PY=.venv/Scripts/python.exe
R=/Users/leslie/Downloads/中证500量化60min/c5_core/data/A_Layer_Research; PV=$R/competition/pv2; M=$R/batches/combinations_v11_396_blend51020.json
mkdir -p $PV/combos $PV/logs
run() { tag=$1; shift; echo "$(date +%H:%M:%S) start $tag"; $PY scripts/competition_union_combine.py --members-file $M --universe all_a --rerank none --label-blend 5 10 20 --out-dir $PV/combos --tag $tag "$@" > $PV/logs/$tag.log 2>&1; echo "$(date +%H:%M:%S) end $tag rc=$?"; }
run pv2_base396_s1 --seed 1
run pv2_turnlabel_s0 --label-neutral-field turnover20 --label-neutral-q 5 --seed 0
run pv2_turnlabel_s1 --label-neutral-field turnover20 --label-neutral-q 5 --seed 1
echo "$(date +%H:%M:%S) all done"
