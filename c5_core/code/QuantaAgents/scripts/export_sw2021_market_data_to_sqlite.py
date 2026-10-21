from __future__ import annotations

import argparse
from dataclasses import dataclass
import os
from pathlib import Path
import sqlite3
from typing import Any, cast

from dotenv import load_dotenv


@dataclass(frozen=True)
class ExportSummary:
    source_db: str
    source_table: str
    target_db: Path
    target_table: str
    rows: int


TABLE_SPECS: list[dict[str, object]] = [
    {
        "table_name": "sw2021_index_classify_l1",
        "columns": ["index_code", "industry_name", "level", "industry_code", "is_pub", "parent_code", "src"],
        "create_sql": """
            CREATE TABLE IF NOT EXISTS sw2021_index_classify_l1 (
                index_code TEXT,
                industry_name TEXT,
                level TEXT,
                industry_code TEXT,
                is_pub TEXT,
                parent_code TEXT,
                src TEXT
            )
        """,
    },
    {
        "table_name": "sw2021_index_classify_l2",
        "columns": ["index_code", "industry_name", "level", "industry_code", "is_pub", "parent_code", "src"],
        "create_sql": """
            CREATE TABLE IF NOT EXISTS sw2021_index_classify_l2 (
                index_code TEXT,
                industry_name TEXT,
                level TEXT,
                industry_code TEXT,
                is_pub TEXT,
                parent_code TEXT,
                src TEXT
            )
        """,
    },
    {
        "table_name": "sw2021_index_classify_l3",
        "columns": ["index_code", "industry_name", "level", "industry_code", "is_pub", "parent_code", "src"],
        "create_sql": """
            CREATE TABLE IF NOT EXISTS sw2021_index_classify_l3 (
                index_code TEXT,
                industry_name TEXT,
                level TEXT,
                industry_code TEXT,
                is_pub TEXT,
                parent_code TEXT,
                src TEXT
            )
        """,
    },
    {
        "table_name": "sw2021_l1_members",
        "columns": [
            "l1_code",
            "l1_name",
            "l2_code",
            "l2_name",
            "l3_code",
            "l3_name",
            "stock_code",
            "ts_code",
            "name",
            "in_date",
            "out_date",
            "is_new",
        ],
        "create_sql": """
            CREATE TABLE IF NOT EXISTS sw2021_l1_members (
                l1_code TEXT,
                l1_name TEXT,
                l2_code TEXT,
                l2_name TEXT,
                l3_code TEXT,
                l3_name TEXT,
                stock_code TEXT,
                ts_code TEXT,
                name TEXT,
                in_date TEXT,
                out_date TEXT,
                is_new TEXT
            )
        """,
    },
]


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


def _normalize_sqlite_value(value: Any) -> Any:
    if value is None:
        return None

    text = str(value).strip()
    if not text or text in {"NaT", "nan", "NaN"}:
        return None

    if hasattr(value, "isoformat"):
        iso_text = value.isoformat()
        if isinstance(iso_text, str):
            if "T" in iso_text:
                return iso_text.split("T", maxsplit=1)[0]
            return iso_text

    return value


def _fetch_table_rows(session: Any, src_db: str, table_name: str, columns: list[str]) -> list[tuple[Any, ...]]:
    select_list = ", ".join(columns)
    script = f"select {select_list} from loadTable({_quote(src_db)}, {_quote(table_name)})"
    df = session.run(script)
    if df is None or len(df) == 0:
        return []

    rows: list[tuple[Any, ...]] = []
    for row in df.itertuples(index=False):
        rows.append(tuple(_normalize_sqlite_value(getattr(row, column)) for column in columns))
    return rows


def _ensure_table(conn: sqlite3.Connection, create_sql: str) -> None:
    conn.execute(create_sql)


def _write_rows(
    conn: sqlite3.Connection,
    *,
    table_name: str,
    columns: list[str],
    create_sql: str,
    rows: list[tuple[Any, ...]],
    replace_existing: bool,
) -> int:
    _ensure_table(conn, create_sql)
    if replace_existing:
        conn.execute(f"DELETE FROM {table_name}")
    if rows:
        placeholders = ", ".join("?" for _ in columns)
        column_sql = ", ".join(columns)
        conn.executemany(
            f"INSERT INTO {table_name} ({column_sql}) VALUES ({placeholders})",
            rows,
        )
    return len(rows)


def export_sw2021_market_data(
    session: Any,
    *,
    source_db: str,
    target_sqlite: str | Path,
    replace_existing: bool,
) -> list[ExportSummary]:
    sqlite_path = _resolve_path(str(target_sqlite))
    if replace_existing and sqlite_path.exists():
        sqlite_path.unlink()
    sqlite_path.parent.mkdir(parents=True, exist_ok=True)

    summaries: list[ExportSummary] = []
    with sqlite3.connect(sqlite_path) as conn:
        for spec in TABLE_SPECS:
            table_name = str(spec["table_name"])
            columns = cast(list[str], spec["columns"])
            create_sql = str(spec["create_sql"])
            rows = _fetch_table_rows(session, source_db, table_name, columns)
            inserted = _write_rows(
                conn,
                table_name=table_name,
                columns=columns,
                create_sql=create_sql,
                rows=rows,
                replace_existing=replace_existing,
            )
            summaries.append(
                ExportSummary(
                    source_db=source_db,
                    source_table=table_name,
                    target_db=sqlite_path,
                    target_table=table_name,
                    rows=inserted,
                )
            )
        conn.commit()
    return summaries


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export sw2021 tables from DolphinDB market_data into SQLite",
    )
    parser.add_argument(
        "--src-db",
        default=os.getenv("DOLPHINDB_MARKET_DATA_DB", "dfs://market_data"),
        help="Source DolphinDB database path",
    )
    parser.add_argument(
        "--sqlite",
        default=os.getenv("SQLITE_MARKET_DATA_DB_PATH", "./market_data.db"),
        help="Target SQLite file",
    )
    parser.add_argument(
        "--replace-existing",
        action="store_true",
        help="Delete the target SQLite file before writing",
    )
    return parser.parse_args()


def main() -> None:
    load_dotenv()
    args = parse_args()

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
        summaries = export_sw2021_market_data(
            session,
            source_db=args.src_db,
            target_sqlite=args.sqlite,
            replace_existing=args.replace_existing,
        )

        for summary in summaries:
            print(
                f"{summary.source_db}/{summary.source_table} -> {summary.target_db}::{summary.target_table} rows={summary.rows}"
            )
        print("Export completed")
    finally:
        close = getattr(session, "close", None)
        if callable(close):
            close()


if __name__ == "__main__":
    main()