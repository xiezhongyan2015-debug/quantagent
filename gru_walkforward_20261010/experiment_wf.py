"""Walk-forward and repaired-panel retraining of the frozen 2026-10-07 GRU protocol.

Thin wrapper over ``gru_market_attention_20261007.experiment``. The frozen module is
imported and monkey-patched (same technique as gru_beta_neutral_20261008/residual_train.py);
nothing in the original package is edited. Exactly these things change:

* split bounds come from six ISO-date environment variables
  WF_TRAIN_START WF_TRAIN_END WF_VAL_START WF_VAL_END WF_TEST_START WF_TEST_END
  (end bounds exclusive, as in the original ``split``);
* the fit-time assertion becomes "every training/validation row < WF_TEST_START"
  (the original hard-codes 2025-01-01);
* the output root comes from GRU_RUN_ROOT (default outputs/<this package>), so results
  never collide with outputs/gru_market_attention_20261007; run dir is <variant>_seed<seed>;
* the dataset dir still comes from GRU_DATA; the sha256 of its hour_complete.npy and the
  window enter the run signature, so a repaired panel or a different window can never be
  mistaken for an original run on resume/export;
* ``export`` writes a single test split: test_scores.npz (keys scores/indices/times, same
  layout as the original *_scores.npz), test_metrics.json, test_sections.csv, export.json,
  plus wf.json describing the window.

Model, loss, Gaussian-rank labels, batching, two-trading-day embargo, label-maturity rule,
checkpoint selection (validation slot-14 y4 RankIC), epochs/patience/learning rate and the
seed list are the original code paths, untouched.

Only ``Data.split`` is re-stated here (six lines) because the original builds its bounds
dict inline; everything else is called on the original module.
"""
from pathlib import Path
import argparse, hashlib, json, os
import numpy as np, pandas as pd

P = Path(__file__).resolve().parent
ROOT = P.parent
BASE_PACKAGE = 'gru_market_attention_20261007'
WINDOW_KEYS = ('WF_TRAIN_START', 'WF_TRAIN_END', 'WF_VAL_START', 'WF_VAL_END', 'WF_TEST_START', 'WF_TEST_END')
DEFAULT_DATA = '/data/users/s1155284034/gru_hourly_20261001/dataset'   # same default as the frozen module


