# -*- coding: utf-8 -*-
"""汇总正式57个因子的2026年1—5月IC，并比较旧排名与长期排名。"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd


BASE_DIR = Path(__file__).resolve().parent
WORK_DIR = BASE_DIR / ".codex-artifact" / "2026_rank_check"
RUN_DIR = WORK_DIR / "all57_runs"
OUTPUT_DIR = WORK_DIR / "all57_results"

EXCLUDED = {
    "REALIZED_SKEW_1M_20D",
    "LAST_30MIN_RETURN_MA5",
    "PRICE_RESILIENCY_1M_TREND",
    "J06_D19_INTRADAY_CVAR_5PCT",
}

SCRIPT_TO_CANONICAL = {
    "UTD_20D_NEUTRAL": "UTD",
    "RVJN_5M": "RVJN",
    "ILLIQ_SHORTCUT_5M_5D": "ILLIQ_SHORTCUT",
    "WEIGHTED_CLOSE_RATIO_5M": "WEIGHTED_CLOSE_RATIO",
    "VOLUME_SHARE_KURT_10M": "VOLUME_SHARE_KURT",
    "SCC_20D": "SCC",
    "IDEAL_AMPLITUDE_20D": "IDEAL_AMPLITUDE",
    "REALIZED_SKEW_5M_20D": "REALIZED_SKEW_20D",
    "VOLUME_PEAK_PRICE_POS_20D_NEUTRAL": "VOLUME_PEAK_WEIGHTED_PERCENTILE",
    "VOLUME_RIDGE_GAP_SKEW_20D": "VOLUME_RIDGE_INTERVAL_SKEW",
    "M03_AMOUNT_SHARE_ENTROPY_5M": "M03_AMOUNT_SHARE_ENTROPY",
    "M06_UPSIDE_VOL_SHARE_5M_20D": "M06_UPSIDE_VOL_SHARE",
    "M11_NETWORK_CENTER_COMPOSITE_20D": "M11_NETWORK_CENTER_COMPOSITE",
    "M12_WEIGHTED_SKEW_5M": "M12_WEIGHTED_SKEW",
    "M13_MORNING_FOG_20D": "M13_MORNING_FOG",
    "M16_LARGE_UP_JUMP_VOL_5M": "M16_LARGE_UP_JUMP_VOL",
    "M21_CORRECTED_INTRADAY_REVERSAL_20D": "M21_CORRECTED_INTRADAY_REVERSAL",
    "M27_INTRADAY_JUMPINESS_20D": "M27_INTRADAY_JUMPINESS",
    "M31_VOLUME_BURST_FOLLOW_20D": "M31_VOLUME_BURST_FOLLOW",
}

NEW_SOURCES = {
    "一月": RUN_DIR / "january" / "original_frequency_ic.csv",
    "二三月": RUN_DIR / "feb_mar" / "original_frequency_ic.csv",
    "四月": RUN_DIR / "april" / "original_frequency_ic.csv",
    "五月": RUN_DIR / "may" / "original_frequency_ic.csv",
    "六月": RUN_DIR / "june" / "original_frequency_ic.csv",
}

OLD_SOURCES = {
    "一月": BASE_DIR
    / "january_original_minute_results"
    / "final_strict_v3"
    / "original_frequency_ic.csv",
    "二三月": BASE_DIR
    / "february_march_results"
    / "final"
    / "original_frequency_ic.csv",
    "四月": BASE_DIR / "april_factor_results" / "original_frequency_ic.csv",
    "五月": BASE_DIR / "may_factor_results" / "final" / "original_frequency_ic.csv",
    "六月": BASE_DIR / "june_factor_results" / "final" / "original_frequency_ic.csv",
}


def canonical(code: str) -> str:
    return SCRIPT_TO_CANONICAL.get(code, code)


def icir(values: pd.Series) -> float:
    clean = pd.to_numeric(values, errors="coerce").dropna()
    if len(clean) < 2:
        return np.nan
    standard = clean.std(ddof=1)
    if not np.isfinite(standard) or standard == 0:
        return np.nan
    return float(clean.mean() / standard)


def rank_abs(values: pd.Series) -> pd.Series:
    return values.abs().rank(method="min", ascending=False).astype("Int64")


def load_clean_table() -> pd.DataFrame:
    payload = json.loads(
        (WORK_DIR / "current_clean_rows.json").read_text(encoding="utf-8-sig")
    )
    frame = pd.DataFrame(payload["rows"], columns=payload["header"])
    frame = frame.rename(
        columns={
            "因子代码": "factor",
            "因子名称": "factor_name",
            "分类": "category",
            "原始频率": "source_frequency_text",
            "信号/IC频率": "ic_frequency_text",
            "观察窗口": "lookback_text",
            "本次实际计算": "calculation_note",
            "IC与ICIR计算方式": "ic_method_note",
            "来源/原图": "source_note",
            "限制/未回测原因": "limitation_note",
        }
    )
    numeric_columns = {
        "|IC|名次": "old_abs_ic_rank",
        "|ICIR|名次": "old_abs_icir_rank",
        "观测期数": "old_observations",
        "全期IC": "old_ic",
        "全期ICIR": "old_icir",
    }
    for source, target in numeric_columns.items():
        frame[target] = pd.to_numeric(frame[source], errors="raise")
    frame["old_abs_ic_rank"] = frame["old_abs_ic_rank"].astype(int)
    frame["old_abs_icir_rank"] = frame["old_abs_icir_rank"].astype(int)
    frame["old_observations"] = frame["old_observations"].astype(int)
    keep = [
        "factor",
        "factor_name",
        "category",
        "source_frequency_text",
        "ic_frequency_text",
        "lookback_text",
        "old_abs_ic_rank",
        "old_abs_icir_rank",
        "old_observations",
        "old_ic",
        "old_icir",
        "calculation_note",
        "ic_method_note",
        "source_note",
        "limitation_note",
    ]
    result = frame[keep].copy()
    if len(result) != 57 or result["factor"].nunique() != 57:
        raise RuntimeError("精简总表不是57个唯一正式因子")
    return result.sort_values("old_abs_ic_rank").reset_index(drop=True)


def load_ic_files(sources: dict[str, Path], sample: str) -> pd.DataFrame:
    frames = []
    for source_month, path in sources.items():
        frame = pd.read_csv(path)
        frame["script_factor"] = frame["factor"].astype(str)
        frame = frame[~frame["script_factor"].isin(EXCLUDED)].copy()
        frame["factor"] = frame["script_factor"].map(canonical)
        frame["source_month"] = source_month
        frame["source_file"] = str(path.relative_to(BASE_DIR))
        frame["sample"] = sample
        frames.append(frame)
    combined = pd.concat(frames, ignore_index=True)
    for column in ["signal_date", "entry_date", "exit_date"]:
        combined[column] = pd.to_datetime(combined[column], errors="raise")
    combined["return_month"] = combined["exit_date"].dt.to_period("M").astype(str)
    return combined


def raw_summary(frame: pd.DataFrame, prefix: str) -> pd.DataFrame:
    rows = []
    for factor, group in frame.groupby("factor", sort=False):
        rows.append(
            {
                "factor": factor,
                f"{prefix}_observations": int(group["ic"].notna().sum()),
                f"{prefix}_ic": float(group["ic"].mean()),
                f"{prefix}_ic_std": float(group["ic"].std(ddof=1)),
                f"{prefix}_icir": icir(group["ic"]),
                f"{prefix}_min_stock_count": int(group["stock_count"].min()),
                f"{prefix}_avg_stock_count": float(group["stock_count"].mean()),
            }
        )
    result = pd.DataFrame(rows)
    result[f"{prefix}_abs_ic_rank"] = rank_abs(result[f"{prefix}_ic"])
    return result


def monthly_summary(frame: pd.DataFrame, prefix: str) -> pd.DataFrame:
    rows = []
    for factor, group in frame.groupby("factor", sort=False):
        rows.append(
            {
                "factor": factor,
                f"{prefix}_months": int(group["monthly_ic"].notna().sum()),
                f"{prefix}_ic": float(group["monthly_ic"].mean()),
                f"{prefix}_ic_std": float(group["monthly_ic"].std(ddof=1)),
                f"{prefix}_icir": icir(group["monthly_ic"]),
                f"{prefix}_positive_months": int((group["monthly_ic"] > 0).sum()),
                f"{prefix}_negative_months": int((group["monthly_ic"] < 0).sum()),
            }
        )
    result = pd.DataFrame(rows)
    result[f"{prefix}_abs_ic_rank"] = rank_abs(result[f"{prefix}_ic"])
    return result


def month_number(value: str) -> int:
    period = pd.Period(value, freq="M")
    return period.year * 12 + period.month - 1


def historical_five_month_percentile(
    old_monthly_common: pd.DataFrame,
    recent_summary: pd.DataFrame,
    clean: pd.DataFrame,
) -> pd.DataFrame:
    recent_by_factor = recent_summary.set_index("factor")
    old_by_factor = clean.set_index("factor")
    rows = []
    for factor, group in old_monthly_common.groupby("factor", sort=False):
        ordered = group.sort_values("return_month")
        values = ordered[["return_month", "monthly_ic"]].to_records(index=False)
        window_means = []
        for start in range(0, len(values) - 4):
            window = values[start : start + 5]
            numbers = [month_number(str(item[0])) for item in window]
            if not all(numbers[index] == numbers[0] + index for index in range(5)):
                continue
            window_means.append(float(np.mean([float(item[1]) for item in window])))
        if not window_means:
            raise RuntimeError(f"{factor}没有连续5个月历史窗口")
        old_ic = float(old_by_factor.loc[factor, "old_ic"])
        expected_sign = 1.0 if old_ic >= 0 else -1.0
        strength = np.sort(expected_sign * np.asarray(window_means, dtype=np.float64))
        recent_ic = float(recent_by_factor.loc[factor, "recent_ic"])
        recent_strength = expected_sign * recent_ic
        percentile = float(np.mean(strength <= recent_strength))
        if percentile <= 0.10:
            range_label = "历史低位"
        elif percentile <= 0.25:
            range_label = "偏弱"
        elif percentile >= 0.90:
            range_label = "历史高位"
        else:
            range_label = "常见范围"
        rows.append(
            {
                "factor": factor,
                "historical_5m_windows": int(len(strength)),
                "recent_direction_strength": recent_strength,
                "historical_5m_strength_mean": float(strength.mean()),
                "historical_5m_strength_p10": float(np.quantile(strength, 0.10)),
                "historical_5m_strength_median": float(np.quantile(strength, 0.50)),
                "historical_5m_strength_p90": float(np.quantile(strength, 0.90)),
                "recent_historical_percentile": percentile,
                "historical_5m_sign_reversal_rate": float(np.mean(strength <= 0)),
                "historical_range_label": range_label,
            }
        )
    return pd.DataFrame(rows)


def main() -> int:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    clean = load_clean_table()
    official = set(clean["factor"])

    new_raw = load_ic_files(NEW_SOURCES, "2026年1—5月")
    new_factors = set(new_raw["factor"])
    if new_factors != official:
        raise RuntimeError(
            f"2026因子清单不一致，缺少{sorted(official-new_factors)}，多出{sorted(new_factors-official)}"
        )
    new_raw = new_raw[
        new_raw["exit_date"].between("2026-01-01", "2026-05-29")
    ].copy()
    new_raw = new_raw.merge(
        clean[["factor", "factor_name", "category"]],
        on="factor",
        how="left",
        validate="many_to_one",
    )
    new_raw = new_raw[
        [
            "sample",
            "source_month",
            "factor",
            "script_factor",
            "factor_name",
            "category",
            "signal_frequency",
            "return_horizon",
            "signal_date",
            "entry_date",
            "exit_date",
            "return_month",
            "ic",
            "stock_count",
            "source_file",
        ]
    ].sort_values(["factor", "exit_date", "signal_date"])

    expected_counts = {"monthly": 5, "daily": 95, "weekly_tuesday": 19}
    raw_counts = (
        new_raw.groupby(["factor", "signal_frequency"], as_index=False)
        .size()
        .rename(columns={"size": "raw_observations"})
    )
    raw_counts["expected_observations"] = raw_counts["signal_frequency"].map(
        expected_counts
    )
    raw_counts["observation_check"] = np.where(
        raw_counts["raw_observations"] == raw_counts["expected_observations"],
        "完整",
        "少于理论值",
    )
    if len(raw_counts) != 57:
        raise RuntimeError("2026原始IC频率记录不是每个因子一行")
    if (raw_counts["observation_check"] != "完整").any():
        raise RuntimeError(
            "部分因子原始IC数量不完整：\n"
            + raw_counts.loc[
                raw_counts["observation_check"] != "完整"
            ].to_string(index=False)
        )
    if len(new_raw) != 3179:
        raise RuntimeError(f"2026正式原始IC应为3179条，实际{len(new_raw)}条")
    if (new_raw["stock_count"] < 50).any():
        raise RuntimeError("存在股票数低于50的已保留IC")

    recent_monthly = (
        new_raw.groupby(
            ["factor", "factor_name", "category", "return_month"], as_index=False
        )
        .agg(
            monthly_ic=("ic", "mean"),
            raw_observations=("ic", "count"),
            min_stock_count=("stock_count", "min"),
            avg_stock_count=("stock_count", "mean"),
        )
        .sort_values(["factor", "return_month"])
    )
    expected_months = [f"2026-{month:02d}" for month in range(1, 6)]
    month_check = recent_monthly.groupby("factor")["return_month"].agg(
        lambda values: sorted(values.unique())
    )
    incomplete_months = month_check[month_check.apply(lambda values: values != expected_months)]
    if not incomplete_months.empty:
        raise RuntimeError(f"有因子缺少2026月份：\n{incomplete_months}")

    recent_summary = monthly_summary(recent_monthly, "recent").merge(
        raw_summary(new_raw, "recent_raw"), on="factor", validate="one_to_one"
    )
    recent_summary = recent_summary.merge(
        raw_counts[
            [
                "factor",
                "signal_frequency",
                "raw_observations",
                "expected_observations",
                "observation_check",
            ]
        ],
        on="factor",
        validate="one_to_one",
    )

    old_raw = load_ic_files(OLD_SOURCES, "2016—2025原记录")
    old_factors = set(old_raw["factor"])
    if old_factors != official:
        raise RuntimeError(
            f"历史因子清单不一致，缺少{sorted(official-old_factors)}，多出{sorted(old_factors-official)}"
        )
    old_summary = raw_summary(old_raw, "old_recalculated")
    old_check = clean.merge(old_summary, on="factor", validate="one_to_one")
    if not np.allclose(
        old_check["old_ic"], old_check["old_recalculated_ic"], rtol=0, atol=1e-12
    ):
        raise RuntimeError("旧总表IC与历史原始IC重新求均值不一致")
    if not np.allclose(
        old_check["old_icir"],
        old_check["old_recalculated_icir"],
        rtol=0,
        atol=1e-12,
    ):
        raise RuntimeError("旧总表ICIR与历史原始IC重新计算不一致")
    if not np.array_equal(
        old_check["old_observations"].to_numpy(),
        old_check["old_recalculated_observations"].to_numpy(),
    ):
        raise RuntimeError("旧总表观测数与历史原始IC记录数不一致")

    old_monthly = (
        old_raw.groupby(["factor", "return_month"], as_index=False)
        .agg(
            monthly_ic=("ic", "mean"),
            raw_observations=("ic", "count"),
            min_stock_count=("stock_count", "min"),
            avg_stock_count=("stock_count", "mean"),
        )
        .sort_values(["factor", "return_month"])
    )
    common_months = (
        old_monthly.groupby("return_month")["factor"]
        .nunique()
        .loc[lambda values: values == 57]
        .index
    )
    old_common_monthly = old_monthly[
        old_monthly["return_month"].isin(common_months)
    ].copy()
    common_summary = monthly_summary(old_common_monthly, "old_common")

    combined_raw = pd.concat(
        [
            old_raw[
                [
                    "sample",
                    "source_month",
                    "factor",
                    "script_factor",
                    "signal_frequency",
                    "return_horizon",
                    "signal_date",
                    "entry_date",
                    "exit_date",
                    "return_month",
                    "ic",
                    "stock_count",
                    "source_file",
                ]
            ],
            new_raw.drop(columns=["factor_name", "category"]),
        ],
        ignore_index=True,
    )
    updated_summary = raw_summary(combined_raw, "updated")

    history_percentile = historical_five_month_percentile(
        old_common_monthly, recent_summary, clean
    )

    comparison = (
        clean.merge(common_summary, on="factor", validate="one_to_one")
        .merge(recent_summary, on="factor", validate="one_to_one")
        .merge(updated_summary, on="factor", validate="one_to_one")
        .merge(history_percentile, on="factor", validate="one_to_one")
    )
    comparison["recent_rank_change"] = (
        comparison["old_abs_ic_rank"]
        - comparison["recent_abs_ic_rank"].astype(int)
    )
    comparison["updated_rank_change"] = (
        comparison["old_abs_ic_rank"]
        - comparison["updated_abs_ic_rank"].astype(int)
    )
    comparison["old_direction"] = np.where(
        comparison["old_ic"] >= 0, "正向", "负向"
    )
    comparison["recent_direction"] = np.where(
        comparison["recent_ic"] >= 0, "正向", "负向"
    )
    comparison["direction_kept"] = np.where(
        np.sign(comparison["old_ic"]) == np.sign(comparison["recent_ic"]),
        "是",
        "否",
    )
    comparison["direction_adjusted_recent_ic"] = (
        np.sign(comparison["old_ic"]) * comparison["recent_ic"]
    )
    comparison["recent_abs_ic"] = comparison["recent_ic"].abs()
    comparison["updated_abs_ic"] = comparison["updated_ic"].abs()
    comparison = comparison.sort_values("recent_abs_ic_rank").reset_index(drop=True)

    # 与前六名专用程序逐条复核。
    top6_path = WORK_DIR / "results" / "top6_2026_ic_raw.csv"
    top6 = pd.read_csv(top6_path)
    top6["factor"] = top6["factor"].map(canonical)
    for column in ["signal_date", "entry_date", "exit_date"]:
        top6[column] = pd.to_datetime(top6[column], errors="raise")
    official_top6 = set(top6["factor"])
    full_top6 = new_raw[new_raw["factor"].isin(official_top6)].copy()
    keys = ["factor", "signal_date", "entry_date", "exit_date"]
    qa = full_top6.merge(
        top6[keys + ["ic", "stock_count"]],
        on=keys,
        how="outer",
        suffixes=("_full", "_top6"),
        indicator=True,
    )
    if (qa["_merge"] != "both").any():
        raise RuntimeError("完整57因子结果与前六名专用结果的日期记录不一致")
    if not np.allclose(qa["ic_full"], qa["ic_top6"], rtol=0, atol=1e-12):
        raise RuntimeError("完整57因子结果与前六名专用结果的IC不一致")
    if not np.array_equal(
        qa["stock_count_full"].to_numpy(), qa["stock_count_top6"].to_numpy()
    ):
        raise RuntimeError("完整57因子结果与前六名专用结果的股票数不一致")

    quality_checks = pd.DataFrame(
        [
            ["正式因子数", 57, int(new_raw["factor"].nunique()), "通过"],
            ["2026原始IC条数", 3179, int(len(new_raw)), "通过"],
            ["2026因子—月份条数", 285, int(len(recent_monthly)), "通过"],
            ["每个因子月份数", 5, int(recent_monthly.groupby("factor").size().min()), "通过"],
            ["最低股票数", 50, int(new_raw["stock_count"].min()), "通过"],
            ["历史共同月份数", 85, int(len(common_months)), "通过" if len(common_months) == 85 else "需说明"],
            ["前六名逐条复核", 120, int(len(qa)), "通过"],
            ["旧IC逐项复核", 57, int(len(old_check)), "通过"],
        ],
        columns=["check_item", "expected", "actual", "status"],
    )

    new_raw.to_csv(
        OUTPUT_DIR / "all57_2026_ic_raw.csv", index=False, encoding="utf-8-sig"
    )
    recent_monthly.to_csv(
        OUTPUT_DIR / "all57_2026_monthly_ic.csv", index=False, encoding="utf-8-sig"
    )
    comparison.to_csv(
        OUTPUT_DIR / "all57_ranking_comparison.csv",
        index=False,
        encoding="utf-8-sig",
    )
    raw_counts.to_csv(
        OUTPUT_DIR / "all57_frequency_counts.csv", index=False, encoding="utf-8-sig"
    )
    quality_checks.to_csv(
        OUTPUT_DIR / "all57_quality_checks.csv", index=False, encoding="utf-8-sig"
    )
    combined_raw.to_csv(
        OUTPUT_DIR / "all57_combined_raw_ic.csv", index=False, encoding="utf-8-sig"
    )

    manifest = {
        "period": "2026-01-01至2026-05-29，按收益实现月统计",
        "official_factors": 57,
        "monthly_factors": 24,
        "daily_factors": 32,
        "weekly_tuesday_factors": 1,
        "raw_ic_rows_2026": int(len(new_raw)),
        "factor_month_rows_2026": int(len(recent_monthly)),
        "old_common_months": int(len(common_months)),
        "old_common_start": str(min(common_months)),
        "old_common_end": str(max(common_months)),
        "old_common_explanation": "57因子共同历史月份为85；2016-01缺少J06_D01_STRUCTURED_REVERSAL_21D_10M，因此前六因子的86个月基准不适用于全部57因子。",
        "historical_5m_windows": 72,
        "ranking_rule": "各因子先按退出月份汇总IC，再以2026年1—5月平均IC绝对值降序排名",
        "icir_rule": "五个月IC均值除以五个月IC样本标准差，ddof=1",
        "updated_long_term_rule": "原频率历史IC记录追加2026原频率IC后重新计算均值、样本标准差和57因子内部排名",
        "normalization": "不做额外归一化；Spearman IC只使用横截面排序",
        "sources": {
            "clean_table": str(
                (
                    BASE_DIR
                    / "outputs"
                    / "half_year_clean_factor_table"
                    / "因子日历2026上半年因子总表_精简版.xlsx"
                ).resolve()
            ),
            "new_ic_files": [str(path.resolve()) for path in NEW_SOURCES.values()],
            "old_ic_files": [str(path.resolve()) for path in OLD_SOURCES.values()],
            "minute_cache_base": str((WORK_DIR / "cache" / "base").resolve()),
            "minute_cache_april": str((WORK_DIR / "cache" / "april").resolve()),
            "minute_cache_may": str((WORK_DIR / "cache" / "may").resolve()),
            "minute_cache_june": str((WORK_DIR / "cache" / "june").resolve()),
            "index": "Tushare index_daily：000985.CSI",
            "membership": "Tushare index_weight：000906.SH、000852.SH，按2026年月末快照",
        },
        "exclusions": sorted(EXCLUDED),
        "limitations": [
            "2026年只有5个月，月频ICIR样本很短，只适合作近期观察。",
            "日频因子的5日未来收益相互重叠，95条原频率IC并非95个完全独立样本；主排名已先按月汇总。",
            "分钟数据截至2026-05-29，未计算2026年6月。",
        ],
    }
    (OUTPUT_DIR / "methodology.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    display_columns = [
        "recent_abs_ic_rank",
        "factor_name",
        "factor",
        "signal_frequency",
        "recent_ic",
        "recent_icir",
        "old_abs_ic_rank",
        "recent_rank_change",
        "direction_kept",
        "recent_historical_percentile",
        "updated_abs_ic_rank",
    ]
    print(comparison[display_columns].head(20).to_string(index=False))
    print("\n方向反转数：", int((comparison["direction_kept"] == "否").sum()))
    print("历史低位数：", int((comparison["historical_range_label"] == "历史低位").sum()))
    print("长期排名变化数：", int((comparison["updated_rank_change"] != 0).sum()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
