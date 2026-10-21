#!/usr/bin/env bash
# A15 checkpoint resume (written 2026-09-16 08:25 when the user paused the background chains).
# Re-runs, in order and one heavy job at a time, exactly the pieces that had not finished; every step skips itself
# when its product already exists, so the script can be re-run after another interruption.
#   nohup bash scripts/pv2_resume.sh > /Users/leslie/Downloads/中证500量化60min/c5_core/data/A_Layer_Research/competition/pv2/logs/pv2_resume.log 2>&1 &
# Pre-flight: F: mounted, FreeVirtualMemory >= 6GB, no other heavy python running.
set -uo pipefail
cd "$(dirname "$0")/.."
export PYTHONPATH=src
PY=.venv/Scripts/python.exe; R=/Users/leslie/Downloads/中证500量化60min/c5_core/data/A_Layer_Research; PV=$R/competition/pv2; M=$R/batches/combinations_v11_396_blend51020.json
FR=$R/competition/frozen/c2_396_enhanced_turnneutral
log() { echo "$(date +%H:%M:%S) $*"; }
retrain() { tag=$1; shift; if [ -f $PV/combos/pred_$tag.parquet ]; then log "skip $tag (exists)"; return 0; fi
  rm -rf $R/batches/tmp_lowmem_$tag; log "start $tag"
  $PY scripts/competition_union_combine.py --members-file $M --universe all_a --rerank none --label-blend 5 10 20 --out-dir $PV/combos --tag $tag "$@" > $PV/logs/$tag.log 2>&1; log "end $tag rc=$?"; }

# ---- item 1: turnover-neutral training label, seed 1 (research) + forward seed 0 (OOS)
retrain pv2_turnlabel_s1 --label-neutral-field turnover20 --label-neutral-q 5 --seed 1
[ -f $PV/eval/compare_research_item1_pv2_turnlabel_s1.json ] || bash scripts/pv2_compare.sh $PV/eval/compare_research_item1_pv2_turnlabel_s1.json --pred "pv2_turnlabel_s1=$PV/combos/pred_pv2_turnlabel_s1.parquet" > $PV/logs/compare_item1_pv2_turnlabel_s1.log 2>&1
retrain pv2_turnlabel_oos_s0 --panel-dir /Users/leslie/Downloads/中证500量化60min/c5_core/data/A_Layer_OOS/panel --ranks-dir /Users/leslie/Downloads/中证500量化60min/c5_core/data/A_Layer_OOS/v11/ranks --target-years 2025 2026 --label-neutral-field turnover20 --label-neutral-q 5 --seed 0
for Y in 2025 2026; do
  [ -f $PV/eval/compare_oos${Y}_item1.json ] || bash scripts/pv2_compare.sh $PV/eval/compare_oos${Y}_item1.json --panel-dir /Users/leslie/Downloads/中证500量化60min/c5_core/data/A_Layer_OOS/panel --years $Y --pred "c2_frozen_s0=$FR/scores_forward.parquet" --pred "pv2_turnlabel_oos_s0=$PV/combos/pred_pv2_turnlabel_oos_s0.parquet" > $PV/logs/compare_oos${Y}_item1.log 2>&1
done
log "item 1 computations done"

# ---- items 4 and 5: candidate evaluation (resumes: only candidates without a result are evaluated), ranks, in-bucket eval
for I in 4 5; do
  if ! grep -q "item $I prep done" $PV/logs/item${I}_prep_driver.log 2>/dev/null; then bash scripts/pv2_item_prep.sh $I 2 > $PV/logs/item${I}_prep_driver.log 2>&1; fi
  $PY scripts/pv2_factors.py table --item $I --member-eval $PV/eval/member_eval_500_pv2.csv --out $PV/eval/item${I}_table.csv > $PV/logs/item${I}_table.log 2>&1
done
log "items 4-5 prep done"

# ---- item 6: OOS clusters + cluster-neutral exclusion comparison (research, seeds 0 and 1)
[ -f $PV/cluster_k25_oos.json ] || $PY scripts/pv2_cluster.py --panel-dir /Users/leslie/Downloads/中证500量化60min/c5_core/data/A_Layer_OOS/panel --out $PV/cluster_k25_oos.parquet --n-clusters 25 > $PV/logs/cluster_oos.log 2>&1
[ -f $PV/eval/compare_research_item6_cluster.json ] || $PY scripts/competition_compare.py --panel-dir /Users/leslie/Downloads/中证500量化60min/c5_core/data/A_Layer_Research/panel --pred "c2_frozen_s0=$FR/scores_research.parquet" --pred "base396_s1=$PV/combos/pred_pv2_base396_s1.parquet" --rules --rule-json "$(cat $PV/eval/item6_rules.json)" --out $PV/eval/compare_research_item6_cluster.json > $PV/logs/item6_compare.log 2>&1
log "item 6 done"

# ---- item 7: tracking-error-budget book (two configs, seed-0 scores; ~25 min per config)
[ -f $PV/eval/compare_research_item7_tebudget.json ] || $PY scripts/competition_compare.py --panel-dir /Users/leslie/Downloads/中证500量化60min/c5_core/data/A_Layer_Research/panel --pred "c2_frozen_s0=$FR/scores_research.parquet" --rules --rule-json "$(cat $PV/eval/item7_rules.json)" --out $PV/eval/compare_research_item7_tebudget.json > $PV/logs/item7_compare.log 2>&1
log "item 7 done"
log "RESUME_DONE"
