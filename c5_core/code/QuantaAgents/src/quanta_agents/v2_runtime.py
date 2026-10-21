from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


def default_cn_daily_v2_profile() -> dict[str, object]:
    """V2 的日线研究设置；这些是运行办法，不是用户观点。"""

    return {
        "profile_id": "cn_daily_research_v2",
        "research_design": {
            "train_start": "2015-01-05",
            "train_end": "2017-12-29",
            "research_start": "2018-01-02",
            "research_end": "2021-12-31",
            "research_folds": 4,
            "confirmation_start": "2022-01-04",
            "confirmation_end": "2024-12-31",
            "confirmation_folds": 3,
            "final_start": "2025-01-02",
            "final_end": "2025-12-31",
            "external_source_cutoff": "2017-12-29",
        },
        "data": {
            "table_key": "stock_kline_daily_qfq",
            "backtest_dataset": "vnpy_stock_daily_qfq",
            "available_fields": [
                "open",
                "high",
                "low",
                "close",
                "volume",
                "amount",
                "float_shares",
                "gu_1m",
                "gd_1m",
                "rbar_up17",
                "rbar_down17",
                "r_0931_1000",
                "r_1001_1030",
                "overnight_return",
            ],
        },
        "universe": {
            "description": "使用本地日线资料中当时有记录的中国股票；这是运行范围，不是用户条件",
            "required_data": {
                "type": "dataset_defined",
                "value": "stock_kline_daily_qfq",
            },
            "experiment_entry": {
                "symbols": [],
                "asset": "中国股票",
                "description": "股票由本地日线资料中的code决定",
                "type": "dataset_defined",
                "dataset": "stock_kline_daily_qfq",
            },
        },
        "execution": {
            "signal_to_trade": "信号日收盘确认，下一实际交易日开盘执行",
            "default_holding_days": 5,
            "exit_execution": "买入日记为T，完整持有5个实际交易日后，在T+5开盘卖出",
            "missing_bar_policy": (
                "某股票当日缺少K线时不成交；已有计划持仓目标继续到原退出日，"
                "不得因缺K清零；恢复K线后按当日仍有效的计划执行；"
                "未买到不顺延原计划退出日；不得读取或推断下一交易日是否有K线"
            ),
            "repeat_signal": "持有期间忽略，不加仓也不延长",
            "single_stock_weight": 0.10,
            "buy_cost": 0.0003,
            "sell_cost_before_change": 0.0013,
            "sell_cost_change_date": "2023-08-28",
            "sell_cost": 0.0008,
            "cost_rule": (
                "买入费0.0003；2018-01-01至2023-08-27卖出费0.0013；"
                "2023-08-28起卖出费0.0008"
            ),
            "slippage": 0.00025,
            "lot_size": 0,
            "capital": 1_000_000.0,
        },
        "baseline_defaults": {
            "consolidation_measure": "前30根日K线的29个简单收盘收益率样本标准差(ddof=1)",
            "consolidation_volatility_limit": 0.015,
            "reference_volume_window": "两次放量前的30根日K线，排除两个放量日",
            "box_interpretation": "单根K线实体绝对值abs(close-open)",
            "kline_length": "单根K线最高价减最低价high-low",
            "apply_box_relation_to": "两个放量日都检查",
            "box_to_kline_max_ratio": 0.5,
            "consecutive_high_volume_days": 2,
            "holding_days": 5,
            "position_rule": (
                "旧计划比例保持到各自退出；当日新计划少于10只且现金足够时每只10%，"
                "否则只在当日新计划之间平均分配剩余现金"
            ),
            "position_plan_contract": {
                "mode": "fixed_target_until_exit",
                "single_stock_weight": 0.10,
                "position_rule": (
                    "旧计划比例保持到各自退出；当日新计划少于10只且现金足够时每只10%，"
                    "否则只在当日新计划之间平均分配剩余现金"
                ),
            },
            "market_day_continuity_contract": {
                "mode": "consecutive_market_trading_days",
                "consecutive_days": 2,
                "condition_field": "consecutive_high_volume_days",
                "missing_bar_action": "condition_false",
            },
        },
        "research_budget": {
            "max_formal_candidates": 7,
            "minimum_categories": [
                "numeric_definition",
                "mechanism_signal",
                "applicability",
                "condition_necessity",
                "trade_timing",
            ],
        },
        "stability_requirements": {
            "confirmation_min_median_sharpe": 1.0,
            "confirmation_min_median_annual_return": 0.08,
            "confirmation_min_worst_fold_sharpe": 0.0,
            "confirmation_max_worst_fold_drawdown": 0.20,
            "confirmation_min_total_trade_count": 300,
            "confirmation_min_cost_stress_sharpe": 0.50,
            "confirmation_min_delay_stress_sharpe": 0.50,
            "final_min_annual_return": 0.05,
            "final_min_sharpe": 0.75,
            "final_max_drawdown": 0.20,
            "final_min_trade_count": 100,
        },
    }


