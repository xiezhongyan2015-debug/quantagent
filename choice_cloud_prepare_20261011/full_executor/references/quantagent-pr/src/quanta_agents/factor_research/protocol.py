"""Frozen research policy; numeric access is limited to exposed 2015--2024."""
from __future__ import annotations

from copy import deepcopy
from datetime import date

VERSION = "meta_v9a.1.0"
ARMS = ("existing_library", "rule_perturbation", "llm_structure")


def default_protocol(project_root):
    return {
        "version": VERSION, "study_id": "v9a_factor_20260912_r1",
        "project_root": str(project_root),
        "fit_start": "2016-01-01", "fit_end": "2018-12-31",
        "development_start": "2019-01-01", "development_end": "2020-12-31",
        "confirmation_start": "2021-01-01", "confirmation_end": "2024-12-31",
        "target_years": list(range(2019, 2025)),
        "target": {"entity_type": "single_factor", "metric": "mean_daily_cross_sectional_pearson_ic",
                   "annual_threshold": .10, "all_declared_years_required": True,
                   "min_cross_section": 100, "min_valid_days_per_year": 200,
                   "min_coverage_per_year": .8},
        "label": {"horizon": 5, "formula": "open[t+6]/open[t+1]-1",
                  "return_transform": "none", "price_field": "fixed_anchor_adjusted_open",
                  "endpoint_rule": "annual_endpoint_containment",
                  "year_tail_policy": "exclude last six signal sessions of every natural year",
                  "annualization": "none"},
        "universe": "signal_date_historical_csi300_eligible_120day_seasoning_20day_positive_amount_nonST_nondelisting",
        "direction": "sign of 2016-2018 pooled daily Pearson IC; fixed thereafter; zero or unknown rejects",
        "baseline": {"asset_ids": ["F2", "F3", "F7"], "ridge_lambda": .1,
                     "fit_start": "2016-01-01", "fit_end": "2018-12-31",
                     "update": "none", "target": "same_day_percentile_rank_return_demeaned",
                     "comparison": "B original coverage, B common sample, B+f common sample; interactions retain main effects"},
        "selection": {"per_arm": 1, "global_best": 3, "maximum": 6,
                      "order": ["worst_development_year_pearson_desc", "mean_development_pearson_desc", "factor_id_asc"],
                      "failed_years": "ineligible; never drop a year from ranking",
                      "below_target": "may diagnose to quantify gap; never classify as supported"},
        "uncertainty": {"block_sessions": 20, "bootstrap_samples": 300, "seed": 20260912,
                        "scope": "descriptive dependent-date uncertainty; no adaptive-search correction"},
        "budget": {"main_attempts_per_arm": 12, "max_unique_candidates": 72,
                   "max_revision_main_attempts": 12,
                   "max_expansion_attempts": 256, "max_development_batches": 2,
                   "max_incremental_evaluations": 54, "max_confirmation_accesses": 1,
                   "max_extra_gateway_calls": 0, "max_workers": 1,
                   "wall_seconds": 7200, "min_free_disk_bytes": 2 * 1024**3,
                   "max_artifact_bytes": 8 * 1024**3, "max_worker_private_bytes": 4 * 1024**3},
        "data": {"data_root": "D:/qlib_data/parquet_cn_a_qfq_tradeable_v2_2015_2025",
                 "calendar_path": "D:/qlib_data/qlib_bin/calendars/day.txt",
                 "membership_path": "D:/qlib_data/qlib_bin/instruments/csi300.txt",
                 "start": "2015-01-01", "optional_fields": ["is_st", "is_delisting"]},
        "exposure": {"previously_exposed": ["2015-01-01", "2024-12-31"],
                     "numeric_authorized_end": "2024-12-31", "independent_holdout": False,
                     "2025": "metadata_only_no_numeric_rows"},
        "trading_check": {"status": "not_run_in_core", "account_budget": 0,
                          "allowed_use_evidence": "fixed top40 and quintile forward gross returns only"},
        "limitations": ["Source adjustment revisions and historical arrival times are not authenticated",
                        "All 2015-2024 history has prior project exposure",
                        "One three-arm research batch is not an independent framework superiority experiment",
                        "No account execution, costs, capacity or profitability claim"],
    }


def validate_protocol(protocol):
    cfg = deepcopy(protocol)
    required = set(default_protocol(cfg.get("project_root", "")))
    if set(cfg) != required or cfg["version"] != VERSION:
        raise ValueError("exact V9A protocol fields and version required")
    names = ("fit_start", "fit_end", "development_start", "development_end", "confirmation_start", "confirmation_end")
    for name in names:
        if date.fromisoformat(cfg[name]).isoformat() != cfg[name]:
            raise ValueError("ISO dates required")
    if not all(cfg[a] < cfg[b] for a, b in zip(names, names[1:])):
        raise ValueError("fit, development and confirmation must be strictly ordered")
    if cfg["confirmation_end"] > "2024-12-31" or cfg["exposure"]["numeric_authorized_end"] > "2024-12-31":
        raise ValueError("2025 numeric access is not authorized")
    frozen = default_protocol(cfg["project_root"])
    for name in (*names, "data"):
        if cfg[name] != frozen[name]:
            raise ValueError("this research version freezes data/split field: " + name)
    for name in ("target", "label", "direction", "baseline", "selection", "universe", "target_years", "exposure", "trading_check"):
        if cfg[name] != frozen[name]:
            raise ValueError("this research version freezes policy field: " + name)
    for name, value in cfg["budget"].items():
        if type(value) is not int or value < 0 or name not in frozen["budget"] or value > frozen["budget"][name]:
            raise ValueError("budget exceeds authorized bounded protocol: " + name)
    if set(cfg["budget"]) != set(frozen["budget"]):
        raise ValueError("complete budgets required")
    return cfg
