from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime
import math
from statistics import NormalDist, median, pstdev
from typing import Iterable, Mapping, Sequence

import pandas as pd


@dataclass(frozen=True)
class EvaluationFold:
    """一次按时间向前推进的开发期测试。"""

    name: str
    train_start: str
    train_end: str
    test_start: str
    test_end: str
    period_kind: str = "development"
    seen_period: bool = True

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def _parse_date(value: object, field: str) -> pd.Timestamp:
    if isinstance(value, (datetime, date, pd.Timestamp)):
        parsed = pd.Timestamp(value)
    elif isinstance(value, str) and value.strip():
        parsed = pd.Timestamp(value.strip())
    else:
        raise ValueError(f"{field} is required")
    if pd.isna(parsed):
        raise ValueError(f"{field} is not a valid date")
    return parsed.normalize()


def _positive_int(value: object, default: int, *, maximum: int | None = None) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    parsed = max(1, parsed)
    if maximum is not None:
        parsed = min(parsed, maximum)
    return parsed


def _nonnegative_int(value: object, default: int, *, maximum: int | None = None) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    parsed = max(0, parsed)
    if maximum is not None:
        parsed = min(parsed, maximum)
    return parsed


def _boolean(value: object, default: bool) -> bool:
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
        return default
    if isinstance(value, bool):
        return value
    return default if value is None else bool(value)


