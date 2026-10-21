# -*- coding: utf-8 -*-
"""用 DuckDB 重新读取 Parquet，并调用已有因子计算函数。

本文件不读取旧回测 CSV。它负责三件事：

1. 把历史清单中的旧结果代码换成当前程序实际使用的代码；
2. 把 96 个可重算代码分配到原有月份程序；
3. 为一月至六月旧程序提供统一的 DuckDB 日度面板读取和计算入口。

七八月、九至十二月和补算因子已经有 DuckDB 入口，本文件只登记调用位置。
"""

from __future__ import annotations

import csv
import importlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

import duckdb
import numpy as np
import pandas as pd

import duckdb_factor_store as factor_store


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_MAPPING_PATH = (
    BASE_DIR / "historical_calendar_backtest_results" / "factor_mapping.csv"
)
DEFAULT_QLIB_DIR = Path(r"/Users/leslie/Downloads/中证500量化60min/c5_core/data/qlib_data/qlib_bin")


# 2026工作簿沿用的短代码与当前 Python 计算程序代码不同。
RESULT_CODE_ALIASES = {
    "IDEAL_AMPLITUDE": "IDEAL_AMPLITUDE_20D",
    "ILLIQ_SHORTCUT": "ILLIQ_SHORTCUT_5M_5D",
    "M03_AMOUNT_SHARE_ENTROPY": "M03_AMOUNT_SHARE_ENTROPY_5M",
    "M06_UPSIDE_VOL_SHARE": "M06_UPSIDE_VOL_SHARE_5M_20D",
    "M12_WEIGHTED_SKEW": "M12_WEIGHTED_SKEW_5M",
    "M13_MORNING_FOG": "M13_MORNING_FOG_20D",
    "M21_CORRECTED_INTRADAY_REVERSAL": "M21_CORRECTED_INTRADAY_REVERSAL_20D",
    "M27_INTRADAY_JUMPINESS": "M27_INTRADAY_JUMPINESS_20D",
    "PRICE_RESILIENCY": "PRICE_RESILIENCY_1M_TREND",
    "REALIZED_SKEW_20D": "REALIZED_SKEW_1M_20D",
    "RVJN": "RVJN_5M",
    "UTD": "UTD_20D_NEUTRAL",
    "VOLUME_SHARE_KURT": "VOLUME_SHARE_KURT_10M",
    "WEIGHTED_CLOSE_RATIO": "WEIGHTED_CLOSE_RATIO_5M",
}


# 映射表中有 implementation_ref，但现有程序没有实际生成这些代码。
UNRESOLVED_IMPLEMENTATION_REFS = frozenset(
    {
        "A24_SNR_ENHANCED_REVERSAL_20D",
        "F02_D07_VOLUME_BIN_ENTROPY_STD20",
        "J07_D17_CPV",
        "J10_D05_HF_IDIO_VOL",
        "J11_D05_NONLINEAR_HF_VOLATILITY",
        "J12_D26_APM",
        "MAY22_MULTILAYER_INTRADAY_SNR",
        "MAY29_PRICE_VOLUME_CORR_COMPOSITE",
    }
)


@dataclass(frozen=True)
class GroupSpec:
    name: str
    module_name: str
    result_codes: tuple[str, ...]
    source_names: tuple[str, ...]
    local_adapter: bool
    note: str


