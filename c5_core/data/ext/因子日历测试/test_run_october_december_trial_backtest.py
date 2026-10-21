from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

import run_january_minute_backtest as january
import build_october_december_trial_network as network_builder
import run_october_december_trial_backtest as runner


def test_rolling_valid_helpers_skip_missing_observations() -> None:
    values = np.array([[1.0], [2.0], [np.nan], [4.0]], dtype=np.float64)
    sums, means = runner.rolling_valid_sum_mean(values, 3)
    assert np.isnan(sums[:3]).all()
    np.testing.assert_allclose(sums[3, 0], 7.0)
    np.testing.assert_allclose(means[3, 0], 7.0 / 3.0)

    weighted_mean, weighted_std = runner.rolling_valid_weighted_mean_std(
        values, 3, 1.0
    )
    weights = np.array([0.25, 0.5, 1.0])
    observations = np.array([1.0, 2.0, 4.0])
    expected_mean = np.average(observations, weights=weights)
    expected_std = np.sqrt(
        np.average((observations - expected_mean) ** 2, weights=weights)
    )
    np.testing.assert_allclose(weighted_mean[3, 0], expected_mean)
    np.testing.assert_allclose(weighted_std[3, 0], expected_std)


def _network_inputs(snapshot: pd.Timestamp) -> tuple[pd.DataFrame, pd.DataFrame]:
    nodes = pd.DataFrame(
        {
            "snapshot_date": [snapshot] * 3,
            "code": ["a", "b", "c"],
            "edge_count": [2, 1, 3],
            "association_sum": [0.7, 0.2, 1.1],
        }
    )
    edges = pd.DataFrame(
        {
            "snapshot_date": [snapshot] * 4,
            "source_code": ["a", "a", "b", "c"],
            "target_code": ["b", "c", "c", "a"],
            "association": [0.2, 0.3, 0.5, 0.4],
        }
    )
    return nodes, edges


def test_network_factor_values_and_node_only_case() -> None:
    dates = pd.DatetimeIndex(["2025-01-31"])
    stocks = ["a", "b", "c"]
    nodes, edges = _network_inputs(dates[0])
    membership = np.ones((1, 3), dtype=bool)
    daily_return = np.array([[0.1, 0.2, 0.4]], dtype=np.float32)
    own_return20 = np.array([[0.05, 0.10, 0.20]], dtype=np.float32)
    negative_jump20 = np.array([[-0.02, -0.03, -0.08]], dtype=np.float32)
    nonjump20 = np.array([[0.08, 0.12, 0.16]], dtype=np.float32)

    factors = runner.build_network_factor_matrices(
        nodes,
        edges,
        dates,
        stocks,
        membership,
        daily_return,
        own_return20,
        negative_jump20,
        nonjump20,
        min_stocks=2,
    )
    np.testing.assert_allclose(
        factors["J10_D18_JUMP_DEGREE_CENTRALITY"][0], [2, 1, 3]
    )
    np.testing.assert_allclose(
        factors["J11_D29_JUMP_NETWORK_DEGREE"][0], [0.7, 0.2, 1.1]
    )
    peer_return = np.array([(0.2 * 0.2 + 0.3 * 0.4) / 0.5, 0.4, 0.1])
    design = np.column_stack([np.ones(3), own_return20[0]])
    expected = peer_return - design @ np.linalg.lstsq(
        design, peer_return, rcond=None
    )[0]
    np.testing.assert_allclose(
        factors["J11_D21_COJUMP_RELATIVE_MOMENTUM"][0], expected, atol=1e-7
    )

    node_only = runner.build_network_factor_matrices(
        nodes,
        edges.iloc[:0],
        dates,
        stocks,
        membership,
        daily_return,
        own_return20,
        negative_jump20,
        nonjump20,
        min_stocks=2,
    )
    np.testing.assert_allclose(
        node_only["J10_D18_JUMP_DEGREE_CENTRALITY"][0], [2, 1, 3]
    )
    assert np.isnan(node_only["J11_D21_COJUMP_RELATIVE_MOMENTUM"]).all()


def test_network_edges_can_be_read_one_snapshot_at_a_time(tmp_path: Path) -> None:
    dates = pd.DatetimeIndex(["2025-01-31"])
    stocks = ["a", "b", "c"]
    nodes, edges = _network_inputs(dates[0])
    edge_path = tmp_path / "20250131.parquet"
    edges.to_parquet(edge_path, index=False)
    membership = np.ones((1, 3), dtype=bool)
    daily_return = np.array([[0.1, 0.2, 0.4]], dtype=np.float32)
    own_return20 = np.array([[0.05, 0.10, 0.20]], dtype=np.float32)
    negative_jump20 = np.array([[-0.02, -0.03, -0.08]], dtype=np.float32)
    nonjump20 = np.array([[0.08, 0.12, 0.16]], dtype=np.float32)

    from_frame = runner.build_network_factor_matrices(
        nodes,
        edges,
        dates,
        stocks,
        membership,
        daily_return,
        own_return20,
        negative_jump20,
        nonjump20,
        min_stocks=2,
    )
    from_files = runner.build_network_factor_matrices(
        nodes,
        [edge_path],
        dates,
        stocks,
        membership,
        daily_return,
        own_return20,
        negative_jump20,
        nonjump20,
        min_stocks=2,
    )

    assert from_files.keys() == from_frame.keys()
    for code in from_frame:
        np.testing.assert_allclose(
            from_files[code], from_frame[code], equal_nan=True, atol=1e-7
        )


