#!/bin/bash
# Run on the cluster after the jobs finish, from /home/users/s1155284034/gru_hourly_20261001.
# Packs everything needed for local account replay (no checkpoints except best*.pt hashes in result.json):
#   result.json, export.json, epochs.jsonl, best15*.json, test_scores*.npz, validation_scores*.npz, finetune metrics/scores, logs.
set -euo pipefail
cd /home/users/s1155284034/gru_hourly_20261001
OUT=gru_aligned_results_$(date +%Y%m%d_%H%M).tar.gz
find outputs/gru_aligned_20261019 \( -name result.json -o -name export.json -o -name epochs.jsonl -o -name 'best15*.json' \
  -o -name 'test_scores*.npz' -o -name 'validation_scores*.npz' -o -name 'test_metrics*.json' -o -name metrics.json \
  -o -name daily15.csv -o -name eval_scores.npz -o -name '*.log' \) -not -path '*/smoke/*' > /tmp/aligned_files.txt
tar -czf "$OUT" -T /tmp/aligned_files.txt
sha256sum "$OUT"; ls -lh "$OUT"
echo "Download $OUT and put it into <project>/outputs/gru_aligned_20261019/downloads/"
