"""Read-only shared arrays and parallel full-account evaluation for V9 ranges.

The account implementation is the established DailyAccount. This module removes
repeated data loading/ranking, not transaction costs, lot sizing or raw outputs.
"""
from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
import os
import time

import numpy as np
import pandas as pd
import psutil

from quanta_agents.meta_v6.portfolio import AccountPolicy, DailyAccount, PortfolioSpec, _selection_weights
from quanta_agents.meta_v7.execution import execution_pool
from quanta_agents.research_kernel.store import clean, digest, write_json
from .study import read, sha, once

_WORKER = None


def build_cache(root, panel, frames, policy, binding):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    if (root / "manifest.json").exists():
        manifest = verify_cache(root)
        if manifest["binding"] != binding:
            raise ValueError("Prepared array cache has a different source binding")
        return manifest
    def save(name, value):
        np.save(root / (name + ".npy"), value, allow_pickle=False)
        return name + ".npy"
    account = DailyAccount(panel, policy)
    arrays = {"a": {k: save("account_" + k, v) for k, v in account.a.items()}}
    for key in ("upper", "lower", "special_delisting_exit", "can_sell", "can_buy", "valid_open", "prior_capacity"):
        arrays[key] = save(key, getattr(account, key))
    eligible = execution_pool(panel)
    arrays["pool"] = save("pool", eligible.to_numpy())
    arrays["eligible"] = save("eligible", panel.eligible.to_numpy())
    ranks = {}
    coverage = {}
    denominator = int(eligible.to_numpy().sum())
    for j, (name, frame) in enumerate(sorted(frames.items())):
        value = frame.where(eligible).replace([np.inf, -np.inf], np.nan)
        coverage[name] = int(value.notna().to_numpy().sum()) / denominator if denominator else 0.
        ranks[name] = {str(sign): save(f"rank_{j}_{sign}", value.mul(sign).rank(axis=1, method="average", pct=True).to_numpy())
                       for sign in (-1, 1)}
    close = panel.fields["close"]
    arrays["vol"] = save("vol", close.pct_change(fill_method=None).rolling(20, min_periods=20).std(ddof=1).to_numpy())
    market_returns = close.pct_change(fill_method=None).where(panel.eligible).mean(axis=1)
    market_index = (1 + market_returns).cumprod()
    trends = {"none": save("trend_none", np.ones(len(close), dtype=bool))}
    for window in (60, 120):
        trend = market_index.gt(market_index.rolling(window, min_periods=window).mean()) & market_returns.notna()
        trends["trend" + str(window)] = save("trend" + str(window), trend.to_numpy())
    manifest = {"version": "v9_range_arrays_1", "binding": binding, "arrays": arrays, "ranks": ranks,
                "trends": trends, "coverage": coverage, "dates": [str(x.date()) for x in panel.dates],
                "codes": list(panel.eligible.columns), "policy": asdict(policy), "provenance": panel.provenance,
                "files": {p.name: sha(p) for p in root.glob("*.npy")}}
    once(root / "manifest.json", manifest)
    return manifest


def verify_cache(root):
    root = Path(root)
    manifest = read(root / "manifest.json")
    for name, expected in manifest["files"].items():
        if sha(root / name) != expected:
            raise ValueError("Shared array cache changed: " + name)
    return manifest


