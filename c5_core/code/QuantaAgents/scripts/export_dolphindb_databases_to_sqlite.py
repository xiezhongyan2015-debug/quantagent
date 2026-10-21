from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import date
import os
from pathlib import Path
import sqlite3
from typing import Any

from dotenv import load_dotenv


@dataclass(frozen=True)
class ExportSummary:
    group: str
    source_db: str
    source_table: str
    target_db: Path
    target_table: str
    rows: int


def _quote(value: str) -> str:
    escaped = value.replace('\\', '\\\\').replace('"', '\\"')
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


def _date_filters(column_name: str, start_date: date | None, end_date: date | None, *, project_to_date: bool = False) -> str:
    filters: list[str] = []
    lhs = f"date({column_name})" if project_to_date else column_name
    if start_date is not None:
        filters.append(f"{lhs} >= date({_quote(start_date.isoformat())})")
    if end_date is not None:
        filters.append(f"{lhs} <= date({_quote(end_date.isoformat())})")
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
    text = text.replace("T", " ")
    if "." in text:
        text = text.split(".", maxsplit=1)[0]
    return text[:19]


def _fetch_market_rows(
    session: Any,
    *,
    src_db: str,
    src_table: str,
    include_open_interest: bool,
    start_date: date | None,
    end_date: date | None,
) -> list[tuple[Any, ...]]:
    select_fields = [
        "code",
        "trade_date",
        "double(open) as open",
        "double(high) as high",
        "double(low) as low",
        "double(close) as close",
        "double(volume) as volume",
    ]
    if include_open_interest:
        select_fields.append("double(open_interest) as open_interest")

    script = (
        "src = loadTable(" + _quote(src_db) + ", " + _quote(src_table) + "); "
        "select "
        + ", ".join(select_fields)
        + " from src where isValid(code) and isValid(trade_date) and isValid(open) and isValid(high) and isValid(low) and isValid(close) and isValid(volume)"
        + (" and isValid(open_interest)" if include_open_interest else "")
        + _date_filters("trade_date", start_date, end_date)
        + " order by code, trade_date"
    )
    df = session.run(script)
    if df is None or len(df) == 0:
        return []

    rows: list[tuple[Any, ...]] = []
    for row in df.itertuples(index=False):
        base_row = (
            str(getattr(row, "code")).strip(),
            _to_python_date(getattr(row, "trade_date")),
            float(getattr(row, "open")),
            float(getattr(row, "high")),
            float(getattr(row, "low")),
            float(getattr(row, "close")),
            float(getattr(row, "volume")),
        )
        if include_open_interest:
            rows.append((*base_row, float(getattr(row, "open_interest"))))
        else:
            rows.append(base_row)
    return rows


