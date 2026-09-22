#!/usr/bin/env bash
# Daily production chain for the competition book (run after the close, data through today).
# Usage: bash scripts/competition_daily.sh [--skip-data] [--nav 2000000] [--holdings <csv>] [--start 2023-06-01]
# Steps: data update -> live window panel -> member ranks (396 daily members, all-A percentiles) -> score with the
#        final model -> target books (c1 enhanced mainline + concentrated shadow) with pre-checks.
set -euo pipefail
cd "$(dirname "$0")/.."
export PYTHONPATH=src
PY=.venv/Scripts/python.exe
LIVE=F:/A_Layer_Live
NAV=2000000; HOLD=""; START=2022-06-01; SKIP_DATA=0
while [ $# -gt 0 ]; do
  case "$1" in
    --skip-data) SKIP_DATA=1; shift;;
    --nav) NAV="$2"; shift 2;;
    --holdings) HOLD="$2"; shift 2;;
    --start) START="$2"; shift 2;;
    *) echo "unknown arg $1"; exit 2;;
  esac
done
TS=$(date +%Y%m%d_%H%M); mkdir -p $LIVE/logs $LIVE/targets
log() { echo "$(date +%H:%M:%S) $*"; }

if [ $SKIP_DATA -eq 0 ]; then
  log "data update"; $PY scripts/competition_data.py update 2>&1 | tee $LIVE/logs/update_$TS.log | tail -n 5
fi
ASOF=$($PY -c "import json;print(json.load(open('$LIVE/data_asof.json',encoding='utf-8')).get('daily_source_end') or json.load(open('$LIVE/data_asof.json',encoding='utf-8')))" 2>/dev/null || echo "?")
log "data as of: $ASOF"

log "live panel window from $START"
$PY -m quanta_agents.factor_lab_a.live build-panel --source $LIVE/daily_source --out $LIVE/panel --instruments $LIVE/instruments --start $START 2>&1 | tail -n 2
# A18: fundamental fields onto the freshly rebuilt live panel (from the research grid; dates past the grid's last build carry the last known value, <= 10 days).
# Refresh the grid first with `scripts/fd_update.py update && refresh-live` once the endpoint token is configured; failure here never stops the chain.
log "fundamental fields (A18) onto the live panel"; $PY scripts/fd_features.py assemble --panels live --ffill-limit 10 2>&1 | tail -n 1 || log "fundamental assemble failed (ignored)"

log "member ranks (396, all-A percentiles; 3 shards in parallel, ~5GB commit)"
mkdir -p $LIVE/ranks_396_allA   # member files are reused only while the panel dates/codes match the cache key (A18 fix); pass --overwrite after a member change
for i in 0 1 2; do
  $PY -m quanta_agents.factor_lab_a.live ranks --panel-dir $LIVE/panel --members-file F:/A_Layer_Research/batches/combinations_v11_396_blend51020.json --out $LIVE/ranks_396_allA --universe all_a --shard $i/3 > $LIVE/logs/ranks_shard${i}_$TS.log 2>&1 &
