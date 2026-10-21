from __future__ import annotations

import argparse
from datetime import date
import os
from pathlib import Path
import sqlite3
from typing import Any

from dotenv import load_dotenv


def _quote(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _resolve_path(raw_path: str) -> Path:
    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        path = _project_root() / path
    return path


def _parse_iso_date(value: str | None) -> date | None:
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    return date.fromisoformat(text)


def _date_filters(start_date: date | None, end_date: date | None) -> str:
    filters: list[str] = []
    if start_date is not None:
        filters.append(f"trade_date >= date({_quote(start_date.isoformat())})")
    if end_date is not None:
        filters.append(f"trade_date <= date({_quote(end_date.isoformat())})")
    if not filters:
        return ""
    return " and " + " and ".join(filters)


def _to_python_date(value: Any) -> str:
    text = str(value)
    return text[:10]


def _to_python_datetime(value: Any) -> str:
    text = str(value).strip()
    if not text:
        return text
    # Normalize to `YYYY-MM-DD HH:MM:SS` for vnpy sqlite backend compatibility.
    text = text.replace("T", " ")
    if "." in text:
        text = text.split(".", maxsplit=1)[0]
    return text[:19]


def _fetch_semantic_rows(
    session: Any,
    src_db: str,
    src_table: str,
    start_date: date | None,
    end_date: date | None,
) -> list[tuple[Any, ...]]:
    script = (
        "src = loadTable("
        + _quote(src_db)
        + ", "
        + _quote(src_table)
        + "); "
        "select code, trade_date, double(open) as open, double(high) as high, "
        "double(low) as low, double(close) as close, double(volume) as volume "
        "from src where isValid(code) and isValid(trade_date)"
        + _date_filters(start_date, end_date)
        + " order by code, trade_date"
    )
    df = session.run(script)
    if df is None or len(df) == 0:
        return []

    rows: list[tuple[Any, ...]] = []
    for row in df.itertuples(index=False):
        rows.append(
            (
                str(getattr(row, "code")).strip(),
                _to_python_date(getattr(row, "trade_date")),
                float(getattr(row, "open")),
                float(getattr(row, "high")),
                float(getattr(row, "low")),
                float(getattr(row, "close")),
                float(getattr(row, "volume")),
            )
        )
    return rows


def _fetch_vnpy_rows(
    session: Any,
    src_db: str,
    src_table: str,
    interval: str,
    start_date: date | None,
    end_date: date | None,
) -> list[tuple[Any, ...]]:
    interval_q = _quote(interval)
    script = (
        "src = loadTable("
        + _quote(src_db)
        + ", "
        + _quote(src_table)
        + "); "
        "select left(string(code), 6) as symbol, "
        "iif(right(string(code), 2) == \"SH\", \"SSE\", \"SZSE\") as exchange, "
        "nanotimestamp(trade_date) as datetime, "
        + interval_q
        + " as interval, "
        "double(volume) as volume, "
        "double(close) * double(volume) as turnover, "
        "0.0 as open_interest, "
        "double(open) as open_price, "
        "double(high) as high_price, "
        "double(low) as low_price, "
        "double(close) as close_price "
        "from src where isValid(code) and isValid(trade_date)"
        + _date_filters(start_date, end_date)
        + " order by code, trade_date"
    )
    df = session.run(script)
    if df is None or len(df) == 0:
        return []

    rows: list[tuple[Any, ...]] = []
    for row in df.itertuples(index=False):
        rows.append(
            (
                str(getattr(row, "symbol")).strip(),
                str(getattr(row, "exchange")).strip(),
                _to_python_datetime(getattr(row, "datetime")),
                str(getattr(row, "interval")).strip(),
                float(getattr(row, "volume")),
                float(getattr(row, "turnover")),
                float(getattr(row, "open_interest")),
                float(getattr(row, "open_price")),
                float(getattr(row, "high_price")),
                float(getattr(row, "low_price")),
                float(getattr(row, "close_price")),
                "DB",
            )
        )
    return rows


def _ensure_semantic_table(conn: sqlite3.Connection, table_name: str) -> None:
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {table_name} (
            code TEXT NOT NULL,
            trade_date TEXT NOT NULL,
            open REAL,
            high REAL,
            low REAL,
            close REAL,
            volume REAL,
            PRIMARY KEY (code, trade_date)
        )
        """
    )


def _ensure_vnpy_bar_table(conn: sqlite3.Connection, table_name: str) -> None:
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {table_name} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            exchange TEXT NOT NULL,
            datetime TEXT NOT NULL,
            interval TEXT NOT NULL,
            volume REAL,
            turnover REAL,
            open_interest REAL,
            open_price REAL,
            high_price REAL,
            low_price REAL,
            close_price REAL,
            gateway_name TEXT,
            UNIQUE(symbol, exchange, interval, datetime)
        )
        """
    )


