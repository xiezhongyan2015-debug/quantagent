#!/bin/bash
#SBATCH --job-name=gru_hourly_20261001
#SBATCH --partition=scrp
#SBATCH --account=ug4
#SBATCH --qos=c16g1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=4
#SBATCH --mem=24G
#SBATCH --gpus=rtx3090:1
#SBATCH --time=4-23:00:00
#SBATCH --output=/home/users/s1155284034/gru_hourly_20261001/outputs/slurm-%j.log
set -euo pipefail
export OMP_NUM_THREADS=4
export MKL_NUM_THREADS=4
export OPENBLAS_NUM_THREADS=4
export CUBLAS_WORKSPACE_CONFIG=:4096:8
export PYTHONUNBUFFERED=1
cd /home/users/s1155284034/gru_hourly_20261001
task_python=/opt/network/anaconda3/envs/pytorch/bin/python
task_dataset=/data/users/s1155284034/gru_hourly_20261001/dataset
task_output=/home/users/s1155284034/gru_hourly_20261001/outputs/formal
mkdir -p "$task_output"
trap 'task_exit=$?; printf "JOB_EXIT=%s\\n" "$task_exit" > "$task_output/job_exit_${SLURM_JOB_ID}.txt"' EXIT
"$task_python" -m gru_research.verify_remote --dataset "$task_dataset" --output "$task_output"
"$task_python" -m gru_research.gpu_preflight --dataset "$task_dataset" --output "$task_output"
"$task_python" -m gru_research.train --dataset "$task_dataset" --output "$task_output" --stage all
