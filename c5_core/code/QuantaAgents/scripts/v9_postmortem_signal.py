"""Read-only cached frozen-score postmortem; no fit, account, or model call.

The only output is a new diagnostic directory. Original study, source, panel,
and score caches are never opened for writing. Daily forward labels overlap;
their means are not a financed, cost-adjusted portfolio return.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import gc
import hashlib
import json
from pathlib import Path
import sys

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from quanta_agents.meta_v6.data import _cache_read as read_panel_cache
from quanta_agents.meta_v6.factors import _json as factor_json
from quanta_agents.meta_v7.execution import execution_pool
from quanta_agents.research_kernel.assets import AssetRegistry, _calculator_identity, _digest, _io
from quanta_agents.research_kernel.compiler import evaluate_expression, factor_ids


MAX_END = "2024-12-31"
HORIZON = 5
TOP_N = 40


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def sha(path):
    h = hashlib.sha256()
    with _io(Path(path)).open("rb") as stream:
        for block in iter(lambda: stream.read(1024*1024), b""):
            h.update(block)
    return h.hexdigest()


def number(value):
    return float(value) if pd.notna(value) and np.isfinite(value) else None


def cached_material(study, end, definitions, wanted):
    candidates = []
    for path in (study / "panel_cache").glob("*/manifest.json"):
        manifest = read(path)
        request = manifest["provenance"]["request"]
        if request["start"] == "2015-01-01" and request["end"] == end:
            candidates.append((path, manifest))
    if len(candidates) != 1 or end > MAX_END:
        raise ValueError("one exact existing authorized panel cache required")
    path, manifest = candidates[0]
    before = sha(path)
    panel = read_panel_cache(path.parent, manifest["cache_key"])
    if panel.dates[-1] > pd.Timestamp(end) or panel.dates[-1] > pd.Timestamp(MAX_END):
        raise ValueError("out-of-scope cached panel")
    # Match FactorEngine's immutable snapshot fingerprint without making a
    # second full defensive market snapshot or computing any factor.
    h = hashlib.sha256(factor_json(deepcopy(panel.provenance)).encode("utf-8"))
    reference = panel.fields["open"]
    h.update(factor_json({"columns": list(reference.columns), "index_dtype": str(reference.index.dtype),
                         "index_name": reference.index.name, "columns_name": reference.columns.name}).encode("utf-8"))
    for name in sorted([*panel.fields, "__eligible__"]):
        value = panel.eligible.astype(bool, copy=False) if name == "__eligible__" else panel.fields[name].astype(float, copy=False)
        h.update(name.encode("utf-8"))
        h.update(pd.util.hash_pandas_object(value, index=True).values.tobytes())
    calculator, engine_fingerprint = _calculator_identity(), h.hexdigest()
    # Do not run AssetRegistry.__init__ (which touches SQLite). Only its
    # existing, hash-checked cache reader is needed.
    registry = object.__new__(AssetRegistry)
    registry.root = (study / "assets").resolve()
    registry.cache_dir = registry.root / "score_cache"
    frames, caches = {}, []
    for name in wanted:
        identity = {"expression": definitions[name]["expression"], "calculator": calculator,
                    "panel_fingerprint": engine_fingerprint}
        frame, corruption = registry._cache_read(identity, panel.eligible)
        if frame is None:
            raise ValueError("required existing factor cache unavailable: " + name + " " + str(corruption))
        frames[name] = frame
        key = _digest(identity)
        cache_path = registry.cache_dir / key / "manifest.json"
        cache_manifest = read(_io(cache_path))
        caches.append({"factor_id": name, "key": key, "manifest_path": str(cache_path),
                       "manifest_sha256": sha(cache_path), "payload_sha256": cache_manifest["payload_sha256"]})
    if sha(path) != before:
        raise ValueError("panel manifest changed")
    evidence = {"panel_manifest_path": str(path), "panel_manifest_sha256": before,
                "panel_payload_sha256": manifest["payload_sha256"], "panel_fingerprint": panel.fingerprint(),
                "factor_engine_fingerprint": engine_fingerprint, "panel_shape": list(panel.eligible.shape),
                "numeric_start": panel.dates[0].date().isoformat(), "numeric_end": panel.dates[-1].date().isoformat(),
                "factor_cache_hits": len(frames), "factor_recomputations": 0, "factor_caches": caches,
                "raw_market_source_payloads_read": False}
    return panel, frames, evidence


def daily_ic(left, labels, pool):
    common = pool & np.isfinite(left) & np.isfinite(labels)
    a = left.where(common).rank(axis=1, method="average")
    b = labels.where(common).rank(axis=1, method="average")
    counts = common.sum(axis=1)
    values = a.corrwith(b, axis=1).where((counts >= 5) & a.nunique(axis=1).gt(1) & b.nunique(axis=1).gt(1))
    return values, counts


def summarize_ic(values, counts, pool_cells, score_cells):
    n = int(values.notna().sum())
    return {"mean_daily_rank_ic": number(values.mean()), "median_daily_rank_ic": number(values.median()),
            "daily_ic_std": number(values.std(ddof=1)), "positive_ic_days": int(values.gt(0).sum()),
            "negative_ic_days": int(values.lt(0).sum()), "zero_ic_days": int(values.eq(0).sum()),
            "positive_ic_fraction_of_observed_days": number(values.gt(0).sum()/n) if n else None,
            "calendar_days": len(values), "observed_ic_days": n, "unknown_ic_days": len(values)-n,
            "paired_stock_cells": int(counts.sum()), "pool_stock_cells": pool_cells,
            "finite_score_stock_cells": score_cells,
            "missing_score_stock_cells": pool_cells-score_cells,
            "missing_label_among_scored_stock_cells": score_cells-int(counts.sum())}


def group_days(scores, labels, pool, top_n=TOP_N):
    rows = []
    for date in scores.index:
        score, target, allowed = (value.loc[date].to_numpy() for value in (scores, labels, pool))
        finite = np.isfinite(score) & allowed
        candidates = np.flatnonzero(finite)
        order = candidates[np.argsort(-score[candidates], kind="stable")]
        top = order[:top_n]
        rest = order[top_n:]
        bottom = order[-top_n:] if len(order) >= top_n*2 else np.array([], dtype=int)
        groups = {"top40": top, "remaining_scored": rest, "bottom40": bottom,
                  "scored_pool": order, "full_eligible_pool": np.flatnonzero(allowed)}
        row = {"date": date.date().isoformat(), "scored_candidates": len(order),
               "top40_complete_membership": len(top) == top_n,
               "top_bottom_disjoint_full_groups": len(order) >= top_n*2}
        for name, indices in groups.items():
            known = target[indices]
            known = known[np.isfinite(known)]
            row[name] = {"selected": len(indices), "observed_labels": len(known),
                         "missing_labels": len(indices)-len(known),
                         "mean_forward_return": float(known.mean()) if len(known) else None}
        for other, key in (("remaining_scored", "top_minus_remaining"), ("bottom40", "top_minus_bottom40"),
                           ("scored_pool", "top_minus_scored_pool"), ("full_eligible_pool", "top_minus_full_pool")):
            a, b = row["top40"]["mean_forward_return"], row[other]["mean_forward_return"]
            row[key] = a-b if a is not None and b is not None else None
        rows.append(row)
    return rows


def group_summary(rows):
    result = {}
    for name in ("top40", "remaining_scored", "bottom40", "scored_pool", "full_eligible_pool"):
        means = pd.Series([row[name]["mean_forward_return"] for row in rows], dtype=float)
        result[name] = {"mean_daily_equal_weight_forward_return": number(means.mean()),
                        "observed_mean_days": int(means.notna().sum()), "unknown_mean_days": int(means.isna().sum()),
                        "selected_stock_cells": sum(row[name]["selected"] for row in rows),
                        "observed_label_stock_cells": sum(row[name]["observed_labels"] for row in rows),
                        "missing_label_stock_cells": sum(row[name]["missing_labels"] for row in rows)}
    for name in ("top_minus_remaining", "top_minus_bottom40", "top_minus_scored_pool", "top_minus_full_pool"):
        values = pd.Series([row[name] for row in rows], dtype=float)
        observed = int(values.notna().sum())
        result[name] = {"mean_daily_forward_return_difference": number(values.mean()),
                        "median_daily_difference": number(values.median()), "observed_days": observed,
                        "unknown_days": len(rows)-observed, "positive_days": int(values.gt(0).sum()),
                        "positive_day_fraction": float(values.gt(0).sum()/observed) if observed else None}
    result["calendar_days"] = len(rows)
    result["days_with_complete_top40"] = sum(row["top40_complete_membership"] for row in rows)
    result["days_with_disjoint_top_bottom40"] = sum(row["top_bottom_disjoint_full_groups"] for row in rows)
    return result


def analyze_period(panel, frames, score, pool, start, end):
    prefix_dates = panel.dates[panel.dates <= pd.Timestamp(end)]
    safe_dates = prefix_dates[:-(HORIZON+1)]
    dates = safe_dates[safe_dates >= pd.Timestamp(start)]
    opening = panel.fields["open"].loc[prefix_dates]
    opening = opening.where(np.isfinite(opening) & opening.gt(0))
    if "open_observed" in panel.fields:
        opening = opening.where(panel.fields["open_observed"].loc[prefix_dates].eq(1))
    labels = (opening.shift(-(HORIZON+1))/opening.shift(-1)-1).loc[dates]
    labels = labels.where(np.isfinite(labels))
    p, scores = pool.loc[dates], score.loc[dates]
    values, counts = daily_ic(scores, labels, p)
    pool_cells = int(p.to_numpy().sum())
    nscore = int((p & np.isfinite(scores)).to_numpy().sum())
    daily = group_days(scores, labels, p)
    for index, row in enumerate(daily):
        row.update(rank_ic=number(values.iloc[index]), paired_stock_cells=int(counts.iloc[index]),
                   eligible_stock_cells=int(p.iloc[index].sum()))
    factors = {}
    for name, frame in frames.items():
        raw = frame.loc[dates]
        factor_ic, paired = daily_ic(raw, labels, p)
        support = int((p & np.isfinite(raw)).to_numpy().sum())
        factors[name] = summarize_ic(factor_ic, paired, pool_cells, support)
    return {"scope": {"requested_start": start, "requested_end": end,
                       "first_signal_date": dates[0].date().isoformat(), "last_signal_date": dates[-1].date().isoformat(),
                       "last_allowed_label_endpoint": prefix_dates[-1].date().isoformat(),
                       "purged_terminal_signal_sessions": HORIZON+1,
                       "all_dates_previously_exposed": True},
            "combined_score": summarize_ic(values, counts, pool_cells, nscore),
            "groups": group_summary(daily), "raw_factor_ic": factors, "daily": daily}


def self_check():
    dates = pd.bdate_range("2010-01-01", periods=2)
    score = pd.DataFrame(np.tile(np.arange(10.), (2, 1)), index=dates)
    labels, pool = -score, score.notna()
    values, counts = daily_ic(score, labels, pool)
    assert np.allclose(values, -1) and counts.eq(10).all()
    labels.iloc[0, 9] = np.nan
    rows = group_days(score, labels, pool, top_n=2)
    assert rows[0]["top40"]["selected"] == 2 and rows[0]["top40"]["observed_labels"] == 1
    assert rows[0]["top40"]["mean_forward_return"] == -8
    assert rows[0]["remaining_scored"]["selected"] == 8
    assert rows[1]["top_minus_bottom40"] == -8


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--study", type=Path, default=ROOT / "output/research/meta_v9_20260909/study_context_repair")
    parser.add_argument("--output", type=Path, default=ROOT / "output/research/meta_v9_20260909/postmortem_signal_20260909")
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("new diagnostic output directory required; no overwrite")
    self_check()
    source_paths = [args.study / name for name in ("selected_strategy.json", "development_report.json", "definitions.json")]
    source_paths += [Path(__file__), ROOT / "src/quanta_agents/meta_v7/combination.py",
                     ROOT / "src/quanta_agents/meta_v6/data.py", ROOT / "src/quanta_agents/research_kernel/compiler.py"]
    pins = {str(path.resolve()): sha(path) for path in source_paths}
    selected, development = (read(args.study / name) for name in ("selected_strategy.json", "development_report.json"))
    definitions = {row["id"]: row for row in read(args.study / "definitions.json")}
    if selected["method"] != "ridge_augmented" or selected["fit_artifact_sha256"] != development["final_fit"]["artifact_sha256"]:
        raise ValueError("frozen selected fit binding mismatch")
    wanted = sorted(factor_ids(selected["strategy"]["score"]))
    if len(wanted) != 8:
        raise ValueError("exact frozen eight-factor family required")
    output = {"version": "v9_frozen_signal_postmortem_v1", "status": "completed",
              "strategy_sha256": selected["strategy_sha256"], "method": selected["method"],
              "inputs": pins, "factor_ids": wanted, "periods": {}, "cache_reads": [],
              "operations": {"new_model_calls": 0, "new_accounts": 0, "refits": 0, "factor_recomputations": 0},
              "semantics": {"score": "exact selected_strategy frozen score; no refit or direction change",
                "label": "observed positive open[t+6]/open[t+1]-1; no future membership filter",
                "ic": "daily common-finite-sample Spearman; min five stocks; average ties",
                "top_selection": "stable descending score before inspecting future label availability",
                "bottom_selection": "last forty by same order only on days with at least eighty scored stocks",
                "means": "equal daily mean of known group labels; full missing-label denominator retained",
                "capital": "overlapping five-session forward labels, no financed equity curve or costs",
                "exposure": "all dates already exposed; posthoc attribution, not a new independent test"},
              "limitations": ["non-executable gross signal diagnostics; no fills, holdings, fees or shared capital simulated",
                "group means exclude unknown labels but retain every selected/unknown denominator; missingness can be selective",
                "final 2016-2020 fit is in-sample and methods were selected on exposed development results",
                "daily five-day labels overlap and are not independent samples",
                "multiple fixed-factor diagnostics are descriptive without multiplicity correction",
                "source point-in-time adjustment and publication vintage limitations are unchanged"],
              "generated_self_check": "passed"}
    for end, periods in (("2020-12-31", [("2016-2020", "2016-01-01", "2020-12-31")]),
                         ("2024-12-31", [(str(y), f"{y}-01-01", f"{y}-12-31") for y in range(2021, 2025)])):
        panel, frames, evidence = cached_material(args.study, end, definitions, wanted)
        output["cache_reads"].append(evidence)
        pool = execution_pool(panel)
        score = evaluate_expression(selected["strategy"]["score"], frames, pool)
        for label, start, cutoff in periods:
            output["periods"][label] = analyze_period(panel, frames, score, pool, start, cutoff)
            print(json.dumps({"period": label, "ic": output["periods"][label]["combined_score"]["mean_daily_rank_ic"],
                              "signal_days": output["periods"][label]["combined_score"]["calendar_days"]}), flush=True)
        del panel, frames, pool, score
        gc.collect()
    saved_ic = development["final_fit"]["models"][selected["method"]]["train_fit_diagnostics"]["mean_daily_rank_ic"]
    observed_ic = output["periods"]["2016-2020"]["combined_score"]["mean_daily_rank_ic"]
    if not np.isclose(saved_ic, observed_ic, rtol=0, atol=1e-12):
        raise ValueError("frozen train IC does not reproduce saved fit")
    output["saved_training_ic_reproduction"] = {"saved": saved_ic, "observed": observed_ic, "absolute_tolerance": 1e-12, "passed": True}
    output["original_fold_train_diagnostics"] = [{"fold": f["index"], "plan": f["plan"],
        "selected_base_factors": f["fit"]["selected_factors"],
        "tma_in_augmented": any(x["id"] == "calendar.standard.tma" for x in f["fit"]["models"]["ridge_augmented"]["features"]),
        "ridge_augmented": f["fit"]["models"]["ridge_augmented"]["train_fit_diagnostics"]} for f in development["folds"]]
    for path, expected in pins.items():
        if sha(path) != expected:
            raise ValueError("original input changed during postmortem: " + path)
    args.output.mkdir(parents=True, exist_ok=False)
    (args.output / "report.json").write_text(json.dumps(output, ensure_ascii=False, sort_keys=True, allow_nan=False, indent=2), encoding="utf-8")
    summary = deepcopy(output)
    for value in summary["periods"].values():
        value.pop("daily")
    (args.output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, sort_keys=True, allow_nan=False, indent=2), encoding="utf-8")
    (args.output / "manifest.json").write_text(json.dumps({"files": {
        name: sha(args.output/name) for name in ("report.json", "summary.json")}, "inputs_unchanged": True}, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
