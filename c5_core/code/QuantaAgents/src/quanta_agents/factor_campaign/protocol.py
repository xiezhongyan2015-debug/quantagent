"""Research scheduling policy; acceptance authority is separately frozen."""
from copy import deepcopy
from pathlib import Path
import hashlib
import json


def default_protocol(source_root):
    return {
        "version": "V10A", "schema": "factor_campaign_v1",
        "source_root": str(Path(source_root).resolve()),
        "inventory_path": str(Path(source_root) / "output/research/meta_v9_20260909/library_inventory.json"),
        "data": {"data_root": "/Users/leslie/Downloads/中证500量化60min/c5_core/data/qlib_data/parquet_cn_a_qfq_tradeable_v2_2015_2025",
                 "calendar_path": "/Users/leslie/Downloads/中证500量化60min/c5_core/data/qlib_data/qlib_bin/calendars/day.txt",
                 "membership_path": "/Users/leslie/Downloads/中证500量化60min/c5_core/data/qlib_data/qlib_bin/instruments/csi300.txt",
                 "start": "2015-01-01", "end": "2024-12-31"},
        "direction_start": "2016-01-01", "direction_end": "2018-12-31",
        "evaluation_years": list(range(2019, 2025)),
        "single_factor_threshold_strict": .05, "combination_threshold_strict": .10,
        "label": "open[t+6]/open[t+1]-1", "year_tail_purge": 6,
        "minimum_cross_section": 100, "minimum_year_days": 200, "minimum_coverage": .8,
        "exposure": "all_2015_2024_previously_exposed_history",
        "numeric_after_2024_allowed": False,
        "budget": {"minimum_factors": 300, "minimum_combinations": 200,
                   "initial_reference_factors": 46, "minimum_new_v10a": 254,
                   "factor_extension": 50, "combination_extension": 40,
                   "max_factors": 3000, "max_combinations": 2000,
                   "max_numeric_seconds": 21600, "plateau_extensions": 2,
                   "minimum_worst_year_improvement": .001,
                   "min_free_ram_bytes": 6 * 1024**3, "min_free_disk_bytes": 10 * 1024**3,
                   "initial_workers": 1, "maximum_workers": 2},
        "combination": {"sizes": [4, 8, 12, 24], "dense_reference_size": 46,
                        "lambdas": [.001, .01, .1, 1.],
                        "targets": ["raw_return_demeaned", "rank_return_demeaned"],
                        "updates": ["fixed", "rolling_3y", "expanding"],
                        "selection_rules": ["quality", "diversity", "role_balanced"],
                        "refit_frequency": "quarterly"},
        "complementarity": {"folds": [["2016-01-01", "2016-12-31", "2017-01-01", "2017-12-31"],
                                      ["2016-01-01", "2017-12-31", "2018-01-01", "2018-12-31"]],
                            "mean_delta_minimum": .001, "each_fold_nonnegative": True},
        "models": {"main": "gpt-6-astra/ultra", "research": "gpt-6-astra/xhigh"},
        "claims": {"historical_target_met": False, "independent_stability_proven": False,
                   "profitability_proven": False, "currency_cost": None}}


def validate_protocol(value):
    cfg = deepcopy(value)
    expected = default_protocol(cfg["source_root"])
    for key in ("direction_start", "direction_end", "evaluation_years",
                "single_factor_threshold_strict", "combination_threshold_strict", "label",
                "year_tail_purge", "minimum_cross_section", "minimum_year_days",
                "minimum_coverage", "numeric_after_2024_allowed"):
        if cfg.get(key) != expected[key]:
            raise ValueError("Frozen acceptance boundary changed: " + key)
    if cfg["data"].get("start") != "2015-01-01" or cfg["data"].get("end") != "2024-12-31":
        raise ValueError("Unauthorized numeric data interval")
    if cfg["budget"] != expected["budget"]:
        raise ValueError("User-approved campaign budgets may not be silently changed")
    return cfg


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(",", ":"), allow_nan=False).encode()).hexdigest()
