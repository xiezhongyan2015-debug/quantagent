"""Shared read-only loaders for the 2026-10-19 VWAP diagnosis.

Everything here only reads: the cluster snapshot, local price sources and the
project-owned risk context.  Derived panels are cached under ../cache.
"""
from __future__ import annotations

import glob
import hashlib
import importlib.util
import json
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path('/Users/leslie/Downloads/中证500量化60min')
OUT = ROOT / 'outputs/vwap_diagnosis_20261019'
CACHE = OUT / 'cache'
RESULTS = OUT / 'results'
SNAP = ROOT / 'outputs/cluster_results_20261012_16_sync_20261017/snapshot'
INFER = SNAP / 'HOME/gru_minute_vwap_20261014/inference_pilot3_v1'
PILOT = SNAP / 'DATA/gru_minute_vwap_20261014/accounts/pilot_v1'
IFIND = ROOT / '中证500_历年成分股_日频_2010-2026_iFinD_后复权'
PARQUET = ROOT / 'outputs/pcf_overlay_20261012/daily_prices.parquet'
RISK = ROOT / 'outputs/gru_beta_neutral_20261008/risk_context.npz'
NEUTRAL_PY = ROOT / 'gru_beta_neutral_20261008/replay.py'
ZSCORE_PY = ROOT / 'pcf_overlay_20261012/replay.py'
EXPECTED_SHA = {
    NEUTRAL_PY: '923f06634f84c7424a832418f941c93391308665c8754c2771a3a22314807185',
    ZSCORE_PY: '53f06b657dae08df5aed481a634cf0c944cb520e387657abe9a235c424596df4',
    RISK: '5b2065310e2cfdb8277f17db891cbb132a0c786c4f736f721e88f3d541d0ccc5',
}
ARMS = ('SHORT_NULL', 'SHORT_STATS', 'VWAP_NULL', 'VWAP_STATS')
SEEDS = (66, 77, 88)
HEADS = {'head3': 2, 'head4': 3}
EXPOSURES = ('volatility', 'turnover', 'log_cap', 'beta60')


