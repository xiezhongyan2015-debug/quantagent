from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd

import research_periodic_active_buying as research


OUTPUT_DIR = Path("outputs/periodic_active_buying_20260804")
INPUT_PATH = OUTPUT_DIR / "level2_candidate_features.parquet"
ALL_RULES_PATH = OUTPUT_DIR / "nested_rules_train_validation.csv"
SELECTED_RULES_PATH = OUTPUT_DIR / "nested_selected_rules.csv"
PREDEFINED_RULES_PATH = OUTPUT_DIR / "predefined_high_precision_rules.csv"
HIGH_PRECISION_EVENTS_PATH = OUTPUT_DIR / "high_precision_events.csv"
BEAM_WIDTH = 40
MAX_CONDITIONS = 4
TRAIN_MIN_COUNT = 50


@dataclass(frozen=True)
class Condition:
    feature: str
    direction: str
    threshold: float

    def apply(self, frame: pd.DataFrame) -> pd.Series:
        values = pd.to_numeric(frame[self.feature], errors="coerce")
        if self.direction == "high":
            return values.ge(self.threshold)
        return values.le(self.threshold)

    @property
    def label(self) -> str:
        sign = ">=" if self.direction == "high" else "<="
        return f"{self.feature}{sign}{self.threshold:.8g}"


@dataclass(frozen=True)
class Rule:
    conditions: tuple[Condition, ...]

    @property
    def label(self) -> str:
        return "; ".join(condition.label for condition in self.conditions)

    @property
    def features(self) -> frozenset[str]:
        return frozenset(condition.feature for condition in self.conditions)

    def apply(self, frame: pd.DataFrame) -> pd.Series:
        mask = pd.Series(True, index=frame.index)
        for condition in self.conditions:
            mask &= condition.apply(frame)
        return mask


