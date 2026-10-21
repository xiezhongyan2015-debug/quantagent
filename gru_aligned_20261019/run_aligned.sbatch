#!/bin/bash
#SBATCH --partition=scrp
#SBATCH --account=ug4
#SBATCH --qos=c16g1
#SBATCH --cpus-per-task=4
#SBATCH --mem=24G
#SBATCH --gpus=rtx3090:1
#SBATCH --time=08:00:00
#SBATCH --job-name=gru-aligned
#SBATCH --array=0-89%15
#SBATCH --output=outputs/gru_aligned_20261019/logs/aligned-%A_%a.log
# Aligned-objective GRU, protocol v3 (gru_aligned_20261019/PROTOCOL_v3.json).
# 90 tasks = 6 arms x 3 walk-forward windows x 5 seeds:
#   ID = 15*a + 5*k + j ; a -> arm in (C0 G1a G1b G1c G2b G4) ; k -> test year T = 2024+k ; j -> seed in (11 22 33 44 55)
#   train 2019-01-01..(T-1)-01-01, validation year T-1, test year T (T=2026 ends 2026-09-24): same windows as the live ctrl5.
# "%15" limits concurrency to 15 GPUs; change it if more/fewer GPUs are free. Resubmitting resumes from last.pt.
set -euo pipefail
cd /home/users/s1155284034/gru_hourly_20261001
mkdir -p outputs/gru_aligned_20261019/logs
export OMP_NUM_THREADS=4 MKL_NUM_THREADS=4
export GRU_DATA=${GRU_DATA:-/data/users/s1155284034/gru_hourly_20261001/dataset}
export ALIGN_AUX=/home/users/s1155284034/gru_hourly_20261001/gru_aligned_20261019/aux/aligned_aux_20261019.npz
PY=/opt/network/anaconda3/envs/pytorch/bin/python
ARMS=(C0 G1a G1b G1c G2b G4); SEEDS=(11 22 33 44 55)
ID=${SLURM_ARRAY_TASK_ID:?run with sbatch --array}
A=$((ID / 15)); K=$(((ID % 15) / 5)); J=$((ID % 5))
export ALIGN_ARM=${ARMS[$A]}; T=$((2024 + K)); S=${SEEDS[$J]}
export WF_TRAIN_START=2019-01-01 WF_TRAIN_END=$((T - 1))-01-01 WF_VAL_START=$((T - 1))-01-01 WF_VAL_END=${T}-01-01 WF_TEST_START=${T}-01-01
if (( T == 2026 )); then export WF_TEST_END=2026-09-24; else export WF_TEST_END=$((T + 1))-01-01; fi
export GRU_RUN_ROOT=outputs/gru_aligned_20261019/T${T}
mkdir -p "$GRU_RUN_ROOT"
echo "task=$ID arm=$ALIGN_ARM T=$T seed=$S root=$GRU_RUN_ROOT"
"$PY" -u -m gru_aligned_20261019.aligned describe
"$PY" -u -m gru_aligned_20261019.aligned train --seed "$S"
"$PY" -u -m gru_aligned_20261019.aligned export --seed "$S"