def test_compact_network_nodes_reproduce_legacy_factor_values() -> None:
    dates = pd.DatetimeIndex(["2025-01-31"])
    stocks = ["a", "b", "c"]
    nodes, edges = _network_inputs(dates[0])
    membership = np.ones((1, 3), dtype=bool)
    daily_return = np.array([[0.1, 0.2, 0.4]], dtype=np.float32)
    own_return20 = np.array([[0.05, 0.10, 0.20]], dtype=np.float32)
    negative_jump20 = np.array([[-0.02, -0.03, -0.08]], dtype=np.float32)
    nonjump20 = np.array([[0.08, 0.12, 0.16]], dtype=np.float32)
    code_map = {code: index for index, code in enumerate(stocks)}
    rows = edges["source_code"].map(code_map).to_numpy(dtype=np.int32)
    columns = edges["target_code"].map(code_map).to_numpy(dtype=np.int32)
    weights = edges["association"].to_numpy(dtype=np.float64)
    compact = nodes.copy()
    for column, values in (
        ("peer_daily_return", daily_return[0]),
        ("peer_negative_jump20", negative_jump20[0]),
        ("peer_nonjump20", nonjump20[0]),
    ):
        compact[column] = network_builder._weighted_peer_values(
            rows, columns, weights, values, len(stocks)
        )

    legacy = runner.build_network_factor_matrices(
        nodes,
        edges,
        dates,
        stocks,
        membership,
        daily_return,
        own_return20,
        negative_jump20,
        nonjump20,
        min_stocks=2,
    )
    restored = runner.build_network_factor_matrices(
        compact,
        [],
        dates,
        stocks,
        membership,
        daily_return,
        own_return20,
        negative_jump20,
        nonjump20,
        min_stocks=2,
    )
    assert restored.keys() == legacy.keys()
    for code in legacy:
        np.testing.assert_array_equal(restored[code], legacy[code])


def test_full_factor_builder_produces_all_fourteen_trial_factors(tmp_path: Path) -> None:
    dates = pd.bdate_range("2025-01-02", periods=30)
    stocks = ["a", "b", "c"]
    rows: list[dict[str, object]] = []
    for day_index, date in enumerate(dates):
        for stock_index, code in enumerate(stocks):
            close = 10.0 + day_index * (0.01 + stock_index * 0.002)
            rows.append(
                {
                    "date": date,
                    "code": code,
                    "open": close - 0.01,
                    "close": close,
                    "volume": 1_000.0 + stock_index,
                    "amount": 10_000.0 + stock_index,
                    "float_shares": 100_000.0,
                    "qfq_ratio": 1.0,
                    "vwap_outside_range_1pct": 0.0,
                    "j10_d10_t_active_share_daily_trial": 0.4 + stock_index * 0.01,
                    "consistent_trading_share_daily_trial": 0.2,
                    "consistent_buying_share_daily_trial": 0.1,
                    "consistent_selling_share_daily_trial": 0.05,
                    "j11_d04_adj_absret_lag_amount_corr_daily_trial": 0.3,
                    "jiang_oomen_js_daily_trial": 0.0,
                    "positive_jump_return_daily_trial": (
                        0.01 if day_index % 3 == 0 else 0.0
                    ),
                    "negative_jump_return_daily_trial": (
                        -0.01 if day_index % 4 == 0 else 0.0
                    ),
                    "nonjump_return_daily_trial": 0.002 + stock_index * 0.001,
                    "adj_lag_absret_amount_corr_daily": -0.2,
                }
            )
    panel = pd.DataFrame(rows)
    nodes, edges = _network_inputs(dates[-1])
    date_map = {date: index for index, date in enumerate(dates)}
    stock_map = {code: index for index, code in enumerate(stocks)}
    membership = np.ones((len(dates), len(stocks)), dtype=bool)
    settings = january.Settings(
        cache_dir=tmp_path,
        output_dir=tmp_path,
        qlib_dir=tmp_path,
        start=str(dates[0].date()),
        backtest_start=str(dates[0].date()),
        end=str(dates[-1].date()),
        min_stocks=2,
    )

    factors, arrays = runner.build_factor_matrices(
        panel,
        nodes,
        edges,
        dates,
        stocks,
        date_map,
        stock_map,
        membership,
        settings,
    )
    assert set(factors) == set(runner.FACTOR_META)
    assert len(factors) == 14
    assert all(values.shape == membership.shape for values in factors.values())
    np.testing.assert_allclose(
        factors["J12_D04_ADJ_LAG_ABSRET_AMOUNT_CORR"][-1], -0.2
    )
    assert arrays["raw_close"].shape == membership.shape

    status = runner.build_status_frame(factors)
    assert len(status) == 14
    assert status["calculated"].all()
    assert status["formula_mode"].eq("trial").all()
    assert status["strict_status"].eq("trial_formula").all()
