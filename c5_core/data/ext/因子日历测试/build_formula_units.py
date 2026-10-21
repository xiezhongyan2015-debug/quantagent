from __future__ import annotations

import csv
from collections import Counter, defaultdict
from pathlib import Path

import pandas as pd


BASE = Path(__file__).resolve().parent
RESULT_DIR = BASE / "historical_calendar_backtest_results"
MAPPING = RESULT_DIR / "factor_mapping.csv"
FORMULA_UNITS = RESULT_DIR / "formula_units.csv"
REPORT = RESULT_DIR / "mapping_consistency_report.md"


# 工作簿里的简写代码与实际结果文件中的代码不同。以下14项使用实际结果代码。
RESULT_CODE_ALIASES = {
    "IDEAL_AMPLITUDE": "IDEAL_AMPLITUDE_20D",
    "ILLIQ_SHORTCUT": "ILLIQ_SHORTCUT_5M_5D",
    "M03_AMOUNT_SHARE_ENTROPY": "M03_AMOUNT_SHARE_ENTROPY_5M",
    "M06_UPSIDE_VOL_SHARE": "M06_UPSIDE_VOL_SHARE_5M_20D",
    "M12_WEIGHTED_SKEW": "M12_WEIGHTED_SKEW_5M",
    "M13_MORNING_FOG": "M13_MORNING_FOG_20D",
    "M21_CORRECTED_INTRADAY_REVERSAL": "M21_CORRECTED_INTRADAY_REVERSAL_20D",
    "M27_INTRADAY_JUMPINESS": "M27_INTRADAY_JUMPINESS_20D",
    "PRICE_RESILIENCY": "PRICE_RESILIENCY_1M_TREND",
    "REALIZED_SKEW_20D": "REALIZED_SKEW_5M_20D",
    "RVJN": "RVJN_5M",
    "UTD": "UTD_20D_NEUTRAL",
    "VOLUME_SHARE_KURT": "VOLUME_SHARE_KURT_10M",
    "WEIGHTED_CLOSE_RATIO": "WEIGHTED_CLOSE_RATIO_5M",
}


FREQUENCIES = ("daily", "monthly", "weekly_tuesday", "every_5_days")
KIND_PRIORITY = ("existing", "cached", "standard", "risk", "unavailable")


def normalize_frequency(value: object) -> str:
    text = "" if value is None else str(value).strip().lower()
    if "每5个交易日" in text or "每 5 个交易日" in text or "every_5" in text:
        return "every_5_days"
    if "周二" in text or "每周" in text or "weekly" in text:
        return "weekly_tuesday"
    if "月末" in text or "月度" in text or "monthly" in text:
        return "monthly"
    return "daily"


def read_available_results() -> set[str]:
    codes: set[str] = set()
    for path in BASE.glob("**/factor_status.csv"):
        try:
            frame = pd.read_csv(path)
        except Exception:
            continue
        if "factor" not in frame.columns:
            continue
        if "calculated" in frame.columns:
            calculated = frame["calculated"].astype(str).str.lower()
            frame = frame[calculated.isin({"true", "1", "yes"})]
        codes.update(frame["factor"].dropna().astype(str))
    if len(codes) != 157:
        raise RuntimeError(f"应识别157个已有结果代码，实际识别{len(codes)}个。")
    return codes


def available_ref(values: list[str], result_codes: set[str]) -> str:
    for raw in values:
        if not raw:
            continue
        candidate = RESULT_CODE_ALIASES.get(raw, raw)
        if candidate in result_codes:
            return candidate
    return ""


def group_mode(values: list[str]) -> str:
    if "trial" in values:
        return "trial"
    if "unavailable" in values:
        return "unavailable"
    return "strict"


def preferred_kind(values: list[str], result_ref: str) -> str:
    if result_ref:
        return "existing"
    present = set(values)
    for kind in KIND_PRIORITY:
        if kind in present:
            return kind
    return "risk"


def representative_name(group: pd.DataFrame) -> str:
    counts = Counter(group["name"].astype(str))
    max_count = max(counts.values())
    candidates = {name for name, count in counts.items() if count == max_count}
    for name in group["name"].astype(str):
        if name in candidates:
            return name
    raise RuntimeError("无法选择代表名称。")


def unique_join(values: pd.Series) -> str:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values.fillna("").astype(str):
        if value and value not in seen:
            seen.add(value)
            ordered.append(value)
    return " | ".join(ordered)


def make_units(frame: pd.DataFrame, result_codes: set[str]) -> pd.DataFrame:
    frame = frame.copy()
    frame["normalized_frequency"] = frame["signal_frequency"].map(normalize_frequency)
    if not set(frame["normalized_frequency"]).issubset(FREQUENCIES):
        raise RuntimeError("存在未统一的回测频率。")

    rows: list[dict[str, object]] = []
    grouped = frame.groupby(["formula_key", "normalized_frequency"], sort=True)
    for (formula_key, frequency), group in grouped:
        refs = group["implementation_ref"].fillna("").astype(str).tolist()
        result_ref = available_ref(refs, result_codes)
        modes = group["mode"].fillna("strict").astype(str).tolist()
        kinds = group["implementation_kind"].fillna("risk").astype(str).tolist()
        rows.append(
            {
                "unit_id": f"{formula_key}__{frequency}",
                "formula_key": formula_key,
                "normalized_frequency": frequency,
                "representative_name": representative_name(group),
                "record_count": len(group),
                "all_names": unique_join(group["name"]),
                "mode": group_mode(modes),
                "existing_ref": result_ref,
                "preferred_kind": preferred_kind(kinds, result_ref),
                "years": unique_join(group["year"]),
                "source_frequencies": unique_join(group["signal_frequency"]),
            }
        )
    return pd.DataFrame(rows)


