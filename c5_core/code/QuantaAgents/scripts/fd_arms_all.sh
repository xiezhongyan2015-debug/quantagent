#!/usr/bin/env bash
# A18 step 3 master driver: after stage 2b, run arm A on the strict and the relaxed member sets, then arms B/C/D — one
# retrain at a time (commit-memory bound).  Re-runnable: finished predictions are skipped by the arm scripts.
set -uo pipefail; cd "$(dirname "$0")/.."
L=/Users/leslie/Downloads/中证500量化60min/c5_core/data/A_Layer_Research/fundamental/logs
log() { echo "$(date +%H:%M:%S) $*"; }
until grep -q "FD_STAGE2B_DONE" $L/stage2b_driver.log 2>/dev/null; do sleep 60; done
log "arm A strict"; bash scripts/fd_arm_a.sh /Users/leslie/Downloads/中证500量化60min/c5_core/data/A_Layer_Research/competition/fd/members/m396_plus_fd.json > $L/armA_fd.log 2>&1; log "arm A strict rc=$?"
log "arm A relaxed"; bash scripts/fd_arm_a.sh /Users/leslie/Downloads/中证500量化60min/c5_core/data/A_Layer_Research/competition/fd/members/m396_plus_fd_relaxed.json > $L/armA_fd_relaxed.log 2>&1; log "arm A relaxed rc=$?"
log "arms B/C/D"; bash scripts/fd_arm_bcd.sh > $L/arm_bcd.log 2>&1; log "arms B/C/D rc=$?"
log "FD_ARMS_ALL_DONE"