def _write_semantic_rows(
    db_path: Path,
    table_name: str,
    rows: list[tuple[Any, ...]],
    replace_existing: bool,
) -> int:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        _ensure_semantic_table(conn, table_name)
        if replace_existing:
            conn.execute(f"DELETE FROM {table_name}")
        conn.executemany(
            f"""
            INSERT OR REPLACE INTO {table_name}
            (code, trade_date, open, high, low, close, volume)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        conn.commit()
    return len(rows)


def _write_vnpy_rows(
    db_path: Path,
    table_name: str,
    rows: list[tuple[Any, ...]],
    replace_existing: bool,
) -> int:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        _ensure_vnpy_bar_table(conn, table_name)
        if replace_existing:
            conn.execute(f"DELETE FROM {table_name}")
        conn.executemany(
            f"""
            INSERT OR REPLACE INTO {table_name}
            (symbol, exchange, datetime, interval, volume, turnover, open_interest,
             open_price, high_price, low_price, close_price, gateway_name)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        conn.commit()
    return len(rows)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export DolphinDB daily kline data to semantic/vnpy SQLite databases",
    )
    parser.add_argument("--src-db", default="dfs://ohlcv_daily", help="Source DolphinDB database path")
    parser.add_argument("--src-table", default="stock_kline_daily_qfq", help="Source DolphinDB table")

    parser.add_argument(
        "--semantic-sqlite",
        default=os.getenv("SQLITE_MARKET_DATA_DB_PATH", "./market_data.db"),
        help="SQLite file for semantic layer data",
    )
    parser.add_argument(
        "--semantic-table",
        default="stock_kline_daily_qfq",
        help="Target table name in semantic sqlite DB",
    )

    parser.add_argument(
        "--vnpy-sqlite",
        default=os.getenv("SQLITE_BACKTEST_DB_PATH", "./vnpy_bar.sqlite3"),
        help="SQLite file for vnpy backtest data",
    )
    parser.add_argument(
        "--vnpy-table",
        default=os.getenv("QUANTA_VNPY_SQLITE_BAR_TABLE", "dbbardata"),
        help="Target table name in vnpy sqlite DB",
    )
    parser.add_argument("--interval", default="d", help="Bar interval for vnpy table, e.g. d/1m")

    parser.add_argument("--start-date", default=None, help="Inclusive start date, e.g. 2020-01-01")
    parser.add_argument("--end-date", default=None, help="Inclusive end date, e.g. 2025-12-31")

    parser.add_argument(
        "--replace-existing",
        action="store_true",
        help="Delete existing target table rows before insert",
    )
    parser.add_argument(
        "--skip-semantic",
        action="store_true",
        help="Skip exporting semantic sqlite dataset",
    )
    parser.add_argument(
        "--skip-vnpy",
        action="store_true",
        help="Skip exporting vnpy sqlite bar dataset",
    )
    return parser.parse_args()


def main() -> None:
    load_dotenv()
    args = parse_args()

    if args.skip_semantic and args.skip_vnpy:
        raise RuntimeError("Both targets are skipped. Use at least one of semantic/vnpy export.")

    start_date = _parse_iso_date(args.start_date)
    end_date = _parse_iso_date(args.end_date)
    if start_date and end_date and end_date < start_date:
        raise ValueError("--end-date must be >= --start-date")

    host = os.getenv("DOLPHINDB_HOST", "localhost").strip()
    port = int(os.getenv("DOLPHINDB_PORT", "8848"))
    user = os.getenv("DOLPHINDB_USER", "admin").strip()
    password = os.getenv("DOLPHINDB_PASSWORD", "123456").strip()

    try:
        import dolphindb as ddb
    except Exception as exc:  # pragma: no cover
        raise RuntimeError("dolphindb package is required") from exc

    session = ddb.session()
    session.connect(host, port, user, password)

    try:
        if not args.skip_semantic:
            semantic_rows = _fetch_semantic_rows(
                session,
                src_db=args.src_db,
                src_table=args.src_table,
                start_date=start_date,
                end_date=end_date,
            )
            semantic_path = _resolve_path(args.semantic_sqlite)
            inserted = _write_semantic_rows(
                semantic_path,
                table_name=args.semantic_table,
                rows=semantic_rows,
                replace_existing=args.replace_existing,
            )
            print(f"[semantic] inserted_rows={inserted}, sqlite={semantic_path}, table={args.semantic_table}")

        if not args.skip_vnpy:
            vnpy_rows = _fetch_vnpy_rows(
                session,
                src_db=args.src_db,
                src_table=args.src_table,
                interval=args.interval,
                start_date=start_date,
                end_date=end_date,
            )
            vnpy_path = _resolve_path(args.vnpy_sqlite)
            inserted = _write_vnpy_rows(
                vnpy_path,
                table_name=args.vnpy_table,
                rows=vnpy_rows,
                replace_existing=args.replace_existing,
            )
            print(f"[vnpy] inserted_rows={inserted}, sqlite={vnpy_path}, table={args.vnpy_table}")

        print("Export completed")
        print(f"source={args.src_db}/{args.src_table}")
        if start_date:
            print(f"start_date={start_date.isoformat()}")
        if end_date:
            print(f"end_date={end_date.isoformat()}")
    finally:
        close = getattr(session, "close", None)
        if callable(close):
            close()


if __name__ == "__main__":
    main()