GROUPS: dict[str, GroupSpec] = {
    "january": GroupSpec(
        "january",
        "run_january_minute_backtest",
        (
            "CGO",
            "IDEAL_AMPLITUDE_20D",
            "ILLIQ_SHORTCUT_5M_5D",
            "LAST_30MIN_RETURN",
            "PRICE_RESILIENCY_1M_TREND",
            "REALIZED_SKEW_1M_20D",
            "RVJN_5M",
            "TGD_20D",
            "UTD_20D_NEUTRAL",
            "VOLUME_SHARE_KURT_10M",
            "WEIGHTED_CLOSE_RATIO_5M",
        ),
        ("january_daily",),
        True,
        "旧入口逐个读取Parquet；本文件改为DuckDB一次读取。",
    ),
    "february_march": GroupSpec(
        "february_march",
        "run_february_march_backtest",
        (
            "F02_D06_JUMP_ASYMMETRY_5M",
            "F02_D10_GOLDEN_SPLIT_REVERSAL_20D",
            "F02_D15_CORRECTED_AMPLITUDE_20D",
            "F02_D16_REALIZED_KURTOSIS_1M_20D",
            "F02_D27_ENHANCED_TGD_20D",
            "M03_AMOUNT_SHARE_ENTROPY_5M",
            "M06_UPSIDE_VOL_SHARE_5M_20D",
            "M10_OVERNIGHT_GAP_20D",
            "M12_WEIGHTED_SKEW_5M",
            "M13_MORNING_FOG_20D",
            "M21_CORRECTED_INTRADAY_REVERSAL_20D",
            "M27_INTRADAY_JUMPINESS_20D",
        ),
        ("base_daily",),
        True,
        "旧入口逐个读取Parquet；本文件改为DuckDB一次读取。",
    ),
    "april": GroupSpec(
        "april",
        "run_april_backtest",
        (
            "A03_TAIL_VOLUME_SHARE_20D",
            "A12_SMALL_DOWN_JUMP_VOL_5M",
            "A13_CORRECTED_OVERNIGHT_REVERSAL_20D",
            "A15_NOON_ANCIENT_TREE",
            "A21_DOWNSIDE_VOL_SHARE_5M_20D",
            "A25_PV_CORR_STD_20D_NEUTRAL",
        ),
        ("base_daily", "april_daily"),
        True,
        "DuckDB按date、code合并基础日表和四月日值。",
    ),
    "may": GroupSpec(
        "may",
        "run_may_backtest",
        (
            "MAY04_B_VOLUME_DISTRIBUTION",
            "MAY05_SALIENCE_RETURN",
            "MAY07_LARGE_JUMP_ASYMMETRY_5M",
            "MAY10_SINGLE_AMOUNT_SHARE_ENTROPY_5M",
            "MAY14_PANIC_20D",
            "MAY17_REALIZED_VOLATILITY_5M",
            "MAY19_NIGHT_SLEEP_FROST_ROAD_20D",
        ),
        ("base_daily", "may_daily"),
        True,
        "DuckDB按date、code合并基础日表和五月日值。",
    ),
    "june": GroupSpec(
        "june",
        "run_june_backtest",
        (
            "J06_D01_STRUCTURED_REVERSAL_21D_10M",
            "J06_D04_FUZZY_AMOUNT_RATIO_20D",
            "J06_D08_WAITING_RESCUE_20D",
            "J06_D10_VOLATILITY_PANIC_20D",
            "J06_D11_DOWNSIDE_REALIZED_VOL_5M",
            "J06_D13_PATV_5M",
            "J06_D17_PV_CORR_TREND_20D",
            "J06_D19_INTRADAY_CVAR_5PCT",
        ),
        ("base_daily", "june_daily"),
        True,
        "DuckDB合并后仍调用原程序的48段异常量合成函数。",
    ),
    "early_four": GroupSpec(
        "early_four",
        "run_early_four_minute_backtest",
        (
            "A06_DAZZLING_VOLATILITY_20D",
            "A10_P_SHAPED_VOLUME_DISTRIBUTION",
            "M25_HIGH_VOLUME_REALIZED_SKEW",
        ),
        ("base_daily", "minute_extra_daily"),
        False,
        "直接调用已有DuckDB入口run_early_four_minute_backtest.py。",
    ),
    "early_trial": GroupSpec(
        "early_trial",
        "run_trial_minute_backtest",
        (
            "A22_INTRADAY_SNR",
            "J06_D02_JUMP_INTENSITY",
            "J06_D07_HIGH_FREQUENCY_RESILIENCY",
            "M15_VSA_LOW_CLOSE_GAP",
            "MAY13_INTRADAY_ABNORMAL_VOLUME",
        ),
        ("base_daily", "trial_daily"),
        False,
        "直接调用已有DuckDB入口run_trial_minute_backtest.py。",
    ),
    "july_august": GroupSpec(
        "july_august",
        "run_july_august_backtest",
        (
            "F02_D12_LONE_GOOSE",
            "J07_D01_FUZZY_QTY_RATIO",
            "J07_D05_SIG_JUMP_ASYM",
            "J07_D07_EXTREME_REVERSAL",
            "J07_D08_AMOUNT_AUTOCORR",
            "J07_D10_CLOSE_AMOUNT_SHARE",
            "J07_D14_UPSIDE_RV",
            "J07_D19_NEG_INTRADAY_REV_FREQ",
            "J08_D01_ADJ_FUZZY_SPREAD",
            "J08_D05_DISASTER_REBUILD",
            "J08_D08_ABSRET_AMOUNT_CORR",
            "J08_D10_AMOUNT_VARIANCE",
            "J08_D14_RSJ",
            "J08_D16_DECAY_FEAR",
            "J08_D17_LARGE_VOL_PV_CORR",
            "J08_D19_POS_INTRADAY_REV_FREQ",
        ),
        (
            "base_daily",
            "may_daily",
            "june_daily",
            "july_august_daily",
            "lone_goose_daily",
        ),
        False,
        "直接调用已有DuckDB入口run_july_august_backtest.py。",
    ),
    "september_december": GroupSpec(
        "september_december",
        "run_september_december_backtest",
        (
            "J09_D04_EMOTION_SPILLOVER",
            "J09_D10_INTRADAY_TREND_RATIO",
            "J09_D14_SMART_MONEY_VWAP_RATIO",
            "J09_D17_REALIZED_BIPOWER_VARIATION",
            "J10_D07_CONFIDENCE_SPILLOVER",
            "J10_D14_REALIZED_TRIPOWER_VAR",
            "J10_D16_INTRADAY_MAX_DRAWDOWN",
            "J10_D19_ABNORMAL_POS_INTRADAY_REV",
            "J11_D01_VOLUME_PEAK_COUNT",
            "J11_D10_VOLUME_SHARE_STD",
            "J11_D11_NORMAL_CDF_ACTIVE_SHARE",
            "J11_D14_VOLUME_TIDE_PRICE_RATE",
            "J11_D15_REALIZED_JUMP_VOLATILITY",
            "J11_D17_OVERNIGHT_RETURN",
            "J12_D01_VOLUME_SHARE_SKEW",
            "J12_D02_VOLUME_CV",
            "J12_D04_ADJ_LAG_ABSRET_AMOUNT_CORR",
            "J12_D05_HF_VOLUME_VOL_CV",
            "J12_D07_DAZZLING_RETURN",
            "J12_D10_UNIFORM_ACTIVE_SHARE",
            "J12_D13_FUZZY_AMOUNT_CORR",
            "J12_D14_UP_JUMP_VOL",
            "J12_D16_INTRADAY_OR_OVERNIGHT_RETURN",
            "J12_D28_TIME_AVG_REL_PRICE",
        ),
        ("base_daily", "minute_extra_daily"),
        False,
        "直接调用已有DuckDB入口run_september_december_backtest.py。",
    ),
    "october_december_trial": GroupSpec(
        "october_december_trial",
        "run_october_december_trial_backtest",
        (
            "J10_D10_T_ACTIVE_SHARE",
            "J10_D20_CONSISTENT_TRADING",
            "J10_D26_CUM_JUMP_ABS_RET",
            "J12_D11_CONSISTENT_SELL",
        ),
        ("base_daily", "minute_extra_daily", "trial_daily", "network"),
        False,
        "直接调用已有DuckDB入口run_october_december_trial_backtest.py。",
    ),
}


