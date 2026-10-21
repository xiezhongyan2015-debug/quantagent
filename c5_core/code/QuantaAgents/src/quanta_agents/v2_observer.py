from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any


_CATEGORY_ALIASES = {
    "numerical_definition": {
        "numerical_definition",
        "numeric_definition",
        "parameter_definition",
        "parameter_value",
        "parameter_scan",
        "threshold_scan",
        "window_scan",
        "condition_shape",
        "numeric_shape",
        "数值与定义",
        "数值",
        "参数",
        "定义",
    },
    "mechanism_signal": {
        "mechanism_signal",
        "mechanism_test",
        "new_signal",
        "added_signal",
        "explanatory_signal",
        "causal_signal",
        "mechanism",
        "现象解释",
        "新增信号",
    },
    "applicability": {
        "applicability",
        "scope",
        "universe",
        "environment",
        "market_regime",
        "universe_or_regime",
        "regime",
        "timing",
        "适用对象",
        "适用环境",
        "股票范围",
        "市场环境",
    },
    "rule_ablation": {
        "rule_ablation",
        "ablation",
        "simplification",
        "remove_rule",
        "simplification_ablation",
        "condition_necessity",
        "remove_condition",
        "删除规则",
        "简化",
    },
    "holding_period": {
        "holding_period",
        "horizon",
        "exit",
        "execution",
        "execution_horizon",
        "trade_timing",
        "entry_or_exit",
        "持有期",
        "退出",
    },
}

_TESTED_STATUSES = {"tested", "completed", "adopted", "rejected", "failed"}
_PLANNED_STATUSES = {"planned", "proposed", "queued", "untested"}


def _finite_number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _nonempty(value: object) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, Mapping):
        return bool(value)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return bool(value)
    return value is not None


def _category(record: Mapping[str, object]) -> str:
    raw_values: list[object] = []
    for key in ("research_category", "category", "change_kind", "test_type"):
        raw = record.get(key)
        if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes)):
            raw_values.extend(raw)
        else:
            raw_values.append(raw)
    normalized_values = {
        str(value or "").strip().lower() for value in raw_values if value is not None
    }
    for canonical, aliases in _CATEGORY_ALIASES.items():
        if normalized_values & aliases:
            return canonical
    return ""


def _record_id(record: Mapping[str, object], index: int) -> str:
    for key in ("candidate_id", "trial_id", "id", "direction_id"):
        value = str(record.get(key, "")).strip()
        if value:
            return value
    return f"record_{index + 1:03d}"


def _is_formally_tested(record: Mapping[str, object]) -> bool:
    status = str(record.get("status", "")).strip().lower()
    if status in _PLANNED_STATUSES:
        return False
    for key in ("development_report", "test_report", "report", "result"):
        value = record.get(key)
        if isinstance(value, Mapping) and bool(value):
            return True
    return status in _TESTED_STATUSES


