"""Frozen control, with optional terminal-five-epoch averaging and train-slot filter.

The original training loop and validation selection are preserved. The averaging
hook runs only after optimizer/validation/RNG snapshots, and its rolling tail is
checkpointed so interrupted runs produce the same average after resumption.
"""
from collections import deque
from pathlib import Path
import argparse
import json
import os
import numpy as np
import pandas as pd
from gru_walkforward_20261010 import experiment_wf as wf


def settings():
    return {'swa_tail_epochs': int(os.environ.get('GRU_SWA_TAIL', '5')),
            'train_slots': [int(x) for x in os.environ.get('GRU_TRAIN_SLOTS', '0,1,2,3').split(',')],
            'extension_source_sha256': wf.sha(__file__)}


def patch(w):
    base = wf.patch(w)
    options = settings()
    assert 0 <= options['swa_tail_epochs'] <= 5
    assert options['train_slots'] and set(options['train_slots']) <= {0, 1, 2, 3}
    base.CONFIG.update(options)
    original = base.Data

    class SlotData(original):
        def split(self, which):
            ix = super().split(which)
            # Only training endpoints change; all 80 input hours remain intact.
            return ix[np.isin(self.p.slots[ix], options['train_slots'])] if which == 'train' else ix
    base.Data = SlotData
    return base


def average_states(states, torch):
    assert states
    return {k: torch.stack([s[k] for s in states]).mean(0) if v.is_floating_point()
            else states[-1][k].clone() for k, v in states[-1].items()}


def train(seed):
    w = wf.window_from_env()
    base = patch(w)
    run = base.OUT / f'control_seed{seed}'
    run.mkdir(parents=True, exist_ok=True)
    tail_n = settings()['swa_tail_epochs']
    tail = deque(maxlen=tail_n or 1)
    if tail_n and (run / 'last.pt').exists():
        tail.extend(base.checkpoint_load(run / 'last.pt', 'cpu').get('swa_tail', []))
    original_save = base.checkpoint_save

    def save(path, state):
        if tail_n and Path(path).name == 'last.pt':
            snapshot = {k: v.detach().cpu().clone() for k, v in state['model'].items()}
            if tail and tail[-1]['epoch'] == state['epoch']:
                tail.pop()
            tail.append({'epoch': state['epoch'], 'model': snapshot})
            state = {**state, 'swa_tail': list(tail)}
        original_save(path, state)

    base.checkpoint_save = save
    try:
        base.train('control', seed)
    finally:
        base.checkpoint_save = original_save
    if tail_n:
        last = base.checkpoint_load(run / 'last.pt', 'cpu')
        snapshots = last['swa_tail']
        assert len(snapshots) == min(tail_n, last['epoch'] + 1)
        original_save(run / 'swa.pt', {
            'signature': last['signature'],
            'model': average_states([x['model'] for x in snapshots], base.torch),
            'averaged_epochs': [x['epoch'] + 1 for x in snapshots],
            'selection': 'terminal epochs; no test/validation selection of SWA tail'})
    base.write(run / 'wf.json', {**wf.describe(w), **settings(), 'seed': seed, 'stage': 'train'})


def export(seed):
    w = wf.window_from_env()
    base = patch(w)
    base.torch.set_num_threads(4)
    assert base.torch.cuda.is_available()
    data = base.Data()
    run = base.OUT / f'control_seed{seed}'
    result = json.loads((run / 'result.json').read_text())
    assert result['signature'] == base.signature(data, 'control', seed)
    assert result['best_checkpoint_sha256'] == base.sha(run / 'best.pt')
    model = base.MarketGRU(data.p.groups, data.n_industries, 'control').cuda()
    ix = data.split('test')
    va = data.split('validation')
    assert len(ix) and len(va)
    outputs = {}
    for kind in ['best'] + (['swa'] if settings()['swa_tail_epochs'] else []):
        ck = base.checkpoint_load(run / f'{kind}.pt', 'cuda')
        assert ck['signature'] == result['signature']
        model.load_state_dict(ck['model'])
        suffix = '' if kind == 'best' else '_swa'
        summary, records = base.evaluate(model, data, ix, 'cuda', run / f'test_scores{suffix}.npz')
        base.write(run / f'test_metrics{suffix}.json', summary)
        pd.DataFrame(records).to_csv(run / f'test_sections{suffix}.csv', index=False)
        val, rows = base.evaluate(model, data, va, 'cuda')
        base.write(run / f'validation_export{suffix}.json', val)
        pd.DataFrame(rows).to_csv(run / f'validation_sections{suffix}.csv', index=False)
        outputs[kind] = {'checkpoint_sha256': base.sha(run / f'{kind}.pt'), 'test': summary, 'validation': val}
        if w['test_start'] == '2025-01-01':
            # A3 needs the T2025 models on 2026, with selection still frozen.
            years = pd.to_datetime(data.p.times, utc=True).tz_convert('Asia/Shanghai').year.to_numpy()
            extra = data.visible[years[data.visible] == 2026]
            extra = np.array([t for t in extra if data.mask(int(t)).sum() >= 100])
            extra_summary, _ = base.evaluate(model, data, extra, 'cuda', run / f'extra2026_scores{suffix}.npz')
            base.write(run / f'extra2026_metrics{suffix}.json', extra_summary)
    base.write(run / 'export.json', {'status': 'complete', 'outputs': outputs,
                                    'test_window': w, 'prediction_eligibility_uses_labels': False,
                                    'historical_tests_already_exposed': True})
    base.write(run / 'wf.json', {**wf.describe(w), **settings(), 'seed': seed, 'stage': 'export',
                               'sections': {'train': len(data.split('train')), 'validation': len(va), 'test': len(ix)},
                               'best_epoch': result['best_epoch']})
    print('DUAL PLATFORM HOURLY EXPORT COMPLETE', seed, w, flush=True)


def test():
    import torch
    a = {'w': torch.tensor([1., 2.]), 'counter': torch.tensor(2)}
    b = {'w': torch.tensor([3., 4.]), 'counter': torch.tensor(3)}
    mean = average_states([a, b], torch)
    assert torch.equal(mean['w'], torch.tensor([2., 3.]))
    assert mean['counter'].item() == 3
    tail = deque(maxlen=5)
    tail.extend(range(9))
    assert list(tail) == [4, 5, 6, 7, 8]
    slots = np.array([0, 1, 2, 3, 0, 1, 2, 3])
    assert np.array_equal(np.flatnonzero(np.isin(slots, [1, 2])), [1, 2, 5, 6])
    print('PASS: averaging, integer buffers, terminal tail and train-slot endpoint filter')


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('command', choices=['describe', 'train', 'export', 'test'])
    ap.add_argument('--seed', type=int)
    a = ap.parse_args()
    if a.command == 'describe':
        print(json.dumps({**wf.describe(), **settings()}, ensure_ascii=False, indent=2))
    elif a.command == 'test':
        test()
    else:
        assert a.seed is not None
        (train if a.command == 'train' else export)(a.seed)