CODE_TO_GROUP: dict[str, str] = {}
for _group_name, _group in GROUPS.items():
    for _code in _group.result_codes:
        if _code in CODE_TO_GROUP:
            raise RuntimeError(f"结果代码重复分组：{_code}")
        CODE_TO_GROUP[_code] = _group_name

if len(CODE_TO_GROUP) != 96:
    raise RuntimeError(f"应登记96个可重算结果代码，实际{len(CODE_TO_GROUP)}个")


@dataclass(frozen=True)
class MappedImplementation:
    implementation_ref: str
    result_code: str
    group_name: str
    record_ids: tuple[str, ...]


def resolve_result_code(implementation_ref: str) -> str:
    """把工作簿短代码换成当前程序实际生成的代码。"""

    return RESULT_CODE_ALIASES.get(implementation_ref, implementation_ref)


def read_mapped_implementations(
    mapping_path: Path = DEFAULT_MAPPING_PATH,
) -> list[MappedImplementation]:
    """读取历史映射，只返回能由现有程序实际重算的代码。"""

    records: dict[str, list[str]] = {}
    with Path(mapping_path).open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            raw = str(row.get("implementation_ref", "")).strip()
            if not raw:
                continue
            records.setdefault(raw, []).append(str(row.get("record_id", "")))
    result = []
    for raw in sorted(records):
        code = resolve_result_code(raw)
        group = CODE_TO_GROUP.get(code)
        if group is None:
            continue
        result.append(
            MappedImplementation(raw, code, group, tuple(records[raw]))
        )
    return result