def build_stage_spec(
    profile: dict[str, object],
    *,
    experiment_id: str | None = None,
    stage: str = "research",
    run_stress: bool | None = None,
) -> dict[str, object]:
    research = profile.get("research_design")
    data = profile.get("data")
    universe = profile.get("universe")
    execution = profile.get("execution")
    baseline_defaults = profile.get("baseline_defaults")
    if not all(
        isinstance(item, dict)
        for item in (research, data, universe, execution, baseline_defaults)
    ):
        raise ValueError("V2 运行设置不完整")
    research = dict(research)  # type: ignore[arg-type]
    data = dict(data)  # type: ignore[arg-type]
    universe = dict(universe)  # type: ignore[arg-type]
    execution = dict(execution)  # type: ignore[arg-type]
    baseline_defaults = dict(baseline_defaults)  # type: ignore[arg-type]

    if stage == "research":
        validate_start = str(research["research_start"])
        validate_end = str(research["research_end"])
        stage_train_end = str(research["train_end"])
        folds = int(research["research_folds"])
        stress = False if run_stress is None else bool(run_stress)
    elif stage == "confirmation":
        validate_start = str(research["confirmation_start"])
        validate_end = str(research["confirmation_end"])
        stage_train_end = str(research["research_end"])
        folds = int(research["confirmation_folds"])
        stress = True if run_stress is None else bool(run_stress)
    elif stage == "final":
        validate_start = str(research["final_start"])
        validate_end = str(research["final_end"])
        stage_train_end = str(research["confirmation_end"])
        folds = 1
        stress = True if run_stress is None else bool(run_stress)
    else:
        raise ValueError(f"未知V2阶段: {stage}")

    entry = universe.get("experiment_entry")
    if not isinstance(entry, dict):
        raise ValueError("V2 universe.experiment_entry 缺失")
    timestamp = datetime.now(tz=timezone.utc).strftime("%Y%m%d_%H%M%S")
    resolved_id = experiment_id or f"exp_research_v2_{timestamp}_{uuid4().hex[:6]}"
    final_start = str(research["final_start"])
    final_end = str(research["final_end"])
    stage_spec = {
        "workflow_version": "v2",
        "experiment_id": resolved_id,
        "experiment_name": resolved_id,
        "stage": stage,
        "train_start": str(research["train_start"]),
        "train_end": stage_train_end,
        "validate_start": validate_start,
        "validate_end": validate_end,
        "development_start": validate_start,
        "development_end": validate_end,
        "backtest_start": final_start,
        "backtest_end": final_end,
        "final_test_start": final_start,
        "final_test_end": final_end,
        "universe": [deepcopy(entry)],
        "bar_interval": "1d",
        "backtest_mode": "daily_portfolio",
        "development_folds": folds,
        "development_fold_count": folds,
        "gap_trading_days": 0,
        "min_development_fold_days": 120 if stage != "final" else 20,
        "walk_forward_development": True,
        "run_cost_stress": stress,
        "run_delay_stress": stress,
        "require_all_folds_beat_cash": True,
        "min_fold_pass_ratio": 0.75 if folds >= 4 else 2.0 / 3.0,
        "min_worst_fold_sharpe": 0.0,
        "min_cost_stress_sharpe": 0.5 if stage != "research" else 0.0,
        "min_delay_stress_sharpe": 0.5 if stage != "research" else 0.0,
        "min_trials_for_deflated_sharpe": 9999,
        "min_deflated_sharpe_probability": 0.0,
        "max_single_change_ratio": 0.35,
        "buy_cost": float(execution["buy_cost"]),
        "sell_cost": float(execution["sell_cost"]),
        "slippage": float(execution["slippage"]),
        "lot_size": int(execution["lot_size"]),
        "capital": float(execution["capital"]),
        "evaluation": {
            "min_sharpe_ratio": 0.5,
            "max_drawdown": 0.20,
            "min_return_rate": 0.0,
            "min_trade_count": 100,
        },
        "allow_final_test": False,
        "run_final_test": False,
        "backtest_dataset": str(data["backtest_dataset"]),
    }
    position_plan_contract = baseline_defaults.get("position_plan_contract")
    if isinstance(position_plan_contract, dict):
        stage_spec["position_plan_contract"] = deepcopy(position_plan_contract)
    continuity_contract = baseline_defaults.get("market_day_continuity_contract")
    if isinstance(continuity_contract, dict):
        condition_field = str(continuity_contract.get("condition_field", "")).strip()
        if condition_field:
            declared_days = baseline_defaults.get(condition_field)
            if declared_days != continuity_contract.get("consecutive_days"):
                raise ValueError("连续市场日约定与基准参数不一致")
        stage_spec["market_day_continuity_contract"] = deepcopy(continuity_contract)
    if (
        execution.get("sell_cost_before_change") is not None
        or execution.get("sell_cost_change_date") is not None
    ):
        if (
            execution.get("sell_cost_before_change") is None
            or execution.get("sell_cost_change_date") is None
        ):
            raise ValueError("V2历史卖出费率和生效日期必须同时提供")
        stage_spec["sell_cost_before_change"] = float(
            execution["sell_cost_before_change"]
        )
        stage_spec["sell_cost_change_date"] = str(
            execution["sell_cost_change_date"]
        )
    if execution.get("cost_rule") is not None:
        stage_spec["cost_rule"] = str(execution["cost_rule"])
    return stage_spec


def default_parquet_glob(project_root: str | Path) -> str:
    root = Path(project_root).resolve()
    return str(
        root.parent.parent
        / "因子日历测试"
        / "february_march_results"
        / "cache"
        / "daily_by_stock"
        / "*.parquet"
    )
