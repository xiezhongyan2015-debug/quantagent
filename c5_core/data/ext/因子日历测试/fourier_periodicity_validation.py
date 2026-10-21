from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

import research_periodic_active_buying as research


OUTPUT_DIR = Path("outputs/periodic_active_buying_20260804")
INPUT_PATH = OUTPUT_DIR / "level2_candidate_features.parquet"
FEATURE_PATH = OUTPUT_DIR / "fourier_features.parquet"
RESULT_PATH = OUTPUT_DIR / "fourier_rule_results.csv"


def score_window(amounts: np.ndarray, period: int) -> tuple[float, float, float]:
    if len(amounts) < 5 or not np.isfinite(amounts).all() or period < 2:
        return np.nan, np.nan, np.nan
    values = np.log1p(np.maximum(amounts, 0.0))
    positions = np.arange(len(values), dtype=np.float64)
    design = np.column_stack([np.ones(len(values)), positions])
    fitted = design @ np.linalg.lstsq(design, values, rcond=None)[0]
    residual = values - fitted
    energy = float(np.square(residual).sum())
    if energy <= 0:
        return 0.0, 0.0, 0.0
    wave = np.exp(-2j * np.pi * positions / float(period))
    coefficient = np.sum(residual * wave)
    power = float(np.square(np.abs(coefficient)) / (len(values) * energy))
    phase = float(np.real(coefficient) / np.sqrt(len(values) * energy))
    alternating = float(
        np.mean(values[::period]) - np.mean(values[np.mod(positions, period) != 0])
    )
    return power, phase, alternating


def build_features(frame: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for date, candidates in frame.groupby("date", sort=True):
        cache_path = OUTPUT_DIR / "minute_cache" / f"{pd.Timestamp(date):%Y-%m-%d}.parquet"
        minute = pd.read_parquet(cache_path, columns=["code", "minute_no", "minute_amount"])
        minute = minute.loc[minute["code"].isin(candidates["code"].unique())]
        lookup = minute.set_index(["code", "minute_no"])["minute_amount"]
        for row in candidates.itertuples(index=False):
            start = int(row.pulse1_position)
            end = int(row.pulse5_position)
            minute_numbers = np.arange(start, end + 1, dtype=np.int64)
            index = pd.MultiIndex.from_arrays(
                [np.repeat(row.code, len(minute_numbers)), minute_numbers]
            )
            amounts = lookup.reindex(index).to_numpy(dtype=np.float64)
            power, phase, phase_contrast = score_window(amounts, int(row.period))
            pulse_positions = np.array(
                [
                    int(row.pulse1_position),
                    int(row.pulse2_position),
                    int(row.pulse3_position),
                    int(row.pulse4_position),
                    int(row.pulse5_position),
                ]
            )
            offsets = pulse_positions - start
            valid_offsets = offsets[(offsets >= 0) & (offsets < len(amounts))]
            pulse_mask = np.zeros(len(amounts), dtype=bool)
            pulse_mask[valid_offsets] = True
            total = float(np.nansum(amounts))
            pulse_share = float(np.nansum(amounts[pulse_mask]) / total) if total > 0 else np.nan
            log_amounts = np.log1p(np.maximum(amounts, 0.0))
            quiet = log_amounts[~pulse_mask]
            pulse_quiet_contrast = (
                float(np.nanmean(log_amounts[pulse_mask]) - np.nanmean(quiet))
                if len(quiet)
                else np.nan
            )
            rows.append(
                {
                    "candidate_id": row.candidate_id,
                    "fourier_power": power,
                    "fourier_phase": phase,
                    "fourier_phase_contrast": phase_contrast,
                    "pulse_amount_share_in_window": pulse_share,
                    "pulse_quiet_log_contrast": pulse_quiet_contrast,
                }
            )
    return pd.DataFrame(rows)


def add_summary(
    rows: list[dict[str, object]],
    frame: pd.DataFrame,
    dates: np.ndarray,
    mask: pd.Series,
    rule: str,
    period_name: str,
) -> None:
    part = frame.loc[frame["date"].isin(dates)]
    summary = research.summarize_events(part.loc[mask.loc[part.index]], rule, period_name)
    rows.append(summary)


def main() -> None:
    frame = pd.read_parquet(INPUT_PATH)
    frame["date"] = pd.to_datetime(frame["date"])
    if FEATURE_PATH.is_file():
        features = pd.read_parquet(FEATURE_PATH)
    else:
        features = build_features(frame)
        features.to_parquet(FEATURE_PATH, index=False)
    frame = frame.merge(features, on="candidate_id", how="left", validate="one_to_one")

    dates = np.array_split(sorted(frame["date"].drop_duplicates()), 3)
    first = frame.loc[frame["date"].isin(dates[0]) & research.base_level2_mask(frame)]
    power_thresholds = {
        f"fourier_power_train_q{quantile}": float(first["fourier_power"].quantile(quantile / 100))
        for quantile in (50, 70, 80)
    }
    pulse_share_threshold = float(first["pulse_amount_share_in_window"].quantile(0.70))

    base = research.base_level2_mask(frame)
    exact_gaps = frame["gap_min"].eq(frame["gap_max"])
    core = (
        base
        & frame["pulse_active_buy_amount_max_min"].le(1.8)
        & frame["pulse_combined_buy_share"].ge(0.75)
    )
    rules: list[tuple[str, pd.Series]] = [("base", base), ("exact_all_four_gaps", base & exact_gaps)]
    for name, threshold in power_thresholds.items():
        rules.append((f"{name}>={threshold:.6f}", base & frame["fourier_power"].ge(threshold)))
    rules.extend(
        [
            (
                f"pulse_amount_share_train_q70>={pulse_share_threshold:.6f}",
                base & frame["pulse_amount_share_in_window"].ge(pulse_share_threshold),
            ),
            ("core_amount_ratio_1.8_buy75", core),
            (
                f"core_and_fourier_train_q50>={power_thresholds['fourier_power_train_q50']:.6f}",
                core & frame["fourier_power"].ge(power_thresholds["fourier_power_train_q50"]),
            ),
        ]
    )

    rows: list[dict[str, object]] = []
    for rule, mask in rules:
        for number, selected_dates in enumerate(dates, 1):
            add_summary(rows, frame, selected_dates, mask, rule, f"part{number}")
        add_summary(rows, frame, np.concatenate(dates), mask, rule, "all")
    result = pd.DataFrame(rows)
    result.to_csv(RESULT_PATH, index=False, encoding="utf-8-sig")
    columns = [
        "rule",
        "period",
        "count",
        "joint_minute_hit",
        "target_buy_up_70",
        "return_5m_mean",
        "return_10m_mean",
        "return_20m_mean",
    ]
    print(result[columns].to_string(index=False))
    print(f"saved: {FEATURE_PATH}")
    print(f"saved: {RESULT_PATH}")


if __name__ == "__main__":
    main()
