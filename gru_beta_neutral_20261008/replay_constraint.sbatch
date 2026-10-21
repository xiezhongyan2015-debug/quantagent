#!/bin/bash
#SBATCH --partition=scrp
#SBATCH --account=ug4
#SBATCH --qos=c16g1
#SBATCH --cpus-per-task=2
#SBATCH --mem=12G
#SBATCH --time=02:00:00
#SBATCH --job-name=gru-beta-lp
#SBATCH --output=outputs/gru-beta-lp-%j.log
set -euo pipefail
cd /home/users/s1155284034/gru_hourly_20261001
export OMP_NUM_THREADS=2 MKL_NUM_THREADS=2 OPENBLAS_NUM_THREADS=2
export GRU_DATA=/data/users/s1155284034/gru_hourly_20261001/dataset
/opt/network/anaconda3/envs/pytorch/bin/python -u -m gru_beta_neutral_20261008.replay --methods constraint
