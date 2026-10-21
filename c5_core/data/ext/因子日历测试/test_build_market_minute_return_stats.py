# -*- coding: utf-8 -*-

from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

import build_market_minute_return_stats as target


def _write_stock(path: Path, stock_number: int) -> pd.DataFrame:
    rows = []
    for day, date in enumerate(pd.bdate_range("2026-05-28", periods=2)):
        previous_close = 10.0 + stock_number + day * 0.1
        for minute in range(8):
            timestamp = date + pd.Timedelta(hours=9, minutes=31 + minute)
            minute_return = (stock_number + 1) * 0.0001 + minute * 0.00002
            open_price = previous_close
            close_price = previous_close * (1.0 + minute_return)
            rows.append(
                {
                    "datetime": timestamp,
                    "open": open_price,
                    "close": close_price,
                }
            )
            previous_close = close_price
    frame = pd.DataFrame(rows)
    frame.to_parquet(path, index=False)
    return frame


def test_market_minute_stats_match_independent_values(tmp_path: Path) -> None:
    frames = []
    for stock_number, code in enumerate(("sh600000", "sh600001", "sz000001")):
        frame = _write_stock(tmp_path / f"{code}.parquet", stock_number)
        frame["code"] = code
        frames.append(frame)

    expected_rows = []
    combined = pd.concat(frames, ignore_index=True)
    combined["date"] = combined["datetime"].dt.normalize()
    previous_close = combined.groupby(
        ["code", "date"], sort=False
    )["close"].shift(1)
    combined["simple_return"] = (
        combined["close"] / previous_close.fillna(combined["open"]) - 1.0
    )
    for timestamp, part in combined.groupby("datetime", sort=True):
        expected_rows.append(
            {
                "datetime": timestamp,
                "market_stock_count": len(part),
                "market_mean_return": part["simple_return"].mean(),
                "market_std_return": part["simple_return"].std(ddof=1),
            }
        )
    expected = pd.DataFrame(expected_rows)

    connection = duckdb.connect()
    try:
        actual = connection.execute(
            target.build_query(
                str(tmp_path / "*.parquet"),
                "2026-05-28",
                "2026-05-29",
            )
        ).fetchdf()
    finally:
        connection.close()

    pd.testing.assert_series_equal(
        pd.to_datetime(actual["datetime"]),
        pd.to_datetime(expected["datetime"]),
        check_names=False,
    )
    np.testing.assert_array_equal(
        actual["market_stock_count"].to_numpy(),
        expected["market_stock_count"].to_numpy(),
    )
    np.testing.assert_allclose(
        actual[["market_mean_return", "market_std_return"]].to_numpy(float),
        expected[["market_mean_return", "market_std_return"]].to_numpy(float),
        rtol=1e-12,
        atol=1e-14,
    )


def test_new_script_compiles() -> None:
    path = Path(target.__file__)
    compile(path.read_text(encoding="utf-8"), str(path), "exec")
