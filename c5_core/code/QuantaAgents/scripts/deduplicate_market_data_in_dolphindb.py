from __future__ import annotations

import argparse
import os
import textwrap

from dotenv import load_dotenv


def _quote(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def build_init_script(*, dst_db: str, dst_table: str, replace_existing: bool, truncate_existing: bool) -> str:
    dst_db_q = _quote(dst_db)
    dst_table_q = _quote(dst_table)

    replace_block = ""
    if replace_existing and not truncate_existing:
        replace_block = textwrap.dedent(
            f"""
            if(existsTable({dst_db_q}, {dst_table_q})) {{
                dropTable(db, {dst_table_q});
            }}
            """
        )

    return textwrap.dedent(
        f"""
        if(!existsDatabase({dst_db_q})) {{
            database({dst_db_q}, VALUE, date(1990.01.01)..date(2050.12.31));
        }}

        db = database({dst_db_q});

        {replace_block}

        if(!existsTable({dst_db_q}, {dst_table_q})) {{
            schema_t = table(
                1:0,
                `code`trade_date`open`high`low`close`volume,
                [SYMBOL, DATE, DOUBLE, DOUBLE, DOUBLE, DOUBLE, DOUBLE]
            );
            createPartitionedTable(db, schema_t, {dst_table_q}, `trade_date);
        }}
        1;
        """
    )


def build_dedup_script(
    *,
    src_db: str,
    src_table: str,
    dst_db: str,
    dst_table: str,
    replace_original: bool,
) -> str:
    src_db_q = _quote(src_db)
    src_table_q = _quote(src_table)
    dst_db_q = _quote(dst_db)
    dst_table_q = _quote(dst_table)

    return textwrap.dedent(
        f"""
        src = loadTable({src_db_q}, {src_table_q});
        dst = loadTable({dst_db_q}, {dst_table_q});

        ordered = select
            code,
            trade_date,
            open,
            high,
            low,
            close,
            volume
        from src
        where isValid(code)
          and isValid(trade_date)
          and isValid(open)
          and isValid(high)
          and isValid(low)
          and isValid(close)
          and isValid(volume)
        order by code, trade_date;

        deduped = select
            code,
            trade_date,
            last(open) as open,
            last(high) as high,
            last(low) as low,
            last(close) as close,
            last(volume) as volume
        from ordered
        group by code, trade_date
        order by code, trade_date;

        source_rows = exec count(*) from ordered;
        deduped_rows = exec count(*) from deduped;

        if(deduped_rows == 0) {{
            select 0 as inserted_rows, source_rows as source_rows, deduped_rows as deduped_rows;
        }} else {{
            {"delete from dst;" if replace_original else ""}
            appended = dst.append!(deduped);
            select
                appended as inserted_rows,
                source_rows as source_rows,
                deduped_rows as deduped_rows,
                min(trade_date) as min_trade_date,
                max(trade_date) as max_trade_date
            from deduped;
        }}
        """
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Deduplicate stock_kline_daily_qfq inside DolphinDB by code and trade_date",
    )
    parser.add_argument("--src-db", default=os.getenv("DOLPHINDB_MARKET_DATA_DB", "dfs://market_data"), help="Source DolphinDB database path")
    parser.add_argument("--src-table", default="stock_kline_daily_qfq", help="Source DolphinDB table")
    parser.add_argument("--dst-db", default=os.getenv("DOLPHINDB_MARKET_DATA_DB", "dfs://market_data"), help="Target DolphinDB database path")
    parser.add_argument("--dst-table", default="stock_kline_daily_qfq_dedup", help="Target deduplicated table name")
    parser.add_argument(
        "--replace-original",
        action="store_true",
        help="Overwrite the source table in-place instead of writing to a separate deduplicated table",
    )
    parser.add_argument(
        "--replace-existing",
        action="store_true",
        help="Drop the target table before writing the deduplicated copy",
    )
    parser.add_argument(
        "--print-only",
        action="store_true",
        help="Print generated DolphinDB scripts without executing them",
    )
    return parser.parse_args()


def main() -> None:
    load_dotenv()
    args = parse_args()

    if args.replace_original and (args.src_db != args.dst_db or args.src_table != args.dst_table):
        raise ValueError("--replace-original requires src-table and dst-table to refer to the same table")
    if not args.replace_original and args.src_db == args.dst_db and args.src_table == args.dst_table:
        raise ValueError("src-table and dst-table must differ unless --replace-original is set")

    host = os.getenv("DOLPHINDB_HOST", "localhost").strip()
    port = int(os.getenv("DOLPHINDB_PORT", "8848"))
    user = os.getenv("DOLPHINDB_USER", "admin").strip()
    password = os.getenv("DOLPHINDB_PASSWORD", "123456").strip()

    init_script = build_init_script(
        dst_db=args.dst_db,
        dst_table=args.dst_table,
        replace_existing=args.replace_existing,
        truncate_existing=args.replace_original,
    )
    dedup_script = build_dedup_script(
        src_db=args.src_db,
        src_table=args.src_table,
        dst_db=args.dst_db,
        dst_table=args.dst_table,
        replace_original=args.replace_original,
    )

    if args.print_only:
        print(init_script)
        print(dedup_script)
        return

    try:
        import dolphindb as ddb
    except Exception as exc:  # pragma: no cover
        raise RuntimeError("dolphindb package is required") from exc

    session = ddb.session()
    session.connect(host, port, user, password)

    try:
        session.run(init_script)
        result = session.run(dedup_script)
        inserted_rows = int(result.iloc[0].get("inserted_rows", 0) or 0) if result is not None and len(result) > 0 else 0
        source_rows = int(result.iloc[0].get("source_rows", 0) or 0) if result is not None and len(result) > 0 else 0
        deduped_rows = int(result.iloc[0].get("deduped_rows", 0) or 0) if result is not None and len(result) > 0 else 0

        print("Dedup completed")
        print(f"source: {args.src_db}/{args.src_table}")
        print(f"target: {args.dst_db}/{args.dst_table}")
        print("replace_original:", args.replace_original)
        print("replace_existing:", args.replace_existing)
        print("source_rows:", source_rows)
        print("deduped_rows:", deduped_rows)
        print("inserted_rows:", inserted_rows)
    finally:
        close = getattr(session, "close", None)
        if callable(close):
            close()


if __name__ == "__main__":
    main()