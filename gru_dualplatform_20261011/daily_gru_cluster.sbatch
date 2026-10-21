#!/bin/bash
# Four seeds per year complement Aliyun's seed11; protocol is unchanged.
#SBATCH --partition=scrp
#SBATCH --account=ug4
#SBATCH --qos=c16g1
#SBATCH --cpus-per-task=4
#SBATCH --mem=24G
#SBATCH --gpus=rtx3090:1
#SBATCH --time=06:00:00
#SBATCH --job-name=gru-B2-daily
#SBATCH --array=0-23%1
#SBATCH --output=outputs/gru-B2-%A_%a.log
set -euo pipefail
cd /home/users/s1155284034/gru_hourly_20261001
export OMP_NUM_THREADS=4 MKL_NUM_THREADS=4
export DAILY_PANEL=/data/users/s1155284034/gru_daily_20261011/daily_panel
export DAILY_OUTPUT=/data/users/s1155284034/gru_daily_20261011/B
SEEDS=(22 33 44 55)
ID=${SLURM_ARRAY_TASK_ID:?}
T=$((2021 + ID / 4)); S=${SEEDS[$((ID % 4))]}
/opt/network/anaconda3/envs/pytorch/bin/python -u -m gru_dualplatform_20261011.daily train --year "$T" --seed "$S"
