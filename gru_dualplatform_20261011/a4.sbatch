#!/bin/bash
#SBATCH --partition=scrp
#SBATCH --account=ug4
#SBATCH --qos=c16g1
#SBATCH --cpus-per-task=4
#SBATCH --mem=24G
#SBATCH --gpus=rtx3090:1
#SBATCH --time=06:00:00
#SBATCH --job-name=gru-A4-slot14
#SBATCH --array=0-29%1
#SBATCH --output=outputs/gru-A4-%A_%a.log
set -euo pipefail
cd /home/users/s1155284034/gru_hourly_20261001
export OMP_NUM_THREADS=4 MKL_NUM_THREADS=4
export GRU_DATA=/data/users/s1155284034/gru_hourly_20261001/dataset
export GRU_SWA_TAIL=0 GRU_TRAIN_SLOTS=1,2
SEEDS=(11 22 33 44 55)
ID=${SLURM_ARRAY_TASK_ID:?}
T=$((2021 + ID / 5)); S=${SEEDS[$((ID % 5))]}
export WF_TRAIN_START=2019-01-01 WF_TRAIN_END=$((T-1))-01-01
export WF_VAL_START=$((T-1))-01-01 WF_VAL_END=${T}-01-01
export WF_TEST_START=${T}-01-01 WF_TEST_END=$((T+1))-01-01
if ((T == 2026)); then export WF_TEST_END=2026-09-24; fi
export GRU_RUN_ROOT=outputs/gru_dualplatform_20261011/A4/T${T}
PY=/opt/network/anaconda3/envs/pytorch/bin/python
"$PY" -u -m gru_dualplatform_20261011.hourly describe
"$PY" -u -m gru_dualplatform_20261011.hourly train --seed "$S"
"$PY" -u -m gru_dualplatform_20261011.hourly export --seed "$S"
