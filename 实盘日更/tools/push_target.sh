#!/bin/zsh
# Push one execution day's target file (+precheck, +csv) to the cloud machine's staging area and verify SHA256 there. Key-based, no password.
# Usage: push_target.sh <exec_date>
set -e
cd "/Users/leslie/Downloads/中证500量化60min"; D=${1:?exec_date}; SRC=实盘日更/outputs/targets/$D; CFG=choice_cloud_prepare_20261011/ssh_config
[ -f $SRC/target_$D.json ] || { echo "no released target for $D"; exit 2; }
LOCAL=$(shasum -a 256 $SRC/target_$D.json | cut -d' ' -f1)
ssh -F $CFG -o BatchMode=yes choice-executor-key "powershell -NoProfile -Command \"New-Item -ItemType Directory -Force -Path C:\\ChoiceBridge\\staging\\targets\\$D | Out-Null\"" 2>/dev/null
scp -q -F $CFG $SRC/target_$D.json $SRC/target_$D.csv $SRC/precheck_$D.json "choice-executor-key:C:/ChoiceBridge/staging/targets/$D/" 2>/dev/null
REMOTE=$(ssh -F $CFG -o BatchMode=yes choice-executor-key "powershell -NoProfile -Command \"(Get-FileHash C:\\ChoiceBridge\\staging\\targets\\$D\\target_$D.json -Algorithm SHA256).Hash.ToLower()\"" 2>/dev/null | tr -d '\r')
if [ "$LOCAL" = "$REMOTE" ]; then echo "PUSHED $D sha256 ${LOCAL:0:16} verified on server: C:/ChoiceBridge/staging/targets/$D/"; else echo "HASH MISMATCH local=$LOCAL remote=$REMOTE"; exit 3; fi