def _records_from(run_record: Mapping[str, object], keys: Sequence[str]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for key in keys:
        raw = run_record.get(key)
        if isinstance(raw, Mapping):
            records.append(dict(raw))
        elif isinstance(raw, Sequence) and not isinstance(raw, (str, bytes)):
            records.extend(dict(item) for item in raw if isinstance(item, Mapping))
    return records


def _classified_records(
    records: Sequence[Mapping[str, object]],
    category: str,
    *,
    tested_only: bool,
) -> list[str]:
    matched: list[str] = []
    for index, record in enumerate(records):
        if _category(record) != category:
            continue
        if tested_only and not _is_formally_tested(record):
            continue
        matched.append(_record_id(record, index))
    return matched


def _valid_mechanisms(run_record: Mapping[str, object]) -> list[dict[str, Any]]:
    raw_records = _records_from(
        run_record,
        (
            "mechanism_hypotheses",
            "theory_records",
            "theory_cards",
            "competing_explanations",
        ),
    )
    valid: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for index, record in enumerate(raw_records):
        mechanism_id = str(
            record.get("mechanism_id", record.get("theory_id", record.get("id", "")))
        ).strip()
        if not mechanism_id:
            mechanism_id = f"mechanism_{index + 1:03d}"
        prediction = record.get(
            "testable_prediction",
            record.get(
                "prediction",
                record.get("expected_results", record.get("extra_predictions")),
            ),
        )
        if mechanism_id in seen_ids or not _nonempty(prediction):
            continue
        seen_ids.add(mechanism_id)
        normalized = dict(record)
        normalized["mechanism_id"] = mechanism_id
        valid.append(normalized)
    return valid


def _mechanism_signal_trial_ids(records: Sequence[Mapping[str, object]]) -> list[str]:
    matched: list[str] = []
    for index, record in enumerate(records):
        if _category(record) != "mechanism_signal" or not _is_formally_tested(record):
            continue
        linked_mechanism = record.get(
            "mechanism_id",
            record.get("linked_mechanism_ids", record.get("theory_id")),
        )
        new_signal = record.get(
            "new_signal",
            record.get("signal", record.get("change_spec")),
        )
        comparison = record.get("mechanism_comparison_results")
        comparison_complete = bool(
            isinstance(comparison, Mapping)
            and isinstance(comparison.get("added_to_base"), Mapping)
            and isinstance(comparison.get("signal_only"), Mapping)
            and isinstance(comparison["added_to_base"].get("report"), Mapping)
            and bool(comparison["added_to_base"]["report"])
            and isinstance(comparison["signal_only"].get("report"), Mapping)
            and bool(comparison["signal_only"]["report"])
        )
        if (
            _nonempty(linked_mechanism)
            and _nonempty(new_signal)
            and comparison_complete
        ):
            matched.append(_record_id(record, index))
    return matched


def _fold_count(report: Mapping[str, object]) -> int | None:
    value = _finite_number(report.get("fold_count"))
    if value is not None and value >= 0 and value.is_integer():
        return int(value)
    folds = report.get("folds")
    return len(folds) if isinstance(folds, list) else None


def _selected_development_report(
    run_record: Mapping[str, object],
    records: Sequence[Mapping[str, object]],
) -> tuple[dict[str, Any] | None, str]:
    for key in ("selected_development_report", "development_report"):
        report = run_record.get(key)
        if isinstance(report, Mapping) and report:
            return dict(report), str(run_record.get("selected_candidate_id", ""))

    selected_id = str(run_record.get("selected_candidate_id", "")).strip()
    for index, record in enumerate(records):
        candidate_id = _record_id(record, index)
        if selected_id and candidate_id != selected_id:
            continue
        judge = record.get("judge_result")
        adopted = record.get("adopted") is True or (
            isinstance(judge, Mapping) and judge.get("adopted") is True
        )
        if not selected_id and not adopted:
            continue
        for key in ("development_report", "test_report", "report"):
            report = record.get(key)
            if isinstance(report, Mapping) and report:
                return dict(report), candidate_id
    return None, selected_id


def _development_stability(
    report: Mapping[str, object] | None,
    *,
    require_cost_stress: bool,
    require_delay_stress: bool,
) -> tuple[bool, list[str]]:
    if not report:
        return False, ["没有选中版本的开发期报告"]

    reasons: list[str] = []
    if report.get("passed") is not True:
        reasons.append("完整开发期报告没有通过")
    count = _fold_count(report)
    if count is None or count < 3:
        reasons.append("开发期分段数量不足")
    worst_sharpe = _finite_number(report.get("worst_fold_sharpe"))
    if worst_sharpe is None or worst_sharpe < 0:
        reasons.append("最差一段的夏普为负或缺失")
    median_return = _finite_number(report.get("median_annual_return"))
    if median_return is None or median_return <= 0:
        reasons.append("年化收益中位数不为正或缺失")
    trades = _finite_number(report.get("total_trade_count"))
    if trades is None or trades <= 0:
        reasons.append("交易数量为零或缺失")
    if report.get("beats_cash_in_all_folds") is not True:
        reasons.append("并非每个开发期分段都跑赢空仓")
    if require_cost_stress and (
        report.get("cost_stress_complete") is not True
        or report.get("cost_stress_passed") is not True
    ):
        reasons.append("费用加倍检查不完整或未通过")
    if require_delay_stress and (
        report.get("delay_stress_complete") is not True
        or report.get("delay_stress_passed") is not True
    ):
        reasons.append("推迟一日检查不完整或未通过")
    if report.get("calculation_valid") is False:
        reasons.append("计算没有通过固定检查")
    return not reasons, reasons


def _final_report(
    run_record: Mapping[str, object],
    records: Sequence[Mapping[str, object]],
) -> dict[str, Any] | None:
    for key in ("final_test_report", "final_report"):
        report = run_record.get(key)
        if isinstance(report, Mapping) and report:
            return dict(report)
    for record in reversed(records):
        if str(record.get("period_kind", "")).strip() != "final_test":
            continue
        report = record.get("result")
        if isinstance(report, Mapping) and report:
            return dict(report)
    return None


def _final_assessment(report: Mapping[str, object] | None) -> dict[str, Any]:
    if not report:
        return {
            "status": "not_run",
            "passed": False,
            "metrics": {},
            "reasons": ["没有最终期报告"],
        }
    annual_return = _finite_number(report.get("annual_return"))
    sharpe = _finite_number(report.get("sharpe"))
    trades = _finite_number(report.get("trade_count"))
    reasons: list[str] = []
    if report.get("passed") is not True:
        reasons.append("最终期报告没有通过")
    if report.get("meets_v2_stability_requirements") is False:
        reasons.append("最终期没有达到V2事先设定的收益和风险要求")
    if annual_return is None or annual_return <= 0:
        reasons.append("最终期年化收益不为正或缺失")
    if sharpe is None or sharpe <= 0:
        reasons.append("最终期夏普不为正或缺失")
    if trades is None or trades <= 0:
        reasons.append("最终期交易数量为零或缺失")
    return {
        "status": "passed" if not reasons else "failed",
        "passed": not reasons,
        "metrics": {
            "annual_return": annual_return,
            "sharpe": sharpe,
            "max_ddpercent": _finite_number(report.get("max_ddpercent")),
            "trade_count": trades,
        },
        "reasons": reasons,
    }


def audit_v2_loop(run_record: Mapping[str, object]) -> dict[str, Any]:
    """只依据实际研究记录回答旁观审查问题。"""

    formal_records = _records_from(
        run_record,
        ("candidate_records", "formal_trials", "research_trials", "experiment_records"),
    )
    planned_records = _records_from(
        run_record,
        ("planned_candidates", "candidate_directions", "untested_plans", "research_plan"),
    )
    all_records = formal_records + planned_records

    numerical_plans = _classified_records(
        all_records, "numerical_definition", tested_only=False
    )
    numerical_tests = _classified_records(
        formal_records, "numerical_definition", tested_only=True
    )
    mechanism_records = _valid_mechanisms(run_record)
    signal_tests = _mechanism_signal_trial_ids(formal_records)
    applicability_plans = _classified_records(
        all_records, "applicability", tested_only=False
    )
    applicability_tests = _classified_records(
        formal_records, "applicability", tested_only=True
    )
    ablation_plans = _classified_records(
        all_records, "rule_ablation", tested_only=False
    )
    ablation_tests = _classified_records(
        formal_records, "rule_ablation", tested_only=True
    )
    holding_plans = _classified_records(
        all_records, "holding_period", tested_only=False
    )
    holding_tests = _classified_records(
        formal_records, "holding_period", tested_only=True
    )

    requirements = run_record.get("observer_requirements")
    requirements = requirements if isinstance(requirements, Mapping) else {}
    require_cost = requirements.get("require_cost_stress", True) is not False
    require_delay = requirements.get("require_delay_stress", True) is not False
    development_report, selected_id = _selected_development_report(
        run_record, formal_records
    )
    development_stable, development_reasons = _development_stability(
        development_report,
        require_cost_stress=require_cost,
        require_delay_stress=require_delay,
    )
    if development_stable:
        minimums = {
            "median_sharpe": requirements.get("confirmation_min_median_sharpe"),
            "median_annual_return": requirements.get(
                "confirmation_min_median_annual_return"
            ),
            "worst_fold_sharpe": requirements.get(
                "confirmation_min_worst_fold_sharpe"
            ),
            "total_trade_count": requirements.get(
                "confirmation_min_total_trade_count"
            ),
            "cost_stress_median_sharpe": requirements.get(
                "confirmation_min_cost_stress_sharpe"
            ),
            "delay_stress_median_sharpe": requirements.get(
                "confirmation_min_delay_stress_sharpe"
            ),
        }
        for key, raw_minimum in minimums.items():
            minimum = _finite_number(raw_minimum)
            actual = _finite_number(development_report.get(key)) if development_report else None
            if minimum is not None and (actual is None or actual < minimum):
                development_reasons.append(f"{key}没有达到事先要求")
        max_drawdown = _finite_number(
            requirements.get("confirmation_max_worst_fold_drawdown")
        )
        actual_drawdown = (
            _finite_number(development_report.get("worst_fold_drawdown"))
            if development_report
            else None
        )
        if max_drawdown is not None and (
            actual_drawdown is None or abs(actual_drawdown) > max_drawdown
        ):
            development_reasons.append("最差分段回撤超过事先要求")
        development_stable = not development_reasons
    final = _final_assessment(_final_report(run_record, formal_records))

    numeric_check = {
        "aware": bool(numerical_plans),
        "formally_tested": bool(numerical_tests),
        "planned_or_tested_record_ids": numerical_plans,
        "tested_record_ids": numerical_tests,
    }
    mechanism_check = {
        "competing_mechanisms_recorded": len(mechanism_records) >= 2,
        "mechanism_count": len(mechanism_records),
        "mechanism_ids": [record["mechanism_id"] for record in mechanism_records],
        "mechanism_linked_new_signal_tested": bool(signal_tests),
        "signal_test_record_ids": signal_tests,
    }
    applicability_check = {
        "aware": bool(applicability_plans),
        "formally_tested": bool(applicability_tests),
        "planned_or_tested_record_ids": applicability_plans,
        "tested_record_ids": applicability_tests,
    }
    ablation_check = {
        "aware": bool(ablation_plans),
        "formally_tested": bool(ablation_tests),
        "planned_or_tested_record_ids": ablation_plans,
        "tested_record_ids": ablation_tests,
    }
    holding_check = {
        "aware": bool(holding_plans),
        "formally_tested": bool(holding_tests),
        "planned_or_tested_record_ids": holding_plans,
        "tested_record_ids": holding_tests,
    }

    research_breadth_passed = bool(
        numeric_check["formally_tested"]
        and mechanism_check["competing_mechanisms_recorded"]
        and mechanism_check["mechanism_linked_new_signal_tested"]
        and applicability_check["formally_tested"]
        and (ablation_check["formally_tested"] or holding_check["formally_tested"])
    )
    final_run = final["status"] != "not_run"
    return {
        "schema_version": "v2_observer_audit",
        "record_counts": {
            "formal_trials": len(formal_records),
            "planned_items": len(planned_records),
        },
        "questions": {
            "number_and_definition": numeric_check,
            "mechanism_and_new_signal": mechanism_check,
            "applicability_or_environment": applicability_check,
            "stable_strategy": {
                "selected_candidate_id": selected_id,
                "development_stable": development_stable,
                "development_reasons": development_reasons,
                "final_test": final,
                "stable_in_development_and_final": bool(
                    development_stable and final["passed"]
                ),
            },
        },
        "additional_checks": {
            "rule_ablation": ablation_check,
            "holding_period": holding_check,
        },
        "observer_conclusion": {
            "research_breadth_passed": research_breadth_passed,
            "development_strategy_found": development_stable,
            "final_test_run": final_run,
            "final_test_passed": bool(final["passed"]),
            "overall_passed": bool(
                research_breadth_passed and development_stable and final["passed"]
            ),
        },
    }
