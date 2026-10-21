"""Aligned-objective GRU experiments (2026-10-19): train what the portfolio actually uses.

Thin wrapper over gru_walkforward_20261010.experiment_wf (which wraps the frozen
gru_market_attention_20261007 protocol, i.e. the live ctrl5 recipe). Nothing in those packages is edited.
The live tilt is w ~ b(1 + k z) with z = zscore(EMA(M_t s)); its one-period active return is
proportional to Cov_b(z, r). The arms below add that quantity to the training loss:

  C0   original loss (bit-identical code path); extra checkpoints selected on 15:00 metrics (= G0)
  G1a  L = a*L_now + (1-a)*c*(1 + A),  A = -Cov_pi(zscore(M_t s4), y4_rank),  a=.5, pi uniform
  G1b  same, a=.5, pi ~ CSI500 PIT weight (gamma=1)
  G1c  same, a=.2, gamma=.5
  G2b  G1b with the 15:00 target replaced by the Gaussian rank of sum_j .8^j y4(d+j), j=0..9
  G4   original loss, SAM optimizer step (rho=.05) around AdamW
The align term only exists on 15:00 sections (the live signal); c = 1/(share of 15:00 sections in the
training split) keeps its expected weight at (1-a). M_t is the production projection
(gru_beta_neutral_20261008.replay.neutralize: ranks of vol/turn, standardised log-cap and beta60,
intercept, equal-weight OLS) on the same stock set (eligible window & finite exposures).

Every arm keeps the original early-stopping/scheduler metric (14:00 y4 RankIC on validation) so the arms
differ only by objective; three checkpoints are written:
  best.pt     original selection (identical to the old protocol for C0)
  best15n.pt  best validation 15:00 production-neutralised RankIC (vs raw y4)
  best15b.pt  best validation 15:00 b-weighted covariance of zscore(M s4) with Gaussian-rank y4

Env: WF_* window variables (see experiment_wf), GRU_DATA, GRU_RUN_ROOT, ALIGN_AUX (npz path),
ALIGN_ARM (C0|G1a|G1b|G1c|G2b|G4).
CLI: python -m gru_aligned_20261019.aligned describe|train|export --seed S
"""
from pathlib import Path
import argparse, hashlib, json, os, time
import numpy as np, pandas as pd
from scipy.stats import rankdata

P = Path(__file__).resolve().parent
ARMS = {
    'C0':  dict(alpha=1.0, gamma=0.0, target='y4', sam_rho=0.0),
    'G1a': dict(alpha=0.5, gamma=0.0, target='y4', sam_rho=0.0),
    'G1b': dict(alpha=0.5, gamma=1.0, target='y4', sam_rho=0.0),
    'G1c': dict(alpha=0.2, gamma=0.5, target='y4', sam_rho=0.0),
    'G2b': dict(alpha=0.5, gamma=1.0, target='y4ema', sam_rho=0.0),
    'G4':  dict(alpha=1.0, gamma=0.0, target='y4', sam_rho=0.05),
}
MIN_STOCKS = 100


