"""Independent V9A numerical audit over explicit, date-gated saved snapshots.

The oracle deliberately does not call the production factor evaluator, its label
builder, its correlation routines, or its annual aggregator. It consumes frozen
factor values: separate behavioural tests establish formula and causality risks.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import time

import numpy as np


VERSION = "v9a_independent_audit_v1"


def scalar_pearson(left, right):
    """Centered dot-product reference, rejecting degenerate paired samples."""
    pairs = [(float(x), float(y)) for x, y in zip(left, right)
             if math.isfinite(float(x)) and math.isfinite(float(y))]
    if len(pairs) < 2:
        return None
    mean_x = math.fsum(x for x, _ in pairs) / len(pairs)
    mean_y = math.fsum(y for _, y in pairs) / len(pairs)
    mass_x = math.fsum((x - mean_x) ** 2 for x, _ in pairs)
    mass_y = math.fsum((y - mean_y) ** 2 for _, y in pairs)
    if mass_x <= 0 or mass_y <= 0:
        return None
    return math.fsum((x - mean_x) * (y - mean_y) for x, y in pairs) / math.sqrt(mass_x * mass_y)


def average_tie_ranks(values):
    """Ranks from explicit sorted tie runs; independent of pandas rank."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [None] * len(values)
    left = 0
    while left < len(order):
        right = left + 1
        while right < len(order) and values[order[right]] == values[order[left]]:
            right += 1
        rank = (left + 1 + right) / 2
        for position in order[left:right]:
            ranks[position] = rank
        left = right
    return ranks


def independent_daily(dates, opening, scores, pool, *, start, end, direction,
                      horizon=5, min_cross_section=100, observed=None):
    """Reference t+1/t+6 endpoints contained in the signal's natural year.

    Date validation is deliberately before any numeric matrix conversion. The
    caller must also keep unauthorized values out of the original saved file.
    """
    dates = [str(x)[:10] for x in dates]
    if not dates or dates != sorted(set(dates)):
        raise ValueError("unique ordered session dates required")
    if dates[0] < "2015-01-01" or dates[-1] > "2024-12-31":
        raise ValueError("unauthorized numeric dates")
    if not "2015-01-01" <= start <= end <= "2024-12-31":
        raise ValueError("unauthorized evaluation dates")
    if direction not in (-1, 1) or type(horizon) is not int or horizon < 1:
        raise ValueError("frozen direction and positive horizon required")
    opening, scores, pool = np.asarray(opening), np.asarray(scores), np.asarray(pool)
    if opening.shape != scores.shape or scores.shape != pool.shape or len(scores) != len(dates):
        raise ValueError("snapshot axes differ")
    if observed is not None and np.asarray(observed).shape != opening.shape:
        raise ValueError("observed endpoint axes differ")
    output = []
    for day, date in enumerate(dates):
        if not start <= date <= end:
            continue
        exit_day = day + horizon + 1
        endpoint_valid = (exit_day < len(dates) and dates[exit_day] <= end
                          and dates[exit_day][:4] == date[:4])
        xs, ys = [], []
        eligible_n, factor_n = 0, 0
        for stock in range(opening.shape[1]):
            if not bool(pool[day, stock]):
                continue
            eligible_n += 1
            value = float(scores[day, stock])
            if math.isfinite(value):
                factor_n += 1
            if not endpoint_valid or not math.isfinite(value):
                continue
            entry, exit_ = float(opening[day + 1, stock]), float(opening[exit_day, stock])
            if not (math.isfinite(entry) and math.isfinite(exit_) and entry > 0 and exit_ > 0):
                continue
            if observed is not None and not (observed[day + 1, stock] == 1 and observed[exit_day, stock] == 1):
                continue
            xs.append(direction * value)
            ys.append(exit_ / entry - 1)
        enough = len(xs) >= min_cross_section
        output.append({"date": date, "pearson_ic": scalar_pearson(xs, ys) if enough else None,
            "rank_ic": scalar_pearson(average_tie_ranks(xs), average_tie_ranks(ys)) if enough else None,
            "paired_count": len(xs), "eligible_count": eligible_n, "factor_count": factor_n,
            "endpoint_within_year_and_scope": endpoint_valid})
    return output


def independent_annual(rows):
    years = sorted({int(row["date"][:4]) for row in rows})
    output = []
    for year in years:
        part = [row for row in rows if row["date"].startswith(str(year))]
        pearson = [row["pearson_ic"] for row in part if row["pearson_ic"] is not None]
        rank = [row["rank_ic"] for row in part if row["rank_ic"] is not None]
        output.append({"year": year, "mean_pearson_ic": math.fsum(pearson) / len(pearson) if pearson else None,
            "mean_rank_ic": math.fsum(rank) / len(rank) if rank else None,
            "ic_days": len(pearson), "signal_days": len(part),
            "paired_observations": sum(row["paired_count"] for row in part),
            "eligible_observations": sum(row["eligible_count"] for row in part)})
    return output


