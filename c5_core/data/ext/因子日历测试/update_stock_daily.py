#!/usr/bin/env python3
"""把本地股票日线 CSV 补到指定日期。"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sqlite3
import sys
import tempfile
import time
import urllib.error
import urllib.request
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path


API_URL = "https://api.tushare.pro"
NOTE_LINE = "数据由邢不行整理，对数据字段有疑问的，可以直接微信私信邢不行，微信号：xbx6064"
HEADER = [
    "股票代码",
    "股票名称",
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


class TushareClient:
    def __init__(self, token: str, interval: float = 0.17) -> None:
        self.token = token
        self.interval = interval
        self.last_call_at = 0.0

    def query(
        self,
        api_name: str,
        params: dict[str, object] | None = None,
        fields: str = "",
        retries: int = 7,
    ) -> list[dict[str, object]]:
        payload = {
            "api_name": api_name,
            "token": self.token,
            "params": params or {},
            "fields": fields,
        }
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        last_error: Exception | None = None

        for attempt in range(retries):
            wait_for = self.interval - (time.monotonic() - self.last_call_at)
            if wait_for > 0:
                time.sleep(wait_for)
            request = urllib.request.Request(
                API_URL,
                data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            try:
                with urllib.request.urlopen(request, timeout=90) as response:
                    raw = response.read().decode("utf-8")
                self.last_call_at = time.monotonic()
                result = json.loads(raw, parse_float=Decimal)
                if result.get("code") != 0:
                    message = str(result.get("msg", "未知错误"))
                    if any(word in message for word in ("每分钟", "频率", "稍后", "timeout", "超时")):
                        raise RuntimeError(message)
                    raise ValueError(f"{api_name} 返回错误：{message}")
                data = result.get("data") or {}
                names = data.get("fields") or []
                items = data.get("items") or []
                return [dict(zip(names, item)) for item in items]
            except (urllib.error.URLError, TimeoutError, RuntimeError) as exc:
                self.last_call_at = time.monotonic()
                last_error = exc
                if attempt + 1 == retries:
                    break
                time.sleep(min(5 * (attempt + 1), 30))

        raise RuntimeError(f"{api_name} 多次请求失败：{last_error}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="补齐本地股票日线 CSV")
    parser.add_argument("--data-dir", required=True, type=Path)
    parser.add_argument("--end-date", required=True, help="格式：YYYY-MM-DD")
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--interval", type=float, default=0.17)
    return parser.parse_args()


def as_decimal(value: object, field: str, ts_code: str, trade_date: str) -> Decimal:
    if value is None or value == "":
        raise ValueError(f"{trade_date} {ts_code} 的 {field} 为空")
    try:
        return Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError(f"{trade_date} {ts_code} 的 {field} 不是数字：{value}") from exc


def decimal_text(value: Decimal) -> str:
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    if "." not in text:
        text += ".0"
    return text


def api_date(value: date) -> str:
    return value.strftime("%Y%m%d")


def csv_date(value: str) -> str:
    return f"{value[:4]}-{value[4:6]}-{value[6:8]}"


def ts_to_local(ts_code: str) -> str:
    symbol, exchange = ts_code.split(".", 1)
    prefixes = {"SH": "sh", "SZ": "sz", "BJ": "bj"}
    if exchange not in prefixes:
        raise ValueError(f"不认识的交易所代码：{ts_code}")
    return prefixes[exchange] + symbol


def local_to_ts(local_code: str) -> str | None:
    prefixes = {"sh": "SH", "sz": "SZ", "bj": "BJ"}
    prefix = local_code[:2].lower()
    if prefix not in prefixes or not local_code[2:].isdigit():
        return None
    return f"{local_code[2:]}.{prefixes[prefix]}"


def read_recent_nonempty_lines(path: Path) -> list[str]:
    with path.open("rb") as handle:
        handle.seek(0, os.SEEK_END)
        size = handle.tell()
        if size == 0:
            return []
        block_size = min(16384, size)
        handle.seek(-block_size, os.SEEK_END)
        block = handle.read()
        while block.count(b"\n") < 2 and block_size < size:
            block_size = min(block_size * 2, size)
            handle.seek(-block_size, os.SEEK_END)
            block = handle.read()
    lines = block.splitlines()
    if block_size < size:
        lines = lines[1:]
    return [line.decode("gbk") for line in lines if line.strip()]


def read_last_nonempty_line(path: Path) -> str:
    lines = read_recent_nonempty_lines(path)
    return lines[-1] if lines else ""


def is_temporary_name(name: str) -> bool:
    return name.startswith(("N", "C", "XD", "XR", "DR"))


def read_existing_files(data_dir: Path) -> tuple[dict[str, Path], dict[str, str], dict[str, str]]:
    files: dict[str, Path] = {}
    last_dates: dict[str, str] = {}
    last_names: dict[str, str] = {}
    for path in data_dir.glob("*.csv"):
        local_code = path.stem.lower()
        ts_code = local_to_ts(local_code)
        if ts_code is None:
            continue
        recent_lines = read_recent_nonempty_lines(path)
        line = recent_lines[-1] if recent_lines else ""
        row = next(csv.reader([line])) if line else []
        if len(row) != 12:
            raise ValueError(f"文件末行不是 12 列：{path}")
        files[ts_code] = path
        if row[0] == "股票代码" and row[2] == "交易日期":
            last_dates[ts_code] = "0000-00-00"
        else:
            last_dates[ts_code] = row[2]
            for recent_line in reversed(recent_lines):
                recent_row = next(csv.reader([recent_line]))
                if (
                    len(recent_row) == 12
                    and recent_row[0] != "股票代码"
                    and not is_temporary_name(recent_row[1])
                ):
                    last_names[ts_code] = recent_row[1]
                    break
    return files, last_dates, last_names


def read_timestamp(data_dir: Path) -> date:
    timestamp_path = data_dir / "timestamp.txt"
    first = timestamp_path.read_text(encoding="utf-8").strip().split(",", 1)[0]
    return date.fromisoformat(first)


def collect_stock_basic(client: TushareClient) -> dict[str, dict[str, object]]:
    result: dict[str, dict[str, object]] = {}
    fields = "ts_code,symbol,name,market,list_date,delist_date,list_status"
    for status in ("D", "P", "L"):
        rows = client.query(
            "stock_basic",
            {"exchange": "", "list_status": status},
            fields,
        )
        for row in rows:
            result[str(row["ts_code"])] = row
    return result


def collect_name_changes(
    client: TushareClient,
    start_date: date,
    end_date: date,
) -> dict[str, list[tuple[str, str, str]]]:
    rows = client.query(
        "namechange",
        {"start_date": api_date(start_date), "end_date": api_date(end_date)},
        "ts_code,name,start_date,end_date,ann_date,change_reason",
    )
    result: dict[str, list[tuple[str, str, str]]] = defaultdict(list)
    for row in rows:
        ts_code = str(row.get("ts_code") or "")
        name = str(row.get("name") or "")
        start = str(row.get("start_date") or "")
        end = str(row.get("end_date") or "")
        if ts_code and name and start:
            result[ts_code].append((start, end, name))
    for values in result.values():
        values.sort(key=lambda item: item[0])
    return result


def name_for_date(
    ts_code: str,
    trade_date: str,
    existing_name: str | None,
    stock_info: dict[str, object] | None,
    changes: list[tuple[str, str, str]],
) -> str:
    name = existing_name or ""
    if not name and stock_info:
        name = str(stock_info.get("name") or "")

    latest_change: tuple[str, str, str] | None = None
    for change in changes:
        if change[0] <= trade_date:
            latest_change = change
        else:
            break
    if latest_change is not None:
        name = latest_change[2]
    if not name:
        raise ValueError(f"找不到 {trade_date} {ts_code} 的股票名称")

    return name


def create_database(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.execute(
        """
        CREATE TABLE daily_rows (
            ts_code TEXT NOT NULL,
            trade_date TEXT NOT NULL,
            open TEXT NOT NULL,
            high TEXT NOT NULL,
            low TEXT NOT NULL,
            close TEXT NOT NULL,
            pre_close TEXT NOT NULL,
            vol TEXT NOT NULL,
            amount TEXT NOT NULL,
            circ_mv TEXT NOT NULL,
            total_mv TEXT NOT NULL,
            PRIMARY KEY (ts_code, trade_date)
        )
        """
    )
    return connection


def download_date(
    client: TushareClient,
    connection: sqlite3.Connection,
    trade_date: str,
) -> int:
    daily = client.query(
        "daily",
        {"trade_date": trade_date},
        "ts_code,trade_date,open,high,low,close,pre_close,vol,amount",
    )
    basic = client.query(
        "daily_basic",
        {"trade_date": trade_date},
        "ts_code,trade_date,close,total_mv,circ_mv",
    )
    if not daily and not basic:
        return 0

    daily_by_code = {str(row["ts_code"]): row for row in daily}
    basic_by_code = {str(row["ts_code"]): row for row in basic}
    if set(daily_by_code) != set(basic_by_code):
        only_daily = sorted(set(daily_by_code) - set(basic_by_code))[:10]
        only_basic = sorted(set(basic_by_code) - set(daily_by_code))[:10]
        raise ValueError(
            f"{trade_date} 两份数据的股票不一致；仅日线：{only_daily}；仅每日指标：{only_basic}"
        )
    inserts: list[tuple[str, ...]] = []
    for ts_code, row in daily_by_code.items():
        basic_row = basic_by_code[ts_code]
        open_price = as_decimal(row.get("open"), "开盘价", ts_code, trade_date)
        high = as_decimal(row.get("high"), "最高价", ts_code, trade_date)
        low = as_decimal(row.get("low"), "最低价", ts_code, trade_date)
        close = as_decimal(row.get("close"), "收盘价", ts_code, trade_date)
        pre_close = as_decimal(row.get("pre_close"), "前收盘价", ts_code, trade_date)
        vol = as_decimal(row.get("vol"), "成交量", ts_code, trade_date)
        amount = as_decimal(row.get("amount"), "成交额", ts_code, trade_date)
        basic_close = as_decimal(basic_row.get("close"), "每日指标收盘价", ts_code, trade_date)
        circ_mv = as_decimal(basic_row.get("circ_mv"), "流通市值", ts_code, trade_date)
        total_mv = as_decimal(basic_row.get("total_mv"), "总市值", ts_code, trade_date)

        if close != basic_close:
            raise ValueError(f"{trade_date} {ts_code} 的两份收盘价不一致")
        if high < max(open_price, low, close) or low > min(open_price, high, close):
            raise ValueError(f"{trade_date} {ts_code} 的开高低收关系不合理")
        if min(open_price, high, low, close, pre_close, circ_mv, total_mv) <= 0:
            raise ValueError(f"{trade_date} {ts_code} 出现非正数价格或市值")
        if vol < 0 or amount < 0:
            raise ValueError(f"{trade_date} {ts_code} 出现负成交量或成交额")

        inserts.append(
            (
                ts_code,
                trade_date,
                decimal_text(open_price),
                decimal_text(high),
                decimal_text(low),
                decimal_text(close),
                decimal_text(pre_close),
                decimal_text(vol * 100),
                decimal_text(amount * 1000),
                decimal_text(circ_mv * 10000),
                decimal_text(total_mv * 10000),
            )
        )
    connection.executemany(
        "INSERT INTO daily_rows VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        inserts,
    )
    connection.commit()
    return len(inserts)


def write_rows(
    connection: sqlite3.Connection,
    data_dir: Path,
    existing_files: dict[str, Path],
    last_dates: dict[str, str],
    last_names: dict[str, str],
    stock_basic: dict[str, dict[str, object]],
    name_changes: dict[str, list[tuple[str, str, str]]],
) -> dict[str, object]:
    inserted = 0
    files_created = 0
    files_updated = 0
    inserted_by_date: dict[str, int] = defaultdict(int)
    expected_last_rows: dict[str, list[str]] = {}

    current_code: str | None = None
    current_is_new = False
    handle = None
    writer = None
    wrote_current = False
    try:
        cursor = connection.execute(
            """
            SELECT ts_code, trade_date, open, high, low, close, pre_close,
                   vol, amount, circ_mv, total_mv
            FROM daily_rows
            ORDER BY ts_code, trade_date
            """
        )
        for values in cursor:
            ts_code = str(values[0])
            trade_date = str(values[1])
            formatted_date = csv_date(trade_date)
            if formatted_date <= last_dates.get(ts_code, "0000-00-00"):
                continue

            name = name_for_date(
                ts_code,
                trade_date,
                last_names.get(ts_code),
                stock_basic.get(ts_code),
                name_changes.get(ts_code, []),
            )
            name.encode("gbk")
            row = [ts_to_local(ts_code), name, formatted_date, *map(str, values[2:11])]
            if len(row) != 12:
                raise AssertionError(f"{trade_date} {ts_code} 写入前不是 12 列")
            if ts_code != current_code:
                if handle is not None:
                    handle.close()
                    if wrote_current and not current_is_new:
                        files_updated += 1
                current_code = ts_code
                wrote_current = False
                path = existing_files.get(ts_code)
                current_is_new = path is None
                if current_is_new:
                    path = data_dir / f"{ts_to_local(ts_code)}.csv"
                    path.parent.mkdir(parents=True, exist_ok=True)
                    handle = path.open("x", encoding="gbk", newline="")
                    writer = csv.writer(handle, lineterminator="\n")
                    handle.write(NOTE_LINE + "\n")
                    writer.writerow(HEADER)
                    existing_files[ts_code] = path
                    last_dates[ts_code] = "0000-00-00"
                    files_created += 1
                else:
                    assert path is not None
                    with path.open("rb") as raw:
                        raw.seek(0, os.SEEK_END)
                        if raw.tell() > 0:
                            raw.seek(-1, os.SEEK_END)
                            ends_with_newline = raw.read(1) == b"\n"
                        else:
                            ends_with_newline = True
                    handle = path.open("a", encoding="gbk", newline="")
                    if not ends_with_newline:
                        handle.write("\n")
                    writer = csv.writer(handle, lineterminator="\n")
            assert writer is not None
            writer.writerow(row)
            inserted += 1
            inserted_by_date[formatted_date] += 1
            expected_last_rows[ts_code] = row
            wrote_current = True
        if handle is not None:
            handle.close()
            handle = None
            if wrote_current and not current_is_new:
                files_updated += 1
    finally:
        if handle is not None:
            handle.close()

    for ts_code, expected in expected_last_rows.items():
        path = existing_files[ts_code]
        actual = next(csv.reader([read_last_nonempty_line(path)]))
        if actual != expected:
            raise ValueError(f"写入后核对失败：{path}")

    return {
        "inserted_rows": inserted,
        "files_created": files_created,
        "files_updated": files_updated,
        "files_touched": files_created + files_updated,
        "inserted_by_date": dict(sorted(inserted_by_date.items())),
        "verified_file_tails": len(expected_last_rows),
    }


def main() -> int:
    args = parse_args()
    data_dir = args.data_dir.resolve()
    report_path = args.report.resolve()
    target_date = date.fromisoformat(args.end_date)
    if not data_dir.is_dir():
        raise FileNotFoundError(f"数据目录不存在：{data_dir}")
    token = os.environ.get("TUSHARE_TOKEN")
    if not token:
        raise RuntimeError("没有找到环境变量 TUSHARE_TOKEN")

    original_end = read_timestamp(data_dir)
    start_date = original_end + timedelta(days=1)
    now = datetime.now().astimezone()
    today = now.date()
    safe_end = target_date
    unavailable_dates: list[str] = []
    if target_date > today:
        safe_end = today - timedelta(days=1)
    elif target_date == today and now.time() < datetime.strptime("17:30", "%H:%M").time():
        safe_end = today - timedelta(days=1)

    report: dict[str, object] = {
        "requested_end": target_date.isoformat(),
        "original_end": original_end.isoformat(),
        "run_time": now.strftime("%Y-%m-%d %H:%M:%S %z"),
        "data_dir": str(data_dir),
        "source": "Tushare daily + daily_basic + stock_basic + namechange",
        "name_rule": "使用正式股票简称；不写 N、C、XD、XR、DR 等临时显示前缀",
    }

    client = TushareClient(token, args.interval)
    calendar_end = max(start_date, target_date)
    calendar_rows = client.query(
        "trade_cal",
        {
            "exchange": "SSE",
            "start_date": api_date(start_date),
            "end_date": api_date(calendar_end),
            "is_open": "1",
        },
        "exchange,cal_date,is_open,pretrade_date",
    )
    all_open_dates = sorted(str(row["cal_date"]) for row in calendar_rows)
    download_dates = [
        value
        for value in all_open_dates
        if start_date <= datetime.strptime(value, "%Y%m%d").date() <= safe_end
    ]
    unavailable_dates.extend(
        csv_date(value)
        for value in all_open_dates
        if safe_end < datetime.strptime(value, "%Y%m%d").date() <= target_date
    )
    print(
        f"现有截止 {original_end.isoformat()}，计划下载 {len(download_dates)} 个完整交易日",
        flush=True,
    )

    existing_files, last_dates, last_names = read_existing_files(data_dir)
    stock_basic = collect_stock_basic(client)
    name_changes = collect_name_changes(client, start_date, target_date)

    downloaded_by_date: dict[str, int] = {}
    with tempfile.TemporaryDirectory(prefix="stock_daily_update_") as temp_dir:
        database_path = Path(temp_dir) / "daily.sqlite3"
        connection = create_database(database_path)
        try:
            for index, trade_date in enumerate(download_dates, start=1):
                count = download_date(client, connection, trade_date)
                if count == 0:
                    raise ValueError(f"完整交易日 {csv_date(trade_date)} 没有取到数据")
                downloaded_by_date[csv_date(trade_date)] = count
                if index == 1 or index % 10 == 0 or index == len(download_dates):
                    print(
                        f"下载进度 {index}/{len(download_dates)}：{csv_date(trade_date)}，{count} 只股票",
                        flush=True,
                    )

            total_downloaded = connection.execute("SELECT COUNT(*) FROM daily_rows").fetchone()[0]
            report["downloaded_rows"] = total_downloaded
            report["downloaded_by_date"] = downloaded_by_date
            report["downloaded_trade_days"] = len(download_dates)

            if download_dates:
                print("下载和数值检查完成，开始写入 CSV", flush=True)
                write_result = write_rows(
                    connection,
                    data_dir,
                    existing_files,
                    last_dates,
                    last_names,
                    stock_basic,
                    name_changes,
                )
            else:
                write_result = {
                    "inserted_rows": 0,
                    "files_created": 0,
                    "files_updated": 0,
                    "files_touched": 0,
                    "inserted_by_date": {},
                    "verified_file_tails": 0,
                }
            report.update(write_result)
        finally:
            connection.close()

    actual_end = csv_date(download_dates[-1]) if download_dates else original_end.isoformat()
    if download_dates:
        (data_dir / "timestamp.txt").write_text(
            f"{actual_end},{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n",
            encoding="utf-8",
            newline="",
        )
    report["actual_end"] = actual_end
    report["unavailable_dates"] = sorted(set(unavailable_dates))
    report["status"] = "completed_to_latest_available"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="",
    )
    print(
        f"完成：写入 {report['inserted_rows']} 行，实际截止 {actual_end}，报告 {report_path}",
        flush=True,
    )
    if unavailable_dates:
        print(f"尚无完整数据的交易日：{', '.join(sorted(set(unavailable_dates)))}", flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"失败：{error}", file=sys.stderr, flush=True)
        raise