def sha(p):
    with open(p, 'rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def arm_name(env=None):
    a = (os.environ if env is None else env).get('ALIGN_ARM', '')
    if a not in ARMS:
        raise SystemExit(f'ALIGN_ARM must be one of {sorted(ARMS)}, got {a!r}')
    return a


def aux_path():
    return Path(os.environ.get('ALIGN_AUX', P / 'aux' / 'aligned_aux_20261019.npz'))


def rank_pct(x):
    return rankdata(x, method='average') / len(x)


class Aux:
    """15:00 rows only. Exposure design and weights are cached per signal row."""
    def __init__(self, panel, path):
        q = np.load(path, allow_pickle=False)
        self.sha = sha(path)
        self.t15 = q['t15']
        assert np.array_equal(q['times15'], panel.times[self.t15]), 'aux times differ from the panel'
        assert list(q['symbols']) == list(panel.symbols), 'aux stock axis differs from the panel'
        self.row = {int(t): r for r, t in enumerate(self.t15)}
        self.E = np.stack([q['vol'], q['turn'], q['logcap'], q['beta60']], axis=-1)  # [R, U, 4]
        self.bw, self.y4ema, self.y4ema_end = q['bw'], q['y4ema'], q['y4ema_end_ns']
        self._design = {}

    def design(self, t, mask):
        """Production neutralize(): returns (positions within mask, X [n,5], Q=(X'X)^-1 X' [5,n]) or None."""
        t = int(t)
        if t in self._design:
            return self._design[t]
        e = self.E[self.row[t]][mask].astype(np.float64)
        ok = np.isfinite(e).all(axis=1)
        out = None
        if ok.sum() >= MIN_STOCKS:
            x = e[ok].copy()
            x[:, 0] = rank_pct(x[:, 0]); x[:, 1] = rank_pct(x[:, 1])
            sd = x.std(axis=0); x = (x - x.mean(axis=0)) / np.where(sd > 1e-12, sd, 1)
            x = np.c_[np.ones(len(x)), x]
            out = (np.flatnonzero(ok), x.astype(np.float32), np.linalg.pinv(x).astype(np.float32))
        self._design[t] = out
        return out

    def weights(self, t, mask, pos, gamma):
        n = len(pos)
        if gamma == 0:
            return np.full(n, 1.0 / n, dtype=np.float32)
        b = self.bw[self.row[int(t)]][mask][pos].astype(np.float64)
        good = np.isfinite(b) & (b > 0)
        floor = b[good].min() if good.any() else 1.0
        b = np.where(good, b, floor) ** gamma
        return (b / b.sum()).astype(np.float32)


def gaussian_rank(v):
    out = np.full(len(v), np.nan, dtype=np.float32); ok = np.isfinite(v); n = int(ok.sum())
    if n:
        from scipy.special import ndtri
        out[ok] = ndtri((rankdata(v[ok], method='average') - .5) / n)
    return out


def align_term(torch, s4, target, design, w):
    """A = -Cov_w(zscore(M s4), target) on the production stock set; None if too few labels."""
    pos, X, Q = design
    dev = s4.device
    pos_t = torch.from_numpy(pos).to(dev); X_t = torch.from_numpy(X).to(dev); Q_t = torch.from_numpy(Q).to(dev)
    s = s4[pos_t]
    u = s - X_t @ (Q_t @ s)
    z = (u - u.mean()) / u.square().mean().sub(u.mean().square()).clamp_min(1e-12).sqrt()
    y = target[pos_t]; ok = torch.isfinite(y)
    if int(ok.sum()) < MIN_STOCKS:
        return None
    ww = torch.from_numpy(w).to(dev)[ok]; ww = ww / ww.sum(); zz = z[ok]; yy = y[ok]
    zc = zz - (ww * zz).sum(); yc = yy - (ww * yy).sum()
    return -(ww * zc * yc).sum()


def patch(window, arm):
    from gru_walkforward_20261010 import experiment_wf as wf
    base = wf.patch(window)
    cfg = ARMS[arm]
    base.CONFIG = {**base.CONFIG, 'aligned_arm': arm, 'aligned_settings': cfg,
                   'aligned_source_sha256': sha(__file__), 'aligned_aux_path_name': aux_path().name,
                   'aligned_selection': 'early stop/scheduler unchanged (validation 14:00 y4 RankIC); extra best15n/best15b checkpoints'}
    Parent = base.Data

    class AlignedData(Parent):
        def __init__(self, fit=False):
            super().__init__(fit)
            self.aux = Aux(self.p, aux_path())
            self.ema_ranks = {}

        def split(self, which):
            ix = super().split(which)
            if cfg['target'] == 'y4ema' and which in ('train', 'validation'):
                # Purge 15:00 rows whose ten-day target matures after the split cut (same cut rule as the parent).
                dates = pd.to_datetime(self.p.times, utc=True).tz_convert('Asia/Shanghai').strftime('%Y-%m-%d').to_numpy()
                end = {'train': window['train_end'], 'validation': window['val_end']}[which]
                prior = np.unique(dates[dates < end]); cut = prior[-base.CONFIG['embargo_trading_days']]
                cutns = pd.Timestamp(cut, tz='Asia/Shanghai').value
                keep = [t for t in ix if self.p.slots[t] != 3 or self.aux.y4ema_end[self.aux.row[int(t)]] < cutns]
                ix = np.array(keep, dtype=np.int64)
            return ix

        def target15(self, t, mask):
            if cfg['target'] == 'y4':
                return self.ranks[int(t)][:, 3]
            if t not in self.ema_ranks:
                self.ema_ranks[t] = gaussian_rank(self.aux.y4ema[self.aux.row[int(t)]][mask].astype(np.float64))
            return self.ema_ranks[t]

    base.Data = AlignedData
    return base, cfg


def validation15(base, model, data, va15, device):
    """Production-neutralised 15:00 RankIC vs raw y4 and b-weighted covariance vs Gaussian-rank y4."""
    import torch
    model.eval(); rics, covs = [], []
    with torch.inference_mode():
        for st in range(0, len(va15), 2):
            ts = va15[st:st + 2]; batch, _ = data.batch(ts, device); scores = model(*batch)[:, 3]; off = 0
            for t, n in zip(ts, batch[-1]):
                s = scores[off:off + n]; off += n; mask = data.mask(int(t)); d = data.aux.design(t, mask)
                if d is None:
                    continue
                y = np.asarray(data.p.labels[t, mask, 3], dtype=np.float64)
                pos, X, Q = d; sv = s.double().cpu().numpy()[pos]; u = sv - X.astype(np.float64) @ (Q.astype(np.float64) @ sv)
                yy = y[pos]; ok = np.isfinite(yy)
                if ok.sum() >= MIN_STOCKS and u[ok].std() > 1e-12:
                    rics.append(float(np.corrcoef(rankdata(u[ok]), rankdata(yy[ok]))[0, 1]))
                g = torch.from_numpy(gaussian_rank(y)).to(s.device)
                a = align_term(torch, s.float(), g, d, data.aux.weights(t, mask, d[0], 1.0))
                if a is not None:
                    covs.append(-float(a))
    return {'n15_rank_ic': float(np.mean(rics)) if rics else float('nan'), 'b15_cov': float(np.mean(covs)) if covs else float('nan'),
            'sections': len(rics)}


def train(seed):
    from gru_walkforward_20261010 import experiment_wf as wf
    w = wf.window_from_env(); arm = arm_name(); base, cfg = patch(w, arm); torch = base.torch
    torch.set_num_threads(4); assert torch.cuda.is_available() or os.environ.get('ALIGN_ALLOW_CPU') == '1'
    device = 'cuda' if torch.cuda.is_available() else 'cpu'
    data = base.Data(fit=True); run = base.OUT / f'{arm}_seed{seed}'; run.mkdir(parents=True, exist_ok=True)
    sig = {**base.signature(data, 'control', seed), 'aligned_arm': arm, 'aux_sha256': data.aux.sha}
    if (run / 'result.json').exists():
        assert json.loads((run / 'result.json').read_text())['signature'] == sig; return
    base.seed_everything(seed); model = base.MarketGRU(data.p.groups, data.n_industries, 'control').to(device)
    C = base.CONFIG
    opt = torch.optim.AdamW(base.adamw_groups(model, C['weight_decay']), lr=C['learning_rate'])
    sched = torch.optim.lr_scheduler.ReduceLROnPlateau(opt, mode='max', factor=.5, patience=2, threshold=0., threshold_mode='abs', min_lr=1e-5)
    tr = data.split('train'); va = data.split('validation'); va14 = va[data.p.slots[va] == 2]; va15 = va[data.p.slots[va] == 3]
    frac15 = float(np.mean(data.p.slots[tr] == 3)); c15 = 1.0 / frac15
    if int(os.environ.get('ALIGN_MAX_VAL', 0)):  # smoke tests only; formal runs leave it unset
        cap = int(os.environ['ALIGN_MAX_VAL']); va14, va15 = va14[:cap], va15[:cap]
    max_epochs = int(os.environ.get('ALIGN_MAX_EPOCHS', C['epochs'])); max_batches = int(os.environ.get('ALIGN_MAX_BATCHES', 0))
    start = 0; best = -float('inf'); best_epoch = -1; bad = 0; best_n = -float('inf'); best_b = -float('inf')
    if (run / 'last.pt').exists():
        ck = base.checkpoint_load(run / 'last.pt', device); assert ck['signature'] == sig
        model.load_state_dict(ck['model']); opt.load_state_dict(ck['optimizer']); sched.load_state_dict(ck['scheduler']); base.restore_rng(ck['rng'])
        start = ck['epoch'] + 1; best = ck['best']; best_epoch = ck['best_epoch']; bad = ck['bad']; best_n = ck['best_n']; best_b = ck['best_b']
    base.write(run / 'config.json', sig)
    base.write(run / 'wf.json', {**wf.describe(w), 'arm': arm, 'seed': seed, 'stage': 'train', 'frac15': frac15})
    print(json.dumps({'event': 'start', 'arm': arm, 'seed': seed, 'window': w, 'train_sections': len(tr), 'validation14': len(va14),
                      'validation15': len(va15), 'frac15': frac15, 'device': device, 'resume_epoch': start}), flush=True)
    params = [p for p in model.parameters()]

    def batch_loss(sub, n_total):
        batch, labels = data.batch(sub, device, targets=True); score = model(*batch)
        lnow = base.loss_fn(score, labels, batch[-1])
        if cfg['alpha'] >= 1.0:
            return lnow
        extra = score.new_zeros(()); off = 0
        for t, n in zip(sub, batch[-1]):
            s4 = score[off:off + n, 3]; off += n
            if data.p.slots[int(t)] != 3:
                continue
            mask = data.mask(int(t)); d = data.aux.design(t, mask)
            if d is None:
                continue
            tgt = torch.from_numpy(np.asarray(data.target15(int(t), mask), dtype=np.float32)).to(device)
            a = align_term(torch, s4, tgt, d, data.aux.weights(t, mask, d[0], cfg['gamma']))
            if a is not None:
                extra = extra + (1.0 + a)
        return cfg['alpha'] * lnow + (1 - cfg['alpha']) * c15 * extra / len(sub)

    last_epoch = start - 1
    for epoch in range(start, max_epochs):
        if bad >= C['patience']:
            break
        started = time.monotonic(); model.train(); vals = []
        batches = list(data.p.batches(tr, 8, np.random.default_rng(seed + 10000 * epoch)))
        if max_batches:
            batches = batches[:max_batches]
        for k, ts in enumerate(batches):
            opt.zero_grad(set_to_none=True)
            for j in range(0, len(ts), 2):
                sub = ts[j:j + 2]; loss = batch_loss(sub, len(ts)); assert torch.isfinite(loss)
                (loss * len(sub) / len(ts)).backward(); vals.append(float(loss.detach()))
            if cfg['sam_rho'] > 0:
                grads = [p.grad for p in params if p.grad is not None]
                gnorm = torch.norm(torch.stack([g.norm(2) for g in grads]), 2).clamp_min(1e-12)
                eps = []
                with torch.no_grad():
                    for p in params:
                        e = None if p.grad is None else p.grad * (cfg['sam_rho'] / gnorm)
                        if e is not None:
                            p.add_(e)
                        eps.append(e)
                opt.zero_grad(set_to_none=True)
                for j in range(0, len(ts), 2):
                    sub = ts[j:j + 2]; loss = batch_loss(sub, len(ts)); assert torch.isfinite(loss)
                    (loss * len(sub) / len(ts)).backward()
                with torch.no_grad():
                    for p, e in zip(params, eps):
                        if e is not None:
                            p.sub_(e)
            norm = torch.nn.utils.clip_grad_norm_(model.parameters(), 1.); assert torch.isfinite(norm); opt.step()
            if k and k % 200 == 0:
                print(json.dumps({'event': 'batch', 'epoch': epoch + 1, 'batch': k, 'total': len(batches), 'seconds': round(time.monotonic() - started, 1)}), flush=True)
        summary, _ = base.evaluate(model, data, va14, device); metric = summary['overall']['rank_ic']; assert np.isfinite(metric)
        v15 = validation15(base, model, data, va15, device)
        improved = metric > best
        if improved:
            best = metric; best_epoch = epoch + 1; bad = 0
        else:
            bad += 1
        sched.step(metric)
        imp_n = v15['n15_rank_ic'] > best_n; imp_b = v15['b15_cov'] > best_b
        best_n = max(best_n, v15['n15_rank_ic']); best_b = max(best_b, v15['b15_cov'])
        state = {'signature': sig, 'model': model.state_dict(), 'optimizer': opt.state_dict(), 'scheduler': sched.state_dict(), 'rng': base.rng_state(),
                 'epoch': epoch, 'best': best, 'best_epoch': best_epoch, 'bad': bad, 'best_n': best_n, 'best_b': best_b}
        if improved:
            base.checkpoint_save(run / 'best.pt', state); base.write(run / 'best_validation14.json', summary)
        if imp_n:
            base.checkpoint_save(run / 'best15n.pt', state); base.write(run / 'best15n.json', {**v15, 'epoch': epoch + 1})
        if imp_b:
            base.checkpoint_save(run / 'best15b.pt', state); base.write(run / 'best15b.json', {**v15, 'epoch': epoch + 1})
        base.checkpoint_save(run / 'last.pt', state)
        event = {'event': 'epoch', 'arm': arm, 'epoch': epoch + 1, 'train_loss': float(np.mean(vals)), 'validation14_rank_ic': metric,
                 'validation15_n_rank_ic': v15['n15_rank_ic'], 'validation15_b_cov': v15['b15_cov'], 'best_rank_ic': best, 'best_epoch': best_epoch,
                 'bad_epochs': bad, 'lr': opt.param_groups[0]['lr'], 'seconds': time.monotonic() - started}
        with (run / 'epochs.jsonl').open('a') as f:
            f.write(json.dumps(event) + '\n')
        print(json.dumps(event), flush=True); last_epoch = epoch
    out = {'status': 'complete', 'signature': sig, 'arm': arm, 'best_epoch': best_epoch, 'epochs_completed': last_epoch + 1,
           'best_validation14_rank_ic': best, 'best15n': best_n, 'best15b': best_b, 'historical_test_used_for_selection': False,
           'checkpoints': {k: sha(run / f'{k}.pt') for k in ('best', 'best15n', 'best15b') if (run / f'{k}.pt').exists()}}
    base.write(run / 'result.json', out)
    print('ALIGNED TRAINING COMPLETE', arm, seed, flush=True)


def export(seed):
    from gru_walkforward_20261010 import experiment_wf as wf
    w = wf.window_from_env(); arm = arm_name(); base, cfg = patch(w, arm); torch = base.torch
    torch.set_num_threads(4); device = 'cuda' if torch.cuda.is_available() else 'cpu'
    assert device == 'cuda' or os.environ.get('ALIGN_ALLOW_CPU') == '1'
    data = base.Data(); run = base.OUT / f'{arm}_seed{seed}'; r = json.loads((run / 'result.json').read_text())
    sig = {**base.signature(data, 'control', seed), 'aligned_arm': arm, 'aux_sha256': data.aux.sha}; assert r['signature'] == sig
    model = base.MarketGRU(data.p.groups, data.n_industries, 'control').to(device)
    ix = data.split('test'); va = data.split('validation'); outputs = {}
    if int(os.environ.get('ALIGN_MAX_VAL', 0)):  # smoke tests only
        cap = int(os.environ['ALIGN_MAX_VAL']); ix, va = ix[:cap], va[:cap]
    for kind, digest in r['checkpoints'].items():
        assert sha(run / f'{kind}.pt') == digest
        model.load_state_dict(base.checkpoint_load(run / f'{kind}.pt', device)['model'])
        suffix = '' if kind == 'best' else '_' + kind
        summary, records = base.evaluate(model, data, ix, device, run / f'test_scores{suffix}.npz')
        base.write(run / f'test_metrics{suffix}.json', summary); pd.DataFrame(records).to_csv(run / f'test_sections{suffix}.csv', index=False)
        t15 = validation15(base, model, data, ix[data.p.slots[ix] == 3], device)
        # validation-year scores (EMA warm-up for replays) only for the primary checkpoint, to save disk
        vsum, _ = base.evaluate(model, data, va, device, run / 'validation_scores_best15n.npz' if kind == 'best15n' else None)
        outputs[kind] = {'checkpoint_sha256': digest, 'test': summary, 'test15': t15, 'validation': vsum}
    base.write(run / 'export.json', {'status': 'complete', 'arm': arm, 'outputs': outputs, 'test_window': [w['test_start'], w['test_end']],
                                    'historical_tests_already_exposed': True, 'prediction_eligibility_uses_labels': False})
    print('ALIGNED EXPORT COMPLETE', arm, seed, w['test_start'], flush=True)


def describe():
    from gru_walkforward_20261010 import experiment_wf as wf
    a = aux_path()
    return {**wf.describe(), 'arm': os.environ.get('ALIGN_ARM'), 'arm_settings': ARMS.get(os.environ.get('ALIGN_ARM', ''), None),
            'aux': str(a), 'aux_sha256': sha(a) if a.exists() else None, 'aligned_source_sha256': sha(__file__)}


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('command', choices=['describe', 'train', 'export'])
    ap.add_argument('--seed', type=int)
    a = ap.parse_args()
    if a.command == 'describe':
        print(json.dumps(describe(), ensure_ascii=False, indent=2))
    else:
        assert a.seed is not None, '--seed is required'
        (train if a.command == 'train' else export)(a.seed)
