#!/bin/bash
#SBATCH --partition=scrp
#SBATCH --account=ug4
#SBATCH --qos=c16g1
#SBATCH --cpus-per-task=4
#SBATCH --mem=24G
#SBATCH --gpus=rtx3090:1
#SBATCH --time=06:00:00
#SBATCH --job-name=gru-wf
#SBATCH --array=0-29
#SBATCH --output=outputs/gru-wf-%A_%a.log
# Walk-forward retraining of the frozen control GRU: 6 test years x 5 seeds = 30 array tasks.
#   task id = 5*k + j ; k = 0..5 -> test year T = 2021+k ; j = 0..4 -> seed in (11 22 33 44 55)
#   validation = calendar year T-1 ; training = WF_TRAIN_START_ALL (default 2019-01-01) .. (T-1)-01-01
#   test      = T-01-01 .. (T+1)-01-01, except T=2026 -> 2026-09-24 (panel ends 2026-09-23)
# Output: outputs/gru_walkforward_20261010/T${T}/control_seed${S}/
# Partial submission: sbatch --array=0-4 gru_walkforward_20261010/walkforward.sbatch  (T=2021 only)
# Longer history for every window: sbatch --export=ALL,WF_TRAIN_START_ALL=2018-01-01 gru_walkforward_20261010/walkforward.sbatch
set -euo pipefail
cd /home/users/s1155284034/gru_hourly_20261001
export OMP_NUM_THREADS=4 MKL_NUM_THREADS=4
export GRU_DATA=${GRU_DATA:-/data/users/s1155284034/gru_hourly_20261001/dataset}
PY=/opt/network/anaconda3/envs/pytorch/bin/python

SEEDS=(11 22 33 44 55)
ID=${SLURM_ARRAY_TASK_ID:?run with sbatch --array}
K=$((ID / 5)); J=$((ID % 5))
if (( K < 0 || K > 5 )); then echo "array index $ID out of range 0-29" >&2; exit 1; fi
T=$((2021 + K)); S=${SEEDS[$J]}
export WF_TRAIN_START=${WF_TRAIN_START_ALL:-2019-01-01}
export WF_TRAIN_END=$((T - 1))-01-01
export WF_VAL_START=$((T - 1))-01-01
export WF_VAL_END=${T}-01-01
export WF_TEST_START=${T}-01-01
if (( T == 2026 )); then export WF_TEST_END=2026-09-24; else export WF_TEST_END=$((T + 1))-01-01; fi
export GRU_RUN_ROOT=outputs/gru_walkforward_20261010/T${T}
mkdir -p "$GRU_RUN_ROOT"

echo "task=$ID T=$T seed=$S train=$WF_TRAIN_START..$WF_TRAIN_END val=$WF_VAL_START..$WF_VAL_END test=$WF_TEST_START..$WF_TEST_END root=$GRU_RUN_ROOT"
"$PY" -u -m gru_walkforward_20261010.experiment_wf describe
"$PY" -u -m gru_walkforward_20261010.experiment_wf train  --variant control --seed "$S"
"$PY" -u -m gru_walkforward_20261010.experiment_wf export --variant control --seed "$S"
