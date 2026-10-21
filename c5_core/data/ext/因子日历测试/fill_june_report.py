# -*- coding: utf-8 -*-
"""用六月正式回测 CSV 填写报告第四节。"""

from __future__ import annotations

import argparse
import math
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

import pandas as pd


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_RESULT_DIR = BASE_DIR / "june_factor_results" / "final"
DEFAULT_REPORT_PATH = BASE_DIR / "june_factor_results" / "六月量价因子分钟回测报告.md"
EMPTY = "—"


@dataclass(frozen=True)
class FactorSpec:
    code: str
    name: str
    frequency: str
    version: str


# 报告的三张表都严格使用这一顺序。
FACTOR_SPECS = (
    FactorSpec(
        "J06_D01_STRUCTURED_REVERSAL_21D_10M",
        "结构化反转",
        "daily",
        "卡片可算",
    ),
    FactorSpec(
        "J06_D04_FUZZY_AMOUNT_RATIO_20D",
        "模糊金额比",
        "monthly",
        "卡片可算（首尾分钟按项目说明）",
    ),
    FactorSpec(
        "J06_D06_CSAD_TIME_SERIES_120D",
        "CSAD 模型",
        "monthly",
        "卡片可算",
    ),
    FactorSpec(
        "J06_D08_WAITING_RESCUE_20D",
        "待著而救因子",
        "monthly",
        "卡片可算（相邻点按项目说明）",
    ),
    FactorSpec(
        "J06_D10_VOLATILITY_PANIC_20D",
        "波动率加剧—惊恐因子",
        "monthly",
        "卡片可算",
    ),
    FactorSpec(
        "J06_D11_DOWNSIDE_REALIZED_VOL_5M",
        "下行已实现波动率",
        "daily",
        "卡片可算（采用 5 分钟示例）",
    ),
    FactorSpec(
        "J06_D13_PATV_5M",
        "持续异常交易量 PATV",
        "daily",
        "解释版本",
    ),
    FactorSpec(
        "J06_D17_PV_CORR_TREND_20D",
        "价量相关性趋势因子",
        "monthly",
        "解释版本",
    ),
    FactorSpec(
        "J06_D19_INTRADAY_CVAR_5PCT",
        "日内条件在险价值",
        "monthly",
        "解释版本",
    ),
    FactorSpec(
        "J06_D23_IDIOSYNCRATIC_OVERNIGHT_VOL_20D",
        "特质隔夜收益波动率",
        "daily",
        "卡片可算（基础缓存股票 PCA）",
    ),
)


FREQUENCY_TEXT = {"daily": "日频", "monthly": "月频"}
DIRECTION_TEXT = {"high": "高组", "low": "低组"}
REPORT_START = "## 四、回测结果"
REPORT_END = "## 五、统一回测方法"


def _read_csv(path: Path, required_columns: set[str]) -> pd.DataFrame:
    if not path.is_file():
        raise FileNotFoundError(f"找不到结果文件：{path}")
    frame = pd.read_csv(path, encoding="utf-8-sig")
    missing = required_columns - set(frame.columns)
    if missing:
        columns = "、".join(sorted(missing))
        raise ValueError(f"{path.name} 缺少这些列：{columns}")
    frame = frame.copy()
    frame["factor"] = frame["factor"].astype("string").str.strip()
    return frame


