#!/bin/bash
#SBATCH --partition=scrp
#SBATCH --account=ug4
#SBATCH --qos=c16g1
#SBATCH --cpus-per-task=4
#SBATCH --mem=24G
#SBATCH --gpus=rtx3090:1
#SBATCH --time=06:00:00
#SBATCH --job-name=gru-residual
#SBATCH --output=outputs/gru-residual-%j.log
set -euo pipefail
cd /home/users/s1155284034/gru_hourly_20261001
export OMP_NUM_THREADS=4 MKL_NUM_THREADS=4
export GRU_DATA=/data/users/s1155284034/gru_hourly_20261001/dataset
PY=/opt/network/anaconda3/envs/pytorch/bin/python
"$PY" -u -m gru_beta_neutral_20261008.residual_train train --seed "$1"
"$PY" -u -m gru_beta_neutral_20261008.residual_train export --seed "$1"
