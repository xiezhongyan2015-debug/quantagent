"""G6: warm-start fine-tuning of the live-recipe walk-forward GRU, replayed at historical cut points.

At each cut date c the base model is the walk-forward control of window T = year(c) (the model that was
live-equivalent at c; for T2026 these are exactly the live ctrl5 checkpoints). Fine-tuning data: all four slots of
the last FT_DAYS trading days whose four labels matured before the 2-trading-day embargo cut preceding c
(same maturity rule as the frozen split). Evaluation: the next EVAL_DAYS trading days from c, never used for any choice.
Variants (fixed in advance, no early stopping, original loss):
  A   no update
  B1  all parameters, lr 3e-5 (1/10 of the original), 1 epoch      B3  same, 3 epochs
  C1  only the output head and the second GRU layer, lr 3e-5, 1 epoch   C3  same, 3 epochs
Outputs per (seed, cut, variant): eval_scores.npz (scores [n,U,4], indices, times) and metrics.json
(15:00 raw y4 RankIC and production-neutralised RankIC over the first 34 and all EVAL_DAYS days).
Env: GRU_DATA, FT_BASE_ROOT (dir with T<year>/control_seed<S>/best.pt+result.json; default: packaged base_models),
FT_OUT (output dir), ALIGN_AUX. CLI: python -m gru_aligned_20261019.finetune --seed S [--cuts ...]
"""
from pathlib import Path
import argparse, importlib, json, os, time
import numpy as np, pandas as pd
from scipy.stats import rankdata
from gru_aligned_20261019 import aligned as AL

P = Path(__file__).resolve().parent
CUTS = ['2024-04-01', '2024-07-01', '2024-10-08', '2025-01-02', '2025-04-01', '2025-07-01', '2025-10-09',
        '2026-01-05', '2026-04-01', '2026-06-02']
VARIANTS = {'A': None, 'B1': ('all', 1), 'B3': ('all', 3), 'C1': ('top', 1), 'C3': ('top', 3)}
LR, WD, FT_DAYS, EVAL_DAYS, EMBARGO = 3e-5, 1e-4, 60, 60, 2


def trainable(name, scope):
    if scope == 'all':
        return True
    return name.startswith('head.') or (name.startswith('backbone.gru.') and name.endswith('_l1'))


