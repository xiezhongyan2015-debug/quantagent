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
class TableCheckResult:
    table: str
    status: str
    ddb_rows: int
    sqlite_rows: int
    missing_rows: int | None
    key_columns: list[str]
    note: str


def _project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _resolve_path(raw_path: str) -> Path:
    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        path = _project_root() / path
    return path


def _quote(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _parse_iso_date(text: str | None) -> date | None:
    if text is None:
        return None
    value = text.strip()
    if not value:
        return None
    return date.fromisoformat(value)


def _sqlite_tables(conn: sqlite3.Connection) -> list[str]:
    cur = conn.execute(
        """
        SELECT name
        FROM sqlite_master
        WHERE type='table' AND name NOT LIKE 'sqlite_%'
        ORDER BY name
        """
    )
    return [str(row[0]) for row in cur.fetchall()]


def _sqlite_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    cur = conn.execute(f"PRAGMA table_info({table})")
    return [str(row[1]) for row in cur.fetchall()]


def _sqlite_primary_key_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    cur = conn.execute(f"PRAGMA table_info({table})")
    rows = cur.fetchall()
    # PRAGMA table_info columns: cid, name, type, notnull, dflt_value, pk
    pk_cols = [(int(row[5]), str(row[1])) for row in rows if int(row[5]) > 0]
    pk_cols.sort(key=lambda x: x[0])
    return [name for _, name in pk_cols]


def _determine_key_columns(sqlite_cols: list[str], sqlite_pk: list[str]) -> list[str]:
    if sqlite_pk:
        return sqlite_pk

    cols = set(sqlite_cols)
    if {"code", "trade_date"}.issubset(cols):
        return ["code", "trade_date"]
    if {"symbol", "exchange", "datetime", "interval"}.issubset(cols):
        return ["symbol", "exchange", "datetime", "interval"]
    if "trade_date" in cols:
        return ["trade_date"]
    if "datetime" in cols:
        return ["datetime"]

    # Fallback: use all columns when no stable key is obvious.
    return sqlite_cols


def _date_filter_for_column(column_name: str, start_date: date | None, end_date: date | None, *, sqlite_mode: bool) -> str:
    if not column_name:
        return ""

    if sqlite_mode:
        lhs = f"date({column_name})"
        q = lambda d: f"date('{d.isoformat()}')"
    else:
        lhs = f"date({column_name})"
        q = lambda d: f"date({_quote(d.isoformat())})"

    filters: list[str] = []
    if start_date is not None:
        filters.append(f"{lhs} >= {q(start_date)}")
    if end_date is not None:
        filters.append(f"{lhs} <= {q(end_date)}")

    if not filters:
        return ""
    return " WHERE " + " AND ".join(filters)


def _count_sqlite_rows(conn: sqlite3.Connection, table: str, date_col: str, start_date: date | None, end_date: date | None) -> int:
    where_clause = _date_filter_for_column(date_col, start_date, end_date, sqlite_mode=True) if date_col else ""
    cur = conn.execute(f"SELECT COUNT(*) FROM {table}{where_clause}")
    return int(cur.fetchone()[0])


def _count_ddb_rows(session: Any, db_path: str, table: str, date_col: str, start_date: date | None, end_date: date | None) -> int:
    where_clause = _date_filter_for_column(date_col, start_date, end_date, sqlite_mode=False) if date_col else ""
    script = (
        f"tbl = loadTable({_quote(db_path)}, {_quote(table)}); "
        f"select count(*) as c from tbl{where_clause}"
    )
    result = session.run(script)
    return int(result.iloc[0, 0]) if len(result) > 0 else 0


def _fetch_sqlite_key_set(
    conn: sqlite3.Connection,
    table: str,
    key_cols: list[str],
    date_col: str,
    start_date: date | None,
    end_date: date | None,
) -> set[tuple[str, ...]]:
    where_clause = _date_filter_for_column(date_col, start_date, end_date, sqlite_mode=True) if date_col else ""
    select_cols = ", ".join(key_cols)
    cur = conn.execute(f"SELECT {select_cols} FROM {table}{where_clause}")
    rows = cur.fetchall()
    return {
        tuple(_normalize_key_value(value, key_cols[idx]) for idx, value in enumerate(row))
        for row in rows
    }


def _normalize_key_value(value: Any, column_name: str) -> str:
    if value is None:
        return ""

    text = str(value).strip()
    if not text:
        return ""

    if column_name == "trade_date":
        return text[:10]

    if column_name == "datetime":
        normalized = text.replace("T", " ")
        if "." in normalized:
            normalized = normalized.split(".", maxsplit=1)[0]
        return normalized[:19]

    return text


def _fetch_ddb_key_set(
    session: Any,
    db_path: str,
    table: str,
    key_cols: list[str],
    date_col: str,
    start_date: date | None,
    end_date: date | None,
) -> set[tuple[str, ...]]:
    select_cols = ", ".join(key_cols)
    where_clause = _date_filter_for_column(date_col, start_date, end_date, sqlite_mode=False) if date_col else ""
    script = (
        f"tbl = loadTable({_quote(db_path)}, {_quote(table)}); "
        f"select {select_cols} from tbl{where_clause}"
    )
    df = session.run(script)
    if df is None or len(df) == 0:
        return set()

    keys: set[tuple[str, ...]] = set()
    for row in df.itertuples(index=False):
        keys.add(
            tuple(
                _normalize_key_value(value, key_cols[idx])
                for idx, value in enumerate(row)
            )
        )
    return keys


def _fetch_date_counts_sqlite(
    conn: sqlite3.Connection,
    table: str,
    date_col: str,
    start_date: date | None,
    end_date: date | None,
) -> dict[str, int]:
    where_clause = _date_filter_for_column(date_col, start_date, end_date, sqlite_mode=True)
    cur = conn.execute(
        f"SELECT date({date_col}) as d, COUNT(*) as c FROM {table}{where_clause} GROUP BY date({date_col})"
    )
    return {str(d): int(c) for d, c in cur.fetchall() if d is not None}


def _fetch_date_counts_ddb(
    session: Any,
    db_path: str,
    table: str,
    date_col: str,
    start_date: date | None,
    end_date: date | None,
) -> dict[str, int]:
    where_clause = _date_filter_for_column(date_col, start_date, end_date, sqlite_mode=False)
    script = (
        f"tbl = loadTable({_quote(db_path)}, {_quote(table)}); "
        f"select string(date({date_col})) as d, count(*) as c from tbl{where_clause} group by date({date_col})"
    )
    df = session.run(script)
    if df is None or len(df) == 0:
        return {}
    result: dict[str, int] = {}
    for row in df.itertuples(index=False):
        result[str(getattr(row, "d"))] = int(getattr(row, "c"))
    return result


def _choose_date_column(columns: list[str]) -> str:
    if "trade_date" in columns:
        return "trade_date"
    if "datetime" in columns:
        return "datetime"
    return ""


def check_table(
    conn: sqlite3.Connection,
    session: Any,
    *,
    db_path: str,
    table: str,
    start_date: date | None,
    end_date: date | None,
    max_exact_rows: int,
) -> TableCheckResult:
    sqlite_cols = _sqlite_columns(conn, table)
    if not sqlite_cols:
        return TableCheckResult(
            table=table,
            status="skipped",
            ddb_rows=0,
            sqlite_rows=0,
            missing_rows=None,
            key_columns=[],
            note="table has no columns in sqlite",
        )

    date_col = _choose_date_column(sqlite_cols)
    key_cols = _determine_key_columns(sqlite_cols, _sqlite_primary_key_columns(conn, table))

    try:
        ddb_rows = _count_ddb_rows(session, db_path, table, date_col, start_date, end_date)
    except Exception as exc:
        return TableCheckResult(
            table=table,
            status="skipped",
            ddb_rows=0,
            sqlite_rows=0,
            missing_rows=None,
            key_columns=key_cols,
            note=f"table not found or unreadable in DolphinDB: {exc}",
        )

    sqlite_rows = _count_sqlite_rows(conn, table, date_col, start_date, end_date)

    # Fast path
    if ddb_rows == 0:
        return TableCheckResult(
            table=table,
            status="ok",
            ddb_rows=0,
            sqlite_rows=sqlite_rows,
            missing_rows=0,
            key_columns=key_cols,
            note="no source rows in DolphinDB",
        )

    # For very large tables, avoid full key-set materialization.
    if max(ddb_rows, sqlite_rows) > max_exact_rows:
        if date_col:
            ddb_counts = _fetch_date_counts_ddb(session, db_path, table, date_col, start_date, end_date)
            sqlite_counts = _fetch_date_counts_sqlite(conn, table, date_col, start_date, end_date)
            missing_by_date = 0
            for d, src_count in ddb_counts.items():
                tgt_count = sqlite_counts.get(d, 0)
                if src_count > tgt_count:
                    missing_by_date += src_count - tgt_count
            status = "missing" if missing_by_date > 0 else "ok"
            return TableCheckResult(
                table=table,
                status=status,
                ddb_rows=ddb_rows,
                sqlite_rows=sqlite_rows,
                missing_rows=missing_by_date,
                key_columns=key_cols,
                note=f"large table; compared by date-level counts using {date_col}",
            )

        return TableCheckResult(
            table=table,
            status="skipped",
            ddb_rows=ddb_rows,
            sqlite_rows=sqlite_rows,
            missing_rows=None,
            key_columns=key_cols,
            note="large table without date column; exact compare skipped",
        )

    sqlite_keys = _fetch_sqlite_key_set(conn, table, key_cols, date_col, start_date, end_date)
    ddb_keys = _fetch_ddb_key_set(session, db_path, table, key_cols, date_col, start_date, end_date)

    missing = ddb_keys - sqlite_keys
    missing_count = len(missing)
    status = "missing" if missing_count > 0 else "ok"

    return TableCheckResult(
        table=table,
        status=status,
        ddb_rows=ddb_rows,
        sqlite_rows=sqlite_rows,
        missing_rows=missing_count,
        key_columns=key_cols,
        note="exact key-set compare",
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check missing rows in market_data.db compared with DolphinDB")
    parser.add_argument(
        "--sqlite",
        default=os.getenv("SQLITE_MARKET_DATA_DB_PATH", "./market_data.db"),
        help="Path to SQLite market data DB",
    )
    parser.add_argument(
        "--ddb-db",
        default=os.getenv("DOLPHINDB_MARKET_DATA_DB", "dfs://market_data"),
        help="DolphinDB database path",
    )
    parser.add_argument(
        "--tables",
        nargs="*",
        default=None,
        help="Optional table whitelist. Default: all sqlite tables",
    )
    parser.add_argument("--start-date", default=None, help="Inclusive start date (YYYY-MM-DD)")
    parser.add_argument("--end-date", default=None, help="Inclusive end date (YYYY-MM-DD)")
    parser.add_argument(
        "--max-exact-rows",
        type=int,
        default=2_000_000,
        help="When either side exceeds this threshold, degrade to date-level compare",
    )
    return parser.parse_args()


def main() -> None:
    load_dotenv()
    args = parse_args()

    sqlite_path = _resolve_path(args.sqlite)
    if not sqlite_path.exists():
        raise FileNotFoundError(f"SQLite DB not found: {sqlite_path}")

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
        with sqlite3.connect(sqlite_path) as conn:
            all_tables = _sqlite_tables(conn)
            table_filter = set(args.tables) if args.tables else None
            target_tables = [t for t in all_tables if table_filter is None or t in table_filter]

            if not target_tables:
                print("No tables selected. Nothing to check.")
                return

            print(f"Checking {len(target_tables)} tables in {sqlite_path} against {args.ddb_db} ...")

            results: list[TableCheckResult] = []
            for table in target_tables:
                result = check_table(
                    conn,
                    session,
                    db_path=args.ddb_db,
                    table=table,
                    start_date=start_date,
                    end_date=end_date,
                    max_exact_rows=max(args.max_exact_rows, 1),
                )
                results.append(result)
                print(
                    f"[{result.status.upper()}] {result.table} | ddb={result.ddb_rows} sqlite={result.sqlite_rows} "
                    f"missing={result.missing_rows if result.missing_rows is not None else 'N/A'} "
                    f"keys={result.key_columns} | {result.note}"
                )

            missing_tables = [r for r in results if r.status == "missing"]
            skipped_tables = [r for r in results if r.status == "skipped"]

            print("\nSummary")
            print(f"- tables_checked: {len(results)}")
            print(f"- tables_with_missing: {len(missing_tables)}")
            print(f"- tables_skipped: {len(skipped_tables)}")

            total_missing = sum(r.missing_rows or 0 for r in missing_tables)
            print(f"- total_missing_rows_estimated: {total_missing}")

            if missing_tables:
                print("\nTables with missing rows:")
                for r in missing_tables:
                    print(f"- {r.table}: missing={r.missing_rows} ({r.note})")
    finally:
        close = getattr(session, "close", None)
        if callable(close):
            close()


if __name__ == "__main__":
    main()
