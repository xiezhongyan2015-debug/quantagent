from __future__ import annotations

import argparse
from datetime import date
import os
import textwrap

from dotenv import load_dotenv


def _quote(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def build_init_script(
    *,
    dst_db: str,
    dst_table: str,
    replace_existing: bool,
) -> str:
    dst_db_q = _quote(dst_db)
    dst_table_q = _quote(dst_table)

    replace_block = ""
    if replace_existing:
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


def build_batch_migration_script(
    *,
    src_db: str,
    src_table: str,
    dst_db: str,
    dst_table: str,
    start_date: date,
    end_date: date,
) -> str:
    src_db_q = _quote(src_db)
    src_table_q = _quote(src_table)
    dst_db_q = _quote(dst_db)
    dst_table_q = _quote(dst_table)
    start_q = _quote(start_date.strftime("%Y.%m.%d"))
    end_q = _quote(end_date.strftime("%Y.%m.%d"))

    return textwrap.dedent(
        f"""
        src = loadTable({src_db_q}, {src_table_q});
        dst = loadTable({dst_db_q}, {dst_table_q});
        start_d = date({start_q});
        end_d = date({end_q});

        normalized_base = select
            iif(
                right(code_text, 5) == ".SZSE" or right(code_text, 4) == ".SSE",
                code_text,
                iif(
                    right(code_text, 3) == ".SH",
                    left(code_text, strlen(code_text) - 3) + ".SSE",
                    iif(
                        right(code_text, 3) == ".SZ",
                        left(code_text, strlen(code_text) - 3) + ".SZSE",
                        iif(
                            left(code_text, 2) == "60" or left(code_text, 2) == "68",
                            left(code_text, 6) + ".SSE",
                            iif(
                                left(code_text, 2) == "00" or left(code_text, 2) == "30",
                                left(code_text, 6) + ".SZSE",
                                code_text
                            )
                        )
                    )
                )
            ) as code_norm,
            trade_date,
            open,
            high,
            low,
            close,
            volume
        from (
            select
                string(code) as code_text,
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
                            and trade_date >= start_d
                            and trade_date < end_d
        );

        normalized = select
            code_norm as code,
            trade_date,
            double(open) as open,
            double(high) as high,
            double(low) as low,
            double(close) as close,
            double(volume) as volume
        from normalized_base
        where right(code_norm, 5) == ".SZSE" or right(code_norm, 4) == ".SSE";

        normalized_rows = exec count(*) from normalized;
        if(normalized_rows == 0) {{
            select
                0 as appended_rows,
                normalized_rows as normalized_rows,
                start_d as batch_start,
                end_d as batch_end;
        }} else {{
            appended = dst.append!(normalized);
            select
                appended as appended_rows,
                normalized_rows as normalized_rows,
                start_d as batch_start,
                end_d as batch_end;
        }}
        """
    )


def _add_months(value: date, months: int) -> date:
    total = value.year * 12 + (value.month - 1) + months
    year = total // 12
    month = total % 12 + 1
    return date(year, month, 1)


def _iter_month_ranges(start: date, end: date, batch_months: int) -> list[tuple[date, date]]:
    ranges: list[tuple[date, date]] = []
    cursor = date(start.year, start.month, 1)
    while cursor < end:
        nxt = _add_months(cursor, batch_months)
        if nxt > end:
            nxt = end
        ranges.append((cursor, nxt))
        cursor = nxt
    return ranges


def _get_source_date_range(session, src_db: str, src_table: str) -> tuple[date, date]:
    script = textwrap.dedent(
        f"""
        src = loadTable({_quote(src_db)}, {_quote(src_table)});
        select min(trade_date) as min_date, max(trade_date) as max_date from src where isValid(trade_date);
        """
    )
    df = session.run(script)
    if df is None or len(df) == 0:
        raise RuntimeError("Source table is empty or no valid trade_date found")

    min_raw = df.iloc[0]["min_date"]
    max_raw = df.iloc[0]["max_date"]
    if min_raw is None or max_raw is None:
        raise RuntimeError("Source table has null min/max trade_date")

    min_date = date.fromisoformat(str(min_raw)[:10])
    max_date = date.fromisoformat(str(max_raw)[:10])
    end_exclusive = _add_months(date(max_date.year, max_date.month, 1), 1)
    return min_date, end_exclusive


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Create dfs://market_data and migrate stock_kline_daily_qfq into it "
            "with code suffixes normalized to .SSE/.SZSE"
        )
    )
    parser.add_argument("--src-db", default="dfs://ohlcv_daily", help="Source DolphinDB database path")
    parser.add_argument("--src-table", default="stock_kline_daily_qfq", help="Source table name")
    parser.add_argument("--dst-db", default="dfs://market_data", help="Target DolphinDB database path")
    parser.add_argument("--dst-table", default="stock_kline_daily_qfq", help="Target table name")
    parser.add_argument(
        "--batch-months",
        type=int,
        default=1,
        help="Append batch size in months (default: 1)",
    )
    parser.add_argument(
        "--replace-existing",
        action="store_true",
        help="Drop existing target table before migration",
    )
    parser.add_argument(
        "--print-only",
        action="store_true",
        help="Print generated DolphinDB script without executing",
    )
    return parser.parse_args()


def main() -> None:
    load_dotenv()
    args = parse_args()

    host = os.getenv("DOLPHINDB_HOST", "localhost").strip()
    port = int(os.getenv("DOLPHINDB_PORT", "8848"))
    user = os.getenv("DOLPHINDB_USER", "admin").strip()
    password = os.getenv("DOLPHINDB_PASSWORD", "123456").strip()

    init_script = build_init_script(
        dst_db=args.dst_db,
        dst_table=args.dst_table,
        replace_existing=args.replace_existing,
    )

    if args.print_only:
        print(init_script)
        return

    if args.batch_months < 1:
        raise ValueError("--batch-months must be >= 1")

    try:
        import dolphindb as ddb
    except Exception as exc:  # pragma: no cover
        raise RuntimeError("dolphindb package is required") from exc

    session = ddb.session()
    session.connect(host, port, user, password)

    try:
        session.run(init_script)
        min_date, end_exclusive = _get_source_date_range(session, args.src_db, args.src_table)
        ranges = _iter_month_ranges(min_date, end_exclusive, args.batch_months)

        total_appended = 0
        non_empty_batches = 0
        for start_d, end_d in ranges:
            batch_script = build_batch_migration_script(
                src_db=args.src_db,
                src_table=args.src_table,
                dst_db=args.dst_db,
                dst_table=args.dst_table,
                start_date=start_d,
                end_date=end_d,
            )
            result = session.run(batch_script)
            if result is None or len(result) == 0:
                continue

            normalized_rows = int(result.iloc[0].get("normalized_rows", 0) or 0)
            if normalized_rows > 0:
                non_empty_batches += 1
                total_appended += normalized_rows
                print(f"batch {start_d} -> {end_d}: normalized_rows={normalized_rows}")

        target_count = session.run(
            f"select count(*) as total_rows from loadTable({_quote(args.dst_db)}, {_quote(args.dst_table)})"
        )

        print("Migration completed")
        print(f"source: {args.src_db}/{args.src_table}")
        print(f"target: {args.dst_db}/{args.dst_table}")
        print("replace_existing:", args.replace_existing)
        print("batch_months:", args.batch_months)
        print("non_empty_batches:", non_empty_batches)
        print("total_appended:", total_appended)
        print("target_total_rows:", int(target_count.iloc[0]["total_rows"]))
    finally:
        close = getattr(session, "close", None)
        if callable(close):
            close()


if __name__ == "__main__":
    main()