def _is_true(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if pd.isna(value):
        return False
    return str(value).strip().lower() in {"true", "1", "yes", "y"}


def _number(value: object) -> float | None:
    if value is None or pd.isna(value):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _format_decimal(value: object, digits: int = 4) -> str:
    number = _number(value)
    if number is None:
        return EMPTY
    rounded = round(number, digits)
    if rounded == 0:
        rounded = 0.0
    return f"{rounded:.{digits}f}"


def _format_percent(value: object) -> str:
    number = _number(value)
    if number is None:
        return EMPTY
    rounded = round(number * 100.0, 2)
    if rounded == 0:
        rounded = 0.0
    return f"{rounded:.2f}%"


def _format_count(value: object) -> str:
    number = _number(value)
    if number is None:
        return EMPTY
    return str(int(round(number)))


def _format_direction(value: object) -> str:
    if value is None or pd.isna(value):
        return EMPTY
    text = str(value).strip().lower()
    if not text:
        return EMPTY
    return DIRECTION_TEXT.get(text, str(value).strip())


def _one_row(
    frame: pd.DataFrame,
    factor: str,
    selector: str,
    value: object,
    source_name: str,
) -> pd.Series | None:
    selected = frame[(frame["factor"] == factor) & (frame[selector] == value)]
    if len(selected) > 1:
        raise ValueError(
            f"{source_name} 中 {factor} 的 {selector}={value} 出现了多行"
        )
    return None if selected.empty else selected.iloc[0]


def _cell(row: pd.Series | None, column: str) -> object:
    return None if row is None else row.get(column)


def _validate_status(status: pd.DataFrame) -> None:
    duplicates = status[status["factor"].duplicated(keep=False)]
    if not duplicates.empty:
        codes = "、".join(sorted(duplicates["factor"].dropna().unique()))
        raise ValueError(f"factor_status.csv 中因子代码重复：{codes}")

    indexed = status.set_index("factor", drop=False)
    for spec in FACTOR_SPECS:
        if spec.code not in indexed.index:
            raise ValueError(f"factor_status.csv 缺少 {spec.code}")
        row = indexed.loc[spec.code]
        actual_frequency = str(row["signal_frequency"]).strip().lower()
        if actual_frequency != spec.frequency:
            raise ValueError(
                f"{spec.code} 的频率应为 {spec.frequency}，实际为 {actual_frequency}"
            )
        if not _is_true(row["calculated"]):
            raise ValueError(f"factor_status.csv 没有把 {spec.code} 标为已计算")


def _build_ic_table(ic_summary: pd.DataFrame) -> list[str]:
    lines = [
        "### 4.1 原频率 IC",
        "",
        "| 因子代码 | 因子名称 | 频率 | 版本 | 2016—2025 IC | 2016—2025 ICIR | 2021—2025 IC | 2021—2025 ICIR |",
        "|---|---|---|---|---:|---:|---:|---:|",
    ]
    for spec in FACTOR_SPECS:
        full = _one_row(ic_summary, spec.code, "period", "full", ic_summary.attrs["name"])
        later = _one_row(
            ic_summary,
            spec.code,
            "period",
            "2021_2025",
            ic_summary.attrs["name"],
        )
        lines.append(
            "| "
            + " | ".join(
                [
                    spec.code,
                    spec.name,
                    FREQUENCY_TEXT[spec.frequency],
                    spec.version,
                    _format_decimal(_cell(full, "ic_mean")),
                    _format_decimal(_cell(full, "icir_raw")),
                    _format_decimal(_cell(later, "ic_mean")),
                    _format_decimal(_cell(later, "icir_raw")),
                ]
            )
            + " |"
        )
    return lines


def _build_backtest_table(backtest: pd.DataFrame) -> list[str]:
    lines = [
        "### 4.2 分组回测摘要",
        "",
        "| 因子代码 | 2016—2020 选定方向 | 2016—2025 扣费年化收益 | 同范围等权年化收益 | 年化相差 | 2021—2025 扣费年化收益 | 同范围等权年化收益 | 年化相差 | 最大回撤 |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for spec in FACTOR_SPECS:
        full = _one_row(backtest, spec.code, "period", "full", backtest.attrs["name"])
        early = _one_row(
            backtest,
            spec.code,
            "period",
            "2016_2020",
            backtest.attrs["name"],
        )
        later = _one_row(
            backtest,
            spec.code,
            "period",
            "2021_2025",
            backtest.attrs["name"],
        )
        lines.append(
            "| "
            + " | ".join(
                [
                    spec.code,
                    _format_direction(_cell(early, "direction")),
                    _format_percent(_cell(full, "annual_return")),
                    _format_percent(_cell(full, "benchmark_annual_return")),
                    _format_percent(_cell(full, "annual_excess_return")),
                    _format_percent(_cell(later, "annual_return")),
                    _format_percent(_cell(later, "benchmark_annual_return")),
                    _format_percent(_cell(later, "annual_excess_return")),
                    _format_percent(_cell(full, "max_drawdown")),
                ]
            )
            + " |"
        )
    return lines


def _build_yearly_table(yearly: pd.DataFrame) -> list[str]:
    lines = [
        "### 4.3 分年结果",
        "",
        "| 因子代码 | 因子名称 | 年份 | 方向 | 扣费收益 | 同范围等权收益 | 相差 | 调仓期数 |",
        "|---|---|---:|---|---:|---:|---:|---:|",
    ]
    for spec in FACTOR_SPECS:
        for year in range(2016, 2026):
            row = _one_row(yearly, spec.code, "year", year, yearly.attrs["name"])
            lines.append(
                "| "
                + " | ".join(
                    [
                        spec.code,
                        spec.name,
                        str(year),
                        _format_direction(_cell(row, "direction")),
                        _format_percent(_cell(row, "strategy_return")),
                        _format_percent(_cell(row, "benchmark_return")),
                        _format_percent(_cell(row, "excess_return")),
                        _format_count(_cell(row, "periods")),
                    ]
                )
                + " |"
            )
    return lines


def _available_later_values(
    frame: pd.DataFrame, column: str
) -> list[tuple[FactorSpec, float]]:
    values: list[tuple[FactorSpec, float]] = []
    for spec in FACTOR_SPECS:
        row = _one_row(frame, spec.code, "period", "2021_2025", frame.attrs["name"])
        value = _number(_cell(row, column))
        if value is not None:
            values.append((spec, value))
    return values


def _build_conclusion(
    ic_summary: pd.DataFrame, backtest: pd.DataFrame
) -> list[str]:
    lines = ["### 4.4 简短结论", ""]
    later_returns = _available_later_values(backtest, "annual_excess_return")
    if later_returns:
        best_spec, best_value = max(later_returns, key=lambda item: item[1])
        worst_spec, worst_value = min(later_returns, key=lambda item: item[1])
        if len(later_returns) == 1:
            lines.append(
                f"- 2021—2025 年只有 {best_spec.name} 有年化相差数值，为 {_format_percent(best_value)}。"
            )
        else:
            lines.append(
                f"- 2021—2025 年年化相差最高的是 {best_spec.name}（{_format_percent(best_value)}），最低的是 {worst_spec.name}（{_format_percent(worst_value)}）。"
            )
        positive = sum(value > 0 for _, value in later_returns)
        negative = sum(value < 0 for _, value in later_returns)
        other = len(FACTOR_SPECS) - positive - negative
        lines.append(
            f"- 2021—2025 年年化相差为正的有 {positive} 项，为负的有 {negative} 项；其余 {other} 项为 0 或没有数值。"
        )
    else:
        lines.append("- 2021—2025 年年化相差没有可用数值。")

    later_ic = _available_later_values(ic_summary, "ic_mean")
    if later_ic:
        strongest_spec, strongest_ic = max(later_ic, key=lambda item: abs(item[1]))
        lines.append(
            f"- 2021—2025 年 IC 绝对值最高的是 {strongest_spec.name}，IC 为 {_format_decimal(strongest_ic)}。"
        )
    else:
        lines.append("- 2021—2025 年 IC 没有可用数值。")

    panic = _one_row(
        ic_summary,
        "J06_D10_VOLATILITY_PANIC_20D",
        "period",
        "full",
        ic_summary.attrs["name"],
    )
    panic_observations = _number(_cell(panic, "observations"))
    if panic_observations is not None and panic_observations < 120:
        lines.append(
            f"- 波动率加剧—惊恐因子共有 {_format_count(panic_observations)} 个月的原频率 IC；本地中证全指日线在部分区间缺值，缺失月份没有按 0 处理。"
        )
    return lines


def build_result_section(result_dir: Path) -> str:
    ic_summary = _read_csv(
        result_dir / "original_frequency_ic_summary.csv",
        {"factor", "period", "ic_mean", "icir_raw"},
    )
    backtest = _read_csv(
        result_dir / "original_frequency_backtest_summary.csv",
        {
            "factor",
            "period",
            "direction",
            "annual_return",
            "benchmark_annual_return",
            "annual_excess_return",
            "max_drawdown",
        },
    )
    yearly = _read_csv(
        result_dir / "original_frequency_yearly_backtest.csv",
        {
            "factor",
            "year",
            "direction",
            "strategy_return",
            "benchmark_return",
            "excess_return",
            "periods",
        },
    )
    status = _read_csv(
        result_dir / "factor_status.csv",
        {"factor", "name", "signal_frequency", "calculated"},
    )
    for frame, name in [
        (ic_summary, "original_frequency_ic_summary.csv"),
        (backtest, "original_frequency_backtest_summary.csv"),
        (yearly, "original_frequency_yearly_backtest.csv"),
    ]:
        frame.attrs["name"] = name

    ic_summary["period"] = ic_summary["period"].astype("string").str.strip()
    backtest["period"] = backtest["period"].astype("string").str.strip()
    yearly["year"] = pd.to_numeric(yearly["year"], errors="coerce")
    _validate_status(status)

    lines = [
        REPORT_START,
        "",
        "> 本节数字由 fill_june_report.py 直接读取正式回测 CSV 生成；空值写作“—”，不按 0 处理。",
        "",
        *_build_ic_table(ic_summary),
        "",
        *_build_backtest_table(backtest),
        "",
        *_build_yearly_table(yearly),
        "",
        *_build_conclusion(ic_summary, backtest),
    ]
    return "\n".join(lines).rstrip()


def fill_report(result_dir: Path, report_path: Path) -> str:
    result_dir = Path(result_dir)
    report_path = Path(report_path)
    if not report_path.is_file():
        raise FileNotFoundError(f"找不到报告：{report_path}")

    original = report_path.read_text(encoding="utf-8")
    if original.count(REPORT_START) != 1 or original.count(REPORT_END) != 1:
        raise ValueError("报告中必须各有一个第四节和第五节标题")
    start = original.index(REPORT_START)
    end = original.index(REPORT_END)
    if start >= end:
        raise ValueError("报告的第四节必须位于第五节之前")

    section = build_result_section(result_dir)
    updated = original[:start].rstrip() + "\n\n" + section + "\n\n" + original[end:]

    report_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        newline="\n",
        dir=report_path.parent,
        prefix=f".{report_path.name}.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        handle.write(updated)
        temporary_path = Path(handle.name)
    try:
        os.replace(temporary_path, report_path)
    finally:
        temporary_path.unlink(missing_ok=True)
    return updated


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--result-dir", type=Path, default=DEFAULT_RESULT_DIR)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT_PATH)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    fill_report(args.result_dir, args.report)
    print(f"已更新报告：{args.report}")


if __name__ == "__main__":
    main()
