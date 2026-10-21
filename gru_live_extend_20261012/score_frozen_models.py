"""Score the frozen hourly panel (all four slots) with three five-seed model sets, for ensemble research and EMA warm-up:
  wf2025  : our walk-forward control seeds 11..55, window T2025 (trained 2019-2023, validated 2024)
  wf2026  : our walk-forward control seeds 11..55, window T2026 (trained 2019-2024, validated 2025)  [live set]
  oldrank : teammate's OLD_RANK seeds 66/77/88/99/111 (T2025-equivalent window; factor columns 40:104 as uniform ranks)
Rows scored: every hour from --start (default 2025-01-01) to the end of the frozen panel (2026-09-23).
Outputs: outputs/gru_live_extend_20261012/scores_frozen/<set>.npz  (scores[T,5,U,4] float32, times, seeds, counts)
Usage: python gru_live_extend_20261012/score_frozen_models.py [--sets wf2025,wf2026,oldrank] [--start 2025-01-01] [--pkg <teammate package dir>]
"""
import json, sys, os, argparse, time
from pathlib import Path
import numpy as np, pandas as pd, torch
ROOT = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(ROOT))
os.environ.setdefault('GRU_DATA', str(ROOT / 'gru_research/artifacts/dataset'))
from gru_market_attention_20261007.experiment import MarketGRU
ART = ROOT / 'gru_research/artifacts'; DATASET = ART / 'dataset'; CTX = ROOT / 'gru_market_attention_20261007'
OUT = ROOT / 'outputs/gru_live_extend_20261012/scores_frozen'; OUT.mkdir(parents=True, exist_ok=True)
TZ = 'Asia/Shanghai'
DEFAULT_PKG = Path('/private/tmp/claude-501/-Users-leslie-Downloads---500--60min/3881df40-0168-4cda-bd66-a5478b75c001/scratchpad/teammate_gru/research/gru_rank_backtest_20261015')
OUR_SEEDS = [11, 22, 33, 44, 55]; HIS_SEEDS = [66, 77, 88, 99, 111]; FACTOR_COLS = slice(40, 104)


def uniform_rank(values, members):
    """Teammate's rank_factor_day: average-tie member-only uniform rank, sqrt(12)*((rank-.5)/n-.5), float32 [U,64]."""
    out = np.full(values.shape, np.nan, np.float32); scale = np.sqrt(12.0)
    for c in range(values.shape[1]):
        ids = np.flatnonzero(members & np.isfinite(values[:, c])); n = ids.size
        if n < 2: continue
        order = np.argsort(values[ids, c], kind='stable'); v = values[ids, c][order]
        starts = np.flatnonzero(np.r_[True, v[1:] != v[:-1]]); ends = np.r_[starts[1:], n]
        avg = (starts + ends + 1) / 2.0; out[ids[order], c] = np.repeat(scale * ((avg - 0.5) / n - 0.5), ends - starts).astype(np.float32)
    return out


def load_models(name, groups, n_ind, pkg):
    models = []
    if name in ('wf2025', 'wf2026'):
        for seed in OUR_SEEDS:
            st = torch.load(ROOT / f'outputs/gru_walkforward_20261010/T{name[2:]}/control_seed{seed}/best.pt', map_location='cpu', weights_only=False)
            m = MarketGRU(groups, n_ind, 'control'); m.load_state_dict(st['model']); m.eval(); models.append(m)
        return OUR_SEEDS, models
    for seed in HIS_SEEDS:
        f = pkg / f'checkpoints/rank_seed{seed}.pt'; f = f if f.exists() else pkg / f'rank_seed{seed}.pt'   # package layout or models/oldrank layout
        st = torch.load(f, map_location='cpu', weights_only=True)
        m = MarketGRU(groups, n_ind, 'control'); m.load_state_dict(st, strict=True); m.eval(); models.append(m)
    return HIS_SEEDS, models


