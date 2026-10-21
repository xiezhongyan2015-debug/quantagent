"""Checks for the aligned-objective code on the real panel (CPU is enough).

1. The torch projection in align_term equals production neutralize() (gru_beta_neutral_20261008.replay) on real 15:00 rows.
2. With uniform weights and an exact copy of the target, A = -1 (perfect alignment), and A is invariant to adding
   any exposure combination to the score (the gradient cannot use style directions).
3. Arm C0 loss is the frozen loss_fn value.
4. y4ema purge keeps only rows whose ten-day target matured before the split cut.
Run from the project root (or the cluster code root):
  GRU_DATA=<dataset> WF_TRAIN_START=2019-01-01 WF_TRAIN_END=2024-01-01 WF_VAL_START=2024-01-01 WF_VAL_END=2025-01-01 \
  WF_TEST_START=2025-01-01 WF_TEST_END=2026-01-01 ALIGN_ARM=G2b python -m gru_aligned_20261019.test_aligned
"""
import json, os
import numpy as np, pandas as pd
from gru_aligned_20261019 import aligned as A


def main():
    from gru_walkforward_20261010 import experiment_wf as wf
    w = wf.window_from_env(); arm = A.arm_name(); base, cfg = A.patch(w, arm); torch = base.torch
    data = base.Data(fit=False)
    va = data.split('validation'); va15 = va[data.p.slots[va] == 3]
    rng = np.random.default_rng(0); checks = {}
    try:
        from gru_beta_neutral_20261008.replay import neutralize
    except Exception:  # cluster code root may not ship that package; inline copy of the production function
        def neutralize(score, exposure):
            out = np.full_like(score, np.nan, dtype=float); ok = np.isfinite(score) & np.isfinite(exposure).all(axis=1)
            if ok.sum() < 100: return out
            x = exposure[ok].copy(); x[:, 0] = pd.Series(x[:, 0]).rank(pct=True).to_numpy(); x[:, 1] = pd.Series(x[:, 1]).rank(pct=True).to_numpy()
            sc = x.std(axis=0); x = (x - x.mean(axis=0)) / np.where(sc > 1e-12, sc, 1); x = np.c_[np.ones(len(x)), x]
            out[ok] = score[ok] - x @ np.linalg.lstsq(x, score[ok], rcond=None)[0]; return out
    worst = 0.0
    for t in rng.choice(va15, size=min(20, len(va15)), replace=False):
        mask = data.mask(int(t)); d = data.aux.design(t, mask); n = int(mask.sum())
        s = rng.normal(size=n)
        e = data.aux.E[data.aux.row[int(t)]][mask].astype(float)
        ref = neutralize(s, e)
        pos, X, Q = d
        u = s[pos] - X.astype(float) @ (Q.astype(float) @ s[pos])
        assert np.array_equal(np.flatnonzero(np.isfinite(ref)), pos)
        worst = max(worst, float(np.max(np.abs(u - ref[pos]))))
    assert worst < 1e-4, worst
    checks['projection_matches_production_max_abs_diff'] = worst

    t = int(va15[0]); mask = data.mask(t); d = data.aux.design(t, mask); pos, X, Q = d; n = int(mask.sum())
    s = torch.randn(n, dtype=torch.float32)
    u = s[torch.from_numpy(pos)] - torch.from_numpy(X) @ (torch.from_numpy(Q) @ s[torch.from_numpy(pos)])
    tgt = torch.full((n,), float('nan')); z = (u - u.mean()) / u.std(unbiased=False); tgt[torch.from_numpy(pos)] = z
    wts = data.aux.weights(t, mask, pos, 0.0)
    a = A.align_term(torch, s, tgt, d, wts); assert abs(float(a) + 1) < 1e-4, float(a)
    s2 = s.clone(); s2[torch.from_numpy(pos)] += torch.from_numpy(X) @ torch.tensor([3., -2., 1.5, .7, -4.])
    a2 = A.align_term(torch, s2, tgt, d, wts); assert abs(float(a2) - float(a)) < 1e-4
    sg = s.clone().requires_grad_(True); A.align_term(torch, sg, tgt, d, wts).backward()
    g = sg.grad[torch.from_numpy(pos)].double().numpy(); leak = float(np.max(np.abs(X.astype(float).T @ g)))
    assert leak < 1e-4, leak
    checks['perfect_alignment_A'] = float(a); checks['style_shift_invariance'] = float(a2 - a); checks['gradient_in_style_span_max'] = leak
    wb = data.aux.weights(t, mask, pos, 1.0); checks['bw_weights_sum'] = float(wb.sum()); checks['bw_weights_max'] = float(wb.max())

    batch, labels = data.batch([int(va[0]), int(va[1])], 'cpu', targets=True)
    model = base.MarketGRU(data.p.groups, data.n_industries, 'control').eval()
    with torch.no_grad():
        sc = model(*batch)
        checks['loss_fn_value_finite'] = float(base.loss_fn(sc, labels, batch[-1]))

    if cfg['target'] == 'y4ema':
        tr = data.split('train')
        dates = pd.to_datetime(data.p.times, utc=True).tz_convert('Asia/Shanghai').strftime('%Y-%m-%d').to_numpy()
        prior = np.unique(dates[dates < w['train_end']]); cutns = pd.Timestamp(prior[-2], tz='Asia/Shanghai').value
        last15 = [int(t) for t in tr if data.p.slots[t] == 3][-1]
        assert data.aux.y4ema_end[data.aux.row[last15]] < cutns
        checks['y4ema_last_train_row'] = str(pd.Timestamp(int(data.p.times[last15]), tz='UTC').tz_convert('Asia/Shanghai'))
        checks['y4ema_target_finite_share'] = float(np.isfinite(data.target15(last15, data.mask(last15))).mean())
    print(json.dumps({'status': 'passed', 'arm': arm, **checks}, indent=2))


if __name__ == '__main__':
    main()
