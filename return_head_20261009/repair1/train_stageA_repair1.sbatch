#!/bin/bash
#SBATCH --partition=scrp
#SBATCH --account=ug4
#SBATCH --qos=c16g1
#SBATCH --cpus-per-task=4
#SBATCH --mem=24G
#SBATCH --gpus=rtx3090:1
#SBATCH --time=04:00:00
#SBATCH --job-name=gru-return-head-A-r1
#SBATCH --output=/home/users/s1155284034/gru_hourly_20261001/return_head_20261009/repair1/logs/%j.log
set -euo pipefail
cd /home/users/s1155284034/gru_hourly_20261001/return_head_20261009/repair1
export OMP_NUM_THREADS=4
export MKL_NUM_THREADS=4
export OPENBLAS_NUM_THREADS=4
/opt/network/anaconda3/envs/pytorch/bin/python -B -u remote_pipeline_repair1.py --original-root /home/users/s1155284034/gru_hourly_20261001 --output-root /home/users/s1155284034/gru_hourly_20261001/outputs/gru_return_head_20261009_stageA_repair1 --inventory ../calibration_inventory.json --visible-manifest /home/users/s1155284034/gru_hourly_20261001/outputs/gru_index_enhancement_20261008/visible_signals/manifest.json --original-visible-signals-dir /home/users/s1155284034/gru_hourly_20261001/outputs/gru_index_enhancement_20261008/visible_signals --adapter-source adapter.py --device cuda:0
