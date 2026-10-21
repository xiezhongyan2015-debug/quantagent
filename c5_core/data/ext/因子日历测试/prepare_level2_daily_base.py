# -*- coding: utf-8 -*-
"""用本地股票日线和 Tushare 复权因子生成 Level2 回测基础日线。"""

from __future__ import annotations

import argparse
import json
import os
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd

from update_stock_daily import TushareClient, local_to_ts


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_DATA_DIR = Path(r"/Users/leslie/Downloads/中证500量化60min/c5_core/data/vendor/stock-trading-data-2025-12-23N")
DEFAULT_INPUT_DIR = BASE_DIR / "level2_calendar_backtest_results" / "input_20260716"
DEFAULT_OUTPUT_DIR = DEFAULT_INPUT_DIR / "daily_base"
DEFAULT_CODES_FILE = DEFAULT_INPUT_DIR / "selected_codes.txt"
DEFAULT_CALENDAR = DEFAULT_INPUT_DIR / "qlib_overlay" / "calendars" / "day.txt"
DEFAULT_ADJ_FILE = DEFAULT_INPUT_DIR / "adj_factors_20251001_20260716.csv"

SOURCE_COLUMNS = [
    "股票代码",
    "交易日期",
    "开盘价",
    "最高价",
    "最低价",
    "收盘价",
    "前收盘价",
    "成交量",
    "成交额",
    "流通市值",
    "总市值",
]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--codes-file", type=Path, default=DEFAULT_CODES_FILE)
    parser.add_argument("--calendar", type=Path, default=DEFAULT_CALENDAR)
    parser.add_argument("--adj-file", type=Path, default=DEFAULT_ADJ_FILE)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--start", default="2025-10-01")
    parser.add_argument("--end", default="2026-07-16")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--interval", type=float, default=0.17)
    return parser.parse_args(argv)


def normalize_code(value: str) -> str:
    raw = str(value).strip().lstrip("\ufeff").lower()
    if "." in raw:
        number, market = raw.split(".", 1)
        if len(number) == 6 and number.isdigit() and market in {"sh", "sz", "bj"}:
            return market + number
    text = Path(raw).stem
    if len(text) == 8 and text[:2] in {"sh", "sz", "bj"} and text[2:].isdigit():
        return text
    if len(text) == 8 and text[-2:] in {"sh", "sz", "bj"} and text[:6].isdigit():
        return text[-2:] + text[:6]
    raise ValueError(f"无法识别股票代码：{value}")


def read_codes(path: Path) -> list[str]:
    codes = {
        normalize_code(line)
        for line in path.read_text(encoding="utf-8-sig").splitlines()
        if line.strip()
    }
    if not codes:
        raise ValueError(f"股票清单为空：{path}")
    return sorted(codes)


def read_dates(calendar: Path, start: str, end: str) -> pd.DatetimeIndex:
    values = pd.to_datetime(pd.read_csv(calendar, header=None)[0], errors="coerce")
    dates = pd.DatetimeIndex(values.dropna()).normalize().drop_duplicates().sort_values()
    dates = dates[(dates >= pd.Timestamp(start)) & (dates <= pd.Timestamp(end))]
    if dates.empty or dates[-1] != pd.Timestamp(end):
        actual = dates[-1].strftime("%Y-%m-%d") if len(dates) else "无"
        raise ValueError(f"交易日历没有覆盖截止日 {end}，实际截止 {actual}")
    return dates


def fetch_adj_factors(
    client: TushareClient,
    dates: Iterable[pd.Timestamp],
    target: Path,
) -> pd.DataFrame:
    requested = pd.DatetimeIndex(dates).normalize().drop_duplicates().sort_values()
    if target.is_file():
        existing = pd.read_csv(target, dtype={"ts_code": str})
        existing["trade_date"] = pd.to_datetime(existing["trade_date"], errors="coerce")
        covered = set(existing["trade_date"].dropna().dt.normalize())
        if set(requested).issubset(covered):
            return existing

    rows: list[dict[str, object]] = []
    for position, date in enumerate(requested, start=1):
        rows.extend(
            client.query(
                "adj_factor",
                {"trade_date": date.strftime("%Y%m%d")},
                "ts_code,trade_date,adj_factor",
            )
        )
        if position == 1 or position % 10 == 0 or position == len(requested):
            print(f"复权因子 {position}/{len(requested)}：{date:%Y-%m-%d}", flush=True)

    frame = pd.DataFrame(rows)
    if frame.empty:
        raise RuntimeError("没有取得复权因子")
    frame["trade_date"] = pd.to_datetime(frame["trade_date"], errors="raise")
    frame["adj_factor"] = pd.to_numeric(frame["adj_factor"], errors="raise")
    frame = frame.drop_duplicates(["ts_code", "trade_date"], keep="last")
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    frame.to_csv(temporary, index=False, encoding="utf-8-sig")
    os.replace(temporary, target)
    return frame


