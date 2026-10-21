from __future__ import annotations

import argparse
from datetime import date
import os
import textwrap

from dotenv import load_dotenv


def _quote(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def build_init_script(*, dst_db: str, dst_table: str) -> str:
    dst_db_q = _quote(dst_db)
    dst_table_q = _quote(dst_table)

    return textwrap.dedent(
        f"""
        if(!existsDatabase({dst_db_q})) {{
            database({dst_db_q}, VALUE, `SSE`SZSE);
        }}

        db = database({dst_db_q});

        if(!existsTable({dst_db_q}, {dst_table_q})) {{
            schema_t = table(
                1:0,
                `symbol`exchange`interval`datetime`volume`turnover`open_interest`open_price`high_price`low_price`close_price,
                [SYMBOL, SYMBOL, SYMBOL, TIMESTAMP, DOUBLE, DOUBLE, DOUBLE, DOUBLE, DOUBLE, DOUBLE, DOUBLE]
            );
            createPartitionedTable(db, schema_t, {dst_table_q}, `exchange);
        }}
        1;
        """
    )


def build_range_import_script(
    *,
    src_db: str,
    src_table: str,
    dst_db: str,
    dst_table: str,
    interval: str,
    start_date: date,
    end_date: date,
    replace_existing: bool,
) -> str:
    src_db_q = _quote(src_db)
    src_table_q = _quote(src_table)
    dst_db_q = _quote(dst_db)
    dst_table_q = _quote(dst_table)
    interval_q = _quote(interval)
    start_text = _quote(start_date.strftime("%Y.%m.%d"))
    end_text = _quote(end_date.strftime("%Y.%m.%d"))

    delete_block = ""
    if replace_existing:
        delete_block = textwrap.dedent(
            """
            delete from dst
            where interval = interval_value
              and datetime >= timestamp(start_d)
              and datetime < timestamp(end_d);
            """
        )

    return textwrap.dedent(
        f"""
        src = loadTable({src_db_q}, {src_table_q});
        dst = loadTable({dst_db_q}, {dst_table_q});
        interval_value = {interval_q};
        start_d = date({start_text});
        end_d = date({end_text});

        normalized = select
            left(string(code), 6) as symbol,
            iif(right(string(code), 2) == "SH", "SSE", "SZSE") as exchange,
            nanotimestamp(trade_date) as datetime,
            interval_value as interval,
            double(volume) as volume,
            double(close) * double(volume) as turnover,
            0.0 as open_interest,
            double(open) as open_price,
            double(high) as high_price,
            double(low) as low_price,
            double(close) as close_price
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
        order by code, trade_date;

        row_count = exec count(*) from normalized;
        if(row_count == 0) {{
            select 0 as inserted_rows, start_d as start_date, end_d as end_date;
        }} else {{
            {delete_block}

            appended = dst.append!(normalized);
            symbol_count = exec count(*) from (select distinct symbol from normalized);

            select
                count(*) as inserted_rows,
                min(datetime) as min_datetime,
                max(datetime) as max_datetime,
                symbol_count as symbol_count
            from normalized;
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
        description="Import stock_kline_daily_qfq into vnpy_bar_db.bar format",
    )
    parser.add_argument("--src-db", default="dfs://ohlcv_daily", help="Source DolphinDB database path")
    parser.add_argument("--src-table", default="stock_kline_daily_qfq", help="Source table name")
    parser.add_argument("--dst-db", default="dfs://vnpy_bar_db", help="Target vnpy bar database path")
    parser.add_argument("--dst-table", default="bar", help="Target vnpy bar table")
    parser.add_argument("--interval", default="d", help="vnpy interval value, e.g. d/1m")
    parser.add_argument(
        "--replace-existing",
        action="store_true",
        help="Delete overlapping datetime range for this interval before append",
    )
    parser.add_argument(
        "--batch-months",
        type=int,
        default=1,
        help="Import batch size in months (default: 1)",
    )
    parser.add_argument(
        "--print-only",
        action="store_true",
        help="Only print generated DolphinDB init script without executing",
    )
    return parser.parse_args()


def main() -> None:
    load_dotenv()
    args = parse_args()

    host = os.getenv("DOLPHINDB_HOST", "localhost").strip()
    port = int(os.getenv("DOLPHINDB_PORT", "8848"))
    user = os.getenv("DOLPHINDB_USER", "admin").strip()
    password = os.getenv("DOLPHINDB_PASSWORD", "123456").strip()

    init_script = build_init_script(dst_db=args.dst_db, dst_table=args.dst_table)
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

        total_inserted = 0
        non_empty_batches = 0

        for start_d, end_d in ranges:
            batch_script = build_range_import_script(
                src_db=args.src_db,
                src_table=args.src_table,
                dst_db=args.dst_db,
                dst_table=args.dst_table,
                interval=args.interval,
                start_date=start_d,
                end_date=end_d,
                replace_existing=args.replace_existing,
            )
            batch_result = session.run(batch_script)
            if batch_result is None or len(batch_result) == 0:
                continue

            inserted_rows = int(batch_result.iloc[0].get("inserted_rows", 0) or 0)
            if inserted_rows > 0:
                non_empty_batches += 1
                total_inserted += inserted_rows
                print(f"batch {start_d} -> {end_d}: inserted_rows={inserted_rows}")

        target_count = session.run(
            f"select count(*) as total_rows from loadTable({_quote(args.dst_db)}, {_quote(args.dst_table)})"
        )

        print("Import completed")
        print(f"source: {args.src_db}/{args.src_table}")
        print(f"target: {args.dst_db}/{args.dst_table}")
        print(f"interval: {args.interval}")
        print("replace_existing:", args.replace_existing)
        print("batch_months:", args.batch_months)
        print("non_empty_batches:", non_empty_batches)
        print("total_inserted:", total_inserted)
        print("target_total_rows:", int(target_count.iloc[0]["total_rows"]))
    finally:
        close = getattr(session, "close", None)
        if callable(close):
            close()


if __name__ == "__main__":
    main()
