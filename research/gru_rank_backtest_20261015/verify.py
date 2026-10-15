"""SHA/format checks for portable OLD_RANK files. Never unpickles a model."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np

SEEDS = (66, 77, 88, 99, 111)
SOURCE_CHECKPOINT_SHA256 = {
    66: '336849919446fbb180b58c4dbf29f6a76b5aad653aa38864bcbec4090bf97cb8',
    77: '1e066034aac3060e059d0eaba5752e869f1bddd97502a8c2630858268b055354',
    88: '77252824c1115e2fc7945348f407edb3059f0e5817dd79dbe11daa73d301ffaf',
    99: 'f425b9e92feb8b7d242afaceaabe0470b67432a59e2b7ebc1a4f97864c56836c',
    111: '118ce2fd012a963d78fc152cfaa304d8c88f3cb5913dfe20812a77239c7ec3b3',
}
SIGNAL_FIELDS = {'scores', 'dates', 'symbols', 'signal_time_ns', 'membership', 'roles'}


def require(ok, message):
    if not ok:
        raise ValueError(message)


def sha256(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(8 * 1024**2), b''):
            h.update(block)
    return h.hexdigest()


def package_file(root, relative):
    """Public manifests have relative paths; reject escape/alias model files."""
    root = Path(root).resolve(strict=True)
    rel = Path(relative)
    require(not rel.is_absolute() and rel.parts and '..' not in rel.parts,
            'Expected a package-relative path')
    path = root / rel
    require(path.is_file() and not path.is_symlink(), 'Missing/alias package file')
    resolved = path.resolve(strict=True)
    require(resolved.is_relative_to(root) and resolved == path.absolute(), 'Path escaped/aliased package')
    return resolved


def verify_weights_manifest(path, expected_sha256):
    path = Path(path)
    require(sha256(path) == expected_sha256, 'Weight manifest SHA mismatch')
    value = json.loads(path.read_text(encoding='utf-8'))
    require(value.get('schema') == 'gru_old_rank_portable_weights_v1', 'Wrong weight schema')
    require(value.get('seeds') == list(SEEDS), 'Wrong five seeds/order')
    require(value.get('variant') == 'control' and value.get('input_features') == 122 and
            value.get('sequence_length') == 80 and value.get('n_industries') == 39 and
            value.get('n_parameters') == 410116 and value.get('heads') == 4,
            'This is not the frozen OLD_RANK architecture')
    require(value.get('factor_input_state') == 'uniform_rank_of_frozen_clipped_z', 'Wrong factor transform')
    require(set(value.get('models', {})) == set(map(str, SEEDS)), 'Expected all five model records')
    records = {}
    for seed in SEEDS:
        row = value['models'][str(seed)]
        require(row.get('source_checkpoint_sha256') == SOURCE_CHECKPOINT_SHA256[seed],
                'Model provenance is not the selected OLD_RANK best checkpoint')
        require(row.get('format') in ('original_checkpoint', 'state_dict'), 'Unsupported model format')
        model_path = package_file(path.parent, row['path'])
        require(model_path.stat().st_size == row['bytes'] and sha256(model_path) == row['sha256'],
                'Model byte identity mismatch')
        if row['format'] == 'original_checkpoint':
            require(row['sha256'] == SOURCE_CHECKPOINT_SHA256[seed], 'Original model SHA differs')
        records[seed] = dict(row, resolved_path=str(model_path))
    return value, records


def verify_signal_pack(path, expected_sha256):
    """Verify the already processed six-field signal artifact, with no labels."""
    require(sha256(path) == expected_sha256, 'Signal SHA mismatch')
    with np.load(path, allow_pickle=False) as q:
        require(set(q.files) == SIGNAL_FIELDS, 'Expected the six-field processed score schema')
        a = {k: q[k] for k in q.files}
    require(all(v.dtype.kind != 'O' for v in a.values()), 'Object array is forbidden')
    scores, dates, symbols, ts, members, roles = (a[k] for k in
            ('scores', 'dates', 'symbols', 'signal_time_ns', 'membership', 'roles'))
    require(scores.dtype == np.float64 and scores.ndim == 2, 'Wrong score dtype/shape')
    t, u = scores.shape
    require(dates.shape == roles.shape == ts.shape == (t,) and symbols.shape == (u,) and
            members.shape == (t, u), 'Signal axes differ')
    require(dates.dtype.kind == symbols.dtype.kind == roles.dtype.kind == 'U' and
            ts.dtype == np.int64 and members.dtype == np.bool_, 'Wrong axis dtype')
    require(len(set(symbols.tolist())) == u and np.all(np.diff(ts) > 0), 'Duplicate/unsorted axes')
    require(not np.isinf(scores).any() and np.all(~np.isfinite(scores[~members])),
            'Inf or nonmember finite score')
    local_ns = ts + 8 * 3600 * 10**9
    observed_dates = local_ns.astype('datetime64[ns]').astype('datetime64[D]').astype('U10')
    require(np.array_equal(dates, observed_dates) and
            np.all(local_ns % (86400 * 10**9) == 15 * 3600 * 10**9), 'Expected T15 Asia/Shanghai clocks')
    expected_roles = np.array(['warmup2024' if d[:4] == '2024' else
                               'primary2025' if d[:4] == '2025' else
                               'carry2026' if d[:4] == '2026' else 'invalid' for d in dates])
    require(np.array_equal(roles, expected_roles), 'Wrong warmup/primary/carry roles')
    return a


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('kind', choices=('weights', 'signals'))
    p.add_argument('--file', required=True)
    p.add_argument('--sha256', required=True)
    args = p.parse_args()
    if args.kind == 'weights':
        _, rows = verify_weights_manifest(args.file, args.sha256)
        output = {'status': 'weights_bytes_verified_no_model_objects_loaded', 'seeds': list(rows)}
    else:
        a = verify_signal_pack(args.file, args.sha256)
        output = {'status': 'processed_signal_axes_verified', 'rows': len(a['dates']),
                  'stocks': len(a['symbols']), 'finite': int(np.isfinite(a['scores']).sum())}
    print(json.dumps(output, ensure_ascii=False))


if __name__ == '__main__':
    main()
