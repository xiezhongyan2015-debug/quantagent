# -*- coding: utf-8 -*-
"""检查并合并分钟因子补算的六组结果，可选附带2026年近期检查。"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

import pandas as pd


REQUIRED_CSV_FILES = (
    "factor_status.csv",
    "original_frequency_ic_summary.csv",
    "original_frequency_backtest_summary.csv",
    "original_frequency_yearly_backtest.csv",
    "original_frequency_ic.csv",
    "original_frequency_rebalance_returns.csv",
)

RECENT_2026_FILES = (
    "minute_completion_2026_ic_raw.csv",
    "minute_completion_2026_monthly_ic.csv",
    "minute_completion_2026_summary.csv",
    "minute_completion_2026_quality_checks.csv",
)

RECENT_2026_REQUIRED_COLUMNS = {
    "minute_completion_2026_ic_raw.csv": {
        "factor",
        "factor_name",
        "formula_mode",
        "source_group",
        "signal_frequency",
        "return_horizon",
        "signal_date",
        "entry_date",
        "exit_date",
        "return_month",
        "ic",
        "stock_count",
    },
    "minute_completion_2026_monthly_ic.csv": {
        "factor",
        "factor_name",
        "formula_mode",
        "source_group",
        "signal_frequency",
        "return_month",
        "monthly_ic",
        "raw_observations",
        "min_stock_count",
        "avg_stock_count",
    },
    "minute_completion_2026_summary.csv": {
        "factor",
        "factor_name",
        "formula_mode",
        "source_group",
        "signal_frequency",
        "recent_months",
        "recent_ic",
        "recent_ic_std",
        "recent_icir",
        "raw_observations",
        "min_stock_count",
        "avg_stock_count",
        "first_exit_date",
        "last_exit_date",
        "recent_abs_ic",
        "recent_abs_ic_rank",
    },
    "minute_completion_2026_quality_checks.csv": {
        "check_item",
        "expected",
        "actual",
        "status",
    },
}

RECENT_2026_MONTHS = tuple(f"2026-{month:02d}" for month in range(1, 6))


@dataclass(frozen=True)
class GroupSpec:
    argument_name: str
    display_name: str
    formula_mode: str
    codes: tuple[str, ...]


GROUP_SPECS = (
    GroupSpec(
        "early_strict_dir",
        "早期4个严格",
        "strict",
        (
            "M25_HIGH_VOLUME_REALIZED_SKEW",
            "A06_DAZZLING_VOLATILITY_20D",
            "A10_P_SHAPED_VOLUME_DISTRIBUTION",
            "MAY25_TOP_RETURN_PRICE_RESONANCE",
        ),
    ),
    GroupSpec(
        "julaug_strict_dir",
        "七八月10个严格",
        "strict",
        (
            "F02_D12_LONE_GOOSE",
            "J07_D01_FUZZY_QTY_RATIO",
            "J07_D07_EXTREME_REVERSAL",
            "J07_D10_CLOSE_AMOUNT_SHARE",
            "J07_D25_AMT_MAX",
            "J08_D01_ADJ_FUZZY_SPREAD",
            "J08_D05_DISASTER_REBUILD",
            "J08_D10_AMOUNT_VARIANCE",
            "J08_D17_LARGE_VOL_PV_CORR",
            "J08_D26_IDEAL_AMPLITUDE",
        ),
    ),
    GroupSpec(
        "sepdec_strict_dir",
        "九至十二月10个严格",
        "strict",
        (
            "J09_D14_SMART_MONEY_VWAP_RATIO",
            "J09_D23_VOLUME_PEAK_MINUTE_RETURN",
            "J10_D22_VALLEY_REL_PRICE",
            "J11_D01_VOLUME_PEAK_COUNT",
            "J11_D14_VOLUME_TIDE_PRICE_RATE",
            "J11_D22_RIDGE_RELATIVE_VWAP",
            "J11_D28_RIDGE_MINUTE_COUNT",
            "J12_D13_FUZZY_AMOUNT_CORR",
            "J12_D20_PEAK_GAP_KURT",
            "J12_D22_VALLEY_PRICE_PERCENTILE",
        ),
    ),
    GroupSpec(
        "early_sep_trial_dir",
        "早期及九月9个试算",
        "trial",
        (
            "M15_VSA_LOW_CLOSE_GAP",
            "A22_INTRADAY_SNR",
            "MAY13_INTRADAY_ABNORMAL_VOLUME",
            "J06_D02_JUMP_INTENSITY",
            "J06_D07_HIGH_FREQUENCY_RESILIENCY",
            "J06_D20_CO_MIN",
            "J09_D07_BRAVE_PEAK",
            "J09_D11_NAIVE_ACTIVE_SHARE",
            "J09_D19_SKEWED_MOMENTUM_RANGE",
        ),
    ),
    GroupSpec(
        "julaug_trial_dir",
        "七八月4个试算",
        "trial",
        (
            "J07_D05_SIG_JUMP_ASYM",
            "J07_D08_AMOUNT_AUTOCORR",
            "J08_D08_ABSRET_AMOUNT_CORR",
            "J08_D25_AMT_MIN",
        ),
    ),
    GroupSpec(
        "octdec_trial_dir",
        "十至十二月14个试算",
        "trial",
        (
            "J10_D10_T_ACTIVE_SHARE",
            "J10_D18_JUMP_DEGREE_CENTRALITY",
            "J10_D20_CONSISTENT_TRADING",
            "J10_D25_AVG_POS_JUMP_RET",
            "J10_D26_CUM_JUMP_ABS_RET",
            "J11_D04_ADJ_ABSRET_LAG_AMOUNT_CORR",
            "J11_D20_CONSISTENT_BUYING",
            "J11_D21_COJUMP_RELATIVE_MOMENTUM",
            "J11_D25_POS_JUMP_RETURN_STD",
            "J11_D29_JUMP_NETWORK_DEGREE",
            "J12_D04_ADJ_LAG_ABSRET_AMOUNT_CORR",
            "J12_D11_CONSISTENT_SELL",
            "J12_D18_COJUMP_NONJUMP_MOM",
            "J12_D25_TURNOVER_POS_JUMP",
        ),
    ),
)


UNAVAILABLE_FACTORS = (
    {
        "factor": "J06_D28_HIGH_VOLUME_AVG_TRADE_AMOUNT",
        "name": "成交量最高时刻笔均成交金额",
        "category": "缺少成交笔数或主动买卖方向",
        "missing_fields": "分钟成交笔数",
        "reason": "分钟库没有成交笔数，不能计算分钟笔均成交金额。",
    },
    {
        "factor": "J07_D28_HIGHVOL_SELL_ATD",
        "name": "成交量最高时刻-主卖笔均成交金额",
        "category": "缺少成交笔数或主动买卖方向",
        "missing_fields": "主动卖出金额、主动卖出笔数",
        "reason": "分钟库没有主动卖出方向和成交笔数。",
    },
    {
        "factor": "J08_D28_PV_DIVERGENCE_ATD",
        "name": "量价背离时刻笔均成交金额",
        "category": "缺少成交笔数或主动买卖方向",
        "missing_fields": "分钟成交笔数",
        "reason": "可识别量价背离分钟，但没有成交笔数，无法计算笔均金额。",
    },
    {
        "factor": "J08_D30_ACTIVE_BUY_SINGULARITY",
        "name": "主买成交特异性",
        "category": "缺少成交笔数或主动买卖方向",
        "missing_fields": "主动买入金额或逐笔买卖方向",
        "reason": "分钟库没有主动买入方向。",
    },
    {
        "factor": "J09_D02_DPIN_BASE",
        "name": "动态知情交易概率",
        "category": "缺少成交笔数或主动买卖方向",
        "missing_fields": "逐笔成交、买卖方向",
        "reason": "DPIN原式需要逐笔成交和买卖方向。",
    },
    {
        "factor": "J09_D20_ACTIVE_BUY_SHARE",
        "name": "主动买入占比",
        "category": "缺少成交笔数或主动买卖方向",
        "missing_fields": "主动买入金额或逐笔买卖方向",
        "reason": "分钟库没有主动买入方向。",
    },
    {
        "factor": "J09_D28_SIPHON_EFFECT",
        "name": "虹吸效应",
        "category": "缺少成交笔数或主动买卖方向",
        "missing_fields": "逐笔成交笔数、买卖方向",
        "reason": "原式需要逐笔成交大小和买卖方向。",
    },
    {
        "factor": "J09_D30_PV_DIVERGENCE_SELL_ATD",
        "name": "量价背离卖出平均成交金额",
        "category": "缺少成交笔数或主动买卖方向",
        "missing_fields": "主动卖出金额、主动卖出笔数",
        "reason": "分钟库没有主动卖出方向和成交笔数。",
    },
    {
        "factor": "J10_D01_SMALL_DPIN",
        "name": "小单的动态知情交易概率",
        "category": "缺少成交笔数或主动买卖方向",
        "missing_fields": "逐笔成交、订单大小、买卖方向",
        "reason": "不能从分钟OHLCVA识别小单及其买卖方向。",
    },
    {
        "factor": "J10_D02_AVG_TRADE_VOL_DIFF_STD",
        "name": "每笔成交量差分标准差",
        "category": "缺少成交笔数或主动买卖方向",
        "missing_fields": "分钟成交笔数",
        "reason": "没有成交笔数，不能计算每笔成交量。",
    },
    {
        "factor": "J10_D13_MAIN_TRADE_SENTIMENT",
        "name": "主力交易情绪",
        "category": "缺少成交笔数或主动买卖方向",
        "missing_fields": "大单识别、主动买卖方向",
        "reason": "不能从分钟OHLCVA识别主力订单及买卖方向。",
    },
    {
        "factor": "J11_D02_LARGE_ORDER_DPIN",
        "name": "大单动态知情交易概率",
        "category": "缺少成交笔数或主动买卖方向",
        "missing_fields": "逐笔成交、订单大小、买卖方向",
        "reason": "不能从分钟OHLCVA识别大单及其买卖方向。",
    },
    {
        "factor": "J12_D30_DOWN_ATD",
        "name": "下跌时刻笔均成交金额",
        "category": "缺少成交笔数或主动买卖方向",
        "missing_fields": "分钟成交笔数",
        "reason": "能识别下跌分钟，但没有成交笔数，无法计算笔均金额。",
    },
    {
        "factor": "J12_D31_PRICE_IMPACT_BIAS",
        "name": "价格冲击偏差",
        "category": "缺少成交笔数或主动买卖方向",
        "missing_fields": "主动买卖方向、明确公式",
        "reason": "缺主动买卖方向；图片公式分子和分母相同，按图恒为1，定义也需确认。",
    },
    {
        "factor": "J07_D26_LIQ_PREMIUM_EQUAL",
        "name": "流动性溢价因子-等权",
        "category": "缺少盘口",
        "missing_fields": "买卖报价、中间价、盘口深度",
        "reason": "分钟库没有盘口报价。",
    },
    {
        "factor": "J08_D02_LIQ_PREMIUM_WEIGHTED",
        "name": "流动性溢价因子-加权",
        "category": "缺少盘口",
        "missing_fields": "买卖报价、中间价、盘口深度",
        "reason": "分钟库没有盘口报价。",
    },
    {
        "factor": "J10_D17_MIDPRICE_CHANGE",
        "name": "中间价变化率",
        "category": "缺少盘口",
        "missing_fields": "买一价、卖一价",
        "reason": "分钟库没有买一价和卖一价，不能构造中间价。",
    },
    {
        "factor": "J12_D17_MIDPRICE_CHANGE_SKEW",
        "name": "中间价变化率偏度",
        "category": "缺少盘口",
        "missing_fields": "买一价、卖一价",
        "reason": "分钟库没有买一价和卖一价，不能构造中间价变化率。",
    },
    {
        "factor": "J08_D20_OPEN_CLOSE_VOLUME_SHARE",
        "name": "成交量占比",
        "category": "缺少集合竞价独立数据",
        "missing_fields": "开盘集合竞价成交量",
        "reason": "09:30集合竞价量已并入09:31分钟，现有文件无法还原独立竞价量。",
    },
    {
        "factor": "J07_D12_TREND_FUNDS_PRICE",
        "name": "趋势资金相对均价因子",
        "category": "缺少行业资料",
        "missing_fields": "历史行业分类",
        "reason": "原式要求行业中性处理，当前数据没有历史行业分类。",
    },
    {
        "factor": "J08_D12_NET_SUPPORT_VOLUME",
        "name": "净支撑成交量因子",
        "category": "缺少行业资料",
        "missing_fields": "历史行业分类",
        "reason": "原式要求行业中性处理，当前数据没有历史行业分类。",
    },
    {
        "factor": "J09_D09_TREND_FUND_NET_SUPPORT",
        "name": "趋势资金净支撑",
        "category": "缺少行业资料",
        "missing_fields": "历史行业分类",
        "reason": "原式要求行业中性处理，当前数据没有历史行业分类。",
    },
    {
        "factor": "J10_D12_EXTREME_FOLLOW_RATIO",
        "name": "极端跟随行为比值因子",
        "category": "缺少行业资料",
        "missing_fields": "历史行业分类",
        "reason": "原式要求行业中性处理，当前数据没有历史行业分类。",
    },
    {
        "factor": "J11_D12_EXTREME_FOLLOWING_CORR",
        "name": "极端趋势跟随相关性",
        "category": "缺少行业资料",
        "missing_fields": "历史行业分类",
        "reason": "原式要求行业中性处理，当前数据没有历史行业分类。",
    },
    {
        "factor": "J10_D05_HF_IDIO_VOL",
        "name": "高频特异波动率",
        "category": "缺少完整风格收益",
        "missing_fields": "图片指定风格因子收益序列",
        "reason": "原式回归需要完整风格收益，本地分钟和日线表没有对应序列。",
    },
    {
        "factor": "J11_D05_NONLINEAR_HF_VOLATILITY",
        "name": "非线性高频波动率",
        "category": "缺少完整风格收益",
        "missing_fields": "图片指定风格因子收益序列",
        "reason": "原式回归需要完整风格收益，本地分钟和日线表没有对应序列。",
    },
    {
        "factor": "J07_D17_CPV",
        "name": "价量相关性因子",
        "category": "核心指标定义不完整",
        "missing_fields": "PV_corr_avg、PV_corr_std、PV_corr_trend的基础定义和窗口",
        "reason": "图片只给三个未定义子指标的组合式，无法唯一复原。",
    },
)


class CompletionInputError(ValueError):
    """六组输入不完整或结果代码不符合要求。"""


def _validate_constants() -> None:
    all_codes = [code for group in GROUP_SPECS for code in group.codes]
    strict = [code for group in GROUP_SPECS if group.formula_mode == "strict" for code in group.codes]
    trial = [code for group in GROUP_SPECS if group.formula_mode == "trial" for code in group.codes]
    if len(all_codes) != 51 or len(set(all_codes)) != 51:
        raise RuntimeError("新增因子常量必须恰好是51个唯一代码。")
    if len(strict) != 24 or len(trial) != 27:
        raise RuntimeError("新增因子常量必须是24个严格和27个试算。")
    if all_codes.count("J12_D04_ADJ_LAG_ABSRET_AMOUNT_CORR") != 1:
        raise RuntimeError("J12_D04必须只属于一个结果组。")
    unavailable_codes = [row["factor"] for row in UNAVAILABLE_FACTORS]
    if len(unavailable_codes) != 27 or len(set(unavailable_codes)) != 27:
        raise RuntimeError("当前不能计算清单必须恰好是27个唯一代码。")
    overlap = sorted(set(all_codes) & set(unavailable_codes))
    if overlap:
        raise RuntimeError(f"可计算与不能计算清单重复：{', '.join(overlap)}")


_validate_constants()


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--early-strict-dir", type=Path, required=True)
    parser.add_argument("--julaug-strict-dir", type=Path, required=True)
    parser.add_argument("--sepdec-strict-dir", type=Path, required=True)
    parser.add_argument("--early-sep-trial-dir", type=Path, required=True)
    parser.add_argument("--julaug-trial-dir", type=Path, required=True)
    parser.add_argument("--octdec-trial-dir", type=Path, required=True)
    parser.add_argument(
        "--recent-2026-dir",
        type=Path,
        help="可选；run_2026_minute_completion_check.py生成的四个近期结果文件所在目录。",
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args(argv)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_result_csv(path: Path) -> pd.DataFrame:
    try:
        frame = pd.read_csv(path)
    except Exception as exc:
        raise CompletionInputError(f"无法读取CSV：{path}；{exc}") from exc
    if "factor" not in frame.columns:
        raise CompletionInputError(f"CSV缺少factor列：{path}")
    frame = frame.copy()
    frame["factor"] = frame["factor"].astype("string").str.strip()
    return frame


def validate_and_load_groups(
    args: argparse.Namespace,
) -> tuple[dict[str, dict[str, pd.DataFrame]], list[dict[str, object]]]:
    """先完成全部检查；任何错误都不会留下部分合并结果。"""

    loaded: dict[str, dict[str, pd.DataFrame]] = {}
    audits: list[dict[str, object]] = []
    errors: list[str] = []
    for group in GROUP_SPECS:
        directory = Path(getattr(args, group.argument_name))
        group_frames: dict[str, pd.DataFrame] = {}
        file_audit: dict[str, object] = {}
        if not directory.is_dir():
            errors.append(f"{group.display_name}：目录不存在：{directory}")
            continue
        for filename in REQUIRED_CSV_FILES:
            path = directory / filename
            if not path.is_file():
                errors.append(f"{group.display_name}：缺文件：{path}")
                continue
            try:
                frame = _read_result_csv(path)
            except CompletionInputError as exc:
                errors.append(f"{group.display_name}：{exc}")
                continue
            present = set(frame["factor"].dropna().astype(str))
            expected = set(group.codes)
            missing = sorted(expected - present)
            extra = sorted(present - expected)
            if missing:
                errors.append(
                    f"{group.display_name}：{filename}缺代码：{', '.join(missing)}"
                )
            selected = frame[frame["factor"].isin(group.codes)].copy()
            if filename == "factor_status.csv":
                duplicates = selected.loc[
                    selected["factor"].duplicated(keep=False), "factor"
                ].dropna().astype(str).unique().tolist()
                if duplicates:
                    errors.append(
                        f"{group.display_name}：factor_status代码重复："
                        + ", ".join(sorted(duplicates))
                    )
            selected["formula_mode"] = group.formula_mode
            selected["formula_label"] = (
                "严格" if group.formula_mode == "strict" else "试算"
            )
            selected["source_group"] = group.display_name
            group_frames[filename] = selected
            file_audit[filename] = {
                "path": str(path.resolve()),
                "sha256": _sha256(path),
                "rows_total": len(frame),
                "rows_selected": len(selected),
                "present_expected_codes": len(expected & present),
                "ignored_extra_codes": extra,
            }
        if len(group_frames) == len(REQUIRED_CSV_FILES):
            loaded[group.argument_name] = group_frames
            audits.append(
                {
                    "source_group": group.display_name,
                    "formula_mode": group.formula_mode,
                    "directory": str(directory.resolve()),
                    "expected_code_count": len(group.codes),
                    "expected_codes": list(group.codes),
                    "files": file_audit,
                }
            )
    if errors:
        raise CompletionInputError(
            "六组结果尚未完整，未生成合并文件：\n- " + "\n- ".join(errors)
        )
    return loaded, audits


def _atomic_write_csv(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    frame.to_csv(temporary, index=False, encoding="utf-8-sig")
    os.replace(temporary, path)


def _atomic_write_json(value: object, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with open(temporary, "w", encoding="utf-8") as file:
        json.dump(value, file, ensure_ascii=False, indent=2, default=str)
    os.replace(temporary, path)


def _atomic_copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    shutil.copyfile(source, temporary)
    os.replace(temporary, destination)


def validate_recent_2026_outputs(
    directory: Path,
) -> tuple[dict[str, Path], dict[str, object]]:
    directory = Path(directory)
    errors: list[str] = []
    frames: dict[str, pd.DataFrame] = {}
    paths: dict[str, Path] = {}
    if not directory.is_dir():
        raise CompletionInputError(f"2026近期结果目录不存在：{directory}")

    expected_codes = {
        code for group in GROUP_SPECS for code in group.codes
    }
    for filename in RECENT_2026_FILES:
        path = directory / filename
        paths[filename] = path
        if not path.is_file():
            errors.append(f"2026近期结果缺文件：{path}")
            continue
        try:
            frame = pd.read_csv(path)
        except Exception as exc:
            errors.append(f"无法读取2026近期结果{path}：{exc}")
            continue
        missing_columns = sorted(
            RECENT_2026_REQUIRED_COLUMNS[filename] - set(frame.columns)
        )
        if missing_columns:
            errors.append(
                f"2026近期结果{filename}缺少列：{', '.join(missing_columns)}"
            )
            continue
        frames[filename] = frame

    for filename in RECENT_2026_FILES[:3]:
        frame = frames.get(filename)
        if frame is None:
            continue
        codes = set(frame["factor"].dropna().astype(str).str.strip())
        if codes != expected_codes:
            errors.append(
                f"2026近期结果{filename}的因子代码不是预期51项；"
                f"缺少={sorted(expected_codes - codes)}；"
                f"多出={sorted(codes - expected_codes)}"
            )

    raw = frames.get("minute_completion_2026_ic_raw.csv")
    if raw is not None:
        exit_dates = pd.to_datetime(raw["exit_date"], errors="coerce")
        if exit_dates.isna().any() or not exit_dates.between(
            "2026-01-01", "2026-05-29", inclusive="both"
        ).all():
            errors.append("2026原始IC的退出日必须在2026-01-01至2026-05-29内。")

    monthly = frames.get("minute_completion_2026_monthly_ic.csv")
    if monthly is not None:
        month_sets = monthly.groupby("factor")["return_month"].agg(
            lambda values: tuple(sorted(values.astype(str).unique()))
        )
        incomplete = sorted(
            str(code)
            for code, months in month_sets.items()
            if months != RECENT_2026_MONTHS
        )
        if len(monthly) != 255 or incomplete:
            errors.append(
                "2026逐月IC必须是51个因子各5个月，共255行；"
                f"当前行数={len(monthly)}，月份不全={incomplete}"
            )

    summary = frames.get("minute_completion_2026_summary.csv")
    if summary is not None:
        modes = summary["formula_mode"].astype(str).value_counts().to_dict()
        if len(summary) != 51 or summary["factor"].nunique() != 51:
            errors.append("2026近期汇总必须恰好是51行、51个唯一因子。")
        if modes != {"trial": 27, "strict": 24}:
            errors.append(
                f"2026近期汇总必须是24个严格、27个试算，当前为{modes}。"
            )

    quality = frames.get("minute_completion_2026_quality_checks.csv")
    if quality is not None and not quality["status"].astype(str).eq("通过").all():
        failed = quality.loc[
            ~quality["status"].astype(str).eq("通过"), "check_item"
        ].astype(str).tolist()
        errors.append("2026近期质量检查未全部通过：" + ", ".join(failed))

    if errors:
        raise CompletionInputError(
            "2026近期结果不完整，未生成合并文件：\n- " + "\n- ".join(errors)
        )

    audit = {
        "included": True,
        "source_directory": str(directory.resolve()),
        "factor_count": 51,
        "strict_count": 24,
        "trial_count": 27,
        "months": list(RECENT_2026_MONTHS),
        "files": {
            filename: {
                "source_path": str(path.resolve()),
                "sha256": _sha256(path),
                "rows": len(frames[filename]),
                "unique_factor_codes": (
                    int(frames[filename]["factor"].nunique())
                    if "factor" in frames[filename].columns
                    else None
                ),
            }
            for filename, path in paths.items()
        },
    }
    return paths, audit


def assemble_frames(
    loaded: dict[str, dict[str, pd.DataFrame]],
) -> dict[str, pd.DataFrame]:
    expected_codes = [code for group in GROUP_SPECS for code in group.codes]
    order = {code: index for index, code in enumerate(expected_codes)}
    outputs: dict[str, pd.DataFrame] = {}
    for filename in REQUIRED_CSV_FILES:
        pieces = [loaded[group.argument_name][filename] for group in GROUP_SPECS]
        frame = pd.concat(pieces, ignore_index=True, sort=False)
        present = set(frame["factor"].dropna().astype(str))
        if present != set(expected_codes):
            missing = sorted(set(expected_codes) - present)
            extra = sorted(present - set(expected_codes))
            raise CompletionInputError(
                f"{filename}合并后代码不等于51个预期代码；"
                f"缺少={missing}；多出={extra}"
            )
        frame["_factor_order"] = frame["factor"].map(order)
        frame["_row_order"] = range(len(frame))
        frame = frame.sort_values(
            ["_factor_order", "_row_order"], kind="stable"
        ).drop(columns=["_factor_order", "_row_order"])
        preferred = [
            column
            for column in [
                "factor",
                "formula_mode",
                "formula_label",
                "source_group",
            ]
            if column in frame.columns
        ]
        remaining = [column for column in frame.columns if column not in preferred]
        outputs[filename] = frame.loc[:, [*preferred, *remaining]]

    status = outputs["factor_status.csv"]
    if len(status) != 51 or status["factor"].nunique() != 51:
        raise CompletionInputError("factor_status合并后必须恰好51行、51个唯一代码。")
    counts = status["formula_mode"].value_counts().to_dict()
    if counts != {"trial": 27, "strict": 24}:
        raise CompletionInputError(
            f"factor_status分类数量错误，应为24严格+27试算，实际为{counts}。"
        )
    j12 = status[
        status["factor"] == "J12_D04_ADJ_LAG_ABSRET_AMOUNT_CORR"
    ]
    if len(j12) != 1 or j12.iloc[0]["formula_mode"] != "trial":
        raise CompletionInputError("J12_D04必须只出现一次且归入试算。")
    return outputs


def build_group_audit_frame(audits: list[dict[str, object]]) -> pd.DataFrame:
    rows = []
    for audit in audits:
        ignored = sorted(
            {
                code
                for file_info in audit["files"].values()
                for code in file_info["ignored_extra_codes"]
            }
        )
        rows.append(
            {
                "source_group": audit["source_group"],
                "formula_mode": audit["formula_mode"],
                "directory": audit["directory"],
                "expected_code_count": audit["expected_code_count"],
                "validated_file_count": len(audit["files"]),
                "ignored_extra_code_count": len(ignored),
                "ignored_extra_codes": "|".join(ignored),
            }
        )
    return pd.DataFrame(rows)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    loaded, audits = validate_and_load_groups(args)
    outputs = assemble_frames(loaded)
    recent_2026_paths: dict[str, Path] = {}
    recent_2026_audit: dict[str, object] = {"included": False}
    if args.recent_2026_dir is not None:
        recent_2026_paths, recent_2026_audit = validate_recent_2026_outputs(
            args.recent_2026_dir
        )

    unavailable = pd.DataFrame(UNAVAILABLE_FACTORS)
    if len(unavailable) != 27 or unavailable["factor"].nunique() != 27:
        raise RuntimeError("当前不能计算清单必须恰好27行。")
    group_audit = build_group_audit_frame(audits)
    output_dir = Path(args.output_dir)
    for filename, frame in outputs.items():
        _atomic_write_csv(frame, output_dir / filename)
    for filename, source in recent_2026_paths.items():
        _atomic_copy(source, output_dir / filename)
        recent_2026_audit["files"][filename]["copied_path"] = str(
            (output_dir / filename).resolve()
        )
    _atomic_write_csv(
        unavailable, output_dir / "currently_unavailable_factors.csv"
    )
    _atomic_write_csv(group_audit, output_dir / "source_group_audit.csv")

    status = outputs["factor_status.csv"]
    methodology = {
        "purpose": "合并分钟因子补算结果，不重新计算因子，也不修改工作簿",
        "created_at": pd.Timestamp.now().isoformat(),
        "validation": {
            "unique_new_factor_codes": int(status["factor"].nunique()),
            "strict_count": int(status["formula_mode"].eq("strict").sum()),
            "trial_count": int(status["formula_mode"].eq("trial").sum()),
            "currently_unavailable_count": len(unavailable),
            "j12_d04_group_count": sum(
                "J12_D04_ADJ_LAG_ABSRET_AMOUNT_CORR" in group.codes
                for group in GROUP_SPECS
            ),
            "j12_d04_factor_status_rows": int(
                status["factor"].eq("J12_D04_ADJ_LAG_ABSRET_AMOUNT_CORR").sum()
            ),
            "required_csv_files": list(REQUIRED_CSV_FILES),
        },
        "group_sources": audits,
        "merged_outputs": {
            filename: {
                "path": str((output_dir / filename).resolve()),
                "rows": len(frame),
                "unique_factor_codes": int(frame["factor"].nunique()),
            }
            for filename, frame in outputs.items()
        },
        "unavailable_output": {
            "path": str(
                (output_dir / "currently_unavailable_factors.csv").resolve()
            ),
            "rows": len(unavailable),
        },
        "recent_2026": recent_2026_audit,
        "extra_code_rule": (
            "输入目录可以包含其他旧因子，但程序会在控制台和source_group_audit.csv中明确列出；"
            "缺少本组任何文件或代码时立即失败。"
        ),
    }
    _atomic_write_json(methodology, output_dir / "methodology.json")

    for row in group_audit.itertuples(index=False):
        if row.ignored_extra_code_count:
            print(
                f"{row.source_group}包含{row.ignored_extra_code_count}个非本组代码，"
                "已明确记录且未纳入。",
                flush=True,
            )
    print(
        "合并完成：51个唯一新增因子，24个严格、27个试算；"
        "另列27个当前不能计算因子。",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
