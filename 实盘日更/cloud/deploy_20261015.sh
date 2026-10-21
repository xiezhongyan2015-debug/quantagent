#!/bin/zsh
# Deploy the 2026-10-15 shadow changes to the cloud, step by step, each verifiable. Run from the Mac after
# upload_parts.py reports MATCH for push_oldrank.tgz (and push_t2025.tgz) and sync_code_20261015.sh has run.
#   1. extract model archives into C:\ChoiceBridge\quant\current (oldrank checkpoints; T2025 best.pt)
#   2. score the lite frozen tail with the three model sets (EMA warm-up for the shadow ensembles)
#   3. smoke test: signals_ext.py 15 all15 for the latest scored session must write a z file
#   4. register the intraday shadow tasks (11:36 slot 11, 14:06 slot 14)
# Nothing here touches the live target path, the executor or its start time.
set -e
cd "/Users/leslie/Downloads/中证500量化60min"; CFG=choice_cloud_prepare_20261011/ssh_config; PY='C:\ChoiceBridge\runtime\gru-venv\Scripts\python.exe'
R() { ssh -F $CFG -o BatchMode=yes -o ServerAliveInterval=15 choice-executor-key "$@" 2>&1 | grep -v "post-quantum\|store now\|openssh.com" | tr -d '\r'; }
case "${1:-all}" in
  extract|all)
    R "$PY -X utf8 -c \"import tarfile,os; [tarfile.open(r'C:/ChoiceBridge/quant/'+n).extractall(r'C:/ChoiceBridge/quant/current') for n in ['push_oldrank.tgz','push_t2025.tgz'] if os.path.exists(r'C:/ChoiceBridge/quant/'+n)]; print(sorted(os.listdir(r'C:/ChoiceBridge/quant/current/gru_live_extend_20261012/models/oldrank'))); print(os.path.exists(r'C:/ChoiceBridge/quant/current/outputs/gru_walkforward_20261010/T2025/control_seed11/best.pt'))\""
    ;|
  score|all)
    # lite tail starts 2026-03; windows need 80 hours, so score from 2026-05-06 (~95 sessions -> WARM=60 at every slot)
    R "cd /d C:\ChoiceBridge\quant\current && $PY -X utf8 gru_live_extend_20261012/score_frozen_models.py --start 2026-05-06 --pkg C:/ChoiceBridge/quant/current/gru_live_extend_20261012/models/oldrank 2>&1 | findstr /i \"DONE error Traceback\""
    ;|
  smoke|all)
    R "cd /d C:\ChoiceBridge\quant\current && $PY -X utf8 gru_live_extend_20261012/signals_ext.py 15 all15 2>&1 | findstr /v Warning | more +0" | tail -3
    R "dir /b C:\ChoiceBridge\quant\current\outputs\gru_live_extend_20261012\signals | findstr all15"
    ;|
  tasks|all)
    R "powershell -NoProfile -ExecutionPolicy Bypass -File C:\ChoiceBridge\quant\current\实盘日更\cloud\register_shadow_tasks.ps1"
    ;;
esac