def main():
    ap = argparse.ArgumentParser(); ap.add_argument('--sets', default='wf2025,wf2026,oldrank'); ap.add_argument('--start', default='2025-01-01'); ap.add_argument('--pkg', default=str(DEFAULT_PKG)); a = ap.parse_args()
    symbols = json.loads((DATASET / 'symbols.json').read_text(encoding='utf-8')); U = len(symbols)
    meta = json.loads((DATASET / 'meta.json').read_text(encoding='utf-8')); groups = {k: list(v) for k, v in meta['groups'].items()}
    ctx_meta = json.loads((CTX / 'context_manifest.json').read_text(encoding='utf-8')); n_ind = ctx_meta['n_industries']
    times = np.load(DATASET / 'times.npy'); X = np.load(DATASET / 'X.npy', mmap_mode='r'); complete = np.load(DATASET / 'hour_complete.npy', mmap_mode='r'); members = np.load(DATASET / 'members.npy', mmap_mode='r')
    ctx = np.load(CTX / 'context.npz'); cpos = {int(t): i for i, t in enumerate(ctx['times'])}; c0 = cpos[int(times[0])]   # lite tail: context.npz is the full file
    assert np.array_equal(ctx['times'][c0:c0 + len(times)], times); industry = ctx['industry'][c0:c0 + len(times)]; market = ctx['market'][c0:c0 + len(times)]
    clock = pd.to_datetime(times, utc=True).tz_convert(TZ); slots = np.array([{10: 0, 11: 1, 14: 2, 15: 3}[int(h)] for h in clock.hour], np.int64)
    bad = np.vstack([np.zeros((1, U), np.int32), np.cumsum(~np.asarray(complete), axis=0, dtype=np.int32)])
    def mask(t): return ((bad[t + 1] - bad[t + 1 - 80]) == 0) & np.asarray(members[t])
    sel = [t for t in range(len(times)) if t >= 79 and clock[t] >= pd.Timestamp(a.start, tz=TZ)]
    torch.set_num_threads(max(2, os.cpu_count() // 2))
    for name in a.sets.split(','):
        t0 = time.time(); seeds, models = load_models(name, groups, n_ind, Path(a.pkg)); scores = np.full((len(sel), 5, U, 4), np.nan, np.float32); counts = []
        # for oldrank the factor columns of every window row are replaced by their member-only uniform rank (per hour row)
        rank_cache = {}
        def row_x(t):
            if name != 'oldrank': return np.asarray(X[t])
            if t not in rank_cache:
                x = np.array(X[t]); x[:, FACTOR_COLS] = uniform_rank(np.asarray(X[t])[:, FACTOR_COLS].astype(np.float32), np.asarray(members[t])); rank_cache[t] = x
                if len(rank_cache) > 200: rank_cache.pop(next(iter(rank_cache)))
            return rank_cache[t]
        with torch.inference_mode():
            for j, t in enumerate(sel):
                mk = mask(t); n = int(mk.sum()); counts.append(n)
                if n < 100: continue
                win = np.stack([row_x(s)[mk] for s in range(t - 79, t + 1)], axis=1)       # [n,80,122]
                x = torch.from_numpy(np.ascontiguousarray(win)); ind = torch.from_numpy(np.ascontiguousarray(np.asarray(industry[t - 79:t + 1])[:, mk].T.astype(np.int64)))
                sl = torch.from_numpy(np.ascontiguousarray(np.broadcast_to(slots[t - 79:t + 1], (n, 80)))); mkt = torch.from_numpy(np.asarray(market[[t]]).copy())
                for k, m in enumerate(models): scores[j, k, mk] = m(x, ind, sl, mkt, [n]).numpy()
                if j % 200 == 0: print(name, j, len(sel), str(clock[t])[:16], round(time.time() - t0), 's', flush=True)
        np.savez_compressed(OUT / f'{name}.npz', scores=scores, times=times[np.array(sel)], seeds=np.array(seeds), counts=np.array(counts), symbols=np.array(symbols))
        print('DONE', name, scores.shape, round(time.time() - t0), 's', flush=True)


if __name__ == '__main__':
    main()