def _fetch_vnpy_rows(
    session: Any,
    *,
    src_db: str,
    src_table: str,
    start_date: date | None,
    end_date: date | None,
) -> list[tuple[Any, ...]]:
    script = (
        "src = loadTable(" + _quote(src_db) + ", " + _quote(src_table) + "); "
        "select symbol, exchange, datetime, interval, volume, turnover, open_interest, open_price, high_price, low_price, close_price "
        "from src where isValid(symbol) and isValid(exchange) and isValid(datetime) and isValid(interval) and isValid(volume) and isValid(turnover) and isValid(open_interest) and isValid(open_price) and isValid(high_price) and isValid(low_price) and isValid(close_price)"
        + _date_filters("datetime", start_date, end_date, project_to_date=True)
        + " order by symbol, exchange, datetime"
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


def _ensure_market_table(conn: sqlite3.Connection, table_name: str, *, include_open_interest: bool) -> None:
    if include_open_interest:
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
                open_interest REAL,
                PRIMARY KEY (code, trade_date)
            )
            """
        )
        return

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


def _ensure_vnpy_table(conn: sqlite3.Connection, table_name: str) -> None:
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


def _write_market_rows(
    conn: sqlite3.Connection,
    *,
    table_name: str,
    rows: list[tuple[Any, ...]],
    include_open_interest: bool,
    replace_existing: bool,
) -> int:
    _ensure_market_table(conn, table_name, include_open_interest=include_open_interest)
    if replace_existing:
        conn.execute(f"DELETE FROM {table_name}")
    if rows:
        if include_open_interest:
            conn.executemany(
                f"""
                INSERT OR REPLACE INTO {table_name}
                (code, trade_date, open, high, low, close, volume, open_interest)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
        else:
            conn.executemany(
                f"""
                INSERT OR REPLACE INTO {table_name}
                (code, trade_date, open, high, low, close, volume)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
    return len(rows)


def _write_vnpy_rows(
    conn: sqlite3.Connection,
    *,
    table_name: str,
    rows: list[tuple[Any, ...]],
    replace_existing: bool,
) -> int:
    _ensure_vnpy_table(conn, table_name)
    if replace_existing:
        conn.execute(f"DELETE FROM {table_name}")
    if rows:
        conn.executemany(
            f"""
            INSERT OR REPLACE INTO {table_name}
            (symbol, exchange, datetime, interval, volume, turnover, open_interest,
             open_price, high_price, low_price, close_price, gateway_name)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
    return len(rows)


def _export_group(
    session: Any,
    *,
    group: str,
    source_db: str,
    target_sqlite: Path,
    replace_existing: bool,
    start_date: date | None,
    end_date: date | None,
) -> list[ExportSummary]:
    if replace_existing and target_sqlite.exists():
        target_sqlite.unlink()
    target_sqlite.parent.mkdir(parents=True, exist_ok=True)

    summaries: list[ExportSummary] = []
    with sqlite3.connect(target_sqlite) as conn:
        if group == "market":
            table_specs = [
                ("stock_kline_daily_qfq", False),
                ("futures_kline_daily_hfq", True),
            ]
            for source_table, include_open_interest in table_specs:
                rows = _fetch_market_rows(
                    session,
                    src_db=source_db,
                    src_table=source_table,
                    include_open_interest=include_open_interest,
                    start_date=start_date,
                    end_date=end_date,
                )
                inserted = _write_market_rows(
                    conn,
                    table_name=source_table,
                    rows=rows,
                    include_open_interest=include_open_interest,
                    replace_existing=replace_existing,
                )
                summaries.append(
                    ExportSummary(
                        group=group,
                        source_db=source_db,
                        source_table=source_table,
                        target_db=target_sqlite,
                        target_table=source_table,
                        rows=inserted,
                    )
                )
        elif group == "vnpy":
            table_specs = [
                "vnpy_stock_daily_qfq",
                "vnpy_futures_daily_hfq",
            ]
            for source_table in table_specs:
                rows = _fetch_vnpy_rows(
                    session,
                    src_db=source_db,
                    src_table=source_table,
                    start_date=start_date,
                    end_date=end_date,
                )
                inserted = _write_vnpy_rows(
                    conn,
                    table_name=source_table,
                    rows=rows,
                    replace_existing=replace_existing,
                )
                summaries.append(
                    ExportSummary(
                        group=group,
                        source_db=source_db,
                        source_table=source_table,
                        target_db=target_sqlite,
                        target_table=source_table,
                        rows=inserted,
                    )
                )
        else:
            raise ValueError(f"unknown export group: {group}")

        conn.commit()
    return summaries


def export_all(
    session: Any,
    *,
    market_src_db: str,
    vnpy_src_db: str,
    market_sqlite: str | Path,
    vnpy_sqlite: str | Path,
    replace_existing: bool,
    start_date: date | None,
    end_date: date | None,
    skip_market: bool = False,
    skip_vnpy: bool = False,
) -> list[ExportSummary]:
    if skip_market and skip_vnpy:
        raise RuntimeError("Both targets are skipped. Use at least one of market/vnpy export.")

    summaries: list[ExportSummary] = []
    if not skip_market:
        summaries.extend(
            _export_group(
                session,
                group="market",
                source_db=market_src_db,
                target_sqlite=_resolve_path(str(market_sqlite)),
                replace_existing=replace_existing,
                start_date=start_date,
                end_date=end_date,
            )
        )
    if not skip_vnpy:
        summaries.extend(
            _export_group(
                session,
                group="vnpy",
                source_db=vnpy_src_db,
                target_sqlite=_resolve_path(str(vnpy_sqlite)),
                replace_existing=replace_existing,
                start_date=start_date,
                end_date=end_date,
            )
        )
    return summaries


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export DolphinDB market_data and vnpy_bar_db tables into SQLite files",
    )
    parser.add_argument(
        "--market-src-db",
        default=os.getenv("DOLPHINDB_MARKET_DATA_DB", "dfs://market_data"),
        help="Source DolphinDB database path for market_data",
    )
    parser.add_argument(
        "--vnpy-src-db",
        default=os.getenv("DOLPHINDB_BACKTEST_DB", "dfs://vnpy_bar_db"),
        help="Source DolphinDB database path for vnpy_bar_db",
    )
    parser.add_argument(
        "--market-sqlite",
        default=os.getenv("SQLITE_MARKET_DATA_DB_PATH", "./market_data.db"),
        help="SQLite file for market_data",
    )
    parser.add_argument(
        "--vnpy-sqlite",
        default=os.getenv("SQLITE_BACKTEST_DB_PATH", "./vnpy_bar.sqlite3"),
        help="SQLite file for vnpy bar data",
    )
    parser.add_argument("--start-date", default=None, help="Inclusive start date, e.g. 2020-01-01")
    parser.add_argument("--end-date", default=None, help="Inclusive end date, e.g. 2025-12-31")
    parser.add_argument(
        "--replace-existing",
        action="store_true",
        help="Delete the target SQLite files before writing",
    )
    parser.add_argument(
        "--skip-market",
        action="store_true",
        help="Skip exporting market_data.db",
    )
    parser.add_argument(
        "--skip-vnpy",
        action="store_true",
        help="Skip exporting vnpy_bar.sqlite3",
    )
    return parser.parse_args()


def main() -> None:
    load_dotenv()
    args = parse_args()

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
        summaries = export_all(
            session,
            market_src_db=args.market_src_db,
            vnpy_src_db=args.vnpy_src_db,
            market_sqlite=args.market_sqlite,
            vnpy_sqlite=args.vnpy_sqlite,
            replace_existing=args.replace_existing,
            start_date=start_date,
            end_date=end_date,
            skip_market=args.skip_market,
            skip_vnpy=args.skip_vnpy,
        )

        for summary in summaries:
            print(
                f"[{summary.group}] {summary.source_db}/{summary.source_table} -> "
                f"{summary.target_db}::{summary.target_table} rows={summary.rows}"
            )

        print("Export completed")
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