def sha(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for b in iter(lambda: f.read(1 << 20), b''):
            h.update(b)
    return h.hexdigest()


def _top_level_function(path, name):
    """Extract one top-level function's source without executing the module."""
    import ast
    src = Path(path).read_text(encoding='utf-8')
    tree = ast.parse(src)
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.get_source_segment(src, node)
    raise KeyError(name)


def math_functions():
    for p, h in EXPECTED_SHA.items():
        assert sha(p) == h, f'hash differs: {p}'
    ns = {'np': np, 'pd': pd}
    for path, names in ((NEUTRAL_PY, ('rank', 'neutralize')), (ZSCORE_PY, ('zscore',))):
        for n in names:
            exec(_top_level_function(path, n), ns)
    return ns['zscore'], ns['neutralize']


def load_signal(arm, channel):
    z = np.load(INFER / f'{arm}_{channel}_ensemble_signals.npz', allow_pickle=False)
    return {k: z[k] for k in z.files}


def load_raw_seed(arm, seed):
    z = np.load(INFER / 'raw_predictions' / f'{arm}_seed{seed}.npz', allow_pickle=False)
    return {k: z[k] for k in z.files}


def risk_context():
    z = np.load(RISK, allow_pickle=True)
    return {k: z[k] for k in z.files}


def build_scores(alphas=(0.2,)):
    """Recompute raw (per-seed pop-z mean), neutralized and EMA-smoothed scores.

    Returns dict[(arm, head)] -> {'raw', 'neu', 'ema<a>'} arrays (662, 1178),
    plus the shared axes.  Matches inference.py processed_channels exactly for
    alpha=0.2 (checked against the sealed ensemble files by the caller).
    """
    zscore, neutralize = math_functions()
    risk = risk_context()
    anchor = load_signal('VWAP_NULL', 'head4')
    members = anchor['membership']
    out = {}
    ix = None
    for arm in ARMS:
        seeds = [load_raw_seed(arm, s) for s in SEEDS]
        if ix is None:
            ix = seeds[0]['indices']
            assert np.array_equal(risk['times'][ix], anchor['signal_time_ns'])
        for s in seeds:
            assert np.array_equal(s['indices'], ix)
        for head, k in HEADS.items():
            per_seed = [np.asarray([zscore(r) for r in s['scores'][:, :, k].astype(float)]) for s in seeds]
            raw = np.mean(per_seed, axis=0)
            neu = np.full_like(raw, np.nan)
            for d, t in enumerate(ix):
                expo = np.stack([risk[e][t] for e in EXPOSURES], axis=1)
                neu[d] = neutralize(np.where(members[d], raw[d], np.nan), expo)
            res = {'raw': np.where(members, raw, np.nan), 'neu': neu}
            for a in alphas:
                rows, last = [], None
                for d in range(len(ix)):
                    row = neu[d]
                    if last is not None:
                        row = np.where(np.isfinite(row) & np.isfinite(last), a * row + (1 - a) * last, row)
                    last = row.copy()
                    rows.append(zscore(np.where(members[d], row, np.nan)))
                res[f'ema{a:g}'] = np.asarray(rows)
            # alpha=1 (no smoothing) still gets the final member pop-z
            res['ema1'] = np.asarray([zscore(np.where(members[d], neu[d], np.nan)) for d in range(len(ix))])
            out[(arm, head)] = res
    axes = {'dates': anchor['dates'], 'symbols': anchor['symbols'], 'members': members,
            'signal_time_ns': anchor['signal_time_ns'], 'risk_index': ix}
    return out, axes


def scores_cached(alphas=(0.2, 0.1, 0.05)):
    path = CACHE / 'scores.npz'
    if path.exists():
        z = np.load(path, allow_pickle=False)
        keys = [k for k in z.files if '|' in k]
        out = {}
        for k in keys:
            arm, head, kind = k.split('|')
            out.setdefault((arm, head), {})[kind] = z[k]
        axes = {k: z[k] for k in ('dates', 'symbols', 'members', 'signal_time_ns', 'risk_index')}
        return out, axes
    out, axes = build_scores(alphas)
    flat = {f'{a}|{h}|{kind}': v for (a, h), d in out.items() for kind, v in d.items()}
    np.savez_compressed(path, **flat, **axes)
    return out, axes


def _ifind_frame():
    fs = sorted(glob.glob(str(IFIND / 'bars/2024/*/*.parquet')) + glob.glob(str(IFIND / 'bars/2025/*/*.parquet')) +
                glob.glob(str(IFIND / 'bars/2026/*/*.parquet')))
    d = pd.concat([pd.read_parquet(f, columns=['thscode', 'date', 'open', 'close', 'avgPrice', 'amount', 'volume']) for f in fs])
    d = d.rename(columns={'thscode': 'symbol', 'avgPrice': 'vwap'})
    return d.drop_duplicates(['symbol', 'date'])


def price_panels():
    """Daily panels on the full exchange calendar 2024-01-02..2026-09-24, 1178-symbol axis.

    Returns dict with 'calendar' (datetime64[D]) and, per source ('if' iFinD
    hfq members-only, 'pq' pcf_overlay parquet hfq), open/close/vwap panels;
    plus parquet amount/isST for the account fill rule.
    """
    path = CACHE / 'prices.npz'
    if path.exists():
        z = np.load(path, allow_pickle=False)
        return {k: z[k] for k in z.files}
    symbols = load_signal('VWAP_NULL', 'head4')['symbols']
    f = _ifind_frame()
    p = pd.read_parquet(PARQUET, columns=['date', 'symbol', 'open', 'close', 'volume', 'amount', 'isST', 'factor',
                                          'open_adjusted', 'close_adjusted', 'tradestatus'])
    p = p[p.date >= '2024-01-02']
    p['amount'] = pd.to_numeric(p['amount'], errors='coerce')
    p['vwap'] = np.where((p.volume > 0) & (p.amount > 0), p.amount / p.volume * p.factor, np.nan)
    cal = sorted(set(f.date) | set(p.date))
    cal = pd.Index(cal)
    sym = pd.Index(symbols)
    out = {'calendar': np.asarray(cal, dtype='datetime64[D]'), 'symbols': symbols}

    def panel(df, col):
        a = np.full((len(cal), len(sym)), np.nan)
        r = cal.get_indexer(df.date); c = sym.get_indexer(df.symbol); ok = (r >= 0) & (c >= 0)
        a[r[ok], c[ok]] = df[col].to_numpy(float)[ok]
        return a
    for col in ('open', 'close', 'vwap'):
        out['if_' + col] = panel(f, col)
    for src, col in (('open_adjusted', 'open'), ('close_adjusted', 'close'), ('vwap', 'vwap'),
                     ('amount', 'amount'), ('isST', 'st'), ('open', 'open_raw'), ('close', 'close_raw'),
                     ('tradestatus', 'tradestatus')):
        out['pq_' + col] = panel(p, src)
    for k in list(out):
        if k.startswith(('if_', 'pq_')) and k not in ('pq_amount', 'pq_st', 'pq_tradestatus'):
            v = out[k]; v[~(np.isfinite(v) & (v > 0))] = np.nan
    np.savez_compressed(path, **out)
    return out


def merged_price(px, field):
    """iFinD hfq where present, else parquet hfq rescaled by the per-stock overlap ratio.

    The ratio is the median iFinD/parquet ratio over the overlap; stocks whose
    ratio is not stable (rel. spread > 1e-4) keep iFinD only.
    """
    a, b = px['if_' + field], px['pq_' + field]
    ratio = a / b
    med = np.nanmedian(ratio, axis=0)
    spread = np.nanmax(np.abs(ratio / med - 1), axis=0)
    stable = np.isfinite(med) & (spread < 1e-4)
    filled = np.where(np.isfinite(a), a, np.where(stable[None, :], b * med[None, :], np.nan))
    return filled, {'stable_symbols': int(stable.sum()), 'unstable_symbols_with_overlap': int((np.isfinite(med) & ~stable).sum())}


def rank_ic(score, ret, weights=None):
    """Per-row Spearman correlation; optional weights give a weighted Pearson of ranks."""
    out = np.full(len(score), np.nan)
    for i in range(len(score)):
        ok = np.isfinite(score[i]) & np.isfinite(ret[i])
        if weights is not None:
            ok &= np.isfinite(weights[i]) & (weights[i] > 0)
        if ok.sum() < 30:
            continue
        x = pd.Series(score[i, ok]).rank().to_numpy(); y = pd.Series(ret[i, ok]).rank().to_numpy()
        if weights is None:
            out[i] = np.corrcoef(x, y)[0, 1]
        else:
            w = weights[i, ok] / weights[i, ok].sum()
            xm, ym = x - w @ x, y - w @ y
            out[i] = (w @ (xm * ym)) / np.sqrt((w @ xm ** 2) * (w @ ym ** 2))
    return out


def write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=True) + '\n', encoding='utf-8')