def split_three(frame: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    dates = sorted(pd.to_datetime(frame["date"]).drop_duplicates())
    first_dates, second_dates, third_dates = np.array_split(dates, 3)
    parts = []
    for selected_dates in (first_dates, second_dates, third_dates):
        part = frame.loc[frame["date"].isin(selected_dates) & research.base_level2_mask(frame)]
        parts.append(research.apply_cooldown(part).copy())
    return parts[0], parts[1], parts[2]


def make_conditions(train: pd.DataFrame) -> list[Condition]:
    conditions: list[Condition] = []
    for feature, direction in research.SEARCH_FEATURES.items():
        values = pd.to_numeric(train[feature], errors="coerce").dropna()
        if len(values) < TRAIN_MIN_COUNT or values.nunique() < 2:
            continue
        for threshold in values.quantile([0.30, 0.50, 0.70]).dropna().unique():
            conditions.append(Condition(feature, direction, float(threshold)))
    return conditions


def summarize(frame: pd.DataFrame, rule: Rule | None) -> dict[str, object]:
    sample = frame if rule is None else frame.loc[rule.apply(frame)]
    count = len(sample)
    success = int(sample["target_buy_up_70"].fillna(False).sum())
    joint = int(sample["joint_minute_hit"].fillna(False).sum())
    low, high = research.wilson_interval(success, count)
    result: dict[str, object] = {
        "count": count,
        "target_buy_up_70": success / count if count else np.nan,
        "target_buy_up_70_wilson_low": low,
        "target_buy_up_70_wilson_high": high,
        "joint_minute_hit": joint / count if count else np.nan,
    }
    for minutes in (5, 10, 20):
        values = pd.to_numeric(sample[f"forward_{minutes}m"], errors="coerce").dropna()
        result[f"return_{minutes}m_count"] = len(values)
        result[f"return_{minutes}m_mean"] = values.mean() if len(values) else np.nan
        result[f"return_{minutes}m_median"] = values.median() if len(values) else np.nan
        result[f"return_{minutes}m_up"] = values.gt(0).mean() if len(values) else np.nan
    return result


def rule_score(summary: dict[str, object]) -> tuple[float, float, int]:
    return (
        float(summary["target_buy_up_70_wilson_low"]),
        float(summary["target_buy_up_70"]),
        int(summary["count"]),
    )


def build_beam(train: pd.DataFrame, conditions: Iterable[Condition]) -> list[Rule]:
    condition_list = list(conditions)
    beam = [Rule(())]
    found: list[Rule] = []
    for _ in range(MAX_CONDITIONS):
        candidates: list[tuple[tuple[float, float, int], Rule]] = []
        seen_masks: set[bytes] = set()
        seen_labels: set[str] = set()
        for parent in beam:
            for condition in condition_list:
                if condition.feature in parent.features:
                    continue
                rule = Rule(parent.conditions + (condition,))
                if rule.label in seen_labels:
                    continue
                mask = rule.apply(train).to_numpy(dtype=np.bool_)
                packed = np.packbits(mask).tobytes()
                if packed in seen_masks:
                    continue
                seen_masks.add(packed)
                seen_labels.add(rule.label)
                summary = summarize(train, rule)
                if int(summary["count"]) < TRAIN_MIN_COUNT:
                    continue
                candidates.append((rule_score(summary), rule))
        candidates.sort(key=lambda item: item[0], reverse=True)
        beam = [rule for _, rule in candidates[:BEAM_WIDTH]]
        found.extend(beam)
        if not beam:
            break
    return found


def add_period(prefix: str, summary: dict[str, object], row: dict[str, object]) -> None:
    for key, value in summary.items():
        row[f"{prefix}_{key}"] = value


def fixed_amount_rule() -> Rule:
    return Rule(
        (
            Condition("pulse_active_buy_amount_max_min", "low", 1.8),
            Condition("pulse_combined_buy_share", "high", 0.75),
        )
    )


def predefined_rules() -> list[tuple[str, Rule]]:
    core = fixed_amount_rule()
    return [
        ("core_amount_ratio_1.8_buy75", core),
        (
            "core_and_market_up_share_le50",
            Rule(core.conditions + (Condition("market_up_share_at_trigger", "low", 0.50),)),
        ),
        (
            "core_and_market_up_share_30_to_50",
            Rule(
                core.conditions
                + (
                    Condition("market_up_share_at_trigger", "high", 0.30),
                    Condition("market_up_share_at_trigger", "low", 0.50),
                )
            ),
        ),
        (
            "core_and_quiet_return_abs_le15bp",
            Rule(core.conditions + (Condition("quiet_net_return_abs", "low", 0.0015),)),
        ),
        (
            "amount_ratio_1.8_buy80",
            Rule(
                (
                    Condition("pulse_active_buy_amount_max_min", "low", 1.8),
                    Condition("pulse_combined_buy_share", "high", 0.80),
                )
            ),
        ),
        (
            "core_and_four_pulses_buy70",
            Rule(core.conditions + (Condition("pulse_buy_share_ge70_count", "high", 4),)),
        ),
    ]


def summarize_raw(frame: pd.DataFrame, rule: Rule) -> tuple[dict[str, object], pd.DataFrame]:
    mask = research.base_level2_mask(frame) & rule.apply(frame)
    sample = research.apply_cooldown(frame.loc[mask]).copy()
    return summarize(sample, None), sample


def save_predefined_results(frame: pd.DataFrame) -> None:
    dates = sorted(frame["date"].drop_duplicates())
    date_parts = np.array_split(dates, 3)
    rows: list[dict[str, object]] = []
    core_sample = pd.DataFrame()
    for name, rule in predefined_rules():
        row: dict[str, object] = {"selection": name, "rule": rule.label}
        full_summary, full_sample = summarize_raw(frame, rule)
        add_period("all", full_summary, row)
        for number, selected_dates in enumerate(date_parts, 1):
            part = frame.loc[frame["date"].isin(selected_dates)]
            part_summary, _ = summarize_raw(part, rule)
            add_period(f"part{number}", part_summary, row)
        rows.append(row)
        if name == "core_amount_ratio_1.8_buy75":
            core_sample = full_sample
    pd.DataFrame(rows).to_csv(PREDEFINED_RULES_PATH, index=False, encoding="utf-8-sig")

    if not core_sample.empty:
        core_sample = core_sample.copy()
        core_sample["market_up_share_le50"] = core_sample["market_up_share_at_trigger"].le(0.50)
        core_sample["market_up_share_30_to_50"] = (
            core_sample["market_up_share_at_trigger"].ge(0.30)
            & core_sample["market_up_share_at_trigger"].le(0.50)
        )
        core_sample["quiet_return_abs_le15bp"] = core_sample["quiet_net_return"].abs().le(0.0015)
        core_sample["combined_buy_share_ge80"] = core_sample["pulse_combined_buy_share"].ge(0.80)
        core_sample["four_pulses_buy_share_ge70"] = core_sample["pulse_buy_share_ge70_count"].ge(4)
        useful = [
            "candidate_id",
            "date",
            "code",
            "trigger_ts",
            "predicted_ts",
            "period",
            "volume_hit",
            "joint_minute_hit",
            "target_buy_up_60",
            "target_buy_up_70",
            "target_buy_up_75",
            "target_minute_return",
            "pulse_combined_buy_share",
            "pulse_active_buy_amount_max_min",
            "pulse_buy_share_ge70_count",
            "quiet_net_return",
            "rise_concentration",
            "market_up_share_at_trigger",
            "market_spike_share_at_trigger",
            "market_up_share_le50",
            "market_up_share_30_to_50",
            "quiet_return_abs_le15bp",
            "combined_buy_share_ge80",
            "four_pulses_buy_share_ge70",
            "forward_5m",
            "forward_10m",
            "forward_20m",
        ]
        for number in range(1, 6):
            useful.extend(
                [
                    f"pulse{number}_ts",
                    f"pulse{number}_return",
                    f"pulse{number}_amount",
                    f"pulse{number}_active_buy_amount",
                    f"pulse{number}_active_buy_share",
                ]
            )
        core_sample[useful].to_csv(HIGH_PRECISION_EVENTS_PATH, index=False, encoding="utf-8-sig")


def main() -> None:
    frame = pd.read_parquet(INPUT_PATH)
    frame["date"] = pd.to_datetime(frame["date"])
    frame["quiet_net_return_abs"] = pd.to_numeric(
        frame["quiet_net_return"], errors="coerce"
    ).abs()
    train, validation, final = split_three(frame)
    conditions = make_conditions(train)
    rules = build_beam(train, conditions)

    rows: list[dict[str, object]] = []
    for rule in rules:
        row: dict[str, object] = {
            "rule": rule.label,
            "condition_count": len(rule.conditions),
        }
        add_period("train", summarize(train, rule), row)
        add_period("validation", summarize(validation, rule), row)
        rows.append(row)
    comparison = pd.DataFrame(rows)
    comparison = comparison.sort_values(
        [
            "validation_target_buy_up_70_wilson_low",
            "validation_target_buy_up_70",
            "validation_count",
        ],
        ascending=[False, False, False],
    )
    comparison.to_csv(ALL_RULES_PATH, index=False, encoding="utf-8-sig")

    base_validation = summarize(validation, None)
    selected: list[tuple[str, Rule]] = []

    frequent = comparison.loc[comparison["validation_count"].ge(50)]
    if not frequent.empty:
        selected.append(("precision_min50", next(rule for rule in rules if rule.label == frequent.iloc[0]["rule"])))

    rare = comparison.loc[comparison["validation_count"].ge(20)]
    if not rare.empty:
        selected.append(("precision_min20", next(rule for rule in rules if rule.label == rare.iloc[0]["rule"])))

    trade = comparison.loc[
        comparison["validation_count"].ge(40)
        & comparison["validation_target_buy_up_70"].ge(base_validation["target_buy_up_70"])
        & comparison["validation_return_5m_mean"].ge(0)
        & comparison["validation_return_10m_mean"].ge(0)
    ].copy()
    if not trade.empty:
        trade = trade.sort_values(
            [
                "validation_target_buy_up_70_wilson_low",
                "validation_return_5m_mean",
                "validation_return_10m_mean",
            ],
            ascending=[False, False, False],
        )
        selected.append(("precision_with_positive_returns", next(rule for rule in rules if rule.label == trade.iloc[0]["rule"])))

    selected.append(("fixed_amount_ratio_1.8_buy75", fixed_amount_rule()))
    unique: dict[str, tuple[str, Rule]] = {}
    for role, rule in selected:
        unique.setdefault(rule.label, (role, rule))

    selected_rows: list[dict[str, object]] = []
    for role, rule in unique.values():
        row = {"selection": role, "rule": rule.label, "condition_count": len(rule.conditions)}
        add_period("train", summarize(train, rule), row)
        add_period("validation", summarize(validation, rule), row)
        add_period("final", summarize(final, rule), row)
        selected_rows.append(row)

    base_row = {"selection": "base", "rule": "minute_fixed_rule", "condition_count": 0}
    add_period("train", summarize(train, None), base_row)
    add_period("validation", base_validation, base_row)
    add_period("final", summarize(final, None), base_row)
    selected_rows.insert(0, base_row)

    selected_frame = pd.DataFrame(selected_rows)
    selected_frame.to_csv(SELECTED_RULES_PATH, index=False, encoding="utf-8-sig")
    save_predefined_results(frame)
    print(selected_frame.to_string(index=False))
    print(f"saved: {ALL_RULES_PATH}")
    print(f"saved: {SELECTED_RULES_PATH}")
    print(f"saved: {PREDEFINED_RULES_PATH}")
    print(f"saved: {HIGH_PRECISION_EVENTS_PATH}")


if __name__ == "__main__":
    main()
