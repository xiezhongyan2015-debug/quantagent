# -*- coding: utf-8 -*-

from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

import build_historical_missing_minute_features as target


TIMES = [
    "09:31:00",
    "09:32:00",
    "10:00:00",
    "11:30:00",
    "13:01:00",
    "14:00:00",
    "14:56:00",
    "14:57:00",
    "14:58:00",
    "14:59:00",
    "15:00:00",
]


def _minute_frame(days: int = 25) -> pd.DataFrame:
    rows = []
    for day_index, day in enumerate(pd.bdate_range("2024-01-02", periods=days)):
        base = 10.0 + day_index * 0.05
        for minute_index, time_text in enumerate(TIMES):
            opening = base if minute_index == 0 else base + (minute_index - 1) * 0.01
            close = base + minute_index * 0.01 + 0.002 * ((-1) ** minute_index)
            volume = 100.0 + minute_index**2 * 2.0 + (minute_index % 3) * 5.0
            rows.append(
                {
                    "datetime": pd.Timestamp(f"{day.date()} {time_text}"),
                    "open": opening,
                    "high": max(opening, close) + 0.02,
                    "low": min(opening, close) - 0.02,
                    "close": close,
                    "volume": volume,
                    "amount": volume * close,
                    "float_shares": 1_000_000.0,
                }
            )
    return pd.DataFrame(rows)


def _market_frame(minute: pd.DataFrame) -> pd.DataFrame:
    close = minute["close"].to_numpy(dtype=float)
    market = np.where(np.arange(len(close)) % 2 == 0, 0.001, -0.001)
    return pd.DataFrame(
        {
            "date": minute["datetime"].dt.normalize(),
            "datetime": minute["datetime"],
            "minute_of_day": minute["datetime"].dt.hour * 60 + minute["datetime"].dt.minute,
            "market_mean_return": market,
        }
    )


def test_compute_daily_features_has_requested_columns() -> None:
    minute = _minute_frame()
    minute["market_mean_return"] = _market_frame(minute)["market_mean_return"]
    daily = target.compute_daily_features(minute, "sh600000")
    assert daily.columns.tolist() == target.OUTPUT_COLUMNS
    assert len(daily) == 25
    assert daily["minute_count"].eq(len(TIMES)).all()
    expected_share = minute.loc[
        minute["datetime"].dt.time >= pd.Timestamp("14:56:00").time(), "volume"
    ].iloc[:5].sum() / minute.iloc[: len(TIMES)]["volume"].sum()
    np.testing.assert_allclose(daily.iloc[0]["close_auction_volume_share_5m"], expected_share)
    assert np.isfinite(daily.iloc[-1]["follow_wave_base"])
    assert np.isfinite(daily.iloc[-1]["top10pct_minute_return"])


def test_correlations_entropy_and_semibeta_are_computed() -> None:
    minute = _minute_frame()
    minute["market_mean_return"] = _market_frame(minute)["market_mean_return"]
    daily = target.compute_daily_features(minute, "sh600000")
    row = daily.iloc[-1]
    assert np.isfinite(row["price_diff_pos_corr"])
    assert np.isfinite(row["volume_diff_pos_corr"])
    assert row["minute_volume_bin_entropy_trial"] > 0
    assert np.isfinite(row["semibeta_market_pos_asset_pos"])
    assert "trial" in "minute_volume_bin_entropy_trial"
    assert "maxbin" in target.FEATURE_NOTES["minute_volume_bin_entropy_trial"]


def test_duckdb_build_atomic_write_and_resume(tmp_path: Path) -> None:
    minute = _minute_frame()
    market = _market_frame(minute)
    minute_path = tmp_path / "sh600000.parquet"
    market_path = tmp_path / "shared_market_minute_stats_2024.parquet"
    output_dir = tmp_path / "output"
    minute.to_parquet(minute_path, index=False)
    market.to_parquet(market_path, index=False)
    task = target.BuildTask(
        code="sh600000",
        minute_path=minute_path,
        market_files=(market_path,),
        start="2024-01-02",
        end=str(minute["datetime"].dt.date.max()),
        output_dir=output_dir,
        memory_limit="1GB",
    )
    first = target.build_one_stock(task)
    assert first["status"] == "written"
    output = output_dir / "sh600000.parquet"
    actual = pd.read_parquet(output)
    expected_input = minute.copy()
    expected_input["market_mean_return"] = market["market_mean_return"].to_numpy()
    expected = target.compute_daily_features(expected_input, "sh600000")
    assert actual[["date", "code", "minute_count", "market_minute_count"]].equals(
        expected[["date", "code", "minute_count", "market_minute_count"]]
    )
    for column in target.OUTPUT_COLUMNS[4:]:
        np.testing.assert_allclose(
            actual[column].to_numpy(dtype=float),
            expected[column].to_numpy(dtype=float),
            rtol=1e-9,
            atol=1e-12,
            equal_nan=True,
            err_msg=column,
        )
    parquet = pq.ParquetFile(output)
    assert parquet.schema_arrow.names == target.OUTPUT_COLUMNS
    assert parquet.schema_arrow.metadata[b"feature_version"].decode() == target.FEATURE_VERSION
    second = target.build_one_stock(task)
    assert second["status"] == "skipped"


def test_parse_codes_and_cli_defaults(tmp_path: Path) -> None:
    minute_dir = tmp_path / "minute"
    minute_dir.mkdir()
    for code in ("sh600000", "sz000001"):
        (minute_dir / f"{code}.parquet").touch()
    assert target.parse_codes(None, minute_dir) == ["sh600000", "sz000001"]
    assert target.parse_codes("SZ000001, sh600000", minute_dir) == ["sh600000", "sz000001"]
    stock_cache = tmp_path / "daily_by_stock"
    stock_cache.mkdir()
    for name in ("SZ000001.parquet", "sh600000.parquet", "metadata.parquet", "sz000002.csv"):
        (stock_cache / name).touch()
    assert target.parse_codes(str(stock_cache), minute_dir) == ["sh600000", "sz000001"]
    args = target.parse_args([])
    assert args.workers == 2
    assert args.memory_limit == "2GB"
    assert args.output_dir == target.DEFAULT_OUTPUT_DIR
