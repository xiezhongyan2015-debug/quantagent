"""Process-parallel factor computation and evaluation.

Each worker holds its own Panel (float32 field cache to halve memory) and computes one candidate at a
time: frame -> full evaluation -> sampled signature + per-date percentile ranks (float16, written to a
temp file). The parent process performs the cheap, order-dependent parts (redundancy vs pool, admission,
ledger writes) serially. Typical throughput on a 32-thread machine: 6 workers ~ 5x a single process.
"""
from __future__ import annotations
import os
import tempfile
import numpy as np
import pandas as pd
from concurrent.futures import ProcessPoolExecutor, as_completed

_CTX = {}
_CORE_FIELDS = frozenset(("open", "high", "low", "close", "volume", "amount", "vwap", "prev_close", "turnover", "ret", "log_cap",
                          "float_market_cap", "mkt_ret", "eligible", "eval_ok", "label_5", "label_1", "label_10", "label_20"))


def _init_worker(root: str, panel_dir: str, target_years, train_years, universe="all_a", label="label_5", min_stocks=100):
    os.environ["OMP_NUM_THREADS"] = "2"
    from .panel import Panel
    from .dsl import Compiler
    from .evaluate import Evaluator
    from .ledger import Store
    panel = Panel(panel_dir)
    panel._dtype = np.float32
    store = Store(root)
    compiler = Compiler(resolver=lambda name: panel[name])
    ev = Evaluator(panel, universe=universe, label=label, min_stocks=min_stocks, train_years=tuple(train_years), target_years=tuple(target_years))
    ev._store = store
    _CTX.update(panel=panel, store=store, compiler=compiler, ev=ev)


def _work(candidate: dict, tmp_dir: str, full: bool, direction: int | None) -> dict:
    from .cli import compute_candidate, pct_ranks
    panel, store, compiler, ev = _CTX["panel"], _CTX["store"], _CTX["compiler"], _CTX["ev"]
    out = {"id": candidate["id"], "name": candidate.get("name")}
    try:
        f, meta = compute_candidate(candidate, panel, compiler, ev)
        if full:
            res = ev.evaluate(f)
            res.update({k: v for k, v in meta.items() if k != "plugin"})
            d = res["direction"]
            out["result"] = res
        else:
            d = direction or 1
        if d:
            pct = pct_ranks(f * d, ev)
            p = os.path.join(tmp_dir, f"{candidate['id']}.npy")
            np.save(p, pct.astype(np.float16)); out["rank_path"] = p
            sig = ev.signature(f * d)
            sp = os.path.join(tmp_dir, f"{candidate['id']}.sig.npy")
            np.save(sp, sig.to_numpy(np.float16)); out["sig_path"] = sp
            try:
                from . import portfolio as pf
                ps = pf.portfolio_series(pct.astype(np.float32), panel["label_1"].to_numpy(np.float64), ev._mask_np, panel.dates, top_frac=0.1, hold=5)
                sm = pf.summarize(ps, tuple(ev.target_years), cost_rate=0.001)
                out["long_hold5"] = {k: sm[k] for k in ("gross_excess_ann", "gross_sharpe", "net_excess_ann", "net_sharpe", "daily_turnover", "years_net_positive")}
            except Exception as exc:  # noqa: BLE001
                out["long_hold5"] = {"error": str(exc)[:80]}
        del f
    except Exception as exc:  # noqa: BLE001
        out["error"] = f"{type(exc).__name__}: {exc}"[:300]
    # keep only the core daily fields cached (float32, ~50MB each); everything else (minute fields, labels of
    # other horizons, plugin intermediates) is re-read from parquet next time. With 30+ intraday fields a
    # worker that cached everything grew past 4GB of commit and exhausted the machine's commit limit.
    panel.release(keep=tuple(k for k in panel._cache if k in _CORE_FIELDS))
    return out


def run_parallel(candidates: list[dict], root: str, panel_dir: str, target_years, train_years, workers=6, full=True,
                 directions: dict | None = None, log=print, universe="all_a", label="label_5", min_stocks=100):
    """Yields worker results as they complete. Temp rank/signature files live in tmp_dir; the caller moves what it
    keeps and the directory is removed when the generator finishes (also on interruption)."""
    import shutil
    tmp_dir = tempfile.mkdtemp(prefix="fla_par_", dir=os.path.join(root, "batches"))
    try:
        with ProcessPoolExecutor(max_workers=workers, initializer=_init_worker,
                                 initargs=(root, panel_dir, tuple(target_years), tuple(train_years), universe, label, min_stocks)) as ex:
            futs = {ex.submit(_work, c, tmp_dir, full, (directions or {}).get(c["id"])): c for c in candidates}
            for fut in as_completed(futs):
                yield fut.result()
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
