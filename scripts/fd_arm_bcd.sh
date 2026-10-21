#!/usr/bin/env bash
# A18 step 3, arms B / C / D on the research panel (2019-2024), two seeds, c4 rule set.
#   B: fundamental-only LightGBM (admitted fd members, turnover-neutral label) rank-averaged with the 396 turnover-label model (0.7/0.3 and 0.5/0.5)
#   C: c4 + additional veto of the worst 10% distress-score names inside the CSI500 bucket (rule-based composite, no model)
#   D: other bucket only: CSI500 keeps the price-volume score, other names get the 0.5/0.5 blend
# Baselines: same-seed 396 turnover-label models (A15 pv2 seeds 0/1).  Skips finished steps.  Pauses 03:00-04:10 machine time.
set -uo pipefail; cd "$(dirname "$0")/.."; export PYTHONPATH=src PYTHONIOENCODING=utf-8
PY=.venv/Scripts/python.exe; R=F:/A_Layer_Research; PV=$R/competition/pv4; FDD=$R/competition/fd; P2=$R/competition/pv2/combos; C=$FDD/combos; E=$FDD/eval; L=$FDD/logs
mkdir -p $C $E $L
log() { echo "$(date +%H:%M:%S) $*"; }
quiet() { while true; do H=$(date +%H%M); H=$((10#$H)); if [ "$H" -ge 300 ] && [ "$H" -le 410 ]; then log "quiet window, sleeping"; sleep 300; else break; fi; done; }
run() { tag=$1; shift; if [ -f $C/pred_$tag.parquet ]; then log "skip $tag"; return 0; fi; quiet; rm -rf $R/batches/tmp_lowmem_$tag; log "start $tag"
  $PY scripts/competition_union_combine.py --universe all_a --rerank none --label-blend 5 10 20 --ranks-dir $FDD/ranks_research $R/pool_ranks $R/cand_ranks --out-dir $C --tag $tag "$@" > $L/$tag.log 2>&1; log "end $tag rc=$?"; }

# fundamental-only models (admitted members only), seeds 0/1
for S in 0 1; do run fdonly_turnlabel_s$S --members-file $FDD/members/fd_ids_relaxed.json --label-neutral-field turnover20 --label-neutral-q 5 --seed $S; done

# arm B blends and arm D mixes against the same-seed price-volume baselines
for S in 0 1; do
  PVP=$P2/pred_pv2_turnlabel_s$S.parquet; FDP=$C/pred_fdonly_turnlabel_s$S.parquet
  [ -f $C/blend73_s$S.parquet ] || $PY scripts/fd_arm_inputs.py blend --pv $PVP --fd $FDP --w 0.7 0.3 --out $C/blend73_s$S.parquet >> $L/inputs.log 2>&1
  [ -f $C/blend55_s$S.parquet ] || $PY scripts/fd_arm_inputs.py blend --pv $PVP --fd $FDP --w 0.5 0.5 --out $C/blend55_s$S.parquet >> $L/inputs.log 2>&1
  [ -f $C/mix55_s$S.parquet ] || $PY scripts/fd_arm_inputs.py mix --pv $PVP --fd $FDP --w 0.5 0.5 --out $C/mix55_s$S.parquet >> $L/inputs.log 2>&1
done
# arm C veto mask from the rule-based distress composite (direction fixed on 2016-2018 inside CSI500)
[ -f $E/veto_distress10.parquet ] || $PY scripts/fd_arm_inputs.py veto --q 0.10 --out $E/veto_distress10.parquet \
  --expr "cs_rank(-fd_accrual_cf) + cs_rank(-fd_debt_ib) + cs_rank(-clip(fd_goodwill_eq, 0, 5)) + cs_rank(-fd_audit_nonstd) + cs_rank(-fd_fc_neg) + cs_rank(-fd_hsell_60) + cs_rank(-fd_pledge)" >> $L/inputs.log 2>&1
# veto v2: composite chosen after the 2019-2024 in-bucket single-factor table (accruals, audit, holder-count growth, insider selling,
# pledge, revenue growth, SUE x low turnover) — selection on the research period, reported next to v1
[ -f $E/veto_distress10_v2.parquet ] || $PY scripts/fd_arm_inputs.py veto --q 0.10 --out $E/veto_distress10_v2.parquet   --expr "cs_rank(-fd_accrual_cf) + cs_rank(-fd_audit_nonstd) + cs_rank(-clip(fd_hnum_chg, -1, 3)) + cs_rank(-fd_hsell_60) + cs_rank(-fd_pledge) + cs_rank(clip(fd_rev_q_yoy, -3, 3)) + cs_rank(cs_rank(clip(fd_sue, -5, 5)) * cs_rank(-rolling_mean(turnover, 20)))" >> $L/inputs.log 2>&1
$PY - <<EOF
import json
r = json.load(open("$PV/eval/c4_rules_research.json", encoding="utf-8"))
r["c4_fdveto"] = dict(r["c4"], veto_mask="$E/veto_distress10.parquet")
r["c4_fdveto2"] = dict(r["c4"], veto_mask="$E/veto_distress10_v2.parquet")
r["c4_fdveto2_x10"] = dict(r["c4"], veto_mask="$E/veto_distress10_v2.parquet", x_out=0.10, x_in=0.25)
json.dump({"c4": r["c4"], "c4_fdveto": r["c4_fdveto"], "c4_fdveto2": r["c4_fdveto2"], "c4_fdveto2_x10": r["c4_fdveto2_x10"]}, open("$E/rules_arm_bcd.json", "w", encoding="utf-8"), indent=1)
EOF
quiet
$PY scripts/competition_compare.py --panel-dir $R/panel \
  --pred "base_s0=$P2/pred_pv2_turnlabel_s0.parquet" --pred "base_s1=$P2/pred_pv2_turnlabel_s1.parquet" \
  --pred "fdonly_s0=$C/pred_fdonly_turnlabel_s0.parquet" --pred "fdonly_s1=$C/pred_fdonly_turnlabel_s1.parquet" \
  --pred "blend73_s0=$C/blend73_s0.parquet" --pred "blend73_s1=$C/blend73_s1.parquet" \
  --pred "blend55_s0=$C/blend55_s0.parquet" --pred "blend55_s1=$C/blend55_s1.parquet" \
  --pred "mix55_s0=$C/mix55_s0.parquet" --pred "mix55_s1=$C/mix55_s1.parquet" \
  --rules --rule-json "$(cat $E/rules_arm_bcd.json)" --out $E/compare_arm_bcd_research.json > $L/compare_arm_bcd.log 2>&1
log "FD_ARM_BCD_DONE rc=$?"
