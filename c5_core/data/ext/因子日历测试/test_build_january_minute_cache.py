# -*- coding: utf-8 -*-
"""每日分钟轻量文件的关键计算检查。"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

import build_january_minute_cache as target


def make_day(date: str, base_price: float = 10.0) -> pd.DataFrame:
    day = pd.Timestamp(date)
    datetimes = [
        day + pd.Timedelta(minutes=int(minute))
        for minute in target.TRADING_MINUTES
    ]
    minute_move = np.arange(target.MINUTES_PER_DAY, dtype=np.float64) * 0.001
    close = base_price + minute_move
    return pd.DataFrame(
        {
            "datetime": datetimes,
            "qfq_open": close - 0.01,
            "qfq_high": close + 0.02,
            "qfq_low": close - 0.02,
            "qfq_close": close,
            "volume": np.full(target.MINUTES_PER_DAY, 100.0),
            "amount": close / 2.0 * 100.0,
            "float_shares": np.full(target.MINUTES_PER_DAY, 1_000_000.0),
            "total_shares": np.full(target.MINUTES_PER_DAY, 2_000_000.0),
            "qfq_ratio": np.full(target.MINUTES_PER_DAY, 2.0),
            "source_id": np.ones(target.MINUTES_PER_DAY, dtype=np.uint8),
        }
    )


def test_only_exact_240_trading_timepoints_are_kept():
    complete = make_day("2025-01-02")
    incomplete = make_day("2025-01-03").iloc[:-1]
    off_session = make_day("2025-01-06")
    extra = off_session.iloc[[0]].copy()
    extra["datetime"] = pd.Timestamp("2025-01-06 09:30:00")
    frame = pd.concat([complete, incomplete, off_session, extra], ignore_index=True)

    kept, dates = target.keep_complete_days(frame)

    assert dates.tolist() == [pd.Timestamp("2025-01-02"), pd.Timestamp("2025-01-06")]
    assert kept.groupby("_date").size().tolist() == [240, 240]
    assert kept.groupby("_date")["_position"].nunique().tolist() == [240, 240]


def test_plain_ohlc_schema_is_read_once_and_renamed(tmp_path: Path, monkeypatch):
    plain = make_day("2025-01-02").rename(
        columns={
            "qfq_open": "open",
            "qfq_high": "high",
            "qfq_low": "low",
            "qfq_close": "close",
        }
    )
    path = tmp_path / "sz000001.parquet"
    pq.write_table(pa.Table.from_pandas(plain, preserve_index=False), path)
    original_read_table = target.pq.read_table
    calls = []

    def counted_read_table(*args, **kwargs):
        calls.append((args, kwargs))
        return original_read_table(*args, **kwargs)

    monkeypatch.setattr(target.pq, "read_table", counted_read_table)
    frame = target.read_one_stock(path, "2025-01-01", "2025-01-31")

    assert len(calls) == 1
    assert calls[0][1]["use_threads"] is False
    assert frame.columns.tolist() == target.INPUT_COLUMNS
    assert np.allclose(frame["qfq_close"], plain["close"])


def test_qfq_schema_has_priority_when_both_price_names_exist(tmp_path: Path):
    frame = make_day("2025-01-02")
    for plain_name, qfq_name in zip(target.PLAIN_PRICE_COLUMNS, target.QFQ_PRICE_COLUMNS):
        frame[plain_name] = frame[qfq_name] + 1000.0
    path = tmp_path / "sz000001.parquet"
    pq.write_table(pa.Table.from_pandas(frame, preserve_index=False), path)

    loaded = target.read_one_stock(path, "2025-01-01", "2025-01-31")

    assert np.allclose(loaded["qfq_close"], frame["qfq_close"])


def test_first_return_uses_day_open_instead_of_previous_day_close():
    returns = target.intraday_log_returns(np.array([11.0, 12.0]), day_open=10.0)
    assert np.isclose(returns[0], np.log(11.0 / 10.0))
    assert np.isclose(returns[1], np.log(12.0 / 11.0))
    assert not np.isclose(returns[0], np.log(11.0 / 100.0))


def test_previous_close_uses_incomplete_day_with_valid_trading():
    first = make_day("2025-01-02", base_price=10.0)
    incomplete = make_day("2025-01-03", base_price=20.0).iloc[:-1]
    third = make_day("2025-01-06", base_price=30.0)
    frame = pd.concat([first, incomplete, third], ignore_index=True)

    daily = target.build_daily_frame(frame, "sz000001")

    assert daily["date"].tolist() == [
        pd.Timestamp("2025-01-02"),
        pd.Timestamp("2025-01-06"),
    ]
    assert np.isclose(daily.loc[1, "prev_close"], incomplete.iloc[-1]["qfq_close"])


def test_price_amount_quality_is_checked_minute_by_minute():
    normal = make_day("2025-01-02")
    abnormal = make_day("2025-01-03")
    abnormal.loc[10, "amount"] *= 10.0
    daily = target.build_daily_frame(
        pd.concat([normal, abnormal], ignore_index=True), "sz000001"
    )

    assert not bool(daily.loc[0, "vwap_outside_range_1pct"])
    assert bool(daily.loc[1, "vwap_outside_range_1pct"])


def test_five_minute_blocks_do_not_cross_lunch_break():
    values = np.arange(240, dtype=np.float64)[None, :]
    blocks = target._reshape_blocks(values, 5)
    assert blocks.shape == (1, 48, 5)
    assert blocks[0, 23].tolist() == [115.0, 116.0, 117.0, 118.0, 119.0]
    assert blocks[0, 24].tolist() == [120.0, 121.0, 122.0, 123.0, 124.0]


def test_peak_and_ridge_recognition_does_not_join_lunch_edges():
    volume = np.full((21, 240), 10.0)
    volume[-1, [0, 10, 11, 119, 120]] = 20.0
    bursts = target.historical_volume_bursts(volume)
    peaks, ridges = target.classify_volume_bursts(bursts)

    assert np.flatnonzero(peaks[-1]).tolist() == [0, 119, 120]
    assert np.flatnonzero(ridges[-1]).tolist() == [10, 11]
    count, sum1, sum2, sum3 = target.ridge_gap_moments(ridges)
    assert count[-1] == 1
    assert (sum1[-1], sum2[-1], sum3[-1]) == (1.0, 1.0, 1.0)


def test_rvjn_uses_negative_returns_and_removes_over_15_percent():
    negative = np.zeros(48)
    negative[0] = -0.10
    positive = np.zeros(48)
    positive[0] = 0.10
    too_large = np.zeros(48)
    too_large[0] = -0.16

    assert np.isclose(target.rvjn_from_returns(negative), 0.01)
    assert target.rvjn_from_returns(positive) == 0.0
    assert target.rvjn_from_returns(too_large) == 0.0


def test_realized_skew_uses_card_formula():
    returns = np.array([-0.03, -0.01, 0.00, 0.04])
    centered = returns - returns.mean()
    expected = (
        np.sqrt(len(returns))
        * np.sum(centered**3)
        / np.sum(centered**2) ** 1.5
    )
    assert np.isclose(target.realized_skew(returns)[0], expected)


def test_ridge_moments_are_missing_until_twenty_prior_complete_days():
    days = [
        make_day(str(day.date()), base_price=10.0 + index)
        for index, day in enumerate(pd.bdate_range("2025-01-02", periods=21))
    ]
    frame = pd.concat(days, ignore_index=True)

    daily = target.build_daily_frame(frame, "sz000001")

    fields = [
        "ridge_gap_count",
        "ridge_gap_sum1",
        "ridge_gap_sum2",
        "ridge_gap_sum3",
    ]
    assert daily.loc[:19, fields].isna().all().all()
    assert daily.loc[20, fields].tolist() == [0.0, 0.0, 0.0, 0.0]


def test_small_stock_is_written_atomically_and_existing_output_is_skipped(tmp_path: Path):
    data_dir = tmp_path / "input"
    output_dir = tmp_path / "output"
    data_dir.mkdir()
    input_path = data_dir / "sz000001.parquet"
    table = pa.Table.from_pandas(make_day("2025-01-02"), preserve_index=False)
    pq.write_table(table, input_path)

    first = target.process_one_stock(
        input_path, output_dir, "2025-01-01", "2025-01-31"
    )
    output_path = output_dir / "sz000001.parquet"
    saved = pq.read_table(output_path).to_pandas()

    assert first == {"code": "sz000001", "status": "ok", "days": 1}
    assert output_path.exists()
    assert saved.columns.tolist() == target.OUTPUT_COLUMNS
    assert saved.loc[0, "minute_count"] == 240
    assert saved.loc[0, "code"] == "sz000001"
    assert not list(output_dir.glob(".*.tmp"))

    modified_time = output_path.stat().st_mtime_ns
    second = target.process_one_stock(
        input_path, output_dir, "2025-01-01", "2025-01-31"
    )
    assert second["status"] == "skipped"
    assert output_path.stat().st_mtime_ns == modified_time
