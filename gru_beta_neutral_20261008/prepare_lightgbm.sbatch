#!/bin/bash
#SBATCH --partition=scrp
#SBATCH --account=ug4
#SBATCH --qos=c16g1
#SBATCH --cpus-per-task=4
#SBATCH --mem=16G
#SBATCH --time=02:00:00
#SBATCH --job-name=gru-lgbm-input
#SBATCH --output=outputs/gru-lgbm-input-%j.log
set -euo pipefail
cd /home/users/s1155284034/gru_hourly_20261001
export GRU_DATA=/data/users/s1155284034/gru_hourly_20261001/dataset
export OMP_NUM_THREADS=4 MKL_NUM_THREADS=4 OPENBLAS_NUM_THREADS=4
/opt/network/anaconda3/envs/pytorch/bin/python -u -m gru_beta_neutral_20261008.lightgbm_baseline prepare
