"""Bounded shared expression cache using the existing AssetRegistry payloads.

No pickle or account arrays. One FactorEngine snapshot and DAG are shared by a
batch; persisted values retain existing hash/axis/dtype integrity checks.
"""
from __future__ import annotations

from collections import OrderedDict
from pathlib import Path

from quanta_agents.meta_v6.factors import FactorEngine
from quanta_agents.research_kernel.assets import AssetRegistry, _calculator_identity

VERSION = "v9a_shared_factor_cache_1"


class FactorCache:
    def __init__(self, panel, *, cache_dir=None, max_cache_bytes=256 * 1024**2):
        self.engine = FactorEngine(panel, max_cache_bytes=max_cache_bytes)
        self.registry = AssetRegistry(Path(cache_dir)) if cache_dir is not None else None
        self.max_cache_bytes = max_cache_bytes
        self._values = OrderedDict()
        self._bytes = 0
        self.metrics = {"memory_hits": 0, "disk_hits": 0, "computed": 0,
                        "corruptions": [], "account_executions": 0}

    def compute(self, spec, *, use_cache=True):
        # Validate the loaded calculator against disk before trusting a payload.
        calculator = _calculator_identity()
        identity = {"expression": spec.expression, "calculator": calculator,
                    "panel_fingerprint": self.engine.data_fingerprint}
        if use_cache and spec.factor_id in self._values:
            self.metrics["memory_hits"] += 1
            self._values.move_to_end(spec.factor_id)
            return self._values[spec.factor_id].copy(deep=True)
        result = None
        if use_cache and self.registry is not None:
            result, corruption = self.registry._cache_read(identity, self.engine._eligible)
            if corruption:
                self.metrics["corruptions"].append({"factor_id": spec.factor_id, "reason": corruption})
            if result is not None:
                self.metrics["disk_hits"] += 1
        if result is None:
            result = self.engine.compute(spec)
            self.metrics["computed"] += 1
            if use_cache and self.registry is not None:
                self.registry._cache_write(identity, result)
        if use_cache:
            size = int(result.memory_usage(index=True, deep=True).sum())
            if size <= self.max_cache_bytes:
                while self._values and self._bytes + size > self.max_cache_bytes:
                    _, old = self._values.popitem(last=False)
                    self._bytes -= int(old.memory_usage(index=True, deep=True).sum())
                self._values[spec.factor_id] = result.copy(deep=True)
                self._bytes += size
        return result.copy(deep=True)

    def compute_batch(self, specs, *, use_cache=True):
        return {spec.factor_id: self.compute(spec, use_cache=use_cache) for spec in specs}

    @property
    def info(self):
        return {"version": VERSION, **self.metrics, "retained_score_bytes": self._bytes,
                "retained_scores": len(self._values), "engine": self.engine.cache_info}
