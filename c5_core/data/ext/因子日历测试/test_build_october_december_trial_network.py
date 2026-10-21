# -*- coding: utf-8 -*-

import numpy as np
import pandas as pd
import json
from pathlib import Path

import build_october_december_trial_network as target


def _directions() -> tuple[np.ndarray, list[str]]:
    values = np.zeros((6, 4), dtype=np.int8)
    values[[1, 4], 0] = 1
    values[[0, 3], 1] = 1
    values[[1, 4], 2] = -1
    values[[0, 3], 3] = -1
    return values, ["sh600000", "sh600001", "sz000001", "sz000002"]


def test_network_counts_same_direction_events_within_one_day() -> None:
    directions, codes = _directions()
    edges, nodes = target.compute_network_snapshot(
        directions,
        codes,
        "2026-05-29",
        prune_fraction=0.0,
    )
    pairs = {
        (row.source_code, row.target_code): (
            row.cojump_count,
            row.source_jump_count,
            row.association,
        )
        for row in edges.itertuples(index=False)
    }
    assert set(pairs) == {
        ("sh600000", "sh600001"),
        ("sh600001", "sh600000"),
        ("sz000001", "sz000002"),
        ("sz000002", "sz000001"),
    }
    assert all(value[:2] == (2, 2) for value in pairs.values())
    assert all(value[2] == 1.0 for value in pairs.values())
    assert nodes["edge_count"].eq(1).all()
    assert nodes["association_sum"].eq(1.0).all()


def test_network_pruning_keeps_exact_top_half_and_saves_edge_count() -> None:
    directions, codes = _directions()
    edges, nodes = target.compute_network_snapshot(
        directions,
        codes,
        "2026-05-29",
        prune_fraction=0.5,
    )
    assert len(edges) == 2
    assert edges[["source_code", "target_code"]].values.tolist() == [
        ["sh600000", "sh600001"],
        ["sh600001", "sh600000"],
    ]
    node_map = nodes.set_index("code")
    assert node_map.loc["sh600000", "edge_count"] == 1
    assert node_map.loc["sh600001", "association_sum"] == 1.0
    assert node_map.loc["sz000001", "edge_count"] == 0
    assert node_map.loc["sz000002", "association_sum"] == 0.0
    assert node_map["candidate_edge_count"].eq(1).all()


def test_month_end_positions_require_full_history() -> None:
    dates = pd.bdate_range("2025-01-02", "2026-05-29")
    positions = target.month_end_positions(dates, window=252)
    assert len(positions) > 0
    assert positions.min() >= 251
    selected = dates[positions]
    assert all(
        position == len(dates) - 1
        or dates[position].month != dates[position + 1].month
        for position in positions
    )
    assert selected[-1] == dates[-1]


def test_network_parser_accepts_thread_limit() -> None:
    args = target.parse_args(["--threads", "2"])
    assert args.threads == 2


def test_compact_snapshot_matches_legacy_edges_value_by_value() -> None:
    directions, codes = _directions()
    daily_return = np.array([0.10, 0.20, 0.30, 0.40], dtype=np.float32)
    negative_jump20 = np.array(
        [-0.01, -0.02, -0.03, -0.04], dtype=np.float32
    )
    nonjump20 = np.array([0.01, 0.03, 0.02, 0.04], dtype=np.float32)
    edges, legacy_nodes = target.compute_network_snapshot(
        directions,
        codes,
        "2026-05-29",
        prune_fraction=0.5,
    )
    positive = target.sparse.csr_matrix((directions == 1).astype(np.int32))
    negative = target.sparse.csr_matrix((directions == -1).astype(np.int32))
    compact = target.compute_compact_network_snapshot_from_events(
        positive,
        negative,
        codes,
        "2026-05-29",
        prune_fraction=0.5,
        network_window=len(directions),
        daily_return=daily_return,
        negative_jump20=negative_jump20,
        nonjump20=nonjump20,
    )

    pd.testing.assert_frame_equal(
        compact[
            [
                "code",
                "source_jump_count",
                "candidate_edge_count",
                "edge_count",
                "association_sum",
            ]
        ].reset_index(drop=True),
        legacy_nodes[
            [
                "code",
                "source_jump_count",
                "candidate_edge_count",
                "edge_count",
                "association_sum",
            ]
        ].reset_index(drop=True),
    )
    code_map = {code: index for index, code in enumerate(codes)}
    rows = edges["source_code"].map(code_map).to_numpy(dtype=np.int32)
    columns = edges["target_code"].map(code_map).to_numpy(dtype=np.int32)
    weights = edges["association"].to_numpy(dtype=np.float64)
    for column, values in (
        ("peer_daily_return", daily_return),
        ("peer_negative_jump20", negative_jump20),
        ("peer_nonjump20", nonjump20),
    ):
        expected = target._weighted_peer_values(
            rows, columns, weights, values, len(codes)
        )
        np.testing.assert_array_equal(
            compact[column].to_numpy(dtype=np.float32), expected
        )


