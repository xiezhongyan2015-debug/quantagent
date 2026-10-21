# -*- coding: utf-8 -*-

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import summarize_historical_factor_backtests as target


def _write_inputs(root: Path) -> target.SummaryPaths:
    root.mkdir(parents=True)
    mapping = pd.DataFrame(
        [
            {"record_id": "HC1", "year": 2022, "date": "2022-01-01", "name": "因子甲", "formula_key": "HF1", "signal_frequency": "每日", "mode": "strict"},
            {"record_id": "HC2", "year": 2023, "date": "2023-01-01", "name": "因子甲旧名", "formula_key": "HF1", "signal_frequency": "daily", "mode": "strict"},
            {"record_id": "HC3", "year": 2024, "date": "2024-01-01", "name": "因子乙", "formula_key": "HF2", "signal_frequency": "月末", "mode": "trial"},
        ]
    )
    mapping.to_csv(root / "factor_mapping.csv", index=False, encoding="utf-8-sig")
    units = pd.DataFrame(
        [
            {"unit_id": "HF1__daily", "formula_key": "HF1", "normalized_frequency": "daily", "representative_name": "因子甲", "mode": "strict"},
            {"unit_id": "HF2__monthly", "formula_key": "HF2", "normalized_frequency": "monthly", "representative_name": "因子乙", "mode": "trial"},
        ]
    )
    units.to_csv(root / "formula_units.csv", index=False, encoding="utf-8-sig")
    audit = units[["unit_id"]].copy()
    audit["calculation_method"] = ["standard", "unresolved"]
    audit["registered_name"] = ["因子甲", ""]
    audit["method_note"] = ["按原式", "尚未登记"]
    audit.to_csv(root / "formula_support_audit.csv", index=False, encoding="utf-8-sig")
    level2 = pd.DataFrame(
        [
            {
                "record_id": "HC4",
                "year": 2024,
                "date": "2024-02-01",
                "name": "Level2因子",
                "formula_key": "HF4",
                "signal_frequency": "日度",
                "excluded_reason": "缺少逐笔委托",
            }
        ]
    )
    level2.to_csv(root / "level2_excluded.csv", index=False, encoding="utf-8-sig")
    return target.SummaryPaths(root)


def _write_complete_batch(paths: target.SummaryPaths, include_yearly: bool = True) -> None:
    batch = paths.runs / "batch_complete"
    batch.mkdir(parents=True)
    pd.DataFrame(
        [
            {
                "unit_id": "HF1__daily",
                "formula_key": "HF1",
                "name": "因子甲",
                "frequency": "daily",
                "status": "completed",
                "finite_values": 100,
                "periods_2022_2024": 2,
                "error": "",
            }
        ]
    ).to_csv(batch / "unit_status.csv", index=False)
    pd.DataFrame(
        [
            {
                "unit_id": "HF1__daily",
                "periods": 2,
                "cumulative_return": 0.155,
                "annual_return": 0.075,
                "mean_ic": 0.03,
            }
        ]
    ).to_csv(batch / "unit_backtest_summary.csv", index=False)
    if include_yearly:
        pd.DataFrame(
            [
                {"unit_id": "HF1__daily", "year": 2022, "strategy_return": 0.10, "benchmark_return": 0.05, "excess_return": 0.05, "periods": 1, "mean_ic": 0.02},
                {"unit_id": "HF1__daily", "year": 2023, "strategy_return": 0.05, "benchmark_return": 0.02, "excess_return": 0.03, "periods": 1, "mean_ic": 0.04},
            ]
        ).to_csv(batch / "unit_yearly_returns.csv", index=False)
    pd.DataFrame(
        [
            {"unit_id": "HF1__daily", "signal_date": "2022-01-01", "exit_date": "2022-01-08", "selected_net_return": 0.10, "benchmark_return": 0.05, "ic": 0.02},
            {"unit_id": "HF1__daily", "signal_date": "2023-01-01", "exit_date": "2023-01-08", "selected_net_return": 0.05, "benchmark_return": 0.02, "ic": 0.04},
        ]
    ).to_parquet(batch / "unit_rebalance_returns.parquet", index=False)


def test_build_outputs_expands_units_and_adds_level2(tmp_path: Path) -> None:
    paths = _write_inputs(tmp_path / "results")
    _write_complete_batch(paths)
    outputs = target.build_outputs(paths)
    summary = outputs["factor_backtest_summary.csv"]
    assert len(summary) == 4
    ordinary = summary[summary["record_scope"] == "ordinary_minute"]
    assert len(ordinary) == 3
    assert set(ordinary.loc[ordinary["formula_key"] == "HF1", "backtest_status"]) == {"completed"}
    assert set(ordinary.loc[ordinary["formula_key"] == "HF1", "cumulative_return"]) == {0.155}
    assert ordinary.loc[ordinary["formula_key"] == "HF2", "backtest_status"].iloc[0] == "unresolved"
    excluded = summary[summary["record_scope"] == "level2_excluded"].iloc[0]
    assert excluded["backtest_status"] == "level2_excluded"
    assert excluded["excluded_reason"] == "缺少逐笔委托"

    yearly = outputs["factor_yearly_returns.csv"]
    assert len(yearly) == 4
    assert set(yearly["record_id"]) == {"HC1", "HC2"}
    assert set(yearly["return_year"].astype(int)) == {2022, 2023}

    status = outputs["formula_unit_status.csv"]
    assert len(status) == 2
    first = status.set_index("unit_id").loc["HF1__daily"]
    assert first["return_rows"] == 2
    assert first["selected_batch"] == "batch_complete"
    assert "普通分钟因子原始记录：3 条" in outputs["README.md"]


def test_missing_batch_files_and_returns_yearly_fallback(tmp_path: Path) -> None:
    paths = _write_inputs(tmp_path / "results")
    _write_complete_batch(paths, include_yearly=False)
    (paths.runs / "empty_batch").mkdir(parents=True)
    outputs = target.build_outputs(paths)
    yearly = outputs["factor_yearly_returns.csv"]
    assert len(yearly) == 4
    assert yearly.loc[yearly["return_year"] == 2022, "strategy_return"].iloc[0] == pytest.approx(0.10)
    status = outputs["formula_unit_status.csv"].set_index("unit_id")
    assert status.loc["HF2__monthly", "status"] == "unresolved"


def test_completed_batch_wins_over_newer_failed_duplicate(tmp_path: Path) -> None:
    paths = _write_inputs(tmp_path / "results")
    _write_complete_batch(paths)
    failed = paths.runs / "batch_failed"
    failed.mkdir(parents=True)
    pd.DataFrame(
        [{"unit_id": "HF1__daily", "status": "failed", "error": "人工测试失败"}]
    ).to_csv(failed / "unit_status.csv", index=False)
    outputs = target.build_outputs(paths)
    row = outputs["formula_unit_status.csv"].set_index("unit_id").loc["HF1__daily"]
    assert row["status"] == "completed"
    assert row["selected_batch"] == "batch_complete"
    assert row["available_batch_count"] == 2


def test_write_outputs_creates_five_requested_files(tmp_path: Path) -> None:
    paths = _write_inputs(tmp_path / "results")
    outputs = target.build_outputs(paths)
    target.write_outputs(paths, outputs)
    assert {path.name for path in paths.result_dir.iterdir() if path.is_file()} >= {
        "factor_backtest_summary.csv",
        "factor_yearly_returns.csv",
        "formula_unit_status.csv",
        "formula_unit_summary.csv",
        "README.md",
    }