def audit_snapshot(path, *, start, end, direction, min_cross_section=100):
    path = Path(path)
    with np.load(path, allow_pickle=False) as saved:
        dates = saved["dates"].astype("datetime64[D]").astype(str).tolist()
        # Gate date metadata before materializing any market arrays.
        if not dates or min(dates) < "2015-01-01" or max(dates) > "2024-12-31":
            raise ValueError("unauthorized numeric snapshot dates")
        daily = independent_daily(dates, saved["open"], saved["scores"], saved["pool"],
            start=start, end=end, direction=direction, min_cross_section=min_cross_section,
            observed=saved["open_observed"] if "open_observed" in saved.files else None)
    return {"version": VERSION, "snapshot": str(path.resolve()),
            "snapshot_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "annual": independent_annual(daily), "daily": daily,
            "scope": "independent_return_endpoints_and_statistics_given_frozen_factor_values",
            "formula_causality_proven": False, "historical_arrival_certified": False,
            "independent_holdout": False, "profitability_proven": False}


def compare_report(reference, report, *, tolerance=1e-10):
    """Compare independently reconstructed statistics to a saved production report."""
    errors, compared, maximum = [], 0, 0.0
    actual_daily = {row["date"]: row for row in report["daily"]}
    if len(actual_daily) != len(report["daily"]):
        errors.append("duplicate_production_daily_dates")
    if set(actual_daily) != {row["date"] for row in reference["daily"]}:
        errors.append("production_and_reference_date_sets_differ")

    def compare(name, actual, expected):
        nonlocal compared, maximum
        compared += 1
        if actual is None or expected is None:
            if actual is not expected:
                errors.append(name + ":missingness_differs")
        elif not math.isfinite(float(actual)) or abs(float(actual) - float(expected)) > tolerance:
            errors.append(name + ":numeric_mismatch")
        else:
            maximum = max(maximum, abs(float(actual) - float(expected)))

    for expected in reference["daily"]:
        actual = actual_daily.get(expected["date"])
        if actual is None:
            continue
        for key in ("pearson_ic", "rank_ic", "paired_count", "factor_count"):
            compare(expected["date"] + ":" + key, actual[key], expected[key])
        compare(expected["date"] + ":pool_count", actual["pool_count"], expected["eligible_count"])
        compare(expected["date"] + ":label_safe", actual["label_safe"], expected["endpoint_within_year_and_scope"])
    actual_annual = {row["year"]: row for row in report["annual"]}
    if len(actual_annual) != len(report["annual"]) or set(actual_annual) != {r["year"] for r in reference["annual"]}:
        errors.append("annual_year_set_or_uniqueness_differs")
    for expected in reference["annual"]:
        actual = actual_annual.get(expected["year"])
        if actual is None:
            errors.append(str(expected["year"]) + ":missing_year")
            continue
        for key in ("mean_pearson_ic", "mean_rank_ic", "signal_days"):
            compare(str(expected["year"]) + ":" + key, actual[key], expected[key])
        for a, b in (("valid_days", "ic_days"), ("paired_cells", "paired_observations"),
                     ("pool_cells", "eligible_observations")):
            compare(str(expected["year"]) + ":" + a, actual[a], expected[b])
    for source, destination in (("pearson_ic", "mean_pearson_ic"), ("rank_ic", "mean_rank_ic")):
        values = [row[source] for row in reference["daily"] if row[source] is not None]
        compare("summary:" + destination, report["summary"][destination],
                math.fsum(values) / len(values) if values else None)
    return {"passed": not errors, "compared_values": compared, "maximum_absolute_difference": maximum,
            "errors": errors, "independent_annual": reference["annual"]}


