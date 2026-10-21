from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
from collections.abc import Sequence

import numpy as np
import pandas as pd


DEFAULT_QLIB_ROOT = Path(r"/Users/leslie/Downloads/中证500量化60min/c5_core/data/qlib_data/qlib_bin")
REQUIRED_FEATURES = (
    "open",
    "high",
    "low",
    "close",
    "volume",
    "amount",
    "factor",
)
NULLABLE_CONNECTOR_COLUMNS = (
    "float_shares",
    "total_shares",
    "gu_1m",
    "gd_1m",
    "rbar_up17",
    "rbar_down17",
    "r_0931_1000",
    "r_1001_1030",
    "overnight_return",
)
OUTPUT_COLUMNS = (
    "date",
    "code",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "amount",
    "float_shares",
    "total_shares",
    "qfq_ratio",
    "vwap_qfq",
    "prev_close",
    "gu_1m",
    "gd_1m",
    "rbar_up17",
    "rbar_down17",
    "r_0931_1000",
    "r_1001_1030",
    "overnight_return",
)
AVAILABLE_FIELDS = (
    "open",
    "high",
    "low",
    "close",
    "volume",
    "amount",
    "prev_close",
)
RAW_DAILY_COLUMNS = {
    "股票代码": "raw_code",
    "股票名称": "stock_name",
    "交易日期": "date",
    "开盘价": "raw_open",
    "收盘价": "raw_close",
    "前收盘价": "raw_prev_close",
    "流通市值": "float_market_cap",
    "总市值": "total_market_cap",
}
RAW_DAILY_OUTPUT_COLUMNS = (
    "raw_open",
    "raw_prev_close",
    "stock_name",
    "is_st",
    "is_delisting",
    "float_market_cap",
    "total_market_cap",
)
RAW_DAILY_AVAILABLE_FIELDS = (
    "raw_prev_close",
    "is_st",
    "is_delisting",
    "float_shares",
    "total_shares",
    "float_market_cap",
    "total_market_cap",
)

