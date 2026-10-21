#!/bin/bash
#SBATCH --partition=scrp
#SBATCH --account=ug4
#SBATCH --qos=c16g1
#SBATCH --cpus-per-task=4
#SBATCH --mem=24G
#SBATCH --gpus=rtx3090:1
#SBATCH --time=02:00:00
#SBATCH --job-name=gru-A3-export
#SBATCH --output=outputs/gru-A3-%j.log
set -euo pipefail
cd /home/users/s1155284034/gru_hourly_20261001
export OMP_NUM_THREADS=4 MKL_NUM_THREADS=4
export GRU_DATA=/data/users/s1155284034/gru_hourly_20261001/dataset
export WF_TRAIN_START=2019-01-01 WF_TRAIN_END=2024-01-01
export WF_VAL_START=2024-01-01 WF_VAL_END=2025-01-01
export WF_TEST_START=2025-01-01 WF_TEST_END=2026-01-01
export GRU_RUN_ROOT=outputs/gru_walkforward_20261010/T2025
for seed in 11 22 33 44 55; do
 /opt/network/anaconda3/envs/pytorch/bin/python -u -m gru_dualplatform_20261011.legacy --seed "$seed"
done