def unresolved_mapping_refs(
    mapping_path: Path = DEFAULT_MAPPING_PATH,
) -> list[str]:
    """列出映射表虽有引用、但没有现成计算结果的代码。"""

    with Path(mapping_path).open("r", encoding="utf-8-sig", newline="") as handle:
        refs = {
            str(row.get("implementation_ref", "")).strip()
            for row in csv.DictReader(handle)
            if str(row.get("implementation_ref", "")).strip()
        }
    return sorted(ref for ref in refs if resolve_result_code(ref) not in CODE_TO_GROUP)


def group_summary(mapping_path: Path = DEFAULT_MAPPING_PATH) -> pd.DataFrame:
    """返回每组代码数量、数据源和现有调用入口。"""

    mapped = read_mapped_implementations(mapping_path)
    present = {item.result_code for item in mapped}
    rows = []
    for name, group in GROUPS.items():
        codes = [code for code in group.result_codes if code in present]
        rows.append(
            {
                "group_name": name,
                "module_name": group.module_name,
                "code_count": len(codes),
                "result_codes": ",".join(codes),
                "source_names": ",".join(group.source_names),
                "local_adapter": group.local_adapter,
                "note": group.note,
            }
        )
    return pd.DataFrame(rows)


def _unique(values: Sequence[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(values))


def required_columns_by_source(group_name: str) -> dict[str, tuple[str, ...] | None]:
    """返回一月至六月各组真正需要从日表读取的字段。

    ``None`` 表示读取该日表的全部字段。一月旧程序没有公开字段常量，
    且其每日文件本身已经是轻量缓存，因此直接读取全部32列。
    """

    if group_name not in GROUPS:
        raise KeyError(f"未知分组：{group_name}")
    if not GROUPS[group_name].local_adapter:
        raise ValueError(f"{group_name}已有DuckDB入口，无需本地适配")
    module = importlib.import_module(GROUPS[group_name].module_name)
    if group_name == "january":
        return {"january_daily": None}
    if group_name == "february_march":
        return {"base_daily": _unique([*module.BASE_COLUMNS, *module.FACTOR_COLUMNS])}
    if group_name == "april":
        return {
            "base_daily": _unique(module.BASE_COLUMNS),
            "april_daily": _unique(module.APRIL_COLUMNS),
        }
    if group_name == "may":
        return {
            "base_daily": _unique(module.BASE_COLUMNS),
            "may_daily": _unique(["date", "code", *module.MAY_COLUMNS]),
        }
    if group_name == "june":
        return {
            "base_daily": _unique(module.BASE_COLUMNS),
            "june_daily": _unique(module.JUNE_COLUMNS),
        }
    raise AssertionError(group_name)


_SAFE_COLUMN = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")


def _quote_column(name: str) -> str:
    if not _SAFE_COLUMN.fullmatch(name):
        raise ValueError(f"字段名不安全：{name}")
    return f'"{name}"'


def _sql_path(path: Path) -> str:
    return str(path.resolve()).replace("\\", "/").replace("'", "''")


def _resolve_files(raw_glob: Path) -> list[Path]:
    return sorted(raw_glob.parent.glob(raw_glob.name))


def load_duckdb_panel(
    source_columns: Mapping[str, Sequence[str] | None],
    start: str,
    end: str,
    *,
    source_globs: Mapping[str, Path],
    threads: int = 8,
    memory_limit: str = "8GB",
    temp_dir: Path | None = None,
    require_full_coverage: bool = True,
) -> pd.DataFrame:
    """用 DuckDB 按 date、code 合并多组 Parquet，并返回日度面板。"""

    if not source_columns:
        raise ValueError("至少需要一个数据源")
    connection = duckdb.connect(":memory:")
    try:
        connection.execute(f"SET threads = {max(1, int(threads))}")
        connection.execute(f"SET memory_limit = '{memory_limit}'")
        connection.execute("SET preserve_insertion_order = false")
        if temp_dir is not None:
            Path(temp_dir).mkdir(parents=True, exist_ok=True)
            connection.execute(f"SET temp_directory = '{_sql_path(Path(temp_dir))}'")

        ctes: list[str] = []
        selected = ["s0.date", "s0.code"]
        aliases: list[str] = []
        parameters: list[pd.Timestamp] = []
        seen = {"date", "code"}
        row_counts: list[int] = []

        for index, (source_name, requested) in enumerate(source_columns.items()):
            if source_name not in source_globs:
                raise KeyError(f"没有提供数据源路径：{source_name}")
            raw_glob = Path(source_globs[source_name])
            files = _resolve_files(raw_glob)
            if not files:
                raise FileNotFoundError(f"没有找到Parquet：{raw_glob}")
            relation = (
                f"read_parquet('{_sql_path(raw_glob)}', union_by_name = true)"
            )
            available_columns = [
                row[0]
                for row in connection.execute(
                    f"DESCRIBE SELECT * FROM {relation}"
                ).fetchall()
            ]
            available = set(available_columns)
            if not {"date", "code"}.issubset(available):
                raise ValueError(f"{source_name}缺少date或code")
            columns = (
                [name for name in available_columns if name not in {"date", "code"}]
                if requested is None
                else [name for name in requested if name not in {"date", "code"}]
            )
            missing = sorted(set(columns) - available)
            if missing:
                raise KeyError(f"{source_name}缺少字段：{', '.join(missing)}")
            duplicates = sorted(set(columns) & seen)
            if duplicates:
                raise ValueError(f"不同数据源有同名输出字段：{', '.join(duplicates)}")
            alias = f"s{index}"
            aliases.append(alias)
            projection = [
                "date",
                "lower(code) AS code",
                *[_quote_column(column) for column in columns],
            ]
            ctes.append(
                f"""
                {alias} AS (
                    SELECT {', '.join(projection)}
                    FROM {relation}
                    WHERE date BETWEEN ? AND ?
                )
                """
            )
            parameters.extend([pd.Timestamp(start), pd.Timestamp(end)])
            selected.extend(
                f"{alias}.{_quote_column(column)}" for column in columns
            )
            seen.update(columns)
            if require_full_coverage:
                count, distinct_count = connection.execute(
                    f"""
                    SELECT count(*), count(DISTINCT (date, lower(code)))
                    FROM {relation}
                    WHERE date BETWEEN ? AND ?
                    """,
                    [pd.Timestamp(start), pd.Timestamp(end)],
                ).fetchone()
                if count != distinct_count:
                    raise ValueError(f"{source_name}存在重复date、code记录")
                row_counts.append(int(count))

        joins = [aliases[0]]
        joins.extend(
            f"INNER JOIN {alias} USING (date, code)" for alias in aliases[1:]
        )
        query = f"""
            WITH {', '.join(ctes)}
            SELECT {', '.join(selected)}
            FROM {' '.join(joins)}
            ORDER BY s0.date, s0.code
        """
        panel = connection.execute(query, parameters).fetchdf()
    finally:
        connection.close()

    if panel.duplicated(["date", "code"]).any():
        raise ValueError("DuckDB合并结果存在重复date、code")
    if require_full_coverage and row_counts and any(
        count != row_counts[0] for count in row_counts[1:]
    ):
        raise ValueError(f"各数据源覆盖行数不一致：{row_counts}")
    if require_full_coverage and row_counts and len(panel) != row_counts[0]:
        raise ValueError(
            f"DuckDB合并丢失记录：基础{row_counts[0]}行，结果{len(panel)}行"
        )
    panel["date"] = pd.to_datetime(panel["date"])
    panel["code"] = panel["code"].astype("string").str.lower()
    for column in panel.select_dtypes(include=["number"]).columns:
        panel[column] = panel[column].astype(np.float32)
    return panel


def default_source_globs() -> dict[str, Path]:
    """返回当前日度缓存位置；只包含本文件能直接适配的五组。"""

    return {
        name: Path(factor_store.DEFAULT_SOURCES[name])
        for name in (
            "january_daily",
            "base_daily",
            "april_daily",
            "may_daily",
            "june_daily",
        )
    }


def load_group_panel(
    group_name: str,
    start: str,
    end: str,
    *,
    source_overrides: Mapping[str, Path] | None = None,
    threads: int = 8,
    memory_limit: str = "8GB",
    temp_dir: Path | None = None,
) -> pd.DataFrame:
    """读取一月至六月某个旧入口所需的完整日度面板。"""

    columns = required_columns_by_source(group_name)
    sources = default_source_globs()
    if source_overrides:
        sources.update({name: Path(path) for name, path in source_overrides.items()})
    panel = load_duckdb_panel(
        columns,
        start,
        end,
        source_globs=sources,
        threads=threads,
        memory_limit=memory_limit,
        temp_dir=temp_dir,
    )
    if group_name == "june":
        module = importlib.import_module(GROUPS[group_name].module_name)
        panel = module._collapse_june_panel(panel)
    return panel


def build_group_factor_matrices(
    group_name: str,
    panel: pd.DataFrame,
    start: str,
    end: str,
    *,
    qlib_dir: Path = DEFAULT_QLIB_DIR,
    min_stocks: int = 50,
    requested_codes: Sequence[str] | None = None,
    cgo_window: int = 100,
    illiq_window: int = 5,
) -> tuple[dict[str, np.ndarray], dict[str, np.ndarray]]:
    """调用旧程序的纯计算函数，不读取旧CSV，也不写回测结果。"""

    if group_name not in GROUPS:
        raise KeyError(f"未知分组：{group_name}")
    group = GROUPS[group_name]
    if not group.local_adapter:
        raise ValueError(f"{group_name}已有DuckDB入口：{group.module_name}.py")
    module = importlib.import_module(group.module_name)
    january = importlib.import_module("run_january_minute_backtest")
    qlib_dir = Path(qlib_dir)
    dates = january.read_calendar(qlib_dir, start, end)
    stocks, date_map, stock_map = january.make_axes(panel, dates)
    membership_rows = january.read_membership_rows(qlib_dir, start, end)
    membership, _ = january.build_membership(dates, stocks, membership_rows)

    if group_name == "january":
        factors, arrays = module.build_factor_matrices(
            panel,
            dates,
            stocks,
            date_map,
            stock_map,
            membership,
            cgo_window,
            illiq_window,
            min_stocks,
        )
    elif group_name in {"february_march", "april"}:
        factors, arrays = module.build_factor_matrices(
            panel,
            dates,
            stocks,
            date_map,
            stock_map,
            membership,
            min_stocks,
        )
    elif group_name == "may":
        module._QLIB_DIR = qlib_dir
        factors, arrays = module.build_factor_matrices(
            panel,
            dates,
            stocks,
            date_map,
            stock_map,
            membership,
            min_stocks,
        )
    elif group_name == "june":
        settings = january.Settings(
            cache_dir=Path("."),
            output_dir=Path("."),
            qlib_dir=qlib_dir,
            start=start,
            backtest_start=start,
            end=end,
            min_stocks=min_stocks,
        )
        factors, arrays = module.build_factor_matrices(
            panel,
            dates,
            stocks,
            date_map,
            stock_map,
            membership,
            settings,
        )
    else:
        raise AssertionError(group_name)

    allowed = set(group.result_codes)
    if requested_codes:
        requested = [resolve_result_code(code) for code in requested_codes]
        wrong_group = sorted(set(requested) - allowed)
        if wrong_group:
            raise ValueError(
                f"这些代码不属于{group_name}：{', '.join(wrong_group)}"
            )
        missing = sorted(set(requested) - set(factors))
        if missing:
            raise KeyError(f"旧计算函数没有返回：{', '.join(missing)}")
        factors = {code: factors[code] for code in requested}
    else:
        factors = {
            code: factors[code]
            for code in group.result_codes
            if code in factors
        }
    return factors, arrays


def iter_group_jobs(
    mapping_path: Path = DEFAULT_MAPPING_PATH,
) -> list[dict[str, object]]:
    """生成可供上层程序逐组执行的任务清单。"""

    mapped = read_mapped_implementations(mapping_path)
    by_group: dict[str, list[str]] = {name: [] for name in GROUPS}
    for item in mapped:
        by_group[item.group_name].append(item.result_code)
    jobs = []
    for name, group in GROUPS.items():
        codes = sorted(set(by_group[name]))
        if not codes:
            continue
        jobs.append(
            {
                "group_name": name,
                "module_name": group.module_name,
                "result_codes": codes,
                "source_names": list(group.source_names),
                "local_adapter": group.local_adapter,
                "action": (
                    "load_group_panel + build_group_factor_matrices"
                    if group.local_adapter
                    else f"调用{group.module_name}.py现有DuckDB入口"
                ),
            }
        )
    return jobs
