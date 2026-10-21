#!/bin/bash
#SBATCH --partition=scrp
#SBATCH --account=ug4
#SBATCH --qos=c16g1
#SBATCH --cpus-per-task=4
#SBATCH --mem=24G
#SBATCH --gpus=rtx3090:1
#SBATCH --time=06:00:00
#SBATCH --job-name=gru-finetune
#SBATCH --array=0-4
#SBATCH --output=outputs/gru_aligned_20261019/logs/finetune-%A_%a.log
# G6 warm-start fine-tuning replay: one task per seed (11 22 33 44 55), all 10 cut dates, variants A/B1/B3/C1/C3.
# Base models are the packaged walk-forward controls (T2026 = live ctrl5), sha-checked against result.json.
set -euo pipefail
cd /home/users/s1155284034/gru_hourly_20261001
mkdir -p outputs/gru_aligned_20261019/logs
export OMP_NUM_THREADS=4 MKL_NUM_THREADS=4
export GRU_DATA=${GRU_DATA:-/data/users/s1155284034/gru_hourly_20261001/dataset}
export ALIGN_AUX=/home/users/s1155284034/gru_hourly_20261001/gru_aligned_20261019/aux/aligned_aux_20261019.npz
export FT_BASE_ROOT=/home/users/s1155284034/gru_hourly_20261001/gru_aligned_20261019/base_models
export FT_OUT=outputs/gru_aligned_20261019/finetune
PY=/opt/network/anaconda3/envs/pytorch/bin/python
SEEDS=(11 22 33 44 55); S=${SEEDS[${SLURM_ARRAY_TASK_ID:?}]}
"$PY" -u -m gru_aligned_20261019.finetune --seed "$S"
