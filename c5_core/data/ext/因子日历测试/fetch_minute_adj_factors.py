#!/usr/bin/env python3
"""下载分钟数据所需的每日复权系数，并压缩成变化点。"""

from __future__ import annotations

import argparse
import json
import os
import time
import urllib.error
import urllib.request
import zipfile
from decimal import Decimal
from pathlib import Path, PurePosixPath

import numpy as np


API_URL = "https://api.tushare.pro"
MAX_ROWS = 6000


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--end-date", default="20260529")
    parser.add_argument("--interval", type=float, default=0.17)
    return parser.parse_args()


class TushareClient:
    def __init__(self, token: str, interval: float) -> None:
        self.token = token
        self.interval = interval
        self.last_call = 0.0

    def query(
        self,
        ts_code: str,
        end_date: str,
        offset: int,
        retries: int = 8,
    ) -> list[dict[str, object]]:
        payload = {
            "api_name": "adj_factor",
            "token": self.token,
            "params": {
                "ts_code": ts_code,
                "start_date": "19900101",
                "end_date": end_date,
                "limit": MAX_ROWS,
                "offset": offset,
            },
            "fields": "ts_code,trade_date,adj_factor",
        }
        request_body = json.dumps(payload).encode("utf-8")
        last_error: Exception | None = None
        for attempt in range(retries):
            wait_for = self.interval - (time.monotonic() - self.last_call)
            if wait_for > 0:
                time.sleep(wait_for)
            request = urllib.request.Request(
                API_URL,
                data=request_body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            try:
                with urllib.request.urlopen(request, timeout=90) as response:
                    raw = response.read().decode("utf-8")
                self.last_call = time.monotonic()
                result = json.loads(raw, parse_float=Decimal)
                if result.get("code") != 0:
                    raise RuntimeError(str(result.get("msg") or "接口返回错误"))
                data = result.get("data") or {}
                fields = data.get("fields") or []
                return [dict(zip(fields, row)) for row in data.get("items") or []]
            except (urllib.error.URLError, TimeoutError, RuntimeError) as exc:
                self.last_call = time.monotonic()
                last_error = exc
                if attempt + 1 < retries:
                    time.sleep(min(5 * (attempt + 1), 30))
        raise RuntimeError(f"{ts_code} 多次请求失败：{last_error}")


def code_from_member(name: str) -> str | None:
    filename = PurePosixPath(name.replace("\\", "/")).name.lower()
    if not filename.endswith(".csv"):
        return None
    stem = filename[:-4]
    if stem.startswith(("sh", "sz", "bj")) and stem[2:].isdigit():
        return stem
    parts = stem.split(".")
    if len(parts) == 2 and parts[0].isdigit() and parts[1] in {"sh", "sz", "bj"}:
        return parts[1] + parts[0]
    return None


def collect_codes(data_dir: Path) -> list[str]:
    archives = [
        data_dir / "1分钟.zip",
        data_dir / "1min.7z",
        data_dir / "沪深2026分钟数据（截止20260529）.zip",
    ]
    codes: set[str] = set()
    for archive_path in archives:
        with zipfile.ZipFile(archive_path) as archive:
            for member in archive.namelist():
                code = code_from_member(member)
                if code:
                    codes.add(code)
    return sorted(codes)


def tushare_code(code: str) -> str:
    suffix = {"sh": "SH", "sz": "SZ", "bj": "BJ"}[code[:2]]
    return f"{code[2:]}.{suffix}"


def fetch_all_rows(client: TushareClient, ts_code: str, end_date: str) -> list[dict[str, object]]:
    rows_by_date: dict[str, dict[str, object]] = {}
    offset = 0
    while True:
        rows = client.query(ts_code, end_date, offset)
        for row in rows:
            trade_date = str(row["trade_date"])
            if trade_date <= end_date:
                rows_by_date[trade_date] = row
        if len(rows) < MAX_ROWS:
            break
        offset += MAX_ROWS
    return [rows_by_date[key] for key in sorted(rows_by_date)]


def reduce_to_changes(rows: list[dict[str, object]]) -> list[list[str]]:
    changes: list[list[str]] = []
    previous: str | None = None
    for row in rows:
        value = format(Decimal(str(row["adj_factor"])), "f")
        if value != previous:
            changes.append([str(row["trade_date"]), value])
            previous = value
    return changes


def load_done(output: Path, factor_dir: Path) -> set[str]:
    done: set[str] = set()
    if not output.exists():
        return done
    with output.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            code = str(item.get("code") or "")
            if item.get("status") == "missing":
                done.add(code)
            elif item.get("status") == "ok" and (factor_dir / f"{code}.npz").exists():
                done.add(code)
    return done


def save_factor_file(path: Path, rows: list[dict[str, object]]) -> None:
    dates = np.asarray([int(str(row["trade_date"])) for row in rows], dtype=np.int32)
    factors = np.asarray([float(Decimal(str(row["adj_factor"]))) for row in rows], dtype=np.float64)
    if len(dates) and (np.any(factors <= 0) or len(np.unique(dates)) != len(dates)):
        raise ValueError("复权系数含非正数或重复日期")
    temporary = path.with_suffix(".tmp.npz")
    np.savez_compressed(temporary, dates=dates, factors=factors)
    os.replace(temporary, path)


def main() -> None:
    args = parse_args()
    token = os.environ.get("TUSHARE_TOKEN")
    if not token:
        raise RuntimeError("没有找到环境变量 TUSHARE_TOKEN")

    data_dir = args.data_dir.resolve()
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    factor_dir = output.parent / "adj_factors"
    factor_dir.mkdir(parents=True, exist_ok=True)
    codes = collect_codes(data_dir)
    done = load_done(output, factor_dir)
    client = TushareClient(token, args.interval)

    print(f"股票总数：{len(codes)}；已经下载：{len(done)}", flush=True)
    with output.open("a", encoding="utf-8", newline="\n") as handle:
        for index, code in enumerate(codes, start=1):
            if code in done:
                continue
            ts_code = tushare_code(code)
            try:
                rows = fetch_all_rows(client, ts_code, args.end_date)
                item = {
                    "code": code,
                    "ts_code": ts_code,
                    "status": "ok" if rows else "missing",
                    "first_date": str(rows[0]["trade_date"]) if rows else None,
                    "last_date": str(rows[-1]["trade_date"]) if rows else None,
                    "daily_rows": len(rows),
                    "changes": reduce_to_changes(rows),
                }
                if rows:
                    save_factor_file(factor_dir / f"{code}.npz", rows)
            except Exception as exc:
                print(f"[{index}/{len(codes)}] {code} 失败：{exc}", flush=True)
                time.sleep(5)
                continue
            handle.write(json.dumps(item, ensure_ascii=False, separators=(",", ":")) + "\n")
            handle.flush()
            if index % 25 == 0 or item["status"] != "ok":
                print(
                    f"[{index}/{len(codes)}] {code}：{item['status']}，"
                    f"{item['daily_rows']} 日，{len(item['changes'])} 个变化点",
                    flush=True,
                )


if __name__ == "__main__":
    main()
