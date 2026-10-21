#!/bin/bash
#SBATCH --partition=scrp
#SBATCH --account=ug4
#SBATCH --qos=c16g1
#SBATCH --cpus-per-task=2
#SBATCH --mem=12G
#SBATCH --time=02:00:00
#SBATCH --job-name=gru-lgbm-fusion
#SBATCH --output=outputs/gru-lgbm-fusion-%j.log
set -euo pipefail
cd /home/users/s1155284034/gru_hourly_20261001
export GRU_DATA=/data/users/s1155284034/gru_hourly_20261001/dataset
export OMP_NUM_THREADS=2 MKL_NUM_THREADS=2 OPENBLAS_NUM_THREADS=2
/opt/network/anaconda3/envs/pytorch/bin/python -u -m gru_beta_neutral_20261008.lightgbm_fusion_replay --methods baseline neutral bucket constraint
