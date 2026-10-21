# -*- coding: utf-8 -*-
"""运行截至 2026-07-16 的完整 Level2 因子补测。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import traceback
from datetime import datetime
from pathlib import Path

import pandas as pd
import psutil

from build_level2_daily_features import BAD_DATES


ROOT = Path(__file__).resolve().parent
RESULT_ROOT = ROOT / "level2_calendar_backtest_results"
INPUT_ROOT = RESULT_ROOT / "input_20260716"
DAILY_BASE = INPUT_ROOT / "daily_base"
QLIB_DIR = INPUT_ROOT / "qlib_overlay"
DAILY_FEATURES = RESULT_ROOT / "full_daily_features_20260716"
FACTOR_OUTPUT = RESULT_ROOT / "full_factors_20260716"
BACKTEST_OUTPUT = RESULT_ROOT / "full_backtest_20260716"
TEMP_DIR = ROOT / "tmp" / "level2_full_20260716"
STATUS_PATH = RESULT_ROOT / "full_pipeline_status.json"
START = "2026-01-05"
END = "2026-07-16"
CONVERTER_PID = 24320


def write_status(stage: str, status: str, **extra: object) -> None:
    previous: dict[str, object] = {}
    if STATUS_PATH.is_file():
        try:
            previous = json.loads(STATUS_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            previous = {}
    previous.update(
        {
            "pipeline_pid": os.getpid(),
            "converter_pid": CONVERTER_PID,
            "start": START,
            "end": END,
            "stage": stage,
            "status": status,
            "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            **extra,
        }
    )
    STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = STATUS_PATH.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(previous, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, STATUS_PATH)


def run_step(name: str, command: list[str]) -> None:
    write_status(name, "running", command=command)
    print(f"\n[{datetime.now():%Y-%m-%d %H:%M:%S}] 开始：{name}", flush=True)
    print(" ".join(command), flush=True)
    subprocess.run(command, cwd=ROOT, check=True)
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] 完成：{name}", flush=True)
    write_status(name, "completed", command=command)


def expected_dates() -> list[str]:
    calendar = pd.to_datetime(
        pd.read_csv(QLIB_DIR / "calendars" / "day.txt", header=None)[0], errors="coerce"
    ).dropna()
    dates = pd.DatetimeIndex(calendar).normalize()
    dates = dates[(dates >= pd.Timestamp(START)) & (dates <= pd.Timestamp(END))]
    if dates.empty or dates[-1] != pd.Timestamp(END):
        raise RuntimeError("交易日历没有覆盖 2026-07-16")
    return [date.strftime("%Y-%m-%d") for date in dates if date.strftime("%Y-%m-%d") not in BAD_DATES]


def validate_inputs() -> list[str]:
    if not DAILY_BASE.is_dir() or not any(DAILY_BASE.glob("*.parquet")):
        raise FileNotFoundError(f"基础日线不存在：{DAILY_BASE}")
    dates = expected_dates()
    if len(dates) != 120:
        raise RuntimeError(f"有效交易日应为 120 天，实际 {len(dates)} 天")
    for kind in ("trades", "orders", "quotes"):
        root = Path(r"F:\2026(QQ群1097616051后续增量更新)\l2_a_share_parquet") / kind
        missing = [date for date in dates if not (root / f"trade_date={date}").is_dir()]
        if missing:
            raise FileNotFoundError(f"{kind} 缺少日期：{missing}")
    if psutil.pid_exists(CONVERTER_PID):
        status = psutil.Process(CONVERTER_PID).status()
        if status != psutil.STATUS_STOPPED:
            raise RuntimeError(f"F 盘转换程序没有挂起，当前状态：{status}")
    return dates


def validate_daily_features(dates: list[str]) -> None:
    actual = sorted(path.stem for path in DAILY_FEATURES.glob("*.parquet"))
    if actual != dates:
        missing = sorted(set(dates) - set(actual))
        extra = sorted(set(actual) - set(dates))
        raise RuntimeError(f"日数据日期不完整，缺少 {missing}，多出 {extra}")


def validate_factors() -> None:
    status_path = FACTOR_OUTPUT / "factor_status.csv"
    values_path = FACTOR_OUTPUT / "factor_values.parquet"
    if not status_path.is_file() or not values_path.is_file():
        raise FileNotFoundError("因子输出文件不完整")
    status = pd.read_csv(status_path)
    available = status[status["status"].eq("available")]
    unavailable = status[status["status"].eq("unavailable")]
    if len(status) != 146 or len(available) != 144 or len(unavailable) != 2:
        raise RuntimeError(
            f"因子数量不符：总数 {len(status)}，可用 {len(available)}，不可用 {len(unavailable)}"
        )
    last_dates = pd.to_datetime(available["last_date"], errors="coerce")
    if last_dates.isna().any() or not last_dates.eq(pd.Timestamp(END)).all():
        bad = available.loc[~last_dates.eq(pd.Timestamp(END)), ["factor_id", "last_date"]]
        raise RuntimeError(f"这些因子没有计算到 {END}：{bad.to_dict('records')}")


def validate_backtest() -> None:
    required = {
        "rank_ic.csv",
        "rebalance_returns.csv",
        "summary.csv",
        "yearly.csv",
        "methodology.json",
    }
    actual = {path.name for path in BACKTEST_OUTPUT.glob("*") if path.is_file()}
    missing = sorted(required - actual)
    if missing:
        raise FileNotFoundError(f"回测结果缺少：{missing}")
    summary = pd.read_csv(BACKTEST_OUTPUT / "summary.csv")
    if summary["factor_id"].nunique() != 144 or len(summary) != 288:
        raise RuntimeError(
            f"回测汇总数量不符：因子 {summary['factor_id'].nunique()}，行数 {len(summary)}"
        )


def main() -> int:
    try:
        dates = validate_inputs()
        write_status("准备", "completed", expected_daily_feature_files=len(dates))
        run_step(
            "生成 Level2 日数据",
            [
                sys.executable,
                "-u",
                "build_level2_daily_features.py",
                "--daily-dir",
                str(DAILY_BASE),
                "--output-dir",
                str(DAILY_FEATURES),
                "--temp-dir",
                str(TEMP_DIR),
                "--start",
                START,
                "--end",
                END,
                "--threads",
                "4",
                "--memory-limit",
                "6GB",
            ],
        )
        validate_daily_features(dates)
        run_step(
            "生成四版日历 Level2 因子",
            [
                sys.executable,
                "-u",
                "historical_level2_factors.py",
                "--input",
                str(DAILY_FEATURES),
                "--output-dir",
                str(FACTOR_OUTPUT),
            ],
        )
        validate_factors()
        run_step(
            "正式回测",
            [
                sys.executable,
                "-u",
                "run_level2_short_backtests.py",
                "--factor-input",
                str(FACTOR_OUTPUT / "factor_values.parquet"),
                "--base-cache-dir",
                str(DAILY_BASE),
                "--qlib-dir",
                str(QLIB_DIR),
                "--output-dir",
                str(BACKTEST_OUTPUT),
                "--start",
                START,
                "--end",
                END,
            ],
        )
        validate_backtest()
        write_status(
            "全部完成",
            "completed",
            daily_feature_files=len(dates),
            available_factors=144,
            backtest_summary_rows=288,
            converter_stopped_for_run=True,
            converter_resume_script=str((RESULT_ROOT / "resume_l2_converter.ps1").resolve()),
        )
        print("全部完成；F 盘转换程序仍保持停止。", flush=True)
        return 0
    except Exception as exc:
        write_status(
            "失败",
            "failed",
            error=str(exc),
            traceback=traceback.format_exc(),
        )
        raise


if __name__ == "__main__":
    raise SystemExit(main())
