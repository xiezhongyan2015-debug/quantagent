#!/usr/bin/env python3
"""为2026年因子排名复测准备独立的交易日、指数和成分股文件。"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import numpy as np
import pandas as pd

from update_stock_daily import TushareClient


BASE_DIR = Path(__file__).resolve().parent
SOURCE_QLIB = Path(r"/Users/leslie/Downloads/中证500量化60min/c5_core/data/qlib_data/qlib_bin")
DEFAULT_WORK_DIR = BASE_DIR / ".codex-artifact" / "2026_rank_check"
WORK_DIR = DEFAULT_WORK_DIR
OUTPUT_QLIB = WORK_DIR / "qlib_overlay"
CACHE_START = pd.Timestamp("2025-06-02")
END_DATE = pd.Timestamp("2026-05-29")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-qlib", type=Path, default=SOURCE_QLIB)
    parser.add_argument("--work-dir", type=Path, default=DEFAULT_WORK_DIR)
    parser.add_argument("--end", default=END_DATE.strftime("%Y-%m-%d"))
    return parser.parse_args(argv)


def tushare_code_to_qlib(code: str) -> str:
    number, market = code.upper().split(".")
    return f"{market}{number}"


def read_instruments(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(
        path,
        sep="\t",
        header=None,
        names=["code", "start", "end"],
        dtype=str,
    )
    frame["start"] = pd.to_datetime(frame["start"])
    frame["end"] = pd.to_datetime(frame["end"])
    return frame


def write_instruments(frame: pd.DataFrame, path: Path) -> None:
    output = frame.copy()
    output["start"] = output["start"].dt.strftime("%Y-%m-%d")
    output["end"] = output["end"].dt.strftime("%Y-%m-%d")
    output.to_csv(path, sep="\t", header=False, index=False, encoding="utf-8")


def fetch_index_weights(
    client: TushareClient, index_code: str
) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for month in pd.period_range("2026-01", END_DATE.to_period("M"), freq="M"):
        rows.extend(
            client.query(
                "index_weight",
                {
                    "index_code": index_code,
                    "start_date": month.start_time.strftime("%Y%m%d"),
                    "end_date": month.end_time.strftime("%Y%m%d"),
                },
                "index_code,con_code,trade_date,weight",
            )
        )
    frame = pd.DataFrame(rows).drop_duplicates(
        subset=["index_code", "con_code", "trade_date"]
    )
    if frame.empty:
        raise RuntimeError(f"没有取得{index_code}的2026年月末成分")
    frame["trade_date"] = pd.to_datetime(frame["trade_date"])
    frame["weight"] = pd.to_numeric(frame["weight"], errors="coerce")
    return frame.sort_values(["trade_date", "con_code"]).reset_index(drop=True)


def append_snapshot_ranges(
    original: pd.DataFrame,
    snapshots: pd.DataFrame,
    calendar: pd.DatetimeIndex,
) -> pd.DataFrame:
    dates = sorted(snapshots["trade_date"].unique())
    additions = []
    for position, snapshot_date in enumerate(dates):
        start = pd.Timestamp(snapshot_date)
        if position + 1 < len(dates):
            next_date = pd.Timestamp(dates[position + 1])
            prior_dates = calendar[calendar < next_date]
            end = prior_dates[-1]
        else:
            end = END_DATE
        codes = snapshots.loc[
            snapshots["trade_date"] == snapshot_date, "con_code"
        ].map(tushare_code_to_qlib)
        additions.extend((code, start, end) for code in codes)
    appended = pd.DataFrame(additions, columns=["code", "start", "end"])
    return (
        pd.concat([original, appended], ignore_index=True)
        .drop_duplicates(subset=["code", "start", "end"])
        .sort_values(["code", "start", "end"])
        .reset_index(drop=True)
    )


def build_index_close(
    index_daily: pd.DataFrame,
    old_calendar: pd.DatetimeIndex,
    new_calendar: pd.DatetimeIndex,
) -> None:
    source_path = SOURCE_QLIB / "features" / "sh000985" / "close.day.bin"
    raw = np.fromfile(source_path, dtype="<f4")
    start_index = int(raw[0])
    old_values = raw[1:].astype(np.float64)
    old_end_index = start_index + len(old_values) - 1
    old_end_date = old_calendar[old_end_index]

    close = index_daily.set_index("trade_date")["close"].astype(float)
    if old_end_date not in close.index:
        raise RuntimeError(f"中证全指接口缺少衔接日{old_end_date:%Y-%m-%d}")
    scale = old_values[-1] / float(close.loc[old_end_date])
    added_dates = new_calendar[new_calendar > old_end_date]
    missing = added_dates.difference(close.index)
    if len(missing):
        raise RuntimeError(f"中证全指接口缺少交易日：{missing.strftime('%Y-%m-%d').tolist()}")
    added_values = close.reindex(added_dates).to_numpy(dtype=np.float64) * scale

    output_dir = OUTPUT_QLIB / "features" / "sh000985"
    output_dir.mkdir(parents=True, exist_ok=True)
    combined = np.concatenate(
        [np.array([start_index], dtype="<f4"), old_values, added_values]
    ).astype("<f4")
    combined.tofile(output_dir / "close.day.bin")


def main(argv: list[str] | None = None) -> int:
    global SOURCE_QLIB, WORK_DIR, OUTPUT_QLIB, END_DATE
    args = parse_args(argv)
    SOURCE_QLIB = args.source_qlib.resolve()
    WORK_DIR = args.work_dir.resolve()
    OUTPUT_QLIB = WORK_DIR / "qlib_overlay"
    END_DATE = pd.Timestamp(args.end).normalize()
    if END_DATE.year != 2026:
        raise ValueError("--end 必须是 2026 年的日期")
    token = os.environ.get("TUSHARE_TOKEN")
    if not token:
        raise RuntimeError("没有找到环境变量TUSHARE_TOKEN")
    client = TushareClient(token)
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    (OUTPUT_QLIB / "calendars").mkdir(parents=True, exist_ok=True)
    (OUTPUT_QLIB / "instruments").mkdir(parents=True, exist_ok=True)

    index_rows = client.query(
        "index_daily",
        {
            "ts_code": "000985.CSI",
            "start_date": "20260129",
            "end_date": END_DATE.strftime("%Y%m%d"),
        },
        "ts_code,trade_date,close",
    )
    index_daily = pd.DataFrame(index_rows)
    if index_daily.empty:
        raise RuntimeError("没有取得中证全指2026年日线")
    index_daily["trade_date"] = pd.to_datetime(index_daily["trade_date"])
    index_daily["close"] = pd.to_numeric(index_daily["close"], errors="raise")
    index_daily = index_daily.sort_values("trade_date").reset_index(drop=True)
    index_daily.to_csv(
        WORK_DIR / "index_daily_000985_2026.csv", index=False, encoding="utf-8-sig"
    )

    old_calendar = pd.DatetimeIndex(
        pd.read_csv(SOURCE_QLIB / "calendars" / "day.txt", header=None)[0]
    )
    appended_dates = pd.DatetimeIndex(
        index_daily.loc[index_daily["trade_date"] > old_calendar[-1], "trade_date"]
    )
    new_calendar = old_calendar.append(appended_dates).drop_duplicates().sort_values()
    if new_calendar[-1] != END_DATE:
        raise RuntimeError(
            f"新交易日历截止{new_calendar[-1]:%Y-%m-%d}，不是预期的{END_DATE:%Y-%m-%d}"
        )
    pd.Series(new_calendar.strftime("%Y-%m-%d")).to_csv(
        OUTPUT_QLIB / "calendars" / "day.txt",
        header=False,
        index=False,
        encoding="utf-8",
    )
    build_index_close(index_daily, old_calendar, new_calendar)

    index_specs = {
        "csi800": "000906.SH",
        "csi1000": "000852.SH",
    }
    final_instruments = []
    snapshot_summary = {}
    for name, index_code in index_specs.items():
        original = read_instruments(SOURCE_QLIB / "instruments" / f"{name}.txt")
        snapshots = fetch_index_weights(client, index_code)
        snapshots.to_csv(
            WORK_DIR / f"{name}_weights_2026.csv",
            index=False,
            encoding="utf-8-sig",
        )
        combined = append_snapshot_ranges(original, snapshots, new_calendar)
        write_instruments(combined, OUTPUT_QLIB / "instruments" / f"{name}.txt")
        final_instruments.append(combined)
        snapshot_summary[name] = {
            date.strftime("%Y-%m-%d"): int(count)
            for date, count in snapshots.groupby("trade_date").size().items()
        }

    all_instruments = pd.concat(final_instruments, ignore_index=True)
    selected = sorted(
        all_instruments.loc[
            (all_instruments["start"] <= END_DATE)
            & (all_instruments["end"] >= CACHE_START),
            "code",
        ]
        .str.lower()
        .unique()
    )
    (WORK_DIR / "selected_codes.txt").write_text(
        "\n".join(selected) + "\n", encoding="utf-8"
    )

    manifest = {
        "cache_start": CACHE_START.strftime("%Y-%m-%d"),
        "end_date": END_DATE.strftime("%Y-%m-%d"),
        "source_qlib_end": old_calendar[-1].strftime("%Y-%m-%d"),
        "overlay_calendar_end": new_calendar[-1].strftime("%Y-%m-%d"),
        "calendar_days_2026": int((new_calendar.year == 2026).sum()),
        "selected_codes": len(selected),
        "snapshot_counts": snapshot_summary,
        "index_source": "Tushare index_daily 000985.CSI",
        "membership_source": "Tushare index_weight 000906.SH and 000852.SH",
    }
    (WORK_DIR / "input_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
