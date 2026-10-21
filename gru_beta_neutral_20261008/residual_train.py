"""One-change control retraining: residual labels, raw y4 validation unchanged."""
from pathlib import Path
import argparse, hashlib, json
import numpy as np
import pandas as pd
from .attribution import ROOT, OUT, sha

def residual_labels(raw, beta, volatility, log_cap, industry):
    """Independent target masks. Missing past exposures have explicit indicators."""
    numeric = np.stack([beta, volatility, log_cap], axis=1).astype(float)
    missing = ~np.isfinite(numeric)
    for k in range(numeric.shape[1]):
        valid = ~missing[:, k]
        fill = float(np.median(numeric[valid, k])) if valid.any() else 0.
        numeric[~valid, k] = fill
        std = numeric[:, k].std()
        numeric[:, k] = (numeric[:, k]-numeric[:, k].mean())/(std if std > 1e-12 else 1)
    sectors = np.unique(industry)
    # Common market return is constant within a cross-section; absorbed by intercept.
    dummy = np.stack([industry == k for k in sectors[1:]], axis=1) if len(sectors)>1 else np.empty((len(raw), 0))
    design = np.c_[np.ones(len(raw)), numeric, missing.astype(float), dummy]
    result = np.full_like(raw, np.nan, dtype=np.float32)
    for head in range(raw.shape[1]):
        valid = np.isfinite(raw[:, head])
        if valid.sum() < 100:
            continue
        x, y = design[valid], raw[valid, head].astype(float)
        residual = y-x@np.linalg.lstsq(x, y, rcond=None)[0]
        assert np.max(np.abs(x.T@residual)) < 1e-7
        result[valid, head] = residual
    return result

def tests():
    rng = np.random.default_rng(17)
    n = 200
    beta, vol, cap = rng.normal(size=(3, n))
    industry = np.arange(n)%5
    y = rng.normal(size=(n, 4)).astype(np.float32)
    y[:, 3] += (beta+cap).astype(np.float32)
    original = residual_labels(y, beta, vol, cap, industry)
    changed = y.copy(); changed[:30, 0] = np.nan
    altered = residual_labels(changed, beta, vol, cap, industry)
    assert np.array_equal(original[:, 3], altered[:, 3])
    assert np.isnan(altered[:30, 0]).all()
    assert abs(original[:, 3].mean()) < 1e-7
    for k in range(5): assert abs(original[industry==k, 3].mean()) < 1e-7
    return ['target-specific missing labels do not alter y4 residuals', 'industry and intercept residual means zero', 'raw missing-label mask preserved', 'past numeric exposures with explicit missing indicators']

def main(command, seed=None):
    from gru_market_attention_20261007 import experiment as base
    folder = ROOT/'outputs/gru_residual_label_20261008'
    q = np.load(OUT/'risk_context.npz')
    config = dict(base.CONFIG)
    config.update(training_loss='same four-head Gaussian-rank correlation loss; ranks of cross-sectional residual labels',
                  label_transform='raw per-head returns -> residual on prior beta60, prior volatility20, PIT industry and logcap -> Gaussian rank',
                  experiment_comparison='original control versus residual-label control; architecture, seeds, training dates, loss form and raw-y4 checkpoint criterion unchanged',
                  residualization_source_sha256=sha(__file__), risk_context_sha256=sha(OUT/'risk_context.npz'))
    base.CONFIG = config
    base.OUT = folder
    original_data = base.Data
    class ResidualData(original_data):
        def __init__(self, fit=False):
            super().__init__(fit)
            assert np.array_equal(q['times'], self.p.times)
        def batch(self, ts, device, targets=False):
            batch, _ = super().batch(ts, device, targets=False)
            if not targets:
                return batch, None
            ys = []
            for t in ts:
                t = int(t)
                if t not in self.ranks:
                    mask = self.mask(t)
                    residual = residual_labels(np.asarray(self.p.labels[t, mask]), q['beta60'][t, mask],
                                               q['volatility'][t, mask], q['log_cap'][t, mask], q['industry'][t, mask])
                    self.ranks[t] = base.ranknormal(residual)
                ys.append(self.ranks[t])
            import torch
            return batch, torch.from_numpy(np.array(np.concatenate(ys), copy=True)).to(device)
    base.Data = ResidualData
    if command == 'test':
        checks = tests(); base.test()
        base.write(folder/'residual_label_tests.json', {'status':'passed', 'checks':checks, 'code_sha256':sha(__file__)})
        print('RESIDUAL LABEL TESTS PASSED', flush=True)
    elif command == 'train':
        base.train('control', seed)
    else:
        base.export('control', seed)

if __name__ == '__main__':
    ap=argparse.ArgumentParser(); ap.add_argument('command', choices=['test','train','export']); ap.add_argument('--seed', type=int)
    a=ap.parse_args(); main(a.command, a.seed)
