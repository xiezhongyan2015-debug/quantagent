"""Shared read-only helpers for the pre-GRU-training score diagnostics (Z1/Z4/Z5).

Only reads project files; every output is written beside this file.
neutralize / zscore are verbatim copies of the production functions
(gru_beta_neutral_20261008/replay.py::neutralize, pcf_overlay_20261012/replay.py::zscore),
copied instead of imported to avoid package import side effects; `fast_residual` is an
equivalent QR form that is checked against `neutralize` in self_check().
"""
from pathlib import Path
import json
import numpy as np
import pandas as pd
from scipy.special import ndtri
from scipy.stats import rankdata

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent
DATA = ROOT / 'gru_research/artifacts/dataset'
TZ = 'Asia/Shanghai'
SNAP = ROOT / 'outputs/cluster_results_20261012_16_sync_20261017/snapshot/HOME/gru_hourly_20261001/outputs/gru_dualplatform_20261011'
LOCAL_DUAL = ROOT / 'outputs/gru_dualplatform_20261011'
WALK = ROOT / 'outputs/gru_walkforward_20261010'


# ---------------- production functions (verbatim) ----------------
def rank(x):
    return pd.Series(x).rank(method='average', pct=True).to_numpy()


def neutralize(score, exposure):
    out = np.full_like(score, np.nan, dtype=float)
    ok = np.isfinite(score) & np.isfinite(exposure).all(axis=1)
    if ok.sum() < 100:
        return out
    x = exposure[ok].copy()
    x[:, 0] = rank(x[:, 0])
    x[:, 1] = rank(x[:, 1])
    scale = x.std(axis=0)
    x = (x-x.mean(axis=0))/np.where(scale > 1e-12, scale, 1)
    x = np.c_[np.ones(len(x)), x]
    out[ok] = score[ok] - x @ np.linalg.lstsq(x, score[ok], rcond=None)[0]
    assert np.max(np.abs(x.T @ out[ok])) < 1e-7
    return out


def zscore(s):
    s=np.asarray(s,float);ok=np.isfinite(s);out=np.full_like(s,np.nan)
    if ok.sum() and s[ok].std()>1e-12:out[ok]=(s[ok]-s[ok].mean())/s[ok].std()
    return out


# ---------------- fast equivalents ----------------
def design(exposure, ok):
    """Production design matrix on rows `ok` (rank vol/turn, standardise, intercept)."""
    x = exposure[ok].astype(float).copy()
    x[:, 0] = rank(x[:, 0])
    x[:, 1] = rank(x[:, 1])
    scale = x.std(axis=0)
    x = (x - x.mean(axis=0)) / np.where(scale > 1e-12, scale, 1)
    return np.c_[np.ones(len(x)), x]


def basis(exposure, ok):
    return np.linalg.qr(design(exposure, ok))[0]


def fast_residual(A, Q):
    """A: [..., n] values on ok rows -> residual after OLS on the design spanned by Q."""
    return A - (A @ Q) @ Q.T


def ranknormal(y):
    out = np.full_like(np.asarray(y, float), np.nan)
    ok = np.isfinite(y); n = ok.sum()
    if n:
        out[ok] = ndtri((rankdata(y[ok]) - .5) / n)
    return out


def row_rank(M):
    """Ranks along the last axis (continuous scores; ties have measure zero)."""
    return np.argsort(np.argsort(M, axis=-1), axis=-1).astype(float)


def row_corr(M, v):
    """Pearson correlation of every row of M with vector v."""
    M = M - M.mean(axis=-1, keepdims=True)
    v = v - v.mean()
    den = np.sqrt((M * M).sum(-1) * (v * v).sum())
    return (M @ v) / np.where(den > 0, den, np.nan)


def row_spearman(M, y_rank):
    return row_corr(row_rank(M), y_rank)


def row_cov_b(M, g, b):
    """b-weighted covariance of every (row-z-scored) row of M with g; b sums to 1."""
    mu_m = M @ b
    mu_g = g @ b
    return ((M - mu_m[:, None]) * (g - mu_g)[None, :]) @ b


