# -*- coding: utf-8 -*-
"""合并两份 Level2 方向复核表，并应用已经确认的方向更正。"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Mapping

import pandas as pd

import run_level2_short_backtests as short_backtest


DIRECTION_MAP = {
    "high": "high",
    "high/正": "high",
    "正": "high",
    "正向": "high",
    "low": "low",
    "low/负": "low",
    "负": "low",
    "负向": "low",
}

CORRECTIONS: Mapping[str, Mapping[str, str]] = {
    "F02_D03_SMALL_ORDER_LAG_RANKCORR": {
        "selected_direction": "high",
        "direction_basis": "原报告给出的该因子 IC 为正，因子值较高对应更高的后续收益。",
        "direction_confidence": "原报告支持",
        "evidence": "按开源证券原报告和实际实现复核，改为高值做多。",
        "conflict_note": "已按原报告更正，不再按散户追涨杀跌的经济含义反向推断。",
        "correction_note": "人工确认更正：high。",
    },
    "HF0110__daily": {
        "selected_direction": "high",
        "direction_basis": "原报告",
        "direction_confidence": "原报告支持",
        "evidence": "开源证券原报告给出的 IC 均值为 +3.09%，高值按正向处理。",
        "conflict_note": "已由原报告的正 IC 消除原先方向疑问。",
        "correction_note": "人工确认更正：high；原报告 IC 均值 +3.09%。",
    },
    "HF0108__daily": {
        "selected_direction": "high",
        "direction_basis": "原报告",
        "direction_confidence": "原报告支持",
        "evidence": "原报告给出的 IC 均值为 +5.13%，高值按正向处理。",
        "source_url_or_file": "https://bigdata-s3.wmcloud.com/researchreport/2022-06/e55eef41a43f49f1d9e85aa0df5d4c86.pdf",
        "conflict_note": "已由原报告的正 IC 消除原先方向疑问。",
        "correction_note": "人工确认更正：high；原报告 IC 均值 +5.13%。",
    },
    "HF0176__daily": {
        "selected_direction": "low",
        "direction_basis": "实际实现复核",
        "direction_confidence": "实现与原文共同支持",
        "evidence": "与 J12_D19_SELL_REBOUND_DEVIATION 的值序列完全相同；当前保存值是原始 LCP 的相反数，因此低值做多。",
        "conflict_note": "原文正向指原始 LCP；当前实现取相反数，所以回测方向必须为 low。价格口径混用风险仍保留。",
        "correction_note": "人工确认更正：low；与 J12_D19 完全相同。",
    },
    "HF0103__daily": {
        "direction_confidence": "仍冲突",
        "conflict_note": "当前实现把原文正向的大单残差与负向的小单残差直接平均，原文没有定义这个合成值的统一方向。",
        "correction_note": "保留 high 作为暂定方向，但明确标为仍冲突。",
    },
    "F02_D22_TOXIC_LIQUIDITY": {
        "selected_direction": "high",
        "direction_basis": "原报告",
        "direction_confidence": "原报告支持",
        "evidence": "原报告明确撤单越多、流动性越低，市场给出的未来收益补偿越高。",
        "source_url_or_file": "https://www.fxbaogao.com/detail/4118416",
        "conflict_note": "原报告已给出高值正向的收益补偿关系，不再标为仍冲突。",
        "correction_note": "人工确认更正：high；原报告支持。",
    },
}


def _normalize_direction(value: object) -> str:
    text = str(value).strip().lower()
    if text not in DIRECTION_MAP:
        raise ValueError(f"无法识别方向：{value!r}")
    return DIRECTION_MAP[text]


def merge_decisions(
    front: pd.DataFrame,
    back: pd.DataFrame,
    factor_status: pd.DataFrame,
) -> pd.DataFrame:
    first = front.copy()
    second = back.copy()
    first["direction_review_part"] = "front72"
    second["direction_review_part"] = "second72"
    decisions = pd.concat([first, second], ignore_index=True, sort=False)
    decisions["factor_id"] = decisions["factor_id"].astype("string").str.strip()
    duplicate = decisions["factor_id"].duplicated(keep=False)
    if duplicate.any():
        sample = decisions.loc[duplicate, "factor_id"].drop_duplicates().tolist()
        raise ValueError(f"两份方向表有重复 factor_id：{sample[:5]}")
    decisions["selected_direction"] = decisions["selected_direction"].map(
        _normalize_direction
    )
    if "correction_note" not in decisions.columns:
        decisions["correction_note"] = ""

    missing_corrections = sorted(set(CORRECTIONS) - set(decisions["factor_id"]))
    if missing_corrections:
        raise ValueError(f"更正项不在方向表中：{missing_corrections}")
    for factor_id, changes in CORRECTIONS.items():
        mask = decisions["factor_id"].eq(factor_id)
        for column, value in changes.items():
            if column not in decisions.columns:
                decisions[column] = ""
            decisions.loc[mask, column] = value

    status = factor_status.copy()
    status["factor_id"] = status["factor_id"].astype("string").str.strip()
    if "status" in status.columns:
        status = status[status["status"].eq("available")].copy()
    available_ids = set(status["factor_id"])
    decision_ids = set(decisions["factor_id"])
    missing = sorted(available_ids - decision_ids)
    extra = sorted(decision_ids - available_ids)
    if missing or extra:
        raise ValueError(f"方向表与可用因子不一致；缺少={missing[:5]}，多出={extra[:5]}")
    if len(decisions) != 144:
        raise ValueError(f"方向表应有 144 项，实际 {len(decisions)} 项")

    status_quality = status.set_index("factor_id")["implementation_quality"]
    if "implementation_quality" not in decisions.columns:
        decisions["implementation_quality"] = pd.NA
    decisions["implementation_quality"] = decisions["implementation_quality"].fillna(
        decisions["factor_id"].map(status_quality)
    )
    if decisions["implementation_quality"].isna().any():
        raise ValueError("有因子缺少 implementation_quality")

    status_frequency = status.set_index("factor_id")["frequency"]
    expected_frequency = decisions["factor_id"].map(status_frequency)
    actual_frequency = decisions["frequency"].map(short_backtest.normalize_frequency)
    if not actual_frequency.eq(expected_frequency).all():
        raise ValueError("方向表与 factor_status.csv 的 frequency 不一致")
    decisions["frequency"] = actual_frequency
    if not decisions["selected_direction"].isin({"high", "low"}).all():
        raise ValueError("方向标准化失败")

    preferred = [
        "factor_id",
        "name",
        "frequency",
        "selected_direction",
        "direction_basis",
        "direction_confidence",
        "evidence",
        "source_url_or_file",
        "implementation_quality",
        "conflict_note",
        "known_issue",
        "correction_note",
        "direction_review_part",
    ]
    ordered = [column for column in preferred if column in decisions.columns]
    ordered += [column for column in decisions.columns if column not in ordered]
    return decisions[ordered].sort_values("factor_id").reset_index(drop=True)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--front", type=Path, required=True)
    parser.add_argument("--back", type=Path, required=True)
    parser.add_argument("--factor-status", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    decisions = merge_decisions(
        pd.read_csv(args.front),
        pd.read_csv(args.back),
        pd.read_csv(args.factor_status),
    )
    short_backtest._atomic_write_csv(decisions, args.output)
    print(
        f"已合并 {len(decisions)} 个可用因子；"
        f"high={decisions['selected_direction'].eq('high').sum()}，"
        f"low={decisions['selected_direction'].eq('low').sum()}；"
        f"结果在 {args.output.resolve()}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
