#!/bin/bash
#SBATCH --partition=scrp
#SBATCH --account=ug4
#SBATCH --qos=c16g1
#SBATCH --cpus-per-task=4
#SBATCH --mem=24G
#SBATCH --gpus=rtx3090:1
#SBATCH --time=06:00:00
#SBATCH --job-name=gru-market
#SBATCH --output=outputs/gru-market-%j.log
set -euo pipefail
cd /home/users/s1155284034/gru_hourly_20261001
export OMP_NUM_THREADS=4 MKL_NUM_THREADS=4
PY=/opt/network/anaconda3/envs/pytorch/bin/python
$PY -u -m gru_market_attention_20261007.experiment train --variant "$1" --seed "$2"
$PY -u -m gru_market_attention_20261007.experiment export --variant "$1" --seed "$2"
