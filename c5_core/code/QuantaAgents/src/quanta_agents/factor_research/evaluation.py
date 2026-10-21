"""V9A single-factor evidence and fixed, paired V7 Ridge diagnostics.

The V6 DSL, label engine, quantiles, turnover and RankIC remain the numerical
implementations. This layer freezes the universe and date masks, adds raw-value
Pearson IC, fixed training direction, and local time-block uncertainty. Predictor
comparisons are separate entities and never substitute for a single factor.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import time

import numpy as np
import pandas as pd

from quanta_agents.meta_v6.factors import FactorEngine, FactorSpec, _rank_ic
from quanta_agents.meta_v7.combination import (
    CombinationFitError, TARGET, _fit_model,
)
from quanta_agents.research_kernel.compiler import evaluate_expression

from .adapters import _day, factor_panel
from .cache import FactorCache

VERSION = "v9a_factor_evaluation_1"
LABEL_ID = "next_open_five_sessions_raw_return_v1"


def clean(value):
    if isinstance(value, dict):
        return {str(k): clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean(v) for v in value]
    if isinstance(value, (pd.Timestamp, np.datetime64)):
        return str(pd.Timestamp(value).date())
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (bool, np.bool_)):
        return bool(value)
    if isinstance(value, (float, np.floating)):
        return float(value) if np.isfinite(value) else None
    return value


def _digest(value):
    return hashlib.sha256(json.dumps(clean(value), ensure_ascii=False,
        sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def mask_identity(mask):
    h = hashlib.sha256()
    h.update(json.dumps([str(c) for c in mask.columns], ensure_ascii=False).encode())
    h.update(pd.util.hash_pandas_object(mask.astype(bool), index=True).values.tobytes())
    return h.hexdigest()


def pearson_ic(scores, labels, pool, minimum):
    """Raw score/raw return correlation; no ranks or per-year sign selection."""
    common = pool & np.isfinite(scores) & np.isfinite(labels)
    a, b = scores.where(common), labels.where(common)
    n = common.sum(axis=1)
    with np.errstate(invalid="ignore", divide="ignore"):
        result = a.corrwith(b, axis=1)
    return result.where((n >= minimum) & a.nunique(axis=1).gt(1)
                        & b.nunique(axis=1).gt(1)), n


def moving_block_interval(series, *, block_sessions=20, samples=300, seed=20260912):
    """Resample contiguous session blocks, retaining missing dates in the grid.

    This is a local descriptive interval, not adaptive-search correction. A
    short or effectively unsupported sequence stays unknown.
    """
    values = np.asarray(series, dtype=float)
    observed = int(np.isfinite(values).sum())
    if observed < max(2 * block_sessions, 20) or len(values) < block_sessions:
        return {"method": "moving_session_block_bootstrap", "status": "insufficient_days",
                "block_sessions": block_sessions, "samples": samples,
                "observed_days": observed, "lower": None, "upper": None}
    rng = np.random.default_rng(seed)
    count = math.ceil(len(values) / block_sessions)
    starts = rng.integers(0, len(values) - block_sessions + 1, size=(samples, count))
    positions = (starts[:, :, None] + np.arange(block_sessions)).reshape(samples, -1)[:, :len(values)]
    draws = values[positions]
    with np.errstate(invalid="ignore", divide="ignore"):
        means = np.nansum(draws, axis=1) / np.isfinite(draws).sum(axis=1)
    finite = means[np.isfinite(means)]
    return clean({"method": "moving_session_block_bootstrap", "status": "estimated",
        "block_sessions": block_sessions, "samples": samples, "seed": seed,
        "observed_days": observed, "lower": np.quantile(finite, .025),
        "upper": np.quantile(finite, .975), "confidence": .95,
        "multiple_testing_corrected": False})


class _DiagnosticView(FactorEngine):
    """Feed precomputed scores/shared labels through unchanged V6 diagnostics.

    This view has no evaluator or formula implementation of its own. Overriding
    these accessors avoids copying a whole market panel or recalculating labels
    for each cached candidate; FactorEngine.evaluate does the actual work.
    """
    def __init__(self, scores, labels, engine, horizon):
        self._scores = scores
        self._shared_labels = labels
        self._eligible = engine._eligible
        self.provenance = engine.provenance
        self.data_fingerprint = engine.data_fingerprint
        self._horizon = horizon

    def compute(self, spec):
        return self._scores.copy(deep=True)

    def _label(self, horizon):
        if horizon != self._horizon:
            raise ValueError("diagnostic view has one frozen horizon")
        return self._shared_labels

    @property
    def cache_info(self):
        return {"shared_precomputed_scores": True, "shared_labels": True,
                "account_executions": 0}


class FactorEvaluator:
    def __init__(self, panel, *, train_start, train_end, horizon=5,
                 cache_dir=None, min_cross_section=100, min_train_days=60,
                 block_sessions=20, bootstrap_samples=300, seed=20260912,
                 max_cache_bytes=256 * 1024**2):
        self.train_start, self.train_end = _day(train_start), _day(train_end)
        last_session = panel.eligible.index[-1]
        trailing_year_end = (self.train_end.month == 12 and self.train_end.day == 31
                             and self.train_end.year == last_session.year)
        if self.train_start > self.train_end or (
                self.train_end > last_session and not trailing_year_end):
            raise ValueError("training scope must be ordered and present in the panel")
        if horizon != 5 or type(horizon) is not int:
            raise ValueError("first V9A protocol fixes horizon to five sessions")
        if type(min_cross_section) is not int or min_cross_section < 3:
            raise ValueError("minimum cross section must be an integer >=3")
        if any(type(v) is not int or v < 1 for v in (
                min_train_days, block_sessions, bootstrap_samples)):
            raise ValueError("positive integer support and bootstrap settings required")
        self.panel = factor_panel(panel)
        self.cache = FactorCache(self.panel, cache_dir=cache_dir,
                                 max_cache_bytes=max_cache_bytes)
        self.engine = self.cache.engine
        self.pool = self.engine._eligible
        self.labels = self.engine.labels(horizon).where(
            np.isfinite(self.engine.labels(horizon)))
        self.horizon = horizon
        self.minimum = min_cross_section
        self.min_train_days = min_train_days
        self.block_sessions = block_sessions
        self.bootstrap_samples = bootstrap_samples
        self.seed = seed
        calendar_path = self.panel.provenance.get("request", {}).get("calendar_path")
        self.calendar = None
        if calendar_path:
            # Trading dates are metadata, including any future-year entries.
            self.calendar = pd.DatetimeIndex([_day(day) for day in
                Path(calendar_path).read_text(encoding="utf-8").splitlines() if day.strip()])
            if not self.calendar.is_unique or not self.calendar.is_monotonic_increasing:
                raise ValueError("frozen calendar metadata must be unique and sorted")
        self._directions = {}
        self._rank_frames = {}
        self._baseline_models = {}
        self.universe_id = self.panel.provenance["factor_research"]["universe_id"]

    def compute(self, spec, *, use_cache=True):
        return self.cache.compute(spec, use_cache=use_cache)

    def _scope(self, start, end):
        first, last = _day(start), _day(end)
        if first > last or last > self.pool.index[-1] + pd.offsets.YearEnd(0):
            raise ValueError("evaluation range exceeds the loaded calendar year or is reversed")
        # Allow Dec 31 when the actual final market session precedes it, while
        # rejecting a request to silently score un-loaded future months/years.
        if last > self.pool.index[-1] and not (
                last.month == 12 and last.day == 31 and last.year == self.pool.index[-1].year):
            raise ValueError("evaluation range exceeds loaded market dates")
        dates = self.pool.index[(self.pool.index >= first) & (self.pool.index <= last)]
        if self.calendar is not None:
            expected = self.calendar[(self.calendar >= first) & (self.calendar <= last)]
            if not dates.equals(expected.rename(dates.name)):
                raise ValueError("evaluation slice is missing sessions from its frozen calendar")
        safe = pd.Series(False, index=dates, dtype=bool)
        endpoints = {}
        positions = pd.Series(np.arange(len(self.pool)), index=self.pool.index)
        for year in sorted(set(dates.year)):
            boundary = min(last, pd.Timestamp(year=year, month=12, day=31))
            terminal = int(self.pool.index.searchsorted(boundary, side="right")) - 1
            part = dates[dates.year == year]
            safe.loc[part] = positions.loc[part].to_numpy() + self.horizon + 1 <= terminal
            endpoints[str(year)] = {"allowed_label_endpoint": str(self.pool.index[terminal].date()),
                "purged_signal_dates": [str(x.date()) for x in part[~safe.loc[part].to_numpy()]]}
        labels = self.labels.loc[dates].where(safe, axis=0)
        return dates, safe, labels, endpoints

    def _ci(self, series):
        return moving_block_interval(series, block_sessions=self.block_sessions,
            samples=self.bootstrap_samples, seed=self.seed)

    def fit_direction(self, spec):
        if spec.factor_id not in self._directions:
            dates, _, labels, endpoints = self._scope(self.train_start, self.train_end)
            raw, counts = pearson_ic(self.compute(spec).loc[dates], labels,
                                    self.pool.loc[dates], self.minimum)
            mean = raw.mean()
            valid = int(raw.count()) >= self.min_train_days and np.isfinite(mean) and mean != 0
            self._directions[spec.factor_id] = clean({
                "direction": int(np.sign(mean)) if valid else 0,
                "status": "frozen_from_training" if valid else "unknown_training_direction",
                "train_start": str(self.train_start.date()), "train_end": str(self.train_end.date()),
                "raw_train_mean_pearson_ic": mean, "valid_days": int(raw.count()),
                "paired_cells": int(counts.sum()), "annual_endpoint_purge": endpoints,
                "criterion": "sign_of_arithmetic_mean_raw_daily_Pearson_IC",
                "per_year_direction_updates": False})
        return deepcopy(self._directions[spec.factor_id])

    def _summary(self, daily):
        pool_cells = int(daily.pool_count.sum())
        paired_cells = int(daily.paired_count.sum())
        score_cells = int(daily.factor_count.sum())
        row = {"mean_pearson_ic": daily.pearson_ic.mean(),
            "mean_raw_pearson_ic": daily.raw_pearson_ic.mean(),
            "mean_rank_ic": daily.rank_ic.mean(), "valid_days": int(daily.pearson_ic.count()),
            "rank_ic_valid_days": int(daily.rank_ic.count()), "signal_days": len(daily),
            "label_safe_days": int(daily.label_safe.sum()),
            "purged_signal_days": int((~daily.label_safe).sum()),
            "pool_cells": pool_cells, "factor_cells": score_cells, "paired_cells": paired_cells,
            "factor_coverage": score_cells / pool_cells if pool_cells else None,
            "evaluation_coverage": paired_cells / pool_cells if pool_cells else None,
            "mean_paired_stocks": daily.paired_count.mean(),
            "mean_head_relative_pool": daily.head_relative_pool.mean(),
            "mean_top40_relative_pool": daily.top40_relative_pool.mean(),
            "mean_top40_observed_relative_pool": daily.top40_observed_relative_pool.mean(),
            "top40_complete_days": int(daily.top40_relative_pool.count()),
            "top40_selected_cells": int(daily.top40_count.sum()),
            "top40_observed_label_cells": int(daily.top40_label_count.sum()),
            "top40_missing_label_cells": int(daily.top40_missing_label_count.sum()),
            "mean_top_minus_bottom": daily.top_minus_bottom.mean(),
            "pearson_ic_interval": self._ci(daily.pearson_ic),
            "rank_ic_interval": self._ci(daily.rank_ic),
            "head_relative_pool_interval": self._ci(daily.head_relative_pool),
            "mean_quantile_returns": {str(q): daily[f"q{q}"].mean() for q in range(1, 6)}}
        return clean(row)

    def evaluate(self, spec, *, start, end, direction=None):
        started = time.perf_counter()
        dates, safe, labels, endpoints = self._scope(start, end)
        frozen = self.fit_direction(spec)
        if direction is not None and (type(direction) is not int or direction != frozen["direction"]):
            raise ValueError("provided direction differs from the frozen training-only rule")
        direction = frozen["direction"]
        scores = self.compute(spec)
        oriented = scores * direction if direction else scores * np.nan
        # Reuse V6's complete RankIC/quantile/turnover implementation on the
        # shared scores, then apply the V9A annual endpoint rule to every metric.
        base = _DiagnosticView(oriented, self.labels, self.engine, self.horizon).evaluate(
            spec, horizons=(self.horizon,), quantiles=5, min_cross_section=self.minimum)
        raw_ic, counts = pearson_ic(scores.loc[dates], labels, self.pool.loc[dates], self.minimum)
        rank = base.daily_ic.set_index("date").loc[dates, "rank_ic"].where(safe)
        daily = pd.DataFrame({"date": dates, "label_safe": safe.to_numpy(),
            "raw_pearson_ic": raw_ic.to_numpy(),
            "pearson_ic": (raw_ic * direction if direction else raw_ic * np.nan).to_numpy(),
            "rank_ic": rank.to_numpy(), "paired_count": counts.to_numpy(),
            "pool_count": self.pool.loc[dates].sum(axis=1).to_numpy(),
            "factor_count": scores.loc[dates].notna().sum(axis=1).to_numpy(),
            "label_count": labels.notna().sum(axis=1).to_numpy()}).set_index("date", drop=False)
        quantiles = base.quantile_returns.pivot(index="date", columns="quantile", values="mean_forward_return")
        quantile_counts = base.quantile_returns.pivot(index="date", columns="quantile", values="count")
        for q in range(1, 6):
            daily[f"q{q}"] = quantiles.loc[dates, q].where(safe)
            daily[f"q{q}_label_count"] = quantile_counts.loc[dates, q].where(safe, 0)
        daily["pool_forward_return"] = labels.where(self.pool.loc[dates]).mean(axis=1)
        daily["head_relative_pool"] = daily.q5 - daily.pool_forward_return
        daily["top_minus_bottom"] = daily.q5 - daily.q1
        # Select exactly by signal-time scores, before looking at future labels.
        # Stable ties follow the frozen stock-column order. Missing exits never
        # cause a lower-ranked replacement, and the primary mean requires all 40.
        selected = oriented.loc[dates].rank(axis=1, method="first", ascending=False).le(40)
        top_labels = labels.where(selected)
        daily["top40_count"] = selected.sum(axis=1)
        daily["top40_label_count"] = top_labels.notna().sum(axis=1)
        daily["top40_missing_label_count"] = daily.top40_count - daily.top40_label_count
        daily["top40_observed_mean_forward_return"] = top_labels.mean(axis=1)
        complete = daily.top40_count.eq(40) & daily.top40_label_count.eq(40)
        daily["top40_mean_forward_return"] = daily.top40_observed_mean_forward_return.where(complete)
        daily["top40_relative_pool"] = daily.top40_mean_forward_return - daily.pool_forward_return
        daily["top40_observed_relative_pool"] = daily.top40_observed_mean_forward_return - daily.pool_forward_return
        turnover = base.turnover.pivot(index="date", columns="quantile", values="turnover")
        daily["head_turnover"] = turnover.loc[dates, 5]
        summary = self._summary(daily)
        annual = [{"year": int(year), **self._summary(part),
            "requested_full_natural_year": _day(start) <= pd.Timestamp(year=year, month=1, day=1)
                and _day(end) >= pd.Timestamp(year=year, month=12, day=31),
            "calendar_completeness": "verified_against_frozen_calendar" if self.calendar is not None
                else "caller_supplied_axis_not_independently_verified"}
            for year, part in daily.groupby(daily.index.year)]
        common = self.pool.loc[dates] & scores.loc[dates].notna() & labels.notna()
        return clean({"version": VERSION, "entity_type": "single_factor", "entity_id": spec.factor_id,
            "factor_id": spec.factor_id, "spec": spec.to_dict(),
            "status": "evaluated" if summary["valid_days"] else "insufficient_evidence",
            "implementation_status": "executable", "direction": direction, "direction_fit": frozen,
            "label_id": LABEL_ID, "universe_id": self.universe_id,
            "split": {"start": str(_day(start).date()), "end": str(_day(end).date()),
                      "annual_endpoint_purge": endpoints},
            "sample_mask_id": mask_identity(common), "data_fingerprint": self.engine.data_fingerprint,
            "summary": summary, "annual": annual,
            "daily": daily.reset_index(drop=True).to_dict(orient="records"),
            "semantics": {"primary_metric": "raw_single_factor_fixed_direction_vs_raw_return_daily_Pearson",
                "label": "adjusted_open[t+6]/adjusted_open[t+1]-1",
                "annual_aggregation": "arithmetic_mean_of_finite_daily_IC_without_annualization",
                "rank_ic": "Spearman_average_ties_on_same_finite_stock_date_sample",
                "quantiles": "V6_five_average_rank_buckets_ties_together_empty_buckets_unknown",
                "head": "highest_fixed_direction_quintile_minus_full_signal_pool_label_mean",
                "top40": "signal_time_top40_stable_column_ties_no_missing_exit_replacement_primary_requires_all40_labels",
                "coverage_denominator": "all_signal_pool_cells_including_missing_factor_and_purged_labels",
                "pool_rank_scope": "same_execution_pool_at_every_factor_rank_node",
                "annual_purge_sessions": self.horizon + 1},
            "uncertainty": {"method": "moving_session_block_bootstrap", "block_sessions": self.block_sessions,
                "samples": self.bootstrap_samples, "seed": self.seed, "search_correction": False},
            "limitations": ["previously_exposed_history", "not_independent_OOS", "historical_arrival_unverified",
                "no_execution_or_profitability_claim", "overlapping_labels", "adaptive_search_not_corrected"],
            "resources": {"seconds": time.perf_counter() - started, "cache": self.cache.info,
                          "model_calls": 0, "account_executions": 0}})

    def evaluate_batch(self, specs, *, start, end, directions=None):
        return {spec.factor_id: self.evaluate(spec, start=start, end=end,
            direction=(directions or {}).get(spec.factor_id)) for spec in specs}

    def paired_factor_comparison(self, spec, control, *, start, end):
        """Raw single-factor quality gain on identical stock/date samples."""
        dates, safe, labels, _ = self._scope(start, end)
        a, b = self.compute(spec).loc[dates], self.compute(control).loc[dates]
        common = self.pool.loc[dates] & a.notna() & b.notna() & labels.notna()
        da, db = self.fit_direction(spec)["direction"], self.fit_direction(control)["direction"]
        ai, n = pearson_ic(a * da if da else a * np.nan, labels, common, self.minimum)
        bi, _ = pearson_ic(b * db if db else b * np.nan, labels, common, self.minimum)
        delta = (ai - bi).where(ai.notna() & bi.notna())
        return clean({"entity_type": "single_factor_comparison", "candidate_id": spec.factor_id,
            "control_id": control.factor_id, "sample_mask_id": mask_identity(common),
            "paired_delta_pearson_ic": delta.mean(), "valid_days": int(delta.count()),
            "interval": self._ci(delta), "daily": [{"date": date, "candidate_pearson_ic": ai.loc[date],
                "control_pearson_ic": bi.loc[date], "delta_pearson_ic": delta.loc[date],
                "paired_count": n.loc[date]} for date in dates]})

    def _ranked(self, spec, *, retain=False):
        # Retain only the few fixed baseline/main-effect ranks; callers should
        # not store the full candidate population as a second unbounded cache.
        if spec.factor_id in self._rank_frames:
            return self._rank_frames[spec.factor_id]
        result = self.compute(spec).rank(axis=1, method="average", pct=True) - .5
        if retain:
            size = int(result.memory_usage(index=True, deep=True).sum())
            current = sum(int(frame.memory_usage(index=True, deep=True).sum())
                          for frame in self._rank_frames.values())
            if current + size <= self.cache.max_cache_bytes:
                self._rank_frames[spec.factor_id] = result
        return result

    def incremental(self, spec, baseline_specs, *, start, end, main_effects=(), ridge_lambda=.1):
        """B(original), B(common), B+f(common), using the existing fixed Ridge.

        Interaction callers pass both main effects. Baseline/main columns are
        sorted by immutable factor ID; augmented columns append f. Features are
        ranked in the original signal pool BEFORE common-sample masks are made.
        The target is V7 rank-return; all reported IC uses original returns.
        """
        if _day(start) <= self.train_end:
            raise ValueError("incremental prediction must start strictly after the training end")
        if ridge_lambda != .1:
            raise ValueError("first V9A baseline fixes ridge_lambda=.1")
        main = {s.factor_id: s for s in [*baseline_specs, *main_effects]}
        if not main:
            raise ValueError("a nonempty frozen baseline is required")
        if spec.factor_id in main:
            return {"entity_type": "predictor_comparison", "status": "duplicate_baseline_feature",
                    "candidate_id": spec.factor_id, "paired_delta_pearson_ic": None,
                    "paired_delta_rank_ic": None, "valid_days": 0, "daily": []}
        order = sorted(main)
        frames = {key: self._ranked(main[key], retain=True) for key in order}
        frames[spec.factor_id] = self._ranked(spec)
        train_dates, _, train_labels, purge = self._scope(self.train_start, self.train_end)
        dates, safe, labels, _ = self._scope(start, end)
        train_frames = {key: value.loc[train_dates] for key, value in frames.items()}
        original_train = self.pool.loc[train_dates] & train_labels.notna()
        original_test = self.pool.loc[dates] & labels.notna()
        for key in order:
            original_train &= train_frames[key].notna()
            original_test &= frames[key].loc[dates].notna()
        common_train = original_train & train_frames[spec.factor_id].notna()
        common_test = original_test & frames[spec.factor_id].loc[dates].notna()
        for mask in (original_train, original_test, common_train, common_test):
            mask &= mask.sum(axis=1).ge(self.minimum).to_numpy()[:, None]
        config = {"min_train_days": self.min_train_days,
                  "min_train_cells": self.min_train_days * self.minimum}
        audit = {"configuration": config}
        fit_cfg = {"min_cross_section": self.minimum}

        def fit(keys, mask):
            features = [{"id": key, "kind": "frozen_pool_centered_rank",
                         "expression": {"op": "factor", "id": key}} for key in keys]
            model = _fit_model(features, train_frames, mask, train_labels, fit_cfg, audit,
                               ridge_lambda=ridge_lambda)
            model.update(feature_order=list(keys), sample_mask_id=mask_identity(mask),
                train_start=str(self.train_start.date()), train_end=str(self.train_end.date()),
                annual_endpoint_purge=purge,
                preprocessing="average_percentile_rank_in_original_execution_pool_minus_0.5")
            return model

        baseline_id = _digest({"columns": order, "ridge_lambda": ridge_lambda,
            "train_start": str(self.train_start.date()), "train_end": str(self.train_end.date()),
            "target": TARGET, "data_fingerprint": self.engine.data_fingerprint})
        try:
            if baseline_id not in self._baseline_models:
                self._baseline_models[baseline_id] = fit(order, original_train)
            original = deepcopy(self._baseline_models[baseline_id])
            common = fit(order, common_train)
            augmented = fit(order + [spec.factor_id], common_train)
        except CombinationFitError as exc:
            return clean({"entity_type": "predictor_comparison", "status": "fit_unavailable",
                "candidate_id": spec.factor_id, "baseline_id": baseline_id, "reason": str(exc),
                "fit_audit": exc.audit, "paired_delta_pearson_ic": None,
                "paired_delta_rank_ic": None, "valid_days": 0, "daily": []})
        test_frames = {key: value.loc[dates] for key, value in frames.items()}
        predictions = {name: evaluate_expression(model["expression"], test_frames, self.pool.loc[dates])
            for name, model in (("baseline_original", original), ("baseline_common", common),
                                ("augmented", augmented))}
        daily = pd.DataFrame({"date": dates, "label_safe": safe.to_numpy()}).set_index("date", drop=False)
        for name, prediction in predictions.items():
            mask = original_test if name == "baseline_original" else common_test
            pi, n = pearson_ic(prediction, labels, mask, self.minimum)
            ri, _ = _rank_ic(prediction.where(mask), labels.where(mask), self.minimum)
            daily[name + "_pearson_ic"] = pi
            daily[name + "_rank_ic"] = ri
            daily[name + "_paired_count"] = n
        # The original-coverage model on common test rows separately measures
        # prediction sample changes without changing coefficients.
        oi, _ = pearson_ic(predictions["baseline_original"], labels, common_test, self.minimum)
        daily["baseline_original_on_common_pearson_ic"] = oi
        for metric in ("pearson_ic", "rank_ic"):
            daily["delta_" + metric] = daily["augmented_" + metric] - daily["baseline_common_" + metric]

        def stats(part):
            return clean({"paired_delta_pearson_ic": part.delta_pearson_ic.mean(),
                "paired_delta_rank_ic": part.delta_rank_ic.mean(),
                "valid_days": int(part.delta_pearson_ic.count()),
                "baseline_original_pearson_ic": part.baseline_original_pearson_ic.mean(),
                "baseline_common_pearson_ic": part.baseline_common_pearson_ic.mean(),
                "augmented_pearson_ic": part.augmented_pearson_ic.mean(),
                "baseline_original_rank_ic": part.baseline_original_rank_ic.mean(),
                "baseline_common_rank_ic": part.baseline_common_rank_ic.mean(),
                "augmented_rank_ic": part.augmented_rank_ic.mean(),
                "baseline_original_on_common_pearson_ic": part.baseline_original_on_common_pearson_ic.mean(),
                "coverage_selection_delta_pearson_ic": (part.baseline_original_on_common_pearson_ic
                                                        - part.baseline_original_pearson_ic).mean(),
                "common_refit_delta_pearson_ic": (part.baseline_common_pearson_ic
                                                  - part.baseline_original_on_common_pearson_ic).mean(),
                "interval": self._ci(part.delta_pearson_ic),
                "rank_ic_interval": self._ci(part.delta_rank_ic),
                "baseline_original_cells": int(part.baseline_original_paired_count.sum()),
                "common_cells": int(part.baseline_common_paired_count.sum())})
        return clean({"version": VERSION, "entity_type": "predictor_comparison", "status": "evaluated",
            "candidate_id": spec.factor_id, "baseline_id": baseline_id, "target": TARGET,
            "single_factor_target_substitution": False, "label_id": LABEL_ID,
            "universe_id": self.universe_id, "sample_mask_id": mask_identity(common_test),
            "train_sample_mask_id": mask_identity(common_train),
            "original_train_sample_mask_id": mask_identity(original_train),
            "main_effect_ids": sorted({s.factor_id for s in main_effects}),
            "comparison": "B+main_effects versus B+main_effects+candidate" if main_effects else "B versus B+f",
            "settings": {"ridge_lambda": ridge_lambda, "fit_once": True,
                "train_end": str(self.train_end.date()), "prediction_start": str(_day(start).date()),
                "prediction_end": str(_day(end).date()), "randomness": "deterministic_linear_solve",
                "preprocessing_pool_uses_future_labels": False},
            **stats(daily), "annual": [{"year": int(year), **stats(part)}
                for year, part in daily.groupby(daily.index.year)],
            "models": {"baseline_original": original, "baseline_common": common, "augmented": augmented},
            "daily": daily.reset_index(drop=True).to_dict(orient="records"),
            "limitations": ["fixed_rank_linear_predictor_only", "no_general_nonlinear_impossibility_claim",
                "previously_exposed_history", "not_single_factor_IC", "no_adaptive_search_correction"]})