def build_one_stock(
    source: Path,
    target: Path,
    start: str,
    end: str,
    factors: pd.DataFrame,
) -> dict[str, object]:
    code = normalize_code(source.stem)
    frame = pd.read_csv(
        source,
        encoding="gbk",
        skiprows=1,
        usecols=SOURCE_COLUMNS,
        low_memory=False,
    )
    frame["交易日期"] = pd.to_datetime(frame["交易日期"], errors="coerce")
    frame = frame[frame["交易日期"].between(start, end)].copy()
    frame = frame.sort_values("交易日期").drop_duplicates("交易日期", keep="last")
    if frame.empty:
        return {"code": code, "status": "empty", "rows": 0, "missing_factors": 0}

    current = factors[["trade_date", "adj_factor"]].copy()
    current["trade_date"] = pd.to_datetime(current["trade_date"], errors="coerce")
    current = current.dropna().drop_duplicates("trade_date", keep="last")
    frame = frame.merge(current, left_on="交易日期", right_on="trade_date", how="left")
    missing_factors = int(frame["adj_factor"].isna().sum())
    if missing_factors:
        return {
            "code": code,
            "status": "missing_factor",
            "rows": int(len(frame)),
            "missing_factors": missing_factors,
        }

    latest_factor = float(frame["adj_factor"].iloc[-1])
    if not np.isfinite(latest_factor) or latest_factor <= 0:
        raise ValueError(f"{code} 最后复权因子无效：{latest_factor}")
    ratio = frame["adj_factor"].astype(float) / latest_factor
    raw_close = pd.to_numeric(frame["收盘价"], errors="coerce")
    result = pd.DataFrame(
        {
            "date": frame["交易日期"],
            "code": code,
            "open": pd.to_numeric(frame["开盘价"], errors="coerce") * ratio,
            "high": pd.to_numeric(frame["最高价"], errors="coerce") * ratio,
            "low": pd.to_numeric(frame["最低价"], errors="coerce") * ratio,
            "close": raw_close * ratio,
            "volume": pd.to_numeric(frame["成交量"], errors="coerce"),
            "amount": pd.to_numeric(frame["成交额"], errors="coerce"),
            "float_shares": pd.to_numeric(frame["流通市值"], errors="coerce") / raw_close,
            "total_shares": pd.to_numeric(frame["总市值"], errors="coerce") / raw_close,
            "qfq_ratio": ratio,
            "raw_close": raw_close,
        }
    )
    adjusted_prior = result["close"].shift(1)
    first_prior = pd.to_numeric(frame["前收盘价"], errors="coerce") * ratio
    result["prev_close"] = adjusted_prior.fillna(first_prior)
    numeric = result.select_dtypes(include=[np.number]).columns
    result[numeric] = result[numeric].replace([np.inf, -np.inf], np.nan)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    result.to_parquet(temporary, compression="zstd", index=False)
    os.replace(temporary, target)
    return {
        "code": code,
        "status": "ok",
        "rows": int(len(result)),
        "missing_factors": 0,
        "first_date": result["date"].min().strftime("%Y-%m-%d"),
        "last_date": result["date"].max().strftime("%Y-%m-%d"),
    }


def _safe_build(task: tuple[str, str, str, str, list[tuple[str, float]]]) -> dict[str, object]:
    source, target, start, end, factor_rows = task
    try:
        factors = pd.DataFrame(factor_rows, columns=["trade_date", "adj_factor"])
        return build_one_stock(Path(source), Path(target), start, end, factors)
    except Exception as exc:
        return {"code": Path(source).stem.lower(), "status": "error", "error": str(exc)}


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.workers < 1:
        raise ValueError("--workers 必须至少为 1")
    dates = read_dates(args.calendar, args.start, args.end)
    codes = read_codes(args.codes_file)
    token = os.environ.get("TUSHARE_TOKEN")
    if not token:
        raise RuntimeError("没有找到环境变量 TUSHARE_TOKEN")
    client = TushareClient(token, interval=args.interval)
    adj = fetch_adj_factors(client, dates, args.adj_file)
    adj["code"] = adj["ts_code"].map(
        lambda value: normalize_code(str(value).split(".")[1].lower() + str(value).split(".")[0])
    )
    adj = adj[adj["code"].isin(codes)].copy()
    grouped = {
        code: list(group[["trade_date", "adj_factor"]].itertuples(index=False, name=None))
        for code, group in adj.groupby("code", sort=False)
    }

    args.output_dir.mkdir(parents=True, exist_ok=True)
    tasks = []
    skipped = 0
    missing_sources = []
    for code in codes:
        source = args.data_dir / f"{code}.csv"
        target = args.output_dir / f"{code}.parquet"
        if target.is_file():
            skipped += 1
            continue
        if not source.is_file():
            missing_sources.append(code)
            continue
        tasks.append((str(source), str(target), args.start, args.end, grouped.get(code, [])))
    print(
        f"股票 {len(codes)} 只，已有 {skipped} 只，待生成 {len(tasks)} 只，缺源文件 {len(missing_sources)} 只。",
        flush=True,
    )

    reports: list[dict[str, object]] = []
    with ProcessPoolExecutor(max_workers=args.workers) as executor:
        for position, report in enumerate(executor.map(_safe_build, tasks, chunksize=1), start=1):
            reports.append(report)
            if position == 1 or position % 100 == 0 or position == len(tasks):
                print(f"基础日线 {position}/{len(tasks)}", flush=True)

    failures = [item for item in reports if item.get("status") not in {"ok", "empty"}]
    summary = {
        "start": args.start,
        "end": args.end,
        "calendar_dates": len(dates),
        "selected_codes": len(codes),
        "existing_files": skipped,
        "created_files": sum(item.get("status") == "ok" for item in reports),
        "empty_files": sum(item.get("status") == "empty" for item in reports),
        "missing_source_codes": missing_sources,
        "failures": failures,
    }
    report_path = args.output_dir.parent / "daily_base_report.json"
    report_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in summary.items() if key != "failures"}, ensure_ascii=False))
    if failures:
        raise RuntimeError(f"有 {len(failures)} 只股票未生成，详见 {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
