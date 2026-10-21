#!/usr/bin/env bash
# A18 stage 2b: after the first evaluation pass finishes and the analyst grid is rebuilt (fd_rc_n90 empty window = 0),
# push the fixed report-count field onto the four panels, reset the analyst-dependent single-factor results and rerun stage 2.
set -uo pipefail; cd "$(dirname "$0")/.."; export PYTHONPATH=src PYTHONIOENCODING=utf-8
PY=.venv/Scripts/python.exe; L=/Users/leslie/Downloads/中证500量化60min/c5_core/data/A_Layer_Research/fundamental/logs
log() { echo "$(date +%H:%M:%S) $*"; }
until grep -q "FD_STAGE2_DONE" $L/stage2.log 2>/dev/null; do sleep 30; done
until grep -q "build done" $L/build_analyst2.log 2>/dev/null; do sleep 30; done
log "assemble fd_rc_n90"
$PY -c "
import sys; sys.path.insert(0,'src')
from quanta_agents.factor_lab_a import fundamental as fd
cov = fd.assemble_all(list(fd.PANELS.values()), fields=['fd_rc_n90'])
print({p: c for p, c in cov.items()})
" > $L/assemble_n90.log 2>&1; log "assemble rc=$?"
log "reset analyst results"; $PY scripts/fd_reset_analyst_eval.py > $L/reset_analyst.log 2>&1; log "reset rc=$?"
log "stage2 rerun"; FLA_WORKERS=3 bash scripts/fd_stage2.sh > $L/stage2_b.log 2>&1; log "stage2 rerun rc=$?"
$PY scripts/fd_members_relaxed.py > $L/members_relaxed.log 2>&1; log "relaxed members rc=$?"
log "FD_STAGE2B_DONE"
