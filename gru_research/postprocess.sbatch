#!/bin/bash
#SBATCH --job-name=gru_evidence_20261001
#SBATCH --partition=scrp
#SBATCH --account=ug4
#SBATCH --qos=c16g1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=1
#SBATCH --mem=4G
#SBATCH --time=01:00:00
#SBATCH --output=/home/users/s1155284034/gru_hourly_20261001/outputs/evidence-%j.log
set -euo pipefail
export OMP_NUM_THREADS=1
export MKL_NUM_THREADS=1
export OPENBLAS_NUM_THREADS=1
export PYTHONUNBUFFERED=1
cd /home/users/s1155284034/gru_hourly_20261001
exec /opt/network/anaconda3/envs/pytorch/bin/python -m gru_research.postprocess \
  --run /home/users/s1155284034/gru_hourly_20261001/outputs/formal \
  --dataset /data/users/s1155284034/gru_hourly_20261001/dataset \
  --receipts /home/users/s1155284034/gru_hourly_20261001/validation_receipts \
  --calendar /home/users/s1155284034/gru_hourly_20261001/validation_receipts/official_market_calendar_2026.json \
  --destination /home/users/s1155284034 \
  --training-job 405900
