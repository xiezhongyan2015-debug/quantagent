from __future__ import annotations

import os
import textwrap

from dotenv import load_dotenv


def main() -> None:
    load_dotenv()

    try:
        import dolphindb as ddb
    except Exception as exc:  # pragma: no cover
        raise RuntimeError("dolphindb package is required") from exc

    host = os.getenv("DOLPHINDB_HOST", "localhost").strip()
    port = int(os.getenv("DOLPHINDB_PORT", "8848"))
    user = os.getenv("DOLPHINDB_USER", "admin").strip()
    password = os.getenv("DOLPHINDB_PASSWORD", "123456").strip()

    db_path = "dfs://ohlcv_daily"
    src_table = "stock_kline_daily"
    dst_table = "stock_kline_daily_qfq"

    session = ddb.session()
    session.connect(host, port, user, password)

    # qfq_ratio = adjust_factor / last(adjust_factor) per code, so latest close is unchanged.
    script = textwrap.dedent(
        f"""
        db = database(\"{db_path}\");

        if(existsTable(\"{db_path}\", \"{dst_table}\")){{
            dropTable(db, \"{dst_table}\");
        }}

        src = loadTable(\"{db_path}\", \"{src_table}\");

        base = select
            code,
            trade_date,
            open,
            high,
            low,
            close,
            volume,
            adjust_factor
        from src
        where isValid(code) and isValid(trade_date)
        context by code
        order by code, trade_date;

        last_af = select code, last(adjust_factor) as last_adjust_factor
                  from base
                  group by code;

        joined = lj(base, last_af, `code);

        qfq = select
            code,
            trade_date,
            open * adjust_factor / iif(last_adjust_factor == 0 or isNull(last_adjust_factor), 1.0, last_adjust_factor) as open,
            high * adjust_factor / iif(last_adjust_factor == 0 or isNull(last_adjust_factor), 1.0, last_adjust_factor) as high,
            low * adjust_factor / iif(last_adjust_factor == 0 or isNull(last_adjust_factor), 1.0, last_adjust_factor) as low,
            close * adjust_factor / iif(last_adjust_factor == 0 or isNull(last_adjust_factor), 1.0, last_adjust_factor) as close,
            volume
        from joined
        order by code, trade_date;

        src_pt = loadTable(\"{db_path}\", \"{src_table}\");
        src_schema = schema(src_pt);
        src_partition_cols = src_schema.partitionColumnName;
        createPartitionedTable(db, qfq, \"{dst_table}\", src_partition_cols).append!(qfq);

        row_count = exec count(*) from loadTable(\"{db_path}\", \"{dst_table}\");
        schema(loadTable(\"{db_path}\", \"{dst_table}\"));
        row_count;
        """
    )

    try:
        result = session.run(script)
        print("Created table:", f"{db_path}/{dst_table}")
        print("Result:", result)
    finally:
        close = getattr(session, "close", None)
        if callable(close):
            close()


if __name__ == "__main__":
    main()