def sha(p):
    with open(p, 'rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def window_from_env(env=None):
    env = os.environ if env is None else env
    missing = [k for k in WINDOW_KEYS if not env.get(k)]
    if missing:
        raise SystemExit('experiment_wf: missing window variables: ' + ' '.join(missing))
    w = {}
    for k in WINDOW_KEYS:
        v = env[k].strip()
        try:
            ok = pd.Timestamp(v).strftime('%Y-%m-%d') == v
        except (ValueError, TypeError):
            ok = False
        if not ok:
            raise SystemExit(f'experiment_wf: {k} must be an ISO date YYYY-MM-DD, got {v!r}')
        w[k[3:].lower()] = v
    order = (w['train_start'], w['train_end'], w['val_start'], w['val_end'], w['test_start'], w['test_end'])
    if not (order[0] < order[1] <= order[2] < order[3] <= order[4] < order[5]):
        raise SystemExit('experiment_wf: window must satisfy train_start < train_end <= val_start < val_end <= test_start < test_end, got ' + json.dumps(w))
    return w


def run_root():
    r = Path(os.environ.get('GRU_RUN_ROOT') or (ROOT / 'outputs' / P.name))
    return r if r.is_absolute() else ROOT / r


def data_dir():
    return Path(os.environ.get('GRU_DATA', DEFAULT_DATA))


def describe(window=None):
    """Resolved window/paths/hashes. Needs neither torch nor the GPU."""
    w = window_from_env() if window is None else window
    d = data_dir()
    hc = d / 'hour_complete.npy'
    base_src = ROOT / BASE_PACKAGE / 'experiment.py'
    return {'window': w, 'run_root': str(run_root()), 'data_dir': str(d),
            'hour_complete_sha256': sha(hc) if hc.exists() else None,
            'hour_complete_is_symlink': hc.is_symlink() if hc.exists() else None,
            'wrapper_source_sha256': sha(__file__),
            'base_experiment_sha256': sha(base_src) if base_src.exists() else None}


def patch(window):
    """Import the frozen module and install the window-aware Data class, CONFIG and OUT."""
    import importlib
    base = importlib.import_module(BASE_PACKAGE + '.experiment')
    config = dict(base.CONFIG)
    config.update(
        training=f"{window['train_start']}..{window['train_end']} (end exclusive; two-trading-day embargo and label maturity unchanged)",
        validation=f"{window['val_start']}..{window['val_end']}",
        checkpoint_selection=f"validation slot14 y4 RankIC on {window['val_start']}..{window['val_end']}",
        historical_test=f"{window['test_start']}..{window['test_end']}; never used for checkpoint selection",
        walkforward=dict(window),
        panel_dir=str(base.DATA),
        panel_hour_complete_sha256=sha(base.DATA / 'hour_complete.npy'),
        wrapper_source_sha256=sha(__file__))
    base.CONFIG = config          # signature() reads the module global at call time
    base.OUT = run_root()
    test_start_ns = pd.Timestamp(window['test_start'], tz='Asia/Shanghai').value
    Original = base.Data

    class WFData(Original):
        def __init__(self, fit=False):
            super().__init__(fit)
            self.wf_fit = fit
            self.fit = False  # disables the original "< 2025-01-01" check; replaced in batch() below

        def split(self, which):
            # Same logic as the frozen split(); only the bounds table differs.
            bounds = {'train': (window['train_start'], window['train_end']),
                      'validation': (window['val_start'], window['val_end']),
                      'test': (window['test_start'], window['test_end'])}
            start, end = bounds[which]
            dates = pd.to_datetime(self.p.times, utc=True).tz_convert('Asia/Shanghai').strftime('%Y-%m-%d').to_numpy()
            ix = np.flatnonzero((dates >= start) & (dates < end))
            if which in ('train', 'validation'):
                prior = np.unique(dates[dates < end]); cut = prior[-base.CONFIG['embargo_trading_days']]; cutns = pd.Timestamp(cut, tz='Asia/Shanghai').value
                ix = ix[(dates[ix] < cut) & np.all((self.p.label_end_times[ix] > self.p.earliest_execution_times[ix, None]) & (self.p.label_end_times[ix] < cutns), axis=1)]
            return np.array([t for t in ix if self.mask(int(t)).sum() >= 100], dtype=np.int64)

        def batch(self, ts, device, targets=False):
            if self.wf_fit:
                for t in ts:
                    assert self.p.times[int(t)] < test_start_ns, f'fit-time row {int(t)} is at or after WF_TEST_START'
            return super().batch(ts, device, targets)

    base.Data = WFData
    return base


def train(variant, seed):
    w = window_from_env(); base = patch(w)
    run = base.OUT / f'{variant}_seed{seed}'; run.mkdir(parents=True, exist_ok=True)
    base.write(run / 'wf.json', {**describe(w), 'variant': variant, 'seed': seed, 'stage': 'train'})
    print(json.dumps({'event': 'wf_window', **w, 'run': str(run), 'data_dir': str(base.DATA)}), flush=True)
    base.train(variant, seed)


def export(variant, seed):
    w = window_from_env(); base = patch(w); torch = base.torch
    torch.set_num_threads(4); assert torch.cuda.is_available(); data = base.Data(); run = base.OUT / f'{variant}_seed{seed}'
    r = json.loads((run / 'result.json').read_text()); assert r['signature'] == base.signature(data, variant, seed); assert base.sha(run / 'best.pt') == r['best_checkpoint_sha256']
    model = base.MarketGRU(data.p.groups, data.n_industries, variant).cuda(); model.load_state_dict(base.checkpoint_load(run / 'best.pt', 'cuda')['model'])
    ix = data.split('test'); assert len(ix), 'empty test split'
    summary, records = base.evaluate(model, data, ix, 'cuda', run / 'test_scores.npz')
    base.write(run / 'test_metrics.json', summary); pd.DataFrame(records).to_csv(run / 'test_sections.csv', index=False)
    tr = data.split('train'); va = data.split('validation'); va14 = va[data.p.slots[va] == 2]
    base.write(run / 'export.json', {'status': 'complete', 'checkpoint_sha256': base.sha(run / 'best.pt'), 'prediction_eligibility_uses_labels': False,
                                     'test_window': [w['test_start'], w['test_end']], 'test_sections': int(len(ix)),
                                     'historical_tests_already_exposed': True})
    base.write(run / 'wf.json', {**describe(w), 'variant': variant, 'seed': seed, 'stage': 'export',
                                 'sections': {'train': int(len(tr)), 'validation': int(len(va)), 'validation14': int(len(va14)), 'test': int(len(ix))},
                                 'best_epoch': r['best_epoch'], 'best_validation14_rank_ic': r['best_validation14_rank_ic'],
                                 'test_overall': summary['overall'], 'test_slot14': summary['slot'].get('2')})
    print('WF EXPORT COMPLETE', variant, seed, w['test_start'], w['test_end'], flush=True)


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('command', choices=['describe', 'train', 'export'])
    ap.add_argument('--variant', choices=['control', 'market_attention'], default='control')
    ap.add_argument('--seed', type=int)
    a = ap.parse_args()
    if a.command == 'describe':
        print(json.dumps(describe(), ensure_ascii=False, indent=2))
    else:
        if a.seed is None:
            raise SystemExit('--seed is required')
        (train if a.command == 'train' else export)(a.variant, a.seed)