def test_compact_main_writes_only_monthly_node_files(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(target, "BASE_DIR", tmp_path)
    daily_dir = tmp_path / "daily"
    base_dir = tmp_path / "base"
    output_root = tmp_path / "network"
    temp_dir = tmp_path / "temp"
    daily_dir.mkdir()
    base_dir.mkdir()
    dates = pd.bdate_range("2025-01-02", periods=25)
    for stock_index, code in enumerate(["a", "b", "c"]):
        direction = np.zeros(len(dates), dtype=np.int8)
        direction[np.arange(stock_index, len(dates), 4)] = (
            1 if stock_index < 2 else -1
        )
        pd.DataFrame(
            {
                "date": dates,
                "code": code,
                "jiang_oomen_jump_direction_daily_trial": direction,
                "negative_jump_return_daily_trial": -0.001
                * (np.arange(len(dates)) + stock_index),
                "nonjump_return_daily_trial": 0.002
                * (np.arange(len(dates)) + stock_index + 1),
            }
        ).to_parquet(daily_dir / f"{code}.parquet", index=False)
        pd.DataFrame(
            {
                "date": dates,
                "code": code,
                "close": 10.0
                + stock_index
                + np.arange(len(dates), dtype=float) * 0.01,
            }
        ).to_parquet(base_dir / f"{code}.parquet", index=False)

    arguments = [
        "--daily-dir",
        str(daily_dir),
        "--base-cache-glob",
        str(base_dir / "*.parquet"),
        "--output-root",
        str(output_root),
        "--temp-dir",
        str(temp_dir),
        "--memory-limit",
        "512MB",
        "--threads",
        "1",
        "--start",
        str(dates[0].date()),
        "--end",
        str(dates[-1].date()),
        "--window",
        "20",
        "--output-mode",
        "compact",
    ]
    assert target.main(arguments) == 0
    node_files = sorted((output_root / "nodes").glob("*.parquet"))
    assert node_files
    assert not (output_root / "edges").exists()
    assert set(target.COMPACT_NODE_COLUMNS).issubset(
        pd.read_parquet(node_files[0]).columns
    )
    assert set(target.COMPACT_RESUME_COLUMNS).issubset(
        pd.read_parquet(node_files[0]).columns
    )
    manifest = json.loads(
        (output_root / "network_manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["output_mode"] == "compact"
    assert manifest["edge_files_written"] == 0
    assert manifest["node_rows"] == len(node_files) * 3

    assert target.main(arguments) == 0
    reused = json.loads(
        (output_root / "network_manifest.json").read_text(encoding="utf-8")
    )
    assert {row["status"] for row in reused["snapshots"]} == {"reused"}

    changed = pd.read_parquet(daily_dir / "a.parquet")
    changed.loc[0, "negative_jump_return_daily_trial"] -= 0.1
    changed.to_parquet(daily_dir / "a.parquet", index=False)
    assert target.main(arguments) == 0
    rebuilt = json.loads(
        (output_root / "network_manifest.json").read_text(encoding="utf-8")
    )
    assert {row["status"] for row in rebuilt["snapshots"]} == {"completed"}
    marker = pd.read_parquet(node_files[0], columns=target.COMPACT_RESUME_COLUMNS)
    assert marker["daily_source_fingerprint"].eq(
        rebuilt["source_fingerprint"]
    ).all()
