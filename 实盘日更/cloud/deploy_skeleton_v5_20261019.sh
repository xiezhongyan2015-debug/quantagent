#!/bin/zsh
# 2026-10-19 user instruction: skeleton v5 goes live = K1 zero-row backfill + sqrt(b) additive tilt + beta tilt without the
# in-episode stop. First pipeline that uses it: 10-20 16:30 (signal 10-20 -> execution 10-21), declared as a switch day
# (turnover gate 0.70) because the backfill adds ~25 names once.
# Usage: deploy_skeleton_v5_20261019.sh <lambda>
# Rollback on the cloud: delete C:\ChoiceBridge\quant\current\实盘日更\config\skeleton_v5.json (daily.sh then runs the old path),
# and restore timing_research_20261017\beta_tilt_shadow.py from its .bak_20261019 copy to bring the stop back.
set -e
LAMBDA=${1:?usage: deploy_skeleton_v5_20261019.sh <lambda>}
cd "/Users/leslie/Downloads/中证500量化60min"; CFG=choice_cloud_prepare_20261011/ssh_config
cat > 实盘日更/config/skeleton_v5.json <<EOF
{"enabled": true, "backfill": true, "backfill_holdings": "实盘日更/config/efund_holdings", "tilt_form": "sqrtb", "tilt_lambda": $LAMBDA,
 "decided": "2026-10-19 user instruction", "first_signal_day": "2026-10-20",
 "replay": "outputs/skeleton_v5_replay_20261019", "rule": "pcf_overlay_20261012/skeleton_k1.py"}
EOF
FILES=(pcf_overlay_20261012/live_targets.py pcf_overlay_20261012/skeleton_k1.py 实盘日更/tools/make_prices.py 实盘日更/daily.sh
       timing_research_20261017/beta_tilt_shadow.py 实盘日更/config/skeleton_v5.json 实盘日更/cloud/deploy_skeleton_v5_20261019.sh
       实盘日更/config/efund_holdings/*(.))
for f in $FILES; do [ -f "$f" ] || { echo "missing $f"; exit 2; }; done
SP=/private/tmp/claude-501/-Users-leslie-Downloads---500--60min/d97cadd7-4d49-4ab3-873a-1a0a90ee2c71/scratchpad
T=$SP/skeleton_v5_20261019.tgz; COPYFILE_DISABLE=1 tar czf $T $FILES; ls -la $T
shasum -a 256 $FILES > $SP/skeleton_v5_local.sha256
scp -q -F $CFG $T choice-executor-key:C:/ChoiceBridge/quant/skeleton_v5_20261019.tgz
ssh -F $CFG -o BatchMode=yes choice-executor-key "C:\ChoiceBridge\runtime\gru-venv\Scripts\python.exe -X utf8 -c \"
import tarfile,hashlib,shutil,json,os
R='C:/ChoiceBridge/quant/current/'; t=tarfile.open('C:/ChoiceBridge/quant/skeleton_v5_20261019.tgz'); names=[m.name for m in t.getmembers() if m.isfile()]
for n in names:
    p=R+n
    if os.path.exists(p) and not os.path.exists(p+'.bak_20261019'): shutil.copy2(p,p+'.bak_20261019')
t.extractall(R)
for n in names: print(hashlib.sha256(open(R+n,'rb').read()).hexdigest()+'  '+n)
f='C:/ChoiceBridge/live/switch_days.json'; d=json.load(open(f,encoding='utf-8-sig')) if os.path.exists(f) else []
if '2026-10-20' not in d: d.append('2026-10-20'); shutil.copy2(f,f+'.bak_20261019') if os.path.exists(f) else None; json.dump(sorted(d),open(f,'w',encoding='utf-8'))
print('switch_days',json.load(open(f,encoding='utf-8')))
os.remove('C:/ChoiceBridge/quant/skeleton_v5_20261019.tgz')
\"" | tr -d '\r' > $SP/skeleton_v5_remote.sha256
cat $SP/skeleton_v5_remote.sha256 | tail -3
diff <(sort $SP/skeleton_v5_local.sha256) <(grep -v switch_days $SP/skeleton_v5_remote.sha256 | sort) && echo "REMOTE HASHES MATCH LOCAL"
