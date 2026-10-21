from __future__ import annotations

import numpy as np
import pandas as pd

import select_backtest_factors_lightgbm as target


def test_compound_returns() -> None:
    actual = target.compound_returns([0.10, -0.05])
    assert np.isclose(actual, 1.10 * 0.95 - 1.0)


def test_aggregate_monthly_returns() -> None:
    source = pd.DataFrame(
        {
            "factor": ["F1", "F1", "F1"],
            "exit_date": ["2025-01-10", "2025-01-20", "2025-02-10"],
            "net_long_short_return": [0.10, -0.05, 0.02],
            "transaction_cost": [0.001, 0.002, 0.001],
        }
    )
    result = target.aggregate_monthly_returns(source, "factor")
    january = result[result["month"].eq("2025-01")].iloc[0]
    assert january["period_count"] == 2
    assert np.isclose(january["monthly_return"], 1.10 * 0.95 - 1.0)
    assert np.isclose(january["average_cost"], 0.0015)


def test_duplicate_groups_keep_higher_quality() -> None:
    panel = pd.DataFrame(
        {
            "factor_id": ["F_TRIAL", "F_TRIAL", "F_FORMAL", "F_FORMAL"],
            "month": ["2023-01", "2023-02", "2023-01", "2023-02"],
            "monthly_ic": [0.01, 0.02, 0.01, 0.02],
            "monthly_return": [0.03, 0.04, 0.03, 0.04],
        }
    )
    metadata = pd.DataFrame(
        {
            "factor_id": ["F_TRIAL", "F_FORMAL"],
            "implementation_quality": ["trial", "strict"],
            "source_kind": ["historical", "historical"],
            "full_months": [12, 12],
            "available_from_month": ["2022-01", "2022-01"],
        }
    )
    groups = target.assign_duplicate_groups(panel, metadata, "2023-12")
    assert groups["duplicate_size"].eq(2).all()
    assert groups["representative_factor_id"].eq("F_FORMAL").all()
    assert bool(
        groups.loc[groups["factor_id"].eq("F_FORMAL"), "is_representative"].iloc[0]
    )


def test_similarity_filter_removes_nearly_identical_factor() -> None:
    months = pd.period_range("2024-01", periods=24, freq="M").astype(str)
    base = np.linspace(-0.02, 0.03, 24)
    panel = pd.DataFrame(
        {
            "factor_id": np.repeat(["F1", "F2", "F3"], 24),
            "month": np.tile(months, 3),
            "monthly_return": np.r_[
                base,
                base * 1.01,
                np.sin(np.linspace(0.0, 4.0 * np.pi, 24)) * 0.02,
            ],
        }
    )
    candidates = pd.DataFrame(
        {
            "factor_id": ["F1", "F2", "F3"],
            "selection_score": [0.03, 0.02, 0.01],
        }
    )
    selected, decisions = target.select_distinct_factors(
        candidates,
        panel,
        top_k=2,
        as_of_month="2025-12",
        similarity_limit=0.85,
        score_column="selection_score",
    )
    assert selected["factor_id"].tolist() == ["F1", "F3"]
    decision = decisions.set_index("factor_id").loc["F2", "selection_decision"]
    assert "过于相似" in decision


def test_similarity_filter_accepts_empty_candidates() -> None:
    candidates = pd.DataFrame(columns=["factor_id", "selection_score"])
    panel = pd.DataFrame(columns=["factor_id", "month", "monthly_return"])
    selected, decisions = target.select_distinct_factors(
        candidates,
        panel,
        top_k=20,
        as_of_month="2025-12",
        similarity_limit=0.85,
        score_column="selection_score",
    )
    assert selected.empty
    assert decisions.empty
    assert "selection_rank" in selected.columns


