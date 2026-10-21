"""V10 orchestration around the unchanged V6/V9 factor numerical contracts."""
from __future__ import annotations

from pathlib import Path
import numpy as np
import pandas as pd

from quanta_agents.factor_research.evaluation import FactorEvaluator
from quanta_agents.meta_v6.factors import FactorSpec
from quanta_agents.research_kernel.assets import _calculator_identity
from .numeric_cache import NumericCache, digest, file_hash

PREPROCESSING = "average_percentile_rank_in_original_execution_pool_minus_0.5"


class _PredictionEvidence(FactorEvaluator):
    def __init__(self, source, scores):
        self.__dict__.update(source.__dict__)
        self._prediction = scores

    def compute(self, spec):
        return self._prediction

    def fit_direction(self, spec):
        return {"direction": 1, "status": "prediction_orientation_fixed_by_training",
                "per_year_direction_updates": False}


class NumericContext:
    def __init__(self, panel, *, train_start="2016-01-01", train_end="2018-12-31",
                 cache_dir=None, min_cross_section=100, min_train_days=60,
                 bootstrap_samples=300, block_sessions=20, seed=20260912,
                 max_cache_bytes=256 * 1024**2):
        directory = Path(cache_dir) if cache_dir else None
        self.evaluator = FactorEvaluator(panel, train_start=train_start, train_end=train_end,
            cache_dir=directory / "factors" if directory else None,
            min_cross_section=min_cross_section, min_train_days=min_train_days,
            bootstrap_samples=bootstrap_samples, block_sessions=block_sessions,
            seed=seed, max_cache_bytes=max_cache_bytes)
        self.pool = self.evaluator.pool
        self.labels = self.evaluator.labels
        self.dates, self.columns = self.pool.index, self.pool.columns
        self.minimum, self.min_train_days = min_cross_section, min_train_days
        self.specs = {}
        self.cache = NumericCache(directory / "numeric" if directory else None,
                                  max_memory_bytes=max_cache_bytes)
        sources = [Path(__file__), Path(__file__).with_name("combination.py"),
                   Path(__file__).with_name("numeric_cache.py")]
        self.identity = {"version": "v10_numeric_1",
            "data_fingerprint": self.evaluator.engine.data_fingerprint,
            "axes": digest({"dates": [str(d.date()) for d in self.dates],
                            "columns": list(map(str, self.columns))}),
            "implementation": {p.name: file_hash(p) for p in sources if p.exists()},
            "factor_calculator": _calculator_identity(),
            "min_cross_section": self.minimum, "min_train_days": self.min_train_days,
            "preprocessing": PREPROCESSING}

    def register(self, specs):
        for spec in specs:
            if not isinstance(spec, FactorSpec):
                raise TypeError("register accepts V6 FactorSpec objects")
            previous = self.specs.get(spec.factor_id)
            if previous is not None and previous.expression != spec.expression:
                raise ValueError("factor identity collision")
            self.specs[spec.factor_id] = spec
        return self

    def resolve(self, spec_or_id):
        if isinstance(spec_or_id, FactorSpec):
            self.register([spec_or_id])
            return spec_or_id
        return self.specs[spec_or_id]

    def compute(self, spec_or_id):
        return self.evaluator.compute(self.resolve(spec_or_id))

    def ranked(self, spec_or_id):
        spec = self.resolve(spec_or_id)
        key = {**self.identity, "factor_id": spec.factor_id, "expression": spec.expression}
        return self.cache.rank_array(key, self.pool.shape,
            lambda: (self.compute(spec).rank(axis=1, method="average", pct=True) - .5).to_numpy())

    def evaluate_factor(self, spec_or_id, *, start, end):
        return self.evaluator.evaluate(self.resolve(spec_or_id), start=start, end=end)

    def evaluate_prediction(self, entity_id, frame, *, start, end):
        if not frame.index.equals(self.dates) or not frame.columns.equals(self.columns):
            raise ValueError("prediction axes must exactly match the frozen panel")
        if np.isinf(frame.to_numpy()).any():
            raise ValueError("prediction contains infinity")
        if frame.where(~self.pool).notna().any().any():
            raise ValueError("prediction supplied outside execution pool")
        report = _PredictionEvidence(self.evaluator, frame).evaluate(
            FactorSpec("precomputed_prediction_diagnostic", "close"), start=start, end=end)
        report.pop("factor_id", None)
        report.pop("spec", None)
        report.update(version="v10_numeric_1", entity_type="combination", entity_id=entity_id)
        report["semantics"]["primary_metric"] = "training_only_prediction_vs_raw_return_daily_Pearson"
        return report
