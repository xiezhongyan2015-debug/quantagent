"""Causal wide Ridge and equal-direction predictors, independent of accounts.

The weighted standardized Ridge equations are the existing V7 equations. Daily
sufficient statistics add raw-return targets and arbitrary registered member
counts without changing the old compiler or its frozen defaults.
"""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import asdict, dataclass, replace
from copy import deepcopy
import time

import numpy as np
import pandas as pd

from quanta_agents.factor_research.evaluation import clean, mask_identity, pearson_ic
from quanta_agents.meta_v6.factors import _rank_ic
from .numeric import PREPROCESSING
from .numeric_cache import NumericCache, digest

TARGETS = ("raw_return_demeaned", "rank_return_demeaned")
UPDATE_RULES = ("fixed", "quarterly_rolling3y", "quarterly_expanding")


@dataclass(frozen=True)
class CombinationSpec:
    name: str
    feature_ids: tuple[str, ...]
    method: str = "ridge"
    target: str = "raw_return_demeaned"
    ridge_lambda: float = .1
    update_rule: str = "fixed"
    fit_start: str = "2016-01-01"
    fit_end: str = "2018-12-31"
    selection_rule: str = "fixed_registered_members"
    candidate_ids: tuple[str, ...] = ()

    def __post_init__(self):
        members = tuple(sorted(self.feature_ids))
        if not members or len(set(members)) != len(members) or len(members) > 128:
            raise ValueError("one to 128 unique registered members required")
        if self.method not in ("ridge", "equal_direction") or self.target not in TARGETS:
            raise ValueError("unknown combination method or target")
        if self.update_rule not in UPDATE_RULES:
            raise ValueError("unknown update rule")
        if self.method == "equal_direction" and self.update_rule != "fixed":
            raise ValueError("fixed direction equal weights have only one fixed numerical scheme")
        if not np.isfinite(self.ridge_lambda) or self.ridge_lambda <= 0:
            raise ValueError("positive finite Ridge lambda required")
        first, last = pd.Timestamp(self.fit_start), pd.Timestamp(self.fit_end)
        if first != first.normalize() or last != last.normalize() or first > last or last.year > 2024:
            raise ValueError("invalid authorized training window")
        candidates = tuple(sorted(self.candidate_ids or members))
        if not set(members).issubset(candidates):
            raise ValueError("members must belong to declared candidate universe")
        if not self.selection_rule:
            raise ValueError("membership selection rule must be declared")
        object.__setattr__(self, "feature_ids", members)
        object.__setattr__(self, "candidate_ids", candidates)

    @property
    def combination_id(self):
        payload = asdict(self)
        # Names and selection descriptions cannot make identical models distinct.
        for key in ("name", "selection_rule", "candidate_ids"):
            payload.pop(key)
        if self.method == "equal_direction":
            payload.pop("ridge_lambda")
            payload.pop("target")
        return "combo_" + digest(payload)[:24]

    @property
    def entity_id(self):
        return self.combination_id

    def to_dict(self):
        return clean({**asdict(self), "combination_id": self.combination_id,
            "preprocessing": PREPROCESSING, "membership_updates": False,
            "target_weighting": "equal_date_then_equal_stock_within_common_sample",
            "prediction_intercept": 0.0})

    @classmethod
    def from_dict(cls, value):
        keys = cls.__dataclass_fields__
        return cls(**{k: v for k, v in value.items() if k in keys})


class _DailyStatistics:
    """One pass per common member set; targets and lambdas share X moments."""
    def __init__(self, context, keys):
        self.ctx, self.keys = context, tuple(keys)
        self.p = len(keys)
        self.width = 1 + self.p + self.p**2 + 2 * self.p + 2
        self.values = np.zeros((len(context.dates), self.width), dtype=np.float64)
        self.ready = np.zeros(len(context.dates), dtype=bool)
        self.processed_days = 0

    def ensure(self, positions, mask, labels):
        arrays = [self.ctx.ranked(key) for key in self.keys]
        for local, position in enumerate(positions):
            if self.ready[position] or not mask[local].any():
                continue
            common = mask[local]
            x = np.column_stack([a[position, common] for a in arrays])
            y = labels[local, common]
            raw = y - y.mean()
            ranked = pd.Series(y).rank(method="average", pct=True).to_numpy()
            ranked -= ranked.mean()
            n = len(y)
            self.values[position] = np.concatenate(([1.], x.mean(axis=0),
                (x.T @ x / n).ravel(), x.T @ raw / n, x.T @ ranked / n,
                [np.mean(raw**2), np.mean(ranked**2)]))
            self.ready[position] = True
            self.processed_days += 1


