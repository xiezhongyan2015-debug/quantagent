# -*- coding: utf-8 -*-
"""六月报告填充脚本的轻量检查。"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import fill_june_report as target


def _write_sample_results(result_dir: Path) -> None:
    result_dir.mkdir(parents=True)
    status_rows = []
    ic_rows = []
    backtest_rows = []
    yearly_rows = []

    for position, spec in enumerate(target.FACTOR_SPECS):
        status_rows.append(
            {
                "factor": spec.code,
                "name": spec.name,
                "signal_frequency": spec.frequency,
                "calculated": True,
            }
        )
        for period in ["full", "2016_2020", "2021_2025"]:
            ic_mean = (position + 1) * 0.01
            if period == "2021_2025":
                ic_mean *= -1
            if spec.code == "J06_D04_FUZZY_AMOUNT_RATIO_20D" and period == "full":
                ic_mean = np.nan
            ic_rows.append(
                {
                    "factor": spec.code,
                    "period": period,
                    "ic_mean": ic_mean,
                    "icir_raw": (position + 1) * 0.1,
                }
            )
            excess = (position - 5) / 100.0
            backtest_rows.append(
                {
                    "factor": spec.code,
                    "period": period,
                    "direction": "high" if position % 2 == 0 else "low",
                    "annual_return": 0.10 + position / 100.0,
                    "benchmark_annual_return": 0.025,
                    "annual_excess_return": excess,
                    "max_drawdown": -0.20 - position / 100.0,
                }
            )
        for year in [2016, 2021]:
            yearly_rows.append(
                {
                    "factor": spec.code,
                    "year": year,
                    "direction": "high" if position % 2 == 0 else "low",
                    "strategy_return": (
                        np.nan
                        if spec.code == "J06_D01_STRUCTURED_REVERSAL_21D_10M"
                        and year == 2016
                        else 0.1234
                    ),
                    "benchmark_return": -0.0567,
                    "excess_return": 0.1801,
                    "periods": 12 if spec.frequency == "monthly" else 48,
                }
            )

    pd.DataFrame(ic_rows).to_csv(
        result_dir / "original_frequency_ic_summary.csv", index=False
    )
    pd.DataFrame(backtest_rows).to_csv(
        result_dir / "original_frequency_backtest_summary.csv", index=False
    )
    pd.DataFrame(yearly_rows).to_csv(
        result_dir / "original_frequency_yearly_backtest.csv", index=False
    )
    pd.DataFrame(status_rows).to_csv(result_dir / "factor_status.csv", index=False)


def _write_template(report_path: Path) -> None:
    report_path.write_text(
        "# 测试报告\n\n"
        "## 三、计算说明\n\n保留第三节。\n\n"
        "## 四、回测结果\n\n【待实际输出】\n\n"
        "## 五、统一回测方法\n\n保留第五节。\n",
        encoding="utf-8",
    )


def test_fill_report_formats_values_keeps_order_and_is_repeatable(tmp_path: Path) -> None:
    result_dir = tmp_path / "final"
    report_path = tmp_path / "六月报告.md"
    _write_sample_results(result_dir)
    _write_template(report_path)

    first = target.fill_report(result_dir, report_path)
    second = target.fill_report(result_dir, report_path)

    assert first == second == report_path.read_text(encoding="utf-8")
    assert "【待实际输出】" not in first
    assert "保留第三节。" in first
    assert "保留第五节。" in first
    assert "0.0100" in first
    assert "10.00%" in first
    assert "2.50%" in first
    assert "-20.00%" in first
    assert "12.34%" in first
    assert "-5.67%" in first
    assert "| J06_D04_FUZZY_AMOUNT_RATIO_20D | 模糊金额比 | 月频 | 卡片可算（首尾分钟按项目说明） | — |" in first
    assert "| J06_D01_STRUCTURED_REVERSAL_21D_10M | 结构化反转 | 2016 | 高组 | — | -5.67% |" in first
    assert "年化相差最高的是 特质隔夜收益波动率（4.00%）" in first
    assert "IC 绝对值最高的是 特质隔夜收益波动率，IC 为 -0.1000" in first

    ic_section = first.split("### 4.1 原频率 IC", 1)[1].split(
        "### 4.2 分组回测摘要", 1
    )[0]
    positions = [ic_section.index(spec.code) for spec in target.FACTOR_SPECS]
    assert positions == sorted(positions)

    yearly_section = first.split("### 4.3 分年结果", 1)[1].split(
        "### 4.4 简短结论", 1
    )[0]
    yearly_positions = [yearly_section.index(spec.code) for spec in target.FACTOR_SPECS]
    assert yearly_positions == sorted(yearly_positions)


def test_status_frequency_mismatch_stops_before_report_change(tmp_path: Path) -> None:
    result_dir = tmp_path / "final"
    report_path = tmp_path / "六月报告.md"
    _write_sample_results(result_dir)
    _write_template(report_path)
    before = report_path.read_text(encoding="utf-8")

    status_path = result_dir / "factor_status.csv"
    status = pd.read_csv(status_path)
    status.loc[0, "signal_frequency"] = "monthly"
    status.to_csv(status_path, index=False)

    with pytest.raises(ValueError, match="频率应为 daily"):
        target.fill_report(result_dir, report_path)
    assert report_path.read_text(encoding="utf-8") == before