def test_lightgbm_method_decision_covers_pass_fail_and_error() -> None:
    passing = {"rank_ic_mean": 0.03, "top_excess_mean": 0.02}
    baseline = {"top_excess_mean": 0.01}
    assert target.lightgbm_passes_method_comparison(passing, baseline)
    assert not target.lightgbm_passes_method_comparison(
        {"rank_ic_mean": -0.01, "top_excess_mean": 0.02},
        baseline,
    )
    assert not target.lightgbm_passes_method_comparison(
        passing,
        baseline,
        "训练失败",
    )


def test_model_feature_sets_remove_time_counts_and_expanding_values() -> None:
    feature_sets = target.model_feature_sets()
    assert set(feature_sets) == {
        "全部输入",
        "去掉历史月份数",
        "仅近期滚动指标",
    }
    assert "history_months" in feature_sets["全部输入"]
    assert "history_months" not in feature_sets["去掉历史月份数"]
    assert "history_months" not in feature_sets["仅近期滚动指标"]
    assert not any(
        "expanding" in column for column in feature_sets["仅近期滚动指标"]
    )
    assert "return_mean_12" in feature_sets["仅近期滚动指标"]


def test_restrict_to_non_level2_removes_all_level2_rows() -> None:
    metadata = pd.DataFrame(
        {
            "factor_id": ["N1", "L1"],
            "data_group": ["非Level2", "Level2"],
        }
    )
    monthly_ic = pd.DataFrame(
        {
            "factor_id": ["N1", "L1", "OTHER"],
            "data_group": ["非Level2", "Level2", "非Level2"],
            "return_month": ["2025-01", "2025-01", "2025-01"],
        }
    )
    filtered_metadata, filtered_monthly_ic = target.restrict_to_non_level2(
        metadata,
        monthly_ic,
    )
    assert filtered_metadata["factor_id"].tolist() == ["N1"]
    assert filtered_monthly_ic["factor_id"].tolist() == ["N1"]
    assert filtered_monthly_ic["data_group"].eq("非Level2").all()


def test_feature_panel_uses_next_month_as_target() -> None:
    metadata = pd.DataFrame(
        {
            "factor_id": ["F1"],
            "implementation_quality": ["strict"],
            "status": ["可排名"],
            "source_kind": ["historical"],
            "calendar_editions": ["2022"],
        }
    )
    metadata = target.add_factor_availability(metadata)
    monthly_ic = pd.DataFrame(
        {
            "factor_id": ["F1"] * 3,
            "factor_name": ["测试因子"] * 3,
            "data_group": ["非Level2"] * 3,
            "frequency": ["daily"] * 3,
            "month": ["2023-01", "2023-02", "2023-03"],
            "monthly_ic": [0.01, 0.02, -0.01],
        }
    )
    returns = pd.DataFrame(
        {
            "factor_id": ["F1"] * 3,
            "month": ["2023-01", "2023-02", "2023-03"],
            "monthly_return": [0.03, 0.04, -0.02],
            "period_count": [4, 4, 4],
            "average_cost": [0.001, 0.001, 0.001],
            "monthly_period_drawdown": [0.0, 0.0, -0.02],
        }
    )
    directions = pd.DataFrame(
        {
            "factor_id": ["F1"],
            "selected_direction": ["high"],
            "direction_sign": [1.0],
            "pre2021_ic": [0.02],
            "direction_source": ["测试"],
        }
    )
    empty_returns = returns.iloc[0:0].copy()
    empty_directions = directions.iloc[0:0].copy()
    targets = pd.DataFrame(
        {
            "factor_id": ["F1", "F1"],
            "target_month": ["2023-02", "2023-03"],
            "target_return": [0.04, -0.02],
            "target_period_count": [4, 4],
            "target_average_cost": [0.001, 0.001],
            "target_period_drawdown": [0.0, -0.02],
        }
    )
    panel = target.build_feature_panel(
        metadata,
        monthly_ic,
        returns,
        directions,
        empty_returns,
        empty_directions,
        targets,
    )
    january = panel[panel["month"].eq("2023-01")].iloc[0]
    february = panel[panel["month"].eq("2023-02")].iloc[0]
    assert january["target_month"] == "2023-02"
    assert np.isclose(january["target_return"], 0.04)
    assert np.isclose(february["return_mean_3"], 0.035)
    assert february["target_is_next_month"] == 1


