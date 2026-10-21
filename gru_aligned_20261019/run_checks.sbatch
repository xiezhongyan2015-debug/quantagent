#!/bin/bash
#SBATCH --partition=scrp
#SBATCH --account=ug4
#SBATCH --qos=c16g1
#SBATCH --cpus-per-task=4
#SBATCH --mem=24G
#SBATCH --gpus=rtx3090:1
#SBATCH --time=01:00:00
#SBATCH --job-name=gru-aligned-check
#SBATCH --output=outputs/gru_aligned_20261019/logs/check-%j.log
# Pre-flight on the cluster: correctness checks + a 2-epoch / 20-batch GPU smoke run of every arm into a scratch root.
set -euo pipefail
cd /home/users/s1155284034/gru_hourly_20261001
mkdir -p outputs/gru_aligned_20261019/logs
export GRU_DATA=${GRU_DATA:-/data/users/s1155284034/gru_hourly_20261001/dataset}
export ALIGN_AUX=/home/users/s1155284034/gru_hourly_20261001/gru_aligned_20261019/aux/aligned_aux_20261019.npz
export WF_TRAIN_START=2019-01-01 WF_TRAIN_END=2024-01-01 WF_VAL_START=2024-01-01 WF_VAL_END=2025-01-01 WF_TEST_START=2025-01-01 WF_TEST_END=2026-01-01
PY=/opt/network/anaconda3/envs/pytorch/bin/python
sha256sum gru_aligned_20261019/aux/aligned_aux_20261019.npz
ALIGN_ARM=G2b "$PY" -u -m gru_aligned_20261019.test_aligned
export GRU_RUN_ROOT=outputs/gru_aligned_20261019/smoke ALIGN_MAX_EPOCHS=2 ALIGN_MAX_BATCHES=20 ALIGN_MAX_VAL=24
rm -rf outputs/gru_aligned_20261019/smoke
for arm in C0 G1a G1b G1c G2b G4; do
  ALIGN_ARM=$arm "$PY" -u -m gru_aligned_20261019.aligned train --seed 11
  ALIGN_ARM=$arm "$PY" -u -m gru_aligned_20261019.aligned export --seed 11
done
echo "ALL CHECKS PASSED"
