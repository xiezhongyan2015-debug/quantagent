# -*- coding: utf-8 -*-
"""汇总历史因子日历的分批回测结果。

脚本允许部分批次或部分结果文件尚未生成。普通分钟因子按
``formula_key + normalized_frequency`` 对应计算单元，再展开回原始记录；
Level2 记录只登记为暂不回测，不会混入年度收益。
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import duckdb
import numpy as np
import pandas as pd

from build_formula_units import normalize_frequency


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_RESULT_DIR = BASE_DIR / "historical_calendar_backtest_results"

STATUS_NAMES = ("unit_status.csv",)
SUMMARY_NAMES = ("unit_backtest_summary.csv", "unit_summary.csv")
YEARLY_NAMES = ("unit_yearly_returns.csv", "yearly_returns.csv", "unit_yearly.csv")
RETURNS_NAMES = (
    "unit_rebalance_returns.parquet",
    "unit_rebalance_returns.csv",
    "unit_returns.parquet",
    "unit_returns.csv",
    "returns.parquet",
    "returns.csv",
)
ALL_RESULT_NAMES = frozenset((*STATUS_NAMES, *SUMMARY_NAMES, *YEARLY_NAMES, *RETURNS_NAMES))


@dataclass(frozen=True)
class SummaryPaths:
    result_dir: Path = DEFAULT_RESULT_DIR

    @property
    def mapping(self) -> Path:
        return self.result_dir / "factor_mapping.csv"

    @property
    def units(self) -> Path:
        return self.result_dir / "formula_units.csv"

    @property
    def audit(self) -> Path:
        return self.result_dir / "formula_support_audit.csv"

    @property
    def level2(self) -> Path:
        return self.result_dir / "level2_excluded.csv"

    @property
    def runs(self) -> Path:
        return self.result_dir / "runs"


@dataclass
class BatchData:
    key: str
    directory: Path
    modified_time: float
    status: pd.DataFrame
    summary: pd.DataFrame
    yearly: pd.DataFrame
    return_summary: pd.DataFrame
    derived_yearly: pd.DataFrame


def _read_csv(path: Path) -> pd.DataFrame:
    return pd.read_csv(path, encoding="utf-8-sig")


def _first_existing(directory: Path, names: Iterable[str]) -> Path | None:
    for name in names:
        candidate = directory / name
        if candidate.is_file():
            return candidate
    return None


def discover_batch_directories(runs_dir: Path) -> list[Path]:
    """寻找至少包含一个结果文件的目录。"""

    if not runs_dir.exists():
        return []
    directories = {
        path.parent
        for path in runs_dir.rglob("*")
        if path.is_file() and path.name in ALL_RESULT_NAMES
    }
    return sorted(directories, key=lambda path: path.as_posix().lower())


def _validate_unit_table(frame: pd.DataFrame, label: str, many: bool = False) -> pd.DataFrame:
    if frame.empty:
        return frame
    if "unit_id" not in frame.columns:
        raise ValueError(f"{label}缺少unit_id字段")
    result = frame.copy()
    result["unit_id"] = result["unit_id"].astype(str)
    if not many and result["unit_id"].duplicated().any():
        duplicates = sorted(result.loc[result["unit_id"].duplicated(), "unit_id"].unique())
        raise ValueError(f"{label}存在重复unit_id：{', '.join(duplicates[:5])}")
    return result


def _sql_path(path: Path) -> str:
    return str(path.resolve()).replace("\\", "/").replace("'", "''")


def _returns_relation(path: Path) -> str:
    escaped = _sql_path(path)
    if path.suffix.lower() == ".parquet":
        return f"read_parquet('{escaped}')"
    return f"read_csv_auto('{escaped}', header = true)"


def read_return_aggregates(path: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    """只读取收益文件所需字段，统计行数、日期和缺失年度收益。"""

    relation = _returns_relation(path)
    connection = duckdb.connect(":memory:")
    try:
        columns = {
            row[0]
            for row in connection.execute(f"DESCRIBE SELECT * FROM {relation}").fetchall()
        }
        if "unit_id" not in columns:
            raise ValueError(f"{path}缺少unit_id字段")
        signal_expression = (
            "min(try_cast(signal_date AS TIMESTAMP)) AS first_signal_date"
            if "signal_date" in columns
            else "NULL::TIMESTAMP AS first_signal_date"
        )
        exit_expression = (
            "max(try_cast(exit_date AS TIMESTAMP)) AS last_exit_date"
            if "exit_date" in columns
            else "NULL::TIMESTAMP AS last_exit_date"
        )
        summary = connection.execute(
            f"""
            SELECT cast(unit_id AS VARCHAR) AS unit_id,
                   count(*)::BIGINT AS return_rows,
                   {signal_expression},
                   {exit_expression}
            FROM {relation}
            GROUP BY unit_id
            """
        ).fetchdf()

        required = {"exit_date", "selected_net_return", "benchmark_return"}
        if not required.issubset(columns):
            yearly = pd.DataFrame(
                columns=[
                    "unit_id",
                    "year",
                    "strategy_return",
                    "benchmark_return",
                    "excess_return",
                    "periods",
                    "mean_ic",
                ]
            )
        else:
            ic_expression = "avg(ic)" if "ic" in columns else "NULL::DOUBLE"
            yearly = connection.execute(
                f"""
                SELECT cast(unit_id AS VARCHAR) AS unit_id,
                       year(try_cast(exit_date AS TIMESTAMP))::INTEGER AS year,
                       exp(sum(ln(1.0 + selected_net_return))) - 1.0 AS strategy_return,
                       exp(sum(ln(1.0 + benchmark_return))) - 1.0 AS benchmark_return,
                       (exp(sum(ln(1.0 + selected_net_return))) -
                        exp(sum(ln(1.0 + benchmark_return)))) AS excess_return,
                       count(*)::BIGINT AS periods,
                       {ic_expression} AS mean_ic
                FROM {relation}
                WHERE try_cast(exit_date AS TIMESTAMP) IS NOT NULL
                  AND selected_net_return IS NOT NULL
                  AND benchmark_return IS NOT NULL
                  AND selected_net_return > -1.0
                  AND benchmark_return > -1.0
                GROUP BY unit_id, year(try_cast(exit_date AS TIMESTAMP))
                ORDER BY unit_id, year
                """
            ).fetchdf()
    finally:
        connection.close()
    return summary, yearly


def read_batch(directory: Path, runs_dir: Path) -> BatchData:
    paths = [path for path in directory.iterdir() if path.is_file() and path.name in ALL_RESULT_NAMES]
    modified = max((path.stat().st_mtime for path in paths), default=0.0)
    key = directory.relative_to(runs_dir).as_posix()

    status_path = _first_existing(directory, STATUS_NAMES)
    summary_path = _first_existing(directory, SUMMARY_NAMES)
    yearly_path = _first_existing(directory, YEARLY_NAMES)
    returns_path = _first_existing(directory, RETURNS_NAMES)

    status = _validate_unit_table(
        _read_csv(status_path) if status_path else pd.DataFrame(), f"{key}/status"
    )
    summary = _validate_unit_table(
        _read_csv(summary_path) if summary_path else pd.DataFrame(), f"{key}/summary"
    )
    yearly = _validate_unit_table(
        _read_csv(yearly_path) if yearly_path else pd.DataFrame(),
        f"{key}/yearly",
        many=True,
    )
    if returns_path:
        return_summary, derived_yearly = read_return_aggregates(returns_path)
    else:
        return_summary = pd.DataFrame(
            columns=["unit_id", "return_rows", "first_signal_date", "last_exit_date"]
        )
        derived_yearly = pd.DataFrame()
    return BatchData(
        key,
        directory,
        modified,
        status,
        summary,
        yearly,
        return_summary,
        derived_yearly,
    )


def read_batches(runs_dir: Path) -> list[BatchData]:
    return [read_batch(directory, runs_dir) for directory in discover_batch_directories(runs_dir)]


def _unit_ids(batch: BatchData) -> set[str]:
    result: set[str] = set()
    for frame in (batch.status, batch.summary, batch.yearly, batch.return_summary):
        if not frame.empty:
            result.update(frame["unit_id"].astype(str))
    return result


def choose_batches(batches: list[BatchData]) -> pd.DataFrame:
    """同一计算单元优先使用已完成、结果更完整的批次。"""

    candidates: list[dict[str, object]] = []
    for batch in batches:
        status_by_unit = (
            batch.status.set_index("unit_id") if not batch.status.empty else pd.DataFrame()
        )
        summary_by_unit = (
            batch.summary.set_index("unit_id") if not batch.summary.empty else pd.DataFrame()
        )
        returns_by_unit = (
            batch.return_summary.set_index("unit_id")
            if not batch.return_summary.empty
            else pd.DataFrame()
        )
        yearly_units = set(batch.yearly.get("unit_id", pd.Series(dtype=str)).astype(str))
        for unit_id in sorted(_unit_ids(batch)):
            raw_status = ""
            if not status_by_unit.empty and unit_id in status_by_unit.index:
                raw_status = str(status_by_unit.loc[unit_id].get("status", "")).strip().lower()
            has_summary = not summary_by_unit.empty and unit_id in summary_by_unit.index
            has_yearly = unit_id in yearly_units
            return_rows = 0
            if not returns_by_unit.empty and unit_id in returns_by_unit.index:
                return_rows = int(returns_by_unit.loc[unit_id].get("return_rows", 0) or 0)
            inferred_status = raw_status or (
                "completed" if has_summary or has_yearly or return_rows > 0 else ""
            )
            periods = 0
            if has_summary:
                numeric_periods = pd.to_numeric(
                    summary_by_unit.loc[unit_id].get("periods", 0), errors="coerce"
                )
                periods = int(numeric_periods) if pd.notna(numeric_periods) else 0
            candidates.append(
                {
                    "unit_id": unit_id,
                    "selected_batch": batch.key,
                    "effective_status": inferred_status,
                    "completed_rank": int(inferred_status == "completed"),
                    "result_parts": int(has_summary) + int(has_yearly) + int(return_rows > 0),
                    "coverage": max(periods, return_rows),
                    "batch_modified_time": batch.modified_time,
                }
            )
    if not candidates:
        return pd.DataFrame(
            columns=["unit_id", "selected_batch", "effective_status", "batch_modified_time"]
        )
    frame = pd.DataFrame(candidates)
    frame = frame.sort_values(
        [
            "unit_id",
            "completed_rank",
            "result_parts",
            "coverage",
            "batch_modified_time",
            "selected_batch",
        ],
        kind="stable",
    )
    chosen = frame.groupby("unit_id", as_index=False, sort=True).tail(1).copy()
    batch_count = frame.groupby("unit_id")["selected_batch"].nunique()
    chosen["available_batch_count"] = chosen["unit_id"].map(batch_count).astype(int)
    return chosen.reset_index(drop=True)


def _selected_frame(
    batches: list[BatchData], chosen: pd.DataFrame, attribute: str
) -> pd.DataFrame:
    frames = []
    for batch in batches:
        frame = getattr(batch, attribute)
        if frame.empty:
            continue
        current = frame.copy()
        current["selected_batch"] = batch.key
        frames.append(current)
    if not frames or chosen.empty:
        return pd.DataFrame()
    combined = pd.concat(frames, ignore_index=True, sort=False)
    keys = chosen[["unit_id", "selected_batch"]]
    return combined.merge(keys, on=["unit_id", "selected_batch"], how="inner")


def selected_yearly(batches: list[BatchData], chosen: pd.DataFrame) -> pd.DataFrame:
    direct = _selected_frame(batches, chosen, "yearly")
    derived = _selected_frame(batches, chosen, "derived_yearly")
    if direct.empty and derived.empty:
        return pd.DataFrame(
            columns=[
                "unit_id",
                "year",
                "strategy_return",
                "benchmark_return",
                "excess_return",
                "periods",
                "mean_ic",
                "selected_batch",
            ]
        )
    direct_units = set(direct.get("unit_id", pd.Series(dtype=str)).astype(str))
    if not derived.empty:
        derived = derived[~derived["unit_id"].astype(str).isin(direct_units)]
    result = pd.concat([direct, derived], ignore_index=True, sort=False)
    if "year" in result.columns:
        result["year"] = pd.to_numeric(result["year"], errors="coerce").astype("Int64")
    return result.sort_values(["unit_id", "year"], kind="stable").reset_index(drop=True)


def _baseline_units(paths: SummaryPaths) -> pd.DataFrame:
    units = _read_csv(paths.units)
    required = {"unit_id", "formula_key", "normalized_frequency"}
    missing = required - set(units.columns)
    if missing:
        raise ValueError(f"formula_units.csv缺少字段：{', '.join(sorted(missing))}")
    if units["unit_id"].duplicated().any():
        raise ValueError("formula_units.csv存在重复unit_id")
    if paths.audit.exists():
        audit = _read_csv(paths.audit)
        audit_columns = [
            column
            for column in ("unit_id", "calculation_method", "registered_name", "method_note")
            if column in audit.columns
        ]
        if "unit_id" in audit_columns:
            units = units.merge(
                audit[audit_columns].drop_duplicates("unit_id"), on="unit_id", how="left"
            )
    return units


def build_unit_outputs(
    paths: SummaryPaths, batches: list[BatchData]
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    units = _baseline_units(paths)
    chosen = choose_batches(batches)
    status_result = _selected_frame(batches, chosen, "status")
    summary_result = _selected_frame(batches, chosen, "summary")
    returns_result = _selected_frame(batches, chosen, "return_summary")
    yearly = selected_yearly(batches, chosen)

    choice_columns = [
        column
        for column in (
            "unit_id",
            "selected_batch",
            "effective_status",
            "available_batch_count",
            "batch_modified_time",
        )
        if column in chosen.columns
    ]
    status = units.merge(chosen[choice_columns], on="unit_id", how="left")
    if not status_result.empty:
        keep = [
            column
            for column in (
                "unit_id",
                "selected_batch",
                "status",
                "finite_values",
                "periods_2022_2024",
                "error",
                "frequency",
            )
            if column in status_result.columns
        ]
        status = status.merge(status_result[keep], on=["unit_id", "selected_batch"], how="left")
    if not returns_result.empty:
        keep = [
            column
            for column in (
                "unit_id",
                "selected_batch",
                "return_rows",
                "first_signal_date",
                "last_exit_date",
            )
            if column in returns_result.columns
        ]
        status = status.merge(returns_result[keep], on=["unit_id", "selected_batch"], how="left")

    raw_result_status = status.get("status", pd.Series("", index=status.index)).fillna("").astype(str)
    inferred = status.get("effective_status", pd.Series("", index=status.index)).fillna("").astype(str)
    method = status.get("calculation_method", pd.Series("", index=status.index)).fillna("").astype(str)
    base_status = np.where(
        method.eq("unavailable"),
        "unavailable",
        np.where(method.eq("unresolved"), "unresolved", "pending"),
    )
    status["status"] = np.where(
        raw_result_status.str.len() > 0,
        raw_result_status,
        np.where(inferred.str.len() > 0, inferred, base_status),
    )
    for column in ("return_rows", "available_batch_count"):
        if column not in status:
            status[column] = 0
        status[column] = pd.to_numeric(status[column], errors="coerce").fillna(0).astype(int)
    status = status.drop(columns=["effective_status"], errors="ignore")

    unit_summary = status.copy()
    if not summary_result.empty:
        drop_from_summary = {
            "formula_key",
            "name",
            "frequency",
            "calculation_method",
            "registered_name",
            "status",
        }
        metrics = summary_result[
            [
                column
                for column in summary_result.columns
                if column in {"unit_id", "selected_batch"} or column not in drop_from_summary
            ]
        ]
        unit_summary = unit_summary.merge(
            metrics, on=["unit_id", "selected_batch"], how="left"
        )
    status = status.sort_values("unit_id", kind="stable").reset_index(drop=True)
    unit_summary = unit_summary.sort_values("unit_id", kind="stable").reset_index(drop=True)
    return status, unit_summary, yearly


def _mapping_with_units(path: Path) -> pd.DataFrame:
    mapping = _read_csv(path)
    required = {"record_id", "formula_key", "signal_frequency"}
    missing = required - set(mapping.columns)
    if missing:
        raise ValueError(f"factor_mapping.csv缺少字段：{', '.join(sorted(missing))}")
    if mapping["record_id"].duplicated().any():
        raise ValueError("factor_mapping.csv存在重复record_id")
    mapping["normalized_frequency"] = mapping["signal_frequency"].map(normalize_frequency)
    mapping["unit_id"] = (
        mapping["formula_key"].astype(str) + "__" + mapping["normalized_frequency"].astype(str)
    )
    mapping["record_scope"] = "ordinary_minute"
    return mapping


def build_factor_summary(
    paths: SummaryPaths, unit_summary: pd.DataFrame
) -> pd.DataFrame:
    mapping = _mapping_with_units(paths.mapping)
    result_columns = [
        column
        for column in unit_summary.columns
        if column
        not in {
            "formula_key",
            "normalized_frequency",
            "representative_name",
            "record_count",
            "all_names",
            "mode",
            "existing_ref",
            "preferred_kind",
            "years",
            "source_frequencies",
        }
    ]
    ordinary = mapping.merge(unit_summary[result_columns], on="unit_id", how="left")
    ordinary = ordinary.rename(columns={"status": "backtest_status"})

    level2 = _read_csv(paths.level2) if paths.level2.exists() else pd.DataFrame()
    if not level2.empty:
        if "record_id" not in level2 or "formula_key" not in level2:
            raise ValueError("level2_excluded.csv缺少record_id或formula_key")
        level2["normalized_frequency"] = level2["signal_frequency"].map(normalize_frequency)
        level2["unit_id"] = (
            level2["formula_key"].astype(str) + "__" + level2["normalized_frequency"].astype(str)
        )
        level2["record_scope"] = "level2_excluded"
        level2["backtest_status"] = "level2_excluded"
        level2["calculation_method"] = "level2_excluded"
        level2["registered_name"] = ""
        level2["selected_batch"] = ""
    result = pd.concat([ordinary, level2], ignore_index=True, sort=False)
    return result.sort_values(["record_id", "record_scope"], kind="stable").reset_index(drop=True)


def build_factor_yearly(paths: SummaryPaths, yearly: pd.DataFrame) -> pd.DataFrame:
    mapping = _mapping_with_units(paths.mapping).rename(
        columns={"year": "calendar_year", "date": "calendar_date", "name": "factor_name"}
    )
    if yearly.empty:
        columns = list(mapping.columns) + [
            "return_year",
            "strategy_return",
            "benchmark_return",
            "excess_return",
            "periods",
            "mean_ic",
            "selected_batch",
        ]
        return pd.DataFrame(columns=list(dict.fromkeys(columns)))
    annual = yearly.rename(columns={"year": "return_year"})
    result = mapping.merge(annual, on="unit_id", how="inner")
    return result.sort_values(["record_id", "return_year"], kind="stable").reset_index(drop=True)


def make_readme(
    factor_summary: pd.DataFrame,
    factor_yearly: pd.DataFrame,
    unit_status: pd.DataFrame,
    batches: list[BatchData],
) -> str:
    ordinary = int((factor_summary["record_scope"] == "ordinary_minute").sum())
    level2 = int((factor_summary["record_scope"] == "level2_excluded").sum())
    counts = unit_status["status"].value_counts().sort_index()
    status_lines = [f"- `{name}`：{int(count)} 个计算单元。" for name, count in counts.items()]
    batch_lines = [f"- `{batch.key}`" for batch in batches] or ["- 暂未发现批次结果。"]
    return "\n".join(
        [
            "# 三年因子回测汇总",
            "",
            "本目录由 `summarize_historical_factor_backtests.py` 生成。脚本允许部分批次尚未完成。",
            "",
            "## 记录数量",
            "",
            f"- 普通分钟因子原始记录：{ordinary} 条。",
            f"- Level2 暂不回测记录：{level2} 条。",
            f"- 计算单元：{len(unit_status)} 个。",
            f"- 已展开年度收益：{len(factor_yearly)} 行。",
            "",
            "## 计算单元状态",
            "",
            *status_lines,
            "",
            "## 回测方法",
            "",
            "- DuckDB 读取 Parquet；准备期为 2015—2024 年，因子方向只用 2016—2020 年 IC 确定。",
            "- 正式报告区间为 2022-01-01 至 2024-12-31；持仓必须在 2024-12-31 前卖出。",
            "- 股票池为历史中证800与中证1000成分并集；未复权收盘价不低于3元，20日平均成交额不低于500万元。",
            "- 每期分10组，报告训练期方向对应的最高或最低10%组合。",
            "- 日频因子每5个交易日调仓，信号次日开盘买入，第6个交易日开盘卖出；月频因子下月首日开盘买入、月末收盘卖出。",
            "- 买入费用0.03%，卖出费用0.13%。",
            "",
            "## 数据限制",
            "",
            "- 109条 Level2 记录按要求暂不回测。",
            "- 22个普通计算单元因缺少无风险利率、Fama-French因子、历史行业、公告时间或明确参数而标为 `unavailable`；原因见 `formula_unit_status.csv`。",
            "- `trial` 表示按现有字段作近似计算，具体替代方法见总表的 `note` 和 `method_note`。",
            "- 中证全指000985缺失日收益时，三项恐慌类因子改用完整的中证800指数000906日收益，并已在相应程序说明中记录。",
            "",
            "## 已读取批次",
            "",
            *batch_lines,
            "",
            "## 文件说明",
            "",
            "- `factor_backtest_summary.csv`：普通分钟记录与 Level2 暂不回测记录的总表。",
            "- `factor_yearly_returns.csv`：已完成普通分钟记录的分年收益；Level2 没有收益行。",
            "- `formula_unit_status.csv`：每个公式和统一频率计算单元的状态。",
            "- `formula_unit_summary.csv`：计算单元状态及回测指标。",
            "",
            "同一计算单元若出现在多个批次，优先采用状态为 completed、文件更齐全、覆盖期数更多的一份。",
            "",
        ]
    )


def build_outputs(paths: SummaryPaths) -> dict[str, object]:
    batches = read_batches(paths.runs)
    unit_status, unit_summary, yearly = build_unit_outputs(paths, batches)
    factor_summary = build_factor_summary(paths, unit_summary)
    factor_yearly = build_factor_yearly(paths, yearly)
    readme = make_readme(factor_summary, factor_yearly, unit_status, batches)
    return {
        "factor_backtest_summary.csv": factor_summary,
        "factor_yearly_returns.csv": factor_yearly,
        "formula_unit_status.csv": unit_status,
        "formula_unit_summary.csv": unit_summary,
        "README.md": readme,
    }


def write_outputs(paths: SummaryPaths, outputs: dict[str, object]) -> None:
    paths.result_dir.mkdir(parents=True, exist_ok=True)
    for name, value in outputs.items():
        destination = paths.result_dir / name
        if isinstance(value, pd.DataFrame):
            value.to_csv(destination, index=False, encoding="utf-8-sig")
        else:
            destination.write_text(str(value), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--result-dir", type=Path, default=DEFAULT_RESULT_DIR)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    paths = SummaryPaths(args.result_dir)
    outputs = build_outputs(paths)
    write_outputs(paths, outputs)
    summary = outputs["factor_backtest_summary.csv"]
    status = outputs["formula_unit_status.csv"]
    print(
        f"records={len(summary)} units={len(status)} "
        f"batches={len(discover_batch_directories(paths.runs))}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
