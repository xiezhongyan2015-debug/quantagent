#!/usr/bin/env bash
# A18 steps 0-1 driver (parallel): each grid part starts as soon as its consolidated tables exist and runs in its own
# process; ratios follow the four statement parts; then one-pass assembly onto the four panels, validation, audit.
# Idempotent (a part is skipped when its summary entry exists).  Pauses 03:00-04:10 machine time (15:00-16:10 Beijing).
set -uo pipefail; cd "$(dirname "$0")/.."; export PYTHONPATH=src PYTHONIOENCODING=utf-8
PY=.venv/Scripts/python.exe; FD=F:/A_Layer_Research/fundamental; RAW=$FD/raw; G=$FD/grid; L=$FD/logs
log() { echo "$(date +%H:%M:%S) $*"; }
quiet() { while true; do H=$(date +%H%M); H=$((10#$H)); if [ "$H" -ge 300 ] && [ "$H" -le 410 ]; then log "quiet window, sleeping"; sleep 300; else break; fi; done; }
wait_for() { for f in "$@"; do until [ -f "$f" ]; do sleep 20; done; done; }
built() { $PY -c "import json,os,sys; p='$G/_build_summary.json'; d=json.load(open(p)) if os.path.exists(p) else {}; sys.exit(0 if '$1' in d else 1)"; }
build() { part=$1; shift; if built $part; then log "skip build $part"; return 0; fi; wait_for "$@"; quiet; log "build $part"; $PY scripts/fd_features.py build --parts $part > $L/build_$part.log 2>&1; log "build $part rc=$?"; }

build income $RAW/income.parquet &
build balancesheet $RAW/balancesheet.parquet &
build cashflow $RAW/cashflow.parquet &
build fina_indicator $RAW/fina_indicator.parquet &
build events $RAW/forecast.parquet $RAW/express.parquet $RAW/dividend.parquet $RAW/stk_holdertrade.parquet $RAW/share_float.parquet $RAW/pledge_stat.parquet $RAW/stk_holdernumber.parquet $RAW/repurchase.parquet $RAW/top10_floatholders.parquet $RAW/fina_audit.parquet &
build analyst $RAW/report_rc.parquet &
wait
build ratios $G/fd_np_ttm.parquet $G/fd_equity.parquet $G/fd_ocf_ttm.parquet $G/fd_dedt_ttm.parquet
quiet; log "assemble"; $PY scripts/fd_features.py assemble > $L/assemble.log 2>&1; log "assemble rc=$?"
log "validate"; $PY scripts/fd_features.py validate --panel-dir F:/A_Layer_OOS/p2026/panel > $L/validate_p2026.log 2>&1; log "validate p2026 rc=$?"
$PY scripts/fd_features.py validate --panel-dir F:/A_Layer_Research/panel > $L/validate_research.log 2>&1; log "validate research rc=$?"
log "audit"; $PY scripts/fd_audit.py > $L/audit.log 2>&1; log "audit rc=$?"
log "FD_PIPELINE_DONE"
