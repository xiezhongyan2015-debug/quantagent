#!/bin/bash
# Build outputs/gru_repair2019_panel/: a symlink view of the frozen dataset in which only
# hour_complete.npy is replaced by the repaired 2019 mask (copied, not linked; X.npy is never copied).
#
# usage: bash gru_walkforward_20261010/make_repair_panel.sh [SRC_DATASET] [REPAIRED_MASK] [DEST]
#   SRC_DATASET   default /data/users/s1155284034/gru_hourly_20261001/dataset
#   REPAIRED_MASK default outputs/gru_beta_neutral_20261008/hour_complete_with_combined_2019_repairs.npy
#                 (upload it from the workstation first; expected sha256 below)
#   DEST          default outputs/gru_repair2019_panel
# Run from the cluster project root (/home/users/s1155284034/gru_hourly_20261001). Idempotent.
set -euo pipefail
SRC=${1:-/data/users/s1155284034/gru_hourly_20261001/dataset}
MASK=${2:-outputs/gru_beta_neutral_20261008/hour_complete_with_combined_2019_repairs.npy}
DEST=${3:-outputs/gru_repair2019_panel}
PY=${PY:-/opt/network/anaconda3/envs/pytorch/bin/python}
# sha256 of hour_complete_with_combined_2019_repairs.npy as produced on the workstation (2026-10-08 repair layer).
EXPECTED_MASK_SHA=${EXPECTED_MASK_SHA:-adffee0970348ce91e9ac059a3a74f6124a411d751c2d0a300c920bc5ada5425}

SRC=$(cd "$SRC" && pwd -P)
[[ -f "$SRC/meta.json" && -f "$SRC/hour_complete.npy" && -f "$SRC/X.npy" ]] || { echo "not a dataset dir: $SRC" >&2; exit 1; }
[[ -f "$MASK" ]] || { echo "repaired mask not found: $MASK (scp it from the workstation)" >&2; exit 1; }

sha() { "$PY" - "$1" <<'PYS'
import hashlib,sys
with open(sys.argv[1],'rb') as f: print(hashlib.file_digest(f,'sha256').hexdigest())
PYS
}
MASK_SHA=$(sha "$MASK")
if [[ "$MASK_SHA" != "$EXPECTED_MASK_SHA" ]]; then
  echo "repaired mask sha256 $MASK_SHA != expected $EXPECTED_MASK_SHA (set EXPECTED_MASK_SHA=... to override)" >&2; exit 1
fi

mkdir -p "$DEST"
DEST=$(cd "$DEST" && pwd -P)
[[ "$DEST" != "$SRC" ]] || { echo "DEST must differ from SRC" >&2; exit 1; }

# Shape/dtype/monotonic-repair check against the original mask before anything is linked.
"$PY" - "$SRC/hour_complete.npy" "$MASK" <<'PYS'
import numpy as np,sys
a=np.load(sys.argv[1],mmap_mode='r'); b=np.load(sys.argv[2],mmap_mode='r')
assert a.shape==b.shape and a.dtype==np.bool_ and b.dtype==np.bool_, (a.shape,a.dtype,b.shape,b.dtype)
a=np.asarray(a); b=np.asarray(b)
assert not np.any(a & ~b), 'repaired mask turns completed hours off; refusing'
print('hour_complete shape',a.shape,'newly completed stock-hours',int((~a & b).sum()))
PYS

n=0
for f in "$SRC"/*; do
  name=$(basename "$f")
  [[ "$name" == "hour_complete.npy" ]] && continue
  ln -sfn "$f" "$DEST/$name"; n=$((n+1))
done
cp -f "$MASK" "$DEST/hour_complete.npy"
[[ "$(sha "$DEST/hour_complete.npy")" == "$MASK_SHA" ]] || { echo "copy verification failed" >&2; exit 1; }
ORIG_SHA=$(sha "$SRC/hour_complete.npy")

"$PY" - "$DEST" "$SRC" "$MASK" "$MASK_SHA" "$ORIG_SHA" "$n" <<'PYS'
import json,sys,datetime
dest,src,mask,msha,osha,n=sys.argv[1:]
json.dump({'dest':dest,'source_dataset':src,'symlinked_files':int(n),'replaced':'hour_complete.npy',
           'repaired_mask_source':mask,'repaired_hour_complete_sha256':msha,'original_hour_complete_sha256':osha,
           'note':'meta.json is the original symlink; its files_sha256[hour_complete.npy] still lists the original mask (PanelDataset does not verify file hashes; the dataset fingerprint hashes meta.json only, so context_manifest checks still pass).',
           'built_at':datetime.datetime.now().isoformat(timespec='seconds')},
          open(dest+'/repair_panel_manifest.json','w'),indent=2)
PYS
echo "repair panel ready: $DEST ($n symlinks + repaired hour_complete.npy)"