# ---------------- data ----------------
class Context:
    def __init__(self):
        self.times = np.load(DATA / 'times.npy')
        self.clock = pd.to_datetime(self.times, utc=True).tz_convert(TZ)
        self.dates = self.clock.strftime('%Y-%m-%d').to_numpy()
        self.hour = self.clock.hour.to_numpy()
        self.symbols = json.loads((DATA / 'symbols.json').read_text())
        self.U = len(self.symbols)
        self.members = np.load(DATA / 'members.npy', mmap_mode='r')
        self.labels = np.load(DATA / 'labels.npy', mmap_mode='r')
        q = np.load(ROOT / 'outputs/gru_beta_neutral_20261008/risk_context.npz', allow_pickle=True)
        assert np.array_equal(q['times'], self.times)
        self.risk = {k: q[k] for k in ['volatility', 'turnover', 'log_cap', 'beta60']}
        self._weights = None

    def exposure(self, i):
        return np.stack([self.risk[k][i] for k in ['volatility', 'turnover', 'log_cap', 'beta60']], axis=1).astype(float)

    def y4(self, i):
        return np.where(self.members[i], self.labels[i, :, 3], np.nan).astype(float)

    def weights(self, date):
        """PIT CSI500 month-end weights: latest month-end strictly before the signal date (raw, not normalised)."""
        if self._weights is None:
            w = pd.read_csv(ROOT / 'outputs/csi500_index_weight_tushare_official_monthly.csv')
            w['d'] = pd.to_datetime(w.trade_date.astype(str), format='mixed')
            w = w.drop_duplicates(['d', 'con_code'])
            pos = {s: k for k, s in enumerate(self.symbols)}
            self._wdates = np.array(sorted(w.d.unique()))
            self._wmat = {}
            for d, g in w.groupby('d'):
                v = np.zeros(self.U)
                for c, x in zip(g.con_code, g.weight):
                    if c in pos:
                        v[pos[c]] = x
                self._wmat[pd.Timestamp(d)] = v
            self._weights = True
        d = pd.Timestamp(date)
        k = np.searchsorted(self._wdates, np.datetime64(d), side='left') - 1
        assert k >= 0
        return self._wmat[pd.Timestamp(self._wdates[k])], str(pd.Timestamp(self._wdates[k]).date())


def load_head4_15(path, ctx):
    """15:00 rows of head y4 from a test_scores.npz: (dataset indices, scores [D,U])."""
    a = np.load(path)
    ix = a['indices']
    assert np.array_equal(a['times'], ctx.times[ix])
    keep = ctx.hour[ix] == 15
    return ix[keep], a['scores'][keep, :, 3].astype(float)


def seed_z(scores):
    return np.asarray([zscore(r) for r in scores])


def newey_west_t(x, lag=None):
    x = np.asarray(x, float); x = x[np.isfinite(x)]; n = len(x)
    if n < 10:
        return np.nan, np.nan, n
    if lag is None:
        lag = int(np.floor(4 * (n / 100) ** (2 / 9)))
    e = x - x.mean()
    s = e @ e / n
    for k in range(1, lag + 1):
        s += 2 * (1 - k / (lag + 1)) * (e[k:] @ e[:-k]) / n
    se = np.sqrt(max(s, 0) / n)
    return x.mean() / se if se > 0 else np.nan, lag, n


def self_check(ctx, n_days=5):
    """fast_residual vs production neutralize on random real days."""
    rng = np.random.default_rng(0)
    worst = 0.
    for i in rng.choice(np.flatnonzero(ctx.hour == 15)[1500:], n_days, replace=False):
        s = rng.standard_normal(ctx.U)
        m = ctx.members[i]
        e = ctx.exposure(i)
        ref = neutralize(np.where(m, s, np.nan), e)
        ok = m & np.isfinite(e).all(1)
        Q = basis(e, ok)
        fast = np.full(ctx.U, np.nan); fast[ok] = fast_residual(s[ok], Q)
        assert np.array_equal(np.isfinite(ref), np.isfinite(fast))
        worst = max(worst, float(np.nanmax(np.abs(ref - fast))))
    assert worst < 1e-9, worst
    return worst