def test_factor_availability_uses_exact_calendar_dates() -> None:
    metadata = pd.DataFrame(
        {
            "factor_id": [
                "HF0022__daily",
                "Y2026_EXTRA_COMPLETION51__J09_D23_VOLUME_PEAK_MINUTE_RETURN",
            ],
            "calendar_editions": ["2022 | 2023 | 2024", "2026"],
            "frequency": ["daily", "daily"],
        }
    )
    result = target.add_factor_availability(metadata).set_index("factor_id")
    assert result.loc["HF0022__daily", "available_date"] == "2022-03-24"
    future_id = "Y2026_EXTRA_COMPLETION51__J09_D23_VOLUME_PEAK_MINUTE_RETURN"
    assert result.loc[future_id, "available_date"] == "2026-09-23"
    assert result.loc[future_id, "available_from_month"] > target.AS_OF_MONTH


def test_entry_month_targets_do_not_use_already_open_positions() -> None:
    source = pd.DataFrame(
        {
            "factor": ["F1", "F1"],
            "signal_date": ["2025-01-28", "2025-02-10"],
            "entry_date": ["2025-01-31", "2025-02-11"],
            "exit_date": ["2025-02-07", "2025-02-18"],
            "net_long_short_return": [0.03, 0.02],
            "transaction_cost": [0.001, 0.001],
        }
    )
    target_returns = target.aggregate_entry_month_targets(source, "factor")
    january = target_returns[target_returns["target_month"].eq("2025-01")]
    february = target_returns[target_returns["target_month"].eq("2025-02")]
    assert np.isclose(january.iloc[0]["target_return"], 0.03)
    assert np.isclose(february.iloc[0]["target_return"], 0.02)
    assert january.iloc[0]["target_period_count"] == 1


def test_rank_labels_are_month_local_ordered_integers() -> None:
    frame = pd.DataFrame(
        {
            "target_month": ["2024-01"] * 5 + ["2024-02"] * 5,
            "target_return": [1, 2, 3, 4, 5, 5, 4, 3, 2, 1],
        }
    )
    labels = target.make_rank_labels(frame)
    assert labels.dtype == np.int32
    assert set(labels.tolist()) == {0, 1, 2, 3, 4}
    for _, part in frame.assign(label=labels).groupby("target_month"):
        assert part.sort_values("target_return")["label"].is_monotonic_increasing


def test_prepare_rank_frame_keeps_month_groups_together() -> None:
    frame = pd.DataFrame(
        {
            "factor_id": ["F2", "F1", "F2", "F1"],
            "target_month": ["2024-02", "2024-01", "2024-01", "2024-02"],
            "target_return": [0.02, 0.03, -0.01, 0.01],
            "return_mean_12": [0.2, 0.3, -0.1, 0.1],
        }
    )
    bounds = target.fit_feature_bounds(frame, ["return_mean_12"])
    ordered, _, labels, groups, weights = target.prepare_rank_frame(
        frame, ["return_mean_12"], bounds
    )
    assert ordered["target_month"].tolist() == [
        "2024-01",
        "2024-01",
        "2024-02",
        "2024-02",
    ]
    assert groups.tolist() == [2, 2]
    assert int(groups.sum()) == len(labels) == len(weights)


def test_rank_score_can_be_negative_without_removing_best_candidates() -> None:
    frame = pd.DataFrame(
        {
            "factor_id": ["F1", "F2"],
            "rank_score": [-1.0, -2.0],
            "return_mean_12": [0.03, 0.02],
            "ic_mean_12": [0.02, 0.01],
            "return_positive_rate_12": [0.6, 0.6],
            "return_max_drawdown_12": [-0.1, -0.1],
        }
    )
    result = target.filter_formal_candidates(
        frame,
        "rank_score",
        20,
        require_positive_score=False,
    )
    assert result["factor_id"].tolist() == ["F1", "F2"]
