# -*- coding: utf-8 -*-
"""检查276个历史因子计算单元是否有可执行方法。"""

from __future__ import annotations

import argparse
import importlib
from pathlib import Path

import pandas as pd

import historical_cached_factors as cached
import historical_existing_duckdb_factors as existing
import historical_risk_factors as risk
import historical_standard_factors as standard


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_UNITS = BASE_DIR / "historical_calendar_backtest_results" / "formula_units.csv"
DEFAULT_OUTPUT = (
    BASE_DIR / "historical_calendar_backtest_results" / "formula_support_audit.csv"
)

FORCED_UNAVAILABLE = {
    "HF0041": "严格计算需统一尾部残差定义；2024版本还依赖FF3。",
    "HF0111": "现有缓存不能还原应交易但成交量为零的日期。",
    "HF0130": "目录没有给出可统一复现的自然月市场尾部风险口径。",
    "HF0224": "严格计算缺市场、规模、价值、反转和流动性因子。",
    "HF0260": "严格计算缺多因子回归得到的特异收益率。",
}


def _names(row: pd.Series) -> list[str]:
    values = [str(row["representative_name"])]
    values.extend(str(row.get("all_names", "")).split(" | "))
    return list(dict.fromkeys(value.strip() for value in values if value.strip()))


def find_method(row: pd.Series) -> tuple[str, str, str]:
    """返回计算方法、登记名称和说明。"""

    if str(row.get("mode", "")) == "unavailable" or str(
        row.get("preferred_kind", "")
    ) == "unavailable":
        return "unavailable", "", "缺少普通补充资料。"
    formula_key = str(row.get("formula_key", ""))
    if formula_key in FORCED_UNAVAILABLE:
        return "unavailable", "", FORCED_UNAVAILABLE[formula_key]
    raw_ref = str(row.get("existing_ref", "")).strip()
    if raw_ref and raw_ref.lower() != "nan":
        code = existing.resolve_result_code(raw_ref)
        if code in existing.CODE_TO_GROUP:
            return "existing_duckdb", code, existing.CODE_TO_GROUP[code]
    for name in _names(row):
        try:
            metadata = standard.get_factor_metadata(name)
            return "standard", name, str(metadata["note"])
        except (KeyError, ValueError):
            pass
    for name in _names(row):
        try:
            spec = risk.get_factor_spec(name)
            if spec.status != "unsupported":
                return "risk", name, spec.note
        except (KeyError, ValueError):
            pass
    for name in _names(row):
        try:
            metadata = cached.get_factor_metadata(name)
            return "cached", name, str(metadata["note"])
        except (KeyError, ValueError):
            pass
    for module_name, method_name in (
        ("historical_additional_factors", "additional"),
        ("historical_missing_minute_factors", "missing_minute"),
    ):
        try:
            module = importlib.import_module(module_name)
        except ModuleNotFoundError:
            continue
        for name in _names(row):
            try:
                metadata = module.get_factor_metadata(name)
                if str(metadata.get("status", "strict")) == "unsupported":
                    continue
                return method_name, name, str(metadata.get("note", ""))
            except (KeyError, ValueError):
                pass
    reasons = []
    for name in _names(row):
        for item, reason in cached.UNSUPPORTED.items():
            if standard.normalize_factor_name(name) == standard.normalize_factor_name(item):
                reasons.append(reason)
        try:
            spec = risk.get_factor_spec(name)
            if spec.status == "unsupported":
                reasons.append(spec.note)
        except (KeyError, ValueError):
            pass
    return "unresolved", "", "；".join(dict.fromkeys(reasons)) or "尚未登记计算方法。"


def build_audit(units: pd.DataFrame) -> pd.DataFrame:
    methods = units.apply(find_method, axis=1, result_type="expand")
    methods.columns = ["calculation_method", "registered_name", "method_note"]
    return pd.concat([units.reset_index(drop=True), methods], axis=1)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--units", type=Path, default=DEFAULT_UNITS)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    units = pd.read_csv(args.units, encoding="utf-8-sig")
    audit = build_audit(units)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    audit.to_csv(args.output, index=False, encoding="utf-8-sig")
    counts = audit["calculation_method"].value_counts().sort_index()
    print(counts.to_string())
    return 0 if not (audit["calculation_method"] == "unresolved").any() else 2


if __name__ == "__main__":
    raise SystemExit(main())
