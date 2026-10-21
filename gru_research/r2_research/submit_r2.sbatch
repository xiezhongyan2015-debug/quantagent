#!/bin/bash
#SBATCH --job-name=gru_r2_lr_chrono_v1
#SBATCH --partition=scrp
#SBATCH --account=ug4
#SBATCH --qos=c16g1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=4
#SBATCH --mem=24G
#SBATCH --gpus=rtx3090:1
#SBATCH --time=4-23:00:00
#SBATCH --output=/home/users/s1155284034/gru_hourly_20261001/outputs/r2-slurm-%j.log
set -euo pipefail
export OMP_NUM_THREADS=4
export MKL_NUM_THREADS=4
export OPENBLAS_NUM_THREADS=4
export CUBLAS_WORKSPACE_CONFIG=:4096:8
export PYTHONUNBUFFERED=1
cd /home/users/s1155284034/gru_hourly_20261001
task_r2_python=/opt/network/anaconda3/envs/pytorch/bin/python
task_r2_output=/home/users/s1155284034/gru_hourly_20261001/outputs/r2_lr_chrono_v1_20261002
mkdir -p "$task_r2_output"
trap 'task_r2_exit=$?; printf "JOB_EXIT=%s\n" "$task_r2_exit" > "$task_r2_output/job_exit_${SLURM_JOB_ID}.txt"' EXIT
"$task_r2_python" -B -m gru_research.r2_research.runner \
  --execute --stage all --device cuda \
  --root /home/users/s1155284034/gru_hourly_20261001 \
  --dataset /data/users/s1155284034/gru_hourly_20261001/dataset \
  --output "$task_r2_output" \
  --protocol /home/users/s1155284034/gru_hourly_20261001/gru_research/r2_research/protocol_v1.json \
  --source-manifest /home/users/s1155284034/gru_hourly_20261001/validation_receipts/r2_source_manifest_v1.json \
  --expected-source-manifest-sha256 8a7c855374b7237044639b1b0610b25a0843f73b6393ec3555270dfa1d9eb809 \
  --causal-evidence-directory /home/users/s1155284034/gru_hourly_20261001/validation_receipts/r2_causal_v1 \
  --expected-causal-receipt-sha256 95370519b5974c6724cef641506eeb113b4d33fc76fdb575182f7783fe2ca3cc \
  --expected-causal-evidence-manifest-sha256 685c131667613b4a6d7fc2ff49713d889faadd3590ebaf986e624bd1b718dc9c
