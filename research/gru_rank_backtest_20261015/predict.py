"""OLD_RANK raw-four-head inference and the separately named score transform.

Native input X is the original assembled, PIT, clipped/z-scored 122-column panel.
It is not raw OHLCV. This package cannot reconstruct those 122 fields from bars.
The 64 artificial factors are re-ranked before endpoint eligibility is applied.
"""
from __future__ import annotations

import argparse
import io
import json
import pickletools
import zipfile

import numpy as np
import torch

try:
    from .model import MarketGRU, N_PARAMETERS
    from .verify import SEEDS, require, sha256, verify_weights_manifest
except ImportError:  # Direct `python predict.py` from an unpacked standalone package.
    from model import MarketGRU, N_PARAMETERS
    from verify import SEEDS, require, sha256, verify_weights_manifest

FACTOR_INPUT_STATE = 'uniform_rank_of_frozen_clipped_z'
EXPOSURE_ORDER = ('volatility', 'turnover', 'log_cap', 'beta60')


def rank_factor_day(values, members):
    """Original average-tie member-only uniform rank, float32 [U,64]."""
    values = np.asarray(values); members = np.asarray(members)
    require(values.ndim == 2 and values.shape[1] == 64 and values.dtype.kind in 'fiu',
            'Expected 64 frozen clipped/z artificial factors')
    require(members.dtype == np.bool_ and members.shape == values.shape[:1], 'Bad membership')
    out = np.full(values.shape, np.nan, np.float32)
    scale = np.sqrt(12.0)
    for c in range(64):
        ids = np.flatnonzero(members & np.isfinite(values[:, c])); n = ids.size
        if n < 2:
            continue
        order = np.argsort(values[ids, c], kind='stable'); v = values[ids, c][order]
        starts = np.flatnonzero(np.r_[True, v[1:] != v[:-1]])
        ends = np.r_[starts[1:], n]
        average_ranks = (starts + ends + 1) / 2.0
        scores = scale * ((average_ranks - 0.5) / n - 0.5)
        out[ids[order], c] = np.repeat(scores, ends - starts).astype(np.float32)
    return out