def build_development_folds(
    experiment_spec: Mapping[str, object],
    trading_dates: Sequence[object] | None = None,
) -> list[EvaluationFold]:
    """只在验证时期内切分测试段，并让可用研究数据逐段向前增加。"""

    train_start = _parse_date(experiment_spec.get("train_start"), "train_start")
    original_train_end = _parse_date(experiment_spec.get("train_end"), "train_end")
    validate_start = _parse_date(experiment_spec.get("validate_start"), "validate_start")
    validate_end = _parse_date(experiment_spec.get("validate_end"), "validate_end")
    if not train_start <= original_train_end < validate_start <= validate_end:
        raise ValueError("train and validation dates must be ordered and non-overlapping")

    if trading_dates is not None and len(trading_dates) > 0:
        parsed_dates = pd.to_datetime(list(trading_dates), errors="coerce")
        business_dates = pd.DatetimeIndex(parsed_dates).dropna().normalize().unique().sort_values()
        business_dates = business_dates[
            (business_dates >= validate_start) & (business_dates <= validate_end)
        ]
    else:
        business_dates = pd.bdate_range(validate_start, validate_end)
    if business_dates.empty:
        raise ValueError("validation period contains no business dates")

    requested_folds = _positive_int(
        experiment_spec.get("development_folds", experiment_spec.get("development_fold_count")),
        3,
        maximum=12,
    )
    minimum_days = _positive_int(experiment_spec.get("min_development_fold_days"), 20)
    gap_days = _nonnegative_int(experiment_spec.get("gap_trading_days"), 10, maximum=252)
    if len(business_dates) <= gap_days:
        raise ValueError("validation period is shorter than the requested trading-day gap")
    test_pool = business_dates[gap_days:]
    possible_folds = max(1, len(test_pool) // minimum_days)
    fold_count = min(requested_folds, possible_folds, len(test_pool))

    base_size, extra = divmod(len(test_pool), fold_count)
    folds: list[EvaluationFold] = []
    cursor = 0
    for index in range(fold_count):
        size = base_size + (1 if index < extra else 0)
        test_dates = test_pool[cursor : cursor + size]
        cursor += size
        test_start = pd.Timestamp(test_dates[0]).normalize()
        test_end = pd.Timestamp(test_dates[-1]).normalize()

        test_start_position = int(business_dates.searchsorted(test_start, side="left"))
        if index == 0:
            train_end = original_train_end
        else:
            train_end_position = test_start_position - gap_days - 1
            if train_end_position < 0:
                raise ValueError("the requested gap leaves no earlier research date")
            train_end = pd.Timestamp(business_dates[train_end_position]).normalize()
        if train_end < train_start:
            raise ValueError(
                "the requested gap leaves no training data before the first development fold"
            )

        folds.append(
            EvaluationFold(
                name=f"development_fold_{index + 1:02d}",
                train_start=train_start.strftime("%Y-%m-%d"),
                train_end=pd.Timestamp(train_end).strftime("%Y-%m-%d"),
                test_start=test_start.strftime("%Y-%m-%d"),
                test_end=test_end.strftime("%Y-%m-%d"),
            )
        )
    return folds


def build_final_fold(experiment_spec: Mapping[str, object]) -> EvaluationFold:
    """构造一次性的最终时期；这段结果不能用于后续改策略。"""

    train_start = _parse_date(experiment_spec.get("train_start"), "train_start")
    validate_end = _parse_date(experiment_spec.get("validate_end"), "validate_end")
    backtest_start = _parse_date(experiment_spec.get("backtest_start"), "backtest_start")
    backtest_end = _parse_date(experiment_spec.get("backtest_end"), "backtest_end")
    if not train_start <= validate_end < backtest_start <= backtest_end:
        raise ValueError("final dates must be after the development dates")
    return EvaluationFold(
        name="final_test",
        train_start=train_start.strftime("%Y-%m-%d"),
        train_end=validate_end.strftime("%Y-%m-%d"),
        test_start=backtest_start.strftime("%Y-%m-%d"),
        test_end=backtest_end.strftime("%Y-%m-%d"),
        period_kind="final_test",
        seen_period=False,
    )


def daily_return_moments(stats: Mapping[str, object]) -> dict[str, float | int]:
    daily_df = stats.get("_daily_df")
    if not isinstance(daily_df, pd.DataFrame) or "return" not in daily_df.columns:
        return {"sample_size": 0, "skew": 0.0, "kurtosis": 3.0}
    returns = pd.to_numeric(daily_df["return"], errors="coerce").dropna().astype(float)
    if len(returns) < 3:
        return {"sample_size": int(len(returns)), "skew": 0.0, "kurtosis": 3.0}
    skew = float(returns.skew())
    kurtosis = float(returns.kurt()) + 3.0
    if not math.isfinite(skew):
        skew = 0.0
    if not math.isfinite(kurtosis) or kurtosis < 1.0:
        kurtosis = 3.0
    return {"sample_size": int(len(returns)), "skew": skew, "kurtosis": kurtosis}


def deflated_sharpe_probability(
    annualized_sharpe: float,
    prior_annualized_sharpes: Sequence[float],
    sample_size: int,
    *,
    skew: float = 0.0,
    kurtosis: float = 3.0,
    periods_per_year: int = 252,
) -> float | None:
    """估计多次尝试后，当前夏普高于偶然最佳值的概率。"""

    if sample_size < 3 or not math.isfinite(float(annualized_sharpe)):
        return None
    scale = math.sqrt(float(periods_per_year))
    observed = float(annualized_sharpe) / scale
    clean_history = [
        float(value) / scale
        for value in prior_annualized_sharpes
        if isinstance(value, (int, float)) and math.isfinite(float(value))
    ]
    trial_count = max(1, len(clean_history) + 1)
    if trial_count == 1:
        benchmark = 0.0
    else:
        trial_sigma = pstdev(clean_history + [observed])
        trial_sigma = max(trial_sigma, 1.0 / math.sqrt(float(sample_size)))
        normal = NormalDist()
        euler_gamma = 0.5772156649015329
        first_quantile = normal.inv_cdf(1.0 - 1.0 / trial_count)
        second_quantile = normal.inv_cdf(1.0 - 1.0 / (trial_count * math.e))
        expected_maximum = (
            (1.0 - euler_gamma) * first_quantile
            + euler_gamma * second_quantile
        )
        benchmark = trial_sigma * expected_maximum

    denominator_term = (
        1.0
        - float(skew) * observed
        + ((float(kurtosis) - 1.0) / 4.0) * observed * observed
    )
    if denominator_term <= 0 or not math.isfinite(denominator_term):
        return None
    z_score = (
        (observed - benchmark)
        * math.sqrt(float(sample_size - 1))
        / math.sqrt(denominator_term)
    )
    return float(NormalDist().cdf(z_score))


def _finite_values(records: Iterable[Mapping[str, object]], key: str) -> list[float]:
    values: list[float] = []
    for record in records:
        value = record.get(key)
        if isinstance(value, (int, float)) and math.isfinite(float(value)):
            values.append(float(value))
    return values


def summarize_development_results(
    fold_records: Sequence[Mapping[str, object]],
    experiment_spec: Mapping[str, object],
    *,
    cost_stress_records: Sequence[Mapping[str, object]] | None = None,
    delay_stress_records: Sequence[Mapping[str, object]] | None = None,
    prior_candidate_sharpes: Sequence[float] = (),
) -> dict[str, object]:
    """用固定规则合并开发期结果，模型不能改写这里的通过条件。"""

    if not fold_records:
        raise ValueError("at least one development fold result is required")
    sharpes = _finite_values(fold_records, "sharpe")
    annual_returns = _finite_values(fold_records, "annual_return")
    drawdowns = _finite_values(fold_records, "max_ddpercent")
    if len(sharpes) != len(fold_records) or len(annual_returns) != len(fold_records):
        raise ValueError("every development fold must have finite return and Sharpe metrics")

    evaluation = experiment_spec.get("evaluation")
    rules = evaluation if isinstance(evaluation, Mapping) else {}
    try:
        minimum_pass_ratio = float(
            experiment_spec.get(
                "min_fold_pass_ratio",
                rules.get("min_fold_pass_ratio", 0.67),
            )
        )
    except (TypeError, ValueError):
        minimum_pass_ratio = 0.67
    minimum_pass_ratio = min(1.0, max(0.0, minimum_pass_ratio))
    passed_count = sum(record.get("passed") is True for record in fold_records)
    pass_ratio = passed_count / len(fold_records)

    try:
        minimum_worst_sharpe = float(
            experiment_spec.get(
                "min_worst_fold_sharpe",
                rules.get("min_worst_fold_sharpe", 0.0),
            )
        )
    except (TypeError, ValueError):
        minimum_worst_sharpe = 0.0
    worst_sharpe = min(sharpes)
    raw_require_cash = experiment_spec.get("require_all_folds_beat_cash", True)
    if isinstance(raw_require_cash, str):
        require_all_folds_beat_cash = raw_require_cash.strip().lower() not in {
            "0",
            "false",
            "no",
            "off",
        }
    else:
        require_all_folds_beat_cash = bool(raw_require_cash)
    beats_cash_in_all_folds = all(value > 0.0 for value in annual_returns)

    cost_records = list(cost_stress_records or [])
    delay_records = list(delay_stress_records or [])
    cost_sharpes = _finite_values(cost_records, "sharpe")
    delay_sharpes = _finite_values(delay_records, "sharpe")
    try:
        minimum_cost_sharpe = float(experiment_spec.get("min_cost_stress_sharpe", 0.0))
    except (TypeError, ValueError):
        minimum_cost_sharpe = 0.0
    try:
        minimum_delay_sharpe = float(experiment_spec.get("min_delay_stress_sharpe", 0.0))
    except (TypeError, ValueError):
        minimum_delay_sharpe = 0.0
    cost_stress_required = _boolean(experiment_spec.get("run_cost_stress"), True)
    delay_stress_required = _boolean(experiment_spec.get("run_delay_stress"), True)
    cost_stress_complete = (
        not cost_stress_required
        or (
            len(cost_records) == len(fold_records)
            and len(cost_sharpes) == len(fold_records)
        )
    )
    delay_stress_complete = (
        not delay_stress_required
        or (
            len(delay_records) == len(fold_records)
            and len(delay_sharpes) == len(fold_records)
        )
    )
    cost_stress_passed = (
        not cost_stress_required
        or (
            cost_stress_complete
            and median(cost_sharpes) >= minimum_cost_sharpe
        )
    )
    delay_stress_passed = (
        not delay_stress_required
        or (
            delay_stress_complete
            and median(delay_sharpes) >= minimum_delay_sharpe
        )
    )

    total_sample_size = sum(
        int(record.get("sample_size", 0))
        for record in fold_records
        if isinstance(record.get("sample_size", 0), (int, float))
    )
    mean_skew = sum(float(record.get("skew", 0.0)) for record in fold_records) / len(fold_records)
    mean_kurtosis = sum(float(record.get("kurtosis", 3.0)) for record in fold_records) / len(fold_records)
    adjusted_probability = deflated_sharpe_probability(
        median(sharpes),
        prior_candidate_sharpes,
        total_sample_size,
        skew=mean_skew,
        kurtosis=mean_kurtosis,
    )
    minimum_trials = _positive_int(experiment_spec.get("min_trials_for_deflated_sharpe"), 8)
    try:
        minimum_adjusted_probability = float(
            experiment_spec.get("min_deflated_sharpe_probability", 0.95)
        )
    except (TypeError, ValueError):
        minimum_adjusted_probability = 0.95
    adjusted_check_active = len(prior_candidate_sharpes) + 1 >= minimum_trials
    adjusted_passed = (
        not adjusted_check_active
        or (
            adjusted_probability is not None
            and adjusted_probability >= minimum_adjusted_probability
        )
    )

    passed = (
        pass_ratio >= minimum_pass_ratio
        and worst_sharpe >= minimum_worst_sharpe
        and (not require_all_folds_beat_cash or beats_cash_in_all_folds)
        and cost_stress_passed
        and delay_stress_passed
        and adjusted_passed
    )
    reasons: list[str] = []
    if pass_ratio < minimum_pass_ratio:
        reasons.append(
            f"fold_pass_ratio={pass_ratio:.2f} is below {minimum_pass_ratio:.2f}"
        )
    if worst_sharpe < minimum_worst_sharpe:
        reasons.append(
            f"worst_fold_sharpe={worst_sharpe:.3f} is below {minimum_worst_sharpe:.3f}"
        )
    if require_all_folds_beat_cash and not beats_cash_in_all_folds:
        reasons.append("at least one development fold did not beat the cash comparison")
    if not cost_stress_passed:
        if not cost_stress_complete:
            reasons.append("double-cost test did not produce one result for every fold")
        else:
            reasons.append("double-cost result is below the required Sharpe")
    if not delay_stress_passed:
        if not delay_stress_complete:
            reasons.append("one-day-delay test did not produce one result for every fold")
        else:
            reasons.append("one-day-delay result is below the required Sharpe")
    if not adjusted_passed:
        reasons.append("trial-adjusted Sharpe probability is below the required level")

    return {
        "period_kind": "development",
        "seen_period": True,
        "fold_count": len(fold_records),
        "passed_fold_count": passed_count,
        "fold_pass_ratio": pass_ratio,
        "median_annual_return": median(annual_returns),
        "cash_comparison_annual_return": 0.0,
        "median_excess_return_over_cash": median(annual_returns),
        "beats_cash_in_all_folds": beats_cash_in_all_folds,
        "median_sharpe": median(sharpes),
        "worst_fold_sharpe": worst_sharpe,
        "worst_fold_drawdown": max(drawdowns) if drawdowns else None,
        "total_trade_count": sum(
            int(record.get("trade_count", 0))
            for record in fold_records
            if isinstance(record.get("trade_count", 0), (int, float))
        ),
        "cost_stress_median_sharpe": median(cost_sharpes) if cost_sharpes else None,
        "cost_stress_complete": cost_stress_complete,
        "cost_stress_passed": cost_stress_passed,
        "delay_stress_median_sharpe": median(delay_sharpes) if delay_sharpes else None,
        "delay_stress_complete": delay_stress_complete,
        "delay_stress_passed": delay_stress_passed,
        "trial_count": len(prior_candidate_sharpes) + 1,
        "deflated_sharpe_probability": adjusted_probability,
        "deflated_sharpe_check_active": adjusted_check_active,
        "passed": passed,
        "failure_reasons": reasons,
        "folds": [dict(record) for record in fold_records],
        "cost_stress_folds": [dict(record) for record in (cost_stress_records or [])],
        "delay_stress_folds": [dict(record) for record in (delay_stress_records or [])],
    }
