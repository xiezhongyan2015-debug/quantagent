#!/bin/zsh
# Sync the 2026-10-15 chain changes (multi-model ensembles, shadow slots, industry refill, intraday slow rows) to the cloud
# quant package C:\ChoiceBridge\quant\current. Code and small config only; model weights go separately (cloud_push_models.tgz).
# The live files are NOT changed by these edits unless the operator switches: z_<day>_slot15.csv is still produced by the
# unchanged ctrl5 path; live_targets defaults stay --refill prorata; the executor start time stays 09:30.
set -e
cd "/Users/leslie/Downloads/中证500量化60min"; CFG=choice_cloud_prepare_20261011/ssh_config
FILES=(pcf_overlay_20261012/live_targets.py gru_live_extend_20261012/assemble_infer.py gru_live_extend_20261012/signals_ext.py gru_live_extend_20261012/blend_signal.py
       gru_live_extend_20261012/daily_extend.py gru_live_extend_20261012/score_frozen_models.py gru_live_extend_20261012/run_chain.sh
       实盘日更/tools/make_industry_map.py 实盘日更/config/industry_sw_l1_20260924.json 实盘日更/cloud/executor_daily/patch_executor_start_time.py
       实盘日更/cloud/shadow_intraday.ps1 实盘日更/cloud/sync_code_20261015.sh 实盘日更/daily.sh 实盘日更/tools/shadow_compare.py 实盘日更/cloud/register_shadow_tasks.ps1 实盘日更/cloud/executor_daily/patch_executor_windows.py 实盘日更/tools/auto_resume.py 实盘日更/tools/preflight.py 实盘日更/cloud/register_hardening_tasks.ps1 实盘日更/cloud/executor_daily/corporate_actions.py)
for f in $FILES; do [ -f "$f" ] || { echo "missing $f"; exit 2; }; done
T=/private/tmp/claude-501/-Users-leslie-Downloads---500--60min/3881df40-0168-4cda-bd66-a5478b75c001/scratchpad/code_sync_20261015.tgz
tar czf $T $FILES; ls -la $T
scp -q -F $CFG $T choice-executor-key:C:/ChoiceBridge/quant/code_sync_20261015.tgz
ssh -F $CFG -o BatchMode=yes choice-executor-key "C:\ChoiceBridge\runtime\gru-venv\Scripts\python.exe -X utf8 -c \"import tarfile,hashlib; t=tarfile.open(r'C:/ChoiceBridge/quant/code_sync_20261015.tgz'); names=t.getnames(); t.extractall(r'C:/ChoiceBridge/quant/current'); print('extracted', len(names)); print(hashlib.sha256(open(r'C:/ChoiceBridge/quant/current/gru_live_extend_20261012/signals_ext.py','rb').read()).hexdigest())\"" | tr -d '\r'
shasum -a 256 gru_live_extend_20261012/signals_ext.py | cut -c1-64