_A_SHARE_CODE = re.compile(
    r"^(?:sh(?:600|601|603|605|688|689)|sz(?:000|001|002|003|300|301|302))\d{3}$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ConversionSummary:
    code: str
    output_path: Path
    rows: int
    first_date: pd.Timestamp | None
    last_date: pd.Timestamp | None
    raw_outside_range_dropped_rows: int = 0


def is_a_share_code(value: object) -> bool:
    """只接受当前任务指定的沪深 A 股代码段。"""

    return bool(_A_SHARE_CODE.fullmatch(str(value).strip()))


def _as_date(value: object | None, *, name: str) -> pd.Timestamp | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        raise ValueError(f"{name} 不是有效日期: {value}")
    timestamp = pd.Timestamp(parsed)
    if timestamp.tzinfo is not None:
        timestamp = timestamp.tz_localize(None)
    return timestamp.normalize()


def read_day_calendar(qlib_root: str | os.PathLike[str]) -> pd.DatetimeIndex:
    calendar_path = Path(qlib_root) / "calendars" / "day.txt"
    if not calendar_path.is_file():
        raise FileNotFoundError(f"找不到日历文件: {calendar_path}")

    raw_dates = [
        line.strip()
        for line in calendar_path.read_text(encoding="utf-8-sig").splitlines()
        if line.strip()
    ]
    if not raw_dates:
        raise ValueError(f"日历文件为空: {calendar_path}")

    parsed = pd.to_datetime(raw_dates, errors="coerce")
    if bool(pd.isna(parsed).any()):
        bad_position = int(np.flatnonzero(pd.isna(parsed))[0])
        raise ValueError(f"日历含无效日期: {raw_dates[bad_position]}")
    calendar = pd.DatetimeIndex(parsed).normalize()
    if calendar.has_duplicates:
        raise ValueError("日历含重复日期")
    if not calendar.is_monotonic_increasing:
        raise ValueError("日历日期必须递增")
    return calendar


def read_day_feature(
    feature_path: str | os.PathLike[str],
    calendar: pd.DatetimeIndex,
) -> pd.Series:
    """读取 Qlib 日频文件；首个 float32 是该文件在日历中的起始位置。"""

    path = Path(feature_path)
    if not path.is_file():
        raise FileNotFoundError(f"找不到日频特征文件: {path}")
    if path.stat().st_size % np.dtype("<f4").itemsize != 0:
        raise ValueError(f"日频特征文件长度不是 float32 的整数倍: {path}")

    raw = np.fromfile(path, dtype="<f4")
    if raw.size == 0:
        raise ValueError(f"日频特征文件为空: {path}")

    raw_start = float(raw[0])
    start = int(raw_start) if np.isfinite(raw_start) else -1
    if start < 0 or not np.isclose(raw_start, start, rtol=0.0, atol=1e-6):
        raise ValueError(f"日频特征文件的起始日序号无效: {path}")

    values = raw[1:].astype("float64", copy=False)
    stop = start + len(values)
    if start > len(calendar) or stop > len(calendar):
        raise ValueError(
            f"日频特征超出 day.txt 范围: {path}，起始={start}，数据数={len(values)}"
        )
    return pd.Series(values, index=calendar[start:stop], dtype="float64")


def _valid_market_rows(frame: pd.DataFrame) -> pd.Series:
    prices = frame.loc[:, ["open", "high", "low", "close"]]
    finite_prices = np.isfinite(prices).all(axis=1)
    positive_prices = prices.gt(0.0).all(axis=1)
    price_order = (
        frame["high"].ge(frame[["open", "close"]].max(axis=1))
        & frame["low"].le(frame[["open", "close"]].min(axis=1))
        & frame["high"].ge(frame["low"])
    )
    finite_trade_data = (
        np.isfinite(frame["volume"])
        & np.isfinite(frame["amount"])
        & np.isfinite(frame["factor"])
    )
    valid_trade_data = (
        frame["volume"].ge(0.0)
        & frame["amount"].ge(0.0)
        & frame["factor"].gt(0.0)
    )
    return finite_prices & positive_prices & price_order & finite_trade_data & valid_trade_data


def load_stock_frame(
    feature_dir: str | os.PathLike[str],
    calendar: pd.DatetimeIndex,
    *,
    start_date: object | None = None,
    end_date: object | None = None,
) -> pd.DataFrame:
    directory = Path(feature_dir)
    code = directory.name.strip().lower()
    if not is_a_share_code(code):
        raise ValueError(f"不是任务允许的沪深 A 股代码: {directory.name}")

    start = _as_date(start_date, name="start_date")
    end = _as_date(end_date, name="end_date")
    if start is not None and end is not None and end < start:
        raise ValueError("end_date 不能早于 start_date")

    features = {
        name: read_day_feature(directory / f"{name}.day.bin", calendar).rename(name)
        for name in REQUIRED_FEATURES
    }
    vwap_path = directory / "vwap.day.bin"
    if vwap_path.is_file():
        features["vwap"] = read_day_feature(vwap_path, calendar).rename("vwap")

    frame = pd.concat(features.values(), axis=1).sort_index()
    frame = frame.loc[_valid_market_rows(frame)].copy()

    # Qlib 价格以首日为基准连续复权。用全历史最后一个复权因子换成项目
    # 原有的前复权价格口径，日期截取前后都使用同一个价格尺度。
    factor_reference = frame.loc[frame.index <= end] if end is not None else frame
    base_factor = (
        float(factor_reference["factor"].iloc[-1])
        if not factor_reference.empty
        else 1.0
    )
    if not np.isfinite(base_factor) or base_factor <= 0.0:
        raise ValueError(f"股票没有可用的末日复权因子: {code}")
    for column in ("open", "high", "low", "close"):
        frame[column] = frame[column] / base_factor
    if "vwap" in frame.columns:
        frame["vwap"] = frame["vwap"] / base_factor
    frame["qfq_ratio"] = frame["factor"] / base_factor

    # Qlib 中国日线中的成交量是复权后的手数，成交额单位是千元。
    frame["volume"] = frame["volume"] * frame["factor"] * 100.0
    frame["amount"] = frame["amount"] * 1000.0
    frame["prev_close"] = frame["close"].shift(1)

    # 先从完整有效历史算前收盘，再限制输出日期，确保复牌日和区间首日正确。
    if start is not None:
        frame = frame.loc[frame.index >= start]
    if end is not None:
        frame = frame.loc[frame.index <= end]

    result = pd.DataFrame(index=frame.index)
    result["date"] = pd.DatetimeIndex(frame.index)
    result["code"] = code
    for column in ("open", "high", "low", "close", "volume", "amount"):
        result[column] = frame[column].astype("float64")
    for column in NULLABLE_CONNECTOR_COLUMNS:
        result[column] = pd.Series(np.nan, index=result.index, dtype="float64")
    result["qfq_ratio"] = frame["qfq_ratio"].astype("float64")
    result["vwap_qfq"] = (
        frame["vwap"].astype("float64")
        if "vwap" in frame.columns
        else pd.Series(np.nan, index=result.index, dtype="float64")
    )
    result["prev_close"] = frame["prev_close"].astype("float64")
    return result.loc[:, OUTPUT_COLUMNS].reset_index(drop=True)


def load_raw_daily_frame(
    raw_daily_root: str | os.PathLike[str],
    code: str,
) -> pd.DataFrame:
    """读取逐股原始日线，并保留回测需要的未复权字段。"""

    normalized_code = str(code).strip().lower()
    path = Path(raw_daily_root).expanduser() / f"{normalized_code}.csv"
    if not path.is_file():
        raise FileNotFoundError(f"找不到原始日线文件: {path}")

    try:
        raw = pd.read_csv(path, encoding="gb18030", skiprows=1)
    except Exception as exc:
        raise ValueError(f"读取原始日线失败: {path}") from exc

    missing_columns = sorted(set(RAW_DAILY_COLUMNS) - set(raw.columns))
    if missing_columns:
        raise ValueError(
            f"原始日线缺少字段: {path}; {', '.join(missing_columns)}"
        )

    result = raw.loc[:, list(RAW_DAILY_COLUMNS)].rename(columns=RAW_DAILY_COLUMNS)
    result["raw_code"] = result["raw_code"].astype("string").str.strip().str.lower()
    wrong_code = result["raw_code"].ne(normalized_code).fillna(True)
    if bool(wrong_code.any()):
        examples = result.loc[wrong_code, "raw_code"].head(3).tolist()
        raise ValueError(
            f"原始日线含有其他股票代码: {path}; 样例={examples}"
        )

    result["date"] = pd.to_datetime(result["date"], errors="coerce")
    invalid_dates = result["date"].isna()
    if bool(invalid_dates.any()):
        raise ValueError(f"原始日线含有无效日期: {path}")
    result["date"] = result["date"].dt.normalize()
    if bool(result["date"].duplicated().any()):
        duplicate_dates = (
            result.loc[result["date"].duplicated(keep=False), "date"]
            .dt.strftime("%Y-%m-%d")
            .head(3)
            .tolist()
        )
        raise ValueError(
            f"原始日线含有重复日期: {path}; 样例={duplicate_dates}"
        )

    for column in (
        "raw_open",
        "raw_close",
        "raw_prev_close",
        "float_market_cap",
        "total_market_cap",
    ):
        result[column] = pd.to_numeric(result[column], errors="coerce")

    result["stock_name"] = result["stock_name"].astype("string")
    return result.drop(columns="raw_code")


def classify_is_st_names(stock_names: pd.Series) -> pd.Series:
    """按行情简称判断风险警示状态。"""

    normalized = stock_names.astype("string").str.replace(r"\s+", "", regex=True)
    normalized = normalized.str.upper()
    # XD、XR、DR、N 是行情展示前缀，不属于股票的基础简称；组合出现时逐层去掉。
    base_names = normalized.str.replace(
        r"^(?:(?:XD|XR|DR|N))+",
        "",
        regex=True,
    )
    return base_names.str.startswith(("ST", "*ST", "SST", "S*ST"), na=False)


def merge_raw_daily_frame(
    qlib_frame: pd.DataFrame,
    raw_daily_root: str | os.PathLike[str],
    code: str,
) -> pd.DataFrame:
    """按股票代码和日期严格合并原始日线资料。"""

    merged, _ = _merge_raw_daily_frame_with_stats(
        qlib_frame,
        raw_daily_root,
        code,
    )
    return merged


def _merge_raw_daily_frame_with_stats(
    qlib_frame: pd.DataFrame,
    raw_daily_root: str | os.PathLike[str],
    code: str,
) -> tuple[pd.DataFrame, int]:
    """合并原始日线，并返回因代码真实日期范围而删除的行数。"""

    if qlib_frame.empty:
        result = qlib_frame.copy()
        result["raw_open"] = pd.Series(dtype="float64")
        result["raw_prev_close"] = pd.Series(dtype="float64")
        result["stock_name"] = pd.Series(dtype="string")
        result["is_st"] = pd.Series(dtype="bool")
        result["is_delisting"] = pd.Series(dtype="bool")
        result["float_market_cap"] = pd.Series(dtype="float64")
        result["total_market_cap"] = pd.Series(dtype="float64")
        return result.loc[:, (*OUTPUT_COLUMNS, *RAW_DAILY_OUTPUT_COLUMNS)], 0

    raw = load_raw_daily_frame(raw_daily_root, code)
    if raw.empty:
        raise ValueError(f"原始日线没有任何交易日: {code}")
    raw_first_date = pd.Timestamp(raw["date"].min())
    raw_last_date = pd.Timestamp(raw["date"].max())
    inside_raw_range = qlib_frame["date"].between(
        raw_first_date,
        raw_last_date,
        inclusive="both",
    )
    dropped_rows = int((~inside_raw_range).sum())
    ranged_qlib = qlib_frame.loc[inside_raw_range].copy()

    merged = ranged_qlib.merge(
        raw,
        on="date",
        how="left",
        validate="one_to_one",
        indicator=True,
    )
    unmatched = merged["_merge"].ne("both")
    if bool(unmatched.any()):
        dates = (
            merged.loc[unmatched, "date"]
            .dt.strftime("%Y-%m-%d")
            .head(5)
            .tolist()
        )
        raise ValueError(
            f"Qlib 行没有匹配的原始日线: {code}; 日期样例={dates}"
        )
    merged = merged.drop(columns="_merge")

    for column in (
        "raw_open",
        "raw_close",
        "raw_prev_close",
        "float_market_cap",
        "total_market_cap",
    ):
        values = merged[column].to_numpy(dtype="float64", na_value=np.nan)
        invalid = ~np.isfinite(values) | (values <= 0.0)
        if bool(invalid.any()):
            dates = (
                merged.loc[invalid, "date"]
                .dt.strftime("%Y-%m-%d")
                .head(5)
                .tolist()
            )
            raise ValueError(
                f"原始日线 {column} 缺失或无效: {code}; 日期样例={dates}"
            )

    merged["float_shares"] = merged["float_market_cap"] / merged["raw_close"]
    merged["total_shares"] = merged["total_market_cap"] / merged["raw_close"]

    normalized_names = merged["stock_name"].str.replace(
        r"\s+", "", regex=True
    )
    missing_names = normalized_names.isna() | normalized_names.eq("")
    if bool(missing_names.any()):
        dates = (
            merged.loc[missing_names, "date"]
            .dt.strftime("%Y-%m-%d")
            .head(5)
            .tolist()
        )
        raise ValueError(f"原始日线股票名称为空: {code}; 日期样例={dates}")
    merged["stock_name"] = merged["stock_name"].astype("string")
    merged["is_st"] = classify_is_st_names(merged["stock_name"])
    merged["is_delisting"] = normalized_names.str.contains("退", regex=False, na=False)
    return (
        merged.loc[:, (*OUTPUT_COLUMNS, *RAW_DAILY_OUTPUT_COLUMNS)],
        dropped_rows,
    )


def _resolve_feature_directories(
    qlib_root: Path,
    codes: Sequence[str] | None,
) -> list[Path]:
    feature_root = qlib_root / "features"
    if not feature_root.is_dir():
        raise FileNotFoundError(f"找不到特征目录: {feature_root}")

    available = {
        path.name.lower(): path
        for path in feature_root.iterdir()
        if path.is_dir() and is_a_share_code(path.name)
    }
    if codes is None:
        selected = sorted(available)
    else:
        selected = []
        for raw_code in codes:
            code = str(raw_code).strip().lower()
            if not is_a_share_code(code):
                raise ValueError(f"不是任务允许的沪深 A 股代码: {raw_code}")
            if code not in available:
                raise FileNotFoundError(f"Qlib 特征目录不存在: {feature_root / code}")
            if code not in selected:
                selected.append(code)
        selected.sort()

    if not selected:
        raise ValueError(f"没有找到任务允许的沪深 A 股目录: {feature_root}")
    return [available[code] for code in selected]


def convert_qlib_daily_to_parquet(
    qlib_root: str | os.PathLike[str],
    output_dir: str | os.PathLike[str],
    *,
    start_date: object | None = None,
    end_date: object | None = None,
    codes: Sequence[str] | None = None,
    raw_daily_root: str | os.PathLike[str] | None = None,
    overwrite: bool = False,
) -> list[ConversionSummary]:
    root = Path(qlib_root).expanduser()
    target_dir = Path(output_dir).expanduser()
    if raw_daily_root is not None:
        raw_root = Path(raw_daily_root).expanduser()
        if not raw_root.is_dir():
            raise FileNotFoundError(f"原始日线目录不存在: {raw_root}")
    calendar = read_day_calendar(root)
    start = _as_date(start_date, name="start_date")
    end = _as_date(end_date, name="end_date")
    if start is not None and end is not None and end < start:
        raise ValueError("end_date 不能早于 start_date")
    feature_directories = _resolve_feature_directories(root, codes)
    target_dir.mkdir(parents=True, exist_ok=True)

    summaries: list[ConversionSummary] = []
    for feature_dir in feature_directories:
        code = feature_dir.name.lower()
        output_path = target_dir / f"{code}.parquet"
        if output_path.exists() and not overwrite:
            raise FileExistsError(f"输出文件已存在，请传 --overwrite 后重试: {output_path}")

        frame = load_stock_frame(
            feature_dir,
            calendar,
            start_date=start,
            end_date=end,
        )
        raw_outside_range_dropped_rows = 0
        if raw_daily_root is not None:
            frame, raw_outside_range_dropped_rows = (
                _merge_raw_daily_frame_with_stats(frame, raw_daily_root, code)
            )
        frame.to_parquet(output_path, index=False)
        summaries.append(
            ConversionSummary(
                code=code,
                output_path=output_path,
                rows=len(frame),
                first_date=(pd.Timestamp(frame["date"].iloc[0]) if not frame.empty else None),
                last_date=(pd.Timestamp(frame["date"].iloc[-1]) if not frame.empty else None),
                raw_outside_range_dropped_rows=raw_outside_range_dropped_rows,
            )
        )

    actual_first_dates = [item.first_date for item in summaries if item.first_date is not None]
    actual_last_dates = [item.last_date for item in summaries if item.last_date is not None]
    nonempty_summaries = [item for item in summaries if item.rows > 0]
    total_rows = sum(item.rows for item in summaries)
    manifest = {
        "source_root": str(root.resolve()),
        "date_range": {
            "start": (start or pd.Timestamp(calendar.min())).strftime("%Y-%m-%d"),
            "end": (end or pd.Timestamp(calendar.max())).strftime("%Y-%m-%d"),
        },
        "actual_date_range": {
            "start": min(actual_first_dates).strftime("%Y-%m-%d") if actual_first_dates else None,
            "end": max(actual_last_dates).strftime("%Y-%m-%d") if actual_last_dates else None,
        },
        "stock_filter": {
            "exchanges": ["SH", "SZ"],
            "code_pattern": _A_SHARE_CODE.pattern,
        },
        "stock_count": len(nonempty_summaries),
        "stock_file_count": len(summaries),
        "row_count": total_rows,
        "available_fields": list(AVAILABLE_FIELDS),
    }
    if raw_daily_root is not None:
        outside_range_dropped_rows = sum(
            item.raw_outside_range_dropped_rows for item in summaries
        )
        outside_range_dropped_codes = [
            item.code
            for item in summaries
            if item.raw_outside_range_dropped_rows > 0
        ]
        manifest["raw_daily"] = {
            "source_root": str(raw_root.resolve()),
            "qlib_candidate_row_count": total_rows + outside_range_dropped_rows,
            "required_row_count": total_rows,
            "matched_row_count": total_rows,
            "match_rate": 1.0 if total_rows else None,
            "outside_range_dropped_row_count": outside_range_dropped_rows,
            "outside_range_dropped_codes": outside_range_dropped_codes,
        }
        manifest["available_fields"].extend(RAW_DAILY_AVAILABLE_FIELDS)
    (target_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return summaries


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="把 Qlib 沪深 A 股日频二进制资料转成项目使用的逐股 Parquet",
    )
    parser.add_argument(
        "--qlib-root",
        default=os.getenv("QUANTA_QLIB_ROOT", str(DEFAULT_QLIB_ROOT)),
        help="Qlib 根目录，目录下应有 calendars/day.txt 和 features",
    )
    parser.add_argument(
        "--output-dir",
        default="data_cache/qlib_daily_by_stock",
        help="逐股 Parquet 输出目录",
    )
    parser.add_argument(
        "--raw-daily-root",
        help="可选的逐股原始日线 CSV 目录；文件名应为股票代码.csv",
    )
    parser.add_argument("--start-date", help="可选的输出开始日期，包含当天")
    parser.add_argument("--end-date", help="可选的输出结束日期，包含当天")
    parser.add_argument(
        "--codes",
        nargs="*",
        help="可选的股票代码，例如 sh600000 sz000001；不传则处理全部允许代码",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="允许覆盖同名 Parquet",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    summaries = convert_qlib_daily_to_parquet(
        args.qlib_root,
        args.output_dir,
        start_date=args.start_date,
        end_date=args.end_date,
        codes=args.codes,
        raw_daily_root=args.raw_daily_root,
        overwrite=bool(args.overwrite),
    )
    total_rows = sum(summary.rows for summary in summaries)
    print(f"转换完成：{len(summaries)} 只股票，{total_rows} 行，输出到 {Path(args.output_dir)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