class PreparedBatch:
    def __init__(self, root):
        self.root = Path(root)
        self.manifest = m = read(self.root / "manifest.json")
        self.dates = pd.DatetimeIndex(m["dates"], name="date")
        self.codes = pd.Index(m["codes"], name="symbol")
        self.policy = AccountPolicy(**m["policy"])
        load = lambda name: np.load(self.root / name, mmap_mode="r", allow_pickle=False)
        self.ranks = {name: {s: load(p) for s, p in paths.items()} for name, paths in m["ranks"].items()}
        self.trends = {name: load(p) for name, p in m["trends"].items()}
        self.vol = load(m["arrays"]["vol"])
        self.pool = load(m["arrays"]["pool"])
        account = DailyAccount.__new__(DailyAccount)
        account.policy, account.dates, account.codes = self.policy, self.dates, list(self.codes)
        account.panel = SimpleNamespace(eligible=pd.DataFrame(load(m["arrays"]["eligible"]), index=self.dates, columns=self.codes, copy=False))
        account.a = {k: load(p) for k, p in m["arrays"]["a"].items()}
        for key in ("upper", "lower", "special_delisting_exit", "can_sell", "can_buy", "valid_open", "prior_capacity"):
            setattr(account, key, load(m["arrays"][key]))
        self.account = account

    def targets(self, spec, start, end):
        spec = PortfolioSpec(name="v9_range", **spec)
        positions = np.flatnonzero((self.dates >= start) & (self.dates <= end))
        if not len(positions) or positions[0] == 0:
            raise ValueError("Nonempty account period requires a previous signal date")
        if spec.rebalance_schedule != "sessions":
            raise ValueError("Range search uses explicit session intervals")
        signals = np.arange(positions[0] - 1, positions[-1], spec.rebalance_sessions)
        score = np.zeros((len(signals), len(self.codes)))
        mass = sum(abs(w) for w in spec.factor_weights.values())
        for name, weight in spec.factor_weights.items():
            if weight:
                score += self.ranks[name][str(1 if weight > 0 else -1)][signals] * (abs(weight) / mass)
        rows, plans = [], {}
        for k, i in enumerate(signals):
            valid = np.flatnonzero(np.isfinite(score[k]))
            order = valid[np.argsort(-score[k, valid], kind="stable")]
            observed = self.vol[i]
            coeff = (np.ones(len(self.codes)) if spec.weighting == "equal" else
                     np.divide(1., observed, out=np.zeros_like(observed), where=np.isfinite(observed) & (observed > 0)))
            plan = {"order": order.tolist(), "exposure": float(spec.gross_exposure * self.trends[spec.market_filter][i]),
                    "coefficient": coeff.tolist(), "gate": np.ones(len(self.codes)).tolist()}
            rows.append(_selection_weights(plan, spec, np.zeros(len(self.codes), dtype=bool), len(self.codes)))
            if spec.membership_buffer:
                plans[str(self.dates[i].date())] = plan
        result = pd.DataFrame(rows, index=pd.DatetimeIndex(self.dates[signals], name="signal_date"), columns=self.codes)
        if plans:
            result.attrs = {"selection_plans": plans, "portfolio_spec": asdict(spec)}
        return result

    def benchmark_targets(self, spec, start, end):
        positions = np.flatnonzero((self.dates >= start) & (self.dates <= end))
        signals = np.arange(positions[0] - 1, positions[-1], spec["rebalance_sessions"])
        pool = self.pool[signals]
        counts = pool.sum(axis=1)
        per_stock = np.minimum(np.divide(1., counts, out=np.zeros(len(counts)), where=counts > 0), spec["max_stock_weight"])
        values = pool * per_stock[:, None]
        return pd.DataFrame(values, index=pd.DatetimeIndex(self.dates[signals], name="signal_date"), columns=self.codes)


def init_worker(cache_root):
    global _WORKER
    _WORKER = PreparedBatch(cache_root)


def evaluate_job(job):
    started = time.perf_counter()
    folder = Path(job["folder"])
    folder.mkdir(parents=True, exist_ok=True)
    # Coordinator handles cache hits; raw failures remain distinct from success.
    try:
        targets = (_WORKER.benchmark_targets(job["spec"], job["start"], job["end"]) if job.get("benchmark") else
                   _WORKER.targets(job["spec"], job["start"], job["end"]))
        cancel_path = Path(job["study_root"]) / "cancel.request"
        result = _WORKER.account.run(targets, start=job["start"], end=job["end"],
                                     cost_multiplier=job.get("cost_multiplier", 1.), cancelled=cancel_path.exists)
        raw_files = {}
        for key in ("daily", "trades", "annual"):
            path = folder / (key + ".parquet")
            result[key].to_parquet(path)
            raw_files[path.name] = sha(path)
        # Attributes contain the exact account-dependent selection plans.
        targets.to_parquet(folder / "targets.parquet")
        raw_files["targets.parquet"] = sha(folder / "targets.parquet")
        row = {"id": job["id"], "spec": job["spec"], "start": job["start"], "end": job["end"],
               "cost_multiplier": job.get("cost_multiplier", 1.), "status": "completed",
               "summary": clean(result["summary"]), "annual": clean(result["annual"].to_dict("records")),
               "raw_files": raw_files, "folder": str(folder), "seconds": time.perf_counter() - started,
               "worker_pid": os.getpid(), "worker_private_bytes": getattr(psutil.Process().memory_info(), "private", psutil.Process().memory_info().rss)}
    except Exception as exc:
        row = {"id": job["id"], "spec": job["spec"], "start": job["start"], "end": job["end"],
               "status": "failed", "summary": None, "error": {"type": type(exc).__name__, "message": str(exc)},
               "seconds": time.perf_counter() - started, "folder": str(folder)}
    once(folder / "result.json", row)
    return row