def make_report(frame: pd.DataFrame, units: pd.DataFrame, result_codes: set[str]) -> str:
    work = frame.copy()
    work["normalized_frequency"] = work["signal_frequency"].map(normalize_frequency)
    differing: list[tuple[str, pd.DataFrame, list[str]]] = []
    for formula_key, group in work.groupby("formula_key", sort=True):
        differences: list[str] = []
        if group["normalized_frequency"].nunique(dropna=False) > 1:
            differences.append("频率")
        if group["mode"].nunique(dropna=False) > 1:
            differences.append("模式")
        if group["implementation_kind"].nunique(dropna=False) > 1:
            differences.append("实现分类")
        if group["category"].nunique(dropna=False) > 1:
            differences.append("数据分类")
        if differences:
            differing.append((formula_key, group, differences))

    if len(differing) != 52:
        raise RuntimeError(f"应有52个同公式差异组，实际识别{len(differing)}个。")

    frequency_diff = sum("频率" in differences for _, _, differences in differing)
    mode_diff = sum("模式" in differences for _, _, differences in differing)
    kind_diff = sum("实现分类" in differences for _, _, differences in differing)
    category_diff = sum("数据分类" in differences for _, _, differences in differing)
    valid_refs = units["existing_ref"].astype(str).isin(result_codes).sum()

    lines = [
        "# 历史因子映射一致性检查",
        "",
        "## 汇总",
        "",
        f"- 原始普通分钟记录：{len(frame)} 条。",
        f"- 公式编号：{frame['formula_key'].nunique()} 个。",
        f"- 按公式编号和统一频率拆分后：{len(units)} 个计算单元。",
        f"- 可直接读取现有结果的计算单元：{valid_refs} 个；现有结果代码集合共 {len(result_codes)} 个。",
        f"- 存在频率或分类差异的同公式组：{len(differing)} 个。其中频率差异 {frequency_diff} 个、模式差异 {mode_diff} 个、实现分类差异 {kind_diff} 个、数据分类差异 {category_diff} 个。",
        "",
        "## 处理规则",
        "",
        "- 统一频率只使用 `daily`、`monthly`、`weekly_tuesday`、`every_5_days`。",
        "- 同一公式编号若频率不同，拆成不同计算单元，不合并回测结果。",
        "- 同组只要出现 `trial`，整个计算单元记为 `trial`；没有试算但有不可用记录时记为 `unavailable`。",
        "- `existing_ref` 只填写确实出现在157个结果代码中的代码；14个工作簿简写代码已换成实际结果代码。",
        "- 首选实现顺序为：已有结果、现有缓存、普通计算、风险或市场比较计算、暂不可用。",
        "",
        "## 52个差异组",
        "",
        "| 公式编号 | 差异项 | 记录数 | 名称 | 统一频率 | 原频率 | 模式 | 实现分类 | 可用结果代码 |",
        "|---|---|---:|---|---|---|---|---|---|",
    ]
    for formula_key, group, differences in differing:
        refs = group["implementation_ref"].fillna("").astype(str).tolist()
        ref = available_ref(refs, result_codes)
        values = {
            "names": unique_join(group["name"]),
            "frequencies": unique_join(group["normalized_frequency"]),
            "source_frequencies": unique_join(group["signal_frequency"]),
            "modes": unique_join(group["mode"]),
            "kinds": unique_join(group["implementation_kind"]),
        }
        lines.append(
            f"| {formula_key} | {'、'.join(differences)} | {len(group)} | "
            f"{values['names']} | {values['frequencies']} | {values['source_frequencies']} | "
            f"{values['modes']} | {values['kinds']} | {ref} |"
        )
    lines.extend(
        [
            "",
            "## 14个结果代码更名",
            "",
            "| 工作簿代码 | 实际结果代码 |",
            "|---|---|",
        ]
    )
    for source, target in RESULT_CODE_ALIASES.items():
        lines.append(f"| {source} | {target} |")
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    frame = pd.read_csv(MAPPING, encoding="utf-8-sig", dtype=str).fillna("")
    result_codes = read_available_results()
    units = make_units(frame, result_codes)
    units.to_csv(FORMULA_UNITS, index=False, encoding="utf-8-sig", quoting=csv.QUOTE_MINIMAL)
    REPORT.write_text(make_report(frame, units, result_codes), encoding="utf-8")
    print(
        f"records={len(frame)} formulas={frame['formula_key'].nunique()} "
        f"units={len(units)} available_refs={(units['existing_ref'] != '').sum()}"
    )


if __name__ == "__main__":
    main()
