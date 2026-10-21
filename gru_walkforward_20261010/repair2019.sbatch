#!/bin/bash
#SBATCH --partition=scrp
#SBATCH --account=ug4
#SBATCH --qos=c16g1
#SBATCH --cpus-per-task=4
#SBATCH --mem=24G
#SBATCH --gpus=rtx3090:1
#SBATCH --time=06:00:00
#SBATCH --job-name=gru-repair2019
#SBATCH --array=0-4
#SBATCH --output=outputs/gru-repair2019-%A_%a.log
# Original fixed window (train 2019-2023, validation 2024, test 2025-01-01..2026-09-24), control variant,
# five seeds, but GRU_DATA points at the repaired-2019 panel built by make_repair_panel.sh.
# Output: outputs/gru_repair2019_20261010/control_seed${S}/   (2024 validation RankIC is the paired comparison target)
set -euo pipefail
cd /home/users/s1155284034/gru_hourly_20261001
export OMP_NUM_THREADS=4 MKL_NUM_THREADS=4
PY=/opt/network/anaconda3/envs/pytorch/bin/python

SEEDS=(11 22 33 44 55)
ID=${SLURM_ARRAY_TASK_ID:?run with sbatch --array}
if (( ID < 0 || ID > 4 )); then echo "array index $ID out of range 0-4" >&2; exit 1; fi
S=${SEEDS[$ID]}

export GRU_DATA=/home/users/s1155284034/gru_hourly_20261001/outputs/gru_repair2019_panel
export GRU_RUN_ROOT=outputs/gru_repair2019_20261010
export WF_TRAIN_START=2019-01-01 WF_TRAIN_END=2024-01-01
export WF_VAL_START=2024-01-01   WF_VAL_END=2025-01-01
export WF_TEST_START=2025-01-01  WF_TEST_END=2026-09-24

# Refuse to run against anything but a panel whose hour_complete.npy is the repaired regular file.
[[ -f "$GRU_DATA/meta.json" && -f "$GRU_DATA/X.npy" ]] || { echo "repair panel missing: run gru_walkforward_20261010/make_repair_panel.sh first" >&2; exit 1; }
[[ -f "$GRU_DATA/hour_complete.npy" && ! -L "$GRU_DATA/hour_complete.npy" ]] || { echo "$GRU_DATA/hour_complete.npy must be the repaired mask (regular file, not a symlink)" >&2; exit 1; }
[[ -f "$GRU_DATA/repair_panel_manifest.json" ]] || { echo "repair_panel_manifest.json missing; rebuild with make_repair_panel.sh" >&2; exit 1; }
mkdir -p "$GRU_RUN_ROOT"

echo "task=$ID seed=$S data=$GRU_DATA root=$GRU_RUN_ROOT"
"$PY" -u -m gru_walkforward_20261010.experiment_wf describe
"$PY" -u -m gru_walkforward_20261010.experiment_wf train  --variant control --seed "$S"
"$PY" -u -m gru_walkforward_20261010.experiment_wf export --variant control --seed "$S"
