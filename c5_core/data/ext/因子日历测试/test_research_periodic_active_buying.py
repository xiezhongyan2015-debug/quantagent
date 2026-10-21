# -*- coding: utf-8 -*-

from pathlib import Path

import numpy as np
import pandas as pd

from research_periodic_active_buying import (
    CandidateBuilder,
    Settings,
    add_level2_aggregates,
    add_window_ids,
    apply_cooldown,
    wilson_interval,
)


def settings() -> Settings:
    return Settings(
        catalog=Path("unused.duckdb"),
        output_dir=Path("unused"),
        min_minute_amount=100_000,
    )


def test_candidate_builder_finds_five_regular_pulses_and_next_hit():
    builder = CandidateBuilder(["sz000001"], settings())
    for day in range(20):
        value = 90_000.0 if day % 2 == 0 else 110_000.0
        builder.add_history(np.full((1, 240), value))

    minute_open = np.full((1, 240), 10.0)
    minute_close = np.full((1, 240), 10.0)
    amount = np.full((1, 240), 100_000.0)
    pulses = [10, 13, 16, 19, 22, 25]
    amount[0, pulses] = 250_000.0
    minute_close[0, pulses] = 10.02
    minute_close[0, 23] = 10.005
    arrays = {
        "open": minute_open,
        "high": np.maximum(minute_open, minute_close),
        "low": np.minimum(minute_open, minute_close),
        "close": minute_close,
        "volume": amount / 10.0,
        "amount": amount,
        "ask1": minute_close + 0.01,
        "bid1": minute_close,
        "ask1_volume": np.full((1, 240), 10_000.0),
        "bid1_volume": np.full((1, 240), 10_000.0),
    }
    rows = builder.process_day(pd.Timestamp("2026-02-10"), arrays)
    found = [row for row in rows if row["current_position"] == 22]
    assert len(found) == 1
    row = found[0]
    assert row["period"] == 3
    assert row["trigger_position"] == 23
    assert row["predicted_position"] == 25
    assert row["volume_hit"] is True
    assert row["joint_minute_hit"] is True
    assert row["past_up_count"] == 5
    assert row["no_early_spike"] is True
    assert row["pre_window_up"] is True


def test_cooldown_is_applied_after_a_rule_is_filtered():
    frame = pd.DataFrame(
        {
            "date": pd.to_datetime(["2026-01-05"] * 3),
            "code": ["sz000001"] * 3,
            "trigger_position": [10, 15, 31],
        }
    )
    result = apply_cooldown(frame, minutes=20)
    assert result["trigger_position"].tolist() == [10, 31]


def test_level2_aggregate_uses_all_five_pulses():
    base = pd.DataFrame(
        {
            "candidate_id": ["x"],
            "date": [pd.Timestamp("2026-01-05")],
            "code": ["sz000001"],
            "volume_hit": [True],
            "next_up": [True],
            "predicted_position": [25],
            "next_spike_position": [25],
            "trigger_position": [23],
            **{f"pulse{i}_position": [10 + 3 * (i - 1)] for i in range(1, 6)},
        }
    )
    base = add_window_ids(base)
    rows = []
    for number in range(1, 6):
        rows.append(
            {
                "window_id": base.loc[0, f"pulse{number}_window_id"],
                "trade_amount": 100.0,
                "active_buy_amount": 80.0,
                "active_sell_amount": 20.0,
                "active_buy_share": 0.8,
                "active_net_buy_share": 0.6,
                "buy_order_median": 10.0,
                "buy_order_q25": 5.0,
                "buy_order_q75": 15.0,
                "largest_buy_order_share": 0.2,
                "repeated_quantity_count_share": 0.3,
                "buy_order_gap_cv": 0.5,
                "buy_order_gap_regular_share": 0.7,
                "mid_lift_end": 0.001,
                "ask_price_lift": 0.001,
                "ask_depth_consumed": 0.5,
            }
        )
    rows.append(
        {
            "window_id": base.loc[0, "target_window_id"],
            "trade_amount": 100.0,
            "active_buy_amount": 75.0,
            "active_sell_amount": 25.0,
            "active_buy_share": 0.75,
        }
    )
    result = add_level2_aggregates(base, pd.DataFrame(rows))
    assert np.isclose(result.loc[0, "pulse_combined_buy_share"], 0.8)
    assert result.loc[0, "pulse_buy_share_ge70_count"] == 5
    assert bool(result.loc[0, "target_buy_up_70"])


def test_wilson_interval_contains_observed_rate():
    low, high = wilson_interval(60, 100)
    assert low < 0.60 < high

