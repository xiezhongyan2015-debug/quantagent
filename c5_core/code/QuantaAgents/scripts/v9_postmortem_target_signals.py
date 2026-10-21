"""Read-only follow-up on the frozen account's exact saved signal dates."""
from copy import deepcopy
import json
from pathlib import Path
import sys

import numpy as np
import pandas as pd

from v9_postmortem_signal import (ROOT, read, sha, cached_material, analyze_period,
                                 summarize_ic, group_summary, daily_ic, HORIZON)
from quanta_agents.meta_v7.execution import execution_pool
from quanta_agents.research_kernel.compiler import evaluate_expression, factor_ids


def main():
    study = ROOT / "output/research/meta_v9_20260909/study_context_repair"
    output = ROOT / "output/research/meta_v9_20260909/postmortem_signal_20260909"
    target_path = study / "diagnostic/candidate/targets.parquet"
    paths = [study / name for name in ("selected_strategy.json", "definitions.json")]
    paths += [target_path, Path(__file__), Path(__file__).with_name("v9_postmortem_signal.py")]
    pins = {str(path): sha(path) for path in paths}
    destinations = [output / name for name in ("targets_subset.json", "targets_summary.json", "targets_manifest.json")]
    if any(path.exists() for path in destinations):
        raise ValueError("new target diagnostic files required")
    chosen = read(study / "selected_strategy.json")
    definitions = {row["id"]: row for row in read(study / "definitions.json")}
    wanted = sorted(factor_ids(chosen["strategy"]["score"]))
    panel, frames, cache = cached_material(study, "2024-12-31", definitions, wanted)
    pool = execution_pool(panel)
    scores = evaluate_expression(chosen["strategy"]["score"], frames, pool)
    full = analyze_period(panel, frames, scores, pool, "2021-01-01", "2024-12-31")
    targets = pd.read_parquet(target_path)
    if not targets.columns.equals(scores.columns) or not targets.index.is_unique:
        raise ValueError("saved targets axes differ")
    retained = {pd.Timestamp(row["date"]): row for row in full["daily"]}
    dates = targets.index.intersection(pd.DatetimeIndex(sorted(retained)))
    selected = [retained[date] for date in dates]
    unknown = []
    for date in targets.index.difference(dates):
        unknown.append({"date": date.date().isoformat(), "reason":
            "signal_before_2021_01_01_first_trade_signal_excluded" if date < pd.Timestamp("2021-01-01") else
            "terminal_five_day_label_would_need_out_of_scope_endpoint"})
    mismatch = []
    for date in dates:
        score = scores.loc[date].to_numpy()
        valid = np.flatnonzero(np.isfinite(score) & pool.loc[date].to_numpy())
        top = set(valid[np.argsort(-score[valid], kind="stable")][:40])
        saved = set(np.flatnonzero(targets.loc[date].to_numpy() > 0))
        if top != saved:
            mismatch.append({"date": date.date().isoformat(), "computed_count": len(top),
                             "saved_count": len(saved), "symmetric_difference": len(top ^ saved)})
    if mismatch:
        raise ValueError("reconstructed top40 differs from saved targets: " + str(mismatch[:3]))
    values = pd.Series([row["rank_ic"] for row in selected], index=dates, dtype=float)
    counts = pd.Series([row["paired_stock_cells"] for row in selected], index=dates, dtype=int)
    combined = summarize_ic(values, counts, sum(row["eligible_stock_cells"] for row in selected),
                            sum(row["scored_candidates"] for row in selected))
    opening = panel.fields["open"].where(np.isfinite(panel.fields["open"]) & panel.fields["open"].gt(0))
    if "open_observed" in panel.fields:
        opening = opening.where(panel.fields["open_observed"].eq(1))
    labels = (opening.shift(-(HORIZON+1)) / opening.shift(-1)-1).loc[dates]
    labels = labels.where(np.isfinite(labels))
    p = pool.loc[dates]
    raw_factors = {}
    for name, frame in frames.items():
        raw = frame.loc[dates]
        ic, paired = daily_ic(raw, labels, p)
        raw_factors[name] = summarize_ic(ic, paired, int(p.to_numpy().sum()),
                                         int((p & np.isfinite(raw)).to_numpy().sum()))
    result = {"version": "v9_actual_target_signal_postmortem_v1", "status": "completed", "inputs": pins,
              "strategy_sha256": chosen["strategy_sha256"], "cache_read": cache,
              "all_daily_2021_2024": full,
              "actual_target_dates": {"original_saved_target_count": len(targets), "retained_signal_days": len(dates),
                  "original_first_signal": str(targets.index[0].date()), "original_last_signal": str(targets.index[-1].date()),
                  "retained_first_signal": str(dates[0].date()), "retained_last_signal": str(dates[-1].date()),
                  "excluded_targets": unknown, "frozen_top40_membership_matches_saved_targets": True,
                  "combined_score": combined, "groups": group_summary(selected), "raw_factor_ic": raw_factors, "daily": selected},
              "operations": {"new_model_calls": 0, "new_accounts": 0, "refits": 0, "factor_recomputations": 0},
              "limits": ["existing-account signal dates, no reselection or new account",
                         "all dates already exposed; descriptive posthoc diagnosis",
                         "gross forward labels are not actual realized holding returns or financed net PnL",
                         "all-daily main interval purges only final six sessions; independent annual tables purge each year separately",
                         "first saved signal 2020-12-31 excluded by requested signal-date interval though it drives the first 2021 trade"]}
    for path, expected in pins.items():
        if sha(path) != expected:
            raise ValueError("input changed")
    destinations[0].write_text(json.dumps(result, ensure_ascii=False, sort_keys=True, allow_nan=False, indent=2), encoding="utf-8")
    brief = deepcopy(result)
    brief["all_daily_2021_2024"].pop("daily")
    brief["actual_target_dates"].pop("daily")
    destinations[1].write_text(json.dumps(brief, ensure_ascii=False, sort_keys=True, allow_nan=False, indent=2), encoding="utf-8")
    destinations[2].write_text(json.dumps({"inputs_unchanged": True, "files": {path.name: sha(path) for path in destinations[:2]}}, indent=2), encoding="utf-8")
    print(json.dumps({"actual_target_dates": len(dates), "ic": combined["mean_daily_rank_ic"],
                      "top_minus_full_pool": result["actual_target_dates"]["groups"]["top_minus_full_pool"]["mean_daily_forward_return_difference"]}))


if __name__ == "__main__":
    main()