done
wait
N_RANKS=$(ls $LIVE/ranks_396_allA/*.npy 2>/dev/null | wc -l); log "ranks written: $N_RANKS / 396"
[ "$N_RANKS" -ge 380 ] || { echo "too few member ranks ($N_RANKS); see $LIVE/logs/ranks_shard*_$TS.log"; exit 3; }

log "score with the final model"
$PY -m quanta_agents.factor_lab_a.live score --panel-dir $LIVE/panel --ranks-dir $LIVE/ranks_396_allA --model-dir $LIVE/models/m396_allA_through202605 --out $LIVE/pred_live.parquet 2>&1 | tail -n 1

log "target books"
# mainline c2: exclusion percentiles inside 20-day-turnover quintiles of each bucket (style-neutral); shadows: c1 (no neutralisation), concentrated 55/3/5
CFG_C2='{"book":"enhanced","gamma":1.0,"x_out":0.2,"x_in":0.35,"y_in":0.9,"y_out":0.75,"tau":0.0,"cap_field":"float_market_cap","smooth":5,"w500":0.88,"woth":0.08,"noth":25,"keep_mult":3.0,"neutral_q":5,"neutral_field":"turnover20"}'
CFG_C1='{"book":"enhanced","gamma":1.0,"x_out":0.2,"x_in":0.35,"y_in":0.9,"y_out":0.75,"tau":0.0,"cap_field":"float_market_cap","smooth":5,"w500":0.88,"woth":0.08,"noth":25,"keep_mult":3.0}'
HARG=""; [ -n "$HOLD" ] && HARG="--holdings $HOLD"
log "mainline c2"; $PY -m quanta_agents.factor_lab_a.live target --panel-dir $LIVE/panel --prediction $LIVE/pred_live.parquet --nav $NAV $HARG --out $LIVE/targets/c2_enhanced_turnneutral --cfg "$CFG_C2" | grep -E '"asof"|target_invested|target_share500|target_max_single|n_orders|n_skipped|lot_rounding|"ok"'
log "shadow c1"; $PY -m quanta_agents.factor_lab_a.live target --panel-dir $LIVE/panel --prediction $LIVE/pred_live.parquet --nav $NAV --out $LIVE/targets/shadow_c1_enhanced --cfg "$CFG_C1" | grep -E '"asof"|target_invested|target_share500|n_orders'
log "shadow concentrated"; $PY -m quanta_agents.factor_lab_a.live target --panel-dir $LIVE/panel --prediction $LIVE/pred_live.parquet --nav $NAV --out $LIVE/targets/shadow_concentrated | grep -E '"asof"|target_invested|target_share500|n_orders'
# A15 item 1: additional shadow arm = concentrated 55/3/5 on the turnover-neutral-label model (same 396 member ranks,
# second final model); skipped when that model is absent
M2=$LIVE/models/m396_allA_turnlabel_through202605
if [ -f $M2/model.txt ]; then
  log "score with the turnover-neutral-label model"
  $PY -m quanta_agents.factor_lab_a.live score --panel-dir $LIVE/panel --ranks-dir $LIVE/ranks_396_allA --model-dir $M2 --out $LIVE/pred_live_turnlabel.parquet 2>&1 | tail -n 1
  log "shadow concentrated (turnover-neutral label)"; $PY -m quanta_agents.factor_lab_a.live target --panel-dir $LIVE/panel --prediction $LIVE/pred_live_turnlabel.parquet --nav $NAV --out $LIVE/targets/shadow_concentrated_turnlabel | grep -E '"asof"|target_invested|target_share500|n_orders'
fi
# A16 candidates as shadow arms (frozen 2026-09-16): c3-A = c2 rule with allocation 84/14/2 and other bucket top-110 keep 4x
# (same score as c2); c3-B = turnover-neutral-label model + concentrated 80/320/5 + 84/14/2 + other bucket top-55
CFG_C3A='{"book":"enhanced","gamma":1.0,"x_out":0.2,"x_in":0.35,"y_in":0.9,"y_out":0.75,"tau":0.0,"cap_field":"float_market_cap","smooth":5,"w500":0.85,"woth":0.13,"noth":110,"keep_mult":4.0,"neutral_q":5,"neutral_field":"turnover20"}'
CFG_C3B='{"smooth":5,"n500":80,"keep_mult":4.0,"w500":0.84,"woth":0.14,"noth":55}'
log "shadow c3-A"; $PY -m quanta_agents.factor_lab_a.live target --panel-dir $LIVE/panel --prediction $LIVE/pred_live.parquet --nav $NAV --out $LIVE/targets/shadow_c3A_enhanced_85_13_n110 --cfg "$CFG_C3A" | grep -E '"asof"|target_invested|target_share500|n_orders'
CFG_C3AG='{"book":"enhanced","gamma":1.0,"x_out":0.2,"x_in":0.35,"y_in":0.9,"y_out":0.75,"tau":0.0,"cap_field":"float_market_cap","smooth":5,"w500":0.85,"woth":0.13,"noth":110,"keep_mult":4.0,"neutral_q":5,"neutral_field":"turnover20","lot_group_below":true}'
log "shadow c3-A (price-neutral lot rounding, A17 item 4)"; $PY -m quanta_agents.factor_lab_a.live target --panel-dir $LIVE/panel --prediction $LIVE/pred_live.parquet --nav $NAV --out $LIVE/targets/shadow_c3A_grouplots_85_13_n110 --cfg "$CFG_C3AG" | grep -E '"asof"|target_invested|target_share500|n_orders' || log "shadow c3-A grouplots failed (ignored)"
# A17 candidate c4: turnover-neutral-label scores + c3-A rule + implied index weights + CSI1000-only other bucket + price-neutral lot rounding
if [ -f $LIVE/pred_live_turnlabel.parquet ]; then
  log "implied index weights"; $PY scripts/pv4_index_weights.py --panel-dir $LIVE/panel --out-dir $LIVE/indexw --alphas 1 --write-alpha 1 --tag _a1 2>/dev/null | tail -1 || log "implied index weights failed (ignored)"
  CFG_C4='{"book":"enhanced","gamma":1.0,"x_out":0.2,"x_in":0.35,"y_in":0.9,"y_out":0.75,"tau":0.0,"cap_field":"float_market_cap","smooth":5,"w500":0.85,"woth":0.13,"noth":110,"keep_mult":4.0,"neutral_q":5,"neutral_field":"turnover20","lot_group_below":true,"cap_mult":"F:/A_Layer_Live/indexw/index_weight_mult_a1.parquet","oth_universe":"csi1000"}'
  [ -f $LIVE/indexw/index_weight_mult_a1.parquet ] && { log "shadow c4"; $PY -m quanta_agents.factor_lab_a.live target --panel-dir $LIVE/panel --prediction $LIVE/pred_live_turnlabel.parquet --nav $NAV --out $LIVE/targets/shadow_c4_turnlabel_iw_o1000 --cfg "$CFG_C4" | grep -E '"asof"|target_invested|target_share500|n_orders'; } || log "shadow c4 skipped or failed (ignored)"
fi
if [ -f $LIVE/pred_live_turnlabel.parquet ]; then
  log "shadow c3-B"; $PY -m quanta_agents.factor_lab_a.live target --panel-dir $LIVE/panel --prediction $LIVE/pred_live_turnlabel.parquet --nav $NAV --out $LIVE/targets/shadow_c3B_turnlabel_conc80k4_84_14 --cfg "$CFG_C3B" | grep -E '"asof"|target_invested|target_share500|n_orders'
fi
# A18 candidate c5 (shadow, records only): c4 scores + c4 rule + fundamental distress veto (worst 10% of CSI500 on the frozen composite, direction fixed) + free-float cap weights
FZ5=F:/A_Layer_Research/competition/frozen/c5_396_turnlabel_ens5_fdveto2_freecap
if [ -f $LIVE/pred_live_turnlabel.parquet ] && [ -f $LIVE/panel/fd_free_cap.parquet ] && [ -f $FZ5/rule.json ]; then
  VEXPR="cs_rank(-fd_accrual_cf) + cs_rank(-fd_audit_nonstd) + cs_rank(-clip(fd_hnum_chg, -1, 3)) + cs_rank(-fd_hsell_60) + cs_rank(-fd_pledge) + cs_rank(clip(fd_rev_q_yoy, -3, 3)) + cs_rank(cs_rank(clip(fd_sue, -5, 5)) * cs_rank(-rolling_mean(turnover, 20)))"
  log "fundamental veto mask (live)"; $PY scripts/fd_arm_inputs.py veto --panel-dir $LIVE/panel --direction 1 --q 0.10 --out $LIVE/fd_veto_distress10_v2.parquet --expr "$VEXPR" 2>&1 | tail -1 || log "veto mask failed (ignored)"
  CFG_C5='{"book":"enhanced","gamma":1.0,"x_out":0.2,"x_in":0.35,"y_in":0.9,"y_out":0.75,"tau":0.0,"cap_field":"fd_free_cap","smooth":5,"w500":0.85,"woth":0.13,"noth":110,"keep_mult":4.0,"neutral_q":5,"neutral_field":"turnover20","lot_group_below":true,"veto_mask":"F:/A_Layer_Live/fd_veto_distress10_v2.parquet","oth_universe":"csi1000"}'
  [ -f $LIVE/fd_veto_distress10_v2.parquet ] && { log "shadow c5"; $PY -m quanta_agents.factor_lab_a.live target --panel-dir $LIVE/panel --prediction $LIVE/pred_live_turnlabel.parquet --nav $NAV --out $LIVE/targets/shadow_c5_fdveto2_freecap --cfg "$CFG_C5" | grep -E '"asof"|target_invested|target_share500|n_orders|vetoed'; } || log "shadow c5 skipped or failed (ignored)"
fi
log "done; targets in $LIVE/targets"
