#!/bin/bash
#SBATCH --partition=scrp
#SBATCH --account=ug4
#SBATCH --qos=c16g1
#SBATCH --cpus-per-task=4
#SBATCH --mem=16G
#SBATCH --time=02:00:00
#SBATCH --job-name=gru-market-report
#SBATCH --output=outputs/gru-market-report-%j.log
set -euo pipefail
cd /home/users/s1155284034/gru_hourly_20261001
export OMP_NUM_THREADS=4 MKL_NUM_THREADS=4
PY=/opt/network/anaconda3/envs/pytorch/bin/python
$PY -u -m gru_market_attention_20261007.check_replay
$PY -u -m gru_market_attention_20261007.report
