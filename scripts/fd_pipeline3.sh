#!/usr/bin/env bash
# A18 resume after the drive outage: events grid -> one-pass assembly onto the four panels -> validation -> audit -> stage 2.
set -uo pipefail; cd "$(dirname "$0")/.."; export PYTHONPATH=src PYTHONIOENCODING=utf-8
PY=.venv/Scripts/python.exe; FD=F:/A_Layer_Research/fundamental; G=$FD/grid; L=$FD/logs
log() { echo "$(date +%H:%M:%S) $*"; }
built() { $PY -c "import json,os,sys; p='$G/_build_summary.json'; d=json.load(open(p)) if os.path.exists(p) else {}; sys.exit(0 if '$1' in d else 1)"; }
if built events; then log "skip build events"; else log "build events"; $PY scripts/fd_features.py build --parts events > $L/build_events.log 2>&1; log "build events rc=$?"; fi
log "assemble"; $PY scripts/fd_features.py assemble > $L/assemble.log 2>&1; log "assemble rc=$?"
log "validate"; $PY scripts/fd_features.py validate --panel-dir F:/A_Layer_OOS/p2026/panel > $L/validate_p2026.log 2>&1; log "validate p2026 rc=$?"
$PY scripts/fd_features.py validate --panel-dir F:/A_Layer_Research/panel > $L/validate_research.log 2>&1; log "validate research rc=$?"
log "audit"; $PY scripts/fd_audit.py > $L/audit.log 2>&1; log "audit rc=$?"
log "FD_PIPELINE_DONE"
log "stage2"; bash scripts/fd_stage2.sh > $L/stage2.log 2>&1; log "stage2 rc=$?"
log "FD_STAGE2_CHAIN_DONE"
