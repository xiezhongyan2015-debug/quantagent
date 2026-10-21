# -*- coding: utf-8 -*-
"""整理 2022—2026 行情期的因子 IC、ICIR 和总排名。

这里的 2022、2023、2024、2026 仅表示《因子日历》的出版版本。
全部因子统一使用 2022-01-04 至 2026-05-29 的实际行情计算结果。
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, Mapping, Sequence

import numpy as np
import pandas as pd

from historical_backtest_core import BacktestSettings, _periods, normalize_frequency
from historical_existing_duckdb_factors import resolve_result_code


BASE_DIR = Path(__file__).resolve().parent
HIST_DIR = BASE_DIR / "historical_calendar_backtest_results"
STATUS_PATH = HIST_DIR / "formula_unit_status.csv"
FIVE_YEAR_ROOT = HIST_DIR / "five_year_2022_2026_20260724"
FORMULA_PATH = FIVE_YEAR_ROOT / "formula_units_raw" / "unit_rebalance_returns.parquet"
MISSING_CORRECTED_PATH = (
    FIVE_YEAR_ROOT
    / "missing_units_corrected_final"
    / "unit_rebalance_returns.parquet"
)
EXISTING_LOCAL_PATH = (
    FIVE_YEAR_ROOT / "existing_local_raw" / "unit_rebalance_returns.parquet"
)
ATTENTION_PATH = (
    FIVE_YEAR_ROOT / "existing_attention_raw" / "unit_rebalance_returns.parquet"
)

REUSED_HISTORY_PATHS = (
    BASE_DIR / "minute_completion_results" / "final" / "original_frequency_ic.csv",
    BASE_DIR / "july_august_factor_results" / "final" / "original_frequency_ic.csv",
    BASE_DIR / "september_december_factor_results" / "final" / "original_frequency_ic.csv",
)
ALL57_INFO_PATH = (
    BASE_DIR
    / ".codex-artifact"
    / "2026_rank_check"
    / "all57_results"
    / "all57_2026_ic_raw.csv"
)
ALL57_HISTORY_PATH = (
    BASE_DIR
    / ".codex-artifact"
    / "2026_rank_check"
    / "all57_results"
    / "all57_combined_raw_ic.csv"
)
COMPLETION_INFO_PATH = (
    BASE_DIR / "minute_completion_results" / "final" / "minute_completion_2026_ic_raw.csv"
)
COMPLETION_HISTORY_PATH = REUSED_HISTORY_PATHS[0]
RECENT_ALL57_ROOT = BASE_DIR / ".codex-artifact" / "2026_rank_check" / "all57_runs"
RECENT_COMPLETION_ROOT = (
    BASE_DIR / ".codex-artifact" / "2026_rank_check" / "minute_completion_runs"
)
RECENT_OLD_ROOT = HIST_DIR / "2026_extension_direct_supported_20260724" / "old_codes_raw"
CALENDAR_PATH = (
    BASE_DIR
    / ".codex-artifact"
    / "2026_rank_check"
    / "qlib_overlay"
    / "calendars"
    / "day.txt"
)
OUTPUT_DIR = BASE_DIR / "outputs" / "icir_ic_ranking_market_2022_2026" / "data"

MARKET_START = pd.Timestamp("2022-01-04")
REPORT_START = pd.Timestamp("2022-01-01")
MARKET_END = pd.Timestamp("2026-05-29")
HISTORY_END = pd.Timestamp("2025-12-31")
TARGET_MONTHS = {
    2022: [f"2022-{month:02d}" for month in range(1, 13)],
    2023: [f"2023-{month:02d}" for month in range(1, 13)],
    2024: [f"2024-{month:02d}" for month in range(1, 13)],
    2025: [f"2025-{month:02d}" for month in range(1, 13)],
    2026: [f"2026-{month:02d}" for month in range(1, 6)],
}
TARGET_MONTH_SET = {month for values in TARGET_MONTHS.values() for month in values}
TARGET_YEARS = tuple(TARGET_MONTHS)
EXPECTED_FREQUENCY_COUNTS = {
    "daily": 209,
    "every_5_days": 2,
    "monthly": 76,
    "weekly_tuesday": 1,
}
EXPECTED_PERIOD_COUNTS = {
    "daily": 212,
    "every_5_days": 212,
    "monthly": 53,
    "weekly_tuesday": 194,
}

# 经原公式和计算程序复核，以下编号属于同一计算。
# 公式相同但取样频率不同的项目不在此表中。
CONFIRMED_DUPLICATE_ALIASES = {
    "HF0089__monthly": "HF0018__monthly",
    "Y2026_EXTRA_COMPLETION51__J09_D11_NAIVE_ACTIVE_SHARE": "HF0276__daily",
    "Y2026_EXTRA_COMPLETION51__J11_D20_CONSISTENT_BUYING": "HF0228__daily",
    "Y2026_EXTRA_ALL57__M16_LARGE_UP_JUMP_VOL": "HF0229__daily",
}
EXACT_SERIES_ALIAS = "HF0089__monthly"

IDENTITY_COLUMNS = [
    "unit_id",
    "factor_code_2026",
    "script_factor_2026",
    "factor_name",
    "frequency",
    "source_kind",
    "source_2026",
    "calendar_editions",
    "formula_mode",
    "calculation_method",
]
DATE_COLUMNS = ["signal_date", "entry_date", "exit_date"]


@dataclass(frozen=True)
class FormalMaps:
    all57_factor_to_unit: dict[str, str]
    all57_code_to_unit: dict[str, str]
    completion_factor_to_unit: dict[str, str]
    formal_historical_units: set[str]
    extra_all57_units: set[str]
    extra_completion_units: set[str]

    @property
    def extra_units(self) -> set[str]:
        return self.extra_all57_units | self.extra_completion_units


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    return parser.parse_args(argv)


def relative_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(BASE_DIR.resolve()))
    except ValueError:
        return str(path.resolve())


def require_file(path: Path) -> Path:
    if not path.is_file():
        raise FileNotFoundError(path)
    return path


def read_csv(path: Path) -> pd.DataFrame:
    return pd.read_csv(require_file(path), low_memory=False, encoding="utf-8-sig")


def read_table(path: Path) -> pd.DataFrame:
    require_file(path)
    if path.suffix.lower() == ".parquet":
        return pd.read_parquet(path)
    return read_csv(path)


def clean_text(value: object) -> str:
    if pd.isna(value):
        return ""
    return str(value).strip()


def join_unique(values: Iterable[object], separator: str = " | ") -> str:
    result: list[str] = []
    for value in values:
        text = clean_text(value)
        if text and text not in result:
            result.append(text)
    return separator.join(result)


def edition_values(value: object) -> list[str]:
    text = clean_text(value).replace("，", "|").replace(",", "|")
    result: list[str] = []
    for part in text.split("|"):
        item = part.strip()
        if item and item not in result:
            result.append(item)
    return result


def merge_calendar_editions(*values: object) -> str:
    editions: set[str] = set()
    for value in values:
        editions.update(edition_values(value))
    return " | ".join(
        sorted(editions, key=lambda item: int(item) if item.isdigit() else 9999)
    )


def add_unique_text(existing: str, value: str) -> str:
    return join_unique([*existing.split(" | "), value])


def make_catalog(status: pd.DataFrame) -> dict[str, dict[str, str]]:
    catalog: dict[str, dict[str, str]] = {}
    for row in status.itertuples(index=False):
        frequency = clean_text(getattr(row, "frequency", "")) or clean_text(
            getattr(row, "normalized_frequency", "")
        )
        unit_id = str(row.unit_id)
        catalog[unit_id] = {
            "unit_id": unit_id,
            "factor_code_2026": "",
            "script_factor_2026": "",
            "factor_name": clean_text(row.representative_name),
            "frequency": normalize_frequency(frequency),
            "source_kind": "历史因子日历",
            "source_2026": "",
            "calendar_editions": merge_calendar_editions(row.years),
            "formula_mode": clean_text(row.mode),
            "calculation_method": clean_text(getattr(row, "calculation_method", "")),
        }
    return catalog


def reference_maps(status: pd.DataFrame) -> tuple[dict[str, str], dict[str, str]]:
    rows = status[status["existing_ref"].notna()].copy()
    rows["existing_ref"] = rows["existing_ref"].astype(str).str.strip()
    if rows["existing_ref"].duplicated().any():
        detail = rows.loc[
            rows["existing_ref"].duplicated(keep=False), ["unit_id", "existing_ref"]
        ].to_dict("records")
        raise RuntimeError(f"existing_ref 不是唯一值：{detail}")
    exact = dict(zip(rows["existing_ref"], rows["unit_id"].astype(str)))
    resolved: dict[str, str] = {}
    for ref, unit_id in exact.items():
        code = resolve_result_code(ref)
        other = resolved.get(code)
        if other is not None and other != unit_id:
            raise RuntimeError(f"结果代码对应多个计算项：{code} -> {other}, {unit_id}")
        resolved[code] = unit_id
    return exact, resolved


def update_publication_metadata(
    catalog: dict[str, dict[str, str]],
    unit_id: str,
    *,
    factor_code: str,
    script_factor: str,
    factor_name: str,
    frequency: str,
    source: str,
    formula_mode: str,
    calculation_method: str,
) -> None:
    if unit_id not in catalog:
        catalog[unit_id] = {
            "unit_id": unit_id,
            "factor_code_2026": factor_code,
            "script_factor_2026": script_factor,
            "factor_name": factor_name or factor_code,
            "frequency": normalize_frequency(frequency),
            "source_kind": "2026因子日历独立因子",
            "source_2026": source,
            "calendar_editions": "2026",
            "formula_mode": formula_mode,
            "calculation_method": calculation_method,
        }
        return

    item = catalog[unit_id]
    if factor_code:
        item["factor_code_2026"] = add_unique_text(item["factor_code_2026"], factor_code)
    if script_factor:
        item["script_factor_2026"] = add_unique_text(
            item["script_factor_2026"], script_factor
        )
    if source:
        item["source_2026"] = add_unique_text(item["source_2026"], source)
    if factor_name and not item["factor_name"]:
        item["factor_name"] = factor_name
    item["calendar_editions"] = merge_calendar_editions(
        item["calendar_editions"], "2026"
    )


def build_formal_maps(
    status: pd.DataFrame,
    catalog: dict[str, dict[str, str]],
) -> FormalMaps:
    exact_refs, _ = reference_maps(status)
    all57 = read_csv(ALL57_INFO_PATH)
    required = {"factor", "script_factor", "signal_frequency"}
    if not required.issubset(all57.columns):
        raise RuntimeError(f"{ALL57_INFO_PATH} 缺少列：{sorted(required - set(all57.columns))}")
    pair_rows = all57.drop_duplicates(["factor", "script_factor"]).copy()
    all57_factor_to_unit: dict[str, str] = {}
    all57_code_to_unit: dict[str, str] = {}
    formal_historical_units: set[str] = set()
    extra_all57_units: set[str] = set()
    for row in pair_rows.itertuples(index=False):
        factor = str(row.factor)
        script_factor = str(row.script_factor)
        script_match = exact_refs.get(script_factor)
        factor_match = exact_refs.get(factor)
        if script_match and factor_match and script_match != factor_match:
            raise RuntimeError(
                f"2026因子代码对应两个历史计算项：{factor}/{script_factor}"
            )
        unit_id = script_match or factor_match
        if unit_id is None:
            unit_id = f"Y2026_EXTRA_ALL57__{factor}"
            extra_all57_units.add(unit_id)
        else:
            formal_historical_units.add(unit_id)
        if factor in all57_factor_to_unit and all57_factor_to_unit[factor] != unit_id:
            raise RuntimeError(f"2026因子代码重复：{factor}")
        all57_factor_to_unit[factor] = unit_id
        for code in (factor, script_factor):
            previous = all57_code_to_unit.get(code)
            if previous is not None and previous != unit_id:
                raise RuntimeError(f"2026程序代码对应两个计算项：{code}")
            all57_code_to_unit[code] = unit_id
        calculation = f"{clean_text(getattr(row, 'source_month', ''))}原频率IC"
        update_publication_metadata(
            catalog,
            unit_id,
            factor_code=factor,
            script_factor=script_factor,
            factor_name=clean_text(getattr(row, "factor_name", "")),
            frequency=clean_text(getattr(row, "signal_frequency", "")),
            source="正式57",
            formula_mode="正式复测",
            calculation_method=calculation,
        )

    completion = read_csv(COMPLETION_INFO_PATH)
    required = {"factor", "signal_frequency"}
    if not required.issubset(completion.columns):
        raise RuntimeError(
            f"{COMPLETION_INFO_PATH} 缺少列：{sorted(required - set(completion.columns))}"
        )
    completion_factor_to_unit: dict[str, str] = {}
    extra_completion_units: set[str] = set()
    for row in completion.drop_duplicates("factor").itertuples(index=False):
        factor = str(row.factor)
        unit_id = exact_refs.get(factor)
        if unit_id is None:
            unit_id = f"Y2026_EXTRA_COMPLETION51__{factor}"
            extra_completion_units.add(unit_id)
        else:
            formal_historical_units.add(unit_id)
        completion_factor_to_unit[factor] = unit_id
        calculation = join_unique(
            [
                clean_text(getattr(row, "formula_label", "")),
                clean_text(getattr(row, "source_group", "")),
            ]
        )
        update_publication_metadata(
            catalog,
            unit_id,
            factor_code=factor,
            script_factor=factor,
            factor_name=clean_text(getattr(row, "factor_name", "")),
            frequency=clean_text(getattr(row, "signal_frequency", "")),
            source="正式51",
            formula_mode=clean_text(getattr(row, "formula_mode", "")),
            calculation_method=calculation,
        )

    result = FormalMaps(
        all57_factor_to_unit=all57_factor_to_unit,
        all57_code_to_unit=all57_code_to_unit,
        completion_factor_to_unit=completion_factor_to_unit,
        formal_historical_units=formal_historical_units,
        extra_all57_units=extra_all57_units,
        extra_completion_units=extra_completion_units,
    )
    if len(result.extra_all57_units) != 15 or len(result.extra_completion_units) != 23:
        raise RuntimeError(
            "2026独立编号数量变化："
            f"正式57={len(result.extra_all57_units)}，正式51={len(result.extra_completion_units)}"
        )
    return result


def build_expected_periods() -> dict[str, pd.DataFrame]:
    calendar = pd.DatetimeIndex(
        pd.to_datetime(pd.read_csv(require_file(CALENDAR_PATH), header=None)[0])
    )
    calendar = calendar[
        (calendar >= pd.Timestamp("2015-01-01")) & (calendar <= MARKET_END)
    ]
    settings = BacktestSettings(
        start="2015-01-01",
        backtest_start="2016-01-04",
        end=MARKET_END.strftime("%Y-%m-%d"),
        report_start=REPORT_START.strftime("%Y-%m-%d"),
        report_end=MARKET_END.strftime("%Y-%m-%d"),
    )
    result: dict[str, pd.DataFrame] = {}
    for frequency in EXPECTED_PERIOD_COUNTS:
        rows = [
            (calendar[signal], calendar[entry], calendar[exit_index])
            for signal, entry, exit_index in _periods(calendar, frequency, settings)
            if calendar[entry] >= REPORT_START
            and calendar[entry] <= MARKET_END
            and calendar[exit_index] <= MARKET_END
        ]
        frame = pd.DataFrame(rows, columns=DATE_COLUMNS)
        expected_count = EXPECTED_PERIOD_COUNTS[frequency]
        if len(frame) != expected_count:
            raise RuntimeError(
                f"{frequency} 应有 {expected_count} 个回测区间，实际为 {len(frame)}"
            )
        frame["frequency"] = frequency
        result[frequency] = frame
    return result


def expected_period_table(expected: Mapping[str, pd.DataFrame]) -> pd.DataFrame:
    result = pd.concat(expected.values(), ignore_index=True)
    if result.duplicated(["frequency", *DATE_COLUMNS]).any():
        raise RuntimeError("预期回测日期出现重复")
    result["expected_period"] = True
    return result


def prepare_candidate(
    frame: pd.DataFrame,
    unit_ids: pd.Series,
    *,
    catalog: Mapping[str, Mapping[str, str]],
    expected_table: pd.DataFrame,
    source_path: Path,
    source_label: str,
    allowed_units: set[str] | None = None,
    exit_start: pd.Timestamp = REPORT_START,
    exit_end: pd.Timestamp = MARKET_END,
) -> pd.DataFrame:
    required = {*DATE_COLUMNS, "ic"}
    missing = required - set(frame.columns)
    if missing:
        raise RuntimeError(f"{source_path} 缺少列：{sorted(missing)}")
    if len(unit_ids) != len(frame):
        raise RuntimeError(f"{source_path} 的编号数量与数据行数不同")

    result = pd.DataFrame(index=frame.index)
    result["unit_id"] = unit_ids.astype("string")
    for column in DATE_COLUMNS:
        result[column] = pd.to_datetime(frame[column], errors="coerce")
    result["ic"] = pd.to_numeric(frame["ic"], errors="coerce")
    result = result[result["unit_id"].notna()].copy()
    if allowed_units is not None:
        result = result[result["unit_id"].isin(allowed_units)].copy()
    result["frequency"] = result["unit_id"].map(
        {unit_id: item["frequency"] for unit_id, item in catalog.items()}
    )
    if result["frequency"].isna().any():
        missing_units = sorted(result.loc[result["frequency"].isna(), "unit_id"].unique())
        raise RuntimeError(f"{source_path} 中的编号缺少频率：{missing_units}")
    result = result[
        result["exit_date"].between(exit_start, exit_end, inclusive="both")
    ].copy()
    result = result.merge(
        expected_table,
        on=["frequency", *DATE_COLUMNS],
        how="inner",
        validate="many_to_one",
    )
    result = result.drop(columns="expected_period")
    duplicate = result.duplicated(["unit_id", *DATE_COLUMNS], keep=False)
    if duplicate.any():
        detail = result.loc[duplicate, ["unit_id", *DATE_COLUMNS]].head(20)
        raise RuntimeError(
            f"{source_path} 同一计算项和日期出现重复：{detail.to_dict('records')}"
        )
    if not np.isfinite(result["ic"]).all():
        detail = result.loc[~np.isfinite(result["ic"]), ["unit_id", *DATE_COLUMNS]].head(20)
        raise RuntimeError(f"{source_path} 的IC含无效数值：{detail.to_dict('records')}")
    result["return_month"] = result["exit_date"].dt.strftime("%Y-%m")
    result["source_file"] = relative_path(source_path)
    result["source_label"] = source_label
    return result[
        [
            "unit_id",
            "frequency",
            *DATE_COLUMNS,
            "return_month",
            "ic",
            "source_file",
            "source_label",
        ]
    ].reset_index(drop=True)


def map_result_factor(
    frame: pd.DataFrame,
    resolved_refs: Mapping[str, str],
) -> pd.Series:
    if "factor" not in frame.columns:
        raise RuntimeError("原始结果缺少 factor 列")
    return frame["factor"].astype("string").map(resolved_refs)


def load_formula_units(
    status: pd.DataFrame,
    catalog: Mapping[str, Mapping[str, str]],
    expected_table: pd.DataFrame,
) -> tuple[pd.DataFrame, dict[str, object]]:
    base = read_table(FORMULA_PATH)
    base_ids = set(base["unit_id"].astype(str))
    expected_formula_ids = set(
        status.loc[~status["calculation_method"].eq("existing_duckdb"), "unit_id"].astype(str)
    )
    if base_ids != expected_formula_ids or len(base_ids) != 159:
        raise RuntimeError(
            "五年公式结果编号不符："
            f"应有159个，实际{len(base_ids)}个；"
            f"缺少{sorted(expected_formula_ids - base_ids)}；"
            f"多出{sorted(base_ids - expected_formula_ids)}"
        )

    corrected = read_table(MISSING_CORRECTED_PATH)
    corrected_ids = set(corrected["unit_id"].astype(str))
    expected_corrected_ids = set(
        status.loc[status["calculation_method"].eq("missing_minute"), "unit_id"].astype(str)
    )
    if corrected_ids != expected_corrected_ids or len(corrected_ids) != 12:
        raise RuntimeError(
            "分钟数据修正结果编号不符："
            f"应有12个，实际{len(corrected_ids)}个；"
            f"缺少{sorted(expected_corrected_ids - corrected_ids)}；"
            f"多出{sorted(corrected_ids - expected_corrected_ids)}"
        )

    base = base[~base["unit_id"].astype(str).isin(corrected_ids)].copy()
    base_rows = prepare_candidate(
        base,
        base["unit_id"],
        catalog=catalog,
        expected_table=expected_table,
        source_path=FORMULA_PATH,
        source_label="公式单元五年重算",
        allowed_units=expected_formula_ids - corrected_ids,
    )
    corrected_rows = prepare_candidate(
        corrected,
        corrected["unit_id"],
        catalog=catalog,
        expected_table=expected_table,
        source_path=MISSING_CORRECTED_PATH,
        source_label="分钟数据缺口修正后重算",
        allowed_units=corrected_ids,
    )
    result = pd.concat([base_rows, corrected_rows], ignore_index=True)
    return result, {
        "formula_unit_count": len(expected_formula_ids),
        "corrected_unit_count": len(corrected_ids),
        "formula_rows": len(result),
    }


def load_existing_history(
    status: pd.DataFrame,
    catalog: Mapping[str, Mapping[str, str]],
    expected_table: pd.DataFrame,
    resolved_refs: Mapping[str, str],
) -> tuple[pd.DataFrame, dict[str, object]]:
    existing_ids = set(
        status.loc[status["calculation_method"].eq("existing_duckdb"), "unit_id"].astype(str)
    )
    if len(existing_ids) != 95:
        raise RuntimeError(f"已有程序计算项应为95个，实际为{len(existing_ids)}个")

    local = read_table(EXISTING_LOCAL_PATH)
    local_ids = set(local["unit_id"].astype(str))
    if len(local_ids) != 43 or not local_ids.issubset(existing_ids):
        raise RuntimeError(
            f"本次重新计算的已有程序项目应为43个，实际为{len(local_ids)}个"
        )
    local_rows = prepare_candidate(
        local,
        local["unit_id"],
        catalog=catalog,
        expected_table=expected_table,
        source_path=EXISTING_LOCAL_PATH,
        source_label="已有程序2022至2025重新计算",
        allowed_units=local_ids,
        exit_end=HISTORY_END,
    )

    attention = read_table(ATTENTION_PATH)
    if "unit_id" not in attention.columns:
        raise RuntimeError(f"{ATTENTION_PATH} 缺少 unit_id 列")
    attention_unit_ids = attention["unit_id"].astype("string")
    attention_ids = set(attention_unit_ids.dropna().astype(str))
    if attention_ids != {"HF0227__monthly"}:
        raise RuntimeError(f"注意力衰减因子编号不符：{sorted(attention_ids)}")
    attention_rows = prepare_candidate(
        attention,
        attention_unit_ids,
        catalog=catalog,
        expected_table=expected_table,
        source_path=ATTENTION_PATH,
        source_label="注意力衰减因子2022至2025重新计算",
        allowed_units={"HF0227__monthly"},
        exit_end=HISTORY_END,
    )

    rerun_ids = local_ids | attention_ids
    reusable_ids = existing_ids - rerun_ids
    if len(rerun_ids) != 44 or len(reusable_ids) != 51:
        raise RuntimeError(
            f"已有程序项目分组不符：重新计算{len(rerun_ids)}个，可复用{len(reusable_ids)}个"
        )
    reused_parts: list[pd.DataFrame] = []
    for path in REUSED_HISTORY_PATHS:
        frame = read_table(path)
        unit_ids = map_result_factor(frame, resolved_refs)
        selected = prepare_candidate(
            frame,
            unit_ids,
            catalog=catalog,
            expected_table=expected_table,
            source_path=path,
            source_label="已有原始结果2022至2025",
            allowed_units=reusable_ids,
            exit_end=HISTORY_END,
        )
        if not selected.empty:
            reused_parts.append(selected)
    if not reused_parts:
        raise RuntimeError("没有读到可复用的2022至2025原始IC")
    reused_rows = pd.concat(reused_parts, ignore_index=True)
    duplicate = reused_rows.duplicated(["unit_id", *DATE_COLUMNS], keep=False)
    if duplicate.any():
        detail = reused_rows.loc[duplicate, ["unit_id", *DATE_COLUMNS]].head(20)
        raise RuntimeError(f"可复用历史结果出现重复：{detail.to_dict('records')}")
    actual_reused_ids = set(reused_rows["unit_id"].astype(str))
    if actual_reused_ids != reusable_ids:
        raise RuntimeError(
            "可复用历史结果编号不完整："
            f"缺少{sorted(reusable_ids - actual_reused_ids)}；"
            f"多出{sorted(actual_reused_ids - reusable_ids)}"
        )

    result = pd.concat([local_rows, attention_rows, reused_rows], ignore_index=True)
    return result, {
        "existing_unit_count": len(existing_ids),
        "local_rerun_unit_count": len(local_ids),
        "attention_rerun_unit_count": len(attention_ids),
        "reused_history_unit_count": len(reusable_ids),
        "existing_history_rows": len(result),
        "existing_ids": existing_ids,
        "rerun_ids": rerun_ids,
        "reusable_ids": reusable_ids,
    }


def load_recent_source_files(
    root: Path,
    *,
    code_map: Mapping[str, str],
    fallback_map: Mapping[str, str],
    catalog: Mapping[str, Mapping[str, str]],
    expected_table: pd.DataFrame,
    allowed_units: set[str],
    source_label: str,
) -> list[pd.DataFrame]:
    paths = sorted(root.rglob("original_frequency_ic.csv"))
    if not paths:
        raise FileNotFoundError(f"{root} 下没有 original_frequency_ic.csv")
    parts: list[pd.DataFrame] = []
    for path in paths:
        frame = read_table(path)
        factor = frame["factor"].astype("string")
        unit_ids = factor.map(code_map).fillna(factor.map(fallback_map))
        selected = prepare_candidate(
            frame,
            unit_ids,
            catalog=catalog,
            expected_table=expected_table,
            source_path=path,
            source_label=source_label,
            allowed_units=allowed_units,
            exit_start=pd.Timestamp("2026-01-01"),
            exit_end=MARKET_END,
        )
        if not selected.empty:
            parts.append(selected)
    return parts


def load_recent_2026(
    catalog: Mapping[str, Mapping[str, str]],
    expected_table: pd.DataFrame,
    resolved_refs: Mapping[str, str],
    formal_maps: FormalMaps,
    existing_ids: set[str],
) -> tuple[pd.DataFrame, dict[str, object]]:
    allowed = existing_ids | formal_maps.extra_units
    parts: list[pd.DataFrame] = []
    parts.extend(
        load_recent_source_files(
            RECENT_ALL57_ROOT,
            code_map=formal_maps.all57_code_to_unit,
            fallback_map=resolved_refs,
            catalog=catalog,
            expected_table=expected_table,
            allowed_units=allowed,
            source_label="2026正式57原始结果",
        )
    )
    parts.extend(
        load_recent_source_files(
            RECENT_COMPLETION_ROOT,
            code_map=formal_maps.completion_factor_to_unit,
            fallback_map=resolved_refs,
            catalog=catalog,
            expected_table=expected_table,
            allowed_units=allowed,
            source_label="2026正式51原始结果",
        )
    )
    parts.extend(
        load_recent_source_files(
            RECENT_OLD_ROOT,
            code_map=resolved_refs,
            fallback_map=resolved_refs,
            catalog=catalog,
            expected_table=expected_table,
            allowed_units=allowed,
            source_label="2026旧程序补充结果",
        )
    )
    if not parts:
        raise RuntimeError("没有读到2026年原始IC")
    result = pd.concat(parts, ignore_index=True)
    duplicate = result.duplicated(["unit_id", *DATE_COLUMNS], keep=False)
    if duplicate.any():
        detail = result.loc[duplicate, ["unit_id", *DATE_COLUMNS]].head(20)
        raise RuntimeError(f"2026年原始结果出现重复：{detail.to_dict('records')}")
    actual_ids = set(result["unit_id"].astype(str))
    missing = allowed - actual_ids
    if missing:
        raise RuntimeError(f"2026年原始结果缺少编号：{sorted(missing)}")
    return result, {
        "recent_existing_unit_count": len(actual_ids & existing_ids),
        "recent_extra_unit_count": len(actual_ids & formal_maps.extra_units),
        "recent_rows": len(result),
    }


def load_extra_history(
    catalog: Mapping[str, Mapping[str, str]],
    expected_table: pd.DataFrame,
    formal_maps: FormalMaps,
) -> tuple[pd.DataFrame, dict[str, object]]:
    all57 = read_table(ALL57_HISTORY_PATH)
    all57_ids = all57["factor"].astype("string").map(formal_maps.all57_factor_to_unit)
    all57_rows = prepare_candidate(
        all57,
        all57_ids,
        catalog=catalog,
        expected_table=expected_table,
        source_path=ALL57_HISTORY_PATH,
        source_label="2026独立因子正式57历史原始结果",
        allowed_units=formal_maps.extra_all57_units,
        exit_end=HISTORY_END,
    )

    completion = read_table(COMPLETION_HISTORY_PATH)
    completion_ids = completion["factor"].astype("string").map(
        formal_maps.completion_factor_to_unit
    )
    completion_rows = prepare_candidate(
        completion,
        completion_ids,
        catalog=catalog,
        expected_table=expected_table,
        source_path=COMPLETION_HISTORY_PATH,
        source_label="2026独立因子正式51历史原始结果",
        allowed_units=formal_maps.extra_completion_units,
        exit_end=HISTORY_END,
    )
    result = pd.concat([all57_rows, completion_rows], ignore_index=True)
    actual_ids = set(result["unit_id"].astype(str))
    if actual_ids != formal_maps.extra_units:
        raise RuntimeError(
            "2026独立因子历史结果编号不完整："
            f"缺少{sorted(formal_maps.extra_units - actual_ids)}；"
            f"多出{sorted(actual_ids - formal_maps.extra_units)}"
        )
    return result, {
        "extra_unit_count_before_alias_merge": len(actual_ids),
        "extra_history_rows": len(result),
    }


def merge_confirmed_aliases(
    raw: pd.DataFrame,
    catalog: dict[str, dict[str, str]],
) -> tuple[pd.DataFrame, dict[str, object]]:
    result = raw.copy()
    details: list[dict[str, object]] = []
    for alias_id, canonical_id in CONFIRMED_DUPLICATE_ALIASES.items():
        ids = set(result["unit_id"].astype(str))
        if alias_id not in ids or canonical_id not in ids:
            raise RuntimeError(f"重复编号缺少数据：{alias_id} -> {canonical_id}")
        alias_meta = catalog.get(alias_id)
        canonical_meta = catalog.get(canonical_id)
        if alias_meta is None or canonical_meta is None:
            raise RuntimeError(f"重复编号缺少资料：{alias_id} -> {canonical_id}")
        if alias_meta["frequency"] != canonical_meta["frequency"]:
            raise RuntimeError(f"重复编号频率不同：{alias_id} -> {canonical_id}")

        alias_monthly = (
            result[result["unit_id"].eq(alias_id)]
            .groupby("return_month")["ic"]
            .mean()
            .reindex(sorted(TARGET_MONTH_SET))
        )
        canonical_monthly = (
            result[result["unit_id"].eq(canonical_id)]
            .groupby("return_month")["ic"]
            .mean()
            .reindex(sorted(TARGET_MONTH_SET))
        )
        if alias_monthly.isna().any() or canonical_monthly.isna().any():
            raise RuntimeError(f"重复编号月份不完整：{alias_id} -> {canonical_id}")
        same_series = bool(
            np.allclose(
                alias_monthly.to_numpy(),
                canonical_monthly.to_numpy(),
                rtol=1e-12,
                atol=1e-14,
            )
        )
        if alias_id == EXACT_SERIES_ALIAS and not same_series:
            raise RuntimeError(f"应完全相同的结果发生变化：{alias_id} -> {canonical_id}")

        canonical_meta["factor_name"] = join_unique(
            [canonical_meta["factor_name"], alias_meta["factor_name"]], " / "
        )
        canonical_meta["calendar_editions"] = merge_calendar_editions(
            canonical_meta["calendar_editions"], alias_meta["calendar_editions"]
        )
        for field in ("factor_code_2026", "script_factor_2026", "source_2026"):
            canonical_meta[field] = join_unique(
                [canonical_meta[field], alias_meta[field]]
            )
        if canonical_id in {"HF0276__daily", "HF0228__daily"}:
            canonical_meta["formula_mode"] = "trial"
            canonical_meta["calculation_method"] = join_unique(
                [canonical_meta["calculation_method"], "2026试算同公式"]
            )
        result = result[~result["unit_id"].eq(alias_id)].copy()
        catalog.pop(alias_id)
        details.append(
            {
                "alias_id": alias_id,
                "canonical_id": canonical_id,
                "monthly_series_equal_before_merge": same_series,
            }
        )
    return result.reset_index(drop=True), {
        "merged_alias_count": len(details),
        "merged_aliases": details,
    }


def expected_calendar_editions(
    status: pd.DataFrame,
    formal_maps: FormalMaps,
) -> dict[str, set[str]]:
    result = {
        str(row.unit_id): set(edition_values(row.years))
        for row in status.itertuples(index=False)
    }
    for unit_id in formal_maps.extra_units:
        result[unit_id] = {"2026"}
    for unit_id in formal_maps.formal_historical_units:
        result[unit_id].add("2026")
    for alias_id, canonical_id in CONFIRMED_DUPLICATE_ALIASES.items():
        result[canonical_id].update(result[alias_id])
        result.pop(alias_id)
    return result


def validate_calendar_editions(
    catalog: Mapping[str, Mapping[str, str]],
    expected: Mapping[str, set[str]],
) -> dict[str, object]:
    actual = {
        unit_id: set(edition_values(item["calendar_editions"]))
        for unit_id, item in catalog.items()
    }
    if actual != expected:
        differing = sorted(
            unit_id
            for unit_id in set(actual) | set(expected)
            if actual.get(unit_id) != expected.get(unit_id)
        )
        detail = [
            {
                "unit_id": unit_id,
                "expected": sorted(expected.get(unit_id, set())),
                "actual": sorted(actual.get(unit_id, set())),
            }
            for unit_id in differing[:30]
        ]
        raise RuntimeError(f"因子日历出版版本资料不符：{detail}")
    edition_2026_ids = {unit_id for unit_id, values in actual.items() if "2026" in values}
    if len(edition_2026_ids) != 108:
        raise RuntimeError(f"含2026出版版本的因子应为108个，实际为{len(edition_2026_ids)}个")
    return {
        "edition_metadata_exact": True,
        "edition_2026_factor_count": len(edition_2026_ids),
    }


def icir(values: pd.Series) -> float:
    clean = pd.to_numeric(values, errors="coerce").dropna()
    if len(clean) < 2:
        return np.nan
    standard_deviation = clean.std(ddof=1)
    if not np.isfinite(standard_deviation) or standard_deviation == 0:
        return np.nan
    return float(clean.mean() / standard_deviation)


def rank_and_score(values: pd.Series) -> tuple[pd.Series, pd.Series]:
    ranks = values.abs().rank(method="min", ascending=False)
    valid_count = int(values.notna().sum())
    if valid_count <= 1:
        scores = pd.Series(np.where(values.notna(), 100.0, np.nan), index=values.index)
    else:
        scores = (valid_count - ranks) / (valid_count - 1) * 100.0
    return ranks, scores


def validate_raw_periods(
    raw: pd.DataFrame,
    catalog: Mapping[str, Mapping[str, str]],
    expected: Mapping[str, pd.DataFrame],
) -> dict[str, object]:
    if raw.duplicated(["unit_id", *DATE_COLUMNS]).any():
        detail = raw.loc[
            raw.duplicated(["unit_id", *DATE_COLUMNS], keep=False),
            ["unit_id", *DATE_COLUMNS],
        ].head(20)
        raise RuntimeError(f"最终逐期IC出现重复：{detail.to_dict('records')}")
    if not np.isfinite(raw["ic"]).all():
        raise RuntimeError("最终逐期IC含无效数值")
    unit_ids = set(raw["unit_id"].astype(str))
    if unit_ids != set(catalog):
        raise RuntimeError(
            "最终数据与资料编号不同："
            f"缺少{sorted(set(catalog) - unit_ids)}；"
            f"多出{sorted(unit_ids - set(catalog))}"
        )
    if len(unit_ids) != 288:
        raise RuntimeError(f"最终因子应为288个，实际为{len(unit_ids)}个")

    metadata = pd.DataFrame([catalog[unit_id] for unit_id in sorted(unit_ids)])
    frequency_counts = metadata["frequency"].value_counts().to_dict()
    if frequency_counts != EXPECTED_FREQUENCY_COUNTS:
        raise RuntimeError(
            f"最终频率数量不符：应为{EXPECTED_FREQUENCY_COUNTS}，实际为{frequency_counts}"
        )

    expected_keys: dict[str, set[tuple[pd.Timestamp, pd.Timestamp, pd.Timestamp]]] = {}
    for frequency, frame in expected.items():
        expected_keys[frequency] = set(frame[DATE_COLUMNS].itertuples(index=False, name=None))
    errors: list[str] = []
    for unit_id, rows in raw.groupby("unit_id", sort=False):
        frequency = catalog[str(unit_id)]["frequency"]
        actual = set(rows[DATE_COLUMNS].itertuples(index=False, name=None))
        wanted = expected_keys[frequency]
        if actual != wanted:
            errors.append(
                f"{unit_id}: 实际{len(actual)}，应有{len(wanted)}，"
                f"缺少{len(wanted - actual)}，多出{len(actual - wanted)}"
            )
    if errors:
        raise RuntimeError("逐期日期不完整：" + "；".join(errors[:30]))

    if len(raw) != 48_954:
        raise RuntimeError(f"最终逐期IC应为48954行，实际为{len(raw)}行")
    raw_months = set(raw["return_month"].astype(str))
    if raw_months != TARGET_MONTH_SET:
        raise RuntimeError(
            f"逐期IC月份不完整：缺少{sorted(TARGET_MONTH_SET - raw_months)}；"
            f"多出{sorted(raw_months - TARGET_MONTH_SET)}"
        )
    return {
        "factor_count": len(unit_ids),
        "frequency_counts": frequency_counts,
        "raw_rows": len(raw),
        "nonfinite_ic_rows": int((~np.isfinite(raw["ic"])).sum()),
        "duplicate_period_rows": int(
            raw.duplicated(["unit_id", *DATE_COLUMNS], keep=False).sum()
        ),
    }


def aggregate_monthly(
    raw: pd.DataFrame,
    catalog: Mapping[str, Mapping[str, str]],
) -> pd.DataFrame:
    grouped = (
        raw.groupby(["unit_id", "return_month"], as_index=False)
        .agg(
            monthly_ic=("ic", "mean"),
            raw_observations=("ic", "size"),
            source_files=("source_file", join_unique),
            source_labels=("source_label", join_unique),
        )
        .sort_values(["unit_id", "return_month"], kind="stable")
        .reset_index(drop=True)
    )
    metadata = pd.DataFrame(catalog.values())
    grouped = grouped.merge(metadata, on="unit_id", how="left", validate="many_to_one")
    grouped["year"] = grouped["return_month"].str.slice(0, 4).astype(int)
    result = grouped[
        [
            *IDENTITY_COLUMNS,
            "year",
            "return_month",
            "monthly_ic",
            "raw_observations",
            "source_files",
            "source_labels",
        ]
    ]
    if len(result) != 15_264:
        raise RuntimeError(f"月度IC应为15264行，实际为{len(result)}行")
    month_counts = result.groupby("unit_id")["return_month"].nunique()
    if not month_counts.eq(53).all():
        bad = month_counts[~month_counts.eq(53)].to_dict()
        raise RuntimeError(f"以下因子没有53个月IC：{bad}")
    if not np.isfinite(result["monthly_ic"]).all():
        raise RuntimeError("月度IC含无效数值")
    return result


def build_annual(monthly: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    metadata = monthly[IDENTITY_COLUMNS].drop_duplicates("unit_id").set_index("unit_id")
    for unit_id, unit_rows in monthly.groupby("unit_id"):
        for year in TARGET_YEARS:
            year_rows = unit_rows[unit_rows["year"].eq(year)].sort_values("return_month")
            values = year_rows["monthly_ic"]
            actual_months = set(year_rows["return_month"])
            wanted_months = set(TARGET_MONTHS[year])
            row = metadata.loc[unit_id].to_dict()
            row.update(
                {
                    "unit_id": unit_id,
                    "year": year,
                    "expected_months": len(wanted_months),
                    "month_count": len(actual_months),
                    "ic": float(values.mean()),
                    "ic_std": float(values.std(ddof=1)),
                    "icir": icir(values),
                    "first_month": year_rows["return_month"].min(),
                    "last_month": year_rows["return_month"].max(),
                    "complete": actual_months == wanted_months,
                }
            )
            rows.append(row)
    result = pd.DataFrame(rows)[
        [
            *IDENTITY_COLUMNS,
            "year",
            "expected_months",
            "month_count",
            "ic",
            "ic_std",
            "icir",
            "first_month",
            "last_month",
            "complete",
        ]
    ]
    if len(result) != 1_440:
        raise RuntimeError(f"年度明细应为1440行，实际为{len(result)}行")
    if not result["complete"].all():
        bad = result.loc[~result["complete"], ["unit_id", "year", "month_count"]]
        raise RuntimeError(f"年度月份不完整：{bad.to_dict('records')}")
    if not np.isfinite(result[["ic", "ic_std", "icir"]].to_numpy(dtype=float)).all():
        raise RuntimeError("年度IC或ICIR含无效数值")
    return result


def build_all_factor_ranking(
    monthly: pd.DataFrame,
    annual: pd.DataFrame,
) -> pd.DataFrame:
    metadata = monthly[IDENTITY_COLUMNS].drop_duplicates("unit_id").set_index("unit_id")
    rows: list[dict[str, object]] = []
    for unit_id, unit_rows in monthly.groupby("unit_id"):
        annual_rows = annual[annual["unit_id"].eq(unit_id)].set_index("year")
        values = unit_rows.sort_values("return_month")["monthly_ic"]
        row = metadata.loc[unit_id].to_dict()
        row["unit_id"] = unit_id
        complete_count = 0
        for year in TARGET_YEARS:
            year_row = annual_rows.loc[year]
            row[f"months_{year}"] = int(year_row["month_count"])
            row[f"ic_{year}"] = float(year_row["ic"])
            row[f"icir_{year}"] = float(year_row["icir"])
            complete_count += int(bool(year_row["complete"]))
        row["years_complete"] = complete_count
        row["total_months"] = int(unit_rows["return_month"].nunique())
        row["total_ic"] = float(values.mean())
        row["total_icir"] = icir(values)
        row["abs_total_ic"] = abs(row["total_ic"])
        row["abs_total_icir"] = abs(row["total_icir"])
        row["direction"] = "正向" if row["total_ic"] >= 0 else "反向"
        row["first_month"] = unit_rows["return_month"].min()
        row["last_month"] = unit_rows["return_month"].max()
        row["official_eligible"] = bool(
            complete_count == len(TARGET_YEARS)
            and row["total_months"] == 53
            and np.isfinite(row["total_ic"])
            and np.isfinite(row["total_icir"])
        )
        rows.append(row)

    result = pd.DataFrame(rows)
    result["abs_ic_rank_all"], ic_score = rank_and_score(result["total_ic"])
    result["abs_icir_rank_all"], icir_score = rank_and_score(result["total_icir"])
    result["composite_score_all"] = 0.5 * ic_score + 0.5 * icir_score
    result["composite_rank_all"] = result["composite_score_all"].rank(
        method="min", ascending=False
    )
    result = result.sort_values(
        ["official_eligible", "composite_rank_all", "unit_id"],
        ascending=[False, True, True],
    ).reset_index(drop=True)
    year_columns: list[str] = []
    for year in TARGET_YEARS:
        year_columns.extend([f"months_{year}", f"ic_{year}", f"icir_{year}"])
    columns = [
        *IDENTITY_COLUMNS,
        "years_complete",
        *year_columns,
        "total_months",
        "total_ic",
        "total_icir",
        "abs_total_ic",
        "abs_total_icir",
        "abs_ic_rank_all",
        "abs_icir_rank_all",
        "composite_score_all",
        "composite_rank_all",
        "official_eligible",
        "direction",
        "first_month",
        "last_month",
    ]
    result = result[columns]
    if len(result) != 288 or not result["official_eligible"].all():
        raise RuntimeError(
            f"正式排名应有288个完整因子，实际完整{int(result['official_eligible'].sum())}个"
        )
    numeric = result[["total_ic", "total_icir", "composite_score_all"]].to_numpy(float)
    if not np.isfinite(numeric).all():
        raise RuntimeError("总IC、总ICIR或综合分含无效数值")
    return result


def build_official_ranking(all_factors: pd.DataFrame) -> pd.DataFrame:
    result = all_factors[all_factors["official_eligible"]].copy()
    result["abs_ic_rank"], result["ic_score"] = rank_and_score(result["total_ic"])
    result["abs_icir_rank"], result["icir_score"] = rank_and_score(
        result["total_icir"]
    )
    result["composite_score"] = 0.5 * result["ic_score"] + 0.5 * result["icir_score"]
    result["composite_rank"] = result["composite_score"].rank(
        method="min", ascending=False
    )
    result = result.sort_values(
        ["composite_rank", "abs_ic_rank", "abs_icir_rank", "unit_id"]
    ).reset_index(drop=True)
    year_columns: list[str] = []
    for year in TARGET_YEARS:
        year_columns.extend([f"months_{year}", f"ic_{year}", f"icir_{year}"])
    columns = [
        "composite_rank",
        *IDENTITY_COLUMNS,
        "direction",
        *year_columns,
        "total_months",
        "total_ic",
        "total_icir",
        "abs_total_ic",
        "abs_total_icir",
        "abs_ic_rank",
        "abs_icir_rank",
        "ic_score",
        "icir_score",
        "composite_score",
        "first_month",
        "last_month",
    ]
    return result[columns]


def check_row(
    check_id: str,
    item: str,
    expected: object,
    actual: object,
    passed: bool,
    detail: str = "",
) -> dict[str, object]:
    return {
        "check_id": check_id,
        "check_item": item,
        "expected": expected,
        "actual": actual,
        "status": "通过" if passed else "失败",
        "detail": detail,
    }


def build_checks(
    *,
    status: pd.DataFrame,
    source_counts: Mapping[str, object],
    alias_details: Mapping[str, object],
    raw_details: Mapping[str, object],
    monthly: pd.DataFrame,
    annual: pd.DataFrame,
    official: pd.DataFrame,
    catalog: Mapping[str, Mapping[str, str]],
) -> pd.DataFrame:
    expected_year_counts = {2022: 12, 2023: 12, 2024: 12, 2025: 12, 2026: 5}
    actual_year_counts = (
        monthly.groupby(["unit_id", "year"])["return_month"].nunique().groupby("year").unique()
    )
    year_complete = all(
        year in actual_year_counts.index
        and actual_year_counts.loc[year].tolist() == [month_count]
        for year, month_count in expected_year_counts.items()
    )
    editions = {
        edition
        for item in catalog.values()
        for edition in edition_values(item["calendar_editions"])
    }
    rows = [
        check_row("C01", "已完成历史计算项", 254, len(status), len(status) == 254),
        check_row(
            "C02",
            "五年公式计算项",
            159,
            source_counts["formula_unit_count"],
            source_counts["formula_unit_count"] == 159,
        ),
        check_row(
            "C03",
            "分钟数据修正后覆盖项目",
            12,
            source_counts["corrected_unit_count"],
            source_counts["corrected_unit_count"] == 12,
        ),
        check_row(
            "C04",
            "已有程序重新计算项目",
            44,
            source_counts["local_rerun_unit_count"]
            + source_counts["attention_rerun_unit_count"],
            source_counts["local_rerun_unit_count"]
            + source_counts["attention_rerun_unit_count"]
            == 44,
        ),
        check_row(
            "C05",
            "复用2022至2025原始结果项目",
            51,
            source_counts["reused_history_unit_count"],
            source_counts["reused_history_unit_count"] == 51,
        ),
        check_row(
            "C06",
            "已有程序2026结果项目",
            95,
            source_counts["recent_existing_unit_count"],
            source_counts["recent_existing_unit_count"] == 95,
        ),
        check_row(
            "C07",
            "2026独立编号（合并重复前）",
            38,
            source_counts["extra_unit_count_before_alias_merge"],
            source_counts["extra_unit_count_before_alias_merge"] == 38,
        ),
        check_row(
            "C08",
            "已确认重复编号合并数",
            4,
            alias_details["merged_alias_count"],
            alias_details["merged_alias_count"] == 4,
        ),
        check_row(
            "C09",
            "最终因子数",
            288,
            raw_details["factor_count"],
            raw_details["factor_count"] == 288,
        ),
        check_row(
            "C10",
            "最终频率数量",
            json.dumps(EXPECTED_FREQUENCY_COUNTS, ensure_ascii=False, sort_keys=True),
            json.dumps(raw_details["frequency_counts"], ensure_ascii=False, sort_keys=True),
            raw_details["frequency_counts"] == EXPECTED_FREQUENCY_COUNTS,
        ),
        check_row(
            "C11",
            "逐期IC行数",
            48_954,
            raw_details["raw_rows"],
            raw_details["raw_rows"] == 48_954,
        ),
        check_row(
            "C12",
            "逐期IC无效值",
            0,
            raw_details["nonfinite_ic_rows"],
            raw_details["nonfinite_ic_rows"] == 0,
        ),
        check_row(
            "C13",
            "逐期日期重复行",
            0,
            raw_details["duplicate_period_rows"],
            raw_details["duplicate_period_rows"] == 0,
        ),
        check_row("C14", "月度IC行数", 15_264, len(monthly), len(monthly) == 15_264),
        check_row("C15", "年度明细行数", 1_440, len(annual), len(annual) == 1_440),
        check_row(
            "C16",
            "各年月份数",
            "12/12/12/12/5",
            "12/12/12/12/5" if year_complete else "不完整",
            year_complete,
        ),
        check_row(
            "C17",
            "正式总榜因子数",
            288,
            len(official),
            len(official) == 288,
        ),
        check_row(
            "C18",
            "出版版本字段不含行情年份2025",
            "仅2022/2023/2024/2026",
            " | ".join(sorted(editions)),
            editions.issubset({"2022", "2023", "2024", "2026"}) and "2025" not in editions,
        ),
        check_row(
            "C19",
            "含2026出版版本的因子编号",
            "108个且逐项正确",
            f"{raw_details['edition_2026_factor_count']}个",
            raw_details["edition_metadata_exact"]
            and raw_details["edition_2026_factor_count"] == 108,
        ),
    ]
    result = pd.DataFrame(rows)
    if result["status"].eq("失败").any():
        failed = result[result["status"].eq("失败")]
        raise RuntimeError(
            "结果检查未通过：" + failed.to_json(orient="records", force_ascii=False)
        )
    return result


def write_csv(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    frame.to_csv(temporary, index=False, encoding="utf-8-sig")
    temporary.replace(path)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    status_all = read_csv(STATUS_PATH)
    status = status_all[status_all["status"].eq("completed")].copy()
    status["unit_id"] = status["unit_id"].astype(str)
    if len(status) != 254:
        raise RuntimeError(f"已完成历史计算项应为254个，实际为{len(status)}个")

    catalog = make_catalog(status)
    formal_maps = build_formal_maps(status, catalog)
    expected_editions = expected_calendar_editions(status, formal_maps)
    _, resolved_refs = reference_maps(status)
    expected = build_expected_periods()
    expected_table = expected_period_table(expected)

    formula_raw, formula_details = load_formula_units(status, catalog, expected_table)
    existing_history, existing_details = load_existing_history(
        status, catalog, expected_table, resolved_refs
    )
    extra_history, extra_details = load_extra_history(
        catalog, expected_table, formal_maps
    )
    recent, recent_details = load_recent_2026(
        catalog,
        expected_table,
        resolved_refs,
        formal_maps,
        set(existing_details["existing_ids"]),
    )
    existing_recent = recent[
        recent["unit_id"].isin(existing_details["existing_ids"])
    ].copy()
    extra_recent = recent[recent["unit_id"].isin(formal_maps.extra_units)].copy()

    raw = pd.concat(
        [formula_raw, existing_history, existing_recent, extra_history, extra_recent],
        ignore_index=True,
    )
    raw, alias_details = merge_confirmed_aliases(raw, catalog)
    edition_details = validate_calendar_editions(catalog, expected_editions)
    raw = raw.sort_values(["unit_id", "signal_date", "exit_date"], kind="stable").reset_index(
        drop=True
    )
    raw_details = validate_raw_periods(raw, catalog, expected)
    raw_details.update(edition_details)
    monthly = aggregate_monthly(raw, catalog)
    annual = build_annual(monthly)
    all_factors = build_all_factor_ranking(monthly, annual)
    official = build_official_ranking(all_factors)

    source_counts: dict[str, object] = {}
    for details in (formula_details, existing_details, extra_details, recent_details):
        for key, value in details.items():
            if not isinstance(value, set):
                source_counts[key] = value
    checks = build_checks(
        status=status,
        source_counts=source_counts,
        alias_details=alias_details,
        raw_details=raw_details,
        monthly=monthly,
        annual=annual,
        official=official,
        catalog=catalog,
    )

    raw_output = raw.copy()
    for column in DATE_COLUMNS:
        raw_output[column] = raw_output[column].dt.strftime("%Y-%m-%d")
    outputs = {
        "raw_period_ic.csv": raw_output,
        "monthly_ic.csv": monthly,
        "annual_detail.csv": annual,
        "all_factors_ranking.csv": all_factors,
        "official_five_year_ranking.csv": official,
        "checks.csv": checks,
    }
    for filename, frame in outputs.items():
        write_csv(frame, output_dir / filename)

    manifest = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "output_directory": relative_path(output_dir),
        "target_market_period": {
            "start": MARKET_START.strftime("%Y-%m-%d"),
            "end": MARKET_END.strftime("%Y-%m-%d"),
            "months": 53,
            "note": "所有因子使用同一段实际行情；日历出版版本只作来源说明。",
        },
        "target_months": TARGET_MONTHS,
        "methodology": {
            "period_selection": "按signal_date、entry_date、exit_date三个日期精确核对；日频和每5个交易日频率均每5个交易日取一次；周频按周二信号并跳过持仓重叠；月频为月末信号、下月首日开盘买入、月末收盘卖出。",
            "monthly_ic": "按收益退出月份，对同月逐期横截面IC取算术平均。",
            "annual_ic": "当年月IC的算术平均。",
            "annual_icir": "当年月IC均值除以月IC样本标准差，样本标准差ddof=1。",
            "total_ic": "2022年1月至2026年5月共53个月IC的算术平均。",
            "total_icir": "53个月IC均值除以月IC样本标准差，样本标准差ddof=1，不做年化。",
            "ranking": "按总IC绝对值和总ICIR绝对值分别排名，各占综合分50%；原始正负号保留。",
            "calendar_editions": "2022、2023、2024、2026仅为因子日历出版版本，不参与行情时间筛选。",
            "level2": "不含需要Level2数据的因子。",
        },
        "sources": {
            "status": relative_path(STATUS_PATH),
            "formula": relative_path(FORMULA_PATH),
            "missing_corrected": relative_path(MISSING_CORRECTED_PATH),
            "existing_local": relative_path(EXISTING_LOCAL_PATH),
            "attention": relative_path(ATTENTION_PATH),
            "reused_history": [relative_path(path) for path in REUSED_HISTORY_PATHS],
            "all57_history": relative_path(ALL57_HISTORY_PATH),
            "completion_history": relative_path(COMPLETION_HISTORY_PATH),
            "recent_all57": relative_path(RECENT_ALL57_ROOT),
            "recent_completion": relative_path(RECENT_COMPLETION_ROOT),
            "recent_old": relative_path(RECENT_OLD_ROOT),
            "trading_calendar": relative_path(CALENDAR_PATH),
        },
        "counts": {
            **source_counts,
            **raw_details,
            "monthly_rows": len(monthly),
            "annual_rows": len(annual),
            "official_ranking_rows": len(official),
            "failed_checks": int(checks["status"].eq("失败").sum()),
        },
        "merged_aliases": alias_details["merged_aliases"],
    }
    manifest_path = output_dir / "ranking_manifest.json"
    temporary_manifest = manifest_path.with_name(manifest_path.name + ".tmp")
    temporary_manifest.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary_manifest.replace(manifest_path)
    print(json.dumps(manifest["counts"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