def audit_study(root):
    """Audit every exported factor and all its train/development/confirmation rows."""
    start_wall, start_cpu = time.perf_counter(), time.process_time()
    root = Path(root).resolve()
    read = lambda path: json.loads(Path(path).read_text(encoding="utf-8"))
    file_hash = lambda path: hashlib.sha256(Path(path).read_bytes()).hexdigest()
    content_hash = lambda value: hashlib.sha256(json.dumps(value, ensure_ascii=False,
        sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()
    config, state = read(root / "protocol.json"), read(root / "state.json")
    freeze = read(root / "frozen_candidates.json")
    # Verify saved authority and phase receipts before reading any market matrix.
    metadata_pins = dict(read(root / "immutable_inputs.json"))
    metadata_pins["protocol.json"] = read(root / "protocol_identity.json")["protocol_sha256"]
    metadata_pins["frozen_candidates.json"] = read(root / "frozen_identity.json")["sha256"]
    if state.get("revision_admitted"):
        metadata_pins.update(read(root / "revision_identity.json"))
    if any(file_hash(root / name) != expected for name, expected in metadata_pins.items()):
        raise ValueError("saved study metadata changed before independent audit")
    if any(file_hash(path) != expected for path, expected in read(root / "source_pins.json").items()):
        raise ValueError("production source pins changed before independent audit")
    if config["confirmation_end"] != "2024-12-31" or state["confirmation_accesses"] != 1:
        raise ValueError("frozen confirmation authority and one-access receipt required")
    for name in ("calendar_path", "membership_path"):
        path = Path(config["data"][name])
        if file_hash(path) != read(root / (path.name + ".source.json"))["sha256"]:
            raise ValueError("frozen calendar/membership changed before independent audit")
    selected = freeze["selected"]
    manifest = read(root / "bundles" / "manifest.json")
    initial_expansion = read(root / "expansion.json")
    expansion_path = Path(freeze.get("expansion_path", str(root / "expansion.json")))
    expansion = read(expansion_path)
    expected_dates = [line.strip() for line in Path(config["data"]["calendar_path"]).read_text(encoding="utf-8").splitlines()
                      if line.strip() and config["data"]["start"] <= line.strip() <= config["confirmation_end"]]
    expected_symbols = read(root / "symbols.json")
    errors = []
    if freeze.get("expansion_sha256") and hashlib.sha256(expansion_path.read_bytes()).hexdigest() != freeze["expansion_sha256"]:
        errors.append("frozen_expansion_hash_changed")
    if state["phase"] != "complete" or state["confirmation_accesses"] != 1:
        errors.append("study_not_closed_after_one_confirmation_access")
    selected_ids = [row["factor_id"] for row in selected]
    selections = {row["factor_id"]: row for row in selected}
    exported_ids = [row["factor_id"] for row in manifest["bundles"]]
    if len(set(exported_ids)) != len(exported_ids) or set(selected_ids) != set(exported_ids):
        errors.append("frozen_and_exported_identity_sets_differ")
    if len(selected_ids) > config["selection"]["maximum"] or len(set(selected_ids)) != len(selected_ids):
        errors.append("frozen_selection_count_or_uniqueness_differs")
    counts = {arm: sum(r["kind"] == "candidate" and r["arm"] == arm for r in initial_expansion["attempts"])
              for arm in ("existing_library", "rule_perturbation", "llm_structure")}
    if any(count != config["budget"]["main_attempts_per_arm"] for count in counts.values()):
        errors.append("primary_arm_attempt_counts_differ_from_protocol")
    original_rows = {r["spec"]["expression"]: r for r in initial_expansion["candidates"]}
    final_rows = {r["spec"]["expression"]: r for r in expansion["candidates"]}
    if any(final_rows.get(expression) != row for expression, row in original_rows.items()):
        errors.append("revision_removed_or_replaced_original_canonical_candidates")
    if len(expansion["candidates"]) > config["budget"]["max_unique_candidates"]:
        errors.append("unique_formula_budget_exceeded")
    evaluated_ids = {content_hash({k: row["spec"][k] for k in ("language", "expression")})
                     for row in expansion["candidates"]}
    reserved_ids = {path.stem for path in (root / "starts").glob("*.json")}
    saved_development_ids = {path.stem for path in (root / "development").glob("*.json")}
    if evaluated_ids != reserved_ids or evaluated_ids != saved_development_ids:
        errors.append("development_queue_start_and_result_identity_sets_differ")
    if state["unique_evaluations_started"] != len(reserved_ids):
        errors.append("unique_formula_counter_differs_from_durable_starts")
    incremental_starts = len(list((root / "incremental_starts").glob("*.json")))
    if incremental_starts != state["incremental_started"] or incremental_starts > config["budget"]["max_incremental_evaluations"]:
        errors.append("incremental_counter_or_budget_differs")
    all_main_counts = {arm: sum(r["kind"] == "candidate" and r["arm"] == arm for r in expansion["attempts"])
                       for arm in counts}
    # Reconstruct the selection ordering from every saved daily development
    # series. This does not use the production annual or ranking implementation.
    ranking, development_aggregation_checks = [], 0
    for identity in sorted(evaluated_ids):
        record = read(root / "development" / (identity + ".json"))
        if record["status"] != "evaluated":
            continue
        report = record["development"]
        reference_daily = [{"date": r["date"], "pearson_ic": r["pearson_ic"], "rank_ic": r["rank_ic"],
            "paired_count": r["paired_count"], "factor_count": r["factor_count"],
            "eligible_count": r["pool_count"], "endpoint_within_year_and_scope": r["label_safe"]}
            for r in report["daily"]]
        annual = independent_annual(reference_daily)
        comparison = compare_report({"daily": reference_daily, "annual": annual}, report)
        development_aggregation_checks += 1
        if not comparison["passed"]:
            errors.append(identity + ":development_saved_daily_aggregation_differs")
        arms = {a["arm"] for a in expansion["attempts"] if a["factor_id"] == identity
                and a["kind"] == "candidate" and a["status"] in ("admitted", "duplicate")}
        eligible = ({r["year"] for r in annual} == {2019, 2020}
            and all(r["ic_days"] >= config["target"]["min_valid_days_per_year"]
                and r["mean_pearson_ic"] is not None and r["eligible_observations"] > 0
                and r["paired_observations"] / r["eligible_observations"] >= config["target"]["min_coverage_per_year"]
                for r in annual))
        if arms and eligible:
            ic = [r["pearson_ic"] for r in reference_daily if r["pearson_ic"] is not None]
            ranking.append((-min(r["mean_pearson_ic"] for r in annual), -math.fsum(ic) / len(ic), identity, arms))
    ranking.sort(key=lambda row: row[:3])
    independently_selected = []
    for arm in counts:
        winner = next((r[2] for r in ranking if arm in r[3]), None)
        if winner is not None and winner not in independently_selected:
            independently_selected.append(winner)
    for row in ranking[:config["selection"]["global_best"]]:
        if row[2] not in independently_selected:
            independently_selected.append(row[2])
    if independently_selected != selected_ids:
        errors.append("independently_reconstructed_selection_differs_from_freeze")
    outputs = []
    for entry in manifest["bundles"]:
        factor_id = entry["factor_id"]
        bundle_path, snapshot = Path(entry["bundle"]), Path(entry["audit_snapshot"])
        if hashlib.sha256(bundle_path.read_bytes()).hexdigest() != entry["bundle_sha256"]:
            errors.append(factor_id + ":bundle_file_hash_changed")
        if hashlib.sha256(snapshot.read_bytes()).hexdigest() != entry["audit_sha256"]:
            errors.append(factor_id + ":snapshot_file_hash_changed")
        with np.load(snapshot, allow_pickle=False) as saved:
            snapshot_dates = saved["dates"].astype("datetime64[D]").astype(str).tolist()
            if not snapshot_dates or min(snapshot_dates) < "2015-01-01" or max(snapshot_dates) > "2024-12-31":
                raise ValueError("unauthorized bundle snapshot dates before loading score matrix")
            if snapshot_dates != expected_dates:
                errors.append(factor_id + ":snapshot_calendar_differs_from_frozen_source")
            if saved["symbols"].astype(str).tolist() != expected_symbols:
                errors.append(factor_id + ":snapshot_symbols_differ_from_frozen_union")
        bundle = read(bundle_path)
        if content_hash({k: v for k, v in bundle.items() if k != "bundle_sha256"}) != bundle["bundle_sha256"]:
            errors.append(factor_id + ":bundle_content_digest_differs")
        formula_body = {k: bundle["spec"][k] for k in ("language", "expression")}
        if content_hash(formula_body) != factor_id:
            errors.append(factor_id + ":formula_identity_differs")
        if bundle["direction"] != selections[factor_id]["direction"]:
            errors.append(factor_id + ":bundle_direction_differs_from_freeze")
        if bundle["data"]["manifest_sha256"] != file_hash(bundle["data"]["manifest"]):
            errors.append(factor_id + ":bundle_data_manifest_changed")
        if (bundle["evidence"]["all_attempts_sha256"] != file_hash(expansion_path)
                or bundle["snapshot_score_direction_applied"] is not False):
            errors.append(factor_id + ":bundle_evidence_or_raw_score_contract_differs")
        # The portable value fingerprint uses the public pandas hash encoding;
        # reconstruct its framing here without calling the production exporter.
        import pandas as pd
        with np.load(snapshot, allow_pickle=False) as saved:
            frozen_scores = pd.DataFrame(saved["scores"], index=pd.DatetimeIndex(saved["dates"]),
                                         columns=saved["symbols"].astype(str).tolist())
        score_digest = hashlib.sha256()
        score_digest.update(str(list(frozen_scores.columns)).encode("utf-8"))
        score_digest.update(pd.util.hash_pandas_object(frozen_scores, index=True).values.tobytes())
        if score_digest.hexdigest() != bundle["scores_hash"] or entry["scores_hash"] != bundle["scores_hash"]:
            errors.append(factor_id + ":snapshot_score_digest_differs")
        development = read(root / "development" / (factor_id + ".json"))
        confirmation = read(root / "confirmation" / (factor_id + ".json"))
        phase_results = {}
        for phase, report, start, end in (
            ("train", development["train"], config["fit_start"], config["fit_end"]),
            ("development", development["development"], config["development_start"], config["development_end"]),
            ("confirmation", confirmation["report"], config["confirmation_start"], config["confirmation_end"])):
            reference = audit_snapshot(snapshot, start=start, end=end, direction=bundle["direction"],
                                       min_cross_section=config["target"]["min_cross_section"])
            comparison = compare_report(reference, report)
            phase_results[phase] = comparison
            if not comparison["passed"]:
                errors.append(factor_id + ":" + phase + ":production_statistics_differ")
            if report["direction"] != bundle["direction"]:
                errors.append(factor_id + ":" + phase + ":frozen_direction_changed")
            if phase == "train":
                training_values = [row["pearson_ic"] for row in reference["daily"] if row["pearson_ic"] is not None]
                oriented_mean = math.fsum(training_values) / len(training_values) if training_values else None
                reported_raw_mean = report["direction_fit"]["raw_train_mean_pearson_ic"]
                if (oriented_mean is None or oriented_mean <= 0 or reported_raw_mean is None
                        or abs(oriented_mean * bundle["direction"] - reported_raw_mean) > 1e-10):
                    errors.append(factor_id + ":independent_training_direction_rule_differs")
        years = phase_results["development"]["independent_annual"] + phase_results["confirmation"]["independent_annual"]
        statistical_target = len(years) == 6 and all(
            row["mean_pearson_ic"] is not None and row["mean_pearson_ic"] >= .1 for row in years)
        supported_target = statistical_target and all(
            row["ic_days"] >= config["target"]["min_valid_days_per_year"]
            and row["eligible_observations"] > 0
            and row["paired_observations"] / row["eligible_observations"] >= config["target"]["min_coverage_per_year"]
            for row in years)
        if confirmation["historical_target_met"] != supported_target:
            errors.append(factor_id + ":historical_target_classification_differs")
        if bundle["independent_stability_proven"] or bundle["profitability_proven"]:
            errors.append(factor_id + ":unsupported_independent_or_profitability_claim")
        outputs.append({"factor_id": factor_id, "name": bundle["spec"]["name"],
                        "direction": bundle["direction"], "phase_checks": phase_results,
                        "six_year_numeric_target_met": statistical_target,
                        "six_year_target_and_support_met": supported_target})
    return {"version": VERSION, "study_root": str(root), "passed": not errors, "errors": errors,
            "frozen_factors": len(selected_ids), "audited_exported_factors": len(outputs),
            "primary_attempts_by_arm": counts, "unique_formula_evaluations": len(expansion["candidates"]),
            "all_attempt_rows": len(expansion["attempts"]), "incremental_evaluations_reserved": incremental_starts,
            "unique_formula_count_is_not_independent_information_source_count": True,
            "all_round_primary_attempts_by_arm": all_main_counts,
            "metadata_and_code_pins_verified": True,
            "development_saved_daily_aggregation_checks": development_aggregation_checks,
            "independently_reconstructed_selection": independently_selected,
            "resources": {"wall_seconds": time.perf_counter() - start_wall,
                          "cpu_seconds": time.process_time() - start_cpu},
            "fair_arm_comparison_scope": "initial_equal_12_attempts_only; targeted_revision_is_separate",
            "factors": outputs, "auditor_source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "limitations": ["Frozen factor values are inputs to the independent statistical oracle",
                "Formula causality is tested separately by adversarial prefix and unit tests",
                "Does not authenticate historical source arrival or unseen data",
                "No execution, cost, capacity or profitability proof"]}


def audit_calibration(root, snapshot_directory):
    """Independent first-three formula reconstruction on authorized 2015--2020.

    Reuses only the frozen source loader. Formula arithmetic, pool construction,
    endpoints, correlations and annual aggregation use independent paths.
    """
    from quanta_agents.meta_v6.data import inspect_parquet_sources, load_market_panel
    start_wall, start_cpu = time.perf_counter(), time.process_time()
    root, destination = Path(root).resolve(), Path(snapshot_directory).resolve()
    read = lambda path: json.loads(Path(path).read_text(encoding="utf-8"))
    config = read(root / "protocol.json")
    if config["development_end"] != "2020-12-31":
        raise ValueError("calibration audit has explicit numeric authority through 2020 only")
    symbols = read(root / "symbols.json")
    actual_sources = inspect_parquet_sources(config["data"]["data_root"], symbols)
    if actual_sources != read(root / "source_inventory.json"):
        raise ValueError("frozen source inventory changed before calibration audit")
    for name in ("calendar_path", "membership_path"):
        path = Path(config["data"][name])
        if hashlib.sha256(path.read_bytes()).hexdigest() != read(root / (path.name + ".source.json"))["sha256"]:
            raise ValueError("frozen calendar/membership changed")
    panel = load_market_panel(**config["data"], end="2020-12-31", authorized_start=config["data"]["start"],
        authorized_end="2020-12-31", symbols=symbols, cache_dir=root.parent / "panel_cache")
    close = panel.fields["close"].to_numpy()
    opening = panel.fields["open"].to_numpy()
    amount = panel.fields["amount"].to_numpy()

    def trailing_sum_and_count(values, window):
        finite = np.isfinite(values)
        sums = np.vstack([np.zeros((1, values.shape[1])), np.cumsum(np.where(finite, values, 0), axis=0)])
        counts = np.vstack([np.zeros((1, values.shape[1]), dtype=int), np.cumsum(finite, axis=0)])
        out_sum, out_count = np.zeros(values.shape), np.zeros(values.shape, dtype=int)
        out_sum[window - 1:] = sums[window:] - sums[:-window]
        out_count[window - 1:] = counts[window:] - counts[:-window]
        return out_sum, out_count

    def lag(values, window):
        result = np.full(values.shape, np.nan)
        result[window:] = values[:-window]
        return result

    _, close_counts = trailing_sum_and_count(close, 120)
    amount_sum, amount_counts = trailing_sum_and_count(amount, 20)
    pool = (panel.eligible.to_numpy() & (close_counts == 120) & (amount_counts == 20)
            & (amount_sum > 0) & (panel.fields["is_st"].to_numpy() == 0)
            & (panel.fields["is_delisting"].to_numpy() == 0))
    formulas = {
        "pct_change(lag(close, 20), 100)": lambda: lag(close, 20) / lag(close, 120) - 1,
        "pct_change(close, 5)": lambda: close / lag(close, 5) - 1,
    }
    gap = np.log(close / opening) - np.log(opening / lag(close, 1))
    gap_sum, gap_count = trailing_sum_and_count(gap, 20)
    formulas["rolling_mean(log(close / open) - log(open / lag(close, 1)), 20)"] = lambda: np.where(gap_count == 20, gap_sum / 20, np.nan)
    rows = read(root / "expansion.json")["candidates"][:3]
    destination.mkdir(parents=True, exist_ok=True)
    results = []
    for row in rows:
        expression = row["spec"]["expression"]
        if expression not in formulas:
            raise ValueError("calibration formula changed from the independently implemented three")
        factor_id = row["factor_id"] if "factor_id" in row else row["trial_id"]
        # Factor IDs are computed from exact language/formula, independently of FactorSpec.
        body = {"language": row["spec"]["language"], "expression": expression}
        factor_id = hashlib.sha256(json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        production = read(root / "development" / (factor_id + ".json"))
        scores = np.where(pool, formulas[expression](), np.nan)
        snapshot = destination / (factor_id + ".npz")
        if snapshot.exists():
            raise ValueError("independent calibration snapshot already exists")
        np.savez_compressed(snapshot, dates=panel.dates.to_numpy(dtype="datetime64[ns]"),
            symbols=np.asarray(symbols, dtype=str), open=opening, scores=scores, pool=pool,
            open_observed=panel.fields["open_observed"].to_numpy())
        phases = {}
        for phase, first, last in (("train", config["fit_start"], config["fit_end"]),
                                  ("development", config["development_start"], config["development_end"])):
            report = production[phase]
            daily = independent_daily(panel.dates.astype(str), opening, scores, pool,
                start=first, end=last, direction=report["direction"],
                min_cross_section=config["target"]["min_cross_section"],
                observed=panel.fields["open_observed"].to_numpy())
            phases[phase] = compare_report({"daily": daily, "annual": independent_annual(daily)}, report)
        results.append({"factor_id": factor_id, "name": row["spec"]["name"], "expression": expression,
                        "snapshot": str(snapshot), "snapshot_sha256": hashlib.sha256(snapshot.read_bytes()).hexdigest(),
                        "formula_rebuilt_independently": True, "checks": phases})
    return {"version": VERSION, "scope": "first_three_real_calibration_formulas_train_and_development",
            "passed": all(check["passed"] for row in results for check in row["checks"].values()),
            "numeric_end": "2020-12-31", "confirmation_values_loaded": False,
            "production_correlation_or_evaluate_called": False, "factors": results,
            "resources": {"wall_seconds": time.perf_counter() - start_wall,
                          "cpu_seconds": time.process_time() - start_cpu, "source_loader": panel.load_metrics},
            "auditor_source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "independent_holdout": False, "profitability_proven": False}


def audit_real_incremental(root, calibration_evidence):
    """Rebuild fixed B/B+F1 using original-pool ranks and augmented least squares."""
    from quanta_agents.meta_v6.data import inspect_parquet_sources, load_market_panel
    begin_wall, begin_cpu = time.perf_counter(), time.process_time()
    root = Path(root).resolve()
    read = lambda path: json.loads(Path(path).read_text(encoding="utf-8"))
    cfg, evidence = read(root / "protocol.json"), read(calibration_evidence)
    if evidence["numeric_end"] != "2020-12-31" or cfg["development_end"] != "2020-12-31":
        raise ValueError("incremental oracle restricted to development through 2020")
    symbols = read(root / "symbols.json")
    if inspect_parquet_sources(cfg["data"]["data_root"], symbols) != read(root / "source_inventory.json"):
        raise ValueError("source inventory changed")
    for name in ("calendar_path", "membership_path"):
        path = Path(cfg["data"][name])
        if hashlib.sha256(path.read_bytes()).hexdigest() != read(root / (path.name + ".source.json"))["sha256"]:
            raise ValueError("frozen calendar/membership changed")
    panel = load_market_panel(**cfg["data"], end="2020-12-31", authorized_start=cfg["data"]["start"],
        authorized_end="2020-12-31", symbols=symbols, cache_dir=root.parent / "panel_cache")
    close, opening = panel.fields["close"].to_numpy(), panel.fields["open"].to_numpy()
    observed = panel.fields["open_observed"].to_numpy()
    dates = panel.dates.strftime("%Y-%m-%d").tolist()
    raw = {}
    for row in evidence["factors"]:
        snapshot = Path(row["snapshot"])
        if hashlib.sha256(snapshot.read_bytes()).hexdigest() != row["snapshot_sha256"]:
            raise ValueError("independent formula snapshot changed")
        with np.load(snapshot, allow_pickle=False) as saved:
            if saved["dates"].astype("datetime64[D]").astype(str).tolist() != dates:
                raise ValueError("independent snapshot differs from authorized date axis")
            raw[row["factor_id"]] = saved["scores"]
            pool = saved["pool"]
    baseline = read(root / "baseline.json")
    factor_id = lambda spec: hashlib.sha256(json.dumps(
        {key: spec[key] for key in ("language", "expression")}, ensure_ascii=False,
        sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    base_order = sorted(factor_id(spec) for spec in baseline)
    volatility_spec = next(spec for spec in baseline if spec["expression"] == "rolling_std(pct_change(close, 1), 20)")
    returns = np.full(close.shape, np.nan)
    returns[1:] = close[1:] / close[:-1] - 1
    volatility = np.full(close.shape, np.nan)
    for day in range(20, len(dates)):
        volatility[day] = np.std(returns[day - 19:day + 1], axis=0, ddof=1)
    raw[factor_id(volatility_spec)] = np.where(pool, volatility, np.nan)
    candidate_id = next(row["factor_id"] for row in evidence["factors"] if row["name"] == "existing.F1")
    order = base_order + [candidate_id]
    ranks = {}
    for identity, values in raw.items():
        result = np.full(values.shape, np.nan)
        for day in range(len(dates)):
            valid = np.flatnonzero(pool[day] & np.isfinite(values[day]))
            if len(valid):
                result[day, valid] = np.asarray(average_tie_ranks(values[day, valid])) / len(valid) - .5
        ranks[identity] = result
    labels = np.full(opening.shape, np.nan)
    for day in range(len(dates) - 6):
        if dates[day][:4] != dates[day + 6][:4]:
            continue
        valid = (np.isfinite(opening[day + 1]) & np.isfinite(opening[day + 6])
                 & (opening[day + 1] > 0) & (opening[day + 6] > 0)
                 & (observed[day + 1] == 1) & (observed[day + 6] == 1))
        labels[day, valid] = opening[day + 6, valid] / opening[day + 1, valid] - 1
    original = pool & np.isfinite(labels)
    for identity in base_order:
        original &= np.isfinite(ranks[identity])
    common = original & np.isfinite(ranks[candidate_id])
    for mask in (original, common):
        mask[mask.sum(axis=1) < cfg["target"]["min_cross_section"]] = False
    train_rows = [i for i, date in enumerate(dates) if cfg["fit_start"] <= date <= cfg["fit_end"]]
    records, fitted = {}, {}
    for name, keys, mask in (("baseline_original", base_order, original),
                             ("baseline_common", base_order, common), ("augmented", order, common)):
        chunks = []
        for day in train_rows:
            cells = np.flatnonzero(mask[day])
            if not len(cells):
                continue
            x = np.column_stack([ranks[identity][day, cells] for identity in keys])
            y = np.asarray(average_tie_ranks(labels[day, cells])) / len(cells)
            chunks.append((x, y - y.mean()))
        x = np.concatenate([a for a, _ in chunks]); y = np.concatenate([b for _, b in chunks])
        weights = np.concatenate([np.full(len(a), 1 / len(a) / len(chunks)) for a, _ in chunks])
        means = np.sum(x * weights[:, None], axis=0)
        scales = np.sqrt(np.sum((x - means) ** 2 * weights[:, None], axis=0))
        standardized = (x - means) / scales
        design = np.vstack([standardized * np.sqrt(weights[:, None]), np.sqrt(.1) * np.eye(len(keys))])
        target = np.concatenate([y * np.sqrt(weights), np.zeros(len(keys))])
        beta = np.linalg.lstsq(design, target, rcond=None)[0]
        fitted[name] = {"feature_order": keys, "feature_means": means.tolist(), "feature_scales": scales.tolist(),
                        "coefficients": beta.tolist(), "days": len(chunks), "cells": len(x)}
        predictions = sum((ranks[key] - means[j]) / scales[j] * beta[j] for j, key in enumerate(keys))
        records[name] = (predictions, mask)
    production = read(root / "development_incremental" / (candidate_id + ".json"))["result"]
    errors, compared, maximum = [], 0, 0.0
    def check(name, actual, expected):
        nonlocal compared, maximum
        compared += 1
        if actual is None or expected is None:
            if actual is not expected:
                errors.append(name + ":missingness_differs")
        elif not math.isfinite(float(actual)) or abs(float(actual) - float(expected)) > 1e-10:
            errors.append(name + ":numeric_mismatch")
        else:
            maximum = max(maximum, abs(float(actual) - float(expected)))
    for name, fitted_model in fitted.items():
        model = production["models"][name]
        if fitted_model["feature_order"] != model["feature_order"]:
            errors.append(name + ":feature_order_differs")
        for key in ("feature_means", "feature_scales", "coefficients"):
            for a, b in zip(model[key], fitted_model[key]):
                check(name + ":" + key, a, b)
        for key in ("days", "cells"):
            check(name + ":" + key, model["common_sample"][key], fitted_model[key])
    saved_daily = {row["date"]: row for row in production["daily"]}
    deltas, daily = [], []
    for day, date in enumerate(dates):
        if not cfg["development_start"] <= date <= cfg["development_end"]:
            continue
        result = {"date": date}
        for name, (predictions, mask) in records.items():
            cells = np.flatnonzero(mask[day])
            xs, ys = predictions[day, cells], labels[day, cells]
            for metric, value in (("pearson_ic", scalar_pearson(xs, ys)),
                                  ("rank_ic", scalar_pearson(average_tie_ranks(xs), average_tie_ranks(ys))),
                                  ("paired_count", len(cells))):
                key = name + "_" + metric
                result[key] = value
                check(date + ":" + key, saved_daily[date][key], value)
        a, b = result["augmented_pearson_ic"], result["baseline_common_pearson_ic"]
        delta = a - b if a is not None and b is not None else None
        check(date + ":delta", saved_daily[date]["delta_pearson_ic"], delta)
        if delta is not None:
            deltas.append(delta)
        daily.append(result)
    check("pooled_delta", production["paired_delta_pearson_ic"], math.fsum(deltas) / len(deltas))
    return {"version": VERSION, "scope": "real_2016_2018_fit_2019_2020_B_BplusF1_incremental",
            "passed": not errors, "errors": errors, "compared_values": compared,
            "maximum_absolute_difference": maximum, "models": fitted, "daily": daily,
            "paired_delta_pearson_ic": math.fsum(deltas) / len(deltas),
            "numeric_end": "2020-12-31", "confirmation_values_loaded": False,
            "production_fitting_or_evaluation_called": False,
            "resources": {"wall_seconds": time.perf_counter() - begin_wall, "cpu_seconds": time.process_time() - begin_cpu},
            "auditor_source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "limitations": ["One real incremental candidate, not every model", "Historical source arrival is not independently authenticated",
                            "Previously exposed history; no profitability inference"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--snapshot")
    source.add_argument("--study-root")
    source.add_argument("--calibration-root")
    source.add_argument("--incremental-calibration-root")
    parser.add_argument("--calibration-evidence")
    parser.add_argument("--start")
    parser.add_argument("--end")
    parser.add_argument("--direction", type=int, choices=(-1, 1))
    parser.add_argument("--min-cross-section", type=int, default=100)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    if args.incremental_calibration_root:
        if not args.calibration_evidence:
            parser.error("incremental calibration requires --calibration-evidence")
        result = audit_real_incremental(args.incremental_calibration_root, args.calibration_evidence)
    elif args.calibration_root:
        result = audit_calibration(args.calibration_root, Path(args.output).with_suffix(""))
    elif args.study_root:
        result = audit_study(args.study_root)
    else:
        if not args.start or not args.end or args.direction is None:
            parser.error("snapshot mode requires --start, --end and --direction")
        result = audit_snapshot(args.snapshot, start=args.start, end=args.end,
                                direction=args.direction, min_cross_section=args.min_cross_section)
    output = Path(args.output)
    if output.exists():
        raise ValueError("independent audit output already exists; preserve previous run")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, allow_nan=False, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(output.resolve()), "passed": result.get("passed"),
                      "audited_exported_factors": result.get("audited_exported_factors"),
                      "annual": result.get("annual"), "errors": result.get("errors", [])}, ensure_ascii=False))


if __name__ == "__main__":
    main()
