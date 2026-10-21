from __future__ import annotations
import concurrent.futures as cf
import multiprocessing as mp
import sys
from enumerate_universe_parallel import worker_init, query_batch

if __name__ == "__main__":
    workers = int(sys.argv[1]) if len(sys.argv) > 1 else 2
    n_batches = int(sys.argv[2]) if len(sys.argv) > 2 else 8
    batches = [[f"{i:06d}.SZ" for i in range(j*20+1, j*20+21)] for j in range(n_batches)]
    ctx = mp.get_context("spawn")
    with cf.ProcessPoolExecutor(max_workers=workers, mp_context=ctx, initializer=worker_init) as pool:
        for idx, res in enumerate(pool.map(query_batch, batches), 1):
            print(idx, len(res), res[0] if res else None, flush=True)
