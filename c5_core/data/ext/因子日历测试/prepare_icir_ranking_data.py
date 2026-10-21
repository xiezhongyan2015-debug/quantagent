# -*- coding: utf-8 -*-
"""整理 2022、2023、2024、2026 四期 IC/ICIR 排名数据。"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd


BASE_DIR = Path(__file__).resolve().parent
HIST_DIR = BASE_DIR / "historical_calendar_backtest_results"
RUNS_DIR = HIST_DIR / "runs"
STATUS_PATH = HIST_DIR / "formula_unit_status.csv"

ALL57_RAW_PATH = (
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
COMPLETION51_RAW_PATH = (
    BASE_DIR / "minute_completion_results" / "final" / "minute_completion_2026_ic_raw.csv"
)
COMPLETION51_HISTORY_PATH = (
    BASE_DIR / "minute_completion_results" / "final" / "original_frequency_ic.csv"
)
EXTENSION_DIR = HIST_DIR / "2026_extension_direct_supported_20260724"
EXTENSION_COMBINED_PATH = EXTENSION_DIR / "combined_2026_ic.csv"
HF0194_CORRECTION_DIR = HIST_DIR / "HF0194_formula_fix_20260724"
HF0194_CORRECTION_PATH = HF0194_CORRECTION_DIR / "corrected_four_year_ic.csv"
HF0194_CORRECTION_CHECKS_PATH = HF0194_CORRECTION_DIR / "correction_checks.json"

OUTPUT_DIR = BASE_DIR / "outputs" / "icir_ic_ranking_2022_2024_2026" / "data"

TARGET_MONTHS = {
    2022: [f"2022-{month:02d}" for month in range(1, 13)],
    2023: [f"2023-{month:02d}" for month in range(1, 13)],
    2024: [f"2024-{month:02d}" for month in range(1, 13)],
    2026: [f"2026-{month:02d}" for month in range(1, 6)],
}
TARGET_MONTH_SET = {month for months in TARGET_MONTHS.values() for month in months}
TARGET_YEARS = tuple(TARGET_MONTHS)

# 经公式原文和实际计算代码复核后，以下编号属于同一计算。
# 统一保留历史编号，四年都采用历史计算项的取样频率。
CONFIRMED_DUPLICATE_ALIASES = {
    "HF0089__monthly": "HF0018__monthly",
    "Y2026_EXTRA_COMPLETION51__J09_D11_NAIVE_ACTIVE_SHARE": "HF0276__daily",
    "Y2026_EXTRA_COMPLETION51__J11_D20_CONSISTENT_BUYING": "HF0228__daily",
    "Y2026_EXTRA_ALL57__M16_LARGE_UP_JUMP_VOL": "HF0229__daily",
}
EXACT_SERIES_ALIAS = "HF0089__monthly"
TRIAL_MODE_CANONICAL_UNITS = {"HF0276__daily", "HF0228__daily"}

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


def relative_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(BASE_DIR.resolve()))
    except ValueError:
        return str(path.resolve())


def read_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(path)
    return pd.read_csv(path, low_memory=False)


def clean_text(value: object) -> str:
    if pd.isna(value):
        return ""
    return str(value).strip()


def join_unique(values: pd.Series, separator: str = " | ") -> str:
    result: list[str] = []
    for value in values:
        text = clean_text(value)
        if text and text not in result:
            result.append(text)
    return separator.join(result)


def add_edition(editions: str, edition: int) -> str:
    parts = [part.strip() for part in clean_text(editions).replace("，", "|").split("|")]
    parts = [part for part in parts if part]
    edition_text = str(edition)
    if edition_text not in parts:
        parts.append(edition_text)
    return " | ".join(parts)


def month_from_columns(frame: pd.DataFrame, columns: tuple[str, ...]) -> pd.Series:
    result = pd.Series(pd.NA, index=frame.index, dtype="string")
    for column in columns:
        if column not in frame.columns:
            continue
        if column == "return_month":
            candidate = frame[column].astype("string").str.slice(0, 7)
        else:
            candidate = pd.to_datetime(frame[column], errors="coerce").dt.strftime("%Y-%m")
        result = result.fillna(candidate)
    return result


def new_raw_rows(
    frame: pd.DataFrame,
    unit_ids: pd.Series,
    month_columns: tuple[str, ...],
    source_path: Path,
    source_label: str,
) -> pd.DataFrame:
    if "ic" not in frame.columns:
        raise RuntimeError(f"{source_path} 缺少 ic 列")
    result = pd.DataFrame(index=frame.index)
    result["unit_id"] = unit_ids.astype("string")
    result["return_month"] = month_from_columns(frame, month_columns)
    result["ic"] = pd.to_numeric(frame["ic"], errors="coerce")
    result["source_file"] = relative_path(source_path)
    result["source_label"] = source_label
    result = result[
        result["unit_id"].notna()
        & result["return_month"].isin(TARGET_MONTH_SET)
        & np.isfinite(result["ic"])
    ].copy()
    return result.reset_index(drop=True)


def rank_and_score(values: pd.Series) -> tuple[pd.Series, pd.Series]:
    ranks = values.abs().rank(method="min", ascending=False)
    valid_count = int(values.notna().sum())
    if valid_count <= 1:
        scores = pd.Series(np.where(values.notna(), 100.0, np.nan), index=values.index)
    else:
        scores = (valid_count - ranks) / (valid_count - 1) * 100.0
    return ranks, scores


def icir(values: pd.Series) -> float:
    clean = pd.to_numeric(values, errors="coerce").dropna()
    if len(clean) < 2:
        return np.nan
    standard_deviation = clean.std(ddof=1)
    if not np.isfinite(standard_deviation) or standard_deviation == 0:
        return np.nan
    return float(clean.mean() / standard_deviation)


def make_catalog(status: pd.DataFrame) -> dict[str, dict[str, str]]:
    catalog: dict[str, dict[str, str]] = {}
    for row in status.itertuples(index=False):
        frequency = clean_text(getattr(row, "frequency", "")) or clean_text(
            getattr(row, "normalized_frequency", "")
        )
        catalog[str(row.unit_id)] = {
            "unit_id": str(row.unit_id),
            "factor_code_2026": "",
            "script_factor_2026": "",
            "factor_name": clean_text(row.representative_name),
            "frequency": frequency,
            "source_kind": "历史因子日历",
            "source_2026": "",
            "calendar_editions": clean_text(row.years),
            "formula_mode": clean_text(row.mode),
            "calculation_method": clean_text(getattr(row, "calculation_method", "")),
        }
    return catalog


def merge_calendar_editions(*values: str) -> str:
    editions: set[str] = set()
    for value in values:
        editions.update(
            part.strip()
            for part in clean_text(value).replace("，", "|").split("|")
            if part.strip()
        )
    return " | ".join(sorted(editions, key=lambda item: int(item) if item.isdigit() else 9999))


def merge_confirmed_duplicate_aliases(
    raw: pd.DataFrame, catalog: dict[str, dict[str, str]]
) -> tuple[pd.DataFrame, dict[str, object]]:
    present_ids = set(raw["unit_id"].astype(str))
    merged_rows: list[dict[str, object]] = []
    result = raw.copy()

    for alias_id, canonical_id in CONFIRMED_DUPLICATE_ALIASES.items():
        if alias_id not in present_ids or canonical_id not in present_ids:
            raise RuntimeError(f"重复编号缺少数据：{alias_id} -> {canonical_id}")
        alias_meta = catalog.get(alias_id)
        canonical_meta = catalog.get(canonical_id)
        if alias_meta is None or canonical_meta is None:
            raise RuntimeError(f"重复编号缺少资料：{alias_id} -> {canonical_id}")
        if clean_text(alias_meta["frequency"]) != clean_text(canonical_meta["frequency"]):
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
            raise RuntimeError(f"应完全相同的计算结果发生变化：{alias_id} -> {canonical_id}")

        names = [canonical_meta["factor_name"], alias_meta["factor_name"]]
        canonical_meta["factor_name"] = " / ".join(
            dict.fromkeys(clean_text(name) for name in names if clean_text(name))
        )
        canonical_meta["calendar_editions"] = merge_calendar_editions(
            canonical_meta["calendar_editions"], alias_meta["calendar_editions"]
        )
        for field in ("factor_code_2026", "script_factor_2026"):
            alias_value = clean_text(alias_meta[field])
            if alias_value and not alias_value.startswith("HF"):
                canonical_meta[field] = alias_value
        if canonical_id in TRIAL_MODE_CANONICAL_UNITS:
            canonical_meta["formula_mode"] = "trial"
            canonical_meta["calculation_method"] = "试算 / 每5个交易日取IC"
        canonical_meta["source_2026"] = "2026补算"

        result = result[~result["unit_id"].eq(alias_id)].copy()
        catalog.pop(alias_id, None)
        merged_rows.append(
            {
                "alias_id": alias_id,
                "canonical_id": canonical_id,
                "monthly_series_equal_before_merge": same_series,
            }
        )

    return result.reset_index(drop=True), {
        "merged_alias_count": len(merged_rows),
        "merged_aliases": merged_rows,
        "historical_alias_count": sum(
            alias_id.startswith("HF") for alias_id in CONFIRMED_DUPLICATE_ALIASES
        ),
        "independent_2026_alias_count": sum(
            alias_id.startswith("Y2026_EXTRA") for alias_id in CONFIRMED_DUPLICATE_ALIASES
        ),
    }


def update_2026_metadata(
    catalog: dict[str, dict[str, str]],
    unit_id: str,
    factor_code: str,
    script_factor: str,
    factor_name: str,
    source_2026: str,
    frequency: str = "",
    formula_mode: str = "",
    calculation_method: str = "",
    source_kind: str = "2026独立因子",
) -> None:
    if unit_id not in catalog:
        catalog[unit_id] = {
            "unit_id": unit_id,
            "factor_code_2026": factor_code,
            "script_factor_2026": script_factor,
            "factor_name": factor_name or factor_code,
            "frequency": frequency,
            "source_kind": source_kind,
            "source_2026": source_2026,
            "calendar_editions": "2026",
            "formula_mode": formula_mode,
            "calculation_method": calculation_method,
        }
        return

    item = catalog[unit_id]
    if factor_code and not item["factor_code_2026"]:
        item["factor_code_2026"] = factor_code
    if script_factor and not item["script_factor_2026"]:
        item["script_factor_2026"] = script_factor
    if source_2026 and source_2026 not in item["source_2026"].split(" | "):
        item["source_2026"] = " | ".join(
            value for value in [item["source_2026"], source_2026] if value
        )
    if frequency and not item["frequency"]:
        item["frequency"] = frequency
    if formula_mode and not item["formula_mode"]:
        item["formula_mode"] = formula_mode
    if calculation_method and not item["calculation_method"]:
        item["calculation_method"] = calculation_method
    item["calendar_editions"] = add_edition(item["calendar_editions"], 2026)


def load_historical_units(
    status: pd.DataFrame,
) -> tuple[pd.DataFrame, list[str], int]:
    raw_parts: list[pd.DataFrame] = []
    missing_files: list[str] = []
    duplicate_rows = 0
    for batch_name, group in status.groupby("selected_batch", dropna=False):
        batch_text = clean_text(batch_name)
        if not batch_text:
            missing_files.append("<selected_batch为空>")
            continue
        path = RUNS_DIR / batch_text / "unit_rebalance_returns.parquet"
        if not path.exists():
            missing_files.append(relative_path(path))
            continue
        frame = pd.read_parquet(path)
        required_units = set(group["unit_id"].astype(str))
        frame = frame[frame["unit_id"].astype(str).isin(required_units)].copy()
        if "exit_date" not in frame.columns:
            raise RuntimeError(f"{path} 缺少 exit_date 列")
        duplicate_mask = frame.duplicated(["unit_id", "exit_date"], keep=False)
        duplicate_rows += int(duplicate_mask.sum())
        if duplicate_mask.any():
            frame = frame.sort_values(["unit_id", "exit_date"]).drop_duplicates(
                ["unit_id", "exit_date"], keep="last"
            )
        raw_parts.append(
            new_raw_rows(
                frame,
                frame["unit_id"],
                ("exit_date", "entry_date"),
                path,
                "历史selected_batch",
            )
        )
    if not raw_parts:
        raise RuntimeError("没有读到任何历史 selected_batch IC")
    return pd.concat(raw_parts, ignore_index=True), missing_files, duplicate_rows


def build_exact_reference_map(status: pd.DataFrame) -> dict[str, str]:
    mapped = status[status["existing_ref"].notna()].copy()
    mapped["existing_ref"] = mapped["existing_ref"].astype(str).str.strip()
    duplicates = mapped[mapped.duplicated("existing_ref", keep=False)]
    if not duplicates.empty:
        detail = duplicates[["unit_id", "existing_ref"]].to_dict("records")
        raise RuntimeError(f"existing_ref 不是唯一值：{detail}")
    return dict(zip(mapped["existing_ref"], mapped["unit_id"].astype(str)))


def load_formal_2026(
    reference_map: dict[str, str], catalog: dict[str, dict[str, str]]
) -> tuple[pd.DataFrame, dict[str, set[str]], dict[str, object]]:
    all57 = read_csv(ALL57_RAW_PATH)
    completion51 = read_csv(COMPLETION51_RAW_PATH)

    pair_table = all57[["factor", "script_factor"]].drop_duplicates().copy()
    pair_table["script_match"] = pair_table["script_factor"].map(reference_map)
    pair_table["factor_match"] = pair_table["factor"].map(reference_map)
    conflicts = pair_table[
        pair_table["script_match"].notna()
        & pair_table["factor_match"].notna()
        & pair_table["script_match"].ne(pair_table["factor_match"])
    ]
    if not conflicts.empty:
        raise RuntimeError(
            "正式57的 factor 与 script_factor 映射到不同 unit："
            + str(conflicts.to_dict("records"))
        )
    pair_table["unit_id"] = pair_table["script_match"].fillna(pair_table["factor_match"])
    pair_table["mapped"] = pair_table["unit_id"].notna()
    pair_table.loc[~pair_table["mapped"], "unit_id"] = (
        "Y2026_EXTRA_ALL57__" + pair_table.loc[~pair_table["mapped"], "factor"].astype(str)
    )
    pair_map = {
        (str(row.factor), str(row.script_factor)): str(row.unit_id)
        for row in pair_table.itertuples(index=False)
    }
    all57_unit_ids = all57.apply(
        lambda row: pair_map[(str(row["factor"]), str(row["script_factor"]))], axis=1
    )

    for row in all57.drop_duplicates(["factor", "script_factor"]).itertuples(index=False):
        unit_id = pair_map[(str(row.factor), str(row.script_factor))]
        update_2026_metadata(
            catalog,
            unit_id,
            str(row.factor),
            str(row.script_factor),
            clean_text(getattr(row, "factor_name", "")),
            "正式57",
            clean_text(getattr(row, "signal_frequency", "")),
            "正式复测",
            f"{clean_text(getattr(row, 'source_month', ''))}原频率IC",
        )

    completion51_codes = completion51["factor"].astype(str)
    completion51_unit_ids = completion51_codes.map(reference_map)
    completion51_unit_ids = completion51_unit_ids.fillna(
        "Y2026_EXTRA_COMPLETION51__" + completion51_codes
    )
    for row in completion51.drop_duplicates("factor").itertuples(index=False):
        factor = str(row.factor)
        unit_id = reference_map.get(factor, f"Y2026_EXTRA_COMPLETION51__{factor}")
        calculation_method = " / ".join(
            value
            for value in [
                clean_text(getattr(row, "formula_label", "")),
                clean_text(getattr(row, "source_group", "")),
            ]
            if value
        )
        update_2026_metadata(
            catalog,
            unit_id,
            factor,
            factor,
            clean_text(getattr(row, "factor_name", "")),
            "正式51",
            clean_text(getattr(row, "signal_frequency", "")),
            clean_text(getattr(row, "formula_mode", "")),
            calculation_method,
        )

    raw57 = new_raw_rows(
        all57,
        all57_unit_ids,
        ("return_month", "exit_date", "entry_date"),
        ALL57_RAW_PATH,
        "2026正式57",
    )
    raw51 = new_raw_rows(
        completion51,
        completion51_unit_ids,
        ("return_month", "exit_date", "entry_date"),
        COMPLETION51_RAW_PATH,
        "2026正式51",
    )

    mapped_all57 = set(pair_table.loc[pair_table["mapped"], "unit_id"].astype(str))
    extra_all57_factors = set(pair_table.loc[~pair_table["mapped"], "factor"].astype(str))
    mapped51 = set(completion51_codes[completion51_codes.isin(reference_map)].astype(str).map(reference_map))
    extra51_factors = set(completion51_codes[~completion51_codes.isin(reference_map)].astype(str))
    formal_units = mapped_all57 | mapped51
    extra_units = {
        *(f"Y2026_EXTRA_ALL57__{factor}" for factor in extra_all57_factors),
        *(f"Y2026_EXTRA_COMPLETION51__{factor}" for factor in extra51_factors),
    }
    details: dict[str, object] = {
        "all57_factor_count": int(pair_table["factor"].nunique()),
        "completion51_factor_count": int(completion51_codes.nunique()),
        "canonical_overlap": int(
            len(set(pair_table["factor"].astype(str)) & set(completion51_codes.astype(str)))
        ),
        "mapped_unit_count": int(len(formal_units)),
        "extra_factor_count": int(len(extra_units)),
        "mapping_conflict_count": int(len(conflicts)),
    }
    groups = {
        "formal_units": formal_units,
        "extra_units": extra_units,
        "extra_all57_factors": extra_all57_factors,
        "extra51_factors": extra51_factors,
    }
    return pd.concat([raw57, raw51], ignore_index=True), groups, details


def load_extra_history(groups: dict[str, set[str]]) -> pd.DataFrame:
    raw_parts: list[pd.DataFrame] = []

    all57_history = read_csv(ALL57_HISTORY_PATH)
    all57_history = all57_history[
        all57_history["factor"].astype(str).isin(groups["extra_all57_factors"])
    ].copy()
    all57_unit_ids = "Y2026_EXTRA_ALL57__" + all57_history["factor"].astype(str)
    raw_parts.append(
        new_raw_rows(
            all57_history,
            all57_unit_ids,
            ("return_month", "exit_date", "entry_date"),
            ALL57_HISTORY_PATH,
            "38个独立因子历史记录_正式57",
        )
    )

    completion_history = read_csv(COMPLETION51_HISTORY_PATH)
    completion_history = completion_history[
        completion_history["factor"].astype(str).isin(groups["extra51_factors"])
    ].copy()
    completion_unit_ids = (
        "Y2026_EXTRA_COMPLETION51__" + completion_history["factor"].astype(str)
    )
    raw_parts.append(
        new_raw_rows(
            completion_history,
            completion_unit_ids,
            ("exit_date", "entry_date"),
            COMPLETION51_HISTORY_PATH,
            "38个独立因子历史记录_正式51",
        )
    )
    result = pd.concat(raw_parts, ignore_index=True)
    return result[result["return_month"].str.slice(0, 4).isin(["2022", "2023", "2024"])].reset_index(
        drop=True
    )


def find_extension_path() -> Path | None:
    if EXTENSION_COMBINED_PATH.exists():
        return EXTENSION_COMBINED_PATH
    candidates = [
        path
        for path in RUNS_DIR.rglob("unit_rebalance_returns.parquet")
        if "2026" in str(path.parent).lower()
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda path: path.stat().st_mtime)


def load_extension_2026(
    path: Path | None,
    catalog: dict[str, dict[str, str]],
) -> tuple[pd.DataFrame, dict[str, object]]:
    if path is None:
        return pd.DataFrame(columns=["unit_id", "return_month", "ic", "source_file", "source_label"]), {
            "extension_found": False,
            "extension_path": "",
            "extension_unit_count": 0,
            "extension_unit_ids": [],
        }
    if path.suffix.lower() == ".parquet":
        frame = pd.read_parquet(path)
    else:
        frame = read_csv(path)
    if "unit_id" not in frame.columns:
        raise RuntimeError(f"{path} 缺少 unit_id 列")
    frame["unit_id"] = frame["unit_id"].astype(str)
    frame = frame.drop_duplicates(
        [column for column in ["unit_id", "signal_date", "entry_date", "exit_date", "ic"] if column in frame],
        keep="last",
    )
    result = new_raw_rows(
        frame,
        frame["unit_id"],
        ("return_month", "exit_date", "entry_date"),
        path,
        "2026补算",
    )
    result = result[result["return_month"].str.startswith("2026-")].reset_index(drop=True)
    extension_units = set(result["unit_id"].astype(str))
    for unit_id in extension_units:
        rows = frame[frame["unit_id"].eq(unit_id)]
        first = rows.iloc[0]
        update_2026_metadata(
            catalog,
            unit_id,
            clean_text(first.get("factor", "")),
            clean_text(first.get("factor", "")),
            clean_text(first.get("name", "")),
            "2026补算",
            clean_text(first.get("frequency", "")),
            clean_text(first.get("formula_mode", "")),
            clean_text(first.get("calculation_method", "")),
            source_kind="2026补算",
        )
    return result, {
        "extension_found": True,
        "extension_path": relative_path(path),
        "extension_unit_count": int(len(extension_units)),
        "extension_unit_ids": sorted(extension_units),
    }


def load_hf0194_correction(
    catalog: dict[str, dict[str, str]]
) -> tuple[pd.DataFrame, dict[str, object]]:
    required_paths = [HF0194_CORRECTION_PATH, HF0194_CORRECTION_CHECKS_PATH]
    missing_paths = [relative_path(path) for path in required_paths if not path.exists()]
    if missing_paths:
        raise FileNotFoundError("HF0194修正结果缺失：" + " | ".join(missing_paths))
    frame = read_csv(HF0194_CORRECTION_PATH)
    frame = frame[frame["unit_id"].astype(str).eq("HF0194__daily")].copy()
    result = new_raw_rows(
        frame,
        frame["unit_id"],
        ("return_month", "exit_date", "entry_date"),
        HF0194_CORRECTION_PATH,
        "HF0194公式方向修正",
    )
    result = result[result["return_month"].isin(TARGET_MONTH_SET)].reset_index(drop=True)
    month_count = int(result["return_month"].nunique())
    correction_checks = json.loads(HF0194_CORRECTION_CHECKS_PATH.read_text(encoding="utf-8"))
    if (
        month_count != len(TARGET_MONTH_SET)
        or len(result) != 163
        or correction_checks.get("duplicate_rows") != 0
        or correction_checks.get("months_by_year")
        != {"2022": 12, "2023": 12, "2024": 12, "2026": 5}
    ):
        raise RuntimeError(
            f"HF0194修正结果不完整：逐期IC {len(result)} 行，月份 {month_count} 个"
        )
    meta = catalog["HF0194__daily"]
    meta["factor_name"] = "绝对收益与调整后滞后成交量相关性（日频）"
    meta["formula_mode"] = "trial"
    meta["calculation_method"] = "公式方向修正 / 每5个交易日取IC"
    meta["source_2026"] = "2026修正重算"
    return result, {
        "corrected_unit_id": "HF0194__daily",
        "source_paths": [relative_path(path) for path in required_paths],
        "raw_ic_rows": int(len(result)),
        "month_count": month_count,
        "finite_ic_rows": int(np.isfinite(result["ic"]).sum()),
    }


def aggregate_monthly(
    raw: pd.DataFrame, catalog: dict[str, dict[str, str]]
) -> pd.DataFrame:
    grouped = (
        raw.groupby(["unit_id", "return_month"], as_index=False)
        .agg(
            monthly_ic=("ic", "mean"),
            raw_observations=("ic", "size"),
            source_files=("source_file", join_unique),
            source_labels=("source_label", join_unique),
        )
        .sort_values(["unit_id", "return_month"])
        .reset_index(drop=True)
    )
    metadata = pd.DataFrame(catalog.values())
    grouped = grouped.merge(metadata, on="unit_id", how="left", validate="many_to_one")
    grouped["year"] = grouped["return_month"].str.slice(0, 4).astype(int)
    columns = [
        *IDENTITY_COLUMNS,
        "year",
        "return_month",
        "monthly_ic",
        "raw_observations",
        "source_files",
        "source_labels",
    ]
    return grouped[columns]


def build_annual(monthly: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    metadata = monthly[IDENTITY_COLUMNS].drop_duplicates("unit_id").set_index("unit_id")
    for unit_id, unit_rows in monthly.groupby("unit_id"):
        for year in TARGET_YEARS:
            year_rows = unit_rows[unit_rows["year"].eq(year)].sort_values("return_month")
            values = year_rows["monthly_ic"]
            actual_months = set(year_rows["return_month"])
            expected_months = set(TARGET_MONTHS[year])
            row = metadata.loc[unit_id].to_dict()
            row.update(
                {
                    "unit_id": unit_id,
                    "year": year,
                    "expected_months": len(expected_months),
                    "month_count": len(actual_months),
                    "ic": float(values.mean()) if not values.empty else np.nan,
                    "ic_std": float(values.std(ddof=1)) if len(values) >= 2 else np.nan,
                    "icir": icir(values),
                    "first_month": year_rows["return_month"].min() if not year_rows.empty else "",
                    "last_month": year_rows["return_month"].max() if not year_rows.empty else "",
                    "complete": actual_months == expected_months,
                }
            )
            rows.append(row)
    return pd.DataFrame(rows)[
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


def build_all_factor_ranking(monthly: pd.DataFrame, annual: pd.DataFrame) -> pd.DataFrame:
    metadata = monthly[IDENTITY_COLUMNS].drop_duplicates("unit_id").set_index("unit_id")
    rows: list[dict[str, object]] = []
    for unit_id, unit_rows in monthly.groupby("unit_id"):
        annual_rows = annual[annual["unit_id"].eq(unit_id)].set_index("year")
        values = unit_rows["monthly_ic"]
        row = metadata.loc[unit_id].to_dict()
        row["unit_id"] = unit_id
        complete_count = 0
        for year in TARGET_YEARS:
            year_row = annual_rows.loc[year]
            row[f"months_{year}"] = int(year_row["month_count"])
            row[f"ic_{year}"] = year_row["ic"]
            row[f"icir_{year}"] = year_row["icir"]
            complete_count += int(bool(year_row["complete"]))
        row["years_complete"] = complete_count
        row["total_months"] = int(unit_rows["return_month"].nunique())
        row["total_ic"] = float(values.mean())
        row["total_icir"] = icir(values)
        row["abs_total_ic"] = abs(row["total_ic"])
        row["abs_total_icir"] = (
            abs(row["total_icir"]) if np.isfinite(row["total_icir"]) else np.nan
        )
        row["direction"] = "正向" if row["total_ic"] >= 0 else "反向"
        row["first_month"] = unit_rows["return_month"].min()
        row["last_month"] = unit_rows["return_month"].max()
        row["official_eligible"] = bool(
            complete_count == len(TARGET_YEARS)
            and row["total_months"] == len(TARGET_MONTH_SET)
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
    ordered_columns = [
        *IDENTITY_COLUMNS,
        "years_complete",
        "months_2022",
        "months_2023",
        "months_2024",
        "months_2026",
        "ic_2022",
        "icir_2022",
        "ic_2023",
        "icir_2023",
        "ic_2024",
        "icir_2024",
        "ic_2026",
        "icir_2026",
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
    return result[ordered_columns]


def build_official_ranking(all_factors: pd.DataFrame) -> pd.DataFrame:
    result = all_factors[all_factors["official_eligible"]].copy()
    result["abs_ic_rank"], result["ic_score"] = rank_and_score(result["total_ic"])
    result["abs_icir_rank"], result["icir_score"] = rank_and_score(result["total_icir"])
    result["composite_score"] = 0.5 * result["ic_score"] + 0.5 * result["icir_score"]
    result["composite_rank"] = result["composite_score"].rank(method="min", ascending=False)
    result = result.sort_values(
        ["composite_rank", "abs_ic_rank", "abs_icir_rank", "unit_id"]
    ).reset_index(drop=True)
    columns = [
        "composite_rank",
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
        "direction",
        "months_2022",
        "ic_2022",
        "icir_2022",
        "months_2023",
        "ic_2023",
        "icir_2023",
        "months_2024",
        "ic_2024",
        "icir_2024",
        "months_2026",
        "ic_2026",
        "icir_2026",
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
    warning: bool = False,
) -> dict[str, object]:
    return {
        "check_id": check_id,
        "check_item": item,
        "expected": expected,
        "actual": actual,
        "status": "通过" if passed else ("提醒" if warning else "失败"),
        "detail": detail,
    }


def build_checks(
    status: pd.DataFrame,
    missing_batch_files: list[str],
    duplicate_history_rows: int,
    formal_details: dict[str, object],
    extension_details: dict[str, object],
    monthly: pd.DataFrame,
    annual: pd.DataFrame,
    all_factors: pd.DataFrame,
    official: pd.DataFrame,
    alias_details: dict[str, object],
    expected_output_ids: set[str],
    correction_details: dict[str, object],
) -> pd.DataFrame:
    historical_alias_ids = {
        alias_id for alias_id in CONFIRMED_DUPLICATE_ALIASES if alias_id.startswith("HF")
    }
    expected_historical_ids = set(status["unit_id"].astype(str)) - historical_alias_ids
    expected_historical_months = len(expected_historical_ids) * 36
    actual_historical_months = int(
        monthly[
            monthly["unit_id"].isin(status["unit_id"].astype(str))
            & monthly["year"].isin([2022, 2023, 2024])
        ][["unit_id", "return_month"]].drop_duplicates().shape[0]
    )
    extra_ids = set(
        all_factors.loc[all_factors["source_kind"].eq("2026独立因子"), "unit_id"].astype(str)
    )
    extra_complete_history = int(
        annual[
            annual["unit_id"].isin(extra_ids)
            & annual["year"].isin([2022, 2023, 2024])
            & annual["complete"]
        ].groupby("unit_id")["year"].nunique().eq(3).sum()
    )
    expected_extra_count = int(
        38 - alias_details["independent_2026_alias_count"]
    )
    actual_output_ids = set(all_factors["unit_id"].astype(str))
    actual_official_ids = set(official["unit_id"].astype(str))
    status_ids = set(status["unit_id"].astype(str))
    extension_unit_ids = set(extension_details["extension_unit_ids"])
    missing_output_ids = sorted(expected_output_ids - actual_output_ids)
    unexpected_output_ids = sorted(actual_output_ids - expected_output_ids)
    official_month_check = bool(
        len(official) == len(expected_output_ids)
        and actual_official_ids == expected_output_ids
        and official[["months_2022", "months_2023", "months_2024", "months_2026"]]
        .eq([12, 12, 12, 5])
        .all(axis=None)
    )
    finite_official = bool(
        np.isfinite(official[["total_ic", "total_icir", "composite_score"]].to_numpy()).all()
    )
    extension_months = monthly[
        monthly["unit_id"].isin(expected_historical_ids) & monthly["year"].eq(2026)
    ]
    extension_complete_units = int(
        extension_months.groupby("unit_id")["return_month"].nunique().eq(5).sum()
    )
    series_groups: dict[tuple[object, ...], list[str]] = {}
    monthly_wide = monthly.pivot(index="unit_id", columns="return_month", values="monthly_ic")
    monthly_wide = monthly_wide.reindex(columns=sorted(TARGET_MONTH_SET))
    for unit_id, values in monthly_wide.iterrows():
        signature = tuple(
            None if pd.isna(value) else round(float(value), 14) for value in values
        )
        series_groups.setdefault(signature, []).append(str(unit_id))
    duplicate_series_groups = [group for group in series_groups.values() if len(group) > 1]
    rows = [
        check_row("C01", "历史已完成计算项数", 254, len(status), len(status) == 254),
        check_row(
            "C02",
            "历史结果文件缺失数",
            0,
            len(missing_batch_files),
            not missing_batch_files,
            " | ".join(missing_batch_files),
        ),
        check_row(
            "C03",
            "历史原始IC重复行",
            0,
            duplicate_history_rows,
            duplicate_history_rows == 0,
            "若有重复，脚本保留同unit同exit_date最后一行",
            warning=True,
        ),
        check_row(
            "C04",
            "正式57因子数",
            57,
            formal_details["all57_factor_count"],
            formal_details["all57_factor_count"] == 57,
        ),
        check_row(
            "C05",
            "正式51因子数",
            51,
            formal_details["completion51_factor_count"],
            formal_details["completion51_factor_count"] == 51,
        ),
        check_row(
            "C06",
            "两份2026正式数据重复代码数",
            0,
            formal_details["canonical_overlap"],
            formal_details["canonical_overlap"] == 0,
        ),
        check_row(
            "C07",
            "2026代码直接对应历史计算项数",
            70,
            formal_details["mapped_unit_count"],
            formal_details["mapped_unit_count"] == 70,
        ),
        check_row(
            "C08",
            "字符串未直接对应历史代码的2026因子数",
            38,
            formal_details["extra_factor_count"],
            formal_details["extra_factor_count"] == 38,
        ),
        check_row(
            "C09",
            "合并重复编号后，历史因子2022至2024月份数",
            expected_historical_months,
            actual_historical_months,
            actual_historical_months == expected_historical_months,
        ),
        check_row(
            "C10",
            "合并同公式后，独立2026因子历史三年完整数",
            expected_extra_count,
            extra_complete_history,
            extra_complete_history == expected_extra_count,
        ),
        check_row(
            "C11",
            "2026补算文件",
            "存在",
            extension_details["extension_path"] or "未找到",
            bool(extension_details["extension_found"]),
            "补算未完成时可先试跑；文件生成后重跑即可纳入",
            warning=True,
        ),
        check_row(
            "C12",
            "正式榜因子集合及月份完整",
            f"{len(expected_output_ids)}个；12+12+12+5",
            len(official),
            official_month_check,
        ),
        check_row(
            "C13",
            "正式榜总IC/ICIR/综合分均为有限数",
            "全部有限",
            len(official),
            finite_official,
        ),
        check_row(
            "C14",
            "正式榜因子数",
            len(expected_output_ids),
            len(official),
            len(official) == len(expected_output_ids),
        ),
        check_row(
            "C15",
            "补算文件中的历史计算项集合",
            254,
            extension_details["extension_unit_count"],
            extension_details["extension_unit_count"] == 254
            and extension_unit_ids == status_ids,
            f"缺少：{sorted(status_ids - extension_unit_ids) or '无'}；多出：{sorted(extension_unit_ids - status_ids) or '无'}",
            warning=not bool(extension_details["extension_found"]),
        ),
        check_row(
            "C16",
            "合并重复编号后，历史因子2026五个月完整数",
            len(expected_historical_ids),
            extension_complete_units,
            extension_complete_units == len(expected_historical_ids),
            "补算未完成时此项仅提醒",
            warning=not bool(extension_details["extension_found"]),
        ),
        check_row(
            "C17",
            "已确认重复编号合并数",
            len(CONFIRMED_DUPLICATE_ALIASES),
            alias_details["merged_alias_count"],
            alias_details["merged_alias_count"] == len(CONFIRMED_DUPLICATE_ALIASES),
            "；".join(
                f"{item['alias_id']}→{item['canonical_id']}"
                for item in alias_details["merged_aliases"]
            ),
        ),
        check_row(
            "C18",
            "最终因子编号集合",
            len(expected_output_ids),
            len(actual_output_ids),
            len(all_factors) == len(actual_output_ids)
            and not missing_output_ids
            and not unexpected_output_ids,
            f"缺少：{missing_output_ids or '无'}；多出：{unexpected_output_ids or '无'}",
        ),
        check_row(
            "C19",
            "剩余完全相同的41个月IC序列",
            0,
            len(duplicate_series_groups),
            not duplicate_series_groups,
            "；".join(" | ".join(group) for group in duplicate_series_groups),
        ),
        check_row(
            "C20",
            "HF0194公式方向修正结果",
            "163行逐期IC；41个月；全部有限",
            f"{correction_details['raw_ic_rows']}行；{correction_details['month_count']}个月；{correction_details['finite_ic_rows']}行有限",
            correction_details["raw_ic_rows"] == 163
            and correction_details["month_count"] == 41
            and correction_details["finite_ic_rows"] == 163,
            " | ".join(correction_details["source_paths"]),
        ),
    ]
    return pd.DataFrame(rows)


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    status_all = read_csv(STATUS_PATH)
    status = status_all[status_all["status"].eq("completed")].copy()
    status["unit_id"] = status["unit_id"].astype(str)
    catalog = make_catalog(status)

    historical_raw, missing_batch_files, duplicate_history_rows = load_historical_units(status)
    reference_map = build_exact_reference_map(status)
    formal_2026_raw, formal_groups, formal_details = load_formal_2026(reference_map, catalog)
    extra_history_raw = load_extra_history(formal_groups)
    extension_path = find_extension_path()
    extension_raw, extension_details = load_extension_2026(extension_path, catalog)
    correction_raw, correction_details = load_hf0194_correction(catalog)

    # 2026补算沿用历史unit自己的取样频率。只要补算中已有该unit，就不再混入
    # 正式57/51的原频率IC；正式57/51始终保留38个独立因子。
    extension_units = set(extension_raw["unit_id"].astype(str))
    formal_2026_effective = formal_2026_raw[
        formal_2026_raw["unit_id"].isin(formal_groups["extra_units"])
        | ~formal_2026_raw["unit_id"].isin(extension_units)
    ].copy()
    overridden_formal_units = formal_groups["formal_units"] & extension_units
    extension_details["formal_units_overridden"] = int(len(overridden_formal_units))
    for unit_id in overridden_formal_units:
        catalog[unit_id]["source_2026"] = "2026补算"

    raw_parts = [historical_raw, extra_history_raw, formal_2026_effective, extension_raw]
    raw = pd.concat([part for part in raw_parts if not part.empty], ignore_index=True)
    raw = raw.drop_duplicates(
        ["unit_id", "return_month", "ic", "source_file", "source_label"], keep="last"
    )
    raw = raw[~raw["unit_id"].eq(correction_details["corrected_unit_id"])].copy()
    raw = pd.concat([raw, correction_raw], ignore_index=True)
    raw, alias_details = merge_confirmed_duplicate_aliases(raw, catalog)
    expected_historical_ids = set(status["unit_id"].astype(str)) - {
        alias_id for alias_id in CONFIRMED_DUPLICATE_ALIASES if alias_id.startswith("HF")
    }
    expected_independent_ids = set(formal_groups["extra_units"]) - {
        alias_id
        for alias_id in CONFIRMED_DUPLICATE_ALIASES
        if alias_id.startswith("Y2026_EXTRA")
    }
    expected_output_ids = expected_historical_ids | expected_independent_ids
    monthly = aggregate_monthly(raw, catalog)
    annual = build_annual(monthly)
    all_factors = build_all_factor_ranking(monthly, annual)
    official = build_official_ranking(all_factors)
    checks = build_checks(
        status,
        missing_batch_files,
        duplicate_history_rows,
        formal_details,
        extension_details,
        monthly,
        annual,
        all_factors,
        official,
        alias_details,
        expected_output_ids,
        correction_details,
    )
    if checks["status"].eq("失败").any():
        failed = checks[checks["status"].eq("失败")]
        raise RuntimeError("检查未通过：" + failed.to_json(orient="records", force_ascii=False))

    outputs = {
        "all_factors_ranking.csv": all_factors,
        "official_four_period_ranking.csv": official,
        "annual_detail.csv": annual,
        "monthly_ic.csv": monthly,
        "checks.csv": checks,
    }
    for filename, frame in outputs.items():
        frame.to_csv(OUTPUT_DIR / filename, index=False, encoding="utf-8-sig")

    manifest = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "output_directory": relative_path(OUTPUT_DIR),
        "target_periods": TARGET_MONTHS,
        "methodology": {
            "monthly_ic": "同一因子、同一收益实现月的原始横截面IC取算术平均；优先使用return_month，无该列时按exit_date月份。",
            "annual_ic": "当年月IC算术平均。",
            "annual_icir": "当年月IC均值除以月IC样本标准差（ddof=1）。",
            "total_ic": "2022、2023、2024各12个月与2026年1至5月，共41个月月IC的算术平均。",
            "total_icir": "41个月月IC均值除以月IC样本标准差（ddof=1）。",
            "official_eligibility": "2022、2023、2024分别有12个月，2026有5个月，且总IC与总ICIR均为有限数。缺失月份不填0。",
            "ranking": "正式榜内按|总IC|与|总ICIR|分别计算0至100分位得分，各占50%，综合分从高到低排序。",
            "factor_matching": "先按2026代码与历史代码完全相同来确认同一因子；另有3组经原公式和实际计算代码复核后确认相同，统一采用历史计算项的取样频率。不按中文名称自动合并。",
            "duplicate_handling": "共合并4个重复编号：1组历史计算结果完全相同，3组2026编号与历史编号的公式和程序相同。",
            "corrected_factor": "HF0194__daily 原程序把滞后方向写反；已改用正确的前一分钟调整后成交额字段，并按历史日频每5个交易日取IC重算四年。月频J11_D04因频率不同继续单列。",
        },
        "sources": {
            "historical_status": relative_path(STATUS_PATH),
            "historical_selected_batches": relative_path(RUNS_DIR),
            "formal_2026_57": relative_path(ALL57_RAW_PATH),
            "formal_2026_51": relative_path(COMPLETION51_RAW_PATH),
            "extra_history_57": relative_path(ALL57_HISTORY_PATH),
            "extra_history_51": relative_path(COMPLETION51_HISTORY_PATH),
            "extension_2026": extension_details["extension_path"],
            "hf0194_correction": correction_details["source_paths"],
        },
        "counts": {
            "historical_completed_units": int(len(status)),
            "historical_unique_calculations": int(len(expected_historical_ids)),
            "formal_2026_factors": int(
                formal_details["all57_factor_count"]
                + formal_details["completion51_factor_count"]
            ),
            "formal_2026_mapped_units": int(formal_details["mapped_unit_count"]),
            "formal_2026_independent_factors": int(formal_details["extra_factor_count"]),
            "independent_factors_after_formula_review": int(len(expected_independent_ids)),
            "confirmed_duplicate_aliases_merged": int(alias_details["merged_alias_count"]),
            "corrected_factor_count": 1,
            "extension_units_added": int(extension_details["extension_unit_count"]),
            "all_factor_rows": int(len(all_factors)),
            "official_ranking_rows": int(len(official)),
            "annual_rows": int(len(annual)),
            "monthly_rows": int(len(monthly)),
            "failed_checks": int(checks["status"].eq("失败").sum()),
            "warning_checks": int(checks["status"].eq("提醒").sum()),
        },
        "merged_aliases": alias_details["merged_aliases"],
    }
    (OUTPUT_DIR / "ranking_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(json.dumps(manifest["counts"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
