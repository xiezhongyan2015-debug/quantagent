#!/usr/bin/env bash
# A18 step 2: single-factor evaluation of the fundamental candidates.
#   evaluate (all-A admission on 2016-2018, FLA_WORKERS) -> rank arrays on the research panel -> member files
#   -> in-bucket re-evaluation 2019-2024 (csi500 / union / csi1000) -> summary table.
set -uo pipefail; cd "$(dirname "$0")/.."; export PYTHONPATH=src PYTHONIOENCODING=utf-8
PY=.venv/Scripts/python.exe; R=F:/A_Layer_Research; FDD=$R/competition/fd; L=$R/fundamental/logs
mkdir -p $FDD/members $FDD/eval $FDD/ranks_research
log() { echo "$(date +%H:%M:%S) $*"; }
quiet() { while true; do H=$(date +%H%M); H=$((10#$H)); if [ "$H" -ge 300 ] && [ "$H" -le 410 ]; then log "quiet window, sleeping"; sleep 300; else break; fi; done; }
# quiet (window already passed)
log "evaluate"; FLA_WORKERS=${FLA_WORKERS:-8} $PY scripts/fd_factors.py evaluate --item 0 --workers ${FLA_WORKERS:-8} > $L/stage2_evaluate.log 2>&1; log "evaluate rc=$?"
log "ranks"; $PY scripts/fd_factors.py ranks --item 0 --panel-dir $R/panel --out $FDD/ranks_research > $L/stage2_ranks.log 2>&1; log "ranks rc=$?"
$PY scripts/fd_factors.py members --item 1 2 3 4 5 6 7 --admitted-only --out $FDD/members/m396_plus_fd.json > $L/stage2_members.log 2>&1
$PY scripts/fd_factors.py members --item 1 2 3 4 5 6 7 --out $FDD/members/m396_plus_fd_all.json >> $L/stage2_members.log 2>&1
$PY - <<'EOF' >> $L/stage2_members.log 2>&1
import json
d = json.load(open("F:/A_Layer_Research/competition/fd/members/m396_plus_fd_all.json", encoding="utf-8"))
json.dump({"members": d["new_members"]}, open("F:/A_Layer_Research/competition/fd/members/fd_ids.json", "w"), indent=1)
adm = json.load(open("F:/A_Layer_Research/competition/fd/members/m396_plus_fd.json", encoding="utf-8"))
json.dump({"members": adm["new_members"]}, open("F:/A_Layer_Research/competition/fd/members/fd_ids_admitted.json", "w"), indent=1)
print("fd ids:", len(d["new_members"]), "admitted:", len(adm["new_members"]))
EOF
log "member_eval"; $PY scripts/competition_member_eval.py --root $R --panel-dir $R/panel --ranks-dir $FDD/ranks_research --ids-file $FDD/members/fd_ids.json --step 2 --extra-universe csi1000 --out $FDD/eval/member_eval_fd.csv > $L/stage2_member_eval.log 2>&1; log "member_eval rc=$?"
$PY scripts/fd_factors.py table --item 0 --member-eval $FDD/eval/member_eval_fd.csv --out $FDD/eval/fd_table.csv > $L/stage2_table.log 2>&1; log "table rc=$?"
log "FD_STAGE2_DONE"
