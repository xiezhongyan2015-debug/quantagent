#!/usr/bin/env bash
# A18 candidate c5 assembly test on the frozen 5-seed turnover-label ensemble (research panel 2019-2024):
#   c4 | c4 + fundamental veto v2 | + exclusion 10% | each with float x implied weights vs free-float cap weights.
set -uo pipefail; cd "$(dirname "$0")/.."; export PYTHONPATH=src PYTHONIOENCODING=utf-8
PY=.venv/Scripts/python.exe; R=F:/A_Layer_Research; PV=$R/competition/pv4; E=$R/competition/fd/eval; L=$R/fundamental/logs
log() { echo "$(date +%H:%M:%S) $*"; }
until grep -q "FD_ARM_BCD_DONE" $L/arm_bcd.log 2>/dev/null; do sleep 20; done
$PY - <<PYEOF
import json
c4 = json.load(open("$PV/eval/c4_rules_research.json", encoding="utf-8"))["c4"]
nocm = {k: v for k, v in c4.items() if k != "cap_mult"}
veto = "$E/veto_distress10_v2.parquet"
rules = {
  "c4": c4,
  "c4_veto2": dict(c4, veto_mask=veto),
  "c4_veto2_x10": dict(c4, veto_mask=veto, x_out=0.10, x_in=0.25),
  "c4_veto2_x15": dict(c4, veto_mask=veto, x_out=0.15, x_in=0.30),
  "c5_free": dict(nocm, cap_field="fd_free_cap", veto_mask=veto),
  "c5_free_x10": dict(nocm, cap_field="fd_free_cap", veto_mask=veto, x_out=0.10, x_in=0.25),
  "c5_free_x15": dict(nocm, cap_field="fd_free_cap", veto_mask=veto, x_out=0.15, x_in=0.30),
  "c5_free_veto15": dict(nocm, cap_field="fd_free_cap", veto_mask="$E/veto_distress15_v2.parquet"),
}
json.dump(rules, open("$E/rules_c5.json", "w", encoding="utf-8"), indent=1)
PYEOF
[ -f $E/veto_distress15_v2.parquet ] || $PY scripts/fd_arm_inputs.py veto --q 0.15 --out $E/veto_distress15_v2.parquet \
  --expr "cs_rank(-fd_accrual_cf) + cs_rank(-fd_audit_nonstd) + cs_rank(-clip(fd_hnum_chg, -1, 3)) + cs_rank(-fd_hsell_60) + cs_rank(-fd_pledge) + cs_rank(clip(fd_rev_q_yoy, -3, 3)) + cs_rank(cs_rank(clip(fd_sue, -5, 5)) * cs_rank(-rolling_mean(turnover, 20)))" >> $R/competition/fd/logs/inputs.log 2>&1
log "c5 compare (5-seed ensemble)"
$PY scripts/competition_compare.py --panel-dir $R/panel --pred "turn_ens5=$PV/combos/ens_turnlabel_s01234.parquet" \
  --rules --rule-json "$(cat $E/rules_c5.json)" --out $E/compare_c5_research.json > $R/competition/fd/logs/compare_c5.log 2>&1
log "FD_C5_COMPARE_DONE rc=$?"
