"""P2: daily LPPLS confidence history for the four indices (protocol S4). Parallel; resumable (skips dates already in the output)."""
import sys, os, warnings
from pathlib import Path
from multiprocessing import Pool
import numpy as np, pandas as pd
HERE = Path(__file__).resolve().parent; sys.path.insert(0, str(HERE))
import lppls
OUT = HERE.parent / 'outputs/timing_p2_20261017'


def work(args):
    code, d, closes = args
    warnings.filterwarnings('ignore')
    pos, neg, n, _ = lppls.confidence(closes)
    return code, d, pos, neg, n


if __name__ == '__main__':
    OUT.mkdir(parents=True, exist_ok=True)
    jobs = []; done = {}
    for code in (sys.argv[1].split(',') if len(sys.argv) > 1 else ['000905', '000300', '000852', '399006']):
        df = pd.read_csv(HERE / f'cache/index_{code}.csv', parse_dates=['date']).set_index('date').sort_index()
        f = OUT / f'lppls_hist_{code}.csv'
        have = set(pd.read_csv(f, parse_dates=['date']).date) if f.exists() else set()
        c = df.close.values
        last = len(df) - 1
        for k in range(760, len(df)):
            d = df.index[k]
            if (last - k) % 5 == 0 and d not in have: jobs.append((code, d, c[:k + 1]))  # every 5th trading day, aligned to the latest bar (Shu & Zhu step)
    print('jobs', len(jobs), flush=True)
    files = {}
    with Pool(9) as p:
        for i, (code, d, pos, neg, n) in enumerate(p.imap_unordered(work, jobs, chunksize=8)):
            f = OUT / f'lppls_hist_{code}.csv'
            new = not f.exists()
            with open(f, 'a') as fh:
                if new: fh.write('date,pos,neg,n\n')
                fh.write(f'{d.date()},{pos},{neg},{n}\n')
            if i % 500 == 0: print(i, code, d.date(), flush=True)
    print('done', flush=True)