def main(seed, cuts):
    base = importlib.import_module('gru_market_attention_20261007.experiment'); torch = base.torch
    torch.set_num_threads(4); device = 'cuda' if torch.cuda.is_available() else 'cpu'
    assert device == 'cuda' or os.environ.get('ALIGN_ALLOW_CPU') == '1'
    data = base.Data(fit=False); aux = AL.Aux(data.p, AL.aux_path())
    root = Path(os.environ.get('FT_BASE_ROOT', P / 'base_models')); out = Path(os.environ.get('FT_OUT', 'outputs/gru_aligned_20261019/finetune'))
    if not out.is_absolute():
        out = Path.cwd() / out
    dates = pd.to_datetime(data.p.times, utc=True).tz_convert('Asia/Shanghai').strftime('%Y-%m-%d').to_numpy()
    days = np.unique(dates)
    max_rows = int(os.environ.get('ALIGN_MAX_VAL', 0))
    for cut in cuts:
        T = int(cut[:4]); src = root / f'T{T}' / f'control_seed{seed}'
        meta = json.loads((src / 'result.json').read_text()); assert AL.sha(src / 'best.pt') == meta['best_checkpoint_sha256']
        ci = int(np.searchsorted(days, cut)); assert days[ci] >= cut
        embargo_day = days[ci - EMBARGO]; cutns = pd.Timestamp(embargo_day, tz='Asia/Shanghai').value
        cand = np.flatnonzero((dates >= days[max(0, ci - EMBARGO - FT_DAYS - 25)]) & (dates < embargo_day))
        mature = np.all((data.p.label_end_times[cand] > data.p.earliest_execution_times[cand, None]) & (data.p.label_end_times[cand] < cutns), axis=1)
        cand = cand[mature]; ft_days = np.unique(dates[cand])[-FT_DAYS:]
        ft = np.array([t for t in cand if dates[t] in set(ft_days) and data.mask(int(t)).sum() >= 100], dtype=np.int64)
        ev_days = days[ci:ci + EVAL_DAYS]
        ev = np.array([t for t in np.flatnonzero(np.isin(dates, ev_days)) if data.mask(int(t)).sum() >= 100], dtype=np.int64)
        if max_rows:
            ft, ev = ft[:max_rows], ev[:max_rows]
        assert data.p.times[ft].max() < data.p.times[ev].min()
        for name, spec in VARIANTS.items():
            run = out / f'cut{cut}' / f'{name}_seed{seed}'
            if (run / 'metrics.json').exists():
                continue
            run.mkdir(parents=True, exist_ok=True); base.seed_everything(seed + 7)
            model = base.MarketGRU(data.p.groups, data.n_industries, 'control').to(device)
            model.load_state_dict(base.checkpoint_load(src / 'best.pt', device)['model'])
            started = time.monotonic(); losses = []
            if spec is not None:
                scope, epochs = spec
                for n_, p_ in model.named_parameters():
                    p_.requires_grad_(trainable(n_, scope))
                params = [p_ for p_ in model.parameters() if p_.requires_grad]
                opt = torch.optim.AdamW(params, lr=LR, weight_decay=WD)
                for epoch in range(epochs):
                    model.train()
                    for ts in data.p.batches(ft, 8, np.random.default_rng(seed + 1000 * epoch + 17)):
                        opt.zero_grad(set_to_none=True)
                        for j in range(0, len(ts), 2):
                            sub = ts[j:j + 2]; batch, labels = data.batch(sub, device, targets=True)
                            loss = base.loss_fn(model(*batch), labels, batch[-1]); assert torch.isfinite(loss)
                            (loss * len(sub) / len(ts)).backward(); losses.append(float(loss.detach()))
                        torch.nn.utils.clip_grad_norm_(params, 1.); opt.step()
            summary, records = base.evaluate(model, data, ev, device, run / 'eval_scores.npz')
            sc = np.load(run / 'eval_scores.npz')['scores']
            rows = []
            for k, t in enumerate(ev):
                if data.p.slots[t] != 3:
                    continue
                mask = data.mask(int(t)); y = np.asarray(data.p.labels[t, mask, 3], dtype=np.float64); s = sc[k][mask, 3].astype(np.float64)
                d = aux.design(t, mask); ok = np.isfinite(y)
                raw = float(np.corrcoef(rankdata(s[ok]), rankdata(y[ok]))[0, 1]) if ok.sum() >= 100 else np.nan
                neu = np.nan
                if d is not None:
                    pos, X, Q = d; u = s[pos] - X.astype(float) @ (Q.astype(float) @ s[pos]); yy = y[pos]; o = np.isfinite(yy)
                    if o.sum() >= 100: neu = float(np.corrcoef(rankdata(u[o]), rankdata(yy[o]))[0, 1])
                rows.append({'date': dates[t], 'raw_rank_ic': raw, 'neutral_rank_ic': neu})
            df = pd.DataFrame(rows); df.to_csv(run / 'daily15.csv', index=False)
            first34 = df[df.date.isin(ev_days[:34])]
            AL_metrics = {'cut': cut, 'variant': name, 'seed': seed, 'base_window': f'T{T}', 'base_checkpoint_sha256': meta['best_checkpoint_sha256'],
                          'ft_rows': int(len(ft)), 'ft_first_day': str(ft_days[0]) if len(ft_days) else None, 'ft_last_day': str(ft_days[-1]) if len(ft_days) else None,
                          'eval_first_day': str(ev_days[0]), 'eval_last_day': str(ev_days[-1]), 'eval_days': int(len(ev_days)),
                          'mean_train_loss': float(np.mean(losses)) if losses else None, 'seconds': time.monotonic() - started,
                          'm34': first34[['raw_rank_ic', 'neutral_rank_ic']].mean().to_dict(), 'm_all': df[['raw_rank_ic', 'neutral_rank_ic']].mean().to_dict(),
                          'all_slots': summary['overall']}
            base.write(run / 'metrics.json', AL_metrics)
            print(json.dumps({k: AL_metrics[k] for k in ('cut', 'variant', 'seed', 'm34', 'm_all')}), flush=True)
    print('FINETUNE REPLAY COMPLETE', seed, flush=True)


if __name__ == '__main__':
    ap = argparse.ArgumentParser(); ap.add_argument('--seed', type=int, required=True); ap.add_argument('--cuts', nargs='*', default=CUTS)
    a = ap.parse_args(); main(a.seed, a.cuts)
