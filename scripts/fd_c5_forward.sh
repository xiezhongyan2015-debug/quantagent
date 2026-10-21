#!/usr/bin/env bash
# A18 candidate c5: the ONE pre-registered look at 2025 / 2026-01..09-16 on the full panel (F:/A_Layer_OOS/p2026), 5-seed
# 2025/2026 ensemble scores.  Run once, after the c5 rule is fixed on 2019-2024.  The veto mask is rebuilt on the forward
# panel with the same expression and the same 2016-2018 direction rule.
set -uo pipefail; cd "$(dirname "$0")/.."; export PYTHONPATH=src PYTHONIOENCODING=utf-8
PY=.venv/Scripts/python.exe; R=F:/A_Layer_Research; PV=$R/competition/pv4; E=$R/competition/fd/eval; O=F:/A_Layer_OOS/p2026
log() { echo "$(date +%H:%M:%S) $*"; }
EXPR="cs_rank(-fd_accrual_cf) + cs_rank(-fd_audit_nonstd) + cs_rank(-clip(fd_hnum_chg, -1, 3)) + cs_rank(-fd_hsell_60) + cs_rank(-fd_pledge) + cs_rank(clip(fd_rev_q_yoy, -3, 3)) + cs_rank(cs_rank(clip(fd_sue, -5, 5)) * cs_rank(-rolling_mean(turnover, 20)))"
[ -f $E/veto_distress10_v2_p2026.parquet ] || $PY scripts/fd_arm_inputs.py veto --panel-dir $O/panel --q 0.10 --out $E/veto_distress10_v2_p2026.parquet --expr "$EXPR" >> $R/competition/fd/logs/inputs.log 2>&1
$PY - <<PYEOF
import json
c4 = json.load(open("$PV/eval/c4_rules_forward.json", encoding="utf-8"))["c4"]
nocm = {k: v for k, v in c4.items() if k != "cap_mult"}
veto = "$E/veto_distress10_v2_p2026.parquet"
# fixed BEFORE looking: c4 = A17 reference (already seen), c4_freecap = structural weight change alone, c4_veto2 = veto alone, c5_free = both
rules = {"c4": c4, "c4_freecap": dict(nocm, cap_field="fd_free_cap"), "c4_veto2": dict(c4, veto_mask=veto), "c5_free": dict(nocm, cap_field="fd_free_cap", veto_mask=veto)}
json.dump(rules, open("$E/rules_c5_forward.json", "w", encoding="utf-8"), indent=1)
PYEOF
log "c5 forward (one look)"
$PY scripts/competition_compare.py --panel-dir $O/panel --years 2025 2026 --pred "turn_ens5_2026=$PV/combos/ens_turnlabel_2026_s01234.parquet" \
  --rules --rule-json "$(cat $E/rules_c5_forward.json)" --out $E/compare_c5_forward.json > $R/competition/fd/logs/compare_c5_forward.log 2>&1
log "FD_C5_FORWARD_DONE rc=$?"
