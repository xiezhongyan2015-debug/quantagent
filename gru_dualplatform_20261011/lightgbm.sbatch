#!/bin/bash
# CPU comparison uses the same installed LightGBM 4.6.0 and fixed B protocol.
#SBATCH --partition=scrp
#SBATCH --account=ug4
#SBATCH --qos=c16g1
#SBATCH --cpus-per-task=4
#SBATCH --mem=24G
#SBATCH --time=06:00:00
#SBATCH --job-name=gru-B3-lgb
#SBATCH --array=0-5%3
#SBATCH --output=outputs/gru-B3-%A_%a.log
set -euo pipefail
cd /home/users/s1155284034/gru_hourly_20261001
export OMP_NUM_THREADS=4 MKL_NUM_THREADS=4 LGB_THREADS=4
export DAILY_PANEL=/data/users/s1155284034/gru_daily_20261011/daily_panel
export DAILY_OUTPUT=/data/users/s1155284034/gru_daily_20261011/B
ID=${SLURM_ARRAY_TASK_ID:?}
T=$((2021 + ID)); S=${LGB_SEED:?}
PY=/opt/network/anaconda3/envs/pytorch/bin/python
"$PY" -u -m gru_dualplatform_20261011.lightgbm_daily train --year "$T" --seed "$S"
if ((S == 55)); then
  "$PY" - "$T" <<'PY'
import json, shutil, sys
from gru_dualplatform_20261011.daily import OUT
year=int(sys.argv[1]); cache=OUT/'feature_cache'/f'T{year}'
receipts=[OUT/'LightGBM'/f'T{year}'/f'seed{s}'/'export.json' for s in [11,22,33,44,55]]
if all(p.exists() and json.loads(p.read_text())['status']=='complete' for p in receipts) and cache.exists():
    shutil.rmtree(cache)  # This runner's reproducible feature cache only.
PY
fi