def make_windows(X, industry, slots, members, hour_complete, endpoint, daily_ranked):
    """One endpoint's original complete80+current-member forward cohort.

    X[H,U,122], industry[H,U] (the real 39-class context, not the old all-zero
    dataset industry), slots[H], members/complete[H,U], ranks[H/4,U,64].
    `daily_ranked` must have been computed using each day's full historical
    membership, independently of this endpoint/window/labels/other factors.
    Returns independent copies (x, industry, slots, stock_indices).
    """
    X = np.asarray(X); industry = np.asarray(industry); slots = np.asarray(slots)
    members = np.asarray(members); hour_complete = np.asarray(hour_complete)
    require(X.ndim == 3 and X.shape[2] == 122 and X.dtype == np.float32, 'Bad native X')
    h, u, _ = X.shape
    require(industry.shape == (h, u) and industry.dtype.kind in 'iu' and
            np.all((industry >= 0) & (industry < 39)), 'Bad PIT industry context')
    require(slots.shape == (h,) and slots.dtype.kind in 'iu' and
            np.array_equal(slots, np.arange(h) % 4), 'Expected consecutive original four slots')
    require(members.shape == hour_complete.shape == (h, u) and
            members.dtype == hour_complete.dtype == np.bool_, 'Bad forward membership/complete flags')
    require(isinstance(endpoint, (int, np.integer)) and 79 <= endpoint < h, 'Insufficient history/endpoint')
    require(daily_ranked.shape == ((h + 3) // 4, u, 64) and daily_ranked.dtype == np.float32,
            'Bad daily uniform-rank cache')
    first = endpoint - 79
    mask = members[endpoint] & hour_complete[first:endpoint + 1].all(axis=0)
    ids = np.flatnonzero(mask)
    require(len(ids) >= 100, 'Original min100 forward cohort failed')
    x = np.array(X[first:endpoint + 1, ids].transpose(1, 0, 2), copy=True)
    history_days = np.arange(first, endpoint + 1) // 4
    replacement = np.asarray(daily_ranked[history_days[:, None], ids[None, :], :]).transpose(1, 0, 2)
    require(np.array_equal(np.isfinite(x[..., 40:104]), np.isfinite(replacement)),
            'Uniform rank changed original missing flags; do not silently fill factors')
    x[..., 40:104] = replacement
    return (x, np.array(industry[first:endpoint + 1, ids].T, dtype=np.int64, copy=True),
            np.array(slots[first:endpoint + 1], dtype=np.int64, copy=True), ids)


def safe_state(path, expected_sha256, original_checkpoint):
    """Original audited weights_only loader; never falls back to unsafe load."""
    with open(path, 'rb') as stream:
        payload = stream.read()
    import hashlib
    require(hashlib.sha256(payload).hexdigest() == expected_sha256, 'Model changed before loading')
    from numpy.core.multiarray import _reconstruct as reconstruct
    numpy_names = {'numpy.ndarray', 'numpy.dtype', 'numpy.core.multiarray._reconstruct',
                   'numpy._core.multiarray._reconstruct'}
    inspector = getattr(torch.serialization, 'get_unsafe_globals_in_checkpoint', None)
    if inspector:
        unsafe = set(inspector(io.BytesIO(payload)))
        require(unsafe <= numpy_names, 'Unapproved checkpoint classes: ' + str(sorted(unsafe - numpy_names)))
        allow = [np.ndarray, np.dtype, type(np.dtype('uint32')),
                 (reconstruct, 'numpy.core.multiarray._reconstruct'),
                 (reconstruct, 'numpy._core.multiarray._reconstruct')]
    else:
        with zipfile.ZipFile(io.BytesIO(payload)) as z:
            names = [n for n in z.namelist() if n.endswith('/data.pkl')]
            require(len(names) == 1 and z.getinfo(names[0]).file_size < 10000000, 'Bad checkpoint pickle entry')
            globals_ = set()
            for op, arg, _ in pickletools.genops(z.read(names[0])):
                require(op.name != 'STACK_GLOBAL', 'Upgrade PyTorch for static STACK_GLOBAL inspection')
                if op.name == 'GLOBAL':
                    globals_.add(arg.replace(' ', '.'))
        default = {'collections.OrderedDict', 'torch._utils._rebuild_tensor_v2', 'torch._utils._rebuild_parameter',
                   'torch.FloatStorage', 'torch.LongStorage', 'torch.IntStorage', 'torch.ByteStorage',
                   'torch.DoubleStorage', 'torch.HalfStorage', 'torch.BoolStorage', 'torch.BFloat16Storage',
                   'torch.Size', 'torch.device', '_codecs.encode', '__builtin__.set', 'builtins.set'}
        require(globals_ <= default | numpy_names, 'Unapproved static checkpoint classes')
        own_name = reconstruct.__module__ + '.' + reconstruct.__name__
        require(not ({'numpy.core.multiarray._reconstruct', 'numpy._core.multiarray._reconstruct'} & globals_) - {own_name},
                'Old PyTorch cannot register NumPy alias; use a newer safe loader')
        allow = [np.ndarray, np.dtype, type(np.dtype('uint32')), reconstruct]
    with torch.serialization.safe_globals(allow):
        ck = torch.load(io.BytesIO(payload), map_location='cpu', weights_only=True)
    if original_checkpoint:
        require(isinstance(ck, dict) and isinstance(ck.get('signature'), dict), 'Bad original checkpoint signature')
        require(ck['signature'].get('variant') == 'control' and ck['signature'].get('seed') in SEEDS,
                'Wrong original variant/seed')
        state = ck.get('model')
    else:
        state = ck
    require(isinstance(state, dict) and state and all(isinstance(v, torch.Tensor) and
            torch.isfinite(v).all() for v in state.values()), 'Invalid/nonfinite tensor state')
    return state


def load_models(manifest, manifest_sha256, device='cpu'):
    _, rows = verify_weights_manifest(manifest, manifest_sha256)
    models = []
    for seed in SEEDS:
        row = rows[seed]
        model = MarketGRU()
        require(sum(p.numel() for p in model.parameters()) == N_PARAMETERS, 'Wrong architecture size')
        model.load_state_dict(safe_state(row['resolved_path'], row['sha256'],
                                       row['format'] == 'original_checkpoint'), strict=True)
        models.append(model.to(device).eval())
    return models


def predict_windows(models, x, industry, slots, factor_input_state):
    """[5,N,4] RAW outputs from one already adapted cross-section; no z/EMA.

    The control model is stock-separable. Subsetting *after full-cohort factor
    ranking* is valid for this raw-forward API, not for the later score transform.
    """
    require(factor_input_state == FACTOR_INPUT_STATE, 'Declare/prepare the OLD_RANK factor transform')
    require(len(models) == 5, 'Exactly five models in seed66,77,88,99,111 order are required')
    require(x.dtype == np.float32 and x.ndim == 3 and x.shape[1:] == (80, 122) and len(x) > 0,
            'Expected float32 [stocks,80,122] native windows')
    require(industry.dtype == np.int64 and industry.shape == x.shape[:2] and
            np.all((industry >= 0) & (industry < 39)), 'Bad PIT39 industry windows')
    require(slots.dtype == np.int64 and slots.shape in ((80,), x.shape[:2]) and
            np.all((slots >= 0) & (slots < 4)), 'Bad slot windows')
    result = []
    with torch.inference_mode():
        for model in models:
            require(not model.training and model.variant == 'control', 'Use eval OLD_RANK control models')
            device = next(model.parameters()).device
            args = [torch.from_numpy(np.ascontiguousarray(v)).to(device) for v in (x, industry, slots)]
            market = torch.zeros((1, 6), dtype=torch.float32, device=device)  # Inactive in control.
            values = model(*args, market, [len(x)]).cpu().numpy()
            require(values.shape == (len(x), 4) and np.isfinite(values).all(), 'Nonfinite/raw-head shape failure')
            result.append(values)
    return np.stack(result)


def zscore(s):
    s=np.asarray(s,float);ok=np.isfinite(s);out=np.full_like(s,np.nan)
    if ok.sum() and s[ok].std()>1e-12:out[ok]=(s[ok]-s[ok].mean())/s[ok].std()
    return out


def average_percentile_rank(x):
    x = np.asarray(x)
    order = np.argsort(x, kind='stable'); v = x[order]; n = len(x)
    starts = np.flatnonzero(np.r_[True, v[1:] != v[:-1]]); ends = np.r_[starts[1:], n]
    out = np.empty(n, np.float64)
    out[order] = np.repeat((starts + ends + 1) / (2.0 * n), ends - starts)
    return out


def neutralize(score, exposure):
    out = np.full_like(score, np.nan, dtype=float)
    ok = np.isfinite(score) & np.isfinite(exposure).all(axis=1)
    if ok.sum() < 100:
        return out
    x = exposure[ok].copy()
    x[:, 0] = average_percentile_rank(x[:, 0])
    x[:, 1] = average_percentile_rank(x[:, 1])
    scale = x.std(axis=0)
    x = (x-x.mean(axis=0))/np.where(scale > 1e-12, scale, 1)
    x = np.c_[np.ones(len(x)), x]
    out[ok] = score[ok] - x @ np.linalg.lstsq(x, score[ok], rcond=None)[0]
    require(np.max(np.abs(x.T @ out[ok])) < 1e-7, 'Four-style OLS orthogonality failed')
    return out


def processed_head4(per_seed_raw, membership, exposures, previous_ema=None):
    """Frozen score recipe; returns (score[T,U], last PRE-final-z EMA[U]).

    Input raw shape [5,T,U,4], exposures[T,U,4] in EXPOSURE_ORDER. Missing
    forward stocks are NaN, not zero. A plain mean is used, never nanmean.
    Replay 2024 warmup before 2025 and retain EMA across year boundaries.
    Exposures must be separately audited asof the signal; no risk fitting here.
    An arbitrary mid-history start with previous_ema=None is NOT the old score.
    """
    raw = np.asarray(per_seed_raw); membership = np.asarray(membership); exposures = np.asarray(exposures)
    require(raw.ndim == 4 and raw.shape[0] == 5 and raw.shape[-1] == 4 and raw.dtype.kind == 'f', 'Wrong raw-head axes')
    t, u = raw.shape[1:3]
    require(membership.dtype == np.bool_ and membership.shape == (t, u) and exposures.shape == (t, u, 4) and
            exposures.dtype.kind == 'f', 'Wrong membership/asof four-style axes')
    require(not np.isinf(raw).any() and not np.isinf(exposures).any(), 'Inf is not a valid missing value')
    require(np.all(~np.isfinite(raw[:, :, :, 3][:, ~membership])), 'Raw head4 finite outside current members')
    finite = np.isfinite(raw[0, :, :, 3])
    require(all(np.array_equal(np.isfinite(raw[k, :, :, 3]), finite) for k in range(1, 5)), 'Seed eligibility differs')
    per_seed = [np.asarray([zscore(s) for s in raw[k, :, :, 3].astype(float)]) for k in range(5)]
    mean_raw = np.mean(per_seed, axis=0)
    last = None if previous_ema is None else np.asarray(previous_ema, dtype=np.float64).copy()
    require(last is None or last.shape == (u,), 'Wrong previous PRE-z EMA state')
    rows = []
    for row, member, exposure in zip(mean_raw, membership, exposures):
        row = neutralize(np.where(member, row, np.nan), exposure)
        if last is not None:
            row = np.where(np.isfinite(row) & np.isfinite(last), .2 * row + .8 * last, row)
        last = row.copy()
        rows.append(zscore(np.where(member, row, np.nan)))
    return np.asarray(rows, dtype=np.float64).reshape(t, u), last


def main():
    p = argparse.ArgumentParser(description='One cross-section RAW four-head forward; processed scores are a separate API.')
    p.add_argument('--manifest', required=True); p.add_argument('--manifest-sha256', required=True)
    p.add_argument('--windows', required=True, help='NPZ x[N,80,122], industry[N,80], slots[80] or [N,80]')
    p.add_argument('--factor-input-state', required=True, choices=[FACTOR_INPUT_STATE])
    p.add_argument('--device', default='cpu'); p.add_argument('--out', required=True)
    args = p.parse_args()
    with np.load(args.windows, allow_pickle=False) as q:
        require(set(q.files) == {'x', 'industry', 'slots'}, 'Expected only native inference-window fields')
        x, industry, slots = (q[k] for k in ('x', 'industry', 'slots'))
    models = load_models(args.manifest, args.manifest_sha256, args.device)
    scores = predict_windows(models, x, industry, slots, args.factor_input_state)
    with open(args.out, 'xb') as stream:
        np.savez_compressed(stream, per_seed_raw_four_heads=scores, seeds=np.asarray(SEEDS, np.int64))
    print(json.dumps({'status': 'raw_four_head_forward_only', 'shape': list(scores.shape),
                      'sha256': sha256(args.out), 'processed_neutral_ema_score': False}))


if __name__ == '__main__':
    main()