class CombinationEvaluator:
    def __init__(self, context, cache_dir=None, *, max_stat_bytes=256 * 1024**2):
        self.ctx = context
        self.cache = NumericCache(cache_dir) if cache_dir else context.cache
        self.groups = OrderedDict()
        self.max_stat_bytes = max_stat_bytes
        self.statistic_day_computations = 0
        self.model_cache = OrderedDict()

    def _window(self, keys, first, last):
        ctx = self.ctx
        dates, safe, labels, purge = ctx.evaluator._scope(first, last)
        if not len(dates):
            raise ValueError("empty training interval")
        positions = ctx.dates.get_indexer(dates)
        common = ctx.pool.loc[dates].to_numpy().copy() & np.isfinite(labels.to_numpy())
        for key in keys:
            common &= np.isfinite(ctx.ranked(key)[positions])
        common &= (common.sum(axis=1) >= ctx.minimum)[:, None]
        days, cells = int(common.any(axis=1).sum()), int(common.sum())
        mask_id = mask_identity(pd.DataFrame(common, index=dates, columns=ctx.columns))
        support = {"train_days": days, "train_cells": cells, "sample_mask_id": mask_id,
            "annual_endpoint_purge": purge,
            "last_safe_signal_date": str(dates[safe.to_numpy()][-1].date()) if safe.any() else None}
        if safe.any():
            endpoint = ctx.dates[ctx.dates.get_loc(dates[safe.to_numpy()][-1]) + 6]
            support["last_label_endpoint"] = str(endpoint.date())
        else:
            support["last_label_endpoint"] = None
        if days < ctx.min_train_days or cells < ctx.min_train_days * ctx.minimum:
            raise ValueError("insufficient common training days/cells")
        cache_key = {**ctx.identity, "kind": "equal_date_sufficient_statistics_v1",
            "common_feature_ids": list(keys), "fit_start": str(pd.Timestamp(first).date()),
            "fit_end": str(pd.Timestamp(last).date()), "sample_mask_id": mask_id,
            "targets": list(TARGETS)}
        saved = self.cache.read_record(cache_key)
        if saved is None:
            group_key = tuple(keys)
            if group_key not in self.groups:
                group = _DailyStatistics(ctx, keys)
                while self.groups and sum(v.values.nbytes for v in self.groups.values()) + group.values.nbytes > self.max_stat_bytes:
                    self.groups.popitem(last=False)
                self.groups[group_key] = group
            self.groups.move_to_end(group_key)
            group = self.groups[group_key]
            before = group.processed_days
            group.ensure(positions, common, labels.to_numpy())
            self.statistic_day_computations += group.processed_days - before
            # Exclude days absent in this fit (annual and current boundary purge).
            totals = group.values[positions[common.any(axis=1)]].sum(axis=0) / days
            saved = {"moments": totals.tolist(), "support": support}
            self.cache.write_record(cache_key, saved)
        return np.asarray(saved["moments"]), support

    def _directions(self, spec):
        from quanta_agents.factor_research.evaluation import pearson_ic
        ctx = self.ctx
        dates, _, labels, _ = ctx.evaluator._scope(spec.fit_start, spec.fit_end)
        result = []
        for key in spec.feature_ids:
            values, _ = pearson_ic(ctx.compute(key).loc[dates], labels,
                                   ctx.pool.loc[dates], ctx.minimum)
            if values.count() < ctx.min_train_days or not np.isfinite(values.mean()) or values.mean() == 0:
                raise ValueError("unknown training direction for " + key)
            result.append(int(np.sign(values.mean())))
        return result

    def _fit(self, spec, first, last, common_keys):
        if pd.Timestamp(last).year > 2024:
            raise ValueError("2025 training is unauthorized")
        cache_id = digest({**self.ctx.identity, "spec": spec.to_dict(), "first": first,
                           "last": last, "common_keys": list(common_keys)})
        if cache_id in self.model_cache:
            self.model_cache.move_to_end(cache_id)
            return deepcopy(self.model_cache[cache_id])
        p = len(common_keys)
        moments, support = self._window(common_keys, first, last)
        indices = [common_keys.index(key) for key in spec.feature_ids]
        mean = moments[1:1+p][indices]
        second = moments[1+p:1+p+p*p].reshape(p, p)[np.ix_(indices, indices)]
        covariance = second - np.outer(mean, mean)
        scales = np.sqrt(np.maximum(np.diag(covariance), 0.))
        if spec.method == "equal_direction":
            signs = self._directions(spec)
            coefficients = np.asarray(signs, dtype=float) / len(signs)
            mean, scales = np.zeros(len(signs)), np.ones(len(signs))
        else:
            signs = None
            if (scales <= 1e-12).any():
                raise ValueError("constant training feature")
            target_index = TARGETS.index(spec.target)
            if moments[-2 + target_index] <= 1e-24:
                raise ValueError("constant training target")
            begin = 1+p+p*p + target_index*p
            rhs = moments[begin:begin+p][indices] / scales
            gram = covariance / np.outer(scales, scales)
            coefficients = np.linalg.solve(gram + spec.ridge_lambda * np.eye(len(indices)), rhs)
            if not np.isfinite(coefficients).all():
                raise ValueError("nonfinite fitted coefficients")
        result = clean({"status": "fitted", "method": spec.method, "target": spec.target,
            "ridge_lambda": spec.ridge_lambda if spec.method == "ridge" else None,
            "feature_order": list(spec.feature_ids), "common_feature_ids": list(common_keys),
            "feature_means": mean.tolist(), "feature_scales": scales.tolist(),
            "coefficients": coefficients.tolist(), "intercept": 0., "directions": signs,
            "fit_start": first, "fit_end": last, "train_start": first, "train_end": last,
            "direction_fit_start": spec.fit_start, "direction_fit_end": spec.fit_end,
            "preprocessing": PREPROCESSING,
            "target_weighting": "equal_date_then_equal_stock_within_common_sample", **support})
        self.model_cache[cache_id] = result
        while len(self.model_cache) > 2048:
            self.model_cache.popitem(last=False)
        return deepcopy(result)

    def _intervals(self, spec, start, end):
        first, last = pd.Timestamp(start), pd.Timestamp(end)
        if first > last or last.year > 2024 or first <= pd.Timestamp(spec.fit_end):
            raise ValueError("prediction must follow declared initial fit and end before 2025")
        # Also validate frozen calendar completeness for the requested evaluation.
        dates, _, _, _ = self.ctx.evaluator._scope(start, end)
        if not len(dates):
            raise ValueError("empty prediction interval")
        if spec.update_rule == "fixed":
            return [(str(dates[0].date()), str(dates[-1].date()), spec.fit_start, spec.fit_end)]
        result = []
        for period in dates.to_period("Q").unique():
            part = dates[dates.to_period("Q") == period]
            rebalance = period.start_time
            cutoff = rebalance - pd.Timedelta(days=1)
            lower = pd.Timestamp(spec.fit_start)
            if spec.update_rule == "quarterly_rolling3y":
                lower = max(lower, rebalance - pd.DateOffset(years=3))
            result.append((str(part[0].date()), str(part[-1].date()),
                           str(lower.date()), str(cutoff.date())))
        return result

    def _predict(self, spec, *, start, end, common_keys=None):
        ctx = self.ctx
        for key in spec.feature_ids:
            ctx.resolve(key)
        common_keys = tuple(sorted(common_keys or spec.feature_ids))
        if not set(spec.feature_ids).issubset(common_keys):
            raise ValueError("paired common members must contain model members")
        prediction = np.full(ctx.pool.shape, np.nan, dtype=np.float64)
        models = []
        for pred_start, pred_end, first, last in self._intervals(spec, start, end):
            try:
                model = self._fit(spec, first, last, common_keys)
                positions = np.flatnonzero((ctx.dates >= pred_start) & (ctx.dates <= pred_end))
                valid = ctx.pool.iloc[positions].to_numpy().copy()
                for key in common_keys:
                    valid &= np.isfinite(ctx.ranked(key)[positions])
                values = np.zeros(valid.shape)
                for key, mean, scale, beta in zip(model["feature_order"], model["feature_means"],
                        model["feature_scales"], model["coefficients"]):
                    values += (ctx.ranked(key)[positions] - mean) / scale * beta
                prediction[positions] = np.where(valid, values, np.nan)
            except (ValueError, np.linalg.LinAlgError) as exc:
                model = {"status": "unavailable", "reason": str(exc), "fit_start": first,
                         "fit_end": last, "feature_order": list(spec.feature_ids),
                         "common_feature_ids": list(common_keys)}
            model.update(predict_start=pred_start, predict_end=pred_end)
            models.append(model)
        return pd.DataFrame(prediction, index=ctx.dates, columns=ctx.columns), models

    def evaluate(self, spec, *, start, end):
        started = time.perf_counter()
        prediction, models = self._predict(spec, start=start, end=end)
        report = self.ctx.evaluate_prediction(spec.combination_id, prediction, start=start, end=end)
        report.update(spec=spec.to_dict(), models_by_interval=models,
                      combination_id=spec.combination_id, training_target=spec.target)
        if any(m["status"] != "fitted" for m in models):
            report["status"] = "partial_fit_unavailable" if any(m["status"] == "fitted" for m in models) else "fit_unavailable"
        report["resources"].update(seconds=time.perf_counter()-started,
            numeric_cache=self.cache.info, statistic_day_computations=self.statistic_day_computations)
        return clean(report), prediction

    def paired_increment(self, base_spec, augmented_spec, *, start, end, fit_start=None, fit_end=None):
        if fit_start is not None or fit_end is not None:
            changes = {"update_rule": "fixed"}
            if fit_start is not None:
                changes["fit_start"] = fit_start
            if fit_end is not None:
                changes["fit_end"] = fit_end
            base_spec, augmented_spec = replace(base_spec, **changes), replace(augmented_spec, **changes)
        settings = ("method", "target", "ridge_lambda", "update_rule", "fit_start", "fit_end")
        if any(getattr(base_spec, k) != getattr(augmented_spec, k) for k in settings):
            raise ValueError("paired models must use identical fitting settings")
        if not set(base_spec.feature_ids) < set(augmented_spec.feature_ids):
            raise ValueError("augmented members must strictly contain baseline members")
        ctx = self.ctx
        own, own_models = self._predict(base_spec, start=start, end=end)
        common, common_models = self._predict(base_spec, start=start, end=end,
                                              common_keys=augmented_spec.feature_ids)
        augmented, aug_models = self._predict(augmented_spec, start=start, end=end)
        dates, safe, labels, _ = ctx.evaluator._scope(start, end)
        mask = ctx.pool.loc[dates] & common.loc[dates].notna() & augmented.loc[dates].notna() & labels.notna()
        mask &= mask.sum(axis=1).ge(ctx.minimum).to_numpy()[:, None]
        daily = pd.DataFrame({"date": dates, "label_safe": safe.to_numpy()}, index=dates)
        for name, values, eligibility in (("baseline_original", own, ctx.pool.loc[dates]),
                ("baseline_common", common, mask), ("augmented", augmented, mask)):
            pi, n = pearson_ic(values.loc[dates], labels, eligibility, ctx.minimum)
            ri, _ = _rank_ic(values.loc[dates].where(eligibility), labels.where(eligibility), ctx.minimum)
            daily[name+"_pearson_ic"], daily[name+"_rank_ic"] = pi, ri
            daily[name+"_paired_count"] = n
        oi, _ = pearson_ic(own.loc[dates], labels, mask, ctx.minimum)
        daily["baseline_original_on_common_pearson_ic"] = oi
        daily["delta_pearson_ic"] = daily.augmented_pearson_ic - daily.baseline_common_pearson_ic
        daily["delta_rank_ic"] = daily.augmented_rank_ic - daily.baseline_common_rank_ic

        def summary(part):
            result = {"paired_delta_pearson_ic": part.delta_pearson_ic.mean(),
                "paired_delta_rank_ic": part.delta_rank_ic.mean(), "valid_days": int(part.delta_pearson_ic.count()),
                "interval": ctx.evaluator._ci(part.delta_pearson_ic),
                "rank_ic_interval": ctx.evaluator._ci(part.delta_rank_ic),
                "baseline_original_cells": int(part.baseline_original_paired_count.sum()),
                "common_cells": int(part.baseline_common_paired_count.sum())}
            for name in ("baseline_original", "baseline_common", "augmented"):
                result[name+"_pearson_ic"] = part[name+"_pearson_ic"].mean()
                result[name+"_rank_ic"] = part[name+"_rank_ic"].mean()
                result[name+"_pearson_ic_interval"] = ctx.evaluator._ci(part[name+"_pearson_ic"])
            result["baseline_original_on_common_pearson_ic"] = part.baseline_original_on_common_pearson_ic.mean()
            result["coverage_selection_delta_pearson_ic"] = (part.baseline_original_on_common_pearson_ic
                - part.baseline_original_pearson_ic).mean()
            result["common_refit_delta_pearson_ic"] = (part.baseline_common_pearson_ic
                - part.baseline_original_on_common_pearson_ic).mean()
            return clean(result)
        failed = any(m["status"] != "fitted" for group in (own_models, common_models, aug_models) for m in group)
        return clean({"entity_type": "predictor_comparison", "status": "fit_unavailable" if failed else "evaluated",
            "baseline_id": base_spec.combination_id, "augmented_id": augmented_spec.combination_id,
            "target": base_spec.target, "evaluation_target": "raw_five_session_return",
            "sample_mask_id": mask_identity(mask), **summary(daily), "summary": summary(daily),
            "annual": [{"year": int(year), **summary(part)} for year, part in daily.groupby(dates.year)],
            "daily": daily.to_dict(orient="records"), "models": {"baseline_original": own_models,
                "baseline_common": common_models, "augmented": aug_models},
            "semantics": "Bown_and_Bcommon_and_Bplusf_common; identical_common_fit_and_prediction_masks; rawPearson_delta",
            "uncertainty": {"search_correction": False, "overlapping_labels": True}})


def replay_predictions(context, models_by_interval):
    """Rebuild published predictions from immutable interval coefficients."""
    values = np.full(context.pool.shape, np.nan)
    occupied = np.zeros(len(context.dates), dtype=bool)
    for model in models_by_interval:
        positions = np.flatnonzero((context.dates >= model["predict_start"]) & (context.dates <= model["predict_end"]))
        if occupied[positions].any():
            raise ValueError("overlapping prediction intervals")
        occupied[positions] = True
        if model["status"] != "fitted":
            continue
        if pd.Timestamp(model["fit_end"]) >= pd.Timestamp(model["predict_start"]):
            raise ValueError("noncausal replay interval")
        valid = context.pool.iloc[positions].to_numpy().copy()
        for key in model["common_feature_ids"]:
            valid &= np.isfinite(context.ranked(key)[positions])
        prediction = np.zeros(valid.shape)
        for key, mean, scale, beta in zip(model["feature_order"], model["feature_means"],
                model["feature_scales"], model["coefficients"], strict=True):
            prediction += (context.ranked(key)[positions] - mean) / scale * beta
        values[positions] = np.where(valid, prediction + model["intercept"], np.nan)
    return pd.DataFrame(values, index=context.dates, columns=context.columns)
