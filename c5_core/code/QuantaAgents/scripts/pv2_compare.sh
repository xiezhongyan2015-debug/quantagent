#!/usr/bin/env bash
# A15 standard comparison table: every prediction under the three competition rules (c2 turnover-neutral exclusion,
# c1 plain exclusion, concentrated 55/3/5), cost 0.004 (0.2% per side).
# usage: bash scripts/pv2_compare.sh <out-json> [--panel-dir P] [--years ...] [--extra-rules '{...}'] --pred label=path [--pred ...]
set -uo pipefail
cd "$(dirname "$0")/.."
export PYTHONPATH=src
PY=.venv/Scripts/python.exe
OUT=$1; shift
PANEL=/Users/leslie/Downloads/中证500量化60min/c5_core/data/A_Layer_Research/panel; YEARS="2019 2020 2021 2022 2023 2024"; EXTRA=""; PREDS=()
while [ $# -gt 0 ]; do
  case "$1" in
    --panel-dir) PANEL="$2"; shift 2;;
    --years) shift; YEARS=""; while [ $# -gt 0 ] && [[ "$1" != --* ]]; do YEARS="$YEARS $1"; shift; done;;
    --extra-rules) EXTRA="$2"; shift 2;;
    --pred) PREDS+=("--pred" "$2"); shift 2;;
    *) echo "unknown arg $1"; exit 2;;
  esac
done
C2='"c2":{"book":"enhanced","gamma":1.0,"x_out":0.2,"x_in":0.35,"y_in":0.9,"y_out":0.75,"tau":0.0,"cap_field":"float_market_cap","cost":0.004,"neutral_q":5,"neutral_field":"turnover20"}'
C1='"c1":{"book":"enhanced","gamma":1.0,"x_out":0.2,"x_in":0.35,"y_in":0.9,"y_out":0.75,"tau":0.0,"cap_field":"float_market_cap","cost":0.004}'
CC='"conc55":{"cost":0.004}'
RULES="{$C2,$C1,$CC"
if [ -n "$EXTRA" ]; then RULES="$RULES,${EXTRA#\{}"; else RULES="$RULES}"; fi
$PY scripts/competition_compare.py --panel-dir $PANEL --years $YEARS "${PREDS[@]}" --rules --rule-json "$RULES" --out "$OUT"
