#!/usr/bin/env bash
# A18 step 3 master driver, reordered after arm A (strict) showed no IC change: arms B/C/D first, then arm A (relaxed).
set -uo pipefail; cd "$(dirname "$0")/.."
L=/Users/leslie/Downloads/中证500量化60min/c5_core/data/A_Layer_Research/fundamental/logs
log() { echo "$(date +%H:%M:%S) $*"; }
until grep -q "FD_ARM_A_DONE" $L/armA_fd.log 2>/dev/null; do sleep 30; done
log "arms B/C/D"; bash scripts/fd_arm_bcd.sh > $L/arm_bcd.log 2>&1; log "arms B/C/D rc=$?"
log "arm A relaxed"; bash scripts/fd_arm_a.sh /Users/leslie/Downloads/中证500量化60min/c5_core/data/A_Layer_Research/competition/fd/members/m396_plus_fd_relaxed.json > $L/armA_fd_relaxed.log 2>&1; log "arm A relaxed rc=$?"
log "FD_ARMS_ALL2_DONE"
